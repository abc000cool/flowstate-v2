"""Synthetic-truth recovery tests for IDM calibration (calibration.idm_fit).

Core check (CLAUDE.md §6.2): generate leader-follower episodes with KNOWN
heterogeneous IDM parameters (seeded draws around the literature defaults),
simulate the follower with the package's own ballistic integrator plus small
gap measurement noise, then verify the population fit recovers the true
population means. The full 40-episode version (15% tolerance) is marked slow;
an unmarked 6-episode variant runs in CI with a looser 25% tolerance.
"""

from __future__ import annotations

from math import sqrt

import numpy as np
import pytest

from calibration.episodes import LeaderFollowerEpisode, validate_episode
from calibration.idm_fit import (
    PARAM_ORDER,
    equilibrium_gap,
    fit_population,
    gap_rmse,
    idm_accel,
    simulate_follower,
)
from flowstate_core.constants import IDM_DEFAULTS, IDM_RANGES
from flowstate_core.rng import make_rng, truncated_normal

DT = 0.5
HETEROGENEITY = 0.08  # sigma as fraction of the mean for the true draws


def _true_params(rng: np.random.Generator) -> dict[str, float]:
    """Draw one driver's true IDM parameters (truncated normal, in-bounds)."""
    params = {}
    for name in PARAM_ORDER:
        lo, hi = IDM_RANGES[name]
        mean = IDM_DEFAULTS[name]
        params[name] = truncated_normal(rng, mean, HETEROGENEITY * mean, low=lo, high=hi)
    params["delta"] = IDM_DEFAULTS["delta"]
    return params


def _leader_profile(rng: np.random.Generator) -> np.ndarray:
    """90 s leader speed profile with strong decel/accel events.

    Two braking events of different depth plus a final free-flow segment —
    strong excitation is required for (T, a_max, s0) identifiability and the
    high-speed tail helps pin v0 (Kesting & Treiber 2008).
    """
    vb = rng.uniform(13.0, 17.0)
    f1 = rng.uniform(0.25, 0.40)
    f2 = rng.uniform(0.50, 0.65)
    vh = rng.uniform(20.0, 24.0)
    knot_t = [0.0, 10.0, 15.0, 23.0, 29.0, 34.0, 38.0, 44.0, 54.0, 90.0]
    knot_v = [vb, vb, f1 * vb, f1 * vb, vb, vb, f2 * vb, f2 * vb, vh, vh]
    t = np.arange(0.0, 90.0 + DT / 2, DT)
    return np.interp(t, knot_t, knot_v)


def _synthetic_episode(
    ep_id: int, rng: np.random.Generator, gap_noise_m: float = 0.05
) -> tuple[LeaderFollowerEpisode, dict[str, float]]:
    """One episode simulated with the package's own ballistic integrator."""
    true = _true_params(rng)
    v_leader = _leader_profile(rng)
    v_start = float(v_leader[0])
    gap0 = equilibrium_gap(v_start, true)
    gap_true, v_true = simulate_follower(DT, v_leader, gap0, v_start, true)
    assert float(np.min(gap_true)) > 0.5, "synthetic follower must not collide"
    gap_obs = gap_true + rng.normal(0.0, gap_noise_m, gap_true.shape[0])
    t = np.arange(gap_true.shape[0]) * DT
    ep = LeaderFollowerEpisode(
        veh_id=f"synth-{ep_id}",
        t=t,
        gap_m=gap_obs,
        v_follower=v_true,
        v_leader=v_leader,
        metadata={"dataset": "synthetic", "lane": 1},
    )
    validate_episode(ep)
    return ep, true


def _make_episodes(n: int, seed: int) -> tuple[list[LeaderFollowerEpisode], dict[str, float]]:
    """n episodes plus the empirical mean of the true parameter draws."""
    rng = make_rng(seed)
    episodes, trues = [], []
    for i in range(n):
        ep, true = _synthetic_episode(i, rng)
        episodes.append(ep)
        trues.append(true)
    true_mean = {name: float(np.mean([t[name] for t in trues])) for name in PARAM_ORDER}
    return episodes, true_mean


class TestIdmPrimitives:
    def test_idm_accel_hand_computed(self) -> None:
        # a = a_max (1 - (v/v0)^4 - (s*/s)^2) with s* = s0 + vT (dv = 0).
        params = {"v0": 30.0, "T": 1.0, "a_max": 1.0, "b": 2.0, "s0": 2.0, "delta": 4.0}
        expected = 1.0 * (1.0 - (10.0 / 30.0) ** 4 - (12.0 / 20.0) ** 2)
        assert idm_accel(20.0, 10.0, 0.0, params) == pytest.approx(expected, rel=1e-12)

    def test_equilibrium_gap_closed_form(self) -> None:
        # s_eq = (s0 + vT) / sqrt(1 - (v/v0)^delta)  (CLAUDE.md §9)
        params = dict(IDM_DEFAULTS)
        v = 20.0
        expected = (params["s0"] + v * params["T"]) / sqrt(1.0 - (v / params["v0"]) ** 4)
        assert equilibrium_gap(v, params) == pytest.approx(expected, rel=1e-12)
        # And the IDM acceleration vanishes at that gap.
        assert idm_accel(expected, v, 0.0, params) == pytest.approx(0.0, abs=1e-12)

    def test_equilibrium_is_a_fixed_point_of_the_integrator(self) -> None:
        params = dict(IDM_DEFAULTS)
        v = 15.0
        gap0 = equilibrium_gap(v, params)
        v_leader = np.full(121, v)  # 60 s
        gap_sim, v_sim = simulate_follower(DT, v_leader, gap0, v, params)
        np.testing.assert_allclose(gap_sim, gap0, atol=1e-6)
        np.testing.assert_allclose(v_sim, v, atol=1e-6)

    def test_ballistic_stop_never_goes_negative(self) -> None:
        params = dict(IDM_DEFAULTS)
        # Leader brakes hard to a standstill and stays stopped.
        v_leader = np.concatenate([np.linspace(12.0, 0.0, 21), np.zeros(100)])
        gap0 = equilibrium_gap(12.0, params)
        gap_sim, v_sim = simulate_follower(DT, v_leader, gap0, 12.0, params)
        assert np.all(v_sim >= 0.0)
        assert np.all(gap_sim > 0.0)  # SUMO-grade: IDM must not collide here
        assert v_sim[-1] == pytest.approx(0.0, abs=1e-3)

    def test_simulate_follower_initial_conditions(self) -> None:
        params = dict(IDM_DEFAULTS)
        gap_sim, v_sim = simulate_follower(DT, np.full(10, 5.0), 30.0, 4.0, params)
        assert gap_sim[0] == 30.0
        assert v_sim[0] == 4.0


class TestPopulationRecoveryFast:
    """6-episode variant (CI speed); looser 25% tolerance per the spec."""

    def test_recovers_population_means(self) -> None:
        episodes, true_mean = _make_episodes(n=6, seed=20260829)
        cal = fit_population(
            episodes,
            seed=424242,
            created_at="2026-08-29T00:00:00+00:00",
            source="synthetic IDM truth (fast, 6 episodes)",
            de_maxiter=40,
            de_popsize=12,
            de_tol=0.01,
        )
        for name in ("T", "a_max", "s0"):
            assert cal.mean[name] == pytest.approx(true_mean[name], rel=0.25), (
                f"{name}: fitted {cal.mean[name]:.3f} vs true mean {true_mean[name]:.3f}"
            )
        # Holdout RMSE with population-mean params must be small relative to
        # the gap scale (driver heterogeneity sets the floor: even the true
        # population mean cannot beat the per-driver spread).
        gap_scale = float(np.mean([np.mean(ep.gap_m) for ep in episodes]))
        assert 0.0 <= cal.holdout_gap_rmse_m < 0.15 * gap_scale
        assert cal.n_episodes_fit == 4
        assert cal.n_episodes_holdout == 2
        assert len(cal.per_episode_rmse_m) == 4
        assert len(cal.cov) == 5 and all(len(row) == 5 for row in cal.cov)
        assert all(cal.cov[i][i] >= 0.0 for i in range(5))
        # Per-episode training fits must track the observations closely.
        assert float(np.median(cal.per_episode_rmse_m)) < 1.0

    def test_reproducible_with_same_seed(self) -> None:
        episodes, _ = _make_episodes(n=4, seed=777)
        kwargs = dict(
            created_at="2026-08-29T00:00:00+00:00",
            source="repro",
            holdout_frac=0.0,
            de_maxiter=10,
            de_popsize=8,
            de_tol=0.05,
        )
        a = fit_population(episodes, seed=5, **kwargs)
        b = fit_population(episodes, seed=5, **kwargs)
        assert a.mean == b.mean
        assert a.per_episode_rmse_m == b.per_episode_rmse_m

    def test_too_few_episodes_raise(self) -> None:
        episodes, _ = _make_episodes(n=1, seed=3)
        with pytest.raises(ValueError, match="episodes"):
            fit_population(episodes, seed=1, created_at="t", source="s", holdout_frac=0.0)

    def test_gap_rmse_zero_on_truth_without_noise(self) -> None:
        rng = make_rng(31)
        ep, true = _synthetic_episode(0, rng, gap_noise_m=0.0)
        assert gap_rmse(ep, true) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.slow
class TestPopulationRecoveryFull:
    """40-episode synthetic-truth recovery at the 15% headline tolerance."""

    def test_recovers_population_means(self) -> None:
        episodes, true_mean = _make_episodes(n=40, seed=20260830)
        cal = fit_population(
            episodes,
            seed=515151,
            created_at="2026-08-29T00:00:00+00:00",
            source="synthetic IDM truth (full, 40 episodes)",
            de_maxiter=60,
            de_popsize=15,
            de_tol=0.005,
        )
        for name in ("T", "a_max", "s0"):
            assert cal.mean[name] == pytest.approx(true_mean[name], rel=0.15), (
                f"{name}: fitted {cal.mean[name]:.3f} vs true mean {true_mean[name]:.3f}"
            )
        gap_scale = float(np.mean([np.mean(ep.gap_m) for ep in episodes]))
        assert 0.0 <= cal.holdout_gap_rmse_m < 0.15 * gap_scale
        assert cal.n_episodes_fit == 28
        assert cal.n_episodes_holdout == 12
        assert float(np.median(cal.per_episode_rmse_m)) < 0.5
