"""The per-vehicle table reader :mod:`validation.vehicles` (WP-69).

No SUMO: the table is built by the runner's own pure builder
(``microsim.runner._vehicle_table``) and written with its writer, which is
the file a run leaves. The battery's trajectory pruning is checked to keep
the table for every seed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pyarrow as pa
import pytest

import microsim.runner as runner
from validation.vehicles import (
    DESTINATION_CORRIDOR_END,
    ORIGIN_MAINLINE,
    VEHICLE_COLUMNS,
    VEHICLES_FILE,
    read_vehicles,
    vehicles_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_table(run_dir: Path) -> Path:
    """A three-vehicle table: a mainline through vehicle, a given-up exiter,
    and an entrant still on its ramp (no trajectory row)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    table = runner._vehicle_table(
        depart_s={"v00000": 0.5, "v00001": 1.0, "v00002": 3.0},
        route_by_id={"v00000": "main", "v00001": "main_off1", "v00002": "on0"},
        depart_planned_s={"v00000": 0.4, "v00001": 0.8, "v00002": 2.6},
        ramp_labels=["th61", "exit18207912"],
        first_sample={"v00000": (1.0, 5.1, 0), "v00001": (1.5, 5.1, 2)},
        last_sample={"v00000": (95.0, 2220.0, 1), "v00001": (99.0, 2221.0, 0)},
        running={"v00002"},
        gave_up_s={"v00001": 44.5},
    )
    path = run_dir / runner.VEHICLES_FILE
    runner._write_parquet(table, path)
    return path


class TestContract:
    def test_reader_and_writer_agree(self) -> None:
        assert [name for name, _ in runner._VEHICLES_SCHEMA] == list(VEHICLE_COLUMNS)
        assert runner.VEHICLES_FILE == VEHICLES_FILE
        assert runner.ORIGIN_MAINLINE == ORIGIN_MAINLINE
        assert runner.DESTINATION_CORRIDOR_END == DESTINATION_CORRIDOR_END

    def test_path_beside_the_trajectories(self, tmp_path: Path) -> None:
        assert vehicles_path(tmp_path) == tmp_path / "vehicles.parquet"


class TestReadVehicles:
    def test_round_trip(self, tmp_path: Path) -> None:
        _write_table(tmp_path)
        df = read_vehicles(tmp_path)
        assert list(df.columns) == list(VEHICLE_COLUMNS)
        assert df["veh_id"].tolist() == ["v00000", "v00001", "v00002"]
        # integer columns stay integers where a row has no sample
        for col in ("origin_ramp", "destination_ramp", "entry_lane", "last_lane"):
            assert df[col].dtype == pd.Int32Dtype()
        assert df["entry_lane"].tolist()[:2] == [0, 2] and df["entry_lane"].isna().tolist()[2]
        exiter = df.set_index("veh_id").loc["v00001"]
        assert (exiter["destination"], exiter["destination_final"]) == (
            "exit18207912",
            DESTINATION_CORRIDOR_END,
        )
        assert bool(exiter["gave_up"]) and exiter["gave_up_s"] == 44.5
        assert df.set_index("veh_id").loc["v00002", "origin"] == "th61"
        assert df["arrived"].tolist() == [True, True, False]

    def test_column_subset(self, tmp_path: Path) -> None:
        _write_table(tmp_path)
        df = read_vehicles(tmp_path, ["veh_id", "origin", "destination"])
        assert list(df.columns) == ["veh_id", "origin", "destination"]
        assert df["origin"].tolist() == [ORIGIN_MAINLINE, ORIGIN_MAINLINE, "th61"]

    def test_a_run_without_the_table(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="before 2026-09-25"):
            read_vehicles(tmp_path)

    def test_an_unknown_column(self, tmp_path: Path) -> None:
        _write_table(tmp_path)
        with pytest.raises(ValueError, match="route_id"):
            read_vehicles(tmp_path, ["veh_id", "route_id"])

    def test_a_file_lacking_a_column(self, tmp_path: Path) -> None:
        table = pa.table({"veh_id": ["v00000"], "origin": [ORIGIN_MAINLINE]})
        runner._write_parquet(table, tmp_path / VEHICLES_FILE)
        with pytest.raises(ValueError, match="lacks the columns"):
            read_vehicles(tmp_path)
        assert read_vehicles(tmp_path, ["veh_id", "origin"])["origin"].tolist() == [ORIGIN_MAINLINE]


def _load_battery() -> ModuleType:
    """Import ``scripts/corridor_battery.py`` by path (``scripts/`` is not a package)."""
    path = REPO_ROOT / "scripts" / "corridor_battery.py"
    spec = importlib.util.spec_from_file_location("_corridor_battery_vehicles", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_battery_pruning_keeps_every_seeds_vehicle_table(tmp_path: Path) -> None:
    """``--keep-trajectories`` off deletes the trajectories of every seed but
    the first; the per-vehicle table (a few MB for a four-hour run, against a
    GB of trajectories) stays for every seed."""
    cli = _load_battery()
    dirs = []
    for seed in (1, 2, 3):
        run_dir = tmp_path / str(seed)
        _write_table(run_dir)
        (run_dir / "trajectories.parquet").write_bytes(b"x")
        dirs.append(run_dir)
    assert cli.prune_trajectories(dirs) == 2
    assert [(d / "trajectories.parquet").is_file() for d in dirs] == [True, False, False]
    assert all(vehicles_path(d).is_file() for d in dirs)
    assert all(len(read_vehicles(d)) == 3 for d in dirs)


def test_lane_end_give_ups_record_the_destination_driven_to(tmp_path: Path) -> None:
    """The lane-end give-up (WP-71, ``OSMNetwork.lane_end_giveup_m``) marks its
    vehicles as a weaving section's give-up is marked: ``gave_up``, the step in
    ``gave_up_s``, the planned destination kept. ``destination_final`` is the
    destination driven to: the exit for a vehicle bound on that took it from
    the end of an exit-only lane, the corridor's end for an exiter that gave
    its exit up at the end of a through lane. A weaving section's give-up,
    absent from the mapping, drives to the corridor's end as before."""
    table = runner._vehicle_table(
        depart_s={"v00000": 0.5, "v00001": 1.0, "v00002": 3.0, "v00003": 4.0},
        route_by_id={
            "v00000": "on0",
            "v00001": "main_off1",
            "v00002": "main_off1",
            "v00003": "on0",
        },
        depart_planned_s={"v00000": 0.4, "v00001": 0.8, "v00002": 2.6, "v00003": 3.9},
        ramp_labels=["th61", "exit18207912"],
        first_sample={},
        last_sample={},
        running=set(),
        gave_up_s={"v00000": 120.0, "v00001": 130.5, "v00002": 140.0},
        destination_final={"v00000": "exit18207912", "v00001": DESTINATION_CORRIDOR_END},
    )
    runner._write_parquet(table, tmp_path / VEHICLES_FILE)
    df = read_vehicles(tmp_path).set_index("veh_id")
    assert df["gave_up"].tolist() == [True, True, True, False]
    assert df["destination"].tolist() == [
        DESTINATION_CORRIDOR_END,
        "exit18207912",
        "exit18207912",
        DESTINATION_CORRIDOR_END,
    ]
    assert df["destination_final"].tolist() == [
        "exit18207912",
        DESTINATION_CORRIDOR_END,
        DESTINATION_CORRIDOR_END,
        DESTINATION_CORRIDOR_END,
    ]
    assert df.loc["v00000", "gave_up_s"] == 120.0 and pd.isna(df.loc["v00003", "gave_up_s"])
