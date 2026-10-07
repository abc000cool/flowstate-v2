"""The scripts carry the scored end of a run with a cool-down (docs/FRISCO_PROTOCOL.md §8.2).

``--scored-end-s`` reaches every measurement of ``scripts/corridor_sweep.py``'s
worker (the standard metrics and the waiting measures, so the tuning
objective and the throughput guard of ``scripts/strategy_tune.py`` are scored
on the study period), is carried by the tuning harness's payloads, and is
recorded additively in the corridor battery's artifact. Without the option
nothing changes: no key is written and every call is the old one. No SUMO
here: the simulation and the two measurements are replaced by recorders.
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.test_scripts import test_corridor_sweep as sweep_base
from tests.test_scripts import test_strategy_tune as tune_base
from tests.test_validation.test_validation_corridor_battery_collisions import (
    SEEDS,
    _load_script,
)
from tests.test_validation.test_validation_observed import table_payload, table_scores
from validation.battery import insertion_stats, json_safe, weave_exit_summary
from validation.metrics import Metrics, WaitingMetrics
from validation.observed import ObservedCorridor

sweep = sweep_base.sweep
tune = tune_base.tune

CONFIG: dict[str, Any] = {
    "name": "table_corridor",
    "tier": "micro",
    "network": {"kind": "corridor", "length_m": 1000.0, "lanes": 1, "inflow": [[0.0, 0.2]]},
    "sim": {"duration_s": 7200.0, "step_length_s": 0.5, "output_hz": 1.0},
    "seed": 11,
    "replicates": len(SEEDS),
}


def _metrics() -> Metrics:
    return Metrics(
        **{
            f.name: (3 if f.name in ("wave_count", "n_travel_time_veh") else 10.0)
            for f in dataclasses.fields(Metrics)
        }
    )


def _waiting() -> WaitingMetrics:
    return WaitingMetrics(
        **{
            f.name: (5 if f.name.startswith("n_") else 1.0)
            for f in dataclasses.fields(WaitingMetrics)
        }
    )


class TestSweepWorker:
    @pytest.fixture()
    def calls(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
        import microsim.runner
        import validation.metrics

        seen: dict[str, list[Any]] = {"metrics": [], "waiting": []}

        def fake_run(cfg: Any, seed: int, root: Path) -> Any:
            run_dir = Path(root) / str(seed)
            run_dir.mkdir(parents=True)
            return microsim.runner.RunPaths(
                run_dir=run_dir,
                trajectories=run_dir / "trajectories.parquet",
                edges=run_dir / "edges.parquet",
                meta=run_dir / "meta.json",
            )

        def fake_metrics(run_dir: Path, **kwargs: Any) -> Metrics:
            seen["metrics"].append(kwargs)
            return _metrics()

        def fake_waiting(run_dir: Path, **kwargs: Any) -> WaitingMetrics:
            seen["waiting"].append(kwargs)
            return _waiting()

        monkeypatch.setattr(microsim.runner, "run_micro", fake_run)
        monkeypatch.setattr(validation.metrics, "compute_metrics", fake_metrics)
        monkeypatch.setattr(validation.metrics, "compute_waiting_metrics", fake_waiting)
        return seen

    def test_the_scored_end_reaches_both_measurements(
        self, tmp_path: Path, calls: dict[str, list[Any]]
    ) -> None:
        margs = {"x_ref": 500.0, "span": (0.0, 1000.0), "scored_end_s": 6600.0}
        *_, ok, err = sweep._worker(("baseline", CONFIG, 7, margs, str(tmp_path), True))
        assert ok, err
        assert calls["metrics"] == [margs]
        assert calls["waiting"] == [{"scored_end_s": 6600.0}]
        stored = json.loads((tmp_path / "baseline" / "7" / "metrics.json").read_text())
        assert stored["n_demand_veh"] == 5  # the waiting measures beside the standard ones

    def test_without_it_the_calls_are_the_old_ones(
        self, tmp_path: Path, calls: dict[str, list[Any]]
    ) -> None:
        margs = {"x_ref": 500.0, "span": (0.0, 1000.0)}
        *_, ok, err = sweep._worker(("baseline", CONFIG, 7, margs, str(tmp_path), True))
        assert ok, err
        assert calls["metrics"] == [margs]
        assert calls["waiting"] == [{"scored_end_s": None}]


class TestTuningPayloads:
    def test_payloads_carry_the_scored_end_only_when_given(self) -> None:
        cells = {"baseline": "h0"}
        configs = {"baseline": CONFIG}
        args = {"x_ref": 1800.0, "span": [100.0, 1900.0]}
        root = Path("/nonexistent/tree")
        (payload,) = tune.pending_payloads(root, cells, configs, [3], args, False)
        assert payload[3] == {"x_ref": 1800.0, "span": (100.0, 1900.0)}
        with_end = {**args, "scored_end_s": 14_400.0}
        (payload,) = tune.pending_payloads(root, cells, configs, [3], with_end, False)
        assert payload[3] == {"x_ref": 1800.0, "span": (100.0, 1900.0), "scored_end_s": 14_400.0}

    def test_the_option_is_parsed(self) -> None:
        parser = tune.build_parser()
        assert parser.parse_args(["--out", "x"]).scored_end_s is None
        assert parser.parse_args(["--out", "x", "--scored-end-s", "14400"]).scored_end_s == 14_400.0


class TestBatteryArtifact:
    @staticmethod
    def _build(tmp_path: Path, scored_end_s: float | None) -> dict[str, Any]:
        battery = _load_script()
        scenario = tmp_path / "table_corridor.yaml"
        scenario.write_text(yaml.safe_dump(CONFIG, sort_keys=False))
        kwargs: dict[str, Any] = {} if scored_end_s is None else {"scored_end_s": scored_end_s}
        artifact: dict[str, Any] = battery.build_artifact(
            scenario="table_corridor",
            cfg=battery.load_scenario(scenario),
            profile=battery.get_profile("fhwa_default"),
            seeds=list(SEEDS),
            dirs=[Path("runs") / str(seed) for seed in SEEDS],
            metrics_list=[_metrics()] * len(SEEDS),
            scores_list=[table_scores()] * len(SEEDS),
            wave_speeds=[math.nan] * len(SEEDS),
            insertion_list=[insertion_stats({"n_vehicles_planned": 7, "n_vehicles_departed": 7})]
            * len(SEEDS),
            weave_exits=weave_exit_summary([{}] * len(SEEDS)),
            observed=ObservedCorridor.from_dict(table_payload()),
            observations_path="artifacts/observations_table.json",
            criteria_rows=[],
            ring=None,
            x_offset_m=50.0,
            wall_s=1.0,
            **kwargs,
        )
        return artifact

    def test_the_scored_end_is_recorded_additively(self, tmp_path: Path) -> None:
        old = self._build(tmp_path, None)
        new = self._build(tmp_path, 6600.0)
        assert "scored_end_s" not in old
        keys = list(new)
        assert keys[keys.index("x_offset_m") + 1] == "scored_end_s"
        assert new["scored_end_s"] == 6600.0
        assert [k for k in keys if k != "scored_end_s"] == list(old)
        assert new["notes"][:-1] == old["notes"]
        assert "Scored period ends at 6600 s" in new["notes"][-1]
        for key in old:
            if key not in ("created_at", "notes"):
                assert json.dumps(json_safe(new[key])) == json.dumps(json_safe(old[key])), key

    def test_the_option_is_parsed(self) -> None:
        battery = _load_script()
        base = ["--scenario", "s", "--observations", "o", "--out", "r", "--artifact", "a"]
        base += ["--report-dir", "d"]
        assert battery.parse_args(base).scored_end_s is None
        assert battery.parse_args([*base, "--scored-end-s", "6600"]).scored_end_s == 6600.0
