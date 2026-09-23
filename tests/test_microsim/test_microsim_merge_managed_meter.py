"""On-ramp merge models, managed (HOV) lanes and ramp metering (docs/CONTRACTS.md §2)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest
import sumolib

from flowstate_core.config import RampSpec, ScenarioConfig, config_hash
from microsim import run_micro
from microsim.networks import (
    AccelLaneEnd,
    accel_lane_end,
    lane_end_patch_file,
    merge_patch_files,
    osm_import,
    patch_net,
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
        assert [Path(p).name for p in meta["net_patch_files"]] == ["accel_end_102.con.xml"]
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
        assert sorted(Path(p).name for p in meta["net_patch_files"]) == [
            "accel_end_102.con.xml",
            "merge_102.con.xml",
            "merge_102.nod.xml",
        ]
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
