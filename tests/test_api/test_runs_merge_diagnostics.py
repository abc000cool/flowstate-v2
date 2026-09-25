"""``MetricsOut.merge_diagnostics``: the ramp-meter and weaving-section
counters of a run's first replicate, read from its ``meta.json`` so the
dashboard shows them without the run directory (2026-09-24).

A macro run on the inline queue (no SUMO) supplies a real run directory;
the lists are then written into its first replicate's meta the way
``microsim.runner`` writes them, because no scenario without SUMO produces
them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from api import results as res
from tests.test_api.conftest import HEADERS, macro_corridor_config, post_run, post_scenario

RAMP_METER: dict[str, Any] = {
    "ramp": "Hickory Hollow Pkwy",
    "controller": "alinea",
    "edge": "19441652#1",
    "stop_pos_m": 65.0,
    "downstream_edge": "19441652#2",
    "interval_s": 30.0,
    "n_released": 412,
    "n_passed_unstoppable": 6,
    "releases_s": [12.0, 42.5, 71.0],
    "rates": [[0.0, 900.0, 0.31], [30.0, 840.0, 0.35]],
}

WEAVE_SECTION: dict[str, Any] = {
    "ramp": "Ruth St",
    "exit": "T.H.52",
    "edges": ["e1", "e2"],
    "exit_edge": "x1",
    "length_m": 305.2,
    "length_m_measured": 305.2,
    "params": {"exit_accept_gap_s": 1.2},
    "n_entered": 388,
    "n_changed_in": 241,
    "n_changed_out": 145,
    "n_forced": 19,
    "n_missed": 1,
    "n_forced_deferred": 57,
    "n_unfinished": 1,
    "n_cooperations": 612,
    "mean_follower_decel_ms2": 0.42,
    "n_changer_eased": 208,
    "n_vacated": 228,
    "n_vacate_refused": 28,
    "n_vacate_skipped_no_gap": 12,
    "n_vacate_requests": 301,
    "n_pair_releases": 9,
    "n_missed_exit": 1,
    "n_giveup_waited": 0,
    "short_section": False,
    "vacate_window_edges": ["e0", "e-1"],
    "n_exited": 143,
    "n_reached_section_exiting": 145,
    "n_departed_exiting": 147,
    "wait_s_mean": 4.84,
    "wait_in_s_mean": 3.9,
    "wait_out_s_mean": 6.3,
}


def _first_meta_path(client: TestClient, run_id: str) -> Path:
    row = client.app.state.store.get_run(run_id)  # type: ignore[attr-defined]
    dirs = res.replicate_dirs(row["run_root"])
    assert dirs
    return dirs[0] / "meta.json"


def _amend_meta(path: Path, **extra: Any) -> dict[str, Any]:
    meta = json.loads(path.read_text())
    meta.update(extra)
    path.write_text(json.dumps(meta))
    return meta  # type: ignore[no-any-return]


def _metrics(client: TestClient, run_id: str) -> dict[str, Any]:
    r = client.get(f"/api/v1/runs/{run_id}/metrics", headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_a_run_without_merge_models_reports_no_diagnostics(client: TestClient) -> None:
    """Absent is honest: a plain corridor has no meter and no weave."""
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]
    body = _metrics(client, run["run_id"])
    assert body["merge_diagnostics"] is None

    # two empty lists are the same as none — the runner writes them on every
    # micro run, and an empty table would be a section with nothing to say
    _amend_meta(_first_meta_path(client, run["run_id"]), ramp_meters=[], weave_sections=[])
    assert _metrics(client, run["run_id"])["merge_diagnostics"] is None


def test_meter_and_weave_counters_are_read_from_the_first_replicate(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    meta_path = _first_meta_path(client, run["run_id"])
    meta = _amend_meta(meta_path, ramp_meters=[RAMP_METER], weave_sections=[WEAVE_SECTION])

    diag = _metrics(client, run["run_id"])["merge_diagnostics"]
    assert diag is not None
    assert diag["seed"] == meta["seed"]

    (meter,) = diag["ramp_meters"]
    assert meter["ramp"] == "Hickory Hollow Pkwy"
    assert meter["controller"] == "alinea"
    assert meter["edge"] == "19441652#1"
    assert meter["interval_s"] == 30.0
    assert meter["n_released"] == 412
    assert meter["n_passed_unstoppable"] == 6
    assert meter["n_rate_updates"] == 2
    # the per-release and per-rate logs stay in meta.json; the response is the counters
    assert "releases_s" not in meter
    assert "rates" not in meter

    (weave,) = diag["weave_sections"]
    assert weave["ramp"] == "Ruth St"
    assert weave["exit"] == "T.H.52"
    assert weave["length_m"] == 305.2
    for key in (
        "n_entered",
        "n_changed_in",
        "n_changed_out",
        "n_exited",
        "n_reached_section_exiting",
        "n_departed_exiting",
        "n_forced",
        "n_forced_deferred",
        "n_missed",
        "n_unfinished",
        "n_cooperations",
        "mean_follower_decel_ms2",
        "n_changer_eased",
        "n_vacated",
        "n_vacate_refused",
        "n_vacate_skipped_no_gap",
        "n_vacate_requests",
        "n_pair_releases",
        "n_missed_exit",
        "n_giveup_waited",
        "short_section",
        "vacate_window_edges",
        "wait_s_mean",
        "wait_in_s_mean",
        "wait_out_s_mean",
    ):
        assert weave[key] == WEAVE_SECTION[key], key
    assert "params" not in weave


def test_a_short_section_with_an_inert_window_reads_as_written(client: TestClient) -> None:
    """``short_section`` true and ``vacate_window_edges`` ``[]`` (the vacate
    rule inert) come through as written: an empty list is a value, not null."""
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    short = {**WEAVE_SECTION, "short_section": True, "vacate_window_edges": []}
    _amend_meta(_first_meta_path(client, run["run_id"]), weave_sections=[short])

    diag = _metrics(client, run["run_id"])["merge_diagnostics"]
    assert diag is not None
    (weave,) = diag["weave_sections"]
    assert weave["short_section"] is True
    assert weave["vacate_window_edges"] == []


def test_a_weave_written_before_the_cooperation_counters_reads_as_null(client: TestClient) -> None:
    """A meta from before the follower-cooperation rule (2026-09-24, block 3)
    has no ``n_cooperations``, ``mean_follower_decel_ms2`` or
    ``n_changer_eased``, nor the later ``n_vacated`` / ``n_vacate_refused``
    (third derivation), ``n_pair_releases`` (fifth), ``n_missed_exit``
    (exit side), the re-derived vacate rule's ``n_vacate_skipped_no_gap``
    / ``n_vacate_requests``, the short-section flag ``short_section``, the
    cross-edge window's ``vacate_window_edges`` and the bounded give-up
    patience's ``n_giveup_waited`` (WP-52) and the two yield counters
    ``n_exiter_yields`` / ``n_entrant_yields`` (WP-54): the fourteen read as
    null, the rest as written."""
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    new_keys = (
        "n_cooperations",
        "mean_follower_decel_ms2",
        "n_changer_eased",
        "n_vacated",
        "n_vacate_refused",
        "n_pair_releases",
        "n_missed_exit",
        "n_giveup_waited",
        "n_exiter_yields",
        "n_entrant_yields",
        "n_vacate_skipped_no_gap",
        "n_vacate_requests",
        "short_section",
        "vacate_window_edges",
    )
    old_weave = {k: v for k, v in WEAVE_SECTION.items() if k not in new_keys}
    _amend_meta(_first_meta_path(client, run["run_id"]), weave_sections=[old_weave])

    diag = _metrics(client, run["run_id"])["merge_diagnostics"]
    assert diag is not None
    (weave,) = diag["weave_sections"]
    for key in new_keys:
        assert weave[key] is None, key
    assert weave["n_forced_deferred"] == 57
    assert weave["n_reached_section_exiting"] == 145


def test_a_weave_with_the_cooperation_counters_but_not_the_later_ones(client: TestClient) -> None:
    """A meta from between the second and the third weave derivation carries
    the cooperation counters but none of ``n_vacated``, ``n_vacate_refused``,
    ``n_pair_releases``, ``n_missed_exit``, ``n_vacate_skipped_no_gap``,
    ``n_vacate_requests``, ``short_section``, ``vacate_window_edges``,
    ``n_giveup_waited``, ``n_exiter_yields``, ``n_entrant_yields``: those
    eleven read as null, the cooperation counters as written."""
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    later = (
        "n_vacated",
        "n_vacate_refused",
        "n_pair_releases",
        "n_missed_exit",
        "n_giveup_waited",
        "n_exiter_yields",
        "n_entrant_yields",
        "n_vacate_skipped_no_gap",
        "n_vacate_requests",
        "short_section",
        "vacate_window_edges",
    )
    mid_weave = {k: v for k, v in WEAVE_SECTION.items() if k not in later}
    _amend_meta(_first_meta_path(client, run["run_id"]), weave_sections=[mid_weave])

    diag = _metrics(client, run["run_id"])["merge_diagnostics"]
    assert diag is not None
    (weave,) = diag["weave_sections"]
    for key in later:
        assert weave[key] is None, key
    assert weave["n_cooperations"] == 612
    assert weave["n_changer_eased"] == 208


def test_one_kind_alone_and_a_meter_written_before_the_counter(client: TestClient) -> None:
    """Only meters: the weave list is empty, and a meter recorded before
    ``n_passed_unstoppable`` existed reads as zero rather than failing."""
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    old_meter = {k: v for k, v in RAMP_METER.items() if k != "n_passed_unstoppable"}
    _amend_meta(_first_meta_path(client, run["run_id"]), ramp_meters=[old_meter])

    diag = _metrics(client, run["run_id"])["merge_diagnostics"]
    assert diag is not None
    assert diag["weave_sections"] == []
    assert diag["ramp_meters"][0]["n_passed_unstoppable"] == 0
    assert diag["ramp_meters"][0]["n_released"] == 412


def test_an_unreadable_entry_yields_no_diagnostics_not_a_500(client: TestClient) -> None:
    """A weave entry missing its counters cannot be shown honestly: the field
    is null and the metrics still answer."""
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    _amend_meta(
        _first_meta_path(client, run["run_id"]),
        weave_sections=[{"ramp": "Ruth St", "exit": "T.H.52"}],
    )
    body = _metrics(client, run["run_id"])
    assert body["merge_diagnostics"] is None
    assert body["aggregate"]["throughput_veh_h"]["mean"] > 0.0
