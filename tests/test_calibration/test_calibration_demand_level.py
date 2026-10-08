"""calibration.demand_level: B5's selection rule (docs/PRE_FRISCO_PROGRAM.md B5, FRISCO_PROTOCOL
Amendment 6), pinned on synthetic grids. Pure: nothing is simulated or read."""

from __future__ import annotations

import json
import math
from fractions import Fraction

import pytest

from calibration import demand_level as dl
from calibration.demand import geh

SEEDS = (11, 22, 33, 44, 55)


def _obj(n_under: int, n_bins: int = 72, mean: float = 3.0) -> dl.ObjectiveReading:
    return dl.ObjectiveReading("replicate_mean", n_bins, n_under, mean)


def _r(
    scale: float, inserted: float | tuple[float, ...], obj: dl.ObjectiveReading
) -> dl.ScaleReading:
    ins = inserted if isinstance(inserted, tuple) else (inserted,) * len(SEEDS)
    return dl.ScaleReading(scale=scale, seeds=SEEDS, inserted=ins, objective=obj)


# --------------------------------------------------------------------------- the constants are the plan's


def test_constants_are_the_plans() -> None:
    assert dl.I24_COARSE == (0.6, 0.7, 0.8, 0.9, 1.0, 1.1)
    assert (dl.REFINE_STEP, dl.REFINE_HALF_WIDTH) == (0.025, 2)
    assert dl.I94_GRID == (0.95, 0.975, 1.0, 1.025, 1.05)
    assert (dl.N_SEEDS, dl.INSERTION_TOLERANCE, dl.GEH_THRESHOLD) == (5, 0.01, 5.0)
    assert (dl.BREAKDOWN_FLOOR, dl.BREAKDOWN_MIN_MEAN, dl.LOCK_MEDIAN_RATIO) == (0.9, 0.95, 0.8)


def test_refine_round_and_the_count_uncertainty() -> None:
    assert dl.refine_scales(0.9) == (0.85, 0.875, 0.925, 0.95)
    assert dl.refine_scales(0.6) == (0.55, 0.575, 0.625, 0.65)
    assert dl.refine_scales(0.04) == (0.015, 0.065, 0.09)  # positive scales only
    assert dl.within_count_error(dl.I94_GRID, 0.05)
    assert not dl.within_count_error(dl.I94_GRID, 0.04)
    with pytest.raises(ValueError, match="count_error"):
        dl.within_count_error(dl.I94_GRID, math.nan)


def test_the_floor_is_the_from_arms_realised_share_less_one_point() -> None:
    assert dl.min_inserted_from(0.96659) == pytest.approx(0.95659)
    assert dl.min_inserted_from(0.9839957965012054) == pytest.approx(0.9739957965012054)
    for bad in (0.0, 1.2, math.nan):
        with pytest.raises(ValueError, match="realised"):
            dl.min_inserted_from(bad)


# --------------------------------------------------------------------------- the two estimators


def test_replicate_mean_objective_scores_each_bins_seed_mean() -> None:
    # bin 0: seeds 1,100 and 1,300 -> mean 1,200 against 1,200 (GEH 0); bin 1: 900 and 1,100 -> 1,000
    # against 1,200 (GEH 6.03, over 5) although each seed alone is 6.03 / 0.88; bin 2: NaN observed, skipped
    sim = [[1100.0, 900.0, 50.0], [1300.0, 1100.0, 60.0]]
    obs = [1200.0, 1200.0, math.nan]
    r = dl.replicate_mean_objective(sim, obs)
    g = math.sqrt(2 * 200.0**2 / 2200.0)
    assert (r.estimator, r.n_bins, r.n_under) == ("replicate_mean", 2, 1)
    assert r.mean_geh == pytest.approx(g / 2)
    assert r.share == Fraction(1, 2)
    # a seed's NaN drops the bin too
    sim[1][0] = math.nan
    assert dl.replicate_mean_objective(sim, obs).n_bins == 1
    with pytest.raises(ValueError, match="bins"):
        dl.replicate_mean_objective([[1.0]], [1.0, 2.0])
    with pytest.raises(ValueError, match="no seed"):
        dl.replicate_mean_objective([], [1.0])


def test_pooled_objective_pools_every_seeds_comparisons() -> None:
    pairs = [[(1200.0, 1200.0), (1000.0, 1200.0)], [(1300.0, 1200.0), (math.nan, 5.0)]]
    r = dl.pooled_objective(pairs)
    vals = [0.0, geh(1000.0, 1200.0), geh(1300.0, 1200.0)]
    assert (r.estimator, r.n_bins, r.n_under) == ("pooled", 3, 2)
    assert r.mean_geh == pytest.approx(sum(vals) / 3)
    assert r.to_dict()["under_5"] == pytest.approx(2 / 3)
    with pytest.raises(ValueError, match="no seed"):
        dl.pooled_objective([])


def test_the_strict_bound_and_the_geh_used_are_the_criterions() -> None:
    # GEH exactly 5 fails (strict <), as validation.criteria scores it
    m, c = 1250.0, 1000.0  # GEH sqrt(2 * 250^2 / 2250) = 7.45: over
    assert dl.pooled_objective([[(m, c)]]).n_under == 0
    from validation.metrics import geh as v_geh

    for mm, cc in ((0.0, 0.0), (10.0, 0.0), (6000.0, 6626.0), (4400.0, 4490.5)):
        assert geh(mm, cc) == v_geh(mm, cc)


# --------------------------------------------------------------------------- the rule


def test_the_best_share_among_the_scales_that_insert_enough() -> None:
    """The Amendment-2 / p14 pattern: the flow optimum holds demand back."""
    grid = [
        _r(0.8, 0.995, _obj(20)),
        _r(0.9, 0.97, _obj(26)),
        _r(1.0, 0.90, _obj(30)),  # best share, but a backlog
        _r(1.1, 0.80, _obj(29)),
    ]
    sel = dl.select_scale(grid, min_inserted=0.95659, reference_scale=0.925)
    assert sel.chosen_scale == 0.9 and not sel.constraint_unmet
    assert sel.qualifying_scales == (0.8, 0.9)
    assert sel.order == (0.9, 0.8) and sel.decided_by == "share"
    assert sel.reason.startswith("s = 0.9: GEH < 5 on 26 of 72")
    assert [p["meets_constraint"] for p in sel.per_scale] == [True, True, False, False]
    # the record is plain JSON
    doc = json.loads(json.dumps(sel.to_dict()))
    assert doc["chosen_scale"] == 0.9 and "Amendment 6" in doc["rule"]


def test_the_constraint_reads_the_mean_over_the_seeds_not_one_seed() -> None:
    low_first = (0.95, 0.97, 0.97, 0.97, 0.97)  # mean 0.966: qualifies at 0.95659
    high_first = (0.97, 0.95, 0.95, 0.95, 0.95)  # mean 0.954: does not
    grid = [_r(0.9, low_first, _obj(30)), _r(1.0, high_first, _obj(40)), _r(0.8, 1.0, _obj(10))]
    sel = dl.select_scale(grid, min_inserted=0.95659, reference_scale=0.925)
    assert sel.chosen_scale == 0.9 and sel.qualifying_scales == (0.8, 0.9)


def test_constraint_unmet_chooses_nothing() -> None:
    grid = [_r(0.9, 0.94, _obj(30)), _r(1.0, 0.90, _obj(40))]
    sel = dl.select_scale(grid, min_inserted=0.95, reference_scale=1.0)
    assert sel.constraint_unmet and sel.chosen_scale is None
    assert sel.qualifying_scales == () and sel.order == () and sel.decided_by is None
    assert sel.reason.startswith("CONSTRAINT UNMET") and "highest 0.9400, s = 0.9" in sel.reason
    assert sel.to_dict()["chosen_scale"] is None


def test_ties_go_to_the_smaller_mean_geh() -> None:
    # 36/72 and 18/36 are the same share: the mean GEH decides
    grid = [_r(0.9, 0.99, _obj(36, 72, 3.2)), _r(0.85, 0.99, _obj(18, 36, 3.1))]
    sel = dl.select_scale(grid, min_inserted=0.95, reference_scale=0.925)
    assert sel.chosen_scale == 0.85 and sel.decided_by == "mean_geh"
    assert "decided by mean_geh" in sel.reason


def test_then_the_smaller_change_from_the_from_arms_level() -> None:
    grid = [_r(0.8, 0.99, _obj(30, mean=3.0)), _r(1.0, 0.99, _obj(30, mean=3.0))]
    # from 0.925: 1.0 is 0.075 away, 0.8 is 0.125
    sel = dl.select_scale(grid, min_inserted=0.95, reference_scale=0.925)
    assert sel.chosen_scale == 1.0 and sel.decided_by == "change"
    # I-94: the from-arm is factor 1
    grid = [_r(0.95, 0.99, _obj(30)), _r(1.025, 0.99, _obj(30))]
    assert dl.select_scale(grid, min_inserted=0.95, reference_scale=1.0).chosen_scale == 1.025


def test_then_the_smaller_scale() -> None:
    # 0.95 and 1.05 lie equally far from 1 (float noise rounded away): the smaller scale
    grid = [_r(1.05, 0.99, _obj(30)), _r(0.95, 0.99, _obj(30))]
    sel = dl.select_scale(grid, min_inserted=0.95, reference_scale=1.0)
    assert sel.chosen_scale == 0.95 and sel.decided_by == "scale"


def test_a_single_qualifying_scale() -> None:
    grid = [_r(0.8, 0.99, _obj(1)), _r(1.0, 0.5, _obj(70))]
    sel = dl.select_scale(grid, min_inserted=0.95, reference_scale=1.0)
    assert sel.chosen_scale == 0.8 and sel.decided_by == "only"
    assert sel.reason.endswith("the only qualifying scale")


def test_readings_the_rule_cannot_read_are_refused() -> None:
    ok = _r(0.9, 0.99, _obj(30))
    with pytest.raises(ValueError, match="no scale"):
        dl.select_scale([], min_inserted=0.95, reference_scale=1.0)
    with pytest.raises(ValueError, match="twice"):
        dl.select_scale(
            [ok, _r(0.9000000001, 0.99, _obj(3))], min_inserted=0.95, reference_scale=1.0
        )
    other = dl.ScaleReading(0.8, (1, 2, 3, 4, 5), (0.99,) * 5, _obj(3))
    with pytest.raises(ValueError, match="not the fit seeds"):
        dl.select_scale([ok, other], min_inserted=0.95, reference_scale=1.0)
    with pytest.raises(ValueError, match="not the fit seeds"):
        dl.select_scale([ok], min_inserted=0.95, reference_scale=1.0, seeds=(1, 2, 3, 4, 5))
    with pytest.raises(ValueError, match="inserted fractions"):
        dl.select_scale(
            [dl.ScaleReading(0.8, SEEDS, (0.99,), _obj(3))], min_inserted=0.95, reference_scale=1.0
        )
    with pytest.raises(ValueError, match="outside"):
        dl.select_scale([_r(0.8, 1.2, _obj(3))], min_inserted=0.95, reference_scale=1.0)
    with pytest.raises(ValueError, match="no scored bin"):
        dl.select_scale([_r(0.8, 0.99, _obj(0, 0))], min_inserted=0.95, reference_scale=1.0)
    with pytest.raises(ValueError, match="positive"):
        dl.select_scale([_r(0.0, 0.99, _obj(3))], min_inserted=0.95, reference_scale=1.0)
    for bad in (0.0, 1.5, math.nan):
        with pytest.raises(ValueError, match="min_inserted"):
            dl.select_scale([ok], min_inserted=bad, reference_scale=1.0)
    with pytest.raises(ValueError, match="not defined"):
        _ = _obj(0, 0).share


# --------------------------------------------------------------------------- the report-only readings


def test_breakdown_reading() -> None:
    """The p14 B1 + B2 seed (0.660 among about 0.984) is a breakdown; a low battery mean reads none."""
    battery = [0.984] * 19 + [0.660]
    flags = dl.breakdown_flags(battery)
    assert flags is not None and flags == [False] * 19 + [True]
    assert dl.breakdown_flags([0.92] * 20) is None  # mean below 0.95: does not apply
    assert dl.breakdown_flags([]) is None
    assert dl.breakdown_flags([0.96, 0.95]) == [False, False]


def test_departed_share_lock_rule() -> None:
    battery = [0.984] * 19 + [0.660]
    assert dl.departed_share_locks(battery) == [False] * 19 + [True]  # 0.660 < 0.8 x 0.984
    assert dl.departed_share_locks([0.98, 0.79, 0.99]) == [False, False, False]  # 0.79 >= 0.784
    assert dl.departed_share_locks([]) == []
