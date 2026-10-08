"""Opt-in recorder of the platform-sensitive tests' numbers (E12; docs/E12_PLATFORM_TESTS.md).

docs/PRE_FRISCO_PROGRAM.md, E12: the marked T.H.52 / Ruth St / T.H.61 fixture
tests must give the same outcome on macOS and on Linux CI, and every mark must
become strict with both platforms' numbers in its reason. This module records
those numbers. When the environment variable :data:`RECORD_ENV`
(``FLOWSTATE_RECORD_STATE``) names a file, every test of ``tests/test_microsim``
appends one JSON line to it at teardown (``tests/test_microsim/conftest.py``
wires :func:`recording` as an autouse fixture)::

    {"test_id", "outcome", "platform", "machine", "python", "sumo_version",
     "libsumo_version", "numpy_version", "commit", "runs": [...], "states": [...]}

- ``outcome``: ``passed``, ``failed``, ``xfailed``, ``xpassed`` (a non-strict
  mark), ``xpassed_strict`` (a strict mark: the test fails), ``skipped`` or
  ``not_run`` (:func:`outcome_of`).
- ``runs``: one entry per ``run_micro`` call the test made (:func:`run_numbers`):
  the run's ``meta.json`` counters (departures, collisions, every ``n_*`` /
  ``wait*`` counter of each weave section and measured zone) and a digest of
  its trajectories, whole and per 60-s window, so two platforms' runs read as
  identical or not and, if not, from which minute.
- ``states``: the return value of every state helper of :data:`STATE_HELPERS`
  the test called — the dicts and per-minute speed series its assertions read —
  serialized at teardown, so a state the test completes after the call
  (``test_th52_weave_at_corridor_demand_exit_side`` adds ``exit_share``) is
  recorded as asserted.

No test calls the recorder. For the test's duration it wraps its own module's
``run_micro`` and the helpers of :data:`STATE_HELPERS` the module defines (the
module attributes are put back at teardown), so the test files stay as they
are — including ``test_microsim_merge_managed_meter.py`` — and the test modules
that ``scripts/merge_model_selfcheck.py`` and ``scripts/platform_probe.py``
load by path import nothing new. Unset (the default), the fixture yields at
once: nothing is wrapped, read or written, and no test's outcome can change.
The wrappers only read what a run wrote and what a helper returned.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

#: The environment variable naming the JSON-lines file the records are appended to.
RECORD_ENV = "FLOWSTATE_RECORD_STATE"

#: The test modules' helpers whose return value is the state a test asserts on
#: (module attribute names; a module that does not define one is left alone).
#: ``lane1_last60m_windows``: test_microsim_weave_short_section (``assert_exit_side``);
#: ``th61_lane_end_state``: test_microsim_th61_lane_end; ``_th52_lane1_windows``,
#: ``_th52_corridor_state``, ``_lane_speed_windows``: test_microsim_merge_managed_meter;
#: ``_th52_state``, ``_th52_lane1_first60m_windows``: test_microsim_merge_measured.
STATE_HELPERS: tuple[str, ...] = (
    "lane1_last60m_windows",
    "th61_lane_end_state",
    "_th52_lane1_windows",
    "_th52_corridor_state",
    "_lane_speed_windows",
    "_th52_state",
    "_th52_lane1_first60m_windows",
)

#: Width of a trajectory window digest [s] and the hex characters kept of it.
WINDOW_S = 60.0
WINDOW_HEX = 12

#: The ``meta.json`` scalars of a run kept in its record.
RUN_SCALARS: tuple[str, ...] = (
    "config_hash",
    "seed",
    "sumo_seed",
    "n_vehicles_planned",
    "n_vehicles_departed",
    "n_vehicles_arrived",
    "n_collisions",
)


def record_path() -> Path | None:
    """The file named by :data:`RECORD_ENV`, or ``None`` when recording is off."""
    value = os.environ.get(RECORD_ENV, "").strip()
    return Path(value) if value else None


def _version(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def platform_header() -> dict[str, Any]:
    """The platform fields of every record: OS, machine, Python and the SUMO / numpy versions."""
    return {
        "platform": platform.system().lower(),
        "machine": platform.machine().lower(),
        "platform_detail": platform.platform(),
        "python": platform.python_version(),
        "sumo_version": _version("eclipse-sumo"),
        "libsumo_version": _version("libsumo"),
        "numpy_version": _version("numpy"),
        "pandas_version": _version("pandas"),
        "pyarrow_version": _version("pyarrow"),
        "commit": os.environ.get("GITHUB_SHA"),
    }


def jsonable(value: Any) -> Any:
    """``value`` as plain JSON: mappings with string keys, lists, numbers, strings, ``None``.

    A pandas Series becomes a mapping of its index (as strings) to its values, a
    DataFrame a mapping of its columns to such mappings, a numpy scalar or array
    its Python value(s), a tuple a list. Floats keep full precision (``json``
    writes the shortest repr that reads back to the same double); a non-finite
    float becomes the string ``"nan"``, ``"inf"`` or ``"-inf"`` so the line is
    strict JSON.
    """
    if isinstance(value, pd.DataFrame):
        return {str(c): jsonable(value[c]) for c in value.columns}
    if isinstance(value, pd.Series):
        return {_key(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, Mapping):
        return {_key(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, np.ndarray):
        return [jsonable(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return jsonable(value.item())
    if isinstance(value, list | tuple | set | frozenset):
        items = sorted(value, key=repr) if isinstance(value, set | frozenset) else value
        return [jsonable(v) for v in items]
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return value
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Path):
        return str(value)
    return repr(value)


def _key(k: Any) -> str:
    if isinstance(k, tuple):
        return "/".join(str(jsonable(x)) for x in k)
    if isinstance(k, np.generic):
        k = k.item()
    return str(k)


def trajectory_digests(path: Path) -> dict[str, Any]:
    """sha256 of a run's trajectories (t, veh_id, lane, x, v), whole and per 60-s window.

    Rows are taken in (t, veh_id) order; the floats and the lane index are
    hashed as little-endian bytes, so the digest is exact and the same on
    every platform for the same numbers. ``by_window`` maps each window index
    (``t // 60``) to the first :data:`WINDOW_HEX` hex characters of its own
    digest, so two platforms' runs show the minute in which they part.
    """
    df = pd.read_parquet(path, columns=["t", "veh_id", "lane", "x", "v"])
    df = df.sort_values(["t", "veh_id"], kind="mergesort").reset_index(drop=True)
    whole = hashlib.sha256()
    by_window: dict[str, str] = {}
    if len(df):
        window = (df["t"].to_numpy() // WINDOW_S).astype(np.int64)
        cuts = np.flatnonzero(np.diff(window)) + 1
        for lo, hi in zip(np.r_[0, cuts], np.r_[cuts, len(df)], strict=True):
            chunk = _rows_bytes(df.iloc[lo:hi])
            whole.update(chunk)
            by_window[str(int(window[lo]))] = hashlib.sha256(chunk).hexdigest()[:WINDOW_HEX]
    return {"rows": len(df), "sha256": whole.hexdigest(), "by_window": by_window}


def _rows_bytes(df: pd.DataFrame) -> bytes:
    """Canonical bytes of trajectory rows: ids, then t, lane, x, v as little-endian arrays."""
    ids = "\x1f".join(df["veh_id"].astype(str)).encode()
    return b"".join(
        (
            ids,
            np.ascontiguousarray(df["t"].to_numpy(), dtype="<f8").tobytes(),
            np.ascontiguousarray(df["lane"].to_numpy(), dtype="<i4").tobytes(),
            np.ascontiguousarray(df["x"].to_numpy(), dtype="<f8").tobytes(),
            np.ascontiguousarray(df["v"].to_numpy(), dtype="<f8").tobytes(),
        )
    )


def _counters(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The label and every ``n_*`` / ``wait*`` / ``mean_*`` counter of a section or zone record."""
    keep = {k: v for k, v in entry.items() if k.startswith(("n_", "wait", "mean_"))}
    return {"ramp": entry.get("ramp"), **keep}


def run_numbers(paths: Any) -> dict[str, Any]:
    """The record of one ``run_micro`` call, read from its ``meta.json`` and trajectories."""
    meta = json.loads(Path(paths.meta).read_text())
    out: dict[str, Any] = {k: meta.get(k) for k in RUN_SCALARS}
    out["collisions"] = meta.get("collisions", [])[:10]
    out["ramps"] = [
        {"name": r.get("name"), "n_planned": r.get("n_planned"), "n_departed": r.get("n_departed")}
        for r in meta.get("ramps", [])
    ]
    out["weave_sections"] = [_counters(w) for w in meta.get("weave_sections", [])]
    out["measured_merges"] = [_counters(z) for z in meta.get("measured_merges", [])]
    out["trajectories"] = trajectory_digests(Path(paths.trajectories))
    return jsonable(out)


def outcome_of(report: Any) -> str:
    """The outcome of a test's call phase, as ``-rxX`` reports it.

    ``report`` is the call-phase ``TestReport`` (``None`` when the call never
    ran: a setup error or skip). An xfail mark's outcomes carry ``wasxfail``:
    ``xfailed`` (the assertion failed, the report reads skipped) or ``xpassed``
    (a non-strict mark whose test passed); a strict mark's pass is a failure
    whose report starts ``[XPASS(strict)]``, here ``xpassed_strict``.
    """
    if report is None:
        return "not_run"
    if getattr(report, "skipped", False):
        return "xfailed" if hasattr(report, "wasxfail") else "skipped"
    if getattr(report, "passed", False):
        return "xpassed" if hasattr(report, "wasxfail") else "passed"
    if str(getattr(report, "longrepr", "")).startswith("[XPASS(strict)]"):
        return "xpassed_strict"
    return "failed"


def append_record(path: Path, record: Mapping[str, Any]) -> None:
    """Append ``record`` as one JSON line to ``path`` (parents created)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(jsonable(record), sort_keys=False, allow_nan=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


class TestRecord:
    """What one test ran and read, gathered while it runs and written at teardown."""

    __test__ = False  # not a test class, despite its name

    def __init__(self, test_id: str) -> None:
        self.test_id = test_id
        self.runs: list[dict[str, Any]] = []
        self.states: list[tuple[str, Any]] = []

    def wrap_run(self, run: Callable[..., Any]) -> Callable[..., Any]:
        """``run`` (a ``run_micro``) that also records each completed run's numbers."""

        def recorded_run(*args: Any, **kwargs: Any) -> Any:
            paths = run(*args, **kwargs)
            self.runs.append(run_numbers(paths))
            return paths

        recorded_run.__wrapped__ = run  # type: ignore[attr-defined]
        return recorded_run

    def wrap_state(self, name: str, helper: Callable[..., Any]) -> Callable[..., Any]:
        """``helper`` that also keeps its return value (serialized at :meth:`record`)."""

        def recorded_helper(*args: Any, **kwargs: Any) -> Any:
            value = helper(*args, **kwargs)
            self.states.append((name, value))
            return value

        recorded_helper.__wrapped__ = helper  # type: ignore[attr-defined]
        return recorded_helper

    def record(self, outcome: str) -> dict[str, Any]:
        """The JSON-line record of the test (its states serialized now)."""
        return {
            "test_id": self.test_id,
            "outcome": outcome,
            **platform_header(),
            "runs": self.runs,
            "states": [{"helper": n, "value": jsonable(v)} for n, v in self.states],
        }


@contextmanager
def recording(
    test_id: str, module: Any, call_report: Callable[[], Any]
) -> Iterator[TestRecord | None]:
    """Record one test while the context is open (the autouse fixture's body).

    Off (:data:`RECORD_ENV` unset or empty) it yields ``None`` and touches
    nothing. On, it wraps ``module.run_micro`` and the :data:`STATE_HELPERS`
    ``module`` defines, puts them back on exit, and appends the test's record
    with the outcome ``call_report()`` returns then (the call-phase report).
    """
    path = record_path()
    if path is None or module is None:
        yield None
        return
    rec = TestRecord(test_id)
    originals: dict[str, Any] = {}
    run = getattr(module, "run_micro", None)
    if callable(run):
        originals["run_micro"] = run
        module.run_micro = rec.wrap_run(run)
    for name in STATE_HELPERS:
        helper = getattr(module, name, None)
        if callable(helper):
            originals[name] = helper
            setattr(module, name, rec.wrap_state(name, helper))
    try:
        yield rec
    finally:
        for name, original in originals.items():
            setattr(module, name, original)
        append_record(path, rec.record(outcome_of(call_report())))


def read_records(path: Path) -> list[dict[str, Any]]:
    """The records of a JSON-lines file, in file order."""
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
