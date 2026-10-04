"""Travel time and delay including waiting (WP-105; docs/FRISCO_PROTOCOL.md §8.2).

The definitions on ``validation.metrics.WaitingMetrics`` checked against a
hand-computed journeys table: the demand of the measurement window (planned
departure in ``[t_lo, t_end)``), the censoring of vehicles not arrived by the
run's end (their clocks stop at ``t_end``; a vehicle never inserted counts its
whole wait), the free-flow subtraction, the completable cohort of the travel
times, and the refusal of inconsistent ledgers. No simulation runs here.
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from microsim import runner
from validation.metrics import (
    JOURNEY_COLUMNS,
    JOURNEYS_FILE,
    WAITING_FIELDS,
    Metrics,
    WaitingMetrics,
    compute_waiting_metrics,
    read_journeys,
    waiting_metrics,
)

T_LO = 100.0
T_END = 400.0
H = 3600.0


def _row(vid: str, planned: float, **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "veh_id": vid,
        "route": "main",
        "origin_ramp": -1,
        "depart_planned_s": planned,
        "route_length_m": 2000.0,
        "free_flow_s": 80.0,
        "inserted": True,
        "depart_s": planned,
        "insert_offset_m": 5.0,
        "arrived": False,
        "arrival_s": np.nan,
        "distance_end_m": np.nan,
        "free_flow_covered_s": 0.0,
        "meter_ramp": -1,
        "meter_hold_start_s": np.nan,
        "meter_released": False,
        "meter_release_s": np.nan,
        "meter_wait_s": 0.0,
    }
    row.update(kw)
    return row


def hand_table() -> pd.DataFrame:
    """Six vehicles; the window [100, 400) holds v1..v4 (module docstring).

    =====  =======  ========  =======  ===========  =====  ======  ======
    veh    planned  inserted  arrived  covered ff   meter  T_i     delay
    =====  =======  ========  =======  ===========  =====  ======  ======
    v0     50       (warm-up: not in the demand)
    v1     110      110.5     210.5    80           0      100.5   20.5
    v2     150      160       300      78           30     150     72
    v3     300      305       —        40           0      100     60
    v4     350      never     —        0            0      50      50
    v5     400      (at the run's end: not in the demand)
    =====  =======  ========  =======  ===========  =====  ======  ======
    """
    rows = [
        _row("v0", 50.0, arrived=True, arrival_s=140.0, free_flow_covered_s=80.0),
        _row(
            "v1",
            110.0,
            depart_s=110.5,
            arrived=True,
            arrival_s=210.5,
            free_flow_covered_s=80.0,
        ),
        _row(
            "v2",
            150.0,
            origin_ramp=0,
            route="on0",
            depart_s=160.0,
            arrived=True,
            arrival_s=300.0,
            free_flow_covered_s=78.0,
            meter_ramp=0,
            meter_hold_start_s=160.5,
            meter_released=True,
            meter_release_s=200.0,
            meter_wait_s=30.0,
        ),
        _row("v3", 300.0, depart_s=305.0, distance_end_m=990.0, free_flow_covered_s=40.0),
        _row("v4", 350.0, inserted=False, depart_s=np.nan, insert_offset_m=np.nan),
        _row("v5", 400.0, inserted=False, depart_s=np.nan, insert_offset_m=np.nan),
    ]
    return pd.DataFrame(rows, columns=list(JOURNEY_COLUMNS))


class TestDefinitions:
    def test_hand_computed_table(self) -> None:
        w = waiting_metrics(hand_table(), t_lo=T_LO, t_end=T_END)
        assert w.n_demand_veh == 4
        assert w.n_not_inserted == 1
        assert w.n_censored == 2  # v3 running, v4 never inserted
        assert w.insertion_delay_veh_h == pytest.approx((0.5 + 10.0 + 5.0 + 50.0) / H)
        assert w.meter_wait_veh_h == pytest.approx(30.0 / H)
        assert w.total_delay_incl_waiting_veh_h == pytest.approx((20.5 + 72.0 + 60.0 + 50.0) / H)
        # completable: planned + 80 s <= 400 s → v1, v2, v3 (v4 could not finish)
        assert w.n_tt_incl_waiting_veh == 3
        assert w.n_tt_censored == 1
        assert w.mean_tt_incl_waiting_s == pytest.approx((100.5 + 150.0 + 100.0) / 3.0)
        # sorted [100, 100.5, 150]: position 0.9·2 = 1.8 → 100.5 + 0.8·49.5
        assert w.p90_tt_incl_waiting_s == pytest.approx(100.5 + 0.8 * 49.5)

    def test_a_never_inserted_vehicle_counts_its_whole_wait(self) -> None:
        table = hand_table()
        w = waiting_metrics(table.loc[table["veh_id"] == "v4"], t_lo=T_LO, t_end=T_END)
        assert w.n_censored == 1 and w.n_not_inserted == 1
        assert w.insertion_delay_veh_h == pytest.approx(50.0 / H)
        assert w.total_delay_incl_waiting_veh_h == pytest.approx(50.0 / H)

    def test_the_warm_up_and_the_run_end_bound_the_demand(self) -> None:
        w = waiting_metrics(hand_table(), t_lo=0.0, t_end=T_END)
        assert w.n_demand_veh == 5  # v0 joins; v5 (planned at the end) never does
        assert w.total_delay_incl_waiting_veh_h == pytest.approx(
            (20.5 + 72.0 + 60.0 + 50.0 + (90.0 - 80.0)) / H
        )

    def test_row_order_does_not_matter(self) -> None:
        table = hand_table()
        a = waiting_metrics(table, t_lo=T_LO, t_end=T_END)
        b = waiting_metrics(table.iloc[::-1].reset_index(drop=True), t_lo=T_LO, t_end=T_END)
        assert a == b

    def test_unknown_geometry_leaves_delay_and_travel_time_undefined(self) -> None:
        table = hand_table()
        table.loc[table["veh_id"] == "v2", "free_flow_covered_s"] = np.nan
        w = waiting_metrics(table, t_lo=T_LO, t_end=T_END)
        assert math.isnan(w.total_delay_incl_waiting_veh_h)
        assert math.isnan(w.mean_tt_incl_waiting_s) and math.isnan(w.p90_tt_incl_waiting_s)
        assert w.insertion_delay_veh_h == pytest.approx(65.5 / H)  # needs no geometry
        # ... but a vehicle outside the window does not matter
        table = hand_table()
        table.loc[table["veh_id"] == "v0", "free_flow_s"] = np.nan
        assert math.isfinite(
            waiting_metrics(table, t_lo=T_LO, t_end=T_END).total_delay_incl_waiting_veh_h
        )

    def test_fewer_than_two_completable_vehicles_give_no_travel_time(self) -> None:
        table = hand_table()
        one = table.loc[table["veh_id"].isin(["v1", "v4"])]
        w = waiting_metrics(one, t_lo=T_LO, t_end=T_END)
        assert w.n_tt_incl_waiting_veh == 1
        assert math.isnan(w.mean_tt_incl_waiting_s)

    @pytest.mark.parametrize(("t_lo", "t_end"), [(400.0, 400.0), (500.0, 400.0), (math.nan, 400.0)])
    def test_an_empty_window_is_refused(self, t_lo: float, t_end: float) -> None:
        with pytest.raises(ValueError, match="t_lo < t_end"):
            waiting_metrics(hand_table(), t_lo=t_lo, t_end=t_end)

    def test_an_arrival_after_the_end_is_refused(self) -> None:
        table = hand_table()
        table.loc[table["veh_id"] == "v1", "arrival_s"] = 401.0
        with pytest.raises(ValueError, match="after the run's end"):
            waiting_metrics(table, t_lo=T_LO, t_end=T_END)

    def test_a_missing_column_is_refused(self) -> None:
        with pytest.raises(ValueError, match="meter_wait_s"):
            waiting_metrics(hand_table().drop(columns="meter_wait_s"), t_lo=T_LO, t_end=T_END)


class TestContract:
    def test_columns_and_file_name_match_the_writer(self) -> None:
        assert [name for name, _ in runner._JOURNEYS_SCHEMA] == list(JOURNEY_COLUMNS)
        assert runner.JOURNEYS_FILE == JOURNEYS_FILE

    def test_the_standard_metric_set_is_unchanged(self) -> None:
        # the goldens compare Metrics' key set: the waiting measures live apart
        assert not set(WAITING_FIELDS) & {f.name for f in dataclasses.fields(Metrics)}
        assert WAITING_FIELDS == tuple(f.name for f in dataclasses.fields(WaitingMetrics))


def _run_dir(tmp_path: Path, *, warmup_s: float, journeys: bool = True, end: bool = True) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    meta: dict[str, Any] = {"config": {"sim": {"warmup_s": warmup_s}}}
    if end:
        meta["journeys"] = {"file": JOURNEYS_FILE, "end_s": T_END}
    (run / "meta.json").write_text(json.dumps(meta))
    if journeys:
        hand_table().to_parquet(run / JOURNEYS_FILE)
    return run


class TestRunDirectory:
    def test_the_window_is_the_runs_warm_up_to_its_end(self, tmp_path: Path) -> None:
        run = _run_dir(tmp_path, warmup_s=T_LO)
        assert compute_waiting_metrics(run) == waiting_metrics(hand_table(), t_lo=T_LO, t_end=T_END)
        assert compute_waiting_metrics(run, warmup_s=0.0).n_demand_veh == 5
        assert list(read_journeys(run).columns) == list(JOURNEY_COLUMNS)

    def test_a_run_before_the_ledger_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="predates the demand ledger"):
            compute_waiting_metrics(_run_dir(tmp_path, warmup_s=0.0, journeys=False))

    def test_a_meta_without_the_end_time_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match=r"journeys\.end_s"):
            compute_waiting_metrics(_run_dir(tmp_path, warmup_s=0.0, end=False))

    def test_a_negative_warm_up_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="warmup_s"):
            compute_waiting_metrics(_run_dir(tmp_path, warmup_s=0.0), warmup_s=-1.0)
