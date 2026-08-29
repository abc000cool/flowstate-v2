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
