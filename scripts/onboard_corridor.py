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
``label``, ``lat``, ``lon``, ``lanes``, ``kind`` (only ``station``/``id``,
``lat`` and ``lon`` are read; every column is preserved on write). It is
written back with ``x_m`` (position along the corridor) and ``offset_m``
(perpendicular distance from the centreline) filled in; a station farther
than ``--max-station-offset-m`` from the corridor is left with an empty
``x_m`` and reported as rejected — it sits on the opposite carriageway, a
frontage road or another route, and comparing its counts against this
corridor would be wrong.

**What this does NOT do:** calibrate. The demand is a flat placeholder from
``--inflow-veh-h``, every discovered ramp carries zero flow, and the fleet is
the ``corridor_10km`` default population. FD, IDM and demand calibration
(CLAUDE.md §6) come after onboarding; nothing produced by the scenario this
writes is a claim about the real road until they are done.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from flowstate_core.units import veh_h_to_veh_s
from microsim.scenarios import MAX_STATION_OFFSET_M, CorridorBuild, corridor_from_bbox

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Columns the CLI adds to the stations CSV it writes back.
STATION_OUT_COLUMNS: tuple[str, ...] = ("x_m", "offset_m", "edge_id", "lane_pos_m")


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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build the corridor, write the scenario and the stations CSV, report."""
    args = parse_args(argv)
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
    )
    build.to_yaml(args.out)
    print(build.summary())
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
