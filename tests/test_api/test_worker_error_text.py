"""Job failure records are exception chains, never tracebacks.

``RunOut.error``, ``SweepOut.error`` and ``ReportOut.error`` (and the 409
detail built from them) are returned verbatim to any API-key holder; a
traceback there would print the server's absolute source paths and code
lines. The run/sweep/report record keeps third-party messages (they are the
run's diagnostic); the calibration record additionally withholds them —
``test_deploy_hardening.py`` covers that half.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import api
from api.jobs import _calibration_error_text, _error_text
from tests.test_api.conftest import HEADERS, macro_corridor_config, post_run, post_scenario

#: Every traceback frame of a job failure names a file under here.
_API_SRC = str(Path(api.__file__).resolve().parent)


def _failed_macro_run(client: TestClient) -> dict:
    # OSM networks are valid configs but unsupported by the macro runner.
    cfg = macro_corridor_config(
        name="macro_osm_fails",
        network={"kind": "osm", "bbox": [36.0, -87.0, 36.1, -86.9], "inflow": [[0.0, 0.3]]},
        replicates=2,
    )
    scenario = post_scenario(client, cfg)
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "failed"
    return run


def test_failed_run_error_is_a_chain_without_frames_or_paths(client: TestClient) -> None:
    run = _failed_macro_run(client)
    error = run["error"]
    assert error is not None
    # Still an honest, typed record with the runner's own message...
    assert error.startswith("NotImplementedError: ")
    assert "macro tier supports ring and corridor networks only" in error
    # ...but no traceback and no server path.
    assert "Traceback" not in error
    assert 'File "' not in error
    assert _API_SRC not in error
    assert "/Users/" not in error and "/home/" not in error and "/app/" not in error

    # The 409 detail that quotes the stored error is equally clean.
    r = client.get(f"/api/v1/runs/{run['run_id']}/metrics", headers=HEADERS)
    assert r.status_code == 409
    assert "NotImplementedError" in r.text
    assert "Traceback" not in r.text
    assert _API_SRC not in r.text


def test_failed_report_error_is_a_chain_without_frames(client: TestClient) -> None:
    run = _failed_macro_run(client)
    r = client.post("/api/v1/reports", json={"run_ids": [run["run_id"]]}, headers=HEADERS)
    assert r.status_code == 202, r.text
    report = r.json()
    assert report["status"] == "failed"
    error = report["error"]
    assert error == f"ValueError: run {run['run_id']!r} is failed, not done"
    assert "Traceback" not in error and _API_SRC not in error

    for route in ("markdown", "pdf", "archive"):
        r = client.get(f"/api/v1/reports/{report['report_id']}/{route}", headers=HEADERS)
        assert r.status_code == 409
        assert "Traceback" not in r.text and _API_SRC not in r.text


def test_error_text_walks_the_cause_chain() -> None:
    try:
        try:
            raise KeyError("inner")
        except KeyError as inner:
            raise RuntimeError("outer") from inner
    except RuntimeError as exc:
        text = _error_text(exc)
    assert text == "RuntimeError: outer\n  caused by: KeyError: 'inner'"


def test_error_text_keeps_third_party_messages_but_calibration_text_withholds_them() -> None:
    try:
        float("SECRET-CELL")  # raised inside the interpreter, not our packages
    except ValueError as exc:
        run_text = _error_text(exc)
        cal_text = _calibration_error_text(exc)
    assert run_text == "ValueError: could not convert string to float: 'SECRET-CELL'"
    assert "SECRET-CELL" not in cal_text
    assert cal_text.startswith("ValueError raised in ")
    assert "message withheld" in cal_text


def test_error_text_drops_pydantic_input_values() -> None:
    """A ValidationError quotes its input; when the worker validated a file it
    read (a calibration artifact), the quote is the file's contents. The run
    error keeps locations and messages only."""
    from pydantic import BaseModel

    class Artifact(BaseModel):
        population: dict[str, float]

    try:
        Artifact.model_validate_json('{"population": "SECRET-LINE-OF-A-FILE"}')
    except Exception as exc:  # pydantic.ValidationError
        text = _error_text(exc)
    assert text.startswith("ValidationError: ")
    assert "validation error" in text
    assert "SECRET-LINE-OF-A-FILE" not in text
    assert "population" in text
