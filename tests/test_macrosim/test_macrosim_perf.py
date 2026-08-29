"""Performance target: 1000 cells × 10,000 steps ≤ 1 s (CLAUDE.md §3.4).

Marked slow so CI (``-m "not slow"``) skips it; run locally with
``uv run --no-sync pytest tests/test_macrosim -m slow``. Wall-clock timing on
shared CI runners is noise, not signal — this belongs on a real machine.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from flowstate_core.rng import make_rng
from macrosim.ctm import CTMSolver, cfl_max_dt
from macrosim.fundamental import v1_legacy_fd


@pytest.mark.slow
def test_numba_kernel_1000_cells_10000_steps_under_1s() -> None:
    fd = v1_legacy_fd()
    n, length = 1000, 100_000.0
    dt = 0.9 * cfl_max_dt(fd, length / n)

    # Warm up the JIT (compilation must not count against the physics budget).
    warm = CTMSolver(fd, n_cells=n, length_m=length, dt_s=dt, boundary="ring", use_numba=True)
    warm.set_uniform_density(0.05)
    for _ in range(5):
        warm.step()

    solver = CTMSolver(fd, n_cells=n, length_m=length, dt_s=dt, boundary="ring", use_numba=True)
    rng = make_rng(4242)
    solver.set_density(np.clip(rng.uniform(0.02, 0.08, n), 0.0, fd.rho_jam))

    t0 = time.perf_counter()
    for _ in range(10_000):
        solver.step()
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"1000 cells x 10000 steps took {elapsed:.3f} s (target < 1 s)"
    assert not solver.clamped
