"""Store ↔ queue reconciliation (``api.jobs.reconcile_store``).

Rows the API answers ``queued``/``running`` for are compared with their RQ
job (job id == row id) and repaired: a vanished job fails a running row and
re-enqueues a never-started one, a failed/abandoned job fails its row, a
requeued job resets a stuck ``running`` row, and rows with a live or waiting
job are left alone. The worker entrypoint runs the pass before its first
job. Uses the throwaway ``redis-server`` fixtures of ``test_redis_queue``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.jobs import ReconcileReport, RedisQueue, reconcile_store, run_scenario_job
from api.store import Store
from flowstate_core.rng import spawn_seeds
from tests.test_api.conftest import HEADERS, macro_corridor_config
from tests.test_api.test_redis_queue import _REDIS_SERVER, redis_client, redis_url

#: The throwaway-redis fixtures, re-exported: pytest finds them in this
#: module's namespace (the tuple keeps the imports referenced).
_FIXTURES = (redis_client, redis_url)

pytestmark = pytest.mark.skipif(
    _REDIS_SERVER is None, reason="redis-server not on PATH; skipping reconciliation tests"
)


def _row(store: Store, settings: Any, run_id: str, status: str = "queued") -> str:
    cfg = macro_corridor_config()
    store.create_run(
        scenario_id=None,
        config=cfg,
        config_hash="cafebabe0000",
        tier="macro",
        seeds=spawn_seeds(cfg["seed"], cfg["replicates"]),
        run_root=settings.runs_dir / run_id,
        run_id=run_id,
    )
    if status == "running":
        assert store.claim_run(run_id)
    elif status != "queued":
        store.set_run_status(run_id, status)
    return run_id


def _enqueue(queue: RedisQueue, settings: Any, run_id: str) -> None:
    queue.enqueue(
        run_scenario_job,
        run_id,
        job_id=run_id,
        db_path=str(settings.db_path),
        results_root=str(settings.results_dir),
    )


def _reconcile(client: TestClient, redis_url: str, **kwargs: Any) -> ReconcileReport:
    settings = client.app.state.settings
    return reconcile_store(
        client.app.state.store, RedisQueue(redis_url), settings.results_dir, **kwargs
    )


def _status(client: TestClient, run_id: str) -> tuple[str, str | None]:
    row = client.get(f"/api/v1/runs/{run_id}", headers=HEADERS).json()
    return row["status"], row["error"]


def test_running_row_without_a_job_is_failed(redis_client: TestClient, redis_url: str) -> None:
    """Redis restarted / worker container gone: the row stops saying running."""
    store, settings = redis_client.app.state.store, redis_client.app.state.settings
    run_id = _row(store, settings, "run_lost", status="running")

    report = _reconcile(redis_client, redis_url)
    assert report.failed == [run_id] and report.requeued == [] and report.reset == []
    status, error = _status(redis_client, run_id)
    assert status == "failed"
    assert error is not None and "lost from the queue" in error and "Traceback" not in error


def test_queued_row_without_a_job_is_enqueued_again_and_runs(
    redis_client: TestClient, redis_url: str
) -> None:
    from rq.job import Job

    store, settings = redis_client.app.state.store, redis_client.app.state.settings
    run_id = _row(store, settings, "run_orphan")

    # Younger than the grace period: its first enqueue may still be in flight.
    assert _reconcile(redis_client, redis_url).changed == 0
    assert _status(redis_client, run_id)[0] == "queued"

    report = _reconcile(redis_client, redis_url, queued_grace_s=0.0)
    assert report.requeued == [run_id] and report.failed == []
    queue = RedisQueue(redis_url)
    assert Job.exists(run_id, queue.connection)
    # Idempotent: a second pass finds the job waiting and does nothing.
    assert _reconcile(redis_client, redis_url, queued_grace_s=0.0).changed == 0

    from api.worker import main

    main(burst=True)
    status, error = _status(redis_client, run_id)
    assert status == "done", error


def test_rows_whose_job_failed_without_reporting_are_failed(
    redis_client: TestClient, redis_url: str
) -> None:
    from rq.job import Job, JobStatus

    store, settings = redis_client.app.state.store, redis_client.app.state.settings
    queue = RedisQueue(redis_url)
    running = _row(store, settings, "run_rqfail", status="running")
    queued = _row(store, settings, "run_rqstop")
    for run_id, rq_status in ((running, JobStatus.FAILED), (queued, JobStatus.STOPPED)):
        _enqueue(queue, settings, run_id)
        Job.fetch(run_id, connection=queue.connection).set_status(rq_status)

    report = _reconcile(redis_client, redis_url)
    assert sorted(report.failed) == sorted([running, queued])
    for run_id, word in ((running, "failed"), (queued, "stopped")):
        status, error = _status(redis_client, run_id)
        assert status == "failed"
        assert error is not None and f"queue job {word}" in error


def test_started_job_with_no_live_execution_is_failed(
    redis_client: TestClient, redis_url: str
) -> None:
    """Worker died mid-job: RQ says started, but nothing heartbeats for it."""
    from rq.job import Job, JobStatus

    store, settings = redis_client.app.state.store, redis_client.app.state.settings
    queue = RedisQueue(redis_url)
    run_id = _row(store, settings, "run_dead", status="running")
    _enqueue(queue, settings, run_id)
    Job.fetch(run_id, connection=queue.connection).set_status(JobStatus.STARTED)

    report = _reconcile(redis_client, redis_url)
    assert report.failed == [run_id]
    status, error = _status(redis_client, run_id)
    assert status == "failed"
    assert error is not None and "worker died" in error


def test_running_row_with_a_requeued_job_is_reset_and_then_runs(
    redis_client: TestClient, redis_url: str
) -> None:
    store, settings = redis_client.app.state.store, redis_client.app.state.settings
    queue = RedisQueue(redis_url)
    run_id = _row(store, settings, "run_requeued", status="running")
    _enqueue(queue, settings, run_id)  # operator requeued it while the row was stuck

    report = _reconcile(redis_client, redis_url)
    assert report.reset == [run_id] and report.failed == []
    assert _status(redis_client, run_id)[0] == "queued"

    from api.worker import main

    main(burst=True)
    status, error = _status(redis_client, run_id)
    assert status == "done", error


def test_waiting_and_finished_rows_are_left_alone(redis_client: TestClient, redis_url: str) -> None:
    store, settings = redis_client.app.state.store, redis_client.app.state.settings
    queue = RedisQueue(redis_url)
    waiting = _row(store, settings, "run_waiting")
    _enqueue(queue, settings, waiting)
    done = _row(store, settings, "run_done", status="done")
    failed = _row(store, settings, "run_failed", status="failed")

    assert _reconcile(redis_client, redis_url, queued_grace_s=0.0).changed == 0
    assert _status(redis_client, waiting)[0] == "queued"
    assert _status(redis_client, done)[0] == "done"
    assert _status(redis_client, failed)[0] == "failed"


def test_jobs_under_a_random_rq_id_are_matched_by_their_first_argument(
    redis_client: TestClient, redis_url: str
) -> None:
    """A producer that passed no ``job_id`` still gets correct reconciliation.

    The row's job is then found among queued/started jobs by its first
    argument, so a waiting row is not re-enqueued and a stuck running row
    is reset rather than failed as "lost".
    """
    from rq.job import Job

    store, settings = redis_client.app.state.store, redis_client.app.state.settings
    queue = RedisQueue(redis_url)
    waiting = _row(store, settings, "run_legacy_q")
    stuck = _row(store, settings, "run_legacy_r", status="running")
    for run_id in (waiting, stuck):
        queue.enqueue(  # no job_id: random RQ id, row id only in args
            run_scenario_job,
            run_id,
            db_path=str(settings.db_path),
            results_root=str(settings.results_dir),
        )
        assert not Job.exists(run_id, queue.connection)

    report = _reconcile(redis_client, redis_url, queued_grace_s=0.0)
    assert report.failed == [] and report.requeued == []
    assert report.reset == [stuck]
    assert _status(redis_client, waiting)[0] == "queued"
    assert _status(redis_client, stuck)[0] == "queued"
    assert len(queue.queue.get_job_ids()) == 2  # nothing enqueued twice

    from api.worker import main

    main(burst=True)
    for run_id in (waiting, stuck):
        status, error = _status(redis_client, run_id)
        assert status == "done", error


def test_inline_queue_has_nothing_to_reconcile(tmp_path: Path) -> None:
    from api.jobs import InlineQueue

    store = Store(tmp_path / "meta.db")
    store.create_run(
        scenario_id=None, config={}, config_hash="x", tier="macro", seeds=[1], run_root=tmp_path
    )
    assert reconcile_store(store, InlineQueue(), tmp_path).changed == 0


def test_worker_main_reconciles_before_working(redis_client: TestClient, redis_url: str) -> None:
    """The deployed entrypoint repairs abandoned rows on start-up."""
    store, settings = redis_client.app.state.store, redis_client.app.state.settings
    stuck = _row(store, settings, "run_stuck", status="running")

    from api.worker import main

    main(burst=True)
    status, error = _status(redis_client, stuck)
    assert status == "failed"
    assert error is not None and "reconciliation" in error
