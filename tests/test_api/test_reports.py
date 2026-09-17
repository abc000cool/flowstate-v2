"""Report endpoints: tier honesty, path hygiene, and the report listing.

The screening tier cannot back validation claims (CLAUDE.md §5.6): an
all-macro set is refused by ``validation.report.generate_report``
(``ReportRefusedError`` → HTTP 422), and a *mixed* micro+macro set is refused
by the API before the row is created, because the generator would otherwise
drop the macro runs from the metrics while still listing them under
Provenance. ``report_path`` is published relative to the results root rather
than as a server filesystem path, and ``GET /reports`` lists reports so the
dashboard's table is not one browser's localStorage. The successful (micro)
path is covered by the integration round trip in ``test_micro_integration.py``.
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


# ---------------------------------------------------------------------------
# Tier honesty, path hygiene and the report listing
# ---------------------------------------------------------------------------


def _store_run(client: TestClient, tier: str) -> str:
    """A finished run row of ``tier`` without executing a simulation.

    The micro tier needs SUMO, which the unmarked tests do not run; the
    report-set checks care only about the stored ``tier``.
    """
    store = client.app.state.store
    return store.create_run(
        scenario_id=None,
        config={"name": f"{tier}_stub", "tier": tier},
        config_hash="0123456789ab",
        tier=tier,
        seeds=[1],
        run_root=str(Path(store.db_path).parent / "runs" / f"{tier}_stub"),
    )


def test_mixed_micro_and_macro_run_set_is_refused_422(client: TestClient) -> None:
    """A screening run may not ride along in a validation report's provenance.

    ``validation.report.generate_report`` drops macro runs from the metrics
    but still lists them under Provenance with no note that they contributed
    nothing — so the report would read as if screening-tier evidence backed
    it (CLAUDE.md §5.6). The mixed set is refused, naming the macro runs.
    """
    scenario = post_scenario(client, macro_corridor_config())
    macro = post_run(client, scenario["scenario_id"])
    micro_id = _store_run(client, "micro")

    r = client.post(
        "/api/v1/reports",
        json={"run_ids": [micro_id, macro["run_id"]]},
        headers=HEADERS,
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert macro["run_id"] in detail
    assert micro_id not in detail
    assert "screening" in detail
    # Refused before anything was stored or enqueued: no half-made report row.
    assert client.app.state.store.list_reports() == []


def test_all_micro_run_set_is_accepted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tier check refuses mixes only — an all-micro set still goes through."""
    import validation.report

    monkeypatch.setattr(validation.report, "generate_report", _Recorder(raise_refusal=False))
    micro_id = _store_run(client, "micro")
    r = client.post("/api/v1/reports", json={"run_ids": [micro_id]}, headers=HEADERS)
    assert r.status_code == 202, r.text


def _done_report(client: TestClient, tmp_path: Path, report_id_hint: str) -> dict[str, Any]:
    """A finished report bundle on disk + its store row (no generator run)."""
    store = client.app.state.store
    report_id = store.create_report(["run_x"], f"report {report_id_hint}")
    report_dir = tmp_path / "results" / "reports" / report_id
    report_dir.mkdir(parents=True)
    md_path = report_dir / "report.md"
    md_path.write_text("# report\n")
    store.set_report_status(report_id, "done", report_dir=str(report_dir), report_path=str(md_path))
    return {"report_id": report_id, "dir": report_dir, "md": md_path}


def test_report_path_is_results_relative_not_a_server_path(
    client: TestClient, tmp_path: Path
) -> None:
    """``report_path`` identifies the bundle; it never publishes the server's layout."""
    made = _done_report(client, tmp_path, "relative")
    r = client.get(f"/api/v1/reports/{made['report_id']}", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report_path"] == f"reports/{made['report_id']}/report.md"
    assert str(tmp_path) not in r.text
    assert not body["report_path"].startswith("/")
    # The download routes still read the absolute path the store kept.
    md = client.get(f"/api/v1/reports/{made['report_id']}/markdown", headers=HEADERS)
    assert md.status_code == 200
    assert md.text == "# report\n"


def test_reports_listing_is_newest_first_and_bounded(client: TestClient, tmp_path: Path) -> None:
    """``GET /reports`` exists so the dashboard's list is not localStorage-only."""
    assert client.get("/api/v1/reports", headers=HEADERS).json() == []

    first = _done_report(client, tmp_path, "one")
    second = _done_report(client, tmp_path, "two")

    r = client.get("/api/v1/reports", headers=HEADERS)
    assert r.status_code == 200, r.text
    listing = r.json()
    assert [row["report_id"] for row in listing] == [second["report_id"], first["report_id"]]
    assert listing[0]["status"] == "done"
    assert listing[0]["report_path"] == f"reports/{second['report_id']}/report.md"
    assert str(tmp_path) not in r.text

    limited = client.get("/api/v1/reports?limit=1", headers=HEADERS).json()
    assert [row["report_id"] for row in limited] == [second["report_id"]]

    assert client.get("/api/v1/reports?limit=0", headers=HEADERS).status_code == 422
    assert client.get("/api/v1/reports?limit=201", headers=HEADERS).status_code == 422
