"""Stage ``p22_reports`` (artifacts/p22_reports_2026-10-08/stage_p22_reports.sh.txt) and its ingest, offline.

E13's auto-reports (docs/PRE_FRISCO_PROGRAM.md): each from its committed battery artifact plus one
figure replicate (scripts/regenerate_report.py). ``uv``, ``curl``, ``sudo`` and ``nproc`` are stubs
on ``PATH`` that record every call; the ``uv`` stub plays ``regenerate_report.py plan`` (it writes
``runs/p22/PLAN.json`` and ``pairs.txt`` and prints the plan's lines); nothing is simulated.

* The inserted pipeline block is the stage text byte for byte, once, after p25's block and right
  above the "# 9. Done marker" block; the stage and the adapter name the same seed, script and arms.
* The plan, the figure replicates through scripts/run_pairs.py with the plan's arguments, the ring
  run, the reports; the plan's per-arm lines reach the pipeline log through ``say``.
* No corridor arm planned: run_pairs is not called; the ring run and the reports still are.
* A plan that wrote nothing stops the stage; a refused arm or a failed report fails it at its end,
  every step having run; ``--p22-arms`` replaces the default arms.
* The ingest installs the record and the reports.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from tests.test_scripts.test_p8c_stage import BASH, GCP, _stubs, _tgz

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE = REPO_ROOT / "artifacts" / "p22_reports_2026-10-08" / "stage_p22_reports.sh.txt"
SCRIPT = REPO_ROOT / "scripts" / "regenerate_report.py"
MARKER = "# 9. Done marker"
PY = "uv run --no-sync python"
H = f"{PY} scripts/regenerate_report.py"
SEED = "6914975401685141156"

UV_STUB = r"""#!/bin/bash
echo "uv $*" >> "$STUB_DIR/calls.log"
if [[ "$*" == *"regenerate_report.py plan"* ]]; then
  if [ -z "${STUB_NO_PLAN:-}" ]; then
    mkdir -p runs/p22
    echo '{"arms": []}' > runs/p22/PLAN.json
    printf '%s' "${STUB_PAIRS:-}" > runs/p22/pairs.txt
    echo "p22: e13_a planned: stub"
    echo "p22: e13_b skipped: no battery artifacts/b.json"
  fi
  exit "${STUB_PLAN_CODE:-0}"
fi
if [ -n "${STUB_UV_FAIL_ON:-}" ] && [[ "$*" == *"$STUB_UV_FAIL_ON"* ]]; then
  echo "[stub] exit 3"; exit 3
fi
echo "[stub] ok"
"""

PAIRS = "--expect-hash\ne13_a=abc123abc123\ne13_a=scenarios/a.yaml:6914975401685141156\n"


def _adapter() -> ModuleType:
    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("regenerate_report_p22_stage", SCRIPT)
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
    assert block.startswith("# p22 (opt-in")
    assert sh.count(MARKER) == 1
    assert sh.count(block) == 1, "the pipeline's p22 block is not the stage text"
    p25 = 'stage p25_i24_b2_k0 p25_steps || say "p25_i24_b2_k0 failed; continuing"'
    assert sh.index(p25) < sh.index(block)
    assert sh.index(block) + len(block) == sh.index(MARKER)
    # the option is parsed and documented in the header
    assert '--p22-arms) P22_ARMS_ARG="$2"; shift 2 ;;' in sh
    assert '#       [--p22-arms "LABEL:SCENARIO:BATTERY ..."]' in sh


def test_the_stage_and_the_adapter_name_the_same_seed_script_and_launch() -> None:
    m = _adapter()
    block = STAGE.read_text()
    assert f"P22_H={SCRIPT.relative_to(REPO_ROOT)}\n" in block
    assert f"P22_SEED={m.KEPT_SEED}\n" in block and str(m.KEPT_SEED) == SEED
    assert m.STAGE == "p22_reports"
    assert "--record artifacts/p22_reports.json" in block
    launch = (
        "--machine n2d-standard-16 \\\n#         --zone us-east1-b,us-east1-c,us-east1-d "
        "--bucket gs://<bucket>/p22 \\\n#         --self-delete --via-bucket --data-set i24 "
        "--cap-min 90 --pipeline-args '--stages \"p22_reports\"'"
    )
    assert launch in block
    assert "figures from replicate <seed>; tables from <artifact>" in block


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    (repo / "scripts" / "gcp").mkdir(parents=True)
    (repo / "scripts" / "gcp" / "pipeline_i24.sh").write_text((GCP / "pipeline_i24.sh").read_text())
    (repo / "artifacts").mkdir()
    (repo / "artifacts" / "a.json").write_text("{}")
    (repo / "scenarios").mkdir()
    (repo / "scenarios" / "s.yaml").write_text("name: s\n")
    return repo


def _run_stage(
    tmp: Path, *args: str, **extra_env: str
) -> tuple[Path, Path, subprocess.CompletedProcess[str]]:
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
            "p22_reports",
            *args,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return stub, repo, r


def _calls(stub: Path) -> list[str]:
    keep = ("regenerate_report", "run_pairs")
    return [
        c
        for c in (stub / "calls.log").read_text().splitlines()
        if c.startswith("uv ") and any(k in c for k in keep)
    ]


def _plan(arms: list[str]) -> str:
    return f"{H} plan --seed {SEED} --out runs/p22 " + " ".join(f"--arm {a}" for a in arms)


RUN_PAIRS = (
    f"{PY} scripts/run_pairs.py --out runs/p22 --procs 14 --expect-hash e13_a=abc123abc123 "
    "e13_a=scenarios/a.yaml:6914975401685141156"
)
RING = f"{H} ring-run --plan runs/p22/PLAN.json"
RENDER = f"{H} render --plan runs/p22/PLAN.json --record artifacts/p22_reports.json --i24-observed"


def test_p22_plans_runs_the_replicates_and_the_ring_then_renders(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, STUB_PAIRS=PAIRS)
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert (repo / "logs" / "p22_reports.done").is_file(), log
    assert _calls(stub) == [_plan(list(_adapter().DEFAULT_ARMS)), RUN_PAIRS, RING, RENDER]
    # the plan's per-arm lines reach the pipeline log through say
    assert "p22: e13_a planned: stub" in log
    assert "p22: e13_b skipped: no battery artifacts/b.json" in log


def test_without_a_corridor_arm_run_pairs_is_not_called(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path)
    assert r.returncode == 0, r.stderr
    assert (repo / "logs" / "p22_reports.done").is_file()
    assert _calls(stub) == [_plan(list(_adapter().DEFAULT_ARMS)), RING, RENDER]


def test_a_plan_that_wrote_nothing_stops_the_stage(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, STUB_NO_PLAN="1", STUB_PLAN_CODE="2")
    assert r.returncode == 0, r.stderr  # the pipeline carries on
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p22_reports: FAILED" in log
    assert "p22: no plan was written" in log
    assert _calls(stub) == [_plan(list(_adapter().DEFAULT_ARMS))]


def test_a_refused_arm_fails_the_stage_after_every_step(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, STUB_PAIRS=PAIRS, STUB_PLAN_CODE="1")
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p22_reports: FAILED" in log
    assert _calls(stub) == [_plan(list(_adapter().DEFAULT_ARMS)), RUN_PAIRS, RING, RENDER]


def test_a_failed_report_fails_the_stage(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, STUB_PAIRS=PAIRS, STUB_UV_FAIL_ON=" render --plan ")
    assert _calls(stub)[-1] == RENDER
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p22_reports: FAILED" in log
    assert "p22: a planned report was not written" in log
    assert (tmp_path / "home" / "final.tgz").is_file()


def test_p22_arms_replace_the_default(tmp_path: Path) -> None:
    arms = ["x1:scenarios/s.yaml:artifacts/a.json", "x2::artifacts/b.json"]
    stub, repo, r = _run_stage(tmp_path, "--p22-arms", " ".join(arms))
    assert r.returncode == 0, r.stderr
    assert (repo / "logs" / "p22_reports.done").is_file()
    assert _calls(stub)[0] == _plan(arms)


def test_the_ingest_installs_the_record_and_the_reports(tmp_path: Path) -> None:
    _, env = _stubs(tmp_path)
    repo = tmp_path / "ingest_repo"
    (repo / "artifacts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    files = {
        "artifacts/p22_reports.json": '{"schema": "flowstate.p22_reports/1"}',
        "docs/reports/e13_i24_b2/report.md": "# report\n",
        "docs/reports/e13_i24_b2/figures/speed_contour_00_seed_1.png": "png",
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
    assert "artifact p22_reports.json\n" in r.stdout
    assert "docs/reports updated" in r.stdout
    assert "NOT ingested" not in r.stdout, r.stdout
    for name, text in files.items():
        if not name.startswith("logs/"):
            assert (repo / name).read_text() == text, name
