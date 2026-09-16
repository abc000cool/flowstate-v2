"""Sweep fan-out is transactional and idempotent.

A fan-out that dies mid-grid (Redis hiccup on enqueue, worker kill) must
leave a resumable sweep: every cell already has its run row (created in one
transaction with the grid that names them), and re-running ``sweep_job`` —
what ``rq requeue`` does — dispatches exactly the cells that never ran,
creates no new run rows, and never re-executes a finished cell.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import api.jobs
from api.jobs import InlineQueue, run_scenario_job, sweep_job
from tests.test_api.conftest import HEADERS, macro_corridor_config, post_scenario


class _FlakyQueue:
    """Inline queue whose N-th enqueue raises (a Redis error mid fan-out)."""

    kind = "inline"

    def __init__(self, fail_at: int) -> None:
        self.calls = 0
        self.fail_at = fail_at
        self.inner = InlineQueue()

    def enqueue(self, func: Any, *args: Any, job_id: str | None = None, **kwargs: Any) -> None:
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("flaky redis: connection reset")
        self.inner.enqueue(func, *args, job_id=job_id, **kwargs)

    def check(self) -> None:
        return None


def _post_three_cell_sweep(client: TestClient) -> str:
    scenario = post_scenario(client, macro_corridor_config())
    r = client.post(
        "/api/v1/sweeps",
        json={
            "scenario_id": scenario["scenario_id"],
            "penetrations": [0.02, 0.05, 0.1],
            "compliances": [1.0],
            "controllers": ["follower_stopper"],
            "replicates": 2,
            "overrides": {"sim": {"duration_s": 60.0}},
        },
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    return str(r.json()["sweep_id"])


def test_interrupted_fan_out_resumes_without_duplicate_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = client.app.state.store
    settings = client.app.state.settings

    # sweep_job looks the queue up through api.jobs.get_queue at call time;
    # the API's own enqueue of the sweep job (main.py) keeps the real queue.
    flaky = _FlakyQueue(fail_at=2)
    monkeypatch.setattr(api.jobs, "get_queue", lambda *_a, **_k: flaky)
    sweep_id = _post_three_cell_sweep(client)

    body = client.get(f"/api/v1/sweeps/{sweep_id}", headers=HEADERS).json()
    assert body["status"] == "failed"
    assert body["error"] == "RuntimeError: flaky redis: connection reset"
    # Every cell has its row — the child set is created in one transaction —
    # and only the first cell ran.
    grid = store.get_sweep(sweep_id)["grid"]
    run_ids = [cell["run_id"] for cell in grid]
    assert all(rid is not None for rid in run_ids) and len(set(run_ids)) == 3
    statuses = [store.get_run(rid)["status"] for rid in run_ids]
    assert statuses == ["done", "queued", "queued"]
    assert len(store.list_runs(sweep_id=sweep_id)) == 3
    first_row_before = store.get_run(run_ids[0])

    # Resume: a working queue, and a spy on the child job to see what runs.
    executed: list[str] = []

    def spy(run_id: str, **kwargs: Any) -> None:
        executed.append(run_id)
        run_scenario_job(run_id, **kwargs)

    monkeypatch.setattr(api.jobs, "run_scenario_job", spy)
    monkeypatch.setattr(api.jobs, "get_queue", lambda *_a, **_k: InlineQueue())
    sweep_job(sweep_id, db_path=str(settings.db_path), results_root=str(settings.results_dir))

    body = client.get(f"/api/v1/sweeps/{sweep_id}", headers=HEADERS).json()
    assert body["status"] == "done", body["error"]
    assert body["runs_total"] == 3 and body["runs_done"] == 3
    # No new rows, same ids in the grid, and the finished cell was not re-run.
    assert len(store.list_runs(sweep_id=sweep_id)) == 3
    assert [cell["run_id"] for cell in store.get_sweep(sweep_id)["grid"]] == run_ids
    assert executed == run_ids[1:]
    assert store.get_run(run_ids[0]) == first_row_before


def test_duplicate_delivery_of_a_run_job_is_a_no_op(client: TestClient) -> None:
    """The claim makes a second execution of the same job return immediately."""
    import macrosim.runner

    store = client.app.state.store
    settings = client.app.state.settings
    sweep_id = _post_three_cell_sweep(client)
    run_id = store.get_sweep(sweep_id)["grid"][0]["run_id"]
    done_row = store.get_run(run_id)
    assert done_row["status"] == "done"

    original = macrosim.runner.run_macro
    calls = 0

    def counting(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    macrosim.runner.run_macro = counting  # type: ignore[assignment]
    try:
        run_scenario_job(
            run_id, db_path=str(settings.db_path), results_root=str(settings.results_dir)
        )
    finally:
        macrosim.runner.run_macro = original  # type: ignore[assignment]
    assert calls == 0
    assert store.get_run(run_id) == done_row


def test_sweep_job_itself_is_not_re_run_while_running_or_done(client: TestClient) -> None:
    store = client.app.state.store
    settings = client.app.state.settings
    sweep_id = _post_three_cell_sweep(client)
    assert store.get_sweep(sweep_id)["status"] == "done"
    before = store.list_runs(sweep_id=sweep_id)

    sweep_job(sweep_id, db_path=str(settings.db_path), results_root=str(settings.results_dir))
    assert store.get_sweep(sweep_id)["status"] == "done"
    assert store.list_runs(sweep_id=sweep_id) == before
