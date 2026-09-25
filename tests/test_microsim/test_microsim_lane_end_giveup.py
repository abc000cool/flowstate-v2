"""The lane-end give-up at every diverge (``OSMNetwork.lane_end_giveup_m``, WP-71).

VM T (docs/ONBOARDING_MNDOT.md §11) read every vehicle's route at the I-94 WB
lock at the T.H.61 → 18207912 gore, the two-lane weave the weave model does
not cover (WP-66). The frontmost vehicle was an exiter bound for 18207912,
held by SUMO at the end of through lane 2. T.H.61 entrants bound for the
corridor's end stood at the end of the exit-only lanes 0-1. Each needs the
other's lane and nothing frees either.

The rule (``microsim.runner._lane_end_step``) gives such a vehicle up to its
lane's own continuation. It acts on a vehicle that is halted, the front of
its lane, within the configured distance of the lane's end, and on a lane its
route does not continue on, when SUMO reports the change toward its route
blocked. An exiter on a through lane drives on to the corridor's end; a
vehicle bound elsewhere on an exit-only lane takes the exit.

No fixture reproduces the lock (docs/WEAVE_MODEL_PLAN.md, 2026-09-25 block 3,
WP-71). The T.H.61 fixture was run under three demand windows at 16 seeds:
the corridor's 06:30-07:00 movements, the lock window's, and the two peaks in
sequence. No minute reads 0.0 m/s at the gore. So the rule is tested on:

* its diverge map, on a minimal net and on the T.H.61 fixture;
* its trigger and each of its conditions, on a fake SUMO;
* the crossing pair built by hand on a two-lane diverge. With the rule off
  the pair stands; with it on each vehicle drives on in the lane it is in,
  without a collision.
* one run of the T.H.61 fixture with the rule on, whose bookkeeping (meta,
  ``vehicles.parquet``) is checked against the vehicles it moved.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import sumolib
from traci import constants as tc

from flowstate_core.config import OSMNetwork, RampSpec, ScenarioConfig, config_hash
from microsim import run_micro
from microsim.networks import _netconvert, osm_import
from microsim.runner import (
    DESTINATION_CORRIDOR_END,
    HALTING_SPEED_MS,
    _commanded_by_runner,
    _lane_end_diverges,
    _lane_end_meta,
    _lane_end_step,
)
from tests.test_microsim.test_microsim_th61_lane_end import TH61_EDGE, th61_config
from validation.vehicles import read_vehicles

#: The distance measured (``OSMNetwork.lane_end_giveup_m``'s docstring: one
#: vehicle's room, 5 m + the corridor population's mean minimum gap 2.53 m).
GIVEUP_M = 7.5

#: The seeds :meth:`TestRun.test_on_the_th61_fixture` tries in turn until
#: the rule acts (docs/WEAVE_MODEL_PLAN.md, WP-85: on macOS it acts at four
#: of these six, not at 7 or 10; on Linux not at 5).
TH61_RULE_SEEDS = (5, 6, 7, 8, 9, 10)

#: The T.H.61 fixture's two peaks in sequence (docs/WEAVE_MODEL_PLAN.md,
#: WP-71, "queued"). First the corridor's 06:30-06:45 steps, the through peak
#: that built its queue at the stretch. Then its 07:35-07:50 steps, the
#: exit-fraction peak VM T's front row arrived in. Each is the movements at
#: the stretch propagated from ``scenarios/mndot_i94_wb_stpaul_weave.yaml``
#: as ``TH61_DEMAND_0630`` is (the upstream inflow through the ten ramps
#: before the entrance; on-ramp 53062592's inflow; off-ramp 18207912's exit
#: fraction), shifted to t = 0 on an empty network.
TH61_DEMAND_QUEUED: dict[str, tuple[tuple[float, float], ...]] = {
    "mainline_vph": (
        (0.0, 4541.3),
        (300.0, 4556.0),
        (600.0, 4470.4),
        (900.0, 2704.9),
        (1200.0, 2306.7),
        (1500.0, 2228.4),
    ),
    "entrance_vph": (
        (0.0, 1788.0),
        (300.0, 1676.0),
        (600.0, 1750.7),
        (900.0, 1894.7),
        (1200.0, 1581.3),
        (1500.0, 1686.7),
    ),
    "exit_fraction": (
        (0.0, 0.2115),
        (300.0, 0.2221),
        (600.0, 0.4302),
        (900.0, 0.5009),
        (1200.0, 0.3718),
        (1500.0, 0.4262),
    ),
}


def th61_queued_config(
    seed: int, lane_end_giveup_m: float = 0.0, duration_s: float = 1800.0
) -> ScenarioConfig:
    """WP-66's T.H.61 fixture (left drop, ``lane_change``) under
    :data:`TH61_DEMAND_QUEUED`, the rule at ``lane_end_giveup_m``."""
    raw = th61_config(seed, duration_s=duration_s).model_dump(mode="json")
    d = TH61_DEMAND_QUEUED
    raw["name"] = "th61_lane_end_queued"
    raw["network"]["inflow"] = [[t, q / 3600.0] for t, q in d["mainline_vph"]]
    raw["network"]["ramps"][0]["inflow"] = [[t, q / 3600.0] for t, q in d["entrance_vph"]]
    raw["network"]["ramps"][1]["exit_fraction"] = [list(s) for s in d["exit_fraction"]]
    if lane_end_giveup_m > 0.0:
        raw["network"]["lane_end_giveup_m"] = lane_end_giveup_m
    return ScenarioConfig.model_validate(raw)


# --- the minimal diverge: A (2 lanes) -> B (lane 1, through) / E (lane 0, exit)


def diverge_net(workdir: Path) -> Path:
    """A 2-lane edge ``A`` whose lane 0 leads only to the exit ``E`` and lane
    1 only on to ``B`` (explicit connections, no internal links)."""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "d.nod.xml").write_text(
        '<nodes><node id="n0" x="0" y="0"/><node id="n1" x="300" y="0"/>'
        '<node id="n2" x="600" y="0"/><node id="n3" x="600" y="-80"/></nodes>'
    )
    (workdir / "d.edg.xml").write_text(
        '<edges><edge id="A" from="n0" to="n1" numLanes="2" speed="25"/>'
        '<edge id="B" from="n1" to="n2" numLanes="1" speed="25"/>'
        '<edge id="E" from="n1" to="n3" numLanes="1" speed="20"/></edges>'
    )
    (workdir / "d.con.xml").write_text(
        '<connections><connection from="A" to="E" fromLane="0" toLane="0"/>'
        '<connection from="A" to="B" fromLane="1" toLane="0"/></connections>'
    )
    net = workdir / "d.net.xml"
    _netconvert(
        [
            *("--node-files", str(workdir / "d.nod.xml")),
            *("--edge-files", str(workdir / "d.edg.xml")),
            *("--connection-files", str(workdir / "d.con.xml")),
            *("-o", str(net)),
            "--no-internal-links",
            "--no-turnarounds",
        ]
    )
    return net


EXIT_RAMP = RampSpec(
    kind="off", edges=["E"], attach_edge="A", exit_fraction=[(0.0, 0.5)], name="exit"
)


def _state(diverges: list[dict[str, Any]], distance_m: float = GIVEUP_M) -> dict[str, Any]:
    return {
        "distance_m": distance_m,
        "diverges": diverges,
        "by_edge": {d["edge"]: d for d in diverges},
        "skipped_edges": frozenset(),
    }


class TestConfig:
    def test_off_by_default_and_hash_neutral(self) -> None:
        cfg = th61_queued_config(3)
        assert isinstance(cfg.network, OSMNetwork)
        assert cfg.network.lane_end_giveup_m == 0.0
        raw = cfg.model_dump(mode="json")
        raw["network"]["lane_end_giveup_m"] = 0.0
        assert config_hash(ScenarioConfig.model_validate(raw)) == config_hash(cfg)
        assert config_hash(th61_queued_config(3, GIVEUP_M)) != config_hash(cfg)

    @pytest.mark.parametrize("bad", [-1.0, 50.5])
    def test_bounds(self, bad: float) -> None:
        raw = th61_queued_config(3).model_dump(mode="json")
        raw["network"]["lane_end_giveup_m"] = bad
        with pytest.raises(ValueError, match="lane_end_giveup_m"):
            ScenarioConfig.model_validate(raw)


@pytest.mark.integration
class TestDiverges:
    def test_minimal_diverge(self, tmp_path: Path) -> None:
        net = sumolib.net.readNet(str(diverge_net(tmp_path)))
        (rec,) = _lane_end_diverges(
            net, ["A", "B"], [EXIT_RAMP], frozenset(), {"A": 0.0, "B": 315.0}
        )
        length = float(net.getEdge("A").getLength())
        assert rec["edge"] == "A" and rec["x_end_m"] == pytest.approx(length)
        assert rec["succ"] == {0: frozenset({"E"}), 1: frozenset({"B"})}
        assert rec["cont"] == {
            0: ("exit", "E", "exit"),
            1: ("through", "B", DESTINATION_CORRIDOR_END),
        }
        assert (rec["n_gave_up_exit"], rec["n_took_exit"]) == (0, 0)

    def test_skipped_edges_and_the_corridor_end(self, tmp_path: Path) -> None:
        """A weaving section's edges keep their own rule; the corridor's last
        edge has no continuation on the chain; an edge whose off-ramp is not
        a scenario ramp has no exit continuation."""
        net = sumolib.net.readNet(str(diverge_net(tmp_path)))
        assert _lane_end_diverges(net, ["A", "B"], [EXIT_RAMP], {"A"}, {"A": 0.0}) == []
        assert _lane_end_diverges(net, ["A"], [EXIT_RAMP], frozenset(), {"A": 0.0}) == []
        (rec,) = _lane_end_diverges(net, ["A", "B"], [], frozenset(), {"A": 0.0})
        assert rec["cont"] == {1: ("through", "B", DESTINATION_CORRIDOR_END)}

    def test_th61_fixture(self, tmp_path: Path) -> None:
        """The T.H.61 stretch is the fixture's one diverge: lanes 0-1 of the
        5-lane edge lead only to the exit, lanes 2-4 only on. The 3 -> 2 lane
        drop past the gore is not one (its ending lane has no successor)."""
        cfg = th61_config(3)
        assert isinstance(cfg.network, OSMNetwork)
        bundle = osm_import(
            osm_file=cfg.network.osm_file,
            corridor_edges=tuple(cfg.network.corridor_edges),
            workdir=tmp_path,
            keep_edges=("200", "201"),
            patch_files=[Path(p) for p in cfg.network.patch_files],
        )
        net = sumolib.net.readNet(str(bundle.net_path))
        offsets = dict(zip(bundle.edge_ids, bundle.offsets, strict=True))
        (rec,) = _lane_end_diverges(
            net, list(bundle.edge_ids), list(cfg.network.ramps), frozenset(), offsets
        )
        assert rec["edge"] == TH61_EDGE
        meta = _lane_end_meta(_state([rec]))
        assert meta is not None
        (row,) = meta["diverges"]
        assert row["through_lanes"] == [2, 3, 4] and row["exit_lanes"] == [0, 1]
        assert row["exits"] == ["mounds exit"]
        assert {c[1] for j, c in rec["cont"].items() if j >= 2} == {bundle.edge_ids[-1]}
        assert {c[1] for j, c in rec["cont"].items() if j <= 1} == {"201"}


# --- the step on a fake SUMO ------------------------------------------------

BLOCKED_RIGHT = tc.LCA_RIGHT | tc.LCA_STRATEGIC | tc.LCA_URGENT | tc.LCA_BLOCKED_BY_RIGHT_LEADER
BLOCKED_LEFT = tc.LCA_LEFT | tc.LCA_STRATEGIC | tc.LCA_URGENT | tc.LCA_BLOCKED_BY_LEFT_FOLLOWER


class _FakeVehicle:
    def __init__(self, routes: dict[str, tuple[tuple[str, ...], int]], states: dict[str, int]):
        self.routes, self.states = routes, states
        self.asked: list[tuple[str, int]] = []
        self.targets: list[tuple[str, str]] = []

    def getRoute(self, vid: str) -> tuple[str, ...]:
        return self.routes[vid][0]

    def getRouteIndex(self, vid: str) -> int:
        return self.routes[vid][1]

    def getLaneChangeState(self, vid: str, direction: int) -> tuple[int, int]:
        self.asked.append((vid, direction))
        return self.states[vid], self.states[vid]

    def changeTarget(self, vid: str, edge: str) -> None:
        self.targets.append((vid, edge))


class _FakeMod:
    def __init__(self, vehicle: _FakeVehicle) -> None:
        self.vehicle = vehicle


def _rec() -> dict[str, Any]:
    return {
        "edge": "A",
        "x_end_m": 315.0,
        "lane_len_m": {0: 315.0, 1: 315.0},
        "succ": {0: frozenset({"E"}), 1: frozenset({"B"})},
        "cont": {0: ("exit", "E", "exit"), 1: ("through", "B", DESTINATION_CORRIDOR_END)},
        "n_gave_up_exit": 0,
        "n_took_exit": 0,
    }


def _res(road: str, lane: int, pos: float, v: float = 0.0) -> dict[int, Any]:
    return {
        tc.VAR_ROAD_ID: road,
        tc.VAR_LANE_INDEX: lane,
        tc.VAR_LANEPOSITION: pos,
        tc.VAR_SPEED: v,
    }


EXITER = ("A", "E")
THROUGH = ("A", "B")


def _run_step(
    results: dict[str, dict[int, Any]],
    routes: dict[str, tuple[str, ...]],
    states: dict[str, int],
    controlled: frozenset[str] = frozenset(),
) -> tuple[list[tuple[str, str]], _FakeVehicle, dict[str, Any]]:
    veh = _FakeVehicle({v: (r, 0) for v, r in routes.items()}, states)
    rec = _rec()
    out = _lane_end_step(_FakeMod(veh), tc, _state([rec]), results, lambda v: v in controlled)
    return out, veh, rec


class TestStep:
    def test_the_crossing_pair_is_given_up_both_ways(self) -> None:
        """The exiter held at the end of the through lane drives on to the
        corridor's end; the vehicle bound on held at the end of the exit-only
        lane takes the exit. Each is asked about the change toward its route
        (the exiter right, the other left), in ``veh_id`` order."""
        out, veh, rec = _run_step(
            {"x": _res("A", 1, 313.0), "t": _res("A", 0, 309.7)},
            {"x": EXITER, "t": THROUGH},
            {"x": BLOCKED_RIGHT, "t": BLOCKED_LEFT},
        )
        assert out == [("t", "exit"), ("x", DESTINATION_CORRIDOR_END)]
        assert veh.targets == [("t", "E"), ("x", "B")]
        assert veh.asked == [("t", 1), ("x", -1)]
        assert (rec["n_gave_up_exit"], rec["n_took_exit"]) == (1, 1)

    @pytest.mark.parametrize(
        ("case", "results", "routes", "states", "controlled"),
        [
            # still creeping: at the halting speed, not below it
            ("moving", {"x": _res("A", 1, 313.0, v=HALTING_SPEED_MS)}, {"x": EXITER}, {}, ()),
            # farther from the lane's end than the distance
            ("far", {"x": _res("A", 1, 315.0 - GIVEUP_M - 0.1)}, {"x": EXITER}, {}, ()),
            # queued behind the lane's front vehicle
            (
                "queued",
                {"x": _res("A", 1, 308.0), "y": _res("A", 1, 314.9)},
                {"x": EXITER, "y": THROUGH},
                {"x": BLOCKED_RIGHT},
                (),
            ),
            # its route continues on its lane (an exiter in the exit-only lane)
            ("continues", {"x": _res("A", 0, 313.0)}, {"x": EXITER}, {"x": BLOCKED_LEFT}, ()),
            # SUMO still has the change open
            ("open", {"x": _res("A", 1, 313.0)}, {"x": EXITER}, {"x": tc.LCA_RIGHT}, ()),
            # SUMO has no state for it yet
            ("unknown", {"x": _res("A", 1, 313.0)}, {"x": EXITER}, {"x": tc.LCA_UNKNOWN}, ()),
            # a weaving section or scripted merge commands it
            ("commanded", {"x": _res("A", 1, 313.0)}, {"x": EXITER}, {"x": BLOCKED_RIGHT}, ("x",)),
            # not on a diverge
            ("elsewhere", {"x": _res("B", 0, 1.0)}, {"x": ("B",)}, {}, ()),
            # its route ends on this edge
            ("arriving", {"x": _res("A", 1, 313.0)}, {"x": ("A",)}, {"x": BLOCKED_RIGHT}, ()),
            # a route whose next edge no lane of the edge reaches
            ("unreached", {"x": _res("A", 1, 313.0)}, {"x": ("A", "Z")}, {"x": BLOCKED_RIGHT}, ()),
        ],
    )
    def test_no_give_up(
        self,
        case: str,
        results: dict[str, dict[int, Any]],
        routes: dict[str, tuple[str, ...]],
        states: dict[str, int],
        controlled: tuple[str, ...],
    ) -> None:
        out, veh, rec = _run_step(results, routes, states, frozenset(controlled))
        assert out == [] and veh.targets == [], case
        assert (rec["n_gave_up_exit"], rec["n_took_exit"]) == (0, 0), case

    def test_the_lane_front_at_the_distance_is_given_up(self) -> None:
        out, _veh, _ = _run_step(
            {"x": _res("A", 1, 315.0 - GIVEUP_M)}, {"x": EXITER}, {"x": BLOCKED_RIGHT}
        )
        assert out == [("x", DESTINATION_CORRIDOR_END)]

    def test_meta_off_and_on(self) -> None:
        assert _lane_end_meta(None) is None
        rec = _rec()
        rec["n_took_exit"] = 2
        meta = _lane_end_meta(_state([rec]))
        assert meta == {
            "distance_m": GIVEUP_M,
            "skipped_edges": [],
            "n_gave_up_exit": 0,
            "n_took_exit": 2,
            "diverges": [
                {
                    "edge": "A",
                    "x_end_m": 315.0,
                    "through_lanes": [1],
                    "exit_lanes": [0],
                    "exits": ["exit"],
                    "n_gave_up_exit": 0,
                    "n_took_exit": 2,
                }
            ],
        }

    def test_commanded_by_runner(self) -> None:
        empty: dict[str, Any] = {k: {} for k in ("veh", "pre", "vacate", "prep", "handover")}
        for key in empty:
            ws = {**empty, key: {"v": object()}}
            assert _commanded_by_runner([ws], [], "v"), key
            assert not _commanded_by_runner([ws], [], "w"), key
        for key in ("veh", "yielding"):
            ss: dict[str, Any] = {"veh": {}, "yielding": {}, key: {"v": 1.0}}
            assert _commanded_by_runner([], [ss], "v"), key
        assert not _commanded_by_runner([empty], [{"veh": {}, "yielding": {}}], "v")


# --- the crossing pair in SUMO ----------------------------------------------


@pytest.mark.integration
def test_the_crossing_pair_stands_until_given_up_then_drives_on_in_its_lane(
    tmp_path: Path,
) -> None:
    """The lock VM T shows, built by hand on the minimal diverge.

    Three vehicles stand in the last metres of ``A``: an exiter ``x`` at the
    end of the through lane 1, a vehicle ``t`` bound on at the end of the
    exit-only lane 0 beside it, and a second exiter ``q`` 8 m behind ``x``.
    ``x`` and ``t`` each wait for the other's lane and stand for as long as
    the run lasts: the lock, with the rule off (the default).

    One step of the rule gives both up. Each is then a route change only:
    ``x`` drives on from lane 1 onto ``B``, ``t`` from lane 0 onto the exit
    ``E``, each in the lane it stood in, with no collision. ``q`` is queued
    (on SUMO 1.27.1 it joins lane 0 behind ``t``, whose lane leads to its
    exit), is not touched, and takes its exit once the pair has gone.
    """
    import libsumo as mod

    net_path = diverge_net(tmp_path)
    net = sumolib.net.readNet(str(net_path))
    diverges = _lane_end_diverges(net, ["A", "B"], [EXIT_RAMP], frozenset(), {"A": 0.0})
    le = _state(diverges)
    end = float(net.getEdge("A").getLanes()[0].getLength())
    sub = [tc.VAR_SPEED, tc.VAR_LANEPOSITION, tc.VAR_ROAD_ID, tc.VAR_LANE_INDEX]
    mod.start(
        [
            "sumo",
            *("-n", str(net_path)),
            *("--step-length", "0.5"),
            *("--time-to-teleport", "-1"),
            "--no-warnings",
            *("--collision.action", "warn"),
            "--no-step-log",
        ]
    )
    try:
        mod.route.add("through", ["A", "B"])
        mod.route.add("exiting", ["A", "E"])
        for vid, route, lane, back in (
            ("x", "exiting", 1, 2.0),
            ("t", "through", 0, 2.0),
            ("q", "exiting", 1, 10.0),
        ):
            mod.vehicle.add(
                vid, route, departLane=str(lane), departPos=str(end - back), departSpeed="0"
            )
        seen: list[dict[str, tuple[str, int, float, float]]] = []

        def step() -> dict[str, Any]:
            mod.simulationStep()
            for vid in mod.simulation.getDepartedIDList():
                mod.vehicle.subscribe(vid, sub)
            assert mod.simulation.getCollidingVehiclesNumber() == 0
            res: dict[str, Any] = mod.vehicle.getAllSubscriptionResults()
            seen.append(
                {
                    v: (
                        r[tc.VAR_ROAD_ID],
                        int(r[tc.VAR_LANE_INDEX]),
                        float(r[tc.VAR_LANEPOSITION]),
                        float(r[tc.VAR_SPEED]),
                    )
                    for v, r in res.items()
                }
            )
            return res

        for _ in range(60):  # 30 s with the rule off
            res = step()
        # the pair stands at the lane ends, each in the lane the other needs
        for snap in seen[-40:]:
            assert snap["x"][:2] == ("A", 1) and snap["t"][:2] == ("A", 0)
            assert snap["x"][3] < HALTING_SPEED_MS and snap["t"][3] < HALTING_SPEED_MS
            assert end - snap["x"][2] <= GIVEUP_M and end - snap["t"][2] <= GIVEUP_M

        out = _lane_end_step(mod, tc, le, res, lambda v: False)
        assert out == [("t", "exit"), ("x", DESTINATION_CORRIDOR_END)]
        assert tuple(mod.vehicle.getRoute("x")) == ("A", "B")
        assert tuple(mod.vehicle.getRoute("t")) == ("A", "E")
        assert tuple(mod.vehicle.getRoute("q")) == ("A", "E")

        n_before = len(seen)
        for _ in range(40):
            step()
        after = seen[n_before:]
        # every sample while still on A is in the lane the vehicle stood in
        for vid, lane in (("x", 1), ("t", 0)):
            on_a = [s[vid] for s in after if vid in s and s[vid][0] == "A"]
            assert all(s[1] == lane for s in on_a), vid
        # each drove on to its lane's continuation (and may have left the net)
        for vid, onward in (("x", "B"), ("t", "E"), ("q", "E")):
            roads = {s[vid][0] for s in after if vid in s}
            assert onward in roads and roads <= {"A", onward}, (vid, roads)
    finally:
        mod.close()


# --- the rule in a run ------------------------------------------------------


@pytest.mark.integration
class TestRun:
    def test_off_by_default(self, tmp_path: Path) -> None:
        cfg = th61_queued_config(3, duration_s=120.0)
        paths = run_micro(cfg, 3, tmp_path / "off")
        meta = json.loads(paths.meta.read_text())
        assert meta["lane_end_giveups"] is None
        assert not read_vehicles(paths.run_dir)["gave_up"].any()

    def test_on_the_th61_fixture(self, tmp_path: Path) -> None:
        """The two peaks in sequence with the rule at 7.5 m, at the first of
        :data:`TH61_RULE_SEEDS` at which the rule acts.

        Measured on macOS (docs/WEAVE_MODEL_PLAN.md, WP-71): at seed 5, 3
        T.H.61 entrants bound on, held at the end of exit-only lane 1, take
        the exit; no exit is given up; 2,592 of 2,592 depart; no collision.
        Since the fixture's exit compiles straight (WP-83) the rule acts at
        3 / 1 / 1 / 3 / 0 / 1 / 3 / 0 entrants over seeds 3-10 on macOS, no
        exit given up and no collision at any; on Linux (CI on 0b9ab40) it
        acts at none at seed 5. Which seed it acts at is the platform's, so
        the test takes the first seed at which it does and fails if none of
        them does. Checked without the counts: the meta block describes the
        fixture's one diverge, and ``vehicles.parquet`` marks exactly the
        vehicles the counters count, each with the destination it drove to.
        Each left the corridor at the end of the lane it stood in.
        """
        for seed in TH61_RULE_SEEDS:
            paths = run_micro(th61_queued_config(seed, GIVEUP_M), seed, tmp_path / f"on_{seed}")
            meta = json.loads(paths.meta.read_text())
            block = meta["lane_end_giveups"]
            if block["n_took_exit"] + block["n_gave_up_exit"] >= 1:
                break
        else:
            pytest.fail(f"the rule acts at none of seeds {TH61_RULE_SEEDS}")
        assert meta["n_collisions"] == 0
        assert block["distance_m"] == GIVEUP_M and block["skipped_edges"] == []
        (row,) = block["diverges"]
        assert (row["edge"], row["exit_lanes"], row["through_lanes"]) == (
            TH61_EDGE,
            [0, 1],
            [2, 3, 4],
        )
        n_took, n_gave = block["n_took_exit"], block["n_gave_up_exit"]
        assert (n_took, n_gave) == (row["n_took_exit"], row["n_gave_up_exit"])
        assert n_took + n_gave >= 1
        df = read_vehicles(paths.run_dir).set_index("veh_id")
        given = df[df["gave_up"]]
        assert len(given) == n_took + n_gave
        took = given[given["destination"] != "mounds exit"]
        gave = given[given["destination"] == "mounds exit"]
        assert len(took) == n_took and len(gave) == n_gave
        assert (took["destination_final"] == "mounds exit").all()
        assert (gave["destination_final"] == DESTINATION_CORRIDOR_END).all()
        x_end = row["x_end_m"]
        for vid, r in took.iterrows():
            # last seen at the gore's end in an exit-only lane, then gone by the exit
            assert int(r["last_lane"]) in (0, 1), vid
            assert x_end - GIVEUP_M - 1.0 <= float(r["last_x_m"]) < x_end, vid
            assert bool(r["arrived"]) or float(r["last_t_s"]) >= 1799.0, vid
        for vid, r in gave.iterrows():
            assert float(r["last_x_m"]) > x_end, vid
