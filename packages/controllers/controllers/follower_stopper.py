"""FollowerStopper wave-dampening controller (Stern et al. 2018, §3.1).

Reference: R. E. Stern et al., "Dissipation of stop-and-go waves via control of
autonomous vehicles: Field experiments", Transportation Research Part C
89:205–221 (2018); arXiv:1705.01693. Constants and equations below were
verified against the arXiv v2 full text on 2026-08-29.

The controller commands the desired (reference) velocity ``U`` whenever safe,
and a suitably lower velocity otherwise, based only on the gap ``Δx`` and the
velocity difference ``Δv = v_leader − v`` (negative arm
``Δv_− = min(Δv, 0)``). The Δx–Δv phase space is split by three parabolas
(paper Eq. (1)):

    Δx_k = Δx_k^0 + (Δv_−)² / (2·d_k),   k = 1, 2, 3

and the commanded velocity is piecewise (paper Eq. (2)):

    v_cmd = 0                                       if Δx ≤ Δx_1   (stopping)
    v_cmd = v·(Δx − Δx_1)/(Δx_2 − Δx_1)             if Δx_1 < Δx ≤ Δx_2
    v_cmd = v + (U − v)·(Δx − Δx_2)/(Δx_3 − Δx_2)   if Δx_2 < Δx ≤ Δx_3
    v_cmd = U                                       if Δx_3 < Δx   (safe)

with ``v = min(max(v_leader, 0), U)``. As implemented in the field experiment
(paper §3.1, text after Eq. (2)): ``Δx_k^0 = (4.5, 5.25, 6.0) m`` and
``d_k = (1.5, 1.0, 0.5) m/s²``; e.g. at Δv = −3 m/s the boundaries evaluate to
(7.5, 9.75, 15) m. ``v_cmd`` is continuous across every region boundary by
construction (verified by unit test).

``U`` is taken from ``obs.v_ref`` — the rolling platoon-mean reference speed
supplied by the runner (docs/CONTRACTS.md §1); the paper sets it from the
average speed of the previous laps/interval.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

from flowstate_core.controller_types import ControllerObs, Memory

FOLLOWER_STOPPER_DEFAULTS: Final[dict[str, float]] = {
    "dx0_1": 4.5,  # region-1 intercept Δx_1^0 [m] (Stern et al. 2018 §3.1)
    "dx0_2": 5.25,  # region-2 intercept Δx_2^0 [m]
    "dx0_3": 6.0,  # region-3 intercept Δx_3^0 [m]
    "d_1": 1.5,  # region-1 curvature / deceleration rate d_1 [m/s²]
    "d_2": 1.0,  # d_2 [m/s²]
    "d_3": 0.5,  # d_3 [m/s²]
}
"""Field-experiment constants from Stern et al. (2018), §3.1."""


def follower_stopper(
    obs: ControllerObs, params: Mapping[str, float], memory: Memory
) -> tuple[float, Memory]:
    """FollowerStopper commanded velocity (Stern et al. 2018, Eqs. (1)–(2)).

    Pure and memoryless: ``memory`` is passed through untouched (copied).

    Args:
        obs: Vehicle observation (SI). ``obs.v_ref`` supplies the desired
            velocity ``U``; ``obs.gap = inf`` / ``obs.v_leader = nan`` denote
            "no leader".
        params: Overrides of ``FOLLOWER_STOPPER_DEFAULTS`` (keys ``dx0_1..3``
            [m], ``d_1..3`` [m/s²]); all d_k must be > 0 and the resulting
            boundaries strictly increasing.
        memory: Controller memory (unused; passed through).

    Returns:
        ``(v_cmd [m/s], new_memory)`` with ``v_cmd ∈ [0, U]``.

    Raises:
        ValueError: If params yield non-increasing region boundaries or a
            non-positive ``d_k``.
    """
    p = {**FOLLOWER_STOPPER_DEFAULTS, **params}
    u = max(obs.v_ref, 0.0)

    if math.isinf(obs.gap) or math.isnan(obs.v_leader):
        # No leader: the safe region extends to infinity — cruise at U.
        return u, dict(memory)

    if p["d_1"] <= 0.0 or p["d_2"] <= 0.0 or p["d_3"] <= 0.0:
        raise ValueError(f"d_k must all be > 0, got {p['d_1']}, {p['d_2']}, {p['d_3']}")

    dv_minus = min(obs.v_leader - obs.v, 0.0)
    dx1 = p["dx0_1"] + dv_minus**2 / (2.0 * p["d_1"])
    dx2 = p["dx0_2"] + dv_minus**2 / (2.0 * p["d_2"])
    dx3 = p["dx0_3"] + dv_minus**2 / (2.0 * p["d_3"])
    if not dx1 < dx2 < dx3:
        raise ValueError(f"region boundaries must increase, got ({dx1}, {dx2}, {dx3})")

    v_lead = min(max(obs.v_leader, 0.0), u)
    gap = obs.gap
    if gap <= dx1:
        v_cmd = 0.0
    elif gap <= dx2:
        v_cmd = v_lead * (gap - dx1) / (dx2 - dx1)
    elif gap <= dx3:
        v_cmd = v_lead + (u - v_lead) * (gap - dx2) / (dx3 - dx2)
    else:
        v_cmd = u
    return v_cmd, dict(memory)
