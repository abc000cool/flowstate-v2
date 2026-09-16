"""Request/response models (Pydantic v2) for the FlowState API.

Reproducibility rule (CLAUDE.md §8): every run, metrics and heatmap response
carries the ``config_hash`` of the exact configuration that produced it.
Overrides on ``POST /api/v1/runs`` are a deep-merge patch onto the stored
:class:`flowstate_core.config.ScenarioConfig`, re-validated and re-hashed —
the run's hash is the hash of the *effective* config.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Request size caps
# ---------------------------------------------------------------------------
# A sweep grid is a cartesian product and a run is a replicate loop, so both
# are cheap to *ask* for and expensive to execute: without ceilings a single
# accepted request materializes an unbounded number of validated cells and
# enqueues an unbounded number of simulations. These caps are the service's
# hard limits, quoted in the endpoint docstrings so they show up in /docs.

#: Maximum values per sweep axis (penetrations, compliances, controllers).
MAX_SWEEP_AXIS_VALUES = 50

#: Maximum total cells in one sweep grid (penetrations × compliances ×
#: controllers), checked from the list lengths before any cell is built.
MAX_SWEEP_CELLS = 200

#: Maximum replicates a single request may ask for, per run and per sweep cell.
MAX_REPLICATES = 200

#: Ceilings on the calibration fit options a request may set
#: (:class:`CalibrationParams`): bootstrap resamples of the FD fit, and the
#: differential-evolution generation cap and population multiplier of the
#: per-episode IDM fit. The defaults (200, 60, 15) sit well inside them.
MAX_N_BOOTSTRAP = 5000
MAX_DE_MAXITER = 500
MAX_DE_POPSIZE = 100


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``patch`` onto ``base`` (dicts merge, rest replaces).

    Lists and scalars are replaced wholesale; ``None`` in the patch
    explicitly overwrites (e.g. clearing a controller). Neither input is
    mutated.
    """
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


class ScenarioOut(BaseModel):
    scenario_id: str
    name: str
    config_hash: str
    created_at: str
    config: dict[str, Any]


class PresetOut(BaseModel):
    """A repo scenario YAML offered as a selectable preset."""

    name: str
    filename: str
    config_hash: str
    config: dict[str, Any]


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """Unknown fields are a client bug (a mis-named key silently dropped once
    sent every dashboard sweep without its controller); refuse them."""

    scenario_id: str
    overrides: dict[str, Any] = Field(default_factory=dict)
    """Deep-merge patch onto the stored ScenarioConfig (re-validated)."""
    replicates: int | None = Field(default=None, ge=1, le=MAX_REPLICATES)
    """Replicates for this run; capped at ``MAX_REPLICATES`` (200)."""
    tier: Literal["micro", "macro"] | None = None


class ProgressOut(BaseModel):
    completed_replicates: int
    total_replicates: int


class RunOut(BaseModel):
    run_id: str
    scenario_id: str | None
    sweep_id: str | None
    status: Literal["queued", "running", "done", "failed"]
    tier: Literal["micro", "macro"]
    config_hash: str
    seeded: bool
    progress: ProgressOut
    seeds: list[int]
    error: str | None = None
    error_kind: str | None = None
    created_at: str


class CIOut(BaseModel):
    """t-distribution replicate CI; ``underpowered`` when n < 20 (§0.6)."""

    mean: float | None
    lo95: float | None
    hi95: float | None
    n: int
    underpowered: bool


class ReplicateMetricsOut(BaseModel):
    seed: int
    metrics: dict[str, float | int | None]


class MetricsOut(BaseModel):
    run_id: str
    config_hash: str
    tier: Literal["micro", "macro"]
    seeded: bool
    n_replicates: int
    underpowered: bool
    """True when the replicate count is below the headline minimum of 20 —
    such values must not be quoted as headline results (CLAUDE.md §0.6)."""
    replicates: list[ReplicateMetricsOut]
    aggregate: dict[str, CIOut]


class HeatmapOut(BaseModel):
    run_id: str
    config_hash: str
    seed: int
    field: Literal["speed", "density"]
    tier: Literal["micro", "macro"]
    t_bins: list[float]
    """Time bin centers [s]."""
    x_bins: list[float]
    """Space bin centers [m]."""
    values: list[list[float | None]]
    """Row-major ``[t][x]``; null = no vehicles in bin."""


# ---------------------------------------------------------------------------
# Sweeps
# ---------------------------------------------------------------------------


#: The no-AV reference cell appended per controller by ``include_baseline``.
BASELINE_PENETRATION = 0.0
BASELINE_COMPLIANCE = 1.0

#: One grid cell: (penetration, compliance, controller).
SweepCellKey = tuple[float, float, str | None]


class SweepCreateRequest(BaseModel):
    """Penetration × compliance × controller grid over one scenario.

    Bounded on both axes and in total: each list holds at most
    ``MAX_SWEEP_AXIS_VALUES`` (50) values and their product — plus the
    baseline cells ``include_baseline`` adds — may not exceed
    ``MAX_SWEEP_CELLS`` (200) cells. The total is checked here, from the
    list lengths alone, so an oversized grid is rejected before a single cell
    config is built or validated.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    penetrations: list[float] = Field(min_length=1, max_length=MAX_SWEEP_AXIS_VALUES)
    compliances: list[float] = Field(min_length=1, max_length=MAX_SWEEP_AXIS_VALUES)
    controllers: list[str | None] = Field(
        default_factory=lambda: [None], min_length=1, max_length=MAX_SWEEP_AXIS_VALUES
    )
    overrides: dict[str, Any] = Field(default_factory=dict)
    """Applied to every cell before the grid values (deep merge)."""
    replicates: int | None = Field(default=None, ge=1, le=MAX_REPLICATES)
    """Replicates per cell; capped at ``MAX_REPLICATES`` (200)."""
    tier: Literal["micro", "macro"] | None = None
    include_baseline: bool = False
    """Append one no-AV reference cell (penetration 0, compliance 1) per
    distinct controller, so a "Δ vs baseline" comparison has an uncontrolled
    run to compare against instead of the smallest controlled cell. Skipped
    when ``penetrations`` already contains 0 (the grid then holds no-AV cells
    of its own). Baseline cells count toward ``MAX_SWEEP_CELLS``."""

    def baseline_cells(self) -> list[SweepCellKey]:
        """The ``include_baseline`` cells to append after the cartesian grid."""
        if not self.include_baseline or BASELINE_PENETRATION in self.penetrations:
            return []
        return [
            (BASELINE_PENETRATION, BASELINE_COMPLIANCE, ctrl)
            for ctrl in dict.fromkeys(self.controllers)
        ]

    def grid_cells(self) -> list[SweepCellKey]:
        """Every cell of the sweep in fan-out order: the product, then baselines."""
        product = [
            (pen, comp, ctrl)
            for pen in self.penetrations
            for comp in self.compliances
            for ctrl in self.controllers
        ]
        return product + self.baseline_cells()

    @model_validator(mode="after")
    def _check_grid_size(self) -> Self:
        product = len(self.penetrations) * len(self.compliances) * len(self.controllers)
        baselines = len(self.baseline_cells())
        cells = product + baselines
        if cells > MAX_SWEEP_CELLS:
            extra = f" + {baselines} baseline cells" if baselines else ""
            raise ValueError(
                f"sweep grid is {len(self.penetrations)} penetrations × "
                f"{len(self.compliances)} compliances × {len(self.controllers)} controllers"
                f"{extra} = {cells} cells, over the limit of {MAX_SWEEP_CELLS}; "
                f"split the grid across several sweeps"
            )
        return self


class SweepCellOut(BaseModel):
    penetration: float
    compliance: float
    controller: str | None
    config_hash: str
    run_id: str | None = None
    status: str | None = None
    progress: ProgressOut | None = None
    aggregate: dict[str, CIOut] | None = None
    """Replicate-aggregated metrics once the cell's run is done."""


class SweepOut(BaseModel):
    sweep_id: str
    scenario_id: str | None
    status: Literal["queued", "running", "done", "failed"]
    error: str | None = None
    created_at: str
    runs_total: int
    runs_done: int
    runs_failed: int
    cells: list[SweepCellOut]


# ---------------------------------------------------------------------------
# Calibrations
# ---------------------------------------------------------------------------


class CalibrationParams(BaseModel, extra="forbid"):
    """Fit options accepted in the ``params`` form field of ``POST /calibrations``.

    Every option is bounded because the fit runs on the single worker under
    a six-hour job timeout: an unbounded ``n_bootstrap`` pre-allocates that
    many resample index arrays and refits the FD that many times, and an
    unbounded ``de_maxiter``/``de_popsize`` pins the worker on one episode.
    Unknown keys are refused (HTTP 422) rather than silently ignored, so a
    misspelt option never runs a fit with defaults the caller did not want.

    Only the keys the caller sets are forwarded to the fit
    (:meth:`forwarded`); everything else keeps the fit function's own
    default, and an explicit ``null`` means "unset". Which keys each kind of
    fit consumes is listed on ``api.jobs.fd_calibration_job`` and
    ``api.jobs.idm_calibration_job``.
    """

    # Shared
    seed: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=4000)
    # FD fit (``calibration.fd_fit.fit_triangular_fd``)
    loader: Literal["tidy", "pems"] | None = None
    n_bootstrap: int | None = Field(default=None, ge=0, le=MAX_N_BOOTSTRAP)
    min_points: int | None = Field(default=None, ge=3)
    congested_quantile: float | None = Field(default=None, gt=0.0, lt=1.0)
    q_max_percentile: float | None = Field(default=None, gt=0.0, le=100.0)
    uncongested_max_density: float | None = Field(default=None, gt=0.0)
    """Free-branch density cut [veh/m]."""
    uncongested_max_occupancy: float | None = Field(default=None, gt=0.0, le=1.0)
    # PeMS loader (``loader: "pems"``)
    g_effective_length_m: float | None = Field(default=None, gt=0.0)
    interval_s: float | None = Field(default=None, gt=0.0)
    speed_unit: Literal["mph", "kmh", "ms"] | None = None
    occupancy_unit: Literal["fraction", "percent"] | None = None
    # IDM fit (``calibration.idm_fit.fit_population``)
    min_duration_s: float | None = Field(default=None, gt=0.0, le=3600.0)
    holdout_frac: float | None = Field(default=None, ge=0.0, lt=1.0)
    trim_quantile: float | None = Field(default=None, gt=0.0, le=1.0)
    de_maxiter: int | None = Field(default=None, ge=1, le=MAX_DE_MAXITER)
    de_popsize: int | None = Field(default=None, ge=1, le=MAX_DE_POPSIZE)
    de_tol: float | None = Field(default=None, gt=0.0)

    def forwarded(self) -> dict[str, Any]:
        """The caller-set, non-null options — what the job forwards to the fit."""
        return self.model_dump(exclude_unset=True, exclude_none=True)


class CalibrationOut(BaseModel):
    calibration_id: str
    kind: Literal["fd", "idm"]
    status: Literal["queued", "running", "done", "failed"]
    data_path: str
    source: str
    artifact_path: str | None = None
    error: str | None = None
    created_at: str
    artifact: dict[str, Any] | None = None
    """Parsed artifact JSON when the fit is done (docs/CONTRACTS.md §5)."""


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


class ReportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """Unknown fields are a client bug (a mis-named key silently dropped once
    sent every dashboard sweep without its controller); refuse them."""

    run_ids: list[str] = Field(min_length=1)
    title: str = "FlowState calibration & validation report"


class ReportOut(BaseModel):
    report_id: str
    status: Literal["queued", "running", "done", "failed"]
    run_ids: list[str]
    title: str
    report_path: str | None = None
    error: str | None = None
    error_kind: str | None = None
    created_at: str


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    store: str
    queue: str
    queue_kind: str
