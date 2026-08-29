"""macrosim — the ported v1 LWR/CTM engine, repurposed as the screening tier.

Fast Godunov/CTM solver with moving-bottleneck actuation and (Phase 4+) a
state-estimation interface. Every output is labeled ``tier="screening"``;
this tier makes no claims about phantom-jam formation or dissipation
(CLAUDE.md ADR-1, §5).
"""

from macrosim.bottleneck import BottleneckVariant, MovingBottleneck
from macrosim.ctm import BOUND_TOL, Boundary, CTMSolver, cfl_max_dt
from macrosim.estimator import CTMKalmanEstimator
from macrosim.fundamental import (
    TriangularFD,
    capacity_at_speed,
    equilibrium_speed,
    equilibrium_speed_scalar,
    fd_tuple,
    v1_legacy_fd,
)
from macrosim.runner import run_macro

__all__ = [
    "BOUND_TOL",
    "BottleneckVariant",
    "Boundary",
    "CTMKalmanEstimator",
    "CTMSolver",
    "MovingBottleneck",
    "TriangularFD",
    "capacity_at_speed",
    "cfl_max_dt",
    "equilibrium_speed",
    "equilibrium_speed_scalar",
    "fd_tuple",
    "run_macro",
    "v1_legacy_fd",
]

__version__ = "2.0.0-dev"
