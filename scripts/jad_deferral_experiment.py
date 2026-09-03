"""ROADMAP B4 — deferred-commitment JAD with a perfect sensor.

docs/JAD_ORACLE_RESULTS.md found that JAD is *unreliable with a perfect
oracle* (it commits to every transient bin, finishes its cycle before the
front arrives, re-triggers, and each abrupt fast-out can seed a secondary
wave; 5 of 20 seeds worse than no control) and that 30–60 s of detection
latency removes the failure. If latency helps because it *defers commitment*,
an explicit deferral rule should capture the benefit with a perfect sensor.
``controllers.jad`` now has ``commit_delay_s``: from CRUISE, slow-in starts
only once a wave has been detected continuously for that long.

Cells on ``corridor_10km`` (EIDM, emergent/unseeded), 5% penetration / 100%
compliance, 20 common-random-number seeds:

* ``baseline``          — no controller;
* ``jad_perfect``       — perfect oracle, no deferral (the M3 controller);
* ``jad_defer30``       — perfect oracle, ``commit_delay_s = 30``;
* ``jad_defer60``       — perfect oracle, ``commit_delay_s = 60``;
* ``jad_noisy_30s``     — 30 s latency + ±20% noise, no deferral (the
  JAD_ORACLE_RESULTS reference cell).

Each run's standard metrics are computed right after it finishes and stored
as ``metrics.json``; trajectories are deleted (a run is reproducible from its
config hash and seed). Resumable. Outputs ``runs/jad_deferral/…``,
``runs/jad_deferral/analysis.json`` and the committed
``artifacts/jad_deferral_summary.json`` (per-cell CIs and paired deltas vs
baseline and vs ``jad_perfect``).

Usage::

    uv run --no-sync python scripts/jad_deferral_experiment.py [--procs 2]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import shutil
import time
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCENARIO = REPO / "scenarios" / "corridor_10km.yaml"
RUNS_ROOT = REPO / "runs" / "jad_deferral"
PENETRATION = 0.05
COMPLIANCE = 1.0
CELLS: tuple[tuple[str, str | None, dict | None, dict | None], ...] = (
    ("baseline", None, None, None),
    ("jad_perfect", "jad", {"kind": "perfect"}, None),
    ("jad_defer30", "jad", {"kind": "perfect"}, {"commit_delay_s": 30.0}),
    ("jad_defer60", "jad", {"kind": "perfect"}, {"commit_delay_s": 60.0}),
    ("jad_noisy_30s", "jad", {"kind": "noisy", "delay_s": 30.0, "amplitude_noise_frac": 0.2}, None),
)
FIELDS = (
    "throughput_veh_h",
    "sigma_v_temporal_ms",
    "sigma_v_spatial_ms",
    "mean_tt_s",
    "fuel_ml_per_veh_km",
    "wave_count",
    "wave_speed_kmh",
    "wave_amplitude_ms",
)


def cell_config(
    base: dict, controller: str | None, oracle: dict | None, params: dict | None
) -> dict:
    cfg = json.loads(json.dumps(base))
    cfg["av"]["penetration"] = 0.0 if controller is None else PENETRATION
    cfg["av"]["compliance"] = COMPLIANCE
    cfg["av"]["controller"] = controller
    cfg["av"]["controller_params"] = dict(params or {})
    if oracle is not None:
        cfg["av"]["oracle"] = oracle
    return cfg


def _worker(payload: tuple[str, dict, int]) -> tuple[str, int, bool, str]:
    cell_name, cfg_json, seed = payload
    try:
        from flowstate_core.config import ScenarioConfig
        from microsim.runner import run_micro
        from validation.metrics import compute_metrics

        paths = run_micro(ScenarioConfig.model_validate(cfg_json), seed, RUNS_ROOT / cell_name)
        m = compute_metrics(paths.run_dir)
        (paths.run_dir / "metrics.json").write_text(json.dumps(asdict(m), indent=2))
        paths.trajectories.unlink(missing_ok=True)
        paths.edges.unlink(missing_ok=True)
        shutil.rmtree(paths.run_dir / "net", ignore_errors=True)
        return cell_name, seed, True, ""
    except Exception as exc:
        return cell_name, seed, False, f"{type(exc).__name__}: {exc}"


def _done(cell_name: str, chash: str, seed: int) -> bool:
    d = RUNS_ROOT / cell_name / chash / str(seed)
    return (d / "meta.json").is_file() and (d / "metrics.json").is_file()


def analyze(manifest: dict) -> dict:
    import numpy as np
    from scipy import stats

    seeds = manifest["seeds"]
    per_cell: dict[str, dict[int, dict]] = {}
    for cell, chash in manifest["cells"].items():
        per_cell[cell] = {}
        for seed in seeds:
            p = RUNS_ROOT / cell / chash / str(seed) / "metrics.json"
            if p.is_file():
                m = json.loads(p.read_text())
                per_cell[cell][seed] = {f: m[f] for f in FIELDS if f in m}

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

    def paired(cell: str, ref: str) -> dict:
        out = {}
        for f in FIELDS:
            pairs = [
                (per_cell[cell][s][f], per_cell[ref][s][f])
                for s in seeds
                if s in per_cell[cell]
                and s in per_cell[ref]
                and np.isfinite(per_cell[cell][s].get(f, np.nan))
                and np.isfinite(per_cell[ref][s].get(f, np.nan))
            ]
            if len(pairs) < 2:
                continue
            d = np.asarray([a - b for a, b in pairs], dtype=float)
            n = len(d)
            mean = float(d.mean())
            half = float(stats.t.ppf(0.975, n - 1) * d.std(ddof=1) / np.sqrt(n))
            ref_mean = float(np.mean([b for _, b in pairs]))
            out[f] = {
                "mean": mean,
                "lo95": mean - half,
                "hi95": mean + half,
                "n": n,
                "pct_of_reference": (100.0 * mean / ref_mean) if ref_mean else None,
                "resolved": bool((mean - half) > 0 or (mean + half) < 0),
                "n_worse_than_reference": int(np.sum(d > 0))
                if f != "throughput_veh_h"
                else int(np.sum(d < 0)),
            }
        return out

    result: dict = {"seeds": seeds, "cells": {}}
    for cell in per_cell:
        entry: dict = {
            "aggregate": {f: ci([per_cell[cell][s][f] for s in per_cell[cell]]) for f in FIELDS}
        }
        if cell != "baseline":
            entry["paired_vs_baseline"] = paired(cell, "baseline")
        if cell not in ("baseline", "jad_perfect"):
            entry["paired_vs_jad_perfect"] = paired(cell, "jad_perfect")
        result["cells"][cell] = entry
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--replicates", type=int, default=20)
    args = ap.parse_args()

    from flowstate_core.config import ScenarioConfig, config_hash
    from flowstate_core.rng import spawn_seeds

    base_cfg = ScenarioConfig.from_yaml(SCENARIO)
    base_json = base_cfg.model_dump(mode="json")
    seeds = spawn_seeds(base_cfg.seed, args.replicates)
    hashes: dict[str, str] = {}
    pending: list[tuple[str, dict, int]] = []
    for name, controller, oracle, params in CELLS:
        cfg_json = cell_config(base_json, controller, oracle, params)
        chash = config_hash(ScenarioConfig.model_validate(cfg_json))
        hashes[name] = chash
        pending += [(name, cfg_json, s) for s in seeds if not _done(name, chash, s)]

    print(f"{len(CELLS) * len(seeds)} runs; {len(pending)} pending", flush=True)
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

    manifest = {
        "experiment": "jad_deferral",
        "scenario": "scenarios/corridor_10km.yaml",
        "penetration": PENETRATION,
        "compliance": COMPLIANCE,
        "cells": hashes,
        "cell_specs": [
            {"name": n, "controller": c, "oracle": o, "controller_params": p}
            for n, c, o, p in CELLS
        ],
        "seeds": seeds,
        "replicates": args.replicates,
        "failures": failures,
    }
    (RUNS_ROOT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    analysis = analyze(manifest)
    (RUNS_ROOT / "analysis.json").write_text(json.dumps(analysis, indent=2))
    summary = {
        **{k: v for k, v in manifest.items() if k not in ("failures", "cells")},
        "config_hashes": hashes,
        "n_failed": len(failures),
        **analysis,
    }
    (REPO / "artifacts" / "jad_deferral_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"done in {time.perf_counter() - t0:.0f} s; {len(failures)} failed")
    for cell, entry in analysis["cells"].items():
        a = entry["aggregate"]
        s, w, f = a["sigma_v_temporal_ms"], a["wave_count"], a["fuel_ml_per_veh_km"]
        line = f"{cell:14s} sigma_v {s['mean']:.3f} [{s['lo95']:.3f}, {s['hi95']:.3f}]  waves {w['mean']:.2f}  fuel {f['mean']:.2f}"
        if "paired_vs_baseline" in entry:
            d = entry["paired_vs_baseline"]["sigma_v_temporal_ms"]
            line += f"  | vs baseline sigma_v {d['pct_of_reference']:+.1f}% ({'resolved' if d['resolved'] else 'not resolved'}), worse in {d['n_worse_than_reference']}/{d['n']} seeds"
        print(line)


if __name__ == "__main__":
    main()
