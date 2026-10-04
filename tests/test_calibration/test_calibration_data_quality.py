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
    assess_quality,
    mask_grid,
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

    def test_a_standing_queue_is_not_flagged(self, clean: pd.DataFrame) -> None:
        frame = syn.set_values(
            clean, station="A", date=D3, start_h=14.0, end_h=14.5,
            flow_veh_h=0.0, occupancy_pct=60.0, speed_ms=1.0,
        )  # fmt: skip
        report = assess_quality(frame, stations=syn.stations_table())
        assert "zero_flow_occupied" not in _checks(report, "A", D3)

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


class TestZeroCounts:
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
