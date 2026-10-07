"""Per-lane (per-detector) MnDOT frames from the 30-second archive.

:func:`calibration.loaders.mndot.station_frame` sums a station's lane
detectors into one station total; a data-quality review needs the lanes one
by one (a degraded loop, a chattering loop or a dead lane is visible against
its neighbours, and much less in the total). :func:`lane_frame` aggregates
each detector on its own with the same validity rule
(:func:`calibration.loaders.mndot.aggregate_day` with one expected detector:
a window is valid when 80 % of its 30-s samples are present) and returns the
tidy detector frame with a trailing ``lane`` column holding the detector
name, ``lanes = 1`` on every row.

The archive is read through :func:`calibration.loaders.mndot.fetch_detector_day`
and its JSON cache. :func:`offline_fetch` is a fetcher that refuses the
network, so a run against a cache never adds to it or writes a 404 into it by
accident.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pandas as pd

from calibration.loaders.detector_csv import DETECTOR_COLUMNS, DetectorKind
from calibration.loaders.mndot import (
    DEFAULT_CACHE_DIR,
    DEFAULT_TIMEZONE,
    MAINLINE_CATEGORY,
    MAYFLY_DISTRICT,
    MAYFLY_ENDPOINTS,
    FetchFn,
    MetroConfig,
    aggregate_day,
    fetch_detector_day,
    parse_date,
    samples_per_window,
)

LANE_COLUMNS: tuple[str, ...] = (*DETECTOR_COLUMNS, "lane")
"""Columns of a per-lane frame."""


def offline_fetch(url: str) -> bytes | None:
    """A :data:`~calibration.loaders.mndot.FetchFn` that never touches the network.

    Raises:
        LookupError: Always — the detector-day is not in the cache.
    """
    raise LookupError(
        f"not in the local cache, and fetching is off (pass allow_fetch / --allow-fetch "
        f"to download it): {url}"
    )


def iris_lane_numbers(
    config: MetroConfig,
    corridor: str,
    stations: Sequence[str] | None = None,
    *,
    separator: str = ":",
) -> dict[str, int]:
    """The IRIS lane number of every mainline lane detector, keyed as :func:`lane_frame`'s sensors.

    ``lane_frame`` rows carry the detector name in ``lane``, so a per-lane
    grid's sensor is ``"<station><separator><detector>"``; the name is not the
    lane. This maps each such sensor of a measured station to its IRIS lane
    (1 = the rightmost mainline lane) for the data-quality lane-order check
    (:func:`calibration.data_quality.lane_order_check`). Only detectors of the
    mainline category with a lane are listed (auxiliary, merge and other loops
    are not lanes of the station's cross-section); abandoned ones are left out.

    Args:
        config: Parsed IRIS configuration.
        corridor: Corridor name (``"I-94 WB"``).
        stations: Station ids to include (default: every station).
        separator: Sensor id separator (``calibration.conservation.LANE_SEPARATOR``).

    Returns:
        Sensor id → IRIS lane number.
    """
    wanted = None if stations is None else {str(s) for s in stations}
    out: dict[str, int] = {}
    for node in config.corridor(corridor).nodes:
        station = node.station_id
        if station is None or (wanted is not None and station not in wanted):
            continue
        for d in node.detectors:
            if d.category == MAINLINE_CATEGORY and d.lane >= 1 and not d.abandoned:
                out[f"{station}{separator}{d.name}"] = int(d.lane)
    return out


def _zone(name: str) -> object | None:
    """The named IANA zone, or None without a tz database (offsets only)."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # absent tzdata or unknown zone: both benign here
        return None


def lane_frame(
    config: MetroConfig,
    corridor: str,
    stations: Sequence[str],
    dates: Sequence[str],
    *,
    window_s: float = 300.0,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    session: FetchFn | None = None,
    include_ramps: bool = True,
    district: str = MAYFLY_DISTRICT,
    timezone: str = DEFAULT_TIMEZONE,
    exclude_detectors: Iterable[str] = (),
    max_workers: int = 8,
) -> pd.DataFrame:
    """One row per detector and window (module docstring).

    Args:
        config: Parsed IRIS configuration.
        corridor: Corridor name (``"I-94 WB"``).
        stations: Station ids; ramps between the first and last are added
            when ``include_ramps``.
        dates: ``YYYYMMDD`` local dates.
        window_s: Analysis window [s]; a multiple of 30 s.
        cache_dir: JSON cache root.
        session: Fetcher; :func:`offline_fetch` to stay on the cache, ``None``
            for the HTTPS default.
        include_ramps: Add each ramp node's flow detectors.
        district: MnDOT district.
        timezone: IANA zone stamping the timestamps' UTC offset.
        exclude_detectors: Detector names not read.
        max_workers: Concurrent cache/archive reads.

    Returns:
        Frame with :data:`LANE_COLUMNS`: ``station`` is the station id or
        ramp node, ``lane`` the detector name, ``lanes`` 1. Sorted by
        timestamp, station, lane; ``attrs["interval_s"]`` set.

    Raises:
        KeyError: Unknown corridor or station.
        ValueError: Bad ``window_s``, no dates, or an excluded detector that
            belongs to no node read here.
        LookupError: A detector-day is missing from the cache under
            :func:`offline_fetch`.
    """
    samples_per_window(window_s)
    if not dates:
        raise ValueError("lane_frame needs at least one date")
    corr = config.corridor(corridor)
    wanted = {corr.station(s).id for s in stations}
    picked = [s for s in corr.stations if s.id in wanted]
    targets: list[tuple[DetectorKind, str, float, tuple[str, ...]]] = [
        ("mainline", s.id, s.x_m, s.detectors) for s in picked
    ]
    if include_ramps and picked:
        xs = [s.x_m for s in picked]
        for r in corr.ramps_between(min(xs), max(xs)):
            if r.flow_detectors:
                targets.append((r.kind, r.node, r.x_m, r.flow_detectors))
    excluded = frozenset(str(d) for d in exclude_detectors)
    known = {name for *_, names in targets for name in names}
    if unknown := sorted(excluded - known):
        raise ValueError(f"exclude_detectors {unknown} belong to no node read here")
    zone = _zone(timezone)
    columns: dict[str, list[object]] = {c: [] for c in LANE_COLUMNS}
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        for date in dates:
            midnight = parse_date(date)
            if zone is not None:
                midnight = midnight.replace(tzinfo=zone)  # type: ignore[arg-type]
            for kind, key, x_m, detectors in targets:
                names = [d for d in detectors if d not in excluded]
                jobs = {
                    (endpoint, name): pool.submit(
                        fetch_detector_day,
                        name,
                        date,
                        endpoint,  # type: ignore[arg-type]
                        cache_dir=cache_dir,
                        session=session,
                        district=district,
                    )
                    for endpoint in MAYFLY_ENDPOINTS
                    for name in names
                }
                for name in names:
                    got = {e: jobs[(e, name)].result() for e in MAYFLY_ENDPOINTS}
                    day = aggregate_day(
                        {name: got["counts"]} if got["counts"] else {},
                        {name: got["occupancy"]} if got["occupancy"] else {},
                        {name: got["speed"]} if got["speed"] else {},
                        window_s=window_s,
                        n_detectors=1,
                    )
                    n = len(day)
                    columns["timestamp"] += [
                        midnight + timedelta(seconds=int(k) * window_s) for k in day["window_index"]
                    ]
                    columns["station"] += [key] * n
                    columns["flow_veh_h"] += day["flow_veh_h"].astype(float).tolist()
                    columns["occupancy_pct"] += day["occupancy_pct"].astype(float).tolist()
                    columns["speed_ms"] += day["speed_ms"].astype(float).tolist()
                    columns["lanes"] += [1] * n
                    columns["kind"] += [kind] * n
                    columns["x_m"] += [float(x_m)] * n
                    columns["lane"] += [name] * n
    frame = pd.DataFrame(columns, columns=list(LANE_COLUMNS))
    if frame.empty:
        return frame
    frame["lanes"] = frame["lanes"].astype(int)
    frame = frame.sort_values(["timestamp", "station", "lane"], kind="stable").reset_index(
        drop=True
    )
    frame.attrs["interval_s"] = float(window_s)
    frame.attrs["excluded_detectors"] = sorted(excluded)
    return frame
