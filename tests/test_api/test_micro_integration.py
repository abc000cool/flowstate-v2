"""Micro-tier round trip through the API (real SUMO — marked integration).

Tiny 300 s ring, 2 replicates, inline queue. Also exercises the successful
report path (micro runs are report-eligible, unlike the screening tier).
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from tests.test_api.conftest import HEADERS, post_run, post_scenario

pytestmark = pytest.mark.integration


def _ring_config() -> dict:
    return {
        "name": "api_micro_ring",
        "tier": "micro",
        "network": {"kind": "ring", "circumference_m": 230.0, "n_vehicles": 22},
        "fleet": {"model": "IDM", "T": 1.2},
        "sim": {"duration_s": 300.0, "step_length_s": 0.5, "output_hz": 2.0},
        "seed": 42,
        "replicates": 2,
    }


def test_micro_ring_round_trip_with_report(client: TestClient) -> None:
    scenario = post_scenario(client, _ring_config())
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]
    assert run["tier"] == "micro"
    assert run["progress"] == {"completed_replicates": 2, "total_replicates": 2}

    # Metrics: real trajectory-based numbers with honest underpowered flags.
    r = client.get(f"/api/v1/runs/{run['run_id']}/metrics", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config_hash"] == run["config_hash"]
    assert body["n_replicates"] == 2
    assert body["underpowered"] is True
    for rep in body["replicates"]:
        m = rep["metrics"]
        assert m["throughput_veh_h"] is not None and m["throughput_veh_h"] > 0.0
        assert m["sigma_v_temporal_ms"] is not None and m["sigma_v_temporal_ms"] > 0.0
        assert m["fuel_ml_per_veh_km"] is not None and m["fuel_ml_per_veh_km"] > 0.0
        assert m["vmt_veh_km"] is not None and m["vmt_veh_km"] > 0.0
    agg = body["aggregate"]
    assert agg["throughput_veh_h"]["n"] == 2
    assert agg["throughput_veh_h"]["underpowered"] is True

    # Heatmaps from the micro tier's Edie-binned edges.parquet.
    r = client.get(f"/api/v1/runs/{run['run_id']}/heatmap?field=speed", headers=HEADERS)
    assert r.status_code == 200
    hm = r.json()
    assert hm["config_hash"] == run["config_hash"]
    assert len(hm["values"]) == len(hm["t_bins"])
    r = client.get(
        f"/api/v1/runs/{run['run_id']}/heatmap?field=density&format=png", headers=HEADERS
    )
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")

    # Micro runs are report-eligible: full report bundle round trip.
    r = client.post(
        "/api/v1/reports",
        json={"run_ids": [run["run_id"]], "title": "ring smoke report"},
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    report = r.json()
    assert report["status"] == "done", report["error"]

    md = client.get(f"/api/v1/reports/{report['report_id']}/markdown", headers=HEADERS)
    assert md.status_code == 200
    assert run["config_hash"] in md.text
    assert "ring smoke report" in md.text

    archive = client.get(f"/api/v1/reports/{report['report_id']}/archive", headers=HEADERS)
    assert archive.status_code == 200
    assert archive.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        names = zf.namelist()
    assert "report.md" in names
    assert any(name.endswith(".png") for name in names)  # speed-contour figures
