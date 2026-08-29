"""v1-behavior reproduction under the ``v1_legacy`` preset (CLAUDE.md M0).

Two claims, carefully scoped:

1. *Qualitative*: the scenario shape v1 shipped (10 km ring, 100 cells,
   seeded Gaussian density bump, v1_legacy FD) shows the seeded jam decaying
   over time. **This is LWR dissipation — the entropy solution of a
   first-order scalar conservation law damps perturbations, amplified here by
   the numerical diffusion of a first-order Godunov scheme running below
   CFL=1 on the congested branch. It is NOT a phantom-jam claim and NOT a
   controller result** (CLAUDE.md ADR-1, §12.1): v1's framing of this decay
   as "phantom jam dissipation" is exactly the defect the port retires.

2. *Quantitative*: a direct in-test port of v1's update rule
   (``lwr_model.py``: vectorized supply–demand Godunov flux, periodic wrap,
   post-update clip) run in SI units agrees with the v2 solver. The two are
   the *same scheme* — v1's km/h-unit code path converted to SI is
   operation-for-operation the CTM update — so agreement is at machine
   precision (measured max |Δρ| = 0.0 during development; asserted < 1e−12
   to allow for benign floating-point reassociation across
   numpy/numba versions).
"""

from __future__ import annotations

import numpy as np
import pytest

from flowstate_core.artifacts import TriangularFD
from macrosim.ctm import CTMSolver, cfl_max_dt
from macrosim.fundamental import v1_legacy_fd

_N_CELLS = 100
_LENGTH_M = 10_000.0
_BASE_RHO = 0.035  # veh/m == v1's default 35 veh/km (congested band)
_BUMP_AMP = 0.020  # veh/m == v1's 20 veh/km perturbation amplitude scale


def _v1_seeded_ic(fd: TriangularFD) -> np.ndarray:
    """v1's ``add_perturbation``: Gaussian bump at mid-road, width 4 cells."""
    cells = np.arange(_N_CELLS)
    bump = _BUMP_AMP * np.exp(-0.5 * ((cells - 0.5 * _N_CELLS) / (0.04 * _N_CELLS)) ** 2)
    return np.clip(_BASE_RHO + bump, 0.0, fd.rho_jam)


def _v1_step(rho: np.ndarray, fd: TriangularFD, lam: float) -> np.ndarray:
    """Direct port of v1 ``LWRModel.step`` to SI units (no actuators).

    v1 computed demand/supply Godunov fluxes on interior interfaces, used the
    identical periodic wrap flux at both ends, applied the conservative
    update, and clipped to [0, ρ_jam]. ``lam = Δt/Δx`` (v1 carried the
    3600/km-h conversions inline; here everything is SI already).
    """
    demand = np.minimum(fd.v_f * rho, fd.q_max)
    supply = np.minimum(fd.q_max, -fd.w * (fd.rho_jam - rho))
    f_interior = np.minimum(demand[:-1], supply[1:])
    f_wrap = min(
        min(fd.v_f * rho[-1], fd.q_max),
        min(fd.q_max, -fd.w * (fd.rho_jam - rho[0])),
    )
    flux = np.concatenate([[f_wrap], f_interior, [f_wrap]])
    return np.clip(rho - lam * (flux[1:] - flux[:-1]), 0.0, fd.rho_jam)


def test_v1_seeded_jam_dissipates_on_ring() -> None:
    """The seeded density bump decays — LWR dissipation, not phantom-jam physics.

    See the module docstring: this documents that the ported engine
    reproduces v1's headline observation while reframing it honestly. The
    bump amplitude (max density above the ring mean) must fall by at least
    half over v1's default 20-minute horizon, with mass exactly conserved.
    """
    fd = v1_legacy_fd()
    dt = 0.9 * cfl_max_dt(fd, _LENGTH_M / _N_CELLS)
    solver = CTMSolver(
        fd, n_cells=_N_CELLS, length_m=_LENGTH_M, dt_s=dt, boundary="ring", use_numba=True
    )
    ic = _v1_seeded_ic(fd)
    solver.set_density(ic)
    amp0 = float(ic.max() - ic.mean())
    n0 = solver.total_vehicles()

    while solver.t_s < 1200.0:  # v1 default sim_duration_min = 20
        solver.step()

    rho = np.asarray(solver.density)
    amp_end = float(rho.max() - rho.mean())
    assert amp_end < 0.5 * amp0, f"bump amplitude {amp0:.4f} -> {amp_end:.4f} veh/m"
    assert solver.total_vehicles() == pytest.approx(n0, abs=1e-9)
    assert not solver.clamped


@pytest.mark.parametrize("use_numba", [False, True], ids=["python", "numba"])
def test_ctm_matches_direct_v1_port_over_200_steps(use_numba: bool) -> None:
    """Max density difference vs the v1 update rule over 200 steps < 1e-12.

    Identical scheme, identical dt/dx/IC — see module docstring for why the
    tolerance is machine-precision rather than a physics tolerance.
    """
    fd = v1_legacy_fd()
    dx = _LENGTH_M / _N_CELLS
    dt = 0.9 * cfl_max_dt(fd, dx)
    solver = CTMSolver(
        fd, n_cells=_N_CELLS, length_m=_LENGTH_M, dt_s=dt, boundary="ring", use_numba=use_numba
    )
    ic = _v1_seeded_ic(fd)
    solver.set_density(ic)

    rho_v1 = ic.copy()
    lam = dt / dx
    max_diff = 0.0
    for _ in range(200):
        solver.step()
        rho_v1 = _v1_step(rho_v1, fd, lam)
        max_diff = max(max_diff, float(np.abs(np.asarray(solver.density) - rho_v1).max()))
    assert max_diff < 1e-12, f"max |rho_v2 - rho_v1| = {max_diff:.3e} veh/m over 200 steps"
    assert not solver.clamped
