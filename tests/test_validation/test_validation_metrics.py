"""Tests for validation.metrics: hand-computed fixtures, GEH/RMSPE values,
and replicate aggregation against a scipy reference."""

import dataclasses
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from validation.metrics import (
    CI,
    MIN_REPLICATES,
    Metrics,
    aggregate,
    compute_metrics,
    geh,
    rmspe,
    travel_times,
)

SPEEDS = (20.0, 25.0, 30.0)


def _three_vehicle_traj() -> pd.DataFrame:
    """Three vehicles at constant speeds 20/25/30 m/s from x=0 over 100 s,
    sampled at 2 Hz — every metric is hand-computable."""
    frames = []
    t = np.arange(0.0, 100.0 + 0.25, 0.5)
    for i, v in enumerate(SPEEDS):
        frames.append(
            pd.DataFrame(
                {
                    "t": t,
                    "veh_id": f"veh{i}",
                    "x": v * t,
                    "lane": np.zeros(len(t), dtype=np.int32),
                    "v": np.full(len(t), v),
                    "a": np.zeros(len(t)),
                    "is_av": False,
                    "complied": True,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _write_run(run_dir: Path, fuel_total_ml: float | None = 750.0) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    _three_vehicle_traj().to_parquet(run_dir / "trajectories.parquet")
    meta = {
        "config_hash": "cafe01234567",
        "seed": 7,
        "tier": "micro",
        "seeded": False,
        "versions": {"eclipse-sumo": "1.27.1"},
        "wall_time_s": 1.0,
    }
    if fuel_total_ml is not None:
        meta["fuel_total_ml"] = fuel_total_ml
    (run_dir / "meta.json").write_text(json.dumps(meta))
    return run_dir


class TestComputeMetrics:
    @pytest.fixture()
    def run_dir(self, tmp_path: Path) -> Path:
        return _write_run(tmp_path / "runs" / "cafe01234567" / "7")

    def test_hand_computed_values(self, run_dir: Path):
        m = compute_metrics(run_dir, x_ref=1000.0, span=(0.0, 2000.0))
        # 3 vehicles cross x=1000 once each over a 100 s observation window.
        assert m.throughput_veh_h == pytest.approx(3.0 / 100.0 * 3600.0)
        # Travel times over [0, 2000] m: 100, 80, 2000/30 s.
        expected_tts = [2000.0 / v for v in SPEEDS]
        assert m.mean_tt_s == pytest.approx(np.mean(expected_tts))
        assert m.p90_tt_s == pytest.approx(np.percentile(expected_tts, 90))
        # Spatial sigma: std over {20,25,30} (ddof=1) at every timestamp.
        assert m.sigma_v_spatial_ms == pytest.approx(np.std(SPEEDS, ddof=1))
        # Temporal sigma: each vehicle's speed is constant.
        assert m.sigma_v_temporal_ms == pytest.approx(0.0, abs=1e-12)
        # VMT: (2000 + 2500 + 3000) m = 7.5 veh-km; VHT: 300 s.
        assert m.vmt_veh_km == pytest.approx(7.5)
        assert m.vht_veh_h == pytest.approx(300.0 / 3600.0)
        # Fuel: 750 ml over 7.5 veh-km.
        assert m.fuel_ml_per_veh_km == pytest.approx(100.0)
        # Free flow at >= 20 m/s: no stop-and-go waves.
        assert m.wave_count == 0
        assert math.isnan(m.wave_speed_kmh)
        assert math.isnan(m.wave_amplitude_ms)

    def test_fuel_nan_when_not_recorded(self, tmp_path: Path):
        run_dir = _write_run(tmp_path / "run", fuel_total_ml=None)
        m = compute_metrics(run_dir, span=(0.0, 2000.0))
        assert math.isnan(m.fuel_ml_per_veh_km)

    def test_default_reference_and_span(self, run_dir: Path):
        # Defaults: x_ref = mid-range (1500 m), span = full range [0, 3000].
        m = compute_metrics(run_dir)
        # All three vehicles pass x=1500 within the 100 s window.
        assert m.throughput_veh_h == pytest.approx(3.0 / 100.0 * 3600.0)
        # Only the 30 m/s vehicle completes the full [0, 3000] span.
        assert m.mean_tt_s == pytest.approx(100.0)

    def test_missing_files_raise(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            compute_metrics(tmp_path)


class TestTravelTimes:
    def test_interpolated_crossings(self):
        traj = _three_vehicle_traj()
        tts = travel_times(traj, 100.0, 1900.0)
        # Constant speed: exactly 1800/v each, crossing times interpolated.
        assert sorted(tts.tolist()) == pytest.approx(sorted(1800.0 / v for v in SPEEDS))

    def test_excludes_non_completers(self):
        traj = _three_vehicle_traj()
        tts = travel_times(traj, 0.0, 2800.0)  # only the 30 m/s vehicle gets there
        assert len(tts) == 1
        assert tts[0] == pytest.approx(2800.0 / 30.0)

    def test_validates_span(self):
        with pytest.raises(ValueError, match="x_hi > x_lo"):
            travel_times(_three_vehicle_traj(), 10.0, 10.0)


class TestGehRmspe:
    def test_geh_hand_values(self):
        assert geh(100.0, 100.0) == 0.0
        assert geh(105.0, 100.0) == pytest.approx(math.sqrt(2 * 25.0 / 205.0))
        assert geh(60.0, 30.0) == pytest.approx(math.sqrt(20.0))
        assert geh(0.0, 0.0) == 0.0
        with pytest.raises(ValueError, match=">= 0"):
            geh(-1.0, 5.0)

    def test_rmspe_hand_values(self):
        sim = np.array([110.0, 90.0])
        obs = np.array([100.0, 100.0])
        assert rmspe(sim, obs) == pytest.approx(0.1)
        with pytest.raises(ValueError, match="zero observations"):
            rmspe(np.array([1.0]), np.array([0.0]))
        with pytest.raises(ValueError, match="shape mismatch"):
            rmspe(np.array([1.0]), np.array([1.0, 2.0]))
        with pytest.raises(ValueError, match="empty"):
            rmspe(np.array([]), np.array([]))


def _metrics_with(throughput: float, fuel: float = math.nan) -> Metrics:
    return Metrics(
        throughput_veh_h=throughput,
        mean_tt_s=100.0,
        p90_tt_s=120.0,
        sigma_v_spatial_ms=1.0,
        sigma_v_temporal_ms=0.5,
        vmt_veh_km=10.0,
        vht_veh_h=0.1,
        fuel_ml_per_veh_km=fuel,
        wave_count=0,
        wave_speed_kmh=math.nan,
        wave_amplitude_ms=math.nan,
    )


class TestAggregate:
    def test_ci_matches_scipy_reference(self):
        values = [1800.0, 1850.0, 1790.0, 1900.0, 1820.0, 1760.0, 1880.0, 1810.0]
        agg = aggregate([_metrics_with(v) for v in values])
        ci = agg["throughput_veh_h"]
        arr = np.asarray(values)
        n = len(arr)
        half = stats.t.ppf(0.975, n - 1) * arr.std(ddof=1) / math.sqrt(n)
        assert ci.mean == pytest.approx(arr.mean())
        assert ci.lo95 == pytest.approx(arr.mean() - half)
        assert ci.hi95 == pytest.approx(arr.mean() + half)
        assert ci.n == n
        # scipy.stats.t.interval agrees too.
        lo_ref, hi_ref = stats.t.interval(0.95, n - 1, loc=arr.mean(), scale=stats.sem(arr))
        assert ci.lo95 == pytest.approx(lo_ref)
        assert ci.hi95 == pytest.approx(hi_ref)

    def test_underpowered_flag(self):
        few = aggregate([_metrics_with(1800.0 + i) for i in range(MIN_REPLICATES - 1)])
        assert few["throughput_veh_h"].underpowered
        enough = aggregate([_metrics_with(1800.0 + i) for i in range(MIN_REPLICATES)])
        assert not enough["throughput_veh_h"].underpowered

    def test_nan_values_dropped_per_field(self):
        ms = [_metrics_with(1800.0, fuel=math.nan), _metrics_with(1900.0, fuel=50.0)]
        agg = aggregate(ms)
        assert agg["throughput_veh_h"].n == 2
        assert agg["fuel_ml_per_veh_km"].n == 1
        assert agg["fuel_ml_per_veh_km"].mean == pytest.approx(50.0)
        assert math.isnan(agg["fuel_ml_per_veh_km"].lo95)
        assert agg["wave_speed_kmh"].n == 0
        assert math.isnan(agg["wave_speed_kmh"].mean)

    def test_covers_every_metrics_field(self):
        agg = aggregate([_metrics_with(1800.0)])
        assert set(agg) == {f.name for f in dataclasses.fields(Metrics)}

    def test_single_replicate_has_nan_bounds(self):
        ci = aggregate([_metrics_with(1800.0)])["throughput_veh_h"]
        assert ci == CI(1800.0, ci.lo95, ci.hi95, 1)
        assert math.isnan(ci.lo95) and math.isnan(ci.hi95)

    def test_empty_list_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            aggregate([])
