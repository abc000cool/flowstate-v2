"""Detector data-quality report for a corridor (WP-101, Stage 1 item 2).

Judges every detector-day ``ok`` / ``suspect`` / ``exclude`` and checks the
station-to-station mass balance with :mod:`calibration.data_quality`, then
writes ``data_quality.json`` (the full record, schema
``flowstate.data_quality/1``, with the inputs' hashes, the commit and
``code_dirty``) and ``data_quality.md`` (the plain-language summary for a
client) to ``--out``.

Inputs are found as the onboarding path finds them
(:mod:`calibration.detector_inputs`): a corridor directory, a generic
detector CSV, or per-lane MnDOT data from the raw 30-second cache.

Run (VM; the MnDOT corridor, station totals, then per lane from the cache):

    uv run --no-sync python scripts/data_quality_report.py \
        --corridor-dir data/mndot/mndot_i94_wb_stpaul --out runs/wp101/dq_stations
    uv run --no-sync python scripts/data_quality_report.py \
        --corridor-dir data/mndot/mndot_i94_wb_stpaul \
        --lanes-from-cache data/mndot/cache \
        --metro-config data/mndot/config/metro_config.xml.gz --out runs/wp101/dq_lanes

A generic export (e.g. a state DOT's 5-minute per-lane CSV):

    uv run --no-sync python scripts/data_quality_report.py \
        --detectors export.csv --stations stations.csv --lane-column Lane \
        --column-map '{"timestamp": "Time", "station": "Station", "flow": "Volume"}' \
        --speed-unit mph --out runs/pilot/dq
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from calibration.conservation import DEFAULT_COUNT_ERROR, DEFAULT_PERIOD_S, parse_ramp
from calibration.data_quality import assess_quality, render_markdown
from calibration.detector_inputs import add_input_arguments, inputs_from_args, split_list

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The paths whose uncommitted changes make the report's ``code_dirty`` true:
#: this script and the code it imports (tests/test_scripts/test_code_dirty.py
#: convention — a rewritten data file is not dirty code).
CODE_PATHS = (
    "scripts/data_quality_report.py",
    "packages/calibration",
    "packages/flowstate_core",
    "pyproject.toml",
    "uv.lock",
)

JSON_NAME = "data_quality.json"
MARKDOWN_NAME = "data_quality.md"


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
        prog="python scripts/data_quality_report.py",
        description="Judge every detector-day and check the station mass balance.",
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
        "--exempt-lanes",
        default="",
        help="comma-separated lane sensors (station:lane) exempt from the lane check",
    )
    parser.add_argument("--count-error", type=float, default=DEFAULT_COUNT_ERROR)
    parser.add_argument("--combination", default="linear", choices=["linear", "quadrature"])
    parser.add_argument("--period-s", type=float, default=DEFAULT_PERIOD_S)
    parser.add_argument("--no-mass-balance", action="store_true")
    parser.add_argument("--title", default="Detector data quality")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the report; returns the process exit code."""
    args = build_parser().parse_args(argv)
    if args.corridor_dir is None and args.detectors is None and args.lanes_from_cache is None:
        print("give --corridor-dir, --detectors or --lanes-from-cache", file=sys.stderr)
        return 2
    inputs = inputs_from_args(args)
    report = assess_quality(
        inputs.frame,
        stations=inputs.stations,
        extra_ramps=[parse_ramp(r) for r in args.ramp],
        count_error=args.count_error,
        combination=args.combination,
        period_s=args.period_s,
        exempt_lanes=split_list(args.exempt_lanes),
        mass_balance=not args.no_mass_balance,
        dates=split_list(args.dates) or None,
        start_local=args.start,
        end_local=args.end,
    )
    provenance: dict[str, Any] = {
        "script": "scripts/data_quality_report.py",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code": git_head(),
        "code_dirty": git_dirty(),
        "argv": list(sys.argv[1:] if argv is None else argv),
        "inputs": inputs.provenance,
    }
    payload = report.to_dict()
    payload["provenance"] = provenance
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / JSON_NAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    shown = {
        "Inputs": inputs.provenance.get("detectors") or inputs.provenance.get("cache_dir") or "",
        "Data hash (sha256 of the loaded rows)": inputs.provenance["frame_sha256"][:16],
        "Code": provenance["code"][:12]
        + (" (uncommitted changes)" if provenance["code_dirty"] else ""),
        "Created": provenance["created_at"],
    }
    (args.out / MARKDOWN_NAME).write_text(
        render_markdown(report, title=args.title, provenance=shown)
    )
    s = report.summary()
    print(
        f"{s['n_sensor_days']} sensor-days: {s['n_ok']} ok, {s['n_suspect']} suspect, "
        f"{s['n_exclude']} excluded; usable {s['usable_share']:.1%} of expected readings"
    )
    print(f"wrote {args.out / JSON_NAME} and {args.out / MARKDOWN_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
