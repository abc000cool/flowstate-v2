"""Analyse the JAD oracle-realism comparison (scripts/jad_oracle_experiment.py).

Computes per-replicate metrics, marginal 95% CIs, and paired per-seed deltas
against the baseline (valid because all cells share one seed list). Writes
``runs/pi_retune/analysis.json`` and the committed summary
``artifacts/jad_oracle_summary.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNS_ROOT = REPO / "runs" / "jad_oracle"
X_REF = 7000.0
SPAN = (2000.0, 11500.0)
FIELDS = (
    "throughput_veh_h",
    "sigma_v_temporal_ms",
    "sigma_v_spatial_ms",
    "mean_tt_s",
    "fuel_ml_per_veh_km",
    "wave_count",
)


def main() -> None:
    import numpy as np
    from scipy import stats

    from validation.metrics import compute_metrics

    manifest = json.loads((RUNS_ROOT / "MANIFEST.json").read_text())
    seeds = manifest["seeds"]
    per_cell: dict[str, dict[int, dict]] = {}

    for cell, chash in manifest["cells"].items():
        per_cell[cell] = {}
        for seed in seeds:
            run_dir = RUNS_ROOT / cell / chash / str(seed)
            m = compute_metrics(run_dir, x_ref=X_REF, span=SPAN)
            per_cell[cell][seed] = {
                f: getattr(m, f) for f in FIELDS if getattr(m, f, None) is not None
            }

    out: dict[str, dict] = {"seeds": seeds, "cells": {}}
    for cell, by_seed in per_cell.items():
        metrics_list = [by_seed[s] for s in seeds]
        agg = {}
        for f in FIELDS:
            vals = [d[f] for d in metrics_list if f in d and d[f] == d[f]]
            if not vals:
                continue
            arr = np.asarray(vals, dtype=float)
            n = len(arr)
            mean = float(arr.mean())
            if n > 1:
                half = float(stats.t.ppf(0.975, n - 1) * arr.std(ddof=1) / np.sqrt(n))
            else:
                half = float("nan")
            agg[f] = {"mean": mean, "lo95": mean - half, "hi95": mean + half, "n": n}
        out["cells"][cell] = {"aggregate": agg, "per_seed": {str(s): by_seed[s] for s in seeds}}

    # Paired deltas vs baseline (common random numbers)
    base = per_cell["baseline"]
    for cell in per_cell:
        if cell == "baseline":
            continue
        deltas = {}
        for f in FIELDS:
            d = [
                per_cell[cell][s][f] - base[s][f]
                for s in seeds
                if f in per_cell[cell][s]
                and f in base[s]
                and per_cell[cell][s][f] == per_cell[cell][s][f]
                and base[s][f] == base[s][f]
            ]
            if len(d) < 2:
                continue
            arr = np.asarray(d, dtype=float)
            n = len(arr)
            mean = float(arr.mean())
            half = float(stats.t.ppf(0.975, n - 1) * arr.std(ddof=1) / np.sqrt(n))
            base_mean = float(np.mean([base[s][f] for s in seeds if f in base[s]]))
            deltas[f] = {
                "mean": mean,
                "lo95": mean - half,
                "hi95": mean + half,
                "n": n,
                "pct_of_baseline": (100.0 * mean / base_mean) if base_mean else None,
                "resolved": bool((mean - half) > 0 or (mean + half) < 0),
            }
        out["cells"][cell]["paired_delta_vs_baseline"] = deltas

    # Bimodality diagnostic: per-seed wave counts vs baseline (M3 §4.5 found
    # JAD helps on most seeds and hurts on a few).
    for cell in per_cell:
        if cell == "baseline":
            continue
        worse = [
            {
                "seed": s,
                "baseline_waves": base[s].get("wave_count"),
                "cell_waves": per_cell[cell][s].get("wave_count"),
            }
            for s in seeds
            if per_cell[cell][s].get("wave_count", 0) > base[s].get("wave_count", 0)
        ]
        out["cells"][cell]["seeds_worse_than_baseline_by_wave_count"] = worse
        out["cells"][cell]["n_seeds_worse"] = len(worse)

    (RUNS_ROOT / "analysis.json").write_text(json.dumps(out, indent=2))
    summary = {
        "experiment": "jad_oracle",
        "scenario": manifest["scenario"],
        "penetration": manifest["penetration"],
        "compliance": manifest["compliance"],
        "n_seeds": len(seeds),
        "config_hashes": manifest["cells"],
        "cells": {
            c: {
                "aggregate": v["aggregate"],
                "paired_delta_vs_baseline": v.get("paired_delta_vs_baseline", {}),
                "n_seeds_worse": v.get("n_seeds_worse"),
                "seeds_worse_than_baseline_by_wave_count": v.get(
                    "seeds_worse_than_baseline_by_wave_count", []
                ),
            }
            for c, v in out["cells"].items()
        },
    }
    (REPO / "artifacts" / "jad_oracle_summary.json").write_text(json.dumps(summary, indent=2))

    print(f"{'cell':<16}{'throughput':>22}{'sigma_v_temporal':>22}{'waves':>16}")
    for cell in manifest["cells"]:
        a = out["cells"][cell]["aggregate"]
        t, s = a["throughput_veh_h"], a["sigma_v_temporal_ms"]
        w = a.get("wave_count", {})
        print(
            f"{cell:<16}{t['mean']:>10.1f} [{t['lo95']:.0f},{t['hi95']:.0f}]"
            f"{s['mean']:>12.2f} [{s['lo95']:.2f},{s['hi95']:.2f}]"
            f"{w.get('mean', float('nan')):>10.2f}"
        )


if __name__ == "__main__":
    main()
