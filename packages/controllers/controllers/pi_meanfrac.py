"""Mean-fraction PI controller — the CLAUDE.md §4.2 simplification (SUPERSEDED).

**This is not the Stern et al. (2018) PI-with-saturation controller.** It is the
simplified form CLAUDE.md §4.2 specified: a PI law driving the ego speed toward
a fixed fraction ``α`` of the runner-supplied rolling *platoon* mean,

    v_target = α · v_ref          (α default 0.75),
    v_cmd    = clamp(v_target + k_p·e + k_i·I, 0, U),   e = v_target − v.

It is retained because the M3 sweep (docs/M3_RESULTS.md) tested it and because
its failure mode is a documented result — but it must not be presented as the
literature controller. Use :mod:`controllers.pi_saturation` for the faithful
implementation of Stern et al. (2018) Eqs. (3)–(5).

**Why it fails on open corridors** (M3, 540 runs): the target is a *multiplicative*
fraction of a quantity the controller itself depresses. The AV drives at 0.75× the
platoon mean, which drags the mean down, which lowers the target, which slows the
AV further — a geometric ratchet with no floor. On a closed ring the fixed vehicle
count and periodic boundary arrest this; on an open corridor it runs to standstill
(94% throughput collapse at 5% penetration, throughput 79 [29, 130] veh/h).
The paper has no such factor: its target is U *plus* a non-negative gap-dependent
offset, so the correction cannot compound downward (see pi_saturation.py).

Anti-windup is conditional integration (Åström & Murray 2008, §10.4).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from flowstate_core.controller_types import ControllerObs, Memory

PI_MEANFRAC_DEFAULTS: Final[dict[str, float]] = {
    "alpha": 0.75,  # v_target = alpha * v_ref [-] (CLAUDE.md §4.2)
    "kp": 0.6,  # proportional gain [-] (Phase-1 placeholder, tuned on tests)
    "ki": 0.08,  # integral gain [1/s] (Phase-1 placeholder, tuned on tests)
}
"""Defaults; gains are placeholders until Phase-1 ring calibration."""

_MEM_INTEGRAL: Final[str] = "integral"


def pi_meanfrac(
    obs: ControllerObs, params: Mapping[str, float], memory: Memory
) -> tuple[float, Memory]:
    """PI speed command with output saturation and conditional anti-windup.

    Args:
        obs: Vehicle observation (SI). ``obs.v_ref`` supplies both the
            saturation ceiling ``U`` and the target ``α·v_ref``; the leader
            state is not used (safety is delegated to the SUMO safety layer,
            CLAUDE.md §3.3).
        params: Overrides of ``PI_MEANFRAC_DEFAULTS`` (``alpha`` [-],
            ``kp`` [-], ``ki`` [1/s]).
        memory: Carries the integrator state under key ``"integral"``
            [m·s... i.e. ∫e dt in m]; absent key means zero.

    Returns:
        ``(v_cmd [m/s], new_memory)`` with ``v_cmd ∈ [0, U]`` and the updated
        integrator in ``new_memory["integral"]``.
    """
    p = {**PI_MEANFRAC_DEFAULTS, **params}
    u = max(obs.v_ref, 0.0)
    v_target = p["alpha"] * u
    error = v_target - obs.v
    integral = memory.get(_MEM_INTEGRAL, 0.0)

    raw = v_target + p["kp"] * error + p["ki"] * integral
    v_cmd = min(max(raw, 0.0), u)

    # Conditional integration: freeze the integrator while the unsaturated
    # command sits past a limit and the error would drive it further past.
    pushing_high = raw > u and error > 0.0
    pushing_low = raw < 0.0 and error < 0.0
    if not (pushing_high or pushing_low):
        integral += error * obs.dt

    new_memory = dict(memory)
    new_memory[_MEM_INTEGRAL] = integral
    return v_cmd, new_memory
