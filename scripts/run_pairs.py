"""Run named (scenario, seed) pairs with every run file kept, trajectories included.

Each pair is one replicate run exactly as a battery runs it
(``microsim.runner._replicate_worker``: the scenario's JSON dump re-validated in a
spawned child, then ``run_micro(cfg, seed, <out>/<label>)``), so a pair reproduces
the battery replicate of the same configuration and seed, and lands in
``<out>/<label>/<config hash>/<seed>/``. Nothing is pruned: the run keeps
``trajectories.parquet`` and ``net/demand.rou.xml`` beside ``meta.json`` and
``vehicles.parquet``. Written for stage ``p8c_i94_cal_collisions``
(docs/I94_CAL_COLLISIONS.md §10), which re-runs seven replicates of two different
batteries; ``corridor_battery.py --keep-trajectories`` can reach a seed only by
running every seed before it in the spawned list.

* **Pairs.** ``[LABEL=]SCENARIO.yaml:SEED``; the label (default: the scenario's file
  stem) names the run tree's first level. A (label, seed) given twice is refused,
  and so is a label given two different scenarios.
* **Hash guard.** Every scenario is loaded and hashed before anything runs;
  ``--expect-hash LABEL=HASH`` refuses the whole set (exit 2, nothing run) when a
  label's scenario does not hash as expected, so a drifted scenario costs no VM time.
  The pin is compared with today's hash (``CONFIG_HASH_VERSION``), never accepted under an
  older policy: a pin that is the file's policy-3 or policy-2 hash is refused with that
  said, since the file may run other physics now (docs/CONTRACTS.md §2).
* **Pool.** One spawn process per run (libsumo is one simulation per process), at
  most ``--procs`` at a time: one wave when ``--procs`` is at least the number of
  pairs. A pair that fails is reported and the others carry on. ``--procs 1`` runs
  the pairs one after another in this process.
* **Manifest.** ``<out>/PAIRS.json``: per pair its label, scenario, seed, config hash,
  run directory, status (``ok`` / ``failed`` / ``planned`` under ``--dry-run``), wall
  time, ``n_collisions`` and the error of a failed one.

Exit status: 0 when every pair ran (or ``--dry-run``), 1 when any failed, 2 on a usage
error or a hash mismatch.

Usage::

    uv run --no-sync python scripts/run_pairs.py --out runs/p8c --procs 7 \\
        --expect-hash dc_cal=beaaa710e6b3 \\
        dc_cal=scenarios/mndot_i94_wb_stpaul_weave_dc_cal.yaml:134183728835869882 ...
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import re
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from flowstate_core.config import CONFIG_HASH_VERSION, config_hash, config_hash_v2, config_hash_v3
from microsim.runner import _replicate_worker
from microsim.scenarios import load_scenario, resolve_scenario

SCHEMA_VERSION = 1
MANIFEST = "PAIRS.json"
_LABEL = re.compile(r"^[A-Za-z0-9_.-]+$")

#: ``(cfg_json, seed, out_root) -> (run_dir, trajectories, edges, meta)``, the
#: signature of ``microsim.runner._replicate_worker``.
Worker = Callable[[tuple[dict[str, Any], int, str]], tuple[str, str, str, str]]


@dataclass(frozen=True)
class Pair:
    """One run to make: a scenario file and a seed, under a label."""

    label: str
    scenario: Path
    seed: int


def parse_pair(spec: str) -> Pair:
    """Parse ``[LABEL=]SCENARIO.yaml:SEED``.

    Args:
        spec: The command-line form.

    Returns:
        The pair; the label defaults to the scenario's file stem.

    Raises:
        ValueError: A malformed spec, a label with characters other than
            letters, digits, ``_``, ``.`` and ``-``, or a negative seed.
    """
    label, sep, rest = spec.partition("=")
    if not sep:
        label, rest = "", spec
    path, sep, seed_text = rest.rpartition(":")
    if not sep or not path or not seed_text:
        raise ValueError(f"{spec!r}: expected [LABEL=]SCENARIO.yaml:SEED")
    try:
        seed = int(seed_text)
    except ValueError as exc:
        raise ValueError(f"{spec!r}: seed {seed_text!r} is not an integer") from exc
    if seed < 0:
        raise ValueError(f"{spec!r}: seed must be non-negative")
    scenario = Path(path)
    label = label or scenario.stem
    if not _LABEL.match(label):
        raise ValueError(f"{spec!r}: label {label!r} must match {_LABEL.pattern}")
    return Pair(label=label, scenario=scenario, seed=seed)


def check_pairs(pairs: Sequence[Pair]) -> None:
    """Refuse duplicates: a (label, seed) twice, or a label on two scenarios.

    Raises:
        ValueError: Naming the offending label.
    """
    seen: set[tuple[str, int]] = set()
    scenario_of: dict[str, Path] = {}
    for p in pairs:
        if (p.label, p.seed) in seen:
            raise ValueError(f"pair {p.label}:{p.seed} given twice (one run directory)")
        seen.add((p.label, p.seed))
        if scenario_of.setdefault(p.label, p.scenario) != p.scenario:
            raise ValueError(
                f"label {p.label} names two scenarios ({scenario_of[p.label]}, {p.scenario})"
            )


def _expected_hashes(items: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        label, sep, value = item.partition("=")
        if not sep or not label or not value:
            raise ValueError(f"--expect-hash {item!r}: expected LABEL=HASH")
        out[label] = value
    return out


def _record(run_dir: str) -> dict[str, Any]:
    meta = json.loads((Path(run_dir) / "meta.json").read_text())
    return {
        "status": "ok",
        "run_dir": run_dir,
        "wall_s": meta.get("wall_time_s"),
        "n_collisions": meta.get("n_collisions"),
        "config_hash_run": meta.get("config_hash"),
    }


def _older_policy(scenario: Path, want: str) -> str:
    """Why a pin is refused although the file may be unchanged: an older policy's hash of it.

    A pin is compared with today's hash, never accepted under an older policy: where a policy
    changed a default since (policy 4, Amendment 4: W1b and W2 at a weave that does not set
    them), the same file now runs other physics than the run the pin names (docs/CONTRACTS.md §2).
    """
    try:
        doc = yaml.safe_load(resolve_scenario(scenario).read_text())
        for version, older in ((3, config_hash_v3), (2, config_hash_v2)):
            if want == older(doc):
                return (
                    f"; {want} is this file's policy-v{version} hash: a pin from before policy "
                    f"v{CONFIG_HASH_VERSION}, refused because the file may run other physics now "
                    "(docs/CONTRACTS.md section 2); re-pin only knowingly"
                )
    except (OSError, ValueError):
        pass
    return ""


def run_pairs(
    pairs: Sequence[Pair],
    out: Path,
    *,
    procs: int,
    expect_hash: dict[str, str] | None = None,
    dry_run: bool = False,
    worker: Worker = _replicate_worker,
) -> tuple[int, dict[str, Any]]:
    """Run every pair and write ``<out>/PAIRS.json``.

    Args:
        pairs: The runs to make.
        out: Root of the run trees (``<out>/<label>/<hash>/<seed>/``).
        procs: Most processes at a time; 1 runs in this process, in order.
        expect_hash: Label -> the config hash its scenario must have.
        dry_run: Load and hash only; write the manifest with every pair
            ``planned``.
        worker: The run function (tests pass a stand-in; the default is the
            battery's replicate worker).

    Returns:
        The exit status (0 / 1 / 2, as the module docstring says) and the manifest.
    """
    check_pairs(pairs)
    expect_hash = expect_hash or {}
    unknown = sorted(set(expect_hash) - {p.label for p in pairs})
    if unknown:
        raise ValueError(f"--expect-hash names labels without a pair: {', '.join(unknown)}")
    records: list[dict[str, Any]] = []
    payloads: list[tuple[dict[str, Any], int, str]] = []
    mismatch: list[str] = []
    for p in pairs:
        cfg = load_scenario(p.scenario)
        chash = config_hash(cfg)
        want = expect_hash.get(p.label)
        if want is not None and want != chash:
            mismatch.append(
                f"{p.label} ({p.scenario}): hash {chash} (policy v{CONFIG_HASH_VERSION}), "
                f"expected {want}{_older_policy(p.scenario, want)}"
            )
        records.append(
            {
                "label": p.label,
                "scenario": str(p.scenario),
                "seed": p.seed,
                "config_hash": chash,
                "expected_hash": want,
                "run_dir": str(out / p.label / chash / str(p.seed)),
                "status": "planned",
            }
        )
        payloads.append((cfg.model_dump(mode="json"), p.seed, str(out / p.label)))
    manifest: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # additive (2026-10-07): the policy every config_hash here is under (docs/CONTRACTS.md §2)
        "config_hash_version": CONFIG_HASH_VERSION,
        "procs": procs,
        "dry_run": dry_run,
        "pairs": records,
    }
    out.mkdir(parents=True, exist_ok=True)
    if mismatch:
        manifest["refused"] = mismatch
        (out / MANIFEST).write_text(json.dumps(manifest, indent=2))
        for m in mismatch:
            print(f"HASH MISMATCH {m}: nothing run", flush=True)
        return 2, manifest
    for r in records:
        print(f"{r['label']} seed {r['seed']}: config {r['config_hash']} -> {r['run_dir']}")
    if dry_run:
        (out / MANIFEST).write_text(json.dumps(manifest, indent=2))
        return 0, manifest

    def done(i: int, result: tuple[str, str, str, str]) -> None:
        records[i].update(_record(result[0]))
        r = records[i]
        print(
            f"{r['label']} seed {r['seed']}: done ({r['wall_s']} s), "
            f"n_collisions {r['n_collisions']}",
            flush=True,
        )

    def failed(i: int, exc: BaseException) -> None:
        records[i].update(status="failed", error=f"{type(exc).__name__}: {exc}")
        print(f"{records[i]['label']} seed {records[i]['seed']}: FAILED {exc!r}", flush=True)

    if procs <= 1:
        for i, payload in enumerate(payloads):
            try:
                done(i, worker(payload))
            except Exception as exc:
                failed(i, exc)
    else:
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=min(procs, len(payloads)), mp_context=ctx) as ex:
            futures = {ex.submit(worker, pl): i for i, pl in enumerate(payloads)}
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    done(i, fut.result())
                except Exception as exc:  # a run that raised, or a dead worker
                    failed(i, exc)
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2))
    n_failed = sum(1 for r in records if r["status"] != "ok")
    print(f"{len(records) - n_failed} of {len(records)} pair(s) ran; manifest {out / MANIFEST}")
    return (1 if n_failed else 0), manifest


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pairs", nargs="+", help="[LABEL=]SCENARIO.yaml:SEED")
    ap.add_argument("--out", type=Path, required=True, help="root of the run trees")
    ap.add_argument("--procs", type=int, default=1, help="most runs at a time (default 1)")
    ap.add_argument(
        "--expect-hash",
        action="append",
        default=[],
        metavar="LABEL=HASH",
        help="refuse everything when LABEL's scenario does not hash to HASH",
    )
    ap.add_argument("--dry-run", action="store_true", help="load and hash only")
    args = ap.parse_args(argv)
    try:
        pairs = [parse_pair(s) for s in args.pairs]
        for p in pairs:
            if not p.scenario.is_file():
                raise ValueError(f"{p.scenario}: no such scenario file")
        status, _ = run_pairs(
            pairs,
            args.out,
            procs=args.procs,
            expect_hash=_expected_hashes(args.expect_hash),
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        print(f"run_pairs: {exc}", file=sys.stderr)
        return 2
    return status


if __name__ == "__main__":
    sys.exit(main())
