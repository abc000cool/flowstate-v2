"""Stages ``p15_i24_b5`` and ``p17_i94_b5`` of scripts/gcp/pipeline_i24.sh and their ingest, offline.

``uv``, ``curl``, ``sudo`` and ``nproc`` are stubs on ``PATH`` that record every call; nothing is
simulated (docs/PRE_FRISCO_PROGRAM.md B5).

* p15: the fit (at most 8 processes) on the from-arm ``--p15-arm`` names (default the B2 arm; the arm
  C7b selects otherwise, with its level), the chosen arm's battery through stage p12's path (20 seeds,
  ring rows, 8 analysis processes, braking counts), its ramp flows, the readout. A fit that exits 3
  (constraint_unmet) or 2 (refused) stops the stage before any battery, and so does a missing input of
  the arm; a finished fit is not repeated. The archive carries the battery's meta.json, edges.parquet and braking counts, never
  trajectories or vehicles.parquet.
* p17: the fit on the from-arm named by ``--p17-arm`` (default the W1b + W2 calibration-day arm), stage
  p8's battery, gate and gated report of ``<arm>_b5``, the readout; a missing arm runs nothing; the
  archive carries the fit runs' files and the pair manifest.
* The ingest installs every output of both stages.

The p15 fit's command is accepted as the pipeline gives it now or with ``--section-lanes all``
(``P15_SECTION_LANES``' default, docs/I24_CONSISTENCY_C7B.md §3) anywhere in it: either is the default
fit. Once the pipeline passes the setting, ``--p15-section-lanes observed`` must reach the fit.
"""

from __future__ import annotations

import re
import subprocess
import tarfile
from pathlib import Path

from tests.test_scripts.test_p8c_stage import BASH, GCP, _stubs, _tgz
from tests.test_scripts.test_p14_stage import UV_STUB

SEED = "6914975401685141156"
P15_RUN = f"runs/i24_validation/p15_b5/abcdef123456/{SEED}"
P17_FIT_RUN = f"runs/mndot_i94_b5_fit/s0.975/fedcba654321/{SEED}"
ARM = "mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2"
P15_FROM = "i24_replica_flow_rc_speedcal_dc_refit"
ARM_NAME = "mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2"
FIT = "uv run --no-sync python scripts/fit_demand_level.py"
READOUT = (
    "uv run --no-sync python artifacts/pre_frisco_2026-10-07/harness_b5/demand_level_readout.py"
)
H = "uv run --no-sync python artifacts/i24_discharge_2026-10-07"
P1A = "artifacts/p1_rehearsal_2026-10-04"


def _repo(tmp: Path, arms: tuple[str, ...] = (ARM,)) -> Path:
    repo = tmp / "repo"
    (repo / "scripts" / "gcp").mkdir(parents=True)
    (repo / "scripts" / "gcp" / "pipeline_i24.sh").write_text((GCP / "pipeline_i24.sh").read_text())
    (repo / "artifacts").mkdir()
    (repo / "artifacts" / "a.json").write_text("{}")
    # the battery artifact as scripts/i24_validate.py leaves it (the reduction reads it)
    (repo / "artifacts" / "i24_validation_p15_b5.json").write_text("{}")
    (repo / "scenarios").mkdir()
    # p15's from-arm (B2), its base and its battery, and the arm the fit writes
    for stem in (P15_FROM, "i24_replica_flow_rc_corrected_dc", f"{P15_FROM}_b5"):
        (repo / "scenarios" / f"{stem}.yaml").write_text(f"name: {stem}\n")
    (repo / "artifacts" / "i24_validation_dc_refit_rc.json").write_text("{}")
    for arm in arms:
        name = ARM_NAME if arm == ARM else f"{arm}_name"
        (repo / "scenarios" / f"{arm}.yaml").write_text(f"name: {name}\n")
        # as the fit writes the arm, and as corridor_battery.py leaves its battery artifact
        (repo / "scenarios" / f"{arm}_b5.yaml").write_text(f"name: {name}_b5\n")
        (repo / "artifacts" / f"validation_{name}_b5.json").write_text("{}")
    for run in (P15_RUN, P17_FIT_RUN):
        (repo / run / "net").mkdir(parents=True)
        for f in (
            "meta.json",
            "edges.parquet",
            "vehicles.parquet",
            "trajectories.parquet",
            "metrics.json",
            "observed_scores.json",
        ):
            (repo / run / f).write_text("x")
    (repo / "runs" / "i24_validation" / "p15_b5" / "hard_braking.json").write_text("{}")
    (repo / "runs" / "mndot_i94_b5_fit" / "PAIRS.json").write_text("{}")
    (repo / "runs" / "mndot_i94_b5_fit" / "scenarios").mkdir()
    (repo / "runs" / "mndot_i94_b5_fit" / "scenarios" / "s0.975.yaml").write_text("name: y\n")
    return repo


def _run(
    tmp: Path, stages: str, *extra: str, arms: tuple[str, ...] = (ARM,), **env_extra: str
) -> tuple[Path, Path, subprocess.CompletedProcess[str]]:
    stub, env = _stubs(tmp)
    (stub / "bin" / "uv").write_text(UV_STUB)
    env.update(env_extra)
    repo = _repo(tmp, arms)
    r = subprocess.run(
        [
            BASH,
            str(repo / "scripts" / "gcp" / "pipeline_i24.sh"),
            "--no-shutdown",
            "--stages",
            stages,
            *extra,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return stub, repo, r


def _calls(stub: Path) -> list[str]:
    keys = ("fit_demand_level", "harness", "i24_validate", "corridor_battery", "baseline_gate")
    return [
        c
        for c in (stub / "calls.log").read_text().splitlines()
        if c.startswith("uv ") and any(k in c for k in keys)
    ]


#: P15_SECTION_LANES' default as the pipeline would pass it (module docstring)
SECTION_LANES_ALL = re.compile(r" --section-lanes all(?= )")


def _p15_calls(stub: Path) -> list[str]:
    """``_calls``, the I-24 fit's ``--section-lanes all`` (the default, if passed) taken out."""
    out = []
    for c in _calls(stub):
        if "fit_demand_level.py --corridor i24" in c:
            assert c.count("--section-lanes") <= 1, c
            c = SECTION_LANES_ALL.sub("", c, count=1)
        out.append(c)
    return out


def _p15_fit(
    frm: str = P15_FROM,
    base: str = "i24_replica_flow_rc_corrected_dc",
    label: str = "dc_refit_rc",
    extra: str = "",
) -> str:
    return (
        f"{FIT} --corridor i24 --procs 8 --from-scenario scenarios/{frm}.yaml --base-yaml scenarios/{base}.yaml "
        f"--from-battery artifacts/i24_validation_{label}.json {extra}--write-scenario "
        f"--out artifacts/demand_level_fit_i24.json --scenario-out scenarios/{frm}_b5.yaml"
    )


def _p15_rest(frm: str = P15_FROM) -> list[str]:
    return [
        f"uv run --no-sync python scripts/i24_validate.py --scenario scenarios/{frm}_b5.yaml "
        "--label p15_b5 --replicates 20 --procs 14 --analysis-procs 8 --ring-seeds 20 --lane-crossings",
        *P15_TAIL,
    ]


P15_FIT = _p15_fit()
P15_TAIL = [
    f"{H}/harness/hard_braking.py --artifact artifacts/i24_validation_p15_b5.json "
    "--out runs/i24_validation/p15_b5/hard_braking.json",
    f"{H}/harness_b2/corridor_b2.py reduce --battery artifacts/i24_validation_p15_b5.json "
    "--out artifacts/i24_b2_ramp_flows_p15_b5.json",
    f"{READOUT} i24 --out artifacts/demand_level_i24.json --fit artifacts/demand_level_fit_i24.json --label p15_b5",
]
P15_REST = _p15_rest()


def test_p15_fits_then_runs_the_battery_and_the_readout(tmp_path: Path) -> None:
    stub, repo, r = _run(tmp_path, "p15_i24_b5")
    assert r.returncode == 0, r.stderr
    assert (repo / "logs" / "p15_i24_b5.done").is_file()
    assert _p15_calls(stub) == [P15_FIT, *P15_REST]
    assert (repo / "logs" / f"p15_fit_{P15_FROM}.ok").is_file()
    assert (repo / "logs" / "p15_b5.battery.ok").is_file()
    with tarfile.open(tmp_path / "home" / "final.tgz", "r:gz") as tf:
        names = set(tf.getnames())
    for kept in ("meta.json", "edges.parquet"):
        assert f"{P15_RUN}/{kept}" in names, kept
    assert "runs/i24_validation/p15_b5/hard_braking.json" in names
    assert not any(
        n.startswith("runs/i24_validation/p15")
        and n.endswith(("trajectories.parquet", "vehicles.parquet"))
        for n in names
    )


def test_p15_stops_when_the_constraint_is_unmet_or_the_fit_refuses(tmp_path: Path) -> None:
    for code, words in (("3", "constraint_unmet"), ("2", "refused its inputs")):
        tmp = tmp_path / code
        tmp.mkdir()
        stub, repo, r = _run(
            tmp, "p15_i24_b5", STUB_UV_FAIL_ON="fit_demand_level.py", STUB_UV_FAIL_CODE=code
        )
        assert r.returncode == 0, r.stderr  # the pipeline carries on
        log = (repo / "logs" / "pipeline.log").read_text() + (
            repo / "logs" / "p15_i24_b5.log"
        ).read_text()
        assert "stage p15_i24_b5: FAILED" in log and words in log
        assert _p15_calls(stub) == [P15_FIT]  # no battery, no readout
        assert not (repo / "logs" / f"p15_fit_{P15_FROM}.ok").exists()
        assert (tmp / "home" / "final.tgz").is_file()


def test_p15_does_not_repeat_a_finished_fit(tmp_path: Path) -> None:
    stub, env = _stubs(tmp_path)
    (stub / "bin" / "uv").write_text(UV_STUB)
    repo = _repo(tmp_path)
    (repo / "logs").mkdir()
    (repo / "logs" / f"p15_fit_{P15_FROM}.ok").write_text("")
    (repo / "artifacts" / "demand_level_fit_i24.json").write_text("{}")
    r = subprocess.run(
        [
            BASH,
            str(repo / "scripts" / "gcp" / "pipeline_i24.sh"),
            "--no-shutdown",
            "--stages",
            "p15_i24_b5",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert _p15_calls(stub) == P15_REST


def test_p15_takes_the_arm_c7b_selects(tmp_path: Path) -> None:
    stub, env = _stubs(tmp_path)
    (stub / "bin" / "uv").write_text(UV_STUB)
    repo = _repo(tmp_path)
    for stem in ("i24_c7b_arm", "i24_c7b_base", "i24_c7b_arm_b5"):
        (repo / "scenarios" / f"{stem}.yaml").write_text(f"name: {stem}\n")
    (repo / "artifacts" / "i24_validation_c7b.json").write_text("{}")
    args = ["--stages", "p15_i24_b5", "--p15-arm", "i24_c7b_arm:i24_c7b_base:c7b:0.95"]
    r = subprocess.run(
        [BASH, str(repo / "scripts" / "gcp" / "pipeline_i24.sh"), "--no-shutdown", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    fit = _p15_fit("i24_c7b_arm", "i24_c7b_base", "c7b", "--carried-scale 0.95 ")
    assert _p15_calls(stub) == [fit, *_p15_rest("i24_c7b_arm")]
    # a missing input of the arm runs nothing
    (repo / "artifacts" / "i24_validation_c7b.json").unlink()
    (repo / "logs" / "p15_i24_b5.done").unlink()
    (stub / "calls.log").write_text("")
    r = subprocess.run(
        [BASH, str(repo / "scripts" / "gcp" / "pipeline_i24.sh"), "--no-shutdown", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0 and _p15_calls(stub) == []
    assert (
        "p15: no artifacts/i24_validation_c7b.json" in (repo / "logs" / "pipeline.log").read_text()
    )


def test_p15_passes_its_section_lanes_setting_to_the_fit(tmp_path: Path) -> None:
    """``--p15-section-lanes observed`` reaches the fit as ``--section-lanes observed`` and the
    battery as ``--section-lanes observed`` beside the ``--lane-crossings`` every p15 battery records (so the fit's objective and the battery's
    link-flow row, C3's input, count the same lanes; docs/I24_CONSISTENCY_C7B.md §3); the reduction and
    the readout lines are unchanged. With ``all`` (the default) every call is as before."""
    stub, _, r = _run(tmp_path, "p15_i24_b5", "--p15-section-lanes", "observed")
    assert r.returncode == 0, r.stderr
    calls = _calls(stub)
    assert calls[0].count(" --section-lanes observed ") == 1, calls[0]
    assert calls[0].replace(" --section-lanes observed", "", 1) == P15_FIT
    assert calls[1] == P15_REST[0] + " --section-lanes observed", calls[1]
    assert calls[2:] == P15_REST[1:]


def test_p15_refuses_an_unknown_section_lanes_setting(tmp_path: Path) -> None:
    stub, _, r = _run(tmp_path, "p15_i24_b5", "--p15-section-lanes", "some")
    assert r.returncode == 0, r.stderr
    assert _calls(stub) == []


def _p17(arm: str, name: str) -> list[str]:
    scn, out = f"scenarios/{arm}_b5.yaml", f"{name}_b5"
    rep, runs = f"docs/reports/{out}", f"runs/{out}/baseline"
    cal, val = f"{P1A}/observations_calibration.json", f"{P1A}/observations_validation.json"
    return [
        f"{FIT} --corridor i94 --from-scenario scenarios/{arm}.yaml --procs 10 --write-scenario "
        f"--out artifacts/demand_level_fit_i94.json --scenario-out {scn}",
        f"uv run --no-sync python scripts/corridor_battery.py --scenario {scn} --observations {cal} "
        f"--replicates 20 --procs 10 --out {runs} --artifact artifacts/validation_{out}.json --report-dir {rep} "
        "--criteria-profile fhwa_tat3_2004",
        f"uv run --no-sync python scripts/baseline_gate.py --battery-artifact artifacts/validation_{out}.json "
        f"--calibration-observations {cal} --validation-observations {val} --day-split {P1A}/day_split.json "
        f"--per-day {P1A}/per_day --out-json artifacts/baseline_gate_{out}.json --out-md {rep}/baseline_gate.md",
        f"uv run --no-sync python scripts/corridor_battery.py --scenario {scn} --observations {cal} "
        f"--replicates 20 --out {runs} --artifact artifacts/validation_{out}_gated.json --report-dir {rep} "
        "--criteria-profile fhwa_tat3_2004 --criteria-only --baseline-gate "
        f"--gate-calibration-observations {cal} --gate-validation-observations {val} "
        f"--gate-day-split {P1A}/day_split.json",
        f"{READOUT} i94 --out artifacts/demand_level_i94.json --fit artifacts/demand_level_fit_i94.json",
    ]


def test_p17_fits_the_default_arm_then_runs_p8s_battery_gate_and_the_readout(
    tmp_path: Path,
) -> None:
    stub, repo, r = _run(tmp_path, "p17_i94_b5", "--procs", "10")
    assert r.returncode == 0, r.stderr
    assert (repo / "logs" / "p17_i94_b5.done").is_file()
    assert _calls(stub) == _p17(ARM, ARM_NAME)
    assert (repo / "logs" / f"p17_fit_{ARM}.ok").is_file()
    with tarfile.open(tmp_path / "home" / "final.tgz", "r:gz") as tf:
        names = set(tf.getnames())
    for kept in ("meta.json", "metrics.json", "observed_scores.json", "vehicles.parquet"):
        assert f"{P17_FIT_RUN}/{kept}" in names, kept
    assert (
        "runs/mndot_i94_b5_fit/PAIRS.json" in names
        and "runs/mndot_i94_b5_fit/scenarios/s0.975.yaml" in names
    )
    assert f"{P17_FIT_RUN}/trajectories.parquet" not in names


def test_p17_takes_the_arm_d10_selects(tmp_path: Path) -> None:
    arm = f"{ARM}_rbc"
    stub, repo, r = _run(tmp_path, "p17_i94_b5", "--procs", "10", "--p17-arm", arm, arms=(ARM, arm))
    assert r.returncode == 0, r.stderr
    assert _calls(stub) == _p17(arm, f"{arm}_name")
    assert (
        "p17: the from-arm is scenarios/" + arm + ".yaml"
        in (repo / "logs" / "pipeline.log").read_text()
    )


def test_p17_without_its_arm_runs_nothing(tmp_path: Path) -> None:
    stub, repo, r = _run(tmp_path, "p17_i94_b5", "--p17-arm", "no_such_arm")
    assert r.returncode == 0, r.stderr
    assert "stage p17_i94_b5: FAILED" in (repo / "logs" / "pipeline.log").read_text()
    assert _calls(stub) == []


def test_p17_stops_on_constraint_unmet(tmp_path: Path) -> None:
    stub, repo, r = _run(
        tmp_path,
        "p17_i94_b5",
        "--procs",
        "10",
        STUB_UV_FAIL_ON="fit_demand_level.py",
        STUB_UV_FAIL_CODE="3",
    )
    assert r.returncode == 0, r.stderr
    assert _calls(stub) == _p17(ARM, ARM_NAME)[:1]
    assert "constraint_unmet" in (repo / "logs" / "pipeline.log").read_text()


def test_the_ingest_installs_every_b5_output(tmp_path: Path) -> None:
    _, env = _stubs(tmp_path)
    repo = tmp_path / "ingest_repo"
    (repo / "artifacts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    out = f"{ARM_NAME}_b5"
    artifacts = [
        "demand_level_fit_i24.json",
        "demand_level_fit_i94.json",
        "demand_level_i24.json",
        "demand_level_i94.json",
        "i24_validation_p15_b5.json",
        "i24_b2_ramp_flows_p15_b5.json",
        f"validation_{out}.json",
        f"validation_{out}_gated.json",
        f"baseline_gate_{out}.json",
        "baseline_gate_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2_rbc_b5.json",
    ]
    scenarios = {
        f"{P15_FROM}_b5.yaml": f"# {P15_FROM}_b5 — B5\nname: {P15_FROM}_b5\n",
        f"{ARM}_b5.yaml": f"# {out} — B5\nname: {out}\n",
        "some_other_arm_b5.yaml": "# some_other_arm_b5 — B5\nname: some_other_arm_b5\n",
    }
    files = {f"artifacts/{a}": f'{{"name": "{a}"}}' for a in artifacts}
    files |= {f"scenarios/{s}": text for s, text in scenarios.items()}
    files |= {
        f"{P15_RUN}/meta.json": '{"run": "p15"}',
        f"{P15_RUN}/edges.parquet": "edges",
        "runs/i24_validation/p15_b5/hard_braking.json": "{}",
        f"{P17_FIT_RUN}/meta.json": '{"run": "p17"}',
        f"{P17_FIT_RUN}/observed_scores.json": "{}",
        "runs/mndot_i94_b5_fit/PAIRS.json": "{}",
        "logs/PIPELINE_EXIT": "rc=0\n",
    }
    tgz = _tgz(tmp_path / "final.tgz", files)
    r = subprocess.run(
        [BASH, str(GCP / "ingest_pipeline_results.sh"), str(tgz)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    for a in artifacts:
        assert f"artifact {a}\n" in r.stdout, a
    for s in scenarios:
        assert f"scenario {s}\n" in r.stdout, s
    assert "runs: i24_validation/p15_b5" in r.stdout and "runs: mndot_i94_b5_fit" in r.stdout
    assert "NOT ingested" not in r.stdout and "WARNING" not in r.stdout, r.stdout
    for name in files:
        if not name.startswith("logs/"):
            assert (repo / name).read_text() == files[name], name
