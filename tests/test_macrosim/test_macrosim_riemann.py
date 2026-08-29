"""Riemann exactness tests for the CTM solver (CLAUDE.md §5.4).

Shock: for the scalar LWR law a discontinuity between ρ_L < ρ_R (concave
flux) is an admissible shock travelling at the Rankine–Hugoniot speed
``s = (q_R − q_L)/(ρ_R − ρ_L)``.

Rarefaction: for ρ_L > ρ_R the entropy solution is a rarefaction. The
triangular flux is *piecewise linear*, so the self-similar fan degenerates
(Osher/convex-hull construction): characteristic speeds take only the two
branch slopes ``w`` and ``v_f``. When both states sit on one branch the
"fan" collapses to a single contact discontinuity moving at that branch's
slope; when the states straddle the kink (congested → free) the solution is
two contacts — one at speed ``w`` down to the capacity state ρ_c, one at
speed ``v_f`` down to ρ_R — separated by a plateau at exactly ρ_c
(capacity discharge). The tests below check the density profile accordingly.

Boundary handling: the right state of a shock/congested-contact problem is
held by capping the downstream outflow at q(ρ_R); otherwise the free-outflow
boundary discharges the congested region at capacity and sends a spurious
rarefaction back upstream (physically correct behavior, but not the Riemann
problem under test).
"""

from __future__ import annotations

import numpy as np
import pytest

from macrosim.ctm import CTMSolver
from macrosim.fundamental import v1_legacy_fd


def _front_position(x: np.ndarray, rho: np.ndarray, level: float, increasing: bool) -> float:
    """Interpolated position where the (monotone) front crosses ``level``."""
    idx = int(np.argmax(rho >= level)) if increasing else int(np.argmax(rho <= level))
    assert idx > 0, "front left the measurable domain"
    r0, r1 = float(rho[idx - 1]), float(rho[idx])
    dx = float(x[1] - x[0])
    return float(x[idx - 1]) + (level - r0) / (r1 - r0) * dx


def _measure_front_speed(
    solver: CTMSolver,
    q_in: float,
    q_out_cap: float | None,
    level: float,
    increasing: bool,
    t_skip: float,
    t_end: float,
    sample_every: int = 10,
) -> float:
    """Run the solver, track the front crossing, return the fitted speed."""
    x = (np.arange(solver.n_cells) + 0.5) * solver.dx_m
    caps = None if q_out_cap is None else {solver.n_cells: q_out_cap}
    ts: list[float] = []
    xs: list[float] = []
    k = 0
    while solver.t_s < t_end:
        solver.step(q_in_veh_s=q_in, iface_caps=caps)
        if solver.t_s > t_skip and k % sample_every == 0:
            ts.append(solver.t_s)
            xs.append(_front_position(x, np.asarray(solver.density), level, increasing))
        k += 1
    assert len(ts) >= 10
    return float(np.polyfit(ts, xs, 1)[0])


@pytest.mark.parametrize("use_numba", [False, True], ids=["python", "numba"])
def test_shock_speed_matches_rankine_hugoniot(use_numba: bool) -> None:
    """Cross-branch shock (free ρ_L → congested ρ_R) at RH speed, ±2%, 150 cells."""
    fd = v1_legacy_fd()
    rho_l, rho_r = 0.02, 0.15  # veh/m: free-flow left, congested right
    assert rho_l < fd.rho_c < rho_r
    q_l = fd.equilibrium_flow(rho_l)
    q_r = fd.equilibrium_flow(rho_r)
    s_rh = (q_r - q_l) / (rho_r - rho_l)

    n, length = 150, 1500.0
    dx = length / n
    dt = 0.9 * dx / max(fd.v_f, abs(fd.w))
    solver = CTMSolver(
        fd, n_cells=n, length_m=length, dt_s=dt, boundary="open", use_numba=use_numba
    )
    x = (np.arange(n) + 0.5) * dx
    solver.set_density(np.where(x < 0.8 * length, rho_l, rho_r))

    speed = _measure_front_speed(
        solver,
        q_in=q_l,
        q_out_cap=q_r,
        level=0.5 * (rho_l + rho_r),
        increasing=True,
        t_skip=30.0,
        t_end=150.0,
    )
    assert speed == pytest.approx(s_rh, rel=0.02)
    assert not solver.clamped


@pytest.mark.parametrize("use_numba", [False, True], ids=["python", "numba"])
def test_rarefaction_free_branch_contact_at_v_f(use_numba: bool) -> None:
    """ρ_L > ρ_R both free-flow: the rarefaction collapses to a contact at v_f."""
    fd = v1_legacy_fd()
    rho_l, rho_r = 0.02, 0.005
    assert rho_r < rho_l < fd.rho_c

    n, length = 300, 3000.0
    dx = length / n
    dt = 0.9 * dx / max(fd.v_f, abs(fd.w))
    solver = CTMSolver(
        fd, n_cells=n, length_m=length, dt_s=dt, boundary="open", use_numba=use_numba
    )
    x = (np.arange(n) + 0.5) * dx
    solver.set_density(np.where(x < 0.1 * length, rho_l, rho_r))

    speed = _measure_front_speed(
        solver,
        q_in=fd.equilibrium_flow(rho_l),
        q_out_cap=None,  # free branch: free outflow keeps rho_r steady
        level=0.5 * (rho_l + rho_r),
        increasing=False,
        t_skip=10.0,
        t_end=85.0,
    )
    assert speed == pytest.approx(fd.v_f, rel=0.02)
    assert not solver.clamped


@pytest.mark.parametrize("use_numba", [False, True], ids=["python", "numba"])
def test_rarefaction_congested_branch_contact_at_w(use_numba: bool) -> None:
    """ρ_L > ρ_R both congested: the rarefaction collapses to a contact at w."""
    fd = v1_legacy_fd()
    rho_l, rho_r = 0.14, 0.10
    assert fd.rho_c < rho_r < rho_l

    n, length = 300, 3000.0
    dx = length / n
    dt = 0.9 * dx / max(fd.v_f, abs(fd.w))
    solver = CTMSolver(
        fd, n_cells=n, length_m=length, dt_s=dt, boundary="open", use_numba=use_numba
    )
    x = (np.arange(n) + 0.5) * dx
    solver.set_density(np.where(x < 0.8 * length, rho_l, rho_r))

    speed = _measure_front_speed(
        solver,
        q_in=fd.equilibrium_flow(rho_l),
        q_out_cap=fd.equilibrium_flow(rho_r),  # hold the congested right state
        level=0.5 * (rho_l + rho_r),
        increasing=False,
        t_skip=30.0,
        t_end=250.0,
    )
    assert speed == pytest.approx(fd.w, rel=0.02)
    assert not solver.clamped


@pytest.mark.parametrize("use_numba", [False, True], ids=["python", "numba"])
def test_rarefaction_cross_branch_two_contacts_and_capacity_plateau(
    use_numba: bool,
) -> None:
    """Congested → free Riemann problem: analytic self-similar profile.

    Analytic solution (convex-hull construction for the triangular flux):
    ``ρ(x, t) = ρ_L`` for ``(x−x0)/t < w``; ``ρ_c`` (capacity discharge) for
    ``w < (x−x0)/t < v_f``; ``ρ_R`` beyond. The profile is compared cell-wise
    away from the two numerically smeared contacts (first-order upwind smears
    a contact over O(√n_steps) cells; a ±15-cell mask is generous for the
    ~185 steps run here).
    """
    fd = v1_legacy_fd()
    rho_l, rho_r = 0.12, 0.01
    assert rho_l > fd.rho_c > rho_r

    n, length = 300, 3000.0
    dx = length / n
    dt = 0.9 * dx / max(fd.v_f, abs(fd.w))
    solver = CTMSolver(
        fd, n_cells=n, length_m=length, dt_s=dt, boundary="open", use_numba=use_numba
    )
    x = (np.arange(n) + 0.5) * dx
    x0 = 0.4 * length
    solver.set_density(np.where(x < x0, rho_l, rho_r))

    q_l = fd.equilibrium_flow(rho_l)
    while solver.t_s < 60.0:
        solver.step(q_in_veh_s=q_l)
    t = solver.t_s
    rho = np.asarray(solver.density)

    x_left = x0 + fd.w * t  # tail contact (congested branch speed)
    x_right = x0 + fd.v_f * t  # head contact (free branch speed)
    analytic = np.where(x < x_left, rho_l, np.where(x < x_right, fd.rho_c, rho_r))
    mask = (np.abs(x - x_left) > 15 * dx) & (np.abs(x - x_right) > 15 * dx)
    assert mask.sum() > 200
    max_err = float(np.abs(rho - analytic)[mask].max())
    assert max_err < 1e-3, f"masked profile error {max_err:.2e} veh/m"

    # The region between the contacts must discharge at capacity (ρ = ρ_c).
    plateau = rho[(x > x_left + 15 * dx) & (x < x_right - 15 * dx)]
    assert plateau.mean() == pytest.approx(fd.rho_c, rel=0.02)
    assert not solver.clamped
