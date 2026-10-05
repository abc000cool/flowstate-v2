"""The mainline stations a corridor study scores at (docs/FRISCO_PROTOCOL.md §2.2).

Reads the corridor's ``data_quality.json`` (``scripts/data_quality_report.py``
judged over the study period) and selects every mainline station whose
verdict is not "exclude" on at least 80 % of the candidate days — Tuesday to
Thursday, not a federal holiday, not on the agency's exclusion list — with
:mod:`calibration.station_selection`. Writes the selection into the corridor's
``selection.json`` under ``scoring_stations`` (every existing key kept), with
each station's share, its excluded days and their checks, and the report's
path and hash. The list is committed before the first simulation of the
corridor; replacing an existing one needs ``--replace`` (a dated amendment,
protocol §2.2). ``scripts/day_split.py --selection`` then takes the selected
stations from it.

Run (the Minnesota rehearsal corridor):

    uv run --no-sync python scripts/station_selection.py \\
        --quality runs/rehearsal/dq/data_quality.json \\
        --base data/mndot/mndot_i94_wb_stpaul/selection.json \\
        --out runs/rehearsal/selection.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from calibration.data_quality import QualityVerdicts
from calibration.day_split import read_exclusions
from calibration.detector_inputs import file_sha256, split_list
from calibration.station_selection import select_stations, selection_document


def build_parser() -> argparse.ArgumentParser:
    """The command line (module docstring)."""
    parser = argparse.ArgumentParser(
        prog="python scripts/station_selection.py",
        description="Select the scoring stations from a data-quality report.",
    )
    parser.add_argument(
        "--quality", required=True, type=Path, help="data_quality.json judged over the study period"
    )
    parser.add_argument(
        "--base",
        type=Path,
        help="the corridor's selection.json to extend (default: --out when it exists)",
    )
    parser.add_argument(
        "--stations",
        default="",
        help="comma-separated stations of the stretch (default: every mainline station judged)",
    )
    parser.add_argument("--dates", default="", help="the study's dates (default: the report's)")
    parser.add_argument(
        "--exclude-day",
        action="append",
        default=[],
        metavar="DATE=REASON",
        help="an incident, weather or holiday day with its reason (repeatable)",
    )
    parser.add_argument(
        "--exclusions",
        action="append",
        default=[],
        type=Path,
        help="JSON object (date -> reason) or CSV (date, reason) of excluded days (repeatable)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="overwrite an existing scoring_stations block (a dated amendment, protocol 2.2)",
    )
    parser.add_argument("--out", required=True, type=Path, help="selection.json to write")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Select and write; returns the process exit code."""
    args = build_parser().parse_args(argv)
    base_path = args.base if args.base is not None else (args.out if args.out.is_file() else None)
    base: dict[str, Any] = {}
    if base_path is not None:
        base = json.loads(base_path.read_text())
    verdicts = QualityVerdicts.from_json(args.quality)
    try:
        selection = select_stations(
            verdicts,
            exclusions=read_exclusions(args.exclusions, args.exclude_day),
            dates=split_list(args.dates) or None,
            stations=split_list(args.stations) or None,
            provenance={
                "script": "scripts/station_selection.py",
                "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "argv": list(sys.argv[1:] if argv is None else argv),
                "base": None if base_path is None else str(base_path),
                "base_sha256": None if base_path is None else file_sha256(base_path),
            },
        )
        payload = selection_document(selection, base, replace=bool(args.replace))
    except ValueError as exc:
        print(f"station_selection: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, allow_nan=False) + "\n")
    for record in selection.records:
        print(f"  {record.station:<10} {record.reason}")
    print(
        f"{len(selection.stations)} of {len(selection.records)} mainline station(s) scored over "
        f"{len(selection.candidate_dates)} candidate day(s) "
        f"({selection.study_period[0]}-{selection.study_period[1]})"
    )
    for note in selection.notes:
        print(f"note: {note}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
