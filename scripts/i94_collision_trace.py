"""Trace logged SUMO collisions in kept-trajectory runs (docs/I94_CAL_COLLISIONS.md §10).

Reads, per run directory (``<root>/<label>/<config hash>/<seed>/``, as
``scripts/run_pairs.py`` writes them), ``meta.json``, ``vehicles.parquet``,
``trajectories.parquet`` (only the row groups whose ``t`` range meets an event's
window) and ``net/demand.rou.xml`` (the pair's vTypes). No simulation.

For each run matched by (config hash, seed) to an expected run:

1. **Reproduction (the first check).** The run's ``meta.json`` collision log must
   hold each expected event: the same collider and victim, the same lane (edge and
   index), ``pos_m`` within ``--pos-tol`` (1e-6 m) and ``t`` within one simulation
   step; and ``n_collisions`` must equal the number of expected events (0 for a
   control). The config hash must be the expected one. A run that does not
   reproduce is still traced, labelled ``reproduced: false``: its windows are then
   a different realisation from the reference run's and must not be read as its
   events. The trajectory is also checked against the log (the collider in the
   logged lane at ``t``, at ``x0 + pos_m`` when the edge's start ``x0`` is in
   ``meta.json["ramps"]``).
2. **The window.** Per step from ``t − --before-s`` (15 s) to ``t + --after-s`` (1 s):
   lane, ``x``, position on the collision edge, speed and acceleration of the rear
   car, the front car and the vehicle ahead of the front car (nearest ahead in the
   front car's lane on the collision edge at ``t``), with bumper gaps.
3. **Braking.** The rear car's deceleration against its vType's ``decel`` (``b``)
   and ``emergencyDecel`` (when the vType does not set it: SUMO's passenger default
   ``max(decel, 9)``, ``SUMOVTypeParameter.cpp`` 979–1020, as docs/I24_STRATEGIES.md
   records; no default is assumed for another vClass), and the bound a held speed
   command can reach (``microsim.runner._command_decel``: ``b`` for EIDM). Per step
   ``at_b`` (``|−a − b| ≤ --decel-tol``, 1e-3 m/s²), ``beyond_b``, ``below_b`` or
   ``not_braking``; over the last ``--last-steps`` (6) steps to contact, whether the
   rear car was pinned at ``b`` while its gap closed (§4.2's signature) or braked
   beyond it.
4. **Lane entries.** For both cars, the step each entered the collision lane in
   the final stretch before contact (``--lookback-s`` 60 s): a ``lane_change`` from
   the ``right`` (lower index) or ``left``, an ``edge_crossing`` (onto the collision
   edge from upstream, where lane indices are renumbered: not a lane change) or
   ``appeared`` (the vehicle's first corridor sample: a ramp vehicle arriving on the
   axis). ``opposing_entries``: both cars changed into the lane in the same sampled
   step from opposite sides (WP-92; §5.2's reading). Also every entry into the
   lane on the collision edge by any vehicle within ``--behind-m`` (150 m) behind
   and ``--ahead-m`` (60 m) ahead of the contact in the window, and each car's lane
   on arriving at the collision edge (the T1 control comparison).
5. **Weave commands.** The runner logs the weave's commands only when the scenario's
   weave block sets ``WeaveSpec.record_commands`` (2026-10-07; docs/CONTRACTS.md §3):
   ``weave_commands.parquet`` beside ``meta.json`` (one row per command decision:
   ``t``, ``veh_id``, ``section``, ``rule``, ``lane_from``, ``lane_to``, ``v_cmd_ms``,
   ``lc_mode_set``, ``x_m``), recorded in ``meta.json["weave_command_log"]``
   (``file``, ``n_rows``, ``sections``). When the file is here, it is read through an
   open file object: the rear car's speed targets in the window (the one-step
   ``slowDown`` rules ``cooperate`` and ``ease``) are listed, with every command of the
   rear car in the window (``commands``, any rule), and ``active_in_contact_step``
   says whether a target was issued at ``t − step`` (a target issued after step k
   acts in step k+1). When ``meta.json`` records a log but the file is not here (a
   meta-only archive such as p8c's) or the file cannot be read, the block says so
   (``log_recorded``, ``log_rows``, ``log_problem``) and reads ``logged: false``: the
   inferred reading (braking and entries) stands. A run that recorded no log reads
   ``logged: false`` exactly as before the recorder existed.

Each run's window rows (every vehicle within the spatial band, and the three cars
throughout) are written to ``<run>/collision_slice.parquet``; the trajectories
themselves are never copied. The artifact (``--out``) holds every run's blocks, a
summary per event, the paired runs (events sharing a ``pair`` key: T1 and its
``_dc_cal`` control) and, at the top, ``reproduced`` with a plain statement of what
did not reproduce. Stage p8 probably ran at commit ``31c04c4`` (§10); a run that
does not reproduce here must be relaunched from that commit before it is read.

Built-in expectations (``--expect p8c``, the default): stage p8's six logged
collisions (§3: R1–R3, T1–T3; positions to full precision from the committed
battery artifacts' ``collisions.locations``) and the no-collision control
(``_dc_cal``, seed 165503670820534583, its window at T1's 574.5 s for v19448 and
v27416). ``--expect FILE`` reads the same structure from JSON (a list of runs:
``arm``, ``config_hash``, ``seed``, ``events``: ``label``, ``t``, ``collider``,
``victim``, ``lane``, ``pos_m``, ``collision``, ``hypothesis``, ``pair``).

Exit status: 0 when every expected run was found, reproduced and traced (it has its
``trajectories.parquet``), 3 otherwise (the artifact is written either way), 2 on a
usage error.

Usage::

    uv run --no-sync python scripts/i94_collision_trace.py --root runs/p8c \\
        --out artifacts/i94_cal_collisions_trace.json
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from microsim.runner import _command_decel
from validation.battery import json_safe

SCHEMA_VERSION: Final[int] = 1
#: The commit stage p8 most probably ran at (docs/I94_CAL_COLLISIONS.md §10).
P8_REFERENCE_COMMIT: Final[str] = "31c04c4"
#: SUMO 1.27.1's passenger ``emergencyDecel`` floor [m/s²] for a vType that does not
#: set it: ``max(decel, 9)`` (``SUMOVTypeParameter.cpp`` 979–1020, docs/I24_STRATEGIES.md).
SUMO_PASSENGER_EMERGENCY_DECEL_MS2: Final[float] = 9.0
#: vClasses that take the passenger default above (the runner writes none, or ``hov``).
PASSENGER_VCLASSES: Final[frozenset[str | None]] = frozenset({None, "passenger", "hov"})
SLICE_FILE: Final[str] = "collision_slice.parquet"
#: The runner's weave command log (``microsim.runner.WEAVE_COMMANDS_FILE``) and its
#: ``meta.json`` record (module item 5).
COMMANDS_FILE: Final[str] = "weave_commands.parquet"
COMMANDS_META_KEY: Final[str] = "weave_command_log"
#: The log's speed-target rules: the one-step ``slowDown`` targets
#: (``microsim.runner.WEAVE_COMMAND_RULES``; ``ceiling`` is a desired-speed cap, not one).
SPEED_TARGET_RULES: Final[frozenset[str]] = frozenset({"cooperate", "ease"})
#: The log's columns this reader needs.
COMMAND_COLUMNS: Final[tuple[str, ...]] = ("t", "veh_id", "rule")
#: The block of a run that recorded no command log (unchanged since the recorder existed).
NOT_LOGGED_NOTE: Final[str] = (
    "the runner does not log weave commands (docs/I94_CAL_COLLISIONS.md §2, §10)"
)
TRAJ_COLUMNS: Final[tuple[str, ...]] = ("t", "veh_id", "x", "lane", "v", "a")
VTYPE_ATTRS: Final[tuple[str, ...]] = (
    "carFollowModel",
    "accel",
    "decel",
    "emergencyDecel",
    "tau",
    "minGap",
    "length",
    "actionStepLength",
    "vClass",
)
_EPS_T: Final[float] = 1e-6
#: Where the collision edge's end is unknown, the front car's leader is looked for this far ahead [m].
LEADER_SEARCH_M: Final[float] = 250.0
#: Margin [m] around the spatial band within which rows are scanned for lane entries (a
#: vehicle's previous sample is at most one step of travel, under 20 m, behind).
ENTRY_SCAN_MARGIN_M: Final[float] = 100.0


@dataclass(frozen=True)
class Event:
    """One expected event: a logged collision, or a probe window (``collision`` False)."""

    label: str
    t: float
    collider: str
    victim: str
    lane: str
    pos_m: float | None
    collision: bool = True
    hypothesis: str | None = None
    pair: str | None = None


@dataclass(frozen=True)
class RunExpectation:
    """The events one reference run logged (none for a control)."""

    arm: str
    config_hash: str
    seed: int
    events: tuple[Event, ...] = field(default_factory=tuple)

    @property
    def n_collisions(self) -> int:
        return sum(1 for e in self.events if e.collision)


_DC = "beaaa710e6b3"  # scenarios/mndot_i94_wb_stpaul_weave_dc_cal.yaml
_NF = "182e3ec2f500"  # scenarios/mndot_i94_wb_stpaul_weave_dc_cal_netfix.yaml
#: Stage p8's six collisions and the paired control (docs/I94_CAL_COLLISIONS.md §3, §10).
P8C_EXPECTED: Final[tuple[RunExpectation, ...]] = (
    RunExpectation(
        "dc_cal",
        _DC,
        134183728835869882,
        (
            Event(
                "R1",
                14356.5,
                "v17620",
                "v17619",
                "999007700_0",
                12.160338265182489,
                True,
                "command_cap",
            ),
        ),
    ),
    RunExpectation(
        "dc_cal",
        _DC,
        6134032994440706937,
        (
            Event(
                "R2",
                8443.5,
                "v17184",
                "v17183",
                "999007700_0",
                10.028103818834543,
                True,
                "command_cap",
            ),
        ),
    ),
    RunExpectation(
        "dc_cal",
        _DC,
        165503670820534583,
        (
            Event(
                "T1-control", 574.5, "v19448", "v27416", "51388891_1", None, False, "control", "T1"
            ),
        ),
    ),
    RunExpectation(
        "dc_cal_netfix",
        _NF,
        134183728835869882,
        (
            Event(
                "R3",
                13421.0,
                "v17557",
                "v17556",
                "999007700_0",
                114.92945249696474,
                True,
                "command_cap",
            ),
        ),
    ),
    RunExpectation(
        "dc_cal_netfix",
        _NF,
        165503670820534583,
        (
            Event(
                "T1",
                574.5,
                "v19448",
                "v27416",
                "51388891_1",
                76.74139768824973,
                True,
                "opposing_entries",
                "T1",
            ),
        ),
    ),
    RunExpectation(
        "dc_cal_netfix",
        _NF,
        677105600768189526,
        (
            Event(
                "T2",
                3538.0,
                "v12401",
                "v28312",
                "51388891_2",
                36.07955938403949,
                True,
                "opposing_entries",
            ),
        ),
    ),
    RunExpectation(
        "dc_cal_netfix",
        _NF,
        6953598295321596746,
        (
            Event(
                "T3",
                4009.5,
                "v20578",
                "v28473",
                "51388891_2",
                85.16994553590914,
                True,
                "opposing_entries",
            ),
        ),
    ),
)


@dataclass(frozen=True)
class Params:
    """Window and tolerance settings (the CLI's options)."""

    before_s: float = 15.0
    after_s: float = 1.0
    lookback_s: float = 60.0
    behind_m: float = 150.0
    ahead_m: float = 60.0
    decel_tol: float = 1e-3
    pos_tol: float = 1e-6
    last_steps: int = 6


# --- inputs --------------------------------------------------------------------------------


def load_expectations(source: str) -> tuple[RunExpectation, ...]:
    """``"p8c"`` (the built-in table) or a JSON file of the same structure."""
    if source == "p8c":
        return P8C_EXPECTED
    runs = json.loads(Path(source).read_text())
    return tuple(
        RunExpectation(
            arm=str(r["arm"]),
            config_hash=str(r["config_hash"]),
            seed=int(r["seed"]),
            events=tuple(Event(**e) for e in r.get("events", [])),
        )
        for r in runs
    )


def find_runs(root: Path) -> list[Path]:
    """Every run directory (one holding ``meta.json``) under ``root``, sorted."""
    return sorted(p.parent for p in root.rglob("meta.json") if p.parent.name != "net")


def lane_parts(lane_id: str) -> tuple[str, int]:
    """``"999007700_0"`` -> ``("999007700", 0)``."""
    edge, _, index = lane_id.rpartition("_")
    return edge, int(index)


def _step_s(meta: Mapping[str, Any]) -> float:
    return float(meta["config"]["sim"]["step_length_s"])


def _sample_s(meta: Mapping[str, Any]) -> float:
    hz = meta.get("output_hz_realized") or meta["config"]["sim"]["output_hz"]
    return 1.0 / float(hz)


def edge_range(meta: Mapping[str, Any], edge: str) -> tuple[float, float] | None:
    """Trajectory x of an edge's start and end, when ``meta.json["ramps"]`` has it."""
    for r in meta.get("ramps") or []:
        if r.get("attach_edge") == edge and r.get("attach_x_m") is not None:
            return float(r["attach_x_m"]), float(r["attach_end_x_m"])
    return None


def section_of(meta: Mapping[str, Any], edge: str) -> dict[str, Any] | None:
    """The weaving section (``meta.json["weave_sections"]``) whose edges hold ``edge``."""
    for ws in meta.get("weave_sections") or []:
        if edge in (ws.get("edges") or []):
            return {
                "ramp": ws.get("ramp"),
                "exit": ws.get("exit"),
                "exit_edge": ws.get("exit_edge"),
            }
    return None


def read_window(path: Path, t_lo: float, t_hi: float) -> pd.DataFrame:
    """Trajectory rows with ``t_lo <= t <= t_hi``, reading only row groups that meet it.

    Read through an open file object, as every trajectory reader here does
    (``microsim.demand_adapter.read_trajectories``).
    """
    frames: list[pd.DataFrame] = []
    with open(path, "rb") as f:
        pf = pq.ParquetFile(f)
        t_idx = pf.schema_arrow.get_field_index("t")
        for i in range(pf.metadata.num_row_groups):
            stats = pf.metadata.row_group(i).column(t_idx).statistics
            if stats is not None and stats.has_min_max:
                if stats.max < t_lo - _EPS_T or stats.min > t_hi + _EPS_T:
                    continue
            tab = pf.read_row_group(i, columns=list(TRAJ_COLUMNS))
            df = tab.to_pandas()
            frames.append(df[(df["t"] >= t_lo - _EPS_T) & (df["t"] <= t_hi + _EPS_T)])
    if not frames:
        return pd.DataFrame({c: pd.Series(dtype=float) for c in TRAJ_COLUMNS})
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["t", "veh_id"], kind="stable").reset_index(drop=True)


def read_vtypes(run_dir: Path, veh_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """The vType attributes of ``veh_ids`` from ``net/demand.rou.xml`` (two streaming passes)."""
    routes = run_dir / "net" / "demand.rou.xml"
    wanted = set(veh_ids)
    if not routes.is_file() or not wanted:
        return {}
    type_of: dict[str, str] = {}
    for _, el in ET.iterparse(routes, events=("end",)):
        if el.tag == "vehicle" and el.get("id") in wanted:
            type_of[str(el.get("id"))] = str(el.get("type"))
        el.clear()
    types: dict[str, dict[str, str]] = {}
    wanted_types = set(type_of.values())
    for _, el in ET.iterparse(routes, events=("end",)):
        if el.tag == "vType" and el.get("id") in wanted_types:
            types[str(el.get("id"))] = {k: v for k, v in el.attrib.items()}
        el.clear()
    out: dict[str, dict[str, Any]] = {}
    for vid, tid in type_of.items():
        attrs = types.get(tid, {})
        rec: dict[str, Any] = {"type": tid}
        for k in VTYPE_ATTRS:
            raw = attrs.get(k)
            if raw is None:
                rec[k] = None
            elif k in ("carFollowModel", "vClass"):
                rec[k] = raw
            else:
                rec[k] = float(raw)
        rec["emergencyDecel_source"] = "vType"
        if rec["emergencyDecel"] is None:
            if rec["decel"] is not None and rec["vClass"] in PASSENGER_VCLASSES:
                rec["emergencyDecel"] = max(float(rec["decel"]), SUMO_PASSENGER_EMERGENCY_DECEL_MS2)
                rec["emergencyDecel_source"] = (
                    "SUMO passenger default max(decel, 9) (not in the vType)"
                )
            else:
                rec["emergencyDecel_source"] = (
                    "not in the vType; no default assumed for this vClass"
                )
        rec["command_decel_bound"] = (
            _command_decel(
                str(rec["carFollowModel"]), float(rec["decel"]), float(rec["emergencyDecel"])
            )
            if rec["carFollowModel"]
            and rec["decel"] is not None
            and rec["emergencyDecel"] is not None
            else None
        )
        out[vid] = rec
    return out


def read_vehicle_rows(run_dir: Path, veh_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """The ``vehicles.parquet`` rows of ``veh_ids`` (only the columns present)."""
    path = run_dir / "vehicles.parquet"
    if not path.is_file():
        return {}
    with open(path, "rb") as f:
        df = pd.read_parquet(f)
    df = df[df["veh_id"].isin(set(veh_ids))]
    keep = [
        c
        for c in (
            "veh_id",
            "origin",
            "destination",
            "depart_planned_s",
            "depart_s",
            "entry_t_s",
            "entry_x_m",
            "entry_lane",
            "last_t_s",
            "last_x_m",
            "last_lane",
            "arrived",
        )
        if c in df.columns
    ]
    return {str(r["veh_id"]): {k: _py(r[k]) for k in keep} for _, r in df[keep].iterrows()}


def read_first_samples(run_dir: Path) -> dict[str, float]:
    """Every departed vehicle's first corridor sample time (``vehicles.parquet`` ``entry_t_s``)."""
    path = run_dir / "vehicles.parquet"
    if not path.is_file():
        return {}
    with open(path, "rb") as f:
        df = pd.read_parquet(f)
    if "entry_t_s" not in df.columns:
        return {}
    df = df[df["entry_t_s"].notna()]
    return dict(zip(df["veh_id"].astype(str), df["entry_t_s"].astype(float), strict=True))


@dataclass(frozen=True)
class CommandLog:
    """A run's weave command log, as far as its directory holds it (module item 5).

    Attributes:
        frame: The log's rows; ``None`` when the file is not here or cannot be read.
        recorded: ``meta.json["weave_command_log"]``; ``None`` when the run recorded no log.
        problem: Why a log that exists cannot be read here; ``None`` when it was read
            or none was recorded.
    """

    frame: pd.DataFrame | None = None
    recorded: Mapping[str, Any] | None = None
    problem: str | None = None

    @property
    def present(self) -> bool:
        """Whether the run recorded a log, or a log file is here."""
        return self.frame is not None or self.recorded is not None or self.problem is not None

    def summary(self) -> dict[str, Any]:
        """The run-level record of the log (only written when :attr:`present`)."""
        return {
            "file": COMMANDS_FILE,
            "read": self.frame is not None,
            "n_rows": None if self.frame is None else len(self.frame),
            "recorded": None if self.recorded is None else dict(self.recorded),
            "problem": self.problem,
        }


def read_commands(run_dir: Path, meta: Mapping[str, Any]) -> CommandLog:
    """The run's weave command log, when it has one (module item 5); never raises on a bad log.

    The file is read through an open file object (a path handed to pyarrow builds a
    ``LocalFileSystem``, which fails once libsumo's Arrow is loaded in the process).
    """
    raw = meta.get(COMMANDS_META_KEY)
    recorded: Mapping[str, Any] | None
    if raw is None:
        recorded = None
    elif isinstance(raw, Mapping):
        recorded = raw
    else:  # not the contract's object: recorded, but nothing more can be said
        recorded = {}
    path = run_dir / COMMANDS_FILE
    if path.is_file():
        try:
            with open(path, "rb") as f:
                frame = pq.read_table(f).to_pandas()
        except (OSError, ValueError, pa.ArrowException) as exc:
            return CommandLog(None, recorded, f"{COMMANDS_FILE} cannot be read ({exc})")
        missing = [c for c in COMMAND_COLUMNS if c not in frame.columns]
        if missing:
            return CommandLog(
                None, recorded, f"{COMMANDS_FILE} lacks the column(s) {missing} of the runner's log"
            )
        return CommandLog(frame, recorded, None)
    if recorded is not None:
        return CommandLog(
            None,
            recorded,
            f"the run recorded weave commands (meta.json {COMMANDS_META_KEY}: "
            f"{recorded.get('n_rows')} rows) but {COMMANDS_FILE} is not in this directory "
            "(a meta-only archive)",
        )
    return CommandLog()


def _py(v: Any) -> Any:
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
        return None
    if isinstance(v, np.generic):
        return v.item()
    return v


def _f(v: Any) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


# --- the checks ----------------------------------------------------------------------------


def check_reproduction(
    meta: Mapping[str, Any], exp: RunExpectation, params: Params
) -> dict[str, Any]:
    """Compare a run's collision log with the reference run's events (module item 1)."""
    step = _step_s(meta)
    logged = list(meta.get("collisions") or [])
    events: list[dict[str, Any]] = []
    used: set[int] = set()
    for ev in exp.events:
        if not ev.collision:
            continue
        best: tuple[int, dict[str, Any]] | None = None
        for i, c in enumerate(logged):
            if (
                i in used
                or str(c.get("collider")) != ev.collider
                or str(c.get("victim")) != ev.victim
            ):
                continue
            if best is None or abs(float(c["t"]) - ev.t) < abs(float(best[1]["t"]) - ev.t):
                best = (i, c)
        rec: dict[str, Any] = {
            "label": ev.label,
            "expected": asdict(ev),
            "logged": None,
            "matched": False,
        }
        if best is not None:
            used.add(best[0])
            c = best[1]
            dt = float(c["t"]) - ev.t
            dpos = None if ev.pos_m is None else float(c["pos_m"]) - ev.pos_m
            checks = {
                "collider": True,
                "victim": True,
                "lane": str(c.get("lane")) == ev.lane,
                "t_within_one_step": abs(dt) <= step + _EPS_T,
                "pos_within_tol": dpos is None or abs(dpos) <= params.pos_tol,
            }
            rec.update(
                logged=dict(c), dt_s=dt, dpos_m=dpos, checks=checks, matched=all(checks.values())
            )
        events.append(rec)
    unexpected = [dict(c) for i, c in enumerate(logged) if i not in used]
    n_logged = meta.get("n_collisions")
    hash_ok = meta.get("config_hash") == exp.config_hash
    count_ok = n_logged == exp.n_collisions
    reproduced = hash_ok and count_ok and all(e["matched"] for e in events)
    problems: list[str] = []
    if not hash_ok:
        problems.append(f"config hash {meta.get('config_hash')}, expected {exp.config_hash}")
    if not count_ok:
        problems.append(f"n_collisions {n_logged}, expected {exp.n_collisions}")
    for e in events:
        if not e["matched"]:
            if e["logged"] is None:
                problems.append(
                    f"{e['label']}: no logged collision of {e['expected']['collider']} into {e['expected']['victim']}"
                )
            else:
                bad = [k for k, ok in e["checks"].items() if not ok]
                problems.append(
                    f"{e['label']}: logged event differs ({', '.join(bad)}; dt {e['dt_s']:+.3f} s, dpos {e['dpos_m']})"
                )
    return {
        "reproduced": reproduced,
        "config_hash": meta.get("config_hash"),
        "config_hash_expected": exp.config_hash,
        "n_collisions": n_logged,
        "n_collisions_expected": exp.n_collisions,
        "events": events,
        "unexpected_logged": unexpected,
        "problems": problems,
    }


def _track(frame: pd.DataFrame, vid: str) -> pd.DataFrame:
    return frame[frame["veh_id"] == vid].sort_values("t", kind="stable").reset_index(drop=True)


def _row_at(track: pd.DataFrame, t: float) -> pd.Series | None:
    hit = track[(track["t"] - t).abs() <= _EPS_T]
    return None if hit.empty else hit.iloc[0]


def _on_edge(x: float, rng: tuple[float, float | None] | None) -> bool:
    if rng is None:
        return True
    x0, x1 = rng
    return x >= x0 - _EPS_T and (x1 is None or x <= x1 + _EPS_T)


def lane_entry(
    track: pd.DataFrame,
    t_c: float,
    lane: int,
    rng: tuple[float, float | None] | None,
    first_sample_t: float | None,
) -> dict[str, Any]:
    """How a vehicle came to be in ``lane`` on the collision edge at ``t_c`` (module item 4).

    Walks back from ``t_c`` over the final stretch the vehicle spent in ``lane`` on the
    edge; the row that starts it is the entry. ``kind``: ``lane_change`` (the previous
    sample is on the edge, in another lane; ``side`` ``right`` from a lower index,
    ``left`` from a higher one), ``edge_crossing`` (the previous sample is upstream of
    the edge: lane indices are renumbered there, so not a lane change), ``appeared``
    (the entry row is the vehicle's first corridor sample), ``before_lookback`` (in
    the lane on the edge throughout the frame) or ``not_in_lane`` (not in it at
    ``t_c``).
    """
    tr = track[track["t"] <= t_c + _EPS_T]
    if tr.empty:
        return {"kind": "absent"}
    last = tr.iloc[-1]
    if (
        abs(float(last["t"]) - t_c) > _EPS_T
        or int(last["lane"]) != lane
        or not _on_edge(float(last["x"]), rng)
    ):
        return {"kind": "not_in_lane", "lane_at_t": int(last["lane"]), "t_last": float(last["t"])}
    i = len(tr) - 1
    while i > 0:
        prev = tr.iloc[i - 1]
        if int(prev["lane"]) != lane or not _on_edge(float(prev["x"]), rng):
            break
        i -= 1
    row = tr.iloc[i]
    rec: dict[str, Any] = {"t": float(row["t"]), "x": float(row["x"]), "lane": lane}
    if i == 0:
        if first_sample_t is not None and abs(float(row["t"]) - first_sample_t) <= _EPS_T:
            rec.update(kind="appeared", from_lane=None, side=None)
        else:
            rec.update(
                kind="before_lookback",
                t=None,
                from_lane=None,
                side=None,
                in_lane_since_s=float(row["t"]),
            )
        return rec
    prev = tr.iloc[i - 1]
    from_lane = int(prev["lane"])
    if not _on_edge(float(prev["x"]), rng):
        rec.update(kind="edge_crossing", from_lane=from_lane, side=None, x_prev=float(prev["x"]))
    else:
        rec.update(
            kind="lane_change", from_lane=from_lane, side="right" if from_lane < lane else "left"
        )
    return rec


def lane_entries(
    frame: pd.DataFrame,
    lane: int,
    rng: tuple[float, float | None] | None,
    t_lo: float,
    t_hi: float,
    x_lo: float,
    x_hi: float,
    first_t: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Every entry into ``lane`` on the collision edge with ``x_lo <= x <= x_hi``, ``t_lo <= t <= t_hi``."""
    out: list[dict[str, Any]] = []
    band = frame[
        (frame["x"] >= x_lo - ENTRY_SCAN_MARGIN_M) & (frame["x"] <= x_hi + ENTRY_SCAN_MARGIN_M)
    ]
    for vid, tr in band.sort_values(["veh_id", "t"], kind="stable").groupby("veh_id", sort=True):
        ts, xs, ls = tr["t"].to_numpy(), tr["x"].to_numpy(), tr["lane"].to_numpy()
        for j in range(len(tr)):
            if not (t_lo - _EPS_T <= ts[j] <= t_hi + _EPS_T) or int(ls[j]) != lane:
                continue
            if not (x_lo <= xs[j] <= x_hi) or not _on_edge(float(xs[j]), rng):
                continue
            if j == 0:
                ft = first_t.get(str(vid))
                if ft is not None and abs(ts[j] - ft) <= _EPS_T:
                    out.append(
                        {
                            "t": float(ts[j]),
                            "veh_id": str(vid),
                            "kind": "appeared",
                            "from_lane": None,
                            "side": None,
                            "x": float(xs[j]),
                        }
                    )
                continue
            if int(ls[j - 1]) == lane and _on_edge(float(xs[j - 1]), rng):
                continue
            kind = "lane_change" if _on_edge(float(xs[j - 1]), rng) else "edge_crossing"
            fl = int(ls[j - 1])
            side = ("right" if fl < lane else "left") if kind == "lane_change" else None
            out.append(
                {
                    "t": float(ts[j]),
                    "veh_id": str(vid),
                    "kind": kind,
                    "from_lane": fl,
                    "side": side,
                    "x": float(xs[j]),
                }
            )
    return sorted(out, key=lambda r: (r["t"], r["veh_id"]))


def arrival_on_edge(
    track: pd.DataFrame, rng: tuple[float, float | None] | None
) -> dict[str, Any] | None:
    """The vehicle's first sample on the collision edge in the frame, and its lane just before."""
    if rng is None or track.empty:
        return None
    on = track[
        (track["x"] >= rng[0] - _EPS_T)
        & ((track["x"] <= rng[1] + _EPS_T) if rng[1] is not None else True)
    ]
    if on.empty:
        return None
    first = on.iloc[0]
    before = track[track["t"] < float(first["t"]) - _EPS_T]
    return {
        "t": float(first["t"]),
        "lane": int(first["lane"]),
        "x": float(first["x"]),
        "lane_before": int(before.iloc[-1]["lane"]) if not before.empty else None,
        "first_in_frame": before.empty,
    }


def braking(
    track: pd.DataFrame,
    gaps: Mapping[float, float | None],
    t_c: float,
    vt: Mapping[str, Any] | None,
    params: Params,
) -> dict[str, Any]:
    """The rear car's deceleration against ``b`` and ``emergencyDecel`` (module item 3)."""
    b = None if vt is None else _f(vt.get("decel"))
    e = None if vt is None else _f(vt.get("emergencyDecel"))
    tr = track[track["t"] <= t_c + _EPS_T]
    if tr.empty or b is None:
        return {"decel_b": b, "emergency_decel": e, "reading": "no data"}
    tol = params.decel_tol

    def cls(a: float) -> str:
        d = -a
        if d <= 0.0:
            return "not_braking"
        if abs(d - b) <= tol:
            return "at_b"
        return "beyond_b" if d > b else "below_b"

    steps: list[dict[str, Any]] = [
        {
            "t": float(r["t"]),
            "a": float(r["a"]),
            "v": float(r["v"]),
            "class": cls(float(r["a"])),
            "gap_m": gaps.get(float(r["t"])),
        }
        for _, r in tr.iterrows()
    ]
    last = steps[-params.last_steps :]
    n_at = sum(1 for s in last if s["class"] == "at_b")
    n_beyond = sum(1 for s in last if s["class"] == "beyond_b")
    braking_steps = [s for s in last if s["class"] != "not_braking"]
    run_at = 0
    for s in reversed(steps):
        if s["class"] != "at_b":
            break
        run_at += 1
    g = [s["gap_m"] for s in last if s["gap_m"] is not None]
    gap_closing = len(g) >= 2 and all(b2 < a2 for a2, b2 in pairwise(g))
    i_max = int(np.argmin([s["a"] for s in steps]))
    max_d = max(-steps[i_max]["a"], 0.0)
    reached_emergency = e is not None and max_d >= e - tol
    if n_at >= 2 and n_beyond == 0 and gap_closing:
        reading = "pinned at b while the gap closed (the signature of a command cap, §4.2)"
    elif n_beyond > 0:
        reading = "braked beyond b in the last steps (not a cap at b)"
    elif braking_steps and all(s["class"] == "below_b" for s in braking_steps):
        reading = "braked below b throughout the last steps (no cap reached: the approach)"
    elif not braking_steps:
        reading = "not braking in the last steps"
    else:
        reading = "mixed: read the steps"
    return {
        "decel_b": b,
        "emergency_decel": e,
        "command_decel_bound": None if vt is None else vt.get("command_decel_bound"),
        "max_decel_ms2": max_d,
        "t_max_decel": steps[i_max]["t"],
        "reached_emergency_decel": reached_emergency,
        "last_steps": last,
        "n_last_at_b": n_at,
        "n_last_beyond_b": n_beyond,
        "n_consecutive_at_b_to_contact": run_at,
        "gap_closing_in_last_steps": gap_closing,
        "pinned_at_b": n_at >= 2 and n_beyond == 0,
        "reading": reading,
    }


def commands_block(
    log: CommandLog, vid: str, t_c: float, step: float, params: Params
) -> dict[str, Any]:
    """The rear car's weave speed targets and commands in the window (module item 5)."""
    if log.frame is None:
        if not log.present:
            # no log recorded: the block as before the recorder existed
            return {"logged": False, "note": NOT_LOGGED_NOTE}
        return {
            "logged": False,
            "log_recorded": log.recorded is not None,
            "log_rows": None if log.recorded is None else log.recorded.get("n_rows"),
            "log_problem": log.problem,
            "note": f"{log.problem}: the inferred reading (braking and entries) stands",
        }
    cmds = log.frame
    mine = cmds[
        (cmds["veh_id"].astype(str) == vid)
        & (cmds["t"] >= t_c - params.before_s - _EPS_T)
        & (cmds["t"] <= t_c + _EPS_T)
    ]
    # stable: the log's rows are in decision order within a step
    commands = [
        {k: _py(v) for k, v in r.items()}
        for r in mine.sort_values("t", kind="stable").to_dict("records")
    ]
    rows = [r for r in commands if r["rule"] in SPEED_TARGET_RULES]
    return {
        "logged": True,
        "speed_targets": rows,
        "active_in_contact_step": any(abs(float(r["t"]) - (t_c - step)) <= _EPS_T for r in rows),
        "n_steps_with_target": len({round(float(r["t"]), 6) for r in rows}),
        "commands": commands,
    }


def _gap_series(
    rear: pd.DataFrame, front: pd.DataFrame, front_len: float | None
) -> dict[float, float | None]:
    """Bumper gap rear->front per shared step (front's x − its length − rear's x; same lane only)."""
    if front_len is None or rear.empty or front.empty:
        return {}
    m = rear.merge(front, on="t", suffixes=("_r", "_f"))
    out: dict[float, float | None] = {}
    for _, r in m.iterrows():
        same = int(r["lane_r"]) == int(r["lane_f"])
        out[float(r["t"])] = float(r["x_f"] - front_len - r["x_r"]) if same else None
    return out


def trace_event(
    run_dir: Path,
    meta: Mapping[str, Any],
    ev: Event,
    params: Params,
    vtypes: Mapping[str, Mapping[str, Any]],
    vrows: Mapping[str, Mapping[str, Any]],
    cmds: CommandLog,
    first_t: Mapping[str, float],
) -> tuple[dict[str, Any], pd.DataFrame]:
    """One event's window, braking, entries and commands; and its slice rows.

    ``first_t``: each vehicle's first corridor sample time (:func:`read_first_samples`),
    which tells a ramp vehicle's arrival on the axis from a lane change.
    """
    step, sample = _step_s(meta), _sample_s(meta)
    edge, lane = lane_parts(ev.lane)
    t_c = ev.t
    frame = read_window(
        run_dir / "trajectories.parquet",
        t_c - max(params.lookback_s, params.before_s),
        t_c + params.after_s,
    )
    rear, front = _track(frame, ev.collider), _track(frame, ev.victim)
    known = edge_range(meta, edge)
    rng: tuple[float, float | None] | None = known
    rear_c, front_c = _row_at(rear, t_c), _row_at(front, t_c)
    x0_source = "meta.json ramps (attach_x_m)" if known else None
    if rng is None and rear_c is not None and ev.pos_m is not None:
        rng = (float(rear_c["x"]) - ev.pos_m, None)
        x0_source = "rear car's x at t minus pos_m (the edge's end unknown)"
    x_contact = (
        float(rear_c["x"])
        if rear_c is not None
        else (rng[0] + ev.pos_m if rng is not None and ev.pos_m is not None else None)
    )

    # the vehicle ahead of the front car at t: nearest ahead in its lane on the collision edge
    leader_id: str | None = None
    if front_c is not None:
        at = frame[(frame["t"] - t_c).abs() <= _EPS_T]
        ahead = at[
            (at["lane"] == int(front_c["lane"]))
            & (at["x"] > float(front_c["x"]))
            & (at["veh_id"] != ev.victim)
        ]
        if known is not None:
            ahead = ahead.loc[np.array([_on_edge(float(x), known) for x in ahead["x"]], dtype=bool)]
        else:
            ahead = ahead[ahead["x"] <= float(front_c["x"]) + LEADER_SEARCH_M]
        if not ahead.empty:
            leader_id = str(ahead.sort_values("x").iloc[0]["veh_id"])
    lead = _track(frame, leader_id) if leader_id else frame.iloc[0:0]
    need_vt = [v for v in (ev.collider, ev.victim, leader_id) if v and v not in vtypes]
    if need_vt:
        vtypes = {**vtypes, **read_vtypes(run_dir, need_vt)}
    if leader_id and leader_id not in vrows:
        vrows = {**vrows, **read_vehicle_rows(run_dir, [leader_id])}

    def length(v: str | None) -> float | None:
        return None if v is None or v not in vtypes else _f(vtypes[v].get("length"))

    gaps_rear = _gap_series(rear, front, length(ev.victim))
    gaps_front = _gap_series(front, lead, length(leader_id)) if leader_id else {}

    w_lo = t_c - params.before_s - _EPS_T
    w_hi = t_c + params.after_s + _EPS_T

    def steps(
        tr: pd.DataFrame, gaps: Mapping[float, float | None], gap_name: str
    ) -> list[dict[str, Any]]:
        out = []
        for _, r in tr[(tr["t"] >= w_lo) & (tr["t"] <= w_hi)].iterrows():
            x = float(r["x"])
            out.append(
                {
                    "t": float(r["t"]),
                    "dt_to_contact_s": round(float(r["t"]) - t_c, 6),
                    "lane": int(r["lane"]),
                    "x": x,
                    "pos_on_edge_m": (x - rng[0]) if rng is not None and _on_edge(x, rng) else None,
                    "v": float(r["v"]),
                    "a": float(r["a"]),
                    gap_name: gaps.get(float(r["t"])),
                }
            )
        return out

    entry_rear = lane_entry(rear, t_c, lane, rng, first_t.get(ev.collider))
    entry_front = lane_entry(front, t_c, lane, rng, first_t.get(ev.victim))
    same_step = (
        entry_rear.get("t") is not None
        and entry_front.get("t") is not None
        and abs(float(entry_rear["t"]) - float(entry_front["t"])) <= _EPS_T
    )
    opposing = (
        same_step
        and entry_rear.get("kind") == "lane_change"
        and entry_front.get("kind") == "lane_change"
        and entry_rear.get("side") != entry_front.get("side")
    )
    if entry_rear.get("kind") in ("absent", "not_in_lane") or entry_front.get("kind") in (
        "absent",
        "not_in_lane",
    ):
        order = "not both in the lane at t"
    elif same_step:
        order = "same sampled step"
    elif entry_rear.get("t") is None and entry_front.get("t") is None:
        order = "both in the lane before the lookback"
    elif entry_rear.get("t") is None or (
        entry_front.get("t") is not None and entry_rear["t"] < entry_front["t"]
    ):
        order = "rear car first (the front car entered in front of it)"
    else:
        order = "front car first (the rear car entered behind it)"

    entries_all = (
        lane_entries(
            frame,
            lane,
            rng,
            w_lo,
            w_hi,
            x_contact - params.behind_m,
            x_contact + params.ahead_m,
            first_t,
        )
        if x_contact is not None
        else []
    )
    traj_check: dict[str, Any] = {
        "rear_at_t": rear_c is not None,
        "front_at_t": front_c is not None,
        "rear_lane_at_t": None if rear_c is None else int(rear_c["lane"]),
        "front_lane_at_t": None if front_c is None else int(front_c["lane"]),
        "rear_in_logged_lane": rear_c is not None and int(rear_c["lane"]) == lane,
        "gap_at_t_m": gaps_rear.get(t_c),
        "contact_line_m": None
        if ev.collider not in vtypes or vtypes[ev.collider].get("minGap") is None
        else 0.1 * float(vtypes[ev.collider]["minGap"]),
    }
    if known is not None and rear_c is not None and ev.pos_m is not None:
        traj_check["x_minus_logged_m"] = float(rear_c["x"]) - (known[0] + ev.pos_m)
    block: dict[str, Any] = {
        "label": ev.label,
        "collision": ev.collision,
        "hypothesis": ev.hypothesis,
        "pair": ev.pair,
        "t": t_c,
        "lane": ev.lane,
        "edge": edge,
        "lane_index": lane,
        "section": section_of(meta, edge),
        "edge_x_range": None if rng is None else list(rng),
        "edge_x_source": x0_source,
        "x_contact": x_contact,
        "step_s": step,
        "sample_s": sample,
        "step_resolved": abs(sample - step) <= _EPS_T,
        "vehicles": {
            "rear": {
                "id": ev.collider,
                "vtype": vtypes.get(ev.collider),
                "table": vrows.get(ev.collider),
            },
            "front": {
                "id": ev.victim,
                "vtype": vtypes.get(ev.victim),
                "table": vrows.get(ev.victim),
            },
            "front_leader": None
            if leader_id is None
            else {"id": leader_id, "vtype": vtypes.get(leader_id), "table": vrows.get(leader_id)},
        },
        "trajectory_check": traj_check,
        "window": {
            "rear": steps(rear, gaps_rear, "gap_to_front_m"),
            "front": steps(front, gaps_front, "gap_to_leader_m"),
            "front_leader": steps(lead, {}, "gap_m") if leader_id else [],
        },
        "braking_rear": braking(
            rear[(rear["t"] >= w_lo)], gaps_rear, t_c, vtypes.get(ev.collider), params
        ),
        "entries": {
            "rear": entry_rear,
            "front": entry_front,
            "same_step": same_step,
            "opposing_entries": opposing,
            "order": order,
            "all_into_lane": entries_all,
        },
        "arrival_on_edge": {
            "rear": arrival_on_edge(rear, rng),
            "front": arrival_on_edge(front, rng),
        },
        "weave_commands_rear": commands_block(cmds, ev.collider, t_c, step, params),
    }
    # the slice: every vehicle within the band, and the three cars throughout the window
    win = frame[(frame["t"] >= w_lo) & (frame["t"] <= w_hi)]
    keep = win["veh_id"].isin({ev.collider, ev.victim, *([leader_id] if leader_id else [])})
    if x_contact is not None:
        keep |= (win["x"] >= x_contact - params.behind_m) & (win["x"] <= x_contact + params.ahead_m)
    piece = win[keep].copy()
    piece.insert(0, "event", ev.label)
    return block, piece


def _unexpected_events(repro: Mapping[str, Any]) -> list[Event]:
    """Logged collisions no expected event claimed: traced too, labelled as such.

    One per (collider, victim) pair, its first report: the runner logs every collision
    SUMO still holds whenever a new one is detected, so a pair still in contact can be
    logged again (scripts/collision_census.py).
    """
    out: list[Event] = []
    seen: set[tuple[str, str]] = set()
    for c in repro.get("unexpected_logged") or []:
        key = (str(c["collider"]), str(c["victim"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Event(
                label=f"unexpected-{len(out) + 1}",
                t=float(c["t"]),
                collider=str(c["collider"]),
                victim=str(c["victim"]),
                lane=str(c["lane"]),
                pos_m=_f(c.get("pos_m")),
                collision=True,
                hypothesis=None,
            )
        )
    return out


def trace_run(run_dir: Path, exp: RunExpectation | None, params: Params) -> dict[str, Any]:
    """Every check of one run directory; writes its ``collision_slice.parquet``."""
    meta = json.loads((run_dir / "meta.json").read_text())
    if exp is None:
        exp = RunExpectation(
            arm="unknown", config_hash=str(meta.get("config_hash")), seed=int(meta.get("seed", -1))
        )
        repro = check_reproduction(meta, exp, params)
        repro.update(reproduced=None, problems=["no reference run for this (config hash, seed)"])
    else:
        repro = check_reproduction(meta, exp, params)
    events = list(exp.events) + _unexpected_events(repro)
    has_traj = (run_dir / "trajectories.parquet").is_file()
    ids = {v for e in events for v in (e.collider, e.victim)}
    vtypes = read_vtypes(run_dir, ids)
    vrows = read_vehicle_rows(run_dir, ids)
    cmds = read_commands(run_dir, meta)
    first_t = read_first_samples(run_dir)
    blocks: list[dict[str, Any]] = []
    pieces: list[pd.DataFrame] = []
    if has_traj:
        for ev in events:
            b, piece = trace_event(run_dir, meta, ev, params, vtypes, vrows, cmds, first_t)
            b["reproduced"] = repro["reproduced"]
            blocks.append(b)
            pieces.append(piece)
    slice_path = None
    if pieces:
        sl = pd.concat(pieces, ignore_index=True)
        slice_path = run_dir / SLICE_FILE
        with open(slice_path, "wb") as f:
            pq.write_table(pa.Table.from_pandas(sl, preserve_index=False), f)
    return {
        "run_dir": str(run_dir),
        "arm": exp.arm,
        "seed": int(meta.get("seed", exp.seed)),
        "config_hash": meta.get("config_hash"),
        "versions": meta.get("versions"),
        "reproduction": repro,
        "trajectories": has_traj,
        "slice": None if slice_path is None else str(slice_path),
        # only for a run that recorded a log (or holds its file): a run without
        # one keeps exactly its record from before the recorder existed
        **({"weave_command_log": cmds.summary()} if cmds.present else {}),
        "events": blocks,
    }


# --- summary and output --------------------------------------------------------------------


def _verdict(b: Mapping[str, Any]) -> str:
    hyp = b.get("hypothesis")
    if hyp == "command_cap":
        cmd = b["weave_commands_rear"]
        cmd_txt = (
            (
                "weave command log not readable here, inferred reading"
                if cmd.get("log_problem")
                else "weave command not logged"
            )
            if not cmd.get("logged")
            else (
                "weave target in the contact step"
                if cmd.get("active_in_contact_step")
                else "no weave target in the contact step"
            )
        )
        return f"{b['braking_rear']['reading']}; {cmd_txt}"
    if hyp == "opposing_entries":
        e = b["entries"]
        if e["opposing_entries"]:
            return "opposing entries: both changed into the lane in the same step from opposite sides (§5.2's reading)"
        kinds = "; ".join(
            f"{role} {e[role].get('kind')}"
            + (f" from the {e[role]['side']}" if e[role].get("side") else "")
            + (f" at {e[role]['t']}" if e[role].get("t") is not None else "")
            for role in ("rear", "front")
        )
        return f"not opposing entries: {e['order']} ({kinds})"
    return f"rear {b['entries']['rear'].get('kind')}, front {b['entries']['front'].get('kind')}"


def summarize(
    runs: Sequence[Mapping[str, Any]], missing: Sequence[RunExpectation]
) -> dict[str, Any]:
    events = []
    pairs: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        for b in r["events"]:
            arr = b["arrival_on_edge"]
            rec = {
                "label": b["label"],
                "arm": r["arm"],
                "seed": r["seed"],
                "reproduced": r["reproduction"]["reproduced"],
                "collision": b["collision"],
                "lane": b["lane"],
                "verdict": _verdict(b),
                "rear_max_decel_ms2": b["braking_rear"].get("max_decel_ms2"),
                "rear_decel_b": b["braking_rear"].get("decel_b"),
                "rear_pinned_at_b": b["braking_rear"].get("pinned_at_b"),
                "opposing_entries": b["entries"]["opposing_entries"],
                "entry_order": b["entries"]["order"],
                "rear_entry": b["entries"]["rear"].get("kind"),
                "front_entry": b["entries"]["front"].get("kind"),
                "rear_lane_on_arrival": None if arr["rear"] is None else arr["rear"]["lane"],
                "front_lane_on_arrival": None if arr["front"] is None else arr["front"]["lane"],
            }
            events.append(rec)
            if b.get("pair"):
                pairs.setdefault(b["pair"], []).append(rec)
    paired = []
    for key, recs in sorted(pairs.items()):
        lanes = {r["rear_lane_on_arrival"] for r in recs}
        paired.append(
            {
                "pair": key,
                "runs": recs,
                "rear_same_arrival_lane": len(lanes) == 1 if len(recs) > 1 else None,
            }
        )
    not_repro = []
    for r in runs:
        status = r["reproduction"]["reproduced"]
        if status is False:
            not_repro.append(f"{r['arm']} seed {r['seed']}")
        elif status is None:
            not_repro.append(f"{r['run_dir']} (no reference run to compare with)")
    not_repro += [f"{m.arm} seed {m.seed} (no run directory)" for m in missing]
    if not runs and not missing:
        not_repro.append("no run directory found")
    reproduced = not not_repro
    untraced = [f"{r['arm']} seed {r['seed']}" for r in runs if not r["trajectories"]]
    if reproduced:
        note = (
            "Every run reproduces its reference run's logged events (collider, victim, lane, "
            "position to the tolerance, time within one step) and logs no other collision; the "
            "windows are the reference runs' events."
        )
    else:
        note = (
            "NOT REPRODUCED: " + "; ".join(not_repro) + ". These runs are not shown to be the "
            "reference runs (the collision log comparison in each run's 'reproduction' says how "
            "they differ); their windows are a different realisation and must not be read as the "
            f"reference events. Stage p8 probably ran at commit {P8_REFERENCE_COMMIT} "
            "(docs/I94_CAL_COLLISIONS.md §10): relaunch from that commit before reading them."
        )
    if untraced:
        note += (
            " NOT TRACED (no trajectories.parquet): " + "; ".join(untraced) + "; their windows "
            "are missing."
        )
    return {
        "reproduced": reproduced,
        "reproduction_note": note,
        "runs_without_trajectories": untraced,
        "events": events,
        "pairs": paired,
    }


def _fmt(v: Any, nd: int = 2) -> str:
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def print_run(r: Mapping[str, Any]) -> None:
    rep = r["reproduction"]
    print(f"\n=== {r['arm']} seed {r['seed']} ({r['config_hash']}) {r['run_dir']}")
    status = {True: "REPRODUCED", False: "NOT REPRODUCED", None: "no reference"}[rep["reproduced"]]
    print(
        f"reproduction: {status}; n_collisions {rep['n_collisions']} (expected {rep['n_collisions_expected']})"
    )
    for p in rep["problems"]:
        print(f"  ! {p}")
    for e in rep["events"]:
        if e["logged"] is not None:
            print(
                f"  {e['label']}: logged t {e['logged']['t']} lane {e['logged']['lane']} pos {e['logged']['pos_m']} (dt {e['dt_s']:+.3f} s, dpos {e['dpos_m']})"
            )
    if not r["trajectories"]:
        print("  no trajectories.parquet: nothing traced")
    for b in r["events"]:
        print(
            f"\n--- {b['label']} t {b['t']} {b['lane']} section {b['section']} x0 {b['edge_x_range']} ({b['edge_x_source']})"
        )
        for role in ("rear", "front", "front_leader"):
            v = b["vehicles"][role]
            if v is None:
                continue
            vt = v["vtype"] or {}
            print(
                f"  {role} {v['id']}: {vt.get('carFollowModel')} decel {vt.get('decel')} emergencyDecel "
                f"{vt.get('emergencyDecel')} minGap {vt.get('minGap')} tau {vt.get('tau')} accel {vt.get('accel')} "
                f"length {vt.get('length')}; origin {(v['table'] or {}).get('origin')} -> {(v['table'] or {}).get('destination')}"
            )
        print(f"  trajectory check: {b['trajectory_check']}")
        for role, gname in (
            ("rear", "gap_to_front_m"),
            ("front", "gap_to_leader_m"),
            ("front_leader", "gap_m"),
        ):
            rows = b["window"][role]
            if not rows:
                continue
            print(f"  {role}: t | dt | lane | x | pos | v | a | gap")
            for s in rows:
                print(
                    f"    {s['t']:.1f} | {s['dt_to_contact_s']:+.1f} | {s['lane']} | {s['x']:.2f} | "
                    f"{_fmt(s['pos_on_edge_m'])} | {s['v']:.3f} | {s['a']:.4f} | {_fmt(s[gname])}"
                )
        br = b["braking_rear"]
        print(
            f"  braking (rear): max {_fmt(br.get('max_decel_ms2'), 4)} m/s2 at {br.get('t_max_decel')}, b "
            f"{br.get('decel_b')}, emergency {br.get('emergency_decel')}, command bound "
            f"{br.get('command_decel_bound')}; last steps at b {br.get('n_last_at_b')}, beyond "
            f"{br.get('n_last_beyond_b')}; {br.get('reading')}"
        )
        en = b["entries"]
        print(
            f"  entries: rear {en['rear']}; front {en['front']}; {en['order']}; opposing {en['opposing_entries']}"
        )
        for x in en["all_into_lane"]:
            print(f"    into lane {b['lane_index']}: {x}")
        print(f"  arrival on the edge: {b['arrival_on_edge']}")
        print(f"  weave commands (rear): {b['weave_commands_rear']}")


def _commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_dirs", nargs="*", type=Path, help="run directories (or use --root)")
    ap.add_argument("--root", type=Path, help="find every run directory under this root")
    ap.add_argument("--expect", default="p8c", help="'p8c' (built in) or a JSON file")
    ap.add_argument("--out", type=Path, required=True, help="the artifact (JSON)")
    ap.add_argument("--before-s", type=float, default=Params.before_s)
    ap.add_argument("--after-s", type=float, default=Params.after_s)
    ap.add_argument("--lookback-s", type=float, default=Params.lookback_s)
    ap.add_argument("--behind-m", type=float, default=Params.behind_m)
    ap.add_argument("--ahead-m", type=float, default=Params.ahead_m)
    ap.add_argument("--decel-tol", type=float, default=Params.decel_tol)
    ap.add_argument("--pos-tol", type=float, default=Params.pos_tol)
    ap.add_argument("--last-steps", type=int, default=Params.last_steps)
    args = ap.parse_args(argv)
    if not args.run_dirs and args.root is None:
        print("i94_collision_trace: give run directories or --root", file=sys.stderr)
        return 2
    params = Params(
        before_s=args.before_s,
        after_s=args.after_s,
        lookback_s=args.lookback_s,
        behind_m=args.behind_m,
        ahead_m=args.ahead_m,
        decel_tol=args.decel_tol,
        pos_tol=args.pos_tol,
        last_steps=args.last_steps,
    )
    try:
        expected = load_expectations(args.expect)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"i94_collision_trace: --expect {args.expect}: {exc}", file=sys.stderr)
        return 2
    dirs = list(args.run_dirs) + (
        find_runs(args.root) if args.root is not None and args.root.is_dir() else []
    )
    by_key = {(e.config_hash, e.seed): e for e in expected}
    runs: list[dict[str, Any]] = []
    found: set[tuple[str, int]] = set()
    for d in dirs:
        meta = json.loads((d / "meta.json").read_text())
        key = (str(meta.get("config_hash")), int(meta.get("seed", -1)))
        found.add(key)
        r = trace_run(d, by_key.get(key), params)
        runs.append(r)
        print_run(r)
    missing = [e for e in expected if (e.config_hash, e.seed) not in found]
    summary = summarize(runs, missing)
    artifact = {
        "schema": SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit": _commit(),
        "reference_commit": P8_REFERENCE_COMMIT,
        "source": "docs/I94_CAL_COLLISIONS.md §10 (stage p8c_i94_cal_collisions)",
        "params": asdict(params),
        "expect": args.expect,
        **summary,
        "missing_runs": [asdict(m) for m in missing],
        "runs": runs,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(json_safe(artifact), indent=1, default=_py))
    print(f"\n{summary['reproduction_note']}")
    for e in summary["events"]:
        print(f"  {e['label']} ({e['arm']} seed {e['seed']}): {e['verdict']}")
    print(f"artifact {args.out}")
    return 0 if summary["reproduced"] and not summary["runs_without_trajectories"] else 3


if __name__ == "__main__":
    sys.exit(main())
