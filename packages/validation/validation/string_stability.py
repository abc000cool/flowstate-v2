"""Closed-form IDM string-stability analysis (CLAUDE.md §3.1, §7.2).

The IDM acceleration law (Treiber, Hennecke & Helbing 2000; CLAUDE.md §3.1),
with approach rate ``Δv := v − v_leader``::

    a(s, v, Δv) = a_max · [1 − (v/v0)^δ − (s*(v, Δv)/s)²]
    s*(v, Δv)   = s0 + max(0, v·T + v·Δv / (2·√(a_max·b)))

Equilibrium (uniform flow): ``Δv = 0`` and ``a = 0`` give
``(s*_e/s_e)² = 1 − (v_e/v0)^δ`` with ``s*_e = s0 + v_e·T``, hence the
closed-form equilibrium gap ``s_e = (s0 + v_e·T) / √(1 − (v_e/v0)^δ)``.

Partial derivatives at equilibrium (interior branch of the ``max`` is active
for ``v_e > 0``), derived step by step:

1. ``f_s = ∂a/∂s``: only the ``−(s*/s)²`` term depends on ``s``;
   ``∂/∂s[−(s*/s)²] = 2·s*²/s³``, so
   ``f_s = 2·a_max·s*_e² / s_e³ > 0``.
2. ``f_v = ∂a/∂v`` holding ``s`` and ``Δv`` fixed: the free-speed term gives
   ``−δ·v^(δ−1)/v0^δ``; the interaction term gives
   ``−2·(s*/s²)·∂s*/∂v`` with ``∂s*/∂v|_{Δv=0} = T``, so
   ``f_v = −a_max·[δ·v_e^(δ−1)/v0^δ + 2·T·s*_e/s_e²] < 0``.
3. ``∂a/∂Δv = −2·a_max·(s*/s²)·∂s*/∂Δv`` with
   ``∂s*/∂Δv = v/(2·√(a_max·b))``, so
   ``∂a/∂Δv = −a_max·s*_e·v_e / (s_e²·√(a_max·b)) ≤ 0``.
   Following the sign convention of the criterion (CLAUDE.md §3.1: partials
   ``f_s > 0``, ``f_v < 0``, ``f_Δv ≥ 0``), this module reports
   ``f_dv := ∂a/∂(v_leader − v) = −∂a/∂Δv ≥ 0`` — the sensitivity to the
   *leader-relative* speed. For the approach-rate convention used in the IDM
   law itself, ``∂a/∂Δv = −f_dv``.

String-stability criterion (Treiber & Kesting 2013, ch. 15): linearizing the
platoon dynamics ``ṡ_n = u_{n−1} − u_n``, ``u̇_n = f_s·y_n + f_v·u_n −
f_dv·(u_n − u_{n−1})`` and taking the vehicle-to-vehicle speed-perturbation
transfer function ``G(λ) = (f_s + f_dv·λ) / (λ² − (f_v − f_dv)·λ + f_s)``,
the condition ``|G(iω)| ≤ 1`` for all frequencies ω reduces to::

    f_v²/2 − f_v·f_dv − f_s ≥ 0   ⇔   string-stable

(the platoon is string-stable iff the criterion is non-negative). The sign
conventions here are verified against a direct nonlinear 30-vehicle platoon
integration in ``tests/test_validation/test_validation_string_stability.py``.

References:
    Treiber, M., Hennecke, A. & Helbing, D. (2000). Congested traffic states
    in empirical observations and microscopic simulations. Phys. Rev. E
    62:1805-1824.
    Treiber, M. & Kesting, A. (2013). Traffic Flow Dynamics, ch. 15.
    Springer.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import brentq

_PARAM_KEYS: tuple[str, ...] = ("v0", "T", "a_max", "b", "s0", "delta")

#: Default passenger-car length [m] used to convert density (veh/m, includes
#: vehicle bodies) to bumper-to-bumper gap; SUMO's default vType length.
DEFAULT_VEHICLE_LENGTH_M = 5.0


@dataclass(frozen=True)
class IDMPartials:
    """IDM acceleration partials at an equilibrium point.

    Attributes:
        f_s: ``∂a/∂s`` [1/s²], > 0.
        f_v: ``∂a/∂v`` at fixed gap and relative speed [1/s], < 0.
        f_dv: ``∂a/∂(v_leader − v)`` [1/s], ≥ 0 (leader-relative convention;
            the approach-rate derivative ``∂a/∂Δv`` equals ``−f_dv``).
    """

    f_s: float
    f_v: float
    f_dv: float


def _check_params(params: Mapping[str, float]) -> None:
    missing = [k for k in _PARAM_KEYS if k not in params]
    if missing:
        raise ValueError(f"IDM params missing keys: {missing}")
    for k in ("v0", "T", "a_max", "b", "s0", "delta"):
        if params[k] <= 0:
            raise ValueError(f"IDM param {k} must be > 0, got {params[k]}")


def equilibrium_gap(v_e: float, params: Mapping[str, float]) -> float:
    """Closed-form IDM equilibrium gap ``s_e(v_e)``.

    ``s_e = (s0 + v_e·T) / √(1 − (v_e/v0)^δ)`` (CLAUDE.md §9), the gap at
    which a vehicle following a leader at the same speed ``v_e`` has zero
    acceleration.

    Args:
        v_e: Equilibrium speed [m/s], ``0 <= v_e < v0``.
        params: IDM parameters (keys ``v0, T, a_max, b, s0, delta``), SI.

    Returns:
        Equilibrium bumper-to-bumper gap [m].

    Raises:
        ValueError: If ``v_e`` is outside ``[0, v0)`` or params are invalid.
    """
    _check_params(params)
    if not 0.0 <= v_e < params["v0"]:
        raise ValueError(f"need 0 <= v_e < v0={params['v0']}, got {v_e}")
    theta = 1.0 - (v_e / params["v0"]) ** params["delta"]
    return (params["s0"] + v_e * params["T"]) / math.sqrt(theta)


def equilibrium_speed(gap: float, params: Mapping[str, float]) -> float:
    """Invert the equilibrium gap relation: speed ``v_e`` for a given gap.

    ``s_e(v)`` is strictly increasing from ``s0`` (at ``v = 0``) to infinity
    (as ``v → v0``), so the inverse is unique; it is found by bisection
    (Brent's method). Gaps at or below ``s0`` map to ``v_e = 0`` (fully
    jammed).

    Args:
        gap: Bumper-to-bumper gap [m].
        params: IDM parameters, SI.

    Returns:
        Equilibrium speed [m/s] in ``[0, v0)``.

    Raises:
        ValueError: If ``gap`` is not positive or params are invalid.
    """
    _check_params(params)
    if gap <= 0:
        raise ValueError(f"gap must be > 0, got {gap}")
    if gap <= params["s0"]:
        return 0.0
    v_hi = params["v0"] * (1.0 - 1e-12)

    def residual(v: float) -> float:
        return equilibrium_gap(v, params) - gap

    return float(brentq(residual, 0.0, v_hi, xtol=1e-10, rtol=1e-12))


def idm_partials(v_e: float, params: Mapping[str, float]) -> IDMPartials:
    """Closed-form IDM acceleration partials at equilibrium speed ``v_e``.

    See the module docstring for the full derivation. Evaluated at
    ``(s, v, Δv) = (s_e(v_e), v_e, 0)``.

    Args:
        v_e: Equilibrium speed [m/s], ``0 < v_e < v0`` (the partials
            degenerate at standstill).
        params: IDM parameters, SI.

    Returns:
        :class:`IDMPartials` with ``f_s > 0``, ``f_v < 0``, ``f_dv >= 0``.

    Raises:
        ValueError: If ``v_e`` is outside ``(0, v0)`` or params are invalid.
    """
    _check_params(params)
    if not 0.0 < v_e < params["v0"]:
        raise ValueError(f"need 0 < v_e < v0={params['v0']}, got {v_e}")
    v0, t_hw, a_max, b, s0, delta = (params[k] for k in _PARAM_KEYS)
    s_star = s0 + v_e * t_hw
    s_e = equilibrium_gap(v_e, params)
    f_s = 2.0 * a_max * s_star**2 / s_e**3
    f_v = -a_max * (delta * v_e ** (delta - 1.0) / v0**delta + 2.0 * t_hw * s_star / s_e**2)
    f_dv = a_max * s_star * v_e / (s_e**2 * math.sqrt(a_max * b))
    return IDMPartials(f_s=f_s, f_v=f_v, f_dv=f_dv)


def stability_criterion(partials: IDMPartials) -> float:
    """String-stability margin ``f_v²/2 − f_v·f_dv − f_s``.

    Non-negative ⇔ string-stable: speed perturbations decay from vehicle to
    vehicle along the platoon (Treiber & Kesting 2013, ch. 15; derivation in
    the module docstring). Sign conventions: ``f_v < 0`` and
    ``f_dv = ∂a/∂(v_leader − v) ≥ 0``, so the ``−f_v·f_dv`` term is a
    stabilizing (positive) contribution from reacting to the leader.

    Args:
        partials: Equilibrium partials from :func:`idm_partials`.

    Returns:
        Criterion value [1/s²]; ``>= 0`` means string-stable.
    """
    return 0.5 * partials.f_v**2 - partials.f_v * partials.f_dv - partials.f_s


def is_string_stable(v_e: float, params: Mapping[str, float]) -> bool:
    """Whether an IDM platoon is string-stable at equilibrium speed ``v_e``.

    Args:
        v_e: Equilibrium speed [m/s], ``0 < v_e < v0``.
        params: IDM parameters, SI.

    Returns:
        ``True`` iff ``stability_criterion(idm_partials(v_e, params)) >= 0``.
    """
    return stability_criterion(idm_partials(v_e, params)) >= 0.0


def unstable_band(
    params: Mapping[str, float],
    rho_grid: NDArray[np.float64],
    vehicle_length_m: float = DEFAULT_VEHICLE_LENGTH_M,
) -> tuple[float, float]:
    """Locate the string-unstable equilibrium-density band.

    For each density ρ [veh/m], the equilibrium spacing is ``1/ρ`` (front
    bumper to front bumper), the gap is ``1/ρ − vehicle_length_m``, the
    equilibrium speed follows from :func:`equilibrium_speed`, and the
    stability criterion is evaluated there. Densities whose equilibrium is
    standstill (gap ≤ s0) or whose gap is non-positive carry no
    positive-speed equilibrium and are treated as not part of the linear
    instability band.

    Args:
        params: IDM parameters, SI.
        rho_grid: Densities to scan [veh/m], strictly positive, ascending.
        vehicle_length_m: Vehicle body length [m] separating spacing from
            gap; defaults to SUMO's default passenger-car length.

    Returns:
        ``(rho_lo, rho_hi)`` [veh/m]: the smallest and largest scanned
        density with a string-unstable equilibrium, or ``(nan, nan)`` if no
        scanned density is unstable. If the unstable set is disjoint (not
        expected for IDM), the envelope (overall min and max) is returned.

    Raises:
        ValueError: If ``rho_grid`` is empty, non-positive, or unsorted.
    """
    _check_params(params)
    rho = np.asarray(rho_grid, dtype=np.float64)
    if rho.size == 0:
        raise ValueError("rho_grid is empty")
    if np.any(rho <= 0):
        raise ValueError("rho_grid must be strictly positive")
    if np.any(np.diff(rho) < 0):
        raise ValueError("rho_grid must be ascending")
    unstable: list[float] = []
    for r in rho:
        gap = 1.0 / float(r) - vehicle_length_m
        if gap <= params["s0"]:
            continue  # standstill equilibrium: no positive-speed dynamics
        v_e = equilibrium_speed(gap, params)
        if v_e <= 0.0:
            continue
        if stability_criterion(idm_partials(v_e, params)) < 0.0:
            unstable.append(float(r))
    if not unstable:
        return (math.nan, math.nan)
    return (min(unstable), max(unstable))
