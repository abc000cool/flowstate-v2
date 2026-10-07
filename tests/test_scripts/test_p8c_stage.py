"""Stage ``p8c_i94_cal_collisions`` of scripts/gcp/pipeline_i24.sh and its ingest, offline.

``uv``, ``curl``, ``sudo`` and ``nproc`` are stubs on ``PATH`` that record every call;
nothing is simulated (docs/I94_CAL_COLLISIONS.md §10).

* The stage runs scripts/run_pairs.py once with stage p8's seven (scenario, seed)
  pairs — the six collision replicates and the ``_dc_cal`` control — at most 7
  processes, both config hashes guarded, then the reader on ``runs/p8c``.
* The archive carries each run's ``meta.json``, ``vehicles.parquet`` and window slice
  and the pair manifest, never ``trajectories.parquet`` or ``net/``.
* A run that does not reproduce (the reader's exit 3) fails the stage, which still
  archives.
* The ingest installs ``artifacts/i94_cal_collisions_trace.json`` and ``runs/p8c``.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GCP = REPO_ROOT / "scripts" / "gcp"
BASH = shutil.which("bash") or "/bin/bash"

P8C_PAIRS = [
    "dc_cal=scenarios/mndot_i94_wb_stpaul_weave_dc_cal.yaml:134183728835869882",
    "dc_cal=scenarios/mndot_i94_wb_stpaul_weave_dc_cal.yaml:6134032994440706937",
    "dc_cal=scenarios/mndot_i94_wb_stpaul_weave_dc_cal.yaml:165503670820534583",
    "dc_cal_netfix=scenarios/mndot_i94_wb_stpaul_weave_dc_cal_netfix.yaml:134183728835869882",
    "dc_cal_netfix=scenarios/mndot_i94_wb_stpaul_weave_dc_cal_netfix.yaml:165503670820534583",
    "dc_cal_netfix=scenarios/mndot_i94_wb_stpaul_weave_dc_cal_netfix.yaml:677105600768189526",
    "dc_cal_netfix=scenarios/mndot_i94_wb_stpaul_weave_dc_cal_netfix.yaml:6953598295321596746",
]

UV_STUB = r"""#!/bin/bash
echo "uv $*" >> "$STUB_DIR/calls.log"
if [ -n "${STUB_UV_FAIL_ON:-}" ] && [[ "$*" == *"$STUB_UV_FAIL_ON"* ]]; then echo "[stub] exit 3"; exit 3; fi
echo "[stub] ok"
"""


def _stubs(tmp: Path) -> tuple[Path, dict[str, str]]:
    stub = tmp / "stub"
    (stub / "bin").mkdir(parents=True)
    bodies = {
        "uv": UV_STUB,
        "curl": "#!/bin/bash\nexit 22\n",
        "sudo": '#!/bin/bash\necho "sudo $*" >> "$STUB_DIR/calls.log"\nexit 0\n',
        "nproc": "#!/bin/bash\necho 16\n",
    }
    for name, body in bodies.items():
        p = stub / "bin" / name
        p.write_text(body)
        p.chmod(0o755)
    home = tmp / "home"
    home.mkdir()
    env = {
        "PATH": f"{stub / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
        "STUB_DIR": str(stub),
        "HOME": str(home),
        "LANG": "C",
        "PIPELINE_BUCKET": "",
        "PIPELINE_SELF_DELETE": "0",
    }
    return stub, env


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    (repo / "scripts" / "gcp").mkdir(parents=True)
    shutil.copy(GCP / "pipeline_i24.sh", repo / "scripts" / "gcp" / "pipeline_i24.sh")
    (repo / "artifacts").mkdir()
    (repo / "artifacts" / "a.json").write_text("{}")
    (repo / "scenarios").mkdir()
    (repo / "scenarios" / "s.yaml").write_text("name: s\n")
    run = repo / "runs" / "p8c" / "dc_cal" / "beaaa710e6b3" / "134183728835869882"
    (run / "net").mkdir(parents=True)
    for name in (
        "meta.json",
        "vehicles.parquet",
        "collision_slice.parquet",
        "trajectories.parquet",
        "edges.parquet",
    ):
        (run / name).write_text("x")
    (run / "net" / "demand.rou.xml").write_text("<routes/>")
    (repo / "runs" / "p8c" / "PAIRS.json").write_text("{}")
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
            "p8c_i94_cal_collisions",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return stub, repo, r


def _stage_calls(stub: Path) -> list[str]:
    """The stage's own ``uv`` calls (the pipeline also makes an unconditional ``python -``
    read of the zipper-merge choice, which is not this stage's)."""
    lines = (stub / "calls.log").read_text().splitlines()
    return [c for c in lines if c.startswith("uv ") and ("p8c" in c or "run_pairs" in c)]


def test_p8c_runs_the_seven_pairs_in_one_wave_then_the_reader(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path)
    assert r.returncode == 0, r.stderr
    assert (repo / "logs" / "p8c_i94_cal_collisions.done").is_file(), (
        repo / "logs" / "pipeline.log"
    ).read_text()
    uv = _stage_calls(stub)
    assert len(uv) == 2, uv
    runs = uv[0].split()
    assert runs[:7] == [
        "uv",
        "run",
        "--no-sync",
        "python",
        "scripts/run_pairs.py",
        "--out",
        "runs/p8c",
    ]
    assert runs[runs.index("--procs") + 1] == "7"  # nproc 16 -> PROCS 14, capped at the 7 runs
    assert "--expect-hash dc_cal=beaaa710e6b3 --expect-hash dc_cal_netfix=182e3ec2f500" in uv[0]
    assert [a for a in runs if ".yaml:" in a] == P8C_PAIRS
    assert uv[1] == (
        "uv run --no-sync python scripts/i94_collision_trace.py --root runs/p8c "
        "--out artifacts/i94_cal_collisions_trace.json"
    )
    with tarfile.open(tmp_path / "home" / "final.tgz", "r:gz") as tf:
        names = set(tf.getnames())
    run = "runs/p8c/dc_cal/beaaa710e6b3/134183728835869882"
    for kept in ("meta.json", "vehicles.parquet", "collision_slice.parquet"):
        assert f"{run}/{kept}" in names, kept
    assert "runs/p8c/PAIRS.json" in names
    assert not any(n.endswith(("trajectories.parquet", "edges.parquet")) for n in names)
    assert not any("/net/" in n or n.endswith("/net") for n in names)


def test_a_run_that_does_not_reproduce_fails_the_stage_and_still_archives(tmp_path: Path) -> None:
    stub, repo, r = _run_stage(tmp_path, STUB_UV_FAIL_ON="i94_collision_trace.py")
    assert r.returncode == 0, r.stderr  # the pipeline carries on
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "stage p8c_i94_cal_collisions: FAILED" in log
    assert "does not reproduce stage p8" in log
    assert not (repo / "logs" / "p8c_i94_cal_collisions.done").exists()
    assert len(_stage_calls(stub)) == 2
    assert (tmp_path / "home" / "final.tgz").is_file()


def _tgz(path: Path, files: dict[str, str]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_the_ingest_installs_the_trace_and_the_run_slices(tmp_path: Path) -> None:
    _, env = _stubs(tmp_path)
    repo = tmp_path / "ingest_repo"
    (repo / "artifacts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    run = "runs/p8c/dc_cal_netfix/182e3ec2f500/165503670820534583"
    files = {
        "artifacts/i94_cal_collisions_trace.json": '{"reproduced": true}',
        "runs/p8c/PAIRS.json": "{}",
        f"{run}/meta.json": "{}",
        f"{run}/collision_slice.parquet": "x",
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
    assert "artifact i94_cal_collisions_trace.json" in r.stdout and "runs: p8c" in r.stdout
    for name in files:
        if not name.startswith("logs/"):
            assert (repo / name).read_text() == files[name], name
