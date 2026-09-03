"""FlowStateEnv smoke tests with the (non-physical) SyntheticBackend.

Random-policy smoke test only per CLAUDE.md §4.5 / ADR-2 — no training code.
The scenario-backed constructor (real ``microsim`` backend, SUMO) is
exercised in ``tests/test_microsim/test_microsim_gym_backend.py``.
"""

import numpy as np
import pytest

from controllers import FlowStateEnv, SyntheticBackend
from controllers.gym_env import DEFAULT_EPISODE_S, DEFAULT_SCENARIO
from flowstate_core.rng import make_rng

SEED = 20260829
K = 6


def _env(lambda_sigma: float = 1.0) -> FlowStateEnv:
    return FlowStateEnv(
        SyntheticBackend(n_downstream=K, episode_steps=50),
        n_downstream=K,
        lambda_sigma=lambda_sigma,
    )


class TestSpaces:
    def test_observation_and_action_spaces(self):
        env = _env()
        assert env.observation_space.shape == (3 + K,)
        assert env.action_space.shape == (1,)
        assert float(env.action_space.low[0]) == 0.0

    def test_reset_returns_valid_observation(self):
        env = _env()
        obs, info = env.reset(seed=SEED)
        assert obs.shape == (3 + K,)
        assert env.observation_space.contains(obs)
        assert info["seed"] == SEED


class TestRandomPolicySmoke:
    def test_seeded_random_rollout(self):
        env = _env(lambda_sigma=2.0)
        obs, _ = env.reset(seed=SEED)
        rng = make_rng(7)
        terminated = False
        steps = 0
        while not terminated:
            action = rng.uniform(env.action_space.low, env.action_space.high)
            obs, reward, terminated, truncated, info = env.step(action)
            steps += 1
            assert np.all(np.isfinite(obs))
            assert env.observation_space.contains(obs)
            assert np.isfinite(reward)
            # reward contract: −(fuel + λ·σ_v), both costs non-negative
            assert reward == pytest.approx(-(info["fuel"] + 2.0 * info["sigma_v"]))
            assert reward <= 0.0
            assert truncated is False
            assert steps <= 50
        assert steps == 50

    def test_same_seed_same_trajectory(self):
        def rollout() -> list[float]:
            env = _env()
            env.reset(seed=SEED)
            rng = make_rng(3)
            rewards = []
            for _ in range(20):
                action = rng.uniform(env.action_space.low, env.action_space.high)
                _, reward, _, _, _ = env.step(action)
                rewards.append(reward)
            return rewards

        assert rollout() == rollout()

    def test_different_seed_different_start(self):
        env = _env()
        obs_a, _ = env.reset(seed=1)
        obs_b, _ = env.reset(seed=2)
        assert not np.allclose(obs_a, obs_b)

    def test_reset_without_seed_reuses_last_seed(self):
        env = _env()
        obs_a, _ = env.reset(seed=SEED)
        env.step(np.array([5.0]))
        obs_b, info = env.reset()
        assert info["seed"] == SEED
        np.testing.assert_allclose(obs_a, obs_b)


class TestBackendValidation:
    def test_wrong_obs_length_raises(self):
        env = FlowStateEnv(SyntheticBackend(n_downstream=2), n_downstream=5)
        with pytest.raises(ValueError, match="length"):
            env.reset(seed=SEED)

    def test_bad_construction_raises(self):
        with pytest.raises(ValueError):
            SyntheticBackend(n_downstream=-1)
        with pytest.raises(ValueError):
            SyntheticBackend(episode_steps=0)
        with pytest.raises(ValueError):
            FlowStateEnv(SyntheticBackend(), v_max=0.0)
        with pytest.raises(ValueError):
            FlowStateEnv(SyntheticBackend(n_downstream=0), n_downstream=-1)


class TestScenarioConstruction:
    """The scenario path without SUMO: argument rules and the defaults it names."""

    def test_backend_and_scenario_are_mutually_exclusive(self):
        with pytest.raises(ValueError, match="either"):
            FlowStateEnv(SyntheticBackend(n_downstream=K), n_downstream=K, scenario="corridor_10km")

    def test_explicit_backend_has_no_scenario(self):
        env = _env()
        assert env.scenario is None

    def test_defaults_name_the_spec_scenario(self):
        # CLAUDE.md §4.5: the hook wraps corridor_10km; the episode is short.
        assert DEFAULT_SCENARIO == "corridor_10km"
        assert 0.0 < DEFAULT_EPISODE_S <= 60.0

    def test_close_is_a_no_op_on_the_synthetic_backend(self):
        env = _env()
        env.reset(seed=SEED)
        env.close()
        obs, _ = env.reset(seed=SEED)  # still usable afterwards
        assert env.observation_space.contains(obs)
