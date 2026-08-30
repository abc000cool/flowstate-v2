"""Report endpoints: the macro-only refusal is a 422, honestly surfaced.

The screening tier cannot back validation claims (CLAUDE.md §5.6);
``validation.report.generate_report`` raises ``ReportRefusedError`` and the
API surfaces it as HTTP 422. The successful (micro) path is covered by the
integration round trip in ``test_micro_integration.py``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_api.conftest import HEADERS, macro_corridor_config, post_run, post_scenario


def test_macro_only_report_is_refused_422(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]

    r = client.post("/api/v1/reports", json={"run_ids": [run["run_id"]]}, headers=HEADERS)
    assert r.status_code == 422, r.text
    assert "screening" in r.json()["detail"]


def test_report_unknown_run_404(client: TestClient) -> None:
    r = client.post("/api/v1/reports", json={"run_ids": ["run_missing"]}, headers=HEADERS)
    assert r.status_code == 404


def test_report_empty_run_ids_422(client: TestClient) -> None:
    r = client.post("/api/v1/reports", json={"run_ids": []}, headers=HEADERS)
    assert r.status_code == 422


def test_missing_report_404(client: TestClient) -> None:
    assert client.get("/api/v1/reports/rpt_missing", headers=HEADERS).status_code == 404
    assert client.get("/api/v1/reports/rpt_missing/markdown", headers=HEADERS).status_code == 404
