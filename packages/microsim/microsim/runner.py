"""Micro-tier run orchestration: ``ScenarioConfig`` → SUMO → run artifacts.

Implements CLAUDE.md §3.3: libsumo in-process stepping (TraCI fallback for
``sumo-gui`` debugging), per-vehicle state capture via **subscriptions** (not
XML post-processing), pure-function controller dispatch for compliant AVs with
SUMO safety checks left ON, once-per-run compliance draws, seeded
perturbations, HBEFA4 fuel accounting, and Parquet/JSON artifacts per
docs/CONTRACTS.md §3:

```
runs/<config_hash>/<seed>/
  trajectories.parquet   # t, veh_id, x, lane, v, a, is_av, complied
                         # (+ x_unwrapped on ring networks, for wave tracking)
  edges.parquet          # 15 s × 100 m Edie bins: mean_speed, density, flow
  vehicles.parquet       # one row per departed vehicle: route, origin,
                         # destination, departure, first/last corridor
                         # sample, arrival, weave give-up (VEHICLES_FILE)
  journeys.parquet       # one row per PLANNED vehicle: planned/actual
                         # departure, arrival, meter hold, route free-flow
                         # time (JOURNEYS_FILE, WP-105)
  meta.json              # config snapshot + hash, versions, tier="micro",
                         # seeded flag, wall time, per-vehicle fuel, AV ids
```

``meta.json`` is the run's **completion marker**: it is written last (and
atomically), and :func:`run_micro` deletes any stale copy before it starts, so
a directory without one holds the debris of an interrupted run — a
footer-less ``trajectories.parquet``, at worst — and must never be consumed.
:func:`is_run_complete` / :func:`require_complete_run` are the predicate every
reader (and :func:`run_replicates`) uses to say so with a clear message.

Fuel unit note (verified against SUMO 1.27): ``vehicle.getFuelConsumption``
returns **mg/s** under the default HBEFA4 emission model (observed magnitude
≈ 500 mg/s for a passenger car crawling at 2.5 m/s, consistent with ~2.5 l/h).
Totals are accumulated as ``rate · step_length`` [mg] and converted to ml via
the HBEFA4 gasoline density 0.74 kg/l (``FUEL_DENSITY_GASOLINE_KG_PER_L``) —
a physical property, not a unit conversion, hence defined here rather than in
``flowstate_core.units``.

libsumo limitation: libsumo is a **per-process singleton** — one SUMO
simulation per Python process, sequential ``start``/``close`` cycles only.
:func:`run_replicates` therefore parallelizes across *processes*
(``multiprocessing`` spawn context, one SUMO per worker, imports inside the
child), which is also the CLAUDE.md §3.4 performance path.
"""

from __future__ import annotations

import dataclasses
import json
import math
import multiprocessing
import os
import platform
import time
from collections import deque
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import sumolib

from controllers.registry import (
    default_params,
    get_segment_controller,
    get_vehicle_controller,
    reads_downstream,
)
from controllers.vsl import VSL_SEGMENT_TARGET_M, effective_limit
from flowstate_core.config import (
    BOUNDARY_LIMIT_FACTOR_DEFAULT,
    CONFIG_HASH_VERSION,
    SCRIPTED_MERGE_DEFAULTS,
    WEAVE_DEFAULTS,
    CorridorNetwork,
    FleetSpec,
    OSMNetwork,
    RampSpec,
    RingNetwork,
    ScenarioConfig,
    config_hash,
)
from flowstate_core.constants import SPEED_FACTOR_DEFAULT
from flowstate_core.controller_types import (
    ControllerObs,
    Memory,
    RampMeterObs,
    SegmentControllerFn,
    SegmentObs,
    VehicleControllerFn,
)
from flowstate_core.rng import make_rng, spawn_seeds, sumo_seed
from microsim import merge_model
from microsim.networks import (
    RAMP_SPLIT_OFF,
    RAMP_SPLIT_ON,
    NetBundle,
    WeaveSection,
    accel_lane_end,
    corridor,
    expand_ramp_splits,
    lane_end_patch_file,
    merge_patch_files,
    osm_import,
    patch_net,
    ring,
    weave_sections,
)
from microsim.paths import effective_roots, ensure_within_roots
from microsim.vehicles import (
    FleetPlan,
    build_corridor_plan,
    build_ring_plan,
    load_idm_calibration,
    merge_gap_stream,
    ramp_routes,
    sublane_vtype_attrs,
    write_corridor_routes,
    write_ring_routes,
)

#: Gasoline density used to convert HBEFA4 fuel mass to volume
#: (HBEFA4 petrol reference density; see module docstring).
FUEL_DENSITY_GASOLINE_KG_PER_L: Final[float] = 0.74

#: Rolling window for the controller reference speed U [s]
#: (Stern et al. 2018 use the recent average platoon speed, 30–60 s window).
V_REF_WINDOW_S: Final[float] = 45.0

#: Leader lookahead for ``vehicle.getLeader`` [m].
LEADER_LOOKAHEAD_M: Final[float] = 250.0

#: Downstream observation bin width [m] (docs/CONTRACTS.md §1 default).
DOWNSTREAM_BIN_M: Final[float] = 100.0

#: Downstream observation horizon [m] (JAD lookahead default, CLAUDE.md §4.3).
DOWNSTREAM_HORIZON_M: Final[float] = 2000.0

#: VSL dispatch cadence [s] (CLAUDE.md §4.4 gantry update interval). The
#: macro tier mirrors this value (``macrosim.runner.VSL_INTERVAL_S``).
VSL_INTERVAL_S: Final[float] = 30.0

#: Edie aggregation bins for edges.parquet (task spec: 15 s × 100 m).
EDGES_DT_BIN_S: Final[float] = 15.0
EDGES_DX_BIN_M: Final[float] = 100.0

#: Corridor insertion-buffer (entry edge) length [m]. With
#: ``departPos="free"`` a 2 km entry sustains ~1960 veh/h of single-lane
#: demand (measured on SUMO 1.27.1); a short fixed-point entry collapses to
#: ~1200 veh/h under oversaturation. Capped at the corridor's own length so
#: short scenarios stay small.
CORRIDOR_INSERTION_BUFFER_M: Final[float] = 2000.0

_MG_PER_G: Final[float] = 1000.0
_G_PER_ML_FACTOR: Final[float] = FUEL_DENSITY_GASOLINE_KG_PER_L  # kg/l == g/ml


def fuel_mg_to_ml(fuel_mg: float) -> float:
    """Convert an HBEFA4 fuel mass [mg] to volume [ml] (gasoline, 0.74 kg/l)."""
    return fuel_mg / _MG_PER_G / _G_PER_ML_FACTOR


@dataclass(frozen=True)
class RunPaths:
    """Artifact locations for one completed replicate."""

    run_dir: Path
    trajectories: Path
    edges: Path
    meta: Path


#: Completion marker of a replicate directory (docs/CONTRACTS.md §3). Written
#: last and atomically by :func:`run_micro`; its absence means the run was
#: interrupted and whatever else the directory holds is debris.
COMPLETION_MARKER: Final[str] = "meta.json"


def is_run_complete(run_dir: str | Path) -> bool:
    """Whether a replicate directory holds a *completed* run.

    ``trajectories.parquet`` is opened at the start of the run and only gains
    its Parquet footer when the run finishes, so its mere presence proves
    nothing: an interrupted replicate (SIGINT, OOM kill, a VM shutdown cap)
    leaves an unreadable file behind. :data:`COMPLETION_MARKER` is written
    last, so it — and only it — answers this question.

    Args:
        run_dir: Replicate directory (``runs/<config_hash>/<seed>/``).

    Returns:
        ``True`` when the run finished and its artifacts are readable.
    """
    return (Path(run_dir) / COMPLETION_MARKER).is_file()


def require_complete_run(run_dir: str | Path) -> Path:
    """Return ``run_dir`` if the replicate completed, else raise.

    Use in place of a bare ``trajectories.parquet`` existence check when
    deciding whether a seed still needs simulating (resumable sweeps) or may
    be analysed.

    Args:
        run_dir: Replicate directory (``runs/<config_hash>/<seed>/``).

    Returns:
        The directory as a :class:`~pathlib.Path`.

    Raises:
        FileNotFoundError: The directory holds no completion marker — it was
            never run, or the run was interrupted. The message names the
            debris to delete.
    """
    path = Path(run_dir)
    if is_run_complete(path):
        return path
    leftovers = sorted(p.name for p in path.glob("*.parquet")) if path.is_dir() else []
    detail = (
        f" (interrupted run: {', '.join(leftovers)} may be truncated and unreadable; "
        f"delete {path} and re-run the seed)"
        if leftovers
        else " (no artifacts: the seed has not been run)"
    )
    raise FileNotFoundError(f"incomplete micro replicate {path}: no {COMPLETION_MARKER}{detail}")


def _apply_merge_models(
    net: OSMNetwork, bundle: NetBundle, workdir: Path, keep: tuple[str, ...]
) -> NetBundle:
    """Apply the on-ramp merge models to a freshly imported OSM network.

    The merge models (``RampSpec.merge``, docs/CONTRACTS.md §2) all need the
    acceleration lane — lane 0 of the attach edge — to dead-end at that edge's
    end: ``zipper`` patches the lane drop there, and the
    runner's ``scripted`` merge drives the vehicles standing on it. Whether
    netconvert's ramp guessing leaves it that way depends on the attach edge's
    length: for an edge longer than ``--ramps.ramp-length`` the lane lives on a
    split ``-AddedOnRampEdge`` piece and dead-ends by construction, while on a
    shorter edge the guessed lane covers the whole edge and spills into the
    following corridor edges, dead-ending there instead (docs/ONBOARDING_MNDOT.md
    §6 — every short entrance of the MnDOT corridor).

    That spill is deleted here: :func:`~microsim.networks.accel_lane_end`
    follows lane 0 downstream, and when it is a spill (it dead-ends within
    :data:`~microsim.networks.ACCEL_LANE_TAIL_MAX_M` and feeds nothing but the
    next corridor edge's lane 0) a ``<delete>`` connection patch terminates it
    at the attach edge's end, shifting nothing else — lanes ``1..n`` keep the
    connections netconvert guessed. The patch names compiled edge ids, so it
    goes through :func:`~microsim.networks.patch_net` (a netconvert pass over
    the built network) rather than an OSM re-import, which reads connection
    files before ramp guessing has created those ids. The dead-end check then
    runs on the patched network.

    Refused, with the geometry in the message: an attach edge that carries no
    added lane at all, and a lane 0 that is not a taper — one feeding an exit
    (a weaving section's auxiliary lane) or running on as a through lane. Those
    entrances are not acceleration-lane merges and belong on ``lane_change``
    — or on ``weave`` when the lane feeds the paired exit: a weave ramp skips
    both the termination and the patches (lane 0 must stay connected to the
    exit) and only has its pairing validated (:func:`_check_weave_pairs`).

    Args:
        net: The scenario's OSM network block, ramps already resolved to their
            compiled attach pieces.
        bundle: The imported network.
        workdir: Run working directory (patches land in ``workdir/patches``).
        keep: Ramp edge ids pinned through pruning, as on the first import.

    Returns:
        The bundle to simulate: unchanged when no merge model asks for a patch,
        else the patched one, carrying ``patch_files`` and ``terminated_lanes``.

    Raises:
        ValueError: A merge model cannot be applied to the geometry (message
            names the ramp, the lane counts and the reason).
        RuntimeError: The termination patch did not take (netconvert kept the
            connection).
    """
    merge_ramps = [
        r
        for r in net.ramps
        if r.kind == "on" and r.merge not in ("lane_change", "weave") and not _is_section_ramp(r)
    ]
    has_weave = any(_is_section_ramp(r) for r in net.ramps)
    if not merge_ramps and not has_weave:
        return bundle
    compiled = sumolib.net.readNet(str(bundle.net_path))
    chain = expand_ramp_splits(list(net.corridor_edges), bundle.edge_ids)
    if has_weave:
        _check_weave_pairs(net, compiled, chain)
    if not merge_ramps:
        return bundle
    patch_dir = workdir / "patches"

    def _index(ramp: RampSpec) -> int:
        i = chain.index(ramp.attach_edge)
        if i + 1 >= len(chain):
            raise ValueError(
                f"ramp {ramp.name or ramp.attach_edge}: merge model {ramp.merge!r} needs a "
                "corridor edge after the attach edge"
            )
        return i

    # (1) Terminate a guessed acceleration lane that spills past the attach edge.
    term_patches: list[Path] = []
    terminated: list[str] = []
    for ramp in merge_ramps:
        i = _index(ramp)
        attach = compiled.getEdge(ramp.attach_edge)
        outgoing = attach.getLanes()[0].getOutgoing()
        if not outgoing or ramp.attach_edge in terminated:
            continue  # dead-ends already, or a second ramp on the same attach edge
        label = ramp.name or ramp.attach_edge
        prev_id = chain[i - 1] if i else None
        prev_lanes = compiled.getEdge(prev_id).getLaneNumber() if prev_id is not None else None
        nxt_lanes = compiled.getEdge(chain[i + 1]).getLaneNumber()
        geometry = (
            f"{ramp.attach_edge} has {attach.getLaneNumber()} lanes, the corridor edge before it "
            f"({prev_id}) {prev_lanes}, the one after it ({chain[i + 1]}) {nxt_lanes}"
        )
        if prev_lanes is not None and attach.getLaneNumber() <= prev_lanes:
            raise ValueError(
                f"ramp {label}: merge model {ramp.merge!r} needs an acceleration lane on the "
                f"attach edge and this merge added none — {geometry}. Put --ramps.guess in "
                "network.netconvert_extra, or use merge: 'lane_change' on this ramp."
            )
        verdict = accel_lane_end(compiled, chain, ramp.attach_edge)
        if not verdict.ok:
            hint = (
                " (or, if the lane feeds an exit, give the ramp a weave block naming it)"
                if ramp.merge == "measured"
                else ""
            )
            raise ValueError(
                f"ramp {label}: merge model {ramp.merge!r} needs the acceleration lane (lane 0 of "
                f"the attach edge) to dead-end at the edge's end, and it cannot be terminated "
                f"there: {verdict.reason} — {geometry}. Use merge: 'lane_change' on this "
                f"ramp{hint}."
            )
        term_patches.append(
            lane_end_patch_file(
                patch_dir,
                ramp.attach_edge,
                chain[i + 1],
                sorted({c.getToLane().getIndex() for c in outgoing}),
            )
        )
        terminated.append(ramp.attach_edge)
    if term_patches:
        bundle = patch_net(
            bundle, term_patches, internal_links=net.internal_links, stem="osm_accel_end"
        )
        bundle = dataclasses.replace(bundle, terminated_lanes=tuple(terminated))
        compiled = sumolib.net.readNet(str(bundle.net_path))

    # (2) The merge-model patches themselves, on a lane 0 that now dead-ends.
    model_patches: list[Path] = []
    for ramp in merge_ramps:
        i = _index(ramp)
        attach = compiled.getEdge(ramp.attach_edge)
        if attach.getLanes()[0].getOutgoing():
            raise RuntimeError(
                f"ramp {ramp.name or ramp.attach_edge}: merge model {ramp.merge!r} needs the "
                "acceleration lane (lane 0 of the attach edge) to dead-end at the edge's end; the "
                f"termination patch did not take (lane 0 of {ramp.attach_edge} still connects)"
            )
        if ramp.merge in ("scripted", "measured"):
            continue  # no patch: the runner drives the acceleration lane
        model_patches += merge_patch_files(
            patch_dir,
            ramp.attach_edge,
            chain[i + 1],
            attach.getToNode().getID(),
            attach.getLaneNumber(),
            compiled.getEdge(chain[i + 1]).getLaneNumber(),
            ramp.merge,
            visibility_m=ramp.merge_visibility_m,
        )
    if not model_patches:
        return bundle
    if term_patches:
        # the termination lives in the compiled network, so an OSM re-import
        # would build it away: patch the built network again instead
        return patch_net(
            bundle, model_patches, internal_links=net.internal_links, stem="osm_merge_models"
        )
    bundle = osm_import(
        osm_file=net.osm_file,
        bbox=net.bbox,
        corridor_edges=tuple(net.corridor_edges),
        workdir=workdir,
        keep_edges=keep,
        patch_files=[*_user_patch_files(net), *model_patches],
        internal_links=net.internal_links,
        netconvert_extra=tuple(net.netconvert_extra),
    )
    return dataclasses.replace(bundle, patch_files=tuple(str(p) for p in model_patches))


def _is_section_ramp(ramp: RampSpec) -> bool:
    """An on-ramp that opens a weaving section: ``merge="weave"``, or ``"measured"`` with a weave block.

    The measured merge model (2026-10-05, docs/MERGE_MODEL.md) runs on the
    weave's geometry where the ramp names its paired exit and on the
    acceleration lane (terminated as the scripted merge's) where it does not.
    """
    return ramp.kind == "on" and (
        ramp.merge == "weave" or (ramp.merge == "measured" and ramp.weave is not None)
    )


def _check_weave_pairs(net: OSMNetwork, compiled: Any, chain: Sequence[str]) -> list[WeaveSection]:
    """The weaving section of every ``merge="weave"`` on-ramp, validated.

    Also of every ``merge="measured"`` on-ramp with a weave block (2026-10-05):
    the measured model's weaving sections are paired and validated alike.

    The schema already requires the paired off-ramp (``WeaveSpec.exit_ramp``)
    to name the same attach edge; here the compiled network must agree: lane
    0 of the on-ramp's attach edge has to reach that off-ramp's first edge
    along the lane-0 walk of :func:`~microsim.networks.weave_sections`.

    Args:
        net: The OSM network block, ramps resolved to their compiled pieces.
        compiled: The compiled network (``sumolib.net.readNet``).
        chain: Corridor edge ids in driving order, ramp splits expanded.

    Returns:
        One :class:`~microsim.networks.WeaveSection` per weave ramp, in ramp
        order.

    Raises:
        ValueError: A weave ramp's lane 0 does not reach its paired exit
            (the message names the edges and where lane 0 goes instead).
    """
    ramps = list(net.ramps)
    found = {s.on_ramp: s for s in weave_sections(compiled, chain, ramps)}
    out: list[WeaveSection] = []
    for k, ramp in enumerate(ramps):
        if not _is_section_ramp(ramp) or ramp.weave is None:
            continue
        label = ramp.name or ramp.attach_edge
        j = next(
            i for i, r in enumerate(ramps) if r.kind == "off" and r.name == ramp.weave.exit_ramp
        )
        off = ramps[j]
        section = found.get(k)
        if section is None or section.off_ramp != j:
            lane0 = sorted(
                (c.getTo().getID(), c.getToLane().getIndex())
                for c in compiled.getEdge(ramp.attach_edge).getLanes()[0].getOutgoing()
            )
            reached = (
                f"lane 0 reaches the exit {ramps[section.off_ramp].name!r} "
                f"({section.exit_edge}) instead"
                if section is not None
                else "lane 0 reaches no exit"
            )
            raise ValueError(
                f"ramp {label}: merge model {ramp.merge!r} pairs it with the exit {off.name!r} "
                f"(leaving {off.attach_edge} for {off.edges[0]}), but lane 0 of "
                f"{ramp.attach_edge} does not carry the entering traffic there: it connects to "
                f"{lane0} and {reached}. Use merge: 'lane_change' on this ramp, or name the "
                "exit its auxiliary lane feeds."
            )
        out.append(section)
    return out


def _user_patch_files(net: OSMNetwork) -> list[Path]:
    """The scenario's own netconvert patches (``OSMNetwork.patch_files``).

    Each path is resolved against the working directory and must lie inside
    the allowed data roots (the same rule as ``osm_file``), so a scenario
    uploaded through the API cannot make netconvert read an arbitrary file.

    Raises:
        ValueError: a patch outside the allowed roots or missing.
    """
    out: list[Path] = []
    for raw in net.patch_files:
        path = Path(raw)
        ensure_within_roots(raw, path.resolve(), effective_roots(None), field="patch_files")
        if not path.is_file():
            raise ValueError(f"patch_files entry not found: {raw}")
        out.append(path)
    return out


def _build_network(cfg: ScenarioConfig, workdir: Path) -> NetBundle:
    """Build the SUMO network for the scenario's network block."""
    net = cfg.network
    if isinstance(net, RingNetwork):
        return ring(net.circumference_m, workdir=workdir)
    if isinstance(net, CorridorNetwork):
        entry_m = min(CORRIDOR_INSERTION_BUFFER_M, net.length_m)
        exit_m = net.boundary.exit_buffer_m if net.boundary is not None else 0.0
        return corridor(
            net.length_m, lanes=net.lanes, workdir=workdir, entry_m=entry_m, exit_m=exit_m
        )
    if isinstance(net, OSMNetwork):
        keep = tuple(e for r in net.ramps for e in r.edges)
        bundle = osm_import(
            osm_file=net.osm_file,
            bbox=net.bbox,
            corridor_edges=tuple(net.corridor_edges),
            workdir=workdir,
            keep_edges=keep,
            patch_files=_user_patch_files(net),
            internal_links=net.internal_links,
            netconvert_extra=tuple(net.netconvert_extra),
        )
        # netconvert's ramp guessing (OSMNetwork.netconvert_extra) splits the
        # highway edge at a merge into the acceleration-lane piece
        # ``<id>-AddedOnRampEdge`` + ``<id>`` (and ``<id>`` + ``<id>-AddedOffRampEdge``
        # before an exit). The scenario names the load-time id; the compiled
        # ramp joins the piece, so every downstream use (routes, checks,
        # meters, meta) sees the piece as the attach edge.
        compiled_ids = set(bundle.edge_ids)
        resolved_ramps = []
        for ramp in net.ramps:
            piece = ramp.attach_edge + (RAMP_SPLIT_ON if ramp.kind == "on" else RAMP_SPLIT_OFF)
            resolved_ramps.append(
                ramp.model_copy(update={"attach_edge": piece}) if piece in compiled_ids else ramp
            )
        if any(r is not o for r, o in zip(resolved_ramps, net.ramps, strict=True)):
            net = net.model_copy(update={"ramps": resolved_ramps})
        bundle = _apply_merge_models(net, bundle, workdir, keep)
        if net.boundary is not None:
            # docs/CONTRACTS.md §2: on an OSM corridor the LAST corridor edge
            # plays the exit-buffer role and hosts the boundary schedule.
            bundle = dataclasses.replace(bundle, exit_edge=bundle.edge_ids[-1])
        return bundle
    raise TypeError(f"unsupported network type: {type(net).__name__}")


def _resolve_ramp_pieces(cfg: ScenarioConfig, bundle: NetBundle) -> ScenarioConfig:
    """Point every ramp at the compiled piece of its attach edge.

    netconvert's ramp guessing (``OSMNetwork.netconvert_extra``) splits the
    highway edge at a merge into ``<id>-AddedOnRampEdge`` + ``<id>`` and the
    edge before an exit into ``<id>`` + ``<id>-AddedOffRampEdge``. The
    scenario names the load-time id; the compiled ramp joins the piece, so
    routes, checks and meters must see the piece. The config hash and the
    ``meta.json`` snapshot keep the scenario as written.
    """
    net = cfg.network
    if not isinstance(net, OSMNetwork) or not net.ramps:
        return cfg
    compiled = set(bundle.edge_ids)
    resolved = []
    changed = False
    for ramp in net.ramps:
        piece = ramp.attach_edge + (RAMP_SPLIT_ON if ramp.kind == "on" else RAMP_SPLIT_OFF)
        if piece in compiled:
            resolved.append(ramp.model_copy(update={"attach_edge": piece}))
            changed = True
        else:
            resolved.append(ramp)
    if not changed:
        return cfg
    return cfg.model_copy(update={"network": net.model_copy(update={"ramps": resolved})})


def _build_plan_and_routes(
    cfg: ScenarioConfig,
    bundle: NetBundle,
    rng: np.random.Generator,
    routes_path: Path,
    depart_edge_spread: int = 1,
) -> FleetPlan:
    """Draw the fleet plan and write the route file for any network kind."""
    net = cfg.network
    if isinstance(net, RingNetwork):
        plan = build_ring_plan(net, cfg.fleet, cfg.av, rng)
        write_ring_routes(
            bundle.edge_ids,
            bundle.offsets,
            net.circumference_m,
            plan,
            cfg.fleet.model,
            cfg.sim.action_step_s,
            cfg.sim.duration_s,
            routes_path,
            heavy=cfg.fleet.heavy,
            jm_timegap_minor_s=cfg.fleet.jm_timegap_minor_s,
            jm_ignore_foe_prob=cfg.fleet.jm_ignore_foe_prob,
            extra_attrs=sublane_vtype_attrs(cfg.fleet),
        )
        return plan
    if isinstance(net, CorridorNetwork):
        inflow = list(net.inflow)
    else:
        assert isinstance(net, OSMNetwork)
        inflow = list(net.inflow)
        if not inflow:
            raise ValueError("OSM scenario needs a non-empty network.inflow for demand")
    routes: dict[str, tuple[str, ...]] | None = None
    if isinstance(net, CorridorNetwork):
        lanes = net.lanes
        plan = build_corridor_plan(
            inflow,
            cfg.sim.duration_s,
            cfg.fleet,
            cfg.av,
            rng,
            entry_lane_shares=net.entry_lane_shares,
        )
    else:
        # OSM: insert round-robin over the entry edge's real lane count (the
        # M3 multi-lane scheme), read from the compiled net.
        compiled = sumolib.net.readNet(str(bundle.net_path))
        lanes = int(compiled.getEdge(bundle.edge_ids[0]).getLaneNumber())
        if net.ramps:
            _check_ramp_connectivity(compiled, net.ramps)
            routes = ramp_routes(bundle.edge_ids, net.ramps)
        plan = build_corridor_plan(
            inflow,
            cfg.sim.duration_s,
            cfg.fleet,
            cfg.av,
            rng,
            ramps=net.ramps,
            corridor_edges=bundle.edge_ids,
            entry_lane_shares=net.entry_lane_shares,
        )
    write_corridor_routes(
        bundle.edge_ids,
        plan,
        cfg.fleet.model,
        cfg.sim.action_step_s,
        routes_path,
        depart_edge_spread=depart_edge_spread,
        lanes=lanes,
        routes=routes,
        lc_strategic=cfg.fleet.lc_strategic,
        lc_keep_right=cfg.fleet.lc_keep_right,
        lc_cooperative=cfg.fleet.lc_cooperative,
        lc_assertive=cfg.fleet.lc_assertive,
        lc_speed_gain=cfg.fleet.lc_speed_gain,
        lc_strategic_ramp=cfg.fleet.lc_strategic_ramp,
        heavy=cfg.fleet.heavy,
        jm_timegap_minor_s=cfg.fleet.jm_timegap_minor_s,
        jm_ignore_foe_prob=cfg.fleet.jm_ignore_foe_prob,
        extra_attrs=sublane_vtype_attrs(cfg.fleet),
    )
    return plan


def _edge_speed_limits(bundle: NetBundle, edge_ids: Sequence[str]) -> dict[str, float]:
    """Base speed limit [m/s] of each edge, read from the compiled net.

    The limit is the fastest lane's ``speed`` attribute in the ``.net.xml``
    (generated networks: :data:`microsim.networks.EDGE_SPEED_LIMIT_MS` on
    every lane; OSM imports: the statutory limit netconvert derived from the
    ``maxspeed`` tags / highway type). It is the ``base_ms`` argument of
    :func:`controllers.vsl.effective_limit`.

    Args:
        bundle: The compiled network.
        edge_ids: Edges to look up.

    Returns:
        ``{edge_id: limit_ms}``.
    """
    compiled = sumolib.net.readNet(str(bundle.net_path))
    return {
        eid: float(max(lane.getSpeed() for lane in compiled.getEdge(eid).getLanes()))
        for eid in edge_ids
    }


def _check_ramp_connectivity(net: Any, ramps: Sequence[RampSpec]) -> None:
    """Verify every ramp's edges chain and join its ``attach_edge`` in the net.

    Raises:
        ValueError: A ramp edge is missing or two consecutive edges (or the
            ramp and its corridor edge) are not connected, which SUMO would
            otherwise only report as a silent route failure.
    """
    for ramp in ramps:
        seq = (
            [*ramp.edges, ramp.attach_edge]
            if ramp.kind == "on"
            else [ramp.attach_edge, *ramp.edges]
        )
        for a, b in pairwise(seq):
            try:
                ea = net.getEdge(a)
                net.getEdge(b)
            except KeyError as exc:
                raise ValueError(
                    f"ramp {ramp.name or ramp.kind}: edge {exc} not in network"
                ) from exc
            if b not in {e.getID() for e in ea.getOutgoing()}:
                raise ValueError(
                    f"ramp {ramp.name or ramp.kind}: edge {a!r} does not connect to {b!r}"
                )


class _TrafficLib:
    """Thin holder selecting libsumo (default) or TraCI (gui/debug)."""

    def __init__(self, use_traci: bool, gui: bool) -> None:
        if gui and not use_traci:
            # libsumo cannot drive sumo-gui; fall back per CLAUDE.md §3.3.
            use_traci = True
        if use_traci:
            import traci as mod
        else:
            import libsumo as mod
        self.mod = mod
        self.use_traci = use_traci
        self.gui = gui
        self.binary = "sumo-gui" if gui else "sumo"


#: TraCI refuses a stop the vehicle cannot reach with its deceleration; the
#: message carries this phrase (SUMO MSVehicle::addStop).
_TOO_CLOSE_TO_BRAKE: Final[str] = "too close to brake"


def _meter_distance_to_stop_m(ms_r: dict[str, Any], edge: str, lane_pos_m: float) -> float:
    """Distance along the ramp from a vehicle to its meter's stop line [m].

    Args:
        ms_r: Meter state (``ramp_edges``, ``edge_len_m``, ``stop_pos_m``;
            the stop line lies on ``ramp_edges[-1]``).
        edge: The ramp edge the vehicle is on.
        lane_pos_m: The vehicle's position on that edge [m].

    Returns:
        Remaining distance; negative once the vehicle is past the line.
        Internal junction lanes between ramp edges are not counted, so the
        value errs short (the conservative side for a braking check).
    """
    edges: list[str] = ms_r["ramp_edges"]
    k = edges.index(edge)
    if k == len(edges) - 1:
        return float(ms_r["stop_pos_m"]) - lane_pos_m
    lengths: dict[str, float] = ms_r["edge_len_m"]
    between = sum(lengths[e] for e in edges[k + 1 : -1])
    return lengths[edge] - lane_pos_m + between + float(ms_r["stop_pos_m"])


def _meter_assign_stop(mod: Any, ms_r: dict[str, Any], vid: str, edge: str, step_s: float) -> bool:
    """Give a ramp vehicle the meter's stop if it can still brake for it.

    The braking distance is ``v² / (2 b) + v · Δt`` with ``b`` the vehicle's
    comfortable deceleration (``vehicle.getDecel``) and one step of reaction
    margin. A vehicle already inside it is not stopped: it passes the meter
    this cycle (the caller counts it in ``n_passed_unstoppable``). TraCI's
    own refusal ("too close to brake", discrete-time brake gap) is handled
    the same way; any other TraCI error propagates.

    Args:
        mod: The libsumo or traci module.
        ms_r: Meter state (see :func:`_meter_distance_to_stop_m`).
        vid: Vehicle id.
        edge: The ramp edge the vehicle is on this step.
        step_s: Simulation step length [s].

    Returns:
        ``True`` if the stop was set, ``False`` if the vehicle passes.
    """
    dist = _meter_distance_to_stop_m(ms_r, edge, float(mod.vehicle.getLanePosition(vid)))
    v = float(mod.vehicle.getSpeed(vid))
    b = max(float(mod.vehicle.getDecel(vid)), 1e-6)
    if dist <= v * v / (2.0 * b) + v * step_s:
        return False
    try:
        mod.vehicle.setStop(vid, ms_r["edge"], ms_r["stop_pos_m"], 0, 1.0e9)
    except mod.TraCIException as exc:
        if _TOO_CLOSE_TO_BRAKE in str(exc):
            return False
        raise
    return True


# SUMO laneChangeMode bit patterns (TraCI docs, "lane change mode"): every
# model-driven change off; bits 8-9 decide how a TraCI request treats others.
COLLISION_LOG_MAX = 50  # collision events kept verbatim in meta.json (the count is exact)
LC_MODE_SCRIPTED_SAFE = 512  # respect the speed / brake gaps of others, adapt speed
LC_MODE_SCRIPTED_FORCE = 256  # avoid immediate collisions only (the follower yields)
LC_MODE_SCRIPTED_SAFE_NO_ADAPT = 768  # respect the gaps of others, no speed adaptation
#: The model-driven change bits of ``laneChangeMode`` (bits 0-7: strategic,
#: cooperative, speed gain, keep right; TraCI docs, "lane change mode"). With
#: them cleared SUMO's own model changes no lane, and a TraCI request keeps
#: the treatment of bits 8-9 (WP-92, the one-step veto of an opposing entry,
#: the measured model's :func:`merge_model.resolve_opposing` and
#: :func:`_weave_opposing_restore`; probed on SUMO 1.27.1). A bit mask of the
#: API, not a fitted value.
LC_MODE_MODEL_BITS: Final[int] = 0xFF
# the vacate rule's bound (_weave_vacate_step): a through vehicle is asked into
# the target lane only within that lane's spare capacity over the last minute
VACATE_LANE_CAPACITY_VEH_H = 2050.0  # one IDM lane at the fleet defaults (CLAUDE.md §3.1)
VACATE_FLOW_WINDOW_S = 60.0  # the window of the target lane's flow and of the asks
SCRIPTED_MERGE_CREEP_MS = 3.0  # desired-speed floor on the acceleration lane [m/s]
HALTING_SPEED_MS = 0.1  # SUMO's own halting threshold (waiting time accrues below it) [m/s]
NEIGHBOR_LEFT_FOLLOWERS = 0  # vehicle.getNeighbors mode bits: bit0 right, bit1 leaders
NEIGHBOR_LEFT_LEADERS = 2
NEIGHBOR_RIGHT_FOLLOWERS = 1  # weaving sections: the exiting movement looks right
NEIGHBOR_RIGHT_LEADERS = 3


def _neighbor_gap(mod: Any, vid: str, mode: int) -> tuple[float, float, str | None]:
    """Smallest gap [m], that neighbour's speed and id on the adjacent lane.

    ``vehicle.getNeighbors`` returns ``(id, gap)`` pairs (gap negative when the
    vehicles overlap longitudinally). Returns ``(inf, nan, None)`` with no
    neighbour.
    """
    best_gap, best_v, best_id = math.inf, math.nan, None
    for nid, gap in mod.vehicle.getNeighbors(vid, mode):
        if gap < best_gap:
            best_gap, best_v, best_id = float(gap), float(mod.vehicle.getSpeed(nid)), str(nid)
    return best_gap, best_v, best_id


def _scripted_force_gap_ok(
    v_ego: float,
    g_lead: float,
    v_lead: float,
    g_foll: float,
    v_foll: float,
    b_ego: float,
    b_foll: float | None,
    step_s: float,
) -> bool:
    """Brake-gap guard of a scripted merge's forced change (``force_guard``, WP-93).

    Mode 256 refuses a change only when the target-lane follower's front is
    inside its own ``minGap`` behind the changer, or the changer's inside
    its own behind the leader (SUMO 1.27.1, ``MSLaneChanger::checkChange``:
    ``LCA_OVERLAPPING`` on a negative gap from ``getRealFollower`` /
    ``getRealLeader``, whose gaps are net of that ``minGap``;
    ``MSVehicle::Influencer::influenceChangeDecision`` clears every other
    blocked bit). The gaps given here are those same net gaps
    (``vehicle.getNeighbors``). The change is allowed only when each side,
    less the distance its pair closes in one step, still holds the brake gap
    of the party behind at that party's own comfortable deceleration:

    ``g_foll − c_F·Δt > c_F² / (2·b_foll)`` with ``c_F = (v_foll − v_ego)⁺``,
    ``g_lead − c_L·Δt > c_L² / (2·b_ego)`` with ``c_L = (v_ego − v_lead)⁺``.

    The ``c·Δt`` term is the step order: the guard reads the state after a
    step and SUMO executes the change after the next step's movement, in
    which the pair may close at ``c`` for ``Δt``. The brake terms are the
    speed-aware terms of the weave's forced guard (:func:`_weave_force_gap_ok`)
    without its time-gap and minimum-gap floors, which on the McKnight Rd
    fixture's queue left the acceleration lane full (docs/WEAVE_MODEL_PLAN.md,
    dated section WP-93). At equal speeds both sides reduce to SUMO's own
    overlap test (a positive net gap). A side with no vehicle passes.

    Args:
        v_ego: The changer's speed [m/s].
        g_lead: Net gap to the target-lane leader [m] (``inf`` when none).
        v_lead: That leader's speed [m/s] (``nan`` when none).
        g_foll: Net gap to the target-lane follower [m] (``inf`` when none).
        v_foll: That follower's speed [m/s] (``nan`` when none).
        b_ego: The changer's comfortable deceleration [m/s²].
        b_foll: The follower's comfortable deceleration [m/s²]; ``None`` with
            no follower.
        step_s: Simulation step length [s].

    Returns:
        Whether the vehicle may be under mode 256 this step.
    """
    closing_lead = max(v_ego - v_lead, 0.0) if g_lead < math.inf else 0.0
    if g_lead - closing_lead * step_s <= closing_lead**2 / (2.0 * b_ego):
        return False
    if b_foll is None or g_foll == math.inf:
        return True
    closing_foll = max(v_foll - v_ego, 0.0)
    return g_foll - closing_foll * step_s > closing_foll**2 / (2.0 * b_foll)


class _RoadIndex:
    """One step's subscription results bucketed by road, for the runner's section rules.

    The scripted merges, weaving sections, measured zones and the lane-end
    give-up each read only the vehicles on their own few edges, but each
    used to find them by a pass over every vehicle in the network — one
    pass per section and rule per step, the largest Python cost of a
    multi-section corridor after SUMO itself (docs/PERFORMANCE_2026-10-07.md).
    The index makes one pass, on the first query of the step, and
    :meth:`on` returns the ``(veh_id, result)`` pairs of the vehicles on
    the given roads **in the order of** ``results`` — the order of the pass
    it replaces, so every dict, list and counter those rules build is
    filled exactly as before.
    """

    __slots__ = ("_by_road", "_items", "_results", "_var_road")

    def __init__(self, results: Mapping[str, Any], var_road: int) -> None:
        self._results = results
        self._var_road = var_road
        self._items: list[tuple[str, Any]] | None = None
        self._by_road: dict[str, list[int]] = {}

    def on(self, roads: Iterable[str]) -> list[tuple[str, Any]]:
        """``(veh_id, result)`` of every vehicle on one of ``roads``, in ``results`` order."""
        items = self._items
        if items is None:
            items = self._items = list(self._results.items())
            by_road = self._by_road
            var_road = self._var_road
            for i, (_, res) in enumerate(items):
                road = res[var_road]
                at = by_road.get(road)
                if at is None:
                    by_road[road] = [i]
                else:
                    at.append(i)
        pos: list[int] = []
        n_roads = 0
        for road in dict.fromkeys(roads):  # each road once, whatever the caller passes
            at = self._by_road.get(road)
            if at:
                pos.extend(at)
                n_roads += 1
        if n_roads > 1:
            pos.sort()
        return [items[i] for i in pos]


def _zone_scan_roads(ws: Mapping[str, Any]) -> frozenset[str]:
    """The roads a weaving section's or measured zone's per-step pass acts on.

    Its exit edges, the roads of its target-lane listing (``lane_map``) and
    its own edges: a vehicle on any other road changes nothing in
    :func:`_weave_step` / :func:`_measured_step` except, when the section
    drives it and it is on an internal junction lane, its ``in_transit``
    flag — which those steps set from the section's own ``veh`` instead.
    """
    return frozenset(ws["exit_edges"]) | {road for road, _ in ws["lane_map"]} | set(ws["edges"])


def _scripted_merge_step(
    mod: Any,
    tc: Any,
    ss: dict[str, Any],
    results: Any,
    t: float,
    index: _RoadIndex | None = None,
) -> None:
    """One step of the scripted merge for one ramp (``RampSpec.merge = "scripted"``).

    Drives every vehicle on lane 0 of the attach edge: desired speed matched
    to the mainline neighbour ahead (or the mainline lane's limit), a lane change
    requested when the mainline gaps ahead and behind both clear
    ``accept_gap_s`` × speed + the ramp vehicle's own minimum gap, and a forced
    change (``LC_MODE_SCRIPTED_FORCE``) after ``force_after_s`` inside the last
    ``force_within_m`` of the lane. Control is handed back to SUMO as soon as
    the vehicle leaves the lane. Bookkeeping lands in ``ss`` for ``meta.json``.

    With ``force_guard`` > 0 (WP-93; the default since 2026-10-04) a vehicle
    due to force is under mode 256 only in the steps in which
    :func:`_scripted_force_gap_ok` passes and under ``LC_MODE_SCRIPTED_SAFE``
    in the others (``n_forced_deferred`` counts them); its requests are made
    exactly as without the key. At 0, the step is call for call the one
    before the key: mode 256 from the first forced step on.
    """
    prm = ss["params"]
    guard = float(prm.get("force_guard", SCRIPTED_MERGE_DEFAULTS["force_guard"])) > 0.0
    edge = ss["edge"]
    on_lane0 = {
        vid
        for vid, res in (results.items() if index is None else index.on((edge,)))
        if res[tc.VAR_ROAD_ID] == edge and int(res[tc.VAR_LANE_INDEX]) == 0
    }
    veh = ss["veh"]
    # vehicles that left the acceleration lane (merged, or gone): hand back control
    for vid in [v for v in veh if v not in on_lane0]:
        st = veh.pop(vid)
        if vid in results:
            mod.vehicle.setMaxSpeed(vid, st["v_max_orig"])
            mod.vehicle.setLaneChangeMode(vid, st["lc_mode_orig"])
            ss["n_changed"] += 1
            ss["n_forced"] += int(st["forced"])
            ss["waits_s"].append(t - st["entered_s"])
    v_limit = float(mod.lane.getMaxSpeed(ss["target_lane"]))
    for vid in sorted(on_lane0):
        st = veh.get(vid)
        if st is None:
            st = veh[vid] = {
                "entered_s": t,
                "zone_s": None,
                "requested_s": -math.inf,
                "forced": False,
                # a pending veto's recorded mode, not the vetoed one: the
                # scripted merges are stepped before every weaving section and
                # measured zone (run_micro's loop), so a vehicle a section vetoed
                # in step t and now on this lane is read here in t + dt before
                # that section's restore (review 2026-10-07, third regression
                # review; _lc_mode_owned)
                "lc_mode_orig": _lc_mode_owned(mod, ss, vid),
                "v_max_orig": float(mod.vehicle.getMaxSpeed(vid)),
                "s0": float(mod.vehicle.getMinGap(vid)),
                "mode": LC_MODE_SCRIPTED_SAFE,  # the mode last set (read under force_guard)
            }
            mod.vehicle.setLaneChangeMode(vid, LC_MODE_SCRIPTED_SAFE)
            ss["n_entered"] += 1
        v_ego = float(results[vid][tc.VAR_SPEED])
        remaining = ss["lane_len_m"] - float(results[vid][tc.VAR_LANEPOSITION])
        g_lead, v_lead, _l_id = _neighbor_gap(mod, vid, NEIGHBOR_LEFT_LEADERS)
        g_foll, v_foll, f_id = _neighbor_gap(mod, vid, NEIGHBOR_LEFT_FOLLOWERS)
        # Speed matching through the vehicle's desired speed (setMaxSpeed), never
        # setSpeed: the car-following model keeps full authority over gaps and the
        # lane end, so matching a crawling mainline cannot command a rear-end
        # collision (setSpeed's max-decel clamp overrides its safe-speed clamp in
        # SUMO's influencer). Floor at a creep so a stopped mainline never
        # freezes the acceleration lane.
        v_match = v_lead if g_lead < prm["lookahead_m"] else v_limit
        v_des = min(max(v_match, SCRIPTED_MERGE_CREEP_MS), st["v_max_orig"])
        mod.vehicle.setMaxSpeed(vid, v_des)
        ok_lead = g_lead >= st["s0"] + prm["accept_gap_s"] * v_ego
        ok_foll = g_foll >= st["s0"] + prm["accept_gap_s"] * (v_foll if g_foll < math.inf else 0.0)
        if remaining <= prm["force_within_m"] and st["zone_s"] is None:
            st["zone_s"] = t
        force = st["zone_s"] is not None and t - st["zone_s"] >= prm["force_after_s"]
        if guard:
            if force:
                b_foll = float(mod.vehicle.getDecel(f_id)) if f_id is not None else None
                ok = _scripted_force_gap_ok(
                    v_ego,
                    g_lead,
                    v_lead,
                    g_foll,
                    v_foll,
                    float(mod.vehicle.getDecel(vid)),
                    b_foll,
                    float(ss["step_s"]),
                )
                mode = LC_MODE_SCRIPTED_FORCE if ok else LC_MODE_SCRIPTED_SAFE
                if st["mode"] != mode:
                    mod.vehicle.setLaneChangeMode(vid, mode)
                    st["mode"] = mode
                if ok:
                    st["forced"] = True
                else:
                    ss["n_forced_deferred"] += 1
        elif force and not st["forced"]:
            mod.vehicle.setLaneChangeMode(vid, LC_MODE_SCRIPTED_FORCE)
            st["forced"] = True
        if (ok_lead and ok_foll) or force:
            if t - st["requested_s"] >= prm["change_duration_s"]:
                mod.vehicle.changeLane(vid, 1, prm["change_duration_s"])
                st["requested_s"] = t


def _weave_pos(ws: dict[str, Any], tc: Any, res: Any) -> tuple[int, float]:
    """Driving-order key of a vehicle on a weaving section: (edge index, position)."""
    return ws["edge_index"][res[tc.VAR_ROAD_ID]], float(res[tc.VAR_LANEPOSITION])


def _weave_set_mode(mod: Any, vid: str, st: dict[str, Any], mode: int) -> None:
    """Set a driven vehicle's ``laneChangeMode`` when it differs from the last one set."""
    if st["mode"] != mode:
        mod.vehicle.setLaneChangeMode(vid, mode)
        st["mode"] = mode


def _weave_brake_gap(s0: float, v_ego: float, v_lead: float, b: float) -> float:
    """The gap a vehicle closing on a slower leader needs to match its speed at ``b`` [m].

    Speed-aware acceptance (2026-09-24, block 3; docs/WEAVE_MODEL_PLAN.md,
    dated section): ``s0 + max(v_ego − v_lead, 0)² / (2·b)`` — constant
    deceleration ``b`` (the vehicle's own comfortable deceleration, SUMO's
    ``decel``; :func:`_weave_veh`) from the closing speed to the leader's,
    the leader holding its speed, plus the minimum-gap floor given. Zero
    closing speed (a leader as fast or faster) leaves ``s0`` alone. For the
    Ruth St trace (a 23 m/s exiter, a queue head at 1 m/s, ``b`` 1.67 m/s²)
    it is 147 m against the 27 m offered; for a follower at 16 m/s behind a
    halted changer 78 m against 10.7 (the corridor-demand trace, seed 5).

    Two IDM forms were measured and rejected on the fixtures (the plan's
    dated section has the table): the changer's desired gap ``s*(v, v −
    v_L)`` itself (the IDM's *no-braking* gap, ``s0 + v·T`` at equal
    speeds — 263 m for the trace) removed the collisions and
    locked every T.H.52 fixture (entrance 398 → 294 of 466 at seed 3, 102 →
    5,232 deferred); the gap at which the changer's IDM towards the leader
    asks exactly ``−b``, ``s*/√(1 + b/a_max − (v/v0)^4)`` (153 m for the
    trace, ≈ 20 m at equal speeds at 23 m/s) removed them too but, through
    IDM's kinematic term ``v·Δv/(2√(a·b))`` — conservative at the moderate
    closing rates of a weave (32 m for 15 → 10 m/s against 10 m here) —
    slowed lane 1 at the gore (corridor demand seed 3: 3.4 m/s in two
    minutes against 6.5) and, on the follower side of the forced guard,
    locked the short section. This kinematic form is the statement of the
    goal — no change onto a leader, and no forced change in front of a
    follower, that the party behind cannot brake for at ``b``.

    Args:
        s0: The minimum-gap floor [m] (the changer's ``minGap``; ``0`` for a
            released pair, :func:`_weave_pair_release`).
        v_ego: The speed of the vehicle behind [m/s].
        v_lead: The speed of the vehicle ahead [m/s].
        b: The comfortable deceleration of the vehicle behind [m/s²].

    Returns:
        The gap [m].
    """
    return s0 + max(v_ego - v_lead, 0.0) ** 2 / (2.0 * b)


def _weave_lead_gap_min(
    s0: float, accept_s: float, v_ego: float, g_lead: float, v_lead: float, b_ego: float
) -> float:
    """The leader-side gap a weave change needs: the time gap or the brake gap, whichever is larger.

    Speed-aware acceptance (2026-09-24, block 3): ``max(s0 + accept_s · v_ego,
    s0 + (v_ego − v_lead)⁺² / (2·b))`` (:func:`_weave_brake_gap`) — the time
    gap reads the changer's speed and not the leader's: a 23 m/s exiter with
    a 27 m gap to a queue head at 1 m/s passed ``s0 + 0.6 · 23 = 16 m``,
    dropped in, braked at −9 m/s² and stopped 1.9 m short, and the next
    exiter hit it (Ruth St fixture, seed 4 at the 500 m vacate window,
    t = 600.5 s; the same trace at the 271 m window, seed 5, t = 717.5 s).
    Refused, the exiter eases in its lane towards the queue's speed
    (:func:`_weave_cooperate`) and drops in once the gap it is offered is
    one it can brake for at ``b``.

    Args:
        s0: The minimum-gap floor [m].
        accept_s: Accepted time gap of the movement [s].
        v_ego: The changer's speed [m/s].
        g_lead: Gap to the target-lane leader [m] (``inf`` when none).
        v_lead: That leader's speed [m/s] (``nan`` when none).
        b_ego: The changer's comfortable deceleration [m/s²].

    Returns:
        The gap [m] the leader side must clear; ``s0 + accept_s · v_ego``
        with no leader.
    """
    floor = s0 + accept_s * v_ego
    if g_lead == math.inf:
        return floor
    return max(floor, _weave_brake_gap(s0, v_ego, v_lead, b_ego))


def _weave_force_gap_ok(
    s0: float,
    accept_s: float,
    v_ego: float,
    g_lead: float,
    v_lead: float,
    g_foll: float,
    v_foll: float,
    b_ego: float | None = None,
    b_foll: float | None = None,
    accept_lag_s: float | None = None,
) -> bool:
    """Minimum-gap guard of a forced weave change (``LC_MODE_SCRIPTED_FORCE``).

    Mode 256 only avoids an *immediate* overlap, so a forced change into a
    gap the follower is closing on fast can still end in a rear-end collision
    (MnDOT I-94 WB slice, 2026-09-23: one on ``999007700_0`` at t = 1018 s).
    The forced change is allowed only when each target-lane gap exceeds the
    vehicle's own ``minGap`` plus the distance the pair closes in
    ``accept_s`` seconds (the movement's accepted time gap, ``exit_accept_gap_s``
    for the exiting movement): ``g_lead > s0 + accept_s · max(v_ego - v_lead, 0)``
    and ``g_foll > s0 + accept_s · max(v_foll - v_ego, 0)``. It is laxer than
    normal acceptance (``s0 + accept_s · v``, the full speed rather than the
    closing speed) — that is what forcing means — but never admits a gap
    below ``s0`` or one closing within the accepted time gap.

    Speed-aware guard (2026-09-24, block 3): with ``b_ego`` given, the
    leader-side bound is also at least the changer's brake gap on that
    leader, ``s0 + (v_ego − v_lead)⁺²/(2·b_ego)`` (:func:`_weave_brake_gap`,
    the term the acceptance in :func:`_weave_step` uses through
    :func:`_weave_lead_gap_min`), so forcing cannot command a change onto a
    slower leader that the acceptance refuses on the leader side; with
    ``b_foll`` given, the follower side is the mirror image, the follower's
    brake gap towards the changer, ``(v_foll − v_ego)⁺²/(2·b_foll)`` (no
    ``s0`` term: the reported follower gap already excludes the follower's
    ``minGap``, as the acceptance's absorption check reads it). A forced
    change is thus never commanded into a gap either party would have to
    brake beyond its ``b`` for, and forcing stays laxer than acceptance by
    the full-speed time gaps alone. The follower side was derived from the
    trace of a *released* pair (``s0 = 0``, :func:`_weave_pair_release`) on
    ``weave_th52.osm`` at the corridor's demand, seed 5, t = 977.5 s
    (session record, the grid of the leader-side form alone): an exiter
    halted at the gore's end forced into lane 0 at 0.54 m/s with 10.7 m to
    a follower at 16.1 m/s — the closing-speed bound asked 9.35 m, the
    follower needed 73 m at ``b`` — and was hit a second later. The pair
    release keeps its regime (both below the creep speed, both brake gaps
    near zero) and loses the fast follower.

    The leader and follower gaps apart (WP-80): ``accept_lag_s``, when
    given, is the follower side's time gap (``None`` = ``accept_s``). The
    runner always passes one value since the weave's follower-side keys were
    removed on 2026-10-06 (docs/WEAVE_MODEL_PLAN.md; release 2.5.0); the
    split serves :func:`_weave_change_ok`, the oracle of the offline
    restatement in ``calibration.lane_change_gaps``.

    Args:
        s0: The changing vehicle's minimum gap [m].
        accept_s: Accepted time gap of the movement [s] — its leader side.
        v_ego: Its speed [m/s].
        g_lead: Gap to the target-lane leader [m] (``inf`` when none).
        v_lead: That leader's speed [m/s] (``nan`` when none).
        g_foll: Gap to the target-lane follower [m] (``inf`` when none).
        v_foll: That follower's speed [m/s] (``nan`` when none).
        b_ego: The changer's comfortable deceleration [m/s²] for the
            speed-aware leader side; ``None`` keeps the closing-speed bound
            alone.
        b_foll: The follower's comfortable deceleration [m/s²] for the
            speed-aware follower side; ``None`` keeps the closing-speed
            bound alone.
        accept_lag_s: The movement's follower-side time gap [s]; ``None``
            uses ``accept_s``.

    Returns:
        Whether the forced change may be requested this step.
    """
    closing_lead = max(v_ego - v_lead, 0.0) if g_lead < math.inf else 0.0
    closing_foll = max(v_foll - v_ego, 0.0) if g_foll < math.inf else 0.0
    lead_min = s0 + accept_s * closing_lead
    if b_ego is not None:
        lead_min = max(lead_min, s0 + closing_lead**2 / (2.0 * b_ego))
    lag_s = accept_s if accept_lag_s is None else accept_lag_s
    foll_min = s0 + lag_s * closing_foll
    if b_foll is not None:
        foll_min = max(foll_min, closing_foll**2 / (2.0 * b_foll))
    return g_lead > lead_min and g_foll > foll_min


def _idm_accel(
    v: float, v0: float, s: float, dv: float, T: float, a_max: float, b: float, s0: float
) -> float:
    """Intelligent Driver Model acceleration [m/s²] (Treiber, Hennecke & Helbing 2000).

    ``a = a_max · [1 − (v/v0)^4 − (s*/s)²]`` with
    ``s* = s0 + max(0, v·T + v·Δv / (2·√(a_max·b)))`` (CLAUDE.md §3.1), where
    ``s`` is the bumper-to-bumper gap to the leader and ``Δv = v − v_leader``.
    A gap of zero or less (the leader overlaps or is behind) returns ``-inf``:
    no finite braking reaches a positive gap from there.

    Args:
        v: Own speed [m/s].
        v0: Desired speed [m/s].
        s: Bumper-to-bumper gap [m]; ``inf`` for a free road.
        dv: Approach rate ``v − v_leader`` [m/s].
        T: Desired time headway [s].
        a_max: Maximum acceleration [m/s²].
        b: Comfortable deceleration [m/s²].
        s0: Minimum gap [m].

    Returns:
        The acceleration; negative is braking.
    """
    if s <= 0.0:
        return -math.inf
    free = 1.0 - (v / v0) ** 4 if v0 > 0.0 else 0.0
    if s == math.inf:
        return a_max * free
    s_star = s0 + max(0.0, v * T + v * dv / (2.0 * math.sqrt(a_max * b)))
    return a_max * (free - (s_star / s) ** 2)


def _weave_veh(mod: Any, ws: dict[str, Any], vid: str) -> dict[str, float]:
    """A vehicle's car-following constants, read once per run and cached in ``ws``.

    SUMO exposes the drawn IDM/EIDM parameters as ``tau`` (T), ``accel``
    (a_max), ``decel`` (b), ``minGap`` (s0) and ``maxSpeed`` (v0, the fleet
    sets ``speedFactor="1.0"``), plus the vehicle ``length``.
    """
    cache: dict[str, dict[str, float]] = ws["veh_params"]
    p = cache.get(vid)
    if p is None:
        p = cache[vid] = {
            "len": float(mod.vehicle.getLength(vid)),
            "T": float(mod.vehicle.getTau(vid)),
            "a": float(mod.vehicle.getAccel(vid)),
            "b": float(mod.vehicle.getDecel(vid)),
            "s0": float(mod.vehicle.getMinGap(vid)),
            "vmax": float(mod.vehicle.getMaxSpeed(vid)),
        }
    return p


def _weave_lane_vmax(mod: Any, ws: dict[str, Any], road: str, lane: int) -> float:
    """A lane's speed limit [m/s], cached in ``ws``."""
    cache: dict[str, float] = ws["lane_vmax"]
    lane_id = f"{road}_{lane}"
    v = cache.get(lane_id)
    if v is None:
        v = cache[lane_id] = float(mod.lane.getMaxSpeed(lane_id))
    return v


def _weave_lane_map(
    net: Any, chain: Sequence[str], section_edges: Sequence[str]
) -> dict[tuple[str, int], int]:
    """``(edge, lane index) → section lane index`` for the lanes a changer's gaps lie on.

    The section's own lanes map to themselves; on the corridor edge before
    the section, the lanes that connect into a section lane ≥ 1 (the through
    lanes — lane 0 comes from the ramp), and on the edge after it, the lanes
    fed by a section lane ≥ 1, map to that section lane. Gaps are then found
    on one lane of consecutive edges from up to a lookahead behind the section
    to one edge beyond it, which is where the follower of an entering
    vehicle's gap is while the vehicle is still near the section start.
    """
    m: dict[tuple[str, int], int] = {}
    for e in section_edges:
        for lane in net.getEdge(e).getLanes():
            m[(e, int(lane.getIndex()))] = int(lane.getIndex())
    first, last = section_edges[0], section_edges[-1]
    i0, i1 = chain.index(first), chain.index(last)
    if i0 > 0:
        prev = chain[i0 - 1]
        for lane in net.getEdge(prev).getLanes():
            for conn in lane.getOutgoing():
                to_lane = conn.getToLane()
                if conn.getTo().getID() == first and int(to_lane.getIndex()) >= 1:
                    m[(prev, int(lane.getIndex()))] = int(to_lane.getIndex())
    if i1 + 1 < len(chain):
        nxt = chain[i1 + 1]
        for lane in net.getEdge(last).getLanes():
            if int(lane.getIndex()) == 0:
                continue
            for conn in lane.getOutgoing():
                if conn.getTo().getID() == nxt:
                    m[(nxt, int(conn.getToLane().getIndex()))] = int(lane.getIndex())
    return m


def _weave_vacate_lanes(
    net: Any,
    chain: Sequence[str],
    section_edges: Sequence[str],
    offsets: dict[str, float],
    ahead_m: float,
) -> dict[str, tuple[int, int]]:
    """``{corridor edge → (its lane feeding section lane 1, its lane feeding section lane 2)}`` over the vacate window.

    The lanes a through vehicle vacates from and to under ``vacate_ahead_m``
    (:func:`_weave_vacate_step`), walked upstream from the section start
    along the corridor chain, edge by edge, as far as the window reaches
    (2026-09-24, block 3, the cross-edge window; before it the window was
    truncated to the edge before the section). On each edge the feeding
    lane is the one lane whose netconvert connection leads into the lane
    found on the edge after it, so the index shifts where a lane is added
    or dropped — an acceleration lane on the right shifts both by one (on
    ``tests/fixtures/weave_th52_upstream.osm``: way 111 lanes 0 → 1, way 110
    lanes 1 → 2, way 101 lanes 0 → 1). The walk stops, and the window is
    truncated there, at an edge missing from ``offsets``, at one where
    either lane has no unique feeder (a lane that splits, or two lanes that
    merge into one), or where both feeders are one lane. Empty — the rule
    is inert — when the section has no corridor edge before it, ``ahead_m``
    is not positive, or the edge before it has no lane feeding section lane
    2 (a two-lane section). Listed nearest the section first.
    """
    out: dict[str, tuple[int, int]] = {}
    i0 = chain.index(section_edges[0])
    if ahead_m <= 0.0 or i0 == 0:
        return out
    x_lo = float(offsets[section_edges[0]]) - ahead_m
    cur, k_from, k_to = section_edges[0], 1, 2
    for prev in reversed(chain[:i0]):
        if prev not in offsets:
            break
        feeders: dict[int, set[int]] = {}
        for lane in net.getEdge(prev).getLanes():
            for conn in lane.getOutgoing():
                if conn.getTo().getID() == cur:
                    k = int(conn.getToLane().getIndex())
                    feeders.setdefault(k, set()).add(int(lane.getIndex()))
        f_from, f_to = feeders.get(k_from, set()), feeders.get(k_to, set())
        if len(f_from) != 1 or len(f_to) != 1 or f_from == f_to:
            break
        (k_from,), (k_to,) = f_from, f_to
        out[prev] = (k_from, k_to)
        cur = prev
        if float(offsets[prev]) <= x_lo:
            break
    return out


def _weave_vacate_exempt_ids(
    net: Any,
    ramps: Sequence[RampSpec],
    section: WeaveSection,
    window_edges: Iterable[str],
    route_by_id: Mapping[str, str],
) -> frozenset[str]:
    """The vehicles :func:`_weave_vacate_step` never asks: not through the section.

    Those bound for the section's paired exit (``exiting_ids``) and those
    bound for an off-ramp that leaves from a window edge
    (:func:`_weave_vacate_lanes`) — they need the right-hand lane the rule
    would move them out of, and they never reach the section (review,
    2026-09-24 block 3: a vehicle exiting inside the window was "through"
    for the section and asked left, away from its exit). An off-ramp
    leaving upstream of the window is behind every vehicle in it: a
    vehicle still on the corridor with such a route gave its exit up
    (``exit_giveup_m``) and is through now, so it is not exempt.
    """
    exits = {section.off_ramp}
    window = set(window_edges)
    for j, ramp in enumerate(ramps):
        if ramp.kind != "off" or not net.hasEdge(ramp.edges[0]):
            continue
        if any(e.getID() in window for e in net.getEdge(ramp.edges[0]).getIncoming()):
            exits.add(j)
    return frozenset(vid for vid, rid in route_by_id.items() if _route_exit(rid) in exits)


def _weave_vacate_bound_veh_h(
    ws: dict[str, Any], t: float, flow_key: str = "vacate_flow_s"
) -> float:
    """The vacate rule's bound this step [veh/h]: ``vacate_max_veh_h`` when set, else the target lane's spare capacity.

    Spare capacity is ``VACATE_LANE_CAPACITY_VEH_H`` less the flow the
    target lane carried into the vacate window over the last
    ``VACATE_FLOW_WINDOW_S`` (vehicles first seen there in that lane, each
    once; :func:`_weave_vacate_step` keeps the sightings), floored at zero.
    The flow is read over the full window from ``t = 0``, so the first
    minute of a run underestimates it and the bound is permissive there.
    ``flow_key`` names the sightings: the vacate rule's target lane (the
    lane feeding section lane 2) by default, ``"prep_flow_s"`` for the
    exiters' early move, whose target is the lane feeding section lane 1
    (:func:`_weave_exit_prepare_step`).
    """
    fixed = float(ws["params"]["vacate_max_veh_h"])
    if fixed > 0.0:
        return fixed
    flow_s: deque[float] = ws[flow_key]
    while flow_s and flow_s[0] <= t - VACATE_FLOW_WINDOW_S:
        flow_s.popleft()
    flow_veh_h = len(flow_s) * 3600.0 / VACATE_FLOW_WINDOW_S
    return max(VACATE_LANE_CAPACITY_VEH_H - flow_veh_h, 0.0)


def _weave_vacate_step(
    mod: Any,
    tc: Any,
    ws: dict[str, Any],
    results: Any,
    lanes: dict[int, list[tuple[float, str]]],
    t: float,
    index: _RoadIndex | None = None,
) -> None:
    """Through traffic vacates the weave lane upstream of the section (2026-09-24, block 3).

    The one rule of the third derivation (docs/WEAVE_MODEL_PLAN.md, dated
    paragraph): a saturated one-sided weave is carried by *through* vehicles
    leaving the weave lane before the section — "through traffic keep left"
    signage and driver anticipation — so that the entering and the exiting
    movements exchange over the auxiliary lane and the weave lane alone.
    SUMO's LC2013 does not do this on its own once the lane crawls (a
    speed-gain change needs a speed advantage a uniform crawl does not
    offer).

    Each through vehicle (not bound for the paired exit, nor for an off-ramp
    leaving from a window edge — ``vacate_exempt_ids``,
    :func:`_weave_vacate_exempt_ids`; review, 2026-09-24 block 3) in the lane feeding section lane 1 of a corridor edge
    within ``vacate_ahead_m`` of the section start (``vacate_lanes``,
    :func:`_weave_vacate_lanes`: the window measured along the corridor
    chain across as many upstream edges as it reaches, the feeding lane's
    index derived per edge from netconvert's connections — 2026-09-24,
    block 3, the cross-edge window), once inside the window, is asked
    **once**: ``vehicle.changeLane(vid, lane_to, duration)`` under
    ``LC_MODE_SCRIPTED_SAFE`` (mode 512: every model-driven change off — no
    speed-gain, keep-right or cooperative change of SUMO's own — and the
    request executed only when SUMO's safety check on the target lane's
    leader and follower gaps passes, the vehicle adapting its speed to reach
    such a gap and the follower informed as for any urgent change). The
    request lives for the travel time to the section start at the vehicle's
    current speed (floored at ``SCRIPTED_MERGE_CREEP_MS``, at least one
    step), so SUMO may execute it at any point of the remaining window. The
    request is by lane *index*: when the vehicle crosses onto the next
    window edge still in the weave lane and that edge's target lane has
    another index (a lane added or dropped), the open request is re-issued
    there for its remaining life. The vehicle's original ``laneChangeMode``
    is restored when it is seen in the target lane — on a window edge, or
    on the section's lane 2 when the change and the crossing onto the
    section fell in one step (review, 2026-09-24 block 3: counted refused
    until then) — (``n_vacated``), or when
    the request has expired or the vehicle has reached the section still in
    the weave lane (``n_vacate_refused``); a request still open at the
    hand-back is ended with a one-step stay in the current lane, because on
    the section's edge (one lane more, the ramp's) the index is the weave
    lane itself. A vehicle that cannot change stays and behaves as before.

    A gap-conditioned form (``vacate_no_follower_braking``) was removed on
    2026-10-06 (docs/WEAVE_MODEL_PLAN.md; reproduce with release 2.5.0).

    **The bound.** Vehicles first asked in the last
    ``VACATE_FLOW_WINDOW_S`` are limited to ``vacate_max_veh_h``: a positive
    value is the bound, ``0`` (the default) the target lane's spare
    capacity ``VACATE_LANE_CAPACITY_VEH_H − flow``, the flow being the
    vehicles the target lane carried into the window over the same 60 s
    (:func:`_weave_vacate_bound_veh_h`), so the rule cannot ask more into
    the target lane than it has room for. ``n_vacate_skipped_no_gap`` counts
    through vehicles that crossed the window never asked — for want of the
    bound — each once (one that moves left by its own model is not
    counted); ``n_vacate_requests`` the requests made, in vehicle-steps.

    The window's lanes are listed here from ``results`` (the section's own
    ``lane_map`` lists only the edge before it); the target lane continues
    onto the section through ``lanes[2]``. ``vacate_ahead_m = 0`` disables
    the rule. A held vehicle on a junction's internal lane between two
    window edges (``OSMNetwork.internal_links``) is left as it is for that
    step. The cooperative-follower rules of the second derivation are untouched: a
    vacating vehicle may still be a chosen gap's follower and receive its
    speed target. A vehicle already under a scripted ``laneChangeMode``
    (512 / 256 / 768: driven by a section whose last edge is this edge, by
    a scripted merge, or held by another section's vacate rule) is not
    asked while that hold lasts and not marked seen — its real mode is not
    readable here, and two holds on one vehicle restored each other's
    (review, 2026-09-24 block 3: the vehicle was left on the other
    section's one-step mode 256).
    """
    spec: dict[str, tuple[int, int]] = ws["vacate_lanes"]
    ahead = float(ws["params"]["vacate_ahead_m"])
    if not spec or ahead <= 0.0:
        return
    step_s = float(ws["step_s"])
    x_offset: dict[str, float] = ws["x_offset"]
    x_start = float(x_offset[ws["edges"][0]])
    x_lo = x_start - ahead
    active: dict[str, dict[str, Any]] = ws["vacate"]
    seen: set[str] = ws["vacate_seen"]
    pending: set[str] = ws["vacate_pending"]
    exempt: frozenset[str] = ws["vacate_exempt_ids"]
    # --- the window's lanes this step, across its edges ---------------------
    # (road, lane index) of every vehicle on a window edge; the weave lane's
    # and the target lane's listings on the section axis
    where: dict[str, tuple[str, int]] = {}
    weave: list[tuple[float, str]] = []
    target: list[tuple[float, str]] = []
    for vid, res in results.items() if index is None else index.on(spec):
        road = res[tc.VAR_ROAD_ID]
        lanes_e = spec.get(road)
        if lanes_e is None:
            continue
        lane = int(res[tc.VAR_LANE_INDEX])
        where[vid] = (road, lane)
        if lane == lanes_e[0]:
            weave.append((x_offset[road] + float(res[tc.VAR_LANEPOSITION]), vid))
        elif lane == lanes_e[1]:
            target.append((x_offset[road] + float(res[tc.VAR_LANEPOSITION]), vid))
    # the target lane continues onto the section and the edge after it (the
    # leader of a gap near the section start); the edge before the section
    # is listed by both and taken once
    listed = {vid for _, vid in target}
    target.extend((x, vid) for x, vid in lanes.get(2, []) if vid not in listed)
    target.sort()
    target_ids = {vid for _, vid in target}

    def in_lane(vid: str, side: int) -> bool:
        """Whether ``vid`` is on a window edge in its weave (0) / target (1) lane."""
        at = where.get(vid)
        return at is not None and at[1] == spec[at[0]][side]

    # --- settle the held vehicles ---------------------------------------
    for vid in sorted(active):
        st = active[vid]
        res = results.get(vid)
        if res is None:
            del active[vid]  # left the network before the section: nothing to restore
            continue
        road = res[tc.VAR_ROAD_ID]
        lane = int(res[tc.VAR_LANE_INDEX])
        if road.startswith(":"):
            continue  # on a junction between window edges: decided on the next edge
        if vid in target_ids:
            # in the target lane: on a window edge, or on the section's lane
            # 2 (``lanes[2]``) when the change and the crossing fell in one
            # step — under mode 512 the request is the only change it can make
            ws["n_vacated"] += 1
        elif in_lane(vid, 0) and t < st["until_s"]:
            lane_to_here = spec[road][1]
            if lane_to_here != st["lane_to"]:
                # crossed onto a window edge where the target lane has
                # another index (a lane added or dropped): the open request
                # re-addressed there for the rest of its life
                st["lane_to"] = lane_to_here
                mod.vehicle.changeLane(vid, lane_to_here, max(st["until_s"] - t, step_s))
            continue  # still in the weave lane with the request open
        else:
            ws["n_vacate_refused"] += 1
        if t < st["until_s"]:
            # the request is by lane *index* and outlives the hand-back: on
            # the section's edge (one lane more, the ramp's) the same index
            # is the weave lane, so a live request would pull a vacated
            # vehicle back in. A one-step stay in the current lane ends it
            mod.vehicle.changeLane(vid, lane, step_s)
        mod.vehicle.setLaneChangeMode(vid, st["lc_mode_orig"])
        del active[vid]
    # --- the through vehicles in the window this step ---------------------
    now: dict[str, float] = {}
    for x, vid in weave:
        if x_lo <= x < x_start and vid not in exempt:
            now[vid] = x
    # not asked last step and no longer in the window: skipped, unless the
    # vehicle moved left by its own model
    for vid in sorted(pending - set(now)):
        if vid not in results or in_lane(vid, 1):
            continue
        ws["n_vacate_skipped_no_gap"] += 1
    pending.clear()
    # --- the target lane's inflow to the window (the bound's flow) ----------
    flow_ids: set[str] = ws["vacate_flow_ids"]
    flow_ids.intersection_update(results.keys())
    for x, vid in target:
        if x_lo <= x < x_start and vid not in flow_ids and in_lane(vid, 1):
            flow_ids.add(vid)
            ws["vacate_flow_s"].append(t)
    bound = _weave_vacate_bound_veh_h(ws, t)
    asks_s: deque[float] = ws["vacate_asks_s"]
    while asks_s and asks_s[0] <= t - VACATE_FLOW_WINDOW_S:
        asks_s.popleft()
    budget = int(bound * VACATE_FLOW_WINDOW_S / 3600.0) - len(asks_s)
    # --- ask, nearest the section first ------------------------------------
    for vid, x in sorted(now.items(), key=lambda kv: -kv[1]):
        if vid in active or vid in seen:
            continue  # the request is open, or was made before (asked once)
        if budget <= 0:
            pending.add(vid)
            continue
        # a veto pending on the vehicle (any section's opposing resolution)
        # is consumed and its recorded mode taken: the hold then restores
        # the real mode (review 2026-10-07, third regression review)
        mode_orig = _lc_mode_owned(mod, ws, vid)
        if mode_orig in (
            LC_MODE_SCRIPTED_SAFE,
            LC_MODE_SCRIPTED_FORCE,
            LC_MODE_SCRIPTED_SAFE_NO_ADAPT,
        ):
            # already under a scripted hold (a driven vehicle of a
            # section whose last edge is this edge, a scripted merge's,
            # or another section's vacate hold): its real mode is not
            # readable here and two holds would restore each other's.
            # Not asked while the hold lasts (review, 2026-09-24)
            continue
        seen.add(vid)
        asks_s.append(t)
        budget -= 1
        lane_to = spec[where[vid][0]][1]
        v = float(results[vid][tc.VAR_SPEED])
        duration = max((x_start - x) / max(v, SCRIPTED_MERGE_CREEP_MS), step_s)
        active[vid] = {
            "lc_mode_orig": mode_orig,
            "until_s": t + duration,
            "lane_to": lane_to,
        }
        mod.vehicle.setLaneChangeMode(vid, LC_MODE_SCRIPTED_SAFE)
        mod.vehicle.changeLane(vid, lane_to, duration)
        ws["n_vacate_requests"] += 1


def _weave_exit_prepare_abreast(
    x_c: float, len_c: float, others: Sequence[tuple[float, float]]
) -> bool:
    """Whether a vehicle (front ``x_c``, length ``len_c``) overlaps any of ``others`` (front, length) along the axis.

    The ordering of the exiters' early move against the vacate rule
    (:func:`_weave_exit_prepare_step`): an exiter one lane left of ``k_from``
    abreast of a through vehicle held by the vacate rule in ``k_from`` —
    each asking into the other's lane — yields.
    """
    return any(x_o - len_o < x_c and x_c - len_c < x_o for x_o, len_o in others)


def _weave_exit_prepare_step(
    mod: Any,
    tc: Any,
    ws: dict[str, Any],
    results: Any,
    lanes: dict[int, list[tuple[float, str]]],
    t: float,
    index: _RoadIndex | None = None,
) -> None:
    """Exiters move right before the section: the vacate rule's mirror (2026-09-24, block 3, WP-62).

    ``exit_prepare`` (``WEAVE_DEFAULTS``; 0 = off). Section lane 0 is the
    auxiliary lane, which begins at the entrance's gore and leads only to
    the exit; the one lane from which a single change reaches it is section
    lane 1, and the only lane feeding section lane 1 on each corridor edge
    before the section is the one the vacate rule empties of through
    traffic (``vacate_lanes``, :func:`_weave_vacate_lanes`: per window edge
    ``(k_from, k_to)``, the lanes feeding section lanes 1 and 2). An exiter
    that arrives in section lane ``k`` owes ``k`` changes inside the
    section, each through a lane the entering movement crosses the other
    way. The rule asks each vehicle bound for the paired exit
    (``exiting_ids``, not given up) that is on a window edge within
    ``vacate_ahead_m`` of the section start in a lane *left* of ``k_from``
    to move into ``k_from`` there — the direction of its own route, from
    which one change reaches the auxiliary lane — under the vacate rule's
    own terms: asked **once**, ``vehicle.changeLane(vid, k_from, duration)``
    under ``LC_MODE_SCRIPTED_SAFE`` (mode 512: SUMO's safety check on both
    target-lane gaps, the vehicle adapting its speed, every model-driven
    change off), the request living for the travel time to the section
    start at the vehicle's speed (floored at ``SCRIPTED_MERGE_CREEP_MS``); a
    vehicle two lanes out is moved one lane at a time by SUMO under the one
    request. Re-addressed when the vehicle crosses onto a window edge where
    ``k_from`` has another index. The gap-conditioned form
    (``vacate_no_follower_braking``) was removed on 2026-10-06
    (docs/WEAVE_MODEL_PLAN.md; reproduce with release 2.5.0).

    The rule asks no more per ``VACATE_FLOW_WINDOW_S`` than
    ``vacate_max_veh_h`` or, at its default of 0, the target lane's spare
    capacity (:func:`_weave_vacate_bound_veh_h` over the sightings of
    vehicles first seen in ``k_from`` inside the window, ``prep_flow_s``).
    The vehicle's mode is restored when it is seen in ``k_from`` on a
    window edge, or in section lane 1 when the change and the crossing
    fell in one step (``n_exit_prepared``), or when the request has
    expired or the vehicle has reached the section still left of it
    (``n_exit_prepare_refused``, state only); an open request at the
    hand-back is ended with a one-step stay in the current lane, because on
    the section's edge the index ``k_from`` is the auxiliary lane, which
    the section's own acceptance (:func:`_weave_step`) must decide. The
    hand-back happens before the section takes the vehicle under control,
    so the mode it records is the vehicle's own.

    **Bounded, no chain.** Nobody but the exiter is commanded: no
    target-lane vehicle is asked to brake or move, each exiter is asked
    once and never while another scripted hold is on it
    (the vacate rule's guard: modes 512 / 256 / 768), and no request
    outlives the window. **The ordering against the vacate rule.** Their
    vehicles are disjoint — the vacate rule never asks a vehicle bound for
    the paired exit (``vacate_exempt_ids``), this rule asks nothing else —
    but their moves are opposite, each into the other's source lane, and
    SUMO does not order an abreast pair asking across each other: with both
    under mode 512 each refuses on the other and adapts its speed towards
    the other, and on the fixture grid such pairs stood for up to 82 s,
    one through vehicle at 0 m/s with 76 m of its own lane free ahead
    (docs/WEAVE_MODEL_PLAN.md, WP-62). So the vacate request goes first:
    the vacate rule runs first each step, and an exiter one lane left of
    ``k_from`` abreast of a vacate-held through vehicle in ``k_from``
    (:func:`_weave_exit_prepare_abreast`) is not asked while the pair
    lasts, and one already asked has its request ended by a one-step stay
    and re-issued for the rest of its life once clear (vehicle-steps
    yielded in ``n_exit_prepare_yielded``, state only). The through
    vehicle then meets a vehicle keeping its lane, which SUMO's own gap
    logic resolves; the vacate rule's behaviour is unchanged.
    ``vacate_ahead_m = 0`` disables both rules.
    Vehicle-steps held under a request are kept in ``n_exit_prepare_held``
    (state only).

    **Measured and left off** (docs/WEAVE_MODEL_PLAN.md, dated section
    WP-62). On ``tests/fixtures/weave_th52_corridor.osm`` under the
    observed 05:30–05:50 movements (seeds 3 / 4 / 5) it moves 71 / 95 / 95
    exiters and the corridor section test reads no better: the T.H.52
    entrance 371 / 355 / 337 of 407 departed against 368 / 360 / 350, the
    exit end's lanes at or below 20 m/s in 11 / 10 / 14 of 16 windows
    against 10 / 11 / 13, the mainline 1,105 and 1,116 of 1,196 at seeds 4
    and 5 (1,137 asked; 1,157 and 1,149 without it), the exit end's lane 0
    1,044 / 1,080 / 1,089 veh/h against 1,035 / 1,104 / 1,122. In free flow
    SUMO's own strategic change (``lcStrategic`` 5, the approach's best
    lane offset −1 over the whole window) has already sorted the exiters —
    73 of 73, 77 of 78 and 77 of 81 in the rightmost lane 126.6 m before
    the gore in minutes 0–5; they are left of it only once that lane queues,
    and there 16 / 50 / 54 of the asked reach the section still left of it.
    """
    prm = ws["params"]
    if float(prm["exit_prepare"]) <= 0.0:
        return
    spec: dict[str, tuple[int, int]] = ws["vacate_lanes"]
    ahead = float(prm["vacate_ahead_m"])
    if not spec or ahead <= 0.0:
        return
    step_s = float(ws["step_s"])
    x_offset: dict[str, float] = ws["x_offset"]
    x_start = float(x_offset[ws["edges"][0]])
    x_lo = x_start - ahead
    active: dict[str, dict[str, Any]] = ws["prep"]
    seen: set[str] = ws["prep_seen"]
    exiting: frozenset[str] = ws["exiting_ids"]
    gave_up: set[str] = ws["gave_up"]
    # --- the window's lanes by offset from the target lane (0 = k_from, 1 =
    # the lane left of it, ...), on the section axis; an exiter's offset
    # lane k continues into section lane k + 1 (``lanes[k + 1]``) -----------
    where: dict[str, tuple[str, int]] = {}
    by_off: dict[int, list[tuple[float, str]]] = {}
    for vid, res in results.items() if index is None else index.on(spec):
        road = res[tc.VAR_ROAD_ID]
        lanes_e = spec.get(road)
        if lanes_e is None:
            continue
        lane = int(res[tc.VAR_LANE_INDEX])
        where[vid] = (road, lane)
        off = lane - lanes_e[0]
        if off >= 0:
            x = x_offset[road] + float(res[tc.VAR_LANEPOSITION])
            by_off.setdefault(off, []).append((x, vid))
    # every offset up to the leftmost occupied one, the target lane always —
    # its section lane is listed even with nobody in it on the window edges
    for off in range(max(by_off, default=0) + 1):
        lst = by_off.setdefault(off, [])
        listed = {vid for _, vid in lst}
        lst.extend((x, vid) for x, vid in lanes.get(off + 1, []) if vid not in listed)
        lst.sort()
    target = by_off.get(0, [])
    target_ids = {vid for _, vid in target}

    def offset(vid: str) -> int | None:
        """``vid``'s lane offset from ``k_from`` on a window edge (``None`` off the window)."""
        at = where.get(vid)
        return None if at is None else at[1] - spec[at[0]][0]

    # the ordering against the vacate rule: its held through vehicles in k_from
    # (front, length), beside which an exiter one lane left of k_from yields
    vacating = [
        (x, _weave_veh(mod, ws, vid)["len"])
        for x, vid in target
        if vid in ws["vacate"] and offset(vid) == 0
    ]

    def beside_vacating(vid: str, x: float) -> bool:
        """An exiter one lane left of ``k_from`` abreast of a vacate-held through vehicle."""
        return (
            bool(vacating)
            and offset(vid) == 1
            and _weave_exit_prepare_abreast(x, _weave_veh(mod, ws, vid)["len"], vacating)
        )

    # --- settle the held vehicles ---------------------------------------
    for vid in sorted(active):
        st = active[vid]
        res = results.get(vid)
        if res is None:
            del active[vid]  # left the network before the section: nothing to restore
            continue
        road = res[tc.VAR_ROAD_ID]
        lane = int(res[tc.VAR_LANE_INDEX])
        if road.startswith(":"):
            continue  # on a junction between window edges: decided on the next edge
        off_v = offset(vid)
        if vid in target_ids:
            # in k_from on a window edge, or in section lane 1 when the
            # change and the crossing onto the section fell in one step
            ws["n_exit_prepared"] += 1
        elif off_v is not None and off_v > 0 and t < st["until_s"]:
            lane_to_here = spec[road][0]
            x_v = x_offset[road] + float(res[tc.VAR_LANEPOSITION])
            if beside_vacating(vid, x_v):
                # the vacate request goes first: the open request is
                # ended by a one-step stay while the pair is abreast
                if not st.get("suspended"):
                    st["suspended"] = True
                    mod.vehicle.changeLane(vid, lane, step_s)
                ws["n_exit_prepare_yielded"] += 1
            elif st.get("suspended") or lane_to_here != st["lane_to"]:
                # clear of the pair again, or crossed onto a window edge
                # where k_from has another index: re-issued for the rest
                # of its life
                st["suspended"] = False
                st["lane_to"] = lane_to_here
                mod.vehicle.changeLane(vid, lane_to_here, max(st["until_s"] - t, step_s))
            continue
        else:
            ws["n_exit_prepare_refused"] += 1
        if t < st["until_s"]:
            # on the section's edge the index k_from is the auxiliary lane:
            # the open request is ended by a one-step stay, so that the
            # section's own acceptance decides that change
            mod.vehicle.changeLane(vid, lane, step_s)
        mod.vehicle.setLaneChangeMode(vid, st["lc_mode_orig"])
        del active[vid]
    # --- the target lane's inflow to the window (the bound's flow) ----------
    flow_ids: set[str] = ws["prep_flow_ids"]
    flow_ids.intersection_update(results.keys())
    for x, vid in target:
        if x_lo <= x < x_start and vid not in flow_ids and offset(vid) == 0:
            flow_ids.add(vid)
            ws["prep_flow_s"].append(t)
    # --- the exiters left of the target lane in the window, nearest the
    # section first ------------------------------------------------------
    now: list[tuple[float, str, int]] = [
        (x, vid, off)
        for off, lst in by_off.items()
        if off > 0
        for x, vid in lst
        if x_lo <= x < x_start and vid in exiting and vid not in gave_up and offset(vid) == off
    ]
    # not asked last step and no longer in the window: skipped (state only),
    # unless the vehicle moved into the target lane by its own model
    pending: set[str] = ws["prep_pending"]
    for vid in sorted(pending - {vid for _, vid, _ in now}):
        if vid in results and offset(vid) != 0 and vid not in target_ids:
            ws["n_exit_prepare_skipped"] += 1
    pending.clear()
    if not now:
        ws["n_exit_prepare_held"] += len(active)
        return
    bound = _weave_vacate_bound_veh_h(ws, t, "prep_flow_s")
    asks_s: deque[float] = ws["prep_asks_s"]
    while asks_s and asks_s[0] <= t - VACATE_FLOW_WINDOW_S:
        asks_s.popleft()
    budget = int(bound * VACATE_FLOW_WINDOW_S / 3600.0) - len(asks_s)
    for x, vid, _off in sorted(now, key=lambda row: -row[0]):
        if vid in active or vid in seen:
            continue  # the request is open, or was made before (asked once)
        if budget <= 0:
            pending.add(vid)
            continue
        if beside_vacating(vid, x):
            pending.add(vid)  # asked once clear of the vacating vehicle
            continue
        # a pending veto's recorded mode, not the vetoed one (review
        # 2026-10-07, third regression review; _lc_mode_owned)
        mode_orig = _lc_mode_owned(mod, ws, vid)
        if mode_orig in (
            LC_MODE_SCRIPTED_SAFE,
            LC_MODE_SCRIPTED_FORCE,
            LC_MODE_SCRIPTED_SAFE_NO_ADAPT,
        ):
            continue  # under another scripted hold: not asked while it lasts
        seen.add(vid)
        asks_s.append(t)
        budget -= 1
        lane_to = spec[where[vid][0]][0]
        v = float(results[vid][tc.VAR_SPEED])
        duration = max((x_start - x) / max(v, SCRIPTED_MERGE_CREEP_MS), step_s)
        active[vid] = {
            "lc_mode_orig": mode_orig,
            "until_s": t + duration,
            "lane_to": lane_to,
        }
        mod.vehicle.setLaneChangeMode(vid, LC_MODE_SCRIPTED_SAFE)
        mod.vehicle.changeLane(vid, lane_to, duration)
        ws["n_exit_prepare_requests"] += 1
    ws["n_exit_prepare_held"] += len(active)


def _weave_choose_gap(
    vid: str,
    x_c: float,
    v_c: float,
    p_c: dict[str, float],
    v0_c: float,
    lane_list: Sequence[tuple[float, str]],
    x_of: dict[str, float],
    v_of: dict[str, float],
    p_of: dict[str, dict[str, float]],
    v0_of: dict[str, float],
    lookahead_m: float,
    committed: str | None,
    priority: bool = False,
    relaxed_T: Callable[[float, float, dict[str, float]], float] | None = None,
) -> tuple[str | None, str | None, float, float]:
    """The target-lane gap a changer works towards: ``(leader, follower, a_F, a_c)``.

    Candidates are the gaps between consecutive target-lane vehicles (plus the
    open road ahead of the first and behind the last) that the changer is in
    or abreast of: the follower F, if any, is behind the changer's rear bumper
    and within ``lookahead_m`` of it, and the leader L, if any, has its front
    bumper no farther back than the changer's rear (the changer overlaps L at
    most — it can drop in behind a vehicle beside it, not behind one it has
    passed). For each, ``a_F`` is the IDM acceleration F would need towards
    the changer as its leader (:func:`_idm_accel` with F's own parameters) and
    ``a_c`` the changer's towards L. A gap is *open* when ``a_F ≥ −b_F`` (F
    opens it at no more than its comfortable deceleration) and *enterable*
    when ``a_c ≥ −b_c`` as well. Ranking: enterable gaps first, then open
    ones, nearest follower first within a tier (the open road behind the
    rearmost vehicle counts as nearest). A ``committed`` follower's gap is
    kept while it is still a candidate and still open (2026-09-24, block 3:
    docs/WEAVE_MODEL_PLAN.md).

    Why nearest and not "least deceleration" within a tier: ranked by
    ``a_F`` alone the farthest follower always wins (it needs none), the
    changer then rides beside a platoon waiting for a gap that only arrives
    if the target lane is faster, and the change is made at the gore.

    **Exit priority** (``priority``, 2026-09-24 block 3, the exit-side
    derivation; docs/WEAVE_MODEL_PLAN.md dated paragraph): for an exit-bound
    changer whose forced change is due (``force_after_s`` after it entered
    the last ``force_within_m``; from zone entry instead it read 407 / 416 /
    402 of 466 on the entrance against 419 / 414 / 419, seeds 3–5, session
    record) the gap behind a target-lane vehicle *beside* it (L's front
    behind the changer's front but ahead of its rear) stays a candidate,
    with ``a_c = inf`` — the changer does not ease towards a vehicle it is
    ahead of, it waits for it to clear — where the abreast rule above
    discards it; at the gore's end the changer's front
    is ahead of every auxiliary-lane front, so without this it had no gap at
    all, held nobody, and the auxiliary lane streamed past it (fixture
    trace, seed 3 at the corridor's demand). Its follower F *holds*: the gap
    is open whatever F's IDM says (F brakes at no more than its ``b`` either
    way, :func:`_weave_command`), so the commitment is kept while F is
    behind the changer's rear; and F is driven towards a virtual leader one
    changer ``minGap`` behind the changer's rear (``s_F − s0_c``), so that
    it comes to rest ``s0_F + s0_c`` behind — a gap the forced guard
    (:func:`_weave_force_gap_ok`, ``> s0_c``) accepts, where IDM towards
    the rear itself stops at ``≈ s0_F`` and the guard refused for ever.

    Args:
        vid: The changer's id (tie-break of an exactly abreast pair).
        x_c: The changer's front-bumper position on the section axis [m].
        v_c: Its speed [m/s].
        p_c: Its constants (:func:`_weave_veh`).
        v0_c: Its desired speed on the target lane [m/s].
        lane_list: Target-lane vehicles as ``(x, id)``, ascending ``x``.
        x_of: Front-bumper position of every vehicle in ``lane_list`` [m].
        v_of: Their speeds [m/s].
        p_of: Their constants.
        v0_of: Their desired speeds [m/s].
        lookahead_m: How far behind the changer a follower may be [m].
        committed: The follower of the gap chosen on an earlier step, if any.
        priority: The exit priority of an exit-bound changer whose forced
            change is due (above).
        relaxed_T: The measured merge model's relaxed headway (2026-10-05,
            docs/MERGE_MODEL.md §2, "gap choice evaluated at the relaxed
            T"): ``(bumper gap, speed, constants) → T`` for F's IDM towards
            the changer and the changer's towards L; ``None`` (every weave
            call) reads each vehicle's own ``T``.

    Returns:
        ``(leader, follower, a_F, a_c)``; ``None`` where the gap has no such
        vehicle, ``a_F = inf`` without a follower, ``a_c = inf`` without a
        leader. ``(None, None, inf, inf)`` when no gap qualifies.
    """
    best: tuple[tuple[bool, bool, float], str | None, str | None, float, float] | None = None
    n = len(lane_list)
    for i in range(n + 1):
        l_id = lane_list[i][1] if i < n else None
        f_id = lane_list[i - 1][1] if i > 0 else None
        if f_id is not None:
            p_f = p_of[f_id]
            s_f = x_c - p_c["len"] - x_of[f_id]
            dist = x_c - x_of[f_id]
            if s_f <= 0.0 or dist > lookahead_m:
                continue
            s_ff = s_f - p_c["s0"] if priority else s_f
            a_f = _idm_accel(
                v_of[f_id],
                v0_of[f_id],
                # exit priority: F holds one changer minGap farther back
                s_ff,
                v_of[f_id] - v_c,
                p_f["T"] if relaxed_T is None else relaxed_T(s_ff, v_of[f_id], p_f),
                p_f["a"],
                p_f["b"],
                p_f["s0"],
            )
            open_ = priority or a_f >= -p_f["b"]
        else:
            a_f, dist, open_ = math.inf, 0.0, True
        if l_id is not None:
            if x_of[l_id] < x_c or (x_of[l_id] == x_c and l_id < vid):
                # L's front is behind the changer's own: not a gap the
                # changer is in. Strictly the front one of an abreast pair,
                # so only the rear one eases off (mutual easing stopped both)
                if not priority or x_of[l_id] <= x_c - p_c["len"]:
                    continue
                # exit priority: L is beside the changer (its front ahead of
                # the changer's rear); the gap behind L is the one the changer
                # drops into once L has cleared it, and its follower holds
                a_c = math.inf
            else:
                s_l = x_of[l_id] - p_of[l_id]["len"] - x_c
                a_c = _idm_accel(
                    v_c,
                    v0_c,
                    s_l,
                    v_c - v_of[l_id],
                    p_c["T"] if relaxed_T is None else relaxed_T(s_l, v_c, p_c),
                    p_c["a"],
                    p_c["b"],
                    p_c["s0"],
                )
        else:
            a_c = math.inf
        enterable = open_ and a_c >= -p_c["b"]
        if f_id is not None and f_id == committed and open_:
            return l_id, f_id, a_f, a_c
        key = (enterable, open_, -dist)
        if best is None or key > best[0]:
            best = (key, l_id, f_id, a_f, a_c)
    if best is None:
        return None, None, math.inf, math.inf
    return best[1], best[2], best[3], best[4]


def _weave_command(
    mod: Any,
    coop: dict[str, tuple[float, float, bool]],
    vid: str,
    v: float,
    v0: float,
    p: dict[str, float],
    a_target: float,
    step_s: float,
    follower: bool = True,
    close_leader: bool = False,
) -> bool:
    """Record a one-step speed target for a vehicle driven towards a virtual leader.

    ``a_target`` (the IDM acceleration towards the virtual leader) is clipped
    at the vehicle's comfortable deceleration ``−b`` and compared with the
    acceleration its own model would take this step — IDM towards its real
    leader (``vehicle.getLeader``) or the free-road term without one. Only a
    target *below* that is recorded in ``coop``; several requests on one
    vehicle keep the lowest speed. A higher target would be inert, not
    faster: under SUMO's default ``speedMode`` the influencer clamps a
    ``slowDown`` / ``setSpeed`` target at the vehicle's safe speed, which for
    the IDM is the model's own next speed (measured on 1.27.1, 2026-09-24
    block 3, seventh derivation: a target of ``v + 0.42`` m/s per step on an
    IDM vehicle near its desired speed left its acceleration at the model's
    0.10 m/s², and only ``speedMode`` 30 — the safety check off, which
    CLAUDE.md §3.3 forbids — gave ``a_max``). The one-step targets of this
    step are therefore ceilings: the runner can ask a vehicle to hold or
    drop back, never to open a gap ahead by a speed target. ``follower`` marks a target-lane follower
    opening a gap (counted in ``n_cooperations``) as opposed to a changer
    dropping in behind its gap's leader (``n_changer_eased``).

    A leader closer than the vehicle's ``minGap`` (``getLeader`` reports a
    negative gap) is read as a free road unless ``close_leader`` (amendment
    W2's ``weave_close_leader``, docs/I94_CAL_COLLISIONS.md §13; the AV
    path's ``observe_close_leader``, WP-96): then it is a leader at bumper gap
    ``max(reported gap + minGap, 0)`` (the IDM reads ``−inf`` at 0), so a
    vehicle that must brake harder than ``b`` is given no target — under the
    default ``speedMode`` a target caps its braking at ``decel`` (WP-95).

    Returns:
        True when the close-leader reading withheld a target that the
        free-road reading would have recorded (counted in
        ``n_close_leader_withheld``); always False without ``close_leader``.
    """
    a_cmd = max(a_target, -p["b"])
    lead = mod.vehicle.getLeader(vid, LEADER_LOOKAHEAD_M)
    withheld = False
    if close_leader and lead is not None and lead[0] != "" and lead[1] < 0.0:
        a_free = _idm_accel(v, v0, math.inf, 0.0, p["T"], p["a"], p["b"], p["s0"])
        a_own = _idm_accel(
            v,
            v0,
            max(float(lead[1]) + p["s0"], 0.0),
            v - float(mod.vehicle.getSpeed(lead[0])),
            p["T"],
            p["a"],
            p["b"],
            p["s0"],
        )
        withheld = a_cmd < a_free and not a_cmd < a_own
    elif lead is None or lead[0] == "" or lead[1] < 0.0:
        a_own = _idm_accel(v, v0, math.inf, 0.0, p["T"], p["a"], p["b"], p["s0"])
    else:
        a_own = _idm_accel(
            v,
            v0,
            float(lead[1]) + p["s0"],
            v - float(mod.vehicle.getSpeed(lead[0])),
            p["T"],
            p["a"],
            p["b"],
            p["s0"],
        )
    if a_cmd < a_own:
        v_new = max(v + a_cmd * step_s, 0.0)
        prev = coop.get(vid)
        if prev is None or v_new < prev[0]:
            coop[vid] = (v_new, a_cmd, follower if prev is None else prev[2] or follower)
    return withheld


def _weave_easing_ok(
    v_c: float, v_l: float, s_l: float, s_need: float, remaining_m: float, b_c: float
) -> bool:
    """Whether a changer can drop in behind its gap's leader by the section end at ≤ ``b``.

    Fourth derivation (2026-09-24, block 3; docs/WEAVE_MODEL_PLAN.md dated
    paragraph): easing is *positioning* — braking so that the changer's
    front clears the leader L's rear by the gap it needs before it runs out
    of section — and is asked for only when that positioning is feasible at
    the changer's comfortable deceleration. With the time available
    ``t_a = remaining / max(v_c, creep)`` (at the current speed, floored at
    ``SCRIPTED_MERGE_CREEP_MS``) and L holding its speed, a constant
    deceleration ``a`` over ``t_a`` moves the changer relative to L by
    ``(v_c − v_l)·t_a − a·t_a²/2``; the drop needed is ``d = s_need − s_l``
    (``≤ 0``: already clear), so ``a_req = 2·(d + (v_c − v_l)·t_a)/t_a²`` and
    easing is feasible iff ``a_req ≤ b_c``. Far from the section end the
    requirement is small and easing proceeds as in the second derivation; in
    the last tens of metres, where only a brake beyond ``b`` (or a stop
    beside L) would still position the changer, it keeps its own
    car-following speed and the change waits for its follower's cooperation
    or the forced mode.

    Fifth derivation (2026-09-24, block 3): easing is asked for only when it
    is *needed* as well — ``0 < a_req``. A leader that opens the gap by
    itself within the horizon (faster than the changer, or already clear by
    more than the accepted gap) gets no brake from the changer: the fourth
    derivation measured that brake as the head of the ramp queue (the
    entrant at the anticipation zone's entry braking for a lane-1 vehicle
    that is passing it anyway) and this condition as the one lead that moved
    the entrance criterion (docs/WEAVE_MODEL_PLAN.md, dated paragraphs).

    Args:
        v_c: The changer's speed [m/s].
        v_l: The gap leader's speed [m/s].
        s_l: Bumper-to-bumper gap from the changer's front to L's rear [m]
            (negative when they overlap).
        s_need: The gap the changer must clear L by [m] (its accepted gap,
            ``s0 + accept · v_c``).
        remaining_m: Section length still ahead of the changer [m].
        b_c: Its comfortable deceleration [m/s²].

    Returns:
        Whether the changer may be eased towards L this step: the drop is
        needed and feasible, ``0 < a_req ≤ b_c``.
    """
    t_a = remaining_m / max(v_c, SCRIPTED_MERGE_CREEP_MS)
    if t_a <= 0.0:
        return False
    d = s_need - s_l
    a_req = 2.0 * (d + (v_c - v_l) * t_a) / (t_a * t_a)
    return 0.0 < a_req <= b_c


def _weave_pair_release(
    mod: Any,
    ws: dict[str, Any],
    pending: dict[str, int],
    x_of: dict[str, float],
    v_of: dict[str, float],
    t: float,
) -> tuple[set[str], set[str]]:
    """Release a stopped changer–follower pair (fifth derivation, 2026-09-24 block 3).

    The state the "ease only when needed" condition reaches at seeds 4 and 5
    of ``weave_th52.osm`` (docs/WEAVE_MODEL_PLAN.md, dated paragraph): a
    changer X stopped at the end of its lane — an exiting vehicle at the
    gore in lane 1, held there by SUMO because lane 1 does not continue on
    its route — with the follower F of its committed gap standing bumper to
    bumper behind X's rear in the target lane, held there by X's own
    cooperation command (IDM towards X as a virtual leader at a zero gap,
    clipped at ``−b``, every step). The gap cannot open — F cannot back up
    and X cannot move on — and the change is refused for ever (the guard's
    ``s0`` floor, and SUMO's neighbour geometry reporting the pair
    overlapping). The pair the fourth derivation described, an entrant that
    is the exiter's cooperating follower with the exiter as its gap leader,
    is the same state with F a driven entrant; the abreast tie-break of
    :func:`_weave_choose_gap` does not cover it once both are stopped (a
    stopped changer's ``a_req`` is small and positive at any distance, so
    :func:`_weave_easing_ok` never releases it), and a release of the
    entrant–exiter pair alone was measured inert on the lock (session
    record).

    A *pair* is a driven changer X and the follower F of its committed gap
    (``veh[X]["target"]``, any vehicle) whose front is within one vehicle
    length — the longer of the two — behind X's rear on the section axis,
    with both below the creep speed ``SCRIPTED_MERGE_CREEP_MS``. A pair
    standing so for more than ``pair_release_s`` (``t − since >
    pair_release_s``, ``since`` the first step the condition held for that
    pair) is released: the one **farther from the section end** — more
    section length ahead of its front; ties, impossible here since F is
    behind X, by the vehicle id string with the greater one yielding —
    *yields* this step: it is given no speed target in either role, makes no
    request if it is itself driven, and its own gap commitment is dropped
    (re-chosen next step), so it moves on under its own car-following. The
    other is the released *partner*: it executes its change under the normal
    acceptance, or under the forced mode at once when it is within
    ``force_within_m``, guarded against closing only (:func:`_weave_step`);
    a changer at the lane end thus drops in behind the follower it had held.
    A vehicle that yields in one pair yields in every pair it is in.
    ``n_pair_releases`` counts each pair once per release (a pair that breaks
    up and stands again counts again).

    Args:
        mod: The libsumo / traci module (vehicle lengths, cached in ``ws``).
        ws: The section's state (``veh``, ``pair_since``, ``pair_released``,
            ``n_pair_releases``, ``params["pair_release_s"]``).
        pending: The vehicles driven this step and their direction.
        x_of: Front-bumper positions on the section axis [m].
        v_of: Speeds [m/s].
        t: Simulation time [s].

    Returns:
        ``(yielders, partners)``: the vehicles that yield this step and the
        released partners that may force their change (disjoint).
    """
    release_s = float(ws["params"]["pair_release_s"])
    veh: dict[str, dict[str, Any]] = ws["veh"]
    since: dict[tuple[str, str], float] = ws["pair_since"]
    released: set[tuple[str, str]] = ws["pair_released"]
    x_end = float(ws["x_offset"][ws["edges"][0]]) + float(sum(ws["lane_len_m"].values()))
    standing: set[tuple[str, str]] = set()
    yielders: set[str] = set()
    partners: set[str] = set()
    for x_id in sorted(pending):
        st = veh.get(x_id)
        f_id = st["target"] if st is not None else None
        if f_id is None or x_id not in x_of or f_id not in x_of:
            continue
        if v_of[x_id] >= SCRIPTED_MERGE_CREEP_MS or v_of[f_id] >= SCRIPTED_MERGE_CREEP_MS:
            continue
        len_x = _weave_veh(mod, ws, x_id)["len"]
        len_f = _weave_veh(mod, ws, f_id)["len"]
        gap = x_of[x_id] - len_x - x_of[f_id]
        if x_of[f_id] > x_of[x_id] or gap > max(len_x, len_f):
            continue
        key = (x_id, f_id)
        standing.add(key)
        first = since.setdefault(key, t)
        if t - first <= release_s:
            continue
        if key not in released:
            released.add(key)
            ws["n_pair_releases"] += 1
        # the one with more section ahead of its front yields; ties by id
        yielder = max((x_end - x_of[x_id], x_id), (x_end - x_of[f_id], f_id))[1]
        yielders.add(yielder)
        partners.add(f_id if yielder == x_id else x_id)
    for key in [k for k in since if k not in standing]:
        del since[key]
        released.discard(key)
    return yielders, partners - yielders


def _weave_change_ok(
    s0_c: float,
    accept_s: float,
    v_c: float,
    b_c: float,
    g_lead: float,
    v_lead: float,
    g_foll: float,
    v_foll: float,
    p_f: dict[str, float] | None,
    v0_f: float,
    accept_lag_s: float | None = None,
) -> bool:
    """The section's acceptance of one change read off given target-lane gaps.

    The acceptance of :func:`_weave_step` restated for gaps the caller
    supplies instead of ``getNeighbors``: the leader side at
    :func:`_weave_lead_gap_min`, the follower side at the movement's time
    gap and the follower absorbing the changer within its ``b`` (its IDM
    acceleration towards the changer, gap = reported gap + its ``minGap``),
    and :func:`_weave_force_gap_ok` with both brake gaps. Gaps are SUMO's
    reported ones — the changer's ``minGap`` excluded on the leader side,
    the follower's on the follower side; ``inf`` / ``nan`` without a
    vehicle there. The oracle of the offline restatement in
    ``calibration.lane_change_gaps`` (its tests read every case against
    it). The follower side, and the guard's follower side, read
    ``accept_lag_s`` when given (WP-80; the runner's follower-side keys were
    removed on 2026-10-06, the restatement keeps the split).

    Args:
        s0_c: The changer's ``minGap`` [m].
        accept_s: The movement's accepted time gap [s] — its leader side.
        v_c: The changer's speed [m/s].
        b_c: Its comfortable deceleration [m/s²].
        g_lead: Reported gap to the target-lane leader [m].
        v_lead: Its speed [m/s].
        g_foll: Reported gap to the target-lane follower [m].
        v_foll: Its speed [m/s].
        p_f: The follower's constants (:func:`_weave_veh`), ``None`` without one.
        v0_f: The follower's desired speed [m/s].
        accept_lag_s: The movement's follower-side time gap [s]; ``None``
            uses ``accept_s``.

    Returns:
        Whether the change is accepted.
    """
    lag_s = accept_s if accept_lag_s is None else accept_lag_s
    ok_lead = g_lead >= _weave_lead_gap_min(s0_c, accept_s, v_c, g_lead, v_lead, b_c)
    ok_foll = g_foll >= s0_c + lag_s * (v_foll if g_foll < math.inf else 0.0)
    b_f = None
    if p_f is not None:
        b_f = p_f["b"]
        if ok_foll:
            a_i = _idm_accel(
                v_foll,
                v0_f,
                g_foll + p_f["s0"],
                v_foll - v_c,
                p_f["T"],
                p_f["a"],
                p_f["b"],
                p_f["s0"],
            )
            ok_foll = a_i >= -p_f["b"]
    return (
        ok_lead
        and ok_foll
        and _weave_force_gap_ok(
            s0_c, accept_s, v_c, g_lead, v_lead, g_foll, v_foll, b_c, b_f, accept_lag_s=lag_s
        )
    )


def _weave_exec_change(
    mod: Any, vid: str, st: dict[str, Any], target: int, step_s: float, t: float, kind: str
) -> None:
    """Request a weave change for one step under ``LC_MODE_SCRIPTED_FORCE`` (mode 256).

    The execution of :func:`_weave_step`, unchanged: an accepted change
    (``kind`` ``"acc"``) is executed under mode 256 for one step — the
    follower yields, SUMO still refuses an immediate collision;
    under mode 512 SUMO refused the change while the follower was closing from
    far back and braked the changer to drop in behind it. A forced change
    (``kind`` ``"force"``) is requested under the gaps just checked by the
    forced guard and marks the vehicle forced. A request SUMO refuses (an
    overlap) stays open into the next step, where the vehicle is back under
    ``LC_MODE_SCRIPTED_SAFE`` unless asked again, so it then executes only
    under SUMO's own gap check (WP-92: 2 entries into lane 1 one step late in 5
    runs of the corridor section fixture).
    """
    _weave_set_mode(mod, vid, st, LC_MODE_SCRIPTED_FORCE)
    mod.vehicle.changeLane(vid, target, step_s)
    st["requested_s"] = t
    if kind == "force":
        st["forced"] = True


def _weave_opposing_restore(mod: Any, ws: dict[str, Any], results: Any) -> None:
    """Give the vehicles vetoed last step their lane-change mode back (WP-92).

    The measured model's opposing-entry resolution
    (:func:`merge_model.resolve_opposing`, :func:`_measured_step`) clears a
    vehicle's model-driven bits for one step; before anything of the next
    step reads a mode, each vetoed vehicle
    still in the network gets the mode it had — unless another rule has set
    one since, which is then left as it is. A veto another rule consumed
    when it took the vehicle into a hold (:func:`_lc_mode_owned`; review
    2026-10-07, third regression review) is no longer listed: that rule
    restores the mode.
    """
    vetoes: dict[str, int] = ws["opp_veto"]
    for vid, mode in vetoes.items():
        if vid in results and int(mod.vehicle.getLaneChangeMode(vid)) == mode & ~LC_MODE_MODEL_BITS:
            mod.vehicle.setLaneChangeMode(vid, mode)
    vetoes.clear()


def _weave_share_vetoes(
    states: Sequence[dict[str, Any]], readers: Sequence[dict[str, Any]] = ()
) -> None:
    """Let every section's mode capture see every section's vetoes (review 2026-10-07, third regression review).

    Each weaving section and measured zone (``states``) keeps its own
    ``opp_veto``, restored by its own :func:`_weave_opposing_restore` at the
    start of its next step; all of them are listed in one ``opp_veto_all``,
    the same list object on every state and on each of ``readers`` (the
    scripted merges, which veto nothing), which :func:`_lc_mode_owned`
    reads. One dict shared by every state was not taken: a section stepped
    later in the step would restore, at its own start, a veto an upstream
    section made earlier in that step, before SUMO stepped, and no veto
    would act.
    """
    shared = [ws["opp_veto"] for ws in states]
    for st in (*states, *readers):
        st["opp_veto_all"] = shared


def _lc_mode_owned(mod: Any, ws: dict[str, Any], vid: str) -> int:
    """``vid``'s own ``laneChangeMode``, read by a rule about to hold it (review 2026-10-07, third regression review).

    A vehicle an opposing-entry resolution vetoed
    (:func:`_weave_resolve_and_execute`, :func:`_measured_step`) is under
    its mode with the model bits cleared until that section restores it at
    the start of its next step. A hold of another section taken in between
    (the vacate request, the exiters' early move, a vehicle taken under
    control, the measured hand-over, a scripted merge's acceleration lane)
    read the vetoed mode as the vehicle's own; the restore then found the
    hold's mode, left it and dropped the record, and the hold handed the
    vetoed mode back at its end — the vehicle's strategic, cooperative,
    speed-gain and keep-right changes off for the rest of the run. A veto
    pending on ``vid`` in any section (``ws["opp_veto_all"]``, also on a
    scripted merge's state; :func:`_weave_share_vetoes`) is consumed here
    and its recorded mode returned, with no TraCI read: the capturing rule
    owns the restoration from now on. Otherwise the live mode, one read, as
    the capture sites made it before. A recorded mode always carries model
    bits (only a ``model`` opponent is vetoed), so a capture site's
    scripted-hold guard never skips a vehicle whose veto was consumed.
    """
    for vetoes in ws.get("opp_veto_all", ()):
        if vid in vetoes:
            return int(vetoes.pop(vid))
    return int(mod.vehicle.getLaneChangeMode(vid))


def _weave_cooperate(
    mod: Any,
    tc: Any,
    ws: dict[str, Any],
    results: Any,
    lanes: dict[int, list[tuple[float, str]]],
    x_of: dict[str, float],
    v_of: dict[str, float],
    p_of: dict[str, dict[str, float]],
    v0_of: dict[str, float],
    coop: dict[str, tuple[float, float, bool]],
    vid: str,
    target_lane: int,
    committed: str | None,
    accept_s: float,
    remaining_m: float,
    priority: bool = False,
) -> str | None:
    """Choose a changer's gap on ``target_lane`` and record the two speed targets.

    :func:`_weave_choose_gap` over the lane's listing, then
    :func:`_weave_command` for the gap's follower (the changer as its virtual
    leader) and, when the changer would have to brake for the gap's leader,
    for the changer itself (L as its virtual leader — an abreast pair
    resolves by the one behind easing off, not by a station-keeping cap).
    ``p_of`` / ``v0_of`` are filled for the listed vehicles as needed.

    Fourth derivation (2026-09-24, block 3), the entrant side: the changer
    is eased only when (a) it is abreast of or ahead of the gap's follower —
    guaranteed by the candidate set of :func:`_weave_choose_gap` (F's front
    is behind the changer's rear) — and (b) dropping in behind the gap's
    leader by the section end takes no more than its comfortable
    deceleration (:func:`_weave_easing_ok`, the gap it needs being its
    accepted gap ``s0 + accept_s · v``, ``remaining_m`` the section still
    ahead — for an entrant on the ramp, the ramp to the gore plus the
    section); otherwise it keeps its own car-following speed and the change
    waits for the follower's cooperation (with consecutive-vehicle gaps the
    changer is in at most one, so there is no "next gap" to move to). Two
    entrant-side rules were measured on ``weave_th52.osm`` and rejected
    (docs/WEAVE_MODEL_PLAN.md, dated paragraph): no easing of an entrant
    upstream of the gore turned the ramp queue into a 3–5 m/s crawl of lane
    1 (the easing on the ramp is what positions the entrant before it
    appears beside its gap), and no cooperation from a ramp vehicle for an
    exit-bound changer locked the section at seed 4 (the entrant's yielding
    before the gore is what resolves the crossing pair). Both stay as the
    second derivation had them.

    Sixth derivation (2026-09-24, block 3), the ramp's throttle: an entrant
    still on the ramp is **not** eased towards a gap leader that overlaps it
    — L's rear behind the entrant's front, ``s_l < 0``. The per-step trace on
    ``weave_th52.osm`` read the entrance bound as the ramp's own queue
    (4–4.5 m/s over its first 100 m at 75 veh/km, 3.1-s headways = 1,150
    veh/h against 1,400 of demand, insertion refused behind it; the IDM's
    own queue discharge is 2.2–2.3 s) and the queue's head as this case:
    34–38 % of the eased ramp steps had the gap leader overlapping the
    entrant (63–70 % within 5 m), the entrant 0.15–0.55 m/s faster, and the
    IDM term against that leader at −19 to −41 m/s² clipped to −b — a
    positioning that needs hundredths of a m/s² (``a_req``) taken as a full
    comfortable brake, ~2.5 m/s per event, then 8 s of recovery at the IDM's
    0.2–0.4 m/s² with the platoon behind following. A vehicle beside the
    entrant is not a leader the car-following model can follow (its gap term
    is undefined at ``s ≤ 0``), and on the ramp the entrant has the whole
    section ahead to drop in behind it or pass it, so it keeps its own
    car-following speed. On the section the same geometry still eases (the
    rear one of an abreast pair drops back at −b — removing that there locks
    the section by minute 4, session record), and a leader just clear of the
    entrant (``0 ≤ s_l < s0``) is still followed on the ramp: the ``s0``
    boundary was measured noisier across seeds (395 / 401 / 423 against
    414 / 412 / 407 of 466 at seeds 3–5). The cost moves to lane 1 — the
    pass completes there and the follower absorbs at −b — which reads as
    one to three minutes of lane 1 at 2–5 m/s at some seeds
    (docs/WEAVE_MODEL_PLAN.md, dated paragraph, has the table).

    Seventh derivation (2026-09-24, block 3), **measured and rejected**: a
    symmetric resolution of the abreast entrant–lane-1 pair at speed parity
    (each side adjusting by half the offset the pair needs over a horizon
    ``pair_tau_s``, the rear one braking at ≤ ``b/2``, the front one let open
    ahead by its own model at half its headway, since a speed target above
    the model is inert — :func:`_weave_command`). In every form measured
    (the offset re-read each step or the relative speed fixed at entry,
    horizons 1.5–6 s, with and without the headway concession, each
    geometry alone, the ``−b`` easing kept beside it; 15 variants, seeds
    3–8) it departs fewer entrants than this rule on average and locks the
    section at some seed where this rule does not (docs/WEAVE_MODEL_PLAN.md,
    dated paragraph, has the table). The ``−b`` resolution of the abreast
    pair on the section stays.

    Exit-side derivation (2026-09-24, block 3): ``priority`` is the exit
    priority of an exit-bound changer whose forced change is due
    (:func:`_weave_choose_gap`): the auxiliary-lane vehicles behind its rear
    hold while it drops in, and a vehicle beside it is waited for, not eased
    towards.

    The bounded hold (WP-58), the gated anticipation (WP-60), the ramp's
    outlet (WP-70) and the anticipation sparing the exiters (WP-75), all off
    by default, were removed on 2026-10-06 (docs/WEAVE_MODEL_PLAN.md;
    reproduce with release 2.5.0).

    Returns:
        The chosen gap's follower id (the commitment carried to the next
        step), or ``None``.
    """
    prm = ws["params"]
    res = results[vid]
    road = res[tc.VAR_ROAD_ID]
    v_c = float(res[tc.VAR_SPEED])
    p_c = _weave_veh(mod, ws, vid)
    # the target lane's limit: on the ramp, that of the section's first edge
    v_road = road if road in ws["edge_index"] else ws["edges"][0]
    v0_c = min(p_c["vmax"], _weave_lane_vmax(mod, ws, v_road, target_lane))
    lane_list = lanes.get(target_lane, [])
    for _x, oid in lane_list:
        if oid not in p_of:
            p_of[oid] = _weave_veh(mod, ws, oid)
            r_o = results[oid]
            v0_of[oid] = min(
                p_of[oid]["vmax"],
                _weave_lane_vmax(mod, ws, r_o[tc.VAR_ROAD_ID], int(r_o[tc.VAR_LANE_INDEX])),
            )
    l_t, f_t, a_f, a_c = _weave_choose_gap(
        vid,
        x_of[vid],
        v_c,
        p_c,
        v0_c,
        lane_list,
        x_of,
        v_of,
        p_of,
        v0_of,
        prm["lookahead_m"],
        committed,
        priority,
    )
    step_s = float(ws["step_s"])
    # amendment W2 (weave_close_leader, off unless set): a leader inside minGap
    # is a leader, not a free road, in the targets' own-acceleration estimate
    close = _weave_switch(prm, "weave_close_leader")
    if f_t is not None:
        if _weave_command(
            mod, coop, f_t, v_of[f_t], v0_of[f_t], p_of[f_t], a_f, step_s, close_leader=close
        ):
            ws["n_close_leader_withheld"] += 1
    if l_t is not None and a_c < 0.0:
        s_l = x_of[l_t] - p_of[l_t]["len"] - x_of[vid]
        # sixth derivation: on the ramp, a gap leader whose rear is behind the
        # entrant's front is beside it, not ahead of it
        beside = road in ws["ramp_edges"] and s_l < 0.0
        if not beside and _weave_easing_ok(
            v_c, v_of[l_t], s_l, p_c["s0"] + accept_s * v_c, remaining_m, p_c["b"]
        ):
            if _weave_command(
                mod, coop, vid, v_c, v0_c, p_c, a_c, step_s, follower=False, close_leader=close
            ):
                ws["n_close_leader_withheld"] += 1
    return f_t


def _weave_short_section_rule(length_m: float, prm: dict[str, float]) -> dict[str, float | bool]:
    """The forced zone and its delay of a section, and whether the section is *short*.

    Short sections (2026-09-24, block 3; docs/CONTRACTS.md §2 "short
    sections", docs/WEAVE_MODEL_PLAN.md dated section). The fixed
    ``force_within_m`` / ``force_after_s`` assume a cooperative stretch
    before the forced zone in which a changer and a target-lane follower
    can open one accepted gap at no more than the follower's ``b`` — from
    abreast, ``sqrt(2·(s0 + accept·v)/b)`` ≈ 3.6 s and ≈ 55 m at 15 m/s with
    the fleet defaults; a section is **short** when it has less than one
    zone length of such stretch, ``length_m < 2 · force_within_m`` (160 m at
    the defaults). ``short`` is reported in ``meta.json`` (``short_section``)
    so a battery can name such sections; ``zone_m`` and ``force_after_s``
    are the values :func:`_weave_step` runs on.

    **No scaling ships** — every value returned is the fixed one, on every
    section. The scalings the task named were derived and measured on the
    136 m Ruth St twin (``tests/fixtures/weave_ruth.osm``, the corridor's
    fleet at the C-D split's exit peak, ``test_microsim_weave_short_section.py``;
    macOS record, seeds 3–5 unless stated), against a baseline that gives
    up 8 / 3 / 4 of 290 / 281 / 274 exits with lane 1's last 60 m at
    3.3–4.3 m/s: (a) the forced zone as the whole section with the delay
    kept (2 / 4 / 3 given up) or at 2 s (4 / 2 / 2, 29 collisions at seed
    3) or 3.4 s (5 / 5 / 2); (b) the whole section with a one-step delay
    (1 / 2 / 0, lane 1 above 5.7 m/s — but 1 / 4 / 1 collisions at seeds 8,
    14 and 15 of thirteen, forced changes at speed in the auxiliary lane's
    first 30 m); (c) with a zero delay, 1 / 5 / 1 collisions on the arrival
    step; (d) the exit priority alone from the section's start (6 / 3 / 3;
    it removes the exiter's early easing), from the start after 1 s
    (3 / 3 / 3) or 4 s (9 / 3 / 6, 3 collisions at seed 3), or for both
    movements (7 / 2 / 2). The mechanism they all meet is an abreast
    crossing pair at the gore's end — an exiter halted at the end of lane 1
    beside an entrant halted at the end of the auxiliary lane, each
    refused on the other's overlap — which forms because the 56 m before
    the zone (3.5 s at the section's 16.6 m/s) is shorter than one
    cooperative gap opening, and which neither an earlier last resort
    (unsafe at speed under mode 256) nor an earlier priority (the pair
    waits for each other) resolves; a brake beyond ``b`` cannot be
    commanded under the default ``speedMode`` (CLAUDE.md §3.3).

    Args:
        length_m: The section's driven length (the sum of its edges) [m].
        prm: The section's ``weave_params`` with the defaults applied.

    Returns:
        ``short`` (whether the section is shorter than the fixed defaults
        fit), ``zone_m`` (the forced zone, ``force_within_m``) and
        ``force_after_s`` (its delay).
    """
    zone = float(prm["force_within_m"])
    return {
        "short": float(length_m) < 2.0 * zone,
        "zone_m": zone,
        "force_after_s": float(prm["force_after_s"]),
    }


def _weave_switch(prm: Mapping[str, float], key: str) -> bool:
    """Whether a weave switch of amendment W2 is on (docs/I94_CAL_COLLISIONS.md §13).

    ``weave_handback``, ``weave_close_leader``, ``weave_resolve_opposing``
    (``flowstate_core.config.WEAVE_W2_SWITCHES``): keys with no default, ``1``
    on, ``0`` or unset off (the schema refuses any other value).
    """
    return bool(prm.get(key, 0.0))


def _weave_handback_needed(mod: Any, ws: dict[str, Any], vid: str, v: float, step_s: float) -> bool:
    """Amendment W2's ``weave_handback``: whether a weave speed target must be withheld this step.

    :func:`_handback_needed` (the AV path's ``emergency_handback`` test,
    WP-95) with the vehicle's :func:`_command_decel` under the run's fleet
    model (``ws["cf_model"]``; ``decel`` for EIDM), cached per vehicle in
    ``ws["hb_decel"]``: the vehicle's own model must brake behind its real
    leader harder than a commanded vehicle can, so a one-step target would
    cap its braking at that bound.
    """
    cache: dict[str, float] = ws["hb_decel"]
    b_cmd = cache.get(vid)
    if b_cmd is None:
        b_cmd = cache[vid] = _command_decel(
            str(ws["cf_model"]),
            float(mod.vehicle.getDecel(vid)),
            float(mod.vehicle.getEmergencyDecel(vid)),
        )
    return _handback_needed(mod, vid, v, b_cmd, step_s)


def _weave_entrant_giveup_m(prm: Mapping[str, float]) -> float | None:
    """The entering give-up's distance (amendment W1, ``entrant_giveup_m``) [m].

    ``None`` when the rule is off: the key unset (it has no default,
    ``flowstate_core.config.WEAVE_OPTIONAL_KEYS``) or ``0``.
    """
    value = prm.get("entrant_giveup_m")
    return float(value) if value is not None and value > 0.0 else None


#: Tolerance of amendment W1b's dwell comparison [s]: simulated times are sums
#: of the step length, so a dwell of a whole number of steps is reached on the
#: step that completes it, not one step later through a rounding residue.
_WEAVE_DWELL_EPS_S: Final[float] = 1e-6


def _weave_entrant_giveup_dwell_s(prm: Mapping[str, float]) -> float | None:
    """The entering give-up's dwell (amendment W1b, ``entrant_giveup_dwell_s``) [s].

    ``None`` when W1 gives up at once: the key unset (no default,
    ``flowstate_core.config.WEAVE_OPTIONAL_KEYS``) or ``0``. The schema refuses
    a positive dwell without ``entrant_giveup_m``.
    """
    value = prm.get("entrant_giveup_dwell_s")
    return float(value) if value is not None and value > 0.0 else None


def _weave_step(
    mod: Any,
    tc: Any,
    ws: dict[str, Any],
    results: Any,
    t: float,
    index: _RoadIndex | None = None,
) -> None:
    """One step of a weaving section (``RampSpec.merge = "weave"``).

    :func:`_scripted_merge_step` generalised to the two crossing movements of
    a one-sided ramp weave (HCM 7th ed. ch. 13; docs/WEAVE_MODEL_PLAN.md
    §2(A)), rewritten 2026-09-24 (block 3, second attempt) around
    **anticipatory follower cooperation as a car-following target**. A
    vehicle on a section edge is driven when it has a change to make there:

    * **entering** (direction +1): a vehicle not bound for the paired exit on
      lane 0 of an edge whose lane 0 leads only to the exit changes left;
    * **exiting** (direction -1): a vehicle bound for the paired exit on lane
      1 or above changes right, one lane per request.

    Either movement is **forced** after ``force_after_s`` inside the last
    ``force_within_m`` before the exit gore (``LC_MODE_SCRIPTED_FORCE``: the
    follower yields, SUMO still refusing collisions) as its last resort — an
    exit missed is a route missed, and an entrant held at the lane end brakes
    its cooperating follower down to a standstill with it — but only through
    the minimum-gap guard :func:`_weave_force_gap_ok`; a refused forced change
    is deferred to the next step and counted in ``n_forced_deferred``.

    **No desired-speed caps.** A driven vehicle's ``maxSpeed`` is never
    touched: it follows its lane by SUMO's own car-following under
    ``LC_MODE_SCRIPTED_SAFE`` (every model-driven change off). The speed
    matching, the station-keeping cap and the exchange rule of the first
    attempt are gone — a max-speed cap below the current speed is an
    emergency brake to IDM's free term and, applied through ``getNeighbors``
    with no floor, stopped a through lane (docs/WEAVE_MODEL_PLAN.md, block 3
    trace). Every speed request below is a one-step car-following target
    (:func:`_weave_command`).

    **Gap choice** (:func:`_weave_choose_gap`): each step, until committed,
    the changer picks among the target-lane gaps it is in or abreast of,
    within ``lookahead_m`` behind it, the one its follower F can open at no
    more than F's own comfortable deceleration ``b_F`` (nearest such gap
    first, gaps the changer can also follow into preferred); the committed
    gap is kept while F still opens it within ``b_F``, else re-chosen.
    Target-lane vehicles are listed from the section's edges, the through
    lanes of the corridor edge before and after it and the on-ramp's lane
    (``lane_map``, :func:`_weave_lane_map`), on the section's linear axis
    ``x_offset`` (negative on the ramp).

    **Cooperation.** F is driven towards the changer as a *virtual leader*:
    ``a_F`` is F's IDM acceleration to a leader at the changer's position
    (gap ``x_c − length_c − x_F``, ``Δv = v_F − v_c``, F's own ``tau``,
    ``accel``, ``decel``, ``minGap``, ``maxSpeed``), clipped at ``−b_F``;
    when it is below F's own acceleration (IDM towards F's real leader,
    ``vehicle.getLeader``) the speed ``v_F + a·Δt`` is set with
    ``vehicle.slowDown(F, v, 0.0)`` — a one-step target: with duration 0
    SUMO's influencer reaches the target on the next step and hands F back
    to its model on the one after (a duration of one step lingers two steps,
    half-way then full, measured on SUMO 1.27.1), and under the default
    ``speedMode`` the target is clamped to F's safe speed and its ``decel``,
    so a collision or a brake beyond ``b_F`` cannot be commanded. Several
    changers on one follower take the lowest target. Through vehicles are
    touched only as followers of a chosen gap; a driven vehicle may be one.
    Cooperation begins when the changer is within ``lookahead_m`` of the gap
    and ends on the change (nothing to restore). Counted in
    ``n_cooperations`` (vehicle-steps) and ``mean_follower_decel_ms2``.

    **Easing.** The changer gets the same one-step target towards its gap's
    leader L when it would have to brake for L (``a_c < 0``, clipped at its
    own ``b``; ``n_changer_eased``): a vehicle abreast of L drops in behind
    it. Only the rear one of an abreast pair has such a gap — the front one
    takes no gap whose leader's front is behind its own — so the pair
    resolves by the rear one easing off; with both easing they braked each
    other to a standstill (fixture trace, t = 55–70 s), and without easing
    the pair rode abreast into the gore, where the follower tracking the
    stopped entrant stopped too (33,876 deferred forced changes, 56 driven
    vehicles unfinished). Fourth derivation (2026-09-24, block 3): the
    easing is asked for only while it can still position the changer —
    when dropping in behind L by the section end needs no more than the
    changer's ``b`` (:func:`_weave_easing_ok`); in the last metres, where
    only a stop beside L would, the changer keeps its own speed and the
    change waits for the follower or the forced mode. Measured on
    ``weave_th52.osm`` at seeds 3–5 the check binds rarely and its effect
    is within seed noise; the entrant-side rules it was derived with (no
    easing on the ramp, no ramp follower for an exiter) were measured and
    rejected (:func:`_weave_cooperate`).

    **Anticipation on the ramp.** An entering vehicle still on the on-ramp
    within ``lookahead_m`` of the section chooses its gap, its follower
    cooperates and it eases before it appears on lane 0 (``pre``, carried
    into ``target`` on arrival); nothing else is done to it there. Without
    this a through vehicle arriving at 28.7 m/s met an entrant appearing at
    19 m/s 7 m ahead of it and could not open the gap in time (fixture
    trace, t = 20–45 s).

    **Vacating the weave lane** (2026-09-24, block 3, third derivation;
    :func:`_weave_vacate_step`). A through vehicle in the lane feeding
    section lane 1, within ``vacate_ahead_m`` of the section start along the
    corridor, is asked once to move one lane left — the signage /
    anticipation that empties the weave lane for the exchange — under mode
    512 (SUMO's safety check, the vehicle adapting its speed), within the
    target lane's spare capacity (``vacate_max_veh_h``). Counted in
    ``n_vacated``, ``n_vacate_refused``, ``n_vacate_skipped_no_gap`` and
    ``n_vacate_requests``.

    **The exit side** (2026-09-24, block 3, exit-side derivation; derived
    from the I-94 WB standstill at the T.H.52 gore's end, docs/WEAVE_MODEL_PLAN.md
    dated paragraph). An exit-bound vehicle whose forced change is due has
    *priority* over the auxiliary lane (:func:`_weave_choose_gap`): the gap
    behind the vehicle beside it is its, its follower holds one changer
    ``minGap`` farther back than IDM would stop, and the commitment is kept
    while the follower is behind its rear. One that has come to a halt
    (``HALTING_SPEED_MS``) still owing its change within ``exit_giveup_m`` of
    the gore's end, with no change to request this step (neither the
    accepted gaps nor the forced guard pass — review, 2026-09-24 block 3),
    has missed the exit: it is rerouted through (``vehicle.changeTarget``
    to the corridor's last edge — its original destination, the paired exit,
    is dropped and it drives the mainline to the corridor's end), handed
    back at once and counted in ``n_missed`` and ``n_missed_exit`` — never
    held by SUMO at the end of a lane its route does not continue on, where
    it stopped the through lane behind it and the auxiliary lane beside it.

    **The entering give-up** (2026-10-07, amendment W1 of
    docs/WEAVE_LOSS_DIAGNOSIS.md §6.2; ``entrant_giveup_m``, a key of
    ``WEAVE_OPTIONAL_KEYS``, unset = off). Its mirror: an entrant still owing
    its change into section lane 1 that has come to a halt
    (``HALTING_SPEED_MS``) within ``entrant_giveup_m`` of the auxiliary
    lane's end (the exit gore), with no change to request this step (neither
    the accepted gaps nor the forced guard pass), takes the paired exit: it
    is rerouted (``vehicle.changeTarget`` to the off-ramp's last edge,
    ``exit_target``), handed back at once, never taken under control again
    (``took_exit``) and counted in ``n_missed`` and ``n_entrant_took_exit``
    — instead of standing at the end of the exit-only lane, where every
    exit-bound vehicle behind it stops (seed 4 of the T.H.52 section test:
    nine, for 50 s). Off, nothing of it runs and nothing is written.

    **The dwell** (2026-10-07, amendment W1b of docs/WEAVE_LOSS_DIAGNOSIS.md
    §10; ``entrant_giveup_dwell_s``, a key of ``WEAVE_OPTIONAL_KEYS``, unset =
    W1 at once). With it, the entering give-up waits: each such entrant's
    clock (``halt_since`` in its state) starts on its first step halted
    within ``entrant_giveup_m`` of the end and resets on any step it is at or
    above the halting speed or farther back — uninterrupted standstill at the
    lane's end, whatever was requested meanwhile — and the entrant takes the
    exit only on a step on which the clock has run ``entrant_giveup_dwell_s``
    and W1's own condition holds. A lock at a weaving gore stands for hours
    (docs/I94_COLLAPSE_DIAGNOSIS.md); the section test's ordinary stands, all
    self-clearing, at most 50.5 s. Unset, nothing of it runs.

    **The collision guards** (2026-10-07, amendment W2 of
    docs/I94_CAL_COLLISIONS.md §13; three switches of ``WEAVE_OPTIONAL_KEYS``,
    unset = off, nothing of them runs). ``weave_handback``: a one-step target
    is withheld in any step where the vehicle's own model must brake harder
    than a commanded vehicle can (:func:`_weave_handback_needed`; a target
    caps braking at ``decel``, WP-95), counted in ``n_handback_skips``.
    ``weave_close_leader``: :func:`_weave_command` reads a leader inside
    ``minGap`` as a leader (``n_close_leader_withheld``). ``weave_resolve_opposing``:
    the step's change requests are collected and, before they execute,
    resolved with :func:`merge_model.resolve_opposing` as the measured model
    does — two entries into one lane from both sides in one step, the loser
    waiting a step (a request withheld, or an undriven vehicle's model
    change vetoed for the step and restored by :func:`_weave_opposing_restore`
    at the start of the next), counted in ``n_opposing_deferred`` (both) and
    ``n_opposing_vetoed``.

    **The exiters' early move** (2026-09-24, block 3, WP-62;
    :func:`_weave_exit_prepare_step`, ``exit_prepare``, off by default).
    The vacate rule's mirror: a vehicle bound for the paired exit in a lane
    left of the one feeding section lane 1, inside the vacate window, is
    asked once into that lane under the vacate rule's own terms, the
    vacate request going first where the two meet abreast; counted in
    ``n_exit_prepared``. Measured and left off: the corridor section test
    reads no better and the fixture grid's entrances fall
    (docs/WEAVE_MODEL_PLAN.md, dated section).

    The section's other switches — the bounded give-up patiences (WP-52,
    WP-53), the yields at the lane ends (WP-54..56), the entrant's entry
    speed (WP-57), the bounded hold (WP-58), the gated anticipation (WP-60),
    the swap (WP-64), the crossings spread (WP-67), the ramp's outlet
    (WP-70), the exit priority from the braking onset (WP-73), the
    anticipation sparing the exiters (WP-75), the follower-side time gaps
    (WP-80) and the opposing-entry guard (WP-92), all off or unset by
    default — were removed on 2026-10-06 (docs/WEAVE_MODEL_PLAN.md;
    reproduce with release 2.5.0).

    **Acceptance and execution.** The change is executed under mode 256 for
    one step as soon as the immediate target-lane gaps (``getNeighbors``)
    clear ``s0 + accept · v`` (``accept_gap_s`` / ``exit_accept_gap_s``) —
    the leader side, for both movements, also the changer's brake gap on
    that leader, ``s0 + (v − v_L)⁺²/(2·b)`` at its own ``decel``
    (:func:`_weave_brake_gap` through :func:`_weave_lead_gap_min`;
    2026-09-24 block 3, speed-aware acceptance: a fast exiter no longer
    drops in behind a queue head it cannot brake for at ``b``) — the
    immediate follower can absorb the changer within its ``b`` (its IDM
    acceleration towards the changer, gap = reported gap + its ``minGap``)
    and :func:`_weave_force_gap_ok` passes; a forced change uses the guard
    alone, whose two sides carry the brake gaps of the changer and of the
    follower. A step with no request leaves the vehicle on mode 512. Control is handed back (mode restored) when the
    vehicle has no change left to make;
    a driven vehicle on an internal junction lane between two section pieces
    (``OSMNetwork.internal_links``) is neither driven nor handed back that
    step. Bookkeeping lands in ``ws`` for ``meta.json``.

    **Exit bookkeeping** (2026-09-24, review finding). An exit-bound vehicle
    (``exiting_ids``) is added to ``reached`` the first step it is seen on a
    section edge — ``n_reached_section_exiting``, the denominator that
    excludes vehicles still upstream when the run ends — and to ``exited``
    (``n_exited``, each vehicle once) when it is seen on **any** edge of the
    paired off-ramp (``exit_edges``, the ramp's ``RampSpec.edges``), or when,
    having reached the section, it is gone from the network while last seen
    on a section edge or on an internal junction lane after one. The second
    rule is what makes the count robust: a per-step sighting on the ramp
    misses a ramp edge — or a whole ramp — shorter than one step of travel
    (12.5 m at 25 m/s and 0.5 s), whereas an exit-bound vehicle can leave the
    network from the section only by driving its route to the ramp's end
    (teleporting is off, ``--time-to-teleport -1``, and ``collision.action
    warn`` removes nobody). A reached vehicle next seen on any other named
    edge left by the mainline (possible only under a reroute) and is never
    counted; whichever way it leaves, it is dropped from ``awaiting_exit`` so
    the per-step check stays bounded by the vehicles at the section.
    """
    prm = ws["params"]
    edges: dict[str, int] = ws["edge_index"]
    exiting: frozenset[str] = ws["exiting_ids"]
    exit_edges: frozenset[str] = ws["exit_edges"]
    lane_map: dict[tuple[str, int], int] = ws["lane_map"]
    x_offset: dict[str, float] = ws["x_offset"]
    ramp_edges: frozenset[str] = ws["ramp_edges"]
    x_start = x_offset[ws["edges"][0]]
    section_len = float(sum(ws["lane_len_m"].values()))
    # the forced zone and its delay (_weave_short_section_rule; the fixed
    # values — no short-section scaling ships)
    rule = ws.get("rule") or _weave_short_section_rule(section_len, prm)
    step_s = float(ws["step_s"])
    veh = ws["veh"]
    # amendment W1 (the entering give-up; None = off) and the entrants it
    # rerouted to the exit, never driven again
    entrant_giveup_m = _weave_entrant_giveup_m(prm)
    # amendment W1b (its dwell; None = W1 gives up at once)
    entrant_dwell_s = _weave_entrant_giveup_dwell_s(prm) if entrant_giveup_m is not None else None
    took_exit: Collection[str] = ws.get("took_exit", ())
    # amendment W2's switches (docs/I94_CAL_COLLISIONS.md §13; off unless set)
    handback = _weave_switch(prm, "weave_handback")
    resolve = _weave_switch(prm, "weave_resolve_opposing")
    if resolve and ws["opp_veto"]:
        # the model changes vetoed last step get their mode back before
        # anything of this step reads a mode
        _weave_opposing_restore(mod, ws, results)
    # with weave_resolve_opposing, this step's change requests and their
    # (target lane, kind), executed after the loop once resolved
    requests: dict[str, merge_model.ChangeRequest] = {}
    req_exec: dict[str, tuple[int, str]] = {}
    pending: dict[str, int] = {}
    # entering vehicles still on the ramp within lookahead_m of the section:
    # gap choice and cooperation only (see the docstring, anticipation)
    approaching: set[str] = set()
    # driven vehicles on an internal junction lane this step (a section of
    # several pieces under ``OSMNetwork.internal_links``): still under
    # control, decided again on the next edge — handing them back here would
    # book a miss and re-enter them with their timers reset
    in_transit: set[str] = set()
    # exit-bound vehicles that reached the section and have not been counted
    # as exited yet (see the docstring, exit bookkeeping)
    awaiting_exit: set[str] = ws["awaiting_exit"]
    for vid in list(awaiting_exit):
        res_a = results.get(vid)
        if res_a is None:
            # gone from the network after the section: it drove the ramp to
            # its end within the step (a ramp shorter than a step of travel)
            ws["exited"].add(vid)
            awaiting_exit.discard(vid)
            continue
        road_a = res_a[tc.VAR_ROAD_ID]
        if road_a in exit_edges:
            ws["exited"].add(vid)
            awaiting_exit.discard(vid)
        elif road_a not in edges and not road_a.startswith(":"):
            awaiting_exit.discard(vid)  # left by the mainline: never counted
    # target-lane listings on the section axis (see _weave_lane_map)
    lanes: dict[int, list[tuple[float, str]]] = {}
    x_of: dict[str, float] = {}
    v_of: dict[str, float] = {}
    if index is not None:
        # the pass below over the section's roads only (_RoadIndex); a driven
        # vehicle on an internal junction lane is the one thing it would have
        # read off them (_zone_scan_roads)
        scan_roads = _zone_scan_roads(ws)
        for vid in veh:
            res_t = results.get(vid)
            if res_t is not None:
                road_t = res_t[tc.VAR_ROAD_ID]
                if road_t not in scan_roads and road_t.startswith(":"):
                    in_transit.add(vid)
    for vid, res in results.items() if index is None else index.on(scan_roads):
        road = res[tc.VAR_ROAD_ID]
        if road in exit_edges:
            if vid in exiting and vid not in ws["exited"]:
                # never sighted on the section (a section shorter than a
                # step of travel): still an exit, and it did reach the section
                ws["reached"].add(vid)
                ws["exited"].add(vid)
            continue
        lane = int(res[tc.VAR_LANE_INDEX])
        k = lane_map.get((road, lane))
        if k is not None:
            x = x_offset[road] + float(res[tc.VAR_LANEPOSITION])
            x_of[vid] = x
            v_of[vid] = float(res[tc.VAR_SPEED])
            lanes.setdefault(k, []).append((x, vid))
            if road in ramp_edges and vid not in exiting and x >= x_start - prm["lookahead_m"]:
                approaching.add(vid)
        if road not in edges:
            if vid in veh and road.startswith(":"):
                in_transit.add(vid)
            continue
        if vid in exiting:
            if vid not in ws["exited"] and vid not in ws["reached"]:
                ws["reached"].add(vid)
                awaiting_exit.add(vid)
            if lane >= 1 and vid not in ws["gave_up"]:
                pending[vid] = -1
        elif lane == 0 and ws["exit_only"][road] and vid not in took_exit:
            pending[vid] = 1
    for lst in lanes.values():
        lst.sort()
    # through traffic vacates the weave lane upstream of the section (third
    # derivation, 2026-09-24 block 3): the only rule touching through vehicles
    # other than as a chosen gap's follower
    _weave_vacate_step(mod, tc, ws, results, lanes, t, index)
    # its mirror for the exit movement (WP-62, ``exit_prepare``): exiters
    # asked into the lane feeding section lane 1 before the section, after
    # the vacate rule and before the section takes any vehicle under control
    _weave_exit_prepare_step(mod, tc, ws, results, lanes, t, index)
    for vid in [v for v in veh if v not in pending and v not in in_transit]:
        st = veh.pop(vid)
        if vid not in results:
            ws["n_missed"] += 1  # left the network while still owing a change
            continue
        mod.vehicle.setLaneChangeMode(vid, st["lc_mode_orig"])
        road = results[vid][tc.VAR_ROAD_ID]
        lane = int(results[vid][tc.VAR_LANE_INDEX])
        if st["dir"] < 0:
            done = road in exit_edges or (road in edges and lane == 0)
        else:
            done = road not in exit_edges
        if not done:
            ws["n_missed"] += 1
            continue
        ws["n_changed_out" if st["dir"] < 0 else "n_changed_in"] += 1
        ws["n_forced"] += int(st["forced"])
        ws["waits_out_s" if st["dir"] < 0 else "waits_in_s"].append(t - st["entered_s"])
    # a stopped crossing pair is released (fifth derivation, 2026-09-24 block
    # 3): the one farther from the section end yields this step, the other
    # may force its change at once
    yielders, released = _weave_pair_release(mod, ws, pending, x_of, v_of, t)
    # vehicle id -> (speed target, commanded acceleration, is a follower) this step
    coop: dict[str, tuple[float, float, bool]] = {}
    p_of: dict[str, dict[str, float]] = {}
    v0_of: dict[str, float] = {}
    for vid in sorted(pending):
        d = pending[vid]
        st = veh.get(vid)
        if st is None:
            st = veh[vid] = {
                "dir": d,
                "entered_s": t,
                "zone_s": None,
                "requested_s": -math.inf,
                "forced": False,
                # a pending veto's recorded mode, not the vetoed one (review
                # 2026-10-07, third regression review; _lc_mode_owned)
                "lc_mode_orig": _lc_mode_owned(mod, ws, vid),
                "s0": float(mod.vehicle.getMinGap(vid)),
                "mode": LC_MODE_SCRIPTED_SAFE,
                # the gap chosen on the ramp, if any, carries over
                "target": ws["pre"].pop(vid, None),
            }
            mod.vehicle.setLaneChangeMode(vid, LC_MODE_SCRIPTED_SAFE)
            ws["n_entered"] += 1
        res = results[vid]
        road = res[tc.VAR_ROAD_ID]
        lane = int(res[tc.VAR_LANE_INDEX])
        v_ego = float(res[tc.VAR_SPEED])
        # distance to the exit gore (the end of the section's last edge)
        remaining = ws["lane_len_m"][road] - float(res[tc.VAR_LANEPOSITION]) + ws["beyond_m"][road]
        if d > 0:
            modes = (NEIGHBOR_LEFT_LEADERS, NEIGHBOR_LEFT_FOLLOWERS)
            accept = prm["accept_gap_s"]
        else:
            modes = (NEIGHBOR_RIGHT_LEADERS, NEIGHBOR_RIGHT_FOLLOWERS)
            accept = prm["exit_accept_gap_s"]
        if remaining <= rule["zone_m"] and st["zone_s"] is None:
            st["zone_s"] = t
        # the forced change is due (the forced zone's exit priority)
        zone_due = st["zone_s"] is not None and t - st["zone_s"] >= rule["force_after_s"]
        # --- acceptance (read before the give-up below: a halted exiter that
        # can still request its change this step is not given up — review,
        # 2026-09-24 block 3: with the give-up first, one halted 3 m from the
        # end with both gaps clear was rerouted where 8 m from the end the
        # same gaps were accepted at once) ---------------------------------
        g_lead, v_lead, _l_id = _neighbor_gap(mod, vid, modes[0])
        g_foll, v_foll, f_id = _neighbor_gap(mod, vid, modes[1])
        # the leader side reads the leader's speed as well (2026-09-24, block
        # 3, speed-aware acceptance): the changer's brake gap on that leader
        # at its own b, floored by the movement's time gap
        b_c = _weave_veh(mod, ws, vid)["b"]
        ok_lead = g_lead >= _weave_lead_gap_min(st["s0"], accept, v_ego, g_lead, v_lead, b_c)
        ok_foll = g_foll >= st["s0"] + accept * (v_foll if g_foll < math.inf else 0.0)
        b_f = _weave_veh(mod, ws, f_id)["b"] if f_id is not None else None
        if ok_foll and f_id is not None:
            # the immediate follower must absorb the changer within its own b
            p_i = _weave_veh(mod, ws, f_id)
            r_i = results.get(f_id)
            v0_i = (
                min(
                    p_i["vmax"],
                    _weave_lane_vmax(mod, ws, r_i[tc.VAR_ROAD_ID], int(r_i[tc.VAR_LANE_INDEX])),
                )
                if r_i is not None
                else p_i["vmax"]
            )
            a_i = _idm_accel(
                v_foll,
                v0_i,
                g_foll + p_i["s0"],
                v_foll - v_ego,
                p_i["T"],
                p_i["a"],
                p_i["b"],
                p_i["s0"],
            )
            ok_foll = a_i >= -p_i["b"]
        # the forced change is the last resort of both movements: an exit
        # missed is a route missed, an entrant held at the lane end brakes
        # its cooperating follower down to a standstill with it. A released
        # partner within the zone forces at once
        force = st["zone_s"] is not None and (
            t - st["zone_s"] >= rule["force_after_s"] or vid in released
        )
        accepted = (
            ok_lead
            and ok_foll
            and _weave_force_gap_ok(
                st["s0"], accept, v_ego, g_lead, v_lead, g_foll, v_foll, b_c, b_f
            )
        )
        # a released partner is guarded against closing only: both of the
        # pair are below the creep speed, the s0 floor is a comfort margin
        # at speed, and mode 256 still refuses an overlap — and, since the
        # speed-aware guard, against a follower that cannot brake for it at b
        s0_guard = 0.0 if vid in released else st["s0"]
        forced_ok = force and _weave_force_gap_ok(
            s0_guard, accept, v_ego, g_lead, v_lead, g_foll, v_foll, b_c, b_f
        )
        if (
            d < 0
            and remaining <= prm["exit_giveup_m"]
            and v_ego < HALTING_SPEED_MS
            and not (accepted or forced_ok)
        ):
            # the exit is missed: a vehicle halted within exit_giveup_m of
            # the gore's end with no change to request this step continues
            # on the mainline (rerouted to the corridor's end) instead of
            # being held by SUMO at the end of a lane its route does not
            # continue on, where it stopped the through lane behind it and
            # the auxiliary lane beside it (exit-side derivation, 2026-09-24
            # block 3). One still rolling there may yet drop in: v00010 of
            # the moderate fixture forced its change in the last 3 m at 2-3
            # m/s (session record)
            mod.vehicle.changeTarget(vid, ws["through_target"])
            mod.vehicle.setLaneChangeMode(vid, st["lc_mode_orig"])
            del veh[vid]
            ws["gave_up"].add(vid)
            awaiting_exit.discard(vid)
            ws["n_missed"] += 1
            ws["n_missed_exit"] += 1
            continue
        if d > 0 and entrant_dwell_s is not None and entrant_giveup_m is not None:
            # amendment W1b's clock: uninterrupted standstill within
            # entrant_giveup_m of the auxiliary lane's end (a requested change
            # that executes ends the stand anyway); reset on any other step
            if remaining <= entrant_giveup_m and v_ego < HALTING_SPEED_MS:
                if st.get("halt_since") is None:
                    st["halt_since"] = t
            else:
                st["halt_since"] = None
        if (
            d > 0
            and entrant_giveup_m is not None
            and remaining <= entrant_giveup_m
            and v_ego < HALTING_SPEED_MS
            and not (accepted or forced_ok)
            # W1b: only once the entrant has stood there for the dwell
            and (
                entrant_dwell_s is None
                or t - st["halt_since"] >= entrant_dwell_s - _WEAVE_DWELL_EPS_S
            )
        ):
            # amendment W1, the entering give-up: an entrant halted within
            # entrant_giveup_m of the auxiliary lane's end with no change to
            # request this step takes the paired exit (rerouted to the
            # off-ramp's last edge; its lane already leads there) instead of
            # standing at the end of the exit-only lane with every exit-bound
            # vehicle behind it stopped (docs/WEAVE_LOSS_DIAGNOSIS.md §3.8,
            # §6.2). Recorded with the rerouted vehicles (``gave_up``, for
            # VEHICLES_FILE) and never taken under control again
            mod.vehicle.changeTarget(vid, ws["exit_target"])
            mod.vehicle.setLaneChangeMode(vid, st["lc_mode_orig"])
            del veh[vid]
            ws["took_exit"].add(vid)
            ws["gave_up"].add(vid)
            ws["n_missed"] += 1
            ws["n_entrant_took_exit"] += 1
            continue
        if vid in yielders:
            # yields to its released partner: no target, no request, the
            # gap commitment dropped (re-chosen next step)
            st["target"] = None
            _weave_set_mode(mod, vid, st, LC_MODE_SCRIPTED_SAFE)
            continue
        # --- gap choice and cooperation ------------------------------------
        st["target"] = _weave_cooperate(
            mod,
            tc,
            ws,
            results,
            lanes,
            x_of,
            v_of,
            p_of,
            v0_of,
            coop,
            vid,
            lane + d,
            st["target"],
            accept,
            remaining,
            # exit priority once the forced change is due (_weave_choose_gap)
            d < 0 and zone_due,
        )
        # --- execution -----------------------------------------------------
        if (accepted or (force and forced_ok)) and resolve:
            # amendment W2: collected, resolved against opposing entries after
            # the loop, then executed (or withheld for a step)
            requests[vid] = merge_model.ChangeRequest(
                vid=vid,
                x=x_of[vid],
                lane=lane,
                target=lane + d,
                due=force,
                accept_s=accept,
                v=v_ego,
                s0=st["s0"],
                b=b_c,
            )
            req_exec[vid] = (lane + d, "acc" if accepted else "force")
        elif accepted or (force and forced_ok):
            # accepted: executed under mode 256 for one step (the follower
            # yields; SUMO still refuses an immediate collision). A forced
            # request lives one step only, so it is executed under the gaps
            # just checked or not at all (_weave_exec_change)
            _weave_exec_change(mod, vid, st, lane + d, step_s, t, "acc" if accepted else "force")
        elif force:
            # deferred: back under SUMO's own safety check, so a pending
            # request cannot execute into the gap that was just refused
            _weave_set_mode(mod, vid, st, LC_MODE_SCRIPTED_SAFE)
            ws["n_forced_deferred"] += 1
        else:
            # no request this step: never leave a one-step forced mode standing
            _weave_set_mode(mod, vid, st, LC_MODE_SCRIPTED_SAFE)
    if requests:
        _weave_resolve_and_execute(mod, tc, ws, results, lanes, v_of, requests, req_exec, t)
    # entering vehicles still on the ramp: their gap is chosen and its
    # follower cooperates before they appear on lane 0
    pre: dict[str, str | None] = ws["pre"]
    for vid in [v for v in pre if v not in approaching]:
        del pre[vid]
    for vid in sorted(approaching):
        # the ramp to the gore, then the whole section
        dist_m = x_start - x_of[vid] + section_len
        pre[vid] = _weave_cooperate(
            mod,
            tc,
            ws,
            results,
            lanes,
            x_of,
            v_of,
            p_of,
            v0_of,
            coop,
            vid,
            1,
            pre.get(vid),
            prm["accept_gap_s"],
            dist_m,
        )
    for vid in yielders:
        # no target in either role this step: a yielder moves on under its own
        # car-following
        coop.pop(vid, None)
    for fid in sorted(coop):
        v_new, a_cmd, follower = coop[fid]
        if handback and _weave_handback_needed(
            mod, ws, fid, float(results[fid][tc.VAR_SPEED]), step_s
        ):
            # amendment W2: its own model must brake harder than a target
            # lets it this step; the target is withheld, not capped
            ws["n_handback_skips"] += 1
            continue
        mod.vehicle.slowDown(fid, v_new, 0.0)
        if follower:
            ws["n_cooperations"] += 1
            ws["coop_decel_sum"] += -a_cmd
        else:
            ws["n_changer_eased"] += 1


def _weave_resolve_and_execute(
    mod: Any,
    tc: Any,
    ws: dict[str, Any],
    results: Any,
    lanes: dict[int, list[tuple[float, str]]],
    v_of: dict[str, float],
    requests: dict[str, merge_model.ChangeRequest],
    req_exec: dict[str, tuple[int, str]],
    t: float,
) -> None:
    """Amendment W2's ``weave_resolve_opposing``: resolve a step's weave changes, then execute them.

    :func:`merge_model.resolve_opposing`, unchanged (the measured model's
    always-on rule, WP-92), on the step's requests and the section's lane
    listings of the pre-step state (``lanes``, front-bumper ``x``; lengths
    from :func:`_weave_veh`). An opponent that is not itself requesting is
    ``open`` when it is driven, asked last step into a lane it has not
    reached yet (that request may still execute this step, B§5.6); ``driven``
    when driven otherwise (its model bits are cleared, it cannot change);
    ``model`` when undriven with model bits (vetoable for one step); ``held``
    when undriven without (its change cannot be read: the request waits).
    A withheld request is left under ``LC_MODE_SCRIPTED_SAFE`` with no new
    request; a vetoed vehicle's model bits are cleared for the step and its
    mode recorded in ``ws["opp_veto"]`` for :func:`_weave_opposing_restore`.
    Each executed request is recorded (``opp_req``: time, target lane) for the
    ``open`` reading of the next step.
    """
    veh: dict[str, dict[str, Any]] = ws["veh"]
    lane_map: dict[tuple[str, int], int] = ws["lane_map"]
    step_s = float(ws["step_s"])

    def state_of(pid: str) -> merge_model.OpponentState:
        st_p = veh.get(pid)
        if st_p is not None:
            req = st_p.get("opp_req")
            res_p = results.get(pid)
            if req is not None and res_p is not None and req[0] >= t - step_s - _WEAVE_DWELL_EPS_S:
                k_p = lane_map.get((res_p[tc.VAR_ROAD_ID], int(res_p[tc.VAR_LANE_INDEX])))
                if k_p is not None and k_p != req[1]:
                    return "open"
            return "driven"
        mode = int(mod.vehicle.getLaneChangeMode(pid))
        return "model" if mode & LC_MODE_MODEL_BITS else "held"

    opp = {
        k: [merge_model.LaneVehicle(o, x, _weave_veh(mod, ws, o)["len"], v_of[o]) for x, o in lst]
        for k, lst in lanes.items()
    }
    withheld, vetoed = merge_model.resolve_opposing(list(requests.values()), opp, state_of)
    for vid in sorted(requests):
        st = veh[vid]
        target, kind = req_exec[vid]
        if vid in withheld:
            _weave_set_mode(mod, vid, st, LC_MODE_SCRIPTED_SAFE)
            ws["n_opposing_deferred"] += 1
            continue
        _weave_exec_change(mod, vid, st, target, step_s, t, kind)
        st["opp_req"] = (t, target)
    for pid in sorted(vetoed):
        mode = int(mod.vehicle.getLaneChangeMode(pid))
        mod.vehicle.setLaneChangeMode(pid, mode & ~LC_MODE_MODEL_BITS)
        ws["opp_veto"][pid] = mode
        ws["n_opposing_deferred"] += 1
        ws["n_opposing_vetoed"] += 1


def _weave_meta(ws: dict[str, Any], n_departed_by_route: dict[str, int]) -> dict[str, Any]:
    """``meta.json["weave_sections"]`` entry of one weaving section.

    ``n_entered`` counts vehicles taken under control (both movements);
    each leaves control as ``n_changed_in`` (entering, now off the auxiliary
    lane), ``n_changed_out`` (exiting, now on the auxiliary lane or the exit),
    ``n_missed`` (left the section, or the network, still owing its change) or
    is still under control at the end of the run (``n_unfinished``).
    ``n_forced`` counts completed changes that needed the forced mode
    (exiting movement only); ``n_forced_deferred`` counts vehicle-steps on
    which a due forced change was refused by the minimum-gap guard
    (:func:`_weave_force_gap_ok`); ``n_cooperations`` counts vehicle-steps on
    which a target-lane follower was given a speed target for a changer and
    ``mean_follower_decel_ms2`` the mean commanded deceleration over them
    (``None`` without any; positive is braking); ``n_changer_eased`` counts
    vehicle-steps on which a changer was given a speed target towards its
    gap's leader; ``n_vacated`` / ``n_vacate_refused`` count through
    vehicles asked to vacate the weave lane upstream of the section
    (:func:`_weave_vacate_step`) that did / did not change before it, each
    once, ``n_vacate_skipped_no_gap`` the through vehicles that crossed the
    window never asked — for want of the bound — each once, and
    ``n_vacate_requests`` the requests made, in vehicle-steps;
    ``n_pair_releases`` counts stopped crossing pairs released
    (:func:`_weave_pair_release`), each pair once per release;
    ``n_missed_exit`` (exit-side derivation) the exit-bound vehicles the
    runner itself rerouted through at the gore's end, halted (below
    ``HALTING_SPEED_MS``) still owing their change with no more than
    ``exit_giveup_m`` of section ahead — a subset of ``n_missed``, so the
    identity above holds; ``n_entrant_took_exit`` (amendment W1, written
    only when ``entrant_giveup_m`` is on, right after ``n_missed_exit``) the
    entrants the runner itself rerouted to the paired exit at the auxiliary
    lane's end, halted still owing their change with no more than
    ``entrant_giveup_m`` of section ahead (with ``entrant_giveup_dwell_s``,
    amendment W1b, only after standing there that long) — also a subset of
    ``n_missed``; amendment W2's counters (docs/I94_CAL_COLLISIONS.md §13),
    each written only while its switch is on, right after it:
    ``n_handback_skips`` (``weave_handback``: vehicle-steps on which a speed
    target was withheld because the vehicle's own model had to brake harder),
    ``n_close_leader_withheld`` (``weave_close_leader``: target requests the
    free-road reading of a leader inside ``minGap`` would have recorded),
    ``n_opposing_deferred`` and ``n_opposing_vetoed``
    (``weave_resolve_opposing``: requests withheld plus model changes vetoed
    for opposing entries, and the vetoes alone);
    ``n_exit_prepared`` (WP-62, the exiters' early
    move) the vehicles bound for the paired exit that the rule asked, inside
    the vacate window, into the lane feeding section lane 1 and that were
    seen there before the section, each once (:func:`_weave_exit_prepare_step`;
    zero at ``exit_prepare`` = 0). The counters of the switches removed on
    2026-10-06 (``n_giveup_waited``, ``n_exiter_yields``,
    ``n_entrant_yields``, ``n_entry_bounded``, ``n_hold_releases``,
    ``n_anticipation_gated``, ``n_swaps``, ``n_spread_withheld``,
    ``n_outlet_spared``, ``n_onset_priority``,
    ``n_anticipation_exiter_spared``, ``n_opposing_deferred``) are no longer
    written (docs/WEAVE_MODEL_PLAN.md; release 2.5.0 wrote them).
    ``n_exited``
    is the number of exit-bound
    vehicles that took the paired exit (seen on any of its edges, or gone from
    the network after the section — :func:`_weave_step`, exit bookkeeping),
    each once; ``n_reached_section_exiting`` the exit-bound vehicles that
    entered the section during the run, its denominator; and
    ``n_departed_exiting`` the departed vehicles routed through the exit,
    including those still upstream of the section when the run ends (so
    ``n_exited <= n_reached_section_exiting <= n_departed_exiting``, and the
    last two agree only once demand has drained through the section).
    """
    waits = ws["waits_in_s"] + ws["waits_out_s"]
    rule = ws.get("rule") or _weave_short_section_rule(
        float(sum(ws["lane_len_m"].values())), ws["params"]
    )

    def _mean(xs: list[float]) -> float | None:
        return float(np.mean(xs)) if xs else None

    return {
        "ramp": ws["ramp"],
        "exit": ws["exit"],
        "edges": ws["edges"],
        "exit_edge": ws["exit_edge"],
        "exit_edges": sorted(ws["exit_edges"]),
        "length_m": ws["length_m"] if ws["length_m"] is not None else ws["length_m_measured"],
        "length_m_measured": ws["length_m_measured"],
        "params": dict(ws["params"]),
        # short sections: shorter than the fixed forced zone's defaults fit
        # (_weave_short_section_rule; no scaling ships, the values are the fixed ones)
        "short_section": bool(rule["short"]),
        # the corridor edges the vacate window reaches, nearest the section
        # first (the cross-edge window, 2026-09-24 block 3; empty = inert)
        "vacate_window_edges": list(ws["vacate_lanes"]),
        "n_entered": ws["n_entered"],
        "n_changed_in": ws["n_changed_in"],
        "n_changed_out": ws["n_changed_out"],
        "n_forced": ws["n_forced"],
        "n_missed": ws["n_missed"],
        "n_missed_exit": ws["n_missed_exit"],
        # amendment W1: absent when the rule is off, so such a meta is as before
        **(
            {"n_entrant_took_exit": ws["n_entrant_took_exit"]}
            if _weave_entrant_giveup_m(ws["params"]) is not None
            else {}
        ),
        # amendment W2's counters: each absent while its switch is off
        **(
            {"n_handback_skips": ws["n_handback_skips"]}
            if _weave_switch(ws["params"], "weave_handback")
            else {}
        ),
        **(
            {"n_close_leader_withheld": ws["n_close_leader_withheld"]}
            if _weave_switch(ws["params"], "weave_close_leader")
            else {}
        ),
        **(
            {
                "n_opposing_deferred": ws["n_opposing_deferred"],
                "n_opposing_vetoed": ws["n_opposing_vetoed"],
            }
            if _weave_switch(ws["params"], "weave_resolve_opposing")
            else {}
        ),
        "n_exit_prepared": ws["n_exit_prepared"],
        "n_forced_deferred": ws["n_forced_deferred"],
        "n_cooperations": ws["n_cooperations"],
        "mean_follower_decel_ms2": (
            ws["coop_decel_sum"] / ws["n_cooperations"] if ws["n_cooperations"] else None
        ),
        "n_changer_eased": ws["n_changer_eased"],
        "n_vacated": ws["n_vacated"],
        "n_vacate_refused": ws["n_vacate_refused"],
        "n_vacate_skipped_no_gap": ws["n_vacate_skipped_no_gap"],
        "n_vacate_requests": ws["n_vacate_requests"],
        "n_pair_releases": ws["n_pair_releases"],
        "n_unfinished": len(ws["veh"]),
        "n_exited": len(ws["exited"]),
        "n_reached_section_exiting": len(ws["reached"]),
        "n_departed_exiting": sum(
            n for rid, n in n_departed_by_route.items() if _route_exit(rid) == ws["off_index"]
        ),
        "wait_s_mean": _mean(waits),
        "wait_in_s_mean": _mean(ws["waits_in_s"]),
        "wait_out_s_mean": _mean(ws["waits_out_s"]),
    }


# --- The measured merge model (RampSpec.merge = "measured", 2026-10-05) -------
#
# docs/MERGE_MODEL.md (the specification) and microsim.merge_model (its pure
# functions). A measured zone is a section state of the weave's layout
# (``_measured_zone_state``) carrying ``ws["mm"]``; it is stepped by
# ``_measured_step`` in the same upstream-first order as the weaving sections
# and reuses the weave's load-bearing core — the gap choice, the one-step
# cooperation of the chosen follower, the easing, the ramp anticipation, the
# pair release, the exit priority and give-up, the vacate request and the
# exiters' early move — with the three measured substitutions: the
# acceptance, the speed ceiling and the relaxation. The run-level state
# (``_measured_run_state``) holds the parameters, the driver draws, the shared
# per-vehicle constants and the relaxations.

#: Seconds after a vehicle was last commanded by a measured zone within which
#: a collision it is party to is attributed to that zone (meta.json
#: ``measured_merges[i].n_collisions_attributable``; a bookkeeping window, not
#: a model value).
MEASURED_ATTRIBUTION_S: Final[float] = 10.0

#: Lane-connection hops within which a relaxed vehicle's new lane still
#: continues its last one (:func:`_lane_continues`). One hop is the next edge;
#: more cover an edge shorter than one step of travel (≈ 15–20 m at a 0.5 s
#: step on a freeway), which the vehicle passes over between two readings. A
#: bookkeeping bound, not a model value: a lane change can only reach a lane
#: these hops reach where the lane connections themselves fan out, which a
#: direct successor already allows.
RELAX_CONTINUITY_HOPS: Final[int] = 3


def _measured_run_state(
    params: merge_model.MergeModelParams,
    plan: FleetPlan,
    av_ids: Collection[str],
    succ: Mapping[tuple[str, int], frozenset[tuple[str, int]]],
    step_s: float,
    via_from: Mapping[tuple[str, int], tuple[str, int]] | None = None,
) -> dict[str, Any]:
    """The run-level state of the measured merge model (one per run).

    ``succ`` is :func:`_lane_successors` and ``via_from``
    :func:`_internal_lane_origins` of the compiled network (empty without
    internal links).
    """
    return {
        "params": params,
        "z_lead": plan.merge_z_lead,
        "z_lag": plan.merge_z_lag,
        "gaps": {},
        "veh_params": {},
        "av_ids": frozenset(av_ids),
        "succ": succ,
        "via_from": dict(via_from or {}),
        "step_s": float(step_s),
        "relax": {},
        "relax_where": {},
        "relax_leader": {},
        "where": {},
        "n_relax_granted_entrant": 0,
        "n_relax_granted_follower": 0,
        "n_relax_regranted": 0,
        "n_relax_restored_expired": 0,
        "n_relax_restored_lane_change": 0,
        "n_relax_restored_left": 0,
        "n_relaxed_cut_ins": 0,
        "min_tau_set_s": None,
        "n_tau_writes": 0,
        # AVs whose command was withdrawn while a zone owns their change
        "av_released": set(),
        "n_av_released": 0,
    }


def _mm_veh(mod: Any, run: dict[str, Any], vid: str) -> dict[str, float]:
    """A vehicle's constants for the measured model, read once per run (shared by every zone).

    The weave's :func:`_weave_veh` fields — ``len``, ``T`` (the **current**
    headway: the relaxation writes it with every ``setTau``), ``a``, ``b``,
    ``s0``, ``vmax`` (``maxSpeed`` at first sight, before any ceiling) — plus
    ``sf`` (``vehicle.getSpeedFactor``: B§5.4, the desired speed is
    ``min(maxSpeed, sf × lane limit)``), ``T0`` (the driver's own ``T_i``) and
    ``av`` (1.0 for an AV-tagged vehicle, exempt from relaxation). A
    measured zone's ``veh_params`` is this same dict, so the weave helpers it
    reuses read the same constants.
    """
    cache: dict[str, dict[str, float]] = run["veh_params"]
    p = cache.get(vid)
    if p is None:
        p = cache[vid] = {
            "len": float(mod.vehicle.getLength(vid)),
            "T": float(mod.vehicle.getTau(vid)),
            "a": float(mod.vehicle.getAccel(vid)),
            "b": float(mod.vehicle.getDecel(vid)),
            "s0": float(mod.vehicle.getMinGap(vid)),
            "vmax": float(mod.vehicle.getMaxSpeed(vid)),
        }
    if "sf" not in p:
        p["sf"] = float(mod.vehicle.getSpeedFactor(vid))
        p["T0"] = p["T"]
        p["av"] = 1.0 if vid in run["av_ids"] else 0.0
    return p


def _mm_v0(p: Mapping[str, float], lane_vmax: float) -> float:
    """Desired speed on a lane, speed factor honoured (:func:`merge_model.desired_speed`)."""
    return merge_model.desired_speed(p["sf"], lane_vmax, p["vmax"])


def _mm_driver(run: dict[str, Any], vid: str) -> merge_model.DriverGaps:
    """A driver's critical gaps (``FleetPlan.merge_z_lead`` / ``merge_z_lag`` through the set)."""
    gaps: dict[str, merge_model.DriverGaps] = run["gaps"]
    g = gaps.get(vid)
    if g is None:
        i = int(vid[1:])
        g = gaps[vid] = merge_model.driver_gaps(
            run["params"], float(run["z_lead"][i]), float(run["z_lag"][i])
        )
    return g


def _mm_relaxed_fn(run: dict[str, Any]) -> Callable[[float, float, dict[str, float]], float]:
    """The relaxed headway the gap choice reads (docs/MERGE_MODEL.md §2): ``T`` at the relaxation start.

    For a bumper gap ``s`` at speed ``v``: ``min(T_now, clamp((s − s0)/v,
    floor, T_i))`` — the headway the vehicle would get if the change were
    made now (B§5.5: F evaluated at its relaxed T; the hold reads F's T at
    the relaxation start, WP-90's form X), its current one if already
    shorter; an AV's own ``T``.
    """
    params: merge_model.MergeModelParams = run["params"]
    step_s = float(run["step_s"])

    def relaxed(s_bb: float, v: float, p: dict[str, float]) -> float:
        if p.get("av", 0.0) > 0.0 or "T0" not in p:
            return p["T"]
        t0 = merge_model.relaxation_start(
            s_bb - p["s0"], v, p["T0"], step_s, params.relax_floor_fraction
        )
        return min(p["T"], t0)

    return relaxed


def _mm_set_tau(mod: Any, run: dict[str, Any], vid: str, tau: float) -> None:
    """Write a headway to SUMO and to the shared constants; record the lowest written."""
    mod.vehicle.setTau(vid, tau)
    p = run["veh_params"].get(vid)
    if p is not None:
        p["T"] = tau
    run["n_tau_writes"] += 1
    if run["min_tau_set_s"] is None or tau < run["min_tau_set_s"]:
        run["min_tau_set_s"] = tau


def _measured_grant(
    mod: Any,
    run: dict[str, Any],
    vid: str,
    gap_net: float,
    v: float,
    t: float,
    role: str,
    zone: int,
    where: tuple[str, int],
) -> float | None:
    """Grant (or re-grant) a vehicle the post-crossing relaxation (B§5.7). Returns ``T_eff,0`` or ``None``.

    ``T_eff,0 = clamp(gap_net / v, max(step, 0.5·T_i), T_i)``
    (:func:`merge_model.relaxation_start`): the vehicle's equilibrium gap
    equals the gap it has. Nothing is granted to an AV (B§5.6), nor when the
    gap is a normal one (``T_eff,0 = T_i``); a vehicle already relaxed is
    re-granted only when the new start is the smaller ``T``
    (:func:`merge_model.regrant`).

    ``where`` anchors the lane-change reading of :func:`_measured_relax_step`,
    which compares normal lanes only. On an internal junction lane (a zone of
    several pieces under ``OSMNetwork.internal_links``, where the new follower
    can still be crossing the junction) the anchor is the lane that junction
    lane leaves from (the connection's ``via``, :func:`_internal_lane_origins`);
    a junction lane no connection names keeps the anchor the vehicle has, or
    none, and the vehicle is anchored on its next normal lane.
    """
    params: merge_model.MergeModelParams = run["params"]
    p = _mm_veh(mod, run, vid)
    if p["av"] > 0.0:
        return None
    t_own = p["T0"]
    t0 = merge_model.relaxation_start(
        gap_net, v, t_own, float(run["step_s"]), params.relax_floor_fraction
    )
    if t0 >= t_own - 1e-12:
        return None
    relax: dict[str, merge_model.RelaxationState] = run["relax"]
    rx = relax.get(vid)
    if rx is not None:
        if not merge_model.regrant(rx.tau_set, t0):
            return None
        run["n_relax_regranted"] += 1
    relax[vid] = merge_model.RelaxationState(
        t_own=t_own, t_start=t0, granted_s=t, tau_set=t0, role=role, zone=zone
    )
    anchor = run["via_from"].get(where) if where[0].startswith(":") else where
    if anchor is not None:
        run["relax_where"][vid] = anchor
    run["relax_leader"].pop(vid, None)
    _mm_set_tau(mod, run, vid, t0)
    run["n_relax_granted_entrant" if role == "entrant" else "n_relax_granted_follower"] += 1
    return t0


def _measured_relax_step(mod: Any, tc: Any, run: dict[str, Any], results: Any, t: float) -> None:
    """One step of every relaxation of the run (B§5.7), before any zone acts.

    Each relaxed vehicle: gone from the network → dropped; its lane changed
    since the last step (a ``(road, lane)`` that does not continue the last
    normal one along the lane connections, :func:`_lane_continues`; internal
    junction lanes skipped) → its own ``T`` restored; ``τ ≥ 4 τ_r`` →
    restored; otherwise ``T_eff(τ) = T_i − (T_i − T_eff,0)·exp(−τ/τ_r)``
    written with ``vehicle.setTau``, never below ``max(step, 0.5·T_i)``.
    LC2013's secure gaps read ``tau``, so a relaxed vehicle also lets SUMO's
    own changes in at shorter gaps (B§5.7): a new leader that came from
    another lane in front of a relaxed vehicle is counted
    (``n_relaxed_cut_ins``).
    """
    relax: dict[str, merge_model.RelaxationState] = run["relax"]
    prev_where: dict[str, tuple[str, int]] = run["where"]
    if relax or prev_where:
        # where every vehicle was this step (for the cut-in reading next step)
        run["where"] = (
            {
                vid: (str(res[tc.VAR_ROAD_ID]), int(res[tc.VAR_LANE_INDEX]))
                for vid, res in results.items()
            }
            if relax
            else {}
        )
    if not relax:
        return
    params: merge_model.MergeModelParams = run["params"]
    step_s = float(run["step_s"])
    succ: Mapping[tuple[str, int], frozenset[tuple[str, int]]] = run["succ"]
    where_of: dict[str, tuple[str, int]] = run["relax_where"]
    leaders: dict[str, str] = run["relax_leader"]
    for vid in sorted(relax):
        rx = relax[vid]
        res = results.get(vid)
        if res is None:
            del relax[vid]
            where_of.pop(vid, None)
            leaders.pop(vid, None)
            run["n_relax_restored_left"] += 1
            continue
        road = str(res[tc.VAR_ROAD_ID])
        now = (road, int(res[tc.VAR_LANE_INDEX]))
        if not road.startswith(":"):
            last = where_of.get(vid)
            if last is not None and now != last and not _lane_continues(succ, last, now):
                _mm_set_tau(mod, run, vid, rx.t_own)
                del relax[vid]
                where_of.pop(vid, None)
                leaders.pop(vid, None)
                run["n_relax_restored_lane_change"] += 1
                continue
            where_of[vid] = now
        elapsed = t - rx.granted_s
        if merge_model.relaxation_expired(elapsed, params.tau_r_s, params.relax_restore_tau_r):
            _mm_set_tau(mod, run, vid, rx.t_own)
            del relax[vid]
            where_of.pop(vid, None)
            leaders.pop(vid, None)
            run["n_relax_restored_expired"] += 1
            continue
        tau = rx.tau_at(t, params, step_s)
        if abs(tau - rx.tau_set) > 1e-12:
            _mm_set_tau(mod, run, vid, tau)
            rx.tau_set = tau
        lead = mod.vehicle.getLeader(vid, LEADER_LOOKAHEAD_M)
        lid = lead[0] if lead is not None and lead[0] else ""
        before = leaders.get(vid)
        if lid and before is not None and lid != before:
            l_was = prev_where.get(lid)
            if l_was is not None and l_was[0] == now[0] and l_was[1] != now[1]:
                run["n_relaxed_cut_ins"] += 1
        leaders[vid] = lid


def _lane_continues(
    succ: Mapping[tuple[str, int], frozenset[tuple[str, int]]],
    last: tuple[str, int],
    now: tuple[str, int],
    hops: int = RELAX_CONTINUITY_HOPS,
) -> bool:
    """Whether ``now`` follows ``last`` along the lane connections within ``hops`` hops.

    One hop is a connection successor (the next edge, in the lane ``last``
    connects into). Further hops admit an edge shorter than one step of
    travel, passed over between two readings. ``succ`` holds normal lanes
    only; its connections pass over the internal junction lanes.
    """
    frontier = succ.get(last, frozenset())
    if now in frontier:
        return True
    seen = {last, *frontier}
    for _ in range(hops - 1):
        frontier = frozenset(s for lane in frontier for s in succ.get(lane, ()) if s not in seen)
        if now in frontier:
            return True
        if not frontier:
            return False
        seen.update(frontier)
    return False


def _measured_reach(net: Any, zone_edges: Sequence[str]) -> dict[str, dict[int, frozenset[str]]]:
    """:func:`merge_model.lane_reach` over the compiled network's lane connections."""

    def outgoing(e: str, j: int) -> list[tuple[str, int]]:
        lane = net.getEdge(e).getLanes()[j]
        return [(c.getTo().getID(), int(c.getToLane().getIndex())) for c in lane.getOutgoing()]

    return merge_model.lane_reach(
        zone_edges, outgoing, lambda e: int(net.getEdge(e).getLaneNumber())
    )


def _lane_successors(net: Any) -> dict[tuple[str, int], frozenset[tuple[str, int]]]:
    """``(edge, lane) → {(edge, lane) it connects to}`` over the compiled network."""
    out: dict[tuple[str, int], frozenset[tuple[str, int]]] = {}
    for e in net.getEdges():
        for lane in e.getLanes():
            out[(e.getID(), int(lane.getIndex()))] = frozenset(
                (c.getTo().getID(), int(c.getToLane().getIndex())) for c in lane.getOutgoing()
            )
    return out


def _internal_lane_origins(net: Any) -> dict[tuple[str, int], tuple[str, int]]:
    """``(internal edge, lane) → (edge, lane)`` it leaves from, over the compiled network.

    From each connection's ``via`` (its first internal junction lane), which
    ``sumolib.net.readNet`` keeps although it loads no internal edge. Empty
    for a network built without internal links.
    """
    out: dict[tuple[str, int], tuple[str, int]] = {}
    for e in net.getEdges():
        for lane in e.getLanes():
            for c in lane.getOutgoing():
                via = c.getViaLaneID()
                if via:
                    road, _, index = str(via).rpartition("_")
                    out[(road, int(index))] = (e.getID(), int(lane.getIndex()))
    return out


def _measured_zone_state(
    *,
    index: int,
    run: dict[str, Any],
    net: Any,
    chain: Sequence[str],
    ramps: Sequence[RampSpec],
    on_index: int,
    section: WeaveSection | None,
    offsets_by_edge: Mapping[str, float],
    route_by_id: Mapping[str, str],
    routes: Mapping[str, Sequence[str]],
    step_s: float,
) -> dict[str, Any]:
    """One measured zone's state, in the weave's section layout plus ``mm``.

    ``section`` is the paired weaving section (a ramp with a weave block,
    validated by :func:`_check_weave_pairs`), or ``None`` for an
    acceleration lane: the zone is then the attach edge alone, whose lane 0
    the termination of :func:`_apply_merge_models` made dead-end, with no
    exit, no vacate window and no exiting movement. The weave's helpers read
    the layout's keys; the section constants are
    :func:`_measured_constants`.
    """
    ramp_w = ramps[on_index]
    weave = section is not None
    if section is not None:
        exit_w: RampSpec | None = ramps[section.off_ramp]
        edges = list(section.edges)
        exit_edge: str | None = section.exit_edge
        exit_only = list(section.exit_only)
        off_index = section.off_ramp
        length_measured = float(section.length_m)
    else:
        exit_w = None
        edges = [ramp_w.attach_edge]
        exit_edge = None
        exit_only = [True]
        off_index = -1
        length_measured = float(net.getEdge(ramp_w.attach_edge).getLength())
    lens = {e: float(net.getEdge(e).getLength()) for e in edges}
    params = _measured_constants()
    vacate_lanes = (
        _weave_vacate_lanes(net, chain, edges, offsets_by_edge, float(params["vacate_ahead_m"]))
        if weave
        else {}
    )
    lane_map: dict[tuple[str, int], int] = {
        k: v for k, v in _weave_lane_map(net, chain, edges).items() if k[0] in offsets_by_edge
    }
    # the ramp's lanes continue the section's lanes they connect into, at
    # negative positions (the weave maps lane 0 only; a two-lane ramp's lane
    # 1 is mapped too, so a two-auxiliary-lane zone sees both, B§5.2)
    last_ramp = ramp_w.edges[-1]
    ramp_to: dict[int, int] = {}
    for lane in net.getEdge(last_ramp).getLanes():
        for c in lane.getOutgoing():
            if c.getTo().getID() == edges[0]:
                ramp_to.setdefault(int(lane.getIndex()), int(c.getToLane().getIndex()))
    for e in ramp_w.edges:
        for lane in net.getEdge(e).getLanes():
            j = int(lane.getIndex())
            lane_map[(e, j)] = ramp_to.get(j, j) if ramp_to else j
    if (last_ramp, 0) not in lane_map or not ramp_to:
        for e in ramp_w.edges:
            lane_map[(e, 0)] = 0
    i_last = chain.index(edges[-1])
    through_edge = chain[i_last + 1] if i_last + 1 < len(chain) else edges[-1]
    target_of_route: dict[str, str | None] = {}
    for rid, r_edges in routes.items():
        r_list = list(r_edges)
        if edges[-1] in r_list:
            j = r_list.index(edges[-1])
            target_of_route[rid] = r_list[j + 1] if j + 1 < len(r_list) else None
    exiting_ids = frozenset(
        vid for vid, rid in route_by_id.items() if off_index >= 0 and _route_exit(rid) == off_index
    )
    ws: dict[str, Any] = {
        "ramp": ramp_w.name or ramp_w.attach_edge,
        "exit": (exit_w.name or exit_w.attach_edge) if exit_w is not None else None,
        "off_index": off_index,
        "edges": edges,
        "edge_index": {e: n for n, e in enumerate(edges)},
        "exit_edge": exit_edge,
        "exit_edges": frozenset(exit_w.edges) if exit_w is not None else frozenset(),
        "exit_only": dict(zip(edges, exit_only, strict=True)),
        "lane_len_m": lens,
        "beyond_m": {e: sum(lens[x] for x in edges[n + 1 :]) for n, e in enumerate(edges)},
        "length_m_measured": length_measured,
        "length_m": ramp_w.weave.length_m if ramp_w.weave is not None else None,
        "params": params,
        "rule": _weave_short_section_rule(float(sum(lens.values())), params),
        "exiting_ids": exiting_ids,
        "exited": set(),
        "reached": set(),
        "awaiting_exit": set(),
        "veh": {},
        "lane_map": lane_map,
        "vacate_lanes": vacate_lanes,
        "vacate_exempt_ids": (
            _weave_vacate_exempt_ids(net, ramps, section, vacate_lanes, route_by_id)
            if section is not None
            else frozenset()
        ),
        "vacate": {},
        "vacate_seen": set(),
        "vacate_pending": set(),
        "vacate_asks_s": deque(),
        "vacate_flow_ids": set(),
        "vacate_flow_s": deque(),
        "prep": {},
        "prep_seen": set(),
        "prep_pending": set(),
        "prep_asks_s": deque(),
        "prep_flow_ids": set(),
        "prep_flow_s": deque(),
        "x_offset": {
            **offsets_by_edge,
            **{
                e: offsets_by_edge[edges[0]]
                - sum(float(net.getEdge(x).getLength()) for x in ramp_w.edges[n:])
                for n, e in enumerate(ramp_w.edges)
            },
        },
        "ramp_edges": frozenset(ramp_w.edges),
        "pre": {},
        # one constants cache for the run: every zone and the relaxation share it
        "veh_params": run["veh_params"],
        "lane_vmax": {},
        "n_entered": 0,
        "n_changed_in": 0,
        "n_changed_out": 0,
        "n_forced": 0,
        "n_missed": 0,
        "n_missed_exit": 0,
        "gave_up": set(),
        "through_target": chain[-1],
        "n_forced_deferred": 0,
        "n_cooperations": 0,
        "coop_decel_sum": 0.0,
        "n_changer_eased": 0,
        "n_vacated": 0,
        "n_vacate_refused": 0,
        "n_vacate_skipped_no_gap": 0,
        "n_vacate_requests": 0,
        "n_exit_prepared": 0,
        "n_exit_prepare_refused": 0,
        "n_exit_prepare_requests": 0,
        "n_exit_prepare_skipped": 0,
        "n_exit_prepare_yielded": 0,
        "n_exit_prepare_held": 0,
        "handover": {},
        "n_handovers": 0,
        "opp_veto": {},
        "n_opposing_deferred": 0,
        "pair_since": {},
        "pair_released": set(),
        "n_pair_releases": 0,
        "step_s": float(step_s),
        "waits_in_s": [],
        "waits_out_s": [],
        "mm": {
            "index": index,
            "run": run,
            "weave": weave,
            "kind": "weave" if weave else "acceleration_lane",
            "reach": _measured_reach(net, edges),
            "target_of_route": target_of_route,
            "route_by_id": route_by_id,
            "through_edge": through_edge,
            "ceiling": {},
            "touched": {},
            "n_crossings_in": 0,
            "n_crossings_out": 0,
            "n_exec_accepted": 0,
            "n_exec_forced": 0,
            "n_requests": 0,
            "n_requests_cancelled": 0,
            "n_model_checks": 0,
            "n_lead_model_checks": 0,
            "refused": {
                k: 0
                for k in (
                    "lead_time",
                    "lead_guard",
                    "lag_time",
                    "lag_guard",
                    "lead_model",
                    "lag_model",
                )
            },
            "n_relax_entrant": 0,
            "n_relax_follower": 0,
            "n_ceiling_steps": 0,
            "n_early_crossings": 0,
            "n_av_released": 0,
            # A2.2: exiters' arrival-step changes kept, and the approach lanes
            "n_arrival_changes_kept": 0,
            "approach_k": {},
            "n_collisions": 0,
            "cross_x_in": [],
            "cross_x_out": [],
        },
    }
    return ws


def _measured_constants() -> dict[str, float]:
    """The section constants a measured zone runs on, in the weave's key layout.

    :data:`flowstate_core.config.WEAVE_DEFAULTS` with the measured model's
    fixed constants (docs/MERGE_MODEL.md §2, ``microsim.merge_model``): the
    forced zone 80 m / 4 s, the pair release 2 s, the exit give-up 5 m, the
    vacate window 500 m under the 2,050 veh/h spare bound, ``exit_prepare``
    on, the lookahead 120 m. The time-gap keys (``accept_gap_s`` …) are not
    read: the measured acceptance replaces them (they were read by the
    vacate and early-move rules' gap-conditioned form, removed on
    2026-10-06).
    """
    return {
        **WEAVE_DEFAULTS,
        "force_within_m": merge_model.FORCE_WITHIN_M,
        "force_after_s": merge_model.FORCE_AFTER_S,
        "change_duration_s": merge_model.REQUEST_REISSUE_S,
        "lookahead_m": merge_model.LOOKAHEAD_M,
        "vacate_ahead_m": merge_model.VACATE_AHEAD_M,
        "vacate_max_veh_h": 0.0,
        "pair_release_s": merge_model.PAIR_RELEASE_S,
        "exit_giveup_m": merge_model.EXIT_GIVEUP_M,
        "exit_prepare": 1.0 if merge_model.EXIT_PREPARE else 0.0,
    }


def _mm_target(ws: dict[str, Any], vid: str) -> str | None:
    """The edge a vehicle must leave the zone onto: its route's edge after the zone's last."""
    mm = ws["mm"]
    if vid in ws["gave_up"]:
        return str(mm["through_edge"])
    rid = mm["route_by_id"].get(vid, "main")
    target: str | None = mm["target_of_route"].get(rid)
    return target


def _mm_follow_speed(
    mod: Any,
    vid: str,
    v: float,
    gap_net: float,
    v_leader: float,
    b_leader: float,
    leader: str,
    tau_eval: float,
    tau_now: float,
) -> float:
    """SUMO's own follow speed of ``vid`` behind ``leader`` at headway ``tau_eval``, read in one step.

    ``vehicle.getFollowSpeed`` (the model's ``followSpeed``, B§5.5); when
    ``tau_eval`` differs from the vehicle's headway now, ``setTau`` before
    and back to ``tau_now`` after, within the same step, so nothing of the
    simulation sees the trial value. Used for the follower behind the
    changer and (amendment A1.2) the changer behind its new leader.
    """
    changed = abs(tau_eval - tau_now) > 1e-12
    if changed:
        mod.vehicle.setTau(vid, tau_eval)
    try:
        return float(mod.vehicle.getFollowSpeed(vid, v, gap_net, v_leader, b_leader, leader))
    finally:
        if changed:
            mod.vehicle.setTau(vid, tau_now)


def _mm_cancel(mod: Any, vid: str, st: dict[str, Any], lane: int, step_s: float) -> None:
    """No request left open across steps (B§5.6): a refused one-step request is ended by a stay."""
    if st.get("open"):
        mod.vehicle.changeLane(vid, lane, step_s)
        st["open"] = False
        st["mm"]["n_requests_cancelled"] += 1


def _measured_handover_step(
    mod: Any,
    tc: Any,
    ws: dict[str, Any],
    results: Any,
    x_of: dict[str, float],
    v_of: dict[str, float],
    pending: dict[str, int],
) -> None:
    """Take every entrant one step before it can reach the zone (B§5.9, amended by A2.2).

    SUMO moves a vehicle and then runs its lane changes in one step, so
    LC2013 makes a sixth to two fifths of a zone's crossings in the step a
    vehicle arrives, unread by the runner (WP-67). An entrant on the ramp
    whose arrival lane does not reach its route
    (:func:`merge_model.mandatory_direction` on the zone's first edge) is set
    to ``LC_MODE_SCRIPTED_SAFE`` when its distance to the zone start is
    within two steps' travel at the speed it can reach in one, ``2·Δt·(v +
    a·Δt)`` (the bound of the weave's crossings spread, WP-67). Its own mode is kept
    in ``ws["handover"]`` and restored at hand-back; one that leaves the
    window without being driven gets it back at once; one under another
    scripted hold is left to it.

    Amendment A2.2 (2026-10-06): the take-over applies to the entering
    movement only. A mainline vehicle — an exiter — reaches the zone under
    its own lane-change model, and a change LC2013 makes in the step it
    arrives is kept, as the weave kept it (stage 1 took exiters over too and
    removed those fast crossings: 78 of 327 exiters reached the T.H.52
    section already in the auxiliary lane against the weave's 157 of 336);
    the model drives only the exiters still in the wrong lane inside the
    zone. The kept changes are counted by :func:`_measured_step`
    (``n_arrival_changes_kept``).
    """
    mm = ws["mm"]
    run = mm["run"]
    x_start = float(ws["x_offset"][ws["edges"][0]])
    step_s = float(ws["step_s"])
    edges: dict[str, int] = ws["edge_index"]
    handover: dict[str, int] = ws["handover"]
    lane_map: dict[tuple[str, int], int] = ws["lane_map"]
    reach0: Mapping[int, frozenset[str]] = mm["reach"][ws["edges"][0]]
    now: set[str] = set()
    for vid, x in x_of.items():
        if vid in ws["veh"] or vid in ws["gave_up"] or x >= x_start:
            continue
        res = results[vid]
        road = res[tc.VAR_ROAD_ID]
        if road in edges or road not in ws["ramp_edges"] or vid in ws["exiting_ids"]:
            continue  # A2.2: entrants on the ramp only
        v = v_of[vid]
        # the bound needs the vehicle's acceleration: a cheap pre-test first
        if x_start - x > 2.0 * step_s * (v + 5.0 * step_s):
            continue
        p = _mm_veh(mod, run, vid)
        if x_start - x > 2.0 * step_s * (v + p["a"] * step_s):
            continue
        k = lane_map.get((road, int(res[tc.VAR_LANE_INDEX])))
        target = _mm_target(ws, vid)
        if k is None or target is None or merge_model.mandatory_direction(reach0, k, target) == 0:
            continue
        if vid not in handover:
            # a pending veto's recorded mode, not the vetoed one (review
            # 2026-10-07, third regression review; _lc_mode_owned)
            mode = _lc_mode_owned(mod, ws, vid)
            if mode in (
                LC_MODE_SCRIPTED_SAFE,
                LC_MODE_SCRIPTED_FORCE,
                LC_MODE_SCRIPTED_SAFE_NO_ADAPT,
            ):
                continue  # under another scripted hold, whose owner restores it
            handover[vid] = mode
            mod.vehicle.setLaneChangeMode(vid, LC_MODE_SCRIPTED_SAFE)
            ws["n_handovers"] += 1
        now.add(vid)
    for vid in [v for v in handover if v not in now and v not in pending]:
        mode = handover.pop(vid)
        if vid in results:
            mod.vehicle.setLaneChangeMode(vid, mode)


def _measured_cooperate(
    mod: Any,
    tc: Any,
    ws: dict[str, Any],
    results: Any,
    lanes: dict[int, list[tuple[float, str]]],
    x_of: dict[str, float],
    v_of: dict[str, float],
    p_of: dict[str, dict[str, float]],
    v0_of: dict[str, float],
    coop: dict[str, tuple[float, float, bool]],
    vid: str,
    target_lane: int,
    committed: str | None,
    remaining_m: float,
    priority: bool,
    entering: bool,
    lead_need: Callable[[float, float], float],
) -> tuple[str | None, str | None, float]:
    """Gap choice and the two one-step targets of a measured changer (B§5.5).

    The weave's core unchanged in form (:func:`_weave_cooperate`):
    :func:`_weave_choose_gap`
    — the nearest gap whose follower F opens it within ``b_F`` — with, for
    the entering movement, F's and the changer's IDM read at their relaxed
    headway (:func:`_mm_relaxed_fn`); F held by a one-step
    :func:`_weave_command` towards the changer (the chosen follower only);
    the changer eased towards the gap's leader when it would brake for it and
    dropping in behind it by the zone's end needs no more than its ``b``
    (:func:`_weave_easing_ok`, the gap it needs being ``lead_need``), never
    towards a leader beside it on the ramp. Desired speeds honour the speed
    factor (:func:`_mm_v0`).

    Returns:
        ``(follower, leader, v0 on the target lane)`` of the chosen gap.
    """
    run = ws["mm"]["run"]
    res = results[vid]
    road = res[tc.VAR_ROAD_ID]
    v_c = float(res[tc.VAR_SPEED])
    p_c = _mm_veh(mod, run, vid)
    v_road = road if road in ws["edge_index"] else ws["edges"][0]
    v0_c = _mm_v0(p_c, _weave_lane_vmax(mod, ws, v_road, target_lane))
    lane_list = lanes.get(target_lane, [])
    for _x, oid in lane_list:
        if oid not in p_of:
            p_of[oid] = _mm_veh(mod, run, oid)
            r_o = results[oid]
            v0_of[oid] = _mm_v0(
                p_of[oid],
                _weave_lane_vmax(mod, ws, r_o[tc.VAR_ROAD_ID], int(r_o[tc.VAR_LANE_INDEX])),
            )
    l_t, f_t, a_f, a_c = _weave_choose_gap(
        vid,
        x_of[vid],
        v_c,
        p_c,
        v0_c,
        lane_list,
        x_of,
        v_of,
        p_of,
        v0_of,
        float(ws["params"]["lookahead_m"]),
        committed,
        priority,
        relaxed_T=_mm_relaxed_fn(run) if entering else None,
    )
    step_s = float(ws["step_s"])
    if f_t is not None:
        _weave_command(mod, coop, f_t, v_of[f_t], v0_of[f_t], p_of[f_t], a_f, step_s)
    if l_t is not None and a_c < 0.0:
        s_l = x_of[l_t] - p_of[l_t]["len"] - x_of[vid]
        beside = road in ws["ramp_edges"] and s_l < 0.0
        if not beside and _weave_easing_ok(
            v_c, v_of[l_t], s_l, lead_need(v_c, v_of[l_t]), remaining_m, p_c["b"]
        ):
            _weave_command(mod, coop, vid, v_c, v0_c, p_c, a_c, step_s, follower=False)
    return f_t, l_t, v0_c


def _measured_ceiling(
    mod: Any,
    ws: dict[str, Any],
    vid: str,
    v_now: float,
    v0_target: float,
    l_t: str | None,
    x_of: dict[str, float],
    v_of: dict[str, float],
    p_of: dict[str, dict[str, float]],
) -> None:
    """Principle (ii): the changer's desired-speed ceiling (B§5.4), through ``setMaxSpeed`` only.

    The chosen gap's leader's speed plus δ, approached kinematically from the
    bumper distance to that leader's rear (:func:`merge_model.speed_ceiling`);
    a gap with open road ahead, or no gap chosen, sets no ceiling (amendment
    A3, 2026-10-06: the stage-1 fallback to the target lane's mean speed
    within 50 m is withdrawn; :func:`merge_model.gap_reference_speed`);
    never below ``v_now − b·Δt``
    (SUMO caps the next speed at ``maxSpeed`` outright). A ceiling at or
    above the desired speed on the target lane is no ceiling: ``maxSpeed``
    stays (or returns to) its own value. Restored when the vehicle is handed
    back.
    """
    mm = ws["mm"]
    run = mm["run"]
    p_c = _mm_veh(mod, run, vid)
    x_c = x_of[vid]
    d_gap = 0.0
    v_leader: float | None = None
    if l_t is not None:
        v_leader = v_of[l_t]
        d_gap = x_of[l_t] - p_of[l_t]["len"] - x_c
    v_gap = merge_model.gap_reference_speed(v_leader)
    params: merge_model.MergeModelParams = run["params"]
    ceiling = (
        None
        if v_gap is None
        else merge_model.speed_ceiling(
            v_gap,
            params.delta(bool(mm["weave"])),
            p_c["b"],
            d_gap,
            v0_target,
            v_now=v_now,
            step_s=float(ws["step_s"]),
        )
    )
    if ceiling is not None and ceiling >= v0_target - 1e-9:
        ceiling = None
    last = mm["ceiling"].get(vid)
    if ceiling is None:
        if last is not None:
            mod.vehicle.setMaxSpeed(vid, p_c["vmax"])
            mm["ceiling"][vid] = None
        return
    mm["n_ceiling_steps"] += 1
    if last is None or abs(ceiling - last) > 1e-9:
        mod.vehicle.setMaxSpeed(vid, ceiling)
        mm["ceiling"][vid] = ceiling


def _mm_restore_ceiling(mod: Any, ws: dict[str, Any], vid: str, in_network: bool) -> None:
    """Hand a vehicle's ``maxSpeed`` back (its own value, read before any ceiling)."""
    mm = ws["mm"]
    if vid not in mm["ceiling"]:
        return
    last = mm["ceiling"].pop(vid)
    if last is not None and in_network:
        mod.vehicle.setMaxSpeed(vid, _mm_veh(mod, mm["run"], vid)["vmax"])


def _measured_crossings(
    mod: Any, tc: Any, ws: dict[str, Any], results: Any, x_of: dict[str, float], t: float
) -> None:
    """Read the driven vehicles' crossings since the last step; grant the relaxation (B§5.7).

    A crossing is a driven vehicle's section lane moving one lane in its
    direction (the zone's lane map, so a lane added or dropped at an edge
    boundary is not one). It is attributed to the request it executed
    (``acc`` / ``force``). For the entering movement the entrant C and its
    new follower F — when within WP-88's car-following range — are granted
    :func:`_measured_grant`: C on its gap to its new leader, F on its gap
    to C (both net of the rear vehicle's ``minGap``, SUMO's ``getLeader`` /
    ``getFollower``).
    """
    mm = ws["mm"]
    run = mm["run"]
    lane_map: dict[tuple[str, int], int] = ws["lane_map"]
    x_start = float(ws["x_offset"][ws["edges"][0]])
    step_s = float(ws["step_s"])
    for vid, st in ws["veh"].items():
        res = results.get(vid)
        if res is None:
            continue
        road = res[tc.VAR_ROAD_ID]
        lane = int(res[tc.VAR_LANE_INDEX])
        k_now = lane_map.get((road, lane))
        if k_now is None:
            continue
        k_prev = st["k"]
        st["k"] = k_now
        if k_prev is None or k_now == k_prev:
            continue
        if k_now - k_prev != st["dir"]:
            continue
        st["open"] = False
        if st["last_kind"] == "force":
            mm["n_exec_forced"] += 1
            st["forced"] = True
        else:
            mm["n_exec_accepted"] += 1
        if t - st["entered_s"] <= 2.0 * step_s + 1e-9:
            mm["n_early_crossings"] += 1
        x_in = x_of.get(vid, math.nan) - x_start
        if st["exiter"]:
            mm["n_crossings_out"] += 1
            mm["cross_x_out"].append(x_in)
            continue
        mm["n_crossings_in"] += 1
        mm["cross_x_in"].append(x_in)
        v_c = float(res[tc.VAR_SPEED])
        here = (str(road), lane)
        lead = mod.vehicle.getLeader(vid, LEADER_LOOKAHEAD_M)
        if lead is not None and lead[0]:
            gap_net = float(lead[1])
            p_c = _mm_veh(mod, run, vid)
            if merge_model.in_car_following_range(gap_net + p_c["s0"], v_c) and (
                _measured_grant(mod, run, vid, gap_net, v_c, t, "entrant", mm["index"], here)
                is not None
            ):
                mm["n_relax_entrant"] += 1
                mm["touched"][vid] = t
        fol = mod.vehicle.getFollower(vid, LEADER_LOOKAHEAD_M)
        if fol is not None and fol[0]:
            fid, gap_f = str(fol[0]), float(fol[1])
            r_f = results.get(fid)
            if r_f is not None:
                v_f = float(r_f[tc.VAR_SPEED])
                p_f = _mm_veh(mod, run, fid)
                where_f = (str(r_f[tc.VAR_ROAD_ID]), int(r_f[tc.VAR_LANE_INDEX]))
                if merge_model.in_car_following_range(gap_f + p_f["s0"], v_f) and (
                    _measured_grant(mod, run, fid, gap_f, v_f, t, "follower", mm["index"], where_f)
                    is not None
                ):
                    mm["n_relax_follower"] += 1
                    mm["touched"][fid] = t


def _measured_step(
    mod: Any,
    tc: Any,
    ws: dict[str, Any],
    results: Any,
    t: float,
    index: _RoadIndex | None = None,
) -> None:
    """One step of a measured merge zone (``RampSpec.merge = "measured"``; docs/MERGE_MODEL.md).

    **Who is driven** (B§5.2): every vehicle on a zone edge whose lane does
    not lead to its route's edge after the zone — computed from the compiled
    lane connections against the route (:func:`merge_model.lane_reach`,
    :func:`merge_model.mandatory_direction`), so an acceleration lane's
    entrants, a weave's entrants and exiters and a zone with two auxiliary
    lanes are one rule. Entrants are taken one step before they can reach the
    zone (:func:`_measured_handover_step`); exiters are driven from the zone
    only, an arrival-step change LC2013 made for them kept (amendment A2.2,
    ``n_arrival_changes_kept``). LC2013 keeps every other change.

    **Each step, for each driven vehicle**, in ``veh_id`` order:

    1. the acceptance (:func:`merge_model.acceptance`) of the immediate
       target-lane gaps (``vehicle.getNeighbors``): the driver's own lead
       and lag critical gaps for its movement on bumper-to-bumper gaps
       (``minGap`` added back), no lead time gate for an exiter, both brake
       guards, and — when all of these pass — the changer's own SUMO model
       behind its new leader
       (amendment A1.2; at its relaxed headway when entering, its own when
       exiting) braking no harder than ``b_C``, then the follower's own model
       at its relaxed headway braking no harder than ``b_F``
       (:func:`_mm_follow_speed`: ``setTau`` → ``getFollowSpeed`` →
       ``setTau`` back, one step; :func:`merge_model.follow_speed_ok`);
    2. the forced change (the last 80 m after 4 s, or a released pair's at
       once) through the two brake guards only (exempt from A1.2);
    3. an exiter halted within 5 m of the gore's end with no change to make
       this step is rerouted through (the exit give-up);
    4. the gap choice and cooperation (:func:`_measured_cooperate`), the
       exit priority once its forced change is due;
    5. the speed ceiling (:func:`_measured_ceiling`);
    6. execution (B§5.6): an accepted or forced change is requested for one
       step under ``LC_MODE_SCRIPTED_FORCE`` (256) after the opposing-entry
       resolution of the step's requests
       (:func:`merge_model.resolve_opposing`, always on); a vehicle with no
       request is held under ``LC_MODE_SCRIPTED_SAFE`` (512) with no
       request left open (:func:`_mm_cancel`).

    Entrants still on the ramp within ``lookahead_m`` of the zone choose
    their gap and their follower cooperates before they appear (the ramp
    anticipation); they get the ceiling too. Crossings are read at the
    start of the next step (:func:`_measured_crossings`), which grants the
    relaxation; the relaxations themselves are stepped run-wide
    (:func:`_measured_relax_step`). The vacate request and the exiters' early
    move (weaving sections) are the weave's rules, unchanged. Bookkeeping
    lands in ``ws`` / ``ws["mm"]`` for ``meta.json["measured_merges"]``.
    """
    mm = ws["mm"]
    run = mm["run"]
    prm = ws["params"]
    edges: dict[str, int] = ws["edge_index"]
    exiting: frozenset[str] = ws["exiting_ids"]
    exit_edges: frozenset[str] = ws["exit_edges"]
    lane_map: dict[tuple[str, int], int] = ws["lane_map"]
    x_offset: dict[str, float] = ws["x_offset"]
    ramp_edges: frozenset[str] = ws["ramp_edges"]
    reach: dict[str, dict[int, frozenset[str]]] = mm["reach"]
    x_start = float(x_offset[ws["edges"][0]])
    section_len = float(sum(ws["lane_len_m"].values()))
    rule = ws["rule"]
    step_s = float(ws["step_s"])
    veh: dict[str, dict[str, Any]] = ws["veh"]
    touched: dict[str, float] = mm["touched"]
    if ws["opp_veto"]:
        _weave_opposing_restore(mod, ws, results)
    # --- exit bookkeeping (weaving sections; the weave's rule) ---------------
    awaiting_exit: set[str] = ws["awaiting_exit"]
    for vid in list(awaiting_exit):
        res_a = results.get(vid)
        if res_a is None:
            ws["exited"].add(vid)
            awaiting_exit.discard(vid)
            continue
        road_a = res_a[tc.VAR_ROAD_ID]
        if road_a in exit_edges:
            ws["exited"].add(vid)
            awaiting_exit.discard(vid)
        elif road_a not in edges and not road_a.startswith(":"):
            awaiting_exit.discard(vid)
    # --- listings on the zone's axis and the vehicles owing a change --------
    lanes: dict[int, list[tuple[float, str]]] = {}
    x_of: dict[str, float] = {}
    v_of: dict[str, float] = {}
    pending: dict[str, int] = {}
    approaching: set[str] = set()
    in_transit: set[str] = set()
    # A2.2: each exiter's section lane on the approach last step and this
    # step, to read the change LC2013 makes in the step it arrives
    approach_prev: dict[str, int] = mm["approach_k"]
    approach_now: dict[str, int] = {}
    reach0: Mapping[int, frozenset[str]] = reach[ws["edges"][0]]
    if index is not None:
        # the pass below over the zone's roads only (_RoadIndex); a driven
        # vehicle on an internal junction lane is the one thing it would have
        # read off them (_zone_scan_roads)
        scan_roads = _zone_scan_roads(ws)
        for vid in veh:
            res_t = results.get(vid)
            if res_t is not None:
                road_t = res_t[tc.VAR_ROAD_ID]
                if road_t not in scan_roads and road_t.startswith(":"):
                    in_transit.add(vid)
    for vid, res in results.items() if index is None else index.on(scan_roads):
        road = res[tc.VAR_ROAD_ID]
        if road in exit_edges:
            if vid in exiting and vid not in ws["exited"]:
                ws["reached"].add(vid)
                ws["exited"].add(vid)
            continue
        lane = int(res[tc.VAR_LANE_INDEX])
        k = lane_map.get((road, lane))
        if k is not None and vid in exiting and road not in edges and road not in ramp_edges:
            approach_now[vid] = k
        if k is not None:
            x = x_offset[road] + float(res[tc.VAR_LANEPOSITION])
            x_of[vid] = x
            v_of[vid] = float(res[tc.VAR_SPEED])
            lanes.setdefault(k, []).append((x, vid))
            if (
                road in ramp_edges
                and vid not in exiting
                and x >= x_start - float(prm["lookahead_m"])
            ):
                approaching.add(vid)
        if road not in edges:
            if vid in veh and road.startswith(":"):
                in_transit.add(vid)
            continue
        if vid in exiting and vid not in ws["exited"] and vid not in ws["reached"]:
            ws["reached"].add(vid)
            awaiting_exit.add(vid)
            # A2.2: an exiter LC2013 moved towards the exit in its arrival step
            # (its lane last step on the approach owed the change, its lane
            # now is one or more lanes nearer) keeps that change
            k_prev = approach_prev.get(vid)
            k_now = lane_map.get((road, lane))
            if (
                k_prev is not None
                and k_now is not None
                and k_now != k_prev
                and ws["exit_edge"] is not None
                and merge_model.mandatory_direction(reach0, k_prev, ws["exit_edge"])
                == (1 if k_now > k_prev else -1)
            ):
                mm["n_arrival_changes_kept"] += 1
        target = _mm_target(ws, vid)
        if target is None:
            continue
        d = merge_model.mandatory_direction(reach[road], lane, target)
        if d != 0:
            pending[vid] = d
    for lst in lanes.values():
        lst.sort()
    mm["approach_k"] = approach_now
    # --- the weave's rules upstream of a weaving section (inert otherwise) --
    _weave_vacate_step(mod, tc, ws, results, lanes, t, index)
    _weave_exit_prepare_step(mod, tc, ws, results, lanes, t, index)
    _measured_handover_step(mod, tc, ws, results, x_of, v_of, pending)
    # --- the crossings made since the last step (relaxation granted) --------
    _measured_crossings(mod, tc, ws, results, x_of, t)
    # --- hand back the vehicles with no change left to make -----------------
    for vid in [v for v in veh if v not in pending and v not in in_transit]:
        st = veh.pop(vid)
        if vid not in results:
            mm["ceiling"].pop(vid, None)
            ws["n_missed"] += 1
            continue
        res_h = results[vid]
        road = res_h[tc.VAR_ROAD_ID]
        _mm_cancel(mod, vid, st, int(res_h[tc.VAR_LANE_INDEX]), step_s)
        mod.vehicle.setLaneChangeMode(vid, st["lc_mode_orig"])
        _mm_restore_ceiling(mod, ws, vid, True)
        if st["exiter"]:
            done = road in exit_edges or road in edges
        else:
            done = road not in exit_edges
        if not done:
            ws["n_missed"] += 1
            continue
        ws["n_changed_out" if st["exiter"] else "n_changed_in"] += 1
        ws["n_forced"] += int(st["forced"])
        ws["waits_out_s" if st["exiter"] else "waits_in_s"].append(t - st["entered_s"])
    # --- stopped crossing pairs (the weave's pair release) ------------------
    yielders, released = _weave_pair_release(mod, ws, pending, x_of, v_of, t)
    coop: dict[str, tuple[float, float, bool]] = {}
    p_of: dict[str, dict[str, float]] = {}
    v0_of: dict[str, float] = {}
    requests: dict[str, merge_model.ChangeRequest] = {}
    req_lanes: dict[str, tuple[int, int, str]] = {}
    for vid in sorted(pending):
        d = pending[vid]
        res = results[vid]
        road = res[tc.VAR_ROAD_ID]
        lane = int(res[tc.VAR_LANE_INDEX])
        k = lane_map.get((road, lane))
        st = veh.get(vid)
        p_c = _mm_veh(mod, run, vid)
        if st is None:
            st = veh[vid] = {
                "dir": d,
                "entered_s": t,
                "zone_s": None,
                "requested_s": -math.inf,
                "forced": False,
                # a pending veto's recorded mode, not the vetoed one (review
                # 2026-10-07, third regression review; _lc_mode_owned)
                "lc_mode_orig": (
                    ws["handover"].pop(vid)
                    if vid in ws["handover"]
                    else _lc_mode_owned(mod, ws, vid)
                ),
                "s0": p_c["s0"],
                "mode": LC_MODE_SCRIPTED_SAFE,
                "target": ws["pre"].pop(vid, None),
                "k": k,
                "open": False,
                "last_kind": None,
                "exiter": vid in exiting and vid not in ws["gave_up"],
                "mm": mm,
            }
            mod.vehicle.setLaneChangeMode(vid, LC_MODE_SCRIPTED_SAFE)
            ws["n_entered"] += 1
        st["dir"] = d
        touched[vid] = t
        if k is None:
            continue  # off the zone's lane map (cannot happen on a zone edge)
        exiter = bool(st["exiter"])
        movement = (
            "exiting_weave" if exiter else ("entering_weave" if mm["weave"] else "entering_merge")
        )
        gaps = _mm_driver(run, vid)
        t_lead = gaps.lead[movement]
        t_lag = gaps.lag[movement]
        v_ego = float(res[tc.VAR_SPEED])
        remaining = ws["lane_len_m"][road] - float(res[tc.VAR_LANEPOSITION]) + ws["beyond_m"][road]
        if remaining <= float(rule["zone_m"]) and st["zone_s"] is None:
            st["zone_s"] = t
        zone_due = st["zone_s"] is not None and t - st["zone_s"] >= float(rule["force_after_s"])
        modes = (
            (NEIGHBOR_LEFT_LEADERS, NEIGHBOR_LEFT_FOLLOWERS)
            if d > 0
            else (NEIGHBOR_RIGHT_LEADERS, NEIGHBOR_RIGHT_FOLLOWERS)
        )
        g_lead, v_lead, l_id = _neighbor_gap(mod, vid, modes[0])
        g_foll, v_foll, f_id = _neighbor_gap(mod, vid, modes[1])
        p_f = _mm_veh(mod, run, f_id) if f_id is not None else None
        acc = merge_model.acceptance(
            v_c=v_ego,
            s0_c=p_c["s0"],
            b_c=p_c["b"],
            t_c_lead=t_lead,
            t_c_lag=t_lag,
            g_lead=g_lead,
            v_lead=v_lead,
            g_foll=g_foll,
            v_foll=v_foll,
            s0_f=p_f["s0"] if p_f is not None else 0.0,
            b_f=p_f["b"] if p_f is not None else 1.0,
            step_s=step_s,
        )
        if acc.static_ok and l_id is not None and g_lead < math.inf:
            # amendment A1.2: the changer's own model behind its new leader, at
            # its relaxed T when entering (its own when exiting), not braking
            # harder than its b — the lag side's check mirrored
            t_rel_c = p_c["T"] if exiter else _mm_relaxed_fn(run)(g_lead + p_c["s0"], v_ego, p_c)
            v_follow_c = _mm_follow_speed(
                mod,
                vid,
                v_ego,
                g_lead,
                v_lead,
                _mm_veh(mod, run, l_id)["b"],
                l_id,
                t_rel_c,
                p_c["T"],
            )
            acc = dataclasses.replace(
                acc,
                lead_model=merge_model.follow_speed_ok(v_follow_c, v_ego, p_c["b"], step_s),
            )
            mm["n_lead_model_checks"] += 1
        if acc.static_ok and acc.lead_model is not False and f_id is not None and p_f is not None:
            # the follower's own model at its relaxed T (entering movement;
            # an exiter's follower at its own), read from SUMO in one step
            t_cur = p_f["T"]
            t_rel = _mm_relaxed_fn(run)(g_foll + p_f["s0"], v_foll, p_f) if not exiter else t_cur
            v_follow = _mm_follow_speed(
                mod, f_id, v_foll, g_foll, v_ego, p_c["b"], vid, t_rel, t_cur
            )
            acc = dataclasses.replace(
                acc,
                lag_model=merge_model.follow_speed_ok(v_follow, v_foll, p_f["b"], step_s),
            )
            mm["n_model_checks"] += 1
        force = st["zone_s"] is not None and (zone_due or vid in released)
        forced_ok = force and acc.guards_ok
        refusal = acc.refusal()
        if refusal is not None:
            mm["refused"][refusal] += 1
        if (
            exiter
            and remaining <= float(prm["exit_giveup_m"])
            and v_ego < HALTING_SPEED_MS
            and not (acc.accepted or forced_ok)
        ):
            # the exit given up (the weave's exit-side rule): rerouted through
            _mm_cancel(mod, vid, st, lane, step_s)
            mod.vehicle.changeTarget(vid, ws["through_target"])
            mod.vehicle.setLaneChangeMode(vid, st["lc_mode_orig"])
            _mm_restore_ceiling(mod, ws, vid, True)
            del veh[vid]
            ws["gave_up"].add(vid)
            awaiting_exit.discard(vid)
            ws["n_missed"] += 1
            ws["n_missed_exit"] += 1
            continue
        if vid in yielders:
            st["target"] = None
            _weave_set_mode(mod, vid, st, LC_MODE_SCRIPTED_SAFE)
            _mm_cancel(mod, vid, st, lane, step_s)
            continue

        def lead_need(v_c: float, v_l: float, _t: float | None = t_lead, _p=p_c) -> float:
            # the bumper gap the changer needs behind the gap's leader: its
            # lead critical gap (none for an exiter) and its brake guard
            c = max(v_c - v_l, 0.0)
            guard = _p["s0"] + c * step_s + c * c / (2.0 * _p["b"])
            return guard if _t is None else max(_t * v_c, guard)

        f_t, l_t, v0_t = _measured_cooperate(
            mod,
            tc,
            ws,
            results,
            lanes,
            x_of,
            v_of,
            p_of,
            v0_of,
            coop,
            vid,
            k + d,
            st["target"],
            remaining,
            exiter and zone_due,
            not exiter,
            lead_need,
        )
        st["target"] = f_t
        _measured_ceiling(mod, ws, vid, v_ego, v0_t, l_t, x_of, v_of, p_of)
        if acc.accepted or forced_ok:
            requests[vid] = merge_model.ChangeRequest(
                vid=vid,
                x=x_of[vid],
                lane=k,
                target=k + d,
                due=force,
                accept_s=t_lead if t_lead is not None else 0.0,
                v=v_ego,
                s0=p_c["s0"],
                b=p_c["b"],
            )
            req_lanes[vid] = (lane, lane + d, "acc" if acc.accepted else "force")
        else:
            if force:
                ws["n_forced_deferred"] += 1
            _weave_set_mode(mod, vid, st, LC_MODE_SCRIPTED_SAFE)
            _mm_cancel(mod, vid, st, lane, step_s)
    if requests:

        def state_of(pid: str) -> merge_model.OpponentState:
            st_p = veh.get(pid)
            if st_p is not None:
                return "open" if st_p.get("open") else "driven"
            mode = int(mod.vehicle.getLaneChangeMode(pid))
            return "model" if mode & LC_MODE_MODEL_BITS else "held"

        opp = {
            kk: [
                merge_model.LaneVehicle(o, x, _weave_veh(mod, ws, o)["len"], v_of[o])
                for x, o in lst
            ]
            for kk, lst in lanes.items()
        }
        withheld, vetoed = merge_model.resolve_opposing(list(requests.values()), opp, state_of)
        for vid in sorted(requests):
            st = veh[vid]
            lane, target, kind = req_lanes[vid]
            if vid in withheld:
                _weave_set_mode(mod, vid, st, LC_MODE_SCRIPTED_SAFE)
                _mm_cancel(mod, vid, st, lane, step_s)
                ws["n_opposing_deferred"] += 1
                continue
            _weave_exec_change(mod, vid, st, target, step_s, t, "acc")
            st["open"] = True
            st["last_kind"] = kind
            mm["n_requests"] += 1
        for pid in sorted(vetoed):
            mode = int(mod.vehicle.getLaneChangeMode(pid))
            mod.vehicle.setLaneChangeMode(pid, mode & ~LC_MODE_MODEL_BITS)
            ws["opp_veto"][pid] = mode
            ws["n_opposing_deferred"] += 1
    # --- entrants still on the ramp: anticipated before they appear ---------
    pre: dict[str, str | None] = ws["pre"]
    for vid in [v for v in pre if v not in approaching]:
        del pre[vid]
    for vid in sorted(approaching):
        touched[vid] = t
        p_a = _mm_veh(mod, run, vid)
        k_a = lane_map.get((results[vid][tc.VAR_ROAD_ID], int(results[vid][tc.VAR_LANE_INDEX])))
        t_lead_a = _mm_driver(run, vid).lead["entering_weave" if mm["weave"] else "entering_merge"]

        def lead_need_a(v_c: float, v_l: float, _t: float | None = t_lead_a, _p=p_a) -> float:
            c = max(v_c - v_l, 0.0)
            guard = _p["s0"] + c * step_s + c * c / (2.0 * _p["b"])
            return guard if _t is None else max(_t * v_c, guard)

        target_a = (k_a if k_a is not None else 0) + 1
        f_a, l_a, v0_a = _measured_cooperate(
            mod,
            tc,
            ws,
            results,
            lanes,
            x_of,
            v_of,
            p_of,
            v0_of,
            coop,
            vid,
            target_a,
            pre.get(vid),
            x_start - x_of[vid] + section_len,
            False,
            True,
            lead_need_a,
        )
        pre[vid] = f_a
        _measured_ceiling(mod, ws, vid, v_of[vid], v0_a, l_a, x_of, v_of, p_of)
    # ceilings of vehicles no longer driven nor anticipated
    for vid in [v for v in mm["ceiling"] if v not in veh and v not in approaching]:
        _mm_restore_ceiling(mod, ws, vid, vid in results)
    for vid in yielders:
        coop.pop(vid, None)
    for fid in sorted(coop):
        v_new, a_cmd, follower = coop[fid]
        mod.vehicle.slowDown(fid, v_new, 0.0)
        touched[fid] = t
        if follower:
            ws["n_cooperations"] += 1
            ws["coop_decel_sum"] += -a_cmd
        else:
            ws["n_changer_eased"] += 1
    if len(touched) > 4096:
        for vid in [v for v, ts in touched.items() if t - ts > MEASURED_ATTRIBUTION_S]:
            del touched[vid]


def _measured_owns(states: Sequence[dict[str, Any]], vid: str) -> bool:
    """Whether a measured zone drives ``vid`` now (its lane change, B§5.6: then the AV command waits)."""
    return any(vid in ws["veh"] or vid in ws["pre"] for ws in states if ws.get("mm"))


def _measured_attribute(
    states: Sequence[dict[str, Any]], collider: str, victim: str, lane_id: str, t: float
) -> None:
    """Count a collision against every measured zone it touches (``n_collisions_attributable``).

    A collision is a zone's when its lane is on one of the zone's edges or
    ramp edges, or when either party was commanded by the zone within
    :data:`MEASURED_ATTRIBUTION_S` (driven, anticipated, cooperating or
    relaxed).
    """
    edge = lane_id.rsplit("_", 1)[0] if "_" in lane_id else lane_id
    for ws in states:
        mm = ws.get("mm")
        if not mm:
            continue
        touched: dict[str, float] = mm["touched"]
        if (
            edge in ws["edge_index"]
            or edge in ws["ramp_edges"]
            or any(
                v in touched and t - touched[v] <= MEASURED_ATTRIBUTION_S
                for v in (collider, victim)
            )
        ):
            mm["n_collisions"] += 1


def _measured_meta(ws: dict[str, Any], n_departed_by_route: dict[str, int]) -> dict[str, Any]:
    """``meta.json["measured_merges"]`` entry of one measured zone.

    Movement completions as the weave counts them (``n_entered = n_changed_in
    + n_changed_out + n_missed + n_unfinished``), the crossings executed by
    movement and by kind (an accepted or a forced request; ``n_crossings_in
    + n_crossings_out = n_exec_accepted + n_exec_forced``), the requests made
    and cancelled, the refusals by first failed condition (vehicle-steps;
    ``lead_model`` added by amendment A1.2, 2026-10-06),
    the deferrals (a due forced change refused by the brake guards; an
    opposing entry), the give-ups, the relaxations granted (entrant and
    follower), the cooperation, the ceiling's binding vehicle-steps, the
    crossings made within two steps of the vehicle's arrival (B§5.13 (d)),
    the exiters' arrival-step changes LC2013 made and the model kept
    (amendment A2.2),
    the crossing positions from the zone start, and the collisions
    attributable to the zone.
    """
    mm = ws["mm"]
    waits = ws["waits_in_s"] + ws["waits_out_s"]

    def _mean(xs: list[float]) -> float | None:
        return float(np.mean(xs)) if xs else None

    def _q(xs: list[float]) -> list[float] | None:
        vals = [x for x in xs if math.isfinite(x)]
        return [float(v) for v in np.percentile(vals, [10, 50, 90])] if vals else None

    prm = ws["params"]
    return {
        "ramp": ws["ramp"],
        "exit": ws["exit"],
        "kind": mm["kind"],
        "edges": ws["edges"],
        "exit_edge": ws["exit_edge"],
        "exit_edges": sorted(ws["exit_edges"]),
        "length_m": ws["length_m"] if ws["length_m"] is not None else ws["length_m_measured"],
        "length_m_measured": ws["length_m_measured"],
        "constants": {
            "force_within_m": prm["force_within_m"],
            "force_after_s": prm["force_after_s"],
            "pair_release_s": prm["pair_release_s"],
            "exit_giveup_m": prm["exit_giveup_m"],
            "vacate_ahead_m": prm["vacate_ahead_m"] if mm["weave"] else None,
            "vacate_bound_veh_h": merge_model.VACATE_LANE_CAPACITY_VEH_H if mm["weave"] else None,
            "exit_prepare": bool(prm["exit_prepare"]) if mm["weave"] else None,
            "lookahead_m": prm["lookahead_m"],
            "creep_ms": merge_model.CREEP_MS,
            "request_reissue_s": merge_model.REQUEST_REISSUE_S,
        },
        "vacate_window_edges": list(ws["vacate_lanes"]),
        "n_entered": ws["n_entered"],
        "n_changed_in": ws["n_changed_in"],
        "n_changed_out": ws["n_changed_out"],
        "n_forced": ws["n_forced"],
        "n_missed": ws["n_missed"],
        "n_missed_exit": ws["n_missed_exit"],
        "n_unfinished": len(ws["veh"]),
        "n_crossings_in": mm["n_crossings_in"],
        "n_crossings_out": mm["n_crossings_out"],
        "n_exec_accepted": mm["n_exec_accepted"],
        "n_exec_forced": mm["n_exec_forced"],
        "n_requests": mm["n_requests"],
        "n_requests_cancelled": mm["n_requests_cancelled"],
        "n_forced_deferred": ws["n_forced_deferred"],
        "n_opposing_deferred": ws["n_opposing_deferred"],
        "refused_vehicle_steps": dict(mm["refused"]),
        "n_follower_model_checks": mm["n_model_checks"],
        # amendment A1.2: the changer's own model on the lead side (2026-10-06)
        "n_changer_model_checks": mm["n_lead_model_checks"],
        "n_relax_granted_entrant": mm["n_relax_entrant"],
        "n_relax_granted_follower": mm["n_relax_follower"],
        "n_cooperations": ws["n_cooperations"],
        "mean_follower_decel_ms2": (
            ws["coop_decel_sum"] / ws["n_cooperations"] if ws["n_cooperations"] else None
        ),
        "n_changer_eased": ws["n_changer_eased"],
        "n_ceiling_vehicle_steps": mm["n_ceiling_steps"],
        "n_handovers": ws["n_handovers"],
        # amendment A2.2 (2026-10-06): exiters LC2013 moved towards the exit in
        # the step they reached the zone, the change kept
        "n_arrival_changes_kept": mm["n_arrival_changes_kept"],
        "n_early_crossings": mm["n_early_crossings"],
        "crossing_x_in_m_p10_p50_p90": _q(mm["cross_x_in"]),
        "crossing_x_out_m_p10_p50_p90": _q(mm["cross_x_out"]),
        "n_pair_releases": ws["n_pair_releases"],
        "n_vacated": ws["n_vacated"],
        "n_vacate_refused": ws["n_vacate_refused"],
        "n_vacate_skipped_no_gap": ws["n_vacate_skipped_no_gap"],
        "n_vacate_requests": ws["n_vacate_requests"],
        "n_exit_prepared": ws["n_exit_prepared"],
        "n_exited": len(ws["exited"]),
        "n_reached_section_exiting": len(ws["reached"]),
        "n_departed_exiting": (
            sum(n for rid, n in n_departed_by_route.items() if _route_exit(rid) == ws["off_index"])
            if ws["off_index"] >= 0
            else 0
        ),
        "n_collisions_attributable": mm["n_collisions"],
        "wait_s_mean": _mean(waits),
        "wait_in_s_mean": _mean(ws["waits_in_s"]),
        "wait_out_s_mean": _mean(ws["waits_out_s"]),
    }


def _measured_run_meta(run: dict[str, Any]) -> dict[str, Any]:
    """``meta.json["measured_merge_model"]``: the parameter artifact, the set and the relaxations."""
    params: merge_model.MergeModelParams = run["params"]
    return {
        "params_artifact": params.artifact,
        "params_sha256": params.artifact_sha256,
        "parameter_set": params.name,
        "values": params.summary(),
        "lane_end_giveup_m": run.get("lane_end_giveup_m"),
        "relaxation": {
            "n_granted_entrant": run["n_relax_granted_entrant"],
            "n_granted_follower": run["n_relax_granted_follower"],
            "n_regranted": run["n_relax_regranted"],
            "n_restored_expired": run["n_relax_restored_expired"],
            "n_restored_lane_change": run["n_relax_restored_lane_change"],
            "n_restored_left": run["n_relax_restored_left"],
            "n_active_at_end": len(run["relax"]),
            "n_cut_ins_ahead_of_relaxed": run["n_relaxed_cut_ins"],
            "min_tau_set_s": run["min_tau_set_s"],
            "n_tau_writes": run["n_tau_writes"],
        },
        "n_av_commands_withdrawn": run["n_av_released"],
    }


# --- The lane-end give-up (OSMNetwork.lane_end_giveup_m, WP-71) ------------


def _lane_end_diverges(
    net: Any,
    chain: Sequence[str],
    ramps: Sequence[RampSpec],
    skip_edges: Collection[str],
    offsets: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Every diverge of the corridor the lane-end give-up acts at (WP-71).

    A diverge is a corridor edge whose lanes do not all lead to the same
    edges: some lane leads to only part of them (exit-only lanes beside
    through lanes). A lane whose successors are exactly one edge has a
    continuation of its own, recorded here:

    * the next corridor edge: ``"through"``, rerouted to the corridor's last
      edge;
    * the first edge of one of the scenario's off-ramps: ``"exit"``,
      rerouted to that ramp's last edge.

    A lane with no successor (a lane drop, an acceleration lane), one leading
    to several edges, and every edge in ``skip_edges`` (the weaving sections,
    which keep their own give-up) are never acted on.

    Args:
        net: The compiled network (``sumolib.net.readNet``).
        chain: Corridor edge ids in driving order, ramp splits expanded; its
            last edge is the corridor's end.
        ramps: The scenario's ramps with attach edges resolved to their
            compiled pieces (``_resolve_ramp_pieces``), in config order.
        skip_edges: Corridor edges the rule never acts on.
        offsets: Trajectory ``x`` of each corridor edge's start [m].

    Returns:
        Per diverge, in driving order: ``edge``, ``x_end_m``, ``lane_len_m``
        and ``succ`` (per lane index: its length [m] and its successor
        edges), ``cont`` (per lane index with a continuation: ``(kind,
        target edge, destination label)``), and the counters
        ``n_gave_up_exit`` / ``n_took_exit``.
    """
    exit_of = {r.edges[0]: (r.edges[-1], r.name or r.attach_edge) for r in ramps if r.kind == "off"}
    out: list[dict[str, Any]] = []
    for i, eid in enumerate(chain[:-1]):
        if eid in skip_edges:
            continue
        nxt = chain[i + 1]
        lanes = net.getEdge(eid).getLanes()
        succ = {
            int(ln.getIndex()): frozenset(c.getTo().getID() for c in ln.getOutgoing())
            for ln in lanes
        }
        every = frozenset().union(*succ.values())
        if len(every) < 2:
            continue
        cont: dict[int, tuple[str, str, str]] = {}
        for j, s in succ.items():
            if len(s) != 1 or s == every:
                continue
            (to,) = s
            if to == nxt:
                cont[j] = ("through", chain[-1], DESTINATION_CORRIDOR_END)
            elif to in exit_of:
                cont[j] = ("exit", *exit_of[to])
        if not cont:
            continue
        length = float(net.getEdge(eid).getLength())
        out.append(
            {
                "edge": eid,
                "x_end_m": float(offsets[eid]) + length if eid in offsets else None,
                "lane_len_m": {int(ln.getIndex()): float(ln.getLength()) for ln in lanes},
                "succ": succ,
                "cont": cont,
                "n_gave_up_exit": 0,
                "n_took_exit": 0,
            }
        )
    return out


def _lane_end_step(
    mod: Any,
    tc: Any,
    le: dict[str, Any],
    results: Any,
    controlled: Callable[[str], bool],
    index: _RoadIndex | None = None,
) -> list[tuple[str, str]]:
    """One step of the lane-end give-up (``OSMNetwork.lane_end_giveup_m``, WP-71).

    Derived from VM T (docs/ONBOARDING_MNDOT.md §11), the I-94 WB lock at
    the T.H.61 → 18207912 gore, a two-lane weave the weave model does not
    cover. The frontmost vehicle of the lock was an exiter held by SUMO at
    the end of through lane 2. T.H.61 entrants bound on stood at the end of
    the exit-only lanes beside it. Each needs the other's lane and nothing
    frees either. At the two weaving sections the exit-side give-up
    (:func:`_weave_step`) removes the exiter half of that state; this rule is
    its general form, at every diverge the weaving sections do not cover, for
    both halves.

    A vehicle on a diverge (:func:`_lane_end_diverges`) is given up when all
    of these hold this step:

    * it is halted (below ``HALTING_SPEED_MS``);
    * it is the front vehicle of its lane (none of the vehicles subscribed
      stands ahead of it there, so it is not queued);
    * it is within ``le["distance_m"]`` of the lane's end;
    * its route's next edge is not one its lane leads to, but another lane
      of the edge does;
    * SUMO's lane-change model reports the change toward the nearest such
      lane blocked (``LCA_BLOCKED``) this step; a vehicle SUMO has no state
      for yet (``LCA_UNKNOWN``, e.g. in its insertion step) is left to the
      next step;
    * no weaving section or scripted merge commands it (``controlled``).

    The vehicle is then rerouted (``vehicle.changeTarget``) to its lane's
    own continuation: an exiter on a through lane to the corridor's last
    edge (its exit given up, ``n_gave_up_exit``), a vehicle bound elsewhere
    on an exit-only lane to the off-ramp's last edge (the exit taken,
    ``n_took_exit``). This is a route change, not a lane change: the
    vehicle's lane already leads to its new route's next edge, and SUMO
    moves it on in that lane. Every rule above is read from the state before
    any reroute this step, and candidates are taken in ``veh_id`` order, so
    the step is deterministic.

    Args:
        mod: libsumo / traci module.
        tc: Its constants.
        le: The rule's state: ``distance_m``, ``by_edge`` (diverge edge →
            its record from :func:`_lane_end_diverges`, whose counters are
            incremented here).
        results: This step's subscription results.
        controlled: Whether a weaving section or scripted merge commands a
            vehicle (such a vehicle is never acted on).

    Returns:
        ``(veh_id, destination label)`` of every vehicle rerouted this step.
    """
    by_edge: dict[str, dict[str, Any]] = le["by_edge"]
    d_max = float(le["distance_m"])
    front: dict[tuple[str, int], float] = {}
    cand: list[tuple[str, str, int, float]] = []
    for vid, res in results.items() if index is None else index.on(by_edge):
        road = res[tc.VAR_ROAD_ID]
        rec = by_edge.get(road)
        if rec is None:
            continue
        lane = int(res[tc.VAR_LANE_INDEX])
        pos = float(res[tc.VAR_LANEPOSITION])
        key = (road, lane)
        if pos > front.get(key, -math.inf):
            front[key] = pos
        if (
            lane in rec["cont"]
            and float(res[tc.VAR_SPEED]) < HALTING_SPEED_MS
            and rec["lane_len_m"][lane] - pos <= d_max
        ):
            cand.append((vid, road, lane, pos))
    out: list[tuple[str, str]] = []
    for vid, road, lane, pos in sorted(cand):
        if pos < front[(road, lane)] or controlled(vid):
            continue
        rec = by_edge[road]
        route = mod.vehicle.getRoute(vid)
        idx = int(mod.vehicle.getRouteIndex(vid))
        if idx < 0 or idx + 1 >= len(route) or route[idx] != road:
            continue
        nxt = route[idx + 1]
        succ: dict[int, frozenset[str]] = rec["succ"]
        if nxt in succ[lane]:
            continue  # its route continues on its lane
        toward = [j for j in sorted(succ) if nxt in succ[j]]
        if not toward:
            continue  # no lane of this edge reaches its route: not a lane-end state
        nearest = min(toward, key=lambda j: (abs(j - lane), j))
        state = int(mod.vehicle.getLaneChangeState(vid, 1 if nearest > lane else -1)[0])
        if state == tc.LCA_UNKNOWN or not state & tc.LCA_BLOCKED:
            continue  # SUMO may still make the change, or has not read it yet
        kind, target, label = rec["cont"][lane]
        mod.vehicle.changeTarget(vid, target)
        rec["n_gave_up_exit" if kind == "through" else "n_took_exit"] += 1
        out.append((vid, label))
    return out


def _commanded_by_runner(
    weave_states: Sequence[dict[str, Any]],
    scripted_states: Sequence[dict[str, Any]],
    vid: str,
) -> bool:
    """Whether a weaving section or a scripted merge commands ``vid`` now.

    A weaving section commands the vehicles it drives (``veh``), the
    approaching entrants it anticipates (``pre``), the through vehicles its
    vacate rule holds (``vacate``) and the exiters its early move holds
    (``prep``); a measured zone also the entrants taken before it
    (``handover``); a scripted merge its changers (``veh``). The lane-end
    give-up never acts on such a vehicle (WP-71).
    """
    return any(
        vid in ws["veh"]
        or vid in ws["pre"]
        or vid in ws["vacate"]
        or vid in ws["prep"]
        or vid in ws.get("handover", ())
        for ws in weave_states
    ) or any(vid in ss["veh"] for ss in scripted_states)


def _lane_end_meta(le: dict[str, Any] | None) -> dict[str, Any] | None:
    """``meta.json["lane_end_giveups"]``: ``None`` when the rule is off."""
    if le is None:
        return None
    rows = [
        {
            "edge": rec["edge"],
            "x_end_m": rec["x_end_m"],
            "through_lanes": sorted(j for j, c in rec["cont"].items() if c[0] == "through"),
            "exit_lanes": sorted(j for j, c in rec["cont"].items() if c[0] == "exit"),
            "exits": sorted({c[2] for c in rec["cont"].values() if c[0] == "exit"}),
            "n_gave_up_exit": rec["n_gave_up_exit"],
            "n_took_exit": rec["n_took_exit"],
        }
        for rec in le["diverges"]
    ]
    return {
        "distance_m": float(le["distance_m"]),
        "skipped_edges": sorted(le["skipped_edges"]),
        "n_gave_up_exit": sum(r["n_gave_up_exit"] for r in rows),
        "n_took_exit": sum(r["n_took_exit"] for r in rows),
        "diverges": rows,
    }


def _leader_obs(
    lib_mod: Any, veh_id: str, ego_min_gap: float, *, close_leader: bool = False
) -> tuple[float, float, bool]:
    """(bumper-to-bumper gap [m], leader speed [m/s], leader within ``s0``).

    ``vehicle.getLeader`` returns the distance from the ego front bumper
    **plus minGap** to the leader's back (verified against SUMO 1.27;
    ``MSVehicle::getLeader``, ``MSVehicle.cpp`` 6755–6781), so the ego's
    drawn ``s0`` is added back to obtain the bumper-to-bumper gap the
    controller contract requires. No leader within ``LEADER_LOOKAHEAD_M``:
    ``(inf, nan, False)``.

    A leader closer than ``s0`` bumper to bumper makes that value negative.
    With ``close_leader`` False (``AVSpec.observe_close_leader`` false, its
    default until 2026-10-04) it is read as "no leader": ``(inf, nan, True)``,
    with no further TraCI call, as before WP-96. With ``close_leader`` the leader is reported with
    the bumper gap floored at 0 m: ``(max(gap + s0, 0), v_leader, True)``.

    Args:
        lib_mod: The ``libsumo`` / ``traci`` module.
        veh_id: The controlled vehicle.
        ego_min_gap: Its ``minGap`` [m] (the drawn ``s0``).
        close_leader: Report a leader within ``s0`` (``AVSpec.observe_close_leader``).

    Returns:
        ``(gap, v_leader, within_s0)``; ``within_s0`` is True whenever a
        leader exists at a bumper gap below ``s0``, whichever way it is reported.
    """
    lead = lib_mod.vehicle.getLeader(veh_id, LEADER_LOOKAHEAD_M)
    if lead is None or lead[0] == "":
        return math.inf, math.nan, False
    if lead[1] < 0.0:
        if not close_leader:
            return math.inf, math.nan, True
        return max(lead[1] + ego_min_gap, 0.0), float(lib_mod.vehicle.getSpeed(lead[0])), True
    return lead[1] + ego_min_gap, float(lib_mod.vehicle.getSpeed(lead[0])), False


def _off_corridor_step(
    lib_mod: Any,
    tc: Any,
    results: Mapping[str, Any],
    oc: dict[str, Any],
    corridor: Collection[str],
    held_by_merge: Callable[[str], bool],
    hb: dict[str, Any] | None,
) -> None:
    """One step of the off-corridor bookkeeping (``AVSpec.release_off_corridor``).

    ``oc["commanded"]`` holds the AVs the dispatch has commanded and not
    released. For each, in id order: one no longer in the network is dropped;
    one on a corridor edge or an internal junction edge (id starting with
    ``":"``) is left alone. One on any other edge has left the corridor
    holding its last command (a ``setSpeed`` target is held until
    ``setSpeed(-1)``). With ``oc["release"]`` False
    (``AVSpec.release_off_corridor`` false, its default until 2026-10-04) it
    is only counted: ``oc["left"]`` (AVs) and ``oc["n_vehicle_steps"]`` (steps spent
    so), with no TraCI call. With ``oc["release"]`` True it is released
    (``setSpeed(-1)``, ``oc["n_released"]``), dropped from ``commanded`` and,
    when the handback is on, from its held commands, so that nothing re-applies
    the command; an AV that a scripted merge or weaving section commands at
    that moment (``held_by_merge``) is left to it and visited again next step.

    Args:
        lib_mod: The ``libsumo`` / ``traci`` module.
        tc: Its ``constants``.
        results: This step's subscription results (every vehicle in the
            network, ramps included).
        oc: The run's off-corridor state (see above).
        corridor: The corridor's edge ids (the keys of the linear-x offsets).
        held_by_merge: Whether a scripted merge or weaving section commands
            a vehicle now.
        hb: The run's handback state, or None when the handback is off.
    """
    commanded: set[str] = oc["commanded"]
    for vid in sorted(commanded):
        res = results.get(vid)
        if res is None:
            commanded.discard(vid)
            continue
        road = res[tc.VAR_ROAD_ID]
        if road in corridor or road.startswith(":"):
            continue
        oc["left"].add(vid)
        if not oc["release"]:
            oc["n_vehicle_steps"] += 1
            continue
        if held_by_merge(vid):
            continue
        lib_mod.vehicle.setSpeed(vid, -1.0)
        commanded.discard(vid)
        oc["n_released"] += 1
        if hb is not None:
            hb["held"].pop(vid, None)
            hb["in_force"].discard(vid)


#: SUMO 1.27.1's IDM lets ``minNextSpeed`` brake at no less than this
#: [m/s²] (capped by ``emergencyDecel``), "to permit exceeding decel when
#: approaching stops" (``MSCFModel_IDM.cpp`` 80–89).
IDM_MIN_NEXT_SPEED_DECEL: Final[float] = 1.5
#: Tolerance [m/s] below which a follow speed is not read as under the floor.
HANDBACK_EPS_MS: Final[float] = 1e-9


def _command_decel(model: str, decel: float, emergency_decel: float) -> float:
    """Strongest deceleration [m/s²] a held ``setSpeed`` command can reach.

    Under the default speed mode, ``MSVehicle::processTraCISpeedControl``
    (SUMO 1.27.1, ``MSVehicle.cpp`` 4014–4043) hands
    ``cfModel.minNextSpeed(v)`` to ``Influencer::influenceSpeed``
    (493–520), whose maximum-deceleration clamp (speed mode bit 2) is
    applied after, and therefore overrides, the safe-speed clamp (bit 0).
    ``minNextSpeed`` brakes at ``max(decel, min(emergencyDecel, 1.5))`` for
    SUMO's IDM (``MSCFModel_IDM.cpp`` 80–89) and at ``decel`` for every
    other model, EIDM included (``MSCFModel.cpp`` 330–338).

    Args:
        model: The vType's ``carFollowModel`` (``FleetSpec.model``).
        decel: The vehicle's ``decel`` [m/s²] (its drawn ``b``).
        emergency_decel: The vehicle's ``emergencyDecel`` [m/s²].

    Returns:
        The deceleration bound of a commanded vehicle [m/s²].
    """
    if model == "IDM":
        return max(decel, min(emergency_decel, IDM_MIN_NEXT_SPEED_DECEL))
    return decel


def _handback_needed(
    lib_mod: Any, veh_id: str, v: float, command_decel: float, step_s: float
) -> bool:
    """Whether the car-following model must brake harder than a command can.

    Asks the vehicle's own model for its follow speed behind its current
    leader (``vehicle.getFollowSpeed``: the model's ``followSpeed`` for this
    gap, speed and leader, the constraint that dominates ``planMove``'s safe
    speed in the coming step) and compares it with the lowest speed a held
    command can reach this step, ``v − command_decel · step_s``
    (:func:`_command_decel`). No leader within ``LEADER_LOOKAHEAD_M``: False.
    A negative gap (``getLeader`` subtracts the ego's ``minGap``) is passed on
    unchanged; the model reads it as a demand to stop.

    Args:
        lib_mod: The ``libsumo`` / ``traci`` module.
        veh_id: The commanded vehicle.
        v: Its current speed [m/s].
        command_decel: Its :func:`_command_decel` [m/s²].
        step_s: Simulation step length [s].

    Returns:
        True when the command must be withdrawn for this step.
    """
    lead = lib_mod.vehicle.getLeader(veh_id, LEADER_LOOKAHEAD_M)
    if lead is None or lead[0] == "":
        return False
    leader, gap = lead[0], float(lead[1])
    v_follow = float(
        lib_mod.vehicle.getFollowSpeed(
            veh_id,
            v,
            gap,
            float(lib_mod.vehicle.getSpeed(leader)),
            float(lib_mod.vehicle.getDecel(leader)),
            leader,
        )
    )
    return v_follow < v - command_decel * step_s - HANDBACK_EPS_MS


def _emergency_handback_step(
    lib_mod: Any, tc: Any, results: Mapping[str, Any], hb: dict[str, Any], step_s: float
) -> None:
    """One step of ``AVSpec.emergency_handback`` over the AVs holding a command.

    ``hb["held"]`` maps each AV the dispatch has commanded to its last
    command [m/s]; ``hb["in_force"]`` holds those whose command SUMO is
    applying (the dispatch adds each AV it commands). For each held AV still
    in the network, in id order: when :func:`_handback_needed`, a command in
    force is withdrawn (``setSpeed(-1)``); otherwise a withdrawn command is
    re-applied. An AV that has left the network is dropped. Counts, in
    ``hb``: ``n_vehicle_steps`` (vehicle-steps without the command),
    ``n_withdrawals`` (commands withdrawn) and ``vehicles`` (AVs ever
    released).

    Args:
        lib_mod: The ``libsumo`` / ``traci`` module.
        tc: Its ``constants``.
        results: This step's subscription results (every vehicle in the
            network, ramps included).
        hb: The run's handback state (see above; ``model`` and a per-vehicle
            ``decel`` cache are kept in it too).
        step_s: Simulation step length [s].
    """
    held: dict[str, float] = hb["held"]
    in_force: set[str] = hb["in_force"]
    for vid in sorted(held):
        res = results.get(vid)
        if res is None:
            del held[vid]
            in_force.discard(vid)
            continue
        b_cmd = hb["decel"].get(vid)
        if b_cmd is None:
            b_cmd = _command_decel(
                hb["model"],
                float(lib_mod.vehicle.getDecel(vid)),
                float(lib_mod.vehicle.getEmergencyDecel(vid)),
            )
            hb["decel"][vid] = b_cmd
        if _handback_needed(lib_mod, vid, float(res[tc.VAR_SPEED]), b_cmd, step_s):
            if vid in in_force:
                lib_mod.vehicle.setSpeed(vid, -1.0)
                in_force.discard(vid)
                hb["n_withdrawals"] += 1
            hb["n_vehicle_steps"] += 1
            hb["vehicles"].add(vid)
        elif vid not in in_force:
            lib_mod.vehicle.setSpeed(vid, held[vid])
            in_force.add(vid)


def _downstream_bins(
    ego_x: float,
    xs: np.ndarray,
    vs: np.ndarray,
    total_length: float,
    is_ring: bool,
) -> tuple[float, ...]:
    """Mean speeds in ``DOWNSTREAM_BIN_M`` bins ahead of ``ego_x`` (JAD obs).

    Ring: distance-ahead is arc distance modulo the circumference (horizon
    capped at one lap). Corridor: capped at the remaining length. Empty bins
    are NaN per the contract.
    """
    if is_ring:
        horizon = min(DOWNSTREAM_HORIZON_M, total_length)
        ahead = (xs - ego_x) % total_length
    else:
        horizon = min(DOWNSTREAM_HORIZON_M, max(total_length - ego_x, 0.0))
        ahead = xs - ego_x
    n_bins = max(math.ceil(horizon / DOWNSTREAM_BIN_M), 1)
    sel = (ahead > 0.0) & (ahead <= horizon)
    idx = np.minimum((ahead[sel] // DOWNSTREAM_BIN_M).astype(np.int64), n_bins - 1)
    # ``bincount`` adds each weight to its bin in input order from 0.0 — the
    # additions ``np.add.at`` made here before 2026-10-07, so the same sums
    # bit for bit, at a fraction of the cost (this runs per AV and step). A
    # bin's mean is its sum over its count, NaN where the count is 0.
    sums = np.bincount(idx, weights=vs[sel], minlength=n_bins)
    counts = np.bincount(idx, minlength=n_bins)
    means = np.full(n_bins, np.nan)
    np.divide(sums, counts, out=means, where=counts > 0)
    return tuple(means.tolist())


def _stale_snapshot(
    history: deque[tuple[float, np.ndarray, np.ndarray]],
    t: float,
    delay_s: float,
    current: tuple[np.ndarray, np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    """The traffic-state snapshot a delayed oracle should see (CLAUDE.md §4.3).

    Returns the most recent snapshot at or before ``t - delay_s``. Falls back to
    the oldest snapshot held while the buffer is still shorter than the delay
    (start of run), and to ``current`` when there is no delay or no history —
    so a perfect oracle costs nothing.
    """
    if delay_s <= 0.0 or not history:
        return current
    t_target = t - delay_s
    chosen = (history[0][1], history[0][2])
    for snap_t, snap_xs, snap_v in history:
        if snap_t <= t_target:
            chosen = (snap_xs, snap_v)
        else:
            break
    return chosen


def _apply_oracle_noise(
    bins: tuple[float, ...], noise_frac: float, rng: np.random.Generator
) -> tuple[float, ...]:
    """Multiply each observed bin speed by ``1 + U(-f, +f)`` (CLAUDE.md §4.3).

    NaN bins (no vehicles) stay NaN: a noisy sensor still reports nothing where
    there is nothing. Speeds are floored at 0 so noise cannot invent reverse
    travel.
    """
    if noise_frac <= 0.0 or not bins:
        return bins
    arr = np.asarray(bins, dtype=float)
    factors = 1.0 + rng.uniform(-noise_frac, noise_frac, size=arr.shape)
    return tuple(float(v) for v in np.maximum(arr * factors, 0.0))


def _edie_edges_frame(
    traj: pd.DataFrame, sample_dt_s: float, duration_s: float, total_length_m: float
) -> pd.DataFrame:
    """Aggregate trajectories into Edie space-time bins (docs/CONTRACTS.md §3).

    Edie's generalized definitions over each ``EDGES_DT_BIN_S × EDGES_DX_BIN_M``
    bin of area ``|A| = Δt·Δx``: with total time spent ``TTS = Σ dt`` and total
    distance traveled ``TTD = Σ v·dt`` (approximated from samples at the
    output cadence), ``density = TTS/|A|`` [veh/m], ``flow = TTD/|A|``
    [veh/s], ``mean_speed = TTD/TTS`` [m/s] (NaN when the bin is empty).
    ``t_bin``/``x_bin`` are bin centers. The full grid over
    ``[0, duration] × [0, total_length]`` is emitted (empty bins: density 0,
    flow 0, speed NaN). Trailing partial bins (a ring circumference or
    duration that is not a bin multiple) use their *actual* covered area so
    densities are not diluted by phantom road.

    Args:
        traj: Trajectory samples with columns ``t`` [s], ``x`` [m], ``v`` [m/s].
        sample_dt_s: The **realized** interval between consecutive samples of
            one vehicle [s], i.e. ``out_every · step_length_s``, NOT the
            nominal ``1/sim.output_hz``: the runner samples on whole steps, so
            a requested rate that does not divide the step length is rounded
            down and weighting by the nominal interval would scale every
            density and flow by ``nominal/realized`` (a 4 Hz request at a
            0.5 s step halved them).
        duration_s: Simulated duration [s] (grid extent in time).
        total_length_m: Road length [m] (grid extent in space).
    """
    acc = _EdieAccumulator(sample_dt_s, duration_s, total_length_m)
    if len(traj):
        acc.add(traj["t"].to_numpy(), traj["x"].to_numpy(), traj["v"].to_numpy())
    return acc.frame()


class _EdieAccumulator:
    """The Edie bins of :func:`_edie_edges_frame`, filled chunk by chunk.

    The runner feeds it each trajectory row group as the group is written
    (:class:`_TrajectoryWriter`), so no ``(t, x, v)`` copy of the whole run
    is held until the end (24 B per row: ≈ 2.5 GB on a 106 M-row four-hour
    corridor, and as much again for the frame the old code concatenated
    them into). ``np.add.at`` adds unbuffered, one row at a time in the
    order given, so feeding the rows in file order in any number of chunks
    performs exactly the additions of one call over all of them: every bin
    sum, and so ``edges.parquet``, is bit-identical (2026-10-07,
    docs/PERFORMANCE_2026-10-07.md).
    """

    def __init__(self, sample_dt_s: float, duration_s: float, total_length_m: float) -> None:
        self.sample_dt_s = sample_dt_s
        self.duration_s = duration_s
        self.total_length_m = total_length_m
        self.nt = max(math.ceil(duration_s / EDGES_DT_BIN_S), 1)
        self.nx = max(math.ceil(total_length_m / EDGES_DX_BIN_M), 1)
        self.tts = np.zeros((self.nt, self.nx))
        self.ttd = np.zeros((self.nt, self.nx))

    def add(self, t: np.ndarray, x: np.ndarray, v: np.ndarray) -> None:
        """Add samples ``t`` [s], ``x`` [m], ``v`` [m/s] (float64, in row order)."""
        if not len(t):
            return
        ti = np.minimum((t / EDGES_DT_BIN_S).astype(np.int64), self.nt - 1)
        xi = np.clip((x / EDGES_DX_BIN_M).astype(np.int64), 0, self.nx - 1)
        np.add.at(self.tts, (ti, xi), self.sample_dt_s)
        np.add.at(self.ttd, (ti, xi), v * self.sample_dt_s)

    def frame(self) -> pd.DataFrame:
        """The full grid as the contract's edges frame (see :func:`_edie_edges_frame`)."""
        nt, nx, tts, ttd = self.nt, self.nx, self.tts, self.ttd
        sample_dt_s, duration_s, total_length_m = (
            self.sample_dt_s,
            self.duration_s,
            self.total_length_m,
        )
        dt_eff = np.minimum(EDGES_DT_BIN_S, duration_s - np.arange(nt) * EDGES_DT_BIN_S).clip(
            min=sample_dt_s
        )
        dx_eff = np.minimum(EDGES_DX_BIN_M, total_length_m - np.arange(nx) * EDGES_DX_BIN_M).clip(
            min=1e-9
        )
        area = np.outer(dt_eff, dx_eff)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean_speed = np.where(tts > 0, ttd / np.where(tts > 0, tts, 1.0), np.nan)
        t_centers = np.arange(nt) * EDGES_DT_BIN_S + dt_eff / 2.0
        x_centers = np.arange(nx) * EDGES_DX_BIN_M + dx_eff / 2.0
        tt, xx = np.meshgrid(t_centers, x_centers, indexing="ij")
        return pd.DataFrame(
            {
                "t_bin": tt.ravel(),
                "x_bin": xx.ravel(),
                "mean_speed": mean_speed.ravel(),
                "density": (tts / area).ravel(),
                "flow": (ttd / area).ravel(),
            }
        )


def _versions() -> dict[str, str]:
    """Package versions recorded in every meta.json (CLAUDE.md §0.5)."""
    from importlib.metadata import PackageNotFoundError, version

    def _v(dist: str) -> str:
        try:
            return version(dist)
        except PackageNotFoundError:  # pragma: no cover - odd installs
            return "unknown"

    import flowstate_core

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "pyarrow": pa.__version__,
        "eclipse-sumo": _v("eclipse-sumo"),
        "libsumo": _v("libsumo"),
        "flowstate_core": getattr(flowstate_core, "__version__", "unknown"),
        "microsim": _v("microsim"),
    }


_TRAJ_SCHEMA_BASE: Final[list[tuple[str, pa.DataType]]] = [
    ("t", pa.float64()),
    ("veh_id", pa.string()),
    ("x", pa.float64()),
    ("lane", pa.int32()),
    ("v", pa.float64()),
    ("a", pa.float64()),
    ("is_av", pa.bool_()),
    ("complied", pa.bool_()),
    ("is_heavy", pa.bool_()),
    ("is_hov", pa.bool_()),
]

#: Per-vehicle table written beside the trajectories (docs/CONTRACTS.md §3,
#: WP-69): one row per vehicle that departed, in ``veh_id`` order. Read with
#: ``validation.vehicles.read_vehicles``.
VEHICLES_FILE: Final[str] = "vehicles.parquet"

#: ``origin`` of a vehicle that entered on the mainline (not by an on-ramp).
ORIGIN_MAINLINE: Final[str] = "mainline"

#: ``destination`` of a vehicle whose route ends at the corridor's last edge
#: (not by an off-ramp); also the rerouted destination of a weave give-up and
#: of a lane-end give-up of an exit (WP-71).
DESTINATION_CORRIDOR_END: Final[str] = "corridor_end"

#: Columns of :data:`VEHICLES_FILE` (:func:`_vehicle_table`). ``origin_ramp``
#: / ``destination_ramp`` index ``meta.json["ramps"]`` (``-1`` = mainline /
#: corridor end); ``entry_*`` / ``last_*`` are the vehicle's first and last
#: rows of ``trajectories.parquet`` (null when it has none); ``gave_up`` marks
#: an exiter a weaving section rerouted through (``weave_sections[i]
#: .n_missed_exit``), an entrant it rerouted to the paired exit (amendment W1,
#: ``weave_sections[i].n_entrant_took_exit``) or a vehicle the lane-end
#: give-up rerouted to its lane's own continuation (``lane_end_giveups``,
#: WP-71), ``destination`` keeping its planned destination and
#: ``destination_final`` the one it drove to.
_VEHICLES_SCHEMA: Final[list[tuple[str, pa.DataType]]] = [
    ("veh_id", pa.string()),
    ("route", pa.string()),
    ("origin", pa.string()),
    ("origin_ramp", pa.int32()),
    ("destination", pa.string()),
    ("destination_ramp", pa.int32()),
    ("depart_planned_s", pa.float64()),
    ("depart_s", pa.float64()),
    ("entry_t_s", pa.float64()),
    ("entry_x_m", pa.float64()),
    ("entry_lane", pa.int32()),
    ("last_t_s", pa.float64()),
    ("last_x_m", pa.float64()),
    ("last_lane", pa.int32()),
    ("arrived", pa.bool_()),
    ("gave_up", pa.bool_()),
    ("gave_up_s", pa.float64()),
    ("destination_final", pa.string()),
]

#: The measured merge model's per-driver critical gaps [s] (2026-10-05,
#: docs/MERGE_MODEL.md, B§5.3), appended to :data:`VEHICLES_FILE` only in a
#: run with a ``measured`` ramp: column → (side, movement). The exiting
#: movement has no lead time gate, so it has no lead column.
DRIVER_GAP_COLUMNS: Final[dict[str, tuple[str, str]]] = {
    "tc_lead_merge_s": ("lead", "entering_merge"),
    "tc_lag_merge_s": ("lag", "entering_merge"),
    "tc_lead_weave_s": ("lead", "entering_weave"),
    "tc_lag_weave_s": ("lag", "entering_weave"),
    "tc_lag_exit_s": ("lag", "exiting_weave"),
}

#: The run's demand ledger (WP-105, docs/FRISCO_PROTOCOL.md §8.2): one row
#: per vehicle of the fleet plan — whether or not it ever entered the network —
#: in ``veh_id`` order, written beside :data:`VEHICLES_FILE` (whose bytes and
#: schema stay as they were). It carries what a fair strategy comparison needs
#: and the trajectories cannot give: the planned and actual departure (the
#: insertion backlog), the arrival, the time a ramp meter held the vehicle, and
#: its route's free-flow time. Read with ``validation.metrics.read_journeys``;
#: the windowed measures are ``validation.metrics.compute_waiting_metrics``.
JOURNEYS_FILE: Final[str] = "journeys.parquet"

#: Columns of :data:`JOURNEYS_FILE` (:func:`_journey_table`; the exact
#: definitions are on ``validation.metrics.JOURNEY_COLUMNS``). Every source is
#: bookkeeping the run already does or a read-only TraCI query; nothing here
#: drives the simulation.
_JOURNEYS_SCHEMA: Final[list[tuple[str, pa.DataType]]] = [
    ("veh_id", pa.string()),
    ("route", pa.string()),
    ("origin_ramp", pa.int32()),
    ("depart_planned_s", pa.float64()),
    ("route_length_m", pa.float64()),
    ("free_flow_s", pa.float64()),
    ("inserted", pa.bool_()),
    ("depart_s", pa.float64()),
    ("insert_offset_m", pa.float64()),
    ("arrived", pa.bool_()),
    ("arrival_s", pa.float64()),
    ("distance_end_m", pa.float64()),
    ("free_flow_covered_s", pa.float64()),
    ("meter_ramp", pa.int32()),
    ("meter_hold_start_s", pa.float64()),
    ("meter_released", pa.bool_()),
    ("meter_release_s", pa.float64()),
    ("meter_wait_s", pa.float64()),
]


@dataclass(frozen=True)
class RouteGeometry:
    """One named route's edges with their lengths and base speed limits.

    Read once, right after SUMO starts and before the boundary schedule or a
    VSL posts any limit (:func:`_route_geometry`), so the limits are the
    compiled network's own: every strategy arm of one scenario sees the same
    geometry, and a vehicle's free-flow time does not depend on the strategy.

    Attributes:
        edges: Edge ids in route order.
        lengths_m: Length of each edge [m] (lane 0; internal junction lanes
            between edges are not counted, so the route length errs short).
        limits_ms: Base speed limit of each edge [m/s], the largest over its
            lanes.
    """

    edges: tuple[str, ...]
    lengths_m: tuple[float, ...]
    limits_ms: tuple[float, ...]

    @property
    def length_m(self) -> float:
        """Route length [m]: the sum of :attr:`lengths_m`."""
        return float(sum(self.lengths_m))

    def offset_m(self, edge: str, lane_pos_m: float) -> float | None:
        """Distance along the route to a position on one of its edges [m].

        Args:
            edge: Edge the vehicle is on (its first occurrence in the route).
            lane_pos_m: Position on that edge [m].

        Returns:
            The cumulative length of the route's edges before ``edge`` plus
            ``lane_pos_m``; None when ``edge`` is not on the route.
        """
        if edge not in self.edges:
            return None
        k = self.edges.index(edge)
        return float(sum(self.lengths_m[:k])) + float(lane_pos_m)

    def free_flow_s(self, v0_ms: float, speed_factor: float = 1.0) -> float:
        """Free-flow travel time of the whole route for one vehicle [s].

        ``Σ_e L_e / min(v0, f · v_limit,e)``: each edge at the vehicle's
        desired speed there — the smaller of its vType ``maxSpeed`` and its
        SUMO ``speedFactor`` ``f`` times the edge's base limit (SUMO's desired
        free-flow speed; ``f`` is 1 unless ``FleetSpec.speed_factor`` sets
        it, WP-109) — the fastest the vehicle drives there on an empty road,
        acceleration aside.

        Args:
            v0_ms: The vehicle's desired speed [m/s], > 0.
            speed_factor: The vehicle's ``speedFactor`` (``FleetPlan``).

        Returns:
            The free-flow time [s].
        """
        lengths = np.asarray(self.lengths_m, dtype=np.float64)
        limits = np.asarray(self.limits_ms, dtype=np.float64)
        return float(np.sum(lengths / np.minimum(float(v0_ms), float(speed_factor) * limits)))

    def free_flow_between_s(
        self, a_m: float, b_m: float, v0_ms: float, speed_factor: float = 1.0
    ) -> float:
        """Free-flow time from route offset ``a_m`` to ``b_m`` for one vehicle [s].

        Edge by edge, each overlap of ``[a_m, b_m]`` with an edge at
        ``min(v0, f · v_limit,e)`` (:meth:`free_flow_s` restricted to the
        stretch). Offsets are clipped to ``[0, length_m]``; ``b_m <= a_m``
        gives 0.

        Args:
            a_m: Start offset along the route [m].
            b_m: End offset along the route [m].
            v0_ms: The vehicle's desired speed [m/s], > 0.
            speed_factor: The vehicle's ``speedFactor`` (``FleetPlan``).

        Returns:
            The free-flow time [s].
        """
        lengths = np.asarray(self.lengths_m, dtype=np.float64)
        limits = np.asarray(self.limits_ms, dtype=np.float64)
        starts = np.concatenate([np.zeros(1), np.cumsum(lengths)[:-1]])
        lo = np.clip(float(a_m), starts, starts + lengths)
        hi = np.clip(float(b_m), starts, starts + lengths)
        covered = np.maximum(hi - lo, 0.0)
        return float(np.sum(covered / np.minimum(float(v0_ms), float(speed_factor) * limits)))


def _route_geometry(mod: Any, route_ids: Iterable[str]) -> dict[str, RouteGeometry]:
    """:class:`RouteGeometry` of every named route SUMO loaded (read-only TraCI).

    Called right after ``start``, before any limit is changed. A route id SUMO
    does not know (the ring's per-vehicle embedded routes: the plan names them
    all ``"main"``) is skipped; its vehicles get no free-flow time.

    Args:
        mod: The libsumo or traci module.
        route_ids: The plan's route ids.

    Returns:
        Route id → geometry.
    """
    known = set(mod.route.getIDList())
    out: dict[str, RouteGeometry] = {}
    for rid in sorted(set(route_ids)):
        if rid not in known:
            continue
        edges = tuple(str(e) for e in mod.route.getEdges(rid))
        lengths: list[float] = []
        limits: list[float] = []
        for e in edges:
            n_lanes = int(mod.edge.getLaneNumber(e))
            lengths.append(float(mod.lane.getLength(f"{e}_0")))
            limits.append(max(float(mod.lane.getMaxSpeed(f"{e}_{i}")) for i in range(n_lanes)))
        out[rid] = RouteGeometry(edges, tuple(lengths), tuple(limits))
    return out


def _journey_table(
    veh_ids: Sequence[str],
    route_by_id: Mapping[str, str],
    depart_planned_s: Mapping[str, float],
    v0_by_id: Mapping[str, float],
    geometry: Mapping[str, RouteGeometry],
    depart_s: Mapping[str, float],
    insert_offset_m: Mapping[str, float | None],
    arrival_s: Mapping[str, float],
    distance_end_m: Mapping[str, float],
    meter_ramp: Mapping[str, int],
    meter_hold: Mapping[str, tuple[float, float | None]],
    meter_release: Mapping[str, tuple[float, float | None]],
    end_s: float,
    speed_factor_by_id: Mapping[str, float] | None = None,
) -> pa.Table:
    """The :data:`JOURNEYS_FILE` table: one row per planned vehicle.

    Pure bookkeeping over what the run already holds — nothing here reads or
    drives the simulation. Two columns are derived here, with the vehicle's
    route geometry (:meth:`RouteGeometry.free_flow_between_s`, edge by edge at
    ``min(v0, speedFactor × base limit)``; the factor is 1 unless the fleet
    sets one, WP-109):

    * ``free_flow_covered_s`` — the free-flow time of the stretch of route the
      vehicle covered: from its insertion offset to the route's end when it
      arrived, to ``insert_offset_m + distance_end_m`` when it was still in
      the network at the end, and 0 when it was never inserted. A vehicle a
      give-up rerouted (``vehicles.parquet`` ``gave_up``) is measured on its
      PLANNED route.
    * ``meter_wait_s`` — the delay a ramp meter caused while it held the
      vehicle: the time from the step the meter gave it its stop to the step
      it was released (to its arrival, or to the run's end when neither
      happened — censored), minus the free-flow time of the route stretch it
      covered in that time (its offset then is ``insert_offset_m`` plus its
      odometer, ``vehicle.getDistance``). 0 for a vehicle never held. Queueing
      behind the stop line counts, creeping included; the approach to the
      queue at free-flow speed does not.

    A held vehicle's offsets are None when its insertion offset is unknown;
    its ``meter_wait_s`` then falls back to the hold time alone (no
    free-flow part subtracted, which errs high).

    Args:
        veh_ids: Every vehicle of the fleet plan.
        route_by_id: Planned route id per vehicle (missing ⇒ ``"main"``).
        depart_planned_s: Planned departure per vehicle [s] (``FleetPlan``).
        v0_by_id: Desired speed per vehicle [m/s] (``FleetPlan.params``).
        geometry: Route id → :class:`RouteGeometry` (:func:`_route_geometry`).
        depart_s: SUMO departure time of every inserted vehicle [s].
        insert_offset_m: Distance along its route at which each inserted
            vehicle was put on the road [m] (front bumper; None when its first
            edge is not on the route).
        arrival_s: Step time at which SUMO reported each arrival [s].
        distance_end_m: Odometer [m] of every vehicle still in the network
            when the run ended (``vehicle.getDistance``).
        meter_ramp: Ramp index (``meta.json["ramps"]``) of the meter that gave
            each vehicle its stop.
        meter_hold: ``(step time, route offset)`` at which the meter gave each
            held vehicle its stop.
        meter_release: ``(step time, route offset)`` at which the meter
            released each vehicle.
        end_s: Simulation time when the run ended [s].
        speed_factor_by_id: SUMO ``speedFactor`` per vehicle
            (``FleetPlan.speed_factor``); None or a missing id ⇒ 1.0.

    Returns:
        The contract-typed table, rows in ``veh_id`` order.
    """
    cols: dict[str, list[Any]] = {name: [] for name, _ in _JOURNEYS_SCHEMA}
    for vid in sorted(veh_ids):
        rid = route_by_id.get(vid, "main")
        geom = geometry.get(rid)
        v0 = float(v0_by_id[vid])
        sf = float(speed_factor_by_id.get(vid, 1.0)) if speed_factor_by_id else 1.0
        offset = insert_offset_m.get(vid)
        arrived = vid in arrival_s
        # the route offset the vehicle reached: its end (arrived), its
        # odometer past the insertion offset (still running), unknown
        reached: float | None = None
        if geom is not None and offset is not None:
            reached = geom.length_m if arrived else offset + distance_end_m.get(vid, 0.0)
        covered: float | None
        if vid not in depart_s:
            covered = 0.0
        elif geom is None or offset is None or reached is None:
            covered = None
        else:
            covered = geom.free_flow_between_s(offset, reached, v0, sf)
        wait = 0.0
        if vid in meter_hold:
            t_hold, off_hold = meter_hold[vid]
            if vid in meter_release:
                t_out, off_out = meter_release[vid]
            elif arrived:
                t_out, off_out = arrival_s[vid], reached
            else:
                t_out, off_out = end_s, reached
            wait = t_out - t_hold
            if geom is not None and off_hold is not None and off_out is not None:
                wait -= geom.free_flow_between_s(off_hold, off_out, v0, sf)
        cols["veh_id"].append(vid)
        cols["route"].append(rid)
        cols["origin_ramp"].append(_route_origin(rid))
        cols["depart_planned_s"].append(float(depart_planned_s[vid]))
        cols["route_length_m"].append(None if geom is None else geom.length_m)
        cols["free_flow_s"].append(None if geom is None else geom.free_flow_s(v0, sf))
        cols["inserted"].append(vid in depart_s)
        cols["depart_s"].append(depart_s.get(vid))
        cols["insert_offset_m"].append(offset)
        cols["arrived"].append(arrived)
        cols["arrival_s"].append(arrival_s.get(vid))
        cols["distance_end_m"].append(distance_end_m.get(vid))
        cols["free_flow_covered_s"].append(covered)
        cols["meter_ramp"].append(meter_ramp.get(vid, -1))
        cols["meter_hold_start_s"].append(meter_hold[vid][0] if vid in meter_hold else None)
        cols["meter_released"].append(vid in meter_release)
        cols["meter_release_s"].append(meter_release[vid][0] if vid in meter_release else None)
        cols["meter_wait_s"].append(float(wait))
    schema = pa.schema(_JOURNEYS_SCHEMA)
    return pa.Table.from_arrays(
        [pa.array(cols[name], type=dtype) for name, dtype in _JOURNEYS_SCHEMA], schema=schema
    )


def _journeys_meta(table: pa.Table, end_s: float, n_routes_unknown: int) -> dict[str, Any]:
    """``meta.json["journeys"]``: whole-run totals of the :data:`JOURNEYS_FILE` table.

    Whole-run and unwindowed (the warm-up is not discarded here; the scored
    measures are ``validation.metrics.compute_waiting_metrics``). A vehicle
    never inserted counts ``end_s − depart_planned_s`` of insertion delay
    (censored at the run's end).

    Args:
        table: The journeys table.
        end_s: Simulation time when the run ended [s].
        n_routes_unknown: Plan route ids SUMO did not know (no geometry).

    Returns:
        The block.
    """
    planned = np.asarray(table.column("depart_planned_s").to_pylist(), dtype=np.float64)
    depart = table.column("depart_s").to_pylist()
    inserted = np.asarray(table.column("inserted").to_pylist(), dtype=np.bool_)
    out_t = np.asarray([end_s if d is None else float(d) for d in depart], dtype=np.float64)
    held = np.asarray(table.column("meter_ramp").to_pylist(), dtype=np.int64) >= 0
    released = np.asarray(table.column("meter_released").to_pylist(), dtype=np.bool_)
    return {
        "file": JOURNEYS_FILE,
        "end_s": float(end_s),
        "n_planned": int(table.num_rows),
        "n_inserted": int(inserted.sum()),
        "n_not_inserted": int((~inserted).sum()),
        "n_arrived": int(np.sum(table.column("arrived").to_pylist())),
        "insertion_delay_s_total": float(np.sum(out_t - planned)),
        "meter_wait_s_total": float(np.sum(table.column("meter_wait_s").to_pylist())),
        "n_meter_held": int(held.sum()),
        "n_meter_unreleased": int((held & ~released).sum()),
        "n_route_ids_without_geometry": int(n_routes_unknown),
    }


def _meter_wait_totals(table: pa.Table) -> dict[int, float]:
    """Summed ``meter_wait_s`` of the :data:`JOURNEYS_FILE` table per meter ramp index."""
    totals: dict[int, float] = {}
    ramps = table.column("meter_ramp").to_pylist()
    waits = table.column("meter_wait_s").to_pylist()
    for ramp, wait in zip(ramps, waits, strict=True):
        if ramp is not None and ramp >= 0:
            totals[int(ramp)] = totals.get(int(ramp), 0.0) + float(wait)
    return totals


#: Vehicle classes refused by a closed lane (every class this fleet can
#: carry; SUMO names that exist in every supported version).
CLOSURE_VCLASSES: Final[tuple[str, ...]] = (
    "passenger",
    "truck",
    "trailer",
    "bus",
    "delivery",
    "emergency",
    "motorcycle",
    "hov",
    "taxi",
)


def _write_parquet(table: pa.Table, path: Path) -> None:
    """Write a parquet file through an open file object.

    Passing a path would make pyarrow construct a ``LocalFileSystem``, which
    fails once libsumo's bundled libarrow is loaded in the process (duplicate
    ``file``-scheme registration, macOS symbol interposition). A file object
    bypasses filesystem resolution entirely.
    """
    with open(path, "wb") as f:
        pq.write_table(table, f)


TRAJ_FLUSH_ROWS: Final[int] = 500_000
"""Rows buffered before a trajectory row group is flushed to disk. A 7,800 s
four-lane corridor run captures ~10 M rows; holding them as Python lists
until the end costs several GB per process, which is what took a 16 GB
machine down with eight workers. Flushing in 500k-row groups bounds the
buffer at ~100 MB while the file content is unchanged."""


class _TrajectoryWriter:
    """Row-group streaming writer for the contract-typed trajectories table.

    Each output step is buffered as one chunk per column
    (:meth:`append_step`: numpy arrays of the step's rows, ``veh_id`` a
    list); :meth:`maybe_flush` concatenates the chunks and writes a Parquet
    row group (through an open file object, see :func:`_write_parquet`)
    once :data:`TRAJ_FLUSH_ROWS` rows are buffered, hands the group's
    ``(t, x, v)`` to the Edie accumulator of the post-run edges frame (when
    one is given) and keeps each vehicle's first and last row
    (``first_sample`` / ``last_sample``: ``(t, x, lane)``) for
    :data:`VEHICLES_FILE` — taken from the rows as they are written, so
    they are the file's first and last row of the vehicle by construction.

    Until 2026-10-07 every row was appended value by value into Python
    lists and the whole run's ``(t, x, v)`` was kept for the edges frame;
    the columns written are the same values in the same row groups, so the
    file is byte-identical (docs/PERFORMANCE_2026-10-07.md).
    """

    def __init__(self, path: Path, is_ring: bool, edie: _EdieAccumulator | None = None) -> None:
        fields = list(_TRAJ_SCHEMA_BASE)
        if is_ring:
            fields.append(("x_unwrapped", pa.float64()))
        self._fields = fields
        self.schema = pa.schema(fields)
        self._chunks: dict[str, list[Any]] = {name: [] for name, _ in fields}
        self._n_buffered = 0
        self._sink = open(path, "wb")
        self._writer = pq.ParquetWriter(self._sink, self.schema)
        self._edie = edie
        self.n_rows = 0
        self.first_sample: dict[str, tuple[float, float, int]] = {}
        self.last_sample: dict[str, tuple[float, float, int]] = {}

    def append_step(self, t: float, veh_id: Sequence[str], **columns: np.ndarray) -> None:
        """Buffer one output step's rows.

        Args:
            t: The step's time [s] (every row's ``t``).
            veh_id: The rows' vehicle ids, in row order.
            **columns: Every other schema column (``x``, ``lane``, ``v``,
                ``a``, the four flags, and ``x_unwrapped`` on a ring) as an
                array of ``len(veh_id)`` values in row order.
        """
        n = len(veh_id)
        if n == 0:
            return
        self._chunks["t"].append(np.full(n, t, dtype=np.float64))
        self._chunks["veh_id"].append(veh_id)
        for name, _ in self._fields[2:]:
            self._chunks[name].append(columns[name])
        self._n_buffered += n

    def _track_first_last(
        self, ids: Sequence[str], t: np.ndarray, x: np.ndarray, lane: np.ndarray
    ) -> None:
        """Update ``first_sample`` / ``last_sample`` from the rows being written.

        The rows are in time order, so within a row group a vehicle's last
        row is its last occurrence and its first row its first occurrence;
        across groups the earliest group holds the first row.
        """
        n = len(ids)
        last_at = {vid: i for i, vid in enumerate(ids)}
        first_at = {vid: i for i, vid in zip(range(n - 1, -1, -1), reversed(ids), strict=True)}

        def samples(at: dict[str, int]) -> Iterable[tuple[str, tuple[float, float, int]]]:
            idx = np.fromiter(at.values(), dtype=np.intp, count=len(at))
            rows = zip(t[idx].tolist(), x[idx].tolist(), lane[idx].tolist(), strict=True)
            return zip(at, rows, strict=True)

        self.last_sample.update(samples(last_at))
        first = self.first_sample
        for vid, row in samples(first_at):
            if vid not in first:
                first[vid] = row

    def maybe_flush(self, force: bool = False) -> None:
        n = self._n_buffered
        if n == 0 or (n < TRAJ_FLUSH_ROWS and not force):
            return
        ids = [vid for chunk in self._chunks["veh_id"] for vid in chunk]
        cols: dict[str, Any] = {"veh_id": ids}
        for name, _ in self._fields:
            if name != "veh_id":
                cols[name] = np.concatenate(self._chunks[name])
        self._track_first_last(ids, cols["t"], cols["x"], cols["lane"])
        arrays = [pa.array(cols[name], type=dtype) for name, dtype in self._fields]
        self._writer.write_table(pa.Table.from_arrays(arrays, schema=self.schema))
        if self._edie is not None:
            self._edie.add(cols["t"], cols["x"], cols["v"])
        self.n_rows += n
        self._n_buffered = 0
        for name in self._chunks:
            self._chunks[name] = []

    def close(self) -> None:
        """Flush the last row group and close the file."""
        self.maybe_flush(force=True)
        self._writer.close()
        self._sink.close()


def _vehicle_table(
    depart_s: Mapping[str, float],
    route_by_id: Mapping[str, str],
    depart_planned_s: Mapping[str, float],
    ramp_labels: Sequence[str],
    first_sample: Mapping[str, tuple[float, float, int]],
    last_sample: Mapping[str, tuple[float, float, int]],
    running: Collection[str],
    gave_up_s: Mapping[str, float],
    destination_final: Mapping[str, str] | None = None,
    driver_gaps: Mapping[str, merge_model.DriverGaps] | None = None,
) -> pa.Table:
    """The :data:`VEHICLES_FILE` table: one row per departed vehicle.

    Pure bookkeeping over what the run already holds — nothing here reads or
    drives the simulation.

    Args:
        depart_s: Every departed vehicle's SUMO departure time [s]
            (``vehicle.getDeparture`` when it was reported departed).
        route_by_id: Planned route id per vehicle (``"main"``, ``"on<k>"``,
            ``"main_off<j>"``, ``"on<k>_off<j>"``; missing ⇒ ``"main"``).
        depart_planned_s: Planned departure per vehicle [s] (``FleetPlan``).
        ramp_labels: Per ramp in config order, the label it is reported by
            (``RampSpec.name``, else its attach edge as compiled — the label
            ``meta.json["weave_sections"]`` uses).
        first_sample: ``(t, x, lane)`` of each vehicle's first trajectory row.
        last_sample: ``(t, x, lane)`` of each vehicle's last trajectory row.
        running: Vehicles still in the network when the run ended (not
            arrived).
        gave_up_s: Step time [s] at which a vehicle was first given up: a
            weaving section rerouted an exiter through or an entrant to its
            paired exit (``ws["gave_up"]``; the latter only under amendment
            W1), or the lane-end give-up (WP-71, :func:`_lane_end_step`)
            rerouted a vehicle to its lane's own continuation.
        destination_final: The destination label a lane-end give-up
            rerouted each vehicle to (its last, when rerouted twice), or a
            weaving section's entering give-up (amendment W1) its paired
            exit; a given-up vehicle absent from it drove to the corridor's
            end (a weaving section's exit give-up). ``None``: none.
        driver_gaps: The measured merge model's critical gaps per vehicle
            (2026-10-05, B§5.3: recorded in ``vehicles.parquet``); when given,
            the columns of :data:`DRIVER_GAP_COLUMNS` follow the contract's,
            and without it the table is exactly as before.

    Returns:
        The contract-typed table, rows in ``veh_id`` order.
    """
    cols: dict[str, list[Any]] = {name: [] for name, _ in _VEHICLES_SCHEMA}
    for vid in sorted(depart_s):
        rid = route_by_id.get(vid, "main")
        k, j = _route_origin(rid), _route_exit(rid)
        destination = DESTINATION_CORRIDOR_END if j < 0 else ramp_labels[j]
        gave = gave_up_s.get(vid)
        first = first_sample.get(vid)
        last = last_sample.get(vid)
        cols["veh_id"].append(vid)
        cols["route"].append(rid)
        cols["origin"].append(ORIGIN_MAINLINE if k < 0 else ramp_labels[k])
        cols["origin_ramp"].append(k)
        cols["destination"].append(destination)
        cols["destination_ramp"].append(j)
        cols["depart_planned_s"].append(depart_planned_s.get(vid))
        cols["depart_s"].append(depart_s[vid])
        cols["entry_t_s"].append(None if first is None else first[0])
        cols["entry_x_m"].append(None if first is None else first[1])
        cols["entry_lane"].append(None if first is None else first[2])
        cols["last_t_s"].append(None if last is None else last[0])
        cols["last_x_m"].append(None if last is None else last[1])
        cols["last_lane"].append(None if last is None else last[2])
        cols["arrived"].append(vid not in running)
        cols["gave_up"].append(gave is not None)
        cols["gave_up_s"].append(gave)
        cols["destination_final"].append(
            destination
            if gave is None
            else (destination_final or {}).get(vid, DESTINATION_CORRIDOR_END)
        )
    fields = list(_VEHICLES_SCHEMA)
    if driver_gaps is not None:
        for name, (side, movement) in DRIVER_GAP_COLUMNS.items():
            col: list[float | None] = []
            for vid in cols["veh_id"]:
                g = driver_gaps.get(vid)
                val = None if g is None else (g.lead if side == "lead" else g.lag)[movement]
                col.append(None if val is None else float(val))
            cols[name] = col
            fields.append((name, pa.float64()))
    schema = pa.schema(fields)
    return pa.Table.from_arrays(
        [pa.array(cols[name], type=dtype) for name, dtype in fields], schema=schema
    )


def run_micro(
    cfg: ScenarioConfig,
    seed: int,
    out_dir: str | Path,
    *,
    gui: bool = False,
    use_traci: bool = False,
    controller_start_s: float = 0.0,
    depart_edge_spread: int = 1,
) -> RunPaths:
    """Run one micro-tier replicate and write its artifacts.

    Args:
        cfg: Scenario configuration (``tier`` should be ``"micro"``).
        seed: Explicit replicate seed. Drives the fleet draws
            (``flowstate_core.rng``) and, reduced via ``sumo_seed``, SUMO's
            own RNG. A fixed ``(cfg, seed)`` reproduces the run bit-stably
            (SUMO is deterministic per version, CLAUDE.md §9).
        out_dir: Root of the run tree; artifacts land in
            ``out_dir/<config_hash>/<seed>/``.
        gui: Launch ``sumo-gui`` (forces TraCI; debugging only).
        use_traci: Use TCP TraCI instead of in-process libsumo
            (CLAUDE.md §3.3 fallback flag).
        controller_start_s: Sim time before which AV controllers stay
            inactive (0 = active from the start).
        depart_edge_spread: Corridor/OSM demand only — number of leading
            route edges insertions are spread over (round-robin, 0 = all);
            see :func:`microsim.vehicles.write_corridor_routes`. Keep the
            default 1 (upstream boundary inflow) for physics scenarios.

    Returns:
        :class:`RunPaths` for the completed replicate.

    Raises:
        ValueError: Missing OSM demand.
        RuntimeError: netconvert/SUMO failures.
    """
    t_wall0 = time.perf_counter()
    notes: list[str] = []
    chash = config_hash(cfg)
    run_dir = Path(out_dir) / chash / str(seed)
    run_dir.mkdir(parents=True, exist_ok=True)
    # Drop any completion marker from an earlier run of this (config, seed)
    # BEFORE touching the artifacts it vouches for: from here until the
    # marker is rewritten (last, atomically) the directory is a work in
    # progress, and an interruption must leave it visibly incomplete rather
    # than pairing a stale meta.json with a truncated trajectories.parquet.
    meta_path = run_dir / COMPLETION_MARKER
    meta_path.unlink(missing_ok=True)
    workdir = run_dir / "net"

    is_ring = isinstance(cfg.network, RingNetwork)
    bundle = _build_network(cfg, workdir)
    cfg_snapshot = cfg  # meta.json records the scenario as written (load-time ramp ids)
    cfg = _resolve_ramp_pieces(cfg, bundle)
    rng = make_rng(seed)
    routes_path = workdir / "demand.rou.xml"
    plan = _build_plan_and_routes(
        cfg, bundle, rng, routes_path, depart_edge_spread=depart_edge_spread
    )
    # The measured merge model (RampSpec.merge = "measured", 2026-10-05,
    # docs/MERGE_MODEL.md): its parameter set, and per driver one lead and one
    # lag critical-gap quantile from a child stream of the run's seed (B§5.3),
    # so every other draw — and every golden — is unchanged. None without a
    # measured ramp: nothing is read or drawn.
    measured_params: merge_model.MergeModelParams | None = None
    if isinstance(cfg.network, OSMNetwork) and any(
        r.kind == "on" and r.merge == "measured" for r in cfg.network.ramps
    ):
        measured_params = dataclasses.replace(
            merge_model.load_params(
                merge_model.params_artifact_path(), cfg.network.merge_model_set
            ),
            artifact=merge_model.PARAMS_ARTIFACT,
        )
        z_lead, z_lag = merge_model.driver_quantiles(merge_gap_stream(rng), plan.n)
        plan = dataclasses.replace(
            plan,
            merge_z_lead=tuple(float(z) for z in z_lead),
            merge_z_lag=tuple(float(z) for z in z_lag),
        )

    if cfg.fleet.delta != 4.0:
        notes.append(
            f"fleet.delta={cfg.fleet.delta} requested but SUMO's IDM fixes the "
            "acceleration exponent at 4 (not a vType attribute); ran with delta=4"
        )
    speed_factor_note = _speed_factor_merge_note(cfg)
    if speed_factor_note is not None:
        notes.append(speed_factor_note)

    # Macro-tier-only config blocks reaching a micro run: recorded, never
    # silently dropped (docs/CONTRACTS.md §2). The micro tier has no
    # fundamental diagram and no CTM grid, so neither can be honoured here.
    if cfg.fd_calibration is not None:
        notes.append(
            f"fd_calibration={cfg.fd_calibration!r} is a macro-tier (screening) input: "
            "the micro tier has no fundamental diagram and did not use it"
        )
    if cfg.macro is not None:
        notes.append(
            "macro options (dx_m, bottleneck_variant) are macro-tier (screening) inputs "
            "and were not used by this micro-tier run"
        )

    # Calibrated-fleet provenance (docs/CONTRACTS.md §2): when the fleet draws
    # from an IDMCalibration artifact, meta.json records its data_hash.
    fleet_calibration: dict[str, str] | None = None
    if cfg.fleet.idm_calibration is not None:
        cal = load_idm_calibration(cfg.fleet.idm_calibration)
        fleet_calibration = {
            "path": cfg.fleet.idm_calibration,
            "data_hash": cal.data_hash,
            "created_at": cal.created_at,
        }

    # --- Controller setup -------------------------------------------------
    controller_fn: VehicleControllerFn | None = None
    controller_params: dict[str, float] = {}
    # The downstream speed bins (ControllerObs.downstream, the wave oracle of
    # CLAUDE.md §4.3) are one pass over every vehicle per AV and control
    # step, so they are built only for a controller that reads them
    # (controllers.registry.reads_downstream; JAD). Every other controller
    # gets the contract's empty default, and the oracle's delay buffer and
    # noise draws, which feed only the bins, are skipped with them.
    wants_downstream = False
    if cfg.av.controller is not None:
        controller_fn = get_vehicle_controller(cfg.av.controller)
        controller_params = {
            **default_params(cfg.av.controller),
            **cfg.av.controller_params,
        }
        wants_downstream = reads_downstream(cfg.av.controller)
    vsl_fn: SegmentControllerFn | None = None
    vsl_params: dict[str, float] = {}
    if cfg.av.vsl is not None:
        vsl_fn = get_segment_controller(cfg.av.vsl)
        vsl_params = {**default_params(cfg.av.vsl), **cfg.av.vsl_params}
    # Gantry segments (CLAUDE.md §4.4: 0.5–1.0 km groups of main edges) and
    # each edge's base limit from the compiled net, so the posted limit can be
    # scaled by compliance against the road's own limit (never above it).
    vsl_segments: list[tuple[str, ...]] = []
    vsl_seg_lengths: list[float] = []
    vsl_base_by_edge: dict[str, float] = {}
    vsl_history: list[dict[str, Any]] = []
    if vsl_fn is not None:
        vsl_segments = bundle.segments(VSL_SEGMENT_TARGET_M)
        length_by_edge = dict(zip(bundle.edge_ids, bundle.edge_lengths, strict=True))
        vsl_seg_lengths = [sum(length_by_edge[e] for e in seg) for seg in vsl_segments]
        vsl_base_by_edge = _edge_speed_limits(bundle, [e for seg in vsl_segments for e in seg])

    compliant_avs = set(plan.complied_ids)
    memories: dict[str, Memory] = {vid: {} for vid in compliant_avs}
    # AVSpec.emergency_handback (WP-95; on by default since 2026-10-04): None
    # (the key false, or no controller) keeps the command path exactly as
    # before WP-95; otherwise the per-step pass in _emergency_handback_step.
    handback: dict[str, Any] | None = None
    if cfg.av.emergency_handback and controller_fn is not None:
        handback = {
            "model": cfg.fleet.model,
            "held": {},
            "in_force": set(),
            "decel": {},
            "n_vehicle_steps": 0,
            "n_withdrawals": 0,
            "vehicles": set(),
        }
    # AVSpec.release_off_corridor and observe_close_leader (WP-96): with a
    # controller both are counted whether on or off (the counting reads only
    # what the step already fetched: no TraCI call when off); None without one.
    off_corridor: dict[str, Any] | None = None
    close_obs: dict[str, Any] | None = None
    if controller_fn is not None:
        off_corridor = {
            "release": cfg.av.release_off_corridor,
            "commanded": set(),
            "left": set(),
            "n_vehicle_steps": 0,
            "n_released": 0,
        }
        close_obs = {
            "observe": cfg.av.observe_close_leader,
            "n_vehicle_steps": 0,
            "vehicles": set(),
        }

    # Wave-detection oracle realism (CLAUDE.md §4.3). A perfect oracle keeps an
    # empty history and zero noise, so this costs nothing when unused.
    oracle = cfg.av.oracle
    oracle_delay_s = float(oracle.delay_s)
    oracle_noise_frac = float(oracle.amplitude_noise_frac)
    # Offset keeps the oracle stream independent of the fleet-generation stream.
    oracle_rng = make_rng(seed + 7919)
    _oracle_maxlen = math.ceil(oracle_delay_s / cfg.sim.step_length_s) + 2
    oracle_history: deque[tuple[float, np.ndarray, np.ndarray]] = deque(
        maxlen=_oracle_maxlen if oracle_delay_s > 0.0 else 1
    )
    vsl_memory: Memory = {}
    min_gap_by_id = {plan.vehicle_id(i): plan.params[i]["s0"] for i in range(plan.n)}
    is_av_by_id = {plan.vehicle_id(i): plan.is_av[i] for i in range(plan.n)}
    is_heavy_by_id = {plan.vehicle_id(i): plan.heavy(i) for i in range(plan.n)}
    is_hov_by_id = {plan.vehicle_id(i): plan.hov(i) for i in range(plan.n)}
    complied_by_id = {plan.vehicle_id(i): plan.complied[i] for i in range(plan.n)}

    # --- SUMO startup -----------------------------------------------------
    lib = _TrafficLib(use_traci, gui)
    mod = lib.mod
    tc = mod.constants
    sumo_cmd = [
        lib.binary,
        "-n",
        str(bundle.net_path),
        "-r",
        str(routes_path),
        "--step-length",
        str(cfg.sim.step_length_s),
        "--seed",
        str(sumo_seed(seed)),
        "--time-to-teleport",
        "-1",
        "--no-warnings",
        "--collision.action",
        "warn",
        "--no-step-log",
        "--begin",
        "0",
    ]
    if cfg.sim.lateral_resolution_m is not None:
        # sublane model (LC_SL2015): continuous lateral positions, gradual merges
        sumo_cmd += ["--lateral-resolution", f"{cfg.sim.lateral_resolution_m:g}"]
    mod.start(sumo_cmd)

    step = cfg.sim.step_length_s
    n_steps = round(cfg.sim.duration_s / step)
    out_every = max(round(1.0 / (cfg.sim.output_hz * step)), 1)
    # Samples land on whole simulation steps, so the realized output rate is
    # 1/(out_every·step) and can differ from the requested sim.output_hz (e.g.
    # 4 Hz at a 0.5 s step is delivered as 2 Hz). Everything downstream —
    # Edie weighting below, docs/CONTRACTS.md §3 consumers — must use the
    # realized cadence, and meta.json records it (output_hz_realized).
    output_hz_realized = 1.0 / (out_every * step)
    if abs(output_hz_realized - cfg.sim.output_hz) > 1e-9:
        notes.append(
            f"sim.output_hz={cfg.sim.output_hz:g} is not attainable at "
            f"step_length_s={step:g} (samples land on whole steps); trajectories "
            f"and edges.parquet were produced at {output_hz_realized:g} Hz "
            "(meta.json: output_hz_realized)"
        )
    act_every = max(round(cfg.sim.action_step_s / step), 1)
    vsl_every = max(round(VSL_INTERVAL_S / step), 1)
    sub_vars = [
        tc.VAR_SPEED,
        tc.VAR_LANEPOSITION,
        tc.VAR_ROAD_ID,
        tc.VAR_LANE_INDEX,
        tc.VAR_ACCELERATION,
        tc.VAR_FUELCONSUMPTION,
    ]

    offsets_by_edge = dict(zip(bundle.edge_ids, bundle.offsets, strict=True))
    circumference = bundle.total_length_m
    has_ramps = isinstance(cfg.network, OSMNetwork) and bool(cfg.network.ramps)
    n_departed_by_route: dict[str, int] = {}
    route_by_id = {plan.vehicle_id(i): plan.route_of(i) for i in range(plan.n)}
    # VEHICLES_FILE bookkeeping (reads only): each departed vehicle's SUMO
    # departure time, and the step at which a weaving section gave up each
    # rerouted exiter (ws["gave_up"] only grows; its size is checked per step)
    depart_s_by_id: dict[str, float] = {}
    gave_up_at: dict[str, float] = {}
    # JOURNEYS_FILE bookkeeping (reads only, WP-105): the named routes' edge
    # lengths and base limits, read here — before the boundary schedule below
    # or a VSL changes any limit; then per vehicle its insertion offset along
    # the route, its arrival step, the step and route offset (insertion offset
    # plus odometer) at which a ramp meter gave it its stop and released it,
    # and its odometer when the run ended. Nothing here drives the simulation.
    route_geom = _route_geometry(mod, route_by_id.values())
    insert_pending: list[str] = []
    insert_offset_by_id: dict[str, float | None] = {}
    arrival_s_by_id: dict[str, float] = {}
    meter_ramp_by_id: dict[str, int] = {}
    meter_hold_by_id: dict[str, tuple[float, float | None]] = {}
    meter_release_by_id: dict[str, tuple[float, float | None]] = {}
    distance_end_by_id: dict[str, float] = {}

    def _route_offset_now(vid: str) -> float | None:
        # insertion offset plus odometer (a read-only TraCI query)
        offset = insert_offset_by_id.get(vid)
        return None if offset is None else offset + float(mod.vehicle.getDistance(vid))

    end_s = 0.0

    # Measured downstream boundary condition (docs/CONTRACTS.md §2): a speed
    # schedule on the exit-buffer edge OUTSIDE the corridor proper, standard
    # FHWA microsim calibration practice for congestion entering the modeled
    # section from downstream (FHWA-HOP-18-036; see BoundarySpec docstring).
    boundary_steps: list[tuple[float, float]] = []
    boundary_spec = getattr(cfg.network, "boundary", None)
    if boundary_spec is not None and bundle.exit_edge is not None:
        boundary_steps = [(float(ts), float(vs)) for ts, vs in boundary_spec.steps]
    # The schedule's speeds are measured speeds; SUMO drives a passenger at
    # speedFactor × the posted limit, so with FleetSpec.speed_factor set
    # (WP-109) each step is posted divided by it: the fleet's mean driver
    # then drives the measured speed. A default fleet posts the schedule as
    # written (no division, so its runs are unchanged).
    # BoundarySpec.limit_factor (amendment B1, docs/I24_DISCHARGE_DIAGNOSIS.md
    # §8.3, opt-in) multiplies every step first; at its default 1.0 the
    # schedule is posted exactly as before the field existed.
    boundary_divisor = float(cfg.fleet.speed_factor)
    boundary_factor = (
        float(boundary_spec.limit_factor)
        if boundary_spec is not None
        else BOUNDARY_LIMIT_FACTOR_DEFAULT
    )

    def _posted_limit(v_schedule: float) -> float:
        v = (
            v_schedule
            if boundary_factor == BOUNDARY_LIMIT_FACTOR_DEFAULT
            else v_schedule * boundary_factor
        )
        return v if boundary_divisor == SPEED_FACTOR_DEFAULT else v / boundary_divisor

    boundary_posted = [(ts, _posted_limit(vs)) for ts, vs in boundary_steps]
    boundary_idx = 0
    # Apply every step scheduled at or before t = 0 up front.
    while boundary_idx < len(boundary_steps) and boundary_steps[boundary_idx][0] <= 0.0:
        mod.edge.setMaxSpeed(bundle.exit_edge, boundary_posted[boundary_idx][1])
        boundary_idx += 1

    fuel_mg: dict[str, float] = {}
    unwrap_x: dict[str, tuple[float, float]] = {}  # veh_id -> (last wrapped x, unwrapped x)
    v_ref_hist: deque[tuple[float, float]] = deque()
    pert_pending = cfg.perturbation is not None
    pert_release_t = math.inf
    pert_vehicle: str | None = None
    n_departed = 0
    n_collisions = 0  # SUMO collision events (collision.action warn keeps both vehicles)
    collision_log: list[dict[str, Any]] = []

    # --- Ramp metering (RampMeterSpec): a virtual signal on each metered
    # on-ramp's last edge; the rate comes from the registry controller. The
    # stop is assigned when a vehicle is first seen on any ramp edge (from
    # edges[0]) and only if it can still brake for it (_meter_assign_stop).
    meter_states: list[dict[str, Any]] = []
    if isinstance(cfg.network, OSMNetwork) and any(
        r.kind == "on" and r.meter is not None for r in cfg.network.ramps
    ):
        from controllers.registry import get_ramp_meter

        net_for_meters = sumolib.net.readNet(str(bundle.net_path))
        # the compiled chain, ramp-guessing pieces in place (2026-10-04): a ramp
        # discovered on a guessed net attaches to its ``-AddedOnRampEdge`` piece,
        # which the scenario's load-time corridor ids do not list (the phase-1
        # rehearsal's ALINEA runs on the I-94 WB corridor all failed with
        # "'43917735#1-AddedOnRampEdge' is not in list")
        chain_m = expand_ramp_splits(list(cfg.network.corridor_edges), bundle.edge_ids)
        for k_ramp_m, ramp_m in enumerate(cfg.network.ramps):
            if ramp_m.kind != "on" or ramp_m.meter is None:
                continue
            spec_r = ramp_m.meter
            last_edge = ramp_m.edges[-1]
            e_last = net_for_meters.getEdge(last_edge)
            if e_last.getLength() < spec_r.stop_line_m + 20.0:
                raise ValueError(
                    f"ramp {ramp_m.name or last_edge}: the last ramp edge ({e_last.getLength():.0f} m) "
                    f"is too short for a stop line {spec_r.stop_line_m:g} m before its end"
                )
            i_attach = chain_m.index(ramp_m.attach_edge)
            down_edge = chain_m[min(i_attach + 1, len(chain_m) - 1)]
            e_down = net_for_meters.getEdge(down_edge)
            meter_states.append(
                {
                    "ramp": ramp_m.name or last_edge,
                    # index in meta.json["ramps"] (JOURNEYS_FILE meter_ramp)
                    "ramp_index": k_ramp_m,
                    "spec": spec_r,
                    "fn": get_ramp_meter(spec_r.controller),
                    "params": {
                        **dict(spec_r.params),
                        "rate_min_veh_h": spec_r.rate_min_veh_h,
                        "rate_max_veh_h": spec_r.rate_max_veh_h,
                    },
                    "edge": last_edge,
                    "stop_pos_m": float(e_last.getLength() - spec_r.stop_line_m),
                    "ramp_edges": list(ramp_m.edges),
                    "edge_len_m": {
                        e: float(net_for_meters.getEdge(e).getLength()) for e in ramp_m.edges
                    },
                    "seen_set": set(),
                    "n_passed_unstoppable": 0,
                    "down_edge": down_edge,
                    "down_len_lanes_m": float(e_down.getLength() * e_down.getLaneNumber()),
                    "rate": float(spec_r.rate_init_veh_h or spec_r.rate_max_veh_h),
                    "memory": {},
                    "next_update_s": 0.0,
                    "last_release_s": -math.inf,
                    "stopped_set": set(),
                    "released": [],
                    "rates": [],
                }
            )

    # --- Scripted on-ramp merges (RampSpec.merge == "scripted") ------------
    # Every vehicle on the acceleration lane (lane 0 of the attach edge, which
    # dead-ends) is driven by a gap-acceptance rule instead of SUMO's
    # lane-change model: match the speed of the mainline lane it enters, take
    # the first gap that clears the accepted time gap on both sides, and force
    # the change (the follower yields; SUMO still refuses collisions) after a
    # wait inside the last stretch of the lane. Late-merge / forced-merge
    # behaviour in the Hidas (2005) sense; docs/I24_VALIDATION.md §0.8.
    scripted_states: list[dict[str, Any]] = []
    if isinstance(cfg.network, OSMNetwork) and any(
        r.kind == "on" and r.merge == "scripted" for r in cfg.network.ramps
    ):
        net_for_merges = sumolib.net.readNet(str(bundle.net_path))
        for ramp_s in cfg.network.ramps:
            if ramp_s.kind != "on" or ramp_s.merge != "scripted":
                continue
            e_attach = net_for_merges.getEdge(ramp_s.attach_edge)
            if e_attach.getLaneNumber() < 2:
                raise ValueError(
                    f"ramp {ramp_s.name or ramp_s.attach_edge}: the scripted merge needs a "
                    "mainline lane beside the acceleration lane"
                )
            scripted_states.append(
                {
                    "ramp": ramp_s.name or ramp_s.attach_edge,
                    "edge": ramp_s.attach_edge,
                    "lane_len_m": float(e_attach.getLength()),
                    "target_lane": f"{ramp_s.attach_edge}_1",
                    "params": {**SCRIPTED_MERGE_DEFAULTS, **dict(ramp_s.merge_params)},
                    "veh": {},
                    "n_entered": 0,
                    "n_changed": 0,
                    "n_forced": 0,
                    "n_forced_deferred": 0,  # force_guard's refused vehicle-steps (WP-93)
                    "step_s": float(cfg.sim.step_length_s),
                    "waits_s": [],
                }
            )
        # Stepped in corridor order (the attach edge's offset along the
        # chain), whatever the ramp list's order (2026-09-24, block 3): the
        # step order of every runner-driven section is its position, so that
        # a section upstream of another always acts first in a step.
        scripted_states.sort(key=lambda ss: float(offsets_by_edge[ss["edge"]]))

    # --- Weaving sections (RampSpec.merge == "weave") ----------------------
    # An entrance whose auxiliary lane also feeds the next exit: lane 0 stays
    # connected to the exit (_apply_merge_models validated the pairing) and
    # _weave_step drives both crossing movements. Which vehicles exit there is
    # known from the plan's route ids ("*_off<j>").
    weave_states: list[dict[str, Any]] = []
    if isinstance(cfg.network, OSMNetwork) and any(
        r.kind == "on" and r.merge == "weave" for r in cfg.network.ramps
    ):
        net_for_weaves = sumolib.net.readNet(str(bundle.net_path))
        chain_w = expand_ramp_splits(list(cfg.network.corridor_edges), bundle.edge_ids)
        for section in _check_weave_pairs(cfg.network, net_for_weaves, chain_w):
            ramp_w = cfg.network.ramps[section.on_ramp]
            if ramp_w.merge != "weave":
                continue  # a measured zone (below)
            exit_w = cfg.network.ramps[section.off_ramp]
            assert ramp_w.weave is not None  # guaranteed by _check_weave_pairs
            lens_w = {e: float(net_for_weaves.getEdge(e).getLength()) for e in section.edges}
            params_w: dict[str, float] = {**WEAVE_DEFAULTS, **dict(ramp_w.weave.weave_params)}
            # per corridor edge of the vacate window, the lanes a through
            # vehicle vacates from and to (_weave_vacate_step)
            vacate_lanes_w = _weave_vacate_lanes(
                net_for_weaves,
                chain_w,
                section.edges,
                offsets_by_edge,
                float(params_w["vacate_ahead_m"]),
            )
            lane_map_w: dict[tuple[str, int], int] = {
                **{
                    k: v
                    for k, v in _weave_lane_map(net_for_weaves, chain_w, section.edges).items()
                    if k[0] in offsets_by_edge
                },
                **{(e, 0): 0 for e in ramp_w.edges},
            }
            weave_states.append(
                {
                    "ramp": ramp_w.name or ramp_w.attach_edge,
                    "exit": exit_w.name or exit_w.attach_edge,
                    "off_index": section.off_ramp,
                    "edges": list(section.edges),
                    "edge_index": {e: n for n, e in enumerate(section.edges)},
                    "exit_edge": section.exit_edge,
                    # every edge of the paired off-ramp: an exit is counted on
                    # any of them (_weave_step, exit bookkeeping)
                    "exit_edges": frozenset(exit_w.edges),
                    "exit_only": dict(zip(section.edges, section.exit_only, strict=True)),
                    "lane_len_m": lens_w,
                    # section length still ahead once an edge is done [m]
                    "beyond_m": {
                        e: sum(lens_w[x] for x in section.edges[n + 1 :])
                        for n, e in enumerate(section.edges)
                    },
                    "length_m_measured": section.length_m,
                    "length_m": ramp_w.weave.length_m,
                    "params": params_w,
                    # the forced zone and its delay, and the short-section flag
                    "rule": _weave_short_section_rule(float(sum(lens_w.values())), params_w),
                    "exiting_ids": frozenset(
                        vid
                        for vid, rid in route_by_id.items()
                        if _route_exit(rid) == section.off_ramp
                    ),
                    "exited": set(),
                    "reached": set(),
                    "awaiting_exit": set(),
                    "veh": {},
                    # target-lane listings for the gap choice (_weave_lane_map);
                    # the ramp's lane 0 continues lane 0 of the section, at
                    # negative positions, so an entrant is seen before it arrives
                    "lane_map": lane_map_w,
                    "vacate_lanes": vacate_lanes_w,
                    # never asked to vacate: bound for the paired exit or for
                    # an off-ramp leaving from a window edge
                    "vacate_exempt_ids": _weave_vacate_exempt_ids(
                        net_for_weaves, cfg.network.ramps, section, vacate_lanes_w, route_by_id
                    ),
                    "vacate": {},
                    # the vehicles asked (the default form asks once); those
                    # in the window last step and not asked; the vehicles
                    # first asked in the last minute; the target lane's
                    # sightings in the window (its flow)
                    "vacate_seen": set(),
                    "vacate_pending": set(),
                    "vacate_asks_s": deque(),
                    "vacate_flow_ids": set(),
                    "vacate_flow_s": deque(),
                    # WP-62, the exiters' early move (_weave_exit_prepare_step):
                    # its holds, the exiters asked, those left unasked last step,
                    # the asks of the last minute and its target lane's
                    # sightings (the bound's flow); counters below
                    "prep": {},
                    "prep_seen": set(),
                    "prep_pending": set(),
                    "prep_asks_s": deque(),
                    "prep_flow_ids": set(),
                    "prep_flow_s": deque(),
                    "x_offset": {
                        **offsets_by_edge,
                        **{
                            e: offsets_by_edge[section.edges[0]]
                            - sum(
                                float(net_for_weaves.getEdge(x).getLength())
                                for x in ramp_w.edges[n:]
                            )
                            for n, e in enumerate(ramp_w.edges)
                        },
                    },
                    "ramp_edges": frozenset(ramp_w.edges),
                    "pre": {},
                    "veh_params": {},
                    "lane_vmax": {},
                    "n_entered": 0,
                    "n_changed_in": 0,
                    "n_changed_out": 0,
                    "n_forced": 0,
                    "n_missed": 0,
                    "n_missed_exit": 0,
                    # exit-bound vehicles rerouted through at the gore's end
                    # (exit-side derivation): no longer driven; their new
                    # destination is the corridor's last edge
                    "gave_up": set(),
                    "through_target": chain_w[-1],
                    # amendment W1 (entrant_giveup_m, off unless set): the
                    # entrants rerouted to the paired exit at the auxiliary
                    # lane's end, its counter and their new destination (the
                    # off-ramp's last edge, where it leaves the network)
                    "took_exit": set(),
                    "n_entrant_took_exit": 0,
                    "exit_target": exit_w.edges[-1],
                    # amendment W2 (three switches, off unless set): the fleet
                    # model and the commanded-braking bound per vehicle of the
                    # handback, the vetoes of the opposing resolution to
                    # restore, and the counters (meta only while on)
                    "cf_model": cfg.fleet.model,
                    "hb_decel": {},
                    "opp_veto": {},
                    "n_handback_skips": 0,
                    "n_close_leader_withheld": 0,
                    "n_opposing_deferred": 0,
                    "n_opposing_vetoed": 0,
                    "n_forced_deferred": 0,
                    "n_cooperations": 0,
                    "coop_decel_sum": 0.0,
                    "n_changer_eased": 0,
                    "n_vacated": 0,
                    "n_vacate_refused": 0,
                    "n_vacate_skipped_no_gap": 0,
                    "n_vacate_requests": 0,
                    # WP-62: exiters moved into the lane feeding section lane
                    # 1 before the section (meta), and the state-only counts
                    "n_exit_prepared": 0,
                    "n_exit_prepare_refused": 0,
                    "n_exit_prepare_requests": 0,
                    "n_exit_prepare_skipped": 0,
                    "n_exit_prepare_yielded": 0,
                    "n_exit_prepare_held": 0,
                    # stopped crossing pairs (_weave_pair_release): first
                    # step each pair stood, the pairs already released
                    "pair_since": {},
                    "pair_released": set(),
                    "n_pair_releases": 0,
                    "step_s": float(cfg.sim.step_length_s),
                    "waits_in_s": [],
                    "waits_out_s": [],
                }
            )
        # Stepped upstream-first — by the section's start offset along the
        # chain, not by where its ramp sits in the ramp list (2026-09-24,
        # block 3). The vacate guard of _weave_vacate_step (a vehicle already
        # held at mode 512 / 256 by another section is not asked) covers a
        # downstream section reading a vehicle the upstream one already
        # drives; stepped the other way round, an upstream section's
        # exit-bound vehicle would find the downstream section's vacate hold
        # (512) on it first, capture that as its "original" mode and restore
        # it at hand-back after the downstream section had restored the real
        # one. meta.json["weave_sections"] lists the sections in this order.
        weave_states.sort(key=lambda ws: float(ws["x_offset"][ws["edges"][0]]))

    # --- Measured merge zones (RampSpec.merge == "measured", 2026-10-05) ---
    # Acceleration lanes (no weave block; the lane terminated as for the
    # scripted merge) and weaving sections (a weave block; paired as for the
    # weave). Each zone is a section state stepped by _measured_step in the
    # same upstream-first order as the weaving sections, so it joins
    # weave_states; meta.json lists it under "measured_merges", not
    # "weave_sections". The run-level state holds the parameters, the driver
    # draws and the relaxations.
    measured_run: dict[str, Any] | None = None
    if measured_params is not None:
        assert isinstance(cfg.network, OSMNetwork)
        net_for_mm = sumolib.net.readNet(str(bundle.net_path))
        chain_mm = expand_ramp_splits(list(cfg.network.corridor_edges), bundle.edge_ids)
        measured_run = _measured_run_state(
            measured_params,
            plan,
            plan.av_ids,
            _lane_successors(net_for_mm),
            float(cfg.sim.step_length_s),
            _internal_lane_origins(net_for_mm),
        )
        sections_mm = {
            sec.on_ramp: sec
            for sec in _check_weave_pairs(cfg.network, net_for_mm, chain_mm)
            if cfg.network.ramps[sec.on_ramp].merge == "measured"
        }
        routes_mm = ramp_routes(bundle.edge_ids, cfg.network.ramps)
        n_mm = 0
        for k_mm, ramp_mm in enumerate(cfg.network.ramps):
            if ramp_mm.kind != "on" or ramp_mm.merge != "measured":
                continue
            weave_states.append(
                _measured_zone_state(
                    index=n_mm,
                    run=measured_run,
                    net=net_for_mm,
                    chain=chain_mm,
                    ramps=cfg.network.ramps,
                    on_index=k_mm,
                    section=sections_mm.get(k_mm),
                    offsets_by_edge=offsets_by_edge,
                    route_by_id=route_by_id,
                    routes=routes_mm,
                    step_s=float(cfg.sim.step_length_s),
                )
            )
            n_mm += 1
        weave_states.sort(key=lambda ws: float(ws["x_offset"][ws["edges"][0]]))
        for n_z, ws_z in enumerate(ws for ws in weave_states if ws.get("mm")):
            ws_z["mm"]["index"] = n_z
    measured_states = [ws for ws in weave_states if ws.get("mm")]
    # every section's and scripted merge's capture sees every section's
    # pending vetoes (review 2026-10-07, third regression review;
    # _weave_share_vetoes)
    _weave_share_vetoes(weave_states, scripted_states)

    # --- The lane-end give-up (OSMNetwork.lane_end_giveup_m, WP-71) ---------
    # At every diverge the weaving sections do not cover, a vehicle held at
    # the end of a lane its route does not continue on, with the change
    # toward its route blocked, is rerouted to that lane's own continuation
    # (_lane_end_step). Off by default: nothing below runs, nothing changes.
    lane_end: dict[str, Any] | None = None
    # with the measured merge model the give-up is always on, at its fixed
    # 7.5 m unless the scenario sets a distance (docs/MERGE_MODEL.md §2, B§5.2)
    lane_end_m = (
        float(cfg.network.lane_end_giveup_m) if isinstance(cfg.network, OSMNetwork) else 0.0
    )
    if measured_run is not None and lane_end_m <= 0.0:
        lane_end_m = merge_model.LANE_END_GIVEUP_M
    if measured_run is not None:
        measured_run["lane_end_giveup_m"] = lane_end_m
    if isinstance(cfg.network, OSMNetwork) and lane_end_m > 0.0:
        net_for_lane_end = sumolib.net.readNet(str(bundle.net_path))
        chain_le = expand_ramp_splits(list(cfg.network.corridor_edges), bundle.edge_ids)
        skip_le = frozenset(e for ws in weave_states for e in ws["edges"])
        diverges_le = _lane_end_diverges(
            net_for_lane_end, chain_le, cfg.network.ramps, skip_le, offsets_by_edge
        )
        lane_end = {
            "distance_m": lane_end_m,
            "diverges": diverges_le,
            "by_edge": {d["edge"]: d for d in diverges_le},
            "skipped_edges": skip_le,
        }
    # the destination each lane-end give-up drove to (VEHICLES_FILE)
    lane_end_dest: dict[str, str] = {}

    # --- Managed (HOV) lanes: lane permission windows like closures ---------
    managed_states: list[dict[str, Any]] = []
    if cfg.managed_lanes:
        net_for_lanes_m = sumolib.net.readNet(str(bundle.net_path))
        lengths_m = dict(zip(bundle.edge_ids, bundle.edge_lengths, strict=True))
        corridor_ids_m = bundle.main_edges
        x_ref_m = offsets_by_edge[corridor_ids_m[0]] if corridor_ids_m else 0.0
        for spec_m in cfg.managed_lanes:
            lane_ids_m: list[str] = []
            skipped_m: list[str] = []
            x_lo_m, x_hi_m = x_ref_m + spec_m.start_m, x_ref_m + spec_m.end_m
            for eid in bundle.edge_ids:
                if eid == bundle.entry_edge:
                    continue
                e_lo = offsets_by_edge[eid]
                e_hi = e_lo + lengths_m[eid]
                if e_hi <= x_lo_m or e_lo >= x_hi_m:
                    continue
                n_lanes_e = int(net_for_lanes_m.getEdge(eid).getLaneNumber())
                for li in spec_m.lanes:
                    (lane_ids_m if li < n_lanes_e else skipped_m).append(f"{eid}_{li}")
            managed_states.append(
                {
                    "spec": spec_m,
                    "x_lo_m": x_lo_m,
                    "x_hi_m": x_hi_m,
                    "lane_ids": lane_ids_m,
                    "skipped": skipped_m,
                    "applied_at_s": None,
                    "released_at_s": None,
                    "orig": {},
                }
            )

    # --- Temporary lane closures (LaneClosureSpec; labelled seeded=True) --
    closure_states: list[dict[str, Any]] = []
    if cfg.closures:
        net_for_lanes = sumolib.net.readNet(str(bundle.net_path))
        lengths_by_edge = dict(zip(bundle.edge_ids, bundle.edge_lengths, strict=True))
        # Closure positions are measured from the start of the analysis
        # corridor (the first edge after the insertion buffer); the insertion
        # edge itself is never closed (a closed departure lane is invalid).
        corridor_ids = bundle.main_edges
        x_ref = offsets_by_edge[corridor_ids[0]] if corridor_ids else 0.0
        for spec_c in cfg.closures:
            lane_ids: list[str] = []
            skipped: list[str] = []
            x_lo, x_hi = x_ref + spec_c.start_m, x_ref + spec_c.end_m
            for eid in bundle.edge_ids:
                if eid == bundle.entry_edge:
                    continue
                e_lo = offsets_by_edge[eid]
                e_hi = e_lo + lengths_by_edge[eid]
                if e_hi <= x_lo or e_lo >= x_hi:
                    continue
                n_lanes_e = int(net_for_lanes.getEdge(eid).getLaneNumber())
                for li in spec_c.lanes:
                    (lane_ids if li < n_lanes_e else skipped).append(f"{eid}_{li}")
            closure_states.append(
                {
                    "spec": spec_c,
                    "x_lo_m": x_lo,
                    "x_hi_m": x_hi,
                    "lane_ids": lane_ids,
                    "skipped": skipped,
                    "applied_at_s": None,
                    "released_at_s": None,
                    "orig": {},
                }
            )

    traj_path = run_dir / "trajectories.parquet"
    # Edie weighting uses the REALIZED sample interval (out_every whole steps),
    # not the nominal 1/output_hz: sampling happens on whole simulation steps,
    # so a requested rate that does not divide the step length is rounded down
    # and the nominal interval would scale density/flow by nominal/realized.
    # The bins are filled row group by row group as the trajectories are
    # written (_EdieAccumulator), so the run's rows are never held twice.
    edie = _EdieAccumulator(out_every * step, cfg.sim.duration_s, bundle.total_length_m)
    traj_writer = _TrajectoryWriter(traj_path, is_ring, edie)
    n_gave_up_seen = [0] * len(weave_states)  # size of each ws["gave_up"] already recorded
    # Subscription keys hoisted out of the step loop (module attribute reads
    # per vehicle and step were a measurable share of its Python time).
    var_road, var_pos, var_speed = tc.VAR_ROAD_ID, tc.VAR_LANEPOSITION, tc.VAR_SPEED
    var_lane, var_accel, var_fuel = tc.VAR_LANE_INDEX, tc.VAR_ACCELERATION, tc.VAR_FUELCONSUMPTION
    # The trajectory flags as id sets: a flag no vehicle carries is a
    # constant column (no per-row lookup); otherwise membership, which is
    # the truth value ``dict.get(vid, False)`` gave per row before.
    flag_ids = {
        "is_av": frozenset(v for v, f in is_av_by_id.items() if f),
        "complied": frozenset(v for v, f in complied_by_id.items() if f),
        "is_heavy": frozenset(v for v, f in is_heavy_by_id.items() if f),
        "is_hov": frozenset(v for v, f in is_hov_by_id.items() if f),
    }

    try:
        for k in range(n_steps):
            mod.simulationStep()
            t = float(mod.simulation.getTime())
            if mod.simulation.getCollidingVehiclesNumber():
                for c in mod.simulation.getCollisions():
                    n_collisions += 1
                    if measured_states:
                        # meta.json measured_merges[i].n_collisions_attributable
                        _measured_attribute(
                            measured_states, str(c.collider), str(c.victim), str(c.lane), t
                        )
                    if len(collision_log) < COLLISION_LOG_MAX:
                        collision_log.append(
                            {
                                "t": t,
                                "collider": c.collider,
                                "victim": c.victim,
                                "type": c.type,
                                "lane": c.lane,
                                "pos_m": float(c.pos),
                            }
                        )

            # Downstream boundary schedule (piecewise-constant, exit edge).
            while boundary_idx < len(boundary_steps) and t >= boundary_steps[boundary_idx][0]:
                mod.edge.setMaxSpeed(bundle.exit_edge, boundary_posted[boundary_idx][1])
                boundary_idx += 1

            for vid in mod.simulation.getDepartedIDList():
                mod.vehicle.subscribe(vid, sub_vars)
                n_departed += 1
                depart_s_by_id[vid] = float(mod.vehicle.getDeparture(vid))
                insert_pending.append(vid)
                if has_ramps:
                    rid = route_by_id.get(vid, "main")
                    n_departed_by_route[rid] = n_departed_by_route.get(rid, 0) + 1
            for vid in mod.simulation.getArrivedIDList():
                arrival_s_by_id[vid] = t
            results = mod.vehicle.getAllSubscriptionResults()
            # JOURNEYS_FILE (reads only): where along its route each vehicle
            # inserted this step was put on the road, from its first
            # subscription row.
            for vid in insert_pending:
                res_i = results.get(vid)
                geom_i = route_geom.get(route_by_id.get(vid, "main"))
                insert_offset_by_id[vid] = (
                    None
                    if res_i is None or geom_i is None
                    else geom_i.offset_m(
                        str(res_i[tc.VAR_ROAD_ID]), float(res_i[tc.VAR_LANEPOSITION])
                    )
                )
            insert_pending.clear()
            # Fuel is accounted for every vehicle; the linear-x state (and
            # therefore controllers, VSL, trajectories) covers vehicles on
            # corridor edges only — ramp edges have no linear x.
            if has_ramps:
                on_corridor: list[str] = []
                for vid, res in results.items():
                    if res[var_road] in offsets_by_edge:
                        on_corridor.append(vid)
                    else:
                        fuel_mg[vid] = fuel_mg.get(vid, 0.0) + res[var_fuel] * step
                ids = sorted(on_corridor)
            else:
                ids = sorted(results)
            if not ids:
                v_ref_hist.append((t, 0.0))
                while v_ref_hist and v_ref_hist[0][0] < t - V_REF_WINDOW_S:
                    v_ref_hist.popleft()
                if off_corridor is not None and off_corridor["commanded"]:
                    # AVs off the corridor (ramps) hold their commands (WP-96)
                    _off_corridor_step(
                        mod,
                        tc,
                        results,
                        off_corridor,
                        offsets_by_edge,
                        lambda v: _commanded_by_runner(weave_states, scripted_states, v),
                        handback,
                    )
                if handback is not None and handback["held"]:
                    # AVs off the corridor (ramps) still hold their commands
                    _emergency_handback_step(mod, tc, results, handback, step)
                continue

            rows = [results[v] for v in ids]
            speeds = np.array([r[var_speed] for r in rows])
            xs = np.array([offsets_by_edge[r[var_road]] + r[var_pos] for r in rows])
            if is_ring:
                xs = xs % circumference

            # Fuel accumulation (mg/s × step, every step — see module docstring).
            for vid, r in zip(ids, rows, strict=True):
                fuel_mg[vid] = fuel_mg.get(vid, 0.0) + r[var_fuel] * step
            if is_ring:
                for i, vid in enumerate(ids):
                    last, unw = unwrap_x.get(vid, (xs[i], xs[i]))
                    d = (xs[i] - last + circumference / 2.0) % circumference - circumference / 2.0
                    unwrap_x[vid] = (float(xs[i]), unw + d)

            # Oracle snapshot buffer: the delayed oracle reads the traffic
            # state as it was `delay_s` ago (positions stay current).
            if oracle_delay_s > 0.0 and wants_downstream:
                oracle_history.append((t, xs.copy(), speeds.copy()))

            # Rolling platoon-mean reference speed (45 s window).
            v_ref_hist.append((t, float(speeds.mean())))
            while v_ref_hist and v_ref_hist[0][0] < t - V_REF_WINDOW_S:
                v_ref_hist.popleft()
            v_ref = float(np.mean([m for _, m in v_ref_hist]))

            # Seeded perturbation (labeled seeded=True in meta, CLAUDE.md §0.2).
            if pert_pending and cfg.perturbation is not None and t >= cfg.perturbation.t_s:
                pert = cfg.perturbation
                if is_ring:
                    dist = np.abs(
                        (xs - pert.position_m + circumference / 2.0) % circumference
                        - circumference / 2.0
                    )
                else:
                    dist = np.abs(xs - pert.position_m)
                j = int(dist.argmin())
                pert_vehicle = ids[j]
                v_target = max(float(speeds[j]) - pert.v_drop_ms, 0.0)
                mod.vehicle.slowDown(pert_vehicle, v_target, pert.duration_s)
                pert_release_t = t + pert.duration_s
                pert_pending = False
            if pert_vehicle is not None and t >= pert_release_t:
                # slowDown pins the speed after ramping; hand control back.
                if pert_vehicle in results:
                    mod.vehicle.setSpeed(pert_vehicle, -1.0)
                pert_release_t = math.inf

            # Ramp meters: update the rate on the controller's interval, hold
            # every ramp vehicle at the stop line, release one per metered
            # headway (one vehicle per green).
            for ms_r in meter_states:
                spec_r = ms_r["spec"]
                if t >= ms_r["next_update_s"]:
                    n_down = float(mod.edge.getLastStepVehicleNumber(ms_r["down_edge"]))
                    obs_r = RampMeterObs(
                        t=t,
                        dt=spec_r.interval_s,
                        density_downstream=n_down / ms_r["down_len_lanes_m"],
                        rate_prev=ms_r["rate"],
                        queue_len=len(ms_r["stopped_set"]),
                    )
                    ms_r["rate"], ms_r["memory"] = ms_r["fn"](obs_r, ms_r["params"], ms_r["memory"])
                    ms_r["rates"].append((t, ms_r["rate"], obs_r.density_downstream))
                    ms_r["next_update_s"] = t + spec_r.interval_s
                # Each ramp vehicle is decided once, at its first step on a
                # ramp edge: stopped if it can brake for the line, otherwise
                # passed this cycle and counted (docs/LESSONS.md row 31).
                for e_r in ms_r["ramp_edges"]:
                    for vid in mod.edge.getLastStepVehicleIDs(e_r):
                        if vid in ms_r["seen_set"]:
                            continue
                        ms_r["seen_set"].add(vid)
                        if _meter_assign_stop(mod, ms_r, vid, e_r, step):
                            ms_r["stopped_set"].add(vid)
                            meter_ramp_by_id[vid] = ms_r["ramp_index"]
                            meter_hold_by_id[vid] = (t, _route_offset_now(vid))
                        else:
                            ms_r["n_passed_unstoppable"] += 1
                on_edge = list(mod.edge.getLastStepVehicleIDs(ms_r["edge"]))
                if t - ms_r["last_release_s"] >= 3600.0 / max(ms_r["rate"], 1.0):
                    waiting = [
                        vid
                        for vid in on_edge
                        if vid in ms_r["stopped_set"] and mod.vehicle.isStopped(vid)
                    ]
                    if waiting:
                        front = max(waiting, key=lambda v: mod.vehicle.getLanePosition(v))
                        mod.vehicle.resume(front)
                        ms_r["stopped_set"].discard(front)
                        meter_release_by_id[front] = (t, _route_offset_now(front))
                        ms_r["released"].append(t)
                        ms_r["last_release_s"] = t

            # This step's vehicles by road for the section rules below
            # (_RoadIndex: built on their first query, never otherwise).
            road_index = _RoadIndex(results, var_road)
            # Scripted on-ramp merges (see the setup block above).
            for ss in scripted_states:
                _scripted_merge_step(mod, tc, ss, results, t, road_index)
            # The measured merge model's relaxations, run-wide, before any zone
            # reads a headway (docs/MERGE_MODEL.md; None without the model).
            if measured_run is not None:
                _measured_relax_step(mod, tc, measured_run, results, t)
            # Weaving sections and measured merge zones (see the setup blocks
            # above), upstream first.
            for n_ws, ws in enumerate(weave_states):
                if ws.get("mm"):
                    _measured_step(mod, tc, ws, results, t, road_index)
                else:
                    _weave_step(mod, tc, ws, results, t, road_index)
                if len(ws["gave_up"]) > n_gave_up_seen[n_ws]:
                    for vid in ws["gave_up"]:
                        gave_up_at.setdefault(vid, t)
                    n_gave_up_seen[n_ws] = len(ws["gave_up"])
                    # amendment W1: an entrant that took the paired exit
                    # drove to it, not to the corridor's end (VEHICLES_FILE)
                    for vid in ws.get("took_exit", ()):
                        lane_end_dest.setdefault(vid, ws["exit"])
            # The lane-end give-up (WP-71), after the sections' own rules.
            if lane_end is not None:
                for vid, dest in _lane_end_step(
                    mod,
                    tc,
                    lane_end,
                    results,
                    lambda v: _commanded_by_runner(weave_states, scripted_states, v),
                    road_index,
                ):
                    gave_up_at.setdefault(vid, t)
                    lane_end_dest[vid] = dest

            # Managed lanes: admit only the hov class for the window, then
            # restore the lanes' original permissions.
            for ms in managed_states:
                spec_m = ms["spec"]
                if ms["applied_at_s"] is None and t >= spec_m.t_start_s:
                    for lid in ms["lane_ids"]:
                        ms["orig"][lid] = list(mod.lane.getDisallowed(lid))
                        mod.lane.setAllowed(lid, ["hov"])
                    ms["applied_at_s"] = t
                elif (
                    ms["applied_at_s"] is not None
                    and ms["released_at_s"] is None
                    and t >= spec_m.t_end_s
                ):
                    for lid in ms["lane_ids"]:
                        mod.lane.setDisallowed(lid, ms["orig"].get(lid, []))
                    ms["released_at_s"] = t

            # Temporary lane closures: refuse every class on the closed lanes
            # for the window, then restore the lanes' original permissions.
            for cs in closure_states:
                spec_c = cs["spec"]
                if cs["applied_at_s"] is None and t >= spec_c.t_start_s:
                    for lid in cs["lane_ids"]:
                        cs["orig"][lid] = list(mod.lane.getDisallowed(lid))
                        mod.lane.setDisallowed(lid, list(CLOSURE_VCLASSES))
                    cs["applied_at_s"] = t
                elif (
                    cs["applied_at_s"] is not None
                    and cs["released_at_s"] is None
                    and t >= spec_c.t_end_s
                ):
                    for lid in cs["lane_ids"]:
                        mod.lane.setDisallowed(lid, cs["orig"].get(lid, []))
                    cs["released_at_s"] = t

            # Controller dispatch for compliant AVs (every action step).
            if (
                controller_fn is not None
                and compliant_avs
                and t >= controller_start_s
                and (k + 1) % act_every == 0
            ):
                x_by_id = dict(zip(ids, xs, strict=True))
                # Oracle realism (§4.3): the controller may read a STALE traffic
                # state (its own position stays current), and each observed bin
                # speed may carry multiplicative error.
                if wants_downstream:
                    o_xs, o_speeds = _stale_snapshot(
                        oracle_history, t, oracle_delay_s, (xs, speeds)
                    )
                for vid in sorted(compliant_avs):
                    # Not in the network yet, already arrived, or still on a
                    # ramp edge (no corridor position, no downstream field):
                    # controllers act on corridor edges only.
                    if vid not in x_by_id:
                        continue
                    if measured_run is not None:
                        released_mm: set[str] = measured_run["av_released"]
                        if _measured_owns(measured_states, vid):
                            # the measured merge model owns the AV's lane
                            # change (B§5.6); its command takes over after the
                            # change. A command held from before is withdrawn
                            # once, meanwhile
                            if vid not in released_mm:
                                mod.vehicle.setSpeed(vid, -1.0)
                                released_mm.add(vid)
                                measured_run["n_av_released"] += 1
                                if handback is not None:
                                    handback["held"].pop(vid, None)
                                    handback["in_force"].discard(vid)
                                if off_corridor is not None:
                                    off_corridor["commanded"].discard(vid)
                            continue
                        released_mm.discard(vid)
                    gap, v_leader, within_s0 = _leader_obs(
                        mod, vid, min_gap_by_id[vid], close_leader=cfg.av.observe_close_leader
                    )
                    if within_s0 and close_obs is not None:
                        # AVSpec.observe_close_leader (WP-96): counted on or off
                        close_obs["n_vehicle_steps"] += 1
                        close_obs["vehicles"].add(vid)
                    downstream: tuple[float, ...] = ()
                    if wants_downstream:
                        downstream = _downstream_bins(
                            x_by_id[vid], o_xs, o_speeds, circumference, is_ring
                        )
                        downstream = _apply_oracle_noise(downstream, oracle_noise_frac, oracle_rng)
                    obs = ControllerObs(
                        t=t,
                        dt=cfg.sim.action_step_s,
                        v=float(results[vid][tc.VAR_SPEED]),
                        gap=gap,
                        v_leader=v_leader,
                        v_ref=v_ref,
                        downstream=downstream,
                        downstream_dx=DOWNSTREAM_BIN_M,
                    )
                    v_cmd, memories[vid] = controller_fn(obs, controller_params, memories[vid])
                    # Default speedMode: SUMO safety checks stay ON (§3.3).
                    mod.vehicle.setSpeed(vid, max(v_cmd, 0.0))
                    if handback is not None:
                        handback["held"][vid] = max(v_cmd, 0.0)
                        handback["in_force"].add(vid)
                    if off_corridor is not None:
                        off_corridor["commanded"].add(vid)

            # AVSpec.release_off_corridor (WP-96): every step, after the
            # dispatch, count (off) or release (on) the commands of AVs that
            # have left the corridor; before the handback, which must not
            # re-apply a released command.
            if off_corridor is not None and off_corridor["commanded"]:
                _off_corridor_step(
                    mod,
                    tc,
                    results,
                    off_corridor,
                    offsets_by_edge,
                    lambda v: _commanded_by_runner(weave_states, scripted_states, v),
                    handback,
                )

            # AVSpec.emergency_handback (WP-95): every step, after the
            # dispatch, withdraw a command the model must brake through.
            if handback is not None and handback["held"]:
                _emergency_handback_step(mod, tc, results, handback, step)

            # VSL dispatch (per gantry segment, every VSL_INTERVAL_S): segment
            # state is the vehicle count over the segment's summed length and
            # the mean speed of those vehicles; the segment's limit is posted
            # to every edge in it, scaled by compliance (CLAUDE.md §4.4).
            if vsl_fn is not None and (k + 1) % vsl_every == 0:
                road_ids = [results[v][tc.VAR_ROAD_ID] for v in ids]
                seg_speed: list[float] = []
                seg_density: list[float] = []
                for seg, seg_len in zip(vsl_segments, vsl_seg_lengths, strict=True):
                    members = set(seg)
                    sel = np.fromiter((rid in members for rid in road_ids), bool, len(road_ids))
                    n_on = int(sel.sum())
                    seg_speed.append(float(speeds[sel].mean()) if n_on else math.nan)
                    seg_density.append(n_on / seg_len)
                seg_obs = SegmentObs(
                    t=t,
                    dt=VSL_INTERVAL_S,
                    seg_speed=tuple(seg_speed),
                    seg_density=tuple(seg_density),
                )
                limits, vsl_memory = vsl_fn(seg_obs, vsl_params, vsl_memory)
                applied: list[float] = []
                for seg, lim in zip(vsl_segments, limits, strict=True):
                    for eid in seg:
                        v_eff = effective_limit(
                            float(lim), vsl_base_by_edge[eid], cfg.av.compliance
                        )
                        mod.edge.setMaxSpeed(eid, v_eff)
                        # Read back what SUMO actually holds (lane 0; setMaxSpeed
                        # sets every lane of the edge) — provenance for reports.
                        applied.append(float(mod.lane.getMaxSpeed(f"{eid}_0")))
                vsl_history.append(
                    {
                        "t": t,
                        "posted_ms": [float(lim) for lim in limits],
                        "applied_ms": applied,
                    }
                )

            # Trajectory capture at the output cadence.
            if (k + 1) % out_every == 0:
                n_ids = len(ids)
                flags = {
                    name: (
                        np.fromiter((v in on for v in ids), dtype=np.bool_, count=n_ids)
                        if on
                        else np.zeros(n_ids, dtype=np.bool_)
                    )
                    for name, on in flag_ids.items()
                }
                traj_writer.append_step(
                    t,
                    ids,
                    x=xs,
                    lane=np.array([int(r[var_lane]) for r in rows], dtype=np.int32),
                    v=speeds,
                    a=np.array([float(r[var_accel]) for r in rows], dtype=np.float64),
                    **flags,
                    **(
                        {"x_unwrapped": np.array([unwrap_x[v][1] for v in ids], dtype=np.float64)}
                        if is_ring
                        else {}
                    ),
                )
                traj_writer.maybe_flush()
        running = frozenset(mod.vehicle.getIDList())
        n_arrived = n_departed - len(running)
        # JOURNEYS_FILE (reads only): the end of the run and the odometer of
        # every vehicle still in the network then (censored journeys).
        end_s = float(mod.simulation.getTime())
        distance_end_by_id = {vid: float(mod.vehicle.getDistance(vid)) for vid in sorted(running)}
    finally:
        mod.close()

    # --- Artifacts --------------------------------------------------------
    traj_writer.close()
    edges_df = edie.frame()
    edges_path = run_dir / "edges.parquet"
    _write_parquet(pa.Table.from_pandas(edges_df, preserve_index=False), edges_path)
    ramps_cfg = list(cfg.network.ramps) if isinstance(cfg.network, OSMNetwork) else []
    _write_parquet(
        _vehicle_table(
            depart_s_by_id,
            route_by_id,
            {plan.vehicle_id(i): plan.depart_s[i] for i in range(plan.n)},
            [r.name or r.attach_edge for r in ramps_cfg],
            traj_writer.first_sample,
            traj_writer.last_sample,
            running,
            gave_up_at,
            lane_end_dest,
            driver_gaps=(
                None
                if measured_run is None
                else {vid: _mm_driver(measured_run, vid) for vid in depart_s_by_id}
            ),
        ),
        run_dir / VEHICLES_FILE,
    )
    journeys = _journey_table(
        [plan.vehicle_id(i) for i in range(plan.n)],
        route_by_id,
        {plan.vehicle_id(i): plan.depart_s[i] for i in range(plan.n)},
        {plan.vehicle_id(i): float(plan.params[i]["v0"]) for i in range(plan.n)},
        route_geom,
        depart_s_by_id,
        insert_offset_by_id,
        arrival_s_by_id,
        distance_end_by_id,
        meter_ramp_by_id,
        meter_hold_by_id,
        meter_release_by_id,
        end_s,
        speed_factor_by_id=(
            {plan.vehicle_id(i): plan.speed_factor_of(i) for i in range(plan.n)}
            if plan.speed_factor
            else None
        ),
    )
    _write_parquet(journeys, run_dir / JOURNEYS_FILE)
    meter_wait_total = _meter_wait_totals(journeys)
    journeys_meta = _journeys_meta(
        journeys, end_s, len(set(route_by_id.values()) - set(route_geom))
    )
    edge_len_by_id = dict(zip(bundle.edge_ids, bundle.edge_lengths, strict=True))

    fuel_ml = {vid: fuel_mg_to_ml(mg) for vid, mg in sorted(fuel_mg.items())}
    wall = time.perf_counter() - t_wall0
    meta: dict[str, Any] = {
        "config": cfg_snapshot.model_dump(mode="json"),
        "config_hash": chash,
        "config_hash_version": CONFIG_HASH_VERSION,
        "seed": seed,
        "sumo_seed": sumo_seed(seed),
        "versions": _versions(),
        "tier": "micro",
        "seeded": cfg.seeded,
        # Realized trajectory/edges sampling rate [Hz]: 1/(out_every·step),
        # which equals sim.output_hz only when the requested rate divides the
        # step length (a mismatch is also spelled out in ``notes``).
        "output_hz_realized": output_hz_realized,
        "wall_time_s": wall,
        "realtime_factor": cfg.sim.duration_s / wall if wall > 0 else None,
        "n_vehicles_planned": plan.n,
        "n_vehicles_departed": n_departed,
        "n_collisions": n_collisions,
        "collisions": collision_log,
        "n_vehicles_arrived": max(n_arrived, 0),
        # the demand ledger's whole-run totals (JOURNEYS_FILE, WP-105)
        "journeys": journeys_meta,
        "av_ids": list(plan.av_ids),
        "complied_ids": list(plan.complied_ids),
        "n_heavy": int(sum(plan.is_heavy)) if plan.is_heavy else 0,
        # effective per-lane departure distribution of heavy vehicles (empty = uniform scheme)
        "heavy_lane_shares": list(plan.heavy_lane_shares),
        "heavy_fraction_realized": (
            float(sum(plan.is_heavy)) / plan.n if plan.is_heavy and plan.n else 0.0
        ),
        "n_hov": int(sum(plan.is_hov)) if plan.is_hov else 0,
        "managed_lanes": [
            {
                "label": ms["spec"].label,
                "start_m": ms["spec"].start_m,
                "end_m": ms["spec"].end_m,
                "lanes": list(ms["spec"].lanes),
                "t_start_s": ms["spec"].t_start_s,
                "t_end_s": ms["spec"].t_end_s,
                "x_lo_m": ms["x_lo_m"],
                "x_hi_m": ms["x_hi_m"],
                "lane_ids": ms["lane_ids"],
                "skipped_lane_ids": ms["skipped"],
                "applied_at_s": ms["applied_at_s"],
                "released_at_s": ms["released_at_s"],
            }
            for ms in managed_states
        ],
        "ramp_meters": [
            {
                "ramp": ms_r["ramp"],
                "controller": ms_r["spec"].controller,
                "edge": ms_r["edge"],
                "stop_pos_m": ms_r["stop_pos_m"],
                "downstream_edge": ms_r["down_edge"],
                "interval_s": ms_r["spec"].interval_s,
                "n_released": len(ms_r["released"]),
                "n_passed_unstoppable": ms_r["n_passed_unstoppable"],
                # WP-105 (JOURNEYS_FILE): vehicles given the meter's stop, those
                # still held when the run ended, and their summed meter_wait_s
                # (hold time minus the free-flow time of the stretch covered
                # meanwhile, censored at the run's end) [s]
                "n_held": sum(1 for r in meter_ramp_by_id.values() if r == ms_r["ramp_index"]),
                "n_unreleased": len(ms_r["stopped_set"]),
                "wait_s_total": meter_wait_total.get(ms_r["ramp_index"], 0.0),
                "releases_s": ms_r["released"],
                "rates": [[tt, rr, dd] for tt, rr, dd in ms_r["rates"]],
            }
            for ms_r in meter_states
        ],
        "merge_models": [
            {"ramp": r.name or r.attach_edge, "attach_edge": r.attach_edge, "merge": r.merge}
            for r in (cfg.network.ramps if isinstance(cfg.network, OSMNetwork) else [])
            if r.kind == "on" and r.merge != "lane_change"
        ],
        "net_patch_files": list(bundle.patch_files),
        "scripted_merges": [
            {
                "ramp": ss["ramp"],
                "attach_edge": ss["edge"],
                "params": dict(ss["params"]),
                "n_entered": ss["n_entered"],
                "n_changed": ss["n_changed"],
                "n_forced": ss["n_forced"],
                # vehicle-steps a due forced change was held under mode 512 by
                # force_guard (WP-93; on by default since 2026-10-04); 0 with
                # the key at 0
                "n_forced_deferred": ss["n_forced_deferred"],
                "n_unfinished": len(ss["veh"]),
                "wait_s_mean": float(np.mean(ss["waits_s"])) if ss["waits_s"] else None,
                "wait_s_p90": float(np.percentile(ss["waits_s"], 90)) if ss["waits_s"] else None,
            }
            for ss in scripted_states
        ],
        "weave_sections": [
            _weave_meta(ws, n_departed_by_route) for ws in weave_states if not ws.get("mm")
        ],
        # WeaveSpec.ramp_to_ramp_share (2026-10-07, docs/TH52_CROSSING_SHARE.md):
        # present only when an entrance sets it, so every other run's meta.json
        # keeps exactly its keys
        **({"ramp_to_ramp_shares": list(plan.ramp_to_ramp)} if plan.ramp_to_ramp else {}),
        # the measured merge model (RampSpec.merge = "measured", 2026-10-05):
        # present only when a ramp uses it, so every other run's meta.json
        # keeps exactly its keys
        **(
            {
                "measured_merges": [
                    _measured_meta(ws, n_departed_by_route) for ws in measured_states
                ],
                "measured_merge_model": _measured_run_meta(measured_run),
            }
            if measured_run is not None
            else {}
        ),
        # the lane-end give-up (OSMNetwork.lane_end_giveup_m, WP-71): None when off
        "lane_end_giveups": _lane_end_meta(lane_end),
        "closures": [
            {
                "label": cs["spec"].label,
                "start_m": cs["spec"].start_m,
                "end_m": cs["spec"].end_m,
                "lanes": list(cs["spec"].lanes),
                "t_start_s": cs["spec"].t_start_s,
                "t_end_s": cs["spec"].t_end_s,
                "x_lo_m": cs["x_lo_m"],
                "x_hi_m": cs["x_hi_m"],
                "lane_ids": cs["lane_ids"],
                "skipped_lane_ids": cs["skipped"],
                "applied_at_s": cs["applied_at_s"],
                "released_at_s": cs["released_at_s"],
            }
            for cs in closure_states
        ],
        "fleet_calibration": fleet_calibration,
        # FleetSpec.speed_factor / speed_dev (WP-109): present only when set
        **_speed_factor_meta(cfg.fleet, plan, bool(boundary_steps)),
        "controller": cfg.av.controller,
        # AVSpec.emergency_handback (WP-95): None when off
        "av_emergency_handback": (
            {
                "n_vehicle_steps": handback["n_vehicle_steps"],
                "n_withdrawals": handback["n_withdrawals"],
                "n_vehicles": len(handback["vehicles"]),
            }
            if handback is not None
            else None
        ),
        # AVSpec.release_off_corridor (WP-96): None without a controller
        "av_off_corridor": (
            {
                "release": off_corridor["release"],
                "n_vehicles": len(off_corridor["left"]),
                "n_vehicle_steps": off_corridor["n_vehicle_steps"],
                "n_released": off_corridor["n_released"],
            }
            if off_corridor is not None
            else None
        ),
        # AVSpec.observe_close_leader (WP-96): None without a controller
        "av_close_leader": (
            {
                "observed": close_obs["observe"],
                "n_vehicle_steps": close_obs["n_vehicle_steps"],
                "n_vehicles": len(close_obs["vehicles"]),
            }
            if close_obs is not None
            else None
        ),
        "controller_start_s": controller_start_s,
        "vsl": cfg.av.vsl,
        "vsl_dispatch": (
            {
                "controller": cfg.av.vsl,
                "compliance": cfg.av.compliance,
                "interval_s": VSL_INTERVAL_S,
                "segment_target_m": VSL_SEGMENT_TARGET_M,
                "segments": [list(seg) for seg in vsl_segments],
                "segment_lengths_m": vsl_seg_lengths,
                "edges": [e for seg in vsl_segments for e in seg],
                "base_limit_ms_by_edge": vsl_base_by_edge,
                "n_dispatches": len(vsl_history),
                # One entry per dispatch: ``posted_ms`` per segment (raw
                # controller output), ``applied_ms`` per edge in ``edges``
                # order as read back from SUMO after compliance scaling.
                "history": vsl_history,
            }
            if vsl_fn is not None
            else None
        ),
        # Linear-x geometry of the analysis corridor, as BUILT (the config
        # alone does not carry it for an OSM import: the edge lengths come
        # from the compiled network). Read by ``api.results.analysis_span``
        # so every replicate of an OSM run measures travel times over the
        # same distance instead of over its own observed extremes.
        "corridor": {
            # ring | corridor | osm. A ring's x wraps, so its numbers below
            # describe the loop and are NOT a travel-time span.
            "kind": bundle.kind,
            "total_length_m": bundle.total_length_m,
            # Where the corridor proper starts: the first edge after the
            # insertion buffer on a generated corridor, the first corridor
            # edge on an OSM import (0.0 on a ring).
            "x_first_edge_m": (offsets_by_edge[bundle.main_edges[0]] if bundle.main_edges else 0.0),
        },
        "boundary": (
            {
                "kind": boundary_spec.kind,
                "exit_edge": bundle.exit_edge,
                "exit_buffer_m": (
                    boundary_spec.exit_buffer_m
                    if isinstance(cfg.network, CorridorNetwork)
                    else bundle.edge_lengths[-1]
                ),
                "n_steps": len(boundary_steps),
                "n_steps_applied": boundary_idx,
                "v_limit_min_ms": min(v for _, v in boundary_steps),
                "v_limit_max_ms": max(v for _, v in boundary_steps),
                # amendment B1 (opt-in): recorded only when set, so a default
                # run's block keeps its keys; the v_limit_* above stay the
                # schedule as written, these are the limits as posted
                **(
                    {
                        "limit_factor": boundary_factor,
                        "v_posted_min_ms": min(v for _, v in boundary_posted),
                        "v_posted_max_ms": max(v for _, v in boundary_posted),
                    }
                    if boundary_factor != BOUNDARY_LIMIT_FACTOR_DEFAULT
                    else {}
                ),
            }
            if boundary_steps and boundary_spec is not None
            else None
        ),
        "fuel_unit": "ml (HBEFA4 mg/s x step, / 0.74 kg/l gasoline density)",
        "fuel_total_ml": float(sum(fuel_ml.values())),
        "fuel_ml_per_vehicle": fuel_ml,
        "perturbed_vehicle": pert_vehicle,
        "ramps": (
            [
                {
                    "index": k,
                    "name": r.name,
                    "kind": r.kind,
                    "attach_edge": r.attach_edge,
                    # trajectory x of the attach edge's start and end (as
                    # compiled): an on-ramp's vehicles join the corridor on
                    # that edge, an off-ramp's leave it at its end (WP-69)
                    "attach_x_m": (
                        float(offsets_by_edge[r.attach_edge])
                        if r.attach_edge in offsets_by_edge
                        else None
                    ),
                    "attach_end_x_m": (
                        float(offsets_by_edge[r.attach_edge] + edge_len_by_id[r.attach_edge])
                        if r.attach_edge in offsets_by_edge
                        else None
                    ),
                    "edges": list(r.edges),
                    "n_planned": sum(
                        1 for i in range(plan.n) if _route_origin(plan.route_of(i)) == k
                    ),
                    "n_departed": sum(
                        n for rid, n in n_departed_by_route.items() if _route_origin(rid) == k
                    ),
                    "n_planned_exiting": sum(
                        1 for i in range(plan.n) if _route_exit(plan.route_of(i)) == k
                    ),
                    # the guessed acceleration lane spilled past the attach
                    # edge and a connection patch terminated it at that edge's
                    # end (_apply_merge_models)
                    "acceleration_lane_terminated": (
                        r.kind == "on" and r.attach_edge in bundle.terminated_lanes
                    ),
                }
                for k, r in enumerate(cfg.network.ramps)
            ]
            if has_ramps and isinstance(cfg.network, OSMNetwork)
            else None
        ),
        "backend": "traci" if lib.use_traci else "libsumo",
        "notes": notes,
    }
    # Completion marker, written last and atomically: a reader either sees no
    # meta.json (run in progress or interrupted) or a complete one — never a
    # half-written file that would vouch for debris.
    tmp_meta = meta_path.with_name(meta_path.name + ".tmp")
    tmp_meta.write_text(json.dumps(meta, indent=2))
    os.replace(tmp_meta, meta_path)
    return RunPaths(run_dir=run_dir, trajectories=traj_path, edges=edges_path, meta=meta_path)


def _speed_factor_meta(fleet: FleetSpec, plan: FleetPlan, has_boundary: bool) -> dict[str, Any]:
    """``meta.json["speed_factor"]`` when the fleet sets one (WP-109).

    Empty for a default fleet (factor 1.0, no spread), so its ``meta.json``
    keeps exactly its keys. Otherwise one block: the configured ``mean`` and
    ``dev``, who carries it (passenger vehicles; heavy vehicles keep 1.0),
    the realized passenger factors (count, mean, min, max — the values
    written into the route file), and the divisor the downstream boundary
    schedule was posted with (None without a schedule).

    Args:
        fleet: The fleet block.
        plan: The run's fleet plan (``FleetPlan.speed_factor``).
        has_boundary: A boundary schedule was applied.

    Returns:
        ``{"speed_factor": {...}}`` or ``{}``.
    """
    if not plan.speed_factor:
        return {}
    passenger = [plan.speed_factor[i] for i in range(plan.n) if not plan.heavy(i)]
    return {
        "speed_factor": {
            "mean": float(fleet.speed_factor),
            "dev": float(fleet.speed_dev),
            "applies_to": "passenger vehicles; heavy vehicles keep 1.0",
            "n_vehicles": len(passenger),
            "realized_mean": float(np.mean(passenger)) if passenger else None,
            "realized_min": float(min(passenger)) if passenger else None,
            "realized_max": float(max(passenger)) if passenger else None,
            "boundary_posted_divided_by": float(fleet.speed_factor) if has_boundary else None,
        }
    }


def _speed_factor_merge_note(cfg: ScenarioConfig) -> str | None:
    """A ``meta.json`` note when a speed factor meets a scripted or weave merge.

    The scripted merge and the weave model their vehicles' car-following
    with the desired speed ``min(maxSpeed, lane limit)`` — speed factor 1 —
    and were left as they are when ``FleetSpec.speed_factor`` was added
    (WP-109), so with a factor other than 1 their gap predictions and the
    scripted merge's speed matching use the posted limit where SUMO's own
    vehicles use the factor times it. Said in the run's notes rather than
    silently.
    """
    if cfg.fleet.speed_factor == SPEED_FACTOR_DEFAULT and cfg.fleet.speed_dev == 0.0:
        return None
    net = cfg.network
    merges = sorted(
        {r.merge for r in net.ramps if r.merge in ("scripted", "weave")}
        if isinstance(net, OSMNetwork)
        else set()
    )
    if not merges:
        return None
    return (
        f"fleet.speed_factor={cfg.fleet.speed_factor:g} (speed_dev={cfg.fleet.speed_dev:g}) "
        f"with {' and '.join(merges)} merge(s): their gap and speed logic takes a vehicle's "
        "desired speed as min(maxSpeed, lane limit), i.e. speed factor 1, while SUMO drives "
        "every passenger vehicle at its factor times the limit (WP-109; not changed)"
    )


def _route_origin(route_id: str) -> int:
    """On-ramp index a route starts from (``-1`` for mainline)."""
    head = route_id.split("_")[0]
    return int(head[2:]) if head.startswith("on") else -1


def _route_exit(route_id: str) -> int:
    """Off-ramp index a route exits by (``-1`` when it drives to the end)."""
    tail = route_id.split("_")[-1]
    return int(tail[3:]) if tail.startswith("off") else -1


def _replicate_worker(payload: tuple[dict[str, Any], int, str]) -> tuple[str, str, str, str]:
    """Spawn-pool worker: one SUMO per process (libsumo singleton).

    Imports happen inside the child (spawn start method) and the config is
    re-validated from its JSON dump — nothing unpicklable crosses the
    process boundary.
    """
    cfg_json, rep_seed, out_root = payload
    from flowstate_core.config import ScenarioConfig as _SC
    from microsim.runner import run_micro as _run

    paths = _run(_SC.model_validate(cfg_json), rep_seed, Path(out_root))
    return (str(paths.run_dir), str(paths.trajectories), str(paths.edges), str(paths.meta))


#: Floor on the per-replicate wall-clock budget of :func:`run_replicates` [s].
#: Short replicates (the CI ring runs are seconds) still get a generous grace
#: period for interpreter spawn, network build and machine contention.
REPLICATE_TIMEOUT_FLOOR_S: Final[float] = 900.0

#: Slowest realtime factor a replicate may run at before the bounded wait
#: calls the pool wedged. CLAUDE.md §3.4 targets ≥ 5× real time on a laptop;
#: 0.05× (20× slower than real time) is a 100× margin on that, so the budget
#: only ever fires on a hang, never on a legitimately slow I-24 battery.
REPLICATE_MIN_REALTIME_FACTOR: Final[float] = 0.05


def replicate_timeout_s(cfg: ScenarioConfig) -> float:
    """Default per-replicate wall-clock budget for :func:`run_replicates` [s].

    Args:
        cfg: Scenario configuration (its ``sim.duration_s`` sets the scale).

    Returns:
        ``max(REPLICATE_TIMEOUT_FLOOR_S, duration_s / REPLICATE_MIN_REALTIME_FACTOR)``.
    """
    return max(REPLICATE_TIMEOUT_FLOOR_S, float(cfg.sim.duration_s) / REPLICATE_MIN_REALTIME_FACTOR)


def _kill_pool(ex: ProcessPoolExecutor) -> None:
    """Kill every worker process, then shut the executor down without waiting.

    ``ProcessPoolExecutor.shutdown(wait=True)`` — what leaving its context
    manager does — joins the workers, so a wedged worker would re-create the
    very hang this guard exists to break. libsumo runs SUMO *in-process*, so
    killing the worker takes its simulation with it and leaves no orphan
    (a ``use_traci`` worker's ``sumo`` child is reparented and exits when its
    TraCI socket closes).
    """
    for proc in list(getattr(ex, "_processes", {}).values()):
        try:
            if proc.is_alive():
                proc.kill()
        except (OSError, ValueError, AttributeError):  # pragma: no cover - exit race
            pass
    ex.shutdown(wait=False, cancel_futures=True)


class ReplicatesAborted(RuntimeError):
    """A completion guard stopped the pool before every replicate had run.

    Raised by :func:`run_replicates` when its ``on_complete`` callback
    returns False for a finished replicate: the remaining workers are killed
    (:func:`_kill_pool`) and the outstanding seeds never run. The caller
    decides what that means — ``scripts/corridor_battery.py`` writes a
    partial artifact and exits non-zero rather than spending an hour of
    cloud time on a configuration whose first replicate already showed it
    was not simulating the demand it was given.

    Attributes:
        seed: Seed of the replicate whose result tripped the guard.
        reason: The guard's own one-line explanation.
        completed: Seeds that had finished when the pool was stopped.
    """

    def __init__(self, seed: int, reason: str, completed: Sequence[int]) -> None:
        self.seed = seed
        self.reason = reason
        self.completed = list(completed)
        super().__init__(
            f"replicate pool stopped after seed {seed}: {reason} "
            f"({len(self.completed)} replicate(s) completed)"
        )


def _map_replicates(
    worker: Callable[[Any], Any],
    payloads: Sequence[Any],
    seeds: Sequence[int],
    n_procs: int,
    timeout_s: float,
    on_complete: Callable[[int, Any], str | None] | None = None,
) -> list[Any]:
    """Run ``worker(payload)`` per seed in a spawn pool, bounded and diagnosable.

    ``multiprocessing.Pool.map`` silently repopulates a worker that dies
    (OOM kill, container limit, operator ``kill -9``) and never completes or
    fails the task it was running, so the parent blocks forever — a run that
    should fail in seconds instead burns a billed VM until someone notices.
    A :class:`~concurrent.futures.ProcessPoolExecutor` fails the futures
    instead, and the bounded wait below covers the remaining case of a worker
    that is alive but stuck.

    Args:
        worker: Picklable callable applied to one payload per seed.
        payloads: One payload per seed, in seed order.
        seeds: Replicate seeds, used to name failures.
        n_procs: Pool size.
        timeout_s: Per-replicate wall-clock budget; the wait is that budget
            times the number of waves (``ceil(len(seeds)/n_procs)``).
        on_complete: Called as ``on_complete(seed, result)`` the moment a
            replicate finishes, in completion order. Returning None lets the
            pool carry on; returning a string stops it, killing the workers
            and raising :class:`ReplicatesAborted` with that string as the
            reason. Exceptions from the callback propagate unchanged (the
            pool is killed first).

    Returns:
        Worker results in seed order.

    Raises:
        ReplicatesAborted: ``on_complete`` stopped the pool.
        RuntimeError: A worker process died, or a replicate raised. The
            message names the seed(s).
        TimeoutError: The budget elapsed with replicates outstanding; the
            message names the seed(s) still running. Workers are killed
            first, so the caller never inherits the hang.
    """
    n_waves = math.ceil(len(seeds) / n_procs)
    budget_s = timeout_s * n_waves
    ctx = multiprocessing.get_context("spawn")
    ex = ProcessPoolExecutor(max_workers=n_procs, mp_context=ctx)
    results: dict[int, Any] = {}
    try:
        futures = {
            ex.submit(worker, payload): seed for payload, seed in zip(payloads, seeds, strict=True)
        }
        try:
            for fut in as_completed(futures, timeout=budget_s):
                seed = futures[fut]
                try:
                    results[seed] = fut.result()
                except BrokenProcessPool as exc:
                    lost = sorted(s for s in seeds if s not in results)
                    raise RuntimeError(
                        f"micro replicate pool broke: a worker process died (an OOM "
                        f"kill is the usual cause) with seed(s) {lost} unfinished "
                        f"({len(results)}/{len(seeds)} replicates completed). Re-run "
                        f"with fewer processes (n_procs) or on a larger machine."
                    ) from exc
                except Exception as exc:
                    # The child's message is NOT re-embedded here: it reaches
                    # the caller as this error's cause, where
                    # ``api.jobs._exception_chain_text`` can scrub it (a
                    # pydantic ValidationError quotes the file the child was
                    # validating). Copying it into this message would put the
                    # unscrubbed text back into the stored run error.
                    raise RuntimeError(
                        f"micro replicate seed={seed} failed: {type(exc).__name__}"
                    ) from exc
                if on_complete is not None:
                    reason = on_complete(seed, results[seed])
                    if reason is not None:
                        raise ReplicatesAborted(seed, reason, sorted(results))
        except TimeoutError as exc:
            stuck = sorted(s for s in seeds if s not in results)
            raise TimeoutError(
                f"micro replicate seed(s) {stuck} did not finish within {budget_s:.0f} s "
                f"({timeout_s:.0f} s per replicate x {n_waves} wave(s) of {n_procs} "
                f"worker(s)); {len(results)}/{len(seeds)} completed. The pool is wedged "
                f"or the machine is oversubscribed - its workers have been killed. Pass "
                f"a larger timeout_s if the replicates are legitimately this slow."
            ) from exc
    except BaseException:
        _kill_pool(ex)
        raise
    ex.shutdown(wait=True)
    return [results[s] for s in seeds]


def run_replicates(
    cfg: ScenarioConfig,
    out_root: str | Path,
    n_procs: int | None = None,
    *,
    timeout_s: float | None = None,
    on_complete: Callable[[int, RunPaths], str | None] | None = None,
) -> list[RunPaths]:
    """Run ``cfg.replicates`` seeded replicates in a spawn process pool.

    Replicate seeds come from ``spawn_seeds(cfg.seed, cfg.replicates)``
    (docs/CONTRACTS.md §6) so adding replicates never reshuffles existing
    ones. libsumo is a per-process singleton, so parallelism is
    process-level: the pool uses the ``spawn`` start method and each worker
    imports SUMO inside the child (CLAUDE.md §3.4).

    The wait is bounded (:func:`_map_replicates`): a worker that dies or a
    replicate that wedges fails the run — naming the seed — in place of the
    indefinite block ``multiprocessing.Pool.map`` produces, and every
    returned replicate is checked for its completion marker
    (:func:`require_complete_run`).

    Args:
        cfg: Scenario configuration.
        out_root: Run-tree root passed to each :func:`run_micro`.
        n_procs: Pool size (default: ``min(cpu_count, replicates)``).
        timeout_s: Per-replicate wall-clock budget (default:
            :func:`replicate_timeout_s`, derived from ``sim.duration_s``).
        on_complete: Optional guard called as ``on_complete(seed, paths)``
            the moment each replicate finishes, in completion order — the
            place to read the finished replicate's ``meta.json`` while the
            rest of the batch is still running. Returning a string stops the
            pool and raises :class:`ReplicatesAborted` with that reason.

    Returns:
        One :class:`RunPaths` per replicate, in seed order.

    Raises:
        ReplicatesAborted: ``on_complete`` stopped the pool.
        RuntimeError: A worker died or a replicate raised (seed named).
        TimeoutError: The budget elapsed with replicates outstanding.
    """
    seeds = spawn_seeds(cfg.seed, cfg.replicates)
    cfg_json = cfg.model_dump(mode="json")
    payloads = [(cfg_json, s, str(out_root)) for s in seeds]
    n_procs = n_procs or min(multiprocessing.cpu_count(), len(seeds))

    def _guard(seed: int, result: Any) -> str | None:
        a, b, c, d = result
        return None if on_complete is None else on_complete(seed, _run_paths(a, b, c, d))

    raw = _map_replicates(
        _replicate_worker,
        payloads,
        seeds,
        n_procs,
        replicate_timeout_s(cfg) if timeout_s is None else timeout_s,
        on_complete=None if on_complete is None else _guard,
    )
    paths = [_run_paths(*row) for row in raw]
    for p in paths:
        require_complete_run(p.run_dir)
    return paths


def _run_paths(run_dir: str, trajectories: str, edges: str, meta: str) -> RunPaths:
    """One worker result tuple as :class:`RunPaths`."""
    return RunPaths(
        run_dir=Path(run_dir),
        trajectories=Path(trajectories),
        edges=Path(edges),
        meta=Path(meta),
    )
