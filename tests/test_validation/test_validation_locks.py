"""Locks (permanent standstills) of a run: validation.locks and its readers (2026-10-07).

docs/I94_COLLAPSE_DIAGNOSIS.md found 3 of 20 replicates of a battery locked at
a weaving gore with nothing in the battery naming them. These tests pin the
detector on hand-built tables, without SUMO:

* the space-time reader (``edges.parquet``) on synthetic Edie fields: a
  planted lock is found with its head cell, section, onset, duration and
  queue; a near-lock that clears within the lock duration, a slow queue that
  still discharges, a single stopped vehicle and an empty road are not locks;
  a lock that clears after the duration is one, with its release;
* the run-end reader (``vehicles.parquet``) on a synthetic vehicle table: the
  front row, the last discharge as onset, the trapped vehicles; recent
  discharge past the head means no lock;
* both readers on one lock agree and merge; a run with neither file is not
  recorded;
* the run-set summary, the battery flag, the stored per-replicate record, the
  report (Model integrity, banner, criterion row, client summary) and the
  corridor battery artifact.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from scipy.stats import beta

from tests.test_validation import test_validation_corridor_battery_collisions as col
from tests.test_validation.test_validation_report import _limitations, _section, _write_run
from validation.battery import (
    METRICS_FILE,
    SCORES_FILE,
    insertion_stats,
    load_replicate_analysis,
    lock_free,
    weave_exit_summary,
)
from validation.criteria import NO_LOCKS, evaluate
from validation.locks import (
    LOCK_MIN_DURATION_S,
    NOT_RECORDED,
    SOURCE_EDGES,
    SOURCE_VEHICLES,
    STANDING_MAX_FLOW_VEH_S,
    Lock,
    RunLocks,
    corridor_sections,
    detect_locks,
    detect_run_locks,
    lock_flags,
    lock_summary,
    never_departed_upstream,
)
from validation.metrics import Metrics
from validation.observed import ObservedCorridor, ObservedScores
from validation.report import generate_report

#: Synthetic run: one hour, 5 km, the runner's 15 s × 100 m Edie bins.
DT = 15.0
DX = 100.0
END = 3600.0
NT = int(END / DT)
NX = 50

#: Free flow: 50 veh/km over all lanes at 25 m/s.
FREE_DENSITY = 0.05
FREE_SPEED = 25.0
#: A standing queue: 300 veh/km over all lanes (four lanes at ~13 m spacing).
JAM_DENSITY = 0.3

#: The planted lock's head cell, [3000, 3100) m, and its onset bin (t = 1200 s).
HEAD = 30
ONSET_K = 80
#: Speed at which the standing queue grows upstream [m/s] (the diagnosis read 2.5–3.6).
QUEUE_GROWTH_MS = 3.0


def _meta(**extra: Any) -> dict[str, Any]:
    """A run's metadata: duration, a weave whose gore is in the head cell, a merge downstream."""
    meta: dict[str, Any] = {
        "config": {"sim": {"duration_s": END, "warmup_s": 0.0, "output_hz": 2.0}},
        "output_hz_realized": 2.0,
        "n_vehicles_planned": 1000,
        "n_vehicles_departed": 890,
        "ramps": [
            {
                "index": 0,
                "name": "off A",
                "kind": "off",
                "attach_edge": "eA",
                "attach_x_m": 1800.0,
                "attach_end_x_m": 2000.0,
                "n_planned": 0,
                "n_departed": 0,
            },
            {
                "index": 1,
                "name": "W-ON",
                "kind": "on",
                "attach_edge": "eW",
                "attach_x_m": 2900.0,
                "attach_end_x_m": 3090.0,
                "n_planned": 100,
                "n_departed": 60,
            },
            {
                "index": 2,
                "name": "W-OFF",
                "kind": "off",
                "attach_edge": "eW",
                "attach_x_m": 2900.0,
                "attach_end_x_m": 3090.0,
                "n_planned": 0,
                "n_departed": 0,
            },
            {
                "index": 3,
                "name": "D-ON",
                "kind": "on",
                "attach_edge": "eD",
                "attach_x_m": 4000.0,
                "attach_end_x_m": 4250.0,
                "n_planned": 100,
                "n_departed": 90,
            },
        ],
        "merge_models": [
            {"ramp": "W-ON", "merge": "weave"},
            {"ramp": "D-ON", "merge": "scripted"},
        ],
        "weave_sections": [{"ramp": "W-ON", "exit": "W-OFF"}],
    }
    meta.update(extra)
    return meta


def _grid() -> tuple[np.ndarray, np.ndarray]:
    """``(density, flow)`` arrays ``[NT, NX]`` at free flow."""
    density = np.full((NT, NX), FREE_DENSITY)
    flow = density * FREE_SPEED
    return density, flow


def _frame(density: np.ndarray, flow: np.ndarray) -> pd.DataFrame:
    """The edges.parquet frame of a grid (bin centres, Edie columns)."""
    t = (np.arange(NT) + 0.5) * DT
    x = (np.arange(NX) + 0.5) * DX
    tt, xx = np.meshgrid(t, x, indexing="ij")
    with np.errstate(invalid="ignore", divide="ignore"):
        speed = np.where(density > 0, flow / np.where(density > 0, density, 1.0), np.nan)
    return pd.DataFrame(
        {
            "t_bin": tt.ravel(),
            "x_bin": xx.ravel(),
            "mean_speed": speed.ravel(),
            "density": density.ravel(),
            "flow": flow.ravel(),
        }
    )


def _plant_lock(
    density: np.ndarray, flow: np.ndarray, *, head: int = HEAD, k0: int = ONSET_K, k1: int = NT
) -> None:
    """A lock at ``head`` standing over bins ``[k0, k1)``: the queue grows upstream at
    :data:`QUEUE_GROWTH_MS`, the road downstream drains empty."""
    for j in range(head + 1):
        start = k0 + math.ceil(j * DX / QUEUE_GROWTH_MS / DT)
        if start < k1:
            density[start:k1, head - j] = JAM_DENSITY
            flow[start:k1, head - j] = 0.0
    density[k0 + 4 : k1, head + 1 :] = 0.0  # drained within a minute
    flow[k0 + 4 : k1, head + 1 :] = 0.0


def _locked_edges(**kw: Any) -> pd.DataFrame:
    density, flow = _grid()
    _plant_lock(density, flow, **kw)
    return _frame(density, flow)


class TestSpaceTimeReader:
    def test_a_planted_lock_is_found_where_and_when_it_formed(self) -> None:
        run = detect_locks(_meta(), edges=_locked_edges())
        assert run.sources == (SOURCE_EDGES,) and run.end_s == END and run.locked is True
        (lock,) = run.locks
        assert (lock.x_lo_m, lock.x_hi_m, lock.x_m) == (3000.0, 3100.0, 3050.0)
        assert lock.onset_s == ONSET_K * DT == 1200.0 and not lock.onset_is_upper_bound
        assert lock.release_s is None and lock.persists_to_end
        assert lock.duration_s == END - 1200.0 and lock.duration_is_lower_bound
        # the queue reached the corridor's entry: every cell from the head back
        assert lock.queue_m == 3100.0
        # the weave whose gore (3,090 m) is in the head cell, not its paired exit
        assert lock.section is not None
        assert (lock.section.name, lock.section.kind, lock.section.model) == (
            "W-ON",
            "weave",
            "weave",
        )
        assert lock.section.exit == "W-OFF" and lock.section.x_end_m == 3090.0
        assert lock.edge == "eW"
        assert lock.detected_by == (SOURCE_EDGES,)
        assert lock.n_trapped_in_network is None  # no vehicle table
        # never departed, origins at or upstream of the head: mainline
        # (1000 - 200) - (890 - 150) = 60, plus W-ON 40; D-ON (4,000 m) is downstream
        assert lock.n_never_departed_upstream == 100

    def test_free_flow_and_stop_and_go_are_not_locks(self) -> None:
        density, flow = _grid()
        # a stop-and-go wave: each cell stands for 45 s as it passes, moving upstream
        for j in range(NX):
            k = 40 + 2 * j
            density[k : k + 3, NX - 1 - j] = JAM_DENSITY
            flow[k : k + 3, NX - 1 - j] = 0.0
        run = detect_locks(_meta(), edges=_frame(density, flow))
        assert run.locked is False and run.locks == ()

    @pytest.mark.parametrize("bins", [1, 20, 39])
    def test_a_near_lock_that_clears_within_the_duration_is_not_a_lock(self, bins: int) -> None:
        # 39 bins = 585 s < 600 s; the head then discharges and the queue clears
        density, flow = _grid()
        _plant_lock(density, flow, k1=ONSET_K + bins)
        assert detect_locks(_meta(), edges=_frame(density, flow)).locked is False

    def test_the_lock_duration_is_the_boundary(self) -> None:
        bins = int(LOCK_MIN_DURATION_S / DT)  # 40 bins = 600 s exactly
        density, flow = _grid()
        _plant_lock(density, flow, k1=ONSET_K + bins)
        run = detect_locks(_meta(), edges=_frame(density, flow))
        (lock,) = run.locks
        assert lock.duration_s == LOCK_MIN_DURATION_S
        assert lock.release_s == 1200.0 + LOCK_MIN_DURATION_S

    def test_a_lock_that_clears_after_the_duration_is_one_with_its_release(self) -> None:
        density, flow = _grid()
        _plant_lock(density, flow, k1=ONSET_K + 80)  # 20 min, then discharge
        (lock,) = detect_locks(_meta(), edges=_frame(density, flow)).locks
        assert lock.onset_s == 1200.0 and lock.release_s == 2400.0
        assert not lock.persists_to_end and lock.duration_s == 1200.0
        assert not lock.duration_is_lower_bound
        # trapped and never-departed counts belong to locks standing at the end
        assert lock.n_never_departed_upstream is None and lock.n_trapped_in_network is None

    @pytest.mark.parametrize("speed_ms", [0.2, 0.05])
    def test_a_slow_queue_that_still_discharges_is_not_a_lock(self, speed_ms: float) -> None:
        # dense and crawling for 40 minutes, but discharging: 216 and 54 veh/h
        density, flow = _grid()
        density[ONSET_K:, 10:HEAD] = JAM_DENSITY
        flow[ONSET_K:, 10:HEAD] = JAM_DENSITY * speed_ms
        assert flow[ONSET_K, 10] > STANDING_MAX_FLOW_VEH_S
        assert detect_locks(_meta(), edges=_frame(density, flow)).locked is False

    def test_a_queue_under_the_flow_threshold_is_standing(self) -> None:
        density, flow = _grid()
        density[ONSET_K:, 10:HEAD] = JAM_DENSITY
        flow[ONSET_K:, 10:HEAD] = 0.8 * STANDING_MAX_FLOW_VEH_S
        (lock,) = detect_locks(_meta(), edges=_frame(density, flow)).locks
        assert (lock.x_lo_m, lock.x_hi_m) == (2900.0, 3000.0)

    def test_a_single_stopped_vehicle_on_an_empty_road_is_not_a_queue(self) -> None:
        density = np.zeros((NT, NX))
        flow = np.zeros((NT, NX))
        density[:, HEAD] = 0.01  # one vehicle in a 100 m cell, all run
        assert detect_locks(_meta(), edges=_frame(density, flow)).locked is False

    def test_a_head_that_moves_down_one_cell_is_one_lock(self) -> None:
        # the gore's cell fills two minutes after the cell behind it locked
        density, flow = _grid()
        _plant_lock(density, flow, head=HEAD - 1)
        density[ONSET_K + 8 :, HEAD] = JAM_DENSITY
        flow[ONSET_K + 8 :, HEAD] = 0.0
        density[ONSET_K + 8 :, HEAD + 1 :] = 0.0
        (lock,) = detect_locks(_meta(), edges=_frame(density, flow)).locks
        assert (lock.x_lo_m, lock.x_hi_m) == (3000.0, 3100.0)
        assert lock.onset_s == 1200.0  # the earlier head's onset

    def test_two_locks_far_apart_are_two(self) -> None:
        density, flow = _grid()
        _plant_lock(density, flow, head=HEAD, k1=ONSET_K + 60)  # clears at 2100 s
        density[ONSET_K + 60 :, : HEAD + 1] = FREE_DENSITY
        flow[ONSET_K + 60 :, : HEAD + 1] = FREE_DENSITY * FREE_SPEED
        _plant_lock(density, flow, head=45, k0=150)
        density[150 + 4 :, : HEAD - 10] = FREE_DENSITY  # its queue stays short
        flow[150 + 4 :, : HEAD - 10] = FREE_DENSITY * FREE_SPEED
        run = detect_locks(_meta(), edges=_frame(density, flow))
        assert [(lk.x_hi_m, lk.onset_s, lk.persists_to_end) for lk in run.locks] == [
            (4600.0, 2250.0, True),
            (3100.0, 1200.0, False),
        ]
        # no gore within reach of the downstream one (D-ON's is 250 m behind it)
        assert run.locks[0].section is None and run.locks[0].edge is None
        assert run.locks[1].section is not None and run.locks[1].section.name == "W-ON"


#: Vehicles of the synthetic run-end table: 40 standing in the queue behind the
#: head (front at 3,089.9 m), 5 of them bound for an exit upstream of it.
QUEUE = 40
FRONT_X = 3089.9


def _vehicles(*, last_crossing_s: float = 1500.0, discharging: bool = False) -> pd.DataFrame:
    """A run-end vehicle table with a lock at :data:`FRONT_X` (or, with
    ``discharging``, one vehicle from upstream past it at the run's end)."""
    rows: list[dict[str, Any]] = []
    # arrived through-traffic that passed the head before the lock
    for k in range(100):
        rows.append(
            {
                "entry_x_m": 5.0,
                "last_t_s": last_crossing_s - 9.0 * k,
                "last_x_m": 4995.0,
                "arrived": True,
                "origin_ramp": -1,
                "destination_final": "corridor_end",
            }
        )
    # the queue at the end
    for k in range(QUEUE):
        rows.append(
            {
                "entry_x_m": 5.0,
                "last_t_s": END,
                "last_x_m": FRONT_X - 7.5 * k,
                "arrived": False,
                "origin_ramp": -1,
                "destination_final": "off A" if k >= QUEUE - 5 else "corridor_end",
            }
        )
    # traffic entering downstream of the head, still in the network
    for k in range(20):
        rows.append(
            {
                "entry_x_m": 4010.0,
                "last_t_s": END,
                "last_x_m": 4100.0 + 40.0 * k,
                "arrived": False,
                "origin_ramp": 3,
                "destination_final": "corridor_end",
            }
        )
    # a vehicle on its on-ramp at the end, W-ON (2,900 m), bound past the head
    rows.append(
        {
            "entry_x_m": math.nan,
            "last_t_s": math.nan,
            "last_x_m": math.nan,
            "arrived": False,
            "origin_ramp": 1,
            "destination_final": "corridor_end",
        }
    )
    if discharging:
        rows.append(
            {
                "entry_x_m": 5.0,
                "last_t_s": END,
                "last_x_m": 3600.0,
                "arrived": False,
                "origin_ramp": -1,
                "destination_final": "corridor_end",
            }
        )
    return pd.DataFrame(rows)


class TestRunEndReader:
    def test_the_front_row_and_the_last_discharge(self) -> None:
        run = detect_locks(_meta(), vehicles=_vehicles())
        assert run.sources == (SOURCE_VEHICLES,) and run.locked is True
        (lock,) = run.locks
        assert lock.x_m == lock.x_lo_m == lock.x_hi_m == FRONT_X
        assert lock.section is not None and lock.section.name == "W-ON"
        assert lock.edge == "eW"
        # the latest last sample of a vehicle from upstream seen past the head
        assert lock.onset_s == 1500.0 and lock.onset_is_upper_bound
        assert lock.duration_s == END - 1500.0 and lock.duration_is_lower_bound
        assert lock.persists_to_end and lock.queue_m is None
        # 35 of the queue bound past the head, and the one still on W-ON
        assert lock.n_trapped_in_network == QUEUE - 5 + 1
        assert lock.n_never_departed_upstream == 100
        assert lock.detected_by == (SOURCE_VEHICLES,)

    def test_recent_discharge_past_the_head_is_not_a_lock(self) -> None:
        # the last vehicle passed 5 minutes before the end: under the duration
        recent = detect_locks(_meta(), vehicles=_vehicles(last_crossing_s=END - 300.0))
        # no lock found; read at the run's end only, "no lock" is not established
        assert recent.locks == () and recent.locked is None and recent.partially_recorded
        # a vehicle from upstream is past the head at the end: it is discharging
        assert detect_locks(_meta(), vehicles=_vehicles(discharging=True)).locks == ()

    def test_a_short_queue_is_not_a_candidate(self) -> None:
        table = _vehicles()
        queue = table.index[(~table["arrived"]) & (table["last_x_m"] < 3100.0)]
        table = table.drop(queue[9:])  # 9 vehicles left within 200 m: under 10
        assert detect_locks(_meta(), vehicles=table).locks == ()


class TestBothReaders:
    def test_one_lock_seen_by_both_is_merged(self) -> None:
        run = detect_locks(_meta(), edges=_locked_edges(), vehicles=_vehicles())
        assert run.sources == (SOURCE_EDGES, SOURCE_VEHICLES)
        (lock,) = run.locks
        assert lock.detected_by == (SOURCE_EDGES, SOURCE_VEHICLES)
        # where from the front vehicle, when from the space-time field
        assert lock.x_m == FRONT_X and lock.onset_s == 1200.0 and not lock.onset_is_upper_bound
        assert lock.queue_m == 3100.0 and lock.n_trapped_in_network == QUEUE - 5 + 1

    def test_a_space_time_lock_the_run_end_reader_cannot_confirm_still_counts(self) -> None:
        run = detect_locks(_meta(), edges=_locked_edges(), vehicles=_vehicles(discharging=True))
        (lock,) = run.locks
        assert lock.detected_by == (SOURCE_EDGES,)
        assert lock.n_trapped_in_network is not None

    def test_files_on_disk_and_a_run_without_them(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "meta.json").write_text(json.dumps(_meta()))
        assert detect_run_locks(run_dir) == NOT_RECORDED
        assert NOT_RECORDED.locked is None and NOT_RECORDED.to_dict()["n_locks"] is None
        _locked_edges().to_parquet(run_dir / "edges.parquet")
        _vehicles().to_parquet(run_dir / "vehicles.parquet")
        on_disk = detect_run_locks(run_dir)
        assert on_disk == detect_locks(_meta(), edges=_locked_edges(), vehicles=_vehicles())
        assert RunLocks.from_dict(json.loads(json.dumps(on_disk.to_dict()))) == on_disk


def _jam(density: np.ndarray, flow: np.ndarray, k0: int, cells: Any, k1: int = NT) -> None:
    """Cells ``cells`` stand (jam density, no flow) over bins ``[k0, k1)``."""
    density[k0:k1, cells] = JAM_DENSITY
    flow[k0:k1, cells] = 0.0


def _free(density: np.ndarray, flow: np.ndarray, cells: Any, k0: int = 0) -> None:
    """Cells ``cells`` at free flow from bin ``k0``."""
    density[k0:, cells] = FREE_DENSITY
    flow[k0:, cells] = FREE_DENSITY * FREE_SPEED


class TestOneQueueIsOneLock:
    """Review 2026-10-07, finding 3: heads were read only at the start of each cell's
    locked interval and merged pairwise, so one standing queue that froze in pieces
    was several locks (the I-24 sublane probes: 4 and 6 locks for one and two standing
    blocks), and a head cell that cleared while the queue behind it stood on read as a
    released lock, with the run-end reader's lock at the same place added again."""

    def test_a_queue_whose_standstill_runs_downstream_is_one_lock(self) -> None:
        # the queue's upstream part stands from 900 s; the standstill then runs
        # downstream a cell every two bins, each cell's downstream neighbour
        # starting to stand 30 s after it (the pushy0.5 probe: 1,400 m in 105 s)
        density, flow = _grid()
        _jam(density, flow, 60, slice(0, 20))
        for j in range(16):
            _jam(density, flow, 80 + 2 * j, 20 + j)
        density[120:, 36:] = flow[120:, 36:] = 0.0
        (lock,) = detect_locks(_meta(), edges=_frame(density, flow)).locks
        assert (lock.x_lo_m, lock.x_hi_m) == (3500.0, 3600.0)  # where the front ended
        assert lock.onset_s == 60 * DT and lock.persists_to_end
        assert lock.queue_m == 3600.0

    def test_a_piece_freezing_upstream_of_the_queue_tail_is_part_of_it(self) -> None:
        # a piece 400 m upstream of the growing queue's tail stands first and is
        # reached by the tail two minutes later (the sublane0.8 probe's 1,750 m piece)
        density, flow = _grid()
        _plant_lock(density, flow)
        _jam(density, flow, 105, slice(11, 13))
        (lock,) = detect_locks(_meta(), edges=_frame(density, flow)).locks
        assert (lock.x_lo_m, lock.x_hi_m, lock.onset_s) == (3000.0, 3100.0, 1200.0)
        assert lock.persists_to_end and lock.queue_m == 3100.0

    def test_a_head_cell_that_clears_while_the_queue_stands_on_is_not_a_release(self) -> None:
        density, flow = _grid()
        _plant_lock(density, flow)
        density[161:, HEAD] = flow[161:, HEAD] = 0.0  # the head cell empties at 2,415 s
        (lock,) = detect_locks(_meta(), edges=_frame(density, flow)).locks
        assert lock.release_s is None and lock.persists_to_end
        assert (lock.x_lo_m, lock.x_hi_m, lock.onset_s) == (3000.0, 3100.0, 1200.0)
        assert lock.n_never_departed_upstream == 100
        # ... and the run-end reader's lock there is the same lock, not a second one
        both = detect_locks(_meta(), edges=_frame(density, flow), vehicles=_vehicles())
        (lock,) = both.locks
        assert lock.detected_by == (SOURCE_EDGES, SOURCE_VEHICLES) and lock.x_m == FRONT_X

    def test_two_queues_apart_are_two_locks(self) -> None:
        density, flow = _grid()
        _plant_lock(density, flow, head=45)
        _free(density, flow, slice(0, 40))  # its queue stays 600 m long
        for j in range(11):  # a second queue behind a head at 3,100 m from 1,350 s
            _jam(density, flow, 90 + math.ceil(j * DX / QUEUE_GROWTH_MS / DT), HEAD - j)
        run = detect_locks(_meta(), edges=_frame(density, flow))
        assert [(lk.x_hi_m, lk.onset_s, lk.persists_to_end) for lk in run.locks] == [
            (4600.0, 1200.0, True),
            (3100.0, 1350.0, True),
        ]

    def test_a_lock_that_stood_on_its_own_before_a_queue_reached_it_stays_a_lock(
        self,
    ) -> None:
        # a head at 3,100 m stands from 900 s; the queue of a lock at 4,600 m
        # (from 1,200 s) reaches it 705 s later, more than the lock duration
        density, flow = _grid()
        _plant_lock(density, flow, head=45)
        for j in range(HEAD + 1):
            _jam(density, flow, 60 + math.ceil(j * DX / QUEUE_GROWTH_MS / DT), HEAD - j)
        run = detect_locks(_meta(), edges=_frame(density, flow))
        assert [(lk.x_hi_m, lk.onset_s, lk.persists_to_end) for lk in run.locks] == [
            (4600.0, 1200.0, True),
            (3100.0, 900.0, True),  # it stands on, behind the other's queue
        ]


class TestPartiallyRecorded:
    """Review 2026-10-07, finding 4: a run with vehicles.parquet alone counted as
    recorded, so no_locks could PASS (and the report say 'confident: yes') on the
    run-end reader, which cannot see a released lock or one whose last crossing
    vehicles are still in the network near the end. Such a run is partially
    recorded: a lock it finds counts, but its "no lock" is not established."""

    def test_the_record_says_partially_recorded_and_never_unlocked(self) -> None:
        run = detect_locks(_meta(), vehicles=_vehicles(last_crossing_s=END - 300.0))
        assert run.recorded and not run.complete and run.partially_recorded
        assert run.locks == () and run.locked is None
        assert run.to_dict()["locked"] is None and run.to_dict()["n_locks"] == 0
        assert lock_flags([run]) == [None] and lock_free([run]) is None
        # a lock it does find counts
        assert detect_locks(_meta(), vehicles=_vehicles()).locked is True
        # with the space-time table the run is completely recorded
        full = detect_locks(_meta(), edges=_frame(*_grid()), vehicles=_vehicles(discharging=True))
        assert full.complete and full.locked is False
        block = lock_summary([run, full], labels=["a", "b"])
        assert block is not None and block["runs_partially_recorded"] == ["a"]

    def test_the_criterion_row_is_not_recorded_and_says_why(self) -> None:
        run = detect_locks(_meta(), vehicles=_vehicles(last_crossing_s=END - 300.0))
        full = detect_locks(_meta(), edges=_frame(*_grid()))
        row = next(r for r in evaluate(lock_records=[run, full]) if r.name == NO_LOCKS)
        assert row.status == "NOT RECORDED" and not row.passed
        assert "1 of 2 run(s) partially recorded (vehicles.parquet without edges.parquet" in (
            row.detail
        )
        assert "carry no lock record" not in row.detail
        # a lock found by the run-end reader alone still fails the set
        locked = detect_locks(_meta(), vehicles=_vehicles())
        row = next(r for r in evaluate(lock_records=[locked, run]) if r.name == NO_LOCKS)
        assert row.status == "FAIL" and "1 of 1 recorded run(s) locked" in row.detail
        assert "partially recorded, no lock found" in row.detail
        with pytest.raises(ValueError, match="not both"):
            evaluate(lock_flags=[False], lock_records=[full])

    def test_the_report_is_not_confident_on_run_end_evidence(self, tmp_path: Path) -> None:
        root = tmp_path / "runs"
        for seed in (1, 2):
            run_dir = _write_run(root / "cafe01234567" / str(seed), seed=seed)
            meta = json.loads((run_dir / "meta.json").read_text())
            meta.update({k: v for k, v in _meta().items() if k != "config"})
            meta["n_collisions"], meta["collisions"] = 0, []
            (run_dir / "meta.json").write_text(json.dumps(meta))
            _vehicles(last_crossing_s=END - 300.0).to_parquet(run_dir / "vehicles.parquet")
        out = tmp_path / "report" / "report.md"
        generate_report(root, out)
        text = out.read_text()
        criteria = next(ln for ln in text.splitlines() if ln.startswith(f"| {NO_LOCKS} |"))
        assert "NOT RECORDED" in criteria and "partially recorded" in criteria
        assert "PASS" not in criteria
        assert "| No permanent standstill (gridlock) in any run | not established |" in text
        assert "| No permanent standstill (gridlock) in any run | yes |" not in text
        assert "no lock established for 2 run(s)" in _section(text, "## Model integrity")
        assert any(
            item.startswith("Locks were read at the run's end only") for item in _limitations(text)
        )


#: A full closure on the head's cell for 20 min (a work zone or incident, seeded=True).
CLOSURE = {
    "label": "incident",
    "start_m": 3100.0,
    "end_m": 3300.0,
    "lanes": [0, 1, 2, 3],
    "t_start_s": 1200.0,
    "t_end_s": 2400.0,
}


class TestSeededDisturbances:
    """Review 2026-10-07, finding 5: a supported LaneClosureSpec closing every lane (or
    a long seeded stop) for 10 min or more was scored a lock, a model defect. A
    standstill an imposed disturbance explains is a seeded standstill, not a lock;
    one standing on for the lock duration after the closure is lifted still is."""

    @staticmethod
    def _closure_meta(where: str = "meta") -> dict[str, Any]:
        meta = _meta(seeded=True)
        if where == "meta":  # the runner's record, on the run's own axis
            meta["closures"] = [{**CLOSURE, "x_lo_m": 3100.0, "x_hi_m": 3300.0}]
        else:  # an older meta: the config's block only
            meta["config"] = {**meta["config"], "closures": [CLOSURE]}
        return meta

    @staticmethod
    def _cleared_after_the_closure() -> pd.DataFrame:
        density, flow = _grid()
        _plant_lock(density, flow, k1=ONSET_K + 81)  # stands 1,200-2,415 s, then discharges
        return _frame(density, flow)

    @pytest.mark.parametrize("where", ["meta", "config"])
    def test_a_closure_standstill_is_seeded_not_a_lock(self, where: str) -> None:
        run = detect_locks(self._closure_meta(where), edges=self._cleared_after_the_closure())
        assert run.locks == () and run.locked is False
        (standstill,) = run.seeded_standstills
        assert standstill.seeded_by == "closure incident"
        assert (standstill.x_hi_m, standstill.onset_s, standstill.release_s) == (
            3100.0,
            1200.0,
            2415.0,
        )
        assert RunLocks.from_dict(json.loads(json.dumps(run.to_dict()))) == run
        row = next(r for r in evaluate(lock_records=[run]) if r.name == NO_LOCKS)
        assert row.status == "PASS"
        assert "1 standstill(s) at a seeded disturbance" in row.detail
        block = lock_summary([run], labels=["r"])
        assert block is not None and block["n_runs_locked"] == 0
        assert block["seeded_standstills"][0]["seeded_by"] == "closure incident"
        # without the closure the same field is a lock
        assert detect_locks(_meta(), edges=self._cleared_after_the_closure()).locked is True

    def test_standing_on_after_the_closure_is_lifted_is_a_lock(self) -> None:
        # the queue never discharges: 20 min of standstill after the closure's end
        run = detect_locks(self._closure_meta(), edges=_locked_edges())
        assert run.locked is True and run.seeded_standstills == ()

    def test_a_long_seeded_stop_is_seeded(self) -> None:
        meta = _meta(seeded=True)
        meta["config"] = {
            **meta["config"],
            "perturbation": {"t_s": 1150.0, "position_m": 100.0, "duration_s": 1300.0},
        }
        run = detect_locks(meta, edges=self._cleared_after_the_closure())
        assert run.locks == () and run.seeded_standstills[0].seeded_by == "perturbation"

    def test_a_stored_record_is_split_with_the_runs_meta(self, tmp_path: Path) -> None:
        old = detect_locks(_meta(), edges=self._cleared_after_the_closure())
        assert old.locked is True  # as a record stored before the split reads
        run = _stored_replicate(tmp_path / "r", stored=old.to_dict())
        (run / "meta.json").write_text(json.dumps(self._closure_meta()))
        locks = load_replicate_analysis(run).locks
        assert locks is not None and locks.locked is False
        assert locks.seeded_standstills[0].seeded_by == "closure incident"

    def test_the_report_names_it_and_raises_no_failure(self, tmp_path: Path) -> None:
        root = tmp_path / "runs"
        run_dir = _write_run(root / "cafe01234567" / "1", seed=1)
        meta = json.loads((run_dir / "meta.json").read_text())
        meta.update({k: v for k, v in self._closure_meta().items() if k != "config"})
        meta["n_collisions"], meta["collisions"] = 0, []
        (run_dir / "meta.json").write_text(json.dumps(meta))
        self._cleared_after_the_closure().to_parquet(run_dir / "edges.parquet")
        out = tmp_path / "report" / "report.md"
        generate_report(root, out)
        text = out.read_text()
        assert "MODEL INTEGRITY FAILURE" not in text
        section = _section(text, "## Model integrity")
        assert "- Locks: 0 of 1 run(s) locked" in section
        assert "1 standstill(s) of 10 min or more at a seeded disturbance" in section
        assert "cafe01234567/1 at closure incident" in section
        criteria = next(ln for ln in text.splitlines() if ln.startswith(f"| {NO_LOCKS} |"))
        assert "PASS" in criteria and "seeded disturbance" in criteria


class TestSectionsAndCounts:
    def test_sections_pair_the_weave_and_skip_its_exit(self) -> None:
        sections = corridor_sections(_meta())
        assert [(s.name, s.kind, s.model) for s in sections] == [
            ("off A", "diverge", None),
            ("W-ON", "weave", "weave"),
            ("D-ON", "merge", "scripted"),
        ]
        assert corridor_sections({}) == []

    def test_never_departed_needs_the_counters(self) -> None:
        assert never_departed_upstream({}, 1000.0) is None
        assert never_departed_upstream(_meta(), 5000.0) == 100 + 10
        assert never_departed_upstream(_meta(), 100.0) == 60


def _lock(onset: float, section: str | None = "W-ON") -> Lock:
    run = detect_locks(_meta(), edges=_locked_edges())
    lock = run.locks[0]
    sec = None if section is None else dataclasses.replace(lock.section, name=section)  # type: ignore[arg-type]
    return dataclasses.replace(lock, onset_s=onset, section=sec)


class TestSummary:
    def test_locked_runs_by_section_with_their_interval(self) -> None:
        locked_a = RunLocks((SOURCE_EDGES,), END, (_lock(1200.0),))
        locked_b = RunLocks((SOURCE_VEHICLES,), END, (_lock(2000.0),))
        clean = RunLocks((SOURCE_EDGES, SOURCE_VEHICLES), END, ())
        records = [locked_a, clean, None, locked_b, NOT_RECORDED]
        block = lock_summary(records, labels=[11, 12, 13, 14, 15])
        assert block is not None
        assert block["n_runs"] == 5 and block["n_runs_recorded"] == 3
        assert block["runs_not_recorded"] == [13, 15]
        assert block["n_runs_locked"] == 2
        share = block["share_locked"]
        assert share["value"] == pytest.approx(2 / 3)
        assert share["lo95"] == pytest.approx(float(beta.ppf(0.025, 2, 2)))
        assert share["hi95"] == pytest.approx(float(beta.ppf(0.975, 3, 1)))
        assert [r["run"] for r in block["runs_locked"]] == [11, 14]
        assert block["runs_locked"][0]["locks"][0]["section"] == "W-ON"
        (row,) = block["by_section"]
        assert (row["section"], row["kind"], row["n_runs"], row["runs"]) == (
            "W-ON",
            "weave",
            2,
            [11, 14],
        )
        assert (row["onset_s_min"], row["onset_s_max"]) == (1200.0, 2000.0)
        assert block["sources"] == {SOURCE_EDGES: 2, SOURCE_VEHICLES: 2}
        assert block["params"]["min_duration_s"] == LOCK_MIN_DURATION_S
        assert lock_flags(records) == [True, False, None, True, None]
        assert lock_free(records) is False

    def test_a_lock_without_a_section_is_grouped_by_place(self) -> None:
        block = lock_summary([RunLocks((SOURCE_EDGES,), END, (_lock(1200.0, None),))])
        assert block is not None and block["by_section"][0]["section"] == "x 3050 m"

    def test_nothing_recorded_is_null_and_clean_runs_pass(self) -> None:
        assert lock_summary([None, NOT_RECORDED]) is None
        assert lock_free([None, NOT_RECORDED]) is None
        clean = RunLocks((SOURCE_EDGES,), END, ())
        assert lock_free([clean, clean]) is True
        assert lock_free([clean, None]) is None
        block = lock_summary([clean])
        assert block is not None and block["n_runs_locked"] == 0
        assert block["share_locked"]["lo95"] == 0.0


def _stored_replicate(root: Path, *, stored: Any = "absent", with_files: bool = True) -> Path:
    """A scored, pruned replicate: meta, metrics.json (``locks`` optional), scores."""
    root.mkdir(parents=True)
    (root / "meta.json").write_text(json.dumps(_meta()))
    if with_files:
        _locked_edges().to_parquet(root / "edges.parquet")
        _vehicles().to_parquet(root / "vehicles.parquet")
    payload: dict[str, Any] = {
        "metrics": {
            f.name: (3 if f.name in ("wave_count", "n_travel_time_veh") else 10.0)
            for f in dataclasses.fields(Metrics)
        },
        "criterion_wave_speed_kmh": None,
        "criterion_detector": "profile",
        "x_ref_m": 0.0,
        "span_m": [0.0, 0.0],
        "insertion": {},
        "waiting": None,
    }
    if stored != "absent":
        payload["locks"] = stored
    (root / METRICS_FILE).write_text(json.dumps(payload))
    scores = ObservedScores(
        geh_values=(4.0,),
        n_link_hours=1,
        rmspe=0.1,
        n_speed_cells=1,
        segment_speeds_sim=((25.0,),),
        segment_speeds_obs=((25.0,),),
        windows=(0,),
    )
    (root / SCORES_FILE).write_text(json.dumps(scores.to_dict()))
    return root


class TestStoredRecord:
    def test_a_stored_block_is_read_back(self, tmp_path: Path) -> None:
        clean = RunLocks((SOURCE_EDGES,), END, ())
        run = _stored_replicate(tmp_path / "r", stored=clean.to_dict())
        # the stored record wins over the files (an archive may lack edges.parquet)
        assert load_replicate_analysis(run).locks == clean

    def test_a_file_scored_before_the_key_is_read_from_the_tables(self, tmp_path: Path) -> None:
        locks = load_replicate_analysis(_stored_replicate(tmp_path / "r")).locks
        assert locks is not None and locks.locked is True
        bare = load_replicate_analysis(_stored_replicate(tmp_path / "b", with_files=False)).locks
        assert bare == NOT_RECORDED


class TestReport:
    @staticmethod
    def _run_set(root: Path, locked: tuple[bool, ...]) -> Path:
        for seed, is_locked in enumerate(locked, start=1):
            run_dir = _write_run(root / "cafe01234567" / str(seed), seed=seed)
            meta = json.loads((run_dir / "meta.json").read_text())
            meta.update({k: v for k, v in _meta().items() if k != "config"})
            meta["n_collisions"], meta["collisions"] = 0, []
            (run_dir / "meta.json").write_text(json.dumps(meta))
            density, flow = _grid()
            if is_locked:
                _plant_lock(density, flow)
            _frame(density, flow).to_parquet(run_dir / "edges.parquet")
        return root

    @staticmethod
    def _report(root: Path, tmp_path: Path) -> str:
        out = tmp_path / "report" / "report.md"
        generate_report(root, out)
        return out.read_text()

    def test_a_locked_run_fails_the_row_and_opens_the_report(self, tmp_path: Path) -> None:
        text = self._report(self._run_set(tmp_path / "runs", (True, False)), tmp_path)
        section = _section(text, "## Model integrity")
        assert "- Locks: 1 of 2 run(s) locked (50 %; 95 % Clopper–Pearson interval" in section
        assert "for at least 10 min." in section
        row = next(ln for ln in section.splitlines() if ln.startswith("| cafe01234567/1 |"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells == [
            "cafe01234567/1",
            "3050.0",
            "W-ON (weave)",
            "1200",
            "≥ 40",
            "yes",
            "—",
            "100",
        ]
        banner = "> **MODEL INTEGRITY FAILURE — 1 of 2 run(s) locked (a permanent standstill)"
        assert banner in text and text.index(banner) < text.index("## Provenance")
        criteria = next(ln for ln in text.splitlines() if ln.startswith(f"| {NO_LOCKS} |"))
        assert "| 1 | " in criteria and "FAIL — 1 of 2 recorded run(s) locked" in criteria
        assert any(
            item.startswith("This run set contains 1 locked run(s) of 2 (cafe01234567/1 at W-ON")
            for item in _limitations(text)
        )
        summary = _section(text, "## Client summary")
        assert "| No permanent standstill (gridlock) in any run | no | 1 of 2 run(s) locked" in (
            summary
        )

    def test_clean_runs_pass_and_runs_without_tables_are_not_recorded(self, tmp_path: Path) -> None:
        text = self._report(self._run_set(tmp_path / "runs", (False, False)), tmp_path)
        assert "- Locks: 0 of 2 run(s) locked" in _section(text, "## Model integrity")
        criteria = next(ln for ln in text.splitlines() if ln.startswith(f"| {NO_LOCKS} |"))
        assert "PASS — no lock in 2 run(s)" in criteria
        assert "MODEL INTEGRITY FAILURE" not in text
        assert "| No permanent standstill (gridlock) in any run | yes |" in text
        # the report test fixtures' runs carry neither table
        bare = tmp_path / "bare"
        _write_run(bare / "cafe01234567" / "1", seed=1)
        text = self._report(bare, tmp_path / "b")
        assert "- Locks: not recorded — no run in this set has the files" in text
        criteria = next(ln for ln in text.splitlines() if ln.startswith(f"| {NO_LOCKS} |"))
        assert "NOT RECORDED" in criteria and "PASS" not in criteria
        assert "| No permanent standstill (gridlock) in any run | not established |" in text

    def test_caller_records_are_used_as_given(self, tmp_path: Path) -> None:
        root = self._run_set(tmp_path / "runs", (False,))
        run_dir = root / "cafe01234567" / "1"
        given = RunLocks((SOURCE_VEHICLES,), END, (_lock(900.0),))
        out = tmp_path / "report" / "report.md"
        generate_report(root, out, locks_by_run={run_dir: given})
        assert "- Locks: 1 of 1 run(s) locked" in out.read_text()


class TestCorridorBattery:
    def test_the_artifact_carries_the_records_the_block_and_the_flag(self, tmp_path: Path) -> None:
        battery = col._load_script()
        records = [RunLocks((SOURCE_VEHICLES,), END, (_lock(1500.0),)), None, NOT_RECORDED]
        artifact = col._strict(_build(battery, tmp_path, records))
        keys = list(artifact)
        assert keys.index("locks") == keys.index("zero_collisions") + 1
        assert keys.index("zero_locks") == keys.index("locks") + 1
        assert artifact["zero_locks"] is False
        block = artifact["locks"]
        assert block["n_runs_locked"] == 1 and block["runs_not_recorded"] == [102, 103]
        assert block["runs_locked"][0]["run"] == 101
        per_seed = [row["locks"] for row in artifact["per_seed"]]
        assert per_seed[0]["locked"] is True and per_seed[0]["locks"][0]["onset_s"] == 1500.0
        assert per_seed[1] is None
        assert per_seed[2] == {
            "locked": None,
            "sources": [],
            "end_s": None,
            "n_locks": None,
            "locks": [],
        }
        assert any(note.startswith("The locks block counts") for note in artifact["notes"])
        line = battery.lock_line(artifact["locks"])
        assert re.search(r"locks\s+1 of 1 replicate\(s\) locked \(100 %", line)
        assert "not recorded for 2 replicate(s); at W-ON (1, onset 1500 s)" in line
        assert "not recorded (no replicate has" in battery.lock_line(None)


def _build(battery: Any, tmp_path: Path, records: list[RunLocks | None]) -> dict[str, Any]:
    """``build_artifact`` as ``col._build`` calls it, with lock records."""
    import yaml

    scenario = tmp_path / "table_corridor.yaml"
    scenario.write_text(
        yaml.safe_dump(
            {
                "name": "table_corridor",
                "tier": "micro",
                "network": {
                    "kind": "corridor",
                    "length_m": 1000.0,
                    "lanes": 1,
                    "inflow": [[0.0, 0.2]],
                },
                "sim": {"duration_s": 7200.0, "step_length_s": 0.5, "output_hz": 1.0},
                "seed": 11,
                "replicates": len(col.SEEDS),
            },
            sort_keys=False,
        )
    )
    metrics = Metrics(
        **{
            f.name: (3 if f.name in ("wave_count", "n_travel_time_veh") else 10.0)
            for f in dataclasses.fields(Metrics)
        }
    )
    from tests.test_validation.test_validation_observed import table_payload, table_scores

    out: dict[str, Any] = battery.build_artifact(
        scenario="table_corridor",
        cfg=battery.load_scenario(scenario),
        profile=battery.get_profile("fhwa_default"),
        seeds=list(col.SEEDS),
        dirs=[Path("runs") / str(seed) for seed in col.SEEDS],
        metrics_list=[metrics] * len(col.SEEDS),
        scores_list=[table_scores()] * len(col.SEEDS),
        wave_speeds=[math.nan] * len(col.SEEDS),
        insertion_list=[insertion_stats({"n_vehicles_planned": 7, "n_vehicles_departed": 7})]
        * len(col.SEEDS),
        weave_exits=weave_exit_summary([{}] * len(col.SEEDS)),
        observed=ObservedCorridor.from_dict(table_payload()),
        observations_path="artifacts/observations_table.json",
        criteria_rows=[],
        ring=None,
        x_offset_m=50.0,
        wall_s=1.0,
        lock_records=records,
    )
    return out
