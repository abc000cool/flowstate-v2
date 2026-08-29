"""VSL threshold controller tests: ladder, hysteresis (no chattering), memory."""

import math
from itertools import pairwise

from controllers import default_params, vsl_threshold
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
