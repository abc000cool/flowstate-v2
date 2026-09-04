"""Tests for validation.waves: detection on synthetic planted-wave fields.

``TestDetectorBenchmark`` measures every registered detector on the
planted-stripe benchmark and pins the numbers quoted in the ``waves`` module
docstring; ``TestStack`` pins the slant-stack estimator's behaviour and the
provenance of its contrast floor.
"""

import math
from typing import ClassVar

import numpy as np
import pytest

from flowstate_core.rng import make_rng
from flowstate_core.units import kmh_to_ms
from validation.fields import SpeedField
from validation.waves import (
    RELATIVE_DETECTOR,
    STACK_DETECTOR,
    STACK_MIN_CONTRAST,
    STANDARD_DETECTOR,
    STRIPE_DETECTOR,
    WAVE_DETECTORS,
    WaveDetector,
    detect_waves,
    get_detector,
    planted_stripe_field,
    stack_wave_speed,
)

V_FREE = 30.0
V_JAMMED = 5.0
PLANT_SPEED_MS = -kmh_to_ms(16.0)  # backward wave, exactly -16 km/h
PLANTED_KMH = 16.0


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


# ---------------------------------------------------------------------------
# Planted-stripe benchmark (waves module docstring)
# ---------------------------------------------------------------------------


def _bench(detector: WaveDetector, seed: int, **kw: float) -> SpeedField:
    """Benchmark field binned the way ``detector`` expects."""
    return planted_stripe_field(
        dt_bin=detector.dt_bin_s, dx_bin=detector.dx_bin_m, seed=seed, **kw
    )[0]


def _readings(detector: WaveDetector, seeds: range, **kw: float) -> np.ndarray:
    return np.asarray([detector.measure(_bench(detector, s, **kw)).speed_kmh for s in seeds])


def _summary(vals: np.ndarray) -> tuple[float, float, int]:
    """(mean, sd, n_found) over the finite readings; NaN mean when none."""
    finite = vals[np.isfinite(vals)]
    if finite.size == 0:
        return math.nan, 0.0, 0
    return float(finite.mean()), float(finite.std()), int(finite.size)


class TestPlantedStripeField:
    def test_geometry_matches_truth(self):
        field, truth = planted_stripe_field(congested_fraction=0.9, noise_sigma_ms=0.0)
        # Noise-free: exactly the downstream 90% of the corridor is below 40 km/h.
        assert np.mean(field.mean_speed < kmh_to_ms(40.0)) == pytest.approx(0.9, abs=0.02)
        assert truth.queue_tail_m == pytest.approx(540.0)
        assert truth.amplitude_ms == pytest.approx(kmh_to_ms(25.0))
        # 4.86 km of queue / 1 km spacing -> 4 to 5 stripe bands per row.
        assert 4.5 < truth.n_stripes_mean < 5.5
        assert not np.isnan(field.mean_speed).any()
        assert field.mean_speed.shape == (240, 72)

    def test_stripes_translate_at_the_planted_speed(self):
        # Choose dt so one row advances the stripes by exactly one bin
        # upstream: |w| dt = dx -> dt = 75 / 4.444 = 16.875 s.
        dt = 75.0 / kmh_to_ms(16.0)
        field, truth = planted_stripe_field(
            congested_fraction=1.0, noise_sigma_ms=0.0, dt_bin=dt, t_end=40 * dt
        )
        v = field.mean_speed
        # Row i+1 is row i shifted one bin toward smaller x (interior bins).
        assert np.allclose(v[1:, :-1], v[:-1, 1:])
        assert truth.wave_speed_ms == pytest.approx(-kmh_to_ms(16.0))

    def test_rejects_bad_params(self):
        with pytest.raises(ValueError, match="congested_fraction"):
            planted_stripe_field(congested_fraction=0.0)
        with pytest.raises(ValueError, match="stripe_width_m"):
            planted_stripe_field(stripe_width_m=1000.0, stripe_spacing_m=1000.0)
        with pytest.raises(ValueError, match="noise_sigma_ms"):
            planted_stripe_field(noise_sigma_ms=-1.0)
        with pytest.raises(ValueError, match="> 0"):
            planted_stripe_field(dt_bin=0.0)


class TestDetectorBenchmark:
    """Every registered detector on the planted-stripe benchmark.

    Planted −16 km/h stripes (5 km/h, 300 m wide every 1000 m) inside a
    standing queue whose background is 30 km/h, noise σ = 1.0 m/s, corridor
    5.4 km × 1 h, seeds 0–4; the ``stripe`` detector sees the same scene on
    its own 10 s × 50 m bins. ``EXPECTED`` is the table in the ``waves``
    module docstring, ``(mean, sd)`` of the recovered speed [km/h] over the
    seeds with a reading, or ``None`` when no seed produced one:

    | f    | standard | stripe        | relative      | stack         |
    |------|----------|---------------|---------------|---------------|
    | 0.30 | none     | 15.25 ± 0.58  | none          | 16.05 ± 0.09  |
    | 0.60 | none     | 15.75 ± 1.08  | none          | 16.01 ± 0.01  |
    | 0.90 | none     | 15.99 ± 1.02  | 15.90 ± 0.01  | 16.01 ± 0.00  |
    | 0.95 | none     | 15.96 ± 1.16  | 16.02 ± 0.00  | 16.01 ± 0.00  |
    """

    SEEDS = range(5)
    EXPECTED: ClassVar[dict[float, dict[str, tuple[float, float] | None]]] = {
        0.3: {"standard": None, "stripe": (15.25, 0.58), "relative": None, "stack": (16.05, 0.09)},
        0.6: {"standard": None, "stripe": (15.75, 1.08), "relative": None, "stack": (16.01, 0.01)},
        0.9: {
            "standard": None,
            "stripe": (15.99, 1.02),
            "relative": (15.90, 0.01),
            "stack": (16.01, 0.00),
        },
        0.95: {
            "standard": None,
            "stripe": (15.96, 1.16),
            "relative": (16.02, 0.00),
            "stack": (16.01, 0.00),
        },
    }

    @pytest.mark.parametrize("f", [0.3, 0.6, 0.9, 0.95])
    def test_table_row(self, f):
        for name, detector in WAVE_DETECTORS.items():
            mean, sd, n_found = _summary(_readings(detector, self.SEEDS, congested_fraction=f))
            expected = self.EXPECTED[f][name]
            if expected is None:
                assert n_found == 0, f"{name} at f={f}: found {n_found}, table says none"
            else:
                assert n_found == len(self.SEEDS), f"{name} at f={f}: only {n_found} readings"
                assert mean == pytest.approx(expected[0], abs=0.05), f"{name} at f={f}"
                assert sd == pytest.approx(expected[1], abs=0.05), f"{name} at f={f}"

    @pytest.mark.parametrize("f", [0.3, 0.6, 0.9])
    def test_default_detector_recovers_planted_speed(self, f):
        """The criteria default (``stack``): every seed within 0.5 km/h of the
        planted 16 km/h, the 5-seed mean within 0.1 km/h (0.2 at f = 0.3,
        where the queue holds under two stripes)."""
        vals = _readings(STACK_DETECTOR, self.SEEDS, congested_fraction=f)
        assert np.all(np.isfinite(vals))
        assert np.all(np.abs(vals - PLANTED_KMH) < 0.5)
        assert vals.mean() == pytest.approx(PLANTED_KMH, abs=0.2 if f == 0.3 else 0.1)

    @pytest.mark.parametrize("f", [0.3, 0.6, 0.9])
    def test_standard_detector_finds_nothing_on_a_congested_background(self, f):
        """The failure mode, pinned: the whole queue is one 40 km/h component
        whose upstream front is the standing queue tail — slope 0, so no
        backward front and a NaN criterion value at every congested fraction."""
        for seed in self.SEEDS:
            field = _bench(STANDARD_DETECTOR, seed, congested_fraction=f)
            ws = STANDARD_DETECTOR.detect(field)
            assert ws.count == 1
            assert ws.waves[0].speed_ms == pytest.approx(0.0, abs=0.05)
            assert math.isnan(STANDARD_DETECTOR.measure(field).speed_kmh)

    @pytest.mark.parametrize("f", [0.3, 0.6])
    def test_relative_detector_needs_under_10pct_free_flow(self, f):
        """With ≥ 10% of the field in free flow the p90 reference is the
        free-flow speed, the threshold (45 km/h) sits above the background and
        the relative detector degenerates to the standard one."""
        for seed in self.SEEDS:
            m = RELATIVE_DETECTOR.measure(_bench(RELATIVE_DETECTOR, seed, congested_fraction=f))
            assert m.threshold_kmh > 40.0
            assert math.isnan(m.speed_kmh)

    def test_straddling_background(self):
        """Background 38 km/h, σ = 2.5 m/s, f = 0.9, seeds 0–9 (module
        docstring): the standard detector reads on 1 seed only (10.5 km/h);
        stripe 15.8 ± 1.1, relative 14.9 ± 1.1, stack 16.0 ± 0.0."""
        kw = {"congested_fraction": 0.9, "v_background_ms": kmh_to_ms(38.0), "noise_sigma_ms": 2.5}
        seeds = range(10)
        std = _readings(STANDARD_DETECTOR, seeds, **kw)
        assert int(np.isfinite(std).sum()) == 1
        assert std[np.isfinite(std)][0] == pytest.approx(10.5, abs=0.1)
        for detector, expected in (
            (STRIPE_DETECTOR, (15.8, 1.1)),
            (RELATIVE_DETECTOR, (14.9, 1.1)),
            (STACK_DETECTOR, (16.0, 0.0)),
        ):
            mean, sd, n_found = _summary(_readings(detector, seeds, **kw))
            assert n_found == 10, detector.name
            assert mean == pytest.approx(expected[0], abs=0.1), detector.name
            assert sd == pytest.approx(expected[1], abs=0.1), detector.name

    def test_stripe_detector_collapses_at_its_threshold(self):
        """Background at the stripe detector's own 25 km/h threshold, f = 0.9,
        seeds 0–4: noise bins below 25 km/h fragment the field and the mean
        of backward fronts drops to 11.6 ± 4.0 km/h; the stack still reads
        16.0."""
        kw = {"congested_fraction": 0.9, "v_background_ms": kmh_to_ms(25.0)}
        mean, sd, n_found = _summary(_readings(STRIPE_DETECTOR, self.SEEDS, **kw))
        assert n_found == 5
        assert mean == pytest.approx(11.6, abs=0.1)
        assert sd == pytest.approx(4.0, abs=0.1)
        stack = _readings(STACK_DETECTOR, self.SEEDS, **kw)
        assert np.all(np.abs(stack - PLANTED_KMH) < 0.1)


class TestStack:
    """Slant-stack estimator: provenance of its floors and its limitations."""

    def test_single_wave_in_free_flow(self):
        # The TestPlantedWave fixture (one stripe, 480 s): the stack agrees
        # with the standard detector.
        est = stack_wave_speed(_planted_field())
        assert est.rejected == ""
        assert -est.speed_ms == pytest.approx(kmh_to_ms(PLANTED_KMH), abs=kmh_to_ms(0.3))
        assert est.contrast > STACK_MIN_CONTRAST

    def test_no_wave_fields_are_rejected_and_pin_the_contrast_floor(self):
        """40 seeded fields without moving structure — pure noise at 25 and
        8 m/s (σ 0.3–2 m/s), and a standing queue with a slow drift — never
        exceed a peak/median contrast of 2.02; STACK_MIN_CONTRAST = 3.0 leaves
        a 1.5× margin over that maximum."""
        contrasts = []
        nt, nx = 240, 72
        t_edges = 15.0 * np.arange(nt + 1)
        x_edges = 75.0 * np.arange(nx + 1)
        t_c = 0.5 * (t_edges[:-1] + t_edges[1:])
        x_c = 0.5 * (x_edges[:-1] + x_edges[1:])
        cases = [(25.0, 0.3), (25.0, 2.0), (8.0, 1.0)]
        for base, sigma in cases:
            for seed in range(10):
                speed = base + sigma * make_rng(seed).standard_normal((nt, nx))
                est = stack_wave_speed(SpeedField(t_edges, x_edges, speed))
                contrasts.append(est.contrast)
                assert math.isnan(est.speed_ms) and est.rejected
        for seed in range(10):
            speed = np.where(x_c[None, :] >= 2000.0, 8.0, 25.0) + make_rng(seed).standard_normal(
                (nt, nx)
            )
            speed = speed - 2.0 * (t_c[:, None] / 3600.0)
            est = stack_wave_speed(SpeedField(t_edges, x_edges, speed))
            contrasts.append(est.contrast)
            assert math.isnan(est.speed_ms) and est.rejected
        assert len(contrasts) == 40
        assert max(contrasts) == pytest.approx(2.02, abs=0.02)
        assert STACK_MIN_CONTRAST == pytest.approx(3.0)
        assert STACK_MIN_CONTRAST >= 1.45 * max(contrasts)

    def test_forward_periodic_pattern_rejected_at_range_edge(self):
        """+80 km/h platoons every 1 km: a strictly periodic train sampled at
        15 s aliases onto the −40 km/h edge of the backward search range and
        is rejected there rather than reported."""
        nt, nx = 240, 72
        t_edges = 15.0 * np.arange(nt + 1)
        x_edges = 75.0 * np.arange(nx + 1)
        t_c = 0.5 * (t_edges[:-1] + t_edges[1:])
        x_c = 0.5 * (x_edges[:-1] + x_edges[1:])
        phase = np.mod(x_c[None, :] - kmh_to_ms(80.0) * t_c[:, None], 1000.0)
        speed = np.where(phase < 300.0, kmh_to_ms(70.0), kmh_to_ms(90.0))
        speed = speed + make_rng(1).standard_normal((nt, nx))
        est = stack_wave_speed(SpeedField(t_edges, x_edges, speed))
        assert est.rejected == "peak at search-range edge"
        assert math.isnan(est.speed_ms)
        assert est.peak_speed_ms == pytest.approx(-kmh_to_ms(40.0))

    def test_nan_holes_do_not_bias(self):
        for seed in range(3):
            field, _ = planted_stripe_field(seed=seed)
            holes = make_rng(100 + seed).random(field.mean_speed.shape) < 0.2
            field.mean_speed[holes] = np.nan
            est = stack_wave_speed(field)
            assert -est.speed_ms == pytest.approx(kmh_to_ms(PLANTED_KMH), abs=kmh_to_ms(0.1))

    def test_two_speeds_report_one_of_them_not_a_blend(self):
        nt, nx = 240, 72
        t_edges = 15.0 * np.arange(nt + 1)
        x_edges = 75.0 * np.arange(nx + 1)
        t_c = 0.5 * (t_edges[:-1] + t_edges[1:])
        x_c = 0.5 * (x_edges[:-1] + x_edges[1:])
        seen = set()
        for seed in range(3):
            speed = np.full((nt, nx), kmh_to_ms(30.0))
            for w_kmh, offset in ((-12.0, 0.0), (-20.0, 500.0)):
                phase = np.mod(x_c[None, :] - kmh_to_ms(w_kmh) * t_c[:, None] + offset, 1000.0)
                speed = np.where(phase < 250.0, kmh_to_ms(5.0), speed)
            speed = speed + make_rng(seed).standard_normal((nt, nx))
            est = stack_wave_speed(SpeedField(t_edges, x_edges, speed))
            reading = round(-est.speed_ms / kmh_to_ms(1.0))
            assert reading in (12, 20)
            seen.add(reading)
        assert seen  # one of the two planted speeds, never 16

    def test_growing_queue_tail_dominates_every_detector(self):
        """A queue tail sweeping upstream at −6 km/h with −16 km/h stripes
        inside: the tail's 60 km/h step out-weighs the stripes, so the stack
        reports the shock (6 km/h) — and so does the standard detector."""
        nt, nx = 240, 72
        t_edges = 15.0 * np.arange(nt + 1)
        x_edges = 75.0 * np.arange(nx + 1)
        t_c = 0.5 * (t_edges[:-1] + t_edges[1:])
        x_c = 0.5 * (x_edges[:-1] + x_edges[1:])
        tail = 5400.0 - kmh_to_ms(6.0) * t_c
        in_queue = x_c[None, :] >= tail[:, None]
        phase = np.mod(x_c[None, :] + kmh_to_ms(16.0) * t_c[:, None], 1000.0)
        speed = np.where(in_queue, kmh_to_ms(30.0), kmh_to_ms(90.0))
        speed = np.where((phase < 300.0) & in_queue, kmh_to_ms(5.0), speed)
        speed = speed + make_rng(0).standard_normal((nt, nx))
        field = SpeedField(t_edges, x_edges, speed)
        assert STACK_DETECTOR.measure(field).speed_kmh == pytest.approx(6.0, abs=0.1)
        assert STANDARD_DETECTOR.measure(field).speed_kmh == pytest.approx(6.0, abs=0.1)

    def test_empty_field_and_bad_params(self):
        t_edges, x_edges = _grid()
        empty = SpeedField(t_edges, x_edges, np.full((len(t_edges) - 1, len(x_edges) - 1), np.nan))
        assert stack_wave_speed(empty).rejected == "empty field"
        assert math.isnan(STACK_DETECTOR.measure(empty).speed_kmh)
        field = _planted_field()
        with pytest.raises(ValueError, match="step_ms"):
            stack_wave_speed(field, step_ms=0.0)
        with pytest.raises(ValueError, match="speed_range_ms"):
            stack_wave_speed(field, speed_range_ms=(-1.0, -2.0))
        with pytest.raises(ValueError, match="min_contrast"):
            stack_wave_speed(field, min_contrast=0.5)
        with pytest.raises(ValueError, match="three candidates"):
            stack_wave_speed(field, speed_range_ms=(-2.0, -1.0), step_ms=5.0)


class TestWaveDetector:
    def test_registry_names_and_lookup(self):
        assert set(WAVE_DETECTORS) == {"standard", "stripe", "relative", "stack"}
        for name, d in WAVE_DETECTORS.items():
            assert d.name == name
            assert name in d.describe()
        assert get_detector("stripe") is STRIPE_DETECTOR
        with pytest.raises(KeyError, match="standard"):
            get_detector("no_such_detector")

    def test_standard_recipe_matches_detect_waves_defaults(self):
        field = _planted_field()
        assert STANDARD_DETECTOR.detect(field) == detect_waves(field)
        m = STANDARD_DETECTOR.measure(field)
        assert m.speed_kmh == pytest.approx(PLANTED_KMH, abs=0.7 * 3.6)
        assert m.n_backward == 1 and m.n_components == 1
        assert m.threshold_kmh == pytest.approx(40.0)
        assert math.isnan(m.contrast)
        assert m.in_band_fraction() == 1.0

    def test_relative_recipe_reports_its_threshold(self):
        field = _slow_striped_field()
        m = RELATIVE_DETECTOR.measure(field)
        assert m.speed_kmh == pytest.approx(PLANTED_KMH, abs=0.7 * 3.6)
        finite = field.mean_speed[~np.isnan(field.mean_speed)]
        assert m.threshold_kmh == pytest.approx(0.5 * np.percentile(finite, 90.0) * 3.6)
        assert RELATIVE_DETECTOR.detect(field) == detect_waves(field, relative_frac=0.5)

    def test_stack_recipe_has_no_waveset(self):
        with pytest.raises(ValueError, match="stack"):
            STACK_DETECTOR.detect(_planted_field())
        m = STACK_DETECTOR.measure(_planted_field())
        assert m.n_components == 0 and math.isnan(m.threshold_kmh)
        assert m.contrast > STACK_MIN_CONTRAST and m.note == ""
        assert m.backward_speeds_kmh == (m.speed_kmh,)

    def test_bins_must_match_the_recipe(self):
        field = _planted_field()  # 15 s x 75 m
        assert STANDARD_DETECTOR.bins_match(field)
        assert not STRIPE_DETECTOR.bins_match(field)
        with pytest.raises(ValueError, match="do not match detector 'stripe'"):
            STRIPE_DETECTOR.measure(field)
        with pytest.raises(ValueError, match="do not match"):
            STRIPE_DETECTOR.detect(field)

    def test_describe_carries_every_parameter(self):
        assert STANDARD_DETECTOR.describe() == (
            "standard: jam = v < 40 km/h on 15 s x 75 m bins, 8-connected components >= 4 "
            "bins, mean magnitude of backward Theil-Sen front speeds"
        )
        assert "25 km/h on 10 s x 50 m bins" in STRIPE_DETECTOR.describe()
        assert "0.5 x p90" in RELATIVE_DETECTOR.describe()
        s = STACK_DETECTOR.describe()
        assert "[-40, -2] km/h" in s and "0.25 km/h steps" in s and "contrast >= 3" in s

    def test_invalid_recipes_rejected(self):
        with pytest.raises(ValueError, match="relative_frac"):
            WaveDetector(name="r", method="relative")
        with pytest.raises(ValueError, match="v_jam_thresh_ms"):
            WaveDetector(name="t", method="threshold", v_jam_thresh_ms=0.0)
        with pytest.raises(ValueError, match="unknown method"):
            WaveDetector(name="x", method="ridge")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="bins"):
            WaveDetector(name="b", method="threshold", dt_bin_s=0.0)
        with pytest.raises(ValueError, match="name"):
            WaveDetector(name=" ", method="threshold")
