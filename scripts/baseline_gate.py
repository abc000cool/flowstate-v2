"""Evaluate the corridor study protocol's baseline gate on a finished battery.

The gate (docs/FRISCO_PROTOCOL.md §6, :mod:`validation.baseline_gate`) asks
whether the no-strategy model reproduces the corridor on the calibration days
and, unchanged, on the validation days: C1 link flows, C3 speeds at 15-minute
aggregation and C6 bottlenecks on both day sets, C5 collisions on every run,
C4 wave speed passing or not applicable; C2 (Texas GEH) is reported beside C1.

It runs on a finished ``scripts/corridor_battery.py`` run tree — one
``<seed>/`` directory per replicate holding ``meta.json``, ``metrics.json`` and
``observed_scores.json`` (pruned trajectories are not needed): each
replicate's stored simulated side is paired with the calibration-day and the
validation-day observations (:func:`validation.baseline_gate.rescore`). The
two artifacts must share the station table and window grid of the artifact
the battery was scored against (``--scored-against``; by default the battery
artifact's ``observations.path``); ``scripts/observations_for_dates.py``
builds them from the corridor's detector frame, quality-masked.

The calibration-day artifact (``--calibration-observations``) and the study's
day split (``--day-split``) are required: the gate checks that each day set's
artifact holds exactly its side of the split and that the two sides are
disjoint, so the battery's all-dates artifact is never scored as the
calibration days (protocol §3.2). ``--per-day DIR`` scores C1, C3 and C6
against each single-day validation artifact in ``DIR`` (``*.json``, one date
each, on the split's validation side) and adds the per-day table to the gate
(protocol §3.5; reported, not gating).

Writes the gate as JSON (``flowstate.baseline_gate/1``) and markdown, prints
the verdict and every check, and with ``--write-into-artifact`` adds the
``baseline_gate`` block to the battery artifact (every other key unchanged).
Exit code 0 whatever the verdict (the verdict is a result, not an error);
2 for unusable inputs.

Run (a cloud VM, the Minnesota rehearsal; paths as the pipeline writes them):

    uv run --no-sync python scripts/baseline_gate.py \\
        --battery-artifact artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg.json \\
        --calibration-observations runs/rehearsal/observations_calibration.json \\
        --validation-observations runs/rehearsal/observations_validation.json \\
        --day-split runs/rehearsal/day_split.json \\
        --per-day runs/rehearsal/validation_days \\
        --out-json artifacts/baseline_gate_mndot_rehearsal.json \\
        --out-md docs/reports/mndot_rehearsal/baseline_gate.md
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from validation.baseline_gate import GateResult, gate_from_replicates, render_markdown
from validation.battery import (
    METRICS_FILE,
    SCORES_FILE,
    collision_counts,
    json_safe,
    load_meta,
    load_replicate_analysis,
)
from validation.observed import ObservedCorridor


def run_dirs_from(runs: Path | None, artifact: dict[str, Any] | None) -> list[Path]:
    """Replicate directories: ``--runs``' children with a ``meta.json``, else the artifact's.

    Raises:
        ValueError: Neither gives a replicate.
    """
    if runs is not None:
        dirs = sorted(
            (p for p in runs.iterdir() if (p / "meta.json").is_file()),
            key=lambda p: p.name,
        )
    elif artifact is not None:
        dirs = [Path(str(row["run_dir"])) for row in artifact.get("per_seed", ())]
    else:
        dirs = []
    if not dirs:
        raise ValueError("no replicate directories: give --runs or a --battery-artifact")
    return dirs


def stored_detector(run_dir: Path) -> str:
    """The wave detector a replicate's ``metrics.json`` says it was read with."""
    stored = json.loads((run_dir / METRICS_FILE).read_text())
    return str(stored.get("criterion_detector") or "")


def split_summary(path: Path) -> dict[str, Any]:
    """The day split's summary as the gate records it (dates, seed, underpowered)."""
    raw = json.loads(path.read_text())
    return {
        "path": str(path),
        "seed": raw.get("seed"),
        "calibration_dates": raw.get("calibration_dates", []),
        "validation_dates": raw.get("validation_dates", []),
        "underpowered": raw.get("underpowered"),
        "underpowered_reason": raw.get("underpowered_reason", ""),
    }


def per_day_artifacts(directory: Path) -> list[tuple[str, ObservedCorridor]]:
    """``(path, artifact)`` of every ``*.json`` in ``directory``, name order.

    Raises:
        ValueError: Not a directory, no ``*.json`` in it, or a file that is not
            an observations artifact.
    """
    if not directory.is_dir():
        raise ValueError(f"--per-day {directory}: not a directory")
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError(f"--per-day {directory}: no *.json single-day artifacts in it")
    out: list[tuple[str, ObservedCorridor]] = []
    for path in paths:
        try:
            out.append((str(path), ObservedCorridor.from_json(path)))
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"--per-day {path}: not an observations artifact ({exc})") from exc
    return out


def build_parser() -> argparse.ArgumentParser:
    """The command line (module docstring)."""
    parser = argparse.ArgumentParser(
        prog="python scripts/baseline_gate.py",
        description="Evaluate the protocol's baseline gate on a finished battery.",
    )
    parser.add_argument(
        "--runs",
        type=Path,
        help="the battery's configuration directory (<out>/<config_hash>) with one <seed>/ "
        "per replicate",
    )
    parser.add_argument(
        "--battery-artifact",
        type=Path,
        help="the battery's validation artifact (seeds, run dirs, scenario, observations path)",
    )
    parser.add_argument(
        "--scored-against",
        type=Path,
        help="observations the stored scores were computed against (default: the battery "
        "artifact's observations.path)",
    )
    parser.add_argument(
        "--calibration-observations",
        type=Path,
        required=True,
        help="calibration-day observations (its dates must be the split's calibration side)",
    )
    parser.add_argument(
        "--validation-observations",
        type=Path,
        help="validation-day observations; without them the gate fails",
    )
    parser.add_argument(
        "--day-split", type=Path, required=True, help="the study's flowstate.day_split/1 JSON"
    )
    parser.add_argument(
        "--per-day",
        type=Path,
        metavar="DIR",
        help="directory of single-day validation artifacts (*.json): C1, C3 and C6 per "
        "validation day, reported beside the gate (protocol section 3.5), not gating",
    )
    parser.add_argument("--out-json", required=True, type=Path, help="gate JSON path")
    parser.add_argument("--out-md", type=Path, help="gate markdown path")
    parser.add_argument(
        "--write-into-artifact",
        action="store_true",
        help="also add the baseline_gate block to --battery-artifact",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Evaluate the gate; returns the process exit code."""
    args = build_parser().parse_args(argv)
    artifact: dict[str, Any] | None = None
    if args.battery_artifact is not None:
        artifact = json.loads(args.battery_artifact.read_text())
    try:
        dirs = run_dirs_from(args.runs, artifact)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    scored_path = args.scored_against
    if scored_path is None and artifact is not None:
        path = (artifact.get("observations") or {}).get("path")
        scored_path = Path(str(path)) if path else None
    if scored_path is None:
        print(
            "give --scored-against (the observations the battery scored its replicates against)",
            file=sys.stderr,
        )
        return 2
    for run_dir in dirs:
        for name in ("meta.json", METRICS_FILE, SCORES_FILE):
            if not (run_dir / name).is_file():
                print(
                    f"{run_dir / name} is missing; the battery has not scored it", file=sys.stderr
                )
                return 2
    scored_against = ObservedCorridor.from_json(scored_path)
    cal_path = args.calibration_observations
    calibration = ObservedCorridor.from_json(cal_path)
    validation = (
        None
        if args.validation_observations is None
        else ObservedCorridor.from_json(args.validation_observations)
    )
    analyses = [load_replicate_analysis(d) for d in dirs]
    metas = [load_meta(d) for d in dirs]
    detectors = {stored_detector(d) for d in dirs}
    detector = detectors.pop() if len(detectors) == 1 else ""
    hashes = {str(m.get("config_hash", "")) for m in metas}
    config_hash = hashes.pop() if len(hashes) == 1 else ""
    if not config_hash:
        print("the replicates carry more than one config_hash; one configuration per gate")
        return 2
    split = split_summary(args.day_split)
    validation_days = None
    if args.per_day is not None:
        try:
            validation_days = per_day_artifacts(args.per_day)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    scenario = str((artifact or {}).get("scenario") or "")
    gate: GateResult = gate_from_replicates(
        [a.scores for a in analyses],
        scored_against=scored_against,
        calibration=calibration,
        validation=validation,
        wave_speeds_kmh=[a.wave_speed_kmh for a in analyses],
        wave_detector=detector,
        collision_counts=collision_counts(metas),
        calibration_path=str(cal_path),
        validation_path=str(args.validation_observations or ""),
        config_hash=config_hash,
        scenario=scenario,
        split=split,
        validation_days=validation_days,
    )
    gate.to_json(args.out_json)
    if args.out_md is not None:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(render_markdown(gate))
    if args.write_into_artifact:
        if artifact is None or args.battery_artifact is None:
            print("--write-into-artifact needs --battery-artifact", file=sys.stderr)
            return 2
        artifact["baseline_gate"] = gate.to_dict()
        args.battery_artifact.write_text(json.dumps(json_safe(artifact), indent=2, allow_nan=False))
    print(gate.headline())
    for check in gate.checks:
        mark = "" if check.gating else " (not gating)"
        print(f"  {check.check:<10} {check.day_set:<12} {check.status:<14}{mark} {check.plain}")
    if gate.per_day is not None:
        print("validation days one by one (reported, not gating):")
        for row in gate.per_day["rows"]:
            statuses = ", ".join(f"{c['check']} {c['status']}" for c in row["checks"])
            print(f"  {row['date']}: {statuses}")
        for item in gate.per_day["refused"]:
            print(f"  not scored: {item['path']} ({item['reason']})")
        if gate.per_day["missing_dates"]:
            print(f"  no single-day artifact for {', '.join(gate.per_day['missing_dates'])}")
    finite = [a.wave_speed_kmh for a in analyses if math.isfinite(a.wave_speed_kmh)]
    print(
        f"{len(dirs)} replicate(s) of {config_hash}; {len(finite)} with a backward front; "
        f"wrote {args.out_json}" + (f", {args.out_md}" if args.out_md else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
