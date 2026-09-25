"""The per-vehicle table ``vehicles.parquet`` (WP-69; docs/CONTRACTS.md §3).

``run_micro`` writes one row per departed vehicle beside the trajectories:
route, origin, destination, departure, first and last trajectory row, arrival
and a weaving section's give-up. The unit tests pin the pure builder
(:func:`microsim.runner._vehicle_table`) and the trajectory writer's
first/last bookkeeping across row-group flushes; the integration tests run
the checked-in weaving fixture (``tests/fixtures/weave.osm``, 150 s) and the
ring and check the table against the route file the runner wrote and the
trajectories it wrote. That the table changes nothing else — config hash,
trajectories, metrics, goldens — is what ``test_microsim_golden.py`` and
``test_microsim_determinism.py`` pin, unchanged.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pytest

import microsim.runner as runner
from flowstate_core.config import ScenarioConfig
from microsim import load_scenario, run_micro
from microsim.runner import (
    _TRAJ_SCHEMA_BASE,
    _VEHICLES_SCHEMA,
    DESTINATION_CORRIDOR_END,
    ORIGIN_MAINLINE,
    VEHICLES_FILE,
    _TrajectoryWriter,
    _vehicle_table,
)
from validation.vehicles import read_vehicles

WEAVE_OSM = Path(__file__).resolve().parents[1] / "fixtures" / "weave.osm"


def _weave_cfg() -> ScenarioConfig:
    """The weaving fixture of ``test_microsim_determinism.py``: an entrance
    ("on", ramp 0) whose auxiliary lane feeds the exit ("off", ramp 1), both
    at edge 102; 30 % of the mainline vehicles exit."""
    return ScenarioConfig.model_validate(
        {
            "name": "weave_vehicle_table",
            "network": {
                "kind": "osm",
                "osm_file": str(WEAVE_OSM),
                "corridor_edges": ["100", "101", "102", "103", "104"],
                "inflow": [[0.0, 0.5]],
                "ramps": [
                    {
                        "kind": "on",
                        "name": "on",
                        "edges": ["200"],
                        "attach_edge": "102",
                        "inflow": [[0.0, 0.2]],
                        "merge": "weave",
                        "weave": {"exit_ramp": "off"},
                    },
                    {
                        "kind": "off",
                        "name": "off",
                        "edges": ["201"],
                        "attach_edge": "102",
                        "exit_fraction": [[0.0, 0.3]],
                    },
                ],
            },
            "sim": {"duration_s": 150.0},
        }
    )


def _routes_file(run_dir: Path) -> tuple[dict[str, str], dict[str, list[str]], dict[str, float]]:
    """The route file the runner wrote: vehicle → route id, route id → edges,
    vehicle → planned departure."""
    root = ET.parse(run_dir / "net" / "demand.rou.xml").getroot()
    edges = {r.get("id", ""): r.get("edges", "").split() for r in root.iter("route")}
    route = {v.get("id", ""): v.get("route", "") for v in root.iter("vehicle")}
    depart = {v.get("id", ""): float(v.get("depart", "nan")) for v in root.iter("vehicle")}
    return route, edges, depart


def _first_last(traj: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Each vehicle's first and last trajectory row (the file is in time order)."""
    cols = ["t", "x", "lane"]
    return traj.groupby("veh_id")[cols].first(), traj.groupby("veh_id")[cols].last()


class TestVehicleTableBuilder:
    """:func:`_vehicle_table` on hand-written inputs (no SUMO)."""

    LABELS = ("th61", "exit18207912")  # ramp 0 on-ramp, ramp 1 off-ramp

    def _args(self) -> dict[str, Any]:
        return {
            "depart_s": {"v00003": 4.5, "v00000": 0.5, "v00001": 1.0, "v00002": 2.0},
            "route_by_id": {
                "v00000": "main",
                "v00001": "main_off1",
                "v00002": "on0",
                "v00003": "on0_off1",
                "v00004": "main",  # planned, never departed: no row
            },
            "depart_planned_s": {
                "v00000": 0.4,
                "v00001": 0.9,
                "v00002": 1.7,
                "v00003": 4.2,
                "v00004": 9.0,
            },
            "ramp_labels": list(self.LABELS),
            "first_sample": {
                "v00000": (1.0, 5.1, 0),
                "v00001": (1.5, 5.1, 1),
                "v00002": (40.0, 990.0, 0),
            },
            "last_sample": {
                "v00000": (90.0, 2220.0, 1),
                "v00001": (60.0, 2221.0, 0),
                "v00002": (100.0, 1500.0, 2),
            },
            "running": {"v00002", "v00003"},
            "gave_up_s": {"v00001": 44.5},
        }

    def _table(self) -> pd.DataFrame:
        return _vehicle_table(**self._args()).to_pandas().set_index("veh_id")

    def test_schema_and_one_row_per_departed_vehicle_in_id_order(self) -> None:
        table = _vehicle_table(**self._args())
        assert table.schema == pa.schema(_VEHICLES_SCHEMA)
        # rows in veh_id order, whatever order the departures were recorded in;
        # the planned vehicle that never departed has none
        assert table.column("veh_id").to_pylist() == ["v00000", "v00001", "v00002", "v00003"]

    def test_origins_and_destinations_follow_the_route_ids(self) -> None:
        df = self._table()
        got = df[["route", "origin", "origin_ramp", "destination", "destination_ramp"]]
        assert got.to_dict("index") == {
            "v00000": {
                "route": "main",
                "origin": ORIGIN_MAINLINE,
                "origin_ramp": -1,
                "destination": DESTINATION_CORRIDOR_END,
                "destination_ramp": -1,
            },
            "v00001": {
                "route": "main_off1",
                "origin": ORIGIN_MAINLINE,
                "origin_ramp": -1,
                "destination": "exit18207912",
                "destination_ramp": 1,
            },
            "v00002": {
                "route": "on0",
                "origin": "th61",
                "origin_ramp": 0,
                "destination": DESTINATION_CORRIDOR_END,
                "destination_ramp": -1,
            },
            "v00003": {
                "route": "on0_off1",
                "origin": "th61",
                "origin_ramp": 0,
                "destination": "exit18207912",
                "destination_ramp": 1,
            },
        }

    def test_a_given_up_exiter_keeps_its_original_destination_beside_the_rerouted_one(
        self,
    ) -> None:
        df = self._table()
        row = df.loc["v00001"]
        assert row["destination"] == "exit18207912" and row["destination_ramp"] == 1
        assert bool(row["gave_up"]) and row["gave_up_s"] == 44.5
        assert row["destination_final"] == DESTINATION_CORRIDOR_END
        # every other vehicle drove to its planned destination
        rest = df.drop(index="v00001")
        assert not rest["gave_up"].any() and rest["gave_up_s"].isna().all()
        assert (rest["destination_final"] == rest["destination"]).all()

    def test_samples_departures_and_arrival(self) -> None:
        df = self._table()
        assert df.loc["v00002", ["entry_t_s", "entry_x_m", "entry_lane"]].tolist() == [
            40.0,
            990.0,
            0,
        ]
        assert df.loc["v00000", ["last_t_s", "last_x_m", "last_lane"]].tolist() == [
            90.0,
            2220.0,
            1,
        ]
        # a vehicle with no trajectory row (still on its ramp) has null samples
        assert df.loc["v00003", ["entry_t_s", "entry_x_m", "last_t_s", "last_x_m"]].isna().all()
        assert df["depart_s"].to_dict() == {
            "v00000": 0.5,
            "v00001": 1.0,
            "v00002": 2.0,
            "v00003": 4.5,
        }
        assert df.loc["v00003", "depart_planned_s"] == 4.2
        assert df["arrived"].to_dict() == {
            "v00000": True,
            "v00001": True,
            "v00002": False,
            "v00003": False,
        }

    def test_no_departures_is_an_empty_contract_table(self) -> None:
        table = _vehicle_table({}, {}, {}, [], {}, {}, set(), {})
        assert table.num_rows == 0 and table.schema == pa.schema(_VEHICLES_SCHEMA)


class TestWriterFirstLast:
    """The trajectory writer keeps each vehicle's first and last row across
    row-group flushes, equal to the file's own first and last row."""

    def test_across_flushes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(runner, "TRAJ_FLUSH_ROWS", 4)  # several row groups
        path = tmp_path / "trajectories.parquet"
        writer = _TrajectoryWriter(path, is_ring=False)
        present = {0: ["a", "b"], 1: ["a", "b", "c"], 2: ["b", "c"], 3: ["c", "d"], 4: ["d"]}
        for k, ids in present.items():
            t = 0.5 * (k + 1)
            for n, vid in enumerate(ids):
                row = {name: False for name, _ in _TRAJ_SCHEMA_BASE}
                row.update(t=t, veh_id=vid, x=10.0 * k + n, lane=n, v=1.0, a=0.0)
                for name, value in row.items():
                    writer.cols[name].append(value)
            writer.maybe_flush()
        writer.close()
        with open(path, "rb") as f:
            traj = pd.read_parquet(f)
        first, last = _first_last(traj)
        assert writer.first_sample == {vid: (r.t, r.x, int(r.lane)) for vid, r in first.iterrows()}
        assert writer.last_sample == {vid: (r.t, r.x, int(r.lane)) for vid, r in last.iterrows()}
        assert writer.first_sample["c"] == (1.0, 12.0, 2)
        assert writer.last_sample["b"] == (1.5, 20.0, 0)


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The weaving fixture run twice (seed 5), its table, trajectories and meta."""
    root = tmp_path_factory.mktemp("vehicle_table")
    paths = run_micro(_weave_cfg(), 5, root / "a")
    again = run_micro(_weave_cfg(), 5, root / "b")
    with open(paths.trajectories, "rb") as f:
        traj = pd.read_parquet(f, columns=["t", "veh_id", "x", "lane"])
    return {
        "paths": paths,
        "again": again,
        "meta": json.loads(paths.meta.read_text()),
        "vehicles": read_vehicles(paths.run_dir).set_index("veh_id"),
        "traj": traj,
    }


@pytest.mark.integration
class TestWeaveFixtureRun:
    """The table of a real run on the weaving fixture, against the route file
    and the trajectories the same run wrote."""

    def test_every_departed_vehicle_has_one_row(self, run: dict[str, Any]) -> None:
        df, meta, traj = run["vehicles"], run["meta"], run["traj"]
        assert df.index.is_unique and list(df.index) == sorted(df.index)
        assert len(df) == meta["n_vehicles_departed"] > 0
        # every vehicle the trajectories saw departed; the rest never reached a corridor edge
        assert set(traj["veh_id"]) <= set(df.index)
        assert int(df["arrived"].sum()) == meta["n_vehicles_arrived"]
        by_ramp = df[df["origin_ramp"] >= 0].groupby("origin_ramp").size()
        assert by_ramp.to_dict() == {
            r["index"]: r["n_departed"] for r in meta["ramps"] if r["n_departed"]
        }

    def test_origins_and_destinations_match_the_routes_the_runner_built(
        self, run: dict[str, Any]
    ) -> None:
        df = run["vehicles"]
        route, edges, depart = _routes_file(run["paths"].run_dir)
        assert set(df.index) <= set(route)
        assert (df["route"] == pd.Series(route).reindex(df.index)).all()
        for vid, row in df.iterrows():
            path = edges[row["route"]]
            if row["origin"] == "on":
                assert row["origin_ramp"] == 0 and path[0] == "200"
            else:
                assert row["origin"] == ORIGIN_MAINLINE and path[0] == "100", vid
            if row["destination"] == "off":
                assert row["destination_ramp"] == 1 and path[-1] == "201"
            else:
                assert row["destination"] == DESTINATION_CORRIDOR_END and path[-1] == "104"
            # the plan's departure as written (3 decimals); SUMO departs on the step grid
            assert row["depart_planned_s"] == pytest.approx(depart[vid], abs=5e-4)
            assert row["depart_s"] >= depart[vid] - 5e-4
        # every movement of the fixture occurs
        assert set(df["route"]) == {"main", "main_off1", "on0", "on0_off1"}

    def test_corridor_entry_and_last_sighting_are_the_trajectory_rows(
        self, run: dict[str, Any]
    ) -> None:
        df, traj, meta = run["vehicles"], run["traj"], run["meta"]
        first, last = _first_last(traj)
        seen = df[df["entry_t_s"].notna()]
        assert set(seen.index) == set(first.index)
        pd.testing.assert_frame_equal(
            seen[["entry_t_s", "entry_x_m", "entry_lane"]]
            .set_axis(["t", "x", "lane"], axis=1)
            .astype({"lane": "int32"}),
            first.loc[seen.index].astype({"lane": "int32"}),
            check_names=False,
        )
        pd.testing.assert_frame_equal(
            seen[["last_t_s", "last_x_m", "last_lane"]]
            .set_axis(["t", "x", "lane"], axis=1)
            .astype({"lane": "int32"}),
            last.loc[seen.index].astype({"lane": "int32"}),
            check_names=False,
        )
        assert (seen["entry_t_s"] > seen["depart_s"]).all()
        # an entrant first appears on the attach edge; an exiter that took the
        # exit was last seen on it (meta.json ramps: attach_x_m, attach_end_x_m)
        on, off = meta["ramps"]
        entrants = seen[seen["origin"] == "on"]
        assert len(entrants) > 0
        assert entrants["entry_x_m"].between(on["attach_x_m"], on["attach_end_x_m"]).all()
        exited = seen[(seen["destination_final"] == "off") & seen["arrived"]]
        assert len(exited) > 0
        assert exited["last_x_m"].between(off["attach_x_m"], off["attach_end_x_m"]).all()

    def test_ramp_records_carry_the_attach_edge_x(self, run: dict[str, Any]) -> None:
        on, off = run["meta"]["ramps"]
        assert on["attach_edge"] == off["attach_edge"] == "102"
        assert 0.0 < on["attach_x_m"] < on["attach_end_x_m"]
        assert (off["attach_x_m"], off["attach_end_x_m"]) == (
            on["attach_x_m"],
            on["attach_end_x_m"],
        )

    def test_the_table_is_deterministic(self, run: dict[str, Any]) -> None:
        a = (run["paths"].run_dir / VEHICLES_FILE).read_bytes()
        b = (run["again"].run_dir / VEHICLES_FILE).read_bytes()
        assert a == b


@pytest.mark.integration
def test_a_given_up_exiter_through_the_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The give-up bookkeeping end to end. The small weaving fixtures give no
    exit up (``n_missed_exit`` = 0 on this one and on golden ``merge_weave``),
    so this test (and only it) gives one up itself:
    the first exit-bound vehicle seen upstream of the section from t = 10 s is
    rerouted through exactly as the weave's exit-side rule does it
    (``vehicle.changeTarget`` to ``ws["through_target"]``, added to
    ``ws["gave_up"]``). Its row keeps the planned exit and records the
    corridor's end beside it, with the step it was given up at."""
    real_step = runner._weave_step
    forced: dict[str, float] = {}

    def step(mod: Any, tc: Any, ws: dict[str, Any], results: Any, t: float) -> None:
        real_step(mod, tc, ws, results, t)
        if forced or t < 10.0:
            return
        for vid in sorted(ws["exiting_ids"]):
            if vid in results and results[vid][tc.VAR_ROAD_ID] in ("100", "101"):
                mod.vehicle.changeTarget(vid, ws["through_target"])
                ws["gave_up"].add(vid)
                forced[vid] = t
                return

    monkeypatch.setattr(runner, "_weave_step", step)
    paths = run_micro(_weave_cfg(), 5, tmp_path)
    df = read_vehicles(paths.run_dir).set_index("veh_id")
    meta = json.loads(paths.meta.read_text())
    ((vid, t_forced),) = forced.items()
    row = df.loc[vid]
    assert row["route"] == "main_off1"
    assert (row["destination"], row["destination_ramp"]) == ("off", 1)
    assert bool(row["gave_up"]) and row["gave_up_s"] == t_forced
    assert row["destination_final"] == DESTINATION_CORRIDOR_END
    # it drove past the gore to the corridor's end instead of taking the exit
    assert row["last_x_m"] > meta["ramps"][1]["attach_end_x_m"]
    assert bool(row["arrived"])
    assert int(df["gave_up"].sum()) == 1


@pytest.mark.integration
def test_ring_rows(tmp_path: Path) -> None:
    """On a ring every vehicle drives the loop ("main") and none arrives."""
    cfg = load_scenario("ring_sugiyama").model_copy(deep=True)
    cfg.sim.duration_s = 30.0
    paths = run_micro(cfg, 42, tmp_path)
    df = read_vehicles(paths.run_dir)
    meta = json.loads(paths.meta.read_text())
    assert len(df) == meta["n_vehicles_departed"] == 22
    assert set(df["route"]) == {"main"} and set(df["origin"]) == {ORIGIN_MAINLINE}
    assert not df["arrived"].any() and not df["gave_up"].any()
    assert df["entry_t_s"].notna().all() and meta["ramps"] is None
