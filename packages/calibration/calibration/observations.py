"""Observed detector profiles for a corridor — the ``flowstate.observations/1``
artifact (docs/CONTRACTS.md, "Detector observations").

An :class:`Observations` artifact is what a corridor's real measurements look
like once they are on the simulation's clock: a fixed grid of ``n_windows``
analysis windows starting at ``t0_local``, one series per station, aggregated
over the requested dates into a typical weekday profile with its spread. It is
the observed side of every comparison the validation report makes — GEH on
hourly link flows, RMSPE on segment speeds — and the input the demand fitter
reads (:mod:`calibration.demand`).

Two rules the whole module exists to keep:

* **Simulation time zero is ``t0_local``.** Window ``k`` covers
  ``[t0 + k·window_s, t0 + (k+1)·window_s)`` in local wall-clock time, and its
  simulation start is ``k·window_s``. A consumer that discards a warm-up
  compares only the windows fully inside the analysed span.
* **Missing is NaN, never a number.** A window a detector did not measure well
  enough is NaN in the series and is counted against the station's
  ``fraction_valid``; it is skipped by every consumer, never imputed, never
  interpolated. On disk NaN is JSON ``null``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from calibration.loaders.detector_csv import (
    DETECTOR_KINDS,
    detector_interval_s,
    local_dates,
    local_seconds,
)

OBSERVATIONS_SCHEMA: Final[str] = "flowstate.observations/1"
"""Schema tag written into every artifact."""

DEFAULT_AGGREGATION: Final[str] = "mean over dates per window (weekday typical profile)"
"""How the per-window value is formed from the fetched dates."""

_S_PER_HOUR: Final[float] = 3600.0


def _nan_to_none(values: Sequence[float]) -> list[float | None]:
    """NaN → JSON ``null`` (JSON has no NaN; the contract says null)."""
    return [
        None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v) for v in values
    ]


def _none_to_nan(values: Iterable[float | None] | None) -> list[float]:
    """JSON ``null`` → NaN (the in-memory spelling of "not observed")."""
    return [float("nan") if v is None else float(v) for v in (values or ())]


@dataclass(frozen=True)
class ObservedStation:
    """One station of an observations artifact.

    Attributes:
        id: Stable station id — the key of every series dict.
        label: Human-readable location.
        x_m: Corridor position [m], None when no stations table gave one.
        lanes: Mainline lanes (0 = not stated).
        kind: ``mainline``, ``on_ramp`` or ``off_ramp``.
        lat: Latitude [deg], None when unknown.
        lon: Longitude [deg], None when unknown.
        speed_limit_ms: Posted limit [m/s], None when unknown.
    """

    id: str
    label: str = ""
    x_m: float | None = None
    lanes: int = 0
    kind: str = "mainline"
    lat: float | None = None
    lon: float | None = None
    speed_limit_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON form (the artifact's ``stations`` entries)."""
        return {
            "id": self.id,
            "label": self.label,
            "x_m": self.x_m,
            "lanes": int(self.lanes),
            "kind": self.kind,
            "lat": self.lat,
            "lon": self.lon,
            "speed_limit_ms": self.speed_limit_ms,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> ObservedStation:
        """Build from a stations-table row or an artifact entry.

        Accepts both spellings of the id (``id`` or ``station``) so a
        stations table written by :func:`calibration.loaders.mndot.stations_table`
        and an artifact's own entry both load.

        Raises:
            ValueError: No id, or an unknown ``kind``.
        """
        ident = raw.get("id", raw.get("station"))
        if ident in (None, ""):
            raise ValueError(f"station entry has no 'id'/'station' key: keys {sorted(raw)}")
        kind = str(raw.get("kind") or "mainline")
        if kind not in DETECTOR_KINDS:
            raise ValueError(
                f"station {ident!r}: unknown kind {kind!r}, expected {list(DETECTOR_KINDS)}"
            )

        def number(key: str) -> float | None:
            value = raw.get(key)
            if value is None or (isinstance(value, float) and math.isnan(value)):
                return None
            return float(value)

        lanes = raw.get("lanes")
        return cls(
            id=str(ident),
            label=str(raw.get("label") or ""),
            x_m=number("x_m"),
            lanes=0
            if lanes is None or (isinstance(lanes, float) and math.isnan(lanes))
            else int(lanes),
            kind=kind,
            lat=number("lat"),
            lon=number("lon"),
            speed_limit_ms=number("speed_limit_ms"),
        )


@dataclass(frozen=True)
class Observations:
    """Observed per-window profiles for one corridor (module docstring).

    Attributes:
        corridor: Corridor name the artifact describes.
        source: Provenance (provider, district, dates, url, fetched_at).
        window_s: Analysis window [s].
        t0_local: Local wall-clock time of simulation t=0 (``"HH:MM"``).
        duration_s: Analysed span [s]; ``n_windows · window_s``.
        stations: Station metadata, in artifact order.
        flows_veh_h: Station id → per-window flow [veh/h] (NaN = not observed).
        speeds_ms: Station id → per-window mean speed [m/s].
        occupancy_pct: Station id → per-window mean occupancy [percent].
        spread: ``{"flows_veh_h_sd": {...}, "speeds_ms_sd": {...}}`` — the
            sample standard deviation across dates, NaN below two dates.
        quality: Station id → ``{"fraction_valid": ..., "n_dates": ...}``.
        aggregation: How the dates were combined.
        schema: :data:`OBSERVATIONS_SCHEMA`.
    """

    corridor: str
    source: dict[str, Any]
    window_s: float
    t0_local: str
    duration_s: float
    stations: tuple[ObservedStation, ...]
    flows_veh_h: dict[str, list[float]]
    speeds_ms: dict[str, list[float]]
    occupancy_pct: dict[str, list[float]]
    spread: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    quality: dict[str, dict[str, float]] = field(default_factory=dict)
    aggregation: str = DEFAULT_AGGREGATION
    schema: str = OBSERVATIONS_SCHEMA

    @property
    def n_windows(self) -> int:
        """Windows in the analysed span."""
        return round(self.duration_s / self.window_s)

    def window_start_s(self, index: int) -> float:
        """Simulation time [s] at which window ``index`` starts."""
        return index * self.window_s

    def station(self, station_id: str) -> ObservedStation:
        """Metadata of one station.

        Raises:
            KeyError: No such station in the artifact.
        """
        for s in self.stations:
            if s.id == station_id:
                return s
        raise KeyError(f"observations for {self.corridor!r} hold no station {station_id!r}")

    def mainline_stations(self) -> tuple[ObservedStation, ...]:
        """Mainline stations with a known position, ordered by ``x_m``."""
        known = [s for s in self.stations if s.kind == "mainline" and s.x_m is not None]
        return tuple(sorted(known, key=lambda s: float(s.x_m or 0.0)))

    # -- construction ------------------------------------------------------

    @classmethod
    def from_frame(
        cls,
        df: pd.DataFrame,
        stations: Sequence[Mapping[str, Any] | ObservedStation] | pd.DataFrame | None = None,
        *,
        window_s: float,
        t0_local: str,
        duration_s: float,
        corridor: str,
        source: Mapping[str, Any] | str,
        aggregation: str = DEFAULT_AGGREGATION,
    ) -> Observations:
        """Aggregate a tidy detector frame into an observations artifact.

        Rows are placed by their **local wall clock**: a row whose local time
        is ``t0_local + k·window_s`` lands in window ``k``, whatever date it
        comes from. Rows outside ``[t0, t0 + duration_s)`` are ignored (that is
        how a whole-day fetch becomes a peak-period artifact); a row *inside*
        the span that does not start on the window grid is an error, not a
        silent drop — it means the frame is on a finer interval than
        ``window_s`` and has to be aggregated first. Each window's value is the
        mean over the dates that observed it (NaN when none did), and
        ``spread`` carries the sample standard deviation across those dates.

        The analysed span must lie inside one local day: window indices are
        computed from the local clock reading, so a span crossing local
        midnight is not supported.

        Args:
            df: Tidy detector frame
                (:mod:`calibration.loaders.detector_csv`).
            stations: Station metadata — a stations table (DataFrame), a
                sequence of mappings or :class:`ObservedStation` objects. When
                None the metadata is taken from the frame itself (id, lanes,
                kind, x_m).
            window_s: Analysis window [s]; must equal the frame's interval.
            t0_local: Local wall-clock start, ``"HH:MM"`` or ``"HH:MM:SS"``.
            duration_s: Analysed span [s]; a whole multiple of ``window_s``.
            corridor: Corridor name recorded on the artifact.
            source: Provenance mapping (or a plain string, stored as
                ``{"provider": ...}``).
            aggregation: Override the aggregation description.

        Returns:
            The artifact.

        Raises:
            ValueError: ``duration_s`` is not a multiple of ``window_s``,
                ``t0_local`` is unparseable, the frame's interval disagrees
                with ``window_s``, or the frame lacks a required column.
        """
        required = ("timestamp", "station", "flow_veh_h")
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"from_frame: detector frame is missing column(s) {missing}")
        if window_s <= 0:
            raise ValueError(f"window_s must be > 0, got {window_s}")
        n_windows = duration_s / window_s
        if duration_s <= 0 or abs(n_windows - round(n_windows)) > 1e-9:
            raise ValueError(
                f"duration_s ({duration_s}) must be a positive whole multiple of "
                f"window_s ({window_s})"
            )
        n_windows = round(n_windows)
        t0_s = parse_clock(t0_local)
        detector_interval_s(df)  # refuse a frame that is not on one grid at all

        work = df.copy()
        work["_secs"] = local_seconds(work)
        work["_date"] = local_dates(work)
        offset = (work["_secs"] - t0_s) / window_s
        work["_window"] = offset.round().astype("int64")
        on_grid = (offset - work["_window"]).abs() < 1e-6
        in_span = (work["_secs"] >= t0_s) & (work["_secs"] < t0_s + duration_s)
        stray = work[in_span & ~on_grid]
        if not stray.empty:
            raise ValueError(
                f"from_frame: {len(stray)} row(s) inside the analysed span do not start on "
                f"the {window_s:g} s window grid (first at {stray['_secs'].iloc[0]:g} s after "
                f"local midnight) — aggregate the frame to window_s first, or pass the "
                f"frame's own window"
            )
        work = work[on_grid & (work["_window"] >= 0) & (work["_window"] < n_windows)]

        specs = _station_specs(stations, work)
        flows: dict[str, list[float]] = {}
        speeds: dict[str, list[float]] = {}
        occupancy: dict[str, list[float]] = {}
        flows_sd: dict[str, list[float]] = {}
        speeds_sd: dict[str, list[float]] = {}
        quality: dict[str, dict[str, float]] = {}
        for spec in specs:
            rows = work[work["station"] == spec.id]
            flows[spec.id], flows_sd[spec.id] = _mean_sd(rows, "flow_veh_h", n_windows)
            speeds[spec.id], speeds_sd[spec.id] = _mean_sd(rows, "speed_ms", n_windows)
            occupancy[spec.id], _ = _mean_sd(rows, "occupancy_pct", n_windows)
            observed = sum(1 for v in flows[spec.id] if not math.isnan(v))
            dates = rows.loc[rows["flow_veh_h"].notna(), "_date"] if not rows.empty else []
            quality[spec.id] = {
                "fraction_valid": observed / n_windows if n_windows else 0.0,
                "n_dates": float(len(set(dates))),
            }
        provenance = dict(source) if isinstance(source, Mapping) else {"provider": str(source)}
        return cls(
            corridor=corridor,
            source=provenance,
            window_s=float(window_s),
            t0_local=t0_local,
            duration_s=float(duration_s),
            stations=tuple(specs),
            flows_veh_h=flows,
            speeds_ms=speeds,
            occupancy_pct=occupancy,
            spread={"flows_veh_h_sd": flows_sd, "speeds_ms_sd": speeds_sd},
            quality=quality,
            aggregation=aggregation,
        )

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The artifact's JSON form (NaN written as ``null``)."""
        return {
            "schema": self.schema,
            "corridor": self.corridor,
            "source": dict(self.source),
            "window_s": self.window_s,
            "t0_local": self.t0_local,
            "duration_s": self.duration_s,
            "n_windows": self.n_windows,
            "aggregation": self.aggregation,
            "stations": [s.to_dict() for s in self.stations],
            "flows_veh_h": {k: _nan_to_none(v) for k, v in self.flows_veh_h.items()},
            "speeds_ms": {k: _nan_to_none(v) for k, v in self.speeds_ms.items()},
            "occupancy_pct": {k: _nan_to_none(v) for k, v in self.occupancy_pct.items()},
            "spread": {
                name: {k: _nan_to_none(v) for k, v in series.items()}
                for name, series in self.spread.items()
            },
            "quality": {k: dict(v) for k, v in self.quality.items()},
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Observations:
        """Rebuild from the JSON form.

        Raises:
            ValueError: The payload carries a different ``schema``.
        """
        schema = str(raw.get("schema", OBSERVATIONS_SCHEMA))
        if schema != OBSERVATIONS_SCHEMA:
            raise ValueError(f"expected schema {OBSERVATIONS_SCHEMA!r}, got {schema!r}")
        stations = tuple(ObservedStation.from_mapping(s) for s in raw.get("stations", ()))
        spread = {
            name: {k: _none_to_nan(v) for k, v in series.items()}
            for name, series in (raw.get("spread") or {}).items()
        }
        return cls(
            corridor=str(raw.get("corridor", "")),
            source=dict(raw.get("source") or {}),
            window_s=float(raw["window_s"]),
            t0_local=str(raw["t0_local"]),
            duration_s=float(raw["duration_s"]),
            stations=stations,
            flows_veh_h={k: _none_to_nan(v) for k, v in (raw.get("flows_veh_h") or {}).items()},
            speeds_ms={k: _none_to_nan(v) for k, v in (raw.get("speeds_ms") or {}).items()},
            occupancy_pct={k: _none_to_nan(v) for k, v in (raw.get("occupancy_pct") or {}).items()},
            spread=spread,
            quality={k: dict(v) for k, v in (raw.get("quality") or {}).items()},
            aggregation=str(raw.get("aggregation", DEFAULT_AGGREGATION)),
            schema=schema,
        )

    def to_json(self, path: str | Path) -> Path:
        """Write the artifact as JSON (parents created).

        Args:
            path: Destination file.

        Returns:
            The destination path.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, allow_nan=False))
        return target

    @classmethod
    def from_json(cls, path: str | Path) -> Observations:
        """Read an artifact written by :meth:`to_json`."""
        return cls.from_dict(json.loads(Path(path).read_text()))


def parse_clock(value: str) -> float:
    """``"HH:MM"`` / ``"HH:MM:SS"`` → seconds since local midnight.

    Raises:
        ValueError: Not a clock time in ``[00:00:00, 24:00:00)``.
    """
    parts = str(value).strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError(f"t0_local must be 'HH:MM' or 'HH:MM:SS', got {value!r}")
    hours, minutes = int(parts[0]), int(parts[1])
    seconds = int(parts[2]) if len(parts) == 3 else 0
    if not (0 <= hours < 24 and 0 <= minutes < 60 and 0 <= seconds < 60):
        raise ValueError(f"t0_local {value!r} is not a clock time in [00:00:00, 24:00:00)")
    return hours * 3600.0 + minutes * 60.0 + seconds


def _mean_sd(rows: pd.DataFrame, column: str, n_windows: int) -> tuple[list[float], list[float]]:
    """Per-window mean and sample sd over dates (NaN where nothing observed)."""
    means = [float("nan")] * n_windows
    sds = [float("nan")] * n_windows
    if rows.empty or column not in rows.columns:
        return means, sds
    grouped = rows.groupby("_window")[column]
    for window, values in grouped:
        good = values.dropna()
        index = int(window)
        if not len(good) or not 0 <= index < n_windows:
            continue
        means[index] = float(good.mean())
        if len(good) > 1:
            sds[index] = float(good.std(ddof=1))
    return means, sds


def _station_specs(
    stations: Sequence[Mapping[str, Any] | ObservedStation] | pd.DataFrame | None,
    frame: pd.DataFrame,
) -> list[ObservedStation]:
    """Station metadata from the caller's table, else inferred from the frame."""
    if stations is None:
        specs: list[ObservedStation] = []
        for station_id, rows in frame.groupby("station", sort=True):
            x_values = rows["x_m"].dropna() if "x_m" in rows.columns else pd.Series(dtype=float)
            lanes = rows["lanes"].dropna() if "lanes" in rows.columns else pd.Series(dtype=float)
            kinds = rows["kind"].dropna() if "kind" in rows.columns else pd.Series(dtype=object)
            specs.append(
                ObservedStation(
                    id=str(station_id),
                    x_m=float(x_values.iloc[0]) if len(x_values) else None,
                    lanes=int(lanes.iloc[0]) if len(lanes) else 0,
                    kind=str(kinds.iloc[0]) if len(kinds) else "mainline",
                )
            )
        return specs
    if isinstance(stations, pd.DataFrame):
        rows_iter: Iterable[Mapping[str, Any] | ObservedStation] = stations.to_dict("records")
    else:
        rows_iter = stations
    return [
        s if isinstance(s, ObservedStation) else ObservedStation.from_mapping(s) for s in rows_iter
    ]


# ---------------------------------------------------------------------------
# Derived views (the report's observed side)
# ---------------------------------------------------------------------------


def hourly_link_flows(obs: Observations) -> pd.DataFrame:
    """Hourly observed link flows per mainline station (contract §5).

    GEH is defined on hourly volumes, so the five-minute windows are combined
    into clock hours of the analysed span. An hour is reported **only** when
    every one of its windows was observed at that station — a partial hour
    would understate the volume, and scaling it up would invent traffic.
    Because each window already carries veh/h, the hour's flow is the mean of
    its windows.

    Args:
        obs: The observations artifact.

    Returns:
        DataFrame with ``x_ref_m`` (station position [m]),
        ``window_start_s`` (simulation time of the hour's start [s]),
        ``flow_veh_h`` and ``station``, sorted by time then position. Empty
        when no station completes an hour.

    Raises:
        ValueError: ``window_s`` does not divide an hour.
    """
    ratio = _S_PER_HOUR / obs.window_s
    if abs(ratio - round(ratio)) > 1e-9:
        raise ValueError(
            f"hourly_link_flows: window_s {obs.window_s:g} s does not divide an hour; "
            f"GEH is defined on hourly volumes"
        )
    per_hour = round(ratio)
    n_hours = obs.n_windows // per_hour
    rows: list[dict[str, Any]] = []
    for station in obs.mainline_stations():
        series = obs.flows_veh_h.get(station.id, [])
        for hour in range(n_hours):
            chunk = series[hour * per_hour : (hour + 1) * per_hour]
            if len(chunk) < per_hour or any(math.isnan(v) for v in chunk):
                continue
            rows.append(
                {
                    "x_ref_m": float(station.x_m or 0.0),
                    "window_start_s": float(hour * per_hour * obs.window_s),
                    "flow_veh_h": float(sum(chunk) / per_hour),
                    "station": station.id,
                }
            )
    frame = pd.DataFrame(rows, columns=["x_ref_m", "window_start_s", "flow_veh_h", "station"])
    if frame.empty:
        return frame
    return frame.sort_values(["window_start_s", "x_ref_m"], kind="stable").reset_index(drop=True)


def segment_speed_matrix(obs: Observations) -> tuple[np.ndarray, list[float]]:
    """Observed segment speeds as a ``windows × stations`` matrix.

    The RMSPE criterion compares simulated segment mean speeds with the
    observed ones on the same grid; this is the observed side, with NaN
    wherever a window was not measured (the comparison skips those cells).

    Args:
        obs: The observations artifact.

    Returns:
        ``(speeds, x_m)`` — a float array of shape
        ``(n_windows, n_mainline_stations)`` in m/s, and the stations'
        positions [m] in increasing order (the array's column order).
    """
    stations = obs.mainline_stations()
    matrix = np.full((obs.n_windows, len(stations)), np.nan, dtype=float)
    for column, station in enumerate(stations):
        series = obs.speeds_ms.get(station.id, [])
        for window in range(min(obs.n_windows, len(series))):
            matrix[window, column] = series[window]
    return matrix, [float(s.x_m or 0.0) for s in stations]


def coverage(obs: Observations) -> pd.DataFrame:
    """Per-station coverage of the analysed span (what the CLI prints).

    Args:
        obs: The observations artifact.

    Returns:
        DataFrame with ``station``, ``kind``, ``x_m``, ``n_windows``,
        ``n_observed``, ``fraction_valid`` and ``n_dates``, in artifact order.
    """
    rows = []
    for station in obs.stations:
        series = obs.flows_veh_h.get(station.id, [])
        observed = sum(1 for v in series if not math.isnan(v))
        quality = obs.quality.get(station.id, {})
        rows.append(
            {
                "station": station.id,
                "kind": station.kind,
                "x_m": station.x_m,
                "n_windows": obs.n_windows,
                "n_observed": observed,
                "fraction_valid": quality.get(
                    "fraction_valid", observed / obs.n_windows if obs.n_windows else 0.0
                ),
                "n_dates": quality.get("n_dates", float("nan")),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "station",
            "kind",
            "x_m",
            "n_windows",
            "n_observed",
            "fraction_valid",
            "n_dates",
        ],
    )
