"""The coverage-weighted segment-speed target (docs/MERGE_ROUND6_PLAN.md §2.2).

``scripts/i24_weighted_target.py`` pools per-lane Edie speeds by
coverage-corrected vehicle-time instead of tracked vehicle-time. These tests
are synthetic and fast: they never touch the recording, the coverage artifact
or any validation artifact.

What is asserted:

* the unweighted pooling is exactly the construction it replaces (the mean of
  the sampled speeds in the bin, pooled over lanes);
* on lanes whose truth is known, the weighted target recovers the true pooled
  Edie speed under per-vehicle random thinning while the unweighted one is
  biased, and the bias has the sign and size the closed form gives;
* the weighting is inert where it must be (equal tracking rates, equal lane
  speeds) — so it cannot move a score by construction;
* the 15-min aggregation is the mean of three 5-min bins per segment.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def _load(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


wt = _load("i24_weighted_target")

MERGE_SPEEDS_KMH = {1: 33.0, 2: 30.0, 3: 27.0, 4: 24.0}
LANE_COVERAGE = {1: 0.73, 2: 0.54, 3: 0.47, 4: 0.56}
LANES = (1, 2, 3, 4)


# --------------------------------------------------------------------------
# The pooling itself
# --------------------------------------------------------------------------


def test_pooled_field_is_the_mean_of_the_sampled_speeds() -> None:
    """The unweighted target reproduces ``i24_validate._segment_speeds``."""
    df = pd.DataFrame(
        {
            "t": [0.0, 10.0, 310.0, 320.0, 320.0],
            "x": [10.0, 90.0, 10.0, 150.0, 190.0],
            "v": [10.0, 20.0, 5.0, 30.0, 10.0],
            "lane": [1, 3, 1, 2, 4],
        }
    )
    tab = wt.lane_tables_from_frame(df, span_hi=200.0, n_win=2, lanes=LANES, n_segments=2)
    field = wt.pooled_field(tab)
    assert field.shape == (2, 2)
    assert field[0, 0] == pytest.approx(15.0)  # (10 + 20) / 2, both in segment 0
    assert field[1, 0] == pytest.approx(5.0)
    assert field[1, 1] == pytest.approx(20.0)  # (30 + 10) / 2
    assert np.isnan(field[0, 1])  # no sample in that bin


def test_binning_matches_the_scored_construction() -> None:
    """Window and segment edges are half-open, and the last segment absorbs x = span."""
    span_hi, n_win, n_seg = 100.0, 2, 2
    df = pd.DataFrame(
        {
            "t": [-1.0, 0.0, 299.999, 300.0, 600.0],
            "x": [10.0, 0.0, 49.999, 50.0, 10.0],
            "v": [1.0, 2.0, 3.0, 4.0, 5.0],
            "lane": [1, 1, 1, 1, 1],
        }
    )
    tab = wt.lane_tables_from_frame(df, span_hi, n_win, LANES, n_seg)
    # t = -1 and t = 600 fall outside [0, 600) and are dropped.
    assert tab.counts.sum() == 3
    field = wt.pooled_field(tab)
    # t = 299.999 is still window 0 and x = 49.999 still segment 0;
    # t = 300 starts window 1 and x = 50 starts segment 1.
    assert field[0, 0] == pytest.approx(2.5)
    assert field[1, 1] == pytest.approx(4.0)
    assert np.isnan(field[0, 1]) and np.isnan(field[1, 0])


def test_out_of_range_lanes_and_rows_are_dropped() -> None:
    df = pd.DataFrame(
        {
            "t": [0.0, 0.0, 0.0],
            "x": [10.0, 10.0, -1.0],
            "v": [10.0, 99.0, 99.0],
            "lane": [1, 5, 1],
        }
    )
    tab = wt.lane_tables_from_frame(df, span_hi=100.0, n_win=1, lanes=LANES, n_segments=1)
    assert tab.counts.sum() == 1
    assert wt.pooled_field(tab)[0, 0] == pytest.approx(10.0)


# --------------------------------------------------------------------------
# The weighting: closed form
# --------------------------------------------------------------------------


def test_weighted_target_is_the_true_pooled_edie_speed() -> None:
    """Σ(t_l/c_l)v_l / Σ(t_l/c_l) on hand-computed lanes."""
    lanes = (1, 2)
    counts = np.array([[[100.0, 100.0]]])  # equal tracked vehicle-time
    sums = counts * np.array([[[20.0, 10.0]]])
    tab = wt.LaneTables(counts, sums, lanes, wt.WINDOW_S, 1.0)
    cov = np.array([[1.0, 0.5]])  # lane 2 tracked half as well → twice the weight
    assert wt.pooled_field(tab)[0, 0] == pytest.approx(15.0)
    assert wt.weighted_field(tab, cov)[0, 0] == pytest.approx((100 * 20 + 200 * 10) / 300)


def test_weighting_is_inert_when_coverage_is_lane_independent() -> None:
    tab = wt.LaneTables(
        np.array([[[30.0, 70.0, 10.0, 5.0]]]),
        np.array([[[300.0, 1400.0, 90.0, 60.0]]]),
        LANES,
        wt.WINDOW_S,
        1.0,
    )
    for c in (1.0, 0.6, 0.31):
        cov = np.full((1, 4), c)
        assert wt.weighted_field(tab, cov)[0, 0] == pytest.approx(wt.pooled_field(tab)[0, 0])


def test_weighting_is_inert_when_the_lanes_run_alike() -> None:
    counts = np.array([[[30.0, 70.0, 10.0, 5.0]]])
    tab = wt.LaneTables(counts, counts * 12.0, LANES, wt.WINDOW_S, 1.0)
    cov = np.array([[0.73, 0.54, 0.47, 0.56]])
    assert wt.weighted_field(tab, cov)[0, 0] == pytest.approx(12.0)
    assert wt.pooled_field(tab)[0, 0] == pytest.approx(12.0)


def test_weighted_field_rejects_impossible_coverage() -> None:
    tab = wt.LaneTables(np.ones((1, 1, 2)), np.ones((1, 1, 2)), (1, 2), wt.WINDOW_S, 1.0)
    for bad in (np.array([[0.0, 0.5]]), np.array([[0.5, 1.5]]), np.array([[np.nan, 0.5]])):
        with pytest.raises(ValueError):
            wt.weighted_field(tab, bad)
    with pytest.raises(ValueError):
        wt.weighted_field(tab, np.array([[0.5, 0.5, 0.5]]))


def test_analytic_case_quantifies_the_bias() -> None:
    """The closed-form case: weighted exact, unweighted biased toward the tracked lanes."""
    case = wt.analytic_case("hand", MERGE_SPEEDS_KMH, {ln: 0.25 for ln in LANES}, LANE_COVERAGE)
    assert case["weighted_bias_kmh"] == pytest.approx(0.0, abs=1e-9)
    # Lane 1 is the fastest and the best tracked, so the present target reads high.
    assert case["unweighted_bias_kmh"] > 0.1
    s = np.array([0.25, 0.25, 0.25, 0.25])
    c = np.array([LANE_COVERAGE[ln] for ln in LANES])
    v = np.array([MERGE_SPEEDS_KMH[ln] for ln in LANES])
    expected = float(s @ v / s.sum() - (s / c) @ v / (s / c).sum())
    assert case["unweighted_bias_kmh"] == pytest.approx(expected, abs=5e-4)


# --------------------------------------------------------------------------
# The weighting: end to end on thinned synthetic lanes
# --------------------------------------------------------------------------


def test_thinning_recovers_the_true_pooled_speed() -> None:
    """One Monte-Carlo case through the real construction."""
    case = wt.synthetic_case(
        "mc",
        MERGE_SPEEDS_KMH,
        {ln: 400 for ln in LANES},
        LANE_COVERAGE,
        seed=11,
        speed_cv=0.20,
    )
    truth = case["true_pooled_kmh"]
    assert abs(case["weighted_pooled_kmh"] - truth) < abs(case["unweighted_pooled_kmh"] - truth)
    assert case["unweighted"]["rms_error_kmh"] > case["weighted"]["rms_error_kmh"]


def test_the_weighted_target_is_unbiased_over_seeds_and_the_present_one_is_not() -> None:
    """20 independent thinnings: the present target's mean error is many SEs from zero."""
    rep = wt.repeat_summary(
        "mc", MERGE_SPEEDS_KMH, {ln: 400 for ln in LANES}, LANE_COVERAGE, speed_cv=0.20
    )
    assert abs(rep["weighted"]["t_statistic_vs_zero"]) < 2.5
    assert rep["unweighted"]["t_statistic_vs_zero"] > 4.0
    assert rep["unweighted"]["mean_signed_error_kmh"] > 0.15


def test_synthetic_samples_thin_whole_vehicles() -> None:
    """Thinning is per vehicle (the coverage estimators' model), not per sample."""
    truth, tracked = wt.synthetic_samples(
        {1: 10.0, 2: 10.0},
        {1: 200, 2: 200},
        {1: 1.0, 2: 0.5},
        span_hi=500.0,
        n_win=2,
        rng=np.random.default_rng(3),
    )
    per_lane = tracked.groupby("lane").size() / truth.groupby("lane").size()
    assert per_lane[1] == pytest.approx(1.0)
    assert per_lane[2] == pytest.approx(0.5, abs=0.08)


# --------------------------------------------------------------------------
# Aggregation and re-scoring helpers
# --------------------------------------------------------------------------


def test_aggregate_windows_averages_three_bins_per_segment() -> None:
    field = np.arange(12, dtype=float).reshape(6, 2)
    out = wt.aggregate_windows(field, 3)
    assert out.shape == (2, 2)
    assert out[0, 0] == pytest.approx(np.mean([0.0, 2.0, 4.0]))
    assert out[1, 1] == pytest.approx(np.mean([7.0, 9.0, 11.0]))


def test_aggregate_windows_ignores_empty_bins() -> None:
    field = np.array([[1.0], [np.nan], [3.0]])
    assert wt.aggregate_windows(field, 3)[0, 0] == pytest.approx(2.0)


def test_lane_time_shares_sum_to_one() -> None:
    tab = wt.LaneTables(
        np.array([[[30.0, 70.0, 10.0, 5.0]]]),
        np.zeros((1, 1, 4)),
        LANES,
        wt.WINDOW_S,
        1.0,
    )
    cov = np.array([[0.73, 0.54, 0.47, 0.56]])
    assert wt.lane_time_shares(tab).sum() == pytest.approx(1.0)
    corrected = wt.lane_time_shares(tab, cov)
    assert corrected.sum() == pytest.approx(1.0)
    # The worst-tracked lane gains share, the best-tracked one loses it.
    assert corrected[0, 0, 2] > wt.lane_time_shares(tab)[0, 0, 2]
    assert corrected[0, 0, 0] < wt.lane_time_shares(tab)[0, 0, 0]
