"""MnDOT Regional TMC detector data: network metadata + 30-second archives.

Two public data products of the Minnesota DOT Regional Transportation
Management Center feed a corridor onboarding (CLAUDE.md §6.1/§6.3):

**Network metadata** — ``https://data.dot.state.mn.us/iris_xml/metro_config.xml.gz``
is the IRIS system configuration: ``<corridor route dir>`` elements holding an
ordered chain of ``<r_node>`` elements (mainline *stations*, ``Entrance`` and
``Exit`` ramp nodes, intersections) with coordinates, lane counts, speed
limits and their ``<detector>`` children. A detector's ``category`` says what
it measures: empty for a mainline lane detector, ``Merge`` for the lane a
metered entrance ramp merges from, ``Exit`` for an exit ramp, and
``Queue``/``Bypass``/``Passage``/``Green``/``HOV``/``Auxiliary``/… for the
rest. :class:`MetroConfig` parses that file and gives each node a corridor
distance ``x_m`` — the cumulative great-circle distance along the r_node
chain, which is the corridor coordinate the scenario builder and the
observations artifact both use.

**30-second archives** — the "Mayfly" JSON API
(``https://data.dot.state.mn.us/mayfly/{counts|occupancy|speed}``) answers one
detector-day per request with a list of 2880 values, one per 30-second bin
from local midnight: ``counts`` are vehicles per 30 s (int), ``occupancy`` is
percent (float), ``speed`` is mph (int), and ``null`` marks a bin the detector
did not report. HTTP 404 means the archive has nothing for that detector-day
at all.

Aggregation to analysis windows (:func:`aggregate_day`, :func:`station_frame`)
follows one rule, stated here because every observed flow in a validation
report descends from it: a window is **valid** when at least
:data:`MIN_SAMPLE_FRACTION` (80%) of the station's mainline 30-second samples
are present; an invalid window is NaN in all three quantities and is never
estimated. In a valid window the count is scaled to the full window in two
explicit stages — each detector's present counts are scaled by its own
presence (``n_samples / n_present``), and the station total is then scaled by
``n_detectors / n_detectors_with_data`` to stand in for a lane that reported
nothing. The second stage assumes the missing lane carries the mean flow of
the reporting lanes, which is *not* true lane by lane; it is bounded by the
80% rule (at three lanes a fully missing lane already fails it) and is
recorded, so a consumer can tell a scaled window from a complete one.

Nothing in this module is called from the test suite over the network: the
fetcher takes an injected ``session`` and a JSON cache directory, and the
tests exercise the parser and the aggregation on fixtures.
"""

from __future__ import annotations

import gzip
import json
import math
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Final, Literal

import pandas as pd

from calibration.loaders.detector_csv import DETECTOR_COLUMNS, DetectorKind
from calibration.loaders.pems import MPH_TO_MS

MAYFLY_BASE_URL: Final[str] = "https://data.dot.state.mn.us/mayfly"
"""Base URL of the MnDOT 30-second archive API (HTTPS only)."""

METRO_CONFIG_URL: Final[str] = "https://data.dot.state.mn.us/iris_xml/metro_config.xml.gz"
"""Gzipped IRIS configuration of the metro district."""

MAYFLY_DISTRICT: Final[str] = "metro"
"""Default district; ``/mayfly/districts`` lists the others."""

Endpoint = Literal["counts", "occupancy", "speed"]

MAYFLY_ENDPOINTS: Final[tuple[str, ...]] = ("counts", "occupancy", "speed")
"""The three per-detector series the archive publishes."""

SAMPLE_INTERVAL_S: Final[float] = 30.0
"""Archive bin width [s]."""

SAMPLES_PER_DAY: Final[int] = 2880
"""Bins in one archive day (24 h / 30 s)."""

MIN_SAMPLE_FRACTION: Final[float] = 0.8
"""Share of a window's 30-s samples that must be present for it to be valid."""

MAINLINE_CATEGORY: Final[str] = ""
AUXILIARY_CATEGORY: Final[str] = "Auxiliary"
"""``detector@category`` of an auxiliary lane (counted in the station cross-section total)."""
"""``detector@category`` of a mainline lane detector (the attribute is empty)."""

ON_RAMP_CATEGORY: Final[str] = "Merge"
"""Category of the detector measuring an entrance ramp's merging flow."""

OFF_RAMP_CATEGORY: Final[str] = "Exit"
"""Category of the detector measuring an exit ramp's flow."""

DEFAULT_TIMEZONE: Final[str] = "America/Chicago"
"""Local clock of the MnDOT metro district (archive bins start at local
midnight). Used only to stamp the tidy frame's timestamps with their UTC
offset; window indexing is done on the local wall clock either way."""

DEFAULT_CACHE_DIR: Final[str] = "data/mndot/cache"
"""Conventional JSON cache root: ``<cache_dir>/<date>/<detector>.<endpoint>.json``."""

EARTH_RADIUS_M: Final[float] = 6371008.8
"""IUGG mean Earth radius [m], for the great-circle chain distance."""

#: Injected fetcher: ``url -> body bytes``, or ``None`` when the archive has
#: no data for that detector-day (HTTP 404). Tests pass a stub; the default is
#: :func:`urlopen_fetch`.
FetchFn = Callable[[str], bytes | None]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance [m] between two WGS-84 points.

    Args:
        lat1: First latitude [deg].
        lon1: First longitude [deg].
        lat2: Second latitude [deg].
        lon2: Second longitude [deg].

    Returns:
        Distance along the sphere of radius :data:`EARTH_RADIUS_M` [m].
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# metro_config.xml
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Detector:
    """One IRIS detector on an r_node.

    Attributes:
        name: Detector id as the archive API addresses it (e.g. ``"9063"``).
        category: IRIS category — empty for a mainline lane detector, else
            ``Merge``, ``Exit``, ``Queue``, ``Green``, ``HOV``, ….
        lane: Lane number (1 = the right-most mainline lane in IRIS; 0 when
            the detector is not lane-specific).
        label: Human-readable label from the configuration.
        abandoned: True for a decommissioned detector (never fetched).
    """

    name: str
    category: str
    lane: int
    label: str
    abandoned: bool


@dataclass(frozen=True)
class RNode:
    """One node of a corridor's ordered chain.

    Attributes:
        name: IRIS node id (``rnd_10786``).
        n_type: ``Station``, ``Entrance``, ``Exit``, ``Intersection``,
            ``Access`` or ``Interchange``.
        station_id: Mainline station id (``"S2104"``) when the node is a
            measured station, else None.
        label: Cross-street or landmark label.
        lat: Latitude [deg].
        lon: Longitude [deg].
        lanes: Lane count at the node (0 when unstated).
        speed_limit_ms: Posted limit [m/s], None when unstated.
        x_m: Cumulative great-circle distance along the corridor chain [m],
            0 at the corridor's first node.
        detectors: The node's detectors, in file order.
        active: IRIS ``active`` flag.
        abandoned: IRIS ``abandoned`` flag.
    """

    name: str
    n_type: str
    station_id: str | None
    label: str
    lat: float
    lon: float
    lanes: int
    speed_limit_ms: float | None
    x_m: float
    detectors: tuple[Detector, ...]
    active: bool
    abandoned: bool

    def detectors_by_category(self, category: str) -> tuple[str, ...]:
        """Names of this node's live detectors in ``category`` (lane order)."""
        picked = [d for d in self.detectors if d.category == category and not d.abandoned]
        return tuple(d.name for d in sorted(picked, key=lambda d: (d.lane, d.name)))


@dataclass(frozen=True)
class Station:
    """A mainline measurement station (an r_node carrying a ``station_id``).

    Attributes:
        id: Station id (``"S2104"``) — the tidy frame's ``station``.
        node: The r_node name it comes from.
        label: Cross-street label.
        lat: Latitude [deg].
        lon: Longitude [deg].
        lanes: Mainline lanes at the station.
        speed_limit_ms: Posted limit [m/s], None when unstated.
        x_m: Corridor distance [m] (see :attr:`RNode.x_m`).
        detectors: Live mainline detector names (category empty), lane order.
    """

    id: str
    node: str
    label: str
    lat: float
    lon: float
    lanes: int
    speed_limit_ms: float | None
    x_m: float
    detectors: tuple[str, ...]


@dataclass(frozen=True)
class RampNode:
    """An ``Entrance`` or ``Exit`` node and its detectors.

    Attributes:
        node: The r_node name — also the tidy frame's ``station`` for ramp
            rows, since ramp nodes carry no ``station_id``.
        kind: ``on_ramp`` (Entrance) or ``off_ramp`` (Exit).
        label: Cross-street label.
        lat: Latitude [deg].
        lon: Longitude [deg].
        lanes: Lane count at the ramp node.
        x_m: Corridor distance [m].
        detectors: Live detector names keyed by IRIS category.
    """

    node: str
    kind: DetectorKind
    label: str
    lat: float
    lon: float
    lanes: int
    x_m: float
    detectors: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def flow_detectors(self) -> tuple[str, ...]:
        """The detectors that measure the ramp's flow (Merge / Exit)."""
        category = ON_RAMP_CATEGORY if self.kind == "on_ramp" else OFF_RAMP_CATEGORY
        return self.detectors.get(category, ())


@dataclass(frozen=True)
class Corridor:
    """One ``<corridor route dir>`` of the configuration.

    Attributes:
        route: Route name (``"I-94"``).
        direction: Direction (``"WB"``).
        nodes: The full r_node chain in file order.
        stations: The measured mainline stations, in chain order.
        ramps: The Entrance/Exit nodes, in chain order.
    """

    route: str
    direction: str
    nodes: tuple[RNode, ...]
    stations: tuple[Station, ...]
    ramps: tuple[RampNode, ...]

    @property
    def name(self) -> str:
        """``"<route> <direction>"`` — how corridors are addressed here."""
        return f"{self.route} {self.direction}"

    def station(self, station_id: str) -> Station:
        """The station with ``station_id``.

        Raises:
            KeyError: No such station on this corridor.
        """
        for s in self.stations:
            if s.id == station_id:
                return s
        raise KeyError(f"corridor {self.name!r} has no station {station_id!r}")

    def station_span(self, first: str, last: str) -> tuple[Station, ...]:
        """Stations from ``first`` to ``last`` inclusive, in chain order.

        Args:
            first: Upstream station id (as the chain is ordered).
            last: Downstream station id.

        Returns:
            The contiguous run of stations between the two ids.

        Raises:
            KeyError: Either id is unknown.
            ValueError: ``last`` precedes ``first`` in the chain.
        """
        ids = [s.id for s in self.stations]
        try:
            i, j = ids.index(first), ids.index(last)
        except ValueError as exc:
            missing = first if first not in ids else last
            raise KeyError(f"corridor {self.name!r} has no station {missing!r}") from exc
        if j < i:
            raise ValueError(
                f"station {last!r} precedes {first!r} on corridor {self.name!r}; the span "
                f"must be given in chain order"
            )
        return tuple(self.stations[i : j + 1])

    def ramps_between(self, x_lo: float, x_hi: float) -> tuple[RampNode, ...]:
        """Ramp nodes whose ``x_m`` lies in ``[x_lo, x_hi]``."""
        return tuple(r for r in self.ramps if x_lo <= r.x_m <= x_hi)


def _as_bool(value: str | None, default: bool = False) -> bool:
    """IRIS ``'t'``/``'f'`` flag → bool."""
    if value is None:
        return default
    return value.strip().lower() in ("t", "true", "1", "yes")


def _as_int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(value)) if value not in (None, "") else default
    except ValueError:
        return default


def _as_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


@dataclass(frozen=True)
class MetroConfig:
    """Parsed IRIS ``metro_config.xml`` (module docstring).

    Attributes:
        time_stamp: The file's own ``tms_config@time_stamp`` (provenance).
        corridors: Corridors keyed by ``"<route> <direction>"``.
    """

    time_stamp: str
    corridors: dict[str, Corridor]

    @classmethod
    def load(cls, path_or_gz: str | Path) -> MetroConfig:
        """Parse a local ``metro_config.xml`` or ``metro_config.xml.gz``.

        Gzip is detected from the file's magic bytes, not its name, so a
        ``.gz`` copy saved without the suffix still loads.

        Args:
            path_or_gz: Path to the configuration file.

        Returns:
            The parsed configuration.

        Raises:
            ValueError: The file holds no ``<corridor>`` element (wrong file,
                or a truncated download).
        """
        path = Path(path_or_gz)
        with path.open("rb") as raw:
            gzipped = raw.read(2) == b"\x1f\x8b"
        handle: BinaryIO = (
            gzip.open(path, "rb") if gzipped else path.open("rb")  # type: ignore[assignment]
        )
        corridors: dict[str, Corridor] = {}
        time_stamp = ""
        try:
            for event, element in ET.iterparse(handle, events=("start", "end")):
                if event == "start" and element.tag == "tms_config":
                    time_stamp = element.get("time_stamp", "")
                    continue
                if event != "end" or element.tag != "corridor":
                    continue
                corridor = _parse_corridor(element)
                corridors[corridor.name] = corridor
                element.clear()
        finally:
            handle.close()
        if not corridors:
            raise ValueError(f"{path}: no <corridor> element — not an IRIS metro_config file")
        return cls(time_stamp=time_stamp, corridors=corridors)

    def corridor(self, name: str) -> Corridor:
        """The corridor named ``"<route> <direction>"``.

        Raises:
            KeyError: No such corridor; the message lists near matches.
        """
        if name in self.corridors:
            return self.corridors[name]
        route = name.split()[0] if name.split() else name
        near = sorted(k for k in self.corridors if k.startswith(route))
        raise KeyError(f"no corridor {name!r}; corridors on {route!r}: {near or 'none'}")

    def corridor_names(self) -> tuple[str, ...]:
        """Every corridor name in file order."""
        return tuple(self.corridors)


def _parse_corridor(element: ET.Element) -> Corridor:
    """One ``<corridor>`` element → :class:`Corridor` with chain distances."""
    nodes: list[RNode] = []
    x_m = 0.0
    prev: tuple[float, float] | None = None
    for node_el in element.findall("r_node"):
        lat = _as_float(node_el.get("lat"))
        lon = _as_float(node_el.get("lon"))
        if lat is None or lon is None:
            continue
        if prev is not None:
            x_m += haversine_m(prev[0], prev[1], lat, lon)
        prev = (lat, lon)
        detectors = tuple(
            Detector(
                name=d.get("name", ""),
                category=d.get("category", MAINLINE_CATEGORY),
                lane=_as_int(d.get("lane"), 0),
                label=d.get("label", ""),
                abandoned=_as_bool(d.get("abandoned"), False),
            )
            for d in node_el.findall("detector")
        )
        s_limit_mph = _as_float(node_el.get("s_limit"))
        nodes.append(
            RNode(
                name=node_el.get("name", ""),
                n_type=node_el.get("n_type", "Station"),
                station_id=node_el.get("station_id"),
                label=node_el.get("label", ""),
                lat=lat,
                lon=lon,
                lanes=_as_int(node_el.get("lanes"), 0),
                speed_limit_ms=None if s_limit_mph is None else s_limit_mph * MPH_TO_MS,
                x_m=x_m,
                detectors=detectors,
                active=_as_bool(node_el.get("active"), True),
                abandoned=_as_bool(node_el.get("abandoned"), False),
            )
        )
    stations = tuple(
        Station(
            id=str(n.station_id),
            node=n.name,
            label=n.label,
            lat=n.lat,
            lon=n.lon,
            lanes=n.lanes,
            speed_limit_ms=n.speed_limit_ms,
            x_m=n.x_m,
            # The station total is a cross-section count: the mainline lanes plus
            # any auxiliary lane (an on-to-off lane between interchanges) that a
            # simulated vehicle crossing the same x would also be counted in.
            detectors=n.detectors_by_category(MAINLINE_CATEGORY)
            + n.detectors_by_category(AUXILIARY_CATEGORY),
        )
        for n in nodes
        if n.station_id
    )
    ramps = tuple(
        RampNode(
            node=n.name,
            kind="on_ramp" if n.n_type == "Entrance" else "off_ramp",
            label=n.label,
            lat=n.lat,
            lon=n.lon,
            lanes=n.lanes,
            x_m=n.x_m,
            detectors={
                category: n.detectors_by_category(category)
                for category in sorted({d.category for d in n.detectors if not d.abandoned})
            },
        )
        for n in nodes
        if n.n_type in ("Entrance", "Exit")
    )
    return Corridor(
        route=element.get("route", ""),
        direction=element.get("dir", ""),
        nodes=tuple(nodes),
        stations=stations,
        ramps=ramps,
    )


# ---------------------------------------------------------------------------
# Mayfly 30-second archive
# ---------------------------------------------------------------------------


def mayfly_url(
    detector: str, date: str, endpoint: Endpoint, *, district: str = MAYFLY_DISTRICT
) -> str:
    """URL of one detector-day series.

    Args:
        detector: Detector name as IRIS spells it (``"9063"``, ``"T9527"``).
        date: ``YYYYMMDD`` local date.
        endpoint: ``counts``, ``occupancy`` or ``speed``.
        district: MnDOT district (``/mayfly/districts`` lists them).

    Returns:
        The fully encoded request URL.

    Raises:
        ValueError: Unknown endpoint or a malformed date.
    """
    if endpoint not in MAYFLY_ENDPOINTS:
        raise ValueError(f"endpoint must be one of {list(MAYFLY_ENDPOINTS)}, got {endpoint!r}")
    day = parse_date(date)
    query = urllib.parse.urlencode(
        {
            "district": district,
            "year": f"{day.year:04d}",
            "date": date,
            "detector": detector,
        }
    )
    return f"{MAYFLY_BASE_URL}/{endpoint}?{query}"


def parse_date(date: str) -> datetime:
    """``YYYYMMDD`` → a naive local ``datetime`` at midnight.

    Raises:
        ValueError: The string is not eight digits of a real date.
    """
    try:
        return datetime.strptime(date, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"date must be YYYYMMDD, got {date!r}") from exc


def urlopen_fetch(url: str, *, timeout_s: float = 60.0) -> bytes | None:
    """Default :data:`FetchFn`: GET ``url``, returning None on HTTP 404.

    Args:
        url: Request URL.
        timeout_s: Socket timeout [s].

    Returns:
        The response body, or None when the archive has no data for that
        detector-day (HTTP 404).
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            body: bytes = response.read()
            return body
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def fetch_metro_config(
    dest: str | Path, *, url: str = METRO_CONFIG_URL, session: FetchFn | None = None
) -> Path:
    """Download the IRIS configuration to ``dest`` (gzip, ~0.5 MB).

    Args:
        dest: Destination path (parent directories are created).
        url: Source URL (default :data:`METRO_CONFIG_URL`).
        session: Injected fetcher (see :data:`FetchFn`).

    Returns:
        The destination path.

    Raises:
        ValueError: The server answered 404 for the configuration URL.
    """
    fetch = session if session is not None else urlopen_fetch
    body = fetch(url)
    if body is None:
        raise ValueError(f"{url} returned HTTP 404 — the IRIS configuration URL has moved")
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return target


def fetch_detector_day(
    detector: str,
    date: str,
    endpoint: Endpoint,
    *,
    cache_dir: str | Path,
    session: FetchFn | None = None,
    district: str = MAYFLY_DISTRICT,
) -> list[float | None]:
    """One detector-day series, from the JSON cache or the archive API.

    The response is cached verbatim at
    ``<cache_dir>/<date>/<detector>.<endpoint>.json`` (a 404 is cached as
    ``null``), so re-running an aggregation costs no requests and a partial
    pull resumes.

    Args:
        detector: Detector name.
        date: ``YYYYMMDD`` local date.
        endpoint: ``counts``, ``occupancy`` or ``speed``.
        cache_dir: Root of the JSON cache.
        session: Injected fetcher (see :data:`FetchFn`); the default performs
            the HTTPS GET. Tests always pass one — the suite never touches
            the network.
        district: MnDOT district.

    Returns:
        ``SAMPLES_PER_DAY`` values with ``None`` for a bin the detector did
        not report, or an **empty list** when the archive holds nothing for
        that detector-day (HTTP 404).

    Raises:
        ValueError: The response is not a JSON list of numbers/nulls.
    """
    cached = Path(cache_dir) / date / f"{detector}.{endpoint}.json"
    if cached.is_file():
        payload = json.loads(cached.read_text())
    else:
        url = mayfly_url(detector, date, endpoint, district=district)
        fetch = session if session is not None else urlopen_fetch
        body = fetch(url)
        payload = None if body is None else json.loads(body)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload))
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError(
            f"mayfly {endpoint} for detector {detector} on {date}: expected a JSON list, "
            f"got {type(payload).__name__}"
        )
    return [None if v is None else float(v) for v in payload]


# ---------------------------------------------------------------------------
# Aggregation to analysis windows
# ---------------------------------------------------------------------------


def _window_slices(n_samples: int, samples_per_window: int) -> list[tuple[int, int]]:
    """``[start, stop)`` sample index pairs of the whole-day windows."""
    n_windows = n_samples // samples_per_window
    return [(w * samples_per_window, (w + 1) * samples_per_window) for w in range(n_windows)]


def samples_per_window(window_s: float) -> int:
    """Number of 30-s archive bins in one analysis window.

    Raises:
        ValueError: ``window_s`` is not a positive multiple of 30 s.
    """
    n = window_s / SAMPLE_INTERVAL_S
    if window_s <= 0 or abs(n - round(n)) > 1e-9:
        raise ValueError(
            f"window_s must be a positive multiple of {SAMPLE_INTERVAL_S:g} s, got {window_s!r}"
        )
    return round(n)


def aggregate_day(
    counts: Mapping[str, Sequence[float | None]],
    occupancy: Mapping[str, Sequence[float | None]],
    speeds: Mapping[str, Sequence[float | None]],
    *,
    window_s: float = 300.0,
    n_detectors: int | None = None,
    min_sample_fraction: float = MIN_SAMPLE_FRACTION,
) -> pd.DataFrame:
    """Aggregate one station-day of 30-s series into analysis windows.

    The validity rule and the two-stage count scaling are stated in the
    module docstring. Speed and occupancy are plain means over the present
    samples of all detectors; a speed sample of 0 is treated as *no
    measurement* (a loop reports 0 for a bin no vehicle crossed, which is an
    absence of evidence, not a measured standstill).

    Args:
        counts: Detector name → vehicles per 30-s bin (``None`` = missing).
        occupancy: Detector name → occupancy percent per bin.
        speeds: Detector name → speed mph per bin.
        window_s: Analysis window [s]; a multiple of 30 s.
        n_detectors: Detectors the station *should* have; defaults to
            ``len(counts)``. Pass the station's lane-detector count when some
            detectors returned nothing at all, so their absence counts
            against the validity rule instead of vanishing.
        min_sample_fraction: Validity threshold (default
            :data:`MIN_SAMPLE_FRACTION`).

    Returns:
        DataFrame indexed 0..n_windows−1 with columns ``window_index``,
        ``flow_veh_h``, ``occupancy_pct``, ``speed_ms`` and
        ``fraction_valid`` (the share of expected count samples present).

    Raises:
        ValueError: ``window_s`` is not a multiple of 30 s, or the series
            have unequal lengths.
    """
    per_window = samples_per_window(window_s)
    lengths = {len(v) for v in list(counts.values()) + list(occupancy.values())}
    lengths |= {len(v) for v in speeds.values()}
    lengths.discard(0)
    if len(lengths) > 1:
        raise ValueError(f"detector series have unequal lengths {sorted(lengths)}")
    # No detector reported anything: still lay out the whole day's windows, so
    # the station appears in the frame as measured-and-missing rather than
    # silently absent.
    n_samples = lengths.pop() if lengths else SAMPLES_PER_DAY
    expected_detectors = int(n_detectors if n_detectors is not None else len(counts))
    rows: list[dict[str, float]] = []
    for w, (lo, hi) in enumerate(_window_slices(n_samples, per_window)):
        present = 0
        scaled_total = 0.0
        with_data = 0
        for series in counts.values():
            window = [v for v in series[lo:hi] if v is not None]
            present += len(window)
            if window:
                with_data += 1
                scaled_total += sum(window) * per_window / len(window)
        expected = expected_detectors * per_window
        fraction = present / expected if expected else 0.0
        if expected and fraction >= min_sample_fraction and with_data:
            veh_in_window = scaled_total * expected_detectors / with_data
            flow_veh_h = veh_in_window * 3600.0 / window_s
            occ_samples = [
                v for series in occupancy.values() for v in series[lo:hi] if v is not None
            ]
            speed_samples = [
                v for series in speeds.values() for v in series[lo:hi] if v is not None and v > 0.0
            ]
            occ = sum(occ_samples) / len(occ_samples) if occ_samples else float("nan")
            speed = (
                sum(speed_samples) / len(speed_samples) * MPH_TO_MS
                if speed_samples
                else float("nan")
            )
        else:
            flow_veh_h = occ = speed = float("nan")
        rows.append(
            {
                "window_index": float(w),
                "flow_veh_h": flow_veh_h,
                "occupancy_pct": occ,
                "speed_ms": speed,
                "fraction_valid": fraction,
            }
        )
    frame = pd.DataFrame(
        rows, columns=["window_index", "flow_veh_h", "occupancy_pct", "speed_ms", "fraction_valid"]
    )
    frame["window_index"] = frame["window_index"].astype(int)
    return frame


def _zone(name: str) -> object | None:
    """The named IANA zone, or None when the platform ships no tz database.

    A missing zone database costs the timestamps their UTC offset, not their
    meaning: the window index is computed from the local wall clock, which is
    what the archive's bins are numbered by.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # absent tzdata or unknown zone: both benign here
        return None


def station_frame(
    config: MetroConfig,
    corridor: str,
    stations: Sequence[str],
    dates: Sequence[str],
    *,
    window_s: float = 300.0,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    max_workers: int = 8,
    session: FetchFn | None = None,
    include_ramps: bool = True,
    district: str = MAYFLY_DISTRICT,
    timezone: str = DEFAULT_TIMEZONE,
) -> pd.DataFrame:
    """Fetch and aggregate a corridor's stations into the tidy frame.

    One station-date at a time: that station's detector-day series are
    fetched through a thread pool, aggregated into windows, and released —
    so peak memory is one station-day of 30-s samples, not the whole pull.
    Ramp nodes between the first and last requested station are included as
    ``on_ramp``/``off_ramp`` rows when they carry a ``Merge``/``Exit``
    detector.

    Args:
        config: Parsed :class:`MetroConfig`.
        corridor: Corridor name (``"I-94 WB"``).
        stations: Station ids to fetch, any order (output is chain-ordered).
        dates: ``YYYYMMDD`` local dates.
        window_s: Analysis window [s]; a multiple of 30 s.
        cache_dir: JSON cache root (see :func:`fetch_detector_day`).
        max_workers: Thread-pool size for the archive requests (the API
            answers one detector-day in ~0.4 s; 8 is the polite ceiling).
        session: Injected fetcher — tests always pass one.
        include_ramps: Include ramp-detector rows.
        district: MnDOT district.
        timezone: IANA zone stamping the timestamps' UTC offset.

    Returns:
        Tidy detector frame (``calibration.loaders.detector_csv``
        :data:`~calibration.loaders.detector_csv.DETECTOR_COLUMNS`) covering
        every whole-day window of each date, sorted by timestamp then
        station. Windows failing the validity rule are NaN.

    Raises:
        KeyError: Unknown corridor or station id.
        ValueError: ``window_s`` is not a multiple of 30 s, or ``dates`` is
            empty.
    """
    per_window = samples_per_window(window_s)
    if not dates:
        raise ValueError("station_frame needs at least one date")
    corr = config.corridor(corridor)
    wanted = {s.id for s in (corr.station(sid) for sid in stations)}
    picked = [s for s in corr.stations if s.id in wanted]
    ramps: list[RampNode] = []
    if include_ramps and picked:
        xs = [s.x_m for s in picked]
        ramps = [r for r in corr.ramps_between(min(xs), max(xs)) if r.flow_detectors]
    zone = _zone(timezone)
    rows: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:

        def series(detectors: Sequence[str], date: str) -> dict[str, dict[str, list[float | None]]]:
            """Fetch one node-day: ``{endpoint: {detector: samples}}``."""
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
                for name in detectors
            }
            out: dict[str, dict[str, list[float | None]]] = {e: {} for e in MAYFLY_ENDPOINTS}
            for (endpoint, name), job in jobs.items():
                got = job.result()
                if got:
                    out[endpoint][name] = got
            return out

        lane_of = {d.name: d.lane for n in corr.nodes for d in n.detectors}
        scaled_station_days: list[dict[str, Any]] = []
        for date in dates:
            midnight = parse_date(date)
            if zone is not None:
                midnight = midnight.replace(tzinfo=zone)  # type: ignore[arg-type]
            for kind, key, lanes, x_m, detectors in _targets(picked, ramps):
                fetched = series(detectors, date)
                # A detector that returned nothing for the whole day (HTTP 404: not
                # installed, not communicating, or an inventory placeholder such as
                # MnDOT's "T…" temporary loops) is not counted as an expected
                # detector; one that reported with gaps still counts against the
                # validity rule. The lanes those detectors cover are then compared
                # with the inventory's lane count: a lane with no reporting detector
                # at all is a dead lane, and the station total is scaled up by
                # lanes / lanes_reporting and listed in ``attrs["scaled_station_days"]``
                # rather than silently under-counted.
                n_expected = len(fetched["counts"]) or len(detectors)
                day = aggregate_day(
                    fetched["counts"],
                    fetched["occupancy"],
                    fetched["speed"],
                    window_s=window_s,
                    n_detectors=n_expected,
                )
                if kind == "mainline" and fetched["counts"]:
                    reporting = {lane_of.get(name, name) for name in fetched["counts"]}
                    if 0 < len(reporting) < lanes:
                        factor = lanes / len(reporting)
                        day["flow_veh_h"] = day["flow_veh_h"] * factor
                        scaled_station_days.append(
                            {
                                "station": key,
                                "date": date,
                                "lanes": lanes,
                                "lanes_reporting": len(reporting),
                                "factor": factor,
                            }
                        )
                for record in day.to_dict("records"):
                    index = int(record["window_index"])
                    rows.append(
                        {
                            "timestamp": midnight + timedelta(seconds=index * window_s),
                            "station": key,
                            "flow_veh_h": float(record["flow_veh_h"]),
                            "occupancy_pct": float(record["occupancy_pct"]),
                            "speed_ms": float(record["speed_ms"]),
                            "lanes": lanes,
                            "kind": kind,
                            "x_m": x_m,
                        }
                    )
    frame = pd.DataFrame(rows, columns=list(DETECTOR_COLUMNS))
    if frame.empty:
        return frame
    frame["lanes"] = frame["lanes"].astype(int)
    frame = frame.sort_values(["timestamp", "station"], kind="stable").reset_index(drop=True)
    frame.attrs["interval_s"] = float(window_s)
    frame.attrs["scaled_station_days"] = scaled_station_days
    frame.attrs["samples_per_window"] = per_window
    return frame


def _targets(
    stations: Iterable[Station], ramps: Iterable[RampNode]
) -> list[tuple[DetectorKind, str, int, float, tuple[str, ...]]]:
    """``(kind, id, lanes, x_m, detectors)`` for every node to fetch."""
    out: list[tuple[DetectorKind, str, int, float, tuple[str, ...]]] = [
        ("mainline", s.id, s.lanes, s.x_m, s.detectors) for s in stations
    ]
    out.extend((r.kind, r.node, r.lanes, r.x_m, r.flow_detectors) for r in ramps)
    return out


def stations_table(stations: Iterable[Station], ramps: Iterable[RampNode] = ()) -> pd.DataFrame:
    """The stations table of the observations contract (§2).

    Args:
        stations: Mainline stations.
        ramps: Ramp nodes to append as ``on_ramp``/``off_ramp`` rows.

    Returns:
        DataFrame with ``station, label, lat, lon, x_m, lanes, kind,
        speed_limit_ms, detectors`` (detector names joined by ``|``).
    """
    rows: list[dict[str, object]] = [
        {
            "station": s.id,
            "label": s.label,
            "lat": s.lat,
            "lon": s.lon,
            "x_m": s.x_m,
            "lanes": s.lanes,
            "kind": "mainline",
            "speed_limit_ms": s.speed_limit_ms,
            "detectors": "|".join(s.detectors),
        }
        for s in stations
    ]
    rows.extend(
        {
            "station": r.node,
            "label": r.label,
            "lat": r.lat,
            "lon": r.lon,
            "x_m": r.x_m,
            "lanes": r.lanes,
            "kind": r.kind,
            "speed_limit_ms": None,
            "detectors": "|".join(r.flow_detectors),
        }
        for r in ramps
    )
    return pd.DataFrame(
        rows,
        columns=[
            "station",
            "label",
            "lat",
            "lon",
            "x_m",
            "lanes",
            "kind",
            "speed_limit_ms",
            "detectors",
        ],
    )
