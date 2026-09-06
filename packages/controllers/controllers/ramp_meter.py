"""ALINEA ramp metering (Papageorgiou, Hadj-Salem & Blosseville 1991).

ALINEA is the local feedback ramp-metering law used on most metered freeways
in Europe and, in its occupancy form, on many US installations::

    r(k) = r(k-1) + K_R · (ô − o_out(k))                    (1)

with ``r`` the metering rate [veh/h], ``o_out`` the occupancy measured just
downstream of the merge and ``ô`` its target (the critical occupancy at
capacity). This implementation is the density form of (1): the downstream
measurement is the per-lane density ``ρ_out`` [veh/m] and the target the
critical density ``ρ̂`` of the calibrated fundamental diagram, which must be
supplied (``rho_target_veh_km``; there is no built-in target). The gain is
given per veh/km: with the classic ``K_R ≈ 70 veh/h per 1% occupancy`` and
``1% occupancy ≈ 1.4 veh/km`` (a 5 m vehicle over a 2 m detector) that is
``≈ 50 veh/h per veh/km``, the default here. The rate is clipped to
``[rate_min, rate_max]`` with anti-windup (the stored state is the clipped
rate, Eq. (1) itself). Pure function: memory carries the previous rate.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from flowstate_core.controller_types import Memory, RampMeterObs

ALINEA_DEFAULTS: Final[dict[str, float]] = {
    "k_r_veh_h_per_veh_km": 50.0,  # K_R in density units (module docstring)
    "rate_min_veh_h": 240.0,
    "rate_max_veh_h": 1800.0,
}
"""Gain and rate bounds; the target ``rho_target_veh_km`` has no default."""


def alinea(obs: RampMeterObs, params: Mapping[str, float], memory: Memory) -> tuple[float, Memory]:
    """ALINEA metering rate for the next interval (density form of Eq. (1)).

    Args:
        obs: Downstream per-lane density [veh/m] and the previous rate.
        params: ``rho_target_veh_km`` (required, the diagram's critical
            density per lane), ``k_r_veh_h_per_veh_km``, ``rate_min_veh_h``,
            ``rate_max_veh_h``.
        memory: Controller memory; ``{"rate": r(k-1)}`` after the first call
            (``obs.rate_prev`` seeds it).

    Returns:
        ``(rate [veh/h] ∈ [rate_min, rate_max], new_memory)``.

    Raises:
        ValueError: Missing target or rate_min >= rate_max.
    """
    p = {**ALINEA_DEFAULTS, **params}
    if "rho_target_veh_km" not in p:
        raise ValueError("alinea needs rho_target_veh_km (critical density per lane, veh/km)")
    r_min, r_max = float(p["rate_min_veh_h"]), float(p["rate_max_veh_h"])
    if r_max <= r_min:
        raise ValueError("rate_max_veh_h must exceed rate_min_veh_h")
    r_prev = float(memory.get("rate", obs.rate_prev))
    rho_out_veh_km = 1000.0 * float(obs.density_downstream)
    rate = r_prev + float(p["k_r_veh_h_per_veh_km"]) * (
        float(p["rho_target_veh_km"]) - rho_out_veh_km
    )
    rate = min(max(rate, r_min), r_max)
    new_memory: Memory = dict(memory)
    new_memory["rate"] = rate
    return rate, new_memory
