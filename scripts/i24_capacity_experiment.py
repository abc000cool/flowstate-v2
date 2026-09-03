"""Can the calibrated I-24 fleet carry the corrected demand? Two experiments.

docs/I24_VALIDATION.md §3 attributes two of the three corridor failures to
insertion: only 82–84% of the coverage-corrected demand enters the replica.
The corrected mainline inflow is ≈ 2.05 veh/s over four lanes, ≈ 1,840 veh/h
per lane, at the fitted fundamental diagram's capacity lower bound
(artifacts/fd_i24.json). Before changing insertion mechanics this script
asks the prior question.

**Part A — fleet capacity.** A straight 4-lane corridor with the replica's
fleet (``artifacts/idm_i24.json`` population, replica lane-change
parameters) and no ramps or boundary, driven at fixed per-lane inflows. The
sustained throughput at a mid-corridor cross-section, and the inserted
fraction, give the model's capacity per lane. If it is below the corrected
demand, the failure is capacity (calibration or lane-change behaviour), not
insertion.

**Part B — insertion variants on the replica.** The corrected arm with the
runner's insertion spread over 1 or 3 leading edges, one seed each: inserted
fraction, cross-section throughputs and segment speeds.

Writes ``artifacts/i24_capacity_experiment.json``. Every number is a
seeded run at the pinned SUMO; nothing is tuned.

Run: ``uv run --no-sync python scripts/i24_capacity_experiment.py --part all --procs 8``
(``--quick`` for a smoke test).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from flowstate_core.config import CorridorNetwork, ScenarioConfig
from flowstate_core.units import h_to_s, veh_h_to_veh_s
from microsim import load_scenario, run_micro
from microsim.runner import _versions

REPO = Path(__file__).resolve().parents[1]
REPLICA_YAML = REPO / "scenarios" / "i24_replica_corrected.yaml"
OUT = REPO / "artifacts" / "i24_capacity_experiment.json"

LEVELS_VEH_H_LANE = (1400.0, 1600.0, 1800.0, 2000.0, 2200.0, 2400.0)
SEEDS = (1, 2, 3)
LANES = 4
LENGTH_M = 4000.0
X_REF_M = 3000.0
DURATION_S = 1800.0
WARMUP_S = 300.0
SPREADS = (1, 3)
SEGMENT_M = 1000.0


def crossings_per_hour(traj: pd.DataFrame, x_ref: float, t_lo: float, t_hi: float) -> float:
    """Vehicles whose trajectory first reaches ``x_ref`` within [t_lo, t_hi), per hour."""
    df = traj[["t", "veh_id", "x"]]
    beyond = df[df["x"] >= x_ref].groupby("veh_id")["t"].min()
    n = int(((beyond >= t_lo) & (beyond < t_hi)).sum())
    return n * h_to_s(1.0) / (t_hi - t_lo)


def segment_speeds(traj: pd.DataFrame, t_lo: float, seg_m: float) -> dict[str, float]:
    df = traj[traj["t"] >= t_lo]
    seg = (df["x"] // seg_m).astype(int)
    return {f"{int(k * seg_m)}": round(float(v), 3) for k, v in df.groupby(seg)["v"].mean().items()}


def _capacity_job(args: tuple[float, int, bool]) -> dict[str, Any]:
    q_lane, seed, quick = args
    base = load_scenario(REPLICA_YAML)
    duration = 420.0 if quick else DURATION_S
    warmup = 120.0 if quick else WARMUP_S
    cfg = ScenarioConfig(
        name="i24_fleet_capacity",
        tier="micro",
        network=CorridorNetwork(
            length_m=LENGTH_M, lanes=LANES, inflow=[(0.0, veh_h_to_veh_s(q_lane * LANES))]
        ),
        fleet=base.fleet,
        av=base.av,
        sim=base.sim.model_copy(update={"duration_s": duration, "warmup_s": warmup}),
        perturbation=None,
        seed=seed,
        replicates=1,
    )
    with tempfile.TemporaryDirectory() as td:
        t0 = time.perf_counter()
        paths = run_micro(cfg, seed, Path(td))
        meta = json.loads(paths.meta.read_text())
        traj = pd.read_parquet(paths.trajectories, columns=["t", "veh_id", "x", "v"])
    planned, departed = meta["n_vehicles_planned"], meta["n_vehicles_departed"]
    mid = traj[(traj["x"] >= X_REF_M - 500) & (traj["x"] < X_REF_M + 500) & (traj["t"] >= warmup)]
    return {
        "q_lane_veh_h": q_lane,
        "seed": seed,
        "config_hash": meta["config_hash"],
        "planned": planned,
        "departed": departed,
        "inserted_fraction": round(departed / planned, 4) if planned else None,
        "throughput_veh_h_lane": round(
            crossings_per_hour(traj, X_REF_M, warmup, duration) / LANES, 1
        ),
        "mean_speed_ms_at_ref": round(float(mid["v"].mean()), 3) if len(mid) else None,
        "wall_s": round(time.perf_counter() - t0, 1),
    }


def _insertion_job(args: tuple[int, int, bool]) -> dict[str, Any]:
    spread, seed, quick = args
    cfg = load_scenario(REPLICA_YAML)
    if quick:
        cfg = cfg.model_copy(deep=True)
        cfg.sim.duration_s = 900.0
        cfg.sim.warmup_s = 300.0
    with tempfile.TemporaryDirectory() as td:
        t0 = time.perf_counter()
        paths = run_micro(cfg, seed, Path(td), depart_edge_spread=spread)
        meta = json.loads(paths.meta.read_text())
        traj = pd.read_parquet(paths.trajectories, columns=["t", "veh_id", "x", "v"])
    planned, departed = meta["n_vehicles_planned"], meta["n_vehicles_departed"]
    t_lo, t_hi = cfg.sim.warmup_s, cfg.sim.duration_s
    x_max = float(traj["x"].max())
    refs = [round(x_max * f) for f in (0.3, 0.5, 0.7, 0.9)]
    return {
        "depart_edge_spread": spread,
        "seed": seed,
        "config_hash": meta["config_hash"],
        "planned": planned,
        "departed": departed,
        "inserted_fraction": round(departed / planned, 4) if planned else None,
        "ramps": meta.get("ramps"),
        "throughput_veh_h": {
            str(x): round(crossings_per_hour(traj, x, t_lo, t_hi), 1) for x in refs
        },
        "segment_mean_speed_ms": segment_speeds(traj, t_lo, SEGMENT_M),
        "wall_s": round(time.perf_counter() - t0, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--part", choices=("capacity", "insertion", "all"), default="all")
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--quick", action="store_true", help="short durations, one level, one seed")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    levels = (1800.0,) if a.quick else LEVELS_VEH_H_LANE
    seeds = (1,) if a.quick else SEEDS
    spreads = (1,) if a.quick else SPREADS
    result: dict[str, Any] = {
        "schema_version": 1,
        "versions": _versions(),
        "replica": str(REPLICA_YAML.relative_to(REPO)),
        "quick": a.quick,
        "capacity": None,
        "insertion": None,
    }
    ctx = mp.get_context("spawn")
    if a.part in ("capacity", "all"):
        jobs = [(q, s, a.quick) for q in levels for s in seeds]
        with ctx.Pool(min(a.procs, len(jobs))) as pool:
            rows = pool.map(_capacity_job, jobs)
        by_level: dict[float, list[dict[str, Any]]] = {}
        for r in rows:
            by_level.setdefault(r["q_lane_veh_h"], []).append(r)
        summary = []
        for q, rs in sorted(by_level.items()):
            thr = np.array([r["throughput_veh_h_lane"] for r in rs])
            ins = np.array([r["inserted_fraction"] for r in rs], dtype=float)
            summary.append(
                {
                    "q_lane_veh_h": q,
                    "throughput_veh_h_lane_mean": round(float(thr.mean()), 1),
                    "throughput_veh_h_lane_min": round(float(thr.min()), 1),
                    "inserted_fraction_mean": round(float(ins.mean()), 4),
                    "mean_speed_ms_at_ref": round(
                        float(np.mean([r["mean_speed_ms_at_ref"] or np.nan for r in rs])), 3
                    ),
                    "n": len(rs),
                }
            )
        sustained = [s for s in summary if s["inserted_fraction_mean"] >= 0.98]
        result["capacity"] = {
            "corridor": {"length_m": LENGTH_M, "lanes": LANES, "x_ref_m": X_REF_M},
            "levels": summary,
            "runs": rows,
            "max_level_fully_inserted_veh_h_lane": max(s["q_lane_veh_h"] for s in sustained)
            if sustained
            else None,
            "max_throughput_veh_h_lane": max(s["throughput_veh_h_lane_mean"] for s in summary),
        }
        for s in summary:
            print(
                f"  capacity q={s['q_lane_veh_h']:.0f}/lane -> throughput {s['throughput_veh_h_lane_mean']:.0f}/lane, "
                f"inserted {s['inserted_fraction_mean']:.3f}, v_ref {s['mean_speed_ms_at_ref']:.1f} m/s"
            )
    if a.part in ("insertion", "all"):
        jobs2 = [(sp, s, a.quick) for sp in spreads for s in seeds]
        with ctx.Pool(min(a.procs, len(jobs2))) as pool:
            rows2 = pool.map(_insertion_job, jobs2)
        result["insertion"] = {"runs": rows2}
        for r in rows2:
            print(
                f"  insertion spread={r['depart_edge_spread']} seed={r['seed']} inserted {r['inserted_fraction']:.3f} "
                f"throughput {r['throughput_veh_h']}"
            )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=1))
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
