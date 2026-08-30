"""RQ worker entrypoint: ``python -m api.worker``.

Consumes the ``flowstate`` queue on ``FLOWSTATE_REDIS_URL`` and executes the
job functions in :mod:`api.jobs`.

macOS/fork note: RQ's default worker forks a work-horse per job. That is safe
here even with libsumo's one-simulation-per-process constraint, because the
job functions never run SUMO in the worker process itself — micro-tier
replicates execute inside *spawned* subprocesses via
:func:`microsim.runner.run_replicates` (one libsumo per child, CLAUDE.md
§3.4), and the macro tier is pure Python/NumPy.
"""

from __future__ import annotations

from api.jobs import QUEUE_NAME
from api.settings import load_settings


def main() -> None:
    """Run a blocking RQ worker until interrupted."""
    import redis
    from rq import Queue, Worker

    settings = load_settings()
    connection = redis.Redis.from_url(settings.redis_url)
    worker = Worker([Queue(QUEUE_NAME, connection=connection)], connection=connection)
    worker.work()


if __name__ == "__main__":
    main()
