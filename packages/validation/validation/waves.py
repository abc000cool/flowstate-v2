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

References:
    Treiber, M. & Kesting, A. (2013). Traffic Flow Dynamics. Springer.
    Sugiyama, Y. et al. (2008). New J. Phys. 10:033001.
    Sen, P. K. (1968). J. Amer. Statist. Assoc. 63:1379-1389 (Theil-Sen).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from scipy.stats import theilslopes

from flowstate_core.constants import V_JAM_THRESH
from validation.fields import SpeedField


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
    the US-101 site's pinned blob) — the stripes inside such a field are
    only visible against the local recovery speed. Relative mode is a
    detection *variant*: results obtained with it must say so and quote the
    fraction, and the CLAUDE.md §7.1 wave-speed criterion remains defined on
    the standard threshold.

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
