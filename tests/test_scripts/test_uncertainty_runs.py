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
from flowstate_core.config import ScenarioConfig

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


def test_an_override_may_touch_av_only(tmp_path: Path) -> None:
    good = tmp_path / "fs_best.yaml"
    good.write_text(yaml.safe_dump({"av": {"controller_params": {"U": 25.0}}}))
    arm = ur.parse_arm(["fs", "controller=follower_stopper", "penetration=0.1", f"override={good}"])
    cfg = arm.config({"av": {"penetration": 0.0}, "network": {}})
    assert cfg["av"]["controller_params"] == {"U": 25.0}
    assert arm.override_sha256 is not None
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"av": {}, "fleet": {"T": 1.0}}))
    with pytest.raises(ValueError, match="single key 'av'"):
        ur.parse_arm(["fs", "controller=follower_stopper", "penetration=0.1", f"override={bad}"])


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
    slower and capacity lower than the population: both knobs read an observed interval)."""
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
    for kind, quantity in (("t_scale", "capacity_per_lane"), ("v0_scale", "free_flow_speed")):
        entry = ranges[quantity]
        assert entry["basis"] == "observed_interval"
        line = next(ln for ln in text.splitlines() if ln.startswith(f"  {kind}: "))
        # the same population: the factors carry over unchanged (to the JSON's 4 decimals)
        assert line.startswith(f"  {kind}: {entry['low']:.4g} – {entry['high']:.4g} ")
        assert "[observed 95 % interval (transfer check)]" in line and "(assumed)" not in line
        assert f"{transfer_json} (sha256 " in line
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
    assert space["t_scale"]["basis"] == "observed_interval" and not space["t_scale"]["assumed"]
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
