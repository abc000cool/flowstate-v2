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

Unit-mistake guards (why they live here and not in the fitter): a wrong
``occupancy_unit``, ``interval_s`` or ``g_effective_length_m`` rescales a
whole column, and the downstream FD fit absorbs that silently — an hourly
count read as a 5-min count fits with an unchanged R² of 0.99 and a free-flow
speed of 1200 km/h. This loader is the only place that sees all three columns
together, so it checks them against each other:

* occupancy must not exceed 1 after the declared unit conversion (a
  systematic breach means ``occupancy_unit``; only the *upper* breach is a
  unit signature — negative occupancy is a failed-interval sentinel, handled
  as a row drop downstream), and
* the implied speed ``q/ρ`` must agree with the reported speed within a
  factor of :data:`MAX_SPEED_RATIO_FACTOR` (a systematic breach means
  ``interval_s`` or ``g_effective_length_m``).

Both are *systematic*-error guards with a small tolerated share of offending
rows: individual failed intervals (negative sentinels, an occupancy spike)
are left in place and dropped, counted and reported by
``calibration.fd_fit.fit_triangular_fd``.
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

MAX_SPEED_RATIO_FACTOR: Final[float] = 2.0
"""Tolerated factor between the implied speed ``q/ρ`` and the reported speed.

Generous on purpose: ``ρ = o/g`` is a g-factor *estimate*, PeMS speed is a
time-mean (not space-mean) speed, and detector stations are not perfectly
calibrated — the repo's own fixtures sit at a median ratio of ≈ 1.4. The
mistakes it is meant to catch are order-of-magnitude: an hourly count read as
a 5-min count lands at ≈ 12, percent occupancy read as a fraction at ≈ 0.01.
"""

MAX_OUT_OF_RANGE_FRACTION: Final[float] = 0.01
"""Share of occupancy rows allowed above 100% before the load is refused."""

_MIN_CONSISTENCY_ROWS: Final[int] = 10
"""Rows with positive flow, density and speed needed to judge the q/ρ ratio."""

# Canonical field → default PeMS column spelling (matched case-insensitively).
_DEFAULT_COLUMNS: Final[dict[str, str]] = {
    "timestamp": "Timestamp",
    "station": "Station",
    "district": "District",
    "flow": "Flow",
    "occupancy": "Occupancy",
    "speed": "Speed",
}


def _check_occupancy_range(
    occ: pd.Series, *, occupancy_unit: str, max_out_of_range_fraction: float, path: str | Path
) -> None:
    """Refuse a file whose occupancy column is systematically above 100%.

    Only the upper breach is a unit signature. Negative occupancy is the
    classic failed-interval sentinel, not a mis-declared unit: those rows stay
    in the frame for ``calibration.fd_fit.fit_triangular_fd`` to drop, count
    under ``negative`` and refuse on when they dominate (module docstring) —
    refusing the whole file here would both hide the real cause behind a
    percent-unit message and defeat that accounting.
    """
    finite = occ[occ.notna()]
    if finite.empty:
        return
    bad = finite[finite > 1.0]
    if len(bad) > max_out_of_range_fraction * len(finite):
        raise ValueError(
            f"{path}: {len(bad)}/{len(finite)} occupancy rows are above 100% after "
            f"occupancy_unit={occupancy_unit!r} conversion (max {finite.max():.3g}); "
            f"part or all of the column may be in percent"
        )


def _check_speed_consistency(
    flow_veh_s: pd.Series,
    density_veh_m: pd.Series,
    speed_ms: pd.Series,
    *,
    max_speed_ratio_factor: float,
    interval_s: float,
    g_effective_length_m: float,
    path: str | Path,
) -> None:
    """Refuse a file whose ``q/ρ`` disagrees with the reported speed in scale.

    ``q = ρ v`` is an identity of the measurement, so the median of
    ``(q/ρ)/v`` over rows with positive flow, density and speed is ≈ 1 when
    ``interval_s`` and ``g_effective_length_m`` are right, and lands orders of
    magnitude away when either is wrong (module docstring).
    """
    usable = (
        ((flow_veh_s > 0.0) & (density_veh_m > 0.0) & (speed_ms > 0.0))
        & flow_veh_s.notna()
        & density_veh_m.notna()
        & speed_ms.notna()
    )
    if int(usable.sum()) < _MIN_CONSISTENCY_ROWS:
        return
    ratio = float(((flow_veh_s[usable] / density_veh_m[usable]) / speed_ms[usable]).median())
    if not 1.0 / max_speed_ratio_factor <= ratio <= max_speed_ratio_factor:
        implied = float((flow_veh_s[usable] / density_veh_m[usable]).median())
        reported = float(speed_ms[usable].median())
        raise ValueError(
            f"{path}: implied speed q/rho (median {implied:.1f} m/s) disagrees with the "
            f"reported speed (median {reported:.1f} m/s) by a factor of {ratio:.3g}, "
            f"beyond {max_speed_ratio_factor:g}x; check interval_s={interval_s:g} "
            f"(Flow must be a vehicle count per interval, not veh/h), "
            f"g_effective_length_m={g_effective_length_m:g} and occupancy_unit="
        )


def load_pems_station_csv(
    path: str | Path,
    *,
    g_effective_length_m: float = G_EFFECTIVE_LENGTH_DEFAULT_M,
    interval_s: float = PEMS_INTERVAL_S,
    speed_unit: Literal["mph", "kmh", "ms"] = "mph",
    occupancy_unit: Literal["fraction", "percent"] = "fraction",
    column_map: dict[str, str] | None = None,
    max_speed_ratio_factor: float | None = MAX_SPEED_RATIO_FACTOR,
    max_out_of_range_fraction: float = MAX_OUT_OF_RANGE_FRACTION,
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
        max_speed_ratio_factor: Tolerated factor between the implied speed
            ``q/ρ`` and the reported speed (module docstring); ``None``
            disables the cross-check, e.g. for an export whose speed column
            is known to be unusable.
        max_out_of_range_fraction: Share of occupancy rows allowed above 100%
            (after the declared conversion) before the load is refused.

    Returns:
        DataFrame with columns ``timestamp`` (as read), ``station`` (str),
        ``flow_veh_s`` [veh/s], ``occupancy`` (fraction), ``density_veh_m``
        [veh/m, estimated — see module docstring] and ``speed_ms`` [m/s].
        Includes ``district`` when present in the source.

    Raises:
        ValueError: On missing required columns, invalid parameters, or a
            systematic unit mistake caught by either guard above.
    """
    if g_effective_length_m <= 0:
        raise ValueError(f"g_effective_length_m must be > 0, got {g_effective_length_m}")
    if interval_s <= 0:
        raise ValueError(f"interval_s must be > 0, got {interval_s}")
    if max_speed_ratio_factor is not None and max_speed_ratio_factor <= 1.0:
        raise ValueError(f"max_speed_ratio_factor must be > 1, got {max_speed_ratio_factor}")
    if not 0.0 <= max_out_of_range_fraction <= 1.0:
        raise ValueError(
            f"max_out_of_range_fraction must be in [0, 1], got {max_out_of_range_fraction}"
        )

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
    _check_occupancy_range(
        occ,
        occupancy_unit=occupancy_unit,
        max_out_of_range_fraction=max_out_of_range_fraction,
        path=path,
    )
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
    if max_speed_ratio_factor is not None:
        _check_speed_consistency(
            out["flow_veh_s"],
            out["density_veh_m"],
            out["speed_ms"],
            max_speed_ratio_factor=max_speed_ratio_factor,
            interval_s=interval_s,
            g_effective_length_m=g_effective_length_m,
            path=path,
        )
    district_col = find("district")
    if district_col is not None:
        out["district"] = raw[district_col]
    return out
