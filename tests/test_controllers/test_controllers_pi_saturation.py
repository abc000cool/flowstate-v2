"""PI-with-saturation tests: tracking, saturation, and anti-windup.

The anti-windup test drives the controller deep into saturation, then releases
it, and compares against a naive always-integrating PI (the v1 defect,
CLAUDE.md §4.2): the conditional-integration controller must keep its
integrator bounded and recover without an overshoot spike.
"""

from collections.abc import Mapping

import pytest

from controllers import default_params, pi_saturation
from flowstate_core.controller_types import ControllerObs, Memory


def _obs(v: float, v_ref: float, dt: float = 0.5) -> ControllerObs:
    return ControllerObs(t=0.0, dt=dt, v=v, gap=100.0, v_leader=v, v_ref=v_ref)


def _naive_pi(
    obs: ControllerObs, params: Mapping[str, float], memory: Memory
) -> tuple[float, Memory]:
    """Reference NAIVE PI (no anti-windup) — the v1 defect, for comparison."""
    p = {**default_params("pi_saturation"), **params}
    u = max(obs.v_ref, 0.0)
    v_target = p["alpha"] * u
    error = v_target - obs.v
    integral = memory.get("integral", 0.0) + error * obs.dt
    raw = v_target + p["kp"] * error + p["ki"] * integral
    return min(max(raw, 0.0), u), {"integral": integral}


class TestBasics:
    def test_zero_error_commands_target(self):
        # v == α·v_ref and empty integrator → command exactly the target.
        cmd, mem = pi_saturation(_obs(v=7.5, v_ref=10.0), {}, {})
        assert cmd == pytest.approx(7.5)
        assert mem["integral"] == pytest.approx(0.0)

    def test_proportional_direction(self):
        slow, _ = pi_saturation(_obs(v=7.0, v_ref=10.0), {}, {})
        fast, _ = pi_saturation(_obs(v=8.0, v_ref=10.0), {}, {})
        assert slow > 7.5 > fast  # push up when below target, down when above

    def test_output_clamped_to_0_u(self):
        hi, _ = pi_saturation(_obs(v=0.0, v_ref=10.0), {}, {})
        lo, _ = pi_saturation(_obs(v=50.0, v_ref=10.0), {}, {})
        assert hi <= 10.0
        assert lo >= 0.0

    def test_integrator_accumulates_when_unsaturated(self):
        obs = _obs(v=7.4, v_ref=10.0)  # small +0.1 error, well inside [0, U]
        _, mem = pi_saturation(obs, {}, {})
        assert mem["integral"] == pytest.approx(0.1 * obs.dt)
        _, mem = pi_saturation(obs, {}, mem)
        assert mem["integral"] == pytest.approx(0.2 * obs.dt)

    def test_memory_not_mutated(self):
        mem_in: Memory = {"integral": 1.0}
        pi_saturation(_obs(v=7.4, v_ref=10.0), {}, mem_in)
        assert mem_in == {"integral": 1.0}


class TestAntiWindup:
    """Conditional integration: never integrate while pushing past a limit."""

    def test_integrator_bounded_in_saturation_and_no_overshoot_on_release(self):
        v_ref, dt, n_sat = 10.0, 0.5, 100
        v_target = default_params("pi_saturation")["alpha"] * v_ref  # 7.5

        # Phase 1: ego stuck at 0 → large positive error → command saturates at U.
        mem_aw: Memory = {}
        mem_nv: Memory = {}
        for _ in range(n_sat):
            obs = _obs(v=0.0, v_ref=v_ref, dt=dt)
            cmd_aw, mem_aw = pi_saturation(obs, {}, mem_aw)
            cmd_nv, mem_nv = _naive_pi(obs, {}, mem_nv)
            assert cmd_aw == pytest.approx(v_ref)  # saturated at U
        # Anti-windup integrator stayed bounded; naive one wound up hugely.
        error = v_target - 0.0
        assert abs(mem_aw["integral"]) <= error * dt + 1e-12  # at most one pre-sat step
        assert mem_nv["integral"] == pytest.approx(error * dt * n_sat)
        assert mem_nv["integral"] > 100.0 * abs(mem_aw["integral"]) + 1.0

        # Phase 2 (release): ego jumps to the target speed → zero error.
        obs = _obs(v=v_target, v_ref=v_ref, dt=dt)
        cmd_aw, _ = pi_saturation(obs, {}, mem_aw)
        cmd_nv, _ = _naive_pi(obs, {}, mem_nv)
        # Recovery without overshoot spike: command is back at the target...
        assert cmd_aw == pytest.approx(v_target, abs=1e-9)
        # ...while the naive integrator still slams the command into the ceiling.
        assert cmd_nv > v_target + 1.0

    def test_no_windup_at_lower_saturation(self):
        # Ego far above target → command pinned at 0, error negative: frozen.
        mem: Memory = {}
        for _ in range(50):
            _, mem = pi_saturation(_obs(v=50.0, v_ref=10.0, dt=0.5), {}, mem)
        obs = _obs(v=7.5, v_ref=10.0, dt=0.5)
        cmd, _ = pi_saturation(obs, {}, mem)
        # A wound-down integrator would drag the command far below target.
        assert cmd == pytest.approx(7.5, abs=1e-6)
