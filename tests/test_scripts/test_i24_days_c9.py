"""C9's scripts on synthetic mornings (docs/PRE_FRISCO_PROGRAM.md C9).

No I-24 data and no simulation: three synthetic westbound mornings (Tuesday
29 Nov, Wednesday 30 Nov and Thursday 1 Dec 2022) built through the loader's
conversion (``tests/test_scripts/test_i24_data_days.py``), each with vehicles at
12.5 m/s, 5 m long, crossing every section, and a coverage artifact of 0.5.
Pinned:

* **the detector CSV** (``scripts/i24_virtual_detectors.py``): the loader's
  columns and units, one row per (window, section) over 06:00–10:00 CST with
  ISO ``-06:00`` stamps; per window the tracked crossings, the flow ×12 / 0.5,
  the crossing speed 12.5 m/s and the occupancy (0.4 s per vehicle per lane,
  / 0.5) by hand; unrecorded windows empty, never zero; the stations table;
  the refusals (another recording's coverage or observed side, a directory of
  another date);
* **the protocol scripts read it unchanged**: ``data_quality_report.py``,
  ``station_selection.py`` and ``day_split.py`` run on the CSV; with the pin
  and the CIRCLES events the split sends 30 Nov to calibration and the other
  two days to validation, and records the split as written beside it;
* **the observed side of a day** (``scripts/i24_validate.py --observed-only``):
  its counts equal the detectors' study-period crossings (the cross-check),
  its recommended flows equal the CSV's, and its apparent coverage is the
  day's equilibrium estimator; the committed day's coverage scaling is the
  committed observed side's to the byte;
* **the re-score** (``--criteria-only --rescore-observed``): a battery
  against its own observed side reproduces its stored GEH tables, RMSPE block
  and criteria values; against the mean of two days the tables are the
  per-window means; the battery artifact is never rewritten;
* **the pin and the event days** (``scripts/day_split.py``): the pin with
  nine candidates gives 5 / 4, the pinned day in calibration; with no pin the
  amended procedure is the protocol's draw exactly; a pinned day that fails
  the screen (a CIRCLES day) is refused;
* **the stage text** lists the CIRCLES days the split excludes and parses.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from calibration.conservation import normalize_date
from calibration.day_split import DaySplit, build_day_split
from calibration.loaders.detector_csv import (
    DETECTOR_COLUMNS,
    load_detector_csv,
    write_detector_csv,
)
from tests.test_scripts.test_day_split import _frame, _weekdays
from tests.test_scripts.test_i24_data_days import (
    REPO_ROOT,
    T0_20221129,
    T0_20221130,
    coverage_doc,
    crossing_vehicles,
    load_script,
    make_day,
    sentinels,
)

d = load_script("i24_data")
vd = load_script("i24_virtual_detectors")
iv = load_script("i24_validate")
ds = load_script("day_split")
dq = load_script("data_quality_report")
sel = load_script("station_selection")

T0_20221201 = T0_20221130 + 86400.0
STAGE = REPO_ROOT / "artifacts" / "i24_days_2026-10-07" / "stage_p20_c9.sh.txt"
#: Start times [s after 06:00] of each day's crossing vehicles: different volumes per day.
STARTS = {
    "2022-11-29": [1810.0, 1830.0, 2410.0, 5000.0],
    "2022-11-30": [1810.0, 1830.0, 1850.0, 2410.0, 5000.0, 7000.0],
    "2022-12-01": [1810.0, 2410.0, 2420.0, 2430.0, 2440.0],
}
T0 = {"2022-11-29": T0_20221129, "2022-11-30": T0_20221130, "2022-12-01": T0_20221201}


@pytest.fixture(scope="module")
def mornings(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Path]]:
    """Three synthetic mornings: their processed dirs and coverage artifacts."""
    root = tmp_path_factory.mktemp("c9")
    out: dict[str, dict[str, Path]] = {}
    for day, starts in STARTS.items():
        compact = day.replace("-", "")
        docs = sentinels(T0[day]) + crossing_vehicles(T0[day], starts, prefix=compact[-1])
        day_dir = make_day(root, f"i24_wb_{compact}", T0[day], docs)
        meta = json.loads((day_dir / "meta.json").read_text())
        cov = root / f"coverage_{compact}.json"
        cov.write_text(json.dumps(coverage_doc(meta["data_hash"])))
        out[day] = {"dir": day_dir, "coverage": cov, "root": root}
    return out


def _day_args(mornings: dict[str, dict[str, Path]], days: list[str] | None = None) -> list[str]:
    args: list[str] = []
    for day in days or sorted(mornings):
        args += ["--day", f"{day}={mornings[day]['dir']}"]
        args += ["--coverage", f"{day}={mornings[day]['coverage']}"]
    return args


@pytest.fixture(scope="module")
def detectors(
    mornings: dict[str, dict[str, Path]], tmp_path_factory: pytest.TempPathFactory
) -> Path:
    out = tmp_path_factory.mktemp("detectors")
    assert vd.main([*_day_args(mornings), "--out-dir", str(out)]) == 0
    return out


# --- the detector CSV ----------------------------------------------------------------


def test_the_csv_has_the_loader_schema_and_hand_computed_readings(
    detectors: Path, mornings: dict[str, dict[str, Path]]
) -> None:
    raw = pd.read_csv(detectors / vd.CSV_NAME)
    assert list(raw.columns[: len(DETECTOR_COLUMNS)]) == list(DETECTOR_COLUMNS)
    assert list(raw.columns[len(DETECTOR_COLUMNS) :]) == list(vd.EXTRA_COLUMNS)
    assert len(raw) == 3 * 48 * 6
    assert raw["timestamp"].iloc[0] == "2022-11-29T06:00:00-06:00"
    assert raw["timestamp"].iloc[-1] == "2022-12-01T09:55:00-06:00"
    frame = load_detector_csv(detectors / vd.CSV_NAME)
    assert frame.attrs["interval_s"] == 300.0
    assert set(frame["station"]) == {"S0200", "S1000", "S2200", "S3200", "S4800", "S5400"}
    assert set(frame["kind"]) == {"mainline"} and set(frame["lanes"]) == {4}
    day = raw[raw["timestamp"].str.startswith("2022-11-30")]
    for x_s in iv.SECTIONS_M:
        st = day[day["station"] == vd.station_id(x_s)].reset_index(drop=True)
        expected = np.zeros(48, dtype=int)
        for t in STARTS["2022-11-30"]:
            # counted at the first sample past x_s, x_s + 1.25 m: (x_s + 150) / 12.5 s after the start
            expected[int((t + (x_s + 150.0) / 12.5) // 300)] += 1
        assert st["n_crossings_tracked"].astype(int).tolist() == expected.tolist()
        assert np.allclose(st["flow_veh_h"], expected * 12 / 0.5)
        assert np.allclose(st["flow_tracked_veh_h"], expected * 12)
        crossed = expected > 0
        assert np.allclose(st.loc[crossed, "speed_ms"], 12.5)
        assert st.loc[~crossed, "speed_ms"].isna().all()
        # 5 m at 12.5 m/s covers the section 0.4 s: two 0.2 s samples per vehicle, lane mean
        occ_tracked = expected * 0.4 / (300.0 * 4) * 100.0
        assert np.allclose(st["occupancy_tracked_pct"], occ_tracked)
        assert np.allclose(st["occupancy_pct"], occ_tracked / 0.5)
        assert np.allclose(st["coverage"], 0.5)
    stations = pd.read_csv(detectors / vd.STATIONS_NAME)
    assert stations["kind"].tolist() == ["mainline"] * 6 + [
        "on_ramp",
        "off_ramp",
        "on_ramp",
        "off_ramp",
    ]
    assert stations["x_m"].tolist()[6:] == [950.0, 3700.0, 4600.0, 5050.0]
    summary = json.loads((detectors / vd.SUMMARY_NAME).read_text())
    assert [s["date"] for s in summary["days"]] == sorted(STARTS)
    assert summary["days"][1]["study_period_tracked_crossings"]["S0200"] == 6
    assert summary["days"][0]["weekday"] == "Tuesday"


def test_an_unrecorded_window_is_empty_not_zero(
    mornings: dict[str, dict[str, Path]], tmp_path: Path
) -> None:
    # a morning whose first sample is at 06:30:10 does not cover the study period's lead pad
    docs = sentinels(T0_20221129)[1:] + crossing_vehicles(T0_20221129, [1810.0], prefix="9")
    day_dir = make_day(tmp_path, "i24_wb_20221129", T0_20221129, docs)
    assert any(
        "first sample at t = 1810 s" in p for p in d.I24Day(day_dir, T0_20221129).check_recording()
    )
    # windows are recorded only inside the sample span
    rec = vd.recorded_windows({"t_min_s": 1810.0, "t_max_s": 14399.8}, 48)
    assert not rec[:7].any() and rec[7:].all()
    rows = vd.detector_rows(
        "2022-11-29",
        iv.SECTIONS_M,
        vd.BlockReadings(np.zeros((6, 48), dtype=np.int64), np.zeros((6, 48)), np.zeros((6, 48))),
        np.full(48, 0.5),
        rec,
    )
    first = rows.iloc[:6]
    assert (
        first[["flow_veh_h", "occupancy_pct", "speed_ms", "flow_tracked_veh_h"]].isna().all().all()
    )
    later = rows.iloc[7 * 6 : 8 * 6]
    assert (later["flow_veh_h"] == 0.0).all() and later["speed_ms"].isna().all()


def test_the_detectors_refuse_another_recording(
    mornings: dict[str, dict[str, Path]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    m = mornings["2022-11-29"]
    wrong_cov = tmp_path / "wrong.json"
    wrong_cov.write_text(json.dumps(coverage_doc("0" * 64)))
    code = vd.main(
        [
            "--day",
            f"2022-11-29={m['dir']}",
            "--coverage",
            f"2022-11-29={wrong_cov}",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert code == 2 and "was computed on data" in capsys.readouterr().err
    # a directory named for 29 Nov holding 30 Nov's morning
    code = vd.main(
        [
            "--day",
            f"2022-11-29={mornings['2022-11-30']['dir']}",
            "--coverage",
            f"2022-11-29={mornings['2022-11-30']['coverage']}",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert code == 2 and "unexpected time origin" in capsys.readouterr().err
    # a day without its coverage
    code = vd.main(["--day", f"2022-11-29={m['dir']}", "--out-dir", str(tmp_path)])
    assert code == 2 and "no --coverage" in capsys.readouterr().err


# --- the observed side of a day, and the cross-check ---------------------------------------


@pytest.fixture(scope="module")
def observed(mornings: dict[str, dict[str, Path]]) -> dict[str, Path]:
    """``--observed-only`` on each synthetic morning (the module's day switched in-process)."""
    out: dict[str, Path] = {}
    saved = (d.WB_DIR, d.T0_UNIX)
    try:
        for day, m in mornings.items():
            d.WB_DIR, d.T0_UNIX = m["dir"], T0[day]
            path = m["root"] / f"observed_{day.replace('-', '')}.json"
            iv.main(["--observed-only", str(path), "--coverage-artifact", str(m["coverage"])])
            out[day] = path
    finally:
        d.WB_DIR, d.T0_UNIX = saved
    return out


def test_the_observed_side_of_a_day_matches_its_detectors(
    mornings: dict[str, dict[str, Path]], observed: dict[str, Path], tmp_path: Path
) -> None:
    obs = json.loads(observed["2022-12-01"].read_text())
    assert obs["day"]["date"] == "2022-12-01"
    assert obs["day"]["source"].startswith("I-24 MOTION INCEPTION v1.x, 1 Dec 2022 westbound")
    assert obs["n_windows"] == 24 and obs["sections_m"] == list(iv.SECTIONS_M)
    # the day's apparent coverage is its own equilibrium estimator (0.4), not 30 Nov's builder rows
    assert obs["coverage_factor_per_window"] == [0.4] * 24
    assert "equilibrium_pooled" in obs["coverage_factor_source"]
    assert obs["coverage_recommended_per_window"] == [0.5] * 24
    # the cross-check holds on every day, and the recommended flows are the CSV's
    out = tmp_path / "checked"
    args = _day_args(mornings)
    for day, path in observed.items():
        args += ["--observed", f"{day}={path}"]
    assert vd.main([*args, "--out-dir", str(out)]) == 0
    csv = pd.read_csv(out / vd.CSV_NAME)
    day = csv[csv["timestamp"].str.startswith("2022-12-01")]
    study = day.iloc[6 * 6 : 30 * 6]
    flows = study["flow_veh_h"].to_numpy().reshape(24, 6).T
    assert np.allclose(flows, np.asarray(obs["hourly_flows_veh_h_recommended"]))
    summary = json.loads((out / vd.SUMMARY_NAME).read_text())
    assert all(s["cross_check"]["equal"] for s in summary["days"])
    # an observed side whose counts differ is refused
    bad = json.loads(observed["2022-11-29"].read_text())
    bad["counts_tracked"][0][0] += 1
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad))
    assert (
        vd.main(
            [
                *_day_args(mornings, ["2022-11-29"]),
                "--observed",
                f"2022-11-29={bad_path}",
                "--out-dir",
                str(tmp_path / "x"),
            ]
        )
        == 2
    )


def test_the_committed_day_keeps_its_coverage_scaling() -> None:
    committed = json.loads((REPO_ROOT / "artifacts" / "i24_validation_observed.json").read_text())
    rec, src = iv._recommended_coverage(24, 1800.0)
    assert rec.round(4).tolist() == committed["coverage_recommended_per_window"]
    assert src == committed["coverage_recommended_source"]
    rec2, src2 = iv._recommended_coverage(24, 1800.0, iv.COVERAGE_ARTIFACT)
    assert (rec2 == rec).all() and src2 == src
    assert tuple(committed["sections_m"]) == iv.SECTIONS_M


def test_observed_only_refuses_a_coverage_it_cannot_use(
    mornings: dict[str, dict[str, Path]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    m = mornings["2022-11-29"]
    monkeypatch.setattr(d, "WB_DIR", m["dir"])
    monkeypatch.setattr(d, "T0_UNIX", T0_20221129)
    other = tmp_path / "other.json"
    other.write_text(mornings["2022-12-01"]["coverage"].read_text())
    with pytest.raises(SystemExit):
        iv.main(["--observed-only", str(tmp_path / "o.json"), "--coverage-artifact", str(other)])
    assert "was computed on data" in capsys.readouterr().err
    meta = json.loads((m["dir"] / "meta.json").read_text())
    free = coverage_doc(meta["data_hash"])
    for w in free["windows"]:
        w["pooled"]["recommended_filled"] = None
    unscaled = tmp_path / "free.json"
    unscaled.write_text(json.dumps(free))
    with pytest.raises(SystemExit):
        iv.main(["--observed-only", str(tmp_path / "o.json"), "--coverage-artifact", str(unscaled)])
    assert "C9 stop rule: replace it" in capsys.readouterr().err
    assert not (tmp_path / "o.json").exists()


def test_observed_only_takes_no_battery_option(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        iv.main(["--observed-only", "x.json", "--criteria-only"])
    assert "takes no battery option" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        iv.main(["--criteria-only", "--coverage-artifact", "c.json"])
    assert "goes with --observed-only" in capsys.readouterr().err


# --- the re-score -------------------------------------------------------------------------

BATTERY = REPO_ROOT / "artifacts" / "i24_validation_dc_refit_rc.json"
COMMITTED_OBSERVED = REPO_ROOT / "artifacts" / "i24_validation_observed.json"


def test_rescoring_a_battery_against_its_own_day_reproduces_it(tmp_path: Path) -> None:
    before = BATTERY.read_bytes()
    out = tmp_path / "self.json"
    iv.main(
        [
            "--criteria-only",
            "--label",
            "dc_refit_rc",
            "--rescore-observed",
            f"2022-11-30={COMMITTED_OBSERVED}",
            "--rescore-out",
            str(out),
        ]
    )
    assert BATTERY.read_bytes() == before
    doc = json.loads(out.read_text())
    battery = json.loads(before)
    assert doc["schema"] == iv.RESCORE_SCHEMA and doc["label"] == "2022-11-30"
    for table in (
        "vs_tracked_counts",
        "vs_coverage_corrected_counts",
        "vs_recommended_coverage_counts",
    ):
        assert doc["geh"][table] == battery["geh"][table]
    assert doc["rmspe"] == battery["rmspe"]
    stored = {r["name"]: r["value"] for r in battery["criteria"]}
    for row in doc["criteria"]:
        if row["name"] in ("link_flows_geh", "speeds_rmspe", "n_seeds", "no_collisions"):
            assert row["value"] == stored[row["name"]]
    gate = doc["gate_checks"]
    assert gate["reported_only"] is True
    assert gate["C1"]["n_comparisons"] == 12 and gate["C1"]["n_windows_left_out"] == 0
    assert gate["C3"]["replicates"]["n"] == battery["replicates"]
    criterion = [
        r
        for r in gate["C3"]["replicate_mean_field_by_aggregation"]
        if "criterion" in r["aggregation"]
    ]
    assert criterion and criterion[0]["aggregation"] == "15 min (criterion)"
    assert gate["C5"]["passed"] is True
    assert gate["C4"]["observed_speed_kmh"] == pytest.approx(
        battery["waves"]["by_detector"]["stack"]["observed"]["mean_backward_speed_kmh"]
    )


def test_the_mean_of_days_is_the_per_window_mean(tmp_path: Path) -> None:
    obs = json.loads(COMMITTED_OBSERVED.read_text())
    other = json.loads(COMMITTED_OBSERVED.read_text())
    other["hourly_flows_veh_h_recommended"] = (
        np.asarray(other["hourly_flows_veh_h_recommended"]) * 1.5
    ).tolist()
    seg = np.asarray(other["segment_speeds_ms"], dtype=float)
    seg[0, 0] = np.nan
    other["segment_speeds_ms"] = [[None if math.isnan(v) else v for v in row] for row in seg]
    other_path = tmp_path / "other.json"
    other_path.write_text(json.dumps(other))
    mean = iv.mean_observed([obs, json.loads(other_path.read_text())])
    assert np.allclose(
        mean["hourly_flows_veh_h_recommended"],
        np.asarray(obs["hourly_flows_veh_h_recommended"]) * 1.25,
    )
    # a cell one day did not measure takes the other day's value
    assert mean["segment_speeds_ms"][0][0] == obs["segment_speeds_ms"][0][0]
    assert mean["n_days"] == 2 and "waves_by_detector" not in mean
    out = tmp_path / "mean.json"
    iv.main(
        [
            "--criteria-only",
            "--label",
            "dc_refit_rc",
            "--rescore-observed",
            f"2022-11-30={COMMITTED_OBSERVED}",
            f"2022-12-01={other_path}",
            "--rescore-out",
            str(out),
            "--rescore-label",
            "validation_mean",
        ]
    )
    doc = json.loads(out.read_text())
    assert doc["label"] == "validation_mean" and doc["observed"]["dates"] == [
        "2022-11-30",
        "2022-12-01",
    ]
    assert doc["observed"]["aggregation"].startswith("mean over 2 days")
    # another grid is refused
    other["n_windows"] = 23
    with pytest.raises(ValueError, match="different grids"):
        iv.mean_observed([obs, other])


def test_the_rescore_options_are_checked(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    base = ["--rescore-observed", f"2022-11-30={COMMITTED_OBSERVED}"]
    for argv, words in (
        ([*base, "--label", "dc_refit_rc", "--rescore-out", "x.json"], "give --criteria-only"),
        ([*base, "--criteria-only", "--label", "dc_refit_rc"], "needs --rescore-out"),
        ([*base, "--criteria-only", "--rescore-out", "x.json"], "exactly one battery"),
        ([*base, "--criteria-only", "--arms", "all", "--rescore-out", "x.json"], "not all/both"),
        (
            ["--criteria-only", "--label", "dc_refit_rc", "--rescore-out", "x.json"],
            "go with --rescore-observed",
        ),
        (
            [*base, "--criteria-only", "--label", "dc_refit_rc", "--rescore-out", str(BATTERY)],
            "may not be the battery artifact",
        ),
    ):
        with pytest.raises(SystemExit):
            iv.main(argv)
        assert words in capsys.readouterr().err


# --- the protocol scripts on the CSV, with the pin and the event days ---------------------


def test_quality_selection_and_the_pinned_split_read_the_csv_unchanged(
    detectors: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv, stations = detectors / vd.CSV_NAME, detectors / vd.STATIONS_NAME
    dq_dir = tmp_path / "dq"
    assert (
        dq.main(
            [
                "--detectors",
                str(csv),
                "--stations",
                str(stations),
                "--start",
                "06:30",
                "--end",
                "08:30",
                "--out",
                str(dq_dir),
            ]
        )
        == 0
    )
    quality = json.loads((dq_dir / "data_quality.json").read_text())
    assert sorted(normalize_date(x) for x in quality["grid"]["dates"]) == sorted(STARTS)
    circles = [f"--exclude-day={k}={v}" for k, v in ds.CIRCLES_TEST_FLEET_DAYS.items()]
    selection = tmp_path / "selection.json"
    assert (
        sel.main(
            ["--quality", str(dq_dir / "data_quality.json"), *circles, "--out", str(selection)]
        )
        == 0
    )
    split_path = tmp_path / "day_split.json"
    code = ds.main(
        [
            "--detectors",
            str(csv),
            "--stations",
            str(stations),
            "--start",
            "06:30",
            "--end",
            "08:30",
            "--quality",
            str(dq_dir / "data_quality.json"),
            "--selection",
            str(selection),
            "--circles-events",
            "--pin-calibration",
            "2022-11-30=every I-24 calibration used it",
            "--out",
            str(split_path),
        ]
    )
    printed = capsys.readouterr().out
    split = json.loads(split_path.read_text())
    if code != 0 or split["candidate_dates"] != sorted(STARTS):
        pytest.fail(f"split: code {code}, candidates {split.get('candidate_dates')}\n{printed}")
    assert split["calibration_dates"] == ["2022-11-30"]
    assert split["validation_dates"] == ["2022-11-29", "2022-12-01"]
    assert split["underpowered"] is True
    block = split["c9_amendment"]
    assert block["pinned"] == {"2022-11-30": "every I-24 calibration used it"}
    assert sorted(block["events"]) == sorted(ds.CIRCLES_TEST_FLEET_DAYS)
    assert (
        block["n_candidates"],
        block["calibration_total"],
        block["n_pinned"],
        block["n_drawn"],
    ) == (3, 1, 1, 0)
    assert len(block["as_written"]["calibration_dates"]) == 1
    assert len(block["as_written"]["validation_dates"]) == 2
    assert all(k in split["exclusions"] for k in ds.CIRCLES_TEST_FLEET_DAYS)
    assert "pinned to calibration: 2022-11-30" in printed


def _split_of(
    days: list[str], volume: dict[str, float], exclusions: dict[str, str] | None = None
) -> DaySplit:
    frame = _frame(days, volume=volume)
    return build_day_split(frame, start="06:00", end="08:00", exclusions=exclusions)


def test_with_no_pin_the_amended_procedure_is_the_protocols_draw() -> None:
    days = [
        d_
        for d_ in _weekdays("2026-09-01", "2026-10-30")
        if pd.Timestamp(d_).weekday() in (1, 2, 3)
    ]
    volume = {day: 1000.0 + 37.0 * (i % 7) + 3.0 * i for i, day in enumerate(days)}
    split = _split_of(days, volume)
    amended, record = ds.pin_calibration(split, {})
    assert amended.calibration_dates == split.calibration_dates
    assert amended.validation_dates == split.validation_dates
    assert [s.to_dict() for s in amended.strata] == [s.to_dict() for s in split.strata]
    assert record["n_pinned"] == 0 and record["n_drawn"] == len(split.calibration_dates)


def test_the_pin_with_nine_candidates_gives_five_and_four() -> None:
    days = _weekdays("2022-11-28", "2022-12-16")
    candidates = [d_ for d_ in days if pd.Timestamp(d_).weekday() in (1, 2, 3)]
    assert len(candidates) == 9
    volume = {day: 1500.0 + 10.0 * i for i, day in enumerate(days)}
    # the pinned day is the lowest volume, so the draw alone would likely miss it
    volume["2022-11-30"] = 100.0
    split = _split_of(days, volume)
    amended, record = ds.pin_calibration(split, {"2022-11-30": "calibrated on it"})
    assert "2022-11-30" in amended.calibration_dates
    assert (len(amended.calibration_dates), len(amended.validation_dates)) == (5, 4)
    assert not amended.underpowered
    assert (record["calibration_total"], record["n_drawn"]) == (5, 4)
    assert sum(s.n_calibration for s in amended.strata) == 4
    assert all("2022-11-30" not in s.dates for s in amended.strata)
    assert amended.rules["c9_pin"] == ds.PIN_RULE
    # eight candidates (one fewer): 4 + 4, underpowered on the calibration side
    eight = _split_of([x for x in days if x != "2022-12-15"], volume)
    amended8, _ = ds.pin_calibration(eight, {"2022-11-30": "calibrated on it"})
    assert (len(amended8.calibration_dates), len(amended8.validation_dates)) == (4, 4)
    assert amended8.underpowered


def test_a_pinned_day_must_pass_the_screen() -> None:
    days = _weekdays("2022-11-14", "2022-11-18")
    volume = dict.fromkeys(days, 1500.0)
    split = _split_of(days, volume, exclusions=dict(ds.CIRCLES_TEST_FLEET_DAYS))
    assert split.candidate_dates == ()
    reasons = {r.date: r.reasons for r in split.days}
    assert any("CIRCLES MegaVanderTest" in r for r in reasons["2022-11-16"])
    with pytest.raises(ValueError, match="not a candidate"):
        ds.pin_calibration(split, {"2022-11-16": "x"})
    with pytest.raises(ValueError, match="not among the dates examined"):
        ds.pin_calibration(split, {"2022-12-16": "x"})


def test_the_script_without_the_amendment_writes_what_it_wrote(tmp_path: Path) -> None:
    days = _weekdays("2022-11-28", "2022-12-09")
    csv = tmp_path / "d.csv"
    write_detector_csv(_frame(days), csv)
    plain, pinned = tmp_path / "plain.json", tmp_path / "pinned.json"
    assert (
        ds.main(
            ["--detectors", str(csv), "--start", "06:00", "--end", "08:00", "--out", str(plain)]
        )
        == 0
    )
    assert "c9_amendment" not in json.loads(plain.read_text())
    assert (
        ds.main(
            [
                "--detectors",
                str(csv),
                "--start",
                "06:00",
                "--end",
                "08:00",
                "--pin-calibration",
                "2022-11-30=x",
                "--out",
                str(pinned),
            ]
        )
        == 0
    )
    a, b = json.loads(plain.read_text()), json.loads(pinned.read_text())
    assert b["c9_amendment"]["as_written"]["calibration_dates"] == a["calibration_dates"]
    assert "2022-11-30" in b["calibration_dates"]
    assert (
        ds.main(
            [
                "--detectors",
                str(csv),
                "--start",
                "06:00",
                "--end",
                "08:00",
                "--pin-calibration",
                "bad",
                "--out",
                str(pinned),
            ]
        )
        == 2
    )


# --- the stage text -----------------------------------------------------------------------


def test_the_stage_text_parses_and_excludes_the_circles_days() -> None:
    text = STAGE.read_text()
    assert shutil.which("bash")
    out = subprocess.run(["bash", "-n"], input=text, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    for day in ds.CIRCLES_TEST_FLEET_DAYS:
        assert f'--exclude-day "{day}=$P20_EVENT"' in text, day
    assert "--circles-events" in text and "--pin-calibration" in text
    assert "--rescore-observed" in text and "--observed-only" in text
    assert "p20_i24_days" in text
