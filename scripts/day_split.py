"""Calibration / validation day split for a corridor study (docs/FRISCO_PROTOCOL.md §3).

Screens every date of a corridor's detector data (Tuesday–Thursday, federal
holidays, the caller's holiday / incident / weather list, at least 80 % of the
selected stations usable in the study period), stratifies the candidates by
study-period mainline volume into terciles and draws 60 % of each tercile to
calibration with the protocol's fixed seed (:mod:`calibration.day_split`).
Writes the split, with every step, as ``flowstate.day_split/1`` JSON. The seed
is not an option: the protocol fixes it, and a split is drawn once.

Inputs are found as the onboarding path finds them
(:mod:`calibration.detector_inputs`): a corridor directory, a generic detector
CSV, or per-lane MnDOT data. ``--quality`` takes the ``data_quality.json`` of
``scripts/data_quality_report.py`` run over the same study period; without
it a station-day is usable when at least half of the period's windows carry a
flow.

Run (the Minnesota rehearsal corridor, its 05:30–09:30 span):

    uv run --no-sync python scripts/day_split.py \\
        --corridor-dir data/mndot/mndot_i94_wb_stpaul --start 05:30 --end 09:30 \\
        --quality runs/rehearsal/dq/data_quality.json \\
        --out runs/rehearsal/day_split.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from calibration.day_split import build_day_split, read_exclusions
from calibration.detector_inputs import add_input_arguments, inputs_from_args, split_list


def build_parser() -> argparse.ArgumentParser:
    """The command line (module docstring)."""
    parser = argparse.ArgumentParser(
        prog="python scripts/day_split.py",
        description="Draw the protocol's calibration / validation day split.",
    )
    add_input_arguments(parser)
    parser.add_argument(
        "--selected-stations",
        default="",
        help="comma-separated mainline stations of the study (default: every mainline "
        "station of the data; a corridor's selection is the protocol's section 2.2 list)",
    )
    parser.add_argument(
        "--quality", type=Path, help="data_quality.json judged over the study period"
    )
    parser.add_argument(
        "--exclude-day",
        action="append",
        default=[],
        metavar="DATE=REASON",
        help="a holiday, incident or weather day with its reason (repeatable)",
    )
    parser.add_argument(
        "--exclusions",
        action="append",
        default=[],
        type=Path,
        help="JSON object (date -> reason) or CSV (date, reason) of excluded days (repeatable)",
    )
    parser.add_argument("--out", required=True, type=Path, help="split JSON path")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Draw the split; returns the process exit code."""
    args = build_parser().parse_args(argv)
    if args.corridor_dir is None and args.detectors is None and args.lanes_from_cache is None:
        print("give --corridor-dir, --detectors or --lanes-from-cache", file=sys.stderr)
        return 2
    if not args.start or not args.end:
        print("give the study period: --start HH:MM --end HH:MM", file=sys.stderr)
        return 2
    inputs = inputs_from_args(args)
    quality: dict[str, Any] | None = None
    if args.quality is not None:
        quality = json.loads(args.quality.read_text())
    exclusions = read_exclusions(args.exclusions, args.exclude_day)
    split = build_day_split(
        inputs.frame,
        start=args.start,
        end=args.end,
        stations=split_list(args.selected_stations) or None,
        dates=split_list(args.dates) or None,
        quality=quality,
        exclusions=exclusions,
        provenance={
            "script": "scripts/day_split.py",
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "argv": list(sys.argv[1:] if argv is None else argv),
            "inputs": inputs.provenance,
            "quality": None if args.quality is None else str(args.quality),
        },
    )
    split.to_json(args.out)
    for day in split.days:
        state = "candidate" if day.candidate else "not a candidate: " + "; ".join(day.reasons)
        volume = "—" if day.volume_veh is None else f"{day.volume_veh:,.0f} veh"
        print(
            f"  {day.date} {day.weekday:<9} usable {day.n_usable}/{day.n_selected} "
            f"volume {volume} — {state}"
        )
    for stratum in split.strata:
        print(
            f"  {stratum.label:<6} tercile: {len(stratum.dates)} day(s), calibration "
            f"{', '.join(stratum.calibration_dates) or 'none'}; validation "
            f"{', '.join(stratum.validation_dates) or 'none'}"
        )
    print(
        f"calibration {len(split.calibration_dates)} day(s), validation "
        f"{len(split.validation_dates)} day(s)"
        + (f" — UNDERPOWERED: {split.underpowered_reason}" if split.underpowered else "")
    )
    for note in split.notes:
        print(f"note: {note}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
