"""``GET /scenarios/preset`` parses each preset file once per content.

Parsing every ``scenarios/*.yaml`` took about a second per request, and
several dashboard views ask at once, so the listing keeps each file's parse
keyed on the sha256 of its bytes (``api.main.load_presets``). Pinned here:
the response is what a fresh parse gives; a second request parses nothing; an
edited file — even one whose mtime was put back — is parsed again, and only
that file; added and removed files show on the next request; a broken file
is skipped and not re-parsed while it stays broken.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from flowstate_core.config import ScenarioConfig, config_hash
from tests.test_api.conftest import API_KEY, HEADERS, data_dir

REPO_SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


@pytest.fixture()
def scenarios_dir(tmp_path: Path) -> Path:
    """A scenarios directory holding copies of two shipped presets."""
    path = tmp_path / "scenarios"
    path.mkdir()
    for name in ("corridor_10km.yaml", "ring_sugiyama.yaml"):
        shutil.copy2(REPO_SCENARIOS / name, path / name)
    return path


@pytest.fixture()
def parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The file names ``load_presets`` parses, in order."""
    import api.main as main

    seen: list[str] = []
    real: Callable[[str, bytes], Any] = main._parse_preset

    def counting(filename: str, raw: bytes) -> Any:
        seen.append(filename)
        return real(filename, raw)

    monkeypatch.setattr(main, "_parse_preset", counting)
    return seen


@pytest.fixture()
def client(
    tmp_path: Path, scenarios_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """The conftest client, serving presets from ``scenarios_dir``."""
    monkeypatch.setenv("FLOWSTATE_QUEUE", "inline")
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(data_dir(tmp_path)))
    monkeypatch.setenv("FLOWSTATE_API_KEY", API_KEY)
    monkeypatch.setenv("FLOWSTATE_SCENARIOS_DIR", str(scenarios_dir))
    from api.main import create_app

    with TestClient(create_app()) as c:
        yield c


def _presets(client: TestClient) -> list[dict[str, Any]]:
    r = client.get("/api/v1/scenarios/preset", headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _fresh(path: Path) -> dict[str, Any]:
    """What the listing said for one file before the cache: a direct parse."""
    cfg = ScenarioConfig.from_yaml(path)
    return {
        "name": cfg.name,
        "filename": path.name,
        "config_hash": config_hash(cfg),
        "config": cfg.model_dump(mode="json"),
        "preset": True,
    }


def test_the_response_is_a_fresh_parse_and_a_repeat_parses_nothing(
    client: TestClient, scenarios_dir: Path, parses: list[str]
) -> None:
    first = _presets(client)
    expected = [_fresh(p) for p in sorted(scenarios_dir.glob("*.yaml"))]
    assert first == expected
    assert parses == ["corridor_10km.yaml", "ring_sugiyama.yaml"]

    assert _presets(client) == first
    assert parses == ["corridor_10km.yaml", "ring_sugiyama.yaml"]  # nothing new


def test_an_edited_preset_is_parsed_again_even_with_its_mtime_restored(
    client: TestClient, scenarios_dir: Path, parses: list[str]
) -> None:
    before = {p["filename"]: p for p in _presets(client)}
    ring = scenarios_dir / "ring_sugiyama.yaml"
    stat = ring.stat()
    doc = yaml.safe_load(ring.read_text())
    doc["seed"] = int(doc.get("seed", 42)) + 1
    ring.write_text(yaml.safe_dump(doc, sort_keys=False))
    # a copy that keeps mtimes (cp -p, rsync -a) must not serve the old parse
    os.utime(ring, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    parses.clear()

    after = {p["filename"]: p for p in _presets(client)}
    assert parses == ["ring_sugiyama.yaml"]  # only the changed file
    assert after["ring_sugiyama.yaml"]["config"]["seed"] == doc["seed"]
    assert after["ring_sugiyama.yaml"]["config_hash"] != before["ring_sugiyama.yaml"]["config_hash"]
    assert after["ring_sugiyama.yaml"] == _fresh(ring)
    assert after["corridor_10km.yaml"] == before["corridor_10km.yaml"]


def test_added_and_removed_presets_show_on_the_next_request(
    client: TestClient, scenarios_dir: Path, parses: list[str]
) -> None:
    assert [p["filename"] for p in _presets(client)] == [
        "corridor_10km.yaml",
        "ring_sugiyama.yaml",
    ]
    doc = yaml.safe_load((scenarios_dir / "ring_sugiyama.yaml").read_text())
    doc["name"] = "ring_copy"
    (scenarios_dir / "ring_copy.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    parses.clear()
    listed = _presets(client)
    assert [p["filename"] for p in listed] == [
        "corridor_10km.yaml",
        "ring_copy.yaml",
        "ring_sugiyama.yaml",
    ]
    assert parses == ["ring_copy.yaml"]

    (scenarios_dir / "corridor_10km.yaml").unlink()
    assert [p["filename"] for p in _presets(client)] == ["ring_copy.yaml", "ring_sugiyama.yaml"]


def test_a_broken_preset_is_skipped_and_not_parsed_again_until_it_changes(
    client: TestClient, scenarios_dir: Path, parses: list[str]
) -> None:
    broken = scenarios_dir / "broken.yaml"
    broken.write_text("{unclosed: [")
    not_a_mapping = scenarios_dir / "list.yaml"
    not_a_mapping.write_text("- just\n- a list\n")
    invalid = scenarios_dir / "invalid.yaml"
    invalid.write_text(yaml.safe_dump({"name": "bad", "av": {"penetration": 0.9}}))

    names = [p["filename"] for p in _presets(client)]
    assert names == ["corridor_10km.yaml", "ring_sugiyama.yaml"]
    assert sorted(parses) == sorted(
        ["broken.yaml", "corridor_10km.yaml", "invalid.yaml", "list.yaml", "ring_sugiyama.yaml"]
    )
    parses.clear()
    assert [p["filename"] for p in _presets(client)] == names
    assert parses == []

    # fixed: listed on the next request
    doc = yaml.safe_load((scenarios_dir / "ring_sugiyama.yaml").read_text())
    doc["name"] = "fixed"
    broken.write_text(yaml.safe_dump(doc, sort_keys=False))
    assert "broken.yaml" in [p["filename"] for p in _presets(client)]
    assert parses == ["broken.yaml"]
