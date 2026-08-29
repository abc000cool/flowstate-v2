"""Reference Intelligent Driver Model acceleration law (CLAUDE.md §3.1).

Implements the IDM of Treiber, Hennecke & Helbing (2000), "Congested traffic
states in empirical observations and microscopic simulations", Phys. Rev. E
62(2):1805–1824, in the exact form frozen in CLAUDE.md §3.1:

    a(s, v, Δv) = a_max · [1 − (v/v0)^δ − (s*(v, Δv)/s)²]
    s*(v, Δv)   = s0 + max(0, v·T + v·Δv / (2·√(a_max·b)))

where ``s`` is the bumper-to-bumper gap [m], ``v`` the ego speed [m/s], and
``Δv = v − v_leader`` the approach rate [m/s] (positive when closing in).

The closed-form equilibrium gap (steady following, Δv = 0, a = 0) follows by
solving ``a = 0`` for ``s`` (Treiber & Kesting, *Traffic Flow Dynamics*,
Springer 2013, ch. 11; CLAUDE.md §9):

    s_eq(v) = (s0 + v·T) / √(1 − (v/v0)^δ),   0 ≤ v < v0.

These functions are pure and shared by the test suite and the Phase-2
calibration forward simulation (`calibration.idm_mle`). SI units throughout.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

from flowstate_core.constants import IDM_DEFAULTS

IDM_PARAM_DEFAULTS: Final[dict[str, float]] = dict(IDM_DEFAULTS)
"""Literature defaults (Treiber et al. 2000; CLAUDE.md §3.1 table), SI units."""


def _merged(params: Mapping[str, float] | None) -> dict[str, float]:
    """Overlay user params on the literature defaults."""
    if params is None:
        return dict(IDM_PARAM_DEFAULTS)
    return {**IDM_PARAM_DEFAULTS, **params}


def desired_gap(v: float, dv: float, params: Mapping[str, float] | None = None) -> float:
    """Dynamic desired gap s*(v, Δv) [m].

    Args:
        v: Ego speed [m/s].
        dv: Approach rate Δv = v − v_leader [m/s], positive when closing.
        params: Optional overrides of ``IDM_PARAM_DEFAULTS``
            (keys: v0, T, a_max, b, s0, delta).

    Returns:
        Desired gap s* [m] per Treiber et al. (2000), Eq. (2) of the paper
        (CLAUDE.md §3.1 second display equation).
    """
    p = _merged(params)
    dynamic = v * p["T"] + v * dv / (2.0 * math.sqrt(p["a_max"] * p["b"]))
    return p["s0"] + max(0.0, dynamic)


def idm_accel(s: float, v: float, dv: float, params: Mapping[str, float] | None = None) -> float:
    """IDM acceleration a(s, v, Δv) [m/s²].

    Args:
        s: Bumper-to-bumper gap to the leader [m]; must be > 0.
        v: Ego speed [m/s].
        dv: Approach rate Δv = v − v_leader [m/s], positive when closing.
        params: Optional overrides of ``IDM_PARAM_DEFAULTS``.

    Returns:
        Acceleration [m/s²] per Treiber et al. (2000), Eq. (1) of the paper
        (CLAUDE.md §3.1 first display equation).

    Raises:
        ValueError: If ``s <= 0`` (the model is singular at zero gap).
    """
    if s <= 0.0:
        raise ValueError(f"gap s must be > 0 m, got {s}")
    p = _merged(params)
    free_flow_term: float = (v / p["v0"]) ** p["delta"]
    interaction_term: float = (desired_gap(v, dv, p) / s) ** 2
    return p["a_max"] * (1.0 - free_flow_term - interaction_term)


def equilibrium_gap(v: float, params: Mapping[str, float] | None = None) -> float:
    """Closed-form equilibrium gap s_eq(v) [m] for steady following.

    Solves ``idm_accel(s, v, 0) == 0`` for ``s`` (Treiber & Kesting 2013,
    ch. 11; CLAUDE.md §9): ``s_eq = (s0 + v·T)/√(1 − (v/v0)^δ)``.

    Args:
        v: Ego (= platoon) speed [m/s]; must satisfy ``0 <= v < v0``.
        params: Optional overrides of ``IDM_PARAM_DEFAULTS``.

    Returns:
        Equilibrium gap [m].

    Raises:
        ValueError: If ``v`` is outside ``[0, v0)`` (no equilibrium exists at
            or above the desired speed).
    """
    p = _merged(params)
    if not 0.0 <= v < p["v0"]:
        raise ValueError(f"equilibrium gap requires 0 <= v < v0={p['v0']}, got v={v}")
    return (p["s0"] + v * p["T"]) / math.sqrt(1.0 - (v / p["v0"]) ** p["delta"])
