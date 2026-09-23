"""Seeded replicate battery for any corridor with detector observations.

Runs one scenario for N seeds, scores every replicate against a
``flowstate.observations/1`` artifact (docs/CONTRACTS.md, "Detector
observations"), and writes two deliverables:

* a **validation artifact** (JSON) — the acceptance criteria of
  ``validation.criteria`` with pass/fail and the measured value, 95%
  t-distribution confidence intervals over the seeds for every metric and for
  the per-seed GEH pass fraction, RMSPE and criterion wave speed, a per-seed
  table, and full provenance (scenario, config hash, seeds, package versions,
  observations source);
* the **auto-report** (``validation.report.generate_report``) with the
  observed-data block and the two observed criteria rows scored, plus the PDF
  when the ``validation[pdf]`` extra is importable.

Nothing here is corridor-specific: the corridor is whatever the scenario YAML
and the observations artifact describe. The comparison conventions live in
:mod:`validation.observed` and :mod:`validation.battery`, so this script and
``api.jobs.report_job`` compute the same numbers.

**Insertion guard.** Every replicate's realized insertion
(:func:`validation.battery.insertion_stats`: planned vs departed vehicles,
per on-ramp) is printed the moment that replicate finishes, recorded per seed
and pooled into the artifact's ``insertion`` block. A battery whose vehicles
never departed is not a congested corridor but a different scenario, and two
20-seed cloud batteries (2026-09-22) each burned an hour before that was
visible. ``--abort-if-departed-below F`` (default 0.0 = off) makes it cheap:
when the FIRST replicate to finish departed less than fraction ``F`` of its
plan, the remaining workers are killed, a partial artifact stating the abort
is written and the process exits 4.

Per-seed results are written into each replicate directory (``metrics.json``,
``observed_scores.json``) so ``--criteria-only`` can re-score a finished
battery — a threshold profile change, a fresh ring benchmark — without
re-simulating anything. Trajectories are pruned to the first seed afterwards
(``--keep-trajectories`` keeps them all); the report is always generated
*before* pruning, since it re-reads every replicate's field.

Usage (repo root)::

    uv run --no-sync python scripts/corridor_battery.py \\
        --scenario scenarios/X.yaml --observations artifacts/observations_X.json \\
        --replicates 20 --procs 30 --out runs/X/baseline \\
        --artifact artifacts/validation_X.json --report-dir docs/reports/X \\
        --criteria-profile fhwa_default
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import time
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from microsim.demand_adapter import corridor_x_offset_m
from microsim.runner import ReplicatesAborted, RunPaths, _versions, run_replicates
from microsim.scenarios import load_scenario
from validation.battery import (
    InsertionStats,
    aggregate_insertion,
    insertion_stats,
    load_meta,
    mean_finite,
    replicate_wave_speed_kmh,
    score_replicate,
)
from validation.criteria import CriteriaProfile, CriteriaResult, evaluate, get_profile
from validation.metrics import Metrics, aggregate, ci, compute_metrics, geh_pass_fraction
from validation.observed import ObservedCorridor, ObservedScores, pool_scores
from validation.report import generate_report

#: Artifact schema written by this script.
ARTIFACT_SCHEMA = "flowstate.corridor_validation/1"

#: Per-replicate files this script writes beside the run artifacts.
METRICS_FILE = "metrics.json"
SCORES_FILE = "observed_scores.json"

#: Exit code of an insertion abort (``--abort-if-departed-below``).
ABORT_EXIT_CODE = 4


class InsertionGuard:
    """Prints each finished replicate's insertion; optionally aborts the pool.

    Passed to ``microsim.runner.run_replicates`` as its ``on_complete``
    callback, so it runs in the parent the moment a replicate finishes —
    while the rest of the batch is still simulating. The abort test is made
    on the FIRST replicate to finish only: that is the earliest honest
    evidence about a configuration, and it is the whole point of the guard
    (the later replicates are the hour this saves).

    Attributes:
        threshold: Departed fraction the first replicate must reach; 0.0
            disables the abort and leaves the printing.
        stats: The finished replicates' ``(seed, stats)``, in completion
            order — the first entry is the one the abort test was made on.
    """

    def __init__(self, threshold: float) -> None:
        """Args:
        threshold: ``--abort-if-departed-below`` (0.0 = print only).
        """
        self.threshold = float(threshold)
        self.stats: list[tuple[int, InsertionStats]] = []

    def __call__(self, seed: int, paths: RunPaths) -> str | None:
        """Report one finished replicate; return an abort reason or None."""
        stats = insertion_stats(load_meta(paths.run_dir))
        self.stats.append((seed, stats))
        arrived = "arrival not recorded" if stats.arrived is None else f"arrived {stats.arrived}"
        print(
            f"    seed {seed}: departed {stats.departed}/{stats.planned} "
            f"({stats.departed_fraction:.3f}), {arrived} — {stats.verdict}",
            flush=True,
        )
        if self.threshold <= 0.0 or len(self.stats) > 1:
            return None
        if not math.isfinite(stats.departed_fraction):
            # No planned vehicles: the departed fraction is undefined, so the
            # threshold can never be met. An empty demand plan is the most
            # complete insertion failure there is, not a pass.
            return (
                f"{stats.verdict} (no vehicles were planned, so the departed fraction "
                f"--abort-if-departed-below {self.threshold:.3f} asks for is undefined)"
            )
        if stats.departed_fraction >= self.threshold:
            return None
        return (
            f"{stats.verdict} (departed fraction {stats.departed_fraction:.3f} < "
            f"--abort-if-departed-below {self.threshold:.3f})"
        )


def abort_artifact(
    *,
    scenario: str,
    cfg: ScenarioConfig,
    seeds: Sequence[int],
    guard: InsertionGuard,
    reason: str,
    observations_path: str,
    wall_s: float,
) -> dict[str, Any]:
    """Partial artifact for a battery stopped by the insertion guard.

    It carries no criteria and no metrics: nothing was validated, and a
    validation artifact that implied otherwise would be exactly the
    unearned claim CLAUDE.md §0.1 forbids.

    Args:
        scenario: Scenario name or path as the caller gave it.
        cfg: The scenario configuration (for the config hash).
        seeds: The seeds that were planned.
        guard: The guard, holding the replicates that did finish.
        reason: The guard's abort reason.
        observations_path: Observations artifact the battery would have used.
        wall_s: Wall-clock seconds spent before the abort.

    Returns:
        The artifact dict.
    """
    return {
        "schema": ARTIFACT_SCHEMA,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario": scenario,
        "config_hash": config_hash(cfg),
        "seeds": list(seeds),
        "replicates": len(seeds),
        "wall_s": round(wall_s, 1),
        "versions": _versions(),
        "observations_path": observations_path,
        "aborted": True,
        "abort": {
            "reason": reason,
            "threshold_departed_fraction": guard.threshold,
            "n_replicates_completed": len(guard.stats),
            "per_seed": [
                {"seed": seed, "insertion": stats.to_dict()} for seed, stats in guard.stats
            ],
        },
        "criteria": [],
        "per_seed": [],
        "notes": [
            "Battery aborted by --abort-if-departed-below after the first replicate "
            "finished; no criterion was evaluated and no metric was computed.",
            "The replicates listed under abort.per_seed are the only ones that ran; "
            "the remaining seeds were never simulated.",
        ],
    }


def _json_safe(obj: object) -> object:
    """Recursively replace non-finite floats with ``null`` (JSON has no NaN)."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _ci_dict(values: Sequence[float]) -> dict[str, Any]:
    """One replicate CI as JSON (:func:`validation.metrics.ci`)."""
    interval = ci(values)
    return {
        "mean": interval.mean,
        "lo95": interval.lo95,
        "hi95": interval.hi95,
        "n": interval.n,
        "underpowered": interval.underpowered,
    }


def _pdf_available() -> bool:
    """Whether the ``validation[pdf]`` extra (fpdf2) is importable."""
    return importlib.util.find_spec("fpdf") is not None


def seed_dirs(out_root: Path, cfg: ScenarioConfig, seeds: Sequence[int]) -> list[Path]:
    """Replicate directories of ``seeds`` under the run tree (``<hash>/<seed>/``)."""
    root = out_root / config_hash(cfg)
    return [root / str(seed) for seed in seeds]


def analyse_seed(
    run_dir: Path,
    observed: ObservedCorridor,
    *,
    profile: CriteriaProfile,
    x_ref: float,
    span: tuple[float, float],
    x_offset_m: float,
) -> tuple[Metrics, ObservedScores, float, InsertionStats]:
    """Measure one replicate and write its per-seed files.

    Args:
        run_dir: Replicate directory.
        observed: The corridor's observations.
        profile: Criteria profile (its detector measures the wave speed).
        x_ref: Throughput cross-section [m], trajectory coordinates.
        span: Travel-time span [m], trajectory coordinates.
        x_offset_m: Simulation ``x`` of the observed origin [m].

    Returns:
        ``(metrics, scores, wave_speed_kmh, insertion)``.
    """
    metrics = compute_metrics(run_dir, x_ref=x_ref, span=span)
    scores = score_replicate(run_dir, observed, x_offset_m=x_offset_m)
    wave_speed = replicate_wave_speed_kmh(run_dir, profile.wave_detector)
    insertion = insertion_stats(load_meta(run_dir))
    (run_dir / METRICS_FILE).write_text(
        json.dumps(
            _json_safe(
                {
                    "metrics": asdict(metrics),
                    "criterion_wave_speed_kmh": wave_speed,
                    "criterion_detector": profile.wave_detector.name,
                    "x_ref_m": x_ref,
                    "span_m": list(span),
                    "insertion": insertion.to_dict(),
                }
            ),
            indent=2,
            allow_nan=False,
        )
    )
    (run_dir / SCORES_FILE).write_text(
        json.dumps(_json_safe(scores.to_dict()), indent=2, allow_nan=False)
    )
    return metrics, scores, wave_speed, insertion


def load_seed(run_dir: Path) -> tuple[Metrics, ObservedScores, float, InsertionStats]:
    """Re-read one replicate's stored per-seed files (``--criteria-only``).

    The insertion stats are re-read from ``meta.json`` rather than from the
    stored ``metrics.json`` block: ``meta.json`` is the completion marker and
    is never pruned, so there is one source for these counters and no way for
    the stored copy to be the one a reader sees.

    Raises:
        FileNotFoundError: The replicate was never analysed.
    """
    for name in (METRICS_FILE, SCORES_FILE):
        if not (run_dir / name).is_file():
            raise FileNotFoundError(
                f"{run_dir / name} is missing; run the battery without --criteria-only first"
            )
    stored = json.loads((run_dir / METRICS_FILE).read_text())
    raw = dict(stored["metrics"])
    for key, value in raw.items():
        if value is None:
            raw[key] = math.nan
    wave = stored.get("criterion_wave_speed_kmh")
    scores = ObservedScores.from_dict(json.loads((run_dir / SCORES_FILE).read_text()))
    insertion = insertion_stats(load_meta(run_dir))
    return Metrics(**raw), scores, math.nan if wave is None else float(wave), insertion


def ring_block(n_seeds: int, out_dir: Path) -> dict[str, Any] | None:
    """Ring emergence/dampening rows from ``n_seeds`` ring replicates.

    Runs in a spawned child so the parent never loads libsumo (the
    parquet-path clash documented on ``microsim.runner._write_parquet``).
    ``n_seeds = 0`` returns ``None`` and the two rows stay *not evaluated*,
    i.e. failing (CLAUDE.md §0.1).
    """
    if n_seeds <= 0:
        return None
    import multiprocessing as mp

    from validation.ring_benchmark import RING_SCENARIO

    ring_cfg = load_scenario(RING_SCENARIO)
    seeds = spawn_seeds(ring_cfg.seed, n_seeds)
    with mp.get_context("spawn").Pool(1) as pool:
        result: dict[str, Any] = pool.apply(_ring_worker, ((seeds, str(out_dir)),))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ring_benchmark.json").write_text(
        json.dumps(_json_safe(result), indent=2, allow_nan=False)
    )
    return result


def _ring_worker(payload: tuple[list[int], str]) -> dict[str, Any]:
    """Ring benchmark in a child process."""
    from validation.ring_benchmark import evaluate_ring_benchmark

    seeds, out = payload
    return evaluate_ring_benchmark(seeds, Path(out)).to_dict()


def build_artifact(
    *,
    scenario: str,
    cfg: ScenarioConfig,
    profile: CriteriaProfile,
    seeds: Sequence[int],
    dirs: Sequence[Path],
    metrics_list: Sequence[Metrics],
    scores_list: Sequence[ObservedScores],
    wave_speeds: Sequence[float],
    insertion_list: Sequence[InsertionStats],
    observed: ObservedCorridor,
    observations_path: str,
    criteria_rows: Sequence[CriteriaResult],
    ring: dict[str, Any] | None,
    x_offset_m: float,
    wall_s: float,
) -> dict[str, Any]:
    """Assemble the validation artifact for one corridor battery."""
    pooled_geh = [g for s in scores_list for g in s.geh_values]
    per_seed = [
        {
            "seed": seed,
            "run_dir": str(run_dir),
            "geh_pass_fraction": (
                geh_pass_fraction(s.geh_values, profile.geh_threshold) if s.geh_values else None
            ),
            "n_link_hours": s.n_link_hours,
            "rmspe": s.rmspe,
            "n_speed_cells": s.n_speed_cells,
            "criterion_wave_speed_kmh": wave,
            "metrics": asdict(m),
            "insertion": ins.to_dict(),
        }
        for seed, run_dir, s, wave, m, ins in zip(
            seeds, dirs, scores_list, wave_speeds, metrics_list, insertion_list, strict=True
        )
    ]
    insertion = aggregate_insertion(list(insertion_list))
    _, _, _, _, provenance = pool_scores(observed, list(scores_list), path=observations_path)
    return {
        "schema": ARTIFACT_SCHEMA,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario": scenario,
        "corridor": observed.corridor,
        "config_hash": config_hash(cfg),
        "seeds": list(seeds),
        "replicates": len(seeds),
        "wall_s": round(wall_s, 1),
        "versions": _versions(),
        "criteria_profile": {"name": profile.name, "source": profile.source},
        "criteria": [asdict(row) for row in criteria_rows],
        "observations": provenance.to_dict(),
        "x_offset_m": x_offset_m,
        # How much of the configured demand actually entered the network. A
        # battery that inserted a fraction of its plan simulated a different
        # scenario from the one it was asked for, so this block sits beside
        # the criteria rather than inside a per-seed table nobody opens.
        "insertion": None if insertion is None else insertion.to_dict(),
        "geh": {
            "pooled_values": [round(g, 4) for g in pooled_geh],
            "n_comparisons": len(pooled_geh),
            "threshold": profile.geh_threshold,
            "pass_fraction_pooled": (
                geh_pass_fraction(pooled_geh, profile.geh_threshold) if pooled_geh else None
            ),
            "pass_fraction_ci": _ci_dict(
                [
                    geh_pass_fraction(s.geh_values, profile.geh_threshold)
                    if s.geh_values
                    else math.nan
                    for s in scores_list
                ]
            ),
            "definition": (
                "hourly volumes at every mainline station, per replicate, pooled across "
                "replicates for the criterion row; the CI is over the per-replicate pass "
                "fractions"
            ),
        },
        "rmspe": {
            "mean": mean_finite([s.rmspe for s in scores_list]),
            "ci": _ci_dict([s.rmspe for s in scores_list]),
            "n_speed_cells_pooled": sum(s.n_speed_cells for s in scores_list),
        },
        "wave_speed": {
            "detector": profile.wave_detector.name,
            "detector_description": profile.wave_detector.describe(),
            "mean_kmh": mean_finite(wave_speeds),
            "ci": _ci_dict(wave_speeds),
            "n_replicates_with_backward_front": sum(1 for w in wave_speeds if math.isfinite(w)),
        },
        "metrics_ci": {
            name: {
                "mean": interval.mean,
                "lo95": interval.lo95,
                "hi95": interval.hi95,
                "n": interval.n,
                "underpowered": interval.underpowered,
            }
            for name, interval in aggregate(list(metrics_list)).items()
        },
        "per_seed": per_seed,
        "ring": ring,
        "notes": [
            "Observed comparisons cover only the artifact's stations and windows; a "
            "station-window the detector did not measure is skipped, never imputed.",
            "The run's configured warm-up is discarded from every metric and from every "
            "observed comparison.",
            "The wave_speed row is measured with the profile's own detector on its own "
            "bins; validation.metrics.wave_speed_kmh in metrics_ci is the standard "
            "detector's separate diagnostic.",
            "The insertion block counts the vehicles the runs actually put on the "
            "network against their demand plan; a mean departed fraction well under 1 "
            "means the metrics above describe less demand than was configured.",
            (
                "Ring rows evaluated on a fresh ring benchmark."
                if ring is not None
                else "Ring rows not evaluated (--ring-seeds 0); reported as failing per "
                "CLAUDE.md §0.1."
            ),
        ],
    }


def prune_trajectories(dirs: Sequence[Path], keep_first: bool = True) -> int:
    """Delete replicate trajectory files, optionally keeping the first seed's.

    A 20-seed battery on a real corridor writes gigabytes of trajectories; the
    metrics, the observed scores and the report figures are already computed
    by the time this runs, and the first seed's file is kept so the run tree
    stays inspectable.

    Args:
        dirs: Replicate directories.
        keep_first: Keep ``dirs[0]``'s trajectories.

    Returns:
        Number of files deleted.
    """
    deleted = 0
    for i, run_dir in enumerate(dirs):
        if keep_first and i == 0:
            continue
        path = run_dir / "trajectories.parquet"
        if path.is_file():
            path.unlink()
            deleted += 1
    return deleted


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Command-line interface (see the module docstring for the usage line)."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    ap.add_argument("--scenario", required=True, help="scenario name or YAML path")
    ap.add_argument(
        "--observations", required=True, help="flowstate.observations/1 artifact (JSON)"
    )
    ap.add_argument("--replicates", type=int, default=20)
    ap.add_argument("--procs", type=int, default=8, help="simulation processes (one per seed)")
    ap.add_argument("--out", required=True, help="run-tree root for the replicates")
    ap.add_argument("--artifact", required=True, help="validation artifact path (JSON)")
    ap.add_argument("--report-dir", required=True, help="directory for report.md and figures")
    ap.add_argument("--criteria-profile", default="fhwa_default")
    ap.add_argument(
        "--criteria-only",
        action="store_true",
        help="re-score from the stored per-seed files; never re-simulates",
    )
    ap.add_argument(
        "--keep-trajectories",
        action="store_true",
        help="keep every replicate's trajectories.parquet (default: only the first seed's)",
    )
    ap.add_argument(
        "--abort-if-departed-below",
        type=float,
        default=0.0,
        metavar="F",
        help=f"stop the battery (exit {ABORT_EXIT_CODE}) when the first replicate to "
        "finish departed less than fraction F of its planned vehicles; 0.0 (the "
        "default) only prints the per-replicate insertion verdict",
    )
    ap.add_argument(
        "--ring-seeds",
        type=int,
        default=0,
        help="seeds for the ring emergence/dampening rows (0 = not evaluated)",
    )
    ap.add_argument(
        "--x-offset-m",
        type=float,
        default=None,
        help="simulation x of the observed origin [m]; default: the corridor's upstream "
        "insertion buffer (0 for ring/OSM networks)",
    )
    ap.add_argument("--title", default="FlowState calibration & validation report")
    return ap.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run (or re-score) one corridor battery; returns a process exit code."""
    args = parse_args(argv)
    t0 = time.perf_counter()
    profile = get_profile(args.criteria_profile)
    observed = ObservedCorridor.from_json(args.observations)
    cfg = load_scenario(args.scenario).model_copy(update={"replicates": args.replicates})
    seeds = spawn_seeds(cfg.seed, args.replicates)
    out_root = Path(args.out)
    dirs = seed_dirs(out_root, cfg, seeds)
    x_offset = corridor_x_offset_m(cfg) if args.x_offset_m is None else float(args.x_offset_m)
    x_refs = observed.mainline_x_refs()
    if len(x_refs) < 2:
        print("the observations define fewer than two positioned mainline stations", flush=True)
        return 2
    span = (x_refs[0] + x_offset, x_refs[-1] + x_offset)
    x_ref = x_refs[len(x_refs) // 2] + x_offset

    guard = InsertionGuard(args.abort_if_departed_below)
    if not args.criteria_only:
        print(
            f"{args.scenario}: {args.replicates} replicate(s), config {config_hash(cfg)} ...",
            flush=True,
        )
        try:
            run_replicates(
                cfg,
                out_root,
                n_procs=max(1, min(args.procs, args.replicates)),
                on_complete=guard,
            )
        except ReplicatesAborted as exc:
            artifact_path = Path(args.artifact)
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(
                    _json_safe(
                        abort_artifact(
                            scenario=str(args.scenario),
                            cfg=cfg,
                            seeds=seeds,
                            guard=guard,
                            reason=exc.reason,
                            observations_path=str(args.observations),
                            wall_s=time.perf_counter() - t0,
                        )
                    ),
                    indent=2,
                    allow_nan=False,
                )
            )
            print(
                f"ABORTED after {len(guard.stats)} replicate(s): {exc.reason}. "
                f"Nothing was validated; partial artifact -> {artifact_path}",
                flush=True,
            )
            return ABORT_EXIT_CODE

    metrics_list: list[Metrics] = []
    scores_list: list[ObservedScores] = []
    wave_speeds: list[float] = []
    insertion_list: list[InsertionStats] = []
    for seed, run_dir in zip(seeds, dirs, strict=True):
        if args.criteria_only:
            metrics, scores, wave, insertion = load_seed(run_dir)
            # Outside --criteria-only the guard already printed this line the
            # moment the replicate finished, which is the point of it.
            print(f"    seed {seed}: {insertion.verdict}", flush=True)
        else:
            metrics, scores, wave, insertion = analyse_seed(
                run_dir,
                observed,
                profile=profile,
                x_ref=x_ref,
                span=span,
                x_offset_m=x_offset,
            )
        metrics_list.append(metrics)
        scores_list.append(scores)
        wave_speeds.append(wave)
        insertion_list.append(insertion)

    ring = ring_block(args.ring_seeds, out_root / "ring")
    pooled_geh, mean_rmspe, sim_speeds, obs_speeds, provenance = pool_scores(
        observed, scores_list, path=str(args.observations)
    )
    criteria_rows = evaluate(
        profile,
        geh_values=pooled_geh or None,
        rmspe_value=mean_rmspe if math.isfinite(mean_rmspe) else None,
        wave_speed_kmh=mean_finite(wave_speeds),
        wave_detector=profile.wave_detector,
        ring_emergence=None if ring is None else bool(ring["emergence"]["passed"]),
        ring_dampening=None if ring is None else bool(ring["dampening"]["passed"]),
        n_seeds=len(seeds),
    )

    report_dir = Path(args.report_dir)
    report_path: Path | None = None
    if all((d / "trajectories.parquet").is_file() for d in dirs):
        result = generate_report(
            out_root,
            report_dir / "report.md",
            profile=profile,
            geh_values=pooled_geh or None,
            rmspe_value=mean_rmspe if math.isfinite(mean_rmspe) else None,
            title=args.title,
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            x_ref=x_ref,
            span=span,
            segment_speeds_obs=obs_speeds,
            segment_speeds_sim=sim_speeds,
            segment_window_s=observed.window_s,
            observed=provenance,
            pdf=_pdf_available(),
        )
        report_path = result[0] if isinstance(result, tuple) else result
    else:
        print(
            "report skipped: some replicates no longer hold trajectories.parquet (pruned by an "
            "earlier run); re-run without --criteria-only to regenerate it",
            flush=True,
        )

    artifact = build_artifact(
        scenario=str(args.scenario),
        cfg=cfg,
        profile=profile,
        seeds=seeds,
        dirs=dirs,
        metrics_list=metrics_list,
        scores_list=scores_list,
        wave_speeds=wave_speeds,
        insertion_list=insertion_list,
        observed=observed,
        observations_path=str(args.observations),
        criteria_rows=criteria_rows,
        ring=ring,
        x_offset_m=x_offset,
        wall_s=time.perf_counter() - t0,
    )
    artifact["report_path"] = None if report_path is None else str(report_path)
    artifact_path = Path(args.artifact)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(_json_safe(artifact), indent=2, allow_nan=False))

    if not args.keep_trajectories and not args.criteria_only:
        deleted = prune_trajectories(dirs)
        if deleted:
            print(f"pruned {deleted} trajectory file(s); the first seed's is kept", flush=True)

    summary = aggregate_insertion(insertion_list)
    if summary is not None:
        print(
            f"    {'insertion':<18} {summary.verdict:<14} "
            f"departed {summary.departed}/{summary.planned} "
            f"(mean {summary.mean_departed_fraction:.3f}, lowest "
            f"{summary.min_departed_fraction:.3f})",
            flush=True,
        )
    for row in criteria_rows:
        state = ("PASS" if row.passed else "FAIL") if row.evaluated else "NOT EVALUATED"
        print(f"    {row.name:<18} {state:<14} {row.value}  ({row.threshold})", flush=True)
    print(
        f"done in {time.perf_counter() - t0:.0f} s -> {artifact_path}"
        + (f", {report_path}" if report_path is not None else ""),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
