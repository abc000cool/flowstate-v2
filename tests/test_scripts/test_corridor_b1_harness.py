"""Amendment B1's readout harness (artifacts/i24_discharge_2026-10-07/harness/), offline.

No simulation and no run tree: the copy writer on the committed scenario into a
temporary directory, the braking counter and the zone reader on synthetic tables,
the 15-min RMSPE convention on a committed battery artifact, and the readout end to
end on synthetic batteries in a temporary repository (a missing replicate; an arm
with problems blocking the adoption reading).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from flowstate_core.config import ScenarioConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "artifacts" / "i24_discharge_2026-10-07" / "harness"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"b1_harness_{name}", HARNESS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cb = _load("corridor_b1")
hb = _load("hard_braking")


def test_make_copy_changes_only_the_name_and_the_factor(tmp_path: Path) -> None:
    src = REPO_ROOT / "scenarios" / "i24_replica_flow_speedcal_dc_refit.yaml"
    out = tmp_path / "copy.yaml"
    h = cb.make_copy(src, out, 1.2185, "ada3f406504b")
    assert h == "843b3b0c8634"  # the copy's hash, as the stage header quotes it
    a, b = ScenarioConfig.from_yaml(src), ScenarioConfig.from_yaml(out)
    assert b.name == "i24_replica_flow_speedcal_dc_refit_b1"
    assert b.network.boundary.limit_factor == 1.2185  # type: ignore[union-attr]
    da, db = a.model_dump(mode="json"), b.model_dump(mode="json")
    db["name"] = da["name"]
    del db["network"]["boundary"]["limit_factor"]
    assert da == db
    assert out.read_text().splitlines()[0].startswith("# i24_replica_flow_speedcal_dc_refit_b1: ")
    with pytest.raises(SystemExit, match="expected 000000000000"):
        cb.make_copy(src, tmp_path / "x.yaml", 1.2185, "000000000000")


def test_the_braking_counter_counts_below_each_threshold(tmp_path: Path) -> None:
    traj = tmp_path / "trajectories.parquet"
    pd.DataFrame(
        {
            "t": [100.0, 700.0, 701.0, 702.0, 703.0],
            "x": [10.0, 20.0, 30.0, 40.0, 50.0],
            "a": [-9.0, -9.5, -7.5, -5.0, 0.3],
        }
    ).to_parquet(traj)
    r = hb.count(traj)
    assert r["n_vehicle_steps"] == 5
    assert r["below_ms2"] == {"-4.5": 4, "-7.0": 3, "-8.9": 2}
    assert r["below_ms2_study_window"] == {"-4.5": 3, "-7.0": 2, "-8.9": 1}
    assert r["emergency_steps_t_x_a"] == [[100.0, 10.0, -9.0], [700.0, 20.0, -9.5]]


def test_the_zone_speed_is_the_edie_speed_of_the_cells_wholly_inside(tmp_path: Path) -> None:
    """Cells of 15 s x 100 m; the zone [150, 460] m holds the 200-300 and 300-400 m
    columns wholly; inside, flow / density = 10 m/s in one and 20 m/s in the other at
    equal density, so the Edie speed is 15 m/s; the cells cut by the zone's edges
    (100-200, 400-500 m; 5 m/s) are left out."""
    rows = []
    for t in np.arange(7.5, 120.0, 15.0):
        for x, v in ((150.0, 5.0), (250.0, 10.0), (350.0, 20.0), (450.0, 5.0)):
            rows.append({"t_bin": t, "x_bin": x, "density": 0.02, "flow": 0.02 * v})
    run = tmp_path / "run"
    run.mkdir()
    pd.DataFrame(rows).to_parquet(run / "edges.parquet")
    zone = {"sim_x_m": [150.0, 460.0]}
    r = cb.zone_speeds(run, zone, 0.0, 120.0)
    assert r["speed_ms"] == pytest.approx(15.0)
    assert r["edie_2h_ms"] == pytest.approx(15.0)  # every window alike: the two estimators agree
    assert r["schedule_windows"] == {
        "window_s": 30.0,
        "n": 4,
        "filled": 0,
        "cells_inside_one_window": True,
    }
    assert r["cells"]["n"] == 16 and r["cells"]["x_bins_m"] == [250.0, 350.0]


def _two_window_field(run: Path, v0: float, v1: float) -> None:
    """15-s x 100-m cells over sim 600-660 s (two 30-s schedule windows): the zone's two columns
    (150, 250 m; zone 100-300 m) at density 0.06 and speed ``v0`` in the first window, 0.02 and ``v1``
    in the second; a column outside the zone (350 m) at 1 m/s."""
    rows = []
    for t in (607.5, 622.5, 637.5, 652.5):
        k, v = (0.06, v0) if t < 630.0 else (0.02, v1)
        rows += [{"t_bin": t, "x_bin": x, "density": k, "flow": k * v} for x in (150.0, 250.0)]
        rows.append({"t_bin": t, "x_bin": 350.0, "density": 0.1, "flow": 0.1})
    run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(run / "edges.parquet")


def test_the_zone_speed_averages_the_schedules_30s_windows_unweighted(tmp_path: Path) -> None:
    """5 m/s at density 0.06, then 25 m/s at 0.02: the schedule's estimator (unweighted mean of the
    30-s window speeds) is 15 m/s; one Edie speed over both windows weights the dense slow one three
    times as much, (3 x 5 + 25) / 4 = 10 m/s, 5 m/s lower. A schedule of the same two window speeds has
    the study-window mean 15 m/s, so the zone speed reproduces it exactly and the old estimator does not."""
    _two_window_field(tmp_path / "run", 5.0, 25.0)
    r = cb.zone_speeds(tmp_path / "run", {"sim_x_m": [100.0, 300.0]}, 600.0, 660.0)
    assert r["speed_ms"] == pytest.approx(15.0)
    assert r["edie_2h_ms"] == pytest.approx(10.0)
    assert r["speed_ms"] - r["edie_2h_ms"] == pytest.approx(5.0)
    assert r["schedule_windows"]["n"] == 2 and r["schedule_windows"]["cells_inside_one_window"]
    steps = [[0.0, 5.0], [630.0, 25.0]]
    assert cb.schedule_on_grid(steps, 600.0, 660.0)
    assert cb.schedule_mean(steps, 600.0, 660.0) == pytest.approx(r["speed_ms"])


def test_cells_straddling_a_schedule_window_are_flagged(tmp_path: Path) -> None:
    """20-s cells from 600 s: the 620-640 s cell crosses the 630-s window boundary."""
    run = tmp_path / "run"
    run.mkdir()
    pd.DataFrame(
        [
            {"t_bin": t, "x_bin": x, "density": 0.02, "flow": 0.2}
            for t in (610.0, 630.0, 650.0)
            for x in (150.0, 250.0)
        ]
    ).to_parquet(run / "edges.parquet")
    r = cb.zone_speeds(run, {"sim_x_m": [100.0, 300.0]}, 600.0, 660.0)
    assert r["schedule_windows"]["cells_inside_one_window"] is False


def test_the_committed_schedule_is_on_the_30s_grid_the_zone_speed_uses() -> None:
    """The I-24 schedule (scripts/i24_build_replica.py boundary_schedule: one value per 30 s from data
    1,800 s, i.e. sim 600 s) as the dc_refit scenario carries it: its study-window mean is the plain
    mean of its 240 windows, 13.86885 m/s (docs/I24_DISCHARGE_DIAGNOSIS.md §8.3)."""
    cfg = ScenarioConfig.from_yaml(
        REPO_ROOT / "scenarios" / "i24_replica_flow_speedcal_dc_refit.yaml"
    )
    steps = cfg.model_dump(mode="json")["network"]["boundary"]["steps"]
    assert cb.schedule_on_grid(steps, 600.0, 7800.0)
    assert len(steps) == 240
    mean = cb.schedule_mean(steps, 600.0, 7800.0)
    assert mean == pytest.approx(float(np.mean([v for _, v in steps])), rel=1e-12)
    assert mean == pytest.approx(13.86885, abs=5e-6)
    assert not cb.schedule_on_grid([[0.0, 1.0], [615.0, 2.0]], 600.0, 660.0)
    assert not cb.schedule_on_grid([[610.0, 1.0]], 600.0, 660.0)
    assert cb.zone_geometry()["schedule_window_s"] == cb.BOUNDARY_WINDOW_S == 30.0


def test_paired_skips_a_pair_missing_either_value() -> None:
    assert cb.paired([1.0, None, 3.0, 4.0], [0.5, 2.0, None, 3.0]) == cb.ci([0.5, 1.0])
    assert cb.paired([None], [1.0]) is None


def test_the_15min_rmspe_reproduces_the_published_value() -> None:
    """docs/I24_VALIDATION.md §0.5(a) published 0.263 for the speedcal arm."""
    d = json.loads((REPO_ROOT / "artifacts" / "i24_validation_speedcal.json").read_text())
    sim = np.asarray(d["simulated"]["segment_speeds_ms_mean"], float)
    obs = np.asarray(d["observed"]["segment_speeds_ms"], float)
    assert cb.rmspe(cb.agg_windows(sim, 3), cb.agg_windows(obs, 3)) == pytest.approx(
        0.263, abs=5e-4
    )
    assert cb.rmspe(sim, obs) == pytest.approx(d["rmspe"]["value"], rel=1e-12)


# --------------------------------------------------------------------------- the readout end to end

SEEDS = [11, 22, 33]
FACTOR = 1.2185
SCHEDULE = [[0.0, 10.0], [630.0, 20.0]]  # study-window mean 15 m/s over sim 600-660 s


def _battery(root: Path, label: str, factor: float | None, v: tuple[float, float]) -> None:
    """A battery of three replicates as scripts/i24_validate.py and the runner record them: the artifact,
    each replicate's meta.json and edges.parquet (``_two_window_field``), the braking counts."""
    run_dirs = []
    for seed in SEEDS:
        rd = root / "runs" / "i24_validation" / label / "cfg" / str(seed)
        _two_window_field(rd, *v)
        meta = {
            "config_hash": label,
            "n_collisions": 0,
            "boundary": {} if factor is None else {"limit_factor": factor},
            "config": {"network": {"boundary": {"steps": SCHEDULE}}},
        }
        (rd / "meta.json").write_text(json.dumps(meta))
        run_dirs.append(str(rd))
    seg = [[10.0, 20.0]] * 3
    art = {
        "config_hash": label,
        "scenario": f"scenarios/{label}.yaml",
        "simulated": {
            "seeds": SEEDS,
            "run_dirs": run_dirs,
            # three 20-s windows, three sections: 100 vehicles each, 6,000 veh/h
            "counts_per_replicate": [[[34, 33, 33]] * 3 for _ in SEEDS],
            "n_collisions_per_replicate": [0] * 3,
            "demand_realized_fraction": [0.9] * 3,
            "waves_per_replicate": [{"n_backward": 3}] * 3,
            "waves_stripe_per_replicate": [{"n_backward": 2}] * 3,
            "segment_speeds_ms_mean": seg,
            "segment_speeds_ms_per_replicate": [seg] * 3,
        },
        "observed": {
            "t_range_s": [1800.0, 1860.0],
            "n_windows": 3,
            "window_s": 20.0,
            "sections_m": [2200.0, 3200.0, 5400.0],
            "hourly_flows_veh_h_recommended": [[6000.0] * 3] * 3,
            "segment_speeds_ms": seg,
        },
        "criteria": [
            {"name": "wave_speed", "value": 16.0, "passed": True, "evaluated": True, "detail": ""},
            {"name": "link_flows_geh", "value": 1.0},
        ],
        "rmspe": {"value": 0.0},
    }
    (root / "artifacts" / f"i24_validation_{label}.json").write_text(json.dumps(art))
    per_seed = {str(s): {"below_ms2": {"-8.9": 0}} for s in SEEDS}
    (root / "runs" / "i24_validation" / label / "hard_braking.json").write_text(
        json.dumps({"per_seed": per_seed})
    )


ARMS = [
    ["canonical", "c_ref", "c_b1", "artifacts/i24_validation_none.json"],
    ["dc_refit", "d_ref", "d_b1", "artifacts/i24_validation_none.json"],
]


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary repository with both arms' batteries. On dc_refit the reference's field is 5 then
    25 m/s (zone speed 15 m/s, error 0; one Edie speed 10 m/s, error -5) and B1's 12 then 20 m/s (16 m/s,
    error +1; Edie 14 m/s, error -1): A2's zone half fails on the schedule's estimator, where the
    vehicle-time weighted one would have passed it."""
    root = tmp_path / "repo"
    (root / "artifacts").mkdir(parents=True)
    (root / "artifacts" / "i24_replica_inputs_flow.json").write_text(
        json.dumps(
            {
                "geometry": {"sim_x_of_data_x": {"a": 0.0, "b": 1.0}},
                "boundary": {"x_range_m": [100.0, 300.0], "window_s": 30.0},
            }
        )
    )
    for arm in ("c", "d"):
        _battery(root, f"{arm}_ref", None, (5.0, 25.0))
        _battery(root, f"{arm}_b1", FACTOR, (12.0, 20.0))
    monkeypatch.setattr(cb, "REPO", root)
    monkeypatch.setattr(cb, "factor_provenance", lambda: {"i24": "stub"})
    return root


def _main(monkeypatch: pytest.MonkeyPatch, out: Path) -> int:
    argv = ["corridor_b1.py", "evaluate", "--factor", str(FACTOR), "--out", str(out)]
    for arm in ARMS:
        argv += ["--arm", *arm]
    monkeypatch.setattr(sys, "argv", argv)
    try:
        cb.main()
    except SystemExit as e:
        return int(e.code or 0)
    return 0


def test_the_readout_reads_a2_on_the_schedules_estimator(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = repo / "out.json"
    assert _main(monkeypatch, out) == 0
    doc = json.loads(out.read_text())
    a2 = doc["arms"]["dc_refit"]["criteria"]["A2"]
    zone = a2["zone"]
    assert zone["schedule_mean_kmh"] == pytest.approx(15.0 * 3.6)
    assert zone["reference_error_kmh"] == pytest.approx(0.0, abs=1e-9)
    assert zone["b1_error_kmh"] == pytest.approx(1.0 * 3.6)
    assert zone["verdict"] is False
    rep = zone["edie_2h_reported"]
    assert rep["reference_error_kmh"] == pytest.approx(-5.0 * 3.6)
    assert rep["b1_error_kmh"] == pytest.approx(-1.0 * 3.6)
    assert zone["paired_zone_speed_b1_minus_ref_kmh"][0] == pytest.approx(3.6)
    assert a2["flow_ok"] is True and a2["verdict"] is False
    assert doc["arms"]["dc_refit"]["b1"]["zone_edie_2h_ms"] == pytest.approx([14.0] * 3)
    assert doc["adoption"]["blocked_by_problems"] == {}
    assert doc["adoption"]["i24"] == {"A1": True, "A2": False, "A3": True, "A4": True, "A5": True}
    assert doc["adoption"]["i24_holds"] is False


@pytest.mark.parametrize("missing", ["edges.parquet", "meta.json"])
def test_a_missing_replicate_leaves_a2_not_computed_and_blocks(
    repo: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    (repo / "runs" / "i24_validation" / "d_b1" / "cfg" / "22" / missing).unlink()
    out = repo / "out.json"
    assert _main(monkeypatch, out) == cb.EXIT_BLOCKED
    doc = json.loads(out.read_text())
    arm = doc["arms"]["dc_refit"]
    zone = arm["criteria"]["A2"]["zone"]
    assert zone["verdict"] is None and "not_computed" in zone
    assert zone["seeds_without_zone_speed"] == {"b1": [22], "reference": []}
    assert arm["criteria"]["A2"]["verdict"] is None
    assert arm["b1"]["zone_speed_ms"][1] is None
    assert any(p.startswith(f"22: no {missing}") for p in arm["problems"])
    assert set(doc["adoption"]["blocked_by_problems"]) == {"dc_refit"}
    assert doc["adoption"]["i24_holds"] is None


def test_an_arm_with_problems_blocks_the_adoption_reading(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every criterion computed, but one B1 replicate of the canonical arm records another factor."""
    meta_p = repo / "runs" / "i24_validation" / "c_b1" / "cfg" / "33" / "meta.json"
    meta = json.loads(meta_p.read_text())
    meta["boundary"]["limit_factor"] = 1.0
    meta_p.write_text(json.dumps(meta))
    out = repo / "out.json"
    assert _main(monkeypatch, out) == cb.EXIT_BLOCKED
    doc = json.loads(out.read_text())
    assert all(v is not None for v in doc["adoption"]["i24"].values())
    assert doc["adoption"]["i24_holds"] is None
    assert doc["adoption"]["blocked_by_problems"] == {
        "canonical": ["33: limit_factor 1.0 recorded, 1.2185 expected"]
    }
