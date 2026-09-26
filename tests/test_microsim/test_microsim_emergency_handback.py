"""``AVSpec.emergency_handback`` (WP-95; docs/I24_STRATEGIES.md, dated section).

The I-24 strategy sweep recorded 311 SUMO collisions, all in its three
FollowerStopper cells; in 305 of them the rear vehicle is a compliant AV. The
runner drives every compliant AV with ``vehicle.setSpeed`` under SUMO's
default speed mode, and in SUMO 1.27.1 the influencer's maximum-deceleration
clamp is applied after, and so overrides, its safe-speed clamp: a commanded
vehicle never brakes harder than ``minNextSpeed`` allows (``b`` for EIDM,
``max(b, min(emergencyDecel, 1.5))`` for IDM), while its car-following model
alone may brake up to ``emergencyDecel`` (9 m/s²). With the key on, the
command is withdrawn for any step in which the model must brake harder than
that. These tests pin:

* the command's deceleration bound per model (``_command_decel``);
* the per-step pass on a fake SUMO (``_handback_needed``,
  ``_emergency_handback_step``): withdraw, count, re-apply, drop;
* the config field: off by default, hash-neutral when off;
* a cut-in built by hand in front of a commanded vehicle on real SUMO: the
  held command collides braking at exactly ``b``, the model alone and the
  handback do not;
* ``run_micro`` on the interchange fixture ``tests/fixtures/merge.osm`` with
  the I-24 fleet and FollowerStopper at 10 %: the default collides, the key
  does not; and on the Sugiyama ring, where the model never needs more than
  the command allows, the key changes nothing.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from traci import constants as tc

from flowstate_core.config import AVSpec, ScenarioConfig, config_hash
from microsim import load_scenario, run_micro
from microsim.networks import corridor
from microsim.runner import (
    IDM_MIN_NEXT_SPEED_DECEL,
    LEADER_LOOKAHEAD_M,
    _command_decel,
    _emergency_handback_step,
    _handback_needed,
)

REPO = Path(__file__).resolve().parents[2]
MERGE_OSM = REPO / "tests" / "fixtures" / "merge.osm"
STEP_S = 0.5

#: The I-24 strategy sweep's fleet (``scenarios/i24_replica_flow_speedcal_ramps.yaml``,
#: ``fleet``): the capacity-scaled I-24 IDM population, whose ``b`` has mean
#: 1.70 and standard deviation 0.89 m/s².
I24_FLEET: dict[str, Any] = {
    "model": "IDM",
    "heterogeneity_frac": 0.12,
    "idm_calibration": "artifacts/idm_i24_capacity.json",
    "lc_strategic": 5.0,
    "lc_strategic_ramp": 1.0,
    "lc_keep_right": 0.0,
}


# --- the command's deceleration bound ----------------------------------------


def test_command_decel_follows_sumo_min_next_speed() -> None:
    # IDM: max(decel, min(emergencyDecel, 1.5)) (MSCFModel_IDM::minNextSpeed)
    assert _command_decel("IDM", 1.67, 9.0) == 1.67
    assert _command_decel("IDM", 0.8, 9.0) == IDM_MIN_NEXT_SPEED_DECEL == 1.5
    assert _command_decel("IDM", 1.2, 1.3) == 1.3
    # every other model: decel (MSCFModel::minNextSpeed)
    assert _command_decel("EIDM", 0.8, 9.0) == 0.8


# --- a fake SUMO for the per-step pass -----------------------------------------


class _Vehicle:
    """Scripted leaders and follow speeds; records every ``setSpeed``."""

    def __init__(self) -> None:
        self.leaders: dict[str, tuple[str, float]] = {}
        self.follow: dict[str, float] = {}
        self.speeds: dict[str, float] = {"L": 10.0}
        self.calls: list[tuple[str, float]] = []
        self.follow_args: list[tuple[Any, ...]] = []

    def getLeader(self, vid: str, dist: float) -> tuple[str, float]:
        assert dist == LEADER_LOOKAHEAD_M
        return self.leaders.get(vid, ("", -1.0))

    def getFollowSpeed(self, vid: str, *args: Any) -> float:
        self.follow_args.append((vid, *args))
        return self.follow[vid]

    def getSpeed(self, vid: str) -> float:
        return self.speeds[vid]

    def getDecel(self, vid: str) -> float:
        return 1.0

    def getEmergencyDecel(self, vid: str) -> float:
        return 9.0

    def setSpeed(self, vid: str, v: float) -> None:
        self.calls.append((vid, v))


class _Mod:
    def __init__(self) -> None:
        self.vehicle = _Vehicle()


def _hb(held: dict[str, float]) -> dict[str, Any]:
    """Handback state as ``run_micro`` builds it, after a dispatch of ``held``."""
    return {
        "model": "IDM",
        "held": dict(held),
        "in_force": set(held),
        "decel": {},
        "n_vehicle_steps": 0,
        "n_withdrawals": 0,
        "vehicles": set(),
    }


def _res(v: float) -> dict[int, float]:
    return {tc.VAR_SPEED: v}


def test_handback_needed_compares_the_follow_speed_with_the_command_floor() -> None:
    mod = _Mod()
    # no leader within the lookahead: never
    assert not _handback_needed(mod, "a", 20.0, 1.5, STEP_S)
    mod.vehicle.leaders["a"] = ("L", 6.0)
    # the floor this step is 20 - 1.5 * 0.5 = 19.25 m/s
    mod.vehicle.follow["a"] = 19.25
    assert not _handback_needed(mod, "a", 20.0, 1.5, STEP_S)
    mod.vehicle.follow["a"] = 19.2
    assert _handback_needed(mod, "a", 20.0, 1.5, STEP_S)
    # the model is asked with the ego's speed, the getLeader gap as is, and
    # the leader's speed, decel and id
    assert mod.vehicle.follow_args[-1] == ("a", 20.0, 6.0, 10.0, 1.0, "L")


def test_step_withdraws_counts_reapplies_and_drops() -> None:
    mod = _Mod()
    mod.vehicle.leaders["a"] = ("L", 3.0)
    mod.vehicle.leaders["b"] = ("L", 50.0)
    mod.vehicle.follow.update({"a": 5.0, "b": 19.9})
    hb = _hb({"a": 18.0, "b": 18.0, "gone": 18.0})
    results = {"a": _res(20.0), "b": _res(20.0)}

    _emergency_handback_step(mod, tc, results, hb, STEP_S)
    # "a" must brake from 20 to 5 m/s: withdrawn; "b" keeps its command;
    # "gone" has left the network and is dropped
    assert mod.vehicle.calls == [("a", -1.0)]
    assert hb["in_force"] == {"b"} and set(hb["held"]) == {"a", "b"}
    assert (hb["n_vehicle_steps"], hb["n_withdrawals"], hb["vehicles"]) == (1, 1, {"a"})
    # IDM with decel 1.0 and emergencyDecel 9.0: the command reaches 1.5 m/s²
    assert hb["decel"] == {"a": 1.5, "b": 1.5}

    # still needed next step: counted, not withdrawn twice
    _emergency_handback_step(mod, tc, results, hb, STEP_S)
    assert mod.vehicle.calls == [("a", -1.0)]
    assert (hb["n_vehicle_steps"], hb["n_withdrawals"]) == (2, 1)

    # the model can brake enough again: the held command is re-applied
    mod.vehicle.follow["a"] = 19.5
    _emergency_handback_step(mod, tc, results, hb, STEP_S)
    assert mod.vehicle.calls == [("a", -1.0), ("a", 18.0)]
    assert hb["in_force"] == {"a", "b"}
    assert (hb["n_vehicle_steps"], hb["n_withdrawals"], len(hb["vehicles"])) == (2, 1, 1)


# --- the config field -----------------------------------------------------------


def test_field_is_off_by_default_and_hash_neutral() -> None:
    assert AVSpec().emergency_handback is False
    base = load_scenario("ring_sugiyama")
    d = base.model_dump(mode="json")
    d["av"]["emergency_handback"] = False
    assert config_hash(ScenarioConfig.model_validate(d)) == config_hash(base)
    d["av"]["emergency_handback"] = True
    assert config_hash(ScenarioConfig.model_validate(d)) != config_hash(base)


# --- a constructed cut-in on real SUMO -------------------------------------------


def _cut_in(tmp_path: Path, mode: str) -> tuple[int, float, dict[str, Any] | None]:
    """A slower vehicle cuts in 8 m ahead of a vehicle commanded at 20 m/s.

    Two-lane straight road. ``av`` (IDM, ``decel`` 1.67, ``emergencyDecel``
    9) is held at 20 m/s by ``setSpeed``, as the runner holds a compliant AV;
    ``cut`` drives at 12 m/s in lane 1 and is 8 m (bumper to bumper) ahead of
    ``av`` at t = 5 s, when it is sent into lane 0 under ``laneChangeMode``
    256 (refused only on an overlap). From then on, every step, ``mode``:
    ``"command"`` re-issues the command (the runner's dispatch);
    ``"handback"`` does the same and then runs ``_emergency_handback_step``;
    ``"model"`` releases ``av`` to its car-following model.

    Returns:
        (steps in which a collision with ``av`` as collider was first
        detected, ``av``'s strongest deceleration [m/s²], the handback state).
    """
    import libsumo as mod

    bundle = corridor(1500.0, lanes=2, workdir=tmp_path / "net", entry_m=100.0)
    rou = tmp_path / "cut_in.rou.xml"
    x_cut = 100.0 + 5.0 + 8.0 + (20.0 - 12.0) * 5.0
    rou.write_text(
        "<routes>\n"
        '  <vType id="idm" carFollowModel="IDM" accel="0.73" decel="1.67" tau="1.4" '
        'minGap="2.0" maxSpeed="33.3" length="5.0" speedFactor="1.0" speedDev="0" '
        f'actionStepLength="{STEP_S}"/>\n'
        f'  <route id="r" edges="{" ".join(bundle.edge_ids[1:])}"/>\n'
        '  <vehicle id="av" type="idm" route="r" depart="0" departLane="0" '
        'departPos="100" departSpeed="20"/>\n'
        '  <vehicle id="cut" type="idm" route="r" depart="0" departLane="1" '
        f'departPos="{x_cut:.3f}" departSpeed="12"/>\n'
        "</routes>\n"
    )
    mod.start(
        [
            "sumo",
            *("-n", str(bundle.net_path)),
            *("-r", str(rou)),
            *("--step-length", str(STEP_S)),
            *("--collision.action", "warn"),
            *("--time-to-teleport", "-1"),
            "--no-step-log",
            "--no-warnings",
        ]
    )
    hb: dict[str, Any] | None = _hb({}) if mode == "handback" else None
    n_collisions = 0
    decel = 0.0
    try:
        mod.simulationStep()
        for vid in ("av", "cut"):
            mod.vehicle.subscribe(vid, [tc.VAR_SPEED])
            mod.vehicle.setLaneChangeMode(vid, 0)
        mod.vehicle.setSpeed("cut", 12.0)
        mod.vehicle.setSpeed("av", 20.0)
        for _ in range(40):
            t = mod.simulation.getTime()
            if math.isclose(t, 5.0):
                mod.vehicle.setLaneChangeMode("cut", 256)
                mod.vehicle.changeLane("cut", 0, 5.0)
            if t >= 5.0:
                if mode == "model":
                    mod.vehicle.setSpeed("av", -1.0)
                else:
                    mod.vehicle.setSpeed("av", 20.0)
                    if hb is not None:
                        hb["held"]["av"] = 20.0
                        hb["in_force"].add("av")
                        results = mod.vehicle.getAllSubscriptionResults()
                        _emergency_handback_step(mod, tc, results, hb, STEP_S)
            mod.simulationStep()
            if mod.simulation.getCollidingVehiclesNumber():
                n_collisions += sum(c.collider == "av" for c in mod.simulation.getCollisions())
            decel = max(decel, -float(mod.vehicle.getAcceleration("av")))
    finally:
        mod.close()
    return n_collisions, decel, hb


@pytest.mark.integration
def test_cut_in_collides_under_a_held_command_and_not_with_the_handback(tmp_path: Path) -> None:
    n_cmd, decel_cmd, _ = _cut_in(tmp_path / "command", "command")
    n_model, decel_model, _ = _cut_in(tmp_path / "model", "model")
    n_hb, decel_hb, hb = _cut_in(tmp_path / "handback", "handback")
    # the held command brakes at exactly decel (1.67 m/s²) and runs into the
    # vehicle that cut in (SUMO 1.27.1, measured 2026-09-26: at t = 7.0 s)
    assert n_cmd >= 1
    assert decel_cmd == pytest.approx(1.67, abs=1e-6)
    # the same vehicle without the command brakes at emergencyDecel and does not
    assert n_model == 0 and decel_model == pytest.approx(9.0, abs=1e-6)
    # the handback withdraws the command for the steps that need it
    assert n_hb == 0 and decel_hb == pytest.approx(9.0, abs=1e-6)
    assert hb is not None and hb["n_withdrawals"] >= 1 and hb["vehicles"] == {"av"}


# --- run_micro ------------------------------------------------------------------


def _merge_fixture(emergency_handback: bool) -> ScenarioConfig:
    """``MERGE_OSM`` with the I-24 fleet and FollowerStopper at 10 %, 240 s.

    Mainline 0.8 veh/s on edges 100–103, an on-ramp at 0.2 veh/s joining the
    added lane of 102 (which ends at 103), an off-ramp taking 15 % from 100.
    """
    av: dict[str, Any] = {"penetration": 0.1, "compliance": 1.0, "controller": "follower_stopper"}
    if emergency_handback:
        av["emergency_handback"] = True
    return ScenarioConfig.model_validate(
        {
            "name": "wp95_merge_fixture",
            "fleet": I24_FLEET,
            "network": {
                "kind": "osm",
                "osm_file": str(MERGE_OSM),
                "corridor_edges": ["100", "101", "102", "103"],
                "inflow": [[0.0, 0.8]],
                "ramps": [
                    {
                        "kind": "on",
                        "name": "on",
                        "edges": ["200"],
                        "attach_edge": "102",
                        "inflow": [[0.0, 0.2]],
                    },
                    {
                        "kind": "off",
                        "name": "off",
                        "edges": ["201"],
                        "attach_edge": "100",
                        "exit_fraction": [[0.0, 0.15]],
                    },
                ],
            },
            "sim": {"duration_s": 240.0},
            "av": av,
            "seed": 1,
        }
    )


@pytest.mark.integration
def test_fixture_default_collides_and_the_handback_does_not(tmp_path: Path) -> None:
    seed = 2  # measured 2026-09-26: three collisions, every collider a compliant AV
    default = json.loads(run_micro(_merge_fixture(False), seed, tmp_path / "d").meta.read_text())
    on = json.loads(run_micro(_merge_fixture(True), seed, tmp_path / "h").meta.read_text())
    complied = set(default["complied_ids"])
    assert default["n_collisions"] >= 1
    assert all(c["collider"] in complied for c in default["collisions"])
    assert default["av_emergency_handback"] is None
    assert on["n_collisions"] == 0
    assert on["av_emergency_handback"]["n_withdrawals"] >= 1
    assert on["av_emergency_handback"]["n_vehicles"] >= 1


@pytest.mark.integration
def test_ring_without_an_emergency_is_unchanged(tmp_path: Path) -> None:
    """One FollowerStopper AV on the Sugiyama ring (the CI gate's damped run):
    the model never needs more than the command allows, nothing is withdrawn
    and the trajectories are the default's, row for row."""
    frames = []
    for key in (False, True):
        d = load_scenario("ring_sugiyama").model_dump(mode="json")
        d["av"] = {
            "penetration": 0.045,
            "compliance": 1.0,
            "controller": "follower_stopper",
            "emergency_handback": key,
        }
        cfg = ScenarioConfig.model_validate(d)
        paths = run_micro(cfg, cfg.seed, tmp_path / str(key))
        meta = json.loads(paths.meta.read_text())
        if key:
            assert meta["av_emergency_handback"] == {
                "n_vehicle_steps": 0,
                "n_withdrawals": 0,
                "n_vehicles": 0,
            }
        frames.append(pd.read_parquet(paths.trajectories))
    pd.testing.assert_frame_equal(frames[0], frames[1])
