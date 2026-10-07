"""Stage ``p12_i24_b1`` of scripts/gcp/pipeline_i24.sh and its ingest, offline.

``uv``, ``curl``, ``sudo`` and ``nproc`` are stubs on ``PATH`` that record every call;
nothing is simulated (amendment B1, docs/I24_DISCHARGE_DIAGNOSIS.md §8.3).

* Per arm, in the order canonical, dc_refit, dc: the B1 copy (source hash guarded),
  the reference battery and its braking counts, the B1 battery and its braking counts
  (step 3's battery path: 20 seeds, ring rows, 8 analysis processes), then the
  readout over every pair so far.
* A failed copy skips that arm only and fails the stage, which still archives.
* The archive carries each replicate's ``meta.json`` and ``edges.parquet`` and each
  battery's braking counts, never ``trajectories.parquet``.
* The ingest installs the readout, the battery artifacts and the run files.
"""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

from tests.test_scripts.test_p8c_stage import BASH, GCP, _stubs, _tgz

ARMS = [
    ("canonical", "i24_replica_flow_speedcal", "ae5861a4d906", "flow_speedcal_ref"),
    ("dc_refit", "i24_replica_flow_speedcal_dc_refit", "ada3f406504b", "dc_refit"),
    ("dc", "i24_replica_flow_speedcal_dc", "8976a1773674", "dc"),
]
H = "uv run --no-sync python artifacts/i24_discharge_2026-10-07/harness"
RUN = "runs/i24_validation/p12_canonical_b1/8378b1c05b1f/6914975401685141156"


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    (repo / "scripts" / "gcp").mkdir(parents=True)
    (repo / "scripts" / "gcp" / "pipeline_i24.sh").write_text((GCP / "pipeline_i24.sh").read_text())
    (repo / "artifacts").mkdir()
    (repo / "artifacts" / "a.json").write_text("{}")
    (repo / "scenarios").mkdir()
    (repo / "scenarios" / "s.yaml").write_text("name: s\n")
    run = repo / RUN
    (run / "net").mkdir(parents=True)
    for name in ("meta.json", "edges.parquet", "vehicles.parquet", "trajectories.parquet"):
        (run / name).write_text("x")
    (repo / "runs" / "i24_validation" / "p12_canonical_b1" / "hard_braking.json").write_text("{}")
    return repo


def _run_stage(tmp: Path, **extra_env: str) -> tuple[Path, Path, subprocess.CompletedProcess[str]]:
    stub, env = _stubs(tmp)
    env.update(extra_env)
    repo = _repo(tmp)
    r = subprocess.run(
        [
            BASH,
            str(repo / "scripts" / "gcp" / "pipeline_i24.sh"),
            "--no-shutdown",
            "--stages",
            "p12_i24_b1",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return stub, repo, r


def _stage_calls(stub: Path) -> list[str]:
    lines = (stub / "calls.log").read_text().splitlines()
    return [c for c in lines if c.startswith("uv ") and ("p12" in c or "corridor_b1" in c)]


def _expected(arms: list[tuple[str, str, str, str]]) -> list[str]:
    calls, readout = [], ""
    for arm, stem, h, committed in arms:
        calls.append(
            f"{H}/corridor_b1.py make-copy --source scenarios/{stem}.yaml --out scenarios/{stem}_b1.yaml "
            f"--factor 1.2185 --source-hash {h}"
        )
        for side, scn in (("ref", f"scenarios/{stem}.yaml"), ("b1", f"scenarios/{stem}_b1.yaml")):
            label = f"p12_{arm}_{side}"
            calls.append(
                f"uv run --no-sync python scripts/i24_validate.py --scenario {scn} --label {label} "
                "--replicates 20 --procs 14 --analysis-procs 8 --ring-seeds 20"
            )
            calls.append(
                f"{H}/hard_braking.py --artifact artifacts/i24_validation_{label}.json "
                f"--out runs/i24_validation/{label}/hard_braking.json"
            )
        readout += (
            f" --arm {arm} p12_{arm}_ref p12_{arm}_b1 artifacts/i24_validation_{committed}.json"
        )
        calls.append(
            f"{H}/corridor_b1.py evaluate --factor 1.2185 --out artifacts/boundary_b1_corridor.json{readout}"
        )
    return calls


def test_p12_runs_three_pairs_in_order_and_reads_after_each(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path)
    assert r.returncode == 0, r.stderr
    assert (repo / "logs" / "p12_i24_b1.done").is_file(), (
        repo / "logs" / "pipeline.log"
    ).read_text()
    assert _stage_calls(stub) == _expected(ARMS)
    for arm, *_ in ARMS:
        for side in ("ref", "b1"):
            assert (repo / "logs" / f"p12_{arm}_{side}.battery.ok").is_file()
    with tarfile.open(tmp_path / "home" / "final.tgz", "r:gz") as tf:
        names = set(tf.getnames())
    for kept in ("meta.json", "edges.parquet"):
        assert f"{RUN}/{kept}" in names, kept
    assert "runs/i24_validation/p12_canonical_b1/hard_braking.json" in names
    assert not any(n.endswith(("trajectories.parquet", "vehicles.parquet")) for n in names)


def test_a_failed_copy_skips_its_arm_and_fails_the_stage(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(
        tmp_path, STUB_UV_FAIL_ON="make-copy --source scenarios/i24_replica_flow_speedcal_dc.yaml"
    )
    assert r.returncode == 0, r.stderr  # the pipeline carries on
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p12_i24_b1: FAILED" in log and "arm dc skipped" in log
    calls = _stage_calls(stub)
    assert calls[:-1] == _expected(ARMS[:2]) and "make-copy" in calls[-1]
    assert (tmp_path / "home" / "final.tgz").is_file()


def test_the_ingest_installs_the_readout_batteries_and_run_files(tmp_path: Path) -> None:
    _, env = _stubs(tmp_path)
    repo = tmp_path / "ingest_repo"
    (repo / "artifacts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    files = {
        "artifacts/boundary_b1_corridor.json": '{"adoption": {}}',
        "artifacts/i24_validation_p12_canonical_b1.json": "{}",
        "artifacts/i24_validation_p12_canonical_ref.json": "{}",
        "scenarios/i24_replica_flow_speedcal_b1.yaml": (
            "# i24_replica_flow_speedcal_b1: scenarios/i24_replica_flow_speedcal.yaml\nname: "
            "i24_replica_flow_speedcal_b1\n"
        ),
        f"{RUN}/meta.json": "{}",
        f"{RUN}/edges.parquet": "x",
        "runs/i24_validation/p12_canonical_b1/hard_braking.json": "{}",
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
    assert "artifact boundary_b1_corridor.json" in r.stdout
    assert "runs: i24_validation/p12_canonical_b1" in r.stdout
    assert "WARNING" not in r.stdout
    for name in files:
        if not name.startswith("logs/"):
            assert (repo / name).read_text() == files[name], name
