"""Synthetic-truth recovery tests for the triangular FD fit (calibration.fd_fit).

A known triangular FD generates a flow-density scatter with realistic
asymmetric congested-branch noise (points scatter *below* the equilibrium
bound — the reason §6.1 prescribes an upper-quantile congested fit) and only
a partial congested branch. The fit must recover v_f within 5%, w and rho_jam
within 15%, with bootstrap CIs bracketing the truth.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from calibration.fd_fit import fit_triangular_fd, quantile_line_fit
from flowstate_core.artifacts import TriangularFD
from flowstate_core.rng import make_rng
from flowstate_core.units import kmh_to_ms, veh_km_to_veh_m

TRUE_FD = TriangularFD(
    v_f=kmh_to_ms(100.0),
    w=-kmh_to_ms(18.0),
    rho_jam=veh_km_to_veh_m(150.0),
)


def _synthetic_scatter(seed: int, n_free: int = 250, n_cong: int = 200) -> pd.DataFrame:
    """Flow-density scatter from TRUE_FD with only a partial congested branch."""
    rng = make_rng(seed)
    rho_c = TRUE_FD.rho_c
    # Free-flow branch: multiplicative noise around q = v_f * rho.
    rho_free = rng.uniform(0.1 * rho_c, 0.8 * rho_c, n_free)
    q_free = TRUE_FD.v_f * rho_free * (1.0 + rng.normal(0.0, 0.02, n_free))
    # Congested branch, PARTIAL (up to 0.6 rho_jam only): scatter mostly below
    # the equilibrium line (non-equilibrium states) -> exponential downward
    # noise plus small symmetric noise.
    rho_cong = rng.uniform(1.15 * rho_c, 0.6 * TRUE_FD.rho_jam, n_cong)
    q_line = -TRUE_FD.w * (TRUE_FD.rho_jam - rho_cong)
    q_cong = q_line - rng.exponential(0.01, n_cong) + rng.normal(0.0, 0.003, n_cong)
    df = pd.DataFrame(
        {
            "density_veh_m": np.concatenate([rho_free, rho_cong]),
            "flow_veh_s": np.concatenate([q_free, q_cong]),
        }
    )
    # Consistent synthetic occupancy (g = 7 m) so the occupancy path works too.
    df["occupancy"] = df["density_veh_m"] * 7.0
    return df


class TestQuantileLineFit:
    def test_exact_on_noiseless_line(self) -> None:
        x = np.linspace(0.0, 10.0, 50)
        y = 3.0 - 0.5 * x
        a, b = quantile_line_fit(x, y, tau=0.9)
        assert a == pytest.approx(3.0, abs=1e-6)
        assert b == pytest.approx(-0.5, abs=1e-6)

    def test_upper_quantile_ignores_downward_outliers(self) -> None:
        rng = make_rng(11)
        x = np.linspace(0.0, 10.0, 200)
        y = 2.0 + 1.0 * x - rng.exponential(1.0, 200)
        a, b = quantile_line_fit(x, y, tau=0.9)
        assert b == pytest.approx(1.0, abs=0.1)
        assert a == pytest.approx(2.0, abs=0.5)

    def test_bad_tau_raises(self) -> None:
        with pytest.raises(ValueError, match="tau"):
            quantile_line_fit(np.arange(3.0), np.arange(3.0), tau=1.0)


class TestFitTriangularFD:
    def test_synthetic_truth_recovery(self) -> None:
        df = _synthetic_scatter(seed=7)
        cal = fit_triangular_fd(
            df,
            created_at="2026-08-29T00:00:00+00:00",
            source="synthetic triangular FD, seed 7",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            seed=13,
        )
        fd = cal.fd
        assert fd.v_f == pytest.approx(TRUE_FD.v_f, rel=0.05)
        assert fd.w == pytest.approx(TRUE_FD.w, rel=0.15)
        assert fd.rho_jam == pytest.approx(TRUE_FD.rho_jam, rel=0.15)
        assert cal.r2_freeflow > 0.98
        assert cal.n_observations == len(df)
        assert cal.congested_quantile == 0.9
        # Bootstrap 95% CIs must bracket the truth (honest uncertainty, §0.6).
        for key, truth in (("v_f", TRUE_FD.v_f), ("w", TRUE_FD.w), ("rho_jam", TRUE_FD.rho_jam)):
            lo, hi = fd.ci95[key]
            assert lo <= truth <= hi, f"{key}: truth {truth} outside CI ({lo}, {hi})"
            assert lo < hi
        # Derived-quantity CIs are recorded too.
        assert "q_max" in fd.ci95 and "rho_c" in fd.ci95

    def test_occupancy_threshold_path(self) -> None:
        df = _synthetic_scatter(seed=21)
        cal = fit_triangular_fd(
            df,
            created_at="2026-08-29T00:00:00+00:00",
            source="synthetic, occupancy split",
            uncongested_max_occupancy=0.85 * TRUE_FD.rho_c * 7.0,
            n_bootstrap=0,
            seed=1,
        )
        assert cal.fd.v_f == pytest.approx(TRUE_FD.v_f, rel=0.05)
        assert cal.fd.rho_jam == pytest.approx(TRUE_FD.rho_jam, rel=0.15)
        assert cal.fd.ci95 == {}  # bootstrap disabled

    def test_bootstrap_is_seeded_and_reproducible(self) -> None:
        df = _synthetic_scatter(seed=7)
        kwargs = dict(
            created_at="2026-08-29T00:00:00+00:00",
            source="repro",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            n_bootstrap=50,
        )
        a = fit_triangular_fd(df, seed=99, **kwargs)
        b = fit_triangular_fd(df, seed=99, **kwargs)
        assert a.fd.ci95 == b.fd.ci95
        assert a.data_hash == b.data_hash

    def test_artifact_round_trip(self, tmp_path) -> None:
        df = _synthetic_scatter(seed=7)
        cal = fit_triangular_fd(
            df,
            created_at="2026-08-29T00:00:00+00:00",
            source="round trip",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            n_bootstrap=0,
        )
        path = tmp_path / "fd.json"
        cal.save(path)
        loaded = type(cal).load(path)
        assert loaded.fd.v_f == cal.fd.v_f
        assert loaded.congested_quantile == 0.9

    def test_missing_columns_raise(self) -> None:
        with pytest.raises(ValueError, match="missing column"):
            fit_triangular_fd(
                pd.DataFrame({"flow_veh_s": [1.0]}),
                created_at="t",
                source="s",
            )

    def test_too_few_congested_points_raise(self) -> None:
        rng = make_rng(3)
        rho = rng.uniform(0.001, 0.8 * TRUE_FD.rho_c, 100)
        df = pd.DataFrame({"density_veh_m": rho, "flow_veh_s": TRUE_FD.v_f * rho})
        with pytest.raises(ValueError, match="congested points"):
            fit_triangular_fd(
                df,
                created_at="t",
                source="s",
                uncongested_max_density=0.85 * TRUE_FD.rho_c,
                n_bootstrap=0,
            )
