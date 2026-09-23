"""Job functions executed by RQ workers, plus the queue plumbing.

Queue selection (env ``FLOWSTATE_QUEUE``):

- ``redis`` — jobs go to an RQ queue on ``FLOWSTATE_REDIS_URL`` and are
  executed by ``python -m api.worker`` processes. No endpoint ever executes a
  simulation synchronously in this mode (CLAUDE.md §8).
- ``inline`` — jobs execute synchronously in-process; for tests and small
  local runs only.

Every job takes ids plus explicit ``db_path``/``results_root`` (both default
from the environment for plain RQ workers) so that API, worker, and tests all
address the same store without hidden global state. Jobs update store
status/progress as they go and record failures honestly — the error column
carries the exception, never a silent success (CLAUDE.md §0.1).

Failure records (:func:`_error_text`) are the exception *chain* — one
``Type: message`` line per cause — never a traceback: the API hands the
column to any key holder, and traceback frames would carry the server's
absolute paths and source lines. The full traceback goes to the worker log
instead (``logging.exception``). Calibration jobs go one step further
(:func:`_calibration_error_text`) and withhold third-party messages, which
can quote the parsed file's contents — so the common bad-upload shapes are
diagnosed here (:func:`_read_calibration_csv`) and re-raised as messages this
package authored, naming the column and row but never the cell value.

Failure records may name paths *under the results root* (a run root, a
staged bundle): those identify the artifact, the way ``report_path`` and
``data_path`` do on the success path. What they never carry is a traceback
frame, a server source path, file contents or a pydantic input value.

Lifecycle guards. RQ job ids equal store row ids (``enqueue(...,
job_id=row_id)``), so a row can always be looked up from its job and vice
versa. Each job *claims* its row first (:meth:`api.store.Store.claim`, a
compare-and-set ``queued``/``failed`` → ``running``) and returns without
working when the claim fails — a duplicate delivery of the same job is a
no-op rather than a second execution. Rows whose job can no longer report
(work horse killed, Redis restarted, worker container gone) are failed from
outside by :func:`reconcile_store` and the worker's horse-death handler
(:mod:`api.worker`).

Simulation dispatch: micro-tier runs go through
:func:`microsim.runner.run_replicates`, which parallelizes across *spawned*
subprocesses (one libsumo per process); macro-tier runs execute
:func:`macrosim.runner.run_macro` per replicate with seeds from
:func:`flowstate_core.rng.spawn_seeds` — the identical seed list the micro
path derives, recorded on the run row at creation time.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import logging
import os
import shutil
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from api.settings import Settings, load_settings
from api.store import KINDS, Store, new_id, now_iso

if TYPE_CHECKING:
    from rq.job import Job

    from flowstate_core.artifacts import FDCalibration

_log = logging.getLogger(__name__)

#: RQ queue name shared by the API producer and ``api.worker`` consumers.
QUEUE_NAME = "flowstate"

#: Generous per-job cap for Redis-backed jobs (sweep fan-out itself is quick;
#: individual replicate sets can be hours at 20+ replicates).
JOB_TIMEOUT = "6h"

#: Error-kind marker for validation-report refusals (surfaced as HTTP 422).
REPORT_REFUSED_KIND = "report_refused"

_ERROR_MAX_CHARS = 4000

#: FlowState's own packages. Exception messages raised inside them are strings
#: we wrote ("missing column 'density_veh_m'"); messages from anywhere else can
#: quote a *value* out of the file being parsed (pandas, for instance, raises
#: "could not convert string to float: '<cell contents>'"), so those are
#: reported by type and raising module only. See :func:`_calibration_error_text`.
_OWN_PACKAGES = (
    "api",
    "calibration",
    "controllers",
    "flowstate_core",
    "macrosim",
    "microsim",
    "validation",
)

#: Artifact files staged per replicate for report generation.
_REPLICATE_FILES = ("meta.json", "edges.parquet", "trajectories.parquet")

#: Reconciliation leaves ``queued`` rows younger than this alone: the API
#: inserts the row *before* it enqueues the job, and a concurrent sweep must
#: not re-enqueue a row whose first enqueue is still in flight.
QUEUED_GRACE_S = 60.0


class JobQueue(Protocol):
    """Minimal queue interface the API codes against."""

    kind: str

    def enqueue(
        self, func: Callable[..., Any], *args: Any, job_id: str | None = None, **kwargs: Any
    ) -> None:
        """Schedule ``func(*args, **kwargs)`` for execution.

        ``job_id`` names the queue job; pass the store row id so the job and
        the row can always be matched (and so a job already present under
        that id is not enqueued twice).
        """
        ...

    def check(self) -> None:
        """Health probe; raises when the backend is unreachable."""
        ...


class InlineQueue:
    """Synchronous in-process execution (tests and small local runs)."""

    kind = "inline"

    def enqueue(
        self, func: Callable[..., Any], *args: Any, job_id: str | None = None, **kwargs: Any
    ) -> None:
        func(*args, **kwargs)

    def check(self) -> None:
        return None


class RedisQueue:
    """RQ-backed queue on Redis (the production mode).

    With ``job_id`` given the enqueue is *unique*: RQ refuses (atomically, in
    a Lua script) to create a second job under an id that still exists in
    Redis, and that refusal is logged and swallowed here — the caller wanted
    the job to exist, and it does. Without ``job_id`` RQ picks a random id.
    """

    kind = "redis"

    def __init__(self, redis_url: str, name: str = QUEUE_NAME) -> None:
        import redis
        import rq

        self.connection = redis.Redis.from_url(redis_url)
        self.queue = rq.Queue(name, connection=self.connection)

    def enqueue(
        self, func: Callable[..., Any], *args: Any, job_id: str | None = None, **kwargs: Any
    ) -> None:
        from rq.exceptions import DuplicateJobError

        try:
            self.queue.enqueue(
                func,
                args=args,
                kwargs=kwargs,
                job_timeout=JOB_TIMEOUT,
                job_id=job_id,
                unique=job_id is not None,
            )
        except DuplicateJobError:
            _log.info("job %s already exists in Redis; not enqueued again", job_id)

    def check(self) -> None:
        self.connection.ping()


def get_queue(settings: Settings | None = None) -> JobQueue:
    """Queue selected by ``FLOWSTATE_QUEUE`` (``redis`` | ``inline``)."""
    s = settings if settings is not None else load_settings()
    if s.queue_kind == "redis":
        return RedisQueue(s.redis_url)
    return InlineQueue()


def _resolve(db_path: str | None, results_root: str | None) -> tuple[Store, Path]:
    """Store + results root from explicit args, else from the environment."""
    if db_path is None or results_root is None:
        s = load_settings()
        db_path = db_path or str(s.db_path)
        results_root = results_root or str(s.results_dir)
    return Store(db_path), Path(results_root)


# ---------------------------------------------------------------------------
# Worker-side path confinement
# ---------------------------------------------------------------------------

#: Environment variable through which a job publishes the directories its
#: simulation may read scenario-config file paths from. It is
#: ``microsim.paths.ROOTS_ENV_VAR``; the literal is repeated rather than
#: imported because importing :mod:`microsim.paths` pulls the whole SUMO
#: micro tier into a macro-tier or calibration worker
#: (``tests/test_api/test_worker_path_roots.py`` asserts the two agree).
WORKER_PATH_ROOTS_ENV = "FLOWSTATE_WORKER_PATH_ROOTS"


def _worker_path_roots(results_root: Path) -> tuple[Path, ...]:
    """Roots a run's ``osm_file`` / ``idm_calibration`` may resolve inside.

    :attr:`api.settings.Settings.config_path_roots` — the same allow-list the
    API enforces with HTTP 422 at ``POST /scenarios``, ``/runs`` and
    ``/sweeps`` — plus the job's own results root when it was passed
    explicitly and the environment's settings do not already cover it (tests
    and one-off workers point at a temporary tree).
    """
    roots = list(load_settings().config_path_roots)
    resolved = results_root.resolve()
    if not any(resolved.is_relative_to(root) for root in roots):
        roots.append(resolved)
    return tuple(roots)


@contextmanager
def _confined_worker_paths(roots: Sequence[Path]) -> Iterator[None]:
    """Publish ``roots`` to the simulation in :data:`WORKER_PATH_ROOTS_ENV`.

    Defence in depth behind the API's 422 (CHANGELOG 2026-09-16, "Security
    (API)"): ``microsim.vehicles.resolve_calibration_path`` and
    ``microsim.networks.osm_import`` refuse a path resolving outside these
    roots, so a config that reached the store without passing
    ``api.main._confine_config_paths`` still cannot make the worker read an
    arbitrary server file.

    The environment is the channel because the micro tier fans replicates out
    into a *spawn* process pool (``microsim.runner.run_replicates``): children
    inherit the environment of the process that spawns them, so the roots
    reach every replicate without a signature change on the runner. Restored
    on exit, so an inline-queue job leaves the API process as it found it.
    """
    previous = os.environ.get(WORKER_PATH_ROOTS_ENV)
    os.environ[WORKER_PATH_ROOTS_ENV] = os.pathsep.join(str(r) for r in roots)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(WORKER_PATH_ROOTS_ENV, None)
        else:
            os.environ[WORKER_PATH_ROOTS_ENV] = previous


# ---------------------------------------------------------------------------
# Failure records
# ---------------------------------------------------------------------------


def _raising_module(exc: BaseException) -> str:
    """Module name of the innermost frame in ``exc``'s traceback."""
    tb = exc.__traceback__
    module = ""
    while tb is not None:
        module = str(tb.tb_frame.f_globals.get("__name__", ""))
        tb = tb.tb_next
    return module


def _exception_chain_text(exc: BaseException, *, withhold_third_party: bool) -> str:
    """The ``cause``-ordered exception chain, one ``Type: message`` line each.

    No traceback frames, ever: they would add the server's absolute source
    paths and code lines to a string the API returns verbatim, and the
    operator can read the same frames in the worker log. That holds for the
    frames a *child* process sends back too: the ``RemoteTraceback`` the micro
    tier's spawn pool attaches as a failed replicate's cause is a formatted
    traceback in message form, so it is replaced by a one-line placeholder
    regardless of ``withhold_third_party``. With ``withhold_third_party`` the
    *message* is also dropped for exceptions raised outside FlowState's own
    packages (see :data:`_OWN_PACKAGES`).
    """
    lines: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        module = _raising_module(current)
        name = type(current).__name__
        if name in (
            "RemoteTraceback",
            "_RemoteTraceback",
        ) or "Traceback (most recent call last)" in str(current):
            # A replicate that fails inside the micro tier's spawn pool is
            # re-raised in this process with the child's *formatted traceback*
            # attached as its cause — server paths and source lines, i.e.
            # exactly what this function exists to keep out. The child's own
            # exception is the line above it; the frames stay in the log.
            lines.append(f"{name}: child traceback withheld (see the worker log)")
        elif withhold_third_party and module.split(".")[0] not in _OWN_PACKAGES:
            lines.append(
                f"{name} raised in {module or '<unknown>'} "
                f"(message withheld: it may quote the input file's contents)"
            )
        elif name == "ValidationError" and hasattr(current, "errors"):
            # pydantic quotes the offending input in its message; when the
            # input was a file the worker read (a calibration artifact, a
            # network), that is the file's contents. Keep locations and
            # messages, drop the input values.
            try:
                items = current.errors(include_input=False)  # type: ignore[attr-defined]
                summary = "; ".join(
                    f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}"
                    for e in items[:8]
                )
                lines.append(f"{name}: {len(items)} validation error(s): {summary}")
            except Exception:
                lines.append(f"{name} raised in {module or '<unknown>'} (message withheld)")
        else:
            lines.append(f"{name}: {current}")
        current = current.__cause__ or current.__context__
    return "\n  caused by: ".join(lines)[:_ERROR_MAX_CHARS]


def _error_text(exc: BaseException) -> str:
    """Failure record for runs, sweeps and reports: the exception chain.

    Third-party messages (pydantic, pandas, pyarrow, netconvert) are kept —
    for a simulation they are the legitimate diagnostic ("no such edge",
    "schema mismatch"), and unlike a calibration the job is not parsing a
    caller-supplied data file.
    """
    return _exception_chain_text(exc, withhold_third_party=False)


def _calibration_error_text(exc: BaseException) -> str:
    """Failure record for calibration jobs, with input data withheld.

    ``GET /api/v1/calibrations/{id}`` hands this string to any API-key holder,
    and the job's whole purpose is parsing a data file — so an unfiltered
    exception message is a read channel into that file's contents. Messages
    raised inside FlowState's own packages (:data:`_OWN_PACKAGES`) are
    diagnostics we authored and are kept verbatim; every other message is
    replaced by its exception type and raising module.
    """
    return _exception_chain_text(exc, withhold_third_party=True)


# ---------------------------------------------------------------------------
# Simulation runs
# ---------------------------------------------------------------------------

#: Repository root, the base a *relative* config file path resolves against —
#: the same fallback ``api.main._resolve_config_path`` and
#: ``microsim.vehicles.resolve_calibration_path`` apply, so a shipped preset's
#: ``artifacts/...`` reference works whatever the worker's working directory is.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_fd_calibration(path: str, roots: Sequence[Path]) -> FDCalibration:
    """Read the ``FDCalibration`` artifact a macro run's config names.

    The macro-tier counterpart of
    :func:`microsim.vehicles.load_idm_calibration`, implemented here rather
    than imported from ``microsim`` because importing that package pulls the
    whole SUMO micro tier into a macro-tier worker (see
    :data:`WORKER_PATH_ROOTS_ENV`).

    Containment is decided on the *resolved* candidates, before existence, so
    a refusal can never be read as an existence oracle for a file outside the
    roots; the message names the configured value and the roots and nothing
    read from the file.

    Args:
        path: ``ScenarioConfig.fd_calibration`` as configured — absolute, or
            relative to the worker's working directory or the repository root.
        roots: Directories the artifact must resolve inside
            (:func:`_worker_path_roots`).

    Returns:
        The parsed ``flowstate_core.artifacts.FDCalibration``.

    Raises:
        ValueError: Every candidate resolves outside ``roots``.
        FileNotFoundError: No candidate exists.
    """
    from flowstate_core.artifacts import FDCalibration

    candidates = [Path(path)]
    repo_candidate = _REPO_ROOT / path
    if repo_candidate != candidates[0]:
        candidates.append(repo_candidate)
    inside = [c for c in candidates if any(c.resolve().is_relative_to(r) for r in roots)]
    if not inside:
        raise ValueError(
            f"fd_calibration {path!r} is outside the allowed data roots {[str(r) for r in roots]}"
        )
    for candidate in inside:
        if candidate.is_file():
            return FDCalibration.load(candidate)
    raise FileNotFoundError(
        f"FDCalibration artifact not found: {path!r} (also tried {repo_candidate})"
    )


def run_scenario_job(
    run_id: str, db_path: str | None = None, results_root: str | None = None
) -> None:
    """Execute one run (all replicates) and keep the store's row current.

    Micro tier: :func:`microsim.runner.run_replicates` (multiprocessing spawn
    pool inside this job; progress lands in one step when the pool returns).
    Macro tier: :func:`macrosim.runner.run_macro` per replicate over the run
    row's recorded seed list, with per-replicate progress updates. The last
    step fills every replicate's metrics cache
    (:func:`api.results.precompute_run_metrics`) so the API never reduces a
    trajectory file inside a request.

    A macro run whose config names an ``fd_calibration`` artifact runs on
    that fitted triangular diagram instead of the uncalibrated ``v1_legacy``
    preset (:func:`_load_fd_calibration`), and its ``meta.json["fd"]`` names
    the artifact. Its ``macro`` block (cell length, bottleneck variant) rides
    in the config and is read by the runner itself.

    The simulation runs inside :func:`_confined_worker_paths`, so the config's
    ``osm_file`` / ``idm_calibration`` fields are re-checked against the
    allow-list by the code that opens them — the API's 422 is the first line
    of that defence, this is the second. ``fd_calibration`` is checked against
    the same roots here, before the artifact is opened.

    Returns without working when the row cannot be claimed (already running
    under another execution, or done): a duplicate delivery is a no-op.
    """
    store, root = _resolve(db_path, results_root)
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(f"run {run_id!r} not found in store {store.db_path}")
    if not store.claim_run(run_id):
        _log.info("run %s is %s, not claimable; duplicate delivery ignored", run_id, run["status"])
        return
    try:
        from api.results import precompute_run_metrics
        from flowstate_core.config import ScenarioConfig

        cfg = ScenarioConfig.model_validate(run["config"])
        run_root = Path(run["run_root"])
        run_root.mkdir(parents=True, exist_ok=True)
        roots = _worker_path_roots(root)
        with _confined_worker_paths(roots):
            if cfg.tier == "macro":
                from macrosim.runner import run_macro

                fd = (
                    None
                    if cfg.fd_calibration is None
                    else _load_fd_calibration(cfg.fd_calibration, roots).fd
                )
                for i, seed in enumerate(run["seeds"]):
                    run_macro(cfg, int(seed), run_root, fd=fd, fd_artifact=cfg.fd_calibration)
                    store.set_run_progress(run_id, i + 1)
            else:
                from microsim.runner import run_replicates

                run_replicates(cfg, run_root)
                store.set_run_progress(run_id, len(run["seeds"]))
        precompute_run_metrics(run_root)
        store.set_run_status(run_id, "done")
    except Exception as exc:
        _log.exception("run %s failed", run_id)
        store.set_run_status(run_id, "failed", error=_error_text(exc))


def sweep_job(sweep_id: str, db_path: str | None = None, results_root: str | None = None) -> None:
    """Fan a validated grid out into child runs (one job per grid cell).

    The grid cells (effective configs, validated and hashed at POST time) are
    stored on the sweep row; this job creates the child run rows and enqueues
    one :func:`run_scenario_job` each via the configured queue — synchronous
    under the inline queue, parallel workers under Redis. Sweep status
    ``done`` means the fan-out completed; cell completion is tracked on the
    child run rows.

    Idempotent: the child rows are created in one transaction
    (:meth:`api.store.Store.create_sweep_runs`) with the grid that names
    them, cells whose row already exists are reused rather than re-created,
    and only rows still ``queued`` are (re-)enqueued — under the row id as
    job id, so a job already in Redis is not duplicated. Re-running the job
    after a failure mid-way (``rq requeue``) therefore dispatches exactly the
    cells that never ran and creates no new run rows.
    """
    store, results = _resolve(db_path, results_root)
    sweep = store.get_sweep(sweep_id)
    if sweep is None:
        raise KeyError(f"sweep {sweep_id!r} not found in store {store.db_path}")
    if not store.claim_sweep(sweep_id):
        _log.info(
            "sweep %s is %s, not claimable; duplicate delivery ignored", sweep_id, sweep["status"]
        )
        return
    try:
        from flowstate_core.rng import spawn_seeds

        queue = get_queue()
        grid = sweep["grid"]
        new_runs: list[dict[str, Any]] = []
        for cell in grid:
            run_id = cell.get("run_id")
            if run_id is not None and store.get_run(run_id) is not None:
                continue  # created by an earlier attempt
            if run_id is None:
                run_id = new_id("run")
                cell["run_id"] = run_id
            cfg = cell["config"]
            new_runs.append(
                {
                    "run_id": run_id,
                    "scenario_id": sweep["scenario_id"],
                    "config": cfg,
                    "config_hash": cell["config_hash"],
                    "tier": cfg["tier"],
                    "seeds": spawn_seeds(int(cfg["seed"]), int(cfg["replicates"])),
                    "run_root": results / "runs" / run_id,
                }
            )
        if new_runs:
            store.create_sweep_runs(sweep_id, grid, new_runs)
        for cell in grid:
            run_id = cell["run_id"]
            run = store.get_run(run_id)
            if run is None or run["status"] != "queued":
                continue  # already dispatched (running/done/failed): never silently re-run
            queue.enqueue(
                run_scenario_job,
                run_id,
                job_id=run_id,
                db_path=str(store.db_path),
                results_root=str(results),
            )
        store.set_sweep_status(sweep_id, "done")
    except Exception as exc:
        _log.exception("sweep %s fan-out failed", sweep_id)
        store.set_sweep_status(sweep_id, "failed", error=_error_text(exc))


# ---------------------------------------------------------------------------
# Calibrations
# ---------------------------------------------------------------------------

_FD_FIT_KEYS = (
    "seed",
    "n_bootstrap",
    "congested_quantile",
    "q_max_percentile",
    "min_points",
    "uncongested_max_density",
    "uncongested_max_occupancy",
    # Input-hygiene / cost knobs of ``calibration.fd_fit.fit_triangular_fd``:
    # the row cap (``None`` = fit every row) and the share of non-physical
    # rows tolerated before the fit is refused.
    "max_fit_rows",
    "max_dropped_fraction",
    "notes",
)

_PEMS_LOADER_KEYS = (
    "g_effective_length_m",
    "interval_s",
    "speed_unit",
    "occupancy_unit",
    # Unit-mistake guards of ``calibration.loaders.pems.load_pems_station_csv``.
    "max_speed_ratio_factor",
    "max_out_of_range_fraction",
)

_DETECTOR_LOADER_KEYS = (
    "column_map",
    "speed_unit",
    "occupancy_unit",
    "kind_default",
)

#: Options of the demand/observations job (``api.calibration_jobs``) on top of
#: :data:`_DETECTOR_LOADER_KEYS`.
_OBSERVATIONS_KEYS = (
    "window_s",
    "t0_local",
    "duration_s",
    "upstream_station",
    "stations",
    "ramps",
    "corridor",
    "notes",
)


def pems_loader_kwargs(params: dict[str, Any]) -> dict[str, Any]:
    """PeMS loader options from the request params, with its unit spelling.

    ``CalibrationParams.occupancy_unit`` accepts the detector-CSV spelling
    ``"pct"`` as well as the PeMS one; the PeMS loader only knows
    ``"fraction"``/``"percent"`` and treats anything else as a fraction, so
    the alias is translated here rather than silently changing the scale of a
    whole column.
    """
    kwargs = _subset(params, _PEMS_LOADER_KEYS)
    if kwargs.get("occupancy_unit") == "pct":
        kwargs["occupancy_unit"] = "percent"
    return kwargs


_IDM_FIT_KEYS = (
    "seed",
    "holdout_frac",
    "trim_quantile",
    "de_maxiter",
    "de_popsize",
    "de_tol",
    "notes",
)


def _subset(params: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: params[k] for k in keys if k in params}


#: Columns the FD fit always reads as numbers (``calibration.fd_fit``), and
#: must therefore hold at least one usable value. ``occupancy`` is checked
#: only when it drives the free/congested split (see
#: :func:`fd_calibration_job`), since an explicit density cut makes the FD fit
#: ignore the column entirely.
_FD_REQUIRED_COLUMNS = ("density_veh_m", "flow_veh_s")

#: Columns the IDM episode cutter reads as numbers
#: (``calibration.episodes.episodes_from_pairs``). None is "required" here —
#: the cutter's own FlowState-authored checks cover missing columns and empty
#: episode sets.
_IDM_NUMERIC_COLUMNS = ("t", "gap_m", "v", "v_leader")


def _read_calibration_csv(
    path: Path, numeric_columns: Sequence[str], required_columns: Sequence[str] = ()
) -> Any:
    """Parse a caller-supplied CSV, raising *actionable* FlowState errors.

    :func:`_calibration_error_text` withholds the message of any exception
    raised outside FlowState's own packages, because a third-party parse
    message can quote the file's contents back to the caller (that is the
    point of the withholding, and ``test_calibration_errors_do_not_echo_the_
    input_file`` pins it). The three commonest bad uploads all raise outside
    FlowState — an empty file is ``pandas.errors.EmptyDataError``, a
    header-only file reaches ``numpy.percentile`` on an empty array, and a
    locale-formatted ``"1,234"`` cell raises inside pandas' Arrow string
    array — so the customer used to get ``IndexError raised in
    numpy.lib._function_base_impl`` and no way to tell an empty file from a
    mis-delimited or mis-typed one.

    Each of those is detected here and re-raised as a message *this* package
    authored, which therefore survives the filter. The numeric check names
    the column and the file's 1-based row number and **never the cell
    value** — quoting it would be the very leak the filter prevents.

    Args:
        path: The CSV on the worker's filesystem (already confined to the
            allowed data roots by the API).
        numeric_columns: Columns to type-check when present.
        required_columns: Columns that must additionally hold at least one
            finite value (an all-blank column has rows, so the row-count
            check alone would pass it through to the fit).

    Returns:
        The parsed :class:`pandas.DataFrame`.

    Raises:
        ValueError: Unreadable file, no data rows, a non-numeric cell in one
            of ``numeric_columns``, or an all-blank ``required_columns``
            entry.
    """
    import pandas as pd

    try:
        df = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise ValueError(
            f"{path.name}: not a readable CSV (no header row parsed) — the file is "
            f"empty, truncated, or not delimited text"
        ) from exc
    if df.empty:
        raise ValueError(
            f"{path.name}: parsed {len(df.columns)} column(s) but 0 data rows — the "
            f"upload is header-only or truncated"
        )
    for column in numeric_columns:
        if column not in df.columns:
            continue
        coerced = pd.to_numeric(df[column], errors="coerce")
        bad = df.index[coerced.isna() & df[column].notna()]
        if len(bad):
            # +2: one for the header line, one for 1-based file rows.
            raise ValueError(
                f"{path.name}: column {column!r} is not numeric in {len(bad)} row(s), "
                f"first at file row {int(bad[0]) + 2} — check the delimiter, the decimal "
                f"mark and thousands separators (the offending value is not echoed)"
            )
        if column in required_columns and not bool(coerced.notna().any()):
            raise ValueError(
                f"{path.name}: column {column!r} holds no numeric value in any of "
                f"{len(df)} row(s) — the column is empty or entirely non-numeric"
            )
    return df


def fd_calibration_job(
    calibration_id: str, db_path: str | None = None, results_root: str | None = None
) -> None:
    """Fit a triangular FD from the calibration row's data file (§6.1).

    ``params.loader`` selects the input shape: ``"tidy"`` (default) expects a
    CSV with ``density_veh_m``/``flow_veh_s`` (optional ``occupancy``)
    columns; ``"pems"`` runs the PeMS station-CSV loader first. The
    ``FDCalibration`` artifact is saved under the results root and its path
    recorded on the row. ``"detector_csv"`` reads the tidy detector frame of
    the observations contract instead and converts it to the per-lane
    (density, flow) table: density from ``q/v`` where the frame carries a
    speed, else from occupancy through the same g-factor estimate the PeMS
    path uses (``calibration.loaders.detector_csv.to_fd_frame``).

    Bad uploads are diagnosed before the fit (:func:`_read_calibration_csv`):
    an empty or truncated file, a header-only file, and a non-numeric column
    each get a named, actionable failure record instead of a withheld
    third-party exception type.
    """
    store, results = _resolve(db_path, results_root)
    cal = store.get_calibration(calibration_id)
    if cal is None:
        raise KeyError(f"calibration {calibration_id!r} not found in store {store.db_path}")
    if not store.claim_calibration(calibration_id):
        _log.info(
            "calibration %s is %s, not claimable; duplicate delivery ignored",
            calibration_id,
            cal["status"],
        )
        return
    try:
        import pandas as pd

        from calibration.fd_fit import fit_triangular_fd

        params = cal["params"]
        data_path = Path(cal["data_path"])
        loader = params.get("loader", "tidy")
        if loader == "pems":
            from calibration.loaders.pems import load_pems_station_csv

            try:
                df = load_pems_station_csv(data_path, **pems_loader_kwargs(params))
            except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
                raise ValueError(
                    f"{data_path.name}: not a readable CSV (no header row parsed) — the "
                    f"file is empty, truncated, or not delimited text"
                ) from exc
        elif loader == "detector_csv":
            # WP-A: the tidy detector frame of the observations contract — a
            # station-total flow, so the FD table is per lane (``to_fd_frame``).
            from calibration.loaders.detector_csv import load_detector_csv, to_fd_frame

            tidy = load_detector_csv(data_path, **_subset(params, _DETECTOR_LOADER_KEYS))
            fd_kwargs = _subset(params, ("g_effective_length_m",))
            df = to_fd_frame(tidy, **fd_kwargs)
        elif loader == "tidy":
            # `occupancy` reaches the fit as numbers only when no explicit
            # density cut is given; checking it otherwise would fail a file
            # over a column the fit never reads.
            numeric = list(_FD_REQUIRED_COLUMNS)
            if params.get("uncongested_max_density") is None:
                numeric.append("occupancy")
            df = _read_calibration_csv(data_path, numeric, _FD_REQUIRED_COLUMNS)
        else:
            raise ValueError(
                f"unknown fd loader {loader!r} (expected 'tidy', 'pems' or 'detector_csv')"
            )
        if df.empty:
            raise ValueError(
                f"{data_path.name}: the {loader!r} loader produced 0 usable rows — "
                f"nothing to fit a fundamental diagram to"
            )
        artifact = fit_triangular_fd(
            df,
            created_at=now_iso(),
            source=cal["source"],
            **_subset(params, _FD_FIT_KEYS),
        )
        out = results / "calibrations" / calibration_id / "fd_calibration.json"
        artifact.save(out)
        store.set_calibration_status(calibration_id, "done", artifact_path=str(out))
    except Exception as exc:
        _log.exception("fd calibration %s failed", calibration_id)
        store.set_calibration_status(calibration_id, "failed", error=_calibration_error_text(exc))


def idm_calibration_job(
    calibration_id: str, db_path: str | None = None, results_root: str | None = None
) -> None:
    """Fit the IDM population distribution from paired trajectories (§6.2).

    Expects a CSV in the paired follower-leader shape of
    :func:`calibration.episodes.episodes_from_pairs` (``t``, ``veh_id``,
    ``lane``, ``leader_id``, ``gap_m``, ``v``, ``v_leader``). The
    ``IDMCalibration`` artifact (population stats + holdout gap RMSE) is
    saved under the results root. Unreadable, header-only and non-numeric
    uploads are diagnosed by :func:`_read_calibration_csv` first.
    """
    store, results = _resolve(db_path, results_root)
    cal = store.get_calibration(calibration_id)
    if cal is None:
        raise KeyError(f"calibration {calibration_id!r} not found in store {store.db_path}")
    if not store.claim_calibration(calibration_id):
        _log.info(
            "calibration %s is %s, not claimable; duplicate delivery ignored",
            calibration_id,
            cal["status"],
        )
        return
    try:
        from calibration.episodes import episodes_from_pairs
        from calibration.idm_fit import fit_population

        params = cal["params"]
        df = _read_calibration_csv(Path(cal["data_path"]), _IDM_NUMERIC_COLUMNS)
        episode_kwargs: dict[str, Any] = {}
        if "min_duration_s" in params:
            episode_kwargs["min_duration_s"] = params["min_duration_s"]
        episodes = episodes_from_pairs(df, dataset=cal["source"], **episode_kwargs)
        fit_kwargs = _subset(params, _IDM_FIT_KEYS)
        fit_kwargs.setdefault("seed", 0)
        artifact = fit_population(
            episodes, created_at=now_iso(), source=cal["source"], **fit_kwargs
        )
        out = results / "calibrations" / calibration_id / "idm_calibration.json"
        artifact.save(out)
        store.set_calibration_status(calibration_id, "done", artifact_path=str(out))
    except Exception as exc:
        _log.exception("idm calibration %s failed", calibration_id)
        store.set_calibration_status(calibration_id, "failed", error=_calibration_error_text(exc))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hard-link an artifact into the staging tree (copy across devices).

    Idempotent: an existing ``dst`` is removed first. Without that,
    re-staging over a previous attempt's link makes ``os.link`` raise
    ``FileExistsError`` and the ``shutil.copy2`` fallback raise
    ``shutil.SameFileError`` (from inside the handler, so it is not caught by
    it) — the whole re-run then dies before it reaches the generator.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _stage_runs(runs: list[dict[str, Any]], stage_dir: Path) -> None:
    """Assemble one run-set directory from several runs' replicate dirs.

    :func:`validation.report.generate_report` discovers runs by scanning a
    single root for ``meta.json``; API runs live under per-run roots, so each
    replicate's contract artifacts are hard-linked (cheap, same filesystem)
    into ``<stage>/<run_id>/<config_hash>/<seed>/``.
    """
    from api.results import replicate_dirs

    for run in runs:
        run_root = Path(run["run_root"])
        dirs = replicate_dirs(run_root)
        if not dirs:
            raise FileNotFoundError(f"run {run['id']} has no completed replicates")
        for rep_dir in dirs:
            rel = rep_dir.relative_to(run_root)
            dest = stage_dir / run["id"] / rel
            for name in _REPLICATE_FILES:
                src = rep_dir / name
                if src.is_file():
                    _link_or_copy(src, dest / name)


def _report_span(runs: list[dict[str, Any]]) -> tuple[float, float] | None:
    """One travel-time span [m] for a report's whole run set, or None.

    ``generate_report`` measures every group over a single span so the
    controller-minus-baseline travel-time contrast compares the same
    distance. Left to itself it derives that span from the reference group's
    trajectories, whose smallest observed ``x`` sits inside a corridor's
    upstream insertion buffer — an entry time taken there is an insertion
    time, and the reported travel time is inflated by the buffer crossing.

    When every run in the set is the same corridor geometry, the geometric
    span (:func:`api.results.analysis_span`) is the honest one and is passed
    explicitly. A heterogeneous set (mixed geometries, or ring runs) gets
    ``None`` — the generator's own shared span is then the best available
    answer, and inventing a common geometry would be worse.

    An OSM corridor's geometry is not in the config (the edge lengths come
    from the compiled network), so the span is taken from a replicate's
    ``meta.json`` when one is readable; the config alone is the fallback,
    which keeps runs recorded before that block existed on their previous
    behaviour.

    Args:
        runs: Store rows of the report's runs (each carries the effective
            ``config`` the run executed and its ``run_root``).

    Returns:
        The shared span, or ``None`` when the set does not define one.
    """
    from api.results import analysis_span

    spans = {analysis_span(_span_meta(run)) for run in runs}
    if len(spans) != 1:
        return None
    return spans.pop()


def _span_meta(run: dict[str, Any]) -> dict[str, Any]:
    """What :func:`api.results.analysis_span` reads for one run row.

    A replicate's ``meta.json`` when the run has one (it carries the geometry
    as built, which an OSM config does not), else the config alone.
    """
    from api.results import load_meta, replicate_dirs

    try:
        rep_dirs = replicate_dirs(Path(run["run_root"]))
    except (KeyError, TypeError, OSError):
        rep_dirs = []
    for rep_dir in rep_dirs:
        try:
            meta = load_meta(rep_dir)
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and isinstance(meta.get("config"), dict):
            return meta
    return {"config": run["config"]}


def _pdf_available() -> bool:
    """Whether the ``validation[pdf]`` extra (fpdf2) is importable here."""
    return importlib.util.find_spec("fpdf") is not None


def _observed_x_offset(meta: dict[str, Any]) -> float:
    """Simulation ``x`` of the corridor's start [m] for one replicate.

    A generated corridor is built with an upstream insertion buffer
    (``microsim.runner.CORRIDOR_INSERTION_BUFFER_M``, capped at the corridor
    length), so a station measured at ``x_m`` along the corridor sits at
    ``x_m + offset`` in trajectory coordinates — the same convention
    :func:`api.results.analysis_span` applies to the travel-time span. Ring
    and OSM networks carry no such synthetic buffer, so their observations are
    expected in the network's own coordinates and the offset is zero.

    Args:
        meta: The replicate's parsed ``meta.json``.

    Returns:
        The offset [m].
    """
    config = meta.get("config")
    network = config.get("network") if isinstance(config, dict) else None
    if not isinstance(network, dict) or network.get("kind") != "corridor":
        return 0.0
    length = network.get("length_m")
    if not isinstance(length, (int, float)) or isinstance(length, bool) or length <= 0:
        return 0.0
    # Imported here, not at module import: the report worker must not load
    # SUMO to read a geometry constant (the reason api.results does the same).
    from microsim.runner import CORRIDOR_INSERTION_BUFFER_M

    return float(min(CORRIDOR_INSERTION_BUFFER_M, float(length)))


def _observed_report_inputs(
    observations_path: str, runs: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Score a report's runs against an observations artifact.

    Every completed micro replicate of every run in the set is scored with
    :func:`validation.battery.score_replicate` — the same function
    ``scripts/corridor_battery.py`` uses, so an API report and a battery
    artifact cannot disagree about the same run. The per-replicate GEH values
    are pooled (each replicate contributes its own station-hours, so the
    criterion's pass fraction is measured over the ensemble rather than over
    one arbitrary seed) and the RMSPE is the mean of the replicates'.

    Args:
        observations_path: The ``flowstate.observations/1`` artifact, already
            confined to the allow-listed roots by the API boundary.
        runs: Store rows of the report's runs.

    An artifact that can form no comparison at all — fewer than two positioned
    mainline stations, so there is no station spacing and therefore no segment
    (:func:`validation.observed.no_comparison_provenance`) — is not a report
    failure: nothing is scored, the two rows stay *not evaluated*, and the
    observed block the report prints says why. Refusing the whole report would
    turn a thin detector inventory into a missing report.

    Returns:
        ``(kwargs, provenance)`` — keyword arguments for
        :func:`validation.report.generate_report` and the JSON provenance for
        the report row. Both are empty/None when no replicate could be scored,
        which leaves the two criteria rows honestly *not evaluated*.
    """
    import math

    from api.results import load_meta, replicate_dirs
    from validation.battery import score_replicate
    from validation.observed import (
        ObservedCorridor,
        ObservedScores,
        no_comparison_provenance,
        pool_scores,
    )

    observed = ObservedCorridor.from_json(observations_path)
    blocked = no_comparison_provenance(observed, path=observations_path)
    if blocked is not None:
        return {"observed": blocked}, blocked.to_dict()
    scores: list[ObservedScores] = []
    for run in runs:
        if run.get("tier") == "macro":
            continue  # the screening tier has no trajectories to compare
        for rep_dir in replicate_dirs(Path(run["run_root"])):
            if not (rep_dir / "trajectories.parquet").is_file():
                continue
            scores.append(
                score_replicate(
                    rep_dir, observed, x_offset_m=_observed_x_offset(load_meta(rep_dir))
                )
            )
    if not scores:
        return {}, None
    geh, rmspe_value, sim_speeds, obs_speeds, provenance = pool_scores(
        observed, scores, path=observations_path
    )
    kwargs: dict[str, Any] = {
        "geh_values": geh or None,
        "rmspe_value": rmspe_value if math.isfinite(rmspe_value) else None,
        "segment_speeds_obs": obs_speeds,
        "segment_speeds_sim": sim_speeds,
        "segment_window_s": observed.window_s,
        "observed": provenance,
    }
    return kwargs, provenance.to_dict()


_warned_no_pdf = False


def _warn_no_pdf_once() -> None:
    global _warned_no_pdf
    if not _warned_no_pdf:
        _warned_no_pdf = True
        _log.warning(
            "fpdf2 is not installed: reports are generated as markdown only and "
            "GET /reports/{id}/pdf answers 404 (install the validation[pdf] extra)"
        )


def report_job(report_id: str, db_path: str | None = None, results_root: str | None = None) -> None:
    """Generate a validation report bundle for a set of finished runs.

    **Acceptance criteria.** The report is scored against the profile named
    on the report row (``ReportCreateRequest.profile``, a key of
    ``validation.criteria.CRITERIA_PROFILES``), so a DOT pilot can ask for
    its own protocol — the profile's ``source`` and thresholds are printed in
    the report. Only the *thresholds* come from the request. The
    measurements are computed from the staged run artifacts, and the
    criteria whose evidence this service does not hold are left *not
    evaluated*: numbers typed into a request body would be exactly the
    free-text report values CLAUDE.md §7.4 forbids.

    **Observed comparisons.** When the request named an
    ``observations_path``, the two observed rows stop being a product gap:
    every completed micro replicate is scored against the corridor's
    detectors (:func:`_observed_report_inputs`), ``link_flows_geh`` is
    evaluated on the pooled station-hour GEH values and ``speeds_rmspe`` on
    the replicate-mean segment-speed RMSPE, and the report gains the
    observed-data provenance block. The numbers are computed from the run
    artifacts and the artifact file, never from the request. Without an
    ``observations_path`` — and for ``ring_emergence``/``ring_dampening``
    (the two benchmark runs) and ``sensitivity_grid`` (the realized
    penetration × compliance cells), which the API still cannot supply —
    the rows stay not evaluated; ``scripts/corridor_battery.py`` is the
    offline path that fills the ring rows too.

    **Travel-time span.** A run set that is one corridor geometry is measured
    over that corridor (:func:`_report_span`), not over the replicates'
    observed position extremes — whose lower bound sits inside the upstream
    insertion buffer and inflates every travel time by the buffer crossing.

    Refusal semantics: :func:`validation.report.generate_report` raises
    :class:`validation.report.ReportRefusedError` on macro-only run sets
    (CLAUDE.md §5.6); the failure is recorded with
    ``error_kind="report_refused"`` so the API can surface HTTP 422.

    The PDF rendering is requested only when fpdf2 is importable
    (:func:`_pdf_available`), decided *before* the single
    ``generate_report`` call — never by trying ``pdf=True`` and retrying
    without it, which would render every report twice on a deployment
    without the extra and would swallow a refusal (``ReportRefusedError`` is
    a ``RuntimeError``) on the first attempt.
    """
    store, results = _resolve(db_path, results_root)
    report = store.get_report(report_id)
    if report is None:
        raise KeyError(f"report {report_id!r} not found in store {store.db_path}")
    if not store.claim_report(report_id):
        _log.info(
            "report %s is %s, not claimable; duplicate delivery ignored",
            report_id,
            report["status"],
        )
        return
    report_dir = results / "reports" / report_id
    try:
        from validation.criteria import get_profile
        from validation.report import ReportRefusedError, generate_report

        runs: list[dict[str, Any]] = []
        for rid in report["run_ids"]:
            run = store.get_run(rid)
            if run is None:
                raise KeyError(f"run {rid!r} not found")
            if run["status"] != "done":
                raise ValueError(f"run {rid!r} is {run['status']}, not done")
            runs.append(run)

        stage_dir = report_dir / "runs"
        # A re-run (``rq requeue`` of a failed row, per docs/DEPLOYMENT.md §
        # "Job recovery") starts from a clean tree: the previous attempt's
        # staged hard links are left behind by every failure path, and
        # ``generate_report`` discovers runs by *scanning* this tree, so
        # leftovers from a run set that has since changed would be folded
        # into the new report. Safe here because the claim above already
        # succeeded — a duplicate delivery that lost the claim returned long
        # before this line and can never wipe a live job's tree.
        shutil.rmtree(stage_dir, ignore_errors=True)
        _stage_runs(runs, stage_dir)
        out_path = report_dir / "report.md"
        for stale in (out_path, report_dir / "report.pdf"):
            stale.unlink(missing_ok=True)
        want_pdf = _pdf_available()
        if not want_pdf:
            _warn_no_pdf_once()
        profile = get_profile(str(report["profile"]))
        span = _report_span(runs)
        observations_path = report.get("observations_path")
        observed_kwargs: dict[str, Any] = {}
        observed_meta: dict[str, Any] | None = None
        if observations_path:
            observed_kwargs, observed_meta = _observed_report_inputs(str(observations_path), runs)
        try:
            if want_pdf:
                generate_report(
                    stage_dir,
                    out_path,
                    title=report["title"],
                    created_at=now_iso(),
                    profile=profile,
                    span=span,
                    pdf=True,
                    **observed_kwargs,
                )
            else:
                generate_report(
                    stage_dir,
                    out_path,
                    title=report["title"],
                    created_at=now_iso(),
                    profile=profile,
                    span=span,
                    **observed_kwargs,
                )
        except ReportRefusedError as exc:
            store.set_report_status(
                report_id,
                "failed",
                report_dir=str(report_dir),
                error=str(exc),
                error_kind=REPORT_REFUSED_KIND,
            )
            return
        store.set_report_status(
            report_id,
            "done",
            report_dir=str(report_dir),
            report_path=str(out_path),
            observed=observed_meta,
        )
    except Exception as exc:
        _log.exception("report %s failed", report_id)
        store.set_report_status(
            report_id, "failed", report_dir=str(report_dir), error=_error_text(exc)
        )


# ---------------------------------------------------------------------------
# Store ↔ queue reconciliation
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ReconcileReport:
    """What one :func:`reconcile_store` pass changed (row ids per outcome)."""

    failed: list[str] = dataclasses.field(default_factory=list)
    """Rows marked ``failed`` because their job is gone, failed, or abandoned."""
    requeued: list[str] = dataclasses.field(default_factory=list)
    """``queued`` rows whose job had vanished from Redis; enqueued again."""
    reset: list[str] = dataclasses.field(default_factory=list)
    """``running`` rows whose job is back in the queue; reset to ``queued``."""

    @property
    def changed(self) -> int:
        return len(self.failed) + len(self.requeued) + len(self.reset)


def _job_for(kind: str, row: dict[str, Any]) -> Callable[..., Any]:
    if kind == "run":
        return run_scenario_job
    if kind == "sweep":
        return sweep_job
    if kind == "report":
        return report_job
    if kind == "corridor":  # WP-F (imported here: onboarding_jobs imports this module)
        from api.onboarding_jobs import corridor_onboarding_job

        return corridor_onboarding_job
    if kind == "calibration":
        if row["kind"] == "demand":  # WP-A (imported here: calibration_jobs imports this module)
            from api.calibration_jobs import demand_calibration_job

            return demand_calibration_job
        return fd_calibration_job if row["kind"] == "fd" else idm_calibration_job
    raise ValueError(f"unknown row kind {kind!r}")


def _age_s(created_at: str, now: datetime) -> float:
    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return float("inf")
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (now - created).total_seconds()


def _rq_failure_summary(job: Job) -> str:
    """Last line of RQ's own failure record for ``job`` (no frames)."""
    try:
        result = job.latest_result()
    except Exception:  # a malformed result must not stop reconciliation
        result = None
    text = (result.exc_string if result is not None else None) or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:400] if lines else "no failure record"


def row_id_of_job(job: Job) -> str | None:
    """The store row a queue job works on, or None for a foreign job.

    Primary: the job id itself (every enqueue in this module passes
    ``job_id=<row id>``). Fallback: the job's first positional argument —
    every job function here takes the row id first — which covers jobs
    enqueued under a random RQ id (a producer that did not pass ``job_id``,
    or jobs queued before ids were aligned).
    """
    from api.store import kind_of_id

    if kind_of_id(job.id) is not None:
        return job.id
    args = job.args or ()
    if args and isinstance(args[0], str) and kind_of_id(args[0]) is not None:
        return args[0]
    return None


def _jobs_by_row_id(queue: RedisQueue, live_ids: set[str]) -> dict[str, Job]:
    """Queued and started jobs indexed by the row they work on.

    The fallback index for rows whose job does not sit under the row id
    (see :func:`row_id_of_job`); jobs under their row id are found directly.
    """
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    conn = queue.connection
    index: dict[str, Job] = {}
    ids = list(queue.queue.get_job_ids()) + sorted(live_ids)
    for job_id in ids:
        try:
            job = Job.fetch(job_id, connection=conn)
        except NoSuchJobError:
            continue
        row_id = row_id_of_job(job)
        if row_id is not None and row_id != job.id:
            index.setdefault(row_id, job)
    return index


def reconcile_store(
    store: Store,
    queue: JobQueue,
    results_root: str | Path,
    *,
    queued_grace_s: float = QUEUED_GRACE_S,
) -> ReconcileReport:
    """Bring ``queued``/``running`` rows back in line with the RQ queue.

    The store row is the only status the API ever reads, and only the job's
    own ``except`` clause normally moves it past ``running`` — so a job that
    dies without reporting (work horse killed while the worker survives,
    worker container SIGKILLed on redeploy, Redis restarted and its queue
    lost) leaves a row that says ``running`` or ``queued`` forever. This pass
    compares every such row with its RQ job (job id == row id) and repairs:

    ==========  =====================================  ==========================
    row         RQ job                                 action
    ==========  =====================================  ==========================
    queued      missing                                re-enqueue under the same
                                                       id (after ``queued_grace_s``)
    running     missing                                fail: lost from Redis
    any         failed / stopped / canceled            fail, with RQ's record
    any         started, no live execution in the      fail: worker died
                ``StartedJobRegistry``
    any         finished                               fail: job never wrote
                                                       its outcome (should
                                                       not happen)
    running     queued / deferred / scheduled          reset to ``queued`` so
                                                       the next claim succeeds
    queued      queued / deferred / scheduled          leave (waiting)
    any         started, live                          leave (executing)
    ==========  =====================================  ==========================

    "Live" is RQ's own notion: the execution's heartbeat entry in the
    ``StartedJobRegistry`` (refreshed every ``job_monitoring_interval`` by
    the forking worker, TTL ≈ interval + 60 s), whose expired entries are
    cleaned up first. Liveness is re-read from Redis before a started job is
    declared dead, because a worker may dequeue a job *while* this pass runs
    (the pass takes ~0.1 s over a 200-cell sweep, and the deployment runs
    several of them — every worker at start and on each maintenance
    interval, the API at startup): a snapshot taken before the row loop
    would fail a healthy running cell with "worker died". A row's job is
    looked up under the row id, then — for
    jobs enqueued under a random RQ id — among the queued and started jobs
    whose first argument is the row id (:func:`row_id_of_job`). Every action
    is a guarded compare-and-set (:meth:`api.store.Store.fail_active`,
    :meth:`Store.reset_to_queued`, unique enqueue), so several processes may
    run this concurrently — the worker runs it at start and with RQ's
    periodic maintenance; the API may run it at startup.

    Under the inline queue there is nothing to compare against: the report
    is empty.
    """
    report = ReconcileReport()
    if not isinstance(queue, RedisQueue):
        return report
    from rq.job import Job, JobStatus
    from rq.registry import StartedJobRegistry

    conn = queue.connection
    registry = StartedJobRegistry(queue.queue.name, connection=conn)
    registry.cleanup()  # expired executions → RQ marks those jobs failed
    live = set(registry.get_job_ids(cleanup=False))
    by_row = _jobs_by_row_id(queue, live)
    now = datetime.now(UTC)

    for kind in KINDS:
        for row in store.list_by_status(kind, ("queued", "running")):
            row_id = str(row["id"])
            row_status = str(row["status"])
            job: Job | None
            if Job.exists(row_id, conn):
                job = Job.fetch(row_id, connection=conn)
            else:
                job = by_row.get(row_id)
            if job is None:
                if row_status == "queued":
                    if _age_s(str(row["created_at"]), now) < queued_grace_s:
                        continue  # its first enqueue may still be in flight
                    queue.enqueue(
                        _job_for(kind, row),
                        row_id,
                        job_id=row_id,
                        db_path=str(store.db_path),
                        results_root=str(results_root),
                    )
                    report.requeued.append(row_id)
                    _log.warning("%s %s had no queue job; enqueued again", kind, row_id)
                elif store.fail_active(
                    kind,
                    row_id,
                    "job lost from the queue while running (Redis or worker restart); "
                    "marked failed by reconciliation — resubmit to run it again",
                ):
                    report.failed.append(row_id)
                    _log.warning("%s %s: job gone from Redis; marked failed", kind, row_id)
                continue

            status = job.get_status()
            error: str | None = None
            if status in (JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELED):
                error = (
                    f"queue job {status.value} without reporting ({_rq_failure_summary(job)}); "
                    f"marked failed by reconciliation"
                )
            elif status == JobStatus.STARTED:
                # `live` is a snapshot taken before this loop; a worker that
                # dequeued this job *during* the pass registers its execution
                # after the snapshot and would be declared dead while SUMO is
                # running it. Confirm with a fresh read, and only on the
                # suspect path (one extra Redis call per suspect row, none in
                # the common case). Reading the registry *after*
                # `job.get_status()` is the correct order and cannot invent
                # the opposite error: RQ adds the execution before it sets
                # STARTED, and removes it only after the job function has
                # written its row, so a job that finished in between leaves a
                # settled row that `fail_active` refuses to touch.
                if job.id not in live and job.id not in registry.get_job_ids(cleanup=False):
                    error = (
                        "worker died while the job was executing (no live execution in the "
                        "started-job registry); marked failed by reconciliation"
                    )
            elif status == JobStatus.FINISHED:
                error = (
                    "queue job finished without recording an outcome on this row; "
                    "marked failed by reconciliation"
                )
            elif row_status == "running":
                # queued / deferred / scheduled: back in the queue (operator
                # requeue) while the row still says running.
                if store.reset_to_queued(kind, row_id):
                    report.reset.append(row_id)
                    _log.warning("%s %s: job requeued; row reset to queued", kind, row_id)
            if error is not None and store.fail_active(kind, row_id, error):
                report.failed.append(row_id)
                _log.warning("%s %s: %s", kind, row_id, error)
    return report
