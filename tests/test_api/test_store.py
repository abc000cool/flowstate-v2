"""Direct Store tests: WAL mode, round trips, status guards, lifecycle CAS."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from api.store import KINDS, Store, kind_of_id, new_id


def test_wal_mode_enabled(tmp_path: Path) -> None:
    store = Store(tmp_path / "meta.db")
    con = sqlite3.connect(store.db_path)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        con.close()


def test_run_round_trip_and_progress(tmp_path: Path) -> None:
    store = Store(tmp_path / "meta.db")
    sid = store.create_scenario("s", {"name": "s"}, "abc123def456")
    rid = store.create_run(
        scenario_id=sid,
        config={"name": "s", "tier": "macro"},
        config_hash="abc123def456",
        tier="macro",
        seeds=[11, 22, 33],
        run_root=tmp_path / "runs" / "r1",
    )
    run = store.get_run(rid)
    assert run is not None
    assert run["status"] == "queued"
    assert run["seeds"] == [11, 22, 33]
    assert run["total_replicates"] == 3
    assert run["completed_replicates"] == 0

    store.set_run_status(rid, "running")
    store.set_run_progress(rid, 2)
    run = store.get_run(rid)
    assert run is not None
    assert (run["status"], run["completed_replicates"]) == ("running", 2)

    store.set_run_status(rid, "failed", error="boom", error_kind="test")
    run = store.get_run(rid)
    assert run is not None
    assert run["error"] == "boom"

    assert store.get_run("run_missing") is None
    assert [r["id"] for r in store.list_runs(scenario_id=sid)] == [rid]
    assert store.list_runs(scenario_id="other") == []


def test_invalid_status_rejected(tmp_path: Path) -> None:
    store = Store(tmp_path / "meta.db")
    rid = store.create_run(
        scenario_id=None,
        config={},
        config_hash="x",
        tier="macro",
        seeds=[1],
        run_root=tmp_path,
    )
    with pytest.raises(ValueError, match="status"):
        store.set_run_status(rid, "exploded")


def test_two_store_handles_share_one_database(tmp_path: Path) -> None:
    """API and worker processes open the store independently (WAL)."""
    a = Store(tmp_path / "meta.db")
    b = Store(tmp_path / "meta.db")
    sid = a.create_scenario("s", {"name": "s"}, "hash")
    seen = b.get_scenario(sid)
    assert seen is not None and seen["name"] == "s"


# ---------------------------------------------------------------------------
# Lifecycle guards: claim / fail_active / reset_to_queued, every kind
# ---------------------------------------------------------------------------


def _row_of_kind(store: Store, kind: str, tmp_path: Path) -> str:
    if kind == "run":
        return store.create_run(
            scenario_id=None, config={}, config_hash="x", tier="macro", seeds=[1], run_root=tmp_path
        )
    if kind == "sweep":
        return store.create_sweep(None, [])
    if kind == "calibration":
        return store.create_calibration("fd", tmp_path / "d.csv", {}, "src")
    return store.create_report(["run_x"], "t")


def test_kind_of_id_follows_the_prefix(tmp_path: Path) -> None:
    store = Store(tmp_path / "meta.db")
    for kind in KINDS:
        assert kind_of_id(_row_of_kind(store, kind, tmp_path)) == kind
    assert kind_of_id(new_id("scn")) is None
    assert kind_of_id("0f1e2d3c4b5a69788796a5b4c3d2e1f0") is None  # a random RQ id
    assert kind_of_id("") is None


@pytest.mark.parametrize("kind", KINDS)
def test_claim_is_a_compare_and_set(kind: str, tmp_path: Path) -> None:
    """queued/failed → running exactly once; running and done are not claimable."""
    store = Store(tmp_path / "meta.db")
    row_id = _row_of_kind(store, kind, tmp_path)

    assert store.claim(kind, row_id) is True
    assert store.get(kind, row_id)["status"] == "running"
    # A duplicate delivery of the same job finds the row running: no-op.
    assert store.claim(kind, row_id) is False
    assert store.get(kind, row_id)["status"] == "running"

    # Done rows stay done.
    if kind == "run":
        store.set_run_status(row_id, "done")
    elif kind == "sweep":
        store.set_sweep_status(row_id, "done")
    elif kind == "calibration":
        store.set_calibration_status(row_id, "done", artifact_path="a.json")
    else:
        store.set_report_status(row_id, "done", report_dir="d", report_path="d/report.md")
    assert store.claim(kind, row_id) is False
    assert store.get(kind, row_id)["status"] == "done"

    # Failed rows are claimable (an operator's `rq requeue` just works) and
    # the previous outcome is cleared so the re-run starts from a clean row.
    assert store.fail_active(kind, row_id, "stale") is False  # done is not active
    if kind == "run":
        store.set_run_status(row_id, "failed", error="boom", error_kind="k")
        store.set_run_progress(row_id, 2)
    elif kind == "sweep":
        store.set_sweep_status(row_id, "failed", error="boom")
    elif kind == "calibration":
        store.set_calibration_status(row_id, "failed", error="boom")
    else:
        store.set_report_status(row_id, "failed", error="boom", error_kind="k")
    assert store.claim(kind, row_id) is True
    row = store.get(kind, row_id)
    assert row["status"] == "running"
    assert row["error"] is None
    if kind == "run":
        assert row["error_kind"] is None
        assert row["completed_replicates"] == 0
    if kind == "report":
        assert row["error_kind"] is None

    assert store.claim(kind, "nope_missing") is False


@pytest.mark.parametrize("kind", KINDS)
def test_fail_active_never_overwrites_a_recorded_outcome(kind: str, tmp_path: Path) -> None:
    store = Store(tmp_path / "meta.db")
    queued = _row_of_kind(store, kind, tmp_path)
    running = _row_of_kind(store, kind, tmp_path)
    store.claim(kind, running)

    assert store.fail_active(kind, queued, "gone") is True
    assert store.fail_active(kind, running, "gone") is True
    for row_id in (queued, running):
        row = store.get(kind, row_id)
        assert (row["status"], row["error"]) == ("failed", "gone")
    # Already failed: not active, untouched (the first error is kept).
    assert store.fail_active(kind, running, "later") is False
    assert store.get(kind, running)["error"] == "gone"


def test_reset_to_queued_only_from_running(tmp_path: Path) -> None:
    store = Store(tmp_path / "meta.db")
    rid = _row_of_kind(store, "run", tmp_path)
    assert store.reset_to_queued("run", rid) is False  # queued already
    store.claim("run", rid)
    assert store.reset_to_queued("run", rid) is True
    assert store.get("run", rid)["status"] == "queued"
    assert store.claim("run", rid) is True  # and claimable again


def test_list_by_status_and_get(tmp_path: Path) -> None:
    store = Store(tmp_path / "meta.db")
    a = _row_of_kind(store, "run", tmp_path)
    b = _row_of_kind(store, "run", tmp_path)
    c = _row_of_kind(store, "run", tmp_path)
    store.claim("run", b)
    store.set_run_status(c, "done")
    assert [r["id"] for r in store.list_by_status("run", ("queued", "running"))] == [a, b]
    assert [r["id"] for r in store.list_by_status("run", ["done"])] == [c]
    assert store.list_by_status("run", []) == []
    assert store.get("run", "run_missing") is None
    with pytest.raises(ValueError, match="status"):
        store.list_by_status("run", ["exploded"])
    with pytest.raises(ValueError, match="kind"):
        store.list_by_status("scenario", ["queued"])


def test_create_sweep_runs_is_one_transaction(tmp_path: Path) -> None:
    """All child rows land with the grid that names them, or nothing does."""
    store = Store(tmp_path / "meta.db")
    grid = [{"config_hash": "h1", "run_id": None}, {"config_hash": "h2", "run_id": None}]
    sweep_id = store.create_sweep(None, grid)

    def spec(run_id: str) -> dict:
        return {
            "run_id": run_id,
            "scenario_id": None,
            "config": {"tier": "macro"},
            "config_hash": "h",
            "tier": "macro",
            "seeds": [1, 2],
            "run_root": tmp_path / run_id,
        }

    # The second row collides with an existing id → IntegrityError; the first
    # row must not survive and the grid must be unchanged.
    existing = store.create_run(
        scenario_id=None, config={}, config_hash="x", tier="macro", seeds=[1], run_root=tmp_path
    )
    grid[0]["run_id"], grid[1]["run_id"] = "run_new1", existing
    with pytest.raises(sqlite3.IntegrityError):
        store.create_sweep_runs(sweep_id, grid, [spec("run_new1"), spec(existing)])
    assert store.get_run("run_new1") is None
    assert all(cell["run_id"] is None for cell in store.get_sweep(sweep_id)["grid"])

    grid[1]["run_id"] = "run_new2"
    store.create_sweep_runs(sweep_id, grid, [spec("run_new1"), spec("run_new2")])
    rows = store.list_runs(sweep_id=sweep_id)
    assert [r["id"] for r in rows] == ["run_new1", "run_new2"]
    assert all(r["status"] == "queued" and r["total_replicates"] == 2 for r in rows)
    assert [c["run_id"] for c in store.get_sweep(sweep_id)["grid"]] == ["run_new1", "run_new2"]


def test_list_reports_newest_first_and_limited(tmp_path: Path) -> None:
    """``GET /reports`` is a server-side list, so the rows come back newest first."""
    store = Store(tmp_path / "meta.db")
    ids = [store.create_report([f"run_{i}"], f"report {i}") for i in range(3)]

    assert [r["id"] for r in store.list_reports()] == list(reversed(ids))
    assert [r["id"] for r in store.list_reports(limit=2)] == list(reversed(ids))[:2]

    newest = store.list_reports(limit=1)[0]
    assert newest["run_ids"] == ["run_2"]  # JSON column decoded like get_report
    assert newest["status"] == "queued"

    with pytest.raises(ValueError, match="limit"):
        store.list_reports(limit=0)


def test_list_reports_is_empty_on_a_fresh_store(tmp_path: Path) -> None:
    assert Store(tmp_path / "meta.db").list_reports() == []
