"""Unit tests for the insertion guard of ``scripts/corridor_battery.py``.

The guard reads the first finished replicate's ``meta.json`` and decides
whether the remaining hour of simulation is worth spending. No SUMO here:
the metadata is written by hand, which is exactly the evidence the guard
sees.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from microsim.runner import RunPaths

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script() -> ModuleType:
    """Import ``scripts/corridor_battery.py`` by path (``scripts/`` is not a package)."""
    path = REPO_ROOT / "scripts" / "corridor_battery.py"
    spec = importlib.util.spec_from_file_location("_corridor_battery_guard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_paths(run_dir: Path, **meta: Any) -> RunPaths:
    """A replicate directory holding only the metadata the guard reads."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(json.dumps(meta))
    return RunPaths(
        run_dir=run_dir,
        trajectories=run_dir / "trajectories.parquet",
        edges=run_dir / "edges.parquet",
        meta=run_dir / "meta.json",
    )


class TestInsertionGuard:
    def test_a_healthy_replicate_does_not_abort(self, tmp_path: Path) -> None:
        cli = _load_script()
        guard = cli.InsertionGuard(0.8)
        paths = _run_paths(tmp_path / "s1", n_vehicles_planned=1000, n_vehicles_departed=950)
        assert guard(1, paths) is None
        assert [seed for seed, _ in guard.stats] == [1]

    def test_a_backlog_aborts_with_the_numbers(self, tmp_path: Path) -> None:
        cli = _load_script()
        guard = cli.InsertionGuard(0.8)
        paths = _run_paths(tmp_path / "s1", n_vehicles_planned=1000, n_vehicles_departed=400)
        reason = guard(1, paths)
        assert reason is not None
        assert "0.400" in reason and "0.800" in reason

    def test_an_empty_plan_aborts_when_a_threshold_is_set(self, tmp_path: Path) -> None:
        """Nothing planned is the most complete insertion failure there is.

        The departed fraction is NaN, which used to return early as if the
        threshold had been met — the pool then spent its hour simulating a
        corridor with no demand at all.
        """
        cli = _load_script()
        guard = cli.InsertionGuard(0.8)
        paths = _run_paths(tmp_path / "s1", n_vehicles_planned=0, n_vehicles_departed=0)
        reason = guard(1, paths)
        assert reason is not None
        assert "no vehicles were planned" in reason

    def test_without_a_threshold_an_empty_plan_only_prints(self, tmp_path: Path) -> None:
        cli = _load_script()
        guard = cli.InsertionGuard(0.0)
        paths = _run_paths(tmp_path / "s1", n_vehicles_planned=0, n_vehicles_departed=0)
        assert guard(1, paths) is None

    def test_only_the_first_replicate_is_tested(self, tmp_path: Path) -> None:
        cli = _load_script()
        guard = cli.InsertionGuard(0.8)
        first = _run_paths(tmp_path / "s1", n_vehicles_planned=10, n_vehicles_departed=10)
        assert guard(1, first) is None
        assert (
            guard(2, _run_paths(tmp_path / "s2", n_vehicles_planned=0, n_vehicles_departed=0))
            is None
        )

    def test_metadata_without_counters_is_refused(self, tmp_path: Path) -> None:
        cli = _load_script()
        guard = cli.InsertionGuard(0.8)
        with pytest.raises(ValueError, match="insertion counters"):
            guard(1, _run_paths(tmp_path / "s1", seed=1))
