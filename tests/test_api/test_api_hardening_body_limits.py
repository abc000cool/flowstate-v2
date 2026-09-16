"""Request-size ceilings, calibration fit-option bounds and upload naming.

Reproduced against the running service: one API-key holder (or a retry
loop) could exhaust the API process's RAM with a multi-GB upload
(``await file.read()`` buffered it whole), post a 20 MB YAML body, hand the
single RQ worker an ``n_bootstrap=1e12`` fit that pins it for the six-hour
job timeout, or 500 the upload handler with a client filename of ``..``.

The caps are ``FLOWSTATE_MAX_BODY_MB`` / ``FLOWSTATE_MAX_UPLOAD_MB``; the
tests here set them to 1 MB so oversized requests stay small.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.schemas import MAX_DE_MAXITER, MAX_DE_POPSIZE, MAX_N_BOOTSTRAP
from api.settings import DEFAULT_MAX_BODY_MB, DEFAULT_MAX_UPLOAD_MB, load_settings
from tests.test_api.conftest import API_KEY, HEADERS, macro_corridor_config

_MB = 1024 * 1024
_CSV = b"density_veh_m,flow_veh_s\n0.01,0.3\n"


@pytest.fixture()
def small_caps_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client whose body and upload caps are both 1 MB."""
    monkeypatch.setenv("FLOWSTATE_QUEUE", "inline")
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("FLOWSTATE_API_KEY", API_KEY)
    monkeypatch.setenv("FLOWSTATE_MAX_BODY_MB", "1")
    monkeypatch.setenv("FLOWSTATE_MAX_UPLOAD_MB", "1")
    from api.main import create_app

    with TestClient(create_app()) as c:
        yield c


def _post_fd(client: TestClient, **kwargs: object) -> object:
    return client.post("/api/v1/calibrations/fd", headers=HEADERS, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_default_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOWSTATE_MAX_BODY_MB", raising=False)
    monkeypatch.delenv("FLOWSTATE_MAX_UPLOAD_MB", raising=False)
    settings = load_settings()
    assert settings.max_body_bytes == DEFAULT_MAX_BODY_MB * _MB
    assert settings.max_upload_bytes == DEFAULT_MAX_UPLOAD_MB * _MB
    assert (DEFAULT_MAX_BODY_MB, DEFAULT_MAX_UPLOAD_MB) == (8, 200)  # as documented


@pytest.mark.parametrize("value", ["0", "-3", "abc", "1.5"])
def test_non_positive_cap_env_is_refused(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("FLOWSTATE_MAX_UPLOAD_MB", value)
    with pytest.raises(ValueError, match="FLOWSTATE_MAX_UPLOAD_MB"):
        load_settings()


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


def test_oversized_declared_body_is_413(small_caps_client: TestClient) -> None:
    """Content-Length over the cap is refused by the middleware, unread."""
    body = b'{"name": "x"' + b" " * _MB + b"}"
    r = small_caps_client.post("/api/v1/scenarios", content=body, headers=HEADERS)
    assert r.status_code == 413
    assert "exceeds the limit" in r.text
    assert str(_MB) in r.text
    assert small_caps_client.get("/api/v1/scenarios", headers=HEADERS).json() == []


def test_oversized_chunked_body_is_413(small_caps_client: TestClient) -> None:
    """A chunked body declares no length; the handler's streaming cap catches it."""

    def chunks() -> Iterator[bytes]:
        yield b'{"name": "x"'
        for _ in range(_MB // 65536 + 1):
            yield b" " * 65536
        yield b"}"

    r = small_caps_client.post(
        "/api/v1/scenarios",
        content=chunks(),
        headers={**HEADERS, "Content-Type": "application/x-yaml"},
    )
    assert r.status_code == 413
    assert "FLOWSTATE_MAX_BODY_MB" in r.text


def test_body_at_the_cap_is_accepted(small_caps_client: TestClient) -> None:
    """The ceiling is inclusive: exactly cap bytes still parses and stores."""
    head = json.dumps(macro_corridor_config()).encode() + b"\n# "
    body = head + b"x" * (_MB - len(head))
    assert len(body) == _MB
    r = small_caps_client.post(
        "/api/v1/scenarios",
        content=body,
        headers={**HEADERS, "Content-Type": "application/x-yaml"},
    )
    assert r.status_code == 201, r.text


def test_json_endpoints_are_capped_too(small_caps_client: TestClient) -> None:
    body = b'{"scenario_id": "x", "overrides": {"pad": "' + b"x" * _MB + b'"}}'
    r = small_caps_client.post(
        "/api/v1/runs", content=body, headers={**HEADERS, "Content-Type": "application/json"}
    )
    assert r.status_code == 413


def test_cap_is_enforced_after_auth(small_caps_client: TestClient) -> None:
    """An anonymous oversized request is a 401, not a 413 (no size oracle for strangers)."""
    r = small_caps_client.post("/api/v1/scenarios", content=b" " * (_MB + 1))
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------


def test_oversized_upload_is_413_and_leaves_no_file(small_caps_client: TestClient) -> None:
    """One byte over the upload cap: refused by the handler from the part size."""
    r = _post_fd(small_caps_client, files={"file": ("big.csv", b"x" * (_MB + 1), "text/csv")})
    assert r.status_code == 413  # type: ignore[attr-defined]
    assert "FLOWSTATE_MAX_UPLOAD_MB" in r.text  # type: ignore[attr-defined]
    uploads = Path(small_caps_client.app.state.settings.uploads_dir)  # type: ignore[attr-defined]
    assert not uploads.exists() or not any(uploads.iterdir())


def test_upload_declared_far_over_the_cap_is_413_from_the_middleware(
    small_caps_client: TestClient,
) -> None:
    """Multipart bodies get the upload cap plus body-cap slack for framing."""
    r = _post_fd(small_caps_client, files={"file": ("huge.csv", b"x" * (2 * _MB + 1), "text/csv")})
    assert r.status_code == 413  # type: ignore[attr-defined]
    assert "request body of" in r.text  # type: ignore[attr-defined]


def test_upload_within_the_cap_is_streamed_to_disk(small_caps_client: TestClient) -> None:
    payload = _CSV + b"0.02,0.5\n" * (512 * 1024 // 9)
    assert len(payload) < _MB
    r = _post_fd(small_caps_client, files={"file": ("loops.csv", payload, "text/csv")})
    assert r.status_code == 202, r.text  # type: ignore[attr-defined]
    stored = Path(r.json()["data_path"])  # type: ignore[attr-defined]
    assert stored.name == "loops.csv"
    assert stored.stat().st_size == len(payload)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("..", "upload.csv"),
        (".", "upload.csv"),
        ("../../etc/evil.csv", "evil.csv"),
        # httpx percent-encodes the NUL, so the server sees a benign name; the
        # NUL fallback in ``_upload_name`` is covered by its unit test below.
        ("a\x00b.csv", "a%00b.csv"),
    ],
)
def test_hostile_upload_filenames_land_inside_the_upload_dir(
    client: TestClient, filename: str, expected: str
) -> None:
    """``..`` used to 500 (``write_bytes`` on the parent directory).

    (A part with an *empty* filename is not an upload at all — the multipart
    parser hands it over as a plain form field and FastAPI answers 422.)
    """
    r = _post_fd(client, files={"file": (filename, _CSV, "text/csv")})
    assert r.status_code == 202, r.text  # type: ignore[attr-defined]
    stored = Path(r.json()["data_path"])  # type: ignore[attr-defined]
    uploads = Path(client.app.state.settings.uploads_dir).resolve()  # type: ignore[attr-defined]
    assert stored.resolve().is_relative_to(uploads)
    assert stored.parent.name.startswith("upl_")
    assert stored.name == expected
    assert stored.is_file()


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        (None, "upload.csv"),
        ("", "upload.csv"),
        (".", "upload.csv"),
        ("..", "upload.csv"),
        ("/", "upload.csv"),
        ("a\x00b.csv", "upload.csv"),
        ("../../etc/evil.csv", "evil.csv"),
        ("loops.csv", "loops.csv"),
    ],
)
def test_upload_name_sanitizer(filename: str | None, expected: str) -> None:
    from api.main import _upload_name

    assert _upload_name(filename) == expected


# ---------------------------------------------------------------------------
# Fit options
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"n_bootstrap": MAX_N_BOOTSTRAP + 1},
        {"n_bootstrap": 10**12},
        {"n_bootstrap": -1},
        {"min_points": -5},
        {"de_maxiter": MAX_DE_MAXITER + 1},
        {"de_maxiter": 10**9},
        {"de_popsize": MAX_DE_POPSIZE + 1},
        {"holdout_frac": 1.0},
        {"trim_quantile": 0.0},
        {"congested_quantile": 1.0},
        {"q_max_percentile": 101.0},
        {"loader": "excel"},
        {"speed_unit": "furlongs"},
        {"min_duration_s": 0.0},
        {"seed": "not-an-int"},
    ],
)
def test_out_of_range_fit_options_are_422(client: TestClient, params: dict) -> None:
    r = _post_fd(
        client, files={"file": ("loops.csv", _CSV, "text/csv")}, data={"params": json.dumps(params)}
    )
    assert r.status_code == 422, r.text  # type: ignore[attr-defined]
    detail = r.json()["detail"]  # type: ignore[attr-defined]
    assert isinstance(detail, list)
    (key,) = params
    assert detail[0]["loc"][:2] == ["params", key]
    assert client.get("/api/v1/scenarios", headers=HEADERS).status_code == 200  # still up


@pytest.mark.parametrize("params", [{"n_procs": 64}, {"bootstrap": 5}, {"created_at": "now"}])
def test_unknown_fit_options_are_refused_not_ignored(client: TestClient, params: dict) -> None:
    r = _post_fd(
        client, files={"file": ("loops.csv", _CSV, "text/csv")}, data={"params": json.dumps(params)}
    )
    assert r.status_code == 422  # type: ignore[attr-defined]
    detail = r.json()["detail"]  # type: ignore[attr-defined]
    assert detail[0]["type"] == "extra_forbidden"
    assert detail[0]["loc"] == ["params", next(iter(params))]


def test_only_the_set_options_reach_the_job(client: TestClient) -> None:
    """Defaults stay the fit functions' own; an explicit null means unset."""
    params = {"n_bootstrap": 5, "seed": 1, "uncongested_max_density": None, "notes": "api"}
    r = _post_fd(
        client, files={"file": ("loops.csv", _CSV, "text/csv")}, data={"params": json.dumps(params)}
    )
    assert r.status_code == 202, r.text  # type: ignore[attr-defined]
    row = client.app.state.store.get_calibration(r.json()["calibration_id"])  # type: ignore[attr-defined]
    assert row["params"] == {"n_bootstrap": 5, "seed": 1, "notes": "api"}


def test_params_is_optional_and_may_be_empty(client: TestClient) -> None:
    for data in ({}, {"params": "{}"}):
        r = _post_fd(client, files={"file": ("loops.csv", _CSV, "text/csv")}, data=data)
        assert r.status_code == 202, r.text  # type: ignore[attr-defined]
