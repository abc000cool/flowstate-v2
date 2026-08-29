"""Moving-bottleneck actuation tests (CLAUDE.md §5.5).

The primary variant is the discrete Delle Monache–Goatin (2014) moving flux
constraint ``F ← min(F, ρ_i·v*)`` at the AV-occupied cell; the alternative
caps the interface at the reduced-speed capacity ``α(v*)·q_max``.
"""

from __future__ import annotations

import numpy as np
import pytest

from macrosim.bottleneck import MovingBottleneck
from macrosim.ctm import CTMSolver, cfl_max_dt
from macrosim.fundamental import capacity_at_speed, v1_legacy_fd

_BASE_RHO = 0.02  # veh/m — free-flow, V_e = v_f, so a slow AV binds hard
_V_STAR = 8.0  # m/s — well below V_e(base) = v_f = 27.8 m/s


def _ring(use_numba: bool = True) -> CTMSolver:
    fd = v1_legacy_fd()
    n, length = 250, 5000.0
    dt = 0.9 * cfl_max_dt(fd, length / n)
    solver = CTMSolver(
        fd, n_cells=n, length_m=length, dt_s=dt, boundary="ring", use_numba=use_numba
    )
    solver.set_uniform_density(_BASE_RHO)
    return solver


@pytest.mark.parametrize("use_numba", [False, True], ids=["python", "numba"])
def test_slow_av_congests_upstream_and_starves_downstream(use_numba: bool) -> None:
    """Relative to the no-AV baseline, ρ rises behind the AV and drops ahead.

    The no-AV baseline of a uniform ring state is that exact uniform state
    forever (LWR equilibrium), so the comparison is against ``_BASE_RHO``.
    """
    solver = _ring(use_numba)
    av = MovingBottleneck(x_m=2500.0, v_star_ms=_V_STAR, variant="flux_cap")
    while solver.t_s < 600.0:
        cap = av.iface_cap(solver)
        assert cap is not None
        solver.step(iface_caps=dict([cap]))
        av.advance(solver)

    rho = np.asarray(solver.density)
    n = solver.n_cells
    k = av.cell_index(solver)
    upstream = float(np.mean(rho[[(k - j) % n for j in range(1, 16)]]))
    downstream = float(np.mean(rho[[(k + 2 + j) % n for j in range(15)]]))
    assert upstream > 2.0 * _BASE_RHO, f"no queue formed upstream ({upstream:.4f} veh/m)"
    assert downstream < 0.6 * _BASE_RHO, f"no starvation downstream ({downstream:.4f} veh/m)"
    assert not solver.clamped


@pytest.mark.parametrize("use_numba", [False, True], ids=["python", "numba"])
def test_flux_at_av_interface_never_exceeds_rho_v_star(use_numba: bool) -> None:
    """Realized flux at the constrained interface obeys F ≤ ρ_i·v* every step."""
    solver = _ring(use_numba)
    av = MovingBottleneck(x_m=1000.0, v_star_ms=_V_STAR, variant="flux_cap")
    while solver.t_s < 300.0:
        cell = av.cell_index(solver)
        rho_before = float(solver.density[cell])
        cap = av.iface_cap(solver)
        assert cap is not None
        iface, _ = cap
        solver.step(iface_caps=dict([cap]))
        realized = float(solver.last_flux[iface])
        assert realized <= rho_before * _V_STAR + 1e-12, (
            f"F={realized:.6g} > rho*v*={rho_before * _V_STAR:.6g} at t={solver.t_s:.1f}"
        )
        av.advance(solver)
    assert not solver.clamped


def test_capacity_variant_caps_at_reduced_capacity() -> None:
    """Alternative variant: F ≤ α(v*)·q_max with α from the reduced-speed FD."""
    solver = _ring()
    fd = solver.fd
    av = MovingBottleneck(x_m=2500.0, v_star_ms=_V_STAR, variant="capacity")
    q_cap = capacity_at_speed(fd, _V_STAR)
    assert 0.0 < q_cap < fd.q_max

    while solver.t_s < 300.0:
        cap = av.iface_cap(solver)
        assert cap is not None
        iface, cap_val = cap
        assert cap_val == pytest.approx(q_cap)
        solver.step(iface_caps=dict([cap]))
        assert float(solver.last_flux[iface]) <= q_cap + 1e-12
        av.advance(solver)

    # Partial obstruction still congests upstream relative to baseline.
    rho = np.asarray(solver.density)
    n = solver.n_cells
    k = av.cell_index(solver)
    upstream = float(np.mean(rho[[(k - j) % n for j in range(1, 16)]]))
    assert upstream > _BASE_RHO
    assert not solver.clamped


def test_av_trajectory_advances_at_min_of_v_star_and_local_speed() -> None:
    """The AV travels at v* in free flow but is carried at V_e in a jam."""
    fd = v1_legacy_fd()
    n, length = 100, 2000.0
    dt = 0.9 * cfl_max_dt(fd, length / n)
    solver = CTMSolver(fd, n_cells=n, length_m=length, dt_s=dt, boundary="ring")

    # Free flow: local V_e = v_f > v*, so the AV moves at exactly v*.
    solver.set_uniform_density(0.5 * fd.rho_c)
    av = MovingBottleneck(x_m=0.0, v_star_ms=_V_STAR)
    av.advance(solver)
    assert av.v_actual_ms == pytest.approx(_V_STAR)
    assert av.x_m == pytest.approx(_V_STAR * solver.dt_s)

    # Deep congestion: V_e(ρ) < v*, so the AV is carried with the traffic.
    rho_dense = 0.9 * fd.rho_jam
    v_e_dense = -fd.w * (fd.rho_jam - rho_dense) / rho_dense
    assert v_e_dense < _V_STAR
    solver.set_uniform_density(rho_dense)
    av2 = MovingBottleneck(x_m=0.0, v_star_ms=_V_STAR)
    av2.advance(solver)
    assert av2.v_actual_ms == pytest.approx(v_e_dense)


def test_inactive_av_constrains_nothing() -> None:
    """A non-compliant (inactive) AV must impose no flux cap."""
    solver = _ring()
    av = MovingBottleneck(x_m=2500.0, v_star_ms=_V_STAR, active=False)
    assert av.iface_cap(solver) is None
    for _ in range(200):
        solver.step()
        av.advance(solver)
    # Uniform ring state stays uniform without a constraint.
    rho = np.asarray(solver.density)
    assert float(rho.std()) < 1e-12


def test_av_exits_open_corridor_and_deactivates() -> None:
    """On an open corridor the AV deactivates at the downstream end."""
    fd = v1_legacy_fd()
    n, length = 50, 500.0
    dt = 0.9 * cfl_max_dt(fd, length / n)
    solver = CTMSolver(fd, n_cells=n, length_m=length, dt_s=dt, boundary="open")
    solver.set_uniform_density(0.5 * fd.rho_c)
    av = MovingBottleneck(x_m=length - 20.0, v_star_ms=_V_STAR)
    for _ in range(50):
        solver.step()
        av.advance(solver)
    assert not av.active
    assert av.iface_cap(solver) is None
