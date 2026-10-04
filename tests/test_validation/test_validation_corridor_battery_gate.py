"""``scripts/corridor_battery.py --baseline-gate`` on a stored run tree (no simulation).

Two stored replicates (``meta.json``, ``metrics.json``, ``observed_scores.json``
as a finished battery leaves them) are re-scored with ``--criteria-only``,
once without and once with the gate. The gate block must be additive — every
other key of the artifact identical — and must reach the report's client
summary and the files beside it. Two replicates are fewer than the protocol's
twenty, so the gate fails on that alone, whatever else holds.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import yaml

from tests.test_validation.test_validation_report import _traj
from validation.metrics import Metrics, geh
from validation.observed import LinkHourRecord, ObservedCorridor, ObservedScores

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "corridor_battery.py"

STATION_X = (200.0, 500.0, 800.0)
IDS = [f"S{i}" for i in range(len(STATION_X))]
WINDOW_S = 300.0
N_WINDOWS = 12
FLOW = 1200.0


def load_battery() -> ModuleType:
    """The battery script under a module name of its own."""
    spec = importlib.util.spec_from_file_location("corridor_battery_gate_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_scenario(path: Path) -> Path:
    config: dict[str, Any] = {
        "name": "battery_gate_corridor",
        "tier": "micro",
        "network": {"kind": "corridor", "length_m": 1000.0, "lanes": 1, "inflow": [[0.0, 0.2]]},
        "fleet": {"model": "IDM", "T": 1.4},
        "sim": {"duration_s": 3600.0, "step_length_s": 0.5, "warmup_s": 0.0, "output_hz": 1.0},
        "seed": 11,
        "replicates": 1,
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def write_observations(path: Path, *, flow: float = FLOW, dates: list[str] | None = None) -> Path:
    speeds = [25.0] * N_WINDOWS
    payload = {
        "schema": "flowstate.observations/1",
        "corridor": "battery_gate",
        "source": {
            "provider": "synthetic",
            "dates": dates or ["20260901"],
            "excluded_detectors": {"D9": "stuck"},
        },
        "window_s": WINDOW_S,
        "t0_local": "06:00",
        "duration_s": WINDOW_S * N_WINDOWS,
        "n_windows": N_WINDOWS,
        "aggregation": "synthetic constant profile",
        "stations": [
            {"id": sid, "label": sid, "x_m": x, "lanes": 1, "kind": "mainline"}
            for sid, x in zip(IDS, STATION_X, strict=True)
        ],
        "flows_veh_h": {sid: [flow] * N_WINDOWS for sid in IDS},
        "speeds_ms": {sid: speeds for sid in IDS},
        "context": {"detector_wave_speed": {"median_kmh": None, "n_pairs": 2, "n_used": 0}},
    }
    path.write_text(json.dumps(payload))
    return path


def stored_scores(observed: ObservedCorridor) -> ObservedScores:
    hourly = observed.hourly_link_flows()
    records = tuple(
        LinkHourRecord(
            station=str(s),
            x_ref_m=float(x),
            window_start_s=float(w),
            clock="06:00",
            obs_veh_h=float(q),
            sim_veh_h=float(q) * 1.02,
            geh=geh(float(q) * 1.02, float(q)),
        )
        for s, x, w, q in zip(
            hourly["station"],
            hourly["x_ref_m"],
            hourly["window_start_s"],
            hourly["flow_veh_h"],
            strict=True,
        )
    )
    sim = np.full((N_WINDOWS, len(IDS)), 24.0)
    return ObservedScores(
        geh_values=tuple(r.geh for r in records),
        n_link_hours=len(records),
        rmspe=0.04,
        n_speed_cells=sim.size,
        segment_speeds_sim=tuple(tuple(float(v) for v in row) for row in sim),
        segment_speeds_obs=tuple(tuple(25.0 for _ in IDS) for _ in range(N_WINDOWS)),
        windows=tuple(range(N_WINDOWS)),
        link_hours=records,
    )


def write_tree(battery: ModuleType, tmp_path: Path, n: int = 2) -> dict[str, Any]:
    """A finished, pruned battery of ``n`` replicates (the first keeps a trajectory)."""
    scenario = write_scenario(tmp_path / "battery_gate_corridor.yaml")
    observations = write_observations(tmp_path / "observations.json")
    cfg = battery.load_scenario(scenario).model_copy(update={"replicates": n})
    seeds = battery.spawn_seeds(cfg.seed, n)
    out_root = tmp_path / "runs"
    dirs = battery.seed_dirs(out_root, cfg, seeds)
    scores = stored_scores(ObservedCorridor.from_json(observations))
    for i, (run_dir, seed) in enumerate(zip(dirs, seeds, strict=True)):
        run_dir.mkdir(parents=True)
        meta = {
            "config_hash": battery.config_hash(cfg),
            "seed": seed,
            "tier": "micro",
            "seeded": False,
            "n_vehicles_planned": 500,
            "n_vehicles_departed": 500,
            "n_collisions": 0,
            "versions": {"eclipse-sumo": "1.27.1"},
        }
        (run_dir / "meta.json").write_text(json.dumps(meta))
        metrics = {
            f.name: (3 if f.type is int or f.name in ("wave_count", "n_travel_time_veh") else 10.0)
            for f in dataclasses.fields(Metrics)
        }
        (run_dir / battery.METRICS_FILE).write_text(
            json.dumps(
                {
                    "metrics": metrics,
                    "criterion_wave_speed_kmh": None,
                    "criterion_detector": "stack",
                    "x_ref_m": 0.0,
                    "span_m": [0.0, 0.0],
                    "insertion": {},
                }
            )
        )
        (run_dir / battery.SCORES_FILE).write_text(json.dumps(scores.to_dict()))
        if i == 0:
            _traj().to_parquet(run_dir / "trajectories.parquet")
    return {
        "scenario": scenario,
        "observations": observations,
        "out_root": out_root,
        "dirs": dirs,
        "config_hash": battery.config_hash(cfg),
    }


def _argv(tree: dict[str, Any], artifact: Path, report_dir: Path, n: int = 2) -> list[str]:
    return [
        "--scenario", str(tree["scenario"]),
        "--observations", str(tree["observations"]),
        "--replicates", str(n),
        "--procs", "1",
        "--out", str(tree["out_root"]),
        "--artifact", str(artifact),
        "--report-dir", str(report_dir),
        "--criteria-profile", "fhwa_default",
        "--ring-seeds", "0",
        "--criteria-only",
    ]  # fmt: skip


VOLATILE = ("created_at", "wall_s", "report_path")


def test_the_gate_block_is_additive_and_reaches_the_report(tmp_path: Path) -> None:
    battery = load_battery()

    def _refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("--criteria-only must not re-simulate")

    battery.run_replicates = _refuse
    tree = write_tree(battery, tmp_path)
    validation = write_observations(tmp_path / "obs_val.json", flow=FLOW * 1.01, dates=["20260902"])
    split = tmp_path / "split.json"
    split.write_text(
        json.dumps(
            {
                "seed": 20261004,
                "calibration_dates": ["2026-09-01"],
                "validation_dates": ["2026-09-02"],
                "underpowered": True,
                "underpowered_reason": "1 calibration day(s), fewer than 5",
            }
        )
    )
    plain_path = tmp_path / "artifacts" / "plain.json"
    gated_path = tmp_path / "artifacts" / "gated.json"
    assert battery.main(_argv(tree, plain_path, tmp_path / "report_plain")) == 0
    gated_report = tmp_path / "report_gated"
    assert (
        battery.main(
            [
                *_argv(tree, gated_path, gated_report),
                "--baseline-gate",
                "--gate-validation-observations",
                str(validation),
                "--gate-day-split",
                str(split),
            ]
        )
        == 0
    )
    plain = json.loads(plain_path.read_text())
    gated = json.loads(gated_path.read_text())
    assert "baseline_gate" not in plain
    block = gated.pop("baseline_gate")
    for key in VOLATILE:
        plain.pop(key, None)
        gated.pop(key, None)
    assert gated == plain  # every existing key byte-identical

    assert block["schema"] == "flowstate.baseline_gate/1"
    assert block["passed"] is False and block["strategy_results_allowed"] is False
    statuses = {(c["check"], c["day_set"]): c["status"] for c in block["checks"]}
    assert statuses[("replicates", "all runs")] == "fail"  # 2 < 20
    assert statuses[("C1", "calibration")] == "pass"
    assert statuses[("C1", "validation")] == "pass"
    assert statuses[("C3", "calibration")] == "pass"
    assert statuses[("C4", "calibration")] == "not_applicable"  # 0 of 2 pairs observed
    assert statuses[("C5", "all runs")] == "pass"
    assert statuses[("C6", "calibration")] == "pass"  # no bottleneck, no phantom
    assert block["config_hash"] == tree["config_hash"]
    assert block["split"]["validation_dates"] == ["2026-09-02"]
    assert block["excluded_detectors"] == {"D9": "stuck"}
    assert block["day_sets"]["validation"]["dates"] == ["20260902"]

    report = (gated_report / "report.md").read_text()
    assert "Baseline gate FAILED" in report
    assert "This report contains no strategy recommendations" in report
    assert (gated_report / "baseline_gate.json").is_file()
    assert "# Baseline gate" in (gated_report / "baseline_gate.md").read_text()
    plain_report = (tmp_path / "report_plain" / "report.md").read_text()
    assert "Baseline gate: NOT EVALUATED" in plain_report
