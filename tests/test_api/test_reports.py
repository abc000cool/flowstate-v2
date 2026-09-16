"""Report endpoints: the macro-only refusal is a 422, honestly surfaced.

The screening tier cannot back validation claims (CLAUDE.md §5.6);
``validation.report.generate_report`` raises ``ReportRefusedError`` and the
API surfaces it as HTTP 422. The successful (micro) path is covered by the
integration round trip in ``test_micro_integration.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
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


# ---------------------------------------------------------------------------
# report_job: one generate_report call, PDF decided up front, refusal intact
# ---------------------------------------------------------------------------


class _Recorder:
    """Stands in for ``validation.report.generate_report``."""

    def __init__(self, raise_refusal: bool) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_refusal = raise_refusal

    def __call__(self, stage_dir: Any, out_path: Any, **kwargs: Any) -> Any:
        from validation.report import ReportRefusedError

        self.calls.append(kwargs)
        if self.raise_refusal:
            raise ReportRefusedError("refused: run set holds screening-tier runs only")
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# stub\n")
        if kwargs.get("pdf"):
            out.with_name("report.pdf").write_bytes(b"%PDF-1.4\n%stub\n")
            return out, out.with_name("report.pdf")
        return out


def _post_report(client: TestClient, run_id: str) -> Any:
    return client.post("/api/v1/reports", json={"run_ids": [run_id]}, headers=HEADERS)


def test_refusal_is_recorded_from_a_single_generate_report_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ReportRefusedError`` (a RuntimeError) is never mistaken for "no PDF".

    The old code tried ``pdf=True`` first and re-ran the whole generator on
    any RuntimeError, so a refusal cost two renders; now the refusal
    surfaces from the one and only call.
    """
    import validation.report

    recorder = _Recorder(raise_refusal=True)
    monkeypatch.setattr(validation.report, "generate_report", recorder)
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]

    r = _post_report(client, run["run_id"])
    assert r.status_code == 422, r.text
    assert "screening" in r.json()["detail"]
    assert len(recorder.calls) == 1


@pytest.mark.parametrize("fpdf_installed", [True, False])
def test_pdf_is_requested_only_when_fpdf_is_importable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fpdf_installed: bool
) -> None:
    """One call, ``pdf`` decided beforehand: no render-twice on a slim deployment."""
    import api.jobs
    import validation.report

    recorder = _Recorder(raise_refusal=False)
    monkeypatch.setattr(validation.report, "generate_report", recorder)
    monkeypatch.setattr(api.jobs, "_pdf_available", lambda: fpdf_installed)
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])

    r = _post_report(client, run["run_id"])
    assert r.status_code == 202, r.text
    report = r.json()
    assert report["status"] == "done", report["error"]
    assert len(recorder.calls) == 1
    assert bool(recorder.calls[0].get("pdf", False)) is fpdf_installed

    pdf = client.get(f"/api/v1/reports/{report['report_id']}/pdf", headers=HEADERS)
    assert pdf.status_code == (200 if fpdf_installed else 404)


def test_pdf_availability_probe_matches_the_import() -> None:
    import importlib.util

    from api.jobs import _pdf_available

    assert _pdf_available() is (importlib.util.find_spec("fpdf") is not None)
