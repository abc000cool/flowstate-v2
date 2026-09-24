"""API-key middleware and health endpoint (CLAUDE.md §8: single-key auth)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_api.conftest import HEADERS


def test_healthz_is_exempt_and_ok(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["store"] == "ok"
    assert body["queue_kind"] == "inline"


def test_health_alias_matches_healthz(client: TestClient) -> None:
    """``/health`` is the dashboard's probe (a Cloud Run front end answers
    ``/healthz`` with its own 404); both are auth-exempt and identical."""
    a = client.get("/health")
    b = client.get("/healthz")
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()
    assert "/health" not in client.get("/openapi.json").json()["paths"]


def test_docs_are_exempt(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_api_requires_key(client: TestClient) -> None:
    assert client.get("/api/v1/scenarios").status_code == 401
    assert client.get("/api/v1/scenarios", headers={"X-API-Key": "wrong"}).status_code == 401


def test_api_accepts_key(client: TestClient) -> None:
    r = client.get("/api/v1/scenarios", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == []


def test_non_ascii_key_is_401_not_500(client: TestClient) -> None:
    """A high-bit byte in X-API-Key is a wrong key, not a crash.

    Starlette decodes header bytes as latin-1, and ``secrets.compare_digest``
    raises ``TypeError`` on a non-ASCII ``str`` — which used to surface as an
    unauthenticated 500 plus a logged traceback per request.
    """
    r = client.get("/api/v1/scenarios", headers={"X-API-Key": b"k\xe9"})
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid or missing X-API-Key"


def test_non_ascii_operator_key_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bytes comparison still accepts a correct key that is not ASCII."""
    monkeypatch.setenv("FLOWSTATE_QUEUE", "inline")
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("FLOWSTATE_API_KEY", "clé-secrète")
    from api.main import create_app

    with TestClient(create_app()) as c:
        assert (
            c.get("/api/v1/scenarios", headers={"X-API-Key": "clé-secrète".encode()}).status_code
            == 200
        )
        assert c.get("/api/v1/scenarios", headers={"X-API-Key": b"cle-secrete"}).status_code == 401
