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
carries the exception, never a silent success (CLAUDE.md §0.1). Calibration
failures go through :func:`_calibration_error_text` first, which keeps our own
diagnostics but withholds third-party messages that can quote the parsed
file's contents back to the caller.

Simulation dispatch: micro-tier runs go through
:func:`microsim.runner.run_replicates`, which parallelizes across *spawned*
subprocesses (one libsumo per process); macro-tier runs execute
:func:`macrosim.runner.run_macro` per replicate with seeds from
:func:`flowstate_core.rng.spawn_seeds` — the identical seed list the micro
path derives, recorded on the run row at creation time.
"""

from __future__ import annotations

import os
import shutil
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from api.settings import Settings, load_settings
from api.store import Store, now_iso

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


class JobQueue(Protocol):
    """Minimal queue interface the API codes against."""

    kind: str

    def enqueue(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Schedule ``func(*args, **kwargs)`` for execution."""
        ...

    def check(self) -> None:
        """Health probe; raises when the backend is unreachable."""
        ...


class InlineQueue:
    """Synchronous in-process execution (tests and small local runs)."""

    kind = "inline"

    def enqueue(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        func(*args, **kwargs)

    def check(self) -> None:
        return None


class RedisQueue:
    """RQ-backed queue on Redis (the production mode)."""

    kind = "redis"

    def __init__(self, redis_url: str, name: str = QUEUE_NAME) -> None:
        import redis
        import rq

        self.connection = redis.Redis.from_url(redis_url)
        self.queue = rq.Queue(name, connection=self.connection)

    def enqueue(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self.queue.enqueue(func, args=args, kwargs=kwargs, job_timeout=JOB_TIMEOUT)

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


def _error_text(exc: BaseException) -> str:
    """Compact, honest failure record: exception plus traceback tail."""
    tb = "".join(traceback.format_exception(exc))
    return tb[-_ERROR_MAX_CHARS:]


def _raising_module(exc: BaseException) -> str:
    """Module name of the innermost frame in ``exc``'s traceback."""
    tb = exc.__traceback__
    module = ""
    while tb is not None:
        module = str(tb.tb_frame.f_globals.get("__name__", ""))
        tb = tb.tb_next
    return module


def _calibration_error_text(exc: BaseException) -> str:
    """Failure record for calibration jobs, with input data withheld.

    ``GET /api/v1/calibrations/{id}`` hands this string to any API-key holder,
    and the job's whole purpose is parsing a data file — so an unfiltered
    exception message is a read channel into that file's contents. Messages
    raised inside FlowState's own packages (:data:`_OWN_PACKAGES`) are
    diagnostics we authored and are kept verbatim; every other message is
    replaced by its exception type and raising module. Tracebacks are dropped
    entirely: their frames add only source lines, which the operator can read
    in the repository anyway.

    Returns:
        The ``cause``-ordered exception chain, one line each, truncated to
        :data:`_ERROR_MAX_CHARS`.
    """
    lines: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        module = _raising_module(current)
        name = type(current).__name__
        if module.split(".")[0] in _OWN_PACKAGES:
            lines.append(f"{name}: {current}")
        else:
            lines.append(
                f"{name} raised in {module or '<unknown>'} "
                f"(message withheld: it may quote the input file's contents)"
            )
        current = current.__cause__ or current.__context__
    return "\n  caused by: ".join(lines)[:_ERROR_MAX_CHARS]


# ---------------------------------------------------------------------------
# Simulation runs
# ---------------------------------------------------------------------------


def run_scenario_job(
    run_id: str, db_path: str | None = None, results_root: str | None = None
) -> None:
    """Execute one run (all replicates) and keep the store's row current.

    Micro tier: :func:`microsim.runner.run_replicates` (multiprocessing spawn
    pool inside this job; progress lands in one step when the pool returns).
    Macro tier: :func:`macrosim.runner.run_macro` per replicate over the run
    row's recorded seed list, with per-replicate progress updates.
    """
    store, _ = _resolve(db_path, results_root)
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(f"run {run_id!r} not found in store {store.db_path}")
    store.set_run_status(run_id, "running")
    try:
        from flowstate_core.config import ScenarioConfig

        cfg = ScenarioConfig.model_validate(run["config"])
        run_root = Path(run["run_root"])
        run_root.mkdir(parents=True, exist_ok=True)
        if cfg.tier == "macro":
            from macrosim.runner import run_macro

            for i, seed in enumerate(run["seeds"]):
                run_macro(cfg, int(seed), run_root)
                store.set_run_progress(run_id, i + 1)
        else:
            from microsim.runner import run_replicates

            run_replicates(cfg, run_root)
            store.set_run_progress(run_id, len(run["seeds"]))
        store.set_run_status(run_id, "done")
    except Exception as exc:
        store.set_run_status(run_id, "failed", error=_error_text(exc))


def sweep_job(sweep_id: str, db_path: str | None = None, results_root: str | None = None) -> None:
    """Fan a validated grid out into child runs (one job per grid cell).

    The grid cells (effective configs, validated and hashed at POST time) are
    stored on the sweep row; this job creates the child run rows and enqueues
    one :func:`run_scenario_job` each via the configured queue — synchronous
    under the inline queue, parallel workers under Redis. Sweep status
    ``done`` means the fan-out completed; cell completion is tracked on the
    child run rows.
    """
    store, results = _resolve(db_path, results_root)
    sweep = store.get_sweep(sweep_id)
    if sweep is None:
        raise KeyError(f"sweep {sweep_id!r} not found in store {store.db_path}")
    store.set_sweep_status(sweep_id, "running")
    try:
        from api.store import new_id
        from flowstate_core.rng import spawn_seeds

        queue = get_queue()
        grid = sweep["grid"]
        for cell in grid:
            cfg = cell["config"]
            run_id = new_id("run")
            store.create_run(
                scenario_id=sweep["scenario_id"],
                config=cfg,
                config_hash=cell["config_hash"],
                tier=cfg["tier"],
                seeds=spawn_seeds(int(cfg["seed"]), int(cfg["replicates"])),
                run_root=results / "runs" / run_id,
                sweep_id=sweep_id,
                run_id=run_id,
            )
            cell["run_id"] = run_id
            store.set_sweep_grid(sweep_id, grid)
            queue.enqueue(
                run_scenario_job, run_id, db_path=str(store.db_path), results_root=str(results)
            )
        store.set_sweep_status(sweep_id, "done")
    except Exception as exc:
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
    "notes",
)

_PEMS_LOADER_KEYS = ("g_effective_length_m", "interval_s", "speed_unit", "occupancy_unit")

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


def fd_calibration_job(
    calibration_id: str, db_path: str | None = None, results_root: str | None = None
) -> None:
    """Fit a triangular FD from the calibration row's data file (§6.1).

    ``params.loader`` selects the input shape: ``"tidy"`` (default) expects a
    CSV with ``density_veh_m``/``flow_veh_s`` (optional ``occupancy``)
    columns; ``"pems"`` runs the PeMS station-CSV loader first. The
    ``FDCalibration`` artifact is saved under the results root and its path
    recorded on the row.
    """
    store, results = _resolve(db_path, results_root)
    cal = store.get_calibration(calibration_id)
    if cal is None:
        raise KeyError(f"calibration {calibration_id!r} not found in store {store.db_path}")
    store.set_calibration_status(calibration_id, "running")
    try:
        import pandas as pd

        from calibration.fd_fit import fit_triangular_fd

        params = cal["params"]
        data_path = Path(cal["data_path"])
        loader = params.get("loader", "tidy")
        if loader == "pems":
            from calibration.loaders.pems import load_pems_station_csv

            df = load_pems_station_csv(data_path, **_subset(params, _PEMS_LOADER_KEYS))
        elif loader == "tidy":
            df = pd.read_csv(data_path)
        else:
            raise ValueError(f"unknown fd loader {loader!r} (expected 'tidy' or 'pems')")
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
        store.set_calibration_status(calibration_id, "failed", error=_calibration_error_text(exc))


def idm_calibration_job(
    calibration_id: str, db_path: str | None = None, results_root: str | None = None
) -> None:
    """Fit the IDM population distribution from paired trajectories (§6.2).

    Expects a CSV in the paired follower-leader shape of
    :func:`calibration.episodes.episodes_from_pairs` (``t``, ``veh_id``,
    ``lane``, ``leader_id``, ``gap_m``, ``v``, ``v_leader``). The
    ``IDMCalibration`` artifact (population stats + holdout gap RMSE) is
    saved under the results root.
    """
    store, results = _resolve(db_path, results_root)
    cal = store.get_calibration(calibration_id)
    if cal is None:
        raise KeyError(f"calibration {calibration_id!r} not found in store {store.db_path}")
    store.set_calibration_status(calibration_id, "running")
    try:
        import pandas as pd

        from calibration.episodes import episodes_from_pairs
        from calibration.idm_fit import fit_population

        params = cal["params"]
        df = pd.read_csv(cal["data_path"])
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
        store.set_calibration_status(calibration_id, "failed", error=_calibration_error_text(exc))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hard-link an artifact into the staging tree (copy across devices)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
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


def report_job(report_id: str, db_path: str | None = None, results_root: str | None = None) -> None:
    """Generate a validation report bundle for a set of finished runs.

    Refusal semantics: :func:`validation.report.generate_report` raises
    :class:`validation.report.ReportRefusedError` on macro-only run sets
    (CLAUDE.md §5.6); the failure is recorded with
    ``error_kind="report_refused"`` so the API can surface HTTP 422.
    """
    store, results = _resolve(db_path, results_root)
    report = store.get_report(report_id)
    if report is None:
        raise KeyError(f"report {report_id!r} not found in store {store.db_path}")
    store.set_report_status(report_id, "running")
    report_dir = results / "reports" / report_id
    try:
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
        _stage_runs(runs, stage_dir)
        out_path = report_dir / "report.md"
        try:
            generate_report(stage_dir, out_path, title=report["title"], created_at=now_iso())
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
            report_id, "done", report_dir=str(report_dir), report_path=str(out_path)
        )
    except Exception as exc:
        store.set_report_status(
            report_id, "failed", report_dir=str(report_dir), error=_error_text(exc)
        )
