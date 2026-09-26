"""Per-replicate scoring shared by the corridor battery and the report job.

``scripts/corridor_battery.py`` (a seeded replicate battery on a corridor with
observations) and ``api.jobs.report_job`` (the same comparison on a set of
finished API runs) need identical answers to the same questions about one
completed replicate directory: what its measurement window is, how it scores
against the corridor's observations (:mod:`validation.observed`), what
backward wave speed a given detector reads on its field, whether the run
actually put its planned demand on the network at all
(:func:`insertion_stats`), and whether the model itself misbehaved
(:func:`collision_summary`, :func:`forced_change_summary`). They live here so
the two callers cannot drift
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
from validation.metrics import (
    Metrics,
    ci,
    compute_metrics,
    n_window_rows,
    time_window_rows,
    vehicle_codes,
    warmup_from_meta,
)
from validation.observed import ObservedCorridor, ObservedScores, score_run_against_observed
from validation.waves import WaveDetector

#: Trajectory columns every comparison here needs (contract §3).
TRAJECTORY_COLUMNS: tuple[str, ...] = ("t", "veh_id", "x", "v")

#: Rows per batch when :func:`read_scoring_frame` fills its arrays: the
#: transient Arrow memory is one batch, not the file.
SCORING_READ_BATCH_ROWS: Final[int] = 262_144

#: Per-replicate files :func:`analyse_replicate` writes beside the run
#: artifacts (docs/CONTRACTS.md, "Corridor battery artifact").
METRICS_FILE: Final[str] = "metrics.json"
SCORES_FILE: Final[str] = "observed_scores.json"

#: Default cap on the scoring pool. One replicate's ``(t, veh_id, x, v)``
#: frame of a 1.15 GB trajectory is a few GB in pandas, so the pool is
#: sized for memory, not for the CPU count — and further capped by
#: :func:`score_pool_size` from the machine's available memory.
DEFAULT_SCORE_PROCS: Final[int] = 6

#: Peak RSS one scoring worker reaches per trajectory row [bytes]. Measured
#: 2026-09-24 (block 3) on synthetic contract-schema trajectories (the ten
#: ``_TRAJ_SCHEMA_BASE`` columns, 500k-row groups, ``veh_{k}`` ids; pandas
#: 3.0.5, pyarrow 25.0.1, numpy 2.5.2, Python 3.12, macOS arm64) at 0.97,
#: 1.97 and 3.88 M rows, one fresh process per function,
#: ``resource.getrusage(RUSAGE_SELF).ru_maxrss`` minus the 177 MB import
#: floor. Before the rewrite the whole :func:`analyse_replicate` reached
#: 416 B/row at 3.88 M rows (slope 418 B/row between 0.97 and 3.88 M):
#: :func:`validation.metrics.compute_metrics` 341, :func:`score_replicate`
#: 239, :func:`replicate_wave_speed_kmh` 130 — sorted copies of the frame,
#: ``veh_id`` as an object array, two ``groupby`` iterations each taking a
#: sorted copy, one sort per station cross-section. After: the trajectory
#: is read once in row batches into four arrays (32 B/row live,
#: :func:`read_scoring_frame` 78 B/row peak), ids are integer codes, one
#: ``(veh_id, t)`` sort serves every per-vehicle quantity, and the whole
#: :func:`analyse_replicate` peaks at 115 B/row (slope 95 B/row); the
#: outputs are byte-identical. Rounded up for allocator slack (macOS never
#: returns freed pages; the estimate is an upper bound for glibc). An 80
#: M-row replicate (a 4-hour corridor at 2 Hz) is therefore ≈ 13 GB per
#: worker, which is what the pool is sized by.
SCORE_WORKER_BYTES_PER_ROW: Final[int] = 160

#: Fixed part of a scoring worker's RSS [bytes]: interpreter, pandas/scipy
#: imports, pyarrow's compute and parquet modules and the observations
#: (≈ 240 MB measured as the intercept of the reader's fit; rounded up).
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

#: Share of a weaving section's reached exiters given up at the gore's end
#: (``weave_sections[i].n_missed_exit / n_reached_section_exiting``, summed
#: over the replicates) above which the battery's insertion verdict is
#: degraded ("exits given up: k % at <section>"). A given-up exiter is
#: rerouted through (docs/CONTRACTS.md §2, exit side), so the exit's link
#: flow is short by one vehicle and every mainline link downstream carries
#: one more: the GEH link-flow comparison is wrong by that count on every
#: one of those links. Derivation: for an hourly flow ``c`` short by the
#: share ``s``, ``GEH = √(2 (s c)² / ((2 − s) c)) = s √c · √(2 / (2 − s))``,
#: ≈ ``s √c`` for small ``s``. The full GEH-5 tolerance at ~1,000 veh/h (an
#: exit at the T.H.52 weave's demand) is therefore ``s ≈ 5 / √1000`` — 15 %
#: short (16 % over) — far too loose a threshold: an exit flow 15 % wrong is
#: the whole criterion spent on the artefact. At 2 % the reroute alone moves
#: a 1,000 veh/h exit link-hour's GEH by ``0.02 · √1000 · √(2/1.98)`` ≈ 0.64,
#: an eighth of the threshold, and the same 20 veh/h over-count moves a
#: 4,900 veh/h downstream mainline link-hour's by ≈ 0.29; only a link-hour
#: already within ~0.6 of GEH 5 can change its pass/fail because of it. The
#: verdict is degraded when the share is strictly above this value.
MISSED_EXIT_SHARE_THRESHOLD: Final[float] = 0.02

#: Prefix of the degraded verdict a weave section's given-up exits produce.
MISSED_EXIT_VERDICT_PREFIX: Final[str] = "exits given up: "

#: Scale of the collision rate the battery artifact and the report print:
#: collisions per this many vehicles that entered the network. Departed
#: vehicles, not vehicle-km: the collision counter covers the whole run
#: (warm-up included) and so does ``n_vehicles_departed``, both in the same
#: ``meta.json``, whereas every distance the metrics measure is windowed.
COLLISION_RATE_PER_VEHICLES: Final[int] = 1000

#: Lane label of a logged collision event that names no lane.
UNKNOWN_LANE: Final[str] = "unknown"

#: What the battery artifact's ``collisions`` block means (docs/CONTRACTS.md,
#: "Model integrity").
COLLISION_DEFINITION: Final[str] = (
    "total is the sum of meta.json n_collisions over the n_runs_recorded replicates "
    "that record the counter: SUMO collision detections over the whole run, warm-up "
    "included, a colliding pair counted once when it is first detected "
    "(docs/CONTRACTS.md). per_run is the two-sided t-interval over those "
    "per-replicate counts. rate.value = rate.per_vehicles * rate.n_collisions / "
    "rate.n_departed over the rate.n_runs replicates that record both n_collisions "
    "and n_vehicles_departed (null when no vehicle departed). locations groups the "
    "logged events (meta.json collisions: each run lists its first events only, "
    "n_logged of total) by SUMO lane; edge is the lane id without its _<index> "
    "suffix, pos_m_min / pos_m_max the positions along the lane [m], runs the "
    "replicates in which the lane had one. A replicate without the counter is not "
    "recorded (per_seed n_collisions null), never zero; the block is null when no "
    "replicate records it."
)


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


def weave_exit_summary(
    metas: Sequence[Mapping[str, Any]], *, threshold: float = MISSED_EXIT_SHARE_THRESHOLD
) -> dict[str, Any]:
    """Given-up exits per weaving section, pooled over the replicates.

    The weave step reroutes an exit-bound vehicle halted within
    ``exit_giveup_m`` of the gore's end onto the corridor's last edge and
    counts it in ``meta.json["weave_sections"][i]["n_missed_exit"]``
    (docs/CONTRACTS.md §2, exit side). Such a vehicle is missing from the
    exit's link flow and present on every mainline link downstream, which the
    GEH comparison cannot tell from a demand error; this summary is how the
    battery artifact and the report surface the count
    (:data:`MISSED_EXIT_SHARE_THRESHOLD` documents the threshold).

    Args:
        metas: One parsed ``meta.json`` per replicate (:func:`load_meta`).
        threshold: Share of reached exiters above which a section is flagged.

    Returns:
        ``{"threshold_share", "n_runs", "sections", "verdict"}``: ``n_runs``
        is the number of metas that list ``weave_sections`` at all;
        ``sections`` has one entry per section, keyed by its on-ramp in
        first-seen order, ``{"ramp", "exit", "n_runs", "reached",
        "missed_exit": {"n", "share"}, "flagged"}`` with ``n`` and
        ``reached`` summed over the replicates that recorded both counters
        (a meta written before ``n_missed_exit`` existed contributes nothing
        to the section, and ``n_runs`` counts the ones that did), ``share =
        n / reached`` (NaN when no exiter reached the section) and
        ``flagged`` true when the share is finite and strictly above
        ``threshold``; ``verdict`` is :data:`OK_VERDICT` or
        ``"exits given up: k % at <ramp>"`` over the flagged sections, in
        order, separated by ``", "``. A run set without weaving sections
        yields an empty ``sections`` list and the OK verdict — it says
        nothing, it does not claim zero.
    """
    sections: dict[str, dict[str, Any]] = {}
    n_runs = 0
    for meta in metas:
        raw = meta.get("weave_sections")
        if not isinstance(raw, list):
            continue
        n_runs += 1
        for position, entry in enumerate(raw):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("ramp", "") or "") or f"section {position}"
            section = sections.setdefault(
                name, {"ramp": name, "exit": None, "n_runs": 0, "reached": 0, "n": 0}
            )
            if section["exit"] is None and entry.get("exit"):
                section["exit"] = str(entry["exit"])
            missed = _count(entry, "n_missed_exit")
            reached = _count(entry, "n_reached_section_exiting")
            if missed is None or reached is None:
                continue
            section["n_runs"] += 1
            section["reached"] += reached
            section["n"] += missed
    rows: list[dict[str, Any]] = []
    flagged: list[str] = []
    for section in sections.values():
        share = section["n"] / section["reached"] if section["reached"] > 0 else math.nan
        is_flagged = math.isfinite(share) and share > threshold
        rows.append(
            {
                "ramp": section["ramp"],
                "exit": section["exit"],
                "n_runs": section["n_runs"],
                "reached": section["reached"],
                "missed_exit": {"n": section["n"], "share": share},
                "flagged": is_flagged,
            }
        )
        if is_flagged:
            flagged.append(f"{_PERCENT * share:.1f} % at {section['ramp']}")
    return {
        "threshold_share": threshold,
        "n_runs": n_runs,
        "sections": rows,
        "verdict": (MISSED_EXIT_VERDICT_PREFIX + ", ".join(flagged)) if flagged else OK_VERDICT,
    }


def degraded_verdict(verdict: str, weave_verdict: str) -> str:
    """The insertion verdict with a weave-exit verdict appended.

    Args:
        verdict: :attr:`InsertionSummary.verdict` (or a replicate's).
        weave_verdict: ``weave_exit_summary(...)["verdict"]``.

    Returns:
        ``verdict`` unchanged when the weave verdict is :data:`OK_VERDICT`;
        the weave verdict alone when only the insertion is OK; otherwise both,
        joined by ``"; "`` like the insertion verdict's own problems.
    """
    if weave_verdict == OK_VERDICT:
        return verdict
    if verdict == OK_VERDICT:
        return weave_verdict
    return f"{verdict}; {weave_verdict}"


def collision_count(meta: Mapping[str, Any]) -> int | None:
    """``meta.json["n_collisions"]`` of one run; None when it is not recorded.

    The runner records the counter since 2026-09-16 (docs/CONTRACTS.md); a
    run written before then, or a hand-written fixture, carries no key and
    its collision count is unknown, which is not the same as zero.

    Args:
        meta: Parsed ``meta.json`` (:func:`load_meta`).

    Returns:
        The count, or None.
    """
    return _count(meta, "n_collisions")


def lane_edge(lane: str) -> str:
    """The edge id of a SUMO lane id: the id without its ``_<index>`` suffix.

    SUMO names lane ``k`` of edge ``e`` ``e_k``, junction-internal lanes
    included (``:J3_0_0`` is lane 0 of ``:J3_0``). An id without a numeric
    suffix is returned unchanged.

    Args:
        lane: SUMO lane id.

    Returns:
        The edge id.
    """
    edge, sep, index = lane.rpartition("_")
    return edge if sep and edge and index.isdigit() else lane


def _run_label(meta: Mapping[str, Any], position: int) -> str | int:
    """A run's default label: its seed, else its position in the run set."""
    seed = meta.get("seed")
    if isinstance(seed, (str, int)) and not isinstance(seed, bool):
        return seed
    return position


def collision_summary(
    metas: Sequence[Mapping[str, Any]], *, labels: Sequence[str | int] | None = None
) -> dict[str, Any] | None:
    """Collisions of a run set, pooled over its runs' ``meta.json``.

    A collision in a car-following simulation is a model defect, not a
    traffic outcome. The runner records the exact count (``n_collisions``)
    and the first events (``collisions``: ``t, collider, victim, type, lane,
    pos_m``); docs/CONTRACTS.md says what one count is. This is the one
    reading the corridor battery artifact and the report both print
    (:data:`COLLISION_DEFINITION` is the artifact's statement of it).

    Args:
        metas: One parsed ``meta.json`` per run (:func:`load_meta`).
        labels: One label per run (the battery's seeds, the report's run
            names); default each meta's ``seed``, else its position.

    Returns:
        None when no run records ``n_collisions`` (not recorded is not
        zero); otherwise ``{n_runs, n_runs_recorded, runs_not_recorded,
        total, n_runs_with_collisions, runs_with_collisions: [{run, n}],
        per_run: {mean, lo95, hi95, n, underpowered}, rate: {per_vehicles,
        value, n_collisions, n_departed, n_runs}, n_logged, locations:
        [{lane, edge, n, pos_m_min, pos_m_max, runs}], definition}``.
        ``per_run`` is :func:`validation.metrics.ci` over the recorded
        counts; ``rate.value`` is ``per_vehicles · n_collisions /
        n_departed`` over the runs that record both counters (NaN when
        none departed); ``locations`` groups the logged events by lane, most
        collisions first, then by lane id; ``runs`` lists labels in
        first-seen order.

    Raises:
        ValueError: ``labels`` has a different length from ``metas``.
    """
    if labels is not None and len(labels) != len(metas):
        raise ValueError(f"{len(labels)} labels for {len(metas)} runs")
    names: list[str | int] = (
        list(labels) if labels is not None else [_run_label(m, i) for i, m in enumerate(metas)]
    )
    counts: list[int] = []
    not_recorded: list[str | int] = []
    with_collisions: list[dict[str, Any]] = []
    rate_collisions = rate_departed = rate_runs = 0
    n_logged = 0
    locations: dict[str, dict[str, Any]] = {}
    for name, meta in zip(names, metas, strict=True):
        n = collision_count(meta)
        if n is None:
            not_recorded.append(name)
            continue
        counts.append(n)
        if n > 0:
            with_collisions.append({"run": name, "n": n})
        departed = _count(meta, "n_vehicles_departed")
        if departed is not None:
            rate_collisions += n
            rate_departed += departed
            rate_runs += 1
        events = meta.get("collisions")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            n_logged += 1
            lane = str(event.get("lane") or "") or UNKNOWN_LANE
            row = locations.setdefault(
                lane,
                {
                    "lane": lane,
                    "edge": lane_edge(lane),
                    "n": 0,
                    "pos_m_min": None,
                    "pos_m_max": None,
                    "runs": [],
                },
            )
            row["n"] += 1
            pos = event.get("pos_m")
            if isinstance(pos, (int, float)) and not isinstance(pos, bool) and math.isfinite(pos):
                p = float(pos)
                row["pos_m_min"] = p if row["pos_m_min"] is None else min(row["pos_m_min"], p)
                row["pos_m_max"] = p if row["pos_m_max"] is None else max(row["pos_m_max"], p)
            if name not in row["runs"]:
                row["runs"].append(name)
    if not counts:
        return None
    interval = ci([float(c) for c in counts])
    return {
        "n_runs": len(metas),
        "n_runs_recorded": len(counts),
        "runs_not_recorded": not_recorded,
        "total": sum(counts),
        "n_runs_with_collisions": len(with_collisions),
        "runs_with_collisions": with_collisions,
        "per_run": {
            "mean": interval.mean,
            "lo95": interval.lo95,
            "hi95": interval.hi95,
            "n": interval.n,
            "underpowered": interval.underpowered,
        },
        "rate": {
            "per_vehicles": COLLISION_RATE_PER_VEHICLES,
            "value": (
                COLLISION_RATE_PER_VEHICLES * rate_collisions / rate_departed
                if rate_departed > 0
                else math.nan
            ),
            "n_collisions": rate_collisions,
            "n_departed": rate_departed,
            "n_runs": rate_runs,
        },
        "n_logged": n_logged,
        "locations": sorted(locations.values(), key=lambda r: (-r["n"], r["lane"])),
        "definition": COLLISION_DEFINITION,
    }


#: Run-level model counters :func:`forced_change_summary` reads: the
#: ``meta.json`` list, the model's name in text, and the counters whose sum is
#: the model's completed changes.
_FORCED_CHANGE_SOURCES: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("scripted_merges", "scripted merge", ("n_changed",)),
    ("weave_sections", "weave section", ("n_changed_in", "n_changed_out")),
)


def forced_change_summary(metas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Forced lane changes per scripted merge and weaving section, pooled.

    A scripted merge (``meta.json["scripted_merges"]``) or weaving section
    (``["weave_sections"]``) completes some of its changes under SUMO's forced
    lane-change mode, which refuses a change only on an overlap
    (docs/CONTRACTS.md, ``merge_params["force_guard"]``): the path by which a
    merge model's changes go around SUMO's own lane-change safety checks,
    and the one the I-94 WB battery's collisions came through (WP-93).

    Args:
        metas: One parsed ``meta.json`` per run.

    Returns:
        One row per model instance, keyed by its on-ramp, in first-seen
        order: ``{model, ramp, n_runs, n_forced, n_changed}`` with ``n_forced``
        = Σ ``n_forced`` and ``n_changed`` = Σ completed changes
        (``n_changed`` of a scripted merge, ``n_changed_in + n_changed_out``
        of a weave section) over the ``n_runs`` runs that record both. Empty
        when no run lists either model: it says nothing, it does not claim
        zero.
    """
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for meta in metas:
        for key, model, changed_keys in _FORCED_CHANGE_SOURCES:
            raw = meta.get(key)
            if not isinstance(raw, list):
                continue
            for position, entry in enumerate(raw):
                if not isinstance(entry, dict):
                    continue
                ramp = str(entry.get("ramp", "") or "") or f"{model} {position}"
                row = rows.setdefault(
                    (model, ramp),
                    {"model": model, "ramp": ramp, "n_runs": 0, "n_forced": 0, "n_changed": 0},
                )
                forced = _count(entry, "n_forced")
                changed = [_count(entry, k) for k in changed_keys]
                if forced is None or any(c is None for c in changed):
                    continue
                row["n_runs"] += 1
                row["n_forced"] += forced
                row["n_changed"] += sum(c for c in changed if c is not None)
    return list(rows.values())


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


def read_scoring_frame(run_dir: str | Path) -> pd.DataFrame:
    """One replicate's ``(t, veh_id, x, v)`` rows with ``veh_id`` as integer codes.

    The frame :func:`analyse_replicate` hands to its three measurements. The
    file is read in row batches straight into preallocated arrays, so the
    transient Arrow memory is one batch rather than the whole file (reading
    through ``pd.read_parquet`` left ≈ 100 B/row in Arrow's allocator pool,
    which numpy cannot reuse, before any measurement began). ``veh_id`` is
    dictionary-encoded per batch and the batch dictionaries are united and
    ordered by :func:`validation.metrics.vehicle_codes` — codes in the ids'
    sort order, so every sort and grouping over them is the one over the
    strings; the strings themselves are never held beside the numbers. Every
    function the frame reaches uses ``veh_id`` only as a grouping key, so the
    outputs equal those on the string frame — pinned by the battery's
    byte-identity test.

    Args:
        run_dir: Replicate directory.

    Returns:
        Frame with ``t`` [s], ``veh_id`` (``np.intp`` codes), ``x`` [m],
        ``v`` [m/s], in the file's row order.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    path = Path(run_dir) / "trajectories.parquet"
    with open(path, "rb") as f:
        reader = pq.ParquetFile(f)
        n = int(reader.metadata.num_rows)
        t = np.empty(n, dtype=np.float64)
        x = np.empty(n, dtype=np.float64)
        v = np.empty(n, dtype=np.float64)
        local = np.empty(n, dtype=np.int32)
        dictionaries: list[list[str]] = []
        bounds: list[tuple[int, int]] = []
        lo = 0
        for batch in reader.iter_batches(
            batch_size=SCORING_READ_BATCH_ROWS, columns=list(TRAJECTORY_COLUMNS)
        ):
            hi = lo + batch.num_rows
            t[lo:hi] = batch.column("t").to_numpy(zero_copy_only=False)
            x[lo:hi] = batch.column("x").to_numpy(zero_copy_only=False)
            v[lo:hi] = batch.column("v").to_numpy(zero_copy_only=False)
            encoded = pc.dictionary_encode(batch.column("veh_id"))
            if encoded.null_count:
                # A null index would be cast to an arbitrary code and silently
                # merged into some vehicle; the contract (§3) has no null ids.
                raise ValueError(f"{path} holds rows with a null veh_id")
            local[lo:hi] = encoded.indices.to_numpy(zero_copy_only=False)
            dictionaries.append(encoded.dictionary.to_pylist())
            bounds.append((lo, hi))
            lo = hi
    # Global codes in the ids' sort order: the batches' dictionaries are
    # united and ordered by the same factorization the string column would
    # get, and each batch's local indices are mapped through it.
    ids = sorted(set().union(*dictionaries)) if dictionaries else []
    ranks = vehicle_codes(pd.Series(ids, dtype="str"))
    rank = dict(zip(ids, ranks.tolist(), strict=True))
    codes = np.empty(n, dtype=np.intp)
    for (lo, hi), dictionary in zip(bounds, dictionaries, strict=True):
        mapping = np.asarray([rank[s] for s in dictionary], dtype=np.intp)
        codes[lo:hi] = mapping[local[lo:hi]]
    del local
    return pd.DataFrame({"t": t, "veh_id": codes, "x": x, "v": v}, copy=False)


def score_replicate(
    run_dir: str | Path,
    observed: ObservedCorridor,
    *,
    x_offset_m: float = 0.0,
    trajectories: pd.DataFrame | None = None,
) -> ObservedScores:
    """Score one completed replicate against a corridor's observations.

    Args:
        run_dir: Replicate directory (``meta.json`` + ``trajectories.parquet``).
        observed: The corridor's observations.
        x_offset_m: Simulation ``x`` of the observed origin [m]; see
            :func:`validation.observed.score_run_against_observed`.
        trajectories: The replicate's rows (:data:`TRAJECTORY_COLUMNS`) when
            the caller already holds them; ``None`` reads them.

    Returns:
        The replicate's :class:`validation.observed.ObservedScores`.
    """
    meta = load_meta(run_dir)
    warmup_s, duration_s = measurement_window(meta)
    if trajectories is None:
        trajectories = read_trajectories(run_dir)
    return score_run_against_observed(
        trajectories,
        observed,
        warmup_s=warmup_s,
        duration_s=duration_s,
        x_offset_m=x_offset_m,
    )


def replicate_wave_speed_kmh(
    run_dir: str | Path,
    detector: WaveDetector,
    *,
    trajectories: pd.DataFrame | None = None,
) -> float:
    """Backward wave-front speed [km/h] one detector reads on a replicate.

    The field is binned at the detector's own bins (a
    :class:`validation.waves.WaveDetector` refuses any other binning) over the
    replicate's measurement window, so the reading describes the scored
    period — exactly what :func:`validation.report.generate_report` measures
    for its wave-speed criterion row.

    Args:
        run_dir: Replicate directory.
        detector: Detector recipe, normally the criteria profile's.
        trajectories: The replicate's rows (``t``, ``x``, ``v`` at least)
            when the caller already holds them; ``None`` reads them.

    Returns:
        Mean backward-front speed magnitude [km/h]; NaN when the detector
        found no backward front.
    """
    meta = load_meta(run_dir)
    warmup_s, _ = measurement_window(meta)
    if trajectories is None:
        trajectories = read_trajectories(run_dir, columns=("t", "x", "v"))
    t = trajectories["t"].to_numpy(dtype=np.float64)
    rows: slice | np.ndarray = slice(0, t.size)
    if warmup_s > 0.0:
        windowed = time_window_rows(t, warmup_s)
        if n_window_rows(windowed) > 0:
            rows = windowed
    # The window's three columns, views when the rows are time-ordered.
    window = pd.DataFrame(
        {
            "t": t[rows],
            "x": trajectories["x"].to_numpy(dtype=np.float64)[rows],
            "v": trajectories["v"].to_numpy(dtype=np.float64)[rows],
        },
        copy=False,
    )
    del t, trajectories
    field = speed_field(window, dt_bin=detector.dt_bin_s, dx_bin=detector.dx_bin_m)
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
    without re-simulating. The trajectory is read once
    (:func:`read_scoring_frame`: ``veh_id`` factorized to integer codes, the
    strings dropped) and the one frame serves the three measurements
    (metrics, scores, wave speed), each of which works on views of it; one
    process per replicate is the unit of parallelism and its peak RSS is
    what :func:`score_pool_size` plans with.

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
    frame = read_scoring_frame(path)
    metrics = compute_metrics(path, x_ref=x_ref, span=span, trajectories=frame)
    scores = score_replicate(path, observed, x_offset_m=x_offset_m, trajectories=frame)
    wave_speed = replicate_wave_speed_kmh(path, profile.wave_detector, trajectories=frame)
    del frame
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
