"""validation.driver_calibration: the Amendment-1 rule on synthetic grids, every branch.

docs/FRISCO_PROTOCOL.md Amendment 1: among the pairs whose lane-share RMSE is
within 1 point of the grid's minimum, the smallest discharge error wins; ties
go to the smaller change (k, then lc_keep_right nearer 0); if no pair improves
lane use or discharge against the current setting (0, 0), it stays. "Improves"
is strictly smaller error, no noise band.
"""

from __future__ import annotations

import json
import math

import pytest

from validation.driver_calibration import (
    CURRENT,
    K_GRID,
    KEEP_RIGHT_GRID,
    GridScore,
    discharge_error,
    grid_pairs,
    improves,
    is_amendment_grid,
    relative_error,
    select_pair,
)


def _grid(values: dict[tuple[float, float], tuple[float | None, float | None]]) -> list[GridScore]:
    return [GridScore(k, kr, rm, de) for (k, kr), (rm, de) in values.items()]


def test_the_grids_are_the_amendments() -> None:
    assert K_GRID == (0.0, 0.25, 0.5, 0.75, 1.0)
    assert KEEP_RIGHT_GRID == (0.0, 0.1, 0.25, 0.5, 1.0)
    pairs = grid_pairs()
    assert len(pairs) == 25 and pairs[0] == CURRENT == (0.0, 0.0)
    assert is_amendment_grid(list(reversed(pairs)))
    assert not is_amendment_grid(pairs[:-1])


def test_errors() -> None:
    assert relative_error(110.0, 100.0) == pytest.approx(0.1)
    assert discharge_error({"a": 90.0, "b": 120.0}, {"a": 100.0, "b": 100.0}) == pytest.approx(0.15)
    with pytest.raises(ValueError, match="> 0"):
        relative_error(1.0, 0.0)
    with pytest.raises(ValueError, match="non-finite"):
        relative_error(math.nan, 1.0)
    with pytest.raises(ValueError, match="sections"):
        discharge_error({"a": 1.0}, {"b": 1.0})


def test_the_rule_picks_the_smallest_discharge_error_inside_the_band() -> None:
    sel = select_pair(
        _grid(
            {
                (0.0, 0.0): (5.0, 0.12),
                (0.5, 0.0): (3.0, 0.08),  # grid minimum RMSE
                (1.0, 0.0): (3.9, 0.02),  # inside the band (<= 4.0), best discharge
                (1.0, 0.5): (4.2, 0.00),  # outside the band
            }
        )
    )
    assert sel.chosen == (1.0, 0.0)
    assert sel.outcome == "rule_chose_other" and not sel.current_stays
    assert sel.min_rmse_pp == 3.0 and sel.band_pp == 4.0
    assert set(sel.candidates) == {(0.5, 0.0), (1.0, 0.0)}
    json.dumps(sel.to_dict())  # JSON-ready


def test_the_band_includes_its_edge() -> None:
    sel = select_pair(
        _grid({(0.0, 0.0): (5.0, 0.12), (0.5, 0.0): (3.0, 0.08), (1.0, 0.0): (4.0, 0.01)})
    )
    assert sel.chosen == (1.0, 0.0)


def test_a_discharge_tie_goes_to_the_smaller_k() -> None:
    sel = select_pair(
        _grid({(0.0, 0.0): (6.0, 0.2), (0.75, 0.1): (3.0, 0.05), (0.25, 0.5): (3.5, 0.05)})
    )
    assert sel.tied == ((0.75, 0.1), (0.25, 0.5))
    assert sel.chosen == (0.25, 0.5)
    assert "tied" in sel.explanation


def test_a_tie_in_discharge_and_k_goes_to_keep_right_nearer_zero() -> None:
    sel = select_pair(
        _grid(
            {(0.0, 0.0): (6.0, 0.2), (0.5, 1.0): (3.0, 0.05), (0.5, 0.1): (3.2, 0.05 * (1 + 1e-12))}
        )
    )
    assert sel.chosen == (0.5, 0.1)


def test_no_pair_improving_either_target_keeps_the_current_setting() -> None:
    sel = select_pair(
        _grid({(0.0, 0.0): (2.0, 0.05), (0.5, 0.0): (2.5, 0.05), (1.0, 0.0): (2.0, 0.06)})
    )
    assert sel.chosen == CURRENT and sel.current_stays
    assert sel.outcome == "no_improvement"
    assert sel.improvers == ()
    assert "stays" in sel.explanation


def test_the_rule_may_choose_the_current_setting_although_a_pair_improves_one_target() -> None:
    # (0.5, 0) has the better lane use, but inside the band the current setting discharges best
    sel = select_pair(_grid({(0.0, 0.0): (2.5, 0.03), (0.5, 0.0): (2.0, 0.07)}))
    assert sel.chosen == CURRENT and sel.current_stays
    assert sel.outcome == "rule_chose_current"
    assert sel.improvers == (((0.5, 0.0), ("lane_use",)),)


def test_the_chosen_pair_may_be_worse_on_discharge_when_the_current_is_outside_the_band() -> None:
    # literal reading: the current setting (RMSE 9) is outside the band of the minimum (3), so the
    # band's best discharge wins even though the current setting discharges better
    sel = select_pair(
        _grid({(0.0, 0.0): (9.0, 0.01), (0.25, 0.25): (3.0, 0.10), (0.5, 0.25): (3.5, 0.20)})
    )
    assert sel.chosen == (0.25, 0.25)
    assert improves(GridScore(0.25, 0.25, 3.0, 0.10), GridScore(0.0, 0.0, 9.0, 0.01)) == {
        "lane_use": True,
        "discharge": False,
    }


def test_unscored_pairs_are_left_out_and_listed() -> None:
    sel = select_pair(
        _grid(
            {
                (0.0, 0.0): (5.0, 0.1),
                (0.5, 0.0): (None, 0.0),
                (1.0, 0.0): (4.5, math.nan),
                (0.25, 0.1): (4.8, 0.09),
            }
        )
    )
    assert set(sel.unscored) == {(0.5, 0.0), (1.0, 0.0)}
    assert sel.chosen == (0.25, 0.1)


@pytest.mark.parametrize(
    ("values", "match"),
    [
        ({(0.5, 0.0): (1.0, 0.1)}, "not in the grid"),
        ({(0.0, 0.0): (None, 0.1), (0.5, 0.0): (1.0, 0.1)}, "has no score"),
    ],
)
def test_the_current_setting_must_be_scored(values: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        select_pair(_grid(values))


def test_a_duplicated_pair_is_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        select_pair([GridScore(0.0, 0.0, 1.0, 0.1), GridScore(0.0, 0.0, 2.0, 0.2)])
    with pytest.raises(ValueError, match="scored"):
        improves(GridScore(0.0, 0.0, None, 0.1), GridScore(0.5, 0.0, 1.0, 0.1))
