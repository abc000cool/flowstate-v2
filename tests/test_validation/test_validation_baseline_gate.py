"""The baseline gate (docs/FRISCO_PROTOCOL.md §6) on synthetic observations and scores.

Six stations on a five-minute grid, two hours, one bottleneck planted at S3
from 06:30 to 07:30. Each replicate's stored scores are built the way the
battery writes them (labelled station-hours with their simulated volumes,
the simulated segment-speed matrix), so the gate reads exactly what it reads
on a real run tree. Covers re-scoring against another day set, the 15-minute
RMSPE, and the verdict combinations: all pass, an RMSPE failure, a collision,
C4 not applicable, a validation-day failure, no validation days, too few
replicates, a wave speed read with another detector.
"""

from __future__ import annotations

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
    SPEED_AGGREGATION_S,
    WAVE_MIN_VALID_PAIRS,
    GateResult,
    aggregated_rmspe,
    gate_from_replicates,
    render_markdown,
    rescore,
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


def _speeds(scale: float = 1.0) -> np.ndarray:
    v = np.full((N_WIN, N_ST), FREE)
    v[BOTTLENECK[0] : BOTTLENECK[1], 3] = SLOW
    return v * scale


def _artifact(
    *,
    flow: float = 1800.0,
    speeds: np.ndarray | None = None,
    wave_used: int | None = 6,
    dates: Sequence[str] = ("20260901", "20260902"),
    missing_hour: tuple[str, int] | None = None,
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


def _scores(
    observed: ObservedCorridor, *, flow_scale: float = 1.0, sim_speeds: np.ndarray | None = None
) -> ObservedScores:
    """One replicate's stored scores, as ``score_run_against_observed`` lays them out."""
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
    return ObservedScores(
        geh_values=tuple(r.geh for r in records),
        n_link_hours=len(records),
        rmspe=value,
        n_speed_cells=int(both.sum()),
        segment_speeds_sim=tuple(tuple(float(x) for x in row) for row in sim),
        segment_speeds_obs=tuple(tuple(float(x) for x in row) for row in obs),
        windows=tuple(range(N_WIN)),
        link_hours=records,
    )


def _gate(
    *,
    n: int = MIN_GATE_REPLICATES,
    scored: ObservedCorridor | None = None,
    calibration: ObservedCorridor | None = None,
    validation: ObservedCorridor | str | None = "same",
    sim_speeds: np.ndarray | None = None,
    collisions: Sequence[int | None] | None = None,
    waves: Sequence[float] | None = None,
    detector: str = GATE_WAVE_DETECTOR,
) -> GateResult:
    scored = scored or _artifact()
    reps = [_scores(scored, sim_speeds=sim_speeds) for _ in range(n)]
    return gate_from_replicates(
        reps,
        scored_against=scored,
        calibration=calibration or scored,
        validation=scored if validation == "same" else validation,  # type: ignore[arg-type]
        wave_speeds_kmh=list(waves) if waves is not None else [18.0] * n,
        wave_detector=detector,
        collision_counts=list(collisions) if collisions is not None else [0] * n,
        config_hash="cafe01234567",
        scenario="gate_test",
        split={"seed": 20261004, "calibration_dates": ["2026-09-01"], "validation_dates": []},
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

    def test_incomplete_blocks_are_dropped_and_odd_aggregations_refused(self) -> None:
        obs = _artifact()
        s = _scores(obs)
        partial = ObservedScores(**{**s.__dict__, "windows": tuple(range(1, N_WIN + 1))})
        _, n = aggregated_rmspe(partial, window_s=WINDOW, aggregation_s=SPEED_AGGREGATION_S)
        assert n == (N_WIN // 3 - 1) * N_ST  # block 0 lacks window 0
        with pytest.raises(ValueError, match="whole multiple"):
            aggregated_rmspe(s, window_s=WINDOW, aggregation_s=400.0)


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
        assert "segment" in c6["notes"][0]
        assert not gate.check("C2", "calibration").gating

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
        validation = _artifact(flow=2600.0, dates=("20260903",))
        gate = _gate(validation=validation)
        assert _status(gate, "C1", "calibration") == "pass"
        c1v = gate.check("C1", "validation")
        assert c1v.status == "fail" and c1v.value == 0.0
        assert c1v.shortfall == pytest.approx(0.85)
        assert not gate.passed
        assert gate.day_sets["validation"]["dates"] == ["20260903"]  # type: ignore[index]

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
