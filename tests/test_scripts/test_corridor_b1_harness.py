"""Amendment B1's readout harness (artifacts/i24_discharge_2026-10-07/harness/), offline.

No simulation and no run tree: the copy writer on the committed scenario into a
temporary directory, the braking counter and the zone reader on synthetic tables,
and the 15-min RMSPE convention on a committed battery artifact.
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
    assert r["cells"]["n"] == 16 and r["cells"]["x_bins_m"] == [250.0, 350.0]


def test_the_15min_rmspe_reproduces_the_published_value() -> None:
    """docs/I24_VALIDATION.md §0.5(a) published 0.263 for the speedcal arm."""
    d = json.loads((REPO_ROOT / "artifacts" / "i24_validation_speedcal.json").read_text())
    sim = np.asarray(d["simulated"]["segment_speeds_ms_mean"], float)
    obs = np.asarray(d["observed"]["segment_speeds_ms"], float)
    assert cb.rmspe(cb.agg_windows(sim, 3), cb.agg_windows(obs, 3)) == pytest.approx(
        0.263, abs=5e-4
    )
    assert cb.rmspe(sim, obs) == pytest.approx(d["rmspe"]["value"], rel=1e-12)
