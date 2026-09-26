"""Unit tests for ``validation.battery.insertion_stats`` and its aggregate.

The insertion counters are the cheapest evidence that a run simulated the
demand it was configured with; these tests pin the three verdicts the
corridor battery and the report print — ok, backlog, starved ramps — and the
conventions around them (off-ramps ignored, small ramps not judged, missing
counters refused rather than reported as zero).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from scipy.stats import t as student_t

from validation.battery import (
    COLLISION_DEFINITION,
    COLLISION_RATE_PER_VEHICLES,
    HEALTHY_DEPARTED_FRACTION,
    MISSED_EXIT_SHARE_THRESHOLD,
    NO_PLAN_VERDICT,
    OK_VERDICT,
    SCORE_MEMORY_FRACTION,
    SCORE_WORKER_BASE_BYTES,
    SCORE_WORKER_BYTES_PER_ROW,
    STARVED_RAMP_MIN_PLANNED,
    UNKNOWN_LANE,
    InsertionStats,
    aggregate_insertion,
    available_memory_bytes,
    collision_count,
    collision_summary,
    degraded_verdict,
    forced_change_summary,
    insertion_stats,
    json_safe,
    lane_edge,
    records_insertion,
    score_pool_size,
    score_worker_bytes,
    trajectory_rows,
    weave_exit_summary,
)

PLANNED = 1000


def _meta(
    planned: int = PLANNED,
    departed: int = PLANNED,
    arrived: int | None = 900,
    ramps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A meta.json fragment carrying only what insertion_stats reads."""
    meta: dict[str, Any] = {
        "n_vehicles_planned": planned,
        "n_vehicles_departed": departed,
    }
    if arrived is not None:
        meta["n_vehicles_arrived"] = arrived
    if ramps is not None:
        meta["ramps"] = ramps
    return meta


def _ramp(
    name: str, kind: str, planned: int, departed: int, index: int | None = None
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "n_planned": planned,
        "n_departed": departed,
    }
    if index is not None:
        entry["index"] = index
    return entry


def _weave_section(
    ramp: str, missed: int | None, reached: int | None, exit_name: str = "X-OFF"
) -> dict[str, Any]:
    """A ``meta.json["weave_sections"][i]`` fragment carrying what the summary reads."""
    entry: dict[str, Any] = {"ramp": ramp, "exit": exit_name, "n_missed": 0}
    if missed is not None:
        entry["n_missed_exit"] = missed
    if reached is not None:
        entry["n_reached_section_exiting"] = reached
    return entry


class TestWeaveExitSummary:
    """Given-up exits per weaving section, pooled over the replicate metas."""

    def test_two_sections_over_two_replicates(self) -> None:
        metas = [
            {"weave_sections": [_weave_section("A-ON", 0, 200), _weave_section("B-ON", 12, 200)]},
            {"weave_sections": [_weave_section("A-ON", 0, 200), _weave_section("B-ON", 18, 200)]},
        ]
        out = weave_exit_summary(metas)
        assert out["threshold_share"] == MISSED_EXIT_SHARE_THRESHOLD == 0.02
        assert out["n_runs"] == 2
        assert [s["ramp"] for s in out["sections"]] == ["A-ON", "B-ON"]
        a, b = out["sections"]
        assert a == {
            "ramp": "A-ON",
            "exit": "X-OFF",
            "n_runs": 2,
            "reached": 400,
            "missed_exit": {"n": 0, "share": 0.0},
            "flagged": False,
        }
        assert b["missed_exit"] == {"n": 30, "share": pytest.approx(0.075)}
        assert b["reached"] == 400 and b["n_runs"] == 2 and b["flagged"] is True
        assert out["verdict"] == "exits given up: 7.5 % at B-ON"

    def test_the_threshold_is_strict(self) -> None:
        at = weave_exit_summary([{"weave_sections": [_weave_section("A-ON", 8, 400)]}])
        assert at["sections"][0]["missed_exit"]["share"] == pytest.approx(0.02)
        assert at["sections"][0]["flagged"] is False
        assert at["verdict"] == OK_VERDICT
        above = weave_exit_summary([{"weave_sections": [_weave_section("A-ON", 9, 400)]}])
        assert above["sections"][0]["flagged"] is True
        assert above["verdict"] == "exits given up: 2.2 % at A-ON"

    def test_several_flagged_sections_are_listed_in_order(self) -> None:
        out = weave_exit_summary(
            [{"weave_sections": [_weave_section("A-ON", 40, 400), _weave_section("B-ON", 4, 40)]}]
        )
        assert out["verdict"] == "exits given up: 10.0 % at A-ON, 10.0 % at B-ON"

    def test_a_custom_threshold_is_honoured_and_recorded(self) -> None:
        out = weave_exit_summary(
            [{"weave_sections": [_weave_section("A-ON", 30, 400)]}], threshold=0.1
        )
        assert out["threshold_share"] == 0.1
        assert out["sections"][0]["flagged"] is False
        assert out["verdict"] == OK_VERDICT

    def test_runs_without_sections_say_nothing(self) -> None:
        out = weave_exit_summary([_meta(), _meta()])
        assert out == {
            "threshold_share": MISSED_EXIT_SHARE_THRESHOLD,
            "n_runs": 0,
            "sections": [],
            "verdict": OK_VERDICT,
        }

    def test_a_meta_written_before_the_counter_contributes_nothing(self) -> None:
        metas = [
            {"weave_sections": [_weave_section("A-ON", None, 200)]},
            {"weave_sections": [_weave_section("A-ON", 10, 200)]},
        ]
        out = weave_exit_summary(metas)
        assert out["n_runs"] == 2
        section = out["sections"][0]
        assert section["n_runs"] == 1
        assert section["reached"] == 200
        assert section["missed_exit"] == {"n": 10, "share": pytest.approx(0.05)}

    def test_no_reached_exiter_is_an_undefined_share_not_a_zero(self) -> None:
        out = weave_exit_summary([{"weave_sections": [_weave_section("A-ON", 0, 0)]}])
        section = out["sections"][0]
        assert math.isnan(section["missed_exit"]["share"])
        assert section["flagged"] is False
        assert out["verdict"] == OK_VERDICT

    def test_an_unnamed_section_is_labelled_by_its_position(self) -> None:
        out = weave_exit_summary(
            [{"weave_sections": [_weave_section("", 0, 10), _weave_section("", 5, 10, "")]}]
        )
        assert [s["ramp"] for s in out["sections"]] == ["section 0", "section 1"]
        assert out["sections"][1]["exit"] is None
        assert out["verdict"] == "exits given up: 50.0 % at section 1"

    def test_the_summary_is_json_serialisable(self) -> None:
        out = weave_exit_summary([{"weave_sections": [_weave_section("A-ON", 3, 100)]}])
        assert json.loads(json.dumps(out))["sections"][0]["missed_exit"]["n"] == 3


class TestDegradedVerdict:
    def test_an_ok_weave_verdict_leaves_the_insertion_verdict_alone(self) -> None:
        assert degraded_verdict(OK_VERDICT, OK_VERDICT) == OK_VERDICT
        assert degraded_verdict("backlog: 5 % ...", OK_VERDICT) == "backlog: 5 % ..."

    def test_a_flagged_section_degrades_an_ok_insertion(self) -> None:
        assert degraded_verdict(OK_VERDICT, "exits given up: 7.5 % at B-ON") == (
            "exits given up: 7.5 % at B-ON"
        )

    def test_both_problems_are_joined_like_the_insertion_verdict(self) -> None:
        assert degraded_verdict("starved ramps: A-ON", "exits given up: 7.5 % at B-ON") == (
            "starved ramps: A-ON; exits given up: 7.5 % at B-ON"
        )


def _collision(lane: str, pos_m: float, t: float = 10.0) -> dict[str, Any]:
    """A ``meta.json["collisions"][i]`` event as the runner logs it."""
    return {
        "t": t,
        "collider": "f",
        "victim": "l",
        "type": "collision",
        "lane": lane,
        "pos_m": pos_m,
    }


def _collision_meta(
    n: int | None,
    events: list[dict[str, Any]] | None = None,
    departed: int | None = PLANNED,
    seed: int = 1,
) -> dict[str, Any]:
    """A meta.json fragment carrying what the collision summary reads."""
    meta: dict[str, Any] = {"seed": seed}
    if departed is not None:
        meta["n_vehicles_departed"] = departed
    if n is not None:
        meta["n_collisions"] = n
        meta["collisions"] = events if events is not None else []
    return meta


class TestLaneEdge:
    def test_the_lane_index_is_dropped(self) -> None:
        assert lane_edge("-178547099#2_1") == "-178547099#2"
        assert lane_edge("e1_0") == "e1"

    def test_internal_lanes_keep_their_edge(self) -> None:
        assert lane_edge(":J3_0_0") == ":J3_0"

    def test_an_id_without_an_index_is_unchanged(self) -> None:
        assert lane_edge("edge") == "edge"
        assert lane_edge("edge_a") == "edge_a"
        assert lane_edge("_0") == "_0"


def _three_runs() -> list[dict[str, Any]]:
    """Three runs: 3 collisions (two lanes), none, 1 (a third lane)."""
    return [
        _collision_meta(
            3,
            [_collision("e1_0", 40.5), _collision("e1_0", 10.0), _collision(":J3_0_0", 2.0)],
            seed=11,
        ),
        _collision_meta(0, seed=12),
        _collision_meta(1, [_collision("e2_1", 12.0)], departed=500, seed=13),
    ]


class TestCollisionSummary:
    """Collisions pooled over a run set: counts, interval, rate and places."""

    def test_total_runs_and_hand_computed_interval(self) -> None:
        out = collision_summary(_three_runs())
        assert out is not None
        assert out["n_runs"] == 3 and out["n_runs_recorded"] == 3
        assert out["runs_not_recorded"] == []
        assert out["total"] == 4
        assert out["n_runs_with_collisions"] == 2
        assert out["runs_with_collisions"] == [{"run": 11, "n": 3}, {"run": 13, "n": 1}]
        # counts (3, 0, 1): mean 4/3, s = sqrt(7/3), half = t(0.975, 2) * s / sqrt(3)
        half = student_t.ppf(0.975, 2) * math.sqrt(7.0 / 3.0) / math.sqrt(3.0)
        per = out["per_run"]
        assert per["mean"] == pytest.approx(4.0 / 3.0)
        assert per["lo95"] == pytest.approx(4.0 / 3.0 - half)
        assert per["hi95"] == pytest.approx(4.0 / 3.0 + half)
        assert per["n"] == 3 and per["underpowered"] is True

    def test_rate_per_thousand_departed_vehicles(self) -> None:
        out = collision_summary(_three_runs())
        assert out is not None
        rate = out["rate"]
        assert COLLISION_RATE_PER_VEHICLES == rate["per_vehicles"] == 1000
        assert (rate["n_collisions"], rate["n_departed"], rate["n_runs"]) == (4, 2500, 3)
        assert rate["value"] == pytest.approx(1000.0 * 4 / 2500)

    def test_locations_are_grouped_by_lane_most_first(self) -> None:
        out = collision_summary(_three_runs())
        assert out is not None
        assert out["n_logged"] == 4
        assert out["locations"] == [
            {
                "lane": "e1_0",
                "edge": "e1",
                "n": 2,
                "pos_m_min": 10.0,
                "pos_m_max": 40.5,
                "runs": [11],
            },
            {
                "lane": ":J3_0_0",
                "edge": ":J3_0",
                "n": 1,
                "pos_m_min": 2.0,
                "pos_m_max": 2.0,
                "runs": [11],
            },
            {
                "lane": "e2_1",
                "edge": "e2",
                "n": 1,
                "pos_m_min": 12.0,
                "pos_m_max": 12.0,
                "runs": [13],
            },
        ]

    def test_labels_name_the_runs(self) -> None:
        out = collision_summary(_three_runs(), labels=["a/1", "a/2", "a/3"])
        assert out is not None
        assert [w["run"] for w in out["runs_with_collisions"]] == ["a/1", "a/3"]
        assert [loc["runs"] for loc in out["locations"]] == [["a/1"], ["a/1"], ["a/3"]]
        with pytest.raises(ValueError, match="2 labels for 3 runs"):
            collision_summary(_three_runs(), labels=["a", "b"])

    def test_one_lane_hit_in_two_runs_lists_both(self) -> None:
        metas = [
            _collision_meta(1, [_collision("e1_0", 5.0)], seed=1),
            _collision_meta(2, [_collision("e1_0", 7.0), _collision("e1_0", 3.0)], seed=2),
        ]
        out = collision_summary(metas)
        assert out is not None
        (row,) = out["locations"]
        assert (row["n"], row["pos_m_min"], row["pos_m_max"], row["runs"]) == (3, 3.0, 7.0, [1, 2])

    def test_no_run_recorded_is_none_not_zero(self) -> None:
        assert collision_summary([_collision_meta(None), _collision_meta(None)]) is None
        assert collision_summary([]) is None

    def test_a_run_without_the_counter_is_named_and_left_out(self) -> None:
        metas = [_collision_meta(2, [_collision("e1_0", 1.0)] * 2, seed=1), _meta()]
        out = collision_summary(metas, labels=["r1", "r2"])
        assert out is not None
        assert out["n_runs"] == 2 and out["n_runs_recorded"] == 1
        assert out["runs_not_recorded"] == ["r2"]
        assert out["total"] == 2
        assert out["per_run"]["n"] == 1
        assert math.isnan(out["per_run"]["lo95"])
        # the unrecorded run's departures are not in the rate's denominator
        assert out["rate"]["n_departed"] == PLANNED and out["rate"]["n_runs"] == 1

    def test_zero_collisions_recorded_is_a_zero(self) -> None:
        out = collision_summary([_collision_meta(0), _collision_meta(0, seed=2)])
        assert out is not None
        assert out["total"] == 0 and out["n_runs_with_collisions"] == 0
        assert out["per_run"]["mean"] == 0.0
        assert (out["per_run"]["lo95"], out["per_run"]["hi95"]) == (0.0, 0.0)
        assert out["rate"]["value"] == 0.0
        assert out["locations"] == [] and out["n_logged"] == 0

    def test_counted_events_beyond_the_log_are_not_located(self) -> None:
        out = collision_summary([_collision_meta(60, [_collision("e1_0", 1.0)] * 50)])
        assert out is not None
        assert out["total"] == 60 and out["n_logged"] == 50
        assert out["locations"][0]["n"] == 50

    def test_a_rate_needs_departures(self) -> None:
        no_counter = collision_summary([_collision_meta(1, departed=None)])
        assert no_counter is not None
        assert no_counter["rate"]["n_runs"] == 0 and math.isnan(no_counter["rate"]["value"])
        none_departed = collision_summary([_collision_meta(0, departed=0)])
        assert none_departed is not None
        assert none_departed["rate"]["n_runs"] == 1
        assert math.isnan(none_departed["rate"]["value"])

    def test_malformed_events_are_skipped_and_unnamed_lanes_labelled(self) -> None:
        events: list[Any] = ["x", {"t": 1.0, "pos_m": None}, {"lane": "e1_0", "pos_m": math.nan}]
        out = collision_summary([_collision_meta(3, events)])
        assert out is not None
        assert out["n_logged"] == 2
        by_lane = {loc["lane"]: loc for loc in out["locations"]}
        assert by_lane[UNKNOWN_LANE]["edge"] == UNKNOWN_LANE
        assert by_lane["e1_0"]["pos_m_min"] is None and by_lane["e1_0"]["pos_m_max"] is None

    def test_collision_count_reads_the_counter_only(self) -> None:
        assert collision_count(_collision_meta(4)) == 4
        assert collision_count(_collision_meta(0)) == 0
        assert collision_count(_meta()) is None
        assert collision_count({"n_collisions": True}) is None

    def test_the_summary_is_strict_json_after_json_safe(self) -> None:
        out = collision_summary([_collision_meta(1, [_collision("e1_0", 1.0)], departed=0)])
        text = json.dumps(json_safe(out), allow_nan=False)
        back = json.loads(text)
        assert back["rate"]["value"] is None and back["per_run"]["lo95"] is None
        assert back["definition"] == COLLISION_DEFINITION


def _merge(ramp: str, changed: int | None, forced: int | None) -> dict[str, Any]:
    entry: dict[str, Any] = {"ramp": ramp, "attach_edge": "e"}
    if changed is not None:
        entry["n_changed"] = changed
    if forced is not None:
        entry["n_forced"] = forced
    return entry


class TestForcedChangeSummary:
    def test_merges_and_weaves_are_pooled_per_ramp(self) -> None:
        weave = {"ramp": "W-ON", "n_changed_in": 100, "n_changed_out": 50, "n_forced": 5}
        metas = [
            {"scripted_merges": [_merge("M-ON", 300, 20)], "weave_sections": [weave]},
            {"scripted_merges": [_merge("M-ON", 310, 25)], "weave_sections": [weave]},
        ]
        assert forced_change_summary(metas) == [
            {
                "model": "scripted merge",
                "ramp": "M-ON",
                "n_runs": 2,
                "n_forced": 45,
                "n_changed": 610,
            },
            {
                "model": "weave section",
                "ramp": "W-ON",
                "n_runs": 2,
                "n_forced": 10,
                "n_changed": 300,
            },
        ]

    def test_runs_without_the_models_say_nothing(self) -> None:
        assert forced_change_summary([_meta(), {"scripted_merges": []}]) == []

    def test_a_run_without_the_counters_contributes_nothing(self) -> None:
        metas = [
            {"scripted_merges": [_merge("M-ON", 300, None)]},
            {"scripted_merges": [_merge("M-ON", 200, 4)]},
        ]
        (row,) = forced_change_summary(metas)
        assert (row["n_runs"], row["n_forced"], row["n_changed"]) == (1, 4, 200)

    def test_an_unnamed_model_is_labelled_by_its_position(self) -> None:
        (row,) = forced_change_summary([{"scripted_merges": [_merge("", 10, 1)]}])
        assert row["ramp"] == "scripted merge 0"


class TestInsertionStats:
    def test_a_run_that_inserted_its_plan_is_ok(self) -> None:
        stats = insertion_stats(_meta())
        assert stats.verdict == OK_VERDICT
        assert (stats.planned, stats.departed, stats.arrived) == (PLANNED, PLANNED, 900)
        assert stats.departed_fraction == pytest.approx(1.0)
        assert stats.ramps == () and stats.starved_ramps == ()

    def test_the_healthy_threshold_is_inclusive(self) -> None:
        departed = round(HEALTHY_DEPARTED_FRACTION * PLANNED)
        assert insertion_stats(_meta(departed=departed)).verdict == OK_VERDICT
        assert insertion_stats(_meta(departed=departed - 1)).verdict.startswith("backlog:")

    def test_a_backlog_names_the_share_that_never_departed(self) -> None:
        stats = insertion_stats(_meta(departed=400))
        assert stats.departed_fraction == pytest.approx(0.4)
        assert stats.verdict == "backlog: 60 % of planned vehicles never departed"

    def test_starved_on_ramps_are_named(self) -> None:
        stats = insertion_stats(
            _meta(
                ramps=[
                    _ramp("OH-ON", "on", 4 * STARVED_RAMP_MIN_PLANNED, STARVED_RAMP_MIN_PLANNED),
                    _ramp(
                        "BR-ON", "on", 4 * STARVED_RAMP_MIN_PLANNED, 4 * STARVED_RAMP_MIN_PLANNED
                    ),
                    # Too few planned to judge, and an off-ramp, which carries
                    # no insertion at all.
                    _ramp("TINY-ON", "on", STARVED_RAMP_MIN_PLANNED - 1, 0),
                    _ramp("OH-OFF", "off", 4 * STARVED_RAMP_MIN_PLANNED, 0),
                ]
            )
        )
        assert [r.name for r in stats.ramps] == ["OH-ON", "BR-ON", "TINY-ON"]
        assert stats.starved_ramps == ("OH-ON",)
        assert stats.verdict == "starved ramps: OH-ON"
        assert stats.ramps[0].fraction == pytest.approx(0.25)
        assert stats.ramps[1].starved is False

    def test_both_problems_are_reported_together(self) -> None:
        stats = insertion_stats(
            _meta(
                departed=400,
                ramps=[_ramp("OH-ON", "on", 4 * STARVED_RAMP_MIN_PLANNED, 0)],
            )
        )
        assert stats.verdict == (
            "backlog: 60 % of planned vehicles never departed; starved ramps: OH-ON"
        )

    def test_an_empty_plan_is_not_called_a_backlog(self) -> None:
        stats = insertion_stats(_meta(planned=0, departed=0, arrived=0))
        assert math.isnan(stats.departed_fraction)
        assert stats.verdict == NO_PLAN_VERDICT

    def test_metadata_without_the_counters_is_refused(self) -> None:
        assert records_insertion({"seed": 1}) is False
        assert records_insertion(_meta()) is True
        with pytest.raises(ValueError, match="insertion counters"):
            insertion_stats({"seed": 1})

    def test_a_missing_arrived_counter_stays_missing(self) -> None:
        """A zero would read as "nothing completed the corridor" — gridlock."""
        stats = insertion_stats(_meta(arrived=None))
        assert stats.arrived is None
        assert json.loads(json.dumps(stats.to_dict()))["arrived"] is None
        assert stats.verdict == OK_VERDICT  # the counter says nothing about insertion

    def test_an_unnamed_ramp_is_labelled_by_its_scenario_index(self) -> None:
        """``meta["ramps"][k]["index"]`` is what the label must resolve against.

        Counting on-ramps only made the second on-ramp "ramp 1" although the
        scenario's ramps[1] is the off-ramp in front of it.
        """
        big = 4 * STARVED_RAMP_MIN_PLANNED
        stats = insertion_stats(
            _meta(
                ramps=[
                    _ramp("", "on", big, 0, index=0),
                    _ramp("OH-OFF", "off", big, 0, index=1),
                    _ramp("", "on", big, 0, index=2),
                ]
            )
        )
        assert [r.name for r in stats.ramps] == ["ramp 0", "ramp 2"]
        assert stats.starved_ramps == ("ramp 0", "ramp 2")

    def test_an_unnamed_ramp_without_an_index_falls_back_to_its_position(self) -> None:
        stats = insertion_stats(
            _meta(ramps=[_ramp("OH-OFF", "off", 10, 0), _ramp("", "on", 10, 0)])
        )
        assert [r.name for r in stats.ramps] == ["ramp 1"]

    def test_to_dict_is_json_serialisable(self) -> None:
        stats = insertion_stats(_meta(ramps=[_ramp("OH-ON", "on", 200, 10)]))
        payload = json.loads(json.dumps(stats.to_dict()))
        assert payload["verdict"] == stats.verdict
        assert payload["ramps"][0]["starved"] is True


class TestAggregateInsertion:
    def test_no_runs_says_nothing(self) -> None:
        assert aggregate_insertion([]) is None

    def test_pooled_counts_mean_and_worst_replicate(self) -> None:
        stats = [insertion_stats(_meta(departed=d)) for d in (1000, 800, 600)]
        summary = aggregate_insertion(stats)
        assert summary is not None
        assert summary.n_runs == 3
        assert (summary.planned, summary.departed) == (3000, 2400)
        assert summary.mean_arrived == pytest.approx(900.0) and summary.n_with_arrived == 3
        assert summary.mean_departed_fraction == pytest.approx(0.8)
        assert summary.min_departed_fraction == pytest.approx(0.6)
        assert summary.verdict == "backlog: 20 % of planned vehicles never departed"

    def test_starved_ramps_are_unioned_in_first_seen_order(self) -> None:
        big = 4 * STARVED_RAMP_MIN_PLANNED
        stats = [
            insertion_stats(
                _meta(ramps=[_ramp("OH-ON", "on", big, 0), _ramp("BR-ON", "on", big, big)])
            ),
            insertion_stats(
                _meta(ramps=[_ramp("OH-ON", "on", big, 0), _ramp("BR-ON", "on", big, 0)])
            ),
        ]
        summary = aggregate_insertion(stats)
        assert summary is not None
        assert summary.starved_ramps == ("OH-ON", "BR-ON")
        assert summary.verdict == "starved ramps: OH-ON, BR-ON"

    def test_an_undefined_fraction_contributes_nothing(self) -> None:
        summary = aggregate_insertion(
            [insertion_stats(_meta()), insertion_stats(_meta(planned=0, departed=0, arrived=0))]
        )
        assert summary is not None
        assert summary.mean_departed_fraction == pytest.approx(1.0)
        assert summary.min_departed_fraction == pytest.approx(1.0)
        assert summary.verdict == OK_VERDICT

    def test_arrived_is_a_mean_over_the_runs_that_recorded_it(self) -> None:
        """Summing a subset beside two full sums would read as vanished vehicles."""
        summary = aggregate_insertion(
            [
                insertion_stats(_meta(arrived=900)),
                insertion_stats(_meta(arrived=None)),
                insertion_stats(_meta(arrived=700)),
            ]
        )
        assert summary is not None
        assert summary.n_runs == 3 and summary.n_with_arrived == 2
        assert summary.mean_arrived == pytest.approx(800.0)
        assert json.loads(json.dumps(summary.to_dict()))["mean_arrived"] == pytest.approx(800.0)

    def test_no_run_recorded_arrival_says_so(self) -> None:
        summary = aggregate_insertion([insertion_stats(_meta(arrived=None))])
        assert summary is not None
        assert summary.mean_arrived is None and summary.n_with_arrived == 0

    def test_stats_are_frozen(self) -> None:
        stats = insertion_stats(_meta())
        assert isinstance(stats, InsertionStats)
        with pytest.raises(AttributeError):
            stats.departed = 0  # type: ignore[misc]


class TestScorePoolSize:
    """The scoring pool is sized by memory, from the parquet footer (no SUMO).

    A scoring worker's peak RSS grows with the replicate's row count
    (``SCORE_WORKER_BYTES_PER_ROW``, measured); six workers on a 4-hour
    corridor would exceed a 125 GB machine, so the default pool is capped by
    ``MemAvailable``.
    """

    ROWS = 1000

    @pytest.fixture
    def run_dir(self, tmp_path: Path) -> Path:
        import numpy as np
        import pyarrow as pa
        import pyarrow.parquet as pq

        d = tmp_path / "abc" / "1"
        d.mkdir(parents=True)
        table = pa.table(
            {
                "t": np.arange(self.ROWS, dtype=np.float64),
                "veh_id": ["v0"] * self.ROWS,
                "x": np.zeros(self.ROWS),
                "v": np.zeros(self.ROWS),
            }
        )
        with open(d / "trajectories.parquet", "wb") as f:
            pq.write_table(table, f)
        return d

    def test_rows_come_from_the_footer(self, run_dir: Path) -> None:
        assert trajectory_rows(run_dir) == self.ROWS
        assert trajectory_rows(run_dir.parent / "pruned") is None

    def test_estimate_is_base_plus_rows(self) -> None:
        assert score_worker_bytes(0) == SCORE_WORKER_BASE_BYTES
        assert score_worker_bytes(self.ROWS) == (
            SCORE_WORKER_BASE_BYTES + SCORE_WORKER_BYTES_PER_ROW * self.ROWS
        )

    def test_pool_is_capped_by_available_memory(self, run_dir: Path) -> None:
        per_worker = score_worker_bytes(self.ROWS)
        # Room for exactly two workers inside the planning fraction.
        available = int(2 * per_worker / SCORE_MEMORY_FRACTION) + 1
        assert score_pool_size(6, [run_dir], available_bytes=available) == (2, per_worker)
        # Plenty of memory: the CPU-side limit stands.
        assert score_pool_size(6, [run_dir], available_bytes=10**12) == (6, per_worker)
        # Not even one worker fits: one runs anyway (the caller's choice to score at all).
        assert score_pool_size(6, [run_dir], available_bytes=1) == (1, per_worker)

    def test_largest_replicate_sets_the_estimate(self, run_dir: Path, tmp_path: Path) -> None:
        import numpy as np
        import pyarrow as pa
        import pyarrow.parquet as pq

        bigger = tmp_path / "abc" / "2"
        bigger.mkdir()
        n = 4 * self.ROWS
        with open(bigger / "trajectories.parquet", "wb") as f:
            pq.write_table(pa.table({"t": np.zeros(n)}), f)
        pruned = tmp_path / "abc" / "3"
        pruned.mkdir()
        _, per_worker = score_pool_size(6, [run_dir, bigger, pruned], available_bytes=10**12)
        assert per_worker == score_worker_bytes(n)

    def test_without_trajectories_or_meminfo_there_is_no_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import validation.battery as battery

        assert score_pool_size(4, [tmp_path], available_bytes=1) == (4, None)
        monkeypatch.setattr(battery, "_MEMINFO_PATH", tmp_path / "absent")
        assert available_memory_bytes() is None

    def test_meminfo_is_parsed_in_kib(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import validation.battery as battery

        meminfo = tmp_path / "meminfo"
        meminfo.write_text("MemTotal:       131072000 kB\nMemAvailable:   122070312 kB\n")
        monkeypatch.setattr(battery, "_MEMINFO_PATH", meminfo)
        assert available_memory_bytes() == 122070312 * 1024


class TestReadScoringFrame:
    """``read_scoring_frame`` equals ``read_trajectories`` with ``veh_id``
    factorized (``sort=True``) on every file layout the reader can meet, and
    the batched read hands the same numbers to the metrics as the file read."""

    @staticmethod
    def _frame(n_veh: int, samples: int = 100, headway_s: float = 1.0):
        """Time-major rows: ``n_veh`` vehicles of ``samples`` rows each."""
        import numpy as np
        import pandas as pd

        offsets = 0.5 * np.arange(samples)
        t = (headway_s * np.arange(n_veh)[:, None] + offsets[None, :]).ravel()
        veh = np.repeat([f"veh_{k}" for k in range(n_veh)], samples)
        rng = np.random.default_rng(0)
        v = rng.uniform(5.0, 30.0, t.size)
        x = np.tile(offsets, n_veh) * np.repeat(rng.uniform(10.0, 30.0, n_veh), samples)
        frame = pd.DataFrame({"t": t, "veh_id": veh, "x": x, "v": v})
        return frame.sort_values("t", kind="stable").reset_index(drop=True)

    @staticmethod
    def _write(run_dir: Path, frame, *, row_group_size: int | None = None, dictionary=False):
        import pyarrow as pa
        import pyarrow.parquet as pq

        run_dir.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(frame, preserve_index=False)
        if dictionary:
            i = table.schema.get_field_index("veh_id")
            table = table.set_column(i, "veh_id", table.column("veh_id").dictionary_encode())
        with open(run_dir / "trajectories.parquet", "wb") as f:
            pq.write_table(table, f, row_group_size=row_group_size)
        (run_dir / "meta.json").write_text(
            json.dumps({"config": {"sim": {"warmup_s": 20.0, "duration_s": 1000.0}}})
        )
        return run_dir

    @staticmethod
    def _assert_same(run_dir: Path) -> None:
        import numpy as np
        import pandas as pd

        from validation.battery import read_scoring_frame, read_trajectories

        got = read_scoring_frame(run_dir)
        ref = read_trajectories(run_dir)
        assert list(got.columns) == list(ref.columns) == ["t", "veh_id", "x", "v"]
        assert got["veh_id"].dtype == np.intp
        for col in ("t", "x", "v"):
            assert got[col].dtype == np.float64
            assert np.array_equal(got[col].to_numpy(), ref[col].to_numpy(), equal_nan=True)
        codes, _ = pd.factorize(ref["veh_id"].astype("str"), sort=True)
        assert np.array_equal(got["veh_id"].to_numpy(), codes)

    def test_batches_split_vehicles_and_the_last_batch_is_short(self, tmp_path: Path) -> None:
        from validation.battery import SCORING_READ_BATCH_ROWS

        frame = self._frame(n_veh=3000)  # 300k rows: two batches, the second short
        assert SCORING_READ_BATCH_ROWS < len(frame) < 2 * SCORING_READ_BATCH_ROWS
        assert len(frame) % SCORING_READ_BATCH_ROWS != 0
        self._assert_same(self._write(tmp_path / "split", frame, row_group_size=100_000))

    def test_extra_and_reordered_columns_and_nan_positions(self, tmp_path: Path) -> None:
        import numpy as np

        frame = self._frame(n_veh=20)
        frame.loc[[3, 40, 41], "x"] = np.nan
        frame.loc[[7, 8], "v"] = np.nan
        frame["lane"] = np.zeros(len(frame), dtype=np.int32)
        frame = frame[["lane", "v", "x", "veh_id", "t"]]
        self._assert_same(self._write(tmp_path / "wide", frame))

    def test_dictionary_encoded_ids_get_the_string_order(self, tmp_path: Path) -> None:
        frame = self._frame(n_veh=20)
        self._assert_same(self._write(tmp_path / "dict", frame, dictionary=True))

    def test_empty_and_single_row_files(self, tmp_path: Path) -> None:
        frame = self._frame(n_veh=2)
        self._assert_same(self._write(tmp_path / "empty", frame.iloc[:0]))
        self._assert_same(self._write(tmp_path / "one", frame.iloc[:1]))

    def test_a_null_id_is_refused(self, tmp_path: Path) -> None:
        from validation.battery import read_scoring_frame

        frame = self._frame(n_veh=5)
        frame.loc[[2, 9], "veh_id"] = None
        with pytest.raises(ValueError, match="null veh_id"):
            read_scoring_frame(self._write(tmp_path / "null", frame))

    def test_the_frame_yields_the_file_reads_metrics(self, tmp_path: Path) -> None:
        import dataclasses

        from validation.battery import read_scoring_frame, replicate_wave_speed_kmh
        from validation.criteria import CRITERIA_PROFILES
        from validation.metrics import compute_metrics

        frame = self._frame(n_veh=60)
        # Two duplicated (veh_id, t) rows exercise the tie rule on the codes.
        frame = frame.iloc[[*range(len(frame)), 5, 700]].reset_index(drop=True)
        run_dir = self._write(tmp_path / "run", frame, row_group_size=1000)
        handed = read_scoring_frame(run_dir)
        via_file = dataclasses.asdict(compute_metrics(run_dir, x_ref=300.0))
        via_frame = dataclasses.asdict(compute_metrics(run_dir, x_ref=300.0, trajectories=handed))
        # JSON text: byte identity, NaN included.
        assert json.dumps(via_frame) == json.dumps(via_file)
        detector = next(iter(CRITERIA_PROFILES.values())).wave_detector
        assert json.dumps(replicate_wave_speed_kmh(run_dir, detector, trajectories=handed)) == (
            json.dumps(replicate_wave_speed_kmh(run_dir, detector))
        )
