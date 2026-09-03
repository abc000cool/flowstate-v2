"""Report endpoints: the macro-only refusal is a 422, honestly surfaced.

The screening tier cannot back validation claims (CLAUDE.md §5.6);
``validation.report.generate_report`` raises ``ReportRefusedError`` and the
API surfaces it as HTTP 422. The successful (micro) path is covered by the
integration round trip in ``test_micro_integration.py``.
"""

from __future__ import annotations

from pathlib import Path

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
    assert client.get("/api/v1/reports/rpt_missing/pdf", headers=HEADERS).status_code == 404


def test_report_pdf_404_until_rendered(client: TestClient, tmp_path: Path) -> None:
    """A finished report answers 404 on /pdf until a report.pdf sits beside it."""
    store = client.app.state.store
    report_dir = tmp_path / "results" / "reports" / "rpt_pdf"
    report_dir.mkdir(parents=True)
    md_path = report_dir / "report.md"
    md_path.write_text("# report\n")
    report_id = store.create_report(["run_x"], "pdf route test")
    store.set_report_status(report_id, "done", report_dir=str(report_dir), report_path=str(md_path))

    r = client.get(f"/api/v1/reports/{report_id}/pdf", headers=HEADERS)
    assert r.status_code == 404
    assert "PDF" in r.json()["detail"]

    (report_dir / "report.pdf").write_bytes(b"%PDF-1.4\n%stub\n")
    r = client.get(f"/api/v1/reports/{report_id}/pdf", headers=HEADERS)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
