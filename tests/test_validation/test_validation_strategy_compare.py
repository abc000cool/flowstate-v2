"""The fair strategy comparison table (WP-105; docs/FRISCO_PROTOCOL.md §8.1, §8.3).

Synthetic per-seed records: the paired differences and intervals against
hand-computed values, and every refusal — a missing measure (absent key or
None), a collision count not recorded, seeds that differ from the baseline's,
no baseline. A NaN that is present (no wave formed) is not missing.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from scipy import stats

from validation.strategy_compare import (
    COLLISIONS_KEY,
    COMPARISON_MEASURES,
    FUEL_MODEL_ESTIMATE,
    REQUIRED_KEYS,
    ComparisonRefusedError,
    build_comparison_table,
    render_markdown,
)

SEEDS = (11, 22, 33)


def _record(seed: int, offset: float, **over: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        k: 100.0 + i * 10.0 + (seed % 10) * (1.0 + offset / 10.0) + offset
        for i, k in enumerate(REQUIRED_KEYS)
    }
    rec[COLLISIONS_KEY] = 0
    rec["wave_count"] = 2
    rec["mean_tt_s"] = 1.0  # extra keys are allowed and ignored
    rec.update(over)
    return rec


def _arms(**over: dict[int, dict[str, Any]]) -> dict[str, dict[int, dict[str, Any]]]:
    arms = {
        "baseline": {s: _record(s, 0.0) for s in SEEDS},
        "alinea_best": {s: _record(s, 10.0) for s in SEEDS},
    }
    arms.update(over)
    return arms


def _t(values: list[float]) -> tuple[float, float, float]:
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    half = float(stats.t.ppf(0.975, arr.size - 1) * arr.std(ddof=1) / math.sqrt(arr.size))
    return mean, mean - half, mean + half


class TestTable:
    def test_the_fixed_measure_set(self) -> None:
        assert REQUIRED_KEYS == (
            "throughput_veh_h",
            "mean_tt_incl_waiting_s",
            "p90_tt_incl_waiting_s",
            "total_delay_incl_waiting_veh_h",
            "sigma_v_temporal_ms",
            "sigma_v_spatial_ms",
            "wave_count",
            "wave_amplitude_ms",
            "n_collisions",
            "fuel_ml_per_veh_km",
        )
        fuel = next(m for m in COMPARISON_MEASURES if m.key == "fuel_ml_per_veh_km")
        assert "model estimate" in fuel.label

    def test_paired_differences_against_hand_computed_intervals(self) -> None:
        arms = _arms()
        table = build_comparison_table(arms)
        assert [r.arm for r in table.rows] == ["baseline", "alinea_best"]
        assert table.seeds == SEEDS and table.underpowered
        key = "total_delay_incl_waiting_veh_h"
        diffs = [arms["alinea_best"][s][key] - arms["baseline"][s][key] for s in SEEDS]
        mean, lo, hi = _t(diffs)
        d = table.row("alinea_best").paired[key]
        assert (d.mean, d.lo95, d.hi95, d.n) == (
            pytest.approx(mean),
            pytest.approx(lo),
            pytest.approx(hi),
            3,
        )
        assert d.resolved is (lo > 0 or hi < 0)
        base_mean = float(np.mean([arms["baseline"][s][key] for s in SEEDS]))
        assert d.pct_of_baseline == pytest.approx(100.0 * mean / base_mean)
        m, mlo, mhi = _t([arms["baseline"][s][key] for s in SEEDS])
        c = table.row("baseline").marginal[key]
        assert (c.mean, c.lo95, c.hi95) == (
            pytest.approx(m),
            pytest.approx(mlo),
            pytest.approx(mhi),
        )
        assert table.row("baseline").paired == {}

    def test_collisions_are_summed_and_flagged(self) -> None:
        arms = _arms()
        arms["alinea_best"][22][COLLISIONS_KEY] = 2
        table = build_comparison_table(arms)
        row = table.row("alinea_best")
        assert row.collisions_total == 2 and row.zero_collisions is False
        assert table.row("baseline").zero_collisions is True

    def test_a_present_nan_is_not_missing(self) -> None:
        arms = _arms()
        arms["alinea_best"][11]["wave_amplitude_ms"] = math.nan  # no wave formed
        table = build_comparison_table(arms)
        assert table.row("alinea_best").marginal["wave_amplitude_ms"].n == 2
        assert table.row("alinea_best").paired["wave_amplitude_ms"].n == 2

    def test_dict_and_markdown(self) -> None:
        table = build_comparison_table(_arms())
        d = table.to_dict()
        assert d["n_seeds"] == 3 and d["underpowered"] is True
        assert set(d["arms"]) == {"baseline", "alinea_best"}
        assert set(d["arms"]["alinea_best"]["vs_baseline_paired"]) == set(REQUIRED_KEYS)
        md = render_markdown(table)
        assert "UNDERPOWERED" in md and FUEL_MODEL_ESTIMATE in md
        assert "Total delay including waiting" in md


class TestRefusals:
    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_a_missing_measure_is_refused(self, key: str) -> None:
        arms = _arms()
        del arms["alinea_best"][33][key]
        with pytest.raises(ComparisonRefusedError, match=f"missing — measures \\['{key}'\\]"):
            build_comparison_table(arms)

    def test_a_none_value_is_missing(self) -> None:
        arms = _arms()
        arms["baseline"][11][COLLISIONS_KEY] = None  # collision count not recorded
        with pytest.raises(ComparisonRefusedError, match="baseline seed 11: n_collisions"):
            build_comparison_table(arms)

    def test_an_old_tree_without_the_waiting_measures_is_refused(self) -> None:
        old = {
            k: v
            for k, v in _record(11, 0.0).items()
            if k not in ("mean_tt_incl_waiting_s", "p90_tt_incl_waiting_s")
            and k != "total_delay_incl_waiting_veh_h"
        }
        arms = {"baseline": {11: old}, "alinea_best": {11: dict(old)}}
        with pytest.raises(ComparisonRefusedError, match="6 value\\(s\\)"):
            build_comparison_table(arms)

    def test_different_seeds_are_refused(self) -> None:
        arms = _arms(alinea_best={s: _record(s, 10.0) for s in (11, 22, 44)})
        with pytest.raises(ComparisonRefusedError, match="only in the arm: \\[44\\]"):
            build_comparison_table(arms)

    def test_no_baseline_is_refused(self) -> None:
        with pytest.raises(ComparisonRefusedError, match="no baseline arm"):
            build_comparison_table({"alinea_best": {11: _record(11, 0.0)}})

    def test_an_arm_without_runs_is_refused(self) -> None:
        with pytest.raises(ComparisonRefusedError, match="has no runs"):
            build_comparison_table(_arms(vsl_best={}))
