"""``validation.observed``: the observed side of a corridor comparison.

The fixtures are analytic on both sides. The "run" is a platoon of vehicles
departing on a fixed headway at one constant speed, so its crossings of every
station and its mean speed in every space-time cell are known exactly; the
observations are constants with two planted NaN holes. Every GEH and the
RMSPE are therefore hand-computable and asserted to 1e-6 (CLAUDE.md §9:
metric fixtures with hand-computed values).
"""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from validation.observed import (
    OBSERVATIONS_SCHEMA,
    DetectorWaveSpeed,
    ObservedCorridor,
    pool_scores,
    score_run_against_observed,
)

# -- the analytic fixture ----------------------------------------------------

WINDOW_S = 300.0
N_WINDOWS = 24  # two hours
DURATION_S = N_WINDOWS * WINDOW_S
WARMUP_S = 600.0  # windows 0 and 1 are warm-up
STATION_X = (1000.0, 2000.0, 3000.0)

HEADWAY_S = 12.0
SPEED_MS = 20.0
SAMPLE_DT_S = 2.0
TRIP_S = 176.0  # samples at 0, 2, ... 174 s after departure

#: Crossings of every station inside the scored hour [3600, 7200) s: one
#: vehicle every 12 s is 300 veh/h, and the constant speed makes each
#: station's count exactly that (verified per station in the test below).
SIM_VEH_H = 3600.0 / HEADWAY_S

#: Observed values, deliberately different from the simulated ones so the
#: statistics are non-degenerate.
OBS_VEH_H = 330.0
OBS_SPEED_MS = 25.0

#: The planted holes: one flow window at the middle station, one speed window
#: at the last station (both inside the scored span).
NAN_FLOW_WINDOW = 15
NAN_SPEED_WINDOW = 5


def observations_payload(
    *,
    x_m: tuple[float, ...] = STATION_X,
    with_ramp: bool = True,
) -> dict[str, Any]:
    """A ``flowstate.observations/1`` payload matching the analytic run."""
    ids = [f"S{i}" for i in range(len(x_m))]
    flows = {sid: [OBS_VEH_H] * N_WINDOWS for sid in ids}
    speeds = {sid: [OBS_SPEED_MS] * N_WINDOWS for sid in ids}
    if len(ids) > 1:
        flows[ids[1]][NAN_FLOW_WINDOW] = None  # type: ignore[call-overload]
        speeds[ids[-1]][NAN_SPEED_WINDOW] = None  # type: ignore[call-overload]
    stations: list[dict[str, Any]] = [
        {"id": sid, "label": f"station {sid}", "x_m": x, "lanes": 2, "kind": "mainline"}
        for sid, x in zip(ids, x_m, strict=True)
    ]
    if with_ramp:
        # A ramp station is demand, not a corridor cross-section: it must not
        # become a GEH cross-section or a speed segment.
        stations.append({"id": "R1", "x_m": 1500.0, "lanes": 1, "kind": "on_ramp"})
        flows["R1"] = [100.0] * N_WINDOWS
        speeds["R1"] = [15.0] * N_WINDOWS
    return {
        "schema": OBSERVATIONS_SCHEMA,
        "corridor": "test_corridor",
        "source": {
            "provider": "Test DOT archive",
            "dates": ["20260915", "20260916"],
            "url": "https://example.invalid/archive",
        },
        "window_s": WINDOW_S,
        "t0_local": "06:00",
        "duration_s": DURATION_S,
        "n_windows": N_WINDOWS,
        "aggregation": "mean over dates per window",
        "stations": stations,
        "flows_veh_h": flows,
        "speeds_ms": speeds,
        "quality": {sid: {"fraction_valid": 1.0, "n_dates": 2} for sid in ids},
    }


#: A second, smaller fixture for the "station the run never reaches" case:
#: three stations, the last of them 9 km along a corridor the run only ever
#: covers 2 km of.
SPAN_STATION_X = (200.0, 1500.0, 9000.0)
SPAN_WINDOW_S = 1800.0
SPAN_DURATION_S = 3600.0
SPAN_RUN_M = 2000.0


def span_payload() -> dict[str, Any]:
    """One observed hour at three stations, one of them past the run's end."""
    ids = ["A", "B", "C"]
    return {
        "schema": OBSERVATIONS_SCHEMA,
        "corridor": "span_corridor",
        "source": {"provider": "Test DOT archive", "dates": ["20260915"], "url": ""},
        "window_s": SPAN_WINDOW_S,
        "t0_local": "06:00",
        "duration_s": SPAN_DURATION_S,
        "n_windows": 2,
        "aggregation": "one date",
        "stations": [
            {"id": sid, "x_m": x, "lanes": 1, "kind": "mainline"}
            for sid, x in zip(ids, SPAN_STATION_X, strict=True)
        ],
        "flows_veh_h": {sid: [OBS_VEH_H] * 2 for sid in ids},
        "speeds_ms": {sid: [OBS_SPEED_MS] * 2 for sid in ids},
        "quality": {sid: {"fraction_valid": 1.0, "n_dates": 1} for sid in ids},
    }


def span_frame() -> pd.DataFrame:
    """The same platoon, on a run whose vehicles never pass ``SPAN_RUN_M``."""
    n_veh = int(SPAN_DURATION_S // HEADWAY_S)
    offsets = np.arange(0.0, SPAN_RUN_M / SPEED_MS, SAMPLE_DT_S)
    departs = HEADWAY_S * np.arange(n_veh, dtype=np.float64)
    t = (departs[:, None] + offsets[None, :]).ravel()
    x = np.tile(SPEED_MS * offsets, n_veh)
    veh = np.repeat([f"v{i}" for i in range(n_veh)], offsets.size)
    frame = pd.DataFrame({"t": t, "veh_id": veh, "x": x, "v": np.full(t.size, SPEED_MS)})
    return frame.loc[frame["t"] < SPAN_DURATION_S].reset_index(drop=True)


def trajectory_frame(x_offset_m: float = 0.0) -> pd.DataFrame:
    """Vehicles on a fixed headway at one constant speed.

    Rows are clipped to ``t < duration_s`` so the frame's last sample falls
    short of the run's nominal end — the case ``link_hour_geh``'s ``sim_span``
    exists for.
    """
    n_veh = int(DURATION_S // HEADWAY_S)
    offsets = np.arange(0.0, TRIP_S, SAMPLE_DT_S)
    departs = HEADWAY_S * np.arange(n_veh, dtype=np.float64)
    t = (departs[:, None] + offsets[None, :]).ravel()
    x = np.tile(SPEED_MS * offsets, n_veh) + x_offset_m
    veh = np.repeat([f"v{i}" for i in range(n_veh)], offsets.size)
    frame = pd.DataFrame({"t": t, "veh_id": veh, "x": x, "v": np.full(t.size, SPEED_MS)})
    return frame.loc[frame["t"] < DURATION_S].reset_index(drop=True)


@pytest.fixture(scope="module")
def observed() -> ObservedCorridor:
    return ObservedCorridor.from_dict(observations_payload())


@pytest.fixture(scope="module")
def trajectories() -> pd.DataFrame:
    return trajectory_frame()


# -- parsing -----------------------------------------------------------------


class TestObservedCorridor:
    def test_parses_the_artifact(self, observed: ObservedCorridor) -> None:
        assert observed.corridor == "test_corridor"
        assert observed.window_s == WINDOW_S
        assert observed.n_windows == N_WINDOWS
        assert observed.t0_local == "06:00"
        assert len(observed.stations) == len(STATION_X) + 1  # incl. the ramp

    def test_ramps_and_unpositioned_stations_are_not_cross_sections(
        self, observed: ObservedCorridor
    ) -> None:
        assert observed.mainline_x_refs() == list(STATION_X)
        assert [s.id for s in observed.mainline_stations()] == ["S0", "S1", "S2"]

    def test_from_json_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "obs.json"
        path.write_text(json.dumps(observations_payload()))
        obs = ObservedCorridor.from_json(path)
        assert obs.path == str(path)
        assert obs.mainline_x_refs() == list(STATION_X)

    def test_wrong_schema_refused(self) -> None:
        payload = observations_payload()
        payload["schema"] = "flowstate.observations/2"
        with pytest.raises(ValueError, match="expected schema"):
            ObservedCorridor.from_dict(payload)

    def test_series_length_mismatch_refused(self) -> None:
        payload = observations_payload()
        payload["flows_veh_h"]["S0"] = [1.0, 2.0]
        with pytest.raises(ValueError, match="expected 24"):
            ObservedCorridor.from_dict(payload)

    def test_duplicate_station_position_refused(self) -> None:
        payload = observations_payload(x_m=(1000.0, 1000.0, 3000.0))
        with pytest.raises(ValueError, match="share the position"):
            ObservedCorridor.from_dict(payload)

    def test_duplicate_station_id_refused(self) -> None:
        """Two stations keyed alike share one series: one would read as the other."""
        payload = observations_payload()
        payload["stations"][1]["id"] = payload["stations"][0]["id"]
        with pytest.raises(ValueError, match="appears twice"):
            ObservedCorridor.from_dict(payload)

    def test_window_count_must_match_duration(self) -> None:
        payload = observations_payload()
        payload["duration_s"] = DURATION_S + 1.0
        with pytest.raises(ValueError, match="does not match"):
            ObservedCorridor.from_dict(payload)

    def test_segment_bins_tile_the_corridor(self, observed: ObservedCorridor) -> None:
        bins = observed.segment_bins()
        assert bins == [(500.0, 1500.0), (1500.0, 2500.0), (2500.0, 3500.0)]
        for (_, hi), (lo, _) in itertools.pairwise(bins):
            assert hi == lo  # no gaps, no overlaps

    def test_segment_bins_need_two_stations(self) -> None:
        payload = observations_payload(x_m=(1000.0,), with_ramp=False)
        obs = ObservedCorridor.from_dict(payload)
        with pytest.raises(ValueError, match="at least two"):
            obs.segment_bins()

    def test_analysis_windows_exclude_warmup_and_partial_windows(
        self, observed: ObservedCorridor
    ) -> None:
        assert observed.analysis_windows(WARMUP_S, DURATION_S) == list(range(2, N_WINDOWS))
        # A span ending mid-window drops that window rather than half-scoring it.
        assert observed.analysis_windows(0.0, DURATION_S - 1.0) == list(range(N_WINDOWS - 1))
        assert observed.analysis_windows(DURATION_S, DURATION_S) == []

    def test_hourly_link_flows_only_from_fully_observed_hours(
        self, observed: ObservedCorridor
    ) -> None:
        rows = observed.hourly_link_flows()
        # 3 stations x 2 hours, less the hour holding the planted NaN flow.
        assert len(rows) == 5
        assert set(rows["window_start_s"]) == {0.0, 3600.0}
        assert rows["flow_veh_h"].to_numpy() == pytest.approx(OBS_VEH_H, abs=1e-12)
        dropped = rows.loc[(rows["station"] == "S1") & (rows["window_start_s"] == 3600.0)]
        assert dropped.empty

    def test_hourly_link_flows_refuse_a_window_that_does_not_divide_an_hour(self) -> None:
        payload = observations_payload()
        payload["window_s"] = 700.0
        payload["duration_s"] = 700.0 * N_WINDOWS
        obs = ObservedCorridor.from_dict(payload)
        with pytest.raises(ValueError, match="does not divide one hour"):
            obs.hourly_link_flows()

    def test_speed_matrix_shape_and_slice(self, observed: ObservedCorridor) -> None:
        full = observed.speed_matrix()
        assert full.shape == (N_WINDOWS, len(STATION_X))
        assert math.isnan(full[NAN_SPEED_WINDOW, -1])
        assert observed.speed_matrix(slice(2, 6)).shape == (4, len(STATION_X))

    def test_coverage_counts_the_planted_holes(self, observed: ObservedCorridor) -> None:
        coverage = observed.coverage()
        cells = len(STATION_X) * N_WINDOWS
        assert coverage.n_stations == len(STATION_X)
        assert coverage.n_windows == N_WINDOWS
        assert coverage.flow_fraction == pytest.approx((cells - 1) / cells, abs=1e-12)
        assert coverage.speed_fraction == pytest.approx((cells - 1) / cells, abs=1e-12)


# -- scoring -----------------------------------------------------------------


class TestScoreRunAgainstObserved:
    def test_crossings_are_the_analytic_count(self, trajectories: pd.DataFrame) -> None:
        """The fixture really does put 300 veh through each station in the hour."""
        from validation.metrics import count_crossings

        for x_ref in STATION_X:
            n = count_crossings(trajectories, x_ref, t_lo=3600.0, t_hi=7200.0)
            assert n == int(SIM_VEH_H)

    def test_geh_and_rmspe_are_hand_computable(
        self, observed: ObservedCorridor, trajectories: pd.DataFrame
    ) -> None:
        scores = score_run_against_observed(
            trajectories, observed, warmup_s=WARMUP_S, duration_s=DURATION_S
        )
        # Only the second hour lies inside [warmup, duration); the middle
        # station's NaN flow removes it, leaving two station-hours.
        assert scores.n_link_hours == 2
        expected = math.sqrt(2.0 * (SIM_VEH_H - OBS_VEH_H) ** 2 / (SIM_VEH_H + OBS_VEH_H))
        assert scores.geh_values == pytest.approx((expected, expected), abs=1e-6)

        # 22 scored windows x 3 segments, less the planted NaN speed cell.
        assert scores.n_speed_cells == 22 * len(STATION_X) - 1
        assert scores.rmspe == pytest.approx(abs(SPEED_MS - OBS_SPEED_MS) / OBS_SPEED_MS, abs=1e-6)
        assert scores.windows == tuple(range(2, N_WINDOWS))
        assert len(scores.segment_speeds_sim) == 22
        assert len(scores.segment_speeds_obs) == 22

    def test_x_offset_maps_observed_positions_onto_the_simulation(
        self, observed: ObservedCorridor, trajectories: pd.DataFrame
    ) -> None:
        shifted = trajectory_frame(x_offset_m=2000.0)
        base = score_run_against_observed(
            trajectories, observed, warmup_s=WARMUP_S, duration_s=DURATION_S
        )
        moved = score_run_against_observed(
            shifted, observed, warmup_s=WARMUP_S, duration_s=DURATION_S, x_offset_m=2000.0
        )
        assert moved.geh_values == pytest.approx(base.geh_values, abs=1e-12)
        assert moved.rmspe == pytest.approx(base.rmspe, abs=1e-12)
        assert moved.n_speed_cells == base.n_speed_cells

    def test_a_warmup_covering_everything_scores_nothing(
        self, observed: ObservedCorridor, trajectories: pd.DataFrame
    ) -> None:
        scores = score_run_against_observed(
            trajectories, observed, warmup_s=DURATION_S, duration_s=DURATION_S
        )
        assert scores.n_link_hours == 0
        assert scores.n_speed_cells == 0
        assert math.isnan(scores.rmspe)

    def test_a_station_the_run_never_reaches_is_excluded_not_failed(self) -> None:
        """A cross-section past the run's end is dropped from both comparisons.

        Scored, it would contribute a simulated flow of zero and a GEH of
        sqrt(2 * q_obs) as an ordinary failing link-hour — a statistic about
        the corridor's extent, not about the model.
        """
        observed = ObservedCorridor.from_dict(span_payload())
        scores = score_run_against_observed(
            span_frame(), observed, warmup_s=0.0, duration_s=SPAN_DURATION_S
        )
        assert scores.stations_outside_span == ("C",)
        assert scores.n_stations_outside_span == 1
        # one observed hour at each of the two reachable stations, and none
        # of the arbitrarily failing kind from the third
        assert scores.n_link_hours == 2
        assert max(scores.geh_values) < math.sqrt(2.0 * OBS_VEH_H)
        # the third station's column is gone from the speed matrices too:
        # 2 windows x 2 segments, all four compared
        assert [len(row) for row in scores.segment_speeds_sim] == [2, 2]
        assert [len(row) for row in scores.segment_speeds_obs] == [2, 2]
        assert scores.n_speed_cells == 4
        assert scores.rmspe == pytest.approx(abs(SPEED_MS - OBS_SPEED_MS) / OBS_SPEED_MS, abs=1e-6)

        # and the provenance the report renders names what was left out
        *_, provenance = pool_scores(observed, [scores])
        assert provenance.n_stations_outside_span == 1
        assert provenance.stations_outside_span == "C"
        assert provenance.n_stations == len(SPAN_STATION_X)  # the artifact still holds three

    def test_missing_columns_refused(self, observed: ObservedCorridor) -> None:
        frame = pd.DataFrame({"t": [0.0], "veh_id": ["a"], "x": [0.0]})
        with pytest.raises(ValueError, match="missing column 'v'"):
            score_run_against_observed(frame, observed, warmup_s=0.0, duration_s=DURATION_S)

    def test_scores_round_trip_through_json(
        self, observed: ObservedCorridor, trajectories: pd.DataFrame
    ) -> None:
        from validation.observed import ObservedScores

        scores = score_run_against_observed(
            trajectories, observed, warmup_s=WARMUP_S, duration_s=DURATION_S
        )
        again = ObservedScores.from_dict(json.loads(json.dumps(scores.to_dict())))
        assert again.n_link_hours == scores.n_link_hours
        assert again.n_speed_cells == scores.n_speed_cells
        assert again.rmspe == pytest.approx(scores.rmspe, abs=1e-6)
        assert again.windows == scores.windows


#: A context block as ``calibration.waves_observed`` writes it.
WAVE_CONTEXT: dict[str, Any] = {
    "detector_wave_speed": {
        "median_kmh": 21.206,
        "iqr_kmh": [18.457, 24.102],
        "n_pairs": 13,
        "n_used": 6,
        "rejected": {"peak correlation below the acceptance floor": 7},
        "method": "normalised cross-correlation",
        "loo_median_min_kmh": 18.47,
        "loo_median_max_kmh": 21.55,
        "loo_pairs_min": 5,
        "leave_one_date_out": {"n_dates": 9},
    }
}


class TestDetectorWaveSpeedContext:
    """The corridor's own wave speed travels in the artifact as context."""

    def test_artifact_without_context_carries_none(self, observed: ObservedCorridor) -> None:
        assert observed.context == {}
        assert DetectorWaveSpeed.from_context(observed.context) is None

    def test_context_is_read_and_summarised(self) -> None:
        payload = observations_payload() | {"context": WAVE_CONTEXT}
        corridor = ObservedCorridor.from_dict(payload)
        assert corridor.context == WAVE_CONTEXT
        wave = DetectorWaveSpeed.from_context(corridor.context)
        assert wave is not None
        assert wave.median_kmh == pytest.approx(21.206)
        assert wave.iqr_kmh == pytest.approx((18.457, 24.102))
        assert wave.n_pairs == 13 and wave.n_used == 6
        assert wave.rejections == "7 peak correlation below the acceptance floor"
        assert wave.to_dict()["median_kmh"] == pytest.approx(21.206)

    def test_the_leave_one_date_out_range_comes_with_it(self) -> None:
        """A median over six pairs is quoted with the range it moves in.

        The artifact carries the same estimate re-run without each date; the
        report prints that range beside the headline, so a number that is 3
        km/h wide is not read as a 0.1 km/h one.
        """
        wave = DetectorWaveSpeed.from_context(WAVE_CONTEXT)
        assert wave is not None and wave.has_loo
        assert wave.loo_n_dates == 9
        assert wave.loo_median_min_kmh == pytest.approx(18.47)
        assert wave.loo_median_max_kmh == pytest.approx(21.55)
        assert wave.loo_pairs_min == 5
        assert wave.to_dict()["loo_pairs_min"] == 5

    def test_an_artifact_without_a_range_reports_none(self) -> None:
        """An older artifact carries no sensitivity; none is invented."""
        older = {"detector_wave_speed": {"median_kmh": 18.0, "n_pairs": 4, "n_used": 3}}
        wave = DetectorWaveSpeed.from_context(older)
        assert wave is not None and not wave.has_loo
        assert wave.loo_n_dates == 0 and math.isnan(wave.loo_median_min_kmh)
        assert wave.to_dict()["loo_median_min_kmh"] is None

    def test_an_estimate_with_no_usable_pair_is_still_read(self) -> None:
        empty = {"detector_wave_speed": {"median_kmh": None, "n_pairs": 4, "n_used": 0}}
        wave = DetectorWaveSpeed.from_context(empty)
        assert wave is not None
        assert wave.n_used == 0
        assert math.isnan(wave.median_kmh)
        assert wave.to_dict()["median_kmh"] is None

    @pytest.mark.parametrize(
        "context",
        [
            {},
            {"detector_wave_speed": "not a mapping"},
            {"detector_wave_speed": {"n_used": 2}},  # no n_pairs
            {"detector_wave_speed": {"n_pairs": 2, "n_used": 1, "median_kmh": None}},
        ],
    )
    def test_an_unreadable_context_is_not_a_report_failure(self, context: Any) -> None:
        assert DetectorWaveSpeed.from_context(context) is None

    def test_the_provenance_carries_it_through_pooling(self, trajectories: pd.DataFrame) -> None:
        corridor = ObservedCorridor.from_dict(observations_payload() | {"context": WAVE_CONTEXT})
        scores = score_run_against_observed(
            trajectories, corridor, warmup_s=WARMUP_S, duration_s=DURATION_S
        )
        _, _, _, _, provenance = pool_scores(corridor, [scores])
        assert provenance.wave_speed is not None
        assert provenance.wave_speed.n_used == 6
        assert provenance.to_dict()["detector_wave_speed"]["n_pairs"] == 13


class TestPoolScores:
    def test_pools_geh_and_averages_rmspe(
        self, observed: ObservedCorridor, trajectories: pd.DataFrame
    ) -> None:
        scores = score_run_against_observed(
            trajectories, observed, warmup_s=WARMUP_S, duration_s=DURATION_S
        )
        geh, value, sim, obs, provenance = pool_scores(
            observed, [scores, scores], path="artifacts/obs.json"
        )
        assert len(geh) == 2 * scores.n_link_hours
        assert value == pytest.approx(scores.rmspe, abs=1e-12)
        assert len(sim) == len(obs) == len(scores.windows)
        assert provenance.n_replicates == 2
        assert provenance.n_link_hours == 2 * scores.n_link_hours
        assert provenance.n_speed_cells == 2 * scores.n_speed_cells
        assert provenance.provider == "Test DOT archive"
        assert provenance.dates == "20260915, 20260916"
        assert provenance.path == "artifacts/obs.json"
        assert provenance.n_windows_compared == len(scores.windows)

    def test_no_scores_refused(self, observed: ObservedCorridor) -> None:
        with pytest.raises(ValueError, match="at least one"):
            pool_scores(observed, [])

    def test_differing_matrix_shapes_get_the_documented_message(
        self, observed: ObservedCorridor, trajectories: pd.DataFrame
    ) -> None:
        """Not numpy's "inhomogeneous shape": the check comes before the array."""
        short = score_run_against_observed(
            trajectories, observed, warmup_s=WARMUP_S + WINDOW_S, duration_s=DURATION_S
        )
        long = score_run_against_observed(
            trajectories, observed, warmup_s=WARMUP_S, duration_s=DURATION_S
        )
        with pytest.raises(ValueError, match="differing speed-matrix shapes"):
            pool_scores(observed, [long, short])

    def test_replicates_scored_over_different_windows_are_refused(
        self, observed: ObservedCorridor, trajectories: pd.DataFrame
    ) -> None:
        """Same shape, shifted windows: pooling would misalign the observed side."""
        early = score_run_against_observed(
            trajectories, observed, warmup_s=WARMUP_S - WINDOW_S, duration_s=DURATION_S - WINDOW_S
        )
        late = score_run_against_observed(
            trajectories, observed, warmup_s=WARMUP_S, duration_s=DURATION_S
        )
        assert len(early.windows) == len(late.windows)
        assert early.windows != late.windows
        with pytest.raises(ValueError, match="replicate 1 was scored over observation windows"):
            pool_scores(observed, [late, early])


class TestNoComparisonProvenance:
    """An artifact too thin to compare is stated, not raised (CLAUDE.md §0.1)."""

    def test_one_station_yields_a_provenance_that_says_why(self) -> None:
        from validation.observed import no_comparison_provenance

        obs = ObservedCorridor.from_dict(observations_payload(x_m=(1000.0,), with_ramp=False))
        provenance = no_comparison_provenance(obs, path="artifacts/obs.json")
        assert provenance is not None
        assert provenance.n_link_hours == 0
        assert provenance.n_speed_cells == 0
        assert provenance.n_replicates == 0
        assert provenance.n_stations == 1
        assert "at least two" in provenance.note
        assert provenance.to_dict()["note"] == provenance.note

    def test_a_comparable_artifact_is_not_blocked(self, observed: ObservedCorridor) -> None:
        from validation.observed import no_comparison_provenance

        assert no_comparison_provenance(observed) is None


class TestSimulatedSpeedMatrix:
    """The per-window position shift equals shifting the whole column, on
    both the time-ordered (slice) and the unordered (mask) path, with an
    origin offset and a station past the run's extent."""

    def test_slice_and_mask_paths_agree_with_the_shifted_column(self) -> None:
        from validation.observed import _simulated_speed_matrix

        frame = trajectory_frame(x_offset_m=250.0)
        shuffled = frame.iloc[np.random.default_rng(0).permutation(len(frame))]
        bins = [(0.0, 500.0), (500.0, 1000.0), (9000.0, 9500.0)]
        windows = [12, 13, 14]
        kwargs = dict(bins=bins, windows=windows, window_s=WINDOW_S, x_offset_m=250.0)
        ordered = _simulated_speed_matrix(frame, **kwargs)
        unordered = _simulated_speed_matrix(shuffled, **kwargs)
        t = frame["t"].to_numpy()
        x = frame["x"].to_numpy() - 250.0
        v = frame["v"].to_numpy()
        expected = np.full((3, 3), np.nan)
        for i, k in enumerate(windows):
            for j, (lo, hi) in enumerate(bins):
                cell = (t >= k * WINDOW_S) & (t < (k + 1) * WINDOW_S) & (x >= lo) & (x < hi)
                if cell.any():
                    expected[i, j] = v[cell].mean()
        assert np.array_equal(ordered, expected, equal_nan=True)
        assert np.array_equal(unordered, expected, equal_nan=True)
        assert np.isnan(expected[:, 2]).all()
        assert np.isfinite(expected[:, :2]).all()


# -- the labelled link-hour table (WP-63) --------------------------------------
#
# A second analytic fixture whose station-hours all carry DIFFERENT counts, so
# a row that is mislabelled, misordered or read from the wrong hour cannot pass.
# Every vehicle drives 0 -> 700 m at 10 m/s sampled every 10 s (x = 0, 100, ...,
# 700 at t = d, d + 10, ..., d + 70). A crossing is stamped at the later sample
# of the pair that brackets the cross-section, so with the 50 m origin offset:
#   S1 (x 100, sim 150 m): stamped d + 20;  S2 (400, sim 450 m): d + 50;
#   S3 (600, sim 650 m): d + 70.
# Departures 100, 200, 3540, 3570, 4000, 5000, 7125 s give
#   S1: 120 220 3560 3590 | 4020 5020 7145  -> 4 in hour 0, 3 in hour 1
#   S2: 150 250 3590 | 3620 4050 5050 7175  -> 3, 4 (hour 0 unobserved)
#   S3: 170 270 | 3610 3640 4070 5070 7195  -> 2, 5

TABLE_OFFSET_M = 50.0
TABLE_WINDOW_S = 1800.0
TABLE_DURATION_S = 7200.0
TABLE_SPEED_MS = 10.0
TABLE_SAMPLE_S = 10.0
TABLE_DEPARTS_S = (100.0, 200.0, 3540.0, 3570.0, 4000.0, 5000.0, 7125.0)
TABLE_STATIONS = (("S1", 100.0), ("S2", 400.0), ("S3", 600.0))

#: Observed 30-min flows [veh/h]; S2's first window is a hole, so its first
#: hour is not fully observed and is never compared.
TABLE_FLOWS: dict[str, list[float | None]] = {
    "S1": [6.0, 8.0, 2.0, 2.0],  # hourly 7, 2
    "S2": [None, 3.0, 4.0, 6.0],  # hour 0 dropped, hourly 5
    "S3": [2.0, 2.0, 5.0, 5.0],  # hourly 2, 5
}

#: The hand counts above, in the order the table must list them (station by
#: position, then hour), with each hour's local clock (t0 05:30) and the
#: observed hourly volume.
TABLE_ROWS: tuple[tuple[str, float, float, str, float, float], ...] = (
    # station, x_ref_m, window_start_s, clock, observed, simulated (hand count)
    ("S1", 100.0, 0.0, "05:30", 7.0, 4.0),
    ("S1", 100.0, 3600.0, "06:30", 2.0, 3.0),
    ("S2", 400.0, 3600.0, "06:30", 5.0, 4.0),
    ("S3", 600.0, 0.0, "05:30", 2.0, 2.0),
    ("S3", 600.0, 3600.0, "06:30", 5.0, 5.0),
)


def table_payload() -> dict[str, Any]:
    """Three stations, two hours of 30-min windows starting at 05:30."""
    ids = [sid for sid, _ in TABLE_STATIONS]
    return {
        "schema": OBSERVATIONS_SCHEMA,
        "corridor": "table_corridor",
        "source": {"provider": "Test DOT archive", "dates": ["20260915"], "url": ""},
        "window_s": TABLE_WINDOW_S,
        "t0_local": "05:30",
        "duration_s": TABLE_DURATION_S,
        "n_windows": 4,
        "aggregation": "one date",
        "stations": [
            {"id": sid, "x_m": x, "lanes": 1, "kind": "mainline"} for sid, x in TABLE_STATIONS
        ],
        "flows_veh_h": TABLE_FLOWS,
        "speeds_ms": {sid: [TABLE_SPEED_MS] * 4 for sid in ids},
        "quality": {sid: {"fraction_valid": 1.0, "n_dates": 1} for sid in ids},
    }


def table_frame(
    departs_s: tuple[float, ...] = TABLE_DEPARTS_S, end_m: float = 700.0
) -> pd.DataFrame:
    """The vehicles of the hand count, in simulation coordinates."""
    offsets = np.arange(0.0, end_m / TABLE_SPEED_MS + TABLE_SAMPLE_S / 2, TABLE_SAMPLE_S)
    departs = np.asarray(departs_s, dtype=np.float64)
    t = (departs[:, None] + offsets[None, :]).ravel()
    x = np.tile(TABLE_SPEED_MS * offsets, departs.size)
    veh = np.repeat([f"v{i}" for i in range(departs.size)], offsets.size)
    return pd.DataFrame({"t": t, "veh_id": veh, "x": x, "v": np.full(t.size, TABLE_SPEED_MS)})


def table_scores(frame: pd.DataFrame | None = None) -> Any:
    """``score_run_against_observed`` on the table fixture (no warm-up)."""
    return score_run_against_observed(
        table_frame() if frame is None else frame,
        ObservedCorridor.from_dict(table_payload()),
        warmup_s=0.0,
        duration_s=TABLE_DURATION_S,
        x_offset_m=TABLE_OFFSET_M,
    )


#: ``observed_scores.json`` exactly as ``ObservedScores.to_dict`` wrote it before
#: the link-hour table existed (keys and order of the 2026-09-24 writer).
OLD_SCORES_FILE: dict[str, Any] = {
    "geh_values": [1.2792, 0.6325, 0.4714, 0.0, 0.0],
    "n_link_hours": 5,
    "rmspe": 0.0,
    "n_speed_cells": 5,
    "windows": [0, 1],
    "segment_speeds_sim": [[10.0, 10.0, None], [10.0, 10.0, 10.0]],
    "segment_speeds_obs": [[10.0, 10.0, 10.0], [10.0, 10.0, 10.0]],
    "n_stations_outside_span": 0,
    "stations_outside_span": [],
}


class TestClockLabel:
    @pytest.mark.parametrize(
        ("t0_local", "offset_s", "label"),
        [
            ("05:30", 0.0, "05:30"),
            ("05:30", 3600.0, "06:30"),
            ("05:30", 12600.0, "09:00"),
            ("23:30", 3600.0, "00:30"),  # wraps past midnight
            ("06:00:30", 0.0, "06:00:30"),
            ("06:00", 90.0, "06:01:30"),
            ("", 0.0, ""),  # no clock stated: no label, no error
            ("not a clock", 0.0, ""),
            ("06:00", math.nan, ""),
        ],
    )
    def test_labels(self, t0_local: str, offset_s: float, label: str) -> None:
        from validation.observed import clock_label

        assert clock_label(t0_local, offset_s) == label


class TestLinkHourTable:
    """Every GEH is stored with the station-hour and both volumes behind it."""

    def test_simulated_counts_are_the_hand_counted_crossings(self) -> None:
        from validation.metrics import count_crossings

        scores = table_scores()
        assert scores.link_hours is not None
        frame = table_frame()
        for record, (station, x_ref, start, _, _, hand) in zip(
            scores.link_hours, TABLE_ROWS, strict=True
        ):
            assert (record.station, record.x_ref_m, record.window_start_s) == (
                station,
                x_ref,
                start,
            )
            # over one hour the hourly-equivalent flow IS the count
            assert record.sim_veh_h == pytest.approx(hand, abs=1e-9)
            counted = count_crossings(
                frame, x_ref + TABLE_OFFSET_M, t_lo=start, t_hi=start + 3600.0
            )
            assert record.sim_veh_h == pytest.approx(counted, abs=1e-9)

    def test_observed_volumes_are_the_hourly_means(self) -> None:
        scores = table_scores()
        assert scores.link_hours is not None
        assert [r.obs_veh_h for r in scores.link_hours] == [row[4] for row in TABLE_ROWS]

    def test_geh_recomputed_from_the_table_is_the_stored_geh(self) -> None:
        from validation.metrics import geh

        scores = table_scores()
        assert scores.link_hours is not None
        for record in scores.link_hours:
            assert geh(record.sim_veh_h, record.obs_veh_h) == record.geh  # bit for bit
        # and as stored: the JSON table carries the GEH to 4 decimals, the
        # volumes unrounded, so the stored GEH is recomputable from the row
        for row in scores.to_dict()["link_hours"]:
            assert round(geh(row["sim_veh_h"], row["obs_veh_h"]), 4) == row["geh"]

    def test_the_table_is_ordered_and_labelled_as_geh_values(self) -> None:
        scores = table_scores()
        assert scores.link_hours is not None
        assert tuple(r.geh for r in scores.link_hours) == scores.geh_values
        assert [(r.station, r.window_start_s, r.clock) for r in scores.link_hours] == [
            (row[0], row[2], row[3]) for row in TABLE_ROWS
        ]
        stored = scores.to_dict()
        assert [r["geh"] for r in stored["link_hours"]] == stored["geh_values"]

    def test_geh_values_are_the_pre_table_computation(self) -> None:
        """The table is additive: ``geh_values`` is still exactly what the
        scorer computed before it existed — ``link_hour_geh(...).geh`` on the
        artifact's fully observed station-hours — and so is its pass fraction."""
        from validation.metrics import geh_pass_fraction, link_hour_geh

        observed = ObservedCorridor.from_dict(table_payload())
        hourly = observed.hourly_link_flows()
        shifted = hourly.assign(x_ref_m=hourly["x_ref_m"] + TABLE_OFFSET_M)
        before = link_hour_geh(
            table_frame(),
            shifted,
            x_refs_m=[x + TABLE_OFFSET_M for _, x in TABLE_STATIONS],
            window_s=3600.0,
            sim_span=(0.0, TABLE_DURATION_S),
        ).geh
        scores = table_scores()
        assert scores.geh_values == before
        assert geh_pass_fraction(scores.geh_values, 0.5) == geh_pass_fraction(before, 0.5)
        hand = tuple(math.sqrt(2.0 * (sim - obs) ** 2 / (sim + obs)) for *_, obs, sim in TABLE_ROWS)
        assert scores.geh_values == pytest.approx(hand, abs=1e-12)

    def test_the_table_round_trips_through_json(self) -> None:
        from validation.observed import ObservedScores

        scores = table_scores()
        again = ObservedScores.from_dict(json.loads(json.dumps(scores.to_dict())))
        assert again.link_hours is not None and scores.link_hours is not None
        for a, b in zip(again.link_hours, scores.link_hours, strict=True):
            assert (a.station, a.x_ref_m, a.window_start_s, a.clock) == (
                b.station,
                b.x_ref_m,
                b.window_start_s,
                b.clock,
            )
            assert (a.obs_veh_h, a.sim_veh_h) == (b.obs_veh_h, b.sim_veh_h)
            assert a.geh == round(b.geh, 4)
        assert again.geh_values == tuple(r.geh for r in again.link_hours)

    def test_no_compared_hour_gives_an_empty_table_not_none(
        self, observed: ObservedCorridor, trajectories: pd.DataFrame
    ) -> None:
        scores = score_run_against_observed(
            trajectories, observed, warmup_s=DURATION_S, duration_s=DURATION_S
        )
        assert scores.link_hours == ()
        assert scores.to_dict()["link_hours"] == []

    def test_an_old_observed_scores_file_still_loads(self) -> None:
        from validation.observed import ObservedScores

        old = ObservedScores.from_dict(json.loads(json.dumps(OLD_SCORES_FILE)))
        assert old.link_hours is None
        assert old.geh_values == tuple(OLD_SCORES_FILE["geh_values"])
        assert old.n_link_hours == 5
        assert old.n_speed_cells == 5
        assert old.windows == (0, 1)
        assert math.isnan(old.segment_speeds_sim[0][2])
        # and it writes the absence back as null rather than an empty table
        assert old.to_dict()["link_hours"] is None
        assert ObservedScores.from_dict(old.to_dict()).link_hours is None

    def test_a_table_that_does_not_label_every_geh_is_refused(self) -> None:
        import dataclasses

        scores = table_scores()
        assert scores.link_hours is not None
        with pytest.raises(ValueError, match="labels geh_values row by row"):
            dataclasses.replace(scores, link_hours=scores.link_hours[:-1])


#: A second replicate of the table fixture: the vehicles departing at 3570 and
#: 5000 s are dropped, leaving S1 3 / 2, S2 - / 2, S3 2 / 3 (hand count above),
#: in table order.
TABLE_SECOND_DEPARTS_S = (100.0, 200.0, 3540.0, 4000.0, 7125.0)
TABLE_SECOND_SIM = (3.0, 2.0, 2.0, 2.0, 3.0)


class TestPoolLinkHours:
    def test_mean_and_range_over_the_seeds_per_station_hour(self) -> None:
        from validation.metrics import geh
        from validation.observed import pool_link_hours

        first = table_scores()
        second = table_scores(table_frame(TABLE_SECOND_DEPARTS_S))
        assert second.link_hours is not None
        assert [r.sim_veh_h for r in second.link_hours] == pytest.approx(TABLE_SECOND_SIM)
        pooled = pool_link_hours([first, second])
        assert pooled is not None
        assert [(p.station, p.window_start_s, p.clock) for p in pooled] == [
            (row[0], row[2], row[3]) for row in TABLE_ROWS
        ]
        for p, row, sim_b in zip(pooled, TABLE_ROWS, TABLE_SECOND_SIM, strict=True):
            sim_a, obs = row[5], row[4]
            assert p.n_seeds == 2
            assert p.obs_veh_h == obs
            assert p.x_ref_m == row[1]
            assert p.sim_veh_h_mean == pytest.approx((sim_a + sim_b) / 2.0)
            assert p.sim_veh_h_min == pytest.approx(min(sim_a, sim_b))
            assert p.sim_veh_h_max == pytest.approx(max(sim_a, sim_b))
            gehs = [round(geh(sim_a, obs), 4), round(geh(sim_b, obs), 4)]
            assert p.geh_mean == pytest.approx(sum(gehs) / 2.0, abs=1e-12)
            assert (p.geh_min, p.geh_max) == (min(gehs), max(gehs))

    def test_a_station_hour_some_seeds_did_not_compare_counts_only_those(self) -> None:
        """A run that never reaches S3 contributes nothing to S3's rows."""
        from validation.observed import pool_link_hours

        short = table_scores(table_frame(end_m=500.0))
        assert short.stations_outside_span == ("S3",)
        pooled = pool_link_hours([table_scores(), short])
        assert pooled is not None
        seeds = {(p.station, p.window_start_s): p.n_seeds for p in pooled}
        assert seeds == {
            ("S1", 0.0): 2,
            ("S1", 3600.0): 2,
            ("S2", 3600.0): 2,
            ("S3", 0.0): 1,
            ("S3", 3600.0): 1,
        }

    def test_none_when_a_replicate_carries_no_table(self) -> None:
        import dataclasses

        from validation.observed import pool_link_hours

        scores = table_scores()
        assert pool_link_hours([scores, dataclasses.replace(scores, link_hours=None)]) is None

    def test_different_observations_are_refused(self) -> None:
        import dataclasses

        from validation.observed import pool_link_hours

        scores = table_scores()
        assert scores.link_hours is not None
        moved = (
            dataclasses.replace(scores.link_hours[0], obs_veh_h=99.0),
            *scores.link_hours[1:],
        )
        with pytest.raises(ValueError, match="different observations"):
            pool_link_hours([scores, dataclasses.replace(scores, link_hours=moved)])

    def test_no_scores_refused(self) -> None:
        from validation.observed import pool_link_hours

        with pytest.raises(ValueError, match="at least one"):
            pool_link_hours([])
