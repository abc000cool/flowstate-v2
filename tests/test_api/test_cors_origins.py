"""CORS origins are configuration, not a hardcoded dev constant.

The documented remote-API path — serve the dashboard from one host, point it
at an API on another — is a *cross-origin* browser call. With the allow-list
frozen at the Vite dev server, every such call died in preflight and the
dashboard could only say "API offline"; the API key was never the problem.
``FLOWSTATE_CORS_ORIGINS`` opts an origin in (``api.settings``), and a
disallowed origin is answered without CORS headers rather than with a body
that describes the policy.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.settings import DEFAULT_CORS_ORIGINS, load_settings
from tests.test_api.conftest import API_KEY, HEADERS

ACAO = "access-control-allow-origin"
REMOTE = "http://192.168.1.178:5173"


@contextmanager
def _client_allowing(
    origins: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """A client over an app whose ``FLOWSTATE_CORS_ORIGINS`` is ``origins``."""
    monkeypatch.setenv("FLOWSTATE_QUEUE", "inline")
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("FLOWSTATE_API_KEY", API_KEY)
    monkeypatch.setenv("FLOWSTATE_CORS_ORIGINS", origins)
    from api.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def cors_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, str]]:
    """A client whose app allows exactly one non-default origin."""
    with _client_allowing(
        f" {REMOTE}/ , https://ops.example.gov ", tmp_path, monkeypatch
    ) as client:
        yield client, REMOTE


def test_env_origins_parse_trimmed_deduped_and_without_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FLOWSTATE_CORS_ORIGINS", "https://a.example/, , https://b.example ,https://a.example"
    )
    assert load_settings().cors_origins == ("https://a.example", "https://b.example")


def test_unset_and_blank_keep_the_loopback_dev_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOWSTATE_CORS_ORIGINS", raising=False)
    assert load_settings().cors_origins == DEFAULT_CORS_ORIGINS
    monkeypatch.setenv("FLOWSTATE_CORS_ORIGINS", "   ")
    assert load_settings().cors_origins == DEFAULT_CORS_ORIGINS


def test_dash_means_no_cross_origin_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same-origin deployments can switch CORS off entirely."""
    monkeypatch.setenv("FLOWSTATE_CORS_ORIGINS", "-")
    assert load_settings().cors_origins == ()


def test_configured_origin_passes_preflight_and_gets_acao(
    cors_client: tuple[TestClient, str],
) -> None:
    client, origin = cors_client
    pre = client.options(
        "/api/v1/scenarios",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-api-key,content-type",
        },
    )
    assert pre.status_code == 200, pre.text
    assert pre.headers[ACAO] == origin

    got = client.get("/api/v1/scenarios", headers={**HEADERS, "Origin": origin})
    assert got.status_code == 200
    assert got.headers[ACAO] == origin
    # The request id stays readable to a browser client (it is what a user quotes).
    assert "x-request-id" in got.headers["access-control-expose-headers"].lower()


def test_unconfigured_origin_gets_no_cors_headers(cors_client: tuple[TestClient, str]) -> None:
    """A disallowed origin is simply not granted CORS — no policy leaked in a body."""
    client, _ = cors_client
    other = "https://evil.example"
    got = client.get("/api/v1/scenarios", headers={**HEADERS, "Origin": other})
    assert got.status_code == 200  # the API key still authenticated the call
    assert ACAO not in got.headers
    assert other not in got.text


def test_unconfigured_origin_preflight_names_nothing(cors_client: tuple[TestClient, str]) -> None:
    """The preflight an unlisted origin sends is refused without naming the allow-list.

    Starlette answers it ``400 Disallowed CORS origin``; the browser blocks
    the call on the missing ``Access-Control-Allow-Origin`` either way. What
    matters is that the refusal grants no origin and does not enumerate the
    configured ones back to the caller.
    """
    client, allowed = cors_client
    pre = client.options(
        "/api/v1/scenarios",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-api-key,content-type",
        },
    )
    assert ACAO not in pre.headers
    assert allowed not in pre.text
    assert "ops.example.gov" not in pre.text


def test_star_allows_any_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``*`` is the documented throwaway-demo setting (docs/DEPLOYMENT.md §2)."""
    with _client_allowing("*", tmp_path, monkeypatch) as client:
        got = client.get(
            "/api/v1/scenarios", headers={**HEADERS, "Origin": "https://anywhere.example"}
        )
        assert got.status_code == 200
        assert got.headers[ACAO] == "*"


def test_default_origins_reach_the_app(client: TestClient) -> None:
    """The out-of-the-box dev experience is unchanged: Vite on loopback works."""
    for origin in DEFAULT_CORS_ORIGINS:
        got = client.get("/api/v1/scenarios", headers={**HEADERS, "Origin": origin})
        assert got.headers[ACAO] == origin
