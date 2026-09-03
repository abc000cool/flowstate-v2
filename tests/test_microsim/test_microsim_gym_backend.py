"""Gymnasium hook with the real SUMO backend (CLAUDE.md §4.5 smoke only)."""

import numpy as np
import pytest

from controllers import FlowStateEnv
from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import make_rng
from microsim import MicrosimBackend, load_scenario
from microsim.runner import CORRIDOR_INSERTION_BUFFER_M

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

    @pytest.mark.parametrize(
        "kwargs, needle",
        [
            ({"n_downstream": -1}, "n_downstream"),
            ({"episode_s": 0.0}, "episode_s"),
            ({"ego_depart_s": 60.0}, "ego_depart_s"),
            ({"lanes": 0}, "lanes"),
            ({"inflow": [(5.0, 0.2), (0.0, 0.3)]}, "ordered"),
            ({"inflow": [(0.0, -0.2)]}, ">= 0"),
        ],
    )
    def test_bad_construction_raises(self, tmp_path, kwargs, needle):
        params = {"n_downstream": K, "episode_s": 60.0, "workdir": tmp_path}
        params.update(kwargs)
        with pytest.raises(ValueError, match=needle):
            MicrosimBackend(**params)


class TestFromScenario:
    """The §4.5 hook on the versioned scenario config (no SUMO until reset)."""

    def test_takes_parameters_from_the_scenario(self, tmp_path):
        cfg = load_scenario("corridor_10km")
        b = MicrosimBackend.from_scenario(cfg, episode_s=45.0, workdir=tmp_path, n_downstream=K)
        assert b.corridor_length_m == cfg.network.length_m
        assert b.lanes == cfg.network.lanes
        assert b.inflow_steps == tuple(cfg.network.inflow)
        assert b.inflow_veh_s == cfg.network.inflow[0][1]
        assert b.fleet == cfg.fleet
        assert b.step_length_s == cfg.sim.step_length_s
        assert b.action_step_s == cfg.sim.action_step_s
        assert b.entry_m == min(CORRIDOR_INSERTION_BUFFER_M, cfg.network.length_m)
        assert b.episode_s == 45.0 and b.n_downstream == K
        assert b.scenario_name == "corridor_10km"
        assert b.scenario_hash == config_hash(cfg)

    def test_rejects_non_corridor_networks(self):
        with pytest.raises(ValueError, match="corridor network"):
            MicrosimBackend.from_scenario(load_scenario("ring_sugiyama"), episode_s=30.0)

    @pytest.mark.parametrize(
        "patch, needle",
        [
            ({"av": {"penetration": 0.1, "controller": "follower_stopper"}}, "controlled"),
            ({"av": {"vsl": "vsl_threshold"}}, "controlled"),
            (
                {"perturbation": {"t_s": 10, "position_m": 100, "duration_s": 5, "v_drop_ms": 5}},
                "perturbation",
            ),
            ({"network": {"boundary": {"steps": [[0.0, 5.0]]}}}, "boundary"),
        ],
    )
    def test_rejects_blocks_the_backend_cannot_honor(self, patch, needle):
        raw = load_scenario("corridor_10km").model_dump(mode="json")
        for key, value in patch.items():
            raw[key] = {**(raw[key] or {}), **value}  # corridor_10km has perturbation: null
        cfg = ScenarioConfig.model_validate(raw)
        with pytest.raises(ValueError, match=needle):
            MicrosimBackend.from_scenario(cfg, episode_s=30.0)

    def test_rejects_inconsistent_timing(self):
        cfg = load_scenario("corridor_10km")
        with pytest.raises(ValueError, match="duration"):
            MicrosimBackend.from_scenario(cfg, episode_s=cfg.sim.duration_s + 1.0)
        with pytest.raises(ValueError, match="ego_depart_s"):
            MicrosimBackend.from_scenario(cfg, episode_s=30.0, ego_depart_s=30.0)

    def test_env_from_corridor_10km_random_policy(self, tmp_path):
        """FlowStateEnv on the versioned corridor_10km scenario, ≤ 60 s episode."""
        env = FlowStateEnv(n_downstream=K, episode_s=45.0, ego_depart_s=10.0, workdir=tmp_path)
        try:
            assert env.scenario is not None and env.scenario.name == "corridor_10km"
            assert config_hash(env.scenario) == config_hash(load_scenario("corridor_10km"))
            obs, info = env.reset(seed=SEED)
            assert info["seed"] == SEED and obs.shape == (3 + K,)
            assert env.observation_space.contains(obs)
            rng = make_rng(11)
            steps, done = 0, False
            while not done and steps < 200:
                action = rng.uniform(env.action_space.low, env.action_space.high)
                obs, reward, done, truncated, info = env.step(action)
                assert np.all(np.isfinite(obs)) and env.observation_space.contains(obs)
                assert np.isfinite(reward) and reward <= 0.0
                assert info["fuel"] >= 0.0 and info["sigma_v"] >= 0.0
                assert truncated is False
                steps += 1
            assert done, "episode never terminated"
            # 45 s horizon, 10 s ego departure, 0.5 s action steps → ~70 steps.
            assert 50 <= steps <= 80
        finally:
            env.close()
