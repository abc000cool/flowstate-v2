"""Sweep fan-out: penetration × compliance grid into child macro runs."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_api.conftest import HEADERS, macro_corridor_config, post_scenario


def test_sweep_2x2_macro_grid_fans_out_and_aggregates(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": scenario["scenario_id"],
            "penetrations": [0.02, 0.05],
            "compliances": [0.5, 1.0],
            "controllers": ["follower_stopper"],
            "replicates": 2,
            "overrides": {"sim": {"duration_s": 60.0}},
        },
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    sweep_id = r.json()["sweep_id"]

    got = client.get(f"/api/v1/sweeps/{sweep_id}", headers=HEADERS)
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "done", body["error"]
    assert body["runs_total"] == 4
    assert body["runs_done"] == 4
    assert body["runs_failed"] == 0

    cells = body["cells"]
    combos = {(c["penetration"], c["compliance"], c["controller"]) for c in cells}
    assert combos == {
        (0.02, 0.5, "follower_stopper"),
        (0.02, 1.0, "follower_stopper"),
        (0.05, 0.5, "follower_stopper"),
        (0.05, 1.0, "follower_stopper"),
    }
    # Each cell is a distinct effective config with its own hash and run.
    assert len({c["config_hash"] for c in cells}) == 4
    assert len({c["run_id"] for c in cells}) == 4
    for cell in cells:
        assert cell["status"] == "done"
        assert cell["progress"] == {"completed_replicates": 2, "total_replicates": 2}
        agg = cell["aggregate"]
        assert agg is not None
        assert agg["throughput_veh_h"]["n"] == 2
        assert agg["throughput_veh_h"]["underpowered"] is True  # 2 < 20, honest flag
        assert agg["throughput_veh_h"]["mean"] is not None

    # Child runs are addressable through the runs API as well.
    child_runs = client.get(f"/api/v1/runs?sweep_id={sweep_id}", headers=HEADERS).json()
    assert len(child_runs) == 4
    assert all(run["sweep_id"] == sweep_id for run in child_runs)


def test_sweep_invalid_cell_is_422(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": scenario["scenario_id"],
            "penetrations": [0.5],  # outside the AVSpec range
            "compliances": [1.0],
        },
        headers=HEADERS,
    )
    assert r.status_code == 422
    assert "penetration" in str(r.json()["detail"])


def test_sweep_unknown_scenario_404(client: TestClient) -> None:
    r = client.post(
        "/api/v1/sweeps",
        json={"scenario_id": "scn_missing", "penetrations": [0.1], "compliances": [1.0]},
        headers=HEADERS,
    )
    assert r.status_code == 404
    assert client.get("/api/v1/sweeps/swp_missing", headers=HEADERS).status_code == 404
