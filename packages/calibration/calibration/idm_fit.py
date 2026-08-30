"""IDM calibration from leader-follower episodes (CLAUDE.md §6.2).

Per-episode calibration follows the trajectory-calibration methodology of
Kesting & Treiber (2008, "Calibrating car-following models by using trajectory
data: methodological study") and Treiber & Kesting (*Traffic Flow Dynamics*,
2013): the follower is *re-simulated* through the recorded leader speed
profile and the parameters minimize the **gap** RMSE — gap-based objectives
identify (T, a_max, s0) far better than speed- or acceleration-based ones
(CLAUDE.md §6.2). The gap obeys ``ds/dt = v_leader − v_follower`` with the
follower integrated by the standard ballistic update (Treiber & Kesting,
ch. 10.2: piecewise-constant acceleration over each step, exact stop handling
so speeds never go negative).

Optimization: ``scipy.optimize.differential_evolution`` (global, seeded) over
(v0, T, a_max, b, s0) with bounds from ``flowstate_core.constants.IDM_RANGES``
and the acceleration exponent δ fixed at 4 (CLAUDE.md §3.1).

Population fit: per-episode parameter vectors are trimmed of poorly-fitted
outliers (episodes with gap RMSE above a quantile — weakly excited episodes
yield unidentifiable, extreme estimates; documented robustness choice), then
summarized as mean + covariance in the contract order (v0, T, a_max, b, s0).
Holdout honesty (CLAUDE.md §6.2): fitting uses 70% of episodes; the artifact
reports the gap RMSE of the *population-mean* parameters re-simulated on the
held-out 30%.
"""

from __future__ import annotations

import hashlib
import multiprocessing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import sqrt
from typing import Any

import numpy as np
from scipy.optimize import differential_evolution

from calibration.episodes import LeaderFollowerEpisode
from flowstate_core.artifacts import IDMCalibration
from flowstate_core.constants import IDM_DEFAULTS, IDM_RANGES
from flowstate_core.rng import make_rng, spawn_seeds

PARAM_ORDER: tuple[str, str, str, str, str] = ("v0", "T", "a_max", "b", "s0")
"""Calibrated-parameter order — matches ``IDMCalibration`` (CONTRACTS §5)."""

DELTA_FIXED: float = IDM_DEFAULTS["delta"]
"""Acceleration exponent δ, fixed at 4 (CLAUDE.md §3.1) — not calibrated."""

_S_MIN_M = 1e-3
"""Gap floor [m] used only inside the IDM interaction term to avoid a
division blow-up; a gap this small is already a collision (penalized)."""

_COLLISION_PENALTY = 1e3
"""Objective penalty added when the simulated follower collides."""


def idm_accel(s: float, v: float, dv: float, params: Mapping[str, float]) -> float:
    """IDM acceleration [m/s²] (Treiber, Hennecke & Helbing 2000).

    ``a = a_max·[1 − (v/v0)^δ − (s*/s)²]`` with
    ``s* = s0 + max(0, v·T + v·Δv/(2√(a_max·b)))`` (CLAUDE.md §3.1).

    Args:
        s: Bumper-to-bumper gap [m] (floored at 1 mm internally).
        v: Follower speed [m/s].
        dv: Approach rate ``v − v_leader`` [m/s].
        params: Mapping with v0, T, a_max, b, s0 (and optionally delta).
    """
    a_max = params["a_max"]
    delta = params.get("delta", DELTA_FIXED)
    s_star = params["s0"] + max(0.0, v * params["T"] + v * dv / (2.0 * sqrt(a_max * params["b"])))
    s_eff = s if s > _S_MIN_M else _S_MIN_M
    return a_max * (1.0 - (v / params["v0"]) ** delta - (s_star / s_eff) ** 2)


def equilibrium_gap(v: float, params: Mapping[str, float]) -> float:
    """Steady-state IDM gap ``s_eq = (s0 + v·T)/√(1 − (v/v0)^δ)`` [m].

    Closed form from setting ``a = 0`` and ``Δv = 0`` in the IDM
    (CLAUDE.md §9). Requires ``v < v0``.
    """
    v0 = params["v0"]
    if not 0.0 <= v < v0:
        raise ValueError(f"equilibrium gap needs 0 <= v < v0, got v={v}, v0={v0}")
    delta = params.get("delta", DELTA_FIXED)
    return (params["s0"] + v * params["T"]) / sqrt(1.0 - (v / v0) ** delta)


def simulate_follower(
    dt: float,
    v_leader: np.ndarray,
    gap0: float,
    v_follower0: float,
    params: Mapping[str, float],
) -> tuple[np.ndarray, np.ndarray]:
    """Re-simulate a follower through a recorded leader speed profile.

    Ballistic update per step (Treiber & Kesting 2013, ch. 10.2): the IDM
    acceleration is held constant over the step, so the follower advances
    ``v·dt + a·dt²/2``; if it would cross zero speed it stops exactly
    (travel ``v²/(2|a|)``, speed clamped at 0 — no negative speeds). The gap
    integrates ``ds/dt = v_leader − v_follower`` with the recorded leader
    speed trapezoidally: ``s_{k+1} = s_k + ½(v_l[k]+v_l[k+1])·dt − Δx_f``.

    Args:
        dt: Uniform time step [s].
        v_leader: Recorded leader speeds [m/s], length n.
        gap0: Initial bumper-to-bumper gap [m].
        v_follower0: Initial follower speed [m/s].
        params: IDM parameters (v0, T, a_max, b, s0; delta optional, fixed 4).

    Returns:
        ``(gap_sim, v_sim)`` — arrays of length n; ``gap_sim[0] == gap0``.
        A non-positive value in ``gap_sim`` marks a (virtual) collision.
    """
    vl = np.asarray(v_leader, dtype=float).tolist()
    n = len(vl)
    v0 = params["v0"]
    t_headway = params["T"]
    a_max = params["a_max"]
    b = params["b"]
    s0 = params["s0"]
    delta = params.get("delta", DELTA_FIXED)
    two_sqrt_ab = 2.0 * sqrt(a_max * b)

    gaps = [0.0] * n
    speeds = [0.0] * n
    s = float(gap0)
    v = max(0.0, float(v_follower0))
    gaps[0] = s
    speeds[0] = v
    for k in range(n - 1):
        dv = v - vl[k]
        s_eff = s if s > _S_MIN_M else _S_MIN_M
        s_star = s0 + max(0.0, v * t_headway + v * dv / two_sqrt_ab)
        a = a_max * (1.0 - (v / v0) ** delta - (s_star / s_eff) ** 2)
        v_next = v + a * dt
        if v_next >= 0.0:
            x_travel = v * dt + 0.5 * a * dt * dt
        else:  # ballistic stop within the step
            x_travel = 0.5 * v * v / -a if a < 0.0 else 0.0
            v_next = 0.0
        s = s + 0.5 * (vl[k] + vl[k + 1]) * dt - x_travel
        v = v_next
        gaps[k + 1] = s
        speeds[k + 1] = v
    return np.asarray(gaps), np.asarray(speeds)


def gap_rmse(episode: LeaderFollowerEpisode, params: Mapping[str, float]) -> float:
    """Gap RMSE [m] of a re-simulated episode (no collision penalty)."""
    gap_sim, _ = simulate_follower(
        episode.dt, episode.v_leader, float(episode.gap_m[0]), float(episode.v_follower[0]), params
    )
    return float(np.sqrt(np.mean((gap_sim - episode.gap_m) ** 2)))


@dataclass(frozen=True)
class EpisodeFit:
    """Result of one per-episode IDM calibration."""

    veh_id: str
    params: dict[str, float]
    """Fitted v0, T, a_max, b, s0 (delta fixed, not included)."""
    gap_rmse_m: float
    n_samples: int


def fit_episode(
    episode: LeaderFollowerEpisode,
    *,
    seed: int,
    bounds: Mapping[str, tuple[float, float]] = IDM_RANGES,
    de_maxiter: int = 60,
    de_popsize: int = 15,
    de_tol: float = 0.005,
) -> EpisodeFit:
    """Calibrate IDM parameters on one episode by minimizing gap RMSE.

    Global optimization via seeded ``differential_evolution`` over
    (v0, T, a_max, b, s0) within ``bounds`` (δ fixed at 4), objective =
    RMSE(simulated gap, recorded gap) plus a large penalty when the simulated
    follower collides (gap ≤ 0) — gap-based objective per Kesting & Treiber
    (2008).

    Args:
        episode: Validated leader-follower episode.
        seed: Explicit RNG seed for the optimizer (``flowstate_core.rng``).
        bounds: Per-parameter (low, high) search bounds, contract order keys.
        de_maxiter: DE generation cap.
        de_popsize: DE population multiplier.
        de_tol: DE convergence tolerance.

    Returns:
        :class:`EpisodeFit` with the fitted parameters (polished by L-BFGS-B)
        and the *pure* gap RMSE (no penalty) at the optimum.
    """
    dt = episode.dt
    v_leader = episode.v_leader
    gap_obs = episode.gap_m
    gap0 = float(gap_obs[0])
    v_f0 = float(episode.v_follower[0])

    def objective(theta: np.ndarray) -> float:
        params = dict(zip(PARAM_ORDER, theta, strict=True))
        gap_sim, _ = simulate_follower(dt, v_leader, gap0, v_f0, params)
        rmse = float(np.sqrt(np.mean((gap_sim - gap_obs) ** 2)))
        min_gap = float(np.min(gap_sim))
        if min_gap <= 0.0:
            rmse += _COLLISION_PENALTY * (1.0 + abs(min_gap))
        return rmse

    result = differential_evolution(
        objective,
        bounds=[bounds[name] for name in PARAM_ORDER],
        seed=make_rng(seed),
        maxiter=de_maxiter,
        popsize=de_popsize,
        tol=de_tol,
        polish=True,
    )
    fitted = dict(zip(PARAM_ORDER, (float(x) for x in result.x), strict=True))
    return EpisodeFit(
        veh_id=episode.veh_id,
        params=fitted,
        gap_rmse_m=gap_rmse(episode, fitted),
        n_samples=episode.n,
    )


def _fit_episode_worker(payload: tuple[LeaderFollowerEpisode, int, dict[str, Any]]) -> EpisodeFit:
    """Process-pool worker: unpack ``(episode, seed, kwargs)`` → :func:`fit_episode`."""
    episode, ep_seed, kwargs = payload
    return fit_episode(episode, seed=ep_seed, **kwargs)


def fit_population(
    episodes: Sequence[LeaderFollowerEpisode],
    *,
    seed: int,
    created_at: str,
    source: str,
    data_hash: str | None = None,
    holdout_frac: float = 0.3,
    trim_quantile: float = 0.9,
    bounds: Mapping[str, tuple[float, float]] = IDM_RANGES,
    de_maxiter: int = 60,
    de_popsize: int = 15,
    de_tol: float = 0.005,
    n_procs: int | None = None,
    notes: str = "",
) -> IDMCalibration:
    """Fit the population distribution of IDM parameters (CLAUDE.md §6.2).

    Pipeline: (1) seeded shuffle, hold out ``holdout_frac`` of the episodes
    (at least one when the fraction is positive); (2) per-episode calibration
    on the training episodes via :func:`fit_episode` (independent spawned
    seeds); (3) robustness trim — episodes whose fit RMSE exceeds the
    ``trim_quantile`` quantile of training RMSEs are excluded from the
    population statistics (poorly excited episodes produce unidentifiable,
    extreme parameter estimates; the trim is documented in the artifact
    notes); (4) population mean and covariance over the kept parameter
    vectors, order (v0, T, a_max, b, s0); (5) holdout validation — each
    held-out episode is re-simulated with the *population-mean* parameters
    and ``holdout_gap_rmse_m`` is the mean of the per-episode gap RMSEs.

    Args:
        episodes: Validated episodes (>= 2).
        seed: Master seed; per-episode optimizer seeds are spawned from it.
        created_at: ISO-8601 timestamp (caller-supplied, CONTRACTS §5).
        source: Human-readable data provenance.
        data_hash: Hash of the input data; computed from episodes when None.
        holdout_frac: Fraction of episodes held out (0 disables holdout and
            reports ``holdout_gap_rmse_m = nan``).
        trim_quantile: RMSE quantile above which training fits are trimmed
            from the population statistics (1.0 disables trimming).
        bounds: Search bounds per parameter.
        de_maxiter: DE generation cap per episode.
        de_popsize: DE population multiplier.
        de_tol: DE convergence tolerance.
        n_procs: Per-episode fits run in a process pool of this size when
            > 1 (episodes are independent; each carries its own spawned
            seed, so results are identical to the serial path). ``None``
            or 1 keeps the serial path.
        notes: Free-text note prepended to the artifact notes.

    Returns:
        ``IDMCalibration`` artifact. ``per_episode_rmse_m`` holds the
        *training* fit RMSEs (pre-trim, in fit order).

    Raises:
        ValueError: With fewer than 2 episodes or invalid fractions.
    """
    n = len(episodes)
    if n < 2:
        raise ValueError(f"need >= 2 episodes, got {n}")
    if not 0.0 <= holdout_frac < 1.0:
        raise ValueError(f"holdout_frac must be in [0, 1), got {holdout_frac}")
    if not 0.0 < trim_quantile <= 1.0:
        raise ValueError(f"trim_quantile must be in (0, 1], got {trim_quantile}")

    rng = make_rng(seed)
    perm = rng.permutation(n)
    n_hold = max(1, round(holdout_frac * n)) if holdout_frac > 0 else 0
    if n - n_hold < 2:
        raise ValueError(f"holdout leaves {n - n_hold} training episodes (< 2)")
    holdout = [episodes[i] for i in perm[:n_hold]]
    train = [episodes[i] for i in perm[n_hold:]]

    fit_seeds = spawn_seeds(seed, len(train))
    fit_kwargs: dict[str, Any] = {
        "bounds": dict(bounds),
        "de_maxiter": de_maxiter,
        "de_popsize": de_popsize,
        "de_tol": de_tol,
    }
    if n_procs is not None and n_procs > 1:
        payloads = [(ep, s, fit_kwargs) for ep, s in zip(train, fit_seeds, strict=True)]
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(processes=min(n_procs, len(train))) as pool:
            fits = pool.map(_fit_episode_worker, payloads)
    else:
        fits = [
            fit_episode(ep, seed=s, **fit_kwargs) for ep, s in zip(train, fit_seeds, strict=True)
        ]

    rmses = np.array([f.gap_rmse_m for f in fits])
    if trim_quantile < 1.0:
        cutoff = float(np.quantile(rmses, trim_quantile))
        kept = [f for f in fits if f.gap_rmse_m <= cutoff]
    else:
        kept = list(fits)
    theta = np.array([[f.params[name] for name in PARAM_ORDER] for f in kept])
    mean = {name: float(m) for name, m in zip(PARAM_ORDER, theta.mean(axis=0), strict=True)}
    if theta.shape[0] >= 2:
        cov = np.atleast_2d(np.cov(theta, rowvar=False))
    else:
        cov = np.zeros((len(PARAM_ORDER), len(PARAM_ORDER)))

    if holdout:
        mean_params = dict(mean)
        holdout_rmse = float(np.mean([gap_rmse(ep, mean_params) for ep in holdout]))
    else:
        holdout_rmse = float("nan")

    trim_note = (
        f"trimmed {len(fits) - len(kept)}/{len(fits)} training fits above the "
        f"q={trim_quantile} RMSE quantile before population statistics."
    )
    return IDMCalibration(
        created_at=created_at,
        source=source,
        data_hash=data_hash if data_hash is not None else _hash_episodes(episodes),
        mean=mean,
        cov=[[float(c) for c in row] for row in cov],
        n_episodes_fit=len(train),
        n_episodes_holdout=len(holdout),
        holdout_gap_rmse_m=holdout_rmse,
        per_episode_rmse_m=[float(r) for r in rmses],
        notes=(notes + " " if notes else "")
        + f"delta fixed at {DELTA_FIXED}; gap-RMSE objective; seed {seed}. "
        + trim_note,
    )


def _hash_episodes(episodes: Sequence[LeaderFollowerEpisode]) -> str:
    """Deterministic sha256 digest over episode contents."""
    h = hashlib.sha256()
    for ep in episodes:
        h.update(ep.veh_id.encode())
        for arr in (ep.t, ep.gap_m, ep.v_follower, ep.v_leader):
            h.update(np.ascontiguousarray(arr, dtype=float).tobytes())
    return h.hexdigest()
