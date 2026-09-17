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

#: Maximum (and default) rows returned by ``GET /api/v1/reports``. Report rows
#: are small metadata records, so the whole recent history fits in one
#: response; the cap keeps a service with thousands of reports from serializing
#: all of them into a dashboard poll.
MAX_REPORT_LIST = 200

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
    preset: Literal[False] = False
    """Always False — the marker that separates a stored (user-posted)
    scenario from a shipped :class:`PresetOut`. Both lists carry the field so
    a client that merges them can tell one from the other; a stored scenario
    created *from* a preset is still a user scenario (it has an id, a
    ``created_at`` and may have been edited)."""


class PresetOut(BaseModel):
    """A repo scenario YAML offered as a selectable preset."""

    name: str
    filename: str
    config_hash: str
    config: dict[str, Any]
    preset: Literal[True] = True
    """Always True; see :attr:`ScenarioOut.preset`. A preset has no
    ``scenario_id`` — ``POST /scenarios`` with its ``config`` creates one."""


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


#: ``CIOut.reason`` when no replicate produced a value for the metric.
NO_OBSERVATIONS = "no_observations"


class CIOut(BaseModel):
    """t-distribution replicate CI over the replicates that produced a value.

    ``n`` counts the replicates with a finite value for this metric, which is
    not always the run's replicate count: ``wave_speed_kmh`` has no value in a
    replicate where no wave was detected, ``fuel_ml_per_veh_km`` none where no
    emission model ran.

    Three distinct states, so a client never renders an interval that does not
    exist:

    - ``n == 0`` — no replicate produced this metric. ``mean``, ``lo95`` and
      ``hi95`` are null, ``reason`` is :data:`NO_OBSERVATIONS` and
      ``underpowered`` is **False**: there is no estimate to be underpowered
      about, and more seeds are not necessarily what is missing (no waves
      detected is an answer, not a sample-size problem). Render the metric as
      absent.
    - ``0 < n < 20`` — an estimate exists but is below the headline minimum
      (CLAUDE.md §0.6): ``underpowered`` is True and the value must not be
      quoted as a headline result. ``lo95``/``hi95`` are null at ``n == 1``
      (no dispersion from a single value).
    - ``n >= 20`` — a headline-quotable estimate; ``underpowered`` False,
      ``reason`` null.
    """

    mean: float | None
    lo95: float | None
    hi95: float | None
    n: int
    underpowered: bool
    reason: Literal["no_observations"] | None = None
    """Why there is no estimate, when there is none (``n == 0``); null
    otherwise."""


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


#: The single no-AV reference cell appended by ``include_baseline``.
BASELINE_PENETRATION = 0.0
BASELINE_COMPLIANCE = 1.0
#: Its controller: none. At penetration 0 no vehicle is ever tagged
#: (``microsim.vehicles``: ``n_avs = round(penetration * n)``), so the
#: controller name is inert simulation input — see :meth:`baseline_cells`.
BASELINE_CONTROLLER: str | None = None

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
    """Append *one* uncontrolled reference cell (penetration 0, compliance 1,
    no controller) after the grid, so a "Δ vs baseline" comparison has an
    uncontrolled run to compare against instead of the smallest controlled
    cell. Skipped when the grid already holds an uncontrolled cell — either
    ``penetrations`` contains 0 or ``controllers`` contains ``null``. The
    baseline cell counts toward ``MAX_SWEEP_CELLS``."""

    def baseline_cells(self) -> list[SweepCellKey]:
        """The single uncontrolled reference cell ``include_baseline`` appends.

        One cell, not one per controller. At penetration 0 no vehicle is
        tagged as an AV (``microsim.vehicles``: ``n_avs = round(penetration *
        n)``) and the controller function is dispatched per AV only, so a
        per-controller baseline would run *k* bit-identical simulations —
        k−1 wasted replicate sets and k−1 cells of the ceiling. Worse, the
        cells hash differently (``av.controller`` survives the
        exclude-defaults hash payload), so ``validation.report`` sees k
        groups labelled ``baseline``, and its
        ``baselines[0] if len(baselines) == 1 else None`` then drops the
        controller-minus-baseline contrast table and the seed-matched
        contour pairs entirely.

        Returns nothing when the grid already contains an uncontrolled cell:
        penetration 0 in ``penetrations``, or ``None`` in ``controllers``
        (``validation.report.group_label`` labels *any* cell with no
        controller ``baseline``, whatever its penetration, so appending a
        second one would re-create the same two-baseline tie).
        """
        if (
            not self.include_baseline
            or BASELINE_PENETRATION in self.penetrations
            or None in self.controllers
        ):
            return []
        return [(BASELINE_PENETRATION, BASELINE_COMPLIANCE, BASELINE_CONTROLLER)]

    def grid_cells(self) -> list[SweepCellKey]:
        """Every cell of the sweep in fan-out order: the product, then baselines.

        Repeated axis values collapse: two identical ``(penetration,
        compliance, controller)`` triples produce the same effective config
        and the same ``config_hash``, so they would be two run rows writing
        the same ``runs/<hash>/<seed>/`` tree — duplicate simulation for one
        result. First occurrence wins, so fan-out order is unchanged.
        """
        product = [
            (pen, comp, ctrl)
            for pen in self.penetrations
            for comp in self.compliances
            for ctrl in self.controllers
        ]
        return list(dict.fromkeys(product + self.baseline_cells()))

    @model_validator(mode="after")
    def _check_grid_size(self) -> Self:
        # The ceiling is checked from the list lengths (before any cell is
        # built), so it counts the *requested* product — de-duplication in
        # grid_cells() can only make the realized grid smaller.
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


#: Acceptance-criteria profile used when a report request names none.
DEFAULT_CRITERIA_PROFILE = "fhwa_default"


def criteria_profile_names() -> tuple[str, ...]:
    """Selectable ``validation.criteria`` profile names (registry order)."""
    from validation.criteria import CRITERIA_PROFILES

    return tuple(CRITERIA_PROFILES)


class ReportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """Unknown fields are a client bug (a mis-named key silently dropped once
    sent every dashboard sweep without its controller); refuse them."""

    run_ids: list[str] = Field(min_length=1)
    title: str = "FlowState calibration & validation report"
    profile: str = DEFAULT_CRITERIA_PROFILE
    """Acceptance-criteria profile the report is scored against — a name from
    ``GET /api/v1/criteria`` (``validation.criteria.CRITERIA_PROFILES``: the
    FlowState default, the 2004 FHWA toolbox table, and the ODOT VISSIM 2011
    and TxDOT TSAP ch. 13 state-DOT protocols). Unknown names are refused
    with HTTP 422; the profile is recorded on the report row and printed in
    the report header.

    Only the *thresholds* are selectable. The measurements they are scored
    against are computed from the run artifacts, never accepted from the
    request: a criterion whose evidence this service cannot compute (GEH
    against observed link counts, segment-speed RMSPE against an observed
    field, the ring benchmarks, the sensitivity grid) is reported as **not
    evaluated**, never as a number the caller typed (CLAUDE.md §7.4)."""

    @model_validator(mode="after")
    def _check_profile(self) -> Self:
        names = criteria_profile_names()
        if self.profile not in names:
            raise ValueError(
                f"unknown criteria profile {self.profile!r}; available: {list(names)} "
                f"(GET /api/v1/criteria)"
            )
        return self


class CriteriaProfileOut(BaseModel):
    """One selectable acceptance-criteria profile (``GET /criteria``)."""

    name: str
    source: str
    """Which document, section and table the numbers were transcribed from,
    what was verified and which rows are FlowState's own conventions — read
    this before quoting the profile (``validation.criteria``)."""
    geh_threshold: float
    """Per-comparison GEH bound (strict ``<``)."""
    geh_pass_fraction: float
    """Share of link-hour comparisons that must satisfy the bound."""
    geh_pass_inclusive: bool
    """``>=`` the share (True) or strictly ``>`` it (the 2004 table's wording)."""
    rmspe_max: float | None
    """Segment-speed RMSPE bound as a fraction; null when the profile's
    source defines none (the row is then not produced at all)."""
    wave_speed_band_kmh: tuple[float, float]
    min_seeds: int
    require_ring_emergence: bool
    require_ring_dampening: bool
    require_sensitivity_grid: bool
    wave_detector: str
    """Name of the wave detector whose recipe the wave-speed row is scored
    against; a value measured with another detector is not evaluated."""
    default: bool
    """True for the profile used when a report request names none."""


class ReportOut(BaseModel):
    report_id: str
    status: Literal["queued", "running", "done", "failed"]
    run_ids: list[str]
    title: str
    profile: str = DEFAULT_CRITERIA_PROFILE
    """The acceptance-criteria profile the report was scored against."""
    report_path: str | None = None
    """The bundle's markdown file *relative to the server's results root*
    (``reports/<report_id>/report.md``) — an identifier for the bundle, not a
    URL and not the server's filesystem layout. Fetch the content from
    ``/reports/{id}/markdown``, ``/pdf`` or ``/archive``."""
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
