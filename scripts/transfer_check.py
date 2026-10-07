"""Do our driver settings fit this corridor? (WP-103, Frisco plan Stage 1 item 8).

Compares a driver population (a scenario's fleet block, or an
``IDMCalibration`` artifact) with a corridor's detector data —
free-flow speed, capacity per lane and truck share — with
:mod:`calibration.transfer_check`, and recommends a corridor-wide adjustment
inside the measured ranges where they disagree. Writes
``transfer_check.json`` (schema ``flowstate.transfer_check/1``, with the
inputs' hashes, the commit and ``code_dirty``) and ``transfer_check.md``
(the plain-language summary for a client) to ``--out``. The JSON's
free-flow and capacity comparisons carry each driver knob's
``uncertainty_range`` (WP-106b), which ``scripts/uncertainty_runs.py
--transfer-check`` reads for the protocol §8.5 uncertainty runs.

Inputs are found as the onboarding path finds them
(:mod:`calibration.detector_inputs`): a corridor directory, a generic
detector CSV, or per-lane MnDOT data from the raw 30-second cache. Every
detector-day is first judged by :mod:`calibration.data_quality` and what it
excludes or sets aside is not used. Pass the study's calibration days with
``--dates`` and its hours with ``--start``/``--end`` (docs/FRISCO_PROTOCOL.md
§3). The posted limit comes from ``--speed-limit`` or the stations table's
``speed_limit_ms``.

Run (VM; the Minnesota corridor against the weave scenario's population):

    uv run --no-sync python scripts/transfer_check.py \
        --corridor-dir data/mndot/mndot_i94_wb_stpaul \
        --scenario scenarios/mndot_i94_wb_stpaul_weave.yaml \
        --out runs/wp103/transfer_mndot

A generic export with classification counts:

    uv run --no-sync python scripts/transfer_check.py \
        --detectors export.csv --stations stations.csv --lane-column Lane \
        --column-map '{"timestamp": "Time", "station": "Station", "flow": "Volume"}' \
        --speed-unit mph --speed-limit 65 \
        --classification classes.csv --heavy-definition "FHWA classes 5-13" \
        --population artifacts/idm_i24_capacity.json --out runs/pilot/transfer
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from calibration.detector_inputs import (
    add_input_arguments,
    file_sha256,
    inputs_from_args,
    split_list,
)
from calibration.loaders.pems import MPH_TO_MS
from calibration.transfer_check import (
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_N_BOOTSTRAP,
    MODEL_DRAW_SEED,
    MODEL_DRAWS,
    check_transfer,
    heavy_share_from_classification,
    observe,
    population_from_artifact,
    population_from_scenario,
    render_markdown,
)
from flowstate_core.units import kmh_to_ms

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The paths whose uncommitted changes make the report's ``code_dirty`` true:
#: this script and the code it imports (tests/test_scripts/test_code_dirty.py
#: convention — a rewritten data file is not dirty code).
CODE_PATHS = (
    "scripts/transfer_check.py",
    "packages/calibration",
    "packages/flowstate_core",
    "pyproject.toml",
    "uv.lock",
)

JSON_NAME = "transfer_check.json"
MARKDOWN_NAME = "transfer_check.md"


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


def speed_limit_ms(value: float | None, unit: str) -> float | None:
    """``--speed-limit`` in m/s."""
    if value is None:
        return None
    if unit == "mph":
        return float(value) * MPH_TO_MS
    if unit == "kmh":
        return kmh_to_ms(float(value))
    return float(value)


def build_parser() -> argparse.ArgumentParser:
    """The command line (module docstring)."""
    parser = argparse.ArgumentParser(
        prog="python scripts/transfer_check.py",
        description="Compare a driver population with a corridor's detector data.",
    )
    add_input_arguments(parser)
    who = parser.add_argument_group("population (one of)")
    who.add_argument("--scenario", type=Path, help="scenario YAML whose fleet block is checked")
    who.add_argument("--population", type=Path, help="IDMCalibration artifact (passengers only)")
    who.add_argument(
        "--cf-model",
        default="IDM",
        choices=["IDM", "EIDM"],
        help="car-following model the --population runs under",
    )
    road = parser.add_argument_group("corridor")
    road.add_argument("--speed-limit", type=float, help="the corridor's posted limit")
    road.add_argument("--speed-limit-unit", default="mph", choices=["mph", "kmh", "ms"])
    road.add_argument(
        "--only-stations",
        default="",
        help="comma-separated mainline stations to use (the study's selection)",
    )
    road.add_argument(
        "--exempt-lanes",
        default="",
        help="comma-separated lane sensors (station:lane) exempt from the lane check",
    )
    road.add_argument(
        "--no-quality",
        action="store_true",
        help="skip the data-quality masking (not for a study: every reading is then used)",
    )
    cls = parser.add_argument_group("truck share")
    cls.add_argument("--classification", type=Path, help="classification counts CSV")
    cls.add_argument(
        "--classification-columns",
        help="JSON object: canonical name (station, timestamp, date, heavy_count, total_count, "
        "heavy_share) -> the CSV's column",
    )
    cls.add_argument("--heavy-definition", default="", help="what the source counts as heavy")
    cap = parser.add_argument_group("simulated capacity")
    cap.add_argument(
        "--capacity-sidecar",
        action="append",
        default=[],
        type=Path,
        help="a scripts/calibrate_capacity.py sidecar to consider for the simulated capacity "
        "(repeatable; replaces discovery in artifacts/). The measured source of a derived "
        "population (the protocol §7.2 measured range) is still read from the sidecars beside "
        "it as well",
    )
    cap.add_argument(
        "--no-sidecar-discovery",
        action="store_true",
        help="consider no simulated capacity unless named with --capacity-sidecar; the "
        "measured source of a derived population is still read from the sidecars beside it",
    )
    stat = parser.add_argument_group("statistics")
    stat.add_argument("--n-bootstrap", type=int, default=DEFAULT_N_BOOTSTRAP)
    stat.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    stat.add_argument("--draws", type=int, default=MODEL_DRAWS)
    stat.add_argument("--draw-seed", type=int, default=MODEL_DRAW_SEED)
    parser.add_argument("--title", default="Do our driver settings fit this corridor?")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the check; returns the process exit code."""
    args = build_parser().parse_args(argv)
    if args.corridor_dir is None and args.detectors is None and args.lanes_from_cache is None:
        print("give --corridor-dir, --detectors or --lanes-from-cache", file=sys.stderr)
        return 2
    if (args.scenario is None) == (args.population is None):
        print("give exactly one of --scenario or --population", file=sys.stderr)
        return 2
    inputs = inputs_from_args(args)
    population = (
        population_from_scenario(args.scenario)
        if args.scenario is not None
        else population_from_artifact(args.population, model=args.cf_model)
    )
    dates = split_list(args.dates) or None
    classification = None
    classification_record: dict[str, Any] = {"given": False}
    only = split_list(args.only_stations) or None
    if args.classification is not None:
        columns = json.loads(args.classification_columns) if args.classification_columns else None
        if columns is not None and not isinstance(columns, dict):
            raise ValueError("--classification-columns must be a JSON object")
        classification = heavy_share_from_classification(
            pd.read_csv(args.classification),
            column_map=columns,
            dates=dates,
            start_local=args.start,
            end_local=args.end,
            stations=only,
            definition=args.heavy_definition,
            n_bootstrap=args.n_bootstrap,
            seed=args.bootstrap_seed,
        )
        classification_record = {
            "given": True,
            "path": str(args.classification),
            "sha256": file_sha256(args.classification),
            "columns": columns or {},
            "definition": args.heavy_definition,
        }
    observed = observe(
        inputs.frame,
        stations=inputs.stations,
        quality=None if args.no_quality else "assess",
        exempt_lanes=split_list(args.exempt_lanes),
        dates=dates,
        start_local=args.start,
        end_local=args.end,
        only_stations=only,
        speed_limit_ms=speed_limit_ms(args.speed_limit, args.speed_limit_unit),
        classification=classification,
        n_bootstrap=args.n_bootstrap,
        seed=args.bootstrap_seed,
    )
    explicit = [str(p) for p in args.capacity_sidecar]
    sidecars: list[str] | None
    if explicit:
        sidecars = explicit
    elif args.no_sidecar_discovery:
        sidecars = []
    else:
        sidecars = None
    provenance: dict[str, Any] = {
        "script": "scripts/transfer_check.py",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code": git_head(),
        "code_dirty": git_dirty(),
        "argv": list(sys.argv[1:] if argv is None else argv),
        "inputs": inputs.provenance,
        "population": dict(population.sources),
        "classification": classification_record,
    }
    report = check_transfer(
        observed,
        population,
        sidecars=sidecars,
        explicit_sidecars=bool(explicit),
        n_draws=args.draws,
        seed=args.draw_seed,
        provenance=provenance,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / JSON_NAME).write_text(report.to_json() + "\n")
    shown = {
        "Detector data": inputs.provenance.get("detectors")
        or inputs.provenance.get("cache_dir")
        or "",
        "Data hash (sha256 of the loaded rows)": inputs.provenance["frame_sha256"][:16],
        "Driver population": population.label,
        "Code": provenance["code"][:12]
        + (" (uncommitted changes)" if provenance["code_dirty"] else ""),
        "Created": provenance["created_at"],
    }
    (args.out / MARKDOWN_NAME).write_text(
        render_markdown(report, title=args.title, provenance=shown)
    )
    for c in report.comparisons:
        print(f"{c.quantity}: {c.verdict} — {c.explanation}")
    for r in report.recommendations:
        print(f"{r.quantity}: {r.action}")
    for c in report.comparisons:
        u = c.uncertainty_range
        if u is not None:
            print(
                f"uncertainty range {u.knob}: {u.low:.4g}–{u.high:.4g} ({u.basis}"
                f"{', clipped' if u.clipped else ''}"
                f"{', widened to the configured value' if u.widened_to_configured else ''}"
                f"{', assumed' if u.assumed else ''})"
            )
    print(f"wrote {args.out / JSON_NAME} and {args.out / MARKDOWN_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
