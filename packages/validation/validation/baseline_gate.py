"""The baseline gate of the corridor study protocol (docs/FRISCO_PROTOCOL.md §6).

Before any strategy result is reported, the do-nothing model must reproduce
the corridor. The gate passes only if checks C1, C3, C5 and C6 pass on the
calibration days, C1, C3 and C6 pass on the validation days, and C4 passes or
is "not applicable" (§6). C2 is reported beside C1 and is not part of the
gate. Every check is scored over at least :data:`MIN_GATE_REPLICATES` seeded
replicates (§4), so a run set with fewer cannot pass.

What each check reads (protocol §4):

* **C1 link flows** — GEH on hourly station volumes, pooled over the
  replicates, under the ``fhwa_default`` profile (:data:`LINK_FLOW_PROFILE`).
* **C2 link flows, Texas** — the same comparisons under ``txdot_tsap_ch13``
  (:data:`TEXAS_PROFILE`); reported, never gating.
* **C3 speeds** — RMSPE of station mean speeds at **15-minute** aggregation
  (:data:`SPEED_AGGREGATION_S`): per replicate, the five-minute simulated and
  observed station-segment speeds are averaged over the same windows of each
  quarter hour of the observation grid (a window enters only where both sides
  are measured, so both block means cover the same windows), and the RMSPE is
  formed over the blocks; the gating value is the mean over the replicates,
  reported with its 95 % interval. 5- and 60-minute values are diagnostics,
  as is the replicate-mean field by aggregation
  (:func:`validation.report.speed_aggregation_rows`).
* **C4 wave speed** — the simulated backward wave speed read by the profile
  detector (``stack``, :data:`GATE_WAVE_DETECTOR`) inside the profile's band;
  the corridor's observed speed from detector cross-correlation
  (``calibration.waves_observed``, read from the calibration-day artifact's
  ``context`` by :class:`validation.observed.DetectorWaveSpeed`) is printed
  beside it. Fewer than :data:`WAVE_MIN_VALID_PAIRS` station pairs with a
  valid cross-correlation on the calibration days make C4 "not applicable",
  decided from the observed data alone; an artifact carrying no estimate
  leaves the applicability undetermined and C4 must then pass.
* **C5 collisions** — zero SUMO collisions in every run, a run without the
  counter "not recorded" (:func:`validation.criteria.zero_collisions`).
* **C6 bottlenecks** — :mod:`validation.bottlenecks` on the day set's
  observed station speeds against each replicate's.

**Scoring a battery against another day set.** A battery scores its
replicates against one observations artifact and stores each replicate's
simulated side in ``observed_scores.json``: the simulated hourly volume of
every compared station-hour (``link_hours[*].sim_veh_h``) and the simulated
mean speed of every (window, station segment) cell (``segment_speeds_sim``).
Those depend on the station positions and the window grid only, so
:func:`rescore` pairs them with a second artifact on the same stations and
grid — the calibration-day or the validation-day mean — without reading a
trajectory. A station-hour the second artifact observes but the first did not
has no stored simulated volume; it is counted (``n_link_hours_unmatched``),
never filled in.

**Simulated station speeds.** The bottleneck rule and the speed criterion read
the simulated speed of a station as the mean of the sampled vehicle speeds in
that station's segment (the span to the midpoints with its neighbours,
:meth:`validation.observed.ObservedCorridor.segment_bins`) during the window —
the battery's ``segment_speeds_sim``. It is a segment-mean stand-in for a
point loop detector, not a virtual detector at the loop's position; the
result's notes say so.
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
from numpy.typing import NDArray

from flowstate_core.units import h_to_s
from validation.bottlenecks import (
    ACTIVATION_TOLERANCE_S,
    BOTTLENECK_WINDOW_S,
    LOCATION_MIN_REPLICATE_SHARE,
    MIN_OBSERVED_ACTIVE_S,
    BottleneckComparison,
    compare_bottlenecks,
    identify_bottlenecks,
)
from validation.criteria import get_profile, zero_collisions
from validation.metrics import CI_LEVEL, MIN_REPLICATES, ci, geh, geh_pass_fraction, rmspe
from validation.observed import (
    DetectorWaveSpeed,
    LinkHourRecord,
    ObservedCorridor,
    ObservedScores,
    ObservedStation,
    clock_label,
)
from validation.waves import STACK_DETECTOR

FloatArray = NDArray[np.float64]

#: Schema tag of the gate's JSON form.
GATE_SCHEMA: Final[str] = "flowstate.baseline_gate/1"

#: The rule set the gate implements.
PROTOCOL_DOC: Final[str] = "docs/FRISCO_PROTOCOL.md"

#: C1 is scored under this criteria profile (§4, C1).
LINK_FLOW_PROFILE: Final[str] = "fhwa_default"

#: C2 is scored under this profile and reported, not gating (§4, C2).
TEXAS_PROFILE: Final[str] = "txdot_tsap_ch13"

#: Seconds per minute (the protocol states aggregations in minutes).
_S_PER_MIN: Final[float] = 60.0

#: C3's aggregation: station mean speeds over 15 minutes (§4, C3; fixed in
#: advance because five-minute station speeds carry a noise floor of their own).
SPEED_AGGREGATION_S: Final[float] = 15.0 * _S_PER_MIN

#: C3's diagnostic aggregations, reported beside it (§4, C3: 5 and 60 minutes).
SPEED_DIAGNOSTIC_AGGREGATIONS_S: Final[tuple[float, ...]] = (5.0 * _S_PER_MIN, h_to_s(1.0))

#: C4 is "not applicable" when the calibration-day observations show fewer than
#: this many station pairs with a valid cross-correlation (§4, C4).
WAVE_MIN_VALID_PAIRS: Final[int] = 3

#: The detector C4's simulated wave speed must be read with (§4, C4).
GATE_WAVE_DETECTOR: Final[str] = STACK_DETECTOR.name

#: Replicates every check is scored over (§4; CLAUDE.md §0.6).
MIN_GATE_REPLICATES: Final[int] = MIN_REPLICATES

#: The two day sets (§3).
CALIBRATION: Final[str] = "calibration"
VALIDATION: Final[str] = "validation"

#: Checks that gate on the calibration days, on the validation days, and on
#: the run set as a whole (§6; "replicates" is §4's precondition).
GATING_CALIBRATION: Final[tuple[str, ...]] = ("C1", "C3", "C5", "C6")
GATING_VALIDATION: Final[tuple[str, ...]] = ("C1", "C3", "C6")

#: Plain names of the checks.
CHECK_NAMES: Final[dict[str, str]] = {
    "replicates": "Replicates",
    "C1": "Link flows",
    "C2": "Link flows, Texas criterion",
    "C3": "Speeds",
    "C4": "Wave speed",
    "C5": "Collisions",
    "C6": "Bottlenecks",
}

#: How each check is labelled in every report (§0 of the protocol).
CHECK_LABELS: Final[dict[str, str]] = {
    "replicates": "[FlowState] CLAUDE.md section 0.6; protocol section 4",
    "C1": "[federal] FHWA TAT Vol. III 2004 (profile fhwa_default)",
    "C2": "[federal] TxDOT TSAP ch. 13 (profile txdot_tsap_ch13); reported, not gating",
    "C3": "common microsimulation practice (CLAUDE.md section 7.1), cited in the report",
    "C4": "empirical stop-and-go literature (flowstate_core.constants.WAVE_SPEED_BAND_KMH)",
    "C5": "[FlowState] CLAUDE.md section 3.3; owner decision 2026-10-04",
    "C6": "[FlowState] protocol section 5",
}

CheckStatus = Literal["pass", "fail", "not_applicable", "not_recorded", "not_evaluated"]

#: Statuses that satisfy a gating check (C4 alone may be not applicable).
_SATISFIED: Final[frozenset[str]] = frozenset({"pass"})

#: Dimensionless fraction → percent (not an SI conversion).
_PERCENT: Final[float] = 100.0

#: Position/time comparison tolerance.
_TOL: Final[float] = 1e-6

SIMULATED_SPEED_NOTE: Final[str] = (
    "simulated station speed = mean of the sampled vehicle speeds in the station's "
    "segment (to the midpoints with its neighbours) during the window "
    "(observed_scores.json segment_speeds_sim); a segment-mean stand-in for a point loop "
    "detector, not a virtual detector at the loop's position"
)


def _num(value: float | None) -> float | None:
    """Finite float or ``None``."""
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def _ci_dict(values: Sequence[float]) -> dict[str, Any]:
    """Replicate interval as JSON (:func:`validation.metrics.ci`)."""
    interval = ci(values)
    return {
        "mean": _num(interval.mean),
        "lo95": _num(interval.lo95),
        "hi95": _num(interval.hi95),
        "n": interval.n,
        "underpowered": interval.underpowered,
    }


# ---------------------------------------------------------------------------
# Re-scoring stored simulated sides against another day set
# ---------------------------------------------------------------------------


def grid_mismatch(a: ObservedCorridor, b: ObservedCorridor) -> str | None:
    """Why two artifacts are not on one station table and window grid, else None."""
    if abs(a.window_s - b.window_s) > _TOL:
        return f"window {a.window_s:g} s against {b.window_s:g} s"
    if a.n_windows != b.n_windows:
        return f"{a.n_windows} windows against {b.n_windows}"
    if a.t0_local != b.t0_local:
        return f"t0_local {a.t0_local!r} against {b.t0_local!r}"
    sa, sb = a.mainline_stations(), b.mainline_stations()
    if [s.id for s in sa] != [s.id for s in sb]:
        return (
            f"mainline stations {[s.id for s in sa]} against {[s.id for s in sb]} (same ids "
            "in the same order needed)"
        )
    for x, y in zip(sa, sb, strict=True):
        if abs(x.x_m - y.x_m) > _TOL:
            return f"station {x.id} at {x.x_m:g} m against {y.x_m:g} m"
    return None


def same_observations(a: ObservedCorridor, b: ObservedCorridor) -> bool:
    """Whether two artifacts carry the same grid and the same mainline series."""
    if grid_mismatch(a, b) is not None:
        return False
    for s in a.mainline_stations():
        if not np.array_equal(a.flows_veh_h[s.id], b.flows_veh_h[s.id], equal_nan=True):
            return False
        if not np.array_equal(a.speeds_ms[s.id], b.speeds_ms[s.id], equal_nan=True):
            return False
    return True


def scored_stations(
    scored_against: ObservedCorridor, scores: ObservedScores
) -> list[ObservedStation]:
    """The stations behind the columns of a replicate's speed matrices.

    ``score_run_against_observed`` keeps the positioned mainline stations
    inside the run's span, in position order, and lists the others in
    ``stations_outside_span``.

    Raises:
        ValueError: The column count disagrees with that station list.
    """
    outside = set(scores.stations_outside_span)
    stations = [s for s in scored_against.mainline_stations() if s.id not in outside]
    if scores.segment_speeds_sim and len(scores.segment_speeds_sim[0]) != len(stations):
        raise ValueError(
            f"the stored speed matrix has {len(scores.segment_speeds_sim[0])} column(s); the "
            f"artifact it was scored against gives {len(stations)} station(s) inside the span"
        )
    return stations


@dataclass(frozen=True)
class Rescored:
    """One replicate's stored simulated side paired with a day set's observations.

    Attributes:
        scores: The replicate scored against the day set (same layout as
            :class:`validation.observed.ObservedScores`).
        n_unmatched: Observed station-hours with no stored simulated volume.
        geh_available: False when the stored scores carry no link-hour table
            and the day set differs from the artifact they were scored
            against (the GEH cannot be re-formed).
    """

    scores: ObservedScores
    n_unmatched: int = 0
    geh_available: bool = True


def rescore(
    scores: ObservedScores,
    *,
    scored_against: ObservedCorridor,
    target: ObservedCorridor,
) -> Rescored:
    """Pair a replicate's stored simulated side with another day set (module docstring).

    Args:
        scores: The replicate's stored scores.
        scored_against: The artifact ``scores`` was computed against.
        target: The day set's artifact (same stations and grid).

    Returns:
        The :class:`Rescored` replicate. When ``target`` carries the same
        series as ``scored_against`` the stored scores are returned as they
        are.

    Raises:
        ValueError: The two artifacts are not on one station table and grid.
    """
    if same_observations(scored_against, target):
        # The stored GEH values were formed against these very series.
        return Rescored(scores=scores)
    mismatch = grid_mismatch(scored_against, target)
    if mismatch is not None:
        raise ValueError(f"cannot re-score against {target.path or target.corridor}: {mismatch}")
    stations = scored_stations(scored_against, scores)
    kept = [s.id for s in stations]
    allowed = set(scores.windows)
    per_hour = round(h_to_s(1.0) / target.window_s)

    records: list[LinkHourRecord] = []
    unmatched = 0
    geh_available = scores.link_hours is not None
    if scores.link_hours is not None:
        sim_by_key = {
            (r.station, round(r.window_start_s, 3)): r.sim_veh_h for r in scores.link_hours
        }
        hourly = target.hourly_link_flows()
        position = {s.id: s.x_m for s in stations}
        for station, start, flow in zip(
            hourly["station"], hourly["window_start_s"], hourly["flow_veh_h"], strict=True
        ):
            if station not in position:
                continue
            k0 = round(float(start) / target.window_s)
            if not all((k0 + i) in allowed for i in range(per_hour)):
                continue
            sim_q = sim_by_key.get((str(station), round(float(start), 3)))
            if sim_q is None:
                unmatched += 1
                continue
            records.append(
                LinkHourRecord(
                    station=str(station),
                    x_ref_m=float(position[station]),
                    window_start_s=float(start),
                    clock=clock_label(target.t0_local, float(start)),
                    obs_veh_h=float(flow),
                    sim_veh_h=float(sim_q),
                    geh=float(geh(float(sim_q), float(flow))),
                )
            )

    sim = np.asarray(scores.segment_speeds_sim, dtype=np.float64)
    obs_full = target.speed_matrix()
    columns = [i for i, s in enumerate(target.mainline_stations()) if s.id in set(kept)]
    rows = list(scores.windows)
    obs = (
        obs_full[np.ix_(rows, columns)]
        if rows and columns
        else np.empty((len(rows), len(columns)), dtype=np.float64)
    )
    if sim.size == 0:
        sim = np.empty(obs.shape, dtype=np.float64)
    both = np.isfinite(sim) & np.isfinite(obs) & (obs != 0.0)
    n_cells = int(np.count_nonzero(both))
    value = float(rmspe(sim[both], obs[both])) if n_cells else math.nan
    rescored = ObservedScores(
        geh_values=tuple(r.geh for r in records),
        n_link_hours=len(records),
        rmspe=value,
        n_speed_cells=n_cells,
        segment_speeds_sim=scores.segment_speeds_sim,
        segment_speeds_obs=tuple(tuple(float(v) for v in row) for row in obs),
        windows=scores.windows,
        n_stations_outside_span=scores.n_stations_outside_span,
        stations_outside_span=scores.stations_outside_span,
        link_hours=tuple(records) if geh_available else None,
    )
    return Rescored(scores=rescored, n_unmatched=unmatched, geh_available=geh_available)


def aggregated_rmspe(
    scores: ObservedScores, *, window_s: float, aggregation_s: float
) -> tuple[float, int]:
    """One replicate's speed RMSPE at a coarser aggregation (module docstring, C3).

    Blocks are aligned to the observation grid (window ``k`` belongs to block
    ``k // (aggregation_s / window_s)``) and only blocks whose every window is
    among the replicate's analysed windows are used. Within a block a cell's
    simulated and observed means are taken over the windows where both are
    measured (and the observation is not zero), so the two means cover the
    same windows. At ``aggregation_s == window_s`` this is the stored
    five-minute RMSPE.

    Args:
        scores: One replicate's scores (``segment_speeds_sim`` / ``_obs``).
        window_s: The observations' window [s].
        aggregation_s: Block length [s]; a whole multiple of ``window_s``.

    Returns:
        ``(rmspe, n_cells)`` — NaN and zero when no block cell compares.

    Raises:
        ValueError: ``aggregation_s`` is not a whole multiple of ``window_s``.
    """
    ratio = aggregation_s / window_s
    k = round(ratio)
    if k < 1 or abs(ratio - k) > 1e-9:
        raise ValueError(
            f"aggregation {aggregation_s:g} s is not a whole multiple of the {window_s:g} s window"
        )
    sim = np.asarray(scores.segment_speeds_sim, dtype=np.float64)
    obs = np.asarray(scores.segment_speeds_obs, dtype=np.float64)
    if sim.size == 0 or obs.size == 0 or sim.shape != obs.shape:
        return math.nan, 0
    joint = np.isfinite(sim) & np.isfinite(obs) & (obs != 0.0)
    sim_j = np.where(joint, sim, np.nan)
    obs_j = np.where(joint, obs, np.nan)
    present = set(scores.windows)
    blocks: dict[int, list[int]] = {}
    for row, window in enumerate(scores.windows):
        blocks.setdefault(window // k, []).append(row)
    s_rows: list[FloatArray] = []
    o_rows: list[FloatArray] = []
    for block, members in sorted(blocks.items()):
        if not all((block * k + i) in present for i in range(k)):
            continue
        counts = np.count_nonzero(joint[members], axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            s_mean = np.nansum(sim_j[members], axis=0) / counts
            o_mean = np.nansum(obs_j[members], axis=0) / counts
        s_rows.append(np.where(counts > 0, s_mean, np.nan))
        o_rows.append(np.where(counts > 0, o_mean, np.nan))
    if not s_rows:
        return math.nan, 0
    s_blk = np.vstack(s_rows)
    o_blk = np.vstack(o_rows)
    ok = np.isfinite(s_blk) & np.isfinite(o_blk) & (o_blk != 0.0)
    n = int(np.count_nonzero(ok))
    if n == 0:
        return math.nan, 0
    return float(rmspe(s_blk[ok], o_blk[ok])), n


# ---------------------------------------------------------------------------
# One day set
# ---------------------------------------------------------------------------


def _aggregation_key(seconds: float) -> str:
    """JSON key of an aggregation, e.g. ``"900"``."""
    return f"{seconds:g}"


@dataclass(frozen=True)
class DaySetScore:
    """The no-strategy run set scored against one day set's observations.

    Attributes:
        day_set: ``calibration`` or ``validation``.
        observations_path: The day set's artifact.
        dates: Its dates (``source.dates``).
        n_replicates: Replicates scored.
        geh_values: Pooled GEH over the replicates; None when the GEH could
            not be formed (stored scores without a link-hour table).
        geh_fraction_per_replicate: Each replicate's GEH pass fraction under
            :data:`LINK_FLOW_PROFILE` (NaN when it compared nothing).
        n_link_hours_unmatched: Observed station-hours without a stored
            simulated volume, pooled.
        rmspe_per_replicate: Aggregation [s] → per-replicate RMSPE.
        n_speed_cells: Aggregation [s] → pooled compared cells.
        replicate_mean_rows: The replicate-mean field by aggregation
            (:func:`validation.report.speed_aggregation_rows`), diagnostics.
        bottlenecks: C6, or None when it could not be evaluated.
        bottleneck_note: Why C6 was not evaluated (empty otherwise).
        wave_context: The artifact's detector wave-speed estimate, if any.
        excluded_detectors: ``source.excluded_detectors`` of the artifact.
        notes: Plain statements.
    """

    day_set: str
    observations_path: str
    dates: tuple[str, ...]
    n_replicates: int
    geh_values: tuple[float, ...] | None
    geh_fraction_per_replicate: tuple[float, ...]
    n_link_hours_unmatched: int
    rmspe_per_replicate: dict[float, tuple[float, ...]]
    n_speed_cells: dict[float, int]
    replicate_mean_rows: tuple[dict[str, str], ...]
    bottlenecks: BottleneckComparison | None
    bottleneck_note: str
    wave_context: DetectorWaveSpeed | None
    excluded_detectors: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def rmspe_mean(self, aggregation_s: float = SPEED_AGGREGATION_S) -> float:
        """Mean over the replicates of the RMSPE at ``aggregation_s`` (NaN dropped)."""
        values = [v for v in self.rmspe_per_replicate.get(aggregation_s, ()) if math.isfinite(v)]
        return float(np.mean(values)) if values else math.nan

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        link = get_profile(LINK_FLOW_PROFILE)
        texas = get_profile(TEXAS_PROFILE)
        pooled = list(self.geh_values) if self.geh_values is not None else None
        return {
            "day_set": self.day_set,
            "observations_path": self.observations_path,
            "dates": list(self.dates),
            "n_replicates": self.n_replicates,
            "geh": {
                "n_comparisons": None if pooled is None else len(pooled),
                "n_unmatched_station_hours": self.n_link_hours_unmatched,
                "pass_fraction_pooled": (
                    None if not pooled else geh_pass_fraction(pooled, link.geh_threshold)
                ),
                "pass_fraction_ci": _ci_dict(self.geh_fraction_per_replicate),
                "texas_pass_fraction_pooled": (
                    None if not pooled else geh_pass_fraction(pooled, texas.geh_threshold)
                ),
                "texas_n_failing": (
                    None
                    if pooled is None
                    else sum(1 for g in pooled if not g < texas.geh_threshold)
                ),
            },
            "rmspe": {
                _aggregation_key(agg): {
                    "ci": _ci_dict(values),
                    "per_replicate": [_num(v) for v in values],
                    "n_cells_pooled": self.n_speed_cells.get(agg, 0),
                }
                for agg, values in sorted(self.rmspe_per_replicate.items())
            },
            "replicate_mean_field": [dict(r) for r in self.replicate_mean_rows],
            "bottlenecks": None if self.bottlenecks is None else self.bottlenecks.to_dict(),
            "bottleneck_note": self.bottleneck_note,
            "detector_wave_speed": (
                None if self.wave_context is None else self.wave_context.to_dict()
            ),
            "excluded_detectors": dict(self.excluded_detectors),
            "notes": list(self.notes),
        }


def _contiguous(windows: Sequence[int]) -> bool:
    return all(b == a + 1 for a, b in itertools.pairwise(windows))


def _bottleneck_inputs(
    target: ObservedCorridor,
    scored_against: ObservedCorridor,
    replicates: Sequence[ObservedScores],
) -> tuple[FloatArray, list[FloatArray], list[ObservedStation], list[int]] | str:
    """Observed and per-replicate station × window speeds for C6, or why not."""
    if not replicates:
        return "no replicate was scored"
    windows = list(replicates[0].windows)
    if not windows:
        return "no observation window lies inside the runs' measurement window"
    if any(list(r.windows) != windows for r in replicates):
        return "the replicates were scored over different windows"
    if not _contiguous(windows):
        return "the analysed windows are not consecutive"
    if abs(target.window_s - BOTTLENECK_WINDOW_S) > _TOL:
        return (
            f"the observations use {target.window_s:g} s windows; the bottleneck rule is "
            f"defined on {BOTTLENECK_WINDOW_S:g} s windows"
        )
    stations = scored_stations(scored_against, replicates[0])
    if len(stations) < 2:
        return "fewer than two stations inside the simulated span"
    ids = {s.id for s in stations}
    columns = [i for i, s in enumerate(target.mainline_stations()) if s.id in ids]
    obs = target.speed_matrix()[np.ix_(windows, columns)]
    sims = [np.asarray(r.segment_speeds_sim, dtype=np.float64) for r in replicates]
    for sim in sims:
        if sim.shape != obs.shape:
            return f"a replicate's speed matrix has shape {sim.shape}, expected {obs.shape}"
    return obs, sims, stations, windows


def score_day_set(
    day_set: str,
    target: ObservedCorridor,
    replicates: Sequence[ObservedScores],
    *,
    scored_against: ObservedCorridor,
    path: str = "",
) -> DaySetScore:
    """Score the stored replicates against one day set (C1, C2, C3, C6 inputs).

    Args:
        day_set: ``calibration`` or ``validation``.
        target: The day set's observations.
        replicates: One stored :class:`ObservedScores` per replicate.
        scored_against: The artifact those scores were computed against.
        path: The day set's artifact path (provenance).

    Returns:
        The :class:`DaySetScore`.

    Raises:
        ValueError: ``target`` is not on the station table and grid of
            ``scored_against``.
    """
    from validation.report import speed_aggregation_rows

    link = get_profile(LINK_FLOW_PROFILE)
    rescored = [rescore(r, scored_against=scored_against, target=target) for r in replicates]
    notes: list[str] = []
    geh_ok = all(r.geh_available for r in rescored)
    pooled: tuple[float, ...] | None
    if geh_ok:
        pooled = tuple(g for r in rescored for g in r.scores.geh_values)
    else:
        pooled = None
        notes.append(
            "the stored scores carry no link-hour table, so the GEH could not be re-formed "
            "against this day set"
        )
    per_rep = tuple(
        geh_pass_fraction(r.scores.geh_values, link.geh_threshold)
        if r.scores.geh_values
        else math.nan
        for r in rescored
    )
    unmatched = sum(r.n_unmatched for r in rescored)
    if unmatched:
        notes.append(
            f"{unmatched} observed station-hour(s) carry no stored simulated volume (the "
            "battery's own observations did not observe them); they are not compared"
        )
    aggregations = sorted({SPEED_AGGREGATION_S, *SPEED_DIAGNOSTIC_AGGREGATIONS_S})
    rmspe_by: dict[float, tuple[float, ...]] = {}
    cells_by: dict[float, int] = {}
    for agg in aggregations:
        values: list[float] = []
        cells = 0
        for r in rescored:
            try:
                value, n = aggregated_rmspe(r.scores, window_s=target.window_s, aggregation_s=agg)
            except ValueError:
                value, n = math.nan, 0
            values.append(value)
            cells += n
        rmspe_by[agg] = tuple(values)
        cells_by[agg] = cells

    mean_rows: tuple[dict[str, str], ...] = ()
    sims = [np.asarray(r.scores.segment_speeds_sim, dtype=np.float64) for r in rescored]
    if sims and all(s.size for s in sims) and len({s.shape for s in sims}) == 1:
        stacked = np.asarray(sims)
        counts = np.count_nonzero(np.isfinite(stacked), axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean_sim = np.nansum(stacked, axis=0) / counts
        mean_sim = np.where(counts > 0, mean_sim, np.nan)
        obs = np.asarray(rescored[0].scores.segment_speeds_obs, dtype=np.float64)
        if obs.shape == mean_sim.shape:
            mean_rows = tuple(
                speed_aggregation_rows(obs.tolist(), mean_sim.tolist(), target.window_s)
            )

    inputs = _bottleneck_inputs(target, scored_against, [r.scores for r in rescored])
    comparison: BottleneckComparison | None = None
    note = ""
    if isinstance(inputs, str):
        note = inputs
    else:
        obs_m, sim_ms, stations, windows = inputs
        ids = [s.id for s in stations]
        xs = [s.x_m for s in stations]
        observed = identify_bottlenecks(
            obs_m,
            ids,
            xs,
            window_s=target.window_s,
            first_window=windows[0],
            t0_local=target.t0_local,
        )
        simulated = [
            identify_bottlenecks(
                m, ids, xs, window_s=target.window_s, first_window=windows[0], t0_local=""
            )
            for m in sim_ms
        ]
        comparison = compare_bottlenecks(
            observed, simulated, station_ids=ids, notes=(SIMULATED_SPEED_NOTE,)
        )
    excluded = target.source.get("excluded_detectors")
    dates = target.source.get("dates")
    return DaySetScore(
        day_set=day_set,
        observations_path=path or target.path,
        dates=tuple(str(d) for d in dates) if isinstance(dates, list) else (),
        n_replicates=len(replicates),
        geh_values=pooled,
        geh_fraction_per_replicate=per_rep,
        n_link_hours_unmatched=unmatched,
        rmspe_per_replicate=rmspe_by,
        n_speed_cells=cells_by,
        replicate_mean_rows=mean_rows,
        bottlenecks=comparison,
        bottleneck_note=note,
        wave_context=DetectorWaveSpeed.from_context(target.context),
        excluded_detectors=(
            {str(k): str(v) for k, v in excluded.items()} if isinstance(excluded, dict) else {}
        ),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Checks and the gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    """One check on one day set.

    Attributes:
        check: ``C1`` … ``C6`` or ``replicates``.
        day_set: ``calibration``, ``validation`` or ``all runs``.
        status: ``pass``, ``fail``, ``not_applicable``, ``not_recorded`` or
            ``not_evaluated``.
        gating: Whether the gate depends on it.
        value: The measured value (fraction, km/h, count), None if none.
        target: The pass rule in words, with its numbers.
        shortfall: How far from passing, in the value's unit (None when it
            passes or there is no value).
        plain: One plain-language sentence with the computed numbers.
        label: Where the rule comes from ([federal] / [FlowState] / …).
    """

    check: str
    day_set: str
    status: CheckStatus
    gating: bool
    value: float | None
    target: str
    shortfall: float | None
    plain: str
    label: str

    @property
    def name(self) -> str:
        """Plain name of the check."""
        return CHECK_NAMES.get(self.check, self.check)

    @property
    def satisfied(self) -> bool:
        """Whether it satisfies the gate (C4 may be not applicable)."""
        if self.status in _SATISFIED:
            return True
        return self.check == "C4" and self.status == "not_applicable"

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "check": self.check,
            "name": self.name,
            "day_set": self.day_set,
            "status": self.status,
            "gating": self.gating,
            "satisfied": self.satisfied,
            "value": _num(self.value),
            "target": self.target,
            "shortfall": _num(self.shortfall),
            "plain": self.plain,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CheckResult:
        """Rebuild from :meth:`to_dict`."""
        value, short = raw.get("value"), raw.get("shortfall")
        status = str(raw["status"])
        if status not in ("pass", "fail", "not_applicable", "not_recorded", "not_evaluated"):
            raise ValueError(f"unknown check status {status!r}")
        return cls(
            check=str(raw["check"]),
            day_set=str(raw["day_set"]),
            status=status,  # type: ignore[arg-type]
            gating=bool(raw["gating"]),
            value=None if value is None else float(value),
            target=str(raw.get("target", "")),
            shortfall=None if short is None else float(short),
            plain=str(raw.get("plain", "")),
            label=str(raw.get("label", "")),
        )


def _pct(fraction: float) -> str:
    return f"{_PERCENT * fraction:.1f} %"


def _link_check(
    check: str, score: DaySetScore | None, day_set: str, *, gating: bool
) -> CheckResult:
    """C1 (``fhwa_default``) or C2 (``txdot_tsap_ch13``) on one day set."""
    profile = get_profile(LINK_FLOW_PROFILE if check == "C1" else TEXAS_PROFILE)
    cmp = ">=" if profile.geh_pass_inclusive else ">"
    target = (
        f"GEH < {profile.geh_threshold:g} on {cmp} {_pct(profile.geh_pass_fraction)} of "
        "station-hour comparisons"
    )
    label = CHECK_LABELS[check]
    if score is None:
        return CheckResult(
            check, day_set, "not_evaluated", gating, None, target, None,
            f"{CHECK_NAMES[check]}, {day_set} days: not evaluated — no {day_set}-day "
            "observations were supplied.",
            label,
        )  # fmt: skip
    if score.geh_values is None or not score.geh_values:
        why = (
            "the GEH could not be re-formed against these observations"
            if score.geh_values is None
            else "no station-hour could be compared"
        )
        return CheckResult(
            check, day_set, "not_evaluated", gating, None, target, None,
            f"{CHECK_NAMES[check]}, {day_set} days: not evaluated — {why}.", label,
        )  # fmt: skip
    values = score.geh_values
    frac = geh_pass_fraction(values, profile.geh_threshold)
    passed = (
        frac >= profile.geh_pass_fraction - 1e-12
        if profile.geh_pass_inclusive
        else frac > profile.geh_pass_fraction
    )
    interval = ci(score.geh_fraction_per_replicate)
    text = (
        f"{CHECK_NAMES[check]}, {day_set} days: GEH < {profile.geh_threshold:g} on "
        f"{_pct(frac)} of {len(values)} station-hour comparisons pooled over "
        f"{score.n_replicates} replicate(s) (per-replicate mean {_pct(interval.mean)}, "
        f"{_PERCENT * CI_LEVEL:g} % interval {_pct(interval.lo95)} to {_pct(interval.hi95)}); "
        f"the target is {cmp} {_pct(profile.geh_pass_fraction)}"
    )
    short = None if passed else profile.geh_pass_fraction - frac
    if short is not None:
        text += f", short by {_PERCENT * short:.1f} percentage points"
    if score.n_link_hours_unmatched:
        text += (
            f"; {score.n_link_hours_unmatched} observed station-hour(s) had no simulated "
            "volume to compare"
        )
    return CheckResult(
        check, day_set, "pass" if passed else "fail", gating, frac, target, short, text + ".", label
    )


def _speed_check(score: DaySetScore | None, day_set: str) -> CheckResult:
    """C3 on one day set."""
    rmspe_max = get_profile(LINK_FLOW_PROFILE).rmspe_max
    assert rmspe_max is not None  # fhwa_default carries the bound
    minutes = SPEED_AGGREGATION_S / _S_PER_MIN
    target = f"RMSPE <= {_pct(rmspe_max)} on station mean speeds at {minutes:g}-minute aggregation"
    label = CHECK_LABELS["C3"]
    if score is None:
        return CheckResult(
            "C3", day_set, "not_evaluated", True, None, target, None,
            f"Speeds, {day_set} days: not evaluated — no {day_set}-day observations were "
            "supplied.",
            label,
        )  # fmt: skip
    values = score.rmspe_per_replicate.get(SPEED_AGGREGATION_S, ())
    interval = ci(values)
    if interval.n == 0:
        return CheckResult(
            "C3", day_set, "not_evaluated", True, None, target, None,
            f"Speeds, {day_set} days: not evaluated — no speed cell could be compared at "
            f"{minutes:g}-minute aggregation.",
            label,
        )  # fmt: skip
    value = interval.mean
    passed = value <= rmspe_max + 1e-12
    diag = "; ".join(
        f"{agg / _S_PER_MIN:g} min {_pct(score.rmspe_mean(agg))}"
        for agg in SPEED_DIAGNOSTIC_AGGREGATIONS_S
        if math.isfinite(score.rmspe_mean(agg))
    )
    text = (
        f"Speeds, {day_set} days: RMSPE of {minutes:g}-minute station mean speeds "
        f"{_pct(value)} (mean over {interval.n} replicate(s), {_PERCENT * CI_LEVEL:g} % "
        f"interval {_pct(interval.lo95)} to {_pct(interval.hi95)}); the target is at most "
        f"{_pct(rmspe_max)}"
    )
    short = None if passed else value - rmspe_max
    if short is not None:
        text += f", over by {_PERCENT * short:.1f} percentage points"
    if diag:
        text += f" (diagnostics: {diag})"
    return CheckResult(
        "C3", day_set, "pass" if passed else "fail", True, value, target, short, text + ".", label
    )


def _wave_check(
    calibration: DaySetScore, wave_speeds_kmh: Sequence[float], wave_detector: str
) -> CheckResult:
    """C4 (calibration-day applicability; one simulated value)."""
    profile = get_profile(LINK_FLOW_PROFILE)
    lo, hi = profile.wave_speed_band_kmh
    target = (
        f"simulated backward wave speed ({GATE_WAVE_DETECTOR} detector) in {lo:g}-{hi:g} km/h, "
        f"or not applicable when fewer than {WAVE_MIN_VALID_PAIRS} station pairs show a valid "
        "cross-correlation on the calibration days"
    )
    label = CHECK_LABELS["C4"]
    context = calibration.wave_context
    observed_text = ""
    if context is not None:
        observed_text = "; observed on the calibration days (detector cross-correlation): " + (
            f"median {context.median_kmh:.1f} km/h from {context.n_used} of "
            f"{context.n_pairs} station pairs"
            if context.n_used > 0
            else f"no estimate from {context.n_pairs} station pairs"
        )
        if context.n_used < WAVE_MIN_VALID_PAIRS:
            return CheckResult(
                "C4", "calibration", "not_applicable", True, None, target, None,
                f"Wave speed: not applicable — the calibration-day observations show "
                f"{context.n_used} of {context.n_pairs} station pairs with a valid "
                f"cross-correlation, fewer than {WAVE_MIN_VALID_PAIRS}, so the corridor shows no "
                "recurrent waves to reproduce (decided from the observed data alone).",
                label,
            )  # fmt: skip
    else:
        observed_text = (
            "; the calibration-day observations carry no detector wave-speed estimate, so "
            "whether C4 applies was not determined and it must pass"
        )
    if wave_detector != GATE_WAVE_DETECTOR:
        return CheckResult(
            "C4", "calibration", "not_evaluated", True, None, target, None,
            f"Wave speed: not evaluated — the runs' wave speed was read with the "
            f"{wave_detector or 'unstated'} detector; the protocol requires "
            f"{GATE_WAVE_DETECTOR}{observed_text}.",
            label,
        )  # fmt: skip
    finite = [float(v) for v in wave_speeds_kmh if math.isfinite(float(v))]
    n_total = len(wave_speeds_kmh)
    if not finite:
        return CheckResult(
            "C4", "calibration", "fail", True, None, target, None,
            f"Wave speed: no backward wave front was detected in any of {n_total} "
            f"replicate(s); the band is {lo:g}-{hi:g} km/h{observed_text}.",
            label,
        )  # fmt: skip
    value = float(np.mean(finite))
    passed = lo <= value <= hi
    short = None if passed else (lo - value if value < lo else value - hi)
    text = (
        f"Wave speed: simulated backward wave speed {value:.1f} km/h (mean over the "
        f"{len(finite)} of {n_total} replicate(s) with a backward front, "
        f"{GATE_WAVE_DETECTOR} detector); the band is {lo:g}-{hi:g} km/h"
    )
    if short is not None:
        text += f", outside it by {short:.1f} km/h"
    return CheckResult(
        "C4", "calibration", "pass" if passed else "fail", True, value, target, short,
        text + observed_text + ".", label,
    )  # fmt: skip


def _collision_check(counts: Sequence[int | None] | None) -> CheckResult:
    """C5 over every run."""
    target = "zero SUMO collisions in every run, each run recording the counter"
    label = CHECK_LABELS["C5"]
    if counts is None:
        return CheckResult(
            "C5", "all runs", "not_recorded", True, None, target, None,
            "Collisions: not recorded — no per-run collision counts were supplied; a missing "
            "counter is never a pass.",
            label,
        )  # fmt: skip
    flag = zero_collisions(counts)
    recorded = [c for c in counts if c is not None]
    total = sum(recorded)
    if flag is True:
        return CheckResult(
            "C5", "all runs", "pass", True, 0.0, target, None,
            f"Collisions: none in {len(counts)} run(s).", label,
        )  # fmt: skip
    if flag is False:
        n_with = sum(1 for c in recorded if c > 0)
        return CheckResult(
            "C5", "all runs", "fail", True, float(total), target, float(total),
            f"Collisions: {total} SUMO collision(s) in {n_with} of {len(recorded)} run(s) that "
            "record the counter; the gate requires zero.",
            label,
        )  # fmt: skip
    missing = len(counts) - len(recorded)
    return CheckResult(
        "C5", "all runs", "not_recorded", True, None, target, None,
        f"Collisions: not recorded for {missing} of {len(counts)} run(s); a missing counter "
        "is never a pass.",
        label,
    )  # fmt: skip


def _bottleneck_check(score: DaySetScore | None, day_set: str) -> CheckResult:
    """C6 on one day set."""
    target = (
        f"every observed bottleneck active for {MIN_OBSERVED_ACTIVE_S / _S_PER_MIN:g} min or "
        f"more reproduced at the same or an adjacent station pair in at least "
        f"{_pct(LOCATION_MIN_REPLICATE_SHARE)} of replicates, median activation within "
        f"{ACTIVATION_TOLERANCE_S / _S_PER_MIN:g} min, duration and queue reach within "
        "tolerance, and no phantom bottleneck (protocol section 5)"
    )
    label = CHECK_LABELS["C6"]
    if score is None:
        return CheckResult(
            "C6", day_set, "not_evaluated", True, None, target, None,
            f"Bottlenecks, {day_set} days: not evaluated — no {day_set}-day observations were "
            "supplied.",
            label,
        )  # fmt: skip
    comparison = score.bottlenecks
    if comparison is None:
        return CheckResult(
            "C6", day_set, "not_evaluated", True, None, target, None,
            f"Bottlenecks, {day_set} days: not evaluated — {score.bottleneck_note}.", label,
        )  # fmt: skip
    n_observed = len(comparison.observed)
    n_sig = len(comparison.matches)
    head = (
        f"Bottlenecks, {day_set} days: {n_observed} observed bottleneck(s), {n_sig} active "
        f"for {MIN_OBSERVED_ACTIVE_S / _S_PER_MIN:g} min or more, compared with "
        f"{comparison.n_replicates} replicate(s)"
    )
    failed = comparison.failed_rules()
    n_failed = float(len(failed))
    if not failed:
        return CheckResult(
            "C6", day_set, "pass", True, 0.0, target, None,
            head + "; all four rules hold.", label,
        )  # fmt: skip
    detail = "; ".join(f"rule {r.rule} fails — {r.detail}" for r in failed)
    return CheckResult(
        "C6", day_set, "fail", True, n_failed, target, n_failed, f"{head}; {detail}.", label
    )


def _replicate_check(n_replicates: int) -> CheckResult:
    """§4's precondition: at least :data:`MIN_GATE_REPLICATES` replicates."""
    target = f"every check scored over at least {MIN_GATE_REPLICATES} seeded replicates"
    passed = n_replicates >= MIN_GATE_REPLICATES
    text = (
        f"Replicates: {n_replicates} seeded replicate(s); the protocol scores every check over "
        f"at least {MIN_GATE_REPLICATES}"
    )
    short = None if passed else float(MIN_GATE_REPLICATES - n_replicates)
    if short is not None:
        text += f", {MIN_GATE_REPLICATES - n_replicates} short"
    return CheckResult(
        "replicates", "all runs", "pass" if passed else "fail", True, float(n_replicates),
        target, short, text + ".", CHECK_LABELS["replicates"],
    )  # fmt: skip


@dataclass(frozen=True)
class GateResult:
    """The baseline gate (module docstring).

    Attributes:
        passed: Every gating check satisfied.
        checks: Every check, in report order.
        reasons: One plain sentence per unsatisfied gating check.
        day_sets: ``calibration`` / ``validation`` → the day set's JSON
            scores (:meth:`DaySetScore.to_dict`); the validation entry is
            None when no validation-day observations were supplied.
        n_replicates: Replicates the checks were scored over.
        config_hash: The no-strategy configuration's hash.
        scenario: Its scenario name or path.
        split: The day split's summary (dates, seed, underpowered), if given.
        excluded_detectors: Detector → reason, from the observations.
        notes: Plain statements.
        schema: :data:`GATE_SCHEMA`.
    """

    passed: bool
    checks: tuple[CheckResult, ...]
    reasons: tuple[str, ...]
    day_sets: dict[str, dict[str, Any] | None]
    n_replicates: int
    config_hash: str = ""
    scenario: str = ""
    split: dict[str, Any] | None = None
    excluded_detectors: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    schema: str = GATE_SCHEMA

    @property
    def strategy_results_allowed(self) -> bool:
        """Whether strategy results may be reported (§6: only when the gate passes)."""
        return self.passed

    @property
    def verdict(self) -> str:
        """``passed`` or ``failed``."""
        return "passed" if self.passed else "failed"

    def gating_checks(self) -> tuple[CheckResult, ...]:
        """The checks the gate depends on."""
        return tuple(c for c in self.checks if c.gating)

    def unsatisfied(self) -> tuple[CheckResult, ...]:
        """Gating checks that do not satisfy the gate."""
        return tuple(c for c in self.checks if c.gating and not c.satisfied)

    def check(self, name: str, day_set: str | None = None) -> CheckResult:
        """One check by name (and day set).

        Raises:
            KeyError: No such check.
        """
        for c in self.checks:
            if c.check == name and (day_set is None or c.day_set == day_set):
                return c
        raise KeyError(f"{name} {day_set or ''}".strip())

    def headline(self) -> str:
        """The one-line verdict, with the failing checks named."""
        if self.passed:
            return (
                "Baseline gate PASSED: the no-strategy model reproduced the corridor on every "
                f"gating check ({PROTOCOL_DOC} section 6); strategy results may be reported."
            )
        failing = ", ".join(f"{c.check} {c.name.lower()} ({c.day_set})" for c in self.unsatisfied())
        return (
            f"Baseline gate FAILED ({PROTOCOL_DOC} section 6): {failing}. No strategy "
            "recommendation may be made from this model."
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "schema": self.schema,
            "protocol": f"{PROTOCOL_DOC} section 6",
            "passed": self.passed,
            "verdict": self.verdict,
            "strategy_results_allowed": self.strategy_results_allowed,
            "headline": self.headline(),
            "reasons": list(self.reasons),
            "checks": [c.to_dict() for c in self.checks],
            "n_replicates": self.n_replicates,
            "config_hash": self.config_hash,
            "scenario": self.scenario,
            "split": None if self.split is None else dict(self.split),
            "excluded_detectors": dict(self.excluded_detectors),
            "day_sets": {k: v for k, v in self.day_sets.items()},
            "thresholds": gate_thresholds(),
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GateResult:
        """Rebuild from :meth:`to_dict` (the day-set blocks stay JSON).

        Raises:
            ValueError: A different schema.
        """
        schema = str(raw.get("schema", ""))
        if schema != GATE_SCHEMA:
            raise ValueError(f"expected schema {GATE_SCHEMA!r}, got {schema!r}")
        split = raw.get("split")
        return cls(
            passed=bool(raw["passed"]),
            checks=tuple(CheckResult.from_dict(c) for c in raw.get("checks", ())),
            reasons=tuple(str(r) for r in raw.get("reasons", ())),
            day_sets=dict(raw.get("day_sets") or {}),
            n_replicates=int(raw.get("n_replicates", 0)),
            config_hash=str(raw.get("config_hash", "")),
            scenario=str(raw.get("scenario", "")),
            split=None if split is None else dict(split),
            excluded_detectors={
                str(k): str(v) for k, v in (raw.get("excluded_detectors") or {}).items()
            },
            notes=tuple(str(n) for n in raw.get("notes", ())),
            schema=schema,
        )

    def to_json(self, path: str | Path) -> Path:
        """Write the JSON form (parents created); returns the path."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(_json_safe(self.to_dict()), indent=2, allow_nan=False) + "\n")
        return target

    @classmethod
    def from_json(cls, path: str | Path) -> GateResult:
        """Read a gate written by :meth:`to_json`."""
        return cls.from_dict(json.loads(Path(path).read_text()))


def _json_safe(obj: object) -> object:
    """Non-finite floats → ``None``."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def gate_thresholds() -> dict[str, Any]:
    """Every gate constant with its protocol section (recorded in each result)."""
    link = get_profile(LINK_FLOW_PROFILE)
    texas = get_profile(TEXAS_PROFILE)
    return {
        "link_flow_profile": f"{LINK_FLOW_PROFILE} (section 4, C1): GEH < "
        f"{link.geh_threshold:g} on >= {link.geh_pass_fraction:g} of station-hours",
        "texas_profile": f"{TEXAS_PROFILE} (section 4, C2, not gating): GEH < "
        f"{texas.geh_threshold:g} on every station-hour",
        "speed_aggregation_s": SPEED_AGGREGATION_S,
        "speed_rmspe_max": link.rmspe_max,
        "speed_diagnostic_aggregations_s": list(SPEED_DIAGNOSTIC_AGGREGATIONS_S),
        "wave_band_kmh": list(link.wave_speed_band_kmh),
        "wave_detector": GATE_WAVE_DETECTOR,
        "wave_min_valid_pairs": WAVE_MIN_VALID_PAIRS,
        "min_replicates": MIN_GATE_REPLICATES,
        "gating_calibration": list(GATING_CALIBRATION),
        "gating_validation": list(GATING_VALIDATION),
        "c4": "passes or is not applicable (section 6)",
    }


def evaluate_gate(
    calibration: DaySetScore,
    validation: DaySetScore | None,
    *,
    wave_speeds_kmh: Sequence[float],
    wave_detector: str,
    collision_counts: Sequence[int | None] | None,
    config_hash: str = "",
    scenario: str = "",
    split: Mapping[str, Any] | None = None,
) -> GateResult:
    """Evaluate the baseline gate from the two day sets' scores.

    Args:
        calibration: The no-strategy runs against the calibration days.
        validation: The same runs against the validation days; None when no
            validation-day observations exist (the gate then fails).
        wave_speeds_kmh: Each replicate's backward wave speed [km/h] (NaN =
            no front), read by ``wave_detector``.
        wave_detector: Name of the detector behind ``wave_speeds_kmh``.
        collision_counts: Each run's ``n_collisions`` (None = not recorded),
            or None when none were supplied.
        config_hash: The configuration's hash (provenance).
        scenario: Its scenario (provenance).
        split: The day split's summary (:meth:`calibration.day_split.DaySplit.summary`).

    Returns:
        The :class:`GateResult`.
    """
    n_rep = calibration.n_replicates
    checks: list[CheckResult] = [_replicate_check(n_rep)]
    for day_set, score in ((CALIBRATION, calibration), (VALIDATION, validation)):
        checks.append(_link_check("C1", score, day_set, gating=True))
        checks.append(_link_check("C2", score, day_set, gating=False))
        checks.append(_speed_check(score, day_set))
        checks.append(_bottleneck_check(score, day_set))
    checks.append(_wave_check(calibration, wave_speeds_kmh, wave_detector))
    checks.append(_collision_check(collision_counts))
    reasons = tuple(c.plain for c in checks if c.gating and not c.satisfied)
    passed = not reasons
    excluded = dict(calibration.excluded_detectors)
    if validation is not None:
        for k, v in validation.excluded_detectors.items():
            excluded.setdefault(k, v)
    notes = [SIMULATED_SPEED_NOTE]
    if validation is None:
        notes.append("no validation-day observations were supplied; the gate cannot pass")
    split_dict = None if split is None else dict(split)
    if split_dict is not None and split_dict.get("underpowered"):
        notes.append(
            "the day split is underpowered: "
            + str(split_dict.get("underpowered_reason") or "fewer days than the protocol's minimum")
        )
    return GateResult(
        passed=passed,
        checks=tuple(checks),
        reasons=reasons,
        day_sets={
            CALIBRATION: calibration.to_dict(),
            VALIDATION: None if validation is None else validation.to_dict(),
        },
        n_replicates=n_rep,
        config_hash=config_hash,
        scenario=scenario,
        split=split_dict,
        excluded_detectors=excluded,
        notes=tuple(notes),
    )


def gate_from_replicates(
    replicates: Sequence[ObservedScores],
    *,
    scored_against: ObservedCorridor,
    calibration: ObservedCorridor,
    validation: ObservedCorridor | None,
    wave_speeds_kmh: Sequence[float],
    wave_detector: str,
    collision_counts: Sequence[int | None] | None,
    calibration_path: str = "",
    validation_path: str = "",
    config_hash: str = "",
    scenario: str = "",
    split: Mapping[str, Any] | None = None,
) -> GateResult:
    """Score both day sets from the stored replicates and evaluate the gate.

    Args:
        replicates: One stored :class:`ObservedScores` per replicate.
        scored_against: The artifact they were scored against.
        calibration: Calibration-day observations.
        validation: Validation-day observations, or None.
        wave_speeds_kmh: Per-replicate wave speed [km/h].
        wave_detector: The detector behind them.
        collision_counts: Per-run collision counts (None entries = not recorded).
        calibration_path: Provenance.
        validation_path: Provenance.
        config_hash: Provenance.
        scenario: Provenance.
        split: Day split summary.

    Returns:
        The :class:`GateResult`.
    """
    cal = score_day_set(
        CALIBRATION, calibration, replicates, scored_against=scored_against, path=calibration_path
    )
    val = (
        None
        if validation is None
        else score_day_set(
            VALIDATION, validation, replicates, scored_against=scored_against, path=validation_path
        )
    )
    return evaluate_gate(
        cal,
        val,
        wave_speeds_kmh=wave_speeds_kmh,
        wave_detector=wave_detector,
        collision_counts=collision_counts,
        config_hash=config_hash,
        scenario=scenario,
        split=split,
    )


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

_STATUS_TEXT: Final[dict[str, str]] = {
    "pass": "PASS",
    "fail": "FAIL",
    "not_applicable": "NOT APPLICABLE",
    "not_recorded": "NOT RECORDED",
    "not_evaluated": "NOT EVALUATED",
}


def status_text(status: str) -> str:
    """Upper-case status for tables."""
    return _STATUS_TEXT.get(status, status.upper())


def render_markdown(gate: GateResult, *, title: str = "Baseline gate") -> str:
    """The gate as a markdown page (every number from the result).

    Args:
        gate: The result.
        title: Page title.

    Returns:
        Markdown text.
    """
    lines = [f"# {title}", "", f"**{gate.headline()}**", ""]
    if gate.scenario or gate.config_hash:
        lines += [f"Configuration: `{gate.scenario}` (`{gate.config_hash}`).", ""]
    lines += [
        "| Check | Name | Days | Status | Gating | Value | Target | Source |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in gate.checks:
        value = "—" if c.value is None else f"{c.value:.4g}"
        lines.append(
            f"| {c.check} | {c.name} | {c.day_set} | {status_text(c.status)} | "
            f"{'yes' if c.gating else 'no'} | {value} | {c.target} | {c.label} |"
        )
    lines += ["", "## Findings", ""]
    lines += [f"- {c.plain}" for c in gate.checks]
    if gate.reasons:
        lines += ["", "## Why the gate failed", ""]
        lines += [f"- {r}" for r in gate.reasons]
    for name, block in gate.day_sets.items():
        if block is None:
            continue
        bn = block.get("bottlenecks")
        lines += ["", f"## Bottlenecks, {name} days", ""]
        if bn is None:
            lines.append(f"Not evaluated: {block.get('bottleneck_note', '')}.")
            continue
        lines += [
            "| Station pair | Activation | Active [min] | Queue reach | Reproduced | "
            "Median activation offset [min] | Median duration ratio |",
            "|---|---|---|---|---|---|---|",
        ]
        matched = {m["observed"]["pair_index"]: m for m in bn.get("matches", ())}
        for b in bn.get("observed", ()):
            m = matched.get(b["pair_index"])
            reproduced, offset_text, ratio_text = "—", "—", "—"
            if m is not None:
                reproduced = f"{m['n_reproduced']} of {m['n_replicates']}"
                if m.get("activation_offset_s") is not None:
                    offset_text = f"{m['activation_offset_s'] / _S_PER_MIN:+.0f}"
                if m.get("duration_ratio") is not None:
                    ratio_text = f"{m['duration_ratio']:.2f}"
            when = b.get("activation_clock") or f"{b['activation_s']:g} s"
            lines.append(
                f"| {b['upstream']}→{b['downstream']} | {when} | {b['active_s'] / _S_PER_MIN:g} | "
                f"{b['queue_reach_station']} | {reproduced} | {offset_text} | {ratio_text} |"
            )
        if not bn.get("observed"):
            lines.append("| none observed | | | | | | |")
        lines.append("")
        lines += [
            f"- rule {r['rule']}: {'holds' if r['passed'] else 'FAILS'} — {r['detail']}"
            for r in bn.get("rules", ())
        ]
    if gate.split:
        s = gate.split
        lines += [
            "",
            "## Calibration and validation days",
            "",
            f"- Calibration days: {', '.join(s.get('calibration_dates') or []) or 'none'}",
            f"- Validation days: {', '.join(s.get('validation_dates') or []) or 'none'}",
            f"- Seed: {s.get('seed')}; underpowered: {'yes' if s.get('underpowered') else 'no'}"
            + (f" ({s.get('underpowered_reason')})" if s.get("underpowered_reason") else ""),
        ]
    if gate.excluded_detectors:
        lines += ["", "## Excluded detectors", ""]
        lines += [f"- {k}: {v}" for k, v in sorted(gate.excluded_detectors.items())]
    if gate.notes:
        lines += ["", "## Notes", ""]
        lines += [f"- {n}" for n in gate.notes]
    return "\n".join(lines) + "\n"
