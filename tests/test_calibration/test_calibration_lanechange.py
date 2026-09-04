"""Synthetic-trajectory tests for the lane-change observables and objective
(calibration.lanechange) and the LaneChangeCalibration artifact round trip.

Every expected number is hand-computed from constructed trajectories with a
known sampling interval, known lane sequences and known travel, so shares and
rates are checked exactly, not statistically.
"""

from __future__ import annotations

from math import sqrt

import numpy as np
import pandas as pd
import pytest

from calibration.lanechange import (
    LaneObservables,
    band_lane_from_sim,
    held_lanes,
    infer_dt,
    lane_change_objective,
    lane_observables,
)
from flowstate_core.artifacts import (
    LANE_CHANGE_PARAMS,
    LaneChangeCalibration,
    LaneChangeGridPoint,
    LaneObservablesRecord,
)

DT = 0.5
CREATED = "2026-09-03T00:00:00Z"
EDGES = (0.0, 1000.0, 2000.0)


def _vehicle(
    veh_id: str,
    t0: float,
    x0: float,
    v: float,
    lanes: list[int],
    dt: float = DT,
) -> pd.DataFrame:
    """Uniform-speed samples: one row per entry of ``lanes``."""
    n = len(lanes)
    t = t0 + dt * np.arange(n)
    return pd.DataFrame(
        {
            "t": t,
            "veh_id": veh_id,
            "x": x0 + v * dt * np.arange(n),
            "lane": np.asarray(lanes, dtype=np.int8),
            "v": v,
        }
    )


class TestObservables:
    def test_shares_and_rate_recovered_exactly(self) -> None:
        # Vehicle A: 40 samples in lane 1 at 20 m/s from x = 100 -> travels 390 m,
        # all inside section 0 (x < 1000). Vehicle B: 20 samples lane 3 then 20
        # samples lane 4 at 10 m/s from x = 1200 -> one change in section 1.
        a = _vehicle("A", 0.0, 100.0, 20.0, [1] * 40)
        b = _vehicle("B", 5.0, 1200.0, 10.0, [3] * 20 + [4] * 20)
        obs = lane_observables(pd.concat([a, b]), EDGES, dt_s=DT)
        assert obs.dt_s == DT
        assert obs.n_samples == 80
        # vehicle-time: A 40 x 0.5 = 20 s in (sec 0, lane 1); B 10 s each in
        # (sec 1, lane 3) and (sec 1, lane 4)
        expected_time = np.array([[20.0, 0.0, 0.0, 0.0], [0.0, 0.0, 10.0, 10.0]])
        assert np.array_equal(obs.veh_time_s, expected_time)
        assert np.allclose(obs.lane_share[0], [1.0, 0.0, 0.0, 0.0])
        assert np.allclose(obs.lane_share[1], [0.0, 0.0, 0.5, 0.5])
        # travel: A 39 pairs x 10 m = 390 m; B 39 pairs x 5 m = 195 m
        assert obs.veh_km == pytest.approx([0.390, 0.195], abs=1e-12)
        assert obs.n_changes.tolist() == [0, 1]
        assert obs.n_changes_right.tolist() == [0, 1]
        assert obs.n_changes_left.tolist() == [0, 0]
        assert not np.isnan(obs.changes_per_veh_km[0])
        assert obs.changes_per_veh_km[0] == 0.0
        assert obs.changes_per_veh_km[1] == pytest.approx(1.0 / 0.195)
        # the change sits at the midpoint of B's samples 19 and 20:
        # x = 1200 + 5 * 19.5 = 1297.5 -> histogram bin [1200, 1300)
        assert obs.change_hist_edges_m.size == 21
        assert obs.change_hist[12] == 1 and obs.change_hist.sum() == 1
        assert np.allclose(obs.change_location_share, [0.0, 1.0])

    def test_left_change_and_direction(self) -> None:
        b = _vehicle("B", 0.0, 100.0, 10.0, [4] * 10 + [3] * 10)
        obs = lane_observables(b, EDGES, dt_s=DT)
        assert obs.n_changes.tolist() == [1, 0]
        assert obs.n_changes_left.tolist() == [1, 0]
        assert obs.n_changes_right.tolist() == [0, 0]

    def test_flicker_is_not_a_change(self) -> None:
        # 1 s dwell guard: a single-sample (0.5 s) excursion 2 -> 3 -> 2 is a
        # lane-line flicker; its vehicle-time goes to lane 2.
        c = _vehicle("C", 0.0, 100.0, 10.0, [2] * 10 + [3] + [2] * 10)
        obs = lane_observables(c, EDGES, dt_s=DT, min_dwell_s=1.0)
        assert obs.n_changes.sum() == 0
        assert obs.veh_time_s[0].tolist() == [0.0, 21 * DT, 0.0, 0.0]
        # Without the guard the same data shows two changes.
        raw = lane_observables(c, EDGES, dt_s=DT, min_dwell_s=0.0)
        assert raw.n_changes.sum() == 2

    def test_double_change_is_two_changes(self) -> None:
        # A short stay that does not return (2 -> 3 -> 4) is a real double change.
        d = _vehicle("D", 0.0, 100.0, 10.0, [2] * 10 + [3] + [4] * 10)
        obs = lane_observables(d, EDGES, dt_s=DT, min_dwell_s=1.0)
        assert obs.n_changes.sum() == 2
        assert obs.n_changes_right.sum() == 2

    def test_non_mainline_rows_are_dropped_and_gaps_break_pairs(self) -> None:
        # Lane 5 (auxiliary) rows vanish; a 3-sample excursion leaves a 2 s
        # gap > max_gap (1.25 s), so no pair and no change spans it.
        e = _vehicle("E", 0.0, 100.0, 10.0, [4] * 10 + [5] * 3 + [4] * 10)
        obs = lane_observables(e, EDGES, dt_s=DT)
        assert obs.n_samples == 20
        assert obs.n_changes.sum() == 0
        # 9 + 9 pairs of 5 m
        assert obs.veh_km[0] == pytest.approx(0.090)
        # a change to lane 5 is never counted, even when it is contiguous
        f = _vehicle("F", 0.0, 100.0, 10.0, [4] * 10 + [5] * 10)
        assert lane_observables(f, EDGES, dt_s=DT).n_changes.sum() == 0

    def test_window_selection_is_additive_with_padding(self) -> None:
        rng = np.random.default_rng(7)
        frames = []
        for k in range(30):
            lanes = list(rng.integers(1, 5, size=1))
            seq: list[int] = []
            for _ in range(60):
                if rng.random() < 0.05:
                    lanes = [int(rng.integers(1, 5))]
                seq.append(lanes[0])
            frames.append(_vehicle(f"V{k}", float(rng.uniform(0, 100)), 50.0 + 60 * k, 8.0, seq))
        df = pd.concat(frames)
        full = lane_observables(df, EDGES, dt_s=DT)
        pad = 5.0
        parts = []
        for lo, hi in ((0.0, 40.0), (40.0, 80.0), (80.0, 200.0)):
            chunk = df[(df["t"] >= lo - pad) & (df["t"] < hi + pad)]
            parts.append(lane_observables(chunk, EDGES, dt_s=DT, window_s=(lo, hi)))
        total = parts[0] + parts[1] + parts[2]
        assert np.array_equal(total.veh_time_s, full.veh_time_s)
        assert np.allclose(total.veh_km, full.veh_km)
        assert np.array_equal(total.n_changes, full.n_changes)
        assert np.array_equal(total.n_changes_left, full.n_changes_left)
        assert np.array_equal(total.change_hist, full.change_hist)
        assert total.n_samples == full.n_samples
        assert total.window_s == (0.0, 200.0)

    def test_dt_inferred_and_empty_frame(self) -> None:
        a = _vehicle("A", 0.0, 100.0, 20.0, [1] * 5, dt=0.2)
        assert infer_dt(a["t"].to_numpy(), np.zeros(5, dtype=np.int64)) == pytest.approx(0.2)
        obs = lane_observables(a, EDGES)
        assert obs.dt_s == pytest.approx(0.2)
        empty = lane_observables(a.iloc[:0], EDGES, dt_s=0.2)
        assert empty.n_samples == 0 and np.isnan(empty.lane_share).all()
        with pytest.raises(ValueError, match="dt_s"):
            lane_observables(a.iloc[:0], EDGES)

    def test_bad_inputs(self) -> None:
        a = _vehicle("A", 0.0, 100.0, 20.0, [1] * 5)
        with pytest.raises(ValueError, match="increasing"):
            lane_observables(a, (0.0, 0.0), dt_s=DT)
        with pytest.raises(ValueError, match="distinct"):
            lane_observables(a, EDGES, lanes=(1, 1), dt_s=DT)
        other = lane_observables(a, (0.0, 500.0, 2000.0), dt_s=DT)
        with pytest.raises(ValueError, match="different sections"):
            _ = lane_observables(a, EDGES, dt_s=DT) + other

    def test_held_lanes_marks_contiguity(self) -> None:
        t = np.array([0.0, 0.5, 1.0, 5.0, 5.5])
        veh = np.array([0, 0, 0, 0, 0], dtype=np.int64)
        lane = np.array([1, 1, 2, 2, 2], dtype=np.int64)
        held, contig = held_lanes(t, veh, lane, dt_s=0.5, max_gap_s=1.25, min_dwell_s=0.0)
        assert held.tolist() == [1, 1, 2, 2, 2]
        assert contig.tolist() == [True, True, False, True]


class TestSimLaneMapping:
    def test_band_from_sumo_index(self) -> None:
        offsets = [0.0, 100.0, 300.0]
        n_lanes = [4, 5, 4]
        idx = np.array([3, 0, 4, 0, 3, 0])
        x = np.array([10.0, 10.0, 150.0, 150.0, 350.0, 350.0])
        band = band_lane_from_sim(idx, x, offsets, n_lanes)
        # leftmost lane is 1 on every edge; the extra right lane is 5
        assert band.tolist() == [1, 4, 1, 5, 1, 4]
        assert band_lane_from_sim([0], [-5.0], offsets, n_lanes).tolist() == [4]

    def test_bad_edge_tables(self) -> None:
        with pytest.raises(ValueError):
            band_lane_from_sim([0], [1.0], [0.0, 10.0], [4])
        with pytest.raises(ValueError):
            band_lane_from_sim([0], [1.0], [0.0, 0.0], [4, 4])


def _two_section_obs(shares: list[list[float]], rates: list[float]) -> LaneObservables:
    veh_time = np.asarray(shares, dtype=float) * 100.0
    veh_km = np.array([10.0, 10.0])
    n_changes = np.rint(np.asarray(rates) * veh_km).astype(np.int64)
    return LaneObservables(
        x_edges_m=np.asarray(EDGES),
        lanes=(1, 2, 3, 4),
        dt_s=DT,
        veh_time_s=veh_time,
        veh_km=veh_km,
        n_changes=n_changes,
        n_changes_left=n_changes,
        n_changes_right=np.zeros(2, dtype=np.int64),
        change_hist_edges_m=np.linspace(0.0, 2000.0, 21),
        change_hist=np.zeros(20, dtype=np.int64),
        n_samples=int(veh_time.sum() / DT),
    )


class TestObjective:
    def test_zero_on_identical_positive_otherwise(self) -> None:
        obs = _two_section_obs([[0.3, 0.24, 0.2, 0.26], [0.25, 0.25, 0.25, 0.25]], [0.5, 1.0])
        same = lane_change_objective(obs, obs)
        assert same.value == 0.0 and same.share_rms == 0.0 and same.rate_rmspe == 0.0
        assert same.n_share_terms == 8 and same.n_rate_terms == 2
        sim = _two_section_obs([[0.3, 0.24, 0.2, 0.26], [0.25, 0.25, 0.25, 0.25]], [0.5, 2.0])
        rate_only = lane_change_objective(sim, obs, rate_weight=0.1)
        assert rate_only.share_rms == 0.0
        # section 1: (2 - 1)/1 = 1 -> rmspe = sqrt(mean([0, 1])) = sqrt(0.5)
        assert rate_only.rate_rmspe == pytest.approx(sqrt(0.5))
        assert rate_only.value == pytest.approx(0.1 * sqrt(0.5))
        sim2 = _two_section_obs([[0.4, 0.2, 0.2, 0.2], [0.25, 0.25, 0.25, 0.25]], [0.5, 1.0])
        share_only = lane_change_objective(sim2, obs)
        # diffs 0.1, -0.04, 0, -0.06 over 8 cells
        assert share_only.share_rms == pytest.approx(sqrt((0.01 + 0.0016 + 0.0036) / 8.0), rel=1e-9)
        assert share_only.rate_rmspe == 0.0
        assert share_only.value == pytest.approx(share_only.share_rms)
        assert share_only.to_dict()["n_rate_terms"] == 2

    def test_undefined_cells_are_skipped(self) -> None:
        obs = _two_section_obs([[0.3, 0.24, 0.2, 0.26], [0.0, 0.0, 0.0, 0.0]], [0.5, 0.0])
        sim = _two_section_obs([[0.3, 0.24, 0.2, 0.26], [0.25, 0.25, 0.25, 0.25]], [0.5, 1.0])
        j = lane_change_objective(sim, obs)
        assert j.n_share_terms == 4 and j.n_rate_terms == 1 and j.value == 0.0

    def test_mismatch_and_bad_weight(self) -> None:
        obs = _two_section_obs([[0.3, 0.24, 0.2, 0.26], [0.25, 0.25, 0.25, 0.25]], [0.5, 1.0])
        other = lane_observables(_vehicle("A", 0.0, 10.0, 10.0, [1] * 4), (0.0, 2000.0), dt_s=DT)
        with pytest.raises(ValueError, match="identical sections"):
            lane_change_objective(other, obs)
        with pytest.raises(ValueError, match="rate_weight"):
            lane_change_objective(obs, obs, rate_weight=-1.0)


class TestArtifact:
    def test_record_round_trip_and_consistency_check(self) -> None:
        a = _vehicle("A", 0.0, 100.0, 20.0, [1] * 40)
        b = _vehicle("B", 5.0, 1200.0, 10.0, [3] * 20 + [4] * 20)
        obs = lane_observables(pd.concat([a, b]), EDGES, dt_s=DT, window_s=(0.0, 100.0))
        rec = obs.to_record()
        back = LaneObservables.from_record(LaneObservablesRecord.model_validate(rec.model_dump()))
        assert np.array_equal(back.veh_time_s, obs.veh_time_s)
        assert np.allclose(back.veh_km, obs.veh_km)
        assert np.array_equal(back.n_changes, obs.n_changes)
        assert np.array_equal(back.change_hist, obs.change_hist)
        assert back.window_s == (0.0, 100.0)
        assert rec.lane_share[1] == pytest.approx([0.0, 0.0, 0.5, 0.5])
        broken = rec.model_dump()
        broken["lane_share"][0][0] = 0.5
        with pytest.raises(ValueError, match="lane_share"):
            LaneObservablesRecord.model_validate(broken)
        broken = rec.model_dump()
        broken["n_changes_left"][1] = 5
        with pytest.raises(ValueError, match="left \\+ right"):
            LaneObservablesRecord.model_validate(broken)

    def test_calibration_artifact_round_trip(self, tmp_path) -> None:
        obs = _two_section_obs([[0.3, 0.24, 0.2, 0.26], [0.25, 0.25, 0.25, 0.25]], [0.5, 1.0])
        sim = _two_section_obs([[0.3, 0.24, 0.2, 0.26], [0.25, 0.25, 0.25, 0.25]], [0.5, 2.0])
        j = lane_change_objective(sim, obs)
        params = {
            "lc_cooperative": 0.5,
            "lc_assertive": 2.0,
            "lc_speed_gain": 1.0,
            "lc_keep_right": 0.0,
        }
        art = LaneChangeCalibration(
            created_at=CREATED,
            source="synthetic",
            data_hash="abc",
            params=params,
            scenario="i24_replica_speedcal",
            scenario_config_hash="b072d754492d",
            fit_config_hash="deadbeef0000",
            seed=1,
            fit_window_s=(0.0, 3600.0),
            holdout_window_s=(3600.0, 7200.0),
            objective=j.value,
            objective_holdout=None,
            objective_spec={"rate_weight": j.rate_weight},
            observed_fit=obs.to_record(),
            simulated_fit=sim.to_record(),
            grid=[
                LaneChangeGridPoint(
                    params=params,
                    config_hash="deadbeef0000",
                    seed=1,
                    objective_fit=j.value,
                    objective_holdout=None,
                )
            ],
        )
        assert art.param_names == LANE_CHANGE_PARAMS
        p = tmp_path / "lc.json"
        art.save(p)
        loaded = LaneChangeCalibration.load(p)
        assert loaded == art
        assert loaded.kind == "lanechange" and loaded.smoke is False
        assert LaneObservables.from_record(loaded.simulated_fit).n_changes.tolist() == [5, 20]

    def test_calibration_artifact_validators(self) -> None:
        obs = _two_section_obs([[0.3, 0.24, 0.2, 0.26], [0.25, 0.25, 0.25, 0.25]], [0.5, 1.0])
        base = dict(
            created_at=CREATED,
            source="synthetic",
            data_hash="abc",
            scenario="s",
            scenario_config_hash="h",
            fit_config_hash="h2",
            seed=1,
            fit_window_s=(0.0, 3600.0),
            holdout_window_s=None,
            objective=0.0,
            objective_holdout=None,
            objective_spec={"rate_weight": 0.1},
            observed_fit=obs.to_record(),
            simulated_fit=obs.to_record(),
            grid=[
                LaneChangeGridPoint(
                    params={n: 1.0 for n in LANE_CHANGE_PARAMS},
                    config_hash="h2",
                    seed=1,
                    objective_fit=0.0,
                    objective_holdout=None,
                )
            ],
        )
        good = {n: 1.0 for n in LANE_CHANGE_PARAMS}
        LaneChangeCalibration(params=good, **base)
        with pytest.raises(ValueError, match="params keys"):
            LaneChangeCalibration(params={"lc_cooperative": 1.0}, **base)
        with pytest.raises(ValueError, match="at least one"):
            LaneChangeCalibration(params=good, **{**base, "grid": []})
        with pytest.raises(ValueError, match="fit_window_s"):
            LaneChangeCalibration(params=good, **{**base, "fit_window_s": (10.0, 10.0)})
        other = lane_observables(
            _vehicle("A", 0.0, 10.0, 10.0, [1] * 4), (0.0, 2000.0), dt_s=DT
        ).to_record()
        with pytest.raises(ValueError, match="differ from observed_fit"):
            LaneChangeCalibration(params=good, **{**base, "simulated_fit": other})
