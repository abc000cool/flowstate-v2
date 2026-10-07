"""``scripts/merge_model_selfcheck.py grid``: which runs the grid makes (no SUMO run).

With ``--fleet-from`` a fixture and its ``_fleet`` twin become one config and
the grid runs it once; the dedupe key must hold the seed as well as the config
hash. The golden fixtures (``weave_golden``, ``weave_moderate``,
``merge_golden``) hard-code ``seed: 3`` in their config, so their hash is one at
every seed, while ``run_micro`` draws from the seed it is given: keyed on the
hash alone, ``--seeds 3-5`` ran seed 3 only and listed 4 and 5 as the same
config (review 2026-10-07, finding 0).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
FLEET_FROM = REPO_ROOT / "scenarios" / "mndot_i94_wb_stpaul_weave_dc.yaml"


def _load() -> ModuleType:
    name = "merge_model_selfcheck"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _grid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> dict[str, Any]:
    """``main(["grid", ...])`` with ``run_one`` stubbed: the rows name what would run."""
    mod = _load()

    def run_one(
        name: str, seed: int, model: str, work: Path, weave_set: Any = None, fleet_from: Any = None
    ) -> tuple[Any, dict[str, Any]]:
        return SimpleNamespace(run_dir=tmp_path / "none"), {"fixture": name, "seed": seed}

    monkeypatch.setattr(mod, "run_one", run_one)
    out = tmp_path / "grid.json"
    mod.main(
        [
            "grid",
            "--model",
            "weave",
            "--work-dir",
            str(tmp_path / "work"),
            "--out",
            str(out),
            *argv,
        ]
    )
    result: dict[str, Any] = json.loads(out.read_text())
    return result


def test_a_fixture_with_a_fixed_config_seed_runs_every_requested_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load()
    from flowstate_core.config import config_hash

    # the premise: the golden fixture's config hash does not move with the seed
    hashes = {
        config_hash(mod.fixture_config("weave_golden", s, "weave", {}, FLEET_FROM))
        for s in (3, 4, 5)
    }
    assert len(hashes) == 1
    res = _grid(
        tmp_path,
        monkeypatch,
        "--fleet-from",
        str(FLEET_FROM),
        "--seeds",
        "3-5",
        "--only",
        "weave_golden",
    )
    assert [(r["fixture"], r["seed"]) for r in res["rows"]] == [
        ("weave_golden", 3),
        ("weave_golden", 4),
        ("weave_golden", 5),
    ]
    assert res["skipped_same_config"] == []


def test_a_fixture_and_its_fleet_twin_still_run_once_per_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    res = _grid(
        tmp_path,
        monkeypatch,
        "--fleet-from",
        str(FLEET_FROM),
        "--seeds",
        "3-4",
        "--only",
        "ruth_entr,ruth_entr_fleet",
    )
    assert [(r["fixture"], r["seed"]) for r in res["rows"]] == [("ruth_entr", 3), ("ruth_entr", 4)]
    assert res["skipped_same_config"] == [
        {"fixture": "ruth_entr_fleet", "seed": 3, "same_as": "ruth_entr:3"},
        {"fixture": "ruth_entr_fleet", "seed": 4, "same_as": "ruth_entr:4"},
    ]
