"""Lane use: shares by lane of vehicle-time and of crossings, and their error.

The lane-use target of docs/FRISCO_PROTOCOL.md Amendment 1 (a): the
root-mean-square error, in percentage points, of a corridor's lane shares
against the measured ones. Two observables, because the two corridors measure
lane use differently:

* **vehicle-time** (:func:`vehicle_time_lane_counts`): the samples of every
  trajectory inside a segment ``[x_lo, x_hi)`` and a time window
  ``[t_lo, t_hi)``, counted by lane. With a fixed sampling rate a sample is a
  fixed slice of vehicle-time, so the shares are shares of vehicle-time — what
  the I-24 MOTION recording gives (``artifacts/i24_lane_profile.json``,
  ``share``);
* **crossings** (:func:`crossing_lane_counts`): the vehicles crossing a
  section ``x_s``, each counted once, in the lane it holds at its first
  crossing — what a loop detector counts (MnDOT's per-lane detectors). The
  rule is ``scripts/i24_build_replica.first_crossing_lane_counts``'s: a
  vehicle crosses when two consecutive samples straddle the section
  (``x_prev < x_s <= x_cur``), in the lane of the later sample, and at its
  first crossing in time only; here the crossing must also fall inside the
  time window (the later sample's ``t``).

Lane numbering is where a comparison goes wrong silently, so it is stated
once here and converted by function, never inline:

* **SUMO** counts lanes per edge from the right: index 0 is the rightmost
  lane of the edge the vehicle is on (an added auxiliary lane on the right is
  index 0 and shifts the through lanes up).
* **I-24 MOTION** (the recording and ``artifacts/i24_lane_profile.json``)
  numbers from the left: 1 is the leftmost lane, 4 the rightmost mainline
  lane, 5 the auxiliary (ramp) lane. A SUMO lane maps to ``n_lanes - index``
  with ``n_lanes`` the lane count of the edge at that ``x``
  (:func:`left_numbered_lane`; ``scripts/i24_lane_profile.run_profile``).
* **MnDOT IRIS** numbers detectors from the right: lane 1 is the rightmost
  lane (``calibration.loaders.mndot.Detector.lane``). A SUMO lane maps to
  ``index + 1`` (:func:`right_numbered_lane`), valid where the simulated
  cross-section has the station's lane count.

Pure functions over arrays; no SUMO and no file access.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

PERCENT: Final[float] = 100.0
"""Shares are fractions internally; errors are reported in percentage points."""


@dataclass(frozen=True)
class LaneSegment:
    """A stretch of the corridor's linear ``x`` with one lane count.

    Attributes:
        x_lo: Start [m], inclusive.
        x_hi: End [m], exclusive.
        n_lanes: Lanes of the edge there (SUMO's count, auxiliary lanes included).
    """

    x_lo: float
    x_hi: float
    n_lanes: int

    def __post_init__(self) -> None:
        if not self.x_hi > self.x_lo:
            raise ValueError(f"LaneSegment needs x_hi > x_lo, got [{self.x_lo}, {self.x_hi})")
        if self.n_lanes < 1:
            raise ValueError(f"LaneSegment needs n_lanes >= 1, got {self.n_lanes}")


def lanes_at(x: npt.ArrayLike, segments: Sequence[LaneSegment]) -> FloatArray:
    """The lane count at each ``x`` (NaN where no segment covers it).

    Args:
        x: Positions [m] in the segments' coordinates.
        segments: Non-overlapping segments (any order).

    Returns:
        Float array of lane counts, NaN outside every segment.
    """
    xs = np.asarray(x, dtype=np.float64)
    out = np.full(xs.shape, np.nan)
    for seg in segments:
        out[(xs >= seg.x_lo) & (xs < seg.x_hi)] = float(seg.n_lanes)
    return out


def distance_to_lane_change(x: float, segments: Sequence[LaneSegment]) -> float:
    """Distance [m] from ``x`` to the nearest point where the lane count changes.

    A segment boundary between equal lane counts is not a change; the ends of
    the covered stretch are (outside it the count is unknown). ``inf`` when
    no change exists.
    """
    ordered = sorted(segments, key=lambda s: s.x_lo)
    edges: list[float] = []
    for i, seg in enumerate(ordered):
        prev = ordered[i - 1] if i > 0 else None
        if prev is None or prev.n_lanes != seg.n_lanes or not math.isclose(prev.x_hi, seg.x_lo):
            edges.append(seg.x_lo)
        nxt = ordered[i + 1] if i + 1 < len(ordered) else None
        if nxt is None or nxt.n_lanes != seg.n_lanes or not math.isclose(seg.x_hi, nxt.x_lo):
            edges.append(seg.x_hi)
    return min((abs(x - e) for e in edges), default=math.inf)


def left_numbered_lane(sumo_lane: npt.ArrayLike, n_lanes: npt.ArrayLike) -> IntArray:
    """SUMO lane index → left-numbered lane (1 = leftmost; the I-24 MOTION frame).

    Args:
        sumo_lane: SUMO lane indices (0 = rightmost of the edge).
        n_lanes: Lane count of the edge each index refers to (finite).

    Returns:
        ``n_lanes - sumo_lane`` as integers.

    Raises:
        ValueError: An index outside ``[0, n_lanes)`` or a non-finite count.
    """
    lane = np.asarray(sumo_lane, dtype=np.float64)
    n = np.asarray(n_lanes, dtype=np.float64)
    if not np.all(np.isfinite(n)):
        raise ValueError("left_numbered_lane: every lane count must be finite")
    if np.any(lane < 0) or np.any(lane >= n):
        raise ValueError("left_numbered_lane: a SUMO lane index lies outside [0, n_lanes)")
    return (n - lane).astype(np.int64)


def right_numbered_lane(sumo_lane: npt.ArrayLike) -> IntArray:
    """SUMO lane index → right-numbered lane (1 = rightmost; MnDOT IRIS detectors).

    Raises:
        ValueError: A negative index.
    """
    lane = np.asarray(sumo_lane, dtype=np.int64)
    if np.any(lane < 0):
        raise ValueError("right_numbered_lane: SUMO lane indices are >= 0")
    return lane + 1


def sumo_lane_of_right_number(lane_number: int) -> int:
    """Right-numbered lane (IRIS: 1 = rightmost) → SUMO lane index (0 = rightmost).

    Raises:
        ValueError: A lane number below 1 (IRIS uses 0 for "not lane-specific").
    """
    if lane_number < 1:
        raise ValueError(f"right-numbered lanes start at 1, got {lane_number}")
    return int(lane_number) - 1


def vehicle_time_lane_counts(
    x: npt.ArrayLike,
    t: npt.ArrayLike,
    lane: npt.ArrayLike,
    *,
    x_range: tuple[float, float],
    t_range: tuple[float, float],
    lanes: Iterable[int],
) -> dict[int, int]:
    """Trajectory samples by lane inside a segment and a time window.

    With a fixed sampling interval each sample is the same slice of
    vehicle-time, so the counts are proportional to vehicle-time by lane.

    Args:
        x: Sample positions [m].
        t: Sample times [s].
        lane: Lane of each sample, already in the numbering of ``lanes``.
        x_range: ``[x_lo, x_hi)`` [m].
        t_range: ``[t_lo, t_hi)`` [s].
        lanes: Lanes to report (samples in any other lane are not counted;
            a listed lane without samples reports 0).

    Returns:
        ``{lane: n_samples}``.
    """
    xs = np.asarray(x, dtype=np.float64)
    ts = np.asarray(t, dtype=np.float64)
    ln = np.asarray(lane)
    if not (xs.shape == ts.shape == ln.shape):
        raise ValueError("vehicle_time_lane_counts: x, t and lane must have one shape")
    keep = (xs >= x_range[0]) & (xs < x_range[1]) & (ts >= t_range[0]) & (ts < t_range[1])
    wanted = [int(v) for v in lanes]
    picked = ln[keep]
    return {v: int(np.count_nonzero(picked == v)) for v in wanted}


def crossing_lane_counts(
    veh_id: npt.ArrayLike,
    t: npt.ArrayLike,
    x: npt.ArrayLike,
    lane: npt.ArrayLike,
    sections: Iterable[float],
    *,
    t_range: tuple[float, float] | None = None,
) -> dict[float, dict[int, int]]:
    """Vehicles crossing each section, once each, by the lane of their first crossing.

    The rule of the module docstring: consecutive samples of one vehicle
    straddling the section (``x_prev < x_s <= x_cur``), counted in the lane of
    the later sample, at the vehicle's first such crossing whose later sample
    lies in ``t_range``.

    Args:
        veh_id: Vehicle id of each sample (any hashable values).
        t: Sample times [s].
        x: Sample positions [m].
        lane: Lane of each sample, in any integer numbering (reported as is).
        sections: Section positions [m].
        t_range: ``[t_lo, t_hi)`` for the crossing time; ``None`` = all.

    Returns:
        ``{section: {lane: n_vehicles}}``, only lanes with a crossing listed.
    """
    raw_ids = np.asarray(veh_id)
    ts = np.asarray(t, dtype=np.float64)
    xs = np.asarray(x, dtype=np.float64)
    ln = np.asarray(lane, dtype=np.int64)
    if not (raw_ids.shape == ts.shape == xs.shape == ln.shape):
        raise ValueError("crossing_lane_counts: veh_id, t, x and lane must have one shape")
    _, codes = np.unique(raw_ids, return_inverse=True)
    order = np.lexsort((ts, codes))
    veh, ts, xs, ln = codes[order], ts[order], xs[order], ln[order]
    same = veh[1:] == veh[:-1]
    x_prev, x_cur = xs[:-1][same], xs[1:][same]
    t_cur, veh_cur, lane_cur = ts[1:][same], veh[1:][same], ln[1:][same]
    in_window = (
        np.ones(t_cur.shape, dtype=bool)
        if t_range is None
        else (t_cur >= t_range[0]) & (t_cur < t_range[1])
    )
    out: dict[float, dict[int, int]] = {}
    for x_s in sections:
        hit = (x_prev < x_s) & (x_cur >= x_s) & in_window
        # rows are ordered by (vehicle, time): the first hit of a vehicle is its first crossing
        _, first = np.unique(veh_cur[hit], return_index=True)
        lanes_hit = lane_cur[hit][first]
        values, counts = np.unique(lanes_hit, return_counts=True)
        out[float(x_s)] = {int(v): int(c) for v, c in zip(values, counts, strict=True)}
    return out


def shares[K: Hashable](counts: Mapping[K, float]) -> dict[K, float]:
    """Counts → fractions of their total (in the mapping's order).

    Raises:
        ValueError: A negative count, or a total of zero.
    """
    if any(v < 0 for v in counts.values()):
        raise ValueError("shares: counts must be >= 0")
    total = float(sum(counts.values()))
    if total <= 0.0:
        raise ValueError("shares: the counts sum to zero — nothing to share out")
    return {k: float(v) / total for k, v in counts.items()}


def share_rmse_pp[K: Hashable](sim: Mapping[K, float], obs: Mapping[K, float]) -> float:
    """Root-mean-square difference of two share sets, in percentage points.

    Args:
        sim: Simulated shares (fractions) keyed by lane (or ``(station, lane)``).
        obs: Observed shares, the same keys.

    Returns:
        ``100 · sqrt(mean((sim - obs)²))`` over the keys.

    Raises:
        ValueError: The key sets differ, or are empty.
    """
    if set(sim) != set(obs):
        raise ValueError(
            f"share_rmse_pp: the simulated and observed keys differ "
            f"({sorted(map(str, set(sim) ^ set(obs)))})"
        )
    if not sim:
        raise ValueError("share_rmse_pp: no shares to compare")
    diff = np.array([float(sim[k]) - float(obs[k]) for k in obs], dtype=np.float64)
    return float(PERCENT * math.sqrt(float(np.mean(diff**2))))


def largest_remainder_percent(fractions: Sequence[float]) -> list[int]:
    """Whole percentages that sum to 100 (largest-remainder rounding).

    How a quoted share set like "30/24/20/26 %" is read back from exact
    shares: the floors, then one point each to the largest remainders (ties
    to the earlier entry).

    Raises:
        ValueError: The fractions do not sum to 1 (within 1e-9) or one is
            negative.
    """
    if any(f < 0 for f in fractions) or not math.isclose(sum(fractions), 1.0, abs_tol=1e-9):
        raise ValueError("largest_remainder_percent: fractions must be >= 0 and sum to 1")
    raw = [PERCENT * f for f in fractions]
    floors = [math.floor(v) for v in raw]
    left = round(PERCENT) - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - floors[i]), i))
    for i in order[:left]:
        floors[i] += 1
    return floors
