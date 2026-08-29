"""Gymnasium hook with the real SUMO backend (CLAUDE.md §4.5 smoke only)."""

import numpy as np
import pytest

from controllers import FlowStateEnv
from flowstate_core.rng import make_rng
from microsim import MicrosimBackend

pytestmark = pytest.mark.integration

K = 6
SEED = 123


@pytest.fixture()
def backend(tmp_path):
    b = MicrosimBackend(n_downstream=K, corridor_length_m=1500.0, episode_s=60.0, workdir=tmp_path)
    yield b
    b.close()


class TestMicrosimBackend:
    def test_reset_returns_contract_obs(self, backend):
        obs = backend.reset(SEED)
        assert len(obs) == 3 + K
        assert all(np.isfinite(obs))
        assert obs[0] >= 0.0 and obs[1] > 0.0  # ego moving-ish, positive gap

    def test_step_before_reset_raises(self, backend):
        with pytest.raises(RuntimeError, match="reset"):
            backend.step(10.0)

    def test_random_policy_episode(self, backend):
        """Random-policy smoke rollout through FlowStateEnv (ADR-2: no training)."""
        env = FlowStateEnv(backend, n_downstream=K, v_max=40.0)
        obs, info = env.reset(seed=SEED)
        assert info["seed"] == SEED
        assert obs.shape == (3 + K,)
        rng = make_rng(5)
        steps, done = 0, False
        while not done and steps < 80:
            action = rng.uniform(env.action_space.low, env.action_space.high)
            obs, reward, done, _truncated, info = env.step(action)
            assert np.all(np.isfinite(obs))
            assert np.isfinite(reward) and reward <= 0.0  # −(fuel + λ·σ_v)
            assert info["fuel"] >= 0.0 and info["sigma_v"] >= 0.0
            steps += 1
        assert done, "episode never terminated"
        assert steps > 5, "episode ended suspiciously early"

    def test_reset_is_reusable(self, backend):
        obs1 = backend.reset(SEED)
        backend.step(15.0)
        obs2 = backend.reset(SEED)  # closes and restarts the singleton
        assert len(obs1) == len(obs2) == 3 + K
