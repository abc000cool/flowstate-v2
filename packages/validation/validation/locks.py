"""Permanent locks (gridlock) of a microscopic run: detection and run-set summary.

docs/I94_COLLAPSE_DIAGNOSIS.md (2026-10-07) found that 3 of the 20 replicates
of the I-94 WB step-3 battery ended in a permanent standstill at a weaving
section's exit gore — nothing passed the gore again and the queue behind it
grew towards the corridor's entry — and that no battery summary named one:
the insertion verdict pools the replicates, a late lock costs few points of
departed share, and a lock involves no collision. This module detects locks in
every run from files every replicate keeps, so that batteries and reports
count them (the ``no_locks`` criterion of :mod:`validation.criteria`).

**Definition.** A run *locks* when, somewhere on the corridor, vehicles stand
with (essentially) zero discharge past a point for at least
:data:`LOCK_MIN_DURATION_S`, with vehicles queued upstream of that point. A
queue that still discharges, however slowly, is congestion, not a lock; a
standstill that clears within the duration is a stop, not a lock.

**Readers.** Both read small per-run tables only, never
``trajectories.parquet``:

* *Space-time reader* — ``edges.parquet`` (Edie density and flow over every
  lane of the corridor in 15 s × 100 m bins, docs/CONTRACTS.md §3). A bin of
  a cell *stands* when the cell holds vehicles (density ≥
  :data:`STANDING_MIN_DENSITY_VEH_M`) and carries no flow (Edie flow ≤
  :data:`STANDING_MAX_FLOW_VEH_S`). A cell standing in every bin of an
  interval at least the lock duration long is *locked* over it. A locked
  interval is a *head* when the cell just downstream is not locked at its
  start (the standing queue does not continue past it); heads in one
  space-time component of locked cells and within
  :data:`HEAD_MERGE_DISTANCE_M` of each other are one lock, placed at the most
  downstream of them. Onset is the head's first standing bin and release its
  last (none when it stands to the run's end). This is the standstill map of
  the diagnosis reader ``diag_lock`` (mean speed on 100 m × 5 min bins of the
  trajectories, artifacts/mndot_rounds/weave_2026-09-24/) on the run's own
  Edie field, with "no flow" in place of "slow", so that a slow queue that
  still discharges is not a lock.
* *Run-end reader* — ``vehicles.parquet`` (one row per departed vehicle with
  its first and last corridor sample). The front-row method the diagnosis
  validated vehicle by vehicle on the step-3 battery (its Table 4 and §9
  "Front row" / "Onset"): among the vehicles still in the network at the
  run's end, one with no other within :data:`END_EMPTY_DOWNSTREAM_M` ahead and
  at least :data:`END_QUEUE_MIN_VEHICLES` within :data:`END_QUEUE_SPAN_M`
  behind is a candidate head. Every vehicle that entered the corridor more
  than :data:`CROSS_UPSTREAM_M` upstream of it and was last seen more than
  :data:`CROSS_DOWNSTREAM_M` downstream of it passed it no later than its own
  last sample, so the latest such sample bounds the last discharge from above
  (a vehicle still in the network counts at the run's end). The candidate is
  a lock when that bound lies at least the lock duration before the run's
  end. This reader sees only locks that persist to the run's end; its onset
  is an upper bound and its duration a lower bound (both include the
  crossing vehicles' travel from the head to where they were last seen, a
  few minutes on a corridor of a few kilometres).

A run is *locked* when either reader finds a lock. A run-end lock within
:data:`HEAD_MERGE_DISTANCE_M` of the head cell of a space-time lock that
persists to the run's end is the same lock: onset and duration from the
space-time reader, head position and trapped vehicles from the run-end
reader. A run with neither file is *not recorded* — never "no lock".

**Where.** Every lock is placed on the corridor axis (``x``) and, when one is
close, at a section of the run's own ``meta.json``: a weaving section, an
on-ramp merge or an off-ramp diverge whose gore (the ramp's
``attach_end_x_m``: the end of its auxiliary, acceleration or deceleration
lane) lies inside the head interval or at most :data:`SECTION_DISTANCE_M`
downstream of it, nearest first. The run records no general edge-to-``x``
table, so a head's edge is named only when the head lies on a ramp's attach
span.

**Trapped vehicles** of a lock that persists to the run's end: the vehicles
still in the network at the end, upstream of the head and bound for a
destination past it (their final destination's gore at or past the head, or
the corridor's end; needs ``vehicles.parquet``) — they cannot arrive while it
stands — and the vehicles of origins upstream of the head that never entered
the network (``meta.json`` planned minus departed, mainline and on-ramps
attached at or before the head; this includes any ordinary insertion backlog
of those origins).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy import ndimage
from scipy.stats import beta

from flowstate_core.units import veh_h_to_veh_s, veh_km_to_veh_m
from validation.vehicles import DESTINATION_CORRIDOR_END, vehicles_path

#: The run's space-time table (docs/CONTRACTS.md §3).
EDGES_FILE: Final[str] = "edges.parquet"

#: Names of the two readers, as a run's record lists them (``sources``,
#: ``locks[i].detected_by``).
SOURCE_EDGES: Final[str] = "edges"
SOURCE_VEHICLES: Final[str] = "vehicles"

#: Shortest standstill that counts as a lock [s]: 10 minutes, two consecutive
#: 5-minute detector windows (the observations' window, docs/FRISCO_PROTOCOL.md
#: §2; the diagnosis proposed one, §7) without discharge. It lies about an order of
#: magnitude from both kinds of standstill seen so far. Ordinary stops are
#: short: on the 22 unlocked I-24 corridor runs kept in ``runs/`` (read
#: 2026-10-07) no 15 s × 100 m cell met the standing test even once, and the
#: stranded entrant of the T.H.52 weave fixtures stood 0–62.5 s per run and
#: always cleared (docs/WEAVE_LOSS_DIAGNOSIS.md §3.8). Every lock seen lasted
#: to its run's end: about 22 min in the 35-minute netfix-probe slice, 14 to
#: 53 min in the step-3 battery (last discharge before the run's end,
#: docs/I94_COLLAPSE_DIAGNOSIS.md §5), 82–93 min of standing in the three I-24
#: sublane probes docs/I24_VALIDATION.md records as locking. The run-end
#: reader's duration is a lower bound (module docstring); the latest step-3
#: lock reads 13.6 min on it.
LOCK_MIN_DURATION_S: Final[float] = 600.0

#: Edie flow at or below which a cell carries no discharge [veh/s]: 18 veh/h,
#: one vehicle length (7.5 m) of progress summed over every vehicle of a
#: 15 s × 100 m bin. Over the lock duration that bounds what crosses a
#: standing cell by three vehicles' worth of traversal, under 1 % of what one
#: lane discharges (≈ 2,000 veh/h); a queue creeping out at one vehicle a
#: minute is above it and is not a lock.
STANDING_MAX_FLOW_VEH_S: Final[float] = veh_h_to_veh_s(18.0)

#: Density at or above which a cell holds a queue [veh/m]: 20 veh/km over
#: all lanes, two vehicles in a 100 m cell. A standing lane holds about 13.
STANDING_MIN_DENSITY_VEH_M: Final[float] = veh_km_to_veh_m(20.0)

#: Heads closer than this in one space-time component are one lock [m]. A
#: gore near a cell boundary leaves the last cell lightly occupied, so the
#: head can start one cell upstream and move down as that cell fills; three
#: cells, well under the ≈ 600 m between the closest gores on I-94 WB.
HEAD_MERGE_DISTANCE_M: Final[float] = 300.0

#: A section is the lock's when its gore lies inside the head interval or at
#: most this far downstream of it [m]. The diagnosis's lock fronts stood
#: 0.05–0.08 m short of the gore in the auxiliary lane; the space-time head is
#: a 100 m cell; 250 m covers both.
SECTION_DISTANCE_M: Final[float] = 250.0

#: Slack below the head interval for a section's gore [m]: the run-end head is
#: one vehicle's position, which may sit a hair past the gore it stands at.
SECTION_TOLERANCE_M: Final[float] = 1.0

#: Run-end reader: no vehicle of the network within this distance ahead of a
#: candidate head [m] (one space-time cell). The diagnosis found the road past
#: each locked gore empty up to the next entrance, 0.6 km and more.
END_EMPTY_DOWNSTREAM_M: Final[float] = 100.0

#: Run-end reader: the queue behind a candidate head is counted over this
#: span [m] ...
END_QUEUE_SPAN_M: Final[float] = 200.0

#: ... and must hold at least this many vehicles (0.05 veh/m over all lanes;
#: one standing lane holds about 27 in 200 m). The three step-3 locks held
#: 50–56.
END_QUEUE_MIN_VEHICLES: Final[int] = 10

#: Run-end reader: a crossing vehicle entered at least this far upstream of
#: the head [m] and was last seen at least :data:`CROSS_DOWNSTREAM_M` past it
#: (the diagnosis's onset recipe, §9).
CROSS_UPSTREAM_M: Final[float] = 50.0
CROSS_DOWNSTREAM_M: Final[float] = 10.0

#: A vehicle whose last sample is within this many output intervals of the
#: run's end is still in the network at the end.
END_SAMPLE_TOLERANCE: Final[int] = 2

#: Two-sided level of the locked-share interval.
SHARE_CI_LEVEL: Final[float] = 0.95

#: What a battery artifact's ``locks`` block and each run's record mean.
LOCK_DEFINITION: Final[str] = (
    "validation.locks (docs/CONTRACTS.md, 'Locks'). A run locks when vehicles stand with "
    "zero discharge past a point for at least params.min_duration_s while vehicles are "
    "queued upstream of it. Space-time reader (edges.parquet): a 15 s x 100 m cell stands "
    "when its Edie density is at least params.min_density_veh_m and its flow at most "
    "params.max_flow_veh_s; a cell standing for min_duration_s is locked; a lock's head is "
    "a locked cell whose downstream neighbour is not locked when it starts; onset_s is the "
    "head's first standing bin, release_s its last (null: to the run's end). Run-end reader "
    "(vehicles.parquet): a vehicle in the network at the end with none within "
    "params.end_empty_downstream_m ahead and at least params.end_queue_min_vehicles within "
    "params.end_queue_span_m behind, past which no vehicle from upstream was last seen "
    "within min_duration_s of the end; onset_s is then an upper bound and duration_s a lower "
    "bound. section: the weave, merge or diverge whose gore (ramp attach_end_x_m) lies in "
    "[x_lo_m - params.section_tolerance_m, x_hi_m + params.section_distance_m]. "
    "n_trapped_in_network: vehicles in the network at the end, upstream of the head and "
    "bound past it; "
    "n_never_departed_upstream: vehicles of origins at or upstream of the head that never "
    "entered the network (any ordinary backlog of those origins included). A run with "
    "neither file is not recorded (locked null), never unlocked. share_locked is the "
    "locked share of the recorded runs with its two-sided Clopper-Pearson interval."
)


@dataclass(frozen=True)
class LockParams:
    """The detector's thresholds (module docstring; defaults are the constants).

    Attributes:
        min_duration_s: :data:`LOCK_MIN_DURATION_S`.
        max_flow_veh_s: :data:`STANDING_MAX_FLOW_VEH_S`.
        min_density_veh_m: :data:`STANDING_MIN_DENSITY_VEH_M`.
        head_merge_distance_m: :data:`HEAD_MERGE_DISTANCE_M`.
        section_distance_m: :data:`SECTION_DISTANCE_M`.
        section_tolerance_m: :data:`SECTION_TOLERANCE_M`.
        end_empty_downstream_m: :data:`END_EMPTY_DOWNSTREAM_M`.
        end_queue_span_m: :data:`END_QUEUE_SPAN_M`.
        end_queue_min_vehicles: :data:`END_QUEUE_MIN_VEHICLES`.
        cross_upstream_m: :data:`CROSS_UPSTREAM_M`.
        cross_downstream_m: :data:`CROSS_DOWNSTREAM_M`.
    """

    min_duration_s: float = LOCK_MIN_DURATION_S
    max_flow_veh_s: float = STANDING_MAX_FLOW_VEH_S
    min_density_veh_m: float = STANDING_MIN_DENSITY_VEH_M
    head_merge_distance_m: float = HEAD_MERGE_DISTANCE_M
    section_distance_m: float = SECTION_DISTANCE_M
    section_tolerance_m: float = SECTION_TOLERANCE_M
    end_empty_downstream_m: float = END_EMPTY_DOWNSTREAM_M
    end_queue_span_m: float = END_QUEUE_SPAN_M
    end_queue_min_vehicles: int = END_QUEUE_MIN_VEHICLES
    cross_upstream_m: float = CROSS_UPSTREAM_M
    cross_downstream_m: float = CROSS_DOWNSTREAM_M

    def to_dict(self) -> dict[str, Any]:
        """JSON form (the battery artifact's ``locks.params``)."""
        return asdict(self)


#: The default thresholds.
DEFAULT_PARAMS: Final[LockParams] = LockParams()


@dataclass(frozen=True)
class LockSection:
    """A weave, merge or diverge of the run's corridor (``meta.json`` ramps).

    Attributes:
        name: The ramp's name (the on-ramp of a weave or merge, the off-ramp
            of a diverge).
        kind: ``"weave"`` (an on-ramp paired with an exit), ``"merge"`` (any
            other on-ramp) or ``"diverge"`` (an off-ramp not paired with a
            weave).
        model: The ramp's merge model as ``meta.json["merge_models"]`` names
            it (``"weave"``, ``"scripted"``, ``"measured"``, ...); None when
            not listed.
        exit: The paired exit of a weave; None otherwise.
        edge: The corridor edge the ramp attaches to.
        x_start_m: Start of the ramp's attach span on the corridor axis [m].
        x_end_m: Its gore: the end of the auxiliary, acceleration or
            deceleration lane [m].
    """

    name: str
    kind: str
    model: str | None
    exit: str | None
    edge: str | None
    x_start_m: float
    x_end_m: float

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> LockSection:
        """Inverse of :meth:`to_dict`."""
        return cls(
            name=str(raw["name"]),
            kind=str(raw["kind"]),
            model=None if raw.get("model") is None else str(raw["model"]),
            exit=None if raw.get("exit") is None else str(raw["exit"]),
            edge=None if raw.get("edge") is None else str(raw["edge"]),
            x_start_m=float(raw["x_start_m"]),
            x_end_m=float(raw["x_end_m"]),
        )


@dataclass(frozen=True)
class Lock:
    """One lock of a run.

    Attributes:
        x_m: The head [m, corridor axis]: the front vehicle's last position
            when the run-end reader places it, else the head cell's centre.
        x_lo_m: Upstream end of the head interval [m] (the head cell, or the
            front vehicle's position).
        x_hi_m: Downstream end of the head interval [m].
        edge: The corridor edge of a ramp attach span holding the head; None
            elsewhere (module docstring, "Where").
        section: The section the lock stands at; None when none is within
            reach.
        onset_s: When the head started standing [s, simulation time]: the
            space-time reader's first standing bin, or the run-end reader's
            upper bound on the last discharge.
        onset_is_upper_bound: True when only the run-end reader dates it.
        release_s: When the head stopped standing [s]; None when it stood to
            the run's end.
        duration_s: ``(release_s or the run's end) − onset_s`` [s].
        duration_is_lower_bound: True when the lock stood to the run's end
            (it may have lasted longer) or only the run-end reader dates it.
        queue_m: Longest extent of standing cells upstream of the head,
            head cell included [m]; None without the space-time reader.
        n_trapped_in_network: Vehicles still in the network at the run's
            end, upstream of the head and bound for a destination past it;
            None when the lock was released or no vehicle table exists.
        n_never_departed_upstream: Vehicles of origins at or upstream of the
            head that never entered the network; None when the lock was
            released or the run records no insertion counters.
        detected_by: The readers that found it (:data:`SOURCE_EDGES`,
            :data:`SOURCE_VEHICLES`).
    """

    x_m: float
    x_lo_m: float
    x_hi_m: float
    edge: str | None
    section: LockSection | None
    onset_s: float
    onset_is_upper_bound: bool
    release_s: float | None
    duration_s: float
    duration_is_lower_bound: bool
    queue_m: float | None
    n_trapped_in_network: int | None
    n_never_departed_upstream: int | None
    detected_by: tuple[str, ...]

    @property
    def persists_to_end(self) -> bool:
        """Whether the lock stood to the run's end (no release)."""
        return self.release_s is None

    def to_dict(self) -> dict[str, Any]:
        """JSON form (a run record's ``locks[i]``)."""
        return {
            "x_m": self.x_m,
            "x_lo_m": self.x_lo_m,
            "x_hi_m": self.x_hi_m,
            "edge": self.edge,
            "section": None if self.section is None else self.section.to_dict(),
            "onset_s": self.onset_s,
            "onset_is_upper_bound": self.onset_is_upper_bound,
            "release_s": self.release_s,
            "persists_to_end": self.persists_to_end,
            "duration_s": self.duration_s,
            "duration_is_lower_bound": self.duration_is_lower_bound,
            "queue_m": self.queue_m,
            "n_trapped_in_network": self.n_trapped_in_network,
            "n_never_departed_upstream": self.n_never_departed_upstream,
            "detected_by": list(self.detected_by),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Lock:
        """Inverse of :meth:`to_dict` (``persists_to_end`` is derived)."""
        section = raw.get("section")
        return cls(
            x_m=float(raw["x_m"]),
            x_lo_m=float(raw["x_lo_m"]),
            x_hi_m=float(raw["x_hi_m"]),
            edge=None if raw.get("edge") is None else str(raw["edge"]),
            section=None if not isinstance(section, Mapping) else LockSection.from_dict(section),
            onset_s=float(raw["onset_s"]),
            onset_is_upper_bound=bool(raw["onset_is_upper_bound"]),
            release_s=None if raw.get("release_s") is None else float(raw["release_s"]),
            duration_s=float(raw["duration_s"]),
            duration_is_lower_bound=bool(raw["duration_is_lower_bound"]),
            queue_m=None if raw.get("queue_m") is None else float(raw["queue_m"]),
            n_trapped_in_network=_opt_int(raw.get("n_trapped_in_network")),
            n_never_departed_upstream=_opt_int(raw.get("n_never_departed_upstream")),
            detected_by=tuple(str(s) for s in raw.get("detected_by") or ()),
        )


@dataclass(frozen=True)
class RunLocks:
    """What one run records about locks.

    Attributes:
        sources: The readers that had their file (:data:`SOURCE_EDGES`,
            :data:`SOURCE_VEHICLES`); empty when the run has neither, and the
            run is then not recorded.
        end_s: The run's end [s] the readers measured against; None when not
            recorded.
        locks: Every lock found, downstream first.
    """

    sources: tuple[str, ...]
    end_s: float | None
    locks: tuple[Lock, ...] = field(default_factory=tuple)

    @property
    def recorded(self) -> bool:
        """Whether either reader had its file."""
        return bool(self.sources)

    @property
    def locked(self) -> bool | None:
        """True / False when recorded; None (not recorded, never False) otherwise."""
        return bool(self.locks) if self.recorded else None

    def to_dict(self) -> dict[str, Any]:
        """JSON form (per-seed ``locks``, ``metrics.json`` ``locks``).

        A run not recorded reads ``locked`` and ``n_locks`` null, never false
        and zero.
        """
        return {
            "locked": self.locked,
            "sources": list(self.sources),
            "end_s": self.end_s,
            "n_locks": len(self.locks) if self.recorded else None,
            "locks": [lock.to_dict() for lock in self.locks],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RunLocks:
        """Inverse of :meth:`to_dict`."""
        end = raw.get("end_s")
        return cls(
            sources=tuple(str(s) for s in raw.get("sources") or ()),
            end_s=None if end is None else float(end),
            locks=tuple(Lock.from_dict(lock) for lock in raw.get("locks") or ()),
        )


#: A run without either file.
NOT_RECORDED: Final[RunLocks] = RunLocks(sources=(), end_s=None)


def _opt_int(value: object) -> int | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


# --- sections -----------------------------------------------------------------


def corridor_sections(meta: Mapping[str, Any]) -> list[LockSection]:
    """The weaves, merges and diverges of a run's corridor, by gore position.

    Read from ``meta.json``: ``ramps`` (name, kind, attach edge and span),
    ``merge_models`` (each ramp's model) and the weave pairings of
    ``weave_sections`` and of the ``measured_merges`` entries of kind
    ``"weave"``.

    Args:
        meta: Parsed ``meta.json``.

    Returns:
        One :class:`LockSection` per on-ramp (weave or merge) and per off-ramp
        not paired with a weave, sorted by gore; empty for a run without
        ramps.
    """
    ramps = meta.get("ramps")
    if not isinstance(ramps, list):
        return []
    models: dict[str, str] = {}
    raw_models = meta.get("merge_models")
    for entry in raw_models if isinstance(raw_models, list) else []:
        if isinstance(entry, dict) and entry.get("ramp") and entry.get("merge"):
            models[str(entry["ramp"])] = str(entry["merge"])
    weaves: dict[str, str | None] = {}
    for key in ("weave_sections", "measured_merges"):
        raw = meta.get(key)
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, dict) or not entry.get("ramp"):
                continue
            if key == "measured_merges" and entry.get("kind") != "weave":
                continue
            weaves[str(entry["ramp"])] = None if not entry.get("exit") else str(entry["exit"])
    paired = {name for name in weaves.values() if name}
    out: list[LockSection] = []
    for ramp in ramps:
        if not isinstance(ramp, dict):
            continue
        name = str(ramp.get("name", "") or "")
        x0, x1 = _finite(ramp.get("attach_x_m")), _finite(ramp.get("attach_end_x_m"))
        if not name or x0 is None or x1 is None:
            continue
        kind = ramp.get("kind")
        if kind == "on":
            section_kind = "weave" if name in weaves else "merge"
        elif kind == "off" and name not in paired:
            section_kind = "diverge"
        else:
            continue
        edge = ramp.get("attach_edge")
        out.append(
            LockSection(
                name=name,
                kind=section_kind,
                model=models.get(name),
                exit=weaves.get(name),
                edge=None if not edge else str(edge),
                x_start_m=x0,
                x_end_m=x1,
            )
        )
    return sorted(out, key=lambda s: (s.x_end_m, s.x_start_m, s.name))


def section_at(
    x_lo_m: float,
    x_hi_m: float,
    sections: Sequence[LockSection],
    params: LockParams = DEFAULT_PARAMS,
) -> LockSection | None:
    """The section a head interval stands at, if any (module docstring, "Where").

    Args:
        x_lo_m: Upstream end of the head interval [m].
        x_hi_m: Downstream end [m].
        sections: :func:`corridor_sections`.
        params: Thresholds (``section_distance_m``, ``section_tolerance_m``).

    Returns:
        The section whose gore lies in ``[x_lo_m − tolerance, x_hi_m +
        distance]`` nearest the head (lowest gore; a weave before a merge or
        diverge sharing its gore), or None.
    """
    lo = x_lo_m - params.section_tolerance_m
    hi = x_hi_m + params.section_distance_m
    reach = [s for s in sections if lo <= s.x_end_m <= hi]
    if not reach:
        return None
    rank = {"weave": 0, "merge": 1, "diverge": 2}
    return min(reach, key=lambda s: (s.x_end_m, rank.get(s.kind, 3), s.name))


def head_edge(
    x_m: float, x_lo_m: float, x_hi_m: float, sections: Sequence[LockSection]
) -> str | None:
    """The corridor edge of a ramp attach span holding the head, if any.

    The span containing ``x_m`` first, else the first (by start) overlapping
    ``[x_lo_m, x_hi_m]``.
    """
    spans = sorted(
        (s for s in sections if s.edge is not None), key=lambda s: (s.x_start_m, s.x_end_m)
    )
    for s in spans:
        if s.x_start_m <= x_m <= s.x_end_m:
            return s.edge
    for s in spans:
        if s.x_start_m <= x_hi_m and x_lo_m <= s.x_end_m:
            return s.edge
    return None


def never_departed_upstream(meta: Mapping[str, Any], x_m: float) -> int | None:
    """Vehicles of origins at or upstream of ``x_m`` that never entered the network.

    The mainline origin (``x`` = 0) and every on-ramp attached at or before
    ``x_m``: planned minus departed from ``meta.json`` (the run's
    ``n_vehicles_planned`` / ``n_vehicles_departed``, the ramps' ``n_planned``
    / ``n_departed``; the mainline's are the run's minus the on-ramps').

    Args:
        meta: Parsed ``meta.json``.
        x_m: The head [m].

    Returns:
        The count, or None when the run records no insertion counters.
    """
    planned, departed = (
        _opt_int(meta.get("n_vehicles_planned")),
        _opt_int(meta.get("n_vehicles_departed")),
    )
    if planned is None or departed is None:
        return None
    ramps = meta.get("ramps")
    upstream = 0
    ramp_planned = ramp_departed = 0
    for ramp in ramps if isinstance(ramps, list) else []:
        if not isinstance(ramp, dict) or ramp.get("kind") != "on":
            continue
        r_planned = _opt_int(ramp.get("n_planned")) or 0
        r_departed = _opt_int(ramp.get("n_departed")) or 0
        ramp_planned += r_planned
        ramp_departed += r_departed
        x0 = _finite(ramp.get("attach_x_m"))
        if x0 is not None and x0 <= x_m:
            upstream += r_planned - r_departed
    mainline = (planned - ramp_planned) - (departed - ramp_departed)
    return max(0, mainline) + upstream


# --- the space-time reader ------------------------------------------------------


def _bounds(centres: NDArray[np.float64]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Bin bounds from the centres of a contiguous grid whose last bin may be partial.

    ``edges.parquet`` emits each bin's centre, the trailing partial bin's at
    the middle of its actual extent (docs/CONTRACTS.md §3), so each bin's
    upper bound is ``2·centre − lower`` and the next bin starts there.
    """
    n = centres.size
    lo = np.empty(n)
    hi = np.empty(n)
    if n == 0:
        return lo, hi
    width = float(centres[1] - centres[0]) if n > 1 else 2.0 * float(centres[0])
    lower = float(centres[0]) - width / 2.0
    for k in range(n):
        lo[k] = lower
        hi[k] = 2.0 * float(centres[k]) - lower
        lower = float(hi[k])
    return lo, hi


@dataclass(frozen=True)
class _Field:
    t_lo: NDArray[np.float64]
    t_hi: NDArray[np.float64]
    x_lo: NDArray[np.float64]
    x_hi: NDArray[np.float64]
    standing: NDArray[np.bool_]


def _standing_field(edges: pd.DataFrame, params: LockParams) -> _Field:
    """The standing mask ``[time bin, cell]`` of an edges table."""
    t = edges["t_bin"].to_numpy(dtype=np.float64)
    x = edges["x_bin"].to_numpy(dtype=np.float64)
    ts = np.unique(t)
    xs = np.unique(x)
    it = np.searchsorted(ts, t)
    ix = np.searchsorted(xs, x)
    density = np.zeros((ts.size, xs.size))
    flow = np.zeros((ts.size, xs.size))
    density[it, ix] = np.nan_to_num(edges["density"].to_numpy(dtype=np.float64), nan=0.0)
    flow[it, ix] = np.nan_to_num(edges["flow"].to_numpy(dtype=np.float64), nan=np.inf)
    t_lo, t_hi = _bounds(ts)
    x_lo, x_hi = _bounds(xs)
    standing = (density >= params.min_density_veh_m) & (flow <= params.max_flow_veh_s)
    return _Field(t_lo=t_lo, t_hi=t_hi, x_lo=x_lo, x_hi=x_hi, standing=standing)


@dataclass
class _Head:
    """One space-time lock while it is assembled."""

    cell: int
    k_start: int
    k_end: int
    component: int


def _runs(column: NDArray[np.bool_]) -> list[tuple[int, int]]:
    """``(first, last)`` index of every run of True in a 1-D mask."""
    padded = np.concatenate(([False], column, [False])).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return [(int(a), int(b) - 1) for a, b in zip(edges[::2], edges[1::2], strict=True)]


def _edges_locks(
    edges: pd.DataFrame, params: LockParams
) -> tuple[list[_Head], _Field, NDArray[np.bool_]]:
    """The space-time reader's heads, its field and its locked mask."""
    fld = _standing_field(edges, params)
    nt, nx = fld.standing.shape
    locked = np.zeros((nt, nx), dtype=bool)
    intervals: list[tuple[int, int, int]] = []
    for i in range(nx):
        for k0, k1 in _runs(fld.standing[:, i]):
            if float(fld.t_hi[k1] - fld.t_lo[k0]) >= params.min_duration_s - 1e-9:
                locked[k0 : k1 + 1, i] = True
                intervals.append((i, k0, k1))
    if not intervals:
        return [], fld, locked
    labels, _ = ndimage.label(locked)
    heads: list[_Head] = []
    for i, k0, k1 in intervals:
        if i + 1 < nx and locked[k0, i + 1]:
            continue  # the standing queue continues past this cell when it starts
        heads.append(_Head(cell=i, k_start=k0, k_end=k1, component=int(labels[k0, i])))
    # one lock per cluster of heads: same component, within the merge distance
    heads.sort(key=lambda h: (-h.cell, h.k_start))
    kept: list[_Head] = []
    for head in heads:
        x_head = float(fld.x_hi[head.cell])
        for other in kept:
            if (
                other.component == head.component
                and float(fld.x_hi[other.cell]) - x_head <= params.head_merge_distance_m
            ):
                other.k_start = min(other.k_start, head.k_start)
                other.k_end = max(other.k_end, head.k_end)
                break
        else:
            kept.append(_Head(head.cell, head.k_start, head.k_end, head.component))
    return kept, fld, locked


def _queue_m(head: _Head, fld: _Field, locked: NDArray[np.bool_]) -> float:
    """Longest extent of locked cells from the head upstream over the head's life [m]."""
    best = 0.0
    for k in range(head.k_start, head.k_end + 1):
        if not locked[k, head.cell]:
            continue
        j = head.cell
        while j - 1 >= 0 and locked[k, j - 1]:
            j -= 1
        best = max(best, float(fld.x_hi[head.cell] - fld.x_lo[j]))
    return best


# --- the run-end reader -----------------------------------------------------------


@dataclass(frozen=True)
class _EndLock:
    x_m: float
    last_discharge_s: float


def _in_network_at_end(vehicles: pd.DataFrame, end_s: float, sample_dt_s: float) -> pd.Series:
    """Mask of the vehicles still on the corridor at the run's end."""
    last_t = pd.to_numeric(vehicles["last_t_s"], errors="coerce")
    last_x = pd.to_numeric(vehicles["last_x_m"], errors="coerce")
    arrived = vehicles["arrived"].fillna(False).astype(bool)
    mask: pd.Series = (
        (~arrived)
        & (last_t >= end_s - END_SAMPLE_TOLERANCE * sample_dt_s - 1e-9)
        & np.isfinite(last_x)
    )
    return mask


def _ramp_positions(meta: Mapping[str, Any]) -> tuple[dict[int, float], dict[str, float]]:
    """Each ramp's attach start by index, and each ramp's gore by name (``meta.json`` ramps)."""
    starts: dict[int, float] = {}
    gores: dict[str, float] = {}
    ramps = meta.get("ramps")
    for position, ramp in enumerate(ramps if isinstance(ramps, list) else []):
        if not isinstance(ramp, dict):
            continue
        index = _opt_int(ramp.get("index"))
        x0, x1 = _finite(ramp.get("attach_x_m")), _finite(ramp.get("attach_end_x_m"))
        if x0 is not None:
            starts[position if index is None else index] = x0
        if x1 is not None and ramp.get("name"):
            gores[str(ramp["name"])] = x1
    return starts, gores


def trapped_in_network(
    vehicles: pd.DataFrame,
    meta: Mapping[str, Any],
    x_lo_m: float,
    x_hi_m: float,
    params: LockParams = DEFAULT_PARAMS,
) -> int | None:
    """Vehicles in the network at the end, upstream of a head and bound past it.

    A vehicle counts when it has not arrived, its last corridor position is at
    or before the head interval's downstream end (or it has none and its
    origin ramp attaches there or before: still on its on-ramp), and its
    final destination (``destination_final``, else ``destination``) is the
    corridor's end or a ramp whose gore lies at or past the head interval's
    upstream end — it cannot arrive while the lock stands.

    Args:
        vehicles: ``vehicles.parquet`` rows (``arrived``, ``last_x_m``,
            ``origin_ramp`` and a destination column at least).
        meta: Parsed ``meta.json`` (ramp positions).
        x_lo_m: Upstream end of the head interval [m].
        x_hi_m: Downstream end [m].
        params: Thresholds (``section_tolerance_m``).

    Returns:
        The count; None when the table carries no destination column.
    """
    dest_col = next(
        (c for c in ("destination_final", "destination") if c in vehicles.columns), None
    )
    if dest_col is None or "origin_ramp" not in vehicles.columns:
        return None
    starts, gores = _ramp_positions(meta)
    tol = params.section_tolerance_m
    dest_x = (
        vehicles[dest_col]
        .astype("string")
        .map(
            lambda name: math.inf if name == DESTINATION_CORRIDOR_END else gores.get(name, math.nan)
        )
        .astype("float64")
        .to_numpy(dtype=np.float64)
    )
    origin = pd.to_numeric(vehicles["origin_ramp"], errors="coerce").to_numpy(dtype=np.float64)
    origin_x = np.array(
        [
            0.0 if o < 0 else starts.get(int(o), math.nan) if math.isfinite(o) else math.nan
            for o in origin
        ]
    )
    last_x = pd.to_numeric(vehicles["last_x_m"], errors="coerce").to_numpy(dtype=np.float64)
    arrived = vehicles["arrived"].fillna(False).astype(bool).to_numpy()
    on_corridor = np.isfinite(last_x)
    upstream = np.where(on_corridor, last_x <= x_hi_m + tol, origin_x <= x_hi_m + tol)
    past = dest_x >= x_lo_m - tol
    return int(np.count_nonzero(~arrived & upstream & past))


def _vehicle_locks(
    vehicles: pd.DataFrame, end_s: float, sample_dt_s: float, params: LockParams
) -> list[_EndLock]:
    """The run-end reader's locks, downstream first."""
    at_end = _in_network_at_end(vehicles, end_s, sample_dt_s)
    xs = np.sort(pd.to_numeric(vehicles.loc[at_end, "last_x_m"]).to_numpy(dtype=np.float64))
    if xs.size == 0:
        return []
    entry = pd.to_numeric(vehicles["entry_x_m"], errors="coerce").to_numpy(dtype=np.float64)
    last_x = pd.to_numeric(vehicles["last_x_m"], errors="coerce").to_numpy(dtype=np.float64)
    last_t = pd.to_numeric(vehicles["last_t_s"], errors="coerce").to_numpy(dtype=np.float64)
    gaps = np.diff(np.append(xs, np.inf))
    found: list[_EndLock] = []
    for k in np.flatnonzero(gaps > params.end_empty_downstream_m)[::-1]:
        x_head = float(xs[k])
        n_queue = int(np.count_nonzero((xs >= x_head - params.end_queue_span_m) & (xs <= x_head)))
        if n_queue < params.end_queue_min_vehicles:
            continue
        crossed = (entry < x_head - params.cross_upstream_m) & (
            last_x > x_head + params.cross_downstream_m
        )
        times = last_t[crossed & np.isfinite(last_t)]
        if times.size == 0:
            continue  # nothing ever passed it: no discharge to measure
        last_discharge = float(times.max())
        if end_s - last_discharge < params.min_duration_s - 1e-9:
            continue
        if any(abs(x_head - f.x_m) <= params.end_queue_span_m for f in found):
            continue  # one queue, its most downstream head already kept
        found.append(_EndLock(x_m=x_head, last_discharge_s=last_discharge))
    return found


# --- one run ---------------------------------------------------------------------


def _end_of_run(meta: Mapping[str, Any]) -> float | None:
    config = meta.get("config")
    sim = config.get("sim") if isinstance(config, dict) else None
    return _finite(sim.get("duration_s")) if isinstance(sim, dict) else None


def _sample_dt(meta: Mapping[str, Any]) -> float:
    """The realized output interval [s] (``output_hz_realized``, else the configured rate)."""
    hz = _finite(meta.get("output_hz_realized"))
    if hz is None:
        config = meta.get("config")
        sim = config.get("sim") if isinstance(config, dict) else None
        hz = _finite(sim.get("output_hz")) if isinstance(sim, dict) else None
    return 1.0 / hz if hz is not None and hz > 0 else 1.0


def detect_locks(
    meta: Mapping[str, Any],
    *,
    edges: pd.DataFrame | None = None,
    vehicles: pd.DataFrame | None = None,
    params: LockParams = DEFAULT_PARAMS,
) -> RunLocks:
    """Locks of one run from its tables (module docstring).

    Args:
        meta: Parsed ``meta.json`` (``config.sim.duration_s`` is the run's
            end; ramps, merge models and weave pairings place the locks;
            insertion counters count the never-departed).
        edges: The run's ``edges.parquet`` (``t_bin``, ``x_bin``,
            ``density`` [veh/m], ``flow`` [veh/s]); None without one.
        vehicles: The run's ``vehicles.parquet`` (``entry_x_m``,
            ``last_t_s``, ``last_x_m``, ``arrived`` at least); None without
            one.
        params: Thresholds.

    Returns:
        The run's :class:`RunLocks`; not recorded when both tables are None.
    """
    sources = tuple(
        name
        for name, table in ((SOURCE_EDGES, edges), (SOURCE_VEHICLES, vehicles))
        if table is not None
    )
    if not sources:
        return NOT_RECORDED
    end_s = _end_of_run(meta)
    if end_s is None:
        candidates: list[float] = []
        if edges is not None and len(edges):
            _, t_hi = _bounds(np.unique(edges["t_bin"].to_numpy(dtype=np.float64)))
            candidates.append(float(t_hi[-1]))
        if vehicles is not None and len(vehicles):
            candidates.append(float(pd.to_numeric(vehicles["last_t_s"]).max()))
        end_s = max(candidates) if candidates else 0.0
    sections = corridor_sections(meta)

    space_time: list[tuple[_Head, _Field, NDArray[np.bool_]]] = []
    if edges is not None and len(edges):
        heads, fld, locked = _edges_locks(edges, params)
        space_time = [(h, fld, locked) for h in heads]
    run_end: list[_EndLock] = []
    if vehicles is not None and len(vehicles):
        run_end = _vehicle_locks(vehicles, end_s, _sample_dt(meta), params)

    locks: list[Lock] = []
    matched: set[int] = set()
    for head, fld, locked in space_time:
        x_lo, x_hi = float(fld.x_lo[head.cell]), float(fld.x_hi[head.cell])
        x_m = 0.5 * (x_lo + x_hi)
        onset = float(fld.t_lo[head.k_start])
        stop = float(fld.t_hi[head.k_end])
        release = None if stop >= end_s - 1e-6 else stop
        n_trapped: int | None = None
        detected = [SOURCE_EDGES]
        if release is None:
            same = next(
                (
                    j
                    for j, end_lock in enumerate(run_end)
                    if j not in matched
                    and x_lo - params.head_merge_distance_m
                    <= end_lock.x_m
                    <= x_hi + params.head_merge_distance_m
                ),
                None,
            )
            if same is not None:
                # the run-end reader places the same lock's front vehicle
                matched.add(same)
                x_m = x_lo = x_hi = run_end[same].x_m
                detected.append(SOURCE_VEHICLES)
            if vehicles is not None:
                n_trapped = trapped_in_network(vehicles, meta, x_lo, x_hi, params)
        locks.append(
            Lock(
                x_m=x_m,
                x_lo_m=x_lo,
                x_hi_m=x_hi,
                edge=head_edge(x_m, x_lo, x_hi, sections),
                section=section_at(x_lo, x_hi, sections, params),
                onset_s=onset,
                onset_is_upper_bound=False,
                release_s=release,
                duration_s=(end_s if release is None else release) - onset,
                duration_is_lower_bound=release is None,
                queue_m=_queue_m(head, fld, locked),
                n_trapped_in_network=n_trapped,
                n_never_departed_upstream=(
                    None if release is not None else never_departed_upstream(meta, x_m)
                ),
                detected_by=tuple(detected),
            )
        )
    for j, end_lock in enumerate(run_end):
        if j in matched:
            continue
        x = end_lock.x_m
        locks.append(
            Lock(
                x_m=x,
                x_lo_m=x,
                x_hi_m=x,
                edge=head_edge(x, x, x, sections),
                section=section_at(x, x, sections, params),
                onset_s=end_lock.last_discharge_s,
                onset_is_upper_bound=True,
                release_s=None,
                duration_s=end_s - end_lock.last_discharge_s,
                duration_is_lower_bound=True,
                queue_m=None,
                n_trapped_in_network=trapped_in_network(vehicles, meta, x, x, params)
                if vehicles is not None
                else None,
                n_never_departed_upstream=never_departed_upstream(meta, x),
                detected_by=(SOURCE_VEHICLES,),
            )
        )
    locks.sort(key=lambda lock: (-lock.x_m, lock.onset_s))
    return RunLocks(sources=sources, end_s=end_s, locks=tuple(locks))


def read_edges(run_dir: str | Path) -> pd.DataFrame | None:
    """A run's ``edges.parquet`` (the four columns the reader needs), or None.

    Read through an open file object, as ``validation.vehicles.read_vehicles``
    does (a bare path makes pyarrow build a filesystem, which fails once
    libsumo's libarrow is loaded).
    """
    import pyarrow.parquet as pq

    path = Path(run_dir) / EDGES_FILE
    if not path.is_file():
        return None
    with open(path, "rb") as f:
        table = pq.ParquetFile(f).read(columns=["t_bin", "x_bin", "density", "flow"])
    frame: pd.DataFrame = table.to_pandas()
    return frame


#: ``vehicles.parquet`` columns the run-end reader needs; without any of them
#: the table is not read.
VEHICLE_COLUMNS: Final[tuple[str, ...]] = ("entry_x_m", "last_t_s", "last_x_m", "arrived")

#: Further columns the trapped count reads when the table has them.
TRAPPED_COLUMNS: Final[tuple[str, ...]] = ("origin_ramp", "destination", "destination_final")


def read_vehicle_rows(run_dir: str | Path) -> pd.DataFrame | None:
    """The columns of a run's ``vehicles.parquet`` the readers use, or None.

    :data:`VEHICLE_COLUMNS` and whichever of :data:`TRAPPED_COLUMNS` the file
    has; None when the file is absent or lacks one of the former. Read through
    an open file object (:func:`read_edges`).
    """
    import pyarrow.parquet as pq

    path = vehicles_path(run_dir)
    if not path.is_file():
        return None
    with open(path, "rb") as f:
        parquet = pq.ParquetFile(f)
        names = set(parquet.schema_arrow.names)
        if not set(VEHICLE_COLUMNS) <= names:
            return None
        wanted = [*VEHICLE_COLUMNS, *(c for c in TRAPPED_COLUMNS if c in names)]
        table = parquet.read(columns=wanted)
    frame: pd.DataFrame = table.to_pandas()
    return frame


def detect_run_locks(
    run_dir: str | Path,
    *,
    meta: Mapping[str, Any] | None = None,
    params: LockParams = DEFAULT_PARAMS,
) -> RunLocks:
    """Locks of one replicate directory (:func:`detect_locks` on its files).

    Reads ``meta.json`` (unless given; an absent one reads as empty: the run's
    end then comes from the tables and no section is named), ``edges.parquet``
    and ``vehicles.parquet`` when present; never the trajectories.

    Args:
        run_dir: Replicate directory.
        meta: Its parsed ``meta.json`` when the caller already holds it.
        params: Thresholds.

    Returns:
        The run's :class:`RunLocks` (not recorded when neither table exists).
    """
    path = Path(run_dir)
    if meta is None:
        meta_path = path / "meta.json"
        raw = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        meta = raw if isinstance(raw, dict) else {}
    return detect_locks(
        meta, edges=read_edges(path), vehicles=read_vehicle_rows(path), params=params
    )


# --- a run set --------------------------------------------------------------------


def lock_flags(records: Sequence[RunLocks | None]) -> list[bool | None]:
    """Each run's :attr:`RunLocks.locked` (None: not recorded), in order.

    The input of the ``no_locks`` acceptance criterion
    (``validation.criteria.evaluate(..., lock_flags=...)``).
    """
    return [None if r is None else r.locked for r in records]


def clopper_pearson(k: int, n: int, level: float = SHARE_CI_LEVEL) -> tuple[float, float]:
    """Two-sided exact binomial interval for ``k`` of ``n`` (NaN, NaN for n = 0)."""
    if n <= 0:
        return math.nan, math.nan
    alpha = 1.0 - level
    lo = 0.0 if k <= 0 else float(beta.ppf(alpha / 2.0, k, n - k + 1))
    hi = 1.0 if k >= n else float(beta.ppf(1.0 - alpha / 2.0, k + 1, n - k))
    return lo, hi


def _section_label(lock: Lock) -> str:
    """How a lock's place is named in summaries: its section, else ``x ≈ …``."""
    if lock.section is not None:
        return lock.section.name
    return f"x {lock.x_m:.0f} m"


def lock_summary(
    records: Sequence[RunLocks | None],
    *,
    labels: Sequence[str | int] | None = None,
    params: LockParams = DEFAULT_PARAMS,
) -> dict[str, Any] | None:
    """Locks of a run set, pooled over its runs (the battery's ``locks`` block).

    Args:
        records: One :class:`RunLocks` per run (None: not recorded).
        labels: One label per run (the battery's seeds, the report's run
            names); default the positions.
        params: The thresholds the records were made with (written out).

    Returns:
        None when no run is recorded (not recorded is not zero); otherwise
        ``{n_runs, n_runs_recorded, runs_not_recorded, n_runs_locked,
        share_locked: {value, lo95, hi95, method}, runs_locked: [{run,
        n_locks, locks: [{section, kind, x_m, onset_s, onset_is_upper_bound,
        duration_s, persists_to_end, n_trapped_in_network,
        n_never_departed_upstream}]}], by_section: [{section, kind, x_end_m,
        n_runs, runs, onset_s_min, onset_s_max}], sources: {edges, vehicles},
        params, definition}``. ``by_section`` groups locks by the section they
        stand at (a lock at no section by its rounded ``x``), most runs first.

    Raises:
        ValueError: ``labels`` has a different length from ``records``.
    """
    if labels is not None and len(labels) != len(records):
        raise ValueError(f"{len(labels)} labels for {len(records)} runs")
    names: list[str | int] = list(labels) if labels is not None else list(range(len(records)))
    recorded = [(n, r) for n, r in zip(names, records, strict=True) if r is not None and r.recorded]
    if not recorded:
        return None
    not_recorded = [n for n, r in zip(names, records, strict=True) if r is None or not r.recorded]
    locked = [(n, r) for n, r in recorded if r.locks]
    lo, hi = clopper_pearson(len(locked), len(recorded))
    by_section: dict[str, dict[str, Any]] = {}
    for name, record in locked:
        for lock in record.locks:
            key = _section_label(lock)
            row = by_section.setdefault(
                key,
                {
                    "section": key,
                    "kind": None if lock.section is None else lock.section.kind,
                    "x_end_m": None if lock.section is None else lock.section.x_end_m,
                    "n_runs": 0,
                    "runs": [],
                    "onset_s_min": lock.onset_s,
                    "onset_s_max": lock.onset_s,
                },
            )
            if name not in row["runs"]:
                row["runs"].append(name)
                row["n_runs"] += 1
            row["onset_s_min"] = min(row["onset_s_min"], lock.onset_s)
            row["onset_s_max"] = max(row["onset_s_max"], lock.onset_s)
    return {
        "n_runs": len(records),
        "n_runs_recorded": len(recorded),
        "runs_not_recorded": not_recorded,
        "n_runs_locked": len(locked),
        "share_locked": {
            "value": len(locked) / len(recorded),
            "lo95": lo,
            "hi95": hi,
            "method": "Clopper-Pearson exact binomial, two-sided 95 %",
        },
        "runs_locked": [
            {
                "run": name,
                "n_locks": len(record.locks),
                "locks": [
                    {
                        "section": _section_label(lock),
                        "kind": None if lock.section is None else lock.section.kind,
                        "x_m": lock.x_m,
                        "onset_s": lock.onset_s,
                        "onset_is_upper_bound": lock.onset_is_upper_bound,
                        "duration_s": lock.duration_s,
                        "persists_to_end": lock.persists_to_end,
                        "n_trapped_in_network": lock.n_trapped_in_network,
                        "n_never_departed_upstream": lock.n_never_departed_upstream,
                    }
                    for lock in record.locks
                ],
            }
            for name, record in locked
        ],
        "by_section": sorted(
            by_section.values(), key=lambda r: (-r["n_runs"], r["onset_s_min"], r["section"])
        ),
        "sources": {
            SOURCE_EDGES: sum(1 for _, r in recorded if SOURCE_EDGES in r.sources),
            SOURCE_VEHICLES: sum(1 for _, r in recorded if SOURCE_VEHICLES in r.sources),
        },
        "params": params.to_dict(),
        "definition": LOCK_DEFINITION,
    }
