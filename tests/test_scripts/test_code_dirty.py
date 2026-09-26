"""``code_dirty`` in the lane-change drivers counts code paths only (VM AE, 2026-09-25).

A pipeline VM rewrites tracked artifacts stage by stage. Before this fix a
later stage read the earlier stage's rewritten artifact as uncommitted code and
recorded ``code_dirty: true`` on a clean snapshot.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        f"flowstate_code_dirty_{name}", SCRIPTS / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for rel in ("artifacts/a.json", "scripts/s.py", "packages/p.py"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("0\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return tmp_path


@pytest.mark.parametrize("script", ["lane_change_relaxation", "i24_critical_gaps"])
def test_a_rewritten_artifact_is_not_dirty_code(repo: Path, script: str, monkeypatch) -> None:
    module = _load(script)
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    assert module.git_dirty() is False
    (repo / "artifacts/a.json").write_text("1\n")
    assert module.git_dirty() is False
    (repo / "scripts/s.py").write_text("1\n")
    assert module.git_dirty() is True


@pytest.mark.parametrize("script", ["lane_change_relaxation", "i24_critical_gaps"])
def test_a_changed_package_is_dirty_code(repo: Path, script: str, monkeypatch) -> None:
    module = _load(script)
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    (repo / "packages/p.py").write_text("1\n")
    assert module.git_dirty() is True
