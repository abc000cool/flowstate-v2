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

    def test_downstream_boundary_congests_measured_span(self, tmp_path):
        """Measured-boundary support (docs/CONTRACTS.md §2 BoundarySpec, M3).

        A slow speed schedule on the exit-buffer edge — outside the corridor
        proper — must spill congestion back into the downstream end of the
        corridor, and the run must record the boundary in meta.json. The
        boundary is a calibration input, not a perturbation: seeded stays
        False.
        """
        base = {
            "name": "corridor_boundary_smoke",
            "network": {
                "kind": "corridor",
                "length_m": 1000.0,
                "lanes": 1,
                "inflow": [[0.0, 0.35]],
            },
            "sim": {"duration_s": 150.0},
        }
        cfg_free = ScenarioConfig.model_validate(base)
        with_boundary = json.loads(json.dumps(base))
        with_boundary["network"]["boundary"] = {
            "steps": [[0.0, 2.0]],
            "exit_buffer_m": 150.0,
        }
        cfg_bound = ScenarioConfig.model_validate(with_boundary)

        p_free = run_micro(cfg_free, 42, tmp_path / "free")
        p_bound = run_micro(cfg_bound, 42, tmp_path / "bound")

        meta = json.loads(p_bound.meta.read_text())
        assert meta["seeded"] is False
        assert meta["boundary"]["kind"] == "speed_schedule"
        assert meta["boundary"]["exit_edge"] == "exit"
        assert meta["boundary"]["n_steps_applied"] == 1
        assert json.loads(p_free.meta.read_text())["boundary"] is None

        df_free = pd.read_parquet(p_free.trajectories)
        df_bound = pd.read_parquet(p_bound.trajectories)

        # Entry buffer = min(2000, length) = 1000 m ⇒ corridor proper is
        # x ∈ [1000, 2000); compare the last 300 m of it, post-ramp-up.
        def tail_speed(df):
            sel = df[(df.x >= 1700.0) & (df.x < 2000.0) & (df.t >= 60.0)]
            return sel.v.mean()

        v_free, v_bound = tail_speed(df_free), tail_speed(df_bound)
        assert v_bound < v_free - 2.0, (v_free, v_bound)
        # And vehicles do exist on the exit edge, at/below its limit + noise.
        on_exit = df_bound[df_bound.x >= 2000.0]
        assert len(on_exit) > 0
        assert on_exit.v.quantile(0.9) <= 2.5

    def test_five_lane_calibrated_fleet_smoke(self, tmp_path):
        """us101_replica shape: 5 lanes + IDMCalibration-driven fleet (M2).

        Verifies the lanes<=8 config bound, multi-lane vtype/route generation,
        that ``fleet.idm_calibration`` is actually consumed (drawn params come
        from the artifact population, not the scalar fields), and that
        meta.json records the artifact provenance (docs/CONTRACTS.md §2).
        """
        from flowstate_core.artifacts import IDMCalibration

        art = tmp_path / "idm_cal.json"
        IDMCalibration(
            created_at="2026-08-29T00:00:00Z",
            source="smoke synthetic population",
            data_hash="smoke-hash",
            mean={"v0": 15.0, "T": 1.3, "a_max": 0.9, "b": 1.6, "s0": 2.1},
            cov=[[0.0] * 5 for _ in range(5)],  # deterministic draws = the mean
            n_episodes_fit=10,
            n_episodes_holdout=4,
            holdout_gap_rmse_m=1.0,
        ).save(art)
        cfg = ScenarioConfig.model_validate(
            {
                "name": "us101_shape_smoke",
                "network": {
                    "kind": "corridor",
                    "length_m": 640.0,
                    "lanes": 5,
                    "inflow": [[0.0, 2.4]],
                },
                "fleet": {"model": "IDM", "idm_calibration": str(art)},
                "sim": {"duration_s": 60.0},
            }
        )
        paths = run_micro(cfg, 42, tmp_path)
        meta = json.loads(paths.meta.read_text())
        df = pd.read_parquet(paths.trajectories)
        assert meta["fleet_calibration"] == {
            "path": str(art),
            "data_hash": "smoke-hash",
            "created_at": "2026-08-29T00:00:00Z",
        }
        assert df.veh_id.nunique() >= 100
        # All 5 lanes carry traffic; no lane index beyond the requested count.
        assert set(df.lane.unique()) == {0, 1, 2, 3, 4}
        # Zero-covariance artifact ⇒ every vehicle drives the artifact mean:
        # with v0 = 15 m/s no free-flowing vehicle can exceed it.
        assert df.v.max() <= 15.0 + 1e-6

    def test_us101_demand_realization_90pct(self, tmp_path):
        """M3 fix: the 5-lane US-101 demand level inserts >= 90% of planned.

        M2 documented a ~73% insertion ceiling under departLane="free" +
        departPos="free" + departSpeed="max" (docs/M2_RESULTS.md §6/§7.7);
        the per-lane round-robin / base / avg scheme must clear 90%. Uses the
        us101_replica geometry and its peak 5-min inflow rate over a 600 s
        window (the hardest sustained load) with the default fleet so the
        test needs no calibration artifact.
        """
        cfg = ScenarioConfig.model_validate(
            {
                "name": "us101_demand_smoke",
                "network": {
                    "kind": "corridor",
                    "length_m": 640.0,
                    "lanes": 5,
                    "inflow": [[0.0, 2.437]],
                },
                "sim": {"duration_s": 600.0},
            }
        )
        paths = run_micro(cfg, 42, tmp_path)
        meta = json.loads(paths.meta.read_text())
        planned = meta["n_vehicles_planned"]
        departed = meta["n_vehicles_departed"]
        assert planned >= 1400  # 2.437 veh/s x 600 s
        assert departed / planned >= 0.90

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
