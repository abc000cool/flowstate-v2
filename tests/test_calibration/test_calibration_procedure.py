"""Unit tests for the corridor-agnostic FHWA calibration-procedure scripts.

``scripts/calibrate_capacity.py`` (Vol. III step 1, capacity) and
``scripts/fit_demand_scale.py`` (step 2, demand level) generalise the I-24
scripts. ``scripts/`` is not a package, so the modules are loaded by path;
only their pure helpers are exercised here — no SUMO run. Two tests check
that the generalised rules rebuild the committed I-24 derived artifacts from
the recorded tables (the simulations themselves are not rerun).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest
import yaml

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import BoundarySpec, ScenarioConfig, config_hash
from flowstate_core.units import veh_h_to_veh_s, veh_s_to_veh_h

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def _load_script(name: str) -> ModuleType:
    """Import ``scripts/<name>.py`` by path (``--import-mode=importlib`` safe)."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cap = _load_script("calibrate_capacity")
dem = _load_script("fit_demand_scale")


def _skip_unless(*paths: Path) -> None:
    missing = [str(p.relative_to(REPO)) for p in paths if not p.exists()]
    if missing:
        pytest.skip(f"artifact(s) not present: {missing}")


# ---------------------------------------------------------------------------
# Step 1: capacity calibration helpers
# ---------------------------------------------------------------------------


class TestInterpolateScale:
    def test_linear_recovery(self) -> None:
        f, how = cap.interpolate_scale([1.0, 0.9, 0.8], [1000.0, 1100.0, 1200.0], 1150.0)
        assert f == pytest.approx(0.85)
        assert "linear interpolation" in how

    def test_grid_order_does_not_matter(self) -> None:
        a = cap.interpolate_scale([1.0, 0.9, 0.8], [1000.0, 1100.0, 1200.0], 1150.0)
        b = cap.interpolate_scale([0.8, 1.0, 0.9], [1200.0, 1000.0, 1100.0], 1150.0)
        assert a == b

    def test_already_meets_target_keeps_largest_scale(self) -> None:
        f, how = cap.interpolate_scale([1.0, 0.9], [1000.0, 1100.0], 900.0)
        assert f == 1.0
        assert "already meets" in how

    def test_target_beyond_grid_takes_best_point(self) -> None:
        f, how = cap.interpolate_scale([1.0, 0.9, 0.8], [1000.0, 1100.0, 1200.0], 1300.0)
        assert f == 0.8
        assert "not reached" in how

    def test_bad_inputs(self) -> None:
        with pytest.raises(ValueError):
            cap.interpolate_scale([1.0, 0.9], [1000.0], 950.0)
        with pytest.raises(ValueError):
            cap.interpolate_scale([], [], 950.0)

    def test_i24_table_rebuilds_committed_artifact(self) -> None:
        """The generalised rule on the recorded I-24 table gives the committed f* and T."""
        sidecar = REPO / "artifacts" / "idm_i24_capacity.calibration.json"
        derived_path = REPO / "artifacts" / "idm_i24_capacity.json"
        src_path = REPO / "artifacts" / "idm_i24.json"
        fd_path = REPO / "artifacts" / "fd_i24.json"
        _skip_unless(sidecar, derived_path, src_path, fd_path)
        side = json.loads(sidecar.read_text())
        target = veh_s_to_veh_h(json.loads(fd_path.read_text())["fd"]["ci95"]["q_max"][0])
        f, how = cap.interpolate_scale(
            [r["T_scale"] for r in side["table"]],
            [r["capacity_veh_h_lane"] for r in side["table"]],
            target,
        )
        assert round(f, 4) == side["T_scale"]
        assert how == side["interpolation"]
        src = IDMCalibration.load(src_path)
        committed = IDMCalibration.load(derived_path)
        rebuilt = cap.derived_artifact(src, f, "rebuilt")
        assert rebuilt.mean["T"] == pytest.approx(committed.mean["T"], rel=1e-9)
        for key in ("v0", "a_max", "b", "s0"):
            assert rebuilt.mean[key] == committed.mean[key] == src.mean[key]
        assert rebuilt.cov == committed.cov == src.cov


class TestCapacityTable:
    @staticmethod
    def _rows() -> list[dict]:
        return [
            {"T_scale": 1.0, "seed": 1, "throughput_veh_h_lane": 1800.0, "inserted_fraction": 0.8},
            {"T_scale": 1.0, "seed": 2, "throughput_veh_h_lane": 1820.0, "inserted_fraction": 0.8},
            {"T_scale": 0.9, "seed": 1, "throughput_veh_h_lane": 1900.0, "inserted_fraction": 0.99},
            {
                "T_scale": 0.9,
                "seed": 2,
                "throughput_veh_h_lane": 1910.0,
                "inserted_fraction": 0.985,
            },
        ]

    def test_means_and_flags(self) -> None:
        table = cap.capacity_table(self._rows(), (1.0, 0.9), t_mean_s=1.5)
        assert [r["T_scale"] for r in table] == [1.0, 0.9]
        assert table[0]["capacity_veh_h_lane"] == pytest.approx(1810.0)
        assert table[0]["n"] == 2
        assert table[0]["T_mean_s"] == pytest.approx(1.5)
        assert table[0]["demand_limited"] is False
        assert table[1]["T_mean_s"] == pytest.approx(1.35)
        assert table[1]["demand_limited"] is True

    def test_missing_scale_raises(self) -> None:
        with pytest.raises(ValueError):
            cap.capacity_table(self._rows(), (1.0, 0.8), t_mean_s=1.5)


class TestSampleEpisodes:
    def test_seeded_without_replacement(self) -> None:
        episodes = list(range(50))
        a = cap.sample_episodes(episodes, 10, seed=3)
        b = cap.sample_episodes(episodes, 10, seed=3)
        assert a == b
        assert len(a) == 10 and len(set(a)) == 10
        assert cap.sample_episodes(episodes, 100, seed=3).__len__() == 50
        assert cap.sample_episodes([], 10, seed=3) == []


class TestBuildConfig:
    def test_without_base(self) -> None:
        cfg = cap.build_config(
            None,
            "artifacts/idm_us101.json",
            lanes=5,
            length_m=4000.0,
            demand_veh_h_lane=2400.0,
            duration_s=1800.0,
            warmup_s=300.0,
            seed=7,
        )
        assert cfg.network.lanes == 5
        assert cfg.network.inflow == [(0.0, pytest.approx(veh_h_to_veh_s(12000.0)))]
        assert cfg.network.boundary is None
        assert cfg.fleet.idm_calibration == "artifacts/idm_us101.json"
        assert cfg.sim.duration_s == 1800.0 and cfg.sim.warmup_s == 300.0
        assert cfg.perturbation is None and cfg.seeded is False
        assert cfg.replicates == 1 and cfg.seed == 7

    def test_inherits_base_fleet_and_sim(self) -> None:
        base = ScenarioConfig.from_yaml(REPO / "scenarios" / "us101_replica.yaml")
        base = base.model_copy(
            update={"fleet": base.fleet.model_copy(update={"lc_keep_right": 0.0})}
        )
        cfg = cap.build_config(
            base,
            "trial.json",
            lanes=4,
            length_m=4000.0,
            demand_veh_h_lane=2400.0,
            duration_s=1800.0,
            warmup_s=300.0,
            seed=1,
        )
        assert cfg.fleet.idm_calibration == "trial.json"
        assert cfg.fleet.lc_keep_right == 0.0
        assert cfg.sim.step_length_s == base.sim.step_length_s
        assert cfg.sim.duration_s == 1800.0
        assert cfg.network.lanes == 4


class TestCrossingsPerHour:
    def test_first_crossing_per_vehicle_in_window(self) -> None:
        # three vehicles cross x_ref at t = 100, 200, 400; one was already beyond at t = 0
        t = np.array([0.0, 100.0, 150.0, 200.0, 300.0, 400.0, 0.0, 50.0])
        x = np.array([900.0, 1000.0, 1100.0, 1000.0, 900.0, 1000.0, 1200.0, 1300.0])
        ids = np.array(["a", "a", "a", "b", "c", "c", "d", "d"])
        thr = cap.crossings_per_hour(t, x, ids, x_ref=1000.0, t_lo=0.0, t_hi=1800.0)
        assert thr == pytest.approx(4 * 2.0)  # a, b, c and d (first sample beyond at t=0)
        thr = cap.crossings_per_hour(t, x, ids, x_ref=1000.0, t_lo=150.0, t_hi=1800.0)
        assert thr == pytest.approx(2 * 3600.0 / 1650.0)  # b and c only


# ---------------------------------------------------------------------------
# Step 2: demand-level fit helpers
# ---------------------------------------------------------------------------


class TestScaleInflows:
    def test_corridor_inflow_scaled_and_input_untouched(self) -> None:
        raw = {"network": {"kind": "corridor", "inflow": [[0.0, 2.0], [300.0, 1.5]]}}
        out = dem.scale_inflows(raw, 0.85)
        assert out["network"]["inflow"] == [[0.0, 1.7], [300.0, 1.275]]
        assert raw["network"]["inflow"] == [[0.0, 2.0], [300.0, 1.5]]

    def test_on_ramps_scaled_off_ramps_untouched(self) -> None:
        raw = {
            "network": {
                "kind": "osm",
                "inflow": [[0.0, 1.0]],
                "ramps": [
                    {"kind": "on", "inflow": [[0.0, 0.2]]},
                    {"kind": "off", "exit_fraction": 0.1},
                ],
            }
        }
        out = dem.scale_inflows(raw, 1.5)
        assert out["network"]["ramps"][0]["inflow"] == [[0.0, 0.3]]
        assert out["network"]["ramps"][1] == {"kind": "off", "exit_fraction": 0.1}

    def test_non_positive_scale_raises(self) -> None:
        with pytest.raises(ValueError):
            dem.scale_inflows({"network": {"inflow": [[0.0, 1.0]]}}, 0.0)

    def test_i24_speedcal_scenario_is_corrected_times_0_85(self) -> None:
        """The generalised scaler reproduces the committed I-24 third arm's inflows."""
        corrected = REPO / "scenarios" / "i24_replica_corrected.yaml"
        speedcal = REPO / "scenarios" / "i24_replica_speedcal.yaml"
        fit = REPO / "artifacts" / "demand_scale_i24_corrected.json"
        _skip_unless(corrected, speedcal, fit)
        level = json.loads(fit.read_text())["best"]["scale"]
        rebuilt = dem.scale_inflows(yaml.safe_load(corrected.read_text()), level)["network"]
        committed = yaml.safe_load(speedcal.read_text())["network"]
        assert rebuilt["inflow"] == committed["inflow"]
        for r_new, r_old in zip(rebuilt.get("ramps", []), committed.get("ramps", []), strict=True):
            assert r_new == r_old


class TestWindows:
    def test_parse_windows(self) -> None:
        assert dem.parse_windows("0-2,5") == [0, 1, 2, 5]
        assert dem.parse_windows("3, 1,2") == [1, 2, 3]
        with pytest.raises(ValueError):
            dem.parse_windows("3-1")
        with pytest.raises(ValueError):
            dem.parse_windows("")

    def test_default_split_is_first_half_second_half(self) -> None:
        assert dem.default_split(6) == ([0, 1, 2], [3, 4, 5])
        assert dem.default_split(24) == (list(range(12)), list(range(12, 24)))
        assert dem.default_split(5) == ([0, 1], [2, 3, 4])
        with pytest.raises(ValueError):
            dem.default_split(1)


class TestSegmentSpeeds:
    def test_known_means_and_empty_bins(self) -> None:
        t = np.array([10.0, 20.0, 10.0, 400.0, 400.0, 900.0, -1.0])
        x = np.array([10.0, 20.0, 500.0, 100.0, 639.9, 100.0, 100.0])
        v = np.array([10.0, 12.0, 20.0, 5.0, 6.0, 99.0, 99.0])
        seg = dem.segment_speeds(t, x, v, window_s=300.0, n_windows=3, span_m=640.0, n_segments=4)
        assert seg.shape == (3, 4)
        assert seg[0, 0] == pytest.approx(11.0)
        assert seg[0, 3] == pytest.approx(20.0)
        assert np.isnan(seg[0, 1]) and np.isnan(seg[0, 2])
        assert seg[1, 0] == pytest.approx(5.0)
        assert seg[1, 3] == pytest.approx(6.0)
        assert np.isnan(seg[2]).all()  # t = 900 is outside [0, 900); t < 0 dropped

    def test_out_of_span_samples_ignored(self) -> None:
        t = np.array([1.0, 1.0])
        x = np.array([640.0, -0.5])
        v = np.array([1.0, 1.0])
        seg = dem.segment_speeds(t, x, v, window_s=300.0, n_windows=1, span_m=640.0, n_segments=4)
        assert np.isnan(seg).all()

    def test_matches_us101_driver_binning(self) -> None:
        m3 = _load_script("m3_us101_validate")
        rng = np.random.default_rng(11)
        n = 5000
        traj = pd.DataFrame(
            {
                "t": rng.uniform(-50.0, 1000.0, n),
                "x": rng.uniform(-20.0, 700.0, n),
                "v": rng.uniform(0.0, 25.0, n),
            }
        )
        theirs = m3._segment_speeds(traj)
        mine = dem.segment_speeds(
            traj["t"].to_numpy(),
            traj["x"].to_numpy(),
            traj["v"].to_numpy(),
            window_s=m3.WINDOW_S,
            n_windows=m3.N_WINDOWS,
            span_m=m3.SITE_LENGTH_M,
            n_segments=m3.N_SEGMENTS,
        )
        np.testing.assert_allclose(mine, theirs, equal_nan=True)
        fine_theirs = m3._segment_speeds(
            traj, window_s=m3.FINE_WINDOW_S, n_windows=m3.N_FINE_WINDOWS
        )
        fine_mine = dem.segment_speeds(
            traj["t"].to_numpy(),
            traj["x"].to_numpy(),
            traj["v"].to_numpy(),
            window_s=m3.FINE_WINDOW_S,
            n_windows=m3.N_FINE_WINDOWS,
            span_m=m3.SITE_LENGTH_M,
            n_segments=m3.N_SEGMENTS,
        )
        np.testing.assert_allclose(fine_mine, fine_theirs, equal_nan=True)

    def test_matches_i24_driver_binning(self) -> None:
        try:
            i24 = _load_script("i24_validate")
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"i24_validate not importable here: {exc}")
        rng = np.random.default_rng(5)
        n = 4000
        traj = pd.DataFrame(
            {
                "t": rng.uniform(-10.0, 7500.0, n),
                "x": rng.uniform(-100.0, 5600.0, n),
                "v": rng.uniform(0.0, 30.0, n),
            }
        )
        span_hi, n_win = 5400.0, 24
        theirs = i24._segment_speeds(traj, span_hi, n_win)
        mine = dem.segment_speeds(
            traj["t"].to_numpy(),
            traj["x"].to_numpy(),
            traj["v"].to_numpy(),
            window_s=i24.WINDOW_S,
            n_windows=n_win,
            span_m=span_hi,
            n_segments=i24.N_SEGMENTS,
        )
        np.testing.assert_allclose(mine, theirs, equal_nan=True)


class TestRmspeWindows:
    def test_subset_and_masking(self) -> None:
        obs = np.array([[10.0, 20.0], [10.0, np.nan], [0.0, 10.0]])
        sim = np.array([[11.0, 22.0], [12.0, 5.0], [7.0, 12.0]])
        assert dem.rmspe_windows(sim, obs, [0]) == pytest.approx(0.1)
        assert dem.rmspe_windows(sim, obs, [1]) == pytest.approx(0.2)  # NaN obs skipped
        assert dem.rmspe_windows(sim, obs, [2]) == pytest.approx(0.2)  # zero obs skipped
        assert dem.rmspe_windows(sim, obs, range(3)) == pytest.approx(
            np.sqrt(np.mean(np.square([0.1, 0.1, 0.2, 0.2])))
        )
        assert np.isnan(dem.rmspe_windows(sim, np.full_like(obs, np.nan), [0]))


class TestGridAndSelection:
    def test_refine_grid(self) -> None:
        assert dem.refine_grid(0.85, 0.025, 3) == [0.775, 0.8, 0.825, 0.875, 0.9, 0.925]
        assert dem.refine_grid(0.05, 0.025, 3) == [0.025, 0.075, 0.1, 0.125]

    def test_best_row_ties_toward_smaller_scale(self) -> None:
        table = [
            {"scale": 1.0, "rmspe_train": 0.30},
            {"scale": 0.9, "rmspe_train": 0.30},
            {"scale": 1.1, "rmspe_train": float("nan")},
        ]
        assert dem.best_row(table)["scale"] == 0.9
        with pytest.raises(ValueError):
            dem.best_row([{"scale": 1.0, "rmspe_train": float("nan")}])

    def test_summarize_averages_seeds(self) -> None:
        rows = [
            {
                "scale": 1.0,
                "seed": 1,
                "config_hash": "a",
                "inserted_fraction": 0.9,
                "rmspe_train": 0.2,
                "rmspe_test": 0.3,
                "rmspe_all": 0.25,
            },
            {
                "scale": 1.0,
                "seed": 2,
                "config_hash": "b",
                "inserted_fraction": 1.0,
                "rmspe_train": 0.4,
                "rmspe_test": 0.5,
                "rmspe_all": 0.45,
            },
            {
                "scale": 0.8,
                "seed": 1,
                "config_hash": "c",
                "inserted_fraction": 1.0,
                "rmspe_train": 0.1,
                "rmspe_test": 0.6,
                "rmspe_all": 0.35,
            },
        ]
        table = dem.summarize(rows)
        assert [r["scale"] for r in table] == [0.8, 1.0]
        assert table[1]["n_seeds"] == 2
        assert table[1]["rmspe_train"] == pytest.approx(0.3)
        assert table[1]["inserted_fraction"] == pytest.approx(0.95)
        assert table[1]["config_hash"] is None and table[0]["config_hash"] == "c"


class TestSimFrame:
    def test_mapping_into_observed_coordinates(self, tmp_path: Path) -> None:
        pq = tmp_path / "trajectories.parquet"
        pd.DataFrame(
            {
                "t": [100.0, 200.0, 300.0],
                "x": [640.0, 960.0, 1280.0],
                "v": [1.0, 2.0, 3.0],
                "veh_id": ["a", "a", "a"],
                "lane": [0, 0, 0],
            }
        ).to_parquet(pq)
        df = dem.sim_frame(pq, warmup_s=180.0, x_offset_m=640.0, x_scale=2.0)
        assert list(df["t"]) == [20.0, 120.0]
        assert list(df["x"]) == [160.0, 320.0]
        assert list(df["v"]) == [2.0, 3.0]


class TestBoundarySnapshot:
    def test_snapshot_reproduces_with_boundary_config(self, tmp_path: Path) -> None:
        m3 = _load_script("m3_us101_validate")
        bspec = BoundarySpec(steps=[(0.0, 15.0), (300.0, 8.0)], exit_buffer_m=200.0)
        out = tmp_path / "snapshot.yaml"
        cfg = m3.write_boundary_snapshot(bspec, out)
        reloaded = ScenarioConfig.from_yaml(out)
        assert config_hash(reloaded) == config_hash(cfg)
        assert reloaded.network.boundary is not None
        assert reloaded.network.boundary.steps == [(0.0, 15.0), (300.0, 8.0)]
        assert reloaded.name == "us101_replica"
        # the scaled, renamed calibrated scenario keeps the boundary verbatim
        scaled = dem.scale_inflows(yaml.safe_load(out.read_text()), 0.9)
        scaled["name"] = "us101_replica_calibrated"
        cal = ScenarioConfig.model_validate(scaled)
        assert cal.network.boundary == reloaded.network.boundary
        assert cal.network.inflow[0][1] == pytest.approx(reloaded.network.inflow[0][1] * 0.9)
