"""Full macro-tier round trip on the inline queue (no SUMO, fast).

POST scenario → POST run → inline execution → metrics with honest CIs and
underpowered flags → heatmap JSON + PNG. Also covers overrides deep-merge,
error honesty for failing runs, the 409/404 paths, the ``seeded`` label
(perturbation *or* lane closure, matching ``meta.json``), and the calibrated
screening path: ``fd_calibration`` + the ``macro`` solver options.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import results as res
from flowstate_core.artifacts import FDCalibration, TriangularFD
from flowstate_core.config import ScenarioConfig
from tests.test_api.conftest import (
    HEADERS,
    data_dir,
    macro_corridor_config,
    post_run,
    post_scenario,
)


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


def _closure_config() -> dict:
    """Two-lane corridor with one lane closed for a minute; no perturbation."""
    return macro_corridor_config(
        name="macro_closure",
        network={"kind": "corridor", "length_m": 1000.0, "lanes": 2, "inflow": [[0.0, 0.3]]},
        closures=[
            {"start_m": 400.0, "end_m": 600.0, "lanes": [0], "t_start_s": 30.0, "t_end_s": 90.0}
        ],
    )


def test_closure_run_is_reported_seeded_like_meta_json(client: TestClient) -> None:
    """A lane closure is an imposed disturbance (CLAUDE.md §0.2).

    ``ScenarioConfig.seeded`` is true for closures as well as perturbations,
    and the runner writes that flag to ``meta.json``; the API's ``seeded`` must
    say the same, or the dashboard presents closure-induced waves as emergent
    while the report generated from the same run labels them seeded.
    """
    cfg = _closure_config()
    assert ScenarioConfig.model_validate(cfg).seeded is True
    scenario = post_scenario(client, cfg)
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]
    assert run["seeded"] is True

    listing = client.get("/api/v1/runs", headers=HEADERS).json()
    assert [r["seeded"] for r in listing] == [True]

    metrics = client.get(f"/api/v1/runs/{run['run_id']}/metrics", headers=HEADERS).json()
    assert metrics["seeded"] is True

    # The same flag the runner wrote beside each replicate's results.
    row = client.app.state.store.get_run(run["run_id"])  # type: ignore[attr-defined]
    metas = [json.loads((d / "meta.json").read_text()) for d in res.replicate_dirs(row["run_root"])]
    assert len(metas) == 3
    assert all(m["seeded"] is True for m in metas)


def test_perturbation_run_is_reported_seeded(client: TestClient) -> None:
    cfg = macro_corridor_config(
        name="macro_perturbed",
        perturbation={"t_s": 30.0, "position_m": 500.0, "duration_s": 20.0, "v_drop_ms": 10.0},
    )
    scenario = post_scenario(client, cfg)
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]
    assert run["seeded"] is True


def test_aggregate_without_observations_is_absent_not_underpowered(client: TestClient) -> None:
    """``n == 0`` reports *no estimate*, not a wide one.

    A metric no replicate produced — ``mean_tt_s`` and ``fuel_ml_per_veh_km``
    in the screening tier, ``wave_speed_kmh`` on any run where no wave was
    detected — used to come back as ``{n: 0, underpowered: true}`` with null
    bounds, which reads as "a number that needs more seeds". There is no
    number: the reason field says so and ``underpowered`` is False.
    """
    run = _finished_macro_run(client)
    agg = client.get(f"/api/v1/runs/{run['run_id']}/metrics", headers=HEADERS).json()["aggregate"]

    for name in ("mean_tt_s", "fuel_ml_per_veh_km"):
        ci = agg[name]
        assert ci["n"] == 0, name
        assert ci["mean"] is None and ci["lo95"] is None and ci["hi95"] is None, name
        assert ci["underpowered"] is False, name
        assert ci["reason"] == "no_observations", name

    # A metric that *was* measured keeps the honest underpowered flag (3 < 20)
    # and carries no reason.
    assert agg["throughput_veh_h"]["underpowered"] is True
    assert agg["throughput_veh_h"]["reason"] is None


def test_ci_to_json_states_are_distinct() -> None:
    """The three CIOut states: no estimate, single replicate, real interval."""
    from validation.metrics import CI

    assert res.ci_to_json(CI(math.nan, math.nan, math.nan, 0)) == {
        "mean": None,
        "lo95": None,
        "hi95": None,
        "n": 0,
        "underpowered": False,
        "reason": "no_observations",
    }
    single = res.ci_to_json(CI(12.5, math.nan, math.nan, 1))
    assert single == {
        "mean": 12.5,
        "lo95": None,
        "hi95": None,
        "n": 1,
        "underpowered": True,
        "reason": None,
    }
    powered = res.ci_to_json(CI(1.0, 0.5, 1.5, 20))
    assert powered["underpowered"] is False
    assert powered["reason"] is None


# ---------------------------------------------------------------------------
# Calibrated fundamental diagram + macro solver options
# ---------------------------------------------------------------------------


#: A fitted diagram that is nothing like the ``v1_legacy`` preset
#: (V_f = 27.8 m/s, w = −5.56 m/s, ρ_jam = 0.16 veh/m), so a run that used it
#: cannot be confused with a run that fell back to the preset.
FITTED_FD = TriangularFD(v_f=30.0, w=-4.0, rho_jam=0.2)


def write_fd_artifact(tmp_path: Path, name: str = "fd_test.json") -> Path:
    """An ``FDCalibration`` artifact inside the allow-listed data root."""
    path = data_dir(tmp_path) / name
    FDCalibration(
        created_at="2026-09-23T00:00:00+00:00",
        source="synthetic fixture (tests/test_api/test_runs_macro.py)",
        data_hash="fixture-hash-1",
        fd=FITTED_FD,
        n_observations=1234,
        r2_freeflow=0.97,
        congested_quantile=0.9,
    ).save(path)
    return path


def _fd_meta(client: TestClient, run_id: str) -> dict:
    """``meta.json["fd"]`` of the run's first replicate."""
    row = client.app.state.store.get_run(run_id)  # type: ignore[attr-defined]
    dirs = res.replicate_dirs(row["run_root"])
    assert dirs
    return json.loads((dirs[0] / "meta.json").read_text())["fd"]


def test_macro_run_uses_the_fd_artifact_and_names_it(client: TestClient, tmp_path: Path) -> None:
    """A calibrated diagram reaches the solver, and meta.json says where from.

    CLAUDE.md §5.1: the FD's parameters are calibrated per-corridor inputs,
    not constants — a screening result is only interpretable together with
    the diagram that produced it, so ``meta.json["fd"]`` must name the
    artifact instead of the uncalibrated ``v1_legacy`` preset.
    """
    artifact = write_fd_artifact(tmp_path)
    scenario = post_scenario(client, macro_corridor_config(fd_calibration=str(artifact)))
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]

    fd = _fd_meta(client, run["run_id"])
    assert fd["artifact"] == str(artifact)
    assert fd["source"] == str(artifact)
    assert fd["preset"] == "artifact"
    assert fd["v_f"] == pytest.approx(FITTED_FD.v_f)
    assert fd["w"] == pytest.approx(FITTED_FD.w)
    assert fd["rho_jam"] == pytest.approx(FITTED_FD.rho_jam)
    assert fd["rho_c"] == pytest.approx(FITTED_FD.rho_c)
    assert fd["q_max"] == pytest.approx(FITTED_FD.q_max)

    # The run is a real screening run, not a pipeline that merely accepted
    # the field: every replicate reports measured throughput.
    body = client.get(f"/api/v1/runs/{run['run_id']}/metrics", headers=HEADERS).json()
    assert body["fd_source"] == str(artifact)
    assert len(body["replicates"]) == 3
    for rep in body["replicates"]:
        assert rep["metrics"]["throughput_veh_h"] > 0.0
    assert body["aggregate"]["throughput_veh_h"]["mean"] > 0.0


def test_macro_run_without_artifact_reports_the_uncalibrated_preset(client: TestClient) -> None:
    """No artifact ⇒ the documented v1_legacy preset, said out loud."""
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    fd = _fd_meta(client, run["run_id"])
    assert fd["preset"] == "v1_legacy"
    assert fd["artifact"] is None
    assert fd["source"] == "v1_legacy preset"
    body = client.get(f"/api/v1/runs/{run['run_id']}/metrics", headers=HEADERS).json()
    assert body["fd_source"] == "v1_legacy preset"


def test_fd_calibration_outside_the_data_roots_is_refused(client: TestClient) -> None:
    """A config file field may only name a file under the allow-listed roots."""
    r = client.post(
        "/api/v1/scenarios",
        json=macro_corridor_config(fd_calibration="/etc/passwd"),
        headers=HEADERS,
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["type"] == "path_outside_roots"
    assert r.json()["detail"][0]["loc"] == ["fd_calibration"]


def test_missing_fd_artifact_fails_the_run_honestly(client: TestClient, tmp_path: Path) -> None:
    """A named-but-absent artifact is a failed run, never a silent fallback."""
    missing = data_dir(tmp_path) / "not_there.json"
    scenario = post_scenario(client, macro_corridor_config(fd_calibration=str(missing)))
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "failed"
    assert run["error"] is not None and "FileNotFoundError" in run["error"]


def test_macro_options_reach_the_solver_and_meta(client: TestClient) -> None:
    """``macro`` on the request selects the cell length and the AV constraint.

    The capacity variant is the §5.5 alternative to the flux cap; both must
    be reachable through the API for the comparison to be runnable at all.
    """
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(
        client,
        scenario["scenario_id"],
        macro={"dx_m": 50.0, "bottleneck_variant": "capacity"},
    )
    assert run["status"] == "done", run["error"]
    # The options are part of the effective config, so the run is a different
    # experiment from the same scenario at the defaults.
    assert run["config_hash"] != scenario["config_hash"]

    row = client.app.state.store.get_run(run["run_id"])  # type: ignore[attr-defined]
    assert row["config"]["macro"] == {"dx_m": 50.0, "bottleneck_variant": "capacity"}
    meta = json.loads((res.replicate_dirs(row["run_root"])[0] / "meta.json").read_text())
    assert meta["macro_options"] == {"dx_m": 50.0, "bottleneck_variant": "capacity"}
    assert meta["av"]["variant"] == "capacity"
    assert meta["grid"]["n_cells"] == 20  # 1000 m at dx = 50 m
    assert meta["grid"]["dx_m"] == pytest.approx(50.0)


def test_default_macro_options_leave_the_config_hash_alone(client: TestClient) -> None:
    """The block is absent unless asked for: existing hashes must not move."""
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    assert run["config_hash"] == scenario["config_hash"]
    row = client.app.state.store.get_run(run["run_id"])  # type: ignore[attr-defined]
    assert row["config"]["macro"] is None
    meta = json.loads((res.replicate_dirs(row["run_root"])[0] / "meta.json").read_text())
    assert meta["macro_options"] == {"dx_m": 100.0, "bottleneck_variant": "flux_cap"}


def test_unknown_bottleneck_variant_and_unknown_key_are_422(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    bad_variant = client.post(
        "/api/v1/runs",
        json={
            "scenario_id": scenario["scenario_id"],
            "macro": {"bottleneck_variant": "moving_average"},
        },
        headers=HEADERS,
    )
    assert bad_variant.status_code == 422
    bad_key = client.post(
        "/api/v1/runs",
        json={"scenario_id": scenario["scenario_id"], "macro": {"dx_metres": 50.0}},
        headers=HEADERS,
    )
    assert bad_key.status_code == 422
    bad_dx = client.post(
        "/api/v1/runs",
        json={"scenario_id": scenario["scenario_id"], "macro": {"dx_m": 0.0}},
        headers=HEADERS,
    )
    assert bad_dx.status_code == 422


def test_micro_tier_records_that_it_ignored_the_macro_blocks() -> None:
    """A macro-only block on a micro config is recorded, not silently dropped.

    Asserted on the config model (the note itself is written by
    ``microsim.runner``, which needs SUMO): the field is tier-independent on
    purpose, so the same scenario can be run on both tiers.
    """
    cfg = ScenarioConfig.model_validate(
        macro_corridor_config(
            tier="micro",
            fd_calibration="artifacts/fd_us101.json",
            macro={"dx_m": 25.0},
        )
    )
    assert cfg.fd_calibration == "artifacts/fd_us101.json"
    assert cfg.macro is not None and cfg.macro.dx_m == 25.0
    assert cfg.macro.bottleneck_variant == "flux_cap"
