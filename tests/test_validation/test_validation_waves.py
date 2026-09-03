"""Tests for validation.waves: detection on synthetic planted-wave fields."""

import numpy as np
import pytest

from flowstate_core.rng import make_rng
from flowstate_core.units import kmh_to_ms
from validation.fields import SpeedField
from validation.waves import detect_waves

V_FREE = 30.0
V_JAMMED = 5.0
PLANT_SPEED_MS = -kmh_to_ms(16.0)  # backward wave, exactly -16 km/h


def _grid(t_end: float = 480.0, x_end: float = 3000.0) -> tuple[np.ndarray, np.ndarray]:
    return np.arange(0.0, t_end + 15.0, 15.0), np.arange(0.0, x_end + 75.0, 75.0)


def _planted_field(seed: int = 123, noise_sigma: float = 0.3) -> SpeedField:
    """Free flow at 30 m/s + noise, with a rigid low-speed band moving at
    exactly -16 km/h (backward): jam center x_c(t) = 2600 + c*t, half-width
    150 m."""
    t_edges, x_edges = _grid()
    t_c = 0.5 * (t_edges[:-1] + t_edges[1:])
    x_c = 0.5 * (x_edges[:-1] + x_edges[1:])
    rng = make_rng(seed)
    speed = V_FREE + noise_sigma * rng.standard_normal((len(t_c), len(x_c)))
    for i, t in enumerate(t_c):
        center = 2600.0 + PLANT_SPEED_MS * t
        speed[i, np.abs(x_c - center) <= 150.0] = V_JAMMED
    return SpeedField(t_edges=t_edges, x_edges=x_edges, mean_speed=speed)


class TestPlantedWave:
    def test_recovers_planted_backward_wave(self):
        ws = detect_waves(_planted_field())
        assert ws.count == 1
        wave = ws.waves[0]
        # Speed recovered within bin-quantization tolerance; sign backward.
        assert wave.speed_ms < 0
        assert wave.speed_ms == pytest.approx(PLANT_SPEED_MS, abs=0.7)
        assert len(ws.backward()) == 1
        # Amplitude = free speed minus jam minimum, close to 25 m/s.
        assert wave.amplitude_ms == pytest.approx(V_FREE - V_JAMMED, abs=2.0)
        # The band exists in every time row -> duration equals the field span.
        assert wave.duration_s == pytest.approx(480.0)
        # Extent covers the band's swept x-range (about 2.4 km), not the
        # instantaneous band width.
        assert 2100.0 < wave.extent_m < 2700.0

    def test_free_flow_has_no_waves(self):
        t_edges, x_edges = _grid()
        rng = make_rng(7)
        speed = V_FREE + 0.3 * rng.standard_normal((len(t_edges) - 1, len(x_edges) - 1))
        ws = detect_waves(SpeedField(t_edges=t_edges, x_edges=x_edges, mean_speed=speed))
        assert ws.count == 0

    def test_nan_bins_are_not_jammed(self):
        field = _planted_field()
        field.mean_speed[:, :] = np.nan
        assert detect_waves(field).count == 0


class TestFilters:
    def test_min_area_filters_small_blobs(self):
        t_edges, x_edges = _grid(t_end=150.0, x_end=750.0)
        speed = np.full((len(t_edges) - 1, len(x_edges) - 1), V_FREE)
        speed[2:4, 3] = V_JAMMED  # 2-bin blob spanning 2 time rows
        field = SpeedField(t_edges=t_edges, x_edges=x_edges, mean_speed=speed)
        assert detect_waves(field, min_area_bins=4).count == 0
        assert detect_waves(field, min_area_bins=1).count == 1

    def test_single_time_row_component_skipped(self):
        t_edges, x_edges = _grid(t_end=150.0, x_end=750.0)
        speed = np.full((len(t_edges) - 1, len(x_edges) - 1), V_FREE)
        speed[3, 2:8] = V_JAMMED  # 6 bins but a single time row: no propagation
        field = SpeedField(t_edges=t_edges, x_edges=x_edges, mean_speed=speed)
        assert detect_waves(field, min_area_bins=4).count == 0

    def test_rejects_bad_params(self):
        field = _planted_field()
        with pytest.raises(ValueError, match="min_area_bins"):
            detect_waves(field, min_area_bins=0)
        with pytest.raises(ValueError, match="v_jam_thresh"):
            detect_waves(field, v_jam_thresh=0.0)

    def test_explicit_v_free_overrides_estimate(self):
        ws = detect_waves(_planted_field(), v_free=40.0)
        assert ws.waves[0].amplitude_ms == pytest.approx(40.0 - V_JAMMED, abs=1e-9)


def _slow_striped_field(
    seed: int = 5, v_recovery: float = 6.0, v_stripe: float = 1.0
) -> SpeedField:
    """A field that is congested EVERYWHERE (recovery 6 m/s ≈ 22 km/h, well
    below the 40 km/h absolute threshold) with a rigid stripe at 1 m/s moving
    backward at exactly -16 km/h — the high-density regime where the absolute
    detector labels the whole field as one jam."""
    t_edges, x_edges = _grid()
    t_c = 0.5 * (t_edges[:-1] + t_edges[1:])
    x_c = 0.5 * (x_edges[:-1] + x_edges[1:])
    rng = make_rng(seed)
    speed = v_recovery + 0.2 * rng.standard_normal((len(t_c), len(x_c)))
    for i, t in enumerate(t_c):
        center = 2600.0 + PLANT_SPEED_MS * t
        speed[i, np.abs(x_c - center) <= 150.0] = v_stripe
    return SpeedField(t_edges=t_edges, x_edges=x_edges, mean_speed=speed)


class TestRelativeMode:
    """Track D1: stripes inside a fully congested field (docs/WAVE_SPEED_DIAGNOSIS.md)."""

    def test_absolute_threshold_degenerates_on_fully_congested_field(self):
        ws = detect_waves(_slow_striped_field())
        # Every bin is below 40 km/h: one blob whose upstream front pins at x = 0.
        assert ws.count == 1
        assert ws.waves[0].speed_ms == pytest.approx(0.0, abs=0.05)
        assert ws.backward() == ()

    def test_relative_threshold_recovers_the_stripe(self):
        ws = detect_waves(_slow_striped_field(), relative_frac=0.5)
        assert ws.count == 1
        wave = ws.waves[0]
        assert wave.speed_ms == pytest.approx(PLANT_SPEED_MS, abs=0.7)
        assert len(ws.backward()) == 1
        assert wave.duration_s == pytest.approx(480.0)

    def test_relative_threshold_value(self):
        from validation.waves import relative_jam_threshold

        field = _slow_striped_field()
        thr = relative_jam_threshold(field, 0.5)
        finite = field.mean_speed[~np.isnan(field.mean_speed)]
        assert thr == pytest.approx(0.5 * np.percentile(finite, 90.0))
        # Same result as the absolute detector when handed that threshold.
        assert detect_waves(field, relative_frac=0.5) == detect_waves(field, v_jam_thresh=thr)

    def test_relative_mode_matches_absolute_on_planted_free_flow_field(self):
        # 0.5 × p90 of a 30 m/s field is 15 m/s: the planted 5 m/s band is
        # found either way, at the same speed.
        assert detect_waves(_planted_field(), relative_frac=0.5).waves[0].speed_ms == pytest.approx(
            detect_waves(_planted_field()).waves[0].speed_ms, abs=1e-9
        )

    def test_rejects_bad_fraction_and_empty_field(self):
        with pytest.raises(ValueError, match="relative_frac"):
            detect_waves(_planted_field(), relative_frac=1.0)
        t_edges, x_edges = _grid()
        empty = SpeedField(
            t_edges=t_edges,
            x_edges=x_edges,
            mean_speed=np.full((len(t_edges) - 1, len(x_edges) - 1), np.nan),
        )
        assert detect_waves(empty, relative_frac=0.5).count == 0
