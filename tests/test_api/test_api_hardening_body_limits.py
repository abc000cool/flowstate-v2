"""Request-size ceilings, calibration fit-option bounds and upload naming.

Reproduced against the running service: one API-key holder (or a retry
loop) could exhaust the API process's RAM with a multi-GB upload
(``await file.read()`` buffered it whole), post a 20 MB YAML body, hand the
single RQ worker an ``n_bootstrap=1e12`` fit that pins it for the six-hour
job timeout, or 500 the upload handler with a client filename of ``..``.

The caps are ``FLOWSTATE_MAX_BODY_MB`` / ``FLOWSTATE_MAX_UPLOAD_MB``; the
tests here set them to 1 MB so oversized requests stay small.

A declared ``Content-Length`` is refused by the auth middleware; a body that
declares no length (chunked) is counted as it streams by
``api.main.BodyCapMiddleware`` — the last section here covers every route
that used to let Starlette read such a body whole.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.schemas import MAX_DE_MAXITER, MAX_DE_POPSIZE, MAX_N_BOOTSTRAP
from api.settings import DEFAULT_MAX_BODY_MB, DEFAULT_MAX_UPLOAD_MB, load_settings
from api.store import STATUSES
from tests.test_api.conftest import API_KEY, HEADERS, macro_corridor_config, post_scenario

_MB = 1024 * 1024
_CSV = b"density_veh_m,flow_veh_s\n0.01,0.3\n"


@pytest.fixture()
def small_caps_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client whose body and upload caps are both 1 MB."""
    monkeypatch.setenv("FLOWSTATE_QUEUE", "inline")
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("FLOWSTATE_API_KEY", API_KEY)
    monkeypatch.setenv("FLOWSTATE_MAX_BODY_MB", "1")
    monkeypatch.setenv("FLOWSTATE_MAX_UPLOAD_MB", "1")
    from api.main import create_app

    with TestClient(create_app()) as c:
        yield c


def _post_fd(client: TestClient, **kwargs: object) -> object:
    return client.post("/api/v1/calibrations/fd", headers=HEADERS, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_default_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOWSTATE_MAX_BODY_MB", raising=False)
    monkeypatch.delenv("FLOWSTATE_MAX_UPLOAD_MB", raising=False)
    settings = load_settings()
    assert settings.max_body_bytes == DEFAULT_MAX_BODY_MB * _MB
    assert settings.max_upload_bytes == DEFAULT_MAX_UPLOAD_MB * _MB
    assert (DEFAULT_MAX_BODY_MB, DEFAULT_MAX_UPLOAD_MB) == (8, 200)  # as documented


@pytest.mark.parametrize("value", ["0", "-3", "abc", "1.5"])
def test_non_positive_cap_env_is_refused(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("FLOWSTATE_MAX_UPLOAD_MB", value)
    with pytest.raises(ValueError, match="FLOWSTATE_MAX_UPLOAD_MB"):
        load_settings()


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


def test_oversized_declared_body_is_413(small_caps_client: TestClient) -> None:
    """Content-Length over the cap is refused by the middleware, unread."""
    body = b'{"name": "x"' + b" " * _MB + b"}"
    r = small_caps_client.post("/api/v1/scenarios", content=body, headers=HEADERS)
    assert r.status_code == 413
    assert "exceeds the limit" in r.text
    assert str(_MB) in r.text
    assert small_caps_client.get("/api/v1/scenarios", headers=HEADERS).json() == []


def test_oversized_chunked_body_is_413(small_caps_client: TestClient) -> None:
    """A chunked body declares no length; the streaming counter catches it.

    (Belt and braces: ``create_scenario`` caps its own stream read too, so
    this route was bounded even before ``BodyCapMiddleware`` existed.)
    """

    def chunks() -> Iterator[bytes]:
        yield b'{"name": "x"'
        for _ in range(_MB // 65536 + 1):
            yield b" " * 65536
        yield b"}"

    r = small_caps_client.post(
        "/api/v1/scenarios",
        content=chunks(),
        headers={**HEADERS, "Content-Type": "application/x-yaml"},
    )
    assert r.status_code == 413
    assert "FLOWSTATE_MAX_BODY_MB" in r.text


def test_body_at_the_cap_is_accepted(small_caps_client: TestClient) -> None:
    """The ceiling is inclusive: exactly cap bytes still parses and stores."""
    head = json.dumps(macro_corridor_config()).encode() + b"\n# "
    body = head + b"x" * (_MB - len(head))
    assert len(body) == _MB
    r = small_caps_client.post(
        "/api/v1/scenarios",
        content=body,
        headers={**HEADERS, "Content-Type": "application/x-yaml"},
    )
    assert r.status_code == 201, r.text


def test_json_endpoints_are_capped_too(small_caps_client: TestClient) -> None:
    body = b'{"scenario_id": "x", "overrides": {"pad": "' + b"x" * _MB + b'"}}'
    r = small_caps_client.post(
        "/api/v1/runs", content=body, headers={**HEADERS, "Content-Type": "application/json"}
    )
    assert r.status_code == 413


def test_cap_is_enforced_after_auth(small_caps_client: TestClient) -> None:
    """An anonymous oversized request is a 401, not a 413 (no size oracle for strangers)."""
    r = small_caps_client.post("/api/v1/scenarios", content=b" " * (_MB + 1))
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------


def test_oversized_upload_is_413_and_leaves_no_file(small_caps_client: TestClient) -> None:
    """One byte over the upload cap: refused by the handler from the part size."""
    r = _post_fd(small_caps_client, files={"file": ("big.csv", b"x" * (_MB + 1), "text/csv")})
    assert r.status_code == 413  # type: ignore[attr-defined]
    assert "FLOWSTATE_MAX_UPLOAD_MB" in r.text  # type: ignore[attr-defined]
    uploads = Path(small_caps_client.app.state.settings.uploads_dir)  # type: ignore[attr-defined]
    assert not uploads.exists() or not any(uploads.iterdir())


def test_upload_declared_far_over_the_cap_is_413_from_the_middleware(
    small_caps_client: TestClient,
) -> None:
    """Multipart bodies get the upload cap plus body-cap slack for framing."""
    r = _post_fd(small_caps_client, files={"file": ("huge.csv", b"x" * (2 * _MB + 1), "text/csv")})
    assert r.status_code == 413  # type: ignore[attr-defined]
    assert "request body of" in r.text  # type: ignore[attr-defined]


def test_upload_within_the_cap_is_streamed_to_disk(small_caps_client: TestClient) -> None:
    payload = _CSV + b"0.02,0.5\n" * (512 * 1024 // 9)
    assert len(payload) < _MB
    r = _post_fd(small_caps_client, files={"file": ("loops.csv", payload, "text/csv")})
    assert r.status_code == 202, r.text  # type: ignore[attr-defined]
    stored = Path(r.json()["data_path"])  # type: ignore[attr-defined]
    assert stored.name == "loops.csv"
    assert stored.stat().st_size == len(payload)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("..", "upload.csv"),
        (".", "upload.csv"),
        ("../../etc/evil.csv", "evil.csv"),
        # httpx percent-encodes the NUL, so the server sees a benign name; the
        # NUL fallback in ``_upload_name`` is covered by its unit test below.
        ("a\x00b.csv", "a%00b.csv"),
    ],
)
def test_hostile_upload_filenames_land_inside_the_upload_dir(
    client: TestClient, filename: str, expected: str
) -> None:
    """``..`` used to 500 (``write_bytes`` on the parent directory).

    (A part with an *empty* filename is not an upload at all — the multipart
    parser hands it over as a plain form field and FastAPI answers 422.)
    """
    r = _post_fd(client, files={"file": (filename, _CSV, "text/csv")})
    assert r.status_code == 202, r.text  # type: ignore[attr-defined]
    stored = Path(r.json()["data_path"])  # type: ignore[attr-defined]
    uploads = Path(client.app.state.settings.uploads_dir).resolve()  # type: ignore[attr-defined]
    assert stored.resolve().is_relative_to(uploads)
    assert stored.parent.name.startswith("upl_")
    assert stored.name == expected
    assert stored.is_file()


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        (None, "upload.csv"),
        ("", "upload.csv"),
        (".", "upload.csv"),
        ("..", "upload.csv"),
        ("/", "upload.csv"),
        ("a\x00b.csv", "upload.csv"),
        ("../../etc/evil.csv", "evil.csv"),
        ("loops.csv", "loops.csv"),
    ],
)
def test_upload_name_sanitizer(filename: str | None, expected: str) -> None:
    from api.main import _upload_name

    assert _upload_name(filename) == expected


# ---------------------------------------------------------------------------
# Fit options
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"n_bootstrap": MAX_N_BOOTSTRAP + 1},
        {"n_bootstrap": 10**12},
        {"n_bootstrap": -1},
        {"min_points": -5},
        {"de_maxiter": MAX_DE_MAXITER + 1},
        {"de_maxiter": 10**9},
        {"de_popsize": MAX_DE_POPSIZE + 1},
        {"holdout_frac": 1.0},
        {"trim_quantile": 0.0},
        {"congested_quantile": 1.0},
        {"q_max_percentile": 101.0},
        {"loader": "excel"},
        {"speed_unit": "furlongs"},
        {"min_duration_s": 0.0},
        {"seed": "not-an-int"},
    ],
)
def test_out_of_range_fit_options_are_422(client: TestClient, params: dict) -> None:
    r = _post_fd(
        client, files={"file": ("loops.csv", _CSV, "text/csv")}, data={"params": json.dumps(params)}
    )
    assert r.status_code == 422, r.text  # type: ignore[attr-defined]
    detail = r.json()["detail"]  # type: ignore[attr-defined]
    assert isinstance(detail, list)
    (key,) = params
    assert detail[0]["loc"][:2] == ["params", key]
    assert client.get("/api/v1/scenarios", headers=HEADERS).status_code == 200  # still up


@pytest.mark.parametrize("params", [{"n_procs": 64}, {"bootstrap": 5}, {"created_at": "now"}])
def test_unknown_fit_options_are_refused_not_ignored(client: TestClient, params: dict) -> None:
    r = _post_fd(
        client, files={"file": ("loops.csv", _CSV, "text/csv")}, data={"params": json.dumps(params)}
    )
    assert r.status_code == 422  # type: ignore[attr-defined]
    detail = r.json()["detail"]  # type: ignore[attr-defined]
    assert detail[0]["type"] == "extra_forbidden"
    assert detail[0]["loc"] == ["params", next(iter(params))]


def test_only_the_set_options_reach_the_job(client: TestClient) -> None:
    """Defaults stay the fit functions' own; an explicit null means unset."""
    params = {"n_bootstrap": 5, "seed": 1, "uncongested_max_density": None, "notes": "api"}
    r = _post_fd(
        client, files={"file": ("loops.csv", _CSV, "text/csv")}, data={"params": json.dumps(params)}
    )
    assert r.status_code == 202, r.text  # type: ignore[attr-defined]
    row = client.app.state.store.get_calibration(r.json()["calibration_id"])  # type: ignore[attr-defined]
    assert row["params"] == {"n_bootstrap": 5, "seed": 1, "notes": "api"}


def test_params_is_optional_and_may_be_empty(client: TestClient) -> None:
    for data in ({}, {"params": "{}"}):
        r = _post_fd(client, files={"file": ("loops.csv", _CSV, "text/csv")}, data=data)
        assert r.status_code == 202, r.text  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Chunked bodies (no Content-Length to check up front)
# ---------------------------------------------------------------------------

#: Bodies are sent as a generator so httpx picks chunked transfer encoding —
#: no ``Content-Length``, so the declared-length gate in the auth middleware
#: cannot fire and the streaming counter is what refuses the request.
_CHUNK = 65536

_JSON_HEADERS = {**HEADERS, "Content-Type": "application/json"}


def _padded_json(head: bytes, pad_bytes: int, tail: bytes) -> Iterator[bytes]:
    """``head`` + ``pad_bytes`` of filler + ``tail``, yielded in chunks."""
    yield head
    remaining = pad_bytes
    while remaining > 0:
        take = min(remaining, _CHUNK)
        yield b"x" * take
        remaining -= take
    yield tail


def _nothing_enqueued(client: TestClient) -> bool:
    store = client.app.state.store  # type: ignore[attr-defined]
    return all(store.list_by_status(kind, STATUSES) == [] for kind in ("run", "sweep", "report"))


@pytest.mark.parametrize(
    ("path", "head", "tail"),
    [
        ("/api/v1/runs", b'{"scenario_id": "nope", "overrides": {"name": "', b'"}}'),
        (
            "/api/v1/sweeps",
            b'{"scenario_id": "nope", "penetrations": [0.05], "compliances": [1.0],'
            b' "overrides": {"name": "',
            b'"}}',
        ),
        ("/api/v1/reports", b'{"run_ids": ["nope"], "title": "', b'"}'),
    ],
)
def test_oversized_chunked_json_body_is_413(
    small_caps_client: TestClient, path: str, head: bytes, tail: bytes
) -> None:
    """Every JSON route is capped, not just the one that streams its body.

    These three used to be read whole by Starlette before any handler ran:
    the declared-length gate is skipped (chunked), so nothing checked them.
    """
    r = small_caps_client.post(path, content=_padded_json(head, _MB, tail), headers=_JSON_HEADERS)
    assert r.status_code == 413, r.text
    assert "exceeds the limit" in r.text
    assert "FLOWSTATE_MAX_BODY_MB" in r.text
    assert _nothing_enqueued(small_caps_client)


def test_an_unauthenticated_oversized_body_is_401_not_413(small_caps_client: TestClient) -> None:
    """The cap is never a size oracle for a caller without a key.

    The counter sits outside the auth middleware and only runs when a handler
    downstream asks for the body, so an unauthenticated request is refused
    the same way whatever its size — declared length or chunked.
    """
    head = b'{"scenario_id": "nope", "overrides": {"name": "'
    no_key = {"Content-Type": "application/json"}
    chunked = small_caps_client.post(
        "/api/v1/runs", content=_padded_json(head, _MB, b'"}}'), headers=no_key
    )
    declared = small_caps_client.post("/api/v1/runs", content=b" " * (_MB + 1), headers=no_key)
    assert (chunked.status_code, declared.status_code) == (401, 401)
    assert _nothing_enqueued(small_caps_client)


def test_chunked_body_under_the_cap_reaches_the_handler(small_caps_client: TestClient) -> None:
    """The counter forwards a body it does not refuse: 202 and 404 as usual."""
    scenario = post_scenario(small_caps_client, macro_corridor_config())
    body = json.dumps({"scenario_id": scenario["scenario_id"]}).encode()
    r = small_caps_client.post(
        "/api/v1/runs", content=_padded_json(body, 0, b""), headers=_JSON_HEADERS
    )
    assert r.status_code == 202, r.text

    missing = json.dumps({"scenario_id": "run_does_not_exist"}).encode()
    r = small_caps_client.post(
        "/api/v1/runs", content=_padded_json(missing, 0, b""), headers=_JSON_HEADERS
    )
    assert r.status_code == 404, r.text


def test_oversized_chunked_upload_is_413_and_leaves_no_file(
    small_caps_client: TestClient,
) -> None:
    """A chunked multipart upload is capped at upload + body bytes (2 MB here)."""
    boundary = "flowstatechunkedboundary"

    def parts() -> Iterator[bytes]:
        yield (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="big.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n"
        ).encode()
        for _ in range(3 * _MB // _CHUNK):
            yield b"x" * _CHUNK
        yield f"\r\n--{boundary}--\r\n".encode()

    r = small_caps_client.post(
        "/api/v1/calibrations/fd",
        content=parts(),
        headers={**HEADERS, "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert r.status_code == 413, r.text
    assert "FLOWSTATE_MAX_UPLOAD_MB" in r.text
    uploads = Path(small_caps_client.app.state.settings.uploads_dir)  # type: ignore[attr-defined]
    assert not uploads.exists() or not any(uploads.iterdir())


def test_the_cap_is_a_running_total_not_a_per_chunk_check(
    small_caps_client: TestClient,
) -> None:
    """Five quarter-cap chunks: each is fine, the sum is not.

    Driven as a raw ASGI call because the test client joins a streamed body
    into one message, which would only prove the per-message check.
    """
    quarter = b"x" * (_MB // 4)
    messages: list[dict[str, Any]] = [
        {"type": "http.request", "body": quarter, "more_body": True} for _ in range(5)
    ]
    messages.append({"type": "http.request", "body": b"", "more_body": False})
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/v1/runs",
        "raw_path": b"/api/v1/runs",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"transfer-encoding", b"chunked"),
            (b"x-api-key", API_KEY.encode()),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "state": {},
    }
    asyncio.run(small_caps_client.app(scope, receive, send))  # type: ignore[arg-type,misc]

    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert [m["status"] for m in starts] == [413]
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert b"exceeds the limit" in body
    # Four quarter-cap chunks are exactly the 1 MB cap (inclusive) and the
    # fifth passes it; the terminating message is never read.
    assert len(messages) == 1
    assert _nothing_enqueued(small_caps_client)
