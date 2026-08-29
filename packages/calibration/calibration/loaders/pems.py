"""PeMS 5-minute station data loader (CLAUDE.md §6.1).

Parses Caltrans PeMS station CSV exports (Timestamp, Station, District, Flow,
Occupancy, Speed) into a tidy SI-unit table for fundamental-diagram fitting,
and accepts a generic column mapping so TxDOT-style or other-state detector
exports work through the same path.

Occupancy → density conversion (the "g-factor"), stated honestly:
a point detector's time occupancy over an aggregation interval is
``o ≈ ρ · g`` where ``g`` is the *effective vehicle length* — mean vehicle
length plus the detector's own field length (Treiber & Kesting, *Traffic Flow
Dynamics*, ch. 2/3; standard loop-detector practice). We therefore estimate
``ρ = o / g``. This assumes (a) an approximately homogeneous vehicle-length
mix within each interval (truck share shifts g substantially), (b) stationary
traffic within the 5-min window, and (c) a known detector field length.
The default ``g = 7.0 m`` (~4.5 m passenger car + ~2.5 m loop field) is a
documented convention, not a calibrated value; the resulting density is an
*estimate*, and any FD fitted from it inherits this g-factor uncertainty.

Unit note: PeMS publishes speed in mph. :data:`MPH_TO_MS` is derived exactly
from the sanctioned :data:`~calibration.loaders.ngsim.FEET_TO_M` ingestion
constant (1 mile = 5280 ft, 1 h = 3600 s by definition); km/h inputs go
through ``flowstate_core.units``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

import pandas as pd

from calibration.loaders.ngsim import FEET_TO_M
from flowstate_core.units import kmh_to_ms

_FT_PER_MILE: Final[float] = 5280.0
_S_PER_HOUR: Final[float] = 3600.0

MPH_TO_MS: Final[float] = FEET_TO_M * _FT_PER_MILE / _S_PER_HOUR
"""mph → m/s (= 0.44704 exactly), derived from FEET_TO_M."""

G_EFFECTIVE_LENGTH_DEFAULT_M: Final[float] = 7.0
"""Default effective vehicle length g [m] for occupancy → density (see module
docstring for the assumptions this carries)."""

PEMS_INTERVAL_S: Final[float] = 300.0
"""PeMS station aggregation interval [s] (5 minutes)."""

# Canonical field → default PeMS column spelling (matched case-insensitively).
_DEFAULT_COLUMNS: Final[dict[str, str]] = {
    "timestamp": "Timestamp",
    "station": "Station",
    "district": "District",
    "flow": "Flow",
    "occupancy": "Occupancy",
    "speed": "Speed",
}


def load_pems_station_csv(
    path: str | Path,
    *,
    g_effective_length_m: float = G_EFFECTIVE_LENGTH_DEFAULT_M,
    interval_s: float = PEMS_INTERVAL_S,
    speed_unit: Literal["mph", "kmh", "ms"] = "mph",
    occupancy_unit: Literal["fraction", "percent"] = "fraction",
    column_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Load a PeMS (or PeMS-like) station CSV into a tidy SI table.

    Args:
        path: CSV path. Default column spellings are the PeMS export names
            (Timestamp, Station, District, Flow, Occupancy, Speed), matched
            case-insensitively.
        g_effective_length_m: Effective vehicle length g [m] for the
            occupancy → density estimate ``ρ = o / g`` (module docstring).
        interval_s: Aggregation interval [s]; ``Flow`` is a vehicle *count*
            per interval, converted to veh/s by dividing by this.
        speed_unit: Unit of the speed column — ``"mph"`` (PeMS default),
            ``"kmh"``, or ``"ms"`` (already SI).
        occupancy_unit: ``"fraction"`` (0–1, PeMS raw) or ``"percent"``
            (0–100, common in TxDOT-style exports).
        column_map: Optional mapping from canonical field names
            (``timestamp, station, district, flow, occupancy, speed``) to the
            actual CSV column names, for non-PeMS exports. ``district`` is
            optional.

    Returns:
        DataFrame with columns ``timestamp`` (as read), ``station`` (str),
        ``flow_veh_s`` [veh/s], ``occupancy`` (fraction), ``density_veh_m``
        [veh/m, estimated — see module docstring] and ``speed_ms`` [m/s].
        Includes ``district`` when present in the source.

    Raises:
        ValueError: On missing required columns or invalid parameters.
    """
    if g_effective_length_m <= 0:
        raise ValueError(f"g_effective_length_m must be > 0, got {g_effective_length_m}")
    if interval_s <= 0:
        raise ValueError(f"interval_s must be > 0, got {interval_s}")

    wanted = dict(_DEFAULT_COLUMNS)
    if column_map:
        unknown = set(column_map) - set(wanted)
        if unknown:
            raise ValueError(f"column_map has unknown canonical fields {sorted(unknown)}")
        wanted.update(column_map)

    raw = pd.read_csv(path)
    lower_lookup = {str(c).strip().lower(): c for c in raw.columns}

    def find(canonical: str) -> str | None:
        return lower_lookup.get(wanted[canonical].strip().lower())

    required = ("timestamp", "station", "flow", "occupancy", "speed")
    missing = [wanted[c] for c in required if find(c) is None]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")

    occ = raw[find("occupancy")].astype(float)
    if occupancy_unit == "percent":
        occ = occ / 100.0
    speed = raw[find("speed")].astype(float)
    if speed_unit == "mph":
        speed_ms = speed * MPH_TO_MS
    elif speed_unit == "kmh":
        speed_ms = speed.map(kmh_to_ms)
    else:
        speed_ms = speed

    out = pd.DataFrame(
        {
            "timestamp": raw[find("timestamp")],
            "station": raw[find("station")].astype(str),
            "flow_veh_s": raw[find("flow")].astype(float) / interval_s,
            "occupancy": occ,
            "density_veh_m": occ / g_effective_length_m,
            "speed_ms": speed_ms,
        }
    )
    district_col = find("district")
    if district_col is not None:
        out["district"] = raw[district_col]
    return out
