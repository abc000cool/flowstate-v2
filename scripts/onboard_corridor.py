"""Onboard any freeway corridor from a bounding box (CLAUDE.md §3.2.4).

The ``osm_generic`` "any city" path as a one-command CLI: a WGS84 bounding
box and a direction of travel in, a runnable scenario YAML plus the corridor
geometry out — mainline chain, lane profile, interchange ramps, and the
linear-``x`` position of every detector station given as (lat, lon).

Usage (from the repository root)::

    uv run --no-sync python scripts/onboard_corridor.py \\
        --name mndot_i94_wb \\
        --bbox 44.94 -93.33 44.99 -93.18 \\
        --bearing 270 \\
        --stations data/mndot/mndot_i94_wb/stations.csv \\
        --workdir runs/onboard/mndot_i94_wb \\
        --out scenarios/mndot_i94_wb.yaml

The stations CSV carries the detector inventory — columns ``station``,
``label``, ``lat``, ``lon``, ``lanes``, ``kind`` (``station``/``id``, ``lat``
and ``lon`` place the station; ``lanes`` and ``kind`` feed the lane check
below; every column is preserved on write). It is
written back with ``x_m`` (position along the corridor) and ``offset_m``
(perpendicular distance from the centreline) filled in; a station farther
than ``--max-station-offset-m`` from the corridor is left with an empty
``x_m`` and reported as rejected — it sits on the opposite carriageway, a
frontage road or another route, and comparing its counts against this
corridor would be wrong.

**Lane pre-flight.** The report ends with a lanes-vs-inventory block: the
compiled lane count at every mainline station against that station's ``lanes``
column (:meth:`microsim.scenarios.CorridorBuild.lane_check`). It is the check
of docs/ONBOARDING_MNDOT.md §7 — a map that tags the mainline straight through
its merges starves the on-ramps, and finding that out costs one build here
instead of a 20-seed battery. ``--fail-on-lane-mismatch`` makes a disagreement
larger than ``--lane-tolerance`` (default 0: every disagreement) exit 3 so a
batch script stops there. One disagreement is exempt by default — a single
*extra* compiled lane at a guessed ramp, which is the acceleration lane
``netconvert`` was asked to build rather than a defect; ``--strict-lanes``
fails on that one too — and is refused (exit 2) together with
``--lane-tolerance 1`` or more, a tolerance that has already dropped every
one-lane disagreement before ``--strict-lanes`` could report it.

**Ramp guessing, on by default (2026-09-24).** OSM rarely draws acceleration
lanes, and an entrance joining lane 0 at a plain junction starves under
SUMO's yielding (docs/ONBOARDING_MNDOT.md §6), so the network is compiled
with ``--ramps.guess --ramps.ramp-length 250``
(:data:`microsim.scenarios.RAMP_GUESSING_OPTIONS`, the MnDOT values) unless
``--no-ramp-guessing`` is given. Options the caller passes in
``--netconvert-extra`` are kept verbatim and never duplicated: a
``--ramps.ramp-length`` of its own wins.

**Split audit and fixes, on by default.** The inventory also lists every
exit leaving the chain with the side OSM draws it on (the signed lateral
offset of the link's first nodes from the continuing mainline, and the
``turn:lanes`` tag when there is one) against the lanes the compiled network
feeds it from (:func:`microsim.split_audit.audit_splits`,
docs/ONBOARDING_MNDOT.md §9). A right-hand exit compiled from the leftmost
lane traps through traffic in a lane that leads only to the exit — the map
fault behind the I-94 WB lock — and the lane check cannot see it because the
lane *count* is right. A ``wrong_side`` / ``added_lane_wrong_side`` finding
is fixed before the YAML is written (:func:`microsim.scenarios.apply_split_fixes`):
a connection patch restating the ``wrong_side`` splits is written beside the
extract as ``<extract stem>.splits.con.xml`` (``--write-split-patch PATH``
chooses another place; added to the scenario's ``patch_files``),
``--ramps.unset`` is added for the lanes ramp guessing put on the wrong side
(``netconvert_extra``), the network is re-imported with the fixes and audited
again; both audits are printed, then the line ``applied ramp guessing on;
split fixes: 2 applied, 0 remaining``. ``--no-split-fixes`` reports the
defects and leaves the network as compiled. ``--fail-on-split-defect`` exits
4 on any defect the *final* audit still carries.

**What this does NOT do:** calibrate. The demand is a flat placeholder from
``--inflow-veh-h``, every discovered ramp carries zero flow, and the fleet is
the ``corridor_10km`` default population. FD, IDM and demand calibration
(CLAUDE.md §6) come after onboarding; nothing produced by the scenario this
writes is a claim about the real road until they are done.
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from flowstate_core.units import veh_h_to_veh_s
from microsim.scenarios import (
    MAX_STATION_OFFSET_M,
    CorridorBuild,
    LaneMismatch,
    corridor_from_bbox,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Exit status of ``--fail-on-split-defect`` when an exit is compiled on the
#: wrong side of the mainline in the final audit (after the split fixes,
#: unless ``--no-split-fixes``). Its own code so a batch script can tell it
#: from a lane disagreement (3).
SPLIT_DEFECT_EXIT: int = 4

#: Why ``--no-split-fixes`` and ``--write-split-patch`` cannot both be asked
#: for: the path is where the fixes write their patch, and there are none.
NO_FIXES_PATCH_MESSAGE: str = (
    "--write-split-patch has no effect with --no-split-fixes: the path is where the "
    "split fixes write their connection patch, and --no-split-fixes applies none. Drop "
    "one of the two."
)

#: Columns the CLI adds to the stations CSV it writes back.
STATION_OUT_COLUMNS: tuple[str, ...] = ("x_m", "offset_m", "edge_id", "lane_pos_m")

#: Exit status of ``--fail-on-lane-mismatch`` when the compiled lane profile
#: and the detector inventory disagree by more than the tolerance. Its own
#: code (not 1) so a batch script can tell a lane disagreement apart from a
#: build that failed outright.
LANE_MISMATCH_EXIT: int = 3

#: Exit status of a self-cancelling combination of flags (usage error).
BAD_USAGE_EXIT: int = 2

#: Why ``--strict-lanes`` and a tolerance of one lane or more cannot both be
#: asked for. ``--strict-lanes`` exists to report the *one* extra compiled lane
#: at a guessed ramp, and ``lane_check`` has already dropped every disagreement
#: of that size before ``failing_mismatches`` sees it, so the combination asks
#: for a stricter check and silently gets a looser one.
STRICT_TOLERANCE_MESSAGE: str = (
    "--strict-lanes has no effect with --lane-tolerance 1 or more: the tolerance drops "
    "the one-lane disagreements (the guessed acceleration lane among them) before "
    "--strict-lanes could report them. Use --strict-lanes with --lane-tolerance 0, or "
    "drop --strict-lanes."
)

#: The one hint (:func:`microsim.scenarios._lane_hint`) that describes a
#: disagreement the build itself asked for: ``--ramps.guess`` adds the
#: acceleration lane the inventory does not list.
ACCEL_LANE_HINT: str = "acceleration lane added by ramp guessing"


def failing_mismatches(
    mismatches: Sequence[LaneMismatch], *, strict: bool = False
) -> list[LaneMismatch]:
    """The mismatches ``--fail-on-lane-mismatch`` exits on.

    A tolerance cannot separate the benign disagreement from the dangerous
    one: both are one lane wide, and a tolerance of 1 that hides the
    acceleration lane also hides a mainline tagged straight through its
    merge — the defect the check exists for. The benign case is suppressed
    by its *hint class* instead: exactly one extra compiled lane at a
    guessed ramp (``delta == +1`` with :data:`ACCEL_LANE_HINT`).

    Args:
        mismatches: What :meth:`CorridorBuild.lane_check` reported.
        strict: ``--strict-lanes``: report the acceleration lane as well,
            for a corridor whose inventory is known to include it.

    Returns:
        The reportable subset, in the order given.
    """
    if strict:
        return list(mismatches)
    return [m for m in mismatches if not (m.delta == 1 and m.hint == ACCEL_LANE_HINT)]


def read_stations(path: Path) -> list[dict[str, str]]:
    """Read a stations CSV into row dicts (all columns preserved).

    Args:
        path: CSV with at least ``station`` (or ``id``), ``lat`` and ``lon``.

    Returns:
        One dict per row, in file order.

    Raises:
        ValueError: The file has no header or lacks the required columns.
    """
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: empty stations CSV (no header row)")
        missing = {"lat", "lon"} - set(reader.fieldnames)
        if missing or not ({"station", "id"} & set(reader.fieldnames)):
            raise ValueError(
                f"{path}: stations CSV needs 'station' (or 'id'), 'lat' and 'lon' columns; "
                f"found {reader.fieldnames}"
            )
        return [dict(row) for row in reader]


def write_stations(path: Path, rows: list[dict[str, str]], build: CorridorBuild) -> None:
    """Write the stations CSV back with the corridor positions filled in.

    Args:
        path: Destination CSV.
        rows: Rows as read by :func:`read_stations`.
        build: The corridor the stations were projected onto.
    """
    columns = list(rows[0]) if rows else ["station", "lat", "lon"]
    columns += [c for c in STATION_OUT_COLUMNS if c not in columns]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            station_id = str(row.get("station", row.get("id", ""))).strip()
            point = build.station_x.get(station_id) or build.stations_rejected.get(station_id)
            out = dict(row)
            accepted = station_id in build.station_x
            out["x_m"] = f"{point.x_m:.1f}" if point is not None and accepted else ""
            out["offset_m"] = f"{point.offset_m:.1f}" if point is not None else ""
            out["edge_id"] = point.edge_id if point is not None and accepted else ""
            out["lane_pos_m"] = f"{point.lane_pos:.1f}" if point is not None and accepted else ""
            writer.writerow(out)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--name", required=True, help="scenario name, e.g. mndot_i94_wb")
    parser.add_argument(
        "--bbox",
        required=True,
        nargs=4,
        type=float,
        metavar=("SOUTH", "WEST", "NORTH", "EAST"),
        help="WGS84 bounding box around the corridor",
    )
    parser.add_argument(
        "--bearing",
        required=True,
        type=float,
        help="direction of travel, compass degrees (270 = westbound, 90 = eastbound)",
    )
    parser.add_argument("--workdir", required=True, type=Path, help="build directory")
    parser.add_argument("--out", required=True, type=Path, help="scenario YAML to write")
    parser.add_argument("--stations", type=Path, help="detector inventory CSV")
    parser.add_argument(
        "--stations-out",
        type=Path,
        help="where the filled-in stations CSV goes (default: <out dir>/<name>_stations.csv)",
    )
    parser.add_argument(
        "--start-near",
        nargs=2,
        type=float,
        metavar=("LON", "LAT"),
        help="anchor point choosing which corridor to follow inside the bbox",
    )
    parser.add_argument(
        "--inflow-veh-h",
        type=float,
        default=6000.0,
        help="UNCALIBRATED placeholder mainline demand across all lanes [veh/h]",
    )
    parser.add_argument("--duration-s", type=float, default=1800.0, help="simulated duration [s]")
    parser.add_argument("--seed", type=int, default=42, help="scenario master seed")
    parser.add_argument("--replicates", type=int, help="seeded replicates (default: 20)")
    parser.add_argument(
        "--osm-file", type=Path, help="use this OSM extract instead of downloading the bbox"
    )
    parser.add_argument(
        "--download",
        choices=("overpass", "osm_api"),
        default="overpass",
        help="service the bbox is fetched from (default: overpass)",
    )
    parser.add_argument(
        "--max-heading-dev-deg",
        type=float,
        default=60.0,
        help="how far an edge's heading may differ from --bearing to stay on the chain",
    )
    parser.add_argument(
        "--max-station-offset-m",
        type=float,
        default=MAX_STATION_OFFSET_M,
        help="reject stations farther than this from the corridor centreline [m]",
    )
    parser.add_argument(
        "--no-ramps", action="store_true", help="mainline only: do not attach discovered ramps"
    )
    parser.add_argument(
        "--netconvert-extra",
        default="",
        help='extra netconvert options recorded in the scenario, e.g. "--ramps.no-split"; '
        "the ramp-guessing defaults (--ramps.guess --ramps.ramp-length 250) are added in "
        "front unless given here or turned off with --no-ramp-guessing",
    )
    parser.add_argument(
        "--no-ramp-guessing",
        action="store_true",
        help="compile without netconvert's ramp guessing (default: on, so entrances the map "
        "draws without an acceleration lane do not starve)",
    )
    parser.add_argument(
        "--no-split-fixes",
        action="store_true",
        help="report the split audit's defects but do not apply their fixes (default: a "
        "wrong_side / added_lane_wrong_side finding is fixed and the network re-audited)",
    )
    parser.add_argument(
        "--max-chain-m",
        type=float,
        default=None,
        help="drop chain edges starting beyond this length [m]",
    )
    parser.add_argument(
        "--lane-tolerance",
        type=int,
        default=0,
        help="lane difference between the compiled map and the inventory that "
        "--fail-on-lane-mismatch tolerates (default: 0, i.e. every disagreement "
        "is reported)",
    )
    parser.add_argument(
        "--fail-on-lane-mismatch",
        action="store_true",
        help="exit 3 when any mainline station's compiled lane count differs "
        "from the inventory by more than --lane-tolerance; one extra compiled "
        "lane at a guessed ramp is the acceleration lane and does not count",
    )
    parser.add_argument(
        "--strict-lanes",
        action="store_true",
        help="count the guessed acceleration lane as a mismatch too (with "
        "--fail-on-lane-mismatch); refused with --lane-tolerance 1 or more, which "
        "would drop those disagreements first",
    )
    parser.add_argument(
        "--fail-on-split-defect",
        action="store_true",
        help="exit 4 when the final split audit (after the fixes, unless --no-split-fixes) "
        "still has an exit compiled on the wrong side of the mainline "
        "(wrong_side / added_lane_wrong_side)",
    )
    parser.add_argument(
        "--write-split-patch",
        type=Path,
        metavar="PATH",
        help="where the split fixes write the connection patch restating every wrong_side "
        "split (default: beside the extract, <extract stem>.splits.con.xml); refused "
        "with --no-split-fixes",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build the corridor, write the scenario and the stations CSV, report."""
    args = parse_args(argv)
    # refused before the build, not after: a run that spends a minute on
    # Overpass and netconvert to then apply neither flag as asked is worse
    # than a usage error
    if args.strict_lanes and args.lane_tolerance >= 1:
        print(STRICT_TOLERANCE_MESSAGE)
        return BAD_USAGE_EXIT
    if args.no_split_fixes and args.write_split_patch is not None:
        print(NO_FIXES_PATCH_MESSAGE)
        return BAD_USAGE_EXIT
    rows = read_stations(args.stations) if args.stations else []
    stations: list[dict[str, Any]] = [dict(r) for r in rows]

    build = corridor_from_bbox(
        args.name,
        (args.bbox[0], args.bbox[1], args.bbox[2], args.bbox[3]),
        args.bearing,
        inflow=veh_h_to_veh_s(args.inflow_veh_h),
        workdir=args.workdir,
        start_near=tuple(args.start_near) if args.start_near else None,
        stations=stations,
        duration_s=args.duration_s,
        seed=args.seed,
        osm_file=args.osm_file,
        download=args.download,
        max_heading_dev_deg=args.max_heading_dev_deg,
        max_station_offset_m=args.max_station_offset_m,
        discover_ramps=not args.no_ramps,
        replicates=args.replicates,
        netconvert_extra=tuple(args.netconvert_extra.split()),
        max_chain_m=args.max_chain_m,
        ramp_guessing=not args.no_ramp_guessing,
        split_fixes=not args.no_split_fixes,
        split_patch_path=args.write_split_patch,
    )
    # The summary carries both audits and the "applied" line: what the
    # scenario will compile is what was fixed and re-imported, not assumed.
    print(build.summary(stations))
    build.to_yaml(args.out)
    print(f"  scenario  {args.out}")
    if rows:
        out_csv = args.stations_out or args.out.parent / f"{args.name}_stations.csv"
        write_stations(out_csv, rows, build)
        print(f"  stations  {out_csv}")
    print(
        "  NOTE: demand is an uncalibrated placeholder "
        f"({args.inflow_veh_h:g} veh/h) and every ramp carries 0 veh/h — "
        "calibrate (CLAUDE.md §6) before any claim about this corridor."
    )
    over = failing_mismatches(
        build.lane_check(stations, tolerance=args.lane_tolerance), strict=args.strict_lanes
    )
    if over and args.fail_on_lane_mismatch:
        print(
            f"  FAIL: {len(over)} mainline station(s) differ from the inventory by more "
            f"than {args.lane_tolerance} lane(s): "
            + ", ".join(
                f"{m.station} (map {m.compiled_lanes}, inventory {m.inventory_lanes})" for m in over
            )
        )
        return LANE_MISMATCH_EXIT
    defects = build.split_defects()
    if defects and args.fail_on_split_defect:
        print(
            f"  FAIL: {len(defects)} exit(s) compiled on the wrong side of the mainline: "
            + ", ".join(
                f"{d.from_edge} -> {d.exit_edge} ({d.verdict}, OSM {d.expected_side}, "
                f"compiled {d.compiled_side})"
                for d in defects
            )
        )
        return SPLIT_DEFECT_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
