"""Property tests (hypothesis, derandomized): bounds, finiteness, purity.

CONTRACTS.md §8: controller outputs ∈ [0, U] — checked here as
``[0, max(U, v)]`` (JAD's rate limiter starts from the ego speed, which may
exceed U). Single-call-from-fresh-memory: multi-step behavior is covered by
the scripted sequence tests in the per-controller files.
"""

import json
import math

from hypothesis import given, seed, settings
from hypothesis import strategies as st

from controllers import ALL_VEHICLE_CONTROLLERS, vsl_threshold
from flowstate_core.controller_types import ControllerObs, SegmentObs

SPEED = st.floats(0.0, 50.0, allow_nan=False, allow_infinity=False)
BIN_SPEED = st.one_of(SPEED, st.just(math.nan))


@st.composite
def controller_obs(draw: st.DrawFn) -> ControllerObs:
    """A valid ControllerObs per docs/CONTRACTS.md §1 conventions."""
    has_leader = draw(st.booleans())
    if has_leader:
        gap = draw(st.floats(0.0, 500.0, allow_nan=False))
        v_leader = draw(SPEED)
    else:
        gap, v_leader = math.inf, math.nan
    n_bins = draw(st.integers(0, 25))
    return ControllerObs(
        t=draw(st.floats(0.0, 1e5, allow_nan=False)),
        dt=draw(st.floats(0.05, 2.0, allow_nan=False)),
        v=draw(SPEED),
        gap=gap,
        v_leader=v_leader,
        v_ref=draw(SPEED),
        downstream=tuple(draw(st.lists(BIN_SPEED, min_size=n_bins, max_size=n_bins))),
        downstream_dx=draw(st.floats(10.0, 500.0, allow_nan=False)),
    )


@st.composite
def segment_obs(draw: st.DrawFn) -> SegmentObs:
    n = draw(st.integers(1, 12))
    speeds = tuple(draw(st.lists(BIN_SPEED, min_size=n, max_size=n)))
    densities = tuple(draw(st.lists(st.floats(0.0, 0.2, allow_nan=False), min_size=n, max_size=n)))
    return SegmentObs(t=draw(st.floats(0.0, 1e5)), dt=30.0, seg_speed=speeds, seg_density=densities)


class TestVehicleControllerProperties:
    @seed(20260829)
    @settings(max_examples=250, derandomize=True)
    @given(obs=controller_obs(), name=st.sampled_from(sorted(ALL_VEHICLE_CONTROLLERS)))
    def test_output_bounded_finite_and_memory_serializable(self, obs: ControllerObs, name: str):
        fn = ALL_VEHICLE_CONTROLLERS[name]
        v_cmd, mem = fn(obs, {}, {})
        assert math.isfinite(v_cmd)
        assert 0.0 <= v_cmd <= max(obs.v_ref, obs.v) + 1e-9
        # memory is a JSON-serializable dict[str, float]
        assert all(isinstance(k, str) for k in mem)
        assert all(isinstance(v, float) and math.isfinite(v) for v in mem.values())
        json.dumps(mem)

    @seed(20260830)
    @settings(max_examples=100, derandomize=True)
    @given(obs=controller_obs(), name=st.sampled_from(sorted(ALL_VEHICLE_CONTROLLERS)))
    def test_pure_deterministic_and_no_input_mutation(self, obs: ControllerObs, name: str):
        fn = ALL_VEHICLE_CONTROLLERS[name]
        mem_in: dict[str, float] = {}
        out1 = fn(obs, {}, mem_in)
        out2 = fn(obs, {}, mem_in)
        assert out1 == out2
        assert mem_in == {}


class TestSegmentControllerProperties:
    @seed(20260831)
    @settings(max_examples=150, derandomize=True)
    @given(obs=segment_obs())
    def test_limits_positive_finite_one_per_segment(self, obs: SegmentObs):
        limits, mem = vsl_threshold(obs, {}, {})
        assert len(limits) == len(obs.seg_speed)
        assert all(math.isfinite(lim) and lim > 0.0 for lim in limits)
        assert all(isinstance(v, float) for v in mem.values())
        json.dumps(mem)
