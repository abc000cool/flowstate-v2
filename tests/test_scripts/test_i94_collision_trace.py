"""scripts/i94_collision_trace.py on synthetic runs (docs/I94_CAL_COLLISIONS.md §10).

No simulation and no real run is read: every run directory here is built from planted
trajectories, with the files the runner writes (``meta.json`` with its collision log
and ``ramps``, ``vehicles.parquet``, ``trajectories.parquet`` in several row groups,
``net/demand.rou.xml``).

* **Rear-end at comfortable deceleration** (§4.2's signature): the rear car brakes at
  exactly its vType's ``decel`` for the four steps to contact while its gap to a
  standing front car closes; the reader finds it pinned at ``b``, below its
  ``emergencyDecel`` (the passenger default, 9), with the vehicle ahead of the front
  car, and both cars arriving on the section (``appeared``).
* **Opposing entries** (§5.2's reading): an entrant changes 0 -> 1 and an exiter
  2 -> 1 in the same step, the exiter having crossed onto the section from upstream
  (a lane renumbering, not a lane change); the reader calls it opposing entries and
  lists every entry into the lane. Shifted one step earlier, the exiter is in the lane
  first and it is not.
* **Reproduction**: a matching log reproduces; a shifted position, a wrong hash, an
  extra collision in a control, or a missing run directory does not, the artifact
  says NOT REPRODUCED with the reference commit, and the exit status is 3.
* **Commands** (the runner's ``weave_commands.parquet``, ``WeaveSpec.record_commands``):
  a logged weave speed target (``cooperate`` / ``ease``) issued one step before contact
  is found, with the rear car's other commands; a meta-only archive (the log recorded
  in ``meta.json["weave_command_log"]``, its file absent) and an unreadable file say so
  and keep the inferred reading; a run with no log reads exactly as before.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tr = _load("i94_collision_trace")

STEP = 0.5
B = 1.7  # the rear car's comfortable deceleration (vType decel)
MIN_GAP = 2.5
LENGTH = 5.0


def _grid(t0: float, t1: float) -> list[float]:
    n = round((t1 - t0) / STEP)
    return [t0 + STEP * k for k in range(n + 1)]


def _write_run(
    run_dir: Path,
    *,
    config_hash: str,
    seed: int,
    rows: list[dict[str, Any]],
    collisions: list[dict[str, Any]],
    ramps: list[dict[str, Any]],
    vtypes: dict[str, dict[str, str]],
    first_samples: dict[str, tuple[float, float, int]],
    commands: list[dict[str, Any]] | None = None,
    command_log: dict[str, Any] | None = None,
) -> Path:
    """A run directory with the files ``run_micro`` writes (only what the reader reads).

    ``commands``: rows of ``weave_commands.parquet`` in the runner's schema (columns not
    given take the runner's "none"); ``command_log``: ``meta.json["weave_command_log"]``.
    """
    run_dir.mkdir(parents=True)
    meta = {
        "config": {"sim": {"step_length_s": STEP, "output_hz": 2.0}},
        "config_hash": config_hash,
        "seed": seed,
        "output_hz_realized": 2.0,
        "versions": {"eclipse-sumo": "1.27.1"},
        "n_collisions": len(collisions),
        "collisions": collisions,
        "ramps": ramps,
        "weave_sections": [
            {"ramp": r["name"], "exit": "off", "edges": [r["attach_edge"]], "exit_edge": "X"}
            for r in ramps
        ],
    }
    if command_log is not None:
        meta["weave_command_log"] = command_log
    (run_dir / "meta.json").write_text(json.dumps(meta))
    df = pd.DataFrame(rows).sort_values(["t", "veh_id"], kind="stable")
    table = pa.Table.from_pandas(df[["t", "veh_id", "x", "lane", "v", "a"]], preserve_index=False)
    table = table.cast(
        pa.schema(
            [
                ("t", pa.float64()),
                ("veh_id", pa.string()),
                ("x", pa.float64()),
                ("lane", pa.int32()),
                ("v", pa.float64()),
                ("a", pa.float64()),
            ]
        )
    )
    with open(run_dir / "trajectories.parquet", "wb") as f:
        pq.write_table(table, f, row_group_size=40)  # several row groups: the reader prunes by t
    vehicles = pd.DataFrame(
        [
            {
                "veh_id": vid,
                "origin": "on",
                "destination": "end",
                "entry_t_s": t,
                "entry_x_m": x,
                "entry_lane": lane,
            }
            for vid, (t, x, lane) in sorted(first_samples.items())
        ]
    )
    with open(run_dir / "vehicles.parquet", "wb") as f:
        pq.write_table(pa.Table.from_pandas(vehicles, preserve_index=False), f)
    (run_dir / "net").mkdir()
    lines = ["<routes>"]
    for vid, attrs in sorted(vtypes.items()):
        tid = "t" + vid[1:]
        extra = "".join(f' {k}="{v}"' for k, v in attrs.items())
        lines.append(
            f'  <vType id="{tid}" carFollowModel="EIDM" accel="1.5" decel="{B:.6f}" tau="1.3" '
            f'minGap="{MIN_GAP:.6f}" maxSpeed="30" length="{LENGTH}" speedFactor="1.0"{extra}/>'
        )
    for vid in sorted(vtypes):
        lines.append(f'  <vehicle id="{vid}" type="t{vid[1:]}" depart="0.00" route="r"/>')
    lines.append("</routes>")
    (run_dir / "net" / "demand.rou.xml").write_text("\n".join(lines))
    if commands is not None:
        with open(run_dir / tr.COMMANDS_FILE, "wb") as f:
            pq.write_table(_command_table(commands), f)
    return run_dir


def _command_table(rows: list[dict[str, Any]]) -> pa.Table:
    """Rows in the runner's ``weave_commands.parquet`` schema (``microsim.runner``)."""
    from microsim.runner import _WEAVE_COMMANDS_SCHEMA

    none = {
        "section": "ruth",
        "lane_from": 0,
        "lane_to": -1,
        "v_cmd_ms": math.nan,
        "lc_mode_set": -1,
        "x_m": math.nan,
    }
    return pa.Table.from_pylist(
        [{**none, **r} for r in rows], schema=pa.schema(_WEAVE_COMMANDS_SCHEMA)
    )


# --- the planted rear-end: Ruth St's auxiliary lane (R1-R3's setting) ---------------------

E1_X0, E1_X1 = 1000.0, 1135.77
T_REAR = 110.0


def _rear_end_rows() -> tuple[list[dict[str, Any]], float]:
    """Rear car v00001 arrives on E1 lane 0 at 106 s, cruises at 4 m/s, then brakes at exactly
    b for the four steps to contact with v00002, which arrived at 90 s and stands at 1,020 m
    (its leader v00003 stands at 1,030 m). Euler as SUMO: v' = v + a dt, x' = x + v' dt.
    Returns the rows and the rear car's x at contact."""
    rows: list[dict[str, Any]] = []
    for t in _grid(40.0, 111.0):
        if t >= 90.0:
            rows.append({"t": t, "veh_id": "v00002", "x": 1020.0, "lane": 0, "v": 0.0, "a": 0.0})
        rows.append({"t": t, "veh_id": "v00003", "x": 1030.0, "lane": 0, "v": 0.0, "a": 0.0})
        if t >= 108.0:  # another ramp vehicle arriving behind, standing
            rows.append({"t": t, "veh_id": "v00004", "x": 1001.0, "lane": 0, "v": 0.0, "a": 0.0})
    x, v = 1003.15, 4.0
    for t in _grid(106.0, 111.0):
        if t > 106.0:
            a = -B if t > 108.0 + 1e-9 and v > 0.0 else 0.0
            v = max(v + a * STEP, 0.0)
            x += v * STEP
        else:
            a = 0.0
        rows.append({"t": t, "veh_id": "v00001", "x": x, "lane": 0, "v": v, "a": a})
    x_contact = next(r["x"] for r in rows if r["veh_id"] == "v00001" and r["t"] == T_REAR)
    return rows, x_contact


def _rear_end_run(
    tmp: Path,
    *,
    pos_shift: float = 0.0,
    commands: list[dict[str, Any]] | None = None,
    command_log: dict[str, Any] | None = None,
) -> tuple[Path, Any]:
    rows, x_c = _rear_end_rows()
    pos = x_c - E1_X0
    run = _write_run(
        tmp / "arm" / "hashR" / "1",
        config_hash="hashR",
        seed=1,
        rows=rows,
        collisions=[
            {
                "t": T_REAR,
                "collider": "v00001",
                "victim": "v00002",
                "type": "collision",
                "lane": "E1_0",
                "pos_m": pos + pos_shift,
            }
        ],
        ramps=[
            {
                "index": 0,
                "name": "ruth",
                "kind": "on",
                "attach_edge": "E1",
                "attach_x_m": E1_X0,
                "attach_end_x_m": E1_X1,
            }
        ],
        vtypes={"v00001": {}, "v00002": {}, "v00003": {}},
        first_samples={
            "v00001": (106.0, 1003.15, 0),
            "v00002": (90.0, 1020.0, 0),
            "v00003": (40.0, 1030.0, 0),
            "v00004": (108.0, 1001.0, 0),
        },
        commands=commands,
        command_log=command_log,
    )
    exp = tr.RunExpectation(
        "arm",
        "hashR",
        1,
        (tr.Event("R", T_REAR, "v00001", "v00002", "E1_0", pos, True, "command_cap"),),
    )
    return run, exp


def test_a_rear_end_at_comfortable_deceleration_is_pinned_at_b(tmp_path: Path) -> None:
    run, exp = _rear_end_run(tmp_path)
    r = tr.trace_run(run, exp, tr.Params())
    rep = r["reproduction"]
    assert rep["reproduced"] is True and rep["problems"] == []
    assert rep["events"][0]["matched"] and rep["events"][0]["dt_s"] == 0.0
    (b,) = r["events"]
    assert b["section"] == {"ramp": "ruth", "exit": "off", "exit_edge": "X"}
    assert b["edge_x_range"] == [E1_X0, E1_X1] and b["step_resolved"] is True
    rear_vt = b["vehicles"]["rear"]["vtype"]
    assert rear_vt["decel"] == pytest.approx(B) and rear_vt["minGap"] == pytest.approx(MIN_GAP)
    assert (
        rear_vt["emergencyDecel"] == 9.0 and "passenger default" in rear_vt["emergencyDecel_source"]
    )
    assert rear_vt["command_decel_bound"] == pytest.approx(B)  # EIDM: a held command brakes at b
    assert b["vehicles"]["front_leader"]["id"] == "v00003"
    chk = b["trajectory_check"]
    assert chk["rear_in_logged_lane"] and abs(chk["x_minus_logged_m"]) < 1e-9
    assert chk["gap_at_t_m"] < chk["contact_line_m"] == pytest.approx(0.1 * MIN_GAP)
    # the window: 15 s before contact to 1 s after; the rear car only since it arrived
    rear_rows = b["window"]["rear"]
    assert [s["t"] for s in rear_rows] == _grid(106.0, 111.0)
    assert b["window"]["front"][0]["t"] == T_REAR - 15.0
    assert b["window"]["front"][0]["gap_to_leader_m"] == pytest.approx(5.0)
    assert rear_rows[-3]["pos_on_edge_m"] == pytest.approx(rep["events"][0]["logged"]["pos_m"])
    br = b["braking_rear"]
    assert br["pinned_at_b"] and br["n_consecutive_at_b_to_contact"] == 4
    assert br["n_last_at_b"] == 4 and br["n_last_beyond_b"] == 0 and br["gap_closing_in_last_steps"]
    assert br["max_decel_ms2"] == pytest.approx(B) and not br["reached_emergency_decel"]
    assert br["reading"].startswith("pinned at b while the gap closed")
    # no lane change: both cars arrived in lane 0 (the ramp's lane), the front one first
    en = b["entries"]
    assert en["rear"]["kind"] == "appeared" and en["front"]["kind"] == "appeared"
    assert not en["opposing_entries"] and en["order"].startswith("front car first")
    # arrivals in the window on the lane, any vehicle (the front car arrived before it)
    assert [(e["t"], e["veh_id"], e["kind"]) for e in en["all_into_lane"]] == [
        (106.0, "v00001", "appeared"),
        (108.0, "v00004", "appeared"),
    ]
    assert b["weave_commands_rear"]["logged"] is False
    sl = pd.read_parquet(run / tr.SLICE_FILE)
    assert (
        set(sl["event"]) == {"R"}
        and sl["t"].min() >= T_REAR - 15.0
        and sl["t"].max() <= T_REAR + 1.0
    )
    assert {"v00001", "v00002", "v00003"} <= set(sl["veh_id"])


def test_braking_beyond_b_is_not_read_as_the_cap(tmp_path: Path) -> None:
    rows, x_c = _rear_end_rows()
    for row in rows:  # the same approach, the last step at 2.5 b
        if row["veh_id"] == "v00001" and row["t"] == T_REAR:
            row["a"] = -2.5 * B
    run = _write_run(
        tmp_path / "r",
        config_hash="h",
        seed=1,
        rows=rows,
        collisions=[],
        ramps=[
            {
                "index": 0,
                "name": "ruth",
                "kind": "on",
                "attach_edge": "E1",
                "attach_x_m": E1_X0,
                "attach_end_x_m": E1_X1,
            }
        ],
        vtypes={"v00001": {"emergencyDecel": "7.5"}, "v00002": {}},
        first_samples={"v00001": (106.0, 1003.15, 0), "v00002": (90.0, 1020.0, 0)},
    )
    ev = tr.Event("R", T_REAR, "v00001", "v00002", "E1_0", x_c - E1_X0, False, "command_cap")
    r = tr.trace_run(run, tr.RunExpectation("a", "h", 1, (ev,)), tr.Params())
    br = r["events"][0]["braking_rear"]
    assert br["emergency_decel"] == 7.5  # read from the vType when it sets one
    assert br["n_last_beyond_b"] == 1 and not br["pinned_at_b"]
    assert br["reading"].startswith("braked beyond b")


#: The rear-end's planted weave command log (the runner's rules): the rear car eased at
#: the two steps before contact and asked into lane 1 at the last; the front car given a
#: cooperation target; one ease of the rear car 20 s before contact, outside the window.
REAR_END_COMMANDS: list[dict[str, Any]] = [
    {"t": T_REAR - 20.0, "veh_id": "v00001", "rule": "ease", "v_cmd_ms": 4.0},
    {"t": T_REAR - 2 * STEP, "veh_id": "v00001", "rule": "ease", "v_cmd_ms": 1.0},
    {"t": T_REAR - STEP, "veh_id": "v00001", "rule": "ease", "v_cmd_ms": 0.5},
    {
        "t": T_REAR - STEP,
        "veh_id": "v00001",
        "rule": "change_accept",
        "lane_to": 1,
        "lc_mode_set": 256,
    },
    {"t": T_REAR - STEP, "veh_id": "v00002", "rule": "cooperate", "v_cmd_ms": 0.0},
]
REAR_END_LOG: dict[str, Any] = {
    "file": "weave_commands.parquet",
    "n_rows": 5,
    "sections": {"ruth": {"change_accept": 1, "cooperate": 1, "ease": 3}},
}


def test_a_logged_weave_target_in_the_contact_step_is_reported(tmp_path: Path) -> None:
    """The runner's log beside meta.json: the rear car's speed targets in the window are
    its ``ease`` / ``cooperate`` rows (the lane request is listed among its commands, not
    as a target), the one at ``t − step`` is active in the contact step; the run record
    says the log was read."""
    run, exp = _rear_end_run(tmp_path, commands=REAR_END_COMMANDS, command_log=REAR_END_LOG)
    r = tr.trace_run(run, exp, tr.Params())
    cmd = r["events"][0]["weave_commands_rear"]
    assert cmd["logged"] and cmd["active_in_contact_step"] and cmd["n_steps_with_target"] == 2
    assert [(c["rule"], c["v_cmd_ms"]) for c in cmd["speed_targets"]] == [
        ("ease", 1.0),
        ("ease", 0.5),
    ]
    assert [c["rule"] for c in cmd["commands"]] == ["ease", "ease", "change_accept"]
    accept = cmd["commands"][-1]
    assert (accept["lane_to"], accept["lc_mode_set"], accept["v_cmd_ms"]) == (1, 256, None)
    assert r["weave_command_log"] == {
        "file": tr.COMMANDS_FILE,
        "read": True,
        "n_rows": 5,
        "recorded": REAR_END_LOG,
        "problem": None,
    }
    summary = tr.summarize([r], [])
    assert summary["events"][0]["verdict"].endswith("weave target in the contact step")
    # the same log without a target in the contact step
    late = [c for c in REAR_END_COMMANDS if not (c["t"] == T_REAR - STEP and c["rule"] == "ease")]
    run2, exp2 = _rear_end_run(tmp_path / "late", commands=late, command_log=REAR_END_LOG)
    r2 = tr.trace_run(run2, exp2, tr.Params())
    cmd2 = r2["events"][0]["weave_commands_rear"]
    assert cmd2["logged"] and not cmd2["active_in_contact_step"]
    assert tr.summarize([r2], [])["events"][0]["verdict"].endswith(
        "no weave target in the contact step"
    )


def test_a_run_without_a_log_reads_exactly_as_before(tmp_path: Path) -> None:
    """No file and no ``weave_command_log``: the block, the run record and the verdict
    the reader wrote before the recorder existed (byte for byte in the artifact)."""
    run, exp = _rear_end_run(tmp_path)
    r = tr.trace_run(run, exp, tr.Params())
    assert r["events"][0]["weave_commands_rear"] == {
        "logged": False,
        "note": "the runner does not log weave commands (docs/I94_CAL_COLLISIONS.md §2, §10)",
    }
    assert "weave_command_log" not in r
    assert list(r) == [
        "run_dir",
        "arm",
        "seed",
        "config_hash",
        "versions",
        "reproduction",
        "trajectories",
        "slice",
        "events",
    ]
    verdict = tr.summarize([r], [])["events"][0]["verdict"]
    assert verdict.endswith("; weave command not logged")


def test_a_meta_only_archive_says_so_and_keeps_the_inferred_reading(tmp_path: Path) -> None:
    """p8c-style archive: meta.json records the log, the parquet file is not shipped. The
    block says so (rows recorded, the problem) and reads ``logged: false``; braking and
    entries are the reading without a log, and nothing raises."""
    run, exp = _rear_end_run(tmp_path, command_log=REAR_END_LOG)
    assert not (run / tr.COMMANDS_FILE).exists()
    r = tr.trace_run(run, exp, tr.Params())
    (b,) = r["events"]
    cmd = b["weave_commands_rear"]
    assert cmd["logged"] is False and cmd["log_recorded"] is True and cmd["log_rows"] == 5
    assert "meta-only archive" in cmd["log_problem"] and "weave_command_log" in cmd["log_problem"]
    assert cmd["note"].endswith("the inferred reading (braking and entries) stands")
    assert r["weave_command_log"]["read"] is False and r["weave_command_log"]["n_rows"] is None
    assert r["weave_command_log"]["recorded"] == REAR_END_LOG
    plain, plain_exp = _rear_end_run(tmp_path / "plain")
    p = tr.trace_run(plain, plain_exp, tr.Params())["events"][0]
    assert b["braking_rear"] == p["braking_rear"] and b["entries"] == p["entries"]
    verdict = tr.summarize([r], [])["events"][0]["verdict"]
    assert verdict.startswith(p["braking_rear"]["reading"])
    assert verdict.endswith("weave command log not readable here, inferred reading")


def test_an_unreadable_log_says_so_and_never_raises(tmp_path: Path) -> None:
    """A file that is not Parquet, and one without the runner's ``rule`` column (the
    speculative ``call`` form the reader once accepted): each reads ``logged: false``
    with its problem, whether or not meta.json records a log."""
    run, exp = _rear_end_run(tmp_path / "garbled")
    (run / tr.COMMANDS_FILE).write_bytes(b"not a parquet file")
    cmd = tr.trace_run(run, exp, tr.Params())["events"][0]["weave_commands_rear"]
    assert cmd["logged"] is False and cmd["log_recorded"] is False
    assert cmd["log_problem"].startswith(f"{tr.COMMANDS_FILE} cannot be read")
    run, exp = _rear_end_run(tmp_path / "old_form", command_log=REAR_END_LOG)
    old = pd.DataFrame([{"t": T_REAR - STEP, "veh_id": "v00001", "call": "slowDown", "value": 0.5}])
    with open(run / tr.COMMANDS_FILE, "wb") as f:
        pq.write_table(pa.Table.from_pandas(old, preserve_index=False), f)
    r = tr.trace_run(run, exp, tr.Params())
    cmd = r["events"][0]["weave_commands_rear"]
    assert cmd["logged"] is False and cmd["log_recorded"] is True
    assert "lacks the column(s) ['rule']" in cmd["log_problem"]
    assert r["weave_command_log"]["problem"] == cmd["log_problem"]


# --- the planted opposing entry: T.H.52 (T1-T3's setting) ---------------------------------

E2_X0, E2_X1 = 2000.0, 2305.02


def _opposing_rows(rear_change_t: float) -> list[dict[str, Any]]:
    """Entrant v00011 appears on E2 lane 0 at 200 s (11 m/s) and changes 0 -> 1 at 204.0;
    exiter v00012 (22 m/s) crosses onto E2 from upstream lane 1, renumbered lane 2, and
    changes 2 -> 1 at ``rear_change_t``; contact at 204.5. v00014 changes 2 -> 1 at 203.0
    ahead of them (the front car's leader at contact); v00015 crosses from upstream lane 0
    into E2 lane 1; v00013, far ahead in lane 1, crossed onto E2 before the window."""
    rows: list[dict[str, Any]] = []
    for t in _grid(150.0, 206.0):
        if t >= 200.0:
            rows.append(
                {
                    "t": t,
                    "veh_id": "v00011",
                    "x": 2006.2 + 11.0 * (t - 200.0),
                    "lane": 0 if t < 204.0 else 1,
                    "v": 11.0,
                    "a": 0.0,
                }
            )
        x12 = 2040.0 + 22.0 * (t - 204.0)
        lane12 = (1 if x12 < E2_X0 else 2) if t < rear_change_t else 1
        rows.append({"t": t, "veh_id": "v00012", "x": x12, "lane": lane12, "v": 22.0, "a": 0.0})
        rows.append(
            {
                "t": t,
                "veh_id": "v00013",
                "x": 2230.0 + 5.0 * (t - 204.0),
                "lane": 1,
                "v": 5.0,
                "a": 0.0,
            }
        )
        rows.append(
            {
                "t": t,
                "veh_id": "v00014",
                "x": 2094.0 + 12.0 * (t - 203.0),
                "lane": 2 if t < 203.0 else 1,
                "v": 12.0,
                "a": 0.0,
            }
        )
        x15 = 2003.0 + 13.0 * (t - 203.5)
        rows.append(
            {
                "t": t,
                "veh_id": "v00015",
                "x": x15,
                "lane": 0 if x15 < E2_X0 else 1,
                "v": 13.0,
                "a": 0.0,
            }
        )
    return rows


def _opposing_run(tmp: Path, rear_change_t: float) -> tuple[Path, Any]:
    pos = 2040.0 + 22.0 * 0.5 - E2_X0
    run = _write_run(
        tmp / "nf" / "hashT" / "7",
        config_hash="hashT",
        seed=7,
        rows=_opposing_rows(rear_change_t),
        collisions=[
            {
                "t": 204.5,
                "collider": "v00012",
                "victim": "v00011",
                "type": "collision",
                "lane": "E2_1",
                "pos_m": pos,
            }
        ],
        ramps=[
            {
                "index": 0,
                "name": "th52",
                "kind": "on",
                "attach_edge": "E2",
                "attach_x_m": E2_X0,
                "attach_end_x_m": E2_X1,
            }
        ],
        vtypes={v: {} for v in ("v00011", "v00012", "v00013")},
        first_samples={
            "v00011": (200.0, 2006.2, 0),
            "v00012": (10.0, 0.0, 1),
            "v00013": (10.0, 0.0, 1),
            "v00014": (10.0, 0.0, 2),
            "v00015": (10.0, 0.0, 0),
        },
    )
    exp = tr.RunExpectation(
        "nf",
        "hashT",
        7,
        (tr.Event("T", 204.5, "v00012", "v00011", "E2_1", pos, True, "opposing_entries", "T"),),
    )
    return run, exp


def test_opposing_entries_into_one_lane_in_one_step(tmp_path: Path) -> None:
    run, exp = _opposing_run(tmp_path, rear_change_t=204.0)
    r = tr.trace_run(run, exp, tr.Params())
    assert r["reproduction"]["reproduced"] is True
    (b,) = r["events"]
    en = b["entries"]
    assert en["rear"]["kind"] == "lane_change" and en["front"]["kind"] == "lane_change"
    assert (en["rear"]["t"], en["rear"]["from_lane"], en["rear"]["side"]) == (204.0, 2, "left")
    assert (en["front"]["t"], en["front"]["from_lane"], en["front"]["side"]) == (204.0, 0, "right")
    assert en["same_step"] and en["opposing_entries"] and en["order"] == "same sampled step"
    into = [(e["t"], e["veh_id"], e["kind"], e["side"]) for e in en["all_into_lane"]]
    assert (203.0, "v00014", "lane_change", "left") in into
    assert (203.5, "v00015", "edge_crossing", None) in into  # a renumbering at the edge start
    assert (204.0, "v00011", "lane_change", "right") in into and (
        204.0,
        "v00012",
        "lane_change",
        "left",
    ) in into
    assert all(e["veh_id"] != "v00013" for e in en["all_into_lane"])
    # the exiter's arrival on the section: renumbered from upstream lane 1 into lane 2
    arr = b["arrival_on_edge"]["rear"]
    assert (arr["lane"], arr["lane_before"]) == (2, 1)
    assert (
        b["arrival_on_edge"]["front"]["lane"] == 0
        and b["arrival_on_edge"]["front"]["first_in_frame"]
    )
    assert b["vehicles"]["front_leader"]["id"] == "v00014"
    assert not b["braking_rear"]["pinned_at_b"]  # the exiter never braked
    s = tr.summarize([r], [])
    assert s["reproduced"] and s["events"][0]["verdict"].startswith("opposing entries")


def test_an_exiter_already_in_the_lane_is_not_an_opposing_entry(tmp_path: Path) -> None:
    run, exp = _opposing_run(tmp_path, rear_change_t=203.0)
    r = tr.trace_run(run, exp, tr.Params())
    en = r["events"][0]["entries"]
    assert en["rear"]["t"] == 203.0 and en["front"]["t"] == 204.0
    assert not en["same_step"] and not en["opposing_entries"]
    assert en["order"].startswith("rear car first")
    assert tr.summarize([r], [])["events"][0]["verdict"].startswith("not opposing entries")


# --- reproduction, controls and the command line ------------------------------------------


def test_a_shifted_position_is_not_reproduced_and_is_still_traced(tmp_path: Path) -> None:
    run, exp = _rear_end_run(tmp_path, pos_shift=0.01)
    r = tr.trace_run(run, exp, tr.Params())
    rep = r["reproduction"]
    assert rep["reproduced"] is False
    assert rep["events"][0]["checks"]["pos_within_tol"] is False
    assert "pos_within_tol" in rep["problems"][0]
    assert r["events"] and r["events"][0]["reproduced"] is False
    s = tr.summarize([r], [])
    assert not s["reproduced"] and s["reproduction_note"].startswith("NOT REPRODUCED: arm seed 1")
    assert tr.P8_REFERENCE_COMMIT in s["reproduction_note"]


def test_another_config_hash_is_not_reproduced(tmp_path: Path) -> None:
    run, exp = _rear_end_run(tmp_path)
    other = tr.RunExpectation(exp.arm, "hashOther", exp.seed, exp.events)
    rep = tr.trace_run(run, other, tr.Params())["reproduction"]
    assert rep["reproduced"] is False and rep["problems"] == [
        "config hash hashR, expected hashOther"
    ]


def test_a_control_reproduces_only_without_collisions(tmp_path: Path) -> None:
    rows = _opposing_rows(204.0)
    common = {
        "rows": rows,
        "ramps": [
            {
                "index": 0,
                "name": "th52",
                "kind": "on",
                "attach_edge": "E2",
                "attach_x_m": E2_X0,
                "attach_end_x_m": E2_X1,
            }
        ],
        "vtypes": {"v00011": {}, "v00012": {}},
        "first_samples": {"v00011": (200.0, 2006.2, 0)},
    }
    probe = tr.Event("T-control", 204.5, "v00012", "v00011", "E2_1", None, False, "control", "T")
    exp = tr.RunExpectation("ctl", "hashC", 9, (probe,))
    quiet = _write_run(tmp_path / "a", config_hash="hashC", seed=9, collisions=[], **common)
    r = tr.trace_run(quiet, exp, tr.Params())
    assert r["reproduction"]["reproduced"] and r["reproduction"]["n_collisions_expected"] == 0
    assert (
        r["events"][0]["label"] == "T-control"
        and r["events"][0]["arrival_on_edge"]["rear"]["lane"] == 2
    )
    noisy = _write_run(
        tmp_path / "b",
        config_hash="hashC",
        seed=9,
        collisions=[
            {
                "t": 204.5,
                "collider": "v00012",
                "victim": "v00011",
                "type": "collision",
                "lane": "E2_1",
                "pos_m": 51.0,
            }
        ],
        **common,
    )
    r2 = tr.trace_run(noisy, exp, tr.Params())
    assert r2["reproduction"]["reproduced"] is False
    assert [b["label"] for b in r2["events"]] == ["T-control", "unexpected-1"]
    # a pair still in contact, reported again when SUMO detects another collision: traced once
    twice = _write_run(
        tmp_path / "c",
        config_hash="hashC",
        seed=9,
        collisions=[
            {
                "t": t,
                "collider": "v00012",
                "victim": "v00011",
                "type": "collision",
                "lane": "E2_1",
                "pos_m": 51.0,
            }
            for t in (204.5, 205.0)
        ],
        **common,
    )
    r3 = tr.trace_run(twice, exp, tr.Params())
    assert r3["reproduction"]["n_collisions"] == 2
    assert [b["label"] for b in r3["events"]] == ["T-control", "unexpected-1"]


def test_a_run_without_trajectories_is_named_and_fails(tmp_path: Path) -> None:
    run, exp = _rear_end_run(tmp_path)
    (run / "trajectories.parquet").unlink()
    r = tr.trace_run(run, exp, tr.Params())
    assert (
        r["reproduction"]["reproduced"] is True and r["trajectories"] is False and r["events"] == []
    )
    s = tr.summarize([r], [])
    assert s["reproduced"] and s["runs_without_trajectories"] == ["arm seed 1"]
    assert "NOT TRACED (no trajectories.parquet): arm seed 1" in s["reproduction_note"]
    out = tmp_path / "trace.json"
    expect = tmp_path / "expect.json"
    expect.write_text(
        json.dumps(
            [
                {
                    "arm": "arm",
                    "config_hash": "hashR",
                    "seed": 1,
                    "events": [vars(e) for e in exp.events],
                }
            ]
        )
    )
    assert (
        tr.main(["--root", str(tmp_path / "arm"), "--expect", str(expect), "--out", str(out)]) == 3
    )


def test_the_command_line_writes_the_artifact_and_flags_what_did_not_reproduce(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    _rear_end_run(root)
    _, exp_t = _opposing_run(root, rear_change_t=204.0)
    _, exp_r = _rear_end_run(tmp_path / "unused")
    expect = [
        {
            "arm": e.arm,
            "config_hash": e.config_hash,
            "seed": e.seed,
            "events": [vars(ev) for ev in e.events],
        }
        for e in (exp_r, exp_t)
    ]
    expect_file = tmp_path / "expect.json"
    expect_file.write_text(json.dumps(expect))
    out = tmp_path / "artifacts" / "trace.json"
    assert tr.main(["--root", str(root), "--expect", str(expect_file), "--out", str(out)]) == 0
    art = json.loads(out.read_text())
    assert art["reproduced"] is True and art["missing_runs"] == []
    assert art["reference_commit"] == "31c04c4" and len(art["runs"]) == 2
    assert {p["pair"] for p in art["pairs"]} == {"T"}
    # an expected run without a directory is named; status 3
    expect.append({"arm": "gone", "config_hash": "hashX", "seed": 3, "events": []})
    expect_file.write_text(json.dumps(expect))
    assert tr.main(["--root", str(root), "--expect", str(expect_file), "--out", str(out)]) == 3
    art = json.loads(out.read_text())
    assert not art["reproduced"] and art["missing_runs"][0]["arm"] == "gone"
    assert "gone seed 3 (no run directory)" in art["reproduction_note"]
    assert tr.main(["--out", str(out)]) == 2


def test_the_built_in_expectations_are_stage_p8s_events() -> None:
    """§3's six events and the control, positions from the committed battery artifacts."""
    exp = tr.load_expectations("p8c")
    assert len(exp) == 7 and sum(e.n_collisions for e in exp) == 6
    labels = {ev.label: (e.config_hash, e.seed, ev) for e in exp for ev in e.events}
    assert set(labels) == {"R1", "R2", "R3", "T1", "T2", "T3", "T1-control"}
    art = REPO_ROOT / "artifacts"
    for arm, chash in (("dc_cal", "beaaa710e6b3"), ("dc_cal_netfix", "182e3ec2f500")):
        d = json.loads((art / f"validation_mndot_i94_wb_stpaul_weave_xlsfg_{arm}.json").read_text())
        assert d["config_hash"] == chash
        logged = {(loc["lane"], s) for loc in d["collisions"]["locations"] for s in loc["runs"]}
        mine = {(ev.lane, seed) for h, seed, ev in labels.values() if h == chash and ev.collision}
        assert mine == logged
        for loc in d["collisions"]["locations"]:
            pos = sorted(
                ev.pos_m
                for h, _, ev in labels.values()
                if h == chash and ev.lane == loc["lane"] and ev.collision
            )
            assert (pos[0], pos[-1]) == (loc["pos_m_min"], loc["pos_m_max"])
    ctl = labels["T1-control"]
    assert ctl[0] == "beaaa710e6b3" and ctl[1] == 165503670820534583 and not ctl[2].collision
    assert labels["T1"][2].pair == ctl[2].pair == "T1"


def test_read_window_prunes_row_groups_and_keeps_every_row_in_range(tmp_path: Path) -> None:
    rows, _ = _rear_end_rows()
    run = _write_run(
        tmp_path / "w",
        config_hash="h",
        seed=1,
        rows=rows,
        collisions=[],
        ramps=[],
        vtypes={},
        first_samples={},
    )
    got = tr.read_window(run / "trajectories.parquet", 100.0, 105.0)
    want = pd.DataFrame(rows)
    want = want[(want["t"] >= 100.0) & (want["t"] <= 105.0)]
    assert len(got) == len(want) and got["t"].between(100.0, 105.0).all()
    with open(run / "trajectories.parquet", "rb") as f:
        assert pq.ParquetFile(f).metadata.num_row_groups > 3
