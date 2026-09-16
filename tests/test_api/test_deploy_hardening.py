"""Deployment hardening: default-key refusal and server-side path confinement.

Three failures reproduced against the running service:

1. A deployed stack (``FLOWSTATE_QUEUE=redis``) booted happily on the API key
   published in this repository's README.
2. ``POST /api/v1/calibrations/{kind}`` took any ``data_path`` the caller
   named and handed it to a worker, which read and parsed it — ``/etc/hosts``
   included — and could echo a parsed value back through the job's error text.
3. Scenario configs carry server-side paths of their own (``network.osm_file``,
   ``fleet.idm_calibration``, ``fleet.heavy.idm_calibration``) that bypassed
   that allow-list: ``POST /scenarios`` accepted ``/etc/hosts`` and the run's
   error text quoted its contents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.settings import DEFAULT_API_KEY, InsecureDefaultKeyError, load_settings
from tests.test_api.conftest import API_KEY, HEADERS, data_dir, macro_corridor_config, post_scenario

# ---------------------------------------------------------------------------
# Default API key
# ---------------------------------------------------------------------------


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: str) -> None:
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)


def test_deployed_service_refuses_the_default_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """redis queue + published default key ⇒ loud startup failure, not a boot."""
    _env(monkeypatch, tmp_path, FLOWSTATE_QUEUE="redis", FLOWSTATE_API_KEY=DEFAULT_API_KEY)
    from api.main import create_app

    with pytest.raises(InsecureDefaultKeyError) as excinfo:
        create_app()
    message = str(excinfo.value)
    assert "FLOWSTATE_API_KEY" in message  # tells the operator the knob to set
    assert DEFAULT_API_KEY in message


def test_default_key_is_also_the_unset_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leaving FLOWSTATE_API_KEY unset is the same insecure case, not an escape."""
    _env(monkeypatch, tmp_path, FLOWSTATE_QUEUE="redis")
    monkeypatch.delenv("FLOWSTATE_API_KEY", raising=False)
    assert load_settings().api_key == DEFAULT_API_KEY
    from api.main import create_app

    with pytest.raises(InsecureDefaultKeyError):
        create_app()


def test_deployed_service_starts_with_an_operator_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal is about the *default*, not about the redis queue itself."""
    _env(monkeypatch, tmp_path, FLOWSTATE_QUEUE="redis", FLOWSTATE_API_KEY="a-real-secret")
    from api.main import create_app

    app = create_app()
    assert app.state.settings.queue_kind == "redis"


def test_inline_dev_queue_may_keep_the_default_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`uv run uvicorn api.main:app` on a laptop stays a one-command start."""
    _env(monkeypatch, tmp_path, FLOWSTATE_QUEUE="inline", FLOWSTATE_API_KEY=DEFAULT_API_KEY)
    from api.main import create_app

    app = create_app()
    assert app.state.settings.api_key == DEFAULT_API_KEY


# ---------------------------------------------------------------------------
# Calibration data_path confinement
# ---------------------------------------------------------------------------


def _post_data_path(client: TestClient, data_path: str) -> tuple[int, str]:
    r = client.post("/api/v1/calibrations/fd", data={"data_path": data_path}, headers=HEADERS)
    return r.status_code, r.text


def test_absolute_path_outside_the_roots_is_refused(client: TestClient) -> None:
    status, text = _post_data_path(client, "/etc/hosts")
    assert status == 422
    assert "outside the allowed data roots" in text
    assert "localhost" not in text  # the refusal never reads the file


def test_relative_traversal_out_of_the_data_root_is_refused(
    client: TestClient, tmp_path: Path
) -> None:
    escape = data_dir(tmp_path) / ".." / ".." / ".." / ".." / ".." / "etc" / "hosts"
    status, text = _post_data_path(client, str(escape))
    assert status == 422
    assert "outside the allowed data roots" in text


def test_symlink_out_of_the_data_root_is_refused(client: TestClient, tmp_path: Path) -> None:
    """Containment is checked after resolution, so a planted symlink loses."""
    link = data_dir(tmp_path) / "innocent.csv"
    link.symlink_to("/etc/hosts")
    status, text = _post_data_path(client, str(link))
    assert status == 422
    assert "outside the allowed data roots" in text


def test_missing_path_inside_the_root_is_refused_without_the_root_message(
    client: TestClient, tmp_path: Path
) -> None:
    status, text = _post_data_path(client, str(data_dir(tmp_path) / "nope.csv"))
    assert status == 422
    assert "not found" in text


def test_path_under_the_results_root_is_allowed(client: TestClient) -> None:
    """Uploads live under the results root, so that root stays readable."""
    settings = client.app.state.settings  # type: ignore[attr-defined]
    staged = Path(settings.uploads_dir) / "staged.csv"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text("density_veh_m,flow_veh_s\n0.01,0.3\n")
    status, _ = _post_data_path(client, str(staged))
    assert status == 202  # accepted; the fit itself then fails or succeeds on its merits


def test_calibration_errors_do_not_echo_the_input_file(client: TestClient) -> None:
    """A parse failure must not stream cell values back to the caller.

    pandas reports a bad numeric cell as ``could not convert string to float:
    '<the cell>'``. That message is third-party, so the job records the
    exception type and raising module instead of the text.
    """
    secret = "SUPER-SECRET-abc123"
    csv = f"density_veh_m,flow_veh_s\n{secret},0.5\n".encode()
    r = client.post(
        "/api/v1/calibrations/fd",
        files={"file": ("loops.csv", csv, "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "failed"
    error = body["error"]
    assert secret not in error  # the leak this test exists for
    assert "ValueError" in error  # still an honest, typed failure record
    assert "message withheld" in error

    fetched = client.get(f"/api/v1/calibrations/{body['calibration_id']}", headers=HEADERS)
    assert secret not in fetched.text


def test_own_diagnostics_survive_sanitizing(client: TestClient) -> None:
    """Messages FlowState itself raises are still reported verbatim."""
    r = client.post(
        "/api/v1/calibrations/fd",
        files={"file": ("bad.csv", b"a,b\n1,2\n", "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "failed"
    assert "fit_triangular_fd: missing column 'density_veh_m'" in body["error"]


def test_upload_path_is_unaffected_by_the_allow_list(client: TestClient) -> None:
    """The confinement is on ``data_path`` only; uploads still work."""
    assert API_KEY  # the fixture's key, for the reader
    r = client.post(
        "/api/v1/calibrations/idm",
        files={"file": ("pairs.csv", b"t,v\n0,1\n", "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 202


# ---------------------------------------------------------------------------
# Scenario config path confinement
# ---------------------------------------------------------------------------
#
# ``network.osm_file``, ``fleet.idm_calibration`` and
# ``fleet.heavy.idm_calibration`` are server-side paths the worker reads, and
# a parse failure there (``IDMCalibration.load`` on a non-JSON file) quotes
# the file's bytes into the run's error text. They are confined like
# ``data_path``: the repo's artifacts/ and data/, FLOWSTATE_DATA_DIR, and the
# results root. POST /scenarios only validates, so these run without SUMO.


def _micro_config(**overrides: Any) -> dict[str, Any]:
    cfg = macro_corridor_config(name="path_confinement", tier="micro")
    cfg.update(overrides)
    return cfg


def _post_config(client: TestClient, cfg: dict[str, Any]) -> tuple[int, str]:
    r = client.post("/api/v1/scenarios", json=cfg, headers=HEADERS)
    return r.status_code, r.text


@pytest.mark.parametrize("value", ["/etc/hosts", "../../etc/hosts", "artifacts/../../../etc/hosts"])
def test_fleet_calibration_outside_the_roots_is_refused(client: TestClient, value: str) -> None:
    status, text = _post_config(client, _micro_config(fleet={"idm_calibration": value}))
    assert status == 422
    assert "outside the allowed data roots" in text
    assert "fleet.idm_calibration" in text
    assert "localhost" not in text  # the refusal never reads the file


def test_osm_file_outside_the_roots_is_refused(client: TestClient) -> None:
    network = {"kind": "osm", "osm_file": "/etc/hosts", "inflow": [[0.0, 0.3]]}
    status, text = _post_config(client, _micro_config(network=network))
    assert status == 422
    assert "network.osm_file" in text
    assert "localhost" not in text


def test_heavy_calibration_outside_the_roots_is_refused(client: TestClient) -> None:
    heavy = {
        "fraction": 0.1,
        "length_m": 16.0,
        "emission_class": "HBEFA4/HDV_TT_Artic_ES_III",
        "idm_calibration": "/etc/hosts",
    }
    status, text = _post_config(client, _micro_config(fleet={"heavy": heavy}))
    assert status == 422
    assert "fleet.heavy.idm_calibration" in text


def test_config_symlink_out_of_the_data_root_is_refused(client: TestClient, tmp_path: Path) -> None:
    link = data_dir(tmp_path) / "innocent.json"
    link.symlink_to("/etc/hosts")
    status, text = _post_config(client, _micro_config(fleet={"idm_calibration": str(link)}))
    assert status == 422
    assert "outside the allowed data roots" in text


def test_repo_relative_artifact_path_is_allowed(client: TestClient) -> None:
    """Presets reference ``artifacts/...`` relative to the repo root, not the CWD."""
    cfg = _micro_config(fleet={"idm_calibration": "artifacts/idm_us101.json"})
    status, _ = _post_config(client, cfg)
    assert status == 201


def test_path_under_the_data_dir_is_allowed(client: TestClient, tmp_path: Path) -> None:
    cfg = _micro_config(fleet={"idm_calibration": str(data_dir(tmp_path) / "idm.json")})
    status, _ = _post_config(client, cfg)
    assert status == 201  # existence is the worker's concern, containment the API's


def test_every_shipped_preset_still_posts(client: TestClient) -> None:
    """The confinement must not reject the repo's own scenarios/*.yaml."""
    presets = client.get("/api/v1/scenarios/preset", headers=HEADERS).json()
    assert presets
    for preset in presets:
        r = client.post("/api/v1/scenarios", json=preset["config"], headers=HEADERS)
        assert r.status_code == 201, (preset["filename"], r.text)
    # The check is only meaningful if some preset carries a file field.
    assert any(
        p["config"]["fleet"]["idm_calibration"] is not None
        or p["config"]["network"].get("osm_file") is not None
        for p in presets
    )


def test_run_overrides_cannot_escape_the_roots(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    r = client.post(
        "/api/v1/runs",
        json={
            "scenario_id": scenario["scenario_id"],
            "overrides": {"fleet": {"idm_calibration": "/etc/hosts"}},
        },
        headers=HEADERS,
    )
    assert r.status_code == 422
    assert "outside the allowed data roots" in r.text
    assert "localhost" not in r.text
    assert client.get("/api/v1/runs", headers=HEADERS).json() == []  # nothing enqueued


def test_sweep_overrides_cannot_escape_the_roots(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": scenario["scenario_id"],
            "penetrations": [0.05],
            "compliances": [1.0],
            "overrides": {"fleet": {"idm_calibration": "/etc/hosts"}},
        },
        headers=HEADERS,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["cell"] == {"penetration": 0.05, "compliance": 1.0, "controller": None}
    assert "outside the allowed data roots" in str(detail["errors"])
