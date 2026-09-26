"""Tests for ``scripts/collision_census.py`` (WP-95).

No simulation runs here: a sweep tree ``<root>/<cell>/<config hash>/<seed>/``
is faked on disk with hand-written ``meta.json`` files, and the census is
checked against hand counts: the colliders' and victims' roles, the distinct
pairs net of the runner's repeats, the departed AVs, the handback counters,
the off-corridor and close-leader counters (WP-96), and a cell whose runs
predate the counter (not recorded, never 0).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "collision_census.py"


def _load_script() -> ModuleType:
    """Import ``scripts/collision_census.py`` by path (``scripts/`` is not a package)."""
    spec = importlib.util.spec_from_file_location("flowstate_collision_census", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


census = _load_script()


def _event(t: float, collider: str, victim: str, lane: str = "e_1") -> dict[str, Any]:
    return {
        "t": t,
        "collider": collider,
        "victim": victim,
        "type": "collision",
        "lane": lane,
        "pos_m": 10.0,
    }


def _meta(seed: int, events: list[dict[str, Any]] | None, **extra: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "config_hash": "abc",
        "seed": seed,
        "n_vehicles_departed": 10,
        "av_ids": ["a1", "a2", "a9"],
        "complied_ids": ["a1", "a2"],
        # a9 never departed: no fuel total
        "fuel_ml_per_vehicle": {v: 1.0 for v in ("a1", "a2", "h1", "h2", "h3")},
        **extra,
    }
    if events is not None:
        meta["n_collisions"] = len(events)
        meta["collisions"] = events
    return meta


def _write(root: Path, cell: str, meta: dict[str, Any]) -> None:
    d = root / cell / str(meta["config_hash"]) / str(meta["seed"])
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps(meta))


def test_census_counts_roles_pairs_and_handback(tmp_path: Path) -> None:
    root = tmp_path / "sweep"
    # seed 1: a1 hits h1; one step later a2 hits h2 while a1-h1 still overlap,
    # so the runner logs a1-h1 again; a human hits a2
    _write(
        root,
        "fs",
        _meta(
            1,
            [
                _event(5.0, "a1", "h1"),
                _event(9.0, "a2", "h2"),
                _event(9.0, "a1", "h1"),
                _event(12.0, "h3", "a2", lane="e_0"),
            ],
            av_emergency_handback={"n_vehicle_steps": 4, "n_withdrawals": 3, "n_vehicles": 2},
            av_off_corridor={
                "release": False,
                "n_vehicles": 2,
                "n_vehicle_steps": 70,
                "n_released": 0,
            },
            av_close_leader={"observed": False, "n_vehicle_steps": 5, "n_vehicles": 1},
        ),
    )
    # seed 2: a compliant AV hits another AV; a non-compliant AV hits a human
    _write(
        root,
        "fs",
        _meta(
            2,
            [_event(3.0, "a1", "a2"), _event(4.0, "a9", "h1")],
            av_emergency_handback={"n_vehicle_steps": 1, "n_withdrawals": 1, "n_vehicles": 1},
            av_off_corridor={
                "release": True,
                "n_vehicles": 3,
                "n_vehicle_steps": 0,
                "n_released": 3,
            },
            av_close_leader={"observed": True, "n_vehicle_steps": 2, "n_vehicles": 2},
        ),
    )
    _write(root, "baseline", _meta(1, []))
    _write(root, "old", _meta(1, None))  # written before the counter existed

    out = census.census(root)
    assert list(out["cells"]) == ["baseline", "fs", "old"]
    fs = out["cells"]["fs"]
    assert fs["n_runs"] == 2 and fs["config_hashes"] == ["abc"]
    assert fs["summary"]["total"] == 6 and fs["summary"]["n_runs_with_collisions"] == 2
    assert fs["summary"]["rate"]["value"] == pytest.approx(1000 * 6 / 20)
    assert fs["n_distinct_pairs"] == 3 + 2
    assert fs["colliders"] == {"compliant_av": 4, "noncompliant_av": 1, "human": 1}
    assert fs["victims_of_compliant_av"] == {"av": 1, "human": 3}
    assert (fs["n_departed"], fs["n_departed_av"]) == (20, 4)
    assert fs["per_1000_departed_av"] == pytest.approx(1000 * 6 / 4)
    assert fs["handback"] == {
        "n_runs": 2,
        "n_vehicle_steps": 5,
        "n_withdrawals": 4,
        "n_vehicles": 3,
    }
    assert fs["off_corridor"] == {
        "n_runs": 2,
        "n_runs_release": 1,
        "n_vehicles": 5,
        "n_vehicle_steps": 70,
        "n_released": 3,
    }
    assert fs["close_leader"] == {
        "n_runs": 2,
        "n_runs_observed": 1,
        "n_vehicle_steps": 7,
        "n_vehicles": 3,
    }

    base = out["cells"]["baseline"]
    assert base["summary"]["total"] == 0 and base["n_distinct_pairs"] == 0
    assert base["handback"] is None
    assert base["off_corridor"] is None and base["close_leader"] is None
    # not recorded is not zero
    old = out["cells"]["old"]
    assert old["summary"] is None and old["per_1000_departed_av"] is None


def test_main_writes_strict_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "sweep"
    _write(root, "fs", _meta(1, [_event(5.0, "a1", "h1")]))
    _write(root, "none", _meta(1, []))
    _write(root, "old", _meta(1, None))
    target = tmp_path / "out" / "census.json"
    assert census.main(["--root", str(root), "--out", str(target)]) == 0
    written = json.loads(target.read_text())
    assert written["kind"] == "collision_census" and written["schema_version"] == 1
    assert written["cells"]["fs"]["summary"]["total"] == 1
    assert "fs: 1 runs, collisions 1 in 1" in capsys.readouterr().out
