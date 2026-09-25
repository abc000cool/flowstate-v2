"""Penetration dose-response on REAL corridor geometry (US-101 replica).

M3's headline dose-response ([docs/M3_RESULTS.md]) was measured on the synthetic
`corridor_10km` scenario: an EIDM fleet on an idealised straight pipe with
screening-calibrated demand. The obvious question a reviewer asks is whether the
effect survives on real geometry with a data-calibrated fleet.

This runs the same FollowerStopper penetration ladder on `us101_replica` — 640 m
of 5-lane US-101 imported from the NGSIM site, fleet drawn from the
`artifacts/idm_us101.json` population fit, real upstream demand from
`artifacts/demand_us101.json`, and the **measured downstream boundary
condition** (without it the replica shows no waves at all to dampen; see
docs/M3_US101_VALIDATION.md).

Read the results as a robustness check on the *shape* of the dose-response, not
as a validated corridor study: the replica fails 5 of 6 FHWA criteria and its
limitations are documented in docs/M3_US101_VALIDATION.md §7.

Usage::

    uv run --no-sync python scripts/us101_penetration_sweep.py [--procs 6]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
SCENARIO = REPO / "scenarios" / "us101_replica.yaml"
RUNS_ROOT = REPO / "runs" / "us101_penetration"
PENETRATIONS = (0.01, 0.02, 0.05, 0.10, 0.20)
COMPLIANCE = 1.0
CONTROLLER = "follower_stopper"


BOUNDARY_SCENARIO = REPO / "runs" / "m3_us101" / "us101_replica_with_boundary.yaml"
CALIBRATED_SCENARIO = REPO / "scenarios" / "us101_replica_calibrated.yaml"


def _base_with_boundary() -> tuple[dict, str]:
    """The replica plus its measured downstream boundary, and where it came from.

    ``scripts/m3_us101_validate.py`` writes the boundary-carrying scenario when
    it runs (the schedule is extracted from the NGSIM chunks); reuse it if
    present. Otherwise take the ``network.boundary`` block of the committed
    ``scenarios/us101_replica_calibrated.yaml``, which carries that measured
    schedule unchanged (its header: "Exit fractions and any boundary
    unchanged"): the replica with that block has the same config hash as the
    snapshot (ab879e240aed under hash policy v2, checked 2026-09-25), and a
    cloud VM, which receives neither ``runs/`` nor ``data/ngsim``, runs the
    same configuration. (The earlier fallback re-derived the schedule from the
    NGSIM chunks but indexed the dict ``us101_data.load_us101`` returns as a
    frame, so it could not run.)
    """
    import yaml

    from flowstate_core.config import ScenarioConfig

    if BOUNDARY_SCENARIO.is_file():
        print(f"using boundary scenario {BOUNDARY_SCENARIO}")
        cfg = ScenarioConfig.from_yaml(BOUNDARY_SCENARIO).model_dump(mode="json")
        return cfg, str(BOUNDARY_SCENARIO.relative_to(REPO))

    print(f"boundary snapshot absent; boundary block of {CALIBRATED_SCENARIO.name}")
    raw = yaml.safe_load(SCENARIO.read_text())
    raw["network"]["boundary"] = yaml.safe_load(CALIBRATED_SCENARIO.read_text())["network"][
        "boundary"
    ]
    cfg = ScenarioConfig.model_validate(raw).model_dump(mode="json")
    return cfg, f"{CALIBRATED_SCENARIO.relative_to(REPO)} network.boundary"


def cell_config(base: dict, pen: float) -> dict:
    cfg = json.loads(json.dumps(base))
    cfg["av"]["penetration"] = pen
    cfg["av"]["compliance"] = COMPLIANCE
    cfg["av"]["controller"] = CONTROLLER if pen > 0.0 else None
    cfg["av"]["controller_params"] = {}
    return cfg


def _worker(payload: tuple[str, dict, int]) -> tuple[str, int, bool, str]:
    cell_name, cfg_json, seed = payload
    try:
        from flowstate_core.config import ScenarioConfig
        from microsim.runner import run_micro

        run_micro(ScenarioConfig.model_validate(cfg_json), seed, RUNS_ROOT / cell_name)
        return cell_name, seed, True, ""
    except Exception as exc:
        return cell_name, seed, False, f"{type(exc).__name__}: {exc}"


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

    base_json, boundary_source = _base_with_boundary()
    base_cfg = ScenarioConfig.model_validate(base_json)
    seeds = spawn_seeds(base_cfg.seed, args.replicates)

    cells = [("baseline", 0.0)] + [(f"fs_p{p:.2f}", p) for p in PENETRATIONS]
    pending: list[tuple[str, dict, int]] = []
    hashes: dict[str, str] = {}
    for name, pen in cells:
        cfg_json = cell_config(base_json, pen)
        chash = config_hash(ScenarioConfig.model_validate(cfg_json))
        hashes[name] = chash
        pending += [(name, cfg_json, s) for s in seeds if not _done(name, chash, s)]

    print(f"{len(cells) * len(seeds)} runs; {len(pending)} pending")
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    n_fail = 0
    if pending:
        with mp.get_context("spawn").Pool(min(args.procs, len(pending))) as pool:
            for i, (cell, seed, ok, err) in enumerate(
                pool.imap_unordered(_worker, pending), start=1
            ):
                if not ok:
                    n_fail += 1
                    print(f"  FAIL {cell} seed={seed}: {err}")
                if i % 10 == 0 or i == len(pending):
                    print(f"  {i}/{len(pending)} ({time.perf_counter() - t0:.0f} s)")

    (RUNS_ROOT / "MANIFEST.json").write_text(
        json.dumps(
            {
                "scenario": "scenarios/us101_replica.yaml",
                "controller": CONTROLLER,
                "compliance": COMPLIANCE,
                "boundary": "measured downstream (docs/M3_US101_VALIDATION.md)",
                "boundary_source": boundary_source,
                "cells": hashes,
                "seeds": seeds,
                "n_failed_this_session": n_fail,
            },
            indent=2,
        )
    )
    print(f"done in {time.perf_counter() - t0:.1f} s; {n_fail} failed")


if __name__ == "__main__":
    main()
