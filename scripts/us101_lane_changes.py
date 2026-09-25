"""Lane changes against FollowerStopper penetration on the US-101 replica (WP-81).

docs/ROADMAP.md §5 item D2. On the 5-lane US-101 replica, docs/US101_PENETRATION.md
measured a resolved σ_v reduction at 1–10 % FollowerStopper penetration together
with a resolved fuel *increase* (+1.4 to +2.7 %, ``artifacts/us101_penetration_summary.json``)
and offered an untested explanation: neighbours change lanes around a slower AV,
and those changes are acceleration/deceleration events that burn fuel. This
script measures each link of that explanation on the penetration sweep's own
runs (``scripts/us101_penetration_sweep.py``: same cells, seeds and
configuration). The opt-in pipeline stage ``us101_lane_changes``
(``scripts/gcp/pipeline_i24.sh``) runs the sweep and this script on a cloud VM;
nothing here simulates.

Definitions
-----------
**Lane changes.** :func:`calibration.lane_change_gaps.lane_change_gaps` on the
run's trajectories, SUMO lane indices mapped to the band convention
(:func:`calibration.lane_change_gaps.sim_band_lanes`: 1 = leftmost of the
replica's lanes; no auxiliary lanes, no zones), with its debounce
(:func:`calibration.lanechange.held_lanes`: a stay shorter than ``min_dwell_s``
that returns to the lane it came from is not a change) and its guards. A change
is placed and timed at its first sample in the new lane.

**Study span and window.** Rates count the changes made, and the vehicle-km
driven, inside the replica itself — ``x ∈ [x0, x0 + length_m)`` with ``x0 =
meta["corridor"]["x_first_edge_m"]`` (the insertion buffer before it and the
exit buffer after it are excluded) — at ``t ≥`` the configured warm-up, the
window ``validation.metrics.compute_metrics`` measures. Vehicle-km: the
distance between a vehicle's consecutive samples clipped to the span (``x`` is
monotone on a corridor), for sample pairs whose first sample is in the window.

**Classes.** A change is an *AV change* when the changer is AV-tagged
(``meta["av_ids"]``; the trajectories' ``is_av`` flag must agree and is
checked), else a *human change*. For a human change:

* the *origin-lane leader* at one of the changer's samples is the nearest
  vehicle whose front is strictly ahead of the changer's front in the
  changer's debounced lane at that time stamp, within ``leader_range_m``
  bumper to bumper (200 m, ``calibration.lane_change_gaps.DEFAULT_MAX_RANGE_M``);
* a *pass-around* is a human change whose origin-lane leader was an AV at the
  change moment (the last sample in the origin lane) or at any of the
  changer's samples in the origin lane during the ``lookback_s`` (5 s) before
  it — the human left the lane of an AV that was directly ahead of it; a
  *pass-around at change* is the stricter reading, the last sample only;
* a *cut-in* is a human change that is not a pass-around and whose new
  follower in the target lane (``lane_change_gaps``' lag, within 200 m) is an
  AV — the human moved into the gap ahead of an AV;
* every other human change is *other*.

Human-change rates are per human vehicle-km, AV-change rates per AV
vehicle-km, the all-change rate per vehicle-km of every vehicle.

**Counterfactual pass-arounds.** Under common random numbers a seed gives every
cell the same drivers, departures and IDM parameters: the corridor plan draws
the AV tags after them (``microsim.vehicles.build_corridor_plan``, RNG order).
The vehicles that are AVs at penetration p are ordinary drivers in the same
seed's baseline, so the baseline's human changes behind those same vehicles
(the same definition; changers that are AVs at p left out; per the baseline's
vehicle-km of the vehicles that are not AVs at p) are the pass-arounds that
happen without the controller. *Excess pass-arounds* = the level's rate minus
that; the same for cut-ins.

**Fuel.** The runner records per-vehicle totals only:
``meta["fuel_ml_per_vehicle"]`` (HBEFA4, accumulated at every step a vehicle is
on the network — insertion buffer, replica and exit buffer — from departure
to arrival; ``microsim.runner``). No per-vehicle fuel inside the span exists,
so fuel per km is *whole-trip*: a vehicle's fuel over its recorded distance
(the trapezoid of speed over its samples, the denominator ``compute_metrics``
uses for its whole-run ratio, so the all-vehicle class ratio reproduces
``compute_metrics``' ``fuel_ml_per_veh_km``; checked per run). Class ratios are
Σ fuel / Σ distance over humans, AVs or all vehicles; the change against the
seed's baseline F0 splits exactly as ``F − F0 = s_h (f_h − F0) + s_a (f_a − F0)``
with ``s`` a class's share of the distance.

**Fuel against lane changes.** Humans with a whole journey — first sample at
``t ≥`` the warm-up, and arrived (``vehicles.parquet`` ``arrived``; for a run
without that table, last sample before the recording's end) — binned by their
number of lane changes over their whole record (the extent of their fuel):
0, 1, 2+; per bin the count, the mean of the per-vehicle ml/km and the pooled
Σ fuel / Σ km. The relation is associational: a driver who changes lanes is
also more likely to be in the slower traffic that made the change worthwhile.

**Original metrics.** ``compute_metrics`` with the original analysis's
``x_ref`` 400 m and ``span`` (100, 600) m (``scripts/us101_penetration_analyze.py``),
so fuel, σ_v, throughput and waves compare with ``artifacts/us101_penetration_summary.json``.
Those two constants are trajectory (run) coordinates, where ``x ∈ [0, 640)``
is the insertion buffer: the throughput and travel-time span of the original
analysis lie upstream of the replica (``x0`` = 640 m), while its σ_v, fuel and
waves cover the whole recorded road. The span-based rates here do not depend
on either constant, and ``site_metrics`` repeats the two span-dependent ones on
the replica itself (throughput at its midpoint, travel time over its length,
as ``scripts/m3_us101_validate.py`` measures them).

Usage::

    uv run --no-sync python scripts/us101_lane_changes.py --sweep runs/us101_penetration --procs 6
    uv run --no-sync python scripts/us101_lane_changes.py --sweep runs/us101_penetration --analyze-only
    uv run --no-sync python scripts/us101_lane_changes.py --run-dir <run dir> [<run dir> ...]
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
from numpy.typing import NDArray

REPO = Path(__file__).resolve().parents[1]
SWEEP_ROOT = REPO / "runs" / "us101_penetration"
ARTIFACT = REPO / "artifacts" / "us101_lane_change_penetration.json"

PER_RUN_FILE: Final[str] = "lane_changes.json"
PER_RUN_VERSION: Final[int] = 1
SCHEMA_VERSION: Final[int] = 1

DEFAULT_LOOKBACK_S: Final[float] = 5.0
"""Lookback of the pass-around test [s]: the changer's samples in its origin
lane during this long before the change are searched for an AV leader."""

DEFAULT_LEADER_RANGE_M: Final[float] = 200.0
"""Origin-lane leader search range [m], bumper to bumper
(``calibration.lane_change_gaps.DEFAULT_MAX_RANGE_M``, the range of the
target-lane lead and lag the detector records)."""

DEFAULT_LENGTH_M: Final[float] = 5.0
"""Vehicle length [m] of every generated passenger vType
(``microsim.vehicles.VEHICLE_LENGTH_M``; a test pins the equality). Heavy
vehicles take ``fleet.heavy.length_m`` from the run's config."""

CORRIDOR_INSERTION_BUFFER_M: Final[float] = 2000.0
"""``microsim.runner.CORRIDOR_INSERTION_BUFFER_M``: the insertion buffer of a
generated corridor is ``min(this, length_m)``; used only for runs whose
``meta.json`` predates the ``corridor`` block."""

ORIGINAL_X_REF_M: Final[float] = 400.0
ORIGINAL_SPAN_M: Final[tuple[float, float]] = (100.0, 600.0)
"""``scripts/us101_penetration_analyze.py``' ``X_REF`` and ``SPAN`` (run coordinates)."""

SITE_X_REF_FRACTION: Final[float] = 0.5
"""Throughput cross-section on the replica as a fraction of its length: 320 m of
640, ``scripts/m3_us101_validate.py``' ``ENTRY_BUFFER_M + SECTIONS_M[1]``."""

ORIGINAL_FIELDS: Final[tuple[str, ...]] = (
    "throughput_veh_h",
    "sigma_v_temporal_ms",
    "fuel_ml_per_veh_km",
    "mean_tt_s",
    "wave_count",
)

CHANGE_BINS: Final[tuple[str, ...]] = ("0", "1", "2+")
"""Bins of a human's whole-record lane-change count for the fuel relation."""

HUMAN_CLASSES: Final[tuple[str, ...]] = ("pass_around", "cut_in", "other")

RESOLVED_CHECKS: Final[dict[str, tuple[str, str]]] = {
    "a_fuel_increase_reproduces": (
        "orig_fuel_ml_per_veh_km",
        "the fuel increase of docs/US101_PENETRATION.md reproduces on these runs "
        "(compute_metrics' fuel_ml_per_veh_km, original definition)",
    ),
    "b_humans_change_more": (
        "lc_per_veh_km_human",
        "humans change lanes more per human vehicle-km in the replica",
    ),
    "c_excess_changes_behind_avs": (
        "excess_pass_arounds_per_human_veh_km",
        "more humans leave the lane of an AV directly ahead than leave the lane "
        "of the same vehicle in the seed's baseline",
    ),
    "d_humans_burn_more": (
        "fuel_ml_per_veh_km_human",
        "the humans' own fuel per km rises (whole trip)",
    ),
    "e_changes_cost_fuel": (
        "fuel_ml_per_km_human_changed_minus_unchanged",
        "within a run, humans who changed lanes burn more per km than humans who did not",
    ),
}
"""The pre-registered checks (docs/US101_PENETRATION.md, dated addendum): each
holds at a level when the named statistic's 95 % CI over seeds lies above 0
(for a, b, c, d the paired change against the seed's baseline; for e the
level's own within-run difference)."""


# ---------------------------------------------------------------------------
# Frame helpers (pure)
# ---------------------------------------------------------------------------


def _sorted_arrays(df: pd.DataFrame) -> dict[str, Any]:
    """The frame's columns sorted by (vehicle, t), as ``lane_change_gaps`` sorts them.

    Vehicle codes come from ``pd.factorize(veh_id, sort=False)`` and the order
    from ``np.lexsort((t, codes))`` — the detector's own ordering, so row
    indices here and the detector's agree.
    """
    codes, labels = pd.factorize(df["veh_id"], sort=False)
    codes = np.asarray(codes, dtype=np.int64)
    t_all = df["t"].to_numpy(dtype=np.float64)
    order = np.lexsort((t_all, codes))
    out: dict[str, Any] = {
        "veh": codes[order],
        "labels": np.asarray(labels, dtype=object),
        "t": t_all[order],
        "x": df["x"].to_numpy(dtype=np.float64)[order],
        "v": df["v"].to_numpy(dtype=np.float64)[order],
        "lane": df["lane"].to_numpy(dtype=np.int64)[order],
        "length": df["length"].to_numpy(dtype=np.float64)[order],
    }
    return out


def origin_leaders(
    t_idx: NDArray[np.int64],
    lane: NDArray[np.int64],
    x: NDArray[np.float64],
    length: NDArray[np.float64],
    max_range_m: float,
) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
    """For every row, the row of the nearest vehicle strictly ahead in its lane.

    Rows are matched on the same time slot ``t_idx`` and the same lane; the
    leader is the nearest vehicle whose front is strictly ahead of the row's
    front, kept when the bumper gap ``x_lead − length_lead − x`` is at most
    ``max_range_m``.

    Args:
        t_idx: Integer time slot per row (``rint(t / dt)``).
        lane: Lane per row (debounced).
        x: Front-bumper position per row [m].
        length: Vehicle length per row [m].
        max_range_m: Largest bumper gap that still counts [m].

    Returns:
        ``(leader_row, gap_m)``: the leader's row index (``-1`` for none) and
        the bumper gap (NaN for none).
    """
    n = x.size
    if n == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float64)
    lane_lo = int(lane.min())
    n_lane = int(lane.max()) - lane_lo + 1
    key = t_idx * n_lane + (lane - lane_lo)
    by = np.lexsort((x, key))
    key_s = key[by]
    x_lo = float(x.min())
    scale = float(x.max()) - x_lo + 1.0
    z = key.astype(np.float64) * scale + (x - x_lo)
    z_s = z[by]
    pos_r = np.searchsorted(z_s, z, side="right")  # first row strictly ahead (same key or next)
    bhi = np.searchsorted(key_s, key, side="right")
    ok = pos_r < bhi
    lead = np.where(ok, by[np.minimum(pos_r, n - 1)], -1)
    safe = np.maximum(lead, 0)
    gap = np.where(ok, x[safe] - length[safe] - x, np.nan)
    ok &= gap <= max_range_m
    return np.where(ok, lead, -1).astype(np.int64), np.where(ok, gap, np.nan)


def span_distance_m(
    veh: NDArray[np.int64],
    t: NDArray[np.float64],
    x: NDArray[np.float64],
    n_codes: int,
    *,
    t_lo: float,
    x_lo: float,
    x_hi: float,
) -> NDArray[np.float64]:
    """Per-vehicle distance driven inside ``[x_lo, x_hi)`` at ``t ≥ t_lo`` [m].

    Consecutive samples of one vehicle (rows sorted by vehicle, then t) cover
    ``[x_i, x_{i+1}]``; the part inside the span is counted when the pair's
    first sample is in the window.
    """
    if veh.size < 2:
        return np.zeros(n_codes, dtype=np.float64)
    same = veh[1:] == veh[:-1]
    seg = np.minimum(x[1:], x_hi) - np.maximum(x[:-1], x_lo)
    seg = np.where(same & (t[:-1] >= t_lo), np.clip(seg, 0.0, None), 0.0)
    return np.bincount(veh[:-1], weights=seg, minlength=n_codes).astype(np.float64)


def trip_distance_m(
    veh: NDArray[np.int64], t: NDArray[np.float64], v: NDArray[np.float64], n_codes: int
) -> NDArray[np.float64]:
    """Per-vehicle recorded distance [m]: the trapezoid of speed over its samples.

    The same integral ``compute_metrics`` sums into its whole-run VMT, the
    denominator of its whole-run fuel ratio.
    """
    if veh.size < 2:
        return np.zeros(n_codes, dtype=np.float64)
    same = veh[1:] == veh[:-1]
    seg = np.where(same, 0.5 * (v[:-1] + v[1:]) * np.diff(t), 0.0)
    return np.bincount(veh[:-1], weights=seg, minlength=n_codes).astype(np.float64)


def classify_changes(
    records: pd.DataFrame,
    arrays: Mapping[str, Any],
    lane_held: NDArray[np.int64],
    contig: NDArray[np.bool_],
    av_code: NDArray[np.bool_],
    *,
    dt_s: float,
    lookback_s: float,
    leader_range_m: float,
) -> pd.DataFrame:
    """Add the changer's class and the origin-lane leaders to detector records.

    Args:
        records: ``lane_change_gaps`` records of the frame ``arrays`` came from.
        arrays: :func:`_sorted_arrays` of that frame (band lanes).
        lane_held: The debounced lanes of those rows (``held_lanes`` with the
            detector's ``dt_s``, ``max_gap_s`` and ``min_dwell_s``).
        contig: Its contiguity mask (length ``n − 1``).
        av_code: AV flag per vehicle code.
        dt_s: Sampling interval [s].
        lookback_s: Pass-around lookback [s].
        leader_range_m: Origin-lane leader range [m].

    Returns:
        A copy of ``records`` with ``changer_is_av``, ``origin_leader_id``,
        ``origin_leader_gap_m``, ``origin_leader_is_av`` (at the change moment),
        ``origin_leader_ids_lookback`` (the distinct origin-lane leaders over the
        lookback, a list), ``pass_around``, ``pass_around_at_change``,
        ``new_follower_is_av``, ``cut_in`` and ``klass`` (``av``,
        ``pass_around``, ``cut_in`` or ``other``).

    Raises:
        RuntimeError: When a record does not sit on a held-lane transition of
            the frame (the detector and this function disagree).
    """
    out = records.copy()
    m = len(out)
    veh = arrays["veh"]
    t = arrays["t"]
    labels = arrays["labels"]
    if m == 0:
        for col in ("origin_leader_id", "origin_leader_ids_lookback", "klass"):
            out[col] = pd.Series(dtype=object)
        out["origin_leader_gap_m"] = pd.Series(dtype=np.float64)
        for col in (
            "changer_is_av",
            "origin_leader_is_av",
            "pass_around",
            "pass_around_at_change",
            "new_follower_is_av",
            "cut_in",
        ):
            out[col] = pd.Series(dtype=bool)
        return out

    t_idx = np.rint(t / dt_s).astype(np.int64)
    span_t = int(t_idx.max()) + 1
    comp = veh * span_t + t_idx  # sorted ascending: rows are sorted by (vehicle, t)
    code_r = pd.Index(labels).get_indexer(out["veh_id"].to_numpy(dtype=object))
    comp_r = code_r.astype(np.int64) * span_t + np.rint(
        out["t"].to_numpy(dtype=np.float64) / dt_s
    ).astype(np.int64)
    j1 = np.searchsorted(comp, comp_r)
    j1c = np.minimum(j1, comp.size - 1)
    from_lane = out["from_lane"].to_numpy(dtype=np.int64)
    to_lane = out["to_lane"].to_numpy(dtype=np.int64)
    j0 = np.maximum(j1c - 1, 0)
    consistent = (
        (code_r >= 0)
        & (j1 < comp.size)
        & (comp[j1c] == comp_r)
        & (j1c >= 1)
        & (veh[j0] == veh[j1c])
        & (lane_held[j0] == from_lane)
        & (lane_held[j1c] == to_lane)
    )
    if not bool(np.all(consistent)):
        raise RuntimeError(
            f"{int(np.sum(~consistent))} detector records do not sit on a held-lane "
            "transition of the frame: the detector's and this debounce's parameters differ"
        )

    lead_row, lead_gap = origin_leaders(
        t_idx, lane_held, arrays["x"], arrays["length"], leader_range_m
    )
    lead_is_av = np.zeros(lead_row.size, dtype=bool)
    has = lead_row >= 0
    lead_is_av[has] = av_code[veh[lead_row[has]]]

    changer_av = av_code[veh[j1c]]
    k_max = math.floor(lookback_s / dt_s + 1e-9)
    alive = np.ones(m, dtype=bool)
    pass_lb = np.zeros(m, dtype=bool)
    pair_change: list[NDArray[np.int64]] = []
    pair_leader: list[NDArray[np.int64]] = []
    for k in range(k_max + 1):
        r = j0 - k
        valid = r >= 0
        rc = np.where(valid, r, 0)
        alive &= valid & (veh[rc] == veh[j0]) & (lane_held[rc] == from_lane)
        alive &= t[j0] - t[rc] <= lookback_s + 1e-9
        if k > 0:
            alive &= contig[np.minimum(rc, contig.size - 1)] if contig.size else False
        lr = lead_row[rc]
        seen = alive & (lr >= 0)
        pass_lb |= seen & lead_is_av[rc]
        idx = np.flatnonzero(seen)
        pair_change.append(idx)
        pair_leader.append(veh[lr[idx]])

    at_row = lead_row[j0]
    at_ok = at_row >= 0
    at_id = np.full(m, None, dtype=object)
    at_id[at_ok] = labels[veh[at_row[at_ok]]]
    at_is_av = np.zeros(m, dtype=bool)
    at_is_av[at_ok] = av_code[veh[at_row[at_ok]]]

    leaders_lb: list[list[str]] = [[] for _ in range(m)]
    if pair_change:
        pc = np.concatenate(pair_change)
        pl = np.concatenate(pair_leader)
        if pc.size:
            uniq = np.unique(np.stack([pc, pl], axis=1), axis=0)
            for ci, li in uniq:
                leaders_lb[int(ci)].append(str(labels[int(li)]))

    lag_ids = out["lag_id"].to_numpy(dtype=object)
    lag_code = pd.Index(labels).get_indexer(
        np.array([i if isinstance(i, str) else "" for i in lag_ids], dtype=object)
    )
    new_follower_av = np.zeros(m, dtype=bool)
    ok_lag = np.array([isinstance(i, str) for i in lag_ids], dtype=bool) & (lag_code >= 0)
    new_follower_av[ok_lag] = av_code[lag_code[ok_lag]]

    human = ~changer_av
    pass_around = human & pass_lb
    cut_in = human & ~pass_around & new_follower_av
    klass = np.where(
        changer_av, "av", np.where(pass_around, "pass_around", np.where(cut_in, "cut_in", "other"))
    ).astype(object)

    out["changer_is_av"] = changer_av
    out["origin_leader_id"] = at_id
    out["origin_leader_gap_m"] = lead_gap[j0]
    out["origin_leader_is_av"] = at_is_av
    out["origin_leader_ids_lookback"] = leaders_lb
    out["pass_around"] = pass_around
    out["pass_around_at_change"] = human & at_is_av
    out["new_follower_is_av"] = new_follower_av
    out["cut_in"] = cut_in
    out["klass"] = klass
    return out


def _bins_of(n_changes: NDArray[np.int64]) -> NDArray[np.object_]:
    """Bin label per vehicle: ``0``, ``1`` or ``2+``."""
    return np.where(n_changes <= 0, "0", np.where(n_changes == 1, "1", "2+")).astype(object)


def fuel_by_changes(per_vehicle: pd.DataFrame) -> dict[str, Any]:
    """Human fuel per km by the number of lane changes (whole journeys only).

    Args:
        per_vehicle: One row per vehicle with ``is_av``, ``whole_journey``,
            ``fuel_ml``, ``trip_km`` and ``n_changes`` (whole record).

    Returns:
        ``{bin: {n, mean_ml_per_km, pooled_ml_per_km}}`` for the bins ``0``,
        ``1``, ``2+`` and ``changed`` (1 or more), plus
        ``changed_minus_unchanged_ml_per_km`` (difference of the per-vehicle
        means; None when a side is empty) and ``n_excluded`` counts.
    """
    use = per_vehicle[
        (~per_vehicle["is_av"])
        & per_vehicle["whole_journey"]
        & per_vehicle["fuel_ml"].notna()
        & (per_vehicle["trip_km"] > 0.0)
    ]
    ratio = (use["fuel_ml"] / use["trip_km"]).to_numpy(dtype=np.float64)
    n_ch = use["n_changes"].to_numpy(dtype=np.int64)
    fuel = use["fuel_ml"].to_numpy(dtype=np.float64)
    km = use["trip_km"].to_numpy(dtype=np.float64)
    labels = _bins_of(n_ch)
    out: dict[str, Any] = {}
    groups: dict[str, NDArray[np.bool_]] = {b: labels == b for b in CHANGE_BINS}
    groups["changed"] = n_ch >= 1
    for name, sel in groups.items():
        n = int(np.sum(sel))
        out[name] = {
            "n": n,
            "mean_ml_per_km": float(np.mean(ratio[sel])) if n else None,
            "pooled_ml_per_km": float(np.sum(fuel[sel]) / np.sum(km[sel])) if n else None,
        }
    a, b = out["changed"]["mean_ml_per_km"], out["0"]["mean_ml_per_km"]
    out["changed_minus_unchanged_ml_per_km"] = (a - b) if a is not None and b is not None else None
    humans = per_vehicle[~per_vehicle["is_av"]]
    out["n_humans"] = len(humans)
    out["n_humans_not_whole_journey"] = int(np.sum(~humans["whole_journey"]))
    out["n_humans_without_fuel"] = int(humans["fuel_ml"].isna().sum())
    return out


def _rate(num: float, den_km: float) -> float | None:
    return float(num / den_km) if den_km > 0.0 else None


def _quantiles(values: Iterable[float]) -> dict[str, float] | None:
    arr = np.asarray([v for v in values if v == v], dtype=np.float64)
    if arr.size == 0:
        return None
    qs = np.quantile(arr, (0.1, 0.25, 0.5, 0.75, 0.9))
    return {f"p{p}": round(float(q), 3) for p, q in zip((10, 25, 50, 75, 90), qs, strict=True)}


def analyse_frame(
    traj: pd.DataFrame,
    *,
    av_ids: Iterable[str],
    fuel_ml_per_vehicle: Mapping[str, float],
    n_lanes: int,
    span_m: tuple[float, float],
    warmup_s: float,
    arrived: Mapping[str, bool] | None = None,
    lookback_s: float = DEFAULT_LOOKBACK_S,
    leader_range_m: float = DEFAULT_LEADER_RANGE_M,
    min_dwell_s: float | None = None,
    keep_neighbours: bool = False,
) -> dict[str, Any]:
    """Lane-change counts, classes and fuel of one run, from its frames.

    Args:
        traj: Trajectory rows ``t, veh_id, x, lane (SUMO index), v`` and
            optionally ``length``, ``is_av``.
        av_ids: The AV-tagged vehicle ids (``meta["av_ids"]``).
        fuel_ml_per_vehicle: Whole-trip fuel per vehicle [ml].
        n_lanes: Lanes of the corridor (constant along it).
        span_m: The study span ``(x_lo, x_hi)`` [m, run coordinates].
        warmup_s: Measurement window start [s].
        arrived: ``veh_id → arrived`` (``vehicles.parquet``); None derives it
            from the trajectories (last sample before the recording's end).
        lookback_s: Pass-around lookback [s].
        leader_range_m: Origin-lane leader range [m].
        min_dwell_s: The detector's dwell; None keeps its default.
        keep_neighbours: Also return, for every human change in the span and
            window, the changer, its lookback origin-lane leaders and its new
            follower, plus every vehicle's span distance (the baseline side of
            the counterfactual pass-arounds).

    Returns:
        A JSON-ready dict (see the module docstring); ``records`` (the
        classified change records) is returned under the key ``_records``.

    Raises:
        ValueError: On an empty frame, or AV flags that disagree with ``av_ids``.
    """
    from calibration.lane_change_gaps import lane_change_gaps, sim_band_lanes
    from calibration.lanechange import DEFAULT_MIN_DWELL_S, held_lanes

    if traj.empty:
        raise ValueError("empty trajectory frame")
    dwell = DEFAULT_MIN_DWELL_S if min_dwell_s is None else float(min_dwell_s)
    frame = traj[["t", "veh_id", "x", "lane", "v"]].copy()
    frame["veh_id"] = frame["veh_id"].astype(str)
    frame["length"] = (
        traj["length"].to_numpy(dtype=np.float64) if "length" in traj.columns else DEFAULT_LENGTH_M
    )
    frame = sim_band_lanes(frame, [0.0], [int(n_lanes)])
    av_set = {str(a) for a in av_ids}

    recorded_av = (
        set(frame.loc[traj["is_av"].to_numpy(dtype=bool), "veh_id"])
        if "is_av" in traj.columns
        else None
    )
    recorded_ids = set(frame["veh_id"].unique())
    if recorded_av is not None and recorded_av != (av_set & recorded_ids):
        raise ValueError(
            "trajectories' is_av disagrees with av_ids: "
            f"{len(recorded_av ^ (av_set & recorded_ids))} vehicles differ"
        )

    gaps = lane_change_gaps(frame, [], mainline_lanes=range(1, int(n_lanes) + 1), min_dwell_s=dwell)
    dt_s = float(gaps.dt_s)
    arrays = _sorted_arrays(frame)
    veh, t, x, v = arrays["veh"], arrays["t"], arrays["x"], arrays["v"]
    labels = arrays["labels"]
    n_codes = labels.size
    lane_held, contig = held_lanes(
        t,
        veh,
        arrays["lane"],
        dt_s=dt_s,
        max_gap_s=float(gaps.parameters["max_gap_s"]),
        min_dwell_s=dwell,
    )
    av_code = np.array([str(lbl) in av_set for lbl in labels], dtype=bool)
    rec = classify_changes(
        gaps.records,
        arrays,
        lane_held,
        contig,
        av_code,
        dt_s=dt_s,
        lookback_s=lookback_s,
        leader_range_m=leader_range_m,
    )

    x_lo, x_hi = float(span_m[0]), float(span_m[1])
    t_end = float(t.max())
    in_sw = (
        (rec["x"].to_numpy(dtype=np.float64) >= x_lo)
        & (rec["x"].to_numpy(dtype=np.float64) < x_hi)
        & (rec["t"].to_numpy(dtype=np.float64) >= warmup_s)
    )
    sw = rec[in_sw]
    span_m_per_veh = span_distance_m(veh, t, x, n_codes, t_lo=warmup_s, x_lo=x_lo, x_hi=x_hi)
    km_all = float(span_m_per_veh.sum()) / 1000.0
    km_av = float(span_m_per_veh[av_code].sum()) / 1000.0
    km_human = km_all - km_av
    n_by = {k: int(np.sum(sw["klass"] == k)) for k in ("av", *HUMAN_CLASSES)}
    n_human = sum(n_by[k] for k in HUMAN_CLASSES)
    n_pa_at = int(np.sum(sw["pass_around_at_change"].to_numpy(dtype=bool)))
    counts = {
        "n_changes": len(sw),
        "n_av": n_by["av"],
        "n_human": n_human,
        "n_pass_around": n_by["pass_around"],
        "n_pass_around_at_change": n_pa_at,
        "n_cut_in": n_by["cut_in"],
        "n_other_human": n_by["other"],
        "n_unconfirmed": int(np.sum(~sw["confirmed"].to_numpy(dtype=bool))) if len(sw) else 0,
    }
    rates = {
        "lc_per_veh_km_all": _rate(len(sw), km_all),
        "lc_per_veh_km_human": _rate(n_human, km_human),
        "lc_per_veh_km_av": _rate(n_by["av"], km_av),
        "pass_arounds_per_human_veh_km": _rate(n_by["pass_around"], km_human),
        "pass_arounds_at_change_per_human_veh_km": _rate(n_pa_at, km_human),
        "cut_ins_per_human_veh_km": _rate(n_by["cut_in"], km_human),
        "pass_around_share_of_human_changes": (n_by["pass_around"] / n_human if n_human else None),
    }

    # --- fuel ------------------------------------------------------------
    trip_m = trip_distance_m(veh, t, v, n_codes)
    first_t = np.full(n_codes, np.inf)
    last_t = np.full(n_codes, -np.inf)
    np.minimum.at(first_t, veh, t)
    np.maximum.at(last_t, veh, t)
    ids = [str(lbl) for lbl in labels]
    fuel = np.array([fuel_ml_per_vehicle.get(i, np.nan) for i in ids], dtype=np.float64)
    if arrived is None:
        arr = last_t < t_end - 1.5 * dt_s
        arrival_source = "trajectories (last sample before the recording's end)"
    else:
        arr = np.array([bool(arrived.get(i, False)) for i in ids], dtype=bool)
        arrival_source = "vehicles.parquet"
    per_veh_changes = rec.groupby("veh_id").size() if len(rec) else pd.Series(dtype=np.int64)
    n_changes = np.array([int(per_veh_changes.get(i, 0)) for i in ids], dtype=np.int64)
    per_vehicle = pd.DataFrame(
        {
            "veh_id": ids,
            "is_av": av_code,
            "whole_journey": (first_t >= warmup_s) & arr,
            "fuel_ml": fuel,
            "trip_km": trip_m / 1000.0,
            "n_changes": n_changes,
        }
    )
    has_fuel = np.isfinite(fuel)

    def _class(sel: NDArray[np.bool_]) -> dict[str, float | None]:
        s = sel & has_fuel
        ml = float(np.sum(fuel[s]))
        km = float(np.sum(trip_m[s])) / 1000.0
        return {"ml": ml, "km": km, "ml_per_km": _rate(ml, km)}

    fuel_classes = {"all": _class(np.ones(n_codes, bool)), "human": _class(~av_code)}
    fuel_classes["av"] = _class(av_code)
    fbc = fuel_by_changes(per_vehicle)

    scalars: dict[str, float | None] = dict(rates)
    scalars["fuel_ml_per_veh_km_all"] = fuel_classes["all"]["ml_per_km"]
    scalars["fuel_ml_per_veh_km_human"] = fuel_classes["human"]["ml_per_km"]
    scalars["fuel_ml_per_veh_km_av"] = fuel_classes["av"]["ml_per_km"]
    scalars["fuel_ml_per_km_human_0_changes"] = fbc["0"]["mean_ml_per_km"]
    scalars["fuel_ml_per_km_human_1_change"] = fbc["1"]["mean_ml_per_km"]
    scalars["fuel_ml_per_km_human_2plus_changes"] = fbc["2+"]["mean_ml_per_km"]
    scalars["fuel_ml_per_km_human_changed_minus_unchanged"] = fbc[
        "changed_minus_unchanged_ml_per_km"
    ]

    pa = sw[sw["pass_around"].to_numpy(dtype=bool)]
    result: dict[str, Any] = {
        "study_span_m": [x_lo, x_hi],
        "window_s": [float(warmup_s), t_end],
        "dt_s": dt_s,
        "counts": counts,
        "vkt_km": {"all": km_all, "human": km_human, "av": km_av},
        "rates": rates,
        "fuel": fuel_classes,
        "fuel_by_changes": fbc,
        "scalars": scalars,
        "pass_around_origin_leader_gap_m": _quantiles(
            pa["origin_leader_gap_m"].to_numpy(dtype=np.float64)
        ),
        "detector_counts": dict(gaps.counts),
        "arrival_source": arrival_source,
        "av": {
            "n_av_ids": len(av_set),
            "n_av_recorded": int(np.sum(av_code)),
            "n_vehicles_recorded": int(n_codes),
            "is_av_checked": recorded_av is not None,
        },
        "n_fuel_without_trajectory": len(set(fuel_ml_per_vehicle) - set(ids)),
        "n_trajectory_without_fuel": int(np.sum(~has_fuel)),
        "_records": rec,
    }
    if keep_neighbours:
        humans_sw = sw[~sw["changer_is_av"].to_numpy(dtype=bool)]
        result["neighbours"] = {
            "changes": [
                [str(c), list(ls), (f if isinstance(f, str) else None)]
                for c, ls, f in zip(
                    humans_sw["veh_id"],
                    humans_sw["origin_leader_ids_lookback"],
                    humans_sw["lag_id"],
                    strict=True,
                )
            ],
            "span_m_per_vehicle": {
                ids[i]: round(float(span_m_per_veh[i]), 3)
                for i in range(n_codes)
                if span_m_per_veh[i] > 0.0
            },
        }
    return result


# ---------------------------------------------------------------------------
# One run on disk
# ---------------------------------------------------------------------------


def _run_geometry(meta: Mapping[str, Any]) -> tuple[int, tuple[float, float], float]:
    """``(n_lanes, study span, warm-up)`` of a generated-corridor run."""
    cfg = meta["config"]
    net = cfg["network"]
    if net.get("kind") != "corridor":
        raise ValueError(
            f"only generated corridors (network.kind 'corridor') are supported, got {net.get('kind')!r}"
        )
    length = float(net["length_m"])
    corridor = meta.get("corridor") or {}
    x0 = corridor.get("x_first_edge_m")
    if x0 is None:
        x0 = min(CORRIDOR_INSERTION_BUFFER_M, length)
    warm = float((cfg.get("sim") or {}).get("warmup_s") or 0.0)
    return int(net["lanes"]), (float(x0), float(x0) + length), warm


def analyse_run(
    run_dir: Path,
    *,
    lookback_s: float = DEFAULT_LOOKBACK_S,
    leader_range_m: float = DEFAULT_LEADER_RANGE_M,
    min_dwell_s: float | None = None,
    with_original_metrics: bool = True,
    write: bool = True,
) -> dict[str, Any]:
    """Analyse one run directory and (by default) write its ``lane_changes.json``.

    Reads ``trajectories.parquet`` and ``vehicles.parquet`` through open file
    objects (``microsim.demand_adapter.read_trajectories``: a bare path breaks
    once libsumo's libarrow is loaded) and ``meta.json``.
    """
    from validation.metrics import compute_metrics

    meta = json.loads((run_dir / "meta.json").read_text())
    n_lanes, span, warm = _run_geometry(meta)
    cols = ["t", "veh_id", "x", "lane", "v", "is_av", "is_heavy"]
    with open(run_dir / "trajectories.parquet", "rb") as f:
        traj = pd.read_parquet(f, columns=cols)
    heavy_len = ((meta["config"].get("fleet") or {}).get("heavy") or {}).get("length_m")
    traj["length"] = DEFAULT_LENGTH_M
    if heavy_len is not None:
        traj.loc[traj["is_heavy"].to_numpy(dtype=bool), "length"] = float(heavy_len)
    arrived: dict[str, bool] | None
    try:
        from validation.vehicles import read_vehicles

        vt = read_vehicles(run_dir, columns=["veh_id", "arrived"])
        arrived = {str(i): bool(a) for i, a in zip(vt["veh_id"], vt["arrived"], strict=True)}
    except FileNotFoundError:
        arrived = None
    av_ids = [str(a) for a in meta.get("av_ids", [])]
    penetration = float(meta["config"]["av"]["penetration"])
    res = analyse_frame(
        traj,
        av_ids=av_ids,
        fuel_ml_per_vehicle={str(k): float(v) for k, v in meta["fuel_ml_per_vehicle"].items()},
        n_lanes=n_lanes,
        span_m=span,
        warmup_s=warm,
        arrived=arrived,
        lookback_s=lookback_s,
        leader_range_m=leader_range_m,
        min_dwell_s=min_dwell_s,
        keep_neighbours=not av_ids,
    )
    res.pop("_records")
    out: dict[str, Any] = {
        "version": PER_RUN_VERSION,
        "run_dir": str(run_dir.relative_to(REPO)) if run_dir.is_relative_to(REPO) else str(run_dir),
        "config_hash": meta.get("config_hash"),
        "seed": meta.get("seed"),
        "penetration": penetration,
        "compliance": float(meta["config"]["av"]["compliance"]),
        "controller": meta["config"]["av"].get("controller"),
        "parameters": {
            "lookback_s": lookback_s,
            "leader_range_m": leader_range_m,
            "min_dwell_s": min_dwell_s,
            "n_lanes": n_lanes,
        },
        "av_ids": av_ids,
        "n_complied": len(meta.get("complied_ids", [])),
        **res,
    }
    if with_original_metrics:
        m = compute_metrics(
            run_dir,
            x_ref=ORIGINAL_X_REF_M,
            span=ORIGINAL_SPAN_M,
            trajectories=traj[["t", "veh_id", "x", "v"]],
        )
        orig = {fld: getattr(m, fld) for fld in ORIGINAL_FIELDS}
        out["original_metrics"] = {
            k: (float(val) if val == val else None) for k, val in orig.items()
        }
        for k, val in out["original_metrics"].items():
            out["scalars"][f"orig_{k}"] = val
        cm = out["original_metrics"]["fuel_ml_per_veh_km"]
        mine = out["fuel"]["all"]["ml_per_km"]
        out["fuel_ratio_matches_compute_metrics"] = (
            cm is not None and mine is not None and abs(cm - mine) <= 1e-9 * max(abs(cm), 1.0)
        )
        # The same two span-dependent metrics on the replica itself (scripts/m3_us101_validate.py's
        # reference: its midpoint and its whole length); σ_v, fuel and waves do not depend on them.
        x_mid = span[0] + SITE_X_REF_FRACTION * (span[1] - span[0])
        ms = compute_metrics(
            run_dir, x_ref=x_mid, span=span, trajectories=traj[["t", "veh_id", "x", "v"]]
        )
        out["site_metrics"] = {
            "x_ref_m": x_mid,
            "span_m": list(span),
            **{
                k: (float(val) if val == val else None)
                for k, val in (
                    ("throughput_veh_h", ms.throughput_veh_h),
                    ("mean_tt_s", ms.mean_tt_s),
                )
            },
        }
        out["scalars"]["site_throughput_veh_h"] = out["site_metrics"]["throughput_veh_h"]
        out["scalars"]["site_mean_tt_s"] = out["site_metrics"]["mean_tt_s"]
    if write:
        tmp = run_dir / (PER_RUN_FILE + ".part")
        tmp.write_text(json.dumps(_json_safe(out), indent=1, allow_nan=False))
        tmp.replace(run_dir / PER_RUN_FILE)
    return out


def _json_safe(obj: Any) -> Any:
    """NaN/inf → None, numpy scalars → Python, recursively."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


# ---------------------------------------------------------------------------
# Aggregation over the sweep (paired by seed)
# ---------------------------------------------------------------------------


def _t_half(arr: NDArray[np.float64]) -> float:
    from scipy import stats

    n = arr.size
    if n < 2:
        return float("nan")
    return float(stats.t.ppf(0.975, n - 1) * arr.std(ddof=1) / math.sqrt(n))


def ci95(values: Sequence[float | None]) -> dict[str, Any] | None:
    """Mean and 95 % t-interval over the non-null values (None when empty)."""
    arr = np.asarray([v for v in values if v is not None and v == v], dtype=np.float64)
    if arr.size == 0:
        return None
    mean = float(arr.mean())
    half = _t_half(arr)
    return {
        "mean": mean,
        "lo95": mean - half if half == half else None,
        "hi95": mean + half if half == half else None,
        "n": int(arr.size),
    }


def paired(
    level: Mapping[int, float | None], base: Mapping[int, float | None]
) -> dict[str, Any] | None:
    """Paired-by-seed change (level − baseline): mean, 95 % t-CI, % of baseline, resolved."""
    pairs: list[tuple[float, float]] = []
    for s, lv in level.items():
        bv = base.get(s)
        if lv is not None and bv is not None and lv == lv and bv == bv:
            pairs.append((float(lv), float(bv)))
    if len(pairs) < 2:
        return None
    d = np.asarray([lv - bv for lv, bv in pairs], dtype=np.float64)
    mean = float(d.mean())
    half = _t_half(d)
    base_mean = float(np.mean([bv for _, bv in pairs]))
    return {
        "mean": mean,
        "lo95": mean - half,
        "hi95": mean + half,
        "n": int(d.size),
        "pct_of_baseline": (100.0 * mean / base_mean) if base_mean else None,
        "resolved": bool((mean - half) > 0 or (mean + half) < 0),
    }


def counterfactual(level_run: Mapping[str, Any], base_run: Mapping[str, Any]) -> dict[str, Any]:
    """Pass-arounds and cut-ins behind the level's AVs, in the seed's baseline.

    Args:
        level_run: The level's per-run record (``av_ids``, ``rates``).
        base_run: The same seed's baseline record (``neighbours``).

    Returns:
        ``pass_arounds_per_human_veh_km`` / ``cut_ins_per_human_veh_km`` in the
        baseline behind the vehicles that are AVs at the level, the human
        vehicle-km they are normalised by, and the excess (level − baseline).
    """
    av = set(level_run.get("av_ids", []))
    nb = base_run["neighbours"]
    n_pa = n_ci = 0
    for changer, leaders, follower in nb["changes"]:
        if changer in av:
            continue
        if any(ld in av for ld in leaders):
            n_pa += 1
        elif follower is not None and follower in av:
            n_ci += 1
    span = nb["span_m_per_vehicle"]
    km = (sum(span.values()) - sum(span.get(a, 0.0) for a in av)) / 1000.0
    pa_cf = _rate(n_pa, km)
    ci_cf = _rate(n_ci, km)
    pa_lv = level_run["rates"]["pass_arounds_per_human_veh_km"]
    ci_lv = level_run["rates"]["cut_ins_per_human_veh_km"]
    return {
        "baseline_pass_arounds_per_human_veh_km": pa_cf,
        "baseline_cut_ins_per_human_veh_km": ci_cf,
        "baseline_human_veh_km": km,
        "excess_pass_arounds_per_human_veh_km": (
            pa_lv - pa_cf if pa_lv is not None and pa_cf is not None else None
        ),
        "excess_cut_ins_per_human_veh_km": (
            ci_lv - ci_cf if ci_lv is not None and ci_cf is not None else None
        ),
    }


def fuel_decomposition(level_run: Mapping[str, Any], base_run: Mapping[str, Any]) -> dict[str, Any]:
    """``F − F0 = s_h (f_h − F0) + s_a (f_a − F0)`` for one seed (whole-trip ratios)."""
    f0 = base_run["fuel"]["all"]["ml_per_km"]
    fl = level_run["fuel"]
    km = fl["all"]["km"]
    if f0 is None or not km:
        return {"from_humans": None, "from_avs": None, "total": None}
    parts = {}
    for cls in ("human", "av"):
        share = fl[cls]["km"] / km
        f = fl[cls]["ml_per_km"]
        parts[cls] = share * (f - f0) if f is not None else 0.0
    total = fl["all"]["ml_per_km"] - f0
    return {"from_humans": parts["human"], "from_avs": parts["av"], "total": total}


def _verdict(checks: Mapping[str, bool | None]) -> tuple[str, list[str]]:
    failed = [k for k, v in checks.items() if not v]
    if not checks.get("a_fuel_increase_reproduces"):
        return "not_applicable", failed
    rest = [k for k in checks if k != "a_fuel_increase_reproduces"]
    if all(checks[k] for k in rest):
        return "supported", failed
    return "not_supported", failed


def aggregate(
    per_run: Mapping[str, Mapping[int, Mapping[str, Any]]],
    seeds: Sequence[int],
    *,
    baseline: str = "baseline",
) -> dict[str, Any]:
    """Per level: aggregates, paired changes against the baseline, the
    counterfactual pass-arounds, the fuel split and the checks.

    Args:
        per_run: ``{cell: {seed: per-run record}}``.
        seeds: The sweep's seed list (pairing order).
        baseline: The baseline cell's name.

    Returns:
        ``{"levels": {cell: {...}}, "missing_runs": [...]}``.
    """
    base = per_run.get(baseline, {})
    missing = [
        {"cell": c, "seed": s} for c, runs in per_run.items() for s in seeds if s not in runs
    ]
    levels: dict[str, Any] = {}
    base_scalars = {s: base[s]["scalars"] for s in seeds if s in base}
    for cell, runs in per_run.items():
        present = [s for s in seeds if s in runs]
        pen = next((float(runs[s]["penetration"]) for s in present), None)
        names = sorted({k for s in present for k in runs[s]["scalars"]})
        agg = {k: ci95([runs[s]["scalars"].get(k) for s in present]) for k in names}
        fbc = {
            b: {
                "mean_ml_per_km": ci95(
                    [runs[s]["fuel_by_changes"][b]["mean_ml_per_km"] for s in present]
                ),
                "n_vehicles": int(sum(runs[s]["fuel_by_changes"][b]["n"] for s in present)),
            }
            for b in (*CHANGE_BINS, "changed")
        }
        entry: dict[str, Any] = {
            "penetration": pen,
            "n_runs": len(present),
            "aggregate": agg,
            "fuel_by_changes": fbc,
            "counts_total": {
                k: int(sum(runs[s]["counts"][k] for s in present))
                for k in (runs[present[0]]["counts"] if present else {})
            },
        }
        if cell != baseline:
            lv = {s: runs[s]["scalars"] for s in present}
            deltas = {}
            for k in names:
                d = paired(
                    {s: lv[s].get(k) for s in lv},
                    {s: base_scalars[s].get(k) for s in base_scalars},
                )
                if d is not None:
                    deltas[k] = d
            cf_by_seed = {
                s: counterfactual(runs[s], base[s])
                for s in present
                if s in base and "neighbours" in base[s]
            }
            cf = {
                k: ci95([cf_by_seed[s][k] for s in cf_by_seed])
                for k in (
                    "baseline_pass_arounds_per_human_veh_km",
                    "baseline_cut_ins_per_human_veh_km",
                    "excess_pass_arounds_per_human_veh_km",
                    "excess_cut_ins_per_human_veh_km",
                )
            }
            for k in ("excess_pass_arounds_per_human_veh_km", "excess_cut_ins_per_human_veh_km"):
                c = cf[k]
                if c is not None and c["lo95"] is not None:
                    c["resolved"] = bool(c["lo95"] > 0 or c["hi95"] < 0)
            dec_by_seed = {s: fuel_decomposition(runs[s], base[s]) for s in present if s in base}
            dec = {
                k: ci95([dec_by_seed[s][k] for s in dec_by_seed])
                for k in ("from_humans", "from_avs", "total")
            }
            entry["paired_delta_vs_baseline"] = deltas
            entry["counterfactual"] = cf
            entry["fuel_decomposition_ml_per_veh_km"] = dec

            def _above(stat: Mapping[str, Any] | None) -> bool:
                return bool(stat is not None and stat.get("lo95") is not None and stat["lo95"] > 0)

            checks: dict[str, bool] = {}
            for name, (stat_name, _) in RESOLVED_CHECKS.items():
                if stat_name.startswith("excess_"):
                    checks[name] = _above(cf.get(stat_name))
                elif name == "e_changes_cost_fuel":
                    checks[name] = _above(agg.get(stat_name))
                else:
                    checks[name] = _above(deltas.get(stat_name))
            verdict, failed = _verdict(checks)
            entry["hypothesis_checks"] = checks
            entry["verdict"] = verdict
            entry["failed_checks"] = failed
            f_diag = deltas.get("fuel_ml_per_km_human_0_changes")
            entry["diagnostic_unchanged_humans_fuel_rises"] = _above(f_diag)
        levels[cell] = entry
    return {"levels": levels, "missing_runs": missing}


# ---------------------------------------------------------------------------
# Sweep driver
# ---------------------------------------------------------------------------


def _load_manifest(root: Path) -> dict[str, Any]:
    path = root / "MANIFEST.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path}: run scripts/us101_penetration_sweep.py first")
    manifest: dict[str, Any] = json.loads(path.read_text())
    return manifest


def _run_dirs(root: Path, manifest: Mapping[str, Any]) -> list[tuple[str, int, Path]]:
    return [
        (cell, int(seed), root / cell / chash / str(seed))
        for cell, chash in manifest["cells"].items()
        for seed in manifest["seeds"]
    ]


def _worker(payload: tuple[str, int, str, float, float, float | None]) -> tuple[str, int, str]:
    cell, seed, run_dir, lookback, rng_m, dwell = payload
    try:
        analyse_run(Path(run_dir), lookback_s=lookback, leader_range_m=rng_m, min_dwell_s=dwell)
        return cell, seed, ""
    except Exception as exc:  # reported, never silent
        return cell, seed, f"{type(exc).__name__}: {exc}"


def _current(
    run_dir: Path, lookback: float, rng_m: float, dwell: float | None
) -> dict[str, Any] | None:
    path = run_dir / PER_RUN_FILE
    if not path.is_file():
        return None
    try:
        rec: dict[str, Any] = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    prm = rec.get("parameters", {})
    if (
        rec.get("version") != PER_RUN_VERSION
        or prm.get("lookback_s") != lookback
        or prm.get("leader_range_m") != rng_m
        or prm.get("min_dwell_s") != dwell
    ):
        return None
    return rec


def _code_version() -> str:
    try:
        return subprocess.run(
            ["git", "log", "-1", "--format=%h %cI"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        return "unknown"


def run_sweep(
    root: Path,
    *,
    procs: int,
    lookback_s: float,
    leader_range_m: float,
    min_dwell_s: float | None,
    analyze_only: bool,
    log: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Analyse (unless ``analyze_only``) and aggregate a sweep tree.

    Returns:
        ``(artifact, n_failed)``.
    """
    t0 = time.perf_counter()
    manifest = _load_manifest(root)
    dirs = _run_dirs(root, manifest)
    n_failed = 0
    if not analyze_only:
        pending = [
            (c, s, str(d), lookback_s, leader_range_m, min_dwell_s)
            for c, s, d in dirs
            if (d / "meta.json").is_file()
            and _current(d, lookback_s, leader_range_m, min_dwell_s) is None
        ]
        log(f"{len(dirs)} runs; {len(pending)} to analyse with {procs} processes")
        if pending:
            with mp.get_context("spawn").Pool(max(1, min(procs, len(pending)))) as pool:
                for i, (cell, seed, err) in enumerate(
                    pool.imap_unordered(_worker, pending), start=1
                ):
                    if err:
                        n_failed += 1
                        log(f"  FAIL {cell} seed={seed}: {err}")
                    if i % 10 == 0 or i == len(pending):
                        log(f"  {i}/{len(pending)} ({time.perf_counter() - t0:.0f} s)")
    per_run: dict[str, dict[int, dict[str, Any]]] = {c: {} for c in manifest["cells"]}
    for cell, seed, d in dirs:
        rec = _current(d, lookback_s, leader_range_m, min_dwell_s)
        if rec is not None:
            per_run[cell][seed] = rec
    agg = aggregate(per_run, [int(s) for s in manifest["seeds"]])
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "experiment": "us101_lane_change_penetration",
        "work_package": "WP-81, docs/ROADMAP.md §5 item D2",
        "hypothesis": (
            "docs/US101_PENETRATION.md: on five lanes, neighbours change lanes around a "
            "slower AV, and those changes are accel/decel events that burn fuel"
        ),
        "source_result": {
            "doc": "docs/US101_PENETRATION.md",
            "artifact": "artifacts/us101_penetration_summary.json",
            "sweep": "scripts/us101_penetration_sweep.py",
            "original_analysis": "scripts/us101_penetration_analyze.py",
        },
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "code": _code_version(),
        "scenario": manifest.get("scenario"),
        "controller": manifest.get("controller"),
        "compliance": manifest.get("compliance"),
        "boundary": manifest.get("boundary"),
        "boundary_source": manifest.get("boundary_source"),
        "config_hashes": manifest["cells"],
        "seeds": manifest["seeds"],
        "n_seeds": len(manifest["seeds"]),
        "parameters": {
            "lookback_s": lookback_s,
            "leader_range_m": leader_range_m,
            "min_dwell_s": min_dwell_s,
            "original_x_ref_m": ORIGINAL_X_REF_M,
            "original_span_m": list(ORIGINAL_SPAN_M),
        },
        "method": (__doc__ or "").split("Usage::")[0].strip(),
        "decision_rule": {
            "checks": {k: {"statistic": v[0], "meaning": v[1]} for k, v in RESOLVED_CHECKS.items()},
            "holds_when": "the statistic's 95 % CI over seeds lies above 0",
            "verdict": {
                "not_applicable": "check a fails: the fuel increase did not reproduce at this level",
                "supported": "a holds and b, c, d and e all hold",
                "not_supported": "a holds and at least one of b, c, d, e fails (failed_checks)",
            },
            "diagnostic_unchanged_humans_fuel_rises": (
                "the paired change of the zero-change humans' fuel per km is resolved "
                "positive: part of the fuel increase does not go through lane changes"
            ),
        },
        **agg,
        "n_runs_analysed": sum(len(v) for v in per_run.values()),
        "n_runs_failed_this_session": n_failed,
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    return artifact, n_failed


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sweep", type=Path, help="sweep root holding MANIFEST.json")
    src.add_argument("--run-dir", type=Path, nargs="+", help="analyse these run dirs only")
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--out", type=Path, default=ARTIFACT)
    ap.add_argument("--analyze-only", action="store_true", help="aggregate existing per-run files")
    ap.add_argument("--allow-partial", action="store_true", help="exit 0 with runs missing")
    ap.add_argument("--lookback-s", type=float, default=DEFAULT_LOOKBACK_S)
    ap.add_argument("--leader-range-m", type=float, default=DEFAULT_LEADER_RANGE_M)
    ap.add_argument("--min-dwell-s", type=float, default=None)
    ap.add_argument(
        "--no-original-metrics",
        action="store_true",
        help="--run-dir only: skip compute_metrics",
    )
    args = ap.parse_args(argv)

    if args.run_dir:
        for d in args.run_dir:
            rec = analyse_run(
                d.resolve(),
                lookback_s=args.lookback_s,
                leader_range_m=args.leader_range_m,
                min_dwell_s=args.min_dwell_s,
                with_original_metrics=not args.no_original_metrics,
            )
            print(json.dumps({"run_dir": rec["run_dir"], **_json_safe(rec["rates"])}, indent=1))
        return 0

    root = args.sweep.resolve()
    artifact, n_failed = run_sweep(
        root,
        procs=args.procs,
        lookback_s=args.lookback_s,
        leader_range_m=args.leader_range_m,
        min_dwell_s=args.min_dwell_s,
        analyze_only=args.analyze_only,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(_json_safe(artifact), indent=1, allow_nan=False))
    print(f"wrote {args.out} ({artifact['n_runs_analysed']} runs)")
    for cell, lv in artifact["levels"].items():
        a = lv["aggregate"]
        h = (a.get("lc_per_veh_km_human") or {}).get("mean")
        f = (a.get("fuel_ml_per_veh_km_all") or {}).get("mean")
        print(
            f"  {cell:<10} n={lv['n_runs']:>2} human lc/km={h if h is None else round(h, 3)} "
            f"fuel ml/km={f if f is None else round(f, 2)} verdict={lv.get('verdict', '-')}"
        )
    if artifact["missing_runs"] or n_failed:
        print(f"{len(artifact['missing_runs'])} runs missing, {n_failed} failed", file=sys.stderr)
        return 0 if args.allow_partial else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
