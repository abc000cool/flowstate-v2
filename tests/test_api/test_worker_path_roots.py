"""Worker-side path confinement: the second line behind the API's 422.

``POST /scenarios``, ``/runs`` and every ``/sweeps`` cell already refuse a
config whose ``network.osm_file`` / ``fleet.idm_calibration`` resolves outside
``Settings.config_path_roots`` (``api.main._confine_config_paths``, HTTP 422
``path_outside_roots``). These tests cover what happens when a config reaches
the worker *without* having passed that check — an edited store row, a direct
``run_scenario_job`` call, a future endpoint that forgets the validator: the
job publishes the allow-list in the environment
(``api.jobs._confined_worker_paths``) and the code that opens the file
(``microsim.networks.osm_import``, ``microsim.vehicles.resolve_calibration_path``)
refuses it, with an error text that quotes the value and the roots and nothing
read from the file.

Inline queue throughout, so the job runs synchronously inside the request /
the enqueue call.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api import jobs
from api.jobs import get_queue, run_scenario_job
from api.settings import load_settings
from api.store import Store, new_id
from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from microsim.paths import ROOTS_ENV_VAR, env_path_roots
from microsim.vehicles import resolve_calibration_path
from tests.test_api.conftest import HEADERS, macro_corridor_config, post_run, post_scenario

#: Contents of the file a tampered config points at. It must never appear in
#: anything the API hands back.
SECRET = "SECRET-PAYLOAD-DO-NOT-LEAK"


def _micro_osm_config(osm_file: Path) -> dict[str, Any]:
    """A micro-tier OSM scenario naming ``osm_file`` (one short replicate)."""
    return {
        "name": "worker_path_roots",
        "tier": "micro",
        "network": {
            "kind": "osm",
            "osm_file": str(osm_file),
            "corridor_edges": ["100", "101"],
            "inflow": [[0.0, 0.2]],
        },
        "sim": {"duration_s": 30.0, "step_length_s": 0.5, "output_hz": 1.0},
        "seed": 7,
        "replicates": 1,
    }


def _outside_file(tmp_path: Path) -> Path:
    """A file outside every allow-listed root, with contents worth leaking."""
    path = tmp_path / "outside" / "evil.osm"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SECRET)
    return path


def test_worker_env_var_is_the_one_microsim_reads() -> None:
    """The two packages name the same variable (the literal is duplicated so
    a macro-tier worker need not import the SUMO micro tier)."""
    assert jobs.WORKER_PATH_ROOTS_ENV == ROOTS_ENV_VAR


def test_confined_worker_paths_sets_and_restores(tmp_path: Path) -> None:
    roots = [tmp_path / "a", tmp_path / "b"]
    before = os.environ.get(ROOTS_ENV_VAR)
    with jobs._confined_worker_paths(roots):
        assert os.environ[ROOTS_ENV_VAR] == os.pathsep.join(str(r) for r in roots)
        assert env_path_roots() == tuple(r.resolve() for r in roots)
    assert os.environ.get(ROOTS_ENV_VAR) == before


def test_confined_worker_paths_restores_a_previous_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOTS_ENV_VAR, "/previous")
    with jobs._confined_worker_paths([tmp_path]):
        assert os.environ[ROOTS_ENV_VAR] == str(tmp_path)
    assert os.environ[ROOTS_ENV_VAR] == "/previous"


def test_post_scenario_still_refuses_the_hostile_path(client: TestClient, tmp_path: Path) -> None:
    """The first line of defence, i.e. what the tampering below bypasses."""
    r = client.post(
        "/api/v1/scenarios", json=_micro_osm_config(_outside_file(tmp_path)), headers=HEADERS
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["type"] == "path_outside_roots"


def test_worker_refuses_a_stored_config_edited_after_validation(
    client: TestClient, tmp_path: Path
) -> None:
    """A run row whose config never passed the API's check still cannot read.

    The row is created and dispatched exactly as ``api.main.create_run``
    does — only the config in it is one the API would have refused, which is
    what an on-disk edit of the store (or a bypassed endpoint) looks like to
    the worker.
    """
    secret = _outside_file(tmp_path)
    settings = load_settings()
    store = Store(str(settings.db_path))
    cfg = ScenarioConfig.model_validate(_micro_osm_config(secret))
    run_id = new_id("run")
    store.create_run(
        scenario_id=None,
        config=cfg.model_dump(mode="json"),
        config_hash=config_hash(cfg),
        tier=cfg.tier,
        seeds=spawn_seeds(cfg.seed, cfg.replicates),
        run_root=settings.runs_dir / run_id,
        run_id=run_id,
    )
    get_queue(settings).enqueue(
        run_scenario_job,
        run_id,
        job_id=run_id,
        db_path=str(settings.db_path),
        results_root=str(settings.results_dir),
    )

    row = client.get(f"/api/v1/runs/{run_id}", headers=HEADERS)
    assert row.status_code == 200, row.text
    body = row.json()
    assert body["status"] == "failed"
    error = body["error"]
    assert "osm_file" in error
    assert "outside the allowed data roots" in error
    assert str(secret) in error and str(settings.results_dir) in error
    # Sanitized: no file contents, no traceback frames, no source paths.
    # (A replicate fails inside a spawn pool, whose re-raise carries the
    # child's formatted traceback as the exception's cause — withheld by
    # ``api.jobs._exception_chain_text``.)
    assert SECRET not in error
    assert "Traceback (most recent call last)" not in error
    assert 'File "' not in error
    assert "RemoteTraceback: child traceback withheld" in error


def test_repo_relative_preset_survives_the_published_roots(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The presets' ``artifacts/...`` references still resolve on the worker.

    ``Settings.config_path_roots`` allow-lists the repository's ``artifacts/``
    exactly so the shipped scenarios keep working; the repo-root fallback in
    ``resolve_calibration_path`` must survive the confinement the job
    publishes. The working directory is moved out of the repository first, so
    it is that fallback candidate — not the cwd-relative one — that has to
    pass the check.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    settings = load_settings()
    roots = jobs._worker_path_roots(settings.results_dir)
    with jobs._confined_worker_paths(roots):
        got = resolve_calibration_path("artifacts/idm_i24.json")
    assert got.is_file()
    assert got.resolve().is_relative_to(Path(__file__).resolve().parents[2] / "artifacts")


def test_a_run_leaves_the_environment_as_it_found_it(client: TestClient) -> None:
    """A macro run goes through the same confinement and cleans up after it."""
    before = os.environ.get(ROOTS_ENV_VAR)
    scenario = post_scenario(client, macro_corridor_config(replicates=1))
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]
    assert os.environ.get(ROOTS_ENV_VAR) == before
