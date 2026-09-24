"""Standard run metrics with honest uncertainty (CLAUDE.md §7.3, contract §7).

All metrics are computed from a run directory's ``trajectories.parquet`` and
``meta.json`` (docs/CONTRACTS.md §3) in SI units, deterministically. GEH and
RMSPE implement the FHWA-style calibration comparison statistics (FHWA
Traffic Analysis Toolbox Vol. III, FHWA-HOP-18-036, 2019). Replicate
aggregation reports t-distribution confidence intervals and flags
underpowered sample sizes (< 20 replicates, CLAUDE.md §0.6).
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.stats import t as student_t

from flowstate_core.constants import V_JAM_THRESH
from flowstate_core.units import ms_to_kmh, s_to_h, veh_s_to_veh_h
from validation.fields import speed_field
from validation.waves import detect_waves

FloatArray = NDArray[np.float64]

#: Minimum replicate count for headline reporting (CLAUDE.md §0.6).
MIN_REPLICATES = 20

#: Two-sided confidence level for replicate CIs.
CI_LEVEL = 0.95

#: Kilometres per metre, derived from the unit helpers only (CLAUDE.md §2):
#: a distance of 1 m is 1 m/s sustained for 1 s, i.e. ms_to_kmh(1) km/h for
#: s_to_h(1) hours.
_KM_PER_M = ms_to_kmh(1.0) * s_to_h(1.0)


class CI(NamedTuple):
    """Replicate confidence interval: ``(mean, lo95, hi95, n)`` (contract §7).

    ``lo95``/``hi95`` are the two-sided t-distribution bounds at
    :data:`CI_LEVEL`; ``n`` is the number of finite replicate values used.
    """

    mean: float
    lo95: float
    hi95: float
    n: int

    @property
    def underpowered(self) -> bool:
        """True when ``n`` is below the headline minimum (CLAUDE.md §0.6)."""
        return self.n < MIN_REPLICATES


@dataclass(frozen=True)
class Metrics:
    """Standard metrics for one run (docs/CONTRACTS.md §7).

    Every field is measured over the run's *measurement window* — the
    recorded run minus its configured warm-up (``sim.warmup_s``, "discarded
    from metrics" per ``flowstate_core.config.SimSpec``); see
    :func:`compute_metrics` for exactly how each metric is windowed.

    Attributes:
        throughput_veh_h: Vehicles crossing the reference cross-section per
            hour [veh/h], over the measurement window.
        mean_tt_s: Mean per-vehicle travel time across the measurement span
            [s]; NaN when fewer than two vehicles traverse the span (a
            one-vehicle sample is not a fleet mean — ``n_travel_time_veh``
            carries the sample size).
        p90_tt_s: 90th-percentile travel time [s] (linear interpolation);
            NaN under the same condition as ``mean_tt_s``.
        n_travel_time_veh: Number of vehicles contributing to ``mean_tt_s``
            and ``p90_tt_s`` — vehicles that entered the network within the
            measurement window and completed the span.
        sigma_v_spatial_ms: Spatial speed standard deviation [m/s]: at each
            output timestamp, the sample standard deviation (ddof=1) of
            instantaneous speeds across the vehicles present, averaged over
            all timestamps with at least two vehicles.
        sigma_v_temporal_ms: Temporal speed standard deviation [m/s]: for
            each vehicle, the sample standard deviation (ddof=1) of its
            speed over time, averaged over all vehicles with at least two
            samples.
        vmt_veh_km: Total distance travelled by all vehicles [veh·km]
            (VMT in the FHWA sense, reported metric), integrated per vehicle
            by the trapezoid rule over sampled speeds.
        vht_veh_h: Total time spent travelling by all vehicles [veh·h]
            (observed sample span per vehicle).
        fuel_ml_per_veh_km: Fuel consumption per vehicle-kilometre [ml/veh·km]
            from the run's meta fuel totals; NaN when not recorded. When the
            run records only the whole-run total ``fuel_total_ml`` this is a
            **whole-run** ratio (total fuel over whole-run VMT) even when a
            warm-up is discarded from the other metrics: dividing whole-run
            fuel by post-warm-up VMT would inflate it. Runs that also record
            ``fuel_total_ml_post_warmup`` get the windowed ratio.
        wave_count: Number of detected stop-and-go waves.
        wave_speed_kmh: Mean magnitude of backward (upstream-propagating)
            wave-front speeds [km/h], reported positive; NaN if no backward
            wave was detected.
        wave_amplitude_ms: Mean wave amplitude (free speed minus jam minimum)
            across all detected waves [m/s]; NaN if none.
    """

    throughput_veh_h: float
    mean_tt_s: float
    p90_tt_s: float
    sigma_v_spatial_ms: float
    sigma_v_temporal_ms: float
    vmt_veh_km: float
    vht_veh_h: float
    fuel_ml_per_veh_km: float
    wave_count: int
    wave_speed_kmh: float
    wave_amplitude_ms: float
    #: Travel-time sample size; defaults to 0 so tiers without per-vehicle
    #: trajectories (``api.results.macro_metrics``) construct unchanged.
    n_travel_time_veh: int = 0


def geh(m: float, c: float) -> float:
    """GEH statistic for hourly flows: ``√(2(m−c)² / (m+c))``.

    The standard microsimulation link-flow comparison statistic; acceptance
    practice is GEH < 5 for at least 85% of link-hour comparisons (FHWA
    Traffic Analysis Toolbox Vol. III, FHWA-HOP-18-036, 2019; UK DMRB).
    Both arguments must be hourly flows [veh/h] — GEH is not dimensionless
    and is only meaningful on hourly volumes.

    Args:
        m: Modelled hourly flow [veh/h], >= 0.
        c: Observed (count) hourly flow [veh/h], >= 0.

    Returns:
        GEH value; defined as 0 when both flows are exactly zero.

    Raises:
        ValueError: If either flow is negative.
    """
    if m < 0 or c < 0:
        raise ValueError(f"flows must be >= 0, got m={m}, c={c}")
    if m == 0 and c == 0:
        return 0.0
    return math.sqrt(2.0 * (m - c) ** 2 / (m + c))


def rmspe(sim: NDArray[np.float64], obs: NDArray[np.float64]) -> float:
    """Root-mean-square percentage error, returned as a fraction.

    ``rmspe = √(mean(((sim − obs)/obs)²))`` — e.g. ``0.15`` means 15%.
    Used for the segment-speed acceptance criterion (CLAUDE.md §7.1).

    Args:
        sim: Simulated values.
        obs: Observed values, same shape, all nonzero.

    Returns:
        RMSPE as a fraction.

    Raises:
        ValueError: On shape mismatch, empty input, or any zero observation.
    """
    sim_a = np.asarray(sim, dtype=np.float64)
    obs_a = np.asarray(obs, dtype=np.float64)
    if sim_a.shape != obs_a.shape:
        raise ValueError(f"shape mismatch: {sim_a.shape} vs {obs_a.shape}")
    if sim_a.size == 0:
        raise ValueError("rmspe of empty arrays is undefined")
    if np.any(obs_a == 0):
        raise ValueError("rmspe undefined for zero observations")
    return float(np.sqrt(np.mean(((sim_a - obs_a) / obs_a) ** 2)))


def vehicle_codes(veh_id: pd.Series) -> NDArray[np.intp]:
    """Vehicle ids as integer codes in the ids' sort order.

    One factorization (``pd.factorize(sort=True)``) replaces every later
    sort and grouping over the id column: code ``k`` is the ``k``-th smallest
    id under the ordering ``sort_values`` and ``groupby(sort=True)`` use, so
    sorting rows by ``(code, t)`` is the sort by ``(veh_id, t)``, grouping by
    code in ascending order is the sorted groupby, and the codes'
    first-appearance order is the ids'. Every trajectory function here uses
    ``veh_id`` only as a grouping key, so a frame whose ``veh_id`` column
    already holds these codes (``validation.battery.read_scoring_frame``)
    produces the same output as the string frame it came from.

    Args:
        veh_id: The ``veh_id`` column (strings, or codes from an earlier call).

    Returns:
        ``np.intp`` codes, one per row.
    """
    if pd.api.types.is_integer_dtype(veh_id.dtype):
        # Codes already (a frame from ``read_scoring_frame``): dense values
        # ``0 … K−1`` all present are exactly what ``factorize(sort=True)``
        # would return, so the pass over them is skipped. ``to_numpy`` of an
        # integer column is a view; of a string column it would materialize
        # every id as a Python object, which is why the dtype is tested first.
        values = veh_id.to_numpy()
        if values.size:
            lo, hi = int(values.min()), int(values.max())
            if (
                lo >= 0
                and hi < values.size
                and bool(np.all(np.bincount(values, minlength=hi + 1) > 0))
            ):
                return np.asarray(values, dtype=np.intp)
    codes, _ = pd.factorize(veh_id, sort=True)
    return np.asarray(codes, dtype=np.intp)


@dataclass(frozen=True)
class _ByVehicle:
    """Trajectory rows sorted stably by ``(veh_id, t)``, as arrays.

    This is the one sort the crossing count, the travel times and the
    per-vehicle integrations share. It replaces ``sort_values`` copies of the
    frame and ``groupby`` iteration (each of which copied every column,
    ``veh_id`` as an object array) with one permutation and two sorted
    columns.

    Attributes:
        order: Frame row positions in sorted order — ``np.lexsort`` on the
            vehicle codes and ``t``, i.e. stable: rows of one vehicle with
            equal ``t`` keep their frame order, the tie rule of
            ``sort_values(["veh_id", "t"], kind="stable")``.
        t: ``t`` in sorted order.
        x: ``x`` in sorted order.
        starts: Start offset of each vehicle's run of rows, by ascending
            code; the run of code ``k`` is ``[starts[k], starts[k + 1])``.
        same: ``same[i]`` — sorted rows ``i`` and ``i + 1`` belong to one
            vehicle (the consecutive-pair mask of :func:`count_crossings`).
        by_first_row: Vehicle codes in the order the vehicles first appear
            in the frame — the iteration order of ``groupby(sort=False)``,
            which fixes the summation order of every per-vehicle total.
        t_frame: ``t`` in frame order (the tie fallback re-sorts from it).
    """

    order: NDArray[np.intp]
    t: FloatArray
    x: FloatArray
    starts: NDArray[np.intp]
    same: NDArray[np.bool_]
    by_first_row: NDArray[np.intp]
    t_frame: FloatArray

    @classmethod
    def build(cls, codes: NDArray[np.intp], t: FloatArray, x: FloatArray) -> _ByVehicle:
        """Sort once. ``codes`` from :func:`vehicle_codes`; ``t``/``x`` in frame order."""
        n = t.size
        if n and time_ordered(t):
            # Time-ordered rows (the runner writes them so): a stable sort by
            # code alone is the (code, t) sort, at a fraction of the cost.
            order = np.argsort(codes, kind="stable")
        else:
            order = np.lexsort((t, codes))
        order = np.asarray(order, dtype=np.intp)
        sorted_codes = codes[order]
        boundaries = np.flatnonzero(sorted_codes[1:] != sorted_codes[:-1]) + 1
        starts = np.concatenate([np.zeros(1 if n else 0, dtype=np.intp), boundaries])
        starts = np.asarray(starts, dtype=np.intp)
        same = np.ones(max(n - 1, 0), dtype=np.bool_)
        same[boundaries - 1] = False
        del sorted_codes
        first_row = np.minimum.reduceat(order, starts) if n else np.empty(0, dtype=np.intp)
        by_first_row = np.asarray(np.argsort(first_row, kind="stable"), dtype=np.intp)
        return cls(
            order=order,
            t=t[order],
            x=x[order],
            starts=starts,
            same=same,
            by_first_row=by_first_row,
            t_frame=t,
        )

    @classmethod
    def from_frame(cls, trajectories: pd.DataFrame) -> _ByVehicle:
        """Build from a frame with ``t``, ``veh_id`` and ``x`` columns."""
        return cls.build(
            vehicle_codes(trajectories["veh_id"]),
            trajectories["t"].to_numpy(dtype=np.float64),
            trajectories["x"].to_numpy(dtype=np.float64),
        )

    def run(self, code: int) -> slice:
        """The sorted-row run of vehicle ``code``."""
        lo = int(self.starts[code])
        hi = int(self.starts[code + 1]) if code + 1 < self.starts.size else self.order.size
        return slice(lo, hi)

    def rows(self, code: int) -> slice | NDArray[np.intp]:
        """Frame-row positions of one vehicle in the order ``sort_values("t")`` gives.

        A vehicle whose timestamps are distinct has one time order, and it is
        the sorted run. A vehicle with equal timestamps needs the tie rule of
        the per-vehicle ``group.sort_values("t")`` this replaced: a
        ``quicksort`` argsort of the vehicle's rows in frame order (not
        stable) — reproduced by making that call on those rows.

        Returns:
            A slice into the sorted arrays, or frame-row positions to gather.
        """
        run = self.run(code)
        t = self.t[run]
        if t.size > 1 and bool(np.any(t[1:] == t[:-1])):
            frame_rows = np.sort(self.order[run])
            perm = np.argsort(self.t_frame[frame_rows], kind="quicksort")
            return np.asarray(frame_rows[perm], dtype=np.intp)
        return run

    def crossing_times(self, x_ref: float) -> FloatArray:
        """Times of every upward crossing of ``x_ref`` (see :func:`count_crossings`)."""
        if self.order.size < 2:
            return np.empty(0, dtype=np.float64)
        x = self.x
        hit = self.same & (x[:-1] < x_ref) & (x[1:] >= x_ref)
        return np.asarray(self.t[1:][hit], dtype=np.float64)


def _gather(
    rows: slice | NDArray[np.intp], sorted_values: FloatArray, frame_values: FloatArray
) -> FloatArray:
    """One vehicle's column in time order: a view of the sorted column, or a gather
    from the frame-order column when :meth:`_ByVehicle.rows` re-sorted ties."""
    if isinstance(rows, slice):
        return sorted_values[rows]
    return frame_values[rows]


def travel_times(trajectories: pd.DataFrame, x_lo: float, x_hi: float) -> FloatArray:
    """Per-vehicle travel time across the measurement span ``[x_lo, x_hi]``.

    A vehicle's entry time is its first upward crossing of ``x_lo`` (linearly
    interpolated between the bracketing samples); if its first sample is
    already at or past ``x_lo``, the first sample time is used. The exit time
    is the first upward crossing of ``x_hi``. Vehicles that never reach
    ``x_hi``, or whose first sample is already past ``x_hi``, are excluded.

    Args:
        trajectories: Trajectory rows with ``t``, ``x``, ``veh_id`` columns.
        x_lo: Span entry position [m].
        x_hi: Span exit position [m], must exceed ``x_lo``.

    Returns:
        Array of travel times [s], one per vehicle completing the span, in
        the vehicles' first-appearance order.

    Raises:
        ValueError: If ``x_hi <= x_lo`` or required columns are missing.
    """
    if x_hi <= x_lo:
        raise ValueError(f"need x_hi > x_lo, got [{x_lo}, {x_hi}]")
    for col in ("t", "x", "veh_id"):
        if col not in trajectories.columns:
            raise ValueError(f"trajectories missing column {col!r}")
    if trajectories.empty:
        return np.empty(0, dtype=np.float64)
    by = _ByVehicle.from_frame(trajectories)
    x_frame = trajectories["x"].to_numpy(dtype=np.float64)
    return _travel_times(by, x_frame, by.by_first_row, x_lo, x_hi)


def _travel_times(
    by: _ByVehicle,
    x_frame: FloatArray,
    vehicles: NDArray[np.intp],
    x_lo: float,
    x_hi: float,
) -> FloatArray:
    """:func:`travel_times` over ``vehicles`` (codes, in iteration order)."""
    out: list[float] = []
    for code in vehicles:
        rows = by.rows(int(code))
        t = _gather(rows, by.t, by.t_frame)
        x = _gather(rows, by.x, x_frame)
        t_enter = _first_crossing(t, x, x_lo)
        t_exit = _first_crossing(t, x, x_hi)
        if t_enter is None or t_exit is None:
            continue
        if x[0] >= x_hi:
            continue  # first observed already past the span exit
        if t_exit > t_enter:
            out.append(t_exit - t_enter)
    return np.asarray(out, dtype=np.float64)


def _first_crossing(t: FloatArray, x: FloatArray, x_target: float) -> float | None:
    """Time of the first upward crossing of ``x_target``; None if never."""
    if x[0] >= x_target:
        return float(t[0])
    above = np.flatnonzero(x >= x_target)
    if above.size == 0:
        return None
    i = int(above[0])
    dx = x[i] - x[i - 1]
    if dx <= 0:
        return float(t[i])
    frac = (x_target - x[i - 1]) / dx
    return float(t[i - 1] + frac * (t[i] - t[i - 1]))


def _crossing_times(trajectories: pd.DataFrame, x_ref: float) -> FloatArray:
    """Times of every upward crossing of ``x_ref`` (see :func:`count_crossings`).

    Each crossing is stamped with the time of the later sample of the pair
    that brackets ``x_ref``. Vectorised over the whole frame: rows are sorted
    by ``(veh_id, t)`` (:class:`_ByVehicle`) and consecutive same-vehicle
    pairs are tested.
    """
    for col in ("t", "veh_id", "x"):
        if col not in trajectories.columns:
            raise ValueError(f"trajectories missing column {col!r}")
    if len(trajectories) < 2:
        return np.empty(0, dtype=np.float64)
    return _ByVehicle.from_frame(trajectories).crossing_times(x_ref)


def count_crossings(
    trajectories: pd.DataFrame,
    x_ref: float,
    *,
    t_lo: float | None = None,
    t_hi: float | None = None,
) -> int:
    """Number of upward crossings of ``x_ref`` over all vehicles.

    A crossing is a consecutive, time-ordered sample pair of one vehicle
    with ``x_prev < x_ref <= x_cur``. It is stamped with the time of the
    later sample and counted when ``t_lo <= t < t_hi`` (either bound may be
    omitted). On a ring the wrap jump is downward and is therefore not
    counted, so each lap contributes exactly one crossing. This is the
    crossing definition behind :func:`compute_metrics` throughput,
    :func:`crossings_per_window` and :func:`link_hour_geh`, and the one the
    demand adapter (``microsim.demand_adapter``) uses for boundary counts.

    Args:
        trajectories: Rows with ``t`` [s], ``veh_id`` and ``x`` [m] columns
            (docs/CONTRACTS.md §3).
        x_ref: Cross-section position [m] in trajectory coordinates.
        t_lo: Inclusive lower time bound [s]; ``None`` for no bound.
        t_hi: Exclusive upper time bound [s]; ``None`` for no bound.

    Returns:
        Crossing count.

    Raises:
        ValueError: On missing columns or ``t_hi <= t_lo``.
    """
    if t_lo is not None and t_hi is not None and t_hi <= t_lo:
        raise ValueError(f"need t_hi > t_lo, got [{t_lo}, {t_hi})")
    times = _crossing_times(trajectories, x_ref)
    if t_lo is not None:
        times = times[times >= t_lo]
    if t_hi is not None:
        times = times[times < t_hi]
    return int(times.size)


def crossings_per_window(
    trajectories: pd.DataFrame,
    x_ref: float,
    *,
    t_lo: float,
    t_hi: float,
    window_s: float,
) -> NDArray[np.int64]:
    """Crossings of ``x_ref`` binned into consecutive windows of ``window_s``.

    Windows are ``[t_lo + k·window_s, t_lo + (k+1)·window_s)`` for
    ``k = 0 … n−1`` with ``n = (t_hi − t_lo) / window_s``, which must be an
    integer. Crossing semantics are those of :func:`count_crossings`.

    Args:
        trajectories: Rows with ``t``, ``veh_id`` and ``x`` columns.
        x_ref: Cross-section position [m] in trajectory coordinates.
        t_lo: Start of the first window [s] (inclusive).
        t_hi: End of the last window [s] (exclusive).
        window_s: Window length [s], > 0.

    Returns:
        Integer counts, one per window, in time order.

    Raises:
        ValueError: If ``window_s <= 0``, ``t_hi <= t_lo``, or the span is
            not a whole number of windows.
    """
    if window_s <= 0:
        raise ValueError(f"window_s must be > 0, got {window_s}")
    if t_hi <= t_lo:
        raise ValueError(f"need t_hi > t_lo, got [{t_lo}, {t_hi})")
    n_win = round((t_hi - t_lo) / window_s)
    if n_win < 1 or abs(n_win * window_s - (t_hi - t_lo)) > 1e-6:
        raise ValueError(f"[{t_lo}, {t_hi}) s is not a whole number of {window_s} s windows")
    times = _crossing_times(trajectories, x_ref)
    times = times[(times >= t_lo) & (times < t_hi)]
    idx = np.minimum(((times - t_lo) // window_s).astype(np.int64), n_win - 1)
    counts = np.bincount(idx, minlength=n_win)[:n_win]
    return np.asarray(counts, dtype=np.int64)


def warmup_from_meta(meta: Mapping[str, object]) -> float:
    """The run's configured metrics warm-up [s] from its ``meta.json``.

    Reads ``config.sim.warmup_s`` — declared by
    :class:`flowstate_core.config.SimSpec` as "discarded from metrics; still
    simulated and recorded" — and returns 0 when the block is absent or not
    a number. This is the single place the warm-up convention is resolved,
    so :func:`compute_metrics` and :mod:`validation.report` always window
    the same run identically.

    Args:
        meta: Parsed ``meta.json`` of one run (docs/CONTRACTS.md §3).

    Returns:
        Warm-up length [s], >= 0.
    """
    config = meta.get("config")
    sim = config.get("sim") if isinstance(config, dict) else None
    value = sim.get("warmup_s") if isinstance(sim, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0.0
    warm = float(value)
    return warm if math.isfinite(warm) and warm > 0.0 else 0.0


def default_travel_span(trajectories: pd.DataFrame) -> tuple[float, float]:
    """Default travel-time measurement span ``(x_lo, x_hi)`` [m] for a frame.

    ``x_lo`` is the smallest observed position; ``x_hi`` is the **median** of
    the per-vehicle furthest observed position. The median is a bound at
    least half the observed vehicles reach by construction, which the global
    maximum is not: only the single farthest vehicle attains that, so a
    ``(x_min, x_max)`` span yields a one-vehicle "fleet" travel time (its
    tell is ``mean_tt_s == p90_tt_s``). A high quantile is required, not a
    low one — a low quantile silently shortens the measured corridor.

    Args:
        trajectories: Trajectory rows with ``veh_id`` and ``x`` columns.

    Returns:
        ``(x_lo, x_hi)``; ``x_hi <= x_lo`` is possible for degenerate frames
        (e.g. a single sample per vehicle) and means no span is measurable.

    Raises:
        ValueError: If required columns are missing or the frame is empty.
    """
    for col in ("veh_id", "x"):
        if col not in trajectories.columns:
            raise ValueError(f"trajectories missing column {col!r}")
    if trajectories.empty:
        raise ValueError("cannot derive a travel-time span from an empty frame")
    x = trajectories["x"].to_numpy(dtype=np.float64)
    return _travel_span(vehicle_codes(trajectories["veh_id"]), x, np.ones(x.size, dtype=np.bool_))


def _travel_span(
    codes: NDArray[np.intp], x: FloatArray, rows: NDArray[np.bool_]
) -> tuple[float, float]:
    """:func:`default_travel_span` over the rows flagged in ``rows`` (frame order).

    The per-vehicle furthest position skips NaN like the groupby ``max`` it
    replaces (NaN only for a vehicle observed at no finite position); the
    median does not depend on the vehicles' order.
    """
    x_rows = x[rows]
    codes_rows = codes[rows]
    known = ~np.isnan(x_rows)
    x_lo = float(np.nanmin(x_rows)) if bool(known.any()) else math.nan
    n_veh = int(codes.max()) + 1 if codes.size else 0
    per_veh_max = np.full(n_veh, -np.inf, dtype=np.float64)
    present = np.zeros(n_veh, dtype=np.bool_)
    present[codes_rows] = True
    if bool(known.all()):
        np.fmax.at(per_veh_max, codes_rows, x_rows)
    else:
        seen = np.zeros(n_veh, dtype=np.bool_)
        seen[codes_rows[known]] = True
        np.fmax.at(per_veh_max, codes_rows[known], x_rows[known])
        per_veh_max[~seen] = np.nan
    return x_lo, float(np.median(per_veh_max[present]))


def time_ordered(t: FloatArray) -> bool:
    """Whether ``t`` is non-decreasing — the order the runner writes rows in.

    Args:
        t: Sample times [s] in frame order.

    Returns:
        True for an empty or non-decreasing array (a NaN makes it False).
    """
    return bool(t.size == 0 or np.all(t[1:] >= t[:-1]))


def time_window_rows(
    t: FloatArray, t_lo: float, t_hi: float | None = None, *, ordered: bool | None = None
) -> slice | NDArray[np.bool_]:
    """Rows with ``t_lo <= t`` (and ``t < t_hi`` when given), in frame order.

    The same rows in the same order as the mask ``(t >= t_lo) & (t < t_hi)``
    selects — as a slice, which indexes to a view, when ``t`` is
    non-decreasing (:func:`time_ordered`), else as that mask. Selecting a
    window of an 80 M-row corridor trajectory by slice costs nothing; by
    mask it copies every column the caller indexes.

    Args:
        t: Sample times [s] in frame order.
        t_lo: Inclusive lower bound [s].
        t_hi: Exclusive upper bound [s]; ``None`` for no bound.
        ordered: :func:`time_ordered` of ``t`` when the caller already knows
            it (one check serves many windows); ``None`` checks here.

    Returns:
        A slice or a boolean mask over the rows.
    """
    if ordered is None:
        ordered = time_ordered(t)
    if ordered:
        lo = int(np.searchsorted(t, t_lo, side="left"))
        hi = t.size if t_hi is None else int(np.searchsorted(t, t_hi, side="left"))
        return slice(lo, max(hi, lo))
    mask = t >= t_lo
    if t_hi is not None:
        mask &= t < t_hi
    return np.asarray(mask, dtype=np.bool_)


def n_window_rows(rows: slice | NDArray[np.bool_]) -> int:
    """Number of rows a :func:`time_window_rows` selection holds.

    Args:
        rows: The selection.

    Returns:
        Its row count.
    """
    if isinstance(rows, slice):
        return int(rows.stop - rows.start)
    return int(np.count_nonzero(rows))


def compute_metrics(
    run_dir: str | Path,
    x_ref: float | None = None,
    span: tuple[float, float] | None = None,
    dt_bin: float = 15.0,
    dx_bin: float = 75.0,
    v_jam_thresh: float = V_JAM_THRESH,
    min_area_bins: int = 4,
    warmup_s: float | None = None,
    *,
    trajectories: pd.DataFrame | None = None,
) -> Metrics:
    """Compute the standard metric set for one run directory.

    Reads ``trajectories.parquet`` and ``meta.json`` per docs/CONTRACTS.md
    §3. Definitions of every metric are on :class:`Metrics`. Wave metrics
    come from :func:`validation.waves.detect_waves` on the binned speed
    field. Fuel is taken from the meta key ``fuel_total_ml`` (total run fuel
    [ml], SUMO HBEFA4 totals) when present, else NaN.

    **Measurement window.** The configured warm-up is discarded, as
    :class:`flowstate_core.config.SimSpec` documents it to be: with
    ``t_lo`` the warm-up end, throughput counts only crossings at
    ``t >= t_lo`` over the window length; σ_v, VMT/VHT and the wave field
    use only rows at ``t >= t_lo``; travel times keep **whole journeys**
    (vehicles whose first recorded sample is at ``t >= t_lo``), because a
    row-level filter would clip the entry time of vehicles already in
    flight and understate travel time. Fuel per vehicle-km stays a
    whole-run ratio unless the run records ``fuel_total_ml_post_warmup``
    (see :class:`Metrics`). On a closed ring every vehicle is present from
    ``t = 0``, so a nonzero warm-up leaves no whole journey and the travel
    times are undefined (``n_travel_time_veh == 0``) — the ring's headline
    metrics are σ_v and the wave set, not travel time.

    **Memory.** The frame is reduced to arrays once — vehicle codes
    (:func:`vehicle_codes`) and one ``(veh_id, t)`` sort
    (:class:`_ByVehicle`) — and every per-vehicle quantity is read from
    views of that sort, so no copy of the frame is made and nothing but the
    window's own columns is alive during the σ_v and wave phases. Measured
    2026-09-24 on a 3.9 M-row synthetic corridor trajectory (see
    ``validation.battery.SCORE_WORKER_BYTES_PER_ROW``): the previous
    implementation peaked at 341 B per row (sorted copies, ``veh_id`` as an
    object array, two ``groupby`` iterations each taking a sorted copy);
    this one at 147 B per row, most of it the parquet read itself, and at
    about 35 B per row above a frame the caller already holds.

    Args:
        run_dir: Directory holding ``trajectories.parquet`` and ``meta.json``.
        x_ref: Reference cross-section for throughput [m]; ``None`` uses the
            midpoint of the position range observed in the measurement
            window.
        span: Measurement span ``(x_lo, x_hi)`` [m] for travel times;
            ``None`` uses :func:`default_travel_span` of the measurement
            window (smallest observed position → median per-vehicle furthest
            position).
        dt_bin: Speed-field time bin [s] for wave detection.
        dx_bin: Speed-field space bin [m] for wave detection.
        v_jam_thresh: Jam threshold [m/s] for wave detection.
        min_area_bins: Minimum wave component size in bins.
        warmup_s: Warm-up to discard [s]; ``None`` (the default) takes the
            run's own ``config.sim.warmup_s`` (:func:`warmup_from_meta`).
            Pass ``0.0`` to measure over the whole recorded run.
        trajectories: The run's trajectory rows (``t``, ``veh_id``, ``x``,
            ``v``), for a caller that already holds them
            (``validation.battery.analyse_replicate`` reads them once for
            three measurements); ``None`` reads ``trajectories.parquet``.

    Returns:
        A :class:`Metrics` instance.

    Raises:
        FileNotFoundError: If either input file is missing.
        ValueError: If the trajectory frame is empty, ``warmup_s`` is
            negative, or the warm-up leaves no measurement window.
    """
    run_path = Path(run_dir)
    traj_path = run_path / "trajectories.parquet"
    meta_path = run_path / "meta.json"
    if trajectories is None and not traj_path.is_file():
        raise FileNotFoundError(f"missing {traj_path}")
    if not meta_path.is_file():
        raise FileNotFoundError(f"missing {meta_path}")
    if trajectories is None:
        # Only the columns the metrics use: a 7,800 s corridor run holds
        # ~10 M rows, and the full eight-column frame plus groupby copies
        # peaked at ~7 GB of RSS in the report generator (part of what
        # crashed a 16 GB machine during the I-24 validation).
        traj = pd.read_parquet(traj_path, columns=["t", "veh_id", "x", "v"])
    else:
        traj = trajectories
    if traj.empty:
        raise ValueError(f"{traj_path} holds no trajectory rows")
    meta = json.loads(meta_path.read_text())

    t_all = traj["t"].to_numpy(dtype=np.float64)
    x_all = traj["x"].to_numpy(dtype=np.float64)
    v_all = traj["v"].to_numpy(dtype=np.float64)
    codes = vehicle_codes(traj["veh_id"])
    del traj
    t_start, t_end = float(t_all.min()), float(t_all.max())

    # Measurement window: the recorded run minus its configured warm-up.
    warm = warmup_from_meta(meta) if warmup_s is None else float(warmup_s)
    if not math.isfinite(warm) or warm < 0.0:
        raise ValueError(f"warmup_s must be finite and >= 0, got {warmup_s!r}")
    t_lo = max(warm, t_start)
    if t_lo >= t_end:
        raise ValueError(
            f"warm-up {warm:g} s leaves no measurement window in a run recorded over "
            f"[{t_start:g}, {t_end:g}] s: every metric would describe the warm-up the "
            "configuration says to discard. Lengthen the run, lower sim.warmup_s, or "
            "pass warmup_s=0.0 to measure the whole record deliberately."
        )
    windowed = warm > t_start
    win = time_window_rows(t_all, t_lo) if windowed else slice(0, t_all.size)

    x_win = x_all[win]
    x_min, x_max = float(x_win.min()), float(x_win.max())
    del x_win
    if x_ref is None:
        x_ref = 0.5 * (x_min + x_max)
    default_span = span is None
    if span is None:
        in_window = np.zeros(t_all.size, dtype=np.bool_)
        in_window[win] = True
        span = _travel_span(codes, x_all, in_window)
        del in_window

    by = _ByVehicle.build(codes, t_all, x_all)

    # Throughput at the reference cross-section, over the measurement window.
    t_span_s = t_end - t_lo
    times = by.crossing_times(x_ref)
    crossings = int(np.count_nonzero(times >= t_lo))
    del times
    throughput = veh_s_to_veh_h(crossings / t_span_s) if t_span_s > 0 else math.nan

    # Travel times over the measurement span, whole journeys only: a vehicle
    # already in flight at t_lo would otherwise be credited an entry time of
    # t_lo and report a truncated travel time.
    if windowed:
        whole = by.t[by.starts] >= t_lo
        vehicles = by.by_first_row[whole[by.by_first_row]]
    else:
        vehicles = by.by_first_row
    if vehicles.size == 0 or (default_span and span[1] <= span[0]):
        tts = np.empty(0, dtype=np.float64)
    elif span[1] <= span[0]:
        raise ValueError(f"need x_hi > x_lo, got [{span[0]}, {span[1]}]")
    else:
        tts = _travel_times(by, x_all, vehicles, span[0], span[1])
    # One completing vehicle is a sample, not a fleet mean (it also makes
    # p90 equal the mean); report the sample size and leave both undefined.
    n_tt = int(tts.size)
    mean_tt = float(tts.mean()) if n_tt >= 2 else math.nan
    p90_tt = float(np.percentile(tts, 90)) if n_tt >= 2 else math.nan

    # VMT / VHT via trapezoid integration of sampled speeds. The whole-run
    # VMT is accumulated in the same pass: it is the denominator of a
    # whole-run fuel total (see Metrics.fuel_ml_per_veh_km). Vehicles in
    # first-appearance order, as the groupby iteration summed them.
    vmt_km = 0.0
    vht_h = 0.0
    vmt_km_whole = 0.0
    for code in by.by_first_row:
        rows = by.rows(int(code))
        t = _gather(rows, by.t, by.t_frame)
        v = v_all[by.order[rows]] if isinstance(rows, slice) else v_all[rows]
        if len(t) < 2:
            continue
        mid_v = 0.5 * (v[:-1] + v[1:])
        dt = np.diff(t)
        segments = mid_v * dt
        vmt_km_whole += _KM_PER_M * float(np.sum(segments))
        if not windowed:
            vmt_km += _KM_PER_M * float(np.sum(segments))
            vht_h += s_to_h(float(t[-1] - t[0]))
            continue
        # Segments of the window are exactly those whose earlier sample is
        # in it (times increase), i.e. the trapezoid sum over window rows.
        vmt_km += _KM_PER_M * float(np.sum(segments[t[:-1] >= t_lo]))
        first = int(np.searchsorted(t, t_lo, side="left"))
        if len(t) - first >= 2:
            vht_h += s_to_h(float(t[-1] - t[first]))
    del by

    # σ_v spatial: std across vehicles at each shared output timestamp.
    # σ_v temporal: std over time per vehicle. Both group the window's rows
    # in frame order (the accumulation order of the groupby they replace).
    t_w = t_all[win]
    v_w = pd.Series(v_all[win], copy=False)
    by_t = v_w.groupby(t_w)
    spatial_stds = by_t.std(ddof=1)[by_t.count() >= 2]
    sigma_spatial = float(spatial_stds.mean()) if len(spatial_stds) else math.nan
    del by_t, spatial_stds
    by_veh = v_w.groupby(codes[win])
    temporal_stds = by_veh.std(ddof=1)[by_veh.count() >= 2]
    sigma_temporal = float(temporal_stds.mean()) if len(temporal_stds) else math.nan
    del by_veh, temporal_stds, v_w, codes

    # Fuel per vehicle-km from meta totals, when recorded. A whole-run total
    # keeps a whole-run denominator; a windowed total gets the window's VMT.
    fuel_windowed = meta.get("fuel_total_ml_post_warmup")
    fuel_total = meta.get("fuel_total_ml")
    if fuel_windowed is not None and vmt_km > 0:
        fuel_per_km = float(fuel_windowed) / vmt_km
    elif fuel_total is not None and vmt_km_whole > 0:
        fuel_per_km = float(fuel_total) / vmt_km_whole
    else:
        fuel_per_km = math.nan

    # Wave metrics from the binned speed field of the window's rows.
    window = pd.DataFrame({"t": t_w, "x": x_all[win], "v": v_all[win]}, copy=False)
    del t_w, t_all, x_all, v_all
    field = speed_field(window, dt_bin=dt_bin, dx_bin=dx_bin)
    del window
    wave_set = detect_waves(field, v_jam_thresh=v_jam_thresh, min_area_bins=min_area_bins)
    backward = wave_set.backward()
    if backward:
        wave_speed_kmh = float(np.mean([ms_to_kmh(-w.speed_ms) for w in backward]))
    else:
        wave_speed_kmh = math.nan
    if wave_set.count:
        wave_amp = float(np.mean([w.amplitude_ms for w in wave_set.waves]))
    else:
        wave_amp = math.nan

    return Metrics(
        throughput_veh_h=throughput,
        mean_tt_s=mean_tt,
        p90_tt_s=p90_tt,
        sigma_v_spatial_ms=sigma_spatial,
        sigma_v_temporal_ms=sigma_temporal,
        vmt_veh_km=vmt_km,
        vht_veh_h=vht_h,
        fuel_ml_per_veh_km=fuel_per_km,
        wave_count=wave_set.count,
        wave_speed_kmh=wave_speed_kmh,
        wave_amplitude_ms=wave_amp,
        n_travel_time_veh=n_tt,
    )


def aggregate(metrics_list: list[Metrics]) -> dict[str, CI]:
    """Aggregate replicate metrics into per-field t-distribution CIs.

    For each numeric field of :class:`Metrics`, NaN replicate values are
    dropped (e.g. fuel unrecorded in some runs), then the two-sided
    :data:`CI_LEVEL` confidence interval ``mean ± t_{α/2, n−1}·s/√n`` is
    computed over the remaining ``n`` values. With ``n == 1`` the bounds are
    NaN; with ``n == 0`` the mean is NaN too. Each :class:`CI` carries an
    ``underpowered`` flag that is True when ``n <`` :data:`MIN_REPLICATES`
    (CLAUDE.md §0.6) — such values must not be quoted as headline results.

    Args:
        metrics_list: Metrics from replicate runs of one configuration.

    Returns:
        Mapping from field name to :class:`CI`.

    Raises:
        ValueError: If ``metrics_list`` is empty.
    """
    if not metrics_list:
        raise ValueError("metrics_list is empty")
    return {
        f.name: ci([float(getattr(m, f.name)) for m in metrics_list])
        for f in dataclasses.fields(Metrics)
    }


def ci(values: Sequence[float]) -> CI:
    """Two-sided t-distribution interval over replicate values.

    The convention :func:`aggregate` applies field by field, exposed for
    replicate quantities that are not :class:`Metrics` fields (a per-seed GEH
    pass fraction, RMSPE or criterion wave speed): NaN values are dropped —
    a replicate that produced no reading contributes nothing rather than a
    zero — then the interval is ``mean ± t_{α/2, n−1}·s/√n`` at
    :data:`CI_LEVEL`. With ``n == 1`` the bounds are NaN; with ``n == 0`` the
    mean is NaN too.

    Args:
        values: One value per replicate.

    Returns:
        The :class:`CI`; its ``underpowered`` flag marks ``n <``
        :data:`MIN_REPLICATES` (CLAUDE.md §0.6).
    """
    finite = np.asarray([float(v) for v in values], dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    n = int(finite.size)
    if n == 0:
        return CI(math.nan, math.nan, math.nan, 0)
    mean = float(finite.mean())
    if n == 1:
        return CI(mean, math.nan, math.nan, 1)
    half = float(student_t.ppf(0.5 + CI_LEVEL / 2.0, n - 1) * finite.std(ddof=1) / math.sqrt(n))
    return CI(mean, mean - half, mean + half, n)


def geh_pass_fraction(geh_values: Sequence[float], threshold: float = 5.0) -> float:
    """Fraction of comparisons with ``GEH < threshold`` (strict).

    NaN entries never satisfy the bound and therefore count as failing —
    the honest reading of a comparison that could not be formed.

    Args:
        geh_values: Per-comparison GEH statistics.
        threshold: Strict upper bound; 5 is the FHWA/DOT convention
            (``validation.criteria`` carries the sourced profiles).

    Returns:
        Fraction in ``[0, 1]``.

    Raises:
        ValueError: If no comparisons are supplied.
    """
    if len(geh_values) == 0:
        raise ValueError("geh_pass_fraction of no comparisons is undefined")
    return sum(1 for g in geh_values if g < threshold) / len(geh_values)


@dataclass(frozen=True)
class LinkHourGEH:
    """Per-link-window GEH comparison (CLAUDE.md §7.1 link-flow criterion).

    One entry per matched ``(x_ref_m, window_start_s)`` bin, in the order of
    the observed rows (rows whose observed flow was NaN are dropped and
    counted in ``n_dropped_nan``).

    Attributes:
        geh: GEH statistic per bin, on hourly-equivalent volumes.
        x_ref_m: Cross-section of each bin [m].
        window_start_s: Window start of each bin [s].
        sim_veh_h: Simulated hourly-equivalent flow per bin [veh/h].
        obs_veh_h: Observed hourly-equivalent flow per bin [veh/h].
        window_s: Window length used for every bin [s].
        n_dropped_nan: Observed rows dropped because their flow was NaN.
        n_dropped_outside_span: Observed rows dropped because their
            cross-section lies outside the simulated position span.
        x_outside_span_m: The dropped cross-sections [m], each once, in the
            order they were met.
    """

    geh: tuple[float, ...]
    x_ref_m: tuple[float, ...]
    window_start_s: tuple[float, ...]
    sim_veh_h: tuple[float, ...]
    obs_veh_h: tuple[float, ...]
    window_s: float
    n_dropped_nan: int
    n_dropped_outside_span: int = 0
    x_outside_span_m: tuple[float, ...] = ()

    def pass_fraction(self, threshold: float = 5.0) -> float:
        """Fraction of bins with ``GEH < threshold`` (:func:`geh_pass_fraction`)."""
        return geh_pass_fraction(self.geh, threshold)


def link_hour_geh(
    sim: pd.DataFrame,
    observed: pd.DataFrame,
    *,
    x_refs_m: Sequence[float],
    window_s: float = 3600.0,
    sim_span: tuple[float, float] | None = None,
) -> LinkHourGEH:
    """GEH per link-window between simulated crossings and observed counts.

    For every observed row, the simulated crossings of its cross-section
    inside ``[window_start_s, window_start_s + window_s)`` are counted with
    :func:`count_crossings`, scaled to an hourly-equivalent flow through
    ``flowstate_core.units`` (``veh_s_to_veh_h(n / window_s)``), and compared
    with the observed hourly-equivalent flow by :func:`geh`. GEH is only
    meaningful on hourly volumes; a shorter ``window_s`` (e.g. the 300 s
    windows of the I-24 comparison) scales both sides identically, which is
    the usual practice, but the resulting values are hourly-*equivalent* and
    the report must say so.

    Args:
        sim: Trajectory rows with ``t`` [s], ``veh_id`` and ``x`` [m]
            columns, in the same coordinates as ``observed``.
        observed: Rows with ``x_ref_m`` [m], ``window_start_s`` [s] and
            ``flow_veh_h`` [veh/h] columns; every ``x_ref_m`` must be one of
            ``x_refs_m`` (a guard against coordinate mix-ups).
        x_refs_m: Cross-sections the comparison may use [m].
        window_s: Window length [s] shared by every observed row.
        sim_span: The recorded simulation span ``(t_lo, t_hi)`` [s] every
            observed window must lie inside. ``None`` infers it from the
            frame's own smallest and largest ``t``, which is right for a
            measured dataset but rejects a run's final window whenever the
            output grid's last sample falls short of the nominal end (a 3600 s
            run sampled at 1 Hz ends at t = 3599 s). A caller that knows the
            recorded span — the scenario's duration — states it here; the
            containment check, and with it the guard against comparing
            windows the run never covered, is unchanged.

    A cross-section outside ``[x.min(), x.max()]`` of ``sim`` can be crossed
    by no simulated vehicle, so comparing it would report a simulated flow of
    zero and a GEH of ``√(2·q_obs)`` — an arbitrarily failing link-hour that
    measures the corridor's extent, not the model. Such rows are dropped and
    counted in ``n_dropped_outside_span`` / ``x_outside_span_m`` instead; it is
    the caller's business (and the report's) to say what was excluded.

    Returns:
        A :class:`LinkHourGEH` with one GEH per matched bin.

    Raises:
        ValueError: On missing columns, an empty simulation, a negative
            observed flow, a non-increasing ``sim_span``, an observed
            cross-section absent from ``x_refs_m``, or an observed window not
            fully covered by the simulated time span (an unmatched bin is an
            error, never a silent skip).
    """
    if window_s <= 0:
        raise ValueError(f"window_s must be > 0, got {window_s}")
    x_refs = [float(x) for x in x_refs_m]
    if not x_refs:
        raise ValueError("x_refs_m is empty")
    for col in ("x_ref_m", "window_start_s", "flow_veh_h"):
        if col not in observed.columns:
            raise ValueError(f"observed missing column {col!r}")
    for col in ("t", "veh_id", "x"):
        if col not in sim.columns:
            raise ValueError(f"sim missing column {col!r}")
    if sim.empty:
        raise ValueError("sim holds no trajectory rows")
    tol = 1e-6
    if sim_span is None:
        t_min = float(sim["t"].min())
        t_max = float(sim["t"].max())
    else:
        t_min, t_max = float(sim_span[0]), float(sim_span[1])
        if t_max <= t_min:
            raise ValueError(f"sim_span must be increasing, got [{t_min}, {t_max}]")
    obs_x = observed["x_ref_m"].to_numpy(dtype=np.float64)
    obs_w = observed["window_start_s"].to_numpy(dtype=np.float64)
    obs_q = observed["flow_veh_h"].to_numpy(dtype=np.float64)
    keep = np.isfinite(obs_q)
    n_dropped = int(np.count_nonzero(~keep))
    sim_x = np.asarray(sim["x"].to_numpy(), dtype=np.float64)
    x_lo, x_hi = float(np.nanmin(sim_x)), float(np.nanmax(sim_x))
    outside: list[float] = []
    n_outside = 0

    by: _ByVehicle | None = None
    crossing_times: dict[int, FloatArray] = {}
    gehs: list[float] = []
    xs: list[float] = []
    ws: list[float] = []
    sims: list[float] = []
    obss: list[float] = []
    for x_o, w_o, q_o in zip(obs_x[keep], obs_w[keep], obs_q[keep], strict=True):
        matches = [i for i, x in enumerate(x_refs) if math.isclose(x, x_o, abs_tol=tol)]
        if not matches:
            raise ValueError(f"observed cross-section x = {x_o} m is not in x_refs_m")
        if q_o < 0:
            raise ValueError(f"observed flow must be >= 0, got {q_o} veh/h at x = {x_o} m")
        if x_o < x_lo - tol or x_o > x_hi + tol:
            n_outside += 1
            if not any(math.isclose(x, x_o, abs_tol=tol) for x in outside):
                outside.append(float(x_o))
            continue
        if w_o < t_min - tol or w_o + window_s > t_max + tol:
            raise ValueError(
                f"observed window [{w_o}, {w_o + window_s}) s at x = {x_o} m is not "
                f"covered by the simulated span [{t_min}, {t_max}] s"
            )
        i = matches[0]
        if i not in crossing_times:
            if by is None:
                by = _ByVehicle.from_frame(sim)  # one sort for every cross-section
            crossing_times[i] = by.crossing_times(x_refs[i])
        times = crossing_times[i]
        n = int(np.count_nonzero((times >= w_o) & (times < w_o + window_s)))
        q_sim = veh_s_to_veh_h(n / window_s)
        gehs.append(geh(q_sim, float(q_o)))
        xs.append(x_refs[i])
        ws.append(float(w_o))
        sims.append(q_sim)
        obss.append(float(q_o))
    return LinkHourGEH(
        geh=tuple(gehs),
        x_ref_m=tuple(xs),
        window_start_s=tuple(ws),
        sim_veh_h=tuple(sims),
        obs_veh_h=tuple(obss),
        window_s=float(window_s),
        n_dropped_nan=n_dropped,
        n_dropped_outside_span=n_outside,
        x_outside_span_m=tuple(outside),
    )
