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
