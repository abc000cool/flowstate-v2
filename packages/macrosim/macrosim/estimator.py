"""CTM-based traffic state estimation — Phase 4+ interface stub.

Plan (CLAUDE.md §5.7, §10): a Kalman filter (extending to an ensemble Kalman
filter if the switched-mode linearization proves too brittle) whose process
model is the CTM step of :class:`macrosim.ctm.CTMSolver` and whose
measurements are simulated loop-detector aggregates — 30-second flow
[veh/s] and occupancy (→ density [veh/m]) at a sparse set of detector cells.
The triangular-FD CTM is piecewise linear in the cell densities, so within a
fixed free-flow/congested mode assignment the exact Jacobian is available in
closed form; the filter switches mode per cell per step (a "switching-mode
model" in the Muñoz et al. lineage). This is the standard real-time
traffic-state-estimation stack and the seed of the future live-product tier —
interface now, implementation in Phase 4.

Only the interface is defined here; every method body raises
``NotImplementedError``.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from flowstate_core.artifacts import TriangularFD

__all__ = ["CTMKalmanEstimator"]


class CTMKalmanEstimator:
    """Kalman/EnKF state estimator over a CTM grid (Phase 4+ stub).

    Attributes:
        fd: Triangular fundamental diagram (SI units).
        n_cells: Number of CTM cells in the estimation grid.
        dx_m: Cell length [m].
        dt_s: Filter/CTM time step [s].
        detector_cells: Cell indices carrying loop detectors.
    """

    def __init__(
        self,
        fd: TriangularFD,
        *,
        n_cells: int,
        dx_m: float,
        dt_s: float,
        detector_cells: tuple[int, ...],
        process_noise_veh_m: float = 1e-4,
        flow_noise_veh_s: float = 5e-3,
        density_noise_veh_m: float = 2e-3,
    ) -> None:
        """Store the filter configuration.

        Args:
            fd: Triangular fundamental diagram (SI units).
            n_cells: Number of CTM cells.
            dx_m: Cell length [m].
            dt_s: Filter/CTM time step [s].
            detector_cells: Indices of cells with (simulated) loop detectors.
            process_noise_veh_m: Std of per-cell density process noise [veh/m].
            flow_noise_veh_s: Std of detector flow measurement noise [veh/s].
            density_noise_veh_m: Std of detector density (occupancy-derived)
                measurement noise [veh/m].
        """
        self.fd = fd
        self.n_cells = n_cells
        self.dx_m = dx_m
        self.dt_s = dt_s
        self.detector_cells = detector_cells
        self.process_noise_veh_m = process_noise_veh_m
        self.flow_noise_veh_s = flow_noise_veh_s
        self.density_noise_veh_m = density_noise_veh_m

    def update(
        self,
        flow_veh_s: NDArray[np.float64],
        density_veh_m: NDArray[np.float64],
    ) -> NDArray[np.float64]:
        """Assimilate one 30-s detector aggregate; return the density estimate.

        Args:
            flow_veh_s: Measured flow per detector cell [veh/s], ordered as
                ``detector_cells``.
            density_veh_m: Measured density per detector cell [veh/m], same
                ordering.

        Returns:
            Posterior density estimate for all cells [veh/m], length
            ``n_cells``.

        Raises:
            NotImplementedError: Always — Phase 4 implements this.
        """
        raise NotImplementedError("CTMKalmanEstimator is a Phase-4 deliverable (CLAUDE.md §5.7)")
