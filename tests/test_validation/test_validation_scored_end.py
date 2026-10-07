"""The scored end of a run with a cool-down (docs/FRISCO_PROTOCOL.md §8.2).

§8.2: "Runs end with a cool-down after the last scored departure of at least
the stretch's free-flow travel time, so that censoring is rare." Before
``scored_end_s`` existed, the scored departures ended at the run's end, so a
cool-down added its own departures to the demand set ``D`` and censoring did
not fall (the 2026-10-07 review's probe: 2 s headway, 600 s free-flow, 7,200
vehicles and 299 censored at a 4-h end; 7,800 and still 299 with a 20-min
cool-down), and a zero-demand cool-down diluted the reference-section
throughput. Checked here, without SUMO:

* the waiting measures count only departures planned before the scored end,
  their clocks still censored at the run's end, and a cool-down then removes
  the censoring without adding vehicles;
* the standard metrics stop at the scored end (throughput over the study
  period, σ_v, VMT/VHT and waves on its rows) while journeys begun inside it
  are followed into the cool-down;
* the battery threads the end through every measurement, records it, and
  refuses stored files scored with another one; the report does the same and
  says so in its measurement note;
* without a scored end every value is what it was.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from tests.test_validation.test_validation_baseline_gate import _artifact
from tests.test_validation.test_validation_metrics import (
    CORRIDOR_M,
    ENTRY_SPEED,
    WARMUP_S,
    _staggered_traj,
    _write_run,
)
from tests.test_validation.test_validation_waiting import T_END, T_LO, _row, hand_table
from validation import report as report_mod
from validation.battery import (
    METRICS_FILE,
    analyse_replicate,
    load_replicate_analysis,
    measurement_window,
    replicate_waiting,
    replicate_wave_speed_kmh,
    score_replicate,
)
from validation.criteria import get_profile
from validation.metrics import (
    JOURNEY_COLUMNS,
    JOURNEYS_FILE,
    compute_metrics,
    compute_waiting_metrics,
    waiting_metrics,
)
from validation.report import generate_report
from validation.waves import STACK_DETECTOR

H = 3600.0


class TestWaitingScoredDepartures:
    def test_departures_after_the_scored_end_are_not_in_the_demand(self) -> None:
        # [100, 320): v1, v2, v3; v4 (planned 350) departs in the cool-down
        w = waiting_metrics(hand_table(), t_lo=T_LO, t_end=T_END, scored_end_s=320.0)
        assert w.n_demand_veh == 3
        assert w.n_not_inserted == 0
        assert w.n_censored == 1  # v3, still running at the run's end (400 s)
        assert w.insertion_delay_veh_h == pytest.approx((0.5 + 10.0 + 5.0) / H)
        assert w.meter_wait_veh_h == pytest.approx(30.0 / H)
        # clocks still stop at the run's end, not at the scored end: v3 counts 100 s
        assert w.total_delay_incl_waiting_veh_h == pytest.approx((20.5 + 72.0 + 60.0) / H)
        assert w.n_tt_incl_waiting_veh == 3 and w.n_tt_censored == 1
        assert w.mean_tt_incl_waiting_s == pytest.approx((100.5 + 150.0 + 100.0) / 3.0)

    def test_a_scored_end_at_the_run_end_is_the_old_rule(self) -> None:
        old = waiting_metrics(hand_table(), t_lo=T_LO, t_end=T_END)
        assert waiting_metrics(hand_table(), t_lo=T_LO, t_end=T_END, scored_end_s=T_END) == old

    @pytest.mark.parametrize("end", [T_LO, 50.0, T_END + 1.0, math.nan])
    def test_a_scored_end_outside_the_run_is_refused(self, end: float) -> None:
        with pytest.raises(ValueError, match="scored_end_s"):
            waiting_metrics(hand_table(), t_lo=T_LO, t_end=T_END, scored_end_s=end)

    @staticmethod
    def _ledger(run_end: float, headway: float = 2.0, free_flow: float = 600.0) -> pd.DataFrame:
        """The review's probe: one departure every 2 s, 600 s free-flow, empty road."""
        planned = np.arange(0.0, run_end, headway)
        arrived = planned + free_flow <= run_end
        rows = [
            _row(
                f"v{i:05d}",
                float(p),
                free_flow_s=free_flow,
                arrived=bool(a),
                arrival_s=float(p + free_flow) if a else np.nan,
                free_flow_covered_s=free_flow if a else float(run_end - p),
            )
            for i, (p, a) in enumerate(zip(planned, arrived, strict=True))
        ]
        return pd.DataFrame(rows, columns=list(JOURNEY_COLUMNS))

    def test_a_cool_down_removes_censoring_without_adding_vehicles(self) -> None:
        study_end, run_end = 14_400.0, 15_600.0  # a 4-h study period, a 20-min cool-down
        no_cool = waiting_metrics(self._ledger(study_end), t_lo=0.0, t_end=study_end)
        assert (no_cool.n_demand_veh, no_cool.n_censored) == (7200, 299)
        ledger = self._ledger(run_end)
        # the old rule: the cool-down's departures join D and censoring does not fall
        old = waiting_metrics(ledger, t_lo=0.0, t_end=run_end)
        assert (old.n_demand_veh, old.n_censored) == (7800, 299)
        new = waiting_metrics(ledger, t_lo=0.0, t_end=run_end, scored_end_s=study_end)
        assert (new.n_demand_veh, new.n_censored) == (7200, 0)
        assert new.total_delay_incl_waiting_veh_h == pytest.approx(0.0, abs=1e-9)

    def test_the_run_directory_passes_the_scored_end_through(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        run.mkdir()
        meta = {
            "config": {"sim": {"warmup_s": T_LO}},
            "journeys": {"file": JOURNEYS_FILE, "end_s": T_END},
        }
        (run / "meta.json").write_text(json.dumps(meta))
        hand_table().to_parquet(run / JOURNEYS_FILE)
        expected = waiting_metrics(hand_table(), t_lo=T_LO, t_end=T_END, scored_end_s=320.0)
        assert compute_waiting_metrics(run, scored_end_s=320.0) == expected
        assert compute_waiting_metrics(run) == waiting_metrics(hand_table(), t_lo=T_LO, t_end=T_END)


class TestMetricsScoredEnd:
    """The staggered fixture: entries at 0, 50, 100, 150 s, 100 s journeys, 60 s warm-up."""

    @pytest.fixture()
    def run_dir(self, tmp_path: Path) -> Path:
        return _write_run(
            tmp_path / "cool",
            fuel_total_ml=1000.0,
            traj=_staggered_traj(),
            warmup_s=WARMUP_S,
            extra_meta={"fuel_total_ml_post_warmup": 800.0},
        )

    def test_every_windowed_measure_stops_at_the_scored_end(self, run_dir: Path) -> None:
        m = compute_metrics(run_dir, x_ref=1250.0, span=(0.0, CORRIDOR_M), scored_end_s=160.0)
        # crossings of 1250 m at 50, 100, 150, 200 s: two in [60, 160) over 100 s
        assert m.throughput_veh_h == pytest.approx(2.0 / 100.0 * H)
        # journeys begun in [60, 160) (100 s and 150 s), the second followed into
        # the cool-down to its end at 250 s
        assert m.n_travel_time_veh == 2
        assert m.mean_tt_s == pytest.approx(CORRIDOR_M / ENTRY_SPEED)
        # rows in [60, 160): 40 + 90 + 59.5 + 9.5 s of travel at 25 m/s
        assert m.vht_veh_h == pytest.approx(199.0 / H)
        assert m.vmt_veh_km == pytest.approx(199.0 * ENTRY_SPEED / 1000.0)
        # a post-warm-up fuel total cannot be cut at 160 s: it keeps the
        # post-warm-up VMT (1000 + 2250 + 2500 + 2500 m) as its denominator
        assert m.fuel_ml_per_veh_km == pytest.approx(800.0 / 8.25)

    def test_without_a_scored_end_the_cool_down_dilutes_throughput(self, run_dir: Path) -> None:
        old = compute_metrics(run_dir, x_ref=1250.0, span=(0.0, CORRIDOR_M))
        assert old.throughput_veh_h == pytest.approx(3.0 / 190.0 * H)  # [60, 250]

    def test_an_end_at_or_after_the_last_sample_changes_nothing(self, run_dir: Path) -> None:
        old = compute_metrics(run_dir)
        assert compute_metrics(run_dir, scored_end_s=250.0) == old
        assert compute_metrics(run_dir, scored_end_s=10_000.0) == old

    @pytest.mark.parametrize("end", [WARMUP_S, 10.0, math.inf])
    def test_an_end_before_the_window_or_not_finite_is_refused(
        self, run_dir: Path, end: float
    ) -> None:
        with pytest.raises(ValueError, match="scored_end_s"):
            compute_metrics(run_dir, scored_end_s=end)


# A replicate on the gate fixture's grid: six stations 0-2000 m, 300 s windows,
# 7200 s; 600 s warm-up, study period to 6600 s, then a 600 s cool-down.
DURATION_S = 7200.0
WARM_S = 600.0
STUDY_END_S = 6600.0
HEADWAY_S = 30.0
SPEED_MS = 25.0
LENGTH_M = 2100.0


def _replicate(root: Path) -> Path:
    """Trajectories, ledger and meta of one synthetic replicate (module comment)."""
    root.mkdir(parents=True)
    entries = np.arange(0.0, DURATION_S, HEADWAY_S)
    trip = LENGTH_M / SPEED_MS  # 84 s
    frames = []
    ledger = []
    for i, e in enumerate(entries):
        t = np.arange(e, min(e + trip, DURATION_S) + 0.5, 1.0)
        t = t[t <= DURATION_S]
        frames.append(
            pd.DataFrame({"t": t, "veh_id": f"v{i:04d}", "x": SPEED_MS * (t - e), "v": SPEED_MS})
        )
        done = e + trip <= DURATION_S
        ledger.append(
            _row(
                f"v{i:04d}",
                float(e),
                route_length_m=LENGTH_M,
                free_flow_s=trip,
                arrived=bool(done),
                arrival_s=float(e + trip) if done else np.nan,
                free_flow_covered_s=trip if done else float(DURATION_S - e),
            )
        )
    traj = pd.concat(frames, ignore_index=True).sort_values(["t", "veh_id"], kind="stable")
    traj.reset_index(drop=True).to_parquet(root / "trajectories.parquet")
    pd.DataFrame(ledger, columns=list(JOURNEY_COLUMNS)).to_parquet(root / JOURNEYS_FILE)
    meta: dict[str, Any] = {
        "config_hash": "c001d0wn0000",
        "seed": 5,
        "tier": "micro",
        "seeded": False,
        "config": {"sim": {"warmup_s": WARM_S, "duration_s": DURATION_S}},
        "journeys": {"file": JOURNEYS_FILE, "end_s": DURATION_S},
        "n_vehicles_planned": len(entries),
        "n_vehicles_departed": len(entries),
    }
    (root / "meta.json").write_text(json.dumps(meta))
    return root


class TestBattery:
    def test_the_measurement_window_ends_at_the_scored_end(self) -> None:
        meta = {"config": {"sim": {"warmup_s": WARM_S, "duration_s": DURATION_S}}}
        assert measurement_window(meta) == (WARM_S, DURATION_S)
        assert measurement_window(meta, STUDY_END_S) == (WARM_S, STUDY_END_S)
        for bad in (WARM_S, DURATION_S + 1.0):
            with pytest.raises(ValueError, match="scored_end_s"):
                measurement_window(meta, bad)

    def test_every_measurement_stops_at_the_scored_end(self, tmp_path: Path) -> None:
        run = _replicate(tmp_path / "runs" / "c001d0wn0000" / "5")
        observed = _artifact()
        profile = get_profile("fhwa_default")
        analysis = analyse_replicate(
            run,
            observed,
            profile=profile,
            x_ref=1000.0,
            span=(0.0, 2000.0),
            x_offset_m=0.0,
            scored_end_s=STUDY_END_S,
        )
        # windows wholly inside [600, 6600): 2..21, not the cool-down's 22 and 23
        assert analysis.scores.windows == tuple(range(2, 22))
        assert score_replicate(run, observed).windows == tuple(range(2, 24))
        # one vehicle per 30 s crosses 1000 m: 200 crossings in 6000 s
        assert analysis.metrics.throughput_veh_h == pytest.approx(120.0)
        assert analysis.waiting is not None
        assert analysis.waiting.n_demand_veh == 200  # departures 600 ... 6570 s
        assert analysis.waiting.n_censored == 0
        assert replicate_waiting(run, STUDY_END_S) == analysis.waiting
        old = replicate_waiting(run)
        assert old is not None and (old.n_demand_veh, old.n_censored) == (220, 2)
        stored = json.loads((run / METRICS_FILE).read_text())
        assert stored["scored_end_s"] == STUDY_END_S
        assert list(stored)[-1] == "scored_end_s"  # additive, after every other key
        detector = profile.wave_detector
        speed = replicate_wave_speed_kmh(run, detector, scored_end_s=STUDY_END_S)
        assert speed == analysis.wave_speed_kmh or (
            math.isnan(speed) and math.isnan(analysis.wave_speed_kmh)
        )

    def test_stored_files_are_reread_only_for_their_own_scored_end(self, tmp_path: Path) -> None:
        run = _replicate(tmp_path / "r")
        kwargs: dict[str, Any] = {
            "profile": get_profile("fhwa_default"),
            "x_ref": 1000.0,
            "span": (0.0, 2000.0),
            "x_offset_m": 0.0,
        }
        analysis = analyse_replicate(run, _artifact(), scored_end_s=STUDY_END_S, **kwargs)
        again = load_replicate_analysis(run, STUDY_END_S)
        assert again.metrics == analysis.metrics and again.waiting == analysis.waiting
        with pytest.raises(ValueError, match="re-score"):
            load_replicate_analysis(run)
        analyse_replicate(run, _artifact(), **kwargs)  # the default writes no key
        assert "scored_end_s" not in json.loads((run / METRICS_FILE).read_text())
        load_replicate_analysis(run)
        with pytest.raises(ValueError, match="re-score"):
            load_replicate_analysis(run, STUDY_END_S)

    def test_the_wave_field_is_cut_at_the_scored_end(self, tmp_path: Path) -> None:
        run = _replicate(tmp_path / "r")
        frames: list[float] = []

        class Spy:
            dt_bin_s = STACK_DETECTOR.dt_bin_s
            dx_bin_m = STACK_DETECTOR.dx_bin_m

            def measure(self, field: Any) -> Any:
                frames.append(float(np.max(field.t_edges)))
                return STACK_DETECTOR.measure(field)

        replicate_wave_speed_kmh(run, Spy(), scored_end_s=STUDY_END_S)  # type: ignore[arg-type]
        replicate_wave_speed_kmh(run, Spy())  # type: ignore[arg-type]
        assert frames[0] <= STUDY_END_S + STACK_DETECTOR.dt_bin_s < frames[1]


class TestReport:
    def test_the_report_scores_to_the_scored_end_and_says_so(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_set = tmp_path / "runs"
        _replicate(run_set / "c001d0wn0000" / "5")
        seen: dict[str, list[Any]] = {"metrics": [], "waiting": []}
        real_metrics = report_mod.compute_metrics
        real_waiting = report_mod.compute_waiting_metrics

        def spy_metrics(*args: Any, **kwargs: Any) -> Any:
            seen["metrics"].append(kwargs.get("scored_end_s"))
            return real_metrics(*args, **kwargs)

        def spy_waiting(*args: Any, **kwargs: Any) -> Any:
            seen["waiting"].append(kwargs.get("scored_end_s"))
            return real_waiting(*args, **kwargs)

        monkeypatch.setattr(report_mod, "compute_metrics", spy_metrics)
        monkeypatch.setattr(report_mod, "compute_waiting_metrics", spy_waiting)
        out = generate_report(run_set, tmp_path / "report" / "report.md", scored_end_s=STUDY_END_S)
        text = out.read_text()
        assert seen == {"metrics": [STUDY_END_S], "waiting": [STUDY_END_S]}
        assert "Scored period: the window ends at the study period's end (6600 s)" in text

        seen = {"metrics": [], "waiting": []}
        out = generate_report(run_set, tmp_path / "default" / "report.md")
        assert seen == {"metrics": [None], "waiting": [None]}
        assert "Scored period" not in out.read_text()
