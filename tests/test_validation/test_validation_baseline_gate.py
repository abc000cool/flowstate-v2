"""The baseline gate (docs/FRISCO_PROTOCOL.md §6) on synthetic observations and scores.

Six stations on a five-minute grid, two hours, one bottleneck planted at S3
from 06:30 to 07:30. Each replicate's stored scores are built the way the
battery writes them (labelled station-hours with their simulated volumes —
t0-aligned and anchored at the study period's start —, the simulated point
speeds and segment-speed matrix), so the gate reads exactly what it reads on
a real run tree. The calibration-day and validation-day artifacts are
separate, carry their own dates and data-quality records, and match the day
split. Covers re-scoring against another day set, the 15-minute RMSPE, and
the verdict combinations: all pass, an RMSPE failure, a collision, C4 not
applicable, a validation-day failure, no validation days, too few replicates,
a wave speed read with another detector; the preconditions (day sets against
the split, quality-masked targets), C4's share of replicates with a front,
point speeds against segment means, anchored hours, and the validation days
one by one.
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from validation.baseline_gate import (
    GATE_WAVE_DETECTOR,
    MIN_GATE_REPLICATES,
    SEGMENT_SPEED_NOTE,
    SPEED_AGGREGATION_S,
    SPEED_BLOCKS_NOTE,
    STANDSTILL_MISSING_NOTE,
    STANDSTILL_NOTE,
    WAVE_MIN_FRONT_REPLICATE_SHARE,
    WAVE_MIN_VALID_PAIRS,
    GateResult,
    aggregated_rmspe,
    aggregation_blocks,
    artifact_dates,
    date_key,
    gate_from_replicates,
    render_markdown,
    rescore,
    score_validation_days,
)
from validation.metrics import geh
from validation.observed import LinkHourRecord, ObservedCorridor, ObservedScores

N_ST = 6
IDS = [f"S{i}" for i in range(N_ST)]
X = [400.0 * i for i in range(N_ST)]
WINDOW = 300.0
N_WIN = 24  # two hours from 06:00
FREE, SLOW = 27.0, 8.0
BOTTLENECK = (6, 18)  # windows 06:30-07:30 at S3

#: The day split of the fixture and the artifacts' dates (the split spells
#: them with dashes, the artifacts without: the gate compares the dates).
CAL_DATES = ("20260901", "20260908")
VAL_DATES = ("20260902",)
SPLIT: dict[str, Any] = {
    "seed": 20261004,
    "calibration_dates": ["2026-09-01", "2026-09-08"],
    "validation_dates": ["2026-09-02"],
}

#: A data-quality record as calibration.observations writes it (source.quality).
QUALITY: dict[str, Any] = {
    "path": "runs/study/data_quality.json",
    "sha256": "ab" * 32,
    "n_masked_sensor_days": 2,
    "masked_sensor_days": [["D7", "20260901"], ["D7", "20260908"]],
    "n_masked_windows": 0,
}

#: ``quality=ABSENT``: an artifact written before the key existed.
ABSENT = "absent"


def _speeds(scale: float = 1.0) -> np.ndarray:
    v = np.full((N_WIN, N_ST), FREE)
    v[BOTTLENECK[0] : BOTTLENECK[1], 3] = SLOW
    return v * scale


def _artifact(
    *,
    flow: float = 1800.0,
    speeds: np.ndarray | None = None,
    wave_used: int | None = 6,
    dates: Sequence[str] = CAL_DATES,
    missing_hour: tuple[str, int] | None = None,
    quality: dict[str, Any] | str | None = QUALITY,
) -> ObservedCorridor:
    v = _speeds() if speeds is None else speeds
    flows: dict[str, list[float | None]] = {}
    for sid in IDS:
        series: list[float | None] = [flow] * N_WIN
        if missing_hour is not None and missing_hour[0] == sid:
            h = missing_hour[1]
            series[h * 12] = None
        flows[sid] = series
    raw: dict[str, Any] = {
        "schema": "flowstate.observations/1",
        "corridor": "gate_test",
        "source": {
            "provider": "synthetic",
            "dates": list(dates),
            "excluded_detectors": {"D7": "chatters"},
            **({} if quality == ABSENT else {"quality": quality}),
        },
        "window_s": WINDOW,
        "t0_local": "06:00",
        "duration_s": WINDOW * N_WIN,
        "n_windows": N_WIN,
        "aggregation": "mean over dates per window",
        "stations": [
            {"id": sid, "x_m": x, "lanes": 3, "kind": "mainline"}
            for sid, x in zip(IDS, X, strict=True)
        ],
        "flows_veh_h": flows,
        "speeds_ms": {sid: [float(v[k, j]) for k in range(N_WIN)] for j, sid in enumerate(IDS)},
    }
    if wave_used is not None:
        raw["context"] = {
            "detector_wave_speed": {
                "median_kmh": 19.0 if wave_used else None,
                "iqr_kmh": [17.0, 21.0],
                "n_pairs": 8,
                "n_used": wave_used,
            }
        }
    return ObservedCorridor.from_dict(raw, path="obs.json")


def _on_days(observed: ObservedCorridor, dates: Sequence[str]) -> ObservedCorridor:
    """The same series recorded for other dates (another day set's artifact)."""
    return dataclasses.replace(observed, source={**observed.source, "dates": list(dates)})


def _scores(
    observed: ObservedCorridor,
    *,
    flow_scale: float = 1.0,
    sim_speeds: np.ndarray | None = None,
    point_speeds: np.ndarray | str | None = "same",
    anchored: bool = True,
) -> ObservedScores:
    """One replicate's stored scores, as ``score_run_against_observed`` lays them out.

    No warm-up: the anchored hours are the t0-aligned ones. ``point_speeds``
    ``"same"`` stores the simulated speeds as the loop readings too; None
    stores none (scores written before point speeds existed).
    """
    hourly = observed.hourly_link_flows()
    records = tuple(
        LinkHourRecord(
            station=str(s),
            x_ref_m=float(x),
            window_start_s=float(w),
            clock="",
            obs_veh_h=float(q),
            sim_veh_h=float(q) * flow_scale,
            geh=geh(float(q) * flow_scale, float(q)),
        )
        for s, x, w, q in zip(
            hourly["station"], hourly["x_ref_m"], hourly["window_start_s"], hourly["flow_veh_h"],
            strict=True,
        )
    )  # fmt: skip
    sim = _speeds() if sim_speeds is None else sim_speeds
    obs = observed.speed_matrix()
    both = np.isfinite(sim) & np.isfinite(obs)
    value = float(np.sqrt(np.mean(((sim[both] - obs[both]) / obs[both]) ** 2)))
    points = sim if isinstance(point_speeds, str) else point_speeds
    return ObservedScores(
        geh_values=tuple(r.geh for r in records),
        n_link_hours=len(records),
        rmspe=value,
        n_speed_cells=int(both.sum()),
        segment_speeds_sim=tuple(tuple(float(x) for x in row) for row in sim),
        segment_speeds_obs=tuple(tuple(float(x) for x in row) for row in obs),
        windows=tuple(range(N_WIN)),
        link_hours=records,
        link_hours_anchored=records if anchored else None,
        hour_anchor_s=0.0 if anchored else None,
        station_point_speeds_sim=(
            None if points is None else tuple(tuple(float(x) for x in row) for row in points)
        ),
        station_point_counts_sim=(
            None if points is None else tuple(tuple(25 for _ in row) for row in points)
        ),
    )


def _gate(
    *,
    n: int = MIN_GATE_REPLICATES,
    scored: ObservedCorridor | None = None,
    calibration: ObservedCorridor | None = None,
    validation: ObservedCorridor | str | None = "separate",
    sim_speeds: np.ndarray | None = None,
    collisions: Sequence[int | None] | None = None,
    waves: Sequence[float] | None = None,
    detector: str = GATE_WAVE_DETECTOR,
    split: dict[str, Any] | str | None = "matching",
    reps: Sequence[ObservedScores] | None = None,
) -> GateResult:
    """The gate on ``n`` identical replicates.

    By default the calibration days are the scored artifact (dated
    :data:`CAL_DATES`) and the validation days the same series dated
    :data:`VAL_DATES` — a separate artifact, never the calibration one.
    """
    scored = scored or _artifact()
    if reps is None:
        reps = [_scores(scored, sim_speeds=sim_speeds) for _ in range(n)]
    val = _on_days(scored, VAL_DATES) if validation == "separate" else validation
    return gate_from_replicates(
        list(reps),
        scored_against=scored,
        calibration=calibration or scored,
        validation=val,  # type: ignore[arg-type]
        wave_speeds_kmh=list(waves) if waves is not None else [18.0] * len(reps),
        wave_detector=detector,
        collision_counts=list(collisions) if collisions is not None else [0] * len(reps),
        config_hash="cafe01234567",
        scenario="gate_test",
        split=dict(SPLIT) if split == "matching" else split,  # type: ignore[arg-type]
    )


def _status(gate: GateResult, check: str, day_set: str | None = None) -> str:
    return gate.check(check, day_set).status


class TestRescore:
    def test_against_the_same_observations_is_the_stored_scores(self) -> None:
        obs = _artifact()
        stored = _scores(obs, flow_scale=1.1)
        again = rescore(stored, scored_against=obs, target=_artifact())
        assert again.scores is stored and again.n_unmatched == 0

    def test_against_another_day_set_pairs_the_stored_simulated_side(self) -> None:
        scored = _artifact(flow=1800.0)
        stored = _scores(scored, flow_scale=1.0)
        target = _artifact(flow=2000.0, speeds=_speeds(1.1), missing_hour=None)
        out = rescore(stored, scored_against=scored, target=target)
        assert out.n_unmatched == 0
        assert len(out.scores.geh_values) == N_ST * 2
        assert all(g == pytest.approx(geh(1800.0, 2000.0)) for g in out.scores.geh_values)
        assert all(
            r.obs_veh_h == 2000.0 and r.sim_veh_h == 1800.0 for r in out.scores.link_hours or ()
        )
        # simulated speeds unchanged, observed now 10 % higher: RMSPE |1/1.1 - 1|
        assert out.scores.rmspe == pytest.approx(1.0 - 1.0 / 1.1)

    def test_station_hours_the_battery_never_simulated_are_counted_not_filled(self) -> None:
        scored = _artifact(missing_hour=("S2", 1))  # the battery's artifact lacks S2's 2nd hour
        stored = _scores(scored)
        out = rescore(stored, scored_against=scored, target=_artifact(flow=1700.0))
        assert out.n_unmatched == 1
        assert len(out.scores.geh_values) == N_ST * 2 - 1

    def test_a_different_grid_is_refused(self) -> None:
        scored = _artifact()
        other = ObservedCorridor.from_dict(
            {**_raw_shifted(), "schema": "flowstate.observations/1"}, path="x.json"
        )
        with pytest.raises(ValueError, match="cannot re-score"):
            rescore(_scores(scored), scored_against=scored, target=other)


def _raw_shifted() -> dict[str, Any]:
    return {
        "corridor": "other",
        "source": {},
        "window_s": WINDOW,
        "t0_local": "06:00",
        "duration_s": WINDOW * N_WIN,
        "n_windows": N_WIN,
        "stations": [
            {"id": sid, "x_m": x + 50.0, "kind": "mainline"} for sid, x in zip(IDS, X, strict=True)
        ],
        "flows_veh_h": {},
        "speeds_ms": {},
    }


class TestAggregatedRmspe:
    def test_native_window_is_the_stored_rmspe(self) -> None:
        obs = _artifact()
        sim = _speeds() * np.where(np.arange(N_WIN)[:, None] % 2 == 0, 1.1, 0.95)
        s = _scores(obs, sim_speeds=sim)
        value, n = aggregated_rmspe(s, window_s=WINDOW, aggregation_s=WINDOW)
        assert value == pytest.approx(s.rmspe) and n == s.n_speed_cells

    def test_quarter_hour_means_cancel_within_block_noise(self) -> None:
        obs = _artifact(speeds=np.full((N_WIN, N_ST), 20.0))
        # 18, 22, 20 in every quarter hour: five-minute RMSPE sqrt(0.02/3), 15-minute zero
        pattern = np.array([18.0, 22.0, 20.0] * (N_WIN // 3))
        sim = np.repeat(pattern[:, None], N_ST, axis=1)
        s = _scores(obs, sim_speeds=sim)
        assert s.rmspe == pytest.approx(math.sqrt(0.02 / 3))
        value, n = aggregated_rmspe(s, window_s=WINDOW, aggregation_s=SPEED_AGGREGATION_S)
        assert value == pytest.approx(0.0) and n == (N_WIN // 3) * N_ST

    def test_both_block_means_use_the_same_windows(self) -> None:
        speeds = np.full((N_WIN, N_ST), 20.0)
        speeds[1, 0] = np.nan  # observed missing in the first quarter hour at S0
        obs = _artifact(speeds=speeds)
        sim = np.full((N_WIN, N_ST), 20.0)
        sim[1, 0] = 40.0  # would bias the block mean if it were not masked with the gap
        s = _scores(obs, sim_speeds=sim)
        value, _ = aggregated_rmspe(s, window_s=WINDOW, aggregation_s=SPEED_AGGREGATION_S)
        assert value == pytest.approx(0.0)

    def test_blocks_are_anchored_at_the_study_periods_start(self) -> None:
        # A 5-minute warm-up: windows 1..24. The quarter hours start at window 1
        # (as C1's hours do), so all eight are whole; aligned to the observation
        # grid, the first (windows 0-2) lacked window 0 and was dropped.
        obs = _artifact()
        s = _scores(obs)
        shifted = ObservedScores(**{**s.__dict__, "windows": tuple(range(1, N_WIN + 1))})
        _, n = aggregated_rmspe(shifted, window_s=WINDOW, aggregation_s=SPEED_AGGREGATION_S)
        assert n == (N_WIN // 3) * N_ST

    def test_the_edges_of_the_study_period_are_scored(self) -> None:
        # Review 2026-10-07: a 35-minute warm-up (windows 7..30) and a model 50 %
        # off in the study period's first two windows and its last one. On the
        # grid-aligned blocks those three windows fell in partial blocks and the
        # 15-minute RMSPE read 0.
        obs = _artifact(speeds=np.full((N_WIN, N_ST), 20.0))
        sim = np.full((N_WIN, N_ST), 20.0)
        sim[[0, 1, -1], :] = 10.0
        s = _scores(obs, sim_speeds=sim)
        late = ObservedScores(**{**s.__dict__, "windows": tuple(range(7, 7 + N_WIN))})
        value, n = aggregated_rmspe(late, window_s=WINDOW, aggregation_s=SPEED_AGGREGATION_S)
        assert n == (N_WIN // 3) * N_ST
        # first block: mean 40/3 against 20 (-1/3); last block 50/3 (-1/6); six exact
        assert value == pytest.approx(math.sqrt(((1 / 3) ** 2 + (1 / 6) ** 2) / 8))

    def test_a_trailing_partial_block_is_left_out_and_counted(self) -> None:
        blocks, left = aggregation_blocks(tuple(range(7, 30)), 3)  # 23 windows
        assert len(blocks) == 7 and left == 2
        assert blocks[0] == [0, 1, 2] and blocks[-1] == [18, 19, 20]
        assert aggregation_blocks((), 3) == ([], 0)
        assert aggregation_blocks(tuple(range(6, 24)), 12) == ([list(range(12))], 6)

    def test_odd_aggregations_are_refused(self) -> None:
        with pytest.raises(ValueError, match="whole multiple"):
            aggregated_rmspe(_scores(_artifact()), window_s=WINDOW, aggregation_s=400.0)


class TestSpeedNotes:
    """What the gate says about its speed blocks, its 15-minute row and standstills."""

    def test_windows_no_whole_block_holds_are_named(self) -> None:
        # windows 6..23: whole quarter hours, but only one whole hour from 06:30
        obs = _artifact()
        gate = _gate(reps=[_warm_scores(obs, t0_scale=1.0, anchored_scale=1.0)] * 20)
        notes = gate.day_sets["calibration"]["notes"]  # type: ignore[index]
        assert any("fill no whole block are not scored: 6 at 60 min" in n for n in notes)
        assert not any("at 15 min" in n for n in notes)
        assert any(n.startswith(SPEED_BLOCKS_NOTE) for n in gate.notes)  # rendered too
        assert SPEED_BLOCKS_NOTE in render_markdown(gate)
        assert not any(SPEED_BLOCKS_NOTE in n for n in _gate().notes)

    def test_the_replicate_mean_field_marks_the_15_minute_row(self) -> None:
        rows = _gate().day_sets["calibration"]["replicate_mean_field"]  # type: ignore[index]
        labels = [r["aggregation"] for r in rows]
        assert labels == ["5 min", "15 min (criterion)", "30 min", "60 min", "whole period"]

    @pytest.mark.parametrize("window_s", [600.0, 1200.0, 3600.0])
    def test_windows_that_do_not_divide_15_minutes_leave_c3_not_evaluated(
        self, window_s: float
    ) -> None:
        """Review 2026-10-07: 10-, 20- or 60-minute observations aborted the whole gate
        (the replicate-mean table raised on C3's 15 minutes). C3 is defined at 15 minutes
        (protocol section 4): on such windows it is not evaluated, with a note saying why,
        and every other check is scored."""
        obs = dataclasses.replace(_artifact(), window_s=window_s, duration_s=window_s * N_WIN)
        gate = _gate(scored=obs)
        for day_set in ("calibration", "validation"):
            assert _status(gate, "C3", day_set) == "not_evaluated"
            assert _status(gate, "C1", day_set) != "not_evaluated"
            block = gate.day_sets[day_set]
            assert block is not None
            assert any(
                f"{window_s:g} s windows, which do not divide C3's 15-minute" in n
                for n in block["notes"]
            )
            rows = block["replicate_mean_field"]
            assert not any(
                "(criterion)" in r["aggregation"] and r["rmspe"] != "not formed" for r in rows
            )
            assert rows[-1]["rmspe"] == "not formed"
        assert not gate.passed  # a gating check not evaluated never passes the gate
        # 5-minute observations: no such note
        assert not any("do not divide" in n for n in _gate().day_sets["calibration"]["notes"])  # type: ignore[index]

    def test_point_speeds_without_the_standstill_rule_are_named(self) -> None:
        obs = _artifact()
        old = _gate()
        assert STANDSTILL_MISSING_NOTE in old.notes and STANDSTILL_NOTE not in old.notes
        new = [dataclasses.replace(_scores(obs), station_point_standstill=True)] * 20
        gate = _gate(reps=new)
        assert STANDSTILL_NOTE in gate.notes and STANDSTILL_MISSING_NOTE not in gate.notes
        segment = _gate(reps=[_scores(obs, point_speeds=None)] * 20)
        assert STANDSTILL_NOTE not in segment.notes
        assert STANDSTILL_MISSING_NOTE not in segment.notes
        # the flag travels with a replicate re-scored against another day set
        target = _artifact(flow=2000.0)
        assert rescore(new[0], scored_against=obs, target=target).scores.station_point_standstill


class TestVerdicts:
    def test_all_checks_pass(self) -> None:
        gate = _gate()
        assert gate.passed and gate.strategy_results_allowed
        assert gate.reasons == ()
        assert gate.headline().startswith("Baseline gate PASSED")
        for day_set in ("calibration", "validation"):
            for check in ("C1", "C2", "C3", "C6"):
                assert _status(gate, check, day_set) == "pass", (check, day_set)
        assert _status(gate, "C4") == "pass" and _status(gate, "C5") == "pass"
        c6 = gate.day_sets["calibration"]["bottlenecks"]  # type: ignore[index]
        assert [b["upstream"] for b in c6["observed"]] == ["S3"]
        assert c6["observed"][0]["activation_clock"] == "06:30"
        assert "crossing the station's position" in c6["notes"][0]
        assert not gate.check("C2", "calibration").gating
        for pre in ("replicates", "days", "quality"):
            assert _status(gate, pre) == "pass" and gate.check(pre).gating, pre
        assert "hours anchored at the study period's start" in gate.check("C1", "validation").plain
        assert gate.day_sets["validation"]["dates"] == list(VAL_DATES)  # type: ignore[index]

    def test_rmspe_failure_fails_the_gate_and_says_by_how_much(self) -> None:
        gate = _gate(sim_speeds=_speeds(1.25))
        assert not gate.passed
        c3 = gate.check("C3", "calibration")
        assert c3.status == "fail" and c3.value == pytest.approx(0.25)
        assert c3.shortfall == pytest.approx(0.10)
        assert "RMSPE of 15-minute station mean speeds 25.0 %" in c3.plain
        assert "over by 10.0 percentage points" in c3.plain
        assert any("Speeds, calibration days" in r for r in gate.reasons)
        assert _status(gate, "C1", "calibration") == "pass"
        assert _status(gate, "C6", "calibration") == "pass"  # the bottleneck is still there

    def test_a_collision_fails_the_gate(self) -> None:
        gate = _gate(collisions=[0] * 19 + [2])
        assert not gate.passed
        c5 = gate.check("C5")
        assert c5.status == "fail" and c5.value == 2.0
        assert "2 SUMO collision(s) in 1 of 20 run(s)" in c5.plain

    def test_collisions_not_recorded_is_never_a_pass(self) -> None:
        gate = _gate(collisions=[0] * 19 + [None])
        assert not gate.passed and _status(gate, "C5") == "not_recorded"

    def test_c4_not_applicable_lets_the_gate_pass(self) -> None:
        obs = _artifact(wave_used=WAVE_MIN_VALID_PAIRS - 1)
        gate = _gate(scored=obs, waves=[math.nan] * MIN_GATE_REPLICATES)
        c4 = gate.check("C4")
        assert c4.status == "not_applicable" and c4.satisfied
        assert f"{WAVE_MIN_VALID_PAIRS - 1} of 8 station pairs" in c4.plain
        assert gate.passed

    def test_c4_out_of_band_and_undetermined_applicability(self) -> None:
        gate = _gate(waves=[30.0] * MIN_GATE_REPLICATES)
        c4 = gate.check("C4")
        assert c4.status == "fail" and c4.shortfall == pytest.approx(8.0)
        assert "outside it by 8.0 km/h" in c4.plain
        observed = (
            "observed on the calibration days (detector cross-correlation): median 19.0 km/h "
            "from 6 of 8 station pairs"
        )
        assert observed in c4.plain
        no_context = _gate(scored=_artifact(wave_used=None))
        assert no_context.check("C4").status == "pass"
        assert "was not determined" in no_context.check("C4").plain

    def test_wave_speed_from_another_detector_is_not_evaluated(self) -> None:
        gate = _gate(detector="standard")
        assert _status(gate, "C4") == "not_evaluated" and not gate.passed

    def test_validation_day_failure_fails_the_gate(self) -> None:
        validation = _artifact(flow=2600.0, dates=VAL_DATES)
        gate = _gate(validation=validation)
        assert _status(gate, "C1", "calibration") == "pass"
        c1v = gate.check("C1", "validation")
        assert c1v.status == "fail" and c1v.value == 0.0
        assert c1v.shortfall == pytest.approx(0.85)
        assert not gate.passed
        assert gate.day_sets["validation"]["dates"] == list(VAL_DATES)  # type: ignore[index]

    def test_no_validation_days_fails_the_gate(self) -> None:
        gate = _gate(validation=None)
        assert not gate.passed
        for check in ("C1", "C3", "C6"):
            assert _status(gate, check, "validation") == "not_evaluated"
        assert gate.day_sets["validation"] is None
        assert any("no validation-day observations" in n for n in gate.notes)

    def test_too_few_replicates_fail_the_gate(self) -> None:
        gate = _gate(n=4)
        assert not gate.passed and _status(gate, "replicates") == "fail"
        assert "4 seeded replicate(s)" in gate.check("replicates").plain

    def test_a_missed_bottleneck_fails_c6(self) -> None:
        gate = _gate(sim_speeds=np.full((N_WIN, N_ST), FREE))
        c6 = gate.check("C6", "calibration")
        assert c6.status == "fail" and "rule location fails" in c6.plain
        assert not gate.passed


class TestSerialisation:
    def test_json_round_trip_and_markdown(self, tmp_path: Path) -> None:
        gate = _gate(sim_speeds=_speeds(1.25))
        path = gate.to_json(tmp_path / "gate.json")
        raw = json.loads(path.read_text())
        assert raw["schema"] == "flowstate.baseline_gate/1"
        assert raw["strategy_results_allowed"] is False
        assert raw["excluded_detectors"] == {"D7": "chatters"}
        assert raw["thresholds"]["speed_aggregation_s"] == SPEED_AGGREGATION_S
        back = GateResult.from_json(path)
        assert back.checks == gate.checks and back.passed == gate.passed
        md = render_markdown(gate)
        assert md.startswith("# Baseline gate")
        assert "Baseline gate FAILED" in md and "## Bottlenecks, calibration days" in md
        assert "| S3→S4 | 06:30 |" in md


class TestDaySets:
    """§3.2: each day set's artifact holds exactly its side of the split."""

    def test_no_split_fails_the_gate(self) -> None:
        gate = _gate(split=None)
        days = gate.check("days")
        assert days.status == "fail" and days.gating and not gate.passed
        assert "no day split was supplied" in days.plain

    def test_the_calibration_artifact_scored_as_validation_fails(self) -> None:
        gate = _gate(validation=_artifact())  # the calibration artifact again
        days = gate.check("days")
        assert days.status == "fail" and not gate.passed
        assert "validation-day artifact holds 20260901, 20260908, not on the split's" in days.plain
        assert "lacks 20260902" in days.plain

    def test_the_all_dates_artifact_as_calibration_fails(self) -> None:
        all_dates = _artifact(dates=(*CAL_DATES, *VAL_DATES))
        gate = _gate(scored=all_dates)
        days = gate.check("days")
        assert days.status == "fail"
        assert "calibration-day artifact holds 20260902, not on the split's calibration side" in (
            days.plain
        )

    def test_review_r2_an_empty_validation_side_and_shared_artifact_fail(self) -> None:
        obs = _artifact(dates=("20260901", "20260902"))
        gate = _gate(
            scored=obs,
            validation=obs,
            split={"seed": 1, "calibration_dates": ["2026-09-03"], "validation_dates": []},
        )
        assert not gate.passed
        assert "the day split lists no validation days" in gate.check("days").plain

    def test_overlapping_sides_fail(self) -> None:
        split = dict(SPLIT, validation_dates=["2026-09-02", "2026-09-08"])
        gate = _gate(split=split)
        assert gate.check("days").status == "fail"
        assert "share 20260908" in gate.check("days").plain

    def test_dates_compare_across_spellings_and_from_the_subset_record(self) -> None:
        assert date_key("2026-09-01") == date_key("20260901") == "20260901"
        assert date_key("Sept 1") is None
        scored = _artifact()
        source = {k: v for k, v in scored.source.items() if k != "dates"}
        source["subset"] = {"set": "calibration", "dates": ["2026-09-01", "2026-09-08"]}
        via_subset = dataclasses.replace(scored, source=source)
        assert artifact_dates(via_subset) == ("2026-09-01", "2026-09-08")
        validation = _on_days(scored, VAL_DATES)
        gate = _gate(scored=via_subset, validation=validation)
        assert gate.check("days").status == "pass" and gate.passed
        wrong_side = dataclasses.replace(
            scored, source={**scored.source, "subset": {"set": "validation"}}
        )
        days = _gate(scored=wrong_side, validation=validation).check("days")
        assert days.status == "fail" and "built for the validation side" in days.plain

    def test_without_validation_days_the_check_is_not_evaluated(self) -> None:
        gate = _gate(validation=None)
        assert gate.check("days").status == "not_evaluated" and not gate.passed


class TestQualityMasked:
    """§2.2: the targets record the data-quality masking (source.quality)."""

    @pytest.mark.parametrize("quality", [None, ABSENT, {"path": "dq.json"}])
    def test_unmasked_calibration_targets_fail_the_gate(self, quality: Any) -> None:
        gate = _gate(scored=_artifact(quality=quality), validation=_on_days(_artifact(), VAL_DATES))
        q = gate.check("quality")
        assert q.status == "fail" and q.gating and not gate.passed
        assert "the calibration-day targets carry no data-quality record" in q.plain
        assert any("Targets quality-masked" in r for r in gate.reasons)
        assert "Targets quality-masked (both day sets)" in gate.headline()

    def test_unmasked_validation_targets_fail_the_gate(self) -> None:
        validation = _on_days(_artifact(quality=None), VAL_DATES)
        gate = _gate(validation=validation)
        assert gate.check("quality").status == "fail" and not gate.passed
        assert "the validation-day targets" in gate.check("quality").plain

    def test_masked_targets_pass_and_name_the_artifact(self) -> None:
        gate = _gate()
        q = gate.check("quality")
        assert q.status == "pass"
        assert "runs/study/data_quality.json (sha256 abababababab): 2 masked detector-day(s)" in (
            q.plain
        )
        assert gate.day_sets["calibration"]["quality"]["sha256"] == QUALITY["sha256"]  # type: ignore[index]


class TestWaveShare:
    """§4, C4: a backward front in at least 80 % of the replicates."""

    @pytest.mark.parametrize(("n_front", "ok"), [(16, True), (15, False)])
    def test_the_share_edge(self, n_front: int, ok: bool) -> None:
        n = MIN_GATE_REPLICATES
        gate = _gate(waves=[18.0] * n_front + [math.nan] * (n - n_front))
        c4 = gate.check("C4")
        assert (c4.status == "pass") is ok
        assert f"a backward front in {n_front} of {n} replicate(s)" in c4.plain
        if not ok:
            assert "short by 1 replicate(s)" in c4.plain and not gate.passed
        assert WAVE_MIN_FRONT_REPLICATE_SHARE == 0.80

    def test_review_r1_one_front_in_twenty_fails(self) -> None:
        gate = _gate(waves=[18.0] + [math.nan] * 19)
        assert gate.check("C4").status == "fail" and not gate.passed

    def test_the_interval_of_the_replicate_speeds_is_reported(self) -> None:
        waves = [16.0, 18.0, 20.0] * 6 + [18.0, 18.0]
        c4 = _gate(waves=waves).check("C4")
        from validation.metrics import ci

        interval = ci(waves)
        assert c4.value == pytest.approx(interval.mean)
        assert f"95 % interval {interval.lo95:.1f} to {interval.hi95:.1f} km/h" in c4.plain

    def test_a_replicate_without_a_reading_is_a_miss(self) -> None:
        gate = _gate(waves=[18.0] * 15)  # 20 replicates, 15 readings
        c4 = gate.check("C4")
        assert c4.status == "fail" and "in 15 of 20 replicate(s)" in c4.plain


class TestPointSpeeds:
    """§5: C3 and C6 read the loop at the station, segment means only as a stated substitute."""

    @staticmethod
    def _smeared() -> np.ndarray:
        seg = _speeds()
        seg[BOTTLENECK[0] : BOTTLENECK[1], 3] = 20.0  # segment mean above 40 mph
        return seg

    def test_point_speeds_are_scored_when_present(self) -> None:
        obs = _artifact()
        reps = [_scores(obs, sim_speeds=self._smeared(), point_speeds=_speeds()) for _ in range(20)]
        gate = _gate(reps=reps)
        assert _status(gate, "C6", "calibration") == "pass"
        assert _status(gate, "C3", "calibration") == "pass"
        assert gate.check("C3", "calibration").value == pytest.approx(0.0)
        assert "point speeds at the stations" in gate.check("C6", "calibration").plain
        assert gate.day_sets["calibration"]["speed_source"] == "point"  # type: ignore[index]

    def test_without_point_speeds_the_segment_means_are_used_and_said(self) -> None:
        obs = _artifact()
        reps = [_scores(obs, sim_speeds=self._smeared(), point_speeds=None) for _ in range(20)]
        gate = _gate(reps=reps)
        c6 = gate.check("C6", "calibration")
        assert c6.status == "fail" and "rule location fails" in c6.plain
        assert "segment means substituted for point speeds" in c6.plain
        assert _status(gate, "C3", "calibration") == "fail"
        assert SEGMENT_SPEED_NOTE in gate.notes
        assert gate.day_sets["calibration"]["speed_source"] == "segment"  # type: ignore[index]


def _warm_scores(
    observed: ObservedCorridor, *, t0_scale: float, anchored_scale: float | None
) -> ObservedScores:
    """A replicate with a 30-minute warm-up: windows 6..23 scored.

    Its t0-aligned hours (only 07:00-08:00 lies wholly after the warm-up)
    carry volumes ``t0_scale`` x observed; its anchored hours (06:30-07:30)
    ``anchored_scale`` x observed, None for scores without anchored hours.
    """
    windows = tuple(range(6, N_WIN))

    def records(anchor: int, scale: float) -> tuple[LinkHourRecord, ...]:
        hourly = observed.hourly_link_flows(anchor_window=anchor)
        out = []
        for s, x, w, q in zip(
            hourly["station"], hourly["x_ref_m"], hourly["window_start_s"], hourly["flow_veh_h"],
            strict=True,
        ):  # fmt: skip
            if all(round(w / WINDOW) + i in windows for i in range(12)):
                out.append(
                    LinkHourRecord(str(s), float(x), float(w), "", float(q), q * scale,
                                   geh(q * scale, float(q)))
                )  # fmt: skip
        return tuple(out)

    t0 = records(0, t0_scale)
    sim = _speeds()[6:]
    obs = observed.speed_matrix()[6:]
    return ObservedScores(
        geh_values=tuple(r.geh for r in t0),
        n_link_hours=len(t0),
        rmspe=0.0,
        n_speed_cells=sim.size,
        segment_speeds_sim=tuple(tuple(float(v) for v in row) for row in sim),
        segment_speeds_obs=tuple(tuple(float(v) for v in row) for row in obs),
        windows=windows,
        link_hours=t0,
        link_hours_anchored=None if anchored_scale is None else records(6, anchored_scale),
        hour_anchor_s=None if anchored_scale is None else 6 * WINDOW,
        station_point_speeds_sim=tuple(tuple(float(v) for v in row) for row in sim),
        station_point_counts_sim=tuple(tuple(25 for _ in row) for row in sim),
    )


class TestAnchoredHours:
    """§4, C1: hours anchored at the study period's start (the warm-up's end)."""

    def test_c1_reads_the_anchored_hours(self) -> None:
        obs = _artifact()
        reps = [_warm_scores(obs, t0_scale=1.5, anchored_scale=1.0) for _ in range(20)]
        gate = _gate(reps=reps)
        c1 = gate.check("C1", "calibration")
        assert c1.status == "pass" and c1.value == pytest.approx(1.0)
        assert "hours anchored at the study period's start (1800 s)" in c1.plain
        block = gate.day_sets["calibration"]
        assert block["hour_anchor"] == "study_period_start"  # type: ignore[index]
        assert block["geh"]["n_comparisons"] == N_ST * 20  # type: ignore[index]

    def test_stored_scores_without_them_fall_back_and_say_so(self) -> None:
        obs = _artifact()
        reps = [_warm_scores(obs, t0_scale=1.5, anchored_scale=None) for _ in range(20)]
        gate = _gate(reps=reps)
        c1 = gate.check("C1", "calibration")
        assert c1.status == "fail"
        assert "hours aligned to t0_local because the stored scores predate anchored" in c1.plain
        assert gate.day_sets["calibration"]["hour_anchor"] == "t0_local"  # type: ignore[index]

    def test_anchored_hours_are_paired_with_another_day_set_on_the_same_anchor(self) -> None:
        obs = _artifact()
        stored = _warm_scores(obs, t0_scale=1.0, anchored_scale=1.0)
        target = _artifact(flow=2000.0)
        out = rescore(stored, scored_against=obs, target=target)
        anchored = out.scores.link_hours_anchored
        assert anchored is not None and len(anchored) == N_ST
        assert {r.window_start_s for r in anchored} == {6 * WINDOW}
        assert all(r.obs_veh_h == 2000.0 and r.sim_veh_h == 1800.0 for r in anchored)
        assert out.n_unmatched_anchored == 0


class TestValidationDaysOneByOne:
    """§3.5: each validation day scored on its own, reported, never gating."""

    def test_rows_refusals_and_missing_days(self) -> None:
        obs = _artifact()
        split = dict(SPLIT, validation_dates=["2026-09-02", "2026-09-04"])
        days = [
            ("day_0902.json", _on_days(_artifact(flow=2600.0), ("20260902",))),
            ("day_0903.json", _on_days(obs, ("20260903",))),
            ("two_days.json", _on_days(obs, ("20260902", "20260904"))),
        ]
        reps = [_scores(obs) for _ in range(20)]
        out = score_validation_days(reps, scored_against=obs, days=days, split=split)
        assert [r["date"] for r in out["rows"]] == ["20260902"]
        checks = {c["check"]: c for c in out["rows"][0]["checks"]}
        assert set(checks) == {"C1", "C3", "C6"}
        assert checks["C1"]["status"] == "fail" and not checks["C1"]["gating"]
        assert "Link flows, validation day 20260902:" in checks["C1"]["plain"]
        assert checks["C3"]["status"] == "pass" and checks["C6"]["status"] == "pass"
        reasons = {r["path"]: r["reason"] for r in out["refused"]}
        assert "not on the split's validation side" in reasons["day_0903.json"]
        assert "not exactly one date" in reasons["two_days.json"]
        assert out["missing_dates"] == ["20260904"]

    def test_the_per_day_table_does_not_gate(self, tmp_path: Path) -> None:
        obs = _artifact()
        reps = [_scores(obs) for _ in range(20)]
        bad_day = _on_days(_artifact(flow=2600.0), VAL_DATES)
        gate = gate_from_replicates(
            reps,
            scored_against=obs,
            calibration=obs,
            validation=_on_days(obs, VAL_DATES),
            wave_speeds_kmh=[18.0] * 20,
            wave_detector=GATE_WAVE_DETECTOR,
            collision_counts=[0] * 20,
            config_hash="cafe01234567",
            split=dict(SPLIT),
            validation_days=[("day.json", bad_day)],
        )
        assert gate.passed and gate.per_day is not None
        assert gate.per_day["rows"][0]["checks"][0]["status"] == "fail"
        md = render_markdown(gate)
        assert "## Validation days one by one (reported, not gating)" in md
        assert "| 20260902 | FAIL (0) | PASS (0) | PASS (0) | yes |" in md
        back = GateResult.from_json(gate.to_json(tmp_path / "gate.json"))
        assert back.per_day == json.loads((tmp_path / "gate.json").read_text())["per_day"]
