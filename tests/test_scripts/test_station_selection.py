"""Scoring-station selection (docs/FRISCO_PROTOCOL.md §2.2) and its use by the day split.

Synthetic data-quality payloads only: four mainline stations (per lane) and a
ramp over two weeks of November 2026, with the verdicts set by hand so every
station's share of non-excluded candidate days is known. Covers the candidate
screen (weekdays, federal holiday, the caller's exclusions — and not the
usable-station rule), the 80 % rule and its boundary, lanes that never
report, the ``selection.json`` block (additive, never silently replaced), and
the scripts ``scripts/station_selection.py`` and ``scripts/day_split.py
--selection``.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

from calibration.data_quality import QualityVerdicts
from calibration.station_selection import (
    MIN_NOT_EXCLUDED_SHARE,
    SELECTION_KEY,
    STATION_SELECTION_SCHEMA,
    StationSelection,
    read_selection,
    select_stations,
    selection_document,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
STATIONS = ("S1", "S2", "S3", "S4")
X_M = {"S1": 0.0, "S2": 600.0, "S3": 1300.0, "S4": 2000.0, "R1": 900.0}
# Mon 2 - Fri 13 November 2026; Wednesday 11 is Veterans Day
DATES = [
    "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06",
    "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13",
]  # fmt: skip
CANDIDATES = ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-10", "2026-11-12"]
START, END = "06:00", "08:00"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        f"flowstate_station_selection_{name}", SCRIPTS / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _quality(excluded: dict[tuple[str, str], str]) -> dict[str, Any]:
    """Per-lane verdicts: ``(sensor, date) → check`` excluded, everything else ok.

    S1 carries a fourth lane that never reports (``missing`` every day).
    """
    days = []
    sensors = []
    for sid in STATIONS:
        lanes = ["1", "2", "3"] + (["4"] if sid == "S1" else [])
        for lane in lanes:
            sensors.append(
                {"sensor": f"{sid}:{lane}", "station": sid, "lane": lane, "kind": "mainline",
                 "x_m": X_M[sid], "lanes": 1}
            )  # fmt: skip
            for d in DATES:
                silent = sid == "S1" and lane == "4"
                check = "missing" if silent else excluded.get((f"{sid}:{lane}", d))
                days.append(
                    {
                        "sensor": f"{sid}:{lane}", "station": sid, "lane": lane,
                        "kind": "mainline", "date": d,
                        "verdict": "exclude" if check else "ok",
                        "n_valid": 0 if silent else 24,
                        "findings": [{"check": check, "verdict": "exclude"}] if check else [],
                        "masked": {"flow": [], "occupancy": [], "speed": []},
                    }
                )  # fmt: skip
    sensors.append(
        {"sensor": "R1:1", "station": "R1", "lane": "1", "kind": "on_ramp", "x_m": 900.0}
    )
    for d in DATES:
        days.append(
            {"sensor": "R1:1", "station": "R1", "lane": "1", "kind": "on_ramp", "date": d,
             "verdict": "exclude", "n_valid": 24, "findings": [{"check": "zero_day"}]}
        )  # fmt: skip
    return {
        "schema": "flowstate.data_quality/1",
        "grid": {
            "interval_s": 300.0,
            "start_local": START,
            "end_local": END,
            "n_windows": 24,
            "dates": DATES,
            "per_lane": True,
        },
        "parameters": {"count_error": 0.05},
        "sensors": sensors,
        "sensor_days": days,
    }


#: S2 excluded on one candidate day (4/5 = 80 %: scored), S3 only on a Monday and
#: on Veterans Day (not candidates: 100 %), S4 on two candidate days (60 %: not scored)
PLANTED = {
    ("S2:2", "2026-11-04"): "stuck_flow",
    ("S3:1", "2026-11-02"): "zero_day",
    ("S3:1", "2026-11-11"): "zero_day",
    ("S4:3", "2026-11-05"): "missing",
    ("S4:1", "2026-11-12"): "implied_length",
}


class TestSelection:
    def test_the_80_percent_rule_on_the_candidate_days(self) -> None:
        sel = select_stations(_quality(PLANTED))
        assert sel.candidate_dates == tuple(CANDIDATES)
        assert sel.not_candidate["2026-11-02"] == ("weekday Monday is not a candidate weekday",)
        assert sel.not_candidate["2026-11-11"] == ("federal holiday: Veterans Day",)
        assert sel.stations == ("S1", "S2", "S3")
        shares = {r.station: (r.n_not_excluded, r.share, r.selected) for r in sel.records}
        assert shares == {
            "S1": (5, 1.0, True),  # its never-reporting lane 4 excludes nothing
            "S2": (4, 0.8, True),  # exactly the 80 % boundary: scored
            "S3": (5, 1.0, True),  # excluded only on days that are not candidates
            "S4": (3, 0.6, False),
        }
        s4 = sel.record("S4")
        assert "3 of 5 candidate day(s) (60% < 80%): not scored" in s4.reason
        assert [d["date"] for d in s4.excluded_days] == ["2026-11-05", "2026-11-12"]
        assert s4.excluded_days[0]["sensors"] == [
            {"sensor": "S4:3", "date": "2026-11-05", "verdict": "exclude", "checks": ["missing"]}
        ]
        assert [r.station for r in sel.records] == list(STATIONS)  # corridor order; no ramp
        assert sel.study_period == (START, END)
        assert any("S1:4" in n for n in sel.notes)
        assert any("no exclusion list" in n for n in sel.notes)
        assert MIN_NOT_EXCLUDED_SHARE == 0.80

    def test_exclusions_and_the_stretch_restrict_what_is_judged(self) -> None:
        sel = select_stations(
            _quality(PLANTED),
            exclusions={"2026-11-12": "crash, two lanes closed"},
            stations=["S4", "S2"],
        )
        assert "2026-11-12" not in sel.candidate_dates
        assert sel.not_candidate["2026-11-12"] == (
            "excluded by the caller: crash, two lanes closed",
        )
        assert [r.station for r in sel.records] == ["S2", "S4"]
        assert sel.record("S4").n_not_excluded == 3 and sel.record("S4").share == 0.75
        # four candidate days: S2's one exclusion is now 75 % < 80 % as well
        assert sel.record("S2").share == 0.75 and sel.stations == ()
        assert not any("no exclusion list" in n for n in sel.notes)

    def test_bad_requests_are_refused(self) -> None:
        payload = _quality({})
        with pytest.raises(ValueError, match="not mainline"):
            select_stations(payload, stations=["R1"])
        with pytest.raises(ValueError, match="not in the data-quality report"):
            select_stations(payload, stations=["S9"])
        with pytest.raises(ValueError, match="not covered"):
            select_stations(payload, dates=["2026-11-17"])
        with pytest.raises(ValueError, match="no candidate day"):
            select_stations(payload, dates=["2026-11-02", "2026-11-06"])

    def test_the_selection_json_block_is_additive_and_never_silently_replaced(self) -> None:
        sel = select_stations(_quality(PLANTED))
        base = {"corridor": "synthetic", "route": "I-0", "dates": ["20261103"]}
        doc = selection_document(sel, base)
        assert {k: doc[k] for k in base} == base
        block = doc[SELECTION_KEY]
        assert block["schema"] == STATION_SELECTION_SCHEMA
        assert block["stations"] == ["S1", "S2", "S3"] and block["n_considered"] == 4
        assert block["rules"]["min_not_excluded_share"] == 0.8
        assert StationSelection.from_dict(block).to_dict() == block
        assert read_selection(doc).stations == sel.stations
        with pytest.raises(ValueError, match="dated amendment"):
            selection_document(sel, doc)
        assert selection_document(sel, doc, replace=True)[SELECTION_KEY] == block
        with pytest.raises(ValueError, match="no 'scoring_stations' block"):
            read_selection(base)
        json.dumps(doc, allow_nan=False)


def _frame() -> pd.DataFrame:
    rows = []
    for i, d in enumerate(DATES):
        for k in range(48):  # 05:00 .. 08:55, offset -06:00
            minutes = 300 + 5 * k
            stamp = f"{d}T{minutes // 60:02d}:{minutes % 60:02d}:00-06:00"
            for sid in STATIONS:
                rows.append(
                    {
                        "timestamp": stamp,
                        "station": sid,
                        "flow_veh_h": 1500.0 + 13.0 * i + 10.0 * (k % 3),
                        "occupancy_pct": 10.0,
                        "speed_ms": 25.0,
                        "lanes": 3,
                        "kind": "mainline",
                        "x_m": X_M[sid],
                    }
                )
    return pd.DataFrame(rows)


class TestScripts:
    def test_select_then_split_on_the_selected_stations(self, tmp_path: Path) -> None:
        quality_path = tmp_path / "data_quality.json"
        quality_path.write_text(json.dumps(_quality(PLANTED)))
        base = tmp_path / "selection.json"
        base.write_text(json.dumps({"corridor": "synthetic", "window_s": 300}, indent=1))
        script = _load("station_selection")
        assert script.main(["--quality", str(quality_path), "--out", str(base)]) == 0
        written = json.loads(base.read_text())
        assert written["corridor"] == "synthetic" and written["window_s"] == 300
        block = written[SELECTION_KEY]
        assert block["stations"] == ["S1", "S2", "S3"]
        assert block["quality"]["path"] == str(quality_path)
        assert block["quality"]["sha256"] == QualityVerdicts.from_json(quality_path).sha256
        # a second run must not replace the committed list silently
        assert script.main(["--quality", str(quality_path), "--out", str(base)]) == 2
        assert script.main(["--quality", str(quality_path), "--out", str(base), "--replace"]) == 0

        detectors = tmp_path / "detectors.csv"
        _frame().to_csv(detectors, index=False)
        split_path = tmp_path / "split.json"
        day_split = _load("day_split")
        argv = ["--detectors", str(detectors), "--start", START, "--end", END,
                "--quality", str(quality_path), "--out", str(split_path)]  # fmt: skip
        assert day_split.main([*argv, "--selection", str(base)]) == 0
        split = json.loads(split_path.read_text())
        assert split["selected_stations"] == ["S1", "S2", "S3"]
        record = split["provenance"]["selection"]
        assert record["stations"] == ["S1", "S2", "S3"] and record["same_quality_report"]
        days = {d["date"]: d for d in split["days"]}
        # 4 November: S2 excluded, 2 of 3 selected stations usable (67 % < 80 %)
        assert not days["2026-11-04"]["candidate"]
        assert days["2026-11-04"]["unusable_stations"] == ["S2"]
        # S4 is not selected, so its exclusion on 5 November no longer costs the day
        assert days["2026-11-05"]["candidate"]
        # S1's silent lane 4 does not make S1 unusable
        assert days["2026-11-03"]["n_usable"] == 3
        assert day_split.main([*argv, "--selection", str(base), "--selected-stations", "S1"]) == 2
        assert day_split.main([*argv, "--selection", str(tmp_path / "missing.json")]) != 0
        empty = tmp_path / "plain.json"
        empty.write_text(json.dumps({"corridor": "synthetic"}))
        assert day_split.main([*argv, "--selection", str(empty)]) == 2

    def test_a_split_refuses_dates_the_quality_report_does_not_cover(self, tmp_path: Path) -> None:
        payload = _quality({})
        payload["grid"]["dates"] = DATES[:5]
        payload["sensor_days"] = [sd for sd in payload["sensor_days"] if sd["date"] in DATES[:5]]
        quality_path = tmp_path / "dq.json"
        quality_path.write_text(json.dumps(payload))
        detectors = tmp_path / "detectors.csv"
        _frame().to_csv(detectors, index=False)
        day_split = _load("day_split")
        argv = ["--detectors", str(detectors), "--start", START, "--end", END,
                "--quality", str(quality_path), "--out", str(tmp_path / "s.json")]  # fmt: skip
        assert day_split.main(argv) == 2
        assert day_split.main([*argv, "--allow-uncovered-dates"]) == 0
        split = json.loads((tmp_path / "s.json").read_text())
        days = {d["date"]: d for d in split["days"]}
        assert days["2026-11-10"]["reasons"] == ["not covered by the data-quality report"]
        assert days["2026-11-10"]["usable_share"] is None
        assert not any("stations usable" in r for d in split["days"] for r in d["reasons"])
        assert "2026-11-10" not in split["candidate_dates"]
        assert any("not covered by the data-quality report" in n for n in split["notes"])
        assert math.isnan(
            next(d for d in _split_days(tmp_path / "s.json") if d.date == "2026-11-12").usable_share
        )


def _split_days(path: Path) -> list[Any]:
    from calibration.day_split import DaySplit

    return list(DaySplit.from_json(path).days)
