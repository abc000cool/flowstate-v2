"""Capacity-aware FollowerStopper: smoothing that does not hold a hole open.

FollowerStopper (Stern et al. 2018) holds a gap that grows with the approach
rate; on a corridor near capacity that held gap is a moving capacity drop,
which is what the I-24 flagship sweep measured (docs/I24_SWEEP.md: −36%
throughput at 5% penetration). This variant keeps the FollowerStopper law
inside the gap it is designed for and adds one rule for large gaps: when the
gap exceeds the time-headway cap ``g_max = g0 + h_max · v``, the command is
released toward the leader's speed so the controlled vehicle stops holding
traffic back. The two regimes are blended linearly over ``blend_m`` to keep
the command continuous in the gap::

    v_fs   = FollowerStopper(obs)
    v_free = max(v_fs, min(v_leader, v_ref))          # follow the leader, not U
    w      = clip((gap − g_max) / blend_m, 0, 1)
    v_cmd  = (1 − w) · v_fs + w · v_free

``h_max`` is the largest time headway the controller will keep on purpose;
the fleet's calibrated headway (1.3–1.5 s on I-24) is the natural scale and
2.0 s the default. With ``v_leader ≤ v_ref`` the output stays within
``[0, U]`` like the original; with no leader it cruises at ``U``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

from controllers.follower_stopper import FOLLOWER_STOPPER_DEFAULTS, follower_stopper
from flowstate_core.controller_types import ControllerObs, Memory

FOLLOWER_STOPPER_CAPACITY_DEFAULTS: Final[dict[str, float]] = {
    **FOLLOWER_STOPPER_DEFAULTS,
    "h_max_s": 2.0,  # largest time headway held on purpose [s]
    "g0_m": 4.0,  # standstill part of the headway cap [m]
    "blend_m": 5.0,  # gap range over which the release blends in [m]
}
"""FollowerStopper constants plus the headway cap (module docstring)."""


def follower_stopper_capacity(
    obs: ControllerObs, params: Mapping[str, float], memory: Memory
) -> tuple[float, Memory]:
    """FollowerStopper command with a time-headway cap (module docstring).

    Args:
        obs: Vehicle observation (SI); ``obs.v_ref`` is ``U``.
        params: Overrides of ``FOLLOWER_STOPPER_CAPACITY_DEFAULTS``.
        memory: Passed through untouched (copied).

    Returns:
        ``(v_cmd [m/s], new_memory)``.

    Raises:
        ValueError: Non-positive ``h_max_s`` or ``blend_m`` (FollowerStopper's
            own parameter checks apply too).
    """
    p = {**FOLLOWER_STOPPER_CAPACITY_DEFAULTS, **params}
    if p["h_max_s"] <= 0.0 or p["blend_m"] <= 0.0:
        raise ValueError("h_max_s and blend_m must be > 0")
    v_fs, mem = follower_stopper(obs, p, memory)
    if math.isinf(obs.gap) or math.isnan(obs.v_leader):
        return v_fs, mem
    u = max(obs.v_ref, 0.0)
    v_free = max(v_fs, min(max(obs.v_leader, 0.0), u))
    g_max = p["g0_m"] + p["h_max_s"] * max(obs.v, 0.0)
    w = min(max((obs.gap - g_max) / p["blend_m"], 0.0), 1.0)
    return (1.0 - w) * v_fs + w * v_free, mem
