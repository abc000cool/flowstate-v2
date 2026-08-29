"""Gymnasium environment hook for RL controllers (CLAUDE.md §4.5, ADR-2).

``FlowStateEnv`` wraps a simulation backend behind the small ``EnvBackend``
protocol so that RL (e.g. PPO via Ray RLlib or CleanRL) can be added later
without refactoring — and without this package importing ``microsim`` (which
would create a circular dependency; ``microsim`` will provide the real backend
and depend on ``controllers``, not vice versa). v2.0 ships the interface and a
random-policy smoke test only; no training code (ADR-2).

Observation (Box, SI): ``(ego speed, gap, leader speed, k downstream mean
speeds)`` [m/s, m, m/s, m/s×k]. Action (Box): target speed command [m/s].
Reward: ``−(fuel + λ·σ_v)`` (CLAUDE.md §4.5 default), where ``fuel`` and
``σ_v`` are per-step costs reported by the backend.

``SyntheticBackend`` is a seeded, deterministic toy backend for the smoke
test. It is **non-physical**: no car-following model, no traffic, arbitrary
fuel/σ_v proxies. Never use it for results.
"""

from __future__ import annotations

import math
from typing import Any, Final, Protocol

import gymnasium as gym
import numpy as np
from numpy.typing import NDArray

from flowstate_core.rng import make_rng

ObsArray = NDArray[np.float64]
ActArray = NDArray[np.float64]


class EnvBackend(Protocol):
    """Minimal simulation backend contract for ``FlowStateEnv``.

    The observation tuple is ``(ego speed [m/s], gap [m], leader speed [m/s],
    *downstream mean speeds [m/s])`` — length ``3 + n_downstream``.
    """

    def reset(self, seed: int) -> tuple[float, ...]:
        """Reset the backend with an explicit seed; return the initial obs tuple."""
        ...

    def step(self, v_cmd: float) -> tuple[tuple[float, ...], float, float, bool]:
        """Advance one control step under speed command ``v_cmd`` [m/s].

        Returns:
            ``(obs tuple, fuel, sigma_v, done)`` — per-step fuel cost,
            per-step speed-dispersion cost σ_v [m/s], episode-end flag.
        """
        ...


class SyntheticBackend:
    """Seeded deterministic toy backend for smoke tests. NON-PHYSICAL.

    A single ego chases a sinusoidally varying "leader" with first-order
    speed dynamics; downstream bins echo a phase-shifted leader speed. Fuel
    and σ_v are dimensionless proxies (idle + speed + accel² penalty;
    |v_leader − v|). Nothing here models traffic — it exists only so
    ``FlowStateEnv`` can be exercised without SUMO.
    """

    _DT: Final[float] = 0.5  # control interval [s]
    _A_MIN: Final[float] = -3.0  # ego decel clip [m/s²]
    _A_MAX: Final[float] = 1.5  # ego accel clip [m/s²]

    def __init__(self, n_downstream: int = 10, episode_steps: int = 200) -> None:
        if n_downstream < 0:
            raise ValueError(f"n_downstream must be >= 0, got {n_downstream}")
        if episode_steps < 1:
            raise ValueError(f"episode_steps must be >= 1, got {episode_steps}")
        self.n_downstream = n_downstream
        self.episode_steps = episode_steps
        self._t = 0.0
        self._k = 0
        self._v = 0.0
        self._gap = 20.0
        self._base = 10.0
        self._amp = 2.0
        self._phase = 0.0

    def _v_leader(self, t: float) -> float:
        return max(0.0, self._base + self._amp * math.sin(0.2 * t + self._phase))

    def _obs(self) -> tuple[float, ...]:
        downstream = tuple(
            max(0.0, self._base + self._amp * math.sin(0.2 * self._t + self._phase + 0.3 * (i + 1)))
            for i in range(self.n_downstream)
        )
        return (self._v, self._gap, self._v_leader(self._t), *downstream)

    def reset(self, seed: int) -> tuple[float, ...]:
        """Seed the leader profile (via ``flowstate_core.rng``) and reset state."""
        rng = make_rng(seed)
        self._base = float(rng.uniform(8.0, 12.0))
        self._amp = float(rng.uniform(1.0, 3.0))
        self._phase = float(rng.uniform(0.0, 2.0 * math.pi))
        self._t = 0.0
        self._k = 0
        self._v = self._v_leader(0.0)
        self._gap = 20.0
        return self._obs()

    def step(self, v_cmd: float) -> tuple[tuple[float, ...], float, float, bool]:
        """Deterministic first-order chase toward ``v_cmd``; proxy costs."""
        accel = min(max((v_cmd - self._v) / self._DT, self._A_MIN), self._A_MAX)
        self._v = max(0.0, self._v + accel * self._DT)
        self._t += self._DT
        self._k += 1
        v_lead = self._v_leader(self._t)
        self._gap = max(0.1, self._gap + (v_lead - self._v) * self._DT)
        fuel = 0.1 + 0.01 * self._v + 0.5 * max(accel, 0.0) ** 2  # proxy, dimensionless
        sigma_v = abs(v_lead - self._v)  # proxy dispersion [m/s]
        done = self._k >= self.episode_steps
        return self._obs(), fuel, sigma_v, done


class FlowStateEnv(gym.Env[ObsArray, ActArray]):
    """Gymnasium wrapper over an ``EnvBackend`` (CLAUDE.md §4.5).

    Args:
        backend: Simulation backend (real ``microsim`` adapter later; use
            ``SyntheticBackend`` for smoke tests only).
        n_downstream: Number of downstream speed bins ``k`` in the
            observation; the backend must return ``3 + k`` values.
        lambda_sigma: Reward weight λ on σ_v [dimensionless]:
            ``reward = −(fuel + λ·σ_v)``.
        v_max: Action-space ceiling for the target-speed command [m/s].
    """

    # metadata: inherited from gym.Env ({"render_modes": []} — no rendering).

    def __init__(
        self,
        backend: EnvBackend,
        n_downstream: int = 10,
        lambda_sigma: float = 1.0,
        v_max: float = 40.0,
    ) -> None:
        super().__init__()
        if v_max <= 0.0:
            raise ValueError(f"v_max must be > 0, got {v_max}")
        self._backend = backend
        self._n_downstream = n_downstream
        self._lambda = lambda_sigma
        self._seed = 0
        self.observation_space = gym.spaces.Box(
            low=0.0, high=np.inf, shape=(3 + n_downstream,), dtype=np.float64
        )
        self.action_space = gym.spaces.Box(low=0.0, high=v_max, shape=(1,), dtype=np.float64)

    def _to_array(self, obs: tuple[float, ...]) -> ObsArray:
        if len(obs) != 3 + self._n_downstream:
            raise ValueError(
                f"backend returned obs of length {len(obs)}, expected {3 + self._n_downstream}"
            )
        return np.asarray(obs, dtype=np.float64)

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[ObsArray, dict[str, Any]]:
        """Reset backend with an explicit seed (last seed reused when None)."""
        super().reset(seed=seed)
        if seed is not None:
            self._seed = seed
        obs = self._backend.reset(self._seed)
        return self._to_array(obs), {"seed": self._seed}

    def step(self, action: ActArray) -> tuple[ObsArray, float, bool, bool, dict[str, Any]]:
        """Apply the target-speed command; reward = −(fuel + λ·σ_v)."""
        v_cmd = float(np.asarray(action, dtype=np.float64).reshape(-1)[0])
        obs, fuel, sigma_v, done = self._backend.step(v_cmd)
        reward = -(fuel + self._lambda * sigma_v)
        info: dict[str, Any] = {"fuel": fuel, "sigma_v": sigma_v}
        return self._to_array(obs), float(reward), bool(done), False, info
