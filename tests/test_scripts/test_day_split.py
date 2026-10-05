"""The protocol's calibration / validation day split (docs/FRISCO_PROTOCOL.md §3).

Synthetic detector frames only: four mainline stations on a 5-minute grid,
one frame per scenario, with the volume of each date set by hand so the
tercile order is known. Covers the candidate screening (weekdays, federal
holidays, the caller's exclusions, the 80 % usable-station rule with and
without data-quality verdicts), the terciles, the seeded draw (fixed seed,
deterministic, the documented recipe), the underpowered flag, the additive
``dates=`` filter of ``Observations.from_frame``, and the two scripts
(``scripts/day_split.py``, ``scripts/observations_for_dates.py``).
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from calibration.day_split import (
    CALIBRATION_SHARE,
    MIN_CALIBRATION_DAYS,
    MIN_VALIDATION_DAYS,
    SPLIT_SEED,
    DaySplit,
    build_day_split,
    read_exclusions,
    us_federal_holidays,
)
from calibration.observations import Observations

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
STATIONS = ("S1", "S2", "S3", "S4")
X_M = (0.0, 600.0, 1300.0, 2000.0)
WINDOW_S = 300
START, END = "06:00", "08:00"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        f"flowstate_day_split_{name}", SCRIPTS / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _weekdays(first: str, last: str) -> list[str]:
    day, end = date.fromisoformat(first), date.fromisoformat(last)
    out = []
    while day <= end:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _frame(
    dates: list[str],
    *,
    volume: dict[str, float] | None = None,
    missing: dict[str, tuple[str, ...]] | None = None,
) -> pd.DataFrame:
    """Station rows 05:00–09:00 local (offset −06:00); flow per date from ``volume``."""
    rows = []
    for d in dates:
        base = (volume or {}).get(d, 1500.0)
        gone = set((missing or {}).get(d, ()))
        for k in range(48):  # 05:00 .. 08:55
            minutes = 300 + 5 * k
            stamp = f"{d}T{minutes // 60:02d}:{minutes % 60:02d}:00-06:00"
            for sid, x in zip(STATIONS, X_M, strict=True):
                flow = math.nan if sid in gone else base + 10.0 * (k % 3)
                rows.append(
                    {
                        "timestamp": stamp,
                        "station": sid,
                        "flow_veh_h": flow,
                        "occupancy_pct": 10.0,
                        "speed_ms": 25.0 if sid in gone else 25.0 + (k % 2),
                        "lanes": 3,
                        "kind": "mainline",
                        "x_m": x,
                    }
                )
    return pd.DataFrame(rows)


NOVEMBER = _weekdays("2026-11-02", "2026-11-27")


class TestHolidays:
    def test_federal_holidays_of_2026(self) -> None:
        h = us_federal_holidays(2026)
        assert h["2026-11-26"] == "Thanksgiving Day"
        assert h["2026-11-11"] == "Veterans Day"
        assert h["2026-09-07"] == "Labor Day"
        assert h["2026-01-19"].startswith("Birthday of Martin Luther King")
        assert h["2026-05-25"] == "Memorial Day"
        # Independence Day 2026 is a Saturday: observed on Friday 3 July.
        assert h["2026-07-04"] == "Independence Day"
        assert h["2026-07-03"] == "Independence Day (observed)"


class TestScreening:
    def test_weekdays_holidays_exclusions_and_usable_stations(self) -> None:
        frame = _frame(NOVEMBER, missing={"2026-11-24": ("S2", "S3")})
        split = build_day_split(
            frame, start=START, end=END, exclusions={"2026-11-18": "crash, lanes 2-3 closed"}
        )
        by = {d.date: d for d in split.days}
        assert by["2026-11-02"].reasons == ("weekday Monday is not a candidate weekday",)
        assert not by["2026-11-06"].candidate  # Friday
        assert by["2026-11-11"].reasons == ("federal holiday: Veterans Day",)
        assert by["2026-11-26"].reasons == ("federal holiday: Thanksgiving Day",)
        assert by["2026-11-18"].reasons == ("excluded by the caller: crash, lanes 2-3 closed",)
        usable = by["2026-11-24"]
        assert not usable.candidate and usable.n_usable == 2 and usable.usable_share == 0.5
        assert usable.unusable_stations == ("S2", "S3")
        assert "2 of 4 selected stations usable" in usable.reasons[0]
        assert split.candidate_dates == (
            "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-10",
            "2026-11-12", "2026-11-17", "2026-11-19", "2026-11-25",
        )  # fmt: skip
        assert split.usability_source == "completeness"
        assert split.exclusions == {"2026-11-18": "crash, lanes 2-3 closed"}
        assert not any("no exclusion list" in n for n in split.notes)

    def test_without_an_exclusion_list_the_split_says_so(self) -> None:
        split = build_day_split(_frame(NOVEMBER[:5]), start=START, end=END)
        assert any("no exclusion list was supplied" in n for n in split.notes)

    def test_data_quality_verdicts_decide_usability(self) -> None:
        dates = NOVEMBER[:10]
        frame = _frame(dates)
        quality = {
            "schema": "flowstate.data_quality/1",
            "grid": {"start_local": START, "end_local": END, "dates": dates},
            "sensor_days": [
                {
                    "sensor": f"{sid}:{lane}",
                    "station": sid,
                    "lane": str(lane),
                    "kind": "mainline",
                    "date": d,
                    # one lane of S4 excluded on 4 November: S4 unusable that day
                    "verdict": "exclude" if (sid, lane, d) == ("S4", 2, "2026-11-04") else "ok",
                }
                for d in dates
                for sid in STATIONS
                for lane in (1, 2, 3)
            ],
        }
        split = build_day_split(frame, start=START, end=END, quality=quality)
        assert split.usability_source == "data_quality"
        nov4 = next(d for d in split.days if d.date == "2026-11-04")
        assert nov4.unusable_stations == ("S4",) and nov4.usable_share == 0.75
        assert not nov4.candidate  # 75 % < 80 %
        assert not any("judged over" in n for n in split.notes)
        mismatch = build_day_split(
            frame, start=START, end="07:00", quality=quality, exclusions={"2026-11-02": "x"}
        )
        assert any("judged over 06:00-08:00" in n for n in mismatch.notes)

    def test_a_date_the_quality_report_does_not_cover_is_refused_not_unusable(self) -> None:
        dates = NOVEMBER[:6]
        quality = {
            "schema": "flowstate.data_quality/1",
            "grid": {"start_local": START, "end_local": END, "dates": dates[:4]},
            "sensor_days": [
                {"station": sid, "kind": "mainline", "date": d, "verdict": "ok"}
                for d in dates[:4]
                for sid in STATIONS
            ],
        }
        frame = _frame(dates)
        with pytest.raises(ValueError, match=r"not covered by the data-quality report"):
            build_day_split(frame, start=START, end=END, quality=quality)
        split = build_day_split(
            frame, start=START, end=END, quality=quality, allow_uncovered_dates=True
        )
        by = {d.date: d for d in split.days}
        for day in dates[4:]:  # 2026-11-06 (Friday) and 2026-11-09 (Monday)
            assert "not covered by the data-quality report" in by[day].reasons
            assert not any("stations usable" in r for r in by[day].reasons)
            assert math.isnan(by[day].usable_share)
        assert by["2026-11-03"].candidate
        assert any("2 date(s) left out" in n for n in split.notes)
        assert "allow_uncovered_dates" in split.rules["usable_with_quality_report"]

    def test_volume_is_the_station_mean_study_period_volume(self) -> None:
        frame = _frame(["2026-11-03"], volume={"2026-11-03": 1200.0})
        split = build_day_split(frame, start=START, end=END)
        # 24 windows 06:00-08:00, flows 1200, 1210, 1220 repeating: mean 1210 veh/h x 2 h.
        assert split.days[0].volume_veh == pytest.approx(2420.0)
        assert split.days[0].n_station_windows == 4 * 24

    def test_bad_inputs(self) -> None:
        frame = _frame(NOVEMBER[:3])
        with pytest.raises(ValueError, match="not in the detector frame"):
            build_day_split(frame, start=START, end=END, stations=["S9"])
        with pytest.raises(ValueError, match="no rows"):
            build_day_split(frame, start=START, end=END, dates=["2026-12-01"])
        with pytest.raises(ValueError, match="not after its start"):
            build_day_split(frame, start="08:00", end="06:00")


def _expected_allocation(sizes: list[int], tie_order: list[int]) -> list[int]:
    """§3.2 as amended 2026-10-04, redone by hand: 60 % of all candidates,
    rounded down, by largest remainder, at least one per tercile when the
    total allows, ties in the seeded order."""
    n = sum(sizes)
    total = (3 * n) // 5
    if total == 0:
        return [0] * len(sizes)
    quotas = [k * total / n for k in sizes]
    alloc = [math.floor(q + 1e-12) for q in quotas]
    if total >= sum(1 for k in sizes if k):
        alloc = [max(a, 1) if k else 0 for a, k in zip(alloc, sizes, strict=True)]
    rank = {k: i for i, k in enumerate(tie_order)}
    order = sorted(
        (i for i in range(len(sizes)) if alloc[i] < sizes[i]),
        key=lambda i: (-(quotas[i] - math.floor(quotas[i] + 1e-12)), rank[i]),
    )
    for i in order[: total - sum(alloc)]:
        alloc[i] += 1
    return alloc


def _expected_positions(sizes: list[int], seed: int) -> list[tuple[int, ...]]:
    """The documented recipe, redone: one generator — the tie permutation
    first, then the terciles low to high."""
    rng = np.random.default_rng(seed)
    alloc = _expected_allocation(sizes, [int(k) for k in rng.permutation(len(sizes))])
    out = []
    for n, k in zip(sizes, alloc, strict=True):
        if k == 0:
            out.append(())
            continue
        out.append(tuple(sorted(int(p) for p in rng.choice(n, size=k, replace=False))))
    return out


class TestSplit:
    DATES = _weekdays("2026-09-01", "2026-11-30")

    def _volumes(self) -> dict[str, float]:
        # a distinct volume per date, not in date order
        return {d: 1000.0 + 37.0 * ((i * 7) % len(self.DATES)) for i, d in enumerate(self.DATES)}

    def test_terciles_draw_and_determinism(self) -> None:
        frame = _frame(self.DATES, volume=self._volumes())
        split = build_day_split(frame, start=START, end=END)
        again = build_day_split(frame, start=START, end=END)
        assert split.to_dict() | {"provenance": {}} == again.to_dict() | {"provenance": {}}
        assert split.seed == SPLIT_SEED == 20261004
        assert CALIBRATION_SHARE == 0.6

        candidates = split.candidate_dates
        assert set(split.calibration_dates) | set(split.validation_dates) == set(candidates)
        assert not set(split.calibration_dates) & set(split.validation_dates)
        vol = {d.date: d.volume_veh for d in split.days}
        sizes = [len(s.dates) for s in split.strata]
        assert sum(sizes) == len(candidates) and max(sizes) - min(sizes) <= 1
        # terciles ordered by volume: every day of a lower tercile is below the next one's
        for lower, upper in zip(split.strata, split.strata[1:], strict=False):
            assert max(vol[d] or 0.0 for d in lower.dates) < min(vol[d] or 0.0 for d in upper.dates)
        assert [s.label for s in split.strata] == ["low", "middle", "high"]
        # the draw is the documented recipe with the fixed seed
        expected = _expected_positions(sizes, SPLIT_SEED)
        assert len(split.calibration_dates) == (3 * len(candidates)) // 5
        for stratum, positions in zip(split.strata, expected, strict=True):
            assert stratum.drawn_positions == positions
            assert stratum.n_calibration == len(positions) >= 1
            assert stratum.calibration_dates == tuple(stratum.dates[p] for p in positions)
            assert list(stratum.dates) == sorted(stratum.dates)
        assert not split.underpowered and split.underpowered_reason == ""

    def test_another_seed_draws_differently(self) -> None:
        frame = _frame(self.DATES, volume=self._volumes())
        a = build_day_split(frame, start=START, end=END)
        b = build_day_split(frame, start=START, end=END, seed=SPLIT_SEED + 1)
        assert a.calibration_dates != b.calibration_dates
        assert [len(s.calibration_dates) for s in a.strata] == [
            len(s.calibration_dates) for s in b.strata
        ]

    def test_small_study_is_underpowered_but_split(self) -> None:
        frame = _frame(NOVEMBER, missing={"2026-11-24": ("S2", "S3")})
        split = build_day_split(frame, start=START, end=END, exclusions={"2026-11-18": "crash"})
        # 8 candidates: terciles 3 / 3 / 2; 60 % of 8 rounded down is 4 calibration days,
        # quotas 1.5 / 1.5 / 1.0 -> 1 / 1 / 1 plus one to a tied remainder (seeded order)
        assert [len(s.dates) for s in split.strata] == [3, 3, 2]
        assert sum(s.n_calibration for s in split.strata) == 4
        assert sorted(s.n_calibration for s in split.strata) == [1, 1, 2]
        assert split.strata[2].n_calibration == 1
        assert len(split.calibration_dates) == 4 < MIN_CALIBRATION_DAYS
        assert len(split.validation_dates) == 4 >= MIN_VALIDATION_DAYS
        assert split.underpowered
        assert split.underpowered_reason.startswith("4 calibration day(s), fewer than 5")

    def test_three_days_send_one_to_calibration(self) -> None:
        # 60 % of three, rounded down, is one: too few for one per tercile, so the
        # day goes to the tercile first in the seeded tie order (all remainders tie)
        frame = _frame(["2026-11-03", "2026-11-04", "2026-11-05"])
        split = build_day_split(frame, start=START, end=END)
        assert sum(s.n_calibration for s in split.strata) == 1
        assert len(split.validation_dates) == 2
        assert "1 calibration day(s), fewer than 5" in split.underpowered_reason
        assert "2 validation day(s), fewer than 3" in split.underpowered_reason

    def test_nine_days_send_five_to_calibration(self) -> None:
        # the Minnesota rehearsal's size: 3 / 3 / 3 terciles, 60 % of 9 rounded down is 5,
        # quotas 1.67 each -> 1 / 1 / 1 and two more to the seeded tie order's first two
        assert _expected_allocation([3, 3, 3], [2, 0, 1]) == [2, 1, 2]
        # the Minnesota corridor's nine Tuesday-Thursday dates
        dates = [f"2026-09-{d:02d}" for d in (1, 2, 3, 8, 9, 10, 15, 16, 17)]
        split = build_day_split(_frame(dates), start=START, end=END)
        assert len(split.candidate_dates) == 9
        assert len(split.calibration_dates) == 5 and len(split.validation_dates) == 4
        assert not split.underpowered

    def test_json_round_trip(self, tmp_path: Path) -> None:
        split = build_day_split(_frame(NOVEMBER), start=START, end=END)
        path = split.to_json(tmp_path / "split.json")
        back = DaySplit.from_json(path)
        assert back.to_dict() == split.to_dict()
        assert back.dates_of("calibration") == split.calibration_dates
        with pytest.raises(ValueError):
            back.dates_of("test")


class TestExclusionFiles:
    def test_json_csv_and_items(self, tmp_path: Path) -> None:
        j = tmp_path / "ex.json"
        j.write_text(json.dumps({"20261103": "snow"}))
        c = tmp_path / "ex.csv"
        c.write_text("date,reason\n2026-11-04,incident\n")
        out = read_exclusions([j, c], ["2026-11-05=state holiday"])
        assert out == {
            "2026-11-03": "snow",
            "2026-11-04": "incident",
            "2026-11-05": "state holiday",
        }
        with pytest.raises(ValueError, match="DATE=REASON"):
            read_exclusions([], ["2026-11-05"])


class TestObservationsForDates:
    def test_dates_filter_aggregates_only_the_subset(self) -> None:
        volume = {"2026-11-03": 1000.0, "2026-11-04": 2000.0, "2026-11-05": 4000.0}
        frame = _frame(list(volume), volume=volume)
        kwargs = {
            "window_s": 300.0,
            "t0_local": "06:00",
            "duration_s": 3600.0,
            "corridor": "synthetic",
            "source": {"provider": "test"},
        }
        every = Observations.from_frame(frame, None, **kwargs)  # type: ignore[arg-type]
        subset = Observations.from_frame(
            frame,
            None,
            dates=["20261103", "2026-11-05"],
            **kwargs,  # type: ignore[arg-type]
        )
        # window 0 (06:00) is k = 12 of the frame: flow base + 10 * (12 % 3) = base
        assert every.flows_veh_h["S1"][0] == pytest.approx((1000 + 2000 + 4000) / 3)
        assert subset.flows_veh_h["S1"][0] == pytest.approx((1000 + 4000) / 2)
        assert subset.quality["S1"]["n_dates"] == 2.0
        with pytest.raises(ValueError, match="have no row"):
            Observations.from_frame(frame, None, dates=["2026-11-06"], **kwargs)  # type: ignore[arg-type]


def _corridor_dir(base: Path, dates: list[str], volume: dict[str, float]) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    _frame(dates, volume=volume).to_csv(base / "detectors.csv", index=False)
    pd.DataFrame(
        {
            "station": list(STATIONS),
            "label": list(STATIONS),
            "x_m": list(X_M),
            "lanes": [3] * 4,
            "kind": ["mainline"] * 4,
        }
    ).to_csv(base / "stations.csv", index=False)
    return base


class TestScripts:
    def test_day_split_then_observations_for_each_day_set(self, tmp_path: Path) -> None:
        dates = _weekdays("2026-09-01", "2026-10-30")
        volume = {d: 1000.0 + 11.0 * ((i * 5) % len(dates)) for i, d in enumerate(dates)}
        corridor = _corridor_dir(tmp_path / "corridor", dates, volume)
        split_path = tmp_path / "split.json"
        script = _load("day_split")
        assert script.main(
            ["--corridor-dir", str(corridor), "--start", START, "--end", END,
             "--exclude-day", "2026-09-16=incident", "--out", str(split_path)]
        ) == 0  # fmt: skip
        split = json.loads(split_path.read_text())
        assert split["schema"] == "flowstate.day_split/1"
        assert split["seed"] == SPLIT_SEED
        assert split["exclusions"] == {"2026-09-16": "incident"}
        assert split["provenance"]["inputs"]["mode"] == "detector_csv"
        assert len(split["calibration_dates"]) >= MIN_CALIBRATION_DAYS
        assert split["rules"]["min_usable_station_share"] == 0.8

        like = Observations.from_frame(
            _frame(dates, volume=volume),
            None,
            window_s=300.0,
            t0_local="06:00",
            duration_s=7200.0,
            corridor="synthetic",
            source={"provider": "test", "dates": [d.replace("-", "") for d in dates]},
        )
        like_path = like.to_json(tmp_path / "like.json")
        subset = _load("observations_for_dates")
        for which in ("calibration", "validation"):
            out = tmp_path / f"obs_{which}.json"
            assert subset.main(
                ["--corridor-dir", str(corridor), "--like", str(like_path),
                 "--split", str(split_path), "--set", which, "--out", str(out)]
            ) == 0  # fmt: skip
            built = json.loads(out.read_text())
            want = [d.replace("-", "") for d in split[f"{which}_dates"]]
            assert built["source"]["dates"] == want  # the reference's spelling
            assert built["source"]["subset"]["set"] == which
            assert [s["x_m"] for s in built["stations"]] == list(X_M)
            first = np.mean([volume[d] for d in split[f"{which}_dates"]])
            assert built["flows_veh_h"]["S1"][0] == pytest.approx(first)
            assert "context" not in built

    def test_observations_for_dates_masks_with_the_quality_report(self, tmp_path: Path) -> None:
        dates = NOVEMBER[:4]
        volume = {d: 1000.0 * (i + 1) for i, d in enumerate(dates)}
        corridor = _corridor_dir(tmp_path / "c", dates, volume)
        like = Observations.from_frame(
            _frame(dates, volume=volume), None, window_s=300.0, t0_local="06:00",
            duration_s=3600.0, corridor="s", source={"provider": "t", "dates": dates},
        )  # fmt: skip
        like_path = like.to_json(tmp_path / "like.json")

        def report(judged: list[str]) -> Path:
            payload = {
                "schema": "flowstate.data_quality/1",
                "grid": {"interval_s": 300.0, "start_local": "05:00", "end_local": "09:00",
                         "n_windows": 48, "dates": judged, "per_lane": False},
                "sensor_days": [
                    {"sensor": sid, "station": sid, "lane": None, "kind": "mainline", "date": d,
                     "verdict": "exclude" if (sid, d) == ("S1", dates[1]) else "ok",
                     "findings": [{"check": "stuck_flow", "verdict": "exclude"}]
                     if (sid, d) == ("S1", dates[1]) else [],
                     "masked": {"flow": [], "occupancy": [], "speed": []}}
                    for d in judged for sid in STATIONS
                ],
            }  # fmt: skip
            path = tmp_path / f"dq_{len(judged)}.json"
            path.write_text(json.dumps(payload))
            return path

        subset = _load("observations_for_dates")
        out = tmp_path / "o.json"
        argv = ["--corridor-dir", str(corridor), "--like", str(like_path), "--out", str(out),
                "--dates", ",".join(dates[:3])]  # fmt: skip
        assert subset.main([*argv, "--quality", str(report(dates))]) == 0
        built = json.loads(out.read_text())
        record = built["source"]["quality"]
        assert record["masked_sensor_days"] == [
            {"sensor": "S1", "date": dates[1], "verdict": "exclude", "checks": ["stuck_flow"]}
        ]
        assert record["n_masked_sensor_days"] == 1 and record["n_masked_windows"] == 12
        assert record["path"].endswith("dq_4.json") and len(record["sha256"]) == 64
        # S1 averages the two kept dates; S2 all three
        assert built["flows_veh_h"]["S1"][0] == pytest.approx((1000.0 + 3000.0) / 2)
        assert built["flows_veh_h"]["S2"][0] == pytest.approx(2000.0)
        assert subset.main(argv) == 0
        assert json.loads(out.read_text())["source"]["quality"] is None
        # a report that never judged a date of the subset is refused
        assert subset.main([*argv, "--quality", str(report(dates[:2]))]) == 2

    def test_context_must_describe_the_same_dates(self, tmp_path: Path) -> None:
        dates = NOVEMBER[:5]
        corridor = _corridor_dir(tmp_path / "c", dates, {})
        like = Observations.from_frame(
            _frame(dates), None, window_s=300.0, t0_local="06:00", duration_s=3600.0,
            corridor="s", source={"provider": "t", "dates": dates},
        )  # fmt: skip
        like_path = like.to_json(tmp_path / "like.json")
        other = {"source": {"dates": dates[:2]}, "context": {"detector_wave_speed": {"n_used": 4}}}
        (tmp_path / "ctx.json").write_text(json.dumps(other))
        subset = _load("observations_for_dates")
        argv = ["--corridor-dir", str(corridor), "--like", str(like_path), "--out",
                str(tmp_path / "o.json"), "--context-from", str(tmp_path / "ctx.json")]  # fmt: skip
        assert subset.main([*argv, "--dates", ",".join(dates[:3])]) == 2
        assert subset.main([*argv, "--dates", ",".join(dates[:2])]) == 0
        built = json.loads((tmp_path / "o.json").read_text())
        assert built["context"] == {"detector_wave_speed": {"n_used": 4}}
        assert built["source"]["dates"] == dates[:2]
