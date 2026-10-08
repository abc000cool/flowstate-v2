"""Stage ``p16_i94_d10`` (artifacts/i94_d10_2026-10-07/stage_p16_d10.sh.txt), pasted into scripts/gcp/pipeline_i24.sh.

The stage was written beside the pipeline (another agent owns that file); these tests paste it where its header
says, above the "# 9. Done marker" block, or, once the pipeline's owner has inserted it, check that the inserted block
is the stage text byte for byte and run that, and run the pipeline offline: ``uv``, ``curl``, ``sudo`` and ``nproc``
are stubs on ``PATH`` that record every call, and nothing is simulated (docs/PRE_FRISCO_PROGRAM.md, D10).

* The arms are checked against their recipe first; a refused check runs nothing.
* The one-seed reproduction (one replicate, its gate, the readout's ``repro``); when it reproduces (exit 0) the
  committed reference is used, when it does not (exit 10) the reference's 20 seeds are re-run with gate and gated
  report, when it is undetermined nothing more runs.
* Each arm, ``_rbc`` first: the 20-seed battery, the baseline gate and the gated report, as stage p8's ``p8_one``.
* The readout, then B5's arm (``select``); an undetermined arm fails the stage, every decided one passes it.
* The stage and the harness name the same files.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from tests.test_scripts.test_p8c_stage import BASH, GCP, _stubs

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE = REPO_ROOT / "artifacts" / "i94_d10_2026-10-07" / "stage_p16_d10.sh.txt"
MARKER = "# 9. Done marker"
P1A = "artifacts/p1_rehearsal_2026-10-04"
REF = "scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2.yaml"
NAME = "mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2"
H = "uv run --no-sync python artifacts/i94_d10_2026-10-07/harness/corridor_d10.py"
PY = "uv run --no-sync python"

# a uv stub with an exit status per matching call: STUB_UV_CODES="pattern=code;pattern=code"
UV_STUB = r"""#!/bin/bash
echo "uv $*" >> "$STUB_DIR/calls.log"
IFS=';' read -ra RULES <<< "${STUB_UV_CODES:-}"
for rule in "${RULES[@]}"; do
  pat="${rule%%=*}"; code="${rule##*=}"
  if [ -n "$pat" ] && [[ "$*" == *"$pat"* ]]; then echo "[stub] exit $code"; exit "$code"; fi
done
echo "[stub] ok"
"""


def _stage_block() -> str:
    """The stage text from its first stage line on (the lines above it address whoever pastes it)."""
    text = STAGE.read_text()
    return text[text.index("# p16 (opt-in") :]


def pasted_pipeline() -> str:
    """The pipeline with stage p16: as it stands when its owner has inserted the stage (the inserted
    block must be the stage text, byte for byte), else with the stage text pasted above the marker;
    never twice."""
    sh = (GCP / "pipeline_i24.sh").read_text()
    assert sh.count(MARKER) == 1
    if "stage p16_i94_d10 p16_steps" in sh:
        assert sh.count(_stage_block()) == 1
        return sh
    return sh.replace(MARKER, STAGE.read_text() + MARKER, 1)


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    (repo / "scripts" / "gcp").mkdir(parents=True)
    (repo / "scripts" / "gcp" / "pipeline_i24.sh").write_text(pasted_pipeline())
    (repo / "artifacts").mkdir()
    (repo / "scenarios").mkdir()
    for suffix in ("_rbc", "_rb"):
        (repo / "scenarios" / f"mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2{suffix}.yaml").write_text(
            f"# header\nname: {NAME}{suffix}\ntier: micro\n"
        )
    # the battery artifacts scripts/corridor_battery.py would leave (the gates read them)
    for label in (f"{NAME}_p16repro", f"{NAME}_p16ref", f"{NAME}_rbc", f"{NAME}_rb"):
        (repo / "artifacts" / f"validation_{label}.json").write_text("{}")
    return repo


def _run_stage(tmp: Path, codes: str = "") -> tuple[Path, Path, subprocess.CompletedProcess[str]]:
    stub, env = _stubs(tmp)
    (stub / "bin" / "uv").write_text(UV_STUB)
    env["STUB_UV_CODES"] = codes
    repo = _repo(tmp)
    r = subprocess.run(
        [
            BASH,
            str(repo / "scripts" / "gcp" / "pipeline_i24.sh"),
            "--no-shutdown",
            "--stages",
            "p16_i94_d10",
            "--procs",
            "10",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return stub, repo, r


def _calls(stub: Path) -> list[str]:
    keep = ("i94_calibration_days", "corridor_battery", "baseline_gate", "corridor_d10")
    return [
        c
        for c in (stub / "calls.log").read_text().splitlines()
        if c.startswith("uv ") and any(k in c for k in keep)
    ]


def _one(scn: str, label: str, reps: int, gated: bool) -> list[str]:
    out, art, rep = (
        f"runs/{label}/baseline",
        f"artifacts/validation_{label}.json",
        f"docs/reports/{label}",
    )
    calls = [
        f"{PY} scripts/corridor_battery.py --scenario {scn} --observations {P1A}/observations_calibration.json "
        f"--replicates {reps} --procs 10 --out {out} --artifact {art} --report-dir {rep} "
        "--criteria-profile fhwa_tat3_2004",
        f"{PY} scripts/baseline_gate.py --battery-artifact {art} "
        f"--calibration-observations {P1A}/observations_calibration.json "
        f"--validation-observations {P1A}/observations_validation.json --day-split {P1A}/day_split.json "
        f"--per-day {P1A}/per_day --out-json artifacts/baseline_gate_{label}.json --out-md {rep}/baseline_gate.md",
    ]
    if gated:
        calls.append(
            f"{PY} scripts/corridor_battery.py --scenario {scn} --observations {P1A}/observations_calibration.json "
            f"--replicates {reps} --out {out} --artifact artifacts/validation_{label}_gated.json --report-dir {rep} "
            "--criteria-profile fhwa_tat3_2004 --criteria-only --baseline-gate "
            f"--gate-calibration-observations {P1A}/observations_calibration.json "
            f"--gate-validation-observations {P1A}/observations_validation.json "
            f"--gate-day-split {P1A}/day_split.json"
        )
    return calls


CHECK = [f"{PY} scripts/i94_calibration_days.py --check --only d10"]
REPRO = [*_one(REF, f"{NAME}_p16repro", 1, False), f"{H} repro --out artifacts/i94_d10_repro.json"]
RERUN = _one(REF, f"{NAME}_p16ref", 20, True)
ARMS = [
    call
    for s in ("_rbc", "_rb")
    for call in _one(
        f"scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2{s}.yaml", f"{NAME}{s}", 20, True
    )
]
READ = [
    f"{H} evaluate --out artifacts/i94_d10_corridor.json",
    f"{H} select --readout artifacts/i94_d10_corridor.json",
]


def test_p16_reads_the_arms_against_the_committed_reference_when_it_reproduces(
    tmp_path: Path,
) -> None:
    stub, repo, r = _run_stage(tmp_path)
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert (repo / "logs" / "p16_i94_d10.done").is_file(), log
    assert _calls(stub) == CHECK + REPRO + ARMS + READ
    for label in (f"{NAME}_p16repro", f"{NAME}_rbc", f"{NAME}_rb"):
        assert (repo / "logs" / f"p16_{label}.battery.ok").is_file(), label
    assert "the reference reproduces at its first seed" in log
    assert "B5's I-94 arm is _rbc" in log
    # an archive after the reference, after each arm, and the stage's own
    assert log.count("archive (light)") >= 4, log


def test_p16_reruns_the_reference_when_its_first_seed_does_not_reproduce(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, codes=" repro =10; select =10")
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert (repo / "logs" / "p16_i94_d10.done").is_file(), log
    assert _calls(stub) == CHECK + REPRO + RERUN + ARMS + READ
    assert "its 20 seeds are re-run here" in log and "B5's I-94 arm is _rb" in log


def test_p16_runs_nothing_more_when_the_reproduction_is_undetermined(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, codes=" repro =3")
    assert r.returncode == 0, r.stderr  # the pipeline carries on
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p16_i94_d10: FAILED" in log
    assert _calls(stub) == CHECK + REPRO
    assert not (repo / "logs" / "p16_i94_d10.done").exists()


def test_a_refused_recipe_check_runs_nothing(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, codes="--only d10=1")
    assert r.returncode == 0, r.stderr
    assert "stage p16_i94_d10: FAILED" in (repo / "logs" / "pipeline.log").read_text()
    assert _calls(stub) == CHECK


def test_the_reference_as_b5s_arm_passes_and_an_undetermined_arm_fails(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, codes=" select =20")
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert (
        "B5's I-94 arm is the reference" in log and (repo / "logs" / "p16_i94_d10.done").is_file()
    )
    stub, repo, r = _run_stage(tmp_path / "again", codes=" select =3; evaluate =3")
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "B5's I-94 arm is undetermined" in log and "stage p16_i94_d10: FAILED" in log
    assert _calls(stub) == CHECK + REPRO + ARMS + READ


def test_a_failed_arm_battery_still_reads_and_fails_the_stage(tmp_path: Path) -> None:
    stub, repo, _ = _run_stage(tmp_path, codes=f"--artifact artifacts/validation_{NAME}_rb.json=1")
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p16_i94_d10: FAILED" in log
    assert not (repo / "logs" / f"p16_{NAME}_rb.battery.ok").exists()
    assert (
        _calls(stub) == CHECK + REPRO + ARMS + READ
    )  # its stale artifact is still gated, then read


def test_the_stage_and_the_harness_name_the_same_files() -> None:
    spec = importlib.util.spec_from_file_location(
        "p16_stage_corridor_d10",
        REPO_ROOT / "artifacts" / "i94_d10_2026-10-07" / "harness" / "corridor_d10.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    sh = STAGE.read_text()
    assert 'P16_REF="scenarios/${MNDOT}_weave_dc_cal_w1b_w2.yaml"' in sh and mod.REF_SCENARIO == REF
    assert mod.REF_NAME == NAME and f"{mod.REPRO_LABEL}" == f"{NAME}_p16repro"
    assert mod.RERUN_LABEL == f"{NAME}_p16ref" and "_p16ref" in sh and "_p16repro" in sh
    assert mod.REPRO_OUT in sh and "artifacts/i94_d10_corridor.json" in sh
    assert list(mod.ARMS) == ["rbc", "rb"]  # the stage runs them in the selection's order
    assert 'P16_ARMS="${MNDOT}_weave_dc_cal_w1b_w2_rbc ${MNDOT}_weave_dc_cal_w1b_w2_rb"' in sh
    assert mod.CAL_OBS == f"{P1A}/observations_calibration.json" and mod.PROFILE in sh
    assert mod.SELECT_CODES == {"rbc": 0, "rb": 10, "reference": 20}
    for code in ("0)", "10)", "20)"):
        assert f"    {code} say \"p16: B5's I-94 arm is" in sh
