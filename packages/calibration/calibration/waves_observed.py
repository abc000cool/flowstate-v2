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
   consecutive jammed samples is one *event*. Only the samples the detrending
   of step 4 leaves defined are counted: an episode sitting wholly inside a
   half-window at the end of a date contributes no paired sample at any lag,
   so it is not evidence this pair has. A pair with fewer than
   ``min_events`` events is rejected. ``min_events`` counts **runs, not
   days**: several episodes of one morning satisfy it, and on a
   multi-date series it says nothing about how many dates contributed. How
   much the answer rests on any one date is measured separately, by
   :func:`leave_one_date_out`.
2. **Date separators.** Dates concatenated into one series are separated by a
   run of ``gap_s`` seconds of NaN (:func:`calibration.loaders.mndot.station_speed_series`
   writes exactly that). Any run of at least ``gap_bins`` samples unmeasured
   at *both* stations is a **barrier**: no correlation pair and no detrending
   window may cross one, so a lag longer than the separator can never align
   one day against the next. With ``gap_s = 0`` the series is taken as one
   continuous record and there are no barriers.
3. **Analysed samples.** The jam indicator dilated by the maximum searched lag
   on both sides. The informative part of the signal is the free-flow → jam
   transition and its upstream echo, and both have to be inside the analysed
   set for the correlation to see them.
4. **Detrending.** Each series has a centred moving mean of width
   ``detrend_s`` subtracted from it. What is left is the oscillation; what is
   removed is the slow envelope of the congestion — the onset, the plateau
   and the recovery, which every station of a corridor shares and which
   therefore correlates strongly at *any* lag. Without this step the peak
   times the growth of the queue rather than the passage of a jam wave, which
   is a different quantity (and on the MnDOT corridor it lands three pairs on
   a zero or negative lag, because the whole corridor's morning peak turns on
   almost together). The residual is defined only where the window is
   genuinely **two-sided** — wholly inside the series and free of barriers —
   because a truncated window's mean is a one-sided mean, and subtracting one
   leaves the local trend in the residual instead of removing it.
   ``detrend_s = 0`` disables the step.
5. **Normalised cross-correlation.** For every lag ``k`` in
   ``[−max_lag, +max_lag]`` bins, the Pearson correlation of the detrended
   ``v_down(t)`` against the detrended ``v_up(t + k)`` over the analysed
   samples at which both are finite and no barrier separates them — the
   normalised cross-correlation for series with gaps (a detector-day the
   archive never reported is NaN and simply contributes no pair).
6. **Peak.** The lag maximising the correlation, refined to sub-bin resolution
   by a parabola through the peak and its two neighbours (the offset is
   clamped to ±½ bin, and is zero unless the three points are concave). The
   implied wave speed is ``Δx / lag``, positive by construction because only a
   positive lag — the disturbance arriving upstream *later* — is accepted.

A pair is used only when its peak lag is strictly positive (tested first, so a
peak at ``−max_lag`` is reported as the forward-moving thing it is rather than
as a search-bound artefact), does not sit on the search bound (a peak at
``max_lag`` means the true lag is outside the searched range, or that there is
no peak at all), carries a correlation of at least
:data:`MIN_PEAK_CORRELATION`, and is — *after* the sub-bin refinement, since
that is the lag the speed is divided by — at least
:data:`MIN_PEAK_LAG_BINS` bins. Every rejection is
reported with its reason; nothing is silently dropped.

The corridor summary is the **median** of the per-pair speeds with its
interquartile range — a median because a single pair straddling a bottleneck
or a lane drop can produce a lag that is a queue-growth rate rather than a
wave speed, and the per-pair table is printed so that such a pair is visible.
A median over a handful of pairs moves when one date is taken away, so
:func:`leave_one_date_out` re-estimates the corridor once per omitted date and
reports the range of medians and the fewest pairs any subset kept; both belong
beside the headline number wherever it is quoted.

Resolution
----------
With 30-s bins the lag is measured to ±½ bin before refinement, so a 0.7 km
pair resolves a wave speed to roughly ±10% and a 0.5 km pair to ±15% **at a
lag of three bins or more**; at two bins the same ±½ bin is ±25%, and at one
bin the half-bin clamp alone spans a factor of three. That is why lags below
:data:`MIN_PEAK_LAG_BINS` are rejected rather than reported. It is the honest
precision of the method on archive data; it is why the summary reports an IQR
over pairs rather than a single number, and why the per-pair sample counts are
kept.

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

MIN_PEAK_LAG_BINS: Final[int] = 2
"""Shortest lag a pair may be used at [bins], after sub-bin refinement.

The floor is applied to the refined lag, the one the reported speed is
divided by, and not only to the integer bin of the peak: a peak at two bins
whose parabola pulls it to 1.5 bins is as far below the grid's resolution as
a one-bin peak is.

A one-bin peak is refined by an offset the parabola clamps to ±½ bin, so its
implied speed is anywhere in ``[Δx/(1.5·Δt), Δx/(0.5·Δt)]`` — a factor of
three, and the upper end is not a wave speed but the grid's Nyquist limit
wearing one. Two bins is the shortest lag at which the method says anything;
the ±10–15% resolution quoted in the module docstring is reached at three.
"""

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
_REASON_SHORT_LAG: Final[str] = (
    f"lag below resolution (refined peak lag under {MIN_PEAK_LAG_BINS} bins)"
)
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
class LeaveOneDateOut:
    """How far the corridor median moves when one date is taken away.

    A median over six surviving pairs, each of which is itself a correlation
    over a handful of mornings, is not a number to quote alone: one date whose
    congestion is unusually sharp can carry a pair over the correlation floor
    and, with it, move the median. Re-estimating the corridor once per omitted
    date says how much of the headline is that date's doing. It is a
    sensitivity, not a confidence interval — the subsets share most of their
    data — and it is reported as the plain range of what was computed.

    Attributes:
        dates: The omitted date labels, in the order they were given.
        medians_kmh: The corridor median with that date left out [km/h]; NaN
            where the remaining dates yielded no usable pair.
        n_used: Pairs the corridor kept with that date left out.
    """

    dates: tuple[str, ...]
    medians_kmh: tuple[float, ...]
    n_used: tuple[int, ...]

    @property
    def median_min_kmh(self) -> float:
        """Smallest leave-one-out median [km/h]; NaN when none is finite."""
        finite = [v for v in self.medians_kmh if math.isfinite(v)]
        return min(finite) if finite else math.nan

    @property
    def median_max_kmh(self) -> float:
        """Largest leave-one-out median [km/h]; NaN when none is finite."""
        finite = [v for v in self.medians_kmh if math.isfinite(v)]
        return max(finite) if finite else math.nan

    @property
    def pairs_min(self) -> int:
        """Fewest pairs any subset kept; 0 when there is no subset."""
        return min(self.n_used) if self.n_used else 0

    def to_dict(self) -> dict[str, Any]:
        """JSON form (NaN written as ``None``)."""
        return {
            "n_dates": len(self.dates),
            "loo_median_min_kmh": _json_number(self.median_min_kmh),
            "loo_median_max_kmh": _json_number(self.median_max_kmh),
            "loo_pairs_min": int(self.pairs_min),
            "by_omitted_date": [
                {"date": date, "median_kmh": _json_number(median), "n_used": int(used)}
                for date, median, used in zip(
                    self.dates, self.medians_kmh, self.n_used, strict=True
                )
            ],
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
        min_events: Congested episodes (runs, not days) a pair needed.
        detrend_s: Width of the moving mean removed before correlating [s];
            0 when the step was disabled.
        gap_s: Width of the all-NaN separator between concatenated dates [s];
            0 when the series was taken as one continuous record.
        band_kmh: The model's acceptance band, carried so a consumer can
            print the comparison without re-deriving it.
        method: :data:`METHOD`.
        loo: The leave-one-date-out sensitivity when it was computed
            (:func:`leave_one_date_out`), else None.
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
    gap_s: float = 0.0
    band_kmh: tuple[float, float] = WAVE_SPEED_BAND_KMH
    method: str = METHOD
    loo: LeaveOneDateOut | None = None

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
        """JSON form — what an observations artifact carries as context.

        The three leave-one-date-out numbers are written at the top level
        (``loo_median_min_kmh``, ``loo_median_max_kmh``, ``loo_pairs_min``)
        as well as inside ``leave_one_date_out``, so a consumer that prints
        the headline beside its sensitivity needs no nested lookup. They are
        absent — not null — when no sensitivity was computed.
        """
        payload: dict[str, Any] = {
            "median_kmh": _json_number(self.median_kmh),
            "iqr_kmh": [_json_number(v) for v in self.iqr_kmh],
            "n_pairs": int(self.n_pairs),
            "n_used": int(self.n_used),
            "dt_s": float(self.dt_s),
            "v_thresh_ms": float(self.v_thresh_ms),
            "max_lag_s": float(self.max_lag_s),
            "min_events": int(self.min_events),
            "detrend_s": float(self.detrend_s),
            "gap_s": float(self.gap_s),
            "min_peak_correlation": MIN_PEAK_CORRELATION,
            "min_peak_lag_bins": MIN_PEAK_LAG_BINS,
            "band_kmh": [float(v) for v in self.band_kmh],
            "method": self.method,
            "pairs": [p.to_dict() for p in self.pairs],
            "rejected": self.rejection_counts(),
        }
        if self.loo is not None:
            loo = self.loo.to_dict()
            payload["loo_median_min_kmh"] = loo["loo_median_min_kmh"]
            payload["loo_median_max_kmh"] = loo["loo_median_max_kmh"]
            payload["loo_pairs_min"] = loo["loo_pairs_min"]
            payload["leave_one_date_out"] = loo
        return payload


def detector_wave_speed(
    station_series: Mapping[str, Sequence[float | None]],
    x_m: Mapping[str, float],
    *,
    dt_s: float,
    v_thresh_ms: float = V_JAM_THRESH,
    max_lag_s: float = DEFAULT_MAX_LAG_S,
    min_events: int = 3,
    detrend_s: float = DEFAULT_DETREND_S,
    gap_s: float = 0.0,
    loo: LeaveOneDateOut | None = None,
) -> ObservedWaveSpeed:
    """Estimate a corridor's backward wave speed from detector speed series.

    The module docstring states the method and its resolution. The result is
    context for a validation report, never a criterion.

    Args:
        station_series: Station id → speeds [m/s] on a regular ``dt_s`` grid,
            ``None``/NaN where the detector measured nothing. Every series
            must have the same length and share one clock. Several days are
            passed as one series separated by a NaN gap of ``gap_s``
            (:func:`calibration.loaders.mndot.station_speed_series` builds
            exactly that, and :func:`concatenate_dates` builds it from
            per-date pieces); state that width in ``gap_s`` and no
            correlation pairs samples from different days.
        x_m: Station id → corridor position [m], increasing downstream. A
            station missing here, or carrying a non-finite position, takes
            part in no pair.
        dt_s: Sampling interval of the series [s] (30 s for a MnDOT archive).
        v_thresh_ms: A downstream sample below this is jammed [m/s]; the
            default is :data:`flowstate_core.constants.V_JAM_THRESH`
            (40 km/h ≈ 11.1 m/s), the same threshold
            :mod:`validation.waves` uses on the simulated field.
        max_lag_s: Widest lag searched [s].
        min_events: Congested episodes (runs of jammed samples, not days) a
            pair's downstream station needs.
        detrend_s: Width of the moving mean removed from each series before
            the correlation [s]; 0 disables the step (step 4 above).
        gap_s: Width of the all-NaN separator between concatenated dates [s].
            A run of at least this many samples unmeasured at both stations is
            a barrier no correlation pair and no detrending window may cross
            (step 2 above). 0 — the default — means the series is one
            continuous record; pass the separator's real width whenever it is
            not, because ``max_lag_s`` alone does not stop a long lag from
            aligning one day against the next.
        loo: A leave-one-date-out sensitivity (:func:`leave_one_date_out`) to
            carry on the result; computed by the caller, since only the
            caller holds the per-date series.

    Returns:
        The per-pair estimates and the corridor summary. With fewer than two
        positioned stations the summary is empty (no pairs, NaN median) — an
        artifact that defines no spacing is not an error.

    Raises:
        ValueError: Non-positive ``dt_s``, a ``max_lag_s`` shorter than one
            bin, ``min_events`` below one, a negative ``detrend_s``, a
            ``gap_s`` that is negative or narrower than one bin (:func:`_gap_bins`),
            or series of differing lengths.
    """
    if dt_s <= 0.0:
        raise ValueError(f"dt_s must be > 0, got {dt_s}")
    if max_lag_s < dt_s:
        raise ValueError(f"max_lag_s ({max_lag_s}) must be at least one bin ({dt_s} s)")
    if min_events < 1:
        raise ValueError(f"min_events must be >= 1, got {min_events}")
    if detrend_s < 0.0:
        raise ValueError(f"detrend_s must be >= 0, got {detrend_s}")
    gap_bins = _gap_bins(gap_s, dt_s)

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
                gap_bins=gap_bins,
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
        gap_s=float(gap_bins * dt_s),
        loo=loo,
    )


def concatenate_dates(
    by_date: Mapping[str, Mapping[str, Sequence[float | None]]], *, gap_bins: int
) -> dict[str, list[float | None]]:
    """Join per-date station series into one series per station.

    The dates are laid end to end in the order given, separated by ``gap_bins``
    samples of ``None`` — the separator :func:`detector_wave_speed` is told
    about through ``gap_s`` and treats as a barrier.

    Args:
        by_date: Date label → (station id → that date's speeds [m/s]). Every
            station of one date must carry the same number of samples; a
            station absent from a date is filled with that date's length of
            ``None``.
        gap_bins: Samples of ``None`` between two dates.

    Returns:
        Station id → the concatenated series. The station order is the order
        the stations first appear.

    Raises:
        ValueError: A date whose stations have differing lengths, or a
            negative ``gap_bins``.
    """
    if gap_bins < 0:
        raise ValueError(f"gap_bins must be >= 0, got {gap_bins}")
    stations = list(dict.fromkeys(station for day in by_date.values() for station in day))
    out: dict[str, list[float | None]] = {station: [] for station in stations}
    for index, (date, day) in enumerate(by_date.items()):
        lengths = {len(values) for values in day.values()}
        if len(lengths) > 1:
            raise ValueError(
                f"date {date!r} has station series of differing lengths {sorted(lengths)}; "
                "they must share one clock"
            )
        length = lengths.pop() if lengths else 0
        for station in stations:
            if index:
                out[station].extend([None] * gap_bins)
            values = day.get(station)
            out[station].extend([None] * length if values is None else list(values))
    return out


def leave_one_date_out(
    by_date: Mapping[str, Mapping[str, Sequence[float | None]]],
    x_m: Mapping[str, float],
    *,
    dt_s: float,
    v_thresh_ms: float = V_JAM_THRESH,
    max_lag_s: float = DEFAULT_MAX_LAG_S,
    min_events: int = 3,
    detrend_s: float = DEFAULT_DETREND_S,
    gap_s: float = 0.0,
) -> LeaveOneDateOut:
    """Re-estimate the corridor once per omitted date.

    The headline median is a median over the pairs that survived, and the
    pairs that survive depend on which mornings are in the archive. Leaving
    each date out in turn and re-running the whole estimate — pair rejection
    included — says how much the answer rests on any one of them
    (:class:`LeaveOneDateOut`).

    Args:
        by_date: Date label → (station id → that date's 30-s speeds [m/s]),
            in the order the dates are concatenated. The same mapping
            :func:`concatenate_dates` takes.
        x_m: Station id → corridor position [m], increasing downstream.
        dt_s: Sampling interval [s].
        v_thresh_ms: Congestion threshold [m/s].
        max_lag_s: Widest lag searched [s].
        min_events: Congested episodes a pair needs.
        detrend_s: Width of the moving mean removed [s].
        gap_s: Width of the separator between two dates [s].

    Returns:
        One median and one used-pair count per omitted date.

    Raises:
        ValueError: Fewer than two dates — a single date cannot be left out,
            and reporting a range over one subset would be a claim about a
            sensitivity that was never measured — a non-positive ``dt_s``, or
            a ``gap_s`` narrower than one bin (:func:`_gap_bins`); the checks
            :func:`detector_wave_speed` makes are made here too, because the
            series are concatenated before it ever sees them.
    """
    if len(by_date) < 2:
        raise ValueError(f"leave-one-date-out needs at least two dates, got {len(by_date)}")
    if dt_s <= 0.0:
        raise ValueError(f"dt_s must be > 0, got {dt_s}")
    gap_bins = _gap_bins(gap_s, dt_s)
    dates = tuple(by_date)
    medians: list[float] = []
    used: list[int] = []
    for omitted in dates:
        kept = {date: day for date, day in by_date.items() if date != omitted}
        result = detector_wave_speed(
            concatenate_dates(kept, gap_bins=gap_bins),
            x_m,
            dt_s=dt_s,
            v_thresh_ms=v_thresh_ms,
            max_lag_s=max_lag_s,
            min_events=min_events,
            detrend_s=detrend_s,
            gap_s=gap_s,
        )
        medians.append(result.median_kmh)
        used.append(result.n_used)
    return LeaveOneDateOut(dates=dates, medians_kmh=tuple(medians), n_used=tuple(used))


def summary_line(result: ObservedWaveSpeed) -> str:
    """The report's one-line rendering of a corridor estimate.

    Args:
        result: A :func:`detector_wave_speed` result (or one rebuilt from an
            artifact's context block).

    Returns:
        ``"median 18.0 km/h (IQR 16.4–19.8) from 7 of 13 station pairs;
        leave-one-date-out 17.2–18.6 km/h over 9 dates (fewest 5 pairs); the
        model's band is 14–22 km/h"``, or a sentence naming the reason when
        no pair survived. The leave-one-date-out clause is present only when
        the estimate carries one. Numbers only — the caller supplies the
        label, so that no number in a report is ever free text
        (CLAUDE.md §7.4).
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
        f"{result.n_used} of {result.n_pairs} station pairs; {loo_clause(result.loo)}{band}"
    )


def loo_clause(loo: LeaveOneDateOut | None) -> str:
    """The leave-one-date-out clause of a summary line, or ``""``.

    Args:
        loo: The sensitivity, or None when none was computed.

    Returns:
        ``"leave-one-date-out 17.2–18.6 km/h over 9 dates (fewest 5 pairs); "``
        — trailing separator included so a caller can concatenate it — and
        ``""`` when there is nothing to report or no subset produced a median.
    """
    if loo is None or not math.isfinite(loo.median_min_kmh):
        return ""
    return (
        f"leave-one-date-out {loo.median_min_kmh:.1f}–{loo.median_max_kmh:.1f} km/h over "
        f"{len(loo.dates)} dates (fewest {loo.pairs_min} pairs); "
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _json_number(value: float) -> float | None:
    """NaN → ``None`` (JSON has no NaN; the observations contract says null)."""
    return None if not math.isfinite(value) else float(value)


def _gap_bins(gap_s: float, dt_s: float) -> int:
    """A date separator's width in whole samples.

    A separator narrower than one bin is refused rather than rounded: a
    ``gap_s`` under half a bin rounds to zero, which silently means *no
    barrier at all* — correlations and detrending windows would then run
    across the join between two mornings, and the result would record
    ``gap_s: 0.0`` as if the caller had declared one continuous record. This
    is the same refusal ``max_lag_s < dt_s`` already makes.

    Args:
        gap_s: Declared separator width [s]; 0 means one continuous record.
        dt_s: Sampling interval [s].

    Returns:
        ``round(gap_s / dt_s)``, and 0 exactly when ``gap_s`` is 0.

    Raises:
        ValueError: A negative ``gap_s``, or one between 0 and one bin.
    """
    if gap_s < 0.0:
        raise ValueError(f"gap_s must be >= 0, got {gap_s}")
    if 0.0 < gap_s < dt_s:
        raise ValueError(
            f"gap_s ({gap_s} s) must be 0 or at least one bin ({dt_s} s): a narrower "
            "separator would round to no barrier, and the series would be correlated "
            "across the join between two dates"
        )
    return round(gap_s / dt_s)


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
    gap_bins: int,
) -> WavePairEstimate:
    """One pair's estimate (module docstring, steps 1–6)."""

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
    barrier = _barriers(up, down, gap_bins)
    down_residual = _detrend(down, detrend_bins, barrier)
    jam = np.isfinite(down) & (down < v_thresh_ms)
    # The episodes that count are the ones the correlation can use. Detrending
    # leaves a half-window undefined at each end of each date, so an episode
    # sitting wholly inside one contributes no paired sample at any lag;
    # counting it would pass a pair through ``min_events`` on evidence that
    # never enters the estimate.
    n_events = _count_events(jam & np.isfinite(down_residual), MIN_EVENT_SAMPLES)
    if n_events < min_events:
        return rejected(_REASON_FEW_EVENTS, n_events=n_events)

    analysed = _dilate(jam, max_lag)
    correlations, counts = _lag_correlations(
        down_residual,
        _detrend(up, detrend_bins, barrier),
        analysed,
        max_lag,
        barrier,
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
    # order matters: a peak at −max_lag is first of all a disturbance that
    # reached the upstream station *earlier*, and calling that a search-bound
    # artefact would hide what the data said.
    if peak_lag <= 0:
        return rejected(_REASON_NOT_BACKWARD, **fields)
    if peak_lag == max_lag:
        return rejected(_REASON_BOUND, **fields)
    if peak_lag < MIN_PEAK_LAG_BINS:
        return rejected(_REASON_SHORT_LAG, **fields)
    if peak_r < MIN_PEAK_CORRELATION:
        return rejected(_REASON_WEAK, **fields)
    # The floor belongs on the lag that is *reported*, not on the bin the peak
    # sits in: a two-bin peak whose parabola pulls it to 1.5 bins is a
    # sub-resolution lag like any other, and dividing the spacing by it would
    # publish a speed the 30-s grid cannot support (module docstring,
    # "Resolution").
    refined_lag = peak_lag + _sub_bin_offset(correlations, index)
    if refined_lag < MIN_PEAK_LAG_BINS:
        return rejected(_REASON_SHORT_LAG, **fields)
    lag_s = refined_lag * dt_s
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


def _barriers(up: FloatArray, down: FloatArray, gap_bins: int) -> NDArray[np.bool_]:
    """Samples inside a date separator (module docstring, step 2).

    A separator is a run of at least ``gap_bins`` consecutive samples that
    *both* stations left unmeasured — what
    :func:`calibration.loaders.mndot.station_speed_series` writes between two
    dates, and equally what a joint outage that long is. Nothing may be
    correlated or averaged across one: on the far side is another morning,
    and the method has no way to tell how far the far side's jam travelled.

    Args:
        up: Upstream speeds [m/s], NaN where unmeasured.
        down: Downstream speeds [m/s], NaN where unmeasured.
        gap_bins: Samples that make a separator; ``<= 0`` means the series is
            one continuous record and there are none.

    Returns:
        A mask of the barrier samples.
    """
    empty = np.zeros(down.size, dtype=np.bool_)
    if gap_bins <= 0:
        return empty
    missing = ~np.isfinite(up) & ~np.isfinite(down)
    if not bool(missing.any()):
        return empty
    padded = np.concatenate(([False], missing, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    for start, stop in zip(edges[0::2], edges[1::2], strict=True):
        if stop - start >= gap_bins:
            empty[start:stop] = True
    return empty


def _detrend(series: FloatArray, window_bins: int, barrier: NDArray[np.bool_]) -> FloatArray:
    """The series minus its centred moving mean over ``window_bins`` samples.

    The mean is taken over the finite samples inside the window only, and is
    undefined — so the residual is NaN — in three cases: fewer than
    :data:`MIN_DETREND_COVERAGE` of the window was measured; the window runs
    off an end of the series; or the window contains a barrier sample. The
    last two are the same defect. A window that reaches past the data is a
    *one-sided* mean, and subtracting a one-sided mean from a series that is
    going anywhere leaves the local trend in the residual instead of removing
    it — at the start of a morning peak exactly the slow envelope the step
    exists to take out. A half-window at each end of each date is the price;
    it is paid in NaN rather than in a biased residual.

    Args:
        series: Speeds [m/s], NaN where unmeasured.
        window_bins: Width of the moving mean [samples]; 0 or 1 is the
            identity (the step is disabled).
        barrier: Date separators (:func:`_barriers`).

    Returns:
        The residual, NaN wherever the series or the local mean is.
    """
    if window_bins <= 1:
        return series.astype(np.float64, copy=True)
    finite = np.isfinite(series)
    values = np.where(finite, series, 0.0)
    cumulative = np.concatenate(([0.0], np.cumsum(values)))
    counts = np.concatenate(([0], np.cumsum(finite.astype(np.int64))))
    blocked = np.concatenate(([0], np.cumsum(barrier.astype(np.int64))))
    n = series.size
    half = window_bins // 2
    index = np.arange(n)
    lo = np.clip(index - half, 0, n)
    hi = np.clip(index + half + 1, 0, n)
    total = cumulative[hi] - cumulative[lo]
    present = counts[hi] - counts[lo]
    two_sided = (index >= half) & (index + half < n) & (blocked[hi] - blocked[lo] == 0)
    enough = present >= max(1, math.ceil(MIN_DETREND_COVERAGE * window_bins))
    with np.errstate(invalid="ignore", divide="ignore"):
        baseline = np.where(enough & two_sided, total / np.maximum(present, 1), math.nan)
    return series - baseline


def _lag_correlations(
    down: FloatArray,
    up: FloatArray,
    analysed: NDArray[np.bool_],
    max_lag: int,
    barrier: NDArray[np.bool_],
) -> tuple[FloatArray, NDArray[np.int64]]:
    """Pearson correlation of ``down(t)`` against ``up(t + k)`` per lag.

    A pair whose two samples are separated by a barrier is dropped, so a lag
    longer than the separator between two dates pairs nothing rather than
    pairing one morning against the next (module docstring, step 2).

    Args:
        down: Downstream speeds [m/s], NaN where unmeasured.
        up: Upstream speeds [m/s], NaN where unmeasured.
        analysed: Samples ``t`` the correlation is taken over.
        max_lag: Widest lag in bins.
        barrier: Date separators (:func:`_barriers`).

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
    blocked = np.concatenate(([0], np.cumsum(barrier.astype(np.int64))))
    any_barrier = bool(barrier.any())
    for lag in range(-max_lag, max_lag + 1):
        lo = max(0, -lag)
        hi = min(n, n - lag)
        if hi - lo < MIN_PAIRED_SAMPLES:
            continue
        left = down[lo:hi]
        right = up[lo + lag : hi + lag]
        keep = base[lo:hi] & np.isfinite(right)
        if any_barrier and lag != 0:
            here = np.arange(lo, hi)
            there = here + lag
            first = np.minimum(here, there)
            last = np.maximum(here, there)
            keep &= blocked[last + 1] - blocked[first] == 0
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
