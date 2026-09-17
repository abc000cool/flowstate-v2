"""Job failure records are exception chains, never tracebacks.

``RunOut.error``, ``SweepOut.error`` and ``ReportOut.error`` (and the 409
detail built from them) are returned verbatim to any API-key holder; a
traceback there would print the server's absolute source paths and code
lines. The run/sweep/report record keeps third-party messages (they are the
run's diagnostic); the calibration record additionally withholds them —
``test_deploy_hardening.py`` covers that half.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api
from api.jobs import _calibration_error_text, _error_text
from tests.test_api.conftest import (
    HEADERS,
    data_dir,
    macro_corridor_config,
    post_run,
    post_scenario,
)

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


def test_spawn_pool_child_traceback_is_withheld() -> None:
    """The micro tier's failure mode, unit-level, beside the guard it tests.

    Every micro run fans its replicates out through
    ``microsim.runner.run_replicates`` → ``multiprocessing.Pool.map``, and
    CPython re-raises a child's exception in the parent with
    ``__cause__ = multiprocessing.pool.RemoteTraceback`` whose *message* is
    the child's whole formatted traceback — the server's interpreter path,
    repository path and source lines. The end-to-end case lives in
    ``test_worker_path_roots.py``; this pins the renderer itself, so a
    refactor of that file cannot leave the guard uncovered.
    """
    from multiprocessing.pool import RemoteTraceback

    child_traceback = (
        'Traceback (most recent call last):\n  File "/srv/flowstate/packages/microsim/'
        'microsim/runner.py", line 1582, in _replicate_worker\n    run_micro(cfg)\n'
        "FileNotFoundError: osm_file not found: artifacts/nope.osm\n"
    )
    exc = ValueError("osm_file not found: artifacts/nope.osm")
    exc.__cause__ = RemoteTraceback(child_traceback)

    text = _error_text(exc)
    assert text.startswith("ValueError: osm_file not found: artifacts/nope.osm")
    assert "RemoteTraceback: child traceback withheld" in text
    assert "Traceback (most recent call last)" not in text
    assert 'File "' not in text
    assert "/srv/flowstate" not in text
    # The calibration record withholds it too, whatever the filter setting.
    assert "Traceback (most recent call last)" not in _calibration_error_text(exc)


@pytest.mark.integration
def test_a_failed_replicate_does_not_re_embed_the_childs_message(
    client: TestClient, tmp_path: Path
) -> None:
    """The pool wrapper names the seed and the type — never the child's text.

    ``api.jobs._exception_chain_text`` strips a pydantic ValidationError's
    quoted input, because when the worker validated a file it read (here an
    ``IDMCalibration`` artifact the caller pointed the fleet at) the quote is
    that file's contents. The micro tier's pool wrapper used to interpolate
    ``{exc}`` — the child's fully rendered message — into its own
    ``RuntimeError``, which is FlowState-authored and therefore kept
    verbatim: the scrubbed text was put straight back.
    """
    marker = "MARKER-INSIDE-THE-ARTIFACT"
    artifact = data_dir(tmp_path) / "bad_idm.json"  # parseable JSON, wrong schema
    artifact.write_text(
        json.dumps(
            {
                "kind": "idm",
                "created_at": "2026-09-17T00:00:00Z",
                "source": "schema-invalid fixture",
                "data_hash": "h",
                "mean": marker,  # must be an object of the five IDM means
                "cov": [[0.0] * 5 for _ in range(5)],
                "n_episodes_fit": 1,
                "n_episodes_holdout": 1,
                "holdout_gap_rmse_m": 1.0,
            }
        )
    )
    scenario = post_scenario(
        client,
        {
            "name": "bad_calibration_artifact",
            "tier": "micro",
            "network": {"kind": "ring", "circumference_m": 230.0, "n_vehicles": 5},
            "fleet": {"model": "IDM", "idm_calibration": str(artifact)},
            "sim": {"duration_s": 10.0},
            "seed": 42,
            "replicates": 1,
        },
    )
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "failed"
    error = run["error"]

    # Honest and actionable: which replicate, which failure, which field.
    assert "micro replicate seed=" in error
    assert "ValidationError" in error and "mean" in error
    # ...without the input the child quoted, or the frames around it.
    assert "input_value=" not in error
    assert marker not in error
    assert "Traceback (most recent call last)" not in error
    assert 'File "' not in error and _API_SRC not in error
    assert "_RemoteTraceback: child traceback withheld" in error


def test_a_message_that_is_itself_a_traceback_is_withheld() -> None:
    """Belt and braces: any frame-bearing message, whatever raised it."""

    class _Odd(Exception):
        pass

    exc = _Odd('Traceback (most recent call last):\n  File "/srv/x.py", line 2, in f\n')
    text = _error_text(exc)
    assert text == "_Odd: child traceback withheld (see the worker log)"
