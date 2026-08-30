"""PI-with-saturation controller — Stern et al. (2018), Eqs. (3)–(5).

Source: R. E. Stern, S. Cui, M. L. Delle Monache, R. Bhadani, M. Bunting,
M. Churchill, N. Hamilton, R. Haulcy, H. Pohlmann, F. Wu, B. Piccoli,
B. Seibold, J. Sprinkle, D. B. Work, "Dissipation of stop-and-go waves via
control of autonomous vehicles: Field experiments", Transportation Research
Part C 89:205–221 (2018); arXiv:1705.01693 §3.2. Equations and constants below
were read from that text; equation numbers are the paper's.

The controller drives the vehicle at its own long-run average speed, opening a
gap when the leader accelerates and closing it when the leader decelerates —
absorbing a stop-and-go wave rather than transmitting it.

**Desired velocity U.** The paper computes ``U`` as the temporal average of the
*AV's own* velocity over the last ``m`` measurements, choosing ``m`` ≈ 38 s
("approximately the time required to travel one lap around the ring"). Because
``Memory`` is a flat ``dict[str, float]``, this implementation carries an
exponential moving average with time constant ``tau_u`` (default 38 s) instead
of a boxcar window:

    U ← U + (v − U)·dt/tau_u.

The EMA has the same steady state and a comparable effective averaging length;
it is an approximation, documented here rather than hidden. ``obs.v_ref`` is
deliberately **not** used as ``U`` — the paper's ``U`` is the ego vehicle's own
history, not a platoon aggregate.

**Target velocity (Eq. 3).**

    v_target = U + v_catch · min(max((Δx − g_l)/(g_u − g_l), 0), 1)

with ``g_l = 7 m``, ``g_u = 30 m``, ``v_catch = 1 m/s``. The correction is
non-negative and bounded by ``v_catch``: the target is never below ``U``.

**Command update (Eq. 4).**

    v_cmd_{j+1} = β_j·(α_j·v_target_j + (1 − α_j)·v_lead_j) + (1 − β_j)·v_cmd_j

**Gap-scheduled weights (Eq. 5).**

    α = min(max((Δx − Δx_s)/γ, 0), 1),        β = 1 − α/2

with ``γ = 2 m`` and safety distance ``Δx_s = max(2 s · Δv, 4 m)`` (the
two-second rule, floored at 4 m), where ``Δv`` is the closing rate. At small
gaps ``α → 0`` and the command follows the leader (safety saturation); at large
gaps ``α → 1`` and the command tracks the target (catch-up saturation).

**Contrast with the superseded mean-fraction form** (:mod:`controllers.pi_meanfrac`,
the CLAUDE.md §4.2 simplification): that variant targets ``0.75 × platoon mean``,
a multiplicative fraction of a quantity the controller itself depresses, which
compounds into gridlock on open corridors. The formulation here cannot ratchet —
the gap term is additive, non-negative, and referenced to the ego vehicle's own
average — and it defers entirely to the leader when gaps are short.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

from flowstate_core.controller_types import ControllerObs, Memory

PI_SATURATION_DEFAULTS: Final[dict[str, float]] = {
    "v_catch": 1.0,  # max catch-up speed above U [m/s]      (Stern §3.2)
    "g_l": 7.0,  # lower gap limit [m]                        (Stern §3.2)
    "g_u": 30.0,  # upper gap limit [m]                       (Stern §3.2)
    "gamma": 2.0,  # α transition width [m]                   (Stern Eq. 5)
    "dx_s_tau": 2.0,  # two-second rule [s]                   (Stern §3.2)
    "dx_s_min": 4.0,  # safety-distance floor [m]             (Stern §3.2)
    "tau_u": 38.0,  # EMA time constant for U [s]             (Stern §3.2, m ≈ 38 s)
}
"""Literature values from Stern et al. (2018) §3.2; see module docstring."""

_MEM_U: Final[str] = "u_bar"
_MEM_VCMD: Final[str] = "v_cmd_prev"


def pi_saturation(
    obs: ControllerObs, params: Mapping[str, float], memory: Memory
) -> tuple[float, Memory]:
    """Commanded speed from Stern et al. (2018) Eqs. (3)–(5).

    Args:
        obs: Vehicle observation (SI). Uses ``v`` (ego speed), ``gap``
            (``math.inf`` when there is no leader), ``v_leader``
            (``math.nan`` when there is no leader) and ``dt``. ``v_ref`` is
            used only as an upper clamp, never as the paper's ``U``.
        params: Overrides of :data:`PI_SATURATION_DEFAULTS`.
        memory: Carries ``"u_bar"`` (EMA of the ego vehicle's own speed [m/s])
            and ``"v_cmd_prev"`` (previous command [m/s]); absent keys are
            initialised from the current observation.

    Returns:
        ``(v_cmd [m/s], new_memory)``. The command is clamped to
        ``[0, max(v_ref, U + v_catch)]`` so it can never be commanded backwards
        and never exceeds the prevailing reference by more than the paper's
        catch-up allowance.
    """
    p = {**PI_SATURATION_DEFAULTS, **params}
    v = max(obs.v, 0.0)

    # U: EMA of the ego vehicle's OWN speed (paper: 38 s temporal average).
    u_bar = memory.get(_MEM_U, v)
    tau_u = max(p["tau_u"], obs.dt)
    u_bar += (v - u_bar) * (obs.dt / tau_u)

    v_cmd_prev = memory.get(_MEM_VCMD, v)
    has_leader = math.isfinite(obs.gap) and not math.isnan(obs.v_leader)
    gap = obs.gap if has_leader else math.inf
    v_lead = max(obs.v_leader, 0.0) if has_leader else v

    # Eq. (3): target = U + catch-up term, saturating between g_l and g_u.
    span = max(p["g_u"] - p["g_l"], 1e-9)
    catch_frac = min(max((gap - p["g_l"]) / span, 0.0), 1.0) if has_leader else 1.0
    v_target = u_bar + p["v_catch"] * catch_frac

    # Eq. (5): α from the gap relative to the two-second safety distance.
    closing = max(v - v_lead, 0.0)
    dx_s = max(p["dx_s_tau"] * closing, p["dx_s_min"])
    alpha = min(max((gap - dx_s) / max(p["gamma"], 1e-9), 0.0), 1.0) if has_leader else 1.0
    beta = 1.0 - 0.5 * alpha

    # Eq. (4): weighted average of target, leader speed and previous command.
    blended = alpha * v_target + (1.0 - alpha) * v_lead
    raw = beta * blended + (1.0 - beta) * v_cmd_prev

    ceiling = max(max(obs.v_ref, 0.0), u_bar + p["v_catch"])
    v_cmd = min(max(raw, 0.0), ceiling)

    new_memory = dict(memory)
    new_memory[_MEM_U] = u_bar
    new_memory[_MEM_VCMD] = v_cmd
    return v_cmd, new_memory
