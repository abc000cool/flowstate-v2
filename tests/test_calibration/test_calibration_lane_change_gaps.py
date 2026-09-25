"""Synthetic-frame tests for accepted lane-change gaps (calibration.lane_change_gaps, WP-77).

Every expected number is hand-computed from constructed trajectories on a
shared 0.2 s grid (the I-24 MOTION processed cadence): uniform speeds, known
lane sequences, known lengths, so gaps, time gaps and closing speeds are
checked exactly. The model's acceptance is checked against hand-computed
thresholds and, case by case, against the runner's own
``microsim.runner._weave_change_ok``.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from calibration.lane_change_gaps import (
    ACCEPTANCE_TERMS,
    AcceptanceParams,
    Zone,
    classify_movement,
    lane_change_gaps,
    sample_records,
    sim_band_lanes,
    summarize_gaps,
    weave_acceptance,
)
from flowstate_core.config import WEAVE_DEFAULTS

DT = 0.2
T0 = 100.0
MAIN = (1, 2, 3, 4)
AUX = (5,)
ZONES = (
    Zone("merge_z", "merge", 0.0, 500.0),
    Zone("weave_z", "weave", 1000.0, 1500.0),
    Zone("diverge_z", "diverge", 2000.0, 2500.0),
)
PARAMS = AcceptanceParams(
    accept_gap_s=0.6,
    exit_accept_gap_s=0.6,
    s0_m=2.5,
    T_s=1.3,
    a_max=1.0,
    b=1.7,
    v0_ms=30.0,
    source="test",
)


def _track(
    veh_id: str,
    x0: float,
    v: float,
    lanes: list[int],
    *,
    length: float = 5.0,
    k0: int = 0,
) -> pd.DataFrame:
    """Uniform-speed samples on the shared grid ``T0 + DT · k``, from slot ``k0``."""
    k = k0 + np.arange(len(lanes))
    return pd.DataFrame(
        {
            "t": T0 + DT * k,
            "veh_id": veh_id,
            "x": x0 + v * DT * np.arange(len(lanes)),
            "lane": np.asarray(lanes, dtype=np.int8),
            "v": float(v),
            "length": float(length),
        }
    )


def _frame(*tracks: pd.DataFrame) -> pd.DataFrame:
    return pd.concat(tracks, ignore_index=True)


def _clean_change_frame() -> pd.DataFrame:
    """C (4.5 m) moves 2 → 1 at slot 10 (t = 102.0 s, x = 1040 m) at 20 m/s;
    in lane 1 a 15 m truck L 59 m ahead at 22 m/s and F 9.5 m behind at 18 m/s,
    farther vehicles behind them, a lane-3 vehicle beside, and Q in lane 2
    whose lane index flickers into lane 1 for 0.6 s right ahead of C."""
    return _frame(
        _track("C", 1000.0, 20.0, [2] * 10 + [1] * 20, length=4.5),
        _track("L", 1070.0, 22.0, [1] * 30, length=15.0),
        _track("L2", 1200.0, 22.0, [1] * 30),
        _track("F", 990.0, 18.0, [1] * 30),
        _track("F2", 900.0, 18.0, [1] * 30),
        _track("S", 1041.0, 20.0, [3] * 30),
        _track("Q", 1045.0, 20.0, [2] * 9 + [1] * 3 + [2] * 18),
    )


class TestDetection:
    def test_clean_change_gaps_bumper_to_bumper(self) -> None:
        out = lane_change_gaps(_clean_change_frame(), ZONES, mainline_lanes=MAIN, aux_lanes=AUX)
        rec = out.records
        assert len(rec) == 1
        r = rec.iloc[0]
        assert r["veh_id"] == "C"
        assert r["t"] == pytest.approx(102.0)
        assert r["x"] == pytest.approx(1040.0)
        assert (r["from_lane"], r["to_lane"], r["direction"]) == (2, 1, "left")
        assert (r["zone"], r["zone_kind"], r["movement"]) == ("weave_z", "weave", "through")
        # lead: 1070 + 22·2 − 15 − 1040 = 59 m; the lane-2 flicker of Q is not a lead
        assert r["lead_id"] == "L"
        assert r["lead_gap_m"] == pytest.approx(59.0)
        assert r["lead_time_gap_s"] == pytest.approx(59.0 / 20.0)
        assert r["lead_closing_ms"] == pytest.approx(-2.0)
        # lag: (1040 − 4.5) − (990 + 18·2) = 9.5 m, time gap on the lag's speed
        assert r["lag_id"] == "F"
        assert r["lag_gap_m"] == pytest.approx(9.5)
        assert r["lag_time_gap_s"] == pytest.approx(9.5 / 18.0)
        assert r["lag_closing_ms"] == pytest.approx(-2.0)
        assert r["dwell_before_s"] == pytest.approx(2.0)
        assert r["dwell_after_s"] == pytest.approx(4.0)
        assert bool(r["confirmed"]) and not bool(r["suspect"])
        assert out.dt_s == pytest.approx(DT)
        # Q's 0.6 s excursion (A-B-A) adds two raw transitions and is debounced (3 samples)
        assert out.counts["n_transitions_raw"] == 3
        assert out.counts["n_flicker_samples"] == 3
        assert out.counts["n_transitions_held"] == 1
        assert out.counts["n_changes"] == 1

    def test_noisy_flicker_is_not_a_change(self) -> None:
        # one lane-line excursion of 0.4 s, then a genuine 1.2 s stay that returns
        df = _frame(_track("N", 0.0, 15.0, [2] * 10 + [1] * 2 + [2] * 10 + [1] * 6 + [2] * 10))
        out = lane_change_gaps(df, ZONES, mainline_lanes=MAIN)
        assert out.counts["n_transitions_raw"] == 4
        assert out.counts["n_flicker_samples"] == 2
        # the 1.2 s stay is a stay: out and back are two changes, the 0.4 s one none
        rec = out.records
        assert list(zip(rec["from_lane"], rec["to_lane"], strict=True)) == [(2, 1), (1, 2)]
        assert rec["t"].to_list() == pytest.approx([T0 + DT * 22, T0 + DT * 28])

    def test_change_with_no_lag_vehicle(self) -> None:
        df = _frame(
            _track("C", 1000.0, 20.0, [2] * 10 + [1] * 20),
            _track("L", 1100.0, 20.0, [1] * 30),
        )
        out = lane_change_gaps(df, ZONES, mainline_lanes=MAIN, acceptance=PARAMS)
        r = out.records.iloc[0]
        assert r["lead_id"] == "L" and r["lead_gap_m"] == pytest.approx(1100.0 - 5.0 - 1000.0)
        assert r["lag_id"] is None
        assert math.isnan(r["lag_gap_m"]) and math.isnan(r["lag_v"])
        assert math.isnan(r["lag_time_gap_s"]) and math.isnan(r["need_lag_m"])
        # an empty follower side passes every follower term
        assert bool(r["ok_lag_time"]) and bool(r["ok_lag_absorb"]) and bool(r["ok_guard"])
        assert bool(r["model_accepts"])
        (row,) = [
            s for s in summarize_gaps(out.records, by=("movement",)) if s["speed_class"] == "all"
        ]
        assert row["share_no_lag"] == 1.0 and row["share_no_lead"] == 0.0
        assert row["lag_gap_m"]["p50"] is None

    def test_lengths_set_the_bumper_gaps(self) -> None:
        # the same positions with other lengths: only the gaps move, by the lengths
        for len_c, len_lead in ((4.0, 5.0), (5.5, 21.0)):
            df = _frame(
                _track("C", 1000.0, 10.0, [3] * 5 + [2] * 5, length=len_c),
                _track("L", 1040.0, 10.0, [2] * 10, length=len_lead),
                _track("F", 980.0, 10.0, [2] * 10, length=7.0),
            )
            r = lane_change_gaps(df, ZONES, mainline_lanes=MAIN).records.iloc[0]
            assert r["lead_gap_m"] == pytest.approx(1040.0 - len_lead - 1000.0)
            assert r["lag_gap_m"] == pytest.approx(1000.0 - len_c - 980.0)
            assert r["length"] == pytest.approx(len_c)

    def test_default_length_without_a_length_column(self) -> None:
        df = _clean_change_frame().drop(columns="length")
        with pytest.raises(ValueError, match="default_length_m"):
            lane_change_gaps(df, ZONES, mainline_lanes=MAIN)
        r = lane_change_gaps(df, ZONES, mainline_lanes=MAIN, default_length_m=5.0).records.iloc[0]
        assert r["lead_gap_m"] == pytest.approx(1114.0 - 5.0 - 1040.0)
        assert r["lag_gap_m"] == pytest.approx(1035.0 - 1026.0)

    def test_quick_double_change_is_two_confirmed_changes(self) -> None:
        df = _frame(_track("C", 0.0, 15.0, [3] * 10 + [2] * 2 + [1] * 10))
        rec = lane_change_gaps(df, ZONES, mainline_lanes=MAIN).records
        assert list(zip(rec["from_lane"], rec["to_lane"], strict=True)) == [(3, 2), (2, 1)]
        assert rec["dwell_after_s"].iloc[0] == pytest.approx(0.4)
        assert rec["confirmed"].all()

    def test_changes_at_a_track_edge_are_unconfirmed(self) -> None:
        df = _frame(
            _track("END", 0.0, 15.0, [2] * 10 + [1] * 3),  # track dies 0.6 s after the change
            _track("START", 3000.0, 15.0, [1] * 2 + [2] * 10),  # first 0.4 s in another band
        )
        rec = lane_change_gaps(df, ZONES, mainline_lanes=MAIN).records.set_index("veh_id")
        assert bool(rec.loc["END", "track_end_after"]) and not bool(rec.loc["END", "confirmed"])
        assert bool(rec.loc["START", "track_start_before"])
        assert not bool(rec.loc["START", "confirmed"])
        assert summarize_gaps(rec.reset_index()) == []
        assert len(summarize_gaps(rec.reset_index(), include_unconfirmed=True)) == 2 * 4

    def test_nonadjacent_jump_and_other_lanes_are_dropped_and_counted(self) -> None:
        df = _frame(
            _track("J", 0.0, 15.0, [2] * 10 + [4] * 10),  # two lanes in 0.2 s
            _track("M", 200.0, 15.0, [1] * 10 + [0] * 10),  # onto the median shoulder
        )
        out = lane_change_gaps(df, ZONES, mainline_lanes=MAIN, aux_lanes=AUX)
        assert out.counts["n_nonadjacent"] == 1 and out.counts["n_other_lanes"] == 1
        assert out.counts["n_changes"] == 0 and out.records.empty

    def test_overlapping_neighbour_marks_the_change_suspect(self) -> None:
        # a duplicate fragment of C, 0.2 m behind it, already in the target lane
        df = _frame(
            _track("C", 1000.0, 20.0, [2] * 10 + [1] * 20),
            _track("C_dup", 1000.0 + 40.0 - 0.2, 20.0, [1] * 20, k0=10),
        )
        r = lane_change_gaps(df, ZONES, mainline_lanes=MAIN).records.iloc[0]
        assert r["lag_id"] == "C_dup" and r["lag_gap_m"] == pytest.approx(-4.8)
        assert bool(r["suspect"])

    def test_neighbours_beyond_range_count_as_none(self) -> None:
        df = _frame(
            _track("C", 1000.0, 20.0, [2] * 10 + [1] * 20),
            _track("L", 1300.0, 20.0, [1] * 30),
        )
        r = lane_change_gaps(df, ZONES, mainline_lanes=MAIN, max_range_m=200.0).records.iloc[0]
        assert r["lead_id"] is None and math.isnan(r["lead_gap_m"])
        r = lane_change_gaps(df, ZONES, mainline_lanes=MAIN, max_range_m=300.0).records.iloc[0]
        assert r["lead_gap_m"] == pytest.approx(1300.0 - 5.0 - 1000.0)

    def test_window_and_span_select_what_is_recorded(self) -> None:
        df = _frame(
            _track("A", 100.0, 10.0, [2] * 10 + [1] * 20),  # t 102.0, x 120
            _track("B", 1100.0, 10.0, [2] * 20 + [3] * 10),  # t 104.0, x 1140
        )
        full = lane_change_gaps(df, ZONES, mainline_lanes=MAIN)
        assert full.records["veh_id"].to_list() == ["A", "B"]
        win = lane_change_gaps(df, ZONES, mainline_lanes=MAIN, window_s=(103.0, 110.0))
        assert win.records["veh_id"].to_list() == ["B"] and win.counts["n_outside_window"] == 1
        span = lane_change_gaps(df, ZONES, mainline_lanes=MAIN, x_range_m=(0.0, 1000.0))
        assert span.records["veh_id"].to_list() == ["A"]
        assert span.counts["n_outside_zones_span"] == 1

    def test_groups_are_attached(self) -> None:
        out = lane_change_gaps(_clean_change_frame(), ZONES, mainline_lanes=MAIN, groups={"C": "X"})
        assert out.records["group"].to_list() == ["X"]

    def test_bad_inputs_raise(self) -> None:
        df = _clean_change_frame()
        with pytest.raises(ValueError, match="missing"):
            lane_change_gaps(df.drop(columns="v"), ZONES, mainline_lanes=MAIN)
        with pytest.raises(ValueError, match="overlap"):
            lane_change_gaps(
                df, (Zone("a", "merge", 0, 100), Zone("b", "weave", 50, 200)), mainline_lanes=MAIN
            )
        with pytest.raises(ValueError, match="both"):
            lane_change_gaps(df, ZONES, mainline_lanes=MAIN, aux_lanes=(4,))
        with pytest.raises(ValueError, match="kind"):
            Zone("z", "ramp", 0.0, 1.0)
        with pytest.raises(ValueError, match="x_hi_m"):
            Zone("z", "merge", 1.0, 1.0)
        empty = lane_change_gaps(df.iloc[:0], ZONES, mainline_lanes=MAIN, dt_s=DT)
        assert empty.records.empty and empty.counts["n_changes"] == 0


class TestMovements:
    def test_movement_by_lanes_and_zone(self) -> None:
        cases = [  # (x at the change, from, to, expected)
            (100.0, 5, 4, "entering"),
            (100.0, 4, 5, "unknown"),  # into an acceleration lane
            (1100.0, 5, 4, "entering"),
            (1100.0, 4, 5, "exiting"),
            (2100.0, 4, 5, "exiting"),
            (2100.0, 5, 4, "unknown"),  # back out of a deceleration lane
            (1700.0, 3, 4, "through"),
            (1700.0, 4, 5, "unknown"),  # an auxiliary lane outside every ramp zone
        ]
        tracks = [
            _track(f"v{i}", x - 10 * DT * 10, 10.0, [a] * 10 + [b] * 10)
            for i, (x, a, b, _) in enumerate(cases)
        ]
        rec = lane_change_gaps(_frame(*tracks), ZONES, mainline_lanes=MAIN, aux_lanes=AUX).records
        got = dict(zip(rec["veh_id"], rec["movement"], strict=True))
        assert got == {f"v{i}": exp for i, (*_, exp) in enumerate(cases)}

    def test_classify_movement_directly(self) -> None:
        out = classify_movement(
            np.array([5, 2, 5]),
            np.array([4, 1, 6]),
            ["weave", "basic", "weave"],
            mainline_lanes=MAIN,
            aux_lanes=(5, 6),
        )
        assert out.tolist() == ["entering", "through", "unknown"]


class TestSimulatedFrame:
    def test_band_mapping_keeps_through_lanes_and_finds_the_entrant(self) -> None:
        # approach 3 lanes [0, 500), a four-lane weave [500, 800), 3 lanes after
        offsets, n_lanes = [0.0, 500.0, 800.0], [3, 4, 3]
        # T drives in the rightmost through lane: SUMO index 0, 1, 0 along the edges
        t_x = 400.0 + 25.0 * DT * np.arange(40)
        t_idx = np.where((t_x >= 500.0) & (t_x < 800.0), 1, 0)
        through = pd.DataFrame(
            {"t": T0 + DT * np.arange(40), "veh_id": "T", "x": t_x, "lane": t_idx, "v": 25.0}
        )
        # E appears in the auxiliary lane (index 0 on the weave edge) and moves to index 1
        e_x = 520.0 + 20.0 * DT * np.arange(20)
        entrant = pd.DataFrame(
            {
                "t": T0 + DT * np.arange(20),
                "veh_id": "E",
                "x": e_x,
                "lane": [0] * 8 + [1] * 12,
                "v": 20.0,
            }
        )
        sim = sim_band_lanes(_frame(through, entrant), offsets, n_lanes)
        assert set(sim.loc[sim.veh_id == "T", "lane"]) == {3}
        rec = lane_change_gaps(
            sim,
            (Zone("weave", "weave", 500.0, 800.0),),
            mainline_lanes=(1, 2, 3),
            aux_lanes=(4,),
            default_length_m=5.0,
        ).records
        assert rec["veh_id"].to_list() == ["E"]
        r = rec.iloc[0]
        assert (r["from_lane"], r["to_lane"], r["movement"]) == (4, 3, "entering")
        # T is in band 3 at slot 8: x_T = 400 + 25·1.6 = 440 → not on the weave yet,
        # but a band-3 vehicle all the same: 552 − 5 − 440 = 107 m of lag gap
        assert r["lag_id"] == "T" and r["lag_gap_m"] == pytest.approx(552.0 - 5.0 - 440.0)


class TestAcceptance:
    def test_hand_computed_thresholds(self) -> None:
        # v 20 m/s, leader 15 m ahead at 20 m/s, follower 30 m behind at 20 m/s
        acc = weave_acceptance(
            np.array([20.0]),
            np.array([15.0]),
            np.array([20.0]),
            np.array([30.0]),
            np.array([20.0]),
            np.array([False]),
            PARAMS,
        )
        # leader side: 2 s0 + 0.6 v = 5 + 12 = 17 m > 15 → refused
        assert acc["need_lead_m"][0] == pytest.approx(17.0)
        assert not acc["ok_lead_time"][0] and acc["ok_lead_brake"][0]
        # follower side: s* = 2.5 + 20·1.3 = 28.5; √(1 − (20/30)⁴ + 1.7/1.0) = √2.50247
        need_absorb = 28.5 / math.sqrt(1.0 - (20.0 / 30.0) ** 4 + 1.7)
        assert acc["need_lag_m"][0] == pytest.approx(max(17.0, need_absorb))
        assert acc["ok_lag_time"][0] and acc["ok_lag_absorb"][0] and acc["ok_guard"][0]
        assert not acc["accepts"][0]

    def test_brake_gap_binds_on_a_slow_leader(self) -> None:
        # 20 m/s onto a leader at 5 m/s: brake gap 15² / 3.4 = 66.2 m; 40 m offered
        acc = weave_acceptance(
            np.array([20.0]),
            np.array([40.0]),
            np.array([5.0]),
            np.array([np.nan]),
            np.array([np.nan]),
            np.array([True]),
            PARAMS,
        )
        assert acc["need_lead_m"][0] == pytest.approx(5.0 + 15.0**2 / 3.4)
        assert acc["ok_lead_time"][0] and not acc["ok_lead_brake"][0]
        assert not acc["ok_guard"][0] and not acc["accepts"][0]

    def test_from_population_uses_weave_defaults_and_the_cap(self) -> None:
        p = AcceptanceParams.from_population(
            {"v0": 32.4, "T": 1.32, "a_max": 1.05, "b": 1.70, "s0": 2.53},
            v0_cap_ms=24.59,
            source="x",
        )
        assert p.accept_gap_s == WEAVE_DEFAULTS["accept_gap_s"]
        assert p.exit_accept_gap_s == WEAVE_DEFAULTS["exit_accept_gap_s"]
        assert p.v0_ms == pytest.approx(24.59) and p.s0_m == pytest.approx(2.53)
        assert p.to_dict()["source"] == "x"
        q = AcceptanceParams.from_population(
            {"v0": 32.4, "T": 1.32, "a_max": 1.05, "b": 1.70, "s0": 2.53},
            weave_params={"exit_accept_gap_s": 0.9},
        )
        assert q.v0_ms == pytest.approx(32.4) and q.exit_accept_gap_s == 0.9

    def test_matches_the_runner_case_by_case(self) -> None:
        """The restatement agrees with ``microsim.runner._weave_change_ok`` on
        2,000 seeded random cases (SUMO's reported gaps = bumper − minGap)."""
        from microsim.runner import _weave_change_ok

        p = AcceptanceParams(
            accept_gap_s=0.6,
            exit_accept_gap_s=0.9,
            s0_m=2.53,
            T_s=1.32,
            a_max=1.05,
            b=1.70,
            v0_ms=24.59,
        )
        rng = np.random.default_rng(20260925)
        n = 2000
        v = rng.uniform(0.0, 30.0, n)
        lead_gap = np.where(rng.random(n) < 0.2, np.nan, rng.uniform(-2.0, 90.0, n))
        lead_v = np.where(np.isnan(lead_gap), np.nan, rng.uniform(0.0, 30.0, n))
        lag_gap = np.where(rng.random(n) < 0.2, np.nan, rng.uniform(-2.0, 90.0, n))
        lag_v = np.where(np.isnan(lag_gap), np.nan, rng.uniform(0.0, 30.0, n))
        right = rng.random(n) < 0.5
        mine = weave_acceptance(v, lead_gap, lead_v, lag_gap, lag_v, right, p)["accepts"]
        p_f = {"b": p.b, "s0": p.s0_m, "T": p.T_s, "a": p.a_max}
        theirs = [
            _weave_change_ok(
                p.s0_m,
                p.exit_accept_gap_s if right[i] else p.accept_gap_s,
                float(v[i]),
                p.b,
                math.inf if np.isnan(lead_gap[i]) else float(lead_gap[i]) - p.s0_m,
                float(lead_v[i]),
                math.inf if np.isnan(lag_gap[i]) else float(lag_gap[i]) - p.s0_m,
                float(lag_v[i]),
                None if np.isnan(lag_gap[i]) else p_f,
                p.v0_ms,
            )
            for i in range(n)
        ]
        assert mine.tolist() == theirs
        # both outcomes occur, so the comparison is not vacuous
        assert 0.1 < float(np.mean(theirs)) < 0.9


class TestSummaries:
    def test_summary_rows_and_refusals(self) -> None:
        out = lane_change_gaps(
            _clean_change_frame(), ZONES, mainline_lanes=MAIN, aux_lanes=AUX, acceptance=PARAMS
        )
        rows = summarize_gaps(out.records)
        assert [(r["zone_kind"], r["movement"], r["speed_class"]) for r in rows] == [
            ("weave", "through", "all"),
            ("weave", "through", "v<10"),
            ("weave", "through", "10<=v<20"),
            ("weave", "through", "v>=20"),
        ]
        allr, slow, mid, fast = rows
        assert allr["n"] == 1 and fast["n"] == 1 and slow["n"] == 0 and mid["n"] == 0
        assert allr["lead_gap_m"]["p50"] == pytest.approx(59.0)
        assert allr["lag_gap_m"]["p05"] == pytest.approx(9.5)
        # the lag at 9.5 m is under the model's 2 s0 + 0.6 · 18 = 15.8 m
        assert allr["model"]["refused_share"] == 1.0
        assert allr["model"]["refused_by"]["lag_time"] == 1.0
        assert allr["model"]["refused_by"]["lead_time"] == 0.0
        assert set(allr["model"]["refused_by"]) == {t.removeprefix("ok_") for t in ACCEPTANCE_TERMS}
        need = float(out.records["need_lag_m"].iloc[0])
        assert allr["model"]["lag_gap_over_need"]["p50"] == pytest.approx(9.5 / need, abs=1e-3)

    def test_suspect_changes_are_left_out_unless_asked(self) -> None:
        df = _frame(
            _track("C", 1000.0, 20.0, [2] * 10 + [1] * 20),
            _track("C_dup", 1039.8, 20.0, [1] * 20, k0=10),
        )
        rec = lane_change_gaps(df, ZONES, mainline_lanes=MAIN).records
        assert summarize_gaps(rec) == []
        assert summarize_gaps(rec, include_suspect=True)[0]["n"] == 1

    def test_sample_is_seeded_and_json_ready(self) -> None:
        tracks = [
            _track(f"v{i}", 100.0 * i, 10.0 + i, [2] * 10 + [1] * 10, k0=i) for i in range(12)
        ]
        rec = lane_change_gaps(_frame(*tracks), ZONES, mainline_lanes=MAIN, acceptance=PARAMS)
        a = sample_records(rec.records, 5, seed=7)
        b = sample_records(rec.records, 5, seed=7)
        assert a == b and len(a) == 5
        assert all(isinstance(r["confirmed"], bool) for r in a)
        assert all(r["lag_gap_m"] is None or isinstance(r["lag_gap_m"], float) for r in a)
        assert len(sample_records(rec.records, 50, seed=7)) == 12
        assert sample_records(rec.records.iloc[:0], 5, seed=7) == []
