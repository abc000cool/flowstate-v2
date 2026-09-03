"""Analyse the I-24 penetration × compliance battery (scripts/i24_penetration_sweep.py).

Reads every run's ``metrics.json`` (computed on the measured span at run
time), builds per-cell marginal 95% t CIs and paired per-seed deltas against
the baseline (valid because all cells share one seed list), and writes
``runs/i24_sweep/analysis.json`` plus the committed summary
``artifacts/i24_sweep_summary.json`` — the source of every number in
docs/I24_SWEEP.md.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNS_ROOT = REPO / "runs" / "i24_sweep"
FIELDS = (
    "throughput_veh_h",
    "sigma_v_temporal_ms",
    "sigma_v_spatial_ms",
    "mean_tt_s",
    "p90_tt_s",
    "fuel_ml_per_veh_km",
    "wave_count",
    "wave_speed_kmh",
    "wave_amplitude_ms",
    "lane_changes_per_veh_km",
    "lane_changes_per_veh",
)


def main() -> None:
    import argparse

    import numpy as np
    from scipy import stats

    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--allow-partial",
        action="store_true",
        help="analyse the cells whose 20 runs are complete and list the rest as incomplete",
    )
    args = ap.parse_args()

    manifest = json.loads((RUNS_ROOT / "MANIFEST.json").read_text())
    seeds = manifest["seeds"]
    per_cell: dict[str, dict[int, dict]] = {}
    missing: list[tuple[str, int]] = []
    for cell, chash in manifest["cells"].items():
        per_cell[cell] = {}
        for seed in seeds:
            p = RUNS_ROOT / cell / chash / str(seed) / "metrics.json"
            if not p.is_file():
                missing.append((cell, seed))
                continue
            m = json.loads(p.read_text())
            per_cell[cell][seed] = {f: m[f] for f in FIELDS if f in m}
    incomplete = sorted({c for c, _ in missing})
    if missing and not args.allow_partial:
        raise SystemExit(f"{len(missing)} runs lack metrics.json, e.g. {missing[:3]}")
    if "baseline" in incomplete:
        raise SystemExit("baseline cell incomplete; nothing to pair against")
    per_cell = {c: v for c, v in per_cell.items() if c not in incomplete}

    def ci(vals: list[float]) -> dict:
        arr = np.asarray(vals, dtype=float)
        arr = arr[np.isfinite(arr)]
        n = len(arr)
        if n == 0:
            return {"mean": None, "lo95": None, "hi95": None, "n": 0}
        mean = float(arr.mean())
        half = (
            float(stats.t.ppf(0.975, n - 1) * arr.std(ddof=1) / np.sqrt(n))
            if n > 1
            else float("nan")
        )
        return {
            "mean": mean,
            "lo95": mean - half,
            "hi95": mean + half,
            "n": n,
            "underpowered": n < 20,
        }

    out: dict[str, dict] = {"seeds": seeds, "cells": {}}
    base = per_cell["baseline"]
    for cell, by_seed in per_cell.items():
        agg = {f: ci([by_seed[s][f] for s in seeds if f in by_seed[s]]) for f in FIELDS}
        entry: dict = {"aggregate": agg, "per_seed": {str(s): by_seed[s] for s in seeds}}
        if cell != "baseline":
            deltas = {}
            for f in FIELDS:
                pairs = [
                    (by_seed[s][f], base[s][f])
                    for s in seeds
                    if f in by_seed[s]
                    and f in base[s]
                    and np.isfinite(by_seed[s][f])
                    and np.isfinite(base[s][f])
                ]
                if len(pairs) < 2:
                    continue
                d = np.asarray([a - b for a, b in pairs], dtype=float)
                n = len(d)
                mean = float(d.mean())
                half = float(stats.t.ppf(0.975, n - 1) * d.std(ddof=1) / np.sqrt(n))
                base_mean = float(np.mean([b for _, b in pairs]))
                deltas[f] = {
                    "mean": mean,
                    "lo95": mean - half,
                    "hi95": mean + half,
                    "n": n,
                    "pct_of_baseline": (100.0 * mean / base_mean) if base_mean else None,
                    "resolved": bool((mean - half) > 0 or (mean + half) < 0),
                }
            entry["paired_delta_vs_baseline"] = deltas
        out["cells"][cell] = entry

    (RUNS_ROOT / "analysis.json").write_text(json.dumps(out, indent=2))
    summary = {
        "experiment": "i24_sweep",
        "incomplete_cells": incomplete,
        "scenario": manifest["scenario"],
        "base_config_hash": manifest["base_config_hash"],
        "controller": manifest["controller"],
        "penetrations": manifest["penetrations"],
        "compliances": manifest["compliances"],
        "n_seeds": len(seeds),
        "config_hashes": manifest["cells"],
        "metrics_args": manifest["metrics_args"],
        "cells": {
            c: {
                "aggregate": v["aggregate"],
                "paired_delta_vs_baseline": v.get("paired_delta_vs_baseline", {}),
            }
            for c, v in out["cells"].items()
        },
    }
    (REPO / "artifacts" / "i24_sweep_summary.json").write_text(json.dumps(summary, indent=2))

    print(f"{'cell':<18}{'throughput':>24}{'sigma_v_temporal':>22}{'fuel':>18}{'waves':>10}")
    for cell in per_cell:
        a = out["cells"][cell]["aggregate"]
        t, s, f, w = (
            a["throughput_veh_h"],
            a["sigma_v_temporal_ms"],
            a["fuel_ml_per_veh_km"],
            a["wave_count"],
        )
        print(
            f"{cell:<18}{t['mean']:>10.0f} [{t['lo95']:.0f},{t['hi95']:.0f}]"
            f"{s['mean']:>10.2f} [{s['lo95']:.2f},{s['hi95']:.2f}]"
            f"{f['mean']:>9.1f} [{f['lo95']:.1f},{f['hi95']:.1f}]"
            f"{w['mean']:>8.2f}"
        )


if __name__ == "__main__":
    main()
