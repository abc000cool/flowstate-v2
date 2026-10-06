"""The measured merge model's pure functions (``microsim.merge_model``; docs/MERGE_MODEL.md).

No SUMO: every function here is controller-style and is tested on
hand-computed cases (B§5.13 item 1): the seeded driver draws (a KS test
against the truncated log-normal), the acceptance on the
``calibration.lane_change_gaps`` definitions with the ``minGap`` add-back,
the brake guards against the scripted merge's guard, the speed ceiling with
the speed factor, the relaxation schedule (start, floors, restore,
re-grant), the mandatory-changer geometry and the opposing-entry resolution.
"""

from __future__ import annotations

import json
import math
from itertools import pairwise
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pytest

from microsim import merge_model as mm
from microsim.runner import _scripted_force_gap_ok
from microsim.vehicles import merge_gap_stream, speed_factor_stream

REPO = Path(__file__).resolve().parents[2]
ARTIFACT = REPO / "artifacts" / "merge_model_params.json"


def _params(**kw: float) -> mm.MergeModelParams:
    base = {
        "name": "test",
        "lead": {
            "entering_merge": mm.LogNormal(math.log(0.42), 1.42),
            "entering_weave": mm.LogNormal(math.log(0.46), 1.44),
            "exiting_weave": None,
        },
        "lag": {
            "entering_merge": mm.LogNormal(math.log(0.59), 1.46),
            "entering_weave": mm.LogNormal(math.log(0.92), 1.28),
            "exiting_weave": mm.LogNormal(math.log(1.11), 1.90),
        },
        "delta_merge_ms": 0.6,
        "delta_weave_ms": 1.0,
        "tau_r_s": 7.5,
    }
    base.update(kw)
    return mm.MergeModelParams(**base)  # type: ignore[arg-type]


class TestDraws:
    def test_quantiles_are_seeded_truncated_and_independent_of_the_run_stream(self):
        rng = np.random.default_rng(np.random.SeedSequence(12345))
        before = rng.bit_generator.state
        z1 = mm.driver_quantiles(merge_gap_stream(rng), 500)
        z2 = mm.driver_quantiles(merge_gap_stream(rng), 500)
        # the child stream neither consumes nor advances the run's generator
        assert rng.bit_generator.state == before
        np.testing.assert_array_equal(z1[0], z2[0])
        np.testing.assert_array_equal(z1[1], z2[1])
        for z in z1:
            assert z.shape == (500,)
            assert np.all(np.abs(z) <= mm.TRUNCATION_Z + 1e-12)
        # a different child than the speed-factor stream
        other = speed_factor_stream(rng).uniform(size=4)
        assert not np.allclose(other, merge_gap_stream(rng).uniform(size=4))
        # the first n draws do not depend on n (fixed variates per vehicle)
        z_small = mm.driver_quantiles(merge_gap_stream(rng), 10)
        np.testing.assert_array_equal(z_small[0], z1[0][:10])

    def test_quantiles_follow_the_truncated_standard_normal(self):
        """KS against the truncated normal's CDF (n = 20,000; α = 0.001, critical value 1.95/√n:
        the draw is fixed, so the bound guards against a wrong distribution, not chance)."""
        z_lead, z_lag = mm.driver_quantiles(np.random.default_rng(7), 20_000)
        nd = NormalDist()
        zmax = mm.TRUNCATION_Z
        lo, hi = nd.cdf(-zmax), nd.cdf(zmax)
        for z in (z_lead, z_lag):
            xs = np.sort(z)
            cdf = (np.array([nd.cdf(float(x)) for x in xs]) - lo) / (hi - lo)
            n = len(xs)
            d = max(
                float(np.max(np.arange(1, n + 1) / n - cdf)),
                float(np.max(cdf - np.arange(0, n) / n)),
            )
            assert d < 1.95 / math.sqrt(n), d
        # the two sides are drawn independently
        assert abs(float(np.corrcoef(z_lead, z_lag)[0, 1])) < 0.03

    def test_driver_gaps_map_one_quantile_per_side_through_each_movement(self):
        p = _params()
        g = mm.driver_gaps(p, 0.0, 0.0)
        assert g.lead["entering_merge"] == pytest.approx(0.42)
        assert g.lag["entering_weave"] == pytest.approx(0.92)
        assert g.lead["exiting_weave"] is None  # no lead time gate for exiters
        g_hi = mm.driver_gaps(p, mm.TRUNCATION_Z, -mm.TRUNCATION_Z)
        assert g_hi.lead["entering_merge"] == pytest.approx(
            p.lead["entering_merge"].bounds()[1]  # type: ignore[union-attr]
        )
        assert g_hi.lag["exiting_weave"] == pytest.approx(p.lag["exiting_weave"].bounds()[0])
        # a consistent driver: short at a merge is short in a weave
        g_lo = mm.driver_gaps(p, -1.0, -1.0)
        assert g_lo.lead["entering_merge"] < 0.42 and g_lo.lead["entering_weave"] < 0.46

    def test_lognormal_bounds_are_p2_5_and_p97_5(self):
        d = mm.LogNormal(math.log(0.59), 1.46)
        lo, hi = d.bounds()
        assert lo == pytest.approx(0.59 * math.exp(-1.46 * 1.959964), rel=1e-5)
        assert hi == pytest.approx(0.59 * math.exp(1.46 * 1.959964), rel=1e-5)


class TestAcceptance:
    def test_lead_time_gap_adds_the_changers_mingap_back(self):
        # changer at 10 m/s, s0 2.5 m; SUMO reports the leader gap net of the
        # changer's minGap: 17.5 m net = 20 m bumper to bumper = 2.0 s
        assert mm.lead_time_ok(17.5, 2.5, 10.0, 2.0)
        assert not mm.lead_time_ok(17.5, 2.5, 10.0, 2.01)
        assert mm.lead_time_ok(math.inf, 2.5, 10.0, 99.0)

    def test_lag_time_gap_reads_the_followers_speed_and_mingap(self):
        # lag gap over the FOLLOWER's speed (calibration.lane_change_gaps):
        # 9 m net + the follower's 1.0 m minGap = 10 m at 20 m/s = 0.5 s
        assert mm.lag_time_ok(9.0, 1.0, 20.0, 0.5)
        assert not mm.lag_time_ok(9.0, 1.0, 20.0, 0.51)
        assert mm.lag_time_ok(math.inf, 1.0, 20.0, 9.0)

    def test_definitions_match_the_extractors_time_gaps(self):
        """The time gaps the extractor records (gap / changer speed, gap / follower speed)."""
        x_c, len_c, s0_c, v_c = 100.0, 5.0, 2.2, 12.0
        x_l_rear, v_l = 118.0, 11.0
        x_f, s0_f, v_f = 88.0, 1.7, 13.0
        lead_bb = x_l_rear - x_c
        lag_bb = (x_c - len_c) - x_f
        lead_tg, lag_tg = lead_bb / v_c, lag_bb / v_f
        g_lead_net, g_foll_net = lead_bb - s0_c, lag_bb - s0_f
        assert mm.lead_time_ok(g_lead_net, s0_c, v_c, lead_tg)
        assert not mm.lead_time_ok(g_lead_net, s0_c, v_c, lead_tg + 1e-9)
        assert mm.lag_time_ok(g_foll_net, s0_f, v_f, lag_tg)
        assert not mm.lag_time_ok(g_foll_net, s0_f, v_f, lag_tg + 1e-9)
        del v_l

    @pytest.mark.parametrize("seed", range(5))
    def test_brake_guards_are_the_scripted_merges_forced_guard(self, seed):
        rng = np.random.default_rng(seed)
        for _ in range(500):
            v, vl, vf = rng.uniform(0, 30, size=3)
            gl, gf = rng.uniform(-2, 60, size=2)
            b, bf = rng.uniform(0.5, 3, size=2)
            ours = mm.brake_guard_ok(gl, v, vl, b, 0.5) and mm.brake_guard_ok(gf, vf, v, bf, 0.5)
            assert ours == _scripted_force_gap_ok(v, gl, vl, gf, vf, b, bf, 0.5)

    def test_brake_guard_hand_cases(self):
        # equal speeds: SUMO's overlap test, a positive net gap
        assert mm.brake_guard_ok(0.01, 10.0, 10.0, 1.5, 0.5)
        assert not mm.brake_guard_ok(0.0, 10.0, 10.0, 1.5, 0.5)
        # WP-93's traced collision: 4.5 m ahead of a follower 9.8 m/s faster
        assert not mm.brake_guard_ok(4.5, 19.8, 10.0, 1.67, 0.5)
        # closing 2 m/s at b = 2: needs g > 2·0.5 + 4/4 = 2 m
        assert not mm.brake_guard_ok(2.0, 12.0, 10.0, 2.0, 0.5)
        assert mm.brake_guard_ok(2.01, 12.0, 10.0, 2.0, 0.5)
        # a front vehicle faster than the rear one: positive gap suffices
        assert mm.brake_guard_ok(0.5, 10.0, 15.0, 1.0, 0.5)
        assert mm.brake_guard_ok(math.inf, 30.0, math.nan, 1.0, 0.5)

    def test_acceptance_sides_and_the_refusal_order(self):
        common = {"v_c": 10.0, "s0_c": 2.0, "b_c": 1.5, "s0_f": 2.0, "b_f": 1.5, "step_s": 0.5}
        ok = mm.acceptance(
            **common,
            t_c_lead=0.5,
            t_c_lag=0.6,
            g_lead=20.0,
            v_lead=10.0,
            g_foll=20.0,
            v_foll=10.0,
        )
        assert ok.static_ok and ok.accepted and ok.refusal() is None and ok.lag_model is None
        lead = mm.acceptance(
            **common, t_c_lead=3.0, t_c_lag=0.6, g_lead=20.0, v_lead=10.0, g_foll=20, v_foll=10
        )
        assert not lead.accepted and lead.refusal() == "lead_time"
        # no lead time gate for an exiter: a short lead gap passes on the guard
        exit_ = mm.acceptance(
            **common, t_c_lead=None, t_c_lag=0.6, g_lead=1.0, v_lead=10.0, g_foll=20, v_foll=10
        )
        assert exit_.lead_time and exit_.lead_guard and exit_.accepted
        lag = mm.acceptance(
            **common, t_c_lead=0.5, t_c_lag=5.0, g_lead=20.0, v_lead=10.0, g_foll=20, v_foll=10
        )
        assert lag.refusal() == "lag_time"
        fast_f = mm.acceptance(
            **common, t_c_lead=0.5, t_c_lag=0.1, g_lead=20.0, v_lead=10.0, g_foll=5, v_foll=20
        )
        assert fast_f.lag_time and not fast_f.lag_guard and fast_f.refusal() == "lag_guard"
        assert not fast_f.guards_ok
        model = mm.Acceptance(True, True, True, True, lag_model=False)
        assert not model.accepted and model.refusal() == "lag_model" and model.guards_ok

    def test_follow_speed_check_is_the_followers_b(self):
        assert mm.follow_speed_ok(19.25, 20.0, 1.5, 0.5)
        assert not mm.follow_speed_ok(19.24, 20.0, 1.5, 0.5)


class TestSpeed:
    def test_desired_speed_honours_the_speed_factor_and_the_cap(self):
        assert mm.desired_speed(1.245, 24.59, 40.0) == pytest.approx(30.61455)
        assert mm.desired_speed(1.245, 24.59, 28.0) == 28.0
        assert mm.desired_speed(1.0, 24.59, 40.0) == 24.59

    def test_ceiling_is_kinematic_and_floored(self):
        # abreast of the gap: the gap's speed plus delta
        assert mm.speed_ceiling(10.0, 0.6, 1.5, 0.0, 30.0) == pytest.approx(10.6)
        # within braking distance: v = sqrt((v_gap + d)^2 + 2 b d)
        assert mm.speed_ceiling(10.0, 0.6, 1.5, 20.0, 30.0) == pytest.approx(
            math.sqrt(10.6**2 + 60.0)
        )
        # far away: the desired speed binds
        assert mm.speed_ceiling(10.0, 0.6, 1.5, 1000.0, 30.0) == 30.0
        # a stopped target lane: the creep floor
        assert mm.speed_ceiling(0.0, 0.0, 1.5, 0.0, 30.0) == mm.CREEP_MS
        # past the gap's position counts as abreast
        assert mm.speed_ceiling(10.0, 1.0, 1.5, -5.0, 30.0) == pytest.approx(11.0)

    def test_gap_reference_speed(self):
        assert mm.gap_reference_speed(12.0, [5.0], 30.0, True) == 12.0
        assert mm.gap_reference_speed(None, [5.0], 30.0, True) is None  # open road ahead
        assert mm.gap_reference_speed(None, [5.0, 7.0], 30.0, False) == 6.0
        assert mm.gap_reference_speed(None, [], 30.0, False) == 30.0


class TestRelaxation:
    def test_start_equals_the_accepted_gap_clamped(self):
        # gap 10 m net at 20 m/s: T = 0.5 s; T_i 1.4 → floor 0.7
        assert mm.relaxation_start(10.0, 20.0, 1.4, 0.5) == pytest.approx(0.7)
        assert mm.relaxation_start(20.0, 20.0, 1.4, 0.5) == pytest.approx(1.0)
        assert mm.relaxation_start(40.0, 20.0, 1.4, 0.5) == pytest.approx(1.4)
        # the step length floors a short T_i (SUMO's tau warning)
        assert mm.relaxation_start(1.0, 20.0, 0.8, 0.5) == pytest.approx(0.5)
        # at rest the headway sets no gap: T_i
        assert mm.relaxation_start(1.0, 0.0, 1.4, 0.5) == 1.4
        # an overlapping gap is floored, never negative
        assert mm.relaxation_start(-3.0, 10.0, 1.4, 0.5) == pytest.approx(0.7)

    def test_schedule_recovers_exponentially_and_is_restored_at_four_tau_r(self):
        t_i, t0, tau_r = 1.4, 0.7, 7.5
        assert mm.relaxed_tau(t_i, t0, 0.0, tau_r, 0.5) == pytest.approx(0.7)
        at_tau = mm.relaxed_tau(t_i, t0, tau_r, tau_r, 0.5)
        assert at_tau == pytest.approx(t_i - (t_i - t0) * math.exp(-1.0))
        assert mm.relaxed_tau(t_i, t0, 4 * tau_r, tau_r, 0.5) == pytest.approx(
            t_i - 0.7 * math.exp(-4.0)
        )
        assert not mm.relaxation_expired(4 * tau_r - 1e-9, tau_r)
        assert mm.relaxation_expired(4 * tau_r, tau_r)
        # monotone recovery, never below the floor
        vals = [mm.relaxed_tau(t_i, t0, s, tau_r, 0.5) for s in np.linspace(0, 40, 81)]
        assert all(b >= a for a, b in pairwise(vals))
        assert min(vals) >= mm.tau_floor(t_i, 0.5)

    def test_regrant_takes_the_smaller_t(self):
        assert mm.regrant(1.0, 0.8)
        assert not mm.regrant(0.8, 1.0)
        assert not mm.regrant(0.8, 0.8)

    def test_state_reads_the_schedule(self):
        p = _params(tau_r_s=5.0)
        rx = mm.RelaxationState(
            t_own=1.2, t_start=0.6, granted_s=100.0, tau_set=0.6, role="entrant", zone=0
        )
        assert rx.tau_at(100.0, p, 0.5) == pytest.approx(0.6)
        assert rx.tau_at(105.0, p, 0.5) == pytest.approx(1.2 - 0.6 * math.exp(-1.0))

    def test_car_following_range(self):
        assert mm.in_car_following_range(10.0, 0.0)
        assert mm.in_car_following_range(110.0, 20.0)
        assert not mm.in_car_following_range(110.1, 20.0)


class TestMandatoryChangers:
    @staticmethod
    def _net(conns: dict[tuple[str, int], list[tuple[str, int]]], lanes: dict[str, int]):
        return (lambda e, j: conns.get((e, j), []), lambda e: lanes[e])

    def test_acceleration_lane_dead_end(self):
        out, n = self._net({("A", 1): [("B", 0)], ("A", 2): [("B", 1)]}, {"A": 3})
        reach = mm.lane_reach(["A"], out, n)
        assert reach["A"][0] == frozenset()
        assert mm.mandatory_direction(reach["A"], 0, "B") == 1
        assert mm.mandatory_direction(reach["A"], 1, "B") == 0

    def test_weave_both_movements(self):
        # lane 0 to the exit X only; lanes 1-2 through to B
        out, n = self._net(
            {("A", 0): [("X", 0)], ("A", 1): [("B", 0)], ("A", 2): [("B", 1)]}, {"A": 3}
        )
        reach = mm.lane_reach(["A"], out, n)
        assert mm.mandatory_direction(reach["A"], 0, "B") == 1  # entrant
        assert mm.mandatory_direction(reach["A"], 2, "X") == -1  # exiter
        assert mm.mandatory_direction(reach["A"], 1, "X") == -1
        assert mm.mandatory_direction(reach["A"], 0, "X") == 0
        assert mm.mandatory_direction(reach["A"], 1, "Z") == 0  # not this zone's route

    def test_two_auxiliary_lanes_and_a_multi_edge_section(self):
        # T.H.61-like: lanes 0 and 1 of A lead only to the exit; the section has
        # a second edge whose lane 0 continues lane 0 of the first
        out, n = self._net(
            {
                ("A", 0): [("A2", 0)],
                ("A", 1): [("A2", 1)],
                ("A", 2): [("A2", 2)],
                ("A2", 0): [("X", 0)],
                ("A2", 1): [("X", 1)],
                ("A2", 2): [("B", 0)],
            },
            {"A": 3, "A2": 3},
        )
        reach = mm.lane_reach(["A", "A2"], out, n)
        assert reach["A"][0] == reach["A"][1] == frozenset({"X"})
        assert reach["A"][2] == frozenset({"B"})
        assert mm.mandatory_direction(reach["A"], 0, "B") == 1
        assert mm.mandatory_direction(reach["A"], 1, "B") == 1
        assert mm.mandatory_direction(reach["A2"], 2, "X") == -1


class TestOpposingEntries:
    @staticmethod
    def _rq(vid, x, lane, target, due=False, v=10.0):
        return mm.ChangeRequest(vid, x, lane, target, due, 0.6, v, 2.0, 1.5)

    def test_conflict_distance(self):
        rq = self._rq("r", 100.0, 0, 1)
        # at parity: fronts within len_P + 2 s0_R
        assert mm.opposing_conflict(rq, mm.LaneVehicle("p", 109.0, 5.0, 10.0))
        assert not mm.opposing_conflict(rq, mm.LaneVehicle("p", 109.01, 5.0, 10.0))
        # closing 4 m/s at b 1.5: + max(0.6 · 4, 16/3)
        fast = self._rq("r", 100.0, 0, 1, v=14.0)
        assert mm.opposing_conflict(fast, mm.LaneVehicle("p", 114.0, 5.0, 10.0))

    def test_equal_priority_the_one_ahead_goes(self):
        r = self._rq("r", 100.0, 0, 1)
        p = self._rq("p", 104.0, 2, 1)
        lanes = {
            0: [mm.LaneVehicle("r", 100.0, 5.0, 10.0)],
            2: [mm.LaneVehicle("p", 104.0, 5.0, 10.0)],
        }
        withheld, vetoed = mm.resolve_opposing([r, p], lanes, lambda _v: "driven")
        assert withheld == {"r"} and vetoed == set()

    def test_a_due_forced_change_has_priority(self):
        r = self._rq("r", 100.0, 0, 1, due=True)
        p = self._rq("p", 104.0, 2, 1)
        lanes = {
            0: [mm.LaneVehicle("r", 100.0, 5.0, 10.0)],
            2: [mm.LaneVehicle("p", 104.0, 5.0, 10.0)],
        }
        withheld, _ = mm.resolve_opposing([r, p], lanes, lambda _v: "driven")
        assert withheld == {"p"}

    def test_undriven_opponents(self):
        r = self._rq("r", 100.0, 0, 1)
        lanes = {2: [mm.LaneVehicle("p", 103.0, 5.0, 10.0)]}
        assert mm.resolve_opposing([r], lanes, lambda _v: "model") == (set(), {"p"})
        assert mm.resolve_opposing([r], lanes, lambda _v: "held") == ({"r"}, set())
        assert mm.resolve_opposing([r], lanes, lambda _v: "open") == ({"r"}, set())
        assert mm.resolve_opposing([r], lanes, lambda _v: "driven") == (set(), set())
        # far ahead, or behind: no conflict
        far = {2: [mm.LaneVehicle("p", 200.0, 5.0, 10.0), mm.LaneVehicle("q", 90.0, 5, 10)]}
        assert mm.resolve_opposing([r], far, lambda _v: "model") == (set(), set())

    def test_a_tie_counts_as_ahead_only_from_the_lower_lane(self):
        # R moves right (2 → 1); P level with it in lane 0 executes first
        r = self._rq("r", 100.0, 2, 1)
        lanes = {0: [mm.LaneVehicle("p", 100.0, 5.0, 10.0)]}
        assert mm.resolve_opposing([r], lanes, lambda _v: "model") == (set(), {"p"})
        # R moves left (0 → 1); P level with it in lane 2 executes after it
        r2 = self._rq("r", 100.0, 0, 1)
        lanes2 = {2: [mm.LaneVehicle("p", 100.0, 5.0, 10.0)]}
        assert mm.resolve_opposing([r2], lanes2, lambda _v: "model") == (set(), set())


class TestParameterArtifact:
    def test_every_set_loads_with_its_provenance(self):
        art = json.loads(ARTIFACT.read_text())
        assert set(art["sets"]) == set(mm.PARAMETER_SETS)
        assert all(c["ok"] for c in art["spec_checks"])
        for name in mm.PARAMETER_SETS:
            p = mm.load_params(ARTIFACT, name)
            assert p.name == name and len(p.artifact_sha256) == 64
            assert p.lead["exiting_weave"] is None
            assert p.relax_floor_fraction == 0.5 and p.relax_restore_tau_r == 4.0
            assert p.truncation_z == pytest.approx(mm.TRUNCATION_Z)
        central = mm.load_params(ARTIFACT)
        assert round(central.lead["entering_merge"].median_s, 2) == 0.42  # type: ignore[union-attr]
        assert round(central.lag["entering_merge"].median_s, 2) == 0.59
        assert round(central.lag["exiting_weave"].median_s, 2) == 1.11
        assert round(central.delta_merge_ms, 1) == 0.6 and round(central.delta_weave_ms, 1) == 1.0
        assert round(central.tau_r_s, 1) == 7.5
        assert mm.load_params(ARTIFACT, "delta_zero").delta(True) == 0.0
        assert round(mm.load_params(ARTIFACT, "tau_r_low").tau_r_s, 1) == 5.2
        assert round(mm.load_params(ARTIFACT, "tau_r_high").tau_r_s, 1) == 15.9
        us = mm.load_params(ARTIFACT, "us101_gaps")
        assert round(us.lead["entering_merge"].median_s, 2) == 0.29  # type: ignore[union-attr]
        assert round(us.lag["entering_weave"].median_s, 2) == 0.45
        assert round(us.lag["exiting_weave"].median_s, 2) == 0.54
        # US-101 holds medians only: sigma is the central set's
        assert us.lag["entering_merge"].sigma == central.lag["entering_merge"].sigma
        with pytest.raises(ValueError, match="no parameter set"):
            mm.load_params(ARTIFACT, "bogus")

    def test_provenance_names_committed_artifacts_with_their_hashes(self):
        art = json.loads(ARTIFACT.read_text())
        for entry in art["inputs"]:
            path = REPO / entry["artifact"]
            assert path.is_file()
            assert mm.file_sha256(path) == entry["sha256"], entry["artifact"]
        lead = art["sets"]["central"]["critical_gaps"]["entering_merge"]["lead"]
        assert lead["provenance"]["json_path"].startswith("fits[")
        assert mm.params_artifact_path() == ARTIFACT


# --- the runner's relaxation bookkeeping on a fake SUMO -------------------------


class _FakeVehicle:
    """The TraCI calls the relaxation reads and writes."""

    def __init__(self, taus: dict[str, float]) -> None:
        self.tau = dict(taus)
        self.writes: list[tuple[str, float]] = []

    def setTau(self, vid: str, tau: float) -> None:
        self.tau[vid] = tau
        self.writes.append((vid, tau))

    def getTau(self, vid: str) -> float:
        return self.tau[vid]

    def getLength(self, vid: str) -> float:
        return 5.0

    def getAccel(self, vid: str) -> float:
        return 1.0

    def getDecel(self, vid: str) -> float:
        return 1.5

    def getMinGap(self, vid: str) -> float:
        return 2.0

    def getMaxSpeed(self, vid: str) -> float:
        return 30.0

    def getSpeedFactor(self, vid: str) -> float:
        return 1.0

    def getLeader(self, vid: str, dist: float):
        return None


class _FakeMod:
    def __init__(self, taus: dict[str, float]) -> None:
        self.vehicle = _FakeVehicle(taus)


def _run_state(av: tuple[str, ...] = ()) -> dict:
    from microsim.runner import _measured_run_state
    from microsim.vehicles import FleetPlan

    plan = FleetPlan(params=(), is_av=(), complied=(), depart_s=(), depart_pos_m=())
    succ = {("e", 1): frozenset({("f", 1)}), ("e", 0): frozenset({("f", 0)})}
    return _measured_run_state(_params(tau_r_s=5.0), plan, av, succ, 0.5)


def _res(road: str, lane: int) -> dict:
    from traci import constants as tc

    return {tc.VAR_ROAD_ID: road, tc.VAR_LANE_INDEX: lane}


class TestRunnerRelaxation:
    def test_grant_sets_the_floored_start_and_updates_the_shared_constants(self):
        from microsim.runner import _measured_grant, _mm_veh

        mod, run = _FakeMod({"c": 1.4, "av": 1.4}), _run_state(av=("av",))
        p = _mm_veh(mod, run, "c")
        # 10 m net at 20 m/s: 0.5 s, floored at 0.5 * 1.4 = 0.7 s
        t0 = _measured_grant(mod, run, "c", 10.0, 20.0, 100.0, "entrant", 0, ("e", 1))
        assert t0 == pytest.approx(0.7) and mod.vehicle.tau["c"] == pytest.approx(0.7)
        assert p["T"] == pytest.approx(0.7) and p["T0"] == pytest.approx(1.4)
        assert run["n_relax_granted_entrant"] == 1 and run["min_tau_set_s"] == pytest.approx(0.7)
        # an AV is exempt; a normal gap needs nothing
        assert _measured_grant(mod, run, "av", 1.0, 20.0, 100.0, "follower", 0, ("e", 0)) is None
        assert "av" not in run["relax"] and mod.vehicle.tau["av"] == 1.4
        mod.vehicle.tau["n"] = 1.4
        assert _measured_grant(mod, run, "n", 40.0, 20.0, 100.0, "follower", 0, ("e", 0)) is None

    def test_regrant_takes_the_smaller_t_only(self):
        from microsim.runner import _measured_grant

        mod, run = _FakeMod({"f": 1.6}), _run_state()
        assert _measured_grant(mod, run, "f", 24.0, 20.0, 10.0, "follower", 0, ("e", 1))
        assert run["relax"]["f"].t_start == pytest.approx(1.2)
        # a longer start does not re-grant; a shorter one does, from now
        assert _measured_grant(mod, run, "f", 30.0, 20.0, 11.0, "follower", 0, ("e", 1)) is None
        assert _measured_grant(mod, run, "f", 18.0, 20.0, 12.0, "follower", 0, ("e", 1))
        assert run["relax"]["f"].t_start == pytest.approx(0.9)
        assert run["relax"]["f"].granted_s == 12.0 and run["n_relax_regranted"] == 1

    def test_schedule_lane_change_edge_change_expiry_and_departure(self):
        from traci import constants as tc

        from microsim.runner import _measured_grant, _measured_relax_step

        mod, run = _FakeMod({"a": 1.4, "b": 1.4, "c": 1.4, "d": 1.4}), _run_state()
        for vid in "abcd":
            _measured_grant(mod, run, vid, 10.0, 20.0, 0.0, "entrant", 0, ("e", 1))
        # one step later on the same lane: the exponential schedule
        results = {v: _res("e", 1) for v in "abcd"}
        _measured_relax_step(mod, tc, run, results, 1.0)
        want = 1.4 - (1.4 - 0.7) * math.exp(-1.0 / 5.0)
        assert mod.vehicle.tau["a"] == pytest.approx(want)
        assert run["veh_params"]["a"]["T"] == pytest.approx(want)
        # b changes lane (not a connection successor): restored at once; c
        # crosses onto the next edge in its lane (a successor): kept; d leaves
        results = {"a": _res("e", 1), "b": _res("e", 0), "c": _res("f", 1)}
        _measured_relax_step(mod, tc, run, results, 1.5)
        assert "b" not in run["relax"] and mod.vehicle.tau["b"] == 1.4
        assert "c" in run["relax"] and "d" not in run["relax"]
        assert run["n_relax_restored_lane_change"] == 1 and run["n_relax_restored_left"] == 1
        # at 4 tau_r = 20 s: restored to the driver's own T
        _measured_relax_step(mod, tc, run, {"a": _res("e", 1), "c": _res("f", 1)}, 20.0)
        assert run["relax"] == {} and mod.vehicle.tau["a"] == 1.4 and mod.vehicle.tau["c"] == 1.4
        assert run["n_relax_restored_expired"] == 2
