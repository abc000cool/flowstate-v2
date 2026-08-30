"""Stern et al. (2018) PI-with-saturation, Eqs. (3)-(5).

Hand-computed cases use the paper's own constants (§3.2): g_l = 7 m,
g_u = 30 m, v_catch = 1 m/s, gamma = 2 m, dx_s = max(2 s * dv, 4 m).
"""

import itertools
import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from controllers import default_params, pi_saturation


def _obs(**kw):
    from flowstate_core.controller_types import ControllerObs

    base = dict(t=0.0, dt=0.5, v=20.0, gap=25.0, v_leader=20.0, v_ref=25.0)
    base.update(kw)
    return ControllerObs(**base)


P = default_params("pi_saturation")


class TestPaperConstants:
    def test_defaults_match_stern_2018(self):
        assert P["g_l"] == 7.0
        assert P["g_u"] == 30.0
        assert P["v_catch"] == 1.0
        assert P["gamma"] == 2.0
        assert P["dx_s_min"] == 4.0


class TestEq3Target:
    """v_target = U + v_catch * clamp((dx - g_l)/(g_u - g_l), 0, 1)."""

    def test_catch_term_saturates_low_at_or_below_g_l(self):
        # gap <= g_l => target reduces to U exactly; with u_bar seeded at v and
        # alpha == 0 at such a short gap the command follows the leader.
        cmd, _ = pi_saturation(_obs(v=20.0, gap=5.0, v_leader=15.0), {}, {"u_bar": 20.0})
        assert cmd <= 20.0

    def test_catch_term_saturates_high_above_g_u(self):
        # gap >= g_u => full catch-up: target = U + v_catch.
        mem = {"u_bar": 20.0, "v_cmd_prev": 20.0}
        cmd, _ = pi_saturation(_obs(v=20.0, gap=50.0, v_leader=20.0, v_ref=30.0), {}, mem)
        # alpha = 1 (gap >> dx_s), beta = 0.5 => 0.5*21 + 0.5*20 = 20.5
        assert cmd == pytest.approx(20.5, abs=1e-9)

    def test_catch_term_is_linear_between_limits(self):
        # gap = 18.5 m is the midpoint of [7, 30] => catch_frac = 0.5
        mem = {"u_bar": 20.0, "v_cmd_prev": 20.0}
        cmd, _ = pi_saturation(_obs(v=20.0, gap=18.5, v_leader=20.0, v_ref=30.0), {}, mem)
        # target = 20.5; alpha = 1 (18.5 >> dx_s = 4); beta = 0.5
        assert cmd == pytest.approx(0.5 * 20.5 + 0.5 * 20.0, abs=1e-9)

    def test_target_never_below_u(self):
        """The paper's correction is additive and non-negative (unlike 0.75*mean)."""
        for gap in (0.0, 3.0, 7.0, 15.0, 30.0, 100.0):
            cmd, _ = pi_saturation(
                _obs(v=20.0, gap=gap, v_leader=20.0, v_ref=30.0),
                {},
                {"u_bar": 20.0, "v_cmd_prev": 20.0},
            )
            assert cmd >= 19.99  # never drags below U when the leader is at U


class TestEq5Weights:
    def test_alpha_zero_at_safety_distance_follows_leader(self):
        # dx_s = max(2*dv, 4); dv = 0 => dx_s = 4 m. gap = 4 => alpha = 0, beta = 1
        # => v_cmd = 1.0 * (0*target + 1*v_lead) + 0 * v_cmd_prev = v_lead
        cmd, _ = pi_saturation(
            _obs(v=10.0, gap=4.0, v_leader=6.0, v_ref=25.0),
            {},
            {"u_bar": 10.0, "v_cmd_prev": 10.0},
        )
        assert cmd == pytest.approx(6.0, abs=1e-9)

    def test_alpha_one_at_safety_distance_plus_gamma(self):
        # Leader at ego speed => closing = 0 => dx_s = 4 m; gap = dx_s + gamma = 6
        # => alpha = 1, beta = 0.5. catch_frac = (6-7)/23 clamps to 0 => target = U.
        mem = {"u_bar": 10.0, "v_cmd_prev": 10.0}
        cmd, _ = pi_saturation(_obs(v=10.0, gap=6.0, v_leader=10.0, v_ref=25.0), {}, mem)
        assert cmd == pytest.approx(0.5 * 10.0 + 0.5 * 10.0, abs=1e-9)

    def test_alpha_ramps_linearly_over_gamma(self):
        """Over gap in [dx_s, dx_s + gamma] the command slides from leader to target."""
        # closing = 0 => dx_s = 4 m; U (20) well above the leader (5) so the two
        # ends of the ramp are clearly distinguishable.
        vals = []
        for gap in (4.0, 4.5, 5.0, 5.5, 6.0):
            cmd, _ = pi_saturation(
                _obs(v=5.0, gap=gap, v_leader=5.0, v_ref=25.0),
                {},
                {"u_bar": 20.0, "v_cmd_prev": 5.0},
            )
            vals.append(cmd)
        assert vals == sorted(vals)
        assert vals[0] == pytest.approx(5.0, abs=1e-9)  # alpha = 0 -> leader speed
        assert vals[-1] > vals[0] + 3.0  # alpha = 1 -> pulled toward U
        # alpha is linear in the gap (Eq. 5), but the COMMAND is not: beta = 1 - alpha/2
        # varies with alpha too, so Eq. (4) is quadratic in alpha and the response is
        # concave — successive increments shrink as the gap opens.
        steps = [b - a for a, b in itertools.pairwise(vals)]
        assert steps == sorted(steps, reverse=True), steps
        assert all(d > 0.0 for d in steps)

    def test_closing_rate_widens_safety_distance(self):
        """dx_s = max(2 s * dv, 4 m): closing fast defers to the leader for longer."""
        closing = _obs(v=20.0, gap=9.0, v_leader=10.0, v_ref=25.0)  # dv=10 => dx_s=20
        steady = _obs(v=10.0, gap=9.0, v_leader=10.0, v_ref=25.0)  # dv=0  => dx_s=4
        c_close, _ = pi_saturation(closing, {}, {"u_bar": 20.0, "v_cmd_prev": 20.0})
        c_steady, _ = pi_saturation(steady, {}, {"u_bar": 10.0, "v_cmd_prev": 10.0})
        # Closing case: alpha = 0 => command is the leader speed exactly.
        assert c_close == pytest.approx(10.0, abs=1e-9)
        assert c_steady > c_close - 1e-9


class TestDesiredVelocityU:
    def test_u_is_ego_history_not_platoon_mean(self):
        """U must ignore obs.v_ref (the paper averages the AV's OWN speed)."""
        a, _ = pi_saturation(_obs(v=20.0, v_ref=25.0), {}, {"u_bar": 20.0, "v_cmd_prev": 20.0})
        b, _ = pi_saturation(_obs(v=20.0, v_ref=40.0), {}, {"u_bar": 20.0, "v_cmd_prev": 20.0})
        assert a == pytest.approx(b, abs=1e-9)

    def test_u_ema_converges_to_steady_speed(self):
        mem: dict[str, float] = {}
        for _ in range(2000):
            _, mem = pi_saturation(_obs(v=18.0, gap=25.0, v_leader=18.0), {}, mem)
        assert mem["u_bar"] == pytest.approx(18.0, abs=0.05)

    def test_u_tracks_slowly(self):
        """One step must not slew U: tau_u = 38 s, dt = 0.5 s."""
        _, mem = pi_saturation(_obs(v=5.0, dt=0.5), {}, {"u_bar": 25.0, "v_cmd_prev": 25.0})
        assert mem["u_bar"] == pytest.approx(25.0 + (5.0 - 25.0) * (0.5 / 38.0), abs=1e-9)


class TestNoRatchet:
    """The open-corridor failure of the mean-fraction variant must not recur."""

    def test_repeated_application_does_not_spiral_down(self):
        """Leader steady at 20 m/s: the command must not decay toward zero."""
        mem: dict[str, float] = {}
        cmd = 20.0
        for _ in range(4000):  # 2000 s at dt = 0.5
            cmd, mem = pi_saturation(_obs(v=cmd, gap=25.0, v_leader=20.0, v_ref=25.0), {}, mem)
        assert cmd > 19.0, f"ratcheted down to {cmd}"

    def test_meanfrac_variant_does_spiral(self):
        """Contrast case: the superseded variant ratchets when v_ref follows v."""
        from controllers import pi_meanfrac

        mem: dict[str, float] = {}
        v = 20.0
        for _ in range(200):
            # v_ref tracks the depressed platoon speed, as on an open corridor
            v, mem = pi_meanfrac(_obs(v=v, v_ref=v), {}, mem)
        assert v < 1.0, f"expected collapse, got {v}"


class TestSafetyAndBounds:
    def test_no_leader_uses_full_catch_up(self):
        cmd, _ = pi_saturation(
            _obs(v=20.0, gap=math.inf, v_leader=math.nan, v_ref=30.0),
            {},
            {"u_bar": 20.0, "v_cmd_prev": 20.0},
        )
        assert math.isfinite(cmd)
        assert cmd == pytest.approx(0.5 * 21.0 + 0.5 * 20.0, abs=1e-9)

    def test_stopped_leader_commands_stop(self):
        cmd, _ = pi_saturation(
            _obs(v=2.0, gap=3.0, v_leader=0.0, v_ref=25.0),
            {},
            {"u_bar": 20.0, "v_cmd_prev": 2.0},
        )
        assert cmd == pytest.approx(0.0, abs=1e-9)

    @settings(max_examples=200, deadline=None)
    @given(
        v=st.floats(0.0, 40.0),
        gap=st.floats(0.1, 200.0),
        v_lead=st.floats(0.0, 40.0),
        v_ref=st.floats(0.1, 40.0),
    )
    def test_output_finite_and_nonnegative(self, v, gap, v_lead, v_ref):
        cmd, mem = pi_saturation(_obs(v=v, gap=gap, v_leader=v_lead, v_ref=v_ref), {}, {})
        assert math.isfinite(cmd)
        assert cmd >= 0.0
        assert cmd <= max(v_ref, mem["u_bar"] + 1.0) + 1e-9

    def test_memory_is_json_serializable_floats(self):
        _, mem = pi_saturation(_obs(), {}, {})
        assert all(isinstance(k, str) and isinstance(v, float) for k, v in mem.items())
