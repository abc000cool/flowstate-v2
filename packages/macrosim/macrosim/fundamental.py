"""Fundamental-diagram helpers for the macroscopic screening tier.

The triangular fundamental diagram itself lives in
:class:`flowstate_core.artifacts.TriangularFD` (docs/CONTRACTS.md §5) — this
module deliberately does **not** duplicate that class. It re-exports it and adds
the small pieces the CTM solver needs on top: the equilibrium speed map
``V_e(ρ)``, a reduced-speed capacity helper used by bottleneck/perturbation
actuation, and the documented-but-uncalibrated ``v1_legacy`` preset accessor.

All quantities are SI (m, s, m/s, veh/m, veh/s); user-facing unit conversions
go through :mod:`flowstate_core.units` only (CLAUDE.md §2).

References:
    Lighthill & Whitham (1955); Richards (1956) — the LWR conservation law
    this diagram closes.
    Daganzo (1994), "The cell transmission model", Transp. Res. B 28(4) —
    triangular FD and the supply/demand (sending/receiving) formulation.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from flowstate_core.artifacts import TriangularFD
from flowstate_core.constants import V1_LEGACY_FD

__all__ = [
    "TriangularFD",
    "capacity_at_speed",
    "equilibrium_speed",
    "equilibrium_speed_scalar",
    "fd_tuple",
    "v1_legacy_fd",
]


def equilibrium_speed(fd: TriangularFD, rho: NDArray[np.float64]) -> NDArray[np.float64]:
    """Equilibrium speed ``V_e(ρ) = Q_e(ρ)/ρ`` for an array of densities.

    On the free-flow branch (ρ ≤ ρ_c) the speed is exactly ``v_f``; on the
    congested branch it is ``−w·(ρ_jam − ρ)/ρ``. The ρ → 0 limit is defined as
    ``v_f`` (Daganzo 1994). Output is clipped to ``[0, v_f]`` so floating-point
    noise can never produce an out-of-range speed.

    Args:
        fd: Triangular fundamental diagram (SI units).
        rho: Densities [veh/m]; any shape.

    Returns:
        Equilibrium speeds [m/s], same shape as ``rho``.
    """
    rho_arr = np.asarray(rho, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        v_cong = np.where(rho_arr > 0.0, -fd.w * (fd.rho_jam - rho_arr) / rho_arr, fd.v_f)
    v = np.where(rho_arr <= fd.rho_c, fd.v_f, v_cong)
    return np.clip(v, 0.0, fd.v_f)


def equilibrium_speed_scalar(fd: TriangularFD, rho: float) -> float:
    """Scalar ``V_e(ρ)`` [m/s]; see :func:`equilibrium_speed`.

    Args:
        fd: Triangular fundamental diagram (SI units).
        rho: Density [veh/m].

    Returns:
        Equilibrium speed [m/s], clipped to ``[0, v_f]``.
    """
    if rho <= fd.rho_c:
        return fd.v_f
    v = -fd.w * (fd.rho_jam - rho) / rho
    return float(min(max(v, 0.0), fd.v_f))


def capacity_at_speed(fd: TriangularFD, v_cap: float) -> float:
    """Capacity [veh/s] of ``fd`` with its free-flow speed capped at ``v_cap``.

    Replacing the free-flow branch slope ``v_f`` by ``v_cap`` (keeping ``w``
    and ``ρ_jam``) yields a reduced triangular diagram whose capacity is::

        q_cap = v_cap · ρ_jam · (−w) / (v_cap − w)

    This is the maximum flow that can pass a point where traffic cannot exceed
    ``v_cap`` — used for the reduced-capacity moving-bottleneck variant and for
    the seeded-perturbation actuation (a temporary local speed/capacity
    reduction). It recovers ``fd.q_max`` at ``v_cap = v_f`` and 0 at
    ``v_cap = 0``, and is monotone in between.

    Args:
        fd: Triangular fundamental diagram (SI units).
        v_cap: Speed cap [m/s]; values are clipped to ``[0, v_f]``.

    Returns:
        Reduced capacity [veh/s].
    """
    v = float(min(max(v_cap, 0.0), fd.v_f))
    if v == 0.0:
        return 0.0
    return v * fd.rho_jam * -fd.w / (v - fd.w)


def fd_tuple(fd: TriangularFD) -> tuple[float, float, float, float, float]:
    """Flatten an FD to plain floats for the Numba kernel.

    Args:
        fd: Triangular fundamental diagram (SI units).

    Returns:
        ``(v_f, w, rho_jam, rho_c, q_max)`` in SI units.
    """
    return (fd.v_f, fd.w, fd.rho_jam, fd.rho_c, fd.q_max)


def v1_legacy_fd() -> TriangularFD:
    """Return a copy of the documented v1 legacy FD preset.

    v1 hard-coded ``V_f = 100 km/h``, ``ρ_jam = 160 veh/km``, ``w = −20 km/h``;
    in v2 those values survive only as this explicitly named, *uncalibrated*
    preset (CLAUDE.md §5.1). The canonical instance is
    :data:`flowstate_core.constants.V1_LEGACY_FD`; a deep copy is returned so
    callers can never mutate the shared constant.

    Returns:
        A fresh :class:`TriangularFD` equal to the v1 preset (SI units).
    """
    return V1_LEGACY_FD.model_copy(deep=True)
