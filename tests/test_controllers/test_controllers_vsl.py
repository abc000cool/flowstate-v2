"""VSL threshold controller tests: ladder, hysteresis (no chattering), memory.

Also covers the two pure application helpers of CLAUDE.md §4.4 that the
runners share: ``effective_limit`` (compliance scaling) and
``gantry_segments`` (0.5–1.0 km segmentation).
"""

import math
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from controllers import default_params, vsl_threshold
from controllers.vsl import VSL_SEGMENT_TARGET_M, effective_limit, gantry_segments
from flowstate_core.controller_types import Memory, SegmentObs
from flowstate_core.units import kmh_to_ms, veh_km_to_veh_m

FREE_RHO = veh_km_to_veh_m(10.0)  # well below rho_off
JAM_RHO = veh_km_to_veh_m(60.0)  # well above rho_on
FREE_V = kmh_to_ms(100.0)  # well above v_off


def _obs(speeds: tuple[float, ...], densities: tuple[float, ...], t: float = 0.0) -> SegmentObs:
    return SegmentObs(t=t, dt=30.0, seg_speed=speeds, seg_density=densities)


class TestLadder:
    def test_uncongested_posts_free_flow_cap_everywhere(self):
        p = default_params("vsl_threshold")
        limits, mem = vsl_threshold(_obs((FREE_V,) * 3, (FREE_RHO,) * 3), {}, {})
        assert limits == (p["v_free"],) * 3
        assert mem == {"seg_0": 0.0, "seg_1": 0.0, "seg_2": 0.0}

    def test_escalates_one_rung_per_call_down_the_ladder(self):
        p = default_params("vsl_threshold")
        ladder = [p[f"ladder_{k}"] for k in range(5)]
        # segment 1 (downstream of segment 0) fully jammed
        obs = _obs((FREE_V, kmh_to_ms(20.0), FREE_V), (FREE_RHO, JAM_RHO, FREE_RHO))
        mem: Memory = {}
        seg0_limits = []
        for _ in range(8):
            limits, mem = vsl_threshold(obs, {}, mem)
            seg0_limits.append(limits[0])
        # walks 90 → 80 → 70 → 60 → 50 km/h one rung per call, then saturates
        assert seg0_limits[:5] == ladder
        assert seg0_limits[5:] == [ladder[-1]] * 3
        assert mem["seg_0"] == 5.0

    def test_speed_ladder_default_is_90_to_50_kmh(self):
        p = default_params("vsl_threshold")
        assert [p[f"ladder_{k}"] for k in range(5)] == [
            kmh_to_ms(90.0),
            kmh_to_ms(80.0),
            kmh_to_ms(70.0),
            kmh_to_ms(60.0),
            kmh_to_ms(50.0),
        ]

    def test_density_alone_triggers_escalation(self):
        obs = _obs((FREE_V, FREE_V), (FREE_RHO, JAM_RHO))
        limits, _ = vsl_threshold(obs, {}, {})
        p = default_params("vsl_threshold")
        assert limits[0] == p["ladder_0"]

    def test_nan_speed_counts_as_uncongested(self):
        obs = _obs((FREE_V, math.nan), (FREE_RHO, FREE_RHO))
        limits, _ = vsl_threshold(obs, {}, {})
        assert limits[0] == default_params("vsl_threshold")["v_free"]

    def test_downstream_most_segment_relaxes_to_free(self):
        mem: Memory = {"seg_1": 3.0}
        limits, mem = vsl_threshold(_obs((FREE_V, FREE_V), (FREE_RHO, FREE_RHO)), {}, mem)
        assert mem["seg_1"] == 2.0  # de-escalates one rung per call
        p = default_params("vsl_threshold")
        assert limits[1] == p["ladder_1"]


class TestHysteresis:
    """Oscillation around a single threshold must not chatter the limit."""

    def test_no_chattering_around_v_on(self):
        p = default_params("vsl_threshold")
        just_below = p["v_on"] - kmh_to_ms(2.0)
        just_above = p["v_on"] + kmh_to_ms(2.0)  # still below v_off → hold
        mem: Memory = {}
        posted = []
        for k in range(20):
            speed1 = just_below if k % 2 == 0 else just_above
            limits, mem = vsl_threshold(_obs((FREE_V, speed1), (FREE_RHO, FREE_RHO)), {}, mem)
            posted.append(limits[0])
        # monotone non-increasing: it may keep stepping down, never bounce up
        assert all(b <= a for a, b in pairwise(posted))

    def test_no_chattering_around_v_off_during_recovery(self):
        p = default_params("vsl_threshold")
        just_below = p["v_off"] - kmh_to_ms(2.0)  # above v_on → hold
        just_above = p["v_off"] + kmh_to_ms(2.0)  # recovered → step up
        mem: Memory = {"seg_0": 5.0}
        posted = []
        for k in range(20):
            speed1 = just_below if k % 2 == 0 else just_above
            limits, mem = vsl_threshold(_obs((FREE_V, speed1), (FREE_RHO, FREE_RHO)), {}, mem)
            posted.append(limits[0])
        # monotone non-decreasing recovery, ending at the free-flow cap
        assert all(b >= a for a, b in pairwise(posted))
        assert posted[-1] == p["v_free"]

    def test_full_congestion_recovery_cycle_changes_direction_once(self):
        obs_jam = _obs((FREE_V, kmh_to_ms(20.0)), (FREE_RHO, JAM_RHO))
        obs_free = _obs((FREE_V, FREE_V), (FREE_RHO, FREE_RHO))
        mem: Memory = {}
        posted = []
        for _ in range(7):
            limits, mem = vsl_threshold(obs_jam, {}, mem)
            posted.append(limits[0])
        for _ in range(7):
            limits, mem = vsl_threshold(obs_free, {}, mem)
            posted.append(limits[0])
        directions = [(1 if b > a else -1) for a, b in pairwise(posted) if b != a]
        # all decreases first, then all increases — exactly one direction change
        assert sum(1 for d1, d2 in pairwise(directions) if d1 != d2) == 1
        assert mem["seg_0"] == 0.0


class TestContract:
    def test_one_limit_per_segment(self):
        for n in (1, 2, 5):
            limits, _ = vsl_threshold(_obs((FREE_V,) * n, (FREE_RHO,) * n), {}, {})
            assert len(limits) == n

    def test_memory_keys_are_seg_i_floats(self):
        import json

        _, mem = vsl_threshold(
            _obs((FREE_V, kmh_to_ms(20.0), FREE_V), (FREE_RHO, JAM_RHO, FREE_RHO)), {}, {}
        )
        assert set(mem) == {"seg_0", "seg_1", "seg_2"}
        assert all(isinstance(v, float) for v in mem.values())
        json.dumps(mem)

    def test_input_memory_not_mutated(self):
        mem_in: Memory = {"seg_0": 2.0}
        vsl_threshold(_obs((FREE_V, FREE_V), (FREE_RHO, FREE_RHO)), {}, mem_in)
        assert mem_in == {"seg_0": 2.0}

    def test_limits_always_within_ladder_or_free(self):
        p = default_params("vsl_threshold")
        valid = {p["v_free"]} | {p[f"ladder_{k}"] for k in range(5)}
        mem: Memory = {}
        for k in range(12):
            speeds = (FREE_V, kmh_to_ms(20.0) if k < 6 else FREE_V)
            limits, mem = vsl_threshold(_obs(speeds, (FREE_RHO, FREE_RHO)), {}, mem)
            assert set(limits) <= valid


class TestEffectiveLimit:
    """Fleet-average compliance scaling of a posted limit (CLAUDE.md §4.4)."""

    def test_full_compliance_returns_posted_exactly(self):
        posted, base = kmh_to_ms(50.0), 50.0
        assert effective_limit(posted, base, 1.0) == posted

    def test_zero_compliance_returns_base_exactly(self):
        assert effective_limit(kmh_to_ms(50.0), 50.0, 0.0) == 50.0

    def test_hand_computed_interpolation(self):
        # 0.5·20 + 0.5·40 = 30; 0.25·20 + 0.75·40 = 35
        assert effective_limit(20.0, 40.0, 0.5) == pytest.approx(30.0)
        assert effective_limit(20.0, 40.0, 0.25) == pytest.approx(35.0)

    def test_monotone_decreasing_in_compliance(self):
        grid = [k / 10.0 for k in range(11)]
        vals = [effective_limit(15.0, 45.0, c) for c in grid]
        assert all(b < a for a, b in pairwise(vals))

    def test_monotone_increasing_in_posted_limit(self):
        vals = [effective_limit(p, 40.0, 0.6) for p in (5.0, 10.0, 20.0, 30.0)]
        assert all(b > a for a, b in pairwise(vals))

    def test_never_above_base_limit(self):
        # A "free-flow cap" above the road's statutory limit is a no-op.
        assert effective_limit(kmh_to_ms(120.0), kmh_to_ms(112.0), 1.0) == kmh_to_ms(112.0)
        assert effective_limit(kmh_to_ms(120.0), kmh_to_ms(112.0), 0.3) == kmh_to_ms(112.0)
        assert effective_limit(30.0, 30.0, 0.5) == 30.0

    @given(
        posted=st.floats(0.0, 60.0),
        base=st.floats(0.0, 60.0),
        compliance=st.floats(0.0, 1.0),
    )
    def test_property_within_bounds(self, posted, base, compliance):
        v = effective_limit(posted, base, compliance)
        assert min(posted, base) <= v <= max(posted, base)
        assert v <= base

    def test_rejects_bad_inputs(self):
        with pytest.raises(ValueError, match="compliance"):
            effective_limit(10.0, 20.0, 1.5)
        with pytest.raises(ValueError, match="compliance"):
            effective_limit(10.0, 20.0, -0.1)
        with pytest.raises(ValueError, match=">= 0"):
            effective_limit(-1.0, 20.0, 0.5)


class TestGantrySegments:
    """Greedy 0.5–1.0 km grouping of consecutive edges/cells (CLAUDE.md §4.4)."""

    def test_default_target_is_1km(self):
        assert VSL_SEGMENT_TARGET_M == 1000.0

    def test_one_km_edges_stay_one_segment_each(self):
        assert gantry_segments([1000.0] * 10) == [(k, k + 1) for k in range(10)]

    def test_trailing_500m_edge_is_its_own_segment(self):
        assert gantry_segments([1000.0, 1000.0, 1000.0, 500.0]) == [(0, 1), (1, 2), (2, 3), (3, 4)]

    def test_short_remainder_joins_previous_segment(self):
        assert gantry_segments([1000.0, 1000.0, 300.0]) == [(0, 1), (1, 3)]

    def test_osm_like_short_ways_merge_without_splitting(self):
        lengths = [1500.0, 200.0, 300.0, 900.0, 100.0]
        bounds = gantry_segments(lengths)
        assert bounds == [(0, 1), (1, 5)]
        seg_len = [sum(lengths[a:b]) for a, b in bounds]
        assert seg_len == [1500.0, 1500.0]
        assert sum(seg_len) == sum(lengths)

    def test_600m_edges_pair_up(self):
        # 600 → closer to add (1200) than to close (600); 1200 → close.
        assert gantry_segments([600.0] * 4) == [(0, 2), (2, 4)]

    def test_three_400m_edges_form_one_segment(self):
        # (0,2) closes at 800 m; the trailing 400 m is below 500 m → merged.
        assert gantry_segments([400.0] * 3) == [(0, 3)]

    def test_ring_of_tiny_edges_is_one_segment(self):
        assert gantry_segments([230.0 / 8.0] * 8) == [(0, 8)]

    def test_long_edges_are_never_split(self):
        assert gantry_segments([2000.0]) == [(0, 1)]
        assert gantry_segments([3000.0, 3000.0]) == [(0, 1), (1, 2)]

    def test_empty_input(self):
        assert gantry_segments([]) == []

    @given(
        lengths=st.lists(st.floats(10.0, 3000.0), min_size=1, max_size=40),
        target=st.floats(200.0, 2000.0),
    )
    def test_property_partition_and_minimum_length(self, lengths, target):
        bounds = gantry_segments(lengths, target)
        # Contiguous half-open ranges covering every element once, in order.
        assert bounds[0][0] == 0 and bounds[-1][1] == len(lengths)
        assert all(a < b for a, b in bounds)
        assert all(b1 == a2 for (_, b1), (a2, _) in pairwise(bounds))
        assert sum(sum(lengths[a:b]) for a, b in bounds) == pytest.approx(sum(lengths))
        # Every segment is at least target/2 long unless the corridor is one segment.
        if len(bounds) > 1:
            assert all(sum(lengths[a:b]) >= target / 2.0 for a, b in bounds)

    def test_rejects_bad_inputs(self):
        with pytest.raises(ValueError, match="target_m"):
            gantry_segments([100.0], target_m=0.0)
        with pytest.raises(ValueError, match="min_m"):
            gantry_segments([100.0], target_m=1000.0, min_m=1500.0)
        with pytest.raises(ValueError, match="element length"):
            gantry_segments([100.0, 0.0])
