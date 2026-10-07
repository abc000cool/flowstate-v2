"""scripts/i94_netfix_probe.py: the 6th Street left-exit probe's plan, analysis and guards.

No corridor runs here (the laptop rule): the plan is checked against the
committed grid (its config hashes and seeds), and the analysis runs on a faked
run tree whose readings are the committed grid readings
(``artifacts/p3_driver_grid_2026-10-07/grid_i94``) — so the as-built scores must
reproduce the grid artifact's numbers (8.24 / 8.40 pp, S97 3,321 / 3,764
veh/h) and docs/I94_LANE_SHARES.md §1's S791-corrected 5.87 / 5.76 pp. The
fixed network's lane table is compiled with netconvert (the I-94 extract, about
a second; no simulation).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from flowstate_core.rng import spawn_seeds

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
GRID = REPO_ROOT / "artifacts" / "p3_driver_grid_2026-10-07" / "grid_i94"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


probe = _load("i94_netfix_probe")
g = probe.g

needs_grid = pytest.mark.skipif(not (GRID / "MANIFEST.json").is_file(), reason="grid tree absent")


@needs_grid
def test_the_plan_is_the_grids_pairs_on_two_networks() -> None:
    plan = probe.build_probe()
    manifest = json.loads((GRID / "MANIFEST.json").read_text())
    grid_hashes = {p["name"]: p["config_hash"] for p in manifest["pairs"]}
    assert plan.n_runs == 16 and plan.names == ["k0.0_kr0.0", "k1.0_kr0.1"]
    # as built, the probe's runs ARE the grid's configurations, and its first two seeds the grid's
    assert plan.hashes["as_built"] == {n: grid_hashes[n] for n in plan.names}
    assert plan.hashes["as_built"]["k0.0_kr0.0"] == manifest["reference_config_hash"]
    assert plan.seeds == spawn_seeds(42, 4) and plan.seeds[:2] == manifest["seeds"]
    # the fixed network differs in its name and the --ramps.unset list only
    assert set(plan.hashes["netfix"].values()).isdisjoint(plan.hashes["as_built"].values())
    assert probe._ramps_unset(plan.references["netfix"]) == "1001426896,45782590"
    assert probe._ramps_unset(plan.references["as_built"]) == "1001426896"
    for name in plan.names:
        a, b = plan.configs["as_built"][name], plan.configs["netfix"][name]
        assert probe._strip_network_fix(a) == probe._strip_network_fix(b)
        assert b["name"] == "mndot_i94_wb_stpaul_weave_slice_netfix_xlsfg"


def test_networks_that_differ_elsewhere_are_refused(tmp_path: Path) -> None:
    raw = yaml.safe_load((REPO_ROOT / probe.NETWORKS["netfix"]).read_text())
    raw["sim"]["warmup_s"] = 600.0
    other = tmp_path / "other.yaml"
    other.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="differ only in their name and netconvert_extra"):
        probe.build_probe({"as_built": probe.NETWORKS["as_built"], "netfix": str(other)})


def test_plan_only_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = probe.main(
        ["--plan-only", "--out", str(tmp_path / "runs"), "--artifact", str(tmp_path / "a.json")]
    )
    out = capsys.readouterr().out
    assert code == 0 and "16 runs" in out and "1001426896,45782590" in out
    assert "nothing written" in out and list(tmp_path.iterdir()) == []


def test_a_small_machine_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(g, "memory_gb", lambda: (16.0, 10.0))
    code = probe.main(["--out", str(tmp_path / "runs"), "--artifact", str(tmp_path / "a.json")])
    assert code == 2 and "p5_i94_netfix_probe" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def _fake_tree(plan: Any, root: Path) -> None:
    """Every run's readings = the committed grid readings of its pair (seeds 3-4 repeat 1-2)."""
    (root / "as_built").mkdir(parents=True)
    (root / "as_built" / "LANES.json").write_text((GRID / "LANES.json").read_text())
    probe.lanes_for(plan, root)  # the fixed network's table: netconvert only
    for net in plan.specs:
        for name in plan.names:
            src_hash = plan.hashes["as_built"][name]
            for i, seed in enumerate(plan.seeds):
                src = GRID / name / src_hash / str(plan.seeds[i % 2]) / "readings.json"
                rd = json.loads(src.read_text())
                rd["run"] |= {"seed": seed, "config_hash": plan.hashes[net][name]}
                d = probe._run_dir(root, net, name, plan.hashes[net][name], seed)
                d.mkdir(parents=True)
                if net == "as_built" and i < 2:
                    (d / "readings.json").write_text(src.read_text())
                else:
                    (d / "readings.json").write_text(json.dumps(rd))


@needs_grid
def test_the_analysis_reproduces_the_grids_scores(tmp_path: Path) -> None:
    plan = probe.build_probe()
    root = tmp_path / "probe"
    _fake_tree(plan, root)
    art = tmp_path / "probe.json"
    res = probe.analyze_probe(plan, root, art, argv=["--analyze-only"])
    assert json.loads(art.read_text())["schema"] == probe.SCHEMA and res["complete"]
    grid = json.loads((REPO_ROOT / "artifacts" / "driver_calibration_i94.json").read_text())
    grid_rows = {p["name"]: p for p in grid["pairs"]}
    for name, corrected in (("k0.0_kr0.0", 5.87), ("k1.0_kr0.1", 5.76)):
        row = res["results"]["as_built"][name]
        sc = row["scores"]
        assert sc["own"]["lane_rmse_pp"] == pytest.approx(grid_rows[name]["lane_rmse_pp"])
        assert sc["own"]["discharge_veh_h"] == pytest.approx(grid_rows[name]["discharge_veh_h"])
        assert sc["own_s791_reversed"]["lane_rmse_pp"] == pytest.approx(corrected, abs=0.005)
        assert row["n_collisions"] == 0 and row["n_seeds_read"] == 4
        # shares at every observed station by IRIS lane, compared or not
        assert set(row["stations"]) >= {"S791", "S792", "S1948", "S97"}
        assert row["stations"]["S1948"]["sim_lanes"] == 5
    # the fixed network's own lane table decides its compared stations
    nets = res["networks"]
    assert nets["netfix"]["ramps_unset"] == "1001426896,45782590"
    assert nets["as_built"]["stations_compared"] == [
        "S1066", "S1067", "S1068", "S1069", "S1070", "S791", "S97",
    ]  # fmt: skip
    assert set(res["stations_compared_by_both"]) <= set(nets["as_built"]["stations_compared"])
    assert {r["edge"] for r in nets["as_built"]["lanes"]} >= {"45782590-AddedOffRampEdge"}
    assert "45782590-AddedOffRampEdge" not in {r["edge"] for r in nets["netfix"]["lanes"]}
    # the committed readings at the grid's seeds are recognised as identical
    assert res["reproduces_grid"] == {
        name: {str(s): "identical" for s in plan.seeds[:2]} for name in plan.names
    }
    # the note's expectations are evaluated (identical readings here: none met)
    assert len(res["expectations"]) == 2 * 5
    assert all(e["met"] is False for e in res["expectations"])
