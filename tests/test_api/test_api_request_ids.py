"""Per-request correlation ids: the ``X-Request-Id`` header and the access log.

A pilot operator reading "the dashboard said 500" had nothing to grep for:
responses carried no identifier, the service logged no line per request, and
an unhandled error answered with FastAPI's bare ``Internal Server Error``.

``api.main.RequestContextMiddleware`` now gives every request an id (the
client's when it is short and log-safe, else uuid4), echoes it on every
response — 401, 413, 422 and 500 included — and logs one line per request at
INFO on the ``api.access`` logger; the 500 body quotes the id instead of a
traceback.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from api.main import (
    ACCESS_LOGGER,
    REQUEST_ID_HEADER,
    configure_service_logging,
    create_app,
    request_id_of,
)
from tests.test_api.conftest import API_KEY, HEADERS

_MB = 1024 * 1024


@pytest.fixture()
def boom_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client whose app carries two extra routes, for what the app cannot do.

    ``/api/v1/boom`` raises (the 500 path) and ``/api/v1/echo-request-id``
    returns what a handler reads from ``request.state`` — the same place the
    error handler takes the id from.

    ``raise_server_exceptions=False`` makes the test client behave like a
    real ASGI server: the error handler's response is returned instead of the
    exception being re-raised into the test.
    """
    monkeypatch.setenv("FLOWSTATE_QUEUE", "inline")
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("FLOWSTATE_API_KEY", API_KEY)
    monkeypatch.setenv("FLOWSTATE_MAX_BODY_MB", "1")
    # No frontend mount: a StaticFiles mount at "/" would shadow a route added
    # after ``create_app`` returned.
    monkeypatch.setenv("FLOWSTATE_FRONTEND_DIST", str(tmp_path / "no-frontend"))
    app = create_app()

    @app.get("/api/v1/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/api/v1/echo-request-id")
    def echo_request_id(request: Request) -> dict[str, str]:
        return {"request_id": request.state.request_id}

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _chunked(body: bytes, pad_bytes: int) -> Iterator[bytes]:
    yield body
    remaining = pad_bytes
    while remaining > 0:
        take = min(remaining, 65536)
        yield b"x" * take
        remaining -= take
    yield b'"}}'


# ---------------------------------------------------------------------------
# The id itself
# ---------------------------------------------------------------------------


def test_generated_id_is_a_uuid4(client: TestClient) -> None:
    r = client.get("/api/v1/scenarios", headers=HEADERS)
    assert r.status_code == 200
    generated = r.headers[REQUEST_ID_HEADER]
    assert uuid.UUID(generated).version == 4


def test_ids_differ_between_requests(client: TestClient) -> None:
    first = client.get("/healthz").headers[REQUEST_ID_HEADER]
    second = client.get("/healthz").headers[REQUEST_ID_HEADER]
    assert first != second


def test_a_clients_id_is_echoed(client: TestClient) -> None:
    """A caller correlating its own logs keeps its id end to end."""
    supplied = "trace-01.abc_DEF-9"
    r = client.get("/api/v1/scenarios", headers={**HEADERS, REQUEST_ID_HEADER: supplied})
    assert r.headers[REQUEST_ID_HEADER] == supplied


@pytest.mark.parametrize(
    "supplied",
    [
        "x" * 65,  # over 64 characters
        "has space",
        "semi;colon",
        "new\nline",  # a log-line injection attempt
        "sl/ash",
        "",
    ],
)
def test_an_unusable_client_id_is_replaced(client: TestClient, supplied: str) -> None:
    r = client.get("/api/v1/scenarios", headers={**HEADERS, REQUEST_ID_HEADER: supplied})
    echoed = r.headers[REQUEST_ID_HEADER]
    assert echoed != supplied
    assert uuid.UUID(echoed).version == 4


@pytest.mark.parametrize(
    ("supplied", "reused"),
    [
        ("a", True),
        ("x" * 64, True),
        ("0123456789abcdef-0123_4567.89", True),
        ("x" * 65, False),
        ("a b", False),
        ("a\rb", False),
        ("trailing\n", False),  # ``$`` would accept this; ``\Z`` does not
        (None, False),
    ],
)
def test_request_id_of_validates(supplied: str | None, reused: bool) -> None:
    result = request_id_of(supplied)
    assert (result == supplied) is reused


# ---------------------------------------------------------------------------
# Echoed on every response, whatever answered it
# ---------------------------------------------------------------------------


def test_id_on_401(client: TestClient) -> None:
    r = client.get("/api/v1/scenarios")
    assert r.status_code == 401
    assert uuid.UUID(r.headers[REQUEST_ID_HEADER]).version == 4


def test_id_on_422(client: TestClient) -> None:
    r = client.post("/api/v1/runs", json={"nope": 1}, headers=HEADERS)
    assert r.status_code == 422
    assert REQUEST_ID_HEADER in r.headers


def test_id_on_404(client: TestClient) -> None:
    r = client.get("/api/v1/runs/run_missing", headers=HEADERS)
    assert r.status_code == 404
    assert REQUEST_ID_HEADER in r.headers


def test_id_on_declared_413(boom_client: TestClient) -> None:
    r = boom_client.post("/api/v1/scenarios", content=b" " * (_MB + 1), headers=HEADERS)
    assert r.status_code == 413
    assert REQUEST_ID_HEADER in r.headers


def test_id_on_streamed_413(boom_client: TestClient) -> None:
    """The 413 the body counter writes itself carries the id too."""
    head = b'{"scenario_id": "nope", "overrides": {"name": "'
    r = boom_client.post(
        "/api/v1/runs",
        content=_chunked(head, _MB),
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert REQUEST_ID_HEADER in r.headers


def test_id_on_500_and_no_traceback(boom_client: TestClient) -> None:
    supplied = "caller-id-42"
    r = boom_client.get("/api/v1/boom", headers={**HEADERS, REQUEST_ID_HEADER: supplied})
    assert r.status_code == 500
    assert r.headers[REQUEST_ID_HEADER] == supplied
    assert r.json() == {"detail": f"internal error; request id {supplied}"}
    assert "secret internal detail" not in r.text
    assert "Traceback" not in r.text


# ---------------------------------------------------------------------------
# Access log
# ---------------------------------------------------------------------------


def _access_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [rec.getMessage() for rec in caplog.records if rec.name == "api.access"]


def test_one_access_line_per_request(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="api.access"):
        r = client.get("/api/v1/scenarios", headers={**HEADERS, REQUEST_ID_HEADER: "log-me-1"})
    assert r.status_code == 200
    lines = _access_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("GET /api/v1/scenarios 200 ")
    assert "ms request_id=log-me-1" in line
    assert all(rec.levelno == logging.INFO for rec in caplog.records if rec.name == "api.access")


def test_access_line_reports_the_real_status(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="api.access"):
        client.get("/api/v1/scenarios")  # no key
        client.post("/api/v1/runs", json={"nope": 1}, headers=HEADERS)
    statuses = [line.split()[2] for line in _access_lines(caplog)]
    assert statuses == ["401", "422"]


def test_access_line_for_an_unhandled_error(
    boom_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="api.access"):
        boom_client.get("/api/v1/boom", headers={**HEADERS, REQUEST_ID_HEADER: "boom-1"})
    (line,) = _access_lines(caplog)
    assert line.startswith("GET /api/v1/boom 500 ")
    assert line.endswith("request_id=boom-1")


def test_the_id_is_on_request_state(boom_client: TestClient) -> None:
    """Handlers read the same id from ``request.state`` that goes out."""
    r = boom_client.get(
        "/api/v1/echo-request-id", headers={**HEADERS, REQUEST_ID_HEADER: "state-1"}
    )
    assert r.status_code == 200
    assert r.json() == {"request_id": "state-1"}
    assert r.headers[REQUEST_ID_HEADER] == "state-1"


def test_the_id_survives_a_rejected_body(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A refused request is still the one the caller and the log agree on."""
    with caplog.at_level(logging.INFO, logger="api.access"):
        r = client.post(
            "/api/v1/scenarios",
            content=json.dumps({"not": "a config"}),
            headers={**HEADERS, REQUEST_ID_HEADER: "state-1"},
        )
    assert r.status_code == 422
    assert r.headers[REQUEST_ID_HEADER] == "state-1"
    assert "request_id=state-1" in _access_lines(caplog)[0]


# ---------------------------------------------------------------------------
# The access log has to be emitted, not just logged
# ---------------------------------------------------------------------------


def _unconfigured_logging(monkeypatch: pytest.MonkeyPatch) -> logging.Logger:
    """A process that has configured no logging, as under uvicorn.

    ``uvicorn api.main:app`` configures uvicorn's own loggers only: the root
    logger keeps level WARNING and no handler. Both attributes are patched
    back to that state (pytest has installed its own root handler) and
    restored when the test ends.
    """
    root = logging.getLogger()
    api_logger = logging.getLogger("api")
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", logging.WARNING)
    monkeypatch.setattr(api_logger, "handlers", [])
    monkeypatch.setattr(api_logger, "level", logging.NOTSET)
    return api_logger


def test_the_access_line_reaches_stderr_under_uvicorn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without this the documented access log is silently empty in the container."""
    api_logger = _unconfigured_logging(monkeypatch)
    assert ACCESS_LOGGER.getEffectiveLevel() == logging.WARNING  # INFO dropped

    configure_service_logging()

    assert ACCESS_LOGGER.getEffectiveLevel() == logging.INFO
    assert len(api_logger.handlers) == 1
    ACCESS_LOGGER.info("GET /api/v1/runs 202 1.0ms request_id=emitted-1")
    assert "request_id=emitted-1" in capsys.readouterr().err


def test_an_already_configured_process_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator's own logging setup is never overridden or duplicated."""
    api_logger = _unconfigured_logging(monkeypatch)
    monkeypatch.setattr(logging.getLogger(), "handlers", [logging.NullHandler()])

    configure_service_logging()

    assert api_logger.handlers == []
    assert api_logger.level == logging.NOTSET


def test_configuring_twice_adds_one_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """``create_app`` is called per test process and per app; lines never double."""
    api_logger = _unconfigured_logging(monkeypatch)
    configure_service_logging()
    configure_service_logging()
    assert len(api_logger.handlers) == 1
