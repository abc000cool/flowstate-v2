"""The gaps after a lane change, observed or simulated: lane-change relaxation (WP-88).

WP-87 (docs/WEAVE_MODEL_PLAN.md, dated section) found that the two-lane loop
holding lane 1 at the T.H.52 entry is car-following at equilibrium on a
leader in the other lane: after a crossing, the model's new follower sits at
1.01–1.07 of its own static gap ``s0 + vT``, i.e. it keeps its full time gap
``T`` at once. The empirical lane-change literature describes a *relaxation*
instead: after a change the new follower (and the changer) accept a shorter
gap and return to their normal gap over some time (Laval & Leclercq 2008,
Transp. Res. B 42(6):511–522; Schakel, Knoop & van Arem 2012, Transp. Res.
Rec. 2316:47–57, which integrates relaxation into a lane-change model; Zheng,
Ahn, Chen & Laval 2013, Transp. Res. C 26:367–379, who measure a
pre-insertion transition and a relaxation process in the immediate
follower). This module measures that transient on any trajectory table the
same way, so an observed table (I-24 MOTION, NGSIM US-101) and a simulated
one (a microsim run) give directly comparable curves.

**Input.** The frame of :func:`calibration.lane_change_gaps.lane_change_gaps`
(``t, veh_id, x`` front bumper, ``lane`` band convention, ``v``, optional
``length``; one shared time grid) and its ``records``, made with the same
``dt_s``, ``max_gap_s`` and ``min_dwell_s``. Lanes are read off the same
debounce (:func:`calibration.lanechange.held_lanes`), neighbours are found as
there (the *lead* is the nearest vehicle in a lane whose front is strictly
ahead of the reference front, the *lag* the nearest whose front is at or
behind it), and gaps are bumper to bumper.

**The two sides of a change.** At the change sample (the record's own time
stamp, offset 0) the *follower side* pairs the changer C with its new
follower F (the record's lag) and the *leader side* pairs C with its new
leader L (the record's lead). Each side is then read at the walk instants
``t_change + k · step_s`` (``step_s`` 1 s by default) and its values are
kept at the requested offsets:

* ``space_gap_m``: bumper gap of the rear vehicle to the front one (F to C on
  the follower side, C to L on the leader side);
* ``time_gap_s``: ``space_gap_m`` over the rear vehicle's speed, NaN when that
  speed is below ``min_speed_ms`` (2 m/s: a time gap at a crawl is not a
  headway choice); the side is still followed;
* ``ratio_own``: ``time_gap_s`` over the rear vehicle's *own* normal time
  gap, the median of its time gaps to its leader (in its own lane, whoever
  that leader was) at the walk instants in ``[t_change − pre_window_s[0],
  t_change − pre_window_s[1]]`` (30 s to 5 s before the change by default;
  the last seconds are left out because the follower's anticipation of the
  insertion falls there, Zheng et al. 2013) at which it was car-following
  (below) and at or above ``min_speed_ms``; NaN with fewer than
  ``min_ref_samples`` such instants;
* ``ratio_pop``: ``time_gap_s`` over the *population's* normal time gap at
  the rear vehicle's current speed: the median time gap of every
  car-following sample of the same table on the walk grid in the same speed
  bin (:func:`normal_time_gaps`, :func:`with_population_ratio`); NaN where
  the bin holds fewer than ``min_n`` samples;
* ``ratio_eq``: ``space_gap_m`` over the static gap ``s0 + v · T`` at the rear
  vehicle's speed, ``(s0, T)`` given by the caller (a population's means, or
  per vehicle) — WP-87's measure (1 = the IDM/EIDM equilibrium gap);
* ``ratio_own_eq``: ``ratio_eq`` over the rear vehicle's own median
  ``ratio_eq`` at the same reference instants as ``ratio_own``. A time gap
  at equilibrium is ``T + s0 / v``, so ``ratio_own`` moves with the speed
  whenever the speed after the change differs from the speed before it (a
  crossing out of a queue); ``ratio_own_eq`` is the vehicle's own normal with
  that speed dependence taken out by the static gap.

**Car-following.** A pair is car-following at an instant when its bumper gap
is at least ``min_gap_m`` (0.5 m; below it the sample is ``suspect``, on
I-24 MOTION most often a duplicate fragment) and at most
``follow_gap_m0 + max_follow_time_gap_s · v_rear`` (10 m + 5 s · v) and
``max_range_m``. A side enters the measurement only if it is car-following at
offset 0.

**Censoring.** A side contributes to an offset only while, at every walk
instant from the change to that offset: the changer is tracked and still in
the target lane (no further change, by the debounced lane); the partner is
tracked and is still C's immediate lag (follower side) or lead (leader side)
in that lane; and the pair is car-following. The first failure ends the side
and is recorded as its ``censor`` reason (:data:`CENSOR_REASONS`); nothing
after it is used. Tracking accepts a tracker's fragment switch: a vehicle
whose id has no sample at an instant, or a partner with a new id, is the
same vehicle when its front, carried forward at the mean of the two speeds,
lands within ``same_vehicle_tol_m`` (2 m) of the earlier one's (the WP-78
rule, :data:`calibration.lane_change_gaps.DEFAULT_SAME_VEHICLE_TOL_M`).
Censoring is not independent of the outcome — a follower that drops far
back is censored as ``gap_bound`` — so :func:`summarize_relaxation` reports
the count at every offset, the reasons, and a complete-case curve (the
sides observed at every offset up to ``complete_to_s``) beside the
per-offset one.

**Summaries and the fit.** Per group (zone kind × movement by default),
changer-speed class and side: ``n`` and the 25th, 50th and 75th percentiles
of each measure at each offset, and an exponential relaxation
``r(τ) = r_∞ + (r_0 − r_∞) · exp(−τ / τ_r)`` fitted to the per-offset medians
of each ratio (weighted least squares, weights ``n``; :func:`fit_relaxation`)
with a bootstrap over the sides (the unit that is resampled). A fit is
``supported`` only when enough offsets carry ``min_n`` sides, ``τ_r`` lies
between the first positive offset and the last offset used, most bootstrap
refits resolve too, the amplitude ``r_∞ − r_0`` has a 95 % interval that
excludes 0, and the exponential describes the medians (weighted RMS residual
at most a quarter of the amplitude); otherwise ``reason`` says which
condition failed. A supported fit with ``r_0 < r_∞`` and ``r_0`` below 1 is
the relaxation of the literature (a short gap that opens); ``r_0 > r_∞`` is a
gap that closes.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from calibration.lane_change_gaps import (
    DEFAULT_MAX_RANGE_M,
    DEFAULT_MIN_GAP_M,
    DEFAULT_SAME_VEHICLE_TOL_M,
    SPEED_CLASSES_MS,
)
from calibration.lanechange import DEFAULT_MAX_GAP_FACTOR, DEFAULT_MIN_DWELL_S, held_lanes, infer_dt

DEFAULT_OFFSETS_S: Final[tuple[float, ...]] = (
    0.0,
    1.0,
    2.0,
    3.0,
    4.0,
    5.0,
    6.0,
    8.0,
    10.0,
    12.0,
    15.0,
    20.0,
    25.0,
    30.0,
)
"""Offsets after the change at which the sides are read [s]: whole seconds, so
they lie on the I-24 MOTION grid (0.2 s), a microsim run's (0.5 s) and
NGSIM's (0.1 s) alike."""

DEFAULT_STEP_S: Final[float] = 1.0
"""Walk interval [s]: the pair's identity and car-following are checked at
every ``step_s`` between the change and the last offset."""

DEFAULT_PRE_WINDOW_S: Final[tuple[float, float]] = (30.0, 5.0)
"""The own-reference window, seconds before the change ``(from, to)``."""

DEFAULT_FOLLOW_GAP_M0: Final[float] = 10.0
"""Car-following bound at standstill [m] (bumper gap)."""

DEFAULT_MAX_FOLLOW_TIME_GAP_S: Final[float] = 5.0
"""Car-following bound's time term [s]: a pair is following while its gap is
at most ``follow_gap_m0 + max_follow_time_gap_s · v_rear``."""

DEFAULT_MIN_SPEED_MS: Final[float] = 2.0
"""Rear speed below which a time gap is not read [m/s]."""

DEFAULT_MIN_REF_SAMPLES: Final[int] = 5
"""Walk instants a vehicle's own reference needs."""

DEFAULT_SPEED_EDGES_MS: Final[tuple[float, ...]] = tuple(float(v) for v in range(2, 42, 2))
"""Speed bins of the population's normal time gap [m/s]: 2-m/s bins from
``min_speed_ms`` to 40 m/s."""

DEFAULT_GAP_BIN_S: Final[float] = 0.02
"""Histogram resolution of the population's normal time gap [s]."""

DEFAULT_MIN_N_NORMAL: Final[int] = 100
"""Samples a speed bin needs before its median is used as a reference."""

DEFAULT_MIN_N_FIT: Final[int] = 30
"""Sides an offset needs to enter the fit."""

DEFAULT_MIN_OFFSETS_FIT: Final[int] = 5
"""Offsets (each with ``min_n`` sides) a fit needs."""

DEFAULT_N_BOOT: Final[int] = 200
"""Bootstrap replicates of a fit."""

DEFAULT_SEED: Final[int] = 20260925
"""Master seed of the bootstrap (``flowstate_core.rng``)."""

DEFAULT_COMPLETE_TO_S: Final[float] = 10.0
"""Horizon of the complete-case curve [s]."""

MIN_RESOLVED_SHARE: Final[float] = 0.8
"""Share of bootstrap refits that must resolve ``τ_r`` for a fit to be supported."""

MAX_REL_RMS: Final[float] = 0.25
"""Largest weighted RMS residual of a supported fit, as a share of its amplitude."""

SIDES: Final[tuple[str, ...]] = ("follower", "leader")
"""``follower``: the new follower F behind the changer C; ``leader``: C behind its new leader L."""

MEASURES: Final[tuple[str, ...]] = (
    "time_gap_s",
    "space_gap_m",
    "ratio_own",
    "ratio_own_eq",
    "ratio_pop",
    "ratio_eq",
)
"""Measures read at each offset (module docstring)."""

SPEEDS: Final[tuple[str, ...]] = ("rear_v_ms", "front_v_ms")
"""Speeds kept beside the measures."""

RATIOS: Final[tuple[str, ...]] = ("ratio_own", "ratio_own_eq", "ratio_pop", "ratio_eq")
"""The measures a relaxation is fitted to by default."""

CENSOR_REASONS: Final[tuple[str, ...]] = (
    "observed_to_end",
    "no_partner",
    "not_following",
    "suspect",
    "window_end",
    "changer_lost",
    "changer_lane_change",
    "partner_lost",
    "partner_lane_change",
    "cut_in",
    "gap_bound",
)
"""Why a side ended. ``observed_to_end``: read at the last offset. ``no_partner``:
no vehicle on that side within range at the change. ``not_following``: the
pair was not car-following at the change. ``suspect``: a gap below
``min_gap_m``. ``window_end``: the next instant lies outside the frame's time
limits. ``changer_lost`` / ``partner_lost``: the vehicle's track ended (no
sample and no fragment continuing it). ``changer_lane_change`` /
``partner_lane_change``: that vehicle changed lanes. ``cut_in``: another
vehicle came between the pair (the partner is still in the lane). ``gap_bound``:
the pair stopped car-following (gap above the bound)."""

_TAU_GRID_S: Final[NDArray[np.float64]] = np.logspace(-1.0, 3.0, 481)
"""Relaxation times searched by the fit [s]: 0.1–1000 s, 2 % apart."""

_CARRY: Final[tuple[str, ...]] = (
    "t",
    "veh_id",
    "x",
    "zone",
    "zone_kind",
    "from_lane",
    "to_lane",
    "direction",
    "movement",
    "v",
    "confirmed",
    "suspect",
    "lead_gap_m",
    "lag_gap_m",
    "seed",
    "group",
    "arrival_crossing",
    "period",
)
"""Record columns an event carries when present."""


# --- the prepared frame -------------------------------------------------------------------


class _Frame:
    """A trajectory frame sorted by ``(vehicle, t)`` with its debounced lanes and orderings."""

    def __init__(
        self,
        df: pd.DataFrame,
        *,
        dt_s: float | None,
        max_gap_s: float | None,
        min_dwell_s: float,
        default_length_m: float | None,
    ) -> None:
        missing = {"t", "veh_id", "x", "lane", "v"} - set(df.columns)
        if missing:
            raise ValueError(f"df is missing columns: {sorted(missing)}")
        has_length = "length" in df.columns
        if not has_length and default_length_m is None:
            raise ValueError("df has no 'length' column: give default_length_m")
        codes, labels = pd.factorize(df["veh_id"], sort=False)
        codes = np.asarray(codes, dtype=np.int64)
        t_all = df["t"].to_numpy(dtype=np.float64)
        if t_all.size < 2 and dt_s is None:
            raise ValueError("cannot infer the sampling interval from fewer than two rows")
        order = np.lexsort((t_all, codes))
        veh = codes[order]
        t = t_all[order]
        if dt_s is None:
            dt_s = infer_dt(t, veh)
        self.dt_s = float(dt_s)
        self.max_gap_s = float(DEFAULT_MAX_GAP_FACTOR * dt_s if max_gap_s is None else max_gap_s)
        t_idx = np.rint(t / self.dt_s).astype(np.int64)
        dup = np.zeros(t.size, dtype=bool)
        dup[1:] = (veh[1:] == veh[:-1]) & (t_idx[1:] == t_idx[:-1])
        self.n_duplicate_slots = int(dup.sum())
        keep = order[~dup]
        self.veh = veh[~dup]
        self.t = t[~dup]
        self.t_idx = t_idx[~dup]
        self.x = df["x"].to_numpy(dtype=np.float64)[keep]
        self.v = df["v"].to_numpy(dtype=np.float64)[keep]
        lane = df["lane"].to_numpy(dtype=np.int64)[keep]
        if has_length:
            self.length = df["length"].to_numpy(dtype=np.float64)[keep]
        else:
            self.length = np.full(self.t.size, float(default_length_m or 0.0))
        self.labels = np.asarray(labels, dtype=object)
        self.n = int(self.t.size)
        self.lane_held, self.contig = held_lanes(
            self.t,
            self.veh,
            lane,
            dt_s=self.dt_s,
            max_gap_s=self.max_gap_s,
            min_dwell_s=min_dwell_s,
        )
        # a lane change of one vehicle between two of its rows, contiguous or across a gap
        lane_break = np.zeros(self.n, dtype=np.int64)
        if self.n > 1:
            lane_break[1:] = (self.veh[1:] == self.veh[:-1]) & (
                self.lane_held[1:] != self.lane_held[:-1]
            )
        self.cum_lane_break = np.cumsum(lane_break)
        if self.n == 0:
            self.t_lo = self.t_hi = 0
            return
        self.t_lo = int(self.t_idx.min())
        self.t_hi = int(self.t_idx.max())
        self._span = self.t_hi - self.t_lo + 1
        self._vt_key = self.veh * self._span + (self.t_idx - self.t_lo)
        self.lane_lo = int(self.lane_held.min())
        self.n_lane = int(self.lane_held.max()) - self.lane_lo + 1
        self._x_lo = float(self.x.min())
        self._scale = float(self.x.max()) - self._x_lo + 1.0
        key = self.t_idx * self.n_lane + (self.lane_held - self.lane_lo)
        self._by = np.lexsort((self.x, key))
        self._key_s = key[self._by]
        self._z_s = key[self._by].astype(np.float64) * self._scale + (self.x[self._by] - self._x_lo)

    # -- lookups ---------------------------------------------------------------------------

    def find(self, code: NDArray[np.int64], tq: NDArray[np.int64]) -> NDArray[np.int64]:
        """Row of (vehicle code, time slot); −1 when absent."""
        out = np.full(code.shape, -1, dtype=np.int64)
        if self.n == 0:
            return out
        ok = (code >= 0) & (tq >= self.t_lo) & (tq <= self.t_hi)
        if not ok.any():
            return out
        q = code[ok] * self._span + (tq[ok] - self.t_lo)
        pos = np.minimum(np.searchsorted(self._vt_key, q, side="left"), self.n - 1)
        hit = self._vt_key[pos] == q
        sub = np.full(q.shape, -1, dtype=np.int64)
        sub[hit] = pos[hit]
        out[ok] = sub
        return out

    def _query(
        self, tq: NDArray[np.int64], lanes: NDArray[np.int64], xq: NDArray[np.float64]
    ) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.float64], NDArray[np.bool_]]:
        """Block bounds of (slot, lane) and the sort key of position ``xq`` in it."""
        valid = (lanes >= self.lane_lo) & (lanes < self.lane_lo + self.n_lane)
        kq = tq * self.n_lane + (np.where(valid, lanes, self.lane_lo) - self.lane_lo)
        blo = np.searchsorted(self._key_s, kq, side="left")
        bhi = np.searchsorted(self._key_s, kq, side="right")
        zq = kq.astype(np.float64) * self._scale + (xq - self._x_lo)
        return blo, bhi, zq, valid

    def neighbours(
        self, rows: NDArray[np.int64], lanes: NDArray[np.int64]
    ) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
        """Lead and lag rows of each row's front in ``lanes`` at the row's own slot (−1 = none).

        The lead is the nearest vehicle whose front is strictly ahead, the lag
        the nearest whose front is at or behind (the row itself excluded).
        """
        lead = np.full(rows.shape, -1, dtype=np.int64)
        lag = np.full(rows.shape, -1, dtype=np.int64)
        ok = rows >= 0
        if self.n == 0 or not ok.any():
            return lead, lag
        r = rows[ok]
        blo, bhi, zq, valid = self._query(self.t_idx[r], lanes[ok], self.x[r])
        pos_r = np.searchsorted(self._z_s, zq, side="right")
        pos_l = np.searchsorted(self._z_s, zq, side="left")
        has_lead = valid & (pos_r < bhi)
        has_lag = valid & (pos_l - 1 >= blo)
        lead[ok] = np.where(has_lead, self._by[np.minimum(pos_r, self.n - 1)], -1)
        lag[ok] = np.where(has_lag, self._by[np.maximum(pos_l - 1, 0)], -1)
        return lead, lag

    def nearest(
        self, tq: NDArray[np.int64], lanes: NDArray[np.int64], xq: NDArray[np.float64]
    ) -> NDArray[np.int64]:
        """The row in (slot, lane) whose front is nearest ``xq`` (−1 when the lane is empty)."""
        out = np.full(tq.shape, -1, dtype=np.int64)
        if self.n == 0 or tq.size == 0:
            return out
        blo, bhi, zq, valid = self._query(tq, lanes, xq)
        pos = np.searchsorted(self._z_s, zq, side="left")
        a = np.where(valid & (pos < bhi), self._by[np.minimum(pos, self.n - 1)], -1)
        b = np.where(valid & (pos - 1 >= blo), self._by[np.maximum(pos - 1, 0)], -1)
        da = np.where(a >= 0, np.abs(self.x[np.maximum(a, 0)] - xq), np.inf)
        db = np.where(b >= 0, np.abs(self.x[np.maximum(b, 0)] - xq), np.inf)
        out = np.where(da <= db, a, b)
        return np.where(np.isfinite(np.minimum(da, db)), out, -1)

    def continues(
        self, prev: NDArray[np.int64], cand: NDArray[np.int64], tol_m: float
    ) -> NDArray[np.bool_]:
        """Whether row ``cand`` is row ``prev``'s vehicle (same id, or position continuity).

        Continuity: ``prev``'s front carried to ``cand``'s time at the mean of
        the two speeds lands within ``tol_m`` of ``cand``'s front (either time
        order). −1 on either side is False.
        """
        ok = (prev >= 0) & (cand >= 0)
        p = np.maximum(prev, 0)
        c = np.maximum(cand, 0)
        same_id = self.veh[p] == self.veh[c]
        carried = self.x[p] + 0.5 * (self.v[p] + self.v[c]) * (self.t[c] - self.t[p])
        close = np.abs(carried - self.x[c]) <= tol_m
        return np.asarray(ok & (same_id | close), dtype=bool)

    def track(
        self,
        prev: NDArray[np.int64],
        tq: NDArray[np.int64],
        lanes: NDArray[np.int64],
        tol_m: float,
    ) -> NDArray[np.int64]:
        """The row of ``prev``'s vehicle at slot ``tq``: by id, else a continuing fragment in ``lanes``."""
        code = np.where(prev >= 0, self.veh[np.maximum(prev, 0)], -1)
        own = self.find(code, tq)
        need = (prev >= 0) & (own < 0)
        if need.any():
            p = prev[need]
            xq = self.x[p] + self.v[p] * (tq[need] - self.t_idx[p]) * self.dt_s
            cand = self.nearest(tq[need], lanes[need], xq)
            ok = self.continues(p, cand, tol_m)
            own[need] = np.where(ok, cand, -1)
        return own

    def gap_behind(self, rear: NDArray[np.int64], front: NDArray[np.int64]) -> NDArray[np.float64]:
        """Bumper gap of ``rear`` to ``front`` [m] (NaN where either is −1)."""
        ok = (rear >= 0) & (front >= 0)
        r = np.maximum(rear, 0)
        f = np.maximum(front, 0)
        return np.where(ok, self.x[f] - self.length[f] - self.x[r], np.nan)


def _following(
    gap: NDArray[np.float64],
    v_rear: NDArray[np.float64],
    *,
    min_gap_m: float,
    max_range_m: float,
    follow_gap_m0: float,
    max_follow_time_gap_s: float,
) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    """``(following, suspect)`` of bumper gaps at the rear vehicle's speed."""
    with np.errstate(invalid="ignore"):
        suspect = np.isfinite(gap) & (gap < min_gap_m)
        bound = np.minimum(
            max_range_m, follow_gap_m0 + max_follow_time_gap_s * np.maximum(v_rear, 0)
        )
        following = np.isfinite(gap) & ~suspect & (gap <= bound)
    return following, suspect


def _time_gap(
    gap: NDArray[np.float64], v_rear: NDArray[np.float64], min_speed_ms: float
) -> NDArray[np.float64]:
    """Time gap [s]; NaN below ``min_speed_ms``."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(np.isfinite(gap) & (v_rear >= min_speed_ms), gap / v_rear, np.nan)


# --- the population's normal time gap -----------------------------------------------------


@dataclass
class NormalTimeGaps:
    """Car-following time gaps of a whole table, histogrammed by the rear vehicle's speed.

    Attributes:
        speed_edges_ms: Speed bin edges [m/s]; bin ``i`` is
            ``[speed_edges_ms[i], speed_edges_ms[i + 1])``.
        gap_bin_s: Time-gap histogram resolution [s].
        counts: ``(n_speed_bins, n_gap_bins)`` sample counts; gap bin ``j`` is
            ``[j · gap_bin_s, (j + 1) · gap_bin_s)``.
        parameters: The sampling parameters, for artifacts.
    """

    speed_edges_ms: NDArray[np.float64]
    gap_bin_s: float
    counts: NDArray[np.int64]
    parameters: dict[str, Any] = field(default_factory=dict)

    def __add__(self, other: NormalTimeGaps) -> NormalTimeGaps:
        """Pool two tables' samples (same bins)."""
        if not (
            np.array_equal(self.speed_edges_ms, other.speed_edges_ms)
            and self.gap_bin_s == other.gap_bin_s
            and self.counts.shape == other.counts.shape
        ):
            raise ValueError("NormalTimeGaps with different bins cannot be pooled")
        return NormalTimeGaps(
            self.speed_edges_ms, self.gap_bin_s, self.counts + other.counts, dict(self.parameters)
        )

    @property
    def n(self) -> NDArray[np.int64]:
        """Samples per speed bin."""
        return np.asarray(self.counts.sum(axis=1), dtype=np.int64)

    def quantile(self, q: float, *, min_n: int = DEFAULT_MIN_N_NORMAL) -> NDArray[np.float64]:
        """Quantile ``q`` of the time gap per speed bin [s], linear within a histogram bin;
        NaN in bins with fewer than ``min_n`` samples."""
        out = np.full(self.counts.shape[0], np.nan)
        for i, row in enumerate(self.counts):
            n = int(row.sum())
            if n < max(min_n, 1):
                continue
            cum = np.cumsum(row)
            target = q * n
            j = int(np.searchsorted(cum, target, side="left"))
            j = min(j, row.size - 1)
            below = float(cum[j - 1]) if j > 0 else 0.0
            frac = (target - below) / float(row[j]) if row[j] > 0 else 0.0
            out[i] = (j + min(max(frac, 0.0), 1.0)) * self.gap_bin_s
        return out

    def lookup(
        self, v_ms: NDArray[np.float64], *, min_n: int = DEFAULT_MIN_N_NORMAL
    ) -> NDArray[np.float64]:
        """The median time gap of each speed's bin [s] (NaN outside the bins or below ``min_n``)."""
        med = self.quantile(0.5, min_n=min_n)
        v = np.asarray(v_ms, dtype=np.float64)
        idx = np.searchsorted(self.speed_edges_ms, v, side="right") - 1
        inside = np.isfinite(v) & (idx >= 0) & (idx < med.size)
        return np.where(inside, med[np.clip(idx, 0, med.size - 1)], np.nan)

    def to_dict(self, *, min_n: int = DEFAULT_MIN_N_NORMAL) -> dict[str, Any]:
        """JSON form: per speed bin ``n`` and the time gap's p25 / p50 / p75 [s]."""
        qs = {f"p{round(q * 100)}": self.quantile(q, min_n=min_n) for q in (0.25, 0.5, 0.75)}
        bins = []
        for i in range(self.counts.shape[0]):
            row: dict[str, Any] = {
                "v_lo_ms": float(self.speed_edges_ms[i]),
                "v_hi_ms": float(self.speed_edges_ms[i + 1]),
                "n": int(self.n[i]),
            }
            for k, arr in qs.items():
                row[k] = None if not math.isfinite(float(arr[i])) else round(float(arr[i]), 3)
            bins.append(row)
        return {
            "definition": "time gap to the nearest leader in the vehicle's own (debounced) lane, "
            "every car-following sample on the walk grid, by the rear vehicle's speed",
            "min_n": min_n,
            "gap_bin_s": self.gap_bin_s,
            "parameters": self.parameters,
            "bins": bins,
        }


def normal_time_gaps(
    df: pd.DataFrame,
    *,
    dt_s: float | None = None,
    max_gap_s: float | None = None,
    min_dwell_s: float = DEFAULT_MIN_DWELL_S,
    step_s: float = DEFAULT_STEP_S,
    window_s: tuple[float, float] | None = None,
    x_range_m: tuple[float, float] | None = None,
    lanes: Sequence[int] | None = None,
    max_range_m: float = DEFAULT_MAX_RANGE_M,
    min_gap_m: float = DEFAULT_MIN_GAP_M,
    follow_gap_m0: float = DEFAULT_FOLLOW_GAP_M0,
    max_follow_time_gap_s: float = DEFAULT_MAX_FOLLOW_TIME_GAP_S,
    min_speed_ms: float = DEFAULT_MIN_SPEED_MS,
    speed_edges_ms: Sequence[float] = DEFAULT_SPEED_EDGES_MS,
    gap_bin_s: float = DEFAULT_GAP_BIN_S,
    default_length_m: float | None = None,
) -> NormalTimeGaps:
    """The population's car-following time gaps, by speed (the ``ratio_pop`` reference).

    Every row on the walk grid (``t`` a whole multiple of ``step_s``) that has
    a leader in its own debounced lane — the nearest vehicle whose front is
    strictly ahead — and is car-following (module docstring) at a speed of at
    least ``min_speed_ms`` contributes its time gap. The samples are not
    screened for recent lane changes: the normal includes whatever share of
    the traffic was relaxing at the time (a limitation the artifacts state).

    Args:
        df: The trajectory frame (``t, veh_id, x, lane, v``, optional ``length``).
        dt_s: Sampling interval [s]; inferred when None.
        max_gap_s: Contiguity bound [s]; ``2.5 × dt_s`` when None.
        min_dwell_s: The debounce [s].
        step_s: Walk interval [s] (a whole multiple of ``dt_s``).
        window_s: Half-open window on the sample time; None = all (a chunked
            reader passes each chunk's own window so no sample counts twice).
        x_range_m: Half-open span on the rear vehicle's front; None = all.
        lanes: Rear-vehicle lanes (band) to keep; None = all.
        max_range_m: Neighbour range [m].
        min_gap_m: Suspect gap [m].
        follow_gap_m0: Car-following bound at standstill [m].
        max_follow_time_gap_s: Car-following bound's time term [s].
        min_speed_ms: Lowest rear speed [m/s].
        speed_edges_ms: Speed bin edges [m/s], increasing.
        gap_bin_s: Histogram resolution [s].
        default_length_m: Vehicle length when ``df`` has none [m].

    Returns:
        :class:`NormalTimeGaps` (pool chunks with ``+``).
    """
    edges = np.asarray(speed_edges_ms, dtype=np.float64)
    if edges.size < 2 or np.any(np.diff(edges) <= 0):
        raise ValueError("speed_edges_ms must be at least two increasing values")
    max_tg = max_follow_time_gap_s + follow_gap_m0 / max(min_speed_ms, 1e-6)
    n_bins = math.ceil(max_tg / gap_bin_s) + 1
    params = {
        "step_s": step_s,
        "window_s": list(window_s) if window_s is not None else None,
        "x_range_m": list(x_range_m) if x_range_m is not None else None,
        "lanes": list(lanes) if lanes is not None else None,
        "min_speed_ms": min_speed_ms,
        "follow_gap_m0": follow_gap_m0,
        "max_follow_time_gap_s": max_follow_time_gap_s,
        "min_gap_m": min_gap_m,
        "max_range_m": max_range_m,
    }
    counts = np.zeros((edges.size - 1, n_bins), dtype=np.int64)
    if len(df) == 0:
        return NormalTimeGaps(edges, gap_bin_s, counts, params)
    fr = _Frame(
        df,
        dt_s=dt_s,
        max_gap_s=max_gap_s,
        min_dwell_s=min_dwell_s,
        default_length_m=default_length_m,
    )
    step = _whole_multiple(step_s, fr.dt_s, "step_s")
    params["dt_s"] = fr.dt_s
    if fr.n < 2:
        return NormalTimeGaps(edges, gap_bin_s, counts, params)
    rear = fr._by[:-1]
    front = fr._by[1:]
    same = fr._key_s[:-1] == fr._key_s[1:]
    rear, front = rear[same], front[same]
    keep = (fr.t_idx[rear] % step) == 0
    if window_s is not None:
        keep &= (fr.t[rear] >= window_s[0]) & (fr.t[rear] < window_s[1])
    if x_range_m is not None:
        keep &= (fr.x[rear] >= x_range_m[0]) & (fr.x[rear] < x_range_m[1])
    if lanes is not None:
        keep &= np.isin(fr.lane_held[rear], np.asarray(list(lanes), dtype=np.int64))
    rear, front = rear[keep], front[keep]
    gap = fr.gap_behind(rear, front)
    v_rear = fr.v[rear]
    following, _ = _following(
        gap,
        v_rear,
        min_gap_m=min_gap_m,
        max_range_m=max_range_m,
        follow_gap_m0=follow_gap_m0,
        max_follow_time_gap_s=max_follow_time_gap_s,
    )
    tg = _time_gap(gap, v_rear, min_speed_ms)
    ok = following & np.isfinite(tg)
    vi = np.searchsorted(edges, v_rear[ok], side="right") - 1
    gi = np.minimum((tg[ok] / gap_bin_s).astype(np.int64), n_bins - 1)
    inside = (vi >= 0) & (vi < edges.size - 1)
    np.add.at(counts, (vi[inside], gi[inside]), 1)
    return NormalTimeGaps(edges, gap_bin_s, counts, params)


def _whole_multiple(value_s: float, dt_s: float, name: str) -> int:
    """``value_s / dt_s`` as a positive integer, or ValueError."""
    k = round(value_s / dt_s)
    if k < 1 or abs(k * dt_s - value_s) > 1e-6 * max(1.0, value_s):
        raise ValueError(f"{name} ({value_s}) must be a whole multiple of dt_s ({dt_s})")
    return int(k)


# --- the post-change trajectories ---------------------------------------------------------


@dataclass
class PostChangeGaps:
    """The two sides of each lane change, read at fixed offsets after it (WP-88).

    Attributes:
        events: One row per change: the record's columns (:data:`_CARRY`
            present in ``records``), ``change`` (its ``records`` row), and per
            side ``<side>_id`` (the partner's id at the change), ``<side>_rear_id``,
            ``<side>_ref_own_s`` (the rear vehicle's own normal time gap),
            ``<side>_ref_n`` (walk instants behind it), ``<side>_censor``
            (:data:`CENSOR_REASONS`), ``<side>_last_offset_s`` (the last offset
            read; NaN when none).
        offsets_s: The offsets [s].
        values: ``values[side][name]``: ``(n_events, n_offsets)`` arrays of
            each :data:`MEASURES` and :data:`SPEEDS` entry, NaN where the side
            was not read (censored, or a time gap below ``min_speed_ms``).
        counts: ``n_changes`` (asked for), ``n_unmatched`` (not found in the
            frame), ``n_duplicate_slots`` (rows dropped: a second sample of one
            vehicle in one slot), and per side ``n_<side>_measured`` and
            ``n_<side>_<reason>``.
        parameters: The parameters, for artifacts.
    """

    events: pd.DataFrame
    offsets_s: NDArray[np.float64]
    values: dict[str, dict[str, NDArray[np.float64]]]
    counts: dict[str, int]
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_long(self) -> pd.DataFrame:
        """One row per (change, side, offset) read: ``change, side, offset_s`` and the arrays."""
        parts = []
        for side in SIDES:
            vals = self.values[side]
            read = np.isfinite(vals["space_gap_m"])
            ev, off = np.nonzero(read)
            if ev.size == 0:
                continue
            part = pd.DataFrame(
                {
                    "change": self.events["change"].to_numpy()[ev],
                    "side": side,
                    "offset_s": self.offsets_s[off],
                }
            )
            for name in (*MEASURES, *SPEEDS):
                part[name] = vals[name][ev, off]
            parts.append(part)
        if not parts:
            cols = ["change", "side", "offset_s", *MEASURES, *SPEEDS]
            return pd.DataFrame({c: pd.Series(dtype=float) for c in cols})
        return pd.concat(parts, ignore_index=True)


def _equilibrium_gap(
    equilibrium: tuple[float, float] | Mapping[str, tuple[float, float]] | None,
    rear_ids: NDArray[np.object_],
    v_rear: NDArray[np.float64],
) -> NDArray[np.float64]:
    """``s0 + v · T`` per row (NaN without parameters)."""
    if equilibrium is None:
        return np.full(v_rear.shape, np.nan)
    if isinstance(equilibrium, tuple):
        s0, T = equilibrium
        return float(s0) + v_rear * float(T)
    s0_arr = np.array(
        [equilibrium[str(i)][0] if str(i) in equilibrium else np.nan for i in rear_ids], dtype=float
    )
    t_arr = np.array(
        [equilibrium[str(i)][1] if str(i) in equilibrium else np.nan for i in rear_ids], dtype=float
    )
    if v_rear.ndim == 2:
        s0_arr, t_arr = s0_arr[:, None], t_arr[:, None]
    out: NDArray[np.float64] = s0_arr + v_rear * t_arr
    return out


def post_change_gaps(
    df: pd.DataFrame,
    records: pd.DataFrame,
    *,
    changes: NDArray[np.bool_] | Sequence[bool] | None = None,
    offsets_s: Sequence[float] = DEFAULT_OFFSETS_S,
    step_s: float = DEFAULT_STEP_S,
    dt_s: float | None = None,
    max_gap_s: float | None = None,
    min_dwell_s: float = DEFAULT_MIN_DWELL_S,
    pre_window_s: tuple[float, float] = DEFAULT_PRE_WINDOW_S,
    max_range_m: float = DEFAULT_MAX_RANGE_M,
    min_gap_m: float = DEFAULT_MIN_GAP_M,
    follow_gap_m0: float = DEFAULT_FOLLOW_GAP_M0,
    max_follow_time_gap_s: float = DEFAULT_MAX_FOLLOW_TIME_GAP_S,
    min_speed_ms: float = DEFAULT_MIN_SPEED_MS,
    min_ref_samples: int = DEFAULT_MIN_REF_SAMPLES,
    same_vehicle_tol_m: float = DEFAULT_SAME_VEHICLE_TOL_M,
    default_length_m: float | None = None,
    equilibrium: tuple[float, float] | Mapping[str, tuple[float, float]] | None = None,
    t_limits_s: tuple[float, float] | None = None,
) -> PostChangeGaps:
    """The new follower's and the changer's gaps at fixed offsets after each lane change.

    Definitions, car-following and censoring: module docstring. ``ratio_pop``
    is left NaN here; :func:`with_population_ratio` fills it from a
    :class:`NormalTimeGaps` (which a chunked reader can only complete after
    the last chunk).

    Args:
        df: The frame :func:`calibration.lane_change_gaps.lane_change_gaps` read.
        records: Its ``records`` (same ``dt_s``, ``max_gap_s``, ``min_dwell_s``).
        changes: Boolean mask over ``records`` rows (all when None).
        offsets_s: Offsets after the change [s], each a whole multiple of
            ``step_s``, non-negative.
        step_s: Walk interval [s], a whole multiple of ``dt_s``.
        dt_s: Sampling interval of ``df`` [s]; inferred when None.
        max_gap_s: Contiguity bound [s]; ``2.5 × dt_s`` when None.
        min_dwell_s: The debounce [s].
        pre_window_s: ``(from, to)`` seconds before the change of the own
            reference window, ``from > to >= 0``.
        max_range_m: Neighbour range [m].
        min_gap_m: A gap below this is ``suspect`` [m].
        follow_gap_m0: Car-following bound at standstill [m].
        max_follow_time_gap_s: Car-following bound's time term [s].
        min_speed_ms: Lowest rear speed for a time gap [m/s].
        min_ref_samples: Walk instants the own reference needs.
        same_vehicle_tol_m: Position-continuity tolerance [m].
        default_length_m: Vehicle length when ``df`` has none [m].
        equilibrium: ``(s0_m, T_s)`` for everyone, or ``veh_id → (s0_m, T_s)``
            (keyed by the rear vehicle's id at the change), for ``ratio_eq``;
            None leaves it NaN.
        t_limits_s: The span of ``df`` whose rows are complete (a chunk's load
            window); an instant outside it ends a side as ``window_end`` and is
            not used for a reference. None = the frame's own span.

    Returns:
        :class:`PostChangeGaps`.

    Raises:
        ValueError: On missing columns, offsets or a step that are not whole
            multiples, or a bad reference window.
    """
    rmissing = {"t", "veh_id", "from_lane", "to_lane"} - set(records.columns)
    if rmissing:
        raise ValueError(f"records is missing columns: {sorted(rmissing)}")
    if not pre_window_s[0] > pre_window_s[1] >= 0.0:
        raise ValueError("pre_window_s must be (from, to) with from > to >= 0")
    offs = np.asarray(sorted({float(o) for o in offsets_s}), dtype=np.float64)
    if offs.size == 0 or offs[0] < 0.0:
        raise ValueError("offsets_s must be non-empty and non-negative")
    fr = _Frame(
        df,
        dt_s=dt_s,
        max_gap_s=max_gap_s,
        min_dwell_s=min_dwell_s,
        default_length_m=default_length_m,
    )
    step = _whole_multiple(step_s, fr.dt_s, "step_s")
    k_off = np.array([round(o / step_s) for o in offs], dtype=np.int64)
    if np.any(np.abs(k_off * step_s - offs) > 1e-6 * np.maximum(1.0, offs)):
        raise ValueError(f"offsets_s must be whole multiples of step_s ({step_s})")
    k_max = int(k_off.max())
    k_pre_lo = math.ceil(pre_window_s[1] / step_s - 1e-9)
    k_pre_hi = math.floor(pre_window_s[0] / step_s + 1e-9)
    if k_pre_lo < 1:
        k_pre_lo = 1

    sel = (
        np.ones(len(records), dtype=bool)
        if changes is None
        else np.asarray(changes, dtype=bool).copy()
    )
    if sel.shape != (len(records),):
        raise ValueError("changes must be a mask over the records rows")
    change_rows = np.flatnonzero(sel).astype(np.int64)
    params: dict[str, Any] = {
        "offsets_s": offs.tolist(),
        "step_s": step_s,
        "dt_s": fr.dt_s,
        "max_gap_s": fr.max_gap_s,
        "min_dwell_s": min_dwell_s,
        "pre_window_s": list(pre_window_s),
        "max_range_m": max_range_m,
        "min_gap_m": min_gap_m,
        "follow_gap_m0": follow_gap_m0,
        "max_follow_time_gap_s": max_follow_time_gap_s,
        "min_speed_ms": min_speed_ms,
        "min_ref_samples": min_ref_samples,
        "same_vehicle_tol_m": same_vehicle_tol_m,
        "default_length_m": default_length_m,
        "t_limits_s": list(t_limits_s) if t_limits_s is not None else None,
        "equilibrium": (
            None
            if equilibrium is None
            else (list(equilibrium) if isinstance(equilibrium, tuple) else "per vehicle")
        ),
    }
    counts: dict[str, int] = {
        "n_changes": int(change_rows.size),
        "n_unmatched": 0,
        "n_duplicate_slots": fr.n_duplicate_slots,
    }

    # --- locate each change in the frame, as gap_sequences does ---------------------------
    rec = records.iloc[change_rows]
    if fr.n == 0:
        rec_code = np.full(len(rec), -1, dtype=np.int64)
    else:
        rec_code = np.asarray(
            pd.Index(fr.labels).get_indexer(pd.Index(rec["veh_id"])), dtype=np.int64
        )
    rec_tidx = np.rint(rec["t"].to_numpy(dtype=np.float64) / fr.dt_s).astype(np.int64)
    from_c = rec["from_lane"].to_numpy(dtype=np.int64)
    to_c = rec["to_lane"].to_numpy(dtype=np.int64)
    j1 = fr.find(rec_code, rec_tidx)
    matched = j1 >= 1
    j1c = np.maximum(j1, 1)
    if fr.n > 1:
        # the change: the previous row is the same vehicle, contiguous, in the origin lane
        matched &= (fr.lane_held[np.maximum(j1, 0)] == to_c) & (fr.lane_held[j1c - 1] == from_c)
        matched &= fr.contig[j1c - 1]
    else:
        matched &= False
    counts["n_unmatched"] = int(np.sum(~matched))
    change_rows, j1, to_c = change_rows[matched], j1[matched], to_c[matched]
    rec_tidx = rec_tidx[matched]
    n_c = int(change_rows.size)

    if t_limits_s is not None:
        lim_lo = math.ceil(t_limits_s[0] / fr.dt_s - 1e-9)
        lim_hi = math.floor(t_limits_s[1] / fr.dt_s + 1e-9)
    else:
        lim_lo, lim_hi = fr.t_lo, fr.t_hi

    n_off = offs.size
    values: dict[str, dict[str, NDArray[np.float64]]] = {
        side: {name: np.full((n_c, n_off), np.nan) for name in (*MEASURES, *SPEEDS)}
        for side in SIDES
    }
    reason = {side: np.full(n_c, "observed_to_end", dtype=object) for side in SIDES}
    last_off = {side: np.full(n_c, np.nan) for side in SIDES}
    partner0 = {side: np.full(n_c, -1, dtype=np.int64) for side in SIDES}
    width = max(k_pre_hi - k_pre_lo + 1, 0)
    ref_tg = {side: np.full((n_c, width), np.nan) for side in SIDES}
    ref_gap = {side: np.full((n_c, width), np.nan) for side in SIDES}
    ref_v = {side: np.full((n_c, width), np.nan) for side in SIDES}

    if n_c:
        _walk(
            fr,
            j1=j1,
            target=to_c,
            tidx0=rec_tidx,
            step=step,
            k_max=k_max,
            k_off=k_off,
            lim=(lim_lo, lim_hi),
            values=values,
            reason=reason,
            last_off=last_off,
            partner0=partner0,
            offs=offs,
            follow=dict(
                min_gap_m=min_gap_m,
                max_range_m=max_range_m,
                follow_gap_m0=follow_gap_m0,
                max_follow_time_gap_s=max_follow_time_gap_s,
            ),
            min_speed_ms=min_speed_ms,
            tol_m=same_vehicle_tol_m,
        )
        for side in SIDES:
            start = j1 if side == "leader" else partner0[side]
            ref_tg[side], ref_gap[side], ref_v[side] = _own_reference(
                fr,
                start,
                tidx0=rec_tidx,
                step=step,
                k_range=(k_pre_lo, k_pre_hi),
                lim=(lim_lo, lim_hi),
                follow=dict(
                    min_gap_m=min_gap_m,
                    max_range_m=max_range_m,
                    follow_gap_m0=follow_gap_m0,
                    max_follow_time_gap_s=max_follow_time_gap_s,
                ),
                min_speed_ms=min_speed_ms,
                tol_m=same_vehicle_tol_m,
            )

    # --- the event table and the ratios ---------------------------------------------------
    carry = [c for c in _CARRY if c in records.columns]
    events = records.iloc[change_rows][carry].reset_index(drop=True)
    events.insert(0, "change", change_rows)
    for side in SIDES:
        p0 = partner0[side]
        ids = np.full(n_c, None, dtype=object)
        if n_c and fr.n:
            ids[p0 >= 0] = fr.labels[fr.veh[p0[p0 >= 0]]]
        rear_ids = ids if side == "follower" else events["veh_id"].to_numpy(dtype=object)
        n_ref = np.sum(np.isfinite(ref_tg[side]), axis=1).astype(np.int64)
        enough = n_ref >= max(min_ref_samples, 1)
        ref_s = _row_median(ref_tg[side], enough)
        with np.errstate(divide="ignore", invalid="ignore"):
            ref_eq = _row_median(
                ref_gap[side] / _equilibrium_gap(equilibrium, rear_ids, ref_v[side]), enough
            )
        events[f"{side}_id"] = ids
        events[f"{side}_rear_id"] = rear_ids
        events[f"{side}_ref_own_s"] = ref_s
        events[f"{side}_ref_own_eq"] = ref_eq
        events[f"{side}_ref_n"] = n_ref
        events[f"{side}_censor"] = reason[side]
        events[f"{side}_last_offset_s"] = last_off[side]
        vals = values[side]
        with np.errstate(divide="ignore", invalid="ignore"):
            vals["ratio_own"] = vals["time_gap_s"] / ref_s[:, None]
            eq = _equilibrium_gap(equilibrium, rear_ids, vals["rear_v_ms"])
            vals["ratio_eq"] = vals["space_gap_m"] / eq
            vals["ratio_own_eq"] = vals["ratio_eq"] / ref_eq[:, None]
        for name in ("ratio_own", "ratio_eq", "ratio_own_eq"):
            vals[name][~np.isfinite(vals[name])] = np.nan
        tally = Counter(reason[side].tolist())
        counts[f"n_{side}_measured"] = int(n_c - tally["no_partner"] - tally["not_following"])
        for r in CENSOR_REASONS:
            counts[f"n_{side}_{r}"] = int(tally[r])
    return PostChangeGaps(events, offs, values, counts, params)


def _walk(
    fr: _Frame,
    *,
    j1: NDArray[np.int64],
    target: NDArray[np.int64],
    tidx0: NDArray[np.int64],
    step: int,
    k_max: int,
    k_off: NDArray[np.int64],
    lim: tuple[int, int],
    values: dict[str, dict[str, NDArray[np.float64]]],
    reason: dict[str, NDArray[np.object_]],
    last_off: dict[str, NDArray[np.float64]],
    partner0: dict[str, NDArray[np.int64]],
    offs: NDArray[np.float64],
    follow: dict[str, float],
    min_speed_ms: float,
    tol_m: float,
) -> None:
    """Walk every change forward from its sample, filling the side arrays in place."""
    n_c = j1.size
    c_row = j1.copy()
    lead0, lag0 = fr.neighbours(c_row, target)
    part = {"follower": lag0, "leader": lead0}
    alive = {}
    for side in SIDES:
        p = part[side]
        partner0[side][:] = p
        rear, front = (p, c_row) if side == "follower" else (c_row, p)
        gap = fr.gap_behind(rear, front)
        following, suspect = _following(gap, fr.v[np.maximum(rear, 0)], **follow)
        reason[side][p < 0] = "no_partner"
        with np.errstate(invalid="ignore"):
            beyond = (p >= 0) & ~(gap <= follow["max_range_m"])
        reason[side][beyond] = "no_partner"
        reason[side][(p >= 0) & ~beyond & suspect] = "suspect"
        reason[side][(p >= 0) & ~beyond & ~suspect & ~following] = "not_following"
        alive[side] = following.copy()
        partner0[side][beyond] = -1
    col_of = {int(k): i for i, k in enumerate(k_off)}

    def record(side: str, col: int, rows_c: NDArray[np.int64], rows_p: NDArray[np.int64]) -> None:
        ok = alive[side]
        if not ok.any():
            return
        rear, front = (rows_p, rows_c) if side == "follower" else (rows_c, rows_p)
        rear = np.where(ok, rear, -1)
        front = np.where(ok, front, -1)
        gap = fr.gap_behind(rear, front)
        v_r = np.where(ok, fr.v[np.maximum(rear, 0)], np.nan)
        v_f = np.where(ok, fr.v[np.maximum(front, 0)], np.nan)
        vals = values[side]
        vals["space_gap_m"][ok, col] = gap[ok]
        vals["time_gap_s"][ok, col] = _time_gap(gap, v_r, min_speed_ms)[ok]
        vals["rear_v_ms"][ok, col] = v_r[ok]
        vals["front_v_ms"][ok, col] = v_f[ok]
        last_off[side][ok] = offs[col]

    if 0 in col_of:
        for side in SIDES:
            record(side, col_of[0], c_row, part[side])

    for k in range(1, k_max + 1):
        if not (alive["follower"].any() or alive["leader"].any()):
            break
        any_alive = alive["follower"] | alive["leader"]
        tq = tidx0 + k * step
        out_of_window = any_alive & ((tq > lim[1]) | (tq < lim[0]))
        for side in SIDES:
            end = alive[side] & out_of_window
            reason[side][end] = "window_end"
            alive[side] &= ~end
        any_alive &= ~out_of_window
        # the changer: its own id in the same (debounced) lane run, else a continuing fragment
        code = np.where(any_alive, fr.veh[np.maximum(c_row, 0)], -1)
        own = fr.find(code, tq)
        lane_changed = (own >= 0) & (
            fr.cum_lane_break[np.maximum(own, 0)] != fr.cum_lane_break[c_row]
        )
        stitched = np.full(n_c, -1, dtype=np.int64)
        need = any_alive & (own < 0)
        if need.any():
            prev = np.where(need, c_row, -1)
            stitched = fr.track(prev, tq, target, tol_m)
        new_c = np.where(own >= 0, own, stitched)
        lost_c = any_alive & (new_c < 0)
        for side in SIDES:
            end_lc = alive[side] & lane_changed
            reason[side][end_lc] = "changer_lane_change"
            end_lost = alive[side] & ~lane_changed & lost_c
            reason[side][end_lost] = "changer_lost"
            alive[side] &= ~(end_lc | end_lost)
        c_now = np.where(any_alive & ~lane_changed & ~lost_c, new_c, -1)
        lead, lag = fr.neighbours(c_now, target)
        cand = {"follower": lag, "leader": lead}
        for side in SIDES:
            a = alive[side]
            if not a.any():
                continue
            prev = part[side]
            nxt = cand[side]
            same = fr.continues(prev, nxt, tol_m) & a
            # why a partner is no longer there: its own track at this instant
            p_code = np.where(a & ~same, fr.veh[np.maximum(prev, 0)], -1)
            p_self = fr.find(p_code, tq)
            gone = a & ~same
            p_lost = gone & (p_self < 0)
            p_lane = gone & (p_self >= 0) & (fr.lane_held[np.maximum(p_self, 0)] != target)
            p_cut = gone & ~p_lost & ~p_lane
            reason[side][p_lost] = "partner_lost"
            reason[side][p_lane] = "partner_lane_change"
            reason[side][p_cut] = "cut_in"
            rear, front = (nxt, c_now) if side == "follower" else (c_now, nxt)
            gap = fr.gap_behind(np.where(same, rear, -1), np.where(same, front, -1))
            following, suspect = _following(gap, fr.v[np.maximum(rear, 0)], **follow)
            reason[side][same & suspect] = "suspect"
            reason[side][same & ~suspect & ~following] = "gap_bound"
            alive[side] = same & following
            part[side] = np.where(alive[side], nxt, -1)
        c_row = np.where(alive["follower"] | alive["leader"], c_now, c_row)
        if k in col_of:
            for side in SIDES:
                record(side, col_of[k], c_row, part[side])


def _own_reference(
    fr: _Frame,
    start: NDArray[np.int64],
    *,
    tidx0: NDArray[np.int64],
    step: int,
    k_range: tuple[int, int],
    lim: tuple[int, int],
    follow: dict[str, float],
    min_speed_ms: float,
    tol_m: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """The rear vehicle's own reference instants over the pre-change window.

    Walks each vehicle back from ``start`` (by id, else a continuing fragment
    in its lane) and, at the walk instants ``k_range`` steps before the
    change, reads its gap to its leader in its own lane.

    Returns:
        ``(time_gap, gap, v)``: ``(n, n_instants)`` arrays, NaN at instants
        that are not reference instants (not tracked, not car-following, or
        below ``min_speed_ms``).
    """
    n_c = start.size
    k_lo, k_hi = k_range
    width = max(k_hi - k_lo + 1, 0)
    tgs = np.full((n_c, width), np.nan)
    gaps = np.full((n_c, width), np.nan)
    vs = np.full((n_c, width), np.nan)
    row = start.copy()
    for k in range(1, k_hi + 1):
        if not (row >= 0).any():
            break
        tq = tidx0 - k * step
        inside = (row >= 0) & (tq >= lim[0]) & (tq <= lim[1])
        prev = np.where(inside, row, -1)
        lanes = fr.lane_held[np.maximum(prev, 0)]
        row = fr.track(prev, tq, lanes, tol_m)
        if k < k_lo:
            continue
        lead, _ = fr.neighbours(row, fr.lane_held[np.maximum(row, 0)])
        gap = fr.gap_behind(row, lead)
        v_r = np.where(row >= 0, fr.v[np.maximum(row, 0)], np.nan)
        following, _ = _following(gap, v_r, **follow)
        tg = _time_gap(gap, v_r, min_speed_ms)
        ok = following & np.isfinite(tg)
        tgs[:, k - k_lo] = np.where(ok, tg, np.nan)
        gaps[:, k - k_lo] = np.where(ok, gap, np.nan)
        vs[:, k - k_lo] = np.where(ok, v_r, np.nan)
    return tgs, gaps, vs


def _row_median(a: NDArray[np.float64], enough: NDArray[np.bool_]) -> NDArray[np.float64]:
    """Row medians of the finite values where ``enough``; NaN elsewhere."""
    out = np.full(a.shape[0], np.nan)
    rows = enough & np.any(np.isfinite(a), axis=1)
    if rows.any():
        out[rows] = np.nanmedian(a[rows], axis=1)
    return out


def with_population_ratio(
    result: PostChangeGaps, normal: NormalTimeGaps, *, min_n: int = DEFAULT_MIN_N_NORMAL
) -> PostChangeGaps:
    """``result`` with ``ratio_pop`` filled: each time gap over the population's median at the
    rear vehicle's speed bin (NaN where that bin has fewer than ``min_n`` samples)."""
    values = {side: dict(vals) for side, vals in result.values.items()}
    for side in SIDES:
        vals = values[side]
        ref = normal.lookup(vals["rear_v_ms"], min_n=min_n)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = vals["time_gap_s"] / ref
        ratio[~np.isfinite(ratio)] = np.nan
        vals["ratio_pop"] = ratio
    params = {**result.parameters, "ratio_pop_min_n": min_n}
    return PostChangeGaps(result.events, result.offsets_s, values, dict(result.counts), params)


def concat_results(parts: Sequence[PostChangeGaps]) -> PostChangeGaps:
    """Pool chunk or run results that share their offsets (counts summed)."""
    if not parts:
        raise ValueError("nothing to concatenate")
    offs = parts[0].offsets_s
    for p in parts[1:]:
        if not np.array_equal(p.offsets_s, offs):
            raise ValueError("results with different offsets cannot be pooled")
    nonempty = [p for p in parts if len(p.events)]
    events = (
        pd.concat([p.events for p in nonempty], ignore_index=True)
        if nonempty
        else parts[0].events.iloc[0:0]
    )
    values = {
        side: {
            name: (
                np.concatenate([p.values[side][name] for p in parts], axis=0)
                if parts
                else np.empty((0, offs.size))
            )
            for name in parts[0].values[side]
        }
        for side in SIDES
    }
    counts: dict[str, int] = {}
    for p in parts:
        for k, v in p.counts.items():
            counts[k] = counts.get(k, 0) + int(v)
    return PostChangeGaps(events, offs, values, counts, dict(parts[-1].parameters))


# --- the fit ------------------------------------------------------------------------------


def _fit_exp(
    off: NDArray[np.float64], y: NDArray[np.float64], w: NDArray[np.float64]
) -> dict[str, float | bool]:
    """Weighted least squares of ``r_inf + (r0 − r_inf) exp(−τ/τ_r)`` over the τ grid.

    For each τ_r the model is linear in ``(r_inf, r0)`` (basis ``1 − e``,
    ``e``); the τ_r with the smallest weighted SSE wins. ``interior`` is False
    when it sits at either end of the grid.
    """
    tau = _TAU_GRID_S
    e = np.exp(-off[None, :] / tau[:, None])
    a = 1.0 - e
    saa = (w * a * a).sum(axis=1)
    sab = (w * a * e).sum(axis=1)
    sbb = (w * e * e).sum(axis=1)
    say = (w * a * y).sum(axis=1)
    sby = (w * e * y).sum(axis=1)
    det = saa * sbb - sab * sab
    good = np.abs(det) > 1e-12 * np.maximum(saa * sbb, 1e-300)
    with np.errstate(divide="ignore", invalid="ignore"):
        r_inf = np.where(good, (say * sbb - sby * sab) / det, np.nan)
        r0 = np.where(good, (sby * saa - say * sab) / det, np.nan)
    resid = y[None, :] - r_inf[:, None] * a - r0[:, None] * e
    sse = np.where(good, (w * resid * resid).sum(axis=1), np.inf)
    i = int(np.argmin(sse))
    return {
        "tau_s": float(tau[i]),
        "r0": float(r0[i]),
        "r_inf": float(r_inf[i]),
        "sse": float(sse[i]),
        "interior": bool(0 < i < tau.size - 1 and np.isfinite(sse[i])),
    }


def _ci(a: NDArray[np.float64]) -> list[float] | None:
    """2.5 / 97.5 percentiles of the finite values (None when there are none)."""
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    lo, hi = np.percentile(a, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def fit_relaxation(
    offsets_s: Sequence[float] | NDArray[np.float64],
    values: NDArray[np.float64],
    *,
    min_n: int = DEFAULT_MIN_N_FIT,
    min_offsets: int = DEFAULT_MIN_OFFSETS_FIT,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """An exponential relaxation fitted to per-offset medians, with a bootstrap over the rows.

    ``r(τ) = r_∞ + (r_0 − r_∞) · exp(−τ / τ_r)`` is fitted by weighted least
    squares (weights: the rows read at each offset) to the medians of the
    offsets read by at least ``min_n`` rows. The bootstrap resamples rows
    (sides) with replacement, recomputes the medians at the same offsets and
    refits. A fit is ``supported`` only when all of these hold, else
    ``reason`` names the first that fails:

    1. at least ``min_offsets`` offsets are read by ``min_n`` rows;
    2. the point fit's ``τ_r`` is interior to the grid and lies between the
       first positive offset used and the last offset used (outside, the
       relaxation is faster than the first offset or slower than the window);
    3. at least :data:`MIN_RESOLVED_SHARE` of the bootstrap refits satisfy 2;
    4. the amplitude ``r_∞ − r_0`` has a 95 % bootstrap interval (over every
       refit) that excludes 0 (else the curve is flat within its noise);
    5. the point fit describes the medians: its weighted RMS residual is at
       most :data:`MAX_REL_RMS` of ``|r_∞ − r_0|`` (a curve that opens and
       closes again is not a relaxation, whatever τ_r fits its first part).

    ``reason`` joins the failures of 2 or 3, of 4 and of 5 (``"supported"``
    when none fails). A curve that is at its final level from the first offset on
    reads "faster than the first offset"; a flat one adds "no relaxation".

    Args:
        offsets_s: Offsets [s] of the columns.
        values: ``(n_rows, n_offsets)``; NaN = not read.
        min_n: Rows an offset needs.
        min_offsets: Offsets the fit needs.
        n_boot: Bootstrap replicates (0 = none; then 3 and 4 cannot hold).
        seed: RNG seed (``flowstate_core.rng.make_rng``).

    Returns:
        ``supported``, ``reason``, ``offsets_used``, ``n_used`` (rows per
        offset used), ``medians``, ``tau_s``, ``tau_resolved``, ``r0``,
        ``r_inf`` (each with ``*_ci95`` from the resolved refits),
        ``amplitude`` (``r_inf − r0``, its ``amplitude_ci95`` over every
        refit, ``amplitude_excludes_zero``), ``rms_resid`` and
        ``rel_rms_resid`` (the weighted RMS residual, and over the
        amplitude), ``n_boot``, ``n_boot_resolved``;
        the point values are null when no fit was made.
    """
    from flowstate_core.rng import make_rng

    off = np.asarray(offsets_s, dtype=np.float64)
    vals = np.asarray(values, dtype=np.float64)
    if vals.ndim != 2 or vals.shape[1] != off.size:
        raise ValueError("values must be (n_rows, n_offsets)")
    n_k = np.sum(np.isfinite(vals), axis=0)
    use = n_k >= max(min_n, 1)
    out: dict[str, Any] = {
        "supported": False,
        "reason": "",
        "offsets_used": [float(o) for o in off[use]],
        "n_used": [int(n) for n in n_k[use]],
        "medians": None,
        "tau_s": None,
        "tau_resolved": None,
        "tau_ci95": None,
        "r0": None,
        "r0_ci95": None,
        "r_inf": None,
        "r_inf_ci95": None,
        "amplitude": None,
        "amplitude_ci95": None,
        "amplitude_excludes_zero": None,
        "rms_resid": None,
        "rel_rms_resid": None,
        "n_boot": int(n_boot),
        "n_boot_resolved": 0,
    }
    if int(use.sum()) < max(min_offsets, 2):
        out["reason"] = f"fewer than {min_offsets} offsets read by at least {min_n} sides"
        return out
    sub = vals[:, use]
    sub = sub[np.any(np.isfinite(sub), axis=1)]
    o = off[use]
    w = n_k[use].astype(np.float64)
    med = np.nanmedian(sub, axis=0)
    out["medians"] = [round(float(m), 4) for m in med]
    pos = o[o > 0]
    lo_tau = float(pos.min()) if pos.size else math.inf
    hi_tau = float(o.max())

    def resolved(fit: dict[str, float | bool]) -> bool:
        return bool(fit["interior"]) and lo_tau <= float(fit["tau_s"]) <= hi_tau

    point = _fit_exp(o, med, w)
    amp_pt = float(point["r_inf"]) - float(point["r0"])
    rms = math.sqrt(max(float(point["sse"]), 0.0) / float(w.sum()))
    rel_rms = rms / abs(amp_pt) if amp_pt != 0.0 else math.inf
    out.update(
        tau_s=round(float(point["tau_s"]), 4),
        tau_resolved=resolved(point),
        r0=round(float(point["r0"]), 4),
        r_inf=round(float(point["r_inf"]), 4),
        amplitude=round(amp_pt, 4),
        rms_resid=round(rms, 4),
        rel_rms_resid=round(rel_rms, 4) if math.isfinite(rel_rms) else None,
    )
    boots: list[dict[str, float | bool]] = []
    if n_boot > 0 and sub.shape[0] > 1:
        rng = make_rng(seed)
        n = sub.shape[0]
        for _ in range(n_boot):
            idx = rng.integers(0, n, size=n)
            b = sub[idx]
            nb = np.sum(np.isfinite(b), axis=0)
            if np.any(nb == 0):
                continue
            with np.errstate(all="ignore"):
                mb = np.nanmedian(b, axis=0)
            boots.append(_fit_exp(o, mb, nb.astype(np.float64)))
    ok = [b for b in boots if resolved(b)]
    out["n_boot_resolved"] = len(ok)
    if ok:
        tau_b = np.array([float(b["tau_s"]) for b in ok])
        r0_b = np.array([float(b["r0"]) for b in ok])
        ri_b = np.array([float(b["r_inf"]) for b in ok])
        out["tau_ci95"] = _ci(tau_b)
        out["r0_ci95"] = _ci(r0_b)
        out["r_inf_ci95"] = _ci(ri_b)
    if boots:
        # the amplitude over every refit, resolved or not: a flat curve has no resolved tau_r
        amp_b = np.array([float(b["r_inf"]) - float(b["r0"]) for b in boots])
        out["amplitude_ci95"] = _ci(amp_b)
    amp = out["amplitude_ci95"]
    flat = amp is None or amp[0] <= 0.0 <= amp[1]
    out["amplitude_excludes_zero"] = None if amp is None else not flat
    reasons: list[str] = []
    if not resolved(point):
        tau = float(point["tau_s"])
        if (point["interior"] and tau < lo_tau) or (not point["interior"] and tau < 1.0):
            reasons.append(
                f"tau_r {tau:.3g} s is faster than the first offset ({lo_tau:g} s): the gap is "
                "at its fitted final level by then"
            )
        else:
            reasons.append(
                f"tau_r {tau:.3g} s is not resolved: slower than the window (last offset "
                f"{hi_tau:g} s)"
            )
    elif n_boot <= 0 or len(ok) < MIN_RESOLVED_SHARE * max(n_boot, 1):
        reasons.append(
            f"unstable: {len(ok)} of {n_boot} bootstrap refits resolve tau_r "
            f"(needs {MIN_RESOLVED_SHARE:.0%})"
        )
    if flat:
        reasons.append("no relaxation: the amplitude's 95 % interval contains 0")
    if not rel_rms <= MAX_REL_RMS:
        reasons.append(
            "the medians are not an exponential approach: weighted RMS residual "
            f"{rms:.3g} is {rel_rms:.2g} of the amplitude (at most {MAX_REL_RMS:g})"
        )
    if reasons:
        out["reason"] = "; ".join(reasons)
        return out
    out["supported"] = True
    out["reason"] = "supported"
    return out


# --- summaries ----------------------------------------------------------------------------


def _q(a: NDArray[np.float64], q: float) -> list[float | None]:
    """Column-wise quantile of the finite values, rounded (None where a column is empty)."""
    out: list[float | None] = []
    for col in a.T:
        c = col[np.isfinite(col)]
        out.append(None if c.size == 0 else round(float(np.quantile(c, q)), 4))
    return out


def _side_summary(
    result: PostChangeGaps,
    rows: NDArray[np.int64],
    side: str,
    *,
    measures: Sequence[str],
    fit_measures: Sequence[str],
    complete_to_s: float,
    min_n_fit: int,
    min_offsets_fit: int,
    n_boot: int,
    seeds: Sequence[int],
) -> dict[str, Any]:
    ev = result.events.iloc[rows]
    vals = {m: result.values[side][m][rows] for m in (*measures, *SPEEDS)}
    read = np.isfinite(result.values[side]["space_gap_m"][rows])
    censor = Counter(ev[f"{side}_censor"].astype(str).tolist())
    measured = rows.size - censor["no_partner"] - censor["not_following"]
    refs = ev[[f"{side}_ref_own_s"]].to_numpy(dtype=float)
    out: dict[str, Any] = {
        "n_changes": int(rows.size),
        "n_measured": int(measured),
        "n_ref_own": int(np.sum(np.isfinite(refs))),
        "censor": {r: int(censor[r]) for r in CENSOR_REASONS if censor[r]},
        "n": [int(v) for v in read.sum(axis=0)],
        "ref_own_s": {f"p{round(q * 100)}": _q(refs, q)[0] for q in (0.25, 0.5, 0.75)},
        "measures": {},
        "rear_v_ms_p50": _q(vals["rear_v_ms"], 0.5),
    }
    for m in measures:
        a = vals[m]
        out["measures"][m] = {
            "n": [int(v) for v in np.isfinite(a).sum(axis=0)],
            "p25": _q(a, 0.25),
            "p50": _q(a, 0.5),
            "p75": _q(a, 0.75),
        }
    horizon = result.offsets_s <= complete_to_s + 1e-9
    complete = read[:, horizon].all(axis=1) if horizon.any() else np.zeros(rows.size, bool)
    out["complete_case"] = {
        "horizon_s": complete_to_s,
        "n": int(complete.sum()),
        "p50": {m: _q(vals[m][complete][:, horizon], 0.5) for m in measures},
    }
    fits: dict[str, Any] = {}
    for i, m in enumerate(fit_measures):
        fits[m] = fit_relaxation(
            result.offsets_s,
            vals[m],
            min_n=min_n_fit,
            min_offsets=min_offsets_fit,
            n_boot=n_boot,
            seed=int(seeds[i]),
        )
    out["fits"] = fits
    return out


def summarize_relaxation(
    result: PostChangeGaps,
    *,
    by: Sequence[str] = ("zone_kind", "movement"),
    speed_classes: Sequence[tuple[str, float, float]] = SPEED_CLASSES_MS,
    movements: Sequence[str] | None = ("entering", "exiting", "through"),
    include_unconfirmed: bool = False,
    include_suspect: bool = False,
    measures: Sequence[str] = MEASURES,
    fit_measures: Sequence[str] = RATIOS,
    complete_to_s: float = DEFAULT_COMPLETE_TO_S,
    min_n_fit: int = DEFAULT_MIN_N_FIT,
    min_offsets_fit: int = DEFAULT_MIN_OFFSETS_FIT,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, Any]]:
    """Per group, changer-speed class and side: counts, per-offset quantiles and the fits.

    Rows are per combination of the ``by`` columns present in the events, each
    with ``speed_class`` ``"all"`` and one per non-empty ``speed_classes``
    entry (the changer's speed at the change, half-open bounds), and per side. Changes
    that are unconfirmed or suspect (the extraction's flags) are left out
    unless asked for; so are movements outside ``movements`` (None keeps
    all). Each row: the group keys, ``speed_class``, ``side``, ``offsets_s``
    and :func:`_side_summary`'s block (``n_changes``, ``n_measured``,
    ``n_ref_own``, ``censor``, ``n`` per offset, ``ref_own_s`` quantiles,
    ``measures.<m>.{n, p25, p50, p75}`` per offset, ``rear_v_ms_p50``,
    ``complete_case`` and ``fits.<m>`` from :func:`fit_relaxation`). Each
    row's bootstrap seeds come from ``flowstate_core.rng.spawn_seeds(seed,
    n_rows)``, one per fitted measure.

    Args:
        result: :class:`PostChangeGaps` (with ``ratio_pop`` filled when wanted).
        by: Grouping columns of ``result.events``.
        speed_classes: ``(label, lo, hi)`` changer-speed classes [m/s].
        movements: Movements kept; None = all.
        include_unconfirmed: Keep ``confirmed == False`` changes.
        include_suspect: Keep ``suspect == True`` changes.
        measures: Measures summarized.
        fit_measures: Measures fitted.
        complete_to_s: Horizon of the complete-case curve [s].
        min_n_fit: Sides an offset needs to enter a fit.
        min_offsets_fit: Offsets a fit needs.
        n_boot: Bootstrap replicates per fit.
        seed: Master seed.

    Returns:
        One dict per group, speed class and side, groups in sorted order.
    """
    from flowstate_core.rng import spawn_seeds

    if not isinstance(result.events.index, pd.RangeIndex) or result.events.index.start != 0:
        result = PostChangeGaps(
            result.events.reset_index(drop=True),
            result.offsets_s,
            result.values,
            result.counts,
            result.parameters,
        )
    ev = result.events
    if len(ev) == 0:
        return []
    keep = np.ones(len(ev), dtype=bool)
    if not include_unconfirmed and "confirmed" in ev.columns:
        keep &= ev["confirmed"].astype(bool).to_numpy()
    if not include_suspect and "suspect" in ev.columns:
        keep &= ~ev["suspect"].astype(bool).to_numpy()
    if movements is not None and "movement" in ev.columns:
        keep &= ev["movement"].isin(list(movements)).to_numpy()
    keys = [k for k in by if k in ev.columns]
    idx = np.flatnonzero(keep)
    if idx.size == 0:
        return []
    sub = ev.iloc[idx]
    tasks: list[tuple[dict[str, str], str, NDArray[np.int64]]] = []
    groups = sub.groupby(keys, sort=True) if keys else [((), sub)]
    for gkey, g in groups:
        key_t = gkey if isinstance(gkey, tuple) else (gkey,)
        head = {k: str(v) for k, v in zip(keys, key_t, strict=True)}
        rows = np.asarray(g.index, dtype=np.int64)
        tasks.append((head, "all", rows))
        vv = ev["v"].to_numpy(dtype=float)[rows]
        for label, lo, hi in speed_classes:
            in_class = rows[(vv >= lo) & (vv < hi)]
            if in_class.size:
                tasks.append((head, label, in_class))
    n_rows = len(tasks) * len(SIDES)
    row_seeds = spawn_seeds(seed, max(n_rows, 1))
    out: list[dict[str, Any]] = []
    i = 0
    for head, label, rows in tasks:
        for side in SIDES:
            seeds = spawn_seeds(row_seeds[i], max(len(fit_measures), 1))
            i += 1
            block = _side_summary(
                result,
                rows,
                side,
                measures=measures,
                fit_measures=fit_measures,
                complete_to_s=complete_to_s,
                min_n_fit=min_n_fit,
                min_offsets_fit=min_offsets_fit,
                n_boot=n_boot,
                seeds=seeds,
            )
            out.append(
                {
                    **head,
                    "speed_class": label,
                    "side": side,
                    "offsets_s": [float(o) for o in result.offsets_s],
                    **block,
                }
            )
    return out


def sample_events(
    result: PostChangeGaps,
    n: int,
    *,
    seed: int,
    measures: Sequence[str] = ("time_gap_s", "ratio_own"),
) -> dict[str, Any]:
    """A seeded sample of the measured changes as a compact table (for artifacts).

    Draws at most ``n`` changes whose follower side was measured, and gives
    per change its carried columns, both sides' ids, references and censor
    reasons, and each side's ``measures`` at every offset (null where not
    read).

    Returns:
        ``{n, seed, offsets_s, columns, rows}``.
    """
    from flowstate_core.rng import make_rng

    ev = result.events
    cand = np.flatnonzero(
        ~ev["follower_censor"].isin(["no_partner", "not_following"]).to_numpy()
        if len(ev)
        else np.zeros(0, dtype=bool)
    )
    k = min(n, cand.size)
    pick = np.sort(make_rng(seed).choice(cand, size=k, replace=False)) if k else cand[:0]
    base = [c for c in ("t", "veh_id", "x", "zone_kind", "movement", "v", "seed") if c in ev]
    side_cols = [f"{s}_{c}" for s in SIDES for c in ("id", "ref_own_s", "censor", "last_offset_s")]
    curve_cols = [f"{s}_{m}" for s in SIDES for m in measures]
    rows: list[list[Any]] = []
    for r in pick:
        row: list[Any] = []
        for c in (*base, *side_cols):
            row.append(_json_value(ev.iloc[r][c]))
        for s in SIDES:
            for m in measures:
                row.append([_json_value(v) for v in result.values[s][m][r]])
        rows.append(row)
    return {
        "n": int(k),
        "seed": seed,
        "offsets_s": [float(o) for o in result.offsets_s],
        "columns": [*base, *side_cols, *curve_cols],
        "rows": rows,
    }


def _json_value(val: Any) -> Any:
    """A JSON-ready scalar (NaN → None, numpy → Python, floats rounded)."""
    if val is None:
        return None
    if isinstance(val, float | np.floating):
        f = float(val)
        return None if not math.isfinite(f) else round(f, 4)
    if isinstance(val, np.bool_ | bool):
        return bool(val)
    if isinstance(val, np.integer):
        return int(val)
    return val
