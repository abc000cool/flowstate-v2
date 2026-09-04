"""Tests for the tracking-coverage estimators (calibration.coverage).

Synthetic-truth recovery, ``calibration.coverage.synthetic_validation``
(seed 42, 20,000 vehicles per lane; estimators applied to the thinned
lane). "moments" uses the generator's true cv; "mixture" is told nothing;
"equil" is the IDM-equilibrium method (only meaningful in the IDM regime);
"bound" is the capacity bound with a ceiling 10% above the true flow and
must return ``c/1.1``.

| regime                  | c   | cv_true | moments | mixture | equil | bound |
|-------------------------|-----|---------|---------|---------|-------|-------|
| congested               | 0.4 | 0.35    | 0.411   | 0.390   | —     | 0.363 |
| congested               | 0.6 | 0.35    | 0.628   | 0.601   | —     | 0.550 |
| congested               | 0.8 | 0.35    | 0.799   | 0.800   | —     | 0.727 |
| congested_classwidth    | 0.4 | 0.32    | 0.374   | 0.392   | —     | 0.362 |
| congested_classwidth    | 0.6 | 0.32    | 0.601   | 0.603   | —     | 0.551 |
| congested_classwidth    | 0.8 | 0.32    | 0.808   | 0.801   | —     | 0.730 |
| congested_heterogeneous | 0.4 | 0.64    | 0.092   | 0.197   | —     | 0.363 |
| congested_heterogeneous | 0.6 | 0.64    | 0.306   | 0.291   | —     | 0.544 |
| congested_heterogeneous | 0.8 | 0.64    | 0.652   | 0.391   | —     | 0.728 |
| uncongested             | 0.4 | 0.80    | 0.493   | 0.187   | —     | 0.358 |
| uncongested             | 0.6 | 0.80    | 0.664   | 0.308   | —     | 0.547 |
| uncongested             | 0.8 | 0.80    | 0.888   | 0.457   | —     | 0.727 |
| congested_correlated    | 0.4 | 0.35    | 0.133   | 0.362   | —     | 0.360 |
| congested_correlated    | 0.6 | 0.35    | 0.000   | 0.554   | —     | 0.551 |
| congested_correlated    | 0.8 | 0.35    | 0.186   | 0.749   | —     | 0.729 |
| idm_equilibrium         | 0.4 | 0.09    | 0.409   | 0.404   | 0.404 | 0.368 |
| idm_equilibrium         | 0.6 | 0.09    | 0.595   | 0.601   | 0.601 | 0.546 |
| idm_equilibrium         | 0.8 | 0.09    | 0.795   | 0.796   | 0.796 | 0.724 |

Reading: with a homogeneous true spacing scale (``congested``,
``congested_classwidth`` with ±17% platoon spread, ``idm_equilibrium``) the
mixture recovers ``c`` within 0.01 and the moment estimator within 0.03
across c ∈ {0.4, 0.6, 0.8}. Correlated losses (runs of mean length 3) bias
the mixture low by at most 0.05 and destroy the moment estimator (the
variance is dominated by long runs). A bimodal within-class scale mix
(8 m / 25 m platoons) and free flow (cv 0.8) bias the mixture low by
0.2–0.4: the estimator must not be used outside the congested regime, and
where the speed-class scale is heterogeneous it is a lower bound. These
limits are asserted below so they stay documented.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from calibration.coverage import (
    DEFAULT_S_DUP_M,
    combine_with_bound,
    coverage_capacity_bound,
    coverage_equilibrium,
    coverage_gap_mixture,
    coverage_gap_moments,
    coverage_section_crossings,
    fit_gap_mixture,
    idm_equilibrium_density,
    snapshot_spacings,
    synthetic_validation,
    thin_positions,
)
from flowstate_core.rng import make_rng

IDM = {"v0": 32.4, "T": 1.322, "s0": 2.533}


@pytest.fixture(scope="module")
def synthetic_rows() -> dict[tuple[str, float], dict[str, float]]:
    rows = synthetic_validation()
    return {(r["regime"], r["c_true"]): r for r in rows}


class TestSnapshotSpacings:
    def test_hand_computed(self) -> None:
        # Two snapshots 1 s apart plus an off-snapshot slot at 0.2 s.
        t = np.array([0.0, 0.0, 0.0, 0.2, 0.2, 0.2, 1.0, 1.0])
        x = np.array([25.0, 0.0, 10.0, 26.0, 1.0, 11.0, 12.0, 5.0])
        v = np.array([3.0, 1.0, 2.0, 3.0, 1.0, 2.0, 6.0, 4.0])
        sp, vp = snapshot_spacings(t, x, v, sample_dt=0.2, snapshot_dt=1.0)
        assert sp.tolist() == pytest.approx([10.0, 15.0, 7.0])
        assert vp.tolist() == pytest.approx([1.5, 2.5, 5.0])

    def test_invalid_snapshot_interval(self) -> None:
        with pytest.raises(ValueError, match="multiple"):
            snapshot_spacings(np.zeros(2), np.zeros(2), np.zeros(2), sample_dt=0.2, snapshot_dt=0.5)

    def test_empty(self) -> None:
        sp, vp = snapshot_spacings(
            np.zeros(0), np.zeros(0), np.zeros(0), sample_dt=0.2, snapshot_dt=1.0
        )
        assert sp.size == 0 and vp.size == 0


class TestEquilibrium:
    def test_density_closed_form(self) -> None:
        v = 8.0
        s_eq = (IDM["s0"] + v * IDM["T"]) / math.sqrt(1.0 - (v / IDM["v0"]) ** 4)
        assert idm_equilibrium_density(v, IDM) == pytest.approx(1.0 / (s_eq + 5.0))

    def test_coverage_is_density_ratio(self) -> None:
        rho_eq = idm_equilibrium_density(8.0, IDM)
        assert coverage_equilibrium(0.5 * rho_eq, 8.0, IDM) == pytest.approx(0.5)
        assert coverage_equilibrium(2.0 * rho_eq, 8.0, IDM) == 1.0

    def test_free_flow_is_nan(self) -> None:
        assert math.isnan(coverage_equilibrium(0.01, 0.95 * IDM["v0"], IDM))
        assert math.isnan(coverage_equilibrium(0.0, 8.0, IDM))

    def test_speed_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError):
            idm_equilibrium_density(IDM["v0"], IDM)


class TestGapMoments:
    def test_formula_hand_computed(self) -> None:
        # mean 15, population variance 25 -> cv_obs^2 = 1/9 -> c = 8/9.
        s = np.array([10.0, 10.0, 20.0, 20.0])
        assert coverage_gap_moments(s, 0.0) == pytest.approx(8.0 / 9.0)
        # cv_true = 0.2 -> divide by 0.96.
        assert coverage_gap_moments(s, 0.2) == pytest.approx((8.0 / 9.0) / 0.96)
        # cv_obs below cv_true is impossible under the model: clipped to 1.
        assert coverage_gap_moments(s, 0.5) == 1.0

    def test_duplicates_removed(self) -> None:
        s = np.array([1.0, 1.0, 10.0, 10.0, 20.0, 20.0])
        assert coverage_gap_moments(s, 0.0) == pytest.approx(8.0 / 9.0)

    def test_invalid_cv_true(self) -> None:
        with pytest.raises(ValueError, match="cv_true"):
            coverage_gap_moments(np.array([10.0, 12.0]), 1.0)

    @pytest.mark.parametrize("c", [0.4, 0.6, 0.8])
    def test_recovers_thinning_probability(self, c: float) -> None:
        rng = make_rng(3)
        alpha = 1 / 0.35**2
        true_s = rng.gamma(alpha, 12.0 / alpha, size=30_000)
        obs = thin_positions(true_s, c, rng)
        assert coverage_gap_moments(obs, 0.35) == pytest.approx(c, abs=0.03)


class TestGapMixture:
    def test_no_thinning_hits_upper_box(self) -> None:
        rng = make_rng(5)
        true_s = rng.gamma(8.0, 1.5, size=5_000)
        fit = fit_gap_mixture(true_s)
        assert fit.c > 0.95
        assert fit.n_duplicates == int((true_s < DEFAULT_S_DUP_M).sum())

    def test_too_few_spacings_is_nan(self) -> None:
        fit = fit_gap_mixture(np.full(20, 10.0), min_n=100)
        assert math.isnan(fit.c) and not fit.converged and fit.n == 20

    def test_censoring_counts(self) -> None:
        rng = make_rng(6)
        obs = thin_positions(rng.gamma(8.0, 1.5, size=5_000), 0.6, rng)
        fit = fit_gap_mixture(obs, s_max=40.0)
        assert fit.n_censored == int((obs[obs >= DEFAULT_S_DUP_M] >= 40.0).sum())
        assert fit.c == pytest.approx(0.6, abs=0.05)

    def test_invalid_thresholds(self) -> None:
        with pytest.raises(ValueError):
            fit_gap_mixture(np.full(200, 10.0), s_dup=10.0, s_max=5.0)

    def test_speed_class_combination(self) -> None:
        rng = make_rng(7)
        # Two classes with different spacing scales, same c: the combined
        # estimate must match c, and the per-class fits must both be usable.
        parts, speeds = [], []
        for mean, v in ((10.0, 2.0), (25.0, 9.0)):
            obs = thin_positions(rng.gamma(9.0, mean / 9.0, size=8_000), 0.6, rng)
            parts.append(obs)
            speeds.append(np.full(obs.size, v))
        res = coverage_gap_mixture(
            np.concatenate(parts),
            np.concatenate(speeds),
            speed_edges_ms=[0.0, 5.0],
            min_n=300,
        )
        assert res.c == pytest.approx(0.6, abs=0.03)
        assert all(cl["usable"] for cl in res.classes)
        assert res.n_used == res.n_total
        assert res.c_min <= res.c <= res.c_max

    def test_v_max_excludes_fast_pairs(self) -> None:
        obs = np.full(1_000, 10.0)
        res = coverage_gap_mixture(obs, np.full(1_000, 20.0), speed_edges_ms=[0.0], v_max_ms=15.0)
        assert math.isnan(res.c) and res.n_used == 0


class TestSyntheticRecovery:
    """Assertions matching the table in the module docstring."""

    @pytest.mark.parametrize("regime", ["congested", "congested_classwidth", "idm_equilibrium"])
    @pytest.mark.parametrize("c", [0.4, 0.6, 0.8])
    def test_homogeneous_scale_recovered(self, synthetic_rows, regime: str, c: float) -> None:
        r = synthetic_rows[(regime, c)]
        assert r["gap_mixture"] == pytest.approx(c, abs=0.05)
        assert r["gap_moments"] == pytest.approx(c, abs=0.05)

    @pytest.mark.parametrize("c", [0.4, 0.6, 0.8])
    def test_equilibrium_exact_in_its_regime(self, synthetic_rows, c: float) -> None:
        r = synthetic_rows[("idm_equilibrium", c)]
        assert r["equilibrium"] == pytest.approx(c, abs=0.02)
        assert math.isnan(synthetic_rows[("congested", c)]["equilibrium"])

    @pytest.mark.parametrize("c", [0.4, 0.6, 0.8])
    def test_correlated_losses(self, synthetic_rows, c: float) -> None:
        r = synthetic_rows[("congested_correlated", c)]
        # Mixture: small downward bias; moments: destroyed by the long runs.
        assert -0.06 <= r["gap_mixture"] - c <= 0.02
        assert r["gap_moments"] < c - 0.2

    @pytest.mark.parametrize("regime", ["congested_heterogeneous", "uncongested"])
    @pytest.mark.parametrize("c", [0.4, 0.6, 0.8])
    def test_documented_failure_modes_bias_low(self, synthetic_rows, regime: str, c: float) -> None:
        r = synthetic_rows[(regime, c)]
        assert r["gap_mixture"] < c - 0.1

    @pytest.mark.parametrize("c", [0.4, 0.6, 0.8])
    def test_bound_is_a_bound(self, synthetic_rows, c: float) -> None:
        for regime in ("congested", "uncongested", "idm_equilibrium"):
            r = synthetic_rows[(regime, c)]
            assert r["capacity_bound_1p1"] == pytest.approx(c / 1.1, abs=0.02)
            assert r["capacity_bound_1p1"] <= c

    def test_table_shape(self, synthetic_rows) -> None:
        regimes = {k[0] for k in synthetic_rows}
        assert regimes == {
            "congested",
            "congested_classwidth",
            "congested_heterogeneous",
            "uncongested",
            "congested_correlated",
            "idm_equilibrium",
        }
        assert len(synthetic_rows) == 18


class TestThinning:
    @pytest.mark.parametrize("run_length", [1.0, 3.0])
    def test_marginal_and_mean_spacing(self, run_length: float) -> None:
        rng = make_rng(9)
        true_s = rng.gamma(9.0, 12.0 / 9.0, size=40_000)
        obs = thin_positions(true_s, 0.6, rng, run_length=run_length)
        assert (obs.size + 1) / (true_s.size + 1) == pytest.approx(0.6, abs=0.02)
        # Wald: E[observed spacing] = mu / c for any stationary thinning.
        assert obs.mean() == pytest.approx(12.0 / 0.6, rel=0.03)

    def test_invalid_c(self) -> None:
        with pytest.raises(ValueError):
            thin_positions(np.ones(10), 0.0, make_rng(1))


class TestBoundsAndSection:
    def test_capacity_bound(self) -> None:
        assert coverage_capacity_bound(0.3, 0.5) == pytest.approx(0.6)
        assert coverage_capacity_bound(0.7, 0.5) == 1.0
        assert math.isnan(coverage_capacity_bound(math.nan, 0.5))
        with pytest.raises(ValueError):
            coverage_capacity_bound(0.3, 0.0)

    def test_combine(self) -> None:
        assert combine_with_bound(0.5, 0.6) == 0.6
        assert combine_with_bound(0.7, 0.6) == 0.7
        assert combine_with_bound(math.nan, 0.6) == 0.6
        assert combine_with_bound(0.5, math.nan) == 0.5
        assert math.isnan(combine_with_bound(math.nan, math.nan))

    def test_section_hand_computed(self) -> None:
        # 90 crossings in 900 s; local tracked flow 0.2 veh/s at coverage
        # 0.5 -> true flow 0.4 veh/s -> 360 vehicles -> c_sec = 0.25.
        assert coverage_section_crossings(90, 900.0, 0.2, 0.5) == pytest.approx(0.25)
        assert coverage_section_crossings(180, 900.0, 0.2, 0.5) == pytest.approx(0.5)
        assert math.isnan(coverage_section_crossings(90, 900.0, 0.0, 0.5))
        assert math.isnan(coverage_section_crossings(90, 900.0, 0.2, math.nan))
