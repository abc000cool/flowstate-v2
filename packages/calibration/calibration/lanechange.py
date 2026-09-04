"""Lane-change observables and objective — the lane-change model as a
calibration target (CLAUDE.md §6, docs/CONTRACTS.md §2 and §5).

The I-24 merge diagnostic (docs/I24_CAPACITY.md §6) isolated the replica's
residual as merge *behaviour* — how readily mainline drivers open gaps and
ramp drivers accept them — which SUMO's LC2013 model governs through
``lcCooperative``, ``lcAssertive``, ``lcSpeedGain`` and ``lcKeepRight``
(``FleetSpec.lc_*``). Those parameters have no car-following analogue that
can be fitted per driver from gap records, so they are calibrated against
*aggregate lane observables* that the same trajectory table yields on both
sides of the comparison:

1. **Lane-use share** per section: vehicle-time in each mainline lane
   divided by the section's total mainline vehicle-time.
2. **Lane-change rate** per section: held mainline-to-mainline lane changes
   per vehicle-kilometre travelled in mainline lanes.
3. **Lane-change location distribution**: a fine histogram of where the
   changes happen, read against the ramp gores.

Every function here is a pure function of a trajectories-like frame with
columns ``t, veh_id, x, lane, v`` (docs/CONTRACTS.md §3; ``v`` is not used
but the contract carries it) and applies identically to observed fragments
(I-24 MOTION, ``lane`` an int8 band index with 1 = leftmost mainline lane and
≥ 5 auxiliary/ramp lanes) and to simulated trajectories once their SUMO lane
index has been mapped into the same band convention
(:func:`band_lane_from_sim`). Definitions are stated on
:func:`lane_observables`; the objective on :func:`lane_change_objective`.

Coverage note (docs/I24_DATA.md §4): the instrument tracks roughly half of
the vehicle-time in the peak, so observed vehicle-time, vehicle-kilometres
and change counts are all lower bounds. Both observables used for fitting
are *ratios* of such quantities and are coverage-robust to first order; a
lane change that happens while a vehicle is untracked is lost together with
the vehicle-kilometres around it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from flowstate_core.artifacts import LaneObservablesRecord

DEFAULT_LANES: tuple[int, ...] = (1, 2, 3, 4)
"""Mainline lanes in the band convention (1 = leftmost / HOV on I-24)."""

DEFAULT_MIN_DWELL_S: float = 1.0
"""Shortest stay in a lane that counts as having been in it (flicker guard)."""

DEFAULT_MAX_GAP_FACTOR: float = 2.5
"""``max_gap_s`` default as a multiple of the sampling interval: one missing
sample is bridged, two consecutive missing samples break the sequence."""

DEFAULT_HIST_DX_M: float = 100.0
"""Bin width of the lane-change location histogram."""

DEFAULT_RATE_WEIGHT: float = 0.1
"""Weight of the rate term in :func:`lane_change_objective`."""


@dataclass
class LaneObservables:
    """Additive lane observables of one trajectory set on one time window.

    Every stored array is a count (vehicle-time, vehicle-km, changes) so two
    objects computed on disjoint time windows of the same sections add
    exactly (``a + b``); the shares and rates are derived on demand. See
    :func:`lane_observables` for the definitions and
    :class:`flowstate_core.artifacts.LaneObservablesRecord` for the JSON
    form.

    Attributes:
        x_edges_m: Section edges [m], ``n_sec + 1`` increasing values.
        lanes: Mainline lane ids (band convention), column order of
            ``veh_time_s``.
        dt_s: Sampling interval [s] that turned sample counts into time.
        veh_time_s: ``[n_sec, n_lanes]`` vehicle-time per section and lane.
        veh_km: ``[n_sec]`` vehicle-kilometres in mainline lanes.
        n_changes: ``[n_sec]`` held lane changes located in each section.
        n_changes_left: ``[n_sec]`` of those toward smaller lane ids.
        n_changes_right: ``[n_sec]`` of those toward larger lane ids.
        change_hist_edges_m: Fine histogram edges of change locations.
        change_hist: ``[n_bins]`` change counts per fine bin.
        n_samples: Mainline samples counted (inside the window and span).
        window_s: Half-open window the counts were restricted to, or None.
    """

    x_edges_m: NDArray[np.float64]
    lanes: tuple[int, ...]
    dt_s: float
    veh_time_s: NDArray[np.float64]
    veh_km: NDArray[np.float64]
    n_changes: NDArray[np.int64]
    n_changes_left: NDArray[np.int64]
    n_changes_right: NDArray[np.int64]
    change_hist_edges_m: NDArray[np.float64]
    change_hist: NDArray[np.int64]
    n_samples: int
    window_s: tuple[float, float] | None = None

    @property
    def n_sections(self) -> int:
        """Number of sections."""
        return len(self.x_edges_m) - 1

    @property
    def lane_share(self) -> NDArray[np.float64]:
        """``[n_sec, n_lanes]`` vehicle-time shares; NaN where a section is empty."""
        total = self.veh_time_s.sum(axis=1, keepdims=True)
        out = np.full_like(self.veh_time_s, np.nan)
        np.divide(self.veh_time_s, total, out=out, where=total > 0.0)
        return out

    @property
    def changes_per_veh_km(self) -> NDArray[np.float64]:
        """``[n_sec]`` lane changes per vehicle-km; NaN where no travel."""
        out = np.full(self.n_sections, np.nan)
        np.divide(self.n_changes.astype(np.float64), self.veh_km, out=out, where=self.veh_km > 0.0)
        return out

    @property
    def change_location_share(self) -> NDArray[np.float64]:
        """``[n_sec]`` fraction of all located changes per section; NaN if none."""
        total = int(self.n_changes.sum())
        if total == 0:
            return np.full(self.n_sections, np.nan)
        return self.n_changes.astype(np.float64) / total

    def __add__(self, other: LaneObservables) -> LaneObservables:
        """Sum two observables of the same sections/lanes (disjoint windows)."""
        if not np.array_equal(self.x_edges_m, other.x_edges_m) or self.lanes != other.lanes:
            raise ValueError("cannot add observables with different sections or lanes")
        if not np.array_equal(self.change_hist_edges_m, other.change_hist_edges_m):
            raise ValueError("cannot add observables with different histogram bins")
        if not math.isclose(self.dt_s, other.dt_s):
            raise ValueError("cannot add observables with different sampling intervals")
        window: tuple[float, float] | None = None
        if self.window_s is not None and other.window_s is not None:
            window = (
                min(self.window_s[0], other.window_s[0]),
                max(self.window_s[1], other.window_s[1]),
            )
        return LaneObservables(
            x_edges_m=self.x_edges_m.copy(),
            lanes=self.lanes,
            dt_s=self.dt_s,
            veh_time_s=self.veh_time_s + other.veh_time_s,
            veh_km=self.veh_km + other.veh_km,
            n_changes=self.n_changes + other.n_changes,
            n_changes_left=self.n_changes_left + other.n_changes_left,
            n_changes_right=self.n_changes_right + other.n_changes_right,
            change_hist_edges_m=self.change_hist_edges_m.copy(),
            change_hist=self.change_hist + other.change_hist,
            n_samples=self.n_samples + other.n_samples,
            window_s=window,
        )

    def to_record(self) -> LaneObservablesRecord:
        """JSON-able form (docs/CONTRACTS.md §5), derived tables included."""
        share = self.lane_share
        rate = self.changes_per_veh_km
        return LaneObservablesRecord(
            window_s=self.window_s,
            x_edges_m=[float(v) for v in self.x_edges_m],
            lanes=[int(lane) for lane in self.lanes],
            dt_s=float(self.dt_s),
            veh_time_s=[[float(v) for v in row] for row in self.veh_time_s],
            veh_km=[float(v) for v in self.veh_km],
            n_changes=[int(v) for v in self.n_changes],
            n_changes_left=[int(v) for v in self.n_changes_left],
            n_changes_right=[int(v) for v in self.n_changes_right],
            change_hist_edges_m=[float(v) for v in self.change_hist_edges_m],
            change_hist=[int(v) for v in self.change_hist],
            n_samples=int(self.n_samples),
            lane_share=[[None if np.isnan(v) else float(v) for v in row] for row in share],
            changes_per_veh_km=[None if np.isnan(v) else float(v) for v in rate],
        )

    @classmethod
    def from_record(cls, rec: LaneObservablesRecord) -> LaneObservables:
        """Inverse of :meth:`to_record`."""
        return cls(
            x_edges_m=np.asarray(rec.x_edges_m, dtype=np.float64),
            lanes=tuple(rec.lanes),
            dt_s=rec.dt_s,
            veh_time_s=np.asarray(rec.veh_time_s, dtype=np.float64),
            veh_km=np.asarray(rec.veh_km, dtype=np.float64),
            n_changes=np.asarray(rec.n_changes, dtype=np.int64),
            n_changes_left=np.asarray(rec.n_changes_left, dtype=np.int64),
            n_changes_right=np.asarray(rec.n_changes_right, dtype=np.int64),
            change_hist_edges_m=np.asarray(rec.change_hist_edges_m, dtype=np.float64),
            change_hist=np.asarray(rec.change_hist, dtype=np.int64),
            n_samples=rec.n_samples,
            window_s=rec.window_s,
        )


def infer_dt(t: NDArray[np.float64], veh: NDArray[np.int64]) -> float:
    """Sampling interval [s]: median positive step between a vehicle's samples.

    Args:
        t: Sample times sorted within vehicle.
        veh: Integer vehicle codes aligned with ``t`` (sorted by vehicle).

    Raises:
        ValueError: If no vehicle has two samples.
    """
    same = veh[1:] == veh[:-1]
    dt = np.diff(t)[same]
    dt = dt[dt > 0.0]
    if dt.size == 0:
        raise ValueError("cannot infer the sampling interval: no vehicle has two samples")
    return float(np.median(dt))


def _runs(breaks: NDArray[np.bool_]) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Run starts and lengths from a per-sample break mask (``breaks[0]`` is True)."""
    starts = np.flatnonzero(breaks)
    lengths = np.diff(np.append(starts, breaks.size))
    return starts.astype(np.int64), lengths.astype(np.int64)


def held_lanes(
    t: NDArray[np.float64],
    veh: NDArray[np.int64],
    lane: NDArray[np.int64],
    *,
    dt_s: float,
    max_gap_s: float,
    min_dwell_s: float,
) -> tuple[NDArray[np.int64], NDArray[np.bool_]]:
    """Debounce lane indices and mark which sample pairs are contiguous.

    The input must be sorted by ``(veh, t)``. A *run* is a maximal stretch of
    one vehicle's consecutive samples with the same lane and no sampling gap
    longer than ``max_gap_s``. A run whose duration ``n_samples × dt_s`` is
    below ``min_dwell_s`` and whose neighbouring runs (both contiguous with
    it) lie in the same lane is a **flicker** — a lateral-position excursion
    across a lane line, the failure mode of an int8 band index — and its
    samples are reassigned to that neighbouring lane. One pass is applied:
    alternating flickers (A-B-A-B-A) are all bracketed by the original runs
    and all removed; a short stay that is *not* a return (A-B-C) is a real
    double change and is kept.

    Args:
        t: Sample times [s].
        veh: Integer vehicle codes.
        lane: Lane ids (band convention).
        dt_s: Sampling interval [s].
        max_gap_s: Largest time step between consecutive samples of a vehicle
            that still counts as contiguous.
        min_dwell_s: Shortest run that counts as a genuine stay.

    Returns:
        ``(lane_held, contig)`` — the debounced lane per sample and a mask of
        length ``n − 1`` that is True where sample ``i`` and ``i + 1`` belong
        to the same vehicle and are at most ``max_gap_s`` apart.
    """
    n = t.size
    if n == 0:
        return lane.copy(), np.zeros(0, dtype=bool)
    step = np.diff(t)
    contig = (veh[1:] == veh[:-1]) & (step > 0.0) & (step <= max_gap_s + 1e-9)
    breaks = np.ones(n, dtype=bool)
    breaks[1:] = ~contig | (lane[1:] != lane[:-1])
    starts, lengths = _runs(breaks)
    run_lane = lane[starts]
    # A run is joined to the previous one when the break that started it was
    # a lane change (contiguous samples) rather than a gap or a new vehicle.
    joined_prev = np.zeros(starts.size, dtype=bool)
    joined_prev[1:] = contig[starts[1:] - 1]
    joined_next = np.zeros(starts.size, dtype=bool)
    joined_next[:-1] = joined_prev[1:]
    short = lengths * dt_s < min_dwell_s - 1e-9
    prev_lane = np.roll(run_lane, 1)
    next_lane = np.roll(run_lane, -1)
    flicker = short & joined_prev & joined_next & (prev_lane == next_lane)
    new_run_lane = np.where(flicker, prev_lane, run_lane)
    lane_held = np.repeat(new_run_lane, lengths).astype(np.int64)
    return lane_held, contig


def _section_index(x: NDArray[np.float64], x_edges: NDArray[np.float64]) -> NDArray[np.int64]:
    """Section index per position, ``-1`` outside ``[x_edges[0], x_edges[-1])``."""
    idx = np.searchsorted(x_edges, x, side="right") - 1
    idx[(x < x_edges[0]) | (x >= x_edges[-1])] = -1
    return idx.astype(np.int64)


def lane_observables(
    df: pd.DataFrame,
    x_edges_m: Sequence[float],
    *,
    lanes: Sequence[int] = DEFAULT_LANES,
    dt_s: float | None = None,
    max_gap_s: float | None = None,
    min_dwell_s: float = DEFAULT_MIN_DWELL_S,
    window_s: tuple[float, float] | None = None,
    hist_dx_m: float = DEFAULT_HIST_DX_M,
) -> LaneObservables:
    """Lane-use shares, lane-change rates and change locations per section.

    Definitions (identical for observed fragments and simulated runs):

    * **Samples** are the rows whose ``lane`` is in ``lanes``; rows in other
      lanes (median shoulder, auxiliary and ramp lanes) are dropped first, so
      a vehicle that leaves the mainline appears as a sampling gap. Samples
      are sorted by ``(veh_id, t)``; ``dt_s`` is the sampling interval
      (inferred as the median positive step when not given).
    * **Vehicle-time per lane** in section ``k``: ``dt_s × number of samples``
      with ``x_edges_m[k] ≤ x < x_edges_m[k+1]`` in that lane (and
      ``window_s[0] ≤ t < window_s[1]`` when a window is given). The lane-use
      share is the row-normalized table.
    * **Pairs** are consecutive samples of one vehicle at most ``max_gap_s``
      apart (default ``2.5 × dt_s``). A pair contributes
      ``max(x_j − x_i, 0)`` metres of travel to the section holding its
      midpoint ``(x_i + x_j)/2`` when its first sample lies in the window.
    * **Lane changes** are the transitions between successive runs of the
      debounced lane sequence (:func:`held_lanes` — a stay shorter than
      ``min_dwell_s`` that returns to the previous lane is a lane-line
      flicker and is not a change). A change is located at the midpoint of
      the pair it occurs on, timed at the pair's second sample, and is
      *left* when the lane id decreases (toward the median in the band
      convention). The rate is ``changes / vehicle-km`` per section.
    * The **location histogram** counts changes in ``hist_dx_m`` bins over
      ``[x_edges_m[0], x_edges_m[-1])`` (bin count rounded so the bins tile
      the span exactly).

    All rows of ``df`` take part in run detection; ``window_s`` only selects
    what is *counted*, so a frame padded by a few seconds on either side of
    the window yields exactly the counts the full record would (this is how
    the observed side is processed in time chunks under a memory cap).

    Args:
        df: Frame with columns ``t`` [s], ``veh_id``, ``x`` [m], ``lane``.
        x_edges_m: Increasing section edges [m] (``n_sec + 1``).
        lanes: Mainline lane ids to keep, in column order.
        dt_s: Sampling interval [s]; inferred when None.
        max_gap_s: Contiguity limit [s]; ``DEFAULT_MAX_GAP_FACTOR × dt_s``
            when None.
        min_dwell_s: Flicker guard [s] (see :func:`held_lanes`).
        window_s: Half-open ``[lo, hi)`` time window to count; None = all.
        hist_dx_m: Location histogram bin width [m].

    Returns:
        :class:`LaneObservables` for the given sections and lanes.

    Raises:
        ValueError: On bad section edges or an uninferrable interval.
    """
    x_edges = np.asarray(x_edges_m, dtype=np.float64)
    if x_edges.ndim != 1 or x_edges.size < 2 or np.any(np.diff(x_edges) <= 0.0):
        raise ValueError("x_edges_m must be >= 2 strictly increasing values")
    lane_tuple = tuple(int(lane) for lane in lanes)
    if len(set(lane_tuple)) != len(lane_tuple) or not lane_tuple:
        raise ValueError("lanes must be a non-empty set of distinct ids")
    n_sec = x_edges.size - 1
    n_lanes = len(lane_tuple)
    span = float(x_edges[-1] - x_edges[0])
    n_bins = max(1, round(span / hist_dx_m))
    hist_edges = np.linspace(x_edges[0], x_edges[-1], n_bins + 1)

    keep = df["lane"].isin(lane_tuple).to_numpy()
    sub = df.loc[keep, ["t", "veh_id", "x", "lane"]]
    codes, _ = pd.factorize(sub["veh_id"], sort=False)
    order = np.lexsort((sub["t"].to_numpy(dtype=np.float64), codes))
    t = sub["t"].to_numpy(dtype=np.float64)[order]
    x = sub["x"].to_numpy(dtype=np.float64)[order]
    lane = sub["lane"].to_numpy(dtype=np.int64)[order]
    veh = np.asarray(codes, dtype=np.int64)[order]

    empty = LaneObservables(
        x_edges_m=x_edges,
        lanes=lane_tuple,
        dt_s=dt_s if dt_s is not None else float("nan"),
        veh_time_s=np.zeros((n_sec, n_lanes)),
        veh_km=np.zeros(n_sec),
        n_changes=np.zeros(n_sec, dtype=np.int64),
        n_changes_left=np.zeros(n_sec, dtype=np.int64),
        n_changes_right=np.zeros(n_sec, dtype=np.int64),
        change_hist_edges_m=hist_edges,
        change_hist=np.zeros(n_bins, dtype=np.int64),
        n_samples=0,
        window_s=window_s,
    )
    if t.size == 0:
        if dt_s is None:
            raise ValueError("no mainline samples and no dt_s given")
        return empty
    if dt_s is None:
        dt_s = infer_dt(t, veh)
        empty.dt_s = dt_s
    if max_gap_s is None:
        max_gap_s = DEFAULT_MAX_GAP_FACTOR * dt_s

    lane_held, contig = held_lanes(
        t, veh, lane, dt_s=dt_s, max_gap_s=max_gap_s, min_dwell_s=min_dwell_s
    )
    lane_col = np.full(lane_held.size, -1, dtype=np.int64)
    for j, lane_id in enumerate(lane_tuple):
        lane_col[lane_held == lane_id] = j

    in_window = np.ones(t.size, dtype=bool)
    if window_s is not None:
        in_window = (t >= window_s[0]) & (t < window_s[1])

    # Vehicle-time per (section, lane) from samples.
    sec = _section_index(x, x_edges)
    ok = in_window & (sec >= 0) & (lane_col >= 0)
    veh_time = np.zeros((n_sec, n_lanes))
    np.add.at(veh_time, (sec[ok], lane_col[ok]), dt_s)
    n_samples = int(ok.sum())

    # Travel from contiguous pairs, keyed by the first sample's time.
    i0 = np.flatnonzero(contig)
    i1 = i0 + 1
    pair_ok = in_window[i0]
    i0, i1 = i0[pair_ok], i1[pair_ok]
    mid = 0.5 * (x[i0] + x[i1])
    dist_km = np.maximum(x[i1] - x[i0], 0.0) / 1000.0
    pair_sec = _section_index(mid, x_edges)
    veh_km = np.zeros(n_sec)
    sel = pair_sec >= 0
    np.add.at(veh_km, pair_sec[sel], dist_km[sel])

    # Lane changes: contiguous pairs whose held lane differs, keyed by the
    # second sample's time.
    j0 = np.flatnonzero(contig & (lane_held[1:] != lane_held[:-1]))
    j1 = j0 + 1
    change_ok = np.ones(j0.size, dtype=bool)
    if window_s is not None:
        change_ok = (t[j1] >= window_s[0]) & (t[j1] < window_s[1])
    j0, j1 = j0[change_ok], j1[change_ok]
    change_x = 0.5 * (x[j0] + x[j1])
    toward_left = lane_held[j1] < lane_held[j0]
    change_sec = _section_index(change_x, x_edges)
    located = change_sec >= 0
    n_changes = np.bincount(change_sec[located], minlength=n_sec).astype(np.int64)
    n_left = np.bincount(change_sec[located & toward_left], minlength=n_sec).astype(np.int64)
    hist, _ = np.histogram(change_x[located], bins=hist_edges)

    return LaneObservables(
        x_edges_m=x_edges,
        lanes=lane_tuple,
        dt_s=dt_s,
        veh_time_s=veh_time,
        veh_km=veh_km,
        n_changes=n_changes,
        n_changes_left=n_left,
        n_changes_right=n_changes - n_left,
        change_hist_edges_m=hist_edges,
        change_hist=hist.astype(np.int64),
        n_samples=n_samples,
        window_s=window_s,
    )


def band_lane_from_sim(
    lane_index: NDArray[np.int64] | Sequence[int],
    x_sim: NDArray[np.float64] | Sequence[float],
    edge_offsets_m: Sequence[float],
    edge_lanes: Sequence[int],
) -> NDArray[np.int64]:
    """Map SUMO lane indices to the data's band convention.

    SUMO numbers lanes from the right (index 0 = rightmost) per edge, so the
    index of the *same physical lane* jumps where an edge gains or loses an
    auxiliary lane, and the I-24 data numbers lanes from the left (1 = HOV,
    4 = rightmost through lane, ≥ 5 auxiliary/ramp). With ``n`` lanes on the
    edge under ``x_sim``, ``band = n − index``: the leftmost lane is 1 on
    every edge and an extra lane on the right (an acceleration, deceleration
    or weaving lane — the only kind the I-24 westbound chain has, verified on
    the compiled net's ramp connections by ``scripts/i24_fit_lanechange.py``)
    becomes 5, exactly as the band index puts it.

    Args:
        lane_index: SUMO lane indices per sample.
        x_sim: Linear sim position per sample [m] (``offset + lanePosition``).
        edge_offsets_m: Start offset of each route edge [m], increasing,
            ``edge_offsets_m[0] == 0``.
        edge_lanes: Lane count of each edge, aligned with ``edge_offsets_m``.

    Returns:
        Band lane per sample (``int64``).

    Raises:
        ValueError: On mismatched or non-increasing edge tables.
    """
    offsets = np.asarray(edge_offsets_m, dtype=np.float64)
    n_lanes = np.asarray(edge_lanes, dtype=np.int64)
    if offsets.size != n_lanes.size or offsets.size == 0:
        raise ValueError("edge_offsets_m and edge_lanes must be non-empty and aligned")
    if np.any(np.diff(offsets) <= 0.0):
        raise ValueError("edge_offsets_m must be strictly increasing")
    x = np.asarray(x_sim, dtype=np.float64)
    idx = np.asarray(lane_index, dtype=np.int64)
    edge = np.clip(np.searchsorted(offsets, x, side="right") - 1, 0, offsets.size - 1)
    return (n_lanes[edge] - idx).astype(np.int64)


@dataclass(frozen=True)
class LaneChangeObjective:
    """Distance between simulated and observed lane observables.

    Attributes:
        value: ``share_rms + rate_weight × rate_rmspe`` (NaN if nothing
            could be compared).
        share_rms: RMS over (section, lane) of lane-share differences.
        rate_rmspe: RMS over sections of the relative lane-change-rate error.
        rate_weight: Weight applied to ``rate_rmspe``.
        n_share_terms: Number of (section, lane) cells compared.
        n_rate_terms: Number of sections whose rates were compared.
    """

    value: float
    share_rms: float
    rate_rmspe: float
    rate_weight: float
    n_share_terms: int
    n_rate_terms: int

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form with NaN mapped to None (JSON-safe)."""
        return {
            "value": None if math.isnan(self.value) else self.value,
            "share_rms": None if math.isnan(self.share_rms) else self.share_rms,
            "rate_rmspe": None if math.isnan(self.rate_rmspe) else self.rate_rmspe,
            "rate_weight": self.rate_weight,
            "n_share_terms": self.n_share_terms,
            "n_rate_terms": self.n_rate_terms,
        }


def lane_change_objective(
    sim: LaneObservables,
    obs: LaneObservables,
    *,
    rate_weight: float = DEFAULT_RATE_WEIGHT,
) -> LaneChangeObjective:
    """Objective for the lane-change calibration (smaller is better).

    ``J = share_rms + rate_weight × rate_rmspe`` with

    * ``share_rms = √(mean over cells (share_sim − share_obs)²)`` over every
      (section, lane) cell where both sides have vehicle-time — a
      dimensionless 0–1 quantity on the lane-use table;
    * ``rate_rmspe = √(mean over sections ((r_sim − r_obs)/r_obs)²)`` over
      sections where the observed rate is positive and the simulated rate
      is defined (the RMSPE form of the segment-speed criterion,
      CLAUDE.md §7.1, applied to changes per vehicle-km).

    The two terms are dimensionless; ``rate_weight`` (default
    :data:`DEFAULT_RATE_WEIGHT`) sets the exchange rate between a 0.01 share
    error and a 10% rate error and is recorded in the artifact's
    ``objective_spec``. The objective is zero for identical observables and
    positive whenever either table differs.

    Args:
        sim: Simulated observables.
        obs: Observed observables on the same sections and lanes.
        rate_weight: Weight of the rate term (≥ 0).

    Returns:
        :class:`LaneChangeObjective`.

    Raises:
        ValueError: If sections or lanes differ, or ``rate_weight < 0``.
    """
    if not np.array_equal(sim.x_edges_m, obs.x_edges_m) or sim.lanes != obs.lanes:
        raise ValueError("objective needs identical sections and lanes on both sides")
    if rate_weight < 0.0:
        raise ValueError("rate_weight must be >= 0")
    s_sim, s_obs = sim.lane_share, obs.lane_share
    both = np.isfinite(s_sim) & np.isfinite(s_obs)
    n_share = int(both.sum())
    share_rms = float(np.sqrt(np.mean((s_sim[both] - s_obs[both]) ** 2))) if n_share else math.nan
    r_sim, r_obs = sim.changes_per_veh_km, obs.changes_per_veh_km
    rate_ok = np.isfinite(r_sim) & np.isfinite(r_obs) & (r_obs > 0.0)
    n_rate = int(rate_ok.sum())
    rate_rmspe = (
        float(np.sqrt(np.mean(((r_sim[rate_ok] - r_obs[rate_ok]) / r_obs[rate_ok]) ** 2)))
        if n_rate
        else math.nan
    )
    if n_share and n_rate:
        value = share_rms + rate_weight * rate_rmspe
    elif n_share:
        value = share_rms
    elif n_rate:
        value = rate_weight * rate_rmspe
    else:
        value = math.nan
    return LaneChangeObjective(
        value=value,
        share_rms=share_rms,
        rate_rmspe=rate_rmspe,
        rate_weight=rate_weight,
        n_share_terms=n_share,
        n_rate_terms=n_rate,
    )
