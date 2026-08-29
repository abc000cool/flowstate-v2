"""String-stability tests: closed-form partials vs finite differences, and
the critical numerical crosscheck of the analytic criterion against a
nonlinear 30-car IDM ring-platoon integration (CLAUDE.md §3.1).

The platoon deliberately uses a local copy of the IDM acceleration law
(transcribed from CLAUDE.md §3.1) rather than importing from the
``controllers`` package: it avoids a cross-package test dependency and, more
importantly, makes this an independent check of the analytic partials — both
sides would inherit the same bug if they shared one implementation. The
integration is fully deterministic (fixed initial kick, no RNG).
"""

import math

import numpy as np
import pytest

from flowstate_core.constants import IDM_DEFAULTS
from flowstate_core.units import veh_km_to_veh_m
from validation.string_stability import (
    equilibrium_gap,
    equilibrium_speed,
    idm_partials,
    is_string_stable,
    stability_criterion,
    unstable_band,
)

L_VEH = 5.0  # vehicle body length [m], SUMO default passenger car
N_CARS = 30
DT = 0.05
T_END = 300.0
V_STABLE = 30.0  # low-density equilibrium (analytically string-stable)
V_UNSTABLE = 15.0  # near-capacity equilibrium (analytically string-unstable)


def _idm_accel(
    gap: np.ndarray, v: np.ndarray, v_lead: np.ndarray, p: dict[str, float]
) -> np.ndarray:
    """Local IDM copy (CLAUDE.md §3.1): a = a_max[1-(v/v0)^d-(s*/s)^2]."""
    v_eff = np.maximum(v, 0.0)
    s_star = p["s0"] + np.maximum(
        0.0, v_eff * p["T"] + v_eff * (v_eff - v_lead) / (2.0 * math.sqrt(p["a_max"] * p["b"]))
    )
    a = p["a_max"] * (1.0 - (v_eff / p["v0"]) ** p["delta"] - (s_star / np.maximum(gap, 0.01)) ** 2)
    # No reverse driving: a standing vehicle cannot decelerate further.
    return np.where((v <= 0.0) & (a < 0.0), 0.0, a)


def _idm_scalar(gap: float, v: float, v_lead: float) -> float:
    return float(
        _idm_accel(np.array([gap]), np.array([v]), np.array([v_lead]), dict(IDM_DEFAULTS))[0]
    )


def _run_ring(v_e: float, kick: float = -0.5) -> tuple[bool, np.ndarray]:
    """Integrate a 30-car IDM ring at equilibrium speed v_e with the lead
    vehicle's speed kicked at t=0; RK4 with dt=0.05 for 300 s.

    Returns:
        (grew, follower_amps): ``grew`` is True when the cross-vehicle speed
        std over the last 50 s exceeds that over the first 10 s;
        ``follower_amps[j]`` is the max |v - v_e| of the j-th follower of
        the kicked vehicle within the first 90 s (before deep nonlinear
        saturation), the vehicle-to-vehicle amplitude measure.
    """
    p = dict(IDM_DEFAULTS)
    s_e = equilibrium_gap(v_e, p)
    spacing = s_e + L_VEH
    circumference = N_CARS * spacing
    x = np.arange(N_CARS, dtype=np.float64) * spacing
    v = np.full(N_CARS, v_e)
    v[0] += kick

    def deriv(x_s: np.ndarray, v_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        gap = (np.roll(x_s, -1) - x_s) % circumference - L_VEH
        a = _idm_accel(gap, v_s, np.roll(v_s, -1), p)
        return np.maximum(v_s, 0.0), a

    n_steps = int(T_END / DT)
    amp = np.zeros(N_CARS)
    std_hist = np.empty(n_steps)
    for k in range(n_steps):
        k1x, k1v = deriv(x, v)
        k2x, k2v = deriv(x + 0.5 * DT * k1x, v + 0.5 * DT * k1v)
        k3x, k3v = deriv(x + 0.5 * DT * k2x, v + 0.5 * DT * k2v)
        k4x, k4v = deriv(x + DT * k3x, v + DT * k3v)
        x = x + DT / 6.0 * (k1x + 2.0 * k2x + 2.0 * k3x + k4x)
        v = np.maximum(v + DT / 6.0 * (k1v + 2.0 * k2v + 2.0 * k3v + k4v), 0.0)
        if (k + 1) * DT <= 90.0:
            amp = np.maximum(amp, np.abs(v - v_e))
        std_hist[k] = float(np.std(v))
    early = float(std_hist[: int(10.0 / DT)].mean())
    late = float(std_hist[-int(50.0 / DT) :].mean())
    # j-th follower of the kicked vehicle 0 is (0 - j) mod N.
    follower_amps = np.array([amp[(0 - j) % N_CARS] for j in range(1, N_CARS)])
    return late > early, follower_amps


class TestEquilibrium:
    def test_gap_closed_form_matches_idm_zero_accel(self):
        for v_e in (5.0, 15.0, 25.0, 30.0):
            s_e = equilibrium_gap(v_e, IDM_DEFAULTS)
            assert _idm_scalar(s_e, v_e, v_e) == pytest.approx(0.0, abs=1e-12)

    def test_speed_inverts_gap(self):
        for v_e in (1.0, 10.0, 20.0, 31.0):
            s_e = equilibrium_gap(v_e, IDM_DEFAULTS)
            assert equilibrium_speed(s_e, IDM_DEFAULTS) == pytest.approx(v_e, abs=1e-6)
        assert equilibrium_speed(IDM_DEFAULTS["s0"], IDM_DEFAULTS) == 0.0

    def test_input_validation(self):
        with pytest.raises(ValueError, match="v_e"):
            equilibrium_gap(IDM_DEFAULTS["v0"], IDM_DEFAULTS)
        with pytest.raises(ValueError, match="gap"):
            equilibrium_speed(0.0, IDM_DEFAULTS)
        with pytest.raises(ValueError, match="missing keys"):
            idm_partials(10.0, {"v0": 33.3})


class TestPartials:
    """Closed-form partials vs central finite differences of the IDM law.

    Conventions checked: f_s = da/ds; f_dv = da/d(v_lead - v) (so the
    finite difference in v_lead at fixed v equals +f_dv); f_v = da/dv at
    fixed gap and fixed relative speed, so the finite difference in v at
    fixed v_lead equals f_v - f_dv (the Delta-v channel contributes -f_dv).
    """

    @pytest.mark.parametrize("v_e", [5.0, 15.0, 25.0, 30.0])
    def test_partials_match_finite_differences(self, v_e: float):
        h = 1e-6
        s_e = equilibrium_gap(v_e, IDM_DEFAULTS)
        p = idm_partials(v_e, IDM_DEFAULTS)
        num_f_s = (_idm_scalar(s_e + h, v_e, v_e) - _idm_scalar(s_e - h, v_e, v_e)) / (2 * h)
        num_f_dv = (_idm_scalar(s_e, v_e, v_e + h) - _idm_scalar(s_e, v_e, v_e - h)) / (2 * h)
        num_dv_own = (_idm_scalar(s_e, v_e + h, v_e) - _idm_scalar(s_e, v_e - h, v_e)) / (2 * h)
        assert p.f_s == pytest.approx(num_f_s, rel=1e-5)
        assert p.f_dv == pytest.approx(num_f_dv, rel=1e-5)
        assert p.f_v - p.f_dv == pytest.approx(num_dv_own, rel=1e-5)

    @pytest.mark.parametrize("v_e", [5.0, 15.0, 25.0, 30.0])
    def test_sign_conventions(self, v_e: float):
        p = idm_partials(v_e, IDM_DEFAULTS)
        assert p.f_s > 0
        assert p.f_v < 0
        assert p.f_dv > 0


class TestNumericalCrosscheck:
    """The critical check: analytic criterion vs nonlinear platoon growth.

    If these disagree, the analytic formula's sign conventions are wrong
    (CLAUDE.md §3.1 mandates verifying against Treiber & Kesting ch. 15).
    """

    def test_analytic_signs_at_chosen_points(self):
        assert stability_criterion(idm_partials(V_STABLE, IDM_DEFAULTS)) > 0
        assert stability_criterion(idm_partials(V_UNSTABLE, IDM_DEFAULTS)) < 0
        assert is_string_stable(V_STABLE, IDM_DEFAULTS)
        assert not is_string_stable(V_UNSTABLE, IDM_DEFAULTS)

    def test_stable_point_perturbation_decays(self):
        grew, amps = _run_ring(V_STABLE)
        assert not grew
        # Vehicle-to-vehicle decay along the follower chain.
        assert amps[24] < amps[9] < amps[0]
        assert is_string_stable(V_STABLE, IDM_DEFAULTS) == (not grew)

    def test_unstable_point_perturbation_grows(self):
        grew, amps = _run_ring(V_UNSTABLE)
        assert grew
        # Vehicle-to-vehicle growth far down the follower chain (index j is
        # the (j+1)-th follower): the 25th follower's early-window amplitude
        # exceeds the 10th's.
        assert amps[24] > amps[9]
        assert is_string_stable(V_UNSTABLE, IDM_DEFAULTS) == (not grew)


class TestUnstableBand:
    def test_band_brackets_capacity_and_excludes_extremes(self):
        rho_grid = veh_km_to_veh_m(1.0) * np.arange(1.0, 121.0, 1.0)
        rho_lo, rho_hi = unstable_band(IDM_DEFAULTS, rho_grid)
        assert math.isfinite(rho_lo) and math.isfinite(rho_hi)
        assert rho_lo < rho_hi
        # The near-capacity unstable point lies inside the band...
        rho_unstable = 1.0 / (equilibrium_gap(V_UNSTABLE, IDM_DEFAULTS) + L_VEH)
        assert rho_lo <= rho_unstable <= rho_hi
        # ...and the low-density stable point below it.
        rho_stable = 1.0 / (equilibrium_gap(V_STABLE, IDM_DEFAULTS) + L_VEH)
        assert rho_stable < rho_lo

    def test_no_band_for_very_light_traffic_grid(self):
        rho_grid = veh_km_to_veh_m(1.0) * np.arange(1.0, 6.0, 1.0)
        rho_lo, rho_hi = unstable_band(IDM_DEFAULTS, rho_grid)
        assert math.isnan(rho_lo) and math.isnan(rho_hi)

    def test_band_matches_pointwise_criterion(self):
        rho_grid = veh_km_to_veh_m(1.0) * np.arange(5.0, 101.0, 5.0)
        rho_lo, rho_hi = unstable_band(IDM_DEFAULTS, rho_grid)
        for rho in rho_grid:
            gap = 1.0 / rho - L_VEH
            if gap <= IDM_DEFAULTS["s0"]:
                continue
            v_e = equilibrium_speed(gap, IDM_DEFAULTS)
            if v_e <= 0.0:
                continue
            inside = rho_lo <= rho <= rho_hi
            assert inside == (not is_string_stable(v_e, IDM_DEFAULTS))

    def test_grid_validation(self):
        with pytest.raises(ValueError, match="empty"):
            unstable_band(IDM_DEFAULTS, np.array([]))
        with pytest.raises(ValueError, match="positive"):
            unstable_band(IDM_DEFAULTS, np.array([0.0, 0.01]))
        with pytest.raises(ValueError, match="ascending"):
            unstable_band(IDM_DEFAULTS, np.array([0.02, 0.01]))
