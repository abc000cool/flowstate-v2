"""The Phase-4 estimator stub exposes its interface but no implementation."""

from __future__ import annotations

import numpy as np
import pytest

from macrosim.estimator import CTMKalmanEstimator
from macrosim.fundamental import v1_legacy_fd


def test_estimator_interface_is_stub() -> None:
    est = CTMKalmanEstimator(
        v1_legacy_fd(),
        n_cells=100,
        dx_m=100.0,
        dt_s=1.0,
        detector_cells=(0, 50, 99),
    )
    assert est.n_cells == 100
    assert est.detector_cells == (0, 50, 99)
    with pytest.raises(NotImplementedError, match="Phase-4"):
        est.update(np.zeros(3), np.zeros(3))
