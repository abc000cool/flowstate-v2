"""Stage ``p14_i24_b1b2`` of scripts/gcp/pipeline_i24.sh and its ingest, offline.

``uv``, ``curl``, ``sudo`` and ``nproc`` are stubs on ``PATH`` that record every call;
nothing is simulated (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.5).

* Part (i): the B1 copy of the committed B2 arm (source hash guarded), B2 alone and B1 + B2
  through stage p12's battery path (20 seeds, ring rows, 8 analysis processes, braking counts),
  their ramp flows, the readout; then an interim archive.
* Part (ii) by the rule fixed in §8.4.5 (``corridor_b1b2.py select``): B1 + B2 (exit 0: the B1
  copy of the refit base, the fit, its battery, ramp flows, the readout with ``--resequence
  b1b2``), B2 alone (exit 10: the fit on the committed base), or nothing (exit 3: the stage
  fails, the archive stands).
* A refused B1 copy runs nothing.
* The archive carries each replicate's ``meta.json`` and ``edges.parquet`` and each battery's
  braking counts, never ``trajectories.parquet`` or ``vehicles.parquet``.
* The ingest installs the readout, the batteries, the reductions, the fit and the scenarios.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tarfile
from pathlib import Path

from tests.test_scripts.test_p8c_stage import BASH, GCP, _stubs, _tgz

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED = "6914975401685141156"  # spawn_seeds(42, 20)[0], the step-3 batteries' first seed
RUN = f"runs/i24_validation/p14_b1b2/e19e5ab64186/{SEED}"
H1 = "uv run --no-sync python artifacts/i24_discharge_2026-10-07/harness/corridor_b1.py"
H2 = "uv run --no-sync python artifacts/i24_discharge_2026-10-07/harness_b2/corridor_b2.py"
H3 = "uv run --no-sync python artifacts/i24_discharge_2026-10-07/harness_b1b2/corridor_b1b2.py"
LABELS = ("p14_b2_ref", "p14_b1b2", "p14_refit2_b1b2", "p14_refit2_b2")

# the uv stub of test_p8c_stage, with a chosen exit status for the matching call (``select``'s 10)
UV_STUB = r"""#!/bin/bash
echo "uv $*" >> "$STUB_DIR/calls.log"
if [ -n "${STUB_UV_FAIL_ON:-}" ] && [[ "$*" == *"$STUB_UV_FAIL_ON"* ]]; then
  echo "[stub] exit ${STUB_UV_FAIL_CODE:-3}"; exit "${STUB_UV_FAIL_CODE:-3}"
fi
echo "[stub] ok"
"""


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    (repo / "scripts" / "gcp").mkdir(parents=True)
    (repo / "scripts" / "gcp" / "pipeline_i24.sh").write_text((GCP / "pipeline_i24.sh").read_text())
    (repo / "artifacts").mkdir()
    (repo / "artifacts" / "a.json").write_text("{}")
    # the batteries' artifacts as scripts/i24_validate.py would leave them (the reductions read them)
    for label in LABELS:
        (repo / "artifacts" / f"i24_validation_{label}.json").write_text("{}")
    (repo / "scenarios").mkdir()
    (repo / "scenarios" / "s.yaml").write_text("name: s\n")
    run = repo / RUN
    (run / "net").mkdir(parents=True)
    for name in ("meta.json", "edges.parquet", "vehicles.parquet", "trajectories.parquet"):
        (run / name).write_text("x")
    (repo / "runs" / "i24_validation" / "p14_b1b2" / "hard_braking.json").write_text("{}")
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
            "p14_i24_b1b2",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return stub, repo, r


def _stage_calls(stub: Path) -> list[str]:
    lines = (stub / "calls.log").read_text().splitlines()
    return [
        c
        for c in lines
        if c.startswith("uv ") and ("p14" in c or "harness" in c or "i24_fit_demand_scale" in c)
    ]


def _battery(label: str, scn: str) -> list[str]:
    return [
        f"uv run --no-sync python scripts/i24_validate.py --scenario {scn} --label {label} "
        "--replicates 20 --procs 14 --analysis-procs 8 --ring-seeds 20",
        f"{H1.replace('corridor_b1.py', 'hard_braking.py')} --artifact artifacts/i24_validation_{label}.json "
        f"--out runs/i24_validation/{label}/hard_braking.json",
    ]


def _reduce(label: str) -> str:
    return (
        f"{H2} reduce --battery artifacts/i24_validation_{label}.json "
        f"--out artifacts/i24_b2_ramp_flows_{label}.json"
    )


PART_I = [
    f"{H1} make-copy --source scenarios/i24_replica_flow_rc_speedcal_dc_refit.yaml "
    "--out scenarios/i24_replica_flow_rc_speedcal_dc_refit_b1.yaml --factor 1.2185 "
    "--source-hash 909b89f298c5 --stage p14_i24_b1b2",
    *_battery("p14_b2_ref", "scenarios/i24_replica_flow_rc_speedcal_dc_refit.yaml"),
    *_battery("p14_b1b2", "scenarios/i24_replica_flow_rc_speedcal_dc_refit_b1.yaml"),
    _reduce("p14_b2_ref"),
    _reduce("p14_b1b2"),
    f"{H3} evaluate --out artifacts/boundary_b1b2_corridor.json",
    f"{H3} select --readout artifacts/boundary_b1b2_corridor.json",
]


def _part_ii(arm: str) -> list[str]:
    b1 = arm == "b1b2"
    base = f"scenarios/i24_replica_flow_rc_corrected_dc{'_b1' if b1 else ''}.yaml"
    scn = f"scenarios/i24_replica_flow_rc_speedcal_dc_refit2{'_b1' if b1 else ''}.yaml"
    fit = f"artifacts/demand_scale_i24_flow_rc{'_b1' if b1 else ''}.json"
    label = f"p14_refit2_{arm}"
    calls = []
    if b1:
        calls.append(
            f"{H1} make-copy --source scenarios/i24_replica_flow_rc_corrected_dc.yaml --out {base} "
            "--factor 1.2185 --source-hash 219f7db55a74 --stage p14_i24_b1b2"
        )
    calls += [
        f"uv run --no-sync python scripts/i24_fit_demand_scale.py --base corrected --base-yaml {base} "
        "--fleet-artifact artifacts/idm_i24_capacity_amax_k1.0.json --procs 14 --write-scenario "
        f"--out {fit} --scenario-out {scn} --name {Path(scn).stem}",
        *_battery(label, scn),
        _reduce(label),
        f"{H3} evaluate --out artifacts/boundary_b1b2_corridor.json --resequence {arm}",
    ]
    return calls


def test_p14_runs_part_i_then_the_resequence_on_b1b2_when_the_reading_holds(
    tmp_path: Path,
) -> None:
    stub, repo, r = _run_stage(tmp_path)
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert (repo / "logs" / "p14_i24_b1b2.done").is_file(), log
    assert _stage_calls(stub) == PART_I + _part_ii("b1b2")
    for label in ("p14_b2_ref", "p14_b1b2", "p14_refit2_b1b2"):
        assert (repo / "logs" / f"{label}.battery.ok").is_file(), label
    assert (repo / "logs" / "p14_fit_b1b2.ok").is_file()
    assert "the re-sequence runs on b1b2" in log
    # the interim archive after part (i) and the stage's own, before the exit trap's two
    assert log.count("archive (light)") >= 3, log
    with tarfile.open(tmp_path / "home" / "final.tgz", "r:gz") as tf:
        names = set(tf.getnames())
    for kept in ("meta.json", "edges.parquet"):
        assert f"{RUN}/{kept}" in names, kept
    assert "runs/i24_validation/p14_b1b2/hard_braking.json" in names
    assert not any(n.endswith(("trajectories.parquet", "vehicles.parquet")) for n in names)


def test_p14_resequences_b2_alone_when_the_reading_does_not_hold(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, STUB_UV_FAIL_ON=" select ", STUB_UV_FAIL_CODE="10")
    assert r.returncode == 0, r.stderr
    assert (repo / "logs" / "p14_i24_b1b2.done").is_file()
    assert _stage_calls(stub) == PART_I + _part_ii("b2")
    assert (repo / "logs" / "p14_fit_b2.ok").is_file()


def test_p14_does_not_resequence_an_undetermined_reading(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, STUB_UV_FAIL_ON=" select ", STUB_UV_FAIL_CODE="3")
    assert r.returncode == 0, r.stderr  # the pipeline carries on
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p14_i24_b1b2: FAILED" in log
    assert _stage_calls(stub) == PART_I
    assert not (repo / "logs" / "p14_i24_b1b2.done").exists()
    assert (tmp_path / "home" / "final.tgz").is_file()


def test_a_refused_b1_copy_runs_nothing(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, STUB_UV_FAIL_ON="--source-hash 909b89f298c5")
    assert r.returncode == 0, r.stderr
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p14_i24_b1b2: FAILED" in log
    assert _stage_calls(stub) == PART_I[:1]


def test_the_stage_and_the_readout_name_the_same_files() -> None:
    """corridor_b1b2.py RESEQ (what the readout reads) against what p14_steps writes."""
    path = (
        REPO_ROOT / "artifacts" / "i24_discharge_2026-10-07" / "harness_b1b2" / "corridor_b1b2.py"
    )
    spec = importlib.util.spec_from_file_location("p14_stage_cbb", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    sh = (GCP / "pipeline_i24.sh").read_text()
    for arm, plan in mod.RESEQ.items():
        for key in ("fit", "scenario", "label"):
            assert plan[key] in sh, (arm, key)
        base = re.search(r"P14_BASE_B1=(\S+)" if arm == "b1b2" else r"P14_BASE=(\S+)", sh)
        assert base is not None and base.group(1) == plan["base"], arm
    assert f"P14_POP={mod.POP}" in sh
    assert f"P14_B2_HASH={mod.B2_HASH}" in sh
    assert mod.REF_LABEL in sh and mod.ARM_LABEL in sh


def test_the_ingest_installs_every_p14_output_and_the_run_files(tmp_path: Path) -> None:
    _, env = _stubs(tmp_path)
    repo = tmp_path / "ingest_repo"
    (repo / "artifacts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    artifacts = [
        "boundary_b1b2_corridor.json",
        "demand_scale_i24_flow_rc_b1.json",
        "demand_scale_i24_flow_rc.json",
        *(f"i24_validation_{lb}.json" for lb in LABELS),
        *(f"i24_b2_ramp_flows_{lb}.json" for lb in LABELS),
    ]
    scenarios = {
        "i24_replica_flow_rc_speedcal_dc_refit_b1.yaml": (
            "# i24_replica_flow_rc_speedcal_dc_refit_b1: scenarios/i24_replica_flow_rc_speedcal_dc_refit.yaml "
            "(config hash 909b89f298c5) with amendment B1\nname: i24_replica_flow_rc_speedcal_dc_refit_b1\n"
        ),
        "i24_replica_flow_rc_corrected_dc_b1.yaml": (
            "# i24_replica_flow_rc_corrected_dc_b1: scenarios/i24_replica_flow_rc_corrected_dc.yaml "
            "(config hash 219f7db55a74) with amendment B1\nname: i24_replica_flow_rc_corrected_dc_b1\n"
        ),
        # the fitter's header names the scenario, then a dash (no "name:" form, so no header check)
        "i24_replica_flow_rc_speedcal_dc_refit2_b1.yaml": (
            "# i24_replica_flow_rc_speedcal_dc_refit2_b1 — the corrected-demand replica\n"
            "name: i24_replica_flow_rc_speedcal_dc_refit2_b1\n"
        ),
    }
    files = {f"artifacts/{a}": f'{{"name": "{a}"}}' for a in artifacts}
    files |= {f"scenarios/{s}": text for s, text in scenarios.items()}
    files |= {
        f"{RUN}/meta.json": '{"run": "b1b2"}',
        f"{RUN}/edges.parquet": "edges",
        "runs/i24_validation/p14_b1b2/hard_braking.json": "{}",
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
    assert "runs: i24_validation/p14_b1b2" in r.stdout
    assert "NOT ingested" not in r.stdout, r.stdout
    assert "WARNING" not in r.stdout
    for name in files:
        if not name.startswith("logs/"):
            assert (repo / name).read_text() == files[name], name
