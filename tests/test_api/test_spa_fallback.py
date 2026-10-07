"""The built dashboard is served with ``index.html`` for its client-side routes (2026-10-07)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .conftest import API_KEY


@pytest.fixture()
def spa_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>FlowState</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("FLOWSTATE_QUEUE", "inline")
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("FLOWSTATE_API_KEY", API_KEY)
    monkeypatch.setenv("FLOWSTATE_FRONTEND_DIST", str(dist))
    from api.main import create_app

    with TestClient(create_app()) as c:
        yield c


def test_client_routes_get_the_dashboard(spa_client: TestClient) -> None:
    for path in ("/", "/runs", "/runs/run_abc123", "/sweeps", "/first-run"):
        r = spa_client.get(path)
        assert r.status_code == 200, path
        assert "<title>FlowState</title>" in r.text, path


def test_assets_are_served_and_missing_files_stay_404(spa_client: TestClient) -> None:
    assert spa_client.get("/assets/app.js").text == "console.log(1)"
    assert spa_client.get("/assets/missing.js").status_code == 404
    assert spa_client.get("/favicon.ico").status_code == 404


def test_api_paths_never_fall_back_to_the_dashboard(spa_client: TestClient) -> None:
    r = spa_client.get("/api/v1/no-such-route", headers={"X-API-Key": API_KEY})
    assert r.status_code == 404
    assert "FlowState" not in r.text
