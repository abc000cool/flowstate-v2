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

``--wave-context`` adds the corridor's *observed* backward wave speed
(:mod:`calibration.waves_observed`) to the artifact under
``context["detector_wave_speed"]`` and prints the per-pair table, the
leave-one-date-out medians and the summary line. It is
estimated from the raw 30-second speed series, not from the analysis windows,
and is context for a validation report — the reviewer's comparison between the
corridor's real wave speed and the 14–22 km/h band the model is scored
against — never a criterion of its own.

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
import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from calibration.loaders.detector_csv import write_detector_csv
from calibration.loaders.mndot import (
    DEFAULT_CACHE_DIR,
    MAYFLY_BASE_URL,
    MAYFLY_DISTRICT,
    METRO_CONFIG_URL,
    SAMPLE_INTERVAL_S,
    SPEED_SERIES_GAP_S,
    MetroConfig,
    Station,
    fetch_metro_config,
    station_frame,
    station_speed_series,
    stations_table,
)
from calibration.observations import Observations, coverage, parse_clock
from calibration.waves_observed import (
    LeaveOneDateOut,
    ObservedWaveSpeed,
    concatenate_dates,
    detector_wave_speed,
    leave_one_date_out,
    summary_line,
)

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
    parser.add_argument(
        "--exclude-detectors",
        default="",
        help=(
            "Comma-separated detector names a reviewer has ruled faulty (never fetched; "
            "a mainline lane they served is scaled as a dead lane, a ramp node left "
            "without a flow detector is dropped). Recorded with --exclude-reason under "
            "the artifact's source.excluded_detectors."
        ),
    )
    parser.add_argument(
        "--exclude-reason",
        default="",
        help="Why the --exclude-detectors are excluded (required with them).",
    )
    parser.add_argument(
        "--wave-context",
        action="store_true",
        help=(
            "Estimate the corridor's observed backward wave speed from the raw 30-s "
            "station speed series and record it under the artifact's "
            "context.detector_wave_speed (context for a report, never a criterion)."
        ),
    )
    return parser


def _wave_context(
    config: MetroConfig,
    corridor_name: str,
    span: tuple[Station, ...],
    dates: list[str],
    *,
    t0_local: str,
    duration_s: float,
    cache_dir: str,
    max_workers: int,
    district: str,
) -> ObservedWaveSpeed:
    """Estimate the observed backward wave speed over the analysed span.

    Args:
        config: Parsed IRIS configuration.
        corridor_name: Corridor the span belongs to.
        span: The mainline stations, upstream first.
        dates: Local dates, concatenated into one series per station.
        t0_local: Local wall clock of the span's start.
        duration_s: Analysed span [s].
        cache_dir: JSON cache root (a cached corridor costs no requests).
        max_workers: Thread-pool size for the archive requests.
        district: MnDOT district.

    Returns:
        The per-pair estimates, the corridor summary and — with two dates or
        more — the leave-one-date-out sensitivity of the median.
    """
    by_date = {
        date: station_speed_series(
            config,
            corridor_name,
            [s.id for s in span],
            [date],
            t0_s=parse_clock(t0_local),
            duration_s=duration_s,
            cache_dir=cache_dir,
            max_workers=max_workers,
            district=district,
        )
        for date in dates
    }
    positions = {s.id: s.x_m - span[0].x_m for s in span}
    gap_bins = round(SPEED_SERIES_GAP_S / SAMPLE_INTERVAL_S)
    # the dates are correlated as one series with the same separator
    # `station_speed_series` would have written for the whole list at once
    series = concatenate_dates(by_date, gap_bins=gap_bins)
    loo = (
        leave_one_date_out(
            by_date,
            positions,
            dt_s=SAMPLE_INTERVAL_S,
            gap_s=SPEED_SERIES_GAP_S,
        )
        if len(by_date) > 1
        else None
    )
    return detector_wave_speed(
        series, positions, dt_s=SAMPLE_INTERVAL_S, gap_s=SPEED_SERIES_GAP_S, loo=loo
    )


def _wave_table(result: ObservedWaveSpeed) -> pd.DataFrame:
    """The per-pair estimates as a printable frame (one row per pair)."""
    return pd.DataFrame(
        [
            {
                "downstream": pair.downstream,
                "upstream": pair.upstream,
                "dx_m": round(pair.dx_m, 1),
                "lag_s": round(pair.lag_s, 1),
                "speed_kmh": round(pair.speed_kmh, 1),
                "corr": round(pair.correlation, 3),
                "n_samples": pair.n_samples,
                "n_events": pair.n_events,
                "used": pair.used,
                "reason": pair.reason,
            }
            for pair in result.pairs
        ]
    )


def _loo_table(loo: LeaveOneDateOut) -> pd.DataFrame:
    """The leave-one-date-out medians as a printable frame (one row per date)."""
    return pd.DataFrame(
        [
            {
                "omitted": date,
                "median_kmh": round(median, 1),
                "n_used": used,
            }
            for date, median, used in zip(loo.dates, loo.medians_kmh, loo.n_used, strict=True)
        ]
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = _build_parser().parse_args(argv)
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    if not dates:
        print("--dates needs at least one YYYYMMDD date")
        return 2

    excluded = [d.strip() for d in args.exclude_detectors.split(",") if d.strip()]
    if excluded and not args.exclude_reason.strip():
        print("--exclude-detectors needs --exclude-reason (the decision is recorded)")
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
    if excluded:
        print(f"excluded detectors {excluded}: {args.exclude_reason.strip()}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    table = stations_table(span, ramps, exclude_detectors=excluded)
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
        exclude_detectors=excluded,
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
            "excluded_detectors": {name: args.exclude_reason.strip() for name in excluded},
            "scaled_station_days": frame.attrs.get("scaled_station_days", []),
        },
    )
    if args.wave_context:
        wave = _wave_context(
            config,
            corridor.name,
            span,
            dates,
            t0_local=args.t0,
            duration_s=args.duration_s,
            cache_dir=args.cache_dir,
            max_workers=args.max_workers,
            district=args.district,
        )
        observations = dataclasses.replace(
            observations, context={"detector_wave_speed": wave.to_dict()}
        )
        print("\nobserved backward wave speed, adjacent mainline station pairs:")
        print(_wave_table(wave).to_string(index=False))
        if wave.loo is not None:
            print("\nleave-one-date-out (the corridor re-estimated without each date):")
            print(_loo_table(wave.loo).to_string(index=False))
        print(f"detector-estimated backward wave speed: {summary_line(wave)}")
    observations.to_json(out_dir / "observations.json")

    print(f"\ncoverage over {observations.n_windows} windows from {args.t0}:")
    print(coverage(observations).to_string(index=False))
    print(f"\nwrote {out_dir}/stations.csv, detectors.csv, observations.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
