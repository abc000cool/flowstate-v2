"""Calibrate an onboarded corridor's demand from its detector observations.

A thin CLI over :func:`calibration.onboarding.calibrate_scenario`, which holds
the method and its documentation: a scenario written by
``scripts/onboard_corridor.py`` plus a ``flowstate.observations/1`` artifact
and the station table with chain positions go in; the filled scenario, the
rewritten observations and the ``flowstate.demand/1`` artifact come out.

The same function backs ``POST /api/v1/corridors`` (``api.onboarding_jobs``),
so the dashboard's guided onboarding and this command derive identical numbers.

Example::

    uv run --no-sync python scripts/corridor_demand.py \\
        --scenario scenarios/mndot_i94_wb_stpaul.yaml \\
        --observations data/mndot/mndot_i94_wb_stpaul/observations.json \\
        --stations-x data/mndot/mndot_i94_wb_stpaul/stations_x.csv \\
        --upstream S1063 --downstream S97 \\
        --idm-calibration artifacts/idm_i24_capacity.json \\
        --demand-out artifacts/demand_mndot_i94_wb_stpaul.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from calibration.observations import Observations
from calibration.onboarding import (
    DEFAULT_ALIVE_VEH_H,
    DEFAULT_MATCH_RADIUS_M,
    calibrate_scenario,
)


def read_stations_x(path: Path) -> dict[str, float]:
    """Station id → position along the corridor chain [m].

    Args:
        path: The stations CSV ``scripts/onboard_corridor.py`` wrote back,
            with ``x_m`` filled in. Rows with an empty ``x_m`` are stations
            the projection rejected and are skipped.

    Returns:
        The chain positions, keyed by station id.
    """
    rows: dict[str, float] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("x_m") in (None, ""):
                continue
            rows[row["station"]] = float(row["x_m"])
    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--scenario", required=True, type=Path)
    ap.add_argument("--observations", required=True, type=Path)
    ap.add_argument("--stations-x", required=True, type=Path, help="station table with chain x_m")
    ap.add_argument(
        "--net",
        type=Path,
        help="SUMO net of the onboarding workdir (default: derived from --workdir)",
    )
    ap.add_argument(
        "--workdir", type=Path, default=None, help="onboarding workdir (net/osm.net.xml)"
    )
    ap.add_argument("--upstream", required=True, help="mainline station supplying the entry inflow")
    ap.add_argument(
        "--downstream", required=True, help="mainline station supplying the exit speed boundary"
    )
    ap.add_argument(
        "--idm-calibration", default=None, help="IDMCalibration artifact for the driver population"
    )
    ap.add_argument("--warmup-s", type=float, default=1800.0)
    ap.add_argument("--match-radius-m", type=float, default=DEFAULT_MATCH_RADIUS_M)
    ap.add_argument(
        "--alive-veh-h",
        type=float,
        default=DEFAULT_ALIVE_VEH_H,
        help="a ramp detector below this mean flow is dead",
    )
    ap.add_argument(
        "--out", type=Path, default=None, help="scenario YAML to write (default: in place)"
    )
    ap.add_argument("--demand-out", required=True, type=Path)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Calibrate the scenario, write the three files, report what was derived."""
    args = parse_args(argv)

    raw: dict[str, Any] = yaml.safe_load(args.scenario.read_text())
    net_path = (
        args.net or (args.workdir or Path("runs/onboard") / raw["name"]) / "net" / "osm.net.xml"
    )
    if not net_path.exists():
        print(f"net not found: {net_path}", file=sys.stderr)
        return 2

    result = calibrate_scenario(
        raw,
        observations=Observations.from_json(args.observations),
        stations_x=read_stations_x(args.stations_x),
        net_path=net_path,
        upstream=args.upstream,
        downstream=args.downstream,
        idm_calibration=args.idm_calibration,
        warmup_s=args.warmup_s,
        match_radius_m=args.match_radius_m,
        alive_veh_h=args.alive_veh_h,
    )
    for station_id in result.stations_without_chain_x:
        print(
            f"station {station_id} has no chain position in {args.stations_x}; kept inventory x",
            file=sys.stderr,
        )

    out = args.out or args.scenario
    out.write_text(yaml.safe_dump(result.scenario, sort_keys=False))
    result.observations.to_json(args.observations)
    result.demand["scenario"] = str(out)
    result.demand["observations"] = str(args.observations)
    args.demand_out.parent.mkdir(parents=True, exist_ok=True)
    args.demand_out.write_text(json.dumps(result.demand, indent=1))

    print(f"scenario {out}  {result.summary[0]}")
    for line in result.summary[1:]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
