"""Macro-tier run orchestration: ``ScenarioConfig`` → ``edges.parquet`` + ``meta.json``.

Executes a scenario on the CTM screening tier and writes the contract run
layout (docs/CONTRACTS.md §3)::

    <out_dir>/<config_hash>/<seed>/
        edges.parquet   # t_bin, x_bin, mean_speed, density, flow
        meta.json       # config snapshot, hash, seed, versions, tier, ...

Every meta.json from this tier carries ``tier="screening"`` — macro results
may never back a validation report (CLAUDE.md §5.6) and never support claims
about phantom-jam formation or dissipation (ADR-1: LWR is string-stable by
construction).

Seeded perturbation (``PerturbationSpec``): implemented as a *temporary local
capacity/speed reduction* — for ``t ∈ [t_s, t_s + duration_s]`` the flux
through the interface nearest ``position_m`` is capped at the capacity of the
fundamental diagram with its free-flow speed reduced to
``max(V_e(ρ_local) − v_drop_ms, 0)`` (see
:func:`macrosim.fundamental.capacity_at_speed`). This mimics a slow vehicle or
incident at that location; any such run is labeled ``seeded=True``.

AV actuation: AVs are moving bottlenecks (:mod:`macrosim.bottleneck`). Their
commanded speed comes from the ``controllers`` registry when that package is
importable (it may be built concurrently — the import happens lazily inside
:func:`run_macro`); otherwise a fixed ``v_star_ms`` argument is accepted and
the degradation is noted in meta.json. Per-AV compliance is drawn once per run
(Bernoulli, seeded) — v1's per-step unseeded compliance coin-flip is retired
(CLAUDE.md §12.6).

Variable speed limits (``cfg.av.vsl``, CLAUDE.md §4.4 "macro tier via capping
``V_f`` per cell"): the corridor is cut into ~1 km gantry segments of cells
(:func:`controllers.vsl.gantry_segments`, the same rule the micro runner
applies to edges) and every :data:`VSL_INTERVAL_S` the segment controller is
called with the mean cell speed/density per segment. Each posted limit is
scaled by fleet compliance against the diagram's ``v_f``
(:func:`controllers.vsl.effective_limit`) and applied by replacing the
free-flow branch of the capped cells' fundamental diagram with the effective
limit ``v_lim``: the sending (demand) function of a capped cell becomes
``min(v_lim·ρ, q_cap)`` and its receiving (supply) function is bounded by
``q_cap = capacity_at_speed(fd, v_lim)`` — the reduced triangular diagram
with unchanged ``w`` and ``ρ_jam`` — realized through the solver's
per-interface flux caps, so the CTM kernel itself is untouched. This is the
standard CTM-based VSL representation (Hadiuzzaman & Qiu 2013, Can. J. Civ.
Eng. 40(1):46–56: VSL as a modified fundamental diagram in the CTM). The
equilibrium speed reported for a capped cell is ``min(v_lim, V_e(ρ))``, the
reduced diagram's ``V_e``. The CFL step is fixed from the *uncapped* ``v_f``
(the largest characteristic speed any cell can have), so stability is
unaffected; conservation is untouched because caps only lower fluxes.
"""

from __future__ import annotations

import json
import math
import platform
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from flowstate_core.artifacts import TriangularFD
from flowstate_core.config import CorridorNetwork, RingNetwork, ScenarioConfig, config_hash
from flowstate_core.controller_types import (
    ControllerObs,
    Memory,
    SegmentControllerFn,
    SegmentObs,
    VehicleControllerFn,
)
from flowstate_core.rng import make_rng
from macrosim.bottleneck import BottleneckVariant, MovingBottleneck, VStarTrajectory
from macrosim.ctm import CTMSolver, cfl_max_dt
from macrosim.fundamental import (
    capacity_at_speed,
    equilibrium_speed,
    equilibrium_speed_scalar,
    v1_legacy_fd,
)

__all__ = ["VSL_INTERVAL_S", "run_macro"]

VSL_INTERVAL_S: float = 30.0
"""VSL dispatch cadence [s]; mirrors ``microsim.runner.VSL_INTERVAL_S`` (CLAUDE.md §4.4)."""

_CFL_SAFETY = 0.9
"""Fraction of the CFL-limit time step the runner targets (v1 used 0.9 too)."""

_DOWNSTREAM_BINS = 5
"""Number of downstream cells exposed to vehicle controllers via the obs."""

_RHO_EPS = 1e-6
"""Density [veh/m] below which the local mean spacing is treated as infinite."""


def _inflow_at(steps: list[tuple[float, float]], t: float) -> float:
    """Piecewise-constant inflow [veh/s] at time ``t`` (0 before first step)."""
    q = 0.0
    for t_start, q_step in steps:
        if t >= t_start:
            q = q_step
        else:
            break
    return q


def _load_controller(
    name: str,
    overrides: dict[str, float],
) -> tuple[VehicleControllerFn | None, dict[str, float], str]:
    """Lazily resolve a vehicle controller from the ``controllers`` registry.

    The controllers package may be under concurrent construction; when it (or
    its registry) is not importable the caller falls back to a fixed
    ``v_star`` and the note is recorded in meta.json. An *unknown controller
    name* with an importable registry is a genuine configuration error and
    propagates (``KeyError``, per docs/CONTRACTS.md §1).

    Args:
        name: Registry name of the controller.
        overrides: Scenario-supplied parameter overrides.

    Returns:
        ``(controller_fn or None, merged_params, note)``.
    """
    try:
        from controllers import registry  # lazy: package may be built concurrently
    except ImportError:
        return None, {}, f"controllers package unavailable; controller {name!r} not applied"
    try:
        fn = registry.get_vehicle_controller(name)
        params = dict(registry.default_params(name))
    except AttributeError:
        return None, {}, "controllers.registry incomplete; fixed v_star fallback used"
    params.update(overrides)
    return fn, params, ""


@dataclass
class _VSLState:
    """Macro-tier VSL actuation state (module docstring, "Variable speed limits").

    Attributes:
        fn: The segment controller (docs/CONTRACTS.md §1).
        params: Merged controller parameters.
        bounds: Half-open cell ranges of the gantry segments.
        every: Dispatch cadence in solver steps.
        target_m: Gantry segment target length used for ``bounds`` [m].
        effective_limit: :func:`controllers.vsl.effective_limit` (lazily
            imported alongside the registry).
        lim_cell: Effective limit per cell [m/s]; ``v_f`` when uncapped.
        qcap_cell: Capacity [veh/s] of the reduced diagram per cell.
        capped: Mask of cells whose limit is below ``v_f``.
        memory: Controller memory threaded between dispatches.
        history: One record per dispatch (posted and effective limits).
    """

    fn: SegmentControllerFn
    params: dict[str, float]
    bounds: list[tuple[int, int]]
    every: int
    target_m: float
    effective_limit: Callable[[float, float, float], float]
    lim_cell: np.ndarray
    qcap_cell: np.ndarray
    capped: np.ndarray
    memory: Memory = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(
        cls,
        name: str,
        overrides: dict[str, float],
        *,
        fd: TriangularFD,
        n_cells: int,
        dx: float,
        dt: float,
    ) -> _VSLState:
        """Resolve the segment controller and lay out the gantry segments.

        Unlike :func:`_load_controller` there is no fixed-speed fallback: a
        run configured with a VSL that is not applied would be mislabeled,
        so an unavailable ``controllers`` package is an error, not a note
        (CLAUDE.md §0.1).

        Args:
            name: Registry name of the segment controller.
            overrides: Scenario-supplied parameter overrides.
            fd: The (uncapped) fundamental diagram.
            n_cells: Number of cells.
            dx: Cell length [m].
            dt: Solver time step [s].

        Returns:
            A fresh, uncapped state.

        Raises:
            ValueError: The ``controllers`` package is not importable.
            KeyError: Unknown controller name (docs/CONTRACTS.md §1).
        """
        try:
            from controllers import registry  # lazy: package may be built concurrently
            from controllers.vsl import VSL_SEGMENT_TARGET_M, effective_limit, gantry_segments
        except ImportError as exc:
            raise ValueError(
                f"VSL {name!r} configured but the controllers package is not importable"
            ) from exc
        fn = registry.get_segment_controller(name)
        params = dict(registry.default_params(name))
        params.update(overrides)
        return cls(
            fn=fn,
            params=params,
            bounds=gantry_segments([dx] * n_cells, VSL_SEGMENT_TARGET_M),
            every=max(1, round(VSL_INTERVAL_S / dt)),
            target_m=VSL_SEGMENT_TARGET_M,
            effective_limit=effective_limit,
            lim_cell=np.full(n_cells, fd.v_f, dtype=np.float64),
            qcap_cell=np.full(n_cells, fd.q_max, dtype=np.float64),
            capped=np.zeros(n_cells, dtype=bool),
        )

    def cap_speeds(self, speeds: np.ndarray) -> np.ndarray:
        """Equilibrium speeds under the reduced diagrams: ``min(v_lim, V_e)``."""
        capped: np.ndarray = np.minimum(speeds, self.lim_cell)
        return capped

    def merge_caps(self, rho: np.ndarray, caps: dict[int, float]) -> None:
        """Add the capped cells' reduced sending/receiving bounds to ``caps``.

        Capped cell ``i``: its outflow interface ``i+1`` carries the reduced
        sending function ``min(v_lim·ρ_i, q_cap_i)`` and its inflow interface
        ``i`` the reduced receiving bound ``q_cap_i``. With the kernel's
        ``min(demand, supply)`` this is exactly the CTM with the reduced
        triangular diagram in cell ``i`` (module docstring); on a ring the
        kernel merges the caps of the wrap-around interfaces 0 and n itself.

        Args:
            rho: Current cell densities [veh/m].
            caps: Interface-cap dict for this step, updated in place.
        """
        if not self.capped.any():
            return
        n = self.lim_cell.shape[0]
        demand_red = np.minimum(self.lim_cell * rho, self.qcap_cell)
        cap_arr = np.full(n + 1, np.inf, dtype=np.float64)
        out_view = cap_arr[1:]
        out_view[self.capped] = demand_red[self.capped]
        in_view = cap_arr[:-1]
        in_view[self.capped] = np.minimum(in_view[self.capped], self.qcap_cell[self.capped])
        finite = np.flatnonzero(np.isfinite(cap_arr))
        for iface_idx, cap_val in zip(finite.tolist(), cap_arr[finite].tolist(), strict=True):
            caps[iface_idx] = min(caps.get(iface_idx, math.inf), cap_val)

    def dispatch(self, t: float, rho: np.ndarray, fd: TriangularFD, compliance: float) -> None:
        """Call the controller on the current state and update the cell caps.

        Args:
            t: Simulation time [s].
            rho: Current cell densities [veh/m].
            fd: The (uncapped) fundamental diagram; its ``v_f`` is the base
                limit the posted limits are scaled against.
            compliance: Fleet compliance ``cfg.av.compliance``.

        Raises:
            ValueError: An effective limit above ``v_f`` (cannot happen with
                :func:`controllers.vsl.effective_limit`; the CFL step relies
                on it).
        """
        v_now = self.cap_speeds(equilibrium_speed(fd, rho))
        obs = SegmentObs(
            t=t,
            dt=VSL_INTERVAL_S,
            seg_speed=tuple(float(v_now[a:b].mean()) for a, b in self.bounds),
            seg_density=tuple(float(rho[a:b].mean()) for a, b in self.bounds),
        )
        limits, self.memory = self.fn(obs, self.params, self.memory)
        effective = [self.effective_limit(float(lim), fd.v_f, compliance) for lim in limits]
        for (a, b), v_lim in zip(self.bounds, effective, strict=True):
            if v_lim > fd.v_f:
                raise ValueError(f"effective VSL limit {v_lim} exceeds v_f={fd.v_f}")
            self.lim_cell[a:b] = v_lim
            self.qcap_cell[a:b] = capacity_at_speed(fd, v_lim)
        self.capped = self.lim_cell < fd.v_f
        self.history.append(
            {"t": t, "posted_ms": [float(lim) for lim in limits], "effective_ms": effective}
        )

    def meta(self, name: str, compliance: float, dx: float, fd: TriangularFD) -> dict[str, Any]:
        """The ``vsl_dispatch`` block of meta.json."""
        return {
            "controller": name,
            "compliance": compliance,
            "interval_s": VSL_INTERVAL_S,
            "segment_target_m": self.target_m,
            "segments": [[a, b] for a, b in self.bounds],  # half-open cell ranges
            "segment_lengths_m": [(b - a) * dx for a, b in self.bounds],
            "base_limit_ms": fd.v_f,
            "n_dispatches": len(self.history),
            # One entry per dispatch: ``posted_ms`` (raw controller output)
            # and ``effective_ms`` (after compliance scaling) per segment.
            "history": self.history,
        }


def _controller_obs(
    solver: CTMSolver,
    av: MovingBottleneck,
    v_ref: float,
    speeds: np.ndarray,
) -> ControllerObs:
    """Build a screening-tier ``ControllerObs`` for one AV.

    Macro-tier approximations (documented, screening use only): the leader is
    the equilibrium traffic stream one cell downstream; the bumper-to-bumper
    gap is the local mean spacing minus the jam spacing,
    ``1/ρ − 1/ρ_jam`` (∞ when the cell is essentially empty); ``v_ref`` is the
    instantaneous spatial mean of ``V_e`` over the corridor (stand-in for the
    rolling platoon mean the micro runner supplies).

    Args:
        solver: The CTM solver.
        av: The AV whose observation is built.
        v_ref: Reference speed U [m/s].
        speeds: Precomputed ``V_e`` per cell [m/s] for this step.

    Returns:
        A populated :class:`flowstate_core.controller_types.ControllerObs`.
    """
    cell = av.cell_index(solver)
    n = solver.n_cells
    rho_cell = float(solver.density[cell])
    if rho_cell < _RHO_EPS:
        gap = math.inf
    else:
        gap = max(1.0 / rho_cell - 1.0 / solver.fd.rho_jam, 0.0)
    if solver.boundary == "ring":
        lead_cell = (cell + 1) % n
        down = tuple(float(speeds[(cell + 1 + k) % n]) for k in range(_DOWNSTREAM_BINS))
    else:
        lead_cell = min(cell + 1, n - 1)
        down = tuple(float(speeds[min(cell + 1 + k, n - 1)]) for k in range(_DOWNSTREAM_BINS))
    return ControllerObs(
        t=solver.t_s,
        dt=solver.dt_s,
        v=av.v_actual_ms if av.v_actual_ms > 0.0 else float(speeds[cell]),
        gap=gap,
        v_leader=float(speeds[lead_cell]),
        v_ref=v_ref,
        downstream=down,
        downstream_dx=solver.dx_m,
    )


def run_macro(
    cfg: ScenarioConfig,
    seed: int,
    out_dir: str | Path,
    *,
    fd: TriangularFD | None = None,
    dx_m: float = 100.0,
    v_star_ms: float | None = None,
    bottleneck_variant: BottleneckVariant = "flux_cap",
    use_numba: bool | None = None,
    prescribed_avs: Sequence[VStarTrajectory] | None = None,
) -> Path:
    """Run one macro-tier (screening) replicate and write its artifacts.

    Args:
        cfg: Scenario configuration. ``network`` must be a ring or corridor
            (OSM import is a micro-tier feature). ``cfg.tier`` is not trusted
            blindly — the output is always labeled ``tier="screening"``.
        seed: Explicit RNG seed for this replicate. Randomness is consumed
            only for per-AV compliance draws; the PDE itself is deterministic.
        out_dir: Root of the run tree; artifacts land in
            ``out_dir/<config_hash>/<seed>/``.
        fd: Fundamental diagram to use. ``None`` selects the documented
            ``v1_legacy`` preset (uncalibrated; CLAUDE.md §5.1). Calibrated
            runs pass the FD from an ``FDCalibration`` artifact explicitly.
        dx_m: Target cell length [m]; the actual grid uses
            ``n_cells = max(10, round(length/dx_m))``.
        v_star_ms: Fixed bottleneck command speed [m/s] used when no
            controller is configured or the ``controllers`` package is not
            yet importable (it may be building concurrently — noted in
            meta.json when the fallback engages).
        bottleneck_variant: ``"flux_cap"`` (primary) or ``"capacity"``
            (:mod:`macrosim.bottleneck`).
        use_numba: Kernel selection forwarded to :class:`CTMSolver`.
        prescribed_avs: Optional prescribed moving-bottleneck trajectories
            (:class:`macrosim.bottleneck.VStarTrajectory` — the
            v*-trajectory entry point, CLAUDE.md §5.5): each is played back
            as a moving flux constraint at its recorded ``x(t)`` with its
            recorded ``v*(t)`` and the selected ``bottleneck_variant``.
            When given, the config's own ``av`` block is NOT actuated (a
            note is recorded); ``meta.json`` gains binding diagnostics
            (fraction of active AV-steps with ``v* < V_e(ρ_cell)``).

    Returns:
        Path of the run directory containing ``edges.parquet`` and
        ``meta.json``.

    Raises:
        NotImplementedError: For OSM networks.
        ValueError: If AV actuation is requested but neither a resolvable
            controller nor ``v_star_ms`` is available, if a VSL is configured
            but the ``controllers`` package is not importable, or if the
            ring's vehicle count exceeds jam storage.
    """
    t_wall0 = time.perf_counter()
    notes: list[str] = []
    fd_preset = "custom"
    if fd is None:
        fd = v1_legacy_fd()
        fd_preset = "v1_legacy"

    net = cfg.network
    if isinstance(net, RingNetwork):
        boundary = "ring"
        length_m = net.circumference_m
        lanes = 1
    elif isinstance(net, CorridorNetwork):
        boundary = "open"
        length_m = net.length_m
        lanes = net.lanes
    else:
        raise NotImplementedError("macro tier supports ring and corridor networks only")

    if lanes > 1:
        # Effective single pipe (CLAUDE.md §10): jam storage scales with lane
        # count; v_f and w are per-lane speeds and stay unchanged.
        fd = TriangularFD(v_f=fd.v_f, w=fd.w, rho_jam=fd.rho_jam * lanes)
        notes.append(f"effective single-pipe: rho_jam scaled by {lanes} lanes")

    n_cells = max(10, round(length_m / dx_m))
    dx = length_m / n_cells
    dt = min(cfg.sim.step_length_s, _CFL_SAFETY * cfl_max_dt(fd, dx))
    solver = CTMSolver(
        fd, n_cells=n_cells, length_m=length_m, dt_s=dt, boundary=boundary, use_numba=use_numba
    )

    if isinstance(net, RingNetwork):
        rho0 = net.n_vehicles / net.circumference_m
        if rho0 > fd.rho_jam:
            raise ValueError(
                f"{net.n_vehicles} vehicles exceed ring jam storage "
                f"({fd.rho_jam * net.circumference_m:.1f} veh)"
            )
        solver.set_uniform_density(rho0)
        inflow_steps: list[tuple[float, float]] = []
        n_veh_nominal = float(net.n_vehicles)
    else:
        inflow_steps = list(net.inflow)
        n_veh_nominal = fd.rho_c * length_m  # storage at capacity, for AV counting

    # --- AV moving bottlenecks -------------------------------------------
    rng = make_rng(seed)
    controller_fn: VehicleControllerFn | None = None
    controller_params: dict[str, float] = {}
    avs: list[MovingBottleneck] = []
    memories: list[Memory] = []
    complied: list[bool] = []
    if prescribed_avs is not None:
        if cfg.av.penetration > 0.0:
            notes.append(
                "av block not actuated: prescribed v*-trajectories supplied "
                f"({len(prescribed_avs)} AVs played back from micro-tier data)"
            )
    elif cfg.av.penetration > 0.0 and (cfg.av.controller is not None or v_star_ms is not None):
        if cfg.av.controller is not None:
            controller_fn, controller_params, note = _load_controller(
                cfg.av.controller, cfg.av.controller_params
            )
            if note:
                notes.append(note)
                if v_star_ms is None:
                    raise ValueError(
                        f"controller {cfg.av.controller!r} unavailable ({note}) and no "
                        "fixed v_star_ms fallback was provided"
                    )
        n_avs = max(1, round(cfg.av.penetration * n_veh_nominal))
        draws = rng.random(n_avs)
        for j in range(n_avs):
            is_compliant = bool(draws[j] < cfg.av.compliance)
            complied.append(is_compliant)
            v0 = v_star_ms if v_star_ms is not None else fd.v_f
            avs.append(
                MovingBottleneck(
                    x_m=(j + 0.5) * length_m / n_avs,
                    v_star_ms=float(v0),
                    variant=bottleneck_variant,
                    active=is_compliant,
                )
            )
            memories.append({})

    # --- Variable speed limits (CLAUDE.md §4.4: cap V_f per cell) ---------
    # The CFL step above was fixed from the UNCAPPED v_f, the largest
    # characteristic speed any cell can have; VSL only lowers it per cell.
    vsl: _VSLState | None = None
    if cfg.av.vsl is not None:
        vsl = _VSLState.load(cfg.av.vsl, cfg.av.vsl_params, fd=fd, n_cells=n_cells, dx=dx, dt=dt)

    # --- Time loop --------------------------------------------------------
    n_steps = math.ceil(cfg.sim.duration_s / dt)
    out_every = max(1, round(1.0 / (cfg.sim.output_hz * dt)))
    pert = cfg.perturbation
    x_centers = (np.arange(n_cells) + 0.5) * dx

    t_rows: list[np.ndarray] = []
    rho_rows: list[np.ndarray] = []
    lim_rows: list[np.ndarray] = []

    def _record() -> None:
        t_rows.append(np.full(n_cells, solver.t_s))
        rho_rows.append(np.asarray(solver.density, dtype=np.float64).copy())
        if vsl is not None:
            lim_rows.append(vsl.lim_cell.copy())

    prescribed_active_steps = 0
    prescribed_binding_steps = 0

    _record()
    for k in range(n_steps):
        caps: dict[int, float] = {}
        speeds = equilibrium_speed(fd, np.asarray(solver.density))
        if vsl is not None:
            speeds = vsl.cap_speeds(speeds)

        # Prescribed v*-trajectory moving bottlenecks (played back verbatim).
        if prescribed_avs is not None:
            for traj in prescribed_avs:
                state = traj.state_at(solver.t_s)
                if state is None:
                    continue
                x_av, v_star = state
                if not 0.0 <= x_av < solver.length_m:
                    continue
                mb = MovingBottleneck(x_m=x_av, v_star_ms=v_star, variant=bottleneck_variant)
                cell = mb.cell_index(solver)
                prescribed_active_steps += 1
                if v_star < equilibrium_speed_scalar(fd, float(solver.density[cell])):
                    prescribed_binding_steps += 1
                cap_entry = mb.iface_cap(solver)
                if cap_entry is not None:
                    iface, cap = cap_entry
                    caps[iface] = min(caps.get(iface, math.inf), cap)

        if pert is not None and pert.t_s <= solver.t_s < pert.t_s + pert.duration_s:
            iface = min(max(round(pert.position_m / dx), 0), n_cells)
            cell = (iface - 1) % n_cells if boundary == "ring" else max(iface - 1, 0)
            v_prevail = equilibrium_speed_scalar(fd, float(solver.density[cell]))
            if vsl is not None:
                v_prevail = min(v_prevail, float(vsl.lim_cell[cell]))
            v_red = max(v_prevail - pert.v_drop_ms, 0.0)
            cap = capacity_at_speed(fd, v_red)
            caps[iface] = min(caps.get(iface, math.inf), cap)

        if avs:
            v_ref = float(np.mean(speeds))
            for j, av in enumerate(avs):
                if not av.active:
                    continue
                if controller_fn is not None:
                    obs = _controller_obs(solver, av, v_ref, speeds)
                    v_cmd, memories[j] = controller_fn(obs, controller_params, memories[j])
                    av.v_star_ms = float(min(max(v_cmd, 0.0), fd.v_f))
                cap_entry = av.iface_cap(solver)
                if cap_entry is not None:
                    iface, cap = cap_entry
                    caps[iface] = min(caps.get(iface, math.inf), cap)

        if vsl is not None:
            vsl.merge_caps(np.asarray(solver.density), caps)

        q_in = _inflow_at(inflow_steps, solver.t_s) if boundary == "open" else 0.0
        solver.step(q_in_veh_s=q_in, iface_caps=caps or None)
        for av in avs:
            av.advance(solver)
        if (k + 1) % out_every == 0:
            _record()

        # VSL dispatch (mirrors the micro runner: after the step, every
        # VSL_INTERVAL_S, on the state just reached; limits apply from the
        # next step on).
        if vsl is not None and (k + 1) % vsl.every == 0:
            vsl.dispatch(solver.t_s, np.asarray(solver.density), fd, cfg.av.compliance)

    # --- Artifacts --------------------------------------------------------
    rho_mat = np.vstack(rho_rows)
    speed_mat = equilibrium_speed(fd, rho_mat)
    if vsl is not None:
        # Reported speed of a capped cell is the reduced diagram's V_e.
        speed_mat = np.minimum(speed_mat, np.vstack(lim_rows))
    flow_mat = rho_mat * speed_mat
    n_samples = rho_mat.shape[0]
    edges = pd.DataFrame(
        {
            "t_bin": np.concatenate(t_rows),
            "x_bin": np.tile(x_centers, n_samples),
            "mean_speed": speed_mat.ravel(),
            "density": rho_mat.ravel(),
            "flow": flow_mat.ravel(),
        }
    )

    chash = config_hash(cfg)
    run_dir = Path(out_dir) / chash / str(seed)
    run_dir.mkdir(parents=True, exist_ok=True)
    edges.to_parquet(run_dir / "edges.parquet", index=False)

    meta: dict[str, Any] = {
        "config": cfg.model_dump(mode="json"),
        "config_hash": chash,
        "seed": seed,
        "versions": _versions(),
        "tier": "screening",
        "seeded": cfg.seeded,
        "wall_time_s": time.perf_counter() - t_wall0,
        "fuel_total_ml": None,  # no emission model in the macro tier (micro-tier output)
        "clamped": solver.clamped,
        "grid": {"n_cells": n_cells, "dx_m": dx, "dt_s": dt, "boundary": boundary},
        "fd": {"preset": fd_preset, "v_f": fd.v_f, "w": fd.w, "rho_jam": fd.rho_jam},
        "ledger": {
            "vehicles_in": solver.vehicles_in,
            "vehicles_out": solver.vehicles_out,
            "queue_veh": solver.queue_veh,
            "stored_veh": solver.total_vehicles(),
        },
        "av": {
            "n_avs": len(avs),
            "n_complied": sum(complied),
            "controller": cfg.av.controller,
            "controller_applied": controller_fn is not None,
            "v_star_fallback_ms": v_star_ms,
            "variant": bottleneck_variant,
            "prescribed": (
                {
                    "n_trajectories": len(prescribed_avs),
                    "active_av_steps": prescribed_active_steps,
                    "binding_av_steps": prescribed_binding_steps,
                    "binding_fraction": (
                        prescribed_binding_steps / prescribed_active_steps
                        if prescribed_active_steps
                        else 0.0
                    ),
                }
                if prescribed_avs is not None
                else None
            ),
        },
        "vsl": cfg.av.vsl,
        "vsl_dispatch": (
            vsl.meta(cfg.av.vsl, cfg.av.compliance, dx, fd)
            if vsl is not None and cfg.av.vsl is not None
            else None
        ),
        "notes": notes,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return run_dir


def _versions() -> dict[str, str]:
    """Package versions recorded in every meta.json (reproducibility, §0.5)."""
    import numba

    try:
        from importlib.metadata import version

        macrosim_version = version("macrosim")
    except Exception:  # pragma: no cover - metadata missing in odd installs
        macrosim_version = "unknown"
    import flowstate_core

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "numba": numba.__version__,
        "flowstate_core": getattr(flowstate_core, "__version__", "unknown"),
        "macrosim": macrosim_version,
    }
