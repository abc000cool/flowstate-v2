"""RQ worker entrypoint: ``python -m api.worker``.

Consumes the ``flowstate`` queue on ``FLOWSTATE_REDIS_URL`` and executes the
job functions in :mod:`api.jobs`.

Two things keep store rows honest when a job cannot report for itself:

- **Work-horse death.** RQ forks a work horse per job; when the horse is
  killed (the kernel's OOM killer on a large corridor run, a stray SIGKILL)
  the worker itself survives and marks only the RQ job failed. The
  ``work_horse_killed_handler`` (:func:`mark_row_failed_on_horse_death`)
  fails the matching store row as well — RQ job ids equal store row ids —
  so the API stops answering ``running`` for work that is not happening.
- **Reconciliation.** :func:`api.jobs.reconcile_store` runs before the
  first job and again with RQ's periodic maintenance (every
  ``maintenance_interval``, default 10 min). It covers what the handler
  cannot see: the whole worker container gone (redeploy, OOM of the worker
  itself), or Redis restarted and its queue lost — rows are failed (or, for
  never-started ``queued`` rows, enqueued again).

macOS/fork note: RQ's default worker forks a work-horse per job. That is safe
here even with libsumo's one-simulation-per-process constraint, because the
job functions never run SUMO in the worker process itself — micro-tier
replicates execute inside *spawned* subprocesses via
:func:`microsim.runner.run_replicates` (one libsumo per child, CLAUDE.md
§3.4), and the macro tier is pure Python/NumPy.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from rq import Queue, SimpleWorker, Worker
from rq.job import Job
from rq.worker import BaseWorker

from api.jobs import QUEUE_NAME, RedisQueue, reconcile_store, row_id_of_job
from api.settings import Settings, load_settings
from api.store import Store, kind_of_id

_log = logging.getLogger(__name__)


def mark_row_failed_on_horse_death(job: Job, retpid: int, ret_val: int, rusage: Any) -> None:
    """``work_horse_killed_handler``: fail the store row of a killed job.

    Called by the surviving worker after ``waitpid`` reports the horse ended
    abnormally (signal or non-zero exit) while the job was still ``started``.
    The row is found from the job (its id, else its first argument — see
    :func:`api.jobs.row_id_of_job`); ``db_path`` comes from the job's own
    kwargs so the handler addresses exactly the store the job was using.
    Only an active (``queued``/``running``) row is touched — an outcome the
    job managed to record itself always wins.
    """
    row_id = row_id_of_job(job)
    kind = kind_of_id(row_id) if row_id is not None else None
    if row_id is None or kind is None:
        return  # not one of our rows
    kwargs = job.kwargs or {}
    db_path = kwargs.get("db_path") or str(load_settings().db_path)
    signal_msg = ""
    try:
        if ret_val and os.WIFSIGNALED(ret_val):
            signal_msg = f", signal {os.WTERMSIG(ret_val)}"
    except (TypeError, ValueError, OverflowError):
        pass
    error = (
        f"work-horse terminated unexpectedly while executing this {kind} "
        f"(waitpid returned {ret_val}{signal_msg}, pid {retpid}); a kernel out-of-memory kill is "
        f"the usual cause — see the worker log"
    )
    if Store(db_path).fail_active(kind, row_id, error):
        _log.warning("%s %s: %s", kind, row_id, error)


def _reconcile_logged(store: Store, queue: RedisQueue, results_root: Path) -> None:
    """One reconciliation pass; a failure here must never take the worker down."""
    try:
        report = reconcile_store(store, queue, results_root)
    except Exception:
        _log.exception("store/queue reconciliation failed; will retry at next maintenance")
        return
    if report.changed:
        _log.warning(
            "reconciliation: %d rows failed, %d requeued, %d reset to queued",
            len(report.failed),
            len(report.requeued),
            len(report.reset),
        )


class _ReconcilingMixin:
    """Run :func:`api.jobs.reconcile_store` with RQ's periodic maintenance."""

    flowstate_store: Store
    flowstate_queue: RedisQueue
    flowstate_results_root: Path

    def run_maintenance_tasks(self) -> None:
        super().run_maintenance_tasks()  # type: ignore[misc]
        store = getattr(self, "flowstate_store", None)
        if store is None:
            # Constructed without build_worker (rq's own CLI, for instance):
            # nothing to reconcile against, and that must not crash the loop.
            return
        _reconcile_logged(store, self.flowstate_queue, self.flowstate_results_root)


class FlowStateWorker(_ReconcilingMixin, Worker):
    """Forking RQ worker (production): one work horse per job."""


class FlowStateSimpleWorker(_ReconcilingMixin, SimpleWorker):
    """Non-forking variant (debugging, or platforms without ``fork``).

    Jobs run in the worker process itself, so a horse-death handler cannot
    apply; reconciliation still does.
    """


def build_worker(settings: Settings | None = None, *, fork: bool = True) -> BaseWorker:
    """A worker on the configured Redis queue with both safety nets attached."""
    s = settings if settings is not None else load_settings()
    queue = RedisQueue(s.redis_url, QUEUE_NAME)
    store = Store(s.db_path)
    cls: type[BaseWorker] = FlowStateWorker if fork else FlowStateSimpleWorker
    worker = cls(
        [Queue(QUEUE_NAME, connection=queue.connection)],
        connection=queue.connection,
        work_horse_killed_handler=mark_row_failed_on_horse_death,
    )
    worker.flowstate_store = store  # type: ignore[attr-defined]
    worker.flowstate_queue = queue  # type: ignore[attr-defined]
    worker.flowstate_results_root = Path(s.results_dir)  # type: ignore[attr-defined]
    return worker


def main(burst: bool = False, *, fork: bool = True) -> None:
    """Reconcile the store with the queue, then run the worker.

    ``burst=True`` drains the queue and returns (tests, one-shot runs);
    otherwise the worker blocks until interrupted.
    """
    settings = load_settings()
    worker = build_worker(settings, fork=fork)
    _reconcile_logged(
        worker.flowstate_store,  # type: ignore[attr-defined]
        worker.flowstate_queue,  # type: ignore[attr-defined]
        worker.flowstate_results_root,  # type: ignore[attr-defined]
    )
    worker.work(burst=burst)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main()
