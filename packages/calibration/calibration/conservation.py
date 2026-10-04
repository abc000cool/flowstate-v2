"""Vehicle conservation between detector stations — the shared core of
:mod:`calibration.data_quality` (station-to-station mass balance) and
:mod:`calibration.ramp_estimation` (unmeasured ramp volumes).

Three things live here, each used by both modules:

**The detector grid** (:class:`DetectorGrid`, :func:`detector_grid`). The tidy
detector frame (``calibration.loaders.detector_csv``: ``timestamp, station,
flow_veh_h, occupancy_pct, speed_ms, lanes, kind, x_m`` and, for a per-lane
export, ``lane``) laid out as one ``dates × windows`` array per sensor and
quantity, on the source's local wall clock. A sensor is a station (station
rows) or one lane of a station (per-lane rows, id ``"<station>:<lane>"``).
Missing is NaN and stays NaN: nothing here fills a window in.
:func:`station_grid` sums a per-lane grid into station totals, and a window is
a station total only when **every** lane of the station reported it — a
partial sum would understate the station, and scaling it up would invent
traffic.

**The corridor layout** (:func:`corridor_layout`). Mainline stations ordered
by ``x_m`` define *segments* between consecutive stations; every ramp whose
position lies in ``(x_up, x_down]`` belongs to that segment (the same
half-open rule as :mod:`calibration.onboarding`). A ramp is *measured* when
the data hold at least one finite flow for it and the caller did not declare
it unmeasured; ramps known to exist without a detector come from a stations
table row with no data or from ``extra_ramps``.

**Lag-aligned period series** (:func:`align_segment`). Conservation between
two stations reads ``q_down(t + τ) − q_up(t) = Σ q_on − Σ q_off``: a vehicle
counted upstream at ``t`` is counted downstream a travel time ``τ`` later.
Every series of a segment is therefore evaluated at a common *reference
position* ``x_ref``: the series measured at ``x`` is read at
``t + (x − x_ref)·s(t)``, with ``s`` the segment's slowness (the mean of the
two stations' ``1/v``, i.e. the trapezoid travel-time rule) and linear
interpolation between window centres. The aligned windows are then averaged
into *periods* (default 15 min); a period is valid only when every window of
every series it needs is finite. The averaging is what tames the lag and the
count noise; it does not remove the change in the number of vehicles stored
between the two stations, which is small in free flow and not small in a
queue — periods in which a station's speed is below
:data:`CONGESTED_SPEED_MS` are flagged ``congested`` so a consumer can tell
them apart.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, Final, Literal, cast

import numpy as np
import pandas as pd

from calibration.loaders.detector_csv import (
    detector_interval_s,
    local_dates,
    local_seconds,
)
from calibration.observations import parse_clock
from flowstate_core.units import kmh_to_ms

RampKind = Literal["on_ramp", "off_ramp"]
Combination = Literal["linear", "quadrature"]

LANE_SEPARATOR: Final[str] = ":"
"""Joins station and lane into a per-lane sensor id (``"S1063:5137"``)."""

DEFAULT_PERIOD_S: Final[float] = 900.0
"""Aggregation period of the conservation terms [s] (15 min).

Reason: three 5-minute windows. The travel-time lag between neighbouring
freeway stations is about a minute in free flow (1–1.5 km at 25–30 m/s), so
an error of a few tens of seconds in the lag moves a 15-min mean by a few per
cent of the change across the period rather than of the period itself; the
period is still short enough to keep the shape of a peak."""

DEFAULT_COUNT_ERROR: Final[float] = 0.05
"""Relative count error assumed for every detector (±5 %).

Reason: freeway count detectors (loops, side-fire radar) are commonly
specified and field-checked to within a few per cent of volume; ±5 % is a
stated working assumption, not a measured property of any corridor's
detectors. It sets the width of every conservation interval and the mass
balance tolerance, and the leave-one-out validation in
:mod:`calibration.ramp_estimation` is how it is checked on a corridor whose
ramps are measured."""

DEFAULT_LAG_SPEED_MS: Final[float] = 25.0
"""Travel speed used for the lag when neither station of a segment reports a
speed in a window [m/s] (90 km/h). Reason: a free-flow speed; over a 1 km
segment it shifts the series by 40 s, small against a 15-min period. Every
window that needed it is counted on the result."""

CONGESTED_SPEED_MS: Final[float] = kmh_to_ms(40.0)
"""A period is ``congested`` when either station reports a speed below this
in any of its windows [m/s] — CLAUDE.md §7.2's default jam threshold
(40 km/h)."""

_ON_GRID_TOLERANCE: Final[float] = 1e-6
"""Window-index tolerance (in windows) for a row to sit on the grid."""


def normalize_date(text: str) -> str:
    """``YYYYMMDD`` or ``YYYY-MM-DD`` → ``YYYY-MM-DD``.

    Raises:
        ValueError: Neither form.
    """
    value = str(text).strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    parts = value.split("-")
    if len(parts) == 3 and [len(p) for p in parts] == [4, 2, 2] and all(p.isdigit() for p in parts):
        return value
    raise ValueError(f"date must be YYYYMMDD or YYYY-MM-DD, got {text!r}")


def clock_text(seconds: float) -> str:
    """Seconds since local midnight → ``"HH:MM"`` (``"HH:MM:SS"`` off the minute)."""
    total = round(float(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}" + (f":{secs:02d}" if secs else "")


def _clock_bound(text: str) -> float:
    """``"HH:MM"`` → seconds; ``"24:00"`` is accepted as the end of the day."""
    if str(text).strip() in ("24:00", "24:00:00"):
        return 86400.0
    return parse_clock(text)


# ---------------------------------------------------------------------------
# The detector grid
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SensorInfo:
    """What the frame says about one sensor.

    Attributes:
        sensor: Sensor id — the station id, or ``"<station>:<lane>"``.
        station: Station id.
        lane: Lane id for a per-lane sensor, else ``None``.
        kind: ``mainline``, ``on_ramp`` or ``off_ramp``.
        x_m: Corridor position [m], ``None`` when the frame gives none.
        lanes: Lanes the sensor's flow covers: 1 for a per-lane sensor, the
            frame's ``lanes`` for a station (0 = not stated).
    """

    sensor: str
    station: str
    lane: str | None
    kind: str
    x_m: float | None
    lanes: int

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "sensor": self.sensor,
            "station": self.station,
            "lane": self.lane,
            "kind": self.kind,
            "x_m": self.x_m,
            "lanes": int(self.lanes),
        }


@dataclass(frozen=True)
class DetectorGrid:
    """Detector series as ``dates × windows`` arrays (module docstring).

    Attributes:
        interval_s: Window length [s].
        start_s: Local wall-clock start of window 0 [s after midnight].
        n_windows: Windows per date.
        dates: Local dates (``YYYY-MM-DD``), sorted.
        sensors: Sensor id → :class:`SensorInfo`, sorted by id.
        flow_veh_h: Sensor id → flow [veh/h], shape ``(len(dates), n_windows)``.
        occupancy_pct: Sensor id → occupancy [percent], same shape.
        speed_ms: Sensor id → speed [m/s], same shape.
        per_lane: True when the sensors are lanes, not stations.
        silent: Sensors with no finite flow anywhere in the data as delivered
            (kept through masking, so a detector set aside by a quality check
            is never mistaken for one that does not exist).
    """

    interval_s: float
    start_s: float
    n_windows: int
    dates: tuple[str, ...]
    sensors: dict[str, SensorInfo]
    flow_veh_h: dict[str, np.ndarray]
    occupancy_pct: dict[str, np.ndarray]
    speed_ms: dict[str, np.ndarray]
    per_lane: bool = False
    silent: frozenset[str] = frozenset()

    def window_start_s(self, index: int) -> float:
        """Local wall clock at the start of window ``index`` [s]."""
        return self.start_s + index * self.interval_s

    def window_label(self, index: int) -> str:
        """``"HH:MM"`` of window ``index``'s start."""
        return clock_text(self.window_start_s(index))

    def stations(self) -> dict[str, list[str]]:
        """Station id → its sensor ids (one per lane, or the station itself)."""
        out: dict[str, list[str]] = {}
        for sid, info in self.sensors.items():
            out.setdefault(info.station, []).append(sid)
        return out

    def replace_values(
        self,
        flow_veh_h: Mapping[str, np.ndarray],
        occupancy_pct: Mapping[str, np.ndarray],
        speed_ms: Mapping[str, np.ndarray],
    ) -> DetectorGrid:
        """The same grid with new value arrays (e.g. after masking)."""
        return DetectorGrid(
            interval_s=self.interval_s,
            start_s=self.start_s,
            n_windows=self.n_windows,
            dates=self.dates,
            sensors=dict(self.sensors),
            flow_veh_h=dict(flow_veh_h),
            occupancy_pct=dict(occupancy_pct),
            speed_ms=dict(speed_ms),
            per_lane=self.per_lane,
            silent=self.silent,
        )


def sensor_id(station: str, lane: str | None) -> str:
    """The grid's sensor id for a station or one of its lanes."""
    return station if lane is None else f"{station}{LANE_SEPARATOR}{lane}"


def _first_number(values: pd.Series) -> float | None:
    finite = pd.to_numeric(values, errors="coerce").dropna()
    return float(finite.iloc[0]) if len(finite) else None


def detector_grid(
    frame: pd.DataFrame,
    *,
    dates: Sequence[str] | None = None,
    start_local: str | None = None,
    end_local: str | None = None,
) -> DetectorGrid:
    """Lay a tidy detector frame out as a :class:`DetectorGrid`.

    Rows are placed by their local wall clock, as
    :meth:`calibration.observations.Observations.from_frame` places them, so
    every date shares one time-of-day grid. Without ``start_local`` the grid
    starts at the earliest local time in the (filtered) frame and, without
    ``end_local``, ends one window after the latest.

    Args:
        frame: Tidy detector frame; a ``lane`` column with values makes the
            grid per-lane.
        dates: Local dates to keep (``YYYYMMDD`` or ``YYYY-MM-DD``); all when
            None.
        start_local: First local clock time kept (``"HH:MM"``).
        end_local: Local clock time the span ends before (``"HH:MM"``;
            ``"24:00"`` allowed).

    Returns:
        The grid.

    Raises:
        ValueError: A required column is missing, no row survives the
            filters, a row inside the span is off the window grid, two rows
            give the same sensor, date and window (a daylight-saving fold
            does this — drop the date), or the frame has no interval.
    """
    required = ("timestamp", "station", "flow_veh_h")
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(f"detector_grid: frame is missing column(s) {missing}")
    if frame.empty:
        raise ValueError("detector_grid: the frame holds no rows")
    interval = float(frame.attrs.get("interval_s") or detector_interval_s(frame))
    if interval <= 0.0:
        raise ValueError(
            "detector_grid: the frame has a single timestamp, so no window length; "
            "a grid needs at least two"
        )
    secs = local_seconds(frame).to_numpy(dtype=float)
    days = local_dates(frame).to_numpy(dtype=object)
    keep = np.ones(len(frame), dtype=bool)
    if dates:
        wanted = {normalize_date(d) for d in dates}
        keep &= np.isin(days, sorted(wanted))
    lo = _clock_bound(start_local) if start_local else None
    hi = _clock_bound(end_local) if end_local else None
    if lo is not None:
        keep &= secs >= lo
    if hi is not None:
        keep &= secs < hi
    if not keep.any():
        raise ValueError("detector_grid: no row lies inside the requested dates and span")
    base = lo if lo is not None else float(secs[keep].min())
    top = hi if hi is not None else float(secs[keep].max()) + interval
    n_windows = round((top - base) / interval)
    if n_windows < 1:
        raise ValueError(f"detector_grid: the span [{base:g}, {top:g}) s holds no whole window")
    offset = (secs - base) / interval
    index = np.round(offset).astype(np.int64)
    off_grid = keep & (np.abs(offset - index) > _ON_GRID_TOLERANCE)
    if off_grid.any():
        first = int(np.flatnonzero(off_grid)[0])
        raise ValueError(
            f"detector_grid: a row at local {clock_text(secs[first])} does not start on the "
            f"{interval:g} s window grid that begins at {clock_text(base)}; choose a start on "
            f"the grid"
        )
    keep &= (index >= 0) & (index < n_windows)

    per_lane = "lane" in frame.columns and bool(frame["lane"].notna().any())
    rows = frame.loc[keep].copy()
    rows["_date"] = days[keep]
    rows["_k"] = index[keep]
    stations = rows["station"].astype(str)
    if per_lane:
        lanes = rows["lane"].astype(str)
        rows["_sensor"] = stations + LANE_SEPARATOR + lanes
    else:
        rows["_sensor"] = stations
    dup = rows.duplicated(subset=["_sensor", "_date", "_k"])
    if dup.any():
        twice = rows.loc[dup].iloc[0]
        raise ValueError(
            f"detector_grid: sensor {twice['_sensor']!r} has two rows for {twice['_date']} at "
            f"{clock_text(base + int(twice['_k']) * interval)} (a daylight-saving fold repeats "
            f"an hour; drop that date)"
        )
    date_list = tuple(sorted(set(rows["_date"])))
    date_index = {d: i for i, d in enumerate(date_list)}
    shape = (len(date_list), n_windows)
    sensors: dict[str, SensorInfo] = {}
    flows: dict[str, np.ndarray] = {}
    occs: dict[str, np.ndarray] = {}
    speeds: dict[str, np.ndarray] = {}
    for sid, group in sorted(rows.groupby("_sensor", sort=True), key=lambda kv: str(kv[0])):
        sid = str(sid)
        di = group["_date"].map(date_index).to_numpy(dtype=np.int64)
        ki = group["_k"].to_numpy(dtype=np.int64)
        for name, target in (
            ("flow_veh_h", flows),
            ("occupancy_pct", occs),
            ("speed_ms", speeds),
        ):
            arr = np.full(shape, np.nan)
            if name in group.columns:
                arr[di, ki] = pd.to_numeric(group[name], errors="coerce").to_numpy(dtype=float)
            target[sid] = arr
        kinds = group["kind"].dropna() if "kind" in group.columns else pd.Series(dtype=object)
        lanes_value = _first_number(group["lanes"]) if "lanes" in group.columns else None
        x_value = _first_number(group["x_m"]) if "x_m" in group.columns else None
        sensors[sid] = SensorInfo(
            sensor=sid,
            station=str(group["station"].iloc[0]),
            lane=str(group["lane"].iloc[0]) if per_lane else None,
            kind=str(kinds.iloc[0]) if len(kinds) else "mainline",
            x_m=x_value,
            lanes=1 if per_lane else int(lanes_value or 0),
        )
    return DetectorGrid(
        interval_s=interval,
        start_s=float(base),
        n_windows=int(n_windows),
        dates=date_list,
        sensors=sensors,
        flow_veh_h=flows,
        occupancy_pct=occs,
        speed_ms=speeds,
        per_lane=per_lane,
        silent=frozenset(sid for sid, arr in flows.items() if not np.isfinite(arr).any()),
    )


def silent_lane_note(grid: DetectorGrid) -> str | None:
    """A sentence naming the lane sensors :func:`station_grid` leaves out, if any."""
    if not grid.per_lane or not grid.silent:
        return None
    return (
        "lane sensors that reported no flow anywhere in the data are not counted as lanes of "
        "their station (a detector that never reports is treated as not installed, as "
        "calibration.loaders.mndot does): " + ", ".join(sorted(grid.silent))
    )


def station_grid(grid: DetectorGrid) -> DetectorGrid:
    """Station totals of a per-lane grid (a station grid is returned as is).

    Flow is the sum over the station's lanes and occupancy their mean, both
    only in windows where **every** lane is finite; speed is the
    flow-weighted mean of the lanes that report one with positive flow
    (NaN when none does). Lane sensors in ``grid.silent`` (no flow anywhere
    in the data as delivered — a placeholder or uninstalled loop) are not
    lanes of their station (:func:`silent_lane_note`); a lane whose readings
    a quality check set aside still is, and its station has no total where
    it is missing.

    Args:
        grid: Per-lane or station grid.

    Returns:
        A station grid; each station's ``lanes`` is its number of lane
        sensors counted.
    """
    if not grid.per_lane:
        return grid
    sensors: dict[str, SensorInfo] = {}
    flows: dict[str, np.ndarray] = {}
    occs: dict[str, np.ndarray] = {}
    speeds: dict[str, np.ndarray] = {}
    for station, every in sorted(grid.stations().items()):
        members = [m for m in every if m not in grid.silent] or every
        q = np.stack([grid.flow_veh_h[m] for m in members])
        o = np.stack([grid.occupancy_pct[m] for m in members])
        v = np.stack([grid.speed_ms[m] for m in members])
        flow = np.where(np.isfinite(q).all(axis=0), q.sum(axis=0), np.nan)
        occ = np.where(np.isfinite(o).all(axis=0), o.mean(axis=0), np.nan)
        weight = np.where(np.isfinite(v) & np.isfinite(q) & (q > 0.0), q, 0.0)
        total = weight.sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            speed = np.where(total > 0.0, (np.nan_to_num(v) * weight).sum(axis=0) / total, np.nan)
        first = grid.sensors[members[0]]
        sensors[station] = SensorInfo(
            sensor=station,
            station=station,
            lane=None,
            kind=first.kind,
            x_m=first.x_m,
            lanes=len(members),
        )
        flows[station] = flow
        occs[station] = occ
        speeds[station] = speed
    return DetectorGrid(
        interval_s=grid.interval_s,
        start_s=grid.start_s,
        n_windows=grid.n_windows,
        dates=grid.dates,
        sensors=sensors,
        flow_veh_h=flows,
        occupancy_pct=occs,
        speed_ms=speeds,
        per_lane=False,
        silent=frozenset(sid for sid, arr in flows.items() if not np.isfinite(arr).any()),
    )


# ---------------------------------------------------------------------------
# The corridor layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RampSpec:
    """A ramp of the corridor.

    Attributes:
        id: Ramp id — its sensor id when measured.
        kind: ``on_ramp`` or ``off_ramp``.
        x_m: Corridor position [m].
        measured: True when the data hold a flow for it and the caller did
            not declare it unmeasured.
        label: Human-readable location.
    """

    id: str
    kind: RampKind
    x_m: float
    measured: bool
    label: str = ""

    @property
    def sign(self) -> int:
        """+1 for an on-ramp (adds flow downstream), −1 for an off-ramp."""
        return 1 if self.kind == "on_ramp" else -1

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "id": self.id,
            "kind": self.kind,
            "x_m": self.x_m,
            "measured": self.measured,
            "label": self.label,
        }


def parse_ramp(text: str) -> RampSpec:
    """``"ID:on:X_M"`` / ``"ID:off:X_M"`` → an unmeasured :class:`RampSpec`.

    The command-line spelling of a ramp that exists but has no detector.

    Raises:
        ValueError: Not three ``:``-separated fields, an unknown kind, or a
            position that is not a number.
    """
    parts = str(text).rsplit(":", 2)
    if len(parts) != 3 or not parts[0]:
        raise ValueError(f"a ramp is written ID:on|off:X_M, got {text!r}")
    ident, kind_text, x_text = parts
    kind = _ramp_kind(kind_text)
    try:
        x_m = float(x_text)
    except ValueError as exc:
        raise ValueError(f"ramp {ident!r}: position {x_text!r} is not a number") from exc
    return RampSpec(id=ident, kind=kind, x_m=x_m, measured=False)


def _ramp_kind(text: str) -> RampKind:
    value = str(text).strip().lower()
    if value in ("on", "on_ramp"):
        return "on_ramp"
    if value in ("off", "off_ramp"):
        return "off_ramp"
    raise ValueError(f"ramp kind must be on/on_ramp or off/off_ramp, got {text!r}")


@dataclass(frozen=True)
class Segment:
    """The corridor between two consecutive mainline stations.

    Attributes:
        upstream: Upstream station id.
        downstream: Downstream station id.
        x_up_m: Upstream station position [m].
        x_down_m: Downstream station position [m].
        ramps: Ramps with ``x_up < x ≤ x_down``, in ``x`` order.
    """

    upstream: str
    downstream: str
    x_up_m: float
    x_down_m: float
    ramps: tuple[RampSpec, ...] = ()

    @property
    def name(self) -> str:
        """``"<upstream>→<downstream>"``."""
        return f"{self.upstream}→{self.downstream}"

    @property
    def length_m(self) -> float:
        """Station spacing [m]."""
        return self.x_down_m - self.x_up_m

    def measured(self) -> tuple[RampSpec, ...]:
        """The measured ramps."""
        return tuple(r for r in self.ramps if r.measured)

    def unmeasured(self) -> tuple[RampSpec, ...]:
        """The ramps without a usable detector."""
        return tuple(r for r in self.ramps if not r.measured)

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "upstream": self.upstream,
            "downstream": self.downstream,
            "x_up_m": self.x_up_m,
            "x_down_m": self.x_down_m,
            "length_m": self.length_m,
            "ramps": [r.to_dict() for r in self.ramps],
        }


@dataclass(frozen=True)
class CorridorLayout:
    """Segments and what could not be placed in one.

    Attributes:
        segments: Consecutive-station segments, upstream first.
        outside_ramps: Ramps outside ``(x_first, x_last]`` (not in any
            segment; their flow is inside the nearest station count).
        stations_without_data: Mainline stations listed in the stations
            table with no finite flow; segments span across them.
        stations_without_position: Mainline sensors with no ``x_m``; they
            cannot be placed and are left out.
    """

    segments: tuple[Segment, ...]
    outside_ramps: tuple[RampSpec, ...] = ()
    stations_without_data: tuple[str, ...] = ()
    stations_without_position: tuple[str, ...] = ()

    def ramp(self, ramp_id: str) -> tuple[Segment, RampSpec]:
        """The segment holding ``ramp_id`` and its spec.

        Raises:
            KeyError: No segment holds such a ramp.
        """
        for seg in self.segments:
            for r in seg.ramps:
                if r.id == ramp_id:
                    return seg, r
        raise KeyError(f"no segment holds a ramp {ramp_id!r}")

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "segments": [s.to_dict() for s in self.segments],
            "outside_ramps": [r.to_dict() for r in self.outside_ramps],
            "stations_without_data": list(self.stations_without_data),
            "stations_without_position": list(self.stations_without_position),
        }


def _station_rows(
    stations: pd.DataFrame | Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """A stations table → normalised records (``station, kind, x_m, label``)."""
    if stations is None:
        return []
    records: Iterable[Mapping[str, Any]] = (
        cast(list[dict[str, Any]], stations.to_dict("records"))
        if isinstance(stations, pd.DataFrame)
        else stations
    )
    out: list[dict[str, Any]] = []
    for raw in records:
        ident = raw.get("station", raw.get("id"))
        if ident is None or (isinstance(ident, float) and math.isnan(ident)) or ident == "":
            raise ValueError(f"stations table row without a station id: keys {sorted(raw)}")
        x = raw.get("x_m")
        x_m = None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)
        label = raw.get("label")
        out.append(
            {
                "station": str(ident),
                "kind": str(raw.get("kind") or "mainline"),
                "x_m": x_m,
                "label": "" if label is None or (isinstance(label, float)) else str(label),
            }
        )
    return out


def corridor_layout(
    grid: DetectorGrid,
    *,
    stations: pd.DataFrame | Sequence[Mapping[str, Any]] | None = None,
    extra_ramps: Iterable[RampSpec] = (),
    unmeasured: Iterable[str] = (),
) -> CorridorLayout:
    """Segments between consecutive mainline stations, with their ramps.

    Positions come from the stations table when it gives one, else from the
    frame's ``x_m``. A mainline station needs a position and at least one
    finite flow to bound a segment; one without data is listed and the
    segment spans across it.

    Args:
        grid: A **station** grid (:func:`station_grid` first for per-lane
            data).
        stations: Optional stations table (``station, kind, x_m[, label]``,
            e.g. ``stations.csv`` of a corridor directory). Ramp rows with no
            data are unmeasured ramps.
        extra_ramps: Ramps known to exist without a detector.
        unmeasured: Ids of ramps to treat as unmeasured even though the data
            hold a flow for them.

    Returns:
        The :class:`CorridorLayout`.

    Raises:
        ValueError: A per-lane grid, an ``unmeasured`` id that is not a ramp,
            fewer than two placeable mainline stations, or two mainline
            stations at the same position.
    """
    if grid.per_lane:
        raise ValueError("corridor_layout needs a station grid; call station_grid() first")
    table = _station_rows(stations)
    position: dict[str, float] = {}
    kind: dict[str, str] = {}
    label: dict[str, str] = {}
    for sid, info in grid.sensors.items():
        kind[sid] = info.kind
        if info.x_m is not None:
            position[sid] = float(info.x_m)
    for row in table:
        sid = row["station"]
        kind[sid] = row["kind"]
        label[sid] = row["label"]
        if row["x_m"] is not None:
            position[sid] = float(row["x_m"])

    def has_data(sid: str) -> bool:
        return sid in grid.flow_veh_h and bool(np.isfinite(grid.flow_veh_h[sid]).any())

    forced = {str(u) for u in unmeasured}
    ramp_ids = {sid for sid, k in kind.items() if k in ("on_ramp", "off_ramp")}
    extras = list(extra_ramps)
    unknown = sorted(forced - ramp_ids - {r.id for r in extras})
    if unknown:
        raise ValueError(f"unmeasured ids {unknown} are not ramps of this corridor")

    without_position = sorted(
        sid for sid, k in kind.items() if k == "mainline" and sid not in position
    )
    without_data = sorted(
        sid for sid, k in kind.items() if k == "mainline" and sid in position and not has_data(sid)
    )
    mainline = sorted(
        (
            (position[sid], sid)
            for sid, k in kind.items()
            if k == "mainline" and sid in position and has_data(sid)
        ),
    )
    if len(mainline) < 2:
        raise ValueError(
            "corridor_layout: fewer than two mainline stations with a position and data — "
            "a segment needs two"
        )
    for (xa, a), (xb, b) in pairwise(mainline):
        if not xb > xa:
            raise ValueError(f"mainline stations {a!r} and {b!r} share the position {xa:g} m")

    ramps: list[RampSpec] = []
    for sid in sorted(ramp_ids):
        if sid not in position:
            without_position.append(sid)
            continue
        ramps.append(
            RampSpec(
                id=sid,
                kind=_ramp_kind(kind[sid]),
                x_m=position[sid],
                measured=has_data(sid) and sid not in forced,
                label=label.get(sid, ""),
            )
        )
    known = {r.id for r in ramps}
    for extra in extras:
        if extra.id in known:
            raise ValueError(f"extra ramp {extra.id!r} is already a ramp of the data or table")
        ramps.append(
            RampSpec(id=extra.id, kind=extra.kind, x_m=extra.x_m, measured=False, label=extra.label)
        )
    segments: list[Segment] = []
    placed: set[str] = set()
    for (xa, a), (xb, b) in pairwise(mainline):
        inside = sorted((r for r in ramps if xa < r.x_m <= xb), key=lambda r: (r.x_m, r.id))
        placed |= {r.id for r in inside}
        segments.append(Segment(a, b, xa, xb, tuple(inside)))
    outside = tuple(sorted((r for r in ramps if r.id not in placed), key=lambda r: r.x_m))
    return CorridorLayout(
        segments=tuple(segments),
        outside_ramps=outside,
        stations_without_data=tuple(without_data),
        stations_without_position=tuple(sorted(without_position)),
    )


# ---------------------------------------------------------------------------
# Lag-aligned period series
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlignedSegment:
    """One segment's series on a common reference position, per period.

    Every array has shape ``(len(dates), n_periods)`` and is in veh/h.

    Attributes:
        segment: The segment.
        x_ref_m: Reference position the series are aligned to [m].
        dates: Local dates.
        period_s: Period length [s].
        period_start_s: Local clock start of each period [s after midnight].
        up: Upstream station flow.
        down: Downstream station flow.
        ramps: Measured ramp id → its flow.
        congested: True where either station's speed fell below the
            congestion threshold in any window of the period.
        n_lag_fallback: Windows whose lag used the fallback speed.
        n_lag_clamped: Windows whose lag hit ``max_lag_s``.
        n_windows_dropped: Trailing windows that did not fill a period.
    """

    segment: Segment
    x_ref_m: float
    dates: tuple[str, ...]
    period_s: float
    period_start_s: tuple[float, ...]
    up: np.ndarray
    down: np.ndarray
    ramps: dict[str, np.ndarray] = field(default_factory=dict)
    congested: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=bool))
    n_lag_fallback: int = 0
    n_lag_clamped: int = 0
    n_windows_dropped: int = 0

    @property
    def n_periods(self) -> int:
        """Periods per date."""
        return len(self.period_start_s)


def periods_per_window(grid: DetectorGrid, period_s: float) -> int:
    """Windows per period.

    Raises:
        ValueError: ``period_s`` is not a positive whole multiple of the
            grid's window.
    """
    ratio = period_s / grid.interval_s
    if period_s <= 0.0 or abs(ratio - round(ratio)) > 1e-9:
        raise ValueError(
            f"period_s ({period_s:g}) must be a positive whole multiple of the "
            f"{grid.interval_s:g} s window"
        )
    return round(ratio)


def shift_series(values: np.ndarray, shift_windows: np.ndarray) -> np.ndarray:
    """``values`` read ``shift_windows`` later, by linear interpolation.

    Element ``k`` of the result is ``values`` at fractional index
    ``k + shift_windows[k]``; NaN where that falls outside the series or
    either neighbour is NaN.

    Args:
        values: One date's window series.
        shift_windows: Per-window shift in windows (negative = earlier).

    Returns:
        The shifted series.
    """
    n = values.shape[-1]
    pos = np.arange(n, dtype=float) + shift_windows
    base = np.floor(pos)
    i0 = base.astype(np.int64)
    w = pos - base
    out = np.full(n, np.nan)
    inside = (i0 >= 0) & (i0 <= n - 1)
    exact = inside & (w < 1e-9)
    out[exact] = values[i0[exact]]
    between = inside & ~exact & (i0 + 1 <= n - 1)
    lo = i0[between]
    out[between] = (1.0 - w[between]) * values[lo] + w[between] * values[lo + 1]
    return out


def period_means(series: np.ndarray, per_period: int) -> np.ndarray:
    """Mean of each whole period; NaN unless every window is finite.

    Args:
        series: Window values, shape ``(..., n_windows)``.
        per_period: Windows per period.

    Returns:
        Shape ``(..., n_windows // per_period)``.
    """
    n_periods = series.shape[-1] // per_period
    block = series[..., : n_periods * per_period].reshape(
        (*series.shape[:-1], n_periods, per_period)
    )
    complete = np.isfinite(block).all(axis=-1)
    return np.where(complete, np.where(complete[..., None], block, 0.0).mean(axis=-1), np.nan)


def _slowness(
    v_up: np.ndarray, v_down: np.ndarray, default_speed_ms: float
) -> tuple[np.ndarray, int]:
    """Per-window mean ``1/v`` of the two stations [s/m], and the fallback count."""
    stack = np.stack([v_up, v_down])
    usable = np.isfinite(stack) & (stack > 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        inverse = np.where(usable, 1.0 / np.where(usable, stack, 1.0), 0.0)
    count = usable.sum(axis=0)
    mean = np.where(count > 0, inverse.sum(axis=0) / np.maximum(count, 1), 1.0 / default_speed_ms)
    return mean, int((count == 0).sum())


def align_segment(
    grid: DetectorGrid,
    segment: Segment,
    *,
    x_ref_m: float | None = None,
    period_s: float = DEFAULT_PERIOD_S,
    default_speed_ms: float = DEFAULT_LAG_SPEED_MS,
    max_lag_s: float | None = None,
    congested_speed_ms: float = CONGESTED_SPEED_MS,
) -> AlignedSegment:
    """A segment's station and measured-ramp flows, lag-aligned, per period.

    The method is in the module docstring. Unmeasured ramps have no series
    and do not appear; their flow is what the conservation residual holds.

    Args:
        grid: Station grid holding the segment's stations and measured ramps.
        segment: The segment.
        x_ref_m: Reference position [m]; the upstream station when None.
        period_s: Period length [s]; a whole multiple of the window.
        default_speed_ms: Lag speed when neither station reports one [m/s].
        max_lag_s: Largest shift applied to any series [s]; one period when
            None (a lag beyond the averaging period means the period is
            dominated by storage, which no shift repairs).
        congested_speed_ms: Speed below which a period is ``congested``.

    Returns:
        The :class:`AlignedSegment`.

    Raises:
        KeyError: A station or measured ramp of the segment is not in the
            grid.
        ValueError: A bad ``period_s`` or ``default_speed_ms``.
    """
    if default_speed_ms <= 0.0:
        raise ValueError(f"default_speed_ms must be > 0, got {default_speed_ms}")
    per_period = periods_per_window(grid, period_s)
    x_ref = segment.x_up_m if x_ref_m is None else float(x_ref_m)
    cap = float(period_s if max_lag_s is None else max_lag_s)
    for sid in (segment.upstream, segment.downstream, *(r.id for r in segment.measured())):
        if sid not in grid.flow_veh_h:
            raise KeyError(f"align_segment: the grid holds no series for {sid!r}")
    positions = {segment.upstream: segment.x_up_m, segment.downstream: segment.x_down_m}
    positions.update({r.id: r.x_m for r in segment.measured()})
    n_dates = len(grid.dates)
    n_periods = grid.n_windows // per_period
    aligned: dict[str, np.ndarray] = {
        sid: np.full((n_dates, n_periods), np.nan) for sid in positions
    }
    congested = np.zeros((n_dates, n_periods), dtype=bool)
    fallback = 0
    clamped = 0
    v_up_all = grid.speed_ms[segment.upstream]
    v_down_all = grid.speed_ms[segment.downstream]
    for d in range(n_dates):
        slowness, n_fb = _slowness(v_up_all[d], v_down_all[d], default_speed_ms)
        fallback += n_fb
        for sid, x in positions.items():
            shift_s = (x - x_ref) * slowness
            over = np.abs(shift_s) > cap
            clamped += int(over.sum())
            shift_s = np.clip(shift_s, -cap, cap)
            series = shift_series(grid.flow_veh_h[sid][d], shift_s / grid.interval_s)
            aligned[sid][d] = period_means(series, per_period)
        slow = np.zeros(grid.n_windows, dtype=bool)
        for v in (v_up_all[d], v_down_all[d]):
            slow |= np.isfinite(v) & (v < congested_speed_ms)
        congested[d] = slow[: n_periods * per_period].reshape(n_periods, per_period).any(axis=1)
    return AlignedSegment(
        segment=segment,
        x_ref_m=x_ref,
        dates=grid.dates,
        period_s=float(period_s),
        period_start_s=tuple(grid.start_s + j * period_s for j in range(n_periods)),
        up=aligned[segment.upstream],
        down=aligned[segment.downstream],
        ramps={r.id: aligned[r.id] for r in segment.measured()},
        congested=congested,
        n_lag_fallback=fallback,
        n_lag_clamped=clamped,
        n_windows_dropped=grid.n_windows - n_periods * per_period,
    )


def count_tolerance(
    terms: Sequence[np.ndarray], count_error: float, combination: Combination
) -> np.ndarray:
    """Error bound of a signed sum of measured flows, from a relative count error.

    Each term carries an error of ``count_error`` times its magnitude.
    ``linear`` adds them (a bound that holds whatever the correlation between
    detectors); ``quadrature`` takes the root sum of squares (independent
    errors).

    Args:
        terms: The measured flows entering the sum (same shape).
        count_error: Relative error per detector (e.g. 0.05).
        combination: ``linear`` or ``quadrature``.

    Returns:
        The half-width, elementwise.

    Raises:
        ValueError: A negative ``count_error`` or an unknown combination.
    """
    if count_error < 0.0:
        raise ValueError(f"count_error must be >= 0, got {count_error}")
    mags = [np.abs(t) for t in terms]
    if combination == "linear":
        return count_error * np.sum(mags, axis=0)
    if combination == "quadrature":
        return count_error * np.sqrt(np.sum([m**2 for m in mags], axis=0))
    raise ValueError(f"combination must be 'linear' or 'quadrature', got {combination!r}")
