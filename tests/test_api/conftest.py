"""Shared fixtures for the API tests.

Unmarked tests run the inline queue against a temporary results root — fast,
no SUMO. The micro-tier round trip is marked ``integration`` per
docs/CONTRACTS.md §8; the Redis-backed test spins a throwaway
``redis-server`` when one is on PATH and skips otherwise.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

API_KEY = "test-key-42"
HEADERS = {"X-API-Key": API_KEY}


def data_dir(tmp_path: Path) -> Path:
    """The allow-listed ``FLOWSTATE_DATA_DIR`` the ``client`` fixture sets.

    Server-side ``data_path`` reads are confined to the results root and this
    directory, so a test staging an input file for ``POST /calibrations``
    must put it here (anywhere else is refused with 422 — see
    ``test_calibration_data_path_traversal``).
    """
    path = tmp_path / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """TestClient over a fresh app: inline queue + tmp results/data dirs."""
    monkeypatch.setenv("FLOWSTATE_QUEUE", "inline")
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(data_dir(tmp_path)))
    monkeypatch.setenv("FLOWSTATE_API_KEY", API_KEY)
    from api.main import create_app

    with TestClient(create_app()) as c:
        yield c


def macro_corridor_config(**overrides: Any) -> dict[str, Any]:
    """Small 1 km corridor, macro tier, 3 replicates — fast inline runs."""
    cfg: dict[str, Any] = {
        "name": "api_macro_corridor",
        "tier": "macro",
        "network": {
            "kind": "corridor",
            "length_m": 1000.0,
            "lanes": 1,
            "inflow": [[0.0, 0.3]],
        },
        "sim": {"duration_s": 120.0, "step_length_s": 0.5, "output_hz": 1.0},
        "seed": 7,
        "replicates": 3,
    }
    cfg.update(overrides)
    return cfg


def post_scenario(client: TestClient, config: dict[str, Any]) -> dict[str, Any]:
    r = client.post("/api/v1/scenarios", json=config, headers=HEADERS)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def post_run(client: TestClient, scenario_id: str, **body: Any) -> dict[str, Any]:
    r = client.post("/api/v1/runs", json={"scenario_id": scenario_id, **body}, headers=HEADERS)
    assert r.status_code == 202, r.text
    return r.json()  # type: ignore[no-any-return]
