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
from typing import Any

import pytest

from validation.battery import (
    HEALTHY_DEPARTED_FRACTION,
    NO_PLAN_VERDICT,
    OK_VERDICT,
    STARVED_RAMP_MIN_PLANNED,
    InsertionStats,
    aggregate_insertion,
    insertion_stats,
    records_insertion,
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
