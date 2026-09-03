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
    LinkHourGEH,
    Metrics,
    aggregate,
    compute_metrics,
    count_crossings,
    crossings_per_window,
    geh,
    geh_pass_fraction,
    link_hour_geh,
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


class TestCountCrossings:
    """Crossing = same-vehicle sample pair with x_prev < x_ref <= x_cur, stamped
    at the later sample. On the 20/25/30 m/s fixture the crossings of
    x = 1000 m are stamped at t = 50.0, 40.0 and 33.5 s (the 30 m/s vehicle
    reaches 1000 m between the 33.0 s and 33.5 s samples)."""

    def test_unbounded_count(self):
        assert count_crossings(_three_vehicle_traj(), 1000.0) == 3

    def test_time_bounds_inclusive_lower_exclusive_upper(self):
        traj = _three_vehicle_traj()
        assert count_crossings(traj, 1000.0, t_lo=33.5, t_hi=50.0) == 2
        assert count_crossings(traj, 1000.0, t_lo=33.6, t_hi=50.0) == 1
        assert count_crossings(traj, 1000.0, t_lo=33.5, t_hi=50.5) == 3
        assert count_crossings(traj, 1000.0, t_hi=33.5) == 0
        assert count_crossings(traj, 1000.0, t_lo=50.0) == 1

    def test_ring_wrap_is_not_a_crossing(self):
        # One vehicle at 5 m/s on a 100 m ring, sampled at 2 Hz for 60 s:
        # x = 50 m is reached at t = 10, 30, 50 s; the wrap jump 97.5 -> 2.5
        # is downward and must not count.
        t = np.arange(0.0, 60.0 + 0.25, 0.5)
        traj = pd.DataFrame({"t": t, "veh_id": "v0", "x": (5.0 * t) % 100.0, "v": 5.0})
        assert count_crossings(traj, 50.0) == 3
        assert count_crossings(traj, 50.0, t_lo=10.0, t_hi=30.0) == 1

    def test_validates_inputs(self):
        traj = _three_vehicle_traj()
        with pytest.raises(ValueError, match="t_hi > t_lo"):
            count_crossings(traj, 1000.0, t_lo=10.0, t_hi=10.0)
        with pytest.raises(ValueError, match="missing column"):
            count_crossings(traj.drop(columns=["x"]), 1000.0)

    def test_per_window_hand_computed(self):
        traj = _three_vehicle_traj()
        counts = crossings_per_window(traj, 1000.0, t_lo=0.0, t_hi=100.0, window_s=25.0)
        # Stamps 33.5 and 40.0 fall in [25, 50); 50.0 falls in [50, 75).
        assert counts.tolist() == [0, 2, 1, 0]
        assert counts.dtype == np.int64

    def test_per_window_rejects_partial_windows(self):
        traj = _three_vehicle_traj()
        with pytest.raises(ValueError, match="whole number"):
            crossings_per_window(traj, 1000.0, t_lo=0.0, t_hi=90.0, window_s=25.0)
        with pytest.raises(ValueError, match="window_s"):
            crossings_per_window(traj, 1000.0, t_lo=0.0, t_hi=100.0, window_s=0.0)


class TestGehPassFraction:
    def test_strict_bound_and_nan_fail(self):
        assert geh_pass_fraction([1.0, 4.9, 5.0, 9.0]) == pytest.approx(0.5)
        assert geh_pass_fraction([1.0, math.nan]) == pytest.approx(0.5)
        assert geh_pass_fraction([1.0, 2.0], threshold=3.0) == 1.0

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="undefined"):
            geh_pass_fraction([])


class TestLinkHourGEH:
    """Two 50 s windows on the 20/25/30 m/s fixture, hourly-equivalent flows.

    x = 1000 m: crossings at 33.5 and 40.0 s -> 2 in [0, 50) -> 144 veh/h;
    50.0 s -> 1 in [50, 100) -> 72 veh/h.
    x = 2000 m: crossings stamped at 67.0 and 80.0 s (the 20 m/s vehicle's
    crossing at exactly 100.0 s is outside [50, 100)) -> 144 veh/h.
    """

    def _observed(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "x_ref_m": [1000.0, 1000.0, 2000.0, 2000.0],
                "window_start_s": [0.0, 50.0, 50.0, 0.0],
                "flow_veh_h": [144.0, 100.0, 144.0, math.nan],
            }
        )

    def test_hand_computed_values(self):
        res = link_hour_geh(
            _three_vehicle_traj(), self._observed(), x_refs_m=[1000.0, 2000.0], window_s=50.0
        )
        assert isinstance(res, LinkHourGEH)
        assert res.n_dropped_nan == 1
        assert res.sim_veh_h == pytest.approx((144.0, 72.0, 144.0))
        assert res.obs_veh_h == pytest.approx((144.0, 100.0, 144.0))
        assert res.x_ref_m == (1000.0, 1000.0, 2000.0)
        assert res.window_start_s == (0.0, 50.0, 50.0)
        expected = (0.0, math.sqrt(2.0 * (72.0 - 100.0) ** 2 / 172.0), 0.0)
        assert res.geh == pytest.approx(expected)
        assert res.pass_fraction() == pytest.approx(1.0)
        assert res.pass_fraction(threshold=3.0) == pytest.approx(2.0 / 3.0)
        assert res.window_s == 50.0

    def test_unmatched_cross_section_raises(self):
        with pytest.raises(ValueError, match="not in x_refs_m"):
            link_hour_geh(_three_vehicle_traj(), self._observed(), x_refs_m=[1000.0], window_s=50.0)

    def test_window_outside_simulated_span_raises(self):
        obs = pd.DataFrame({"x_ref_m": [1000.0], "window_start_s": [75.0], "flow_veh_h": [10.0]})
        with pytest.raises(ValueError, match="not covered"):
            link_hour_geh(_three_vehicle_traj(), obs, x_refs_m=[1000.0], window_s=50.0)

    def test_window_ending_exactly_at_last_sample_is_covered(self):
        obs = pd.DataFrame({"x_ref_m": [1000.0], "window_start_s": [50.0], "flow_veh_h": [72.0]})
        res = link_hour_geh(_three_vehicle_traj(), obs, x_refs_m=[1000.0], window_s=50.0)
        assert res.geh == pytest.approx((0.0,))

    def test_validates_columns_and_flows(self):
        traj = _three_vehicle_traj()
        with pytest.raises(ValueError, match="observed missing column"):
            link_hour_geh(traj, pd.DataFrame({"x_ref_m": [1.0]}), x_refs_m=[1.0])
        bad = pd.DataFrame({"x_ref_m": [1000.0], "window_start_s": [0.0], "flow_veh_h": [-1.0]})
        with pytest.raises(ValueError, match=">= 0"):
            link_hour_geh(traj, bad, x_refs_m=[1000.0], window_s=50.0)
        with pytest.raises(ValueError, match="x_refs_m is empty"):
            link_hour_geh(traj, self._observed(), x_refs_m=[], window_s=50.0)
