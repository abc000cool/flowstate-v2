"""Per-lane station totals: one rule set (calibration.lane_totals), used by
calibration.conservation.station_grid and calibration.day_split.station_totals.

Synthetic per-lane frames only (lane ids are detector names, as in the MnDOT
per-lane reader). Regression tests for the 2026-10-07 review:

* an installed lane excluded by name (no rows) must leave its station without
  a total in conservation.station_grid, not with the sum of its other lanes;
* day_split.station_totals must drop a never-reporting placeholder lane (it
  made the station NaN in every window) and must treat a lane missing from a
  window as missing (it gave a partial sum);
* Juneteenth is a federal holiday only from 2021.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from calibration.conservation import detector_grid, silent_lane_note, station_grid
from calibration.data_quality import QualityVerdicts
from calibration.day_split import (
    JUNETEENTH_FIRST_YEAR,
    build_day_split,
    calendar_reasons,
    holidays_for,
    station_totals,
    us_federal_holidays,
)
from calibration.lane_totals import (
    EXCLUDED_DETECTORS_ATTR,
    EXCLUDED_LANES_ATTR,
    attribute_exclusions,
    complete_sum,
    counted_lanes,
    frame_exclusions,
    station_lanes,
)

TZ = timezone(timedelta(hours=-5))
# Tuesday to Thursday of three weeks (Labor Day, 7 Sept 2026, is a Monday).
DATES = (
    "2026-09-01",
    "2026-09-02",
    "2026-09-03",
    "2026-09-08",
    "2026-09-09",
    "2026-09-10",
    "2026-09-15",
    "2026-09-16",
    "2026-09-17",
)
LANES: dict[str, tuple[str, ...]] = {
    "S1": ("101", "102", "103"),
    "S2": ("201", "202", "203"),
    "S3": ("301", "302"),
    "S4": ("401", "402", "403"),
    "S5": ("501", "502", "503"),
}
X_M = {"S1": 0.0, "S2": 700.0, "S3": 1500.0, "S4": 2300.0, "S5": 3100.0}
WINDOW_S = 300
N_WINDOWS = 48  # 05:00 .. 08:55
START, END = "06:00", "08:00"


def _flow(day: int, station: str, lane: int, k: int) -> float:
    return 400.0 + 50.0 * lane + 10.0 * (k % 3) + 7.0 * day + 3.0 * int(station[1:])


def per_lane_frame(
    dates: tuple[str, ...] = DATES[:1],
    lanes: Mapping[str, tuple[str, ...]] | None = None,
) -> pd.DataFrame:
    """Per-lane rows 05:00–08:55 local (UTC−5), every lane of every station reporting."""
    rows = []
    for di, d in enumerate(dates):
        midnight = datetime.fromisoformat(d).replace(tzinfo=TZ)
        for k in range(N_WINDOWS):
            stamp = midnight + timedelta(seconds=5 * 3600 + k * WINDOW_S)
            for station, ids in (lanes or LANES).items():
                for li, lane in enumerate(ids):
                    rows.append(
                        {
                            "timestamp": stamp,
                            "station": station,
                            "flow_veh_h": _flow(di, station, li, k),
                            "occupancy_pct": 8.0 + li,
                            "speed_ms": 27.0 + 0.5 * li,
                            "lanes": 1,
                            "kind": "mainline",
                            "x_m": X_M[station],
                            "lane": lane,
                        }
                    )
    frame = pd.DataFrame(rows)
    frame.attrs["interval_s"] = float(WINDOW_S)
    return frame


def with_placeholder(frame: pd.DataFrame, station: str, lane: str) -> pd.DataFrame:
    """Add an all-NaN lane (an inventory placeholder that never reports) to a station."""
    first = frame.loc[frame["station"] == station, "lane"].iloc[0]
    extra = frame[(frame["station"] == station) & (frame["lane"] == first)].copy()
    extra["lane"] = lane
    extra[["flow_veh_h", "occupancy_pct", "speed_ms"]] = np.nan
    out = pd.concat([frame, extra], ignore_index=True)
    out.attrs = dict(frame.attrs)
    return out


def without_lane(frame: pd.DataFrame, station: str, lane: str) -> pd.DataFrame:
    """Drop every row of one lane, as the MnDOT per-lane reader drops an excluded detector."""
    out = frame[~((frame["station"] == station) & (frame["lane"] == lane))].copy()
    out.attrs = dict(frame.attrs)
    return out


def _totals_by_station(totals: pd.DataFrame) -> dict[str, np.ndarray]:
    out = {}
    for station, group in totals.groupby("station"):
        order = np.argsort([t.timestamp() for t in group["timestamp"]])
        out[str(station)] = group["flow_veh_h"].to_numpy(dtype=float)[order]
    return out


def _station_sum(frame: pd.DataFrame, station: str, lanes: tuple[str, ...]) -> np.ndarray:
    part = frame[(frame["station"] == station) & frame["lane"].isin(lanes)]
    return part.groupby("timestamp", sort=True)["flow_veh_h"].sum().to_numpy(dtype=float)


# ---------------------------------------------------------------------------
# The rules themselves
# ---------------------------------------------------------------------------


class TestRules:
    def test_placeholders_leave_the_lane_set_unless_every_lane_is_one(self) -> None:
        got = station_lanes("S", ["a", "b", "T9"], {"T9"})
        assert got.counted == ("a", "b") and got.placeholders == ("T9",) and got.n_lanes == 2
        dead = station_lanes("S", ["T8", "T9"], {"T8", "T9"})
        assert dead.counted == ("T8", "T9") and dead.placeholders == ()

    def test_an_excluded_lane_is_counted_and_never_a_placeholder(self) -> None:
        got = station_lanes("S", ["a", "b"], set(), excluded=["c"])
        assert got.counted == ("a", "b", "c") and got.excluded == ("c",)
        assert got.measured == ("a", "b")
        # delivered but excluded: still excluded, even when it never reported
        also = station_lanes("S", ["a", "c"], {"c"}, excluded=["c"])
        assert also.counted == ("a", "c") and also.placeholders == ()
        # every delivered lane a placeholder, one excluded: only the excluded counts
        only = station_lanes("S", ["T9"], {"T9"}, excluded=["c"])
        assert only.counted == ("c",) and only.placeholders == ("T9",)

    def test_a_window_is_a_total_only_when_every_counted_lane_is_finite(self) -> None:
        values = {"a": np.array([1.0, 2.0, np.nan]), "b": np.array([10.0, 20.0, 30.0])}
        both = station_lanes("S", ["a", "b"], set())
        assert np.array_equal(complete_sum(both, values), [11.0, 22.0, np.nan], equal_nan=True)
        lost = station_lanes("S", ["a", "b"], set(), excluded=["c"])
        assert np.isnan(complete_sum(lost, values)).all()
        only_excluded = station_lanes("S", ["T9"], {"T9"}, excluded=["c"])
        assert np.isnan(complete_sum(only_excluded, {"T9": np.full(3, np.nan)})).all()

    def test_attribute_exclusions_maps_names_to_their_stations(self) -> None:
        got = attribute_exclusions({"S792": ["3238", "3239", "3240"], "R1": ["9"]}, ["3240"])
        assert got == {"S792": ["3240"]}

    def test_the_data_quality_report_applies_the_same_placeholder_rule(self) -> None:
        """QualityVerdicts.station_sensors (read-only reference) == counted_lanes."""
        days = []
        for d in DATES[:2]:
            for lane, n_valid in (("201", 24), ("202", 24), ("T9", 0)):
                days.append(
                    {"sensor": f"S2:{lane}", "station": "S2", "lane": lane, "date": d,
                     "verdict": "exclude" if lane == "T9" else "ok", "n_valid": n_valid}
                )  # fmt: skip
            for lane in ("T1", "T2"):
                days.append(
                    {"sensor": f"S9:{lane}", "station": "S9", "lane": lane, "date": d,
                     "verdict": "exclude", "n_valid": 0}
                )  # fmt: skip
        verdicts = QualityVerdicts.from_dict(
            {
                "schema": "flowstate.data_quality/1",
                "grid": {"start_local": START, "end_local": END, "dates": list(DATES[:2])},
                "sensor_days": days,
            }
        )
        silent = verdicts.silent_lanes()
        members = verdicts.station_sensors()
        delivered = {"S2": ["S2:201", "S2:202", "S2:T9"], "S9": ["S9:T1", "S9:T2"]}
        for station, lanes in delivered.items():
            assert members[station] == tuple(sorted(counted_lanes(lanes, silent)))
        assert not verdicts.station_days()[("S2", DATES[0])].excluded


# ---------------------------------------------------------------------------
# conservation.station_grid (finding 1)
# ---------------------------------------------------------------------------


class TestStationGridExclusions:
    def test_an_excluded_lane_leaves_its_station_without_a_total(self) -> None:
        """The I-94 S792 case: lane_frame drops excluded detector 3240's rows."""
        frame = without_lane(per_lane_frame(), "S2", "203")
        frame.attrs[EXCLUDED_DETECTORS_ATTR] = ["203"]
        frame.attrs[EXCLUDED_LANES_ATTR] = {"S2": ["203"]}
        grid = detector_grid(frame)
        assert grid.excluded_lanes == {"S2": ("S2:203",)}
        totals = station_grid(grid)
        assert totals.sensors["S2"].lanes == 3
        assert np.isnan(totals.flow_veh_h["S2"]).all()
        assert np.isnan(totals.occupancy_pct["S2"]).all()
        # the speed is a flow-weighted mean, not a sum: the reporting lanes still give it
        assert np.isfinite(totals.speed_ms["S2"]).all()
        assert np.isfinite(totals.flow_veh_h["S1"]).all()
        assert totals.flow_veh_h["S1"][0] == pytest.approx(_station_sum(frame, "S1", LANES["S1"]))
        note = silent_lane_note(grid) or ""
        assert "S2:203" in note and "excluded by name" in note

    def test_an_exclusion_without_a_station_is_refused(self) -> None:
        frame = without_lane(per_lane_frame(), "S2", "203")
        frame.attrs[EXCLUDED_DETECTORS_ATTR] = ["203"]
        grid = detector_grid(frame)  # the per-lane checks can still run
        assert grid.unattributed_exclusions == frozenset({"203"})
        with pytest.raises(ValueError, match="excluded by name"):
            station_grid(grid)
        # masking keeps the record, so the masked grid is refused as well
        masked = grid.replace_values(grid.flow_veh_h, grid.occupancy_pct, grid.speed_ms)
        with pytest.raises(ValueError, match="does not say which"):
            station_grid(masked)

    def test_the_caller_can_attribute_the_exclusion(self) -> None:
        frame = without_lane(per_lane_frame(), "S2", "203")
        frame.attrs[EXCLUDED_DETECTORS_ATTR] = ["203"]
        grid = detector_grid(frame, excluded_lanes={"S2": ["203"]})
        assert not grid.unattributed_exclusions
        masked = grid.replace_values(grid.flow_veh_h, grid.occupancy_pct, grid.speed_ms)
        assert np.isnan(station_grid(masked).flow_veh_h["S2"]).all()

    def test_an_excluded_name_that_is_still_a_lane_of_the_frame_is_attributed(self) -> None:
        frame = per_lane_frame()
        frame.attrs[EXCLUDED_DETECTORS_ATTR] = ["203"]
        assert frame_exclusions(frame).by_station == {"S2": ("203",)}
        assert np.isnan(station_grid(detector_grid(frame)).flow_veh_h["S2"]).all()

    def test_a_station_frame_ignores_the_loaders_exclusion_record(self) -> None:
        """Station totals of calibration.loaders.mndot carry their own (scaled) record."""
        frame = per_lane_frame().drop(columns=["lane"])
        frame = frame.groupby(["timestamp", "station"], as_index=False).agg(
            flow_veh_h=("flow_veh_h", "sum"), kind=("kind", "first"), x_m=("x_m", "first")
        )
        frame.attrs = {"interval_s": float(WINDOW_S), EXCLUDED_DETECTORS_ATTR: ["203"]}
        grid = detector_grid(frame)
        assert station_grid(grid) is grid and not grid.unattributed_exclusions


# ---------------------------------------------------------------------------
# day_split.station_totals (finding 2)
# ---------------------------------------------------------------------------


class TestDaySplitTotals:
    def test_a_placeholder_lane_does_not_blank_its_station(self) -> None:
        frame = with_placeholder(per_lane_frame(), "S2", "T9")
        totals = _totals_by_station(station_totals(frame))
        assert np.isfinite(totals["S2"]).all()
        assert totals["S2"] == pytest.approx(_station_sum(frame, "S2", LANES["S2"]))

    def test_a_missing_lane_row_makes_the_window_missing(self) -> None:
        frame = per_lane_frame()
        stamps = sorted(set(frame["timestamp"]))
        gone = (frame["station"] == "S3") & (frame["lane"] == "302")
        gone &= frame["timestamp"].isin(stamps[10:12])
        cut = frame[~gone].copy()
        cut.attrs = dict(frame.attrs)
        totals = _totals_by_station(station_totals(cut))
        assert np.isnan(totals["S3"][10:12]).all()
        assert np.isfinite(np.delete(totals["S3"], [10, 11])).all()

    def test_an_excluded_lane_leaves_no_total_and_an_unattributed_one_is_refused(self) -> None:
        frame = without_lane(per_lane_frame(), "S2", "203")
        frame.attrs[EXCLUDED_DETECTORS_ATTR] = ["203"]
        with pytest.raises(ValueError, match="excluded by name"):
            station_totals(frame)
        totals = _totals_by_station(station_totals(frame, excluded_lanes={"S2": ["203"]}))
        assert np.isnan(totals["S2"]).all() and np.isfinite(totals["S1"]).all()

    def test_the_placeholder_period_is_the_study_period(self) -> None:
        """A lane that reports only outside the period is not a lane in it (the report's rule)."""
        frame = with_placeholder(per_lane_frame(), "S2", "T9")
        early = (frame["lane"] == "T9") & (frame["timestamp"] == frame["timestamp"].min())
        frame.loc[early, "flow_veh_h"] = 50.0  # 05:00, before the 06:00 start
        whole = _totals_by_station(station_totals(frame))
        assert np.isfinite(whole["S2"][0]) and np.isnan(whole["S2"][1:]).all()
        period = _totals_by_station(station_totals(frame, dates=DATES[:1], start=START, end=END))
        assert np.isfinite(period["S2"][1:]).all()
        with pytest.raises(ValueError, match="together"):
            station_totals(frame, start=START)

    def test_two_rows_for_one_lane_and_window_are_refused(self) -> None:
        frame = per_lane_frame()
        twice = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
        with pytest.raises(ValueError, match="two rows"):
            station_totals(twice)

    def test_station_frames_pass_through_unchanged(self) -> None:
        frame = per_lane_frame().drop(columns=["lane"])
        got = station_totals(frame)
        assert got.equals(frame[["timestamp", "station", "flow_veh_h", "kind"]])

    def test_day_split_and_conservation_give_the_same_station_totals(self) -> None:
        """One rule set: placeholder, missing rows, NaN readings and an exclusion together."""
        frame = with_placeholder(per_lane_frame(DATES[:2]), "S2", "T9")
        rng = np.random.default_rng(7)
        nan_rows = rng.choice(len(frame), size=40, replace=False)
        frame.loc[frame.index[nan_rows], "flow_veh_h"] = np.nan
        drop = rng.choice(len(frame), size=30, replace=False)
        attrs = dict(frame.attrs)
        frame = frame.drop(index=frame.index[drop]).reset_index(drop=True)
        frame = without_lane(frame, "S4", "403")
        frame.attrs = {**attrs, EXCLUDED_DETECTORS_ATTR: ["403"],
                       EXCLUDED_LANES_ATTR: {"S4": ["403"]}}  # fmt: skip
        grid = station_grid(detector_grid(frame))
        totals = station_totals(frame)
        secs = np.array([(t.hour * 3600 + t.minute * 60) for t in totals["timestamp"]])
        index = ((secs - 5 * 3600) // WINDOW_S).astype(int)
        day = np.array([DATES.index(t.date().isoformat()) for t in totals["timestamp"]])
        for station in LANES:
            mine = totals["station"] == station
            from_grid = grid.flow_veh_h[station][day[mine], index[mine]]
            assert np.array_equal(
                totals.loc[mine, "flow_veh_h"].to_numpy(dtype=float), from_grid, equal_nan=True
            ), station
        assert np.isnan(grid.flow_veh_h["S4"]).all()
        assert np.isfinite(grid.flow_veh_h["S2"]).sum() > 0


class TestDaySplitPerLane:
    def test_a_placeholder_lane_keeps_its_station_usable(self) -> None:
        frame = with_placeholder(per_lane_frame(DATES), "S2", "T9")
        split = build_day_split(frame, start=START, end=END)
        assert all(day.unusable_stations == () for day in split.days)
        assert all(day.n_usable == 5 for day in split.days)

    def test_the_80_percent_rule_counts_the_placeholder_station(self) -> None:
        """Reviewer's scenario: one genuinely missing station plus a placeholder lane elsewhere."""
        frame = with_placeholder(per_lane_frame(DATES), "S2", "T9")
        gone = (frame["station"] == "S5") & frame["timestamp"].map(
            lambda t: t.date().isoformat() == DATES[4]
        )
        frame.loc[gone, "flow_veh_h"] = np.nan
        split = build_day_split(frame, start=START, end=END)
        day = next(d for d in split.days if d.date == DATES[4])
        assert day.unusable_stations == ("S5",) and day.usable_share == 0.8
        assert day.candidate  # 4 of 5 = 80 %; the old sum made it 3 of 5

    def test_with_quality_verdicts_the_placeholder_station_has_a_volume(self) -> None:
        frame = with_placeholder(per_lane_frame(DATES[:3]), "S2", "T9")
        sensor_days = [
            {"sensor": f"{st}:{lane}", "station": st, "lane": lane, "kind": "mainline",
             "date": d, "verdict": "exclude" if lane == "T9" else "ok",
             "n_valid": 0 if lane == "T9" else 24}
            for d in DATES[:3]
            for st, ids in {**LANES, "S2": (*LANES["S2"], "T9")}.items()
            for lane in ids
        ]  # fmt: skip
        quality = {
            "schema": "flowstate.data_quality/1",
            "grid": {"start_local": START, "end_local": END, "dates": list(DATES[:3])},
            "sensor_days": sensor_days,
        }
        split = build_day_split(frame, start=START, end=END, quality=quality)
        for day in split.days:
            assert day.n_usable == 5 and day.unusable_stations == ()
            assert day.n_station_windows == 5 * 24


# ---------------------------------------------------------------------------
# Juneteenth (finding 3)
# ---------------------------------------------------------------------------


class TestJuneteenth:
    def test_not_a_federal_holiday_before_2021(self) -> None:
        assert JUNETEENTH_FIRST_YEAR == 2021
        for year in (2018, 2019, 2020):
            assert not any("Juneteenth" in name for name in us_federal_holidays(year).values())
        # Wednesday 19 June 2019 and Tuesday 19 June 2018 are ordinary candidate days
        for day in ("2019-06-19", "2018-06-19"):
            assert calendar_reasons(day, holidays_for([day]), {}) == []

    def test_a_federal_holiday_from_2021_with_the_observed_rule(self) -> None:
        h2021 = us_federal_holidays(2021)  # Saturday: observed Friday 18 June
        assert h2021["2021-06-19"] == "Juneteenth National Independence Day"
        assert h2021["2021-06-18"] == "Juneteenth National Independence Day (observed)"
        h2022 = us_federal_holidays(2022)  # Sunday: observed Monday 20 June
        assert h2022["2022-06-20"] == "Juneteenth National Independence Day (observed)"
        assert calendar_reasons("2025-06-19", holidays_for(["2025-06-19"]), {}) == [
            "federal holiday: Juneteenth National Independence Day"
        ]
