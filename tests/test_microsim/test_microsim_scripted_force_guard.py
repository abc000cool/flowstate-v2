"""The scripted merge's forced-change guard (``merge_params["force_guard"]``, WP-93).

VM AF (docs/ONBOARDING_MNDOT.md §11) placed 14 of the I-94 WB reference
battery's 15 collisions on lane 1 of the two edges carrying the scripted
merges' added acceleration lanes, 217-238 m along them (McKnight Rd
178547099: 11; Hudson Rd 18207436: 3). In every one the victim is a ramp
vehicle and the collider a vehicle from upstream.

On ``tests/fixtures/mcknight_merge.osm``, the McKnight Rd merge cut from the
corridor's map, every collision is the same event (docs/WEAVE_MODEL_PLAN.md,
2026-09-26 block 3, WP-93). A ramp vehicle due to force is put under
``laneChangeMode`` 256 for the rest of its time on the lane. SUMO then
executes its open request at any gap in which the lane-1 follower's front is
clear of the follower's own ``minGap`` behind it, whatever the follower's
closing speed. The ramp vehicle has slowed for the lane's end, so it lands
2-8 m in front of a follower 10-16 m/s faster. The follower brakes at 9 m/s²
and hits it.

Under ``force_guard`` the vehicle is under mode 256 only in a step in which
each target-lane gap, less one step of closing, holds the brake gap of the
party behind at its own ``b`` (``microsim.runner._scripted_force_gap_ok``),
and under mode 512 otherwise. The key is off by default. These tests pin:

* the guard's bounds, including the traced collision's numbers;
* ``_scripted_merge_step`` on a fake SUMO, with the key off (mode 256 for
  good from the first forced step, as before) and on (the mode follows the
  guard each step, the requests unchanged);
* the fixture's compiled geometry against the corridor's;
* one fixture run with the key on: no collision, and the guard binds.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
import sumolib
import yaml
from traci import constants as tc

from flowstate_core.config import (
    SCRIPTED_MERGE_DEFAULTS,
    WEAVE_DEFAULTS,
    RampSpec,
    ScenarioConfig,
    config_hash,
)
from microsim import run_micro
from microsim.networks import osm_import
from microsim.runner import (
    LC_MODE_SCRIPTED_FORCE,
    LC_MODE_SCRIPTED_SAFE,
    NEIGHBOR_LEFT_FOLLOWERS,
    NEIGHBOR_LEFT_LEADERS,
    _scripted_force_gap_ok,
    _scripted_merge_step,
)

REPO = Path(__file__).resolve().parents[2]
MCKNIGHT_OSM = REPO / "tests" / "fixtures" / "mcknight_merge.osm"
#: The compiled piece carrying the added acceleration lane (lane 0).
ATTACH = "638519829-AddedOnRampEdge"
CORRIDOR = ("900", "638377595", "638519829", "901")
#: The corridor's ramp guessing (``scenarios/mndot_i94_wb_stpaul_weave.yaml``,
#: ``network.netconvert_extra``, less its ``--ramps.unset`` of an edge not in
#: the cut).
NETCONVERT_EXTRA = ("--ramps.guess", "--ramps.ramp-length", "250")
STEP_S = 0.5

#: The corridor's own demand at the McKnight Rd merge in minutes 50-70 of
#: ``scenarios/mndot_i94_wb_stpaul_weave.yaml`` (06:20-06:40), veh/h per 5-min
#: window. Mainline: the scenario's upstream inflow, lagged by the free-flow
#: travel to the merge (4,297.84 m at 25 m/s), thinned by the exit fractions of
#: off-ramps 18279036 and 18207390, plus the inflows of on-ramps 1077665160,
#: 18207436 and 18207653. Ramp: on-ramp 178547099's own inflow.
MCKNIGHT_MINUTES_50_70: dict[str, tuple[tuple[float, float], ...]] = {
    "mainline_vph": ((0.0, 2494.8), (300.0, 2998.5), (600.0, 3332.5), (900.0, 3445.9)),
    "ramp_vph": ((0.0, 428.0), (300.0, 401.2), (600.0, 497.3), (900.0, 493.3)),
}


def corridor_fleet() -> dict:
    """The corridor's ``fleet`` block (EIDM, the I-24 capacity population)."""
    path = REPO / "scenarios" / "mndot_i94_wb_stpaul_weave.yaml"
    return dict(yaml.safe_load(path.read_text())["fleet"])


def mcknight_config(seed: int, merge_params: dict[str, float] | None = None) -> ScenarioConfig:
    """The McKnight Rd fixture under :data:`MCKNIGHT_MINUTES_50_70`, the
    corridor's fleet, the scripted merge, 20 simulated minutes, step 0.5 s."""
    d = MCKNIGHT_MINUTES_50_70
    ramp: dict[str, Any] = {
        "kind": "on",
        "name": "on-ramp 178547099",
        "edges": ["178547099"],
        "attach_edge": "638519829",
        "inflow": [[t, q / 3600.0] for t, q in d["ramp_vph"]],
        "merge": "scripted",
    }
    if merge_params:
        ramp["merge_params"] = merge_params
    return ScenarioConfig.model_validate(
        {
            "name": "mcknight_merge",
            "fleet": corridor_fleet(),
            "network": {
                "kind": "osm",
                "osm_file": str(MCKNIGHT_OSM),
                "corridor_edges": list(CORRIDOR),
                "inflow": [[t, q / 3600.0] for t, q in d["mainline_vph"]],
                "ramps": [ramp],
                "netconvert_extra": list(NETCONVERT_EXTRA),
            },
            "sim": {"duration_s": 1200.0, "step_length_s": STEP_S, "action_step_s": STEP_S},
            "seed": seed,
        }
    )


# --- A fake SUMO for _scripted_merge_step ----------------------------------


class _Vehicle:
    """Records every TraCI write; neighbours are scripted per (vid, mode)."""

    def __init__(self, speeds: dict[str, float], decel: dict[str, float]) -> None:
        self.speeds = speeds
        self.decel = decel
        self.neighbors: dict[tuple[str, int], tuple[tuple[str, float], ...]] = {}
        self.lc_modes: dict[str, int] = {}
        self.max_speeds: dict[str, float] = {}
        self.calls: list[tuple] = []

    def getLaneChangeMode(self, vid: str) -> int:
        return self.lc_modes.get(vid, 1621)

    def getMaxSpeed(self, vid: str) -> float:
        return self.max_speeds.get(vid, 33.3)

    def getMinGap(self, vid: str) -> float:
        return 2.5

    def getDecel(self, vid: str) -> float:
        return self.decel[vid]

    def getSpeed(self, vid: str) -> float:
        return self.speeds[vid]

    def getNeighbors(self, vid: str, mode: int) -> tuple[tuple[str, float], ...]:
        return self.neighbors.get((vid, mode), ())

    def setLaneChangeMode(self, vid: str, mode: int) -> None:
        self.lc_modes[vid] = mode
        self.calls.append(("mode", vid, mode))

    def setMaxSpeed(self, vid: str, v: float) -> None:
        self.max_speeds[vid] = v

    def changeLane(self, vid: str, lane: int, dur: float) -> None:
        self.calls.append(("change", vid, lane, dur))


class _Lane:
    def getMaxSpeed(self, lane_id: str) -> float:
        return 24.59


class _Mod:
    def __init__(self, vehicle: _Vehicle) -> None:
        self.vehicle = vehicle
        self.lane = _Lane()


def _state(**params: float) -> dict[str, Any]:
    """``scripted_states`` entry as ``run_micro`` builds it (a 251 m lane)."""
    return {
        "ramp": "on-ramp 178547099",
        "edge": ATTACH,
        "lane_len_m": 251.04,
        "target_lane": f"{ATTACH}_1",
        "params": {**SCRIPTED_MERGE_DEFAULTS, **params},
        "veh": {},
        "yielding": {},
        "n_entered": 0,
        "n_changed": 0,
        "n_forced": 0,
        "n_forced_deferred": 0,
        "step_s": STEP_S,
        "waits_s": [],
    }


def _res(pos: float, v: float) -> dict[int, Any]:
    return {tc.VAR_ROAD_ID: ATTACH, tc.VAR_LANE_INDEX: 0, tc.VAR_LANEPOSITION: pos, tc.VAR_SPEED: v}


#: The traced collision (the fixture at a 6 m/s downstream boundary, seed 3,
#: t = 1,097.0 s, the step the vehicle became due to force): ramp vehicle
#: v02074 at 225.8 m and 9.27 m/s (``decel`` 1.80); lane-1 leader 25.3 m
#: ahead at 21.66 m/s; follower v00567 8.07 m behind (net of its ``minGap``)
#: at 22.19 m/s, ``decel`` 1.62. It landed half a step later 4.93 m ahead of
#: the follower's front (1.5 m net) and was hit a second later.
TRACE = {"v_ego": 9.27, "g_lead": 25.30, "v_lead": 21.66, "g_foll": 8.07, "v_foll": 22.19}
TRACE_B_EGO, TRACE_B_FOLL = 1.80, 1.62


def _trace_vehicle() -> _Vehicle:
    veh = _Vehicle(
        {"r": TRACE["v_ego"], "l": TRACE["v_lead"], "f": TRACE["v_foll"]},
        {"r": TRACE_B_EGO, "l": 1.67, "f": TRACE_B_FOLL},
    )
    veh.neighbors[("r", NEIGHBOR_LEFT_LEADERS)] = (("l", TRACE["g_lead"]),)
    veh.neighbors[("r", NEIGHBOR_LEFT_FOLLOWERS)] = (("f", TRACE["g_foll"]),)
    return veh


def _due(mod: _Mod, ss: dict[str, Any]) -> None:
    """Take ``r`` under control inside the forced zone, then step it to due."""
    _scripted_merge_step(mod, tc, ss, {"r": _res(221.0, TRACE["v_ego"])}, 0.0)
    _scripted_merge_step(
        mod, tc, ss, {"r": _res(225.8, TRACE["v_ego"])}, SCRIPTED_MERGE_DEFAULTS["force_after_s"]
    )


class TestSchema:
    def test_off_by_default_and_hash_neutral(self) -> None:
        assert SCRIPTED_MERGE_DEFAULTS["force_guard"] == 0.0
        # the weave shares the keys; its forced changes are always guarded
        assert WEAVE_DEFAULTS["force_guard"] == 0.0
        cfg = mcknight_config(3)
        assert cfg.network.ramps[0].merge_params == {}
        on = mcknight_config(3, {"force_guard": 1.0})
        assert on.network.ramps[0].merge_params == {"force_guard": 1.0}
        assert config_hash(on) != config_hash(cfg)
        # unset, the key is not part of what is hashed (policy v2)
        assert "force_guard" not in json.dumps(cfg.model_dump(mode="json", exclude_defaults=True))
        RampSpec.model_validate(
            {
                "kind": "on",
                "edges": ["178547099"],
                "attach_edge": "638519829",
                "inflow": [[0.0, 0.1]],
                "merge": "scripted",
                "merge_params": {"force_guard": 1.0},
            }
        )


class TestGuardBounds:
    def test_the_traced_collision_is_refused(self) -> None:
        assert not _scripted_force_gap_ok(
            **TRACE, b_ego=TRACE_B_EGO, b_foll=TRACE_B_FOLL, step_s=STEP_S
        )
        # it would have needed c·Δt + c²/(2 b) behind it: 6.46 + 51.52 m
        c = TRACE["v_foll"] - TRACE["v_ego"]
        need = c * STEP_S + c * c / (2.0 * TRACE_B_FOLL)
        assert need == pytest.approx(57.98, abs=0.01)
        args = {**TRACE, "g_foll": need + 0.01}
        assert _scripted_force_gap_ok(**args, b_ego=TRACE_B_EGO, b_foll=TRACE_B_FOLL, step_s=STEP_S)
        args = {**TRACE, "g_foll": need}
        assert not _scripted_force_gap_ok(
            **args, b_ego=TRACE_B_EGO, b_foll=TRACE_B_FOLL, step_s=STEP_S
        )

    def test_at_equal_speeds_it_is_the_overlap_test(self) -> None:
        """No closing on either side: any positive net gap, as SUMO's own
        mode-256 check (a follower in a stopped queue does not block)."""
        for v in (0.0, 5.0, 20.0):
            assert _scripted_force_gap_ok(v, 0.01, v, 0.01, v, 1.67, 1.67, STEP_S)
            assert not _scripted_force_gap_ok(v, 0.0, v, 1.0, v, 1.67, 1.67, STEP_S)
            assert not _scripted_force_gap_ok(v, 1.0, v, 0.0, v, 1.67, 1.67, STEP_S)
        # a slower follower or a faster leader closes nothing
        assert _scripted_force_gap_ok(10.0, 0.5, 15.0, 0.5, 5.0, 1.67, 1.67, STEP_S)

    def test_leader_side_and_missing_neighbours(self) -> None:
        # the changer closing on a slower leader, by the changer's own b
        c = 6.0
        need = c * STEP_S + c * c / (2.0 * 2.0)
        assert _scripted_force_gap_ok(10.0, need + 0.01, 4.0, math.inf, math.nan, 2.0, None, STEP_S)
        assert not _scripted_force_gap_ok(10.0, need, 4.0, math.inf, math.nan, 2.0, None, STEP_S)
        # nobody on the target lane
        assert _scripted_force_gap_ok(0.0, math.inf, math.nan, math.inf, math.nan, 1.67, None, 0.5)
        # a follower's b is its own: a harder-braking follower needs less room
        assert _scripted_force_gap_ok(0.0, math.inf, math.nan, 20.0, 10.0, 1.67, 4.0, STEP_S)
        assert not _scripted_force_gap_ok(0.0, math.inf, math.nan, 20.0, 10.0, 1.67, 1.67, STEP_S)


class TestStep:
    def test_off_forces_under_256_for_good(self) -> None:
        """The key off: the traced state gets mode 256 at its first forced
        step and keeps it (call for call the step before WP-93)."""
        veh = _trace_vehicle()
        mod = _Mod(veh)
        ss = _state()
        _due(mod, ss)
        assert veh.lc_modes["r"] == LC_MODE_SCRIPTED_FORCE
        assert ss["veh"]["r"]["forced"] and ss["n_forced_deferred"] == 0
        assert ("change", "r", 1, SCRIPTED_MERGE_DEFAULTS["change_duration_s"]) in veh.calls
        veh.calls.clear()
        _scripted_merge_step(mod, tc, ss, {"r": _res(226.5, TRACE["v_ego"])}, 4.5)
        assert not [c for c in veh.calls if c[0] == "mode"]

    def test_guard_holds_the_traced_state_under_512(self) -> None:
        """The key on: the same state stays under mode 512 (SUMO's own gap
        check), its request is made as before, the step is counted."""
        veh = _trace_vehicle()
        mod = _Mod(veh)
        ss = _state(force_guard=1.0)
        _due(mod, ss)
        assert veh.lc_modes["r"] == LC_MODE_SCRIPTED_SAFE
        assert ("mode", "r", LC_MODE_SCRIPTED_FORCE) not in veh.calls
        assert ("change", "r", 1, SCRIPTED_MERGE_DEFAULTS["change_duration_s"]) in veh.calls
        assert not ss["veh"]["r"]["forced"] and ss["n_forced_deferred"] == 1

    def test_guard_sets_the_mode_each_step(self) -> None:
        """Room behind: mode 256 and the vehicle counts as forced; the
        follower closes up the next step: back to 512 while its request
        stays open (no new request inside ``change_duration_s``)."""
        veh = _trace_vehicle()
        veh.neighbors[("r", NEIGHBOR_LEFT_FOLLOWERS)] = (("f", 60.0),)
        mod = _Mod(veh)
        ss = _state(force_guard=1.0)
        _due(mod, ss)
        assert veh.lc_modes["r"] == LC_MODE_SCRIPTED_FORCE
        assert ss["veh"]["r"]["forced"] and ss["n_forced_deferred"] == 0
        veh.calls.clear()
        veh.neighbors[("r", NEIGHBOR_LEFT_FOLLOWERS)] = (("f", TRACE["g_foll"]),)
        _scripted_merge_step(mod, tc, ss, {"r": _res(226.5, TRACE["v_ego"])}, 4.5)
        assert veh.calls == [("mode", "r", LC_MODE_SCRIPTED_SAFE)]
        assert ss["n_forced_deferred"] == 1
        # the mode is written only when it changes
        veh.calls.clear()
        _scripted_merge_step(mod, tc, ss, {"r": _res(227.0, TRACE["v_ego"])}, 5.0)
        assert not [c for c in veh.calls if c[0] == "mode"]
        assert ss["n_forced_deferred"] == 2


class TestFixture:
    def test_compiles_to_the_corridor_geometry(self, tmp_path: Path) -> None:
        """The cut compiles to the corridor's edges (docs/WEAVE_MODEL_PLAN.md,
        WP-93: netconvert as run_micro calls it on the corridor's own map):
        the added lane is lane 0 of a 251.04 m four-lane piece, with no
        successor; lanes 1-3 feed lanes 0-2 of 638519829 (631.99 m); the
        ramp's one lane (290.64 m, 22.22 m/s) feeds lane 0."""
        bundle = osm_import(
            osm_file=MCKNIGHT_OSM,
            corridor_edges=CORRIDOR,
            workdir=tmp_path,
            keep_edges=("178547099",),
            netconvert_extra=NETCONVERT_EXTRA,
        )
        net = sumolib.net.readNet(str(bundle.net_path))
        piece = net.getEdge(ATTACH)
        assert piece.getLaneNumber() == 4
        assert piece.getLength() == pytest.approx(251.04, abs=0.01)
        lanes = piece.getLanes()
        assert lanes[0].getOutgoing() == []
        assert [
            [(c.getTo().getID(), c.getToLane().getIndex()) for c in ln.getOutgoing()]
            for ln in lanes[1:]
        ] == [[("638519829", 0)], [("638519829", 1)], [("638519829", 2)]]
        assert all(ln.getSpeed() == pytest.approx(24.59, abs=0.01) for ln in lanes)
        assert net.getEdge("638519829").getLength() == pytest.approx(631.99, abs=0.01)
        assert net.getEdge("638377595").getLength() == pytest.approx(331.49, abs=0.01)
        ramp = net.getEdge("178547099")
        assert ramp.getLaneNumber() == 1
        assert ramp.getLength() == pytest.approx(290.64, abs=0.01)
        assert ramp.getSpeed() == pytest.approx(22.22, abs=0.01)
        ((conn,),) = [ramp.getLanes()[0].getOutgoing()]
        assert (conn.getTo().getID(), conn.getToLane().getIndex()) == (ATTACH, 0)

    def test_run_with_the_guard(self, tmp_path: Path) -> None:
        """Seed 3 with the key on: no collision, the guard binds, and the
        merge keeps up with the ramp (docs/WEAVE_MODEL_PLAN.md, WP-93)."""
        cfg = mcknight_config(3, {"force_guard": 1.0})
        meta = json.loads(run_micro(cfg, 3, tmp_path).meta.read_text())
        (sm,) = meta["scripted_merges"]
        assert sm["params"]["force_guard"] == 1.0
        assert meta["n_collisions"] == 0, meta["collisions"]
        assert sm["n_forced_deferred"] > 0, sm
        assert sm["n_changed"] + sm["n_unfinished"] == sm["n_entered"]
        assert sm["n_unfinished"] <= 0.1 * sm["n_entered"], sm
        (ramp,) = meta["ramps"]
        assert ramp["n_departed"] == ramp["n_planned"], ramp
