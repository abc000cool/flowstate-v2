"""Observed corridor detector data, as the validation tier reads it.

A corridor's measurements arrive as a ``flowstate.observations/1`` artifact
(docs/CONTRACTS.md, "Detector observations"): a fixed grid of ``n_windows``
analysis windows starting at ``t0_local``, one flow and one speed series per
station, NaN wherever nothing was measured. :class:`ObservedCorridor` is the
read-only view of that artifact this package needs, and
:func:`score_run_against_observed` turns one simulated replicate plus that
view into the two FHWA-style comparison statistics of CLAUDE.md §7.1 — GEH on
hourly link flows and RMSPE on segment speeds — together with the counts
behind them. Every GEH comes with its labelled station-hour
(:class:`LinkHourRecord`: station, position, hour, local clock, observed and
simulated volume), and :func:`pool_link_hours` summarises those per
station-hour over the replicates.

The artifact is parsed with the standard library only: ``validation`` is the
credibility core and must stay importable without the calibration stack that
*writes* observations (:mod:`calibration.observations`). The two agree on the
file, not on an import.

Three rules the module exists to keep:

* **Simulation time zero is ``t0_local``.** Observation window ``k`` starts at
  ``k · window_s`` in simulation time; nothing here rescales or shifts time.
* **The warm-up is not scored.** Only windows lying wholly inside
  ``[warmup_s, duration_s)`` are compared (:meth:`ObservedCorridor.analysis_windows`),
  the same measurement window :func:`validation.metrics.compute_metrics` uses.
* **Missing is NaN, never a number.** A NaN observation is skipped and
  counted — ``n_link_hours``, ``n_speed_cells`` and
  :meth:`ObservedCorridor.coverage` say how much of the grid was actually
  compared — and is never imputed.
"""

from __future__ import annotations

import itertools
import json
import math
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from flowstate_core.units import s_to_h
from validation.metrics import (
    crossing_speeds,
    link_hour_geh,
    n_window_rows,
    rmspe,
    time_ordered,
    time_window_rows,
)

FloatArray = NDArray[np.float64]

#: Schema tag every observations artifact carries (docs/CONTRACTS.md).
OBSERVATIONS_SCHEMA: Final[str] = "flowstate.observations/1"

#: Station kind that carries mainline through-flow — the only kind a link-flow
#: or segment-speed comparison is formed from (ramps are demand, not corridor
#: cross-sections).
MAINLINE: Final[str] = "mainline"

#: Seconds in one hour, derived from the unit helpers rather than written as a
#: magic number (CLAUDE.md §2): 1 h is ``s_to_h(1)`` hours per second.
_S_PER_HOUR: Final[float] = 1.0 / s_to_h(1.0)

#: Position/float comparison tolerance [m] / [s].
_TOL: Final[float] = 1e-6

#: Extent [m] of a station's loop in the trajectories' front-bumper
#: coordinates: a vehicle stands over the loop at ``x_ref`` while its front
#: bumper lies in ``[x_ref, x_ref + LOOP_OCCUPANCY_ZONE_M)`` — it has crossed
#: the loop and its body still covers it. The fleet's passenger-car length
#: (``microsim.vehicles.VEHICLE_LENGTH_M``, 5 m; a test pins the two
#: together); the loop's own length is left out, so a longer vehicle (a
#: truck) is counted over a shorter stretch than its body covers — the rule
#: can miss an occupied loop, never invent one.
LOOP_OCCUPANCY_ZONE_M: Final[float] = 5.0


def _series(raw: Mapping[str, Any] | None, station_id: str, n_windows: int) -> FloatArray:
    """One station's per-window series as floats, JSON ``null`` → NaN.

    Args:
        raw: The artifact's series mapping (station id → list), or None.
        station_id: Station whose series to read; absent means all-NaN.
        n_windows: Expected length.

    Returns:
        Array of length ``n_windows``.

    Raises:
        ValueError: The stored series has a different length.
    """
    values = (raw or {}).get(station_id)
    if values is None:
        return np.full(n_windows, np.nan, dtype=np.float64)
    out = np.asarray(
        [math.nan if v is None else float(v) for v in values],
        dtype=np.float64,
    )
    if out.size != n_windows:
        raise ValueError(
            f"station {station_id!r}: series holds {out.size} values, expected {n_windows}"
        )
    return out


@dataclass(frozen=True)
class ObservedStation:
    """One detector station of an observations artifact.

    Attributes:
        id: Stable station id — the key of every series.
        x_m: Position along the corridor [m] (NaN when the artifact stated
            none; such a station takes part in no comparison).
        lanes: Mainline lanes at the station (0 = not stated).
        kind: ``mainline``, ``on_ramp`` or ``off_ramp``.
        label: Human-readable location, for report provenance.
    """

    id: str
    x_m: float
    lanes: int = 0
    kind: str = MAINLINE
    label: str = ""

    @property
    def positioned(self) -> bool:
        """Whether the station has a usable corridor position."""
        return math.isfinite(self.x_m)


@dataclass(frozen=True)
class ObservedCoverage:
    """How much of the observed grid carries a measurement.

    Attributes:
        n_stations: Mainline stations with a position (the comparison set).
        n_windows: Windows in the artifact.
        flow_fraction: Share of station-windows with a finite flow.
        speed_fraction: Share of station-windows with a finite speed.
    """

    n_stations: int
    n_windows: int
    flow_fraction: float
    speed_fraction: float


#: Decimals GEH is stored with in every JSON form (``observed_scores.json``,
#: the battery artifact's ``geh.pooled_values`` and its link-hour tables), so a
#: table row and the pooled list carry the identical number.
GEH_DECIMALS: Final[int] = 4

#: Clock formats ``t0_local`` is accepted in (the observations contract's
#: ``"HH:MM"`` / ``"HH:MM:SS"``).
_CLOCK_FORMATS: Final[tuple[str, ...]] = ("%H:%M:%S", "%H:%M")


def clock_label(t0_local: str, offset_s: float) -> str:
    """Local wall-clock label of simulation time ``offset_s``.

    Simulation ``t = 0`` is the observations' ``t0_local``; the label is that
    clock time advanced by ``offset_s`` (wrapping past midnight). It is a
    label for a reader, never an input to a statistic, so an artifact whose
    ``t0_local`` is not a clock time yields an empty label rather than an
    error.

    Args:
        t0_local: ``"HH:MM"`` or ``"HH:MM:SS"`` (the observations artifact's
            ``t0_local``).
        offset_s: Simulation time [s].

    Returns:
        ``"HH:MM"`` (``"HH:MM:SS"`` when the seconds are not zero), or ``""``
        when ``t0_local`` is not a clock time or ``offset_s`` is not finite.
    """
    if not math.isfinite(offset_s):
        return ""
    text = str(t0_local).strip()
    for fmt in _CLOCK_FORMATS:
        try:
            start = datetime.strptime(text, fmt)
        except ValueError:
            continue
        moment = start + timedelta(seconds=float(offset_s))
        return moment.strftime("%H:%M:%S" if moment.second else "%H:%M")
    return ""


@dataclass(frozen=True)
class LinkHourRecord:
    """One compared station-hour of the link-flow criterion, labelled.

    The row behind one entry of :attr:`ObservedScores.geh_values`: the same
    position in :attr:`ObservedScores.link_hours` holds the station, the hour
    and both volumes the GEH was formed from, so the simulated count is read,
    never recovered by inverting the GEH.

    Attributes:
        station: Station id (the observations artifact's ``stations[].id``).
        x_ref_m: The station's position in the observations' coordinates [m];
            the simulated cross-section is ``x_ref_m + x_offset_m``.
        window_start_s: Start of the hour in simulation time, i.e. seconds
            from the observations' ``t0_local`` [s]; the hour is
            ``[window_start_s, window_start_s + 3600)``.
        clock: Local clock time of ``window_start_s`` (:func:`clock_label`),
            empty when ``t0_local`` is not a clock time.
        obs_veh_h: Observed hourly volume [veh/h] — the mean of the hour's
            windows (:meth:`ObservedCorridor.hourly_link_flows`).
        sim_veh_h: Simulated crossings of the cross-section in the hour
            [veh/h] (:func:`validation.metrics.link_hour_geh` ``.sim_veh_h``;
            over one hour the count itself).
        geh: ``validation.metrics.geh(sim_veh_h, obs_veh_h)`` — the value at
            this record's position in :attr:`ObservedScores.geh_values`.
    """

    station: str
    x_ref_m: float
    window_start_s: float
    clock: str
    obs_veh_h: float
    sim_veh_h: float
    geh: float

    def to_dict(self) -> dict[str, Any]:
        """JSON form; GEH at :data:`GEH_DECIMALS`, the volumes unrounded."""
        return {
            "station": self.station,
            "x_ref_m": self.x_ref_m,
            "window_start_s": self.window_start_s,
            "clock": self.clock,
            "obs_veh_h": self.obs_veh_h,
            "sim_veh_h": self.sim_veh_h,
            "geh": round(self.geh, GEH_DECIMALS),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> LinkHourRecord:
        """Rebuild from :meth:`to_dict`."""
        return cls(
            station=str(raw["station"]),
            x_ref_m=float(raw["x_ref_m"]),
            window_start_s=float(raw["window_start_s"]),
            clock=str(raw.get("clock") or ""),
            obs_veh_h=float(raw["obs_veh_h"]),
            sim_veh_h=float(raw["sim_veh_h"]),
            geh=float(raw["geh"]),
        )


@dataclass(frozen=True)
class PooledLinkHour:
    """One station-hour summarised over the replicates (:func:`pool_link_hours`).

    Attributes:
        station: Station id.
        x_ref_m: Station position in the observations' coordinates [m].
        window_start_s: Hour start, seconds from ``t0_local`` [s].
        clock: Local clock label of the hour start.
        obs_veh_h: Observed hourly volume [veh/h] (one artifact, so one value).
        n_seeds: Replicates that compared this station-hour.
        sim_veh_h_mean: Mean simulated volume over those replicates [veh/h].
        sim_veh_h_min: Smallest simulated volume [veh/h].
        sim_veh_h_max: Largest simulated volume [veh/h].
        geh_mean: Mean GEH over those replicates, from the GEH values as
            stored (:data:`GEH_DECIMALS`), so it is recomputable from the
            per-seed tables.
        geh_min: Smallest GEH.
        geh_max: Largest GEH.
    """

    station: str
    x_ref_m: float
    window_start_s: float
    clock: str
    obs_veh_h: float
    n_seeds: int
    sim_veh_h_mean: float
    sim_veh_h_min: float
    sim_veh_h_max: float
    geh_mean: float
    geh_min: float
    geh_max: float

    def to_dict(self) -> dict[str, Any]:
        """JSON form; GEH statistics at :data:`GEH_DECIMALS`."""
        return {
            "station": self.station,
            "x_ref_m": self.x_ref_m,
            "window_start_s": self.window_start_s,
            "clock": self.clock,
            "obs_veh_h": self.obs_veh_h,
            "n_seeds": self.n_seeds,
            "sim_veh_h_mean": self.sim_veh_h_mean,
            "sim_veh_h_min": self.sim_veh_h_min,
            "sim_veh_h_max": self.sim_veh_h_max,
            "geh_mean": round(self.geh_mean, GEH_DECIMALS),
            "geh_min": round(self.geh_min, GEH_DECIMALS),
            "geh_max": round(self.geh_max, GEH_DECIMALS),
        }


@dataclass(frozen=True)
class ObservedScores:
    """One replicate scored against an :class:`ObservedCorridor`.

    Attributes:
        geh_values: GEH per compared station-hour (link-flow criterion).
        n_link_hours: Number of station-hours compared (``len(geh_values)``).
        rmspe: Segment-speed RMSPE as a fraction; NaN when no cell could be
            compared.
        n_speed_cells: Number of (window, segment) cells behind ``rmspe``.
        segment_speeds_sim: Simulated mean speed ``[window][segment]`` [m/s]
            over the analysed windows, NaN where no vehicle was sampled.
        segment_speeds_obs: The observed matrix on the same bins and windows.
        windows: Indices of the observation windows the matrices cover.
        n_stations_outside_span: Mainline stations excluded because their
            cross-section lies outside the replicate's own position span.
        stations_outside_span: The ids of those stations, in position order.
        link_hours: One :class:`LinkHourRecord` per entry of ``geh_values``,
            in the same order (station by position, then hour). ``None`` only
            for scores read back from an ``observed_scores.json`` written
            before the table existed; a replicate scored now carries it, empty
            when no station-hour was compared. Its hours are aligned to
            ``t0_local`` (the run's start, warm-up included) and kept only
            when they lie wholly after the warm-up.
        link_hours_anchored: The same comparison on hours anchored at the
            study period's start — the first analysed window, the warm-up's
            end (docs/FRISCO_PROTOCOL.md §4, C1) — so the whole study period
            is scored (``hour_anchor_s`` is that start). Additive: it changes
            neither ``geh_values`` nor ``link_hours``. ``None`` for scores
            stored before it existed.
        hour_anchor_s: Start of the first anchored hour [s] (simulation
            time); ``None`` with ``link_hours_anchored``.
        station_point_speeds_sim: The speed a loop detector at each scored
            station would read, ``[window][station]`` [m/s] over ``windows``:
            the mean speed of the vehicles crossing the station's position in
            the window, each crossing interpolated between consecutive
            trajectory samples (:func:`validation.metrics.crossing_speeds`;
            a time-mean over every lane's vehicles). In a window no vehicle
            crosses, the mean sampled speed of the vehicles standing over the
            loop (:data:`LOOP_OCCUPANCY_ZONE_M`) — about zero in a stopped
            queue, where an occupied loop counts nothing and its
            count-over-occupancy speed is zero — and NaN only where the loop
            is empty (``station_point_standstill``). Docs/FRISCO_PROTOCOL.md
            §5's virtual detector, beside the segment means; ``None`` for
            scores stored before it existed.
        station_point_counts_sim: The crossings behind each point speed (zero
            in a window read from the vehicles standing over the loop).
        station_point_standstill: True when the point speeds follow the
            standstill rule above (every replicate scored since 2026-10-07);
            False for scores stored before it, whose window without a
            crossing is NaN even where vehicles stood over the loop.

    Raises:
        ValueError: ``link_hours`` is given and holds a different number of
            rows than ``geh_values`` holds values, or the point-speed
            matrices disagree in shape with each other.
    """

    geh_values: tuple[float, ...]
    n_link_hours: int
    rmspe: float
    n_speed_cells: int
    segment_speeds_sim: tuple[tuple[float, ...], ...]
    segment_speeds_obs: tuple[tuple[float, ...], ...]
    windows: tuple[int, ...]
    n_stations_outside_span: int = 0
    stations_outside_span: tuple[str, ...] = ()
    link_hours: tuple[LinkHourRecord, ...] | None = None
    link_hours_anchored: tuple[LinkHourRecord, ...] | None = None
    hour_anchor_s: float | None = None
    station_point_speeds_sim: tuple[tuple[float, ...], ...] | None = None
    station_point_counts_sim: tuple[tuple[int, ...], ...] | None = None
    station_point_standstill: bool = False

    def __post_init__(self) -> None:
        if self.link_hours is not None and len(self.link_hours) != len(self.geh_values):
            raise ValueError(
                f"link_hours holds {len(self.link_hours)} rows for "
                f"{len(self.geh_values)} GEH values; the table labels geh_values row by row"
            )
        speeds, counts = self.station_point_speeds_sim, self.station_point_counts_sim
        if (speeds is None) != (counts is None) or (
            speeds is not None
            and counts is not None
            and [len(r) for r in speeds] != [len(r) for r in counts]
        ):
            raise ValueError("station_point_speeds_sim and station_point_counts_sim disagree")

    def to_dict(self) -> dict[str, Any]:
        """JSON form (NaN written as ``null``) for a per-seed artifact.

        The keys of the anchored hours and of the point speeds are written
        only when the scores carry them, so scores read back from an older
        file write the same keys they were read with.
        """

        def safe(value: float) -> float | None:
            return None if not math.isfinite(value) else float(value)

        out: dict[str, Any] = {
            "geh_values": [round(g, GEH_DECIMALS) for g in self.geh_values],
            "n_link_hours": self.n_link_hours,
            "rmspe": safe(self.rmspe),
            "n_speed_cells": self.n_speed_cells,
            "windows": list(self.windows),
            "segment_speeds_sim": [[safe(v) for v in row] for row in self.segment_speeds_sim],
            "segment_speeds_obs": [[safe(v) for v in row] for row in self.segment_speeds_obs],
            "n_stations_outside_span": self.n_stations_outside_span,
            "stations_outside_span": list(self.stations_outside_span),
            "link_hours": (
                None if self.link_hours is None else [r.to_dict() for r in self.link_hours]
            ),
        }
        if self.link_hours_anchored is not None:
            out["hour_anchor_s"] = self.hour_anchor_s
            out["link_hours_anchored"] = [r.to_dict() for r in self.link_hours_anchored]
        if self.station_point_speeds_sim is not None and self.station_point_counts_sim is not None:
            out["station_point_speeds_sim"] = [
                [safe(v) for v in row] for row in self.station_point_speeds_sim
            ]
            out["station_point_counts_sim"] = [
                [int(n) for n in row] for row in self.station_point_counts_sim
            ]
            if self.station_point_standstill:
                out["station_point_standstill"] = True
        return out

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ObservedScores:
        """Rebuild from :meth:`to_dict` (``--criteria-only`` rescoring).

        A file written before the link-hour table existed has no
        ``link_hours`` key; it loads with ``link_hours = None``. Likewise a
        file without the anchored hours or the point speeds loads with
        those fields ``None``, and one without ``station_point_standstill``
        with that flag False.
        """

        def rows(key: str) -> tuple[tuple[float, ...], ...]:
            return tuple(
                tuple(math.nan if v is None else float(v) for v in row) for row in raw.get(key, ())
            )

        value = raw.get("rmspe")
        table = raw.get("link_hours")
        anchored = raw.get("link_hours_anchored")
        anchor = raw.get("hour_anchor_s")
        points = raw.get("station_point_speeds_sim")
        counts = raw.get("station_point_counts_sim")
        return cls(
            geh_values=tuple(float(g) for g in raw.get("geh_values", ())),
            n_link_hours=int(raw["n_link_hours"]),
            rmspe=math.nan if value is None else float(value),
            n_speed_cells=int(raw["n_speed_cells"]),
            segment_speeds_sim=rows("segment_speeds_sim"),
            segment_speeds_obs=rows("segment_speeds_obs"),
            windows=tuple(int(k) for k in raw.get("windows", ())),
            n_stations_outside_span=int(raw.get("n_stations_outside_span", 0)),
            stations_outside_span=tuple(str(s) for s in raw.get("stations_outside_span", ())),
            link_hours=(
                None if table is None else tuple(LinkHourRecord.from_dict(r) for r in table)
            ),
            link_hours_anchored=(
                None if anchored is None else tuple(LinkHourRecord.from_dict(r) for r in anchored)
            ),
            hour_anchor_s=None if anchored is None or anchor is None else float(anchor),
            station_point_speeds_sim=(
                None if points is None or counts is None else rows("station_point_speeds_sim")
            ),
            station_point_counts_sim=(
                None
                if points is None or counts is None
                else tuple(tuple(int(n) for n in row) for row in counts)
            ),
            station_point_standstill=(
                points is not None
                and counts is not None
                and bool(raw.get("station_point_standstill", False))
            ),
        )


#: Key under an observations artifact's ``context`` that carries the corridor's
#: detector-estimated backward wave speed (``calibration.waves_observed``).
WAVE_SPEED_CONTEXT_KEY: Final[str] = "detector_wave_speed"


@dataclass(frozen=True)
class DetectorWaveSpeed:
    """A corridor's observed backward wave speed, read from an artifact.

    The estimate is made by the calibration side
    (``calibration.waves_observed.detector_wave_speed``: the lag of the
    normalised cross-correlation peak between adjacent stations' 30-second
    speed series, divided into the station spacing) and travels in the
    artifact's ``context`` block. This package reads it and prints it; it
    scores nothing with it. The corridor's real wave speed is a property of
    the corridor, not a target the model must hit, so it is context in the
    report's observed-data block and never a criterion row (CLAUDE.md §7.1).

    Attributes:
        median_kmh: Median over the station pairs that yielded an estimate
            [km/h]; NaN when none did.
        iqr_kmh: ``(q25, q75)`` of those pairs [km/h]; NaN when none.
        n_pairs: Adjacent mainline station pairs examined.
        n_used: Pairs that yielded an estimate.
        rejections: Why the others did not, as ``"3 reason, 2 other reason"``;
            empty when every pair was used.
        loo_n_dates: Dates the leave-one-date-out sensitivity covered; 0 when
            the artifact carries none.
        loo_median_min_kmh: Smallest median over the subsets that leave one
            date out [km/h]; NaN when the artifact carries none.
        loo_median_max_kmh: Largest such median [km/h]; NaN likewise.
        loo_pairs_min: Fewest pairs any of those subsets kept; 0 when the
            artifact carries none. A median that rests on five pairs on some
            days is a different number from one that always rests on ten, and
            the report says so beside the headline.
    """

    median_kmh: float
    iqr_kmh: tuple[float, float]
    n_pairs: int
    n_used: int
    rejections: str = ""
    loo_n_dates: int = 0
    loo_median_min_kmh: float = math.nan
    loo_median_max_kmh: float = math.nan
    loo_pairs_min: int = 0

    @property
    def has_loo(self) -> bool:
        """Whether a usable leave-one-date-out range came with the artifact."""
        return (
            self.loo_n_dates > 1
            and math.isfinite(self.loo_median_min_kmh)
            and math.isfinite(self.loo_median_max_kmh)
        )

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> DetectorWaveSpeed | None:
        """Read the estimate out of an artifact's ``context`` block.

        Args:
            context: The artifact's ``context`` mapping (possibly empty).

        Returns:
            The estimate, or None when the artifact carries none or carries
            one this version cannot read. A context block written by another
            version is not a reason to fail a report: the line is simply not
            printed.
        """
        raw = context.get(WAVE_SPEED_CONTEXT_KEY)
        if not isinstance(raw, Mapping):
            return None
        try:
            n_pairs = int(raw["n_pairs"])
            n_used = int(raw["n_used"])
            median = _optional_float(raw.get("median_kmh"))
            iqr_raw = raw.get("iqr_kmh") or (None, None)
            iqr = (_optional_float(iqr_raw[0]), _optional_float(iqr_raw[1]))
            rejected = {str(k): int(v) for k, v in (raw.get("rejected") or {}).items()}
            loo = raw.get("leave_one_date_out")
            n_dates = int(loo["n_dates"]) if isinstance(loo, Mapping) else 0
            loo_min = _optional_float(raw.get("loo_median_min_kmh"))
            loo_max = _optional_float(raw.get("loo_median_max_kmh"))
            pairs_min = int(raw.get("loo_pairs_min") or 0)
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        if n_used > 0 and not math.isfinite(median):
            return None
        return cls(
            median_kmh=median,
            iqr_kmh=iqr,
            n_pairs=n_pairs,
            n_used=n_used,
            rejections=", ".join(f"{n} {reason}" for reason, n in sorted(rejected.items())),
            loo_n_dates=n_dates,
            loo_median_min_kmh=loo_min,
            loo_median_max_kmh=loo_max,
            loo_pairs_min=pairs_min,
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON form (NaN written as ``null``)."""
        return {
            "median_kmh": _optional_number(self.median_kmh),
            "iqr_kmh": [_optional_number(v) for v in self.iqr_kmh],
            "n_pairs": self.n_pairs,
            "n_used": self.n_used,
            "rejections": self.rejections,
            "loo_n_dates": self.loo_n_dates,
            "loo_median_min_kmh": _optional_number(self.loo_median_min_kmh),
            "loo_median_max_kmh": _optional_number(self.loo_median_max_kmh),
            "loo_pairs_min": self.loo_pairs_min,
        }


def _optional_float(value: Any) -> float:
    """``None`` → NaN, anything numeric → float."""
    return math.nan if value is None else float(value)


def _optional_number(value: float) -> float | None:
    """NaN → ``None`` (JSON has no NaN)."""
    return float(value) if math.isfinite(value) else None


@dataclass(frozen=True)
class ObservedProvenance:
    """What the report prints about the observed side of a comparison.

    Every field is computed from the artifact and the scoring, never typed by
    a caller (CLAUDE.md §7.4).

    Attributes:
        path: Artifact path as the caller named it.
        corridor: Corridor id recorded in the artifact.
        provider: ``source.provider`` (empty when the artifact states none).
        dates: ``source.dates`` joined, or empty.
        url: ``source.url``, or empty.
        aggregation: How the dates were combined into the profile.
        t0_local: Local wall-clock time simulation ``t = 0`` corresponds to.
        window_s: Observation window length [s].
        n_stations: Mainline stations with a position.
        n_windows: Windows in the artifact.
        n_windows_compared: Windows inside the scored measurement window.
        flow_fraction: Share of station-windows with a finite flow.
        speed_fraction: Share of station-windows with a finite speed.
        n_link_hours: Station-hours compared, pooled over replicates.
        n_speed_cells: Speed cells compared, pooled over replicates.
        n_replicates: Replicates scored against the artifact.
        n_stations_outside_span: Mainline stations excluded from both
            comparisons because their cross-section lies outside the
            simulated position span.
        stations_outside_span: The ids of those stations, comma-joined.
        note: Why no comparison was formed, when none was; empty otherwise.
        wave_speed: The corridor's detector-estimated backward wave speed
            when the artifact carries one (:class:`DetectorWaveSpeed`) —
            context for the reader, scored by nothing.
    """

    path: str
    corridor: str
    provider: str
    dates: str
    url: str
    aggregation: str
    t0_local: str
    window_s: float
    n_stations: int
    n_windows: int
    n_windows_compared: int
    flow_fraction: float
    speed_fraction: float
    n_link_hours: int
    n_speed_cells: int
    n_replicates: int
    n_stations_outside_span: int = 0
    stations_outside_span: str = ""
    note: str = ""
    wave_speed: DetectorWaveSpeed | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON form (the API's report row, the battery artifact)."""
        return {
            "path": self.path,
            "corridor": self.corridor,
            "provider": self.provider,
            "dates": self.dates,
            "url": self.url,
            "aggregation": self.aggregation,
            "t0_local": self.t0_local,
            "window_s": self.window_s,
            "n_stations": self.n_stations,
            "n_windows": self.n_windows,
            "n_windows_compared": self.n_windows_compared,
            "flow_fraction": self.flow_fraction,
            "speed_fraction": self.speed_fraction,
            "n_link_hours": self.n_link_hours,
            "n_speed_cells": self.n_speed_cells,
            "n_replicates": self.n_replicates,
            "n_stations_outside_span": self.n_stations_outside_span,
            "stations_outside_span": self.stations_outside_span,
            "note": self.note,
            "detector_wave_speed": None if self.wave_speed is None else self.wave_speed.to_dict(),
        }


@dataclass(frozen=True)
class ObservedCorridor:
    """Read-only view of a ``flowstate.observations/1`` artifact.

    Attributes:
        corridor: Corridor id.
        source: Provenance mapping (provider, dates, url, fetched_at, …).
        window_s: Window length [s]; window ``k`` starts at ``k · window_s``
            in simulation time.
        n_windows: Number of windows.
        t0_local: Local wall-clock time of simulation ``t = 0``.
        duration_s: Observed span [s] (``n_windows · window_s``).
        aggregation: How the fetched dates were combined.
        stations: Every station in the artifact, artifact order.
        flows_veh_h: Station id → per-window flow [veh/h], NaN = not observed.
        speeds_ms: Station id → per-window mean speed [m/s], NaN likewise.
        quality: Station id → the loader's quality block.
        path: Where the artifact was read from (provenance only).
        context: The artifact's optional ``context`` block — computed
            statements about the corridor that are not per-window
            measurements (currently the detector-estimated wave speed). Read
            and printed, never scored.
    """

    corridor: str
    source: dict[str, Any]
    window_s: float
    n_windows: int
    t0_local: str
    duration_s: float
    aggregation: str
    stations: tuple[ObservedStation, ...]
    flows_veh_h: dict[str, FloatArray]
    speeds_ms: dict[str, FloatArray]
    quality: dict[str, dict[str, float]]
    path: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, path: str = "") -> ObservedCorridor:
        """Build from the artifact's JSON payload.

        Args:
            raw: Parsed artifact object.
            path: Where it came from (recorded for provenance only).

        Returns:
            The view.

        Raises:
            ValueError: Wrong ``schema``, a non-positive ``window_s``, a
                window count inconsistent with ``duration_s``, a series of the
                wrong length, two stations sharing an id (the flow and speed
                series are keyed by it, so one of them would be read as the
                other's), or two mainline stations at the same position
                (which would make a link-flow comparison ambiguous).
        """
        schema = str(raw.get("schema", ""))
        if schema != OBSERVATIONS_SCHEMA:
            raise ValueError(f"expected schema {OBSERVATIONS_SCHEMA!r}, got {schema!r}")
        window_s = float(raw["window_s"])
        if window_s <= 0.0:
            raise ValueError(f"window_s must be > 0, got {window_s}")
        duration_s = float(raw["duration_s"])
        n_windows = int(raw.get("n_windows", round(duration_s / window_s)))
        if n_windows < 1 or abs(n_windows * window_s - duration_s) > _TOL:
            raise ValueError(
                f"n_windows ({n_windows}) x window_s ({window_s}) does not match "
                f"duration_s ({duration_s})"
            )

        stations: list[ObservedStation] = []
        for entry in raw.get("stations", ()):
            ident = entry.get("id", entry.get("station"))
            if ident in (None, ""):
                raise ValueError(f"station entry has no id: keys {sorted(entry)}")
            x_raw = entry.get("x_m")
            lanes = entry.get("lanes")
            stations.append(
                ObservedStation(
                    id=str(ident),
                    x_m=math.nan if x_raw is None else float(x_raw),
                    lanes=0 if lanes is None else int(lanes),
                    kind=str(entry.get("kind") or MAINLINE),
                    label=str(entry.get("label") or ""),
                )
            )

        seen: set[str] = set()
        for station in stations:
            if station.id in seen:
                raise ValueError(
                    f"station id {station.id!r} appears twice; ids must be unique because "
                    "the flow and speed series are keyed by them"
                )
            seen.add(station.id)

        flows = {s.id: _series(raw.get("flows_veh_h"), s.id, n_windows) for s in stations}
        speeds = {s.id: _series(raw.get("speeds_ms"), s.id, n_windows) for s in stations}
        obs = cls(
            corridor=str(raw.get("corridor", "")),
            source=dict(raw.get("source") or {}),
            window_s=window_s,
            n_windows=n_windows,
            t0_local=str(raw.get("t0_local", "")),
            duration_s=duration_s,
            aggregation=str(raw.get("aggregation", "")),
            stations=tuple(stations),
            flows_veh_h=flows,
            speeds_ms=speeds,
            quality={k: dict(v) for k, v in (raw.get("quality") or {}).items()},
            path=path,
            context=dict(raw.get("context") or {}),
        )
        positions = obs.mainline_x_refs()
        for a, b in itertools.pairwise(positions):
            if abs(b - a) <= _TOL:
                raise ValueError(
                    f"two mainline stations share the position x = {a} m; a link-flow "
                    "comparison could not tell them apart"
                )
        return obs

    @classmethod
    def from_json(cls, path: str | Path) -> ObservedCorridor:
        """Read an observations artifact from disk."""
        target = Path(path)
        return cls.from_dict(json.loads(target.read_text()), path=str(target))

    # -- geometry -----------------------------------------------------------

    def mainline_stations(self) -> tuple[ObservedStation, ...]:
        """Positioned mainline stations, ordered by position.

        Ramp stations and stations without an ``x_m`` take part in no
        comparison: a ramp is demand, not a corridor cross-section, and an
        unpositioned station cannot be matched to a simulated one.
        """
        usable = [s for s in self.stations if s.kind == MAINLINE and s.positioned]
        return tuple(sorted(usable, key=lambda s: s.x_m))

    def mainline_x_refs(self) -> list[float]:
        """Positions [m] of :meth:`mainline_stations`, in the same order."""
        return [s.x_m for s in self.mainline_stations()]

    def segment_bins(self) -> list[tuple[float, float]]:
        """One segment per mainline station: the span to its neighbours' midpoints.

        The outer half-spans mirror the adjacent spacing, so the corridor is
        tiled without gaps or overlaps and every station owns the stretch it
        is the best measurement of.

        Returns:
            ``(x_start_m, x_end_m)`` per station, ordered by position.

        Raises:
            ValueError: Fewer than two positioned mainline stations — a
                single station defines no spacing and therefore no segment.
        """
        xs = self.mainline_x_refs()
        if len(xs) < 2:
            raise ValueError(
                f"segment bins need at least two positioned mainline stations, got {len(xs)}"
            )
        mids = [0.5 * (a + b) for a, b in itertools.pairwise(xs)]
        lo = [xs[0] - (mids[0] - xs[0]), *mids]
        hi = [*mids, xs[-1] + (xs[-1] - mids[-1])]
        return list(zip(lo, hi, strict=True))

    # -- windows ------------------------------------------------------------

    def analysis_windows(self, warmup_s: float, duration_s: float) -> list[int]:
        """Window indices lying wholly inside ``[warmup_s, duration_s)``.

        The run's configured warm-up is discarded from every metric, so it is
        discarded from the observed comparison too; a window the run does not
        cover to its end is dropped rather than compared against a partial
        simulation.

        Args:
            warmup_s: Run warm-up [s] (simulation time).
            duration_s: End of the run's recorded span [s].

        Returns:
            Window indices in increasing order (possibly empty).
        """
        return [
            k
            for k in range(self.n_windows)
            if k * self.window_s >= warmup_s - _TOL and (k + 1) * self.window_s <= duration_s + _TOL
        ]

    def hourly_link_flows(self, anchor_window: int = 0) -> pd.DataFrame:
        """Hour-aligned observed volumes per station, ready for GEH.

        GEH is only meaningful on hourly volumes, so the sub-hourly windows
        are aggregated: the vehicles counted in the ``3600 / window_s``
        windows of one hour are summed, which is the mean of those windows'
        ``veh/h`` values. Hours are aligned to window ``anchor_window`` —
        by default window 0, i.e. ``t0_local``; the study protocol's C1
        anchors them at the study period's start, the warm-up's end
        (docs/FRISCO_PROTOCOL.md §4, C1; :func:`score_run_against_observed`)
        — and are reported **only when every window in the hour is valid at
        that station** — a partly observed hour would be a lower-bound volume
        compared as if it were a count.

        Args:
            anchor_window: Index of the window the first hour starts at
                (``0 <= anchor_window < n_windows``).

        Returns:
            Rows ``(x_ref_m, window_start_s, flow_veh_h, station)``, one per
            fully observed station-hour, in station then time order.
            ``window_start_s`` is simulation time.

        Raises:
            ValueError: ``window_s`` does not divide one hour, or the anchor
                lies outside the grid.
        """
        per_hour = round(_S_PER_HOUR / self.window_s)
        if per_hour < 1 or abs(per_hour * self.window_s - _S_PER_HOUR) > _TOL:
            raise ValueError(
                f"window_s = {self.window_s} s does not divide one hour; hourly volumes "
                "cannot be formed"
            )
        if not 0 <= anchor_window < max(self.n_windows, 1):
            raise ValueError(
                f"anchor_window {anchor_window} lies outside the {self.n_windows}-window grid"
            )
        x_ref: list[float] = []
        starts: list[float] = []
        flows: list[float] = []
        names: list[str] = []
        for station in self.mainline_stations():
            series = self.flows_veh_h[station.id]
            for k0 in range(anchor_window, self.n_windows - per_hour + 1, per_hour):
                block = series[k0 : k0 + per_hour]
                if not bool(np.all(np.isfinite(block))):
                    continue
                x_ref.append(station.x_m)
                starts.append(k0 * self.window_s)
                flows.append(float(block.mean()))
                names.append(station.id)
        return pd.DataFrame(
            {
                "x_ref_m": x_ref,
                "window_start_s": starts,
                "flow_veh_h": flows,
                "station": names,
            }
        )

    def speed_matrix(self, windows: slice | None = None) -> FloatArray:
        """Observed mean speeds ``[window][station]`` [m/s], NaN = not observed.

        Args:
            windows: Optional row slice (e.g. the analysed windows); ``None``
                returns every window.

        Returns:
            Array of shape ``(n_selected_windows, n_mainline_stations)`` with
            the station order of :meth:`mainline_stations`.
        """
        stations = self.mainline_stations()
        if not stations:
            return np.empty((0, 0), dtype=np.float64)
        matrix = np.column_stack([self.speeds_ms[s.id] for s in stations])
        out = matrix if windows is None else matrix[windows]
        return np.asarray(out, dtype=np.float64)

    def coverage(self) -> ObservedCoverage:
        """Share of the mainline station-window grid that carries a measurement."""
        stations = self.mainline_stations()
        cells = len(stations) * self.n_windows
        if cells == 0:
            return ObservedCoverage(len(stations), self.n_windows, math.nan, math.nan)
        flows = np.column_stack([self.flows_veh_h[s.id] for s in stations])
        speeds = np.column_stack([self.speeds_ms[s.id] for s in stations])
        return ObservedCoverage(
            n_stations=len(stations),
            n_windows=self.n_windows,
            flow_fraction=float(np.count_nonzero(np.isfinite(flows))) / cells,
            speed_fraction=float(np.count_nonzero(np.isfinite(speeds))) / cells,
        )


def _simulated_speed_matrix(
    trajectories: pd.DataFrame,
    *,
    bins: Sequence[tuple[float, float]],
    windows: Sequence[int],
    window_s: float,
    x_offset_m: float,
) -> FloatArray:
    """Mean sampled speed per (window, segment) from trajectory rows.

    The mean is over the recorded samples inside the cell — the same
    time-mean-speed estimator the detector reports — and is NaN in a cell no
    vehicle was sampled in, so an empty cell is skipped rather than read as a
    zero speed.
    """
    out = np.full((len(windows), len(bins)), np.nan, dtype=np.float64)
    if not windows or not bins:
        return out
    t = np.asarray(trajectories["t"].to_numpy(), dtype=np.float64)
    x = np.asarray(trajectories["x"].to_numpy(), dtype=np.float64)
    v = np.asarray(trajectories["v"].to_numpy(), dtype=np.float64)
    ordered = time_ordered(t)
    for i, k in enumerate(windows):
        lo = k * window_s
        # The window's rows as a view when the rows are time-ordered; the
        # origin shift is applied to the window's positions only, never to a
        # copy of the whole column.
        in_window = time_window_rows(t, lo, lo + window_s, ordered=ordered)
        if n_window_rows(in_window) == 0:
            continue
        xs = x[in_window] - x_offset_m
        vs = v[in_window]
        for j, (x_lo, x_hi) in enumerate(bins):
            cell = (xs >= x_lo) & (xs < x_hi)
            if bool(cell.any()):
                out[i, j] = float(vs[cell].mean())
    return out


def _point_speed_matrix(
    trajectories: pd.DataFrame,
    *,
    x_refs: Sequence[float],
    windows: Sequence[int],
    window_s: float,
) -> tuple[FloatArray, NDArray[np.int64]]:
    """What a loop at each cross-section reads in each window (``ObservedScores``).

    The crossings of each ``x_refs`` entry (trajectory coordinates) by
    :func:`validation.metrics.crossing_speeds`, binned by their interpolated
    time into ``[k·window_s, (k+1)·window_s)``: the speed is the arithmetic
    mean of the crossing speeds (the time-mean speed a loop reports, every
    lane's vehicles together), the count the number of crossings with a
    finite speed.

    **Standstill.** A window in which no vehicle crosses is not necessarily
    an unmeasured one: a stopped queue standing over the loop occupies it
    and counts nothing, and a loop that estimates speed from its count and
    occupancy then reads zero. Such a window reads the mean sampled speed
    of the vehicles standing over the loop in it (front bumper in
    ``[x_ref, x_ref + LOOP_OCCUPANCY_ZONE_M)``, samples in the window) — about
    zero in a stopped queue, and still the vehicle's own speed where one
    merely lingers across a window boundary on a near-empty road — with its
    count left at zero. Only a window whose loop is empty throughout stays
    NaN: a loop over an empty road reads nothing.

    Returns:
        ``(speeds, counts)``, each ``[window][station]`` over ``windows``.
    """
    speeds = np.full((len(windows), len(x_refs)), np.nan, dtype=np.float64)
    counts = np.zeros((len(windows), len(x_refs)), dtype=np.int64)
    if not windows or not x_refs:
        return speeds, counts
    first, last = int(min(windows)), int(max(windows))
    row_of = np.full(last - first + 1, -1, dtype=np.int64)
    for i, k_win in enumerate(windows):
        row_of[int(k_win) - first] = i
    for j, (times, vs) in enumerate(crossing_speeds(trajectories, x_refs)):
        ok = np.isfinite(times) & np.isfinite(vs)
        k = np.floor(times[ok] / window_s).astype(np.int64)
        v = vs[ok]
        inside = (k >= first) & (k <= last)
        k, v = k[inside], v[inside]
        rows = row_of[k - first]
        keep = rows >= 0
        rows, v = rows[keep], v[keep]
        n = np.bincount(rows, minlength=len(windows))
        total = np.bincount(rows, weights=v, minlength=len(windows))
        counts[:, j] = n
        with np.errstate(invalid="ignore", divide="ignore"):
            speeds[:, j] = np.where(n > 0, total / np.maximum(n, 1), np.nan)
    idle = counts == 0
    if bool(idle.any()):
        _standstill_speeds(
            trajectories, speeds, idle, x_refs=x_refs, windows=windows, window_s=window_s
        )
    return speeds, counts


def _standstill_speeds(
    trajectories: pd.DataFrame,
    speeds: FloatArray,
    idle: NDArray[np.bool_],
    *,
    x_refs: Sequence[float],
    windows: Sequence[int],
    window_s: float,
) -> None:
    """Fill ``speeds`` in place where no vehicle crossed (:func:`_point_speed_matrix`).

    Cell ``(i, j)`` flagged in ``idle`` gets the mean finite speed of the
    window's samples with ``x`` in ``[x_refs[j], x_refs[j] + LOOP_OCCUPANCY_ZONE_M)``;
    it stays NaN when there are none. The window's rows are a view when the
    rows are time-ordered (:func:`validation.metrics.time_window_rows`).
    """
    t = np.asarray(trajectories["t"].to_numpy(), dtype=np.float64)
    x = np.asarray(trajectories["x"].to_numpy(), dtype=np.float64)
    v = np.asarray(trajectories["v"].to_numpy(), dtype=np.float64)
    ordered = time_ordered(t)
    for i, k_win in enumerate(windows):
        columns = np.flatnonzero(idle[i])
        if columns.size == 0:
            continue
        lo = int(k_win) * window_s
        rows = time_window_rows(t, lo, lo + window_s, ordered=ordered)
        if n_window_rows(rows) == 0:
            continue
        xs = x[rows]
        vs = v[rows]
        finite = np.isfinite(vs)
        for j in columns:
            x_ref = float(x_refs[int(j)])
            over = finite & (xs >= x_ref) & (xs < x_ref + LOOP_OCCUPANCY_ZONE_M)
            if bool(over.any()):
                speeds[i, j] = float(vs[over].mean())


def _link_hour_records(
    trajectories: pd.DataFrame,
    hourly: pd.DataFrame,
    *,
    stations: Sequence[ObservedStation],
    inside: Sequence[int],
    x_refs: Sequence[float],
    x_offset_m: float,
    duration_s: float,
    t0_local: str,
) -> tuple[LinkHourRecord, ...]:
    """The labelled GEH comparison of the station-hours in ``hourly``."""
    if hourly.empty:
        return ()
    shifted = hourly.assign(x_ref_m=hourly["x_ref_m"].to_numpy(dtype=np.float64) + x_offset_m)
    compared = link_hour_geh(
        trajectories,
        shifted,
        x_refs_m=x_refs,
        window_s=_S_PER_HOUR,
        sim_span=(0.0, duration_s),
    )
    # link_hour_geh reports each row's cross-section as the x_refs entry
    # it matched, so the station is looked up by that exact value rather
    # than by assuming its rows line up with ``hourly``'s.
    station_at = {x_refs[j]: stations[i] for j, i in enumerate(inside)}
    return tuple(
        LinkHourRecord(
            station=station_at[x].id,
            x_ref_m=station_at[x].x_m,
            window_start_s=float(w),
            clock=clock_label(t0_local, float(w)),
            obs_veh_h=float(q_obs),
            sim_veh_h=float(q_sim),
            geh=float(g),
        )
        for x, w, q_obs, q_sim, g in zip(
            compared.x_ref_m,
            compared.window_start_s,
            compared.obs_veh_h,
            compared.sim_veh_h,
            compared.geh,
            strict=True,
        )
    )


def _hours_inside(hourly: pd.DataFrame, allowed: set[int], window_s: float) -> pd.DataFrame:
    """The rows of ``hourly`` whose every window is among ``allowed``."""
    if hourly.empty:
        return hourly
    per_hour = round(_S_PER_HOUR / window_s)
    first = (hourly["window_start_s"].to_numpy(dtype=np.float64) / window_s).round()
    keep = [all((int(k0) + i) in allowed for i in range(per_hour)) for k0 in first.astype(np.int64)]
    return hourly.loc[np.asarray(keep, dtype=bool)]


def score_run_against_observed(
    trajectories: pd.DataFrame,
    observed: ObservedCorridor,
    *,
    warmup_s: float,
    duration_s: float,
    x_offset_m: float = 0.0,
) -> ObservedScores:
    """Score one simulated replicate against a corridor's observations.

    Both FHWA-style statistics of CLAUDE.md §7.1 are formed on the run's
    measurement window (the recorded span minus its warm-up):

    * **Link flows** — every fully observed station-hour
      (:meth:`ObservedCorridor.hourly_link_flows`) that lies inside the
      measurement window is compared with the simulated crossings of that
      station's cross-section by :func:`validation.metrics.link_hour_geh`.
      Each comparison is kept as a labelled :class:`LinkHourRecord`
      (``ObservedScores.link_hours``, row for row with ``geh_values``).
    * **Segment speeds** — the simulated mean sampled speed in each
      (window, segment) cell (:meth:`ObservedCorridor.segment_bins`) is
      compared with the observed cell by
      :func:`validation.metrics.rmspe`; cells with no observation, no
      simulated sample, or a zero observed speed are skipped and counted.

    Two further readings are stored beside them for the study protocol's
    baseline gate (:mod:`validation.baseline_gate`), additively — neither
    changes ``geh_values``, ``link_hours``, ``rmspe`` or the segment
    matrices:

    * **Anchored hours** (``link_hours_anchored``, ``hour_anchor_s``) — the
      link-flow comparison on hours that start at the first analysed window
      (the warm-up's end, the study period's start; docs/FRISCO_PROTOCOL.md
      §4, C1), so a warm-up that is not a whole number of hours does not
      leave the first part of the study period unscored.
    * **Point speeds** (``station_point_speeds_sim`` /
      ``station_point_counts_sim``) — per window and scored station, the mean
      speed of the vehicles crossing the station's cross-section, as a loop
      reads it (§5's virtual detector), and how many crossed; where none
      crossed, the speed of the vehicles standing over the loop (about zero
      in a stopped queue), NaN only over an empty road
      (:func:`_point_speed_matrix`).

    **Stations the run does not reach are excluded, not failed.** A station
    whose cross-section ``x_m + x_offset_m`` falls outside
    ``[x.min(), x.max()]`` of ``trajectories`` can be crossed by no simulated
    vehicle and sits in no simulated speed cell; scoring it would contribute a
    zero simulated flow (GEH ``√(2·q_obs)``) and nothing else, which measures
    the run's extent rather than the model. Such stations are dropped from
    both the link-hours and the speed matrix and reported on
    ``n_stations_outside_span`` / ``stations_outside_span`` so the report can
    state what was left out (CLAUDE.md §7.4).

    Args:
        trajectories: One replicate's trajectory rows with ``t`` [s],
            ``veh_id``, ``x`` [m] and ``v`` [m/s] columns, whole run (the
            warm-up is discarded here, not by the caller).
        observed: The corridor's observations.
        warmup_s: The run's configured warm-up [s].
        duration_s: End of the run's recorded span [s].
        x_offset_m: Simulation ``x`` of the observed origin [m] — the
            observed position ``x_m`` sits at ``x_m + x_offset_m`` in
            trajectory coordinates. Zero when the station table was measured
            on the simulated chain itself; a generated corridor's upstream
            insertion buffer (``microsim.demand_adapter.corridor_x_offset_m``)
            is the usual non-zero value.

    Returns:
        The :class:`ObservedScores` for this replicate.

    Raises:
        ValueError: Missing trajectory columns, an empty frame, or fewer than
            two positioned mainline stations.
    """
    for col in ("t", "veh_id", "x", "v"):
        if col not in trajectories.columns:
            raise ValueError(f"trajectories missing column {col!r}")
    if trajectories.empty:
        raise ValueError("trajectories holds no rows")

    windows = observed.analysis_windows(warmup_s, duration_s)
    all_bins = observed.segment_bins()
    stations = observed.mainline_stations()

    sim_x = np.asarray(trajectories["x"].to_numpy(), dtype=np.float64)
    x_lo, x_hi = float(np.nanmin(sim_x)), float(np.nanmax(sim_x))
    inside = [
        i
        for i, station in enumerate(stations)
        if x_lo - _TOL <= station.x_m + x_offset_m <= x_hi + _TOL
    ]
    kept = {stations[i].id for i in inside}
    outside_ids = tuple(s.id for s in stations if s.id not in kept)
    bins = [all_bins[i] for i in inside]
    x_refs = [stations[i].x_m + x_offset_m for i in inside]

    allowed = set(windows)
    hourly = observed.hourly_link_flows()
    if not hourly.empty:
        hourly = hourly.loc[hourly["station"].isin(kept)]
    hourly = _hours_inside(hourly, allowed, observed.window_s)
    record_args: dict[str, Any] = {
        "stations": stations,
        "inside": inside,
        "x_refs": x_refs,
        "x_offset_m": x_offset_m,
        "duration_s": duration_s,
        "t0_local": observed.t0_local,
    }
    link_hours = _link_hour_records(trajectories, hourly, **record_args)
    geh_values = tuple(r.geh for r in link_hours)

    # The same comparison on hours anchored at the study period's start
    # (docs/FRISCO_PROTOCOL.md §4, C1), additively: when the anchored hours
    # are the t0-aligned ones (a warm-up that is a whole number of hours)
    # the records are reused, else formed by the same function.
    anchored: tuple[LinkHourRecord, ...] = ()
    anchor_s: float | None = None
    if windows:
        anchor_s = windows[0] * observed.window_s
        hourly_a = observed.hourly_link_flows(anchor_window=windows[0])
        if not hourly_a.empty:
            hourly_a = hourly_a.loc[hourly_a["station"].isin(kept)]
        hourly_a = _hours_inside(hourly_a, allowed, observed.window_s)
        same_hours = list(zip(hourly["station"], hourly["window_start_s"], strict=True)) == list(
            zip(hourly_a["station"], hourly_a["window_start_s"], strict=True)
        )
        anchored = (
            link_hours if same_hours else _link_hour_records(trajectories, hourly_a, **record_args)
        )

    point_speeds, point_counts = _point_speed_matrix(
        trajectories, x_refs=x_refs, windows=windows, window_s=observed.window_s
    )

    sim_speeds = _simulated_speed_matrix(
        trajectories,
        bins=bins,
        windows=windows,
        window_s=observed.window_s,
        x_offset_m=x_offset_m,
    )
    obs_speeds = observed.speed_matrix(
        slice(windows[0], windows[-1] + 1) if windows else slice(0, 0)
    )
    if windows:
        obs_speeds = obs_speeds[[k - windows[0] for k in windows], :]
    obs_speeds = obs_speeds[:, inside]
    both = np.isfinite(sim_speeds) & np.isfinite(obs_speeds) & (obs_speeds != 0.0)
    n_cells = int(np.count_nonzero(both))
    value = float(rmspe(sim_speeds[both], obs_speeds[both])) if n_cells else math.nan

    return ObservedScores(
        geh_values=tuple(float(g) for g in geh_values),
        n_link_hours=len(geh_values),
        rmspe=value,
        n_speed_cells=n_cells,
        segment_speeds_sim=tuple(tuple(float(v) for v in row) for row in sim_speeds),
        segment_speeds_obs=tuple(tuple(float(v) for v in row) for row in obs_speeds),
        windows=tuple(windows),
        n_stations_outside_span=len(outside_ids),
        stations_outside_span=outside_ids,
        link_hours=link_hours,
        link_hours_anchored=anchored,
        hour_anchor_s=anchor_s,
        station_point_speeds_sim=tuple(tuple(float(v) for v in row) for row in point_speeds),
        station_point_counts_sim=tuple(tuple(int(n) for n in row) for row in point_counts),
        station_point_standstill=True,
    )


def _source_fields(observed: ObservedCorridor) -> tuple[str, str, str]:
    """``(provider, dates, url)`` of an artifact's ``source`` block, as text."""
    source = observed.source
    dates = source.get("dates")
    return (
        str(source.get("provider", "")),
        ", ".join(str(d) for d in dates) if isinstance(dates, list) else str(dates or ""),
        str(source.get("url", "")),
    )


def no_comparison_provenance(
    observed: ObservedCorridor, *, path: str = ""
) -> ObservedProvenance | None:
    """Provenance for an artifact no comparison can be formed from, else None.

    An artifact with fewer than two positioned mainline stations defines no
    station spacing and therefore no segment
    (:meth:`ObservedCorridor.segment_bins`). That is a property of the
    *artifact*, not of any run, and it is not a reason to fail a report: the
    two observed criteria are simply not evaluated, and the block the report
    prints says why (CLAUDE.md §0.1 — an unevaluated criterion is stated, never
    quietly passed or failed).

    Args:
        observed: The corridor's observations.
        path: Artifact path recorded in the provenance block.

    Returns:
        A zero-count :class:`ObservedProvenance` whose ``note`` gives the
        reason, or ``None`` when the artifact can be scored.
    """
    n_stations = len(observed.mainline_stations())
    if n_stations >= 2:
        return None
    coverage = observed.coverage()
    provider, dates_text, url = _source_fields(observed)
    return ObservedProvenance(
        path=path or observed.path,
        corridor=observed.corridor,
        provider=provider,
        dates=dates_text,
        url=url,
        aggregation=observed.aggregation,
        t0_local=observed.t0_local,
        window_s=observed.window_s,
        n_stations=coverage.n_stations,
        n_windows=coverage.n_windows,
        n_windows_compared=0,
        flow_fraction=coverage.flow_fraction,
        speed_fraction=coverage.speed_fraction,
        n_link_hours=0,
        n_speed_cells=0,
        n_replicates=0,
        wave_speed=DetectorWaveSpeed.from_context(observed.context),
        note=(
            f"the artifact holds {n_stations} positioned mainline station(s); a link-flow "
            "and segment-speed comparison needs at least two, so no run was scored "
            "against it"
        ),
    )


def pool_scores(
    observed: ObservedCorridor,
    scores: Sequence[ObservedScores],
    *,
    path: str = "",
) -> tuple[list[float], float, list[list[float]], list[list[float]], ObservedProvenance]:
    """Pool per-replicate scores into the report's observed inputs.

    GEH values are pooled across replicates (every replicate contributes its
    own station-hours, so the criterion's pass fraction is measured over the
    whole ensemble rather than over one arbitrary seed); RMSPE is the mean of
    the replicates' values; the simulated speed matrix is the replicate mean
    (NaN-aware), which is what the report's aggregation table compares with
    the observed field.

    Args:
        observed: The corridor's observations.
        scores: One :class:`ObservedScores` per replicate; must be non-empty
            and share a matrix shape.
        path: Artifact path recorded in the provenance block.

    Returns:
        ``(geh_values, rmspe, segment_speeds_sim, segment_speeds_obs,
        provenance)``.

    Raises:
        ValueError: No scores, matrices of differing shape, or replicates
            scored over different observation windows (the observed matrix is
            taken from the first replicate, so pooling misaligned windows
            would compare each replicate against another one's hours).
    """
    if not scores:
        raise ValueError("pool_scores needs at least one replicate's scores")
    shapes = {np.asarray(s.segment_speeds_sim, dtype=np.float64).shape for s in scores} | {
        np.asarray(s.segment_speeds_obs, dtype=np.float64).shape for s in scores
    }
    if len(shapes) != 1:
        raise ValueError(f"replicates produced differing speed-matrix shapes: {sorted(shapes)}")
    for i, replicate in enumerate(scores):
        if replicate.windows != scores[0].windows:
            raise ValueError(
                f"replicate {i} was scored over observation windows "
                f"{list(replicate.windows)}, replicate 0 over {list(scores[0].windows)}; "
                "replicates scored over different windows cannot be pooled"
            )
    sim = np.asarray([np.asarray(s.segment_speeds_sim, dtype=np.float64) for s in scores])
    obs = np.asarray(scores[0].segment_speeds_obs, dtype=np.float64)
    with warnings.catch_warnings():
        # A cell no replicate sampled is an all-NaN slice; NaN is the answer
        # (the cell is skipped downstream), not a condition worth warning about.
        warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning)
        mean_sim = np.nanmean(sim, axis=0) if sim.size else np.empty((0, 0), dtype=np.float64)
    geh = [g for s in scores for g in s.geh_values]
    finite = [s.rmspe for s in scores if math.isfinite(s.rmspe)]
    value = float(np.mean(finite)) if finite else math.nan
    coverage = observed.coverage()
    provider, dates_text, url = _source_fields(observed)
    excluded = sorted({sid for s in scores for sid in s.stations_outside_span})
    provenance = ObservedProvenance(
        path=path or observed.path,
        corridor=observed.corridor,
        provider=provider,
        dates=dates_text,
        url=url,
        aggregation=observed.aggregation,
        t0_local=observed.t0_local,
        window_s=observed.window_s,
        n_stations=coverage.n_stations,
        n_windows=coverage.n_windows,
        n_windows_compared=len(scores[0].windows),
        flow_fraction=coverage.flow_fraction,
        speed_fraction=coverage.speed_fraction,
        n_link_hours=sum(s.n_link_hours for s in scores),
        n_speed_cells=sum(s.n_speed_cells for s in scores),
        n_replicates=len(scores),
        n_stations_outside_span=len(excluded),
        stations_outside_span=", ".join(excluded),
        wave_speed=DetectorWaveSpeed.from_context(observed.context),
    )
    return (
        geh,
        value,
        [[float(v) for v in row] for row in mean_sim],
        [[float(v) for v in row] for row in obs],
        provenance,
    )


def pool_link_hours(scores: Sequence[ObservedScores]) -> tuple[PooledLinkHour, ...] | None:
    """Summarise the replicates' link-hour tables per station-hour.

    Rows are keyed by ``(station, window_start_s)`` and appear in the order
    they are first met (replicate 0's order — station by position, then
    hour — followed by any station-hour only a later replicate compared). A
    station-hour a replicate did not compare (its station outside that run's
    position span) contributes nothing to that row, and ``n_seeds`` says how
    many did. GEH statistics are taken over the values as stored
    (:data:`GEH_DECIMALS`), so they are recomputable from the per-seed tables
    and identical whether the scores were computed now or read back.

    Args:
        scores: One :class:`ObservedScores` per replicate.

    Returns:
        One :class:`PooledLinkHour` per station-hour, or ``None`` when any
        replicate carries no table (scores read back from a file written
        before the table existed) — a summary over some of the replicates
        would read as one over all of them.

    Raises:
        ValueError: No scores, or one station-hour carrying different observed
            volumes in two replicates (they were scored against different
            observations and cannot be pooled).
    """
    if not scores:
        raise ValueError("pool_link_hours needs at least one replicate's scores")
    tables: list[tuple[LinkHourRecord, ...]] = []
    for replicate in scores:
        if replicate.link_hours is None:
            return None
        tables.append(replicate.link_hours)
    grouped: dict[tuple[str, float], list[LinkHourRecord]] = {}
    for table in tables:
        for record in table:
            grouped.setdefault((record.station, record.window_start_s), []).append(record)
    pooled: list[PooledLinkHour] = []
    for (station, start), records in grouped.items():
        first = records[0]
        for other in records[1:]:
            if not math.isclose(other.obs_veh_h, first.obs_veh_h, rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError(
                    f"station {station!r} at {start} s carries observed volumes "
                    f"{first.obs_veh_h} and {other.obs_veh_h} veh/h in two replicates; "
                    "replicates scored against different observations cannot be pooled"
                )
        sims = [r.sim_veh_h for r in records]
        gehs = [round(r.geh, GEH_DECIMALS) for r in records]
        pooled.append(
            PooledLinkHour(
                station=station,
                x_ref_m=first.x_ref_m,
                window_start_s=start,
                clock=first.clock,
                obs_veh_h=first.obs_veh_h,
                n_seeds=len(records),
                sim_veh_h_mean=math.fsum(sims) / len(sims),
                sim_veh_h_min=min(sims),
                sim_veh_h_max=max(sims),
                geh_mean=math.fsum(gehs) / len(gehs),
                geh_min=min(gehs),
                geh_max=max(gehs),
            )
        )
    return tuple(pooled)
