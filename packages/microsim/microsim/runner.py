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

import dataclasses
import json
import math
import multiprocessing
import os
import platform
import time
from collections import deque
from collections.abc import Callable, Sequence
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
from microsim.networks import NetBundle, corridor, merge_patch_files, osm_import, ring
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
            internal_links=net.internal_links,
        )
        merge_ramps = [r for r in net.ramps if r.kind == "on" and r.merge != "lane_change"]
        if merge_ramps:
            # Second pass: on-ramp merge models are netconvert patches whose
            # inputs (lane counts, the end node, a dead-ending lane 0) come
            # from the first import (RampSpec.merge, docs/CONTRACTS.md §2).
            compiled = sumolib.net.readNet(str(bundle.net_path))
            chain = list(net.corridor_edges)
            patches: list[Path] = []
            for ramp in merge_ramps:
                i = chain.index(ramp.attach_edge)
                if i + 1 >= len(chain):
                    raise ValueError(
                        f"ramp {ramp.name or ramp.attach_edge}: merge model {ramp.merge!r} needs a "
                        "corridor edge after the attach edge"
                    )
                attach = compiled.getEdge(ramp.attach_edge)
                if attach.getLanes()[0].getOutgoing():
                    raise ValueError(
                        f"ramp {ramp.name or ramp.attach_edge}: merge model {ramp.merge!r} needs the "
                        "acceleration lane (lane 0 of the attach edge) to dead-end at the edge's end"
                    )
                nxt = compiled.getEdge(chain[i + 1])
                if ramp.merge == "scripted":
                    continue  # no patch: the runner drives the acceleration lane
                patches += merge_patch_files(
                    workdir / "patches",
                    ramp.attach_edge,
                    chain[i + 1],
                    attach.getToNode().getID(),
                    attach.getLaneNumber(),
                    nxt.getLaneNumber(),
                    ramp.merge,
                    visibility_m=ramp.merge_visibility_m,
                )
            if patches:
                bundle = osm_import(
                    osm_file=net.osm_file,
                    bbox=net.bbox,
                    corridor_edges=tuple(net.corridor_edges),
                    workdir=workdir,
                    keep_edges=keep,
                    patch_files=patches,
                    internal_links=net.internal_links,
                )
                bundle = dataclasses.replace(bundle, patch_files=tuple(str(p) for p in patches))
        if net.boundary is not None:
            # docs/CONTRACTS.md §2: on an OSM corridor the LAST corridor edge
            # plays the exit-buffer role and hosts the boundary schedule.
            bundle = dataclasses.replace(bundle, exit_edge=bundle.edge_ids[-1])
        return bundle
    raise TypeError(f"unsupported network type: {type(net).__name__}")


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


# SUMO laneChangeMode bit patterns (TraCI docs, "lane change mode"): every
# model-driven change off; bits 8-9 decide how a TraCI request treats others.
COLLISION_LOG_MAX = 50  # collision events kept verbatim in meta.json (the count is exact)
LC_MODE_SCRIPTED_SAFE = 512  # respect the speed / brake gaps of others, adapt speed
LC_MODE_SCRIPTED_FORCE = 256  # avoid immediate collisions only (the follower yields)
SCRIPTED_MERGE_CREEP_MS = 3.0  # desired-speed floor on the acceleration lane [m/s]
NEIGHBOR_LEFT_FOLLOWERS = 0  # vehicle.getNeighbors mode bits: bit0 right, bit1 leaders
NEIGHBOR_LEFT_LEADERS = 2


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
    # on-ramp's last edge; the rate comes from the registry controller.
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
                on_edge = list(mod.edge.getLastStepVehicleIDs(ms_r["edge"]))
                for vid in on_edge:
                    if vid not in ms_r["stopped_set"] and (
                        mod.vehicle.getLanePosition(vid) < ms_r["stop_pos_m"] - 1.0
                    ):
                        mod.vehicle.setStop(vid, ms_r["edge"], ms_r["stop_pos_m"], 0, 1.0e9)
                        ms_r["stopped_set"].add(vid)
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
        "config": cfg.model_dump(mode="json"),
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


def _map_replicates(
    worker: Callable[[Any], Any],
    payloads: Sequence[Any],
    seeds: Sequence[int],
    n_procs: int,
    timeout_s: float,
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

    Returns:
        Worker results in seed order.

    Raises:
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

    Returns:
        One :class:`RunPaths` per replicate, in seed order.

    Raises:
        RuntimeError: A worker died or a replicate raised (seed named).
        TimeoutError: The budget elapsed with replicates outstanding.
    """
    seeds = spawn_seeds(cfg.seed, cfg.replicates)
    cfg_json = cfg.model_dump(mode="json")
    payloads = [(cfg_json, s, str(out_root)) for s in seeds]
    n_procs = n_procs or min(multiprocessing.cpu_count(), len(seeds))
    raw = _map_replicates(
        _replicate_worker,
        payloads,
        seeds,
        n_procs,
        replicate_timeout_s(cfg) if timeout_s is None else timeout_s,
    )
    paths = [
        RunPaths(run_dir=Path(a), trajectories=Path(b), edges=Path(c), meta=Path(d))
        for a, b, c, d in raw
    ]
    for p in paths:
        require_complete_run(p.run_dir)
    return paths
