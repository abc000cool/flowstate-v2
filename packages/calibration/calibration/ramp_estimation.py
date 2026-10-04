"""Unmeasured ramp volumes from mainline station counts, with stated uncertainty.

Many corridors count the mainline well and the ramps poorly or not at all.
Between two consecutive mainline stations vehicles are conserved::

    q_down(t + τ) − q_up(t) = Σ q_on − Σ q_off

with ``τ`` the travel time between the stations. When exactly one ramp of a
segment has no usable count, this identity gives its flow directly from the
counts that exist. :mod:`calibration.conservation` aligns the series (the lag
``τ`` from the station spacing and the measured speeds, by linear
interpolation) and averages them into periods (default 15 min) to tame lag
and count noise; this module solves for the unmeasured ramps.

**Identifiability.** One equation per period fixes one unknown. With exactly
one unmeasured ramp in a segment the estimate is direct. With more (an
unmeasured entrance *and* exit, say) the counts fix only their net flow: the
ramps are reported ``unidentified`` unless the caller supplies one explicit
:class:`SplitAssumption` per extra ramp — an exit's share of the upstream
flow, a fixed flow from a manual count, or a ratio to another ramp — and each
assumption is recorded on the output with its ``source`` string. An
assumption is taken as exact: the interval covers count error only.

**Clipping.** A ramp cannot carry negative flow. A negative estimate (count
error, storage in a queue) is set to 0, flagged, and the clipped amount is
reported; nothing is redistributed to other ramps.

**Uncertainty.** Every measured count carries a stated relative error
(default ±5 %, :data:`calibration.conservation.DEFAULT_COUNT_ERROR`), and the
estimate is a linear function of the counts, so the error propagates exactly:
``linear`` adds the terms' bounds (holds whatever the correlation between
detectors), ``quadrature`` takes their root sum of squares (independent
errors). The interval does **not** cover the change in the number of vehicles
stored between the stations, which is not small in a queue: such periods are
flagged ``congested`` and the leave-one-out statistics are split by it.

**Validation.** :func:`leave_one_out` treats each *measured* ramp in turn as
unmeasured, estimates it from the other counts and compares the estimate with
its own detector: bias, mean absolute error, relative error and how often the
interval covers the measured value. On a corridor with instrumented ramps
this is the check of both the method and the ±5 % assumption.

This is a different tool from the demand closure in
:mod:`calibration.onboarding`, which assigns each bracket's whole residual to
its ramps window by window so a simulation reproduces every station count; it
carries no lag and no uncertainty. Nothing here imputes a missing count: a
period with any required count missing has no estimate.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import numpy as np
import pandas as pd

from calibration.conservation import (
    CONGESTED_SPEED_MS,
    DEFAULT_COUNT_ERROR,
    DEFAULT_LAG_SPEED_MS,
    DEFAULT_PERIOD_S,
    Combination,
    CorridorLayout,
    DetectorGrid,
    RampSpec,
    Segment,
    align_segment,
    clock_text,
    corridor_layout,
    detector_grid,
    period_means,
    periods_per_window,
    silent_lane_note,
    station_grid,
)

RAMP_SCHEMA: Final[str] = "flowstate.ramp_estimates/1"
"""Schema tag of the estimation JSON."""

LOO_SCHEMA: Final[str] = "flowstate.ramp_leave_one_out/1"
"""Schema tag of the leave-one-out JSON."""

SplitKind = Literal["share_of_upstream", "fixed_veh_h", "ratio_to"]
SPLIT_KINDS: Final[tuple[str, ...]] = ("share_of_upstream", "fixed_veh_h", "ratio_to")

METHOD: Final[str] = (
    "Between two neighbouring mainline stations every vehicle is conserved: the flow counted "
    "downstream (one travel time later) equals the flow counted upstream plus the entrances "
    "minus the exits in between. Travel time comes from the station spacing and the speeds "
    "the two stations measure; all counts are read at the unmeasured ramp's position by "
    "shifting them by their travel time, then averaged over each period. With one ramp "
    "without a count in a segment, its flow is what the counts leave over; with more, the "
    "counts fix only their net flow, and a stated split assumption per extra ramp is needed. "
    "Negative results are set to zero and reported. The interval propagates a stated "
    "per-detector count error; it does not include the change in the number of vehicles "
    "queued between the stations, so periods in congestion are flagged."
)
"""The method in plain language (printed in every summary)."""


@dataclass(frozen=True)
class SplitAssumption:
    """An explicit assumption that identifies an extra unmeasured ramp.

    Attributes:
        ramp: The unmeasured ramp it fixes.
        kind: ``share_of_upstream`` (ramp flow = ``value`` × the segment's
            upstream station flow), ``fixed_veh_h`` (ramp flow = ``value``
            veh/h) or ``ratio_to`` (ramp flow = ``value`` × the flow of
            ramp ``other``, measured or not, in the same segment).
        value: The share, flow or ratio (finite, ≥ 0).
        source: Where the number comes from (a count, a plan, a guess —
            say so). Required; recorded on every output.
        other: The reference ramp of a ``ratio_to`` assumption.
    """

    ramp: str
    kind: SplitKind
    value: float
    source: str
    other: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in SPLIT_KINDS:
            raise ValueError(f"split kind must be one of {list(SPLIT_KINDS)}, got {self.kind!r}")
        if not math.isfinite(self.value) or self.value < 0.0:
            raise ValueError(f"split for {self.ramp!r}: value must be finite and >= 0")
        if not str(self.source).strip():
            raise ValueError(f"split for {self.ramp!r}: a source string is required")
        if (self.kind == "ratio_to") != (self.other is not None):
            raise ValueError(
                f"split for {self.ramp!r}: 'other' is required for ratio_to and only for it"
            )

    def describe(self) -> str:
        """One plain sentence."""
        if self.kind == "share_of_upstream":
            what = f"{self.value:.1%} of the upstream station's flow"
        elif self.kind == "fixed_veh_h":
            what = f"a fixed {self.value:,.0f} veh/h"
        else:
            what = f"{self.value:g}x the flow of {self.other}"
        return f"{self.ramp} carries {what} (source: {self.source})"

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "ramp": self.ramp,
            "kind": self.kind,
            "value": self.value,
            "other": self.other,
            "source": self.source,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> SplitAssumption:
        """Build from a JSON object (the CLI's ``--splits`` file)."""
        other = raw.get("other")
        return cls(
            ramp=str(raw["ramp"]),
            kind=raw["kind"],
            value=float(raw["value"]),
            source=str(raw.get("source") or ""),
            other=None if other in (None, "") else str(other),
        )


def _r(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    value = float(value)
    return None if not math.isfinite(value) else round(value, digits)


def _series(values: np.ndarray, digits: int = 1) -> list[float | None]:
    return [_r(float(v), digits) for v in values]


@dataclass(frozen=True)
class RampEstimate:
    """Estimated flow of one unmeasured ramp (or why there is none).

    Arrays have shape ``(len(dates), n_periods)`` and are in veh/h.

    Attributes:
        ramp: Ramp id.
        kind: ``on_ramp`` or ``off_ramp``.
        x_m: Position [m].
        label: Location label.
        upstream: Upstream station of its segment.
        downstream: Downstream station.
        status: ``estimated`` or ``unidentified``.
        reason: Plain statement of how it was obtained, or why not.
        assumptions: Split assumptions used in its segment.
        x_ref_m: Position the counts were aligned to [m].
        estimate_veh_h: Estimate, clipped at 0 (NaN where a count is missing).
        lower_veh_h: Interval lower bound (clipped at 0).
        upper_veh_h: Interval upper bound (clipped at 0).
        raw_veh_h: Estimate before clipping.
        congested: Periods in which a station's speed was below the
            congestion threshold.
        lag_fallback_windows: Windows whose lag used the fallback speed.
        lag_clamped_windows: Windows whose lag hit the cap.
    """

    ramp: str
    kind: str
    x_m: float
    label: str
    upstream: str
    downstream: str
    status: str
    reason: str
    assumptions: tuple[SplitAssumption, ...] = ()
    x_ref_m: float | None = None
    estimate_veh_h: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    lower_veh_h: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    upper_veh_h: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    raw_veh_h: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    congested: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=bool))
    lag_fallback_windows: int = 0
    lag_clamped_windows: int = 0

    def summary(self) -> dict[str, Any]:
        """Counts and means over every estimated period."""
        if self.status != "estimated":
            return {
                "n_periods": 0,
                "n_estimated": 0,
                "n_clipped": 0,
                "mean_estimate_veh_h": None,
                "mean_half_width_veh_h": None,
                "mean_clipped_veh_h": None,
                "share_congested": None,
            }
        valid = np.isfinite(self.raw_veh_h)
        clipped = valid & (self.raw_veh_h < 0.0)
        half = (self.upper_veh_h - self.lower_veh_h) / 2.0
        return {
            "n_periods": int(self.raw_veh_h.size),
            "n_estimated": int(valid.sum()),
            "n_clipped": int(clipped.sum()),
            "mean_estimate_veh_h": _r(float(self.estimate_veh_h[valid].mean()))
            if valid.any()
            else None,
            "mean_half_width_veh_h": _r(float(half[valid].mean())) if valid.any() else None,
            "mean_clipped_veh_h": _r(float(-self.raw_veh_h[clipped].mean()))
            if clipped.any()
            else None,
            "share_congested": _r(float(self.congested[valid].mean()), 4) if valid.any() else None,
        }

    def profile(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Mean over dates per period: ``(estimate, lower, upper, n_dates)``.

        ``lower``/``upper`` are the means of the per-day bounds (an envelope
        of the count error, not a confidence interval of the mean).
        """
        if self.status != "estimated" or not self.estimate_veh_h.size:
            empty = np.zeros(0)
            return empty, empty, empty, empty
        n = np.isfinite(self.estimate_veh_h).sum(axis=0)

        def mean(a: np.ndarray) -> np.ndarray:
            total = np.where(np.isfinite(a), a, 0.0).sum(axis=0)
            return np.where(n > 0, total / np.maximum(n, 1), np.nan)

        return mean(self.estimate_veh_h), mean(self.lower_veh_h), mean(self.upper_veh_h), n

    def to_dict(self, dates: Sequence[str]) -> dict[str, Any]:
        """JSON form (per-date arrays and the mean profile)."""
        out: dict[str, Any] = {
            "ramp": self.ramp,
            "kind": self.kind,
            "x_m": _r(self.x_m),
            "label": self.label,
            "segment": {"upstream": self.upstream, "downstream": self.downstream},
            "status": self.status,
            "reason": self.reason,
            "assumptions": [a.to_dict() for a in self.assumptions],
            "x_ref_m": _r(self.x_ref_m),
            "summary": self.summary(),
            "lag_fallback_windows": self.lag_fallback_windows,
            "lag_clamped_windows": self.lag_clamped_windows,
            "by_date": {},
            "profile": None,
        }
        if self.status == "estimated":
            out["by_date"] = {
                date: {
                    "estimate_veh_h": _series(self.estimate_veh_h[d]),
                    "lower_veh_h": _series(self.lower_veh_h[d]),
                    "upper_veh_h": _series(self.upper_veh_h[d]),
                    "raw_veh_h": _series(self.raw_veh_h[d]),
                    "congested": [bool(c) for c in self.congested[d]],
                }
                for d, date in enumerate(dates)
            }
            est, lo, hi, n = self.profile()
            out["profile"] = {
                "estimate_veh_h": _series(est),
                "lower_veh_h": _series(lo),
                "upper_veh_h": _series(hi),
                "n_dates": [int(x) for x in n],
            }
        return out


@dataclass(frozen=True)
class RampEstimationResult:
    """Every unmeasured ramp's estimate and the parameters that produced it.

    Attributes:
        interval_s: Detector window [s].
        period_s: Aggregation period [s].
        period_start_s: Local clock start of each period [s].
        dates: Local dates.
        count_error: Relative count error per detector.
        combination: ``linear`` or ``quadrature``.
        default_speed_ms: Lag fallback speed [m/s].
        congested_speed_ms: Congestion flag threshold [m/s].
        max_lag_s: Lag cap [s].
        estimates: One record per unmeasured ramp in a segment.
        layout: The corridor layout used.
        splits: Every split assumption supplied.
        notes: Plain statements (ramps outside the span, …).
        schema: :data:`RAMP_SCHEMA`.
    """

    interval_s: float
    period_s: float
    period_start_s: tuple[float, ...]
    dates: tuple[str, ...]
    count_error: float
    combination: str
    default_speed_ms: float
    congested_speed_ms: float
    max_lag_s: float
    estimates: tuple[RampEstimate, ...]
    layout: CorridorLayout
    splits: tuple[SplitAssumption, ...] = ()
    notes: tuple[str, ...] = ()
    schema: str = RAMP_SCHEMA

    def estimate(self, ramp: str) -> RampEstimate:
        """The record of one ramp.

        Raises:
            KeyError: No estimate record for that ramp.
        """
        for e in self.estimates:
            if e.ramp == ramp:
                return e
        raise KeyError(f"no estimate for ramp {ramp!r}")

    def assumptions(self) -> list[str]:
        """Every assumption the estimates rest on, as plain sentences."""
        out = [
            f"every detector count is within ±{self.count_error:.0%} of the true count "
            f"(errors combined {self.combination}ly)",
            "vehicles are conserved between neighbouring stations, apart from the change in "
            "the number queued between them (not in the interval; congested periods flagged)",
            f"travel time between stations = spacing × mean of the two stations' 1/speed; "
            f"{self.default_speed_ms:g} m/s where neither reports a speed; shifts capped at "
            f"{self.max_lag_s:g} s",
            "a ramp cannot carry negative flow (negative estimates set to 0 and reported)",
            "the station and ramp positions are those of the inventory supplied",
        ]
        out += [s.describe() for s in self.splits]
        return out

    def to_dict(self) -> dict[str, Any]:
        """JSON form (fixed keys)."""
        return {
            "schema": self.schema,
            "method": METHOD,
            "parameters": {
                "interval_s": self.interval_s,
                "period_s": self.period_s,
                "count_error": self.count_error,
                "combination": self.combination,
                "default_speed_ms": self.default_speed_ms,
                "congested_speed_ms": round(self.congested_speed_ms, 4),
                "max_lag_s": self.max_lag_s,
            },
            "assumptions": self.assumptions(),
            "splits": [s.to_dict() for s in self.splits],
            "dates": list(self.dates),
            "period_start_local": [clock_text(t) for t in self.period_start_s],
            "layout": self.layout.to_dict(),
            "estimates": [e.to_dict(self.dates) for e in self.estimates],
            "notes": list(self.notes),
        }

    def to_json(self) -> str:
        """JSON text (sorted keys; NaN as ``null``)."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, allow_nan=False)


# ---------------------------------------------------------------------------
# Solving one segment
# ---------------------------------------------------------------------------


def _solve_segment(
    grid: DetectorGrid,
    segment: Segment,
    *,
    splits: Sequence[SplitAssumption],
    period_s: float,
    count_error: float,
    combination: Combination,
    default_speed_ms: float,
    max_lag_s: float,
    congested_speed_ms: float,
    x_ref_m: float | None = None,
) -> list[RampEstimate]:
    """Estimates for the unmeasured ramps of one segment (module docstring).

    Raises:
        ValueError: More assumptions than unknowns, an assumption naming a
            ramp outside the segment, or an unknown ``combination``.
    """
    unknowns = list(segment.unmeasured())
    measured = list(segment.measured())
    index = {r.id: i for i, r in enumerate(unknowns)}
    measured_index = {r.id: i for i, r in enumerate(measured)}
    own = [s for s in splits if s.ramp in index]
    n, k = len(unknowns), len(own)
    if k + 1 > n:
        raise ValueError(
            f"segment {segment.name}: {k} split assumption(s) for {n} unmeasured ramp(s) — "
            f"the counts already fix one ramp; drop an assumption"
        )
    for s in own:
        if s.kind == "ratio_to" and s.other not in index and s.other not in measured_index:
            raise ValueError(
                f"split for {s.ramp!r}: ratio_to {s.other!r} is not a ramp of segment "
                f"{segment.name}"
            )
    if k + 1 < n:
        ids = ", ".join(f"{r.id} ({r.kind})" for r in unknowns)
        reason = (
            f"{n} ramps without counts in {segment.name} ({ids}) and {k} split assumption(s): "
            f"the counts fix only their net flow; {n - 1 - k} more assumption(s) (e.g. an exit's "
            f"share of the upstream flow) would identify them"
        )
        return [
            RampEstimate(
                ramp=r.id,
                kind=r.kind,
                x_m=r.x_m,
                label=r.label,
                status="unidentified",
                reason=reason,
                assumptions=tuple(own),
                upstream=segment.upstream,
                downstream=segment.downstream,
            )
            for r in unknowns
        ]
    A = np.zeros((n, n))
    B = np.zeros((n, 2 + len(measured)))
    c = np.zeros(n)
    for j, r in enumerate(unknowns):
        A[0, j] = r.sign
    B[0, 0], B[0, 1] = -1.0, 1.0
    for i, r in enumerate(measured):
        B[0, 2 + i] = -r.sign
    for row, s in enumerate(own, start=1):
        A[row, index[s.ramp]] = 1.0
        if s.kind == "share_of_upstream":
            B[row, 0] = s.value
        elif s.kind == "fixed_veh_h":
            c[row] = s.value
        elif s.other in index:
            A[row, index[str(s.other)]] -= s.value
        else:
            B[row, 2 + measured_index[str(s.other)]] = s.value
    if np.linalg.matrix_rank(A) < n:
        reason = (
            f"the split assumptions for {segment.name} do not determine the ramps "
            f"(they are not independent of the conservation equation)"
        )
        return [
            RampEstimate(
                ramp=r.id,
                kind=r.kind,
                x_m=r.x_m,
                label=r.label,
                status="unidentified",
                reason=reason,
                assumptions=tuple(own),
                upstream=segment.upstream,
                downstream=segment.downstream,
            )
            for r in unknowns
        ]
    x_ref = unknowns[0].x_m if x_ref_m is None else float(x_ref_m)
    aligned = align_segment(
        grid,
        segment,
        x_ref_m=x_ref,
        period_s=period_s,
        default_speed_ms=default_speed_ms,
        max_lag_s=max_lag_s,
        congested_speed_ms=congested_speed_ms,
    )
    m = np.stack([aligned.up, aligned.down, *(aligned.ramps[r.id] for r in measured)])
    inverse = np.linalg.inv(A)
    G = inverse @ B
    g = inverse @ c
    valid = np.isfinite(m).all(axis=0)
    m0 = np.where(np.isfinite(m), m, 0.0)
    u = np.einsum("nm,mdp->ndp", G, m0) + g[:, None, None]
    if combination == "linear":
        half = count_error * np.einsum("nm,mdp->ndp", np.abs(G), np.abs(m0))
    elif combination == "quadrature":
        half = count_error * np.sqrt(np.einsum("nm,mdp->ndp", G**2, m0**2))
    else:
        raise ValueError(f"combination must be 'linear' or 'quadrature', got {combination!r}")
    raw = np.where(valid[None], u, np.nan)
    half = np.where(valid[None], half, np.nan)
    terms = [f"{segment.downstream} − {segment.upstream}"]
    terms += [f"{'−' if r.sign > 0 else '+'} {r.id}" for r in measured]
    out: list[RampEstimate] = []
    for j, r in enumerate(unknowns):
        if n == 1:
            sign = "" if r.sign > 0 else "−("
            close = "" if r.sign > 0 else ")"
            reason = (
                f"the only ramp without a count in {segment.name}: flow = "
                f"{sign}{' '.join(terms)}{close}, counts read at x = {x_ref:,.0f} m"
            )
        else:
            reason = (
                f"one of {n} ramps without counts in {segment.name}, identified by the "
                f"conservation equation and {k} stated split assumption(s)"
            )
        out.append(
            RampEstimate(
                ramp=r.id,
                kind=r.kind,
                x_m=r.x_m,
                label=r.label,
                status="estimated",
                reason=reason,
                assumptions=tuple(own),
                x_ref_m=x_ref,
                estimate_veh_h=np.maximum(raw[j], 0.0),
                lower_veh_h=np.maximum(raw[j] - half[j], 0.0),
                upper_veh_h=np.maximum(raw[j] + half[j], 0.0),
                raw_veh_h=raw[j],
                congested=aligned.congested.copy(),
                lag_fallback_windows=aligned.n_lag_fallback,
                lag_clamped_windows=aligned.n_lag_clamped,
                upstream=segment.upstream,
                downstream=segment.downstream,
            )
        )
    return out


def _prepare(
    data: pd.DataFrame | DetectorGrid,
    *,
    stations: pd.DataFrame | Sequence[Mapping[str, Any]] | None,
    extra_ramps: Iterable[RampSpec],
    unmeasured: Iterable[str],
    splits: Iterable[SplitAssumption],
    period_s: float,
    count_error: float,
) -> tuple[DetectorGrid, CorridorLayout, tuple[SplitAssumption, ...], list[str]]:
    """Station grid, layout, validated splits and notes (shared by both entry points)."""
    if count_error < 0.0:
        raise ValueError(f"count_error must be >= 0, got {count_error}")
    grid = data if isinstance(data, DetectorGrid) else detector_grid(data)
    silent = silent_lane_note(grid)
    grid = station_grid(grid)
    periods_per_window(grid, period_s)
    layout = corridor_layout(
        grid, stations=stations, extra_ramps=extra_ramps, unmeasured=unmeasured
    )
    split_list = tuple(splits)
    seen: set[str] = set()
    in_segments = {r.id for seg in layout.segments for r in seg.ramps}
    for s in split_list:
        if s.ramp in seen:
            raise ValueError(f"two split assumptions for ramp {s.ramp!r}")
        seen.add(s.ramp)
        if s.ramp not in in_segments:
            raise ValueError(f"split for {s.ramp!r}: not a ramp inside any segment")
        seg, spec = layout.ramp(s.ramp)
        if spec.measured:
            raise ValueError(
                f"split for {s.ramp!r}: the ramp is measured; an assumption applies only to a "
                f"ramp without counts"
            )
        del seg
    notes = _layout_notes(layout) + ([silent] if silent else [])
    return grid, layout, split_list, notes


def _layout_notes(layout: CorridorLayout) -> list[str]:
    notes: list[str] = []
    if layout.outside_ramps:
        notes.append(
            "ramps outside the stations' span, not estimated (their flow is inside the nearest "
            "station count): " + ", ".join(r.id for r in layout.outside_ramps)
        )
    if layout.stations_without_data:
        notes.append(
            "mainline stations without data, spanned by a longer segment: "
            + ", ".join(layout.stations_without_data)
        )
    if layout.stations_without_position:
        notes.append(
            "sensors without a position, left out: " + ", ".join(layout.stations_without_position)
        )
    return notes


def estimate_ramps(
    data: pd.DataFrame | DetectorGrid,
    *,
    stations: pd.DataFrame | Sequence[Mapping[str, Any]] | None = None,
    extra_ramps: Iterable[RampSpec] = (),
    unmeasured: Iterable[str] = (),
    splits: Iterable[SplitAssumption] = (),
    period_s: float = DEFAULT_PERIOD_S,
    count_error: float = DEFAULT_COUNT_ERROR,
    combination: Combination = "linear",
    default_speed_ms: float = DEFAULT_LAG_SPEED_MS,
    congested_speed_ms: float = CONGESTED_SPEED_MS,
    max_lag_s: float | None = None,
) -> RampEstimationResult:
    """Estimate every unmeasured ramp of a corridor (module docstring).

    Args:
        data: Tidy detector frame or grid (per-lane data are summed to
            station totals first).
        stations: Optional stations table (positions, kinds, ramps without
            detectors).
        extra_ramps: Ramps known to exist without a detector.
        unmeasured: Ramps with counts to treat as unmeasured.
        splits: Split assumptions for segments with more than one unmeasured
            ramp.
        period_s: Aggregation period [s].
        count_error: Relative count error per detector.
        combination: ``linear`` or ``quadrature``.
        default_speed_ms: Lag speed when no station reports one [m/s].
        congested_speed_ms: Congestion flag threshold [m/s].
        max_lag_s: Lag cap [s] (one period when None).

    Returns:
        The :class:`RampEstimationResult`.

    Raises:
        ValueError: A bad parameter or assumption (see :func:`_solve_segment`
            and :func:`calibration.conservation.corridor_layout`).
    """
    grid, layout, split_list, notes = _prepare(
        data,
        stations=stations,
        extra_ramps=extra_ramps,
        unmeasured=unmeasured,
        splits=splits,
        period_s=period_s,
        count_error=count_error,
    )
    cap = float(period_s if max_lag_s is None else max_lag_s)
    estimates: list[RampEstimate] = []
    for seg in layout.segments:
        if not seg.unmeasured():
            continue
        estimates += _solve_segment(
            grid,
            seg,
            splits=split_list,
            period_s=period_s,
            count_error=count_error,
            combination=combination,
            default_speed_ms=default_speed_ms,
            max_lag_s=cap,
            congested_speed_ms=congested_speed_ms,
        )
    per = periods_per_window(grid, period_s)
    n_periods = grid.n_windows // per
    if not estimates:
        notes.append("every ramp inside the stations' span has counts: nothing to estimate")
    return RampEstimationResult(
        interval_s=grid.interval_s,
        period_s=float(period_s),
        period_start_s=tuple(grid.start_s + j * period_s for j in range(n_periods)),
        dates=grid.dates,
        count_error=float(count_error),
        combination=str(combination),
        default_speed_ms=float(default_speed_ms),
        congested_speed_ms=float(congested_speed_ms),
        max_lag_s=cap,
        estimates=tuple(estimates),
        layout=layout,
        splits=split_list,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Leave-one-out validation
# ---------------------------------------------------------------------------


def error_stats(
    estimate: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    truth: np.ndarray,
    raw: np.ndarray | None = None,
) -> dict[str, Any]:
    """Estimate-vs-measured statistics over the periods where both exist.

    Args:
        estimate: Clipped estimate [veh/h].
        lower: Interval lower bound.
        upper: Interval upper bound.
        truth: The ramp's own measured flow, same shape.
        raw: Unclipped estimate (to count clipped periods), optional.

    Returns:
        ``n``, ``mean_measured_veh_h``, ``bias_veh_h`` (mean estimate −
        measured), ``mae_veh_h``, ``rmse_veh_h``, ``relative_error``
        (Σ|error| / Σ measured), ``relative_bias`` (Σ error / Σ measured),
        ``coverage`` (share of periods whose measured flow lies in the
        interval) and ``n_clipped``. Relative values are ``None`` when the
        measured flow sums to 0; everything is ``None`` when ``n`` is 0.
    """
    both = np.isfinite(estimate) & np.isfinite(truth)
    n = int(both.sum())
    keys = (
        "mean_measured_veh_h",
        "bias_veh_h",
        "mae_veh_h",
        "rmse_veh_h",
        "relative_error",
        "relative_bias",
        "coverage",
    )
    if n == 0:
        return {"n": 0, **dict.fromkeys(keys), "n_clipped": 0}
    e = estimate[both] - truth[both]
    t = truth[both]
    total = float(t.sum())
    covered = (lower[both] <= t) & (t <= upper[both])
    clipped = int(((raw if raw is not None else estimate)[both] < 0.0).sum())
    return {
        "n": n,
        "mean_measured_veh_h": _r(float(t.mean())),
        "bias_veh_h": _r(float(e.mean())),
        "mae_veh_h": _r(float(np.abs(e).mean())),
        "rmse_veh_h": _r(float(np.sqrt((e**2).mean()))),
        "relative_error": _r(float(np.abs(e).sum()) / total, 4) if total > 0 else None,
        "relative_bias": _r(float(e.sum()) / total, 4) if total > 0 else None,
        "coverage": _r(float(covered.mean()), 4),
        "n_clipped": clipped,
    }


@dataclass(frozen=True)
class LeaveOneOutRamp:
    """One measured ramp, estimated as if it had no count.

    Attributes:
        ramp: Ramp id.
        kind: ``on_ramp`` or ``off_ramp``.
        x_m: Position [m].
        upstream: Upstream station of its segment.
        downstream: Downstream station.
        status: ``tested`` or ``not_testable``.
        reason: Why not testable (or how tested).
        overall: :func:`error_stats` over every compared period.
        free_flow: The same over periods not flagged congested.
        congested: The same over congested periods.
    """

    ramp: str
    kind: str
    x_m: float
    upstream: str
    downstream: str
    status: str
    reason: str
    overall: dict[str, Any] = field(default_factory=dict)
    free_flow: dict[str, Any] = field(default_factory=dict)
    congested: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "ramp": self.ramp,
            "kind": self.kind,
            "x_m": _r(self.x_m),
            "segment": {"upstream": self.upstream, "downstream": self.downstream},
            "status": self.status,
            "reason": self.reason,
            "overall": dict(self.overall),
            "free_flow": dict(self.free_flow),
            "congested": dict(self.congested),
        }


@dataclass(frozen=True)
class LeaveOneOutResult:
    """Leave-one-out validation of the estimator on measured ramps.

    Attributes:
        estimation: Parameters of the estimator (as in
            :meth:`RampEstimationResult.to_dict`'s ``parameters``).
        dates: Local dates.
        ramps: One record per measured ramp inside a segment.
        pooled: :func:`error_stats` over every tested ramp's periods.
        pooled_free_flow: The same, free-flow periods.
        pooled_congested: The same, congested periods.
        median_ramp_relative_error: Median over tested ramps of their
            relative error.
        splits: Split assumptions supplied.
        notes: Plain statements.
        schema: :data:`LOO_SCHEMA`.
    """

    estimation: dict[str, Any]
    dates: tuple[str, ...]
    ramps: tuple[LeaveOneOutRamp, ...]
    pooled: dict[str, Any]
    pooled_free_flow: dict[str, Any]
    pooled_congested: dict[str, Any]
    median_ramp_relative_error: float | None
    splits: tuple[SplitAssumption, ...] = ()
    notes: tuple[str, ...] = ()
    schema: str = LOO_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        """JSON form (fixed keys)."""
        return {
            "schema": self.schema,
            "method": METHOD
            + " Leave-one-out: each measured ramp in turn is treated as unmeasured, estimated "
            "from the other counts and compared with its own detector, whose own count error is "
            "not part of the interval.",
            "parameters": dict(self.estimation),
            "dates": list(self.dates),
            "splits": [s.to_dict() for s in self.splits],
            "ramps": [r.to_dict() for r in self.ramps],
            "pooled": {
                "overall": dict(self.pooled),
                "free_flow": dict(self.pooled_free_flow),
                "congested": dict(self.pooled_congested),
                "median_ramp_relative_error": _r(self.median_ramp_relative_error, 4),
                "n_tested": sum(1 for r in self.ramps if r.status == "tested"),
                "n_not_testable": sum(1 for r in self.ramps if r.status != "tested"),
            },
            "notes": list(self.notes),
        }

    def to_json(self) -> str:
        """JSON text (sorted keys; NaN as ``null``)."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, allow_nan=False)


def _loo_record(
    target: RampSpec,
    seg: Segment,
    status: str,
    reason: str,
    *,
    overall: dict[str, Any] | None = None,
    free_flow: dict[str, Any] | None = None,
    congested: dict[str, Any] | None = None,
) -> LeaveOneOutRamp:
    """One :class:`LeaveOneOutRamp` for ``target`` in ``seg``."""
    return LeaveOneOutRamp(
        ramp=target.id,
        kind=target.kind,
        x_m=target.x_m,
        upstream=seg.upstream,
        downstream=seg.downstream,
        status=status,
        reason=reason,
        overall=overall or {},
        free_flow=free_flow or {},
        congested=congested or {},
    )


def leave_one_out(
    data: pd.DataFrame | DetectorGrid,
    *,
    stations: pd.DataFrame | Sequence[Mapping[str, Any]] | None = None,
    extra_ramps: Iterable[RampSpec] = (),
    unmeasured: Iterable[str] = (),
    splits: Iterable[SplitAssumption] = (),
    period_s: float = DEFAULT_PERIOD_S,
    count_error: float = DEFAULT_COUNT_ERROR,
    combination: Combination = "linear",
    default_speed_ms: float = DEFAULT_LAG_SPEED_MS,
    congested_speed_ms: float = CONGESTED_SPEED_MS,
    max_lag_s: float | None = None,
) -> LeaveOneOutResult:
    """Estimate each measured ramp as if unmeasured and score it against its count.

    A ramp is ``not_testable`` when hiding it leaves its segment with more
    unmeasured ramps than the supplied assumptions can identify. The
    estimate is aligned to the ramp's own position, so it is compared with
    the ramp's own period means directly.

    Args:
        data: Tidy detector frame or grid.
        stations: Optional stations table.
        extra_ramps: Ramps known to exist without a detector.
        unmeasured: Ramps with counts to treat as unmeasured throughout
            (they are neither tested nor used).
        splits: Split assumptions (as for :func:`estimate_ramps`).
        period_s: Aggregation period [s].
        count_error: Relative count error per detector.
        combination: ``linear`` or ``quadrature``.
        default_speed_ms: Lag fallback speed [m/s].
        congested_speed_ms: Congestion flag threshold [m/s].
        max_lag_s: Lag cap [s] (one period when None).

    Returns:
        The :class:`LeaveOneOutResult`.
    """
    grid, layout, split_list, notes = _prepare(
        data,
        stations=stations,
        extra_ramps=extra_ramps,
        unmeasured=unmeasured,
        splits=splits,
        period_s=period_s,
        count_error=count_error,
    )
    cap = float(period_s if max_lag_s is None else max_lag_s)
    per = periods_per_window(grid, period_s)
    records: list[LeaveOneOutRamp] = []
    pooled: dict[str, list[np.ndarray]] = {
        k: [] for k in ("est", "lo", "hi", "truth", "raw", "cong")
    }
    ramp_rel: list[float] = []
    for seg in layout.segments:
        for target in seg.measured():
            hidden = Segment(
                seg.upstream,
                seg.downstream,
                seg.x_up_m,
                seg.x_down_m,
                tuple(
                    RampSpec(r.id, r.kind, r.x_m, False, r.label) if r.id == target.id else r
                    for r in seg.ramps
                ),
            )
            own = [s for s in split_list if s.ramp in {r.id for r in hidden.unmeasured()}]
            n_unknown = len(hidden.unmeasured())
            if len(own) + 1 < n_unknown:
                others = ", ".join(r.id for r in seg.unmeasured())
                records.append(
                    _loo_record(
                        target,
                        seg,
                        "not_testable",
                        f"segment {seg.name} already has unmeasured ramp(s) ({others}); "
                        f"hiding {target.id} leaves {n_unknown} unknowns for "
                        f"{len(own)} assumption(s)",
                    )
                )
                continue
            solved = _solve_segment(
                grid,
                hidden,
                splits=own,
                period_s=period_s,
                count_error=count_error,
                combination=combination,
                default_speed_ms=default_speed_ms,
                max_lag_s=cap,
                congested_speed_ms=congested_speed_ms,
                x_ref_m=target.x_m,
            )
            est = next(e for e in solved if e.ramp == target.id)
            if est.status != "estimated":
                records.append(_loo_record(target, seg, "not_testable", est.reason))
                continue
            truth = period_means(grid.flow_veh_h[target.id], per)
            cong = est.congested
            free = ~cong
            overall = error_stats(
                est.estimate_veh_h, est.lower_veh_h, est.upper_veh_h, truth, est.raw_veh_h
            )
            records.append(
                _loo_record(
                    target,
                    seg,
                    "tested",
                    f"estimated from the other counts of {seg.name}, aligned to its own "
                    f"position, compared with its detector",
                    overall=overall,
                    free_flow=error_stats(
                        np.where(free, est.estimate_veh_h, np.nan),
                        est.lower_veh_h,
                        est.upper_veh_h,
                        truth,
                        est.raw_veh_h,
                    ),
                    congested=error_stats(
                        np.where(cong, est.estimate_veh_h, np.nan),
                        est.lower_veh_h,
                        est.upper_veh_h,
                        truth,
                        est.raw_veh_h,
                    ),
                )
            )
            if overall["relative_error"] is not None:
                ramp_rel.append(float(overall["relative_error"]))
            pooled["est"].append(est.estimate_veh_h.ravel())
            pooled["lo"].append(est.lower_veh_h.ravel())
            pooled["hi"].append(est.upper_veh_h.ravel())
            pooled["truth"].append(truth.ravel())
            pooled["raw"].append(est.raw_veh_h.ravel())
            pooled["cong"].append(cong.ravel())
    if pooled["est"]:
        cat = {k: np.concatenate(v) for k, v in pooled.items()}
        cong_all = cat["cong"].astype(bool)
        pooled_all = error_stats(cat["est"], cat["lo"], cat["hi"], cat["truth"], cat["raw"])
        pooled_free = error_stats(
            np.where(~cong_all, cat["est"], np.nan), cat["lo"], cat["hi"], cat["truth"], cat["raw"]
        )
        pooled_cong = error_stats(
            np.where(cong_all, cat["est"], np.nan), cat["lo"], cat["hi"], cat["truth"], cat["raw"]
        )
    else:
        notes.append("no measured ramp could be tested")
        pooled_all = pooled_free = pooled_cong = error_stats(
            np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0)
        )
    return LeaveOneOutResult(
        estimation={
            "interval_s": grid.interval_s,
            "period_s": float(period_s),
            "count_error": float(count_error),
            "combination": str(combination),
            "default_speed_ms": float(default_speed_ms),
            "congested_speed_ms": round(float(congested_speed_ms), 4),
            "max_lag_s": cap,
        },
        dates=grid.dates,
        ramps=tuple(records),
        pooled=pooled_all,
        pooled_free_flow=pooled_free,
        pooled_congested=pooled_cong,
        median_ramp_relative_error=float(np.median(ramp_rel)) if ramp_rel else None,
        splits=split_list,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Plain-language summaries
# ---------------------------------------------------------------------------


def _fmt(value: Any, spec: str = ",.0f", none: str = "—") -> str:
    return none if value is None else format(value, spec)


def render_markdown(
    result: RampEstimationResult,
    *,
    title: str = "Ramp volume estimates",
    provenance: Mapping[str, Any] | None = None,
) -> str:
    """The estimation summary: method, every assumption, one row per ramp."""
    lines = [f"# {title}", ""]
    for key, value in (provenance or {}).items():
        lines.append(f"- **{key}**: {value}")
    if provenance:
        lines.append("")
    lines += [
        "## Method",
        "",
        METHOD,
        "",
        "## Assumptions",
        "",
        *[f"- {a}" for a in result.assumptions()],
        "",
        f"## Estimates ({len(result.dates)} day(s), {result.period_s / 60:g}-minute periods)",
        "",
    ]
    if result.estimates:
        lines += [
            "| Ramp | Kind | Segment | Status | Mean estimate | Mean ± | Periods clipped | "
            "Congested share |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for e in result.estimates:
            s = e.summary()
            if e.status == "estimated":
                lines.append(
                    f"| {e.ramp} | {e.kind} | {e.upstream}→{e.downstream} | estimated | "
                    f"{_fmt(s['mean_estimate_veh_h'])} veh/h | "
                    f"{_fmt(s['mean_half_width_veh_h'])} veh/h | "
                    f"{s['n_clipped']} of {s['n_estimated']} | "
                    f"{_fmt(s['share_congested'], '.0%')} |"
                )
            else:
                lines.append(
                    f"| {e.ramp} | {e.kind} | {e.upstream}→{e.downstream} | unidentified | "
                    f"— | — | — | — |"
                )
        lines.append("")
        for e in result.estimates:
            lines.append(f"- **{e.ramp}**: {e.reason}.")
            s = e.summary()
            if s["n_clipped"]:
                lines.append(
                    f"  {s['n_clipped']} period(s) came out negative and were set to 0 (mean "
                    f"clipped amount {_fmt(s['mean_clipped_veh_h'])} veh/h)."
                )
    else:
        lines.append("Nothing to estimate: every ramp inside the stations' span has counts.")
    if result.notes:
        lines += ["", "## Notes", "", *[f"- {n}" for n in result.notes]]
    lines.append("")
    return "\n".join(lines)


def render_leave_one_out_markdown(
    result: LeaveOneOutResult,
    *,
    title: str = "Ramp estimation: leave-one-out validation",
    provenance: Mapping[str, Any] | None = None,
) -> str:
    """The validation summary: per-ramp and pooled error statistics."""
    p = result.estimation
    lines = [f"# {title}", ""]
    for key, value in (provenance or {}).items():
        lines.append(f"- **{key}**: {value}")
    if provenance:
        lines.append("")
    lines += [
        "Each ramp that has a detector was, in turn, treated as if it had none: its flow was "
        "estimated from the other counts and compared with what its own detector measured. "
        f"Counts were assumed accurate to ±{p['count_error']:.0%} "
        f"({p['combination']} combination); the measured ramp's own error is not in the "
        "interval, so coverage below the nominal level can come from either side.",
        "",
        "## Method",
        "",
        METHOD,
        "",
        "## Per ramp",
        "",
        "| Ramp | Kind | Segment | Periods | Mean measured | Bias | MAE | Relative error | "
        "Coverage | Clipped |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in result.ramps:
        o = r.overall
        if r.status != "tested":
            lines.append(
                f"| {r.ramp} | {r.kind} | {r.upstream}→{r.downstream} | not testable | | | | | | |"
            )
            continue
        lines.append(
            f"| {r.ramp} | {r.kind} | {r.upstream}→{r.downstream} | {o['n']} | "
            f"{_fmt(o['mean_measured_veh_h'])} | {_fmt(o['bias_veh_h'], '+,.0f')} | "
            f"{_fmt(o['mae_veh_h'])} | {_fmt(o['relative_error'], '.1%')} | "
            f"{_fmt(o['coverage'], '.0%')} | {o['n_clipped']} |"
        )
    q = result.pooled
    lines += [
        "",
        "## Pooled over tested ramps",
        "",
        "| Periods | Bias | MAE | RMSE | Relative error | Coverage |",
        "|---|---|---|---|---|---|",
    ]
    for label, stats in (
        ("all", q),
        ("free flow", result.pooled_free_flow),
        ("congested", result.pooled_congested),
    ):
        lines.append(
            f"| {label}: {stats['n']} | {_fmt(stats['bias_veh_h'], '+,.0f')} | "
            f"{_fmt(stats['mae_veh_h'])} | {_fmt(stats['rmse_veh_h'])} | "
            f"{_fmt(stats['relative_error'], '.1%')} | {_fmt(stats['coverage'], '.0%')} |"
        )
    lines += [
        "",
        f"Median relative error over ramps: {_fmt(result.median_ramp_relative_error, '.1%')}.",
    ]
    untestable = [r for r in result.ramps if r.status != "tested"]
    if untestable:
        lines += ["", "## Not testable", ""]
        lines += [f"- {r.ramp}: {r.reason}" for r in untestable]
    if result.splits:
        lines += ["", "## Split assumptions", ""]
        lines += [f"- {s.describe()}" for s in result.splits]
    if result.notes:
        lines += ["", "## Notes", "", *[f"- {n}" for n in result.notes]]
    lines.append("")
    return "\n".join(lines)
