"""Generic detector (loop/radar) CSV loader → the tidy observation frame.

This is the shape every detector source is normalised to before it becomes an
observations artifact (docs/CONTRACTS.md, "Detector observations"): one row
per station × window, with

``timestamp``
    ISO-8601 with offset, the window **start**, on a regular interval.
``station``
    Stable detector-station id as the source spells it (MnDOT ``"S76"``, a
    PeMS VDS number, any string).
``flow_veh_h``
    Station **total** across the mainline lanes [veh/h], NaN when the window
    was not measured well enough to report (the upstream loader decides; see
    :func:`calibration.loaders.mndot.station_frame`).
``occupancy_pct``
    Mean lane occupancy [percent], NaN when unmeasured.
``speed_ms``
    Mean lane speed [m/s], NaN when unmeasured.
``lanes``
    Mainline lanes at the station (0 = not stated by the source).
``kind``
    ``mainline``, ``on_ramp`` or ``off_ramp``.
``x_m``
    Position along the corridor [m], NaN until a stations table supplies it.

A file that already uses those spellings loads with no arguments; anything
else passes ``column_map`` from the canonical field names
(``timestamp, station, flow, occupancy, speed, lanes, kind, x_m``) to its own
column names.

Honesty rules this module enforces: missing is NaN and is never filled in; the
timestamps must live on one regular grid (:func:`detector_interval_s` — gaps
are fine, a second interval is not, because every downstream window index
assumes a single one); and an unreadable or absent required column raises a
plain ``ValueError`` that names the column rather than a pandas message
quoting the file's contents.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Final, Literal

import pandas as pd

from calibration.loaders.pems import MPH_TO_MS
from flowstate_core.units import kmh_to_ms

SpeedUnit = Literal["mph", "kmh", "ms"]
OccupancyUnit = Literal["pct", "percent", "fraction"]
DetectorKind = Literal["mainline", "on_ramp", "off_ramp"]

DETECTOR_KINDS: Final[tuple[str, ...]] = ("mainline", "on_ramp", "off_ramp")
"""Allowed values of the ``kind`` column."""

DETECTOR_COLUMNS: Final[tuple[str, ...]] = (
    "timestamp",
    "station",
    "flow_veh_h",
    "occupancy_pct",
    "speed_ms",
    "lanes",
    "kind",
    "x_m",
)
"""Column order of the tidy frame (module docstring)."""

#: Canonical field → the column name the tidy frame uses for it.
_DEFAULT_COLUMNS: Final[dict[str, str]] = {
    "timestamp": "timestamp",
    "station": "station",
    "flow": "flow_veh_h",
    "occupancy": "occupancy_pct",
    "speed": "speed_ms",
    "lanes": "lanes",
    "kind": "kind",
    "x_m": "x_m",
}

#: Fields a file must carry; the rest default to NaN / ``kind_default``.
_REQUIRED_FIELDS: Final[tuple[str, ...]] = ("timestamp", "station", "flow")

#: Seconds tolerated between two timestamp differences before the interval is
#: called irregular. Timestamps are written with whole-second precision, so
#: anything below a second is formatting noise, not a different interval.
INTERVAL_TOLERANCE_S: Final[float] = 1.0


def _parse_timestamp(value: object, *, row: int, path: str | Path) -> datetime:
    """One timestamp cell → an aware or naive :class:`datetime.datetime`.

    Args:
        value: Cell value (ISO-8601 string, ``datetime`` or
            :class:`pandas.Timestamp`).
        row: 1-based file row, for the error message.
        path: Source path, for the error message.

    Raises:
        ValueError: The cell is empty or not ISO-8601 (the offending text is
            not echoed — it is file content).
    """
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in ("nan", "nat"):
        raise ValueError(f"{path}: empty timestamp at file row {row}")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{path}: timestamp at file row {row} is not ISO-8601 "
            f"(expected e.g. 2026-09-15T06:00:00-05:00; the value is not echoed)"
        ) from exc


def _numeric(series: pd.Series, *, column: str, path: str | Path) -> pd.Series:
    """Coerce a column to float, naming the column and row on a bad cell."""
    coerced = pd.to_numeric(series, errors="coerce")
    bad = series.index[coerced.isna() & series.notna() & (series.astype(str).str.strip() != "")]
    if len(bad):
        raise ValueError(
            f"{path}: column {column!r} is not numeric in {len(bad)} row(s), first at "
            f"file row {int(bad[0]) + 2} (the offending value is not echoed)"
        )
    return coerced.astype(float)


def _as_datetime(value: object) -> datetime:
    """Any accepted timestamp representation → :class:`datetime.datetime`.

    Frames built by :func:`load_detector_csv` already hold ``datetime``
    objects; a frame read back with plain ``pandas.read_csv`` holds strings,
    and both must behave the same in the helpers below.
    """
    return _parse_timestamp(value, row=0, path="<frame>")


def timestamps_utc(df: pd.DataFrame) -> pd.DatetimeIndex:
    """UTC instants of a tidy frame's ``timestamp`` column (for ordering).

    Args:
        df: Tidy detector frame.

    Returns:
        A UTC :class:`pandas.DatetimeIndex` aligned with ``df``'s rows. Naive
        timestamps (a source that published no offset) are read as UTC, which
        keeps them ordered and equally spaced — their *local* interpretation
        comes from :func:`local_seconds`, never from this index.
    """
    return pd.DatetimeIndex(pd.to_datetime(df["timestamp"], utc=True))


def local_seconds(df: pd.DataFrame) -> pd.Series:
    """Seconds since local midnight for each row (the source's own clock).

    The window index of an observations artifact is defined on local wall
    time (``t0_local``), and each timestamp carries its own UTC offset, so the
    local clock reading is simply the timestamp with its offset dropped — no
    timezone database and no DST arithmetic is involved.

    Args:
        df: Tidy detector frame.

    Returns:
        Float seconds in ``[0, 86400)``, indexed like ``df``.
    """
    naive = [_as_datetime(t).replace(tzinfo=None) for t in df["timestamp"]]
    return pd.Series(
        [t.hour * 3600.0 + t.minute * 60.0 + t.second + t.microsecond / 1e6 for t in naive],
        index=df.index,
        dtype=float,
    )


def local_dates(df: pd.DataFrame) -> pd.Series:
    """Local calendar date (``YYYY-MM-DD``) of each row, as strings."""
    return pd.Series(
        [_as_datetime(t).replace(tzinfo=None).date().isoformat() for t in df["timestamp"]],
        index=df.index,
        dtype=object,
    )


def detector_interval_s(df: pd.DataFrame) -> float:
    """The frame's sampling interval [s], validating that the grid is regular.

    "Regular" means every distinct timestamp sits on one grid: the shortest
    gap is the interval and every other gap is a whole multiple of it. Longer
    gaps are therefore fine and expected — a window no detector measured, the
    overnight break between two fetched dates, a span that covers only the
    morning peak — while a file mixing 5-minute and 7-minute windows (two
    exports concatenated, a daylight-saving fold) is refused, because every
    window index downstream is computed from a single interval.

    Args:
        df: Tidy detector frame.

    Returns:
        The interval [s]. A frame with a single distinct timestamp has no
        interval and returns ``0.0``.

    Raises:
        ValueError: A gap is not a whole multiple of the interval (within
            :data:`INTERVAL_TOLERANCE_S`). The message names the offending
            pair of timestamps.
    """
    instants = timestamps_utc(df).unique().sort_values()
    if len(instants) < 2:
        return 0.0
    deltas = (instants[1:] - instants[:-1]).total_seconds().tolist()
    base = min(deltas)
    if base <= 0.0:
        raise ValueError("detector frame holds two rows with the same timestamp ordering")
    for k, delta in enumerate(deltas):
        multiple = delta / base
        if abs(multiple - round(multiple)) * base > INTERVAL_TOLERANCE_S:
            raise ValueError(
                f"detector frame has an irregular interval: {base:g} s is the shortest gap "
                f"but {delta:g} s separates {instants[k]} from {instants[k + 1]}, which is "
                f"not a whole multiple of it — a tidy detector frame lives on one interval "
                f"(an unmeasured window is a NaN row or an absent one, never a shorter gap)"
            )
    return float(base)


def load_detector_csv(
    path: str | Path,
    *,
    column_map: dict[str, str] | None = None,
    speed_unit: SpeedUnit = "ms",
    occupancy_unit: OccupancyUnit = "pct",
    kind_default: DetectorKind = "mainline",
) -> pd.DataFrame:
    """Load a detector CSV into the tidy observation frame (module docstring).

    Args:
        path: CSV path.
        column_map: Optional mapping from canonical field names
            (``timestamp, station, flow, occupancy, speed, lanes, kind,
            x_m``) to the file's own column names, matched
            case-insensitively. Fields left out keep the tidy spelling.
        speed_unit: Unit of the speed column — ``"mph"``, ``"kmh"`` or
            ``"ms"`` (already SI).
        occupancy_unit: ``"pct"``/``"percent"`` (0–100, the tidy convention)
            or ``"fraction"`` (0–1, multiplied by 100 on load).
        kind_default: ``kind`` for rows whose file has no ``kind`` column.

    Returns:
        DataFrame with the columns and units of :data:`DETECTOR_COLUMNS`,
        sorted by timestamp then station, with a fresh index. Missing values
        are NaN and are never filled in. The regular interval is validated on
        load and recorded in ``df.attrs["interval_s"]``.

    Raises:
        ValueError: A required column is missing (the message names it), a
            numeric column holds a non-numeric cell, a timestamp is not
            ISO-8601, ``kind`` holds a value outside
            :data:`DETECTOR_KINDS`, or the interval is irregular.
    """
    if kind_default not in DETECTOR_KINDS:
        raise ValueError(
            f"kind_default must be one of {list(DETECTOR_KINDS)}, got {kind_default!r}"
        )
    if speed_unit not in ("mph", "kmh", "ms"):
        raise ValueError(f"speed_unit must be one of ['mph', 'kmh', 'ms'], got {speed_unit!r}")
    if occupancy_unit not in ("pct", "percent", "fraction"):
        raise ValueError(
            f"occupancy_unit must be one of ['pct', 'percent', 'fraction'], got {occupancy_unit!r}"
        )

    wanted = dict(_DEFAULT_COLUMNS)
    if column_map:
        unknown = set(column_map) - set(wanted)
        if unknown:
            raise ValueError(
                f"column_map has unknown canonical fields {sorted(unknown)}; "
                f"expected a subset of {sorted(wanted)}"
            )
        wanted.update(column_map)

    try:
        raw = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise ValueError(
            f"{path}: not a readable CSV (no header row parsed) — the file is empty, "
            f"truncated, or not delimited text"
        ) from exc
    lookup = {str(c).strip().lower(): c for c in raw.columns}

    def find(field: str) -> str | None:
        return lookup.get(wanted[field].strip().lower())

    missing = [wanted[f] for f in _REQUIRED_FIELDS if find(f) is None]
    if missing:
        raise ValueError(
            f"{path}: missing column(s) {missing} — a detector CSV needs at least "
            f"{[wanted[f] for f in _REQUIRED_FIELDS]} (pass column_map to name them)"
        )
    if raw.empty:
        raise ValueError(
            f"{path}: parsed {len(raw.columns)} column(s) but 0 data rows — the file is "
            f"header-only or truncated"
        )

    timestamps = [
        _parse_timestamp(v, row=i + 2, path=path) for i, v in enumerate(raw[str(find("timestamp"))])
    ]
    out = pd.DataFrame({"timestamp": pd.Series(timestamps, dtype=object)})
    out["station"] = raw[str(find("station"))].astype(str).str.strip()
    out["flow_veh_h"] = _numeric(raw[str(find("flow"))], column=wanted["flow"], path=path)

    occ_col = find("occupancy")
    if occ_col is None:
        out["occupancy_pct"] = float("nan")
    else:
        occ = _numeric(raw[occ_col], column=wanted["occupancy"], path=path)
        out["occupancy_pct"] = occ * 100.0 if occupancy_unit == "fraction" else occ

    speed_col = find("speed")
    if speed_col is None:
        out["speed_ms"] = float("nan")
    else:
        speed = _numeric(raw[speed_col], column=wanted["speed"], path=path)
        if speed_unit == "mph":
            speed = speed * MPH_TO_MS
        elif speed_unit == "kmh":
            speed = speed.map(kmh_to_ms)
        out["speed_ms"] = speed

    lanes_col = find("lanes")
    lanes = (
        _numeric(raw[lanes_col], column=wanted["lanes"], path=path)
        if lanes_col is not None
        else pd.Series(0.0, index=raw.index)
    )
    out["lanes"] = lanes.fillna(0.0).astype(int)

    kind_col = find("kind")
    if kind_col is None:
        out["kind"] = kind_default
    else:
        kinds = raw[kind_col].astype(str).str.strip()
        kinds = kinds.where(kinds.str.len() > 0, kind_default)
        bad_kinds = sorted(set(kinds) - set(DETECTOR_KINDS))
        if bad_kinds:
            raise ValueError(
                f"{path}: column {wanted['kind']!r} holds unknown kind(s) {bad_kinds}; "
                f"expected {list(DETECTOR_KINDS)}"
            )
        out["kind"] = kinds

    x_col = find("x_m")
    out["x_m"] = (
        _numeric(raw[x_col], column=wanted["x_m"], path=path) if x_col is not None else float("nan")
    )

    out = out[list(DETECTOR_COLUMNS)]
    order = pd.DataFrame({"t": timestamps_utc(out), "s": out["station"]}).sort_values(
        ["t", "s"], kind="stable"
    )
    out = out.loc[order.index].reset_index(drop=True)
    out.attrs["interval_s"] = detector_interval_s(out)
    return out


def write_detector_csv(df: pd.DataFrame, path: str | Path) -> Path:
    """Write a tidy detector frame as CSV (the inverse of the loader).

    Timestamps are written as ISO-8601 with their offset, missing values as
    empty cells, and the columns in :data:`DETECTOR_COLUMNS` order.

    Args:
        df: Tidy detector frame (extra columns are dropped).
        path: Destination CSV path (parent directories are created).

    Returns:
        The destination path.

    Raises:
        ValueError: ``df`` lacks one of :data:`DETECTOR_COLUMNS`.
    """
    missing = [c for c in DETECTOR_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"write_detector_csv: frame is missing column(s) {missing}")
    out = df[list(DETECTOR_COLUMNS)].copy()
    out["timestamp"] = [_as_datetime(t).isoformat() for t in df["timestamp"]]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(target, index=False)
    return target


def to_fd_frame(
    df: pd.DataFrame,
    *,
    g_effective_length_m: float = 7.0,
) -> pd.DataFrame:
    """Tidy detector frame → the per-lane (density, flow) table the FD fit reads.

    ``flow_veh_h`` is a station total, while a fundamental diagram is a
    per-lane relation, so flow is divided by the station's lane count.
    Density is taken from the identity ``ρ = q / v`` where a speed is
    reported, and otherwise estimated from occupancy as ``ρ = o / g`` with
    the effective-vehicle-length assumption documented in
    :mod:`calibration.loaders.pems` — the two estimators are not equivalent
    and the g-factor one inherits that module's caveats.

    Only ``mainline`` rows with a positive lane count, a finite flow and one
    of the two density estimators are kept; nothing is imputed.

    Args:
        df: Tidy detector frame.
        g_effective_length_m: Effective vehicle length g [m] for the
            occupancy fallback.

    Returns:
        DataFrame with ``density_veh_m``, ``flow_veh_s``, ``occupancy``
        (fraction) and ``speed_ms``, one row per usable input row.

    Raises:
        ValueError: ``g_effective_length_m <= 0``, or no row is usable.
    """
    if g_effective_length_m <= 0:
        raise ValueError(f"g_effective_length_m must be > 0, got {g_effective_length_m}")
    rows = df[df["kind"] == "mainline"] if "kind" in df.columns else df
    lanes = pd.to_numeric(rows["lanes"], errors="coerce").astype(float)
    flow_veh_s = pd.to_numeric(rows["flow_veh_h"], errors="coerce").astype(float) / 3600.0
    per_lane = flow_veh_s.where(lanes > 0) / lanes.where(lanes > 0)
    speed = pd.to_numeric(rows["speed_ms"], errors="coerce").astype(float)
    occupancy = pd.to_numeric(rows["occupancy_pct"], errors="coerce").astype(float) / 100.0
    from_speed = per_lane / speed.where(speed > 0)
    from_occupancy = occupancy / g_effective_length_m
    density = from_speed.where(from_speed.notna(), from_occupancy)
    out = pd.DataFrame(
        {
            "density_veh_m": density,
            "flow_veh_s": per_lane,
            "occupancy": occupancy,
            "speed_ms": speed,
        }
    )
    out = out[out["density_veh_m"].notna() & out["flow_veh_s"].notna()].reset_index(drop=True)
    if out.empty:
        raise ValueError(
            "to_fd_frame: no usable row — a fundamental diagram needs mainline rows with a "
            "lane count, a finite flow and either a speed or an occupancy"
        )
    return out
