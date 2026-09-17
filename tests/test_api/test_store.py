"""Direct Store tests: WAL mode, round trips, status guards, lifecycle CAS."""

from __future__ import annotations

import multiprocessing
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from api.schemas import DEFAULT_CRITERIA_PROFILE
from api.store import KINDS, Store, _add_missing_columns, kind_of_id, new_id

#: Repository root — spawned children import this module from here (see
#: ``test_concurrent_store_init_on_an_upgraded_database``).
REPO_ROOT = Path(__file__).resolve().parents[2]


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


# ---------------------------------------------------------------------------
# Schema migration of a database written by an earlier version
# ---------------------------------------------------------------------------

#: The ``reports`` table as it shipped before the ``profile`` column, i.e. the
#: state an upgraded deployment's database file is in on first open.
_REPORTS_BEFORE_PROFILE = """
CREATE TABLE reports (
    id            TEXT PRIMARY KEY,
    run_ids_json  TEXT NOT NULL,
    title         TEXT NOT NULL,
    status        TEXT NOT NULL,
    report_dir    TEXT,
    report_path   TEXT,
    error         TEXT,
    error_kind    TEXT,
    created_at    TEXT NOT NULL
);
"""


def _pre_profile_database(path: Path) -> Path:
    """A store file whose ``reports`` table predates the ``profile`` column.

    WAL, like every file an earlier :class:`Store` wrote: a *journal-mode*
    conversion needs exclusive access, so a non-WAL fixture would make the
    concurrent opens below race over that instead of over the migration.
    """
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(_REPORTS_BEFORE_PROFILE)
        con.commit()
    finally:
        con.close()
    assert not _report_columns(path) & {"profile"}
    return path


def _report_columns(path: Path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {str(row[1]) for row in con.execute("PRAGMA table_info(reports)")}
    finally:
        con.close()


def _profile_column_count(path: Path) -> int:
    con = sqlite3.connect(path)
    try:
        return sum(1 for row in con.execute("PRAGMA table_info(reports)") if row[1] == "profile")
    finally:
        con.close()


def _open_store_at_the_barrier(db_path: str, barrier: Any, outcomes: Any) -> None:
    """Child process: wait for the others, then open the store (spawn-safe)."""
    from api.store import Store

    try:
        barrier.wait(timeout=60)
        Store(db_path)
    except BaseException as exc:  # reported, not raised: the parent asserts on it
        outcomes.put(f"{type(exc).__name__}: {exc}")
    else:
        outcomes.put("")


def test_concurrent_store_init_on_an_upgraded_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API and the workers start together against one upgraded database.

    ``ALTER TABLE ... ADD COLUMN`` is guarded by a ``PRAGMA table_info``
    check, and SQLite has no ``ADD COLUMN IF NOT EXISTS``, so every process
    but the first sees the column appear between its check and its ALTER.
    That lost race must not crash ``Store()`` — a container that cannot open
    the store is a failed deployment — and must not add the column twice.

    Whether any process actually loses the race here is up to the scheduler
    (the window between the check and the ALTER is microseconds), so this
    covers the real multi-process path end to end; the two tests below pin
    the tolerate-and-verify branch itself deterministically.
    """
    # A spawned child unpickles the target by module name and inherits the
    # parent's sys.path; pytest's importlib mode registers test modules in
    # sys.modules without putting the repository root on the path.
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    db_path = _pre_profile_database(tmp_path / "meta.db")
    ctx = multiprocessing.get_context("spawn")
    n_procs = 6
    barrier = ctx.Barrier(n_procs)
    outcomes: Any = ctx.Queue()
    procs = [
        ctx.Process(target=_open_store_at_the_barrier, args=(str(db_path), barrier, outcomes))
        for _ in range(n_procs)
    ]
    try:
        for proc in procs:
            proc.start()
        reported = [outcomes.get(timeout=60) for _ in range(n_procs)]
        for proc in procs:
            proc.join(timeout=30)
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=10)

    assert reported == [""] * n_procs, reported
    assert [proc.exitcode for proc in procs] == [0] * n_procs
    assert _profile_column_count(db_path) == 1
    # And the migrated table is usable: a report row round-trips with the
    # default the migration declared.
    store = Store(db_path)
    rid = store.create_report(["run_x"], title="t")
    report = store.get_report(rid)
    assert report is not None and report["profile"] == DEFAULT_CRITERIA_PROFILE


class _AlterRaises:
    """A connection whose ``ALTER`` fails, optionally after the column lands.

    Stands in for the losing racer above without needing the race: the other
    process's ALTER is applied first (``add_column``) and ours then fails,
    exactly as SQLite reports it.
    """

    def __init__(self, con: sqlite3.Connection, message: str, *, add_column: bool) -> None:
        self._con = con
        self._message = message
        self._add_column = add_column

    def execute(self, sql: str, *args: Any) -> Any:
        if sql.lstrip().upper().startswith("ALTER"):
            if self._add_column:
                self._con.execute(sql, *args)
            raise sqlite3.OperationalError(self._message)
        return self._con.execute(sql, *args)


@pytest.mark.parametrize(
    "message",
    ["duplicate column name: profile", "table reports has more than one column named profile"],
    ids=["sqlite-wording", "some-other-wording"],
)
def test_a_lost_alter_race_is_verified_not_pattern_matched(tmp_path: Path, message: str) -> None:
    """Tolerated because the column is *there*, whatever the driver said.

    The guard used to match the message prefix, which tied the store's
    startup to SQLite's wording; the column list is the fact that matters.
    """
    db_path = _pre_profile_database(tmp_path / "meta.db")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        _add_missing_columns(_AlterRaises(con, message, add_column=True))  # type: ignore[arg-type]
        con.commit()
    finally:
        con.close()
    assert _profile_column_count(db_path) == 1


def test_a_failed_alter_is_re_raised_when_the_column_is_still_missing(tmp_path: Path) -> None:
    """A migration that did not happen must not be reported as one that did.

    ``duplicate column name`` is the message of the benign race, so matching
    on it swallowed this case: the store would open with a ``reports`` table
    the queries need a column of.
    """
    db_path = _pre_profile_database(tmp_path / "meta.db")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        with pytest.raises(sqlite3.OperationalError):
            _add_missing_columns(
                _AlterRaises(con, "duplicate column name: profile", add_column=False)  # type: ignore[arg-type]
            )
    finally:
        con.close()
    assert _profile_column_count(db_path) == 0


def test_wal_conversion_waits_out_a_cold_start_lock(tmp_path: Path) -> None:
    """A fresh database held under a write lock by another connection: the
    journal-mode switch used to fail at once with ``database is locked``; it
    now waits (bounded) and succeeds once the lock is released."""
    import sqlite3
    import threading
    import time

    from api.store import _set_wal

    db = tmp_path / "cold.db"
    holder = sqlite3.connect(db, isolation_level=None, check_same_thread=False)
    holder.execute("CREATE TABLE t (x INTEGER)")
    holder.execute("BEGIN IMMEDIATE")  # write lock; journal-mode change must wait

    def release() -> None:
        time.sleep(0.3)
        holder.execute("COMMIT")

    threading.Thread(target=release, daemon=True).start()
    con = sqlite3.connect(db, timeout=0.05)
    t0 = time.monotonic()
    _set_wal(con)  # raised OperationalError before the retry existed
    assert time.monotonic() - t0 >= 0.25
    assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    con.close()
    holder.close()
