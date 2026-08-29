"""Unit conversion helpers.

FlowState uses SI units internally everywhere: meters, seconds, m/s, veh/m,
veh/s. Anything user-facing (km/h, veh/km, veh/h) converts at the boundary
through these helpers — never with inline magic numbers (CLAUDE.md §2).
"""

from __future__ import annotations

_KMH_PER_MS = 3.6
_M_PER_KM = 1000.0
_S_PER_H = 3600.0


def kmh_to_ms(v_kmh: float) -> float:
    """km/h → m/s."""
    return v_kmh / _KMH_PER_MS


def ms_to_kmh(v_ms: float) -> float:
    """m/s → km/h."""
    return v_ms * _KMH_PER_MS


def veh_km_to_veh_m(rho_veh_km: float) -> float:
    """veh/km → veh/m."""
    return rho_veh_km / _M_PER_KM


def veh_m_to_veh_km(rho_veh_m: float) -> float:
    """veh/m → veh/km."""
    return rho_veh_m * _M_PER_KM


def veh_h_to_veh_s(q_veh_h: float) -> float:
    """veh/h → veh/s."""
    return q_veh_h / _S_PER_H


def veh_s_to_veh_h(q_veh_s: float) -> float:
    """veh/s → veh/h."""
    return q_veh_s * _S_PER_H


def h_to_s(t_h: float) -> float:
    """hours → seconds."""
    return t_h * _S_PER_H


def s_to_h(t_s: float) -> float:
    """seconds → hours."""
    return t_s / _S_PER_H
