"""Is the US-101 wave-speed criterion failure a physics or a measurement problem?

The US-101 replica fails the 14-22 km/h emergent-wave-speed criterion, reporting
roughly 11 km/h (docs/M3_US101_VALIDATION.md). Two observations motivate this
check:

* Newell theory applied to the *fitted* parameters predicts a congested wave
  speed of (s0 + L)/T = (2.02 + 4.5..5.0) / 1.285 = 18.3-19.7 km/h — inside the
  empirical band, and close to the 15.6 km/h measured in the NGSIM data itself.
* The same simulated runs yield 5.8 km/h site-clipped versus 10.7 km/h at stripe
  level, i.e. the answer depends strongly on how a 640 m window is measured.

So: run the SAME calibrated US-101 fleet on the 10 km corridor geometry, where a
backward front has room to be tracked over kilometres, and measure the wave
speed there. If it lands in the band, the criterion failure at the replica is a
site-length/measurement artifact rather than a calibration or physics failure.
If it does not, the calibration is genuinely off and better data is required.

Usage::

    uv run --no-sync python scripts/wave_speed_sitelength.py [--procs 6] [--replicates 10]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNS_ROOT = REPO / "runs" / "wave_speed_sitelength"
RING_CIRCUMFERENCE_M = 1500.0
DENSITIES_VEH_KM = (40.0, 60.0, 80.0, 100.0)
"""Inside the analytic instability band for the fitted params (31.8-141.5 veh/km)."""


FLEETS = {"us101": "artifacts/idm_us101.json", "i24": "artifacts/idm_i24.json"}
RELATIVE_FRAC = 0.5
"""Relative-threshold detector fraction (ROADMAP D1): jam = v < 0.5 × p90 of
the field, which resolves stripes where the absolute 40 km/h threshold labels
the whole ring as one jam (80-100 veh/km)."""


def build_config(density_veh_km: float, fleet: str = "us101") -> dict:
    """Ring at a prescribed density, driven by a calibrated IDM population."""
    n = round(density_veh_km * RING_CIRCUMFERENCE_M / 1000.0)
    return {
        "name": f"ring_{fleet}fleet_{density_veh_km:.0f}",
        "tier": "micro",
        "network": {
            "kind": "ring",
            "circumference_m": RING_CIRCUMFERENCE_M,
            "n_vehicles": n,
        },
        "fleet": {"model": "IDM", "idm_calibration": FLEETS[fleet]},
        "av": {"penetration": 0.0, "compliance": 1.0, "controller": None},
        "sim": {"duration_s": 900.0, "warmup_s": 180.0},
        "seed": 42,
        "replicates": 5,
    }


def _worker(payload: tuple[str, dict, int]) -> tuple[str, int, bool, str]:
    cell, cfg_json, seed = payload
    try:
        from flowstate_core.config import ScenarioConfig
        from microsim.runner import run_micro

        run_micro(ScenarioConfig.model_validate(cfg_json), seed, RUNS_ROOT / cell)
        return cell, seed, True, ""
    except Exception as exc:
        return cell, seed, False, f"{type(exc).__name__}: {exc}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--replicates", type=int, default=10)
    ap.add_argument("--fleet", choices=sorted(FLEETS), default="us101")
    args = ap.parse_args()

    import numpy as np

    from flowstate_core.config import ScenarioConfig, config_hash
    from flowstate_core.rng import spawn_seeds

    results: dict[str, dict] = {}
    pending: list[tuple[str, dict, int]] = []
    meta: dict[str, tuple[dict, str, list[int]]] = {}
    for dens in DENSITIES_VEH_KM:
        cfg_json = build_config(dens, args.fleet)
        cfg = ScenarioConfig.model_validate(cfg_json)
        chash = config_hash(cfg)
        seeds = spawn_seeds(cfg.seed, args.replicates)
        cell = cfg_json["name"]
        meta[cell] = (cfg_json, chash, seeds)
        pending += [
            (cell, cfg_json, s)
            for s in seeds
            if not (RUNS_ROOT / cell / chash / str(s) / "meta.json").is_file()
        ]
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"{sum(len(m[2]) for m in meta.values())} runs; {len(pending)} pending")
    t0 = time.perf_counter()
    if pending:
        with mp.get_context("spawn").Pool(min(args.procs, len(pending))) as pool:
            for cell, seed, ok, err in pool.imap_unordered(_worker, pending):
                if not ok:
                    print(f"  FAIL {cell} seed={seed}: {err}")
    print(f"runs done in {time.perf_counter() - t0:.0f} s")

    import pandas as pd

    from validation.fields import speed_field
    from validation.waves import detect_waves

    all_speeds: list[float] = []

    def _stats(vals: list[float]) -> dict:
        a = np.asarray(vals, dtype=float)
        return {
            "n_backward_fronts": int(a.size),
            "mean_kmh": float(a.mean()) if a.size else None,
            "median_kmh": float(np.median(a)) if a.size else None,
            "fraction_in_band": float(np.mean((a >= 14.0) & (a <= 22.0))) if a.size else None,
        }

    for cell, (cfg_json, chash, seeds) in meta.items():
        got_cell: list[float] = []
        got_rel: list[float] = []
        for s in seeds:
            with open(RUNS_ROOT / cell / chash / str(s) / "trajectories.parquet", "rb") as fh:
                traj = pd.read_parquet(fh)
            field = speed_field(traj)
            waves = detect_waves(field)
            got_cell += [-w.speed_ms * 3.6 for w in waves.waves if w.speed_ms < 0.0]
            waves_rel = detect_waves(field, relative_frac=RELATIVE_FRAC)
            got_rel += [-w.speed_ms * 3.6 for w in waves_rel.waves if w.speed_ms < 0.0]
        results[cell] = {
            "density_veh_km": cfg_json["network"]["n_vehicles"] / (RING_CIRCUMFERENCE_M / 1000.0),
            "n_vehicles": cfg_json["network"]["n_vehicles"],
            "config_hash": chash,
            **_stats(got_cell),
            "relative_detector": {"relative_frac": RELATIVE_FRAC, **_stats(got_rel)},
        }
        all_speeds += got_cell
        print(
            f"  {cell}: absolute n={len(got_cell)} mean={results[cell]['mean_kmh']} | "
            f"relative n={len(got_rel)} mean={results[cell]['relative_detector']['mean_kmh']}"
        )

    speeds_kmh = all_speeds
    arr = np.asarray(speeds_kmh, dtype=float)
    summary = {
        "experiment": "wave_speed_sitelength",
        "fleet": FLEETS[args.fleet],
        "question": f"does the calibrated {args.fleet} fleet produce 14-22 km/h waves at prescribed densities?",
        "scenario": f"1500 m ring at prescribed densities + {FLEETS[args.fleet]} fleet, IDM, no AVs",
        "detectors": "absolute 40 km/h threshold (standard) and relative 0.5 x p90 (ROADMAP D1)",
        "per_density": results,
        "n_backward_fronts": int(arr.size),
        "wave_speed_kmh": {
            "mean": float(arr.mean()) if arr.size else None,
            "median": float(np.median(arr)) if arr.size else None,
            "p10": float(np.percentile(arr, 10)) if arr.size else None,
            "p90": float(np.percentile(arr, 90)) if arr.size else None,
        },
        "empirical_band_kmh": [14.0, 22.0],
        "fraction_in_band": (float(np.mean((arr >= 14.0) & (arr <= 22.0))) if arr.size else None),
        "newell_prediction_kmh": [18.3, 19.7],
        "us101_replica_measured_kmh": 10.7,
    }
    out_name = (
        "wave_speed_sitelength.json"
        if args.fleet == "us101"
        else f"wave_speed_sitelength_{args.fleet}.json"
    )
    (REPO / "artifacts" / out_name).write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "per_density"}, indent=2))


if __name__ == "__main__":
    main()
