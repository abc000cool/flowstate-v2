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
    default_travel_span,
    geh,
    geh_pass_fraction,
    link_hour_geh,
    rmspe,
    travel_times,
    warmup_from_meta,
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


#: Staggered-entry fixture: entry times [s] and the common speed [m/s].
ENTRY_TIMES = (0.0, 50.0, 100.0, 150.0)
ENTRY_SPEED = 25.0
CORRIDOR_M = 2500.0
WARMUP_S = 60.0


def _staggered_traj(
    entries: tuple[float, ...] = ENTRY_TIMES, speed: float = ENTRY_SPEED
) -> pd.DataFrame:
    """One vehicle per entry time, each crossing ``CORRIDOR_M`` at ``speed``.

    Every vehicle travels the whole corridor in exactly
    ``CORRIDOR_M / speed`` s, so warm-up windowing is hand-computable.
    """
    frames = []
    for i, t0 in enumerate(entries):
        t = np.arange(t0, t0 + CORRIDOR_M / speed + 0.25, 0.5)
        frames.append(
            pd.DataFrame(
                {
                    "t": t,
                    "veh_id": f"veh{i}",
                    "x": speed * (t - t0),
                    "lane": np.zeros(len(t), dtype=np.int32),
                    "v": np.full(len(t), speed),
                    "a": np.zeros(len(t)),
                    "is_av": False,
                    "complied": True,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _write_run(
    run_dir: Path,
    fuel_total_ml: float | None = 750.0,
    traj: pd.DataFrame | None = None,
    warmup_s: float | None = None,
    extra_meta: dict | None = None,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (traj if traj is not None else _three_vehicle_traj()).to_parquet(
        run_dir / "trajectories.parquet"
    )
    meta: dict = {
        "config_hash": "cafe01234567",
        "seed": 7,
        "tier": "micro",
        "seeded": False,
        "versions": {"eclipse-sumo": "1.27.1"},
        "wall_time_s": 1.0,
    }
    if fuel_total_ml is not None:
        meta["fuel_total_ml"] = fuel_total_ml
    if warmup_s is not None:
        meta["config"] = {"sim": {"warmup_s": warmup_s}}
    if extra_meta:
        meta.update(extra_meta)
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
        # Defaults: x_ref = mid-range (1500 m), span = [0, median furthest x].
        m = compute_metrics(run_dir)
        # All three vehicles pass x=1500 within the 100 s window.
        assert m.throughput_veh_h == pytest.approx(3.0 / 100.0 * 3600.0)
        # Per-vehicle furthest positions are 2000/2500/3000 m: the default
        # exit bound is the median, 2500 m, which two of the three reach.
        expected = [2500.0 / v for v in (25.0, 30.0)]
        assert m.n_travel_time_veh == 2
        assert m.mean_tt_s == pytest.approx(np.mean(expected))
        assert m.p90_tt_s == pytest.approx(np.percentile(expected, 90))

    def test_default_span_is_not_the_global_maximum(self, run_dir: Path):
        """A (x_min, x_max) span is reached by one vehicle only (regression).

        Its tell is ``mean_tt_s == p90_tt_s``: a single-vehicle sample was
        being published as a fleet mean with replicate CIs.
        """
        assert default_travel_span(_three_vehicle_traj()) == (0.0, 2500.0)
        one = compute_metrics(run_dir, span=(0.0, 3000.0))
        assert one.n_travel_time_veh == 1
        assert math.isnan(one.mean_tt_s) and math.isnan(one.p90_tt_s)

    def test_travel_times_undefined_below_two_vehicles(self, tmp_path: Path):
        run_dir = _write_run(tmp_path / "run", traj=_staggered_traj(entries=(0.0,)))
        m = compute_metrics(run_dir)
        assert m.n_travel_time_veh == 1
        assert math.isnan(m.mean_tt_s) and math.isnan(m.p90_tt_s)


class TestWarmup:
    """``sim.warmup_s`` is discarded from every metric (SimSpec's contract)."""

    @pytest.fixture()
    def run_dir(self, tmp_path: Path) -> Path:
        return _write_run(
            tmp_path / "warm",
            fuel_total_ml=1000.0,
            traj=_staggered_traj(),
            warmup_s=WARMUP_S,
        )

    def test_warmup_from_meta_reads_the_config_block(self):
        assert warmup_from_meta({"config": {"sim": {"warmup_s": 600.0}}}) == 600.0
        assert warmup_from_meta({}) == 0.0
        assert warmup_from_meta({"config": {"sim": {}}}) == 0.0
        assert warmup_from_meta({"config": {"sim": {"warmup_s": None}}}) == 0.0
        assert warmup_from_meta({"config": {"sim": {"warmup_s": True}}}) == 0.0
        assert warmup_from_meta({"config": {"sim": {"warmup_s": -5.0}}}) == 0.0

    def test_window_applied_to_every_metric(self, run_dir: Path):
        m = compute_metrics(run_dir)
        t_end = ENTRY_TIMES[-1] + CORRIDOR_M / ENTRY_SPEED  # 250 s
        # Throughput: the three crossings of the midpoint at t >= 60 s, over
        # the window length — not four crossings over the whole record.
        assert m.throughput_veh_h == pytest.approx(3.0 / (t_end - WARMUP_S) * 3600.0)
        # Travel times: whole journeys only. The two vehicles entering after
        # the warm-up; the one already in flight is not clipped to t_lo.
        assert m.n_travel_time_veh == 2
        assert m.mean_tt_s == pytest.approx(CORRIDOR_M / ENTRY_SPEED)
        # VMT: distance covered after the warm-up (1000 + 2250 + 2500 + 2500 m).
        assert m.vmt_veh_km == pytest.approx(8.25)
        # VHT: 40 + 90 + 100 + 100 s of observed travel inside the window.
        assert m.vht_veh_h == pytest.approx(330.0 / 3600.0)

    def test_whole_run_fuel_keeps_a_whole_run_denominator(self, run_dir: Path):
        """Whole-run fuel over post-warm-up VMT would inflate ml/veh·km."""
        m = compute_metrics(run_dir)
        assert m.vmt_veh_km == pytest.approx(8.25)
        assert m.fuel_ml_per_veh_km == pytest.approx(1000.0 / 10.0)

    def test_windowed_fuel_total_is_preferred(self, tmp_path: Path):
        run_dir = _write_run(
            tmp_path / "warm",
            fuel_total_ml=1000.0,
            traj=_staggered_traj(),
            warmup_s=WARMUP_S,
            extra_meta={"fuel_total_ml_post_warmup": 800.0},
        )
        m = compute_metrics(run_dir)
        assert m.fuel_ml_per_veh_km == pytest.approx(800.0 / 8.25)

    def test_explicit_zero_measures_the_whole_record(self, run_dir: Path):
        m = compute_metrics(run_dir, warmup_s=0.0)
        t_end = ENTRY_TIMES[-1] + CORRIDOR_M / ENTRY_SPEED
        assert m.throughput_veh_h == pytest.approx(len(ENTRY_TIMES) / t_end * 3600.0)
        assert m.vmt_veh_km == pytest.approx(10.0)
        assert m.n_travel_time_veh == len(ENTRY_TIMES)

    def test_warmup_only_traffic_excluded_from_speed_statistics(self, tmp_path: Path):
        """A slow vehicle that leaves before the window must not move σ_v."""
        transient = pd.DataFrame(
            {
                "t": np.arange(0.0, 50.0, 0.5),
                "veh_id": "slow",
                "x": 2.0 * np.arange(0.0, 50.0, 0.5),
                "lane": 0,
                "v": 2.0,
                "a": 0.0,
                "is_av": False,
                "complied": True,
            }
        )
        traj = pd.concat([_staggered_traj(), transient], ignore_index=True)
        run_dir = _write_run(tmp_path / "warm", traj=traj, warmup_s=WARMUP_S)
        assert compute_metrics(run_dir).sigma_v_spatial_ms == pytest.approx(0.0, abs=1e-12)
        assert compute_metrics(run_dir, warmup_s=0.0).sigma_v_spatial_ms > 1.0

    def test_warmup_covering_the_run_is_an_error(self, tmp_path: Path):
        run_dir = _write_run(tmp_path / "warm", traj=_staggered_traj(), warmup_s=10_000.0)
        with pytest.raises(ValueError, match="no measurement window"):
            compute_metrics(run_dir)

    def test_negative_warmup_rejected(self, run_dir: Path):
        with pytest.raises(ValueError, match="must be finite and >= 0"):
            compute_metrics(run_dir, warmup_s=-1.0)

    def test_ring_style_run_has_no_whole_journey(self, tmp_path: Path):
        """Every vehicle present from t=0 (a ring): travel times undefined."""
        run_dir = _write_run(
            tmp_path / "ring",
            traj=_staggered_traj(entries=(0.0, 0.0 + 1e-9)),
            warmup_s=WARMUP_S,
        )
        m = compute_metrics(run_dir)
        assert m.n_travel_time_veh == 0
        assert math.isnan(m.mean_tt_s) and math.isnan(m.p90_tt_s)

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

    def test_cross_section_outside_the_simulated_span_is_dropped_not_scored(self):
        """No vehicle can cross x = 5000 m, so it is excluded, not failed.

        Scoring it would report a simulated flow of zero and a GEH of
        sqrt(2 * q_obs) — a failing link-hour that measures the corridor's
        extent rather than the model.
        """
        obs = pd.DataFrame(
            {
                "x_ref_m": [1000.0, 5000.0, 5000.0],
                "window_start_s": [0.0, 0.0, 50.0],
                "flow_veh_h": [144.0, 144.0, 144.0],
            }
        )
        res = link_hour_geh(_three_vehicle_traj(), obs, x_refs_m=[1000.0, 5000.0], window_s=50.0)
        assert res.n_dropped_outside_span == 2
        assert res.x_outside_span_m == (5000.0,)
        assert res.x_ref_m == (1000.0,)
        assert res.geh == pytest.approx((0.0,))

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
