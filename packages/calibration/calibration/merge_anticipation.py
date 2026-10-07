"""How far ahead entrants line up their merge: the anticipation reach (M1).

docs/WEAVE_LOSS_DIAGNOSIS.md (§3.12, §6.3) found that the weave's
``lookahead_m`` (``flowstate_core.config.WEAVE_DEFAULTS``, 120 m, an
engineering constant, never measured) moves the T.H.52 weave's capacity
(200 m: +98 [+55, +141] veh/h; 300 m: +177 [+134, +219]), and that adopting a
value because it moves the result is the tuning the protocol forbids. This
module measures the quantity the constant stands for from trajectories, so
that a measured distribution can replace it under the pre-registered rule of
docs/MERGE_ANTICIPATION.md.

**What the constant does in the model** (``microsim.runner._weave_step``,
``_weave_choose_gap``, read 2026-10-07). An entering vehicle still on the
on-ramp within ``lookahead_m`` of the weaving section's start
(``approaching``) chooses a target-lane gap — one it is in or abreast of: the
gap's follower F behind its rear, the gap's leader L with its front ahead of
the entrant's own — F is driven towards it as a virtual leader
(cooperation), and the entrant eases towards L when it would have to brake
for L. All three start at that one trigger and continue every step, in the
section too, until the change. The constant also bounds how far behind the
entrant F may be. It sets no speed match: the weave has no speed ceiling
(the scripted merge's use of the same key is a speed-match reach, a different
mechanism). The anticipation the model defines is therefore a *gap held*:
from a fixed reach before the gore to the change, the entrant is beside the
gap it will enter.

**The observable** (definition ``gap``, the primary). For an entering change
(auxiliary lane → mainline, ``movement == "entering"`` in a merge or weave
zone of :func:`calibration.lane_change_gaps.lane_change_gaps`; confirmed, not
suspect) with new leader L and new follower F (the vehicles
``lane_change_gaps`` finds at the change: in the target lane, the nearest
front strictly ahead of the entrant's front and the nearest at or behind it,
both within ``max_range_m``), walk back from the change every ``step_s``. The
entrant is *beside its eventual gap* at an instant when its own target-lane
neighbours at its front, found the same way, are L (lead) and F (lag), each
followed back through tracker fragment switches. The **anticipation reach**
``D`` is the distance the entrant travelled from the start of its last such
run to the change, ``x(t_change) − x(t_onset)`` along its own trajectory;
the **anticipation time** is ``t_change − t_onset``. Going back, the run ends
at the first spell of at least ``min_break_s`` in which the relation fails
through the vehicles' own motion — the entrant behind F or ahead of L, F or L
not yet in the target lane, or another tracked vehicle between them that is
still tracked one step later — so a one-sample flicker does not end it.
``D = 0`` means the entrant was beside the gap only at the change: it closed
on the gap and changed. This is the gap identity of WP-78's
:func:`calibration.lane_change_gaps.gap_sequences` (its ``accepted``
samples), read at the table's own cadence and with censoring.

Two speed definitions are measured beside it and reported, never adopted
(docs/MERGE_ANTICIPATION.md §2): ``speed_1`` — the operational sketch of
docs/WEAVE_LOSS_DIAGNOSIS.md §6.3, the last time before the change from which
the entrant's speed stays within ±1 m/s of L's (L followed back by identity,
whether or not it is the entrant's immediate lead then) — and ``speed_2``
(±2 m/s). The same ``min_break_s`` persistence applies.

**Censoring.** The walk can stop before the relation fails; the event is then
right-censored at the reach of its last instant beside the gap (the true
reach is at least that). Reasons (:data:`CENSOR_REASONS`): ``window_start``
(the frame's first time slot), ``changer_track_start`` (the entrant's track
begins: no sample by id, no fragment continuing it within ``max_bridge_s``),
``changer_lane`` (the entrant was on a mainline lane: it is not an entrant
before that), ``upstream_limit`` (more than ``upstream_limit_m`` upstream of
its zone's start), ``partner_track_start`` (L, or F for ``gap``, begins),
``intruder_track_end`` (``gap`` only: a vehicle between the entrant and L or
F whose track ends one step later — it may still be there, untracked) and
``lookback_cap``. Fragment switches are followed with the WP-78 continuity
rule (a front carried at the mean of two speeds landing within
``same_vehicle_tol_m``); a vehicle missing for at most ``max_bridge_s`` is
bridged: those instants neither extend nor break the run. Events with no L
(no F, for ``gap``) at the change are ``no_partner`` and left out.

**Coverage (I-24 MOTION, docs/I24_DATA.md §4; about half the peak
vehicle-time tracked).** An untracked vehicle inside the observed gap merges
two true gaps into one: the entrant can move from one to the other unseen,
so ``gap``'s reach is expected to read long (not a bound). An untracked L or
F at the change makes the recorded partner a farther vehicle. Partner
fragment boundaries and vanishing intruders censor rather than end a run.
The speed definitions read speeds (coverage-robust, docs/MERGE_MODEL_BRIEF.md
coverage rules) of a recorded L that may not be the true one.

**Statistics.** The reach distribution is the Kaplan–Meier product-limit
estimate under right censoring (:func:`km_curve`; Kaplan & Meier 1958,
J. Am. Stat. Assoc. 53:457–481): quantile ``q`` is the smallest observed
reach at which the survival falls to ``1 − q`` or below, ``None`` when it
never does (not identified). Intervals: percentile bootstrap over events
(:func:`km_bootstrap`), an unidentified resample counting as +∞, so an
interval whose upper end is +∞ is reported as not identified. The estimate
assumes censoring independent of the reach given the stratum; fragment
starts are set by camera geometry, but ``upstream_limit`` and a zone-start
effect need not be (stated with the results).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from calibration.lane_change_gaps import (
    DEFAULT_MAX_RANGE_M,
    DEFAULT_SAME_VEHICLE_TOL_M,
    SPEED_CLASSES_MS,
    Zone,
)
from calibration.lane_change_relaxation import _Frame
from calibration.lanechange import DEFAULT_MIN_DWELL_S

DEFAULT_LOOKBACK_S: Final[float] = 120.0
"""How far back from the change the walk goes [s]. The Old Hickory
acceleration lane is 1.15 km (docs/MERGE_MODEL_BRIEF.md §1.5): 120 s covers it
above 10 m/s, and 2.4 km at the free-flow 20 m/s class boundary."""

DEFAULT_MIN_BREAK_S: Final[float] = 1.0
"""Shortest spell of failure that ends a run [s]: the debounce's dwell
(``calibration.lanechange.DEFAULT_MIN_DWELL_S``), five 5 Hz samples."""

DEFAULT_MAX_BRIDGE_S: Final[float] = 1.0
"""Longest stretch a vehicle may be missing and still be followed [s]. At
3 m/s² a front predicted over 1 s at the mean of two speeds is off by under
1.5 m, inside ``same_vehicle_tol_m`` (2 m); two vehicles in one lane are at
least a length plus a gap apart."""

DEFAULT_UPSTREAM_LIMIT_M: Final[float] = 500.0
"""How far upstream of its zone's start the entrant may be followed [m].
On I-24 MOTION ramp-lane fragments appear at the gores (docs/I24_DATA.md §6),
so the bound should rarely bind; it keeps a stray ramp track from running on."""

DEFAULT_SPEED_BANDS_MS: Final[tuple[float, ...]] = (1.0, 2.0)
"""Speed bands of the secondary definitions [m/s]: ±1 m/s is the sketch of
docs/WEAVE_LOSS_DIAGNOSIS.md §6.3; ±2 m/s a sensitivity."""

PRIMARY_DEFINITION: Final[str] = "gap"
"""The definition the pre-registered rule adopts (docs/MERGE_ANTICIPATION.md)."""

CENSOR_REASONS: Final[tuple[str, ...]] = (
    "window_start",
    "changer_track_start",
    "changer_lane",
    "upstream_limit",
    "partner_track_start",
    "intruder_track_end",
    "lookback_cap",
)
"""Why a walk stopped with the relation still holding (module docstring)."""

OUTCOMES: Final[tuple[str, ...]] = ("onset", *CENSOR_REASONS, "no_partner")
"""Every event's outcome per definition: ``onset`` (observed), a censoring
reason, or ``no_partner`` (not evaluated)."""

QUANTILES: Final[tuple[float, ...]] = (0.1, 0.25, 0.5, 0.75, 0.9)
"""Quantiles of the reach reported per stratum."""

DEFAULT_N_BOOT: Final[int] = 1000
"""Bootstrap resamples per stratum."""

DEFAULT_SEED: Final[int] = 20261007
"""Bootstrap master seed (``flowstate_core.rng.spawn_seeds``)."""

_GAP_SAME, _GAP_FAIL, _GAP_INTRUDER, _GAP_SELF = 0, 1, 2, 3


def speed_definition_name(band_ms: float) -> str:
    """The name of a speed definition: ``speed_1`` for ±1 m/s, ``speed_0p5`` for ±0.5."""
    return "speed_" + f"{band_ms:g}".replace(".", "p")


def definitions(speed_bands_ms: Sequence[float] = DEFAULT_SPEED_BANDS_MS) -> tuple[str, ...]:
    """Every definition measured: ``gap`` then one per speed band."""
    return (PRIMARY_DEFINITION, *(speed_definition_name(b) for b in speed_bands_ms))


def speed_class(v: NDArray[np.float64]) -> NDArray[np.object_]:
    """Changer-speed class labels of :data:`calibration.lane_change_gaps.SPEED_CLASSES_MS`."""
    out = np.full(v.shape, "", dtype=object)
    for label, lo, hi in SPEED_CLASSES_MS:
        out[(v >= lo) & (v < hi)] = label
    return out


@dataclass
class AnticipationEvents:
    """Per-change anticipation measurements and the counts behind them.

    Attributes:
        events: One row per evaluated entering change (columns documented on
            :func:`anticipation_events`).
        counts: Records seen, left out by reason, events, and per-definition
            outcome counts (``outcome_<definition>_<outcome>``).
        parameters: The measurement parameters, for artifacts.
    """

    events: pd.DataFrame
    counts: dict[str, int]
    parameters: dict[str, Any] = field(default_factory=dict)


def _whole_steps(value_s: float, unit_s: float, name: str) -> int:
    """``value_s`` as a whole number of ``unit_s`` steps (rounded up)."""
    if value_s < 0.0:
        raise ValueError(f"{name} must be >= 0")
    return math.ceil(value_s / unit_s - 1e-9)


def _side_status(
    fr: _Frame,
    *,
    found: NDArray[np.int64],
    partner: NDArray[np.int64],
    changer: NDArray[np.int64],
    target: NDArray[np.int64],
    partner_ahead: NDArray[np.bool_],
    tq_later: NDArray[np.int64],
    tol_m: float,
) -> NDArray[np.int64]:
    """One side of the gap relation at one instant (rows all valid where evaluated).

    ``found`` is the neighbour the search returns on that side, ``partner``
    the eventual partner's row at this instant, ``partner_ahead`` whether the
    partner is on its own side of the entrant (L ahead of its front, F at or
    behind it). Status: same vehicle (or, with the partner in the target lane,
    a duplicate fragment of it within ``tol_m``), a target-lane vehicle level
    with the entrant's front within ``tol_m`` — a duplicate of the entrant, or
    a vehicle abreast of it; ambiguous, as ``lane_change_gaps`` marks such
    gaps ``suspect`` — (unknown), an intruder between them whose track ends
    one step later (censoring), else a failure.
    """
    p = np.maximum(partner, 0)
    q = np.maximum(found, 0)
    c = np.maximum(changer, 0)
    has = found >= 0
    in_lane = fr.lane_held[p] == target
    same = (found == partner) | (has & in_lane & (np.abs(fr.x[q] - fr.x[p]) <= tol_m))
    self_dup = has & ~same & (np.abs(fr.x[q] - fr.x[c]) <= tol_m)
    between = has & ~same & ~self_dup & in_lane & partner_ahead
    later = fr.find(np.where(between, fr.veh[q], -1), tq_later)
    intruder_ends = between & (later < 0)
    status = np.full(found.shape, _GAP_FAIL, dtype=np.int64)
    status[intruder_ends] = _GAP_INTRUDER
    status[self_dup] = _GAP_SELF
    status[same] = _GAP_SAME
    return status


def _walk_back(
    fr: _Frame,
    *,
    j1: NDArray[np.int64],
    target: NDArray[np.int64],
    zone_lo: NDArray[np.float64],
    lead0: NDArray[np.int64],
    lag0: NDArray[np.int64],
    mainline: NDArray[np.int64],
    step: int,
    k_max: int,
    n_break: int,
    n_bridge: int,
    upstream_limit_m: float,
    bands: Sequence[float],
    tol_m: float,
) -> dict[str, Any]:
    """Walk every change back from its sample; per definition, the last row beside the gap."""
    n = j1.size
    nd = 1 + len(bands)
    done = np.zeros((nd, n), dtype=bool)
    outcome = np.full((nd, n), "", dtype=object)
    last_c = np.tile(j1, (nd, 1))
    last_l = np.tile(lead0, (nd, 1))
    last_f = np.tile(lag0, (nd, 1))
    streak = np.zeros((nd, n), dtype=np.int64)
    unknown = np.zeros((nd, n), dtype=np.int64)
    no_lead = lead0 < 0
    no_lag = lag0 < 0
    done[:, no_lead] = True
    outcome[:, no_lead] = "no_partner"
    done[0, no_lag] = True
    outcome[0, no_lag] = "no_partner"
    c_seen, l_seen, f_seen = j1.copy(), lead0.copy(), lag0.copy()
    c_miss = np.zeros(n, dtype=np.int64)
    l_miss = np.zeros(n, dtype=np.int64)
    f_miss = np.zeros(n, dtype=np.int64)
    c_stitched = np.zeros(n, dtype=bool)
    p_stitched = np.zeros(n, dtype=bool)
    tidx0 = fr.t_idx[j1]
    veh0 = fr.veh[j1]
    l_veh0 = np.where(lead0 >= 0, fr.veh[np.maximum(lead0, 0)], -1)
    f_veh0 = np.where(lag0 >= 0, fr.veh[np.maximum(lag0, 0)], -1)

    def apply(
        d: int,
        censors: list[tuple[NDArray[np.bool_], str]],
        track: NDArray[np.bool_],
        violate: NDArray[np.bool_],
        c_now: NDArray[np.int64],
        l_now: NDArray[np.int64],
        f_now: NDArray[np.int64],
    ) -> None:
        act = ~done[d]
        for mask, name in censors:
            m = act & mask
            outcome[d, m] = name
            done[d, m] = True
            act &= ~m
        tr = act & track
        streak[d, tr] = 0
        last_c[d, tr] = c_now[tr]
        last_l[d, tr] = l_now[tr]
        last_f[d, tr] = f_now[tr]
        vi = act & violate
        streak[d, vi] += 1
        fin = vi & (streak[d] >= n_break)
        outcome[d, fin] = "onset"
        done[d, fin] = True
        unknown[d, act & ~tr & ~vi] += 1

    for k in range(1, k_max + 1):
        active = ~done.all(axis=0)
        if not active.any():
            break
        tq = tidx0 - k * step
        tq_later = tq + step
        prev_c = np.where(active, c_seen, -1)
        c_now = fr.track(prev_c, tq, fr.lane_held[np.maximum(c_seen, 0)], tol_m)
        c_ok = c_now >= 0
        c_miss = np.where(active & ~c_ok, c_miss + 1, 0)
        c_stitched |= c_ok & (fr.veh[np.maximum(c_now, 0)] != veh0)
        prev_l = np.where(active & (l_seen >= 0), l_seen, -1)
        l_now = fr.track(prev_l, tq, target, tol_m)
        l_ok = l_now >= 0
        l_miss = np.where((prev_l >= 0) & ~l_ok, l_miss + 1, 0)
        prev_f = np.where(active & (f_seen >= 0), f_seen, -1)
        f_now = fr.track(prev_f, tq, target, tol_m)
        f_ok = f_now >= 0
        f_miss = np.where((prev_f >= 0) & ~f_ok, f_miss + 1, 0)
        p_stitched |= (l_ok & (fr.veh[np.maximum(l_now, 0)] != l_veh0)) | (
            f_ok & (fr.veh[np.maximum(f_now, 0)] != f_veh0)
        )

        cens_window = active & (tq < fr.t_lo)
        cens_c = active & ~c_ok & (c_miss > n_bridge)
        cc = np.maximum(c_now, 0)
        on_main = c_ok & np.isin(fr.lane_held[cc], mainline)
        upstream = c_ok & (fr.x[cc] < zone_lo - upstream_limit_m)
        cens_l = (prev_l >= 0) & ~l_ok & (l_miss > n_bridge)
        cens_f = (prev_f >= 0) & ~f_ok & (f_miss > n_bridge)
        common = [
            (cens_window, "window_start"),
            (cens_c, "changer_track_start"),
            (on_main, "changer_lane"),
            (upstream, "upstream_limit"),
        ]

        # the gap relation: the entrant's target-lane neighbours are L and F
        known = c_ok & l_ok & f_ok & ~on_main & ~upstream
        lead_r, lag_r = fr.neighbours(np.where(known, c_now, -1), target)
        ll = np.maximum(l_now, 0)
        ff = np.maximum(f_now, 0)
        lead_status = _side_status(
            fr,
            found=lead_r,
            partner=l_now,
            changer=c_now,
            target=target,
            partner_ahead=fr.x[ll] > fr.x[cc],
            tq_later=tq_later,
            tol_m=tol_m,
        )
        lag_status = _side_status(
            fr,
            found=lag_r,
            partner=f_now,
            changer=c_now,
            target=target,
            partner_ahead=fr.x[ff] <= fr.x[cc],
            tq_later=tq_later,
            tol_m=tol_m,
        )
        fail = known & ((lead_status == _GAP_FAIL) | (lag_status == _GAP_FAIL))
        intr = known & ~fail & ((lead_status == _GAP_INTRUDER) | (lag_status == _GAP_INTRUDER))
        track_gap = known & (lead_status == _GAP_SAME) & (lag_status == _GAP_SAME)
        apply(
            0,
            [*common, (cens_l | cens_f, "partner_track_start"), (intr, "intruder_track_end")],
            track_gap,
            fail,
            c_now,
            l_now,
            f_now,
        )

        # the speed relations: the entrant's speed within the band of L's
        known_s = c_ok & l_ok & ~on_main & ~upstream
        dv = np.abs(fr.v[cc] - fr.v[ll])
        for i, band in enumerate(bands, start=1):
            within = known_s & (dv <= band + 1e-9)
            apply(
                i,
                [*common, (cens_l, "partner_track_start")],
                within,
                known_s & ~within,
                c_now,
                l_now,
                f_now,
            )

        c_seen = np.where(c_ok, c_now, c_seen)
        l_seen = np.where(l_ok, l_now, l_seen)
        f_seen = np.where(f_ok, f_now, f_seen)

    left = ~done
    outcome[left] = "lookback_cap"
    return {
        "outcome": outcome,
        "last_c": last_c,
        "last_l": last_l,
        "last_f": last_f,
        "unknown": unknown,
        "c_stitched": c_stitched,
        "p_stitched": p_stitched,
    }


def anticipation_events(
    df: pd.DataFrame,
    records: pd.DataFrame,
    zones: Sequence[Zone],
    *,
    mainline_lanes: Collection[int],
    movements: Collection[str] = ("entering",),
    zone_names: Collection[str] | None = None,
    dt_s: float | None = None,
    max_gap_s: float | None = None,
    min_dwell_s: float = DEFAULT_MIN_DWELL_S,
    step_s: float | None = None,
    lookback_s: float = DEFAULT_LOOKBACK_S,
    min_break_s: float = DEFAULT_MIN_BREAK_S,
    max_bridge_s: float = DEFAULT_MAX_BRIDGE_S,
    upstream_limit_m: float = DEFAULT_UPSTREAM_LIMIT_M,
    speed_bands_ms: Sequence[float] = DEFAULT_SPEED_BANDS_MS,
    max_range_m: float = DEFAULT_MAX_RANGE_M,
    same_vehicle_tol_m: float = DEFAULT_SAME_VEHICLE_TOL_M,
    default_length_m: float | None = None,
) -> AnticipationEvents:
    """The anticipation reach of every entering change (module docstring).

    Args:
        df: The frame ``records`` were read from (``t, veh_id, x, lane, v``,
            optional ``length``; front-bumper ``x``, band lanes, one shared
            time grid — :func:`calibration.lane_change_gaps.lane_change_gaps`).
        records: :attr:`calibration.lane_change_gaps.LaneChangeGaps.records`
            of ``df``, made with the same ``dt_s``, ``max_gap_s`` and
            ``min_dwell_s``.
        zones: The zones ``lane_change_gaps`` was given (the start of each
            change's zone anchors ``d_change_m`` and ``upstream_limit_m``).
        mainline_lanes: Band ids of the through lanes.
        movements: Movements measured (``entering`` by default).
        zone_names: Only changes in these zones (all when None).
        dt_s: Sampling interval of ``df`` [s]; inferred when None.
        max_gap_s: Contiguity bound [s]; ``2.5 × dt_s`` when None.
        min_dwell_s: The lane debounce [s].
        step_s: Walk step [s], a whole multiple of ``dt_s``; ``dt_s`` when None.
        lookback_s: Walk length [s].
        min_break_s: Shortest failure spell that ends a run [s].
        max_bridge_s: Longest bridged absence of a vehicle [s].
        upstream_limit_m: Walk bound upstream of the zone's start [m].
        speed_bands_ms: Bands of the speed definitions [m/s].
        max_range_m: Partner range at the change [m].
        same_vehicle_tol_m: Fragment continuity and duplicate tolerance [m].
        default_length_m: Vehicle length [m] when ``df`` has no ``length``.

    Returns:
        :class:`AnticipationEvents`. ``events`` columns: ``record`` (row of
        ``records``), ``t, veh_id, x, zone, zone_kind, from_lane, to_lane,
        v`` (the change), ``speed_class``, ``d_change_m`` (change position
        past the zone's start), ``lead_gap_m, lag_gap_m, lead_closing_ms,
        lag_closing_ms`` (the record's), ``changer_stitched`` /
        ``partner_stitched`` (a fragment switch was followed), and per
        definition ``<def>_outcome`` (:data:`OUTCOMES`), ``<def>_censored``,
        ``<def>_reach_m``, ``<def>_time_s`` (NaN when ``no_partner``),
        ``<def>_onset_x_rel_m`` (onset position past the zone's start;
        negative upstream of it), ``<def>_onset_v_ms``, ``<def>_unknown_s``
        (bridged or unknown time inside the walk); for ``gap`` also
        ``gap_onset_lead_dv_ms`` (L's speed minus the entrant's at onset),
        ``gap_onset_lag_dv_ms`` (the entrant's minus F's),
        ``gap_onset_lead_gap_m``, ``gap_onset_lag_gap_m`` (bumper gaps) and
        ``gap_onset_lag_dist_m`` (F's front behind the entrant's front: the
        reach of the model's follower search).

    Raises:
        ValueError: On missing columns, a missing length, a ``step_s`` that
            is not a whole multiple of ``dt_s`` or a negative duration.
    """
    rmissing = {"t", "veh_id", "from_lane", "to_lane", "zone", "movement", "v"} - set(
        records.columns
    )
    if rmissing:
        raise ValueError(f"records is missing columns: {sorted(rmissing)}")
    defs = definitions(speed_bands_ms)
    fr = _Frame(
        df,
        dt_s=dt_s,
        max_gap_s=max_gap_s,
        min_dwell_s=min_dwell_s,
        default_length_m=default_length_m,
    )
    dt = fr.dt_s
    step_s = dt if step_s is None else float(step_s)
    step = round(step_s / dt)
    if step < 1 or abs(step * dt - step_s) > 1e-6 * max(1.0, step_s):
        raise ValueError(f"step_s ({step_s}) must be a whole multiple of dt_s ({dt})")
    if lookback_s < 0.0 or max_bridge_s < 0.0:
        raise ValueError("lookback_s and max_bridge_s must be >= 0")
    k_max = math.floor(lookback_s / step_s + 1e-9)
    n_break = max(1, _whole_steps(min_break_s, step_s, "min_break_s"))
    n_bridge = math.floor(max_bridge_s / step_s + 1e-9)
    params: dict[str, Any] = {
        "definitions": list(defs),
        "primary_definition": PRIMARY_DEFINITION,
        "dt_s": dt,
        "max_gap_s": fr.max_gap_s,
        "min_dwell_s": min_dwell_s,
        "step_s": step_s,
        "lookback_s": lookback_s,
        "min_break_s": min_break_s,
        "n_break_steps": n_break,
        "max_bridge_s": max_bridge_s,
        "n_bridge_steps": n_bridge,
        "upstream_limit_m": upstream_limit_m,
        "speed_bands_ms": [float(b) for b in speed_bands_ms],
        "max_range_m": max_range_m,
        "same_vehicle_tol_m": same_vehicle_tol_m,
        "movements": sorted(movements),
        "zone_names": sorted(zone_names) if zone_names is not None else None,
    }
    counts: dict[str, int] = {
        "n_records": len(records),
        "n_other_movement": 0,
        "n_other_zone": 0,
        "n_unconfirmed": 0,
        "n_suspect": 0,
        "n_unmatched": 0,
        "n_events": 0,
    }
    for d in defs:
        for o in OUTCOMES:
            counts[f"outcome_{d}_{o}"] = 0
    if len(records) == 0 or fr.n == 0:
        return AnticipationEvents(_empty_events(defs), counts, params)

    bounds = {z.name: z for z in zones}
    wanted = set(bounds) if zone_names is None else set(bounds) & {str(z) for z in zone_names}
    rec = records.reset_index(drop=True)
    sel = np.array(rec["movement"].isin(list(movements)), dtype=bool)
    counts["n_other_movement"] = int(np.sum(~sel))
    in_zone_np = np.array(rec["zone"].astype(str).isin(sorted(wanted)), dtype=bool)
    counts["n_other_zone"] = int(np.sum(sel & ~in_zone_np))
    sel &= in_zone_np
    if "confirmed" in rec.columns:
        conf = np.array(rec["confirmed"].astype(bool), dtype=bool)
        counts["n_unconfirmed"] = int(np.sum(sel & ~conf))
        sel &= conf
    if "suspect" in rec.columns:
        sus = np.array(rec["suspect"].astype(bool), dtype=bool)
        counts["n_suspect"] = int(np.sum(sel & sus))
        sel &= ~sus
    rows = np.flatnonzero(sel).astype(np.int64)
    sub = rec.iloc[rows]
    code = pd.Index(fr.labels).get_indexer(pd.Index(sub["veh_id"]))
    code = np.asarray(code, dtype=np.int64)
    tidx = np.rint(sub["t"].to_numpy(dtype=np.float64) / dt).astype(np.int64)
    j1 = fr.find(code, tidx)
    from_l = sub["from_lane"].to_numpy(dtype=np.int64)
    to_l = sub["to_lane"].to_numpy(dtype=np.int64)
    j1c = np.maximum(j1, 1)
    matched = j1 >= 1
    matched &= fr.veh[j1c - 1] == fr.veh[j1c]
    matched &= (fr.lane_held[j1c] == to_l) & (fr.lane_held[j1c - 1] == from_l)
    matched &= fr.contig[j1c - 1]
    counts["n_unmatched"] = int(np.sum(~matched))
    rows, sub, j1, to_l = rows[matched], sub[matched], j1[matched], to_l[matched]
    n = int(j1.size)
    counts["n_events"] = n
    if n == 0:
        return AnticipationEvents(_empty_events(defs), counts, params)

    zone_lo = np.array([bounds[str(z)].x_lo_m for z in sub["zone"]], dtype=np.float64)
    lead0, lag0 = fr.neighbours(j1, to_l)
    lead_gap0 = fr.gap_behind(j1, lead0)
    lag_gap0 = fr.gap_behind(lag0, j1)
    with np.errstate(invalid="ignore"):
        lead0 = np.where((lead0 >= 0) & (lead_gap0 <= max_range_m), lead0, -1)
        lag0 = np.where((lag0 >= 0) & (lag_gap0 <= max_range_m), lag0, -1)
    walk = _walk_back(
        fr,
        j1=j1,
        target=to_l,
        zone_lo=zone_lo,
        lead0=lead0,
        lag0=lag0,
        mainline=np.fromiter((int(m) for m in mainline_lanes), dtype=np.int64),
        step=step,
        k_max=k_max,
        n_break=n_break,
        n_bridge=n_bridge,
        upstream_limit_m=upstream_limit_m,
        bands=list(speed_bands_ms),
        tol_m=same_vehicle_tol_m,
    )

    v_change = sub["v"].to_numpy(dtype=np.float64)
    out: dict[str, Any] = {
        "record": rows,
        "t": sub["t"].to_numpy(dtype=np.float64),
        "veh_id": sub["veh_id"].to_numpy(dtype=object),
        "x": fr.x[j1],
        "zone": sub["zone"].astype(str).to_numpy(dtype=object),
        "zone_kind": (
            sub["zone_kind"].astype(str).to_numpy(dtype=object)
            if "zone_kind" in sub.columns
            else np.array([bounds[str(z)].kind for z in sub["zone"]], dtype=object)
        ),
        "from_lane": sub["from_lane"].to_numpy(dtype=np.int64),
        "to_lane": to_l,
        "v": v_change,
        "speed_class": speed_class(v_change),
        "d_change_m": fr.x[j1] - zone_lo,
    }
    for col in ("lead_gap_m", "lag_gap_m", "lead_closing_ms", "lag_closing_ms"):
        out[col] = sub[col].to_numpy(dtype=np.float64) if col in sub.columns else np.full(n, np.nan)
    out["changer_stitched"] = walk["c_stitched"]
    out["partner_stitched"] = walk["p_stitched"]
    for i, d in enumerate(defs):
        oc = walk["outcome"][i]
        evaluated = oc != "no_partner"
        lc = walk["last_c"][i]
        lcc = np.maximum(lc, 0)
        out[f"{d}_outcome"] = oc
        out[f"{d}_censored"] = np.isin(oc, list(CENSOR_REASONS))
        out[f"{d}_reach_m"] = np.where(evaluated, fr.x[j1] - fr.x[lcc], np.nan)
        out[f"{d}_time_s"] = np.where(evaluated, fr.t[j1] - fr.t[lcc], np.nan)
        out[f"{d}_onset_x_rel_m"] = np.where(evaluated, fr.x[lcc] - zone_lo, np.nan)
        out[f"{d}_onset_v_ms"] = np.where(evaluated, fr.v[lcc], np.nan)
        out[f"{d}_unknown_s"] = np.where(evaluated, walk["unknown"][i] * step_s, np.nan)
        if d == PRIMARY_DEFINITION:
            ll = walk["last_l"][i]
            lf = walk["last_f"][i]
            okp = evaluated & (ll >= 0) & (lf >= 0)
            lll = np.maximum(ll, 0)
            lff = np.maximum(lf, 0)
            out["gap_onset_lead_dv_ms"] = np.where(okp, fr.v[lll] - fr.v[lcc], np.nan)
            out["gap_onset_lag_dv_ms"] = np.where(okp, fr.v[lcc] - fr.v[lff], np.nan)
            out["gap_onset_lead_gap_m"] = np.where(okp, fr.gap_behind(lcc, lll), np.nan)
            out["gap_onset_lag_gap_m"] = np.where(okp, fr.gap_behind(lff, lcc), np.nan)
            out["gap_onset_lag_dist_m"] = np.where(okp, fr.x[lcc] - fr.x[lff], np.nan)
        for o, c in Counter(oc.tolist()).items():
            counts[f"outcome_{d}_{o}"] += int(c)
    events = pd.DataFrame(out).sort_values(["t", "x"], kind="stable").reset_index(drop=True)
    return AnticipationEvents(events, counts, params)


def _empty_events(defs: Sequence[str]) -> pd.DataFrame:
    """An events frame with the full column set and no rows."""
    cols = [
        "record",
        "t",
        "veh_id",
        "x",
        "zone",
        "zone_kind",
        "from_lane",
        "to_lane",
        "v",
        "speed_class",
        "d_change_m",
        "lead_gap_m",
        "lag_gap_m",
        "lead_closing_ms",
        "lag_closing_ms",
        "changer_stitched",
        "partner_stitched",
    ]
    for d in defs:
        cols += [
            f"{d}_outcome",
            f"{d}_censored",
            f"{d}_reach_m",
            f"{d}_time_s",
            f"{d}_onset_x_rel_m",
            f"{d}_onset_v_ms",
            f"{d}_unknown_s",
        ]
        if d == PRIMARY_DEFINITION:
            cols += [
                "gap_onset_lead_dv_ms",
                "gap_onset_lag_dv_ms",
                "gap_onset_lead_gap_m",
                "gap_onset_lag_gap_m",
                "gap_onset_lag_dist_m",
            ]
    return pd.DataFrame({c: pd.Series(dtype=object) for c in cols})


# --- Kaplan-Meier and the bootstrap ----------------------------------------------------------


def km_curve(
    reach: NDArray[np.float64] | Sequence[float],
    censored: NDArray[np.bool_] | Sequence[bool],
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.int64]]:
    """Kaplan–Meier product-limit survival of a right-censored sample.

    ``S(t) = Π_{t_j ≤ t} (1 − d_j / n_j)`` over the distinct observed
    (uncensored) values ``t_j``, with ``d_j`` the observed values equal to
    ``t_j`` and ``n_j`` the values (observed or censored) at or above it — a
    value censored at ``t_j`` is still at risk there (the usual convention).
    Non-finite values are dropped.

    Args:
        reach: Values (reach [m] or time [s]).
        censored: True where the value is a lower bound.

    Returns:
        ``(times, survival, n_at_risk)`` at the distinct observed values,
        ascending; empty arrays when nothing is observed.
    """
    d = np.asarray(reach, dtype=np.float64)
    c = np.asarray(censored, dtype=bool)
    ok = np.isfinite(d)
    d, c = d[ok], c[ok]
    ev = d[~c]
    if ev.size == 0:
        return np.zeros(0), np.zeros(0), np.zeros(0, dtype=np.int64)
    times = np.unique(ev)
    d_sorted = np.sort(d)
    n_risk = (d.size - np.searchsorted(d_sorted, times, side="left")).astype(np.int64)
    ev_sorted = np.sort(ev)
    deaths = np.searchsorted(ev_sorted, times, side="right") - np.searchsorted(
        ev_sorted, times, side="left"
    )
    surv = np.cumprod(1.0 - deaths / n_risk)
    return times, surv, n_risk


def km_quantile(times: NDArray[np.float64], surv: NDArray[np.float64], q: float) -> float | None:
    """Quantile ``q`` of a :func:`km_curve`: the smallest time with ``S ≤ 1 − q``; None if never."""
    hit = np.flatnonzero(surv <= 1.0 - q + 1e-12)
    return float(times[hit[0]]) if hit.size else None


def km_quantiles(
    reach: NDArray[np.float64] | Sequence[float],
    censored: NDArray[np.bool_] | Sequence[bool],
    qs: Sequence[float] = QUANTILES,
) -> dict[str, float | None]:
    """The :func:`km_curve` quantiles keyed ``p10`` … (None where not identified)."""
    times, surv, _ = km_curve(reach, censored)
    return {_qkey(q): km_quantile(times, surv, q) for q in qs}


def _qkey(q: float) -> str:
    return f"p{round(q * 100):02d}"


def km_bootstrap(
    reach: NDArray[np.float64] | Sequence[float],
    censored: NDArray[np.bool_] | Sequence[bool],
    qs: Sequence[float] = QUANTILES,
    *,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
) -> dict[str, dict[str, Any]]:
    """Percentile bootstrap intervals (95 %) of the Kaplan–Meier quantiles.

    Events are resampled with replacement; a resample in which a quantile is
    not identified counts as +∞ for it, so the interval's upper end is
    reported as None (not identified) when more than 2.5 % of resamples miss
    it. Intervals are order statistics of the resamples (no interpolation).

    Args:
        reach: Values.
        censored: Censoring flags.
        qs: Quantiles.
        n_boot: Resamples.
        seed: RNG seed (``flowstate_core.rng.make_rng``).

    Returns:
        Per quantile key: ``ci95`` (``[lo, hi]``, either None when not
        identified) and ``unidentified_share``.
    """
    from flowstate_core.rng import make_rng

    d = np.asarray(reach, dtype=np.float64)
    c = np.asarray(censored, dtype=bool)
    ok = np.isfinite(d)
    d, c = d[ok], c[ok]
    out: dict[str, dict[str, Any]] = {}
    if d.size == 0 or n_boot < 1:
        return {_qkey(q): {"ci95": [None, None], "unidentified_share": None} for q in qs}
    rng = make_rng(seed)
    vals = np.full((n_boot, len(qs)), np.inf)
    for b in range(n_boot):
        idx = rng.integers(0, d.size, d.size)
        times, surv, _ = km_curve(d[idx], c[idx])
        for j, q in enumerate(qs):
            val = km_quantile(times, surv, q) if times.size else None
            if val is not None:
                vals[b, j] = val
    for j, q in enumerate(qs):
        col = np.sort(vals[:, j])
        lo = col[max(0, math.ceil(0.025 * n_boot) - 1)]
        hi = col[min(n_boot - 1, math.ceil(0.975 * n_boot) - 1)]
        out[_qkey(q)] = {
            "ci95": [
                round(float(lo), 2) if np.isfinite(lo) else None,
                round(float(hi), 2) if np.isfinite(hi) else None,
            ],
            "unidentified_share": round(float(np.mean(~np.isfinite(vals[:, j]))), 4),
        }
    return out


# --- summaries --------------------------------------------------------------------------------


def _plain_quantiles(values: NDArray[np.float64], qs: Sequence[float]) -> dict[str, float | None]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {_qkey(q): None for q in qs}
    return {
        _qkey(q): round(float(v), 2) for q, v in zip(qs, np.quantile(finite, list(qs)), strict=True)
    }


def _rounded(d: Mapping[str, float | None]) -> dict[str, float | None]:
    return {k: (None if v is None else round(float(v), 2)) for k, v in d.items()}


def stratum_summary(
    sub: pd.DataFrame,
    definition: str,
    *,
    qs: Sequence[float] = QUANTILES,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """The reach distribution of one definition over one set of events.

    Args:
        sub: :attr:`AnticipationEvents.events` rows.
        definition: One of :func:`definitions`.
        qs: Quantiles.
        n_boot: Bootstrap resamples (0 = no intervals).
        seed: Bootstrap seed.

    Returns:
        ``n_changes`` (rows), ``n`` (evaluated: a partner at the change),
        ``n_onset`` / ``n_censored``, ``share_censored``, ``share_zero``
        (observed onsets at the change itself, of ``n``), ``outcomes``
        (counts), ``km_reach_m`` and its ``km_reach_m_boot`` (intervals and
        unidentified shares), ``km_time_s``, ``observed_reach_m`` (quantiles
        of the uncensored reaches only: biased low, a diagnostic),
        ``max_reach_m`` (largest value, observed or censored); for ``gap`` the
        onset diagnostics (quantiles over observed onsets) and
        ``share_onset_upstream`` (of observed onsets, upstream of the zone's
        start).
    """
    oc = sub[f"{definition}_outcome"].astype(str).to_numpy()
    ev = oc != "no_partner"
    reach = sub[f"{definition}_reach_m"].to_numpy(dtype=np.float64)[ev]
    tm = sub[f"{definition}_time_s"].to_numpy(dtype=np.float64)[ev]
    cen = sub[f"{definition}_censored"].astype(bool).to_numpy()[ev]
    n = int(ev.sum())
    obs = ~cen
    out: dict[str, Any] = {
        "definition": definition,
        "n_changes": len(sub),
        "n": n,
        "n_onset": int(obs.sum()),
        "n_censored": int(cen.sum()),
        "share_censored": round(float(cen.mean()), 4) if n else None,
        "share_zero": round(float(np.mean(obs & (reach <= 0.0))), 4) if n else None,
        "outcomes": {o: int(c) for o, c in sorted(Counter(oc.tolist()).items())},
    }
    if n == 0:
        return out
    out["km_reach_m"] = _rounded(km_quantiles(reach, cen, qs))
    if n_boot > 0:
        out["km_reach_m_boot"] = km_bootstrap(reach, cen, qs, n_boot=n_boot, seed=seed)
    out["km_time_s"] = _rounded(km_quantiles(tm, cen, qs))
    out["observed_reach_m"] = _plain_quantiles(reach[obs], qs)
    out["max_reach_m"] = round(float(np.nanmax(reach)), 2)
    if definition == PRIMARY_DEFINITION:
        on = ev & (oc == "onset")
        for col in (
            "gap_onset_lag_dist_m",
            "gap_onset_lead_gap_m",
            "gap_onset_lag_gap_m",
            "gap_onset_lead_dv_ms",
            "gap_onset_lag_dv_ms",
        ):
            out[col.removeprefix("gap_")] = _plain_quantiles(
                sub[col].to_numpy(dtype=np.float64)[on], qs
            )
        xr = sub["gap_onset_x_rel_m"].to_numpy(dtype=np.float64)[on]
        out["share_onset_upstream"] = round(float(np.mean(xr < 0.0)), 4) if xr.size else None
    return out


def summarize_anticipation(
    events: pd.DataFrame,
    *,
    by: Sequence[str] = ("zone",),
    defs: Sequence[str] | None = None,
    speed_classes: Sequence[tuple[str, float, float]] = SPEED_CLASSES_MS,
    qs: Sequence[float] = QUANTILES,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, Any]]:
    """Per group, changer-speed class (``all`` and each class) and definition: :func:`stratum_summary`.

    Bootstrap seeds are ``flowstate_core.rng.spawn_seeds(seed, n_rows)``,
    assigned in the rows' fixed order (groups sorted, then ``all`` and the
    classes, then the definitions).
    """
    from flowstate_core.rng import spawn_seeds

    if defs is None:
        defs = [c.removesuffix("_outcome") for c in events.columns if c.endswith("_outcome")]
    keys = list(by)
    plan: list[tuple[dict[str, str], pd.DataFrame, str, str]] = []
    if len(events):
        for group_key, sub in events.groupby(keys, sort=True):
            key_t = group_key if isinstance(group_key, tuple) else (group_key,)
            head = {k: str(v) for k, v in zip(keys, key_t, strict=True)}
            vv = sub["v"].to_numpy(dtype=np.float64)
            parts = [("all", sub)] + [
                (label, sub[(vv >= lo) & (vv < hi)]) for label, lo, hi in speed_classes
            ]
            for label, part in parts:
                for d in defs:
                    plan.append((head, part, label, d))
    seeds = spawn_seeds(seed, len(plan)) if plan else []
    rows: list[dict[str, Any]] = []
    for (head, part, label, d), s in zip(plan, seeds, strict=True):
        rows.append(
            {
                **head,
                "speed_class": label,
                **stratum_summary(part, d, qs=qs, n_boot=n_boot, seed=int(s)),
            }
        )
    return rows


def change_positions(
    events: pd.DataFrame, *, by: Sequence[str] = ("zone",), qs: Sequence[float] = QUANTILES
) -> list[dict[str, Any]]:
    """Where entrants change, past their zone's start [m]: quantiles per group and speed class.

    Every event counts (with or without partners): docs/MERGE_MODEL_BRIEF.md
    §1.5 records that no merge-position distribution had been extracted.
    """
    rows: list[dict[str, Any]] = []
    if len(events) == 0:
        return rows
    keys = list(by)
    for group_key, sub in events.groupby(keys, sort=True):
        key_t = group_key if isinstance(group_key, tuple) else (group_key,)
        head = {k: str(v) for k, v in zip(keys, key_t, strict=True)}
        vv = sub["v"].to_numpy(dtype=np.float64)
        dd = sub["d_change_m"].to_numpy(dtype=np.float64)
        rows.append({**head, "speed_class": "all", "n": len(sub), **_plain_quantiles(dd, qs)})
        for label, lo, hi in SPEED_CLASSES_MS:
            m = (vv >= lo) & (vv < hi)
            rows.append(
                {**head, "speed_class": label, "n": int(m.sum()), **_plain_quantiles(dd[m], qs)}
            )
    return rows


# --- the pre-registered proposal --------------------------------------------------------------


@dataclass(frozen=True)
class Stratum:
    """A set of events the rule may adopt from.

    Attributes:
        name: Label (``primary``, ``fallback``).
        zones: Zone names pooled.
        speed_class: A :data:`SPEED_CLASSES_MS` label, or ``all``.
    """

    name: str
    zones: tuple[str, ...]
    speed_class: str

    def select(self, events: pd.DataFrame) -> pd.DataFrame:
        """The stratum's rows of ``events``."""
        m = events["zone"].astype(str).isin(list(self.zones))
        if self.speed_class != "all":
            m &= events["speed_class"].astype(str) == self.speed_class
        return events[m]

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {"name": self.name, "zones": list(self.zones), "speed_class": self.speed_class}


@dataclass(frozen=True)
class AdoptionRule:
    """The identification checks of docs/MERGE_ANTICIPATION.md §6 (fixed before the run).

    Attributes:
        strata: Tried in order; the first that passes every check is used.
        definition: The definition adopted from.
        min_n: Fewest evaluated events in the stratum.
        max_unidentified_share: Largest share of bootstrap resamples in which
            the median is not identified (0.025: the interval's upper end is
            finite).
        min_ci_width_cap_m: The interval width may not exceed
            ``max(min_ci_width_cap_m, median)``.
        round_to_m: The adopted value is the median rounded to this [m].
        current_value_m: The constant it would replace [m].
    """

    strata: tuple[Stratum, ...]
    definition: str = PRIMARY_DEFINITION
    min_n: int = 100
    max_unidentified_share: float = 0.025
    min_ci_width_cap_m: float = 50.0
    round_to_m: float = 10.0
    current_value_m: float = 120.0

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "strata": [s.to_dict() for s in self.strata],
            "definition": self.definition,
            "min_n": self.min_n,
            "max_unidentified_share": self.max_unidentified_share,
            "ci_width_cap": f"(hi - lo) <= max({self.min_ci_width_cap_m:g} m, median)",
            "round_to_m": self.round_to_m,
            "current_value_m": self.current_value_m,
        }


def propose(
    events: pd.DataFrame,
    rule: AdoptionRule,
    *,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Apply the pre-registered identification checks; the value they propose, or none.

    Each stratum is summarized (:func:`stratum_summary`, its own seed from
    ``spawn_seeds(seed, len(strata))``) and checked: ``n ≥ min_n``; the KM
    median identified; the median identified in all but at most
    ``max_unidentified_share`` of the resamples; the interval no wider than
    ``max(min_ci_width_cap_m, median)``. The first stratum passing all four is
    used and its median, rounded to ``round_to_m``, is the proposed value.
    The proposal is not an adoption: docs/MERGE_ANTICIPATION.md §6 says what
    follows it.

    Returns:
        ``rule``, ``strata`` (each with its numbers and checks),
        ``selected`` (a stratum name or None), ``proposed_value_m`` (or
        None: the constant stays) and ``current_value_m``.
    """
    from flowstate_core.rng import spawn_seeds

    seeds = spawn_seeds(seed, len(rule.strata)) if rule.strata else []
    evaluated: list[dict[str, Any]] = []
    selected: str | None = None
    proposed: float | None = None
    for stratum, s in zip(rule.strata, seeds, strict=True):
        sub = stratum.select(events)
        summ = stratum_summary(sub, rule.definition, qs=(0.5,), n_boot=n_boot, seed=int(s))
        median = (summ.get("km_reach_m") or {}).get("p50")
        boot = (summ.get("km_reach_m_boot") or {}).get("p50") or {}
        lo, hi = (boot.get("ci95") or [None, None])[:2]
        unid = boot.get("unidentified_share")
        width = None if lo is None or hi is None else hi - lo
        checks = {
            "min_n": summ["n"] >= rule.min_n,
            "median_identified": median is not None,
            "median_identified_in_resamples": unid is not None
            and unid <= rule.max_unidentified_share,
            "ci_width": width is not None
            and median is not None
            and width <= max(rule.min_ci_width_cap_m, median) + 1e-9,
        }
        passed = all(checks.values())
        evaluated.append(
            {
                **stratum.to_dict(),
                "n": summ["n"],
                "n_onset": summ.get("n_onset"),
                "share_censored": summ.get("share_censored"),
                "median_m": median,
                "ci95_m": [lo, hi],
                "unidentified_share": unid,
                "checks": checks,
                "passed": passed,
            }
        )
        if passed and selected is None and median is not None:
            selected = stratum.name
            proposed = rule.round_to_m * round(median / rule.round_to_m)
    return {
        "rule": rule.to_dict(),
        "strata": evaluated,
        "selected": selected,
        "proposed_value_m": proposed,
        "current_value_m": rule.current_value_m,
    }
