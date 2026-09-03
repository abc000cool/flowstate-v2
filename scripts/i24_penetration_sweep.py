"""ROADMAP §1.5 — penetration × compliance battery on the I-24 replica.

The CLAUDE.md §7.1 sensitivity grid — FollowerStopper at penetration
{1, 2, 5, 10, 15, 20}% × compliance {25, 50, 80, 100}% plus the uncontrolled
baseline, 25 cells × 20 common-random-number seeds = 500 emergent (unseeded)
runs — on the I-24 westbound replica (``scripts/i24_build_replica.py``; the
arm is chosen explicitly with ``--scenario`` so the result records which demand
assumption it rests on, docs/I24_VALIDATION.md).

Every run's standard metrics are computed on the measured span right after it
finishes (``validation.metrics.compute_metrics``; throughput at data
x = 2200 m, travel time over the span) and stored as ``metrics.json`` next to
``meta.json``; the per-replicate trajectories (~240 MB each, 500 runs) are
deleted unless ``--keep-trajectories`` is given — a run is reproducible from
its config hash and seed, and the summary statistics are what the analysis
consumes. Cells already complete on disk are skipped, so the battery resumes.

Usage::

    uv run --no-sync python scripts/i24_penetration_sweep.py --scenario i24_replica_corrected [--procs 8]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
RUNS_ROOT = REPO / "runs" / "i24_sweep"
INPUTS = REPO / "artifacts" / "i24_replica_inputs.json"
PENETRATIONS = (0.01, 0.02, 0.05, 0.10, 0.15, 0.20)
COMPLIANCES = (0.25, 0.50, 0.80, 1.00)
CONTROLLER = "follower_stopper"


def cell_config(base: dict, pen: float, comp: float) -> dict:
    cfg = json.loads(json.dumps(base))
    cfg["av"]["penetration"] = pen
    cfg["av"]["compliance"] = comp
    cfg["av"]["controller"] = CONTROLLER if pen > 0.0 else None
    cfg["av"]["controller_params"] = {}
    return cfg


def _metrics_args() -> dict:
    inputs = json.loads(INPUTS.read_text())
    a, b = inputs["geometry"]["sim_x_of_data_x"]["a"], inputs["geometry"]["sim_x_of_data_x"]["b"]
    lo, hi = inputs["geometry"]["measured_span_data_x_m"]
    return {"x_ref": a + b * 2200.0, "span": (a + b * lo, a + b * hi)}


def _worker(payload: tuple[str, dict, int, bool]) -> tuple[str, int, bool, str]:
    cell_name, cfg_json, seed, keep = payload
    try:
        from flowstate_core.config import ScenarioConfig
        from microsim.runner import run_micro
        from validation.metrics import compute_metrics

        paths = run_micro(ScenarioConfig.model_validate(cfg_json), seed, RUNS_ROOT / cell_name)
        m = compute_metrics(paths.run_dir, **_metrics_args())
        record = asdict(m)
        record.update(lane_change_stats(paths.trajectories, _metrics_args()["span"]))
        (paths.run_dir / "metrics.json").write_text(json.dumps(record, indent=2))
        if not keep:
            paths.trajectories.unlink(missing_ok=True)
            paths.edges.unlink(missing_ok=True)
            shutil.rmtree(paths.run_dir / "net", ignore_errors=True)
        return cell_name, seed, True, ""
    except Exception as exc:
        return cell_name, seed, False, f"{type(exc).__name__}: {exc}"


def lane_change_stats(trajectories: Path, span: tuple[float, float]) -> dict[str, float]:
    """Lane-change events on the measured span (ROADMAP Track D2).

    A lane change is a change of the recorded lane index between consecutive
    2 Hz samples of one vehicle while inside ``span``. Returned per vehicle
    and per vehicle-kilometre travelled inside the span (trapezoid of sampled
    speeds), plus the split between AV-tagged and human vehicles.
    """
    import numpy as np
    import pandas as pd

    df = pd.read_parquet(trajectories, columns=["t", "veh_id", "x", "lane", "v", "is_av"])
    df = df[(df["x"] >= span[0]) & (df["x"] < span[1])].sort_values(["veh_id", "t"], kind="stable")
    same = df["veh_id"].to_numpy()[1:] == df["veh_id"].to_numpy()[:-1]
    lane = df["lane"].to_numpy()
    changed = (lane[1:] != lane[:-1]) & same
    is_av = df["is_av"].to_numpy()[1:]
    t = df["t"].to_numpy()
    v = df["v"].to_numpy()
    dist_m = float(np.sum(0.5 * (v[1:] + v[:-1]) * (t[1:] - t[:-1]) * same))
    n_veh = int(df["veh_id"].nunique())
    n_lc = int(changed.sum())
    return {
        "n_lane_changes": n_lc,
        "n_lane_changes_av": int((changed & is_av).sum()),
        "n_lane_changes_human": int((changed & ~is_av).sum()),
        "lane_changes_per_veh": n_lc / n_veh if n_veh else float("nan"),
        "lane_changes_per_veh_km": n_lc / (dist_m / 1000.0) if dist_m > 0 else float("nan"),
        "n_vehicles_on_span": n_veh,
    }


def _done(cell_name: str, chash: str, seed: int) -> bool:
    d = RUNS_ROOT / cell_name / chash / str(seed)
    return (d / "meta.json").is_file() and (d / "metrics.json").is_file()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--scenario", required=True, help="i24_replica or i24_replica_corrected")
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--replicates", type=int, default=20)
    ap.add_argument("--keep-trajectories", action="store_true")
    args = ap.parse_args()

    from flowstate_core.config import ScenarioConfig, config_hash
    from flowstate_core.rng import spawn_seeds
    from microsim.scenarios import load_scenario

    base_cfg = load_scenario(args.scenario)
    base_json = base_cfg.model_dump(mode="json")
    seeds = spawn_seeds(base_cfg.seed, args.replicates)

    # Cell order = priority: the baseline and the 100%-compliance penetration
    # ladder first (the headline dose-response), then decreasing compliance,
    # so an interrupted battery leaves the most informative cells complete.
    cells: list[tuple[str, float, float]] = [("baseline", 0.0, 1.0)]
    cells += [
        (f"fs_p{p:.2f}_c{c:.2f}", p, c)
        for c in sorted(COMPLIANCES, reverse=True)
        for p in PENETRATIONS
    ]
    pending: list[tuple[str, dict, int, bool]] = []
    hashes: dict[str, str] = {}
    for name, pen, comp in cells:
        cfg_json = cell_config(base_json, pen, comp)
        chash = config_hash(ScenarioConfig.model_validate(cfg_json))
        hashes[name] = chash
        pending += [
            (name, cfg_json, s, args.keep_trajectories) for s in seeds if not _done(name, chash, s)
        ]

    print(f"{len(cells) * len(seeds)} runs; {len(pending)} pending ({args.scenario})", flush=True)
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    failures: list[dict] = []
    if pending:
        with mp.get_context("spawn").Pool(min(args.procs, len(pending))) as pool:
            for i, (cell, seed, ok, err) in enumerate(pool.imap_unordered(_worker, pending), 1):
                if not ok:
                    failures.append({"cell": cell, "seed": seed, "error": err})
                    print(f"  FAIL {cell} seed={seed}: {err}", flush=True)
                if i % 10 == 0 or i == len(pending):
                    print(f"  {i}/{len(pending)} ({time.perf_counter() - t0:.0f} s)", flush=True)

    (RUNS_ROOT / "MANIFEST.json").write_text(
        json.dumps(
            {
                "experiment": "i24_sweep",
                "scenario": args.scenario,
                "base_config_hash": config_hash(base_cfg),
                "controller": CONTROLLER,
                "penetrations": list(PENETRATIONS),
                "compliances": list(COMPLIANCES),
                "cells": hashes,
                "seeds": seeds,
                "replicates": args.replicates,
                "metrics_args": _metrics_args(),
                "trajectories_kept": args.keep_trajectories,
                "failures": failures,
            },
            indent=2,
        )
    )
    print(f"done in {time.perf_counter() - t0:.0f} s; {len(failures)} failed")


if __name__ == "__main__":
    main()
