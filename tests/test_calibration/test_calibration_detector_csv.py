"""Tidy detector CSV loader (``calibration.loaders.detector_csv``).

Covers the contract's frame shape: unit conversion, the column mapping, the
regular-interval rule (gaps are whole multiples, mixed intervals are refused),
missing-as-NaN, the round trip through :func:`write_detector_csv`, and the
per-lane FD table the ``detector_csv`` FD loader builds.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from calibration.loaders.detector_csv import (
    DETECTOR_COLUMNS,
    detector_interval_s,
    load_detector_csv,
    local_dates,
    local_seconds,
    to_fd_frame,
    write_detector_csv,
)

MPH_60_IN_MS = 26.8224
"""60 mph in m/s (exactly, from the sanctioned foot→metre constant)."""


def _rows(timestamps: list[str], **columns: object) -> pd.DataFrame:
    base = {"timestamp": timestamps, "station": ["S1"] * len(timestamps)}
    base.update(columns)  # type: ignore[arg-type]
    return pd.DataFrame(base)


def _write(tmp_path: Path, frame: pd.DataFrame, name: str = "det.csv") -> Path:
    path = tmp_path / name
    frame.to_csv(path, index=False)
    return path


class TestLoad:
    def test_tidy_columns_load_unchanged(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            _rows(
                ["2026-09-15T06:00:00-05:00", "2026-09-15T06:05:00-05:00"],
                flow_veh_h=[3600.0, 1800.0],
                occupancy_pct=[12.0, 6.0],
                speed_ms=[30.0, 20.0],
                lanes=[3, 3],
                kind=["mainline", "mainline"],
                x_m=[0.0, 0.0],
            ),
        )
        df = load_detector_csv(path)
        assert list(df.columns) == list(DETECTOR_COLUMNS)
        assert df["flow_veh_h"].tolist() == [3600.0, 1800.0]
        assert df["lanes"].tolist() == [3, 3]
        assert df.attrs["interval_s"] == 300.0

    def test_units_convert_and_missing_stays_nan(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            _rows(
                ["2026-09-15T06:00:00-05:00", "2026-09-15T06:05:00-05:00"],
                flow_veh_h=[1200.0, None],
                occupancy_pct=[0.12, 0.06],
                speed_ms=[60.0, None],
            ),
        )
        df = load_detector_csv(path, speed_unit="mph", occupancy_unit="fraction")
        assert df["speed_ms"][0] == pytest.approx(MPH_60_IN_MS)
        assert pd.isna(df["speed_ms"][1])
        assert pd.isna(df["flow_veh_h"][1])
        assert df["occupancy_pct"].tolist() == [12.0, 6.0]
        # Columns the file does not carry are NaN, not zero.
        assert df["x_m"].isna().all()
        assert (df["kind"] == "mainline").all()

    def test_column_map_and_kind_default(self, tmp_path: Path) -> None:
        frame = pd.DataFrame(
            {
                "when": ["2026-09-15T06:00:00-05:00", "2026-09-15T06:05:00-05:00"],
                "det": ["R7", "R7"],
                "volume": [100.0, 120.0],
            }
        )
        path = _write(tmp_path, frame)
        df = load_detector_csv(
            path,
            column_map={"timestamp": "when", "station": "det", "flow": "volume"},
            kind_default="on_ramp",
        )
        assert df["station"].tolist() == ["R7", "R7"]
        assert df["flow_veh_h"].tolist() == [100.0, 120.0]
        assert (df["kind"] == "on_ramp").all()

    def test_missing_required_column_names_it(self, tmp_path: Path) -> None:
        path = _write(tmp_path, pd.DataFrame({"timestamp": ["2026-09-15T06:00:00-05:00"]}))
        with pytest.raises(ValueError, match="station"):
            load_detector_csv(path)

    def test_unknown_column_map_field_is_refused(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            _rows(["2026-09-15T06:00:00-05:00"], flow_veh_h=[1.0]),
        )
        with pytest.raises(ValueError, match="unknown canonical fields"):
            load_detector_csv(path, column_map={"speeed": "v"})

    def test_unknown_kind_is_refused(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            _rows(["2026-09-15T06:00:00-05:00"], flow_veh_h=[1.0], kind=["shoulder"]),
        )
        with pytest.raises(ValueError, match="unknown kind"):
            load_detector_csv(path)

    def test_non_numeric_flow_names_the_column_without_echoing_the_cell(
        self, tmp_path: Path
    ) -> None:
        path = _write(
            tmp_path,
            _rows(
                ["2026-09-15T06:00:00-05:00", "2026-09-15T06:05:00-05:00"],
                flow_veh_h=["1,234", "5"],
            ),
        )
        with pytest.raises(ValueError) as excinfo:
            load_detector_csv(path)
        message = str(excinfo.value)
        assert "flow_veh_h" in message and "1,234" not in message


class TestInterval:
    def test_gaps_are_whole_multiples(self, tmp_path: Path) -> None:
        # 06:00, 06:05, then a jump to 07:00 (a span with unmeasured windows)
        # and the next day: all whole multiples of 300 s.
        path = _write(
            tmp_path,
            _rows(
                [
                    "2026-09-15T06:00:00-05:00",
                    "2026-09-15T06:05:00-05:00",
                    "2026-09-15T07:00:00-05:00",
                    "2026-09-16T06:00:00-05:00",
                ],
                flow_veh_h=[1.0, 2.0, 3.0, 4.0],
            ),
        )
        assert load_detector_csv(path).attrs["interval_s"] == 300.0

    def test_mixed_interval_is_refused(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            _rows(
                [
                    "2026-09-15T06:00:00-05:00",
                    "2026-09-15T06:05:00-05:00",
                    "2026-09-15T06:12:00-05:00",
                ],
                flow_veh_h=[1.0, 2.0, 3.0],
            ),
        )
        with pytest.raises(ValueError, match="irregular interval"):
            load_detector_csv(path)

    def test_single_timestamp_has_no_interval(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _rows(["2026-09-15T06:00:00-05:00"], flow_veh_h=[1.0]))
        assert detector_interval_s(load_detector_csv(path)) == 0.0


class TestLocalClock:
    def test_offsets_do_not_move_the_local_reading(self, tmp_path: Path) -> None:
        # The same local 06:00 in two different offsets stays 06:00 locally.
        path = _write(
            tmp_path,
            _rows(
                ["2026-09-15T06:00:00-05:00", "2026-11-15T06:00:00-06:00"],
                flow_veh_h=[1.0, 2.0],
            ),
        )
        df = load_detector_csv(path)
        assert local_seconds(df).tolist() == [6 * 3600.0, 6 * 3600.0]
        assert local_dates(df).tolist() == ["2026-09-15", "2026-11-15"]


class TestWriteRoundTrip:
    def test_round_trip_preserves_values_and_offsets(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            _rows(
                ["2026-09-15T06:00:00-05:00", "2026-09-15T06:05:00-05:00"],
                flow_veh_h=[3600.0, None],
                occupancy_pct=[12.0, None],
                speed_ms=[30.0, None],
                lanes=[3, 3],
                kind=["mainline", "mainline"],
                x_m=[0.0, 0.0],
            ),
        )
        first = load_detector_csv(path)
        again = load_detector_csv(write_detector_csv(first, tmp_path / "out.csv"))
        pd.testing.assert_frame_equal(first, again)


class TestFdFrame:
    def test_density_from_speed_then_occupancy_per_lane(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            pd.DataFrame(
                {
                    "timestamp": [
                        "2026-09-15T06:00:00-05:00",
                        "2026-09-15T06:05:00-05:00",
                        "2026-09-15T06:10:00-05:00",
                    ],
                    "station": ["S1", "S1", "R1"],
                    # 5400 veh/h over 3 lanes = 1800 veh/h/lane = 0.5 veh/s/lane.
                    "flow_veh_h": [5400.0, 5400.0, 900.0],
                    "occupancy_pct": [14.0, 14.0, 5.0],
                    "speed_ms": [25.0, None, 20.0],
                    "lanes": [3, 3, 1],
                    "kind": ["mainline", "mainline", "on_ramp"],
                }
            ),
        )
        fd = to_fd_frame(load_detector_csv(path), g_effective_length_m=7.0)
        # The ramp row is not part of the mainline fundamental diagram.
        assert len(fd) == 2
        assert fd["flow_veh_s"].tolist() == [0.5, 0.5]
        assert fd["density_veh_m"][0] == pytest.approx(0.5 / 25.0)
        assert fd["density_veh_m"][1] == pytest.approx(0.14 / 7.0)

    def test_no_usable_row_is_refused(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            _rows(["2026-09-15T06:00:00-05:00"], flow_veh_h=[1800.0], lanes=[0]),
        )
        with pytest.raises(ValueError, match="no usable row"):
            to_fd_frame(load_detector_csv(path))
