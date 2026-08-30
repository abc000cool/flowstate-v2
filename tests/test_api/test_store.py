"""Direct Store tests: WAL mode, round trips, status guards."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from api.store import Store


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
