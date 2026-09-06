"""ALINEA ramp meter and the capacity-aware FollowerStopper (pure functions)."""

from __future__ import annotations

import math
from itertools import pairwise

import pytest

from controllers.follower_stopper import follower_stopper
from controllers.follower_stopper_capacity import (
    FOLLOWER_STOPPER_CAPACITY_DEFAULTS,
    follower_stopper_capacity,
)
from controllers.ramp_meter import ALINEA_DEFAULTS, alinea
from controllers.registry import (
    default_params,
    get_ramp_meter,
    get_vehicle_controller,
    list_controllers,
)
from flowstate_core.controller_types import ControllerObs, RampMeterObs


def _mobs(rho_veh_km: float, rate_prev: float = 1200.0) -> RampMeterObs:
    return RampMeterObs(t=0.0, dt=30.0, density_downstream=rho_veh_km / 1000.0, rate_prev=rate_prev)


class TestAlinea:
    def test_needs_a_target(self):
        with pytest.raises(ValueError, match="rho_target_veh_km"):
            alinea(_mobs(20.0), {}, {})

    def test_integral_feedback_direction_and_gain(self):
        p = {"rho_target_veh_km": 30.0}
        below, mem = alinea(_mobs(20.0), p, {})
        assert below == pytest.approx(1200.0 + ALINEA_DEFAULTS["k_r_veh_h_per_veh_km"] * 10.0)
        above, _ = alinea(_mobs(40.0), p, {})
        assert above == pytest.approx(1200.0 - ALINEA_DEFAULTS["k_r_veh_h_per_veh_km"] * 10.0)
        # memory carries the applied rate forward, not the observation's rate_prev
        again, _ = alinea(_mobs(30.0, rate_prev=0.0), p, mem)
        assert again == pytest.approx(below)

    def test_saturation_with_anti_windup(self):
        p = {"rho_target_veh_km": 30.0}
        mem: dict = {}
        for _ in range(50):
            rate, mem = alinea(_mobs(0.0), p, mem)
        assert rate == pytest.approx(ALINEA_DEFAULTS["rate_max_veh_h"])
        rate, mem = alinea(_mobs(60.0), p, mem)
        assert rate == pytest.approx(ALINEA_DEFAULTS["rate_max_veh_h"] - 50.0 * 30.0)
        for _ in range(50):
            rate, mem = alinea(_mobs(200.0), p, mem)
        assert rate == pytest.approx(ALINEA_DEFAULTS["rate_min_veh_h"])
        with pytest.raises(ValueError, match="rate_max"):
            alinea(_mobs(1.0), {**p, "rate_min_veh_h": 900.0, "rate_max_veh_h": 800.0}, {})

    def test_registry(self):
        assert get_ramp_meter("alinea") is alinea
        assert "ramp_meter" in list_controllers() and "alinea" in list_controllers()["ramp_meter"]
        assert default_params("alinea")["k_r_veh_h_per_veh_km"] == 50.0
        with pytest.raises(KeyError, match="unknown ramp meter"):
            get_ramp_meter("nope")


def _vobs(
    gap: float, v: float = 20.0, v_leader: float = 20.0, v_ref: float = 25.0
) -> ControllerObs:
    return ControllerObs(t=0.0, dt=0.5, v=v, gap=gap, v_leader=v_leader, v_ref=v_ref)


class TestFollowerStopperCapacity:
    def test_identical_to_follower_stopper_inside_the_cap(self):
        p = FOLLOWER_STOPPER_CAPACITY_DEFAULTS
        for gap in (3.0, 5.0, 6.5, 10.0, 20.0):
            obs = _vobs(gap, v=10.0, v_leader=12.0)
            g_max = p["g0_m"] + p["h_max_s"] * obs.v
            if gap <= g_max:
                assert follower_stopper_capacity(obs, {}, {})[0] == pytest.approx(
                    follower_stopper(obs, {}, {})[0]
                )

    def test_releases_toward_the_leader_beyond_the_cap(self):
        # FollowerStopper at a large gap already commands U; make U low so the
        # release (toward min(v_leader, U)) is visible: U below the leader speed
        obs = _vobs(gap=80.0, v=10.0, v_leader=15.0, v_ref=12.0)
        v_fs = follower_stopper(obs, {}, {})[0]
        v_cap = follower_stopper_capacity(obs, {}, {})[0]
        assert v_cap >= v_fs and v_cap <= max(obs.v_ref, 0.0)
        # a leader slower than the command never pulls the command down
        obs2 = _vobs(gap=80.0, v=10.0, v_leader=5.0, v_ref=12.0)
        assert follower_stopper_capacity(obs2, {}, {})[0] == pytest.approx(
            follower_stopper(obs2, {}, {})[0]
        )

    def test_continuity_across_the_blend(self):
        p = FOLLOWER_STOPPER_CAPACITY_DEFAULTS
        v, v_leader, v_ref = 10.0, 15.0, 12.0
        g_max = p["g0_m"] + p["h_max_s"] * v
        gaps = [
            g_max - 0.01,
            g_max,
            g_max + 0.01,
            g_max + p["blend_m"] - 0.01,
            g_max + p["blend_m"] + 0.01,
        ]
        cmds = [follower_stopper_capacity(_vobs(g, v, v_leader, v_ref), {}, {})[0] for g in gaps]
        for a, b in pairwise(cmds):
            assert abs(a - b) < 0.5

    def test_bounds_and_no_leader(self):
        assert follower_stopper_capacity(_vobs(math.inf, v_leader=math.nan), {}, {})[0] == 25.0
        for gap in (1.0, 5.0, 8.0, 30.0, 200.0):
            for v_leader in (0.0, 10.0, 30.0):
                v_cmd = follower_stopper_capacity(_vobs(gap, 10.0, v_leader, 25.0), {}, {})[0]
                assert 0.0 <= v_cmd <= 25.0
        with pytest.raises(ValueError):
            follower_stopper_capacity(_vobs(10.0), {"h_max_s": 0.0}, {})
        assert get_vehicle_controller("follower_stopper_capacity") is follower_stopper_capacity
