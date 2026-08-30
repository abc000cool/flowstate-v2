"""Deployment hardening: default-key refusal and calibration path confinement.

Two failures the pre-release review reproduced against the running service:

1. A deployed stack (``FLOWSTATE_QUEUE=redis``) booted happily on the API key
   published in this repository's README.
2. ``POST /api/v1/calibrations/{kind}`` took any ``data_path`` the caller
   named and handed it to a worker, which read and parsed it — ``/etc/hosts``
   included — and could echo a parsed value back through the job's error text.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.settings import DEFAULT_API_KEY, InsecureDefaultKeyError, load_settings
from tests.test_api.conftest import API_KEY, HEADERS, data_dir

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
