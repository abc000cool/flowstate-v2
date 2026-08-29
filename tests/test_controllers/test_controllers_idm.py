"""IDM reference-law tests: equilibrium-gap closed form, signs, domain guards."""

import math

import numpy as np
import pytest

from controllers import IDM_PARAM_DEFAULTS, desired_gap, equilibrium_gap, idm_accel


class TestEquilibriumGapClosedForm:
    """CLAUDE.md §9: verify s_eq = (s0 + v·T)/√(1 − (v/v0)^δ) symbolically."""

    def test_accel_vanishes_at_equilibrium_gap_across_speed_grid(self):
        v0 = IDM_PARAM_DEFAULTS["v0"]
        for v in np.linspace(0.0, 0.99 * v0, 40):
            v = float(v)
            assert abs(idm_accel(equilibrium_gap(v), v, 0.0)) <= 1e-9

    def test_holds_for_non_default_params(self):
        params = {"v0": 25.0, "T": 1.0, "a_max": 1.2, "b": 2.0, "s0": 1.5, "delta": 4.0}
        for v in np.linspace(0.0, 0.99 * 25.0, 20):
            v = float(v)
            assert abs(idm_accel(equilibrium_gap(v, params), v, 0.0, params)) <= 1e-9

    def test_standstill_equilibrium_is_minimum_gap(self):
        assert equilibrium_gap(0.0) == pytest.approx(IDM_PARAM_DEFAULTS["s0"])

    def test_rejects_speed_at_or_above_v0(self):
        with pytest.raises(ValueError):
            equilibrium_gap(IDM_PARAM_DEFAULTS["v0"])
        with pytest.raises(ValueError):
            equilibrium_gap(-0.1)


class TestAccelerationSigns:
    def test_decelerates_below_equilibrium_gap_accelerates_above(self):
        v = 20.0
        s_eq = equilibrium_gap(v)
        assert idm_accel(0.8 * s_eq, v, 0.0) < 0.0
        assert idm_accel(1.5 * s_eq, v, 0.0) > 0.0

    def test_closing_in_reduces_acceleration(self):
        # Δv = v − v_leader > 0 (approaching) must lower the acceleration.
        assert idm_accel(30.0, 20.0, 3.0) < idm_accel(30.0, 20.0, 0.0)

    def test_free_road_accel_at_low_speed_is_nearly_a_max(self):
        a = idm_accel(1e9, 0.0, 0.0)
        assert a == pytest.approx(IDM_PARAM_DEFAULTS["a_max"], rel=1e-6)

    def test_hand_computed_case(self):
        # v=10, Δv=0, s=50: s* = 2 + 10·1.4 = 16
        # a = 0.73·(1 − (10/33.3)^4 − (16/50)²) ≈ 0.73·(1 − 0.008133 − 0.1024)
        s_star = 2.0 + 10.0 * 1.4
        expected = 0.73 * (1.0 - (10.0 / 33.3) ** 4 - (s_star / 50.0) ** 2)
        assert idm_accel(50.0, 10.0, 0.0) == pytest.approx(expected, abs=1e-12)


class TestDesiredGap:
    def test_static_part_only_when_dynamic_negative(self):
        # Strongly opening gap (Δv very negative) → max(0, ·) clips to s0.
        assert desired_gap(5.0, -100.0) == pytest.approx(IDM_PARAM_DEFAULTS["s0"])

    def test_hand_computed(self):
        p = IDM_PARAM_DEFAULTS
        expected = p["s0"] + 10.0 * p["T"] + 10.0 * 2.0 / (2.0 * math.sqrt(p["a_max"] * p["b"]))
        assert desired_gap(10.0, 2.0) == pytest.approx(expected)


class TestDomain:
    def test_zero_or_negative_gap_raises(self):
        with pytest.raises(ValueError):
            idm_accel(0.0, 10.0, 0.0)
        with pytest.raises(ValueError):
            idm_accel(-1.0, 10.0, 0.0)
