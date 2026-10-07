"""scripts/gcp/*.sh: the stop guarantees, offline, with stubbed cloud tools.

No cloud call is made: ``gcloud``, ``curl`` (the metadata server), ``sudo``, ``shutdown``,
``sleep``, ``uv`` and ``pgrep`` are stubs on ``PATH`` that record every call. The stub
``gcloud`` keeps a bucket as a directory, an instance as a small state file, and runs
``compute ssh`` commands locally with ``/root`` mapped into the sandbox.

* Every script parses (``bash -n``).
* The pipeline's EXIT trap (2026-10-07 review, finding 1): with --self-delete it deletes the
  instance and never powers it off: after a failed full upload (once the light archive is
  uploaded again), with no archive in the bucket at all, under SIGTERM (light archive only);
  a failed delete is retried and leaves the instance up, not stopped. Without --self-delete it
  powers off. Every archive carries logs/INSTANCE_ID.
* The idle guard: a --self-delete instance whose delete fails is never powered off; any other
  falls back to a power-off.
* The watcher: refuses a bucket archive another instance wrote; restarts a stopped instance
  under a server-side max-run-duration instead of a guest power-off; reads a --via-bucket
  pipeline's /root home; arms the fetch-window power-off only without --self-delete.
* The launcher (--via-bucket): a failed read of the STARTED marker does not delete the
  running VM; the serial console is a second channel; the printed watcher command carries
  --deadline-min CAP+20, the instance id and /root; the penetration sweep's three-level run
  tree is shipped for resume.
* The ingest script installs stage 23's and stage 24's outputs and names what it skips.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GCP = REPO_ROOT / "scripts" / "gcp"
BASH = shutil.which("bash") or "/bin/bash"

GCLOUD_STUB = r'''#!__PYTHON__
"""Stub gcloud: records calls, keeps a bucket directory and one instance's state."""
import json, os, shutil, subprocess, sys
from pathlib import Path

D = Path(os.environ["STUB_DIR"])
cfg = json.loads((D / "config.json").read_text())
sp = D / "state.json"
state = json.loads(sp.read_text()) if sp.exists() else {}
args = sys.argv[1:]
with open(D / "calls.log", "a") as f:
    f.write("gcloud " + " ".join(args) + "\n")
VALUE_FLAGS = {"--project", "--zone", "--format", "--machine-type", "--command", "--metadata",
               "--metadata-from-file", "--labels", "--member", "--role", "--image-family",
               "--image-project", "--boot-disk-size", "--boot-disk-type"}
pos, i = [], 0
while i < len(args):
    a = args[i]
    if a.startswith("--"):
        i += 2 if (a in VALUE_FLAGS and "=" not in a) else 1
        continue
    pos.append(a)
    i += 1

def opt(name):
    for j, a in enumerate(args):
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
        if a == name and j + 1 < len(args):
            return args[j + 1]
    return None

def save():
    sp.write_text(json.dumps(state))

def bump(key):
    state[key] = state.get(key, 0) + 1
    save()
    return state[key]

def fails(key, n):
    v = cfg.get(key)
    return v == "all" or (isinstance(v, list) and n in v)

def gs(url):
    return D / "bucket" / url[len("gs://"):]

def to_sandbox(text):
    return text.replace("/root", str(D / "root")).replace("/tmp/flowstate_fetch", str(D / "tmp_flowstate_fetch"))

def from_sandbox(text):
    return text.replace(str(D / "root"), "/root")

cmd = pos[:3]
if pos[:2] == ["config", "get-value"]:
    print("test-project")
elif pos[:2] == ["storage", "cp"]:
    n = bump("cp")
    if fails("cp_fail", n):
        sys.exit(1)
    *srcs, dest = pos[2:]
    for s in srcs:
        if s.startswith("gs://"):
            src = gs(s)
            if not src.exists():
                sys.exit(1)
            shutil.copy(src, dest)
        else:
            t = gs(dest + os.path.basename(s)) if dest.endswith("/") else gs(dest)
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(s, t)
            (D / "uploads").mkdir(exist_ok=True)
            shutil.copy(s, D / "uploads" / f"{n:02d}_{os.path.basename(s)}")
elif pos[:2] == ["storage", "ls"]:
    url = pos[2]
    if cfg.get("started_marker") and url.endswith("/STARTED"):
        sys.exit(0)
    sys.exit(0 if gs(url).exists() else 1)
elif pos[:2] == ["storage", "cat"]:
    if cfg.get("cat_fail"):
        sys.exit(1)
    print(gs(pos[2]).read_text())
elif pos[:2] == ["storage", "buckets"]:
    pass
elif pos[:2] == ["compute", "instances"]:
    verb = pos[2]
    if verb == "create":
        meta = opt("--metadata-from-file") or ""
        for item in meta.split(","):
            k, _, v = item.partition("=")
            if k == "startup-script":
                shutil.copy(v, D / "startup-script.sh")
        state["status"] = "RUNNING"
        save()
    elif verb == "describe":
        if state.get("deleted"):
            sys.exit(1)
        fmt = opt("--format") or ""
        if fmt == "value(status)":
            print(state.get("status", cfg.get("status", "RUNNING")))
        elif fmt == "value(id)":
            print(cfg.get("instance_id", "111"))
        elif fmt.startswith("value(serviceAccounts"):
            print("sa@test")
        elif fmt == "value(machineType)":
            print(state.get("machine_type", "n2-standard-32"))
    elif verb == "delete":
        n = bump("delete")
        if fails("delete_fail", n):
            sys.exit(1)
        state["deleted"] = True
        save()
    elif verb == "list":
        if not state.get("deleted"):
            print(cfg.get("vm", "vm"))
    elif verb == "set-machine-type":
        state["machine_type"] = opt("--machine-type")
        save()
    elif verb == "start":
        state["status"] = "RUNNING"
        save()
    elif verb == "get-serial-port-output":
        print(cfg.get("serial", ""))
elif pos[:2] == ["compute", "ssh"]:
    env = dict(os.environ, HOME=str(D / "userhome"),
               PATH=f"{D / 'remote_bin'}:{D / 'bin'}:/usr/bin:/bin")
    r = subprocess.run([os.environ.get("STUB_BASH", "bash"), "-c", to_sandbox(opt("--command"))],
                       env=env, capture_output=True, text=True)
    sys.stdout.write(from_sandbox(r.stdout))
    sys.stderr.write(r.stderr)
    sys.exit(r.returncode)
elif pos[:2] == ["compute", "scp"]:
    src, dest = pos[2], pos[3]
    remote = src.split(":", 1)[1].replace("~", str(D / "userhome"))
    p = Path(to_sandbox(remote))
    if not p.exists():
        sys.exit(1)
    shutil.copy(p, dest)
'''

CURL_STUB = r"""#!/bin/bash
url="${@: -1}"
case "$url" in
  */instance/id) echo "${STUB_INSTANCE_ID:-111}" ;;
  */instance/name) echo vm ;;
  */instance/zone) echo projects/1/zones/us-west1-b ;;
  */attributes/flowstate-self-delete) [ -n "${STUB_SELF_DELETE_ATTR:-}" ] || exit 22; echo "$STUB_SELF_DELETE_ATTR" ;;
  *) exit 22 ;;
esac
"""

RECORD_ONLY = r"""#!/bin/bash
echo "$(basename "$0") $*" >> "$STUB_DIR/calls.log"
"""

SLEEP_STUB = r"""#!/bin/bash
echo "sleep $*" >> "$STUB_DIR/calls.log"
if [ -n "${STUB_SLEEP_LIMIT:-}" ]; then
  n=$(( $(cat "$STUB_DIR/sleep_count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STUB_DIR/sleep_count"
  [ "$n" -ge "$STUB_SLEEP_LIMIT" ] && kill -TERM "$PPID"
fi
exit 0
"""

SUDO_STUB = r"""#!/bin/bash
echo "sudo $*" >> "$STUB_DIR/calls.log"
[ "$1" = shutdown ] && exit 0
exec "$@"
"""

UV_STUB = r"""#!/bin/bash
echo "uv $*" >> "$STUB_DIR/calls.log"
if [ -n "${STUB_UV_BLOCK:-}" ]; then touch "$STUB_DIR/uv_started"; exec /bin/sleep 60; fi
echo "[stub] ok"
"""

STAT_SHIM = r"""#!/bin/bash
# GNU `stat -c` on the "remote" side, also on a BSD host
if [ "$1" = "-c" ] && ! /usr/bin/stat -c %s / >/dev/null 2>&1; then
  fmt="${2//%s/%z}"; fmt="${fmt//%Y/%m}"; shift 2; exec /usr/bin/stat -f "$fmt" "$@"
fi
exec /usr/bin/stat "$@"
"""


class Sandbox:
    """A stub directory, its call log and config, and the environment to run a script in."""

    def __init__(self, root: Path, config: dict[str, Any] | None = None) -> None:
        self.dir = root / "stub"
        self.bin = self.dir / "bin"
        self.bin.mkdir(parents=True)
        (self.dir / "remote_bin").mkdir()
        (self.dir / "userhome").mkdir()
        (self.dir / "root").mkdir()
        self.write_config(config or {})
        stubs = {
            "gcloud": GCLOUD_STUB.replace("__PYTHON__", sys.executable),
            "curl": CURL_STUB,
            "sudo": SUDO_STUB,
            "shutdown": RECORD_ONLY,
            "sleep": SLEEP_STUB,
            "uv": UV_STUB,
            "nproc": "#!/bin/bash\necho 4\n",
            "pgrep": "#!/bin/bash\nexit 1\n",
            "caffeinate": '#!/bin/bash\nexec "$@"\n',
        }
        for name, body in stubs.items():
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        shim = self.dir / "remote_bin" / "stat"
        shim.write_text(STAT_SHIM)
        shim.chmod(0o755)

    def write_config(self, config: dict[str, Any]) -> None:
        (self.dir / "config.json").write_text(json.dumps(config))

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "STUB_DIR": str(self.dir),
            "STUB_BASH": BASH,
            "HOME": str(self.dir.parent / "home"),
            "LANG": "C",
        }
        Path(env["HOME"]).mkdir(exist_ok=True)
        env.update(extra)
        return env

    def calls(self) -> list[str]:
        log = self.dir / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def count(self, prefix: str) -> int:
        return sum(1 for c in self.calls() if c.startswith(prefix))

    def uploads(self) -> list[str]:
        d = self.dir / "uploads"
        return sorted(p.name for p in d.iterdir()) if d.exists() else []


def _members(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, "r:gz") as tf:
        return {
            m.name: (tf.extractfile(m).read() if m.isfile() else b"")  # type: ignore[union-attr]
            for m in tf.getmembers()
        }


def _tgz(path: Path, files: dict[str, str]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


@pytest.mark.parametrize("script", sorted(p.name for p in GCP.glob("*.sh")))
def test_every_gcp_script_parses(script: str) -> None:
    subprocess.run([BASH, "-n", str(GCP / script)], check=True)


# --- the pipeline's EXIT trap ------------------------------------------------------------------


def _pipeline_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts" / "gcp").mkdir(parents=True)
    shutil.copy(GCP / "pipeline_i24.sh", repo / "scripts" / "gcp" / "pipeline_i24.sh")
    (repo / "artifacts").mkdir()
    (repo / "artifacts" / "a.json").write_text("{}")
    (repo / "scenarios").mkdir()
    (repo / "scenarios" / "s.yaml").write_text("name: s\n")
    return repo


def _run_pipeline(
    tmp_path: Path, config: dict[str, Any], *, bucket: str = "gs://b/p", self_delete: str = "1"
) -> tuple[Sandbox, subprocess.CompletedProcess[str], Path]:
    sb = Sandbox(tmp_path, config)
    repo = _pipeline_repo(tmp_path)
    env = sb.env(PIPELINE_BUCKET=bucket, PIPELINE_SELF_DELETE=self_delete)
    r = subprocess.run(
        [BASH, str(repo / "scripts" / "gcp" / "pipeline_i24.sh"), "--stages", "none_selected"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return sb, r, repo


def test_self_delete_after_both_uploads_never_powers_off(tmp_path: Path) -> None:
    sb, r, repo = _run_pipeline(tmp_path, {})
    assert r.returncode == 0, r.stderr
    assert sb.count("gcloud storage cp") == 2  # light, full
    assert sb.count("gcloud compute instances delete vm --zone us-west1-b") == 1
    assert not any(c.startswith("sudo shutdown") for c in sb.calls())
    final = _members(sb.dir / "bucket" / "b" / "p" / "final.tgz")
    assert final["logs/INSTANCE_ID"].strip() == b"111"
    assert final["logs/PIPELINE_EXIT"].startswith(b"rc=0")
    assert "this run's archive is in gs://b/p" in (repo / "logs" / "pipeline.log").read_text()


def test_a_failed_full_upload_retries_the_light_one_then_deletes(tmp_path: Path) -> None:
    sb, r, repo = _run_pipeline(tmp_path, {"cp_fail": [2]})
    assert r.returncode == 0, r.stderr
    # light (ok), full (failed), the light archive again (ok)
    assert sb.uploads() == ["01_final.tgz", "03_final_light.tgz"]
    assert sb.count("gcloud compute instances delete") == 1
    assert not any(c.startswith("sudo shutdown") for c in sb.calls())
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "uploading the light archive again" in log
    assert "this run's archive is in gs://b/p" in log
    assert "logs/PIPELINE_EXIT" in _members(sb.dir / "bucket" / "b" / "p" / "final.tgz")


def test_no_archive_in_the_bucket_still_deletes_never_stops(tmp_path: Path) -> None:
    sb, r, repo = _run_pipeline(tmp_path, {"cp_fail": "all"})
    assert r.returncode == 0, r.stderr
    # light twice, full once, the light one again four times
    assert sb.count("gcloud storage cp") == 2 + 1 + 4
    assert sb.count("gcloud compute instances delete") == 1
    assert not any(c.startswith("sudo shutdown") for c in sb.calls())
    assert "NO archive of this run reached gs://b/p" in (repo / "logs" / "pipeline.log").read_text()


def test_a_failing_self_delete_is_retried_and_leaves_the_instance_up(tmp_path: Path) -> None:
    sb, r, repo = _run_pipeline(tmp_path, {"delete_fail": "all"})
    assert r.returncode == 0, r.stderr
    assert sb.count("gcloud compute instances delete") == 3
    assert not any(c.startswith("sudo shutdown") for c in sb.calls())
    assert "NOT powering off" in (repo / "logs" / "pipeline.log").read_text()


def test_without_self_delete_the_trap_powers_off(tmp_path: Path) -> None:
    sb, r, _ = _run_pipeline(tmp_path, {}, self_delete="0")
    assert r.returncode == 0, r.stderr
    assert sb.count("gcloud compute instances delete") == 0
    assert sb.count("sudo shutdown -h +3") == 1


def test_self_delete_without_a_bucket_is_ignored(tmp_path: Path) -> None:
    sb, r, repo = _run_pipeline(tmp_path, {}, bucket="")
    assert r.returncode == 0, r.stderr
    assert sb.count("gcloud") == 0
    assert sb.count("sudo shutdown -h +3") == 1
    assert "without PIPELINE_BUCKET: ignored" in (repo / "logs" / "pipeline.log").read_text()


def test_sigterm_ships_the_light_archive_and_deletes(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, {})
    repo = _pipeline_repo(tmp_path)
    env = sb.env(PIPELINE_BUCKET="gs://b/p", PIPELINE_SELF_DELETE="1", STUB_UV_BLOCK="1")
    proc = subprocess.Popen(
        [
            BASH,
            str(repo / "scripts" / "gcp" / "pipeline_i24.sh"),
            "--stages",
            "p5_i94_netfix_probe",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        for _ in range(200):
            if (sb.dir / "uv_started").exists():
                break
            time.sleep(0.05)
        assert (sb.dir / "uv_started").exists()
        os.killpg(proc.pid, signal.SIGTERM)  # systemd's stop: every process of the unit
        assert proc.wait(timeout=60) == 143
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
    log = (repo / "logs" / "pipeline.log").read_text()
    assert "SIGTERM received" in log and "system stop: light archive only" in log
    assert sb.count("gcloud storage cp") == 1
    assert sb.count("gcloud compute instances delete") == 1
    assert not any(c.startswith("sudo shutdown") for c in sb.calls())


# --- the idle guard ----------------------------------------------------------------------------


def _run_guard(tmp_path: Path, config: dict[str, Any], **extra: str) -> Sandbox:
    sb = Sandbox(tmp_path, config)
    uptime = tmp_path / "uptime"
    uptime.write_text("90000.00 1.00\n")
    env = sb.env(
        GRACE_UPTIME_S="0",
        IDLE_GUARD_LOG=str(tmp_path / "guard.log"),
        IDLE_GUARD_UPTIME_FILE=str(uptime),
        STUB_SLEEP_LIMIT="8",
        **extra,
    )
    r = subprocess.run(
        [BASH, str(GCP / "idle_guard.sh")], env=env, capture_output=True, text=True, timeout=60
    )
    assert r.returncode != 0  # ended by the sleep stub's SIGTERM
    return sb


def test_idle_guard_never_powers_off_a_self_delete_instance(tmp_path: Path) -> None:
    sb = _run_guard(tmp_path, {"delete_fail": "all"}, STUB_SELF_DELETE_ATTR="1")
    assert sb.count("gcloud compute instances delete vm") >= 2  # retried at every check
    assert sb.count("shutdown") == 0
    assert "NOT powering off" in (tmp_path / "guard.log").read_text()


def test_idle_guard_powers_off_only_without_a_delete_grant(tmp_path: Path) -> None:
    sb = _run_guard(tmp_path, {"delete_fail": "all"})
    assert sb.count("shutdown -h now") >= 1
    assert sb.count("gcloud compute instances delete vm") >= 2  # one retry before the power-off


def test_idle_guard_deletes_an_idle_instance(tmp_path: Path) -> None:
    sb = _run_guard(tmp_path, {}, STUB_SELF_DELETE_ATTR="1")
    assert sb.count("gcloud compute instances delete vm") >= 1
    assert sb.count("shutdown") == 0
    assert "delete issued" in (tmp_path / "guard.log").read_text()


# --- the watcher -------------------------------------------------------------------------------

EXIT_ARCHIVE = {"logs/PIPELINE_EXIT": "rc=0 2026-10-07T00:00:00Z\n", "logs/x.done": "1\n"}


def _run_watcher(
    tmp_path: Path, sb: Sandbox, *args: str, **extra: str
) -> subprocess.CompletedProcess[str]:
    dest = tmp_path / "dest"
    dest.mkdir(exist_ok=True)
    return subprocess.run(
        [
            BASH,
            str(GCP / "watch_pipeline.sh"),
            "--vm",
            "vm",
            "--zone",
            "z",
            "--poll-s",
            "1",
            "--dest",
            str(dest),
            *args,
        ],
        env=sb.env(**extra),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_watcher_refuses_a_foreign_bucket_archive_and_restarts_under_a_server_cap(
    tmp_path: Path,
) -> None:
    sb = Sandbox(tmp_path, {"status": "TERMINATED", "instance_id": "111"})
    bucket = sb.dir / "bucket" / "b" / "p"
    bucket.mkdir(parents=True)
    _tgz(bucket / "final.tgz", {**EXIT_ARCHIVE, "logs/INSTANCE_ID": "999\n"})  # an older launch's
    _tgz(sb.dir / "userhome" / "final.tgz", {**EXIT_ARCHIVE, "logs/INSTANCE_ID": "111\n"})
    (sb.dir / "userhome" / "flowstate" / "logs").mkdir(parents=True)
    r = _run_watcher(tmp_path, sb, "--bucket", "gs://b/p")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BUCKET_ARCHIVE_FOREIGN" in r.stdout
    calls = sb.calls()
    sched = next(i for i, c in enumerate(calls) if "set-scheduling" in c)
    start = next(i for i, c in enumerate(calls) if "instances start" in c)
    assert sched < start
    assert "--max-run-duration=60m" in calls[sched]
    assert "--instance-termination-action=DELETE" in calls[sched]
    # no guest power-off on the restart: Compute Engine's limit is the cap
    assert not any(c.startswith("sudo shutdown") for c in calls)
    assert sb.count("gcloud compute instances delete") == 1
    assert _members(tmp_path / "dest" / "final.tgz")["logs/INSTANCE_ID"].strip() == b"111"


def test_watcher_deletes_a_stopped_instance_whose_bucket_archive_is_its_own(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, {"status": "TERMINATED", "instance_id": "111"})
    bucket = sb.dir / "bucket" / "b" / "p"
    bucket.mkdir(parents=True)
    _tgz(bucket / "final.tgz", {**EXIT_ARCHIVE, "logs/INSTANCE_ID": "111\n"})
    r = _run_watcher(tmp_path, sb, "--bucket", "gs://b/p", "--instance-id", "111")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FETCHED_BUCKET" in r.stdout and "VM_DELETED vm" in r.stdout
    assert sb.count("gcloud compute instances set-machine-type") == 0
    assert sb.count("gcloud compute instances start") == 0


@pytest.mark.parametrize("self_delete_attr", ["1", ""])
def test_watcher_reads_a_via_bucket_pipeline_in_root(tmp_path: Path, self_delete_attr: str) -> None:
    sb = Sandbox(tmp_path, {"status": "RUNNING", "instance_id": "111"})
    logs = sb.dir / "root" / "flowstate" / "logs"
    logs.mkdir(parents=True)
    (logs / "PIPELINE_EXIT").write_text("rc=0 2026-10-07T00:00:00Z\n")
    (logs / "battery.done").write_text("x\n")
    _tgz(sb.dir / "root" / "final.tgz", {**EXIT_ARCHIVE, "logs/INSTANCE_ID": "111\n"})
    r = _run_watcher(tmp_path, sb, **{"STUB_SELF_DELETE_ATTR": self_delete_attr})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "pipeline home on the VM: /root" in r.stdout
    assert "stages_done=[battery ]" in r.stdout and "rc=0" in r.stdout
    assert "FETCHED" in r.stdout and "VM_DELETED vm" in r.stdout
    assert _members(tmp_path / "dest" / "final.tgz")["logs/INSTANCE_ID"].strip() == b"111"
    window = sb.count("sudo shutdown -h +30")
    assert window == (0 if self_delete_attr == "1" else 1)


# --- the launcher (--via-bucket) ---------------------------------------------------------------


def _launch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "launch_repo"
    (repo / "scripts" / "gcp").mkdir(parents=True)
    for s in ("launch_i24_pipeline.sh", "vm_setup.sh", "idle_guard.sh"):
        shutil.copy(GCP / s, repo / "scripts" / "gcp" / s)
    (repo / "README").write_text("x\n")
    git = [
        "git",
        "-C",
        str(repo),
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
    # the I-24 data set (untracked, as in the real tree) and an interrupted penetration sweep
    proc = repo / "data" / "i24motion" / "processed"
    (proc / "i24_wb_20221130").mkdir(parents=True)
    (proc / "i24_wb_20221130" / "x").write_text("x")
    for f in (
        "i24_wb_episodes.pkl",
        "i24_wb_episodes_heavy.pkl",
        "i24_wb_episode_summary.json",
        "i24_wb_episode_summary_heavy.json",
    ):
        (proc / f).write_text("x")
    (repo / "data" / "i24motion" / "auxiliary_information").mkdir()
    (repo / "runs" / "i24_validation").mkdir(parents=True)
    (repo / "runs" / "i24_validation" / "observed_i24.json").write_text("{}")
    run = repo / "runs" / "i24_sweep" / "cellA" / "abc123" / "7"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text("{}")
    (run / "meta.json").write_text("{}")
    (repo / "runs" / "i24_sweep" / "MANIFEST.json").write_text("{}")
    return repo


def _launch(
    tmp_path: Path, config: dict[str, Any]
) -> tuple[Sandbox, subprocess.CompletedProcess[str]]:
    sb = Sandbox(tmp_path, config)
    repo = _launch_repo(tmp_path)
    r = subprocess.run(
        [BASH, str(repo / "scripts" / "gcp" / "launch_i24_pipeline.sh"), "--vm", "vm", "--zone", "z1",
         "--bucket", "gs://b/p", "--self-delete", "--via-bucket", "--cap-min", "100"],
        cwd=repo,
        env=sb.env(),
        capture_output=True,
        text=True,
        timeout=120,
    )  # fmt: skip
    return sb, r


def test_launcher_keeps_a_running_vm_when_the_marker_read_fails(tmp_path: Path) -> None:
    sb, r = _launch(tmp_path, {"started_marker": True, "cat_fail": True, "instance_id": "111"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert sb.count("gcloud compute instances delete") == 0
    watch = next(line for line in r.stdout.splitlines() if "watch_pipeline.sh" in line)
    assert "--deadline-min 120" in watch and "--instance-id 111" in watch
    assert "--remote-home /root" in watch and "--bucket gs://b/p" in watch
    assert "gcloud compute instances delete vm" in r.stdout  # the recovery note
    create = next(c for c in sb.calls() if "instances create" in c)
    assert "--metadata flowstate-self-delete=1" in create
    startup = sb.dir / "startup-script.sh"
    subprocess.run([BASH, "-n", str(startup)], check=True)
    text = startup.read_text()
    assert "put_marker STARTED" in text and "FLOWSTATE_SETUP $1" in text
    # the interrupted penetration sweep rides along for resume: <cell>/<config hash>/<seed>/
    inputs = next((sb.dir / "bucket" / "b" / "p" / "inputs").iterdir())
    with tarfile.open(inputs / "i24_data.tar") as tf:
        names = set(tf.getnames())
    assert "runs/i24_sweep/cellA/abc123/7/metrics.json" in names
    assert "runs/i24_sweep/cellA/abc123/7/meta.json" in names


def test_launcher_accepts_the_serial_console_marker(tmp_path: Path) -> None:
    sb, r = _launch(
        tmp_path, {"serial": "boot...\nFLOWSTATE_SETUP STARTED\n", "instance_id": "111"}
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "STARTED on the serial console" in r.stdout
    assert sb.count("gcloud compute instances delete") == 0


def test_launcher_deletes_the_vm_when_setup_reports_failure(tmp_path: Path) -> None:
    sb, r = _launch(tmp_path, {"serial": "FLOWSTATE_SETUP SETUP_FAILED\n"})
    assert r.returncode == 1
    assert sb.count("gcloud compute instances delete vm") == 1


# --- the ingest script -------------------------------------------------------------------------


def test_ingest_installs_stage_23_and_24_outputs_and_names_what_it_skips(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, {})
    repo = tmp_path / "ingest_repo"
    (repo / "artifacts").mkdir(parents=True)
    (repo / "scenarios").mkdir()
    (repo / "artifacts" / "committed.json").write_text('{"same": 1}')
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    files = {
        "artifacts/i24_validation_dc.json": "{}",
        "artifacts/i24_validation_dc_refit.json": "{}",
        "artifacts/baseline_gate_mndot_dc.json": "{}",
        "artifacts/validation_corridor_x_dc_gated.json": "{}",
        "artifacts/demand_scale_i24_flow_dc.json": "{}",
        "artifacts/i94_netfix_probe.json": "{}",
        "artifacts/committed.json": '{"same": 1}',
        "artifacts/something_new.json": "{}",
        "scenarios/i24_replica_flow_speedcal_dc_refit.yaml": "name: a\n",
        "scenarios/i24_replica_flow_corrected_dc.yaml": "name: b\n",
        "scenarios/mndot_x_weave_dc.yaml": "name: c\n",
        "runs/i24_validation/dc/h1/7/meta.json": "{}",
        "runs/i24_validation/dc_refit/h2/7/meta.json": "{}",
        "runs/p5/i94_netfix_probe/as_built/LANES.json": "{}",
        "docs/reports/corridor_x_dc/baseline_gate.md": "# gate\n",
        "logs/PIPELINE_EXIT": "rc=0\n",
    }
    tgz = _tgz(tmp_path / "final.tgz", files)
    r = subprocess.run(
        [BASH, str(GCP / "ingest_pipeline_results.sh"), str(tgz)],
        cwd=repo,
        env=sb.env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    for name in files:
        if name.startswith("logs/") or name.endswith(("committed.json", "something_new.json")):
            continue
        assert (repo / name).is_file(), name
    assert "artifact something_new.json NOT ingested" in r.stdout
    assert "artifact committed.json" not in r.stdout  # unchanged: silent
    assert not (repo / "artifacts" / "something_new.json").exists()
