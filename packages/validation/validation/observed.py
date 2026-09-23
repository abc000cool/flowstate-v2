"""Observed corridor detector data, as the validation tier reads it.

A corridor's measurements arrive as a ``flowstate.observations/1`` artifact
(docs/CONTRACTS.md, "Detector observations"): a fixed grid of ``n_windows``
analysis windows starting at ``t0_local``, one flow and one speed series per
station, NaN wherever nothing was measured. :class:`ObservedCorridor` is the
read-only view of that artifact this package needs, and
:func:`score_run_against_observed` turns one simulated replicate plus that
view into the two FHWA-style comparison statistics of CLAUDE.md §7.1 — GEH on
hourly link flows and RMSPE on segment speeds — together with the counts
behind them.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from flowstate_core.units import s_to_h
from validation.metrics import link_hour_geh, rmspe

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

    def to_dict(self) -> dict[str, Any]:
        """JSON form (NaN written as ``null``) for a per-seed artifact."""

        def safe(value: float) -> float | None:
            return None if not math.isfinite(value) else float(value)

        return {
            "geh_values": [round(g, 4) for g in self.geh_values],
            "n_link_hours": self.n_link_hours,
            "rmspe": safe(self.rmspe),
            "n_speed_cells": self.n_speed_cells,
            "windows": list(self.windows),
            "segment_speeds_sim": [[safe(v) for v in row] for row in self.segment_speeds_sim],
            "segment_speeds_obs": [[safe(v) for v in row] for row in self.segment_speeds_obs],
            "n_stations_outside_span": self.n_stations_outside_span,
            "stations_outside_span": list(self.stations_outside_span),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ObservedScores:
        """Rebuild from :meth:`to_dict` (``--criteria-only`` rescoring)."""

        def rows(key: str) -> tuple[tuple[float, ...], ...]:
            return tuple(
                tuple(math.nan if v is None else float(v) for v in row) for row in raw.get(key, ())
            )

        value = raw.get("rmspe")
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
        )


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

    def hourly_link_flows(self) -> pd.DataFrame:
        """Hour-aligned observed volumes per station, ready for GEH.

        GEH is only meaningful on hourly volumes, so the sub-hourly windows
        are aggregated: the vehicles counted in the ``3600 / window_s``
        windows of one hour are summed, which is the mean of those windows'
        ``veh/h`` values. Hours are aligned to window 0 (i.e. to
        ``t0_local``) and are reported **only when every window in the hour
        is valid at that station** — a partly observed hour would be a
        lower-bound volume compared as if it were a count.

        Returns:
            Rows ``(x_ref_m, window_start_s, flow_veh_h, station)``, one per
            fully observed station-hour, in station then time order.
            ``window_start_s`` is simulation time.

        Raises:
            ValueError: ``window_s`` does not divide one hour.
        """
        per_hour = round(_S_PER_HOUR / self.window_s)
        if per_hour < 1 or abs(per_hour * self.window_s - _S_PER_HOUR) > _TOL:
            raise ValueError(
                f"window_s = {self.window_s} s does not divide one hour; hourly volumes "
                "cannot be formed"
            )
        x_ref: list[float] = []
        starts: list[float] = []
        flows: list[float] = []
        names: list[str] = []
        for station in self.mainline_stations():
            series = self.flows_veh_h[station.id]
            for k0 in range(0, self.n_windows - per_hour + 1, per_hour):
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
    x = np.asarray(trajectories["x"].to_numpy(), dtype=np.float64) - x_offset_m
    v = np.asarray(trajectories["v"].to_numpy(), dtype=np.float64)
    for i, k in enumerate(windows):
        lo = k * window_s
        in_window = (t >= lo) & (t < lo + window_s)
        if not bool(in_window.any()):
            continue
        xs = x[in_window]
        vs = v[in_window]
        for j, (x_lo, x_hi) in enumerate(bins):
            cell = (xs >= x_lo) & (xs < x_hi)
            if bool(cell.any()):
                out[i, j] = float(vs[cell].mean())
    return out


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
    * **Segment speeds** — the simulated mean sampled speed in each
      (window, segment) cell (:meth:`ObservedCorridor.segment_bins`) is
      compared with the observed cell by
      :func:`validation.metrics.rmspe`; cells with no observation, no
      simulated sample, or a zero observed speed are skipped and counted.

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

    per_hour = round(_S_PER_HOUR / observed.window_s)
    allowed = set(windows)
    hourly = observed.hourly_link_flows()
    if not hourly.empty:
        hourly = hourly.loc[hourly["station"].isin(kept)]
    if not hourly.empty:
        first = (hourly["window_start_s"].to_numpy(dtype=np.float64) / observed.window_s).round()
        keep = [
            all((int(k0) + i) in allowed for i in range(per_hour)) for k0 in first.astype(np.int64)
        ]
        hourly = hourly.loc[np.asarray(keep, dtype=bool)]
    geh_values: tuple[float, ...] = ()
    if not hourly.empty:
        shifted = hourly.assign(x_ref_m=hourly["x_ref_m"].to_numpy(dtype=np.float64) + x_offset_m)
        geh_values = link_hour_geh(
            trajectories,
            shifted,
            x_refs_m=x_refs,
            window_s=_S_PER_HOUR,
            sim_span=(0.0, duration_s),
        ).geh

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
    )
    return (
        geh,
        value,
        [[float(v) for v in row] for row in mean_sim],
        [[float(v) for v in row] for row in obs],
        provenance,
    )
