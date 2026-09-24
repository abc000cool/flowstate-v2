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
        assert WEAVE_DEFAULTS == {**SCRIPTED_MERGE_DEFAULTS, "exit_accept_gap_s": 0.6}
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
        self.calls: list[tuple] = []

    def getLaneChangeMode(self, vid):
        return self.lc_modes.get(vid, 1621)

    def getMaxSpeed(self, vid):
        return self.max_speeds.get(vid, 33.3)

    def getMinGap(self, vid):
        return 2.5

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
        "yielding": {},
        "n_entered": 0,
        "n_changed_in": 0,
        "n_changed_out": 0,
        "n_forced": 0,
        "n_missed": 0,
        "n_forced_deferred": 0,
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
        from microsim.runner import LC_MODE_SCRIPTED_SAFE, _weave_step

        ws = _weave_state()
        veh = _WeaveVehicle({"e": 20.0})
        veh.lc_modes["e"] = 1621
        veh.max_speeds["e"] = 31.0
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"e": _res("a", 1, 10.0, 20.0)}, 0.0)
        assert veh.lc_modes["e"] == LC_MODE_SCRIPTED_SAFE
        # 190 m from the gore, outside force_within_m: drives with its own lane
        # (2026-09-24), not the target lane's limit
        assert veh.max_speeds["e"] == pytest.approx(31.0)
        assert ("change", "e", 0, WEAVE_DEFAULTS["change_duration_s"]) in veh.calls
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

    def test_courtesy_yield_of_a_through_vehicle_is_restored_next_step(self):
        """Through traffic on lane 1 (not exit-bound, so never driven) that
        blocks an entering vehicle's gap is slowed by ``courtesy`` for one step
        through its desired speed only; its lane-change mode is never touched
        and its speed comes back the next step."""
        from microsim.runner import NEIGHBOR_LEFT_FOLLOWERS, _weave_step

        ws = _weave_state(courtesy=2.0)
        veh = _WeaveVehicle({"n": 15.0, "f": 15.0}, {("n", NEIGHBOR_LEFT_FOLLOWERS): (("f", 4.0),)})
        veh.max_speeds["f"] = 29.0
        mod = _WeaveMod(veh)
        res = {"n": _res("a", 0, 50.0, 15.0), "f": _res("a", 1, 40.0, 15.0)}
        _weave_step(mod, _tc, ws, res, 0.0)
        assert ws["n_entered"] == 1 and set(ws["veh"]) == {"n"}
        assert veh.max_speeds["f"] == pytest.approx(13.0) and ws["yielding"] == {"f": 29.0}
        assert "f" not in veh.lc_modes
        veh.neighbors = {}
        _weave_step(mod, _tc, ws, res, 0.5)
        assert veh.max_speeds["f"] == 29.0 and ws["yielding"] == {}

    def test_exchange_yields_strictly_the_rear_vehicle(self):
        """An overlapping exiting/entering pair report each other as follower;
        only the rear one drops back (no creep floor), so the pair cannot hold
        each other and the front one changes first."""
        from microsim.runner import (
            NEIGHBOR_LEFT_FOLLOWERS,
            NEIGHBOR_RIGHT_FOLLOWERS,
            WEAVE_EXCHANGE_YIELD_MS,
            _weave_step,
        )

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
        # n was driven first (no left leader: the lane limit); that is what is restored
        assert ws["yielding"] == {"n": pytest.approx(30.0)}
        assert veh.max_speeds["n"] == pytest.approx(3.0 - WEAVE_EXCHANGE_YIELD_MS)
        assert veh.max_speeds["e"] >= 3.0  # the front vehicle is never told to yield
        # neither gap is acceptable, so no change is requested by either
        assert not [c for c in veh.calls if c[0] == "change"]


class TestWeaveLockRules:
    """The two rule changes of 2026-09-24 (docs/ONBOARDING_MNDOT.md §10)."""

    def test_exiting_vehicle_outside_the_zone_drives_with_its_lane(self):
        """An exiting vehicle 150 m from the gore behind a stopped auxiliary-lane
        vehicle keeps its own desired speed; inside ``force_within_m`` it holds
        station behind that vehicle as before."""
        from microsim.runner import NEIGHBOR_RIGHT_LEADERS, _weave_step

        ws = _weave_state()
        veh = _WeaveVehicle({"e": 20.0, "q": 0.0}, {("e", NEIGHBOR_RIGHT_LEADERS): (("q", 30.0),)})
        veh.max_speeds["e"] = 31.0
        mod = _WeaveMod(veh)
        _weave_step(mod, _tc, ws, {"e": _res("a", 1, 50.0, 20.0), "q": _res("a", 0, 85.0, 0.0)}, 0)
        assert veh.max_speeds["e"] == 31.0
        # 60 m from the gore (force_within_m 80): station-keeping applies
        _weave_step(mod, _tc, ws, {"e": _res("b", 1, 40.0, 20.0), "q": _res("b", 0, 75.0, 0.0)}, 1)
        assert veh.max_speeds["e"] < 31.0

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
