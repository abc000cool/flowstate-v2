"""Accepted lane-change gaps from trajectories, observed or simulated (WP-77).

The weave model (``microsim.runner._weave_step``, docs/WEAVE_MODEL_PLAN.md)
accepts a lane change when the target-lane gaps clear a set of terms — the
movement's time gap ``s0 + accept · v`` (``WEAVE_DEFAULTS`` ``accept_gap_s``
/ ``exit_accept_gap_s``), the changer's brake gap on its new leader, the new
follower absorbing the changer within its comfortable deceleration and the
forced-change guard — and SUMO's LC2013 decides every other change. None of
those terms had been compared with how real drivers change lanes. This
module measures, for every lane change in a trajectory table, the gaps the
driver actually took, and evaluates the model's acceptance on the same gaps,
so an observed table (I-24 MOTION) and a simulated one (a microsim run) give
directly comparable records.

**Input.** A frame with columns ``t`` [s], ``veh_id``, ``x`` [m, the FRONT
bumper, increasing along travel], ``lane`` (band convention: 1 = leftmost
lane, increasing to the right) and ``v`` [m/s]; an optional ``length`` [m]
column, else every vehicle has ``default_length_m``. Samples of different
vehicles must share one time grid (I-24 MOTION's processed Parquet snaps to
the 0.04 s grid and keeps every fifth slot; a microsim run records every
vehicle at the output cadence), because neighbours are looked up at the
change's own time stamp. A simulated frame carries SUMO's per-edge lane index
(0 = rightmost); :func:`sim_band_lanes` maps it to the band convention first.

**Lane changes and lane-assignment noise.** I-24 MOTION publishes no lane:
the loader derives it as ``floor(y / 12 ft)`` from the tracker's smoothed
lateral position (``calibration.loaders.i24motion``), so a vehicle driving
near a lane line produces a band index that flickers across it. Changes are
therefore read off the *debounced* lane sequence of
:func:`calibration.lanechange.held_lanes` — the same debounce the committed
lane-change observables use: a stay shorter than ``min_dwell_s`` (1 s, five
5 Hz samples) that returns to the lane it came from (A-B-A) is a lane-line
excursion and is reassigned to that lane; a short stay that is not a return
(A-B-C) is kept. On the first 15-min chunk of the study period that guard
removes 0.9 % of the transitions (2.2369 → 2.2166 changes per veh-km,
``min_dwell_sensitivity_first_chunk`` of ``artifacts/i24_lanechange_observed.json``).
Two further guards are applied here and counted, never silent:

* a transition that skips a lane within one sample interval (``|Δlane| > 1``)
  is not a lane change at 5 Hz and is dropped (``n_nonadjacent``);
* a change made within ``min_dwell_s`` of the start or the end of the
  vehicle's track (the run on either side is that short and is bounded by
  the track, not by another change) is recorded with ``confirmed = False``:
  a tracker's lateral position drifts as a fragment starts or dies, which
  the A-B-A guard cannot see. :func:`summarize_gaps` leaves those out unless
  asked. On a simulated run the track's start and end are the recording's
  own boundaries (on-ramp and off-ramp edges are not recorded), so the
  flag there marks changes made just after arriving from a ramp or just
  before leaving onto one, not noise; the comparison states both counts.

A change is timed at the first sample in the new lane — for a tracked
vehicle, the first sample after its centre crossed the lane line; for SUMO,
the step in which the (instantaneous) change was executed.

**Neighbours.** At the change's time stamp, among the vehicles whose
debounced lane is the target lane: the *lead* is the nearest vehicle whose
front is strictly ahead of the changer's front, the *lag* the nearest whose
front is at or behind it. Gaps are bumper to bumper: ``lead_gap = x_lead −
length_lead − x`` and ``lag_gap = x − length − x_lag``; time gaps divide by
the speed of the vehicle behind (the changer for the lead gap, the lag for
the lag gap); closing speeds are positive when the rear vehicle is faster
(``v − v_lead`` and ``v_lag − v``). A neighbour farther than
``max_range_m`` counts as none. A gap below ``min_gap_m`` (0.5 m, the
duplicate-fragment bound of ``scripts/i24_data.py`` ``MIN_GAP_M``) marks the
change ``suspect`` — on I-24 MOTION that is most often a duplicate fragment
of the changer itself — and suspect changes are left out of the summaries.

**Coverage (I-24 MOTION, docs/I24_DATA.md §4).** The instrument tracks about
half of the vehicle-time in the peak (lane 1 at 0.70–0.76, interior lane 3
at 0.40–0.54), so the nearest *tracked* vehicle is not always the nearest
vehicle. What that bounds, on the changes recorded, and what it does not:

* A recorded space gap is the true gap or larger, and so is a lead time gap
  (over the changer's own speed). These, and their quantiles, are upper
  bounds.
* When the true neighbour is untracked, the recorded one is a *different
  vehicle* at a different speed. A lag time gap divides by that vehicle's
  speed, and the closing speeds use it, so a lag time gap can read shorter
  than the true one. Its expected direction is longer, but it is not a bound.
* Only ``ok_lead_time`` compares a gap with the changer's own speed. The
  leader's brake gap, both follower-side terms and the forced guard read a
  neighbour's speed, so a farther but faster recorded follower can turn an
  acceptance into a refusal. **The share of observed changes the model's
  acceptance refuses is expected to be lower than on complete data, but it is
  not a lower bound.** Only the refusals of ``ok_lead_time`` alone are.

**The model's acceptance** (:func:`weave_acceptance`) restates
``microsim.runner._weave_change_ok`` — the acceptance of ``_weave_step`` read
off given gaps — in bumper-to-bumper terms (SUMO reports the leader-side
gap net of the changer's ``minGap`` and the follower-side gap net of the
follower's), evaluated with one parameter set for the changer and its
follower (:class:`AcceptanceParams`, the population means of the fleet's IDM
artifact); ``tests/test_calibration/test_calibration_lane_change_gaps.py``
checks the restatement against the runner's function case by case.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, Final

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from calibration.lanechange import (
    DEFAULT_MAX_GAP_FACTOR,
    DEFAULT_MIN_DWELL_S,
    band_lane_from_sim,
    held_lanes,
    infer_dt,
)
from flowstate_core.config import WEAVE_DEFAULTS

ZONE_KINDS: Final[frozenset[str]] = frozenset({"merge", "diverge", "weave", "basic"})
"""Zone kinds: an acceleration lane, a deceleration lane, a weaving section,
and anything else (a change outside every zone is in ``basic``)."""

MOVEMENTS: Final[tuple[str, ...]] = ("entering", "exiting", "through", "unknown")
"""Lane-change movements (:func:`classify_movement`)."""

DEFAULT_MIN_GAP_M: Final[float] = 0.5
"""A neighbour closer than this (bumper to bumper) marks the change suspect:
``scripts/i24_data.py`` ``MIN_GAP_M``, the documented duplicate-fragment
bound of I-24 MOTION."""

DEFAULT_MAX_RANGE_M: Final[float] = 200.0
"""Neighbour search range [m]. Beyond it a vehicle does not bind the model's
acceptance at the fleet's means (``artifacts/idm_i24_capacity.json``): at
25 m/s and speed parity the leader side needs ``2 s0 + 0.6 · v`` ≈ 20 m and
the follower side 24–29 m (v0 capped at 31.29 or 24.59 m/s); only a closing
speed above ~25 m/s (a brake gap ``Δv² / 2b`` of 184 m at b = 1.70 m/s²)
would reach it."""

SPEED_CLASSES_MS: Final[tuple[tuple[str, float, float], ...]] = (
    ("v<10", 0.0, 10.0),
    ("10<=v<20", 10.0, 20.0),
    ("v>=20", 20.0, math.inf),
)
"""Changer-speed classes of :func:`summarize_gaps` [m/s]; 20 m/s is the
free-flow boundary the T.H.52 criterion reads (``TH52_FREE_FLOW_MS``)."""

QUANTILES: Final[tuple[float, ...]] = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9)
"""Quantiles reported per distribution."""

MIN_SPEED_FOR_TIME_GAP_MS: Final[float] = 0.1
"""A time gap needs a moving rear vehicle; below this speed it is NaN
(SUMO's halting speed)."""

ACCEPTANCE_TERMS: Final[tuple[str, ...]] = (
    "ok_lead_time",
    "ok_lead_brake",
    "ok_lag_time",
    "ok_lag_absorb",
    "ok_guard",
)
"""The acceptance's terms, in the order ``_weave_step`` reads them."""


@dataclass(frozen=True)
class Zone:
    """A stretch of road with a kind, half-open ``[x_lo_m, x_hi_m)``.

    Attributes:
        name: Label (e.g. ``"HH_on_BR_off_weave"``).
        kind: One of :data:`ZONE_KINDS`.
        x_lo_m: Upstream end [m] on the frame's ``x`` axis.
        x_hi_m: Downstream end [m].
    """

    name: str
    kind: str
    x_lo_m: float
    x_hi_m: float

    def __post_init__(self) -> None:
        if self.kind not in ZONE_KINDS:
            raise ValueError(f"zone kind must be one of {sorted(ZONE_KINDS)}, got {self.kind!r}")
        if not self.x_hi_m > self.x_lo_m:
            raise ValueError(f"zone {self.name!r}: x_hi_m must exceed x_lo_m")

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {"name": self.name, "kind": self.kind, "x_lo_m": self.x_lo_m, "x_hi_m": self.x_hi_m}


@dataclass(frozen=True)
class AcceptanceParams:
    """The weave acceptance's inputs for one (mean) changer and follower.

    Attributes:
        accept_gap_s: Time gap of a change to the left [s]
            (``WEAVE_DEFAULTS["accept_gap_s"]``; the entering movement).
        exit_accept_gap_s: Time gap of a change to the right [s]
            (``WEAVE_DEFAULTS["exit_accept_gap_s"]``; the exiting movement).
        s0_m: Minimum gap of changer and follower [m] (SUMO ``minGap``).
        T_s: Follower's desired time headway [s] (SUMO ``tau``).
        a_max: Follower's maximum acceleration [m/s²] (SUMO ``accel``).
        b: Comfortable deceleration of changer and follower [m/s²]
            (SUMO ``decel``).
        v0_ms: Follower's desired speed [m/s] — the runner uses
            ``min(maxSpeed, lane limit)``, so pass the capped value.
        source: Where the numbers come from (recorded in artifacts).
        accept_lag_gap_s: Follower-side time gap of a change to the left
            [s] (``WEAVE_DEFAULTS["accept_lag_gap_s"]``, WP-80); ``None``
            (the default, unset) uses ``accept_gap_s`` on both sides, as
            the runner does (``microsim.runner._weave_lag_gap_s``).
        exit_accept_lag_gap_s: The same for a change to the right
            (``exit_accept_lag_gap_s``); ``None`` uses ``exit_accept_gap_s``.
    """

    accept_gap_s: float
    exit_accept_gap_s: float
    s0_m: float
    T_s: float
    a_max: float
    b: float
    v0_ms: float
    source: str = ""
    accept_lag_gap_s: float | None = None
    exit_accept_lag_gap_s: float | None = None

    @property
    def lag_gap_s(self) -> float:
        """The entering movement's follower-side time gap [s] (unset: ``accept_gap_s``)."""
        return self.accept_gap_s if self.accept_lag_gap_s is None else self.accept_lag_gap_s

    @property
    def exit_lag_gap_s(self) -> float:
        """The exiting movement's follower-side time gap [s] (unset: ``exit_accept_gap_s``)."""
        return (
            self.exit_accept_gap_s
            if self.exit_accept_lag_gap_s is None
            else self.exit_accept_lag_gap_s
        )

    @classmethod
    def from_population(
        cls,
        mean: Mapping[str, float],
        *,
        v0_cap_ms: float | None = None,
        weave_params: Mapping[str, float] | None = None,
        source: str = "",
    ) -> AcceptanceParams:
        """From an IDM population's ``mean`` (keys ``v0, T, a_max, b, s0``).

        Args:
            mean: Population means (``IDMCalibration.mean``).
            v0_cap_ms: Lane speed limit the follower's desired speed is capped
                at (the runner's ``min(maxSpeed, lane limit)``); None = no cap.
            weave_params: Overrides of ``WEAVE_DEFAULTS`` (the time gaps: the
                two leader-side keys and, WP-80, the two follower-side ones).
            source: Provenance string.
        """
        prm: dict[str, float | None] = {**WEAVE_DEFAULTS, **dict(weave_params or {})}
        v0 = float(mean["v0"])
        if v0_cap_ms is not None:
            v0 = min(v0, float(v0_cap_ms))
        lead_in, lead_out = prm["accept_gap_s"], prm["exit_accept_gap_s"]
        if lead_in is None or lead_out is None:
            raise ValueError("accept_gap_s and exit_accept_gap_s must be set")
        lag_in = prm.get("accept_lag_gap_s")
        lag_out = prm.get("exit_accept_lag_gap_s")
        return cls(
            accept_gap_s=float(lead_in),
            exit_accept_gap_s=float(lead_out),
            s0_m=float(mean["s0"]),
            T_s=float(mean["T"]),
            a_max=float(mean["a_max"]),
            b=float(mean["b"]),
            v0_ms=v0,
            source=source,
            accept_lag_gap_s=None if lag_in is None else float(lag_in),
            exit_accept_lag_gap_s=None if lag_out is None else float(lag_out),
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON form; the follower-side keys (WP-80) only when set, so an
        artifact written without them reads as before."""
        out: dict[str, Any] = {
            "accept_gap_s": self.accept_gap_s,
            "exit_accept_gap_s": self.exit_accept_gap_s,
            "s0_m": self.s0_m,
            "T_s": self.T_s,
            "a_max": self.a_max,
            "b": self.b,
            "v0_ms": self.v0_ms,
            "source": self.source,
        }
        if self.accept_lag_gap_s is not None:
            out["accept_lag_gap_s"] = self.accept_lag_gap_s
        if self.exit_accept_lag_gap_s is not None:
            out["exit_accept_lag_gap_s"] = self.exit_accept_lag_gap_s
        return out


@dataclass
class LaneChangeGaps:
    """Per-change records and the counts behind them.

    Attributes:
        records: One row per lane change (columns documented on
            :func:`lane_change_gaps`).
        counts: ``n_transitions_raw`` (lane transitions before the debounce),
            ``n_flicker_samples`` (samples the debounce reassigned),
            ``n_transitions_held`` (after it), ``n_nonadjacent`` (dropped
            multi-lane jumps), ``n_other_lanes`` (changes from or to a lane
            outside ``mainline_lanes ∪ aux_lanes``, dropped),
            ``n_outside_window`` and ``n_outside_zones_span`` (dropped),
            ``n_changes`` (recorded).
        dt_s: Sampling interval used [s].
        parameters: The detection parameters, for artifacts.
    """

    records: pd.DataFrame
    counts: dict[str, int]
    dt_s: float
    parameters: dict[str, Any] = field(default_factory=dict)


def _idm_accel_vec(
    v: NDArray[np.float64],
    v0: float,
    s: NDArray[np.float64],
    dv: NDArray[np.float64],
    T: float,
    a_max: float,
    b: float,
    s0: float,
) -> NDArray[np.float64]:
    """``microsim.runner._idm_accel`` vectorized (``s <= 0`` → −inf, ``s = inf`` → free term)."""
    free = 1.0 - (v / v0) ** 4 if v0 > 0.0 else np.zeros_like(v)
    s_star = s0 + np.maximum(0.0, v * T + v * dv / (2.0 * math.sqrt(a_max * b)))
    with np.errstate(divide="ignore", invalid="ignore"):
        acc = a_max * (free - (s_star / s) ** 2)
    acc = np.where(np.isinf(s) & (s > 0), a_max * free, acc)
    return np.where(s <= 0.0, -np.inf, acc)


def weave_acceptance(
    v: NDArray[np.float64],
    lead_gap_m: NDArray[np.float64],
    lead_v: NDArray[np.float64],
    lag_gap_m: NDArray[np.float64],
    lag_v: NDArray[np.float64],
    rightward: NDArray[np.bool_],
    params: AcceptanceParams,
) -> dict[str, NDArray[Any]]:
    """The weave's acceptance of each change, read off bumper-to-bumper gaps.

    ``microsim.runner._weave_change_ok`` restated (docs/WEAVE_MODEL_PLAN.md,
    speed-aware acceptance; the runner reads SUMO's gaps, which are these
    minus the changer's ``minGap`` on the leader side and minus the
    follower's on the follower side). With ``A`` the movement's time gap
    (``exit_accept_gap_s`` for a change to the right, ``accept_gap_s`` to the
    left) and ``A_F`` its follower-side one (WP-80: ``exit_accept_lag_gap_s``
    / ``accept_lag_gap_s``, ``A`` when unset), ``g_L = lead_gap − s0`` and
    ``g_F = lag_gap − s0``:

    * ``ok_lead_time``: ``g_L ≥ s0 + A · v``;
    * ``ok_lead_brake``: ``g_L ≥ s0 + (v − v_L)⁺² / (2b)`` (the changer's
      brake gap on its new leader);
    * ``ok_lag_time``: ``g_F ≥ s0 + A_F · v_F``;
    * ``ok_lag_absorb``: the follower's IDM acceleration towards the changer
      at the bumper gap is at least ``−b``;
    * ``ok_guard`` (``_weave_force_gap_ok`` with both brake gaps):
      ``g_L > s0 + A · (v − v_L)⁺``, ``g_L > s0 + (v − v_L)⁺²/(2b)``,
      ``g_F > s0 + A_F · (v_F − v)⁺`` and ``g_F > (v_F − v)⁺²/(2b)``.

    A side with no vehicle (a non-finite gap) passes. ``accepts`` is the
    conjunction. The ``need_*`` outputs are the bumper-to-bumper gaps each
    side needs (the non-strict bounds): ``need_lead_m = 2 s0 + max(A · v,
    (v − v_L)⁺²/(2b))`` and ``need_lag_m`` the largest of ``2 s0 + A_F · v_F``,
    the absorption gap ``s*_F / √(1 − (v_F/v0)⁴ + b/a_max)`` (inf when the
    radicand is not positive) and ``s0 + (v_F − v)⁺²/(2b)``; NaN where the
    side is empty.

    Args:
        v: Changer speeds [m/s].
        lead_gap_m: Bumper gaps to the new leader [m] (NaN or inf = none).
        lead_v: Its speeds [m/s].
        lag_gap_m: Bumper gaps to the new follower [m] (NaN or inf = none).
        lag_v: Its speeds [m/s].
        rightward: True where the change is to the right.
        params: :class:`AcceptanceParams`.

    Returns:
        Arrays keyed ``need_lead_m``, ``need_lag_m``, the
        :data:`ACCEPTANCE_TERMS` and ``accepts``.
    """
    p = params
    v = np.asarray(v, dtype=np.float64)
    has_lead = np.isfinite(lead_gap_m)
    has_lag = np.isfinite(lag_gap_m)
    right = np.asarray(rightward, dtype=bool)
    acc = np.where(right, p.exit_accept_gap_s, p.accept_gap_s)
    # the follower side (WP-80): the leader side's value when unset
    acc_f = np.where(right, p.exit_lag_gap_s, p.lag_gap_s)
    s0, b = p.s0_m, p.b
    g_l = np.where(has_lead, lead_gap_m - s0, np.inf)
    g_f = np.where(has_lag, lag_gap_m - s0, np.inf)
    v_l = np.where(has_lead, lead_v, 0.0)
    v_f = np.where(has_lag, lag_v, 0.0)
    close_l = np.where(has_lead, np.maximum(v - v_l, 0.0), 0.0)
    close_f = np.where(has_lag, np.maximum(v_f - v, 0.0), 0.0)
    brake_l = close_l**2 / (2.0 * b)
    brake_f = close_f**2 / (2.0 * b)

    ok_lead_time = g_l >= s0 + acc * v
    ok_lead_brake = g_l >= s0 + brake_l
    ok_lag_time = g_f >= s0 + acc_f * v_f
    a_f = _idm_accel_vec(v_f, p.v0_ms, g_f + s0, v_f - v, p.T_s, p.a_max, b, s0)
    ok_lag_absorb = ~has_lag | (a_f >= -b)
    ok_guard = (
        (g_l > s0 + acc * close_l)
        & (g_l > s0 + brake_l)
        & (g_f > s0 + acc_f * close_f)
        & (g_f > brake_f)
    )
    accepts = ok_lead_time & ok_lead_brake & ok_lag_time & ok_lag_absorb & ok_guard

    need_lead = np.where(has_lead, 2.0 * s0 + np.maximum(acc * v, brake_l), np.nan)
    s_star = s0 + np.maximum(0.0, v_f * p.T_s + v_f * (v_f - v) / (2.0 * math.sqrt(p.a_max * b)))
    rad = 1.0 - (v_f / p.v0_ms) ** 4 + b / p.a_max
    with np.errstate(divide="ignore", invalid="ignore"):
        absorb = np.where(rad > 0.0, s_star / np.sqrt(np.where(rad > 0.0, rad, 1.0)), np.inf)
    need_lag = np.where(
        has_lag, np.maximum(np.maximum(2.0 * s0 + acc_f * v_f, absorb), s0 + brake_f), np.nan
    )
    return {
        "need_lead_m": need_lead,
        "need_lag_m": need_lag,
        "ok_lead_time": ok_lead_time,
        "ok_lead_brake": ok_lead_brake,
        "ok_lag_time": ok_lag_time,
        "ok_lag_absorb": ok_lag_absorb,
        "ok_guard": ok_guard,
        "accepts": accepts,
    }


def classify_movement(
    from_lane: NDArray[np.int64],
    to_lane: NDArray[np.int64],
    zone_kind: NDArray[np.object_] | Sequence[str],
    *,
    mainline_lanes: Collection[int],
    aux_lanes: Collection[int],
) -> NDArray[np.object_]:
    """The movement a change serves, from its lanes and its zone.

    * ``entering``: auxiliary → mainline in a ``merge`` or ``weave`` zone;
    * ``exiting``: mainline → auxiliary in a ``diverge`` or ``weave`` zone;
    * ``through``: mainline → mainline (anywhere; a change between through
      lanes, whatever the driver's route — trajectories carry no route);
    * ``unknown``: everything else (an auxiliary → mainline change in a
      diverge zone, a mainline → auxiliary one in a merge zone, auxiliary →
      auxiliary, or an auxiliary lane outside every ramp zone).

    Args:
        from_lane: Band lane before the change.
        to_lane: Band lane after it.
        zone_kind: The zone kind of each change.
        mainline_lanes: Band ids of the through lanes.
        aux_lanes: Band ids of auxiliary (acceleration, deceleration,
            weaving) lanes.

    Returns:
        Object array of :data:`MOVEMENTS`.
    """
    main = np.fromiter(mainline_lanes, dtype=np.int64)
    aux = np.fromiter(aux_lanes, dtype=np.int64)
    kind = np.asarray(zone_kind, dtype=object)
    f_main, t_main = np.isin(from_lane, main), np.isin(to_lane, main)
    f_aux, t_aux = np.isin(from_lane, aux), np.isin(to_lane, aux)
    merge_like = (kind == "merge") | (kind == "weave")
    diverge_like = (kind == "diverge") | (kind == "weave")
    out = np.full(from_lane.shape, "unknown", dtype=object)
    out[f_main & t_main] = "through"
    out[f_aux & t_main & merge_like] = "entering"
    out[f_main & t_aux & diverge_like] = "exiting"
    return out


def _zone_of(x: NDArray[np.float64], zones: Sequence[Zone]) -> tuple[NDArray[Any], NDArray[Any]]:
    """Zone name and kind per position (the first zone containing it; else ``basic``)."""
    name = np.full(x.shape, "basic", dtype=object)
    kind = np.full(x.shape, "basic", dtype=object)
    assigned = np.zeros(x.shape, dtype=bool)
    for z in zones:
        inside = ~assigned & (x >= z.x_lo_m) & (x < z.x_hi_m)
        name[inside] = z.name
        kind[inside] = z.kind
        assigned |= inside
    return name, kind


def _runs(
    lane_held: NDArray[np.int64], contig: NDArray[np.bool_], dt_s: float
) -> tuple[NDArray[np.int64], NDArray[np.float64], NDArray[np.bool_], NDArray[np.bool_]]:
    """Runs of the debounced lane sequence.

    Returns:
        ``(run_id, duration_s, after_change, before_change)``: the run index
        per sample, and per run its duration ``n_samples × dt_s``, whether it
        began with a lane change (its first sample is contiguous with the
        vehicle's previous one) rather than at the start of a track, and
        whether it ended with a lane change rather than at a track's end.
    """
    n = lane_held.size
    breaks = np.ones(n, dtype=bool)
    breaks[1:] = ~contig | (lane_held[1:] != lane_held[:-1])
    run_id = (np.cumsum(breaks) - 1).astype(np.int64)
    starts = np.flatnonzero(breaks)
    ends = np.append(starts[1:], n) - 1
    lengths = np.bincount(run_id).astype(np.float64)
    after_change = np.zeros(starts.size, dtype=bool)
    after_change[starts > 0] = contig[starts[starts > 0] - 1]
    before_change = np.zeros(starts.size, dtype=bool)
    before_change[ends < n - 1] = contig[ends[ends < n - 1]]
    return run_id, lengths * dt_s, after_change, before_change


def lane_change_gaps(
    df: pd.DataFrame,
    zones: Sequence[Zone],
    *,
    mainline_lanes: Collection[int],
    aux_lanes: Collection[int] = (),
    dt_s: float | None = None,
    max_gap_s: float | None = None,
    min_dwell_s: float = DEFAULT_MIN_DWELL_S,
    window_s: tuple[float, float] | None = None,
    x_range_m: tuple[float, float] | None = None,
    max_range_m: float = DEFAULT_MAX_RANGE_M,
    min_gap_m: float = DEFAULT_MIN_GAP_M,
    default_length_m: float | None = None,
    acceptance: AcceptanceParams | None = None,
    groups: Mapping[str, str] | None = None,
) -> LaneChangeGaps:
    """Every lane change of a trajectory frame with the gaps it was made into.

    Args:
        df: Frame with ``t, veh_id, x, lane, v`` and optionally ``length``
            (module docstring: front-bumper ``x``, band lanes, shared grid).
        zones: Non-overlapping :class:`Zone` s; a change outside all of them
            is in zone ``basic``.
        mainline_lanes: Band ids of the through lanes.
        aux_lanes: Band ids of the auxiliary lanes.
        dt_s: Sampling interval [s]; inferred when None.
        max_gap_s: Largest step between one vehicle's samples that still
            counts as contiguous; ``2.5 × dt_s`` when None (one missing
            sample bridged).
        min_dwell_s: Flicker guard and confirmation dwell [s].
        window_s: Half-open window on the change time; None = all. Every row
            takes part in the debounce and the neighbour search, so a frame
            padded by a few seconds yields exactly the whole record's changes.
        x_range_m: Half-open span the change position must lie in; None = all.
        max_range_m: Neighbour search range [m].
        min_gap_m: A neighbour gap below this marks the change ``suspect``.
        default_length_m: Vehicle length [m] when ``df`` has no ``length``
            column (required then).
        acceptance: When given, the model's acceptance is evaluated on every
            change (:func:`weave_acceptance`) and its columns are added.
        groups: Optional ``veh_id → label`` mapping (e.g. a simulated
            vehicle's route class) added as column ``group``.

    Returns:
        :class:`LaneChangeGaps`. ``records`` columns: ``t, veh_id, x, zone,
        zone_kind, from_lane, to_lane, direction ("left"|"right"), movement,
        v, length, dwell_before_s, dwell_after_s`` (durations of the held
        runs either side of the change; a run ends at a further change or
        at the track's end), ``track_start_before`` / ``track_end_after``
        (that run began at the start of the vehicle's track / ended at its
        end rather than at another change), ``confirmed`` (False when a run
        either side is shorter than ``min_dwell_s`` AND bounded by the track's
        start or end — a change at a fragment's first or last samples, the
        tracker's lateral drift as a track starts or dies), ``lead_id,
        lead_gap_m, lead_v, lead_closing_ms, lead_time_gap_s, lag_id,
        lag_gap_m, lag_v, lag_closing_ms, lag_time_gap_s, suspect``
        (neighbour columns NaN / None when there is none within range), plus
        ``need_lead_m, need_lag_m``, the :data:`ACCEPTANCE_TERMS` and
        ``model_accepts`` with ``acceptance``, and ``group`` with ``groups``.

    Raises:
        ValueError: On missing columns, overlapping zones, a missing length
            or an uninferrable sampling interval.
    """
    missing = {"t", "veh_id", "x", "lane", "v"} - set(df.columns)
    if missing:
        raise ValueError(f"df is missing columns: {sorted(missing)}")
    has_length = "length" in df.columns
    if not has_length and default_length_m is None:
        raise ValueError("df has no 'length' column: give default_length_m")
    ordered = sorted(zones, key=lambda z: z.x_lo_m)
    for a, b in pairwise(ordered):
        if b.x_lo_m < a.x_hi_m:
            raise ValueError(f"zones {a.name!r} and {b.name!r} overlap")
    main_set = {int(v) for v in mainline_lanes}
    aux_set = {int(v) for v in aux_lanes}
    if not main_set:
        raise ValueError("mainline_lanes must not be empty")
    if main_set & aux_set:
        raise ValueError("a lane cannot be both mainline and auxiliary")
    considered = np.fromiter(sorted(main_set | aux_set), dtype=np.int64)

    codes, labels = pd.factorize(df["veh_id"], sort=False)
    codes = np.asarray(codes, dtype=np.int64)
    t_all = df["t"].to_numpy(dtype=np.float64)
    order = np.lexsort((t_all, codes))
    veh = codes[order]
    t = t_all[order]
    x = df["x"].to_numpy(dtype=np.float64)[order]
    v = df["v"].to_numpy(dtype=np.float64)[order]
    lane = df["lane"].to_numpy(dtype=np.int64)[order]
    if has_length:
        length = df["length"].to_numpy(dtype=np.float64)[order]
    else:
        length = np.full(t.size, float(default_length_m or 0.0))

    params: dict[str, Any] = {
        "min_dwell_s": min_dwell_s,
        "max_range_m": max_range_m,
        "min_gap_m": min_gap_m,
        "mainline_lanes": sorted(main_set),
        "aux_lanes": sorted(aux_set),
        "window_s": list(window_s) if window_s is not None else None,
        "x_range_m": list(x_range_m) if x_range_m is not None else None,
        "default_length_m": default_length_m,
    }
    counts = {
        "n_rows": int(t.size),
        "n_transitions_raw": 0,
        "n_flicker_samples": 0,
        "n_transitions_held": 0,
        "n_nonadjacent": 0,
        "n_other_lanes": 0,
        "n_outside_window": 0,
        "n_outside_zones_span": 0,
        "n_changes": 0,
    }
    if t.size < 2:
        if dt_s is None:
            raise ValueError("cannot infer the sampling interval from fewer than two rows")
        return LaneChangeGaps(_empty_records(acceptance, groups), counts, dt_s, params)
    if dt_s is None:
        dt_s = infer_dt(t, veh)
    if max_gap_s is None:
        max_gap_s = DEFAULT_MAX_GAP_FACTOR * dt_s
    params.update({"dt_s": dt_s, "max_gap_s": max_gap_s})

    lane_held, contig = held_lanes(
        t, veh, lane, dt_s=dt_s, max_gap_s=max_gap_s, min_dwell_s=min_dwell_s
    )
    counts["n_transitions_raw"] = int(np.sum(contig & (lane[1:] != lane[:-1])))
    counts["n_flicker_samples"] = int(np.sum(lane_held != lane))
    run_id, run_dur, run_after_change, run_before_change = _runs(lane_held, contig, dt_s)

    j0 = np.flatnonzero(contig & (lane_held[1:] != lane_held[:-1]))
    j1 = j0 + 1
    counts["n_transitions_held"] = int(j0.size)
    from_l, to_l = lane_held[j0], lane_held[j1]
    adjacent = np.abs(to_l - from_l) == 1
    counts["n_nonadjacent"] = int(np.sum(~adjacent))
    in_set = np.isin(from_l, considered) & np.isin(to_l, considered)
    counts["n_other_lanes"] = int(np.sum(adjacent & ~in_set))
    keep = adjacent & in_set
    if window_s is not None:
        in_win = (t[j1] >= window_s[0]) & (t[j1] < window_s[1])
        counts["n_outside_window"] = int(np.sum(keep & ~in_win))
        keep &= in_win
    if x_range_m is not None:
        in_span = (x[j1] >= x_range_m[0]) & (x[j1] < x_range_m[1])
        counts["n_outside_zones_span"] = int(np.sum(keep & ~in_span))
        keep &= in_span
    j0, j1 = j0[keep], j1[keep]
    counts["n_changes"] = int(j1.size)
    if j1.size == 0:
        return LaneChangeGaps(_empty_records(acceptance, groups), counts, dt_s, params)

    # --- neighbour search on a (time slot, held lane, x) ordering ----------
    t_idx = np.rint(t / dt_s).astype(np.int64)
    lane_lo = int(lane_held.min())
    n_lane = int(lane_held.max()) - lane_lo + 1
    key = t_idx * n_lane + (lane_held - lane_lo)
    x_lo = float(x.min())
    scale = float(x.max()) - x_lo + 1.0
    z = key.astype(np.float64) * scale + (x - x_lo)
    by = np.lexsort((x, key))
    key_s, z_s = key[by], z[by]
    kq = t_idx[j1] * n_lane + (to_l[keep] - lane_lo)
    zq = kq.astype(np.float64) * scale + (x[j1] - x_lo)
    blo = np.searchsorted(key_s, kq, side="left")
    bhi = np.searchsorted(key_s, kq, side="right")
    pos_r = np.searchsorted(z_s, zq, side="right")  # first vehicle with front strictly ahead
    pos_l = np.searchsorted(z_s, zq, side="left")  # the changer (or a tie at its front)
    lead_ok = pos_r < bhi
    lag_ok = pos_l - 1 >= blo
    lead_row = np.where(lead_ok, by[np.minimum(pos_r, by.size - 1)], -1)
    lag_row = np.where(lag_ok, by[np.maximum(pos_l - 1, 0)], -1)

    xc, lc, vc = x[j1], length[j1], v[j1]
    lead_gap = np.where(lead_ok, x[lead_row] - length[lead_row] - xc, np.nan)
    lag_gap = np.where(lag_ok, (xc - lc) - x[lag_row], np.nan)
    lead_ok &= lead_gap <= max_range_m
    lag_ok &= lag_gap <= max_range_m
    lead_gap = np.where(lead_ok, lead_gap, np.nan)
    lag_gap = np.where(lag_ok, lag_gap, np.nan)
    lead_v = np.where(lead_ok, v[lead_row], np.nan)
    lag_v = np.where(lag_ok, v[lag_row], np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        lead_tg = np.where(lead_ok & (vc >= MIN_SPEED_FOR_TIME_GAP_MS), lead_gap / vc, np.nan)
        lag_tg = np.where(lag_ok & (lag_v >= MIN_SPEED_FOR_TIME_GAP_MS), lag_gap / lag_v, np.nan)
    suspect = (lead_ok & (lead_gap < min_gap_m)) | (lag_ok & (lag_gap < min_gap_m))

    zone_name, zone_kind = _zone_of(xc, zones)
    from_c, to_c = lane_held[j0], lane_held[j1]
    dwell_before = run_dur[run_id[j0]]
    dwell_after = run_dur[run_id[j1]]
    track_start_before = ~run_after_change[run_id[j0]]
    track_end_after = ~run_before_change[run_id[j1]]
    guard = min_dwell_s - 1e-9
    confirmed = ~(
        (track_start_before & (dwell_before < guard)) | (track_end_after & (dwell_after < guard))
    )
    label_arr = np.asarray(labels, dtype=object)
    rec: dict[str, Any] = {
        "t": t[j1],
        "veh_id": label_arr[veh[j1]],
        "x": xc,
        "zone": zone_name,
        "zone_kind": zone_kind,
        "from_lane": from_c,
        "to_lane": to_c,
        "direction": np.where(to_c > from_c, "right", "left").astype(object),
        "movement": classify_movement(
            from_c, to_c, zone_kind, mainline_lanes=main_set, aux_lanes=aux_set
        ),
        "v": vc,
        "length": lc,
        "dwell_before_s": dwell_before,
        "dwell_after_s": dwell_after,
        "track_start_before": track_start_before,
        "track_end_after": track_end_after,
        "confirmed": confirmed,
        "lead_id": _ids_or_none(label_arr, veh, lead_row, lead_ok),
        "lead_gap_m": lead_gap,
        "lead_v": lead_v,
        "lead_closing_ms": vc - lead_v,
        "lead_time_gap_s": lead_tg,
        "lag_id": _ids_or_none(label_arr, veh, lag_row, lag_ok),
        "lag_gap_m": lag_gap,
        "lag_v": lag_v,
        "lag_closing_ms": lag_v - vc,
        "lag_time_gap_s": lag_tg,
        "suspect": suspect,
    }
    if acceptance is not None:
        acc = weave_acceptance(vc, lead_gap, lead_v, lag_gap, lag_v, to_c > from_c, acceptance)
        rec["need_lead_m"] = acc["need_lead_m"]
        rec["need_lag_m"] = acc["need_lag_m"]
        for term in ACCEPTANCE_TERMS:
            rec[term] = acc[term]
        rec["model_accepts"] = acc["accepts"]
    if groups is not None:
        rec["group"] = np.array(
            [groups.get(str(i), "unknown") for i in rec["veh_id"]], dtype=object
        )
    records = pd.DataFrame(rec).sort_values(["t", "x"], kind="stable").reset_index(drop=True)
    return LaneChangeGaps(records, counts, dt_s, params)


def _ids_or_none(
    labels: NDArray[np.object_],
    veh: NDArray[np.int64],
    rows: NDArray[np.int64],
    ok: NDArray[np.bool_],
) -> NDArray[np.object_]:
    """Vehicle ids of the given rows where ``ok``, None elsewhere."""
    out = np.full(rows.shape, None, dtype=object)
    out[ok] = labels[veh[rows[ok]]]
    return out


def _empty_records(
    acceptance: AcceptanceParams | None, groups: Mapping[str, str] | None
) -> pd.DataFrame:
    """A records frame with the full column set and no rows."""
    cols = [
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
        "length",
        "dwell_before_s",
        "dwell_after_s",
        "track_start_before",
        "track_end_after",
        "confirmed",
        "lead_id",
        "lead_gap_m",
        "lead_v",
        "lead_closing_ms",
        "lead_time_gap_s",
        "lag_id",
        "lag_gap_m",
        "lag_v",
        "lag_closing_ms",
        "lag_time_gap_s",
        "suspect",
    ]
    if acceptance is not None:
        cols += ["need_lead_m", "need_lag_m", *ACCEPTANCE_TERMS, "model_accepts"]
    if groups is not None:
        cols.append("group")
    return pd.DataFrame({c: pd.Series(dtype=object) for c in cols})


def sim_band_lanes(
    df: pd.DataFrame,
    edge_offsets_m: Sequence[float],
    edge_lanes: Sequence[int],
) -> pd.DataFrame:
    """A simulated trajectory frame with ``lane`` mapped to the band convention.

    ``microsim`` records SUMO's per-edge lane index (0 = rightmost), whose
    value for one physical lane jumps where an edge gains or loses a lane on
    the right; :func:`calibration.lanechange.band_lane_from_sim` renumbers it
    from the left (``band = n_lanes(edge) − index``), so the through lanes
    keep their numbers across edges and an added right-hand lane (an
    auxiliary lane) is ``n_through + 1``.

    Args:
        df: ``trajectories.parquet`` rows (``t, veh_id, x, lane, v``).
        edge_offsets_m: Start of each corridor edge on the ``x`` axis [m].
        edge_lanes: Lane count of each edge.

    Returns:
        A copy with ``lane`` replaced.
    """
    out = df.copy()
    out["lane"] = band_lane_from_sim(
        out["lane"].to_numpy(dtype=np.int64),
        out["x"].to_numpy(dtype=np.float64),
        edge_offsets_m,
        edge_lanes,
    )
    return out


def _quantiles(values: NDArray[np.float64]) -> dict[str, float | None]:
    """The :data:`QUANTILES` of the finite values (None when there are none)."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {f"p{round(q * 100):02d}": None for q in QUANTILES}
    qs = np.quantile(finite, QUANTILES)
    return {
        f"p{round(q * 100):02d}": round(float(v), 3) for q, v in zip(QUANTILES, qs, strict=True)
    }


def _group_summary(sub: pd.DataFrame) -> dict[str, Any]:
    """Distributions and acceptance shares of one group of records."""
    n = len(sub)
    out: dict[str, Any] = {"n": n}
    if n == 0:
        return out
    out["v_ms"] = _quantiles(sub["v"].to_numpy(dtype=np.float64))
    for side in ("lead", "lag"):
        gap = sub[f"{side}_gap_m"].to_numpy(dtype=np.float64)
        out[f"share_no_{side}"] = round(float(np.mean(~np.isfinite(gap))), 4)
        out[f"{side}_gap_m"] = _quantiles(gap)
        out[f"{side}_time_gap_s"] = _quantiles(sub[f"{side}_time_gap_s"].to_numpy(dtype=np.float64))
        out[f"{side}_closing_ms"] = _quantiles(sub[f"{side}_closing_ms"].to_numpy(dtype=np.float64))
    if "model_accepts" in sub.columns:
        accepts = sub["model_accepts"].to_numpy(dtype=bool)
        model: dict[str, Any] = {
            "refused_share": round(float(np.mean(~accepts)), 4),
            "refused_by": {
                term.removeprefix("ok_"): round(float(np.mean(~sub[term].to_numpy(dtype=bool))), 4)
                for term in ACCEPTANCE_TERMS
            },
        }
        for side in ("lead", "lag"):
            gap = sub[f"{side}_gap_m"].to_numpy(dtype=np.float64)
            need = sub[f"need_{side}_m"].to_numpy(dtype=np.float64)
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = gap / need
            model[f"{side}_gap_over_need"] = _quantiles(ratio)
        out["model"] = model
    return out


def summarize_gaps(
    records: pd.DataFrame,
    *,
    by: Sequence[str] = ("zone_kind", "movement"),
    speed_classes: Sequence[tuple[str, float, float]] = SPEED_CLASSES_MS,
    include_unconfirmed: bool = False,
    include_suspect: bool = False,
) -> list[dict[str, Any]]:
    """Gap distributions and the model's refusal shares per group and speed class.

    Rows are per combination of the ``by`` columns present in ``records``,
    each with a ``speed_class`` of ``"all"`` and one per ``speed_classes``
    entry (changer speed, half-open bounds). Unconfirmed and suspect changes
    are left out unless asked for. Each row carries ``n``, the changer speed
    quantiles, per side (``lead`` / ``lag``) the share with no vehicle within
    range and the quantiles of the gap [m], the time gap [s] and the closing
    speed [m/s] over the changes that have one, and — when the records carry
    the acceptance — ``model.refused_share`` (changes the model's acceptance
    refuses), ``model.refused_by.<term>`` (changes each term refuses; terms
    overlap) and the quantiles of gap / need per side.

    Args:
        records: :attr:`LaneChangeGaps.records`.
        by: Grouping columns.
        speed_classes: ``(label, lo, hi)`` changer-speed classes [m/s].
        include_unconfirmed: Keep changes with ``confirmed == False``.
        include_suspect: Keep changes with ``suspect == True``.

    Returns:
        One dict per group and speed class, groups in sorted order.
    """
    sel = records
    if not include_unconfirmed and "confirmed" in sel.columns and len(sel):
        sel = sel[sel["confirmed"].astype(bool)]
    if not include_suspect and "suspect" in sel.columns and len(sel):
        sel = sel[~sel["suspect"].astype(bool)]
    rows: list[dict[str, Any]] = []
    if len(sel) == 0:
        return rows
    keys = list(by)
    for group_key, sub in sel.groupby(keys, sort=True):
        key_t = group_key if isinstance(group_key, tuple) else (group_key,)
        head = {k: str(val) for k, val in zip(keys, key_t, strict=True)}
        rows.append({**head, "speed_class": "all", **_group_summary(sub)})
        vv = sub["v"].to_numpy(dtype=np.float64)
        for label, lo, hi in speed_classes:
            rows.append(
                {**head, "speed_class": label, **_group_summary(sub[(vv >= lo) & (vv < hi)])}
            )
    return rows


def sample_records(records: pd.DataFrame, n: int, *, seed: int) -> list[dict[str, Any]]:
    """A seeded random sample of records, JSON-ready (NaN → None, floats rounded).

    Args:
        records: :attr:`LaneChangeGaps.records`.
        n: Rows to draw (all when fewer exist).
        seed: RNG seed (``flowstate_core.rng.make_rng``).

    Returns:
        Rows as dicts in time order.
    """
    from flowstate_core.rng import make_rng

    if len(records) == 0:
        return []
    k = min(n, len(records))
    idx = np.sort(make_rng(seed).choice(len(records), size=k, replace=False))
    out: list[dict[str, Any]] = []
    for row in records.iloc[idx].to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for col, val in row.items():
            if isinstance(val, float | np.floating):
                clean[str(col)] = None if not math.isfinite(float(val)) else round(float(val), 3)
            elif isinstance(val, np.bool_ | bool):
                clean[str(col)] = bool(val)
            elif isinstance(val, np.integer):
                clean[str(col)] = int(val)
            else:
                clean[str(col)] = val
        out.append(clean)
    return out


# --- WP-78: the gaps a changer passed up before it changed --------------------

DEFAULT_LOOKBACK_S: Final[float] = 10.0
"""How far before a change its target-lane gaps are sampled [s] (WP-78)."""

DEFAULT_SAMPLE_EVERY_S: Final[float] = 1.0
"""Sampling interval of the lookback [s]: a whole multiple of both the I-24
MOTION table's 0.2 s and a microsim run's 0.5 s (``SimSpec.output_hz`` 2), so
the observed and the simulated sequences are read at one rate."""

DEFAULT_SAME_VEHICLE_TOL_M: Final[float] = 2.0
"""Neighbours in one role (lead or lag) at two consecutive lookback samples
are one vehicle when their ids match or when the earlier one's front, carried
forward at the mean of its two speeds, lands within this distance [m] of the
later one's: a fragment switch of one tracked vehicle (I-24 MOTION documents
are fragments, median 9.9 s) is not a new gap, while a different vehicle in
the same role is at least one vehicle length plus its gap away."""

GAP_STATUSES: Final[tuple[str, ...]] = ("accepted", "rejected", "empty")
"""Lookback sample statuses (:func:`gap_sequences`)."""


@dataclass
class GapSequences:
    """Target-lane gaps sampled before each change (WP-78).

    Attributes:
        samples: One row per (change, sample instant); columns documented on
            :func:`gap_sequences`.
        counts: ``n_changes`` (asked for), ``n_unmatched`` (not found in the
            frame; 0 when ``records`` came from the same frame), ``n_samples``,
            ``n_accepted_samples``, ``n_rejected_samples``,
            ``n_empty_samples``, ``n_suspect_samples``,
            ``n_changes_with_rejected``, ``n_rejected_gaps`` (distinct).
        parameters: The sampling parameters, for artifacts.
    """

    samples: pd.DataFrame
    counts: dict[str, int]
    parameters: dict[str, Any] = field(default_factory=dict)


def _same_vehicle(
    code_a: NDArray[np.int64],
    x_a: NDArray[np.float64],
    v_a: NDArray[np.float64],
    code_b: NDArray[np.int64],
    x_b: NDArray[np.float64],
    v_b: NDArray[np.float64],
    dt_ab: NDArray[np.float64],
    tol_m: float,
) -> NDArray[np.bool_]:
    """Whether role neighbour ``a`` (earlier) and ``b`` (``dt_ab`` later) are one vehicle.

    Codes are ``-1`` for "none within range"; two empty roles are the same
    (an open side stays open), one empty and one filled are not.
    """
    both_none = (code_a < 0) & (code_b < 0)
    both = (code_a >= 0) & (code_b >= 0)
    with np.errstate(invalid="ignore"):
        carried = x_a + 0.5 * (v_a + v_b) * dt_ab
        close = np.abs(carried - x_b) <= tol_m
    return np.asarray(both_none | (both & ((code_a == code_b) | close)), dtype=bool)


def gap_sequences(
    df: pd.DataFrame,
    records: pd.DataFrame,
    *,
    changes: NDArray[np.bool_] | Sequence[bool] | None = None,
    zones: Sequence[Zone] | None = None,
    dt_s: float | None = None,
    max_gap_s: float | None = None,
    min_dwell_s: float = DEFAULT_MIN_DWELL_S,
    lookback_s: float = DEFAULT_LOOKBACK_S,
    sample_every_s: float = DEFAULT_SAMPLE_EVERY_S,
    max_range_m: float = DEFAULT_MAX_RANGE_M,
    min_gap_m: float = DEFAULT_MIN_GAP_M,
    default_length_m: float | None = None,
    same_vehicle_tol_m: float = DEFAULT_SAME_VEHICLE_TOL_M,
) -> GapSequences:
    """The target-lane gaps each change's driver had before it changed, accepted and rejected.

    WP-78 (docs/WEAVE_MODEL_PLAN.md, dated section). :func:`lane_change_gaps`
    records the gap a driver took; a critical-gap estimator also needs the
    gaps the driver let go by (Troutbeck 1992; Brilon, Koenig & Troutbeck
    1999, Transp. Res. A 33:161–186: each driver's accepted gap and largest
    rejected gap). This function reads, for each change of ``records`` (which
    must come from :func:`lane_change_gaps` on the same frame with the same
    ``dt_s``, ``max_gap_s`` and ``min_dwell_s``), the target lane's lead and
    lag at the change moment and every ``sample_every_s`` before it, back
    ``lookback_s`` at most, while the driver was still in its origin lane.

    **Samples.** Instant ``k = 0`` is the change moment (the record's own
    time stamp; its neighbours are the record's). Instant ``k = −m`` is
    ``m · sample_every_s`` earlier. The lookback ends at the first earlier
    instant at which the vehicle has no sample in the debounced run of its
    origin lane that ends at the change — the start of that run (an earlier
    change or the vehicle's arrival in the lane), the start of its track, or
    a missing sample slot — and, with ``zones`` given, at the first earlier
    instant at which the changer's front is outside the zone the change was
    made in (an auxiliary lane exists only inside its zone: upstream of it
    there is no gap beside the driver to accept or reject, and a vehicle in
    the same band farther on is not beside it). At every instant the neighbours are found exactly
    as :func:`lane_change_gaps` finds them — in the target lane (by debounced
    lane), the lead the nearest vehicle whose front is strictly ahead of the
    changer's front, the lag the nearest at or behind it, within
    ``max_range_m`` — and the gaps, time gaps and closing speeds are defined
    identically (bumper to bumper; time gaps over the rear vehicle's speed).

    **Gaps, accepted and rejected.** A *gap* is one lag–lead pair of
    consecutive target-lane vehicles beside the changer. Going back from the
    change, a new gap begins whenever the lead or the lag is a different
    vehicle from the one at the next-later instant — a vehicle crossed the
    changer's position (the changer moved past it or it moved past the
    changer), or cut into or out of the target lane. A neighbour is the same
    vehicle when its id matches or its position is continuous within
    ``same_vehicle_tol_m`` (:data:`DEFAULT_SAME_VEHICLE_TOL_M`: a tracker's
    fragment switch is not a new gap). Identity reads the neighbours without
    the range cut, so a neighbour drifting across ``max_range_m`` does not
    start a new gap. ``gap_index`` numbers the gaps from the change back (0 =
    the gap the driver entered).

    * ``accepted``: the change moment, and every earlier instant of the gap
      the driver entered (``gap_index`` 0, or the same lead and lag ids — the
      gap seen earlier, before the driver's relative position moved and came
      back): the driver was beside it and had not yet taken it, which is not
      a rejection.
    * ``rejected``: every instant of every other gap with a vehicle on at
      least one side — a distinct lag–lead pair that went by while the
      driver stayed in its lane. This is the rejected gap of gap-acceptance
      theory transposed to a lane change: at a minor-road stop line, a
      major-stream gap the waiting driver lets pass (Brilon et al. 1999;
      Tian et al. 1999, Transp. Res. A 33:187–197, define the gap events);
      at a freeway merge, "the net distances between two vehicles on the
      shoulder lane which are passed by vehicles driving on the acceleration
      lane, which merge further downstream and thus reject these offered
      gaps" (Marczak, Daamen & Buisson 2013, Transp. Res. C 36:530–546, §6).
    * ``empty``: no vehicle on either side within ``max_range_m`` — an empty
      target lane the driver did not move into, which says the driver was not
      yet trying to change, not that a gap was too small; never a rejection.

    ``suspect`` marks an instant with a neighbour gap below ``min_gap_m`` (a
    duplicate fragment of the changer, most often); estimators leave suspect
    instants out.

    **Coverage.** On I-24 MOTION (about half the peak vehicle-time tracked)
    an untracked vehicle inside a gap makes the observed gap larger than the
    true one, and an untracked vehicle between two observed neighbours merges
    two true gaps into one observed gap: the accepted space gap (and its lead
    time gap, over the changer's own speed) is the true one or larger, a
    rejected gap may be larger (an untracked vehicle inside it) or lost
    (merged into the accepted gap). A lag time gap is over the recorded
    follower's speed, which can be a different vehicle's, so it is not
    bounded. ``calibration.critical_gap`` states how that biases the estimate.

    Args:
        df: The frame :func:`lane_change_gaps` read (``t, veh_id, x, lane, v``,
            optional ``length``).
        records: :attr:`LaneChangeGaps.records` of that frame.
        changes: Boolean mask over ``records`` rows to sample (all when None).
        zones: The zones :func:`lane_change_gaps` was given; when set, the
            lookback stays inside the change's own zone (a change in
            ``basic`` is not bounded).
        dt_s: Sampling interval of ``df`` [s]; inferred when None.
        max_gap_s: Contiguity bound [s]; ``2.5 × dt_s`` when None.
        min_dwell_s: The debounce of :func:`lane_change_gaps` [s].
        lookback_s: How far back to sample [s].
        sample_every_s: Interval between samples [s]; a whole multiple of
            ``dt_s``.
        max_range_m: Neighbour search range [m].
        min_gap_m: A neighbour gap below this marks the instant suspect.
        default_length_m: Vehicle length [m] when ``df`` has no ``length``.
        same_vehicle_tol_m: Position-continuity tolerance [m].

    Returns:
        :class:`GapSequences`. ``samples`` columns: ``change`` (row of
        ``records``), ``veh_id``, ``t_change``, ``k`` (0 at the change,
        negative before it), ``t``, ``dt_before_s`` (``t_change − t``), ``x``,
        ``v``, ``lane`` (the origin lane before the change, the target lane
        at ``k = 0``), ``target_lane``, ``lead_id, lead_gap_m, lead_v,
        lead_closing_ms, lead_time_gap_s, lag_id, lag_gap_m, lag_v,
        lag_closing_ms, lag_time_gap_s`` (as in :func:`lane_change_gaps`),
        ``suspect``, ``gap_index``, ``status`` (:data:`GAP_STATUSES`); sorted
        by ``change`` then ``k``.

    Raises:
        ValueError: On missing columns, a missing length, a ``sample_every_s``
            that is not a whole multiple of ``dt_s``, or a negative lookback.
    """
    missing = {"t", "veh_id", "x", "lane", "v"} - set(df.columns)
    if missing:
        raise ValueError(f"df is missing columns: {sorted(missing)}")
    rmissing = {"t", "veh_id", "from_lane", "to_lane"} - set(records.columns)
    if rmissing:
        raise ValueError(f"records is missing columns: {sorted(rmissing)}")
    has_length = "length" in df.columns
    if not has_length and default_length_m is None:
        raise ValueError("df has no 'length' column: give default_length_m")
    if lookback_s < 0.0:
        raise ValueError("lookback_s must be >= 0")

    sel = (
        np.ones(len(records), dtype=bool)
        if changes is None
        else np.asarray(changes, dtype=bool).copy()
    )
    if sel.shape != (len(records),):
        raise ValueError("changes must be a mask over the records rows")
    change_rows = np.flatnonzero(sel).astype(np.int64)
    params: dict[str, Any] = {
        "lookback_s": lookback_s,
        "sample_every_s": sample_every_s,
        "min_dwell_s": min_dwell_s,
        "max_range_m": max_range_m,
        "min_gap_m": min_gap_m,
        "same_vehicle_tol_m": same_vehicle_tol_m,
        "default_length_m": default_length_m,
        "bounded_by_zone": zones is not None,
    }
    counts = {
        "n_changes": int(change_rows.size),
        "n_unmatched": 0,
        "n_samples": 0,
        "n_accepted_samples": 0,
        "n_rejected_samples": 0,
        "n_empty_samples": 0,
        "n_suspect_samples": 0,
        "n_changes_with_rejected": 0,
        "n_rejected_gaps": 0,
    }
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
    if max_gap_s is None:
        max_gap_s = DEFAULT_MAX_GAP_FACTOR * dt_s
    step = round(sample_every_s / dt_s)
    if step < 1 or abs(step * dt_s - sample_every_s) > 1e-6 * max(1.0, sample_every_s):
        raise ValueError(
            f"sample_every_s ({sample_every_s}) must be a whole multiple of dt_s ({dt_s})"
        )
    n_back = math.floor(lookback_s / sample_every_s + 1e-9)
    params.update({"dt_s": dt_s, "max_gap_s": max_gap_s, "n_back": n_back})
    if change_rows.size == 0 or t.size == 0:
        return GapSequences(_empty_samples(), counts, params)

    x = df["x"].to_numpy(dtype=np.float64)[order]
    v = df["v"].to_numpy(dtype=np.float64)[order]
    lane = df["lane"].to_numpy(dtype=np.int64)[order]
    if has_length:
        length = df["length"].to_numpy(dtype=np.float64)[order]
    else:
        length = np.full(t.size, float(default_length_m or 0.0))
    lane_held, contig = held_lanes(
        t, veh, lane, dt_s=dt_s, max_gap_s=max_gap_s, min_dwell_s=min_dwell_s
    )
    run_id, _, _, _ = _runs(lane_held, contig, dt_s)
    first_of_run = np.ones(t.size, dtype=bool)
    first_of_run[1:] = run_id[1:] != run_id[:-1]
    run_start = np.flatnonzero(first_of_run)[run_id]

    # --- locate each change: (vehicle, time slot) → row in the (veh, t) order
    t_idx = np.rint(t / dt_s).astype(np.int64)
    t_lo_idx = int(t_idx.min())
    span_idx = int(t_idx.max()) - t_lo_idx + 1 + (n_back + 1) * step
    vt_key = veh * span_idx + (t_idx - t_lo_idx)
    rec = records.iloc[change_rows]
    rec_code = pd.Index(labels).get_indexer(pd.Index(rec["veh_id"]))
    rec_tidx = np.rint(rec["t"].to_numpy(dtype=np.float64) / dt_s).astype(np.int64)
    from_c = rec["from_lane"].to_numpy(dtype=np.int64)
    to_c = rec["to_lane"].to_numpy(dtype=np.int64)
    zone_lo = np.full(len(rec), -np.inf)
    zone_hi = np.full(len(rec), np.inf)
    if zones is not None:
        if "zone" not in rec.columns:
            raise ValueError("records has no 'zone' column: cannot bound the lookback by zone")
        bounds = {z.name: (z.x_lo_m, z.x_hi_m) for z in zones}
        names = rec["zone"].astype(str).to_numpy()
        zone_lo = np.array([bounds.get(nm, (-np.inf, np.inf))[0] for nm in names], dtype=float)
        zone_hi = np.array([bounds.get(nm, (-np.inf, np.inf))[1] for nm in names], dtype=float)

    def _find(code: NDArray[np.int64], tq: NDArray[np.int64]) -> NDArray[np.int64]:
        """Row of (vehicle code, time slot), −1 when absent."""
        q = code * span_idx + (tq - t_lo_idx)
        pos = np.searchsorted(vt_key, q, side="left")
        pos_c = np.minimum(pos, vt_key.size - 1)
        ok = (code >= 0) & (tq >= t_lo_idx) & (vt_key[pos_c] == q)
        return np.where(ok, pos_c, -1)

    j1 = _find(rec_code, rec_tidx)
    matched = j1 >= 1
    j1c = np.maximum(j1, 1)
    matched &= (lane_held[np.maximum(j1, 0)] == to_c) & (lane_held[j1c - 1] == from_c)
    matched &= contig[j1c - 1]
    counts["n_unmatched"] = int(np.sum(~matched))
    change_rows, j1 = change_rows[matched], j1[matched]
    rec_code, rec_tidx, to_c = rec_code[matched], rec_tidx[matched], to_c[matched]
    zone_lo, zone_hi = zone_lo[matched], zone_hi[matched]
    n_c = change_rows.size
    if n_c == 0:
        return GapSequences(_empty_samples(), counts, params)
    j0 = j1 - 1
    origin_start = run_start[j0]

    # --- the sample grid: (change, m), m = 0 at the change, m ≥ 1 earlier
    ms = np.arange(n_back + 1, dtype=np.int64)
    rows = np.full((n_c, n_back + 1), -1, dtype=np.int64)
    rows[:, 0] = j1
    alive = np.ones(n_c, dtype=bool)
    for m in range(1, n_back + 1):
        r = _find(rec_code, rec_tidx - m * step)
        ok = alive & (r >= origin_start) & (r <= j0)
        xr = x[np.maximum(r, 0)]
        ok &= (xr >= zone_lo) & (xr < zone_hi)
        rows[:, m] = np.where(ok, r, -1)
        alive = ok
    valid = rows >= 0

    # --- neighbours on a (time slot, held lane, x) ordering, as lane_change_gaps
    lane_lo = int(lane_held.min())
    n_lane = int(lane_held.max()) - lane_lo + 1
    key = t_idx * n_lane + (lane_held - lane_lo)
    x_lo = float(x.min())
    scale = float(x.max()) - x_lo + 1.0
    z = key.astype(np.float64) * scale + (x - x_lo)
    by = np.lexsort((x, key))
    key_s, z_s = key[by], z[by]
    rr = rows[valid]
    tgt = np.broadcast_to(to_c[:, None], rows.shape)[valid]
    kq = t_idx[rr] * n_lane + (tgt - lane_lo)
    zq = kq.astype(np.float64) * scale + (x[rr] - x_lo)
    blo = np.searchsorted(key_s, kq, side="left")
    bhi = np.searchsorted(key_s, kq, side="right")
    pos_r = np.searchsorted(z_s, zq, side="right")
    pos_l = np.searchsorted(z_s, zq, side="left")
    lead_ok = pos_r < bhi
    lag_ok = pos_l - 1 >= blo
    lead_row = np.where(lead_ok, by[np.minimum(pos_r, by.size - 1)], -1)
    lag_row = np.where(lag_ok, by[np.maximum(pos_l - 1, 0)], -1)
    xc, lc, vc = x[rr], length[rr], v[rr]
    lead_gap = np.where(lead_ok, x[lead_row] - length[lead_row] - xc, np.nan)
    lag_gap = np.where(lag_ok, (xc - lc) - x[lag_row], np.nan)

    def _grid(flat: NDArray[Any], fill: Any, dtype: Any) -> NDArray[Any]:
        out = np.full(rows.shape, fill, dtype=dtype)
        out[valid] = flat
        return out

    # a gap's identity reads the neighbours without the range cut (a lead that
    # drifts past max_range_m is still the same gap); the reported values honour it
    lead_code = _grid(np.where(lead_ok, veh[lead_row], -1), -1, np.int64)
    lag_code = _grid(np.where(lag_ok, veh[lag_row], -1), -1, np.int64)
    lead_x = _grid(np.where(lead_ok, x[lead_row], np.nan), np.nan, np.float64)
    lag_x = _grid(np.where(lag_ok, x[lag_row], np.nan), np.nan, np.float64)
    lead_vg = _grid(np.where(lead_ok, v[lead_row], np.nan), np.nan, np.float64)
    lag_vg = _grid(np.where(lag_ok, v[lag_row], np.nan), np.nan, np.float64)
    tg = _grid(t[rr], np.nan, np.float64)
    lead_ok &= lead_gap <= max_range_m
    lag_ok &= lag_gap <= max_range_m

    # --- gap identity from the change back: a new gap where a role changes vehicle
    same = np.ones(rows.shape, dtype=bool)
    if n_back >= 1:
        dt_ab = tg[:, :-1] - tg[:, 1:]
        same_lead = _same_vehicle(
            lead_code[:, 1:],
            lead_x[:, 1:],
            lead_vg[:, 1:],
            lead_code[:, :-1],
            lead_x[:, :-1],
            lead_vg[:, :-1],
            dt_ab,
            same_vehicle_tol_m,
        )
        same_lag = _same_vehicle(
            lag_code[:, 1:],
            lag_x[:, 1:],
            lag_vg[:, 1:],
            lag_code[:, :-1],
            lag_x[:, :-1],
            lag_vg[:, :-1],
            dt_ab,
            same_vehicle_tol_m,
        )
        same[:, 1:] = same_lead & same_lag
    gap_index = np.cumsum(~same & valid, axis=1)
    same_ids = (lead_code == lead_code[:, :1]) & (lag_code == lag_code[:, :1])
    accepted = (gap_index == 0) | same_ids
    empty = _grid(~lead_ok & ~lag_ok, True, bool)
    status = np.where(accepted, 0, np.where(empty, 2, 1))

    flat_status = status[valid]
    lead_v = np.where(lead_ok, v[lead_row], np.nan)
    lag_v = np.where(lag_ok, v[lag_row], np.nan)
    lead_gap = np.where(lead_ok, lead_gap, np.nan)
    lag_gap = np.where(lag_ok, lag_gap, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        lead_tg = np.where(lead_ok & (vc >= MIN_SPEED_FOR_TIME_GAP_MS), lead_gap / vc, np.nan)
        lag_tg = np.where(lag_ok & (lag_v >= MIN_SPEED_FOR_TIME_GAP_MS), lag_gap / lag_v, np.nan)
    suspect = (lead_ok & (lead_gap < min_gap_m)) | (lag_ok & (lag_gap < min_gap_m))
    label_arr = np.asarray(labels, dtype=object)
    change_of = np.broadcast_to(change_rows[:, None], rows.shape)[valid]
    t_change = np.broadcast_to(t[j1][:, None], rows.shape)[valid]
    k = -np.broadcast_to(ms[None, :], rows.shape)[valid]
    samples = pd.DataFrame(
        {
            "change": change_of,
            "veh_id": label_arr[veh[rr]],
            "t_change": t_change,
            "k": k,
            "t": t[rr],
            "dt_before_s": t_change - t[rr],
            "x": xc,
            "v": vc,
            "lane": lane_held[rr],
            "target_lane": tgt,
            "lead_id": _ids_or_none(label_arr, veh, lead_row, lead_ok),
            "lead_gap_m": lead_gap,
            "lead_v": lead_v,
            "lead_closing_ms": vc - lead_v,
            "lead_time_gap_s": lead_tg,
            "lag_id": _ids_or_none(label_arr, veh, lag_row, lag_ok),
            "lag_gap_m": lag_gap,
            "lag_v": lag_v,
            "lag_closing_ms": lag_v - vc,
            "lag_time_gap_s": lag_tg,
            "suspect": suspect,
            "gap_index": gap_index[valid],
            "status": np.asarray(GAP_STATUSES, dtype=object)[flat_status],
        }
    )
    counts["n_samples"] = len(samples)
    counts["n_accepted_samples"] = int(np.sum(flat_status == 0))
    counts["n_rejected_samples"] = int(np.sum(flat_status == 1))
    counts["n_empty_samples"] = int(np.sum(flat_status == 2))
    counts["n_suspect_samples"] = int(np.sum(suspect))
    rej = samples[samples["status"] == "rejected"]
    counts["n_changes_with_rejected"] = int(rej["change"].nunique())
    counts["n_rejected_gaps"] = len(rej[["change", "gap_index"]].drop_duplicates())
    return GapSequences(samples, counts, params)


def _empty_samples() -> pd.DataFrame:
    """A samples frame with the full column set and no rows."""
    cols = [
        "change",
        "veh_id",
        "t_change",
        "k",
        "t",
        "dt_before_s",
        "x",
        "v",
        "lane",
        "target_lane",
        "lead_id",
        "lead_gap_m",
        "lead_v",
        "lead_closing_ms",
        "lead_time_gap_s",
        "lag_id",
        "lag_gap_m",
        "lag_v",
        "lag_closing_ms",
        "lag_time_gap_s",
        "suspect",
        "gap_index",
        "status",
    ]
    return pd.DataFrame({c: pd.Series(dtype=object) for c in cols})
