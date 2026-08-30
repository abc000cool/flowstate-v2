"""M3 penetration x compliance sweep driver (CLAUDE.md §7.1, §11 M3).

Runs the full M3 experiment battery on ``corridor_10km`` (as shipped: EIDM
fleet, emergent waves from inflow noise, ``seeded=False``):

* ``baseline`` — penetration 0, no controller (1 cell);
* ``follower_stopper`` at penetration {1, 2, 5, 10, 15, 20}% x compliance
  {25, 50, 80, 100}% (24 cells);
* controller-comparison arm at penetration 5% / compliance 100% for
  ``pi_saturation`` and ``jad`` (2 cells).

27 cells x 20 replicates = 540 runs. Replicate seeds come from
``flowstate_core.rng.spawn_seeds(scenario seed, 20)`` — the SAME seed list in
every cell (common random numbers, so penetration/compliance contrasts are
paired). Outputs land under ``runs/m3_sweep/<cell>/<config_hash>/<seed>/``
with the standard contract artifacts (trajectories + edges + meta,
docs/CONTRACTS.md §3) written by ``microsim.runner.run_micro``.

Honest and resumable:

* a (cell, seed) whose ``meta.json`` already exists and parses is skipped, so
  the script can be re-launched after an interruption;
* one JSON line per completed run is appended to
  ``runs/m3_sweep/progress.jsonl`` (cell, seed, wall_s, ok / error+message);
* a heartbeat with completed/total and an ETA prints every few minutes;
* ``MANIFEST.json`` (cells, config hashes, seed lists, package versions,
  wall time, failures) is written when all runs have been attempted.

Parallelism: a ``spawn``-context process pool sized by env ``M3_PROCS``
(default 6) over (cell, seed) pairs — libsumo is a per-process singleton
(one SUMO per process; sequential start/close cycles inside each worker are
fine, see microsim.runner module docstring).

Usage (from the repo root)::

    M3_PROCS=6 uv run --no-sync python scripts/m3_sweep.py            # full sweep
    uv run --no-sync python scripts/m3_sweep.py --list                # show cells
    uv run --no-sync python scripts/m3_sweep.py --only cell=baseline,seed=0
        # one run; seed may be the replicate index (0..19) or a literal seed
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SCENARIO = "corridor_10km"
PENETRATIONS = (0.01, 0.02, 0.05, 0.10, 0.15, 0.20)
COMPLIANCES = (0.25, 0.50, 0.80, 1.00)
SWEEP_CONTROLLER = "follower_stopper"
COMPARISON_CONTROLLERS = ("pi_saturation", "jad")
COMPARISON_PENETRATION = 0.05
COMPARISON_COMPLIANCE = 1.00
N_REPLICATES = 20
DEFAULT_ROOT = REPO_ROOT / "runs" / "m3_sweep"
HEARTBEAT_S = 180.0


@dataclass(frozen=True)
class Cell:
    """One grid cell: a named scenario-config variant."""

    name: str
    controller: str | None
    penetration: float
    compliance: float


def build_cells() -> list[Cell]:
    """The 27-cell M3 battery, baseline first (order is also run order)."""
    cells = [Cell("baseline", None, 0.0, 1.0)]
    for pen in PENETRATIONS:
        for comp in COMPLIANCES:
            cells.append(
                Cell(f"{SWEEP_CONTROLLER}_p{pen:.2f}_c{comp:.2f}", SWEEP_CONTROLLER, pen, comp)
            )
    for ctrl in COMPARISON_CONTROLLERS:
        cells.append(
            Cell(
                f"{ctrl}_p{COMPARISON_PENETRATION:.2f}_c{COMPARISON_COMPLIANCE:.2f}",
                ctrl,
                COMPARISON_PENETRATION,
                COMPARISON_COMPLIANCE,
            )
        )
    return cells


def cell_config(base_cfg_json: dict, cell: Cell) -> dict:
    """Scenario-config JSON for one cell (baseline config + AV overrides)."""
    cfg = json.loads(json.dumps(base_cfg_json))  # deep copy
    cfg["av"]["penetration"] = cell.penetration
    cfg["av"]["compliance"] = cell.compliance
    cfg["av"]["controller"] = cell.controller
    cfg["av"]["controller_params"] = {}
    return cfg


def _worker(payload: tuple[str, dict, int, str]) -> tuple[str, int, float, bool, str]:
    """Pool worker: one seeded replicate, one SUMO in this process.

    Imports happen inside the child (spawn start method); returns
    ``(cell_name, seed, wall_s, ok, error_message)``.
    """
    cell_name, cfg_json, seed, out_root = payload
    t0 = time.perf_counter()
    try:
        from flowstate_core.config import ScenarioConfig
        from microsim.runner import run_micro

        cfg = ScenarioConfig.model_validate(cfg_json)
        run_micro(cfg, seed, Path(out_root) / cell_name)
        return cell_name, seed, time.perf_counter() - t0, True, ""
    except Exception as exc:  # sweep must survive a bad run and record it
        return cell_name, seed, time.perf_counter() - t0, False, f"{type(exc).__name__}: {exc}"


def _done(root: Path, cell_name: str, chash: str, seed: int) -> bool:
    """True when the run's meta.json exists and parses (resume criterion)."""
    meta = root / cell_name / chash / str(seed) / "meta.json"
    if not meta.is_file():
        return False
    try:
        json.loads(meta.read_text())
        return True
    except (json.JSONDecodeError, OSError):
        return False


def _parse_only(only: str, seeds: list[int]) -> tuple[str, int]:
    """Parse ``--only cell=NAME,seed=S`` (S: replicate index 0..19 or literal seed)."""
    parts = dict(kv.split("=", 1) for kv in only.split(","))
    cell_name = parts["cell"]
    raw = int(parts["seed"])
    if raw in seeds:
        return cell_name, raw
    if 0 <= raw < len(seeds):
        return cell_name, seeds[raw]
    raise SystemExit(f"--only seed={raw} is neither a replicate index (<{len(seeds)}) nor a seed")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="run-tree root")
    ap.add_argument("--only", default=None, metavar="cell=NAME,seed=S", help="run one (cell, seed)")
    ap.add_argument("--list", action="store_true", help="print the cell table and exit")
    ap.add_argument(
        "--procs",
        type=int,
        default=int(os.environ.get("M3_PROCS", "6")),
        help="process-pool size (env M3_PROCS, default 6)",
    )
    args = ap.parse_args()

    from flowstate_core.config import ScenarioConfig, config_hash
    from flowstate_core.rng import spawn_seeds
    from microsim.runner import _versions
    from microsim.scenarios import load_scenario

    base_cfg = load_scenario(SCENARIO)
    if base_cfg.replicates != N_REPLICATES:
        raise SystemExit(
            f"{SCENARIO} ships replicates={base_cfg.replicates}, M3 spec wants {N_REPLICATES}"
        )
    seeds = spawn_seeds(base_cfg.seed, N_REPLICATES)
    base_json = base_cfg.model_dump(mode="json")
    cells = build_cells()
    cfgs = {c.name: cell_config(base_json, c) for c in cells}
    hashes = {c.name: config_hash(ScenarioConfig.model_validate(cfgs[c.name])) for c in cells}

    if args.list:
        for c in cells:
            print(
                f"{c.name:32s} controller={c.controller or '-':16s} "
                f"pen={c.penetration:.2f} comp={c.compliance:.2f} hash={hashes[c.name]}"
            )
        return

    if args.only:
        cell_name, seed = _parse_only(args.only, seeds)
        if cell_name not in cfgs:
            raise SystemExit(f"unknown cell {cell_name!r}; use --list")
        pending = [(cell_name, cfgs[cell_name], seed, str(args.root))]
    else:
        pending = [
            (c.name, cfgs[c.name], s, str(args.root))
            for c in cells
            for s in seeds
            if not _done(args.root, c.name, hashes[c.name], s)
        ]

    total = len(cells) * len(seeds) if not args.only else 1
    already = total - len(pending)
    print(
        f"M3 sweep: {len(cells)} cells x {len(seeds)} replicates = {total} runs; "
        f"{already} already complete, {len(pending)} to run; procs={args.procs}",
        flush=True,
    )
    args.root.mkdir(parents=True, exist_ok=True)
    progress_path = args.root / "progress.jsonl"

    t_start = time.perf_counter()
    n_done = 0
    n_fail = 0
    last_beat = t_start
    if pending:
        ctx = multiprocessing.get_context("spawn")
        with (
            ctx.Pool(processes=min(args.procs, len(pending))) as pool,
            open(progress_path, "a") as progress,
        ):
            for cell_name, seed, wall_s, ok, err in pool.imap_unordered(_worker, pending):
                n_done += 1
                n_fail += 0 if ok else 1
                line = {
                    "cell": cell_name,
                    "seed": seed,
                    "wall_s": round(wall_s, 3),
                    "ok": ok,
                }
                if not ok:
                    line["error"] = err
                    print(f"FAIL {cell_name}/{seed}: {err}", file=sys.stderr, flush=True)
                progress.write(json.dumps(line) + "\n")
                progress.flush()
                now = time.perf_counter()
                if now - last_beat >= HEARTBEAT_S or n_done == len(pending):
                    rate = n_done / (now - t_start)
                    eta_s = (len(pending) - n_done) / rate if rate > 0 else float("nan")
                    print(
                        f"[heartbeat] {n_done + already}/{total} complete "
                        f"({n_fail} failed) — ETA {eta_s / 3600.0:.2f} h",
                        flush=True,
                    )
                    last_beat = now

    wall = time.perf_counter() - t_start
    if args.only:
        print(f"--only run finished in {wall:.1f} s ({n_fail} failed); MANIFEST not written")
        sys.exit(1 if n_fail else 0)

    incomplete = [
        {"cell": c.name, "seed": s}
        for c in cells
        for s in seeds
        if not _done(args.root, c.name, hashes[c.name], s)
    ]
    manifest = {
        "scenario": SCENARIO,
        "master_seed": base_cfg.seed,
        "seeds": seeds,
        "replicates": N_REPLICATES,
        "cells": [
            {
                "name": c.name,
                "controller": c.controller,
                "penetration": c.penetration,
                "compliance": c.compliance,
                "config_hash": hashes[c.name],
            }
            for c in cells
        ],
        "n_runs_total": total,
        "n_runs_this_session": n_done,
        "n_failed_this_session": n_fail,
        "incomplete": incomplete,
        "versions": _versions(),
        "wall_time_s_this_session": wall,
        "procs": args.procs,
    }
    (args.root / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    print(
        f"sweep session done in {wall / 3600.0:.2f} h; {len(incomplete)} runs incomplete; "
        f"MANIFEST at {args.root / 'MANIFEST.json'}"
    )
    sys.exit(1 if incomplete else 0)


if __name__ == "__main__":
    main()
