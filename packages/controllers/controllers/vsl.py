"""Segment-level threshold VSL controller (CLAUDE.md §4.4).

Gantry-style variable speed limits: the corridor is divided into segments
(0.5–1.0 km, runner-defined) and each segment posts a limit from a descending
speed ladder, default {90, 80, 70, 60, 50} km/h (converted to SI via
``flowstate_core.units``), plus a free-flow "no-op" cap posted when
uncongested.

Logic (simple threshold variant of CLAUDE.md §4.4; the shock-wave-targeting
refinement is SPECIALIST — A. Hegyi et al., "SPECIALIST: A dynamic speed limit
control algorithm based on shock wave theory", Proc. IEEE ITSC 2008,
pp. 827–832): each segment reacts to the state of its immediately downstream
neighbour with hysteresis:

* **Escalate** one ladder rung per call when the downstream segment is
  congested: mean speed < ``v_on`` OR density > ``rho_on``.
* **De-escalate** one rung per call only when the downstream segment has
  clearly recovered: mean speed > ``v_off`` AND density < ``rho_off``.
* Otherwise hold the current rung.

Because ``v_off > v_on`` and ``rho_off < rho_on``, an input oscillating around
either single threshold cannot chatter the posted limit up and down (verified
by unit test). The downstream-most segment has no downstream neighbour and
relaxes to the free-flow cap. NaN speeds (empty segments) count as
uncongested. Call cadence (typically 30–60 s) is the runner's choice; one
rung moves per call.

Application: micro tier via per-edge ``edge.setMaxSpeed`` scaled by
compliance; macro tier by capping ``V_f`` per cell (CLAUDE.md §4.4) — both
outside this pure function.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

from flowstate_core.controller_types import Memory, SegmentObs
from flowstate_core.units import kmh_to_ms, veh_km_to_veh_m

VSL_THRESHOLD_DEFAULTS: Final[dict[str, float]] = {
    "ladder_0": kmh_to_ms(90.0),  # restriction ladder [m/s], rung 1
    "ladder_1": kmh_to_ms(80.0),
    "ladder_2": kmh_to_ms(70.0),
    "ladder_3": kmh_to_ms(60.0),
    "ladder_4": kmh_to_ms(50.0),  # deepest restriction
    "v_free": kmh_to_ms(120.0),  # no-op free-flow cap [m/s] (rung 0)
    "v_on": kmh_to_ms(60.0),  # escalate when downstream speed drops below [m/s]
    "v_off": kmh_to_ms(70.0),  # de-escalate only above this speed [m/s]
    "rho_on": veh_km_to_veh_m(40.0),  # escalate when downstream density exceeds [veh/m]
    "rho_off": veh_km_to_veh_m(30.0),  # de-escalate only below this density [veh/m]
}
"""Defaults (CLAUDE.md §4.4 ladder; thresholds are Phase-1 placeholders)."""


def _ladder(p: Mapping[str, float]) -> list[float]:
    """Extract the contiguous ladder ``ladder_0..ladder_{k-1}`` from params."""
    rungs: list[float] = []
    k = 0
    while f"ladder_{k}" in p:
        rungs.append(p[f"ladder_{k}"])
        k += 1
    return rungs


def vsl_threshold(
    obs: SegmentObs, params: Mapping[str, float], memory: Memory
) -> tuple[tuple[float, ...], Memory]:
    """Threshold VSL with hysteresis (SegmentControllerFn contract).

    Args:
        obs: Segment observation (SI), segments ordered upstream→downstream.
        params: Overrides of ``VSL_THRESHOLD_DEFAULTS``. The ladder is read
            from contiguous ``ladder_0, ladder_1, …`` keys (descending speeds
            expected); ``v_free`` is the uncongested no-op cap.
        memory: Current ladder rung per segment under keys ``"seg_{i}"``
            (float; 0 = free-flow cap, k = ladder rung k−1). Missing keys
            start at 0.

    Returns:
        ``(posted limit per segment [m/s], new_memory)``, one limit per
        observed segment.
    """
    p = {**VSL_THRESHOLD_DEFAULTS, **params}
    rungs = _ladder(p)
    n_seg = len(obs.seg_speed)
    new_memory = dict(memory)
    limits: list[float] = []

    for i in range(n_seg):
        idx = int(new_memory.get(f"seg_{i}", 0.0))
        idx = min(max(idx, 0), len(rungs))

        j = i + 1
        if j < n_seg:
            speed_dn = obs.seg_speed[j]
            rho_dn = obs.seg_density[j] if j < len(obs.seg_density) else 0.0
            speed_low = not math.isnan(speed_dn) and speed_dn < p["v_on"]
            dens_high = not math.isnan(rho_dn) and rho_dn > p["rho_on"]
            speed_ok = math.isnan(speed_dn) or speed_dn > p["v_off"]
            dens_ok = math.isnan(rho_dn) or rho_dn < p["rho_off"]
            congested = speed_low or dens_high
            recovered = speed_ok and dens_ok
        else:
            # Downstream-most segment: nothing observable ahead — relax.
            congested, recovered = False, True

        if congested:
            idx = min(idx + 1, len(rungs))
        elif recovered:
            idx = max(idx - 1, 0)

        limits.append(p["v_free"] if idx == 0 else rungs[idx - 1])
        new_memory[f"seg_{i}"] = float(idx)

    return tuple(limits), new_memory
