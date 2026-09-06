"""Single-seed controller comparison on the I-24 fitted arm (ROADMAP next step 4).

Runs the fitted arm (``scenarios/i24_replica_speedcal.yaml``) with no
controlled vehicles, with FollowerStopper and with the capacity-aware
FollowerStopper at the same penetration and compliance, one seed each, and
reports the standard metrics on the measured span (throughput at data
x = 2,200 m, travel time, speed spread, fuel, waves). One seed is a probe,
not a result: the 20-seed battery (docs/I24_SWEEP.md) is what a claim needs.
Output: ``artifacts/i24_controller_probe.json``. Run from the repo root::

    uv run --no-sync python scripts/i24_controller_probe.py [--penetration 0.05] [--procs 3]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_validate import _inputs, _span

from flowstate_core.config import ScenarioConfig, config_hash
from microsim.runner import _versions, run_micro
from validation.metrics import compute_metrics

REPO = Path(__file__).resolve().parents[1]
ARM_YAML = REPO / "scenarios" / "i24_replica_speedcal.yaml"
OUT = REPO / "artifacts" / "i24_controller_probe.json"
CONTROLLERS = (None, "follower_stopper", "follower_stopper_capacity")


def _job(args: tuple[str | None, float, float, int]) -> dict[str, Any]:
    controller, penetration, compliance, seed = args
    raw = yaml.safe_load(ARM_YAML.read_text())
    raw["av"] = {
        "penetration": penetration if controller else 0.0,
        "compliance": compliance,
        "controller": controller,
        "controller_params": {},
    }
    raw["name"] = f"i24_probe_{controller or 'baseline'}"
    cfg = ScenarioConfig.model_validate(raw)
    geo = _inputs()["geometry"]
    a, b = geo["sim_x_of_data_x"]["a"], geo["sim_x_of_data_x"]["b"]
    lo, hi = _span()
    with tempfile.TemporaryDirectory() as td:
        t0 = time.perf_counter()
        paths = run_micro(cfg, seed, Path(td))
        meta = json.loads(paths.meta.read_text())
        m = compute_metrics(paths.run_dir, x_ref=a + b * 2200.0, span=(a + b * lo, a + b * hi))
    return {
        "controller": controller or "baseline",
        "penetration": penetration if controller else 0.0,
        "compliance": compliance,
        "seed": seed,
        "config_hash": config_hash(cfg),
        "inserted_fraction": round(meta["n_vehicles_departed"] / meta["n_vehicles_planned"], 4),
        "n_avs": len(meta.get("av_ids", [])),
        "metrics": asdict(m),
        "wall_s": round(time.perf_counter() - t0, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--penetration", type=float, default=0.05)
    ap.add_argument("--compliance", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=6914975401685141156)
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    jobs = [(c, a.penetration, a.compliance, a.seed) for c in CONTROLLERS]
    with mp.get_context("spawn").Pool(min(a.procs, len(jobs))) as pool:
        rows = pool.map(_job, jobs)
    base = next(r for r in rows if r["controller"] == "baseline")["metrics"]
    for r in rows:
        m = r["metrics"]
        rel = {
            k: (round((m[k] - base[k]) / base[k], 4) if base[k] else None)
            for k in ("throughput_veh_h", "mean_tt_s", "sigma_v_temporal_ms", "fuel_ml_per_veh_km")
        }
        r["vs_baseline"] = rel
        print(
            f"  {r['controller']:<28} inserted={r['inserted_fraction']:.3f} AVs={r['n_avs']:4d} "
            f"throughput {m['throughput_veh_h']:.0f} ({rel['throughput_veh_h']:+.1%}) "
            f"tt {m['mean_tt_s']:.0f} s ({rel['mean_tt_s']:+.1%}) "
            f"sigma_v {m['sigma_v_temporal_ms']:.2f} ({rel['sigma_v_temporal_ms']:+.1%}) "
            f"fuel {m['fuel_ml_per_veh_km']:.1f} ({rel['fuel_ml_per_veh_km']:+.1%}) "
            f"waves {m['wave_count']:.0f}",
            flush=True,
        )
    out = {
        "schema_version": 1,
        "versions": _versions(),
        "arm": str(ARM_YAML.relative_to(REPO)),
        "note": "single seed per configuration — a probe, not a result (docs/I24_SWEEP.md carries the 20-seed battery)",
        "rows": rows,
    }
    a.out.write_text(json.dumps(out, indent=2))
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
