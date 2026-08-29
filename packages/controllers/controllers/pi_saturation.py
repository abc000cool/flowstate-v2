"""PI-with-saturation wave-dampening controller (CLAUDE.md §4.2).

Lineage: R. E. Stern et al., "Dissipation of stop-and-go waves via control of
autonomous vehicles: Field experiments", Transportation Research Part C
89:205–221 (2018); arXiv:1705.01693, §3.2 ("The PI with saturation
controller", Eqs. (3)–(5)).

Structure implemented here (per CLAUDE.md §4.2, which deliberately simplifies
the paper's field implementation): the target speed is a fixed fraction of the
runner-supplied rolling platoon mean,

    v_target = α · v_ref          (α default 0.75),

and the command is a PI law on the tracking error ``e = v_target − v`` with
target feedforward,

    v_cmd = clamp(v_target + k_p·e + k_i·I, 0, U),   I ← I + e·dt,

where ``U = v_ref`` and ``I`` is the integrator state carried in ``memory``.
Anti-windup is conditional integration (Åström & Murray 2008, §10.4): the
integrator is frozen whenever the unsaturated command is beyond a limit *and*
the error pushes further past that limit — the defect CLAUDE.md §4.2 flags in
the v1 implementation.

Note on fidelity: the paper's field controller (Eqs. (3)–(5)) instead computes
a gap-dependent target ``v_target = U + v_catch·min(max((Δx−g_l)/(g_u−g_l),
0), 1)`` and blends it with the leader speed through gap-scheduled weights
α, β — a PI action with small-gap and large-gap saturation. The spec freezes
the simpler explicit-integrator form above; revisit against the paper when
gains are calibrated on the ring scenario (Phase 1).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from flowstate_core.controller_types import ControllerObs, Memory

PI_SATURATION_DEFAULTS: Final[dict[str, float]] = {
    "alpha": 0.75,  # v_target = alpha * v_ref [-] (CLAUDE.md §4.2)
    "kp": 0.6,  # proportional gain [-] (Phase-1 placeholder, tuned on tests)
    "ki": 0.08,  # integral gain [1/s] (Phase-1 placeholder, tuned on tests)
}
"""Defaults; gains are placeholders until Phase-1 ring calibration."""

_MEM_INTEGRAL: Final[str] = "integral"


def pi_saturation(
    obs: ControllerObs, params: Mapping[str, float], memory: Memory
) -> tuple[float, Memory]:
    """PI speed command with output saturation and conditional anti-windup.

    Args:
        obs: Vehicle observation (SI). ``obs.v_ref`` supplies both the
            saturation ceiling ``U`` and the target ``α·v_ref``; the leader
            state is not used (safety is delegated to the SUMO safety layer,
            CLAUDE.md §3.3).
        params: Overrides of ``PI_SATURATION_DEFAULTS`` (``alpha`` [-],
            ``kp`` [-], ``ki`` [1/s]).
        memory: Carries the integrator state under key ``"integral"``
            [m·s... i.e. ∫e dt in m]; absent key means zero.

    Returns:
        ``(v_cmd [m/s], new_memory)`` with ``v_cmd ∈ [0, U]`` and the updated
        integrator in ``new_memory["integral"]``.
    """
    p = {**PI_SATURATION_DEFAULTS, **params}
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
