"""Moving-bottleneck actuation for the CTM screening tier.

A controlled vehicle (AV) travelling at ``v*`` below the surrounding traffic
speed acts as a moving bottleneck: traffic cannot overtake (effective single
pipe), so the flux through the interface just downstream of the AV's cell is
constrained. Two selectable variants:

* ``"flux_cap"`` (primary, the v1 rule that survives the port):
  ``F_{i+1/2} ← min(F_{i+1/2}, ρ_i · v*)`` at the AV-occupied cell ``i``.
  This is the discrete analog of the Delle Monache–Goatin (2014) PDE-ODE
  moving flux constraint ``q(t, x_AV) − ρ(t, x_AV)·ẋ_AV ≤ F_α(ẋ_AV)`` in the
  fully blocking limit ``F_α = 0``: no traffic passes the AV, so the Eulerian
  flux at its position is exactly the convective flux ``ρ·v*`` carried along
  with it (CLAUDE.md §5.5, ADR-1d).

* ``"capacity"`` (alternative): ``F_{i+1/2} ← min(F_{i+1/2}, α(v*)·q_max)``
  where ``α(v*) = capacity_at_speed(fd, v*)/q_max`` — the capacity fraction of
  a triangular diagram whose free-flow branch is capped at ``v*`` (same ``w``,
  ``ρ_jam``). This models partial obstruction: flow past the AV may exceed
  ``ρ_i·v*`` but never the reduced-speed capacity. The two variants are to be
  compared against micro-tier ground truth in Phase 3 (CLAUDE.md §5.5); the
  α(v*) form here is a documented modeling choice, not a calibrated result.

The AV trajectory advances at ``min(v*, V_e(ρ))`` evaluated at its current
cell — it can never outrun the surrounding traffic stream.

References:
    Delle Monache & Goatin (2014), "Scalar conservation laws with moving
    constraints arising in traffic flow modeling", J. Diff. Eq. 257(11).
    Liard & Piccoli (2019), SIAM J. Appl. Math. 79(2) — well-posedness of the
    moving-constraint problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from macrosim.ctm import CTMSolver
from macrosim.fundamental import capacity_at_speed, equilibrium_speed_scalar

__all__ = ["BottleneckVariant", "MovingBottleneck"]

BottleneckVariant = Literal["flux_cap", "capacity"]


@dataclass
class MovingBottleneck:
    """A moving flux constraint (controlled vehicle) on a CTM grid.

    Attributes:
        x_m: Current position along the domain [m].
        v_star_ms: Commanded speed ``v*`` [m/s]; update each step to drive the
            bottleneck from a controller.
        variant: ``"flux_cap"`` (primary, ``F ≤ ρ_i·v*``) or ``"capacity"``
            (``F ≤ α(v*)·q_max``); see module docstring.
        active: When False (e.g. a non-compliant AV, or an AV that left an
            open corridor) the bottleneck constrains nothing but its position
            still advances while on the domain.
        v_actual_ms: Speed actually travelled during the last
            :meth:`advance` call [m/s].
    """

    x_m: float
    v_star_ms: float
    variant: BottleneckVariant = "flux_cap"
    active: bool = True
    v_actual_ms: float = field(default=0.0, init=False)

    def cell_index(self, solver: CTMSolver) -> int:
        """Index of the cell currently occupied by the AV.

        Args:
            solver: The CTM solver whose grid the AV lives on.

        Returns:
            Cell index in ``[0, n_cells − 1]``.
        """
        idx = int(self.x_m / solver.dx_m)
        return min(max(idx, 0), solver.n_cells - 1)

    def iface_cap(self, solver: CTMSolver) -> tuple[int, float] | None:
        """Flux cap this bottleneck imposes for the coming step.

        The constrained interface is the one just downstream of the occupied
        cell (``i+1/2`` for cell ``i``), matching the v1 discretization.

        Args:
            solver: The CTM solver (provides grid geometry, density and FD).

        Returns:
            ``(interface_index, cap_veh_s)``, or None when inactive.
        """
        if not self.active:
            return None
        cell = self.cell_index(solver)
        iface = cell + 1
        if self.variant == "flux_cap":
            cap = float(solver.density[cell]) * max(self.v_star_ms, 0.0)
        else:
            cap = capacity_at_speed(solver.fd, self.v_star_ms)
        return iface, cap

    def advance(self, solver: CTMSolver) -> None:
        """Move the AV forward by one solver time step.

        Travels at ``min(v*, V_e(ρ_cell))`` — the AV is carried with the flow
        when local traffic is slower than its command. On a ring the position
        wraps; on an open corridor an AV that reaches the downstream end is
        deactivated (screening-tier simplification — vehicle recycling is a
        micro-tier concern).

        Args:
            solver: The CTM solver whose state was just advanced.
        """
        cell = self.cell_index(solver)
        v_local = equilibrium_speed_scalar(solver.fd, float(solver.density[cell]))
        v = min(max(self.v_star_ms, 0.0), v_local)
        self.v_actual_ms = v
        self.x_m += v * solver.dt_s
        if solver.boundary == "ring":
            self.x_m %= solver.length_m
        elif self.x_m >= solver.length_m:
            self.x_m = solver.length_m
            self.active = False
