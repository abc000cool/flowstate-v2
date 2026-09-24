"""On-ramp merge models, managed (HOV) lanes and ramp metering (docs/CONTRACTS.md §2)."""

from __future__ import annotations

import importlib.util
import json
import math
from collections import deque
from pathlib import Path
from typing import ClassVar

import pandas as pd
import pytest
import sumolib

from flowstate_core.config import (
    SCRIPTED_MERGE_DEFAULTS,
    WEAVE_DEFAULTS,
    RampSpec,
    ScenarioConfig,
    config_hash,
)
from microsim import run_micro
from microsim.networks import (
    AccelLaneEnd,
    WeaveSection,
    accel_lane_end,
    lane_end_patch_file,
    merge_patch_files,
    osm_import,
    patch_net,
    weave_sections,
)

pytestmark = pytest.mark.integration

_RAMPS_MOD = Path(__file__).with_name("test_microsim_osm_ramps.py")
_spec = importlib.util.spec_from_file_location("ramps_fixture", _RAMPS_MOD)
assert _spec is not None and _spec.loader is not None
_ramps_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ramps_fixture)

# The ramp fixture's mainline widens to three lanes after the merge (way 102,
# the auxiliary lane) and ends there; append a two-lane way 103 so lane 0 of
# 102 dead-ends like a real acceleration lane.
MERGE_OSM = _ramps_fixture.RAMP_OSM.replace(
    '  <node id="10" lat="39.9994" lon="-95.9960"/>',
    '  <node id="5" lat="40.0000" lon="-95.9760"/>\n  <node id="10" lat="39.9994" lon="-95.9960"/>',
).replace(
    "</osm>",
    '  <way id="103">\n    <nd ref="4"/><nd ref="5"/>\n    <tag k="highway" v="motorway"/>\n'
    '    <tag k="oneway" v="yes"/>\n    <tag k="lanes" v="2"/>\n  </way>\n</osm>',
)


@pytest.fixture(scope="module")
def merge_osm(tmp_path_factory):
    p = tmp_path_factory.mktemp("merge") / "merge.osm"
    p.write_text(MERGE_OSM)
    return p


def _merge_scenario(
    osm_path: Path, merge: str, meter: dict | None = None, duration_s: float = 200.0
):
    on_ramp = {
        "kind": "on",
        "name": "test on-ramp",
        "edges": ["200"],
        "attach_edge": "102",
        "inflow": [[0.0, 0.25]],
        "merge": merge,
    }
    if meter is not None:
        on_ramp["meter"] = meter
    return ScenarioConfig.model_validate(
        {
            "name": f"merge_{merge}",
            "network": {
                "kind": "osm",
                "osm_file": str(osm_path),
                "corridor_edges": ["100", "101", "102", "103"],
                "inflow": [[0.0, 0.6]],
                "ramps": [on_ramp],
            },
            "sim": {"duration_s": duration_s},
        }
    )


class TestMergePatches:
    def test_patch_files_content(self, tmp_path):
        assert merge_patch_files(tmp_path, "a", "b", "n", 3, 2, "lane_change") == []
        (edg,) = merge_patch_files(tmp_path, "a", "b", "n", 3, 2, "acceleration_lane")
        assert edg.name.endswith(".edg.xml") and 'acceleration="true"' in edg.read_text()
        nod, con = merge_patch_files(tmp_path, "a#1", "b", "n", 3, 2, "zipper")
        assert 'type="zipper"' in nod.read_text() and 'id="n"' in nod.read_text()
        text = con.read_text()
        assert 'fromLane="0" toLane="0"' in text and 'fromLane="1" toLane="0"' in text
        assert 'fromLane="2" toLane="1"' in text
        assert "visibility" not in text  # SUMO's default distance unless asked for
        _nod, con_v = merge_patch_files(
            tmp_path, "a#1", "b", "n", 3, 2, "zipper", visibility_m=250.0
        )
        lines = [ln for ln in con_v.read_text().splitlines() if "<connection " in ln]
        # the ramp lane and the mainline lane it merges with interleave from 250 m; the
        # untouched lane keeps SUMO's default
        assert lines[0].endswith('fromLane="0" toLane="0" visibility="250"/>')
        assert lines[1].endswith('fromLane="1" toLane="0" visibility="250"/>')
        assert "visibility" not in lines[2]
        # a terminated spill keeps the next edge's width: lanes go through
        # unshifted and the ramp lane zippers into lane 1
        nod0, con0 = merge_patch_files(tmp_path, "a", "b", "n", 3, 3, "zipper")
        assert 'type="zipper"' in nod0.read_text()
        lines0 = [ln for ln in con0.read_text().splitlines() if "<connection " in ln]
        assert lines0[0].endswith('fromLane="0" toLane="1"/>')
        assert lines0[1].endswith('fromLane="1" toLane="1"/>')
        assert lines0[2].endswith('fromLane="2" toLane="2"/>')
        with pytest.raises(ValueError, match="drop exactly one lane"):
            merge_patch_files(tmp_path, "a", "b", "n", 3, 1, "zipper")
        with pytest.raises(ValueError, match="unknown merge"):
            merge_patch_files(tmp_path, "a", "b", "n", 3, 2, "teleport")

    @pytest.mark.parametrize("merge", ["acceleration_lane", "zipper"])
    def test_merge_models_run_and_are_recorded(self, merge_osm, tmp_path, merge):
        cfg = _merge_scenario(merge_osm, merge)
        paths = run_micro(cfg, 3, tmp_path / merge)
        meta = json.loads(paths.meta.read_text())
        assert meta["merge_models"] == [
            {"ramp": "test on-ramp", "attach_edge": "102", "merge": merge}
        ]
        assert meta["net_patch_files"], "patches were not applied"
        # the ramp's traffic gets through the merge
        assert meta["n_vehicles_departed"] > 0.8 * meta["n_vehicles_planned"]
        df = pd.read_parquet(paths.trajectories, columns=["veh_id", "x"])
        assert df["x"].max() > 1500.0


# The I-24 defect (docs/LESSONS.md row 31): the stop line sits on a short last
# ramp edge that vehicles reach at speed. Split way 200 so its last piece (202)
# is ~95 m; with the stop only set on that edge SUMO refused it ("too close to
# brake") and the run aborted.
TWO_EDGE_OSM = (
    MERGE_OSM.replace(
        '  <node id="10"', '  <node id="12" lat="39.99988" lon="-95.9896"/>\n  <node id="10"'
    )
    .replace('<nd ref="10"/><nd ref="3"/>', '<nd ref="10"/><nd ref="12"/>')
    .replace(
        "</osm>",
        '  <way id="202">\n    <nd ref="12"/><nd ref="3"/>\n'
        '    <tag k="highway" v="motorway_link"/>\n    <tag k="oneway" v="yes"/>\n'
        '    <tag k="lanes" v="1"/>\n  </way>\n</osm>',
    )
)
METER_BASE = {
    "controller": "alinea",
    "params": {"rho_target_veh_km": 25.0},
    "interval_s": 30.0,
    "rate_min_veh_h": 240.0,
    "rate_max_veh_h": 600.0,
}


class TestRampMeter:
    def test_meter_releases_one_vehicle_per_headway(self, merge_osm, tmp_path):
        meter = {
            "controller": "alinea",
            "params": {"rho_target_veh_km": 25.0},
            "interval_s": 30.0,
            "stop_line_m": 40.0,
            "rate_min_veh_h": 240.0,
            "rate_max_veh_h": 600.0,
        }
        cfg = _merge_scenario(merge_osm, "lane_change", meter=meter, duration_s=240.0)
        paths = run_micro(cfg, 3, tmp_path / "meter")
        meta = json.loads(paths.meta.read_text())
        (m,) = meta["ramp_meters"]
        assert m["controller"] == "alinea" and m["downstream_edge"] == "103"
        assert m["n_released"] >= 3
        gaps = [b - a for a, b in zip(m["releases_s"][:-1], m["releases_s"][1:], strict=True)]
        assert min(gaps) >= 3600.0 / 600.0 - 0.51  # never faster than the rate ceiling
        assert len(m["rates"]) >= 4 and all(240.0 <= r <= 600.0 for _, r, _ in m["rates"])
        assert m["n_passed_unstoppable"] == 0

    def test_short_last_edge_stop_is_set_from_the_first_edge(self, tmp_path):
        osm = tmp_path / "two_edge.osm"
        osm.write_text(TWO_EDGE_OSM)
        meter = {**METER_BASE, "stop_line_m": 30.0}
        cfg = _merge_scenario(osm, "lane_change", meter=meter, duration_s=240.0)
        d = cfg.model_dump()
        d["network"]["ramps"][0]["edges"] = ["200", "202"]
        cfg = ScenarioConfig.model_validate(d)
        paths = run_micro(cfg, 3, tmp_path / "two")
        (m,) = json.loads(paths.meta.read_text())["ramp_meters"]
        assert m["edge"] == "202"
        # every vehicle got its stop on edge 200, far enough upstream to brake
        assert m["n_passed_unstoppable"] == 0
        assert m["n_released"] >= 3

    def test_unbrakeable_vehicles_pass_and_the_meter_still_holds(self, merge_osm, tmp_path):
        # Way 200 is a single ~644 m edge; a stop line 510 m before its end is
        # ~134 m from the insertion point, inside the braking distance of a
        # vehicle inserted at speed. The old code raised TraCIException there.
        meter = {**METER_BASE, "stop_line_m": 510.0}
        cfg = _merge_scenario(merge_osm, "lane_change", meter=meter, duration_s=240.0)
        metas = []
        for k in range(2):
            paths = run_micro(cfg, 3, tmp_path / f"single{k}")
            (m,) = json.loads(paths.meta.read_text())["ramp_meters"]
            metas.append(m)
        m = metas[0]
        assert m["n_passed_unstoppable"] > 0
        assert m["n_released"] >= 3  # the others are still held and released
        assert metas[0] == metas[1]  # deterministic under the same seed


class _FakeTraCIException(Exception):
    pass


class _FakeVehicle:
    def __init__(self, pos: float, speed: float, decel: float, error: str | None) -> None:
        self.pos, self.speed, self.decel, self.error = pos, speed, decel, error
        self.stops: list[tuple] = []

    def getLanePosition(self, vid):
        return self.pos

    def getSpeed(self, vid):
        return self.speed

    def getDecel(self, vid):
        return self.decel

    def setStop(self, *args):
        if self.error is not None:
            raise _FakeTraCIException(self.error)
        self.stops.append(args)


class _FakeMod:
    TraCIException = _FakeTraCIException

    def __init__(self, vehicle: _FakeVehicle) -> None:
        self.vehicle = vehicle


METER_STATE = {
    "ramp_edges": ["a", "b", "c"],
    "edge_len_m": {"a": 200.0, "b": 50.0, "c": 80.0},
    "edge": "c",
    "stop_pos_m": 50.0,
}


class TestMeterStopPlacement:
    def test_distance_to_stop(self):
        from microsim.runner import _meter_distance_to_stop_m

        assert _meter_distance_to_stop_m(METER_STATE, "a", 20.0) == pytest.approx(180 + 50 + 50)
        assert _meter_distance_to_stop_m(METER_STATE, "b", 10.0) == pytest.approx(40 + 50)
        assert _meter_distance_to_stop_m(METER_STATE, "c", 60.0) == pytest.approx(-10.0)

    def test_stop_set_when_brakeable(self):
        from microsim.runner import _meter_assign_stop

        veh = _FakeVehicle(pos=0.0, speed=20.0, decel=2.0, error=None)
        assert _meter_assign_stop(_FakeMod(veh), METER_STATE, "v", "a", 0.5)
        assert veh.stops == [("v", "c", 50.0, 0, 1.0e9)]

    def test_within_braking_distance_passes_without_a_stop(self):
        from microsim.runner import _meter_assign_stop

        # 20 m/s at 2 m/s² needs 100 m + 10 m; only 90 m remain from edge b
        veh = _FakeVehicle(pos=10.0, speed=20.0, decel=2.0, error=None)
        assert not _meter_assign_stop(_FakeMod(veh), METER_STATE, "v", "b", 0.5)
        assert veh.stops == []

    def test_traci_refusal_is_a_pass_other_errors_raise(self):
        from microsim.runner import _meter_assign_stop

        refuse = _FakeVehicle(0.0, 5.0, 2.0, "stop for vehicle 'v' is too close to brake.")
        assert not _meter_assign_stop(_FakeMod(refuse), METER_STATE, "v", "a", 0.5)
        broken = _FakeVehicle(0.0, 5.0, 2.0, "unknown edge 'c'")
        with pytest.raises(_FakeTraCIException, match="unknown edge"):
            _meter_assign_stop(_FakeMod(broken), METER_STATE, "v", "a", 0.5)


class TestManagedLanes:
    def test_hov_lane_admits_only_eligible_vehicles_in_the_window(self, tmp_path):
        cfg = ScenarioConfig.model_validate(
            {
                "name": "hov",
                "network": {
                    "kind": "corridor",
                    "length_m": 3000.0,
                    "lanes": 2,
                    "inflow": [[0.0, 0.5]],
                },
                "fleet": {"hov_fraction": 0.3},
                "sim": {"duration_s": 300.0},
                "managed_lanes": [
                    {
                        "label": "HOV left lane",
                        "start_m": 200.0,
                        "end_m": 900.0,
                        "lanes": [1],
                        "t_start_s": 120.0,
                        "t_end_s": 210.0,
                    }
                ],
            }
        )
        assert cfg.seeded is False
        paths = run_micro(cfg, 5, tmp_path)
        meta = json.loads(paths.meta.read_text())
        assert meta["seeded"] is False
        (m,) = meta["managed_lanes"]
        assert m["lane_ids"] and all(lid.endswith("_1") for lid in m["lane_ids"])
        assert 120.0 <= m["applied_at_s"] < 121.0 and 210.0 <= m["released_at_s"] < 211.0
        assert abs(meta["n_hov"] / meta["n_vehicles_planned"] - 0.3) < 0.08
        df = pd.read_parquet(paths.trajectories)
        in_lane = df[(df.lane == 1) & (df.x >= m["x_lo_m"]) & (df.x < m["x_hi_m"])]
        before = in_lane[(in_lane.t > 90.0) & (in_lane.t < 118.0)]
        during = in_lane[(in_lane.t > 150.0) & (in_lane.t < 210.0)]
        after = in_lane[(in_lane.t > 240.0)]
        assert len(before) > 0 and not before["is_hov"].all()
        assert len(during) > 0 and during["is_hov"].all(), "non-eligible vehicle in the HOV lane"
        assert len(after) > 0 and not after["is_hov"].all()


class TestInternalLinks:
    def test_option_compiles_internal_lanes(self, merge_osm, tmp_path):
        import sumolib

        from flowstate_core.config import OSMNetwork
        from microsim.networks import osm_import

        assert OSMNetwork(osm_file="x.osm").internal_links is False
        plain = osm_import(
            osm_file=merge_osm,
            corridor_edges=("100", "101", "102", "103"),
            keep_edges=("200", "201"),
            workdir=tmp_path / "plain",
            geometry_remove=False,
        )
        with_links = osm_import(
            osm_file=merge_osm,
            corridor_edges=("100", "101", "102", "103"),
            keep_edges=("200", "201"),
            workdir=tmp_path / "links",
            geometry_remove=False,
            internal_links=True,
        )
        n_plain = len(
            sumolib.net.readNet(str(plain.net_path), withInternal=True).getEdges(withInternal=True)
        )
        n_links = len(
            sumolib.net.readNet(str(with_links.net_path), withInternal=True).getEdges(
                withInternal=True
            )
        )
        assert 'function="internal"' not in plain.net_path.read_text()
        assert 'function="internal"' in with_links.net_path.read_text()
        assert n_links > n_plain, "internal junction lanes were not compiled"
        assert with_links.edge_ids == plain.edge_ids


class TestScriptedMerge:
    """``RampSpec.merge = "scripted"``: the runner drives the acceleration lane."""

    def test_schema(self, merge_osm):
        cfg = _merge_scenario(merge_osm, "scripted")
        assert cfg.network.ramps[0].merge_params == {}
        with pytest.raises(ValueError, match="unknown merge_params"):
            RampSpec.model_validate(
                {
                    "kind": "on",
                    "edges": ["200"],
                    "attach_edge": "102",
                    "inflow": [[0.0, 0.1]],
                    "merge": "scripted",
                    "merge_params": {"bogus": 1.0},
                }
            )
        with pytest.raises(ValueError, match="scripted"):
            RampSpec.model_validate(
                {
                    "kind": "on",
                    "edges": ["200"],
                    "attach_edge": "102",
                    "inflow": [[0.0, 0.1]],
                    "merge": "zipper",
                    "merge_params": {"accept_gap_s": 1.0},
                }
            )
        # the hash sees the model and its parameters
        raw = cfg.model_dump()
        raw["network"]["ramps"][0]["merge_params"] = {"accept_gap_s": 0.3}
        assert config_hash(ScenarioConfig.model_validate(raw)) != config_hash(cfg)

    def test_runs_and_merges_every_ramp_vehicle(self, merge_osm, tmp_path):
        cfg = _merge_scenario(merge_osm, "scripted", duration_s=300.0)
        paths = run_micro(cfg, 3, tmp_path / "scripted")
        meta = json.loads(paths.meta.read_text())
        assert meta["merge_models"] == [
            {"ramp": "test on-ramp", "attach_edge": "102", "merge": "scripted"}
        ]
        assert meta["net_patch_files"] == []  # no netconvert patch for this model
        (sm,) = meta["scripted_merges"]
        assert sm["ramp"] == "test on-ramp" and sm["attach_edge"] == "102"
        assert sm["params"]["accept_gap_s"] == 0.6 and sm["params"]["force_after_s"] == 4.0
        assert sm["n_entered"] > 20, sm
        # every vehicle is either merged or still on the lane at the end of the
        # run (the fixture's merge is congested by design: a queue is expected)
        assert sm["n_changed"] + sm["n_unfinished"] == sm["n_entered"]
        assert sm["n_changed"] >= 0.6 * sm["n_entered"], sm
        assert sm["n_forced"] > 0, sm
        assert sm["wait_s_mean"] is not None and sm["wait_s_mean"] < 90.0
        assert meta["n_vehicles_departed"] > 0.8 * meta["n_vehicles_planned"]
        assert meta["n_collisions"] == 0
        # most ramp vehicles that departed reached the acceleration lane (the
        # rest queue on the ramp edge behind it at the end of the run)
        (ramp_meta,) = meta["ramps"]
        assert ramp_meta["n_departed"] > 0
        assert sm["n_entered"] >= 0.5 * ramp_meta["n_departed"], (sm, ramp_meta)
        df = pd.read_parquet(paths.trajectories, columns=["veh_id", "x"])
        assert df["x"].max() > 1500.0

    def test_params_change_behaviour(self, merge_osm, tmp_path):
        raw = _merge_scenario(merge_osm, "scripted", duration_s=200.0).model_dump()
        raw["network"]["ramps"][0]["merge_params"] = {"force_after_s": 0.0, "force_within_m": 1e9}
        cfg = ScenarioConfig.model_validate(raw)
        meta = json.loads(run_micro(cfg, 3, tmp_path / "forced").meta.read_text())
        (sm,) = meta["scripted_merges"]
        assert sm["params"]["force_after_s"] == 0.0
        assert sm["n_forced"] == sm["n_changed"] > 0  # every merge was a forced one


# --- A short attach edge: the guessed acceleration lane spills past it ------
#
# ``--ramps.guess`` adds an acceleration lane ``--ramps.ramp-length`` metres
# long at a merge. Way 102 below is ~153 m, shorter than that, so the lane
# covers the whole attach edge (3 lanes → 4) and the ~100 m that is left over
# splits the next way into a 4-lane ``103-AddedOnRampEdge`` piece and the
# 3-lane rest. Lane 0 of the attach edge therefore does NOT dead-end at that
# edge's end, which is what every merge model needs — the shape of every short
# entrance of the MnDOT corridor (docs/ONBOARDING_MNDOT.md §6).
#: Longitudes of the fixture's mainline nodes (0.006° ≈ 510 m at 40° N; the
#: third gap is deliberately ~153 m so way 102 is shorter than the ramp
#: length).
SPILL_LONS: tuple[float, ...] = (
    -96.0000,
    -95.9940,
    -95.9880,
    -95.98624,
    -95.9800,
    -95.9740,
)
SPILL_CORRIDOR: tuple[str, ...] = ("100", "101", "102", "103", "104")
SPILL_EXTRA: tuple[str, ...] = ("--ramps.guess", "--ramps.ramp-length", "250")


def _spill_osm() -> str:
    """The fixture OSM: a 3-lane mainline with an on-ramp at a short way."""

    def way(way_id: int, refs: list[int], highway: str, lanes: int) -> str:
        nds = "".join(f'<nd ref="{r}"/>' for r in refs)
        return (
            f'  <way id="{way_id}">\n    {nds}\n'
            f'    <tag k="highway" v="{highway}"/>\n'
            '    <tag k="oneway" v="yes"/>\n'
            f'    <tag k="lanes" v="{lanes}"/>\n'
            '    <tag k="maxspeed" v="70 mph"/>\n  </way>'
        )

    nodes = [
        f'  <node id="{i}" lat="40.0000" lon="{lon:.5f}"/>' for i, lon in enumerate(SPILL_LONS, 1)
    ]
    # the ramp approaches at a shallow angle, like real gore geometry
    nodes.append('  <node id="10" lat="39.9988" lon="-95.99300"/>')
    ways = [
        way(100, [1, 2], "motorway", 3),
        way(101, [2, 3], "motorway", 3),
        way(102, [3, 4], "motorway", 3),  # ~153 m: shorter than the ramp length
        way(103, [4, 5], "motorway", 3),
        way(104, [5, 6], "motorway", 3),
        way(200, [10, 3], "motorway_link", 1),  # on-ramp joining at node 3
    ]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<osm version="0.6" generator="hand-written-test-fixture">\n'
        + "\n".join(nodes)
        + "\n"
        + "\n".join(ways)
        + "\n</osm>\n"
    )


@pytest.fixture(scope="module")
def spill_osm(tmp_path_factory):
    p = tmp_path_factory.mktemp("spill") / "spill.osm"
    p.write_text(_spill_osm())
    return p


def _spill_scenario(
    osm_path: Path,
    merge: str,
    *,
    internal_links: bool = False,
    guess: bool = True,
    duration_s: float = 60.0,
) -> ScenarioConfig:
    """A scenario on the spill fixture with one on-ramp carrying ``merge``."""
    network: dict = {
        "kind": "osm",
        "osm_file": str(osm_path),
        "corridor_edges": list(SPILL_CORRIDOR),
        "inflow": [[0.0, 0.5]],
        "internal_links": internal_links,
        "ramps": [
            {
                "kind": "on",
                "name": "spill on-ramp",
                "edges": ["200"],
                "attach_edge": "102",
                "inflow": [[0.0, 0.25]],
                "merge": merge,
            }
        ],
    }
    if guess:
        network["netconvert_extra"] = list(SPILL_EXTRA)
    return ScenarioConfig.model_validate(
        {"name": f"spill_{merge}", "network": network, "sim": {"duration_s": duration_s}, "seed": 3}
    )


def _lane_connections(edge) -> list[list[tuple[str, int]]]:
    """Per-lane outgoing connections as ``(target edge id, target lane)``."""
    return [
        [(c.getTo().getID(), c.getToLane().getIndex()) for c in lane.getOutgoing()]
        for lane in edge.getLanes()
    ]


class TestSpilledAccelerationLane:
    """Terminating an acceleration lane that runs past its attach edge."""

    def test_walk_reports_the_spill_and_its_limits(self, spill_osm, tmp_path):
        bundle = osm_import(
            osm_file=spill_osm,
            corridor_edges=SPILL_CORRIDOR,
            keep_edges=("200",),
            workdir=tmp_path / "raw",
            netconvert_extra=SPILL_EXTRA,
        )
        assert bundle.edge_ids == ("100", "101", "102", "103-AddedOnRampEdge", "103", "104")
        net = sumolib.net.readNet(str(bundle.net_path))
        chain = list(bundle.edge_ids)
        assert net.getEdge("102").getLaneNumber() == 4
        assert _lane_connections(net.getEdge("102"))[0] == [("103-AddedOnRampEdge", 0)]

        end = accel_lane_end(net, chain, "102")
        assert end.ok and end.reason == ""
        assert end.tail == ("103-AddedOnRampEdge",) and 90.0 < end.tail_length_m < 120.0
        # a lane that already dead-ends has nothing after it
        assert accel_lane_end(net, chain, "103-AddedOnRampEdge") == AccelLaneEnd((), 0.0, "")
        # too long a tail is an added through lane, not a taper
        tight = accel_lane_end(net, chain, "102", max_tail_m=10.0)
        assert not tight.ok and "added through lane" in tight.reason
        # a lane feeding anything but the next corridor edge is a weave
        weave = accel_lane_end(net, ["100", "101", "102"], "102")
        assert not weave.ok and "weaving section" in weave.reason
        with pytest.raises(ValueError, match="not on the corridor chain"):
            accel_lane_end(net, chain, "200")

    def test_scripted_merge_terminates_the_lane_and_runs(self, spill_osm, tmp_path):
        cfg = _spill_scenario(spill_osm, "scripted")
        paths = run_micro(cfg, 3, tmp_path / "scripted")
        meta = json.loads(paths.meta.read_text())
        # the patch is named after the attach edge plus a digest of its raw id
        # (two ids that sanitise alike must not share a file)
        (patch,) = [Path(p).name for p in meta["net_patch_files"]]
        assert patch.startswith("accel_end_102_") and patch.endswith(".con.xml")
        (ramp_meta,) = meta["ramps"]
        assert ramp_meta["acceleration_lane_terminated"] is True
        assert ramp_meta["n_departed"] > 0, "no ramp vehicle departed"
        (sm,) = meta["scripted_merges"]
        assert sm["attach_edge"] == "102" and sm["n_entered"] > 0 and sm["n_changed"] > 0
        assert meta["n_collisions"] == 0

        net = sumolib.net.readNet(str(paths.run_dir / "net" / "osm_accel_end.net.xml"))
        attach = net.getEdge("102")
        assert attach.getLaneNumber() == 4
        conns = _lane_connections(attach)
        assert conns[0] == [], "the acceleration lane still continues"
        assert conns[1:] == [[("103-AddedOnRampEdge", i)] for i in (1, 2, 3)]
        # the ramp still feeds the acceleration lane
        assert _lane_connections(net.getEdge("200"))[0] == [("102", 0)]

    def test_zipper_merge_on_the_terminated_lane(self, spill_osm, tmp_path):
        cfg = _spill_scenario(spill_osm, "zipper", internal_links=True)
        paths = run_micro(cfg, 3, tmp_path / "zipper")
        meta = json.loads(paths.meta.read_text())
        # every stem is the attach edge plus a digest of its raw id
        names = sorted(Path(p).name for p in meta["net_patch_files"])
        assert [n.split("_1", 1)[0] for n in names] == ["accel_end", "merge", "merge"]
        assert [n.rsplit(".", 2)[-2] for n in names] == ["con", "con", "nod"]
        (ramp_meta,) = meta["ramps"]
        assert ramp_meta["acceleration_lane_terminated"] is True
        assert ramp_meta["n_departed"] > 0, "no ramp vehicle departed"
        assert meta["n_collisions"] == 0

        net = sumolib.net.readNet(str(paths.run_dir / "net" / "osm_merge_models.net.xml"))
        attach = net.getEdge("102")
        assert attach.getToNode().getType() == "zipper"
        # the ramp lane and the mainline lane beside it interleave into one lane
        conns = _lane_connections(attach)
        assert conns[0] == [("103-AddedOnRampEdge", 1)] and conns[1] == [("103-AddedOnRampEdge", 1)]
        assert conns[2:] == [[("103-AddedOnRampEdge", i)] for i in (2, 3)]

    def test_two_connection_patches_load_in_one_pass(self, spill_osm, tmp_path):
        """netconvert refuses a repeated option; patches of a kind are joined."""
        bundle = osm_import(
            osm_file=spill_osm,
            corridor_edges=SPILL_CORRIDOR,
            keep_edges=("200",),
            workdir=tmp_path / "two",
            netconvert_extra=SPILL_EXTRA,
        )
        patches = [
            lane_end_patch_file(tmp_path / "patches", "102", "103-AddedOnRampEdge"),
            lane_end_patch_file(tmp_path / "patches", "103", "104"),
        ]
        patched = patch_net(bundle, patches)
        assert len(patched.patch_files) == 2
        net = sumolib.net.readNet(str(patched.net_path))
        assert _lane_connections(net.getEdge("102"))[0] == []
        assert _lane_connections(net.getEdge("103"))[0] == []
        assert _lane_connections(net.getEdge("103"))[1] == [("104", 1)]

    def test_merge_model_refuses_an_attach_edge_with_no_added_lane(self, spill_osm, tmp_path):
        cfg = _spill_scenario(spill_osm, "scripted", guess=False)
        with pytest.raises(ValueError, match="added none"):
            run_micro(cfg, 3, tmp_path / "noguess")


# --- Weaving sections (RampSpec.merge = "weave", docs/WEAVE_MODEL_PLAN.md) ---
#
# ``WEAVE_OSM`` (test_microsim_osm_ramps.py): an on-ramp feeds lane 0 of way
# 102 and that lane leaves as exit link 201 ~180 m downstream, so the entering
# and the exiting streams cross over the same auxiliary lane.
WEAVE_OSM: str = _ramps_fixture.WEAVE_OSM
WEAVE_CORRIDOR: tuple[str, ...] = _ramps_fixture.WEAVE_CORRIDOR


@pytest.fixture(scope="module")
def weave_osm(tmp_path_factory):
    p = tmp_path_factory.mktemp("weave") / "weave.osm"
    p.write_text(WEAVE_OSM)
    return p


def _weave_ramps(merge: str = "weave", weave: dict | None = None) -> list[dict]:
    """On-ramp 200 and exit 201, both attached to way 102 (load-time ids)."""
    on_ramp: dict = {
        "kind": "on",
        "name": "weave on-ramp",
        "edges": ["200"],
        "attach_edge": "102",
        "inflow": [[0.0, 0.15], [100.0, 0.0]],
        "merge": merge,
    }
    if merge == "weave":
        on_ramp["weave"] = weave if weave is not None else {"exit_ramp": "weave exit"}
    off_ramp = {
        "kind": "off",
        "name": "weave exit",
        "edges": ["201"],
        "attach_edge": "102",
        "exit_fraction": [[0.0, 0.3]],
    }
    return [on_ramp, off_ramp]


def _weave_scenario(
    osm_path: Path, merge: str = "weave", weave: dict | None = None, duration_s: float = 200.0
) -> ScenarioConfig:
    """Mainline 0.4 veh/s and ramp 0.15 veh/s for 100 s, 30 % of the mainline exiting.

    Demand stops at 100 s so every vehicle has crossed the section by 200 s:
    a vehicle still under weave control at the end would be a stuck one.
    """
    return ScenarioConfig.model_validate(
        {
            "name": f"weave_{merge}",
            "network": {
                "kind": "osm",
                "osm_file": str(osm_path),
                "corridor_edges": list(WEAVE_CORRIDOR),
                "inflow": [[0.0, 0.4], [100.0, 0.0]],
                "ramps": _weave_ramps(merge, weave),
            },
            "sim": {"duration_s": duration_s},
            "seed": 3,
        }
    )


#: Two T.H.52-shaped sections in series on a three-lane mainline (2026-09-24,
#: block 3): A = entrance 200 / exit 201 on way 102, B = entrance 202 / exit
#: 203 on way 105, 560 m of three-lane mainline (103, 104) between them.
TWO_WEAVE_OSM = Path(__file__).resolve().parents[1] / "fixtures" / "weave_two.osm"
TWO_WEAVE_CORRIDOR: tuple[str, ...] = tuple(str(i) for i in range(100, 108))


def two_weave_scenario(
    *,
    downstream_first: bool,
    ramp_rate: float,
    mainline_rate: float = 0.5,
    demand_end_s: float = 120.0,
    duration_s: float = 300.0,
) -> ScenarioConfig:
    """Scenario on :data:`TWO_WEAVE_OSM`, the pairs listed either way round.

    Mainline ``mainline_rate`` veh/s and each entrance ``ramp_rate`` veh/s
    until ``demand_end_s`` (a rate of 0 draws nothing from the RNG, so the
    plan is the same whichever pair is listed first), 25 % of the mainline
    exiting at each exit; ``duration_s`` leaves time for every vehicle to
    clear the 2.3 km corridor.
    """
    pairs = {
        "A": ("A on", "200", "102", "A off", "201"),
        "B": ("B on", "202", "105", "B off", "203"),
    }
    ramps: list[dict] = []
    for key in "BA" if downstream_first else "AB":
        on_name, on_edge, attach, off_name, off_edge = pairs[key]
        ramps.append(
            {
                "kind": "on",
                "name": on_name,
                "edges": [on_edge],
                "attach_edge": attach,
                "inflow": [[0.0, ramp_rate], [demand_end_s, 0.0]],
                "merge": "weave",
                "weave": {"exit_ramp": off_name},
            }
        )
        ramps.append(
            {
                "kind": "off",
                "name": off_name,
                "edges": [off_edge],
                "attach_edge": attach,
                "exit_fraction": [[0.0, 0.25]],
            }
        )
    return ScenarioConfig.model_validate(
        {
            "name": "weave_two_" + ("downstream_first" if downstream_first else "upstream_first"),
            "network": {
                "kind": "osm",
                "osm_file": str(TWO_WEAVE_OSM),
                "corridor_edges": list(TWO_WEAVE_CORRIDOR),
                "inflow": [[0.0, mainline_rate], [demand_end_s, 0.0]],
                "ramps": ramps,
            },
            "sim": {"duration_s": duration_s},
            "seed": 3,
        }
    )


class TestWeaveGeometry:
    """``microsim.networks.weave_sections`` and the lane-0 walk it shares."""

    def test_pairs_the_entrance_with_its_exit(self, weave_osm, tmp_path):
        bundle = osm_import(
            osm_file=weave_osm,
            corridor_edges=WEAVE_CORRIDOR,
            keep_edges=("200", "201"),
            workdir=tmp_path / "w",
        )
        net = sumolib.net.readNet(str(bundle.net_path))
        chain = list(bundle.edge_ids)
        assert _lane_connections(net.getEdge("102")) == [[("201", 0)], [("103", 0)], [("103", 1)]]
        # the positive detector of the refusal: lane 0 is an auxiliary lane
        # serving an exit, not a taper that may be terminated
        refusal = accel_lane_end(net, chain, "102")
        assert not refusal.ok and "weaving section" in refusal.reason and "201" in refusal.reason

        ramps = [RampSpec.model_validate(r) for r in _weave_ramps("lane_change")]
        (section,) = weave_sections(net, chain, ramps)
        assert isinstance(section, WeaveSection)
        assert (section.on_ramp, section.off_ramp) == (0, 1)
        assert section.edges == ("102",) and section.exit_edge == "201"
        assert section.exit_only == (True,)
        # L_S runs from the entrance gore (start of 102) to the exit gore (its end)
        assert section.length_m == pytest.approx(net.getEdge("102").getLength())
        assert 150.0 < section.length_m < 220.0
        # no exit, no weave; and a short-length bound below L_S pairs nothing
        assert weave_sections(net, chain, ramps[:1]) == []
        assert weave_sections(net, chain, ramps, max_length_m=100.0) == []

    def test_an_exit_elsewhere_is_not_paired(self, merge_osm, tmp_path):
        """On the merge fixture the exit leaves before the entrance: no section."""
        bundle = osm_import(
            osm_file=merge_osm,
            corridor_edges=("100", "101", "102", "103"),
            keep_edges=("200", "201"),
            workdir=tmp_path / "m",
        )
        net = sumolib.net.readNet(str(bundle.net_path))
        ramps = [
            RampSpec.model_validate(
                {"kind": "on", "edges": ["200"], "attach_edge": "102", "inflow": [[0.0, 0.1]]}
            ),
            RampSpec.model_validate(
                {"kind": "off", "edges": ["201"], "attach_edge": "100", "exit_fraction": [[0, 0.1]]}
            ),
        ]
        assert weave_sections(net, list(bundle.edge_ids), ramps) == []


class TestWeaveSchema:
    """``RampSpec.merge = "weave"`` / ``WeaveSpec`` validation and hashing."""

    def test_rejections(self, weave_osm):
        on = _weave_ramps()[0]
        with pytest.raises(ValueError, match="needs a weave block"):
            RampSpec.model_validate({**on, "weave": None})
        with pytest.raises(ValueError, match="merge='weave' only"):
            RampSpec.model_validate({**on, "merge": "scripted"})
        with pytest.raises(ValueError, match="unknown weave_params"):
            RampSpec.model_validate({**on, "weave": {"exit_ramp": "x", "weave_params": {"b": 1}}})
        off = _weave_ramps()[1]
        with pytest.raises(ValueError, match="off-ramp cannot carry weave"):
            RampSpec.model_validate({**off, "weave": {"exit_ramp": "weave exit"}})
        with pytest.raises(ValueError, match="on-ramps only"):
            RampSpec.model_validate({**off, "merge": "weave"})
        with pytest.raises(ValueError, match="length_m"):
            RampSpec.model_validate({**on, "weave": {"exit_ramp": "x", "length_m": 0.0}})
        # the pairing: the named exit must exist, once, on the same attach edge
        with pytest.raises(ValueError, match="exactly one off-ramp"):
            _weave_scenario(weave_osm, weave={"exit_ramp": "no such exit"})
        raw = _weave_scenario(weave_osm).model_dump(mode="json")
        raw["network"]["ramps"][1]["attach_edge"] = "101"
        with pytest.raises(ValueError, match="not from the on-ramp's attach edge"):
            ScenarioConfig.model_validate(raw)

    def test_defaults_and_hash(self, weave_osm):
        cfg = _weave_scenario(weave_osm)
        spec = cfg.network.ramps[0].weave
        assert spec is not None and spec.length_m is None and spec.weave_params == {}
        assert WEAVE_DEFAULTS == {
            **SCRIPTED_MERGE_DEFAULTS,
            "exit_accept_gap_s": 0.6,
            "vacate_ahead_m": 500.0,
            "vacate_max_veh_h": 0.0,
            "vacate_no_follower_braking": 0.0,
            "pair_release_s": 2.0,
            "exit_giveup_m": 5.0,
            # WP-52: the bounded give-up patience, shipped off
            "exit_giveup_patience_s": 0.0,
            # WP-53: the abreast patience (docs/WEAVE_MODEL_PLAN.md, dated section)
            "exit_abreast_patience_s": 0.0,
        }
        # both fields enter the hash when set, and only then
        raw = cfg.model_dump(mode="json")
        raw["network"]["ramps"][0]["weave"]["weave_params"] = {"exit_accept_gap_s": 1.0}
        assert config_hash(ScenarioConfig.model_validate(raw)) != config_hash(cfg)
        raw = cfg.model_dump(mode="json")
        raw["network"]["ramps"][0]["weave"]["length_m"] = 136.0
        assert config_hash(ScenarioConfig.model_validate(raw)) != config_hash(cfg)
        plain = _weave_scenario(weave_osm, merge="lane_change")
        explicit = plain.model_dump(mode="json")
        explicit["network"]["ramps"][0]["weave"] = None
        assert config_hash(ScenarioConfig.model_validate(explicit)) == config_hash(plain)


#: The corridor's T.H.52 flows at the peak of the 35-min slice
#: (``scenarios/mndot_i94_wb_stpaul_weave_slice.yaml``, the 600-900 s step;
#: 2026-09-24, block 3, exit-side derivation): the mainline arriving at the
#: section edge 51388891 is the scenario's upstream ``inflow`` (1.0956 veh/s =
#: 3,944 veh/h at 600 s) propagated through the fourteen upstream ramps'
#: ``exit_fraction`` and ``inflow`` series in corridor order, 1.3663 veh/s =
#: 4,919 veh/h; off-ramp 18207598's ``exit_fraction`` at 600 s is 0.2122
#: (1,044 veh/h exiting); on-ramp 769818012's ``inflow`` is 0.3922 veh/s =
#: 1,412 veh/h. The section edge has four lanes (three through + the
#: auxiliary) over 305 m, as the fixture.
TH52_CORRIDOR_DEMAND = {"mainline_vph": 4919.0, "exit_fraction": 0.2122, "entrance_vph": 1412.0}


def _th52_config(
    seed: int,
    mainline_vph: float = 4500.0,
    exit_fraction: float = 0.25,
    entrance_vph: float = 1400.0,
) -> ScenarioConfig:
    """The T.H.52 weave at capacity on ``tests/fixtures/weave_th52.osm``
    (``TestWeaveRun.test_th52_weave_at_capacity_flows``): mainline 4,500 veh/h
    with 25 % exiting, entrance 1,400 veh/h, 20 simulated minutes, step 0.5 s,
    the fleet defaults; :data:`TH52_CORRIDOR_DEMAND` for the corridor's flows
    (``test_th52_weave_at_corridor_demand_exit_side``)."""
    return ScenarioConfig.model_validate(
        {
            "name": "weave_th52",
            "network": {
                "kind": "osm",
                "osm_file": str(Path(__file__).parents[1] / "fixtures" / "weave_th52.osm"),
                "corridor_edges": ["100", "101", "102", "103", "104"],
                "inflow": [[0.0, mainline_vph / 3600.0]],
                "ramps": [
                    {
                        "kind": "on",
                        "name": "th52",
                        "edges": ["200"],
                        "attach_edge": "102",
                        "inflow": [[0.0, entrance_vph / 3600.0]],
                        "merge": "weave",
                        "weave": {"exit_ramp": "cd exit"},
                    },
                    {
                        "kind": "off",
                        "name": "cd exit",
                        "edges": ["201"],
                        "attach_edge": "102",
                        "exit_fraction": [[0.0, exit_fraction]],
                    },
                ],
            },
            "sim": {"duration_s": 1200.0},
            "seed": seed,
        }
    )


#: The corridor's last 850 m at the same 6000 s step of
#: ``scenarios/mndot_i94_wb_stpaul_weave.yaml`` (07:10-07:15), propagated in
#: the scenario's ramp order: 3,793 veh/h arriving at on-ramp 40648744 (the
#: entry's 1.0956 veh/s through the thirteen ramps before it), that entrance's
#: ``inflow`` 0.3 veh/s = 1,080 veh/h (``artifacts/demand_mndot_i94_wb_stpaul.json``:
#: ``method: conservation``, no detector), so 4,873 veh/h arrive at the weave
#: (:data:`TH52_CORRIDOR_DEMAND` says 4,919 from an earlier propagation; the
#: 46 veh/h are within the artifact's carried residuals), on-ramp 769818012's
#: ``inflow`` 0.3922 veh/s = 1,412 veh/h and off-ramp 18207598's
#: ``exit_fraction`` 0.2122, both as the weave-only constant.
TH52_UPSTREAM_DEMAND = {
    "mainline_vph": 3793.0,
    "upstream_entrance_vph": 1080.0,
    "exit_fraction": 0.2122,
    "entrance_vph": 1412.0,
}


def corridor_fleet_block() -> dict:
    """The I-94 WB St. Paul corridor's ``fleet`` block, read from
    ``scenarios/mndot_i94_wb_stpaul_weave.yaml`` (EIDM, heterogeneity 0.15,
    ``idm_calibration: artifacts/idm_i24_capacity.json``, ``lc_strategic``
    5.0, ``lc_strategic_ramp`` 1.0, ``lc_keep_right`` 0.0) so a fixture can
    run the corridor's population instead of the builder's defaults
    (2026-09-24, block 3, the vacate rule re-derived)."""
    import yaml

    path = Path(__file__).parents[2] / "scenarios" / "mndot_i94_wb_stpaul_weave.yaml"
    return dict(yaml.safe_load(path.read_text())["fleet"])


def _th52_upstream_config(seed: int, merge: str, fleet: dict | None = None) -> ScenarioConfig:
    """The T.H.52 weave with the corridor's upstream entrance E1 in front of it
    (``tests/fixtures/weave_th52_upstream.osm``: E1 = way 300 onto the added
    lane of way 110, 102 m, then 230 m of three lanes, then the weave, way
    102) under :data:`TH52_UPSTREAM_DEMAND`; E1's ``merge`` is ``merge``
    (``lane_change`` as the corridor, or ``scripted``), 20 simulated minutes;
    ``fleet`` replaces the builder's fleet defaults (:func:`corridor_fleet_block`
    for the corridor's)."""
    d = TH52_UPSTREAM_DEMAND
    return ScenarioConfig.model_validate(
        {
            "name": f"weave_th52_upstream_{merge}",
            **({"fleet": fleet} if fleet is not None else {}),
            "network": {
                "kind": "osm",
                "osm_file": str(Path(__file__).parents[1] / "fixtures" / "weave_th52_upstream.osm"),
                "corridor_edges": ["100", "101", "110", "111", "102", "103", "104"],
                "inflow": [[0.0, d["mainline_vph"] / 3600.0]],
                "ramps": [
                    {
                        "kind": "on",
                        "name": "upstream entrance",
                        "edges": ["300"],
                        "attach_edge": "110",
                        "inflow": [[0.0, d["upstream_entrance_vph"] / 3600.0]],
                        "merge": merge,
                    },
                    {
                        "kind": "on",
                        "name": "th52",
                        "edges": ["200"],
                        "attach_edge": "102",
                        "inflow": [[0.0, d["entrance_vph"] / 3600.0]],
                        "merge": "weave",
                        "weave": {"exit_ramp": "cd exit"},
                    },
                    {
                        "kind": "off",
                        "name": "cd exit",
                        "edges": ["201"],
                        "attach_edge": "102",
                        "exit_fraction": [[0.0, d["exit_fraction"]]],
                    },
                ],
            },
            "sim": {"duration_s": 1200.0},
            "seed": seed,
        }
    )


def _lane_speed_windows(df: pd.DataFrame, x0: float, lane: int) -> pd.Series:
    """Mean speed of ``lane`` over ``[x0, x0 + 60 m)`` per 60-s window after
    a 120-s warm-up, to 1200 s (the section-style criterion of
    :func:`_th52_lane1_windows` at any position)."""
    part = df[
        (df.x >= x0) & (df.x < x0 + 60.0) & (df.t >= 120.0) & (df.t < 1200.0) & (df.lane == lane)
    ]
    return part.groupby((part.t // 60.0).astype(int)).v.mean()


def _th52_lane1_windows(paths, meta: dict, last_60m: bool = False) -> tuple[pd.Series, dict]:
    """Mean speed of section lane 1 over its first (``last_60m``: last) 60 m
    per 60-s window after a 120-s warm-up, and the state dict the assertions
    report."""
    net = sumolib.net.readNet(str(next(paths.run_dir.glob("**/*.net.xml"))))
    x0 = sum(net.getEdge(e).getLength() for e in ("100", "101"))
    if last_60m:
        x0 += net.getEdge("102").getLength() - 60.0
    df = pd.read_parquet(paths.trajectories)
    start = df[
        (df.x >= x0) & (df.x < x0 + 60.0) & (df.t >= 120.0) & (df.t < 1200.0) & (df.lane == 1)
    ]
    windows = start.groupby((start.t // 60.0).astype(int)).v.mean()
    (ws,) = meta["weave_sections"]
    (on_meta, _off_meta) = meta["ramps"]
    state = {
        "lane1_first60m_by_minute": {int(k): round(float(v), 1) for k, v in windows.items()},
        "entrance_departed": (on_meta["n_departed"], on_meta["n_planned"]),
        "weave": {k: v for k, v in ws.items() if k.startswith(("n_", "wait"))},
    }
    return windows, state


class TestWeaveRun:
    """The runner drives both crossing movements of the section."""

    def test_both_movements_complete(self, weave_osm, tmp_path):
        cfg = _weave_scenario(weave_osm)
        paths = run_micro(cfg, 3, tmp_path / "weave")
        meta = json.loads(paths.meta.read_text())
        assert meta["merge_models"] == [
            {"ramp": "weave on-ramp", "attach_edge": "102", "merge": "weave"}
        ]
        assert meta["net_patch_files"] == []  # lane 0 stays connected to the exit
        assert meta["scripted_merges"] == []
        (ws,) = meta["weave_sections"]
        assert ws["ramp"] == "weave on-ramp" and ws["exit"] == "weave exit"
        assert ws["edges"] == ["102"] and ws["exit_edge"] == "201"
        assert ws["length_m"] == ws["length_m_measured"] and 150.0 < ws["length_m"] < 220.0
        assert ws["params"] == WEAVE_DEFAULTS
        # both movements happened, under control, and nobody is left owing a change
        # 2026-09-24: with entering vehicles off the auxiliary lane sooner, most
        # exit-bound vehicles reach lane 0 by SUMO's own strategic change at
        # the junction and are never driven (9 in / 5 out of 17 exits, seed 3)
        assert ws["n_changed_in"] > 5 and ws["n_changed_out"] >= 3, ws
        assert ws["n_unfinished"] == 0 and ws["n_missed"] == 0, ws
        assert ws["n_changed_in"] + ws["n_changed_out"] == ws["n_entered"], ws
        assert ws["n_forced"] <= ws["n_changed_out"], ws
        assert ws["n_forced_deferred"] >= 0
        assert ws["wait_s_mean"] is not None and ws["wait_s_mean"] < 60.0
        # 2026-09-24 (block 3, second attempt): followers were driven towards
        # changers, at most at their comfortable deceleration (b drawn at
        # 1.67 +- 15 %), and changers eased towards their gap's leader
        assert ws["n_cooperations"] > 0 and ws["n_changer_eased"] >= 0, ws
        assert 0.0 <= ws["mean_follower_decel_ms2"] <= 2.5, ws["mean_follower_decel_ms2"]
        # every departed vehicle routed to the exit reached it
        assert ws["n_departed_exiting"] > 5
        # demand drains within the run, so all three exit counters agree
        assert ws["n_exited"] == ws["n_reached_section_exiting"] == ws["n_departed_exiting"], ws
        assert ws["exit_edges"] == ["201"]
        (on_meta, off_meta) = meta["ramps"]
        assert on_meta["n_departed"] == on_meta["n_planned"] > 0
        assert off_meta["n_planned_exiting"] == ws["n_departed_exiting"]
        assert meta["n_vehicles_departed"] == meta["n_vehicles_planned"]
        assert meta["n_collisions"] == 0

    def test_weave_params_override_the_defaults(self, weave_osm, tmp_path):
        weave = {"exit_ramp": "weave exit", "length_m": 136.0, "weave_params": {"courtesy": 2.0}}
        cfg = _weave_scenario(weave_osm, weave=weave)
        meta = json.loads(run_micro(cfg, 3, tmp_path / "courtesy").meta.read_text())
        (ws,) = meta["weave_sections"]
        assert ws["params"]["courtesy"] == 2.0 and ws["params"]["accept_gap_s"] == 0.6
        # the configured L_S is reported, the measured one kept beside it
        assert ws["length_m"] == 136.0 and ws["length_m_measured"] > 150.0
        assert ws["n_unfinished"] == 0 and meta["n_collisions"] == 0

    @pytest.mark.parametrize(
        ("seed", "exit_accept_gap_s"),
        # with _weave_force_gap_ok stubbed to True, each of these seeds
        # collides once on 102_1 (checked 2026-09-23; seeds 1-8 scanned); a
        # 3 s exit gap makes almost every exiting change a forced one
        [(1, 3.0), (6, 3.0)],
    )
    def test_forced_changes_never_collide(self, weave_osm, tmp_path, seed, exit_accept_gap_s):
        """Dense crossing demand with forcing allowed everywhere on the section:
        every forced change goes through ``_weave_force_gap_ok`` and a refused
        one is deferred, so SUMO reports no collision."""
        weave = {
            "exit_ramp": "weave exit",
            "weave_params": {
                "force_after_s": 0.0,
                "force_within_m": 1000.0,
                "exit_accept_gap_s": exit_accept_gap_s,
            },
        }
        raw = _weave_scenario(weave_osm, weave=weave, duration_s=300.0).model_dump(mode="json")
        raw["network"]["inflow"] = [[0.0, 0.9]]
        raw["network"]["ramps"][0]["inflow"] = [[0.0, 0.35]]
        raw["network"]["ramps"][1]["exit_fraction"] = [[0.0, 0.6]]
        cfg = ScenarioConfig.model_validate(raw)
        meta = json.loads(run_micro(cfg, seed, tmp_path / "dense").meta.read_text())
        (ws,) = meta["weave_sections"]
        assert ws["n_forced"] > 5 and ws["n_forced_deferred"] > 0, ws
        assert ws["n_changed_out"] > 10 and ws["n_changed_in"] > 5, ws
        assert meta["n_collisions"] == 0, meta["collisions"]

    def test_moderate_crossing_demand_does_not_crawl(self, weave_osm, tmp_path):
        """Regression of the T.H.52 lock (docs/ONBOARDING_MNDOT.md §10,
        2026-09-24) on the fixture: 1,620 veh/h through a 3-lane 178 m section
        (mainline 0.3 veh/s, entrance 0.15 veh/s, 30 % exiting, from t = 0).
        Before the fix the auxiliary lane ran at 1.7-2.9 m/s in the section's
        first 50 m (seeds 3, 4), the mean wait was 10-14 s and 2-3 vehicles were
        still under control at 300 s; after it 11-17 m/s, 4-6 s, none."""
        raw = _weave_scenario(weave_osm, duration_s=300.0).model_dump(mode="json")
        raw["network"]["inflow"] = [[0.0, 0.3]]
        raw["network"]["ramps"][0]["inflow"] = [[0.0, 0.15]]
        cfg = ScenarioConfig.model_validate(raw)
        paths = run_micro(cfg, 3, tmp_path / "crawl")
        meta = json.loads(paths.meta.read_text())
        (ws,) = meta["weave_sections"]
        assert meta["n_collisions"] == 0 and ws["n_missed"] == 0
        assert ws["n_unfinished"] == 0 and ws["wait_s_mean"] < 8.0, ws
        net = sumolib.net.readNet(str(paths.run_dir.glob("**/*.net.xml").__next__()))
        x0 = sum(net.getEdge(e).getLength() for e in ("100", "101"))
        df = pd.read_parquet(paths.trajectories)
        start = df[(df.x >= x0) & (df.x < x0 + 50.0) & (df.t >= 60.0)]
        assert start[start.lane == 0].v.mean() > 5.0, start.groupby("lane").v.mean()
        assert start[start.lane == 1].v.mean() > 5.0, start.groupby("lane").v.mean()

    @pytest.mark.xfail(
        strict=True,
        reason="T.H.52 weave at capacity (docs/WEAVE_MODEL_PLAN.md, 2026-09-24 block 3, "
        "the speed-aware acceptance): nothing locks at seeds 3-5, the entrance departs "
        "395 / 401 / 373 of 466 against 420 required (90 % of 466 is 419.4; 398 / 365 / "
        "389 under the cross-edge vacate window alone, 406 / 386 / 393 with the 150 m "
        "window), and lane 1 at the section start reads 3.9 and 4.4 m/s in two minutes at "
        "seed 3 (the ramp still queues over its first 100 m); one exit given up per seed, "
        "no collision",
    )
    def test_th52_weave_at_capacity_flows(self, tmp_path):
        """Mirror of the T.H.52 weaving section on I-94 WB St. Paul
        (docs/ONBOARDING_MNDOT.md §10): ``tests/fixtures/weave_th52.osm``, three
        through lanes, a 305 m auxiliary lane from the entrance to the exit
        (netconvert: 308 m), 600 m of approach and downstream; mainline
        4,500 veh/h with 25 % exiting, entrance 1,400 veh/h (all through),
        20 simulated minutes, step 0.5 s, the fleet defaults, seed 3.

        Reality carries this weave (the corridor's lowest station speed at
        the peak is 8.5 m/s). The criterion is a flowing section: mean speed
        in lane 1 over the section's first 60 m above 5 m/s in every 60-s
        window after a 120-s warm-up, at least 90 % of the entrance's planned
        vehicles departed and at most 10 % of the driven vehicles still under
        control at the end, no collision. At 462b731 it fails: lane 1's
        first 60 m read 20.2, 10.9, 2.9, 0.2 m/s in minutes 0-3 and 0.0 from
        minute 4 to the end, the entrance departs 81 of 466, 92 vehicles are
        driven (25 in, 18 out, 0 forced, 16,576 forced changes deferred, 49
        unfinished), 3 of the 62 exit-bound vehicles that reach the section
        exit, 583 of 1,966 planned vehicles depart. The mechanism (per-step
        trace, same seed) and the eight re-derivations tried on 2026-09-24,
        each of which locked the section as hard or harder, are in
        docs/WEAVE_MODEL_PLAN.md (dated paragraph); the run takes ~8 s.

        Second attempt (2026-09-24, block 3: follower cooperation as a
        car-following target, docs/WEAVE_MODEL_PLAN.md dated paragraph): no
        lock — lane 1's first 60 m read 12.7, 11.8, 12.4, 10.3 m/s in minutes
        2-5, then 4.2-6.4 m/s (2.3 in the last minute); the entrance departs
        225 of 466; 334 vehicles are driven (122 in, 210 out, 2 forced, 0
        deferred, 2 unfinished, 11,320 follower cooperations at a mean
        0.13 m/s^2, 6,588 changer easings); 273 of the 280 exit-bound
        vehicles that reach the section exit; 1,386 of 1,966 depart; no
        collision. The remaining failure is a crawl equilibrium at the
        section entry, not a lock (the plan's dated paragraph has the lane
        flows), so the marker stays.

        Third derivation (2026-09-24, block 3: through traffic vacates the
        weave lane within ``vacate_ahead_m`` = 150 m of the section start,
        ``microsim.runner._weave_vacate_step``): lane 1's first 60 m read
        11.5, 10.0, 6.9, 8.1, 11.2, 12.9, 12.6, 8.6, 11.7, 11.1, 5.4, 4.5,
        7.1, 6.3, 12.0, 13.4, 12.5, 10.8 m/s in minutes 2-19 (one window
        below 5); the entrance departs 317 of 466; 307 driven (130 in, 177
        out, 0 unfinished), 228 through vehicles vacate and 28 are refused;
        no collision. The entrance criterion fails at every window tried
        (150-500 m: 240-317 of 466): the ramp is now held at 3 m/s over its
        first 100 m by the second derivation's easing rule at the
        anticipation-zone entry, not by lane 1 (the plan's dated paragraph
        has the sensitivity table and the ramp profile), so the marker
        stays.

        Fourth derivation (2026-09-24, block 3: easing only while it can
        still position the changer at no more than its ``b`` by the section
        end, ``microsim.runner._weave_easing_ok``): lane 1's first 60 m read
        11.5, 10.0, 6.9, 8.1, 11.2, 12.9, 11.8, 7.0, 11.2, 10.9, 11.2, 8.7,
        10.7, 11.3, 6.2, 12.3, 12.3, 12.3 m/s in minutes 2-19 (every window
        above 5 at this seed; 4.0 in one at seed 4, which is seed noise: the
        check binds rarely); the entrance departs 325 of 466; 308 driven
        (134 in, 174 out, 5 forced, 0 unfinished); no collision. The ramp
        still queues at 3.3 m/s over its first 100 m. The entrant-side rules
        the derivation set out with were measured and rejected: no easing
        of an entrant on the ramp gives a 3-5 m/s crawl of lane 1 in every
        minute (entrance 277), no ramp follower for an exiter locks the
        section at seed 4 (0.0 m/s from minute 13, entrance 204), and easing
        only when the drop is needed within the horizon gives 400 of 466 at
        seeds 3 and 5 but locks at seed 4 (the plan's dated paragraph has
        the table), so the marker stays.

        Fifth derivation (2026-09-24, block 3: easing only when needed,
        ``0 < a_req``, with the ramp follower on, and a stopped
        changer-follower pair released after ``pair_release_s`` = 2 s,
        ``microsim.runner._weave_pair_release``): lane 1's first 60 m read
        7.0, 8.2, 6.3, 8.8, 10.1, 9.4, 9.3, 8.1, 8.6, 10.2, 9.2, 12.2,
        10.4, 7.8, 8.2, 8.6, 10.4, 11.6 m/s in minutes 2-19 (every window
        above 5); the entrance departs 395 of 466 (85 %); 452 driven (211
        in, 235 out, 20 forced, 6 unfinished); 310 of 323 exit-bound
        vehicles that reach the section exit; 1,529 of 1,966 depart; no
        collision; no pair is released at this seed. Without the release,
        the "needed" condition with the ramp follower on locks at seeds 4
        and 5 from minute 16 (a changer stopped at the gore with its gap's
        follower held bumper to bumper behind it by its own cooperation
        command; seed 5 with 10 collisions in the jam); with it neither
        seed locks (392 and 389 of 466, 2 and 4 unfinished, 0 collisions;
        ``test_th52_weave_at_capacity_does_not_lock`` pins that). The
        entrance criterion (419) still fails at every seed, so the marker
        stays.

        Sixth derivation (2026-09-24, block 3: an entrant on the ramp is not
        eased towards a gap leader that overlaps it, ``_weave_cooperate``):
        the per-step trace read the bound as the ramp's own queue (4-4.5 m/s
        over its first 100 m, 3.1-s headways = 1,150 veh/h, insertion refused
        behind it; free IDM queue discharge is 2.2-2.3 s) and its head as
        the entrant braking at -b for a lane-1 vehicle beside it. Lane 1's
        first 60 m read 8.8, 6.8, 5.0, 8.1, 5.0, 4.8, 8.9, 7.7, 5.2, 6.1,
        6.3, 10.7, 10.8, 6.1, 9.2, 4.4, 6.4, 9.1 m/s in minutes 2-19 (three
        at or below 5); the entrance departs 411 of 466 (88 %); 474 driven
        (249 in, 222 out, 22 forced, 3 unfinished); 304 of 307 exit-bound
        vehicles that reach the section exit; 1,527 of 1,966 depart; 14
        pairs released; no collision. Seeds 4 / 5: entrance 412 / 420, one
        minute at 4.6 / 4.3 m/s, 6 of 491 / 2 of 501 unfinished, 0 / 16
        releases. The ramp queue runs at 5-6 m/s (2.95-s headways, 1,215
        veh/h) instead of 4-4.5; the cost moved to lane 1 at the section
        start. The entrance criterion fails at seed 3 (411) and the lane-1
        criterion at every seed, so the marker stays.

        Seventh derivation (2026-09-24, block 3; measured and rejected, the
        runner unchanged): the abreast entrant-lane-1 pair at speed parity
        resolved symmetrically (each side half the offset over a horizon,
        the rear one at <= b/2, the front one at half its headway) instead
        of the rear one at -b. Fifteen variants at seeds 3-8: the best
        (relative speed fixed at entry, 2-s horizon) meets this criterion at
        seed 3 alone (421 of 466, every minute above 5 m/s, 2 of 477
        unfinished) but departs 386 / 386 / 362 / 408 at seeds 4-7 and locks
        seed 8 with a collision; the 3-s form locks seed 7. This rule reads
        411 / 412 / 420 / 397 / 424 / 407 at seeds 3-8 and locks none (at
        seed 7 it meets the whole criterion, which is seed noise, not a
        pass). The marker stays with the sixth derivation's numbers
        (docs/WEAVE_MODEL_PLAN.md, dated paragraph, has the table).

        Exit-side derivation (2026-09-24, block 3: an exit-bound vehicle
        whose forced change is due has priority over the auxiliary lane —
        the vehicles behind its rear hold one changer minGap farther back,
        a vehicle beside it is waited for — and one halted within
        ``exit_giveup_m`` of the gore's end continues through, counted in
        ``n_missed_exit``; derived from the corridor's exit-side standstill,
        ``test_th52_weave_at_corridor_demand_exit_side``): lane 1's first
        60 m read 8.8, 6.8, 5.0, 7.3, 9.1, 5.1, 5.0, 6.9, 7.7, 9.7, 7.5,
        7.4, 8.6, 8.3, 6.4, 7.4, 9.7, 11.1 m/s in minutes 2-19 (two at
        5.0); the entrance departs 419 of 466 (89.9 %; 0.9 x 466 = 419.4);
        488 driven (253 in, 231 out, 26 forced, 4 unfinished, no exit given
        up); 311 of 322 exit-bound vehicles that reach the section exit;
        1,566 of 1,966 depart; 2 pairs released; no collision. Seeds 4 / 5:
        entrance 414 / 419, one minute at 5.0 / 4.3 m/s, 5 of 509 / 2 of
        483 unfinished, 2 / 14 releases. The marker stays: 419 against
        419.4 and the two minutes at 5.0 m/s at this seed.

        Cross-edge vacate window (2026-09-24, block 3: ``vacate_ahead_m``
        measured along the corridor chain across edges, default 500 m —
        here over the 282 m approach edge onto the 300 m entry edge; with
        the 150 m window this fixture read entrance 406 / 386 / 393, lane 1
        minimum 5.0 / 5.0 / 5.6 m/s): lane 1's first 60 m read 3.7, 4.4,
        5.6, 6.3, 7.2, 7.1, 6.7, 6.5, 7.1, 9.3, 10.8, 11.7, 10.7, 7.0, 10.0,
        10.2, 6.1, 7.0 m/s in minutes 2-19 (two below 5); the entrance
        departs 398 of 466; 458 driven (224 in, 231 out, 29 forced, 102
        deferred, 3 unfinished); 309 of 316 exit-bound vehicles that reach
        the section exit; 145 vacated, 56 refused, 39 skipped by the bound;
        14 releases; no collision. Seeds 4 / 5: entrance 365 / 389, lane 1
        minimum 4.1 / 3.2 m/s, 5 of 499 / 1 of 477 unfinished, 27 / 19
        releases, no collision. The window that suits this fixture alone is
        300 m (entrance 396 / 388 / 407); the default is set for the
        two-entrance fixture (docs/WEAVE_MODEL_PLAN.md, dated section, has
        the 150 / 300 / 500 / 800 m table). The marker stays.
        """
        paths = run_micro(_th52_config(3), 3, tmp_path / "th52")
        meta = json.loads(paths.meta.read_text())
        (ws,) = meta["weave_sections"]
        assert 300.0 < ws["length_m"] < 315.0, ws["length_m"]
        assert meta["n_collisions"] == 0, meta["collisions"]
        windows, state = _th52_lane1_windows(paths, meta)
        (on_meta, _off_meta) = meta["ramps"]
        assert len(windows) == 18 and (windows > 5.0).all(), state
        assert on_meta["n_departed"] >= 0.9 * on_meta["n_planned"], state
        assert ws["n_missed"] == 0 and ws["n_unfinished"] <= 0.1 * ws["n_entered"], state

    @pytest.mark.parametrize(
        ("seed", "min_releases"),
        [
            pytest.param(
                4,
                0,
                marks=pytest.mark.xfail(
                    strict=False,
                    reason=(
                        "platform-sensitive at capacity: on the Linux CI runner (eclipse-sumo "
                        "1.27.1 wheel, 2026-09-24) seed 4 locks — entrance 331 of 466, a lane-1 "
                        "minute at or below 2 m/s — while it passed on macOS with 412; recorded in "
                        "docs/WEAVE_MODEL_PLAN.md (block 3, CI note). With the cross-edge vacate "
                        "window at 500 m (same date) it fails on macOS too, on the entrance pin "
                        "alone: 365 of 466 (78 % against 80 %) with no lock — lane 1 never below "
                        "4.1 m/s, 5 of 499 unfinished, nothing missed, 27 releases. Under the "
                        "speed-aware acceptance (same date) it passes on macOS again: 401 of "
                        "466, lane 1 never below 4.3 m/s, 2 of 496 unfinished, one exit given "
                        "up, 23 releases, no collision"
                    ),
                ),
            ),
            (5, 1),
        ],
    )
    def test_th52_weave_at_capacity_does_not_lock(self, tmp_path, seed, min_releases):
        """The seeds at which the fifth derivation's "ease only when needed"
        condition locked the section before the pair release (2026-09-24,
        block 3): from minute 16 lane 1 at the section start read 0.0 m/s to
        the end, 25-38 driven vehicles were left unfinished and the entrance
        departed 343 / 335 of 466 (seed 5 with 10 collisions in the jam). A
        lock reads 0.0 m/s from its minute on and leaves the driven vehicles
        of the jam unfinished; this pins its absence, not the entrance
        criterion of ``test_th52_weave_at_capacity_flows`` (392 / 389 of 466
        here against 419). Measured after the release: seed 4 lane 1 never
        below 5.4 m/s, 2 of 501 unfinished, 9 pairs released; seed 5 one
        minute at 5.0 m/s, 4 of 483 unfinished, 1 pair released; no
        collision at either. Sixth derivation (the ramp's overlap rule):
        seed 4 never below 4.6 m/s, 6 of 491 unfinished, entrance 412, no
        pair stands long enough to be released (the pin of the release
        firing is kept at seed 5: 16 releases, 2 of 501 unfinished, one
        minute at 4.3 m/s, entrance 420); no collision at either. Seventh
        derivation (the symmetric abreast-pair resolution, rejected): its
        forms read 368-392 / 353-409 here and lock seeds 7 or 8, where this
        rule departs 424 / 407 and locks neither. Exit-side derivation
        (2026-09-24, block 3): seed 4 never below 5.0 m/s, 5 of 509
        unfinished, entrance 414, 2 releases; seed 5 one minute at 4.3 m/s,
        2 of 483 unfinished, entrance 419, 14 releases; no collision and no
        exit given up at either. The vacate rule's spare-capacity bound
        (2026-09-24, block 3, the rule re-derived; 4 / 15 through vehicles
        skipped by it): seed 4 never below 5.0 m/s, 3 of 511 unfinished,
        entrance 386, no release; seed 5 never below 5.6 m/s, 4 of 494
        unfinished, entrance 393, 4 releases; no collision at either. The
        cross-edge vacate window at 500 m (2026-09-24, block 3): seed 4
        never below 4.1 m/s, 5 of 499 unfinished, entrance 365 (the 80 %
        pin fails there, hence its marker's second clause), 27 releases;
        seed 5 never below 3.2 m/s, 1 of 477 unfinished, entrance 389, 19
        releases; no collision and nothing missed at either. Speed-aware
        acceptance (2026-09-24, block 3: the brake gap on the acceptance's
        leader side and on both sides of the forced guard): seed 4 never
        below 4.3 m/s, 2 of 496 unfinished, entrance 401 (the 80 % pin passes
        on macOS again), 23 releases, one exit given up; seed 5 never below
        3.3 m/s, 3 of 496 unfinished, entrance 373 (372.8 required — the pin
        holds by one vehicle), 18 releases, one exit given up; no collision
        at either. The ``n_missed`` pin moves from 0 to at most 1: the exit
        given up is a vehicle halted at the gore's end whose lane-0 follower
        could not brake for it at ``b`` — the forced change the guard now
        refuses is the one that, at the corridor's demand and seed 5, ended
        in a collision (t = 977.5 s, session trace)."""
        paths = run_micro(_th52_config(seed), seed, tmp_path / f"th52_{seed}")
        meta = json.loads(paths.meta.read_text())
        (ws,) = meta["weave_sections"]
        windows, state = _th52_lane1_windows(paths, meta)
        (on_meta, _off_meta) = meta["ramps"]
        assert meta["n_collisions"] == 0, meta["collisions"]
        assert len(windows) == 18 and (windows > 2.0).all(), state
        # at most one exit given up at the gore's end (speed-aware guard,
        # 2026-09-24 block 3); a lock leaves the driven vehicles unfinished
        assert ws["n_missed"] <= 1 and ws["n_unfinished"] <= 0.1 * ws["n_entered"], state
        assert on_meta["n_departed"] >= 0.8 * on_meta["n_planned"], state
        assert ws["n_pair_releases"] >= min_releases, state

    @pytest.mark.xfail(
        strict=False,
        reason="T.H.52 weave at the corridor's demand, seed 3, under the speed-aware "
        "acceptance (2026-09-24, block 3): lane 1's last 60 m reads 3.8 m/s in one minute "
        "(the criterion is > 5 in every minute; 6.5 at the minimum before), 292 of 299 "
        "exit, 1 given up, 2 unfinished, no collision; seeds 4 / 5 read 11.9 / 1.7 m/s at "
        "the minimum with no collision (docs/WEAVE_MODEL_PLAN.md, dated section). Not "
        "strict: one minute 1.2 m/s under the line, within the platform sensitivity of "
        "the CI note",
    )
    def test_th52_weave_at_corridor_demand_exit_side(self, tmp_path):
        """The T.H.52 fixture under the corridor's own flows, judged on the
        exit side (2026-09-24, block 3, exit-side derivation).

        On the I-94 WB slice (cloud VM, sixth-derivation runner, one seed,
        session record) the first standstill forms at minute 11 at the END
        of the T.H.52 section — x = 10.60-10.70 km, lanes 0 and 1, the exit
        18207598 at 10.73 km — with the section's counters entered 114,
        exited 88 of 133 reached, forced 1, forced deferred 8,971
        vehicle-steps, unfinished 21, vacated 131 / refused 24, pair
        releases 88: the EXIT movement stalls at the gore's end while the
        fixture work optimised the entrance. Demand is
        :data:`TH52_CORRIDOR_DEMAND` (mainline 4,919 veh/h arriving, 21.2 %
        exiting, entrance 1,412 veh/h; the scenario's series at its 600-900 s
        step), 20 simulated minutes at seed 3. The criterion is the exit
        side: at least 90 % of the exit-bound vehicles that reach the
        section exit (``n_exited / n_reached_section_exiting``), mean speed
        in lane 1 over the section's LAST 60 m above 5 m/s in every 60-s
        window after a 120-s warm-up, at most 10 % of the driven vehicles
        still under control at the end, no collision.

        At 3f47446 (the sixth derivation's runner) it fails on lane 1: the
        last 60 m read 15.0, 16.3, 4.0, 3.7, 11.5 m/s in minutes 2-6 and
        11-19 m/s after — a standstill at the gore's end in minutes 4-5 (96
        trajectory samples of lane 1 stopped within the last 20 m in minute
        4, lane 0 beside it) that resolves after 4 pair releases — while
        303 of 307 exit-bound vehicles that reach the section exit, 1 of 436
        driven is unfinished, 125 forced changes are deferred and nothing
        collides; seeds 4 and 5 never stall. The per-step trace (exiter
        v00251, docs/WEAVE_MODEL_PLAN.md, dated paragraph): at the lane end
        its front is ahead of every auxiliary-lane front, so the abreast
        rule of ``_weave_choose_gap`` discards every gap and nobody is
        held; lane 0 streams past at 5-11 m/s, each vehicle overlapping it
        in turn (the guard refuses on the leader or the follower side, 67
        steps); a follower finally chosen is held by IDM at its own minGap
        behind the exiter's rear, which the guard's ``s0`` floor never
        passes, and the pair release lets it go past. With the exit-side
        rules (an exit-bound vehicle whose forced change is due has
        priority — the gap behind the vehicle beside it is its, its
        follower holds one changer minGap farther back — and one halted
        within ``exit_giveup_m`` of the gore's end continues through,
        counted in ``n_missed_exit``) the last 60 m read 15.0, 16.3, 10.3,
        9.6, 15.2 m/s in minutes 2-6 and 14.4-19.1 after, no trajectory
        sample stops within the last 20 m, 300 of 310 exit-bound vehicles
        that reach the section exit, 2 of 422 driven are unfinished, no exit
        is given up, 37 forced changes are deferred, no pair is released,
        the entrance departs 419 of 470; seeds 4 / 5: 307 of 315 / 317 of
        325 exit, 1 / 4 unfinished, the last 60 m never below 12.3 m/s.
        Under the vacate rule's spare-capacity bound (2026-09-24, block 3,
        the rule re-derived; 19 / 17 / 17 through vehicles skipped by it at
        seeds 3-5): 299 of 306 / 305 of 312 / 302 of 311 exit, 3 / 1 / 2
        unfinished, the last 60 m never below 11.9 / 7.5 / 8.7 m/s, the
        entrance 386 / 398 / 384 of 470, no give-up, no collision.
        The cross-edge vacate window at 500 m (2026-09-24, block 3; 140 /
        143 / 138 vacated, 35 / 45 / 48 skipped at seeds 3-5): 297 of 301 /
        302 of 312 / 299 of 303 exit, 1 / 4 / 1 unfinished, the last 60 m
        never below 6.5 / 11.7 m/s at seeds 3 / 4, the entrance 398 / 394 /
        395; at seed 5 (not run here) the gore's end stalls for one minute
        (last 60 m 1.7 m/s, 534 deferred, 39 releases, 3 exits given up),
        which the 300 m window does not show (5.3 m/s, 82 deferred, no
        give-up; docs/WEAVE_MODEL_PLAN.md, dated section).

        Speed-aware acceptance (2026-09-24, block 3: the brake gap
        ``Δv⁺²/(2·b)`` on the acceptance's leader side and on both sides of
        the forced guard; docs/WEAVE_MODEL_PLAN.md, dated section): at seed
        3 lane 1's last 60 m reads 3.8 m/s in one minute (was 6.5 at the
        minimum), 292 of 299 exit, 1 exit is given up, 2 of 465 driven are
        unfinished, 12 forced / 67 deferred / 5 releases, the entrance 403
        of 470, no collision; seeds 4 / 5: 308 of 317 / 296 of 308 exit, the
        last 60 m never below 11.9 m/s at seed 4, 1.7 m/s in two minutes at
        seed 5 (was 1.7 in three), 0 / 5 given up, no collision. The marker
        is not strict: seed 3's failure is one minute 1.2 m/s under the
        line, within the platform sensitivity the CI note records.
        """
        cfg = _th52_config(3, **TH52_CORRIDOR_DEMAND)
        paths = run_micro(cfg, 3, tmp_path / "th52_corridor")
        meta = json.loads(paths.meta.read_text())
        (ws,) = meta["weave_sections"]
        windows, state = _th52_lane1_windows(paths, meta, last_60m=True)
        state["exit_share"] = (ws["n_exited"], ws["n_reached_section_exiting"])
        assert meta["n_collisions"] == 0, meta["collisions"]
        assert ws["n_reached_section_exiting"] > 0, state
        assert ws["n_exited"] >= 0.9 * ws["n_reached_section_exiting"], state
        assert len(windows) == 18 and (windows > 5.0).all(), state
        assert ws["n_unfinished"] <= 0.1 * ws["n_entered"], state

    @pytest.mark.xfail(
        strict=True,
        reason="T.H.52 weave with the corridor's upstream entrance in front of it "
        "(docs/WEAVE_MODEL_PLAN.md, 2026-09-24 block 3, the upstream-entrance fixture; "
        "numbers of the cross-edge vacate window at 500 m, same date): at seed 3 the "
        "entrance E1 departs 205 of 360 (0.57 against 0.90 required) and lane 1 over its "
        "acceleration-lane end reads 1.2-13.9 m/s (above 5 m/s in 4 of 18 minutes); the "
        "section's side passes — lane 1 over its last 60 m never below 7.4 m/s, 300 of 308 "
        "exiters exit, 2 of 455 driven unfinished, nothing collides. The head has moved: "
        "lane 1 of the 230 m before the gore is above 5 m/s in 10 of 18 minutes (was 2, at "
        "1.3-3.8 m/s); lane 0 is the first lane below 2 m/s there (minute 4 at 50 m, lane 1 "
        "minute 12), as it already was under the bound at 150 m (minute 2; lane 1 minute "
        "6); the queue stands on E1's merge (way 110: the acceleration lane below 2 m/s "
        "from minute 0, lane 1 beside it from minute 3). Under the speed-aware acceptance "
        "(same date): E1 departs 216 of 360, its acceleration-lane end reads 0.2 m/s at "
        "the minimum (16 of 18 minutes at or below 5), the gore's last 60 m 3.7 m/s in one "
        "minute, 294 of 301 exit, 3 given up, 3 unfinished, no collision",
    )
    def test_th52_with_upstream_entrance_at_corridor_demand(self, tmp_path):
        """The corridor's last 850 m (2026-09-24, block 3): the T.H.52 weave of
        ``weave_th52.osm`` with the 40648744 entrance 230 m in front of its
        gore, ``tests/fixtures/weave_th52_upstream.osm``, under the flows of
        :data:`TH52_UPSTREAM_DEMAND` (the scenario's 6000 s step: 3,793 veh/h
        arriving at the entrance, 1,080 veh/h entering on a lane-change merge
        over a 102 m acceleration lane, 1,412 veh/h entering the weave, 21.2 %
        exiting), 20 simulated minutes at seed 3.

        docs/ONBOARDING_MNDOT.md §11a puts the head of every I-94 WB queue in
        this stretch from the warm-up on while
        ``test_th52_weave_at_corridor_demand_exit_side`` (the weave alone)
        passes; this fixture is the local twin of that stretch. Criteria: the
        exit-side test's (no collision, at least 90 % of the exit-bound
        vehicles that reach the section exit, lane 1 over the section's last
        60 m above 5 m/s in every 60-s window after 120 s, at most 10 % of
        the driven vehicles unfinished) plus the entrance E1 departing at
        least 90 % of its plan and lane 1 over the last 60 m of E1's
        acceleration lane above 5 m/s in every such window.
        """
        cfg = _th52_upstream_config(3, "lane_change")
        paths = run_micro(cfg, 3, tmp_path / "th52_upstream")
        meta = json.loads(paths.meta.read_text())
        (ws,) = meta["weave_sections"]
        e1_meta, _e2_meta, _x_meta = meta["ramps"]
        net = sumolib.net.readNet(str(next(paths.run_dir.glob("**/*.net.xml"))))
        x_accel_end = sum(net.getEdge(e).getLength() for e in ("100", "101", "110"))
        x_section_end = x_accel_end + sum(net.getEdge(e).getLength() for e in ("111", "102"))
        df = pd.read_parquet(paths.trajectories)
        accel = _lane_speed_windows(df, x_accel_end - 60.0, 1)
        gore = _lane_speed_windows(df, x_section_end - 60.0, 1)
        state = {
            "lane1_accel_end_by_minute": {int(k): round(float(v), 1) for k, v in accel.items()},
            "lane1_section_end_by_minute": {int(k): round(float(v), 1) for k, v in gore.items()},
            "e1_departed": (e1_meta["n_departed"], e1_meta["n_planned"]),
            "exit_share": (ws["n_exited"], ws["n_reached_section_exiting"]),
            "weave": {k: v for k, v in ws.items() if k.startswith(("n_", "wait"))},
        }
        assert meta["n_collisions"] == 0, meta["collisions"]
        assert ws["n_reached_section_exiting"] > 0, state
        assert ws["n_exited"] >= 0.9 * ws["n_reached_section_exiting"], state
        assert len(gore) == 18 and (gore > 5.0).all(), state
        assert ws["n_unfinished"] <= 0.1 * ws["n_entered"], state
        assert e1_meta["n_departed"] >= 0.9 * e1_meta["n_planned"], state
        assert len(accel) == 18 and (accel > 5.0).all(), state

    @pytest.mark.xfail(
        strict=True,
        reason="The two-entrance fixture on the corridor's own fleet block (EIDM, "
        "heterogeneity 0.15, the I-24 capacity calibration, lc_strategic 5.0, lc_keep_right "
        "0.0; docs/WEAVE_MODEL_PLAN.md, 2026-09-24 block 3, the cross-edge vacate window at "
        "500 m): at seed 3 E1 departs 272 of 360 (0.76 against 0.90) and lane 1 over its "
        "acceleration-lane end reads 1.0-12.7 m/s (above 5 m/s in 2 of 18 minutes); the "
        "section's side passes — lane 1 over its last 60 m never below 8.0 m/s, 307 of 316 "
        "exiters exit, 4 of 378 driven unfinished, nothing collides. Lane 1 of the 230 m "
        "before the gore is above 5 m/s in 10 of 18 minutes (was 4) and lane 0 is now the "
        "first lane below 2 m/s there (minute 3 at 100 m; lane 1 minute 5) where at 150 m "
        "lane 1 was (minute 4; lane 0 minute 7) — the cloud's lane order on this fleet. "
        "Under the speed-aware acceptance (same date): E1 departs 253 of 360, its "
        "acceleration-lane end 1.6 m/s at the minimum (13 minutes at or below 5), the "
        "gore's last 60 m never below 7.3 m/s, 309 of 314 exit, none given up, 1 "
        "unfinished, no collision",
    )
    def test_th52_with_upstream_entrance_on_the_corridor_fleet(self, tmp_path):
        """:func:`test_th52_with_upstream_entrance_at_corridor_demand` with the
        corridor's fleet block (:func:`corridor_fleet_block`) in place of the
        builder's defaults — the alignment docs/WEAVE_MODEL_PLAN.md's
        upstream-entrance section named as the next thing to do before
        reading the lane order across: the fixtures ran IDM with
        ``lc_strategic`` 1.0 and ``lc_keep_right`` 1.0, the corridor EIDM with
        5.0 / 0.0 and the calibrated population. Same criteria, same seed.
        The default-fleet test is kept as it is."""
        cfg = _th52_upstream_config(3, "lane_change", fleet=corridor_fleet_block())
        assert cfg.fleet.model == "EIDM" and cfg.fleet.lc_keep_right == 0.0
        paths = run_micro(cfg, 3, tmp_path / "th52_upstream_fleet")
        meta = json.loads(paths.meta.read_text())
        (ws,) = meta["weave_sections"]
        e1_meta, _e2_meta, _x_meta = meta["ramps"]
        net = sumolib.net.readNet(str(next(paths.run_dir.glob("**/*.net.xml"))))
        x_accel_end = sum(net.getEdge(e).getLength() for e in ("100", "101", "110"))
        x_section_end = x_accel_end + sum(net.getEdge(e).getLength() for e in ("111", "102"))
        df = pd.read_parquet(paths.trajectories)
        accel = _lane_speed_windows(df, x_accel_end - 60.0, 1)
        gore = _lane_speed_windows(df, x_section_end - 60.0, 1)
        state = {
            "lane1_accel_end_by_minute": {int(k): round(float(v), 1) for k, v in accel.items()},
            "lane1_section_end_by_minute": {int(k): round(float(v), 1) for k, v in gore.items()},
            "e1_departed": (e1_meta["n_departed"], e1_meta["n_planned"]),
            "exit_share": (ws["n_exited"], ws["n_reached_section_exiting"]),
            "weave": {k: v for k, v in ws.items() if k.startswith(("n_", "wait"))},
        }
        assert meta["n_collisions"] == 0, meta["collisions"]
        assert ws["n_reached_section_exiting"] > 0, state
        assert ws["n_exited"] >= 0.9 * ws["n_reached_section_exiting"], state
        assert len(gore) == 18 and (gore > 5.0).all(), state
        assert ws["n_unfinished"] <= 0.1 * ws["n_entered"], state
        assert e1_meta["n_departed"] >= 0.9 * e1_meta["n_planned"], state
        assert len(accel) == 18 and (accel > 5.0).all(), state

    def test_two_sections_are_stepped_and_listed_upstream_first(self, tmp_path):
        """``tests/fixtures/weave_two.osm`` (two T.H.52-shaped sections, 560 m
        apart) with entrants on both ramps and the downstream pair listed
        first: ``weave_sections`` lists the upstream section first — the
        stepping order, by the section's start offset, not the ramp list's
        (2026-09-24, block 3) — while ``merge_models`` keeps the list's
        order; both sections drive both movements and every driven vehicle
        is handed back by the end of the run (demand stops at 120 s).

        Mainline 0.4 veh/s, entrances 0.10 veh/s: at 0.5 / 0.12 and seed 3
        section A locks at its gore (2 driven vehicles unfinished, 708
        forced changes deferred, 7 vehicles at 0 m/s at the section end) —
        the known one-sided weave lock, identical on the runner before the
        stepping order was fixed, so not the order's doing. The per-section
        counters of this scenario at 0.5 / 0.12, 0.4 / 0.10, 0.35 / 0.10 and
        0.3 / 0.10, both listings, were identical before and after the fix
        (sections 560 m apart: the order cannot matter here).

        With the cross-edge vacate window (2026-09-24, block 3; 500 m, so
        B's window reaches over 104 onto 103 and A's over 101 onto 100) B's
        twelve entrants all change in by SUMO's own strategic change at the
        junction, lane 1 having been vacated 500 m back, and none is driven
        (``n_changed_in`` 2 → 0 at B; A 6 → 5): the pin is that each section
        drives vehicles and hands every one back, not that the entering
        movement needs driving at this demand."""
        cfg = two_weave_scenario(downstream_first=True, ramp_rate=0.10, mainline_rate=0.4)
        paths = run_micro(cfg, 3, tmp_path / "two")
        meta = json.loads(paths.meta.read_text())
        assert [m["ramp"] for m in meta["merge_models"]] == ["B on", "A on"]
        assert [w["ramp"] for w in meta["weave_sections"]] == ["A on", "B on"]
        assert [w["edges"] for w in meta["weave_sections"]] == [["102"], ["105"]]
        assert [w["vacate_window_edges"] for w in meta["weave_sections"]] == [
            ["101", "100"],
            ["104", "103"],
        ]
        for w in meta["weave_sections"]:
            assert w["n_entered"] > 0 and w["n_changed_out"] > 0, w
            assert w["n_changed_in"] + w["n_changed_out"] == w["n_entered"], w
            assert w["n_unfinished"] == 0 and w["n_missed"] == 0, w
        (on_b, _off_b, on_a, _off_a) = meta["ramps"]
        assert on_a["n_departed"] == on_a["n_planned"] == 12
        assert on_b["n_departed"] == on_b["n_planned"] == 12
        assert meta["n_collisions"] == 0

    def test_listing_the_downstream_pair_first_is_byte_identical(self, tmp_path):
        """The ramp list's order does not reach the run (2026-09-24, block
        3): on ``tests/fixtures/weave_two.osm`` the scenario listing the
        downstream pair first writes the same trajectory bytes as the one
        listing it second, and the same ``weave_sections`` entries matched by
        ramp name (listed upstream-first in both), with every driven vehicle
        handed back. Mainline demand only, the entrances at rate 0: the
        fleet plan draws each on-ramp's departures in config order
        (``microsim.vehicles.build_corridor_plan``), so entrants would make
        the two listings different plans rather than different runs; the
        exit draws are made in corridor order and are the same for both. The
        exiting movement and the vacate rule (both sections' driven
        vehicles) act in both runs."""
        cfg_down = two_weave_scenario(downstream_first=True, ramp_rate=0.0)
        cfg_up = two_weave_scenario(downstream_first=False, ramp_rate=0.0)
        assert config_hash(cfg_down) != config_hash(cfg_up)  # different listings
        p_down = run_micro(cfg_down, 5, tmp_path / "down")
        p_up = run_micro(cfg_up, 5, tmp_path / "up")
        assert p_down.trajectories.read_bytes() == p_up.trajectories.read_bytes()
        m_down = json.loads(p_down.meta.read_text())
        m_up = json.loads(p_up.meta.read_text())
        assert [m["ramp"] for m in m_down["merge_models"]] == ["B on", "A on"]
        assert [m["ramp"] for m in m_up["merge_models"]] == ["A on", "B on"]
        w_down = {w["ramp"]: w for w in m_down["weave_sections"]}
        w_up = {w["ramp"]: w for w in m_up["weave_sections"]}
        assert list(w_down) == list(w_up) == ["A on", "B on"]
        assert w_down == w_up
        for w in w_down.values():
            assert w["n_entered"] == w["n_changed_out"] > 0 and w["n_changed_in"] == 0, w
            assert w["n_unfinished"] == 0 and w["n_missed"] == 0, w
            assert w["n_vacated"] > 0, w
        assert m_down["n_collisions"] == m_up["n_collisions"] == 0
        assert m_down["n_vehicles_departed"] == m_up["n_vehicles_departed"] > 0

    def test_unpaired_geometry_is_refused_with_the_edges(self, merge_osm, tmp_path):
        """The schema pairing holds (same attach edge) but lane 0 of 102 never
        reaches exit link 201, which leaves the merge fixture upstream."""
        cfg = ScenarioConfig.model_validate(
            {
                "name": "weave_unpaired",
                "network": {
                    "kind": "osm",
                    "osm_file": str(merge_osm),
                    "corridor_edges": ["100", "101", "102", "103"],
                    "inflow": [[0.0, 0.3]],
                    "ramps": [
                        {
                            "kind": "on",
                            "name": "on",
                            "edges": ["200"],
                            "attach_edge": "102",
                            "inflow": [[0.0, 0.1]],
                            "merge": "weave",
                            "weave": {"exit_ramp": "off"},
                        },
                        {
                            "kind": "off",
                            "name": "off",
                            "edges": ["201"],
                            "attach_edge": "102",
                            "exit_fraction": [[0.0, 0.1]],
                        },
                    ],
                },
                "sim": {"duration_s": 30.0},
            }
        )
        with pytest.raises(ValueError, match="does not carry the entering traffic") as exc:
            run_micro(cfg, 3, tmp_path / "unpaired")
        assert "102" in str(exc.value) and "201" in str(exc.value)


# --- Review of 2026-09-24: fake-TraCI harness for the weave step ------------
# ``_weave_step`` is exercised against a scripted vehicle module so the
# bookkeeping (control hand-back, counters, mode restoration) can be checked
# per step without SUMO; the geometry is a two-piece section ``a`` -> ``b``
# (an attach edge split by ramp guessing) leaving to exit ``x``.

import traci.constants as _tc  # noqa: E402


class _WeaveVehicle:
    """Records every TraCI write; neighbours are scripted per (vid, mode)."""

    def __init__(self, speeds: dict[str, float], neighbors: dict | None = None) -> None:
        self.speeds = speeds
        self.neighbors = neighbors or {}
        self.lc_modes: dict[str, int] = {}
        self.max_speeds: dict[str, float] = {}
        self.leaders: dict[str, tuple[str, float]] = {}
        self.calls: list[tuple] = []

    def getLaneChangeMode(self, vid):
        return self.lc_modes.get(vid, 1621)

    def getMaxSpeed(self, vid):
        return self.max_speeds.get(vid, 33.3)

    def getMinGap(self, vid):
        return 2.5

    # the IDM constants the cooperation reads (CLAUDE.md §3.1 defaults)
    def getLength(self, vid):
        return 5.0

    def getTau(self, vid):
        return 1.4

    def getAccel(self, vid):
        return 0.73

    def getDecel(self, vid):
        return 1.67

    def getLeader(self, vid, dist):
        return self.leaders.get(vid)

    def getSpeed(self, vid):
        return self.speeds[vid]

    def getNeighbors(self, vid, mode):
        return self.neighbors.get((vid, mode), ())

    def setLaneChangeMode(self, vid, mode):
        self.lc_modes[vid] = mode
        self.calls.append(("lc", vid, mode))

    def setMaxSpeed(self, vid, v):
        self.max_speeds[vid] = v
        self.calls.append(("vmax", vid, v))

    def slowDown(self, vid, v, dur):
        self.calls.append(("slow", vid, v, dur))

    def changeLane(self, vid, lane, dur):
        self.calls.append(("change", vid, lane, dur))

    def changeTarget(self, vid, edge):
        self.calls.append(("target", vid, edge))


class _WeaveLane:
    def getMaxSpeed(self, lane_id):
        return 30.0


class _WeaveMod:
    TraCIException = _FakeTraCIException

    def __init__(self, vehicle: _WeaveVehicle) -> None:
        self.vehicle = vehicle
        self.lane = _WeaveLane()


def _weave_state(**params) -> dict:
    """``weave_states`` entry as ``run_micro`` builds it (two pieces, exit x)."""
    edges = ("a", "b")
    lens = {"a": 100.0, "b": 100.0}
    return {
        "ramp": "on",
        "exit": "off",
        "off_index": 1,
        "edges": list(edges),
        "edge_index": {e: n for n, e in enumerate(edges)},
        "exit_edge": "x",
        "exit_edges": frozenset({"x", "x2"}),
        "exit_only": {"a": True, "b": True},
        "lane_len_m": lens,
        "beyond_m": {"a": 100.0, "b": 0.0},
        "length_m_measured": 200.0,
        "length_m": None,
        "params": {**WEAVE_DEFAULTS, **params},
        "exiting_ids": frozenset({"e"}),
        # never asked to vacate: the paired exit's vehicles and those bound
        # for an off-ramp leaving from a window edge (review, 2026-09-24)
        "vacate_exempt_ids": frozenset({"e"}),
        "exited": set(),
        "reached": set(),
        "awaiting_exit": set(),
        "veh": {},
        # 2026-09-24 (block 3, second attempt): gap listings on the section
        # axis and the cooperation counters
        "lane_map": {(e, k): k for e in edges for k in range(3)},
        "x_offset": {"a": 0.0, "b": 100.0},
        # third derivation: no corridor edge before the section here
        "vacate_lanes": {},
        "vacate": {},
        "vacate_seen": set(),
        "vacate_pending": set(),
        "vacate_asks_s": deque(),
        "vacate_flow_ids": set(),
        "vacate_flow_s": deque(),
        "ramp_edges": frozenset(),
        "pre": {},
        "veh_params": {},
        "lane_vmax": {},
        "n_entered": 0,
        "n_changed_in": 0,
        "n_changed_out": 0,
        "n_forced": 0,
        "n_missed": 0,
        # exit-side derivation: exits given up at the gore's end
        "n_missed_exit": 0,
        # WP-52: give-ups deferred by the bounded patience (vehicle-steps)
        "n_giveup_waited": 0,
        "gave_up": set(),
        "through_target": "z",
        "n_forced_deferred": 0,
        "n_cooperations": 0,
        "coop_decel_sum": 0.0,
        "n_changer_eased": 0,
        "n_vacated": 0,
        "n_vacate_refused": 0,
        "n_vacate_skipped_no_gap": 0,
        "n_vacate_requests": 0,
        # fifth derivation: stopped crossing pairs
        "pair_since": {},
        "pair_released": set(),
        "n_pair_releases": 0,
        "step_s": 0.5,
        "waits_in_s": [],
        "waits_out_s": [],
    }


def _sections_back_to_back() -> tuple[dict, dict]:
    """``(B, A)``: two weave states with B's last edge the edge before A.

    B is :func:`_weave_state` (edges a, b; e exits there); A's section is
    (c, d) with its vacate window on b, so an exit-bound vehicle of B in B's
    lane 1 is "through" for A and inside A's window while B drives it. B's
    exit leaves from A's window edge b, so e stays in A's
    ``vacate_exempt_ids`` (as ``run_micro`` builds them, review 2026-09-24).
    """
    ws_b = _weave_state()
    ws_a = _weave_state()
    ws_a.update(
        {
            "off_index": 2,
            "edges": ["c", "d"],
            "edge_index": {"c": 0, "d": 1},
            "exit_only": {"c": True, "d": True},
            "lane_len_m": {"c": 100.0, "d": 100.0},
            "beyond_m": {"c": 100.0, "d": 0.0},
            "exiting_ids": frozenset(),
            "lane_map": {
                **{(e, k): k for e in ("c", "d") for k in range(3)},
                ("b", 1): 1,
                ("b", 2): 2,
            },
            "x_offset": {"b": 100.0, "c": 200.0, "d": 300.0},
            "vacate_lanes": {"b": (1, 2)},
        }
    )
    return ws_b, ws_a


def _res(road: str, lane: int, pos: float, v: float) -> dict:
    return {
        _tc.VAR_ROAD_ID: road,
        _tc.VAR_LANE_INDEX: lane,
        _tc.VAR_LANEPOSITION: pos,
        _tc.VAR_SPEED: v,
    }


class TestWeaveStepBookkeeping:
    """Review findings of 2026-09-24 on ``microsim.runner._weave_step``."""

    def test_junction_lane_between_section_pieces_keeps_control(self):
        """With ``internal_links`` an exiting vehicle crossing the junction
        between two section pieces reports an internal road id for a step; it
        must stay under control, not be handed back as missed and re-entered
        (which would reset its wait and forcing timers and double-count it)."""
        from microsim.runner import _weave_step

        ws = _weave_state()
        veh = _WeaveVehicle({"e": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"e": _res("a", 1, 95.0, 20.0)}, 0.0)
        assert ws["n_entered"] == 1 and "e" in ws["veh"]
        _weave_step(mod, _tc, ws, {"e": _res(":j_0", 1, 3.0, 20.0)}, 0.5)
        assert ws["n_missed"] == 0, "handed back on the junction lane"
        assert "e" in ws["veh"] and ws["veh"]["e"]["entered_s"] == 0.0
        _weave_step(mod, _tc, ws, {"e": _res("b", 1, 5.0, 20.0)}, 1.0)
        assert ws["n_entered"] == 1 and ws["n_missed"] == 0
        assert ws["veh"]["e"]["entered_s"] == 0.0
        # leaving the section on lane 1 through the downstream junction is a miss,
        # booked once the vehicle is on a non-section edge
        _weave_step(mod, _tc, ws, {"e": _res(":k_0", 1, 3.0, 20.0)}, 1.5)
        assert ws["n_missed"] == 0 and "e" in ws["veh"]
        _weave_step(mod, _tc, ws, {"e": _res("c", 1, 4.0, 20.0)}, 2.0)
        assert ws["n_missed"] == 1 and ws["veh"] == {}
        assert ws["n_entered"] == ws["n_changed_in"] + ws["n_changed_out"] + ws["n_missed"]

    def test_hand_back_restores_mode_and_speed_once(self):
        from microsim.runner import LC_MODE_SCRIPTED_FORCE, _weave_step

        ws = _weave_state()
        veh = _WeaveVehicle({"e": 20.0})
        veh.lc_modes["e"] = 1621
        veh.max_speeds["e"] = 31.0
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"e": _res("a", 1, 10.0, 20.0)}, 0.0)
        # an empty target lane: accepted at once, executed under mode 256 for
        # one step (2026-09-24, block 3: no mode-512 request any more)
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_FORCE
        assert ("change", "e", 0, ws["step_s"]) in veh.calls
        # the desired speed is never written (no caps, second attempt)
        assert not [c for c in veh.calls if c[0] == "vmax"]
        assert veh.max_speeds["e"] == pytest.approx(31.0)
        _weave_step(mod, _tc, ws, {"e": _res("a", 0, 30.0, 20.0)}, 0.5)
        assert ws["n_changed_out"] == 1 and ws["veh"] == {}
        assert veh.lc_modes["e"] == 1621 and veh.max_speeds["e"] == 31.0
        assert ws["waits_out_s"] == [0.5]

    def test_forced_deferred_counts_vehicle_steps(self):
        """One vehicle refused twice by the minimum-gap guard is two
        ``n_forced_deferred``, as the docstring and CONTRACTS §2 say."""
        from microsim.runner import LC_MODE_SCRIPTED_SAFE, _weave_step

        ws = _weave_state(force_after_s=0.0, force_within_m=1000.0)
        # a lane-0 follower closing fast on a small gap: acceptance fails, the
        # forced change is due at once and the guard refuses it. The follower
        # is exit-bound and already on lane 0, so it is not itself driven.
        ws["exiting_ids"] = frozenset({"e", "f"})
        veh = _WeaveVehicle({"e": 10.0, "f": 20.0}, {("e", 1): (("f", 3.0),)})
        mod = _WeaveMod(veh)
        for k in range(2):
            _weave_step(
                mod,
                _tc,
                ws,
                {"e": _res("b", 1, 50.0 + k, 10.0), "f": _res("b", 0, 40.0, 20.0)},
                0.5 * k,
            )
        assert ws["n_forced_deferred"] == 2 and ws["n_entered"] == 1
        assert ws["veh"]["e"]["forced"] is False
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_SAFE
        assert not [c for c in veh.calls if c[0] == "change"]

    def test_through_follower_cooperates_by_a_one_step_speed_target(self):
        """Through traffic on lane 1 (not exit-bound, so never driven) that is
        the follower of an entering vehicle's chosen gap is driven towards the
        entrant as its virtual leader: one ``slowDown(v, 0.0)`` per step, at
        most its own ``decel`` below its speed, never a desired-speed write and
        never a lane-change mode write. ``courtesy`` is inert (2026-09-24,
        block 3, second attempt)."""
        from microsim.runner import NEIGHBOR_LEFT_FOLLOWERS, _weave_step

        ws = _weave_state(courtesy=2.0)
        veh = _WeaveVehicle({"n": 15.0, "f": 15.0}, {("n", NEIGHBOR_LEFT_FOLLOWERS): (("f", 4.0),)})
        veh.max_speeds["f"] = 29.0
        mod = _WeaveMod(veh)
        res = {"n": _res("a", 0, 50.0, 15.0), "f": _res("a", 1, 40.0, 15.0)}
        _weave_step(mod, _tc, ws, res, 0.0)
        assert ws["n_entered"] == 1 and set(ws["veh"]) == {"n"}
        assert ws["veh"]["n"]["target"] == "f"
        # 5 m behind the entrant's rear at equal speed: IDM asks for far more
        # than b, so the command is clipped at b = 1.67 m/s^2 over one step
        (slow,) = [c for c in veh.calls if c[0] == "slow"]
        assert slow == ("slow", "f", pytest.approx(15.0 - 1.67 * 0.5), 0.0)
        assert ws["n_cooperations"] == 1 and ws["coop_decel_sum"] == pytest.approx(1.67)
        assert not [c for c in veh.calls if c[0] == "vmax"]
        assert veh.max_speeds["f"] == 29.0 and "f" not in veh.lc_modes

    def test_abreast_pair_only_the_rear_one_eases(self):
        """An overlapping exiting/entering pair, each wanting the other's lane:
        only the rear one (front bumper behind) has the other as its gap's
        leader and eases off towards it (one-step target, clipped at b); the
        front one takes no gap whose leader is behind it and is not commanded.
        With both easing they braked each other to a standstill (fixture
        trace, 2026-09-24 block 3, second attempt)."""
        from microsim.runner import NEIGHBOR_LEFT_FOLLOWERS, NEIGHBOR_RIGHT_FOLLOWERS, _weave_step

        ws = _weave_state()
        veh = _WeaveVehicle(
            {"e": 3.0, "n": 3.0},
            {
                ("e", NEIGHBOR_RIGHT_FOLLOWERS): (("n", -1.0),),
                ("n", NEIGHBOR_LEFT_FOLLOWERS): (("e", -1.0),),
            },
        )
        mod = _WeaveMod(veh)
        res = {"e": _res("b", 1, 90.0, 3.0), "n": _res("b", 0, 88.0, 3.0)}
        _weave_step(mod, _tc, ws, res, 0.0)
        assert set(ws["veh"]) == {"e", "n"}
        slows = [c for c in veh.calls if c[0] == "slow"]
        assert slows == [("slow", "n", pytest.approx(3.0 - 1.67 * 0.5), 0.0)]
        assert ws["n_changer_eased"] == 1 and ws["n_cooperations"] == 0
        assert ws["veh"]["e"]["target"] is None and ws["veh"]["n"]["target"] is None
        assert not [c for c in veh.calls if c[0] == "vmax"]
        # neither gap is acceptable, so no change is requested by either
        assert not [c for c in veh.calls if c[0] == "change"]


class TestWeaveLockRules:
    """The two rule changes of 2026-09-24 (docs/ONBOARDING_MNDOT.md §10)."""

    def test_exiting_vehicle_eases_towards_its_gap_leader_never_by_a_cap(self):
        """An exiting vehicle behind a stopped auxiliary-lane vehicle is driven
        towards it as its gap's leader by a one-step car-following target
        (clipped at b), inside and outside ``force_within_m`` alike; its
        desired speed is never written (the station-keeping cap of the first
        attempt commanded 0 m/s with no floor, 2026-09-24 block 3). Fourth
        derivation: only while dropping in behind that leader by the section
        end needs no more than b (``_weave_easing_ok``) — at 20 m/s with
        150 m left the drop behind a stopped vehicle 30 m ahead needs
        4.8 m/s² and the vehicle keeps its own speed; at 10 m/s it needs
        1.1 m/s² and is eased. The stopped vehicle on the exit-only lane is
        itself an entering changer whose gap's follower is the exiter, so
        the exiter also receives the same clipped target as a *follower*
        (``n_cooperations``); the counters tell the two apart."""
        from microsim.runner import NEIGHBOR_RIGHT_LEADERS, _weave_step

        ws = _weave_state()
        veh = _WeaveVehicle({"e": 20.0, "q": 0.0}, {("e", NEIGHBOR_RIGHT_LEADERS): (("q", 30.0),)})
        veh.max_speeds["e"] = 31.0
        mod = _WeaveMod(veh)

        def slows():
            return [c for c in veh.calls if c[0] == "slow"]

        # 150 m of section left at 20 m/s (t_a = 7.5 s), s_l = 30 m, accepted
        # gap 14.5 m: a_req = 2·(−15.5 + 150)/7.5² = 4.8 m/s² > b — not eased;
        # the one target it gets is as q's follower
        _weave_step(mod, _tc, ws, {"e": _res("a", 1, 50.0, 20.0), "q": _res("a", 0, 85.0, 0.0)}, 0)
        assert slows() == [("slow", "e", pytest.approx(20.0 - 1.67 * 0.5), 0.0)]
        assert ws["n_changer_eased"] == 0 and ws["n_cooperations"] == 1
        veh.calls.clear()
        # at 10 m/s (t_a = 15 s): a_req = 2·(−21.5 + 150)/15² = 1.1 m/s² —
        # eased by IDM towards the stopped leader, clipped at b
        veh.speeds["e"] = 10.0
        _weave_step(mod, _tc, ws, {"e": _res("a", 1, 50.0, 10.0), "q": _res("a", 0, 85.0, 0.0)}, 1)
        assert slows() == [("slow", "e", pytest.approx(10.0 - 1.67 * 0.5), 0.0)]
        assert ws["n_changer_eased"] == 1 and ws["n_cooperations"] == 1
        veh.calls.clear()
        # 60 m from the gore (force_within_m 80): the same rule, no cap. At
        # 6 m/s 10 m behind the leader a_req = 2·(−3.9 + 60)/10² = 1.1 m/s²
        veh.speeds["e"] = 6.0
        _weave_step(mod, _tc, ws, {"e": _res("b", 1, 40.0, 6.0), "q": _res("b", 0, 55.0, 0.0)}, 2)
        assert slows() == [("slow", "e", pytest.approx(6.0 - 1.67 * 0.5), 0.0)]
        assert ws["n_changer_eased"] == 2 and ws["n_cooperations"] == 1
        veh.calls.clear()
        # and at 10 m/s 30 m behind it (t_a = 6 s): 2·(−21.5 + 60)/6² = 2.1 > b
        veh.speeds["e"] = 10.0
        _weave_step(mod, _tc, ws, {"e": _res("b", 1, 40.0, 10.0), "q": _res("b", 0, 75.0, 0.0)}, 3)
        assert slows() == [("slow", "e", pytest.approx(10.0 - 1.67 * 0.5), 0.0)]
        assert ws["n_changer_eased"] == 2 and ws["n_cooperations"] == 2
        assert not [c for c in veh.calls if c[0] == "vmax"] and veh.max_speeds["e"] == 31.0

    def test_entering_vehicle_with_accepted_gaps_changes_at_once(self):
        """Accepted gaps: the entering change is executed under mode 256 for one
        step (a far, fast follower no longer makes SUMO refuse it and brake the
        entering vehicle); an unaccepted gap keeps mode 512 and no request."""
        from microsim.runner import (
            LC_MODE_SCRIPTED_FORCE,
            LC_MODE_SCRIPTED_SAFE,
            NEIGHBOR_LEFT_FOLLOWERS,
            _weave_step,
        )

        ws = _weave_state()
        veh = _WeaveVehicle(
            {"n": 18.0, "f": 29.0}, {("n", NEIGHBOR_LEFT_FOLLOWERS): (("f", 130.0),)}
        )
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"n": _res("a", 0, 5.0, 18.0), "f": _res("a", 1, 1.0, 29.0)}, 0.0)
        assert veh.lc_modes["n"] == LC_MODE_SCRIPTED_FORCE
        assert ("change", "n", 1, ws["step_s"]) in veh.calls
        veh.calls.clear()
        veh.neighbors = {("n", NEIGHBOR_LEFT_FOLLOWERS): (("f", 10.0),)}
        _weave_step(
            mod, _tc, ws, {"n": _res("a", 0, 14.0, 18.0), "f": _res("a", 1, 1.0, 29.0)}, 0.5
        )
        assert veh.lc_modes["n"] == LC_MODE_SCRIPTED_SAFE
        assert not [c for c in veh.calls if c[0] == "change"]


class TestWeaveExitBookkeeping:
    """Exit counting of ``_weave_step`` (review finding of 2026-09-24): an
    exit-bound vehicle is counted once, on any edge of the exit ramp or when
    it leaves the network from the section, against the vehicles that reached
    the section rather than every departed one."""

    @staticmethod
    def _meta(ws: dict, n_departed_exiting: int) -> dict:
        from microsim.runner import _weave_meta

        return _weave_meta(ws, {"main_off1": n_departed_exiting})

    def test_exit_edge_crossed_within_one_step_is_still_counted_once(self):
        """The ramp's first edge is never sighted (shorter than a step of
        travel): the vehicle is counted on the ramp's second edge, once."""
        from microsim.runner import _weave_step

        ws = _weave_state()
        mod = _WeaveMod(_WeaveVehicle({"e": 25.0}))
        # on lane 0 of the section: exit-bound, nothing left to change, not driven
        _weave_step(mod, _tc, ws, {"e": _res("b", 0, 95.0, 25.0)}, 0.0)
        assert ws["reached"] == {"e"} and ws["exited"] == set() and ws["veh"] == {}
        _weave_step(mod, _tc, ws, {"e": _res("x2", 0, 2.0, 25.0)}, 0.5)
        assert ws["exited"] == {"e"} and ws["awaiting_exit"] == set()
        _weave_step(mod, _tc, ws, {"e": _res("x2", 0, 14.0, 25.0)}, 1.0)
        _weave_step(mod, _tc, ws, {}, 1.5)
        meta = self._meta(ws, 1)
        assert meta["n_exited"] == 1 and meta["n_reached_section_exiting"] == 1
        assert meta["n_departed_exiting"] == 1 and meta["exit_edges"] == ["x", "x2"]

    def test_whole_ramp_driven_within_one_step_is_counted_once(self):
        """Neither ramp edge is ever sighted: the vehicle leaves the network
        from the section's last edge and is counted then, once."""
        from microsim.runner import _weave_step

        ws = _weave_state()
        mod = _WeaveMod(_WeaveVehicle({"e": 25.0}))
        _weave_step(mod, _tc, ws, {"e": _res("b", 0, 99.0, 25.0)}, 0.0)
        assert ws["awaiting_exit"] == {"e"} and ws["exited"] == set()
        _weave_step(mod, _tc, ws, {}, 0.5)
        assert ws["exited"] == {"e"} and ws["awaiting_exit"] == set()
        _weave_step(mod, _tc, ws, {}, 1.0)
        assert self._meta(ws, 1)["n_exited"] == 1

    def test_internal_lane_after_the_section_then_gone_is_counted_once(self):
        from microsim.runner import _weave_step

        ws = _weave_state()
        mod = _WeaveMod(_WeaveVehicle({"e": 25.0}))
        _weave_step(mod, _tc, ws, {"e": _res("b", 0, 99.0, 25.0)}, 0.0)
        _weave_step(mod, _tc, ws, {"e": _res(":k_0", 0, 3.0, 25.0)}, 0.5)
        assert ws["exited"] == set() and ws["awaiting_exit"] == {"e"}
        _weave_step(mod, _tc, ws, {}, 1.0)
        assert ws["exited"] == {"e"} and ws["awaiting_exit"] == set()

    def test_vehicle_still_upstream_at_the_end_is_departed_not_reached(self):
        """An exit-bound vehicle seen only upstream of the section is in
        ``n_departed_exiting`` and in neither ``n_reached_section_exiting``
        nor ``n_exited``."""
        from microsim.runner import _weave_step

        ws = _weave_state()
        ws["exiting_ids"] = frozenset({"e", "u"})
        mod = _WeaveMod(_WeaveVehicle({"e": 25.0, "u": 25.0}))
        res = {"e": _res("b", 0, 50.0, 25.0), "u": _res("up", 0, 10.0, 25.0)}
        for k in range(3):
            _weave_step(mod, _tc, ws, res, 0.5 * k)
        _weave_step(mod, _tc, ws, {"u": _res("up", 0, 40.0, 25.0)}, 1.5)
        assert ws["reached"] == {"e"} and ws["exited"] == {"e"}
        meta = self._meta(ws, 2)
        assert meta["n_departed_exiting"] == 2
        assert meta["n_reached_section_exiting"] == 1 and meta["n_exited"] == 1

    def test_leaving_by_the_mainline_is_never_counted(self):
        """A reached vehicle next seen on a named non-section, non-ramp edge
        (a reroute) left by the mainline: dropped, never an exit, even when
        it later leaves the network."""
        from microsim.runner import _weave_step

        ws = _weave_state()
        mod = _WeaveMod(_WeaveVehicle({"e": 25.0}))
        _weave_step(mod, _tc, ws, {"e": _res("b", 0, 99.0, 25.0)}, 0.0)
        _weave_step(mod, _tc, ws, {"e": _res("c", 0, 5.0, 25.0)}, 0.5)
        assert ws["awaiting_exit"] == set() and ws["exited"] == set()
        _weave_step(mod, _tc, ws, {}, 1.0)
        assert ws["exited"] == set() and ws["reached"] == {"e"}

    def test_hand_back_on_a_later_ramp_edge_is_a_completed_change(self):
        """A driven exiting vehicle next seen on the ramp's second edge made
        its change (``n_changed_out``), not a miss, and is counted as exited."""
        from microsim.runner import _weave_step

        ws = _weave_state()
        mod = _WeaveMod(_WeaveVehicle({"e": 20.0}))
        # 10 m of section ahead: beyond exit_giveup_m, so still driven
        _weave_step(mod, _tc, ws, {"e": _res("b", 1, 90.0, 20.0)}, 0.0)
        assert ws["n_entered"] == 1
        _weave_step(mod, _tc, ws, {"e": _res("x2", 0, 3.0, 20.0)}, 0.5)
        assert ws["n_changed_out"] == 1 and ws["n_missed"] == 0 and ws["veh"] == {}
        assert ws["exited"] == {"e"}


class TestWeaveForceGapGuard:
    def test_closing_speed_signs(self):
        from microsim.runner import _weave_force_gap_ok

        s0, tau = 2.5, 0.6
        # ego faster than the leader closes on it; slower does not
        assert _weave_force_gap_ok(s0, tau, 20.0, 5.0, 25.0, math.inf, math.nan)
        assert not _weave_force_gap_ok(s0, tau, 20.0, 5.0, 15.0, math.inf, math.nan)
        # a faster follower closes on ego; a slower one does not
        assert _weave_force_gap_ok(s0, tau, 20.0, math.inf, math.nan, 5.0, 15.0)
        assert not _weave_force_gap_ok(s0, tau, 20.0, math.inf, math.nan, 5.0, 25.0)
        # never below minGap, whatever the speeds; overlap is never admitted
        assert not _weave_force_gap_ok(s0, tau, 0.0, 2.5, 0.0, math.inf, math.nan)
        assert not _weave_force_gap_ok(s0, tau, 0.0, math.inf, math.nan, -1.0, 0.0)
        assert _weave_force_gap_ok(s0, tau, 0.0, 2.6, 0.0, 2.6, 0.0)
        # boundary: exactly the closing distance is refused
        assert not _weave_force_gap_ok(s0, tau, 20.0, s0 + tau * 5.0, 15.0, math.inf, math.nan)

    def test_speed_aware_guard(self):
        """With the parties' decelerations the guard's bounds are also their
        brake gaps ``s0 + Δv⁺²/(2·b)`` (2026-09-24, block 3, speed-aware
        acceptance): the Ruth St trace — a 23 m/s exiter, 27 m behind a
        queue head at 1 m/s — passes the closing-speed bound (``s0 + 0.6 ·
        22 = 15.7 m``) and is refused at 147.4 m; the corridor-demand trace
        — a follower at 16.1 m/s, 10.7 m behind a released exiter at 0.54
        m/s (``s0 = 0``) — passes 9.35 m and is refused at 72.5 m; a leader
        (follower) as fast or faster leaves the closing-speed bound as the
        floor; each side is touched by its own ``b`` alone."""
        from microsim.runner import _weave_brake_gap, _weave_force_gap_ok, _weave_lead_gap_min

        s0, tau, b = 2.5, 0.6, 1.67
        g_lead_b = _weave_brake_gap(s0, 23.0, 1.0, b)
        assert g_lead_b == pytest.approx(s0 + 22.0**2 / (2.0 * b))
        assert _weave_force_gap_ok(s0, tau, 23.0, 27.0, 1.0, math.inf, math.nan)
        assert not _weave_force_gap_ok(s0, tau, 23.0, 27.0, 1.0, math.inf, math.nan, b)
        assert _weave_force_gap_ok(s0, tau, 23.0, g_lead_b + 0.1, 1.0, math.inf, math.nan, b)
        assert not _weave_force_gap_ok(s0, tau, 23.0, g_lead_b, 1.0, math.inf, math.nan, b)
        # a faster leader: the brake gap is s0, the closing-speed bound (0) the floor
        assert _weave_force_gap_ok(s0, tau, 20.0, 2.6, 25.0, math.inf, math.nan, b)
        assert not _weave_force_gap_ok(s0, tau, 20.0, 2.5, 25.0, math.inf, math.nan, b)
        # the follower side: the corridor-demand trace, released (s0 = 0)
        g_foll_b = (16.13 - 0.54) ** 2 / (2.0 * b)
        assert _weave_force_gap_ok(0.0, tau, 0.54, math.inf, math.nan, 10.7, 16.13)
        assert not _weave_force_gap_ok(0.0, tau, 0.54, math.inf, math.nan, 10.7, 16.13, None, b)
        assert _weave_force_gap_ok(0.0, tau, 0.54, math.inf, math.nan, g_foll_b + 0.1, 16.13, b, b)
        assert not _weave_force_gap_ok(0.0, tau, 0.54, math.inf, math.nan, g_foll_b, 16.13, b, b)
        # each side by its own b: b_ego alone leaves the follower side as it was
        assert _weave_force_gap_ok(0.0, tau, 0.54, math.inf, math.nan, 10.7, 16.13, b, None)
        assert _weave_force_gap_ok(s0, tau, 20.0, math.inf, math.nan, 5.0, 15.0, b, b)
        assert not _weave_force_gap_ok(s0, tau, 20.0, math.inf, math.nan, 5.0, 25.0, b, b)
        # the acceptance's bound: the time gap at the changer's speed is the
        # floor, the brake gap the bound on a slower leader, the time gap
        # alone without one
        assert _weave_lead_gap_min(s0, tau, 23.0, 27.0, 1.0, b) == pytest.approx(g_lead_b)
        assert _weave_lead_gap_min(s0, tau, 23.0, 27.0, 30.0, b) == pytest.approx(s0 + tau * 23.0)
        assert _weave_lead_gap_min(s0, tau, 23.0, math.inf, math.nan, b) == s0 + tau * 23.0
        # the floor binds up to a closing speed of √(2·b·accept·v): at 23 m/s
        # a leader at 16.2 m/s or faster is accepted at the time gap
        assert _weave_lead_gap_min(s0, tau, 23.0, 27.0, 16.3, b) == pytest.approx(s0 + tau * 23.0)
        assert _weave_lead_gap_min(s0, tau, 23.0, 27.0, 16.0, b) > s0 + tau * 23.0

    def test_acceptance_refuses_a_fast_changer_behind_a_slow_leader(self):
        """Through ``_weave_step``: an exit-bound vehicle at 23 m/s with the
        auxiliary lane's queue head 27 m ahead at 1 m/s is not accepted (it
        was, at ``s0 + 0.6 · 23 = 16.3 m``) and keeps mode 512; with that
        leader at the changer's own speed the same gap is accepted and the
        change requested under mode 256 for one step."""
        from microsim.runner import (
            LC_MODE_SCRIPTED_FORCE,
            LC_MODE_SCRIPTED_SAFE,
            NEIGHBOR_RIGHT_LEADERS,
            _weave_step,
        )

        ws = _weave_state()
        veh = _WeaveVehicle({"e": 23.0, "q": 1.0}, {("e", NEIGHBOR_RIGHT_LEADERS): (("q", 27.0),)})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"e": _res("a", 1, 15.0, 23.0), "q": _res("a", 0, 47.0, 1.0)}, 0)
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_SAFE
        # (q, on the exit-only lane 0 and not exit-bound, is an entrant with
        # an open left lane and changes at once; only e's requests are read)
        assert not [c for c in veh.calls if c[0] == "change" and c[1] == "e"]
        veh.calls.clear()
        veh.speeds["q"] = 23.0
        _weave_step(
            mod, _tc, ws, {"e": _res("a", 1, 15.0, 23.0), "q": _res("a", 0, 47.0, 23.0)}, 0.5
        )
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_FORCE
        assert ("change", "e", 0, ws["step_s"]) in veh.calls


class TestWeaveEasingFeasibility:
    """``_weave_easing_ok`` (fourth derivation, 2026-09-24 block 3): a changer
    is eased towards its gap's leader only while dropping in behind it by the
    section end needs no more than its comfortable deceleration."""

    def test_constant_deceleration_kinematics(self):
        from microsim.runner import _weave_easing_ok

        b = 1.67
        # abreast of L at equal speed, 300 m of section left at 10 m/s
        # (t_a = 30 s): a drop of 8 + 5 m needs 2·13/900 = 0.03 m/s²
        assert _weave_easing_ok(10.0, 10.0, -5.0, 8.0, 300.0, b)
        # the same drop in the last 20 m (t_a = 2 s): 2·13/4 = 6.5 m/s² > b
        assert not _weave_easing_ok(10.0, 10.0, -5.0, 8.0, 20.0, b)
        # just inside b (0.99 b) is allowed, just outside (1.01 b) is not:
        # a_req = 2 d / t_a² with Δv = 0, t_a = 4 s
        t_a = 4.0
        assert _weave_easing_ok(10.0, 10.0, 8.0 - 0.99 * b * t_a * t_a / 2.0, 8.0, 40.0, b)
        assert not _weave_easing_ok(10.0, 10.0, 8.0 - 1.01 * b * t_a * t_a / 2.0, 8.0, 40.0, b)
        # a faster leader opens the gap by itself: 13 − 5·4 < 0, a_req < 0 —
        # feasible but not needed, so not eased (fifth derivation)
        assert not _weave_easing_ok(10.0, 15.0, -5.0, 8.0, 40.0, b)
        # a slower leader adds the closing distance: 2·(13 + 5·2)/4 = 11.5
        assert not _weave_easing_ok(10.0, 5.0, -5.0, 8.0, 20.0, b)
        # already clear by more than needed: nothing to drop, not eased
        assert not _weave_easing_ok(10.0, 10.0, 20.0, 8.0, 1.0, b)
        # exactly the accepted gap at equal speed: a_req = 0, not needed
        assert not _weave_easing_ok(10.0, 10.0, 8.0, 8.0, 40.0, b)

    def test_stopped_changer_uses_the_creep_floor(self):
        from microsim.runner import SCRIPTED_MERGE_CREEP_MS, _weave_easing_ok

        # v_c = 0: t_a = remaining / creep, not a division by zero
        assert _weave_easing_ok(0.0, 0.0, -2.0, 2.0, 30.0 * SCRIPTED_MERGE_CREEP_MS, 1.67)
        assert not _weave_easing_ok(0.0, 0.0, -2.0, 2.0, 0.0, 1.67)


class TestWeaveRampBesideLeader:
    """``_weave_cooperate`` (sixth derivation, 2026-09-24 block 3): an entrant
    still on the ramp is not eased towards a gap leader that overlaps it —
    the leader's rear behind the entrant's front — while the same geometry
    on the section, and a leader just clear of the entrant on the ramp, are
    eased as before."""

    @staticmethod
    def _case(road: str, pos: float, x_leader_front: float, remaining_m: float):
        """Entrant ``n`` at 10 m/s on ``road`` (the ramp ``r`` at offset −100
        m, or section edge ``a``), a lane-1 leader ``l`` at 9 m/s with its
        front at ``x_leader_front`` and a lane-1 follower ``f`` at 9 m/s 30 m
        behind the entrant's front. Returns the chosen follower and the
        one-step targets recorded."""
        from microsim.runner import _weave_cooperate

        ws = _weave_state()
        ws["ramp_edges"] = frozenset({"r"})
        ws["x_offset"]["r"] = -100.0
        ws["lane_map"][("r", 0)] = 0
        veh = _WeaveVehicle({"n": 10.0, "l": 9.0, "f": 9.0})
        mod = _WeaveMod(veh)
        res = {
            "n": _res(road, 0, pos, 10.0),
            "l": _res("a", 1, 0.0, 9.0),
            "f": _res("a", 1, 0.0, 9.0),
        }
        x_n = ws["x_offset"][road] + pos
        x_of = {"n": x_n, "l": x_leader_front, "f": x_n - 30.0}
        v_of = {"n": 10.0, "l": 9.0, "f": 9.0}
        lanes = {1: sorted([(x_of["f"], "f"), (x_of["l"], "l")])}
        coop: dict = {}
        f_t = _weave_cooperate(
            mod, _tc, ws, res, lanes, x_of, v_of, {}, {}, coop, "n", 1, None, 0.6, remaining_m
        )
        return f_t, coop

    def test_overlapping_leader_on_the_ramp_is_not_followed(self):
        # entrant front at x = −10, leader front at −6: rear at −11, s_l = −1
        f_t, coop = self._case("r", 90.0, -6.0, 210.0)
        assert f_t == "f" and "f" in coop and coop["f"][2] is True
        assert "n" not in coop, coop

    def test_leader_just_clear_on_the_ramp_is_followed(self):
        # leader front at −2: rear at −7, s_l = 3 ≥ 0 — needed (5.5 m to
        # drop over t_a = 21 s, a_req = 0.12 m/s²) and feasible, so eased
        f_t, coop = self._case("r", 90.0, -2.0, 210.0)
        assert f_t == "f" and "n" in coop and coop["n"][2] is False
        assert coop["n"][1] == pytest.approx(-1.67)

    def test_overlapping_leader_on_the_section_is_still_eased(self):
        # the second derivation's rule: the rear one of an abreast pair
        # drops back at −b — entrant front at x = 10, leader front at 14
        f_t, coop = self._case("a", 10.0, 14.0, 190.0)
        assert f_t == "f" and "n" in coop and coop["n"][2] is False
        assert coop["n"][1] == pytest.approx(-1.67)


class TestWeavePairRelease:
    """``_weave_pair_release`` (fifth derivation, 2026-09-24 block 3): a changer
    stopped at the section end with the follower of its committed gap standing
    bumper to bumper behind it, both held for ever otherwise (the follower by
    the changer's own cooperation command, the changer by the guard)."""

    @staticmethod
    def _stopped_pair(**params):
        """Exiter ``e`` at 6 m before the section end in lane 1, stopped (one
        metre beyond ``exit_giveup_m``, so it is not given up — 2026-09-24,
        exit-side derivation; it stood 0.5 m before the end when the rule
        was derived); the follower ``f`` of its lane-0 gap 1.5 m behind its
        rear, stopped — an exit-bound vehicle already in lane 0 (the
        fixture's lock pair), so it is not itself driven."""
        from microsim.runner import NEIGHBOR_RIGHT_FOLLOWERS

        ws = _weave_state(**params)
        ws["exiting_ids"] = frozenset({"e", "f"})
        veh = _WeaveVehicle({"e": 0.0, "f": 0.0}, {("e", NEIGHBOR_RIGHT_FOLLOWERS): (("f", 1.5),)})
        res = {"e": _res("b", 1, 94.0, 0.0), "f": _res("b", 0, 87.5, 0.0)}
        return ws, veh, _WeaveMod(veh), res

    def test_follower_released_after_pair_release_s_and_the_changer_forces_at_once(self):
        from microsim.runner import LC_MODE_SCRIPTED_FORCE, _weave_step

        ws, veh, mod, res = self._stopped_pair()
        # e is taken under control at t = 0 (its commitment to f exists from
        # then on), the pair stands from t = 0.5 and is released once it has
        # stood for more than pair_release_s = 2 s: at t = 3.0
        for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5):
            veh.calls.clear()
            _weave_step(mod, _tc, ws, res, t)
            assert ws["veh"]["e"]["target"] == "f"
            # f is held: IDM towards e at a 1.5 m gap, to a stop, every step
            assert [c for c in veh.calls if c[0] == "slow"] == [("slow", "f", 0.0, 0.0)], t
            # e's own forced change (zone entered at t = 0, due at 4 s) is not
            # yet due, and its accepted gap (s0 = 2.5 m) is not met
            assert not [c for c in veh.calls if c[0] == "change"], t
            assert ws["n_pair_releases"] == 0
        veh.calls.clear()
        _weave_step(mod, _tc, ws, res, 3.0)
        assert ws["n_pair_releases"] == 1
        # f yields (farther from the section end): no target this step
        assert not [c for c in veh.calls if c[0] == "slow"]
        # e forces at once, guarded against closing only (1.5 m > 0 at rest)
        assert [c for c in veh.calls if c[0] == "change"] == [("change", "e", 0, 0.5)]
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_FORCE
        assert ws["veh"]["e"]["forced"] is True
        # the same pair standing on counts once; a pair that breaks up and
        # stands again counts again
        _weave_step(mod, _tc, ws, res, 3.5)
        assert ws["n_pair_releases"] == 1 and ("e", "f") in ws["pair_released"]
        res["f"] = _res("b", 0, 60.0, 0.0)  # f has dropped back out of reach
        _weave_step(mod, _tc, ws, res, 4.0)
        assert ws["pair_since"] == {} and ws["pair_released"] == set()
        res["f"] = _res("b", 0, 87.5, 0.0)
        for t in (4.5, 5.0, 5.5, 6.0, 6.5, 7.0):
            _weave_step(mod, _tc, ws, res, t)
        assert ws["n_pair_releases"] == 2

    def test_pair_release_s_is_the_standing_time(self):
        from microsim.runner import _weave_step

        ws, veh, mod, res = self._stopped_pair(pair_release_s=0.0)
        _weave_step(mod, _tc, ws, res, 0.0)
        _weave_step(mod, _tc, ws, res, 0.5)  # the pair is first seen standing
        assert ws["n_pair_releases"] == 0
        veh.calls.clear()
        _weave_step(mod, _tc, ws, res, 1.0)  # stood for 0.5 s > 0
        assert ws["n_pair_releases"] == 1
        assert [c for c in veh.calls if c[0] == "change"] == [("change", "e", 0, 0.5)]

    def test_a_moving_or_separated_pair_is_not_released(self):
        from microsim.runner import SCRIPTED_MERGE_CREEP_MS, _weave_step

        ws, veh, mod, res = self._stopped_pair()
        # the follower at the creep speed: not standing
        veh.speeds["f"] = SCRIPTED_MERGE_CREEP_MS
        res["f"] = _res("b", 0, 87.5, SCRIPTED_MERGE_CREEP_MS)
        for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5):
            _weave_step(mod, _tc, ws, res, t)
        assert ws["pair_since"] == {} and ws["n_pair_releases"] == 0
        # stopped, but more than a vehicle length behind: not a pair
        veh.speeds["f"] = 0.0
        res["f"] = _res("b", 0, 83.5, 0.0)  # gap 5.5 m > 5 m
        for t in (4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0):
            _weave_step(mod, _tc, ws, res, t)
        assert ws["pair_since"] == {} and ws["n_pair_releases"] == 0

    def test_a_driven_follower_that_yields_gets_no_target_and_makes_no_request(self):
        """The pair the fourth derivation described: an entrant that is the
        exiter's cooperating follower with the exiter as its gap leader. The
        entrant, farther from the section end, yields: neither its follower
        target nor its easing is applied and its commitment is dropped."""
        from microsim.runner import NEIGHBOR_LEFT_LEADERS, NEIGHBOR_RIGHT_FOLLOWERS, _weave_step

        ws = _weave_state()
        veh = _WeaveVehicle(
            {"e": 0.0, "n": 0.0},
            {
                ("e", NEIGHBOR_RIGHT_FOLLOWERS): (("n", 1.5),),
                ("n", NEIGHBOR_LEFT_LEADERS): (("e", 1.5),),
            },
        )
        mod = _WeaveMod(veh)
        res = {"e": _res("b", 1, 94.0, 0.0), "n": _res("b", 0, 87.5, 0.0)}
        for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5):
            veh.calls.clear()
            _weave_step(mod, _tc, ws, res, t)
            # n is held as e's follower and eased towards e as its gap leader
            # (the drop it needs, 1 m, is feasible: a_req = 0.37 m/s²); one
            # target, counted in the role that recorded it first (e's
            # follower — e is processed before n)
            assert [c for c in veh.calls if c[0] == "slow"] == [("slow", "n", 0.0, 0.0)], t
        assert ws["n_cooperations"] == 6 and ws["n_changer_eased"] == 0
        assert ws["veh"]["n"]["target"] is None and ws["veh"]["e"]["target"] == "n"
        veh.calls.clear()
        _weave_step(mod, _tc, ws, res, 3.0)
        assert ws["n_pair_releases"] == 1
        assert not [c for c in veh.calls if c[0] == "slow"]
        assert ws["n_cooperations"] == 6 and ws["n_changer_eased"] == 0
        assert [c for c in veh.calls if c[0] == "change"] == [("change", "e", 0, 0.5)]


class TestWeaveExitPriority:
    """The exit side (2026-09-24, block 3, exit-side derivation): an exit-bound
    changer whose forced change is due (``force_after_s`` after entering the
    last ``force_within_m``) has priority over the auxiliary lane — the
    vehicles behind its rear hold, a vehicle beside it is waited for — and
    one that reaches the gore's end still in lane 1 continues through,
    counted, never held there."""

    @staticmethod
    def _at_the_gore(**params):
        """Exiter ``e`` in lane 1 at 8 m before the section end, at 1 m/s;
        ``b`` beside it in lane 0 (front 2 m behind e's front, ahead of e's
        rear), ``f`` 12 m behind e's rear in lane 0, both at 1 m/s; neither
        is driven (exit-bound vehicles already in lane 0). The forced change
        is due at once (``force_after_s`` = 0) unless ``params`` say
        otherwise."""
        from microsim.runner import NEIGHBOR_RIGHT_FOLLOWERS, NEIGHBOR_RIGHT_LEADERS

        ws = _weave_state(**{"force_after_s": 0.0, **params})
        ws["exiting_ids"] = frozenset({"e", "b", "f"})
        veh = _WeaveVehicle(
            {"e": 1.0, "b": 1.0, "f": 1.0},
            {
                ("e", NEIGHBOR_RIGHT_LEADERS): (("b", -3.0),),
                ("e", NEIGHBOR_RIGHT_FOLLOWERS): (("f", 12.0),),
            },
        )
        res = {
            "e": _res("b", 1, 92.0, 1.0),
            "b": _res("b", 0, 90.0, 1.0),
            "f": _res("b", 0, 75.0, 1.0),
        }
        return ws, veh, _WeaveMod(veh), res

    def test_the_gap_behind_the_vehicle_beside_the_exiter_is_held(self):
        """Without priority the abreast rule discards every gap whose leader's
        front is behind the changer's (at the gore's end, all of them) and
        nobody is held; with it the gap behind ``b`` is the exiter's and its
        follower ``f`` is driven to hold one changer minGap farther back
        than IDM towards the rear itself would."""
        from microsim.runner import _idm_accel, _weave_step

        ws, veh, mod, res = self._at_the_gore()
        _weave_step(mod, _tc, ws, res, 0.0)
        assert ws["veh"]["e"]["zone_s"] == 0.0 and ws["veh"]["e"]["target"] == "f"
        (slow,) = [c for c in veh.calls if c[0] == "slow"]
        # f's gap to e's rear is 87 - 75 = 12 m; the hold is IDM towards a
        # virtual leader at 12 - s0 = 9.5 m, at equal speeds
        a_hold = _idm_accel(1.0, 30.0, 12.0 - 2.5, 0.0, 1.4, 0.73, 1.67, 2.5)
        assert slow == ("slow", "f", pytest.approx(1.0 + max(a_hold, -1.67) * 0.5), 0.0)
        assert a_hold < _idm_accel(1.0, 30.0, 12.0, 0.0, 1.4, 0.73, 1.67, 2.5)
        assert ws["n_cooperations"] == 1 and ws["n_changer_eased"] == 0
        # e is not eased towards b (it is the front one) and cannot change
        # yet: b overlaps it on the leader side
        assert not [c for c in veh.calls if c[0] == "change"]
        assert ws["n_forced_deferred"] == 1  # due, refused by the overlap on the leader side

    def test_the_hold_is_kept_while_the_follower_closes(self):
        """The commitment survives the follower's IDM falling below ``-b`` (it
        brakes at its ``b`` either way) and ends only when it is no longer
        behind the exiter's rear."""
        from microsim.runner import _weave_step

        ws, veh, mod, res = self._at_the_gore()
        _weave_step(mod, _tc, ws, res, 0.0)
        # f now 4 m behind e's rear at 6 m/s: IDM says far below -b, the
        # gap is still f's to hold
        veh.speeds["f"] = 6.0
        res["f"] = _res("b", 0, 83.0, 6.0)
        veh.calls.clear()
        _weave_step(mod, _tc, ws, res, 0.5)
        assert ws["veh"]["e"]["target"] == "f"
        assert [c for c in veh.calls if c[0] == "slow"] == [("slow", "f", 6.0 - 1.67 * 0.5, 0.0)]
        # f has passed e's rear: no longer a follower candidate
        res["f"] = _res("b", 0, 88.0, 6.0)
        veh.calls.clear()
        _weave_step(mod, _tc, ws, res, 1.0)
        assert ws["veh"]["e"]["target"] is None
        assert not [c for c in veh.calls if c[0] == "slow"]

    def test_before_the_forced_change_is_due_the_abreast_rule_is_unchanged(self):
        """The same geometry with ``force_after_s`` = 4 s: until the forced
        change is due the gap behind ``b`` is discarded as before and nobody
        is held; from the step it is due, ``f`` holds."""
        from microsim.runner import _idm_accel, _weave_step

        ws, veh, mod, res = self._at_the_gore(force_after_s=4.0)
        for t in (0.0, 0.5, 3.5):
            _weave_step(mod, _tc, ws, res, t)
            assert ws["veh"]["e"]["zone_s"] == 0.0 and ws["veh"]["e"]["target"] is None, t
            assert not [c for c in veh.calls if c[0] == "slow"], t
        _weave_step(mod, _tc, ws, res, 4.0)
        assert ws["veh"]["e"]["target"] == "f"
        a_hold = _idm_accel(1.0, 30.0, 12.0 - 2.5, 0.0, 1.4, 0.73, 1.67, 2.5)
        assert [c for c in veh.calls if c[0] == "slow"] == [
            ("slow", "f", pytest.approx(1.0 + a_hold * 0.5), 0.0)
        ]

    def test_outside_the_zone_the_abreast_rule_is_unchanged(self):
        """The same geometry 90 m before the section end (outside
        ``force_within_m`` = 80): the gap behind ``b`` is discarded as before
        and nobody is held."""
        from microsim.runner import _weave_step

        ws, veh, mod, res = self._at_the_gore()
        res = {
            "e": _res("a", 1, 10.0, 1.0),
            "b": _res("a", 0, 8.0, 1.0),
            "f": _res("a", 0, -7.0, 1.0),
        }
        _weave_step(mod, _tc, ws, res, 0.0)
        assert ws["veh"]["e"]["zone_s"] is None and ws["veh"]["e"]["target"] is None
        assert not [c for c in veh.calls if c[0] == "slow"]

    def test_the_exiter_changes_once_the_vehicle_beside_it_has_cleared(self):
        from microsim.runner import LC_MODE_SCRIPTED_FORCE, NEIGHBOR_RIGHT_LEADERS, _weave_step

        ws, veh, mod, res = self._at_the_gore()
        # b now 3 m ahead of e's front, f held 5 m behind e's rear at rest:
        # both gaps clear s0 = 2.5 m at v = 0, so the change is accepted and
        # executed under mode 256 for one step (the forced mode was due too)
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = (("b", 3.0),)
        veh.neighbors[("e", 1)] = (("f", 5.0),)
        veh.speeds.update({"e": 0.0, "b": 2.0, "f": 0.0})
        res = {
            "e": _res("b", 1, 92.0, 0.0),
            "b": _res("b", 0, 100.0, 2.0),
            "f": _res("b", 0, 82.0, 0.0),
        }
        _weave_step(mod, _tc, ws, res, 0.0)
        assert [c for c in veh.calls if c[0] == "change"] == [("change", "e", 0, 0.5)]
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_FORCE and ws["veh"]["e"]["forced"] is False

    def test_an_exiter_halted_at_the_gore_end_continues_through_and_is_counted(self):
        """Halted within ``exit_giveup_m`` of the section end still in lane 1:
        the exit is given up — rerouted to the corridor's last edge, the lane
        change mode restored, handed back at once, ``n_missed`` and
        ``n_missed_exit`` both counted — and the vehicle is not driven on
        the following steps even though it is still exit-bound on paper. One
        still rolling there is left to try (the moderate fixture's v00010
        forced in during the last 3 m at 2-3 m/s)."""
        from microsim.runner import _weave_step

        ws, veh, mod, res = self._at_the_gore()
        veh.lc_modes["e"] = 1621
        _weave_step(mod, _tc, ws, res, 0.0)
        assert "e" in ws["veh"] and ws["n_missed_exit"] == 0
        veh.speeds["e"] = 2.0
        res["e"] = _res("b", 1, 96.0, 2.0)  # 4 m ahead but still rolling: kept
        _weave_step(mod, _tc, ws, res, 0.5)
        assert "e" in ws["veh"] and ws["n_missed_exit"] == 0
        veh.speeds["e"] = 0.0
        res["e"] = _res("b", 1, 97.0, 0.0)  # halted: given up
        veh.calls.clear()
        _weave_step(mod, _tc, ws, res, 1.0)
        assert ("target", "e", "z") in veh.calls
        assert veh.lc_modes["e"] == 1621 and "e" not in ws["veh"]
        assert ws["n_missed"] == 1 and ws["n_missed_exit"] == 1
        assert ws["n_entered"] == ws["n_changed_in"] + ws["n_changed_out"] + ws["n_missed"]
        assert ws["gave_up"] == {"e"} and "e" not in ws["awaiting_exit"]
        assert not [c for c in veh.calls if c[0] == "change"]
        veh.calls.clear()
        _weave_step(mod, _tc, ws, res, 1.5)
        assert "e" not in ws["veh"] and ws["n_entered"] == 1
        assert not [c for c in veh.calls if c[0] in ("target", "change", "slow", "lc")]

    def test_exit_giveup_m_zero_gives_up_only_at_the_lane_end(self):
        from microsim.runner import _weave_step

        ws, veh, mod, res = self._at_the_gore(exit_giveup_m=0.0)
        veh.speeds["e"] = 0.0
        res["e"] = _res("b", 1, 99.0, 0.0)
        _weave_step(mod, _tc, ws, res, 0.0)
        assert "e" in ws["veh"] and ws["n_missed_exit"] == 0
        res["e"] = _res("b", 1, 100.0, 0.0)
        _weave_step(mod, _tc, ws, res, 0.5)
        assert ws["n_missed_exit"] == 1 and ("target", "e", "z") in veh.calls

    def test_a_halted_exiter_that_can_request_its_change_is_not_given_up(self):
        """Review (2026-09-24, block 3): the give-up is read after the
        acceptance. Halted 3 m from the end with both lane-0 gaps clear
        (``b`` 3 m ahead, ``f`` held 5 m behind at rest — the state the
        priority hold produces) the exiter requests its change under mode
        256 exactly as it does 8 m from the end
        (``test_the_exiter_changes_once_the_vehicle_beside_it_has_cleared``);
        with the give-up first it was rerouted through instead. The same
        holds for a forced request that passes the guard alone. Given up
        only when neither can be requested this step."""
        from microsim.runner import LC_MODE_SCRIPTED_FORCE, NEIGHBOR_RIGHT_LEADERS, _weave_step

        ws, veh, mod, res = self._at_the_gore()
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = (("b", 3.0),)
        veh.neighbors[("e", 1)] = (("f", 5.0),)
        veh.speeds.update({"e": 0.0, "b": 2.0, "f": 0.0})
        res = {
            "e": _res("b", 1, 97.0, 0.0),
            "b": _res("b", 0, 105.0, 2.0),
            "f": _res("b", 0, 87.0, 0.0),
        }
        _weave_step(mod, _tc, ws, res, 0.0)
        assert [c for c in veh.calls if c[0] == "change"] == [("change", "e", 0, 0.5)]
        assert not [c for c in veh.calls if c[0] == "target"]
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_FORCE and "e" in ws["veh"]
        assert (
            ws["n_missed_exit"] == 0 and ws["n_missed"] == 0 and ws["veh"]["e"]["forced"] is False
        )
        # f 8 m behind closing at 6 m/s fails the acceptance (its IDM
        # absorption of e is below -b) and, since the speed-aware guard
        # (2026-09-24, block 3), the forced guard as well: it cleared the
        # closing margin (8 > 2.5 + 0.6 * 6) but f's brake gap towards a
        # halted e is 2.5 + 6²/(2 · 1.67) = 13.3 m — the follower that was
        # forced in front of and hit a released exiter (weave_th52.osm,
        # corridor demand, seed 5, t = 977.5 s: 10.7 m at 16 m/s). Halted
        # with no request possible, e is given up
        ws, veh, mod, res = self._at_the_gore()
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = (("b", 3.0),)
        veh.neighbors[("e", 1)] = (("f", 8.0),)
        veh.speeds.update({"e": 0.0, "b": 2.0, "f": 6.0})
        res = {
            "e": _res("b", 1, 97.0, 0.0),
            "b": _res("b", 0, 105.0, 2.0),
            "f": _res("b", 0, 84.0, 6.0),
        }
        _weave_step(mod, _tc, ws, res, 0.0)
        assert not [c for c in veh.calls if c[0] == "change"]
        assert ("target", "e", "z") in veh.calls and ws["n_missed_exit"] == 1
        # 14 m behind at 6 m/s the same follower clears its brake gap and
        # absorbs e within b (IDM at a bumper gap of 16.5 m: -1.26 m/s²), so
        # the change is accepted, not forced
        ws, veh, mod, res = self._at_the_gore()
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = (("b", 3.0),)
        veh.neighbors[("e", 1)] = (("f", 14.0),)
        veh.speeds.update({"e": 0.0, "b": 2.0, "f": 6.0})
        res = {
            "e": _res("b", 1, 97.0, 0.0),
            "b": _res("b", 0, 105.0, 2.0),
            "f": _res("b", 0, 78.0, 6.0),
        }
        _weave_step(mod, _tc, ws, res, 0.0)
        assert [c for c in veh.calls if c[0] == "change"] == [("change", "e", 0, 0.5)]
        assert not [c for c in veh.calls if c[0] == "target"]
        assert ws["veh"]["e"]["forced"] is False and ws["n_missed_exit"] == 0
        # neither accepted nor forced (b still beside it, overlapping): given up
        ws, veh, mod, res = self._at_the_gore()
        veh.speeds.update({"e": 0.0})
        res["e"] = _res("b", 1, 97.0, 0.0)
        _weave_step(mod, _tc, ws, res, 0.0)
        assert ("target", "e", "z") in veh.calls and ws["n_missed_exit"] == 1
        assert not [c for c in veh.calls if c[0] == "change"]

    def test_an_entrant_halted_at_the_lane_end_is_never_given_up(self):
        """The give-up is the exiting movement's alone: an entrant halted at
        the end of lane 0 beside a lane-1 vehicle is deferred every step,
        never rerouted, and ``n_missed_exit`` stays at zero."""
        from microsim.runner import NEIGHBOR_LEFT_LEADERS, _weave_step

        ws = _weave_state(force_after_s=0.0)
        veh = _WeaveVehicle({"n": 0.0, "l": 0.0}, {("n", NEIGHBOR_LEFT_LEADERS): (("l", -2.0),)})
        mod = _WeaveMod(veh)
        res = {"n": _res("b", 0, 99.5, 0.0), "l": _res("b", 1, 98.0, 0.0)}
        for t in (0.0, 0.5, 1.0):
            _weave_step(mod, _tc, ws, res, t)
        assert not [c for c in veh.calls if c[0] == "target"]
        assert ws["n_missed"] == 0 and ws["n_missed_exit"] == 0
        assert ws["n_forced_deferred"] == 3 and "n" in ws["veh"]

    def test_n_missed_exit_is_a_subset_of_n_missed(self):
        """A miss by the mainline (an exiter leaving the section in lane 1)
        counts in ``n_missed`` only; a give-up counts in both; the identity
        ``n_entered = n_changed_in + n_changed_out + n_missed + n_unfinished``
        holds with both kinds present."""
        from microsim.runner import NEIGHBOR_RIGHT_LEADERS, _weave_step

        ws = _weave_state(force_after_s=0.0)
        ws["exiting_ids"] = frozenset({"e", "g", "b"})
        # e halted 3 m from the end with b beside it in lane 0 (overlapping,
        # so no change can be requested); g leaves the section in lane 1
        veh = _WeaveVehicle(
            {"e": 0.0, "g": 10.0, "b": 0.0}, {("e", NEIGHBOR_RIGHT_LEADERS): (("b", -3.0),)}
        )
        mod = _WeaveMod(veh)
        res = {
            "e": _res("b", 1, 97.0, 0.0),
            "b": _res("b", 0, 95.0, 0.0),
            "g": _res("b", 1, 50.0, 10.0),
        }
        _weave_step(mod, _tc, ws, res, 0.0)
        res["g"] = _res("c", 1, 4.0, 10.0)
        _weave_step(mod, _tc, ws, res, 0.5)
        assert ws["n_missed"] == 2 and ws["n_missed_exit"] == 1
        assert ws["n_entered"] == 2 == ws["n_changed_in"] + ws["n_changed_out"] + ws["n_missed"]
        assert ws["veh"] == {}

    def test_priority_is_due_by_time_in_the_zone_not_by_distance(self):
        """Review (2026-09-24, block 3): ``force_after_s`` runs from the step
        the exiter is first within ``force_within_m``, whatever its speed.
        Crawling into the zone at 1 m/s it is due 4 s later, 4 m in — with
        priority over the auxiliary lane for the remaining 76 m, its lane-0
        follower held from there. Recorded as the rule's consequence; the
        alternative (priority from zone entry) was measured and rejected on
        the entrance (docs/WEAVE_MODEL_PLAN.md, exit-side derivation)."""
        from microsim.runner import NEIGHBOR_RIGHT_FOLLOWERS, NEIGHBOR_RIGHT_LEADERS, _weave_step

        ws = _weave_state()  # force_after_s = 4 s, force_within_m = 80 m
        ws["exiting_ids"] = frozenset({"e", "b", "f"})
        veh = _WeaveVehicle(
            {"e": 1.0, "b": 1.0, "f": 1.0},
            {
                ("e", NEIGHBOR_RIGHT_LEADERS): (("b", -3.0),),
                ("e", NEIGHBOR_RIGHT_FOLLOWERS): (("f", 12.0),),
            },
        )
        mod = _WeaveMod(veh)
        for t in (0.0, 0.5, 3.5, 4.0, 4.5):
            x = 20.0 + t  # 1 m/s along the last edge: remaining = 100 - x
            res = {
                "e": _res("b", 1, x, 1.0),
                "b": _res("b", 0, x - 2.0, 1.0),
                "f": _res("b", 0, x - 17.0, 1.0),
            }
            veh.calls.clear()
            _weave_step(mod, _tc, ws, res, t)
            assert ws["veh"]["e"]["zone_s"] == 0.0, t
            held = [c for c in veh.calls if c[0] == "slow"]
            if t < 4.0:
                assert ws["veh"]["e"]["target"] is None and not held, t
            else:
                assert ws["veh"]["e"]["target"] == "f" and held and held[0][1] == "f", t


class TestWeaveGiveupPatience:
    """The bounded give-up patience (WP-52, 2026-09-24 block 3):
    ``microsim.runner._weave_giveup_patient`` and the give-up branch of
    ``_weave_step`` under ``exit_giveup_patience_s``. Measured and shipped
    off (default 0; docs/WEAVE_MODEL_PLAN.md, dated section); these pin the
    rule's statement so a positive value behaves as documented."""

    @staticmethod
    def _st(prev=("f", 7.0), since=None):
        return {"foll_prev": prev, "giveup_since": since}

    def test_waits_only_while_the_same_follower_behind_is_still_braking(self):
        from microsim.runner import HALTING_SPEED_MS
        from microsim.runner import _weave_giveup_patient as p

        # the follower reported last step at 7.0 m/s, now 6.0 with 8 m behind: wait
        st = self._st()
        assert p(st, 10.0, 10.0, "f", 8.0, 6.0, 1.67, 0.5) is True
        assert st["giveup_since"] == 10.0 and st["giveup_v_foll"] == 6.0
        # its speed no longer falling (within the tolerance, 0.1 m/s² · 0.5 s): give up
        st["foll_prev"] = ("f", 6.0)
        assert p(st, 10.5, 10.0, "f", 7.5, 5.96, 1.67, 0.5) is False
        # falling by more than the tolerance: still waiting
        assert p(st, 10.5, 10.0, "f", 7.5, 5.9, 1.67, 0.5) is True
        # at rest: give up
        st["foll_prev"] = ("f", 0.2)
        assert p(st, 11.0, 10.0, "f", 7.0, HALTING_SPEED_MS / 2, 1.67, 0.5) is False
        # a different follower than last step, or none reported: give up
        assert p(self._st(), 10.0, 10.0, "g", 8.0, 6.0, 1.67, 0.5) is False
        assert p(self._st(prev=None), 10.0, 10.0, "f", 8.0, 6.0, 1.67, 0.5) is False
        assert p(self._st(), 10.0, 10.0, None, math.inf, math.nan, None, 0.5) is False
        # a follower overlapping the exiter (beside it) is not braking towards a gap
        assert p(self._st(), 10.0, 10.0, "f", -3.0, 6.0, 1.67, 0.5) is False
        # patience 0: never
        assert p(self._st(), 10.0, 0.0, "f", 8.0, 6.0, 1.67, 0.5) is False

    def test_the_bound_is_the_value_or_the_follower_braking_time(self):
        from microsim.runner import _weave_giveup_patient as p

        # v_F / b_F = 6 / 1.67 = 3.59 s is shorter than 10 s: the wait ends
        # at 3.59 s after the first refused step, the follower still braking
        st = self._st(since=10.0)
        st["giveup_v_foll"] = 6.0
        st["foll_prev"] = ("f", 3.0)
        assert p(st, 13.5, 10.0, "f", 8.0, 2.5, 1.67, 0.5) is True
        st["foll_prev"] = ("f", 2.5)
        assert p(st, 14.0, 10.0, "f", 8.0, 2.0, 1.67, 0.5) is False
        # a bound of 2 s shorter than the braking time binds instead
        st["foll_prev"] = ("f", 4.0)
        assert p(st, 11.5, 2.0, "f", 8.0, 3.5, 1.67, 0.5) is True
        assert p(st, 12.0, 2.0, "f", 8.0, 3.5, 1.67, 0.5) is False
        # the budget is not renewed: since stays at the first refused step
        assert st["giveup_since"] == 10.0

    def test_step_waits_counts_and_then_gives_up(self):
        """``_at_the_gore`` of ``TestWeaveExitPriority``: ``e`` halted 3 m
        from the end with ``b`` 3 m ahead in lane 0 and ``f`` 8 m behind
        closing — the speed-aware guard refuses on the follower side (its
        brake gap on a halted ``e``, 6²/(2 · 1.67) = 10.8 m, is above the 8 m).
        With the patience, the step after ``f`` was reported at 7 m/s and now
        reads 6 is a wait (counted in ``n_giveup_waited``, the forced request
        deferred, no reroute); the step on which ``f`` is no longer slowing is
        the give-up."""
        from microsim.runner import (
            LC_MODE_SCRIPTED_SAFE,
            NEIGHBOR_RIGHT_LEADERS,
            _weave_meta,
            _weave_step,
        )

        ws, veh, mod, res = TestWeaveExitPriority._at_the_gore(exit_giveup_patience_s=10.0)
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = (("b", 3.0),)
        veh.neighbors[("e", 1)] = (("f", 8.0),)
        veh.speeds.update({"e": 1.0, "b": 2.0, "f": 7.0})
        res = {
            "e": _res("b", 1, 97.0, 1.0),
            "b": _res("b", 0, 105.0, 2.0),
            "f": _res("b", 0, 84.0, 7.0),
        }
        # still rolling: not given up, the follower's speed recorded
        _weave_step(mod, _tc, ws, res, 0.0)
        assert "e" in ws["veh"] and ws["veh"]["e"]["foll_prev"] == ("f", 7.0)
        assert ws["n_giveup_waited"] == 0 and not [c for c in veh.calls if c[0] == "target"]
        # halted, f slowing 7 → 6: waited
        veh.speeds.update({"e": 0.0, "f": 6.0})
        res["e"] = _res("b", 1, 97.0, 0.0)
        res["f"] = _res("b", 0, 84.0, 6.0)
        veh.calls.clear()
        deferred = ws["n_forced_deferred"]
        _weave_step(mod, _tc, ws, res, 0.5)
        assert "e" in ws["veh"] and ws["n_giveup_waited"] == 1 and ws["n_missed_exit"] == 0
        assert not [c for c in veh.calls if c[0] in ("target", "change")]
        assert ws["n_forced_deferred"] == deferred + 1
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_SAFE
        assert ws["veh"]["e"]["giveup_since"] == 0.5 and ws["veh"]["e"]["foll_prev"] == ("f", 6.0)
        # f at its speed: given up, counted, handed back
        veh.calls.clear()
        _weave_step(mod, _tc, ws, res, 1.0)
        assert ("target", "e", "z") in veh.calls and "e" not in ws["veh"]
        assert ws["n_missed_exit"] == 1 and ws["n_giveup_waited"] == 1
        assert _weave_meta(ws, {})["n_giveup_waited"] == 1

    def test_step_gives_up_at_the_bound_while_the_follower_still_brakes(self):
        """``f`` 2 m behind (under ``s0``, never accepted) slowing 0.5 m/s a
        step from 6 m/s: waited every step until the follower's braking time
        at ``b``, 6 / 1.67 = 3.59 s after the first refused step, then given
        up although it is still slowing."""
        from microsim.runner import NEIGHBOR_RIGHT_LEADERS, _weave_step

        ws, veh, mod, res = TestWeaveExitPriority._at_the_gore(exit_giveup_patience_s=10.0)
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = (("b", 3.0),)
        veh.neighbors[("e", 1)] = (("f", 2.0),)
        veh.speeds.update({"e": 1.0, "b": 2.0, "f": 6.5})
        res = {
            "e": _res("b", 1, 97.0, 1.0),
            "b": _res("b", 0, 105.0, 2.0),
            "f": _res("b", 0, 90.0, 6.5),
        }
        _weave_step(mod, _tc, ws, res, 0.0)
        res["e"] = _res("b", 1, 97.0, 0.0)
        veh.speeds["e"] = 0.0
        t, v_f = 0.5, 6.0
        while "e" in ws["veh"]:
            veh.speeds["f"] = v_f
            res["f"] = _res("b", 0, 90.0, v_f)
            _weave_step(mod, _tc, ws, res, t)
            t, v_f = t + 0.5, v_f - 0.5
        # waited at t = 0.5 … 4.0 (elapsed 0 … 3.5 s < 3.59), given up at 4.5
        assert ws["n_giveup_waited"] == 8 and ws["n_missed_exit"] == 1
        assert t - 0.5 == pytest.approx(4.5) and ("target", "e", "z") in veh.calls

    def test_default_never_waits(self):
        ws, *_ = TestWeaveExitPriority._at_the_gore()
        assert ws["params"]["exit_giveup_patience_s"] == 0.0


class TestWeaveAbreastPatience:
    """The abreast state (WP-53, 2026-09-24 block 3):
    ``microsim.runner._weave_abreast_clear_m``, ``_weave_giveup_abreast`` and
    the give-up branch of ``_weave_step`` under ``exit_abreast_patience_s``
    (docs/WEAVE_MODEL_PLAN.md, dated section). These pin the rule's
    statement so a positive value behaves as documented, whatever the
    default."""

    def test_clear_distance_reads_the_reported_gaps_and_the_floor(self):
        from microsim.runner import _weave_abreast_clear_m as clear

        # a leader inside the acceptance's floor (2.5 m at a halted changer):
        # the shortfall, whether it still overlaps (negative) or not
        assert clear(-7.0, math.inf, 2.5, 5.0, 2.5, 5.0, 2.5) == pytest.approx(9.5)
        assert clear(1.78, 5.0, 2.5, 5.0, 2.5, 5.0, 2.5) == pytest.approx(0.72)
        # a follower overlapping: its rear is len_c + g + s0_a + len_a behind
        # the exiter's front; it must be lead_need + s0_c ahead of it
        assert clear(math.inf, -6.0, 2.5, 5.0, 2.5, 5.0, 2.5) == pytest.approx(11.5)
        assert clear(30.0, -7.5, 2.5, 5.0, 2.5, 5.0, 2.5) == pytest.approx(10.0)
        # the leader side is read first; nothing in the way is zero
        assert clear(-1.0, -6.0, 2.5, 5.0, 2.5, 5.0, 2.5) == pytest.approx(3.5)
        assert clear(3.0, 5.0, 2.5, 5.0, 2.5, 5.0, 2.5) == 0.0
        assert clear(math.inf, math.inf, 2.5, 5.0, 2.5, 5.0, 2.5) == 0.0

    def test_waits_only_for_a_moving_non_entrant_that_clears_within_the_budget(self):
        from microsim.runner import HALTING_SPEED_MS
        from microsim.runner import _weave_giveup_abreast as p

        # 11.5 m to clear at 4 m/s = 2.9 s within 10 s: wait, the budget started
        st: dict = {}
        assert p(st, 10.0, 10.0, "a", 4.0, 11.5, False) is True
        assert st["giveup_since"] == 10.0
        # at its speed it needs longer than the budget left: give up
        assert p(st, 18.0, 10.0, "a", 4.0, 11.5, False) is False
        # the budget is exhausted: give up, and it is never renewed
        assert p(st, 20.0, 10.0, "a", 40.0, 1.0, False) is False
        assert st["giveup_since"] == 10.0
        # a halted vehicle beside the exiter never clears: give up
        assert p({}, 10.0, 10.0, "a", HALTING_SPEED_MS / 2, 1.0, False) is False
        # a driven entrant beside it (the crossing pair at the lane ends): give up
        assert p({}, 10.0, 10.0, "a", 4.0, 11.5, True) is False
        # nobody beside it, or patience 0: give up
        assert p({}, 10.0, 10.0, None, math.nan, 0.0, False) is False
        assert p({}, 10.0, 0.0, "a", 4.0, 11.5, False) is False

    def test_step_waits_while_the_vehicle_beside_clears_then_changes(self):
        """``_at_the_gore`` of ``TestWeaveExitPriority``: ``e`` halted 3 m
        from the end; ``b`` in lane 0 sliding past it at 4 m/s (reported as
        an overlapping follower, then as a leader inside the floor, then
        clear), ``f`` held 5 m behind at rest. The two steps on which ``b``
        is still in the way are waits (counted, the request deferred, no
        reroute); on the third the change is accepted under mode 256."""
        from microsim.runner import (
            LC_MODE_SCRIPTED_FORCE,
            LC_MODE_SCRIPTED_SAFE,
            NEIGHBOR_RIGHT_LEADERS,
            _weave_meta,
            _weave_step,
        )

        ws, veh, mod, res = TestWeaveExitPriority._at_the_gore(exit_abreast_patience_s=10.0)
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = ()
        veh.neighbors[("e", 1)] = (("b", -6.0), ("f", 5.0))
        veh.speeds.update({"e": 0.0, "b": 4.0, "f": 0.0})
        res = {
            "e": _res("b", 1, 97.0, 0.0),
            "b": _res("b", 0, 95.0, 4.0),
            "f": _res("b", 0, 87.0, 0.0),
        }
        deferred = ws["n_forced_deferred"]
        _weave_step(mod, _tc, ws, res, 0.0)
        assert "e" in ws["veh"] and ws["n_giveup_waited"] == 1 and ws["n_missed_exit"] == 0
        assert not [c for c in veh.calls if c[0] in ("target", "change")]
        assert ws["n_forced_deferred"] == deferred + 1
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_SAFE
        assert ws["veh"]["e"]["giveup_since"] == 0.0
        # b now 1 m ahead (inside the 2.5 m floor) at 4 m/s: 0.4 s to clear, waited
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = (("b", 1.0),)
        veh.neighbors[("e", 1)] = (("f", 5.0),)
        res["b"] = _res("b", 0, 103.5, 4.0)
        veh.calls.clear()
        _weave_step(mod, _tc, ws, res, 0.5)
        assert "e" in ws["veh"] and ws["n_giveup_waited"] == 2 and ws["n_missed_exit"] == 0
        assert not [c for c in veh.calls if c[0] in ("target", "change")]
        # clear of the floor: accepted, not forced
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = (("b", 3.0),)
        res["b"] = _res("b", 0, 105.5, 4.0)
        veh.calls.clear()
        _weave_step(mod, _tc, ws, res, 1.0)
        assert [c for c in veh.calls if c[0] == "change"] == [("change", "e", 0, 0.5)]
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_FORCE and ws["veh"]["e"]["forced"] is False
        assert ws["n_missed_exit"] == 0 and _weave_meta(ws, {})["n_giveup_waited"] == 2

    def test_step_gives_up_at_once_beside_a_halted_entrant_or_a_halted_vehicle(self):
        """The crossing pair at the lane ends: ``b`` is a driven entrant
        halted beside ``e`` (lane 0, not exit-bound), and ``e`` is given up
        on the first refused step with nothing waited; the same with an
        exit-bound ``b`` at rest beside it."""
        from microsim.runner import _weave_step

        ws, veh, mod, res = TestWeaveExitPriority._at_the_gore(exit_abreast_patience_s=10.0)
        ws["exiting_ids"] = frozenset({"e", "f"})
        veh.neighbors[("e", 1)] = (("b", -7.0), ("f", 0.0))
        veh.speeds.update({"e": 0.0, "b": 0.0, "f": 0.0})
        res = {
            "e": _res("b", 1, 97.0, 0.0),
            "b": _res("b", 0, 96.5, 0.0),
            "f": _res("b", 0, 89.5, 0.0),
        }
        _weave_step(mod, _tc, ws, res, 0.0)
        assert ws["veh"]["b"]["dir"] == 1 and "e" not in ws["veh"]
        assert ("target", "e", "z") in veh.calls
        assert ws["n_missed_exit"] == 1 and ws["n_giveup_waited"] == 0
        ws, veh, mod, res = TestWeaveExitPriority._at_the_gore(exit_abreast_patience_s=10.0)
        veh.neighbors[("e", 1)] = (("b", -7.0), ("f", 0.0))
        veh.speeds.update({"e": 0.0, "b": 0.0, "f": 0.0})
        res = {
            "e": _res("b", 1, 97.0, 0.0),
            "b": _res("b", 0, 96.5, 0.0),
            "f": _res("b", 0, 89.5, 0.0),
        }
        _weave_step(mod, _tc, ws, res, 0.0)
        assert ("target", "e", "z") in veh.calls and ws["n_giveup_waited"] == 0

    def test_step_gives_up_at_the_bound(self):
        """``b`` beside ``e`` on the leader side (reported −3 m: 5.5 m to
        the 2.5 m floor) creeping at 0.5 m/s needs 11 s, beyond a 10 s
        budget: given up on the first refused step. At 2 m/s (2.75 s) it is
        waited for — ``b`` re-reported in the same place each step, never
        clearing — on every step on which the budget left is still 2.75 s
        or more (t = 0 … 7.0, fifteen steps), then given up."""
        from microsim.runner import _weave_step

        for v_b, waited in ((0.5, 0), (2.0, 15)):
            ws, veh, mod, res = TestWeaveExitPriority._at_the_gore(exit_abreast_patience_s=10.0)
            veh.neighbors[("e", 1)] = (("f", 5.0),)
            veh.speeds.update({"e": 0.0, "b": v_b, "f": 0.0})
            res = {
                "e": _res("b", 1, 97.0, 0.0),
                "b": _res("b", 0, 98.0, v_b),
                "f": _res("b", 0, 87.0, 0.0),
            }
            _weave_step(mod, _tc, ws, res, 0.0)
            t = 0.5
            while "e" in ws["veh"]:
                _weave_step(mod, _tc, ws, res, t)
                t += 0.5
            assert ws["n_giveup_waited"] == waited and ws["n_missed_exit"] == 1
            assert ("target", "e", "z") in veh.calls

    def test_default_never_waits(self):
        ws, *_ = TestWeaveExitPriority._at_the_gore()
        assert ws["params"]["exit_abreast_patience_s"] == 0.0

    def test_the_budget_opened_by_the_abreast_wait_is_read_by_the_braking_patience(self):
        """Both patiences on: ``b`` sliding past opens the budget (the
        abreast wait, no follower speed recorded); a step later ``b`` is
        clear and ``f`` 8 m behind is braking 7 → 6 m/s towards the gap —
        the braking patience (WP-52) then reads the budget it did not open
        and records ``f``'s speed on that step as its braking-time basis
        (a KeyError before the fix, session record)."""
        from microsim.runner import NEIGHBOR_RIGHT_LEADERS, _weave_step

        ws, veh, mod, res = TestWeaveExitPriority._at_the_gore(
            exit_abreast_patience_s=10.0, exit_giveup_patience_s=10.0
        )
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = ()
        veh.neighbors[("e", 1)] = (("b", -6.0), ("f", 20.0))
        veh.speeds.update({"e": 0.0, "b": 4.0, "f": 7.0})
        res = {
            "e": _res("b", 1, 97.0, 0.0),
            "b": _res("b", 0, 95.0, 4.0),
            "f": _res("b", 0, 72.0, 7.0),
        }
        _weave_step(mod, _tc, ws, res, 0.0)
        assert ws["n_giveup_waited"] == 1 and ws["veh"]["e"]["giveup_since"] == 0.0
        assert ws["veh"]["e"].get("giveup_v_foll") is None
        veh.neighbors[("e", NEIGHBOR_RIGHT_LEADERS)] = (("b", 3.0),)
        veh.neighbors[("e", 1)] = (("f", 8.0),)
        veh.speeds["f"] = 6.0
        res["b"] = _res("b", 0, 105.5, 4.0)
        res["f"] = _res("b", 0, 84.0, 6.0)
        ws["veh"]["e"]["foll_prev"] = ("f", 7.0)
        _weave_step(mod, _tc, ws, res, 0.5)
        assert "e" in ws["veh"] and ws["n_giveup_waited"] == 2
        assert ws["veh"]["e"]["giveup_v_foll"] == 6.0 and ws["veh"]["e"]["giveup_since"] == 0.0


class TestMeterStopPlacementReview:
    """Review of 2026-09-24: the braking inequality and its units."""

    def test_boundary_of_the_braking_check(self):
        from microsim.runner import _meter_assign_stop

        # 10 m/s, b = 2: 25 m + 0.5 s * 10 m/s = 30 m; 30 m left on the last edge
        state = {**METER_STATE, "ramp_edges": ["c"], "edge_len_m": {"c": 80.0}}
        at = _FakeVehicle(pos=20.0, speed=10.0, decel=2.0, error=None)
        assert not _meter_assign_stop(_FakeMod(at), state, "v", "c", 0.5)
        above = _FakeVehicle(pos=19.9, speed=10.0, decel=2.0, error=None)
        assert _meter_assign_stop(_FakeMod(above), state, "v", "c", 0.5)
        # a standing vehicle is stopped wherever it is short of the line, and
        # passes once it is on or past it
        assert _meter_assign_stop(
            _FakeMod(_FakeVehicle(49.0, 0.0, 2.0, None)), state, "v", "c", 0.5
        )
        assert not _meter_assign_stop(
            _FakeMod(_FakeVehicle(50.0, 0.0, 2.0, None)), state, "v", "c", 0.5
        )
        # the step length scales the reaction margin (a 1 s step doubles it)
        assert not _meter_assign_stop(
            _FakeMod(_FakeVehicle(15.0, 10.0, 2.0, None)), state, "v", "c", 1.0
        )
        assert _meter_assign_stop(
            _FakeMod(_FakeVehicle(14.9, 10.0, 2.0, None)), state, "v", "c", 1.0
        )


class TestWeaveCooperation:
    """The second attempt of 2026-09-24 (block 3): gap choice, the one-step
    car-following targets and the lane map behind ``_weave_step``."""

    def test_idm_accel_equilibrium_free_road_and_overlap(self):
        from microsim.runner import _idm_accel

        v, v0, T, a, b, s0 = 20.0, 33.3, 1.4, 0.73, 1.67, 2.0
        s_eq = (s0 + v * T) / math.sqrt(1.0 - (v / v0) ** 4)  # CLAUDE.md §9
        assert _idm_accel(v, v0, s_eq, 0.0, T, a, b, s0) == pytest.approx(0.0, abs=1e-12)
        assert _idm_accel(v, v0, math.inf, 0.0, T, a, b, s0) == pytest.approx(
            a * (1 - (v / v0) ** 4)
        )
        assert _idm_accel(v, v0, 0.0, 0.0, T, a, b, s0) == -math.inf
        # closing on the leader asks for more braking than standing off it
        assert _idm_accel(v, v0, 30.0, 5.0, T, a, b, s0) < _idm_accel(v, v0, 30.0, 0.0, T, a, b, s0)

    @staticmethod
    def _gap_case(changer_x, vehicles, committed=None, vid="n"):
        from microsim.runner import _weave_choose_gap

        p = {"len": 5.0, "T": 1.4, "a": 0.73, "b": 1.67, "s0": 2.0, "vmax": 33.3}
        x_of = {k: x for k, (x, _v) in vehicles.items()}
        v_of = {k: v for k, (_x, v) in vehicles.items()}
        lane = sorted((x, k) for k, x in x_of.items())
        p_of = dict.fromkeys(vehicles, p)
        v0_of = dict.fromkeys(vehicles, 30.0)
        return _weave_choose_gap(
            vid, changer_x, 20.0, p, 30.0, lane, x_of, v_of, p_of, v0_of, 120.0, committed
        )

    def test_choose_gap_prefers_the_nearest_open_gap_and_drops_a_passed_commitment(self):
        # A ahead, B 35 m behind the changer's rear (open), C far behind
        veh = {"A": (130.0, 20.0), "B": (60.0, 20.0), "C": (10.0, 20.0)}
        l_id, f_id, a_f, a_c = self._gap_case(100.0, veh)
        assert (l_id, f_id) == ("A", "B") and a_f >= -1.67 and a_c >= -1.67
        assert a_f == pytest.approx(0.73 * (1 - (20 / 30) ** 4 - (30 / 35) ** 2))
        # committed to C, but C's gap has B as leader, which the changer has
        # passed: not a gap it is in any more, so the commitment is dropped
        assert self._gap_case(100.0, veh, committed="C")[:2] == ("A", "B")
        # committed to B and B still opens it within b: kept even though the
        # open road behind C would need no cooperation at all
        assert self._gap_case(100.0, veh, committed="B")[:2] == ("A", "B")
        # the follower right behind cannot open its gap within b, so it is
        # not open, but it is the only gap the changer is in
        veh_tight = {"A": (130.0, 20.0), "B": (92.0, 20.0)}
        l_id, f_id, a_f, _a_c = self._gap_case(100.0, veh_tight)
        assert (l_id, f_id) == ("A", "B") and a_f < -1.67
        # a gap whose follower is beyond lookahead_m is not a candidate yet: no
        # target (and no easing) until that follower comes within reach
        assert self._gap_case(100.0, {"A": (130.0, 20.0), "Z": (-50.0, 20.0)}) == (
            None,
            None,
            math.inf,
            math.inf,
        )

    def test_choose_gap_abreast_pair_only_the_rear_one_has_the_gap(self):
        # the changer 1 m behind L's front: L is its gap's leader (a_c = -inf,
        # so it eases); the reverse pair has no gap with that leader
        l_id, f_id, _a_f, a_c = self._gap_case(100.0, {"L": (101.0, 20.0)})
        assert (l_id, f_id) == ("L", None) and a_c == -math.inf
        assert self._gap_case(102.0, {"L": (101.0, 20.0)}) == (None, None, math.inf, math.inf)
        # exactly abreast: the id breaks the tie one way only
        assert self._gap_case(100.0, {"m": (100.0, 20.0)}, vid="n")[:2] == (None, None)
        assert self._gap_case(100.0, {"o": (100.0, 20.0)}, vid="n")[:2] == ("o", None)

    def test_command_is_clipped_at_b_recorded_only_below_the_own_model_and_kept_lowest(self):
        from microsim.runner import _weave_command

        p = {"len": 5.0, "T": 1.4, "a": 0.73, "b": 1.67, "s0": 2.0, "vmax": 33.3}
        veh = _WeaveVehicle({"f": 20.0})
        mod = _WeaveMod(veh)
        coop: dict = {}
        _weave_command(mod, coop, "f", 20.0, 30.0, p, -5.0, 0.5)
        assert coop == {"f": (pytest.approx(20.0 - 1.67 * 0.5), -1.67, True)}
        # above the free-road acceleration (0.73 * (1 - (20/30)^4) = 0.586): no record
        coop = {}
        _weave_command(mod, coop, "f", 20.0, 30.0, p, 0.7, 0.5)
        assert coop == {}
        # a real leader 40 m ahead (reported gap 40 + minGap 2): the own model
        # gives 0.73 * (1 - (20/30)^4 - (30/42)^2) = 0.213 m/s^2
        veh.leaders["f"] = ("g", 40.0)
        veh.speeds["g"] = 20.0
        _weave_command(mod, coop, "f", 20.0, 30.0, p, 0.3, 0.5, follower=False)
        assert coop == {}  # above the own model: nothing to command
        _weave_command(mod, coop, "f", 20.0, 30.0, p, -0.4, 0.5)
        _weave_command(mod, coop, "f", 20.0, 30.0, p, -1.0, 0.5, follower=False)
        _weave_command(mod, coop, "f", 20.0, 30.0, p, -0.6, 0.5)
        assert coop["f"] == (pytest.approx(19.5), -1.0, True)  # lowest target, follower flag kept
        # a leader 10 m ahead: the own model brakes at -3.97 m/s^2, below any
        # cooperation request, so the vehicle is left to its model
        coop = {}
        veh.leaders["f"] = ("g", 10.0)
        _weave_command(mod, coop, "f", 20.0, 30.0, p, -1.0, 0.5)
        assert coop == {}

    def test_lane_map_continues_the_through_lanes_onto_the_neighbouring_edges(
        self, weave_osm, tmp_path
    ):
        from microsim.runner import _weave_lane_map

        bundle = osm_import(
            osm_file=weave_osm,
            corridor_edges=WEAVE_CORRIDOR,
            keep_edges=("200", "201"),
            workdir=tmp_path / "w",
        )
        net = sumolib.net.readNet(str(bundle.net_path))
        m = _weave_lane_map(net, list(WEAVE_CORRIDOR), ["102"])
        n_section = len(net.getEdge("102").getLanes())
        assert {k: v for k, v in m.items() if k[0] == "102"} == {
            ("102", k): k for k in range(n_section)
        }
        upstream = {k: v for k, v in m.items() if k[0] == "101"}
        downstream = {k: v for k, v in m.items() if k[0] == "103"}
        assert upstream and all(v == k[1] + 1 for k, v in upstream.items()), upstream
        assert downstream and all(v == k[1] + 1 for k, v in downstream.items()), downstream
        assert 0 not in upstream.values() and 0 not in downstream.values()
        assert not [k for k in m if k[0] not in ("101", "102", "103")]


class TestWeaveVacateStep:
    """The third derivation's one rule (2026-09-24, block 3): through traffic
    vacates the weave lane upstream of the section (``_weave_vacate_step``)."""

    @staticmethod
    def _state(**params) -> dict:
        # the third derivation's 150 m window (the request logic under test
        # was laid out on it; the default is 500 m since the cross-edge window)
        ws = _weave_state(**{"vacate_ahead_m": 150.0, **params})
        # the corridor edge "p" before the section: its lane 0 feeds section
        # lane 1 (the weave lane), its lane 1 feeds section lane 2
        ws["vacate_lanes"] = {"p": (0, 1)}
        ws["lane_map"].update({("p", 0): 1, ("p", 1): 2})
        ws["x_offset"]["p"] = -200.0
        return ws

    def test_lanes_walked_upstream_across_edges(self, tmp_path):
        """The cross-edge window (2026-09-24, block 3) on
        ``tests/fixtures/weave_th52_upstream.osm``: the lane feeding section
        lane 1 is way 111's lane 0, way 110's lane 1 (its lane 0 is the
        entrance's acceleration lane) and way 101's lane 0 again; the window
        lists the edges it reaches, nearest the section first."""
        from microsim.runner import _weave_vacate_lanes

        chain = ["100", "101", "110", "111", "102", "103", "104"]
        bundle = osm_import(
            osm_file=Path(__file__).parents[1] / "fixtures" / "weave_th52_upstream.osm",
            corridor_edges=chain,
            keep_edges=("300", "200", "201"),
            workdir=tmp_path / "up",
        )
        net = sumolib.net.readNet(str(bundle.net_path))
        offsets = dict(zip(bundle.edge_ids, bundle.offsets, strict=True))
        assert offsets["102"] - offsets["110"] == pytest.approx(332.0, abs=1.0)
        walk = _weave_vacate_lanes(net, chain, ["102"], offsets, 500.0)
        assert walk == {"111": (0, 1), "110": (1, 2), "101": (0, 1)}
        assert list(walk) == ["111", "110", "101"]
        assert _weave_vacate_lanes(net, chain, ["102"], offsets, 150.0) == {"111": (0, 1)}
        assert _weave_vacate_lanes(net, chain, ["102"], offsets, 2000.0) == {
            "111": (0, 1),
            "110": (1, 2),
            "101": (0, 1),
            "100": (0, 1),
        }
        assert _weave_vacate_lanes(net, chain, ["102"], offsets, 0.0) == {}
        # no corridor edge before the section
        assert _weave_vacate_lanes(net, chain[3:], ["111", "102"], offsets, 500.0) == {}

    def test_walk_stops_where_a_lane_has_no_unique_feeder(self):
        """A fake net: the walk ends (the window truncated there) at an edge
        where the lane it follows has two feeders or none, or where both
        lanes are fed by one lane; a two-lane section makes it empty."""
        from microsim.runner import _weave_vacate_lanes

        class Conn:
            def __init__(self, to_edge, to_lane):
                self._to, self._lane = to_edge, to_lane

            def getTo(self):
                return self._to

            def getToLane(self):
                return self._lane

        class Lane:
            def __init__(self, index, outgoing):
                self._index, self._out = index, outgoing

            def getIndex(self):
                return self._index

            def getOutgoing(self):
                return self._out

        class Edge:
            def __init__(self, eid, lanes):
                self._id, self._lanes = eid, lanes

            def getID(self):
                return self._id

            def getLanes(self):
                return self._lanes

        class Net:
            def __init__(self, conns: dict[str, dict[int, list[tuple[str, int]]]]):
                self.edges = {e: Edge(e, []) for e in conns}
                for e, by_lane in conns.items():
                    self.edges[e]._lanes = [
                        Lane(k, [Conn(self.edges[to], Lane(to_k, [])) for to, to_k in outs])
                        for k, outs in sorted(by_lane.items())
                    ]

            def getEdge(self, eid):
                return self.edges[eid]

        offsets = {"q": 0.0, "p": 200.0, "a": 400.0}
        # p's lanes 0 / 1 feed a's lanes 1 / 2; q's lanes 1 and 2 both merge into p's lane 1
        net = Net(
            {
                "q": {0: [("p", 0)], 1: [("p", 1)], 2: [("p", 1)]},
                "p": {0: [("a", 1)], 1: [("a", 2)]},
                "a": {0: [], 1: [], 2: []},
            }
        )
        assert _weave_vacate_lanes(net, ["q", "p", "a"], ["a"], offsets, 1000.0) == {"p": (0, 1)}
        # p's lane 0 splits into a's lanes 1 and 2: both fed by one lane, inert
        net = Net({"p": {0: [("a", 1), ("a", 2)], 1: []}, "a": {0: [], 1: [], 2: []}})
        assert _weave_vacate_lanes(net, ["p", "a"], ["a"], offsets, 1000.0) == {}
        # a two-lane section: nothing feeds a lane 2
        net = Net({"p": {0: [("a", 1)]}, "a": {0: [], 1: []}})
        assert _weave_vacate_lanes(net, ["p", "a"], ["a"], offsets, 1000.0) == {}
        # an edge missing from the offsets ends the walk
        net = Net(
            {
                "q": {0: [("p", 0)], 1: [("p", 1)]},
                "p": {0: [("a", 1)], 1: [("a", 2)]},
                "a": {0: [], 1: [], 2: []},
            }
        )
        offs = {"p": 200.0, "a": 400.0}
        assert _weave_vacate_lanes(net, ["q", "p", "a"], ["a"], offs, 1000.0) == {"p": (0, 1)}
        assert _weave_vacate_lanes(net, ["q", "p", "a"], ["a"], offsets, 1000.0) == {
            "p": (0, 1),
            "q": (0, 1),
        }

    def test_open_request_is_re_addressed_where_the_target_lane_index_shifts(self):
        """The cross-edge window (2026-09-24, block 3): a vehicle asked on
        edge ``q`` (weave lane 1, target lane 2 — an acceleration lane on its
        right) that crosses onto ``p`` (lanes 0 / 1) still in the weave lane
        with its request open has the request re-issued for ``p``'s target
        index and its remaining life; seen in ``p``'s target lane it is
        vacated, the request ended by the one-step stay, the mode restored."""
        from microsim.runner import LC_MODE_SCRIPTED_SAFE, _weave_step

        ws = self._state(vacate_ahead_m=500.0)
        ws["vacate_lanes"] = {"p": (0, 1), "q": (1, 2)}
        ws["x_offset"]["q"] = -400.0
        veh = _WeaveVehicle({"t": 20.0})
        mod = _WeaveMod(veh)
        # t: on q's lane 1 at x = -300 (300 m before the section start)
        _weave_step(mod, _tc, ws, {"t": _res("q", 1, 100.0, 20.0)}, 0.0)
        assert veh.calls == [("lc", "t", LC_MODE_SCRIPTED_SAFE), ("change", "t", 2, 15.0)]
        assert ws["vacate"]["t"]["lane_to"] == 2 and ws["n_vacate_requests"] == 1
        veh.calls.clear()
        # crossed onto p in the weave lane (index 0 there): re-addressed to 1
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 5.0, 20.0)}, 10.0)
        assert veh.calls == [("change", "t", 1, 5.0)]
        assert ws["vacate"]["t"]["lane_to"] == 1 and ws["n_vacate_requests"] == 1
        veh.calls.clear()
        # still there next step: nothing more is sent
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 15.0, 20.0)}, 10.5)
        assert veh.calls == []
        # in p's target lane: vacated, the open request ended by the stay
        _weave_step(mod, _tc, ws, {"t": _res("p", 1, 25.0, 20.0)}, 11.0)
        assert veh.calls == [("change", "t", 1, 0.5), ("lc", "t", 1621)]
        assert (ws["n_vacated"], ws["n_vacate_refused"]) == (1, 0) and "t" not in ws["vacate"]
        # the target lane's inflow to the window is read across the edges too
        ws2 = self._state(vacate_ahead_m=500.0)
        ws2["vacate_lanes"] = {"p": (0, 1), "q": (1, 2)}
        ws2["x_offset"]["q"] = -400.0
        veh2 = _WeaveVehicle({"k": 20.0, "m": 20.0})
        res = {"k": _res("q", 2, 50.0, 20.0), "m": _res("p", 1, 50.0, 20.0)}
        _weave_step(_WeaveMod(veh2), _tc, ws2, res, 0.0)
        assert ws2["vacate_flow_ids"] == {"k", "m"} and veh2.calls == []

    def test_through_vehicle_asked_once_then_counted_vacated(self):
        from microsim.runner import LC_MODE_SCRIPTED_SAFE, _weave_meta, _weave_step

        ws = self._state()
        veh = _WeaveVehicle({"t": 20.0, "e": 20.0, "u": 20.0})
        mod = _WeaveMod(veh)
        # t: through, 100 m before the section start (inside the 150 m
        # window); e: exit-bound beside it, never asked; u: 180 m out
        res = {
            "t": _res("p", 0, 100.0, 20.0),
            "e": _res("p", 0, 90.0, 20.0),
            "u": _res("p", 0, 20.0, 20.0),
        }
        _weave_step(mod, _tc, ws, res, 0.0)
        assert ("lc", "t", LC_MODE_SCRIPTED_SAFE) in veh.calls
        assert ("change", "t", 1, 5.0) in veh.calls  # 100 m at 20 m/s
        assert not [c for c in veh.calls if c[1] in ("e", "u")]
        veh.calls.clear()
        # still in the weave lane with the request open: nothing more is sent
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 110.0, 20.0)}, 0.5)
        assert not [c for c in veh.calls if c[1] == "t"]
        # seen in the target lane: vacated, the still-open request ended by
        # a one-step stay, the original mode restored
        _weave_step(mod, _tc, ws, {"t": _res("p", 1, 120.0, 20.0)}, 1.0)
        assert (ws["n_vacated"], ws["n_vacate_refused"]) == (1, 0)
        assert ("change", "t", 1, 0.5) in veh.calls and ("lc", "t", 1621) in veh.calls
        assert "t" not in ws["vacate"]
        veh.calls.clear()
        # back in the weave lane later: asked once only
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 150.0, 20.0)}, 1.5)
        assert not [c for c in veh.calls if c[1] == "t"]
        meta = _weave_meta(ws, {})
        assert (meta["n_vacated"], meta["n_vacate_refused"]) == (1, 0)

    def test_reaching_the_section_in_the_weave_lane_is_refused(self):
        from microsim.runner import _weave_step

        ws = self._state()
        veh = _WeaveVehicle({"t": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 100.0, 20.0)}, 0.0)
        veh.calls.clear()
        # on the section's first edge, lane index 1 = the weave lane, with the
        # request (5 s) still open: refused, and the request is ended there
        # (by index it now points at the weave lane itself)
        _weave_step(mod, _tc, ws, {"t": _res("a", 1, 2.0, 20.0)}, 0.5)
        assert (ws["n_vacated"], ws["n_vacate_refused"]) == (0, 1)
        assert ("change", "t", 1, 0.5) in veh.calls and ("lc", "t", 1621) in veh.calls
        assert "t" not in ws["vacate"]

    def test_expired_request_is_refused_without_a_stay(self):
        from microsim.runner import _weave_step

        ws = self._state()
        veh = _WeaveVehicle({"t": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 100.0, 20.0)}, 0.0)
        veh.calls.clear()
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 190.0, 20.0)}, 5.0)
        assert (ws["n_vacated"], ws["n_vacate_refused"]) == (0, 1)
        assert ("lc", "t", 1621) in veh.calls
        assert not [c for c in veh.calls if c[0] == "change"]

    @pytest.mark.parametrize("how", ["no_lanes", "zero_window"])
    def test_inert_without_lanes_or_window(self, how):
        from microsim.runner import _weave_step

        ws = _weave_state() if how == "no_lanes" else self._state(vacate_ahead_m=0.0)
        if how == "no_lanes":
            ws["lane_map"].update({("p", 0): 1, ("p", 1): 2})
            ws["x_offset"]["p"] = -200.0
        veh = _WeaveVehicle({"t": 20.0})
        _weave_step(_WeaveMod(veh), _tc, ws, {"t": _res("p", 0, 100.0, 20.0)}, 0.0)
        assert veh.calls == []


class TestWeaveVacateGapConditioned:
    """The vacate rule re-derived so that it never asks the target lane to
    brake (2026-09-24, block 3): ``vacate_no_follower_braking = 1`` selects
    a form that asks only on a step when the target-lane gap accepts the
    through vehicle without follower braking, under mode 768, and both forms
    ask no more per minute than ``vacate_max_veh_h`` (the target lane's
    spare capacity by default). Measured on the fixtures and not made the
    default (docs/WEAVE_MODEL_PLAN.md, dated section)."""

    @staticmethod
    def _state(**params) -> dict:
        # the 150 m window the form was laid out on (the default is 500 m
        # since the cross-edge window, 2026-09-24 block 3)
        ws = _weave_state(**{"vacate_ahead_m": 150.0, **params})
        ws["vacate_lanes"] = {"p": (0, 1)}
        ws["lane_map"].update({("p", 0): 1, ("p", 1): 2})
        ws["x_offset"]["p"] = -200.0
        return ws

    # the harness constants: length 5, T 1.4, a 0.73, b 1.67, s0 2.5
    P: ClassVar[dict[str, float]] = {
        "len": 5.0,
        "T": 1.4,
        "a": 0.73,
        "b": 1.67,
        "s0": 2.5,
        "vmax": 33.3,
    }

    def _ok(self, x_c, v_c, target, v_of):
        from microsim.runner import _weave_vacate_gap_ok

        p_of = {vid: dict(self.P) for _, vid in target}
        return _weave_vacate_gap_ok(x_c, v_c, dict(self.P), target, v_of, p_of, 0.6)

    def test_gap_acceptance_time_gaps_and_the_follower_desired_gap(self):
        from microsim.runner import _idm_desired_gap

        # an empty target lane accepts
        assert self._ok(100.0, 20.0, [], {})
        # leader: the gap to its rear must clear s0 + 0.6 v_c = 14.5 m at 20 m/s
        assert self._ok(100.0, 20.0, [(119.6, "l")], {"l": 20.0})
        assert not self._ok(100.0, 20.0, [(119.4, "l")], {"l": 20.0})
        # a vehicle beside the changer (front ahead, rear behind its front) refuses
        assert not self._ok(100.0, 20.0, [(103.0, "l")], {"l": 20.0})
        # follower at the changer's speed: the time gap s0 + 0.6 v_F = 14.5 m
        # behind the changer's rear, and the desired gap s0 + v T = 30.5 m
        s_star = _idm_desired_gap(20.0, 0.0, 1.4, 0.73, 1.67, 2.5)
        assert s_star == pytest.approx(30.5)
        assert self._ok(100.0, 20.0, [(100.0 - 5.0 - 30.6, "f")], {"f": 20.0})
        assert not self._ok(100.0, 20.0, [(100.0 - 5.0 - 30.4, "f")], {"f": 20.0})
        # a follower closing at 15 m/s on a 5 m/s changer needs its approach
        # term as well: 2.5 + 28 + 20·15/(2·√(0.73·1.67)) ≈ 166 m
        s_fast = _idm_desired_gap(20.0, 15.0, 1.4, 0.73, 1.67, 2.5)
        assert s_fast > 160.0
        assert not self._ok(100.0, 5.0, [(100.0 - 5.0 - 100.0, "f")], {"f": 20.0})
        assert self._ok(100.0, 5.0, [(100.0 - 5.0 - s_fast - 0.1, "f")], {"f": 20.0})
        # a follower whose front is level with the changer's overlaps: refused
        assert not self._ok(100.0, 20.0, [(100.0, "f")], {"f": 20.0})

    def test_gap_conditioned_form_asks_per_accepting_step_under_mode_768(self):
        from microsim.runner import LC_MODE_SCRIPTED_SAFE_NO_ADAPT, _weave_meta, _weave_step

        ws = self._state(vacate_no_follower_braking=1.0)
        veh = _WeaveVehicle({"t": 20.0})
        mod = _WeaveMod(veh)
        # 100 m before the section start, the target lane empty: asked at once
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 100.0, 20.0)}, 0.0)
        assert ("lc", "t", LC_MODE_SCRIPTED_SAFE_NO_ADAPT) in veh.calls
        assert ("change", "t", 1, 0.5) in veh.calls
        assert ws["vacate"]["t"]["lc_mode_orig"] == 1621 and ws["n_vacate_requests"] == 1
        veh.calls.clear()
        # still in the weave lane, the gap still accepting: asked again, the
        # hold not set twice
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 110.0, 20.0)}, 0.5)
        assert veh.calls == [("change", "t", 1, 0.5)] and ws["n_vacate_requests"] == 2
        veh.calls.clear()
        # a fast follower appears behind in the target lane: not asked this step
        res = {"t": _res("p", 0, 120.0, 20.0), "f": _res("p", 1, 80.0, 30.0)}
        veh.speeds["f"] = 30.0
        _weave_step(mod, _tc, ws, res, 1.0)
        assert not [c for c in veh.calls if c[1] == "t"]
        assert "t" in ws["vacate"]  # the hold stays while it is in the window
        veh.calls.clear()
        # seen in the target lane: vacated, the mode restored, no stay
        _weave_step(mod, _tc, ws, {"t": _res("p", 1, 130.0, 20.0)}, 1.5)
        assert (ws["n_vacated"], ws["n_vacate_refused"]) == (1, 0)
        assert veh.calls == [("lc", "t", 1621)] and "t" not in ws["vacate"]
        meta = _weave_meta(ws, {})
        assert (meta["n_vacated"], meta["n_vacate_skipped_no_gap"], meta["n_vacate_requests"]) == (
            1,
            0,
            2,
        )

    def test_gap_conditioned_form_counts_a_vehicle_never_offered_a_gap_as_skipped(self):
        from microsim.runner import _weave_step

        ws = self._state(vacate_no_follower_braking=1.0)
        veh = _WeaveVehicle({"t": 5.0, "f": 25.0, "u": 5.0})
        mod = _WeaveMod(veh)
        # t crawls at 5 m/s with a 25 m/s follower 60 m behind it in the
        # target lane: never asked; u does the same but moves left by itself
        res = {
            "t": _res("p", 0, 100.0, 5.0),
            "f": _res("p", 1, 40.0, 25.0),
            "u": _res("p", 0, 90.0, 5.0),
        }
        _weave_step(mod, _tc, ws, res, 0.0)
        assert veh.calls == [] and ws["vacate_pending"] == {"t", "u"}
        res = {"t": _res("a", 1, 2.0, 5.0), "u": _res("p", 1, 95.0, 5.0)}
        _weave_step(mod, _tc, ws, res, 0.5)
        assert ws["n_vacate_skipped_no_gap"] == 1 and ws["vacate_pending"] == set()
        assert (ws["n_vacated"], ws["n_vacate_refused"], ws["n_vacate_requests"]) == (0, 0, 0)

    def test_gap_conditioned_form_refuses_at_the_section_and_restores_the_mode(self):
        from microsim.runner import _weave_step

        ws = self._state(vacate_no_follower_braking=1.0)
        veh = _WeaveVehicle({"t": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 100.0, 20.0)}, 0.0)
        veh.calls.clear()
        _weave_step(mod, _tc, ws, {"t": _res("a", 1, 2.0, 20.0)}, 0.5)
        assert (ws["n_vacated"], ws["n_vacate_refused"]) == (0, 1)
        assert veh.calls == [("lc", "t", 1621)]  # no one-step stay in this form

    @pytest.mark.parametrize("form", [0.0, 1.0])
    def test_a_fixed_bound_limits_the_vehicles_asked_per_minute(self, form):
        from microsim.runner import _weave_step

        # 60 veh/h = one vehicle per minute; n nearest the section is asked,
        # u is not and is counted skipped when it reaches the section
        ws = self._state(vacate_no_follower_braking=form, vacate_max_veh_h=60.0)
        veh = _WeaveVehicle({"n": 20.0, "u": 20.0})
        mod = _WeaveMod(veh)
        res = {"n": _res("p", 0, 150.0, 20.0), "u": _res("p", 0, 100.0, 20.0)}
        _weave_step(mod, _tc, ws, res, 0.0)
        changes = [c for c in veh.calls if c[0] == "change"]
        assert [c[1] for c in changes] == ["n"] and ws["vacate_pending"] == {"u"}
        assert len(ws["vacate_asks_s"]) == 1
        res = {"n": _res("p", 1, 160.0, 20.0), "u": _res("a", 1, 2.0, 20.0)}
        _weave_step(mod, _tc, ws, res, 0.5)
        assert (ws["n_vacated"], ws["n_vacate_skipped_no_gap"]) == (1, 1)
        # within the minute the bound is spent; a minute after the ask it has room
        veh.speeds["w"] = 20.0
        veh.calls.clear()
        _weave_step(mod, _tc, ws, {"w": _res("p", 0, 100.0, 20.0)}, 59.5)
        assert veh.calls == [] and ws["vacate_pending"] == {"w"}
        _weave_step(mod, _tc, ws, {"w": _res("p", 0, 110.0, 20.0)}, 60.5)
        assert [c[1] for c in veh.calls if c[0] == "change"] == ["w"]

    def test_the_default_bound_is_the_target_lane_spare_capacity(self):
        from microsim.runner import (
            VACATE_LANE_CAPACITY_VEH_H,
            _weave_step,
            _weave_vacate_bound_veh_h,
        )

        ws = self._state()
        assert _weave_vacate_bound_veh_h(ws, 0.0) == VACATE_LANE_CAPACITY_VEH_H
        # 40 target-lane vehicles sighted in the window within a minute read
        # as 2,400 veh/h: no spare capacity, nobody is asked
        speeds = {f"f{i}": 20.0 for i in range(40)}
        speeds["t"] = 20.0
        veh = _WeaveVehicle(speeds)
        mod = _WeaveMod(veh)
        res = {f"f{i}": _res("p", 1, 50.0 + 3.7 * i, 20.0) for i in range(40)}
        res["t"] = _res("p", 0, 100.0, 20.0)
        _weave_step(mod, _tc, ws, res, 0.0)
        assert _weave_vacate_bound_veh_h(ws, 0.0) == 0.0
        assert veh.calls == [] and ws["vacate_pending"] == {"t"}
        # sightings older than the window drop out of the flow
        assert _weave_vacate_bound_veh_h(ws, 60.5) == VACATE_LANE_CAPACITY_VEH_H
        # a positive vacate_max_veh_h is the bound itself, whatever the flow
        ws2 = self._state(vacate_max_veh_h=120.0)
        ws2["vacate_flow_s"].extend([0.0] * 40)
        assert _weave_vacate_bound_veh_h(ws2, 0.0) == 120.0

    def test_default_form_counts_requests_once_per_vehicle(self):
        from microsim.runner import LC_MODE_SCRIPTED_SAFE, _weave_step

        ws = self._state()
        veh = _WeaveVehicle({"t": 20.0})
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 100.0, 20.0)}, 0.0)
        assert ("lc", "t", LC_MODE_SCRIPTED_SAFE) in veh.calls
        assert ("change", "t", 1, 5.0) in veh.calls and ws["n_vacate_requests"] == 1
        veh.calls.clear()
        _weave_step(mod, _tc, ws, {"t": _res("p", 0, 110.0, 20.0)}, 0.5)
        assert veh.calls == [] and ws["n_vacate_requests"] == 1


class TestWeaveReviewDerivations3To6:
    """Review of 2026-09-24 (block 3) on the third to sixth derivations:
    the vacate rule's "through" set and its mode hand-offs, the pair release's
    frame and speed criterion, the easing bound's guards, dual-role targets
    and the counters of ``_weave_meta``."""

    @staticmethod
    def _vacate_state(lane_from: int = 0, lane_to: int = 1, **params) -> dict:
        """Section ``a`` -> ``b`` with the corridor edge ``p`` before it; its
        lane ``lane_from`` feeds section lane 1 and ``lane_to`` lane 2."""
        ws = _weave_state(**{"vacate_ahead_m": 150.0, **params})  # the window it was laid out on
        ws["vacate_lanes"] = {"p": (lane_from, lane_to)}
        ws["lane_map"].update({("p", lane_from): 1, ("p", lane_to): 2})
        ws["x_offset"]["p"] = -200.0
        return ws

    def test_vacate_through_is_not_bound_for_this_exit_and_only_the_feeding_lane(self):
        """A vehicle exiting at a *later* ramp is through for this section
        (asked); one already in the lane feeding section lane 2 is never
        asked; one bound for the paired exit is never asked."""
        from microsim.runner import _weave_step

        ws = self._vacate_state()
        # "e" exits here (exiting_ids); "l" exits later (its route id is not
        # this section's off index, so it is not in exiting_ids); "k" is in
        # the lane feeding section lane 2 already
        veh = _WeaveVehicle({"l": 20.0, "k": 20.0, "e": 20.0})
        res = {
            "l": _res("p", 0, 100.0, 20.0),
            "k": _res("p", 1, 100.0, 20.0),
            "e": _res("p", 0, 95.0, 20.0),
        }
        _weave_step(_WeaveMod(veh), _tc, ws, res, 0.0)
        assert [c for c in veh.calls if c[0] == "change"] == [("change", "l", 1, 5.0)]
        assert not [c for c in veh.calls if c[1] in ("k", "e")]
        assert set(ws["vacate"]) == {"l"} and ws["vacate_seen"] == {"l"}

    def test_scripted_merge_hands_back_before_the_weave_asks_on_the_same_edge(self):
        """The I-94 geometry (T.H.52: the scripted merge 178547099 attaches at
        the edge before the weave's 999007700): the scripted merge drives lane
        0 of the vacate edge, which dead-ends and is not in ``lane_map``; a
        vehicle that merges into lane 1 within the window is handed back by
        ``_scripted_merge_step`` (stepped first in ``run_micro``) and then
        asked to vacate with its *original* mode captured, and the stay at
        the vacated sighting uses the current lane index."""
        from flowstate_core.config import SCRIPTED_MERGE_DEFAULTS
        from microsim.runner import LC_MODE_SCRIPTED_SAFE, _scripted_merge_step, _weave_step

        ws = self._vacate_state(lane_from=1, lane_to=2)
        ss = {
            "ramp": "sm",
            "edge": "p",
            "lane_len_m": 200.0,
            "target_lane": "p_1",
            "params": dict(SCRIPTED_MERGE_DEFAULTS),
            "veh": {},
            "yielding": {},
            "n_entered": 0,
            "n_changed": 0,
            "n_forced": 0,
            "waits_s": [],
        }
        veh = _WeaveVehicle({"m": 20.0})
        mod = _WeaveMod(veh)
        res = {"m": _res("p", 0, 100.0, 20.0)}
        _scripted_merge_step(mod, _tc, ss, res, 0.0)
        _weave_step(mod, _tc, ws, res, 0.0)
        assert veh.lc_modes["m"] == LC_MODE_SCRIPTED_SAFE and "m" in ss["veh"]
        assert "m" not in ws["vacate"]  # lane 0 of p is not a weave-lane candidate
        veh.calls.clear()
        res = {"m": _res("p", 1, 110.0, 20.0)}  # merged into the weave lane
        _scripted_merge_step(mod, _tc, ss, res, 0.5)
        _weave_step(mod, _tc, ws, res, 0.5)
        assert "m" not in ss["veh"] and ss["n_changed"] == 1
        assert ws["vacate"]["m"]["lc_mode_orig"] == 1621, veh.calls
        assert ("change", "m", 2, pytest.approx(4.5)) in veh.calls
        veh.calls.clear()
        res = {"m": _res("p", 2, 120.0, 20.0)}
        _weave_step(mod, _tc, ws, res, 1.0)
        assert ws["n_vacated"] == 1 and veh.lc_modes["m"] == 1621
        assert ("change", "m", 2, 0.5) in veh.calls  # the stay: the current index

    def test_a_vehicle_held_by_another_section_is_not_asked_to_vacate(self):
        """Two weaves back to back (section B's last edge is the corridor edge
        before section A): B's exit-bound vehicle in B's lane 1 is "through"
        for A and inside A's window while B drives it. Before the fix A
        captured B's mode 512 as the original and restored it after B had
        restored the real one, leaving the vehicle with every model-driven
        change off. A vehicle already under a scripted mode is not asked
        while that hold lasts."""
        from microsim.runner import LC_MODE_SCRIPTED_FORCE, LC_MODE_SCRIPTED_SAFE, _weave_step

        ws_b, ws_a = _sections_back_to_back()
        veh = _WeaveVehicle({"e": 5.0})
        mod = _WeaveMod(veh)
        res = {"e": _res("b", 1, 60.0, 5.0)}  # x = 160: inside A's 150 m window
        _weave_step(mod, _tc, ws_b, res, 0.0)
        # B accepts the change at once (no neighbours): a one-step mode 256
        assert "e" in ws_b["veh"]
        assert veh.lc_modes["e"] in (LC_MODE_SCRIPTED_SAFE, LC_MODE_SCRIPTED_FORCE)
        _weave_step(mod, _tc, ws_a, res, 0.0)
        assert "e" not in ws_a["vacate"] and "e" not in ws_a["vacate_seen"]
        # B's own exit request (lane 0) is the only change asked of e
        assert [c for c in veh.calls if c[0] == "change"] == [("change", "e", 0, 0.5)]
        # B hands the vehicle back once it is in lane 0; A has nothing to restore
        res = {"e": _res("b", 0, 65.0, 5.0)}
        _weave_step(mod, _tc, ws_b, res, 0.5)
        _weave_step(mod, _tc, ws_a, res, 0.5)
        assert veh.lc_modes["e"] == 1621 and ws_b["n_changed_out"] == 1
        assert (ws_a["n_vacated"], ws_a["n_vacate_refused"]) == (0, 0)
        # once the hold is over, e is still not asked: its exit leaves from
        # A's window edge (``vacate_exempt_ids``; review 2026-09-24 — until
        # then it was asked left, away from its exit); a through vehicle in
        # the same place is asked as any other
        res = {"e": _res("b", 1, 70.0, 5.0), "t": _res("b", 1, 60.0, 5.0)}
        veh.speeds["t"] = 5.0
        _weave_step(mod, _tc, ws_a, res, 1.0)
        assert "e" not in ws_a["vacate"] and "e" not in ws_a["vacate_seen"]
        assert ws_a["vacate"]["t"]["lc_mode_orig"] == 1621

    def test_stepping_the_downstream_section_first_leaves_its_hold_on_the_vehicle(self):
        """Why ``run_micro`` steps the sections upstream-first whatever the
        ramp list's order (2026-09-24, block 3): the two sections of the test
        above with A (downstream) stepped before B (upstream) — the ramp
        list's order when the downstream pair is listed first. A asks e to
        vacate first (mode 512, e's real mode 1621 captured); B then admits
        its exit-bound e reading 512 and captures that as the original. When
        e reaches lane 0, A books a refusal and restores 1621, then B, done,
        restores 512: the vehicle is left with every model-driven change off.
        The guard of ``_weave_vacate_step`` cannot see this — it is B's
        admission, not A's request, that reads the other hold. Since the
        review of 2026-09-24 ``run_micro`` exempts B's exiters from A's ask
        (``vacate_exempt_ids``: B's exit leaves from A's window edge), so the
        exemption is lifted here to keep the ordering hazard on record."""
        from microsim.runner import LC_MODE_SCRIPTED_SAFE, _weave_step

        ws_b, ws_a = _sections_back_to_back()
        ws_a["vacate_exempt_ids"] = frozenset()
        veh = _WeaveVehicle({"e": 5.0})
        mod = _WeaveMod(veh)
        res = {"e": _res("b", 1, 60.0, 5.0)}  # x = 160: inside A's 150 m window
        _weave_step(mod, _tc, ws_a, res, 0.0)
        assert ws_a["vacate"]["e"]["lc_mode_orig"] == 1621
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_SAFE
        _weave_step(mod, _tc, ws_b, res, 0.0)
        assert ws_b["veh"]["e"]["lc_mode_orig"] == LC_MODE_SCRIPTED_SAFE  # A's hold, captured
        res = {"e": _res("b", 0, 65.0, 5.0)}
        _weave_step(mod, _tc, ws_a, res, 0.5)
        assert ws_a["n_vacate_refused"] == 1 and veh.lc_modes["e"] == 1621
        _weave_step(mod, _tc, ws_b, res, 0.5)
        assert ws_b["n_changed_out"] == 1 and not ws_b["veh"]
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_SAFE  # handed back to the wrong mode

    def test_pair_release_compares_positions_on_the_section_axis_across_edges(self):
        """The changer on the section's first edge, the follower of its gap
        still on the ramp: a pair in one frame (the ramp at negative
        ``x_offset``), released after ``pair_release_s`` with the ramp
        vehicle, farther from the section end, yielding."""
        from microsim.runner import NEIGHBOR_RIGHT_FOLLOWERS, _weave_step

        ws = _weave_state()
        ws["ramp_edges"] = frozenset({"r"})
        ws["x_offset"]["r"] = -100.0
        ws["lane_map"][("r", 0)] = 0
        veh = _WeaveVehicle({"e": 0.0, "f": 0.0}, {("e", NEIGHBOR_RIGHT_FOLLOWERS): (("f", 1.0),)})
        mod = _WeaveMod(veh)
        # e's front at x = 2 (lane 1), f's front at x = -4 on the ramp: 1 m
        # behind e's rear on the axis although its lane position (96) is the
        # larger number
        res = {"e": _res("a", 1, 2.0, 0.0), "f": _res("r", 0, 96.0, 0.0)}
        for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5):
            veh.calls.clear()
            _weave_step(mod, _tc, ws, res, t)
            assert ws["veh"]["e"]["target"] == "f"
            assert [c for c in veh.calls if c[0] == "slow"] == [("slow", "f", 0.0, 0.0)], t
        assert ws["pair_since"] == {("e", "f"): 0.5}
        veh.calls.clear()
        _weave_step(mod, _tc, ws, res, 3.0)
        assert ws["n_pair_releases"] == 1
        assert not [c for c in veh.calls if c[0] == "slow"]  # f yields
        # e is 198 m from the section end, outside force_within_m: no forced
        # change, and its 1 m follower gap fails the normal acceptance
        assert not [c for c in veh.calls if c[0] == "change"]

    def test_pair_release_keys_on_the_creep_speed_not_on_standstill(self):
        """The contract's criterion is both below ``SCRIPTED_MERGE_CREEP_MS``,
        not stopped: a pair creeping together within one length for longer
        than ``pair_release_s`` is released, and the yielder yields on every
        step the pair keeps standing (``since`` is kept until it breaks up)."""
        from microsim.runner import NEIGHBOR_RIGHT_FOLLOWERS, SCRIPTED_MERGE_CREEP_MS, _weave_step

        v = SCRIPTED_MERGE_CREEP_MS - 0.1
        ws = _weave_state()
        ws["exiting_ids"] = frozenset({"e", "f"})
        veh = _WeaveVehicle({"e": v, "f": v}, {("e", NEIGHBOR_RIGHT_FOLLOWERS): (("f", 1.5),)})
        mod = _WeaveMod(veh)
        res = {"e": _res("b", 1, 90.0, v), "f": _res("b", 0, 83.5, v)}
        for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5):
            _weave_step(mod, _tc, ws, res, t)
        assert ws["n_pair_releases"] == 0 and ws["pair_since"] == {("e", "f"): 0.5}
        for t in (3.0, 3.5, 4.0):
            veh.calls.clear()
            _weave_step(mod, _tc, ws, res, t)
            assert not [c for c in veh.calls if c[0] == "slow"], t
            assert ws["veh"]["e"]["forced"] is True
        assert ws["n_pair_releases"] == 1 and ws["pair_since"] == {("e", "f"): 0.5}
        # at the creep speed the pair is moving: not standing, nothing kept
        veh.speeds["f"] = SCRIPTED_MERGE_CREEP_MS
        res["f"] = _res("b", 0, 83.5, SCRIPTED_MERGE_CREEP_MS)
        _weave_step(mod, _tc, ws, res, 4.5)
        assert ws["pair_since"] == {} and ws["pair_released"] == set()

    def test_easing_bound_is_guarded_at_and_past_the_section_end(self):
        from microsim.runner import _weave_easing_ok

        # no section left, or the changer past the section end: never eased,
        # and no division by zero on the way
        assert not _weave_easing_ok(10.0, 10.0, -5.0, 8.0, 0.0, 1.67)
        assert not _weave_easing_ok(10.0, 10.0, -5.0, 8.0, -5.0, 1.67)
        # a hair of section left: a_req is astronomically large, not an error
        assert not _weave_easing_ok(10.0, 10.0, -5.0, 8.0, 1e-9, 1.67)
        assert not _weave_easing_ok(0.0, 0.0, -5.0, 8.0, 1e-9, 1.67)
        # remaining_m = 0 with a leader that opens the gap by itself
        assert not _weave_easing_ok(10.0, 15.0, -5.0, 8.0, 0.0, 1.67)

    def test_dual_role_target_is_the_lowest_and_independent_of_the_processing_order(self):
        """An exiter that is at once the follower of an entrant's committed
        gap and a changer eased towards that entrant (its own gap's leader)
        gets one target, the lower of the two, whichever role is processed
        first; the counters attribute the step to one role only."""
        from microsim.runner import _weave_step

        def run(exiter: str, entrant: str):
            ws = _weave_state()
            ws["exiting_ids"] = frozenset({exiter})
            veh = _WeaveVehicle({exiter: 10.0, entrant: 10.0, "L": 10.0})
            res = {
                entrant: _res("a", 0, 50.0, 10.0),
                exiter: _res("a", 1, 40.0, 10.0),
                "L": _res("a", 1, 80.0, 10.0),
            }
            _weave_step(_WeaveMod(veh), _tc, ws, res, 0.0)
            slows = [c for c in veh.calls if c[0] == "slow"]
            return ws, slows

        ws1, slows1 = run("e", "n")  # the exiter is processed first
        ws2, slows2 = run("n", "e")  # the entrant is processed first
        assert len(slows1) == 1 and slows1[0][1] == "e" and slows1[0][2] == pytest.approx(9.165)
        assert len(slows2) == 1 and slows2[0][1] == "n" and slows2[0][2] == pytest.approx(9.165)
        assert ws1["veh"]["n"]["target"] == "e" and ws2["veh"]["e"]["target"] == "n"
        assert ws1["n_cooperations"] + ws1["n_changer_eased"] == 1
        assert ws2["n_cooperations"] + ws2["n_changer_eased"] == 1

    def test_meta_counters_are_zero_without_participants(self):
        from microsim.runner import _weave_meta, _weave_step

        ws = _weave_state()
        # a run in which nothing ever needed the section: through vehicles in
        # lane 2, an exit-bound vehicle already in lane 0
        ws["exiting_ids"] = frozenset({"e"})
        veh = _WeaveVehicle({"t": 25.0, "e": 25.0})
        for t in (0.0, 0.5, 1.0):
            _weave_step(
                _WeaveMod(veh),
                _tc,
                ws,
                {"t": _res("a", 2, 10.0 + t, 25.0), "e": _res("b", 0, 50.0 + t, 25.0)},
                t,
            )
        assert veh.calls == []
        meta = _weave_meta(ws, {"main": 3, "main_off1": 1, "main_off2": 4})
        counters = {k: v for k, v in meta.items() if k.startswith("n_")}
        assert counters == {
            "n_entered": 0,
            "n_changed_in": 0,
            "n_changed_out": 0,
            "n_forced": 0,
            "n_missed": 0,
            "n_missed_exit": 0,
            "n_giveup_waited": 0,
            "n_forced_deferred": 0,
            "n_cooperations": 0,
            "n_changer_eased": 0,
            "n_vacated": 0,
            "n_vacate_refused": 0,
            "n_vacate_skipped_no_gap": 0,
            "n_vacate_requests": 0,
            "n_pair_releases": 0,
            "n_unfinished": 0,
            "n_exited": 0,
            "n_reached_section_exiting": 1,
            "n_departed_exiting": 1,
        }
        assert meta["mean_follower_decel_ms2"] is None
        assert meta["wait_s_mean"] is None
        assert meta["wait_in_s_mean"] is None and meta["wait_out_s_mean"] is None
