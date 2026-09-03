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
import platform
import time
from collections import deque
from collections.abc import Sequence
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
    SegmentControllerFn,
    SegmentObs,
    VehicleControllerFn,
)
from flowstate_core.rng import make_rng, spawn_seeds, sumo_seed
from microsim.networks import NetBundle, corridor, osm_import, ring
from microsim.vehicles import (
    FleetPlan,
    build_corridor_plan,
    build_ring_plan,
    load_idm_calibration,
    ramp_routes,
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
        bundle = osm_import(
            osm_file=net.osm_file,
            bbox=net.bbox,
            corridor_edges=tuple(net.corridor_edges),
            workdir=workdir,
            keep_edges=tuple(e for r in net.ramps for e in r.edges),
        )
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
        plan = build_corridor_plan(inflow, cfg.sim.duration_s, cfg.fleet, cfg.av, rng)
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
]


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
    mod.start(sumo_cmd)

    step = cfg.sim.step_length_s
    n_steps = round(cfg.sim.duration_s / step)
    out_every = max(round(1.0 / (cfg.sim.output_hz * step)), 1)
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

    traj_path = run_dir / "trajectories.parquet"
    traj_writer = _TrajectoryWriter(traj_path, is_ring)
    cols = traj_writer.cols

    try:
        for k in range(n_steps):
            mod.simulationStep()
            t = float(mod.simulation.getTime())

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
                    if is_ring:
                        cols["x_unwrapped"].append(unwrap_x[vid][1])
                traj_writer.maybe_flush()
        n_arrived = n_departed - len(mod.vehicle.getIDList())
    finally:
        mod.close()

    # --- Artifacts --------------------------------------------------------
    traj_df = traj_writer.close()
    edges_df = _edie_edges_frame(
        traj_df, 1.0 / cfg.sim.output_hz, cfg.sim.duration_s, bundle.total_length_m
    )
    edges_path = run_dir / "edges.parquet"
    _write_parquet(pa.Table.from_pandas(edges_df, preserve_index=False), edges_path)

    fuel_ml = {vid: fuel_mg_to_ml(mg) for vid, mg in sorted(fuel_mg.items())}
    wall = time.perf_counter() - t_wall0
    meta: dict[str, Any] = {
        "config": cfg.model_dump(mode="json"),
        "config_hash": chash,
        "seed": seed,
        "sumo_seed": sumo_seed(seed),
        "versions": _versions(),
        "tier": "micro",
        "seeded": cfg.seeded,
        "wall_time_s": wall,
        "realtime_factor": cfg.sim.duration_s / wall if wall > 0 else None,
        "n_vehicles_planned": plan.n,
        "n_vehicles_departed": n_departed,
        "n_vehicles_arrived": max(n_arrived, 0),
        "av_ids": list(plan.av_ids),
        "complied_ids": list(plan.complied_ids),
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
    meta_path = run_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
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


def run_replicates(
    cfg: ScenarioConfig, out_root: str | Path, n_procs: int | None = None
) -> list[RunPaths]:
    """Run ``cfg.replicates`` seeded replicates in a spawn process pool.

    Replicate seeds come from ``spawn_seeds(cfg.seed, cfg.replicates)``
    (docs/CONTRACTS.md §6) so adding replicates never reshuffles existing
    ones. libsumo is a per-process singleton, so parallelism is
    process-level: the pool uses the ``spawn`` start method and each worker
    imports SUMO inside the child (CLAUDE.md §3.4).

    Args:
        cfg: Scenario configuration.
        out_root: Run-tree root passed to each :func:`run_micro`.
        n_procs: Pool size (default: ``min(cpu_count, replicates)``).

    Returns:
        One :class:`RunPaths` per replicate, in seed order.
    """
    seeds = spawn_seeds(cfg.seed, cfg.replicates)
    cfg_json = cfg.model_dump(mode="json")
    payloads = [(cfg_json, s, str(out_root)) for s in seeds]
    n_procs = n_procs or min(multiprocessing.cpu_count(), len(seeds))
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(processes=n_procs) as pool:
        raw = pool.map(_replicate_worker, payloads)
    return [
        RunPaths(run_dir=Path(a), trajectories=Path(b), edges=Path(c), meta=Path(d))
        for a, b, c, d in raw
    ]
