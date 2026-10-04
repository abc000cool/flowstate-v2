"""An observations artifact for a subset of dates, on a reference artifact's grid.

The baseline gate (docs/FRISCO_PROTOCOL.md §3.5, §6) scores the no-strategy
model against the mean of the calibration days and, unchanged, against the
mean of the validation days. Both artifacts must sit on one station table and
window grid, so this script rebuilds the ``flowstate.observations/1`` artifact
from the corridor's tidy detector frame (``detectors.csv``, every window of
every fetched date) for the chosen dates only
(:meth:`calibration.observations.Observations.from_frame` with ``dates=``),
copying from a reference artifact (``--like``, normally the corridor's
committed ``observations.json``) everything that is not a measurement: the
station table with its positions, ``t0_local``, the window, the span, the
corridor name and the ``source`` block. ``source.dates`` becomes the subset,
``source.scaled_station_days`` keeps the subset's entries, and
``source.subset`` records what the artifact was cut from.

The corridor's observed wave speed (``context.detector_wave_speed``) is
estimated from the raw 30-second series, not from this frame, so it is not
recomputed here. ``--context-from`` copies the ``context`` of an artifact
built for the same dates (e.g. ``scripts/mndot_fetch.py --wave-context
--dates <the subset>``); the script refuses one whose ``source.dates`` differ.
Without it the artifact carries no context, and the gate's C4 applicability
is then reported as undetermined.

Run:

    uv run --no-sync python scripts/observations_for_dates.py \\
        --corridor-dir data/mndot/mndot_i94_wb_stpaul \\
        --like data/mndot/mndot_i94_wb_stpaul/observations.json \\
        --split runs/rehearsal/day_split.json --set calibration \\
        --out runs/rehearsal/observations_calibration.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from pathlib import Path
from typing import Any

from calibration.conservation import normalize_date
from calibration.day_split import DaySplit
from calibration.detector_inputs import file_sha256, load_corridor_inputs, split_list
from calibration.observations import Observations, ObservedStation


def _compact(dates: list[str], like: list[Any]) -> list[str]:
    """Dates in the reference's spelling (``YYYYMMDD`` when it uses that)."""
    iso = [normalize_date(d) for d in dates]
    if like and all(len(str(d)) == 8 and str(d).isdigit() for d in like):
        return [d.replace("-", "") for d in iso]
    return iso


def build_parser() -> argparse.ArgumentParser:
    """The command line (module docstring)."""
    parser = argparse.ArgumentParser(
        prog="python scripts/observations_for_dates.py",
        description="Rebuild an observations artifact for a subset of dates.",
    )
    parser.add_argument("--corridor-dir", type=Path, help="directory holding detectors.csv")
    parser.add_argument("--detectors", type=Path, help="tidy detector CSV (overrides the dir's)")
    parser.add_argument(
        "--like", required=True, type=Path, help="reference observations artifact (grid, stations)"
    )
    parser.add_argument("--dates", default="", help="comma-separated dates of the subset")
    parser.add_argument("--split", type=Path, help="flowstate.day_split/1 JSON")
    parser.add_argument(
        "--set", choices=["calibration", "validation"], help="which day set of --split"
    )
    parser.add_argument(
        "--context-from",
        type=Path,
        help="artifact for the same dates whose context block (wave speed) is copied",
    )
    parser.add_argument("--out", required=True, type=Path, help="output artifact path")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build the artifact; returns the process exit code."""
    args = build_parser().parse_args(argv)
    if args.split is not None:
        if args.set is None:
            print("--split needs --set calibration|validation", file=sys.stderr)
            return 2
        split = DaySplit.from_json(args.split)
        dates = list(split.dates_of(args.set))
    else:
        dates = split_list(args.dates)
    if not dates:
        print("no dates: give --dates or --split with --set", file=sys.stderr)
        return 2
    if args.corridor_dir is None and args.detectors is None:
        print("give --corridor-dir or --detectors", file=sys.stderr)
        return 2
    like_raw = json.loads(args.like.read_text())
    like = Observations.from_dict(like_raw)
    inputs = load_corridor_inputs(corridor_dir=args.corridor_dir, detectors=args.detectors)
    stations = [ObservedStation.from_mapping(s.to_dict()) for s in like.stations]
    source: dict[str, Any] = dict(like.source)
    ref_dates = list(source.get("dates") or [])
    subset = _compact(dates, ref_dates)
    source["dates"] = subset
    wanted = set(subset)
    scaled = source.get("scaled_station_days")
    if isinstance(scaled, list):
        source["scaled_station_days"] = [
            e for e in scaled if isinstance(e, dict) and str(e.get("date")) in wanted
        ]
    source["subset"] = {
        "of": str(args.like),
        "of_sha256": file_sha256(args.like),
        "dates_of_reference": ref_dates,
        "set": args.set or "",
        "split": None if args.split is None else str(args.split),
        "detectors": inputs.provenance.get("detectors"),
        "detectors_sha256": inputs.provenance.get("detectors_sha256"),
        "script": "scripts/observations_for_dates.py",
    }
    built = Observations.from_frame(
        inputs.frame,
        stations,
        window_s=like.window_s,
        t0_local=like.t0_local,
        duration_s=like.duration_s,
        corridor=like.corridor,
        source=source,
        aggregation=like.aggregation,
        dates=dates,
    )
    if args.context_from is not None:
        other = json.loads(args.context_from.read_text())
        other_dates = _compact(list((other.get("source") or {}).get("dates") or []), ref_dates)
        if sorted(other_dates) != sorted(subset):
            print(
                f"--context-from {args.context_from} was built for dates {sorted(other_dates)}, "
                f"not {sorted(subset)}; its context does not describe this subset",
                file=sys.stderr,
            )
            return 2
        context = dict(other.get("context") or {})
        built = dataclasses.replace(built, context=context)
        source["subset"]["context_from"] = str(args.context_from)
    built.to_json(args.out)
    n_obs = sum(1 for series in built.flows_veh_h.values() for v in series if math.isfinite(v))
    print(
        f"{built.corridor}: {len(subset)} date(s) {', '.join(subset)} -> {args.out} "
        f"({len(built.stations)} stations, {built.n_windows} windows, {n_obs} observed "
        f"station-window flows; context: {', '.join(built.context) or 'none'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
