"""FlowState v2 FastAPI service (CLAUDE.md §8).

API routes live under ``/api/v1/...`` so the optional single-origin frontend
mount at ``/`` never collides with them. OpenAPI docs at ``/docs``; health at
``/healthz``. Auth is a single API key in the ``X-API-Key`` header checked on
every ``/api/...`` route (``/healthz`` and ``/docs`` are exempt; real auth is
a Phase 4 concern).

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
import secrets
import shutil
import zipfile
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

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

#: Origins allowed by CORS (the Vite dev server).
CORS_ORIGINS = ["http://localhost:5173"]

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


def _report_out(row: dict[str, Any]) -> ReportOut:
    return ReportOut(
        report_id=row["id"],
        status=row["status"],
        run_ids=row["run_ids"],
        title=row["title"],
        report_path=row["report_path"],
        error=row["error"],
        error_kind=row["error_kind"],
        created_at=row["created_at"],
    )


@router.post("/reports", status_code=202, response_model=ReportOut)
def create_report(request: Request, body: ReportCreateRequest) -> ReportOut:
    """Generate a validation report for a set of finished runs.

    Macro-only run sets are refused with HTTP 422: the screening tier cannot
    support validation claims (CLAUDE.md §5.6). Under the Redis queue the
    refusal surfaces asynchronously as ``status=failed`` with
    ``error_kind="report_refused"``.
    """
    store = _store(request)
    settings = _settings(request)
    for rid in body.run_ids:
        if store.get_run(rid) is None:
            raise HTTPException(status_code=404, detail=f"run {rid!r} not found")
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
    return _report_out(row)


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


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(request: Request, report_id: str) -> ReportOut:
    row = _store(request).get_report(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"report {report_id!r} not found")
    return _report_out(row)


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


def _body_cap(path: str, settings: Settings) -> int:
    """Byte ceiling for a request body on ``path``.

    Calibration uploads may carry one data file plus the small form fields
    and multipart framing; everything else is a JSON/YAML document.
    """
    if path.startswith(f"{router.prefix}/calibrations/"):
        return settings.max_upload_bytes + settings.max_body_bytes
    return settings.max_body_bytes


def create_app() -> FastAPI:
    """Build the app from the current environment (see api.settings).

    Raises:
        InsecureDefaultKeyError: When a deployed service (``FLOWSTATE_QUEUE=
            redis``) still holds the published default API key. Failing at
            startup is deliberate: the alternative is a service that looks
            healthy while accepting the key printed in this repository's
            README.
    """
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

    expected_key = settings.api_key.encode("utf-8")

    @app.middleware("http")
    async def api_key_middleware(request: Request, call_next: Any) -> Any:
        """Single API key on every /api/... route; /healthz and /docs exempt.

        Also the request-size gate: a declared ``Content-Length`` over the
        body cap answers 413 before any handler reads the body (chunked
        bodies, which declare no length, are capped by the handlers that
        read them).
        """
        path = request.url.path
        if path.startswith("/api/"):
            # Starlette decodes header bytes as latin-1, so re-encoding the
            # same way recovers the raw bytes; comparing bytes keeps
            # compare_digest from raising on a non-ASCII header value.
            supplied = request.headers.get("X-API-Key", "").encode("latin-1", "replace")
            if not secrets.compare_digest(supplied, expected_key):
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

    # Added after the auth middleware so CORS is outermost (preflights never 401).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
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
