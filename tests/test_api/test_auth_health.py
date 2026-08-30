"""API-key middleware and health endpoint (CLAUDE.md §8: single-key auth)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_api.conftest import HEADERS


def test_healthz_is_exempt_and_ok(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["store"] == "ok"
    assert body["queue_kind"] == "inline"


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
