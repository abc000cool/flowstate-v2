"""Smoke test for ``scripts/corridor_battery.py`` (real SUMO, tiny corridor).

One 60 s replicate of a 1 km corridor scored against a synthetic
``flowstate.observations/1`` artifact: the battery must write the per-seed
files, the validation artifact and the report, and ``--criteria-only`` must
re-score from what is on disk without touching the simulator.

A second scenario asks a single lane for an inflow it cannot take, so the
insertion guard (``--abort-if-departed-below``) must stop the pool on the
first finished replicate, write a partial artifact that says so and exit
``ABORT_EXIT_CODE`` instead of validating a run of demand that never entered
the network.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from validation.battery import MISSED_EXIT_SHARE_THRESHOLD

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


def _scenario(
    path: Path,
    *,
    inflow_veh_s: float = 0.2,
    duration_s: float = DURATION_S,
) -> Path:
    config: dict[str, Any] = {
        "name": "battery_smoke_corridor",
        "tier": "micro",
        "network": {
            "kind": "corridor",
            "length_m": CORRIDOR_M,
            "lanes": 1,
            "inflow": [[0.0, inflow_veh_s]],
        },
        "fleet": {"model": "IDM", "T": 1.4},
        "sim": {
            "duration_s": duration_s,
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

    # Insertion: the counters the guard reads are recorded per seed, pooled
    # into the artifact and stated in the report.
    insertion = artifact["insertion"]
    assert insertion["n_runs"] == 1
    assert insertion["planned"] > 0
    assert insertion["departed"] == seed_row["insertion"]["departed"]
    assert insertion["mean_departed_fraction"] == pytest.approx(
        seed_row["insertion"]["departed_fraction"]
    )
    assert seed_row["insertion"]["verdict"]
    # A corridor without a weaving section says nothing about given-up exits.
    assert artifact["weave_exits"] is None

    run_dir = Path(seed_row["run_dir"])
    assert (run_dir / battery.METRICS_FILE).is_file()
    assert (run_dir / battery.SCORES_FILE).is_file()
    assert (run_dir / "trajectories.parquet").is_file()  # the first seed is kept

    report = (report_dir / "report.md").read_text()
    assert "### Observed data" in report
    assert "synthetic" in report
    assert "Insertion: " in report
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


def _weave_section(ramp: str, exit_name: str, missed: int, reached: int) -> dict[str, Any]:
    return {
        "ramp": ramp,
        "exit": exit_name,
        "n_missed": missed,
        "n_missed_exit": missed,
        "n_reached_section_exiting": reached,
    }


def _write_scored_replicate(
    run_dir: Path, *, seed: int, config_hash: str, weave_sections: list[dict[str, Any]]
) -> None:
    """A replicate directory as a finished, pruned battery leaves it.

    ``meta.json`` (the completion marker), ``metrics.json`` and
    ``observed_scores.json`` are present; the trajectory is not, as after
    pruning — ``--criteria-only`` must work from the stored files alone.
    """
    battery = sys.modules["corridor_battery"]
    run_dir.mkdir(parents=True)
    meta = {
        "config_hash": config_hash,
        "seed": seed,
        "tier": "micro",
        "seeded": False,
        "n_vehicles_planned": 500,
        "n_vehicles_departed": 500,
        "n_vehicles_arrived": 480,
        "weave_sections": weave_sections,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta))
    metrics = {
        f.name: (3 if f.type is int or f.name in ("wave_count", "n_travel_time_veh") else 10.0)
        for f in dataclasses.fields(battery.Metrics)
    }
    (run_dir / battery.METRICS_FILE).write_text(
        json.dumps(
            {
                "metrics": metrics,
                "criterion_wave_speed_kmh": None,
                "criterion_detector": "profile",
                "x_ref_m": 0.0,
                "span_m": [0.0, 0.0],
                "insertion": {},
            }
        )
    )
    scores = battery.ObservedScores(
        geh_values=(4.0, 6.0),
        n_link_hours=2,
        rmspe=0.1,
        n_speed_cells=2,
        segment_speeds_sim=((25.0, 24.0),),
        segment_speeds_obs=((25.0, 25.0),),
        windows=(0,),
    )
    (run_dir / battery.SCORES_FILE).write_text(json.dumps(scores.to_dict()))


def test_criteria_only_surfaces_weave_exits_from_the_stored_metas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two stored replicates, two sections: one clean, one giving up 30 of 400."""
    battery = _load_script()
    scenario = _scenario(tmp_path / "battery_smoke_corridor.yaml")
    observations = _observations(tmp_path / "observations.json")
    out_root = tmp_path / "runs"
    cfg = battery.load_scenario(scenario).model_copy(update={"replicates": 2})
    seeds = battery.spawn_seeds(cfg.seed, 2)
    dirs = battery.seed_dirs(out_root, cfg, seeds)
    assert len(dirs) == 2
    for run_dir, seed, missed_b in zip(dirs, seeds, (12, 18), strict=True):
        _write_scored_replicate(
            run_dir,
            seed=seed,
            config_hash=battery.config_hash(cfg),
            weave_sections=[
                _weave_section("A-ON", "A-OFF", 0, 200),
                _weave_section("B-ON", "B-OFF", missed_b, 200),
            ],
        )

    def _refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("--criteria-only must not re-simulate")

    battery.run_replicates = _refuse
    artifact_path = tmp_path / "artifacts" / "validation.json"
    argv = [
        "--scenario",
        str(scenario),
        "--observations",
        str(observations),
        "--replicates",
        "2",
        "--procs",
        "1",
        "--out",
        str(out_root),
        "--artifact",
        str(artifact_path),
        "--report-dir",
        str(tmp_path / "report"),
        "--criteria-profile",
        "fhwa_default",
        "--ring-seeds",
        "0",
        "--criteria-only",
    ]
    assert battery.main(argv) == 0
    console = capsys.readouterr().out

    artifact = json.loads(artifact_path.read_text())
    assert artifact["schema"] == battery.ARTIFACT_SCHEMA  # additive: no version bump
    weave = artifact["weave_exits"]
    assert weave["threshold_share"] == MISSED_EXIT_SHARE_THRESHOLD == 0.02
    assert weave["n_runs"] == 2
    a, b = weave["sections"]
    assert a["ramp"] == "A-ON" and a["exit"] == "A-OFF"
    assert a["missed_exit"] == {"n": 0, "share": 0.0}
    assert a["reached"] == 400 and a["n_runs"] == 2 and a["flagged"] is False
    assert b["ramp"] == "B-ON" and b["exit"] == "B-OFF"
    assert b["missed_exit"]["n"] == 30
    assert b["missed_exit"]["share"] == pytest.approx(0.075)
    assert b["reached"] == 400 and b["flagged"] is True
    assert weave["verdict"] == "exits given up: 7.5 % at B-ON"
    # The insertion block itself is untouched; the console verdict is degraded.
    assert artifact["insertion"]["verdict"] == "ok"
    assert any("weave_exits block" in note for note in artifact["notes"])
    insertion_line = next(ln for ln in console.splitlines() if ln.strip().startswith("insertion"))
    assert "exits given up: 7.5 % at B-ON" in insertion_line
    assert "departed 1000/1000" in insertion_line
    weave_lines = [ln for ln in console.splitlines() if ln.strip().startswith("weave exits")]
    assert len(weave_lines) == 2
    assert (
        "A-ON: 0 of 400 reached exiters given up (0.0 %, within the 2 % threshold"
        in (weave_lines[0])
    )
    assert (
        "B-ON: 30 of 400 reached exiters given up (7.5 %, ABOVE the 2 % threshold"
        in (weave_lines[1])
    )
    assert "2 run(s)" in weave_lines[1]
    assert math.isfinite(artifact["metrics_ci"]["throughput_veh_h"]["mean"])


#: Artifact keys that legitimately differ between two batteries of the same
#: scenario (timestamps, wall-clock, and the output tree each was given).
VOLATILE_ARTIFACT_KEYS = ("created_at", "wall_s", "report_path")


def _comparable(artifact: dict[str, Any]) -> dict[str, Any]:
    """The artifact without its volatile keys and per-seed run directories."""
    stripped = {k: v for k, v in artifact.items() if k not in VOLATILE_ARTIFACT_KEYS}
    stripped["per_seed"] = [
        {k: v for k, v in row.items() if k != "run_dir"} for row in artifact["per_seed"]
    ]
    return stripped


def test_score_pool_matches_in_process_scoring(tmp_path: Path) -> None:
    """``--score-procs 2`` writes byte-identical per-seed files and the same artifact.

    Two 2-replicate batteries of one scenario: one scored in the parent
    (``--score-procs 1``), one in a two-process spawn pool. Replicates are
    independent, so the pool may only change the wall-clock. The report
    renders one contour (the first seed's, the one kept after pruning), not
    one per replicate.
    """
    battery = _load_script()
    scenario = _scenario(tmp_path / "battery_pool_corridor.yaml")
    observations = _observations(tmp_path / "observations.json")
    artifacts: dict[int, dict[str, Any]] = {}
    per_seed_files: dict[int, list[tuple[bytes, bytes]]] = {}
    for score_procs in (1, 2):
        root = tmp_path / f"score_procs_{score_procs}"
        artifact_path = root / "validation.json"
        report_dir = root / "report"
        argv = [
            "--scenario",
            str(scenario),
            "--observations",
            str(observations),
            "--replicates",
            "2",
            "--procs",
            "2",
            "--score-procs",
            str(score_procs),
            "--out",
            str(root / "runs"),
            "--artifact",
            str(artifact_path),
            "--report-dir",
            str(report_dir),
            "--ring-seeds",
            "0",
        ]
        assert battery.main(argv) == 0
        artifact = json.loads(artifact_path.read_text())
        artifacts[score_procs] = artifact
        per_seed_files[score_procs] = [
            (
                (Path(row["run_dir"]) / battery.METRICS_FILE).read_bytes(),
                (Path(row["run_dir"]) / battery.SCORES_FILE).read_bytes(),
            )
            for row in artifact["per_seed"]
        ]
        assert len(artifact["per_seed"]) == 2
        # One figure for the whole battery: the first seed's contour.
        assert len(list(report_dir.glob("speed_contour_*.png"))) == 1
        assert (report_dir / "report.md").is_file()
        # The first seed's trajectory is kept, the second's pruned.
        dirs = [Path(row["run_dir"]) for row in artifact["per_seed"]]
        assert (dirs[0] / "trajectories.parquet").is_file()
        assert not (dirs[1] / "trajectories.parquet").exists()

    assert per_seed_files[2] == per_seed_files[1]
    assert _comparable(artifacts[2]) == _comparable(artifacts[1])


#: Per-lane inflow no single lane can take (≈ 10× a lane's capacity), so most
#: of the plan is still queued outside the network when the run ends.
IMPOSSIBLE_INFLOW_VEH_S = 3.0
ABORT_DURATION_S = 90.0


def test_insertion_guard_aborts_on_the_first_replicate(tmp_path: Path) -> None:
    """A starved first replicate stops the pool and writes a partial artifact."""
    battery = _load_script()
    scenario = _scenario(
        tmp_path / "battery_starved_corridor.yaml",
        inflow_veh_s=IMPOSSIBLE_INFLOW_VEH_S,
        duration_s=ABORT_DURATION_S,
    )
    observations = _observations(tmp_path / "observations.json")
    artifact_path = tmp_path / "artifacts" / "validation_starved.json"
    report_dir = tmp_path / "report"
    argv = [
        "--scenario",
        str(scenario),
        "--observations",
        str(observations),
        "--replicates",
        "2",
        "--procs",
        "2",
        "--out",
        str(tmp_path / "runs"),
        "--artifact",
        str(artifact_path),
        "--report-dir",
        str(report_dir),
        "--ring-seeds",
        "0",
        "--abort-if-departed-below",
        "0.9",
    ]
    assert battery.main(argv) == battery.ABORT_EXIT_CODE

    artifact = json.loads(artifact_path.read_text())
    assert artifact["aborted"] is True
    assert artifact["criteria"] == [] and artifact["per_seed"] == []
    abort = artifact["abort"]
    assert abort["threshold_departed_fraction"] == pytest.approx(0.9)
    assert abort["n_replicates_completed"] >= 1
    assert "never departed" in abort["reason"]
    first = abort["per_seed"][0]["insertion"]
    assert first["departed"] < first["planned"]
    assert first["departed_fraction"] < 0.9
    # Nothing was validated, so no report was generated either.
    assert not (report_dir / "report.md").exists()


#: Config hash planted for the fake ring benchmark runs below.
RING_HASH = "ring0000ring"


def test_ring_benchmark_runs_are_not_report_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runs under ``<out>/ring/`` never join the corridor's report.

    ``--ring-seeds N`` writes the ring benchmark's replicates under
    ``<out>/ring/<hash>/<seed>/`` — micro runs with their own ``meta.json``.
    The report discovers runs by walking its root, so given ``<out>`` it
    would take them as extra configuration groups: a second baseline (no Δ
    column, the corridor unseated as reference when it is a controlled
    scenario), and their seed count feeding the replicate criterion. The
    battery therefore hands the report its own configuration's tree only.
    The ring benchmark itself is stubbed: it plants one ring-like run (the
    corridor's first replicate under another hash) and reports both rows
    passed.
    """
    battery = _load_script()
    scenario = _scenario(tmp_path / "battery_ring_corridor.yaml")
    observations = _observations(tmp_path / "observations.json")
    out_root = tmp_path / "runs"
    artifact_path = tmp_path / "artifacts" / "validation.json"
    report_dir = tmp_path / "report"

    def fake_ring_block(n_seeds: int, out_dir: Path) -> dict[str, Any]:
        corridor_run = next(p for p in out_root.rglob("meta.json") if "ring" not in p.parts)
        planted = out_dir / RING_HASH / "7"
        planted.mkdir(parents=True, exist_ok=True)
        meta = json.loads(corridor_run.read_text())
        meta["config_hash"] = RING_HASH
        meta["seed"] = 7
        (planted / "meta.json").write_text(json.dumps(meta))
        (planted / "trajectories.parquet").write_bytes(
            (corridor_run.parent / "trajectories.parquet").read_bytes()
        )
        return {
            "emergence": {"passed": True},
            "dampening": {"passed": True},
            "seeds": [7],
        }

    monkeypatch.setattr(battery, "ring_block", fake_ring_block)
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
        "--ring-seeds",
        "1",
    ]
    assert battery.main(argv) == 0
    assert (out_root / "ring" / RING_HASH / "7" / "meta.json").is_file()
    artifact = json.loads(artifact_path.read_text())
    assert artifact["ring"]["emergence"]["passed"] is True

    report = (report_dir / "report.md").read_text()
    assert RING_HASH not in report
    assert "strategy comparison" not in report.lower()
    assert "no unambiguous reference to subtract" not in report
    # The replicate criterion sees the corridor's own seed count, as the artifact does.
    n_seeds = next(row for row in artifact["criteria"] if row["name"] == "n_seeds")
    assert f"| n_seeds | {n_seeds['value']:g} |" in report

    # --criteria-only regenerates the report from the pruned tree with the same run set.
    (report_dir / "report.md").unlink()
    assert battery.main([*argv, "--criteria-only"]) == 0
    assert RING_HASH not in (report_dir / "report.md").read_text()
