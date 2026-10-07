"""validation.lane_use: lane shares of vehicle-time and of crossings, the lane numberings, the RMSE.

Hand-computed fixtures only. The lane-order mapping is tested on a synthetic
case where getting it backwards is visible: a three-lane detector station
whose rightmost lane (IRIS lane 1) carries 10 %, middle 30 %, leftmost 60 %,
against simulated crossings in SUMO lanes 0 / 1 / 2 (0 = rightmost) carrying
the same — RMSE 0 with the right mapping, about 41 points with the reversed one.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from validation.lane_use import (
    LaneSegment,
    crossing_lane_counts,
    distance_to_lane_change,
    lanes_at,
    largest_remainder_percent,
    left_numbered_lane,
    right_numbered_lane,
    share_rmse_pp,
    shares,
    sumo_lane_of_right_number,
    vehicle_time_lane_counts,
)


def test_lane_segment_refuses_empty_or_laneless() -> None:
    with pytest.raises(ValueError, match="x_hi > x_lo"):
        LaneSegment(5.0, 5.0, 3)
    with pytest.raises(ValueError, match="n_lanes"):
        LaneSegment(0.0, 5.0, 0)


def test_lanes_at_and_distance_to_a_lane_count_change() -> None:
    segs = [LaneSegment(0.0, 100.0, 3), LaneSegment(100.0, 250.0, 3), LaneSegment(250.0, 400.0, 4)]
    got = lanes_at([-1.0, 0.0, 99.9, 100.0, 260.0, 400.0], segs)
    assert np.array_equal(np.isnan(got), [True, False, False, False, False, True])
    assert got[1:5].tolist() == [3.0, 3.0, 3.0, 4.0]
    # 100 m is a boundary between equal counts (not a change); 250 m is one; 0 and 400 are the ends
    assert distance_to_lane_change(120.0, segs) == pytest.approx(120.0)
    assert distance_to_lane_change(240.0, segs) == pytest.approx(10.0)
    assert distance_to_lane_change(390.0, segs) == pytest.approx(10.0)
    assert distance_to_lane_change(5.0, []) == math.inf


def test_left_numbering_counts_from_the_left_through_the_edge_lane_count() -> None:
    # 4-lane edge: SUMO 0 (rightmost) is lane 4, SUMO 3 (leftmost) is lane 1
    assert left_numbered_lane([0, 1, 2, 3], [4, 4, 4, 4]).tolist() == [4, 3, 2, 1]
    # with an auxiliary lane on the right (5 lanes) the auxiliary lane is 5 and the through lanes keep 1-4
    assert left_numbered_lane([0, 1, 4], [5, 5, 5]).tolist() == [5, 4, 1]
    with pytest.raises(ValueError, match="outside"):
        left_numbered_lane([4], [4])
    with pytest.raises(ValueError, match="finite"):
        left_numbered_lane([0], [np.nan])


def test_right_numbering_is_the_iris_frame_and_inverts() -> None:
    assert right_numbered_lane([0, 1, 2]).tolist() == [1, 2, 3]
    for n in (1, 2, 3, 5):
        assert right_numbered_lane([sumo_lane_of_right_number(n)]).tolist() == [n]
    with pytest.raises(ValueError, match="start at 1"):
        sumo_lane_of_right_number(0)
    with pytest.raises(ValueError, match=">= 0"):
        right_numbered_lane([-1])


def test_vehicle_time_counts_inside_the_segment_and_window_only() -> None:
    x = np.array([5.0, 15.0, 25.0, 15.0, 15.0, 15.0])
    t = np.array([0.0, 0.0, 0.0, 9.9, 10.0, 5.0])
    lane = np.array([1, 2, 1, 2, 1, 7])
    got = vehicle_time_lane_counts(
        x, t, lane, x_range=(10.0, 20.0), t_range=(0.0, 10.0), lanes=(1, 2, 3)
    )
    # inside: (15, 0, 2), (15, 9.9, 2), (15, 5, lane 7 not reported); x 5/25 outside, t 10 outside
    assert got == {1: 0, 2: 2, 3: 0}
    with pytest.raises(ValueError, match="one shape"):
        vehicle_time_lane_counts([1.0], [1.0, 2.0], [1], x_range=(0, 1), t_range=(0, 1), lanes=(1,))


def test_crossings_once_per_vehicle_in_the_lane_of_the_later_sample() -> None:
    # vehicle a: crosses 100 m between t 1 and 2 (lane 0 -> 1: counted in 1), back and across again (not twice)
    # vehicle b: crosses at t 4 in lane 2; vehicle c: never reaches 100 m; vehicle d: crosses at t 20 (outside window)
    rows = [
        ("a", 0.0, 90.0, 0), ("a", 1.0, 99.0, 0), ("a", 2.0, 101.0, 1), ("a", 3.0, 99.5, 1), ("a", 4.0, 102.0, 0),
        ("b", 3.0, 95.0, 2), ("b", 4.0, 100.0, 2),
        ("c", 0.0, 10.0, 0), ("c", 5.0, 50.0, 0),
        ("d", 19.0, 99.0, 0), ("d", 20.0, 105.0, 0),
    ]  # fmt: skip
    ids, t, x, lane = (np.array(c) for c in zip(*rows, strict=True))
    order = np.random.default_rng(3).permutation(len(rows))  # input order must not matter
    got = crossing_lane_counts(
        ids[order], t[order], x[order], lane[order], [100.0, 50.0], t_range=(0.0, 10.0)
    )
    assert got[100.0] == {1: 1, 2: 1}
    assert got[50.0] == {0: 1}  # only c crosses 50 m (at t 5, lane 0)
    every = crossing_lane_counts(ids, t, x, lane, [100.0])
    assert every[100.0] == {0: 1, 1: 1, 2: 1}
    with pytest.raises(ValueError, match="one shape"):
        crossing_lane_counts(["a"], [0.0, 1.0], [0.0], [0], [1.0])


def test_shares_and_rmse_in_percentage_points() -> None:
    assert shares({"a": 1.0, "b": 3.0}) == {"a": 0.25, "b": 0.75}
    with pytest.raises(ValueError, match="zero"):
        shares({"a": 0.0})
    with pytest.raises(ValueError, match=">= 0"):
        shares({"a": -1.0, "b": 2.0})
    # differences 0.02 and -0.02: RMSE 2 points
    assert share_rmse_pp({1: 0.52, 2: 0.48}, {1: 0.50, 2: 0.50}) == pytest.approx(2.0)
    # differences 0.03, -0.01, -0.02: sqrt((9 + 1 + 4) / 3) points
    got = share_rmse_pp({1: 0.33, 2: 0.32, 3: 0.35}, {1: 0.30, 2: 0.33, 3: 0.37})
    assert got == pytest.approx(math.sqrt(14.0 / 3.0))
    with pytest.raises(ValueError, match="keys differ"):
        share_rmse_pp({1: 1.0}, {2: 1.0})
    with pytest.raises(ValueError, match="no shares"):
        share_rmse_pp({}, {})


def test_largest_remainder_reads_the_i24_quote_back() -> None:
    # the recorded I-24 span shares (artifacts/i24_lane_profile.json, lanes 1-4) and their quote
    span = [0.30326768867264775, 0.24156314092974993, 0.20056721083846568, 0.25460195955913656]
    assert largest_remainder_percent(span) == [30, 24, 20, 26]
    assert sum(largest_remainder_percent([1 / 3, 1 / 3, 1 / 3])) == 100
    with pytest.raises(ValueError, match="sum to 1"):
        largest_remainder_percent([0.5, 0.6])


def test_the_detector_lane_order_mapping_on_a_synthetic_station() -> None:
    """IRIS lane n is SUMO lane n - 1: the right mapping scores 0, the reversed one does not."""
    observed = {1: 0.10, 2: 0.30, 3: 0.60}  # IRIS: 1 = rightmost
    rng = np.random.default_rng(0)
    # 1,000 simulated vehicles crossing x = 50 m, 10 % in SUMO lane 0 (rightmost), 30 % in 1, 60 % in 2
    sumo_lanes = np.repeat([0, 1, 2], [100, 300, 600])
    ids = np.repeat(np.arange(1000), 2)
    t = np.tile([0.0, 1.0], 1000) + np.repeat(rng.uniform(0, 100, 1000), 2)
    x = np.tile([40.0, 60.0], 1000)
    lane = np.repeat(sumo_lanes, 2)
    counts = crossing_lane_counts(ids, t, x, lane, [50.0])[50.0]
    right = shares({n: float(counts.get(sumo_lane_of_right_number(n), 0)) for n in (1, 2, 3)})
    assert share_rmse_pp(right, observed) == pytest.approx(0.0, abs=1e-12)
    # reading SUMO's index as counted from the left (lane n = 3 - index) swaps lanes 1 and 3
    reversed_ = shares({n: float(counts.get(3 - n, 0)) for n in (1, 2, 3)})
    assert share_rmse_pp(reversed_, observed) == pytest.approx(100 * math.sqrt((0.5**2 * 2) / 3))
