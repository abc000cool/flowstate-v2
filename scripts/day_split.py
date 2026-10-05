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
flow. A date the report does not cover is refused (``--allow-uncovered-dates``
leaves it out with that reason instead). ``--selection`` takes the selected
stations from the corridor's ``selection.json`` (``scoring_stations``, written
by ``scripts/station_selection.py``: protocol §2.2).

Run (the Minnesota rehearsal corridor, its 05:30–09:30 span):

    uv run --no-sync python scripts/day_split.py \\
        --corridor-dir data/mndot/mndot_i94_wb_stpaul --start 05:30 --end 09:30 \\
        --quality runs/rehearsal/dq/data_quality.json \\
        --selection runs/rehearsal/selection.json \\
        --out runs/rehearsal/day_split.json
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from calibration.data_quality import QualityVerdicts
from calibration.day_split import build_day_split, read_exclusions
from calibration.detector_inputs import (
    add_input_arguments,
    file_sha256,
    inputs_from_args,
    split_list,
)
from calibration.station_selection import read_selection


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
        "--selection",
        type=Path,
        help="selection.json whose scoring_stations block (scripts/station_selection.py) "
        "names the selected stations",
    )
    parser.add_argument(
        "--quality", type=Path, help="data_quality.json judged over the study period"
    )
    parser.add_argument(
        "--allow-uncovered-dates",
        action="store_true",
        help="leave out dates the data-quality report does not cover (default: refuse)",
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
    if args.selection is not None and args.selected_stations:
        print("give --selection or --selected-stations, not both", file=sys.stderr)
        return 2
    stations = split_list(args.selected_stations) or None
    selection_record: dict[str, Any] | None = None
    if args.selection is not None:
        try:
            selection = read_selection(args.selection)
        except (OSError, ValueError) as exc:
            print(f"day_split: {args.selection}: {exc}", file=sys.stderr)
            return 2
        if not selection.stations:
            print(f"day_split: {args.selection} selects no station", file=sys.stderr)
            return 2
        stations = list(selection.stations)
        selection_record = {
            "path": str(args.selection),
            "sha256": file_sha256(args.selection),
            "stations": list(selection.stations),
            "quality_sha256": selection.quality.get("sha256"),
        }
    inputs = inputs_from_args(args)
    quality: QualityVerdicts | None = None
    if args.quality is not None:
        quality = QualityVerdicts.from_json(args.quality)
    if selection_record is not None and quality is not None:
        same = selection_record["quality_sha256"] == quality.sha256
        selection_record["same_quality_report"] = same
        if not same:
            print(
                "warning: the station selection was made from another data-quality report "
                f"(sha256 {str(selection_record['quality_sha256'])[:12]}, not "
                f"{str(quality.sha256)[:12]})",
                file=sys.stderr,
            )
    exclusions = read_exclusions(args.exclusions, args.exclude_day)
    try:
        split = build_day_split(
            inputs.frame,
            start=args.start,
            end=args.end,
            stations=stations,
            dates=split_list(args.dates) or None,
            quality=quality,
            exclusions=exclusions,
            allow_uncovered_dates=bool(args.allow_uncovered_dates),
            provenance={
                "script": "scripts/day_split.py",
                "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "argv": list(sys.argv[1:] if argv is None else argv),
                "inputs": inputs.provenance,
                "quality": None if args.quality is None else str(args.quality),
                "selection": selection_record,
            },
        )
    except ValueError as exc:
        print(f"day_split: {exc}", file=sys.stderr)
        return 2
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
