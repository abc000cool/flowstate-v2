"""Redis-backed queue: enqueue-only endpoints + an rq SimpleWorker burst.

Spins a throwaway ``redis-server`` on a random port when one is on PATH;
skipped otherwise. Verifies the CLAUDE.md §8 rule that no endpoint executes a
simulation synchronously when ``FLOWSTATE_QUEUE=redis``.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_api.conftest import HEADERS, macro_corridor_config, post_run, post_scenario

_REDIS_SERVER = shutil.which("redis-server")

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


def _work_burst(redis_url: str) -> None:
    import redis
    from rq import Queue, SimpleWorker

    from api.jobs import QUEUE_NAME

    conn = redis.Redis.from_url(redis_url)
    try:
        worker = SimpleWorker([Queue(QUEUE_NAME, connection=conn)], connection=conn)
        worker.work(burst=True)
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

    _work_burst(redis_url)

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

    _work_burst(redis_url)  # burst drains the sweep job and the child runs it enqueues

    body = redis_client.get(f"/api/v1/sweeps/{sweep_id}", headers=HEADERS).json()
    assert body["status"] == "done", body["error"]
    assert body["runs_total"] == 2
    assert body["runs_done"] == 2
