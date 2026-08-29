"""Density-bounds property tests (hypothesis) and the hard CFL guard.

CLAUDE.md §5.2/§5.3: densities must stay in [0, ρ_jam] with the ``clamped``
flag never set (a clamp firing is a test failure, not a silent fix), and a
CFL-violating Δt is a construction-time ``ValueError``.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from flowstate_core.rng import make_rng
from macrosim.ctm import CTMSolver, cfl_max_dt
from macrosim.fundamental import v1_legacy_fd

_N_CELLS = 50
_LENGTH_M = 2500.0
_STEPS = 60


@settings(max_examples=25, deadline=None, derandomize=True)
@given(
    ic_seed=st.integers(min_value=0, max_value=2**32 - 1),
    boundary=st.sampled_from(["ring", "open"]),
    inflow_frac=st.floats(min_value=0.0, max_value=1.3, allow_nan=False),
    use_numba=st.booleans(),
)
def test_random_ic_densities_stay_in_bounds(
    ic_seed: int, boundary: str, inflow_frac: float, use_numba: bool
) -> None:
    """Random ICs in [0, ρ_jam] stay in bounds; clamped is never set.

    The IC is drawn from a generator seeded by the hypothesis-provided
    ``ic_seed`` (explicit-seed discipline, docs/CONTRACTS.md §6);
    ``derandomize=True`` keeps the example set itself reproducible.
    """
    fd = v1_legacy_fd()
    rng = make_rng(ic_seed)
    ic = rng.uniform(0.0, fd.rho_jam, _N_CELLS)
    dt = 0.9 * cfl_max_dt(fd, _LENGTH_M / _N_CELLS)
    solver = CTMSolver(
        fd,
        n_cells=_N_CELLS,
        length_m=_LENGTH_M,
        dt_s=dt,
        boundary=boundary,  # type: ignore[arg-type]
        use_numba=use_numba,
    )
    solver.set_density(ic)
    q_in = inflow_frac * fd.q_max if boundary == "open" else 0.0
    for _ in range(_STEPS):
        solver.step(q_in_veh_s=q_in)
        rho = solver.density
        assert float(rho.min()) >= 0.0
        assert float(rho.max()) <= fd.rho_jam
    assert not solver.clamped


@settings(max_examples=15, deadline=None, derandomize=True)
@given(
    ic_seed=st.integers(min_value=0, max_value=2**32 - 1),
    cap_frac=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    cap_iface=st.integers(min_value=0, max_value=_N_CELLS),
)
def test_bounds_hold_under_flux_caps(ic_seed: int, cap_frac: float, cap_iface: int) -> None:
    """Interface flux caps (bottleneck/perturbation actuation) keep bounds."""
    fd = v1_legacy_fd()
    rng = make_rng(ic_seed)
    ic = rng.uniform(0.0, fd.rho_jam, _N_CELLS)
    dt = 0.9 * cfl_max_dt(fd, _LENGTH_M / _N_CELLS)
    solver = CTMSolver(
        fd, n_cells=_N_CELLS, length_m=_LENGTH_M, dt_s=dt, boundary="ring", use_numba=True
    )
    solver.set_density(ic)
    caps = {cap_iface: cap_frac * fd.q_max}
    for _ in range(_STEPS):
        solver.step(iface_caps=caps)
        assert float(solver.density.min()) >= 0.0
        assert float(solver.density.max()) <= fd.rho_jam
    assert not solver.clamped


class TestCFLGuard:
    """Hard CFL guard at construction (CLAUDE.md §5.2)."""

    def test_violating_dt_raises(self) -> None:
        fd = v1_legacy_fd()
        dx = 100.0
        dt_max = cfl_max_dt(fd, dx)
        with pytest.raises(ValueError, match="CFL"):
            CTMSolver(fd, n_cells=100, length_m=100 * dx, dt_s=1.01 * dt_max)

    def test_dt_at_exact_limit_is_allowed(self) -> None:
        fd = v1_legacy_fd()
        dx = 100.0
        solver = CTMSolver(fd, n_cells=100, length_m=100 * dx, dt_s=cfl_max_dt(fd, dx))
        assert solver.dt_s == pytest.approx(dx / max(fd.v_f, abs(fd.w)))

    def test_cfl_limit_uses_the_faster_characteristic(self) -> None:
        """The bound is dx / max(v_f, |w|) — v_f governs for realistic FDs."""
        fd = v1_legacy_fd()
        assert cfl_max_dt(fd, 100.0) == pytest.approx(100.0 / fd.v_f)

    def test_nonpositive_dt_raises(self) -> None:
        with pytest.raises(ValueError, match="dt_s"):
            CTMSolver(v1_legacy_fd(), n_cells=10, length_m=1000.0, dt_s=0.0)

    def test_too_few_cells_raises(self) -> None:
        with pytest.raises(ValueError, match="n_cells"):
            CTMSolver(v1_legacy_fd(), n_cells=2, length_m=1000.0, dt_s=0.1)


class TestStepValidation:
    """Misuse of step() fails loudly instead of corrupting the ledger."""

    @staticmethod
    def _solver(boundary: str) -> CTMSolver:
        fd = v1_legacy_fd()
        dt = 0.9 * cfl_max_dt(fd, 100.0)
        return CTMSolver(
            fd,
            n_cells=20,
            length_m=2000.0,
            dt_s=dt,
            boundary=boundary,  # type: ignore[arg-type]
        )

    def test_ring_rejects_inflow(self) -> None:
        with pytest.raises(ValueError, match="ring"):
            self._solver("ring").step(q_in_veh_s=0.1)

    def test_negative_inflow_rejected(self) -> None:
        with pytest.raises(ValueError, match=">= 0"):
            self._solver("open").step(q_in_veh_s=-0.1)

    def test_bad_interface_index_rejected(self) -> None:
        with pytest.raises(ValueError, match="interface"):
            self._solver("ring").step(iface_caps={21: 0.1})

    def test_negative_cap_rejected(self) -> None:
        with pytest.raises(ValueError, match="cap"):
            self._solver("ring").step(iface_caps={5: -0.1})

    def test_out_of_range_ic_rejected(self) -> None:
        solver = self._solver("ring")
        with pytest.raises(ValueError, match="rho"):
            solver.set_uniform_density(solver.fd.rho_jam * 1.1)
