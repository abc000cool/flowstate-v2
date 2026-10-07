"""The opt-in weave command recorder (``WeaveSpec.record_commands``; 2026-10-07).

docs/I94_CAL_COLLISIONS.md §15: the collision diagnoses had to infer which
weave rule issued each command to each vehicle, because the runner logged
none. With ``record_commands`` on a weave block, every command decision of
that section's rules (or of the measured model's zone) is one row of
``weave_commands.parquet`` (``microsim.runner.WEAVE_COMMANDS_FILE``,
docs/CONTRACTS.md §3).

Three groups:

* **Call sites** (fake TraCI, the harness of ``test_microsim_merge_managed_meter``,
  no SUMO): each rule's recording call passes its own ``rule`` string with the
  lanes, speed and mode it commanded.
* **Zero cost off** (fake TraCI): the section makes the same TraCI calls, reads
  included, with the recorder on and off.
* **Runs** (SUMO, fixture size): off writes byte-identical files whether the
  flag is unset or false, and never builds the recorder; on, every other file
  is byte-identical and ``meta.json`` differs only by the flag and
  ``weave_command_log`` (the hash is the same: both share one run directory,
  and a run without the flag removes an earlier run's log there); the file has
  the contract's schema, only documented rules, and per-rule row counts equal
  to the meta counters (``WEAVE_COMMAND_COUNTERS``). The comparison with the code before the
  recorder existed was made out of band (two trees, the same five fixtures;
  the change's record).
"""

from __future__ import annotations

import copy
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import tests.test_microsim.test_microsim_merge_managed_meter as mmm
import tests.test_microsim.test_microsim_vacate_window_review as vwr
from flowstate_core.config import ScenarioConfig
from microsim import run_micro
from microsim.runner import (
    _WEAVE_COMMANDS_SCHEMA,
    LC_MODE_MODEL_BITS,
    LC_MODE_SCRIPTED_FORCE,
    LC_MODE_SCRIPTED_SAFE,
    NEIGHBOR_LEFT_FOLLOWERS,
    NEIGHBOR_LEFT_LEADERS,
    NEIGHBOR_RIGHT_FOLLOWERS,
    NEIGHBOR_RIGHT_LEADERS,
    WEAVE_COMMAND_COUNTERS,
    WEAVE_COMMAND_RULES,
    WEAVE_COMMANDS_FILE,
    _measured_ceiling,
    _measured_constants,
    _measured_crossings,
    _measured_handover_step,
    _measured_run_state,
    _measured_step,
    _mm_restore_ceiling,
    _weave_short_section_rule,
    _weave_step,
    _WeaveCommandRecorder,
)
from microsim.vehicles import FleetPlan
from tests.test_microsim.test_microsim_merge_model import _params

_tc = mmm._tc
_res = mmm._res
_WeaveMod = mmm._WeaveMod

DEFAULT_MODE = 1621  # the fake vehicles' own laneChangeMode


def _rows(rec: _WeaveCommandRecorder, vid: str | None = None) -> list[dict[str, Any]]:
    """The recorder's rows as dicts (one vehicle's when ``vid`` is given)."""
    return [r for r in rec.table().to_pylist() if vid is None or r["veh_id"] == vid]


def _rules(rec: _WeaveCommandRecorder, vid: str | None = None) -> list[str]:
    return [r["rule"] for r in _rows(rec, vid)]


def _recording(ws: dict) -> _WeaveCommandRecorder:
    """Attach a recorder to a section state as ``run_micro`` does."""
    rec = _WeaveCommandRecorder()
    ws["cmd_rec"] = rec
    return rec


def _one(rec: _WeaveCommandRecorder, rule: str, vid: str | None = None) -> dict[str, Any]:
    (row,) = [r for r in _rows(rec, vid) if r["rule"] == rule]
    return row


class _MMVehicle(mmm._GuardVehicle):
    """The fake vehicle module with the reads and writes of the measured model."""

    def __init__(self, speeds: dict[str, float], neighbors: dict | None = None) -> None:
        super().__init__(speeds, neighbors)
        self.followers: dict[str, tuple[str, float]] = {}

    def getSpeedFactor(self, vid):
        return 1.0

    def setTau(self, vid, tau):
        self.calls.append(("tau", vid, tau))

    def getFollower(self, vid, dist):
        return self.followers.get(vid)


# --- the recorder itself -----------------------------------------------------


class TestRecorder:
    def test_schema_lane_and_position_from_the_results(self):
        """Columns as the contract names them; ``lane_from`` and ``x_m`` read
        off the step's results (corridor axis: ``x_offset`` + lane position),
        −1 / NaN for a vehicle off the subscription or a road off the axis."""
        rec = _WeaveCommandRecorder()
        ws = mmm._weave_state()
        res = {"a1": _res("a", 1, 12.5, 10.0), "j": _res(":J_0", 0, 1.0, 5.0)}
        rec.step(ws, _tc, res, 7.5)
        rec.add("a1", "change_accept", lane_to=0, lc_mode=LC_MODE_SCRIPTED_FORCE)
        rec.add("j", "cooperate", v_cmd=4.25)
        rec.add("gone", "control_release_missed")
        table = rec.table()
        assert table.schema == pa.schema(_WEAVE_COMMANDS_SCHEMA)
        assert table.schema.names == [
            "t",
            "veh_id",
            "section",
            "rule",
            "lane_from",
            "lane_to",
            "v_cmd_ms",
            "lc_mode_set",
            "x_m",
        ]
        a1, j, gone = table.to_pylist()
        assert a1 == {
            "t": 7.5,
            "veh_id": "a1",
            "section": "on",
            "rule": "change_accept",
            "lane_from": 1,
            "lane_to": 0,
            "v_cmd_ms": pytest.approx(math.nan, nan_ok=True),
            "lc_mode_set": LC_MODE_SCRIPTED_FORCE,
            "x_m": 12.5,
        }
        assert (j["lane_from"], j["v_cmd_ms"], math.isnan(j["x_m"])) == (0, 4.25, True)
        assert (gone["lane_from"], gone["lane_to"], gone["lc_mode_set"]) == (-1, -1, -1)
        assert math.isnan(gone["x_m"]) and len(rec) == 3
        assert rec.rule_counts("on") == {
            "change_accept": 1,
            "control_release_missed": 1,
            "cooperate": 1,
        }

    def test_an_undocumented_rule_is_refused(self):
        rec = _WeaveCommandRecorder()
        rec.step(mmm._weave_state(), _tc, {}, 0.0)
        with pytest.raises(ValueError, match="unknown weave command rule"):
            rec.add("v", "accepted")
        assert len(rec) == 0

    def test_counter_identities_name_documented_rules(self):
        for counters, rules in WEAVE_COMMAND_COUNTERS.items():
            assert counters and all(c.startswith("n_") for c in counters)
            assert set(rules) <= set(WEAVE_COMMAND_RULES), rules


# --- the weave's call sites (fake TraCI) ---------------------------------------


class TestWeaveCallSites:
    def test_take_accept_release(self):
        """An exiter on lane 1 with an empty lane 0: taken (512), its change
        accepted and requested under 256 at once, handed back on lane 0 with
        its own mode."""
        ws = mmm._weave_state()
        rec = _recording(ws)
        veh = mmm._WeaveVehicle({"e": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"e": _res("a", 1, 10.0, 20.0)}, 0.0)
        _weave_step(mod, _tc, ws, {"e": _res("a", 0, 30.0, 20.0)}, 0.5)
        assert _rules(rec) == ["control_take", "change_accept", "control_release"]
        take, acc, rel = _rows(rec)
        assert (take["t"], take["lc_mode_set"], take["lane_from"], take["x_m"]) == (
            0.0,
            LC_MODE_SCRIPTED_SAFE,
            1,
            10.0,
        )
        assert (acc["lane_to"], acc["lc_mode_set"]) == (0, LC_MODE_SCRIPTED_FORCE)
        assert (rel["t"], rel["lc_mode_set"], rel["lane_from"], rel["x_m"]) == (
            0.5,
            DEFAULT_MODE,
            0,
            30.0,
        )
        assert ws["n_entered"] == 1 and ws["n_changed_out"] == 1

    def test_release_still_owing_the_change(self):
        """Gone onto a non-section edge still in lane 1: handed back missed;
        the edge is off the section's axis, so ``x_m`` is NaN."""
        ws = mmm._weave_state()
        rec = _recording(ws)
        veh = mmm._WeaveVehicle({"e": 20.0, "q": 20.0})
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = (("q", 0.5),)
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"e": _res("a", 1, 10.0, 20.0)}, 0.0)
        _weave_step(mod, _tc, ws, {"e": _res("c", 1, 4.0, 20.0)}, 0.5)
        assert _rules(rec) == ["control_take", "control_release_missed"]
        row = _one(rec, "control_release_missed")
        assert row["lc_mode_set"] == DEFAULT_MODE and math.isnan(row["x_m"])
        assert ws["n_missed"] == 1

    def test_hold_reset_after_a_step_under_256(self):
        """Accepted at 0.0 (mode 256); refused at 0.5 outside the forced zone:
        mode 512 is written back — one ``hold_reset``. A later refused step
        writes nothing and records nothing."""
        ws = mmm._weave_state()
        rec = _recording(ws)
        veh = mmm._WeaveVehicle({"e": 20.0, "q": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"e": _res("a", 1, 10.0, 20.0)}, 0.0)
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = (("q", 0.5),)
        _weave_step(mod, _tc, ws, {"e": _res("a", 1, 20.0, 20.0)}, 0.5)
        _weave_step(mod, _tc, ws, {"e": _res("a", 1, 30.0, 20.0)}, 1.0)
        assert _rules(rec) == ["control_take", "change_accept", "hold_reset"]
        row = _one(rec, "hold_reset")
        assert (row["t"], row["lc_mode_set"], row["lane_to"]) == (0.5, LC_MODE_SCRIPTED_SAFE, -1)
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_SAFE

    def test_force_deferred_every_refused_step(self):
        """``test_forced_deferred_counts_vehicle_steps``'s case: two refused
        steps, two rows; the mode was already 512, so none is written."""
        ws = mmm._weave_state(force_after_s=0.0, force_within_m=1000.0)
        ws["exiting_ids"] = frozenset({"e", "f"})
        rec = _recording(ws)
        veh = mmm._WeaveVehicle({"e": 10.0, "f": 20.0}, {("e", 1): (("f", 3.0),)})
        mod = _WeaveMod(veh)
        for k in range(2):
            res = {"e": _res("b", 1, 50.0 + k, 10.0), "f": _res("b", 0, 40.0, 20.0)}
            _weave_step(mod, _tc, ws, res, 0.5 * k)
        deferred = [r for r in _rows(rec, "e") if r["rule"] == "force_deferred"]
        assert [r["t"] for r in deferred] == [0.0, 0.5]
        assert {r["lc_mode_set"] for r in deferred} == {-1}
        assert len(deferred) == ws["n_forced_deferred"] == 2

    def test_change_force(self):
        """The forced change due at once, the follower 3 m back at equal
        speed: refused by the acceptance (0.6 s at 10 m/s), passed by the
        forced guard (no closing speed) — ``change_force`` into lane 0."""
        ws = mmm._weave_state(force_after_s=0.0, force_within_m=1000.0)
        ws["exiting_ids"] = frozenset({"e", "f"})
        rec = _recording(ws)
        veh = mmm._WeaveVehicle(
            {"e": 10.0, "f": 10.0}, {("e", NEIGHBOR_RIGHT_FOLLOWERS): (("f", 3.0),)}
        )
        mod = _WeaveMod(veh)
        res = {"e": _res("b", 1, 50.0, 10.0), "f": _res("b", 0, 42.0, 10.0)}
        _weave_step(mod, _tc, ws, res, 0.0)
        row = _one(rec, "change_force", "e")
        assert (row["lane_from"], row["lane_to"], row["lc_mode_set"]) == (
            1,
            0,
            LC_MODE_SCRIPTED_FORCE,
        )
        assert ("change", "e", 0, 0.5) in veh.calls and ws["veh"]["e"]["forced"]

    def test_exit_giveup(self):
        """An exiter halted 2 m from the gore with no change to request: rerouted
        through, its own mode back — ``exit_giveup``."""
        ws = mmm._weave_state()
        rec = _recording(ws)
        veh = mmm._WeaveVehicle({"e": 0.0, "q": 0.0})
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = (("q", -1.0),)
        _weave_step(_WeaveMod(veh), _tc, ws, {"e": _res("b", 1, 98.0, 0.0)}, 0.0)
        assert _rules(rec) == ["control_take", "exit_giveup"]
        assert _one(rec, "exit_giveup")["lc_mode_set"] == DEFAULT_MODE
        assert ("target", "e", "z") in veh.calls and ws["n_missed_exit"] == 1

    def test_entrant_giveup_release(self):
        """Amendment W1: an entrant halted at the auxiliary lane's end takes the exit."""
        ws = mmm._weave_state(entrant_giveup_m=5.0)
        rec = _recording(ws)
        veh = mmm._WeaveVehicle({"n": 0.0, "q": 0.0})
        veh.neighbors[("n", NEIGHBOR_LEFT_LEADERS)] = (("q", -1.0),)
        _weave_step(_WeaveMod(veh), _tc, ws, {"n": _res("b", 0, 98.0, 0.0)}, 0.0)
        assert _rules(rec) == ["control_take", "entrant_giveup_release"]
        assert _one(rec, "entrant_giveup_release")["lc_mode_set"] == DEFAULT_MODE
        assert ("target", "n", "x2") in veh.calls and ws["n_entrant_took_exit"] == 1

    def test_pair_release_and_yield(self):
        """Exiter e (lane 1, 50 m) committed to the gap behind it whose
        follower is entrant n (lane 0, 42 m), both stopped: standing from
        0.5 s, released at 3.0 s; n, farther from the section end, yields —
        one ``pair_release`` (the release) and a ``pair_yield`` (the step)."""
        ws = mmm._weave_state()
        rec = _recording(ws)
        veh = mmm._WeaveVehicle({"e": 0.0, "n": 0.0})
        mod = _WeaveMod(veh)
        res = {"e": _res("b", 1, 50.0, 0.0), "n": _res("b", 0, 42.0, 0.0)}
        for t in (0.0, 0.5, 3.0):
            _weave_step(mod, _tc, ws, res, t)
        assert ws["veh"]["e"]["target"] == "n" and ws["n_pair_releases"] == 1
        assert [(r["t"], r["rule"]) for r in _rows(rec, "n") if r["rule"].startswith("pair")] == [
            (3.0, "pair_release"),
            (3.0, "pair_yield"),
        ]
        assert not [r for r in _rows(rec, "e") if r["rule"].startswith("pair")]

    def test_cooperate_and_ease(self):
        """The follower's one-step target (``cooperate``) and the rear one of
        an abreast pair easing (``ease``), each with the speed written."""
        ws = mmm._weave_state()
        rec = _recording(ws)
        veh = mmm._WeaveVehicle(
            {"n": 15.0, "f": 15.0}, {("n", NEIGHBOR_LEFT_FOLLOWERS): (("f", 4.0),)}
        )
        res = {"n": _res("a", 0, 50.0, 15.0), "f": _res("a", 1, 40.0, 15.0)}
        _weave_step(_WeaveMod(veh), _tc, ws, res, 0.0)
        row = _one(rec, "cooperate", "f")
        assert row["v_cmd_ms"] == pytest.approx(15.0 - 1.67 * 0.5)
        assert (row["lane_from"], row["lane_to"], row["lc_mode_set"], row["x_m"]) == (
            1,
            -1,
            -1,
            40.0,
        )
        ws = mmm._weave_state()
        rec = _recording(ws)
        veh = mmm._WeaveVehicle(
            {"e": 3.0, "n": 3.0},
            {
                ("e", NEIGHBOR_RIGHT_FOLLOWERS): (("n", -1.0),),
                ("n", NEIGHBOR_LEFT_FOLLOWERS): (("e", -1.0),),
            },
        )
        res = {"e": _res("b", 1, 90.0, 3.0), "n": _res("b", 0, 88.0, 3.0)}
        _weave_step(_WeaveMod(veh), _tc, ws, res, 0.0)
        assert _one(rec, "ease", "n")["v_cmd_ms"] == pytest.approx(3.0 - 1.67 * 0.5)
        assert "cooperate" not in _rules(rec)

    def test_w2_handback_skip_and_close_leader_withheld(self):
        """Amendment W2: the follower's target withheld by the handback, and
        by the close-leader reading; neither writes a speed."""
        ws, veh, mod, res = mmm.TestWeaveCollisionGuards._cooperating(weave_handback=1.0)
        rec = _recording(ws)
        veh.follow["f"] = 10.0
        _weave_step(mod, _tc, ws, res, 0.0)
        row = _one(rec, "handback_skip", "f")
        assert math.isnan(row["v_cmd_ms"]) and "cooperate" not in _rules(rec)
        ws, veh, mod, res = mmm.TestWeaveCollisionGuards._cooperating(weave_close_leader=1.0)
        rec = _recording(ws)
        veh.leaders["f"] = ("g", -0.5)
        veh.speeds["g"] = 10.0
        _weave_step(mod, _tc, ws, res, 0.0)
        assert math.isnan(_one(rec, "close_leader_withheld", "f")["v_cmd_ms"])
        assert ws["n_close_leader_withheld"] == 1

    def test_w2_opposing_resolution(self):
        """Amendment W2's resolution: the rear exiter withheld
        (``opposing_deferred``) and the entrant's change executed through the
        resolution (``change_accept``); an undriven vehicle vetoed
        (``opposing_vetoed``, model bits cleared) and given its mode back at
        the next step (``opposing_restore``)."""
        ws, _veh, mod, res = mmm.TestWeaveCollisionGuards._opposing(
            True, weave_resolve_opposing=1.0
        )
        rec = _recording(ws)
        _weave_step(mod, _tc, ws, res, 0.0)
        assert _one(rec, "opposing_deferred", "e")["lc_mode_set"] == -1  # 512 already
        acc = _one(rec, "change_accept", "n")
        assert (acc["lane_to"], acc["lc_mode_set"]) == (1, LC_MODE_SCRIPTED_FORCE)
        ws, _veh, mod, res = mmm.TestWeaveCollisionGuards._opposing(
            False, weave_resolve_opposing=1.0
        )
        rec = _recording(ws)
        _weave_step(mod, _tc, ws, res, 0.0)
        vetoed = DEFAULT_MODE & ~LC_MODE_MODEL_BITS
        assert _one(rec, "opposing_vetoed", "p")["lc_mode_set"] == vetoed
        assert _one(rec, "change_accept", "e")["lane_to"] == 1
        res = {"e": _res("a", 1, 60.0, 10.0), "p": _res("a", 0, 65.0, 10.0)}
        _weave_step(mod, _tc, ws, res, 0.5)
        row = _one(rec, "opposing_restore", "p")
        assert (row["t"], row["lc_mode_set"]) == (0.5, DEFAULT_MODE)

    def test_vacate_rule(self):
        """The vacate request, its re-address on the next window edge, and the
        hand-back as vacated (with the stay that ends the request, open till
        15 s) or as refused (none: it expired that step)."""
        ws = vwr.TestVacateStepReview._state(vacate_ahead_m=500.0)
        ws["vacate_lanes"] = {"p": (0, 1), "q": (1, 2)}
        ws["x_offset"]["q"] = -400.0
        rec = _recording(ws)
        veh = mmm._WeaveVehicle({"t": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"t": _res("q", 1, 100.0, 20.0)}, 0.0)
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 5.0, 20.0)}, 10.0)
        _weave_step(mod, _tc, ws, {"t": _res("p", 1, 15.0, 20.0)}, 10.5)
        assert _rules(rec) == ["vacate", "vacate_readdress", "vacate_done"]
        ask, again, done = _rows(rec)
        assert (ask["lane_from"], ask["lane_to"], ask["lc_mode_set"], ask["x_m"]) == (
            1,
            2,
            LC_MODE_SCRIPTED_SAFE,
            -300.0,
        )
        assert (again["t"], again["lane_to"], again["lc_mode_set"]) == (10.0, 1, -1)
        assert (done["lane_to"], done["lc_mode_set"]) == (1, DEFAULT_MODE)  # stay: open till 15 s
        assert ws["n_vacate_requests"] == 1 and ws["n_vacated"] == 1
        ws = vwr.TestVacateStepReview._state()
        rec = _recording(ws)
        veh = mmm._WeaveVehicle({"w": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"w": _res("p", 0, 195.0, 20.0)}, 0.0)
        _weave_step(mod, _tc, ws, {"w": _res("a", 1, 5.0, 20.0)}, 0.5)
        assert _rules(rec, "w")[:2] == ["vacate", "vacate_refused"]
        refused = _one(rec, "vacate_refused", "w")
        assert (refused["lane_to"], refused["lc_mode_set"]) == (-1, DEFAULT_MODE)

    def test_exiters_early_move(self):
        """The exiters' early move: the request, its suspension beside a
        vacating vehicle and re-issue once clear, the hand-back as prepared
        (with the stay ending the open request) and as refused."""
        ws = mmm.TestWeaveExitPrepare._state()
        rec = _recording(ws)
        veh = mmm._WeaveVehicle({"t": 20.0, "e": 20.0})
        mod = _WeaveMod(veh)
        for t, xt, xe in ((0.0, 100.0, 102.0), (0.5, 101.0, 112.0), (1.0, 111.0, 113.0)):
            res = {"t": _res("p", 0, xt, 20.0), "e": _res("p", 1, xe, 20.0)}
            _weave_step(mod, _tc, ws, res, t)
        _weave_step(
            mod, _tc, ws, {"t": _res("p", 0, 100.0, 20.0), "e": _res("p", 1, 120.0, 20.0)}, 2.0
        )
        _weave_step(
            mod, _tc, ws, {"t": _res("p", 1, 101.0, 20.0), "e": _res("p", 0, 125.0, 20.0)}, 2.5
        )
        assert _rules(rec, "e") == [
            "exit_prepare",
            "exit_prepare_suspend",
            "exit_prepare_reissue",
            "exit_prepare_done",
        ]
        ask, suspend, reissue, done = _rows(rec, "e")
        assert (ask["t"], ask["lane_to"], ask["lc_mode_set"]) == (0.5, 0, LC_MODE_SCRIPTED_SAFE)
        assert (suspend["t"], suspend["lane_to"]) == (1.0, 1)
        assert (reissue["t"], reissue["lane_to"]) == (2.0, 0)
        assert (done["lane_to"], done["lc_mode_set"]) == (0, DEFAULT_MODE)
        assert ws["n_exit_prepared"] == 1
        ws = mmm.TestWeaveExitPrepare._state()
        rec = _recording(ws)
        veh = mmm._WeaveVehicle({"e": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"e": _res("p", 1, 100.0, 20.0)}, 0.0)
        _weave_step(mod, _tc, ws, {"e": _res("a", 2, 2.0, 20.0)}, 0.5)
        refused = _one(rec, "exit_prepare_refused", "e")
        assert (refused["lane_to"], refused["lc_mode_set"]) == (2, DEFAULT_MODE)
        # handed back before the section takes it under control
        assert _rules(rec, "e")[:3] == ["exit_prepare", "exit_prepare_refused", "control_take"]


# --- the measured model's call sites (fake TraCI) ------------------------------


def _measured_state(rec: _WeaveCommandRecorder | None = None) -> dict:
    """An acceleration-lane zone on ``_weave_state``'s edges ``a``, ``b``.

    Lane 0 dead-ends (it reaches nothing), lanes 1 and 2 reach ``after``,
    every vehicle's route continues onto ``after``: a vehicle on lane 0 owes
    one change left. Drivers ``*0`` .. ``*2`` read quantile 0 (the median
    critical gaps of :func:`_params`).
    """
    plan = FleetPlan(
        params=(),
        is_av=(),
        complied=(),
        depart_s=(),
        depart_pos_m=(),
        merge_z_lead=(0.0, 0.0, 0.0),
        merge_z_lag=(0.0, 0.0, 0.0),
    )
    run = _measured_run_state(_params(), plan, (), {}, 0.5)
    params = _measured_constants()
    ws = mmm._weave_state()
    ws.update(
        {
            "params": params,
            "rule": _weave_short_section_rule(200.0, params),
            "exiting_ids": frozenset(),
            "vacate_exempt_ids": frozenset(),
            "veh_params": run["veh_params"],
            "handover": {},
            "n_handovers": 0,
            "mm": {
                "index": 0,
                "run": run,
                "weave": False,
                "kind": "acceleration_lane",
                "reach": {
                    e: {0: frozenset(), 1: frozenset({"after"}), 2: frozenset({"after"})}
                    for e in ("a", "b")
                },
                "target_of_route": {"main": "after"},
                "route_by_id": {},
                "through_edge": "after",
                "ceiling": {},
                "touched": {},
                "n_crossings_in": 0,
                "n_crossings_out": 0,
                "n_exec_accepted": 0,
                "n_exec_forced": 0,
                "n_requests": 0,
                "n_requests_cancelled": 0,
                "n_model_checks": 0,
                "n_lead_model_checks": 0,
                "refused": dict.fromkeys(
                    ("lead_time", "lead_guard", "lag_time", "lag_guard", "lead_model", "lag_model"),
                    0,
                ),
                "n_relax_entrant": 0,
                "n_relax_follower": 0,
                "n_ceiling_steps": 0,
                "n_early_crossings": 0,
                "n_av_released": 0,
                "n_arrival_changes_kept": 0,
                "approach_k": {},
                "n_collisions": 0,
                "cross_x_in": [],
                "cross_x_out": [],
            },
        }
    )
    if rec is not None:
        ws["cmd_rec"] = rec
    return ws


class TestMeasuredCallSites:
    def test_step_take_accept_release_and_hold_reset(self):
        """Entrant n0 on lane 0 with lane 1 empty: taken, accepted (256);
        seen on lane 1: handed back (``control_release``). Another, refused
        in the step after its request with the request open: 512 written back
        and the request ended by a stay — one ``hold_reset``."""
        rec = _WeaveCommandRecorder()
        ws = _measured_state(rec)
        veh = _MMVehicle({"n0": 10.0})
        mod = _WeaveMod(veh)
        _measured_step(mod, _tc, ws, {"n0": _res("a", 0, 50.0, 10.0)}, 0.0)
        _measured_step(mod, _tc, ws, {"n0": _res("a", 1, 55.0, 10.0)}, 0.5)
        assert _rules(rec) == ["control_take", "change_accept", "control_release"]
        acc, rel = _rows(rec)[1:]
        assert (acc["lane_to"], acc["lc_mode_set"]) == (1, LC_MODE_SCRIPTED_FORCE)
        assert (rel["lane_to"], rel["lc_mode_set"]) == (-1, DEFAULT_MODE)
        assert ws["mm"]["n_requests"] == 1 and ws["n_changed_in"] == 1
        rec = _WeaveCommandRecorder()
        ws = _measured_state(rec)
        veh = _MMVehicle({"n0": 10.0, "q": 10.0})
        mod = _WeaveMod(veh)
        _measured_step(mod, _tc, ws, {"n0": _res("a", 0, 50.0, 10.0)}, 0.0)
        veh.neighbors[("n0", NEIGHBOR_LEFT_LEADERS)] = (("q", 0.5),)
        _measured_step(mod, _tc, ws, {"n0": _res("a", 0, 55.0, 10.0)}, 0.5)
        _measured_step(mod, _tc, ws, {"n0": _res("a", 0, 60.0, 10.0)}, 1.0)
        assert _rules(rec) == ["control_take", "change_accept", "hold_reset"]
        hold = _one(rec, "hold_reset")
        assert (hold["t"], hold["lane_to"], hold["lc_mode_set"]) == (0.5, 0, LC_MODE_SCRIPTED_SAFE)

    def test_step_opposing_veto_and_restore(self):
        """The measured model's always-on resolution: entrant n0 (lane 0,
        55 m) asks into lane 1; p1, undriven on lane 2 ahead of it, is vetoed
        for the step and restored at the start of the next."""
        rec = _WeaveCommandRecorder()
        ws = _measured_state(rec)
        veh = _MMVehicle({"n0": 10.0, "p1": 10.0})
        mod = _WeaveMod(veh)
        res = {"n0": _res("a", 0, 55.0, 10.0), "p1": _res("a", 2, 60.0, 10.0)}
        _measured_step(mod, _tc, ws, res, 0.0)
        assert _rules(rec) == ["control_take", "change_accept", "opposing_vetoed"]
        assert _one(rec, "opposing_vetoed", "p1")["lc_mode_set"] == (
            DEFAULT_MODE & ~LC_MODE_MODEL_BITS
        )
        res = {"n0": _res("a", 1, 60.0, 10.0), "p1": _res("a", 2, 65.0, 10.0)}
        _measured_step(mod, _tc, ws, res, 0.5)
        assert _rules(rec)[3] == "opposing_restore"
        assert _one(rec, "opposing_restore", "p1")["lc_mode_set"] == DEFAULT_MODE

    def test_handover_and_return(self):
        """``_measured_handover_step``: an entrant within two steps' travel of
        the zone taken under 512 (``measured_handover``), and given its own
        mode back when it leaves the window undriven."""
        rec = _WeaveCommandRecorder()
        ws = _measured_state(rec)
        ws["ramp_edges"] = frozenset({"r"})
        ws["x_offset"]["r"] = -50.0
        ws["lane_map"][("r", 0)] = 0
        veh = _MMVehicle({"n0": 10.0})
        mod = _WeaveMod(veh)
        res = {"n0": _res("r", 0, 47.0, 10.0)}
        rec.step(ws, _tc, res, 0.0)
        _measured_handover_step(mod, _tc, ws, res, {"n0": -3.0}, {"n0": 10.0}, {})
        rec.step(ws, _tc, res, 0.5)
        _measured_handover_step(mod, _tc, ws, res, {}, {}, {})
        assert _rules(rec) == ["measured_handover", "measured_handover_return"]
        take, back = _rows(rec)
        assert (take["lc_mode_set"], take["x_m"]) == (LC_MODE_SCRIPTED_SAFE, -3.0)
        assert (back["t"], back["lc_mode_set"]) == (0.5, DEFAULT_MODE)

    def test_ceiling_written_and_handed_back(self):
        """The ceiling (``setMaxSpeed``) towards a slow gap leader, its release
        with no leader to match, and the hand-back at control's end; an
        unchanged ceiling or a vehicle gone writes nothing."""
        rec = _WeaveCommandRecorder()
        ws = _measured_state(rec)
        veh = _MMVehicle({"n0": 10.0})
        mod = _WeaveMod(veh)
        res = {"n0": _res("a", 0, 50.0, 10.0)}
        rec.step(ws, _tc, res, 0.0)
        x_of, v_of, p_of = {"n0": 50.0, "l2": 70.0}, {"l2": 5.0}, {"l2": {"len": 5.0}}
        _measured_ceiling(mod, ws, "n0", 10.0, 30.0, "l2", x_of, v_of, p_of)
        _measured_ceiling(mod, ws, "n0", 10.0, 30.0, "l2", x_of, v_of, p_of)  # unchanged
        _measured_ceiling(mod, ws, "n0", 10.0, 30.0, None, x_of, v_of, p_of)
        _measured_ceiling(mod, ws, "n0", 10.0, 30.0, "l2", x_of, v_of, p_of)
        _mm_restore_ceiling(mod, ws, "n0", True)
        _measured_ceiling(mod, ws, "n0", 10.0, 30.0, "l2", x_of, v_of, p_of)
        _mm_restore_ceiling(mod, ws, "n0", False)  # gone: nothing written
        writes = [c[2] for c in veh.calls if c[0] == "vmax"]
        rows = _rows(rec)
        assert [r["rule"] for r in rows] == [
            "ceiling",
            "ceiling_release",
            "ceiling",
            "ceiling_release",
            "ceiling",
        ]
        assert [r["v_cmd_ms"] for r in rows] == pytest.approx(writes)
        assert rows[0]["v_cmd_ms"] < 30.0 and rows[1]["v_cmd_ms"] == pytest.approx(33.3)

    def test_relaxation_grants(self):
        """``_measured_crossings``: the entrant's crossing read, the relaxation
        granted to it and to its new follower — one row each."""
        rec = _WeaveCommandRecorder()
        ws = _measured_state(rec)
        veh = _MMVehicle({"n0": 20.0, "f1": 20.0, "l2": 20.0})
        veh.leaders["n0"] = ("l2", 10.0)
        veh.followers["n0"] = ("f1", 8.0)
        mod = _WeaveMod(veh)
        ws["veh"]["n0"] = {
            "dir": 1,
            "k": 0,
            "open": True,
            "last_kind": "acc",
            "entered_s": 0.0,
            "exiter": False,
            "forced": False,
        }
        res = {"n0": _res("a", 1, 60.0, 20.0), "f1": _res("a", 1, 45.0, 20.0)}
        rec.step(ws, _tc, res, 5.0)
        _measured_crossings(mod, _tc, ws, res, {"n0": 60.0, "f1": 45.0}, 5.0)
        assert [(r["veh_id"], r["rule"]) for r in _rows(rec)] == [
            ("n0", "relax_entrant"),
            ("f1", "relax_follower"),
        ]
        assert ws["mm"]["n_relax_entrant"] == 1 and ws["mm"]["n_relax_follower"] == 1


# --- zero cost when off --------------------------------------------------------


class _Logged:
    """A fake TraCI domain that logs every call made on it, reads included."""

    def __init__(self, inner: Any, log: list[tuple]) -> None:
        self._inner = inner
        self._log = log

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            self._log.append((name, args, tuple(sorted(kwargs.items()))))
            return attr(*args, **kwargs)

        return call


class _LoggedMod:
    TraCIException = mmm._FakeTraCIException

    def __init__(self, vehicle: Any, log: list[tuple]) -> None:
        self.vehicle = _Logged(vehicle, log)
        self.lane = _Logged(mmm._WeaveLane(), log)


def _scripted_run(record: bool) -> tuple[list[tuple], dict, _WeaveCommandRecorder | None]:
    """Eight steps of a section with every W2 switch, W1 and the vacate and
    early-move windows: an exiter ``e`` vetoing the undriven exiter ``p``
    ahead of it on lane 0 each step, an entrant ``n`` whose gap follower
    ``f`` cooperates on even steps and is handed back on odd ones (its own
    model then brakes harder), a through vehicle ``t`` asked to vacate, an
    exiter ``e2`` asked to move early, ``h`` behind with a leader inside its
    ``minGap``."""
    ws = mmm._weave_state(
        weave_handback=1.0,
        weave_close_leader=1.0,
        weave_resolve_opposing=1.0,
        entrant_giveup_m=5.0,
        vacate_ahead_m=150.0,
        exit_prepare=1.0,
    )
    ws["vacate_lanes"] = {"p": (0, 1)}
    ws["lane_map"].update({("p", 0): 1, ("p", 1): 2})
    ws["x_offset"]["p"] = -200.0
    ws["exiting_ids"] = frozenset({"e", "e2", "p"})
    ws["vacate_exempt_ids"] = ws["exiting_ids"]
    rec = _recording(ws) if record else None
    veh = mmm._GuardVehicle(
        dict.fromkeys(("e", "n", "p", "t", "e2", "f", "g", "h"), 10.0),
        {("n", NEIGHBOR_LEFT_FOLLOWERS): (("f", 4.0),)},
    )
    veh.leaders["f"] = ("g", 40.0)
    veh.leaders["h"] = ("g", -0.5)
    log: list[tuple] = []
    mod = _LoggedMod(veh, log)
    for k in range(8):
        veh.follow["f"] = 9.0 if k % 2 else 10.0
        res = {
            "e": _res("a", 2, 55.0 + k, 10.0),
            "p": _res("a", 0, 60.0 + k, 10.0),
            "n": _res("a", 0, 40.0 + k, 10.0),
            "f": _res("a", 1, 30.0 + k, 10.0),
            "h": _res("a", 1, 20.0 + k, 10.0),
            "t": _res("p", 0, 100.0 + 5 * k, 10.0),
            "e2": _res("p", 1, 110.0 + 5 * k, 10.0),
        }
        _weave_step(mod, _tc, ws, res, 0.5 * k)
    return log, ws, rec


class TestZeroCostOff:
    def test_same_traci_calls_with_the_recorder_on_and_off(self):
        """Every TraCI call of the section, reads included, is the same with
        the recorder on: it reads only the step's results. Off, the state has
        no recorder at all."""
        log_off, ws_off, _ = _scripted_run(False)
        log_on, ws_on, rec = _scripted_run(True)
        assert log_on == log_off and len(log_off) > 50
        assert "cmd_rec" not in ws_off
        assert rec is not None and len(rec) > 10
        rules = set(_rules(rec))
        assert {
            "vacate",
            "exit_prepare",
            "control_take",
            "change_accept",
            "cooperate",
            "handback_skip",
            "opposing_vetoed",
            "opposing_restore",
        } <= rules
        counters = {k: v for k, v in ws_off.items() if k.startswith("n_")}
        assert counters == {k: v for k, v in ws_on.items() if k.startswith("n_")}


# --- SUMO runs (fixture size) ---------------------------------------------------

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
W2 = {"weave_handback": 1.0, "weave_close_leader": 1.0, "weave_resolve_opposing": 1.0}


def _weave_raw(merge: str = "weave", weave_params: dict | None = None) -> dict:
    """The golden weave case (``tests/fixtures/weave.osm``, 300 s), optionally measured."""
    weave: dict[str, Any] = {"exit_ramp": "weave exit"}
    if weave_params:
        weave["weave_params"] = dict(weave_params)
    return {
        "name": f"weave_commands_{merge}",
        "network": {
            "kind": "osm",
            "osm_file": str(FIXTURES / "weave.osm"),
            "corridor_edges": ["100", "101", "102", "103", "104"],
            "inflow": [[0.0, 0.55], [140.0, 0.0]],
            "ramps": [
                {
                    "kind": "on",
                    "name": "weave on-ramp",
                    "edges": ["200"],
                    "attach_edge": "102",
                    "inflow": [[0.0, 0.2], [140.0, 0.0]],
                    "merge": merge,
                    "weave": weave,
                },
                {
                    "kind": "off",
                    "name": "weave exit",
                    "edges": ["201"],
                    "attach_edge": "102",
                    "exit_fraction": [[0.0, 0.3]],
                },
            ],
        },
        "sim": {"duration_s": 300.0},
    }


def _th52_raw() -> dict:
    """The T.H.52 fixture at capacity with W2 on (600 s): pair releases,
    a missed exit, opposing vetoes, vacate requests."""
    return {
        "name": "weave_commands_th52",
        "network": {
            "kind": "osm",
            "osm_file": str(FIXTURES / "weave_th52.osm"),
            "corridor_edges": ["100", "101", "102", "103", "104"],
            "inflow": [[0.0, 4500.0 / 3600.0]],
            "ramps": [
                {
                    "kind": "on",
                    "name": "th52",
                    "edges": ["200"],
                    "attach_edge": "102",
                    "inflow": [[0.0, 1400.0 / 3600.0]],
                    "merge": "weave",
                    "weave": {"exit_ramp": "cd exit", "weave_params": dict(W2)},
                },
                {
                    "kind": "off",
                    "name": "cd exit",
                    "edges": ["201"],
                    "attach_edge": "102",
                    "exit_fraction": [[0.0, 0.25]],
                },
            ],
        },
        "sim": {"duration_s": 600.0},
    }


def _with_flag(raw: dict, value: bool | None) -> ScenarioConfig:
    doc = copy.deepcopy(raw)
    if value is not None:
        for r in doc["network"]["ramps"]:
            if r.get("weave") is not None:
                r["weave"]["record_commands"] = value
    return ScenarioConfig.model_validate(doc)


#: meta.json keys that differ between two runs of one config (wall clock).
_WALL_KEYS = ("wall_time_s", "realtime_factor")


def _meta(paths, root: Path) -> dict:
    """``meta.json`` without the wall-clock keys, the run root masked."""
    text = paths.meta.read_text().replace(str(root), "<ROOT>")
    meta = json.loads(text)
    for key in _WALL_KEYS:
        meta.pop(key)
    return meta


def _parquet_bytes(paths) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(paths.run_dir.glob("*.parquet"))}


def _read_log(paths) -> pa.Table:
    with (paths.run_dir / WEAVE_COMMANDS_FILE).open("rb") as f:
        return pq.read_table(f)


@pytest.mark.integration
class TestRecorderRuns:
    def test_off_is_byte_identical_and_builds_no_recorder(self, tmp_path, monkeypatch):
        """Unset and explicitly false, on the weave fixture with every W2
        switch, W1 and the early move on (so every recording call site's
        guard runs): the same bytes in every file, meta.json the same but for
        the wall clock, no log and no ``weave_command_log``; the recorder is
        never built nor called (both refuse here)."""

        def refuse(*_a: Any, **_k: Any) -> None:
            raise AssertionError("the recorder was used with record_commands off")

        monkeypatch.setattr(_WeaveCommandRecorder, "__init__", refuse)
        monkeypatch.setattr(_WeaveCommandRecorder, "add", refuse)
        raw = _weave_raw(weave_params={**W2, "entrant_giveup_m": 5.0, "exit_prepare": 1.0})
        unset, false = _with_flag(raw, None), _with_flag(raw, False)
        assert unset == false and unset.model_dump(mode="json") == false.model_dump(mode="json")
        p_unset = run_micro(unset, 3, tmp_path / "unset")
        p_false = run_micro(false, 3, tmp_path / "false")
        assert _parquet_bytes(p_unset) == _parquet_bytes(p_false)
        assert set(_parquet_bytes(p_unset)) == {
            "edges.parquet",
            "journeys.parquet",
            "trajectories.parquet",
            "vehicles.parquet",
        }
        m_unset, m_false = _meta(p_unset, tmp_path / "unset"), _meta(p_false, tmp_path / "false")
        assert m_unset == m_false
        assert "weave_command_log" not in m_unset
        assert "record_commands" not in m_unset["config"]["network"]["ramps"][0]["weave"]
        assert m_unset["weave_sections"][0]["n_entered"] > 0

    def test_on_changes_no_other_output(self, tmp_path):
        """On: trajectories, edges, vehicles and journeys byte-identical to
        off; meta.json differs only by the flag in the config and
        ``weave_command_log``: the config hash is the same (the recorder is
        hash-neutral on and off, review 2026-10-07), so both runs share one
        ``<config_hash>/<seed>`` directory. Run there in turn (off, on, off),
        the last run leaves no log behind from the one before."""
        raw = _weave_raw(weave_params={**W2, "entrant_giveup_m": 5.0, "exit_prepare": 1.0})
        root = tmp_path / "runs"
        p_off = run_micro(_with_flag(raw, None), 3, root)
        off, m_off = _parquet_bytes(p_off), _meta(p_off, root)
        p_on = run_micro(_with_flag(raw, True), 3, root)
        assert p_on.run_dir == p_off.run_dir
        on, m_on = _parquet_bytes(p_on), _meta(p_on, root)
        assert set(on) == {*off, WEAVE_COMMANDS_FILE}
        assert all(on[name] == data for name, data in off.items())
        assert {k for k in {*m_off, *m_on} if m_off.get(k) != m_on.get(k)} == {
            "config",
            "weave_command_log",
        }
        assert m_on["config_hash"] == m_off["config_hash"] == p_off.run_dir.parent.name
        assert m_on["config"]["network"]["ramps"][0]["weave"].pop("record_commands") is True
        assert m_on["config"] == m_off["config"]
        # off again in the same directory: the previous run's log is gone
        p_again = run_micro(_with_flag(raw, False), 3, root)
        assert p_again.run_dir == p_off.run_dir
        assert not (p_again.run_dir / WEAVE_COMMANDS_FILE).exists()
        assert _parquet_bytes(p_again) == off
        assert _meta(p_again, root) == m_off

    @pytest.mark.parametrize(
        ("case", "seed"),
        [("weave_w2_w1", 3), ("measured", 3), ("th52_w2", 5)],
    )
    def test_log_matches_the_contract_and_the_meta_counters(self, tmp_path, case, seed):
        """The file's schema, documented rules only, ``meta.json``'s record of
        it, the counter identities of ``WEAVE_COMMAND_COUNTERS`` on the
        section's meta entry, and each row's ``lane_from`` / ``x_m`` equal to
        the vehicle's trajectory row of that step on corridor edges."""
        raw = {
            "weave_w2_w1": lambda: _weave_raw(
                weave_params={**W2, "entrant_giveup_m": 5.0, "exit_prepare": 1.0}
            ),
            "measured": lambda: _weave_raw(merge="measured"),
            "th52_w2": _th52_raw,
        }[case]()
        paths = run_micro(_with_flag(raw, True), seed, tmp_path)
        meta = json.loads(paths.meta.read_text())
        table = _read_log(paths)
        assert table.schema == pa.schema(_WEAVE_COMMANDS_SCHEMA)
        df = table.to_pandas()
        assert len(df) > 0 and set(df["rule"]) <= set(WEAVE_COMMAND_RULES)
        (entry,) = meta["measured_merges"] if case == "measured" else meta["weave_sections"]
        log = meta["weave_command_log"]
        assert log["file"] == WEAVE_COMMANDS_FILE and log["n_rows"] == len(df)
        assert set(df["section"]) == set(log["sections"]) == {entry["ramp"]}
        counts = Counter(df["rule"])
        assert log["sections"][entry["ramp"]] == dict(sorted(counts.items()))
        checked = 0
        for counters, rules in WEAVE_COMMAND_COUNTERS.items():
            if all(c in entry for c in counters):
                assert sum(entry[c] for c in counters) == sum(counts[r] for r in rules), counters
                checked += 1
        assert checked >= 12
        # rows are in decision order, one step at a time
        assert df["t"].is_monotonic_increasing
        # speeds only on the speed commands; lane targets on the change requests
        speed_rules = {"cooperate", "ease", "ceiling", "ceiling_release"}
        assert df.loc[df["rule"].isin(speed_rules), "v_cmd_ms"].notna().all()
        assert df.loc[~df["rule"].isin(speed_rules), "v_cmd_ms"].isna().all()
        changes = df[df["rule"].isin({"change_accept", "change_force", "vacate"})]
        assert (changes["lane_to"] >= 0).all()
        assert (df.loc[df["rule"] == "control_take", "lc_mode_set"] == LC_MODE_SCRIPTED_SAFE).all()
        # lane and position read off the step's subscription: as the trajectories
        with (paths.run_dir / "trajectories.parquet").open("rb") as f:
            traj = pq.read_table(f, columns=["t", "veh_id", "x", "lane"]).to_pandas()
        # (a vehicle with a trajectory row that step was on a corridor edge;
        # rows on the section's own ramp or a junction lane have none)
        both = df.merge(traj, on=["t", "veh_id"])
        assert len(both) > 0 and both["x_m"].notna().all()
        assert (both["x_m"] == both["x"]).all() and (both["lane_from"] == both["lane"]).all()
