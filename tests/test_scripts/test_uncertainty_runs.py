"""scripts/uncertainty_runs.py (WP-106): the CLI around validation.uncertainty.

Arm parsing and its refusals, ``--plan-only`` (run count and simulated-time
cost, nothing written), the design written for a tiny generated corridor and
refused when the inputs change, the analysis of a faked run tree (the layout
``<out>/<sample>/<arm>/<config hash>/<seed>/metrics.json`` the sweep worker
writes), the ``code_dirty`` scope, and one real smoke run (2 samples × 1 seed ×
2 arms of a 60-s, 600-m corridor; ``integration``) that is then re-analysed
and resumed with nothing pending. ``--transfer-check`` (WP-106b) on a report
that ``calibration.transfer_check`` writes for synthetic detector data: the
plan's driver ranges and their basis, the design recording the file only when
given, and clean refusals.
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

import numpy as np
import pytest
import yaml

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.strategies import on_ramps
from validation import uncertainty as unc

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "flowstate_wp106_uncertainty_runs", SCRIPTS / "uncertainty_runs.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ur = _load()


@pytest.fixture
def scenario(tmp_path: Path) -> Path:
    """A 600-m single-lane corridor, 60 s, on a synthetic artifact population."""
    pop = tmp_path / "idm_synthetic.json"
    IDMCalibration(
        created_at="2026-10-04T00:00:00Z",
        source="synthetic",
        data_hash="0" * 64,
        mean={"v0": 30.0, "T": 1.4, "a_max": 1.0, "b": 1.7, "s0": 2.0},
        cov=np.diag([4.0**2, 0.4**2, 0.3**2, 0.5**2, 0.5**2]).tolist(),
        n_episodes_fit=50,
        n_episodes_holdout=20,
        holdout_gap_rmse_m=4.0,
    ).save(pop)
    path = tmp_path / "tiny.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "tiny_uncertainty",
                "network": {
                    "kind": "corridor",
                    "length_m": 600.0,
                    "lanes": 1,
                    "inflow": [[0.0, 0.3]],
                },
                "fleet": {"model": "IDM", "idm_calibration": str(pop)},
                "sim": {"duration_s": 60.0, "warmup_s": 0.0},
                "seed": 7,
            }
        )
    )
    return path


ARGS = ["--x-ref", "300", "--span", "100", "500"]


# --- arms ---------------------------------------------------------------------------------------


def test_parse_arm_builds_sweep_cells() -> None:
    arm = ur.parse_arm(["fs10", "controller=follower_stopper", "penetration=0.10"])
    assert (arm.controller, arm.penetration, arm.compliance, arm.strategy) == (
        "follower_stopper",
        0.10,
        1.0,
        "none",
    )
    alinea = ur.parse_arm(["meter", "strategy=alinea", "rho_target_veh_km=19.9"])
    assert alinea.rho_target_veh_km == 19.9


@pytest.mark.parametrize(
    ("tokens", "match"),
    [
        (["baseline", "strategy=vsl"], "do-nothing"),
        (["x y", "strategy=vsl"], "letters"),
        (["a", "colour=red"], "key=value"),
        (["a", "controller=follower_stopper"], "penetration > 0"),
        (["a", "penetration=0.1"], "needs a controller"),
        (["a", "strategy=alinea"], "rho_target_veh_km"),
        (["a", "strategy=bogus"], "not in"),
        (["a"], "identical to the baseline"),
    ],
)
def test_parse_arm_refusals(tokens: list[str], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        ur.parse_arm(tokens)


def test_an_override_may_touch_av_and_the_tuned_meter_gain_only(tmp_path: Path) -> None:
    good = tmp_path / "fs_best.yaml"
    good.write_text(yaml.safe_dump({"av": {"controller_params": {"U": 25.0}}}))
    arm = ur.parse_arm(["fs", "controller=follower_stopper", "penetration=0.1", f"override={good}"])
    cfg = arm.config({"av": {"penetration": 0.0}, "network": {}})
    assert cfg["av"]["controller_params"] == {"U": 25.0}
    assert arm.override_sha256 is not None
    assert "override_meter" not in arm.to_dict()  # a design without one keeps its key
    fs = ["fs", "controller=follower_stopper", "penetration=0.1"]
    alinea = ["m", "strategy=alinea", "rho_target_veh_km=19.9"]
    cases: list[tuple[list[str], Any, str]] = [
        (fs, {"av": {}, "fleet": {"T": 1.0}}, "av and/or meter_params only"),
        (fs, {}, "av and/or meter_params only"),
        (fs, {"av": [1, 2]}, "'av' must be a mapping"),
        (fs, {"meter_params": {"k_r_veh_h_per_veh_km": 75.0}}, "places none"),
        (alinea, {"meter_params": {}}, "non-empty mapping"),
        (alinea, {"meter_params": {"rate_min_veh_h": 100.0}}, "not allowed"),
        (alinea, {"meter_params": {"rho_target_veh_km": 18.0}}, "arm's rho_target_veh_km"),
        (alinea, {"meter_params": {"k_r_veh_h_per_veh_km": "75"}}, "must be a number"),
        (alinea, {"meter_params": {"k_r_veh_h_per_veh_km": True}}, "must be a number"),
        (alinea, {"meter_params": {"k_r_veh_h_per_veh_km": -5.0}}, "positive and finite"),
        (alinea, {"meter_params": {"k_r_veh_h_per_veh_km": float("inf")}}, "positive and finite"),
    ]
    for i, (tokens, raw, match) in enumerate(cases):
        bad = tmp_path / f"bad{i}.yaml"
        bad.write_text(yaml.safe_dump(raw))
        with pytest.raises(ValueError, match=match):
            ur.parse_arm([*tokens, f"override={bad}"])


def _strategy_tune() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "flowstate_wp105_strategy_tune_for_unc", SCRIPTS / "strategy_tune.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_meter_whitelist_is_what_strategy_tune_tunes() -> None:
    st = _strategy_tune()
    tuned = {d.name for d in st._ALINEA_DIMS} - {"rho_target_factor"}  # the target: an arm key
    assert set(ur.METER_OVERRIDE_PARAMS) == tuned


RHO_C = 19.9


@pytest.fixture
def ramp_scenario(scenario: Path, tmp_path: Path) -> Path:
    """The tiny scenario's population on a map corridor with two on-ramps (meters need
    ramps; the map file is never read: nothing is simulated)."""
    raw = yaml.safe_load(scenario.read_text())
    raw["name"] = "tiny_ramps"
    raw["network"] = {
        "kind": "osm",
        "osm_file": "x.osm",
        "corridor_edges": ["a", "b", "c"],
        "inflow": [[0.0, 0.5]],
        "ramps": [
            {"kind": "on", "edges": ["r1"], "attach_edge": "a", "inflow": [[0.0, 0.1]]},
            {"kind": "on", "edges": ["r2"], "attach_edge": "b", "inflow": [[0.0, 0.1]]},
            {"kind": "off", "edges": ["r3"], "attach_edge": "c", "exit_fraction": [[0.0, 0.2]]},
        ],
    }
    path = tmp_path / "tiny_ramps.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def _best_override(st: ModuleType, strategy: str, values: dict[str, float], path: Path) -> Path:
    """The override file of a tuned candidate (module docstring of uncertainty_runs.py)."""
    space = st.SPACES[strategy]
    raw: dict[str, Any] = {}
    if "vsl" in space.infra:
        raw["av"] = {"vsl_params": st._vsl_params(values)}
    if space.vehicle_controller == "follower_stopper":
        raw["av"] = {
            "controller_params": {
                **{k: v * float(values["dx0_scale"]) for k, v in st._FS_DX0.items()},
                **{k: v * float(values["d_scale"]) for k, v in st._FS_D.items()},
            }
        }
    if "alinea" in space.infra:
        raw["meter_params"] = {"k_r_veh_h_per_veh_km": float(values["k_r_veh_h_per_veh_km"])}
    path.write_text(yaml.safe_dump(raw))
    return path


def _best_tokens(st: ModuleType, strategy: str, values: dict[str, float], override: Path):
    space = st.SPACES[strategy]
    tokens = [strategy, f"strategy={space.infra}", f"override={override}"]
    if "alinea" in space.infra:
        tokens.append(f"rho_target_veh_km={round(values['rho_target_factor'] * RHO_C, 4)!r}")
    if space.vehicle_controller:
        tokens += [f"controller={space.vehicle_controller}", "penetration=0.1"]
    return tokens


def test_a_tuned_best_is_the_tuning_candidate_config_hash_and_all(
    ramp_scenario: Path, tmp_path: Path
) -> None:
    """Review 2026-10-07 (major): the tuned ALINEA gain could not be passed, so §8.5 re-ran
    'alinea' at the textbook gain. Every tuned setting of every strategy now reproduces
    strategy_tune.candidate_config exactly."""
    st = _strategy_tune()
    base = json.loads(ScenarioConfig.from_yaml(ramp_scenario).model_dump_json())
    for strategy in ("alinea", "vsl", "vsl+alinea", "follower_stopper"):
        values = st.candidate_values(st.SPACES[strategy], 6)[3]  # not the textbook candidate
        cand = st.candidate_config(
            base,
            st.SPACES[strategy],
            values,
            textbook=False,
            rho_c_veh_km=RHO_C,
            penetration=0.1,
            compliance=1.0,
        )
        override = _best_override(st, strategy, values, tmp_path / f"{strategy}_best.yaml")
        arm = ur.parse_arm(_best_tokens(st, strategy, values, override))
        got = arm.config(base)
        assert config_hash(ScenarioConfig.model_validate(got)) == config_hash(
            ScenarioConfig.model_validate(cand)
        ), strategy
        if "alinea" in strategy:
            gains = {r["meter"]["params"]["k_r_veh_h_per_veh_km"] for r in on_ramps(got)}
            assert gains == {values["k_r_veh_h_per_veh_km"]} != {50.0}
            assert arm.meter_gain() == values["k_r_veh_h_per_veh_km"]
            assert arm.to_dict()["override_meter"] == {
                "k_r_veh_h_per_veh_km": values["k_r_veh_h_per_veh_km"]
            }


def test_the_uncertainty_run_executes_the_tuned_meter_setting(
    ramp_scenario: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The design, the payloads handed to the sweep worker and the analysis, with the
    worker faked (nothing is simulated): every ALINEA run of every sample carries the
    tuned gain and target on every metered ramp; the baseline carries no meter."""
    best = tmp_path / "alinea_best.yaml"
    best.write_text(yaml.safe_dump({"meter_params": {"k_r_veh_h_per_veh_km": 75.0}}))
    seen: list[tuple[str, dict[str, Any]]] = []

    def worker(payload: tuple[Any, ...]) -> tuple[str, int, bool, str]:
        cell, cfg_json, seed, _metrics, root, _keep = payload
        seen.append((cell, cfg_json))
        chash = config_hash(ScenarioConfig.model_validate(cfg_json))
        run = Path(root) / cell / chash / str(seed)
        run.mkdir(parents=True, exist_ok=True)
        (run / "metrics.json").write_text(json.dumps({"mean_tt_s": 100.0}))
        (run / "meta.json").write_text(json.dumps({"n_collisions": 0}))
        return cell, seed, True, ""

    monkeypatch.setattr(ur, "_worker", worker)
    out = tmp_path / "run"
    rc = ur.main(
        [
            *("--scenario", str(ramp_scenario), "--samples", "2", "--seeds", "2", "--procs", "1"),
            *("--arm", "alinea", "strategy=alinea", "rho_target_veh_km=18.905"),
            f"override={best}",
            *("--arm", "alinea_textbook", "strategy=alinea", f"rho_target_veh_km={RHO_C}"),
            *ARGS,
            *("--out", str(out)),
        ]
    )
    text = capsys.readouterr().out
    assert rc == 0 and "12 runs; 12 pending" in text
    assert (
        f"arm alinea: ALINEA target 18.905 veh/km, gain 75 veh/h per veh/km (meter_params of {best})"
        in text
    )
    assert (
        "arm alinea_textbook: ALINEA target 19.9 veh/km, gain 50 veh/h per veh/km (the textbook"
        in text
    )
    by_arm: dict[str, list[dict[str, Any]]] = {}
    for cell, cfg in seen:
        by_arm.setdefault(cell.split("/")[1], []).append(cfg)
    assert {k: len(v) for k, v in by_arm.items()} == {
        "baseline": 4,
        "alinea": 4,
        "alinea_textbook": 4,
    }
    for cfg in by_arm["alinea"]:
        assert [r["meter"]["params"] for r in on_ramps(cfg)] == [
            {"rho_target_veh_km": 18.905, "k_r_veh_h_per_veh_km": 75.0}
        ] * 2
    for cfg in by_arm["alinea_textbook"]:
        assert [r["meter"]["params"] for r in on_ramps(cfg)] == [{"rho_target_veh_km": RHO_C}] * 2
    assert all(r["meter"] is None for cfg in by_arm["baseline"] for r in on_ramps(cfg))
    design = json.loads((out / "DESIGN.json").read_text())
    arms = {a["name"]: a for a in design["arms"]}
    assert arms["alinea"]["override_meter"] == {"k_r_veh_h_per_veh_km": 75.0}
    assert "override_meter" not in arms["alinea_textbook"]
    doc = json.loads((out / "uncertainty.json").read_text())
    assert doc["n_runs"] == 12 and doc["arms"] == ["baseline", "alinea", "alinea_textbook"]
    assert doc["provenance"]["arms"][0]["override_meter"] == {"k_r_veh_h_per_veh_km": 75.0}


# --- plan, design ----------------------------------------------------------------------------------


def test_plan_only_prints_the_count_and_cost_and_writes_nothing(
    scenario: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "plan"
    rc = ur.main(
        [
            *("--scenario", str(scenario), "--arm", "vsl", "strategy=vsl", "--samples", "4"),
            *("--seeds", "2", "--plan-only", "--out", str(out)),
        ]
    )
    text = capsys.readouterr().out
    assert rc == 0 and not out.exists()
    assert "plan: 4 samples × 2 seeds × 2 arms (baseline + vsl) = 16 runs" in text
    assert "cost: 16 runs × 60 s simulated = 0.3 simulated hours" in text
    assert "below the docs/FRISCO_PROTOCOL.md §8.5 minimum" in text
    assert "s03: demand_scale=" in text


def test_defaults_meet_the_protocol(scenario: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ur.main(["--scenario", str(scenario), "--plan-only", "--out", "unused"])
    text = capsys.readouterr().out
    assert "10 samples × 5 seeds × 1 arms (baseline) = 50 runs" in text
    assert "§8.5 minimum" not in text


def _design(scenario: Path, out: Path, *extra: str) -> tuple[dict[str, Any], list[Any]]:
    args = ur.build_parser().parse_args(
        [
            *("--scenario", str(scenario), "--out", str(out), "--samples", "3", "--seeds", "2"),
            *("--arm", "vsl", "strategy=vsl", *ARGS, *extra),
        ]
    )
    base = ScenarioConfig.from_yaml(scenario)
    arms = [ur.parse_arm(t) for t in args.arm]
    return ur.make_design(args, base, ur.build_space(args, base), arms), arms


def test_the_design_records_samples_and_refuses_other_inputs(
    scenario: Path, tmp_path: Path
) -> None:
    out = tmp_path / "unc"
    design, arms = _design(scenario, out)
    assert [r["sample_id"] for r in design["samples"]] == ["s00", "s01", "s02"]
    assert all(len(r["seeds"]) == 2 for r in design["samples"])
    assert len({r["config_hash"] for r in design["samples"]}) == 3
    for row in design["samples"]:
        assert (out / row["sample_id"] / "scenario.yaml").is_file()
        assert Path(row["idm_calibration"]).parent == out / "populations"
        assert set(row["arms"]) == {"baseline", "vsl"}
    assert json.loads((out / "DESIGN.json").read_text())["design_key"] == design["design_key"]
    again, _ = _design(scenario, out)  # same inputs: accepted, identical hashes
    assert again["samples"] == design["samples"]
    assert again["provenance"] == design["provenance"]
    total, pending = ur.pending_runs(out, design, arms, False)
    assert (total, len(pending)) == (12, 12)
    with pytest.raises(SystemExit, match="different design"):
        _design(scenario, out, "--seed", "99")


def test_analysis_of_a_faked_tree(scenario: Path, tmp_path: Path) -> None:
    out = tmp_path / "unc"
    design, _arms = _design(scenario, out)
    for i, row in enumerate(design["samples"]):
        for arm, chash in row["arms"].items():
            for j, seed in enumerate(row["seeds"]):
                if (i, arm, j) == (2, "vsl", 1):
                    continue  # one run missing
                d = out / row["sample_id"] / arm / chash / str(seed)
                d.mkdir(parents=True)
                tt = 100.0 + 10.0 * i + j - (4.0 if arm == "vsl" else 0.0)
                (d / "metrics.json").write_text(
                    json.dumps({"mean_tt_s": tt, "throughput_veh_h": 1000.0, "wave_count": 0})
                )
                (d / "meta.json").write_text(json.dumps({"n_collisions": 0}))
    summary = tmp_path / "artifacts" / "uncertainty_tiny.json"
    result = ur.analyze(out, summary, None)
    doc = json.loads((out / "uncertainty.json").read_text())
    assert summary.is_file() and summary.with_suffix(".md").is_file()
    assert (out / "uncertainty.md").read_text().startswith("# Uncertainty")
    eff = doc["effects"]["vsl"]["mean_tt_s"]
    assert eff["effect"]["mean"] == pytest.approx(-4.0)
    assert (eff["n_same_sign"], eff["n_samples_design"], eff["verdict"]) == (3, 3, "robust")
    assert doc["missing_runs"] == [
        {"sample_id": "s02", "arm": "vsl", "seed": design["samples"][2]["seeds"][1]}
    ]
    assert doc["zero_collisions"] is True
    assert doc["provenance"]["design_key"] == design["design_key"]
    assert "code_dirty" in doc["provenance"] and "code" in doc["provenance"]
    assert doc["metrics"][:2] == ["throughput_veh_h", "mean_tt_s"]  # the sweep's FIELDS order
    assert result.meets_protocol_minimum is False


def test_an_unknown_headline_is_a_clean_refusal(scenario: Path, tmp_path: Path) -> None:
    out = tmp_path / "unc"
    design, _arms = _design(scenario, out)
    row = design["samples"][0]
    d = out / row["sample_id"] / "baseline" / row["arms"]["baseline"] / str(row["seeds"][0])
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps({"mean_tt_s": 100.0}))
    with pytest.raises(SystemExit, match="analysis refused"):
        ur.main(["--analyze-only", "--out", str(out), "--headline", "total_delay"])


def test_analyze_only_without_a_design_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="nothing to analyse"):
        ur.main(["--analyze-only", "--out", str(tmp_path / "none")])


def test_running_needs_the_metric_window(scenario: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--x-ref and --span"):
        ur.main(["--scenario", str(scenario), "--out", str(tmp_path / "x"), "--samples", "2"])


# --- provenance --------------------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_code_dirty_counts_code_paths_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for rel in ("artifacts/a.json", "scripts/uncertainty_runs.py", "packages/validation/p.py"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("0\n")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    monkeypatch.setattr(ur, "REPO_ROOT", tmp_path)
    assert ur.git_dirty() is False
    (tmp_path / "artifacts/a.json").write_text("1\n")
    assert ur.git_dirty() is False
    (tmp_path / "packages/validation/p.py").write_text("1\n")
    assert ur.git_dirty() is True


# --- a real smoke run ----------------------------------------------------------------------------------


@pytest.mark.integration
def test_smoke_run_then_analyze_then_resume(
    scenario: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "smoke"
    common = ["--scenario", str(scenario), "--out", str(out), "--samples", "2", "--seeds", "1"]
    common += ["--arm", "fs", "controller=follower_stopper", "penetration=0.2", *ARGS]
    assert ur.main([*common, "--procs", "1"]) == 0
    text = capsys.readouterr().out
    assert "4 runs; 4 pending" in text and "0 failed" in text
    doc = json.loads((out / "uncertainty.json").read_text())
    assert doc["n_runs"] == 4 and doc["missing_runs"] == []
    assert doc["arms"] == ["baseline", "fs"]
    tt = doc["summaries"]["baseline"]["mean_tt_s"]
    assert tt["n_samples"] == 2 and tt["mean"] is not None
    assert doc["effects"]["fs"]["throughput_veh_h"]["n_samples_design"] == 2
    for row in json.loads((out / "DESIGN.json").read_text())["samples"]:
        for arm, chash in row["arms"].items():
            run = out / row["sample_id"] / arm / chash / str(row["seeds"][0])
            assert (run / "metrics.json").is_file() and (run / "meta.json").is_file()
            assert not (run / "trajectories.parquet").exists()  # dropped, as the sweep does
    assert ur.main(["--analyze-only", "--out", str(out)]) == 0
    assert ur.main([*common, "--procs", "1"]) == 0
    assert "4 runs; 0 pending" in capsys.readouterr().out


# --- driver ranges from the corridor's transfer check (WP-106b) ------------------------------------


def _syn() -> ModuleType:
    path = REPO_ROOT / "tests" / "test_calibration" / "synthetic_transfer.py"
    spec = importlib.util.spec_from_file_location("flowstate_wp106b_syn", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def transfer_json(scenario: Path, tmp_path: Path) -> Path:
    """The transfer check of the scenario's own population on a synthetic corridor (drivers
    slower and capacity lower than the population). Free-flow speed reads an observed
    interval; capacity, without a simulated capacity sidecar, may come off the analytical
    index, a basis protocol §8.5 replaces by the measured range, labelled assumed — the
    tests follow whichever basis the check wrote."""
    from calibration.transfer_check import check_transfer, observe, population_from_scenario

    syn = _syn()
    observed = observe(
        syn.corridor_frame(ff_speed=27.0, capacity=1650.0, dates=syn.DATES[:3]),
        stations=syn.stations_table(),
        n_bootstrap=100,
    )
    report = check_transfer(observed, population_from_scenario(scenario), sidecars=[], n_draws=1000)
    path = tmp_path / "transfer_check.json"
    path.write_text(report.to_json() + "\n")
    return path


def test_plan_only_with_a_transfer_check(
    scenario: Path, transfer_json: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "plan"
    rc = ur.main(
        [
            *("--scenario", str(scenario), "--samples", "4", "--seeds", "2", "--plan-only"),
            *("--transfer-check", str(transfer_json), "--out", str(out)),
        ]
    )
    text = capsys.readouterr().out
    assert rc == 0 and not out.exists()
    doc = json.loads(transfer_json.read_text())
    ranges = {c["quantity"]: c.get("uncertainty_range") for c in doc["comparisons"]}
    assert ranges["free_flow_speed"]["basis"] == "observed_interval"
    for kind, quantity in (("t_scale", "capacity_per_lane"), ("v0_scale", "free_flow_speed")):
        entry = ranges[quantity]
        line = next(ln for ln in text.splitlines() if ln.startswith(f"  {kind}: "))
        assert f"{transfer_json} (sha256 " in line
        if entry["basis"] == "observed_interval":
            # the same population: the factors carry over unchanged (JSON's 4 decimals)
            assert line.startswith(f"  {kind}: {entry['low']:.4g} – {entry['high']:.4g} ")
            assert "[observed 95 % interval (transfer check)]" in line and "(assumed)" not in line
        else:
            # any other basis (a fallback, the analytical index) is the measured range, assumed
            assert "[§7.2 measured range; the transfer check gave no interval] (assumed)" in line
            assert f"its range basis is {entry['basis']}" in line
    assert "driver ranges: the wide §7.2 measured range" not in text
    assert "s03: demand_scale=" in text
    # without it the wide range is said, and flagged
    ur.main(["--scenario", str(scenario), "--plan-only", "--out", str(out)])
    text = capsys.readouterr().out
    assert "[§7.2 measured range; no transfer check given] (assumed)" in text
    assert "driver ranges: the wide §7.2 measured range, flagged assumed" in text


def test_the_design_records_the_transfer_check_only_when_given(
    scenario: Path, transfer_json: Path, tmp_path: Path
) -> None:
    plain, _ = _design(scenario, tmp_path / "plain")
    assert "transfer_check" not in plain
    design, _ = _design(scenario, tmp_path / "tc", "--transfer-check", str(transfer_json))
    record = {"path": str(transfer_json), "sha256": ur.file_sha256(transfer_json)}
    assert design["transfer_check"] == record
    assert design["design_key"] != plain["design_key"]
    stored = json.loads((tmp_path / "tc" / "DESIGN.json").read_text())
    assert stored["transfer_check"] == record
    space = {p["kind"]: p for p in stored["space"]["parameters"]}
    assert space["v0_scale"]["basis"] == "observed_interval" and not space["v0_scale"]["assumed"]
    capacity = next(c for c in json.loads(transfer_json.read_text())["comparisons"]
                    if c["quantity"] == "capacity_per_lane")["uncertainty_range"]  # fmt: skip
    if capacity["basis"] != "observed_interval":
        assert (
            space["t_scale"]["basis"] == "measured_range_fallback" and space["t_scale"]["assumed"]
        )
    # the analysis carries the record into its provenance
    row = design["samples"][0]
    run = tmp_path / "tc" / row["sample_id"] / "baseline" / row["arms"]["baseline"]
    (run / str(row["seeds"][0])).mkdir(parents=True)
    (run / str(row["seeds"][0]) / "metrics.json").write_text(json.dumps({"mean_tt_s": 100.0}))
    ur.analyze(tmp_path / "tc", None, None)
    doc = json.loads((tmp_path / "tc" / "uncertainty.json").read_text())
    assert doc["provenance"]["transfer_check"] == record
    assert (
        "observed 95 % interval (transfer check)"
        in (tmp_path / "tc" / "uncertainty.md").read_text()
    )


def test_a_wrong_transfer_check_is_a_clean_refusal(
    scenario: Path, transfer_json: Path, tmp_path: Path
) -> None:
    common = ["--scenario", str(scenario), "--plan-only", "--out", str(tmp_path / "x")]
    with pytest.raises(SystemExit, match="not found"):
        ur.main([*common, "--transfer-check", str(tmp_path / "none.json")])
    doc = json.loads(transfer_json.read_text())
    doc["model"]["model"] = "EIDM"
    other = tmp_path / "eidm_transfer_check.json"
    other.write_text(json.dumps(doc))
    with pytest.raises(SystemExit, match="EIDM"):
        ur.main([*common, "--transfer-check", str(other)])


# --- the demand range from the study's data-quality artifact (protocol §8.5) ----------------


def _data_quality(path: Path, count_error: float = 0.08) -> Path:
    path.write_text(
        json.dumps(
            {"schema": "flowstate.data_quality/1", "parameters": {"count_error": count_error}}
        )
    )
    return path


def test_the_demand_range_reads_the_data_quality_count_error(
    scenario: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dq = _data_quality(tmp_path / "data_quality.json")
    common = ["--scenario", str(scenario), "--plan-only", "--out", str(tmp_path / "p")]
    assert ur.main([*common, "--data-quality", str(dq)]) == 0
    text = capsys.readouterr().out
    line = next(ln for ln in text.splitlines() if ln.startswith("  demand_scale: "))
    assert line.startswith("  demand_scale: 0.92 – 1.08 [count error recorded in the data-quality")
    assert "(assumed)" not in line and f"{dq} (sha256 " in line
    assert "demand range: the ±5 % default" not in text
    assert ur.main(common) == 0
    text = capsys.readouterr().out
    line = next(ln for ln in text.splitlines() if ln.startswith("  demand_scale: "))
    assert "0.95 – 1.05 [assumed detector count error (no data-quality artifact given)]" in line
    assert "(assumed)" in line and "demand range: the ±5 % default count error" in text


def test_the_design_records_the_data_quality_artifact_only_when_given(
    scenario: Path, tmp_path: Path
) -> None:
    plain, _ = _design(scenario, tmp_path / "plain")
    assert "data_quality" not in plain
    dq = _data_quality(tmp_path / "data_quality.json")
    design, _ = _design(scenario, tmp_path / "dq", "--data-quality", str(dq))
    assert design["data_quality"] == {
        "path": str(dq),
        "sha256": ur.file_sha256(dq),
        "count_error": 0.08,
    }
    assert design["design_key"] != plain["design_key"]
    demand = next(p for p in design["space"]["parameters"] if p["kind"] == "demand_scale")
    assert demand["basis"] == "data_quality_count_error" and not demand["assumed"]


def test_a_wrong_data_quality_file_is_a_clean_refusal(scenario: Path, tmp_path: Path) -> None:
    common = ["--scenario", str(scenario), "--plan-only", "--out", str(tmp_path / "x")]
    with pytest.raises(SystemExit, match="not found"):
        ur.main([*common, "--data-quality", str(tmp_path / "none.json")])
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "flowstate.observations/1"}))
    with pytest.raises(SystemExit, match="not a data-quality artifact"):
        ur.main([*common, "--data-quality", str(bad)])


# --- review 2026-10-07 ---------------------------------------------------------------------------


def test_a_stated_range_is_an_assumption_unless_said_measured(
    scenario: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Minor: --set-range was recorded as not assumed (basis 'stated') whatever it was."""
    common = ["--scenario", str(scenario), "--plan-only", "--out", str(tmp_path / "p")]
    judged = ["--set-range", "t_scale", "0.9", "1.1", "engineering judgement"]
    assert ur.main([*common, *judged]) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("  t_scale: "))
    assert line == (
        "  t_scale: 0.9 – 1.1 [stated with the run, an assumption] (assumed); engineering judgement"
    )
    measured = ["--set-range", "t_scale", "0.9", "1.1", "the 2025 headway survey, report X"]
    assert ur.main([*common, *measured, "--range-basis", "t_scale", "measured"]) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("  t_scale: "))
    assert "[stated with the run, from the measurement its source names]" in line
    assert "(assumed)" not in line
    assert ur.main([*common, *judged, "--range-basis", "t_scale", "assumed"]) == 0
    assert "an assumption] (assumed)" in capsys.readouterr().out
    for extra, match in (
        (["--range-basis", "v0_scale", "measured"], "no --set-range states a v0_scale range"),
        (["--range-basis", "t_scale", "guess"], "one of assumed, measured"),
        (["--range-basis", "t_scale", "measured"] * 2, "given twice"),
    ):
        with pytest.raises(SystemExit, match=match):
            ur.main([*common, *judged, *extra])
    design, _ = _design(scenario, tmp_path / "unc", *judged)
    t = next(p for p in design["space"]["parameters"] if p["kind"] == "t_scale")
    assert t["assumed"] is True and t["basis"] == "stated_assumption"


def test_a_design_resumed_with_out_spelled_otherwise_finds_its_runs(
    scenario: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Minor: the derived populations' paths were built from the --out spelling and enter
    every sample's config hash, so a resume under another spelling redid every run."""
    monkeypatch.chdir(tmp_path)
    design, arms = _design(scenario, Path("unc"))
    row = design["samples"][0]
    done = tmp_path / "unc" / row["sample_id"] / "baseline" / row["arms"]["baseline"]
    (done / str(row["seeds"][0])).mkdir(parents=True)
    (done / str(row["seeds"][0]) / "metrics.json").write_text(json.dumps({"mean_tt_s": 1.0}))
    again, _ = _design(scenario, tmp_path / "unc")
    assert again["samples"] == design["samples"]
    assert Path(row["idm_calibration"]).is_absolute()  # outside the repository: resolved
    total, pending = ur.pending_runs(tmp_path / "unc", again, arms, False)
    assert (total, len(pending)) == (12, 11)


def test_a_derived_population_widens_the_checks_reading_to_its_own_mean(
    scenario: Path, transfer_json: Path, tmp_path: Path
) -> None:
    """Review 2026-10-07 (major), end to end: the check ran on the scenario's population
    (mean v0 30 m/s), read a free-flow range below it and widened it to 30 m/s. A population
    derived by mean v0 x 0.95 (28.5 m/s) is widened to 28.5 m/s, not to 30."""
    doc = json.loads(transfer_json.read_text())
    ff = next(c for c in doc["comparisons"] if c["quantity"] == "free_flow_speed")
    entry = ff["uncertainty_range"]
    # the case under test: the check widened its reading to its own population's mean
    assert entry["basis"] == "observed_interval" and entry["widened_to_configured"] is True
    read_lo, read_hi = entry["parameter_read_low"], entry["parameter_read_high"]
    assert read_hi < 28.5 < 30.0 == pytest.approx(entry["parameter_high"])
    raw = yaml.safe_load(scenario.read_text())
    cal = IDMCalibration.load(raw["fleet"]["idm_calibration"])
    derived = tmp_path / "idm_v0_scaled.json"
    cal.model_copy(update={"mean": {**cal.mean, "v0": cal.mean["v0"] * 0.95}}).save(derived)
    raw["fleet"]["idm_calibration"] = str(derived)
    dscen = tmp_path / "derived.yaml"
    dscen.write_text(yaml.safe_dump(raw))
    args = ur.build_parser().parse_args(
        [
            *("--scenario", str(dscen), "--out", str(tmp_path / "o")),
            *("--transfer-check", str(transfer_json), "--parameters", "v0_scale"),
        ]
    )
    base = ScenarioConfig.from_yaml(dscen)
    (v,) = ur.build_space(args, base).parameters
    mean = 30.0 * 0.95
    m = unc._measured_range("v0", base, 1.0, "measured")
    expected = (max(min(read_lo, mean), m.lo), min(max(read_hi, mean), m.hi))
    assert (v.low * mean, v.high * mean) == (pytest.approx(expected[0]), pytest.approx(expected[1]))
    assert v.high * mean == pytest.approx(mean)  # widened to 28.5 m/s, not 30
    assert v.basis == "observed_interval" and not v.assumed
    assert "is not carried over" in v.source


# --- review 2026-10-07: the scored end of a cool-down scenario ---------------------------------

COMMITTED_DESIGNS = (
    "artifacts/uncertainty_mndot_i94_wb_stpaul_p1_rehearsal.json",
    "artifacts/uncertainty_mndot_i94_wb_stpaul_p1b_rehearsal.json",
)


class _Recorded:
    """An object whose ``to_dict`` is a recorded JSON form (a space or an arm)."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw

    def to_dict(self) -> Any:
        return self.raw


@pytest.mark.parametrize("rel", COMMITTED_DESIGNS)
def test_the_committed_designs_keep_their_keys(rel: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``design_inputs`` rebuilt from what each committed rehearsal records (its scenario's
    sha256, config hash, transfer-check and data-quality records stand in for the files,
    which are not in the repository) gives the recorded key: the scored end enters the
    design only when given."""
    doc = json.loads((REPO_ROOT / rel).read_text())
    prov = doc["provenance"]
    monkeypatch.setattr(ur, "file_sha256", lambda path: prov["scenario_sha256"])
    monkeypatch.setattr(ur, "config_hash", lambda cfg: prov["base_config_hash"])
    monkeypatch.setattr(ur, "transfer_record", lambda args: prov.get("transfer_check"))
    monkeypatch.setattr(ur, "data_quality_record", lambda args: prov.get("data_quality"))
    args = ur.build_parser().parse_args(
        [
            *("--scenario", prov["scenario"], "--out", "unused"),
            *("--seed", str(prov["design_seed"]), "--samples", str(doc["n_samples_design"])),
            *(
                "--seeds",
                str(prov["seeds_per_sample"]),
                "--x-ref",
                str(prov["metrics_args"]["x_ref"]),
            ),
            *("--span", *(str(v) for v in prov["metrics_args"]["span"])),
        ]
    )
    assert args.scored_end_s is None
    space = _Recorded(doc["space"])
    arms = [_Recorded(a) for a in prov["arms"]]
    inputs = ur.design_inputs(args, object(), space, arms)  # type: ignore[arg-type]
    assert "scored_end_s" not in inputs["metrics_args"]
    assert ur._key(inputs) == prov["design_key"]
    args.scored_end_s = 14_400.0
    with_end = ur.design_inputs(args, object(), space, arms)  # type: ignore[arg-type]
    assert with_end["metrics_args"]["scored_end_s"] == 14_400.0
    assert ur._key(with_end) != prov["design_key"]


def test_the_scored_end_reaches_the_design_and_every_payload(
    scenario: Path, tmp_path: Path
) -> None:
    out = tmp_path / "unc"
    plain, _ = _design(scenario, tmp_path / "plain")
    design, arms = _design(scenario, out, "--scored-end-s", "50")
    assert design["metrics_args"] == {"x_ref": 300.0, "span": [100.0, 500.0], "scored_end_s": 50.0}
    assert "scored_end_s" not in plain["metrics_args"]
    assert design["design_key"] != plain["design_key"]
    total, pending = ur.pending_runs(out, design, arms, False)
    assert total == len(pending) == 12
    assert all(
        p[3] == {"x_ref": 300.0, "span": (100.0, 500.0), "scored_end_s": 50.0} for p in pending
    )
    _, plain_pending = ur.pending_runs(tmp_path / "plain", plain, arms, False)
    assert all("scored_end_s" not in p[3] for p in plain_pending)


@pytest.mark.parametrize(("first", "second"), [((), ("50",)), (("50",), ("40",)), (("50",), ())])
def test_a_design_resumed_with_another_scored_end_is_refused(
    scenario: Path, tmp_path: Path, first: tuple[str, ...], second: tuple[str, ...]
) -> None:
    out = tmp_path / "unc"
    _design(scenario, out, *(("--scored-end-s", *first) if first else ()))
    with pytest.raises(
        SystemExit, match=r"different design.*scored with metrics_args.*never mixed"
    ):
        _design(scenario, out, *(("--scored-end-s", *second) if second else ()))


@pytest.mark.parametrize("end", ["0", "61", "inf"])
def test_an_out_of_range_scored_end_is_refused_before_anything_runs(
    scenario: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, end: str
) -> None:
    def refuse(_payload: Any) -> Any:
        raise AssertionError("a run started")

    monkeypatch.setattr(ur, "_worker", refuse)
    out = tmp_path / "unc"
    with pytest.raises(SystemExit, match="nothing was simulated"):
        ur.main(
            [
                *("--scenario", str(scenario), "--samples", "2", "--seeds", "1", "--procs", "1"),
                *ARGS,
                *("--scored-end-s", end, "--out", str(out)),
            ]
        )
    assert not out.exists()
