"""FlowState v2 FastAPI service (CLAUDE.md §8).

API routes live under ``/api/v1/...`` so the optional single-origin frontend
mount at ``/`` never collides with them. OpenAPI docs at ``/docs``; health at
``/healthz``. Auth is a shared API key in the ``X-API-Key`` header checked on
every ``/api/...`` route — any key in ``Settings.api_keys``
(``FLOWSTATE_API_KEY`` plus the ``FLOWSTATE_API_KEYS`` rotation list), so a
key can be replaced without a lock-out window; ``/healthz`` and ``/docs`` are
exempt and real auth is a Phase 4 concern.

Browser clients on another origin (a dashboard served from a different host
or port than the API) are allowed by ``FLOWSTATE_CORS_ORIGINS``; the default
is the Vite dev server on loopback. Serving the dashboard from the API's own
``/`` mount is same-origin and needs no entry.

Every response carries ``X-Request-Id`` and every request logs one line on
the ``api.access`` logger (method, path, status, duration, id), so a client
report ties to a server log line; request bodies are counted as they stream
(:class:`BodyCapMiddleware`) and refused with HTTP 413 above the cap for the
path, whether or not the client declared a ``Content-Length``.

Job model: no endpoint executes a simulation synchronously when
``FLOWSTATE_QUEUE=redis`` — all long work (runs, sweeps, calibrations,
reports) is enqueued for ``python -m api.worker`` processes. The inline queue
(tests, small local runs) executes the same job functions synchronously.

Every run/metrics/heatmap response carries ``config_hash`` (PNG heatmaps via
the ``X-Config-Hash`` header) so results always trace to an exact
configuration (CLAUDE.md §0.5).
"""

from __future__ import annotations

import io
import json
import logging
import re
import secrets
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import api as api_pkg
from api import results as res
from api.jobs import (
    REPORT_REFUSED_KIND,
    fd_calibration_job,
    get_queue,
    idm_calibration_job,
    report_job,
    run_scenario_job,
    sweep_job,
)
from api.schemas import (
    MAX_REPORT_LIST,
    CalibrationOut,
    CalibrationParams,
    CIOut,
    HealthOut,
    HeatmapOut,
    MetricsOut,
    PresetOut,
    ProgressOut,
    ReplicateMetricsOut,
    ReportCreateRequest,
    ReportOut,
    RunCreateRequest,
    RunOut,
    ScenarioOut,
    SweepCellOut,
    SweepCreateRequest,
    SweepOut,
    deep_merge,
)
from api.settings import REPO_ROOT, Settings, check_api_key_not_default, load_settings
from api.store import Store, new_id
from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from validation.metrics import MIN_REPLICATES

router = APIRouter(prefix="/api/v1")


def _settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def _store(request: Request) -> Store:
    return request.app.state.store  # type: ignore[no-any-return]


def _validation_422(exc: ValidationError) -> HTTPException:
    return HTTPException(
        status_code=422, detail=exc.errors(include_url=False, include_context=False)
    )


def _validate_config(
    raw: dict[str, Any], settings: Settings
) -> tuple[dict[str, Any], str, ScenarioConfig]:
    """Validate a raw config dict → (normalized json, config_hash, model).

    Schema validation first (422 with the pydantic error list), then
    :func:`_confine_config_paths`, so every stored config — scenario, run
    override, sweep cell — has passed both.
    """
    try:
        cfg = ScenarioConfig.model_validate(raw)
    except ValidationError as exc:
        raise _validation_422(exc) from exc
    _confine_config_paths(cfg, settings)
    return cfg.model_dump(mode="json"), config_hash(cfg), cfg


def _config_file_fields(cfg: ScenarioConfig) -> list[tuple[tuple[str, ...], str]]:
    """The config fields that name files on the worker's filesystem, when set."""
    fields: list[tuple[tuple[str, ...], str]] = []
    if cfg.network.kind == "osm" and cfg.network.osm_file is not None:
        fields.append((("network", "osm_file"), cfg.network.osm_file))
    if cfg.fleet.idm_calibration is not None:
        fields.append((("fleet", "idm_calibration"), cfg.fleet.idm_calibration))
    heavy = cfg.fleet.heavy
    if heavy is not None and heavy.idm_calibration is not None:
        fields.append((("fleet", "heavy", "idm_calibration"), heavy.idm_calibration))
    return fields


def _resolve_config_path(value: str) -> Path:
    """Where the worker will look for a config file field.

    A relative path is taken against the repository root — the fallback
    ``microsim.vehicles.resolve_calibration_path`` applies to the shipped
    presets' ``artifacts/...`` references — never against the API process's
    working directory. Symlinks and ``..`` are then collapsed so containment
    is checked on the file actually read.
    """
    path = Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _confine_config_paths(cfg: ScenarioConfig, settings: Settings) -> None:
    """Refuse config file fields that escape :attr:`Settings.config_path_roots`.

    ``network.osm_file``, ``fleet.idm_calibration`` and
    ``fleet.heavy.idm_calibration`` are read by the worker (netconvert, the
    ``IDMCalibration`` loader), and a parse failure there can quote the
    file's bytes back through the run's error text — so, like a calibration
    ``data_path``, they may only point inside the allow-listed roots.
    Existence is not checked here: a missing file is the worker's honest
    ``FileNotFoundError``, and the API host need not mount every dataset.

    Raises:
        HTTPException: 422 in the pydantic error-list shape, naming the
            field, the requested value and the allowed roots — never
            anything read from the file.
    """
    roots = settings.config_path_roots
    errors: list[dict[str, Any]] = []
    for loc, value in _config_file_fields(cfg):
        resolved = _resolve_config_path(value)
        if not any(resolved.is_relative_to(root) for root in roots):
            errors.append(
                {
                    "type": "path_outside_roots",
                    "loc": list(loc),
                    "msg": (
                        f"{'.'.join(loc)} {value!r} is outside the allowed data roots "
                        f"{[str(r) for r in roots]}; reference a file under the repo's "
                        f"artifacts/ or data/ directories, FLOWSTATE_DATA_DIR, or the "
                        f"results root"
                    ),
                    "input": value,
                }
            )
    if errors:
        raise HTTPException(status_code=422, detail=errors)


def _seeded(config: dict[str, Any]) -> bool:
    """``ScenarioConfig.seeded`` of a stored config (perturbation *or* closures).

    Single-sourced from the model so the API never disagrees with the
    ``seeded`` flag the runners write to ``meta.json`` and the report reads
    (CLAUDE.md §0.2). Stored configs are full ``model_dump`` output, so
    re-validation always succeeds.
    """
    return ScenarioConfig.model_validate(config).seeded


async def _read_body_capped(request: Request, cap: int) -> bytes:
    """The raw request body, refused with HTTP 413 once it exceeds ``cap`` bytes.

    Streams instead of ``await request.body()`` so a chunked body — which
    carries no ``Content-Length`` for the middleware to check — is bounded
    too.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise HTTPException(
                status_code=413,
                detail=f"request body exceeds the limit of {cap} bytes (FLOWSTATE_MAX_BODY_MB)",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _apply_overrides(
    base: dict[str, Any],
    overrides: dict[str, Any],
    replicates: int | None,
    tier: str | None,
) -> dict[str, Any]:
    merged = deep_merge(base, overrides)
    if replicates is not None:
        merged["replicates"] = replicates
    if tier is not None:
        merged["tier"] = tier
    return merged


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def _scenario_out(row: dict[str, Any]) -> ScenarioOut:
    return ScenarioOut(
        scenario_id=row["id"],
        name=row["name"],
        config_hash=row["config_hash"],
        created_at=row["created_at"],
        config=row["config"],
    )


@router.post("/scenarios", status_code=201, response_model=ScenarioOut)
async def create_scenario(request: Request) -> ScenarioOut:
    """Validate + store a scenario config (JSON or YAML request body).

    File fields (``network.osm_file``, ``fleet.idm_calibration``,
    ``fleet.heavy.idm_calibration``) must resolve inside the allow-listed
    roots — the repo's ``artifacts/`` and ``data/``, ``FLOWSTATE_DATA_DIR``
    and the results root — or the config is refused with HTTP 422. Bodies
    over ``FLOWSTATE_MAX_BODY_MB`` (default 8 MB) are refused with HTTP 413.
    """
    settings = _settings(request)
    body = await _read_body_capped(request, settings.max_body_bytes)
    try:
        raw = yaml.safe_load(body.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=f"unparseable config body: {exc}") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="config body must be a mapping")
    config, chash, cfg = _validate_config(raw, settings)
    store = _store(request)
    sid = store.create_scenario(cfg.name, config, chash)
    row = store.get_scenario(sid)
    assert row is not None
    return _scenario_out(row)


@router.get("/scenarios", response_model=list[ScenarioOut])
def list_scenarios(request: Request) -> list[ScenarioOut]:
    return [_scenario_out(row) for row in _store(request).list_scenarios()]


@router.get("/scenarios/preset", response_model=list[PresetOut])
def list_presets(request: Request) -> list[PresetOut]:
    """The repo's versioned ``scenarios/*.yaml`` as selectable presets."""
    presets: list[PresetOut] = []
    for path in sorted(_settings(request).scenarios_dir.glob("*.yaml")):
        try:
            cfg = ScenarioConfig.from_yaml(path)
        except (ValueError, ValidationError, yaml.YAMLError):
            continue  # a broken preset must not break the listing
        presets.append(
            PresetOut(
                name=cfg.name,
                filename=path.name,
                config_hash=config_hash(cfg),
                config=cfg.model_dump(mode="json"),
            )
        )
    return presets


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut)
def get_scenario(request: Request, scenario_id: str) -> ScenarioOut:
    row = _store(request).get_scenario(scenario_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"scenario {scenario_id!r} not found")
    return _scenario_out(row)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def _run_out(row: dict[str, Any]) -> RunOut:
    return RunOut(
        run_id=row["id"],
        scenario_id=row["scenario_id"],
        sweep_id=row["sweep_id"],
        status=row["status"],
        tier=row["tier"],
        config_hash=row["config_hash"],
        seeded=_seeded(row["config"]),
        progress=ProgressOut(
            completed_replicates=row["completed_replicates"],
            total_replicates=row["total_replicates"],
        ),
        seeds=row["seeds"],
        error=row["error"],
        error_kind=row["error_kind"],
        created_at=row["created_at"],
    )


def _get_run_or_404(request: Request, run_id: str) -> dict[str, Any]:
    row = _store(request).get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return row


def _require_done(row: dict[str, Any]) -> None:
    if row["status"] != "done":
        detail = f"run {row['id']!r} is {row['status']}, not done"
        if row["error"]:
            detail += f": {row['error']}"
        raise HTTPException(status_code=409, detail=detail)


@router.post("/runs", status_code=202, response_model=RunOut)
def create_run(request: Request, body: RunCreateRequest) -> RunOut:
    """Enqueue a run: stored config + deep-merged overrides, re-validated.

    Caps: ``replicates`` is limited to 200 per request
    (``api.schemas.MAX_REPLICATES``), and the effective config's own
    ``replicates`` to 500 (``flowstate_core.config.MAX_REPLICATES``) — a run
    request is a request to execute that many simulations. The merged
    config's file fields are confined like a posted scenario's (HTTP 422
    outside the allowed roots), so overrides cannot name arbitrary files.
    """
    store = _store(request)
    settings = _settings(request)
    scenario = store.get_scenario(body.scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"scenario {body.scenario_id!r} not found")
    merged = _apply_overrides(scenario["config"], body.overrides, body.replicates, body.tier)
    config, chash, cfg = _validate_config(merged, settings)
    run_id = new_id("run")
    store.create_run(
        scenario_id=body.scenario_id,
        config=config,
        config_hash=chash,
        tier=cfg.tier,
        seeds=spawn_seeds(cfg.seed, cfg.replicates),
        run_root=settings.runs_dir / run_id,
        run_id=run_id,
    )
    get_queue(settings).enqueue(
        run_scenario_job,
        run_id,
        job_id=run_id,
        db_path=str(settings.db_path),
        results_root=str(settings.results_dir),
    )
    row = store.get_run(run_id)
    assert row is not None
    return _run_out(row)


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    request: Request, scenario_id: str | None = None, sweep_id: str | None = None
) -> list[RunOut]:
    return [_run_out(r) for r in _store(request).list_runs(scenario_id, sweep_id)]


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(request: Request, run_id: str) -> RunOut:
    return _run_out(_get_run_or_404(request, run_id))


@router.get("/runs/{run_id}/metrics", response_model=MetricsOut)
def get_run_metrics(request: Request, run_id: str) -> MetricsOut:
    """Per-replicate metrics + aggregate t-distribution CIs (contract §7).

    ``underpowered`` is reported honestly: any aggregate over fewer than 20
    replicates is flagged and must not be quoted as a headline result
    (CLAUDE.md §0.6).
    """
    row = _get_run_or_404(request, run_id)
    _require_done(row)
    try:
        # The worker fills the per-replicate cache as the last step of a run
        # (results.precompute_run_metrics); runs from before that step are
        # computed once here, which also writes their cache.
        cached = res.cached_run_metrics(row["run_root"])
        per_replicate, agg = cached if cached is not None else res.run_metrics(row["run_root"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MetricsOut(
        run_id=run_id,
        config_hash=row["config_hash"],
        tier=row["tier"],
        seeded=_seeded(row["config"]),
        n_replicates=len(per_replicate),
        underpowered=len(per_replicate) < MIN_REPLICATES,
        replicates=[
            ReplicateMetricsOut(seed=seed, metrics=res.metrics_to_json(m))
            for seed, m in per_replicate
        ],
        aggregate={name: CIOut(**res.ci_to_json(ci)) for name, ci in agg.items()},
    )


@router.get("/runs/{run_id}/heatmap", response_model=None)
def get_run_heatmap(
    request: Request,
    run_id: str,
    field: Literal["speed", "density"] = "speed",
    format: Literal["json", "png"] = "json",
    seed: int | None = None,
) -> HeatmapOut | Response:
    """Binned space-time array from ``edges.parquet`` (JSON or PNG).

    ``seed`` selects the replicate (default: the run's first seed). PNG
    responses carry the config hash in the ``X-Config-Hash`` header.
    """
    row = _get_run_or_404(request, run_id)
    _require_done(row)
    if seed is None:
        seed = int(row["seeds"][0])
    rep_dir = None
    for d in res.replicate_dirs(row["run_root"]):
        if d.name == str(seed):
            rep_dir = d
            break
    if rep_dir is None:
        raise HTTPException(
            status_code=404, detail=f"run {run_id!r} has no replicate for seed {seed}"
        )
    t_centers, x_centers, values = res.heatmap_arrays(rep_dir, field)
    if format == "png":
        title = f"{field} field — run {run_id}, seed {seed}"
        if row["tier"] == "macro":
            title += " (screening tier)"
        png = res.heatmap_png(t_centers, x_centers, values, field=field, title=title)
        return Response(
            content=png,
            media_type="image/png",
            headers={"X-Config-Hash": row["config_hash"]},
        )
    return HeatmapOut(
        run_id=run_id,
        config_hash=row["config_hash"],
        seed=seed,
        field=field,
        tier=row["tier"],
        t_bins=[float(t) for t in t_centers],
        x_bins=[float(x) for x in x_centers],
        values=res.matrix_to_json(values),
    )


# ---------------------------------------------------------------------------
# Sweeps
# ---------------------------------------------------------------------------


def _sweep_out(request: Request, sweep: dict[str, Any]) -> SweepOut:
    store = _store(request)
    cells: list[SweepCellOut] = []
    runs_done = 0
    runs_failed = 0
    for cell in sweep["grid"]:
        run_id = cell.get("run_id")
        run = store.get_run(run_id) if run_id else None
        aggregate = None
        progress = None
        status = None
        if run is not None:
            status = run["status"]
            progress = ProgressOut(
                completed_replicates=run["completed_replicates"],
                total_replicates=run["total_replicates"],
            )
            if run["status"] == "done":
                runs_done += 1
                # Polled endpoint: caches only, never trajectory reads in the
                # request path; a cell whose cache is not written yet reports
                # no aggregate until the next poll.
                cached = res.cached_run_metrics(run["run_root"])
                aggregate = (
                    {name: CIOut(**res.ci_to_json(ci)) for name, ci in cached[1].items()}
                    if cached is not None
                    else None
                )
            elif run["status"] == "failed":
                runs_failed += 1
        cells.append(
            SweepCellOut(
                penetration=cell["penetration"],
                compliance=cell["compliance"],
                controller=cell["controller"],
                config_hash=cell["config_hash"],
                run_id=run_id,
                status=status,
                progress=progress,
                aggregate=aggregate,
            )
        )
    return SweepOut(
        sweep_id=sweep["id"],
        scenario_id=sweep["scenario_id"],
        status=sweep["status"],
        error=sweep["error"],
        created_at=sweep["created_at"],
        runs_total=len(sweep["grid"]),
        runs_done=runs_done,
        runs_failed=runs_failed,
        cells=cells,
    )


@router.post("/sweeps", status_code=202, response_model=SweepOut)
def create_sweep(request: Request, body: SweepCreateRequest) -> SweepOut:
    """Fan a penetration × compliance × controller grid into child runs.

    Every cell's effective config is validated and hashed here (422 on any
    invalid cell); the fan-out itself runs as a job.

    ``include_baseline`` appends one no-AV reference cell (penetration 0,
    compliance 1) per distinct controller after the grid, unless the grid
    already contains penetration 0; a "Δ vs baseline" comparison then has an
    uncontrolled run to compare against rather than the smallest controlled
    cell.

    Caps (HTTP 422 when exceeded, all checked before any cell is built):
    at most 50 values per axis (``api.schemas.MAX_SWEEP_AXIS_VALUES``), 200
    total cells including baseline cells (``MAX_SWEEP_CELLS``), and 200
    replicates per cell (``MAX_REPLICATES``). The grid is a cartesian
    product, so the cell ceiling is checked from the three list lengths
    rather than by materializing them.
    """
    store = _store(request)
    settings = _settings(request)
    scenario = store.get_scenario(body.scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"scenario {body.scenario_id!r} not found")
    base = _apply_overrides(scenario["config"], body.overrides, body.replicates, body.tier)
    grid: list[dict[str, Any]] = []
    for pen, comp, ctrl in body.grid_cells():
        cell_patch = {"av": {"penetration": pen, "compliance": comp, "controller": ctrl}}
        try:
            config, chash, _ = _validate_config(deep_merge(base, cell_patch), settings)
        except HTTPException as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "cell": {"penetration": pen, "compliance": comp, "controller": ctrl},
                    "errors": exc.detail,
                },
            ) from exc
        grid.append(
            {
                "penetration": pen,
                "compliance": comp,
                "controller": ctrl,
                "config": config,
                "config_hash": chash,
                "run_id": None,
            }
        )
    sweep_id = store.create_sweep(body.scenario_id, grid)
    get_queue(settings).enqueue(
        sweep_job,
        sweep_id,
        job_id=sweep_id,
        db_path=str(settings.db_path),
        results_root=str(settings.results_dir),
    )
    sweep = store.get_sweep(sweep_id)
    assert sweep is not None
    return _sweep_out(request, sweep)


@router.get("/sweeps/{sweep_id}", response_model=SweepOut)
def get_sweep(request: Request, sweep_id: str) -> SweepOut:
    sweep = _store(request).get_sweep(sweep_id)
    if sweep is None:
        raise HTTPException(status_code=404, detail=f"sweep {sweep_id!r} not found")
    return _sweep_out(request, sweep)


# ---------------------------------------------------------------------------
# Calibrations
# ---------------------------------------------------------------------------


def _resolve_data_path(data_path: str, settings: Settings) -> Path:
    """Resolve a server-side ``data_path`` inside the allow-listed roots.

    ``data_path`` names a file on the *worker's* filesystem, so an unchecked
    value is an arbitrary-file-read primitive for anyone holding the API key.
    The path is resolved first (symlinks and ``..`` collapsed) and then
    required to sit under one of :attr:`Settings.data_roots`.

    Raises:
        HTTPException: 422 when the path escapes every allowed root or names
            no file. The detail repeats the requested path and the allowed
            roots only — never anything read from the file.
    """
    resolved = Path(data_path).resolve()
    roots = settings.data_roots
    if not any(resolved.is_relative_to(root) for root in roots):
        raise HTTPException(
            status_code=422,
            detail=(
                f"data_path {data_path!r} is outside the allowed data roots "
                f"{[str(r) for r in roots]}; upload the file instead, or mount it under "
                f"FLOWSTATE_DATA_DIR"
            ),
        )
    if not resolved.is_file():
        raise HTTPException(status_code=422, detail=f"data_path {data_path!r} not found")
    return resolved


#: Read size for streaming an upload to disk.
_UPLOAD_CHUNK_BYTES = 1 << 20

#: Name given to an upload whose client filename carries no usable name.
_DEFAULT_UPLOAD_NAME = "upload.csv"


def _upload_name(filename: str | None) -> str:
    """A plain file name for an upload: the client's basename, or a default.

    ``Path(...).name`` strips any directory part, so ``../../x.csv`` lands
    as ``x.csv`` inside the upload directory. Names that are empty, ``.``
    or ``..`` (which would address the directory itself) and names carrying
    a NUL byte fall back to :data:`_DEFAULT_UPLOAD_NAME`.
    """
    name = Path(filename or "").name
    if name in ("", ".", "..") or "\x00" in name:
        return _DEFAULT_UPLOAD_NAME
    return name


def _parse_calibration_params(params: str | None) -> dict[str, Any]:
    """Validate the ``params`` form field → the options to forward to the fit.

    Raises:
        HTTPException: 422 when the field is not a JSON object or fails
            :class:`CalibrationParams` (unknown key, out-of-range value).
    """
    try:
        raw = json.loads(params) if params else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"params is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="params must be a JSON object")
    try:
        parsed = CalibrationParams.model_validate(raw)
    except ValidationError as exc:
        errors = [
            {**err, "loc": ("params", *err["loc"])}
            for err in exc.errors(include_url=False, include_context=False)
        ]
        raise HTTPException(status_code=422, detail=errors) from exc
    return parsed.forwarded()


async def _save_upload(file: UploadFile, dest: Path, cap: int) -> None:
    """Stream ``file`` to ``dest`` in chunks, refusing more than ``cap`` bytes.

    The multipart parser reports the part's size once it has spooled it, so
    an oversized upload is refused before a byte is copied; the running
    count is the same check for a parser that leaves ``size`` unset. Never
    reads the whole upload into one ``bytes`` object.

    Raises:
        HTTPException: 413 above ``cap`` (the partial file is removed).
    """
    too_large = HTTPException(
        status_code=413,
        detail=f"upload exceeds the limit of {cap} bytes (FLOWSTATE_MAX_UPLOAD_MB)",
    )
    if file.size is not None and file.size > cap:
        raise too_large
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with dest.open("wb") as fh:
            while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > cap:
                    raise too_large
                fh.write(chunk)
    except HTTPException:
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise


def _calibration_out(row: dict[str, Any]) -> CalibrationOut:
    artifact = None
    if row["status"] == "done" and row["artifact_path"]:
        path = Path(row["artifact_path"])
        if path.is_file():
            artifact = json.loads(path.read_text())
    return CalibrationOut(
        calibration_id=row["id"],
        kind=row["kind"],
        status=row["status"],
        data_path=row["data_path"],
        source=row["source"],
        artifact_path=row["artifact_path"],
        error=row["error"],
        created_at=row["created_at"],
        artifact=artifact,
    )


@router.post("/calibrations/{kind}", status_code=202, response_model=CalibrationOut)
async def create_calibration(
    request: Request,
    kind: Literal["fd", "idm"],
    file: Annotated[UploadFile | None, File()] = None,
    data_path: Annotated[str | None, Form()] = None,
    params: Annotated[str | None, Form()] = None,
    source: Annotated[str | None, Form()] = None,
) -> CalibrationOut:
    """Run an FD or IDM calibration on an uploaded file or a server path.

    Multipart/form fields: exactly one of ``file`` (upload) or ``data_path``
    (path visible to the workers); optional ``params`` (JSON object of fit
    options, validated against ``api.schemas.CalibrationParams`` — bounded
    values, unknown keys refused with HTTP 422) and ``source`` (provenance
    string stored on the artifact).

    ``data_path`` is confined to the results root (which holds uploads) and
    the optional ``FLOWSTATE_DATA_DIR``; anything resolving outside those
    roots is refused with HTTP 422. Uploads larger than
    ``FLOWSTATE_MAX_UPLOAD_MB`` (default 200 MB) are refused with HTTP 413.
    """
    store = _store(request)
    settings = _settings(request)
    if (file is None) == (data_path is None):
        raise HTTPException(status_code=422, detail="provide exactly one of 'file' or 'data_path'")
    params_dict = _parse_calibration_params(params)

    if file is not None:
        dest = settings.uploads_dir / new_id("upl") / _upload_name(file.filename)
        await _save_upload(file, dest, settings.max_upload_bytes)
        resolved = dest
    else:
        assert data_path is not None
        resolved = _resolve_data_path(data_path, settings)

    cal_id = store.create_calibration(
        kind, resolved, params_dict, source or f"{kind} upload {resolved.name}"
    )
    job = fd_calibration_job if kind == "fd" else idm_calibration_job
    get_queue(settings).enqueue(
        job,
        cal_id,
        job_id=cal_id,
        db_path=str(settings.db_path),
        results_root=str(settings.results_dir),
    )
    row = store.get_calibration(cal_id)
    assert row is not None
    return _calibration_out(row)


@router.get("/calibrations/{calibration_id}", response_model=CalibrationOut)
def get_calibration(request: Request, calibration_id: str) -> CalibrationOut:
    row = _store(request).get_calibration(calibration_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"calibration {calibration_id!r} not found")
    return _calibration_out(row)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _relative_report_path(raw: str | None, settings: Settings) -> str | None:
    """A stored report path as a results-root-relative one (``reports/<id>/report.md``).

    The store keeps the absolute path the worker wrote — that is what the
    download routes read — but publishing it would hand every API-key holder
    the server's directory layout, which is exactly what the sanitised error
    text and 500 bodies are careful not to do. A path that does not sit under
    the results root (a hand-made row, a relocated results directory) falls
    back to its last two components, so nothing absolute escapes either way.
    """
    if not raw:
        return None
    path = Path(raw)
    try:
        return path.resolve().relative_to(settings.results_dir.resolve()).as_posix()
    except ValueError:
        return Path(path.parent.name, path.name).as_posix()


def _report_out(row: dict[str, Any], settings: Settings) -> ReportOut:
    return ReportOut(
        report_id=row["id"],
        status=row["status"],
        run_ids=row["run_ids"],
        title=row["title"],
        report_path=_relative_report_path(row["report_path"], settings),
        error=row["error"],
        error_kind=row["error_kind"],
        created_at=row["created_at"],
    )


def _refuse_macro_runs_in_report(rows: list[dict[str, Any]]) -> None:
    """Refuse a run set that mixes screening (macro) runs with micro runs.

    ``validation.report.generate_report`` refuses an all-macro set outright
    (CLAUDE.md §5.6) but *silently* drops macro runs from a mixed set while
    still listing them under Provenance — so a validation report would appear
    to rest partly on screening-tier evidence that contributed no metric. The
    report package offers no "excluded because screening tier" annotation, so
    the honest answer here is to refuse the set and name the macro runs
    rather than publish a provenance table the reader cannot interpret.

    All-macro sets are left to the generator's own refusal (same 422, via
    ``error_kind=REPORT_REFUSED_KIND``), so there is exactly one message for
    that case.

    Raises:
        HTTPException: 422 naming the macro run ids, when the set holds both
            tiers.
    """
    macro_ids = [r["id"] for r in rows if r["tier"] == "macro"]
    if not macro_ids or len(macro_ids) == len(rows):
        return
    raise HTTPException(
        status_code=422,
        detail=(
            f"run set mixes tiers: {macro_ids} are macroscopic screening runs and would "
            f"appear in the report's provenance while contributing no metrics; the "
            f"screening tier cannot support validation claims (CLAUDE.md §5.6). Request "
            f"the report from the micro-tier runs alone."
        ),
    )


@router.post("/reports", status_code=202, response_model=ReportOut)
def create_report(request: Request, body: ReportCreateRequest) -> ReportOut:
    """Generate a validation report for a set of finished runs.

    Run sets carrying screening (macro) runs are refused with HTTP 422 — the
    screening tier cannot support validation claims (CLAUDE.md §5.6). A
    *mixed* micro+macro set is refused here, before the row is created,
    naming the macro runs (:func:`_refuse_macro_runs_in_report`); an
    *all-macro* set is refused by ``validation.report.generate_report``
    itself, which under the Redis queue surfaces asynchronously as
    ``status=failed`` with ``error_kind="report_refused"``.
    """
    store = _store(request)
    settings = _settings(request)
    rows: list[dict[str, Any]] = []
    for rid in body.run_ids:
        run = store.get_run(rid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {rid!r} not found")
        rows.append(run)
    _refuse_macro_runs_in_report(rows)
    report_id = store.create_report(body.run_ids, body.title)
    get_queue(settings).enqueue(
        report_job,
        report_id,
        job_id=report_id,
        db_path=str(settings.db_path),
        results_root=str(settings.results_dir),
    )
    row = store.get_report(report_id)
    assert row is not None
    if row["status"] == "failed" and row["error_kind"] == REPORT_REFUSED_KIND:
        raise HTTPException(status_code=422, detail=row["error"])
    return _report_out(row, settings)


def _get_report_done(request: Request, report_id: str) -> dict[str, Any]:
    row = _store(request).get_report(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"report {report_id!r} not found")
    if row["status"] != "done":
        detail = f"report {report_id!r} is {row['status']}, not done"
        if row["error"]:
            detail += f": {row['error']}"
        code = 422 if row["error_kind"] == REPORT_REFUSED_KIND else 409
        raise HTTPException(status_code=code, detail=detail)
    return row


@router.get("/reports", response_model=list[ReportOut])
def list_reports(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_REPORT_LIST)] = MAX_REPORT_LIST,
) -> list[ReportOut]:
    """Reports newest first, at most ``limit`` (default and maximum 200).

    The dashboard's report table would otherwise live only in one browser's
    localStorage: clearing site data, or opening the dashboard as a
    colleague, loses every report the server still holds. Rows are metadata
    only — the bundles are fetched through ``/markdown``, ``/pdf`` and
    ``/archive``.
    """
    settings = _settings(request)
    return [_report_out(row, settings) for row in _store(request).list_reports(limit=limit)]


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(request: Request, report_id: str) -> ReportOut:
    row = _store(request).get_report(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"report {report_id!r} not found")
    return _report_out(row, _settings(request))


@router.get("/reports/{report_id}/markdown")
def get_report_markdown(request: Request, report_id: str) -> PlainTextResponse:
    """The rendered markdown report."""
    row = _get_report_done(request, report_id)
    return PlainTextResponse(Path(row["report_path"]).read_text(), media_type="text/markdown")


@router.get("/reports/{report_id}/pdf")
def get_report_pdf(request: Request, report_id: str) -> Response:
    """The PDF rendering of the report, when one was generated beside it.

    The PDF is optional (``generate_report(..., pdf=True)``, the
    ``validation[pdf]`` extra); a finished report without one answers 404.
    """
    row = _get_report_done(request, report_id)
    pdf_path = Path(row["report_path"]).with_name("report.pdf")
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail=f"report {report_id!r} has no PDF rendering")
    return Response(
        content=pdf_path.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.pdf"'},
    )


@router.get("/reports/{report_id}/archive")
def get_report_archive(request: Request, report_id: str) -> Response:
    """The report bundle — markdown plus figure files — as a zip download."""
    row = _get_report_done(request, report_id)
    report_dir = Path(row["report_dir"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(report_dir.iterdir()):
            if path.is_file():  # report.md + figures; the staged runs/ tree stays out
                zf.write(path, arcname=path.name)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.zip"'},
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _declared_body_length(request: Request) -> int | None:
    """The request's ``Content-Length`` as an int, ``None`` when absent/unparseable."""
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _is_calibration_path(path: str) -> bool:
    """Whether ``path`` is the multipart upload route (the larger cap)."""
    return path.startswith(f"{router.prefix}/calibrations/")


def _body_cap(path: str, settings: Settings) -> int:
    """Byte ceiling for a request body on ``path``.

    Calibration uploads may carry one data file plus the small form fields
    and multipart framing; everything else is a JSON/YAML document.
    """
    if _is_calibration_path(path):
        return settings.max_upload_bytes + settings.max_body_bytes
    return settings.max_body_bytes


def _cap_env_hint(path: str) -> str:
    """The environment variable(s) that set the cap for ``path``."""
    if _is_calibration_path(path):
        return "FLOWSTATE_MAX_UPLOAD_MB + FLOWSTATE_MAX_BODY_MB"
    return "FLOWSTATE_MAX_BODY_MB"


class BodyCapMiddleware:
    """Pure-ASGI body counter: HTTP 413 once a streamed body passes its cap.

    The auth middleware refuses a *declared* ``Content-Length`` over the cap
    before anything is read, but a chunked request declares no length, and
    every endpoint but ``POST /scenarios`` lets Starlette read the whole body
    (JSON models, multipart uploads) before a handler can look at it. This
    middleware sits in the ASGI path instead of the HTTP one, so it sees the
    body message by message: it adds up ``http.request`` bodies and, the
    moment the running total passes :func:`_body_cap` for the path, stops
    forwarding — the downstream app is told the client disconnected, its
    response (a parse error on a truncated body) is dropped, and the 413 goes
    out in its place. Nothing is enqueued, and the process never holds more
    than one chunk past the cap.

    Mounted *outside* the auth middleware so an unauthenticated oversized
    request is still a plain 401: the counter only ever runs when a handler
    downstream asks for the body, and the 401 is returned without reading it.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        cap = _body_cap(path, self.settings)
        total = 0
        over = False
        started = False

        async def counted_receive() -> Message:
            nonlocal total, over
            if over:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] != "http.request":
                return message
            total += len(message.get("body", b""))
            if total > cap:
                over = True
                return {"type": "http.disconnect"}
            return message

        async def capped_send(message: Message) -> None:
            nonlocal started
            if over and not started:
                return  # the 413 below replaces whatever the app answered
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, capped_send)
        except Exception:
            # A downstream read of a cut-off body may raise instead of
            # answering; the 413 is the answer either way. Anything raised
            # before the cap was passed is still the server's problem.
            if not over:
                raise
        if over and not started:
            response = JSONResponse(
                status_code=413,
                content={
                    "detail": (
                        f"request body exceeds the limit of {cap} bytes for {path} "
                        f"({_cap_env_hint(path)})"
                    )
                },
            )
            await response(scope, receive, send)


#: Logger for the one access line per request.
ACCESS_LOGGER = logging.getLogger("api.access")

#: Correlation-id header, read from the client when sane and always echoed.
REQUEST_ID_HEADER = "X-Request-Id"

#: An inbound id is reused only if it is short and log-safe; anything else
#: (over-long, control characters, an injected newline) is replaced.
#: ``\A``/``\Z`` rather than ``^``/``$``: ``$`` also matches before a final
#: newline, which would let ``"abc\n"`` through as an id.
_REQUEST_ID_RE = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z")


def request_id_of(supplied: str | None) -> str:
    """The client's ``X-Request-Id`` when it is usable, else a fresh uuid4.

    A client-supplied id is echoed back and written to the log, so it is
    reused only when it is at most 64 characters of ``[A-Za-z0-9._-]``:
    that rules out log-line injection, header splitting and unbounded ids
    while keeping the common uuid/trace-id shapes.
    """
    if supplied is not None and _REQUEST_ID_RE.match(supplied):
        return supplied
    return str(uuid.uuid4())


class RequestContextMiddleware:
    """Pure-ASGI request id + access log, outermost of the app's middleware.

    Gives every request an id (the client's ``X-Request-Id`` when it matches
    :data:`_REQUEST_ID_RE`, else uuid4), publishes it on
    ``request.state.request_id``, echoes it on every response — 401, 413, 422
    and the 500 handler included — and logs one line per request at INFO on
    ``api.access``. The id is what a user quotes from a failed call and what
    the server log is grepped for; the 500 body carries it instead of a
    traceback.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = request_id_of(Headers(scope=scope).get(REQUEST_ID_HEADER))
        scope.setdefault("state", {})["request_id"] = request_id
        # Not answered ⇒ the error handler answers 500; that is what is logged.
        status = 500
        start = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                message["headers"] = _with_request_id(message.get("headers", []), request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            ACCESS_LOGGER.info(
                "%s %s %s %.1fms request_id=%s",
                scope.get("method", "-"),
                scope["path"],
                status,
                (time.perf_counter() - start) * 1000.0,
                request_id,
            )


def _with_request_id(
    headers: list[tuple[bytes, bytes]], request_id: str
) -> list[tuple[bytes, bytes]]:
    """``headers`` with exactly one ``X-Request-Id`` — this request's."""
    name = REQUEST_ID_HEADER.lower().encode("latin-1")
    kept = [(key, value) for key, value in headers if key.lower() != name]
    kept.append((name, request_id.encode("latin-1")))
    return kept


def configure_service_logging() -> None:
    """Give the ``api`` logger tree a handler when nothing else has.

    ``uvicorn api.main:app`` (the Dockerfile's command) configures uvicorn's
    own loggers only: the root logger keeps level WARNING and no handler, so
    every ``api.access`` INFO line would be dropped before it reached the
    container log — the access log documented in docs/DEPLOYMENT.md §5 would
    be silently empty. ``api.worker`` calls :func:`logging.basicConfig` for
    the same reason; the API cannot, because that would also be wrong for a
    host process that has configured logging itself.

    One stderr handler is attached to ``api`` (``api.access`` propagates into
    it) the first time an app is built. A process that already has logging
    configured — a handler on the root logger, as under pytest or
    ``basicConfig``, or one on ``api`` from an earlier :func:`create_app` —
    is left alone, so lines are never duplicated.
    """
    logger = logging.getLogger("api")
    if logger.handlers or logging.getLogger().handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def create_app() -> FastAPI:
    """Build the app from the current environment (see api.settings).

    Raises:
        InsecureDefaultKeyError: When a deployed service (``FLOWSTATE_QUEUE=
            redis``) still holds the published default API key. Failing at
            startup is deliberate: the alternative is a service that looks
            healthy while accepting the key printed in this repository's
            README.
    """
    configure_service_logging()
    settings = load_settings()
    check_api_key_not_default(settings)
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.db_path)
    # Rows left at queued/running by a worker or Redis that died while the API
    # was down are repaired once at startup (the worker repeats this on its own
    # start and maintenance passes); the API must come up even if Redis is not
    # reachable yet, so failures here are logged, not raised.
    try:
        from api.jobs import reconcile_store

        report = reconcile_store(store, get_queue(settings), settings.results_dir)
        if report.changed:
            logging.getLogger("api").warning(
                "startup reconciliation: failed=%s requeued=%s reset=%s",
                report.failed,
                report.requeued,
                report.reset,
            )
    except Exception as exc:
        logging.getLogger("api").warning("startup reconciliation skipped: %s", exc)

    app = FastAPI(
        title="FlowState API",
        version=api_pkg.__version__,
        description="Two-tier traffic simulation & analysis service (CLAUDE.md §8).",
    )
    app.state.settings = settings
    app.state.store = store

    expected_keys = tuple(key.encode("utf-8") for key in settings.api_keys)

    @app.middleware("http")
    async def api_key_middleware(request: Request, call_next: Any) -> Any:
        """Shared API key on every /api/... route; /healthz and /docs exempt.

        Any key in ``Settings.api_keys`` authenticates (``FLOWSTATE_API_KEY``
        plus the ``FLOWSTATE_API_KEYS`` rotation list), each compared in
        constant time and without short-circuiting, so the response time does
        not say which key matched or how far along the list it sits.

        Also the request-size gate: a declared ``Content-Length`` over the
        body cap answers 413 before any handler reads the body (a chunked
        body declares no length and is counted as it streams, by
        :class:`BodyCapMiddleware` outside this one).
        """
        path = request.url.path
        if path.startswith("/api/"):
            # Starlette decodes header bytes as latin-1, so re-encoding the
            # same way recovers the raw bytes; comparing bytes keeps
            # compare_digest from raising on a non-ASCII header value.
            supplied = request.headers.get("X-API-Key", "").encode("latin-1", "replace")
            matches = [secrets.compare_digest(supplied, key) for key in expected_keys]
            if not any(matches):
                return JSONResponse(
                    status_code=401, content={"detail": "invalid or missing X-API-Key"}
                )
            declared = _declared_body_length(request)
            cap = _body_cap(path, settings)
            if declared is not None and declared > cap:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            f"request body of {declared} bytes exceeds the limit of "
                            f"{cap} bytes for {path}"
                        )
                    },
                )
        return await call_next(request)

    # Outside the auth middleware (an unauthenticated oversized body is a 401,
    # not a size oracle) and inside CORS.
    app.add_middleware(BodyCapMiddleware, settings=settings)

    # Added after the auth middleware so CORS wraps it (preflights never 401);
    # expose_headers lets a browser client read the request id it must quote.
    app.add_middleware(
        CORSMiddleware,
        # Configured origins only (api.settings.DEFAULT_CORS_ORIGINS is the
        # loopback dev default): a dashboard on another host or port is a
        # different origin, and the browser refuses the call before the API
        # key is ever checked.
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )

    # Last, so it is the outermost: every response — including the ones the
    # middlewares above write themselves — leaves with an X-Request-Id, and
    # every request is logged exactly once.
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """500 body: the request id, never a traceback or a server path.

        The traceback goes to the ``api`` logger (and to the ASGI server,
        which re-raises after this response); the caller gets the id to quote
        so the two ends can be joined without leaking internals.
        """
        request_id = str(request.scope.get("state", {}).get("request_id", "unknown"))
        logging.getLogger("api").exception(
            "unhandled error on %s %s (request id %s)",
            request.method,
            request.url.path,
            request_id,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": f"internal error; request id {request_id}"},
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.get("/healthz", response_model=HealthOut)
    def healthz() -> Any:
        """Store + queue health; 503 when either backend is unreachable."""
        store_status = "ok"
        queue_status = "ok"
        try:
            store.check()
        except Exception as exc:
            store_status = f"error: {exc}"
        try:
            get_queue(settings).check()
        except Exception as exc:
            queue_status = f"error: {exc}"
        ok = store_status == "ok" and queue_status == "ok"
        body = HealthOut(
            status="ok" if ok else "degraded",
            store=store_status,
            queue=queue_status,
            queue_kind=settings.queue_kind,
        )
        if not ok:
            return JSONResponse(status_code=503, content=body.model_dump())
        return body

    app.include_router(router)

    # Single-origin deploy: serve the built frontend at / when it exists.
    # API routes live under /api/v1/... so statics and API never collide.
    if settings.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """Lazily build the module-level ``app`` for ``uvicorn api.main:app``.

    Keeps ``import api.main`` side-effect free (no results dir creation) so
    tests can configure the environment before calling :func:`create_app`.
    """
    if name == "app":
        global _app
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
