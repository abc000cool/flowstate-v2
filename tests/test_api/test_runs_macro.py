"""Full macro-tier round trip on the inline queue (no SUMO, fast).

POST scenario → POST run → inline execution → metrics with honest CIs and
underpowered flags → heatmap JSON + PNG. Also covers overrides deep-merge,
error honesty for failing runs, and the 409/404 paths.
"""

from __future__ import annotations

import math

from fastapi.testclient import TestClient

from tests.test_api.conftest import HEADERS, macro_corridor_config, post_run, post_scenario


def _finished_macro_run(client: TestClient) -> dict:
    scenario = post_scenario(client, macro_corridor_config())
    return post_run(client, scenario["scenario_id"])


def test_macro_round_trip_run(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    assert run["run_id"].startswith("run_")
    # Inline queue: the job already executed synchronously.
    assert run["status"] == "done", run["error"]
    assert run["progress"] == {"completed_replicates": 3, "total_replicates": 3}
    assert len(run["seeds"]) == 3
    assert run["config_hash"] == scenario["config_hash"]  # no overrides
    assert run["seeded"] is False
    assert run["tier"] == "macro"

    got = client.get(f"/api/v1/runs/{run['run_id']}", headers=HEADERS).json()
    assert got == run

    listing = client.get("/api/v1/runs", headers=HEADERS).json()
    assert [r["run_id"] for r in listing] == [run["run_id"]]


def test_macro_metrics_real_numbers_and_honest_underpowered(client: TestClient) -> None:
    run = _finished_macro_run(client)
    r = client.get(f"/api/v1/runs/{run['run_id']}/metrics", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config_hash"] == run["config_hash"]
    assert body["tier"] == "macro"
    assert body["n_replicates"] == 3
    # 3 < 20 replicates: must be flagged, never quoted as headline (§0.6).
    assert body["underpowered"] is True

    seeds = {rep["seed"] for rep in body["replicates"]}
    assert seeds == set(run["seeds"])
    for rep in body["replicates"]:
        m = rep["metrics"]
        assert m["throughput_veh_h"] is not None and m["throughput_veh_h"] > 0.0
        assert m["sigma_v_spatial_ms"] is not None and m["sigma_v_spatial_ms"] >= 0.0
        assert m["vmt_veh_km"] is not None and m["vmt_veh_km"] > 0.0
        # No trajectories/fuel in the screening tier — honestly absent.
        assert m["mean_tt_s"] is None
        assert m["fuel_ml_per_veh_km"] is None

    agg = body["aggregate"]
    thr = agg["throughput_veh_h"]
    assert thr["n"] == 3
    assert thr["underpowered"] is True
    assert thr["mean"] is not None and math.isfinite(thr["mean"])
    assert thr["lo95"] is not None and thr["hi95"] is not None
    assert thr["lo95"] <= thr["mean"] <= thr["hi95"]


def test_macro_heatmap_json_and_png(client: TestClient) -> None:
    run = _finished_macro_run(client)
    rid = run["run_id"]

    r = client.get(f"/api/v1/runs/{rid}/heatmap?field=speed", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config_hash"] == run["config_hash"]
    assert body["seed"] == run["seeds"][0]
    assert len(body["values"]) == len(body["t_bins"])
    assert all(len(row) == len(body["x_bins"]) for row in body["values"])
    speeds = [v for row in body["values"] for v in row if v is not None]
    assert speeds and all(v >= 0.0 for v in speeds)

    r = client.get(f"/api/v1/runs/{rid}/heatmap?field=density", headers=HEADERS)
    assert r.status_code == 200
    densities = [v for row in r.json()["values"] for v in row if v is not None]
    assert densities and all(v >= 0.0 for v in densities)

    r = client.get(f"/api/v1/runs/{rid}/heatmap?field=speed&format=png", headers=HEADERS)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")
    assert r.headers["X-Config-Hash"] == run["config_hash"]

    # An explicit replicate seed selects that replicate.
    seed = run["seeds"][2]
    r = client.get(f"/api/v1/runs/{rid}/heatmap?seed={seed}", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["seed"] == seed

    assert client.get(f"/api/v1/runs/{rid}/heatmap?seed=999999", headers=HEADERS).status_code == 404
    assert client.get(f"/api/v1/runs/{rid}/heatmap?field=nope", headers=HEADERS).status_code == 422


def test_run_overrides_deep_merge_and_rehash(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(
        client,
        scenario["scenario_id"],
        overrides={"sim": {"duration_s": 60.0}},
        replicates=2,
    )
    assert run["status"] == "done", run["error"]
    assert run["progress"]["total_replicates"] == 2
    # A patched config is re-hashed: never reuse the base scenario's hash.
    assert run["config_hash"] != scenario["config_hash"]

    r = client.post(
        "/api/v1/runs",
        json={
            "scenario_id": scenario["scenario_id"],
            "overrides": {"av": {"penetration": 0.9}},
        },
        headers=HEADERS,
    )
    assert r.status_code == 422  # merged config is re-validated


def test_run_unknown_scenario_404(client: TestClient) -> None:
    r = client.post("/api/v1/runs", json={"scenario_id": "scn_missing"}, headers=HEADERS)
    assert r.status_code == 404


def test_failed_run_records_error_and_blocks_metrics(client: TestClient) -> None:
    # OSM networks are valid configs but unsupported by the macro runner.
    cfg = macro_corridor_config(
        name="macro_osm_fails",
        network={"kind": "osm", "bbox": [36.0, -87.0, 36.1, -86.9], "inflow": [[0.0, 0.3]]},
        replicates=2,
    )
    scenario = post_scenario(client, cfg)
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "failed"
    assert run["error"] is not None and "NotImplementedError" in run["error"]
    assert run["progress"]["completed_replicates"] == 0

    r = client.get(f"/api/v1/runs/{run['run_id']}/metrics", headers=HEADERS)
    assert r.status_code == 409
    r = client.get(f"/api/v1/runs/{run['run_id']}/heatmap", headers=HEADERS)
    assert r.status_code == 409


def test_missing_run_404(client: TestClient) -> None:
    assert client.get("/api/v1/runs/run_missing", headers=HEADERS).status_code == 404
    assert client.get("/api/v1/runs/run_missing/metrics", headers=HEADERS).status_code == 404
