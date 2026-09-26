"""The AV command path's two WP-95 side findings, and their options (WP-96).

docs/I24_STRATEGIES.md, section of 2026-09-26 (WP-96). Every compliant AV is
driven by ``vehicle.setSpeed`` on corridor edges only (CLAUDE.md §3.3), and a
``setSpeed`` target is held until ``setSpeed(-1)``:

* (a) an AV that leaves the corridor by an off-ramp keeps its last command
  until it arrives. ``AVSpec.release_off_corridor`` releases it;
* (b) ``vehicle.getLeader`` returns the gap net of the ego's ``minGap``, and
  ``microsim.runner._leader_obs`` read a negative value as "no leader", so at
  a bumper gap below the AV's own ``s0`` FollowerStopper was told the road was
  free and commanded ``U``. ``AVSpec.observe_close_leader`` reports the leader.

Both are off by default and hash-neutral when off; the runner counts both
defects either way (``meta.json["av_off_corridor"]``,
``meta.json["av_close_leader"]``) without a TraCI call. These tests pin:

* the config fields: off by default, hash-neutral when off;
* ``_leader_obs`` on a fake SUMO, and what each controller commands from the
  two readings;
* ``_off_corridor_step`` on a fake SUMO: count (off), release (on), drop,
  leave internal edges and merge-model vehicles alone, clear the handback;
* a constructed case of each on real SUMO: (a) an AV commanded to stop just
  before ``tests/fixtures/merge.osm``'s off-ramp stops on the ramp for good
  under the default and arrives with the release; (b) a faster vehicle
  changes into the lane 2.5 m ahead of a commanded AV whose ``s0`` is 3 m:
  the default reads no leader, FollowerStopper commands ``U`` and the AV
  keeps its model's safe speed; the option reads the leader, commands 0 and
  the AV brakes at the command's bound;
* ``run_micro`` on the interchange fixture (both counted, both acted on) and
  on the Sugiyama ring (nothing to act on: the run is the default's).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from traci import constants as tc

from controllers.follower_stopper import follower_stopper
from controllers.follower_stopper_capacity import follower_stopper_capacity
from controllers.jad import jad
from controllers.pi_saturation import pi_saturation
from controllers.registry import default_params
from flowstate_core.config import AVSpec, ScenarioConfig, config_hash
from flowstate_core.controller_types import ControllerObs
from microsim import load_scenario, run_micro
from microsim.networks import corridor, osm_import
from microsim.runner import LEADER_LOOKAHEAD_M, _leader_obs, _off_corridor_step

REPO = Path(__file__).resolve().parents[2]
MERGE_OSM = REPO / "tests" / "fixtures" / "merge.osm"
STEP_S = 0.5

#: The I-24 strategy sweep's fleet, as in ``test_microsim_emergency_handback.py``.
I24_FLEET: dict[str, Any] = {
    "model": "IDM",
    "heterogeneity_frac": 0.12,
    "idm_calibration": "artifacts/idm_i24_capacity.json",
    "lc_strategic": 5.0,
    "lc_strategic_ramp": 1.0,
    "lc_keep_right": 0.0,
}


# --- the config fields ----------------------------------------------------------


@pytest.mark.parametrize("field", ["release_off_corridor", "observe_close_leader"])
def test_fields_are_off_by_default_and_hash_neutral(field: str) -> None:
    assert getattr(AVSpec(), field) is False
    base = load_scenario("ring_sugiyama")
    d = base.model_dump(mode="json")
    d["av"][field] = False
    assert config_hash(ScenarioConfig.model_validate(d)) == config_hash(base)
    d["av"][field] = True
    assert config_hash(ScenarioConfig.model_validate(d)) != config_hash(base)


# --- (b) the leader observation --------------------------------------------------


class _LeaderVehicle:
    """``getLeader`` returns a scripted value; records ``getSpeed`` calls."""

    def __init__(self, lead: Any) -> None:
        self.lead = lead
        self.speed_calls: list[str] = []

    def getLeader(self, vid: str, dist: float) -> Any:
        assert dist == LEADER_LOOKAHEAD_M
        return self.lead

    def getSpeed(self, vid: str) -> float:
        self.speed_calls.append(vid)
        return 8.0


class _LeaderMod:
    def __init__(self, lead: Any) -> None:
        self.vehicle = _LeaderVehicle(lead)


def test_leader_obs_without_a_leader() -> None:
    for lead in (None, ("", -1.0)):
        for close in (False, True):
            gap, v_l, within = _leader_obs(_LeaderMod(lead), "av", 2.0, close_leader=close)
            assert math.isinf(gap) and math.isnan(v_l) and within is False


def test_leader_obs_at_or_beyond_s0_is_unchanged() -> None:
    for close in (False, True):
        mod = _LeaderMod(("L", 3.0))
        assert _leader_obs(mod, "av", 2.0, close_leader=close) == (5.0, 8.0, False)
        mod = _LeaderMod(("L", 0.0))  # bumper gap exactly s0
        assert _leader_obs(mod, "av", 2.0, close_leader=close) == (2.0, 8.0, False)


def test_leader_obs_within_s0_default_reads_no_leader() -> None:
    # getLeader's -0.75 m is a bumper gap of 1.25 m for an s0 of 2 m (the
    # WP-95 harness, one step before contact)
    mod = _LeaderMod(("L", -0.75))
    gap, v_l, within = _leader_obs(mod, "av", 2.0)
    assert math.isinf(gap) and math.isnan(v_l) and within is True
    # no further TraCI call on the default path, as before WP-96
    assert mod.vehicle.speed_calls == []


def test_leader_obs_within_s0_with_the_option_reports_the_leader() -> None:
    mod = _LeaderMod(("L", -0.75))
    assert _leader_obs(mod, "av", 2.0, close_leader=True) == (1.25, 8.0, True)
    assert mod.vehicle.speed_calls == ["L"]
    # an overlap (the vehicles are in contact) is floored at 0 m
    assert _leader_obs(_LeaderMod(("L", -5.0)), "av", 2.0, close_leader=True) == (0.0, 8.0, True)


def _obs(gap: float, v_leader: float) -> ControllerObs:
    return ControllerObs(t=100.0, dt=STEP_S, v=10.0, gap=gap, v_leader=v_leader, v_ref=20.0)


def test_what_each_controller_commands_from_the_two_readings() -> None:
    """Bumper gap 1.25 m, ego 10 m/s, leader 8 m/s, U = 20 m/s."""
    free, true = _obs(math.inf, math.nan), _obs(1.25, 8.0)

    def cmd(fn: Callable[..., Any], name: str, obs: ControllerObs) -> float:
        return float(fn(obs, default_params(name), {})[0])

    # FollowerStopper: the safe region (U) against region 1 (stop)
    assert cmd(follower_stopper, "follower_stopper", free) == 20.0
    assert cmd(follower_stopper, "follower_stopper", true) == 0.0
    assert cmd(follower_stopper_capacity, "follower_stopper_capacity", free) == 20.0
    assert cmd(follower_stopper_capacity, "follower_stopper_capacity", true) == 0.0
    # PI with saturation (fresh memory: U = v, previous command = v): half
    # way to U + v_catch against the leader's speed (alpha = 0, beta = 1)
    assert cmd(pi_saturation, "pi_saturation", free) == pytest.approx(0.5 * (10.0 + 1.0) + 5.0)
    assert cmd(pi_saturation, "pi_saturation", true) == pytest.approx(8.0)
    # JAD does not read the leader
    assert cmd(jad, "jad", free) == cmd(jad, "jad", true)


# --- (a) the off-corridor pass on a fake SUMO -------------------------------------


class _SpeedVehicle:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def setSpeed(self, vid: str, v: float) -> None:
        self.calls.append((vid, v))


class _SpeedMod:
    def __init__(self) -> None:
        self.vehicle = _SpeedVehicle()


def _oc(release: bool, commanded: set[str]) -> dict[str, Any]:
    """Off-corridor state as ``run_micro`` builds it, after a dispatch of ``commanded``."""
    return {
        "release": release,
        "commanded": set(commanded),
        "left": set(),
        "n_vehicle_steps": 0,
        "n_released": 0,
    }


CORRIDOR = {"100", "101", "102"}
#: on the corridor, on an internal junction lane, on the off-ramp (twice),
#: and one that has left the network
RESULTS = {
    "on": {tc.VAR_ROAD_ID: "101"},
    "junction": {tc.VAR_ROAD_ID: ":J2_0"},
    "ramp": {tc.VAR_ROAD_ID: "201"},
    "merging": {tc.VAR_ROAD_ID: "201"},
}


def test_off_corridor_default_counts_and_writes_nothing() -> None:
    mod = _SpeedMod()
    oc = _oc(False, {"on", "junction", "ramp", "merging", "gone"})
    for _ in range(3):
        _off_corridor_step(mod, tc, RESULTS, oc, CORRIDOR, lambda v: False, None)
    assert mod.vehicle.calls == []
    assert oc["commanded"] == {"on", "junction", "ramp", "merging"}  # "gone" dropped
    assert oc["left"] == {"ramp", "merging"}
    assert (oc["n_vehicle_steps"], oc["n_released"]) == (6, 0)


def test_off_corridor_release_writes_once_and_clears_the_handback() -> None:
    mod = _SpeedMod()
    oc = _oc(True, {"on", "junction", "ramp", "merging", "gone"})
    hb: dict[str, Any] = {"held": {"ramp": 4.0, "on": 9.0}, "in_force": {"ramp", "on"}}
    held_by_merge = {"merging"}
    for _ in range(2):
        _off_corridor_step(mod, tc, RESULTS, oc, CORRIDOR, held_by_merge.__contains__, hb)
    # "ramp" released once and forgotten; "merging" left to its merge model
    assert mod.vehicle.calls == [("ramp", -1.0)]
    assert oc["commanded"] == {"on", "junction", "merging"}
    assert hb == {"held": {"on": 9.0}, "in_force": {"on"}}
    assert (oc["n_vehicle_steps"], oc["n_released"]) == (0, 1)
    # the merge model lets go: released in the next step
    held_by_merge.clear()
    _off_corridor_step(mod, tc, RESULTS, oc, CORRIDOR, held_by_merge.__contains__, hb)
    assert mod.vehicle.calls == [("ramp", -1.0), ("merging", -1.0)]
    assert oc["left"] == {"ramp", "merging"} and oc["n_released"] == 2


# --- (a) constructed on real SUMO: a command held onto the off-ramp ----------------


def _exit_with_a_held_command(tmp_path: Path, last_cmd: float, release: bool) -> dict[str, Any]:
    """One AV leaves ``MERGE_OSM`` by its off-ramp 201, commanded as the runner commands.

    The AV (IDM, ``decel`` 1.67) drives edge 100 commanded at 15 m/s; from
    60 m before the diverge it is commanded ``last_cmd`` (as FollowerStopper
    commands in a queue), every step while it is on a corridor edge (100–103).
    Then, every step, ``_off_corridor_step`` with ``release``.

    Returns:
        Its arrival time (None if still in the network after 300 s), its
        speed and edge at the end, and the pass's counters.
    """
    import libsumo as mod

    corridor_edges = ["100", "101", "102", "103"]
    bundle = osm_import(
        osm_file=MERGE_OSM,
        corridor_edges=corridor_edges,
        keep_edges=["200", "201"],
        workdir=tmp_path / "net",
    )
    rou = tmp_path / "exit.rou.xml"
    rou.write_text(
        "<routes>\n"
        '  <vType id="idm" carFollowModel="IDM" accel="0.73" decel="1.67" tau="1.4" '
        'minGap="2.0" maxSpeed="33.3" length="5.0" speedFactor="1.0" speedDev="0" '
        f'actionStepLength="{STEP_S}"/>\n'
        '  <vehicle id="av" type="idm" depart="0" departPos="0" departSpeed="15">\n'
        '    <route edges="100 201"/>\n'
        "  </vehicle>\n"
        "</routes>\n"
    )
    mod.start(
        [
            "sumo",
            *("-n", str(bundle.net_path)),
            *("-r", str(rou)),
            *("--step-length", str(STEP_S)),
            *("--time-to-teleport", "-1"),
            "--no-step-log",
            "--no-warnings",
        ]
    )
    oc = _oc(release, set())
    arrived: float | None = None
    last: tuple[float, str] = (math.nan, "")
    try:
        mod.simulationStep()
        mod.vehicle.subscribe("av", [tc.VAR_ROAD_ID, tc.VAR_SPEED, tc.VAR_LANEPOSITION])
        len_100 = float(mod.lane.getLength("100_0"))
        for _ in range(600):
            results = mod.vehicle.getAllSubscriptionResults()
            if "av" not in results:
                arrived = float(mod.simulation.getTime())
                break
            res = results["av"]
            last = (float(res[tc.VAR_SPEED]), str(res[tc.VAR_ROAD_ID]))
            if res[tc.VAR_ROAD_ID] in bundle.edge_ids:
                near = res[tc.VAR_LANEPOSITION] >= len_100 - 60.0
                mod.vehicle.setSpeed("av", last_cmd if near else 15.0)
                oc["commanded"].add("av")
            _off_corridor_step(mod, tc, results, oc, bundle.edge_ids, lambda v: False, None)
            mod.simulationStep()
    finally:
        mod.close()
    return {"arrived": arrived, "v_end": last[0], "edge_end": last[1], "oc": oc}


@pytest.mark.integration
def test_command_held_onto_the_off_ramp_and_the_release(tmp_path: Path) -> None:
    # commanded to stop just before the diverge, the AV (b = 1.67 m/s²)
    # cannot stop in 60 m from 15 m/s and rolls onto the ramp; the held 0
    # then stops it there for good (no teleporting)
    held = _exit_with_a_held_command(tmp_path / "held0", 0.0, release=False)
    assert held["arrived"] is None
    assert held["edge_end"] == "201" and held["v_end"] == 0.0
    assert held["oc"]["left"] == {"av"} and held["oc"]["n_released"] == 0
    # every step since it left the corridor (523 on macOS, 2026-09-26)
    assert held["oc"]["n_vehicle_steps"] > 400
    released = _exit_with_a_held_command(tmp_path / "rel0", 0.0, release=True)
    assert released["arrived"] is not None
    assert released["oc"]["n_released"] == 1 and released["oc"]["n_vehicle_steps"] == 0
    # a crawl command (4 m/s) is kept for the whole ramp; released, the AV
    # drives the ramp at its own pace
    crawl = _exit_with_a_held_command(tmp_path / "held4", 4.0, release=False)
    crawl_rel = _exit_with_a_held_command(tmp_path / "rel4", 4.0, release=True)
    assert crawl["arrived"] is not None and crawl_rel["arrived"] is not None
    # 201.5 s against 78.5 s on macOS, 2026-09-26
    assert crawl["arrived"] - crawl_rel["arrived"] > 60.0


# --- (b) constructed on real SUMO: a vehicle changes in within s0 ------------------


def _close_cut_in(tmp_path: Path, observe: bool) -> list[dict[str, float]]:
    """A faster vehicle changes into the lane 2.5 m ahead of a commanded AV whose s0 is 3 m.

    Two-lane straight road. ``av`` (IDM, ``minGap`` 3 m, ``decel`` 1.67) is
    held at 3 m/s in lane 0; ``cut`` is held at 8 m/s in lane 1 and overtakes
    it. At t = 10 s ``cut`` is sent into lane 0 under ``laneChangeMode`` 0 (a
    forced change: SUMO's own modes wait until the gap net of ``minGap`` is
    non-negative) and lands 2.5 m (bumper to bumper) ahead of ``av``; from
    t = 10.5 s ``cut`` drives on its model and ``av`` is commanded every step
    as the runner commands it: ``_leader_obs`` (``close_leader=observe``),
    FollowerStopper with ``U`` = 20 m/s, ``setSpeed``.

    Returns:
        One record per dispatch: the observation's gap, whether the leader
        was within ``s0``, the command, the AV's speed before and after the step.
    """
    import libsumo as mod

    bundle = corridor(1500.0, lanes=2, workdir=tmp_path / "net", entry_m=100.0)
    rou = tmp_path / "cut.rou.xml"
    rou.write_text(
        "<routes>\n"
        '  <vType id="idm" carFollowModel="IDM" accel="0.73" decel="1.67" tau="1.4" '
        'minGap="3.0" maxSpeed="33.3" length="5.0" speedFactor="1.0" speedDev="0" '
        f'actionStepLength="{STEP_S}"/>\n'
        f'  <route id="r" edges="{" ".join(bundle.edge_ids[1:])}"/>\n'
        '  <vehicle id="av" type="idm" route="r" depart="0" departLane="0" '
        'departPos="100" departSpeed="3"/>\n'
        '  <vehicle id="cut" type="idm" route="r" depart="0" departLane="1" '
        'departPos="57.5" departSpeed="8"/>\n'
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
    params = default_params("follower_stopper")
    out: list[dict[str, float]] = []
    try:
        mod.simulationStep()
        for vid in ("av", "cut"):
            mod.vehicle.setLaneChangeMode(vid, 0)
        for _ in range(30):
            t = float(mod.simulation.getTime())
            if t < 10.5:
                mod.vehicle.setSpeed("cut", 8.0)
                mod.vehicle.setSpeed("av", 3.0)
                if math.isclose(t, 10.0):
                    mod.vehicle.changeLane("cut", 0, 5.0)
            else:
                if math.isclose(t, 10.5):
                    mod.vehicle.setSpeed("cut", -1.0)
                v = float(mod.vehicle.getSpeed("av"))
                gap, v_l, within = _leader_obs(mod, "av", 3.0, close_leader=observe)
                obs = ControllerObs(t=t, dt=STEP_S, v=v, gap=gap, v_leader=v_l, v_ref=20.0)
                v_cmd = max(follower_stopper(obs, params, {})[0], 0.0)
                mod.vehicle.setSpeed("av", v_cmd)
                out.append({"t": t, "gap": gap, "within": within, "cmd": v_cmd, "v": v})
            mod.simulationStep()
            if out and "v_next" not in out[-1]:
                out[-1]["v_next"] = float(mod.vehicle.getSpeed("av"))
    finally:
        mod.close()
    return out


@pytest.mark.integration
def test_close_cut_in_default_reads_no_leader_and_the_option_stops(tmp_path: Path) -> None:
    default = _close_cut_in(tmp_path / "default", observe=False)
    observed = _close_cut_in(tmp_path / "observe", observe=True)
    # the first dispatch, with the new leader 2.5 m ahead (s0 = 3 m)
    d0, o0 = default[0], observed[0]
    assert d0["within"] and o0["within"]
    assert d0["t"] == o0["t"] == 10.5 and d0["v"] == o0["v"] == pytest.approx(3.0)
    # default: "no leader", FollowerStopper's safe region, U
    assert math.isinf(d0["gap"]) and d0["cmd"] == 20.0
    # option: the bumper gap, region 1, a stop command
    assert o0["gap"] == pytest.approx(2.5, abs=1e-6) and o0["cmd"] == 0.0
    # the stop command brakes at the command's bound (IDM: max(1.67, 1.5));
    # the default leaves the AV at its model's safe speed behind a leader
    # pulling away, which is above that bound (SUMO 1.27.1, measured
    # 2026-09-26: 2.84 against 2.165 m/s)
    assert o0["v_next"] == pytest.approx(3.0 - 1.67 * STEP_S, abs=1e-9)
    assert d0["v_next"] > o0["v_next"] + 0.5


# --- run_micro --------------------------------------------------------------------


def _merge_fixture(**av_extra: bool) -> ScenarioConfig:
    """``MERGE_OSM`` with the I-24 fleet and FollowerStopper at 10 %, 240 s
    (``test_microsim_emergency_handback.py``'s fixture)."""
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
            "av": {
                "penetration": 0.1,
                "compliance": 1.0,
                "controller": "follower_stopper",
                **av_extra,
            },
            "seed": 1,
        }
    )


@pytest.mark.integration
def test_fixture_counts_both_by_default_and_acts_with_the_options(tmp_path: Path) -> None:
    seed = 2  # measured 2026-09-26: see the dated section
    default = json.loads(run_micro(_merge_fixture(), seed, tmp_path / "d").meta.read_text())
    both = json.loads(
        run_micro(
            _merge_fixture(release_off_corridor=True, observe_close_leader=True),
            seed,
            tmp_path / "b",
        ).meta.read_text()
    )
    off, close = default["av_off_corridor"], default["av_close_leader"]
    assert off["release"] is False and off["n_released"] == 0
    assert off["n_vehicles"] >= 1 and off["n_vehicle_steps"] >= 1
    assert close["observed"] is False and close["n_vehicle_steps"] >= 1
    off_b, close_b = both["av_off_corridor"], both["av_close_leader"]
    assert off_b["release"] is True and off_b["n_vehicle_steps"] == 0
    assert off_b["n_released"] == off_b["n_vehicles"] >= 1
    assert close_b["observed"] is True


@pytest.mark.integration
def test_ring_has_nothing_to_act_on(tmp_path: Path) -> None:
    """One FollowerStopper AV on the Sugiyama ring (the CI gate's damped run):
    no edge off the corridor and no leader within s0, so both options leave
    the run row for row as the default's."""
    frames = []
    for key in (False, True):
        d = load_scenario("ring_sugiyama").model_dump(mode="json")
        d["av"] = {
            "penetration": 0.045,
            "compliance": 1.0,
            "controller": "follower_stopper",
            "release_off_corridor": key,
            "observe_close_leader": key,
        }
        cfg = ScenarioConfig.model_validate(d)
        paths = run_micro(cfg, cfg.seed, tmp_path / str(key))
        meta = json.loads(paths.meta.read_text())
        assert meta["av_off_corridor"] == {
            "release": key,
            "n_vehicles": 0,
            "n_vehicle_steps": 0,
            "n_released": 0,
        }
        assert meta["av_close_leader"] == {"observed": key, "n_vehicle_steps": 0, "n_vehicles": 0}
        frames.append(pd.read_parquet(paths.trajectories))
    pd.testing.assert_frame_equal(frames[0], frames[1])
