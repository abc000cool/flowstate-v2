"""Loader tests on tiny synthetic fixture files (calibration.loaders).

Fixtures live in ``tests/test_calibration/fixtures/`` and follow each
dataset's documented format; assertions spot-check exact unit conversions
(feet → m via the single FEET_TO_M ingestion constant) and episode
invariants. No network, no registered-access data.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from calibration.loaders.highd import frame_rate_of, load_highd_episodes
from calibration.loaders.i24motion import load_i24_episodes, load_i24_trajectories
from calibration.loaders.ngsim import (
    FEET_TO_M,
    build_ngsim_episodes,
    load_ngsim_episodes,
    load_ngsim_trajectories,
)
from calibration.loaders.pems import MPH_TO_MS, load_pems_station_csv

FIXTURES = Path(__file__).parent / "fixtures"


class TestNgsimLoader:
    def test_feet_to_m_constant(self) -> None:
        assert FEET_TO_M == 0.3048  # exact international foot

    def test_tidy_units_exact(self) -> None:
        df = load_ngsim_trajectories(FIXTURES / "ngsim_tiny.csv")
        assert len(df) == 200
        veh1 = df[df["veh_id"] == "1"].sort_values("frame")
        # v_Vel = 50 ft/s, Local_Y starts at 200 ft, v_Length = 14 ft.
        assert veh1["v"].iloc[0] == pytest.approx(50.0 * FEET_TO_M, rel=1e-12)
        assert veh1["x"].iloc[0] == pytest.approx(200.0 * FEET_TO_M, rel=1e-12)
        assert veh1["length_m"].iloc[0] == pytest.approx(14.0 * FEET_TO_M, rel=1e-12)
        # 10 Hz frame clock.
        assert np.diff(veh1["t"].to_numpy())[0] == pytest.approx(0.1, rel=1e-9)

    def test_episode_cut_at_lane_change(self) -> None:
        df = load_ngsim_trajectories(FIXTURES / "ngsim_tiny.csv")
        eps = build_ngsim_episodes(df, min_duration_s=4.0)
        # Vehicle 2 follows vehicle 1 for frames 1..60 then changes lane.
        assert len(eps) == 1
        ep = eps[0]
        assert ep.veh_id == "2"
        assert ep.metadata["leader_id"] == "1"
        assert ep.metadata["lane"] == 2
        assert ep.n == 60
        assert ep.duration_s == pytest.approx(5.9, rel=1e-9)
        assert ep.dt == pytest.approx(0.1, rel=1e-9)
        # gap = (Space_Headway - leader v_Length) * FEET_TO_M, bumper-to-bumper.
        np.testing.assert_allclose(ep.gap_m, (100.0 - 14.0) * FEET_TO_M, rtol=1e-12)
        np.testing.assert_allclose(ep.v_leader, 50.0 * FEET_TO_M, rtol=1e-12)
        np.testing.assert_allclose(ep.v_follower, 50.0 * FEET_TO_M, rtol=1e-12)

    def test_downsample(self) -> None:
        df = load_ngsim_trajectories(FIXTURES / "ngsim_tiny.csv", downsample=2)
        assert set(df["frame"] % 2) == {0}
        eps = build_ngsim_episodes(df, min_duration_s=4.0)
        assert len(eps) == 1
        assert eps[0].dt == pytest.approx(0.2, rel=1e-9)
        assert eps[0].n == 30

    def test_default_min_duration_rejects_short_fixture(self) -> None:
        # Contract default: >= 30 s continuous car-following (CLAUDE.md §6.2).
        assert load_ngsim_episodes(FIXTURES / "ngsim_tiny.csv") == []

    def test_lowercase_column_spelling(self) -> None:
        df = load_ngsim_trajectories(FIXTURES / "ngsim_tiny_lower.csv")
        assert len(df) == 20
        veh2 = df[df["veh_id"] == "2"]
        assert veh2["v"].iloc[0] == pytest.approx(48.0 * FEET_TO_M, rel=1e-12)
        assert veh2["leader_id"].iloc[0] == "1"

    def test_missing_columns_raise(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.csv"
        bad.write_text("Vehicle_ID,Frame_ID\n1,1\n")
        with pytest.raises(ValueError, match="missing NGSIM columns"):
            load_ngsim_trajectories(bad)

    def test_bad_downsample_raises(self) -> None:
        with pytest.raises(ValueError, match="downsample"):
            load_ngsim_trajectories(FIXTURES / "ngsim_tiny.csv", downsample=0)


class TestPemsLoader:
    def test_mph_constant_derived_from_feet(self) -> None:
        assert MPH_TO_MS == pytest.approx(0.44704, abs=1e-15)
        assert MPH_TO_MS == FEET_TO_M * 5280.0 / 3600.0

    def test_pems_units_exact(self) -> None:
        df = load_pems_station_csv(FIXTURES / "pems_tiny.csv", g_effective_length_m=7.0)
        assert len(df) == 12
        row = df.iloc[0]  # Flow=150 veh/5-min, Occupancy=0.08, Speed=65 mph
        assert row["flow_veh_s"] == pytest.approx(150.0 / 300.0, rel=1e-12)
        assert row["speed_ms"] == pytest.approx(65.0 * MPH_TO_MS, rel=1e-12)
        assert row["occupancy"] == pytest.approx(0.08, rel=1e-12)
        # Documented g-factor estimate: rho = occupancy / g.
        assert row["density_veh_m"] == pytest.approx(0.08 / 7.0, rel=1e-12)
        assert row["station"] == "717490"
        assert (df["density_veh_m"] >= 0).all()

    def test_generic_column_map_txdot_style(self) -> None:
        df = load_pems_station_csv(
            FIXTURES / "txdot_tiny.csv",
            column_map={
                "timestamp": "time",
                "station": "det_id",
                "flow": "volume",
                "occupancy": "occ_pct",
                "speed": "spd_kmh",
            },
            occupancy_unit="percent",
            speed_unit="kmh",
            g_effective_length_m=7.0,
        )
        assert len(df) == 6
        row = df.iloc[0]
        assert row["occupancy"] == pytest.approx(0.08, rel=1e-9)
        # 65 mph was written as km/h (3 decimals) -> back to m/s via units.
        assert row["speed_ms"] == pytest.approx(65.0 * MPH_TO_MS, rel=1e-4)
        assert row["flow_veh_s"] == pytest.approx(150.0 / 300.0, rel=1e-12)
        assert row["station"] == "TX42"

    def test_missing_column_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.csv"
        bad.write_text("Timestamp,Flow\n2024,1\n")
        with pytest.raises(ValueError, match="missing columns"):
            load_pems_station_csv(bad)

    def test_bad_g_raises(self) -> None:
        with pytest.raises(ValueError, match="g_effective_length_m"):
            load_pems_station_csv(FIXTURES / "pems_tiny.csv", g_effective_length_m=0.0)


class TestHighdLoader:
    def test_episode_units_and_invariants(self) -> None:
        eps = load_highd_episodes(
            FIXTURES / "highd_tracks_tiny.csv",
            FIXTURES / "highd_recordingMeta_tiny.csv",
            min_duration_s=4.0,
        )
        assert len(eps) == 1
        ep = eps[0]
        assert ep.veh_id == "2"
        assert ep.metadata["leader_id"] == "1"
        assert ep.metadata["dataset"] == "highd"
        assert ep.metadata["frame_rate_hz"] == 25.0
        assert ep.n == 150
        assert ep.dt == pytest.approx(1.0 / 25.0, rel=1e-9)
        # 40 m front-to-front minus leader length (highD 'width' = 5.1 m).
        np.testing.assert_allclose(ep.gap_m, 40.0 - 5.1, atol=1e-9)
        np.testing.assert_allclose(ep.v_follower, 30.0, atol=1e-12)
        np.testing.assert_allclose(ep.v_leader, 30.0, atol=1e-12)

    def test_frame_rate_reader(self) -> None:
        assert frame_rate_of(FIXTURES / "highd_recordingMeta_tiny.csv") == 25.0

    def test_missing_tracks_columns_raise(self, tmp_path: Path) -> None:
        bad = tmp_path / "tracks.csv"
        bad.write_text("frame,id\n1,1\n")
        with pytest.raises(ValueError, match="missing highD columns"):
            load_highd_episodes(bad, FIXTURES / "highd_recordingMeta_tiny.csv")


class TestI24MotionLoader:
    def test_tidy_units_exact(self) -> None:
        df = load_i24_trajectories(FIXTURES / "i24_tiny.json", direction=-1)
        assert set(df["veh_id"]) == {"L1", "F2"}
        lead = df[df["veh_id"] == "L1"]
        # Westbound: x = -x_position * FEET_TO_M so x increases with travel.
        assert lead["x"].iloc[0] == pytest.approx(-4800.0 * FEET_TO_M, rel=1e-12)
        assert lead["length"].iloc[0] == pytest.approx(15.0 * FEET_TO_M, rel=1e-12)
        # Constant 88 ft/s -> finite-difference speed (up to float grid jitter
        # from snapping the 25 Hz timestamps, ~1e-6 relative).
        np.testing.assert_allclose(lead["v"].to_numpy(), 88.0 * FEET_TO_M, rtol=1e-5)
        # y = 30 ft in 12 ft lanes -> lane index 2.
        assert set(lead["lane"]) == {2}

    def test_position_ordered_episode(self) -> None:
        eps = load_i24_episodes(FIXTURES / "i24_tiny.json", min_duration_s=4.0)
        assert len(eps) == 1
        ep = eps[0]
        assert ep.veh_id == "F2"
        assert ep.metadata["leader_id"] == "L1"
        assert ep.metadata["dataset"] == "i24motion"
        assert ep.n == 151
        assert ep.dt == pytest.approx(1.0 / 25.0, rel=1e-6)
        # gap = (120 ft spacing - 15 ft leader length) in meters.
        np.testing.assert_allclose(ep.gap_m, (120.0 - 15.0) * FEET_TO_M, atol=1e-6)
        np.testing.assert_allclose(ep.v_follower, 88.0 * FEET_TO_M, rtol=1e-5)

    def test_direction_filter(self) -> None:
        df = load_i24_trajectories(FIXTURES / "i24_tiny.json", direction=1)
        assert set(df["veh_id"]) == {"E9"}  # eastbound-only doc
        assert load_i24_episodes(FIXTURES / "i24_tiny.json", direction=1, min_duration_s=1.0) == []

    def test_missing_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            load_i24_trajectories(FIXTURES / "i24_tiny.json", direction=7)
