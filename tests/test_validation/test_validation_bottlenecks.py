"""Active bottlenecks and check C6 (docs/FRISCO_PROTOCOL.md §5) on planted fields.

Station × five-minute speed matrices with a bottleneck planted by hand: the
identification (thresholds in mph, the 5-of-7 persistence rule at its edge,
missing windows, episodes, queue reach) and the four comparison rules
(location with the adjacent-pair tolerance, timing, queue reach, phantom
bottlenecks at the 30-minute / 50 % edges), the one-to-one matching of
observed and simulated bottlenecks, and phantoms counted per replicate.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from validation.bottlenecks import (
    ACTIVATION_TOLERANCE_S,
    BOTTLENECK_WINDOW_S,
    DURATION_TOLERANCE_SHARE,
    LOCATION_MIN_REPLICATE_SHARE,
    MIN_OBSERVED_ACTIVE_S,
    MPH_TO_MS,
    PERSISTENCE_MIN_ACTIVE,
    PERSISTENCE_WINDOWS,
    PHANTOM_ACTIVE_S,
    PHANTOM_MAX_REPLICATE_SHARE,
    QUEUE_REACH_TOLERANCE_STATIONS,
    SPEED_DIFFERENCE_MIN_MS,
    UPSTREAM_SPEED_MAX_MS,
    Bottleneck,
    active_condition,
    compare_bottlenecks,
    episodes_of,
    identify_bottlenecks,
    match_replicate,
)

FREE = 27.0  # m/s, about 60 mph
SLOW = 8.0  # m/s, about 18 mph
N_ST = 8
IDS = [f"S{i}" for i in range(N_ST)]
X = [500.0 * i for i in range(N_ST)]
W = BOTTLENECK_WINDOW_S


def _field(n_windows: int = 36) -> np.ndarray:
    return np.full((n_windows, N_ST), FREE)


def _bn(pair: int, act_min: float, dur_min: float, reach: int | None = None) -> Bottleneck:
    start = act_min * 60.0
    return Bottleneck(
        pair_index=pair,
        upstream=IDS[pair],
        downstream=IDS[pair + 1],
        upstream_x_m=X[pair],
        downstream_x_m=X[pair + 1],
        activation_s=start,
        activation_clock="",
        active_s=dur_min * 60.0,
        episodes=((start, start + dur_min * 60.0),),
        queue_reach_index=pair if reach is None else reach,
        queue_reach_station=IDS[pair if reach is None else reach],
    )


class TestThresholds:
    def test_protocol_values_in_si(self) -> None:
        assert MPH_TO_MS == pytest.approx(0.44704)
        assert UPSTREAM_SPEED_MAX_MS == pytest.approx(17.8816)  # 40 mph, 64.4 km/h
        assert SPEED_DIFFERENCE_MIN_MS == pytest.approx(8.9408)  # 20 mph, 32.2 km/h
        assert BOTTLENECK_WINDOW_S == 300.0
        assert (PERSISTENCE_MIN_ACTIVE, PERSISTENCE_WINDOWS) == (5, 7)
        assert MIN_OBSERVED_ACTIVE_S == PHANTOM_ACTIVE_S == 1800.0
        assert ACTIVATION_TOLERANCE_S == 900.0
        assert (LOCATION_MIN_REPLICATE_SHARE, DURATION_TOLERANCE_SHARE) == (0.8, 0.3)
        assert (QUEUE_REACH_TOLERANCE_STATIONS, PHANTOM_MAX_REPLICATE_SHARE) == (1, 0.5)

    def test_condition_edges(self) -> None:
        v = np.array(
            [
                [UPSTREAM_SPEED_MAX_MS - 0.01, UPSTREAM_SPEED_MAX_MS + SPEED_DIFFERENCE_MIN_MS],
                [UPSTREAM_SPEED_MAX_MS, UPSTREAM_SPEED_MAX_MS + 2 * SPEED_DIFFERENCE_MIN_MS],
                [10.0, 10.0 + SPEED_DIFFERENCE_MIN_MS - 0.01],
                [10.0, np.nan],
            ]
        )
        assert active_condition(v)[:, 0].tolist() == [True, False, False, False]


class TestIdentify:
    def test_one_planted_bottleneck_with_its_queue(self) -> None:
        v = _field()
        v[10:22, 5] = SLOW  # 12 windows = 60 min at S5, S6 free downstream
        v[14:17, 3:5] = SLOW  # the queue reaches S3 for 15 min
        found = identify_bottlenecks(v, IDS, X, first_window=4, t0_local="06:00")
        assert len(found) == 1
        b = found[0]
        assert (b.pair_index, b.upstream, b.downstream) == (5, "S5", "S6")
        assert b.activation_s == (4 + 10) * W
        assert b.activation_clock == "07:10"
        assert b.active_s == 12 * W
        assert b.episodes == (((4 + 10) * W, (4 + 22) * W),)
        assert (b.queue_reach_index, b.queue_reach_station) == (3, "S3")

    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            ("TTFTFTF", []),  # 4 of 7: never activated
            ("TTFTTFT", [(0, 6)]),  # 5 of 7: the episode spans first to last true window
            ("FTTFTTFF", []),  # 4 of 7 in every block
            ("TFTTTTF", [(0, 5)]),
        ],
    )
    def test_persistence_edge(self, pattern: str, expected: list[tuple[int, int]]) -> None:
        cond = [c == "T" for c in pattern]
        assert episodes_of(cond) == expected
        v = np.full((len(pattern), 2), FREE)
        v[[i for i, c in enumerate(cond) if c], 0] = SLOW
        found = identify_bottlenecks(v, IDS[:2], X[:2])
        assert len(found) == len(expected)
        if expected:
            lo, hi = expected[0]
            assert found[0].active_s == (hi - lo + 1) * W

    def test_missing_windows_do_not_count(self) -> None:
        v = np.full((7, 2), FREE)
        v[:5, 0] = SLOW
        v[2, 1] = np.nan  # the downstream detector missed one window: 4 of 7 left
        assert identify_bottlenecks(v, IDS[:2], X[:2]) == ()

    def test_two_episodes_sum(self) -> None:
        v = np.full((30, 2), FREE)
        v[0:6, 0] = SLOW
        v[20:28, 0] = SLOW
        (b,) = identify_bottlenecks(v, IDS[:2], X[:2])
        assert b.episodes == ((0.0, 6 * W), (20 * W, 28 * W))
        assert b.active_s == 14 * W and b.activation_s == 0.0

    def test_refuses_other_windows_and_unordered_stations(self) -> None:
        v = _field()
        with pytest.raises(ValueError, match="300 s windows"):
            identify_bottlenecks(v, IDS, X, window_s=60.0)
        with pytest.raises(ValueError, match="strictly increasing"):
            identify_bottlenecks(v, IDS, list(reversed(X)))
        with pytest.raises(ValueError, match="does not match"):
            identify_bottlenecks(v, IDS[:3], X[:3])


class TestCompare:
    def test_reproduced_bottleneck_passes_all_rules(self) -> None:
        obs = [_bn(5, 70, 60, reach=3)]
        sims = [[_bn(5, 70 + (i % 3) * 5, 60 + (i % 2) * 10, reach=3 + (i % 2))] for i in range(20)]
        c = compare_bottlenecks(obs, sims, station_ids=IDS)
        assert c.passed
        (m,) = c.matches
        assert (m.n_reproduced, m.n_same_pair, m.share) == (20, 20, 1.0)
        assert [r.rule for r in c.rules] == ["location", "timing", "queue_reach", "no_phantom"]
        assert c.to_dict()["passed"] is True

    def test_adjacent_pair_counts_as_reproduced(self) -> None:
        obs = [_bn(4, 70, 60)]
        c = compare_bottlenecks(obs, [[_bn(5, 70, 60)] for _ in range(20)], station_ids=IDS)
        assert c.matches[0].n_reproduced == 20 and c.matches[0].n_same_pair == 0
        assert c.rule("location").passed and c.passed
        far = compare_bottlenecks(obs, [[_bn(6, 70, 60)] for _ in range(20)], station_ids=IDS)
        assert far.matches[0].n_reproduced == 0
        assert not far.rule("location").passed
        assert not far.rule("timing").passed  # nothing matched, nothing to time
        assert not far.rule("no_phantom").passed  # pair 6 is two pairs away: a phantom
        assert far.phantoms[0].pair_index == 6 and far.phantoms[0].share_long == 1.0

    @pytest.mark.parametrize(("n_reproducing", "ok"), [(16, True), (15, False)])
    def test_location_share_edge(self, n_reproducing: int, ok: bool) -> None:
        obs = [_bn(5, 70, 60)]
        sims = [[_bn(5, 70, 60)] if i < n_reproducing else [] for i in range(20)]
        c = compare_bottlenecks(obs, sims, station_ids=IDS)
        assert c.rule("location").passed is ok
        assert c.rule("timing").passed  # the reproducing replicates are on time

    @pytest.mark.parametrize(
        ("offset_min", "duration_min", "ok"),
        [(15, 60, True), (-15, 78, True), (20, 60, False), (0, 84, False), (0, 42, True)],
    )
    def test_timing_edges(self, offset_min: float, duration_min: float, ok: bool) -> None:
        obs = [_bn(5, 70, 60)]
        sims = [[_bn(5, 70 + offset_min, duration_min)] for _ in range(20)]
        assert compare_bottlenecks(obs, sims, station_ids=IDS).rule("timing").passed is ok

    @pytest.mark.parametrize(("reach", "ok"), [(4, True), (2, True), (1, False), (5, False)])
    def test_queue_reach_within_one_station(self, reach: int, ok: bool) -> None:
        obs = [_bn(5, 70, 60, reach=3)]
        sims = [[_bn(5, 70, 60, reach=reach)] for _ in range(20)]
        assert compare_bottlenecks(obs, sims, station_ids=IDS).rule("queue_reach").passed is ok

    @pytest.mark.parametrize(
        ("n_long", "minutes", "ok"),
        [(10, 35, True), (11, 35, False), (20, 30, True), (20, 31, False)],
    )
    def test_phantom_edges(self, n_long: int, minutes: float, ok: bool) -> None:
        obs = [_bn(5, 70, 60)]
        sims = [[_bn(5, 70, 60), *([_bn(1, 20, minutes)] if i < n_long else [])] for i in range(20)]
        c = compare_bottlenecks(obs, sims, station_ids=IDS)
        assert c.rule("no_phantom").passed is ok
        assert c.passed is ok
        assert c.phantoms[0].pair_index == 1

    def test_short_observed_bottleneck_need_not_be_reproduced_but_is_no_phantom(self) -> None:
        obs = [_bn(2, 20, 25)]  # active 25 min: below the 30-min reproduction bar
        c = compare_bottlenecks(obs, [[_bn(2, 20, 40)] for _ in range(20)], station_ids=IDS)
        assert c.matches == ()
        assert c.rule("location").passed and not c.rule("location").applicable
        assert "nothing to reproduce" in c.rule("location").detail
        assert c.rule("no_phantom").passed  # it is at an observed bottleneck
        assert c.passed

    def test_no_replicates_is_not_a_pass(self) -> None:
        c = compare_bottlenecks([_bn(5, 70, 60)], [], station_ids=IDS)
        assert not c.passed
        assert math.isnan(c.matches[0].share)


class TestOneToOne:
    """§5.4: observed and simulated bottlenecks are matched one to one."""

    def test_one_simulated_bottleneck_cannot_reproduce_two_observed(self) -> None:
        obs = [_bn(4, 70, 60), _bn(5, 70, 60)]
        sims = [[_bn(5, 70, 60)] for _ in range(20)]
        c = compare_bottlenecks(obs, sims, station_ids=IDS)
        by_pair = {m.observed.pair_index: m for m in c.matches}
        assert by_pair[5].n_reproduced == 20 and by_pair[5].n_same_pair == 20
        assert by_pair[4].n_reproduced == 0  # the one at pair 5 is taken
        assert not c.rule("location").passed

    def test_the_matching_reproduces_as_many_as_it_can(self) -> None:
        # exact-pair-first would give obs 1 -> sim 1 and leave obs 2 unmatched
        obs, rep = [_bn(1, 70, 60), _bn(2, 70, 60)], [_bn(0, 70, 60), _bn(1, 70, 60)]
        matched = match_replicate(obs, rep)
        assert {i: b.pair_index for i, b in matched.items()} == {0: 0, 1: 1}
        c = compare_bottlenecks(obs, [rep] * 20, station_ids=IDS)
        assert c.rule("location").passed
        assert [m.n_same_pair for m in c.matches] == [0, 0]

    def test_same_pair_wins_over_a_longer_adjacent_one(self) -> None:
        matched = match_replicate([_bn(3, 70, 60)], [_bn(2, 70, 90), _bn(3, 70, 40)])
        assert matched[0].pair_index == 3


class TestPhantomsPerReplicate:
    """§5.4: a replicate with any phantom counts once, wherever it sits."""

    def test_review_r3_a_wandering_phantom_fails(self) -> None:
        obs = [_bn(5, 70, 60)]
        sims = [
            [_bn(5, 70, 60), *([_bn(0, 20, 50)] if i < 7 else [_bn(1, 20, 50)] if i < 14 else [])]
            for i in range(20)
        ]
        c = compare_bottlenecks(obs, sims, station_ids=IDS)
        assert [(p.pair_index, p.n_replicates_long) for p in c.phantoms] == [(0, 7), (1, 7)]
        assert all(p.share_long <= PHANTOM_MAX_REPLICATE_SHARE for p in c.phantoms)
        assert c.n_replicates_with_phantom == 14 and c.phantom_share == pytest.approx(0.7)
        rule = c.rule("no_phantom")
        assert not rule.passed and "14 of 20 replicates (70%; limit 50%)" in rule.detail
        assert c.to_dict()["n_replicates_with_phantom"] == 14

    def test_two_phantoms_in_one_replicate_count_once(self) -> None:
        obs = [_bn(5, 70, 60)]
        sims = [
            [_bn(5, 70, 60), _bn(0, 20, 50), _bn(1, 20, 50)] if i < 10 else [] for i in range(20)
        ]
        c = compare_bottlenecks(obs, sims, station_ids=IDS)
        assert c.n_replicates_with_phantom == 10 and c.rule("no_phantom").passed

    def test_next_to_an_observed_bottleneck_is_no_phantom(self) -> None:
        obs = [_bn(5, 70, 60)]
        sims = [[_bn(5, 70, 60), _bn(4, 20, 50), _bn(6, 20, 50)] for _ in range(20)]
        c = compare_bottlenecks(obs, sims, station_ids=IDS)
        assert c.n_replicates_with_phantom == 0 and c.passed
