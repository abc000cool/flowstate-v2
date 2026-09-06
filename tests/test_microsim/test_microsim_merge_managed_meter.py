"""On-ramp merge models, managed (HOV) lanes and ramp metering (docs/CONTRACTS.md §2)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from flowstate_core.config import ScenarioConfig
from microsim import run_micro
from microsim.networks import merge_patch_files

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
        with pytest.raises(ValueError, match="drop exactly one lane"):
            merge_patch_files(tmp_path, "a", "b", "n", 3, 3, "zipper")
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
