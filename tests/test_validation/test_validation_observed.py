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
