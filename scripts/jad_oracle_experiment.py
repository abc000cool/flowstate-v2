"""JAD under a degraded wave-detection oracle (CLAUDE.md §4.3).

§4.3 requires the oracle interface to be swappable and every headline JAD
result to be reported under the delayed/noisy variant as well as the perfect
one. This runs `corridor_10km` at 5% penetration / 100% compliance with:

* ``baseline``      — no controller;
* ``jad_perfect``   — instantaneous, exact downstream reading;
* ``jad_noisy_30s`` — 30 s detection latency, +/-20% bin-speed error;
* ``jad_noisy_60s`` — 60 s latency (the top of the §4.3 range), +/-20% error.

Common random numbers across cells; resumable; outputs under
``runs/jad_oracle/<cell>/<config_hash>/<seed>/``.

Usage::

    uv run --no-sync python scripts/jad_oracle_experiment.py [--procs 6]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCENARIO = REPO / "scenarios" / "corridor_10km.yaml"
RUNS_ROOT = REPO / "runs" / "jad_oracle"
PENETRATION = 0.05
COMPLIANCE = 1.0
CELLS: tuple[tuple[str, str | None, dict | None], ...] = (
    ("baseline", None, None),
    ("jad_perfect", "jad", {"kind": "perfect"}),
    ("jad_noisy_30s", "jad", {"kind": "noisy", "delay_s": 30.0, "amplitude_noise_frac": 0.2}),
    ("jad_noisy_60s", "jad", {"kind": "noisy", "delay_s": 60.0, "amplitude_noise_frac": 0.2}),
)


def cell_config(base: dict, controller: str | None, oracle: dict | None) -> dict:
    cfg = json.loads(json.dumps(base))
    cfg["av"]["penetration"] = 0.0 if controller is None else PENETRATION
    cfg["av"]["compliance"] = COMPLIANCE
    cfg["av"]["controller"] = controller
    cfg["av"]["controller_params"] = {}
    if oracle is not None:
        cfg["av"]["oracle"] = oracle
    return cfg


def _worker(payload: tuple[str, dict, int]) -> tuple[str, int, float, bool, str]:
    """One seeded replicate; one SUMO per process (libsumo is a per-process singleton)."""
    cell_name, cfg_json, seed = payload
    t0 = time.perf_counter()
    try:
        from flowstate_core.config import ScenarioConfig
        from microsim.runner import run_micro

        run_micro(ScenarioConfig.model_validate(cfg_json), seed, RUNS_ROOT / cell_name)
        return cell_name, seed, time.perf_counter() - t0, True, ""
    except Exception as exc:
        return cell_name, seed, time.perf_counter() - t0, False, f"{type(exc).__name__}: {exc}"


def _done(cell_name: str, chash: str, seed: int) -> bool:
    meta = RUNS_ROOT / cell_name / chash / str(seed) / "meta.json"
    if not meta.is_file():
        return False
    try:
        json.loads(meta.read_text())
        return True
    except (OSError, json.JSONDecodeError):
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--replicates", type=int, default=20)
    args = ap.parse_args()

    from flowstate_core.config import ScenarioConfig, config_hash
    from flowstate_core.rng import spawn_seeds

    base_cfg = ScenarioConfig.from_yaml(SCENARIO)
    base_json = base_cfg.model_dump(mode="json")
    seeds = spawn_seeds(base_cfg.seed, args.replicates)

    pending: list[tuple[str, dict, int]] = []
    hashes: dict[str, str] = {}
    for cell_name, controller, oracle in CELLS:
        cfg_json = cell_config(base_json, controller, oracle)
        chash = config_hash(ScenarioConfig.model_validate(cfg_json))
        hashes[cell_name] = chash
        for seed in seeds:
            if not _done(cell_name, chash, seed):
                pending.append((cell_name, cfg_json, seed))

    total = len(CELLS) * len(seeds)
    print(f"{total} runs total; {len(pending)} pending; hashes={hashes}")
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    n_fail = 0
    if pending:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=min(args.procs, len(pending))) as pool:
            for i, (cell, seed, _wall, ok, err) in enumerate(
                pool.imap_unordered(_worker, pending), start=1
            ):
                if not ok:
                    n_fail += 1
                    print(f"  FAIL {cell} seed={seed}: {err}")
                if i % 10 == 0 or i == len(pending):
                    print(f"  {i}/{len(pending)} done ({time.perf_counter() - t0:.0f} s)")

    (RUNS_ROOT / "MANIFEST.json").write_text(
        json.dumps(
            {
                "scenario": str(SCENARIO.relative_to(REPO)),
                "penetration": PENETRATION,
                "compliance": COMPLIANCE,
                "cells": {name: hashes[name] for name, _, _ in CELLS},
                "seeds": seeds,
                "n_failed_this_session": n_fail,
            },
            indent=2,
        )
    )
    print(f"done in {time.perf_counter() - t0:.1f} s; {n_fail} failed")


if __name__ == "__main__":
    main()
