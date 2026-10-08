"""E12 step 2: the per-step divergence probe (docs/E12_PLATFORM_TESTS.md).

docs/PRE_FRISCO_PROGRAM.md, E12, design step 2: for the platform-sensitive
fixtures (a test that passes on macOS and fails on Linux CI, or the other way),
hash the simulation's state at every step on each platform, so the first step
at which the two platforms part is found, and whether a value SUMO returned or
a command the runner wrote differs first.

Per step (every ``sim.step_length_s``; 2,400 steps for the fixtures' 20
simulated minutes at 0.5 s) the probe keeps three digests, the first
:data:`HASH_HEX` hex characters of a sha256:

- ``sumo``: every vehicle in the network (ramps included), sorted by id, as
  (id, lane id, position on the lane, speed) — read with libsumo getters from a
  ``libsumo.addStepListener`` listener, which SUMO's Python binding calls after
  each ``simulationStep`` the runner makes, before the runner reads the step.
  The getters are pure reads; ``run_micro`` itself is not changed, and a probed
  run's files equal an unprobed run's (tests/test_scripts/test_platform_probe.py).
- ``traj``: the run's ``trajectories.parquet`` rows of the step, as (id, lane
  index, corridor x, speed). The fixtures run at the defaults ``output_hz`` 2 Hz
  and ``step_length_s`` 0.5 s, so the runner captures every step
  (``out_every`` = 1) and no runner hook is needed; the capture covers vehicles
  on corridor edges only, which is why ``sumo`` is kept beside it.
- ``commands``: the step's rows of ``weave_commands.parquet`` in decision order
  (``WeaveSpec.record_commands``, set on every weave block of the probed
  config; a pure recorder, hash-neutral: the probe checks that the config hash
  is unchanged by it). ``null`` for a step with no command.

Floats are hashed as little-endian IEEE-754 bytes (exact). Within a step the
runner reads the state the step produced and then writes its commands, which
act on the next step: so if ``sumo`` differs first, SUMO's step parted from
identical observed state and commands; if ``commands`` differs first, the
runner decided differently on identical (id, lane, position, speed) — Python
arithmetic, or a SUMO value outside that tuple that the rules read
(``getLeader``, ``getFollowSpeed``, ``getNeighbors``, ``getLaneChangeState``).

Usage::

    uv run --no-sync python scripts/platform_probe.py                 # default fixtures
    uv run --no-sync python scripts/platform_probe.py --fixture all
    uv run --no-sync python scripts/platform_probe.py --fixture ruth_exit_peak_s5 \\
        --dump-at 600.5 601.0                     # also every row of those steps
    uv run --no-sync python scripts/platform_probe.py --list
    uv run --no-sync python scripts/platform_probe.py --compare a.json b.json
    uv run --no-sync python scripts/platform_probe.py --compare-records a.jsonl b.jsonl

Writes ``<out-dir>/<fixture>_<system>-<machine>.json`` (default out-dir
``artifacts/e12_platform_2026-10-07``); the runs themselves go to a temporary
directory, so no parquet is left behind. ``--compare`` exits 0 when the two
files agree at every step, 1 when they part, 2 when they are not the same
fixture and step grid. ``--compare-records`` reads two ``FLOWSTATE_RECORD_STATE``
files (tests/test_microsim/_platform_record.py) and prints, per test, both
outcomes, the minute from which each run's trajectories part and the recorded
numbers that differ; it exits 0 when every test agrees in all three, else 1.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests" / "test_microsim"
DEFAULT_OUT = REPO / "artifacts" / "e12_platform_2026-10-07"

#: Hex characters kept of each per-step sha256 (48 bits: two different states
#: share a digest with probability 2^-48 per step).
HASH_HEX = 12
#: Version of the output format.
FORMAT = 1
#: Channels of a probe file, in the order the comparison reports them.
CHANNELS: tuple[str, ...] = ("sumo", "traj", "commands")
#: Prefix of the probed tests' node ids.
_SHORT = "tests/test_microsim/test_microsim_weave_short_section.py::TestRuthStWeave::"
_MMT = "tests/test_microsim/test_microsim_merge_managed_meter.py::TestWeaveRun::"


def _load_test_module(name: str) -> ModuleType:
    """A test module of ``tests/test_microsim`` (its fixture configs are the single source)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TESTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ruth(demand: str, seed: int) -> Any:
    ws = _load_test_module("test_microsim_weave_short_section")
    return ws.ruth_config(seed, **ws.RUTH_DEMAND[demand], fleet=ws.CORRIDOR_FLEET)


def _th52_capacity(seed: int, amendment4: str) -> Any:
    mmt = _load_test_module("test_microsim_merge_managed_meter")
    cfg = mmt._th52_config(seed)
    if amendment4 == "off":
        cfg = mmt._with_weave_params(cfg, dict(mmt.WEAVE_AMENDMENT4_OFF))
    return cfg


def _th52_corridor_demand(seed: int) -> Any:
    mmt = _load_test_module("test_microsim_merge_managed_meter")
    return mmt._th52_config(seed, **mmt.TH52_CORRIDOR_DEMAND)


@dataclass(frozen=True)
class ProbeFixture:
    """A probed fixture: the test it mirrors, its seed and how the test builds its config."""

    name: str
    test_id: str
    seed: int
    build: Callable[[], Any]


#: The probed fixtures, each built exactly as its test builds it (the test
#: module's own config function, loaded by path). The first three are the
#: program's two xpass fixtures (``[4-0]`` is two arms since the Amendment-4
#: parametrisation: ``off`` is the weave before W1b/W2, ``on`` the default); the
#: last two passed their non-strict marks on macOS under the W1b/W2 defaults
#: (docs/E12_PLATFORM_TESTS.md).
FIXTURES: dict[str, ProbeFixture] = {
    f.name: f
    for f in (
        ProbeFixture(
            "ruth_exit_peak_s5",
            _SHORT + "test_short_section_with_the_corridor_fleet[exit_peak-5]",
            5,
            lambda: _ruth("exit_peak", 5),
        ),
        ProbeFixture(
            "th52_capacity_off_s4",
            _MMT + "test_th52_weave_at_capacity_does_not_lock[off-4-0]",
            4,
            lambda: _th52_capacity(4, "off"),
        ),
        ProbeFixture(
            "th52_capacity_on_s4",
            _MMT + "test_th52_weave_at_capacity_does_not_lock[on-4-0]",
            4,
            lambda: _th52_capacity(4, "on"),
        ),
        ProbeFixture(
            "ruth_entrance_peak_s4",
            _SHORT + "test_short_section_with_the_corridor_fleet[entrance_peak-4]",
            4,
            lambda: _ruth("entrance_peak", 4),
        ),
        ProbeFixture(
            "th52_exit_side_s3",
            _MMT + "test_th52_weave_at_corridor_demand_exit_side",
            3,
            lambda: _th52_corridor_demand(3),
        ),
    )
}
DEFAULT_FIXTURES: tuple[str, ...] = (
    "ruth_exit_peak_s5",
    "th52_capacity_off_s4",
    "th52_capacity_on_s4",
)


# --- the per-step digests ----------------------------------------------------


def _digest(chunks: Sequence[bytes]) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(c)
    return h.hexdigest()[:HASH_HEX]


def _f8(values: Sequence[float] | np.ndarray) -> bytes:
    return np.ascontiguousarray(values, dtype="<f8").tobytes()


def _strings(values: Sequence[str]) -> bytes:
    return ("\x1f".join(values) + "\x1e").encode()


def sumo_step_digest(ids: Sequence[str], lanes: Sequence[str], pos, speed) -> str:
    """Digest of one step's vehicles: ids, lane ids, then positions and speeds as bytes."""
    return _digest((_strings(ids), _strings(lanes), _f8(pos), _f8(speed)))


def traj_step_digests(path: Path) -> dict[float, str]:
    """Per step time, the digest of ``trajectories.parquet``'s rows (id, lane, x, v)."""
    df = pd.read_parquet(path, columns=["t", "veh_id", "lane", "x", "v"])
    df = df.sort_values(["t", "veh_id"], kind="mergesort").reset_index(drop=True)
    out: dict[float, str] = {}
    if not len(df):
        return out
    t = df["t"].to_numpy()
    cuts = np.flatnonzero(np.diff(t)) + 1
    for lo, hi in zip(np.r_[0, cuts], np.r_[cuts, len(df)], strict=True):
        part = df.iloc[lo:hi]
        out[round(float(t[lo]), 6)] = _digest(
            (
                _strings(part["veh_id"].astype(str).tolist()),
                np.ascontiguousarray(part["lane"].to_numpy(), dtype="<i4").tobytes(),
                _f8(part["x"].to_numpy()),
                _f8(part["v"].to_numpy()),
            )
        )
    return out


def _command_row(row: Mapping[str, Any]) -> str:
    return "\x1f".join(
        (
            str(row["veh_id"]),
            str(row["section"]),
            str(row["rule"]),
            str(int(row["lane_from"])),
            str(int(row["lane_to"])),
            float(row["v_cmd_ms"]).hex(),
            str(int(row["lc_mode_set"])),
            float(row["x_m"]).hex(),
        )
    )


def command_step_digests(path: Path) -> dict[float, str]:
    """Per step time, the digest of ``weave_commands.parquet``'s rows, in decision order."""
    if not path.is_file():
        return {}
    df = pd.read_parquet(path)
    out: dict[float, str] = {}
    for t, part in df.groupby("t", sort=True):
        rows = [_command_row(r) for r in part.to_dict("records")]
        out[round(float(t), 6)] = _digest(("\x1e".join(rows).encode(),))
    return out


class _StateListener:
    """A libsumo step listener: after every step, the digest of every vehicle's state.

    Built as a ``traci.StepListener`` subclass at install time (libsumo's
    ``addStepListener`` checks the type). ``dump_at`` steps also keep their rows.
    """

    def __init__(self, dump_at: Sequence[float] = ()) -> None:
        self.digests: dict[float, str | None] = {}
        self.dumps: dict[float, list[list[Any]]] = {}
        self._dump_at = {round(float(t), 6) for t in dump_at}
        self._lib: Any = None
        self._id: int | None = None

    def install(self) -> None:
        import libsumo
        from traci.step import StepListener

        probe = self

        class _Listener(StepListener):
            def step(self, t: float = 0) -> bool:
                probe.observe()
                return True

        self._lib = libsumo
        self._id = libsumo.addStepListener(_Listener())
        if self._id is None:
            raise RuntimeError("libsumo refused the probe's step listener")

    def uninstall(self) -> None:
        if self._lib is not None and self._id is not None:
            self._lib.removeStepListener(self._id)
        self._id = None

    def observe(self) -> None:
        veh = self._lib.vehicle
        t = round(float(self._lib.simulation.getTime()), 6)
        ids = sorted(veh.getIDList())
        if not ids:
            self.digests[t] = None
            return
        lanes = [veh.getLaneID(v) for v in ids]
        pos = [veh.getLanePosition(v) for v in ids]
        speed = [veh.getSpeed(v) for v in ids]
        self.digests[t] = sumo_step_digest(ids, lanes, pos, speed)
        if t in self._dump_at:
            self.dumps[t] = [
                [v, ln, float(p).hex(), float(s).hex()]
                for v, ln, p, s in zip(ids, lanes, pos, speed, strict=True)
            ]


# --- one probe run -------------------------------------------------------------


def with_command_log(cfg: Any) -> Any:
    """``cfg`` with ``record_commands`` on every weave block (hash-neutral, checked)."""
    from flowstate_core.config import ScenarioConfig, config_hash

    raw = cfg.model_dump(mode="json")
    for ramp in raw["network"].get("ramps", []):
        if ramp.get("weave") is not None:
            ramp["weave"]["record_commands"] = True
    out = ScenarioConfig.model_validate(raw)
    if config_hash(out) != config_hash(cfg):
        raise RuntimeError("record_commands moved the config hash; the probe would not reproduce")
    return out


def portable_config_digest(cfg: Any) -> dict[str, Any]:
    """The config's hash payload with ``osm_file`` made repo-relative, digested.

    ``config_hash`` carries the fixture's absolute OSM path (each machine's
    checkout differs), so two platforms compare this digest and the OSM file's
    content hash instead.
    """
    from flowstate_core.config import config_hash, config_hash_payload

    payload = config_hash_payload(cfg)
    network = dict(payload["config"].get("network", {}))
    osm = network.get("osm_file")
    osm_sha = None
    if osm:
        p = Path(osm)
        osm_sha = hashlib.sha256(p.read_bytes()).hexdigest()
        try:
            network["osm_file"] = p.resolve().relative_to(REPO).as_posix()
        except ValueError:
            network["osm_file"] = p.name
    payload = {**payload, "config": {**payload["config"], "network": network}}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        "portable_sha256": hashlib.sha256(canonical.encode()).hexdigest()[:HASH_HEX],
        "osm_file": network.get("osm_file"),
        "osm_sha256": osm_sha,
        "config_hash_this_checkout": config_hash(cfg),
    }


def _version(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, check=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def platform_tag() -> str:
    """``<system>-<machine>``, e.g. ``darwin-arm64`` or ``linux-x86_64``."""
    return f"{platform.system().lower()}-{platform.machine().lower()}"


def platform_header() -> dict[str, Any]:
    """Where the probe ran: OS, machine, Python, the SUMO and numeric stack, the commit."""
    status = _git("status", "--porcelain", "--", "packages", "tests", "scripts/platform_probe.py")
    return {
        "system": platform.system().lower(),
        "machine": platform.machine().lower(),
        "platform_detail": platform.platform(),
        "python": platform.python_version(),
        "eclipse_sumo": _version("eclipse-sumo"),
        "libsumo": _version("libsumo"),
        "numpy": _version("numpy"),
        "pandas": _version("pandas"),
        "pyarrow": _version("pyarrow"),
        "commit": _git("rev-parse", "HEAD"),
        "uncommitted_paths_in_packages_tests": None
        if status is None
        else len([ln for ln in status.splitlines() if ln.strip()]),
    }


def _summary(meta: Mapping[str, Any]) -> dict[str, Any]:
    keep = (
        "n_entered",
        "n_unfinished",
        "n_missed",
        "n_missed_exit",
        "n_exited",
        "n_reached_section_exiting",
        "n_pair_releases",
        "n_forced",
        "n_forced_deferred",
    )
    return {
        "n_vehicles_planned": meta.get("n_vehicles_planned"),
        "n_vehicles_departed": meta.get("n_vehicles_departed"),
        "n_collisions": meta.get("n_collisions"),
        "ramps": {r["name"]: [r["n_departed"], r["n_planned"]] for r in meta.get("ramps", [])},
        "weave_sections": [
            {"ramp": w.get("ramp"), **{k: w.get(k) for k in keep}}
            for w in meta.get("weave_sections", [])
        ],
    }


def _dump_rows(path: Path, times: set[float], columns: Sequence[str]) -> dict[float, list]:
    if not times or not path.is_file():
        return {}
    df = pd.read_parquet(path, columns=list(columns))
    out: dict[float, list] = {}
    for t, part in df[df["t"].round(6).isin(times)].groupby("t", sort=True):
        rows = []
        for r in part.to_dict("records"):
            rows.append([float(v).hex() if isinstance(v, float) else v for v in r.values()])
        out[round(float(t), 6)] = rows
    return out


def probe(
    fixture: ProbeFixture,
    work_dir: Path,
    *,
    duration_s: float | None = None,
    dump_at: Sequence[float] = (),
) -> dict[str, Any]:
    """Run ``fixture`` once under the probe and return the probe document.

    Args:
        fixture: The fixture (its config built as its test builds it).
        work_dir: Where ``run_micro`` writes the run (the caller removes it).
        duration_s: Override of ``sim.duration_s`` (tests use a short run; the
            probe files use the fixture's own).
        dump_at: Step times [s] whose full rows (every channel) are kept too.
    """
    from flowstate_core.config import ScenarioConfig
    from microsim import run_micro

    cfg = fixture.build()
    if duration_s is not None:
        raw = cfg.model_dump(mode="json")
        raw["sim"]["duration_s"] = float(duration_s)
        cfg = ScenarioConfig.model_validate(raw)
    out_hz, step = cfg.sim.output_hz, cfg.sim.step_length_s
    if max(round(1.0 / (out_hz * step)), 1) != 1:
        raise RuntimeError(
            f"{fixture.name}: output_hz {out_hz:g} at step {step:g} s does not capture every step"
        )
    portable = portable_config_digest(cfg)
    probed = with_command_log(cfg)
    listener = _StateListener(dump_at)
    listener.install()
    try:
        paths = run_micro(probed, fixture.seed, work_dir)
    finally:
        listener.uninstall()
    meta = json.loads(paths.meta.read_text())
    n_steps = round(cfg.sim.duration_s / step)
    times = [round((k + 1) * step, 6) for k in range(n_steps)]
    traj = traj_step_digests(paths.trajectories)
    cmd_path = paths.run_dir / "weave_commands.parquet"
    cmds = command_step_digests(cmd_path)
    dump_times = {round(float(t), 6) for t in dump_at}
    traj_rows = _dump_rows(paths.trajectories, dump_times, ("t", "veh_id", "lane", "x", "v"))
    cmd_rows = _dump_rows(
        cmd_path,
        dump_times,
        (
            "t",
            "veh_id",
            "section",
            "rule",
            "lane_from",
            "lane_to",
            "v_cmd_ms",
            "lc_mode_set",
            "x_m",
        ),
    )
    return {
        "probe": "scripts/platform_probe.py",
        "format": FORMAT,
        "fixture": fixture.name,
        "test_id": fixture.test_id,
        "seed": fixture.seed,
        "duration_s": cfg.sim.duration_s,
        "step_s": step,
        "n_steps": n_steps,
        "hash_hex_chars": HASH_HEX,
        "config": portable,
        "platform": platform_header(),
        "summary": _summary(meta),
        "commands_rows": int(meta.get("weave_command_log", {}).get("n_rows", 0)),
        "steps": {
            "sumo": [listener.digests.get(t) for t in times],
            "traj": [traj.get(t) for t in times],
            "commands": [cmds.get(t) for t in times],
        },
        "dumps": {
            repr(t): {
                "sumo": listener.dumps.get(t),
                "traj": traj_rows.get(t),
                "commands": cmd_rows.get(t),
            }
            for t in sorted(dump_times)
        },
    }


def write_probe(doc: Mapping[str, Any], path: Path) -> None:
    """Write a probe document: the header indented, each step list on one line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    head = {k: v for k, v in doc.items() if k not in ("steps", "dumps")}
    lines = json.dumps(head, indent=1).rstrip("}").rstrip().rstrip(",")
    steps = ",\n".join(
        f"  {json.dumps(c)}: {json.dumps(doc['steps'][c], separators=(',', ':'))}" for c in CHANNELS
    )
    dumps = json.dumps(doc.get("dumps", {}), separators=(",", ":"))
    path.write_text(f'{lines},\n "steps": {{\n{steps}\n }},\n "dumps": {dumps}\n}}\n')


# --- comparison ----------------------------------------------------------------


def compare(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    """Where two probe documents of the same fixture part, per channel and overall.

    Returns ``comparable`` (same fixture, seed, step grid and portable config),
    per channel the first differing step index (``None`` if none) and the
    number of differing steps, the first differing step overall with the
    channels differing at it, and a ``verdict``: ``identical``,
    ``state_first`` (``sumo`` or ``traj`` parts at a step whose commands and
    every earlier step agree), ``commands_first`` (the commands part at a step
    whose state agrees) or ``same_step`` (both part at the same step).
    """
    reasons = [
        f"{k}: {a.get(k)!r} != {b.get(k)!r}"
        for k in ("fixture", "seed", "step_s", "n_steps", "hash_hex_chars")
        if a.get(k) != b.get(k)
    ]
    pa_, pb_ = a.get("config", {}), b.get("config", {})
    for k in ("portable_sha256", "osm_sha256"):
        if pa_.get(k) != pb_.get(k):
            reasons.append(f"config {k}: {pa_.get(k)!r} != {pb_.get(k)!r}")
    out: dict[str, Any] = {"comparable": not reasons, "reasons": reasons, "channels": {}}
    if reasons:
        out["verdict"] = "incomparable"
        return out
    step = float(a["step_s"])
    first: dict[str, int | None] = {}
    for c in CHANNELS:
        sa, sb = a["steps"][c], b["steps"][c]
        diff = [i for i, (x, y) in enumerate(zip(sa, sb, strict=True)) if x != y]
        first[c] = diff[0] if diff else None
        out["channels"][c] = {
            "first_step": first[c],
            "first_t_s": None if first[c] is None else round((first[c] + 1) * step, 6),
            "n_differing": len(diff),
        }
    hits = [i for i in first.values() if i is not None]
    if not hits:
        out.update(first_step=None, first_t_s=None, differing_at_first=[], verdict="identical")
        return out
    k = min(hits)
    at_k = [c for c in CHANNELS if first[c] == k]
    state = any(c in at_k for c in ("sumo", "traj"))
    command = "commands" in at_k
    verdict = "same_step" if state and command else "state_first" if state else "commands_first"
    out.update(
        first_step=k, first_t_s=round((k + 1) * step, 6), differing_at_first=at_k, verdict=verdict
    )
    return out


_VERDICT_TEXT = {
    "identical": "the two runs agree at every step: no divergence on this fixture",
    "state_first": (
        "the state differs first: SUMO's step parted from identical observed state and "
        "identical commands (SUMO's own arithmetic, or SUMO state outside (id, lane, x, v))"
    ),
    "commands_first": (
        "a command differs first: the runner decided differently on identical (id, lane, x, v) "
        "(Python arithmetic, or a SUMO value outside that tuple that a rule reads)"
    ),
    "same_step": (
        "state and commands part at the same step: the step's state differs and the runner's "
        "commands follow it (state first)"
    ),
}


def _describe(doc: Mapping[str, Any], label: str, path: str) -> str:
    p = doc.get("platform", {})
    return (
        f"{label}: {p.get('system')} {p.get('machine')}, python {p.get('python')}, "
        f"eclipse-sumo {p.get('eclipse_sumo')}, libsumo {p.get('libsumo')}, numpy {p.get('numpy')}, "
        f"commit {str(p.get('commit'))[:10]} ({path})"
    )


def report(a: Mapping[str, Any], b: Mapping[str, Any], path_a: str, path_b: str) -> list[str]:
    """The comparison as printed lines."""
    res = compare(a, b)
    lines = [
        f"fixture: {a.get('fixture')} ({a.get('test_id')}), seed {a.get('seed')}, "
        f"{a.get('n_steps')} steps of {a.get('step_s')} s",
        _describe(a, "a", path_a),
        _describe(b, "b", path_b),
    ]
    if not res["comparable"]:
        lines += ["not comparable:", *(f"  {r}" for r in res["reasons"])]
        return lines
    for c in CHANNELS:
        ch = res["channels"][c]
        lines.append(
            f"channel {c}: "
            + (
                "identical at every step"
                if ch["first_step"] is None
                else f"first differs at step {ch['first_step']} (t = {ch['first_t_s']} s); "
                f"{ch['n_differing']} of {a['n_steps']} steps differ"
            )
        )
    if res["first_step"] is not None:
        lines.append(
            f"first differing step: {res['first_step']} (t = {res['first_t_s']} s), "
            f"channels: {', '.join(res['differing_at_first'])}"
        )
    lines.append(f"verdict: {res['verdict']} - {_VERDICT_TEXT[res['verdict']]}")
    sa, sb = a.get("summary", {}), b.get("summary", {})
    lines.append(f"summary a: {json.dumps(sa, separators=(',', ':'))}")
    lines.append(f"summary b: {json.dumps(sb, separators=(',', ':'))}")
    return lines


# --- the recorder's records (tests/test_microsim/_platform_record.py) ------------


def read_records(path: Path) -> dict[str, dict[str, Any]]:
    """A ``FLOWSTATE_RECORD_STATE`` file's records by test id (the last record of an id wins)."""
    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["test_id"]] = rec
    return out


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    if isinstance(value, list):
        out = {}
        for i, v in enumerate(value):
            out.update(_flatten(v, f"{prefix}.{i}" if prefix else str(i)))
        return out
    return {prefix: value}


def compare_records(
    a: Mapping[str, Mapping[str, Any]], b: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Per test id: both outcomes, where each run's trajectories part, the numbers that differ.

    ``trajectories`` holds, per run, ``"identical"`` or the first 60-s window
    (``t // 60``) whose digest differs; ``differing`` the flattened keys of the
    runs (digests left out) and the states whose values differ, with both values.
    """
    rows: list[dict[str, Any]] = []
    for tid in sorted(set(a) | set(b)):
        ra, rb = a.get(tid), b.get(tid)
        if ra is None or rb is None:
            rows.append({"test_id": tid, "missing_in": "a" if ra is None else "b"})
            continue
        traj: list[str | int] = []
        for x, y in zip(ra.get("runs", []), rb.get("runs", []), strict=False):
            ta, tb = x["trajectories"], y["trajectories"]
            if ta["sha256"] == tb["sha256"]:
                traj.append("identical")
                continue
            keys = sorted(set(ta["by_window"]) | set(tb["by_window"]), key=int)
            traj.append(
                next(
                    (int(k) for k in keys if ta["by_window"].get(k) != tb["by_window"].get(k)),
                    -1,
                )
            )

        def numbers(rec: Mapping[str, Any]) -> dict[str, Any]:
            runs = [
                {k: v for k, v in r.items() if k != "trajectories"} for r in rec.get("runs", [])
            ]
            return _flatten({"runs": runs, "states": rec.get("states", [])})

        na, nb = numbers(ra), numbers(rb)
        differing = {
            k: (na.get(k), nb.get(k)) for k in sorted(set(na) | set(nb)) if na.get(k) != nb.get(k)
        }
        rows.append(
            {
                "test_id": tid,
                "outcome_a": ra["outcome"],
                "outcome_b": rb["outcome"],
                "n_runs": (len(ra.get("runs", [])), len(rb.get("runs", []))),
                "trajectories": traj,
                "differing": differing,
            }
        )
    return rows


def records_report(rows: Sequence[Mapping[str, Any]], max_keys: int = 8) -> list[str]:
    """The record comparison as printed lines, one block per test."""
    lines: list[str] = []
    for r in rows:
        tid = r["test_id"].split("/")[-1]
        if "missing_in" in r:
            lines.append(f"{tid}: missing in {r['missing_in']}")
            continue
        same = "same outcome" if r["outcome_a"] == r["outcome_b"] else "OUTCOME DIFFERS"
        traj = ", ".join(
            "identical" if t == "identical" else f"part from minute {t}" for t in r["trajectories"]
        )
        lines.append(
            f"{tid}: {r['outcome_a']} / {r['outcome_b']} ({same}); trajectories: {traj or 'no run'}; "
            f"{len(r['differing'])} numbers differ"
        )
        for k, (va, vb) in list(r["differing"].items())[:max_keys]:
            lines.append(f"    {k}: {va!r} -> {vb!r}")
        if len(r["differing"]) > max_keys:
            lines.append(f"    ... {len(r['differing']) - max_keys} more")
    return lines


# --- CLI -------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--fixture",
        nargs="+",
        default=list(DEFAULT_FIXTURES),
        help=f"fixture names, or 'all' (default: {' '.join(DEFAULT_FIXTURES)})",
    )
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--dump-at", type=float, nargs="*", default=[], help="step times to dump")
    p.add_argument("--duration-s", type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument("--list", action="store_true", help="list the fixtures and exit")
    p.add_argument("--compare", nargs=2, metavar=("A", "B"), help="compare two probe files")
    p.add_argument(
        "--compare-records",
        nargs=2,
        metavar=("A", "B"),
        help="compare two FLOWSTATE_RECORD_STATE files (tests/test_microsim/_platform_record.py)",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.list:
        for f in FIXTURES.values():
            mark = " (default)" if f.name in DEFAULT_FIXTURES else ""
            print(f"{f.name}{mark}: seed {f.seed}, {f.test_id}")
        return 0
    if args.compare:
        path_a, path_b = args.compare
        a = json.loads(Path(path_a).read_text())
        b = json.loads(Path(path_b).read_text())
        for line in report(a, b, path_a, path_b):
            print(line)
        res = compare(a, b)
        return 2 if not res["comparable"] else 0 if res["verdict"] == "identical" else 1
    if args.compare_records:
        rows = compare_records(*(read_records(Path(p)) for p in args.compare_records))
        for line in records_report(rows):
            print(line)
        agree = all(
            "missing_in" not in r
            and r["outcome_a"] == r["outcome_b"]
            and not r["differing"]
            and all(t == "identical" for t in r["trajectories"])
            for r in rows
        )
        return 0 if agree else 1
    names = list(FIXTURES) if args.fixture == ["all"] else args.fixture
    unknown = [n for n in names if n not in FIXTURES]
    if unknown:
        raise SystemExit(f"unknown fixture(s) {unknown}; --list shows them")
    for name in names:
        with tempfile.TemporaryDirectory(prefix=f"probe_{name}_") as tmp:
            doc = probe(FIXTURES[name], Path(tmp), duration_s=args.duration_s, dump_at=args.dump_at)
        path = args.out_dir / f"{name}_{platform_tag()}.json"
        write_probe(doc, path)
        n_cmd = sum(x is not None for x in doc["steps"]["commands"])
        print(
            f"{name}: {doc['n_steps']} steps, {n_cmd} with commands, "
            f"summary {json.dumps(doc['summary'], separators=(',', ':'))} -> {path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
