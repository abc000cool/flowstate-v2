"""Runner smoke tests: contract-compliant artifacts from real SUMO runs.

Parquet files are read via ``pd.read_parquet`` / file objects — constructing a
``pyarrow`` ``LocalFileSystem`` from a bare path fails once libsumo's bundled
libarrow is loaded in-process (see ``microsim.runner._write_parquet``).
"""

import json

import pandas as pd
import pyarrow.parquet as pq
import pytest

from flowstate_core.config import ScenarioConfig, config_hash
from microsim import load_scenario, run_micro, run_replicates

pytestmark = pytest.mark.integration

#: Contract dtypes (docs/CONTRACTS.md §3) + the ring-only unwrapped column.
TRAJ_SCHEMA_RING = [
    ("t", "double"),
    ("veh_id", "string"),
    ("x", "double"),
    ("lane", "int32"),
    ("v", "double"),
    ("a", "double"),
    ("is_av", "bool"),
    ("complied", "bool"),
    ("x_unwrapped", "double"),
]

EDGES_COLUMNS = ["t_bin", "x_bin", "mean_speed", "density", "flow"]


def _short_ring_cfg(duration_s: float = 60.0) -> ScenarioConfig:
    cfg = load_scenario("ring_sugiyama").model_copy(deep=True)
    cfg.sim.duration_s = duration_s
    return cfg


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    cfg = _short_ring_cfg()
    paths = run_micro(cfg, 42, tmp_path_factory.mktemp("ring_smoke"))
    return cfg, paths


class TestRingSmoke:
    def test_trajectories_schema_exact(self, run):
        _, paths = run
        with open(paths.trajectories, "rb") as f:
            schema = pq.read_schema(f)
        assert [(fld.name, str(fld.type)) for fld in schema] == TRAJ_SCHEMA_RING

    def test_trajectory_content(self, run):
        cfg, paths = run
        df = pd.read_parquet(paths.trajectories)
        assert df.veh_id.nunique() == 22
        assert df.t.max() == pytest.approx(cfg.sim.duration_s)
        # output_hz = 2 → samples every 0.5 s.
        one = df[df.veh_id == "v00000"].sort_values("t")
        assert one.t.diff().dropna().unique() == pytest.approx([0.5])
        # Ring x wraps into [0, C); unwrapped x is monotone non-decreasing.
        assert (df.x >= 0.0).all() and (df.x <= 230.0).all()
        assert one.x_unwrapped.is_monotonic_increasing
        assert not df.is_av.any() and not df.complied.any()

    def test_edges_parquet_contract(self, run):
        _cfg, paths = run
        e = pd.read_parquet(paths.edges)
        assert list(e.columns) == EDGES_COLUMNS
        assert (e.density >= 0).all() and (e.flow >= 0).all()
        # 22 veh on 230 m ⇒ mean density ≈ 0.0957 veh/m over occupied bins.
        occupied = e[e.mean_speed.notna()]
        assert occupied.density.mean() == pytest.approx(22.0 / 230.0, rel=0.05)

    def test_meta_contract(self, run):
        cfg, paths = run
        meta = json.loads(paths.meta.read_text())
        assert meta["tier"] == "micro"
        assert meta["seeded"] is False
        assert meta["config_hash"] == config_hash(cfg)
        assert meta["seed"] == 42
        assert meta["config"]["name"] == "ring_sugiyama"
        for key in ("versions", "wall_time_s", "fuel_ml_per_vehicle", "av_ids", "complied_ids"):
            assert key in meta
        assert meta["versions"]["eclipse-sumo"].startswith("1.27")
        # Per-vehicle fuel recorded for every departed vehicle, in ml.
        assert len(meta["fuel_ml_per_vehicle"]) == 22
        assert meta["fuel_total_ml"] > 0.0


class TestCorridorSmoke:
    def test_120s_smoke_inserts_300_plus_vehicles(self, tmp_path):
        cfg = ScenarioConfig.model_validate(
            {
                "name": "corridor_smoke",
                "network": {
                    "kind": "corridor",
                    "length_m": 5000.0,
                    "lanes": 4,
                    "inflow": [[0.0, 2.7]],
                },
                "sim": {"duration_s": 120.0},
            }
        )
        paths = run_micro(cfg, 42, tmp_path, depart_edge_spread=0)
        meta = json.loads(paths.meta.read_text())
        df = pd.read_parquet(paths.trajectories)
        assert meta["n_vehicles_departed"] >= 300
        assert df.veh_id.nunique() >= 300
        assert set(df.lane.unique()) <= {0, 1, 2, 3}
        # Corridor linear x: monotone along each vehicle's trajectory.
        one = df[df.veh_id == df.veh_id.iloc[0]].sort_values("t")
        assert one.x.is_monotonic_increasing
        # No x_unwrapped column off-ring (contract columns only).
        assert "x_unwrapped" not in df.columns

    def test_vsl_scenario_runs(self, tmp_path):
        cfg = ScenarioConfig.model_validate(
            {
                "name": "vsl_smoke",
                "network": {
                    "kind": "corridor",
                    "length_m": 3000.0,
                    "lanes": 1,
                    "inflow": [[0.0, 0.3]],
                },
                "av": {"penetration": 0.0, "compliance": 1.0, "vsl": "vsl_threshold"},
                "sim": {"duration_s": 90.0},
            }
        )
        paths = run_micro(cfg, 11, tmp_path)
        meta = json.loads(paths.meta.read_text())
        assert meta["vsl"] == "vsl_threshold"
        assert pd.read_parquet(paths.trajectories).veh_id.nunique() > 0


class TestReplicates:
    def test_spawn_pool_runs_distinct_seeds(self, tmp_path):
        cfg = _short_ring_cfg(30.0).model_copy(update={"replicates": 3})
        paths = run_replicates(cfg, tmp_path, n_procs=3)
        assert len(paths) == 3
        seeds = [json.loads(p.meta.read_text())["seed"] for p in paths]
        assert len(set(seeds)) == 3
        assert all(p.trajectories.exists() and p.edges.exists() for p in paths)
        # All replicates share the config hash directory.
        assert len({p.run_dir.parent for p in paths}) == 1
