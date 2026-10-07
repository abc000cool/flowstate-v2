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


# --- review 2026-10-07: runs record their window; resumes never mix windows ------------------

MARGS = {"x_ref": 500.0, "span": (0.0, 1000.0)}


class TestRunsRecordTheirWindow:
    @pytest.fixture()
    def fake(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import microsim.runner
        import validation.metrics

        def fake_run(cfg: Any, seed: int, root: Path) -> Any:
            run_dir = Path(root) / str(seed)
            run_dir.mkdir(parents=True)
            return microsim.runner.RunPaths(
                run_dir=run_dir,
                trajectories=run_dir / "trajectories.parquet",
                edges=run_dir / "edges.parquet",
                meta=run_dir / "meta.json",
            )

        monkeypatch.setattr(microsim.runner, "run_micro", fake_run)
        monkeypatch.setattr(validation.metrics, "compute_metrics", lambda d, **kw: _metrics())
        monkeypatch.setattr(
            validation.metrics, "compute_waiting_metrics", lambda d, **kw: _waiting()
        )

    @pytest.mark.parametrize("end", [None, 6600.0])
    def test_metrics_json_records_the_metric_arguments(
        self, tmp_path: Path, fake: None, end: float | None
    ) -> None:
        margs = {**MARGS, **({} if end is None else {"scored_end_s": end})}
        *_, ok, err = sweep._worker(("baseline", CONFIG, 7, margs, str(tmp_path), True))
        assert ok, err
        stored = json.loads((tmp_path / "baseline" / "7" / "metrics.json").read_text())
        assert stored["metrics_args"] == {
            "x_ref": 500.0,
            "span": [0.0, 1000.0],
            "scored_end_s": end,
        }


def _stored_run(root: Path, cell: str, chash: str, seed: int, record: Any = "absent") -> Path:
    d = root / cell / chash / str(seed)
    d.mkdir(parents=True)
    payload: dict[str, Any] = {"throughput_veh_h": 1000.0}
    if record != "absent":
        payload["metrics_args"] = record
    (d / "metrics.json").write_text(json.dumps(payload))
    return d


def _manifest(root: Path, metrics_args: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "MANIFEST.json").write_text(json.dumps({"metrics_args": metrics_args}))


class TestResumeConflict:
    RUNS = (("baseline", "h0", 1), ("baseline", "h0", 2))

    def test_nothing_stored_nothing_to_refuse(self, tmp_path: Path) -> None:
        _manifest(tmp_path, {"x_ref": 1.0, "span": [0.0, 2.0]})
        assert sweep.resume_conflict(tmp_path, self.RUNS, {**MARGS, "scored_end_s": 6600.0}) is None

    def test_a_manifest_scored_otherwise_is_refused(self, tmp_path: Path) -> None:
        # a tree launched without --scored-end-s (the manifest before the option existed)
        _manifest(tmp_path, {"x_ref": 500.0, "span": [0.0, 1000.0]})
        _stored_run(tmp_path, "baseline", "h0", 1)
        assert sweep.resume_conflict(tmp_path, self.RUNS, MARGS) is None
        why = sweep.resume_conflict(tmp_path, self.RUNS, {**MARGS, "scored_end_s": 6600.0})
        assert why is not None and "MANIFEST.json" in why and "fresh --out" in why
        # and the other way round: a tree scored to 6600 s resumed without the option
        _manifest(tmp_path, {**MARGS, "scored_end_s": 6600.0})
        why = sweep.resume_conflict(tmp_path, self.RUNS, MARGS)
        assert why is not None and "'scored_end_s': 6600.0" in why
        # x_ref and span count too
        why = sweep.resume_conflict(tmp_path, self.RUNS, {**MARGS, "x_ref": 400.0})
        assert why is not None

    def test_a_run_scored_otherwise_is_refused(self, tmp_path: Path) -> None:
        _manifest(tmp_path, dict(MARGS))
        record = {"x_ref": 500.0, "span": [0.0, 1000.0], "scored_end_s": 6600.0}
        p = _stored_run(tmp_path, "baseline", "h0", 2, record) / "metrics.json"
        why = sweep.resume_conflict(tmp_path, self.RUNS, MARGS)
        assert why is not None and str(p) in why and "fresh --out" in why
        assert sweep.resume_conflict(tmp_path, [], MARGS) is None  # not among the runs reused

    def test_an_unrecorded_run_is_unknown(self, tmp_path: Path) -> None:
        """A run stored before the record existed: refused only when a scored end is set."""
        _manifest(tmp_path, {**MARGS, "scored_end_s": 6600.0})
        _stored_run(tmp_path, "baseline", "h0", 1)
        _stored_run(tmp_path, "baseline", "h0", 2, {**MARGS, "scored_end_s": 6600.0})
        why = sweep.resume_conflict(tmp_path, self.RUNS, {**MARGS, "scored_end_s": 6600.0})
        assert why is not None and "do not record the window" in why
        _manifest(tmp_path, dict(MARGS))
        (tmp_path / "baseline" / "h0" / "2" / "metrics.json").unlink()
        assert sweep.resume_conflict(tmp_path, self.RUNS, MARGS) is None


def _scenario(tmp_path: Path) -> Path:
    path = tmp_path / "table_corridor.yaml"
    path.write_text(yaml.safe_dump(CONFIG, sort_keys=False))
    return path


def _sweep_argv(scenario: Path, out: Path, *extra: str) -> list[str]:
    return [
        "corridor_sweep.py", "--scenario", str(scenario), "--penetration", "--strategies", "none",
        "--x-ref", "500", "--span", "0", "1000", "--replicates", "2", "--out", str(out),
        "--summary", str(out.parent / "summary.json"), *extra,
    ]  # fmt: skip


def _no_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("simulation started")

    monkeypatch.setattr(sweep.mp, "get_context", refuse)


class TestSweepRefusesBeforeSimulating:
    def test_a_resume_with_another_scored_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from flowstate_core.config import ScenarioConfig, config_hash
        from flowstate_core.rng import spawn_seeds

        scenario = _scenario(tmp_path)
        out = tmp_path / "sweep"
        base = ScenarioConfig.from_yaml(scenario)
        cfg = sweep.cell_config(json.loads(base.model_dump_json()), 0.0, 1.0, None, "none", None)
        chash = config_hash(ScenarioConfig.model_validate(cfg))
        _manifest(out, {"x_ref": 500.0, "span": [0.0, 1000.0]})
        _stored_run(out, "baseline", chash, spawn_seeds(base.seed, 2)[0])  # before the record
        before = (out / "MANIFEST.json").read_text()
        _no_pool(monkeypatch)
        monkeypatch.setattr("sys.argv", _sweep_argv(scenario, out, "--scored-end-s", "6600"))
        with pytest.raises(SystemExit, match="refusing to resume"):
            sweep.main()
        assert (out / "MANIFEST.json").read_text() == before  # the manifest is not rewritten

    @pytest.mark.parametrize("end", ["0", "7200.5", "nan"])
    def test_an_out_of_range_scored_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, end: str
    ) -> None:
        scenario = _scenario(tmp_path)
        out = tmp_path / "sweep"
        _no_pool(monkeypatch)
        monkeypatch.setattr("sys.argv", _sweep_argv(scenario, out, "--scored-end-s", end))
        with pytest.raises(SystemExit, match="nothing was simulated"):
            sweep.main()
        assert not out.exists()

    def test_the_bounds(self) -> None:
        cfg = {**CONFIG, "sim": {**CONFIG["sim"], "warmup_s": 600.0}}
        assert sweep.scored_end_problem(cfg, None) is None
        assert sweep.scored_end_problem(cfg, 7200.0) is None  # the run's end itself
        assert sweep.scored_end_problem(cfg, 601.0) is None
        for bad in (600.0, 4.0, 7201.0, math.inf):
            why = sweep.scored_end_problem(cfg, bad)
            assert why is not None and "after the warm-up (600 s)" in why


class TestTuneRefusesBeforeRunning:
    @staticmethod
    def _argv(scenario: Path, out: Path, *extra: str) -> list[str]:
        return [
            "--scenario", str(scenario), "--strategies", "vsl", "--budget", "1",
            "--tuning-seeds", "1", "--eval-seeds", "1", "--x-ref", "500", "--span", "0", "1000",
            "--procs", "1", "--out", str(out), *extra,
        ]  # fmt: skip

    @staticmethod
    def _no_runs(monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(*_a: Any, **_k: Any) -> int:
            raise AssertionError("runs started")

        monkeypatch.setattr(tune, "execute", refuse)

    def test_a_resume_with_another_scored_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from flowstate_core.config import ScenarioConfig

        scenario = _scenario(tmp_path)
        out = tmp_path / "study"
        plan = tune.build_plan(
            ScenarioConfig.from_yaml(scenario), str(scenario), ["vsl"], budget=1, n_tune=1,
            n_eval=1, metrics_args={"x_ref": 500.0, "span": [0.0, 1000.0]}, rho_c_veh_km=None,
        )  # fmt: skip
        troot = out / tune.TUNING_DIR
        _manifest(troot, plan.metrics_args)
        _stored_run(troot, "baseline", plan.hashes["baseline"], plan.tune_seeds[0])
        self._no_runs(monkeypatch)
        with pytest.raises(SystemExit, match="refusing to resume"):
            tune.main(self._argv(scenario, out, "--scored-end-s", "6600"))
        # the evaluation tree is checked before anything runs too
        (troot / "baseline").rename(tmp_path / "moved")
        eroot = out / tune.EVALUATION_DIR
        eroot.mkdir(parents=True)
        (eroot / "MANIFEST.json").write_text(
            json.dumps(
                {
                    "metrics_args": {**plan.metrics_args, "scored_end_s": 6600.0},
                    "cells": {"baseline": plan.hashes["baseline"]},
                    "seeds": plan.eval_seeds,
                }
            )
        )
        _stored_run(eroot, "baseline", plan.hashes["baseline"], plan.eval_seeds[0])
        with pytest.raises(SystemExit, match=r"refusing to resume .*evaluation"):
            tune.main(self._argv(scenario, out))

    def test_an_out_of_range_scored_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scenario = _scenario(tmp_path)
        out = tmp_path / "study"
        self._no_runs(monkeypatch)
        with pytest.raises(SystemExit, match="nothing was simulated"):
            tune.main(self._argv(scenario, out, "--scored-end-s", "9000"))
        assert not out.exists()


def test_the_battery_refuses_an_out_of_range_scored_end_before_simulating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.test_validation.test_validation_corridor_battery_gate import (
        write_observations,
        write_scenario,
    )

    battery = _load_script()

    def refuse(*_a: Any, **_k: Any) -> None:
        raise AssertionError("simulation started")

    monkeypatch.setattr(battery, "run_replicates", refuse)
    scenario = write_scenario(tmp_path / "s.yaml")  # 3600 s, no warm-up
    observations = write_observations(tmp_path / "obs.json")
    out = tmp_path / "runs"
    argv = [
        "--scenario", str(scenario), "--observations", str(observations), "--replicates", "1",
        "--out", str(out), "--artifact", str(tmp_path / "a.json"), "--report-dir",
        str(tmp_path / "rep"), "--scored-end-s", "14400",
    ]  # fmt: skip
    assert battery.main(argv) == 2
    assert "nothing was simulated" in capsys.readouterr().out
    assert not out.exists() and not (tmp_path / "a.json").exists()
