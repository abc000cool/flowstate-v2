"""Fit one demand scale factor for the I-24 replica on observed speeds.

Why a third demand arm. The instrument tracks about half of vehicle-time at
the peak, so the tracked counts are a lower bound on demand (arm 1) and the
coverage-corrected counts (arm 2) are an estimate whose peak, 1,840 veh/h
per lane at the entry, sits at the fitted fundamental diagram's capacity
lower bound: the replica cannot insert it and queues from the first window
(docs/I24_VALIDATION.md §3, docs/I24_DATA.md). Speeds, unlike counts, are
robust to coverage. This script therefore calibrates a single scale factor
``s`` applied to the tracked mainline and on-ramp inflows (exit fractions
and the measured boundary unchanged) by minimising the segment-speed RMSPE
over the FIRST HOUR of the study period (06:30–07:30 CST, windows 0–11), and
reports the SECOND hour (07:30–08:30, windows 12–23) as an out-of-sample
check. Standard demand calibration (FHWA Vol. III), done out-of-sample so a
pass on the second hour is not a fit.

Method: two rounds of a parallel grid (coarse, then refined around the best),
one seeded replicate per scale (the first replicate seed of the scenario),
ties broken toward the smaller scale. Nothing else is tuned.

Outputs ``artifacts/demand_scale_i24.json`` and, with ``--write-scenario``,
``scenarios/i24_replica_speedcal.yaml``.

Run: ``uv run --no-sync python scripts/i24_fit_demand_scale.py --procs 7 --write-scenario``
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from i24_validate import (
    SECTIONS_M,
    WINDOW_S,
    _inputs,
    _segment_speeds,
    _sim_frame,
    _span,
    crossings_per_window,
)

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from microsim.runner import _versions, run_micro
from validation.metrics import rmspe

REPO = Path(__file__).resolve().parents[1]
TRACKED_YAML = REPO / "scenarios" / "i24_replica.yaml"
OBSERVED = REPO / "artifacts" / "i24_validation_observed.json"
OUT = REPO / "artifacts" / "demand_scale_i24.json"
SCENARIO_OUT = REPO / "scenarios" / "i24_replica_speedcal.yaml"
FLEET_ARTIFACT = "artifacts/idm_i24_capacity.json"  # FHWA step 1 output (docs/I24_CAPACITY.md)

COARSE = (1.0, 1.15, 1.3, 1.45, 1.6, 1.75, 1.9)
REFINE_STEP = 0.025
REFINE_HALF_WIDTH = 3  # ± 3 steps around the coarse optimum
TRAIN_WINDOWS = range(0, 12)
TEST_WINDOWS = range(12, 24)


def scaled_config(scale: float, fleet_artifact: str = FLEET_ARTIFACT) -> dict[str, Any]:
    """The tracked-demand scenario dict with inflows × scale and the step-1 fleet."""
    raw = yaml.safe_load(TRACKED_YAML.read_text())
    raw["fleet"]["idm_calibration"] = fleet_artifact
    net = raw["network"]
    net["inflow"] = [[t, round(q * scale, 6)] for t, q in net["inflow"]]
    for ramp in net.get("ramps", []):
        if ramp.get("kind") == "on" and ramp.get("inflow"):
            ramp["inflow"] = [[t, round(q * scale, 6)] for t, q in ramp["inflow"]]
    raw["name"] = "i24_replica_speedcal"
    return raw


def _rmspe_windows(sim: np.ndarray, obs: np.ndarray, windows: range) -> float:
    s = sim[list(windows)].ravel()
    o = obs[list(windows)].ravel()
    ok = np.isfinite(s) & np.isfinite(o)
    return float(rmspe(s[ok], o[ok]))


def _job(args: tuple[float, int, str]) -> dict[str, Any]:
    scale, seed, fleet_artifact = args
    geo = _inputs()["geometry"]
    a, b = geo["sim_x_of_data_x"]["a"], geo["sim_x_of_data_x"]["b"]
    _span_lo, span_hi = _span()
    obs = np.array(json.loads(OBSERVED.read_text())["segment_speeds_ms"], dtype=float)
    n_win = obs.shape[0]
    cfg = ScenarioConfig.model_validate(scaled_config(scale, fleet_artifact))
    with tempfile.TemporaryDirectory() as td:
        t0 = time.perf_counter()
        paths = run_micro(cfg, seed, Path(td))
        meta = json.loads(paths.meta.read_text())
        df = _sim_frame(paths.run_dir, a, b)
        seg = _segment_speeds(df, span_hi, n_win)
        counts = [crossings_per_window(df, s, 0.0, n_win * WINDOW_S) for s in SECTIONS_M]
        wall = time.perf_counter() - t0
    return {
        "scale": scale,
        "seed": seed,
        "config_hash": meta["config_hash"],
        "inserted_fraction": round(meta["n_vehicles_departed"] / meta["n_vehicles_planned"], 4),
        "rmspe_train": round(_rmspe_windows(seg, obs, TRAIN_WINDOWS), 4),
        "rmspe_test": round(_rmspe_windows(seg, obs, TEST_WINDOWS), 4),
        "rmspe_all": round(_rmspe_windows(seg, obs, range(n_win)), 4),
        "segment_speeds_ms": np.round(seg, 3).tolist(),
        "counts_per_window": counts,
        "wall_s": round(wall, 1),
    }


def _best(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return min(rows, key=lambda r: (r["rmspe_train"], r["scale"]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--write-scenario", action="store_true")
    ap.add_argument("--coarse-only", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--fleet-artifact", default=FLEET_ARTIFACT)
    args = ap.parse_args()
    fleet = args.fleet_artifact
    base = ScenarioConfig.model_validate(scaled_config(1.0, fleet))
    seed = spawn_seeds(base.seed, base.replicates)[0]
    ctx = mp.get_context("spawn")
    rows: list[dict[str, Any]] = []
    with ctx.Pool(min(args.procs, len(COARSE))) as pool:
        rows += pool.map(_job, [(s, seed, fleet) for s in COARSE])
    for r in sorted(rows, key=lambda r: r["scale"]):
        print(
            f"  s={r['scale']:.3f} inserted={r['inserted_fraction']:.3f} rmspe train={r['rmspe_train']:.3f} test={r['rmspe_test']:.3f}"
        )
    best = _best(rows)
    if not args.coarse_only:
        fine = [
            round(best["scale"] + k * REFINE_STEP, 3)
            for k in range(-REFINE_HALF_WIDTH, REFINE_HALF_WIDTH + 1)
            if k != 0 and best["scale"] + k * REFINE_STEP >= 1.0
        ]
        with ctx.Pool(min(args.procs, len(fine))) as pool:
            rows += pool.map(_job, [(s, seed, fleet) for s in fine])
        best = _best(rows)
        for r in sorted(rows, key=lambda r: r["scale"]):
            print(
                f"  s={r['scale']:.3f} inserted={r['inserted_fraction']:.3f} rmspe train={r['rmspe_train']:.3f} test={r['rmspe_test']:.3f}"
            )
    print(
        f"best scale {best['scale']:.3f}: rmspe train {best['rmspe_train']:.3f}, test {best['rmspe_test']:.3f}, inserted {best['inserted_fraction']:.3f}"
    )
    result = {
        "schema_version": 1,
        "versions": _versions(),
        "base_scenario": str(TRACKED_YAML.relative_to(REPO)),
        "fleet_artifact": fleet,
        "objective": "segment-speed RMSPE, windows 0-11 (06:30-07:30 CST); windows 12-23 held out",
        "seed": seed,
        "grid": sorted(rows, key=lambda r: r["scale"]),
        "best": {
            k: best[k]
            for k in (
                "scale",
                "config_hash",
                "inserted_fraction",
                "rmspe_train",
                "rmspe_test",
                "rmspe_all",
            )
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1))
    print(f"-> {args.out}")
    if args.write_scenario:
        raw = scaled_config(best["scale"], fleet)
        cfg = ScenarioConfig.model_validate(raw)
        header = (
            "# i24_replica_speedcal — the tracked-demand replica with mainline and on-ramp\n"
            f"# inflows multiplied by s = {best['scale']:.3f}, fitted by scripts/i24_fit_demand_scale.py\n"
            "# on observed segment speeds over 06:30-07:30 CST only (windows 0-11); 07:30-08:30\n"
            f"# is held out. Fleet: {fleet} (capacity-calibrated, docs/I24_CAPACITY.md).\n"
            "# Exit fractions and the measured boundary are unchanged. See\n"
            f"# artifacts/demand_scale_i24.json (rmspe train {best['rmspe_train']:.3f}, test {best['rmspe_test']:.3f}).\n"
            f"# config hash {config_hash(cfg)}; seeded=False.\n"
        )
        SCENARIO_OUT.write_text(header + yaml.safe_dump(raw, sort_keys=False))
        print(f"-> {SCENARIO_OUT} ({config_hash(cfg)})")


if __name__ == "__main__":
    main()
