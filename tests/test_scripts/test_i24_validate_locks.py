"""scripts/i24_validate.py records each replicate's locks and scores ``no_locks`` (2026-10-07).

No simulation and no I-24 data (the laptop rule): replicate directories are
hand-built — a ``meta.json`` and the runner's 15 s × 100 m ``edges.parquet``
(and, for one, a ``vehicles.parquet``) — and the trajectory-reading parts of
the analysis worker are stubbed. Pinned:

* the worker's lock record is :func:`validation.locks.detect_run_locks` on
  the replicate's tables, the corridor batteries' reader: free flow is no
  lock; a standing queue that freezes in pieces is one lock (the "one
  standing queue is one lock" rule of the second regression review); a
  breakdown like the p14 B1 + B2 seed 134183728835869882 — a dense queue that
  crawls over most of the corridor with standstills of 180 s, but never stops
  discharging for the lock duration — is congestion, not a lock (the
  archived seed's own reading, ``artifacts/i24_locks_p14_b1b2.json``); the
  same breakdown once it stops discharging for 10 min is one lock;
* ``micro_arm`` stores the records in seed order, ``build_results`` scores
  ``no_locks`` from them (PASS / FAIL instead of NOT RECORDED) and
  ``add_lock_blocks`` writes the corridor batteries' ``locks`` /
  ``zero_locks`` keys; without records the row stays NOT RECORDED;
* every other key and value of the artifact is unchanged (keys only added,
  last), the explicit-scenario path writes the keys after ``zero_collisions``
  and ``--criteria-only`` re-scores the row from the stored records.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd
import pytest

from validation.locks import LOCK_MIN_DURATION_S, SOURCE_EDGES, SOURCE_VEHICLES, RunLocks
from validation.metrics import Metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
ARM = REPO_ROOT / "artifacts" / "i24_validation_flow_speedcal.json"
OBSERVED = REPO_ROOT / "artifacts" / "i24_validation_observed.json"
SCENARIO = REPO_ROOT / "scenarios" / "i24_replica_flow_speedcal.yaml"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v = _load("i24_validate")

# --- synthetic replicates ---------------------------------------------------------

#: One hour on a 5 km corridor in the runner's Edie bins.
DT = 15.0
DX = 100.0
END = 3600.0
NT = int(END / DT)
NX = 50
#: Free flow: 50 veh/km over all lanes at 25 m/s.
FREE_DENSITY = 0.05
FREE_SPEED = 25.0
#: A standing queue: 300 veh/km over all lanes.
JAM_DENSITY = 0.3
#: The head cell [3000, 3100) m (the on-ramp's gore is in it) and the onset bin (1200 s).
HEAD = 30
ONSET_K = 80
#: The p14 seed's breakdown, scaled down: 400 veh/km over all lanes crawling at
#: 0.5 m/s (720 veh/h, about the 752 veh/h the archived seed's slowest cell
#: discharged over its worst 10 min) with 180 s standstills, its longest.
CRAWL_DENSITY = 0.4
CRAWL_SPEED = 0.5
STALL_BINS = 12  # 180 s
STALL_EVERY_BINS = 40  # one standstill per cell every 10 min, staggered upstream
#: The piece of :func:`standing_queue`: five cells upstream of the head, frozen 60 s in.
PIECE_CELLS_UPSTREAM = 5
PIECE_DELAY_BINS = 4


def _meta(seed: int) -> dict[str, Any]:
    return {
        "seed": seed,
        "config": {"sim": {"duration_s": END, "warmup_s": 0.0, "output_hz": 1.0}},
        "output_hz_realized": 1.0,
        "n_vehicles_planned": 1000,
        "n_vehicles_departed": 990,
        "n_collisions": 0,
        "ramps": [
            {
                "index": 0,
                "name": "OH-ON",
                "kind": "on",
                "attach_edge": "eOH",
                "attach_x_m": 2900.0,
                "attach_end_x_m": 3090.0,
                "n_planned": 100,
                "n_departed": 95,
            }
        ],
    }


def _free() -> tuple[np.ndarray, np.ndarray]:
    density = np.full((NT, NX), FREE_DENSITY)
    return density, density * FREE_SPEED


def _frame(density: np.ndarray, flow: np.ndarray) -> pd.DataFrame:
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


def free_flow() -> pd.DataFrame:
    """Free flow everywhere: no cell ever stands."""
    return _frame(*_free())


def standing_queue() -> pd.DataFrame:
    """One queue frozen in pieces: the head cell stands first and its queue grows
    upstream at 3 m/s; a piece five cells upstream freezes a minute later, 300 m
    clear of the queue's tail (not bridged), and the tail reaches it 15 s after.
    The road downstream drains. One lock (the second review's rule), not two."""
    density, flow = _free()
    for j in range(HEAD + 1):
        start = ONSET_K + math.ceil(j * DX / 3.0 / DT)
        if j == PIECE_CELLS_UPSTREAM:
            start = ONSET_K + PIECE_DELAY_BINS
        density[start:, HEAD - j] = JAM_DENSITY
        flow[start:, HEAD - j] = 0.0
    density[ONSET_K + 4 :, HEAD + 1 :] = 0.0
    flow[ONSET_K + 4 :, HEAD + 1 :] = 0.0
    return _frame(density, flow)


def _crawl(density: np.ndarray, flow: np.ndarray) -> None:
    """The breakdown: from the onset, cells upstream of the head crawl, each
    standing 180 s every 10 min (a backward-moving stop-and-go stripe)."""
    for i in range(HEAD + 1):
        density[ONSET_K:, i] = CRAWL_DENSITY
        flow[ONSET_K:, i] = CRAWL_DENSITY * CRAWL_SPEED
        phase = (HEAD - i) % STALL_EVERY_BINS
        for k in range(ONSET_K + phase, NT, STALL_EVERY_BINS):
            flow[k : k + STALL_BINS, i] = 0.0


def crawling_breakdown() -> pd.DataFrame:
    """Like the p14 seed: dense and slow over most of the corridor, never standing
    the lock duration in any cell. Congestion, not a lock."""
    density, flow = _free()
    _crawl(density, flow)
    return _frame(density, flow)


def stopped_breakdown() -> pd.DataFrame:
    """The same breakdown, but the head stops discharging for good 20 min in:
    a queue standing behind it from then on. One lock."""
    density, flow = _free()
    _crawl(density, flow)
    stop = ONSET_K + 80
    for j in range(HEAD + 1):
        start = stop + math.ceil(j * DX / 3.0 / DT)
        density[start:, HEAD - j] = JAM_DENSITY
        flow[start:, HEAD - j] = 0.0
    density[stop + 4 :, HEAD + 1 :] = 0.0
    flow[stop + 4 :, HEAD + 1 :] = 0.0
    return _frame(density, flow)


def _vehicles() -> pd.DataFrame:
    """A run's vehicle table in which every vehicle arrived (no run-end lock)."""
    n = 20
    return pd.DataFrame(
        {
            "veh_id": [f"v{i}" for i in range(n)],
            "origin_ramp": np.full(n, -1, dtype=np.int32),
            "destination": ["corridor_end"] * n,
            "destination_final": ["corridor_end"] * n,
            "entry_x_m": np.zeros(n),
            "last_t_s": np.linspace(100.0, END - 60.0, n),
            "last_x_m": np.full(n, NX * DX - 5.0),
            "arrived": np.ones(n, dtype=bool),
        }
    )


def write_replicate(
    run_dir: Path, seed: int, edges: pd.DataFrame | None, *, vehicles: bool = False
) -> Path:
    """A replicate directory: ``meta.json``, ``edges.parquet`` unless None, and
    ``vehicles.parquet`` when asked."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(json.dumps(_meta(seed)))
    if edges is not None:
        edges.to_parquet(run_dir / "edges.parquet", index=False)
    if vehicles:
        _vehicles().to_parquet(run_dir / "vehicles.parquet", index=False)
    return run_dir


def _record(run_dir: Path) -> RunLocks:
    meta = json.loads((run_dir / "meta.json").read_text())
    return RunLocks.from_dict(v.replicate_locks(run_dir, meta))


# --- one replicate ----------------------------------------------------------------


class TestReplicateLocks:
    def test_free_flow_is_no_lock_and_reads_both_tables(self, tmp_path: Path) -> None:
        rec = _record(write_replicate(tmp_path / "1", 1, free_flow(), vehicles=True))
        assert rec.sources == (SOURCE_EDGES, SOURCE_VEHICLES)
        assert rec.locked is False and rec.locks == () and rec.end_s == END

    def test_a_standing_queue_frozen_in_pieces_is_one_lock(self, tmp_path: Path) -> None:
        rec = _record(write_replicate(tmp_path / "2", 2, standing_queue()))
        assert rec.locked is True
        (lock,) = rec.locks
        assert (lock.x_lo_m, lock.x_hi_m) == (HEAD * DX, (HEAD + 1) * DX)
        assert lock.onset_s == ONSET_K * DT and lock.persists_to_end
        assert lock.section is not None and lock.section.name == "OH-ON"

    def test_a_crawling_breakdown_like_the_p14_seed_is_not_a_lock(self, tmp_path: Path) -> None:
        edges = crawling_breakdown()
        slow = (edges["mean_speed"] < 2.0) | (edges["flow"] == 0.0)
        assert slow.mean() > 0.3  # most of the field crawls, as in the archived seed
        rec = _record(write_replicate(tmp_path / "3", 3, edges))
        assert STALL_BINS * DT < LOCK_MIN_DURATION_S
        assert rec.locked is False and rec.locks == ()

    def test_the_breakdown_that_stops_discharging_is_one_lock(self, tmp_path: Path) -> None:
        rec = _record(write_replicate(tmp_path / "4", 4, stopped_breakdown()))
        assert rec.locked is True
        (lock,) = rec.locks
        assert lock.x_lo_m == HEAD * DX and lock.persists_to_end
        assert lock.duration_s >= LOCK_MIN_DURATION_S

    def test_a_replicate_without_tables_is_not_recorded(self, tmp_path: Path) -> None:
        rec = _record(write_replicate(tmp_path / "5", 5, None))
        assert not rec.recorded and rec.locked is None


# --- the battery ------------------------------------------------------------------


def _stub_heavy_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the trajectory-reading parts of the analysis worker (no trajectories exist):
    one 20 m/s sample per 5-min window and speed segment of the measured span."""
    span_hi = v._span()[1]
    n_win = int(json.loads(OBSERVED.read_text())["n_windows"])
    t, x = np.meshgrid(
        (np.arange(n_win) + 0.5) * v.WINDOW_S,
        (np.arange(v.N_SEGMENTS) + 0.5) * span_hi / v.N_SEGMENTS,
        indexing="ij",
    )
    samples = pd.DataFrame(
        {"t": t.ravel(), "x": x.ravel(), "v": 20.0, "veh_id": [f"s{i}" for i in range(t.size)]}
    )
    nan_metrics = Metrics(**{f.name: math.nan for f in dataclasses.fields(Metrics)})
    wave = {"mean_backward_speed_kmh": None, "count": 0, "backward_speeds_kmh": []}
    monkeypatch.setattr(v, "_sim_frame", lambda run_dir, a, b: samples.copy())
    monkeypatch.setattr(v, "compute_metrics", lambda run_dir, **k: nan_metrics)
    monkeypatch.setattr(
        v, "_wave_summaries", lambda df, span_hi: {n: dict(wave) for n in v.WAVE_DETECTORS}
    )


def _battery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fields: list[pd.DataFrame | None]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``micro_arm`` over hand-built replicates (one per field, seed order), then
    ``build_results`` and ``add_lock_blocks`` as ``main`` does for an arm."""
    from microsim.runner import RunPaths

    cfg = v.load_scenario(SCENARIO)
    seeds = v.spawn_seeds(cfg.seed, len(fields))
    root = tmp_path / "runs"
    paths = []
    for seed, edges in zip(seeds, fields, strict=True):
        d = write_replicate(root / str(seed), seed, edges)
        paths.append(RunPaths(d, d / "trajectories.parquet", d / "edges.parquet", d / "meta.json"))
    _stub_heavy_parts(monkeypatch)
    monkeypatch.setattr(v, "run_replicates", lambda cfg, out_root, n_procs: paths)
    obs = json.loads(OBSERVED.read_text())
    sim = v.micro_arm(cfg, len(fields), root, 1, obs, analysis_procs=1)
    records = v.stored_lock_records(sim)
    results = v.build_results("speedcal", cfg, sim, obs, len(fields), None, lock_records=records)
    v.add_lock_blocks(results, records, sim["seeds"])
    return sim, results


def _row(results: dict[str, Any]) -> dict[str, Any]:
    (row,) = [r for r in results["criteria"] if r["name"] == "no_locks"]
    return row


class TestBattery:
    def test_free_replicates_pass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sim, res = _battery(tmp_path, monkeypatch, [free_flow(), crawling_breakdown()])
        assert [r["locked"] for r in sim["locks_per_replicate"]] == [False, False]
        row = _row(res)
        assert row["evaluated"] and row["passed"] and row["value"] == 0.0
        assert res["zero_locks"] is True and res["locks"]["n_runs_locked"] == 0
        assert list(res)[-2:] == ["locks", "zero_locks"]

    def test_a_locked_replicate_fails_and_is_named_by_seed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sim, res = _battery(tmp_path, monkeypatch, [free_flow(), stopped_breakdown(), None])
        seeds = sim["seeds"]
        assert [r["locked"] for r in sim["locks_per_replicate"]] == [False, True, None]
        row = _row(res)
        assert row["evaluated"] and not row["passed"] and row["value"] == 1.0
        assert res["zero_locks"] is False
        locks = res["locks"]
        assert [r["run"] for r in locks["runs_locked"]] == [seeds[1]]
        assert locks["runs_not_recorded"] == [seeds[2]]
        assert locks["by_section"][0]["section"] == "OH-ON"
        assert "OH-ON" in v.lock_console_line(res) and str(seeds[1]) in v.lock_console_line(res)

    def test_without_records_the_row_stays_not_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sim, _ = _battery(tmp_path, monkeypatch, [free_flow()])
        bare = {k: val for k, val in sim.items() if k != "locks_per_replicate"}
        assert v.stored_lock_records(bare) is None
        res = v.build_results("speedcal", v.load_scenario(SCENARIO), bare, json.loads(OBSERVED.read_text()), 1, None)  # fmt: skip
        row = _row(res)
        assert not row["evaluated"] and row["value"] is None
        assert row["detail"].startswith("not recorded")
        v.add_lock_blocks(res, None, bare["seeds"])
        assert res["locks"] is None and res["zero_locks"] is None
        assert v.lock_console_line(res).startswith("    locks              not recorded")


def test_the_artifact_only_gains_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """From the committed arm's sides, the artifact with lock records equals the one
    without in every key and value but the no_locks row and the three new keys,
    which come last (``simulated.locks_per_replicate`` last in its block)."""
    monkeypatch.setattr(v, "FAMILY", "_flow")
    d = json.loads(ARM.read_text())
    cfg = v.load_scenario(SCENARIO)
    free = _record(write_replicate(tmp_path / "f", 1, free_flow()))
    before = v.build_results("speedcal", cfg, d["simulated"], d["observed"], 20, d["ring"])
    sim = copy.deepcopy(d["simulated"])
    sim["locks_per_replicate"] = [free.to_dict()] * 20
    records = v.stored_lock_records(sim)
    after = v.build_results(
        "speedcal", cfg, sim, d["observed"], 20, d["ring"], lock_records=records
    )
    v.add_lock_blocks(after, records, sim["seeds"])
    assert list(after) == [*before, "locks", "zero_locks"]
    assert list(after["simulated"]) == [*d["simulated"], "locks_per_replicate"]
    for key in before:
        if key in ("created_at", "criteria", "simulated"):
            continue
        assert after[key] == before[key], key
    assert {k: val for k, val in after["simulated"].items() if k != "locks_per_replicate"} == d[
        "simulated"
    ]
    rows_before = {r["name"]: r for r in before["criteria"]}
    rows_after = {r["name"]: r for r in after["criteria"]}
    assert list(rows_after) == list(rows_before)
    for name in rows_before:
        if name != "no_locks":
            assert rows_after[name] == rows_before[name], name
    assert not rows_before["no_locks"]["evaluated"]
    assert rows_after["no_locks"]["evaluated"] and rows_after["no_locks"]["passed"]
    assert after["zero_locks"] is True


def test_the_explicit_path_writes_the_keys_and_criteria_only_rescores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    d = json.loads(ARM.read_text())
    seeds = d["simulated"]["seeds"]
    runs = tmp_path / "fake"
    dirs = [
        write_replicate(runs / str(s), s, stopped_breakdown() if i == 3 else free_flow())
        for i, s in enumerate(seeds)
    ]
    sim = copy.deepcopy(d["simulated"])
    sim["run_dirs"] = [str(p) for p in dirs]
    sim["locks_per_replicate"] = [_record(p).to_dict() for p in dirs]

    def fake_micro_arm(cfg: Any, n: int, out_root: Path, *a: Any, **k: Any) -> dict[str, Any]:
        return copy.deepcopy(sim)

    arts = tmp_path / "artifacts"
    arts.mkdir()
    monkeypatch.setattr(v, "OUT_ROOT", tmp_path / "runs")
    monkeypatch.setattr(v, "artifact_path", lambda arm: arts / f"i24_validation_{arm}.json")
    monkeypatch.setattr(v, "observed_side", lambda cache: d["observed"])
    monkeypatch.setattr(v, "micro_arm", fake_micro_arm)
    v.main(["--scenario", str(SCENARIO), "--label", "lk", "--ring-seeds", "0"])
    path = arts / "i24_validation_lk.json"
    res = json.loads(path.read_text())
    assert list(res)[-5:] == ["scenario_file", "collisions", "zero_collisions", "locks", "zero_locks"]  # fmt: skip
    assert res["zero_collisions"] is True and res["zero_locks"] is False
    assert [r["run"] for r in res["locks"]["runs_locked"]] == [seeds[3]]
    row = _row(res)
    assert row["evaluated"] and not row["passed"] and row["value"] == 1.0
    out = capsys.readouterr().out
    assert "locks              1 of 20 replicate(s) locked (5 %, 95 % CI 0–25 %)" in out
    assert f"; at OH-ON (1, onset 2400 s); seeds {seeds[3]}" in out
    # --criteria-only re-scores the row from the stored records
    stored = json.loads(path.read_text())
    for r in stored["criteria"]:
        if r["name"] == "no_locks":
            r.update(evaluated=False, passed=False, value=None, detail="stale")
    path.write_text(json.dumps(stored))
    v.main(["--label", "lk", "--criteria-only", "--ring-seeds", "0"])
    again = _row(json.loads(path.read_text()))
    assert again["evaluated"] and not again["passed"] and again["value"] == 1.0
