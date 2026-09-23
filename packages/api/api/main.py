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
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from fastapi import (
    APIRouter,
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Security,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import api as api_pkg
from api import results as res
from api.calibration_jobs import demand_calibration_job  # WP-A
from api.jobs import (
    REPORT_REFUSED_KIND,
    JobQueue,
    fd_calibration_job,
    get_queue,
    idm_calibration_job,
    report_job,
    run_scenario_job,
    sweep_job,
)
from api.onboarding_jobs import corridor_onboarding_job, extract_path  # WP-F
from api.schemas import (
    CORRIDOR_NAME_PATTERN,
    CORRIDOR_STAGES,
    DEFAULT_CRITERIA_PROFILE,
    MAX_CORRIDOR_LIST,
    MAX_REPORT_LIST,
    CalibrationOut,
    CalibrationParams,
    CIOut,
    CorridorOut,
    CorridorProgressOut,
    CorridorRowOut,
    CriteriaProfileOut,
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
from api.store import CorridorNameTaken, Store, new_id
from flowstate_core.artifacts import FDCalibration
from flowstate_core.config import MacroOptions, ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from flowstate_core.strategies import StrategyError, apply_strategy
from flowstate_core.units import veh_m_to_veh_km
from validation.metrics import MIN_REPLICATES

router = APIRouter(prefix="/api/v1")

#: The API key as an OpenAPI security scheme, declared purely so the contract
#: says so: ``/docs`` renders an **Authorize** button from
#: ``components.securitySchemes`` and nothing else, and a client generated
#: from ``/openapi.json`` emits the header only when the spec names it.
#:
#: ``auto_error=False`` is load-bearing — with FastAPI's default the
#: dependency would answer 403 "Not authenticated" *ahead of*
#: ``api_key_middleware``, replacing the documented 401. Enforcement stays in
#: the middleware alone (it also covers the routes FastAPI does not own), so
#: declaring this scheme changes the spec and nothing else.
API_KEY_SCHEME = APIKeyHeader(name="X-API-Key", auto_error=False, scheme_name="ApiKeyAuth")

#: Responses every ``/api/v1`` route can produce from the middleware layer,
#: declared once at the include point rather than repeated per route.
_ROUTER_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "invalid or missing X-API-Key"},
    413: {"description": "request body exceeds the cap for this path"},
}

#: Added to routes addressed by an id.
_NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {404: {"description": "no such record"}}


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
    if cfg.fd_calibration is not None:
        fields.append((("fd_calibration",), cfg.fd_calibration))
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

    ``network.osm_file``, ``fleet.idm_calibration``,
    ``fleet.heavy.idm_calibration`` and ``fd_calibration`` are read by the
    worker (netconvert, the ``IDMCalibration`` / ``FDCalibration`` loaders),
    and a parse failure there can quote the file's bytes back through the
    run's error text — so, like a calibration ``data_path``, they may only
    point inside the allow-listed roots.
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
    macro: MacroOptions | None = None,
) -> dict[str, Any]:
    """The effective config of a run/sweep request.

    The typed request fields are applied after the free-form ``overrides``
    patch, so a request that sets both wins with the typed one. ``macro``
    lands in the config (rather than staying on the request) because the
    config is the reproducible record: it is hashed, stored on the run row,
    re-read by the worker on a reconciliation re-enqueue, and snapshotted
    into every replicate's ``meta.json``.
    """
    merged = deep_merge(base, overrides)
    if replicates is not None:
        merged["replicates"] = replicates
    if tier is not None:
        merged["tier"] = tier
    if macro is not None:
        merged["macro"] = macro.model_dump(mode="json")
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


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut, responses=_NOT_FOUND_RESPONSE)
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


def _check_measurement_window(cfg: ScenarioConfig) -> None:
    """Refuse a **micro** run whose warm-up swallows its whole duration.

    Everything before ``sim.warmup_s`` is discarded from the metrics, so a
    duration at or below it leaves nothing to measure and every replicate
    dies on the worker with "warm-up N s leaves no measurement window". The
    request is unsatisfiable as posted — usually a shortened ``duration_s``
    override against a scenario calibrated with a long warm-up — so it is
    refused here instead of consuming a queue slot per replicate.

    The macro tier is exempt: ``api.results.macro_metrics`` reports over the
    whole run and never applies ``sim.warmup_s``, so a macro run with
    ``duration_s <= warmup_s`` completes and yields metrics. Refusing it
    would be a false refusal of a request the platform can satisfy.
    """
    if cfg.tier == "macro":
        return
    warmup = cfg.sim.warmup_s
    if warmup > 0 and cfg.sim.duration_s <= warmup:
        raise HTTPException(
            status_code=422,
            detail=(
                f"sim.duration_s ({cfg.sim.duration_s:g} s) leaves no measurement window: "
                f"the first {warmup:g} s are discarded as warm-up (sim.warmup_s). "
                "Raise the duration past the warm-up, or lower the warm-up."
            ),
        )


@router.post("/runs", status_code=202, response_model=RunOut, responses=_NOT_FOUND_RESPONSE)
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
    merged = _apply_overrides(
        scenario["config"], body.overrides, body.replicates, body.tier, body.macro
    )
    config, chash, cfg = _validate_config(merged, settings)
    _check_measurement_window(cfg)
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


@router.get("/runs/{run_id}", response_model=RunOut, responses=_NOT_FOUND_RESPONSE)
def get_run(request: Request, run_id: str) -> RunOut:
    return _run_out(_get_run_or_404(request, run_id))


@router.get("/runs/{run_id}/metrics", response_model=MetricsOut, responses=_NOT_FOUND_RESPONSE)
def get_run_metrics(request: Request, run_id: str) -> MetricsOut:
    """Per-replicate metrics + aggregate t-distribution CIs (contract §7).

    ``underpowered`` is reported honestly: any aggregate over fewer than 20
    replicates is flagged and must not be quoted as a headline result
    (CLAUDE.md §0.6). A macro (screening) run also reports ``fd_source`` —
    the ``FDCalibration`` artifact its fundamental diagram was read from, or
    the uncalibrated ``v1_legacy`` preset — so a screening number is never
    shown without the provenance of the diagram that produced it.
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
        fd_source=_fd_source(row),
    )


def _fd_source(row: dict[str, Any]) -> str | None:
    """Provenance of a macro run's fundamental diagram, from its ``meta.json``.

    Reads the first replicate's meta (the config, and therefore the diagram,
    is identical across replicates). Returns ``None`` for a micro-tier run,
    and for any run whose meta cannot be read or predates the field — the
    dashboard then says the source is unknown rather than naming one.

    Args:
        row: The run's store row (``tier``, ``run_root``).

    Returns:
        ``meta["fd"]["source"]`` — an artifact path or ``"v1_legacy preset"``
        — or ``None``.
    """
    if row["tier"] != "macro":
        return None
    try:
        dirs = res.replicate_dirs(row["run_root"])
        if not dirs:
            return None
        fd = res.load_meta(dirs[0]).get("fd") or {}
    except (OSError, ValueError):
        return None
    source = fd.get("source")
    return source if isinstance(source, str) else None


@router.get("/runs/{run_id}/heatmap", response_model=None, responses=_NOT_FOUND_RESPONSE)
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
                # Sweeps stored before the strategy axis carry no such key;
                # every one of their cells ran the scenario as calibrated.
                strategy=cell.get("strategy", "none"),
                config_hash=cell["config_hash"],
                run_id=run_id,
                status=status,
                progress=progress,
                aggregate=aggregate,
            )
        )
    grid = sweep["grid"]
    # One base config builds every cell, so the grid is single-tier; read it
    # from the stored cell config rather than from a run row, which does not
    # exist until the fan-out job has created it.
    tier = grid[0]["config"].get("tier", "micro") if grid else None
    return SweepOut(
        sweep_id=sweep["id"],
        scenario_id=sweep["scenario_id"],
        status=sweep["status"],
        tier=tier,
        error=sweep["error"],
        created_at=sweep["created_at"],
        runs_total=len(sweep["grid"]),
        runs_done=runs_done,
        runs_failed=runs_failed,
        cells=cells,
    )


def _alinea_target_veh_km(
    base: dict[str, Any], body: SweepCreateRequest, settings: Settings
) -> float | None:
    """The ALINEA target density [veh/km] a sweep's metered cells post.

    ``None`` when no requested strategy meters ramps. Otherwise the request's
    own ``alinea.rho_target_veh_km``, or the critical density of the
    scenario's ``FDCalibration`` artifact (``fd.rho_c``, per lane) when the
    request states none. There is no third source: a metering target that
    traced to no diagram would be an uncalibrated constant driving a
    published result (CLAUDE.md §0.1).

    The artifact is read here, in the request path, so the refusal is a 422
    the caller can act on rather than a worker failure per cell; it is a
    small JSON file and only read when metering is requested. The base config
    is validated and path-confined first (:func:`_confine_config_paths`), so
    this read cannot reach outside the allow-listed roots.

    Raises:
        HTTPException: 422 when metering is requested and neither source
            supplies a target, or the named artifact cannot be read.
    """
    if not body.needs_alinea_target():
        return None
    if body.alinea is not None:
        return body.alinea.rho_target_veh_km
    try:
        cfg = ScenarioConfig.model_validate(base)
    except ValidationError as exc:
        raise _validation_422(exc) from exc
    _confine_config_paths(cfg, settings)
    if cfg.fd_calibration is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "an alinea strategy needs a metering target: set "
                "alinea.rho_target_veh_km on the request, or fd_calibration on the "
                "scenario so the target can be read from its fitted diagram"
            ),
        )
    path = _resolve_config_path(cfg.fd_calibration)
    try:
        artifact = FDCalibration.load(path)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"the scenario's fd_calibration {cfg.fd_calibration!r} could not be read "
                f"as an FDCalibration artifact ({type(exc).__name__}); set "
                "alinea.rho_target_veh_km explicitly or fix the artifact"
            ),
        ) from exc
    return veh_m_to_veh_km(artifact.fd.rho_c)


@router.post("/sweeps", status_code=202, response_model=SweepOut, responses=_NOT_FOUND_RESPONSE)
def create_sweep(request: Request, body: SweepCreateRequest) -> SweepOut:
    """Fan a penetration × compliance × controller × strategy grid into runs.

    Every cell's effective config is validated and hashed here (422 on any
    invalid cell); the fan-out itself runs as a job.

    ``strategies`` is the infrastructure axis — ``none``, ``vsl``,
    ``alinea``, ``vsl+alinea`` — applied to each cell by
    :func:`flowstate_core.strategies.apply_strategy`, the same patch
    ``scripts/corridor_sweep.py`` applies, so a CLI cell and an API cell of
    one grid point share a ``config_hash``. Each requested strategy also gets
    one uncontrolled cell of its own, so the infrastructure can be priced
    without any controlled vehicle. An ``alinea`` strategy needs a metering
    target (``alinea.rho_target_veh_km``, else the scenario's
    ``fd_calibration`` critical density) and at least one on-ramp to meter;
    without either, 422.

    ``include_baseline`` appends a *single* uncontrolled reference cell
    (penetration 0, compliance 1, no controller) after the grid, unless the
    grid already holds an uncontrolled cell (penetration 0 among
    ``penetrations``, or ``null`` among ``controllers``); a "Δ vs baseline"
    comparison then has an uncontrolled run to compare against rather than
    the smallest controlled cell. One cell, not one per controller: at
    penetration 0 no vehicle is controlled, so per-controller baselines would
    be identical simulations — and the report's controller-minus-baseline
    contrast needs exactly one baseline group to exist at all. Repeated axis
    values collapse for the same reason (identical cells share a
    ``config_hash`` and a run tree).

    Caps (HTTP 422 when exceeded, all checked before any cell is built):
    at most 50 values per axis (``api.schemas.MAX_SWEEP_AXIS_VALUES``), 200
    total cells including the infrastructure-only and baseline cells
    (``MAX_SWEEP_CELLS``), and 200 replicates per cell (``MAX_REPLICATES``).
    The grid is a cartesian product, so the cell ceiling is checked from the
    four list lengths rather than by materializing them.

    The measurement window is checked once for the whole grid
    (:func:`_check_measurement_window`, the same check ``POST /runs``
    makes): no cell patch touches ``sim``, so a micro grid whose overrides
    leave ``duration_s`` at or below ``sim.warmup_s`` is one 422 naming the
    two numbers rather than a fan-out of cells that each die on the worker.
    """
    store = _store(request)
    settings = _settings(request)
    scenario = store.get_scenario(body.scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"scenario {body.scenario_id!r} not found")
    base = _apply_overrides(
        scenario["config"], body.overrides, body.replicates, body.tier, body.macro
    )
    rho_target_veh_km = _alinea_target_veh_km(base, body, settings)
    grid: list[dict[str, Any]] = []
    for pen, comp, ctrl, strategy in body.grid_cells():
        cell_patch = {"av": {"penetration": pen, "compliance": comp, "controller": ctrl}}
        # deepcopy: deep_merge shares the sub-dicts the patch does not touch
        # with `base`, and a strategy patches network.ramps in place — one
        # metered cell would otherwise meter every later cell too.
        merged = deepcopy(deep_merge(base, cell_patch))
        try:
            apply_strategy(merged, strategy, rho_target_veh_km)
        except StrategyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            config, chash, cell_cfg = _validate_config(merged, settings)
        except HTTPException as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "cell": {
                        "penetration": pen,
                        "compliance": comp,
                        "controller": ctrl,
                        "strategy": strategy,
                    },
                    "errors": exc.detail,
                },
            ) from exc
        if not grid:
            # No cell patch touches `sim`, so the measurement window is the
            # same in every cell: check it once, on the first cell built, and
            # refuse the whole grid rather than fanning out cells that would
            # each die on the worker.
            _check_measurement_window(cell_cfg)
        grid.append(
            {
                "penetration": pen,
                "compliance": comp,
                "controller": ctrl,
                "strategy": strategy,
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


@router.get("/sweeps/{sweep_id}", response_model=SweepOut, responses=_NOT_FOUND_RESPONSE)
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


#: Extra artifact files a calibration kind writes beside ``artifact_path``,
#: published under ``CalibrationOut.artifact_paths`` (WP-A: a ``demand``
#: calibration writes the observations artifact *and* the demand artifact).
_EXTRA_ARTIFACTS: dict[str, dict[str, str]] = {"demand": {"demand": "demand.json"}}


def _artifact_paths(row: dict[str, Any]) -> dict[str, str]:
    """Every artifact file of a finished calibration, keyed by name."""
    primary = row["artifact_path"]
    if row["status"] != "done" or not primary:
        return {}
    kind = str(row["kind"])
    name = "observations" if kind == "demand" else kind
    paths = {name: str(primary)}
    for label, filename in _EXTRA_ARTIFACTS.get(kind, {}).items():
        sibling = Path(primary).parent / filename
        if sibling.is_file():
            paths[label] = str(sibling)
    return paths


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
        artifact_paths=_artifact_paths(row),
    )


@router.post("/calibrations/{kind}", status_code=202, response_model=CalibrationOut)
async def create_calibration(
    request: Request,
    kind: Literal["fd", "idm", "demand"],  # WP-A: 'demand' → observations + demand artifacts
    file: Annotated[UploadFile | None, File()] = None,
    data_path: Annotated[str | None, Form()] = None,
    params: Annotated[str | None, Form()] = None,
    source: Annotated[str | None, Form()] = None,
) -> CalibrationOut:
    """Run an FD, IDM or demand calibration on an uploaded file or server path.

    Multipart/form fields: exactly one of ``file`` (upload) or ``data_path``
    (path visible to the workers); optional ``params`` (JSON object of fit
    options, validated against ``api.schemas.CalibrationParams`` — bounded
    values, unknown keys refused with HTTP 422) and ``source`` (provenance
    string stored on the artifact).

    ``demand`` (WP-A) takes the tidy detector CSV of the observations contract
    and writes two artifacts — ``observations.json``
    (``flowstate.observations/1``) and ``demand.json``
    (``flowstate.demand/1``) — both listed in ``artifact_paths`` on
    ``GET /calibrations/{id}``; its options are ``window_s``, ``t0_local``,
    ``duration_s``, ``upstream_station``, ``stations``, ``ramps``,
    ``corridor`` plus the detector-loader keys (``column_map``,
    ``speed_unit``, ``occupancy_unit``, ``kind_default``).

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
    job = {  # WP-A: 'demand' added to the dispatch
        "fd": fd_calibration_job,
        "idm": idm_calibration_job,
        "demand": demand_calibration_job,
    }[kind]
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


@router.get(
    "/calibrations/{calibration_id}",
    response_model=CalibrationOut,
    responses=_NOT_FOUND_RESPONSE,
)
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
        profile=row["profile"],
        observations_path=row["observations_path"],
        observed=row["observed"],
        report_path=_relative_report_path(row["report_path"], settings),
        error=row["error"],
        error_kind=row["error_kind"],
        created_at=row["created_at"],
    )


@router.get("/criteria", response_model=list[CriteriaProfileOut])
def list_criteria_profiles() -> list[CriteriaProfileOut]:
    """Selectable acceptance-criteria profiles for ``POST /reports``.

    ``validation.criteria.CRITERIA_PROFILES`` holds the FlowState default
    plus the state-DOT variants CLAUDE.md §7.1 calls for (the 2004 FHWA
    toolbox table, ODOT's 2011 VISSIM protocol, TxDOT TSAP ch. 13). Each
    row's ``source`` states which document, section and table its numbers
    were transcribed from, what was verified and which rows are FlowState's
    own conventions rather than the document's — read it before quoting a
    profile in a deliverable.

    Thresholds only. The measurements scored against them are computed from
    run artifacts; this endpoint never accepts a value (CLAUDE.md §7.4).
    """
    from validation.criteria import CRITERIA_PROFILES

    return [
        CriteriaProfileOut(
            name=p.name,
            source=p.source,
            geh_threshold=p.geh_threshold,
            geh_pass_fraction=p.geh_pass_fraction,
            geh_pass_inclusive=p.geh_pass_inclusive,
            rmspe_max=p.rmspe_max,
            wave_speed_band_kmh=p.wave_speed_band_kmh,
            min_seeds=p.min_seeds,
            require_ring_emergence=p.require_ring_emergence,
            require_ring_dampening=p.require_ring_dampening,
            require_sensitivity_grid=p.require_sensitivity_grid,
            wave_detector=p.wave_detector.name,
            default=p.name == DEFAULT_CRITERIA_PROFILE,
        )
        for p in CRITERIA_PROFILES.values()
    ]


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


def _resolve_observations_path(value: str, settings: Settings) -> Path:
    """Resolve a report's ``observations_path`` inside the allow-listed roots.

    The artifact names a file on the *worker's* filesystem, exactly like a
    calibration ``data_path`` (:func:`_resolve_data_path`) or a config file
    field (:func:`_confine_config_paths`), so it is confined the same way —
    to :attr:`Settings.config_path_roots`, which is the data roots plus the
    repository's ``artifacts/`` and ``data/`` directories where corridor
    onboarding writes its observations. Relative values resolve against the
    repository root, never the API process's working directory.

    Args:
        value: The requested path.
        settings: Live settings (the allow-list).

    Returns:
        The resolved path.

    Raises:
        HTTPException: 422 when it escapes the roots, 404 when it is missing.
    """
    resolved = _resolve_config_path(value)
    roots = settings.config_path_roots
    if not any(resolved.is_relative_to(root) for root in roots):
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "type": "path_outside_roots",
                    "loc": ["body", "observations_path"],
                    "msg": (
                        f"observations_path {value!r} is outside the allowed data roots "
                        f"{[str(r) for r in roots]}"
                    ),
                    "input": value,
                }
            ],
        )
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"observations_path {value!r} not found")
    return resolved


@router.post("/reports", status_code=202, response_model=ReportOut, responses=_NOT_FOUND_RESPONSE)
def create_report(request: Request, body: ReportCreateRequest) -> ReportOut:
    """Generate a validation report for a set of finished runs.

    Run sets carrying screening (macro) runs are refused with HTTP 422 — the
    screening tier cannot support validation claims (CLAUDE.md §5.6). A
    *mixed* micro+macro set is refused here, before the row is created,
    naming the macro runs (:func:`_refuse_macro_runs_in_report`); an
    *all-macro* set is refused by ``validation.report.generate_report``
    itself, which under the Redis queue surfaces asynchronously as
    ``status=failed`` with ``error_kind="report_refused"``.

    ``profile`` selects the acceptance-criteria thresholds the report is
    scored against (``GET /api/v1/criteria``; unknown names are 422), so a
    DOT pilot can ask for its own protocol instead of the FlowState default.
    The *measurements* are computed from the run artifacts and are never
    accepted from the request: criteria whose evidence is observed field data
    this service does not hold (GEH against observed link counts,
    segment-speed RMSPE, the two ring benchmarks, the sensitivity grid) come
    back **not evaluated**. An API report is therefore a run-set metrics
    bundle with the profile's thresholds stated, not a signed-off calibration
    acceptance deliverable — see docs/DEPLOYMENT.md.

    ``observations_path`` is the one way to supply the missing evidence: a
    server-side ``flowstate.observations/1`` artifact (docs/CONTRACTS.md,
    "Detector observations"), confined to the allow-listed roots like every
    other path in a request. With it the worker scores every completed micro
    replicate against the corridor's detectors and the ``link_flows_geh`` and
    ``speeds_rmspe`` rows are evaluated from *computed* comparisons — still
    never from a number in the request body.
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
    observations = (
        str(_resolve_observations_path(body.observations_path, settings))
        if body.observations_path is not None
        else None
    )
    report_id = store.create_report(body.run_ids, body.title, body.profile, observations)
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


@router.get("/reports/{report_id}", response_model=ReportOut, responses=_NOT_FOUND_RESPONSE)
def get_report(request: Request, report_id: str) -> ReportOut:
    row = _store(request).get_report(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"report {report_id!r} not found")
    return _report_out(row, _settings(request))


@router.get("/reports/{report_id}/markdown", responses=_NOT_FOUND_RESPONSE)
def get_report_markdown(request: Request, report_id: str) -> PlainTextResponse:
    """The rendered markdown report."""
    row = _get_report_done(request, report_id)
    return PlainTextResponse(Path(row["report_path"]).read_text(), media_type="text/markdown")


@router.get("/reports/{report_id}/pdf", responses=_NOT_FOUND_RESPONSE)
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


@router.get("/reports/{report_id}/archive", responses=_NOT_FOUND_RESPONSE)
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
# Corridor onboarding (WP-F)
# ---------------------------------------------------------------------------

_CORRIDOR_NAME_RE = re.compile(CORRIDOR_NAME_PATTERN)

#: Server-side names the two ``POST /corridors`` uploads are stored under.
#: They are fixed rather than taken from the client's filenames: the two parts
#: share one upload directory, so two parts named alike would collide and the
#: second would overwrite the first.
DETECTORS_UPLOAD_NAME = "detectors.csv"
STATIONS_UPLOAD_NAME = "stations.csv"


def _parse_bbox(raw: str) -> list[float]:
    """``"S W N E"`` (or comma-separated) → the four WGS84 bounds.

    Raises:
        HTTPException: 422 when it is not four numbers in range with
            ``south < north`` and ``west < east``. The message says which,
            because a transposed bbox is the most common onboarding mistake.
    """
    parts = [p for p in re.split(r"[,\s]+", raw.strip()) if p]
    if len(parts) != 4:
        raise HTTPException(
            status_code=422,
            detail=(
                f"bbox must be four numbers 'south west north east' (comma or space "
                f"separated), got {raw!r}"
            ),
        )
    try:
        south, west, north, east = (float(p) for p in parts)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"bbox is not numeric: {exc}") from exc
    if not (south < north and west < east):
        raise HTTPException(
            status_code=422,
            detail=(
                f"bbox must be (south, west, north, east) with south < north and west < east, "
                f"got south={south}, west={west}, north={north}, east={east}"
            ),
        )
    if not (-90.0 <= south and north <= 90.0 and -180.0 <= west and east <= 180.0):
        raise HTTPException(status_code=422, detail=f"bbox is outside the WGS84 range: {raw!r}")
    return [south, west, north, east]


def _parse_column_map(raw: str | None) -> dict[str, str]:
    """The optional ``column_map`` form field → the detector loader's mapping.

    Raises:
        HTTPException: 422 when it is not a JSON object of strings.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"column_map is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
    ):
        raise HTTPException(
            status_code=422, detail="column_map must be a JSON object of string → string"
        )
    return {str(k): str(v) for k, v in parsed.items()}


def _confine_corridor_path(value: str, field: str, settings: Settings) -> str:
    """Confine a server-side path named in a corridor request to the roots.

    The same rule as a scenario config's file fields
    (:func:`_confine_config_paths`): the worker opens the file, so an
    unchecked value reads anything the worker can see.

    Raises:
        HTTPException: 422 in the pydantic error-list shape.
    """
    resolved = _resolve_config_path(value)
    roots = settings.config_path_roots
    if not any(resolved.is_relative_to(root) for root in roots):
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "type": "path_outside_roots",
                    "loc": ["body", field],
                    "msg": (
                        f"{field} {value!r} is outside the allowed data roots "
                        f"{[str(r) for r in roots]}; reference a file under the repo's "
                        f"artifacts/ or data/ directories, FLOWSTATE_DATA_DIR, or the "
                        f"results root"
                    ),
                    "input": value,
                }
            ],
        )
    return str(resolved)


def _corridor_row_fields(row: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """The status fields both corridor responses carry (all but the summary).

    Args:
        row: A ``corridors`` store row.
        settings: The app settings, for the results root ``corridor_dir`` is
            reported relative to.

    Returns:
        Keyword arguments shared by :class:`CorridorRowOut` and
        :class:`CorridorOut`.
    """
    stage = row["stage"]
    completed = CORRIDOR_STAGES.index(stage) if stage in CORRIDOR_STAGES else 0
    corridor_dir = row["corridor_dir"]
    if corridor_dir:
        try:
            corridor_dir = str(Path(corridor_dir).relative_to(settings.results_dir))
        except ValueError:
            pass  # a results root moved between runs: report the path as stored
    return {
        "corridor_id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "progress": CorridorProgressOut(
            stage=stage,
            completed_stages=completed,
            total_stages=len(CORRIDOR_STAGES) - 1,
        ),
        "scenario_id": row["scenario_id"],
        "preset_filename": f"{row['name']}.yaml" if row["scenario_id"] else None,
        "config_hash": row["config_hash"],
        "observations_path": row["observations_path"],
        "corridor_dir": corridor_dir,
        "error": row["error"],
        "error_kind": row["error_kind"],
        "created_at": row["created_at"],
    }


def _corridor_row_out(row: dict[str, Any], settings: Settings) -> CorridorRowOut:
    return CorridorRowOut(**_corridor_row_fields(row, settings))


def _corridor_out(row: dict[str, Any], settings: Settings) -> CorridorOut:
    return CorridorOut(**_corridor_row_fields(row, settings), summary=row["summary"])


def _run_corridor_inline(
    queue: JobQueue, store: Store, corridor_id: str, settings: Settings
) -> None:
    """Run one inline corridor onboarding as a background task, and settle it.

    Nothing reconciles an inline queue: it has no worker, no Redis record and
    no startup sweep behind it, so this call is the only thing that will ever
    look at the row. An exception that escapes
    :func:`api.onboarding_jobs.corridor_onboarding_job`'s own handler — raised
    before it claimed the row, or by the claim itself — would otherwise leave
    the row ``queued`` for ever and a polling client waiting for ever. The row
    is therefore failed from outside, exactly as the worker's work-horse death
    handler does it (:meth:`api.store.Store.fail_active`, which never
    overwrites an outcome the job already recorded).

    Nothing is re-raised: a Starlette background task has no caller left to
    raise to — the response went out before it ran — so the store row, not an
    exception, is how this failure reaches anyone.

    Args:
        queue: The inline queue; its ``enqueue`` *is* the call.
        store: Store holding the corridor row.
        corridor_id: Corridor row id (also the job id).
        settings: Server settings; the job is given the store path and the
            results root explicitly, as a worker would be.
    """
    try:
        queue.enqueue(
            corridor_onboarding_job,
            corridor_id,
            job_id=corridor_id,
            db_path=str(settings.db_path),
            results_root=str(settings.results_dir),
        )
    # BaseException, and nothing re-raised: the row is the only report left.
    except BaseException as exc:
        logging.getLogger("api").exception(
            "inline corridor onboarding %s crashed outside the job's own handler", corridor_id
        )
        store.fail_active(
            "corridor",
            corridor_id,
            f"the onboarding job stopped without recording an outcome: {type(exc).__name__}: {exc}",
        )


@router.post("/corridors", status_code=202, response_model=CorridorOut)
async def create_corridor(
    request: Request,
    background: BackgroundTasks,
    name: Annotated[str, Form()],
    bbox: Annotated[str, Form()],
    bearing_deg: Annotated[float, Form()],
    upstream_station: Annotated[str, Form()],
    downstream_station: Annotated[str, Form()],
    detectors: Annotated[UploadFile, File()],
    stations: Annotated[UploadFile, File()],
    column_map: Annotated[str | None, Form()] = None,
    idm_calibration: Annotated[str | None, Form()] = None,
    window_s: Annotated[float, Form()] = 300.0,
    t0_local: Annotated[str, Form()] = "06:00",
    duration_s: Annotated[float, Form()] = 14400.0,
    warmup_s: Annotated[float, Form()] = 1800.0,
    source: Annotated[str | None, Form()] = None,
) -> CorridorOut:
    """Onboard a freeway corridor from a bounding box and a detector export.

    The "any city" path of CLAUDE.md §3.2.4 as one asynchronous job
    (``api.onboarding_jobs``): the OSM extract is downloaded, the mainline
    chain, lane profile and ramps are discovered, the detector CSV becomes a
    ``flowstate.observations/1`` artifact, the demand is derived from it
    (upstream station inflow, ramps closing the station balance, downstream
    speed boundary), and the calibrated scenario is stored *and* written into
    the presets directory so it is selectable beside the shipped corridors.
    Poll ``GET /corridors/{id}``; the finished row carries the
    ``scenario_id`` for ``POST /runs`` and the ``observations_path`` for
    ``POST /reports``. The 202 body is always the *queued* row — under the
    inline queue too, where the job is handed to a background task once the
    response has gone out rather than run inside the request (an onboarding
    held in-request blocks ``/healthz`` for its whole duration).

    Multipart form fields: ``name`` (also the preset filename, so
    ``[A-Za-z0-9_-]``), ``bbox`` as ``"south west north east"``,
    ``bearing_deg`` (270 = westbound), ``upstream_station`` and
    ``downstream_station`` (the mainline detectors at the two ends of the
    analysed span), the ``detectors`` CSV (the tidy detector contract, with
    an optional ``column_map`` JSON object naming the file's own columns) and
    the ``stations`` CSV (``station,label,lat,lon,lanes,kind``). Optional:
    ``idm_calibration`` (a driver population, a server-side path confined to
    the allow-listed roots), ``window_s`` (300), ``t0_local`` (06:00),
    ``duration_s`` (14400), ``warmup_s`` (1800) and ``source`` (provenance
    recorded on the observations artifact).

    Refusals: HTTP 422 for a malformed name, bbox, bearing, window/span or
    ``column_map``, and for an ``idm_calibration`` outside the roots; HTTP
    409 when a preset **or an OSM extract** of that name already exists (a
    corridor is never onboarded over either — that would silently redefine a
    scenario other runs were launched from, or replace the map a shipped
    scenario re-imports on every replicate). Uploads over
    ``FLOWSTATE_MAX_UPLOAD_MB`` are refused with HTTP 413.

    A name whose onboarding *failed* is free again: the job removes the preset
    and extract it wrote before failing, so the same name can simply be
    retried. A name whose onboarding is still ``queued`` or ``running`` is
    taken: the store holds it from the moment the row is created
    (:exc:`api.store.CorridorNameTaken`), so of two concurrent requests for
    one name the second is refused with HTTP 409 and no second job is ever
    dispatched. The job re-checks the preset and the extract anyway — the row
    lock is released when the onboarding settles, and the files it installed
    outlive it.

    Onboarding is not validation: the job derives numbers and records where
    each came from. Whether the corridor reproduces the observations is what
    ``POST /reports`` with ``observations_path`` answers (CLAUDE.md §0.1).
    """
    settings = _settings(request)
    store = _store(request)
    # fullmatch, not match: `$` also matches before a trailing newline, so a
    # name ending in one would pass and then become a file name carrying it.
    if not _CORRIDOR_NAME_RE.fullmatch(name):
        raise HTTPException(
            status_code=422,
            detail=(
                f"name {name!r} must match {CORRIDOR_NAME_PATTERN} — it becomes a directory "
                f"and a scenarios/<name>.yaml preset file"
            ),
        )
    bounds = _parse_bbox(bbox)
    if not 0.0 <= bearing_deg <= 360.0:
        raise HTTPException(
            status_code=422, detail=f"bearing_deg must be within [0, 360], got {bearing_deg}"
        )
    for field, value in (("window_s", window_s), ("duration_s", duration_s)):
        if value <= 0.0:
            raise HTTPException(status_code=422, detail=f"{field} must be positive, got {value}")
    if warmup_s < 0.0 or warmup_s >= duration_s:
        raise HTTPException(
            status_code=422,
            detail=(
                f"warmup_s must be within [0, duration_s), got warmup_s={warmup_s} and "
                f"duration_s={duration_s}"
            ),
        )
    columns = _parse_column_map(column_map)
    calibration = (
        _confine_corridor_path(idm_calibration, "idm_calibration", settings)
        if idm_calibration
        else None
    )
    # The two files the job installs outside its own directory. Both are
    # checked here so a name that would overwrite a shipped extract (the
    # scenario names it and every replicate re-imports it) is refused before a
    # job is queued, not after it has already clobbered the map.
    for target, what in (
        (settings.scenarios_dir / f"{name}.yaml", "preset"),
        (extract_path(name, settings), "OSM extract"),
    ):
        if target.exists():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"a {what} named {target.name!r} already exists; onboard this corridor "
                    f"under another name rather than redefining a scenario runs were "
                    f"launched from"
                ),
            )

    # Fixed, server-side names: the two parts share one directory, and a
    # client that names its station table like its detector export (or both
    # the same) must not have one upload land on top of the other.
    upload_dir = settings.uploads_dir / new_id("upl")
    detectors_path = upload_dir / DETECTORS_UPLOAD_NAME
    await _save_upload(detectors, detectors_path, settings.max_upload_bytes)
    stations_path = upload_dir / STATIONS_UPLOAD_NAME
    await _save_upload(stations, stations_path, settings.max_upload_bytes)

    try:
        corridor_id = store.create_corridor(
            name,
            {
                "bbox": bounds,
                "bearing_deg": float(bearing_deg),
                "upstream_station": upstream_station,
                "downstream_station": downstream_station,
                "column_map": columns,
                "idm_calibration": calibration,
                "window_s": float(window_s),
                "t0_local": t0_local,
                "duration_s": float(duration_s),
                "warmup_s": float(warmup_s),
                "source": source,
            },
            detectors_path,
            stations_path,
        )
    except CorridorNameTaken as exc:
        # Nothing was created, so the two uploads this request saved have no
        # row to belong to; they go again rather than accumulate under a name
        # that was refused.
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    queue = get_queue(settings)
    if queue.kind == "inline":
        # The inline queue *is* the caller's thread, and an onboarding is
        # seconds of work (an OSM download, netconvert, a demand fit). Running
        # it inside the request holds the event loop for that long, so
        # ``/healthz`` stops answering and the dashboard declares the server
        # offline while the very request it is waiting on is being served
        # normally. The store row is already ``queued`` (``create_corridor``
        # above), so the response is the same asynchronous contract the Redis
        # queue gives: 202 with a queued row, poll ``GET /corridors/{id}``.
        # Starlette runs a sync background task in a threadpool, after the
        # response has gone out.
        background.add_task(_run_corridor_inline, queue, store, corridor_id, settings)
    else:
        queue.enqueue(
            corridor_onboarding_job,
            corridor_id,
            job_id=corridor_id,
            db_path=str(settings.db_path),
            results_root=str(settings.results_dir),
        )
    row = store.get_corridor(corridor_id)
    assert row is not None
    return _corridor_out(row, settings)


@router.get("/corridors", response_model=list[CorridorRowOut])
def list_corridors(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_CORRIDOR_LIST)] = MAX_CORRIDOR_LIST,
) -> list[CorridorRowOut]:
    """Corridor onboardings newest first, at most ``limit`` (default/max 200).

    The status rows only — ``status``, ``name``, ``scenario_id``,
    ``observations_path``, ``config_hash`` — so a client can pick a corridor
    onboarded in another session (or another browser) and hand its
    ``scenario_id`` to ``POST /runs`` and its ``observations_path`` to
    ``POST /reports``. The summary of what each onboarding discovered belongs
    to the corridor being looked at and is served by
    ``GET /corridors/{id}`` alone.

    A listed corridor is an onboarding this server ran, never a claim that it
    reproduces its road: that is what a report scored against its
    observations answers (CLAUDE.md §0.1).
    """
    settings = _settings(request)
    return [_corridor_row_out(row, settings) for row in _store(request).list_corridors(limit=limit)]


@router.get("/corridors/{corridor_id}", response_model=CorridorOut, responses=_NOT_FOUND_RESPONSE)
def get_corridor(request: Request, corridor_id: str) -> CorridorOut:
    """Status, stage progress and — once done — the onboarding summary.

    The summary is what was *discovered and derived*, never a judgement:
    chain length and lane profile, the ramps found and the method behind each
    ramp's profile, which stations were placed and which were rejected, the
    demand peaks, and the brackets whose flow change no ramp could carry.
    """
    row = _store(request).get_corridor(corridor_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"corridor {corridor_id!r} not found")
    return _corridor_out(row, _settings(request))


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


#: Route prefixes that accept multipart data uploads (the larger body cap):
#: ``POST /calibrations/{kind}`` and ``POST /corridors`` (WP-F, which carries
#: a detector export and a station inventory).
_UPLOAD_PATH_PREFIXES: tuple[str, ...] = (
    f"{router.prefix}/calibrations/",
    f"{router.prefix}/corridors",
)


def _is_upload_path(path: str) -> bool:
    """Whether ``path`` is a multipart upload route (the larger cap)."""
    return path.startswith(_UPLOAD_PATH_PREFIXES)


def _body_cap(path: str, settings: Settings) -> int:
    """Byte ceiling for a request body on ``path``.

    An upload route may carry its data file(s) plus the small form fields
    and multipart framing; everything else is a JSON/YAML document.
    """
    if _is_upload_path(path):
        return settings.max_upload_bytes + settings.max_body_bytes
    return settings.max_body_bytes


def _cap_env_hint(path: str) -> str:
    """The environment variable(s) that set the cap for ``path``."""
    if _is_upload_path(path):
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

    @app.get(
        "/healthz",
        response_model=HealthOut,
        responses={503: {"description": "store or queue unreachable", "model": HealthOut}},
    )
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

    # The security scheme is declared here (not appended to
    # ``router.dependencies`` after construction, which silently does
    # nothing). It documents the header the middleware already enforces.
    app.include_router(router, dependencies=[Security(API_KEY_SCHEME)], responses=_ROUTER_RESPONSES)

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
