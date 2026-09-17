"""Request-size caps on sweeps and runs (API/deployment hardening).

A sweep grid is a cartesian product and a run is a replicate loop: both are
one cheap request that expands into an unbounded amount of validation and
simulation work. These tests pin the ceilings and, where it matters, pin
*when* they fire — the grid-size check has to reject before any cell config is
built, not after 120,000 of them have been validated.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from api.schemas import MAX_REPLICATES, MAX_SWEEP_AXIS_VALUES, MAX_SWEEP_CELLS
from flowstate_core.config import MAX_REPLICATES as CONFIG_MAX_REPLICATES
from tests.test_api.conftest import HEADERS, macro_corridor_config, post_scenario


def test_cap_constants_match_the_documented_values() -> None:
    """The caps quoted in the endpoint docstrings and README are these."""
    assert (MAX_SWEEP_AXIS_VALUES, MAX_SWEEP_CELLS, MAX_REPLICATES) == (50, 200, 200)
    assert CONFIG_MAX_REPLICATES == 500


def test_caps_are_discoverable_in_openapi(client: TestClient) -> None:
    """/docs must state the limits — they are part of the endpoint contract."""
    spec = client.get("/openapi.json").json()
    sweep_doc = spec["paths"]["/api/v1/sweeps"]["post"]["description"]
    assert str(MAX_SWEEP_AXIS_VALUES) in sweep_doc
    assert str(MAX_SWEEP_CELLS) in sweep_doc
    run_doc = spec["paths"]["/api/v1/runs"]["post"]["description"]
    assert str(MAX_REPLICATES) in run_doc
    assert str(CONFIG_MAX_REPLICATES) in run_doc


# ---------------------------------------------------------------------------
# Sweep grid size
# ---------------------------------------------------------------------------


def test_sweep_axis_longer_than_the_cap_is_422(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": scenario["scenario_id"],
            "penetrations": [0.01] * (MAX_SWEEP_AXIS_VALUES + 1),
            "compliances": [1.0],
        },
        headers=HEADERS,
    )
    assert r.status_code == 422
    assert "penetrations" in str(r.json()["detail"])


def test_sweep_over_the_cell_ceiling_is_422(client: TestClient) -> None:
    """20 × 10 × 2 = 400 cells, every axis individually legal."""
    scenario = post_scenario(client, macro_corridor_config())
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": scenario["scenario_id"],
            "penetrations": [0.001 * i for i in range(1, 21)],
            "compliances": [0.05 * i for i in range(1, 11)],
            "controllers": ["follower_stopper", None],
            "replicates": 1,
        },
        headers=HEADERS,
    )
    assert r.status_code == 422
    detail = str(r.json()["detail"])
    assert "400 cells" in detail
    assert str(MAX_SWEEP_CELLS) in detail


def test_reported_120000_cell_request_is_rejected_before_any_cell_is_built(
    client: TestClient,
) -> None:
    """The reproduced 200 × 200 × 3 = 120,000-cell request.

    Both size gates run in schema validation, so the request must die before
    the endpoint body executes at all: the unknown ``scenario_id`` would 404
    from the handler, and the out-of-range compliances would surface as a
    per-cell ``{"cell": ..., "errors": ...}`` detail once cells were built.
    A prompt axis-cap 422 in the pydantic error shape proves neither happened.
    """
    started = time.monotonic()
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": "scn_does_not_exist",
            "penetrations": [0.001 * i for i in range(200)],
            "compliances": [9.0] * 200,  # invalid values; never validated
            "controllers": ["follower_stopper", "pi_saturation", None],
        },
        headers=HEADERS,
    )
    elapsed = time.monotonic() - started
    assert r.status_code == 422  # not 404 from the handler
    assert elapsed < 5.0, f"oversized grid took {elapsed:.1f}s — cells were materialized"
    detail = r.json()["detail"]
    assert isinstance(detail, list)  # pydantic errors, not the per-cell dict
    assert {tuple(e["loc"]) for e in detail} == {
        ("body", "penetrations"),
        ("body", "compliances"),
    }
    assert all(e["type"] == "too_long" for e in detail)


def test_sweep_at_the_cell_ceiling_is_accepted(client: TestClient) -> None:
    """200 cells exactly — the boundary is inclusive (validated, not run)."""
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": "scn_does_not_exist",
            "penetrations": [0.001 * i for i in range(20)],
            "compliances": [0.1 * i for i in range(1, 11)],
        },
        headers=HEADERS,
    )
    # Past the size gate; the handler's own scenario lookup is what fails.
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Replicates
# ---------------------------------------------------------------------------


def test_run_replicates_over_the_cap_is_422(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    for replicates in (MAX_REPLICATES + 1, 10_000_000):
        r = client.post(
            "/api/v1/runs",
            json={"scenario_id": scenario["scenario_id"], "replicates": replicates},
            headers=HEADERS,
        )
        assert r.status_code == 422, replicates
        assert "replicates" in str(r.json()["detail"])


def test_run_replicates_at_the_cap_passes_validation(client: TestClient) -> None:
    """200 replicates is accepted by the schema (404 comes from the handler)."""
    r = client.post(
        "/api/v1/runs",
        json={"scenario_id": "scn_does_not_exist", "replicates": MAX_REPLICATES},
        headers=HEADERS,
    )
    assert r.status_code == 404


def test_sweep_replicates_over_the_cap_is_422(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config())
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": scenario["scenario_id"],
            "penetrations": [0.05],
            "compliances": [1.0],
            "replicates": 10_000_000,
        },
        headers=HEADERS,
    )
    assert r.status_code == 422
    assert "replicates" in str(r.json()["detail"])


def test_scenario_config_replicates_are_bounded(client: TestClient) -> None:
    """The stored config carries its own ceiling, so overrides cannot dodge it."""
    over = macro_corridor_config(replicates=CONFIG_MAX_REPLICATES + 1)
    r = client.post("/api/v1/scenarios", json=over, headers=HEADERS)
    assert r.status_code == 422
    assert "replicates" in str(r.json()["detail"])

    at_cap = macro_corridor_config(replicates=CONFIG_MAX_REPLICATES)
    assert client.post("/api/v1/scenarios", json=at_cap, headers=HEADERS).status_code == 201


def test_run_overrides_cannot_exceed_the_config_ceiling(client: TestClient) -> None:
    """``overrides`` is re-validated against ScenarioConfig, ceiling included."""
    scenario = post_scenario(client, macro_corridor_config())
    r = client.post(
        "/api/v1/runs",
        json={
            "scenario_id": scenario["scenario_id"],
            "overrides": {"replicates": CONFIG_MAX_REPLICATES + 1},
        },
        headers=HEADERS,
    )
    assert r.status_code == 422
    assert "replicates" in str(r.json()["detail"])


# ---------------------------------------------------------------------------
# OpenAPI contract: auth and the status codes the middlewares produce
# ---------------------------------------------------------------------------


def test_openapi_declares_the_api_key_security_scheme(client: TestClient) -> None:
    """Without the scheme /docs has no Authorize button and no way to try a route.

    Auth is enforced by an ASGI middleware, which FastAPI cannot see, so the
    scheme is declared explicitly at the include point. Swagger UI builds its
    Authorize dialog from ``components.securitySchemes`` alone, and a client
    generated from the spec emits the header only when the spec names it.
    """
    spec = client.get("/openapi.json").json()
    assert spec["components"]["securitySchemes"]["ApiKeyAuth"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }
    api_ops = [
        (path, method, op)
        for path, item in spec["paths"].items()
        if path.startswith("/api/")
        for method, op in item.items()
    ]
    assert api_ops
    for path, method, op in api_ops:
        assert op.get("security") == [{"ApiKeyAuth": []}], f"{method} {path} declares no security"
    # /healthz is exempt in the middleware, so it must not claim otherwise.
    assert "security" not in spec["paths"]["/healthz"]["get"]


def test_openapi_documents_the_status_codes_the_middlewares_produce(client: TestClient) -> None:
    """401/413 on every /api route, 404 where an id is addressed, 503 on health."""
    spec = client.get("/openapi.json").json()
    for path, item in spec["paths"].items():
        if not path.startswith("/api/"):
            continue
        for method, op in item.items():
            codes = set(op["responses"])
            assert {"401", "413"} <= codes, f"{method} {path}: {sorted(codes)}"
            # POST /calibrations/{kind} is the one templated path whose
            # parameter is a Literal discriminator, not a record id: an
            # unknown kind is a 422, never a 404.
            if "{" in path and path != "/api/v1/calibrations/{kind}":
                assert "404" in codes, f"{method} {path} addresses an id but documents no 404"
    assert "503" in spec["paths"]["/healthz"]["get"]["responses"]


def test_declaring_the_scheme_did_not_change_the_auth_response(client: TestClient) -> None:
    """``auto_error=False``: the middleware stays the only enforcement point.

    With FastAPI's default the dependency would answer 403 "Not
    authenticated" before the middleware ran, replacing the documented 401.
    """
    r = client.get("/api/v1/scenarios")
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid or missing X-API-Key"
    assert client.get("/api/v1/scenarios", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/v1/scenarios", headers=HEADERS).status_code == 200
