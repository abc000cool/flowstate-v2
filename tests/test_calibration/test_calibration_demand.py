"""Demand-fitter tests on a fast surrogate simulator (calibration.demand).

The simulate function is injected (docs: the microscopic tier provides the
real one), so a toy surrogate with a known ground-truth inflow lets us assert
convergence of the iterative proportional scaling to the GEH criterion.
"""

from __future__ import annotations

from math import sqrt

import numpy as np
import pandas as pd
import pytest

from calibration.demand import fit_inflow, geh
from flowstate_core.artifacts import DemandProfile
from flowstate_core.units import veh_s_to_veh_h

CREATED = "2026-08-29T00:00:00+00:00"

TRUTH_STEPS = [(0.0, 0.5), (900.0, 0.9), (1800.0, 0.4)]
BIN_S = 300.0
HORIZON_S = 2700.0


def _profile(steps: list[tuple[float, float]]) -> DemandProfile:
    return DemandProfile(created_at=CREATED, source="test", data_hash="", steps=steps)


def _surrogate(profile: DemandProfile) -> pd.DataFrame:
    """Toy link 'simulator': attenuated inflow plus a base flow, per bin."""
    starts = np.arange(0.0, HORIZON_S, BIN_S)
    flows = [0.92 * profile.inflow_at(t0 + BIN_S / 2.0) + 0.01 for t0 in starts]
    return pd.DataFrame({"t_start_s": starts, "t_end_s": starts + BIN_S, "flow_veh_s": flows})


def _observed() -> pd.DataFrame:
    return _surrogate(_profile(list(TRUTH_STEPS)))


class TestGeh:
    def test_hand_computed(self) -> None:
        # GEH = sqrt(2 (m-c)^2 / (m+c)) on hourly volumes (FHWA-HOP-18-036).
        assert geh(1000.0, 900.0) == pytest.approx(sqrt(2 * 100.0**2 / 1900.0), rel=1e-12)

    def test_zero_volumes(self) -> None:
        assert geh(0.0, 0.0) == 0.0

    def test_symmetry(self) -> None:
        assert geh(700.0, 500.0) == pytest.approx(geh(500.0, 700.0), rel=1e-12)


class TestFitInflow:
    def test_converges_to_known_truth(self) -> None:
        initial = _profile([(0.0, 0.6), (900.0, 0.6), (1800.0, 0.6)])
        fitted = fit_inflow(
            _observed(),
            initial,
            _surrogate,
            created_at=CREATED,
            source="toy surrogate",
            geh_threshold=1.0,  # much tighter than the FHWA 5 to force recovery
            geh_pass_frac=1.0,
            max_iters=30,
        )
        assert fitted.geh_vs_counts is not None
        assert fitted.geh_vs_counts < 1.0  # worst-bin GEH of the returned profile
        for (t_fit, q_fit), (t_true, q_true) in zip(fitted.steps, TRUTH_STEPS, strict=True):
            assert t_fit == t_true
            assert q_fit == pytest.approx(q_true, rel=0.02)

    def test_meets_fhwa_criterion_shape(self) -> None:
        initial = _profile([(0.0, 0.45), (900.0, 1.1), (1800.0, 0.5)])
        fitted = fit_inflow(
            _observed(),
            initial,
            _surrogate,
            created_at=CREATED,
            source="toy surrogate",
            geh_threshold=5.0,
            geh_pass_frac=0.85,
            max_iters=25,
        )
        sim = _surrogate(fitted)
        obs = _observed()
        gehs = [
            geh(veh_s_to_veh_h(m), veh_s_to_veh_h(c))
            for m, c in zip(sim["flow_veh_s"], obs["flow_veh_s"], strict=True)
        ]
        assert np.mean(np.array(gehs) < 5.0) >= 0.85

    def test_zero_iters_returns_scored_initial(self) -> None:
        initial = _profile([(0.0, 0.6), (900.0, 0.6), (1800.0, 0.6)])
        fitted = fit_inflow(
            _observed(),
            initial,
            _surrogate,
            created_at=CREATED,
            source="toy surrogate",
            geh_threshold=0.001,  # unreachable -> no early stop
            geh_pass_frac=1.0,
            max_iters=0,
        )
        # No scaling happened; the reported GEH describes the initial profile.
        assert [q for _, q in fitted.steps] == [0.6, 0.6, 0.6]
        assert fitted.geh_vs_counts is not None and fitted.geh_vs_counts > 0.001

    def test_scale_damping_is_bounded(self) -> None:
        initial = _profile([(0.0, 0.001), (900.0, 0.001), (1800.0, 0.001)])
        fitted = fit_inflow(
            _observed(),
            initial,
            _surrogate,
            created_at=CREATED,
            source="toy surrogate",
            max_iters=1,
            max_scale_step=2.0,
            geh_threshold=0.001,
            geh_pass_frac=1.0,
        )
        # One damped iteration can at most double each step.
        for (_, q_fit), (_, q0) in zip(fitted.steps, initial.steps, strict=True):
            assert q_fit <= 2.0 * q0 + 1e-12

    def test_mismatched_bins_raise(self) -> None:
        def bad_sim(profile: DemandProfile) -> pd.DataFrame:
            return pd.DataFrame({"t_start_s": [0.0], "t_end_s": [1.0], "flow_veh_s": [0.1]})

        with pytest.raises(ValueError, match="bins"):
            fit_inflow(
                _observed(),
                _profile(list(TRUTH_STEPS)),
                bad_sim,
                created_at=CREATED,
                source="toy",
            )

    def test_missing_columns_raise(self) -> None:
        with pytest.raises(ValueError, match="missing column"):
            fit_inflow(
                pd.DataFrame({"t_start_s": [0.0]}),
                _profile(list(TRUTH_STEPS)),
                _surrogate,
                created_at=CREATED,
                source="toy",
            )
