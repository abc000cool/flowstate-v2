"""The deployed image must carry every file the shipped presets name.

14 of the 17 scenario presets reference ``artifacts/...`` calibrations and 12
of them also reference ``data/osm/...`` extracts. Both are resolved against
the repository root, which is ``/app`` in the image (uv installs the workspace
editable). The image used to copy neither, so a pilot could pick a preset from
the gallery, get 201 from ``POST /scenarios`` (path containment is a pure path
test — ``/app/artifacts/...`` is inside ``/app/artifacts`` whether or not it
exists) and 202 from ``POST /runs``, and then watch the run end ``failed``
with ``FileNotFoundError``. CI cannot catch it: the ``docker`` job is
tag-gated and only builds the image.

These tests read the Dockerfile and .dockerignore, so they hold without a
Docker daemon.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import _config_file_fields
from api.settings import REPO_ROOT
from flowstate_core.config import ScenarioConfig
from tests.test_api.conftest import HEADERS

DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"


def _copy_sources() -> list[str]:
    """Build-context paths the runtime stage copies into the image."""
    sources: list[str] = []
    for raw in DOCKERFILE.read_text().splitlines():
        line = raw.strip()
        if not line.startswith("COPY "):
            continue
        tokens = [t for t in line.split()[1:] if not t.startswith("--")]
        if "--from=" in line:
            continue  # the frontend build stage, not the build context
        sources.extend(tokens[:-1])  # the last token is the destination
    return sources


def _is_copied(rel_path: str, sources: list[str]) -> bool:
    return any(rel_path == src or rel_path.startswith(src.rstrip("/") + "/") for src in sources)


def _ignored(rel_path: str) -> bool:
    """Whether .dockerignore keeps ``rel_path`` out of the build context.

    Docker applies every pattern and the last match wins, so a later ``!``
    line re-includes what an earlier line excluded.
    """
    from fnmatch import fnmatch

    ignored = False
    for raw in DOCKERIGNORE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line.lstrip("!").rstrip("/")
        if fnmatch(rel_path, pattern) or fnmatch(rel_path, pattern + "/*"):
            ignored = not negate
    return ignored


def test_every_preset_file_reference_is_in_the_image(client: TestClient) -> None:
    presets = client.get("/api/v1/scenarios/preset", headers=HEADERS).json()
    assert presets
    sources = _copy_sources()
    referenced: set[str] = set()
    for preset in presets:
        cfg = ScenarioConfig.model_validate(preset["config"])
        for _, value in _config_file_fields(cfg):
            assert not Path(value).is_absolute(), (preset["filename"], value)
            referenced.add(value)

    assert referenced, "no preset names a file; this test would prove nothing"
    for rel_path in sorted(referenced):
        assert (REPO_ROOT / rel_path).is_file(), f"{rel_path} is missing from the repo"
        assert _is_copied(rel_path, sources), f"Dockerfile never COPYs {rel_path}"
        assert not _ignored(rel_path), f".dockerignore strips {rel_path} from the build context"


@pytest.mark.parametrize("directory", ["artifacts", "data/osm", "scenarios"])
def test_runtime_data_directories_reach_the_build_context(directory: str) -> None:
    assert _is_copied(f"{directory}/probe", _copy_sources()), f"Dockerfile never COPYs {directory}/"
    assert not _ignored(f"{directory}/probe")


def test_local_datasets_stay_out_of_the_build_context() -> None:
    """Only ``data/osm`` is re-included; PeMS/I-24 payloads stay local."""
    for rel_path in ("data/i24motion/big.csv", "data/processed/x.parquet", "data/ngsim/y.csv"):
        assert _ignored(rel_path), f"{rel_path} would be shipped in the image"


def test_image_stays_multi_stage_and_non_root() -> None:
    """The copies must not have cost the image its build stages or its user."""
    text = DOCKERFILE.read_text()
    assert "AS frontend" in text and "AS runtime" in text
    assert "useradd" in text and "USER flowstate" in text
    # Ownership is set after every COPY, so the added trees are readable by
    # the non-root user too.
    assert text.index("COPY artifacts/") < text.index("chown -R flowstate:flowstate")
    assert text.index("COPY data/osm/") < text.index("chown -R flowstate:flowstate")
