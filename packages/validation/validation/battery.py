"""Per-replicate scoring shared by the corridor battery and the report job.

``scripts/corridor_battery.py`` (a seeded replicate battery on a corridor with
observations) and ``api.jobs.report_job`` (the same comparison on a set of
finished API runs) need identical answers to the same questions about one
completed replicate directory: what its measurement window is, how it scores
against the corridor's observations (:mod:`validation.observed`), what
backward wave speed a given detector reads on its field, and whether the run
actually put its planned demand on the network at all
(:func:`insertion_stats`). They live here so the two callers cannot drift
apart — the failure mode CLAUDE.md §0.1 is about, where a number in a report
and the same number in an artifact were computed by two copies of the code.

Everything in this module reads a replicate directory as written by
``microsim.runner.run_micro`` (docs/CONTRACTS.md §3): ``meta.json`` plus
``trajectories.parquet``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from validation.fields import speed_field
from validation.metrics import warmup_from_meta
from validation.observed import ObservedCorridor, ObservedScores, score_run_against_observed
from validation.waves import WaveDetector

#: Trajectory columns every comparison here needs (contract §3).
TRAJECTORY_COLUMNS: tuple[str, ...] = ("t", "veh_id", "x", "v")

#: Dimensionless fraction → percent (not an SI conversion; kept in one place
#: so no bare ``* 100`` appears in the verdict text).
_PERCENT: Final[float] = 100.0

#: Share of its plan a run must actually insert before its insertion counts
#: as healthy. 0.98 is this repo's demand-limited convention
#: (``scripts/calibrate_capacity.py``): below it, vehicles are queueing at the
#: boundary instead of entering, so the realized demand is no longer the
#: configured demand and every flow-based metric describes a different
#: scenario from the one that was asked for.
HEALTHY_DEPARTED_FRACTION: Final[float] = 0.98

#: Vehicles an on-ramp must have planned before its delivery share is
#: diagnostic rather than small-sample noise.
STARVED_RAMP_MIN_PLANNED: Final[int] = 100

#: Delivery share below which such an on-ramp is called starved.
STARVED_RAMP_FRACTION: Final[float] = 0.5

#: Verdict of a run whose demand plan was empty (nothing to insert).
NO_PLAN_VERDICT: Final[str] = "no vehicles planned"

#: Verdict of a run that inserted its plan and fed every sizeable on-ramp.
OK_VERDICT: Final[str] = "ok"


def load_meta(run_dir: str | Path) -> dict[str, Any]:
    """Parse one replicate's ``meta.json``.

    Args:
        run_dir: Replicate directory.

    Returns:
        The parsed metadata.

    Raises:
        ValueError: The file does not hold a JSON object.
    """
    raw = json.loads((Path(run_dir) / "meta.json").read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{run_dir}: meta.json is not a JSON object")
    return raw


@dataclass(frozen=True)
class RampInsertion:
    """What one on-ramp of a replicate managed to put on the network.

    Attributes:
        name: Ramp name as the scenario names it.
        planned: Vehicles the demand plan assigned to this ramp.
        departed: How many of them actually entered the network.
        fraction: ``departed / planned``; NaN when nothing was planned.
    """

    name: str
    planned: int
    departed: int
    fraction: float

    @property
    def starved(self) -> bool:
        """Whether this ramp is starved (≥ :data:`STARVED_RAMP_MIN_PLANNED`
        planned vehicles, less than :data:`STARVED_RAMP_FRACTION` of them
        delivered)."""
        return self.planned >= STARVED_RAMP_MIN_PLANNED and self.fraction < STARVED_RAMP_FRACTION

    def to_dict(self) -> dict[str, Any]:
        """JSON form (the battery artifact's per-seed table)."""
        return {
            "name": self.name,
            "planned": self.planned,
            "departed": self.departed,
            "fraction": self.fraction,
            "starved": self.starved,
        }


@dataclass(frozen=True)
class InsertionStats:
    """Whether one replicate actually ran the demand it was configured with.

    A replicate whose vehicles never departed is not a slow corridor, it is a
    different scenario: the queue sits outside the network, throughput is the
    insertion rate rather than the corridor's, and every metric computed from
    it describes demand that was never applied. Two 20-seed cloud batteries
    (2026-09-22) each burned an hour before this was visible in the per-seed
    ``meta.json``; it is cheap to read and belongs in front of every reader.

    Attributes:
        planned: Vehicles in the run's demand plan (``n_vehicles_planned``).
        departed: Vehicles that entered the network (``n_vehicles_departed``).
        arrived: Vehicles that left it again (``n_vehicles_arrived``).
        departed_fraction: ``departed / planned``; NaN when nothing was
            planned.
        ramps: One entry per on-ramp, in scenario order (empty for networks
            without ramps).
        starved_ramps: Names of the ramps whose :attr:`RampInsertion.starved`
            is true, in the same order.
        verdict: One line: :data:`OK_VERDICT`, ``"backlog: N % of planned
            vehicles never departed"``, ``"starved ramps: <names>"`` (both,
            joined by ``"; "``, when both apply), or
            :data:`NO_PLAN_VERDICT`.
    """

    planned: int
    departed: int
    arrived: int
    departed_fraction: float
    ramps: tuple[RampInsertion, ...]
    starved_ramps: tuple[str, ...]
    verdict: str

    def to_dict(self) -> dict[str, Any]:
        """JSON form (the battery artifact, the per-seed ``metrics.json``)."""
        return {
            "planned": self.planned,
            "departed": self.departed,
            "arrived": self.arrived,
            "departed_fraction": self.departed_fraction,
            "ramps": [r.to_dict() for r in self.ramps],
            "starved_ramps": list(self.starved_ramps),
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class InsertionSummary:
    """:class:`InsertionStats` pooled over the replicates of a run set.

    Attributes:
        n_runs: Replicates contributing.
        planned: Planned vehicles summed over them.
        departed: Departed vehicles summed over them.
        arrived: Arrived vehicles summed over them.
        mean_departed_fraction: Mean of the per-replicate fractions (NaN
            contributions dropped, as :func:`mean_finite` does).
        min_departed_fraction: The worst replicate's fraction; NaN when none
            is defined.
        starved_ramps: Ramps starved in at least one replicate, first-seen
            order.
        verdict: The one-line verdict of the pooled fractions, worded as
            :attr:`InsertionStats.verdict`.
    """

    n_runs: int
    planned: int
    departed: int
    arrived: int
    mean_departed_fraction: float
    min_departed_fraction: float
    starved_ramps: tuple[str, ...]
    verdict: str

    def to_dict(self) -> dict[str, Any]:
        """JSON form (the battery artifact's ``insertion`` block)."""
        return {
            "n_runs": self.n_runs,
            "planned": self.planned,
            "departed": self.departed,
            "arrived": self.arrived,
            "mean_departed_fraction": self.mean_departed_fraction,
            "min_departed_fraction": self.min_departed_fraction,
            "starved_ramps": list(self.starved_ramps),
            "verdict": self.verdict,
        }


def _count(source: Mapping[str, Any], key: str) -> int | None:
    """One integer counter of a metadata block; None when it is absent."""
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def records_insertion(meta: Mapping[str, Any]) -> bool:
    """Whether ``meta`` carries the counters :func:`insertion_stats` needs.

    Args:
        meta: Parsed ``meta.json``.

    Returns:
        True when both ``n_vehicles_planned`` and ``n_vehicles_departed``
        are present (runs written before those counters existed, and
        hand-written test fixtures, carry neither).
    """
    return _count(meta, "n_vehicles_planned") is not None and (
        _count(meta, "n_vehicles_departed") is not None
    )


def _verdict(departed_fraction: float, starved: Sequence[str]) -> str:
    """The one-line verdict for a departed fraction and its starved ramps."""
    if not math.isfinite(departed_fraction):
        return NO_PLAN_VERDICT
    problems: list[str] = []
    if departed_fraction < HEALTHY_DEPARTED_FRACTION:
        missed = _PERCENT * (1.0 - departed_fraction)
        problems.append(f"backlog: {missed:.0f} % of planned vehicles never departed")
    if starved:
        problems.append(f"starved ramps: {', '.join(starved)}")
    return "; ".join(problems) if problems else OK_VERDICT


def insertion_stats(meta: Mapping[str, Any]) -> InsertionStats:
    """How much of one replicate's demand plan actually entered the network.

    Args:
        meta: Parsed ``meta.json`` (:func:`load_meta`).

    Returns:
        The replicate's :class:`InsertionStats`, including the per-on-ramp
        breakdown when the run recorded ramps (off-ramps carry no insertion
        and are skipped).

    Raises:
        ValueError: The metadata carries no insertion counters; test with
            :func:`records_insertion` when that is a possibility.
    """
    planned = _count(meta, "n_vehicles_planned")
    departed = _count(meta, "n_vehicles_departed")
    if planned is None or departed is None:
        raise ValueError(
            "meta.json carries no n_vehicles_planned / n_vehicles_departed insertion counters"
        )
    arrived = _count(meta, "n_vehicles_arrived")
    raw_ramps = meta.get("ramps")
    ramps: list[RampInsertion] = []
    if isinstance(raw_ramps, list):
        for entry in raw_ramps:
            if not isinstance(entry, dict) or str(entry.get("kind", "")) != "on":
                continue
            r_planned = _count(entry, "n_planned") or 0
            r_departed = _count(entry, "n_departed") or 0
            ramps.append(
                RampInsertion(
                    name=str(entry.get("name", "")) or f"ramp {len(ramps)}",
                    planned=r_planned,
                    departed=r_departed,
                    fraction=(r_departed / r_planned) if r_planned > 0 else math.nan,
                )
            )
    starved = tuple(r.name for r in ramps if r.starved)
    fraction = (departed / planned) if planned > 0 else math.nan
    return InsertionStats(
        planned=planned,
        departed=departed,
        arrived=0 if arrived is None else arrived,
        departed_fraction=fraction,
        ramps=tuple(ramps),
        starved_ramps=starved,
        verdict=_verdict(fraction, starved),
    )


def aggregate_insertion(stats: Sequence[InsertionStats]) -> InsertionSummary | None:
    """Pool per-replicate insertion into one summary.

    Args:
        stats: One entry per replicate.

    Returns:
        The :class:`InsertionSummary`, or None when ``stats`` is empty —
        a run set that recorded no insertion counters says nothing about
        insertion, and a zero would be a claim.
    """
    if not stats:
        return None
    fractions = [s.departed_fraction for s in stats]
    finite = [f for f in fractions if math.isfinite(f)]
    starved: dict[str, None] = {}
    for s in stats:
        for name in s.starved_ramps:
            starved.setdefault(name, None)
    mean_fraction = mean_finite(fractions)
    return InsertionSummary(
        n_runs=len(stats),
        planned=sum(s.planned for s in stats),
        departed=sum(s.departed for s in stats),
        arrived=sum(s.arrived for s in stats),
        mean_departed_fraction=mean_fraction,
        min_departed_fraction=min(finite) if finite else math.nan,
        starved_ramps=tuple(starved),
        verdict=_verdict(mean_fraction, tuple(starved)),
    )


def measurement_window(meta: Mapping[str, Any]) -> tuple[float, float]:
    """The replicate's ``(warmup_s, duration_s)`` [s] from its metadata.

    ``warmup_s`` is resolved by :func:`validation.metrics.warmup_from_meta`
    (the single place the warm-up convention lives) and ``duration_s`` is the
    run's configured simulated length — the *nominal* recorded span, not the
    last output sample, so the final analysis window is compared rather than
    dropped for want of one sampling interval.

    Args:
        meta: Parsed ``meta.json``.

    Returns:
        ``(warmup_s, duration_s)``.

    Raises:
        ValueError: The metadata carries no ``config.sim.duration_s``.
    """
    config = meta.get("config")
    sim = config.get("sim") if isinstance(config, dict) else None
    value = sim.get("duration_s") if isinstance(sim, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("meta.json carries no config.sim.duration_s")
    return warmup_from_meta(meta), float(value)


def read_trajectories(
    run_dir: str | Path, columns: Sequence[str] = TRAJECTORY_COLUMNS
) -> pd.DataFrame:
    """Read one replicate's trajectory rows.

    Args:
        run_dir: Replicate directory.
        columns: Columns to read (default :data:`TRAJECTORY_COLUMNS`).

    Returns:
        The trajectory frame.
    """
    return pd.read_parquet(Path(run_dir) / "trajectories.parquet", columns=list(columns))


def score_replicate(
    run_dir: str | Path,
    observed: ObservedCorridor,
    *,
    x_offset_m: float = 0.0,
) -> ObservedScores:
    """Score one completed replicate against a corridor's observations.

    Args:
        run_dir: Replicate directory (``meta.json`` + ``trajectories.parquet``).
        observed: The corridor's observations.
        x_offset_m: Simulation ``x`` of the observed origin [m]; see
            :func:`validation.observed.score_run_against_observed`.

    Returns:
        The replicate's :class:`validation.observed.ObservedScores`.
    """
    meta = load_meta(run_dir)
    warmup_s, duration_s = measurement_window(meta)
    trajectories = read_trajectories(run_dir)
    return score_run_against_observed(
        trajectories,
        observed,
        warmup_s=warmup_s,
        duration_s=duration_s,
        x_offset_m=x_offset_m,
    )


def replicate_wave_speed_kmh(run_dir: str | Path, detector: WaveDetector) -> float:
    """Backward wave-front speed [km/h] one detector reads on a replicate.

    The field is binned at the detector's own bins (a
    :class:`validation.waves.WaveDetector` refuses any other binning) over the
    replicate's measurement window, so the reading describes the scored
    period — exactly what :func:`validation.report.generate_report` measures
    for its wave-speed criterion row.

    Args:
        run_dir: Replicate directory.
        detector: Detector recipe, normally the criteria profile's.

    Returns:
        Mean backward-front speed magnitude [km/h]; NaN when the detector
        found no backward front.
    """
    meta = load_meta(run_dir)
    warmup_s, _ = measurement_window(meta)
    traj = read_trajectories(run_dir, columns=("t", "x", "v"))
    if warmup_s > 0.0:
        windowed = traj.loc[traj["t"] >= warmup_s]
        if not windowed.empty:
            traj = windowed
    field = speed_field(traj, dt_bin=detector.dt_bin_s, dx_bin=detector.dx_bin_m)
    return float(detector.measure(field).speed_kmh)


def mean_finite(values: Sequence[float]) -> float:
    """Mean of the finite entries; NaN when none are finite.

    Replicates that produced no reading (no backward front, no comparable
    speed cell) contribute nothing rather than a zero — the convention
    :func:`validation.metrics.aggregate` already follows.

    Args:
        values: Per-replicate values.

    Returns:
        The mean, or NaN.
    """
    finite = [float(v) for v in values if math.isfinite(float(v))]
    return float(np.mean(finite)) if finite else math.nan
