"""Scenario endpoints: validation, YAML/JSON bodies, listing, presets."""

from __future__ import annotations

import yaml
from fastapi.testclient import TestClient

from flowstate_core.config import ScenarioConfig, config_hash
from tests.test_api.conftest import HEADERS, macro_corridor_config, post_scenario


def test_post_json_scenario_returns_hash(client: TestClient) -> None:
    cfg = macro_corridor_config()
    body = post_scenario(client, cfg)
    assert body["scenario_id"].startswith("scn_")
    assert body["name"] == "api_macro_corridor"
    # The reported hash is the hash of the validated config (contract §2).
    expected = config_hash(ScenarioConfig.model_validate(cfg))
    assert body["config_hash"] == expected
    assert body["config"]["tier"] == "macro"


def test_post_yaml_scenario(client: TestClient) -> None:
    text = yaml.safe_dump(macro_corridor_config(name="yaml_variant"))
    r = client.post(
        "/api/v1/scenarios",
        content=text,
        headers={**HEADERS, "Content-Type": "application/x-yaml"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "yaml_variant"


def test_post_invalid_config_is_422(client: TestClient) -> None:
    bad = macro_corridor_config()
    bad["av"] = {"penetration": 0.9}  # outside [0, 0.3] (contract §2)
    r = client.post("/api/v1/scenarios", json=bad, headers=HEADERS)
    assert r.status_code == 422
    assert "penetration" in str(r.json()["detail"])


def test_post_non_mapping_body_is_422(client: TestClient) -> None:
    r = client.post(
        "/api/v1/scenarios",
        content="just a string",
        headers={**HEADERS, "Content-Type": "text/plain"},
    )
    assert r.status_code == 422


def test_post_unparseable_body_is_422(client: TestClient) -> None:
    r = client.post(
        "/api/v1/scenarios",
        content="{unclosed: [",
        headers={**HEADERS, "Content-Type": "application/x-yaml"},
    )
    assert r.status_code == 422


def test_list_and_get_scenarios(client: TestClient) -> None:
    a = post_scenario(client, macro_corridor_config(name="one"))
    post_scenario(client, macro_corridor_config(name="two", seed=8))
    listing = client.get("/api/v1/scenarios", headers=HEADERS).json()
    assert [s["name"] for s in listing] == ["one", "two"]
    got = client.get(f"/api/v1/scenarios/{a['scenario_id']}", headers=HEADERS)
    assert got.status_code == 200
    assert got.json()["config_hash"] == a["config_hash"]
    assert client.get("/api/v1/scenarios/scn_missing", headers=HEADERS).status_code == 404


def test_presets_list_repo_scenarios(client: TestClient) -> None:
    r = client.get("/api/v1/scenarios/preset", headers=HEADERS)
    assert r.status_code == 200
    presets = {p["name"]: p for p in r.json()}
    assert "ring_sugiyama" in presets
    assert "corridor_10km" in presets
    ring = presets["ring_sugiyama"]
    assert ring["filename"] == "ring_sugiyama.yaml"
    assert ring["config"]["network"]["kind"] == "ring"
    assert len(ring["config_hash"]) == 12
