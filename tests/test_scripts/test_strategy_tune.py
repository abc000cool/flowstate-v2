"""The tuning harness ``scripts/strategy_tune.py`` (WP-105; docs/FRISCO_PROTOCOL.md §8.4).

Seed streams disjoint and stable; the same budget for every strategy; the
textbook candidate is the sweep's own cell; the selection rules (collisions
disqualify, a throughput interval entirely below the baseline's is reported
but not selectable, incomplete and unrecorded runs are not selectable, ties go
to the lower index); ``--plan-only`` counts and writes nothing; the analysis of
a synthetic tree (comparison built, refused for a tree without the waiting
measures, refused when the evaluation tree was run for another selection);
and one tiny end-to-end study on the interchange fixture (2 candidates × 1
tuning seed × 1 evaluation seed, 150 sim-s) followed by ``--analyze-only``.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
MERGE_OSM = REPO_ROOT / "tests" / "fixtures" / "merge.osm"


def _load() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "flowstate_strategy_tune", SCRIPTS / "strategy_tune.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tune = _load()
import corridor_sweep  # noqa: E402  (on sys.path once the script is loaded)

RHO_C = 25.0
METRICS_ARGS = {"x_ref": 1800.0, "span": [100.0, 1900.0]}


def merge_config(duration_s: float = 150.0) -> ScenarioConfig:
    """The interchange fixture (golden ``merge_*`` cases), absolute OSM path, unmetered."""
    return ScenarioConfig.model_validate(
        {
            "name": "tune_fixture",
            "network": {
                "kind": "osm",
                "osm_file": str(MERGE_OSM),
                "corridor_edges": ["100", "101", "102", "103"],
                "inflow": [[0.0, 0.6]],
                "ramps": [
                    {
                        "kind": "on",
                        "name": "test on-ramp",
                        "edges": ["200"],
                        "attach_edge": "102",
                        "inflow": [[0.0, 0.25]],
                    }
                ],
            },
            "sim": {"duration_s": duration_s},
            "seed": 3,
        }
    )


def _plan(strategies: tuple[str, ...] = ("alinea", "vsl"), **kw: Any) -> Any:
    args: dict[str, Any] = {
        "budget": 3,
        "n_tune": 2,
        "n_eval": 4,
        "metrics_args": METRICS_ARGS,
        "rho_c_veh_km": RHO_C,
    }
    args.update(kw)
    return tune.build_plan(merge_config(), "scenarios/fixture.yaml", strategies, **args)


class TestSeeds:
    def test_streams_are_disjoint_deterministic_and_prefix_stable(self) -> None:
        t, e = tune.seed_streams(42, 10, 50)
        assert not set(t) & set(e)
        assert e == spawn_seeds(42, 50)  # the sweep's own evaluation seeds
        assert t == tune.tuning_seeds(42, 10)
        assert tune.tuning_seeds(42, 3) == t[:3]
        assert tune.tuning_seeds(43, 3) != t[:3]
        assert all(0 <= s < 2**63 for s in t)

    def test_a_shared_seed_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tune, "tuning_seeds", lambda m, n: spawn_seeds(m, 1) * n)
        with pytest.raises(ValueError, match="not disjoint"):
            tune.seed_streams(42, 2, 5)


class TestCandidates:
    @pytest.mark.parametrize("strategy", sorted(tune.SPACES))
    def test_budget_candidates_textbook_first_inside_the_box(self, strategy: str) -> None:
        space = tune.SPACES[strategy]
        values = tune.candidate_values(space, 6)
        assert len(values) == 6
        assert values[0] == {d.name: d.default for d in space.dims}
        for v in values[1:]:
            for d in space.dims:
                assert d.lo <= v[d.name] <= d.hi
        assert len({tuple(sorted(v.items())) for v in values}) == 6
        assert tune.candidate_values(space, 6) == values  # deterministic
        assert tune.candidate_values(space, 3) == values[:3]  # a smaller budget is a prefix
        for d in space.dims:
            assert d.lo <= d.default <= d.hi and d.source

    def test_every_strategy_gets_the_same_budget(self) -> None:
        plan = _plan(tuple(sorted(tune.SPACES)), budget=4)
        per: dict[str, int] = {}
        for name, g in plan.grid.items():
            if name != tune.BASELINE:
                per[g["tuned_strategy"]] = per.get(g["tuned_strategy"], 0) + 1
        assert per == {s: 4 for s in tune.SPACES}
        assert plan.n_tuning_runs == (1 + 4 * len(tune.SPACES)) * 2
        assert plan.n_evaluation_runs == (1 + len(tune.SPACES)) * 4

    def test_the_textbook_candidate_is_the_sweeps_own_cell(self) -> None:
        plan = _plan(("alinea", "vsl", "follower_stopper"), penetration=0.05)
        base = json.loads(merge_config().model_dump_json())

        def h(cfg: dict[str, Any]) -> str:
            return config_hash(ScenarioConfig.model_validate(cfg))

        sweep = corridor_sweep.cell_config
        assert plan.hashes["alinea_c00"] == h(sweep(base, 0.0, 1.0, None, "alinea", RHO_C))
        assert plan.hashes["vsl_c00"] == h(sweep(base, 0.0, 1.0, None, "vsl", None))
        assert plan.hashes["follower_stopper_c00"] == h(
            sweep(base, 0.05, 1.0, "follower_stopper", "none", None)
        )
        assert plan.hashes[tune.BASELINE] == h(sweep(base, 0.0, 1.0, None, "none", None))
        # other candidates carry their parameters
        meter = plan.configs["alinea_c01"]["network"]["ramps"][0]["meter"]["params"]
        v = plan.grid["alinea_c01"]["values"]
        assert meter["rho_target_veh_km"] == pytest.approx(v["rho_target_factor"] * RHO_C)
        assert meter["k_r_veh_h_per_veh_km"] == v["k_r_veh_h_per_veh_km"]
        vsl = plan.configs["vsl_c01"]["av"]["vsl_params"]
        assert vsl["v_off"] > vsl["v_on"] and vsl["rho_off"] < vsl["rho_on"]
        fs = plan.configs["follower_stopper_c01"]["av"]["controller_params"]
        assert set(fs) == {"dx0_1", "dx0_2", "dx0_3", "d_1", "d_2", "d_3"}
        assert len(set(plan.hashes.values())) == len(plan.hashes)

    @pytest.mark.parametrize(
        ("kw", "message"),
        [
            ({"rho_c_veh_km": None}, "rho-target-veh-km"),
            ({"n_tune": 0}, "at least one tuning"),
        ],
    )
    def test_refusals(self, kw: dict[str, Any], message: str) -> None:
        with pytest.raises(SystemExit, match=message):
            _plan(**kw)

    def test_unknown_strategy_and_zero_penetration_are_refused(self) -> None:
        with pytest.raises(SystemExit, match="no declared search space"):
            _plan(("pi_saturation",))
        with pytest.raises(SystemExit, match="av-penetration"):
            _plan(("follower_stopper",), penetration=0.0)


# --------------------------------------------------------------------------- selection

SEEDS = [1, 2, 3]


def _rec(obj: float, thr: float = 1000.0, coll: int | None = 0, **kw: Any) -> dict[str, Any]:
    return {
        "total_delay_incl_waiting_veh_h": obj,
        "throughput_veh_h": thr,
        "n_collisions": coll,
        **kw,
    }


def _manifest(cells: dict[str, tuple[str, int]], seeds: list[int] = SEEDS) -> dict[str, Any]:
    grid: dict[str, Any] = {tune.BASELINE: {"strategy": "none"}}
    for name, (strategy, j) in cells.items():
        grid[name] = {"tuned_strategy": strategy, "candidate": j, "textbook": j == 0, "values": {}}
    return {
        "seeds": seeds,
        "grid_spec": {"strategies": sorted({s for s, _ in cells.values()})},
        "grid": grid,
        "cells": {n: f"h{n}" for n in grid},
    }


class TestSelection:
    def test_rules(self) -> None:
        cells = {f"alinea_c{j:02d}": ("alinea", j) for j in range(6)}
        base = {s: _rec(20.0, thr=1000.0 + s) for s in SEEDS}
        records = {
            tune.BASELINE: base,
            "alinea_c00": {s: _rec(10.0 + s, thr=1000.0 + s) for s in SEEDS},  # mean 12
            "alinea_c01": {s: _rec(5.0, coll=1 if s == 2 else 0) for s in SEEDS},
            "alinea_c02": {s: _rec(6.0, thr=800.0 + s) for s in SEEDS},  # −200 veh/h every seed
            "alinea_c03": {s: _rec(7.0 + s, thr=1000.0 + 2 * s) for s in SEEDS},  # mean 9
            "alinea_c04": {s: _rec(1.0) for s in SEEDS[:2]},  # a tuning run failed
            "alinea_c05": {s: _rec(2.0, coll=None) for s in SEEDS},  # no collision counter
        }
        out = tune.select(_manifest(cells), records)
        status = {r["cell"]: r["status"] for r in out["candidates"]["alinea"]}
        assert status == {
            "alinea_c00": tune.SELECTABLE,
            "alinea_c01": tune.DISQUALIFIED_COLLISION,
            "alinea_c02": tune.THROUGHPUT_BELOW_BASELINE,
            "alinea_c03": tune.SELECTABLE,
            "alinea_c04": tune.INCOMPLETE,
            "alinea_c05": tune.COLLISIONS_NOT_RECORDED,
        }
        assert out["selected"]["alinea"]["cell"] == "alinea_c03"
        c02 = next(r for r in out["candidates"]["alinea"] if r["cell"] == "alinea_c02")
        assert c02["throughput_vs_baseline"]["hi95"] < 0.0  # reported, not selectable
        assert c02["objective"]["mean"] == pytest.approx(6.0)
        assert out["not_evaluated"] == {}

    def test_ties_go_to_the_lower_index_and_an_undefined_objective_is_not_selectable(self) -> None:
        cells = {"vsl_c00": ("vsl", 0), "vsl_c01": ("vsl", 1), "vsl_c02": ("vsl", 2)}
        records = {
            tune.BASELINE: {s: _rec(9.0) for s in SEEDS},
            "vsl_c00": {s: _rec(4.0) for s in SEEDS},
            "vsl_c01": {s: _rec(4.0) for s in SEEDS},
            "vsl_c02": {s: {"throughput_veh_h": 1000.0, "n_collisions": 0} for s in SEEDS},
        }
        out = tune.select(_manifest(cells), records)
        assert out["selected"]["vsl"]["cell"] == "vsl_c00"
        c02 = out["candidates"]["vsl"][2]
        assert c02["status"] == tune.OBJECTIVE_UNDEFINED

    def test_no_selectable_candidate_is_not_evaluated(self) -> None:
        cells = {"vsl_c00": ("vsl", 0), "vsl_c01": ("vsl", 1)}
        records = {
            tune.BASELINE: {s: _rec(9.0) for s in SEEDS},
            "vsl_c00": {s: _rec(4.0, coll=3) for s in SEEDS},
            "vsl_c01": {s: _rec(4.0, coll=1) for s in SEEDS},
        }
        out = tune.select(_manifest(cells), records)
        assert out["selected"] == {}
        assert "c00 disqualified_collision" in out["not_evaluated"]["vsl"]

    def test_one_tuning_seed_cannot_fire_the_throughput_rule(self) -> None:
        cells = {"vsl_c00": ("vsl", 0)}
        records = {
            tune.BASELINE: {1: _rec(9.0, thr=1000.0)},
            "vsl_c00": {1: _rec(4.0, thr=500.0)},
        }
        out = tune.select(_manifest(cells, [1]), records)
        row = out["candidates"]["vsl"][0]
        assert row["throughput_rule_evaluable"] is False and row["status"] == tune.SELECTABLE

    def test_an_incomplete_baseline_is_refused(self) -> None:
        cells = {"vsl_c00": ("vsl", 0)}
        with pytest.raises(SystemExit, match="baseline lacks tuning runs"):
            tune.select(_manifest(cells), {tune.BASELINE: {1: _rec(1.0)}})


# --------------------------------------------------------------------------- CLI and analysis


def _scenario_yaml(tmp_path: Path, duration_s: float = 150.0) -> Path:
    path = tmp_path / "fixture.yaml"
    path.write_text(yaml.safe_dump(merge_config(duration_s).model_dump(mode="json")))
    return path


def _common(scenario: Path, out: Path) -> list[str]:
    return [
        "--scenario",
        str(scenario),
        "--rho-target-veh-km",
        str(RHO_C),
        "--x-ref",
        "1800",
        "--span",
        "100",
        "1900",
        "--out",
        str(out),
    ]


def test_plan_only_counts_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "study"
    argv = [
        *_common(_scenario_yaml(tmp_path), out),
        *["--strategies", "alinea", "vsl", "--budget", "2", "--tuning-seeds", "2"],
        *["--eval-seeds", "4", "--plan-only"],
    ]
    assert tune.main(argv) == 0
    text = capsys.readouterr().out
    assert "tuning runs: 5 cells × 2 seeds = 10" in text
    assert "evaluation runs: 3 cells × 4 seeds = 12" in text
    assert "total runs: 22" in text
    assert "UNDERPOWERED" in text
    assert not out.exists()


FULL = {
    "throughput_veh_h": 1000.0,
    "mean_tt_incl_waiting_s": 90.0,
    "p90_tt_incl_waiting_s": 120.0,
    "total_delay_incl_waiting_veh_h": 2.0,
    "sigma_v_temporal_ms": 1.0,
    "sigma_v_spatial_ms": 1.0,
    "wave_count": 0,
    "wave_amplitude_ms": None,
    "fuel_ml_per_veh_km": 70.0,
}


def _fake_tree(root: Path, manifest: dict[str, Any], value: Any) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "MANIFEST.json").write_text(json.dumps(manifest))
    for cell, chash in manifest["cells"].items():
        for seed in manifest["seeds"]:
            d = root / cell / chash / str(seed)
            d.mkdir(parents=True, exist_ok=True)
            rec = value(cell, int(seed))
            (d / "metrics.json").write_text(json.dumps(rec))
            (d / "meta.json").write_text(json.dumps({"n_collisions": 0}))


def _metrics(cell: str, seed: int, *, full: bool = True) -> dict[str, Any]:
    """Every measure; total delay lowest for candidate 0 (``_best`` cells count as 0)."""
    rec = {k: v for k, v in FULL.items() if v is not None}
    rec["wave_amplitude_ms"] = float("nan")
    j = 0 if cell.endswith("_best") or cell == tune.BASELINE else int(cell[-2:])
    base = 5.0 if cell == tune.BASELINE else 2.0 + j
    rec["total_delay_incl_waiting_veh_h"] = base + (seed % 7) * 0.5
    if not full:
        for k in (
            "mean_tt_incl_waiting_s",
            "p90_tt_incl_waiting_s",
            "total_delay_incl_waiting_veh_h",
        ):
            rec.pop(k)
    return rec


def _synthetic_study(out: Path, *, full_eval: bool = True) -> Any:
    plan = _plan(("alinea", "vsl"), budget=2, n_tune=2, n_eval=3)
    tm = tune.tuning_manifest(plan, out)
    _fake_tree(out / tune.TUNING_DIR, tm, _metrics)
    sel = tune.select(
        tm, corridor_sweep.run_records(out / tune.TUNING_DIR, tm["cells"], plan.tune_seeds)
    )
    em, _ = tune.evaluation_manifest(tm, out, sel["selected"], plan.configs)
    _fake_tree(out / tune.EVALUATION_DIR, em, lambda c, s: _metrics(c, s, full=full_eval))
    return plan


class TestAnalysis:
    def test_selection_and_comparison_from_a_tree(self, tmp_path: Path) -> None:
        out = tmp_path / "study"
        _synthetic_study(out)
        s = tune.analyze_study(out, out / "strategy_tune.json", [])
        assert s["schema"] == tune.SCHEMA and s["seeds"]["disjoint"] is True
        assert {k: v["cell"] for k, v in s["selected"].items()} == {
            "alinea": "alinea_c00",
            "vsl": "vsl_c00",
        }
        ev = s["evaluation"]
        assert ev["comparison_refused"] is None and ev["underpowered"] is True
        assert set(ev["comparison"]["arms"]) == {"baseline", "alinea_best", "vsl_best"}
        assert (out / "comparison.json").is_file() and (out / "comparison.md").is_file()
        md = (out / "strategy_tune.md").read_text()
        assert "**selected**" in md and "model estimate" in md
        assert s["provenance"]["code_dirty"] in (True, False, None)
        assert json.loads((out / "strategy_tune.json").read_text()) == s

    def test_a_tree_without_the_waiting_measures_is_refused(self, tmp_path: Path) -> None:
        out = tmp_path / "study"
        _synthetic_study(out, full_eval=False)
        assert tune.main(["--analyze-only", "--out", str(out)]) == 1
        s = json.loads((out / "strategy_tune.json").read_text())
        assert "total_delay_incl_waiting_veh_h" in s["evaluation"]["comparison_refused"]
        assert s["evaluation"]["comparison"] is None

    def test_an_evaluation_run_for_another_selection_is_refused(self, tmp_path: Path) -> None:
        out = tmp_path / "study"
        _synthetic_study(out)
        path = out / tune.EVALUATION_DIR / "MANIFEST.json"
        em = json.loads(path.read_text())
        em["selection"]["alinea"]["config_hash"] = "000000000000"
        path.write_text(json.dumps(em))
        with pytest.raises(SystemExit, match=r"now selects"):
            tune.analyze_study(out, out / "s.json", [])


def test_code_paths_cover_the_script_and_the_worker() -> None:
    assert "scripts/strategy_tune.py" in tune.CODE_PATHS
    assert "scripts/corridor_sweep.py" in tune.CODE_PATHS
    assert "packages/microsim" in tune.CODE_PATHS


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_rewritten_artifact_is_not_dirty_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for rel in ("artifacts/a.json", "scripts/strategy_tune.py"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("0\n")
    for args in (
        ["init", "-q"],
        ["add", "."],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.setattr(tune, "REPO_ROOT", tmp_path)
    assert tune.git_dirty() is False
    (tmp_path / "artifacts/a.json").write_text("1\n")
    assert tune.git_dirty() is False
    (tmp_path / "scripts/strategy_tune.py").write_text("1\n")
    assert tune.git_dirty() is True


@pytest.mark.integration
def test_tiny_end_to_end_then_analyze_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """2 candidates × 1 tuning seed × 1 evaluation seed of ALINEA, 150 sim-s, inline."""
    out = tmp_path / "study"
    argv = [
        *_common(_scenario_yaml(tmp_path), out),
        *["--strategies", "alinea", "--budget", "2", "--tuning-seeds", "1"],
        *["--eval-seeds", "1", "--procs", "1"],
    ]
    assert tune.main(argv) == 0
    s = json.loads((out / "strategy_tune.json").read_text())
    assert s["run_counts"] == {"tuning": 3, "evaluation": 2}
    assert [r["status"] for r in s["candidates"]["alinea"]] == [tune.SELECTABLE] * 2
    assert s["tuning_throughput_rule_evaluable"] is False
    ev = s["evaluation"]
    assert ev["comparison_refused"] is None
    assert set(ev["comparison"]["arms"]) == {"baseline", "alinea_best"}
    # every run went through the sweep's worker: waiting measures stored, ledger kept
    runs = sorted(out.glob("*/*/*/*/metrics.json"))
    assert len(runs) == 5
    for p in runs:
        m = json.loads(p.read_text())
        assert "total_delay_incl_waiting_veh_h" in m and "throughput_veh_h" in m
        assert (p.parent / "journeys.parquet").is_file()
        assert not (p.parent / "trajectories.parquet").exists()
    before = (out / "strategy_tune.json").read_text()
    capsys.readouterr()
    assert tune.main(["--analyze-only", "--out", str(out)]) == 0
    again = json.loads((out / "strategy_tune.json").read_text())
    for key in ("candidates", "selected", "evaluation", "seeds", "run_counts"):
        assert again[key] == json.loads(before)[key]
    assert "selected alinea" in capsys.readouterr().out
