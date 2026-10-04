"""Unmeasured ramp volumes from mainline counts, and their leave-one-out check
(WP-101, Stage 1 item 3).

Estimates every ramp without a usable count from station-to-station
conservation with :mod:`calibration.ramp_estimation` and writes
``ramp_estimates.json`` (schema ``flowstate.ramp_estimates/1``) and
``ramp_estimates.md`` to ``--out``. ``--leave-one-out`` instead treats each
*measured* ramp in turn as unmeasured, estimates it and scores it against its
own detector, writing ``ramp_leave_one_out.json`` (schema
``flowstate.ramp_leave_one_out/1``) and ``ramp_leave_one_out.md``. Both
record the inputs' hashes, the commit and ``code_dirty``.

``--apply-quality`` first runs :mod:`calibration.data_quality` on the same
inputs and masks what it excludes or sets aside (a ramp loop that reads zero
all day is then an unmeasured ramp, not a measured one); the quality summary
is recorded in the output. Inputs are found as the onboarding path finds them
(:mod:`calibration.detector_inputs`); per-lane data are summed to station
totals.

Run (VM; the MnDOT corridor, whose ramps are measured):

    uv run --no-sync python scripts/ramp_estimate.py \
        --corridor-dir data/mndot/mndot_i94_wb_stpaul --apply-quality \
        --leave-one-out --out runs/wp101/ramp_loo

A corridor with an unmeasured entrance and exit in one segment needs a split
assumption per extra ramp (``--splits splits.json``: a JSON list of
``{"ramp", "kind", "value", "source"[, "other"]}`` objects, kinds
``share_of_upstream``, ``fixed_veh_h``, ``ratio_to``).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from calibration.conservation import (
    CONGESTED_SPEED_MS,
    DEFAULT_COUNT_ERROR,
    DEFAULT_LAG_SPEED_MS,
    DEFAULT_PERIOD_S,
    detector_grid,
    parse_ramp,
)
from calibration.data_quality import assess_quality, mask_grid
from calibration.detector_inputs import add_input_arguments, inputs_from_args, split_list
from calibration.ramp_estimation import (
    SplitAssumption,
    estimate_ramps,
    leave_one_out,
    render_leave_one_out_markdown,
    render_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The paths whose uncommitted changes make the output's ``code_dirty`` true:
#: this script and the code it imports (tests/test_scripts/test_code_dirty.py
#: convention — a rewritten data file is not dirty code).
CODE_PATHS = (
    "scripts/ramp_estimate.py",
    "packages/calibration",
    "packages/flowstate_core",
    "pyproject.toml",
    "uv.lock",
)


def git_head() -> str:
    """The code's commit (``"<sha> <subject>"``), or ``"unknown"``."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H %s"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def git_dirty() -> bool | None:
    """Whether :data:`CODE_PATHS` had uncommitted changes (None without git)."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no", "--", *CODE_PATHS],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def build_parser() -> argparse.ArgumentParser:
    """The command line (module docstring)."""
    parser = argparse.ArgumentParser(
        prog="python scripts/ramp_estimate.py",
        description="Estimate unmeasured ramp volumes from mainline station counts.",
    )
    add_input_arguments(parser)
    parser.add_argument(
        "--ramp",
        action="append",
        default=[],
        metavar="ID:on|off:X_M",
        help="a ramp that exists without a detector (repeatable)",
    )
    parser.add_argument(
        "--unmeasured", default="", help="comma-separated ramps with counts to treat as unmeasured"
    )
    parser.add_argument("--splits", type=Path, help="JSON list of split assumptions")
    parser.add_argument("--period-s", type=float, default=DEFAULT_PERIOD_S)
    parser.add_argument("--count-error", type=float, default=DEFAULT_COUNT_ERROR)
    parser.add_argument("--combination", default="linear", choices=["linear", "quadrature"])
    parser.add_argument("--default-speed-ms", type=float, default=DEFAULT_LAG_SPEED_MS)
    parser.add_argument("--congested-speed-ms", type=float, default=CONGESTED_SPEED_MS)
    parser.add_argument("--max-lag-s", type=float, help="lag cap [s] (default: one period)")
    parser.add_argument(
        "--apply-quality",
        action="store_true",
        help="mask what calibration.data_quality excludes or sets aside first",
    )
    parser.add_argument(
        "--leave-one-out",
        action="store_true",
        help="validate on measured ramps instead of estimating unmeasured ones",
    )
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    return parser


def load_splits(path: Path | None) -> list[SplitAssumption]:
    """The ``--splits`` file → assumptions.

    Raises:
        ValueError: Not a JSON list of objects.
    """
    if path is None:
        return []
    raw = json.loads(path.read_text())
    if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
        raise ValueError(f"{path}: expected a JSON list of split objects")
    return [SplitAssumption.from_mapping(r) for r in raw]


def main(argv: list[str] | None = None) -> int:
    """Run the estimate (or the leave-one-out check); returns the exit code."""
    args = build_parser().parse_args(argv)
    if args.corridor_dir is None and args.detectors is None and args.lanes_from_cache is None:
        print("give --corridor-dir, --detectors or --lanes-from-cache", file=sys.stderr)
        return 2
    inputs = inputs_from_args(args)
    grid = detector_grid(
        inputs.frame,
        dates=split_list(args.dates) or None,
        start_local=args.start,
        end_local=args.end,
    )
    extra = [parse_ramp(r) for r in args.ramp]
    quality: dict[str, Any] | None = None
    if args.apply_quality:
        report = assess_quality(
            grid,
            stations=inputs.stations,
            extra_ramps=extra,
            count_error=args.count_error,
            combination=args.combination,
            period_s=args.period_s,
        )
        grid = mask_grid(grid, report)
        excluded = sorted(
            {f"{sd.sensor} {sd.date}" for sd in report.sensor_days if sd.verdict == "exclude"}
        )
        quality = {
            "applied": True,
            "summary": report.summary(),
            "thresholds": report.thresholds.to_dict(),
            "excluded_sensor_days": excluded,
        }
    options: dict[str, Any] = {
        "stations": inputs.stations,
        "extra_ramps": extra,
        "unmeasured": split_list(args.unmeasured),
        "splits": load_splits(args.splits),
        "period_s": args.period_s,
        "count_error": args.count_error,
        "combination": args.combination,
        "default_speed_ms": args.default_speed_ms,
        "congested_speed_ms": args.congested_speed_ms,
        "max_lag_s": args.max_lag_s,
    }
    provenance: dict[str, Any] = {
        "script": "scripts/ramp_estimate.py",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code": git_head(),
        "code_dirty": git_dirty(),
        "argv": list(sys.argv[1:] if argv is None else argv),
        "inputs": inputs.provenance,
        "quality": quality or {"applied": False},
    }
    shown = {
        "Inputs": inputs.provenance.get("detectors") or inputs.provenance.get("cache_dir") or "",
        "Data hash (sha256 of the loaded rows)": inputs.provenance["frame_sha256"][:16],
        "Data-quality masking": "applied" if quality else "not applied",
        "Code": provenance["code"][:12]
        + (" (uncommitted changes)" if provenance["code_dirty"] else ""),
        "Created": provenance["created_at"],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    if args.leave_one_out:
        loo = leave_one_out(grid, **options)
        payload = loo.to_dict()
        name = "ramp_leave_one_out"
        markdown = render_leave_one_out_markdown(loo, provenance=shown)
        pooled = loo.pooled
        tested = sum(1 for r in loo.ramps if r.status == "tested")
        rel = pooled["relative_error"]
        cov = pooled["coverage"]
        line = (
            f"{tested} of {len(loo.ramps)} measured ramps tested; pooled relative error "
            f"{'n/a' if rel is None else f'{rel:.1%}'}, interval coverage "
            f"{'n/a' if cov is None else f'{cov:.0%}'}"
        )
    else:
        result = estimate_ramps(grid, **options)
        payload = result.to_dict()
        name = "ramp_estimates"
        markdown = render_markdown(result, provenance=shown)
        n_est = sum(1 for e in result.estimates if e.status == "estimated")
        line = f"{n_est} of {len(result.estimates)} unmeasured ramps estimated"
    payload["provenance"] = provenance
    (args.out / f"{name}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    (args.out / f"{name}.md").write_text(markdown)
    print(line)
    print(f"wrote {args.out / (name + '.json')} and {args.out / (name + '.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
