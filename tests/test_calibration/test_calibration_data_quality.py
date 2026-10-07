"""calibration.data_quality (WP-101 item 2) on synthetic corridors with planted faults.

Every fixture is generated (``synthetic_detectors.py``); no real detector data
is read. A clean corridor must pass every check — the planted faults are then
the only thing a finding can come from.
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

from calibration.conservation import detector_grid, parse_ramp
from calibration.data_quality import (
    CHECKS,
    FLOW_CEILING_VEH_H_LANE,
    QUALITY_SCHEMA,
    THRESHOLD_SOURCES,
    QualityThresholds,
    QualityVerdicts,
    assess_quality,
    lane_order_check,
    mask_frame,
    mask_grid,
    neighbours,
    render_markdown,
)
from calibration.loaders.detector_csv import load_detector_csv


def _load_synthetic() -> ModuleType:
    path = Path(__file__).parent / "synthetic_detectors.py"
    spec = importlib.util.spec_from_file_location("wp101_synthetic_detectors", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


syn = _load_synthetic()
D1, D2, D3, D4 = syn.DATES


@pytest.fixture(scope="module")
def clean() -> pd.DataFrame:
    return syn.daily_frame(seed=1)


@pytest.fixture(scope="module")
def clean_lanes() -> pd.DataFrame:
    return syn.daily_frame(seed=2, per_lane=True)


@pytest.fixture(scope="module")
def lanes_30s() -> pd.DataFrame:
    """Per-lane 30-second data (MnDOT's own resolution)."""
    return syn.daily_frame(seed=2, per_lane=True, interval_s=30.0)


def _queue_upstream(
    frame: pd.DataFrame, *, date: str, start_h: float, end_h: float
) -> pd.DataFrame:
    """Station A congested (the queue's upstream part): 1,800 veh/h at 3 m/s, 36 % occupancy.

    Consistent with the 6.5 m effective length (v·o/q = 3 × 0.36 / 0.5 = 6.5 m).
    """
    return syn.set_values(
        frame, station="A", date=date, start_h=start_h, end_h=end_h,
        flow_veh_h=1800.0, occupancy_pct=36.0, speed_ms=3.0,
    )  # fmt: skip


def _lane_day(report, station: str, lane: str, date: str):
    return next(
        sd
        for sd in report.sensor_days
        if sd.station == station and sd.lane == lane and sd.date == date
    )


def _checks(report, sensor: str, date: str) -> dict[str, str]:
    return {f.check: f.verdict for f in report.sensor_day(sensor, date).findings}


def _degrade(frame: pd.DataFrame, *, station: str, lane: str, factor: float) -> pd.DataFrame:
    """A loop that misses detections: its count and its occupancy both shrink."""
    out = syn.scale_values(frame, station=station, lane=lane, factor=factor)
    return syn.scale_values(out, station=station, lane=lane, factor=factor, column="occupancy_pct")


def _window(report, hour: float) -> int:
    return round((hour * 3600.0 - report.start_s) / report.interval_s)


class TestCleanCorridor:
    def test_every_sensor_day_passes(self, clean: pd.DataFrame) -> None:
        report = assess_quality(clean, stations=syn.stations_table())
        summary = report.summary()
        assert summary["n_ok"] == summary["n_sensor_days"] == 20
        assert summary["usable_share"] == 1.0
        assert all(m.verdict == "ok" for m in report.mass_balance)
        assert not report.attributions
        assert all(not f["unusual"] for f in report.corridor_day_factors.values())

    def test_per_lane_clean_corridor_passes_and_reports_lane_ratios(
        self, clean_lanes: pd.DataFrame
    ) -> None:
        report = assess_quality(clean_lanes, stations=syn.stations_table())
        assert report.per_lane
        assert report.summary()["n_ok"] == report.summary()["n_sensor_days"]
        ratio = report.sensor_day("A:1", D1).stats["lane_ratio"]
        # lane shares 0.30 against the mean of 0.33 and 0.37
        assert ratio == pytest.approx(0.30 / 0.35, rel=0.05)


class TestStuck:
    def test_a_frozen_hour_is_suspect_and_only_those_windows_are_masked(
        self, clean: pd.DataFrame
    ) -> None:
        frame = syn.set_values(
            clean, station="A", date=D2, start_h=10.0, end_h=11.0, flow_veh_h=2400.0
        )
        report = assess_quality(frame, stations=syn.stations_table())
        sd = report.sensor_day("A", D2)
        assert _checks(report, "A", D2)["stuck_flow"] == "suspect"
        assert sd.verdict == "suspect"
        expected = tuple(range(_window(report, 10.0), _window(report, 11.0)))
        assert sd.masked["flow"] == expected
        assert sd.stats["longest_stuck_flow_s"] == 3600.0
        masked = mask_grid(detector_grid(frame), report)
        day = masked.dates.index(D2)
        assert np.isnan(masked.flow_veh_h["A"][day, list(expected)]).all()
        assert np.isfinite(masked.flow_veh_h["A"][day, : expected[0]]).all()
        # the other days of the sensor are untouched
        assert report.sensor_day("A", D1).verdict == "ok"

    def test_a_frozen_afternoon_excludes_the_day(self, clean: pd.DataFrame) -> None:
        frame = syn.set_values(
            clean, station="A", date=D2, start_h=12.0, end_h=16.0, flow_veh_h=2400.0
        )
        report = assess_quality(frame, stations=syn.stations_table())
        assert _checks(report, "A", D2)["stuck_flow"] == "exclude"
        assert report.sensor_day("A", D2).n_usable == 0

    def test_a_repeated_low_night_count_is_not_stuck(self, clean: pd.DataFrame) -> None:
        # 1 vehicle per 5 min repeated for 3 hours at night: legitimate (below 10 veh/window)
        frame = syn.set_values(
            clean, station="R1", date=D1, start_h=1.0, end_h=4.0, flow_veh_h=12.0
        )
        report = assess_quality(frame, stations=syn.stations_table())
        assert "stuck_flow" not in _checks(report, "R1", D1)

    def test_a_frozen_speed_masks_speed_only(self, clean: pd.DataFrame) -> None:
        frame = syn.set_values(clean, station="C", date=D3, start_h=9.0, end_h=12.0, speed_ms=29.0)
        report = assess_quality(frame, stations=syn.stations_table())
        sd = report.sensor_day("C", D3)
        assert _checks(report, "C", D3) == {"stuck_speed": "suspect"}
        assert len(sd.masked["speed"]) == 36
        assert sd.masked["flow"] == ()
        assert sd.n_usable == sd.n_valid


class TestMissing:
    def test_a_missing_day_is_excluded(self, clean: pd.DataFrame) -> None:
        frame = syn.drop_rows(clean, station="R1", date=D3)
        report = assess_quality(frame, stations=syn.stations_table())
        sd = report.sensor_day("R1", D3)
        assert sd.verdict == "exclude"
        assert sd.n_valid == 0
        assert sd.stats["missing_share"] == 1.0
        assert _checks(report, "R1", D3) == {"missing": "exclude"}

    def test_a_third_missing_is_suspect(self, clean: pd.DataFrame) -> None:
        frame = syn.set_values(
            clean, station="B", date=D1, start_h=0.0, end_h=8.0, flow_veh_h=np.nan
        )
        report = assess_quality(frame, stations=syn.stations_table())
        assert _checks(report, "B", D1)["missing"] == "suspect"
        assert report.sensor_day("B", D1).stats["missing_share"] == pytest.approx(1 / 3)


class TestImpossible:
    def test_one_flow_above_the_ceiling_is_suspect_and_masked(self, clean: pd.DataFrame) -> None:
        too_high = 3 * FLOW_CEILING_VEH_H_LANE + 600.0
        frame = syn.set_values(
            clean, station="B", date=D1, start_h=3.0, end_h=3.05, flow_veh_h=too_high
        )
        report = assess_quality(frame, stations=syn.stations_table())
        sd = report.sensor_day("B", D1)
        assert _checks(report, "B", D1)["impossible"] == "suspect"
        assert sd.masked["flow"] == (_window(report, 3.0),)
        assert sd.stats["max_flow_veh_h_lane"] == pytest.approx(too_high / 3)

    def test_out_of_range_occupancy_and_speed_mask_their_own_quantity(
        self, clean: pd.DataFrame
    ) -> None:
        frame = syn.set_values(
            clean, station="C", date=D2, start_h=6.0, end_h=6.1, occupancy_pct=140.0
        )
        frame = syn.set_values(frame, station="C", date=D2, start_h=7.0, end_h=7.05, speed_ms=-1.0)
        report = assess_quality(frame, stations=syn.stations_table())
        sd = report.sensor_day("C", D2)
        assert sd.verdict == "suspect"
        assert sd.masked["occupancy"] == (_window(report, 6.0), _window(report, 6.0) + 1)
        assert sd.masked["speed"] == (_window(report, 7.0),)
        assert sd.masked["flow"] == ()

    def test_negative_sentinels_in_many_windows_exclude_the_day(self, clean: pd.DataFrame) -> None:
        frame = syn.set_values(
            clean, station="R2", date=D4, start_h=0.0, end_h=2.0, flow_veh_h=-1.0
        )
        report = assess_quality(frame, stations=syn.stations_table())
        assert _checks(report, "R2", D4)["impossible"] == "exclude"


class TestInconsistent:
    def test_zero_flow_with_occupancy_is_flagged(self, clean: pd.DataFrame) -> None:
        frame = syn.set_values(
            clean, station="A", date=D3, start_h=14.0, end_h=14.5,
            flow_veh_h=0.0, occupancy_pct=30.0, speed_ms=np.nan,
        )  # fmt: skip
        report = assess_quality(frame, stations=syn.stations_table())
        sd = report.sensor_day("A", D3)
        assert _checks(report, "A", D3)["zero_flow_occupied"] == "suspect"
        assert sd.stats["zero_flow_occupied_windows"] == 6

    @pytest.mark.parametrize("speed", [np.nan, 1.0, 25.0])
    def test_a_standing_queue_is_not_flagged(self, clean: pd.DataFrame, speed: float) -> None:
        # 20 min with nothing crossing B, the loop covered, while the queue
        # reaches A upstream; the speed field of a zero-count window is not
        # read, whatever it holds (MnDOT reports none)
        frame = syn.set_values(
            clean, station="B", date=D3, start_h=17.0, end_h=17.0 + 20.0 / 60.0,
            flow_veh_h=0.0, occupancy_pct=95.0, speed_ms=speed,
        )  # fmt: skip
        frame = _queue_upstream(frame, date=D3, start_h=17.0, end_h=17.0 + 20.0 / 60.0)
        report = assess_quality(frame, stations=syn.stations_table())
        sd = report.sensor_day("B", D3)
        assert sd.findings == () and sd.verdict == "ok"
        assert sd.stats["standstill_windows"] == 4
        assert sd.stats["zero_flow_occupied_windows"] == 0
        assert sd.stats["zero_run_windows"] == 0
        assert report.sensor_day("A", D3).findings == ()

    def test_occupancy_held_with_no_count_while_neighbours_flow_is_flagged(
        self, clean: pd.DataFrame
    ) -> None:
        # 30 min of 60 % occupancy and no count at A while B downstream keeps
        # counting A's traffic: a hanging-on loop, not a queue (and not a dead
        # loop: the loop is occupied); the 1 m/s in the speed field is no evidence
        frame = syn.set_values(
            clean, station="A", date=D3, start_h=14.0, end_h=14.5,
            flow_veh_h=0.0, occupancy_pct=60.0, speed_ms=1.0,
        )  # fmt: skip
        report = assess_quality(frame, stations=syn.stations_table())
        assert _checks(report, "A", D3) == {"zero_flow_occupied": "suspect"}
        sd = report.sensor_day("A", D3)
        assert sd.stats["standstill_windows"] == 0
        assert "neighbouring lane or station" in sd.findings[0].reason

    def test_zero_occupancy_under_heavy_flow_masks_occupancy_only(
        self, clean: pd.DataFrame
    ) -> None:
        frame = syn.set_values(
            clean, station="B", date=D2, start_h=7.0, end_h=9.0, occupancy_pct=0.0
        )
        report = assess_quality(frame, stations=syn.stations_table())
        sd = report.sensor_day("B", D2)
        assert _checks(report, "B", D2) == {"flow_without_occupancy": "suspect"}
        assert sd.masked["flow"] == ()
        assert len(sd.masked["occupancy"]) == sd.stats["flow_without_occupancy_windows"] > 0

    def test_a_mis_scaled_occupancy_excludes_the_day(self, clean: pd.DataFrame) -> None:
        # a fraction read as percent the other way round: occupancy ten times too high
        frame = syn.scale_values(clean, station="C", date=D1, factor=10.0, column="occupancy_pct")
        report = assess_quality(frame, stations=syn.stations_table())
        sd = report.sensor_day("C", D1)
        assert _checks(report, "C", D1)["implied_length"] == "exclude"
        assert sd.stats["implied_length_median_m"] == pytest.approx(65.0, rel=0.1)
        assert report.sensor_day("C", D2).stats["implied_length_median_m"] == pytest.approx(
            6.5, rel=0.1
        )


class TestStandstills:
    """Standing queues on 30-second per-lane data (docs: module "Standing queues")."""

    def test_neighbours_are_the_station_lanes_and_the_adjacent_stations(
        self, clean_lanes: pd.DataFrame
    ) -> None:
        near = neighbours(detector_grid(clean_lanes))
        assert near["A:1"] == ("A:2", "A:3", "B:1", "B:2", "B:3")
        assert set(near["B:2"]) == {"B:1", "B:3", "A:1", "A:2", "A:3", "C:1", "C:2", "C:3"}
        assert near["R1:1"] == ()  # a ramp is no neighbour of the mainline, nor it of a ramp

    def test_stop_and_go_standstills_are_not_flagged(self, lanes_30s: pd.DataFrame) -> None:
        # one 60-s standstill every 6 min over lane 3 of B, 16:00-18:00, speed
        # not reported (MnDOT reports none when nothing crosses the loop)
        frame = lanes_30s
        for i in range(20):
            a = 16.0 + i * 0.1
            frame = syn.set_values(
                frame, station="B", date=D2, start_h=a, end_h=a + 1.0 / 60.0, lane="3",
                flow_veh_h=0.0, occupancy_pct=100.0, speed_ms=np.nan,
            )  # fmt: skip
        report = assess_quality(frame, stations=syn.stations_table(), mass_balance=False)
        sd = _lane_day(report, "B", "3", D2)
        assert sd.findings == () and sd.verdict == "ok"
        assert sd.stats["standstill_windows"] == 40

    def test_a_long_standstill_seen_by_the_other_lanes_is_not_flagged(
        self, lanes_30s: pd.DataFrame
    ) -> None:
        # 15 min (beyond the 5-min standstill bound) with every lane of B
        # stopped: each lane's neighbours read the queue
        frame = lanes_30s
        for lane in ("1", "2", "3"):
            frame = syn.set_values(
                frame, station="B", date=D2, start_h=17.0, end_h=17.25, lane=lane,
                flow_veh_h=0.0, occupancy_pct=100.0, speed_ms=np.nan,
            )  # fmt: skip
        report = assess_quality(frame, stations=syn.stations_table(), mass_balance=False)
        for lane in ("1", "2", "3"):
            sd = _lane_day(report, "B", lane, D2)
            assert sd.findings == (), (lane, [f.reason for f in sd.findings])
            assert sd.stats["standstill_windows"] == 30

    def test_a_hanging_on_loop_is_still_caught(self, lanes_30s: pd.DataFrame) -> None:
        # occupancy held at 100 % with no count for an hour while the other
        # lanes and the neighbouring stations flow freely
        frame = syn.set_values(
            lanes_30s, station="B", date=D2, start_h=16.0, end_h=17.0, lane="3",
            flow_veh_h=0.0, occupancy_pct=100.0, speed_ms=np.nan,
        )  # fmt: skip
        report = assess_quality(frame, stations=syn.stations_table(), mass_balance=False)
        sd = _lane_day(report, "B", "3", D2)
        assert {f.check: f.verdict for f in sd.findings} == {"zero_flow_occupied": "suspect"}
        assert len(sd.masked["flow"]) == 120 and sd.stats["standstill_windows"] == 0
        assert "longest 1 h from 16:00" in sd.findings[0].reason

    def test_a_dead_lane_is_still_caught(self, lanes_30s: pd.DataFrame) -> None:
        # zero count AND zero occupancy for two hours of the morning peak
        frame = syn.set_values(
            lanes_30s, station="B", date=D2, start_h=7.0, end_h=9.0, lane="2",
            flow_veh_h=0.0, occupancy_pct=0.0, speed_ms=np.nan,
        )  # fmt: skip
        report = assess_quality(frame, stations=syn.stations_table(), mass_balance=False)
        sd = _lane_day(report, "B", "2", D2)
        assert {f.check: f.verdict for f in sd.findings} == {"zero_run": "suspect"}
        assert sd.stats["zero_run_windows"] == 240
        assert "the loop empty" in sd.findings[0].reason


class TestZeroCounts:
    def test_a_count_only_source_is_judged_by_its_neighbours(self, clean: pd.DataFrame) -> None:
        # no occupancy reported: a zero-count run is a dead loop unless a
        # neighbour reads congestion at the time
        frame = clean.assign(occupancy_pct=np.nan)
        frame.attrs = dict(clean.attrs)
        frame = syn.set_values(
            frame, station="B", date=D2, start_h=17.0, end_h=17.5,
            flow_veh_h=0.0, speed_ms=np.nan,
        )  # fmt: skip
        report = assess_quality(frame, stations=syn.stations_table(), mass_balance=False)
        sd = report.sensor_day("B", D2)
        assert _checks(report, "B", D2) == {"zero_run": "suspect"}
        assert "occupancy not reported" in sd.findings[0].reason
        assert sd.stats["zero_run_windows"] == 6
        queued = syn.set_values(
            frame, station="A", date=D2, start_h=17.0, end_h=17.5,
            flow_veh_h=1800.0, speed_ms=3.0,
        )  # fmt: skip
        report = assess_quality(queued, stations=syn.stations_table(), mass_balance=False)
        assert "zero_run" not in _checks(report, "B", D2)

    def test_a_daytime_zero_run_is_flagged_and_a_night_one_is_not(
        self, clean: pd.DataFrame
    ) -> None:
        frame = syn.set_values(
            clean, station="R1", date=D2, start_h=7.0, end_h=9.0,
            flow_veh_h=0.0, occupancy_pct=0.0, speed_ms=np.nan,
        )  # fmt: skip
        frame = syn.set_values(
            frame, station="R1", date=D3, start_h=2.0, end_h=2.5,
            flow_veh_h=0.0, occupancy_pct=0.0, speed_ms=np.nan,
        )  # fmt: skip
        report = assess_quality(frame, stations=syn.stations_table())
        assert _checks(report, "R1", D2)["zero_run"] == "suspect"
        assert report.sensor_day("R1", D2).stats["zero_run_windows"] == 24
        assert "zero_run" not in _checks(report, "R1", D3)

    def test_a_dead_ramp_detector_is_excluded(self, clean: pd.DataFrame) -> None:
        frame = syn.set_values(
            clean, station="R2", date=D1, start_h=0.0, end_h=24.0,
            flow_veh_h=0.0, occupancy_pct=0.0, speed_ms=np.nan,
        )  # fmt: skip
        report = assess_quality(frame, stations=syn.stations_table())
        assert _checks(report, "R2", D1) == {"zero_day": "exclude"}

    def test_a_near_dead_detector_is_low_flow(self, clean: pd.DataFrame) -> None:
        frame = syn.set_values(
            clean, station="R1", date=D4, start_h=0.0, end_h=24.0, flow_veh_h=0.0
        )
        frame = syn.set_values(
            frame, station="R1", date=D4, start_h=12.0, end_h=12.1, flow_veh_h=240.0
        )
        report = assess_quality(frame, stations=syn.stations_table())
        assert _checks(report, "R1", D4)["low_flow"] == "suspect"


class TestDayOutlier:
    def test_a_halved_day_is_excluded_and_a_quarter_down_is_suspect(
        self, clean: pd.DataFrame
    ) -> None:
        frame = syn.scale_values(clean, station="C", date=D4, factor=0.5)
        frame = syn.scale_values(frame, station="A", date=D3, factor=0.72)
        report = assess_quality(frame, stations=syn.stations_table(), mass_balance=False)
        assert _checks(report, "C", D4)["day_outlier"] == "exclude"
        # the median day includes the planted one, so the ratio sits a little above 0.5
        assert report.sensor_day("C", D4).stats["day_ratio_relative"] == pytest.approx(
            0.5, abs=0.05
        )
        assert _checks(report, "A", D3)["day_outlier"] == "suspect"

    def test_a_corridor_wide_low_day_is_reported_not_flagged(self, clean: pd.DataFrame) -> None:
        frame = clean
        for station in ("A", "B", "C", "R1", "R2"):
            frame = syn.scale_values(frame, station=station, date=D1, factor=0.7)
        report = assess_quality(frame, stations=syn.stations_table())
        factor = report.corridor_day_factors[D1]
        assert factor["unusual"] is True
        assert factor["factor"] == pytest.approx(0.7, abs=0.05)
        assert all("day_outlier" not in _checks(report, s, D1) for s in ("A", "B", "C"))


class TestLaneImbalance:
    def test_a_degraded_lane_is_excluded_and_its_neighbours_kept(
        self, clean_lanes: pd.DataFrame
    ) -> None:
        frame = _degrade(clean_lanes, station="B", lane="3", factor=0.25)
        report = assess_quality(frame, stations=syn.stations_table())
        for date in syn.DATES:
            assert _checks(report, "B:3", date).get("lane_imbalance") == "exclude"
            assert "lane_imbalance" not in _checks(report, "B:1", date)
            assert "lane_imbalance" not in _checks(report, "B:2", date)

    def test_an_exempt_lane_is_not_judged(self, clean_lanes: pd.DataFrame) -> None:
        frame = _degrade(clean_lanes, station="B", lane="3", factor=0.25)
        report = assess_quality(frame, stations=syn.stations_table(), exempt_lanes=["B:3"])
        assert all("lane_imbalance" not in _checks(report, "B:3", d) for d in syn.DATES)
        assert report.sensor_day("B:3", D1).stats["lane_ratio"] < 0.4

    def test_at_a_two_lane_station_an_imbalance_is_only_suspect(
        self, clean_lanes: pd.DataFrame
    ) -> None:
        frame = clean_lanes[~((clean_lanes["station"] == "C") & (clean_lanes["lane"] == "1"))]
        frame = frame.reset_index(drop=True)
        frame.attrs = dict(clean_lanes.attrs)
        frame = _degrade(frame, station="C", lane="3", factor=0.2)
        # this light synthetic station rarely reaches the default 600 veh/h/lane floor
        rules = QualityThresholds(lane_min_flow_veh_h_lane=300.0)
        report = assess_quality(
            frame, stations=syn.stations_table(), mass_balance=False, thresholds=rules
        )
        assert _checks(report, "C:3", D1)["lane_imbalance"] == "suspect"
        assert _checks(report, "C:2", D1)["lane_imbalance"] == "suspect"

    def test_unknown_exempt_lane_is_refused(self, clean_lanes: pd.DataFrame) -> None:
        with pytest.raises(ValueError, match="not sensors"):
            assess_quality(clean_lanes, exempt_lanes=["Z:9"])


class TestMassBalance:
    def test_an_undercounting_station_is_named_by_its_two_segments(
        self, clean: pd.DataFrame
    ) -> None:
        frame = syn.scale_values(clean, station="B", factor=0.85)
        report = assess_quality(frame, stations=syn.stations_table())
        above = [m for m in report.mass_balance if m.upstream == "A"]
        below = [m for m in report.mass_balance if m.upstream == "B"]
        assert all(m.verdict == "suspect" for m in above + below)
        assert all((m.residual_share or 0.0) < -0.1 for m in above)
        assert all((m.residual_share or 0.0) > 0.1 for m in below)
        assert {a["station"] for a in report.attributions} == {"B"}
        assert all(a["direction"] == "undercounts" for a in report.attributions)
        assert all(s.persistent for s in report.segments)
        assert all(
            _checks(report, "B", d).get("mass_balance_pattern") == "suspect" for d in syn.DATES
        )
        assert all("mass_balance_pattern" not in _checks(report, "A", d) for d in syn.DATES)

    def test_a_missing_ramp_count_is_explained_by_the_unmeasured_ramp(
        self, clean: pd.DataFrame
    ) -> None:
        frame = clean[clean["station"] != "R1"].reset_index(drop=True)
        frame.attrs = dict(clean.attrs)
        report = assess_quality(frame, stations=syn.stations_table())
        above = [m for m in report.mass_balance if m.upstream == "A"]
        assert all(m.verdict == "ok" and m.unmeasured_ramps == ("R1",) for m in above)
        assert all((m.residual_veh_h or 0.0) > 100.0 for m in above)

    def test_an_inventory_without_the_ramp_flags_the_segment(self, clean: pd.DataFrame) -> None:
        frame = clean[clean["station"] != "R1"].reset_index(drop=True)
        frame.attrs = dict(clean.attrs)
        table = syn.stations_table()
        table = table[table["station"] != "R1"]
        report = assess_quality(frame, stations=table)
        above = [m for m in report.mass_balance if m.upstream == "A"]
        assert all(m.verdict == "suspect" for m in above)
        assert report.segments[0].persistent
        assert not report.attributions

    def test_a_declared_unmeasured_exit_with_a_positive_residual_is_a_bad_detector(
        self, clean: pd.DataFrame
    ) -> None:
        frame = clean[clean["station"] != "R1"].reset_index(drop=True)
        frame.attrs = dict(clean.attrs)
        table = syn.stations_table()
        table = table[table["station"] != "R1"]
        report = assess_quality(frame, stations=table, extra_ramps=[parse_ramp("X:off:400")])
        above = [m for m in report.mass_balance if m.upstream == "A"]
        assert all(m.verdict == "suspect" and m.unmeasured_ramps == ("X",) for m in above)


class TestOutput:
    def test_json_keys_are_stable_and_round_trip(self, clean: pd.DataFrame) -> None:
        frame = syn.set_values(
            clean, station="A", date=D2, start_h=10.0, end_h=11.0, flow_veh_h=2400.0
        )
        report = assess_quality(frame, stations=syn.stations_table())
        text = report.to_json()
        assert text == assess_quality(frame, stations=syn.stations_table()).to_json()
        payload = json.loads(text)
        assert payload["schema"] == QUALITY_SCHEMA
        assert set(payload) == {
            "corridor_day_factors",
            "grid",
            "lane_order",
            "mass_balance",
            "method",
            "notes",
            "parameters",
            "schema",
            "sensor_days",
            "sensors",
            "summary",
        }
        first = payload["sensor_days"][0]
        assert set(first) == {
            "date", "findings", "kind", "lane", "masked", "n_expected", "n_usable",
            "n_valid", "sensor", "station", "stats", "verdict",
        }  # fmt: skip
        assert set(first["masked"]) == {"flow", "occupancy", "speed"}
        assert all(set(sd["stats"]) == set(first["stats"]) for sd in payload["sensor_days"])
        assert set(payload["method"]["checks"]) == set(CHECKS)
        assert set(payload["parameters"]["thresholds"]) == set(THRESHOLD_SOURCES)
        assert payload["method"]["imputation"].startswith("none")
        # the count error the report assumed, at the key the uncertainty runner reads
        assert payload["parameters"]["count_error"] == 0.05
        assert payload["parameters"]["combination"] == "linear"

    def test_every_threshold_has_a_source(self) -> None:
        assert set(QualityThresholds().to_dict()) == set(THRESHOLD_SOURCES)
        assert all(text.strip() for text in THRESHOLD_SOURCES.values())

    def test_markdown_names_what_was_dropped_and_why(self, clean: pd.DataFrame) -> None:
        frame = syn.drop_rows(clean, station="R1", date=D3)
        report = assess_quality(frame, stations=syn.stations_table())
        text = render_markdown(report, provenance={"Inputs": "synthetic"})
        assert "## Detectors dropped" in text
        assert "| R1 | on_ramp | 1 of 4 |" in text
        assert "missing" in text
        assert "Nothing was filled in" in text
        assert "**Inputs**: synthetic" in text


class TestLaneColumnLoader:
    def test_a_per_lane_csv_keeps_its_lane_column(
        self, clean_lanes: pd.DataFrame, tmp_path: Path
    ) -> None:
        out = clean_lanes.copy()
        out["timestamp"] = [t.isoformat() for t in out["timestamp"]]
        out = out.rename(columns={"lane": "LaneNo"})
        path = tmp_path / "lanes.csv"
        out.to_csv(path, index=False)
        frame = load_detector_csv(path, lane_column="laneno")
        assert list(frame.columns)[-1] == "lane"
        assert set(frame.loc[frame["station"] == "A", "lane"]) == {"1", "2", "3"}
        assert detector_grid(frame).per_lane
        plain = load_detector_csv(path)
        assert "lane" not in plain.columns

    def test_a_missing_lane_column_is_named(self, clean: pd.DataFrame, tmp_path: Path) -> None:
        out = clean.copy()
        out["timestamp"] = [t.isoformat() for t in out["timestamp"]]
        path = tmp_path / "stations.csv"
        out.to_csv(path, index=False)
        with pytest.raises(ValueError, match="lane_column 'Lane'"):
            load_detector_csv(path, lane_column="Lane")


class TestLaneOrder:
    """The lane-order check (module docstring, "Lane order"; docs/I94_LANE_SHARES.md §3).

    ``synthetic_detectors.lane_order_frame``: 30-second per-lane data for five
    three-lane stations and one four-lane station (``W``) whose lane use
    follows the flow (light traffic keeps right), with optional stations whose
    labels are reversed.
    """

    @staticmethod
    def _report(frame: pd.DataFrame, **kw: object):
        return assess_quality(
            frame, stations=syn.lane_order_stations_table(), mass_balance=False, **kw
        )

    @staticmethod
    def _verdicts(report) -> dict[str, str]:
        return {st.station: st.verdict for st in report.lane_order}

    @staticmethod
    def _lane_order_findings(report) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for sd in report.sensor_days:
            for f in sd.findings:
                if f.check == "lane_order":
                    out.setdefault(sd.station, []).append(f.verdict)
        return out

    def test_a_normal_corridor_is_not_flagged(self) -> None:
        report = self._report(syn.lane_order_frame(seed=0))
        assert self._verdicts(report) == {
            "S1": "ok", "S2": "ok", "S3": "ok", "W": "not_checkable", "S4": "ok", "S5": "ok",
        }  # fmt: skip
        assert self._lane_order_findings(report) == {}
        assert report.lanes_reversed() == []
        s3 = next(st for st in report.lane_order if st.station == "S3")
        assert [p.neighbour for p in s3.pairs] == ["S2", "S4"]
        assert all(
            p.reading == "consistent" and (p.direct or 0) > 0.4 and (p.mirrored or 0) < -0.4
            for p in s3.pairs
        )
        # the supporting signatures: the right lane is slower and heavier and
        # carries the light traffic
        assert s3.occupancy_per_vehicle_s[1] > s3.occupancy_per_vehicle_s[3]
        assert s3.light_share[1] > s3.light_share[3]
        assert s3.support == {
            "occupancy_per_vehicle": "as_labelled",
            "light_traffic_share": "as_labelled",
        }

    def test_a_reversed_station_is_flagged_and_marked_for_remapping(self) -> None:
        report = self._report(syn.lane_order_frame(seed=0, reversed_stations=("S3",)))
        verdicts = self._verdicts(report)
        assert verdicts["S3"] == "reversed"
        # its neighbours read mirrored against it, explained by its reversal
        assert verdicts["S2"] == verdicts["S4"] == "ok"
        assert report.lanes_reversed() == ["S3"]
        s3 = next(st for st in report.lane_order if st.station == "S3")
        assert all(p.reading == "mirrored" for p in s3.pairs) and len(s3.pairs) == 2
        assert set(s3.support.values()) == {"reversed"}
        assert s3.sensors_by_lane == {1: "S3:1", 2: "S3:2", 3: "S3:3"}
        assert "lanes_reversed" in s3.reason and "lane 4 - k" in s3.reason
        # every lane of S3 on every date: suspect, with nothing masked (the counts are good)
        findings = self._lane_order_findings(report)
        assert list(findings) == ["S3"] and findings["S3"] == ["suspect"] * 9
        for sd in report.sensor_days:
            if sd.station == "S3":
                assert sd.verdict == "suspect" and sd.masked == {
                    q: () for q in ("flow", "occupancy", "speed")
                }
                assert sd.n_usable == sd.n_valid
        # JSON and the verdicts a consumer reads
        payload = json.loads(report.to_json())
        assert payload["lane_order"]["lanes_reversed"] == ["S3"]
        rec = next(r for r in payload["lane_order"]["stations"] if r["station"] == "S3")
        assert rec["verdict"] == "reversed" and rec["sensors_by_lane"] == {
            "1": "S3:1",
            "2": "S3:2",
            "3": "S3:3",
        }
        verdicts_ = QualityVerdicts.from_dict(payload)
        assert verdicts_.lanes_reversed == frozenset({"S3"})
        assert verdicts_.lane_order["S2"]["verdict"] == "ok"
        # masking a frame with the report sets nothing aside for it
        frame = syn.lane_order_frame(seed=0, reversed_stations=("S3",))
        masked = mask_frame(frame, report)
        assert not any(sd.station == "S3" for sd in masked.masked_sensor_days)
        text = render_markdown(report)
        assert "## Lane order" in text and "| S3 | reversed | yes |" in text

    def test_an_end_station_with_one_anchored_neighbour_is_reversed(self) -> None:
        report = self._report(syn.lane_order_frame(seed=0, reversed_stations=("S1",)))
        assert self._verdicts(report)["S1"] == "reversed"
        assert report.lanes_reversed() == ["S1"]
        assert self._verdicts(report)["S2"] == "ok"

    @pytest.mark.parametrize(("reversed_station", "neighbour"), [("S2", "S1"), ("S4", "S5")])
    def test_an_end_station_beside_a_reversed_one_reads_through_it(
        self, reversed_station: str, neighbour: str
    ) -> None:
        """Review 2026-10-07: a correctly labelled end station whose only clear neighbour is
        the station found reversed was flagged uncertain (and its sensor-days suspect). Its
        mirrored reading is the reversed neighbour's: read through the remapped lanes, it
        is ok and nothing of it is flagged."""
        report = self._report(syn.lane_order_frame(seed=0, reversed_stations=(reversed_station,)))
        verdicts = self._verdicts(report)
        assert verdicts[reversed_station] == "reversed"
        assert verdicts[neighbour] == "ok"
        assert report.lanes_reversed() == [reversed_station]
        assert list(self._lane_order_findings(report)) == [reversed_station]
        st = next(s for s in report.lane_order if s.station == neighbour)
        assert f"read through the reversed labels of {reversed_station}" in st.reason
        assert not st.lanes_reversed

    def test_a_pair_no_third_station_anchors_is_never_remapped(self) -> None:
        """Two comparable stations that disagree: the pair alone cannot say
        which is reversed. The corridor's other stations of that lane count
        could single one out; with none, both are flagged and neither remapped."""
        stations = (("S1", 0.0, 3), ("S2", 800.0, 3), ("W", 2000.0, 4))
        frame = syn.lane_order_frame(seed=0, reversed_stations=("S2",), stations=stations)
        report = assess_quality(
            frame, stations=syn.lane_order_stations_table(stations), mass_balance=False
        )
        assert self._verdicts(report) == {
            "S1": "uncertain",
            "S2": "uncertain",
            "W": "not_checkable",
        }
        assert report.lanes_reversed() == []
        assert set(self._lane_order_findings(report)) == {"S1", "S2"}
        s2 = next(st for st in report.lane_order if st.station == "S2")
        assert "not anchored by a third station" in s2.reason and not s2.lanes_reversed

    def test_without_supporting_signatures_a_mirrored_station_is_only_uncertain(self) -> None:
        rules = QualityThresholds(lane_order_min_contrast=10.0)  # no signature reads a direction
        report = self._report(
            syn.lane_order_frame(seed=0, reversed_stations=("S3",)), thresholds=rules
        )
        assert self._verdicts(report)["S3"] == "uncertain"
        assert report.lanes_reversed() == []
        s3 = next(st for st in report.lane_order if st.station == "S3")
        assert "no supporting signature" in s3.reason
        # S2 and S4 read mirrored against an unremapped S3: flagged too, never remapped
        assert self._verdicts(report)["S2"] == self._verdicts(report)["S4"] == "uncertain"

    def test_a_station_without_comparable_neighbours_is_not_checkable(self) -> None:
        # W is reversed too, and the only four-lane station: not checkable, not flagged
        report = self._report(syn.lane_order_frame(seed=0, reversed_stations=("W",)))
        w = next(st for st in report.lane_order if st.station == "W")
        assert (w.verdict, w.lanes_reversed, w.pairs) == ("not_checkable", False, ())
        assert "no station with 4 lanes" in w.reason
        assert "W" not in self._lane_order_findings(report)

    def test_detector_named_lanes_need_their_lane_numbers(self) -> None:
        """MnDOT-style lane ids (detector names) are not lane numbers: without
        ``lane_numbers`` nothing is checkable (and the report says so); with
        them the check runs as on numbered lanes."""
        frame = syn.lane_order_frame(seed=0, reversed_stations=("S3",), lane_ids="detector")
        bare = self._report(frame)
        assert set(self._verdicts(bare).values()) == {"not_checkable"}
        assert any("lane order not checked" in n for n in bare.notes)
        numbered = self._report(frame, lane_numbers=syn.lane_order_numbers())
        assert self._verdicts(numbered)["S3"] == "reversed"
        assert numbered.lanes_reversed() == ["S3"]
        s3 = next(st for st in numbered.lane_order if st.station == "S3")
        assert s3.sensors_by_lane[1] == "S3:S3d1"

    def test_an_incomplete_station_is_not_checkable(self) -> None:
        frame = syn.lane_order_frame(seed=0)
        frame = frame[~((frame["station"] == "S4") & (frame["lane"] == "2"))].reset_index(drop=True)
        frame.attrs["interval_s"] = 30.0
        report = self._report(frame)
        s4 = next(st for st in report.lane_order if st.station == "S4")
        assert s4.verdict == "not_checkable" and "numbered 1, 3, not exactly 1..3" in s4.reason
        # its neighbours skip it and pair across it
        s3 = next(st for st in report.lane_order if st.station == "S3")
        assert [p.neighbour for p in s3.pairs] == ["S2", "S5"]

    def test_station_totals_skip_the_check(self, clean: pd.DataFrame) -> None:
        report = assess_quality(clean, stations=syn.stations_table())
        assert report.lane_order == ()
        assert any("lane-order checks need per-lane data" in n for n in report.notes)
        grid = detector_grid(syn.lane_order_frame(seed=0))
        assert lane_order_check(grid)  # the public function on a per-lane grid
