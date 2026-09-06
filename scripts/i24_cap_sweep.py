"""Headway-cap sweep of the capacity-aware FollowerStopper on the I-24 fitted arm.

Configurations on one arm, ``--replicates`` seeds each (common random
numbers across configurations): the uncontrolled baseline, FollowerStopper
at the literature defaults, and the capacity-aware FollowerStopper at each
``--h-max`` value, all at the same penetration and compliance. Every run's
metrics on the measured span (throughput at data x = 2,200 m, travel time,
speed spread, fuel, waves) are aggregated with t-distribution 95% intervals
and contrasted with the baseline seed by seed (paired). Trajectories are
deleted once the metrics are computed (``--keep-trajectories`` keeps them).
Resumable: a configuration whose run tree already holds every replicate's
``metrics.json`` is not rerun.

Output: ``artifacts/i24_cap_sweep_summary.json``. Run from the repo root::

    uv run --no-sync python scripts/i24_cap_sweep.py --procs 30 --replicates 20
"""

from __future__ import annotations

import argparse
import json
import math
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
from microsim.runner import _versions, run_replicates
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


def _metrics_for(cfg: ScenarioConfig, out_root: Path, procs: int, keep: bool) -> dict[int, Metrics]:
    geo = _inputs()["geometry"]
    a, b = geo["sim_x_of_data_x"]["a"], geo["sim_x_of_data_x"]["b"]
    lo, hi = _span()
    seeds = spawn_seeds(cfg.seed, cfg.replicates)
    tree = out_root / config_hash(cfg)
    done = all((tree / str(s) / "metrics.json").is_file() for s in seeds)
    if not done:
        paths = run_replicates(cfg, out_root, n_procs=min(procs, cfg.replicates))
        for p in paths:
            m = compute_metrics(p.run_dir, x_ref=a + b * 2200.0, span=(a + b * lo, a + b * hi))
            (p.run_dir / "metrics.json").write_text(json.dumps(asdict(m)))
            if not keep:
                (p.run_dir / "trajectories.parquet").unlink(missing_ok=True)
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
    for label, cfg in configs:
        t1 = time.perf_counter()
        results[label] = _metrics_for(cfg, a.run_root, a.procs, a.keep_trajectories)
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
    base = results["baseline"]
    for c in cells:
        if c["label"] == "baseline":
            continue
        c["vs_baseline_paired"] = {k: _paired(base, results[c["label"]], k) for k in METRIC_KEYS}
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
        "note": "common random numbers across configurations; vs_baseline_paired = per-seed differences with t-distribution 95% intervals",
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=2))
    print(f"-> {a.out} ({out['wall_s']} s)")


if __name__ == "__main__":
    main()
