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
outside this pure function. The two pure pieces the runners share for that
application live here as well: :func:`effective_limit` (the compliance
scaling rule) and :func:`gantry_segments` (the 0.5–1.0 km gantry
segmentation of edges or cells).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Final

from flowstate_core.controller_types import Memory, SegmentObs
from flowstate_core.units import kmh_to_ms, veh_km_to_veh_m

#: Target gantry segment length [m] (CLAUDE.md §4.4: "0.5–1.0 km segments").
#: :func:`gantry_segments` aims at this length and never goes below half of
#: it (500 m at the default) except for a corridor shorter than that.
VSL_SEGMENT_TARGET_M: Final[float] = 1000.0

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


def effective_limit(posted_ms: float, base_ms: float, compliance: float) -> float:
    """Speed limit a segment presents once fleet compliance is accounted for.

    This is the CLAUDE.md §4.4 rule "per-edge limit *scaled by compliance*",
    made explicit as a **fleet-average speed-limit response assumption**: a
    fraction ``compliance`` of drivers obey the posted VSL ``posted_ms`` and
    the remainder keep driving to the road's base limit ``base_ms``, so the
    one aggregate limit the segment can carry is the compliance-weighted
    mean::

        v_eff = compliance · posted + (1 − compliance) · base

    Properties (unit-tested):

    * ``compliance = 1`` returns exactly ``posted`` (when it restricts) and
      ``compliance = 0`` returns exactly ``base``; the response is linear and
      monotone in between.
    * The result is clamped to ``[min(posted, base), max(posted, base)]``
      and **never exceeds the base limit** — a VSL can only restrict, so a
      posted limit at or above the base limit is a no-op (the base stands).

    Modelling caveat: this is an aggregate stand-in for the per-driver
    response (a compliant subset obeying the gantry while the rest do not);
    on the programmatically generated networks the base limit is
    ``microsim.networks.EDGE_SPEED_LIMIT_MS`` (50 m/s, deliberately above
    every desired-speed draw), so there the non-compliant fraction is treated
    as unconstrained by the road and the scaled limit is a conservative
    (weak) VSL. A per-vehicle compliance model is the stricter follow-up.

    Args:
        posted_ms: Limit posted by the segment controller [m/s].
        base_ms: Base (statutory / network) limit of the segment [m/s]; in
            the macro tier this is the fundamental diagram's ``v_f``.
        compliance: Fraction of drivers obeying the posted limit, in
            ``[0, 1]``.

    Returns:
        Effective limit [m/s].

    Raises:
        ValueError: ``compliance`` outside ``[0, 1]`` or a negative speed.
    """
    if not 0.0 <= compliance <= 1.0:
        raise ValueError(f"compliance must lie in [0, 1], got {compliance}")
    if posted_ms < 0.0 or base_ms < 0.0:
        raise ValueError(f"speed limits must be >= 0, got posted={posted_ms}, base={base_ms}")
    if posted_ms >= base_ms:
        return float(base_ms)
    v_eff = compliance * posted_ms + (1.0 - compliance) * base_ms
    return float(min(max(v_eff, posted_ms), base_ms))


def gantry_segments(
    lengths: Sequence[float],
    target_m: float = VSL_SEGMENT_TARGET_M,
    min_m: float | None = None,
) -> list[tuple[int, int]]:
    """Group consecutive elements (edges or cells) into gantry segments.

    Greedy in route order (CLAUDE.md §4.4, "corridor divided into 0.5–1.0 km
    segments"): elements are appended to the open segment; the segment is
    closed before an element when it is already at least ``min_m`` long and
    closing it now leaves its length closer to ``target_m`` than appending
    that element would. Elements are never split, so an element longer than
    ``target_m`` forms its own segment. A trailing segment shorter than
    ``min_m`` joins the previous one (a corridor shorter than ``min_m``
    overall is a single segment). Total length is preserved.

    Args:
        lengths: Element lengths [m] in route order, all ``> 0``.
        target_m: Target segment length [m].
        min_m: Shortest admissible segment [m]; ``None`` ⇒ ``target_m / 2``
            (500 m at the default target).

    Returns:
        Half-open index ranges ``(start, stop)`` covering ``lengths`` in
        order; empty for an empty input.

    Raises:
        ValueError: Non-positive ``target_m``/``min_m``, ``min_m`` above
            ``target_m``, or a non-positive element length.
    """
    if target_m <= 0.0:
        raise ValueError(f"target_m must be > 0, got {target_m}")
    if min_m is None:
        min_m = target_m / 2.0
    if min_m <= 0.0 or min_m > target_m:
        raise ValueError(f"min_m must lie in (0, target_m], got {min_m}")
    if any(elem <= 0.0 for elem in lengths):
        raise ValueError("every element length must be > 0")

    n = len(lengths)
    if n == 0:
        return []
    bounds: list[tuple[int, int]] = []
    start = 0
    acc = 0.0
    for i, elem in enumerate(lengths):
        if i > start and acc >= min_m and abs(acc - target_m) <= abs(acc + elem - target_m):
            bounds.append((start, i))
            start = i
            acc = 0.0
        acc += elem
    bounds.append((start, n))
    if len(bounds) > 1 and acc < min_m:
        prev_start, _ = bounds[-2]
        bounds[-2:] = [(prev_start, n)]
    return bounds
