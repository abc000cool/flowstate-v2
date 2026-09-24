"""On-ramp merge models, managed (HOV) lanes and ramp metering (docs/CONTRACTS.md §2)."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

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
            "vacate_ahead_m": 150.0,
            "pair_release_s": 2.0,
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


def _th52_config(seed: int) -> ScenarioConfig:
    """The T.H.52 weave at capacity on ``tests/fixtures/weave_th52.osm``
    (``TestWeaveRun.test_th52_weave_at_capacity_flows``): mainline 4,500 veh/h
    with 25 % exiting, entrance 1,400 veh/h, 20 simulated minutes, step 0.5 s,
    the fleet defaults."""
    return ScenarioConfig.model_validate(
        {
            "name": "weave_th52",
            "network": {
                "kind": "osm",
                "osm_file": str(Path(__file__).parents[1] / "fixtures" / "weave_th52.osm"),
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
                        "weave": {"exit_ramp": "cd exit"},
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
            "sim": {"duration_s": 1200.0},
            "seed": seed,
        }
    )


def _th52_lane1_windows(paths, meta: dict) -> tuple[pd.Series, dict]:
    """Mean speed of section lane 1 over its first 60 m per 60-s window after
    a 120-s warm-up, and the state dict the assertions report."""
    net = sumolib.net.readNet(str(next(paths.run_dir.glob("**/*.net.xml"))))
    x0 = sum(net.getEdge(e).getLength() for e in ("100", "101"))
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
        reason="T.H.52 weave at capacity (docs/WEAVE_MODEL_PLAN.md, 2026-09-24 block 3, fifth "
        "derivation): lane 1 at the section start flows at 6-12 m/s at seed 3, nothing locks "
        "at seeds 3-5, but the entrance departs 395 of 466 against 419 required (the ramp "
        "still queues at 4-5 m/s over its first 100 m)",
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

    @pytest.mark.parametrize("seed", [4, 5])
    def test_th52_weave_at_capacity_does_not_lock(self, tmp_path, seed):
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
        collision at either."""
        paths = run_micro(_th52_config(seed), seed, tmp_path / f"th52_{seed}")
        meta = json.loads(paths.meta.read_text())
        (ws,) = meta["weave_sections"]
        windows, state = _th52_lane1_windows(paths, meta)
        (on_meta, _off_meta) = meta["ramps"]
        assert meta["n_collisions"] == 0, meta["collisions"]
        assert len(windows) == 18 and (windows > 2.0).all(), state
        assert ws["n_missed"] == 0 and ws["n_unfinished"] <= 0.1 * ws["n_entered"], state
        assert on_meta["n_departed"] >= 0.8 * on_meta["n_planned"], state
        assert ws["n_pair_releases"] >= 1, state

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
        "exited": set(),
        "reached": set(),
        "awaiting_exit": set(),
        "veh": {},
        # 2026-09-24 (block 3, second attempt): gap listings on the section
        # axis and the cooperation counters
        "lane_map": {(e, k): k for e in edges for k in range(3)},
        "x_offset": {"a": 0.0, "b": 100.0},
        # third derivation: no corridor edge before the section here
        "vacate_lanes": None,
        "vacate": {},
        "vacate_seen": set(),
        "ramp_edges": frozenset(),
        "pre": {},
        "veh_params": {},
        "lane_vmax": {},
        "n_entered": 0,
        "n_changed_in": 0,
        "n_changed_out": 0,
        "n_forced": 0,
        "n_missed": 0,
        "n_forced_deferred": 0,
        "n_cooperations": 0,
        "coop_decel_sum": 0.0,
        "n_changer_eased": 0,
        "n_vacated": 0,
        "n_vacate_refused": 0,
        # fifth derivation: stopped crossing pairs
        "pair_since": {},
        "pair_released": set(),
        "n_pair_releases": 0,
        "step_s": 0.5,
        "waits_in_s": [],
        "waits_out_s": [],
    }


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
        _weave_step(mod, _tc, ws, {"e": _res("b", 1, 95.0, 20.0)}, 0.0)
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


class TestWeavePairRelease:
    """``_weave_pair_release`` (fifth derivation, 2026-09-24 block 3): a changer
    stopped at the section end with the follower of its committed gap standing
    bumper to bumper behind it, both held for ever otherwise (the follower by
    the changer's own cooperation command, the changer by the guard)."""

    @staticmethod
    def _stopped_pair(**params):
        """Exiter ``e`` at 0.5 m before the section end in lane 1, stopped; the
        follower ``f`` of its lane-0 gap 1.5 m behind its rear, stopped — an
        exit-bound vehicle already in lane 0 (the fixture's lock pair), so it
        is not itself driven."""
        from microsim.runner import NEIGHBOR_RIGHT_FOLLOWERS

        ws = _weave_state(**params)
        ws["exiting_ids"] = frozenset({"e", "f"})
        veh = _WeaveVehicle({"e": 0.0, "f": 0.0}, {("e", NEIGHBOR_RIGHT_FOLLOWERS): (("f", 1.5),)})
        res = {"e": _res("b", 1, 99.5, 0.0), "f": _res("b", 0, 93.0, 0.0)}
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
        res["f"] = _res("b", 0, 93.0, 0.0)
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
        res["f"] = _res("b", 0, 93.0, SCRIPTED_MERGE_CREEP_MS)
        for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5):
            _weave_step(mod, _tc, ws, res, t)
        assert ws["pair_since"] == {} and ws["n_pair_releases"] == 0
        # stopped, but more than a vehicle length behind: not a pair
        veh.speeds["f"] = 0.0
        res["f"] = _res("b", 0, 89.0, 0.0)  # gap 5.5 m > 5 m
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
        res = {"e": _res("b", 1, 99.5, 0.0), "n": _res("b", 0, 93.0, 0.0)}
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
        ws = _weave_state(**params)
        # the corridor edge "p" before the section: its lane 0 feeds section
        # lane 1 (the weave lane), its lane 1 feeds section lane 2
        ws["vacate_lanes"] = ("p", 0, 1)
        ws["lane_map"].update({("p", 0): 1, ("p", 1): 2})
        ws["x_offset"]["p"] = -200.0
        return ws

    def test_lanes_from_the_lane_map(self):
        from microsim.runner import _weave_vacate_lanes

        lane_map = {("p", 0): 1, ("p", 1): 2, ("a", 0): 0, ("a", 1): 1, ("a", 2): 2}
        assert _weave_vacate_lanes(("p", "a", "b"), ("a", "b"), lane_map) == ("p", 0, 1)
        # no corridor edge before the section, or no lane feeding section lane 2
        assert _weave_vacate_lanes(("a", "b"), ("a", "b"), lane_map) is None
        assert _weave_vacate_lanes(("p", "a"), ("a",), {("p", 0): 1, ("a", 1): 1}) is None

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
