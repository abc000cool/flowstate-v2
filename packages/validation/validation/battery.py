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

:func:`analyse_replicate` is the whole per-replicate measurement (metrics,
observed scores, the profile detector's wave speed, insertion) and writes the
two per-seed files the battery re-scores from; :func:`analyse_replicates`
runs it over a run set in a spawn process pool. It lives here rather than in
the script because a spawn worker must be importable in the child, and the
script is loaded by path. Each replicate's trajectory is read by three
functions in one process, so the post-run phase of a 20-seed corridor battery
(1.15 GB per trajectory, about 5 min per replicate) is bounded by the pool
size, not by the replicate count: the 2026-09-24 cloud round spent 100 min
scoring in one process while 31 CPUs idled.
"""

from __future__ import annotations

import json
import math
import multiprocessing
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from validation.criteria import CriteriaProfile
from validation.fields import speed_field
from validation.metrics import Metrics, compute_metrics, warmup_from_meta
from validation.observed import ObservedCorridor, ObservedScores, score_run_against_observed
from validation.waves import WaveDetector

#: Trajectory columns every comparison here needs (contract §3).
TRAJECTORY_COLUMNS: tuple[str, ...] = ("t", "veh_id", "x", "v")

#: Per-replicate files :func:`analyse_replicate` writes beside the run
#: artifacts (docs/CONTRACTS.md, "Corridor battery artifact").
METRICS_FILE: Final[str] = "metrics.json"
SCORES_FILE: Final[str] = "observed_scores.json"

#: Default cap on the scoring pool. One replicate's ``(t, veh_id, x, v)``
#: frame of a 1.15 GB trajectory is several GB in pandas, so the pool is
#: sized for memory, not for the CPU count — and further capped by
#: :func:`score_pool_size` from the machine's available memory.
DEFAULT_SCORE_PROCS: Final[int] = 6

#: Peak RSS one scoring worker reaches per trajectory row [bytes]. Measured
#: 2026-09-24 on synthetic contract-schema trajectories (10 columns, 500k-row
#: groups; pandas 3.0.5, pyarrow 25.0.1, Python 3.12) at 1, 2 and 4 M rows:
#: :func:`validation.metrics.compute_metrics` ≈ 400 B/row (the sort and the
#: two per-vehicle groupby iterations each copy the frame, ``veh_id`` becomes
#: an object array in the crossing count), :func:`score_replicate` ≈ 470
#: B/row, and the high-water mark of the whole :func:`analyse_replicate`
#: ≈ 545 B/row (blocks freed by one function are not all reused by the
#: next). Rounded up for longer vehicle ids and allocator slack. An 80 M-row
#: replicate (a 4-hour corridor at 2 Hz) is therefore ≈ 45 GB per worker,
#: which is what the pool must be sized by.
SCORE_WORKER_BYTES_PER_ROW: Final[int] = 640

#: Fixed part of a scoring worker's RSS [bytes]: interpreter, pandas/scipy
#: imports and the observations (≈ 180 MB measured; rounded up).
SCORE_WORKER_BASE_BYTES: Final[int] = 512 * 1024**2

#: Share of the machine's available memory the scoring pool may plan on;
#: the rest is for the parent, the page cache the reads need, and the error
#: in the per-row estimate.
SCORE_MEMORY_FRACTION: Final[float] = 0.8

#: Where Linux reports the memory a new process can use without swapping.
_MEMINFO_PATH: Final[Path] = Path("/proc/meminfo")

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
        arrived: Vehicles that left it again (``n_vehicles_arrived``), or
            None when the run did not record the counter — runs written
            before it existed, and hand-written fixtures. A zero there would
            read as "nothing completed the corridor", the signature of a
            gridlocked run, so the absence is carried rather than filled.
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
    arrived: int | None
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
        mean_arrived: Mean arrived count over the replicates that recorded
            one, or None when none did. A *mean*, not a sum, precisely
            because it may rest on fewer replicates than
            :attr:`planned` and :attr:`departed` do — a partial sum beside
            two full ones would read as vehicles that vanished.
        n_with_arrived: How many replicates :attr:`mean_arrived` rests on.
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
    mean_arrived: float | None
    n_with_arrived: int
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
            "mean_arrived": self.mean_arrived,
            "n_with_arrived": self.n_with_arrived,
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
        for position, entry in enumerate(raw_ramps):
            if not isinstance(entry, dict) or str(entry.get("kind", "")) != "on":
                continue
            r_planned = _count(entry, "n_planned") or 0
            r_departed = _count(entry, "n_departed") or 0
            # An unnamed ramp is labelled by its index in the scenario's ramp
            # list, the key `meta["ramps"][k]["index"]` carries. Numbering the
            # on-ramps only produced a label that resolved against nothing:
            # "ramp 1" was the second on-ramp, which on a corridor with an
            # off-ramp before it is ramps[2].
            index = _count(entry, "index")
            ramps.append(
                RampInsertion(
                    name=str(entry.get("name", ""))
                    or f"ramp {position if index is None else index}",
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
        arrived=arrived,
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
    with_arrived = [s.arrived for s in stats if s.arrived is not None]
    return InsertionSummary(
        n_runs=len(stats),
        planned=sum(s.planned for s in stats),
        departed=sum(s.departed for s in stats),
        mean_arrived=(sum(with_arrived) / len(with_arrived)) if with_arrived else None,
        n_with_arrived=len(with_arrived),
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


def json_safe(obj: object) -> object:
    """Recursively replace non-finite floats with ``None`` (JSON has no NaN)."""
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


@dataclass(frozen=True)
class ReplicateAnalysis:
    """Everything the battery measures on one completed replicate.

    Attributes:
        metrics: The standard metric set (:func:`validation.metrics.compute_metrics`).
        scores: The replicate against the corridor's observations.
        wave_speed_kmh: The criteria profile detector's backward wave speed
            [km/h] (:func:`replicate_wave_speed_kmh`); NaN = no front.
        insertion: Planned vs departed vehicles (:func:`insertion_stats`).
    """

    metrics: Metrics
    scores: ObservedScores
    wave_speed_kmh: float
    insertion: InsertionStats


def analyse_replicate(
    run_dir: str | Path,
    observed: ObservedCorridor,
    *,
    profile: CriteriaProfile,
    x_ref: float,
    span: tuple[float, float],
    x_offset_m: float,
) -> ReplicateAnalysis:
    """Measure one replicate and write its per-seed files.

    Writes :data:`METRICS_FILE` (metrics, the criterion wave speed and its
    detector, ``x_ref``/``span``, insertion) and :data:`SCORES_FILE` (the
    :class:`validation.observed.ObservedScores`) into ``run_dir``, so a
    finished battery can be re-scored (:func:`load_replicate_analysis`)
    without re-simulating. The trajectory is read three times (metrics,
    scores, wave speed) — the reads are cheap beside the binning and the
    groupbys, and one process per replicate is the unit of parallelism.

    Args:
        run_dir: Replicate directory.
        observed: The corridor's observations.
        profile: Criteria profile (its detector measures the wave speed).
        x_ref: Throughput cross-section [m], trajectory coordinates.
        span: Travel-time span [m], trajectory coordinates.
        x_offset_m: Simulation ``x`` of the observed origin [m].

    Returns:
        The replicate's :class:`ReplicateAnalysis`.
    """
    path = Path(run_dir)
    metrics = compute_metrics(path, x_ref=x_ref, span=span)
    scores = score_replicate(path, observed, x_offset_m=x_offset_m)
    wave_speed = replicate_wave_speed_kmh(path, profile.wave_detector)
    insertion = insertion_stats(load_meta(path))
    (path / METRICS_FILE).write_text(
        json.dumps(
            json_safe(
                {
                    "metrics": asdict(metrics),
                    "criterion_wave_speed_kmh": wave_speed,
                    "criterion_detector": profile.wave_detector.name,
                    "x_ref_m": x_ref,
                    "span_m": list(span),
                    "insertion": insertion.to_dict(),
                }
            ),
            indent=2,
            allow_nan=False,
        )
    )
    (path / SCORES_FILE).write_text(
        json.dumps(json_safe(scores.to_dict()), indent=2, allow_nan=False)
    )
    return ReplicateAnalysis(
        metrics=metrics, scores=scores, wave_speed_kmh=wave_speed, insertion=insertion
    )


def load_replicate_analysis(run_dir: str | Path) -> ReplicateAnalysis:
    """Re-read one replicate's stored per-seed files (``--criteria-only``).

    The insertion stats are re-read from ``meta.json`` rather than from the
    stored ``metrics.json`` block: ``meta.json`` is the completion marker and
    is never pruned, so there is one source for these counters and no way for
    the stored copy to be the one a reader sees.

    Args:
        run_dir: Replicate directory holding the files
            :func:`analyse_replicate` wrote.

    Returns:
        The stored :class:`ReplicateAnalysis` (NaN restored from ``null``).

    Raises:
        FileNotFoundError: The replicate was never analysed.
    """
    path = Path(run_dir)
    for name in (METRICS_FILE, SCORES_FILE):
        if not (path / name).is_file():
            raise FileNotFoundError(
                f"{path / name} is missing; run the battery without --criteria-only first"
            )
    stored = json.loads((path / METRICS_FILE).read_text())
    raw = dict(stored["metrics"])
    for key, value in raw.items():
        if value is None:
            raw[key] = math.nan
    wave = stored.get("criterion_wave_speed_kmh")
    scores = ObservedScores.from_dict(json.loads((path / SCORES_FILE).read_text()))
    return ReplicateAnalysis(
        metrics=Metrics(**raw),
        scores=scores,
        wave_speed_kmh=math.nan if wave is None else float(wave),
        insertion=insertion_stats(load_meta(path)),
    )


def trajectory_rows(run_dir: str | Path) -> int | None:
    """Row count of a replicate's trajectory file, from the parquet footer only.

    Args:
        run_dir: Replicate directory.

    Returns:
        The number of rows, or ``None`` when the file is absent (pruned).
    """
    import pyarrow.parquet as pq

    path = Path(run_dir) / "trajectories.parquet"
    if not path.is_file():
        return None
    with open(path, "rb") as f:
        return int(pq.ParquetFile(f).metadata.num_rows)


def score_worker_bytes(rows: int) -> int:
    """Estimated peak RSS [bytes] of one scoring worker on a ``rows``-row replicate."""
    return SCORE_WORKER_BASE_BYTES + SCORE_WORKER_BYTES_PER_ROW * max(0, int(rows))


def available_memory_bytes() -> int | None:
    """``MemAvailable`` from ``/proc/meminfo`` [bytes]; ``None`` where absent.

    Linux only (the cloud VMs); on other platforms the pool is not capped by
    memory and the caller's own limit stands.
    """
    if not _MEMINFO_PATH.is_file():
        return None
    for line in _MEMINFO_PATH.read_text().splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return None


def score_pool_size(
    n_procs: int,
    dirs: Sequence[Path],
    *,
    available_bytes: int | None = None,
    fraction: float = SCORE_MEMORY_FRACTION,
) -> tuple[int, int | None]:
    """Scoring-pool size that fits in memory: ``n_procs`` capped by the estimate.

    Every worker holds one replicate's trajectory frame and its copies
    (:data:`SCORE_WORKER_BYTES_PER_ROW`), so the pool is bounded by
    ``fraction × available / per_worker``, where ``per_worker`` is
    :func:`score_worker_bytes` of the largest replicate in ``dirs``. A 20-seed
    battery of a 4-hour corridor (80 M rows a replicate) needs ≈ 45 GB per
    worker: six workers on a 125 GB machine would be killed in the scoring
    phase after the simulation had already run for hours.

    Args:
        n_procs: The requested pool size (the CPU-side limit).
        dirs: Replicate directories; pruned ones (no trajectory) are ignored.
        available_bytes: Memory to plan against; ``None`` reads
            :func:`available_memory_bytes`, and when that is unknown too
            (not Linux) the pool is not capped.
        fraction: Share of ``available_bytes`` the pool may plan on.

    Returns:
        ``(pool_size, per_worker_bytes)`` — the size, at least 1 and at most
        ``n_procs``, and the per-worker estimate it rests on (``None`` when no
        replicate holds a trajectory).
    """
    rows = [r for r in (trajectory_rows(d) for d in dirs) if r is not None]
    if not rows:
        return max(1, n_procs), None
    per_worker = score_worker_bytes(max(rows))
    available = available_memory_bytes() if available_bytes is None else available_bytes
    if available is None:
        return max(1, n_procs), per_worker
    fits = int(fraction * available) // per_worker
    return max(1, min(n_procs, fits)), per_worker


#: One scoring-pool payload: ``(run_dir, observed, profile, x_ref, span, x_offset_m)``.
_AnalysePayload = tuple[str, ObservedCorridor, CriteriaProfile, float, tuple[float, float], float]


def _analyse_worker(payload: _AnalysePayload) -> ReplicateAnalysis:
    """Spawn-pool worker: :func:`analyse_replicate` on one payload."""
    run_dir, observed, profile, x_ref, span, x_offset_m = payload
    return analyse_replicate(
        run_dir, observed, profile=profile, x_ref=x_ref, span=span, x_offset_m=x_offset_m
    )


def analyse_replicates(
    dirs: Sequence[Path],
    observed: ObservedCorridor,
    *,
    profile: CriteriaProfile,
    x_ref: float,
    span: tuple[float, float],
    x_offset_m: float,
    n_procs: int = 1,
    on_complete: Callable[[Path, ReplicateAnalysis, float], None] | None = None,
) -> list[ReplicateAnalysis]:
    """:func:`analyse_replicate` over a run set, in a spawn process pool.

    Replicates are independent, so the result of each is the same whatever
    the pool size and the per-seed files are byte-identical; only the
    wall-clock changes. ``n_procs <= 1`` runs in the calling process (no
    pool, no spawn cost — the CI path). Fresh spawned children never load
    libsumo, so pandas reads the parquet files by path without the libarrow
    clash documented on ``microsim.runner._write_parquet``; that is why the
    scoring is not done inside the simulation worker.

    Args:
        dirs: Replicate directories, in seed order.
        observed: The corridor's observations.
        profile: Criteria profile.
        x_ref: Throughput cross-section [m].
        span: Travel-time span [m].
        x_offset_m: Simulation ``x`` of the observed origin [m].
        n_procs: Pool size; sized for memory (:data:`DEFAULT_SCORE_PROCS`),
            since each worker holds one trajectory frame.
        on_complete: Called as ``on_complete(run_dir, analysis, seconds)``
            the moment a replicate is scored, in completion order, with the
            seconds that replicate took (in-process) or the seconds since the
            pool started (pool).

    Returns:
        One :class:`ReplicateAnalysis` per directory, in ``dirs`` order.

    Raises:
        RuntimeError: A pool worker died or a replicate raised; the message
            names the directory.
    """
    payloads: list[_AnalysePayload] = [
        (str(d), observed, profile, x_ref, span, x_offset_m) for d in dirs
    ]
    t0 = time.perf_counter()
    if n_procs <= 1 or len(dirs) <= 1:
        out: list[ReplicateAnalysis] = []
        for d, payload in zip(dirs, payloads, strict=True):
            t_rep = time.perf_counter()
            analysis = _analyse_worker(payload)
            out.append(analysis)
            if on_complete is not None:
                on_complete(d, analysis, time.perf_counter() - t_rep)
        return out

    results: dict[int, ReplicateAnalysis] = {}
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=min(n_procs, len(dirs)), mp_context=ctx) as ex:
        futures = {ex.submit(_analyse_worker, p): i for i, p in enumerate(payloads)}
        for fut in as_completed(futures):
            index = futures[fut]
            try:
                results[index] = fut.result()
            except Exception as exc:
                raise RuntimeError(
                    f"scoring {dirs[index]} failed in the scoring pool: {type(exc).__name__}"
                ) from exc
            if on_complete is not None:
                on_complete(dirs[index], results[index], time.perf_counter() - t0)
    return [results[i] for i in range(len(dirs))]
