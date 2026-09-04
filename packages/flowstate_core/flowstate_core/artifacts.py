"""Calibration artifacts (docs/CONTRACTS.md §5).

Versioned, JSON-serialized records of every calibration run. Scenario configs
reference these by path/hash; nothing downstream may use calibration numbers
that don't trace to one of these artifacts (CLAUDE.md §0.1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class _Artifact(BaseModel):
    """Common artifact envelope: provenance + round-trip helpers."""

    schema_version: int = 1
    created_at: str
    """ISO-8601 timestamp, supplied by the caller (never auto-generated)."""
    source: str
    """Human-readable data provenance, e.g. 'PeMS D7 station 717490, 2024-03'."""
    data_hash: str
    """Hash of the input data file(s) this artifact was fitted from."""

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: str | Path) -> Self:
        return cls.model_validate_json(Path(path).read_text())


class TriangularFD(BaseModel):
    """Triangular fundamental diagram in SI units.

    Free-flow branch slope ``v_f`` [m/s], congested wave speed ``w`` [m/s,
    negative], jam density ``rho_jam`` [veh/m]. Critical density and capacity
    are derived, not stored.
    """

    v_f: float = Field(gt=0)
    w: float = Field(lt=0)
    rho_jam: float = Field(gt=0)
    ci95: dict[str, tuple[float, float]] = Field(default_factory=dict)
    """Optional bootstrap 95% CIs keyed by field name."""

    @property
    def rho_c(self) -> float:
        """Critical density [veh/m] where the two branches intersect."""
        return self.rho_jam * -self.w / (self.v_f - self.w)

    @property
    def q_max(self) -> float:
        """Capacity [veh/s]."""
        return self.rho_c * self.v_f

    def demand(self, rho: float) -> float:
        """Daganzo sending function Λ(ρ) [veh/s]."""
        return min(self.v_f * rho, self.q_max)

    def supply(self, rho: float) -> float:
        """Daganzo receiving function Σ(ρ) [veh/s]."""
        return min(self.q_max, -self.w * (self.rho_jam - rho))

    def equilibrium_flow(self, rho: float) -> float:
        """Q_e(ρ) [veh/s] for ρ ∈ [0, rho_jam]."""
        return min(self.v_f * rho, -self.w * (self.rho_jam - rho))


class FDCalibration(_Artifact):
    """A fitted fundamental diagram plus fit diagnostics."""

    kind: Literal["fd"] = "fd"
    fd: TriangularFD
    n_observations: int
    r2_freeflow: float
    """R² of the free-flow branch regression."""
    congested_quantile: float
    """τ used for the congested-branch quantile regression."""
    notes: str = ""


class IDMCalibration(_Artifact):
    """Population-level IDM parameter distribution fitted from trajectories.

    Parameter order everywhere: (v0, T, a_max, b, s0).
    """

    kind: Literal["idm"] = "idm"
    param_names: tuple[str, str, str, str, str] = ("v0", "T", "a_max", "b", "s0")
    mean: dict[str, float]
    cov: list[list[float]]
    n_episodes_fit: int
    n_episodes_holdout: int
    holdout_gap_rmse_m: float
    """Gap RMSE on held-out episodes — the honest generalization number."""
    per_episode_rmse_m: list[float] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _check_shapes(self) -> Self:
        k = len(self.param_names)
        if set(self.mean) != set(self.param_names):
            raise ValueError(f"mean keys {set(self.mean)} != {set(self.param_names)}")
        if len(self.cov) != k or any(len(row) != k for row in self.cov):
            raise ValueError(f"cov must be {k}x{k}")
        return self


class DemandProfile(_Artifact):
    """Piecewise-constant inflow profile for a corridor boundary."""

    kind: Literal["demand"] = "demand"
    steps: list[tuple[float, float]]
    """(t_start [s], inflow [veh/s]) — steps must be time-ordered."""
    geh_vs_counts: float | None = None
    """GEH of fitted inflow vs observed counts, when fitted (not hand-set)."""

    @model_validator(mode="after")
    def _check_ordered(self) -> Self:
        times = [t for t, _ in self.steps]
        if times != sorted(times):
            raise ValueError("demand steps must be ordered by t_start")
        if any(q < 0 for _, q in self.steps):
            raise ValueError("inflow must be >= 0")
        return self

    def inflow_at(self, t: float) -> float:
        """Inflow [veh/s] at time t (0 before the first step)."""
        q = 0.0
        for t_start, q_step in self.steps:
            if t >= t_start:
                q = q_step
            else:
                break
        return q


class LaneObservablesRecord(BaseModel):
    """Lane-use and lane-change observables of one trajectory set on one window.

    The JSON form of ``calibration.lanechange.LaneObservables`` — the same
    definitions apply to observed fragments and to simulated trajectories
    (docs/CONTRACTS.md §5). Sections are the half-open bins
    ``[x_edges_m[k], x_edges_m[k+1])`` along the corridor; ``lanes`` are lane
    ids in the data's band convention (1 = leftmost mainline lane).

    Stored quantities are additive counts; ``lane_share`` and
    ``changes_per_veh_km`` are the derived tables and are checked against the
    counts on validation so a record can never disagree with itself.
    """

    window_s: tuple[float, float] | None = None
    """Half-open time window [s] the counts were taken on (``None`` = all)."""
    x_edges_m: list[float]
    lanes: list[int]
    dt_s: float = Field(gt=0)
    """Sampling interval [s] used to turn sample counts into vehicle-time."""
    veh_time_s: list[list[float]]
    """Vehicle-time [s] per section (rows) and lane (columns)."""
    veh_km: list[float]
    """Vehicle-kilometres travelled in mainline lanes per section."""
    n_changes: list[int]
    """Held mainline-to-mainline lane changes located in each section."""
    n_changes_left: list[int]
    n_changes_right: list[int]
    change_hist_edges_m: list[float]
    """Edges of the fine lane-change location histogram."""
    change_hist: list[int]
    n_samples: int = Field(ge=0)
    lane_share: list[list[float | None]]
    """``veh_time_s`` row-normalized; ``None`` where a section has no time."""
    changes_per_veh_km: list[float | None]
    """``n_changes / veh_km``; ``None`` where a section has no travel."""

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        n_sec = len(self.x_edges_m) - 1
        n_lanes = len(self.lanes)
        if n_sec < 1 or n_lanes < 1:
            raise ValueError("need >= 2 section edges and >= 1 lane")
        if any(b <= a for a, b in zip(self.x_edges_m[:-1], self.x_edges_m[1:], strict=True)):
            raise ValueError("x_edges_m must be strictly increasing")
        for name, rows in (("veh_time_s", self.veh_time_s), ("lane_share", self.lane_share)):
            if len(rows) != n_sec or any(len(r) != n_lanes for r in rows):
                raise ValueError(f"{name} must be {n_sec}x{n_lanes}")
        for name, col in (
            ("veh_km", self.veh_km),
            ("n_changes", self.n_changes),
            ("n_changes_left", self.n_changes_left),
            ("n_changes_right", self.n_changes_right),
            ("changes_per_veh_km", self.changes_per_veh_km),
        ):
            if len(col) != n_sec:
                raise ValueError(f"{name} must have {n_sec} entries")
        if len(self.change_hist) != len(self.change_hist_edges_m) - 1:
            raise ValueError("change_hist must have one entry per histogram bin")
        for k in range(n_sec):
            if self.n_changes_left[k] + self.n_changes_right[k] != self.n_changes[k]:
                raise ValueError(f"section {k}: left + right changes != n_changes")
            total = sum(self.veh_time_s[k])
            for j in range(n_lanes):
                expected = self.veh_time_s[k][j] / total if total > 0.0 else None
                got = self.lane_share[k][j]
                if (expected is None) != (got is None) or (
                    expected is not None and got is not None and abs(expected - got) > 1e-9
                ):
                    raise ValueError(f"lane_share[{k}][{j}] disagrees with veh_time_s")
            rate = self.n_changes[k] / self.veh_km[k] if self.veh_km[k] > 0.0 else None
            got_rate = self.changes_per_veh_km[k]
            if (rate is None) != (got_rate is None) or (
                rate is not None and got_rate is not None and abs(rate - got_rate) > 1e-9
            ):
                raise ValueError(f"changes_per_veh_km[{k}] disagrees with the counts")
        return self


class LaneChangeGridPoint(BaseModel):
    """One evaluated point of a lane-change parameter grid."""

    params: dict[str, float]
    config_hash: str
    seed: int
    objective_fit: float | None
    """Objective on the fitted window (``None`` when it could not be scored)."""
    objective_holdout: float | None
    share_rms_fit: float | None = None
    rate_rmspe_fit: float | None = None
    share_rms_holdout: float | None = None
    rate_rmspe_holdout: float | None = None
    inserted_fraction: float | None = None
    """Departed / planned vehicles of the run (a merge that jams shows here)."""
    wall_s: float = 0.0


LANE_CHANGE_PARAMS: tuple[str, ...] = (
    "lc_cooperative",
    "lc_assertive",
    "lc_speed_gain",
    "lc_keep_right",
)
"""``FleetSpec`` lane-change fields a ``LaneChangeCalibration`` fits, in order."""


class LaneChangeCalibration(_Artifact):
    """Calibrated SUMO lane-change parameters for a corridor scenario.

    Records the grid that was evaluated, the best point by the fit-window
    objective, and the observed and simulated observables on both the fitted
    window and the held-out window, so the holdout number is the honest
    generalization check (as ``IDMCalibration.holdout_gap_rmse_m`` is for
    car-following). ``objective_spec`` carries the objective's weights;
    its definition lives in ``calibration.lanechange.lane_change_objective``.
    """

    kind: Literal["lanechange"] = "lanechange"
    param_names: tuple[str, ...] = LANE_CHANGE_PARAMS
    params: dict[str, float]
    """Fitted values of ``param_names`` (the best grid point)."""
    scenario: str
    scenario_config_hash: str
    """Config hash of the base scenario the grid was applied to."""
    fit_config_hash: str
    """Config hash of the best point's run (base + fitted params)."""
    seed: int
    """Replicate seed every grid point was run with."""
    fit_window_s: tuple[float, float]
    """Data-relative time window [s] the objective was minimized on."""
    holdout_window_s: tuple[float, float] | None
    """Window [s] scored but never fitted (``None`` if not evaluated)."""
    objective: float
    objective_holdout: float | None
    objective_spec: dict[str, float]
    observed_fit: LaneObservablesRecord
    observed_holdout: LaneObservablesRecord | None = None
    simulated_fit: LaneObservablesRecord
    simulated_holdout: LaneObservablesRecord | None = None
    grid: list[LaneChangeGridPoint]
    extra_observables: dict[str, LaneObservablesRecord] = Field(default_factory=dict)
    """Further observable tables on other partitions (e.g. ramp-relative
    zones), keyed by name; not used by the objective."""
    zone_names: list[str] = Field(default_factory=list)
    """Names of the sections of the ramp-relative partition, when recorded."""
    observed_source: str = ""
    """Path of the observed-observables file the objective was scored against."""
    versions: dict[str, str] = Field(default_factory=dict)
    """Package versions of the runs (CLAUDE.md §0.5)."""
    smoke: bool = False
    """True when produced by a shortened mechanics check — never a calibration."""
    notes: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if set(self.params) != set(self.param_names):
            raise ValueError(f"params keys {set(self.params)} != {set(self.param_names)}")
        if not self.grid:
            raise ValueError("grid must hold at least one evaluated point")
        if self.fit_window_s[1] <= self.fit_window_s[0]:
            raise ValueError("fit_window_s must be a non-empty [lo, hi) window")
        if self.holdout_window_s is not None and (
            self.holdout_window_s[1] <= self.holdout_window_s[0]
        ):
            raise ValueError("holdout_window_s must be a non-empty [lo, hi) window")
        for name, rec in (
            ("observed_fit", self.observed_fit),
            ("simulated_fit", self.simulated_fit),
            ("observed_holdout", self.observed_holdout),
            ("simulated_holdout", self.simulated_holdout),
        ):
            if rec is None:
                continue
            if rec.x_edges_m != self.observed_fit.x_edges_m or rec.lanes != self.observed_fit.lanes:
                raise ValueError(f"{name}: sections/lanes differ from observed_fit")
        return self
