"""Conservation tests (CLAUDE.md §5.3).

Closed ring: total vehicle count conserved to 1e−10 *per step* over 5000
steps. Open corridor: the boundary ledger must satisfy
``inflow − outflow − Δstorage = 0`` to 1e−8 — vehicles never appear or vanish,
including under congestion, boundary queuing, and interface flux caps.
"""

from __future__ import annotations

import numpy as np
import pytest

from flowstate_core.rng import make_rng
from macrosim.ctm import CTMSolver
from macrosim.fundamental import v1_legacy_fd


def _bumpy_ic(n: int, base: float, amp: float, seed: int, rho_jam: float) -> np.ndarray:
    """Base density + seeded smooth noise, clipped inside (0, rho_jam)."""
    rng = make_rng(seed)
    noise = rng.normal(0.0, 1.0, n)
    kernel = np.exp(-0.5 * (np.arange(-8, 9) / 3.0) ** 2)
    smooth = np.convolve(np.tile(noise, 3), kernel / kernel.sum(), mode="same")[n : 2 * n]
    return np.clip(base + amp * smooth, 0.0, rho_jam)


def test_ring_conservation_5000_steps_numba() -> None:
    """Ring total vehicles conserved to 1e-10 per step over 5000 steps."""
    fd = v1_legacy_fd()
    n, length = 150, 10000.0
    dt = 0.9 * (length / n) / max(fd.v_f, abs(fd.w))
    solver = CTMSolver(fd, n_cells=n, length_m=length, dt_s=dt, boundary="ring", use_numba=True)
    solver.set_density(_bumpy_ic(n, 0.05, 0.03, seed=1234, rho_jam=fd.rho_jam))

    n0 = solver.total_vehicles()
    prev = n0
    worst = 0.0
    for _ in range(5000):
        solver.step()
        cur = solver.total_vehicles()
        worst = max(worst, abs(cur - prev))
        prev = cur
    assert worst < 1e-10, f"worst per-step drift {worst:.3e} veh"
    assert solver.total_vehicles() == pytest.approx(n0, abs=1e-9)
    assert not solver.clamped


def test_ring_conservation_python_kernel() -> None:
    """Same conservation property on the pure-Python fallback kernel."""
    fd = v1_legacy_fd()
    n, length = 100, 5000.0
    dt = 0.9 * (length / n) / max(fd.v_f, abs(fd.w))
    solver = CTMSolver(fd, n_cells=n, length_m=length, dt_s=dt, boundary="ring", use_numba=False)
    solver.set_density(_bumpy_ic(n, 0.06, 0.04, seed=99, rho_jam=fd.rho_jam))

    n0 = solver.total_vehicles()
    prev = n0
    worst = 0.0
    for _ in range(500):
        solver.step()
        cur = solver.total_vehicles()
        worst = max(worst, abs(cur - prev))
        prev = cur
    assert worst < 1e-10
    assert not solver.clamped


@pytest.mark.parametrize("use_numba", [False, True], ids=["python", "numba"])
def test_open_corridor_ledger_balances(use_numba: bool) -> None:
    """inflow − outflow − Δstorage = 0 to 1e-8, through congestion and queuing.

    A mid-corridor flux cap creates a real bottleneck (backup, then supply
    limitation at the upstream boundary and a growing entry queue); removing
    it lets the backlog discharge. The ledger must balance throughout.
    """
    fd = v1_legacy_fd()
    n, length = 120, 6000.0
    dt = 0.9 * (length / n) / max(fd.v_f, abs(fd.w))
    solver = CTMSolver(
        fd, n_cells=n, length_m=length, dt_s=dt, boundary="open", use_numba=use_numba
    )
    solver.set_uniform_density(0.01)
    n0 = solver.total_vehicles()

    q_in = 0.8 * fd.q_max
    cap = {n // 2: 0.3 * fd.q_max}
    for k in range(3000):
        caps = cap if k < 1500 else None
        solver.step(q_in_veh_s=q_in, iface_caps=caps)
        balance = solver.vehicles_in - solver.vehicles_out - (solver.total_vehicles() - n0)
        assert abs(balance) < 1e-8, f"ledger imbalance {balance:.3e} veh at step {k}"
    # The bottleneck must actually have produced congestion for this test to
    # mean anything.
    assert np.asarray(solver.density).max() > fd.rho_c
    assert not solver.clamped


def test_open_corridor_queue_accounts_for_unserved_demand() -> None:
    """Demand beyond capacity queues at the boundary instead of vanishing."""
    fd = v1_legacy_fd()
    n, length = 60, 3000.0
    dt = 0.9 * (length / n) / max(fd.v_f, abs(fd.w))
    solver = CTMSolver(fd, n_cells=n, length_m=length, dt_s=dt, boundary="open", use_numba=True)

    q_in = 1.5 * fd.q_max  # oversaturated demand
    t_total = 0.0
    for _ in range(1000):
        solver.step(q_in_veh_s=q_in)
        t_total += solver.dt_s
    offered = q_in * t_total
    assert solver.queue_veh > 0.0
    assert solver.vehicles_in + solver.queue_veh == pytest.approx(offered, abs=1e-8)
    # Mainline can never absorb more than capacity.
    assert solver.vehicles_in <= fd.q_max * t_total * (1 + 1e-12)
