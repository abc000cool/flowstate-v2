"""``MetricsOut.insertion`` and ``MetricsOut.weave_exits``: the corridor
battery artifact's two demand-integrity blocks, pooled over a run's
replicates by the same ``validation.battery`` functions the battery uses
(2026-09-24, block 3), so the dashboard's verdicts are the battery's.

A macro run on the inline queue (no SUMO) supplies a real run directory of
three replicates; the counters are then written into their ``meta.json`` the
way ``microsim.runner`` writes them, because no scenario without SUMO
produces them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api import results as res
from tests.test_api.conftest import HEADERS, macro_corridor_config, post_run, post_scenario
from validation.battery import HEALTHY_DEPARTED_FRACTION, MISSED_EXIT_SHARE_THRESHOLD, OK_VERDICT


def _meta_paths(client: TestClient, run_id: str) -> list[Path]:
    row = client.app.state.store.get_run(run_id)  # type: ignore[attr-defined]
    dirs = res.replicate_dirs(row["run_root"])
    assert len(dirs) == 3
    return [d / "meta.json" for d in dirs]


def _amend_meta(path: Path, **extra: Any) -> dict[str, Any]:
    meta = json.loads(path.read_text())
    meta.update(extra)
    path.write_text(json.dumps(meta))
    return meta  # type: ignore[no-any-return]


def _metrics(client: TestClient, run_id: str) -> dict[str, Any]:
    r = client.get(f"/api/v1/runs/{run_id}/metrics", headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _weave(ramp: str, exit_: str, missed: int | None, reached: int | None) -> dict[str, Any]:
    section: dict[str, Any] = {"ramp": ramp, "exit": exit_, "n_entered": 10, "n_missed": 1}
    if missed is not None:
        section["n_missed_exit"] = missed
    if reached is not None:
        section["n_reached_section_exiting"] = reached
    return section


def test_a_run_without_the_counters_reports_neither_block(client: TestClient) -> None:
    """Absent is honest: a macro run records no insertion and no weave."""
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]
    body = _metrics(client, run["run_id"])
    assert body["insertion"] is None
    assert body["weave_exits"] is None

    # an empty weave list on every replicate is still no section — the
    # verdict would be "ok" over nothing, which is a claim
    for path in _meta_paths(client, run["run_id"]):
        _amend_meta(path, weave_sections=[])
    assert _metrics(client, run["run_id"])["weave_exits"] is None


def test_insertion_is_pooled_over_every_replicate_that_records_it(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    paths = _meta_paths(client, run["run_id"])
    on_ramp = {"name": "Ruth St", "kind": "on", "index": 1, "n_planned": 200, "n_departed": 180}
    off_ramp = {"name": "T.H.52", "kind": "off", "index": 2, "n_planned": 0, "n_departed": 0}
    _amend_meta(
        paths[0],
        n_vehicles_planned=1000,
        n_vehicles_departed=990,
        n_vehicles_arrived=900,
        ramps=[on_ramp, off_ramp],
    )
    # the worst seed starves the on-ramp and never recorded an arrival count
    _amend_meta(
        paths[1],
        n_vehicles_planned=1000,
        n_vehicles_departed=700,
        ramps=[{**on_ramp, "n_departed": 60}, off_ramp],
    )
    _amend_meta(
        paths[2],
        n_vehicles_planned=1000,
        n_vehicles_departed=1000,
        n_vehicles_arrived=650,
        ramps=[on_ramp, off_ramp],
    )

    insertion = _metrics(client, run["run_id"])["insertion"]
    assert insertion is not None
    assert insertion["n_runs"] == 3
    assert insertion["planned"] == 3000
    assert insertion["departed"] == 2690
    # a mean over the two replicates that recorded it, not a sum beside two full ones
    assert insertion["mean_arrived"] == pytest.approx(775.0)
    assert insertion["n_with_arrived"] == 2
    assert insertion["mean_departed_fraction"] == pytest.approx((0.99 + 0.7 + 1.0) / 3)
    assert insertion["min_departed_fraction"] == pytest.approx(0.7)
    assert insertion["starved_ramps"] == ["Ruth St"]
    assert insertion["mean_departed_fraction"] < HEALTHY_DEPARTED_FRACTION
    assert insertion["verdict"] == (
        "backlog: 10 % of planned vehicles never departed; starved ramps: Ruth St"
    )
    # the pooled block rests on the replicates that record the counters only
    _amend_meta(paths[1], n_vehicles_planned=None)
    partial = _metrics(client, run["run_id"])["insertion"]
    assert partial["n_runs"] == 2
    assert partial["starved_ramps"] == []
    assert partial["verdict"] == OK_VERDICT


def test_weave_exits_are_pooled_per_section_and_flagged_above_the_threshold(
    client: TestClient,
) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    paths = _meta_paths(client, run["run_id"])
    for path, missed in zip(paths, (5, 7, 3), strict=True):
        _amend_meta(
            path,
            weave_sections=[
                _weave("Ruth St", "T.H.52", missed, 100),
                # no exiter ever reached the second section: share undefined
                _weave("Hickory Hollow", "Bell Rd", 0, 0),
            ],
        )

    weave_exits = _metrics(client, run["run_id"])["weave_exits"]
    assert weave_exits is not None
    assert weave_exits["threshold_share"] == MISSED_EXIT_SHARE_THRESHOLD
    assert weave_exits["n_runs"] == 3
    first, second = weave_exits["sections"]
    assert first["ramp"] == "Ruth St"
    assert first["exit"] == "T.H.52"
    assert first["n_runs"] == 3
    assert first["reached"] == 300
    assert first["missed_exit"] == {"n": 15, "share": pytest.approx(0.05)}
    assert first["flagged"] is True
    assert second["ramp"] == "Hickory Hollow"
    assert second["reached"] == 0
    # NaN never reaches the wire: an undefined share is null, and not flagged
    assert second["missed_exit"] == {"n": 0, "share": None}
    assert second["flagged"] is False
    assert weave_exits["verdict"] == "exits given up: 5.0 % at Ruth St"


def test_a_meta_written_before_the_exit_rule_contributes_nothing(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    paths = _meta_paths(client, run["run_id"])
    # two older metas (no n_missed_exit) and one that records the counter
    _amend_meta(paths[0], weave_sections=[_weave("Ruth St", "T.H.52", None, 100)])
    _amend_meta(paths[1], weave_sections=[_weave("Ruth St", "T.H.52", None, None)])
    _amend_meta(paths[2], weave_sections=[_weave("Ruth St", "T.H.52", 1, 100)])

    weave_exits = _metrics(client, run["run_id"])["weave_exits"]
    assert weave_exits["n_runs"] == 3
    (section,) = weave_exits["sections"]
    assert section["n_runs"] == 1
    assert section["reached"] == 100
    assert section["missed_exit"] == {"n": 1, "share": pytest.approx(0.01)}
    assert section["flagged"] is False
    assert weave_exits["verdict"] == OK_VERDICT

    # a plan of nothing is "no vehicles planned", with null fractions — not zero
    for path in paths:
        _amend_meta(path, n_vehicles_planned=0, n_vehicles_departed=0)
    insertion = _metrics(client, run["run_id"])["insertion"]
    assert insertion["n_runs"] == 3
    assert insertion["mean_departed_fraction"] is None
    assert insertion["min_departed_fraction"] is None
    assert insertion["mean_arrived"] is None
    assert insertion["n_with_arrived"] == 0
    assert insertion["verdict"] == "no vehicles planned"
