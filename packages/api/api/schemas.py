"""Request/response models (Pydantic v2) for the FlowState API.

Reproducibility rule (CLAUDE.md §8): every run, metrics and heatmap response
carries the ``config_hash`` of the exact configuration that produced it.
Overrides on ``POST /api/v1/runs`` are a deep-merge patch onto the stored
:class:`flowstate_core.config.ScenarioConfig`, re-validated and re-hashed —
the run's hash is the hash of the *effective* config.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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
    scenario_id: str
    overrides: dict[str, Any] = Field(default_factory=dict)
    """Deep-merge patch onto the stored ScenarioConfig (re-validated)."""
    replicates: int | None = Field(default=None, ge=1)
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


class SweepCreateRequest(BaseModel):
    """Penetration × compliance × controller grid over one scenario."""

    scenario_id: str
    penetrations: list[float] = Field(min_length=1)
    compliances: list[float] = Field(min_length=1)
    controllers: list[str | None] = Field(default_factory=lambda: [None], min_length=1)
    overrides: dict[str, Any] = Field(default_factory=dict)
    """Applied to every cell before the grid values (deep merge)."""
    replicates: int | None = Field(default=None, ge=1)
    tier: Literal["micro", "macro"] | None = None


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
