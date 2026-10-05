"""Observed targets masked by the data-quality verdicts (docs/FRISCO_PROTOCOL.md §2.2).

Synthetic corridors only (``synthetic_detectors.py``): a fault is planted, the
data-quality check judges it, and the observations artifact built with the
verdicts must not carry it — an excluded detector-day is dropped and a
``suspect`` window loses only the quantity its finding names, before the
dates are averaged — while ``source["quality"]`` records what was set aside.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from calibration.conservation import detector_grid
from calibration.data_quality import (
    QUALITY_MASK_RULE,
    QualityVerdicts,
    assess_quality,
    mask_frame,
    mask_grid,
)
from calibration.observations import Observations


def _load_synthetic() -> ModuleType:
    path = Path(__file__).parent / "synthetic_detectors.py"
    spec = importlib.util.spec_from_file_location("obs_quality_synthetic_detectors", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


syn = _load_synthetic()
D1, D2, D3, D4 = syn.DATES
STATIONS = [
    {"id": s["station"], "x_m": s["x_m"], "lanes": s["lanes"], "kind": s["kind"]}
    for s in syn.STATIONS
]
SPAN = {"window_s": 300.0, "t0_local": "16:00", "duration_s": 3 * 3600.0, "corridor": "syn"}
RECORD_KEYS = {
    "path",
    "sha256",
    "n_masked_sensor_days",
    "masked_sensor_days",
    "n_masked_windows",
    "rule",
}


@pytest.fixture(scope="module")
def clean() -> pd.DataFrame:
    return syn.daily_frame(seed=1)


def _obs(frame: pd.DataFrame, dates: list[str], quality=None) -> Observations:
    return Observations.from_frame(
        frame, STATIONS, dates=dates, quality=quality, source={"provider": "syn"}, **SPAN
    )


class TestMaskedTargets:
    def test_an_excluded_day_is_dropped_before_averaging(self, clean: pd.DataFrame) -> None:
        # a frozen counter at B all day on D2: excluded by the data-quality check
        bad = syn.set_values(
            clean, station="B", date=D2, start_h=0.0, end_h=24.0, flow_veh_h=7200.0
        )
        report = assess_quality(bad, stations=syn.stations_table(), mass_balance=False)
        assert report.sensor_day("B", D2).verdict == "exclude"
        masked = _obs(bad, [D1, D2, D3], report)
        left_out = _obs(bad, [D1, D3])
        unmasked = _obs(bad, [D1, D2, D3])
        assert masked.flows_veh_h["B"] == pytest.approx(left_out.flows_veh_h["B"])
        assert np.nanmean(unmasked.flows_veh_h["B"]) > 1.5 * np.nanmean(masked.flows_veh_h["B"])
        assert masked.quality["B"]["n_dates"] == 2.0 and masked.quality["A"]["n_dates"] == 3.0
        record = masked.source["quality"]
        assert set(record) == RECORD_KEYS and record["rule"] == QUALITY_MASK_RULE
        assert record["masked_sensor_days"] == [
            {"sensor": "B", "date": D2, "verdict": "exclude", "checks": ["stuck_flow"]}
        ]
        assert record["n_masked_sensor_days"] == 1
        assert record["n_masked_windows"] == 36  # 3 h of 5-minute readings
        assert record["path"] is None and len(record["sha256"]) == 64
        assert "quality" not in unmasked.source  # nothing given, nothing recorded

    def test_a_suspect_window_loses_only_the_quantity_it_names(self, clean: pd.DataFrame) -> None:
        # speed frozen at A on D3 from 14:00 to 18:00: speed masked, counts kept
        frame = syn.set_values(clean, station="A", date=D3, start_h=14.0, end_h=18.0, speed_ms=22.0)
        report = assess_quality(frame, stations=syn.stations_table(), mass_balance=False)
        sd = report.sensor_day("A", D3)
        assert sd.verdict == "suspect" and [f.check for f in sd.findings] == ["stuck_speed"]
        obs = _obs(frame, [D1, D2, D3], report)
        both = _obs(frame, [D1, D2])
        every = _obs(frame, [D1, D2, D3])
        # 16:00-18:00 (windows 0-23): speed from D1 and D2 only; 18:00-19:00 all three
        assert obs.speeds_ms["A"][:24] == pytest.approx(both.speeds_ms["A"][:24])
        assert obs.speeds_ms["A"][24:] == pytest.approx(every.speeds_ms["A"][24:])
        assert obs.flows_veh_h["A"] == pytest.approx(every.flows_veh_h["A"])
        record = obs.source["quality"]
        assert record["masked_sensor_days"] == [
            {"sensor": "A", "date": D3, "verdict": "suspect", "checks": ["stuck_speed"]}
        ]
        assert record["n_masked_windows"] == 24

    def test_per_lane_verdicts_mask_the_station_rows(self, clean: pd.DataFrame) -> None:
        lanes = syn.daily_frame(seed=2, per_lane=True)
        # a hanging-on loop in lane 3 of B on D2, 16:00-17:00
        lanes = syn.set_values(
            lanes, station="B", date=D2, start_h=16.0, end_h=17.0, lane="3",
            flow_veh_h=0.0, occupancy_pct=100.0, speed_ms=np.nan,
        )  # fmt: skip
        # a lane that never reports anything (a placeholder): not an installed lane
        silent = lanes[(lanes["station"] == "B") & (lanes["lane"] == "1")].copy()
        silent["lane"] = "4"
        silent[["flow_veh_h", "occupancy_pct", "speed_ms"]] = np.nan
        lanes = pd.concat([lanes, silent], ignore_index=True)
        lanes.attrs["interval_s"] = 300.0
        report = assess_quality(lanes, stations=syn.stations_table(), mass_balance=False)
        assert report.sensor_day("B:4", D1).verdict == "exclude"  # missing every reading
        verdicts = QualityVerdicts.from_report(report)
        assert verdicts.silent_lanes() == frozenset({"B:4"})
        obs = _obs(clean, [D1, D2, D3], verdicts)
        three = _obs(clean, [D1, D2, D3])
        two = _obs(clean, [D1, D3])
        # B's station rows of D2 16:00-17:00 are set aside; the silent lane excludes nothing
        assert obs.flows_veh_h["B"][:12] == pytest.approx(two.flows_veh_h["B"][:12])
        assert obs.flows_veh_h["B"][12:] == pytest.approx(three.flows_veh_h["B"][12:])
        assert obs.flows_veh_h["A"] == pytest.approx(three.flows_veh_h["A"])
        record = obs.source["quality"]
        assert record["masked_sensor_days"] == [
            {"sensor": "B:3", "date": D2, "verdict": "suspect", "checks": ["zero_flow_occupied"]}
        ]
        assert record["n_masked_windows"] == 12

    def test_readings_the_report_did_not_judge_are_refused(self, clean: pd.DataFrame) -> None:
        report = assess_quality(clean, dates=[D1, D2, D3], mass_balance=False)
        with pytest.raises(ValueError, match=r"\['2026-09-04'\] are not covered"):
            _obs(clean, [D1, D4], report)
        morning = assess_quality(clean, start_local="06:00", end_local="12:00", mass_balance=False)
        with pytest.raises(ValueError, match="outside the span"):
            _obs(clean, [D1], morning)
        no_c = assess_quality(clean[clean["station"] != "C"], mass_balance=False)
        with pytest.raises(ValueError, match="'C' is not among the sensors"):
            _obs(clean, [D1], no_c)
        # a report covering the span and dates (and more) is fine
        assert _obs(clean, [D1], report).source["quality"]["n_masked_sensor_days"] == 0


class TestMaskFrame:
    def test_the_frame_form_agrees_with_mask_grid(self, clean: pd.DataFrame) -> None:
        frame = syn.set_values(clean, station="A", date=D3, start_h=14.0, end_h=18.0, speed_ms=22.0)
        frame = syn.set_values(
            frame, station="R2", date=D1, start_h=0.0, end_h=24.0, flow_veh_h=0.0
        )
        report = assess_quality(frame, stations=syn.stations_table())
        expected = mask_grid(detector_grid(frame), report)
        got = detector_grid(mask_frame(frame, report).frame)
        for sid in expected.sensors:
            for name in ("flow_veh_h", "occupancy_pct", "speed_ms"):
                np.testing.assert_array_equal(
                    getattr(got, name)[sid], getattr(expected, name)[sid], err_msg=f"{sid} {name}"
                )

    def test_verdicts_read_from_the_json_file(self, clean: pd.DataFrame, tmp_path: Path) -> None:
        frame = syn.set_values(
            clean, station="B", date=D2, start_h=0.0, end_h=24.0, flow_veh_h=7200.0
        )
        report = assess_quality(frame, stations=syn.stations_table(), mass_balance=False)
        path = tmp_path / "data_quality.json"
        path.write_text(report.to_json())
        from_file = QualityVerdicts.from_json(path)
        in_memory = QualityVerdicts.from_report(report)
        assert from_file.sensor_days == in_memory.sensor_days
        assert (from_file.interval_s, from_file.start_s, from_file.n_windows) == (300.0, 0.0, 288)
        assert from_file.dates == tuple(syn.DATES) and from_file.path == str(path)
        assert from_file.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert from_file.positions["C"] == 2000.0
        obs = _obs(frame, [D1, D2], from_file)
        assert obs.source["quality"]["path"] == str(path)
        assert obs.source["quality"]["sha256"] == from_file.sha256
        json.dumps(obs.to_dict(), allow_nan=False)  # the record is plain JSON
        with pytest.raises(ValueError, match=r"flowstate\.data_quality/1"):
            QualityVerdicts.from_dict({"schema": "other/1", "sensor_days": []})
