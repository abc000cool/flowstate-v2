"""Tests for macrosim.fundamental — FD helpers and the v1_legacy preset."""

from __future__ import annotations

import numpy as np
import pytest

import flowstate_core.artifacts
from flowstate_core.constants import V1_LEGACY_FD
from flowstate_core.units import kmh_to_ms, veh_km_to_veh_m
from macrosim.fundamental import (
    TriangularFD,
    capacity_at_speed,
    equilibrium_speed,
    equilibrium_speed_scalar,
    fd_tuple,
    v1_legacy_fd,
)


def test_triangular_fd_is_reexported_not_duplicated() -> None:
    """macrosim must reuse the core FD class, never define its own."""
    assert TriangularFD is flowstate_core.artifacts.TriangularFD


def test_v1_legacy_preset_values() -> None:
    """The preset carries v1's (100 km/h, 160 veh/km, −20 km/h) in SI units."""
    fd = v1_legacy_fd()
    assert fd.v_f == pytest.approx(kmh_to_ms(100.0))
    assert fd.w == pytest.approx(-kmh_to_ms(20.0))
    assert fd.rho_jam == pytest.approx(veh_km_to_veh_m(160.0))
    # Derived values follow from the class, sanity-check the branch intersection
    assert fd.rho_c == pytest.approx(fd.rho_jam * -fd.w / (fd.v_f - fd.w))
    assert fd.q_max == pytest.approx(fd.rho_c * fd.v_f)


def test_v1_legacy_preset_returns_isolated_copy() -> None:
    """Mutating the returned preset must never touch the shared constant."""
    fd = v1_legacy_fd()
    assert fd is not V1_LEGACY_FD
    fd.rho_jam = 999.0
    assert V1_LEGACY_FD.rho_jam == pytest.approx(veh_km_to_veh_m(160.0))


def test_equilibrium_speed_limits_and_branches() -> None:
    """V_e is v_f on the free branch, 0 at jam, and continuous at rho_c."""
    fd = v1_legacy_fd()
    rho = np.array([0.0, 0.5 * fd.rho_c, fd.rho_c, fd.rho_jam])
    v = equilibrium_speed(fd, rho)
    assert v[0] == pytest.approx(fd.v_f)
    assert v[1] == pytest.approx(fd.v_f)
    assert v[2] == pytest.approx(fd.v_f)
    assert v[3] == pytest.approx(0.0)
    # continuity just past the kink
    v_kink = equilibrium_speed(fd, np.array([fd.rho_c * (1 + 1e-9)]))[0]
    assert v_kink == pytest.approx(fd.v_f, rel=1e-6)


def test_equilibrium_speed_monotone_and_bounded() -> None:
    """V_e(ρ) is non-increasing and stays inside [0, v_f]."""
    fd = v1_legacy_fd()
    rho = np.linspace(0.0, fd.rho_jam, 500)
    v = equilibrium_speed(fd, rho)
    assert np.all(np.diff(v) <= 1e-12)
    assert v.min() >= 0.0
    assert v.max() <= fd.v_f + 1e-12


def test_equilibrium_speed_scalar_matches_array() -> None:
    fd = v1_legacy_fd()
    for rho in [0.0, 0.01, fd.rho_c, 0.08, fd.rho_jam]:
        assert equilibrium_speed_scalar(fd, rho) == pytest.approx(
            float(equilibrium_speed(fd, np.array([rho]))[0])
        )


def test_capacity_at_speed_limits_and_monotonicity() -> None:
    """q_cap(v_f) = q_max, q_cap(0) = 0, monotone increasing in between."""
    fd = v1_legacy_fd()
    assert capacity_at_speed(fd, fd.v_f) == pytest.approx(fd.q_max)
    assert capacity_at_speed(fd, 0.0) == 0.0
    assert capacity_at_speed(fd, 2 * fd.v_f) == pytest.approx(fd.q_max)  # clipped
    vs = np.linspace(0.0, fd.v_f, 100)
    caps = np.array([capacity_at_speed(fd, float(v)) for v in vs])
    assert np.all(np.diff(caps) > 0)


def test_fd_tuple_matches_fd() -> None:
    fd = v1_legacy_fd()
    v_f, w, rho_jam, rho_c, q_max = fd_tuple(fd)
    assert (v_f, w, rho_jam) == (fd.v_f, fd.w, fd.rho_jam)
    assert rho_c == pytest.approx(fd.rho_c)
    assert q_max == pytest.approx(fd.q_max)
