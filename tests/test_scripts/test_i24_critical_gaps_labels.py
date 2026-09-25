"""The critical-gap script labels an observed run's weave time gaps a proposal and a simulated
run's a self-check (2026-09-25: a loop variable named ``kind`` shadowed the run's ``kind``, so the
first I-24 artifact carried the self-check labels on its proposal; the numbers were unaffected)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "flowstate_i24_critical_gaps", SCRIPTS / "i24_critical_gaps.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cg = _load()


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> Any:
    rows = [
        {"zone": "Z_weave", "zone_kind": "weave", "movement": "entering"},
        {"zone": "Z_weave", "zone_kind": "weave", "movement": "exiting"},
        {"zone": "Z_merge", "zone_kind": "merge", "movement": "entering"},
    ]
    monkeypatch.setattr(cg, "select_drivers", lambda drivers, **kw: drivers)
    monkeypatch.setattr(cg, "fit_groups", lambda sel, points, **kw: (rows, [None] * len(rows)))

    def mapping(
        group_rows: Any, boots: Any, acceptance: Any, *, movement: str, estimator: str
    ) -> dict[str, Any]:
        param = "accept_gap_s" if movement == "entering" else "exit_accept_gap_s"
        return {
            "parameter": param,
            "current": 0.6,
            "accept_s": {"value": 0.1},
            "accept_s_lead": {"value": 0.0},
            "accept_s_lag": {"value": 0.7},
        }

    monkeypatch.setattr(cg, "acceptance_mapping", mapping)
    pool = cg.Pool()
    pool.drivers = [pd.DataFrame({"x": [1.0]})]
    pool.points = [pd.DataFrame({"x": [1.0]})]
    pool.drivers_short = [pd.DataFrame({"x": [1.0]})]
    pool.points_short = [pd.DataFrame({"x": [1.0]})]
    return pool


def test_an_observed_run_writes_a_proposal(stubbed: Any) -> None:
    out = cg.fits_and_mapping(stubbed, acceptance=None, n_boot=0, seed=1, kind="observed")
    assert "proposal" in out and "self_check" not in out
    block = out["proposal"]
    assert set(block) == {"accept_gap_s", "exit_accept_gap_s", "status"}
    assert block["status"].startswith("proposal only")
    assert "proposed" in block["accept_gap_s"] and "implied" not in block["accept_gap_s"]


def test_a_simulated_run_writes_a_self_check(stubbed: Any) -> None:
    out = cg.fits_and_mapping(stubbed, acceptance=None, n_boot=0, seed=1, kind="simulated")
    assert "self_check" in out and "proposal" not in out
    block = out["self_check"]
    assert block["status"].startswith("self-check")
    assert "implied" in block["exit_accept_gap_s"] and "proposed" not in block["exit_accept_gap_s"]
