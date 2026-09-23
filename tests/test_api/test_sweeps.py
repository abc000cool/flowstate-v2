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


# ---------------------------------------------------------------------------
# include_baseline
# ---------------------------------------------------------------------------
#
# The dashboard colours every cell "Δ vs baseline"; without a penetration-0
# cell in the sweep it used to fall back to the smallest *controlled* cell, so
# every improvement was a difference between two controlled configurations.
# ``include_baseline`` appends exactly one uncontrolled reference cell after
# the grid (counted in the cell ceiling) — never one per controller: at
# penetration 0 the controller never acts, so k controllers meant k identical
# simulations, and ``validation.report`` then saw k groups labelled
# ``baseline`` and dropped the controller-minus-baseline contrast table and
# the seed-matched contour pairs for want of a single reference group.


def _sweep(client: TestClient, scenario_id: str, **body: object) -> dict:
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": scenario_id,
            "replicates": 1,
            "overrides": {"sim": {"duration_s": 30.0}},
            **body,
        },
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    got = client.get(f"/api/v1/sweeps/{r.json()['sweep_id']}", headers=HEADERS)
    assert got.status_code == 200
    return got.json()  # type: ignore[no-any-return]


def test_include_baseline_appends_a_no_av_cell(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    body = _sweep(
        client,
        scenario["scenario_id"],
        penetrations=[0.02, 0.05],
        compliances=[0.5, 1.0],
        controllers=["follower_stopper"],
        include_baseline=True,
    )
    assert body["status"] == "done", body["error"]
    assert (body["runs_total"], body["runs_done"], body["runs_failed"]) == (5, 5, 0)
    combos = [(c["penetration"], c["compliance"], c["controller"]) for c in body["cells"]]
    assert combos[:4] == [
        (0.02, 0.5, "follower_stopper"),
        (0.02, 1.0, "follower_stopper"),
        (0.05, 0.5, "follower_stopper"),
        (0.05, 1.0, "follower_stopper"),
    ]
    assert combos[4] == (0.0, 1.0, None)  # appended after the grid, uncontrolled
    baseline = body["cells"][4]
    assert baseline["status"] == "done"
    assert baseline["aggregate"]["throughput_veh_h"]["mean"] is not None
    assert len({c["config_hash"] for c in body["cells"]}) == 5
    assert baseline["config_hash"] != scenario["config_hash"]  # replicates/duration overridden

    # The stored cell config really is a no-AV run of the same scenario.
    child = client.get(f"/api/v1/runs/{baseline['run_id']}", headers=HEADERS).json()
    assert child["status"] == "done"
    assert child["seeded"] is False


def test_include_baseline_is_one_uncontrolled_cell_whatever_the_controllers(
    client: TestClient,
) -> None:
    """Two controllers, one baseline — not one identical baseline each.

    The per-controller variant ran k bit-identical no-AV simulations (the
    controller is never dispatched at penetration 0) under k different config
    hashes, and ``validation.report`` needs exactly *one* group labelled
    ``baseline`` to emit the controller-minus-baseline contrast at all.
    """
    scenario = post_scenario(client, macro_corridor_config())
    body = _sweep(
        client,
        scenario["scenario_id"],
        penetrations=[0.05],
        compliances=[1.0],
        controllers=["follower_stopper", "pi_saturation"],
        include_baseline=True,
    )
    assert body["status"] == "done", body["error"]
    combos = [(c["penetration"], c["compliance"], c["controller"]) for c in body["cells"]]
    assert combos == [
        (0.05, 1.0, "follower_stopper"),
        (0.05, 1.0, "pi_saturation"),
        (0.0, 1.0, None),
    ]
    assert body["runs_total"] == 3
    # One uncontrolled config hash, so the report sees one baseline group.
    assert len({c["config_hash"] for c in body["cells"]}) == 3


def test_include_baseline_is_skipped_when_a_controller_is_null(client: TestClient) -> None:
    """A ``null`` controller cell is already uncontrolled at any penetration.

    ``validation.report.group_label`` labels *any* cell with no controller
    ``baseline``, whatever its penetration, so appending another one would
    re-create the two-baseline tie the single-cell rule exists to avoid.
    """
    scenario = post_scenario(client, macro_corridor_config())
    body = _sweep(
        client,
        scenario["scenario_id"],
        penetrations=[0.05],
        compliances=[1.0],
        controllers=["follower_stopper", None],
        include_baseline=True,
    )
    assert body["status"] == "done", body["error"]
    combos = [(c["penetration"], c["compliance"], c["controller"]) for c in body["cells"]]
    assert combos == [(0.05, 1.0, "follower_stopper"), (0.05, 1.0, None)]


def test_repeated_axis_values_do_not_run_the_same_cell_twice(client: TestClient) -> None:
    """Identical cells share a config hash and a run tree: run one of them."""
    scenario = post_scenario(client, macro_corridor_config())
    body = _sweep(
        client,
        scenario["scenario_id"],
        penetrations=[0.05, 0.05],
        compliances=[1.0],
        controllers=["follower_stopper", None, "follower_stopper"],
    )
    assert body["status"] == "done", body["error"]
    combos = [(c["penetration"], c["compliance"], c["controller"]) for c in body["cells"]]
    assert combos == [(0.05, 1.0, "follower_stopper"), (0.05, 1.0, None)]
    assert body["runs_total"] == 2
    assert len({c["run_id"] for c in body["cells"]}) == 2


def test_include_baseline_is_skipped_when_the_grid_already_has_penetration_zero(
    client: TestClient,
) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    body = _sweep(
        client,
        scenario["scenario_id"],
        penetrations=[0.0, 0.05],
        compliances=[0.5],
        include_baseline=True,
    )
    assert body["status"] == "done", body["error"]
    combos = [(c["penetration"], c["compliance"], c["controller"]) for c in body["cells"]]
    assert combos == [(0.0, 0.5, None), (0.05, 0.5, None)]


def test_include_baseline_defaults_off_and_counts_toward_the_cell_ceiling(
    client: TestClient,
) -> None:
    """200 grid cells are accepted; 200 + 1 baseline cell is over the limit.

    Both checks run in schema validation (the unknown scenario would 404 from
    the handler), like the other size gates in test_limits.py.
    """
    grid = {
        "scenario_id": "scn_does_not_exist",
        "penetrations": [0.001 * i for i in range(1, 21)],
        "compliances": [0.1 * i for i in range(1, 11)],
        # An explicit controller: the default ``[None]`` grid is already
        # uncontrolled, so no baseline cell would be appended to it.
        "controllers": ["follower_stopper"],
    }
    assert client.post("/api/v1/sweeps", json=grid, headers=HEADERS).status_code == 404
    r = client.post("/api/v1/sweeps", json={**grid, "include_baseline": True}, headers=HEADERS)
    assert r.status_code == 422
    detail = str(r.json()["detail"])
    assert "1 baseline cells" in detail
    assert "201 cells" in detail


def test_unknown_request_fields_are_refused(client: TestClient) -> None:
    """A mis-named key is a client bug: the dashboard once sent ``controller``
    (singular), which pydantic's default silently dropped, so every sweep ran
    without its controller. The request models forbid extras."""
    scenario = post_scenario(client, macro_corridor_config())
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": scenario["scenario_id"],
            "penetrations": [0.05],
            "compliances": [1.0],
            "controller": "follower_stopper",
        },
        headers=HEADERS,
    )
    assert r.status_code == 422
    assert "controller" in str(r.json()["detail"])
    assert "extra" in str(r.json()["detail"]).lower()
    r = client.post(
        "/api/v1/runs",
        json={"scenario_id": scenario["scenario_id"], "replicate": 3},
        headers=HEADERS,
    )
    assert r.status_code == 422
    r = client.post("/api/v1/reports", json={"run_ids": ["x"], "titel": "t"}, headers=HEADERS)
    assert r.status_code == 422


def test_sweep_reports_its_tier_and_carries_macro_options_into_every_cell(
    client: TestClient,
) -> None:
    """A macro sweep says so, and its solver options reach each cell's config.

    The dashboard labels a screening matrix from ``SweepOut.tier`` (macro
    results may never be read as a validation result, CLAUDE.md §5.6), and the
    tier has to be readable before the fan-out job has created a single run —
    so it comes from the stored cell configs, not from a run row.
    """
    scenario = post_scenario(client, macro_corridor_config())
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": scenario["scenario_id"],
            "penetrations": [0.05],
            "compliances": [1.0],
            "controllers": ["follower_stopper"],
            "replicates": 1,
            "macro": {"dx_m": 200.0, "bottleneck_variant": "capacity"},
            "overrides": {"sim": {"duration_s": 60.0}},
        },
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    assert r.json()["tier"] == "macro"

    body = client.get(f"/api/v1/sweeps/{r.json()['sweep_id']}", headers=HEADERS).json()
    assert body["tier"] == "macro"
    (cell,) = body["cells"]
    run = client.get(f"/api/v1/runs/{cell['run_id']}", headers=HEADERS).json()
    assert run["status"] == "done", run["error"]
    row = client.app.state.store.get_run(run["run_id"])  # type: ignore[attr-defined]
    assert row["config"]["macro"] == {"dx_m": 200.0, "bottleneck_variant": "capacity"}


def test_sweep_with_an_unknown_bottleneck_variant_is_422(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": scenario["scenario_id"],
            "penetrations": [0.05],
            "compliances": [1.0],
            "controllers": [None],
            "replicates": 1,
            "macro": {"bottleneck_variant": "sideways"},
        },
        headers=HEADERS,
    )
    assert r.status_code == 422
