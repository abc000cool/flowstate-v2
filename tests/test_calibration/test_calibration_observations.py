"""Observations artifact and the demand it produces.

``calibration.observations`` (window indexing on local wall time, the
mean/spread over dates, the JSON round trip, the report's derived views) and
the ``flowstate.demand/1`` half of ``calibration.demand`` (inflow steps, ramp
detectors and the conservation closure), all against hand-computed numbers.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from pathlib import Path

import pandas as pd
import pytest

from calibration.demand import (
    DEMAND_SCHEMA,
    DemandArtifact,
    demand_from_observations,
    demand_from_observations_artifact,
    ramp_flows_from_observations,
)
from calibration.loaders.detector_csv import load_detector_csv, write_detector_csv
from calibration.observations import (
    OBSERVATIONS_SCHEMA,
    Observations,
    ObservedStation,
    coverage,
    hourly_link_flows,
    parse_clock,
    segment_speed_matrix,
)

WINDOW_S = 300.0
N_WINDOWS = 24  # two hours from 06:00


def _timestamp(date: str, window: int) -> str:
    minute = window * 5
    return f"{date}T{6 + minute // 60:02d}:{minute % 60:02d}:00-05:00"


def _frame(
    *,
    dates: tuple[str, ...] = ("2026-09-15", "2026-09-16"),
    flows: dict[str, float] | None = None,
    drop: set[tuple[str, int]] = frozenset(),  # type: ignore[assignment]
) -> pd.DataFrame:
    """Two stations 1 km apart plus a ramp detector, constant over the span."""
    flows = flows or {"S1": 1800.0, "S2": 2160.0, "R1": 360.0}
    meta = {
        "S1": (0.0, 3, "mainline"),
        "S2": (1000.0, 3, "mainline"),
        "R1": (500.0, 1, "on_ramp"),
    }
    rows = []
    for date in dates:
        for window in range(N_WINDOWS):
            for station, flow in flows.items():
                x_m, lanes, kind = meta[station]
                missing = (station, window) in drop
                rows.append(
                    {
                        "timestamp": _timestamp(date, window),
                        "station": station,
                        "flow_veh_h": None if missing else flow,
                        "occupancy_pct": None if missing else 10.0,
                        "speed_ms": None if missing else 25.0,
                        "lanes": lanes,
                        "kind": kind,
                        "x_m": x_m,
                    }
                )
    return pd.DataFrame(rows)


def _observations(tmp_path: Path, **kwargs: object) -> Observations:
    path = tmp_path / "det.csv"
    _frame(**kwargs).to_csv(path, index=False)  # type: ignore[arg-type]
    return Observations.from_frame(
        load_detector_csv(path),
        None,
        window_s=WINDOW_S,
        t0_local="06:00",
        duration_s=WINDOW_S * N_WINDOWS,
        corridor="mndot_i94_wb",
        source={"provider": "MnDOT RTMC Mayfly API", "dates": ["20260915", "20260916"]},
    )


class TestFromFrame:
    def test_windows_are_indexed_from_t0_local(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path)
        assert obs.schema == OBSERVATIONS_SCHEMA
        assert obs.n_windows == N_WINDOWS
        assert obs.window_start_s(0) == 0.0
        assert obs.window_start_s(3) == 900.0
        assert obs.flows_veh_h["S1"] == [1800.0] * N_WINDOWS
        assert [s.id for s in obs.stations] == ["R1", "S1", "S2"]
        assert obs.station("S2").x_m == 1000.0
        assert obs.station("R1").kind == "on_ramp"

    def test_rows_outside_the_span_are_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "det.csv"
        frame = _frame()
        # An 05:55 row, one window before t0, must not land in window 0.
        early = frame.iloc[[0]].copy()
        early["timestamp"] = "2026-09-15T05:55:00-05:00"
        early["flow_veh_h"] = 9999.0
        pd.concat([early, frame]).to_csv(path, index=False)
        obs = Observations.from_frame(
            load_detector_csv(path),
            None,
            window_s=WINDOW_S,
            t0_local="06:00",
            duration_s=WINDOW_S * N_WINDOWS,
            corridor="c",
            source="test",
        )
        assert obs.flows_veh_h["S1"][0] == 1800.0

    def test_mean_and_spread_over_dates(self, tmp_path: Path) -> None:
        path = tmp_path / "det.csv"
        rows = []
        for date, flow in (("2026-09-15", 1800.0), ("2026-09-16", 2200.0)):
            rows.append(
                {
                    "timestamp": _timestamp(date, 0),
                    "station": "S1",
                    "flow_veh_h": flow,
                    "occupancy_pct": 10.0,
                    "speed_ms": 25.0,
                    "lanes": 3,
                    "kind": "mainline",
                    "x_m": 0.0,
                }
            )
        pd.DataFrame(rows).to_csv(path, index=False)
        obs = Observations.from_frame(
            load_detector_csv(path),
            None,
            window_s=WINDOW_S,
            t0_local="06:00",
            duration_s=WINDOW_S,
            corridor="c",
            source="test",
        )
        assert obs.flows_veh_h["S1"] == [2000.0]
        # Sample sd of {1800, 2200} is 200*sqrt(2) = 282.84...
        assert obs.spread["flows_veh_h_sd"]["S1"][0] == pytest.approx(200.0 * math.sqrt(2.0))
        assert obs.quality["S1"] == {"fraction_valid": 1.0, "n_dates": 2.0}

    def test_unobserved_windows_are_nan_and_counted(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path, drop={("S1", 0), ("S1", 1)})
        assert math.isnan(obs.flows_veh_h["S1"][0])
        assert obs.quality["S1"]["fraction_valid"] == pytest.approx(22 / 24)
        assert obs.quality["S2"]["fraction_valid"] == 1.0

    def test_explicit_stations_table_supplies_metadata(self, tmp_path: Path) -> None:
        path = tmp_path / "det.csv"
        _frame().to_csv(path, index=False)
        table = [
            {"station": "S1", "label": "Co Rd 71", "x_m": 0.0, "lanes": 3, "kind": "mainline"},
            {"station": "S2", "label": "Manning", "x_m": 1000.0, "lanes": 3, "kind": "mainline"},
        ]
        obs = Observations.from_frame(
            load_detector_csv(path),
            table,
            window_s=WINDOW_S,
            t0_local="06:00",
            duration_s=WINDOW_S * N_WINDOWS,
            corridor="c",
            source="test",
        )
        assert [s.id for s in obs.stations] == ["S1", "S2"]  # the ramp was not listed
        assert obs.station("S1").label == "Co Rd 71"

    def test_window_mismatch_and_bad_clock_are_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "det.csv"
        _frame().to_csv(path, index=False)
        frame = load_detector_csv(path)
        with pytest.raises(ValueError, match="window grid"):
            Observations.from_frame(
                frame,
                None,
                window_s=600.0,
                t0_local="06:00",
                duration_s=7200.0,
                corridor="c",
                source="t",
            )
        with pytest.raises(ValueError, match="whole multiple"):
            Observations.from_frame(
                frame,
                None,
                window_s=WINDOW_S,
                t0_local="06:00",
                duration_s=450.0,
                corridor="c",
                source="t",
            )
        with pytest.raises(ValueError, match="HH:MM"):
            parse_clock("6am")
        with pytest.raises(ValueError, match="clock time"):
            parse_clock("25:00")


class TestArtifactRoundTrip:
    def test_json_round_trip_keeps_nan_as_null(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path, drop={("S1", 4)})
        path = obs.to_json(tmp_path / "artifacts" / "observations.json")
        raw = path.read_text()
        assert "NaN" not in raw  # JSON has no NaN; the contract says null
        assert '"schema": "flowstate.observations/1"' in raw
        back = Observations.from_json(path)
        assert back.to_dict() == obs.to_dict()
        assert math.isnan(back.flows_veh_h["S1"][4])
        assert back.n_windows == obs.n_windows

    def test_context_round_trips_and_is_absent_when_empty(self, tmp_path: Path) -> None:
        """The optional context block, e.g. an observed wave-speed estimate.

        An artifact that carries none is byte-for-byte what earlier versions
        wrote (no ``"context"`` key at all), and one that carries a context
        survives the round trip untouched.
        """
        plain = _observations(tmp_path)
        assert "context" not in plain.to_dict()
        assert '"context"' not in plain.to_json(tmp_path / "plain.json").read_text()

        context = {
            "detector_wave_speed": {
                "median_kmh": 19.4,
                "iqr_kmh": [17.1, 21.8],
                "n_pairs": 13,
                "n_used": 6,
                "rejected": {"peak correlation below the acceptance floor": 7},
            }
        }
        annotated = dataclasses.replace(plain, context=context)
        path = annotated.to_json(tmp_path / "annotated.json")
        assert json.loads(path.read_text())["context"] == context
        back = Observations.from_json(path)
        assert back.context == context
        assert back.to_dict() == annotated.to_dict()
        # The context is the only difference from the plain artifact.
        plain_payload = plain.to_dict()
        annotated_payload = back.to_dict()
        assert annotated_payload.pop("context") == context
        assert annotated_payload == plain_payload

    def test_foreign_schema_is_refused(self) -> None:
        with pytest.raises(ValueError, match=re.escape("flowstate.observations/1")):
            Observations.from_dict(
                {"schema": "other/1", "window_s": 300, "t0_local": "06:00", "duration_s": 300}
            )

    def test_station_entry_needs_an_id(self) -> None:
        with pytest.raises(ValueError, match="no 'id'"):
            ObservedStation.from_mapping({"label": "nameless"})


class TestDerivedViews:
    def test_hourly_link_flows_need_every_window(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path)
        hourly = hourly_link_flows(obs)
        assert list(hourly.columns) == ["x_ref_m", "window_start_s", "flow_veh_h", "station"]
        assert len(hourly) == 4  # 2 stations x 2 complete hours
        first = hourly.iloc[0]
        assert (first["x_ref_m"], first["window_start_s"], first["flow_veh_h"]) == (
            0.0,
            0.0,
            1800.0,
        )
        # One missing window removes that station-hour, not the whole hour.
        holed = _observations(tmp_path, drop={("S1", 2)})
        rows = hourly_link_flows(holed)
        assert set(zip(rows["station"], rows["window_start_s"], strict=True)) == {
            ("S1", 3600.0),
            ("S2", 0.0),
            ("S2", 3600.0),
        }

    def test_segment_speed_matrix_is_windows_by_station(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path, drop={("S2", 0)})
        matrix, xs = segment_speed_matrix(obs)
        assert xs == [0.0, 1000.0]  # ramps excluded, ordered by position
        assert matrix.shape == (N_WINDOWS, 2)
        assert matrix[0, 0] == pytest.approx(25.0)
        assert math.isnan(matrix[0, 1])

    def test_coverage_table(self, tmp_path: Path) -> None:
        table = coverage(_observations(tmp_path, drop={("S1", 0)}))
        s1 = table[table["station"] == "S1"].iloc[0]
        assert s1["n_observed"] == N_WINDOWS - 1
        assert s1["fraction_valid"] == pytest.approx(23 / 24)


class TestDemandFromObservations:
    def test_inflow_steps_are_the_upstream_station_in_si(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path)
        steps = demand_from_observations(obs, "S1", step_s=WINDOW_S)
        assert len(steps) == N_WINDOWS
        assert steps[0] == (0.0, pytest.approx(0.5))  # 1800 veh/h = 0.5 veh/s
        assert steps[1][0] == 300.0

    def test_coarser_steps_average_their_windows(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path)
        obs.flows_veh_h["S1"][0] = 1800.0
        obs.flows_veh_h["S1"][1] = 2160.0
        obs.flows_veh_h["S1"][2] = 1800.0
        steps = demand_from_observations(obs, "S1", step_s=900.0)
        assert len(steps) == N_WINDOWS // 3
        assert steps[0][1] == pytest.approx((1800.0 + 2160.0 + 1800.0) / 3.0 / 3600.0)

    def test_unknown_station_and_bad_step_are_refused(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path)
        with pytest.raises(KeyError, match="S9"):
            demand_from_observations(obs, "S9")
        with pytest.raises(ValueError, match="multiple"):
            demand_from_observations(obs, "S1", step_s=400.0)

    def test_a_station_with_nothing_observed_is_an_error(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path)
        obs.flows_veh_h["S1"] = [float("nan")] * N_WINDOWS
        with pytest.raises(ValueError, match="nothing to build a demand profile from"):
            demand_from_observations(obs, "S1")

    def test_ramp_detector_and_conservation_closure(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path)
        ramps = ramp_flows_from_observations(
            obs,
            [
                {"name": "measured on", "kind": "on", "x_m": 500.0, "station": "R1"},
                {"name": "closed on", "kind": "on_ramp", "x_m": 600.0},
                {"name": "closed off", "kind": "off", "x_m": 700.0},
                {"name": "measured off", "kind": "off_ramp", "x_m": 800.0, "station": "R1"},
            ],
            step_s=WINDOW_S,
        )
        measured_on, closed_on, closed_off, measured_off = ramps
        # Detector: 360 veh/h = 0.1 veh/s.
        assert measured_on["method"] == "detector"
        assert measured_on["inflow_steps"][0] == [0.0, pytest.approx(0.1)]
        # Conservation: q_down - q_up = 2160 - 1800 = 360 veh/h = 0.1 veh/s.
        assert closed_on["method"] == "conservation"
        assert closed_on["reference_station"] == "S1"
        assert closed_on["downstream_station"] == "S2"
        assert closed_on["inflow_steps"][0] == [0.0, pytest.approx(0.1)]
        # Conservation off-ramp: flow rises downstream, so the exit fraction
        # is clamped at 0 rather than going negative.
        assert closed_off["exit_fraction_steps"][0] == [0.0, 0.0]
        # Detector off-ramp: 360 / 1800 upstream = 0.2.
        assert measured_off["exit_fraction_steps"][0] == [0.0, pytest.approx(0.2)]
        assert measured_off["reference_station"] == "S1"

    def test_conservation_off_ramp_fraction(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path, flows={"S1": 2160.0, "S2": 1800.0, "R1": 360.0})
        (ramp,) = ramp_flows_from_observations(
            obs, [{"name": "off", "kind": "off", "x_m": 500.0}], step_s=WINDOW_S
        )
        assert ramp["exit_fraction_steps"][0] == [0.0, pytest.approx((2160.0 - 1800.0) / 2160.0)]

    def test_unbracketed_ramp_and_bad_descriptor_are_refused(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path)
        with pytest.raises(ValueError, match="not bracketed"):
            ramp_flows_from_observations(obs, [{"name": "r", "kind": "on", "x_m": 5000.0}])
        with pytest.raises(ValueError, match="kind must be"):
            ramp_flows_from_observations(obs, [{"name": "r", "kind": "merge", "x_m": 100.0}])
        with pytest.raises(ValueError, match="x_m"):
            ramp_flows_from_observations(obs, [{"name": "r", "kind": "on"}])


class TestDemandArtifact:
    def test_artifact_round_trip(self, tmp_path: Path) -> None:
        obs = _observations(tmp_path, drop={("S1", 0)})
        observations_path = obs.to_json(tmp_path / "observations.json")
        artifact = demand_from_observations_artifact(
            obs,
            "S1",
            ramps=[{"name": "measured on", "kind": "on", "x_m": 500.0, "station": "R1"}],
            observations_path=str(observations_path),
            step_s=WINDOW_S,
        )
        assert artifact.schema == DEMAND_SCHEMA
        assert artifact.upstream_station == "S1"
        # The unobserved first window is not invented: the profile starts at
        # t=0 with the first observed level, and the gap is recorded.
        assert artifact.inflow_steps[0] == (0.0, pytest.approx(0.5))
        assert artifact.coverage == {"n_steps": 24.0, "n_steps_carried": 1.0}
        path = artifact.to_json(tmp_path / "demand.json")
        assert DemandArtifact.from_json(path).to_dict() == artifact.to_dict()
        assert "NaN" not in path.read_text()

    def test_written_frame_feeds_the_artifact(self, tmp_path: Path) -> None:
        """The CLI's detectors.csv reloads into the same observations."""
        path = tmp_path / "det.csv"
        _frame().to_csv(path, index=False)
        frame = load_detector_csv(path)
        again = load_detector_csv(write_detector_csv(frame, tmp_path / "detectors.csv"))
        kwargs = {
            "window_s": WINDOW_S,
            "t0_local": "06:00",
            "duration_s": WINDOW_S * N_WINDOWS,
            "corridor": "c",
            "source": "test",
        }
        first = Observations.from_frame(frame, None, **kwargs)  # type: ignore[arg-type]
        second = Observations.from_frame(again, None, **kwargs)  # type: ignore[arg-type]
        assert first.to_dict() == second.to_dict()
