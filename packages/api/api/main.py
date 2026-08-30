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
import secrets
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
from api.settings import Settings, load_settings
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


def _validate_config(raw: dict[str, Any]) -> tuple[dict[str, Any], str, ScenarioConfig]:
    """Validate a raw config dict → (normalized json, config_hash, model)."""
    try:
        cfg = ScenarioConfig.model_validate(raw)
    except ValidationError as exc:
        raise _validation_422(exc) from exc
    return cfg.model_dump(mode="json"), config_hash(cfg), cfg


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
    """Validate + store a scenario config (JSON or YAML request body)."""
    body = await request.body()
    try:
        raw = yaml.safe_load(body.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=f"unparseable config body: {exc}") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="config body must be a mapping")
    config, chash, cfg = _validate_config(raw)
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
        seeded=row["config"].get("perturbation") is not None,
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
    """Enqueue a run: stored config + deep-merged overrides, re-validated."""
    store = _store(request)
    settings = _settings(request)
    scenario = store.get_scenario(body.scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"scenario {body.scenario_id!r} not found")
    merged = _apply_overrides(scenario["config"], body.overrides, body.replicates, body.tier)
    config, chash, cfg = _validate_config(merged)
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
        per_replicate, agg = res.run_metrics(row["run_root"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MetricsOut(
        run_id=run_id,
        config_hash=row["config_hash"],
        tier=row["tier"],
        seeded=row["config"].get("perturbation") is not None,
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
                try:
                    _, agg = res.run_metrics(run["run_root"])
                    aggregate = {name: CIOut(**res.ci_to_json(ci)) for name, ci in agg.items()}
                except FileNotFoundError:
                    aggregate = None
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
    """
    store = _store(request)
    settings = _settings(request)
    scenario = store.get_scenario(body.scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"scenario {body.scenario_id!r} not found")
    base = _apply_overrides(scenario["config"], body.overrides, body.replicates, body.tier)
    grid: list[dict[str, Any]] = []
    for pen in body.penetrations:
        for comp in body.compliances:
            for ctrl in body.controllers:
                cell_patch = {"av": {"penetration": pen, "compliance": comp, "controller": ctrl}}
                try:
                    config, chash, _ = _validate_config(deep_merge(base, cell_patch))
                except HTTPException as exc:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "cell": {
                                "penetration": pen,
                                "compliance": comp,
                                "controller": ctrl,
                            },
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
    options) and ``source`` (provenance string stored on the artifact).
    """
    store = _store(request)
    settings = _settings(request)
    if (file is None) == (data_path is None):
        raise HTTPException(status_code=422, detail="provide exactly one of 'file' or 'data_path'")
    try:
        params_dict = json.loads(params) if params else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"params is not valid JSON: {exc}") from exc
    if not isinstance(params_dict, dict):
        raise HTTPException(status_code=422, detail="params must be a JSON object")

    if file is not None:
        filename = Path(file.filename or "upload.csv").name
        dest = settings.uploads_dir / new_id("upl") / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(await file.read())
        resolved = dest
    else:
        assert data_path is not None
        resolved = Path(data_path)
        if not resolved.is_file():
            raise HTTPException(status_code=422, detail=f"data_path {data_path!r} not found")

    cal_id = store.create_calibration(
        kind, resolved, params_dict, source or f"{kind} upload {resolved.name}"
    )
    job = fd_calibration_job if kind == "fd" else idm_calibration_job
    get_queue(settings).enqueue(
        job, cal_id, db_path=str(settings.db_path), results_root=str(settings.results_dir)
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


def create_app() -> FastAPI:
    """Build the app from the current environment (see api.settings)."""
    settings = load_settings()
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.db_path)

    app = FastAPI(
        title="FlowState API",
        version=api_pkg.__version__,
        description="Two-tier traffic simulation & analysis service (CLAUDE.md §8).",
    )
    app.state.settings = settings
    app.state.store = store

    @app.middleware("http")
    async def api_key_middleware(request: Request, call_next: Any) -> Any:
        """Single API key on every /api/... route; /healthz and /docs exempt."""
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("X-API-Key", "")
            if not secrets.compare_digest(supplied, settings.api_key):
                return JSONResponse(
                    status_code=401, content={"detail": "invalid or missing X-API-Key"}
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
