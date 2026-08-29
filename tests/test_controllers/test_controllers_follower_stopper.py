"""FollowerStopper unit tests: hand-computed regions, boundary continuity.

Hand cases follow Stern et al. (2018), §3.1, Eqs. (1)–(2) with the paper's
constants Δx^0 = (4.5, 5.25, 6.0) m, d = (1.5, 1.0, 0.5) m/s².
"""

import math

import pytest

from controllers import default_params, follower_stopper
from flowstate_core.controller_types import ControllerObs


def _obs(v: float, gap: float, v_leader: float, v_ref: float) -> ControllerObs:
    return ControllerObs(t=0.0, dt=0.5, v=v, gap=gap, v_leader=v_leader, v_ref=v_ref)


def _cmd(v: float, gap: float, v_leader: float, v_ref: float) -> float:
    cmd, _ = follower_stopper(_obs(v, gap, v_leader, v_ref), {}, {})
    return cmd


class TestHandComputedRegions:
    """At Δv = 0 the boundaries are the intercepts (4.5, 5.25, 6.0) m."""

    def test_region1_stopping(self):
        assert _cmd(v=5.0, gap=4.0, v_leader=5.0, v_ref=15.0) == 0.0
        assert _cmd(v=5.0, gap=4.5, v_leader=5.0, v_ref=15.0) == 0.0  # boundary inclusive

    def test_region2_scaled_leader_speed(self):
        # gap = 4.875 is midway between Δx_1=4.5 and Δx_2=5.25 → v_cmd = v_lead/2
        assert _cmd(v=5.0, gap=4.875, v_leader=5.0, v_ref=15.0) == pytest.approx(2.5)
        # at Δx_2 the full (clamped) leader speed is commanded
        assert _cmd(v=5.0, gap=5.25, v_leader=5.0, v_ref=15.0) == pytest.approx(5.0)

    def test_region3_blend_to_u(self):
        # gap = 5.625 is midway between Δx_2=5.25 and Δx_3=6.0 → v_lead + (U−v_lead)/2
        assert _cmd(v=5.0, gap=5.625, v_leader=5.0, v_ref=15.0) == pytest.approx(10.0)
        assert _cmd(v=5.0, gap=6.0, v_leader=5.0, v_ref=15.0) == pytest.approx(15.0)

    def test_safe_region_commands_u(self):
        assert _cmd(v=5.0, gap=50.0, v_leader=5.0, v_ref=15.0) == 15.0

    def test_paper_example_boundaries_at_dv_minus_3(self):
        """Stern et al. §3.1: at Δv = −3 m/s, Δx = (7.5, 9.75, 15) m."""
        v, v_leader = 10.0, 7.0  # Δv = v_leader − v = −3
        # just inside region 1 at the shifted boundary
        assert _cmd(v, 7.5, v_leader, 15.0) == 0.0
        assert _cmd(v, 7.5 + 1e-6, v_leader, 15.0) > 0.0
        # region 2/3 boundary: full leader speed
        assert _cmd(v, 9.75, v_leader, 15.0) == pytest.approx(7.0)
        # region 3 / safe boundary: U
        assert _cmd(v, 15.0, v_leader, 15.0) == pytest.approx(15.0)
        assert _cmd(v, 15.1, v_leader, 15.0) == 15.0

    def test_v_lead_clamped_to_zero_and_u(self):
        # negative leader speed → treated as 0 in the adaptation regions:
        # at Δv=−1 the region-2 upper boundary is 5.75 m, where the full
        # (clamped) leader speed is commanded — here max(−1, 0) = 0.
        assert _cmd(v=0.0, gap=5.75, v_leader=-1.0, v_ref=15.0) == 0.0
        # leader faster than U → clamped to U (region 2 at its upper boundary)
        assert _cmd(v=5.0, gap=5.25, v_leader=30.0, v_ref=15.0) == pytest.approx(15.0)

    def test_no_leader_commands_u(self):
        assert _cmd(v=10.0, gap=math.inf, v_leader=math.nan, v_ref=15.0) == 15.0

    def test_negative_v_ref_clamped_to_zero(self):
        assert _cmd(v=5.0, gap=50.0, v_leader=5.0, v_ref=-1.0) == 0.0


class TestBoundaryContinuity:
    """v_cmd must be continuous at every region boundary (CLAUDE.md §4.1)."""

    @pytest.mark.parametrize("dv_minus", [0.0, -0.5, -1.0, -3.0, -6.0])
    @pytest.mark.parametrize("boundary_key", ["1", "2", "3"])
    def test_left_right_limits_agree(self, dv_minus: float, boundary_key: str):
        p = default_params("follower_stopper")
        v_leader = 5.0
        v = v_leader - dv_minus  # Δv = v_leader − v = dv_minus ≤ 0
        u = 15.0
        boundary = p[f"dx0_{boundary_key}"] + dv_minus**2 / (2.0 * p[f"d_{boundary_key}"])
        delta = 1e-12  # slope ≤ ~20 m/s per m ⇒ value change ≤ 4e-11 across ±δ
        left = _cmd(v, boundary - delta, v_leader, u)
        mid = _cmd(v, boundary, v_leader, u)
        right = _cmd(v, boundary + delta, v_leader, u)
        assert abs(left - mid) <= 1e-9
        assert abs(right - mid) <= 1e-9
        assert abs(left - right) <= 1e-9


class TestPurity:
    def test_memory_passed_through_unmutated(self):
        mem = {"unrelated": 1.0}
        cmd1, out1 = follower_stopper(_obs(5.0, 10.0, 5.0, 15.0), {}, mem)
        cmd2, out2 = follower_stopper(_obs(5.0, 10.0, 5.0, 15.0), {}, mem)
        assert cmd1 == cmd2
        assert mem == {"unrelated": 1.0}
        assert out1 == mem and out1 is not mem
        assert out2 == mem

    def test_invalid_params_raise(self):
        with pytest.raises(ValueError):
            follower_stopper(_obs(5.0, 10.0, 5.0, 15.0), {"d_2": 0.0}, {})
        with pytest.raises(ValueError):
            follower_stopper(_obs(5.0, 10.0, 5.0, 15.0), {"dx0_2": 4.0}, {})
