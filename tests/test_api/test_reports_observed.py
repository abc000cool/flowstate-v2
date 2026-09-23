"""``POST /reports`` with an observations artifact (WP-B).

The two FHWA-style criteria rows the API used to leave *not evaluated* —
``link_flows_geh`` and ``speeds_rmspe`` — are evaluated once the request names
a server-side ``flowstate.observations/1`` artifact, from comparisons computed
out of the run artifacts (never from a number in the request). The path is
confined to the allow-listed roots like every other server-side path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_api.conftest import (
    HEADERS,
    data_dir,
    macro_corridor_config,
    post_run,
    post_scenario,
)

CORRIDOR_M = 1000.0
DURATION_S = 3600.0
STATION_X = (200.0, 800.0)
OBSERVED_VEH_H = 720.0
OBSERVED_SPEED_MS = 30.0


def _micro_corridor_config() -> dict[str, Any]:
    """A 1 km corridor run long enough to form one observed hour."""
    return {
        "name": "api_micro_corridor_observed",
        "tier": "micro",
        "network": {
            "kind": "corridor",
            "length_m": CORRIDOR_M,
            "lanes": 1,
            "inflow": [[0.0, 0.2]],
        },
        "fleet": {"model": "IDM", "T": 1.4},
        "sim": {
            "duration_s": DURATION_S,
            "step_length_s": 1.0,
            "warmup_s": 0.0,
            "output_hz": 0.5,
        },
        "seed": 5,
        "replicates": 1,
    }


def _write_observations(path: Path) -> Path:
    """One-hour synthetic observations at two mainline stations."""
    ids = [f"S{i}" for i in range(len(STATION_X))]
    payload = {
        "schema": "flowstate.observations/1",
        "corridor": "api_smoke_corridor",
        "source": {"provider": "synthetic detectors", "dates": ["20260101"], "url": ""},
        "window_s": DURATION_S,
        "t0_local": "07:00",
        "duration_s": DURATION_S,
        "n_windows": 1,
        "aggregation": "synthetic constant profile",
        "stations": [
            {"id": sid, "label": sid, "x_m": x, "lanes": 1, "kind": "mainline"}
            for sid, x in zip(ids, STATION_X, strict=True)
        ],
        "flows_veh_h": {sid: [OBSERVED_VEH_H] for sid in ids},
        "speeds_ms": {sid: [OBSERVED_SPEED_MS] for sid in ids},
        "quality": {sid: {"fraction_valid": 1.0, "n_dates": 1} for sid in ids},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def _one_station_observations(path: Path) -> Path:
    """The same artifact with a single mainline station — no spacing, no segment."""
    payload = json.loads(_write_observations(path).read_text())
    keep = payload["stations"][0]["id"]
    payload["stations"] = [payload["stations"][0]]
    payload["flows_veh_h"] = {keep: payload["flows_veh_h"][keep]}
    payload["speeds_ms"] = {keep: payload["speeds_ms"][keep]}
    payload["quality"] = {keep: payload["quality"][keep]}
    path.write_text(json.dumps(payload))
    return path


def _criterion_row(markdown: str, name: str) -> str:
    return next(line for line in markdown.splitlines() if line.startswith(f"| {name} "))


@pytest.mark.integration
def test_report_with_observations_evaluates_geh_and_rmspe(
    client: TestClient, tmp_path: Path
) -> None:
    observations = _write_observations(data_dir(tmp_path) / "observations_smoke.json")
    scenario = post_scenario(client, _micro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]

    r = client.post(
        "/api/v1/reports",
        json={
            "run_ids": [run["run_id"]],
            "title": "observed corridor report",
            "observations_path": str(observations),
        },
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    report = r.json()
    assert report["status"] == "done", report["error"]
    assert report["observations_path"] == str(observations)

    # The row carries the coverage numbers the report printed.
    fetched = client.get(f"/api/v1/reports/{report['report_id']}", headers=HEADERS).json()
    observed = fetched["observed"]
    assert observed["corridor"] == "api_smoke_corridor"
    assert observed["n_stations"] == len(STATION_X)
    assert observed["n_replicates"] == 1
    assert observed["n_link_hours"] == len(STATION_X)
    assert observed["n_speed_cells"] > 0
    assert observed["flow_fraction"] == pytest.approx(1.0)

    md = client.get(f"/api/v1/reports/{report['report_id']}/markdown", headers=HEADERS)
    assert md.status_code == 200
    assert "### Observed data" in md.text
    assert "synthetic detectors" in md.text
    # Both rows are evaluated (the "Evaluated" column reads yes), from computed
    # comparisons rather than caller-supplied numbers.
    assert "| yes |" in _criterion_row(md.text, "link_flows_geh")
    assert "| yes |" in _criterion_row(md.text, "speeds_rmspe")


@pytest.mark.integration
def test_an_artifact_with_one_station_leaves_the_rows_not_evaluated(
    client: TestClient, tmp_path: Path
) -> None:
    """A thin detector inventory is a missing comparison, not a missing report.

    One positioned mainline station defines no station spacing and therefore
    no segment, so ``segment_bins()`` cannot be formed. The report is still
    generated: the two observed rows stay NOT EVALUATED and the observed block
    says why (CLAUDE.md §0.1).
    """
    observations = _one_station_observations(data_dir(tmp_path) / "observations_one.json")
    scenario = post_scenario(client, _micro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]

    r = client.post(
        "/api/v1/reports",
        json={
            "run_ids": [run["run_id"]],
            "title": "one-station corridor report",
            "observations_path": str(observations),
        },
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    report = r.json()
    assert report["status"] == "done", report["error"]

    fetched = client.get(f"/api/v1/reports/{report['report_id']}", headers=HEADERS).json()
    observed = fetched["observed"]
    assert observed["n_stations"] == 1
    assert observed["n_link_hours"] == 0
    assert observed["n_speed_cells"] == 0
    assert "at least two" in observed["note"]

    md = client.get(f"/api/v1/reports/{report['report_id']}/markdown", headers=HEADERS).text
    assert "NOT EVALUATED" in _criterion_row(md, "link_flows_geh")
    assert "NOT EVALUATED" in _criterion_row(md, "speeds_rmspe")
    assert "comparison not formed" in md


def test_observations_path_outside_roots_is_422(client: TestClient, tmp_path: Path) -> None:
    outside = _write_observations(tmp_path / "outside" / "observations.json")
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]

    r = client.post(
        "/api/v1/reports",
        json={"run_ids": [run["run_id"]], "observations_path": str(outside)},
        headers=HEADERS,
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail[0]["type"] == "path_outside_roots"
    assert detail[0]["loc"] == ["body", "observations_path"]


def test_missing_observations_path_is_404(client: TestClient, tmp_path: Path) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    missing = data_dir(tmp_path) / "nope.json"

    r = client.post(
        "/api/v1/reports",
        json={"run_ids": [run["run_id"]], "observations_path": str(missing)},
        headers=HEADERS,
    )
    assert r.status_code == 404, r.text


def test_report_without_observations_records_no_observed_block(client: TestClient) -> None:
    """The default stays honest: no observations, no observed provenance."""
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    r = client.post("/api/v1/reports", json={"run_ids": [run["run_id"]]}, headers=HEADERS)
    # All-macro sets are refused by the generator; the row still shows the
    # observations fields as unset rather than absent.
    assert r.status_code == 422, r.text
    rows = client.get("/api/v1/reports", headers=HEADERS).json()
    assert rows[0]["observations_path"] is None
    assert rows[0]["observed"] is None
