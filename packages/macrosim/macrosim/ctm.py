"""Godunov / Cell Transmission Model solver for the LWR conservation law.

Ported from the v1 engine (``lwr_model.py``) with the CLAUDE.md §5 corrections:
SI units throughout, a hard CFL guard at construction, exact conservation
accounting (ring) / an inflow–outflow ledger (open corridor), and
clamped-density *flagging* instead of silent clipping. The inner kernel is
Numba-jitted (``njit(cache=True)``) with a pure-Python fallback selectable per
solver or via the ``MACROSIM_DISABLE_NUMBA`` environment variable, so tests can
exercise both paths.

Scheme (Godunov 1959; Daganzo 1994, 1995):
    ``n_i^{t+1} = n_i^t + (Δt/Δx)(F_{i−1/2} − F_{i+1/2})`` with the
    supply–demand numerical flux ``F = min(Λ(ρ_L), Σ(ρ_R))``,
    ``Λ(ρ) = min(v_f·ρ, q_max)``, ``Σ(ρ) = min(q_max, −w·(ρ_jam − ρ))`` —
    exactly the sending/receiving functions of
    :class:`flowstate_core.artifacts.TriangularFD`. For concave flux this is
    the exact Godunov flux and automatically satisfies the entropy condition.

Role restriction: this tier is *string-stable by construction* — LWR entropy
solutions dissipate perturbations and can never grow stop-and-go waves
(CLAUDE.md ADR-1). All outputs from this package are labeled
``tier="screening"``.

References:
    Lighthill & Whitham (1955), Proc. R. Soc. A 229; Richards (1956),
    Oper. Res. 4 — the LWR model.
    Daganzo (1994), Transp. Res. B 28(4):269–287; Daganzo (1995),
    Transp. Res. B 29(2):79–93 — CTM and supply–demand flux.
    Godunov (1959), Mat. Sb. 47 — the finite-volume scheme.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Literal

import numpy as np
from numba import njit
from numpy.typing import NDArray

from flowstate_core.artifacts import TriangularFD
from macrosim.fundamental import fd_tuple

__all__ = ["BOUND_TOL", "Boundary", "CTMSolver", "cfl_max_dt"]

Boundary = Literal["ring", "open"]

BOUND_TOL: float = 1e-9
"""Density excursions beyond [0, ρ_jam] larger than this set ``clamped``."""

_ENV_DISABLE_NUMBA = "MACROSIM_DISABLE_NUMBA"

_FloatArray = NDArray[np.float64]

_StepKernel = Callable[
    [
        _FloatArray,
        _FloatArray,
        _FloatArray,
        float,
        float,
        float,
        float,
        float,
        float,
        bool,
        float,
        _FloatArray,
    ],
    tuple[float, float, float],
]


def cfl_max_dt(fd: TriangularFD, dx_m: float) -> float:
    """Largest stable time step ``Δx / max(v_f, |w|)`` [s] (CFL condition).

    Args:
        fd: Triangular fundamental diagram (SI units).
        dx_m: Cell length [m].

    Returns:
        Maximum admissible ``Δt`` [s] for the Godunov scheme.
    """
    return dx_m / max(fd.v_f, abs(fd.w))


def _ctm_step(
    rho: _FloatArray,
    rho_new: _FloatArray,
    flux: _FloatArray,
    dt_over_dx: float,
    v_f: float,
    w: float,
    rho_jam: float,
    rho_c: float,  # kept for kernel-signature completeness; q_max form used below
    q_max: float,
    ring: bool,
    q_in_demand: float,
    iface_cap: _FloatArray,
) -> tuple[float, float, float]:
    """One Godunov/CTM step (pure-Python reference kernel; Numba-wrapped below).

    Interface indexing: ``flux`` has length ``n+1``; ``flux[i]`` is the flow
    [veh/s] from cell ``i−1`` into cell ``i``. ``flux[0]`` is the upstream
    boundary flux and ``flux[n]`` the downstream one; on a ring both equal the
    wrap-around interface flux (so the update is exactly conservative).

    Args:
        rho: Current densities [veh/m], length n (read-only).
        rho_new: Output buffer for updated densities, length n.
        flux: Output buffer for interface fluxes [veh/s], length n+1.
        dt_over_dx: Δt/Δx [s/m].
        v_f: Free-flow speed [m/s].
        w: Congested wave speed [m/s], negative.
        rho_jam: Jam density [veh/m].
        rho_c: Critical density [veh/m] (unused; slope tests use q_max form).
        q_max: Capacity [veh/s].
        ring: True for periodic (closed-ring) boundaries.
        q_in_demand: Upstream demand [veh/s] (open boundary only).
        iface_cap: Per-interface flux caps [veh/s], length n+1 (np.inf = none).

    Returns:
        ``(inflow, outflow, excursion)``: boundary fluxes actually realized
        [veh/s] and the largest density excursion beyond [0, ρ_jam] [veh/m]
        before clamping.
    """
    n = rho.shape[0]

    # Interior interfaces: F = min(demand(rho_L), supply(rho_R)), then caps.
    for i in range(1, n):
        d = v_f * rho[i - 1]
        if d > q_max:
            d = q_max
        s = -w * (rho_jam - rho[i])
        if s > q_max:
            s = q_max
        f = d if d < s else s
        if f > iface_cap[i]:
            f = iface_cap[i]
        flux[i] = f

    if ring:
        d = v_f * rho[n - 1]
        if d > q_max:
            d = q_max
        s = -w * (rho_jam - rho[0])
        if s > q_max:
            s = q_max
        f = d if d < s else s
        c = iface_cap[0] if iface_cap[0] < iface_cap[n] else iface_cap[n]
        if f > c:
            f = c
        flux[0] = f
        flux[n] = f
    else:
        # Upstream: demand comes from the boundary (inflow + queue), supply
        # from cell 0. Downstream: free outflow (infinite supply).
        s = -w * (rho_jam - rho[0])
        if s > q_max:
            s = q_max
        f0 = q_in_demand if q_in_demand < s else s
        if f0 > iface_cap[0]:
            f0 = iface_cap[0]
        if f0 < 0.0:
            f0 = 0.0
        flux[0] = f0
        d = v_f * rho[n - 1]
        if d > q_max:
            d = q_max
        fn = d
        if fn > iface_cap[n]:
            fn = iface_cap[n]
        flux[n] = fn

    excursion = 0.0
    for i in range(n):
        r = rho[i] + dt_over_dx * (flux[i] - flux[i + 1])
        if r < 0.0:
            if -r > excursion:
                excursion = -r
            r = 0.0
        elif r > rho_jam:
            if r - rho_jam > excursion:
                excursion = r - rho_jam
            r = rho_jam
        rho_new[i] = r
    return flux[0], flux[n], excursion


_ctm_step_numba: _StepKernel = njit(cache=True)(_ctm_step)


class CTMSolver:
    """Godunov/CTM solver on a uniform 1-D grid (effective single pipe).

    Supports closed-ring (periodic) and open-corridor boundaries. The open
    corridor takes an upstream *demand* inflow — vehicles that cannot enter
    (supply-limited) wait in a virtual boundary queue rather than vanishing —
    and a free (non-reflecting) downstream outflow. Cumulative vehicles
    admitted/discharged are tracked in a ledger so tests can assert
    ``inflow − outflow − Δstorage = 0``.

    A density that leaves ``[0, ρ_jam]`` by more than :data:`BOUND_TOL` sets
    ``clamped=True``; the tests treat any such clamp as a failure (CLAUDE.md
    §5.3). Sub-tolerance floating-point noise is clamped silently.

    Attributes:
        fd: The fundamental diagram in use.
        n_cells: Number of cells.
        length_m: Domain length [m].
        dx_m: Cell length [m].
        dt_s: Time step [s].
        boundary: ``"ring"`` or ``"open"``.
        t_s: Current simulation time [s].
        clamped: True once any density excursion exceeded :data:`BOUND_TOL`.
        vehicles_in: Cumulative vehicles admitted at the upstream boundary.
        vehicles_out: Cumulative vehicles discharged downstream.
        queue_veh: Vehicles currently waiting in the upstream boundary queue.
    """

    def __init__(
        self,
        fd: TriangularFD,
        *,
        n_cells: int,
        length_m: float,
        dt_s: float,
        boundary: Boundary = "ring",
        use_numba: bool | None = None,
    ) -> None:
        """Build a solver; raises on an unstable discretization.

        Args:
            fd: Triangular fundamental diagram (SI units).
            n_cells: Number of cells (≥ 3).
            length_m: Domain length [m].
            dt_s: Time step [s]. Must satisfy the CFL condition
                ``dt ≤ dx / max(v_f, |w|)`` — violation is a hard
                ``ValueError``, not a warning (CLAUDE.md §5.2).
            boundary: ``"ring"`` (periodic) or ``"open"`` (demand inflow,
                free outflow).
            use_numba: Force the Numba kernel (True) or the pure-Python
                fallback (False). ``None`` uses Numba unless the
                ``MACROSIM_DISABLE_NUMBA`` environment variable is set to a
                non-empty value.

        Raises:
            ValueError: On non-positive sizes/steps, unknown boundary, or a
                CFL-violating ``dt_s``.
        """
        if n_cells < 3:
            raise ValueError(f"n_cells must be >= 3, got {n_cells}")
        if length_m <= 0:
            raise ValueError(f"length_m must be > 0, got {length_m}")
        if dt_s <= 0:
            raise ValueError(f"dt_s must be > 0, got {dt_s}")
        if boundary not in ("ring", "open"):
            raise ValueError(f"boundary must be 'ring' or 'open', got {boundary!r}")
        dx = length_m / n_cells
        dt_max = cfl_max_dt(fd, dx)
        if dt_s > dt_max * (1.0 + 1e-12):
            raise ValueError(
                f"CFL condition violated: dt_s={dt_s:.6g} s > dx/max(v_f,|w|)="
                f"{dt_max:.6g} s (dx={dx:.6g} m, v_f={fd.v_f:.6g} m/s, w={fd.w:.6g} m/s)"
            )

        self.fd = fd
        self.n_cells = n_cells
        self.length_m = length_m
        self.dx_m = dx
        self.dt_s = dt_s
        self.boundary: Boundary = boundary
        self.t_s = 0.0
        self.clamped = False
        self.vehicles_in = 0.0
        self.vehicles_out = 0.0
        self.queue_veh = 0.0

        self._fd_tuple = fd_tuple(fd)
        self._rho: _FloatArray = np.zeros(n_cells, dtype=np.float64)
        self._rho_new: _FloatArray = np.zeros(n_cells, dtype=np.float64)
        self._flux: _FloatArray = np.zeros(n_cells + 1, dtype=np.float64)
        self._iface_cap: _FloatArray = np.full(n_cells + 1, np.inf, dtype=np.float64)
        self._caps_active = False

        if use_numba is None:
            use_numba = not os.environ.get(_ENV_DISABLE_NUMBA)
        self.use_numba = use_numba
        self._kernel: _StepKernel = _ctm_step_numba if use_numba else _ctm_step

    @property
    def density(self) -> _FloatArray:
        """Current densities [veh/m] as a read-only view (length ``n_cells``)."""
        view = self._rho.view()
        view.flags.writeable = False
        return view

    @property
    def last_flux(self) -> _FloatArray:
        """Interface fluxes [veh/s] of the last step, read-only (length n+1).

        ``last_flux[i]`` is the realized flow from cell ``i−1`` into cell
        ``i``; index 0 is the upstream boundary, index ``n_cells`` the
        downstream one (equal on a ring).
        """
        view = self._flux.view()
        view.flags.writeable = False
        return view

    def set_uniform_density(self, rho: float) -> None:
        """Fill the domain with a uniform density.

        Args:
            rho: Density [veh/m], must lie in ``[0, ρ_jam]``.

        Raises:
            ValueError: If ``rho`` is outside ``[0, ρ_jam]``.
        """
        if not 0.0 <= rho <= self.fd.rho_jam:
            raise ValueError(f"rho={rho} outside [0, rho_jam={self.fd.rho_jam}]")
        self._rho[:] = rho

    def set_density(self, profile: NDArray[np.float64]) -> None:
        """Set the full density profile.

        Args:
            profile: Densities [veh/m], length ``n_cells``, all within
                ``[0, ρ_jam]``.

        Raises:
            ValueError: On wrong length or out-of-range values.
        """
        arr = np.asarray(profile, dtype=np.float64)
        if arr.shape != (self.n_cells,):
            raise ValueError(f"profile shape {arr.shape} != ({self.n_cells},)")
        if float(arr.min()) < 0.0 or float(arr.max()) > self.fd.rho_jam:
            raise ValueError("initial densities must lie within [0, rho_jam]")
        self._rho[:] = arr

    def total_vehicles(self) -> float:
        """Vehicles currently stored on the mainline: ``Σ ρ_i · Δx`` [veh]."""
        return float(np.sum(self._rho) * self.dx_m)

    def step(
        self,
        q_in_veh_s: float = 0.0,
        iface_caps: Mapping[int, float] | None = None,
    ) -> None:
        """Advance one time step.

        Args:
            q_in_veh_s: Upstream demand [veh/s] for this step (open boundary
                only; must be 0 on a ring). Demand that exceeds the receiving
                supply of cell 0 accumulates in the boundary queue and is
                offered again on later steps.
            iface_caps: Optional flux caps [veh/s] keyed by interface index
                (0 … ``n_cells``); the realized flux at a capped interface is
                ``min(F, cap)``. Used by moving-bottleneck actuation and the
                seeded perturbation. Caps apply to this step only.

        Raises:
            ValueError: On a nonzero ``q_in_veh_s`` for a ring, a negative
                inflow, an out-of-range interface index, or a negative cap.
        """
        if self.boundary == "ring":
            if q_in_veh_s != 0.0:
                raise ValueError("q_in_veh_s must be 0 on a closed ring")
            q_demand = 0.0
        else:
            if q_in_veh_s < 0.0:
                raise ValueError(f"q_in_veh_s must be >= 0, got {q_in_veh_s}")
            q_demand = q_in_veh_s + self.queue_veh / self.dt_s

        if iface_caps:
            self._iface_cap[:] = np.inf
            for idx, cap in iface_caps.items():
                if not 0 <= idx <= self.n_cells:
                    raise ValueError(f"interface index {idx} outside [0, {self.n_cells}]")
                if cap < 0.0:
                    raise ValueError(f"flux cap must be >= 0, got {cap}")
                if cap < self._iface_cap[idx]:
                    self._iface_cap[idx] = cap
            self._caps_active = True
        elif self._caps_active:
            self._iface_cap[:] = np.inf
            self._caps_active = False

        v_f, w, rho_jam, rho_c, q_max = self._fd_tuple
        f_in, f_out, excursion = self._kernel(
            self._rho,
            self._rho_new,
            self._flux,
            self.dt_s / self.dx_m,
            v_f,
            w,
            rho_jam,
            rho_c,
            q_max,
            self.boundary == "ring",
            q_demand,
            self._iface_cap,
        )
        self._rho, self._rho_new = self._rho_new, self._rho
        self.t_s += self.dt_s
        if excursion > BOUND_TOL:
            self.clamped = True
        if self.boundary == "open":
            entered = f_in * self.dt_s
            self.vehicles_in += entered
            self.vehicles_out += f_out * self.dt_s
            self.queue_veh = max(self.queue_veh + q_in_veh_s * self.dt_s - entered, 0.0)
