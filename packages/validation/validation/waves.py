"""Stop-and-go wave detection on space-time speed fields (CLAUDE.md §7.2).

Pipeline: threshold the binned mean-speed field at ``v_jam_thresh`` to get a
jam mask, label its connected components (8-connectivity, so diagonally
propagating fronts stay one component), extract the upstream front of each
component (the minimum-``x`` jammed bin per time row — the back of the queue,
which grows upstream as vehicles join the jam), and fit a robust Theil-Sen
line ``x_front(t)`` whose slope is the wave propagation speed. A negative
slope means the front moves toward smaller ``x``, i.e. upstream/backward in
road coordinates — the signature of stop-and-go waves, empirically near
−15 to −20 km/h (Treiber & Kesting 2013, ch. 18).

Detector recipes
================

The wave-speed number a criterion row carries depends on *which* detector
produced it, so every detector is a named, fully parametrized
:class:`WaveDetector` in :data:`WAVE_DETECTORS`:

* ``standard`` — absolute threshold 40 km/h on 15 s × 75 m bins (CLAUDE.md
  §7.2 defaults); the detector behind ``validation.metrics.compute_metrics``
  and every artifact produced before this registry existed.
* ``stripe`` — absolute threshold 25 km/h on 10 s × 50 m bins (the M3 and
  I-24 "stripe" variant, ``scripts/i24_validate.py``).
* ``relative`` — threshold ``0.5 × p90`` of the field's non-empty bins on
  15 s × 75 m bins (ROADMAP D1).
* ``stack`` — no threshold: :func:`stack_wave_speed` projects the two-way
  demeaned field along candidate front speeds (a slant-stack / Radon-type
  projection) and returns the speed at which the moving structure lines up.

Planted-stripe benchmark
========================

:func:`planted_stripe_field` builds a field with a periodic train of
backward-moving stripes of known speed inside a standing queue that occupies
the downstream ``congested_fraction`` of the corridor (free flow upstream of
the queue tail). The tables below are measured by
``tests/test_validation/test_validation_waves.py::TestDetectorBenchmark`` —
planted −16 km/h, stripes 5 km/h and 300 m wide every 1000 m, corridor
5.4 km × 1 h; "recovered" is the mean ± sd over seeds 0–4 of each detector's
criterion statistic (mean magnitude of backward fronts; the stack's peak
speed), "found" the number of seeds with a reading.

Deep congestion (background 30 km/h, noise σ = 1.0 m/s; values in km/h):

| congested fraction | standard (40 km/h) | stripe (25 km/h, 10 s × 50 m) | relative (0.5 × p90) | stack |
|---|---|---|---|---|
| 0.30 | none found (0/5) | 15.25 ± 0.58 | none found (0/5) | 16.05 ± 0.09 |
| 0.60 | none found (0/5) | 15.75 ± 1.08 | none found (0/5) | 16.01 ± 0.01 |
| 0.90 | none found (0/5) | 15.99 ± 1.02 | 15.90 ± 0.01 | 16.01 ± 0.00 |
| 0.95 | none found (0/5) | 15.96 ± 1.16 | 16.02 ± 0.00 | 16.01 ± 0.00 |

Background straddling the 40 km/h threshold (38 km/h, σ = 2.5 m/s, congested
fraction 0.9, seeds 0–9; ``TestDetectorBenchmark.test_straddling_background``):

| standard | stripe | relative | stack |
|---|---|---|---|
| 10.5 km/h on the 1/10 seeds with a backward front | 15.8 ± 1.1 km/h (10/10) | 14.9 ± 1.1 km/h (10/10) | 16.0 ± 0.0 km/h (10/10) |

Reading: the ``standard`` detector cannot measure a wave speed on a congested
background — every bin of the queue is below 40 km/h, so the queue is one
component whose upstream front is the standing queue tail (slope 0, not
backward), and when the background straddles the threshold the few fragments
it does find read far below the planted speed. ``relative`` only works once
the free-flow share of the field is under 10% (its p90 reference is the
free-flow speed otherwise). ``stripe`` is unbiased at a 30–35 km/h background
and collapses when the background sits at its own 25 km/h threshold
(``TestDetectorBenchmark.test_stripe_detector_collapses_at_its_threshold``).
``stack`` is unbiased across congested fractions and backgrounds on this
benchmark; its limitations are in :func:`stack_wave_speed`. It is the
default ``wave_detector`` of every ``validation.criteria`` profile.

References:
    Treiber, M. & Kesting, A. (2013). Traffic Flow Dynamics. Springer.
    Treiber, M. & Helbing, D. (2002). Reconstructing the spatio-temporal
        traffic dynamics from stationary detector data. Cooperative
        Transportation Dynamics 1:3.1-3.24 (smoothing along the congested
        characteristic direction).
    Sugiyama, Y. et al. (2008). New J. Phys. 10:033001.
    Sen, P. K. (1968). J. Amer. Statist. Assoc. 63:1379-1389 (Theil-Sen).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage
from scipy.stats import theilslopes

from flowstate_core.constants import V_JAM_THRESH, WAVE_SPEED_BAND_KMH
from flowstate_core.rng import make_rng
from flowstate_core.units import kmh_to_ms, ms_to_kmh
from validation.fields import SpeedField

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class Wave:
    """One detected stop-and-go wave.

    Attributes:
        speed_ms: Front propagation speed [m/s]; negative = backward
            (upstream-propagating in road coordinates).
        amplitude_ms: Free speed minus the minimum speed inside the jammed
            region [m/s].
        duration_s: Time span covered by the jammed component [s].
        extent_m: Spatial span covered by the jammed component [m].
        n_bins: Number of jammed bins in the component.
    """

    speed_ms: float
    amplitude_ms: float
    duration_s: float
    extent_m: float
    n_bins: int


@dataclass(frozen=True)
class WaveSet:
    """All waves detected in one speed field (docs/CONTRACTS.md §4)."""

    waves: tuple[Wave, ...]

    @property
    def count(self) -> int:
        """Number of detected waves."""
        return len(self.waves)

    def backward(self) -> tuple[Wave, ...]:
        """Waves whose front propagates upstream (``speed_ms < 0``)."""
        return tuple(w for w in self.waves if w.speed_ms < 0)


RELATIVE_REFERENCE_PERCENTILE: float = 90.0
"""Percentile of the non-empty bin speeds used as the reference speed in
relative-threshold mode (the field's recovery speed between stripes)."""


def relative_jam_threshold(field: SpeedField, relative_frac: float) -> float:
    """Jam threshold [m/s] for relative mode: ``relative_frac`` × the field's
    :data:`RELATIVE_REFERENCE_PERCENTILE` speed over non-empty bins."""
    if not 0.0 < relative_frac < 1.0:
        raise ValueError(f"relative_frac must be in (0, 1), got {relative_frac}")
    finite = field.mean_speed[~np.isnan(field.mean_speed)]
    if finite.size == 0:
        return float("nan")
    return float(relative_frac * np.percentile(finite, RELATIVE_REFERENCE_PERCENTILE))


def detect_waves(
    field: SpeedField,
    v_jam_thresh: float = V_JAM_THRESH,
    min_area_bins: int = 4,
    v_free: float | None = None,
    relative_frac: float | None = None,
) -> WaveSet:
    """Detect stop-and-go waves in a binned speed field.

    A bin is jammed when its mean speed is below ``v_jam_thresh`` (NaN bins
    — no vehicles — are never jammed). Jammed bins are grouped by
    8-connected component labeling (``scipy.ndimage.label`` with a full 3×3
    structuring element). For each component the upstream front is the
    minimum-``x`` jammed bin center per time row; a Theil-Sen robust line
    fit of front position against time gives the wave speed. Components
    smaller than ``min_area_bins`` bins, or spanning fewer than two time
    rows (no propagation to measure), are ignored.

    **Relative mode** (``relative_frac`` given) replaces the absolute
    threshold by ``relative_frac × p90`` of the field's non-empty bin speeds
    (:func:`relative_jam_threshold`). The absolute 40 km/h threshold labels
    an entire heavily congested field as one jam and finds no fronts
    (docs/WAVE_SPEED_DIAGNOSIS.md: zero fronts on a ring at 80–100 veh/km,
    the US-101 site's pinned blob; the planted-stripe benchmark in the
    module docstring) — the stripes inside such a field are only visible
    against the local recovery speed. Relative mode is a detection
    *variant*: results obtained with it must say so and quote the fraction,
    and the CLAUDE.md §7.1 wave-speed criterion is defined on the detector
    named by the criteria profile (``CriteriaProfile.wave_detector``).

    Args:
        field: Binned mean-speed field from :func:`validation.fields.speed_field`.
        v_jam_thresh: Jam speed threshold [m/s]; defaults to
            ``flowstate_core.constants.V_JAM_THRESH`` (40 km/h in SI).
            Ignored when ``relative_frac`` is given.
        min_area_bins: Minimum component size in bins; smaller blobs are
            treated as noise and skipped.
        v_free: Free speed used for the amplitude ``v_free − min(v_in_jam)``
            [m/s]. ``None`` (default) estimates it as the mean of all
            non-jammed, non-empty bins; if every bin is jammed, the field
            maximum is used.
        relative_frac: Fraction in (0, 1) of the field's p90 speed to use as
            the jam threshold (relative mode); ``None`` keeps the absolute
            threshold.

    Returns:
        A :class:`WaveSet`; ``count == 0`` when no component qualifies.

    Raises:
        ValueError: If ``min_area_bins < 1``, ``v_jam_thresh <= 0`` or
            ``relative_frac`` is outside (0, 1).
    """
    if min_area_bins < 1:
        raise ValueError(f"min_area_bins must be >= 1, got {min_area_bins}")
    if v_jam_thresh <= 0:
        raise ValueError(f"v_jam_thresh must be > 0, got {v_jam_thresh}")
    if relative_frac is not None:
        v_jam_thresh = relative_jam_threshold(field, relative_frac)
        if not np.isfinite(v_jam_thresh):
            return WaveSet(waves=())

    speed = field.mean_speed
    finite = ~np.isnan(speed)
    jam = np.zeros(speed.shape, dtype=bool)
    jam[finite] = speed[finite] < v_jam_thresh

    if v_free is None:
        free_bins = speed[finite & ~jam]
        if free_bins.size:
            v_free = float(free_bins.mean())
        elif finite.any():
            v_free = float(np.nanmax(speed))
        else:
            return WaveSet(waves=())

    structure = np.ones((3, 3), dtype=int)  # 8-connectivity
    labeled, n_components = ndimage.label(jam, structure=structure)
    labeled = np.asarray(labeled)
    n_components = int(n_components)

    t_centers = 0.5 * (field.t_edges[:-1] + field.t_edges[1:])
    x_centers = 0.5 * (field.x_edges[:-1] + field.x_edges[1:])

    waves: list[Wave] = []
    for lab in range(1, n_components + 1):
        mask = labeled == lab
        n_bins = int(mask.sum())
        if n_bins < min_area_bins:
            continue
        rows = np.flatnonzero(mask.any(axis=1))
        if len(rows) < 2:
            continue  # cannot measure propagation from a single time row
        front_t = t_centers[rows]
        front_x = np.asarray(
            [x_centers[int(np.flatnonzero(mask[r]).min())] for r in rows], dtype=np.float64
        )
        slope = float(theilslopes(front_x, front_t)[0])
        v_min = float(speed[mask].min())
        cols = np.flatnonzero(mask.any(axis=0))
        waves.append(
            Wave(
                speed_ms=slope,
                amplitude_ms=float(v_free) - v_min,
                duration_s=float(field.t_edges[rows.max() + 1] - field.t_edges[rows.min()]),
                extent_m=float(field.x_edges[cols.max() + 1] - field.x_edges[cols.min()]),
                n_bins=n_bins,
            )
        )
    return WaveSet(waves=tuple(waves))


# --------------------------------------------------------------------------
# Slant-stack (Radon-type) wave-speed estimate
# --------------------------------------------------------------------------

STACK_SPEED_RANGE_MS: tuple[float, float] = (-kmh_to_ms(40.0), -kmh_to_ms(2.0))
"""Default candidate front-speed range [m/s] searched by :func:`stack_wave_speed`
(backward −40 … −2 km/h; the empirical band is 14–22 km/h)."""

STACK_SPEED_STEP_MS: float = kmh_to_ms(0.25)
"""Default candidate spacing [m/s] (0.25 km/h; the peak is refined
parabolically between neighbours)."""

STACK_MIN_CONTRAST: float = 3.0
"""Peak/median contrast below which :func:`stack_wave_speed` reports no
wave. On 40 seeded no-wave fields (pure noise at 25 and 8 m/s, a standing
queue with a slow drift) the contrast never exceeds 2.02
(``tests/test_validation/test_validation_waves.py::TestStack``); 3.0 leaves a
1.5× margin over that maximum. Planted stripes on a 30% congested corridor
reach 7 and higher (``TestDetectorBenchmark``)."""


@dataclass(frozen=True)
class StackEstimate:
    """Result of :func:`stack_wave_speed`.

    Attributes:
        speed_ms: Dominant front propagation speed [m/s], negative =
            backward; ``NaN`` when the estimate was rejected.
        peak_speed_ms: Candidate speed at the statistic's maximum [m/s]
            before rejection rules (diagnostic).
        contrast: Peak statistic divided by the median over candidates.
        rejected: Empty when accepted; otherwise the reason (``"empty
            field"``, ``"peak at search-range edge"``, ``"contrast below
            floor"``).
    """

    speed_ms: float
    peak_speed_ms: float
    contrast: float
    rejected: str = ""


def _two_way_demeaned(speed: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Residual after removing per-x (time-mean) and per-t (space-mean)
    structure, plus a 0/1 weight mask; NaN bins get residual 0 and weight 0."""
    finite = np.isfinite(speed)
    w = finite.astype(np.float64)
    v = np.where(finite, speed, 0.0)
    col_n = w.sum(axis=0)
    row_n = w.sum(axis=1)
    col_mean = np.zeros_like(col_n)
    row_mean = np.zeros_like(row_n)
    np.divide(v.sum(axis=0), col_n, out=col_mean, where=col_n > 0)
    np.divide(v.sum(axis=1), row_n, out=row_mean, where=row_n > 0)
    grand = float(v.sum() / w.sum()) if w.sum() > 0 else 0.0
    residual = np.where(finite, v - col_mean[None, :] - row_mean[:, None] + grand, 0.0)
    return np.asarray(residual, dtype=np.float64), w


def stack_wave_speed(
    field: SpeedField,
    speed_range_ms: tuple[float, float] = STACK_SPEED_RANGE_MS,
    step_ms: float = STACK_SPEED_STEP_MS,
    min_contrast: float = STACK_MIN_CONTRAST,
) -> StackEstimate:
    """Dominant front propagation speed by slant-stacking the speed field.

    The field is two-way demeaned (per-``x`` time means and per-``t`` space
    means removed, so a standing queue, a bottleneck profile or a slow drift
    contribute nothing) and, for every candidate speed ``c``, projected onto
    the moving coordinate ``ξ = x − c·t`` binned at the field's ``dx``. The
    stack statistic is the weighted second moment of the projected mean
    profile, ``Σ n_k m_k² / Σ n_k`` — largest when the moving structure lines
    up along ``c``, i.e. when ``c`` is the speed at which the stripes travel
    (the slant-stack / Radon projection; the same alignment idea as smoothing
    along the congested characteristic in Treiber & Helbing 2002). The
    maximum over candidates is refined by a parabola through its neighbours.

    Because nothing is thresholded, the estimate does not depend on where the
    background sits relative to 40 km/h: on the planted-stripe benchmark
    (module docstring) it reads 16.0–16.1 km/h for a planted 16 km/h at
    congested fractions 0.3–0.95 and backgrounds 25–38 km/h. Documented
    limitations (each pinned in ``TestStack``):

    * It returns **one** speed — the dominant moving structure. When stripes
      of two speeds coexist it reports one of them, not a blend; when a
      growing queue's tail sweeps the field its (larger-amplitude) shock
      wins over the stripes inside, as it does for every detector here.
    * A strictly periodic train sampled at ``dt`` has aliases at
      ``c − (spacing/dt)·(m/n)``; a peak on either edge of the search range
      is rejected for that reason (a forward-moving periodic pattern aliases
      onto the range edge).
    * A field without moving structure has a flat statistic; the estimate is
      rejected when peak/median falls below ``min_contrast``
      (:data:`STACK_MIN_CONTRAST`).

    Args:
        field: Binned mean-speed field (uniform bins).
        speed_range_ms: Inclusive candidate range of front speeds [m/s]
            (negative = backward).
        step_ms: Candidate spacing [m/s].
        min_contrast: Minimum peak/median contrast to accept the estimate.

    Returns:
        A :class:`StackEstimate`; ``speed_ms`` is NaN when rejected.

    Raises:
        ValueError: If ``step_ms <= 0``, the range is empty or has fewer than
            three candidates, or ``min_contrast < 1``.
    """
    lo, hi = speed_range_ms
    if step_ms <= 0:
        raise ValueError(f"step_ms must be > 0, got {step_ms}")
    if not hi > lo:
        raise ValueError(f"speed_range_ms must be (lo, hi) with hi > lo, got {speed_range_ms}")
    if min_contrast < 1.0:
        raise ValueError(f"min_contrast must be >= 1, got {min_contrast}")
    candidates = np.arange(lo, hi + 0.5 * step_ms, step_ms, dtype=np.float64)
    if len(candidates) < 3:
        raise ValueError("speed range must contain at least three candidates")

    speed = np.asarray(field.mean_speed, dtype=np.float64)
    finite = np.isfinite(speed)
    if speed.shape[0] < 2 or not finite.any():
        return StackEstimate(math.nan, math.nan, math.nan, rejected="empty field")
    residual, weights = _two_way_demeaned(speed)
    t_c = 0.5 * (field.t_edges[:-1] + field.t_edges[1:])
    x_c = 0.5 * (field.x_edges[:-1] + field.x_edges[1:])
    dx = float(field.x_edges[1] - field.x_edges[0])
    r_flat = residual.ravel()
    w_flat = weights.ravel()
    total_w = float(w_flat.sum())

    stats = np.empty(len(candidates), dtype=np.float64)
    for i, c in enumerate(candidates):
        xi = x_c[None, :] - c * t_c[:, None]
        k = np.floor((xi - xi.min()) / dx).astype(np.intp).ravel()
        sums = np.bincount(k, weights=r_flat)
        counts = np.bincount(k, weights=w_flat)
        means = np.zeros_like(sums)
        np.divide(sums, counts, out=means, where=counts > 0)
        stats[i] = float(np.sum(counts * means * means) / total_w)

    i_peak = int(np.argmax(stats))
    median = float(np.median(stats))
    contrast = float(stats[i_peak] / median) if median > 0 else math.inf
    peak = float(candidates[i_peak])
    if i_peak == 0 or i_peak == len(candidates) - 1:
        return StackEstimate(math.nan, peak, contrast, rejected="peak at search-range edge")
    if contrast < min_contrast:
        return StackEstimate(math.nan, peak, contrast, rejected="contrast below floor")
    y0, y1, y2 = stats[i_peak - 1], stats[i_peak], stats[i_peak + 1]
    denom = y0 - 2.0 * y1 + y2
    offset = 0.5 * (y0 - y2) / denom if denom != 0.0 else 0.0
    offset = min(max(offset, -0.5), 0.5)
    return StackEstimate(peak + offset * step_ms, peak, contrast)


# --------------------------------------------------------------------------
# Named detector recipes
# --------------------------------------------------------------------------

WaveMethod = Literal["threshold", "relative", "stack"]


@dataclass(frozen=True)
class WaveMeasurement:
    """One detector's reading of one field — the wave-speed criterion input.

    Attributes:
        detector: Name of the :class:`WaveDetector` that produced it.
        speed_kmh: Criterion statistic [km/h, positive = backward]: the mean
            magnitude of backward fronts (threshold/relative methods) or the
            stack peak speed; ``NaN`` when nothing backward was found.
        backward_speeds_kmh: Per-front magnitudes [km/h] (threshold/relative)
            or the single stack speed; empty when nothing was found.
        n_components: Labeled jam components (threshold/relative); 0 for
            the stack method, which does not segment.
        threshold_kmh: Jam threshold applied [km/h]; ``NaN`` for the stack.
        contrast: Stack peak/median contrast; ``NaN`` for threshold methods.
        note: Rejection reason (stack) or empty.
    """

    detector: str
    speed_kmh: float
    backward_speeds_kmh: tuple[float, ...]
    n_components: int
    threshold_kmh: float
    contrast: float
    note: str = ""

    @property
    def n_backward(self) -> int:
        """Number of backward readings behind ``speed_kmh``."""
        return len(self.backward_speeds_kmh)

    def in_band_fraction(self, band_kmh: tuple[float, float] = WAVE_SPEED_BAND_KMH) -> float:
        """Fraction of ``backward_speeds_kmh`` inside ``band_kmh``; NaN if none."""
        if not self.backward_speeds_kmh:
            return math.nan
        lo, hi = band_kmh
        return float(np.mean([lo <= v <= hi for v in self.backward_speeds_kmh]))


@dataclass(frozen=True)
class WaveDetector:
    """A named, fully parametrized wave-detection recipe.

    Binning is part of the recipe (the ``stripe`` variant differs from
    ``standard`` in its bins as much as in its threshold), so
    :meth:`measure` refuses a field whose bins do not match — a reading can
    then never be mislabeled with another recipe's name.

    Attributes:
        name: Registry key (``WAVE_DETECTORS``), recorded in criteria rows.
        method: ``"threshold"`` (absolute ``v_jam_thresh_ms``), ``"relative"``
            (``relative_frac × p90``) or ``"stack"`` (:func:`stack_wave_speed`).
        dt_bin_s: Time bin width the field must have [s].
        dx_bin_m: Space bin width the field must have [m].
        v_jam_thresh_ms: Absolute jam threshold [m/s] (``threshold`` method).
        relative_frac: Fraction of p90 (``relative`` method).
        min_area_bins: Component-size floor for the segmenting methods.
        stack_speed_range_ms: Candidate front-speed range [m/s] (``stack``).
        stack_step_ms: Candidate spacing [m/s] (``stack``).
        stack_min_contrast: Contrast floor (``stack``).
    """

    name: str
    method: WaveMethod
    dt_bin_s: float = 15.0
    dx_bin_m: float = 75.0
    v_jam_thresh_ms: float = V_JAM_THRESH
    relative_frac: float | None = None
    min_area_bins: int = 4
    stack_speed_range_ms: tuple[float, float] = STACK_SPEED_RANGE_MS
    stack_step_ms: float = STACK_SPEED_STEP_MS
    stack_min_contrast: float = STACK_MIN_CONTRAST

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("detector name must be non-empty")
        if self.dt_bin_s <= 0 or self.dx_bin_m <= 0:
            raise ValueError(f"bins must be > 0, got {self.dt_bin_s} s x {self.dx_bin_m} m")
        if self.method == "relative":
            if self.relative_frac is None or not 0.0 < self.relative_frac < 1.0:
                raise ValueError("relative method needs relative_frac in (0, 1)")
        elif self.method == "threshold":
            if self.v_jam_thresh_ms <= 0:
                raise ValueError(f"v_jam_thresh_ms must be > 0, got {self.v_jam_thresh_ms}")
        elif self.method != "stack":
            raise ValueError(f"unknown method {self.method!r}")

    def bins_match(self, field: SpeedField) -> bool:
        """Whether ``field`` was binned with this recipe's ``dt`` and ``dx``."""
        dt = float(field.t_edges[1] - field.t_edges[0])
        dx = float(field.x_edges[1] - field.x_edges[0])
        return math.isclose(dt, self.dt_bin_s, rel_tol=1e-6) and math.isclose(
            dx, self.dx_bin_m, rel_tol=1e-6
        )

    def detect(self, field: SpeedField) -> WaveSet:
        """Run the segmenting detector (threshold/relative methods).

        Raises:
            ValueError: For the ``stack`` method (no segmentation) or a
                field whose bins do not match the recipe.
        """
        self._check_bins(field)
        if self.method == "stack":
            raise ValueError(f"detector {self.name!r} (stack) produces no WaveSet; use measure()")
        if self.method == "relative":
            return detect_waves(
                field, min_area_bins=self.min_area_bins, relative_frac=self.relative_frac
            )
        return detect_waves(
            field, v_jam_thresh=self.v_jam_thresh_ms, min_area_bins=self.min_area_bins
        )

    def measure(self, field: SpeedField) -> WaveMeasurement:
        """Criterion reading of ``field`` with this recipe.

        Raises:
            ValueError: If the field's bins do not match the recipe.
        """
        self._check_bins(field)
        if self.method == "stack":
            est = stack_wave_speed(
                field,
                speed_range_ms=self.stack_speed_range_ms,
                step_ms=self.stack_step_ms,
                min_contrast=self.stack_min_contrast,
            )
            speed = ms_to_kmh(-est.speed_ms) if math.isfinite(est.speed_ms) else math.nan
            return WaveMeasurement(
                detector=self.name,
                speed_kmh=speed,
                backward_speeds_kmh=(speed,) if math.isfinite(speed) else (),
                n_components=0,
                threshold_kmh=math.nan,
                contrast=est.contrast,
                note=est.rejected,
            )
        ws = self.detect(field)
        backward = tuple(ms_to_kmh(-w.speed_ms) for w in ws.backward())
        threshold = (
            relative_jam_threshold(field, self.relative_frac)
            if self.method == "relative" and self.relative_frac is not None
            else self.v_jam_thresh_ms
        )
        return WaveMeasurement(
            detector=self.name,
            speed_kmh=float(np.mean(backward)) if backward else math.nan,
            backward_speeds_kmh=backward,
            n_components=ws.count,
            threshold_kmh=ms_to_kmh(threshold) if math.isfinite(threshold) else math.nan,
            contrast=math.nan,
        )

    def describe(self) -> str:
        """One-line description with every parameter, for criteria rows."""
        bins = f"{self.dt_bin_s:g} s x {self.dx_bin_m:g} m bins"
        if self.method == "stack":
            lo, hi = self.stack_speed_range_ms
            return (
                f"{self.name}: slant-stack peak of the two-way demeaned field on {bins} over "
                f"front speeds [{ms_to_kmh(lo):g}, {ms_to_kmh(hi):g}] km/h in "
                f"{ms_to_kmh(self.stack_step_ms):g} km/h steps, peak/median contrast >= "
                f"{self.stack_min_contrast:g}, edge peaks rejected"
            )
        if self.method == "relative":
            jam = (
                f"v < {self.relative_frac:g} x p{RELATIVE_REFERENCE_PERCENTILE:g} of "
                "non-empty bin speeds"
            )
        else:
            jam = f"v < {ms_to_kmh(self.v_jam_thresh_ms):g} km/h"
        return (
            f"{self.name}: jam = {jam} on {bins}, 8-connected components >= "
            f"{self.min_area_bins} bins, mean magnitude of backward Theil-Sen front speeds"
        )

    def _check_bins(self, field: SpeedField) -> None:
        if not self.bins_match(field):
            dt = float(field.t_edges[1] - field.t_edges[0])
            dx = float(field.x_edges[1] - field.x_edges[0])
            raise ValueError(
                f"field bins {dt:g} s x {dx:g} m do not match detector {self.name!r} "
                f"({self.dt_bin_s:g} s x {self.dx_bin_m:g} m)"
            )


STANDARD_DETECTOR = WaveDetector(name="standard", method="threshold")
STRIPE_DETECTOR = WaveDetector(
    name="stripe", method="threshold", dt_bin_s=10.0, dx_bin_m=50.0, v_jam_thresh_ms=kmh_to_ms(25.0)
)
RELATIVE_DETECTOR = WaveDetector(name="relative", method="relative", relative_frac=0.5)
STACK_DETECTOR = WaveDetector(name="stack", method="stack")

WAVE_DETECTORS: dict[str, WaveDetector] = {
    d.name: d for d in (STANDARD_DETECTOR, STRIPE_DETECTOR, RELATIVE_DETECTOR, STACK_DETECTOR)
}
"""Registered detector recipes by name (module docstring for what each is)."""


def get_detector(name: str) -> WaveDetector:
    """Look up a registered :class:`WaveDetector`.

    Raises:
        KeyError: Unknown name; the message lists the available detectors.
    """
    try:
        return WAVE_DETECTORS[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown wave detector {name!r}; available: {sorted(WAVE_DETECTORS)}"
        ) from exc


# --------------------------------------------------------------------------
# Planted-stripe benchmark field
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlantedStripes:
    """Ground truth of a :func:`planted_stripe_field`.

    Attributes:
        wave_speed_ms: Planted front speed [m/s], negative = backward.
        congested_fraction: Fraction of the corridor length inside the queue.
        queue_tail_m: Upstream end of the queue [m]; free flow below it.
        v_background_ms: Queue speed between stripes [m/s].
        v_stripe_ms: Speed inside a stripe [m/s].
        v_free_ms: Free-flow speed upstream of the queue [m/s].
        stripe_width_m: Stripe width [m].
        stripe_spacing_m: Distance between consecutive stripe leading edges [m].
        noise_sigma_ms: Gaussian noise added to every bin [m/s].
        n_stripes_mean: Mean number of stripe bands per time row.
    """

    wave_speed_ms: float
    congested_fraction: float
    queue_tail_m: float
    v_background_ms: float
    v_stripe_ms: float
    v_free_ms: float
    stripe_width_m: float
    stripe_spacing_m: float
    noise_sigma_ms: float
    n_stripes_mean: float

    @property
    def amplitude_ms(self) -> float:
        """Background minus stripe speed [m/s]."""
        return self.v_background_ms - self.v_stripe_ms


def planted_stripe_field(
    *,
    wave_speed_ms: float = -kmh_to_ms(16.0),
    congested_fraction: float = 0.9,
    v_background_ms: float = kmh_to_ms(30.0),
    v_stripe_ms: float = kmh_to_ms(5.0),
    v_free_ms: float = kmh_to_ms(90.0),
    stripe_width_m: float = 300.0,
    stripe_spacing_m: float = 1000.0,
    noise_sigma_ms: float = 1.0,
    dt_bin: float = 15.0,
    dx_bin: float = 75.0,
    t_end: float = 3600.0,
    x_end: float = 5400.0,
    seed: int = 0,
) -> tuple[SpeedField, PlantedStripes]:
    """Synthetic field with backward-moving stripes on a congested background.

    A standing queue occupies the downstream ``congested_fraction`` of the
    corridor (``x >= (1 − f)·x_end``); upstream of its tail the field is free
    flow at ``v_free_ms``. Inside the queue the speed is ``v_background_ms``
    except in a periodic train of stripes — bands of width ``stripe_width_m``
    every ``stripe_spacing_m`` — that travel at ``wave_speed_ms`` and hold
    ``v_stripe_ms``; stripes dissolve at the queue tail. Seeded Gaussian
    noise of ``noise_sigma_ms`` is added to every bin and speeds are clipped
    at zero. Bin centres are evaluated analytically, so the field has no
    empty bins.

    Args:
        wave_speed_ms: Stripe propagation speed [m/s], negative = backward.
        congested_fraction: Fraction of the corridor length in the queue,
            in (0, 1].
        v_background_ms: Queue speed between stripes [m/s]; below 40 km/h
            makes the queue one 40 km/h component.
        v_stripe_ms: Speed inside stripes [m/s] (< ``v_background_ms``).
        v_free_ms: Free-flow speed upstream of the queue [m/s].
        stripe_width_m: Stripe width [m] (< ``stripe_spacing_m``).
        stripe_spacing_m: Stripe period along ``x`` [m].
        noise_sigma_ms: Noise standard deviation [m/s].
        dt_bin: Time bin width [s].
        dx_bin: Space bin width [m].
        t_end: Field duration [s].
        x_end: Corridor length [m].
        seed: RNG seed for the noise.

    Returns:
        The field and its :class:`PlantedStripes` ground truth.

    Raises:
        ValueError: On a non-positive bin or extent, a congested fraction
            outside (0, 1], or a stripe not narrower than its spacing.
    """
    if dt_bin <= 0 or dx_bin <= 0 or t_end <= 0 or x_end <= 0:
        raise ValueError("bins and extents must be > 0")
    if not 0.0 < congested_fraction <= 1.0:
        raise ValueError(f"congested_fraction must be in (0, 1], got {congested_fraction}")
    if not 0.0 < stripe_width_m < stripe_spacing_m:
        raise ValueError("need 0 < stripe_width_m < stripe_spacing_m")
    if noise_sigma_ms < 0:
        raise ValueError(f"noise_sigma_ms must be >= 0, got {noise_sigma_ms}")
    nt = max(1, round(t_end / dt_bin))
    nx = max(1, round(x_end / dx_bin))
    t_edges = np.asarray(dt_bin * np.arange(nt + 1, dtype=np.float64))
    x_edges = np.asarray(dx_bin * np.arange(nx + 1, dtype=np.float64))
    t_c = 0.5 * (t_edges[:-1] + t_edges[1:])
    x_c = 0.5 * (x_edges[:-1] + x_edges[1:])
    queue_tail = (1.0 - congested_fraction) * x_end
    in_queue = x_c[None, :] >= queue_tail
    phase = np.mod(x_c[None, :] - wave_speed_ms * t_c[:, None], stripe_spacing_m)
    stripe = (phase < stripe_width_m) & in_queue
    speed = np.where(in_queue, v_background_ms, v_free_ms)
    speed = np.where(stripe, v_stripe_ms, speed)
    noise = noise_sigma_ms * make_rng(seed).standard_normal(speed.shape)
    speed = np.maximum(speed + noise, 0.0)
    # Stripe bands per row: count leading edges (stripe bins whose upstream
    # neighbour is not a stripe bin), averaged over rows.
    edges = stripe & ~np.concatenate([np.zeros((nt, 1), dtype=bool), stripe[:, :-1]], axis=1)
    truth = PlantedStripes(
        wave_speed_ms=wave_speed_ms,
        congested_fraction=congested_fraction,
        queue_tail_m=queue_tail,
        v_background_ms=v_background_ms,
        v_stripe_ms=v_stripe_ms,
        v_free_ms=v_free_ms,
        stripe_width_m=stripe_width_m,
        stripe_spacing_m=stripe_spacing_m,
        noise_sigma_ms=noise_sigma_ms,
        n_stripes_mean=float(edges.sum(axis=1).mean()),
    )
    field = SpeedField(
        t_edges=t_edges, x_edges=x_edges, mean_speed=np.asarray(speed, dtype=np.float64)
    )
    return field, truth
