"""Stage ``p25_i24_b2_k0`` (artifacts/i24_discharge_2026-10-07/stage_p25_b2_k0.sh.txt) and its ingest, offline.

B2 on the k = 0 arm (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.7). ``uv``, ``curl``, ``sudo`` and ``nproc`` are stubs on
``PATH`` that record every call; nothing is simulated.

* The inserted pipeline block is the stage text byte for byte, once, after p24's block and above the
  "# 9. Done marker" block; the stage and the harness name the same files and labels.
* The arm first, then the same-code reference through stage p4_i24_ref's battery line (20 seeds, ring rows, 8
  analysis processes, ``--procs`` as p13), its braking counts and ramp flows, ``check-ref``, the arm's battery the
  same way, the readout.
* A refused arm runs nothing; a reference that does not reproduce the committed battery stops the stage before the
  arm's battery.
* The ingest installs the readout, the batteries, the reductions, the braking counts and the arm.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from tests.test_scripts.test_p8c_stage import BASH, GCP, _stubs, _tgz

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE = REPO_ROOT / "artifacts" / "i24_discharge_2026-10-07" / "stage_p25_b2_k0.sh.txt"
HARNESS = (
    REPO_ROOT / "artifacts" / "i24_discharge_2026-10-07" / "harness_b2_k0" / "corridor_b2_k0.py"
)
MARKER = "# 9. Done marker"
PY = "uv run --no-sync python"
H = f"{PY} artifacts/i24_discharge_2026-10-07/harness_b2_k0/corridor_b2_k0.py"
H2 = f"{PY} artifacts/i24_discharge_2026-10-07/harness_b2/corridor_b2.py"
BRAKE = f"{PY} artifacts/i24_discharge_2026-10-07/harness/hard_braking.py"
REF, RC = "flow_speedcal_p25ref", "flow_speedcal_rc"
ARM = "scenarios/i24_replica_flow_rc_speedcal.yaml"

UV_STUB = r"""#!/bin/bash
echo "uv $*" >> "$STUB_DIR/calls.log"
if [ -n "${STUB_UV_FAIL_ON:-}" ] && [[ "$*" == *"$STUB_UV_FAIL_ON"* ]]; then
  echo "[stub] exit ${STUB_UV_FAIL_CODE:-3}"; exit "${STUB_UV_FAIL_CODE:-3}"
fi
echo "[stub] ok"
"""


def _harness():
    spec = importlib.util.spec_from_file_location("p25_stage_harness", HARNESS)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_pipeline_carries_the_stage_text_verbatim() -> None:
    subprocess.run([BASH, "-n", str(STAGE)], check=True)
    subprocess.run([BASH, "-n", str(GCP / "pipeline_i24.sh")], check=True)
    sh = (GCP / "pipeline_i24.sh").read_text()
    block = STAGE.read_text()
    assert block.startswith("# p25 (opt-in")
    assert sh.count(MARKER) == 1
    assert sh.count(block) == 1, "the pipeline's p25 block is not the stage text"
    assert sh.index('stage p24_i94_a3 p24_steps || say "p24_i94_a3 failed; continuing"') < sh.index(
        block
    )
    # stage p22_reports's block follows it (tests/test_scripts/test_p22_stage.py)
    assert sh.index(block) + len(block) <= sh.index(MARKER)


def test_the_stage_and_the_harness_name_the_same_files() -> None:
    h = _harness()
    block = STAGE.read_text()
    assert f"P25_H={HARNESS.relative_to(REPO_ROOT)}\n" in block
    assert f"P25_REF_SCN={h.REF_SCENARIO}\n" in block
    assert f"P25_ARM={h.ARM}\n" in block
    assert f"P25_REF={h.REF_LABEL}\n" in block and f"P25_RC={h.ARM_LABEL}\n" in block
    assert h.COMMITTED_REF in block and h.OUT in block
    for pinned in (h.ARM_HASH_V4, h.ARM_HASH_V3, h.REF_HASH_V3, "5a8e079c4bfa"):
        assert pinned in block, pinned
    assert h.STAGE == "p25_i24_b2_k0"


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    (repo / "scripts" / "gcp").mkdir(parents=True)
    (repo / "scripts" / "gcp" / "pipeline_i24.sh").write_text((GCP / "pipeline_i24.sh").read_text())
    (repo / "artifacts").mkdir()
    (repo / "artifacts" / "a.json").write_text("{}")
    (repo / "scenarios").mkdir()
    (repo / "scenarios" / "s.yaml").write_text("name: s\n")
    return repo


def _run_stage(tmp: Path, **extra_env: str) -> tuple[Path, Path, subprocess.CompletedProcess[str]]:
    stub, env = _stubs(tmp)
    (stub / "bin" / "uv").write_text(UV_STUB)
    env.update(extra_env)
    repo = _repo(tmp)
    r = subprocess.run(
        [
            BASH,
            str(repo / "scripts" / "gcp" / "pipeline_i24.sh"),
            "--no-shutdown",
            "--stages",
            "p25_i24_b2_k0",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return stub, repo, r


def _calls(stub: Path) -> list[str]:
    keep = ("i24_validate", "harness")
    return [
        c
        for c in (stub / "calls.log").read_text().splitlines()
        if c.startswith("uv ") and any(k in c for k in keep)
    ]


def _battery(scn: str, label: str) -> list[str]:
    return [
        f"{PY} scripts/i24_validate.py --scenario {scn} --label {label} --replicates 20 --procs 14 "
        "--analysis-procs 8 --ring-seeds 20",
        f"{BRAKE} --artifact artifacts/i24_validation_{label}.json "
        f"--out artifacts/i24_hard_braking_{label}.json",
        f"{H2} reduce --battery artifacts/i24_validation_{label}.json "
        f"--out artifacts/i24_b2_ramp_flows_{label}.json",
    ]


ARM_CALL = f"{H} arm --out {ARM}"
CHECK = f"{H} check-ref --battery artifacts/i24_validation_{REF}.json"
SCORE = f"{H} score --out artifacts/boundary_b2_k0_corridor.json"
ALL = [
    ARM_CALL,
    *_battery("scenarios/i24_replica_flow_speedcal.yaml", REF),
    CHECK,
    *_battery(ARM, RC),
    SCORE,
]


def test_p25_runs_the_arm_the_reference_the_check_the_arm_battery_and_the_readout(
    tmp_path: Path,
) -> None:
    stub, repo, r = _run_stage(tmp_path)
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert (repo / "logs" / "p25_i24_b2_k0.done").is_file(), log
    assert _calls(stub) == ALL
    for label in (REF, RC):
        assert (repo / "logs" / f"p25_{label}.battery.ok").is_file(), label
    # an archive after the reference, after the arm, and the stage's own
    assert log.count("archive (light)") >= 3, log


def test_a_refused_arm_runs_nothing(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, STUB_UV_FAIL_ON=" arm --out ", STUB_UV_FAIL_CODE="2")
    assert r.returncode == 0, r.stderr  # the pipeline carries on
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p25_i24_b2_k0: FAILED" in log
    assert _calls(stub) == [ARM_CALL]
    assert not (repo / "logs" / "p25_i24_b2_k0.done").exists()


def test_a_reference_that_does_not_reproduce_stops_before_the_arm(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, STUB_UV_FAIL_ON=" check-ref ")
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p25_i24_b2_k0: FAILED" in log
    assert _calls(stub) == ALL[: ALL.index(CHECK) + 1]
    assert (repo / "logs" / f"p25_{REF}.battery.ok").is_file()
    assert not (repo / "logs" / f"p25_{RC}.battery.ok").exists()
    stage_log = (repo / "logs" / "p25_i24_b2_k0.log").read_text()
    assert "does not reproduce artifacts/i24_validation_flow_speedcal_ref.json" in stage_log
    assert (tmp_path / "home" / "final.tgz").is_file()


def test_a_blocked_readout_fails_the_stage(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, STUB_UV_FAIL_ON=" score --out ")
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p25_i24_b2_k0: FAILED" in log
    assert _calls(stub) == ALL


def test_the_ingest_installs_every_p25_output(tmp_path: Path) -> None:
    _, env = _stubs(tmp_path)
    repo = tmp_path / "ingest_repo"
    (repo / "artifacts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    artifacts = [
        "boundary_b2_k0_corridor.json",
        *(f"i24_validation_{lb}.json" for lb in (REF, RC)),
        *(f"i24_b2_ramp_flows_{lb}.json" for lb in (REF, RC)),
        *(f"i24_hard_braking_{lb}.json" for lb in (REF, RC)),
    ]
    scenarios = {
        "i24_replica_flow_rc_speedcal.yaml": (
            "# i24_replica_flow_rc_speedcal: amendment B2 on the k = 0 I-24 arm (docs/I24_DISCHARGE_DIAGNOSIS.md "
            "§8.4.7)\nname: i24_replica_flow_rc_speedcal\n"
        ),
    }
    files = {f"artifacts/{a}": f'{{"name": "{a}"}}' for a in artifacts}
    files |= {f"scenarios/{s}": text for s, text in scenarios.items()}
    files["logs/PIPELINE_EXIT"] = "rc=0\n"
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
    assert "NOT ingested" not in r.stdout, r.stdout
    assert "WARNING" not in r.stdout
    assert "re-score/figures skipped" in r.stdout
    for name in files:
        if not name.startswith("logs/"):
            assert (repo / name).read_text() == files[name], name
