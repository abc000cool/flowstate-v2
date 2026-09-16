"""SQLite metadata store (stdlib ``sqlite3``, WAL mode).

Holds *metadata only* — scenario configs, run/sweep/calibration/report status,
progress, seed lists, and paths under the results root. Results payloads
(Parquet trajectories/edges, calibration artifacts, report bundles) stay on
disk per docs/CONTRACTS.md §3/§5; the upgrade path to Postgres is documented
in CLAUDE.md §10 and nothing here depends on SQLite-only semantics beyond
single-file convenience.

Concurrency: the API process and RQ worker processes share the database, so
every operation opens a fresh connection with WAL journaling and a generous
busy timeout. Rows in/out are plain dicts with JSON columns already decoded.

Lifecycle guards: a job *claims* its row with a compare-and-set
(:meth:`Store.claim` — ``queued``/``failed`` → ``running``) so a duplicate
delivery of the same job is a no-op, and abandoned rows are failed with
:meth:`Store.fail_active`, which never overwrites a finished row. Row ids are
prefixed per kind (``run_…``, ``swp_…``, ``cal_…``, ``rpt_…``) and double as
the RQ job ids, so :func:`kind_of_id` maps a queue job back to its row.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Job/record lifecycle states.
STATUSES = ("queued", "running", "done", "failed")

#: Row kinds that go through the job queue, in reconciliation order.
KINDS = ("sweep", "run", "calibration", "report")

_TABLES = {"run": "runs", "sweep": "sweeps", "calibration": "calibrations", "report": "reports"}

#: Id prefix per kind (``new_id`` argument); the reverse map serves ``kind_of_id``.
ID_PREFIXES = {"run": "run", "sweep": "swp", "calibration": "cal", "report": "rpt"}
_KIND_OF_PREFIX = {prefix: kind for kind, prefix in ID_PREFIXES.items()}

#: Columns a claim resets besides ``status``: a re-run starts from a clean row.
_CLAIM_RESET_COLUMNS = {
    "run": ("error", "error_kind", "completed_replicates"),
    "sweep": ("error",),
    "calibration": ("error", "artifact_path"),
    "report": ("error", "error_kind", "report_dir", "report_path"),
}
_CLAIM_RESET_VALUES: dict[str, Any] = {"completed_replicates": 0}

#: Statuses a claim may take over: fresh rows and failed ones (so an
#: operator's ``rq requeue`` of a failed job just works). Never ``running``
#: (a live execution) or ``done``.
CLAIMABLE = ("queued", "failed")

#: Statuses that still await a job outcome; only these may be failed by
#: reconciliation or the work-horse death handler.
ACTIVE = ("queued", "running")

_BUSY_TIMEOUT_MS = 30_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    config_json TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id                   TEXT PRIMARY KEY,
    scenario_id          TEXT,
    sweep_id             TEXT,
    config_json          TEXT NOT NULL,
    config_hash          TEXT NOT NULL,
    tier                 TEXT NOT NULL,
    status               TEXT NOT NULL,
    error                TEXT,
    error_kind           TEXT,
    completed_replicates INTEGER NOT NULL DEFAULT 0,
    total_replicates     INTEGER NOT NULL,
    seeds_json           TEXT NOT NULL,
    run_root             TEXT NOT NULL,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_scenario ON runs (scenario_id);
CREATE INDEX IF NOT EXISTS idx_runs_sweep ON runs (sweep_id);
CREATE TABLE IF NOT EXISTS sweeps (
    id          TEXT PRIMARY KEY,
    scenario_id TEXT,
    grid_json   TEXT NOT NULL,
    status      TEXT NOT NULL,
    error       TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calibrations (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    params_json   TEXT NOT NULL,
    data_path     TEXT NOT NULL,
    source        TEXT NOT NULL,
    artifact_path TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
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


def now_iso() -> str:
    """Current UTC time, ISO-8601 (metadata bookkeeping, not physics)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    """Short unique id, e.g. ``run_1a2b3c4d5e6f``."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def kind_of_id(row_id: str) -> str | None:
    """Row kind (``run``/``sweep``/``calibration``/``report``) from an id prefix.

    Queue job ids equal store row ids, so this is how the worker's
    work-horse-death handler finds the row to fail. ``None`` for ids that are
    not one of ours (scenarios, uploads, foreign jobs).
    """
    prefix, sep, _ = row_id.partition("_")
    return _KIND_OF_PREFIX.get(prefix) if sep else None


def _table(kind: str) -> str:
    try:
        return _TABLES[kind]
    except KeyError:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}") from None


class Store:
    """Metadata store bound to one SQLite file (created on first use)."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as con:
            con.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_MS / 1000.0)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            yield con
            con.commit()
        finally:
            con.close()

    def check(self) -> None:
        """Health probe: raises if the database is unreachable."""
        with self._conn() as con:
            con.execute("SELECT 1").fetchone()

    # -- scenarios ---------------------------------------------------------

    def create_scenario(self, name: str, config: dict[str, Any], config_hash: str) -> str:
        sid = new_id("scn")
        with self._conn() as con:
            con.execute(
                "INSERT INTO scenarios (id, name, config_json, config_hash, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (sid, name, json.dumps(config), config_hash, now_iso()),
            )
        return sid

    def get_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
        return _scenario_dict(row) if row else None

    def list_scenarios(self) -> list[dict[str, Any]]:
        with self._conn() as con:
            # rowid preserves insertion order (created_at has 1 s resolution).
            rows = con.execute("SELECT * FROM scenarios ORDER BY rowid").fetchall()
        return [_scenario_dict(r) for r in rows]

    # -- runs --------------------------------------------------------------

    def create_run(
        self,
        *,
        scenario_id: str | None,
        config: dict[str, Any],
        config_hash: str,
        tier: str,
        seeds: list[int],
        run_root: str | Path,
        sweep_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        """Insert a queued run row; ``run_id`` may be pre-generated so the
        caller can embed it in ``run_root`` before the insert."""
        rid = run_id if run_id is not None else new_id("run")
        with self._conn() as con:
            _insert_run(
                con,
                run_id=rid,
                scenario_id=scenario_id,
                sweep_id=sweep_id,
                config=config,
                config_hash=config_hash,
                tier=tier,
                seeds=seeds,
                run_root=run_root,
            )
        return rid

    def create_sweep_runs(
        self, sweep_id: str, grid: list[dict[str, Any]], new_runs: list[dict[str, Any]]
    ) -> None:
        """Insert a sweep's child run rows and its updated grid in ONE transaction.

        ``new_runs`` holds :func:`_insert_run` keyword sets (``run_id``,
        ``scenario_id``, ``config``, ``config_hash``, ``tier``, ``seeds``,
        ``run_root``); ``grid`` is the sweep grid with those ``run_id`` values
        filled in. Either every row lands together with the grid that points
        at it, or nothing changes — so a fan-out interrupted mid-way can never
        leave a cell with a row the grid does not know about (or the reverse),
        and a re-run finds either the complete child set or none of it.
        """
        with self._conn() as con:
            for spec in new_runs:
                _insert_run(con, sweep_id=sweep_id, **spec)
            con.execute(
                "UPDATE sweeps SET grid_json = ? WHERE id = ?", (json.dumps(grid), sweep_id)
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _run_dict(row) if row else None

    def list_runs(
        self, scenario_id: str | None = None, sweep_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM runs"
        clauses: list[str] = []
        args: list[Any] = []
        if scenario_id is not None:
            clauses.append("scenario_id = ?")
            args.append(scenario_id)
        if sweep_id is not None:
            clauses.append("sweep_id = ?")
            args.append(sweep_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY rowid"  # insertion order (created_at has 1 s resolution)
        with self._conn() as con:
            rows = con.execute(query, args).fetchall()
        return [_run_dict(r) for r in rows]

    def set_run_status(
        self,
        run_id: str,
        status: str,
        *,
        error: str | None = None,
        error_kind: str | None = None,
    ) -> None:
        _check_status(status)
        with self._conn() as con:
            con.execute(
                "UPDATE runs SET status = ?, error = ?, error_kind = ? WHERE id = ?",
                (status, error, error_kind, run_id),
            )

    def set_run_progress(self, run_id: str, completed_replicates: int) -> None:
        with self._conn() as con:
            con.execute(
                "UPDATE runs SET completed_replicates = ? WHERE id = ?",
                (completed_replicates, run_id),
            )

    # -- sweeps ------------------------------------------------------------

    def create_sweep(self, scenario_id: str | None, grid: list[dict[str, Any]]) -> str:
        wid = new_id("swp")
        with self._conn() as con:
            con.execute(
                "INSERT INTO sweeps (id, scenario_id, grid_json, status, created_at)"
                " VALUES (?, ?, ?, 'queued', ?)",
                (wid, scenario_id, json.dumps(grid), now_iso()),
            )
        return wid

    def get_sweep(self, sweep_id: str) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM sweeps WHERE id = ?", (sweep_id,)).fetchone()
        return _sweep_dict(row) if row else None

    def set_sweep_status(self, sweep_id: str, status: str, *, error: str | None = None) -> None:
        _check_status(status)
        with self._conn() as con:
            con.execute(
                "UPDATE sweeps SET status = ?, error = ? WHERE id = ?", (status, error, sweep_id)
            )

    def set_sweep_grid(self, sweep_id: str, grid: list[dict[str, Any]]) -> None:
        with self._conn() as con:
            con.execute(
                "UPDATE sweeps SET grid_json = ? WHERE id = ?", (json.dumps(grid), sweep_id)
            )

    # -- calibrations ------------------------------------------------------

    def create_calibration(
        self, kind: str, data_path: str | Path, params: dict[str, Any], source: str
    ) -> str:
        cid = new_id("cal")
        with self._conn() as con:
            con.execute(
                "INSERT INTO calibrations (id, kind, status, params_json, data_path,"
                " source, created_at) VALUES (?, ?, 'queued', ?, ?, ?, ?)",
                (cid, kind, json.dumps(params), str(data_path), source, now_iso()),
            )
        return cid

    def get_calibration(self, calibration_id: str) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM calibrations WHERE id = ?", (calibration_id,)
            ).fetchone()
        return _calibration_dict(row) if row else None

    def set_calibration_status(
        self,
        calibration_id: str,
        status: str,
        *,
        artifact_path: str | None = None,
        error: str | None = None,
    ) -> None:
        _check_status(status)
        with self._conn() as con:
            con.execute(
                "UPDATE calibrations SET status = ?, artifact_path = ?, error = ? WHERE id = ?",
                (status, artifact_path, error, calibration_id),
            )

    # -- reports -----------------------------------------------------------

    def create_report(self, run_ids: list[str], title: str) -> str:
        pid = new_id("rpt")
        with self._conn() as con:
            con.execute(
                "INSERT INTO reports (id, run_ids_json, title, status, created_at)"
                " VALUES (?, ?, ?, 'queued', ?)",
                (pid, json.dumps(run_ids), title, now_iso()),
            )
        return pid

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return _report_dict(row) if row else None

    def set_report_status(
        self,
        report_id: str,
        status: str,
        *,
        report_dir: str | None = None,
        report_path: str | None = None,
        error: str | None = None,
        error_kind: str | None = None,
    ) -> None:
        _check_status(status)
        with self._conn() as con:
            con.execute(
                "UPDATE reports SET status = ?, report_dir = ?, report_path = ?,"
                " error = ?, error_kind = ? WHERE id = ?",
                (status, report_dir, report_path, error, error_kind, report_id),
            )

    # -- lifecycle guards, all kinds ----------------------------------------

    def get(self, kind: str, row_id: str) -> dict[str, Any] | None:
        """One row of ``kind`` (see :data:`KINDS`) as its usual dict, or None."""
        with self._conn() as con:
            row = con.execute(f"SELECT * FROM {_table(kind)} WHERE id = ?", (row_id,)).fetchone()
        return _CONVERTERS[kind](row) if row else None

    def list_by_status(self, kind: str, statuses: Iterable[str]) -> list[dict[str, Any]]:
        """Rows of ``kind`` whose status is in ``statuses`` (insertion order)."""
        wanted = tuple(statuses)
        for status in wanted:
            _check_status(status)
        if not wanted:
            return []
        marks = ", ".join("?" for _ in wanted)
        with self._conn() as con:
            rows = con.execute(
                f"SELECT * FROM {_table(kind)} WHERE status IN ({marks}) ORDER BY rowid", wanted
            ).fetchall()
        return [_CONVERTERS[kind](r) for r in rows]

    def claim(self, kind: str, row_id: str) -> bool:
        """Compare-and-set ``queued``/``failed`` → ``running``; True if it took.

        The job function calls this first and returns without working when
        it answers False: the row is already ``running`` under another
        execution (a duplicate delivery of the same job) or ``done``. A
        claim also clears the previous outcome (error, progress, artifact
        paths — :data:`_CLAIM_RESET_COLUMNS`), so a requeued failed job
        starts from a clean row.
        """
        sets = ["status = 'running'"]
        values: list[Any] = []
        for column in _CLAIM_RESET_COLUMNS[kind]:
            sets.append(f"{column} = ?")
            values.append(_CLAIM_RESET_VALUES.get(column))
        marks = ", ".join("?" for _ in CLAIMABLE)
        with self._conn() as con:
            cur = con.execute(
                f"UPDATE {_table(kind)} SET {', '.join(sets)} WHERE id = ? AND status IN ({marks})",
                (*values, row_id, *CLAIMABLE),
            )
            return cur.rowcount == 1

    def claim_run(self, run_id: str) -> bool:
        return self.claim("run", run_id)

    def claim_sweep(self, sweep_id: str) -> bool:
        return self.claim("sweep", sweep_id)

    def claim_calibration(self, calibration_id: str) -> bool:
        return self.claim("calibration", calibration_id)

    def claim_report(self, report_id: str) -> bool:
        return self.claim("report", report_id)

    def fail_active(self, kind: str, row_id: str, error: str) -> bool:
        """Mark a ``queued``/``running`` row ``failed``; True if it was active.

        Used when the *job* can no longer report for itself — its work horse
        was killed, or its Redis record vanished — so the row is failed from
        outside. A row that already reached ``done`` or ``failed`` is left
        exactly as it is: an outcome recorded by the job itself always wins.
        """
        marks = ", ".join("?" for _ in ACTIVE)
        with self._conn() as con:
            cur = con.execute(
                f"UPDATE {_table(kind)} SET status = 'failed', error = ?"
                f" WHERE id = ? AND status IN ({marks})",
                (error, row_id, *ACTIVE),
            )
            return cur.rowcount == 1

    def reset_to_queued(self, kind: str, row_id: str) -> bool:
        """``running`` → ``queued`` for a row whose job is back in the queue.

        Reconciliation calls this when a row says ``running`` but its RQ job
        is waiting for a worker (an operator requeued it while the row was
        stuck): the claim that the next execution makes requires ``queued``.
        """
        with self._conn() as con:
            cur = con.execute(
                f"UPDATE {_table(kind)} SET status = 'queued' WHERE id = ? AND status = 'running'",
                (row_id,),
            )
            return cur.rowcount == 1


def _insert_run(
    con: sqlite3.Connection,
    *,
    run_id: str,
    scenario_id: str | None,
    sweep_id: str | None,
    config: dict[str, Any],
    config_hash: str,
    tier: str,
    seeds: list[int],
    run_root: str | Path,
) -> None:
    """The one ``INSERT INTO runs`` (shared by single runs and sweep fan-out)."""
    con.execute(
        "INSERT INTO runs (id, scenario_id, sweep_id, config_json, config_hash,"
        " tier, status, completed_replicates, total_replicates, seeds_json,"
        " run_root, created_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)",
        (
            run_id,
            scenario_id,
            sweep_id,
            json.dumps(config),
            config_hash,
            tier,
            len(seeds),
            json.dumps(seeds),
            str(run_root),
            now_iso(),
        ),
    )


def _check_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")


def _scenario_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["config"] = json.loads(d.pop("config_json"))
    return d


def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["config"] = json.loads(d.pop("config_json"))
    d["seeds"] = json.loads(d.pop("seeds_json"))
    return d


def _sweep_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["grid"] = json.loads(d.pop("grid_json"))
    return d


def _calibration_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["params"] = json.loads(d.pop("params_json"))
    return d


def _report_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["run_ids"] = json.loads(d.pop("run_ids_json"))
    return d


_CONVERTERS: dict[str, Callable[[sqlite3.Row], dict[str, Any]]] = {
    "run": _run_dict,
    "sweep": _sweep_dict,
    "calibration": _calibration_dict,
    "report": _report_dict,
}
