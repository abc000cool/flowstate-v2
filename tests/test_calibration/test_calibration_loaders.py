"""Loader tests on tiny synthetic fixture files (calibration.loaders).

Fixtures live in ``tests/test_calibration/fixtures/`` and follow each
dataset's documented format; assertions spot-check exact unit conversions
(feet → m via the single FEET_TO_M ingestion constant) and episode
invariants. No network, no registered-access data.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from calibration.loaders.highd import frame_rate_of, load_highd_episodes
from calibration.loaders.i24motion import (
    convert_i24_to_parquet,
    i24_document_id,
    iter_i24_documents,
    load_i24_episodes,
    load_i24_parquet,
    load_i24_trajectories,
    load_i24_vehicles,
    sha256_file,
)
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
        # Westbound: x_position is the BACK-center roadway coordinate (data
        # documentation v1.x), travel is toward decreasing x_position, so
        # x = -(x_position - x_ref) * FEET_TO_M + length puts the FRONT bumper
        # on a travel-oriented axis.
        assert lead["x"].iloc[0] == pytest.approx((-4800.0 + 15.0) * FEET_TO_M, rel=1e-12)
        assert lead["length"].iloc[0] == pytest.approx(15.0 * FEET_TO_M, rel=1e-12)
        # Constant 88 ft/s -> finite-difference speed (up to float grid jitter
        # from snapping the 25 Hz timestamps, ~1e-6 relative).
        np.testing.assert_allclose(lead["v"].to_numpy(), 88.0 * FEET_TO_M, rtol=1e-5)
        # y = 30 ft in the documented 12 ft westbound bands -> lane 2.
        assert set(lead["lane"]) == {2}
        assert lead["y"].iloc[0] == pytest.approx(30.0 * FEET_TO_M, rel=1e-12)
        # Grid-snapped time starts at 0 and steps by 0.04 s.
        np.testing.assert_allclose(np.diff(lead["t"].to_numpy()), 0.04, atol=1e-9)
        assert lead["t"].iloc[0] == 0.0

    def test_position_ordered_episode(self) -> None:
        eps = load_i24_episodes(FIXTURES / "i24_tiny.json", min_duration_s=4.0)
        assert len(eps) == 1
        ep = eps[0]
        assert ep.veh_id == "F2"
        assert ep.metadata["leader_id"] == "L1"
        assert ep.metadata["dataset"] == "i24motion"
        assert ep.n == 151
        assert ep.dt == pytest.approx(1.0 / 25.0, rel=1e-6)
        # Back-center spacing 120 ft; the bumper-to-bumper gap is the leader's
        # back minus the follower's FRONT, i.e. spacing - follower length
        # (16 ft) — not spacing - leader length as a front-position schema
        # would give.
        np.testing.assert_allclose(ep.gap_m, (120.0 - 16.0) * FEET_TO_M, atol=1e-6)
        np.testing.assert_allclose(ep.v_follower, 88.0 * FEET_TO_M, rtol=1e-5)

    def test_downsample_keeps_shared_grid(self) -> None:
        df = load_i24_trajectories(FIXTURES / "i24_tiny.json", direction=-1, downsample=5)
        # Every kept slot is a multiple of 0.2 s, identical for both vehicles.
        for _, g in df.groupby("veh_id"):
            np.testing.assert_allclose(np.diff(g["t"].to_numpy()), 0.2, atol=1e-9)
        assert set(df[df["veh_id"] == "L1"]["t"]) == set(df[df["veh_id"] == "F2"]["t"])
        # Speed was computed at 25 Hz before decimation.
        np.testing.assert_allclose(df["v"].to_numpy(), 88.0 * FEET_TO_M, rtol=1e-5)
        eps = load_i24_episodes(FIXTURES / "i24_tiny.json", min_duration_s=4.0, downsample=5)
        assert len(eps) == 1 and eps[0].dt == pytest.approx(0.2)

    def test_direction_filter(self) -> None:
        df = load_i24_trajectories(FIXTURES / "i24_tiny.json", direction=1)
        assert set(df["veh_id"]) == {"E9"}  # eastbound-only doc
        # Eastbound: x increases with x_position; y = -20 ft mirrors to lane 1.
        assert df["x"].iloc[0] == pytest.approx((1000.0 + 14.0) * FEET_TO_M, rel=1e-12)
        assert set(df["lane"]) == {1}
        assert load_i24_episodes(FIXTURES / "i24_tiny.json", direction=1, min_duration_s=1.0) == []

    def test_missing_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            load_i24_trajectories(FIXTURES / "i24_tiny.json", direction=7)


def _mongo_export(tmp_path: Path, *, indent: int | None = 1, as_zip: bool = True) -> Path:
    """The tiny fixture re-shaped like the INCEPTION export (``$oid`` ids)."""
    docs = json.loads((FIXTURES / "i24_tiny.json").read_text())
    for d in docs:
        d["_id"] = {"$oid": d["_id"]}
        d["first_timestamp"] = d["timestamp"][0]
        d["last_timestamp"] = d["timestamp"][-1]
        d["coarse_vehicle_class"] = 0
        d["flags"] = ["Lost"]
    text = json.dumps(docs, indent=indent)
    if not as_zip:
        p = tmp_path / "export.json"
        p.write_text(text)
        return p
    p = tmp_path / "export.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("export.json", text)
    return p


class TestI24Streaming:
    def test_iter_documents_from_zip_with_tiny_chunks(self, tmp_path: Path) -> None:
        # A 97-char buffer forces many refills and objects straddling chunk
        # boundaries — the streaming decoder must still yield every document.
        path = _mongo_export(tmp_path)
        docs = list(iter_i24_documents(path, chunk_chars=97))
        assert [i24_document_id(d) for d in docs] == ["L1", "F2", "E9"]
        assert docs == list(iter_i24_documents(path))  # chunking is invisible

    def test_iter_documents_compact_and_ndjson(self, tmp_path: Path) -> None:
        compact = _mongo_export(tmp_path, indent=None, as_zip=False)
        assert len(list(iter_i24_documents(compact, chunk_chars=50))) == 3
        docs = json.loads(compact.read_text())
        nd = tmp_path / "export.ndjson.json"
        nd.write_text("\n".join(json.dumps(d) for d in docs) + "\n")
        assert [i24_document_id(d) for d in iter_i24_documents(nd)] == ["L1", "F2", "E9"]

    def test_iter_documents_rejects_truncated(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text('[{"_id": "a", "timestamp": [1.0, 1.04')
        with pytest.raises(ValueError, match="truncated"):
            list(iter_i24_documents(p, chunk_chars=8))

    def test_convert_to_parquet_round_trip(self, tmp_path: Path) -> None:
        path = _mongo_export(tmp_path)
        out = tmp_path / "wb"
        summary = convert_i24_to_parquet(
            path, out, direction=-1, downsample=5, mm_range=(0.0, 1.0), row_group_rows=10
        )
        assert summary.n_docs_read == 3
        assert summary.n_docs_direction == 2 == summary.n_docs_kept
        assert summary.n_samples_native == 302
        assert summary.t_origin_unix == 1668600000.0  # floored to the hour
        assert summary.x_ref_ft == 5280.0  # westbound: x = 0 at the max mile marker
        assert summary.class_counts == {"sedan": 2}
        assert summary.duration_hist[1] == 2 and summary.n_docs_ge_30s == 0
        assert summary.data_hash == sha256_file(path)

        df = load_i24_parquet(out)
        assert list(df.columns) == ["t", "veh_id", "x", "y", "lane", "v", "length", "cls"]
        assert summary.n_rows == len(df) == 62
        lead = df[df["veh_id"] == "L1"]
        # x = (5280 - 4800) ft + 15 ft length, travel oriented, front bumper.
        assert lead["x"].iloc[0] == pytest.approx((5280.0 - 4800.0 + 15.0) * FEET_TO_M)
        np.testing.assert_allclose(np.diff(lead["t"].to_numpy()), 0.2, atol=1e-9)
        assert str(df["lane"].dtype) == "int8" and set(lead["lane"]) == {2}

        # Pushed-down slices.
        sl = load_i24_parquet(out, t_range_s=(1.0, 2.0), lanes=(2, 2), columns=["t", "veh_id"])
        assert list(sl.columns) == ["t", "veh_id"]
        assert sl["t"].between(1.0, 2.0 - 1e-9).all() and len(sl) == 10
        assert load_i24_parquet(out, x_range_m=(0.0, 1.0)).empty

        veh = load_i24_vehicles(out)
        assert set(veh["veh_id"]) == {"L1", "F2"}
        assert veh["n_samples"].tolist() == [151, 151]
        assert veh["flags"].tolist() == ["Lost", "Lost"]
        meta = json.loads((out / "meta.json").read_text())
        assert meta["n_docs_kept"] == 2 and meta["data_hash"] == summary.data_hash
        assert "FRAGMENTS" in meta["notes"][0]

    def test_convert_time_window_and_eastbound(self, tmp_path: Path) -> None:
        path = _mongo_export(tmp_path)
        s = convert_i24_to_parquet(
            path, tmp_path / "eb", direction=1, mm_range=(0.0, 1.0), t_range_s=(0.0, 1.0)
        )
        assert s.n_docs_kept == 1 and s.x_ref_ft == 0.0
        assert s.t_origin_unix == 1668600000.0  # detected origin recorded
        s_explicit = convert_i24_to_parquet(
            path, tmp_path / "eb2", direction=1, mm_range=(0.0, 1.0), t_origin_unix=1668599990.0
        )
        assert s_explicit.t_origin_unix == 1668599990.0  # explicit origin recorded
        assert load_i24_parquet(tmp_path / "eb2")["t"].iloc[0] == pytest.approx(10.0)
        assert load_i24_parquet(tmp_path / "eb", t_range_s=(0.0, 2.0))[
            "veh_id"
        ].unique().tolist() == ["E9"]
        # A window overlapping nothing keeps nothing (but still writes files).
        s2 = convert_i24_to_parquet(
            path, tmp_path / "none", direction=1, mm_range=(0.0, 1.0), t_range_s=(5000.0, 6000.0)
        )
        assert s2.n_docs_kept == 0 and s2.n_rows == 0
        assert load_i24_parquet(tmp_path / "none").empty

    def test_convert_rejects_bad_args(self, tmp_path: Path) -> None:
        path = _mongo_export(tmp_path)
        with pytest.raises(ValueError, match="direction"):
            convert_i24_to_parquet(path, tmp_path / "x", direction=0)
        with pytest.raises(ValueError, match="mm_range"):
            convert_i24_to_parquet(path, tmp_path / "x", mm_range=(2.0, 1.0))
        with pytest.raises(ValueError, match="t_range_s"):
            convert_i24_to_parquet(path, tmp_path / "x", t_range_s=(1.0, 1.0))

    def test_direction_filter(self) -> None:
        df = load_i24_trajectories(FIXTURES / "i24_tiny.json", direction=1)
        assert set(df["veh_id"]) == {"E9"}  # eastbound-only doc
        assert load_i24_episodes(FIXTURES / "i24_tiny.json", direction=1, min_duration_s=1.0) == []

    def test_missing_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            load_i24_trajectories(FIXTURES / "i24_tiny.json", direction=7)
