"""calibration.ramp_estimation and calibration.conservation (WP-101 item 3), synthetic only.

The exact-recovery tests use ``synthetic_detectors.linear_frame``: linear flows
at a constant speed, built with the travel-time lag, for which the lag-aligned
conservation identity holds to rounding error.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from calibration.conservation import (
    corridor_layout,
    detector_grid,
    parse_ramp,
    period_means,
    shift_series,
    station_grid,
)
from calibration.ramp_estimation import (
    LOO_SCHEMA,
    RAMP_SCHEMA,
    SplitAssumption,
    error_stats,
    estimate_ramps,
    leave_one_out,
    render_leave_one_out_markdown,
    render_markdown,
)


def _load_synthetic() -> ModuleType:
    path = Path(__file__).parent / "synthetic_detectors.py"
    spec = importlib.util.spec_from_file_location("wp101_synthetic_detectors_ramps", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


syn = _load_synthetic()


@pytest.fixture(scope="module")
def linear() -> pd.DataFrame:
    return syn.linear_frame()


def _compare(estimate: np.ndarray, truth: np.ndarray) -> np.ndarray:
    both = np.isfinite(estimate) & np.isfinite(truth[None, :])
    assert both.sum() > 100
    return np.abs(estimate - truth[None, :])[both]


class TestConservationCore:
    def test_shift_is_exact_on_a_linear_series(self) -> None:
        values = 100.0 + 3.0 * np.arange(10.0)
        shifted = shift_series(values, np.full(10, 0.25))
        assert shifted[:-1] == pytest.approx(values[:-1] + 0.75)
        assert np.isnan(shifted[-1])
        back = shift_series(values, np.full(10, -1.0))
        assert np.isnan(back[0]) and back[1:] == pytest.approx(values[:-1])

    def test_a_period_is_valid_only_when_every_window_is(self) -> None:
        series = np.array([1.0, 2.0, 3.0, np.nan, 5.0, 6.0, 7.0])
        assert period_means(series, 3).tolist()[0] == 2.0
        assert np.isnan(period_means(series, 3)[1])
        assert len(period_means(series, 3)) == 2

    def test_station_totals_need_every_lane(self) -> None:
        lanes = syn.daily_frame(seed=3, per_lane=True, dates=syn.DATES[:1])
        lanes = syn.set_values(lanes, station="A", lane="2", date=syn.DATES[0], start_h=6.0,
                               end_h=9.0, flow_veh_h=np.nan)  # fmt: skip
        grid = station_grid(detector_grid(lanes))
        assert not grid.per_lane
        gap = slice(6 * 12, 9 * 12)  # 5-min windows from midnight
        assert np.isnan(grid.flow_veh_h["A"][0, gap]).all()
        assert np.isfinite(grid.flow_veh_h["A"][0, : gap.start]).all()
        assert grid.sensors["B"].lanes == 3
        stations = syn.daily_frame(seed=3, dates=syn.DATES[:1])
        reference = detector_grid(stations)
        assert np.isfinite(grid.flow_veh_h["B"]).all()
        assert grid.flow_veh_h["R1"] == pytest.approx(reference.flow_veh_h["R1"])

    def test_a_never_reporting_lane_is_not_a_lane_but_a_masked_one_still_is(self) -> None:
        from calibration.conservation import silent_lane_note
        from calibration.data_quality import assess_quality, mask_grid

        lanes = syn.daily_frame(seed=6, per_lane=True, dates=syn.DATES[:3])
        placeholder = lanes[(lanes["station"] == "B") & (lanes["lane"] == "1")].copy()
        placeholder["lane"] = "T9"
        placeholder[["flow_veh_h", "occupancy_pct", "speed_ms"]] = np.nan
        frame = pd.concat([lanes, placeholder], ignore_index=True)
        frame.attrs = dict(lanes.attrs)
        grid = detector_grid(frame)
        assert grid.silent == frozenset({"B:T9"})
        totals = station_grid(grid)
        assert totals.sensors["B"].lanes == 3
        assert np.isfinite(totals.flow_veh_h["B"]).all()
        assert "B:T9" in (silent_lane_note(grid) or "")
        # a real lane whose loop is dead is set aside by the checks, not dropped as a lane
        dead = frame.copy()
        dead.attrs = dict(frame.attrs)
        dead.loc[(dead["station"] == "B") & (dead["lane"] == "2"), "flow_veh_h"] = 0.0
        dead_grid = detector_grid(dead)
        masked = mask_grid(dead_grid, assess_quality(dead_grid, mass_balance=False))
        assert masked.silent == frozenset({"B:T9"})
        assert np.isnan(station_grid(masked).flow_veh_h["B"]).all()

    def test_layout_places_ramps_half_open(self, linear: pd.DataFrame) -> None:
        layout = corridor_layout(
            station_grid(detector_grid(linear)), extra_ramps=[parse_ramp("X:off:1000")]
        )
        first, second = layout.segments
        assert (first.upstream, first.downstream) == ("A", "B")
        assert [r.id for r in first.ramps] == ["R1", "X"]
        assert [r.id for r in second.ramps] == ["R2"]
        assert not first.ramps[1].measured

    def test_rows_off_the_grid_and_daylight_saving_folds_are_refused(
        self, linear: pd.DataFrame
    ) -> None:
        with pytest.raises(ValueError, match="window grid"):
            detector_grid(linear, start_local="00:02")
        twice = pd.concat([linear.iloc[:3], linear.iloc[:1]], ignore_index=True)
        twice.attrs = dict(linear.attrs)
        with pytest.raises(ValueError, match="daylight-saving"):
            detector_grid(twice)

    def test_unknown_unmeasured_id_is_refused(self, linear: pd.DataFrame) -> None:
        with pytest.raises(ValueError, match="not ramps"):
            corridor_layout(station_grid(detector_grid(linear)), unmeasured=["B"])


class TestEstimates:
    def test_a_planted_entrance_is_recovered_exactly(self, linear: pd.DataFrame) -> None:
        result = estimate_ramps(linear, unmeasured=["R1"])
        est = result.estimate("R1")
        assert est.status == "estimated"
        truth = syn.truth_period_means("R1")
        assert _compare(est.estimate_veh_h, truth).max() < 1e-6
        assert est.summary()["n_clipped"] == 0
        inside = (est.lower_veh_h <= truth) & (truth <= est.upper_veh_h)
        assert inside[np.isfinite(est.estimate_veh_h)].all()
        assert "the only ramp without a count in A→B" in est.reason

    def test_a_planted_exit_is_recovered_exactly(self, linear: pd.DataFrame) -> None:
        est = estimate_ramps(linear, unmeasured=["R2"]).estimate("R2")
        assert _compare(est.estimate_veh_h, syn.truth_period_means("R2")).max() < 1e-6

    def test_without_the_lag_the_estimate_is_not_exact(self, linear: pd.DataFrame) -> None:
        # with the lag capped at zero the counts are compared at the same clock time, which
        # is not when the same vehicles pass: the identity no longer holds exactly
        est = estimate_ramps(linear, unmeasured=["R1"], max_lag_s=0.0).estimate("R1")
        assert _compare(est.estimate_veh_h, syn.truth_period_means("R1")).max() > 0.1

    def test_negative_estimates_are_clipped_and_reported(self, linear: pd.DataFrame) -> None:
        frame = linear.copy()
        frame.attrs = dict(linear.attrs)
        hours = pd.to_datetime(frame["timestamp"].astype(str)).dt.hour
        frame.loc[(frame["station"] == "B") & (hours < 6), "flow_veh_h"] -= 500.0
        est = estimate_ramps(frame, unmeasured=["R1"]).estimate("R1")
        summary = est.summary()
        assert summary["n_clipped"] > 0
        clipped = np.isfinite(est.raw_veh_h) & (est.raw_veh_h < 0.0)
        assert (est.estimate_veh_h[clipped] == 0.0).all()
        assert summary["mean_clipped_veh_h"] == pytest.approx(
            float(-est.raw_veh_h[clipped].mean()), abs=0.1
        )
        assert (est.lower_veh_h[clipped] >= 0.0).all()

    def test_two_unknowns_in_a_segment_are_unidentified(self) -> None:
        frame = syn.linear_frame(with_r0=True)
        result = estimate_ramps(frame, unmeasured=["R0", "R1"])
        by_id = {e.ramp: e for e in result.estimates}
        assert by_id["R0"].status == by_id["R1"].status == "unidentified"
        assert "fix only their net flow" in by_id["R0"].reason
        assert by_id["R0"].summary()["n_estimated"] == 0
        payload = json.loads(result.to_json())
        assert {e["status"] for e in payload["estimates"]} == {"unidentified"}

    def test_a_supplied_split_identifies_both_and_is_recorded(self) -> None:
        share = 0.08
        frame = syn.linear_frame(with_r0=True, r0_share=share, r1_slope=0.0)
        split = SplitAssumption(
            ramp="R0", kind="share_of_upstream", value=share, source="synthetic: planted share"
        )
        result = estimate_ramps(frame, unmeasured=["R0", "R1"], splits=[split])
        r0, r1 = result.estimate("R0"), result.estimate("R1")
        assert r0.status == r1.status == "estimated"
        assert (
            _compare(r0.estimate_veh_h, syn.truth_period_means("R0", r0_share=share)).max() < 1e-6
        )
        r1_truth = syn.truth_period_means("R1", r1_slope=0.0)
        assert _compare(r1.estimate_veh_h, r1_truth).max() < 1e-6
        payload = result.to_dict()
        assert payload["splits"] == [split.to_dict()]
        assert any("synthetic: planted share" in a for a in payload["assumptions"])
        assert payload["estimates"][0]["assumptions"][0]["source"] == "synthetic: planted share"

    def test_a_wrong_split_moves_the_estimate_by_its_error(self) -> None:
        frame = syn.linear_frame(with_r0=True, r0_share=0.08, r1_slope=0.0)
        wrong = SplitAssumption(ramp="R0", kind="share_of_upstream", value=0.10, source="guess")
        result = estimate_ramps(frame, unmeasured=["R0", "R1"], splits=[wrong])
        r1 = result.estimate("R1")
        # R1 absorbs the 2 % of upstream flow the assumption moved onto R0
        assert np.nanmin(r1.estimate_veh_h) > 200.0 + 0.015 * 1500.0

    @pytest.mark.parametrize(
        ("split", "match"),
        [
            (SplitAssumption("R0", "share_of_upstream", 0.1, "x"), "already fix one ramp"),
            (SplitAssumption("R1", "fixed_veh_h", 100.0, "x"), "the ramp is measured"),
        ],
    )
    def test_misplaced_splits_are_refused(self, split: SplitAssumption, match: str) -> None:
        frame = syn.linear_frame(with_r0=True)
        with pytest.raises(ValueError, match=match):
            estimate_ramps(frame, unmeasured=["R0"], splits=[split])

    def test_a_split_needs_a_source(self) -> None:
        with pytest.raises(ValueError, match="source"):
            SplitAssumption("R0", "share_of_upstream", 0.1, "  ")
        with pytest.raises(ValueError, match="other"):
            SplitAssumption("R0", "ratio_to", 0.5, "x")

    def test_interval_width_scales_with_the_count_error(self, linear: pd.DataFrame) -> None:
        widths = {}
        for error in (0.025, 0.05, 0.10):
            est = estimate_ramps(linear, unmeasured=["R1"], count_error=error).estimate("R1")
            widths[error] = est.upper_veh_h - est.estimate_veh_h
        finite = np.isfinite(widths[0.05])
        assert widths[0.10][finite] == pytest.approx(2.0 * widths[0.05][finite])
        assert widths[0.025][finite] == pytest.approx(0.5 * widths[0.05][finite])
        quad = estimate_ramps(linear, unmeasured=["R1"], combination="quadrature").estimate("R1")
        assert ((quad.upper_veh_h - quad.estimate_veh_h)[finite] < widths[0.05][finite]).all()

    def test_per_lane_data_are_summed_to_stations(self) -> None:
        lanes = syn.daily_frame(seed=4, per_lane=True, dates=syn.DATES[:2])
        stations = syn.daily_frame(seed=4, dates=syn.DATES[:2])
        a = estimate_ramps(lanes, unmeasured=["R1"]).estimate("R1")
        b = estimate_ramps(stations, unmeasured=["R1"]).estimate("R1")
        assert a.estimate_veh_h.shape == b.estimate_veh_h.shape
        assert np.isfinite(a.estimate_veh_h).sum() > 100

    def test_json_and_markdown(self, linear: pd.DataFrame) -> None:
        result = estimate_ramps(linear, unmeasured=["R1"])
        payload = json.loads(result.to_json())
        assert payload["schema"] == RAMP_SCHEMA
        assert set(payload) == {
            "assumptions", "dates", "estimates", "layout", "method", "notes",
            "parameters", "period_start_local", "schema", "splits",
        }  # fmt: skip
        est = payload["estimates"][0]
        assert set(est["by_date"]) == set(syn.DATES[:2])
        assert len(est["profile"]["estimate_veh_h"]) == len(payload["period_start_local"]) == 96
        text = render_markdown(result)
        assert "## Assumptions" in text and "±5%" in text
        assert "| R1 | on_ramp | A→B | estimated |" in text

    def test_nothing_to_estimate_says_so(self, linear: pd.DataFrame) -> None:
        result = estimate_ramps(linear)
        assert result.estimates == ()
        assert any("nothing to estimate" in n for n in result.notes)


class TestLeaveOneOut:
    def test_exact_on_the_noiseless_corridor(self, linear: pd.DataFrame) -> None:
        loo = leave_one_out(linear)
        assert {r.ramp: r.status for r in loo.ramps} == {"R1": "tested", "R2": "tested"}
        for r in loo.ramps:
            assert abs(r.overall["bias_veh_h"]) < 1e-3
            assert r.overall["mae_veh_h"] < 1e-3
            assert r.overall["coverage"] == 1.0
        assert loo.pooled["n"] == sum(r.overall["n"] for r in loo.ramps)

    def test_on_a_noisy_corridor_errors_are_small_and_reported(self) -> None:
        loo = leave_one_out(syn.daily_frame(seed=5), stations=syn.stations_table())
        assert all(r.status == "tested" for r in loo.ramps)
        assert loo.pooled["relative_error"] < 0.1
        assert abs(loo.pooled["relative_bias"]) < 0.02
        assert loo.pooled["coverage"] > 0.9
        assert loo.pooled_congested["n"] == 0
        assert loo.median_ramp_relative_error is not None

    def test_a_segment_with_another_unmeasured_ramp_is_not_testable(
        self, linear: pd.DataFrame
    ) -> None:
        loo = leave_one_out(linear, extra_ramps=[parse_ramp("X:off:800")])
        status = {r.ramp: r for r in loo.ramps}
        assert status["R1"].status == "not_testable"
        assert "X" in status["R1"].reason
        assert status["R2"].status == "tested"

    def test_a_dead_ramp_detector_shows_as_a_large_error(self, linear: pd.DataFrame) -> None:
        frame = linear.copy()
        frame.attrs = dict(linear.attrs)
        frame.loc[frame["station"] == "R2", "flow_veh_h"] = 0.0
        loo = leave_one_out(frame)
        r2 = next(r for r in loo.ramps if r.ramp == "R2")
        assert r2.overall["relative_error"] is None  # measured flow sums to 0
        assert r2.overall["bias_veh_h"] > 100.0
        # the ±5 % band on two busy mainline counts is wide against a ~300 veh/h exit,
        # so a zero reading still falls inside some clipped intervals
        assert r2.overall["coverage"] < 0.5

    def test_json_and_markdown(self, linear: pd.DataFrame) -> None:
        loo = leave_one_out(linear)
        payload = json.loads(loo.to_json())
        assert payload["schema"] == LOO_SCHEMA
        assert set(payload["pooled"]) == {
            "overall", "free_flow", "congested", "median_ramp_relative_error",
            "n_tested", "n_not_testable",
        }  # fmt: skip
        text = render_leave_one_out_markdown(loo, provenance={"Inputs": "synthetic"})
        assert "## Per ramp" in text and "| R1 | on_ramp | A→B |" in text

    def test_error_stats_on_hand_numbers(self) -> None:
        est = np.array([10.0, 20.0, np.nan, 0.0])
        truth = np.array([12.0, 18.0, 5.0, 4.0])
        stats = error_stats(est, est - 3.0, est + 3.0, truth, raw=np.array([10, 20, 0, -2.0]))
        assert stats["n"] == 3
        assert stats["bias_veh_h"] == pytest.approx((-2 + 2 - 4) / 3, abs=0.05)
        assert stats["mae_veh_h"] == pytest.approx(8 / 3, abs=0.05)
        assert stats["relative_error"] == pytest.approx(8 / 34, abs=1e-4)
        assert stats["coverage"] == pytest.approx(2 / 3, abs=1e-4)
        assert stats["n_clipped"] == 1
