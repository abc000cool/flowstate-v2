"""JAD tests: full phase cycle on a scripted obs sequence, rate limits, timing.

The scripted sequence emulates a wave 400 m downstream that later dissipates;
the ego's observed speed tracks the previous command (perfect low-level
tracking), so commanded decel/accel limits are exactly the realized ones.
"""

import math

import pytest

from controllers import default_params, jad
from controllers.jad import (
    JAD_PHASES,
    PHASE_CRUISE,
    PHASE_FAST_OUT,
    PHASE_HOLD,
    PHASE_SLOW_IN,
)
from flowstate_core.controller_types import ControllerObs, Memory
from flowstate_core.units import kmh_to_ms

FREE = 25.0  # free-flow bin speed [m/s], well above the 40 km/h threshold
JAM = 8.0  # jammed bin speed [m/s], below threshold
DT = 0.5


def _obs(
    v: float,
    downstream: tuple[float, ...],
    t: float,
    v_ref: float = 25.0,
) -> ControllerObs:
    return ControllerObs(
        t=t,
        dt=DT,
        v=v,
        gap=80.0,
        v_leader=v,
        v_ref=v_ref,
        downstream=downstream,
        downstream_dx=100.0,
    )


def _free_bins(n: int = 8) -> tuple[float, ...]:
    return (FREE,) * n


def _wave_bins(jam_at: int = 4, n: int = 8) -> tuple[float, ...]:
    return tuple(JAM if i == jam_at else FREE for i in range(n))


class TestDetection:
    def test_no_wave_stays_cruise_at_v_ref(self):
        mem: Memory = {}
        v = 25.0
        for k in range(20):
            cmd, mem = jad(_obs(v, _free_bins(), t=k * DT), {}, mem)
            v = cmd
            assert mem["phase"] == PHASE_CRUISE
            assert cmd == pytest.approx(25.0)

    def test_wave_beyond_lookahead_ignored(self):
        # Jam 2.5 km ahead with a 2 km lookahead → not detected.
        bins = tuple(FREE if i != 25 else JAM for i in range(30))
        _, mem = jad(_obs(25.0, bins, t=0.0), {}, {})
        assert mem["phase"] == PHASE_CRUISE

    def test_nan_bins_skipped(self):
        bins = (math.nan, FREE, math.nan, JAM, FREE)
        _, mem = jad(_obs(25.0, bins, t=0.0), {}, {})
        assert mem["phase"] == PHASE_SLOW_IN

    def test_threshold_is_40_kmh(self):
        just_above = kmh_to_ms(40.0) + 0.01
        just_below = kmh_to_ms(40.0) - 0.01
        _, mem = jad(_obs(25.0, (just_above,) * 4, t=0.0), {}, {})
        assert mem["phase"] == PHASE_CRUISE
        _, mem = jad(_obs(25.0, (just_below,) * 4, t=0.0), {}, {})
        assert mem["phase"] == PHASE_SLOW_IN


class TestFullPhaseCycle:
    def test_cruise_slow_in_hold_fast_out_cruise(self):
        p = default_params("jad")
        mem: Memory = {}
        v = 25.0
        t = 0.0
        phases: list[float] = []
        cmds: list[float] = []

        def step(downstream: tuple[float, ...]) -> None:
            nonlocal v, t, mem
            cmd, mem = jad(_obs(v, downstream, t=t), {}, mem)
            assert mem["phase"] in JAD_PHASES
            if cmds:  # commanded rate limits respected at every step
                dv = cmd - cmds[-1]
                assert dv >= -p["a_slow"] * DT - 1e-9
                assert dv <= p["a_out"] * DT + 1e-9
            phases.append(mem["phase"])
            cmds.append(cmd)
            v = cmd  # perfect low-level tracking
            t += DT

        # 10 free steps → CRUISE
        for _ in range(10):
            step(_free_bins())
        assert set(phases) == {PHASE_CRUISE}

        # wave appears 400 m ahead (bin 4) and persists for 40 steps (20 s)
        for _ in range(40):
            step(_wave_bins(jam_at=4))

        # slow-in target: β·v at trigger = 0.55·25 = 13.75 m/s
        v_slow = p["beta"] * 25.0
        assert mem["v_slow"] == pytest.approx(v_slow)
        assert PHASE_SLOW_IN in phases
        assert PHASE_HOLD in phases  # reached v_slow and held
        assert cmds[-1] == pytest.approx(v_slow)

        # wave dissipates → FAST_OUT ramps back to v_ref, then CRUISE
        for _ in range(40):
            step(_free_bins())
        assert PHASE_FAST_OUT in phases
        assert phases[-1] == PHASE_CRUISE
        assert cmds[-1] == pytest.approx(25.0)

        # phase codes appear in cycle order
        order = [phases[0]]
        for ph in phases[1:]:
            if ph != order[-1]:
                order.append(ph)
        assert order == [PHASE_CRUISE, PHASE_SLOW_IN, PHASE_HOLD, PHASE_FAST_OUT, PHASE_CRUISE]

    def test_slow_in_decel_is_exactly_rate_limited(self):
        mem: Memory = {}
        v = 25.0
        cmd0, mem = jad(_obs(v, _wave_bins(), t=0.0), {}, mem)
        # first slow-in step drops by exactly a_slow·dt from the ego speed
        assert cmd0 == pytest.approx(25.0 - 1.0 * DT)
        cmd1, mem = jad(_obs(cmd0, _wave_bins(), t=DT), {}, mem)
        assert cmd1 == pytest.approx(cmd0 - 1.0 * DT)


class TestInterceptTiming:
    def test_hold_times_out_at_estimated_intercept(self):
        """With no recovery, HOLD ends at t_int = x_w / (v_slow − w_wave)."""
        p = default_params("jad")
        v0 = 25.0
        v_slow = p["beta"] * v0
        x_w = 4 * 100.0  # jam bin 4, bin width 100 m
        t_int = x_w / (v_slow - p["w_wave"])

        mem: Memory = {}
        v = v0
        t = 0.0
        # trigger at t=0; wave never recovers in the observation
        left_hold_at = None
        for _ in range(200):
            cmd, mem = jad(_obs(v, _wave_bins(jam_at=4), t=t), {}, mem)
            if mem["phase"] == PHASE_FAST_OUT and left_hold_at is None:
                left_hold_at = t
                break
            v = cmd
            t += DT
        assert left_hold_at is not None
        assert mem["t_int_end"] == pytest.approx(t_int)  # triggered at t = 0
        # transition happens at the first step at/after the ceiling
        assert t_int - DT <= left_hold_at <= t_int + DT

    def test_recovery_during_slow_in_goes_fast_out(self):
        mem: Memory = {}
        _, mem = jad(_obs(25.0, _wave_bins(), t=0.0), {}, mem)
        assert mem["phase"] == PHASE_SLOW_IN
        _, mem = jad(_obs(24.5, _free_bins(), t=DT), {}, mem)
        assert mem["phase"] == PHASE_FAST_OUT


class TestMemoryContract:
    def test_memory_json_serializable_floats(self):
        import json

        _, mem = jad(_obs(25.0, _wave_bins(), t=0.0), {}, {})
        assert all(isinstance(val, float) for val in mem.values())
        json.dumps(mem)

    def test_input_memory_not_mutated(self):
        mem_in: Memory = {"phase": PHASE_CRUISE, "v_cmd_prev": 25.0}
        jad(_obs(25.0, _wave_bins(), t=0.0), {}, mem_in)
        assert mem_in == {"phase": PHASE_CRUISE, "v_cmd_prev": 25.0}


class TestDeferredCommitment:
    """ROADMAP B4: ``commit_delay_s`` defers slow-in until a detection persists."""

    def test_default_zero_delay_is_unchanged(self):
        p = default_params("jad")
        assert p["commit_delay_s"] == 0.0
        _, mem = jad(_obs(25.0, _wave_bins(), t=0.0), p, {})
        assert mem["phase"] == PHASE_SLOW_IN

    def test_commit_waits_for_persistent_detection(self):
        p = {**default_params("jad"), "commit_delay_s": 30.0}
        mem: Memory = {}
        v_cmd = None
        # A wave visible continuously from t = 0: no slow-in before 30 s.
        for k in range(60):  # 0 .. 29.5 s
            t = k * DT
            v_cmd, mem = jad(_obs(25.0, _wave_bins(), t=t), p, mem)
            assert mem["phase"] == PHASE_CRUISE, t
            assert v_cmd == pytest.approx(25.0)
        assert mem["detect_t"] == 0.0
        v_cmd, mem = jad(_obs(25.0, _wave_bins(), t=30.0), p, mem)
        assert mem["phase"] == PHASE_SLOW_IN
        assert "detect_t" not in mem
        assert v_cmd == pytest.approx(25.0 - p["a_slow"] * DT)

    def test_transient_detection_does_not_commit(self):
        p = {**default_params("jad"), "commit_delay_s": 30.0}
        mem: Memory = {}
        for k in range(40):  # wave for 20 s ...
            _, mem = jad(_obs(25.0, _wave_bins(), t=k * DT), p, mem)
        assert mem["phase"] == PHASE_CRUISE and mem["detect_t"] == 0.0
        _, mem = jad(_obs(25.0, _free_bins(), t=20.0), p, mem)  # ... then gone
        assert "detect_t" not in mem
        # It reappears: the clock restarts from the new first detection.
        _, mem = jad(_obs(25.0, _wave_bins(), t=25.0), p, mem)
        assert mem["detect_t"] == 25.0 and mem["phase"] == PHASE_CRUISE
        for k in range(1, 60):
            _, mem = jad(_obs(25.0, _wave_bins(), t=25.0 + k * DT), p, mem)
        assert mem["phase"] == PHASE_CRUISE  # 29.5 s of persistence
        _, mem = jad(_obs(25.0, _wave_bins(), t=55.0), p, mem)
        assert mem["phase"] == PHASE_SLOW_IN

    def test_delay_only_gates_cruise(self):
        # Once committed, the cycle runs as before regardless of the delay.
        p = {**default_params("jad"), "commit_delay_s": 10.0}
        mem: Memory = {}
        v = 25.0
        for k in range(21):
            v, mem = jad(_obs(v, _wave_bins(), t=k * DT), p, mem)
        assert mem["phase"] == PHASE_SLOW_IN
        seen = {mem["phase"]}
        for k in range(21, 200):
            v, mem = jad(_obs(v, _wave_bins(), t=k * DT), p, mem)
            seen.add(mem["phase"])
        # The full cycle ran (and re-armed, since the wave never clears).
        assert seen == set(JAD_PHASES)
