"""Redis-backed queue: enqueue-only endpoints, the real worker, horse death.

Spins a throwaway ``redis-server`` on a random port when one is on PATH and
skips otherwise — except in CI, where the deployed path (``FLOWSTATE_QUEUE=
redis`` + ``python -m api.worker``) must be exercised, so a missing binary
is an error rather than a skip. Verifies the CLAUDE.md §8 rule that no
endpoint executes a simulation synchronously under the redis queue, drains
the queue through the actual worker entrypoint (``api.worker.main``, the
forking RQ worker with its horse-death handler and reconciliation attached),
and checks that a killed work horse leaves a ``failed`` row, not a
``running`` one.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_api.conftest import HEADERS, macro_corridor_config, post_run, post_scenario

_REDIS_SERVER = shutil.which("redis-server")

if _REDIS_SERVER is None and os.environ.get("CI"):
    raise RuntimeError(
        "redis-server must be installed in CI (see .github/workflows/ci.yml); "
        "the redis queue and worker path is otherwise untested"
    )

pytestmark = pytest.mark.skipif(
    _REDIS_SERVER is None, reason="redis-server not on PATH; skipping redis-backed queue test"
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def redis_url(tmp_path: Path) -> Iterator[str]:
    assert _REDIS_SERVER is not None
    port = _free_port()
    proc = subprocess.Popen(
        [
            _REDIS_SERVER,
            "--port",
            str(port),
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(tmp_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"redis://127.0.0.1:{port}/0"
    import redis

    conn = redis.Redis.from_url(url)
    try:
        for _ in range(100):
            try:
                conn.ping()
                break
            except redis.ConnectionError:
                time.sleep(0.05)
        else:
            pytest.skip("redis-server did not come up in time")
        yield url
    finally:
        conn.close()
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture()
def redis_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redis_url: str
) -> Iterator[TestClient]:
    monkeypatch.setenv("FLOWSTATE_QUEUE", "redis")
    monkeypatch.setenv("FLOWSTATE_REDIS_URL", redis_url)
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("FLOWSTATE_API_KEY", HEADERS["X-API-Key"])
    from api.main import create_app

    with TestClient(create_app()) as c:
        yield c


def _work_burst() -> None:
    """Drain the queue through the real entrypoint (env set by ``redis_client``)."""
    from api.worker import main

    main(burst=True)


def _job(job_id: str, redis_url: str) -> Any:
    import redis
    from rq.job import Job

    conn = redis.Redis.from_url(redis_url)
    try:
        return Job.fetch(job_id, connection=conn) if Job.exists(job_id, conn) else None
    finally:
        conn.close()


def test_redis_run_is_asynchronous_then_worked(redis_client: TestClient, redis_url: str) -> None:
    assert redis_client.get("/healthz").json()["queue_kind"] == "redis"

    scenario = post_scenario(redis_client, macro_corridor_config())
    run = post_run(redis_client, scenario["scenario_id"])
    # No synchronous execution under the redis queue (CLAUDE.md §8).
    assert run["status"] == "queued"
    assert run["progress"]["completed_replicates"] == 0
    r = redis_client.get(f"/api/v1/runs/{run['run_id']}/metrics", headers=HEADERS)
    assert r.status_code == 409  # not done yet

    _work_burst()

    done = redis_client.get(f"/api/v1/runs/{run['run_id']}", headers=HEADERS).json()
    assert done["status"] == "done", done["error"]
    assert done["progress"] == {"completed_replicates": 3, "total_replicates": 3}
    metrics = redis_client.get(f"/api/v1/runs/{run['run_id']}/metrics", headers=HEADERS)
    assert metrics.status_code == 200
    assert metrics.json()["aggregate"]["throughput_veh_h"]["mean"] is not None


def test_redis_sweep_children_drain_in_one_burst(redis_client: TestClient, redis_url: str) -> None:
    scenario = post_scenario(redis_client, macro_corridor_config())
    r = redis_client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": scenario["scenario_id"],
            "penetrations": [0.02, 0.05],
            "compliances": [1.0],
            "controllers": ["follower_stopper"],
            "replicates": 2,
            "overrides": {"sim": {"duration_s": 60.0}},
        },
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    sweep_id = r.json()["sweep_id"]
    assert r.json()["status"] == "queued"

    _work_burst()  # burst drains the sweep job and the child runs it enqueues

    body = redis_client.get(f"/api/v1/sweeps/{sweep_id}", headers=HEADERS).json()
    assert body["status"] == "done", body["error"]
    assert body["runs_total"] == 2
    assert body["runs_done"] == 2

    # Child jobs are enqueued under their run ids: the RQ job of a cell is
    # addressable from the store row and vice versa (reconciliation relies on
    # it), and a finished job's record is still there under that id.
    from rq.job import JobStatus

    for cell in body["cells"]:
        job = _job(cell["run_id"], redis_url)
        assert job is not None, f"no RQ job under run id {cell['run_id']}"
        assert job.get_status() == JobStatus.FINISHED


def test_killed_work_horse_fails_the_row(
    redis_client: TestClient, redis_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run whose horse is SIGKILLed mid-simulation ends ``failed``, not ``running``.

    The horse is a fork of this process, so a monkeypatched ``run_macro``
    that kills its own process stands in for the kernel's OOM killer. The
    surviving worker's ``work_horse_killed_handler`` must then fail the row
    (found through the job id, which equals the run id).
    """
    import macrosim.runner
    from api.jobs import RedisQueue, run_scenario_job
    from flowstate_core.rng import spawn_seeds

    def die(*_args: Any, **_kwargs: Any) -> None:
        os.kill(os.getpid(), signal.SIGKILL)

    monkeypatch.setattr(macrosim.runner, "run_macro", die)

    store = redis_client.app.state.store
    settings = redis_client.app.state.settings
    cfg = macro_corridor_config()
    run_id = store.create_run(
        scenario_id=None,
        config=cfg,
        config_hash="deadbeefcafe",
        tier="macro",
        seeds=spawn_seeds(cfg["seed"], cfg["replicates"]),
        run_root=settings.runs_dir / "run_horse",
        run_id="run_horse",
    )
    RedisQueue(redis_url).enqueue(
        run_scenario_job,
        run_id,
        job_id=run_id,
        db_path=str(settings.db_path),
        results_root=str(settings.results_dir),
    )

    _work_burst()

    row = redis_client.get(f"/api/v1/runs/{run_id}", headers=HEADERS).json()
    assert row["status"] == "failed"
    assert row["error"] is not None
    assert "work-horse terminated" in row["error"]
    assert f"signal {int(signal.SIGKILL)}" in row["error"]
    assert "Traceback" not in row["error"]


def test_killed_work_horse_of_an_api_submitted_run_fails_the_row(
    redis_client: TestClient, redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same as above through ``POST /runs``: the handler finds the row whether
    the API enqueued the job under the run id or under a random RQ id (then
    by the job's first argument)."""
    import macrosim.runner

    def die(*_args: Any, **_kwargs: Any) -> None:
        os.kill(os.getpid(), signal.SIGKILL)

    monkeypatch.setattr(macrosim.runner, "run_macro", die)
    scenario = post_scenario(redis_client, macro_corridor_config())
    run = post_run(redis_client, scenario["scenario_id"])
    assert run["status"] == "queued"

    _work_burst()

    row = redis_client.get(f"/api/v1/runs/{run['run_id']}", headers=HEADERS).json()
    assert row["status"] == "failed"
    assert row["error"] is not None and "work-horse terminated" in row["error"]
