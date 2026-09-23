"""Smoke test for ``scripts/corridor_battery.py`` (real SUMO, tiny corridor).

One 60 s replicate of a 1 km corridor scored against a synthetic
``flowstate.observations/1`` artifact: the battery must write the per-seed
files, the validation artifact and the report, and ``--criteria-only`` must
re-score from what is on disk without touching the simulator.
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

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "corridor_battery.py"

CORRIDOR_M = 1000.0
DURATION_S = 60.0
WINDOW_S = 30.0
N_WINDOWS = 2
STATION_X = (200.0, 800.0)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("corridor_battery", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["corridor_battery"] = module
    spec.loader.exec_module(module)
    return module


def _scenario(path: Path) -> Path:
    config: dict[str, Any] = {
        "name": "battery_smoke_corridor",
        "tier": "micro",
        "network": {
            "kind": "corridor",
            "length_m": CORRIDOR_M,
            "lanes": 1,
            "inflow": [[0.0, 0.2]],
        },
        "fleet": {"model": "IDM", "T": 1.4},
        "sim": {
            "duration_s": DURATION_S,
            "step_length_s": 0.5,
            "warmup_s": 0.0,
            "output_hz": 1.0,
        },
        "seed": 11,
        "replicates": 1,
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def _observations(path: Path) -> Path:
    ids = [f"S{i}" for i in range(len(STATION_X))]
    payload = {
        "schema": "flowstate.observations/1",
        "corridor": "battery_smoke",
        "source": {"provider": "synthetic", "dates": ["20260101"], "url": ""},
        "window_s": WINDOW_S,
        "t0_local": "06:00",
        "duration_s": WINDOW_S * N_WINDOWS,
        "n_windows": N_WINDOWS,
        "aggregation": "synthetic constant profile",
        "stations": [
            {"id": sid, "label": sid, "x_m": x, "lanes": 1, "kind": "mainline"}
            for sid, x in zip(ids, STATION_X, strict=True)
        ],
        "flows_veh_h": {sid: [720.0] * N_WINDOWS for sid in ids},
        "speeds_ms": {sid: [25.0] * N_WINDOWS for sid in ids},
        "quality": {sid: {"fraction_valid": 1.0, "n_dates": 1} for sid in ids},
    }
    path.write_text(json.dumps(payload))
    return path


def test_corridor_battery_end_to_end(tmp_path: Path) -> None:
    battery = _load_script()
    scenario = _scenario(tmp_path / "battery_smoke_corridor.yaml")
    observations = _observations(tmp_path / "observations.json")
    out_root = tmp_path / "runs"
    artifact_path = tmp_path / "artifacts" / "validation_smoke.json"
    report_dir = tmp_path / "report"
    argv = [
        "--scenario",
        str(scenario),
        "--observations",
        str(observations),
        "--replicates",
        "1",
        "--procs",
        "1",
        "--out",
        str(out_root),
        "--artifact",
        str(artifact_path),
        "--report-dir",
        str(report_dir),
        "--criteria-profile",
        "fhwa_default",
        "--ring-seeds",
        "0",
    ]
    assert battery.main(argv) == 0

    artifact = json.loads(artifact_path.read_text())
    assert artifact["schema"] == battery.ARTIFACT_SCHEMA
    assert artifact["replicates"] == 1
    assert len(artifact["seeds"]) == 1
    assert artifact["versions"]["eclipse-sumo"]
    assert artifact["observations"]["corridor"] == "battery_smoke"
    assert artifact["observations"]["n_stations"] == len(STATION_X)
    assert artifact["x_offset_m"] == pytest.approx(CORRIDOR_M)  # insertion buffer
    names = {row["name"] for row in artifact["criteria"]}
    assert {"link_flows_geh", "speeds_rmspe", "wave_speed", "n_seeds"} <= names
    assert artifact["metrics_ci"]["throughput_veh_h"]["n"] == 1
    assert artifact["metrics_ci"]["throughput_veh_h"]["underpowered"] is True
    assert len(artifact["per_seed"]) == 1
    seed_row = artifact["per_seed"][0]
    assert seed_row["n_speed_cells"] > 0
    assert seed_row["rmspe"] is not None
    # A 60 s run spans no whole hour, so the link-flow row is honestly
    # not evaluated rather than scored on a partial hour.
    assert seed_row["n_link_hours"] == 0
    geh_row = next(r for r in artifact["criteria"] if r["name"] == "link_flows_geh")
    assert geh_row["evaluated"] is False
    rmspe_row = next(r for r in artifact["criteria"] if r["name"] == "speeds_rmspe")
    assert rmspe_row["evaluated"] is True

    run_dir = Path(seed_row["run_dir"])
    assert (run_dir / battery.METRICS_FILE).is_file()
    assert (run_dir / battery.SCORES_FILE).is_file()
    assert (run_dir / "trajectories.parquet").is_file()  # the first seed is kept

    report = (report_dir / "report.md").read_text()
    assert "### Observed data" in report
    assert "synthetic" in report
    assert list(report_dir.glob("*.png"))

    # --criteria-only re-scores from disk and never re-simulates.
    def _refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("--criteria-only must not re-simulate")

    battery.run_replicates = _refuse
    artifact_path.unlink()
    assert battery.main([*argv, "--criteria-only"]) == 0
    rescored = json.loads(artifact_path.read_text())
    assert rescored["per_seed"][0]["n_speed_cells"] == seed_row["n_speed_cells"]
    assert rescored["metrics_ci"]["throughput_veh_h"]["mean"] == pytest.approx(
        artifact["metrics_ci"]["throughput_veh_h"]["mean"], rel=1e-9
    )
