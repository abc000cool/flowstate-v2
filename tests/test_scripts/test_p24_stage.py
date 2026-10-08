"""Stage ``p24_i94_a3`` (artifacts/a3_range_2026-10-07/stage_p24_a3.sh.txt), pasted into scripts/gcp/pipeline_i24.sh.

Amendment 3's range round (docs/A3_RANGE_ROUND.md; docs/FRISCO_PROTOCOL.md, adoption of Amendment 3, items 1-7),
offline: ``uv``, ``curl``, ``sudo`` and ``nproc`` are stubs on ``PATH`` that record every call, and nothing is
simulated. The scenario copies are written by the stage's own sed and awk from the committed files and then checked
here with the harness's real ``check-copy`` (the stub stands in for it on the stage's side).

* The inserted pipeline block is the stage text byte for byte, above the "# 9. Done marker" block.
* Copies first (F2's u0 is the committed file, only its hash is checked), then per arm in the pre-registered order
  F2 u0, F2 u1, F1 u0, F1 u1, F2 u05, F1 u05: the 20-seed battery at ``--procs`` min(PROCS, 10), the gate, the gated
  report, the kept trajectory's lanes, a light archive; then the readout.
* The copies differ from their committed sources only in their name, the T.H.52 share ``{u: U}`` and (F1) W2's three
  switches at 0; none is written under scenarios/.
* A refused copy runs nothing of its family; the other family still runs and the stage fails.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_scripts.test_p8c_stage import BASH, GCP, _stubs

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE = REPO_ROOT / "artifacts" / "a3_range_2026-10-07" / "stage_p24_a3.sh.txt"
HARNESS = REPO_ROOT / "artifacts" / "a3_range_2026-10-07" / "harness" / "corridor_a3.py"
MARKER = "# 9. Done marker"
P1A = "artifacts/p1_rehearsal_2026-10-04"
MNDOT = "mndot_i94_wb_stpaul"
PY = "uv run --no-sync python"
H = f"{PY} artifacts/a3_range_2026-10-07/harness/corridor_a3.py"
FAM = {
    "F1": (f"{MNDOT}_weave_dc_cal_w1b", f"{MNDOT}_weave_xlsfg_dc_cal_w1b", "2dd495d173f4", True),
    "F2": (
        f"{MNDOT}_weave_dc_cal_w1b_w2",
        f"{MNDOT}_weave_xlsfg_dc_cal_w1b_w2",
        "395a111cb991",
        False,
    ),
}
U = {"u0": None, "u05": 0.5, "u1": 1.0}
ORDER = [("F2", "u0"), ("F2", "u1"), ("F1", "u0"), ("F1", "u1"), ("F2", "u05"), ("F1", "u05")]

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


def harness():
    spec = importlib.util.spec_from_file_location("corridor_a3_stage_test", HARNESS)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["corridor_a3_stage_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _stage_block() -> str:
    text = STAGE.read_text()
    return text[text.index("# p24 (opt-in") :]


def pasted_pipeline() -> str:
    """The pipeline as committed: the stage is inserted above the marker, byte for byte the stage text, once."""
    sh = (GCP / "pipeline_i24.sh").read_text()
    assert sh.count(MARKER) == 1
    assert sh.count(_stage_block()) == 1, "the pipeline's p24 block is not the stage text"
    assert sh.index(_stage_block()) < sh.index(MARKER)
    assert sh.index("stage p23_c7b p23_steps") < sh.index(_stage_block())
    return sh


def scenario(fam: str, arm: str) -> str:
    stem, name, _, _ = FAM[fam]
    if fam == "F2" and arm == "u0":
        return f"scenarios/{stem}.yaml"
    return f"runs/p24_a3/scenarios/{name}_a3{arm}.yaml"


def label(fam: str, arm: str) -> str:
    return f"{FAM[fam][1]}_a3{arm}"


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    (repo / "scripts" / "gcp").mkdir(parents=True)
    (repo / "scripts" / "gcp" / "pipeline_i24.sh").write_text(pasted_pipeline())
    (repo / "artifacts").mkdir()
    (repo / "scenarios").mkdir()
    for stem, *_ in FAM.values():
        shutil.copy(REPO_ROOT / "scenarios" / f"{stem}.yaml", repo / "scenarios" / f"{stem}.yaml")
    # the battery artifacts scripts/corridor_battery.py would leave (gate, report and lanes read them)
    for fam, arm in ORDER:
        (repo / "artifacts" / f"validation_{label(fam, arm)}.json").write_text("{}")
    return repo


def _run_stage(
    tmp: Path, codes: str = "", procs: str | None = None
) -> tuple[Path, Path, subprocess.CompletedProcess[str]]:
    stub, env = _stubs(tmp)
    (stub / "bin" / "uv").write_text(UV_STUB)
    env["STUB_UV_CODES"] = codes
    repo = _repo(tmp)
    args = [
        BASH,
        str(repo / "scripts" / "gcp" / "pipeline_i24.sh"),
        "--no-shutdown",
        "--stages",
        "p24_i94_a3",
    ]
    if procs is not None:
        args += ["--procs", procs]
    r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=120)
    return stub, repo, r


def _calls(stub: Path) -> list[str]:
    keep = ("corridor_battery", "baseline_gate", "corridor_a3")
    return [
        c
        for c in (stub / "calls.log").read_text().splitlines()
        if c.startswith("uv ") and any(k in c for k in keep)
    ]


def _copy_call(fam: str, arm: str) -> str:
    stem, _, h, w2off = FAM[fam]
    src = f"scenarios/{stem}.yaml"
    call = f"{H} check-copy --source {src} --source-hash {h} --copy {scenario(fam, arm)}"
    if U[arm] is not None:
        call += f" --u {U[arm]}"
    if w2off and not (fam == "F2" and arm == "u0"):
        call += " --w2-off"
    return call


def _arm_calls(fam: str, arm: str, procs: int = 10) -> list[str]:
    scn, lab = scenario(fam, arm), label(fam, arm)
    out, art, rep = (
        f"runs/{lab}/baseline",
        f"artifacts/validation_{lab}.json",
        f"docs/reports/{lab}",
    )
    return [
        f"{PY} scripts/corridor_battery.py --scenario {scn} --observations {P1A}/observations_calibration.json "
        f"--replicates 20 --procs {procs} --out {out} --artifact {art} --report-dir {rep} "
        "--criteria-profile fhwa_tat3_2004",
        f"{PY} scripts/baseline_gate.py --battery-artifact {art} "
        f"--calibration-observations {P1A}/observations_calibration.json "
        f"--validation-observations {P1A}/observations_validation.json --day-split {P1A}/day_split.json "
        f"--per-day {P1A}/per_day --out-json artifacts/baseline_gate_{lab}.json --out-md {rep}/baseline_gate.md",
        f"{PY} scripts/corridor_battery.py --scenario {scn} --observations {P1A}/observations_calibration.json "
        f"--replicates 20 --out {out} --artifact artifacts/validation_{lab}_gated.json --report-dir {rep} "
        "--criteria-profile fhwa_tat3_2004 --criteria-only --baseline-gate "
        f"--gate-calibration-observations {P1A}/observations_calibration.json "
        f"--gate-validation-observations {P1A}/observations_validation.json "
        f"--gate-day-split {P1A}/day_split.json",
        f"{H} lanes --label {lab}",
    ]


EVALUATE = f"{H} evaluate --out artifacts/a3_range.json"


def test_p24_writes_checks_and_runs_every_arm_in_the_registered_order(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path)
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert (repo / "logs" / "p24_i94_a3.done").is_file(), log
    copies = [_copy_call(f, a) for f, a in ORDER]
    arms = [c for f, a in ORDER for c in _arm_calls(f, a)]
    assert _calls(stub) == [*copies, *arms, EVALUATE]
    for f, a in ORDER:
        assert (repo / "logs" / f"p24_{label(f, a)}.battery.ok").is_file()
    # an archive after each arm and the stage's own
    assert log.count("archive (light)") >= len(ORDER) + 1, log
    # nothing written under scenarios/ but the two committed sources
    assert sorted(p.name for p in (repo / "scenarios").iterdir()) == sorted(
        f"{stem}.yaml" for stem, *_ in FAM.values()
    )


def test_the_copies_are_their_sources_but_for_the_name_the_share_and_the_switches(
    tmp_path: Path,
) -> None:
    """The stage's sed and awk output, checked with the harness's real check-copy, and refused when wrong."""
    _, repo, r = _run_stage(tmp_path)
    assert r.returncode == 0, r.stderr
    a3 = harness()
    written = sorted(p.name for p in (repo / "runs" / "p24_a3" / "scenarios").iterdir())
    assert written == sorted(
        f"{label(f, a)}.yaml" for f, a in ORDER if not (f == "F2" and a == "u0")
    )
    for fam, arm in ORDER:
        stem, name, h, w2off = FAM[fam]
        src = repo / "scenarios" / f"{stem}.yaml"
        copy = repo / scenario(fam, arm)
        why, chash = a3.check_copy(src, copy, h, U[arm], w2off and copy != src)
        assert why == [], (fam, arm, why)
        assert chash is not None
        if copy == src:
            assert chash == h  # F2's u0 runs the committed file
            continue
        text = copy.read_text()
        head = text.splitlines()[0]
        assert head.startswith(
            f"# {name}_a3{arm}: scenarios/{stem}.yaml (config hash {h}, policy v4)"
        )
        assert f"name: {name}_a3{arm}\n" in text
        shares = [
            line for line in text.splitlines() if line.startswith("      ramp_to_ramp_share:")
        ]
        assert shares == ([] if U[arm] is None else [f"      ramp_to_ramp_share: {{u: {U[arm]}}}"])
        if w2off:
            assert (
                text.count(
                    "weave_handback: 0.0, weave_close_leader: 0.0, weave_resolve_opposing: 0.0"
                )
                == 2
            )
        # the copy's own hash moves with u and, for F1, with the switches; never the source's
        assert chash != h
    # check-copy refuses a copy that differs elsewhere, the share on Ruth St, a moved source
    f1u1 = repo / scenario("F1", "u1")
    src1 = repo / "scenarios" / f"{FAM['F1'][0]}.yaml"
    bad = tmp_path / "bad.yaml"
    bad.write_text(f1u1.read_text().replace("lc_keep_right: 0.1", "lc_keep_right: 0.2", 1))
    why, _ = a3.check_copy(src1, bad, FAM["F1"][2], 1.0, True)
    assert any("beyond its name" in w for w in why), why
    ruth = f1u1.read_text().replace(
        "      exit_ramp: C-D split 18208090\n",
        "      exit_ramp: C-D split 18208090\n      ramp_to_ramp_share: {u: 1.0}\n",
        1,
    )
    bad.write_text(ruth)
    why, _ = a3.check_copy(src1, bad, FAM["F1"][2], 1.0, True)
    assert any("Ruth St" in w for w in why), why
    why, _ = a3.check_copy(src1, f1u1, "000000000000", 1.0, True)
    assert any("no longer" in w or "not 000000000000" in w for w in why), why
    why, _ = a3.check_copy(src1, f1u1, FAM["F1"][2], 0.5, True)
    assert any("not {u: 0.5}" in w for w in why), why
    why, _ = a3.check_copy(src1, f1u1, FAM["F1"][2], 1.0, False)
    assert why, "an F1 copy read as F2's (switches not expected off) still differs from its source"


@pytest.mark.parametrize(("procs", "expected"), [("6", 6), ("14", 10)])
def test_the_batteries_run_at_most_ten_processes(tmp_path: Path, procs: str, expected: int) -> None:
    stub, _, r = _run_stage(tmp_path, procs=procs)
    assert r.returncode == 0, r.stderr
    batteries = [
        c for c in _calls(stub) if "corridor_battery.py" in c and "--criteria-only" not in c
    ]
    assert len(batteries) == len(ORDER)
    assert all(f" --procs {expected} " in c for c in batteries), batteries


def test_a_refused_copy_runs_nothing_of_its_family(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(
        tmp_path, codes="--copy runs/p24_a3/scenarios/" + label("F1", "u1") + ".yaml=1"
    )
    assert r.returncode == 0, r.stderr  # the pipeline carries on
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p24_i94_a3: FAILED" in log
    assert "the F1 u1 copy was refused; F1 runs nothing" in log
    calls = _calls(stub)
    # F1's later copies are not written or checked; F2's arms run, F1's do not; the readout still runs
    copies = [c for c in calls if "check-copy" in c]
    assert copies == [_copy_call(f, a) for f, a in ORDER if f == "F2" or a in ("u0", "u1")]
    ran = [c for c in calls if "corridor_battery.py" in c and "--criteria-only" not in c]
    assert [c.split("--scenario ")[1].split()[0] for c in ran] == [
        scenario(f, a) for f, a in ORDER if f == "F2"
    ]
    assert calls[-1] == EVALUATE


def test_a_readout_with_problems_fails_the_stage_after_every_arm(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, codes=" evaluate =3")
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p24_i94_a3: FAILED" in log
    assert "the readout recorded problems" in log
    assert _calls(stub)[-1] == EVALUATE
    assert sum("lanes --label" in c for c in _calls(stub)) == len(ORDER)


def test_a_finished_battery_is_not_repeated(tmp_path: Path) -> None:
    stub, env = _stubs(tmp_path)
    (stub / "bin" / "uv").write_text(UV_STUB)
    env["STUB_UV_CODES"] = ""
    repo = _repo(tmp_path)
    (repo / "logs").mkdir()
    done = label("F2", "u0")
    (repo / "logs" / f"p24_{done}.battery.ok").write_text("")
    r = subprocess.run(
        [
            BASH,
            str(repo / "scripts" / "gcp" / "pipeline_i24.sh"),
            "--no-shutdown",
            "--stages",
            "p24_i94_a3",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    batteries = [
        c for c in _calls(stub) if "corridor_battery.py" in c and "--criteria-only" not in c
    ]
    assert all(f"validation_{done}.json " not in c for c in batteries)
    assert len(batteries) == len(ORDER) - 1
    assert f"p24: {done} battery done earlier" in (repo / "logs" / "pipeline.log").read_text()
