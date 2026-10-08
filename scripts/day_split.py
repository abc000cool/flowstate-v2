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

**The C9 amendment** (docs/PRE_FRISCO_PROGRAM.md C9: Amendment 9 to
docs/FRISCO_PROTOCOL.md as proposed there, approved by the coordinator on
2026-10-07 before any new day's file was read). Two opt-in options:

* ``--pin-calibration DATE=REASON`` (repeatable): a day already used to
  calibrate is pinned to calibration (:func:`pin_calibration`). It must pass
  the screen (§3.1) like any candidate, or the split is refused. The pinned
  days fill the first of the ``floor(0.6 × candidates)`` calibration places;
  the places left are drawn from the other candidates by §3.2's own procedure
  (their terciles, largest remainder, the protocol's seed). Without a pin the
  procedure is §3.2's draw exactly.
* ``--circles-events`` (I-24 only): the days the CIRCLES test fleet was on
  the road (:data:`CIRCLES_TEST_FLEET_DAYS`, the MegaVanderTest week of
  14–18 Nov 2022; CLAUDE.md §13) are events affecting the stretch (§3.1) and
  join the exclusion list with that reason.

With either, the JSON gains a last block, ``c9_amendment``: the rules, the
pinned and event days, the calibration total and how many places were drawn,
and the split **as written** (no pin, no event days) — change control reports
results under both rules. Without either the output is what it was.

Run (the Minnesota rehearsal corridor, its 05:30–09:30 span):

    uv run --no-sync python scripts/day_split.py \\
        --corridor-dir data/mndot/mndot_i94_wb_stpaul --start 05:30 --end 09:30 \\
        --quality runs/rehearsal/dq/data_quality.json \\
        --selection runs/rehearsal/selection.json \\
        --out runs/rehearsal/day_split.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from calibration.conservation import normalize_date
from calibration.data_quality import QualityVerdicts
from calibration.day_split import (
    CALIBRATION_SHARE,
    MIN_CALIBRATION_DAYS,
    MIN_VALIDATION_DAYS,
    N_STRATA,
    STRATUM_LABELS,
    DaySplit,
    Stratum,
    _allocate_calibration,
    build_day_split,
    read_exclusions,
)
from calibration.detector_inputs import (
    add_input_arguments,
    file_sha256,
    inputs_from_args,
    split_list,
)
from calibration.station_selection import read_selection

AMENDMENT = (
    "docs/FRISCO_PROTOCOL.md Amendment 9, as proposed in docs/PRE_FRISCO_PROGRAM.md C9 and "
    "approved by the coordinator on 2026-10-07, before any new day's file was read"
)

PIN_RULE = (
    "a day already used to calibrate is pinned to calibration; it must be a candidate (section "
    "3.1); the calibration total stays floor(0.6 x all candidates), the pinned days fill its "
    "first places, and the places left are drawn from the other candidates by section 3.2's "
    "procedure (their volume terciles, largest-remainder allocation of the places left, at least "
    "one per tercile when they allow, the seeded draw); with no pin this is section 3.2's draw "
    "exactly"
)

EVENTS_RULE = (
    "a day the CIRCLES test fleet was on the road (CLAUDE.md section 13) is an event affecting "
    "the stretch (section 3.1) and is excluded"
)

#: The days the CIRCLES test fleet drove I-24 (the MegaVanderTest week, Monday 14 to Friday
#: 18 November 2022; CLAUDE.md §13, BAIR blog 2025-03-25): events on the stretch (§3.1).
CIRCLES_TEST_FLEET_DAYS: dict[str, str] = {
    day: (
        "event affecting the stretch: the CIRCLES MegaVanderTest test fleet on I-24 "
        "(14-18 Nov 2022; CLAUDE.md section 13; " + AMENDMENT.split(",")[0] + ")"
    )
    for day in ("2022-11-14", "2022-11-15", "2022-11-16", "2022-11-17", "2022-11-18")
}


def pin_calibration(split: DaySplit, pinned: Mapping[str, str]) -> tuple[DaySplit, dict[str, Any]]:
    """The split with the pinned days in calibration (:data:`PIN_RULE`).

    The screening, volumes and seed are the split's. The candidates not
    pinned are ranked by ``(volume, date)`` and cut into terciles as §3.2 cuts
    all candidates; ``total − pinned`` places (``total = floor(0.6 × all
    candidates)``, never below zero) are allocated over those terciles by
    ``calibration.day_split._allocate_calibration`` with the share
    ``places / unpinned`` (exactly ``places``), and drawn with one
    ``numpy.random.default_rng(seed)`` in §3.2's order: the tie permutation
    first, then each tercile low to high. With no pin the strata, the draw and
    both day sets are :func:`calibration.day_split.build_day_split`'s.

    Args:
        split: The protocol's split (``build_day_split``).
        pinned: Date → why the day is pinned (the calibrations that used it).

    Returns:
        ``(amended split, record)``: the record holds the total, the places
        drawn and the pinned days, for the JSON's ``c9_amendment`` block.

    Raises:
        ValueError: A pinned date was not examined, or is not a candidate.
    """
    pins = {normalize_date(k): str(v) for k, v in pinned.items()}
    by_date = {r.date: r for r in split.days}
    for day in sorted(pins):
        rec = by_date.get(day)
        if rec is None:
            raise ValueError(f"pinned date {day} is not among the dates examined")
        if not rec.candidate:
            raise ValueError(
                f"pinned date {day} is not a candidate ({'; '.join(rec.reasons)}): a day already "
                "used to calibrate enters the split only through the screen of section 3.1"
            )
    candidates = [r for r in split.days if r.candidate]
    share = Fraction(CALIBRATION_SHARE).limit_denominator(1000)
    total = len(candidates) * share.numerator // share.denominator
    places = max(total - len(pins), 0)
    free = [r for r in candidates if r.date not in pins]
    ranked = sorted(free, key=lambda r: (float(r.volume_veh or 0.0), r.date))
    pieces = np.array_split(np.arange(len(ranked)), N_STRATA)
    rng = np.random.default_rng(split.seed)
    allocation = _allocate_calibration(
        [len(piece) for piece in pieces],
        Fraction(places, len(free)) if free else Fraction(0),
        rng.permutation(N_STRATA),
    )
    strata: list[Stratum] = []
    drawn: list[str] = []
    for label, piece, n_cal in zip(STRATUM_LABELS, pieces, allocation, strict=True):
        members = [ranked[int(i)] for i in piece]
        in_order = sorted(m.date for m in members)
        positions: tuple[int, ...] = ()
        if n_cal > 0:
            picked = rng.choice(len(in_order), size=n_cal, replace=False)
            positions = tuple(sorted(int(p) for p in picked))
        cal = tuple(in_order[p] for p in positions)
        volumes = [float(m.volume_veh) for m in members if m.volume_veh is not None]
        strata.append(
            Stratum(
                label=label,
                dates=tuple(in_order),
                volume_min_veh=min(volumes) if volumes else None,
                volume_max_veh=max(volumes) if volumes else None,
                n_calibration=n_cal,
                drawn_positions=positions,
                calibration_dates=cal,
                validation_dates=tuple(d for d in in_order if d not in cal),
            )
        )
        drawn.extend(cal)
    calibration = tuple(sorted([*pins, *drawn]))
    validation = tuple(sorted(r.date for r in candidates if r.date not in calibration))
    shortfalls: list[str] = []
    if len(calibration) < MIN_CALIBRATION_DAYS:
        shortfalls.append(
            f"{len(calibration)} calibration day(s), fewer than {MIN_CALIBRATION_DAYS}"
        )
    if len(validation) < MIN_VALIDATION_DAYS:
        shortfalls.append(f"{len(validation)} validation day(s), fewer than {MIN_VALIDATION_DAYS}")
    reason = (
        "; ".join(shortfalls)
        + " (docs/FRISCO_PROTOCOL.md section 3.3: the study proceeds and states that its "
        "validation is underpowered)"
        if shortfalls
        else ""
    )
    notes = list(split.notes)
    if pins:
        notes.append(
            f"{len(pins)} day(s) pinned to calibration ({', '.join(sorted(pins))}): they fill "
            f"{min(len(pins), total)} of the {total} calibration place(s) (floor(0.6 x "
            f"{len(candidates)} candidates)); {places} place(s) drawn from the other "
            f"{len(free)} candidate(s)"
            + (
                f"; the pinned days exceed the total by {len(pins) - total}"
                if len(pins) > total
                else ""
            )
        )
    amended = dataclasses.replace(
        split,
        strata=tuple(strata),
        calibration_dates=calibration,
        validation_dates=validation,
        underpowered=bool(shortfalls),
        underpowered_reason=reason,
        notes=tuple(notes),
        rules={**split.rules, "c9_pin": PIN_RULE},
    )
    record = {
        "pinned": dict(sorted(pins.items())),
        "n_candidates": len(candidates),
        "calibration_total": total,
        "n_pinned": len(pins),
        "n_drawn": places,
    }
    return amended, record


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
    parser.add_argument(
        "--pin-calibration",
        action="append",
        default=[],
        metavar="DATE=REASON",
        help="a day already used to calibrate, pinned to calibration (repeatable; the C9 "
        "amendment, module docstring)",
    )
    parser.add_argument(
        "--circles-events",
        action="store_true",
        help="I-24 only: exclude the CIRCLES test-fleet days (14-18 Nov 2022) as events "
        "affecting the stretch (the C9 amendment)",
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
        pins = read_exclusions((), args.pin_calibration)
    except ValueError as exc:
        print(f"day_split: --pin-calibration: {exc}", file=sys.stderr)
        return 2
    amended = bool(pins) or bool(args.circles_events)
    events = dict(CIRCLES_TEST_FLEET_DAYS) if args.circles_events else {}
    screened = dict(exclusions)
    for day, why in events.items():
        screened[day] = why if day not in screened else f"{screened[day]}; {why}"
    provenance = {
        "script": "scripts/day_split.py",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "argv": list(sys.argv[1:] if argv is None else argv),
        "inputs": inputs.provenance,
        "quality": None if args.quality is None else str(args.quality),
        "selection": selection_record,
    }

    def draw(day_exclusions: dict[str, str]) -> DaySplit:
        return build_day_split(
            inputs.frame,
            start=args.start,
            end=args.end,
            stations=stations,
            dates=split_list(args.dates) or None,
            quality=quality,
            exclusions=day_exclusions,
            allow_uncovered_dates=bool(args.allow_uncovered_dates),
            provenance=provenance,
        )

    try:
        split = draw(screened)
        as_written = draw(exclusions) if amended else split
        pin_record: dict[str, Any] = {}
        if pins:
            split, pin_record = pin_calibration(split, pins)
    except ValueError as exc:
        print(f"day_split: {exc}", file=sys.stderr)
        return 2
    if not amended:
        split.to_json(args.out)
    else:
        if events and not exclusions:
            split = dataclasses.replace(
                split,
                notes=(
                    *split.notes,
                    "the caller supplied no exclusion list of its own: state and local holidays, "
                    "incidents and weather events were not screened here (federal holidays and the "
                    "CIRCLES test-fleet days were)",
                ),
            )
        payload = split.to_dict()
        payload["c9_amendment"] = {
            "amendment": AMENDMENT,
            "rules": {"pin": PIN_RULE, "events": EVENTS_RULE},
            "pinned": pin_record.get("pinned", {}),
            "events": dict(sorted(events.items())),
            "n_candidates": len(split.candidate_dates),
            "calibration_total": pin_record.get("calibration_total", len(split.calibration_dates)),
            "n_pinned": pin_record.get("n_pinned", 0),
            "n_drawn": pin_record.get("n_drawn", len(split.calibration_dates)),
            "as_written": {
                "rule": "docs/FRISCO_PROTOCOL.md section 3 without the amendment (no pin, no event "
                "days); change control reports results under both",
                "candidate_dates": list(as_written.candidate_dates),
                "calibration_dates": list(as_written.calibration_dates),
                "validation_dates": list(as_written.validation_dates),
                "strata": [s.to_dict() for s in as_written.strata],
                "underpowered": as_written.underpowered,
                "underpowered_reason": as_written.underpowered_reason,
            },
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
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
    if amended:
        for day, why in sorted(pins.items()):
            print(f"  pinned to calibration: {day} ({why})")
        print(
            f"as written (no pin, no event days): calibration "
            f"{', '.join(as_written.calibration_dates) or 'none'}; validation "
            f"{', '.join(as_written.validation_dates) or 'none'}"
        )
    for note in split.notes:
        print(f"note: {note}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
