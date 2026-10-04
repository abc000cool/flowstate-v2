"""The demand ledger ``journeys.parquet`` (WP-105; docs/FRISCO_PROTOCOL.md §8.2).

Pure helpers first (route geometry, the table, the meta totals: hand cases),
then short SUMO runs: the ALINEA-metered on-ramp of the golden case
``merge_meter_alinea`` against the same interchange unmetered (meter waiting
counted and positive under the meter, zero without), a deliberately saturated
short corridor (insertion backlog: vehicles never inserted are kept, censored
at the run's end and counted for their whole wait), and the ring (no named
route: no free-flow time, delay undefined). Every run is under a second.

The ledger is bookkeeping only: the goldens (``test_microsim_golden.py``) and
the byte-identical determinism tests are the evidence that no vehicle moves
differently.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from flowstate_core.config import ScenarioConfig
from microsim import load_scenario, run_micro
from microsim.runner import (
    JOURNEYS_FILE,
    RouteGeometry,
    _journey_table,
    _journeys_meta,
    _meter_wait_totals,
)
from tests.test_microsim.test_microsim_golden import ALINEA_METER, REPO_ROOT, _merge_config
from validation.metrics import compute_waiting_metrics, read_journeys
from validation.vehicles import read_vehicles

GEOM = RouteGeometry(("a", "b"), (100.0, 200.0), (20.0, 10.0))


class TestRouteGeometry:
    def test_length_offset_and_free_flow(self) -> None:
        assert GEOM.length_m == 300.0
        assert GEOM.offset_m("b", 30.0) == 130.0
        assert GEOM.offset_m("x", 1.0) is None
        # each edge at min(v0, limit): 100/15 + 200/10
        assert GEOM.free_flow_s(15.0) == pytest.approx(100.0 / 15.0 + 20.0)
        assert GEOM.free_flow_s(40.0) == pytest.approx(5.0 + 20.0)

    def test_free_flow_between_offsets(self) -> None:
        # 50 m of a at 15 m/s, then 50 m of b at 10 m/s
        assert GEOM.free_flow_between_s(50.0, 150.0, 15.0) == pytest.approx(50.0 / 15.0 + 5.0)
        assert GEOM.free_flow_between_s(-10.0, 400.0, 15.0) == pytest.approx(GEOM.free_flow_s(15.0))
        assert GEOM.free_flow_between_s(150.0, 50.0, 15.0) == 0.0


def _table(**over: Any) -> pd.DataFrame:
    args: dict[str, Any] = {
        "veh_ids": ["v1", "v2", "v3", "v4"],
        "route_by_id": {"v1": "r", "v2": "r", "v3": "r", "v4": "r"},
        "depart_planned_s": {"v1": 0.0, "v2": 5.0, "v3": 90.0, "v4": 150.0},
        "v0_by_id": {"v1": 15.0, "v2": 15.0, "v3": 15.0, "v4": 15.0},
        "geometry": {"r": GEOM},
        "depart_s": {"v1": 0.5, "v2": 5.5, "v3": 90.5},
        "insert_offset_m": {"v1": 5.0, "v2": 5.0, "v3": 5.0},
        "arrival_s": {"v1": 120.0},
        "distance_end_m": {"v2": 45.0, "v3": 60.0},
        "meter_ramp": {"v1": 0, "v2": 0},
        "meter_hold": {"v1": (10.0, 20.0), "v2": (100.0, 20.0)},
        "meter_release": {"v1": (70.0, 90.0)},
        "end_s": 160.0,
    }
    args.update(over)
    return _journey_table(**args).to_pandas().set_index("veh_id")


class TestJourneyTable:
    def test_hand_cases(self) -> None:
        t = _table()
        # v1: held 10 → 70 s while moving 20 → 90 m (70 m of edge a at 15 m/s)
        assert t.loc["v1", "meter_wait_s"] == pytest.approx(60.0 - 70.0 / 15.0)
        assert bool(t.loc["v1", "meter_released"]) and t.loc["v1", "meter_release_s"] == 70.0
        # arrived: covered from its insertion offset to the route's end
        assert t.loc["v1", "free_flow_covered_s"] == pytest.approx(
            GEOM.free_flow_between_s(5.0, 300.0, 15.0)
        )
        # v2: still held at the end, offset 5 + 45 = 50 m: censored at 160 s
        assert t.loc["v2", "meter_wait_s"] == pytest.approx(60.0 - 30.0 / 15.0)
        assert not bool(t.loc["v2", "meter_released"])
        assert t.loc["v2", "free_flow_covered_s"] == pytest.approx(45.0 / 15.0)
        # v3: running, never held
        assert t.loc["v3", "meter_ramp"] == -1 and t.loc["v3", "meter_wait_s"] == 0.0
        # v4: never inserted
        assert not bool(t.loc["v4", "inserted"]) and math.isnan(t.loc["v4", "depart_s"])
        assert t.loc["v4", "free_flow_covered_s"] == 0.0
        assert t["free_flow_s"].tolist() == [pytest.approx(GEOM.free_flow_s(15.0))] * 4

    def test_unknown_route_has_no_free_flow(self) -> None:
        t = _table(geometry={})
        assert t["free_flow_s"].isna().all() and t["route_length_m"].isna().all()
        assert t.loc[["v1", "v2", "v3"], "free_flow_covered_s"].isna().all()
        assert t.loc["v4", "free_flow_covered_s"] == 0.0  # never inserted: nothing covered
        # the hold time stands, with no free-flow part to subtract
        assert t.loc["v1", "meter_wait_s"] == pytest.approx(60.0)

    def test_meta_totals(self) -> None:
        args: dict[str, Any] = {
            "veh_ids": ["v1", "v2"],
            "route_by_id": {},
            "depart_planned_s": {"v1": 0.0, "v2": 100.0},
            "v0_by_id": {"v1": 15.0, "v2": 15.0},
            "geometry": {},
            "depart_s": {"v1": 2.0},
            "insert_offset_m": {"v1": 0.0},
            "arrival_s": {},
            "distance_end_m": {"v1": 10.0},
            "meter_ramp": {"v1": 1},
            "meter_hold": {"v1": (3.0, None)},
            "meter_release": {},
            "end_s": 160.0,
        }
        table = _journey_table(**args)
        meta = _journeys_meta(table, 160.0, 1)
        assert meta["n_planned"] == 2 and meta["n_inserted"] == 1 and meta["n_not_inserted"] == 1
        assert meta["insertion_delay_s_total"] == pytest.approx(2.0 + 60.0)
        assert meta["meter_wait_s_total"] == pytest.approx(157.0)
        assert meta["n_meter_held"] == 1 and meta["n_meter_unreleased"] == 1
        assert meta["n_route_ids_without_geometry"] == 1
        assert _meter_wait_totals(table) == {1: pytest.approx(157.0)}


def _run(tmp_path: Path, cfg: ScenarioConfig, name: str) -> tuple[Path, dict[str, Any]]:
    paths = run_micro(cfg, cfg.seed, tmp_path / name)
    return paths.run_dir, json.loads(paths.meta.read_text())


@pytest.fixture(scope="module")
def merge_runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, dict[str, Any]]]:
    """The golden metering case and the same interchange unmetered (240 sim-s, seed 3)."""
    tmp = tmp_path_factory.mktemp("journeys")
    cwd = Path.cwd()
    os.chdir(REPO_ROOT)  # the merge fixture is named repo-relative (golden convention)
    try:
        return {
            "metered": _run(tmp, _merge_config("m", "lane_change", ALINEA_METER, 240.0), "m"),
            "unmetered": _run(tmp, _merge_config("u", "lane_change", None, 240.0), "u"),
        }
    finally:
        os.chdir(cwd)


@pytest.mark.integration
class TestMergeRuns:
    def test_meter_waiting_is_counted_under_the_meter_and_zero_without(
        self, merge_runs: dict[str, tuple[Path, dict[str, Any]]]
    ) -> None:
        run_m, meta_m = merge_runs["metered"]
        run_u, _ = merge_runs["unmetered"]
        jm, ju = read_journeys(run_m), read_journeys(run_u)
        held = jm[jm["meter_ramp"] >= 0]
        assert len(held) > 0 and (held["origin_ramp"] == 0).all()
        assert (held["meter_wait_s"] > 0.0).any()
        assert (jm["meter_wait_s"] >= -1e-6).all()
        assert (ju["meter_ramp"] == -1).all() and (ju["meter_wait_s"] == 0.0).all()
        wm = compute_waiting_metrics(run_m, warmup_s=0.0)
        wu = compute_waiting_metrics(run_u, warmup_s=0.0)
        assert wm.meter_wait_veh_h > 0.0 and wu.meter_wait_veh_h == 0.0
        # the meta's per-meter counters agree with the table
        meter = meta_m["ramp_meters"][0]
        assert meter["n_held"] == len(held)
        assert meter["n_unreleased"] == int((~held["meter_released"]).sum())
        assert meter["wait_s_total"] == pytest.approx(float(held["meter_wait_s"].sum()))
        assert meter["n_released"] == int(held["meter_released"].sum())
        assert meta_m["journeys"]["meter_wait_s_total"] == pytest.approx(meter["wait_s_total"])

    @pytest.mark.parametrize("arm", ["metered", "unmetered"])
    def test_the_ledger_agrees_with_the_runs_other_records(
        self, merge_runs: dict[str, tuple[Path, dict[str, Any]]], arm: str
    ) -> None:
        run, meta = merge_runs[arm]
        j = read_journeys(run).set_index("veh_id")
        v = read_vehicles(run).set_index("veh_id")
        assert len(j) == meta["n_vehicles_planned"] == meta["journeys"]["n_planned"]
        assert int(j["inserted"].sum()) == meta["n_vehicles_departed"]
        assert int(j["arrived"].sum()) == meta["n_vehicles_arrived"]
        assert sorted(j.index[j["inserted"]]) == sorted(v.index)
        np.testing.assert_array_equal(j.loc[v.index, "depart_s"], v["depart_s"])
        np.testing.assert_array_equal(j.loc[v.index, "depart_planned_s"], v["depart_planned_s"])
        np.testing.assert_array_equal(j.loc[v.index, "arrived"], v["arrived"])
        assert j["free_flow_s"].notna().all() and j["insert_offset_m"].notna().all()
        arrived = j[j["arrived"]]
        assert (arrived["arrival_s"] >= arrived["depart_s"]).all()
        in_system = arrived["arrival_s"] - arrived["depart_planned_s"]
        assert (in_system >= arrived["free_flow_covered_s"] - 1e-6).all()
        running = j[j["inserted"] & ~j["arrived"]]
        assert running["distance_end_m"].notna().all()
        assert j.loc[j["arrived"], "distance_end_m"].isna().all()
        assert meta["journeys"]["end_s"] == meta["config"]["sim"]["duration_s"]


@pytest.mark.integration
def test_insertion_backlog_is_counted_and_censored(tmp_path: Path) -> None:
    """A 500 m single-lane corridor offered 1.2 veh/s cannot insert it all."""
    cfg = ScenarioConfig.model_validate(
        {
            "name": "saturated_entry",
            "network": {"kind": "corridor", "length_m": 500.0, "lanes": 1, "inflow": [[0.0, 1.2]]},
            "sim": {"duration_s": 120.0},
            "seed": 7,
        }
    )
    run, meta = _run(tmp_path, cfg, "sat")
    j = read_journeys(run)
    never = j[~j["inserted"]]
    assert len(never) > 0
    assert never["depart_s"].isna().all() and (never["free_flow_covered_s"] == 0.0).all()
    n_never = meta["n_vehicles_planned"] - meta["n_vehicles_departed"]
    assert meta["journeys"]["n_not_inserted"] == len(never) == n_never
    end = meta["journeys"]["end_s"]
    w = compute_waiting_metrics(run, warmup_s=0.0)
    in_window = j[j["depart_planned_s"] < end]
    expected = float((in_window["depart_s"].fillna(end) - in_window["depart_planned_s"]).sum())
    assert w.insertion_delay_veh_h * 3600.0 == pytest.approx(expected)
    assert w.n_not_inserted == len(never) and w.n_censored >= w.n_not_inserted
    # a never-inserted vehicle's whole wait is delay (nothing covered)
    assert w.total_delay_incl_waiting_veh_h * 3600.0 >= float(
        (end - never["depart_planned_s"]).sum()
    )
    assert w.insertion_delay_veh_h > 0.0


@pytest.mark.integration
def test_the_ring_has_no_named_route(tmp_path: Path) -> None:
    cfg = load_scenario("ring_sugiyama").model_copy(deep=True)
    cfg.sim.duration_s = 60.0
    run, meta = _run(tmp_path, cfg, "ring")
    assert meta["journeys"]["n_route_ids_without_geometry"] == 1
    assert (run / JOURNEYS_FILE).is_file()
    w = compute_waiting_metrics(run, warmup_s=0.0)
    assert math.isnan(w.total_delay_incl_waiting_veh_h)
    assert w.n_censored == w.n_demand_veh == 22  # nobody arrives on a ring
