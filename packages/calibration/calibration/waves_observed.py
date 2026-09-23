"""Observed backward wave speed from a fixed-detector archive.

The validation report scores the *simulated* backward wave-front speed against
the empirical 14–22 km/h band (CLAUDE.md §7.1,
:data:`flowstate_core.constants.WAVE_SPEED_BAND_KMH`). A detector archive at
30-s resolution and sub-kilometre station spacing supports the *observed*
counterpart of that number for the corridor being modelled, so a reviewer can
put the corridor's own recurrent wave speed next to the band the model is
asked to land in. It is **context, never a criterion**: a corridor whose real
waves run at 13 km/h is not a corridor whose model has failed, and this module
never returns a pass or a fail.

Method (one pair of adjacent stations at a time)
------------------------------------------------
A stop-and-go wave travels upstream, so it reaches the **downstream** station
of a pair first and the **upstream** station ``Δx / w`` seconds later. For the
pair ``(upstream at x_u, downstream at x_d > x_u)``:

1. **Congested episodes.** The downstream speed series marks a sample jammed
   when it is below ``v_thresh_ms``; a run of ≥ :data:`MIN_EVENT_SAMPLES`
   consecutive jammed samples is one *event*. A pair with fewer than
   ``min_events`` events is rejected — a single morning's congestion is an
   anecdote, not a wave-speed measurement.
2. **Analysed samples.** The jam indicator dilated by the maximum searched lag
   on both sides. The informative part of the signal is the free-flow → jam
   transition and its upstream echo, and both have to be inside the analysed
   set for the correlation to see them.
3. **Detrending.** Each series has a centred moving mean of width
   ``detrend_s`` subtracted from it. What is left is the oscillation; what is
   removed is the slow envelope of the congestion — the onset, the plateau
   and the recovery, which every station of a corridor shares and which
   therefore correlates strongly at *any* lag. Without this step the peak
   times the growth of the queue rather than the passage of a jam wave, which
   is a different quantity (and on the MnDOT corridor it lands three pairs on
   a zero or negative lag, because the whole corridor's morning peak turns on
   almost together). ``detrend_s = 0`` disables the step.
4. **Normalised cross-correlation.** For every lag ``k`` in
   ``[−max_lag, +max_lag]`` bins, the Pearson correlation of the detrended
   ``v_down(t)`` against the detrended ``v_up(t + k)`` over the analysed
   samples at which both are finite — the normalised cross-correlation for
   series with gaps (a detector-day the archive never reported is NaN and
   simply contributes no pair).
5. **Peak.** The lag maximising the correlation, refined to sub-bin resolution
   by a parabola through the peak and its two neighbours (the offset is
   clamped to ±½ bin, and is zero unless the three points are concave). The
   implied wave speed is ``Δx / lag``, positive by construction because only a
   positive lag — the disturbance arriving upstream *later* — is accepted.

A pair is used only when its peak correlation is at least
:data:`MIN_PEAK_CORRELATION`, the peak lag is strictly positive, and the peak
does not sit on the search bound (a peak at ``±max_lag`` means the true lag is
outside the searched range, or that there is no peak at all). Every rejection
is reported with its reason; nothing is silently dropped.

The corridor summary is the **median** of the per-pair speeds with its
interquartile range — a median because a single pair straddling a bottleneck
or a lane drop can produce a lag that is a queue-growth rate rather than a
wave speed, and the per-pair table is printed so that such a pair is visible.

Resolution
----------
With 30-s bins the lag is measured to ±½ bin before refinement, so a 0.7 km
pair resolves a wave speed to roughly ±10% and a 0.5 km pair to ±15%. That is
the honest precision of the method on archive data; it is why the summary
reports an IQR over pairs rather than a single number, and why the per-pair
sample counts are kept.

Lineage: timing a moving jam by cross-correlating adjacent loop-detector
series is the standard freeway-oscillation measurement — Mauch & Cassidy
(2002), *Freeway traffic oscillations: observations and predictions*; Zielke,
Bertini & Treiber (2008), *Empirical measurement of freeway oscillation
characteristics*, TRR 2088:57–67; Coifman (2002), *Estimating travel times and
vehicle trajectories on freeway traffic loop detectors*, Transportation
Research Part A 36:351–364, for what a single loop can and cannot resolve.
The recipe above is stated in full here rather than cited by equation number:
no step of it rests on a source this repository has not read.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from flowstate_core.constants import V_JAM_THRESH, WAVE_SPEED_BAND_KMH
from flowstate_core.units import ms_to_kmh

FloatArray = NDArray[np.float64]

METHOD: Final[str] = (
    "normalised cross-correlation of adjacent-station 30-s speed series, "
    "detrended by a centred moving mean, over the downstream station's "
    "congested episodes; lag of the correlation peak, parabolically refined, "
    "divided into the station spacing"
)
"""One-line description of the recipe, recorded in every artifact."""

MIN_PEAK_CORRELATION: Final[float] = 0.3
"""Peak Pearson correlation below which a station pair is not used."""

MIN_PAIRED_SAMPLES: Final[int] = 10
"""Fewest paired samples a lag needs before its correlation is defined."""

MIN_EVENT_SAMPLES: Final[int] = 2
"""Consecutive jammed samples that make a congested episode (60 s at 30 s)."""

DEFAULT_MAX_LAG_S: Final[float] = 900.0
"""Widest lag searched [s]; 0.9 km at 3.6 km/h, slower than any moving jam."""

DEFAULT_DETREND_S: Final[float] = 1200.0
"""Width of the moving mean removed from each series [s].

Longer than a stop-and-go oscillation period (2–15 min in the freeway
literature), so the oscillation survives, and short enough to follow the
envelope of a morning peak, so the shared onset and recovery do not.
"""

MIN_DETREND_COVERAGE: Final[float] = 0.25
"""Share of a detrending window that must be measured for its mean to exist."""

_REASON_FEW_EVENTS: Final[str] = "fewer than min_events congested episodes downstream"
_REASON_NO_PEAK: Final[str] = "no lag had enough paired samples for a correlation"
_REASON_WEAK: Final[str] = "peak correlation below the acceptance floor"
_REASON_BOUND: Final[str] = "peak lag sits on the search bound"
_REASON_NOT_BACKWARD: Final[str] = "peak lag is not positive (no backward propagation)"
_REASON_SPACING: Final[str] = "stations share a position"


@dataclass(frozen=True)
class WavePairEstimate:
    """One adjacent-station pair's wave-speed estimate.

    Attributes:
        upstream: Station id at the smaller position — where the wave arrives
            second.
        downstream: Station id at the larger position — where it arrives
            first.
        dx_m: Spacing ``x_downstream − x_upstream`` [m].
        lag_s: Refined lag of the correlation peak [s]; NaN when none was
            found.
        lag_bins: Integer lag of the peak [bins]; 0 when none was found.
        speed_kmh: Implied backward wave speed ``dx / lag`` [km/h], positive
            for an upstream-propagating disturbance; NaN when unused.
        correlation: Peak Pearson correlation; NaN when no lag had enough
            paired samples.
        n_samples: Paired samples the peak correlation rests on.
        n_events: Congested episodes at the downstream station.
        used: Whether the pair entered the corridor summary.
        reason: Why it did not, when it did not; empty otherwise.
    """

    upstream: str
    downstream: str
    dx_m: float
    lag_s: float
    lag_bins: int
    speed_kmh: float
    correlation: float
    n_samples: int
    n_events: int
    used: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON form (NaN written as ``None``)."""
        return {
            "upstream": self.upstream,
            "downstream": self.downstream,
            "dx_m": _json_number(self.dx_m),
            "lag_s": _json_number(self.lag_s),
            "lag_bins": int(self.lag_bins),
            "speed_kmh": _json_number(self.speed_kmh),
            "correlation": _json_number(self.correlation),
            "n_samples": int(self.n_samples),
            "n_events": int(self.n_events),
            "used": bool(self.used),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ObservedWaveSpeed:
    """A corridor's detector-estimated backward wave speed.

    Attributes:
        pairs: Every adjacent mainline pair examined, in corridor order,
            used or not.
        median_kmh: Median of the used pairs' speeds [km/h]; NaN when none.
        iqr_kmh: ``(q25, q75)`` of the used pairs' speeds [km/h]; NaN when
            none.
        n_pairs: Adjacent pairs examined.
        n_used: Pairs that entered the summary.
        dt_s: Sampling interval of the input series [s].
        v_thresh_ms: Congestion threshold used [m/s].
        max_lag_s: Widest lag searched [s].
        min_events: Congested episodes a pair needed.
        detrend_s: Width of the moving mean removed before correlating [s];
            0 when the step was disabled.
        band_kmh: The model's acceptance band, carried so a consumer can
            print the comparison without re-deriving it.
        method: :data:`METHOD`.
    """

    pairs: tuple[WavePairEstimate, ...]
    median_kmh: float
    iqr_kmh: tuple[float, float]
    n_pairs: int
    n_used: int
    dt_s: float
    v_thresh_ms: float
    max_lag_s: float
    min_events: int
    detrend_s: float = DEFAULT_DETREND_S
    band_kmh: tuple[float, float] = WAVE_SPEED_BAND_KMH
    method: str = METHOD

    @property
    def rejected(self) -> tuple[WavePairEstimate, ...]:
        """The pairs that did not enter the summary."""
        return tuple(p for p in self.pairs if not p.used)

    def rejection_counts(self) -> dict[str, int]:
        """Rejection reason → how many pairs it rejected."""
        counts: dict[str, int] = {}
        for pair in self.rejected:
            counts[pair.reason] = counts.get(pair.reason, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        """JSON form — what an observations artifact carries as context."""
        return {
            "median_kmh": _json_number(self.median_kmh),
            "iqr_kmh": [_json_number(v) for v in self.iqr_kmh],
            "n_pairs": int(self.n_pairs),
            "n_used": int(self.n_used),
            "dt_s": float(self.dt_s),
            "v_thresh_ms": float(self.v_thresh_ms),
            "max_lag_s": float(self.max_lag_s),
            "min_events": int(self.min_events),
            "detrend_s": float(self.detrend_s),
            "min_peak_correlation": MIN_PEAK_CORRELATION,
            "band_kmh": [float(v) for v in self.band_kmh],
            "method": self.method,
            "pairs": [p.to_dict() for p in self.pairs],
            "rejected": self.rejection_counts(),
        }


def detector_wave_speed(
    station_series: Mapping[str, Sequence[float | None]],
    x_m: Mapping[str, float],
    *,
    dt_s: float,
    v_thresh_ms: float = V_JAM_THRESH,
    max_lag_s: float = DEFAULT_MAX_LAG_S,
    min_events: int = 3,
    detrend_s: float = DEFAULT_DETREND_S,
) -> ObservedWaveSpeed:
    """Estimate a corridor's backward wave speed from detector speed series.

    The module docstring states the method and its resolution. The result is
    context for a validation report, never a criterion.

    Args:
        station_series: Station id → speeds [m/s] on a regular ``dt_s`` grid,
            ``None``/NaN where the detector measured nothing. Every series
            must have the same length and share one clock. Several days are
            passed as one series separated by a NaN gap longer than
            ``max_lag_s`` (:func:`calibration.loaders.mndot.station_speed_series`
            builds exactly that), so no correlation pairs samples from
            different days.
        x_m: Station id → corridor position [m], increasing downstream. A
            station missing here, or carrying a non-finite position, takes
            part in no pair.
        dt_s: Sampling interval of the series [s] (30 s for a MnDOT archive).
        v_thresh_ms: A downstream sample below this is jammed [m/s]; the
            default is :data:`flowstate_core.constants.V_JAM_THRESH`
            (40 km/h ≈ 11.1 m/s), the same threshold
            :mod:`validation.waves` uses on the simulated field.
        max_lag_s: Widest lag searched [s].
        min_events: Congested episodes a pair's downstream station needs.
        detrend_s: Width of the moving mean removed from each series before
            the correlation [s]; 0 disables the step (step 3 above).

    Returns:
        The per-pair estimates and the corridor summary. With fewer than two
        positioned stations the summary is empty (no pairs, NaN median) — an
        artifact that defines no spacing is not an error.

    Raises:
        ValueError: Non-positive ``dt_s``, a ``max_lag_s`` shorter than one
            bin, ``min_events`` below one, or series of differing lengths.
    """
    if dt_s <= 0.0:
        raise ValueError(f"dt_s must be > 0, got {dt_s}")
    if max_lag_s < dt_s:
        raise ValueError(f"max_lag_s ({max_lag_s}) must be at least one bin ({dt_s} s)")
    if min_events < 1:
        raise ValueError(f"min_events must be >= 1, got {min_events}")
    if detrend_s < 0.0:
        raise ValueError(f"detrend_s must be >= 0, got {detrend_s}")

    arrays: dict[str, FloatArray] = {}
    lengths: set[int] = set()
    for station, series in station_series.items():
        values = np.asarray([math.nan if v is None else float(v) for v in series], dtype=np.float64)
        arrays[station] = values
        lengths.add(int(values.size))
    if len(lengths) > 1:
        raise ValueError(
            f"station speed series have differing lengths {sorted(lengths)}; they must share "
            "one clock"
        )

    positioned = [
        s for s in arrays if s in x_m and math.isfinite(float(x_m[s])) and arrays[s].size > 0
    ]
    ordered = sorted(positioned, key=lambda s: float(x_m[s]))
    max_lag = math.floor(max_lag_s / dt_s)

    pairs: list[WavePairEstimate] = []
    for upstream, downstream in itertools.pairwise(ordered):
        pairs.append(
            _pair_estimate(
                upstream,
                downstream,
                arrays[upstream],
                arrays[downstream],
                dx_m=float(x_m[downstream]) - float(x_m[upstream]),
                dt_s=dt_s,
                v_thresh_ms=v_thresh_ms,
                max_lag=max_lag,
                min_events=min_events,
                detrend_bins=round(detrend_s / dt_s),
            )
        )

    speeds = np.asarray([p.speed_kmh for p in pairs if p.used], dtype=np.float64)
    if speeds.size:
        median = float(np.median(speeds))
        iqr = (float(np.percentile(speeds, 25.0)), float(np.percentile(speeds, 75.0)))
    else:
        median = math.nan
        iqr = (math.nan, math.nan)
    return ObservedWaveSpeed(
        pairs=tuple(pairs),
        median_kmh=median,
        iqr_kmh=iqr,
        n_pairs=len(pairs),
        n_used=int(speeds.size),
        dt_s=float(dt_s),
        v_thresh_ms=float(v_thresh_ms),
        max_lag_s=float(max_lag * dt_s),
        min_events=int(min_events),
        detrend_s=float(round(detrend_s / dt_s) * dt_s),
    )


def summary_line(result: ObservedWaveSpeed) -> str:
    """The report's one-line rendering of a corridor estimate.

    Args:
        result: A :func:`detector_wave_speed` result (or one rebuilt from an
            artifact's context block).

    Returns:
        ``"median 18.0 km/h (IQR 16.4–19.8) from 7 of 13 station pairs; the
        model's band is 14–22 km/h"``, or a sentence naming the reason when
        no pair survived. Numbers only — the caller supplies the label, so
        that no number in a report is ever free text (CLAUDE.md §7.4).
    """
    lo, hi = result.band_kmh
    band = f"the model's band is {lo:g}–{hi:g} km/h"
    if result.n_used == 0:
        reasons = ", ".join(
            f"{count} {reason}" for reason, count in sorted(result.rejection_counts().items())
        )
        detail = f" ({reasons})" if reasons else ""
        return f"not estimated from {result.n_pairs} station pair(s){detail}; {band}"
    q25, q75 = result.iqr_kmh
    return (
        f"median {result.median_kmh:.1f} km/h (IQR {q25:.1f}–{q75:.1f}) from "
        f"{result.n_used} of {result.n_pairs} station pairs; {band}"
    )


def summary_line_from_context(context: Mapping[str, Any]) -> str:
    """:func:`summary_line` for a context block read back from JSON.

    Args:
        context: The ``detector_wave_speed`` mapping of an observations
            artifact's ``context``.

    Returns:
        The sentence, or ``""`` when the mapping carries no usable summary
        (an artifact written by a different version is not a reason to fail a
        report; the line is simply not printed).
    """
    try:
        n_pairs = int(context["n_pairs"])
        n_used = int(context["n_used"])
        band_raw = context.get("band_kmh") or WAVE_SPEED_BAND_KMH
        band = (float(band_raw[0]), float(band_raw[1]))
        median = _float_or_nan(context.get("median_kmh"))
        iqr_raw = context.get("iqr_kmh") or (None, None)
        iqr = (_float_or_nan(iqr_raw[0]), _float_or_nan(iqr_raw[1]))
        rejected = {str(k): int(v) for k, v in (context.get("rejected") or {}).items()}
    except (KeyError, IndexError, TypeError, ValueError):
        return ""
    if n_used > 0 and not math.isfinite(median):
        return ""
    stub = ObservedWaveSpeed(
        pairs=tuple(
            WavePairEstimate(
                upstream="",
                downstream="",
                dx_m=math.nan,
                lag_s=math.nan,
                lag_bins=0,
                speed_kmh=math.nan,
                correlation=math.nan,
                n_samples=0,
                n_events=0,
                used=False,
                reason=reason,
            )
            for reason, count in sorted(rejected.items())
            for _ in range(count)
        ),
        median_kmh=median,
        iqr_kmh=iqr,
        n_pairs=n_pairs,
        n_used=n_used,
        dt_s=float(context.get("dt_s", math.nan)),
        v_thresh_ms=float(context.get("v_thresh_ms", math.nan)),
        max_lag_s=float(context.get("max_lag_s", math.nan)),
        min_events=int(context.get("min_events", 0)),
        band_kmh=band,
    )
    return summary_line(stub)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _float_or_nan(value: Any) -> float:
    """``None`` → NaN, anything numeric → float."""
    return math.nan if value is None else float(value)


def _json_number(value: float) -> float | None:
    """NaN → ``None`` (JSON has no NaN; the observations contract says null)."""
    return None if not math.isfinite(value) else float(value)


def _pair_estimate(
    upstream: str,
    downstream: str,
    up: FloatArray,
    down: FloatArray,
    *,
    dx_m: float,
    dt_s: float,
    v_thresh_ms: float,
    max_lag: int,
    min_events: int,
    detrend_bins: int,
) -> WavePairEstimate:
    """One pair's estimate (module docstring, steps 1–5)."""

    def rejected(reason: str, **fields: Any) -> WavePairEstimate:
        base: dict[str, Any] = {
            "lag_s": math.nan,
            "lag_bins": 0,
            "speed_kmh": math.nan,
            "correlation": math.nan,
            "n_samples": 0,
            "n_events": 0,
        }
        base.update(fields)
        return WavePairEstimate(
            upstream=upstream, downstream=downstream, dx_m=dx_m, used=False, reason=reason, **base
        )

    if not (dx_m > 0.0):
        return rejected(_REASON_SPACING)
    jam = np.isfinite(down) & (down < v_thresh_ms)
    n_events = _count_events(jam, MIN_EVENT_SAMPLES)
    if n_events < min_events:
        return rejected(_REASON_FEW_EVENTS, n_events=n_events)

    analysed = _dilate(jam, max_lag)
    correlations, counts = _lag_correlations(
        _detrend(down, detrend_bins), _detrend(up, detrend_bins), analysed, max_lag
    )
    finite = np.isfinite(correlations)
    if not bool(finite.any()):
        return rejected(_REASON_NO_PEAK, n_events=n_events)
    index = int(np.nanargmax(correlations))
    peak_lag = index - max_lag
    peak_r = float(correlations[index])
    n_samples = int(counts[index])
    fields: dict[str, Any] = {
        "lag_bins": peak_lag,
        "correlation": peak_r,
        "n_samples": n_samples,
        "n_events": n_events,
    }
    if abs(peak_lag) == max_lag:
        return rejected(_REASON_BOUND, **fields)
    if peak_lag <= 0:
        return rejected(_REASON_NOT_BACKWARD, **fields)
    if peak_r < MIN_PEAK_CORRELATION:
        return rejected(_REASON_WEAK, **fields)
    lag_s = (peak_lag + _sub_bin_offset(correlations, index)) * dt_s
    return WavePairEstimate(
        upstream=upstream,
        downstream=downstream,
        dx_m=dx_m,
        lag_s=lag_s,
        lag_bins=peak_lag,
        speed_kmh=ms_to_kmh(dx_m / lag_s),
        correlation=peak_r,
        n_samples=n_samples,
        n_events=n_events,
        used=True,
    )


def _count_events(jam: NDArray[np.bool_], min_samples: int) -> int:
    """Runs of ``True`` at least ``min_samples`` long."""
    if not bool(jam.any()):
        return 0
    padded = np.concatenate(([False], jam, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    starts, stops = edges[0::2], edges[1::2]
    return int(np.count_nonzero(stops - starts >= min_samples))


def _dilate(mask: NDArray[np.bool_], radius: int) -> NDArray[np.bool_]:
    """``mask`` widened by ``radius`` samples on both sides."""
    if radius <= 0 or not bool(mask.any()):
        return mask.copy()
    out = mask.copy()
    for shift in range(1, radius + 1):
        out[shift:] |= mask[:-shift]
        out[:-shift] |= mask[shift:]
    return out


def _detrend(series: FloatArray, window_bins: int) -> FloatArray:
    """The series minus its centred moving mean over ``window_bins`` samples.

    The mean is taken over the finite samples inside the window only, and is
    undefined — so the residual is NaN — where fewer than
    :data:`MIN_DETREND_COVERAGE` of the window was measured. A window of 0 or
    1 bins is the identity (the step is disabled).

    Args:
        series: Speeds [m/s], NaN where unmeasured.
        window_bins: Width of the moving mean [samples].

    Returns:
        The residual, NaN wherever the series or the local mean is.
    """
    if window_bins <= 1:
        return series.astype(np.float64, copy=True)
    finite = np.isfinite(series)
    values = np.where(finite, series, 0.0)
    cumulative = np.concatenate(([0.0], np.cumsum(values)))
    counts = np.concatenate(([0], np.cumsum(finite.astype(np.int64))))
    n = series.size
    half = window_bins // 2
    index = np.arange(n)
    lo = np.clip(index - half, 0, n)
    hi = np.clip(index + half + 1, 0, n)
    total = cumulative[hi] - cumulative[lo]
    present = counts[hi] - counts[lo]
    enough = present >= max(1, math.ceil(MIN_DETREND_COVERAGE * window_bins))
    with np.errstate(invalid="ignore", divide="ignore"):
        baseline = np.where(enough, total / np.maximum(present, 1), math.nan)
    return series - baseline


def _lag_correlations(
    down: FloatArray,
    up: FloatArray,
    analysed: NDArray[np.bool_],
    max_lag: int,
) -> tuple[FloatArray, NDArray[np.int64]]:
    """Pearson correlation of ``down(t)`` against ``up(t + k)`` per lag.

    Args:
        down: Downstream speeds [m/s], NaN where unmeasured.
        up: Upstream speeds [m/s], NaN where unmeasured.
        analysed: Samples ``t`` the correlation is taken over.
        max_lag: Widest lag in bins.

    Returns:
        ``(correlations, counts)``, both indexed ``k + max_lag`` for
        ``k ∈ [−max_lag, max_lag]``; a lag with fewer than
        :data:`MIN_PAIRED_SAMPLES` pairs, or with no variance on either side,
        is NaN.
    """
    n = down.size
    correlations = np.full(2 * max_lag + 1, math.nan, dtype=np.float64)
    counts = np.zeros(2 * max_lag + 1, dtype=np.int64)
    base = analysed & np.isfinite(down)
    for lag in range(-max_lag, max_lag + 1):
        lo = max(0, -lag)
        hi = min(n, n - lag)
        if hi - lo < MIN_PAIRED_SAMPLES:
            continue
        left = down[lo:hi]
        right = up[lo + lag : hi + lag]
        keep = base[lo:hi] & np.isfinite(right)
        count = int(np.count_nonzero(keep))
        counts[lag + max_lag] = count
        if count < MIN_PAIRED_SAMPLES:
            continue
        a = left[keep]
        b = right[keep]
        a = a - a.mean()
        b = b - b.mean()
        denominator = math.sqrt(float(a @ a) * float(b @ b))
        if denominator <= 0.0:
            continue
        correlations[lag + max_lag] = float(a @ b) / denominator
    return correlations, counts


def _sub_bin_offset(correlations: FloatArray, index: int) -> float:
    """Parabolic sub-bin refinement of a correlation peak [bins].

    Zero unless the peak is interior, both neighbours are finite and the three
    points are concave; clamped to ±½ bin so the refinement can never move the
    peak into a neighbouring bin.
    """
    if index <= 0 or index >= correlations.size - 1:
        return 0.0
    left = float(correlations[index - 1])
    middle = float(correlations[index])
    right = float(correlations[index + 1])
    if not (math.isfinite(left) and math.isfinite(right)):
        return 0.0
    denominator = left - 2.0 * middle + right
    if denominator >= 0.0:
        return 0.0
    offset = 0.5 * (left - right) / denominator
    return max(-0.5, min(0.5, offset))
