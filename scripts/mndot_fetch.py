"""Fetch a MnDOT corridor's 30-second detector archive into an observations set.

The onboarding path for a Twin Cities corridor (CLAUDE.md §6.1/§6.3): read the
IRIS configuration, pick the stations between two ids, pull every mainline (and
ramp) detector-day from the "Mayfly" archive API, aggregate to analysis windows
and write the three files a corridor build needs:

* ``stations.csv``    — station, label, lat, lon, x_m, lanes, kind, speed limit,
  detector names (the stations table of the observations contract §2).
* ``detectors.csv``   — the tidy detector frame, every whole-day window of every
  fetched date (contract §1), so the artifact can be rebuilt with a different
  ``--t0`` / ``--duration-s`` without re-fetching.
* ``observations.json`` — the ``flowstate.observations/1`` artifact for the
  analysed span, plus a per-station coverage table printed to stdout.

Responses are cached as JSON per detector-day under ``--cache-dir``, so a
re-run costs no requests and an interrupted pull resumes. Memory stays flat:
one station-day of 30-second samples is aggregated and released before the next
is fetched.

Run (network; never from tests):

    uv run --no-sync python scripts/mndot_fetch.py \
        --corridor "I-94 WB" --from-station S2104 --to-station S1060 \
        --dates 20260915,20260916 --window-s 300 --t0 06:00 \
        --duration-s 10800 --out data/mndot/i94_wb
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from calibration.loaders.detector_csv import write_detector_csv
from calibration.loaders.mndot import (
    DEFAULT_CACHE_DIR,
    MAYFLY_BASE_URL,
    MAYFLY_DISTRICT,
    METRO_CONFIG_URL,
    MetroConfig,
    fetch_metro_config,
    station_frame,
    stations_table,
)
from calibration.observations import Observations, coverage

DEFAULT_CONFIG_PATH = "data/mndot/metro_config.xml.gz"
"""Where the IRIS configuration is kept (downloaded on first use)."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/mndot_fetch.py",
        description="Fetch a MnDOT corridor's detector archive into an observations artifact.",
    )
    parser.add_argument("--corridor", required=True, help="Corridor name, e.g. 'I-94 WB'.")
    parser.add_argument("--from-station", required=True, help="Upstream station id, e.g. S2104.")
    parser.add_argument("--to-station", required=True, help="Downstream station id.")
    parser.add_argument("--dates", required=True, help="Comma-separated YYYYMMDD local dates.")
    parser.add_argument("--window-s", type=float, default=300.0, help="Analysis window [s].")
    parser.add_argument("--t0", default="06:00", help="Local wall clock of simulation t=0.")
    parser.add_argument(
        "--duration-s", type=float, default=10800.0, help="Analysed span [s] from --t0."
    )
    parser.add_argument("--out", required=True, help="Output directory.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="IRIS configuration path.")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help="JSON response cache root.")
    parser.add_argument("--district", default=MAYFLY_DISTRICT, help="MnDOT district.")
    parser.add_argument("--max-workers", type=int, default=8, help="Concurrent archive requests.")
    parser.add_argument(
        "--no-ramps", action="store_true", help="Skip Entrance/Exit ramp detectors."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = _build_parser().parse_args(argv)
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    if not dates:
        print("--dates needs at least one YYYYMMDD date")
        return 2

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"downloading {METRO_CONFIG_URL} -> {config_path}")
        fetch_metro_config(config_path)
    config = MetroConfig.load(config_path)
    corridor = config.corridor(args.corridor)
    span = corridor.station_span(args.from_station, args.to_station)
    ramps = () if args.no_ramps else corridor.ramps_between(span[0].x_m, span[-1].x_m)
    ramps = tuple(r for r in ramps if r.flow_detectors)
    print(
        f"{corridor.name}: {len(span)} stations from {span[0].id} (x=0 m) to {span[-1].id} "
        f"(x={span[-1].x_m - span[0].x_m:.0f} m), {len(ramps)} ramp detectors, "
        f"{len(dates)} date(s)"
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    table = stations_table(span, ramps)
    # Corridor coordinates are reported from the first requested station, not
    # from the corridor's first node, so x=0 is the modelled boundary.
    table["x_m"] = table["x_m"] - span[0].x_m
    table.to_csv(out_dir / "stations.csv", index=False)

    frame = station_frame(
        config,
        corridor.name,
        [s.id for s in span],
        dates,
        window_s=args.window_s,
        cache_dir=args.cache_dir,
        max_workers=args.max_workers,
        include_ramps=not args.no_ramps,
        district=args.district,
    )
    frame["x_m"] = frame["x_m"] - span[0].x_m
    write_detector_csv(frame, out_dir / "detectors.csv")

    observations = Observations.from_frame(
        frame,
        table,
        window_s=args.window_s,
        t0_local=args.t0,
        duration_s=args.duration_s,
        corridor=corridor.name,
        source={
            "provider": "MnDOT RTMC Mayfly API",
            "district": args.district,
            "dates": dates,
            "url": MAYFLY_BASE_URL,
            "config": METRO_CONFIG_URL,
            "config_time_stamp": config.time_stamp,
            "fetched_at": datetime.now(UTC).isoformat(),
        },
    )
    observations.to_json(out_dir / "observations.json")

    print(f"\ncoverage over {observations.n_windows} windows from {args.t0}:")
    print(coverage(observations).to_string(index=False))
    print(f"\nwrote {out_dir}/stations.csv, detectors.csv, observations.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
