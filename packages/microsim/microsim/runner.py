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

import bisect
import dataclasses
import json
import math
import multiprocessing
import os
import platform
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
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

from controllers.registry import default_params, get_segment_controller, get_vehicle_controller
from controllers.vsl import VSL_SEGMENT_TARGET_M, effective_limit
from flowstate_core.config import (
    CONFIG_HASH_VERSION,
    SCRIPTED_MERGE_DEFAULTS,
    WEAVE_DEFAULTS,
    CorridorNetwork,
    OSMNetwork,
    RampSpec,
    RingNetwork,
    ScenarioConfig,
    config_hash,
)
from flowstate_core.controller_types import (
    ControllerObs,
    Memory,
    RampMeterObs,
    SegmentControllerFn,
    SegmentObs,
    VehicleControllerFn,
)
from flowstate_core.rng import make_rng, spawn_seeds, sumo_seed
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
    end: ``zipper``/``acceleration_lane`` patch the lane drop there, and the
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
        r for r in net.ramps if r.kind == "on" and r.merge not in ("lane_change", "weave")
    ]
    has_weave = any(r.kind == "on" and r.merge == "weave" for r in net.ramps)
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
            raise ValueError(
                f"ramp {label}: merge model {ramp.merge!r} needs the acceleration lane (lane 0 of "
                f"the attach edge) to dead-end at the edge's end, and it cannot be terminated "
                f"there: {verdict.reason} — {geometry}. Use merge: 'lane_change' on this ramp."
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
        if ramp.merge == "scripted":
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


def _check_weave_pairs(net: OSMNetwork, compiled: Any, chain: Sequence[str]) -> list[WeaveSection]:
    """The weaving section of every ``merge="weave"`` on-ramp, validated.

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
        if ramp.kind != "on" or ramp.merge != "weave" or ramp.weave is None:
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
                f"ramp {label}: merge model 'weave' pairs it with the exit {off.name!r} "
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
# the vacate rule's bound (_weave_vacate_step): a through vehicle is asked into
# the target lane only within that lane's spare capacity over the last minute
VACATE_LANE_CAPACITY_VEH_H = 2050.0  # one IDM lane at the fleet defaults (CLAUDE.md §3.1)
VACATE_FLOW_WINDOW_S = 60.0  # the window of the target lane's flow and of the asks
SCRIPTED_MERGE_CREEP_MS = 3.0  # desired-speed floor on the acceleration lane [m/s]
HALTING_SPEED_MS = 0.1  # SUMO's own halting threshold (waiting time accrues below it) [m/s]
#: The give-up patience (2026-09-24, block 3, WP-52) reads a follower as still
#: braking towards the gap while its speed falls by more than this
#: deceleration times the step per step; below it the follower is at its
#: speed (SUMO's speeds settle at an equilibrium within a few hundredths of a
#: m/s per step). One tenth of the fleet's smallest comfortable deceleration
#: of interest, not a fitted value.
WEAVE_GIVEUP_DECEL_TOL_MS2 = 0.1
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


def _scripted_merge_step(mod: Any, tc: Any, ss: dict[str, Any], results: Any, t: float) -> None:
    """One step of the scripted merge for one ramp (``RampSpec.merge = "scripted"``).

    Drives every vehicle on lane 0 of the attach edge: desired speed matched
    to the mainline neighbour ahead (or the mainline lane's limit), a lane change
    requested when the mainline gaps ahead and behind both clear
    ``accept_gap_s`` × speed + the ramp vehicle's own minimum gap, and a forced
    change (``LC_MODE_SCRIPTED_FORCE``) after ``force_after_s`` inside the last
    ``force_within_m`` of the lane. Control is handed back to SUMO as soon as
    the vehicle leaves the lane. Bookkeeping lands in ``ss`` for ``meta.json``.
    """
    prm = ss["params"]
    edge = ss["edge"]
    on_lane0 = {
        vid
        for vid, res in results.items()
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
    # courtesy yielding: restore every follower asked to hold back last step
    for fid, v_orig in ss["yielding"].items():
        if fid in results:
            mod.vehicle.setMaxSpeed(fid, v_orig)
    ss["yielding"] = {}
    for vid in sorted(on_lane0):
        st = veh.get(vid)
        if st is None:
            st = veh[vid] = {
                "entered_s": t,
                "zone_s": None,
                "requested_s": -math.inf,
                "forced": False,
                "lc_mode_orig": int(mod.vehicle.getLaneChangeMode(vid)),
                "v_max_orig": float(mod.vehicle.getMaxSpeed(vid)),
                "s0": float(mod.vehicle.getMinGap(vid)),
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
        if prm["courtesy"] > 0.0 and ok_lead and not ok_foll and f_id is not None:
            # the mainline follower blocking an otherwise acceptable gap eases
            # off (desired speed below the ramp vehicle's) so the gap opens;
            # its car-following model still decides how, and it is restored
            # next step unless it is still the blocker
            if f_id not in ss["yielding"]:
                ss["yielding"][f_id] = float(mod.vehicle.getMaxSpeed(f_id))
            mod.vehicle.setMaxSpeed(f_id, max(v_ego - prm["courtesy"], SCRIPTED_MERGE_CREEP_MS))
        if remaining <= prm["force_within_m"] and st["zone_s"] is None:
            st["zone_s"] = t
        force = st["zone_s"] is not None and t - st["zone_s"] >= prm["force_after_s"]
        if force and not st["forced"]:
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
    v_L)`` itself (:func:`_idm_desired_gap`, the *no-braking* gap, ``s0 +
    v·T`` at equal speeds — 263 m for the trace) removed the collisions and
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

    Args:
        s0: The changing vehicle's minimum gap [m].
        accept_s: Accepted time gap of the movement [s].
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

    Returns:
        Whether the forced change may be requested this step.
    """
    closing_lead = max(v_ego - v_lead, 0.0) if g_lead < math.inf else 0.0
    closing_foll = max(v_foll - v_ego, 0.0) if g_foll < math.inf else 0.0
    lead_min = s0 + accept_s * closing_lead
    if b_ego is not None:
        lead_min = max(lead_min, s0 + closing_lead**2 / (2.0 * b_ego))
    foll_min = s0 + accept_s * closing_foll
    if b_foll is not None:
        foll_min = max(foll_min, closing_foll**2 / (2.0 * b_foll))
    return g_lead > lead_min and g_foll > foll_min


def _weave_giveup_patient(
    st: dict[str, Any],
    t: float,
    patience_s: float,
    f_id: str | None,
    g_foll: float,
    v_foll: float,
    b_foll: float | None,
    step_s: float,
) -> bool:
    """Whether a halted exiter whose request is refused waits this step (WP-52).

    Bounded give-up patience (2026-09-24, block 3; docs/WEAVE_MODEL_PLAN.md,
    dated section). Under the speed-aware guard (:func:`_weave_force_gap_ok`)
    the first step on which a halted exiter at the gore's end can request no
    change often comes while its auxiliary-lane follower is still braking
    towards the gap the priority hold opened for it — the follower's brake
    gap ``(v_F − v_c)⁺²/(2·b_F)`` is above the gap it is closing for two or
    three seconds and then is not. Giving up on that step (the exit-side
    derivation) reroutes an exiter whose change would have been accepted a
    few steps later; the unbounded patient form (a halted exiter is never
    given up while its follower is held) locks the short section, because
    a held follower at rest behind a halted exiter is the abreast-pair state
    the pair release exists for (WP-51, variant E).

    The exiter waits only while the refusal is that transient: the follower
    reported this step, ``f_id``, is the one recorded on the previous step in
    ``st["foll_prev"]`` (so its speed history is one vehicle's), its speed
    fell since by more than ``WEAVE_GIVEUP_DECEL_TOL_MS2 · step_s``, it has
    not come to rest (``HALTING_SPEED_MS``), and the wait since the first
    refused step (``st["giveup_since"]``, set here or by
    :func:`_weave_giveup_abreast`, the budget being one) is shorter than the
    bound — ``patience_s`` or the follower's braking time to rest at its own
    ``b`` from its speed on the first step this rule read it
    (``st["giveup_v_foll"]``), ``v_F / b_F``, whichever is shorter, so a
    follower already crawling earns no patience. Any other
    state gives up at once, as before: no follower, a different follower, a
    follower at its speed or at rest, or the bound reached. The budget runs
    from the first refused step and is not reset, so an exiter that rolls
    again and halts again cannot renew it; ``patience_s`` of ``0`` never
    waits.

    Args:
        st: The exiter's per-vehicle state (``foll_prev``, ``giveup_since``,
            ``giveup_v_foll`` read and written here).
        t: Simulation time [s].
        patience_s: ``exit_giveup_patience_s``.
        f_id: The auxiliary-lane follower reported this step (``None`` = none).
        v_foll: Its speed [m/s] (``nan`` with none).
        b_foll: Its comfortable deceleration [m/s²] (``None`` with none).
        step_s: The step length [s].

    Returns:
        ``True`` to keep the exiter this step (the refusal is deferred as a
        forced change is, ``n_giveup_waited`` counted by the caller); ``False``
        to give the exit up now.
    """
    if patience_s <= 0.0 or f_id is None or b_foll is None or math.isnan(v_foll):
        return False
    if g_foll <= 0.0:
        return False
    prev = st.get("foll_prev")
    if prev is None or prev[0] != f_id:
        return False
    if st.get("giveup_since") is None:
        st["giveup_since"] = t
    if st.get("giveup_v_foll") is None:
        # the budget may have been opened by the abreast patience (WP-53,
        # :func:`_weave_giveup_abreast`) with no follower behind: the
        # braking time is then from the follower's speed on the first
        # step this rule reads it
        st["giveup_v_foll"] = v_foll
    bound = min(patience_s, st["giveup_v_foll"] / b_foll if b_foll > 0.0 else 0.0)
    if t - st["giveup_since"] >= bound:
        return False
    if v_foll < HALTING_SPEED_MS:
        return False
    return v_foll < prev[1] - WEAVE_GIVEUP_DECEL_TOL_MS2 * step_s


def _weave_abreast_clear_m(
    g_lead: float,
    g_foll: float,
    lead_need: float,
    len_c: float,
    s0_c: float,
    len_a: float,
    s0_a: float,
) -> float:
    """How far the auxiliary-lane vehicle beside a halted exiter must still advance [m].

    The abreast state (2026-09-24, block 3, WP-53; docs/WEAVE_MODEL_PLAN.md,
    dated section): a vehicle reported by ``getNeighbors`` with a negative
    gap overlaps the exiter longitudinally. SUMO reports a leader's gap as
    ``rear_A − front_c − s0_c`` (the ego's ``minGap`` taken off) and a
    follower's as ``rear_c − front_A − s0_A`` (the follower's). The exiter's
    leader side accepts the vehicle once the reported gap reaches
    ``lead_need`` — the acceptance's floor ``s0_c + accept · v_c``, which at
    a halted changer is ``s0_c`` (:func:`_weave_lead_gap_min`; the brake
    term is zero against a vehicle pulling away) — so from the leader side
    the distance is ``lead_need − g_lead`` whenever ``g_lead`` is under the
    floor (a vehicle just clear of the overlap but inside the floor is still
    clearing); from a follower-side overlap the vehicle's rear is
    ``len_c + g_foll + s0_A + len_A`` behind the exiter's front, so
    ``len_c + s0_c + lead_need + g_foll + s0_A + len_A``. Zero otherwise.

    Args:
        g_lead: Reported gap to the target-lane leader [m] (``inf`` = none).
        g_foll: Reported gap to the target-lane follower [m] (``inf`` = none).
        lead_need: The reported leader gap the acceptance needs [m].
        len_c: The exiter's length [m].
        s0_c: The exiter's ``minGap`` [m].
        len_a: The overlapping vehicle's length [m].
        s0_a: The overlapping vehicle's ``minGap`` [m].

    Returns:
        The distance [m], ``0`` when no vehicle is in the way on either side.
    """
    if g_lead < lead_need:
        return lead_need - g_lead
    if g_foll < 0.0:
        return max(len_c + s0_c + lead_need + g_foll + s0_a + len_a, 0.0)
    return 0.0


def _weave_giveup_abreast(
    st: dict[str, Any],
    t: float,
    patience_s: float,
    a_id: str | None,
    v_a: float,
    clear_m: float,
    a_is_entrant: bool,
) -> bool:
    """Whether a halted exiter waits for the vehicle beside it to clear (WP-53).

    The abreast state (2026-09-24, block 3; docs/WEAVE_MODEL_PLAN.md, dated
    section): of the 44 give-ups on the fixture grid at 6497b2d, 34 had an
    auxiliary-lane vehicle overlapping the halted exiter. A per-give-up
    trace of their geometry splits them: **24** are a *driven entrant*
    halted at the end of the auxiliary lane beside the exiter — the crossing
    pair at the lane ends, each owing the change into the other's lane,
    which no local rule resolves (neither can move on, neither can change
    across the other; the exiter's reroute is what frees both) — and **10**
    are an exit-bound queue vehicle sliding past the exiter's rear at
    0.7–7 m/s, most often its own held follower caught inside its brake
    distance (WP-52), whose leader ahead is moving: it clears the exiter's
    front within ``clear_m / v_a`` = 1.7–8 s at its speed, after which the
    gap behind it is the exiter's (its follower is held by the exit priority,
    :func:`_weave_choose_gap`).

    The exiter waits this step when the overlapping vehicle is not a driven
    entrant (its lane ends at the gore for its route: it never clears), is
    moving (``v_a`` at or above ``HALTING_SPEED_MS``), and would clear the
    exiter's leader side at its current speed within the budget left —
    ``patience_s`` less the time since the first refused step
    (``st["giveup_since"]``, set here or by :func:`_weave_giveup_patient`,
    never renewed). A halted or slower vehicle, one that needs longer than
    the budget, or an exhausted budget gives up at once, so the wait is
    bounded by ``patience_s`` per exiter and a stopped queue beside a halted
    exiter is given up on the first step as before; ``patience_s`` of ``0``
    never waits. Holding the abreast vehicle instead (commanding it to stop
    beside the exiter) was derived inert — a vehicle beside the exiter opens
    nothing by stopping, it makes the crossing pair — and measured as such
    (one vehicle-step held over the grid, the runs otherwise this rule's).

    Measured on the same grid and **shipped off** (``exit_abreast_patience_s``
    defaults to 0): at 10 s the rule rescues the sliding vehicle's exiter
    where it was written (the creeping-past give-ups 10 → 1, Ruth St
    corridor fleet at the exit peak seed 3: 273 → 280 of 290 exited) but
    the give-ups read 46 against 44, because the waited exiter then meets
    the next follower inside its brake distance (the brake-distance class
    9 → 18), and the wait holds lane 1: T.H.52 at capacity seed 4 departs
    386 of 466 against 401 with lane 1 at the gore at 2.7 m/s for three
    minutes against 10.7 in none, the two-entrance defaults at seed 3
    394 of 470 against 417. With WP-52's braking patience beside it the
    give-ups are 44 again, with 16 fewer exits, six more lane-1 minutes at
    or below 5 m/s and the T.H.52 capacity entrance at seed 5 at 372 of
    466, under the no-lock pin. The 5 s and 20 s bounds are the 10 s runs
    within one give-up (the clearing condition ends the wait, the bound
    binds nowhere above 10 s).

    Args:
        st: The exiter's per-vehicle state (``giveup_since`` read and written).
        t: Simulation time [s].
        patience_s: ``exit_abreast_patience_s``.
        a_id: The overlapping auxiliary-lane vehicle (``None`` = none).
        v_a: Its speed [m/s].
        clear_m: :func:`_weave_abreast_clear_m` for it.
        a_is_entrant: Whether it is a driven entrant of this section.

    Returns:
        ``True`` to keep the exiter this step (deferred as a forced change
        is, ``n_giveup_waited`` counted by the caller); ``False`` to give up.
    """
    if patience_s <= 0.0 or a_id is None or a_is_entrant:
        return False
    if st.get("giveup_since") is None:
        st["giveup_since"] = t
    budget = patience_s - (t - st["giveup_since"])
    if budget <= 0.0 or v_a < HALTING_SPEED_MS:
        return False
    return clear_m / v_a <= budget


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


def _idm_desired_gap(v: float, dv: float, T: float, a_max: float, b: float, s0: float) -> float:
    """IDM desired (safe) gap ``s*(v, Δv) = s0 + max(0, v·T + v·Δv / (2·√(a_max·b)))`` [m].

    The gap at which the model's interaction term equals its free term: with
    a leader exactly ``s*`` ahead the vehicle holds its speed (its
    acceleration is ``−a_max·(v/v0)^4``, the free-road deficit it has
    anyway), any smaller gap makes it brake (CLAUDE.md §3.1; Treiber,
    Hennecke & Helbing 2000).
    """
    return s0 + max(0.0, v * T + v * dv / (2.0 * math.sqrt(a_max * b)))


def _weave_vacate_gap_ok(
    x_c: float,
    v_c: float,
    p_c: dict[str, float],
    target: Sequence[tuple[float, str]],
    v_of: dict[str, float],
    p_of: dict[str, dict[str, float]],
    accept_s: float,
) -> bool:
    """Whether the target-lane gap a through vehicle is in accepts it without follower braking.

    The vacate rule's acceptance (2026-09-24, block 3, re-derived): the
    leader L is the first target-lane vehicle whose front is ahead of the
    changer's front, the follower F the last one whose front is not; the
    gap accepts when ``g_lead ≥ s0_c + accept_s · v_c`` and
    ``g_foll ≥ s0_c + accept_s · v_F`` (the weave's own time-gap
    acceptance, ``_weave_step``) **and** ``g_foll ≥ s*_F(v_F, v_F − v_c)``,
    F's IDM desired gap towards the changer at its current speed
    (:func:`_idm_desired_gap`) — so F, with the changer in front of it,
    needs no braking beyond its own free-road term. A vehicle beside the
    changer (front ahead, rear behind the changer's front) is a leader with
    a negative gap; one whose front is level with or behind the changer's
    but ahead of its rear is a follower with a negative gap; both refuse.

    Args:
        x_c: The changer's front-bumper position on the section axis [m].
        v_c: Its speed [m/s].
        p_c: Its constants (:func:`_weave_veh`).
        target: Target-lane vehicles as ``(x, id)``, ascending ``x``.
        v_of: Their speeds [m/s].
        p_of: Their constants (every id in ``target`` present).
        accept_s: The accepted time gap [s] (``accept_gap_s``).

    Returns:
        Whether the vehicle may be asked to change this step.
    """
    xs = [x for x, _ in target]
    i = bisect.bisect_right(xs, x_c)
    if i < len(target):
        x_l, l_id = target[i]
        if x_l - p_of[l_id]["len"] - x_c < p_c["s0"] + accept_s * v_c:
            return False
    if i > 0:
        x_f, f_id = target[i - 1]
        p_f = p_of[f_id]
        v_f = v_of[f_id]
        g_foll = x_c - p_c["len"] - x_f
        if g_foll < p_c["s0"] + accept_s * v_f:
            return False
        if g_foll < _idm_desired_gap(v_f, v_f - v_c, p_f["T"], p_f["a"], p_f["b"], p_f["s0"]):
            return False
    return True


def _weave_vacate_bound_veh_h(ws: dict[str, Any], t: float) -> float:
    """The vacate rule's bound this step [veh/h]: ``vacate_max_veh_h`` when set, else the target lane's spare capacity.

    Spare capacity is ``VACATE_LANE_CAPACITY_VEH_H`` less the flow the
    target lane carried into the vacate window over the last
    ``VACATE_FLOW_WINDOW_S`` (vehicles first seen there in that lane, each
    once; :func:`_weave_vacate_step` keeps the sightings), floored at zero.
    The flow is read over the full window from ``t = 0``, so the first
    minute of a run underestimates it and the bound is permissive there.
    """
    fixed = float(ws["params"]["vacate_max_veh_h"])
    if fixed > 0.0:
        return fixed
    flow_s: deque[float] = ws["vacate_flow_s"]
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
) -> None:
    """Through traffic vacates the weave lane upstream of the section (2026-09-24, block 3).

    The one rule of the third derivation (docs/WEAVE_MODEL_PLAN.md, dated
    paragraph): a saturated one-sided weave is carried by *through* vehicles
    leaving the weave lane before the section — "through traffic keep left"
    signage and driver anticipation — so that the entering and the exiting
    movements exchange over the auxiliary lane and the weave lane alone.
    SUMO's LC2013 does not do this on its own once the lane crawls (a
    speed-gain change needs a speed advantage a uniform crawl does not
    offer). Two forms, chosen by ``vacate_no_follower_braking``
    (``WEAVE_DEFAULTS``), both under the bound below.

    **The default form (0).** Each through vehicle (not bound for the
    paired exit, nor for an off-ramp leaving from a window edge —
    ``vacate_exempt_ids``, :func:`_weave_vacate_exempt_ids`; review,
    2026-09-24 block 3) in the lane feeding section lane 1 of a corridor edge
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

    **The gap-conditioned form (1; block 3, the rule re-derived so that it
    never asks the target lane to brake).** On the corridor's last 850 m
    (``tests/fixtures/weave_th52_upstream.osm``) the default form put
    ≈ 780 veh/h of requests into a lane carrying ≈ 560 veh/h and the lane
    asked into crawled at 1.3–1.4 m/s, the head of the I-94 WB queue
    (docs/WEAVE_MODEL_PLAN.md, the upstream-entrance fixture). In this form
    a through vehicle in the window is evaluated every step, nearest the
    section first, against the target-lane gap it is in
    (:func:`_weave_vacate_gap_ok`: the weave's own time gaps to the leader
    and the follower, ``accept_gap_s``, plus the follower's IDM desired gap
    at its current speed, so the follower needs no braking), and asked only
    on a step when the gap accepts: ``changeLane(vid, lane_to, step_s)`` —
    one request per accepting step — under ``LC_MODE_SCRIPTED_SAFE_NO_ADAPT``
    (mode 768: SUMO's safety check, **no speed adaptation**). The hold is
    set at the first request and restored as in the default form (no stay
    is needed: a request lives one step). A vehicle never asked keeps its
    own mode. **Measured and not made the default** (docs/WEAVE_MODEL_PLAN.md,
    dated section): at the corridor's demand the candidates reach the
    window at 2–6 m/s behind the entrance's merge while the target lane
    runs at 10–25 m/s, no gap accepts that approach rate, vacating
    collapses (2–34 per 20 min against 159–201) and the section's own
    end-lock of the third derivation's "without the rule" case returns.

    **The bound (both forms).** Vehicles first asked in the last
    ``VACATE_FLOW_WINDOW_S`` are limited to ``vacate_max_veh_h``: a positive
    value is the bound, ``0`` (the default) the target lane's spare
    capacity ``VACATE_LANE_CAPACITY_VEH_H − flow``, the flow being the
    vehicles the target lane carried into the window over the same 60 s
    (:func:`_weave_vacate_bound_veh_h`), so the rule cannot ask more into
    the target lane than it has room for. ``n_vacate_skipped_no_gap`` counts
    through vehicles that crossed the window never asked — for want of an
    accepting gap or of the bound — each once (one that moves left by its
    own model is not counted); ``n_vacate_requests`` the requests made, in
    vehicle-steps.

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
    prm = ws["params"]
    gap_conditioned = float(prm["vacate_no_follower_braking"]) > 0.0
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
    for vid, res in results.items():
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
        elif in_lane(vid, 0) and (gap_conditioned or t < st["until_s"]):
            lane_to_here = spec[road][1]
            if not gap_conditioned and lane_to_here != st["lane_to"]:
                # crossed onto a window edge where the target lane has
                # another index (a lane added or dropped): the open request
                # re-addressed there for the rest of its life
                st["lane_to"] = lane_to_here
                mod.vehicle.changeLane(vid, lane_to_here, max(st["until_s"] - t, step_s))
            continue  # still in the weave lane with the request open (or re-evaluated below)
        else:
            ws["n_vacate_refused"] += 1
        if not gap_conditioned and t < st["until_s"]:
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
    # --- evaluate, nearest the section first ------------------------------
    v_of = {vid: float(results[vid][tc.VAR_SPEED]) for _, vid in target} if now else {}
    p_of = {vid: _weave_veh(mod, ws, vid) for _, vid in target} if now else {}
    accept = float(prm["accept_gap_s"])
    for vid, x in sorted(now.items(), key=lambda kv: -kv[1]):
        if vid in active:
            if not gap_conditioned:
                continue  # the default form's request is open: nothing more is sent
        elif vid in seen:
            continue  # asked before (the default form asks once)
        v = float(results[vid][tc.VAR_SPEED])
        if gap_conditioned and not _weave_vacate_gap_ok(
            x, v, _weave_veh(mod, ws, vid), target, v_of, p_of, accept
        ):
            if vid not in active:
                pending.add(vid)
            continue
        if vid not in active:
            if budget <= 0:
                pending.add(vid)
                continue
            mode_orig = int(mod.vehicle.getLaneChangeMode(vid))
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
            if gap_conditioned:
                active[vid] = {"lc_mode_orig": mode_orig, "until_s": math.inf, "lane_to": lane_to}
                mod.vehicle.setLaneChangeMode(vid, LC_MODE_SCRIPTED_SAFE_NO_ADAPT)
            else:
                duration = max((x_start - x) / max(v, SCRIPTED_MERGE_CREEP_MS), step_s)
                active[vid] = {
                    "lc_mode_orig": mode_orig,
                    "until_s": t + duration,
                    "lane_to": lane_to,
                }
                mod.vehicle.setLaneChangeMode(vid, LC_MODE_SCRIPTED_SAFE)
                mod.vehicle.changeLane(vid, lane_to, duration)
                ws["n_vacate_requests"] += 1
                continue
        mod.vehicle.changeLane(vid, spec[where[vid][0]][1], step_s)
        ws["n_vacate_requests"] += 1


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
            a_f = _idm_accel(
                v_of[f_id],
                v0_of[f_id],
                # exit priority: F holds one changer minGap farther back
                s_f - p_c["s0"] if priority else s_f,
                v_of[f_id] - v_c,
                p_f["T"],
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
                    v_c, v0_c, s_l, v_c - v_of[l_id], p_c["T"], p_c["a"], p_c["b"], p_c["s0"]
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
) -> None:
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
    """
    a_cmd = max(a_target, -p["b"])
    lead = mod.vehicle.getLeader(vid, LEADER_LOOKAHEAD_M)
    if lead is None or lead[0] == "" or lead[1] < 0.0:
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
    if f_t is not None:
        _weave_command(mod, coop, f_t, v_of[f_t], v0_of[f_t], p_of[f_t], a_f, step_s)
    if l_t is not None and a_c < 0.0:
        s_l = x_of[l_t] - p_of[l_t]["len"] - x_of[vid]
        # sixth derivation: on the ramp, a gap leader whose rear is behind the
        # entrant's front is beside it, not ahead of it
        beside = road in ws["ramp_edges"] and s_l < 0.0
        if not beside and _weave_easing_ok(
            v_c, v_of[l_t], s_l, p_c["s0"] + accept_s * v_c, remaining_m, p_c["b"]
        ):
            _weave_command(mod, coop, vid, v_c, v0_c, p_c, a_c, step_s, follower=False)
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


def _weave_step(mod: Any, tc: Any, ws: dict[str, Any], results: Any, t: float) -> None:
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
    trace). ``courtesy`` is accepted for config-hash stability but inert.
    Every speed request below is a one-step car-following target
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

    **Vacating the weave lane** (2026-09-24, block 3, third derivation,
    re-derived; :func:`_weave_vacate_step`). A through vehicle in the lane
    feeding section lane 1, within ``vacate_ahead_m`` of the section start
    on the corridor edge before it, is asked to move one lane left — the
    signage / anticipation that empties the weave lane for the exchange —
    only on a step when the target-lane gap it is in accepts it without
    the follower braking (:func:`_weave_vacate_gap_ok`), under mode 768
    (SUMO's safety check, no speed adaptation), within the target lane's
    spare capacity (``vacate_max_veh_h``); a vehicle never offered such a
    gap stays. Counted in ``n_vacated``, ``n_vacate_refused``,
    ``n_vacate_skipped_no_gap`` and ``n_vacate_requests``.

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
    follower. A step with no request leaves the vehicle on mode 512. Control is
    handed back (mode restored) when the vehicle has no change left to make;
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
    for vid, res in results.items():
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
        elif lane == 0 and ws["exit_only"][road]:
            pending[vid] = 1
    for lst in lanes.values():
        lst.sort()
    # through traffic vacates the weave lane upstream of the section (third
    # derivation, 2026-09-24 block 3): the only rule touching through vehicles
    # other than as a chosen gap's follower
    _weave_vacate_step(mod, tc, ws, results, lanes, t)
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
                "lc_mode_orig": int(mod.vehicle.getLaneChangeMode(vid)),
                "s0": float(mod.vehicle.getMinGap(vid)),
                "mode": LC_MODE_SCRIPTED_SAFE,
                # the gap chosen on the ramp, if any, carries over
                "target": ws["pre"].pop(vid, None),
                # the give-up patience (WP-52): last step's target-lane
                # follower and its speed, the first refused step
                "foll_prev": None,
                "giveup_since": None,
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
            # the vehicle beside the exiter (WP-53): the leader side's while
            # it is under the acceptance's floor, else an overlapping follower
            lead_need = st["s0"] + accept * v_ego
            if g_lead < lead_need:
                a_id, v_a = _l_id, v_lead
            elif g_foll < 0.0:
                a_id, v_a = f_id, v_foll
            else:
                a_id, v_a = None, math.nan
            if a_id is not None:
                p_a = _weave_veh(mod, ws, a_id)
                p_c = _weave_veh(mod, ws, vid)
                clear_m = _weave_abreast_clear_m(
                    g_lead, g_foll, lead_need, p_c["len"], st["s0"], p_a["len"], p_a["s0"]
                )
            else:
                clear_m = 0.0
            if _weave_giveup_patient(
                st, t, prm["exit_giveup_patience_s"], f_id, g_foll, v_foll, b_f, step_s
            ) or _weave_giveup_abreast(
                st,
                t,
                prm["exit_abreast_patience_s"],
                a_id,
                v_a,
                clear_m,
                a_id in veh and veh[a_id]["dir"] > 0,
            ):
                # the refusal is a transient — the follower still braking
                # towards the gap (bounded give-up patience, WP-52) or the
                # vehicle beside the exiter clearing it within the budget
                # (the abreast state, WP-53): kept this step, the request
                # deferred below as a forced change is
                ws["n_giveup_waited"] += 1
            else:
                # the exit is missed: a vehicle halted within exit_giveup_m
                # of the gore's end with no change to request this step
                # continues on the mainline (rerouted to the corridor's end)
                # instead of being held by SUMO at the end of a lane its
                # route does not continue on, where it stopped the through
                # lane behind it and the auxiliary lane beside it (exit-side
                # derivation, 2026-09-24 block 3). One still rolling there
                # may yet drop in: v00010 of the moderate fixture forced its
                # change in the last 3 m at 2-3 m/s (session record)
                mod.vehicle.changeTarget(vid, ws["through_target"])
                mod.vehicle.setLaneChangeMode(vid, st["lc_mode_orig"])
                del veh[vid]
                ws["gave_up"].add(vid)
                awaiting_exit.discard(vid)
                ws["n_missed"] += 1
                ws["n_missed_exit"] += 1
                continue
        # the follower reported this step, for the patience's speed history
        st["foll_prev"] = (f_id, v_foll)
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
            d < 0 and st["zone_s"] is not None and t - st["zone_s"] >= rule["force_after_s"],
        )
        # --- execution -----------------------------------------------------
        if accepted:
            # accepted: executed under mode 256 for one step (the follower
            # yields; SUMO still refuses an immediate collision). Under mode
            # 512 SUMO refused the change while the follower was closing from
            # far back and braked the changer to drop in behind it
            _weave_set_mode(mod, vid, st, LC_MODE_SCRIPTED_FORCE)
            mod.vehicle.changeLane(vid, lane + d, step_s)
            st["requested_s"] = t
        elif force:
            if forced_ok:
                # a forced request lives one step only, so it is executed
                # under the gaps just checked or not at all
                _weave_set_mode(mod, vid, st, LC_MODE_SCRIPTED_FORCE)
                mod.vehicle.changeLane(vid, lane + d, step_s)
                st["requested_s"] = t
                st["forced"] = True
            else:
                # deferred: back under SUMO's own safety check, so a pending
                # request cannot execute into the gap that was just refused
                _weave_set_mode(mod, vid, st, LC_MODE_SCRIPTED_SAFE)
                ws["n_forced_deferred"] += 1
        else:
            # no request this step: never leave a one-step forced mode standing
            _weave_set_mode(mod, vid, st, LC_MODE_SCRIPTED_SAFE)
    # entering vehicles still on the ramp: their gap is chosen and its
    # follower cooperates before they appear on lane 0
    pre: dict[str, str | None] = ws["pre"]
    for vid in [v for v in pre if v not in approaching]:
        del pre[vid]
    for vid in sorted(approaching):
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
            # the ramp to the gore, then the whole section
            x_start - x_of[vid] + section_len,
        )
    for vid in yielders:
        coop.pop(vid, None)  # no target in either role this step
    for fid in sorted(coop):
        v_new, a_cmd, follower = coop[fid]
        mod.vehicle.slowDown(fid, v_new, 0.0)
        if follower:
            ws["n_cooperations"] += 1
            ws["coop_decel_sum"] += -a_cmd
        else:
            ws["n_changer_eased"] += 1


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
    once, ``n_vacate_skipped_no_gap`` (block 3, the rule re-derived) the
    through vehicles that crossed the window never asked — no step on
    which the target-lane gap accepted them within the bound — each once,
    and ``n_vacate_requests`` the requests made, in vehicle-steps;
    ``n_pair_releases`` counts stopped crossing pairs released
    (:func:`_weave_pair_release`), each pair once per release;
    ``n_missed_exit`` (exit-side derivation) the exit-bound vehicles the
    runner itself rerouted through at the gore's end, halted (below
    ``HALTING_SPEED_MS``) still owing their change with no more than
    ``exit_giveup_m`` of section ahead — a subset of ``n_missed``, so the
    identity above holds; ``n_giveup_waited`` (WP-52, the bounded give-up
    patience) the vehicle-steps on which such a give-up was deferred because
    the exiter's auxiliary-lane follower was still braking towards the gap
    (:func:`_weave_giveup_patient`; zero at ``exit_giveup_patience_s`` = 0).
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
        "n_giveup_waited": ws["n_giveup_waited"],
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


def _leader_obs(lib_mod: Any, veh_id: str, ego_min_gap: float) -> tuple[float, float]:
    """(bumper-to-bumper gap [m], leader speed [m/s]); (inf, nan) if none.

    ``vehicle.getLeader`` returns the distance from the ego front bumper
    **plus minGap** to the leader's back (verified against SUMO 1.27), so the
    ego's drawn ``s0`` is added back to obtain the bumper-to-bumper gap the
    controller contract requires.
    """
    lead = lib_mod.vehicle.getLeader(veh_id, LEADER_LOOKAHEAD_M)
    if lead is None or lead[0] == "" or lead[1] < 0.0:
        return math.inf, math.nan
    return lead[1] + ego_min_gap, float(lib_mod.vehicle.getSpeed(lead[0]))


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
    sums = np.zeros(n_bins)
    counts = np.zeros(n_bins)
    np.add.at(sums, idx, vs[sel])
    np.add.at(counts, idx, 1.0)
    with np.errstate(invalid="ignore"):
        means = np.where(counts > 0, sums / np.maximum(counts, 1.0), np.nan)
    return tuple(float(m) for m in means)


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
    nt = max(math.ceil(duration_s / EDGES_DT_BIN_S), 1)
    nx = max(math.ceil(total_length_m / EDGES_DX_BIN_M), 1)
    tts = np.zeros((nt, nx))
    ttd = np.zeros((nt, nx))
    if len(traj):
        ti = np.minimum((traj["t"].to_numpy() / EDGES_DT_BIN_S).astype(np.int64), nt - 1)
        xi = np.clip((traj["x"].to_numpy() / EDGES_DX_BIN_M).astype(np.int64), 0, nx - 1)
        v = traj["v"].to_numpy()
        np.add.at(tts, (ti, xi), sample_dt_s)
        np.add.at(ttd, (ti, xi), v * sample_dt_s)
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

    Rows are appended column-wise into ``cols``; :meth:`maybe_flush` writes a
    Parquet row group (through an open file object, see :func:`_write_parquet`)
    once :data:`TRAJ_FLUSH_ROWS` are buffered, keeping only a compact numpy
    copy of ``(t, x, v)`` for the post-run Edie edges frame.
    """

    def __init__(self, path: Path, is_ring: bool) -> None:
        fields = list(_TRAJ_SCHEMA_BASE)
        if is_ring:
            fields.append(("x_unwrapped", pa.float64()))
        self._fields = fields
        self.schema = pa.schema(fields)
        self.cols: dict[str, list[Any]] = {name: [] for name, _ in fields}
        self._sink = open(path, "wb")
        self._writer = pq.ParquetWriter(self._sink, self.schema)
        self._txv: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        self.n_rows = 0

    def maybe_flush(self, force: bool = False) -> None:
        n = len(self.cols["t"])
        if n == 0 or (n < TRAJ_FLUSH_ROWS and not force):
            return
        arrays = [pa.array(self.cols[name], type=dtype) for name, dtype in self._fields]
        self._writer.write_table(pa.Table.from_arrays(arrays, schema=self.schema))
        self._txv.append(
            (
                np.asarray(self.cols["t"], dtype=np.float64),
                np.asarray(self.cols["x"], dtype=np.float64),
                np.asarray(self.cols["v"], dtype=np.float64),
            )
        )
        self.n_rows += n
        for name in self.cols:
            self.cols[name] = []

    def close(self) -> pd.DataFrame:
        """Flush, close the file, and return the ``(t, x, v)`` frame of all rows."""
        self.maybe_flush(force=True)
        self._writer.close()
        self._sink.close()
        if not self._txv:
            return pd.DataFrame({"t": [], "x": [], "v": []}, dtype=np.float64)
        return pd.DataFrame(
            {
                "t": np.concatenate([c[0] for c in self._txv]),
                "x": np.concatenate([c[1] for c in self._txv]),
                "v": np.concatenate([c[2] for c in self._txv]),
            }
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

    if cfg.fleet.delta != 4.0:
        notes.append(
            f"fleet.delta={cfg.fleet.delta} requested but SUMO's IDM fixes the "
            "acceleration exponent at 4 (not a vType attribute); ran with delta=4"
        )

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
    if cfg.av.controller is not None:
        controller_fn = get_vehicle_controller(cfg.av.controller)
        controller_params = {
            **default_params(cfg.av.controller),
            **cfg.av.controller_params,
        }
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

    # Measured downstream boundary condition (docs/CONTRACTS.md §2): a speed
    # schedule on the exit-buffer edge OUTSIDE the corridor proper, standard
    # FHWA microsim calibration practice for congestion entering the modeled
    # section from downstream (FHWA-HOP-18-036; see BoundarySpec docstring).
    boundary_steps: list[tuple[float, float]] = []
    boundary_spec = getattr(cfg.network, "boundary", None)
    if boundary_spec is not None and bundle.exit_edge is not None:
        boundary_steps = [(float(ts), float(vs)) for ts, vs in boundary_spec.steps]
    boundary_idx = 0
    # Apply every step scheduled at or before t = 0 up front.
    while boundary_idx < len(boundary_steps) and boundary_steps[boundary_idx][0] <= 0.0:
        mod.edge.setMaxSpeed(bundle.exit_edge, boundary_steps[boundary_idx][1])
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
        chain_m = list(cfg.network.corridor_edges)
        for ramp_m in cfg.network.ramps:
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
                    "yielding": {},
                    "n_entered": 0,
                    "n_changed": 0,
                    "n_forced": 0,
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
                    # vehicle-steps a halted exiter's give-up was deferred
                    # by the bounded patience (WP-52)
                    "n_giveup_waited": 0,
                    # exit-bound vehicles rerouted through at the gore's end
                    # (exit-side derivation): no longer driven; their new
                    # destination is the corridor's last edge
                    "gave_up": set(),
                    "through_target": chain_w[-1],
                    "n_forced_deferred": 0,
                    "n_cooperations": 0,
                    "coop_decel_sum": 0.0,
                    "n_changer_eased": 0,
                    "n_vacated": 0,
                    "n_vacate_refused": 0,
                    "n_vacate_skipped_no_gap": 0,
                    "n_vacate_requests": 0,
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
    traj_writer = _TrajectoryWriter(traj_path, is_ring)
    cols = traj_writer.cols

    try:
        for k in range(n_steps):
            mod.simulationStep()
            t = float(mod.simulation.getTime())
            if mod.simulation.getCollidingVehiclesNumber():
                for c in mod.simulation.getCollisions():
                    n_collisions += 1
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
                mod.edge.setMaxSpeed(bundle.exit_edge, boundary_steps[boundary_idx][1])
                boundary_idx += 1

            for vid in mod.simulation.getDepartedIDList():
                mod.vehicle.subscribe(vid, sub_vars)
                n_departed += 1
                if has_ramps:
                    rid = route_by_id.get(vid, "main")
                    n_departed_by_route[rid] = n_departed_by_route.get(rid, 0) + 1
            results = mod.vehicle.getAllSubscriptionResults()
            # Fuel is accounted for every vehicle; the linear-x state (and
            # therefore controllers, VSL, trajectories) covers vehicles on
            # corridor edges only — ramp edges have no linear x.
            if has_ramps:
                for vid, res in results.items():
                    if res[tc.VAR_ROAD_ID] not in offsets_by_edge:
                        fuel_mg[vid] = fuel_mg.get(vid, 0.0) + res[tc.VAR_FUELCONSUMPTION] * step
                ids = sorted(v for v in results if results[v][tc.VAR_ROAD_ID] in offsets_by_edge)
            else:
                ids = sorted(results)
            if not ids:
                v_ref_hist.append((t, 0.0))
                while v_ref_hist and v_ref_hist[0][0] < t - V_REF_WINDOW_S:
                    v_ref_hist.popleft()
                continue

            speeds = np.array([results[v][tc.VAR_SPEED] for v in ids])
            xs = np.array(
                [
                    offsets_by_edge[results[v][tc.VAR_ROAD_ID]] + results[v][tc.VAR_LANEPOSITION]
                    for v in ids
                ]
            )
            if is_ring:
                xs = xs % circumference

            # Fuel accumulation (mg/s × step, every step — see module docstring).
            for i, vid in enumerate(ids):
                fuel_mg[vid] = fuel_mg.get(vid, 0.0) + results[vid][tc.VAR_FUELCONSUMPTION] * step
                if is_ring:
                    last, unw = unwrap_x.get(vid, (xs[i], xs[i]))
                    d = (xs[i] - last + circumference / 2.0) % circumference - circumference / 2.0
                    unwrap_x[vid] = (float(xs[i]), unw + d)

            # Oracle snapshot buffer: the delayed oracle reads the traffic
            # state as it was `delay_s` ago (positions stay current).
            if oracle_delay_s > 0.0:
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
                        ms_r["released"].append(t)
                        ms_r["last_release_s"] = t

            # Scripted on-ramp merges (see the setup block above).
            for ss in scripted_states:
                _scripted_merge_step(mod, tc, ss, results, t)
            # Weaving sections (see the setup block above).
            for ws in weave_states:
                _weave_step(mod, tc, ws, results, t)

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
                o_xs, o_speeds = _stale_snapshot(oracle_history, t, oracle_delay_s, (xs, speeds))
                for vid in sorted(compliant_avs):
                    # Not in the network yet, already arrived, or still on a
                    # ramp edge (no corridor position, no downstream field):
                    # controllers act on corridor edges only.
                    if vid not in x_by_id:
                        continue
                    gap, v_leader = _leader_obs(mod, vid, min_gap_by_id[vid])
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
                for i, vid in enumerate(ids):
                    cols["t"].append(t)
                    cols["veh_id"].append(vid)
                    cols["x"].append(float(xs[i]))
                    cols["lane"].append(int(results[vid][tc.VAR_LANE_INDEX]))
                    cols["v"].append(float(speeds[i]))
                    cols["a"].append(float(results[vid][tc.VAR_ACCELERATION]))
                    cols["is_av"].append(is_av_by_id.get(vid, False))
                    cols["complied"].append(complied_by_id.get(vid, False))
                    cols["is_heavy"].append(is_heavy_by_id.get(vid, False))
                    cols["is_hov"].append(is_hov_by_id.get(vid, False))
                    if is_ring:
                        cols["x_unwrapped"].append(unwrap_x[vid][1])
                traj_writer.maybe_flush()
        n_arrived = n_departed - len(mod.vehicle.getIDList())
    finally:
        mod.close()

    # --- Artifacts --------------------------------------------------------
    traj_df = traj_writer.close()
    # Edie weighting uses the REALIZED sample interval (out_every whole steps),
    # not the nominal 1/output_hz: sampling happens on whole simulation steps,
    # so a requested rate that does not divide the step length is rounded down
    # and the nominal interval would scale density/flow by nominal/realized.
    edges_df = _edie_edges_frame(
        traj_df, out_every * step, cfg.sim.duration_s, bundle.total_length_m
    )
    edges_path = run_dir / "edges.parquet"
    _write_parquet(pa.Table.from_pandas(edges_df, preserve_index=False), edges_path)

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
                "n_unfinished": len(ss["veh"]),
                "wait_s_mean": float(np.mean(ss["waits_s"])) if ss["waits_s"] else None,
                "wait_s_p90": float(np.percentile(ss["waits_s"], 90)) if ss["waits_s"] else None,
            }
            for ss in scripted_states
        ],
        "weave_sections": [_weave_meta(ws, n_departed_by_route) for ws in weave_states],
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
        "controller": cfg.av.controller,
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
