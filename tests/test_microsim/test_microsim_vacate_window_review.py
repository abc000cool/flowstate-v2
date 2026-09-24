"""Review of the cross-edge vacate window (commit cf2e4f6; 2026-09-24, block 3).

``microsim.runner._weave_vacate_lanes`` walks the corridor chain upstream of a
weaving section and derives, per edge, the lanes feeding section lanes 1 and 2
from netconvert's connections; ``_weave_vacate_step`` asks through vehicles in
the first to move to the second. Pinned here: the walk on the committed I-94
extract (both sections), the walk on ``tests/fixtures/weave_two.osm`` (windows
of the two sections at the default and beyond it), the two bookkeeping defects
the review confirmed and fixed — a vehicle whose change and crossing onto the
section fell in one step was counted refused, and a vehicle bound for an exit
leaving from a window edge was "through" and asked left, away from its exit —
and the run-to-run byte identity of a two-section run at the default window.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sumolib
import yaml

from flowstate_core.config import WEAVE_DEFAULTS, OSMNetwork, ScenarioConfig
from microsim.networks import expand_ramp_splits, osm_import
from microsim.runner import (
    LC_MODE_SCRIPTED_SAFE,
    _check_weave_pairs,
    _weave_step,
    _weave_vacate_exempt_ids,
    _weave_vacate_lanes,
    run_micro,
)
from tests.test_microsim.test_microsim_merge_managed_meter import (
    TWO_WEAVE_CORRIDOR,
    TWO_WEAVE_OSM,
    _res,
    _sections_back_to_back,
    _tc,
    _weave_state,
    _WeaveMod,
    _WeaveVehicle,
    two_weave_scenario,
)

pytestmark = pytest.mark.integration

I94_SCENARIO = Path("scenarios/mndot_i94_wb_stpaul_weave.yaml")
I94_OSM = Path("data/osm/mndot_i94_wb_stpaul.osm")


def _compile_two(tmp_path: Path):
    bundle = osm_import(
        osm_file=TWO_WEAVE_OSM,
        corridor_edges=TWO_WEAVE_CORRIDOR,
        keep_edges=("200", "201", "202", "203"),
        workdir=tmp_path / "two",
    )
    net = sumolib.net.readNet(str(bundle.net_path))
    chain = expand_ramp_splits(list(TWO_WEAVE_CORRIDOR), bundle.edge_ids)
    offsets = dict(zip(bundle.edge_ids, bundle.offsets, strict=True))
    return net, chain, offsets


@pytest.mark.skipif(not (I94_SCENARIO.is_file() and I94_OSM.is_file()), reason="MnDOT files absent")
def test_i94_windows_of_both_sections(tmp_path: Path) -> None:
    """The committed extract compiled as ``run_micro`` compiles it: the T.H.52
    section's 500 m window spans four edges and follows section lane 1's
    feeder through the 40648744 acceleration lane (lane 0 of the two
    four-lane pieces, so the feeder is lane 1 there and lane 0 on the
    three-lane pieces either side); the walk stops at 40648793#0, whose
    start (9,867.5 m) lies below the window's lower end (9,926.6 m). Ruth
    St's window lies within the 632 m edge before it. No off-ramp leaves
    from a window edge, so the exempt set is the paired exit's vehicles."""
    cfg = ScenarioConfig.model_validate(yaml.safe_load(I94_SCENARIO.read_text()))
    net_cfg = cfg.network
    assert isinstance(net_cfg, OSMNetwork)
    bundle = osm_import(
        osm_file=net_cfg.osm_file,
        corridor_edges=tuple(net_cfg.corridor_edges),
        workdir=tmp_path / "i94",
        keep_edges=tuple(e for r in net_cfg.ramps for e in r.edges),
        patch_files=[Path(p) for p in net_cfg.patch_files],
        netconvert_extra=tuple(net_cfg.netconvert_extra),
    )
    net = sumolib.net.readNet(str(bundle.net_path))
    chain = expand_ramp_splits(list(net_cfg.corridor_edges), bundle.edge_ids)
    offsets = dict(zip(bundle.edge_ids, bundle.offsets, strict=True))
    assert list(bundle.edge_ids) == chain  # every ramp-split piece is in the offsets
    sections = {s.edges[0]: s for s in _check_weave_pairs(net_cfg, net, chain)}
    assert set(sections) == {"999007700", "51388891"}
    for s in sections.values():
        assert net_cfg.ramps[s.on_ramp].weave is not None
        assert net_cfg.ramps[s.on_ramp].weave.weave_params == {}  # the default window
    ahead = float(WEAVE_DEFAULTS["vacate_ahead_m"])
    assert ahead == 500.0

    th52 = _weave_vacate_lanes(net, chain, sections["51388891"].edges, offsets, ahead)
    assert th52 == {
        "40648738": (0, 1),
        "40648738-AddedOnRampEdge": (1, 2),
        "40648793#1": (1, 2),
        "40648793#0": (0, 1),
    }
    assert list(th52) == ["40648738", "40648738-AddedOnRampEdge", "40648793#1", "40648793#0"]
    assert [net.getEdge(e).getLaneNumber() for e in th52] == [3, 4, 4, 3]
    assert offsets["51388891"] == pytest.approx(10426.6, abs=0.1)
    assert offsets["40648793#0"] < offsets["51388891"] - ahead < offsets["40648793#1"]
    # the acceleration lane is lane 0 of both four-lane pieces: it dead-ends
    # at the end of the split piece, the other three shift down by one
    assert sorted(
        (int(lane.getIndex()), int(c.getToLane().getIndex()))
        for lane in net.getEdge("40648738-AddedOnRampEdge").getLanes()
        for c in lane.getOutgoing()
        if c.getTo().getID() == "40648738"
    ) == [(1, 0), (2, 1), (3, 2)]
    # the shorter windows the sensitivity table names
    assert _weave_vacate_lanes(net, chain, ["51388891"], offsets, 150.0) == {"40648738": (0, 1)}
    assert list(_weave_vacate_lanes(net, chain, ["51388891"], offsets, 300.0)) == [
        "40648738",
        "40648738-AddedOnRampEdge",
    ]

    ruth = _weave_vacate_lanes(net, chain, sections["999007700"].edges, offsets, ahead)
    assert ruth == {"638519829": (0, 1)}
    assert net.getEdge("638519829").getLength() > ahead

    # no off-ramp leaves from a window edge of either section: the exempt
    # set is the paired exit's vehicles, nothing more
    routes = {"v_main": "main"} | {f"v_off{j}": f"main_off{j}" for j in range(len(net_cfg.ramps))}
    for first, walk in (("51388891", th52), ("999007700", ruth)):
        s = sections[first]
        exempt = _weave_vacate_exempt_ids(net, net_cfg.ramps, s, walk, routes)
        assert exempt == {f"v_off{s.off_ramp}"}


def test_weave_two_windows_do_not_overlap_at_the_default(tmp_path: Path) -> None:
    """On ``weave_two.osm`` (sections 102 and 105, 560 m apart) the 500 m
    windows are 101/100 and 104/103: neither reaches the other section's
    edge. At 800 m B's window reaches A's section edge 102 (its lanes 1 / 2
    — A's weave lane and the one left of it), where A's exit leaves: A's
    exiters are then exempt from B's ask; at 500 m they are not (A's exit is
    behind B's window, so a vehicle still carrying that route gave it up)."""
    net, chain, offsets = _compile_two(tmp_path)
    assert _weave_vacate_lanes(net, chain, ["102"], offsets, 500.0) == {
        "101": (0, 1),
        "100": (0, 1),
    }
    assert _weave_vacate_lanes(net, chain, ["105"], offsets, 500.0) == {
        "104": (0, 1),
        "103": (0, 1),
    }
    assert _weave_vacate_lanes(net, chain, ["105"], offsets, 800.0) == {
        "104": (0, 1),
        "103": (0, 1),
        "102": (1, 2),
    }
    cfg = two_weave_scenario(downstream_first=False, ramp_rate=0.0)
    net_cfg = cfg.network
    assert isinstance(net_cfg, OSMNetwork)
    a, b = _check_weave_pairs(net_cfg, net, chain)
    assert (a.edges, b.edges) == (("102",), ("105",))
    routes = {"ta": "main", "ea": "main_off1", "eb": "main_off3"}
    assert _weave_vacate_exempt_ids(net, net_cfg.ramps, a, ["101", "100"], routes) == {"ea"}
    assert _weave_vacate_exempt_ids(net, net_cfg.ramps, b, ["104", "103"], routes) == {"eb"}
    assert _weave_vacate_exempt_ids(net, net_cfg.ramps, b, ["104", "103", "102"], routes) == {
        "ea",
        "eb",
    }


class TestVacateStepReview:
    """The fake-TraCI harness of ``test_microsim_merge_managed_meter``."""

    @staticmethod
    def _state(**params) -> dict:
        ws = _weave_state(**{"vacate_ahead_m": 150.0, **params})
        ws["vacate_lanes"] = {"p": (0, 1)}
        ws["lane_map"].update({("p", 0): 1, ("p", 1): 2})
        ws["x_offset"]["p"] = -200.0
        return ws

    def test_change_and_crossing_in_one_step_is_vacated(self) -> None:
        """Asked 5 m before the section start, seen next on the section's
        lane 2 (the target lane's continuation, ``lanes[2]``): vacated, not
        refused (the count was refused before the review); the one-step stay
        and the mode restore are issued as before."""
        ws = self._state()
        veh = _WeaveVehicle({"t": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 195.0, 20.0)}, 0.0)
        assert veh.calls == [("lc", "t", LC_MODE_SCRIPTED_SAFE), ("change", "t", 1, 0.5)]
        veh.calls.clear()
        _weave_step(mod, _tc, ws, {"t": _res("a", 2, 5.0, 20.0)}, 0.5)
        assert (ws["n_vacated"], ws["n_vacate_refused"]) == (1, 0)
        assert veh.calls == [("lc", "t", 1621)] and "t" not in ws["vacate"]
        # with the request still open at the crossing the stay is issued too
        ws = self._state()
        veh = _WeaveVehicle({"u": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"u": _res("p", 0, 150.0, 20.0)}, 0.0)
        veh.calls.clear()
        _weave_step(mod, _tc, ws, {"u": _res("a", 2, 2.0, 20.0)}, 0.5)
        assert (ws["n_vacated"], ws["n_vacate_refused"]) == (1, 0)
        assert veh.calls == [("change", "u", 2, 0.5), ("lc", "u", 1621)]
        # reaching the section still in the weave lane is refused as before
        ws = self._state()
        veh = _WeaveVehicle({"w": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"w": _res("p", 0, 195.0, 20.0)}, 0.0)
        _weave_step(mod, _tc, ws, {"w": _res("a", 1, 5.0, 20.0)}, 0.5)
        assert (ws["n_vacated"], ws["n_vacate_refused"]) == (0, 1)

    def test_a_vehicle_exiting_inside_the_window_is_not_asked(self) -> None:
        """``vacate_exempt_ids`` (as ``run_micro`` builds them: the paired
        exit's vehicles and those bound for an off-ramp leaving from a window
        edge): ``e`` of the back-to-back sections exits at B, from A's window
        edge, and is never asked by A, while a through vehicle beside it is."""
        _ws_b, ws_a = _sections_back_to_back()
        assert ws_a["vacate_exempt_ids"] == {"e"} and ws_a["exiting_ids"] == frozenset()
        veh = _WeaveVehicle({"e": 5.0, "t": 5.0})
        mod = _WeaveMod(veh)
        res = {"e": _res("b", 1, 70.0, 5.0), "t": _res("b", 1, 60.0, 5.0)}
        _weave_step(mod, _tc, ws_a, res, 0.0)
        assert set(ws_a["vacate"]) == {"t"} and ws_a["vacate_seen"] == {"t"}
        assert [c for c in veh.calls if c[0] == "change"] == [("change", "t", 2, 8.0)]
        assert ws_a["n_vacate_requests"] == 1
        # a vehicle exiting at this section's own exit is exempt through the same set
        ws = self._state()
        veh = _WeaveVehicle({"e": 20.0})
        _weave_step(_WeaveMod(veh), _tc, ws, {"e": _res("p", 0, 100.0, 20.0)}, 0.0)
        assert veh.calls == [] and not ws["vacate"]

    def test_re_issue_on_edge_crossing_is_not_a_new_request(self) -> None:
        """``n_vacate_requests`` counts the requests of the default form once
        per vehicle: the re-issue where the target index shifts across an
        edge (``lane_to`` re-addressed) does not add to it; ``n_vacated``
        counts the vehicle once, whichever edge it is seen vacated on."""
        ws = self._state(vacate_ahead_m=500.0)
        ws["vacate_lanes"] = {"p": (0, 1), "q": (1, 2)}
        ws["x_offset"]["q"] = -400.0
        veh = _WeaveVehicle({"t": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"t": _res("q", 1, 100.0, 20.0)}, 0.0)
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 5.0, 20.0)}, 10.0)
        assert [c for c in veh.calls if c[0] == "change"] == [
            ("change", "t", 2, 15.0),
            ("change", "t", 1, 5.0),
        ]
        assert ws["n_vacate_requests"] == 1
        _weave_step(mod, _tc, ws, {"t": _res("p", 1, 15.0, 20.0)}, 10.5)
        _weave_step(mod, _tc, ws, {"t": _res("a", 2, 5.0, 20.0)}, 11.0)
        assert (ws["n_vacated"], ws["n_vacate_refused"], ws["n_vacate_requests"]) == (1, 0, 1)


def test_two_section_run_is_byte_identical_run_to_run(tmp_path: Path) -> None:
    """``weave_two.osm`` at the default window (500 m: both windows span two
    edges, ``vacate_window_edges``), the same scenario and seed run twice:
    the same trajectory bytes and the same ``weave_sections`` entries."""
    cfg = two_weave_scenario(downstream_first=False, ramp_rate=0.0)
    p1 = run_micro(cfg, 5, tmp_path / "r1")
    p2 = run_micro(cfg, 5, tmp_path / "r2")
    assert p1.trajectories.read_bytes() == p2.trajectories.read_bytes()
    m1 = json.loads(p1.meta.read_text())
    m2 = json.loads(p2.meta.read_text())
    assert m1["weave_sections"] == m2["weave_sections"]
    assert [w["vacate_window_edges"] for w in m1["weave_sections"]] == [
        ["101", "100"],
        ["104", "103"],
    ]
    assert all(w["n_vacated"] > 0 for w in m1["weave_sections"])
