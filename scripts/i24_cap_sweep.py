"""Headway-cap sweep of the capacity-aware FollowerStopper on the I-24 fitted arm.

Configurations on one arm, ``--replicates`` seeds each (common random
numbers across configurations): the uncontrolled baseline, FollowerStopper
at the literature defaults, and the capacity-aware FollowerStopper at each
``--h-max`` value, all at the same penetration and compliance. Every run's
metrics on the measured span (throughput at data x = 2,200 m, travel time,
speed spread, fuel, waves) are aggregated with t-distribution 95% intervals
and contrasted with the baseline seed by seed (paired). Trajectories are
deleted once the metrics are computed (``--keep-trajectories`` keeps them).
Resumable per seed: replicates with ``metrics.json`` are not rerun, replicates
with trajectories but no metrics are only analysed. Simulation and analysis
each run in their own process pool (``--procs``, ``--analysis-procs``). The
summary is rewritten after every configuration (``complete: false`` until
the last one), so a run cut short still leaves the finished cells and their
paired contrasts.

Output: ``artifacts/i24_cap_sweep_summary.json``. Run from the repo root::

    uv run --no-sync python scripts/i24_cap_sweep.py --procs 30 --replicates 20
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_validate import _inputs, _span

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from microsim.runner import _versions, run_micro
from validation.metrics import Metrics, aggregate, compute_metrics

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "artifacts" / "i24_cap_sweep_summary.json"
RUN_ROOT = REPO / "runs" / "i24_cap_sweep"
METRIC_KEYS = (
    "throughput_veh_h",
    "mean_tt_s",
    "p90_tt_s",
    "sigma_v_temporal_ms",
    "sigma_v_spatial_ms",
    "fuel_ml_per_veh_km",
    "wave_count",
    "wave_amplitude_ms",
)


def _config(
    arm_yaml: Path,
    controller: str | None,
    params: dict[str, float],
    pen: float,
    comp: float,
    n: int,
) -> ScenarioConfig:
    raw = yaml.safe_load(arm_yaml.read_text())
    raw["av"] = {
        "penetration": pen if controller else 0.0,
        "compliance": comp,
        "controller": controller,
        "controller_params": params,
    }
    raw["replicates"] = n
    label = controller or "baseline"
    if params:
        label += "_" + "_".join(f"{k}{v:g}" for k, v in sorted(params.items()))
    raw["name"] = f"i24_cap_{label}"
    return ScenarioConfig.model_validate(raw)


def _run_one(args: tuple[ScenarioConfig, int, str]) -> str:
    """Pool worker: one replicate into ``out_root/<hash>/<seed>/``."""
    cfg, seed, out_root = args
    return str(run_micro(cfg, seed, out_root).run_dir)


def _analyse_one(args: tuple[str, float, float, float, bool]) -> None:
    """Pool worker: metrics.json for one run directory, trajectories dropped unless kept."""
    run_dir, x_ref, span_lo, span_hi, keep = args
    d = Path(run_dir)
    m = compute_metrics(d, x_ref=x_ref, span=(span_lo, span_hi))
    (d / "metrics.json").write_text(json.dumps(asdict(m)))
    if not keep:
        (d / "trajectories.parquet").unlink(missing_ok=True)


def _metrics_for(
    cfg: ScenarioConfig, out_root: Path, procs: int, analysis_procs: int, keep: bool
) -> dict[int, Metrics]:
    """Metrics of every replicate of ``cfg``; simulates and analyses only the missing seeds.

    Resumable per seed: a seed whose ``metrics.json`` exists is not touched, a
    seed whose run directory holds trajectories but no metrics is analysed
    only, and the rest are simulated in a spawn pool and then analysed in a
    second pool (the analysis is the sequential bottleneck otherwise: one
    replica's metrics take minutes on a single core).
    """
    geo = _inputs()["geometry"]
    a, b = geo["sim_x_of_data_x"]["a"], geo["sim_x_of_data_x"]["b"]
    lo, hi = _span()
    x_ref, span_lo, span_hi = a + b * 2200.0, a + b * lo, a + b * hi
    seeds = spawn_seeds(cfg.seed, cfg.replicates)
    tree = out_root / config_hash(cfg)
    missing = [s for s in seeds if not (tree / str(s) / "metrics.json").is_file()]
    to_run = [s for s in missing if not (tree / str(s) / "trajectories.parquet").is_file()]
    ctx = mp.get_context("spawn")
    if to_run:
        with ctx.Pool(max(1, min(procs, len(to_run)))) as pool:
            pool.map(_run_one, [(cfg, s, str(out_root)) for s in to_run], chunksize=1)
    if missing:
        jobs = [(str(tree / str(s)), x_ref, span_lo, span_hi, keep) for s in missing]
        with ctx.Pool(max(1, min(analysis_procs, len(jobs)))) as pool:
            pool.map(_analyse_one, jobs, chunksize=1)
    out: dict[int, Metrics] = {}
    for s in seeds:
        out[s] = Metrics(**json.loads((tree / str(s) / "metrics.json").read_text()))
    return out


def _paired(base: dict[int, Metrics], other: dict[int, Metrics], key: str) -> dict[str, Any]:
    seeds = sorted(set(base) & set(other))
    d = np.array([getattr(other[s], key) - getattr(base[s], key) for s in seeds], dtype=float)
    d = d[np.isfinite(d)]
    n = len(d)
    if n < 2:
        return {"n": n, "mean": None, "lo95": None, "hi95": None, "rel": None}
    from scipy.stats import t as student_t

    half = float(student_t.ppf(0.975, n - 1) * d.std(ddof=1) / math.sqrt(n))
    base_mean = float(np.mean([getattr(base[s], key) for s in seeds]))
    return {
        "n": n,
        "mean": float(d.mean()),
        "lo95": float(d.mean() - half),
        "hi95": float(d.mean() + half),
        "rel": float(d.mean() / base_mean) if base_mean else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--arm-yaml", type=Path, default=REPO / "scenarios" / "i24_replica_speedcal.yaml"
    )
    ap.add_argument("--h-max", type=float, nargs="*", default=[1.3, 1.5, 1.7, 2.0])
    ap.add_argument("--penetration", type=float, default=0.05)
    ap.add_argument("--compliance", type=float, default=1.0)
    ap.add_argument("--replicates", type=int, default=20)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument(
        "--analysis-procs",
        type=int,
        default=None,
        help="processes for the per-run metrics (default: min(procs, 8))",
    )
    ap.add_argument("--keep-trajectories", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--run-root", type=Path, default=RUN_ROOT)
    a = ap.parse_args()
    configs: list[tuple[str, ScenarioConfig]] = [
        ("baseline", _config(a.arm_yaml, None, {}, a.penetration, a.compliance, a.replicates)),
        (
            "follower_stopper",
            _config(a.arm_yaml, "follower_stopper", {}, a.penetration, a.compliance, a.replicates),
        ),
    ]
    for h in a.h_max:
        configs.append(
            (
                f"follower_stopper_capacity_h{h:g}",
                _config(
                    a.arm_yaml,
                    "follower_stopper_capacity",
                    {"h_max_s": h},
                    a.penetration,
                    a.compliance,
                    a.replicates,
                ),
            )
        )
    t0 = time.perf_counter()
    results: dict[str, dict[int, Metrics]] = {}
    cells: list[dict[str, Any]] = []

    def write(complete: bool) -> None:
        """Write the summary for the cells done so far (partial until ``complete``)."""
        base = results.get("baseline")
        for c in cells:
            if c["label"] != "baseline" and base is not None:
                c["vs_baseline_paired"] = {
                    k: _paired(base, results[c["label"]], k) for k in METRIC_KEYS
                }
        out = {
            "schema_version": 1,
            "versions": _versions(),
            "arm": str(a.arm_yaml.relative_to(REPO))
            if a.arm_yaml.is_relative_to(REPO)
            else str(a.arm_yaml),
            "penetration": a.penetration,
            "compliance": a.compliance,
            "n_seeds": a.replicates,
            "h_max_values": a.h_max,
            "cells": cells,
            "complete": complete,
            "n_configs_planned": len(configs),
            "note": "common random numbers across configurations; vs_baseline_paired = per-seed differences with t-distribution 95% intervals"
            + (
                ""
                if complete
                else "; PARTIAL: written after each configuration, the sweep was still running"
            ),
            "wall_s": round(time.perf_counter() - t0, 1),
        }
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, indent=2))

    for label, cfg in configs:
        t1 = time.perf_counter()
        results[label] = _metrics_for(
            cfg, a.run_root, a.procs, a.analysis_procs or min(a.procs, 8), a.keep_trajectories
        )
        agg = aggregate(list(results[label].values()))
        cells.append(
            {
                "label": label,
                "config_hash": config_hash(cfg),
                "controller": cfg.av.controller,
                "controller_params": dict(cfg.av.controller_params),
                "penetration": cfg.av.penetration,
                "compliance": cfg.av.compliance,
                "n_seeds": len(results[label]),
                "metrics": {
                    k: {"mean": agg[k].mean, "lo95": agg[k].lo95, "hi95": agg[k].hi95}
                    for k in METRIC_KEYS
                },
                "wall_s": round(time.perf_counter() - t1, 1),
            }
        )
        print(
            f"  {label:<36} n={len(results[label])} throughput {agg['throughput_veh_h'].mean:.0f} sigma_v {agg['sigma_v_temporal_ms'].mean:.2f} ({cells[-1]['wall_s']} s)",
            flush=True,
        )
        write(complete=False)
    write(complete=True)
    print(f"-> {a.out} ({round(time.perf_counter() - t0, 1)} s)")


if __name__ == "__main__":
    main()
