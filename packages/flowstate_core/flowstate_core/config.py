"""Scenario configuration schema (docs/CONTRACTS.md §2).

Pydantic-validated, YAML round-trippable, hashable. A ``ScenarioConfig`` plus a
seed fully determines a run; ``config_hash`` is recorded in every output
artifact so results always trace back to an exact configuration
(CLAUDE.md §0.5).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, Field, model_validator

from flowstate_core.constants import HETEROGENEITY_FRAC_DEFAULT, IDM_DEFAULTS


class RingNetwork(BaseModel):
    """Single-lane closed ring (Sugiyama benchmark geometry)."""

    kind: Literal["ring"] = "ring"
    circumference_m: float = Field(gt=0)
    n_vehicles: int = Field(gt=0)


class BoundarySpec(BaseModel):
    """Measured downstream boundary condition (docs/CONTRACTS.md §2).

    A time-varying speed schedule imposed at the corridor's downstream
    boundary, OUTSIDE the measured span: the micro runner appends an
    exit-buffer edge after the corridor proper and applies each step via
    ``edge.setMaxSpeed``, so congestion that originates downstream of the
    modeled section spills back into it exactly as a measured boundary
    would force. Imposing measured boundary conditions (demands, speeds,
    or bottleneck states taken from field data at the model limits) is
    standard microsimulation calibration practice — FHWA Traffic Analysis
    Toolbox Vol. III (FHWA-HOP-18-036, 2019) — and is required whenever the
    observed congestion enters the modeled section from outside it (e.g.
    the NGSIM US-101 site, docs/M2_RESULTS.md §7.3). A schedule derived
    from observations is a calibration input, not a seeded perturbation:
    it does NOT set ``seeded=True`` (the waves inside the span remain
    emergent), but its provenance must be recorded wherever it is used.
    """

    kind: Literal["speed_schedule"] = "speed_schedule"
    steps: list[tuple[float, float]] = Field(min_length=1)
    """Piecewise-constant (t_s [s, sim time], v_limit [m/s]) steps,
    time-ordered; each limit holds until the next step (the last holds to
    the end of the run)."""
    exit_buffer_m: float = Field(default=200.0, gt=0)
    """Length of the appended exit-buffer edge the limit applies to [m]."""

    @model_validator(mode="after")
    def _check_steps(self) -> Self:
        times = [t for t, _ in self.steps]
        if times != sorted(times):
            raise ValueError("boundary steps must be ordered by t_s")
        if any(v <= 0 for _, v in self.steps):
            raise ValueError("boundary speed limits must be > 0")
        return self


class CorridorNetwork(BaseModel):
    """Straight corridor with an upstream inflow boundary."""

    kind: Literal["corridor"] = "corridor"
    length_m: float = Field(gt=0)
    lanes: int = Field(ge=1, le=8, default=1)
    inflow: list[tuple[float, float]] = Field(min_length=1)
    """Piecewise-constant (t_start [s], inflow [veh/s]) steps, time-ordered."""
    boundary: BoundarySpec | None = None
    """Optional measured downstream boundary condition (speed schedule on an
    exit-buffer edge outside the measured span); None ⇒ free outflow."""

    @model_validator(mode="after")
    def _check_inflow(self) -> Self:
        times = [t for t, _ in self.inflow]
        if times != sorted(times):
            raise ValueError("inflow steps must be ordered by t_start")
        if any(q < 0 for _, q in self.inflow):
            raise ValueError("inflow must be >= 0")
        return self


class OSMNetwork(BaseModel):
    """Corridor imported from OpenStreetMap (the 'any city' path, §3.2.4)."""

    kind: Literal["osm"] = "osm"
    osm_file: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    """(south, west, north, east) in WGS84 degrees."""
    corridor_edges: list[str] = Field(default_factory=list)
    """SUMO edge ids forming the analysis corridor after import/pruning."""
    inflow: list[tuple[float, float]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_source(self) -> Self:
        if self.osm_file is None and self.bbox is None:
            raise ValueError("OSMNetwork needs osm_file or bbox")
        return self


Network = Annotated[RingNetwork | CorridorNetwork | OSMNetwork, Field(discriminator="kind")]


class FleetSpec(BaseModel):
    """Human-driver fleet: car-following model + population parameters."""

    model: Literal["IDM", "EIDM"] = "IDM"
    v0: float = Field(default=IDM_DEFAULTS["v0"], gt=0)
    T: float = Field(default=IDM_DEFAULTS["T"], gt=0)
    a_max: float = Field(default=IDM_DEFAULTS["a_max"], gt=0)
    b: float = Field(default=IDM_DEFAULTS["b"], gt=0)
    s0: float = Field(default=IDM_DEFAULTS["s0"], gt=0)
    delta: float = Field(default=IDM_DEFAULTS["delta"], gt=0)
    heterogeneity_frac: float = Field(default=HETEROGENEITY_FRAC_DEFAULT, ge=0, le=0.3)
    """σ of the per-vehicle truncated-normal draw, as a fraction of each mean."""
    idm_calibration: str | None = None
    """Path to an IDMCalibration artifact; when set, population stats override
    the scalar fields above and outputs record the artifact's data_hash."""


class AVSpec(BaseModel):
    """Controlled-vehicle deployment."""

    penetration: float = Field(default=0.0, ge=0.0, le=0.3)
    compliance: float = Field(default=1.0, ge=0.1, le=1.0)
    controller: str | None = None
    """Vehicle controller registry name; None ⇒ AVs drive as humans."""
    controller_params: dict[str, float] = Field(default_factory=dict)
    vsl: str | None = None
    """Segment controller registry name (VSL); None ⇒ no VSL."""
    vsl_params: dict[str, float] = Field(default_factory=dict)


class SimSpec(BaseModel):
    """Time discretization and output cadence."""

    duration_s: float = Field(gt=0)
    step_length_s: float = Field(default=0.5, gt=0, le=1.0)
    action_step_s: float = Field(default=0.5, gt=0)
    warmup_s: float = Field(default=0.0, ge=0)
    """Discarded from metrics; still simulated and recorded."""
    output_hz: float = Field(default=2.0, gt=0, le=10.0)


class PerturbationSpec(BaseModel):
    """A seeded shock. Presence of this block ⇒ seeded=True everywhere."""

    t_s: float = Field(ge=0)
    position_m: float = Field(ge=0)
    duration_s: float = Field(gt=0)
    v_drop_ms: float = Field(gt=0)
    """Commanded slowdown magnitude below prevailing speed [m/s]."""


class ScenarioConfig(BaseModel):
    """A complete, hashable scenario description."""

    name: str = Field(min_length=1)
    tier: Literal["micro", "macro"] = "micro"
    network: Network
    fleet: FleetSpec = Field(default_factory=FleetSpec)
    av: AVSpec = Field(default_factory=AVSpec)
    sim: SimSpec
    perturbation: PerturbationSpec | None = None
    seed: int = 42
    replicates: int = Field(default=20, ge=1)

    @property
    def seeded(self) -> bool:
        """True when results come from a seeded shock (must be labeled, §0.2)."""
        return self.perturbation is not None

    @classmethod
    def from_yaml(cls, path: str | Path) -> ScenarioConfig:
        raw = yaml.safe_load(Path(path).read_text())
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: expected a mapping at top level")
        return cls.model_validate(raw)

    def to_yaml(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        )


def config_hash(cfg: ScenarioConfig) -> str:
    """12-hex-char sha256 of the canonical JSON form (sorted keys)."""
    canonical = json.dumps(cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]
