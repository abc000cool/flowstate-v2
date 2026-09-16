"""Per-replicate metrics are computed by the worker, not in the request path.

``run_scenario_job`` fills every replicate's ``metrics.json`` before it marks
the run ``done``; ``api.results.cached_run_metrics`` reads those caches only
(None when any is missing, never computing or writing), so a polled endpoint
can serve it without reducing a trajectory file; and the cache write is
atomic (temp file + rename), leaving no partial file behind.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api import results as res
from tests.test_api.conftest import macro_corridor_config, post_run, post_scenario


def _done_run(client: TestClient) -> dict:
    scenario = post_scenario(client, macro_corridor_config())
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]
    return run


def test_worker_precomputes_every_replicate_cache(client: TestClient) -> None:
    run = _done_run(client)
    run_root = Path(client.app.state.store.get_run(run["run_id"])["run_root"])
    dirs = res.replicate_dirs(run_root)
    assert len(dirs) == 3
    for d in dirs:
        cache = d / res.METRICS_CACHE_NAME
        assert cache.is_file(), f"worker left no metrics cache in {d}"
        payload = json.loads(cache.read_text())
        assert payload["schema"] == 1
        assert payload["metrics"]["throughput_veh_h"] > 0.0
        assert not list(d.glob(f"{res.METRICS_CACHE_NAME}.tmp-*"))  # atomic write, no leftovers

    cached = res.cached_run_metrics(run_root)
    assert cached is not None
    per_replicate, agg = cached
    assert sorted(seed for seed, _ in per_replicate) == sorted(run["seeds"])
    assert agg["throughput_veh_h"].n == 3


def test_cached_run_metrics_is_read_only(client: TestClient) -> None:
    run = _done_run(client)
    run_root = Path(client.app.state.store.get_run(run["run_id"])["run_root"])
    victim = res.replicate_dirs(run_root)[1] / res.METRICS_CACHE_NAME
    victim.unlink()

    assert res.cached_run_metrics(run_root) is None
    assert not victim.exists()  # not recreated by the cached-only read
    assert res.cached_replicate_metrics(victim.parent) is None

    # A stale or unreadable cache counts as absent too.
    victim.write_text(json.dumps({"schema": 0, "metrics": {}}))
    assert res.cached_run_metrics(run_root) is None
    victim.write_text("{not json")
    assert res.cached_run_metrics(run_root) is None

    # The on-demand path recomputes and refills it (atomically).
    per_replicate, _ = res.run_metrics(run_root)
    assert len(per_replicate) == 3
    assert json.loads(victim.read_text())["schema"] == 1
    assert res.cached_run_metrics(run_root) is not None
    assert not list(victim.parent.glob(f"{res.METRICS_CACHE_NAME}.tmp-*"))


def test_precompute_skips_cached_and_requires_replicates(
    client: TestClient, tmp_path: Path
) -> None:
    run = _done_run(client)
    run_root = Path(client.app.state.store.get_run(run["run_id"])["run_root"])
    before = [(d / res.METRICS_CACHE_NAME).stat().st_mtime_ns for d in res.replicate_dirs(run_root)]
    assert res.precompute_run_metrics(run_root) == 3
    after = [(d / res.METRICS_CACHE_NAME).stat().st_mtime_ns for d in res.replicate_dirs(run_root)]
    assert after == before  # already cached: untouched

    empty = tmp_path / "no_replicates"
    empty.mkdir()
    try:
        res.precompute_run_metrics(empty)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("precompute_run_metrics must refuse an empty run root")
    try:
        res.cached_run_metrics(empty)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("cached_run_metrics must refuse an empty run root")
