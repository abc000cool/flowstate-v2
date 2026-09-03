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

#: Ceiling on ``ScenarioConfig.replicates``. Generous next to the ≥ 20 seeds a
#: headline claim needs (CLAUDE.md §0.6) and next to every scenario shipped
#: here (1–20), while keeping one config from queueing unbounded simulation
#: work. The API caps a *request* lower still (``api.schemas.MAX_REPLICATES``).
MAX_REPLICATES = 500


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


class RampSpec(BaseModel):
    """One on- or off-ramp attached to an OSM corridor (docs/CONTRACTS.md §2).

    Real corridors exchange traffic with interchanges; a mainline-only
    replica cannot conserve flow along the corridor (the US-101 replica's
    missing merge was a documented structural failure,
    docs/M3_US101_VALIDATION.md §6). A ramp is a chain of network edges
    (OSM ``motorway_link`` ways) joined to one corridor edge:

    * ``kind="on"`` — ``edges`` end at the junction where the ramp joins
      ``attach_edge``; vehicles are inserted at the start of ``edges[0]``
      following ``inflow`` (time-ordered ``(t_start [s], veh/s)`` steps,
      same convention as the corridor inflow) and then drive the corridor.
    * ``kind="off"`` — ``edges`` start at the junction where the ramp leaves
      ``attach_edge`` (the last corridor edge a diverging vehicle drives);
      every vehicle passing that point exits with probability
      ``exit_fraction`` (time-ordered ``(t_start [s], fraction)`` steps,
      looked up at the vehicle's departure time), drawn once per vehicle
      from the run's RNG, and leaves the network at the end of ``edges[-1]``.

    Ramp vehicles draw fleet parameters, AV tags and compliance exactly like
    mainline vehicles. Positions on ramp edges are not part of the corridor's
    linear ``x`` and are not recorded in ``trajectories.parquet``; a ramp
    vehicle appears in the outputs from the moment it is on a corridor edge.
    """

    kind: Literal["on", "off"]
    edges: list[str] = Field(min_length=1)
    """Ramp edge ids in driving order."""
    attach_edge: str = Field(min_length=1)
    """Corridor edge the ramp joins (on) or leaves from (off)."""
    inflow: list[tuple[float, float]] = Field(default_factory=list)
    """On-ramp demand steps ``(t_start [s], veh/s)``; empty for off-ramps."""
    exit_fraction: list[tuple[float, float]] = Field(default_factory=list)
    """Off-ramp diverge steps ``(t_start [s], fraction in [0, 1])``; empty
    for on-ramps."""
    name: str = ""
    """Optional label (e.g. the interchange) recorded in run metadata."""

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        if self.kind == "on":
            if not self.inflow:
                raise ValueError("an on-ramp needs a non-empty inflow")
            if self.exit_fraction:
                raise ValueError("an on-ramp cannot carry exit_fraction")
            times = [t for t, _ in self.inflow]
            if times != sorted(times) or any(q < 0 for _, q in self.inflow):
                raise ValueError("on-ramp inflow must be time-ordered and >= 0")
        else:
            if not self.exit_fraction:
                raise ValueError("an off-ramp needs a non-empty exit_fraction")
            if self.inflow:
                raise ValueError("an off-ramp cannot carry inflow")
            times = [t for t, _ in self.exit_fraction]
            if times != sorted(times):
                raise ValueError("exit_fraction steps must be time-ordered")
            if any(not 0.0 <= f <= 1.0 for _, f in self.exit_fraction):
                raise ValueError("exit_fraction values must lie in [0, 1]")
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
    boundary: BoundarySpec | None = None
    """Optional measured downstream boundary condition. On an OSM corridor
    the schedule is applied to the LAST edge of ``corridor_edges``, which
    therefore plays the exit-buffer role (its ``exit_buffer_m`` is ignored;
    the edge has its real length) and lies outside the measured span."""
    ramps: list[RampSpec] = Field(default_factory=list)
    """Interchange ramps exchanging traffic with the corridor (see
    :class:`RampSpec`); each ``attach_edge`` must be in ``corridor_edges``."""

    @model_validator(mode="after")
    def _check_source(self) -> Self:
        if self.osm_file is None and self.bbox is None:
            raise ValueError("OSMNetwork needs osm_file or bbox")
        if self.boundary is not None and len(self.corridor_edges) < 2:
            raise ValueError(
                "an OSM boundary needs corridor_edges with at least two edges "
                "(the last one hosts the boundary, outside the measured span)"
            )
        corridor = set(self.corridor_edges)
        for ramp in self.ramps:
            if ramp.attach_edge not in corridor:
                raise ValueError(f"ramp attach_edge {ramp.attach_edge!r} is not in corridor_edges")
            if corridor & set(ramp.edges):
                raise ValueError(f"ramp edges {ramp.edges} overlap corridor_edges")
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
    lc_strategic: float = Field(default=1.0, ge=0.0)
    """SUMO ``lcStrategic``: eagerness for route-required (strategic) lane
    changes, written on every vType when it differs from SUMO's default 1.0.
    Needed on corridors with off-ramps: with the default, exiting vehicles
    that are still in an inner lane at the diverge stop at the edge end and
    wait for a gap, which creates a spurious fixed bottleneck (measured on the
    I-24 replica and on the ramp fixture, docs/I24_VALIDATION.md); larger
    values make them move over earlier. It does not touch car-following."""
    lc_keep_right: float = Field(default=1.0, ge=0.0)
    """SUMO ``lcKeepRight``: eagerness to obey a keep-right rule, written on
    every vType when it differs from SUMO's default 1.0. US freeways have no
    keep-right obligation and the I-24 data shows all four lanes carrying
    similar vehicle-time (30/24/20/26%, left to right) at similar speeds;
    with the default, SUMO piles the replica's traffic into the two right
    lanes (merge-zone crawl at 23-27 km/h while the left lanes run free —
    docs/I24_VALIDATION.md). 0 disables the rule. Car-following untouched."""


class OracleSpec(BaseModel):
    """Downstream wave-detection oracle realism (CLAUDE.md §4.3).

    JAD's detection stage reads the downstream speed field. A *perfect* oracle
    sees the field instantaneously and exactly; real detection is late and
    noisy. This block makes the oracle swappable so that, as §4.3 requires,
    every headline JAD result is also reported under a degraded oracle.

    ``delay_s`` makes the controller observe the field as it was ``delay_s``
    ago (its own position stays current — it is the *traffic state* that is
    stale, as with loop-detector or probe latency). ``amplitude_noise_frac``
    multiplies each observed bin speed by ``1 + U(-f, +f)`` drawn per bin per
    control step from the run's seeded RNG. Defaults are a perfect oracle, so
    existing configs are unaffected.
    """

    kind: Literal["perfect", "noisy"] = "perfect"
    delay_s: float = Field(default=0.0, ge=0.0, le=120.0)
    """Detection latency [s]; §4.3 names 10–60 s as the realistic range."""
    amplitude_noise_frac: float = Field(default=0.0, ge=0.0, le=1.0)
    """Multiplicative speed error per bin; §4.3 names ±20% (0.2)."""

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        if self.kind == "perfect" and (self.delay_s > 0.0 or self.amplitude_noise_frac > 0.0):
            raise ValueError("kind='perfect' cannot carry delay_s or amplitude_noise_frac")
        if self.kind == "noisy" and self.delay_s == 0.0 and self.amplitude_noise_frac == 0.0:
            raise ValueError("kind='noisy' needs a nonzero delay_s or amplitude_noise_frac")
        return self


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
    oracle: OracleSpec = Field(default_factory=OracleSpec)
    """Wave-detection oracle realism for downstream-reading controllers (JAD)."""


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
    replicates: int = Field(default=20, ge=1, le=MAX_REPLICATES)
    """Seeded replicates per run. The ≥ 20 floor for headline claims is
    CLAUDE.md §0.6; the ``MAX_REPLICATES`` ceiling is a resource guard — a
    config is a request to execute this many simulations, and the largest
    study in this repository (the 540-run M3 sweep) uses 20 per cell."""

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
