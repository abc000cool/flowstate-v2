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

from flowstate_core.config import MacroOptions
from flowstate_core.strategies import Strategy, needs_target

# ---------------------------------------------------------------------------
# Request size caps
# ---------------------------------------------------------------------------
# A sweep grid is a cartesian product and a run is a replicate loop, so both
# are cheap to *ask* for and expensive to execute: without ceilings a single
# accepted request materializes an unbounded number of validated cells and
# enqueues an unbounded number of simulations. These caps are the service's
# hard limits, quoted in the endpoint docstrings so they show up in /docs.

#: Maximum values per sweep axis (penetrations, compliances, controllers,
#: strategies).
MAX_SWEEP_AXIS_VALUES = 50

#: Maximum total cells in one sweep grid (penetrations × compliances ×
#: controllers × strategies), checked from the list lengths before any cell
#: is built.
MAX_SWEEP_CELLS = 200

#: Maximum replicates a single request may ask for, per run and per sweep cell.
MAX_REPLICATES = 200

#: Maximum (and default) rows returned by ``GET /api/v1/reports``. Report rows
#: are small metadata records, so the whole recent history fits in one
#: response; the cap keeps a service with thousands of reports from serializing
#: all of them into a dashboard poll.
MAX_REPORT_LIST = 200

#: Maximum (and default) rows returned by ``GET /api/v1/corridors``. Corridor
#: rows are the same kind of small metadata record as report rows, and the
#: summary — the bulky part — is served only by ``GET /corridors/{id}``.
MAX_CORRIDOR_LIST = 200

#: Ceilings on the calibration fit options a request may set
#: (:class:`CalibrationParams`): bootstrap resamples of the FD fit, and the
#: differential-evolution generation cap and population multiplier of the
#: per-episode IDM fit. The defaults (200, 60, 15) sit well inside them.
MAX_N_BOOTSTRAP = 5000
MAX_DE_MAXITER = 500
MAX_DE_POPSIZE = 100

#: Floor on ``CalibrationParams.max_fit_rows``: the FD fit needs a population
#: of detector intervals, and a cap below this turns a corridor calibration
#: into a fit of a handful of points without saying so. ``null`` (no cap) is
#: the documented way to fit every row.
MIN_MAX_FIT_ROWS = 1000

#: Ceiling on ``CalibrationParams.max_speed_ratio_factor``. The PeMS loader
#: cross-checks the implied speed ``q/ρ`` against the reported speed within
#: that factor (the loader's own default is 2.0); a factor this large already
#: tolerates any unit mistake the guard exists to catch, so nothing above it
#: is worth accepting.
SPEED_RATIO_FACTOR_CEILING = 100.0

#: Ceilings on the detector-observation options of :class:`CalibrationParams`.
#: They bound what one request can ask the worker to materialise: the window
#: grid of an observations artifact is ``duration_s / window_s`` entries per
#: station, so an unbounded span with a small window is an unbounded artifact.
#: A day of 30-second windows (2880) sits inside all three.
MAX_COLUMN_MAP_ENTRIES = 16
MAX_WINDOW_S = 3600.0
MAX_OBSERVED_DURATION_S = 86400.0
MAX_STATIONS = 500


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
    macro: MacroOptions | None = None
    """Macro-tier (CTM screening) solver options for this run: cell length
    ``dx_m`` and moving-bottleneck discretization ``bottleneck_variant``
    (``flux_cap`` | ``capacity``, CLAUDE.md §5.5). Merged into the effective
    config like ``tier``/``replicates``, so it is hashed with the run and
    echoed in every replicate's ``meta.json["macro_options"]``. Unknown keys
    are refused (the model forbids extras); an unknown variant is a 422."""


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


class RampMeterDiagnosticsOut(BaseModel):
    """One ramp meter's counters from ``meta.json["ramp_meters"][i]``.

    ``n_passed_unstoppable`` (2026-09-24) counts ramp vehicles that were
    already too close to the stop line to brake for it when first seen on the
    ramp and so passed the meter uncounted by its release logic; a large share
    against ``n_released`` means the stop line sits too near the ramp's end
    for the entry speeds (docs/LESSONS.md row 31).
    """

    ramp: str
    controller: str
    edge: str
    interval_s: float
    n_released: int
    n_passed_unstoppable: int = 0
    n_rate_updates: int = 0
    """Length of the meter's ``rates`` log — how many times the rate was set."""


class WeaveSectionDiagnosticsOut(BaseModel):
    """One weaving section's counters from ``meta.json["weave_sections"][i]``
    (``microsim.runner._weave_meta``; docs/CONTRACTS.md §2, weaving sections).

    ``n_entered`` vehicles were taken under control; each left it as
    ``n_changed_in`` / ``n_changed_out`` (its change made), ``n_missed`` (left
    the section still owing its change) or is still under control at the end
    (``n_unfinished``). ``n_forced`` completed changes needed the forced mode;
    ``n_forced_deferred`` is in vehicle-steps, not vehicles. ``n_exited``
    against ``n_departed_exiting`` says how many of the vehicles routed through
    the exit were seen on it.
    """

    ramp: str
    exit: str
    length_m: float | None = None
    n_entered: int
    n_changed_in: int
    n_changed_out: int
    n_exited: int
    n_departed_exiting: int
    n_forced: int
    n_forced_deferred: int
    n_missed: int
    n_unfinished: int
    wait_s_mean: float | None = None
    wait_in_s_mean: float | None = None
    wait_out_s_mean: float | None = None


class MergeDiagnosticsOut(BaseModel):
    """Ramp-meter and weaving-section counters of one replicate (2026-09-24).

    Read from the first replicate's ``meta.json`` (``seed`` names it) so a
    tester sees them without opening the run directory. They are one seed's
    counters, not a replicate aggregate, and describe how the merge models
    behaved — never a corridor result.
    """

    seed: int
    ramp_meters: list[RampMeterDiagnosticsOut] = Field(default_factory=list)
    weave_sections: list[WeaveSectionDiagnosticsOut] = Field(default_factory=list)


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
    fd_source: str | None = None
    """Where the macro (screening) tier's fundamental diagram came from, as
    the run's own ``meta.json["fd"]["source"]`` records it: the path of the
    ``FDCalibration`` artifact the config named, or ``"v1_legacy preset"``
    for the documented *uncalibrated* default (CLAUDE.md §5.1). ``None`` on
    micro-tier runs, which have no fundamental diagram, and on runs written
    before the field existed — a screening number quoted without it is a
    number whose diagram's provenance is unknown."""
    merge_diagnostics: MergeDiagnosticsOut | None = None
    """Ramp-meter and weaving-section counters of the first replicate
    (2026-09-24); ``None`` when the run has neither (every ring and plain
    corridor run), or when its meta cannot be read."""


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
#: Its strategy: the scenario as calibrated, no infrastructure deployed.
BASELINE_STRATEGY: Strategy = "none"

#: One grid cell: (penetration, compliance, controller, strategy).
SweepCellKey = tuple[float, float, str | None, Strategy]


class AlineaOptions(BaseModel):
    """Metering target of a sweep's ``alinea``/``vsl+alinea`` cells.

    ALINEA has no built-in target (CLAUDE.md §0.1: no uncalibrated
    constants), so a metered sweep either states it here or inherits it from
    the scenario's ``fd_calibration`` artifact (``fd.rho_c``); with neither,
    ``POST /sweeps`` answers 422 rather than inventing a critical density.
    """

    model_config = ConfigDict(extra="forbid")

    rho_target_veh_km: float = Field(gt=0.0)
    """Per-lane critical density [veh/km] written into every on-ramp meter."""


class SweepCreateRequest(BaseModel):
    """Penetration × compliance × controller × strategy grid over one scenario.

    The first three axes are Lagrangian (what the controlled vehicles do);
    ``strategies`` is the infrastructure axis (what the operator deploys —
    VSL gantries, ALINEA ramp metering, both or neither), applied by
    :func:`flowstate_core.strategies.apply_strategy`, the same patch
    ``scripts/corridor_sweep.py`` uses, so a CLI cell and an API cell of the
    same grid point carry the same ``config_hash``.

    Bounded on every axis and in total: each list holds at most
    ``MAX_SWEEP_AXIS_VALUES`` (50) values and their product — plus the
    infrastructure-only and baseline cells described on :meth:`grid_cells` —
    may not exceed ``MAX_SWEEP_CELLS`` (200) cells. The total is checked
    here, from the list lengths alone, so an oversized grid is rejected
    before a single cell config is built or validated.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    penetrations: list[float] = Field(min_length=1, max_length=MAX_SWEEP_AXIS_VALUES)
    compliances: list[float] = Field(min_length=1, max_length=MAX_SWEEP_AXIS_VALUES)
    controllers: list[str | None] = Field(
        default_factory=lambda: [None], min_length=1, max_length=MAX_SWEEP_AXIS_VALUES
    )
    strategies: list[Strategy] = Field(
        default_factory=lambda: [BASELINE_STRATEGY],
        min_length=1,
        max_length=MAX_SWEEP_AXIS_VALUES,
    )
    """Infrastructure strategies fanned out over the Lagrangian grid
    (:data:`flowstate_core.strategies.STRATEGIES`). The default runs the
    scenario as calibrated."""
    alinea: AlineaOptions | None = None
    """Metering target of the ALINEA strategies; ``None`` reads it from the
    scenario's ``fd_calibration`` artifact (:class:`AlineaOptions`)."""
    overrides: dict[str, Any] = Field(default_factory=dict)
    """Applied to every cell before the grid values (deep merge)."""
    replicates: int | None = Field(default=None, ge=1, le=MAX_REPLICATES)
    """Replicates per cell; capped at ``MAX_REPLICATES`` (200)."""
    tier: Literal["micro", "macro"] | None = None
    macro: MacroOptions | None = None
    """Macro-tier solver options applied to every cell before the grid values
    (see :attr:`RunCreateRequest.macro`); they enter each cell's effective
    config and therefore its ``config_hash``."""
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

        Returns nothing when the grid already contains an uncontrolled cell
        *with no strategy*: ``none`` among ``strategies`` and either
        penetration 0 in ``penetrations`` or ``None`` in ``controllers``
        (``validation.report.group_label`` labels *any* cell with no
        controller ``baseline``, whatever its penetration, so appending a
        second one would re-create the same two-baseline tie). An
        uncontrolled cell that deploys VSL or metering is not a baseline —
        the label names the strategy — so it does not suppress this one.
        """
        uncontrolled_in_grid = BASELINE_STRATEGY in self.strategies and (
            BASELINE_PENETRATION in self.penetrations or None in self.controllers
        )
        if not self.include_baseline or uncontrolled_in_grid:
            return []
        return [
            (
                BASELINE_PENETRATION,
                BASELINE_COMPLIANCE,
                BASELINE_CONTROLLER,
                BASELINE_STRATEGY,
            )
        ]

    def strategy_cells(self) -> list[SweepCellKey]:
        """One no-AV cell per requested infrastructure strategy.

        VSL and ramp metering act on every vehicle whether or not any is
        controlled, so each strategy is also its own configuration: without
        these cells a grid could only price "controller *and* strategy"
        against the bare baseline, never the strategy alone — and the two
        levers are bought by different budgets. The mirror of
        ``scripts/corridor_sweep.py``'s ``strategy_<s>`` cells.

        ``none`` yields nothing (that cell is the baseline), and a strategy
        listed twice yields one cell.
        """
        return [
            (BASELINE_PENETRATION, BASELINE_COMPLIANCE, BASELINE_CONTROLLER, strategy)
            for strategy in dict.fromkeys(self.strategies)
            if strategy != BASELINE_STRATEGY
        ]

    def needs_alinea_target(self) -> bool:
        """Whether any requested strategy meters ramps and so needs a target."""
        return any(needs_target(s) for s in self.strategies)

    def grid_cells(self) -> list[SweepCellKey]:
        """Every cell in fan-out order: product, strategy-only cells, baseline.

        Repeated axis values collapse: two identical ``(penetration,
        compliance, controller, strategy)`` tuples produce the same effective
        config and the same ``config_hash``, so they would be two run rows
        writing the same ``runs/<hash>/<seed>/`` tree — duplicate simulation
        for one result. First occurrence wins, so fan-out order is unchanged
        (a strategy-only cell already in the product is not repeated).
        """
        product = [
            (pen, comp, ctrl, strategy)
            for strategy in self.strategies
            for pen in self.penetrations
            for comp in self.compliances
            for ctrl in self.controllers
        ]
        return list(dict.fromkeys(product + self.strategy_cells() + self.baseline_cells()))

    @model_validator(mode="after")
    def _check_grid_size(self) -> Self:
        # The ceiling is checked from the list lengths (before any cell is
        # built), so it counts the *requested* product — de-duplication in
        # grid_cells() can only make the realized grid smaller.
        product = (
            len(self.penetrations)
            * len(self.compliances)
            * len(self.controllers)
            * len(self.strategies)
        )
        infra = len(self.strategy_cells())
        baselines = len(self.baseline_cells())
        cells = product + infra + baselines
        if cells > MAX_SWEEP_CELLS:
            extra = f" + {infra} infrastructure-only cells" if infra else ""
            extra += f" + {baselines} baseline cells" if baselines else ""
            raise ValueError(
                f"sweep grid is {len(self.penetrations)} penetrations × "
                f"{len(self.compliances)} compliances × {len(self.controllers)} controllers"
                f" × {len(self.strategies)} strategies"
                f"{extra} = {cells} cells, over the limit of {MAX_SWEEP_CELLS}; "
                f"split the grid across several sweeps"
            )
        return self


class SweepCellOut(BaseModel):
    penetration: float
    compliance: float
    controller: str | None
    strategy: Strategy = BASELINE_STRATEGY
    """Infrastructure strategy of this cell
    (:data:`flowstate_core.strategies.STRATEGIES`); ``none`` on a sweep
    created before the axis existed."""
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
    tier: Literal["micro", "macro"] | None = None
    """Tier every cell of the grid runs on, read from the stored cell configs
    (they are built from one base config, so a sweep is single-tier).
    ``macro`` means the whole matrix is screening-tier output and may not be
    read as a validation result (CLAUDE.md §5.6); ``None`` only for a sweep
    with no cells."""
    error: str | None = None
    created_at: str
    runs_total: int
    runs_done: int
    runs_failed: int
    cells: list[SweepCellOut]


# ---------------------------------------------------------------------------
# Calibrations
# ---------------------------------------------------------------------------


#: Calibration options whose explicit ``null`` is a *setting*, not "unset":
#: ``max_fit_rows: null`` fits every usable row (no subsample cap). Every
#: other null in :class:`CalibrationParams` leaves the fit's own default.
NULL_IS_A_VALUE: frozenset[str] = frozenset({"max_fit_rows"})


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
    default, and an explicit ``null`` means "unset" — except for the keys in
    :data:`NULL_IS_A_VALUE`, where ``null`` is itself the option the fit
    takes. Which keys each kind of fit consumes is listed on
    ``api.jobs.fd_calibration_job`` and ``api.jobs.idm_calibration_job``.
    """

    # Shared
    seed: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=4000)
    # FD fit (``calibration.fd_fit.fit_triangular_fd``)
    loader: Literal["tidy", "pems", "detector_csv"] | None = None
    n_bootstrap: int | None = Field(default=None, ge=0, le=MAX_N_BOOTSTRAP)
    min_points: int | None = Field(default=None, ge=3)
    congested_quantile: float | None = Field(default=None, gt=0.0, lt=1.0)
    q_max_percentile: float | None = Field(default=None, gt=0.0, le=100.0)
    uncongested_max_density: float | None = Field(default=None, gt=0.0)
    """Free-branch density cut [veh/m]."""
    uncongested_max_occupancy: float | None = Field(default=None, gt=0.0, le=1.0)
    max_fit_rows: int | None = Field(default=None, ge=MIN_MAX_FIT_ROWS)
    """Cap on the rows fitted; above it the fit draws a seeded subsample.
    Explicit ``null`` fits **every** usable row (see :data:`NULL_IS_A_VALUE`),
    at the cost of a congested-branch LP that is quadratic in rows — the one
    option here whose unbounded setting is a deliberate, documented choice.
    The floor keeps a cap from silently shrinking the fit to a handful of
    points; ``calibration.fd_fit`` additionally requires
    ``max_fit_rows >= 2 * min_points``."""
    max_dropped_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    """Share of non-physical input rows tolerated before the fit is refused."""
    # PeMS loader (``loader: "pems"``)
    g_effective_length_m: float | None = Field(default=None, gt=0.0)
    interval_s: float | None = Field(default=None, gt=0.0)
    speed_unit: Literal["mph", "kmh", "ms"] | None = None
    occupancy_unit: Literal["fraction", "percent", "pct"] | None = None
    """Occupancy unit of the input column. ``"pct"`` is the detector-CSV
    spelling of ``"percent"`` and is accepted for both loaders (the PeMS
    loader is given the spelling it knows — ``api.jobs.pems_loader_kwargs``)."""
    max_speed_ratio_factor: float | None = Field(
        default=None, gt=1.0, le=SPEED_RATIO_FACTOR_CEILING
    )
    """Tolerated factor between the implied speed ``q/ρ`` and the reported
    speed. ``null`` means "unset" (the loader's own default): the loader can
    be told ``None`` to switch the cross-check off entirely, but that is a
    data-hygiene guard and the API does not expose a way to disable it."""
    max_out_of_range_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    """Share of occupancy rows allowed above 100% before the load is refused."""
    # Detector CSV loader (``loader: "detector_csv"``, and every demand job)
    column_map: dict[str, str] | None = Field(default=None, max_length=MAX_COLUMN_MAP_ENTRIES)
    """Canonical field → the uploaded file's own column name, for
    ``calibration.loaders.detector_csv.load_detector_csv`` (fields:
    ``timestamp, station, flow, occupancy, speed, lanes, kind, x_m``). An
    unknown field name is refused by the loader, naming it."""
    kind_default: Literal["mainline", "on_ramp", "off_ramp"] | None = None
    """``kind`` for rows in a file that carries no ``kind`` column."""
    # Demand / observations job (``POST /calibrations/demand``)
    window_s: float | None = Field(default=None, gt=0.0, le=MAX_WINDOW_S)
    """Analysis window [s] of the uploaded detector frame (must equal its
    own interval)."""
    t0_local: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    """Local wall-clock time of simulation t=0 (``"HH:MM"``)."""
    duration_s: float | None = Field(default=None, gt=0.0, le=MAX_OBSERVED_DURATION_S)
    """Analysed span [s]; a whole multiple of ``window_s``."""
    upstream_station: str | None = Field(default=None, max_length=200)
    """Station id whose observed flow becomes the corridor inflow."""
    stations: list[dict[str, Any]] | None = Field(default=None, max_length=MAX_STATIONS)
    """Optional stations table (``station``/``id``, ``label``, ``lat``,
    ``lon``, ``x_m``, ``lanes``, ``kind``, ``speed_limit_ms``); without it the
    station metadata is taken from the uploaded frame."""
    ramps: list[dict[str, Any]] | None = Field(default=None, max_length=MAX_STATIONS)
    """Optional ramp descriptors (``name``, ``kind``, ``x_m``, optional
    ``station``) turned into the demand artifact's ramp profiles."""
    corridor: str | None = Field(default=None, max_length=200)
    """Corridor name recorded on the observations artifact."""
    # IDM fit (``calibration.idm_fit.fit_population``)
    min_duration_s: float | None = Field(default=None, gt=0.0, le=3600.0)
    holdout_frac: float | None = Field(default=None, ge=0.0, lt=1.0)
    trim_quantile: float | None = Field(default=None, gt=0.0, le=1.0)
    de_maxiter: int | None = Field(default=None, ge=1, le=MAX_DE_MAXITER)
    de_popsize: int | None = Field(default=None, ge=1, le=MAX_DE_POPSIZE)
    de_tol: float | None = Field(default=None, gt=0.0)

    def forwarded(self) -> dict[str, Any]:
        """The caller-set options — what the job forwards to the fit.

        Unset keys and explicit nulls are dropped so the fit keeps its own
        default, except for :data:`NULL_IS_A_VALUE`, whose explicit null is
        forwarded as ``None`` because that is a meaningful setting.
        """
        set_keys = self.model_dump(exclude_unset=True)
        forwarded = {k: v for k, v in set_keys.items() if v is not None}
        forwarded.update(
            {k: None for k in NULL_IS_A_VALUE if k in set_keys and set_keys[k] is None}
        )
        return forwarded


class CalibrationOut(BaseModel):
    calibration_id: str
    kind: Literal["fd", "idm", "demand"]
    status: Literal["queued", "running", "done", "failed"]
    data_path: str
    source: str
    artifact_path: str | None = None
    error: str | None = None
    created_at: str
    artifact: dict[str, Any] | None = None
    """Parsed artifact JSON when the fit is done (docs/CONTRACTS.md §5)."""
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    """Every artifact file the job wrote, keyed by name. A fit that writes one
    file carries just its kind (``{"fd": ...}``); a ``demand`` calibration
    writes two and carries both (``{"observations": ..., "demand": ...}``), so
    a client never has to guess the second path from the first."""


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

    observations_path: str | None = None
    """Server-side ``flowstate.observations/1`` artifact the run set is scored
    against (docs/CONTRACTS.md, "Detector observations"), or null for a report
    with no observed side.

    This supplies the *evidence*, not the answer: the worker loads the
    artifact, scores every completed micro replicate against it
    (``validation.observed.score_run_against_observed``) and fills the
    ``link_flows_geh`` and ``speeds_rmspe`` criteria rows from the pooled
    comparisons, so both numbers remain computed from run artifacts. Like
    every other path in a request it must resolve inside the allow-listed
    roots (the results and uploads roots, ``FLOWSTATE_DATA_DIR``, and the
    repository's ``artifacts/`` and ``data/``); anything else is 422 with
    ``type: "path_outside_roots"``, and a missing file is 404. Relative values
    resolve against the repository root."""

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
    observations_path: str | None = None
    """The observations artifact the run set was scored against, as resolved
    on the server; null when the report has no observed side."""
    observed: dict[str, Any] | None = None
    """What the observed comparison rested on, once the report is done
    (``validation.observed.ObservedProvenance.to_dict()``): ``corridor``,
    ``provider``, ``dates``, ``url``, ``aggregation``, ``t0_local``,
    ``window_s``, ``n_stations``, ``n_windows``, ``n_windows_compared``,
    ``flow_fraction`` and ``speed_fraction`` (coverage of the station-window
    grid), ``n_link_hours``, ``n_speed_cells`` and ``n_replicates``. Null
    while the job is running, on failure, and for a report with no
    observations."""
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


# ---------------------------------------------------------------------------
# Corridor onboarding (WP-F: POST /corridors)
# ---------------------------------------------------------------------------

#: The stages ``api.onboarding_jobs.corridor_onboarding_job`` reports, in
#: order. ``GET /corridors/{id}`` turns the current one into a progress
#: fraction, so a 60–90 s onboarding is not an opaque spinner.
CORRIDOR_STAGES: tuple[str, ...] = (
    "extract",
    "network",
    "observations",
    "demand",
    "install",
    "done",
)

#: Scenario names accepted by ``POST /corridors``. The name becomes a
#: directory under the results root and a ``scenarios/<name>.yaml`` preset
#: file, so it is restricted to characters that mean the same thing in both.
CORRIDOR_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


class CorridorProgressOut(BaseModel):
    """How far the onboarding has got (stage names, not a time estimate)."""

    stage: str | None = None
    """The stage currently running, the one that failed, or ``"done"``."""
    completed_stages: int = 0
    total_stages: int = len(CORRIDOR_STAGES) - 1


class CorridorStationOut(BaseModel):
    """One detector station's place on the corridor chain."""

    station: str
    x_m: float
    """Position along the corridor [m] (the nearest chain point for a
    rejected station)."""
    offset_m: float
    """Perpendicular distance from the corridor centreline [m]."""


class CorridorRampOut(BaseModel):
    """One discovered ramp and where its profile came from."""

    name: str
    kind: Literal["on", "off"]
    x_m: float
    method: str
    """``detector`` (a live ramp detector), ``detector_scaled``,
    ``conservation`` (it closes the station balance) or
    ``zero_outside_observed_span``."""
    peak: float
    """Peak of the profile: veh/h for an on-ramp, a fraction for an off-ramp."""
    unit: Literal["veh/h", "frac"]
    station: str | None = None
    """The observed ramp detector it was matched to, when there was one."""


class CorridorLaneMismatchOut(BaseModel):
    """One mainline station where the compiled map and the inventory disagree.

    Reported, never enforced: the map and the detector inventory are both
    evidence, and which of them is wrong is the operator's call. A
    disagreement at a merge usually means the map tags the mainline straight
    through it (no acceleration lane), which starves the on-ramp — worth
    knowing before a battery runs, not after.
    """

    station: str
    x_m: float
    """Position along the corridor [m]."""
    compiled_lanes: int
    """Lanes the compiled network carries there — what SUMO will simulate."""
    inventory_lanes: int
    """Lanes the detector inventory reports at that station."""
    hint: str
    """One line naming the likeliest cause (a triage aid, not a diagnosis)."""


class CorridorSplitFindingOut(BaseModel):
    """One exit leaving the corridor, audited: the side OSM draws it on versus
    the lanes the compiled network feeds it from (``microsim.split_audit``,
    docs/ONBOARDING_MNDOT.md §9). A ``wrong_side`` or ``added_lane_wrong_side``
    verdict means through traffic is trapped in a lane that leads only to the
    exit; the ``remedy`` names the fix in the engine's terms. Reported, never
    enforced, like the lane check."""

    from_edge: str
    exit_edge: str
    continuing_edge: str | None = None
    x_m: float
    """Chain position of the split [m]."""
    osm_way: str
    osm_lanes: int | None = None
    turn_lanes: str | None = None
    turn_lanes_side: Literal["left", "right", "unknown"]
    osm_side: Literal["left", "right", "unknown"]
    osm_offsets_m: list[float] = Field(default_factory=list)
    """Signed lateral offsets [m] of the link's first nodes (+ left, − right)."""
    compiled_lanes: int
    exit_from_lanes: list[int] = Field(default_factory=list)
    compiled_side: Literal["rightmost", "leftmost", "middle", "all"]
    option_lanes: list[int] = Field(default_factory=list)
    added_lane: bool
    exit_lanes: int
    continuing_lanes: int | None = None
    verdict: Literal["ok", "wrong_side", "added_lane_wrong_side", "unknown"]
    remedy: str = ""


class CorridorSummaryOut(BaseModel):
    """What the onboarding found — the panel the dashboard shows.

    Every field is measured or derived by the job; none is a claim about the
    corridor's behaviour. A corridor is onboarded, not validated: the numbers
    here say what geometry was discovered and which detector each demand
    number came from, and the report (``POST /reports`` with
    ``observations_path``) is what scores the result against the observations.
    """

    corridor: str
    chain_length_m: float
    n_chain_edges: int
    lanes_profile: list[tuple[float, float, int]]
    """``(x_start_m, x_end_m, lanes)`` runs along the chain."""
    n_ramps: int
    stations_placed: list[CorridorStationOut]
    stations_rejected: list[CorridorStationOut]
    """Stations farther from the centreline than the acceptance threshold —
    another carriageway, a frontage road or another route. Their counts are
    not comparable with this corridor and are not used."""
    stations_without_chain_x: list[str] = Field(default_factory=list)
    """Observed stations the projection placed nowhere, kept at their
    inventory position."""
    inflow_peak_veh_h: float
    ramps: list[CorridorRampOut] = Field(default_factory=list)
    residuals: list[dict[str, Any]] = Field(default_factory=list)
    """Brackets whose flow change no ramp of the needed kind could carry; the
    remainder is recorded and carried, never smeared (CLAUDE.md §0.1)."""
    zeroed_ramps: list[str] = Field(default_factory=list)
    unmatched_detectors: list[str] = Field(default_factory=list)
    lanes_compared: int = 0
    """Mainline stations whose lane count could be compared with the map."""
    lane_mismatches: list[CorridorLaneMismatchOut] = Field(default_factory=list)
    """Of those, the ones that disagree (``lanes_compared`` minus this many
    match)."""
    split_audit: list[CorridorSplitFindingOut] = Field(default_factory=list)
    """Every exit leaving the chain, audited for the side it was compiled on
    (2026-09-24); a defect here is the map fault behind the I-94 WB lock."""
    lines: list[str] = Field(default_factory=list)
    """The same summary as plain text (``summary.txt`` in the bundle)."""


class CorridorRowOut(BaseModel):
    """One corridor onboarding job without its summary (``GET /corridors``).

    The status row: what a listing needs to offer a corridor as the target of
    a run or a report (``scenario_id``, ``observations_path``,
    ``config_hash``). The summary — what the onboarding discovered and
    derived — is several hundred lines of geometry and demand provenance per
    corridor and belongs to the one corridor being looked at, so it is served
    only by ``GET /corridors/{id}``.
    """

    corridor_id: str
    name: str
    status: Literal["queued", "running", "done", "failed"]
    progress: CorridorProgressOut
    scenario_id: str | None = None
    """The stored scenario the calibrated corridor was installed as — what
    ``POST /runs`` takes."""
    preset_filename: str | None = None
    """The ``scenarios/<name>.yaml`` preset file the scenario was written to,
    so it is selectable next to the shipped corridors."""
    config_hash: str | None = None
    observations_path: str | None = None
    """Server-side path of the observations artifact, ready to hand to
    ``POST /reports`` as ``observations_path``."""
    corridor_dir: str | None = None
    """Where the bundle (scenario YAML, stations table, observations, demand,
    OSM extract, summary) was written, relative to the results root."""
    error: str | None = None
    error_kind: str | None = None
    created_at: str


class CorridorOut(CorridorRowOut):
    """A corridor onboarding job's row (``POST``/``GET /corridors/{id}``)."""

    summary: CorridorSummaryOut | None = None
