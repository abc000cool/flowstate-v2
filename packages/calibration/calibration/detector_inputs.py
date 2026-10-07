"""A corridor's detector inputs, found the way the onboarding path finds them.

The scripts that review a corridor's detector data
(``scripts/data_quality_report.py``, ``scripts/ramp_estimate.py``) read the
same inputs the onboarding path reads (docs/ONBOARDING_MNDOT.md §3):

* **A corridor directory** (``data/mndot/<corridor>/``): ``detectors.csv``
  (the tidy detector frame, station totals, every window of every fetched
  date), ``stations.csv`` (the stations table with ``x_m``, ``kind`` and the
  ramp nodes) and ``selection.json`` (route, direction, station span and
  dates of the fetch).
* **A generic detector CSV** (a state DOT export), loaded by
  :func:`calibration.loaders.detector_csv.load_detector_csv` with its
  ``column_map`` and unit options, plus an optional stations table.
* **Per-lane MnDOT data from the raw 30-second cache**
  (``data/mndot/cache/<date>/<detector>.<endpoint>.json`` and the IRIS
  ``metro_config.xml.gz``), through
  :func:`calibration.loaders.mndot_lanes.lane_frame`; the corridor, span and
  dates come from ``selection.json`` unless given. The cache is read offline
  unless fetching is allowed.

:func:`add_input_arguments` and :func:`inputs_from_args` give both scripts
one command-line spelling of all three.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from calibration.conservation import normalize_date
from calibration.loaders.detector_csv import load_detector_csv

DETECTORS_FILENAME = "detectors.csv"
STATIONS_FILENAME = "stations.csv"
SELECTION_FILENAME = "selection.json"


@dataclass(frozen=True)
class CorridorInputs:
    """What a review script runs on.

    Attributes:
        frame: Tidy detector frame (with ``lane`` for per-lane data).
        stations: Stations table, or ``None``.
        per_lane: True for per-lane rows.
        provenance: Paths, file hashes, loader options and the frame's own
            hash — enough to say which data a report describes.
        lane_numbers: Lane sensor id → its lane number in the source's
            numbering, when the lane ids are not the numbers (MnDOT per-lane
            mode: the IRIS lane of each detector, 1 = rightmost); empty
            otherwise (``calibration.data_quality.assess_quality``'s
            ``lane_numbers``).
    """

    frame: pd.DataFrame
    stations: pd.DataFrame | None
    per_lane: bool
    provenance: dict[str, Any] = field(default_factory=dict)
    lane_numbers: dict[str, int] = field(default_factory=dict)


def file_sha256(path: str | Path) -> str:
    """Hex SHA-256 of a file's bytes (streamed)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frame_sha256(frame: pd.DataFrame) -> str:
    """Hex SHA-256 of a frame's row hashes (``pandas.util.hash_pandas_object``)."""
    hashed = pd.util.hash_pandas_object(frame.astype(str), index=False)
    return hashlib.sha256(hashed.to_numpy().tobytes()).hexdigest()


def load_corridor_inputs(
    *,
    corridor_dir: str | Path | None = None,
    detectors: str | Path | None = None,
    stations: str | Path | None = None,
    column_map: dict[str, str] | None = None,
    speed_unit: str = "ms",
    occupancy_unit: str = "pct",
    kind_default: str = "mainline",
    lane_column: str | None = None,
    lanes_from_cache: str | Path | None = None,
    metro_config: str | Path | None = None,
    mndot_corridor: str | None = None,
    from_station: str | None = None,
    to_station: str | None = None,
    dates: Sequence[str] | None = None,
    window_s: float | None = None,
    allow_fetch: bool = False,
    exclude_detectors: Sequence[str] = (),
) -> CorridorInputs:
    """Resolve and load a corridor's detector inputs (module docstring).

    Args:
        corridor_dir: Directory holding ``detectors.csv`` (and optionally
            ``stations.csv``, ``selection.json``).
        detectors: Detector CSV (overrides the directory's).
        stations: Stations table CSV (overrides the directory's).
        column_map: Loader column mapping (generic CSV).
        speed_unit: Loader speed unit.
        occupancy_unit: Loader occupancy unit.
        kind_default: Loader ``kind`` default.
        lane_column: Lane column of a per-lane CSV.
        lanes_from_cache: MnDOT JSON cache root — read per-lane data from it
            instead of a detector CSV.
        metro_config: IRIS configuration (per-lane MnDOT mode).
        mndot_corridor: ``"I-94 WB"``; from ``selection.json`` when None.
        from_station: First station; from ``selection.json`` when None.
        to_station: Last station; from ``selection.json`` when None.
        dates: Dates to read in per-lane mode (``YYYYMMDD``/``YYYY-MM-DD``);
            from ``selection.json`` when None.
        window_s: Window [s] in per-lane mode; ``selection.json`` or 300.
        allow_fetch: Per-lane mode may download missing detector-days.
        exclude_detectors: Detector names not read (per-lane mode).

    Returns:
        The :class:`CorridorInputs`.

    Raises:
        ValueError: Nothing to read, or a per-lane request missing its
            corridor, span or dates.
        FileNotFoundError: A named file does not exist.
    """
    base = Path(corridor_dir) if corridor_dir is not None else None
    selection: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    if base is not None:
        provenance["corridor_dir"] = str(base)
        sel_path = base / SELECTION_FILENAME
        if sel_path.is_file():
            selection = json.loads(sel_path.read_text())
            provenance["selection"] = str(sel_path)
            provenance["selection_sha256"] = file_sha256(sel_path)
    stations_path = Path(stations) if stations is not None else None
    if stations_path is None and base is not None and (base / STATIONS_FILENAME).is_file():
        stations_path = base / STATIONS_FILENAME
    table: pd.DataFrame | None = None
    if stations_path is not None:
        if not stations_path.is_file():
            raise FileNotFoundError(f"stations table {stations_path} does not exist")
        table = pd.read_csv(stations_path)
        provenance["stations"] = str(stations_path)
        provenance["stations_sha256"] = file_sha256(stations_path)

    lane_numbers: dict[str, int] = {}
    if lanes_from_cache is not None:
        from calibration.conservation import LANE_SEPARATOR
        from calibration.loaders.mndot import MetroConfig
        from calibration.loaders.mndot_lanes import iris_lane_numbers, lane_frame, offline_fetch

        if metro_config is None:
            raise ValueError("per-lane MnDOT mode needs the IRIS configuration (metro_config)")
        corridor = mndot_corridor or (
            f"{selection['route']} {selection['dir']}"
            if "route" in selection and "dir" in selection
            else None
        )
        first = from_station or selection.get("from_station")
        last = to_station or selection.get("to_station")
        day_list = list(dates or selection.get("dates") or [])
        if not (corridor and first and last and day_list):
            raise ValueError(
                "per-lane MnDOT mode needs a corridor, a station span and dates — from "
                "selection.json or given explicitly"
            )
        config = MetroConfig.load(metro_config)
        span = [s.id for s in config.corridor(corridor).station_span(str(first), str(last))]
        compact = [normalize_date(d).replace("-", "") for d in day_list]
        frame = lane_frame(
            config,
            corridor,
            span,
            compact,
            window_s=float(window_s or selection.get("window_s") or 300.0),
            cache_dir=lanes_from_cache,
            session=None if allow_fetch else offline_fetch,
            exclude_detectors=exclude_detectors,
        )
        lane_numbers = iris_lane_numbers(config, corridor, span, separator=LANE_SEPARATOR)
        provenance.update(
            {
                "mode": "mndot_lanes_from_cache",
                "cache_dir": str(lanes_from_cache),
                "metro_config": str(metro_config),
                "metro_config_sha256": file_sha256(metro_config),
                "metro_config_time_stamp": config.time_stamp,
                "corridor": corridor,
                "stations_span": [first, last],
                "dates": compact,
                "allow_fetch": bool(allow_fetch),
                "exclude_detectors": sorted(exclude_detectors),
            }
        )
        per_lane = True
    else:
        detectors_path = Path(detectors) if detectors is not None else None
        if detectors_path is None and base is not None:
            detectors_path = base / DETECTORS_FILENAME
        if detectors_path is None:
            raise ValueError("no detector input: give a corridor directory or a detector CSV")
        if not detectors_path.is_file():
            raise FileNotFoundError(f"detector CSV {detectors_path} does not exist")
        frame = load_detector_csv(
            detectors_path,
            column_map=column_map,
            speed_unit=speed_unit,  # type: ignore[arg-type]
            occupancy_unit=occupancy_unit,  # type: ignore[arg-type]
            kind_default=kind_default,  # type: ignore[arg-type]
            lane_column=lane_column,
        )
        provenance.update(
            {
                "mode": "detector_csv",
                "detectors": str(detectors_path),
                "detectors_sha256": file_sha256(detectors_path),
                "loader": {
                    "column_map": column_map or {},
                    "speed_unit": speed_unit,
                    "occupancy_unit": occupancy_unit,
                    "kind_default": kind_default,
                    "lane_column": lane_column,
                },
            }
        )
        per_lane = lane_column is not None
    provenance["n_rows"] = len(frame)
    provenance["frame_sha256"] = frame_sha256(frame)
    return CorridorInputs(
        frame=frame,
        stations=table,
        per_lane=per_lane,
        provenance=provenance,
        lane_numbers=lane_numbers,
    )


def add_input_arguments(parser: argparse.ArgumentParser) -> None:
    """The input options both review scripts share."""
    group = parser.add_argument_group("inputs")
    group.add_argument(
        "--corridor-dir",
        type=Path,
        help="corridor directory with detectors.csv [stations.csv, selection.json], "
        "e.g. data/mndot/mndot_i94_wb_stpaul",
    )
    group.add_argument("--detectors", type=Path, help="detector CSV (overrides the directory's)")
    group.add_argument("--stations", type=Path, help="stations table CSV (positions, kinds)")
    group.add_argument(
        "--column-map", help="JSON object: canonical field -> the CSV's column (generic CSV)"
    )
    group.add_argument("--speed-unit", default="ms", choices=["ms", "kmh", "mph"])
    group.add_argument("--occupancy-unit", default="pct", choices=["pct", "percent", "fraction"])
    group.add_argument(
        "--kind-default", default="mainline", choices=["mainline", "on_ramp", "off_ramp"]
    )
    group.add_argument("--lane-column", help="lane column of a per-lane detector CSV")
    group.add_argument(
        "--lanes-from-cache",
        type=Path,
        help="MnDOT JSON cache root: read per-lane data from the raw 30-s archive cache",
    )
    group.add_argument("--metro-config", type=Path, help="IRIS metro_config.xml(.gz)")
    group.add_argument("--mndot-corridor", help="e.g. 'I-94 WB' (default: selection.json)")
    group.add_argument("--from-station", help="first station (default: selection.json)")
    group.add_argument("--to-station", help="last station (default: selection.json)")
    group.add_argument(
        "--allow-fetch",
        action="store_true",
        help="per-lane mode may download detector-days missing from the cache",
    )
    group.add_argument(
        "--exclude-detectors", default="", help="comma-separated detector names not read"
    )
    span = parser.add_argument_group("span")
    span.add_argument("--dates", default="", help="comma-separated dates (YYYYMMDD or YYYY-MM-DD)")
    span.add_argument("--start", help="local clock start of the daily span, HH:MM")
    span.add_argument("--end", help="local clock end of the daily span, HH:MM (24:00 allowed)")


def split_list(text: str | None) -> list[str]:
    """``"a,b, c"`` → ``["a", "b", "c"]`` (empty → ``[]``)."""
    return [p.strip() for p in str(text or "").split(",") if p.strip()]


def inputs_from_args(args: argparse.Namespace) -> CorridorInputs:
    """:func:`load_corridor_inputs` from :func:`add_input_arguments`' options.

    Raises:
        ValueError: ``--column-map`` is not a JSON object of strings.
    """
    column_map = None
    if args.column_map:
        parsed = json.loads(args.column_map)
        if not isinstance(parsed, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
        ):
            raise ValueError("--column-map must be a JSON object of string -> string")
        column_map = parsed
    return load_corridor_inputs(
        corridor_dir=args.corridor_dir,
        detectors=args.detectors,
        stations=args.stations,
        column_map=column_map,
        speed_unit=args.speed_unit,
        occupancy_unit=args.occupancy_unit,
        kind_default=args.kind_default,
        lane_column=args.lane_column,
        lanes_from_cache=args.lanes_from_cache,
        metro_config=args.metro_config,
        mndot_corridor=args.mndot_corridor,
        from_station=args.from_station,
        to_station=args.to_station,
        dates=split_list(args.dates) or None,
        allow_fetch=bool(args.allow_fetch),
        exclude_detectors=split_list(args.exclude_detectors),
    )
