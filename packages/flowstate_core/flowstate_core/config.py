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
from typing import Annotated, Any, Final, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

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


def _check_lane_shares(
    shares: list[float] | None, lanes: int | None, name: str = "entry_lane_shares"
) -> None:
    """Validate a per-lane share list (left to right; None = round-robin).

    Args:
        shares: The list to check, or ``None`` (always valid).
        lanes: Expected length (the network's lane count), or ``None`` when the
            lane count is only known at run time (OSM imports).
        name: Field name used in the error messages.
    """
    if shares is None:
        return
    if len(shares) < 2:
        raise ValueError(f"{name} needs at least two lanes")
    if any(s < 0.0 for s in shares) or sum(shares) <= 0.0:
        raise ValueError(f"{name} must be non-negative with a positive sum")
    if lanes is not None and len(shares) != lanes:
        raise ValueError(f"{name} has {len(shares)} entries for {lanes} lanes")


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
    entry_lane_shares: list[float] | None = None
    """Measured share of mainline entries per lane, LEFT to RIGHT (normalised
    at use; length = lane count). ``None`` (default) inserts round-robin
    across lanes. An upstream boundary carries a lane distribution as much as
    a flow: on I-24 the right lane holds 17% of vehicle-time at the entry
    against 34% in the left lane (an off-ramp has just drained it), and a
    replica that feeds a quarter of the flow into the lane the next on-ramp
    merges into queues that lane 1.5 km upstream of the gore
    (docs/I24_VALIDATION.md §0.5). Drawn per vehicle from the run's RNG."""

    @model_validator(mode="after")
    def _check_inflow(self) -> Self:
        times = [t for t, _ in self.inflow]
        if times != sorted(times):
            raise ValueError("inflow steps must be ordered by t_start")
        if any(q < 0 for _, q in self.inflow):
            raise ValueError("inflow must be >= 0")
        _check_lane_shares(self.entry_lane_shares, self.lanes)
        return self


class RampMeterSpec(BaseModel):
    """Ramp metering on an on-ramp (micro tier, 2026-09-06).

    A virtual signal at ``stop_line_m`` before the end of the ramp's last
    edge: every ramp vehicle stops there and the meter releases the first
    waiting vehicle whenever the metered headway ``3600 / rate`` has elapsed
    since the last release (one vehicle per green, the usual US practice).
    The rate comes from a pure controller in the ``controllers`` registry
    (``"alinea"``: Papageorgiou et al. 1991, integral feedback on the
    density measured on the corridor edge downstream of the merge every
    ``interval_s``), clipped to ``[rate_min_veh_h, rate_max_veh_h]``.
    ``params`` must carry the controller's target (for ALINEA the critical
    density ``rho_target_veh_km`` of the calibrated diagram); there is no
    built-in target. Releases and rates are recorded in ``meta.json``.
    """

    controller: Literal["alinea"] = "alinea"
    params: dict[str, float] = Field(default_factory=dict)
    interval_s: float = Field(default=30.0, gt=0.0)
    stop_line_m: float = Field(default=30.0, gt=0.0)
    rate_min_veh_h: float = Field(default=240.0, gt=0.0)
    rate_max_veh_h: float = Field(default=1800.0, gt=0.0)
    rate_init_veh_h: float | None = None
    """Initial rate; ``None`` starts at ``rate_max_veh_h``."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.rate_max_veh_h <= self.rate_min_veh_h:
            raise ValueError("rate_max_veh_h must exceed rate_min_veh_h")
        return self


SCRIPTED_MERGE_DEFAULTS: dict[str, float] = {
    "accept_gap_s": 0.6,
    "force_after_s": 4.0,
    "force_within_m": 80.0,
    "change_duration_s": 2.0,
    "lookahead_m": 120.0,
    "courtesy": 0.0,
}
"""Defaults of :attr:`RampSpec.merge_params` for the ``scripted`` merge."""
SCRIPTED_MERGE_KEYS = frozenset(SCRIPTED_MERGE_DEFAULTS)


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
    meter: RampMeterSpec | None = None
    """Ramp metering on this on-ramp (:class:`RampMeterSpec`); ``None`` =
    unmetered. Off-ramps cannot carry a meter."""
    merge_visibility_m: float | None = Field(default=None, gt=0.0)
    """Zipper merges only: SUMO connection ``visibility`` [m], the distance
    upstream of the zipper junction from which the ramp lane and the mainline
    lane it merges with consider each other and interleave (SUMO's default
    100 m). ``None`` keeps the default. Set to the acceleration lane's length
    to let the ramp feed the mainline over the lane instead of at its end
    (docs/I24_VALIDATION.md §0.7)."""
    merge: Literal["lane_change", "acceleration_lane", "zipper", "scripted"] = "lane_change"
    """How an on-ramp's acceleration lane hands its traffic to the mainline
    (micro tier, 2026-09-06). ``lane_change`` (default): the lane dead-ends
    and ramp vehicles change lanes under the lane-change model — on the I-24
    replica this locked the merge into a right-lane crawl under every
    parameter tried (docs/I24_VALIDATION.md §0.5). ``acceleration_lane``:
    SUMO's lane attribute of that name on the attach edge's rightmost lane
    (vehicles do not brake for the lane end). ``zipper``: the attach edge's
    rightmost lane is connected into the next corridor edge's rightmost lane
    alongside the mainline lane and the junction becomes a zipper, so ramp
    and mainline traffic interleave at the lane end instead of negotiating
    lane changes. Both need the acceleration lane to dead-end at the attach
    edge's end node (checked at run time) and are applied as netconvert
    patches recorded in ``meta.json``. ``scripted`` (2026-09-16): the network
    is the ``lane_change`` one, but every vehicle on the acceleration lane is
    driven by the runner's gap-acceptance merge instead of SUMO's lane-change
    model — it matches the speed of the mainline lane it is entering, takes
    the first gap that clears ``merge_params["accept_gap_s"]`` on both sides,
    and after ``merge_params["force_after_s"]`` of waiting inside the last
    ``merge_params["force_within_m"]`` of the lane it forces the change (the
    mainline follower yields, SUMO still refusing collisions). Recorded per
    ramp in ``meta.json["scripted_merges"]``."""
    merge_params: dict[str, float] = Field(default_factory=dict)
    """Tuning of the ``scripted`` merge (ignored by the other models).
    Keys and defaults: ``accept_gap_s`` 0.6 (time gap accepted to the mainline
    leader and follower, on top of the vehicle's ``s0``), ``force_after_s``
    4.0, ``force_within_m`` 80.0, ``change_duration_s`` 2.0, ``lookahead_m``
    120.0 (distance over which the mainline lane's speed is matched),
    ``courtesy`` 0.0 (m/s; when > 0 the mainline follower that blocks an
    otherwise acceptable gap is asked to hold its desired speed this far below
    the ramp vehicle's until the gap opens — courtesy yielding)."""

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        unknown = set(self.merge_params) - SCRIPTED_MERGE_KEYS
        if unknown:
            raise ValueError(f"unknown merge_params keys: {sorted(unknown)}")
        if self.merge_params and self.merge != "scripted":
            raise ValueError("merge_params apply to merge='scripted' only")
        if self.kind == "on":
            if not self.inflow:
                raise ValueError("an on-ramp needs a non-empty inflow")
            if self.exit_fraction:
                raise ValueError("an on-ramp cannot carry exit_fraction")
            times = [t for t, _ in self.inflow]
            if times != sorted(times) or any(q < 0 for _, q in self.inflow):
                raise ValueError("on-ramp inflow must be time-ordered and >= 0")
        else:
            if self.meter is not None:
                raise ValueError("an off-ramp cannot carry a meter")
            if self.merge != "lane_change":
                raise ValueError("merge models apply to on-ramps only")
            if self.merge_params:
                raise ValueError("merge_params apply to on-ramps only")
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
    entry_lane_shares: list[float] | None = None
    """Measured share of mainline entries per lane, LEFT to RIGHT, on the
    first corridor edge (its lane count comes from the map and is checked at
    run time); ``None`` = round-robin. See :class:`CorridorNetwork`."""
    netconvert_extra: list[str] = Field(default_factory=list)
    """Extra ``netconvert`` options appended verbatim at every import of this
    network (before the pruning flags). The onboarding path uses
    ``["--ramps.guess", "--ramps.no-split", "--ramps.ramp-length", "250"]``
    when the map lacks acceleration lanes: OSM often tags a motorway as three
    lanes straight through a merge, and a link that joins lane 0 at a plain
    junction starves under SUMO's yielding (observed 2026-09-23 on I-94 WB:
    four on-ramps delivered 4-6 % of their demand). ``--ramps.no-split`` keeps
    the attach edge's id, so ``corridor_edges`` and station positions stay
    valid. Recorded in the config hash whenever set."""
    internal_links: bool = False
    """Compile the network with SUMO's internal junction lanes (netconvert
    without ``--no-internal-links``). Off by default (every existing import
    is lane-to-lane at the node); on, junction movements — including a
    zipper merge (``RampSpec.merge``) — are resolved along internal lanes,
    which is where SUMO computes zipper interleaving. Vehicles are not
    recorded while on an internal lane (a few metres per junction)."""

    @model_validator(mode="after")
    def _check_source(self) -> Self:
        if self.osm_file is None and self.bbox is None:
            raise ValueError("OSMNetwork needs osm_file or bbox")
        _check_lane_shares(self.entry_lane_shares, None)
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


class HeavyVehicleSpec(BaseModel):
    """Heavy vehicles (trucks) as a share of the human fleet.

    A heavy vehicle is a second IDM population with its own length, SUMO
    vehicle class and emission class. Its parameters must come from a
    calibration artifact or be given explicitly — there are no built-in
    truck defaults, because none would carry provenance (CLAUDE.md §0.1,
    §12.6). On I-24 the recording's semi and truck classes are fitted from
    the same day (``artifacts/idm_i24_heavy.json``, docs/I24_DATA.md).
    Heavy vehicles are never tagged as controlled vehicles.
    """

    fraction: float = Field(ge=0.0, le=0.5)
    """Share of departures (ring: of vehicles) drawn as heavy, Bernoulli per
    vehicle from the run's RNG after every existing draw, so a fleet without
    this block reproduces its previous draws exactly."""
    length_m: float = Field(gt=5.0, le=30.0)
    emission_class: str = Field(min_length=1)
    """SUMO emission class written on the heavy vTypes (e.g. an HBEFA4 heavy
    duty class); the passenger class stays the fleet default."""
    vclass: Literal["truck", "trailer"] = "truck"
    """SUMO ``vClass`` of the heavy vTypes (lane permissions, closures)."""
    idm_calibration: str | None = None
    """IDMCalibration artifact for the heavy population (overrides the scalar
    means below, as for the passenger fleet)."""
    v0: float | None = Field(default=None, gt=0)
    T: float | None = Field(default=None, gt=0)
    a_max: float | None = Field(default=None, gt=0)
    b: float | None = Field(default=None, gt=0)
    s0: float | None = Field(default=None, gt=0)
    heterogeneity_frac: float = Field(default=HETEROGENEITY_FRAC_DEFAULT, ge=0, le=0.3)
    lane_shares: list[float] | None = None
    """Departure-lane distribution of the heavy population, LEFT to RIGHT like
    ``entry_lane_shares`` (normalised at use; length = the entry edge's lane
    count, checked against the network's lanes here and against the compiled
    map at run time). ``None`` (default) leaves heavy vehicles wherever the
    fleet's own lane scheme put them, i.e. spread over lanes like the light
    fleet — the uniform placement every heavy arm has used so far.

    Heavy traffic is not uniform across lanes: in the I-24 recording heavy
    fragments are 1.4 / 6.5 / 19.0 / 14.4% of lanes 1–4 against 9.2% corridor
    wide (``artifacts/i24_heavy_by_lane.json``, docs/MERGE_ROUND6_PLAN.md §2.4
    addendum), so a uniform share puts trucks in the HOV lane and takes them
    out of the two right lanes. Convert those per-lane fractions into this
    distribution with ``microsim.vehicles.heavy_lane_shares_from_artifact``.
    Drawn per heavy vehicle from a stream of the run's seed that is
    independent of every other draw, so setting this field moves the heavy
    vehicles' lanes and nothing else."""

    @model_validator(mode="after")
    def _check_population(self) -> Self:
        _check_lane_shares(self.lane_shares, None, name="heavy.lane_shares")
        scalars = (self.v0, self.T, self.a_max, self.b, self.s0)
        if self.idm_calibration is None and any(v is None for v in scalars):
            raise ValueError(
                "HeavyVehicleSpec needs idm_calibration or all five IDM means "
                "(v0, T, a_max, b, s0): heavy vehicles carry no built-in defaults"
            )
        return self


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
    lc_strategic_ramp: float | None = Field(default=None, ge=0.0)
    """SUMO ``lcStrategic`` for vehicles that enter from an on-ramp; ``None``
    (default) means the same value as ``lc_strategic``. One eagerness serves
    two opposite needs on a corridor with both ramp kinds: vehicles bound for
    an off-ramp must reach the deceleration pocket early (a large value),
    while vehicles entering from an acceleration lane should use its whole
    length before merging (SUMO's default 1.0). With a single elevated value
    the I-24 replica's ramp traffic merged at the gore at 9–13 km/h and the
    right lane crawled 1.5 km upstream of it (docs/I24_VALIDATION.md §0.5).
    Written on the ramp-origin vTypes only when it differs from 1.0."""
    lc_keep_right: float = Field(default=1.0, ge=0.0)
    """SUMO ``lcKeepRight``: eagerness to obey a keep-right rule, written on
    every vType when it differs from SUMO's default 1.0. US freeways have no
    keep-right obligation and the I-24 data shows all four lanes carrying
    similar vehicle-time (30/24/20/26%, left to right) at similar speeds;
    with the default, SUMO piles the replica's traffic into the two right
    lanes (merge-zone crawl at 23-27 km/h while the left lanes run free —
    docs/I24_VALIDATION.md). 0 disables the rule. Car-following untouched."""
    lc_cooperative: float = Field(default=1.0, ge=0.0, le=1.0)
    """SUMO ``lcCooperative``: willingness of mainline drivers to slow for a
    neighbour's lane change (0 = never, 1 = SUMO default). Written on every
    vType when it differs from 1.0. Governs how readily gaps open at merges;
    exposed for the Old Hickory merge calibration (docs/I24_CAPACITY.md §6)."""
    lc_assertive: float = Field(default=1.0, gt=0.0)
    """SUMO ``lcAssertive``: the minimum gap a lane-changing vehicle accepts is
    divided by this factor (>1 = accepts smaller gaps). Written on every vType
    when it differs from SUMO's default 1.0."""
    lc_speed_gain: float = Field(default=1.0, ge=0.0)
    """SUMO ``lcSpeedGain``: eagerness for speed-gain (tactical) lane changes.
    Written on every vType when it differs from SUMO's default 1.0."""
    heavy: HeavyVehicleSpec | None = None
    """Heavy-vehicle share and population (:class:`HeavyVehicleSpec`);
    ``None`` = passenger cars only, as before."""
    jm_timegap_minor_s: float | None = Field(default=None, gt=0.0)
    """SUMO junction-model ``jmTimegapMinor`` [s] written on every vType when
    set: the minimum time gap a vehicle accepts when entering a junction
    ahead of a prioritised (or, at a zipper, an interleaving) vehicle; SUMO's
    default is 1.0 s. Exposed for the zipper merge model (``RampSpec.merge``),
    whose merged-lane throughput it sets (docs/I24_VALIDATION.md §0.5 (j))."""
    jm_ignore_foe_prob: float | None = Field(default=None, ge=0.0, le=1.0)
    """SUMO junction-model ``jmIgnoreFoeProb`` written on every vType when
    set: the probability that a vehicle entering a junction ignores a foe
    slower than ``jmIgnoreFoeSpeed`` (SUMO defaults 0 and 0). Exposed as the
    last vehicle-side lever for a zipper merge's admittance, after the
    junction gap and the internal lanes proved inert (docs/I24_VALIDATION.md)."""
    lc_sublane: float | None = Field(default=None, ge=0.0)
    """Sublane model (``SimSpec.lateral_resolution_m``) eagerness for lateral
    moves within a lane, SUMO ``lcSublane`` (default 1.0); written when set."""
    lc_pushy: float | None = Field(default=None, ge=0.0, le=1.0)
    """Sublane model: willingness to encroach laterally on neighbours to
    open a gap, SUMO ``lcPushy`` (0 = never, SUMO's default; 1 = full);
    written when set. The lever for merging over a lane's length."""
    lc_impatience: float | None = Field(default=None, ge=-1.0, le=1.0)
    """Dynamic impatience that lets a vehicle accept smaller gaps the longer
    it has wanted to change lanes, SUMO ``lcImpatience`` (default 0.0);
    written when set."""
    lc_accel_lat: float | None = Field(default=None, gt=0.0)
    """Sublane model: maximum lateral acceleration [m/s²], SUMO
    ``lcAccelLat`` (default 1.0); written when set."""
    max_speed_lat: float | None = Field(default=None, gt=0.0)
    """Sublane model: maximum lateral speed [m/s], SUMO ``maxSpeedLat``
    (default 1.0); written when set."""
    min_gap_lat: float | None = Field(default=None, ge=0.0)
    """Sublane model: desired lateral gap to neighbours [m], SUMO
    ``minGapLat`` (default 0.6); written when set."""
    lat_alignment: Literal["left", "right", "center", "compact", "nice", "arbitrary"] | None = None
    """Sublane model: preferred lateral alignment within the lane, SUMO
    ``latAlignment`` (default ``center``); written when set."""
    lc_overtake_right: float | None = Field(default=None, ge=0.0, le=1.0)
    """Probability of overtaking on the right, SUMO ``lcOvertakeRight``
    (default 0: never, the European rule). US freeways allow it; written
    when set."""
    hov_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    """Share of passenger vehicles eligible for managed (HOV) lanes, drawn
    per vehicle from the run's RNG after every other draw (0 = none, so
    fleets without managed lanes reproduce their draws exactly); eligible
    vehicles carry SUMO ``vClass="hov"``. A measured or assumed occupancy
    share — record its source in the scenario header."""


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
    lateral_resolution_m: float | None = Field(default=None, gt=0.0, le=4.0)
    """SUMO ``--lateral-resolution`` [m]: ``None`` (default) keeps the
    lane-discrete LC2013 lane-change model; a value switches SUMO to the
    sublane model (LC_SL2015), in which vehicles occupy continuous lateral
    positions and merge gradually instead of jumping lanes when a whole-lane
    gap exists. Exposed for on-ramp merges, where the lane-discrete model
    produced a self-sustaining crawl on the I-24 replica (right lane at
    6–9 km/h upstream of the gore under every lane-change parameter tried,
    docs/I24_VALIDATION.md §0.5). Car-following is untouched; runs are slower."""


class PerturbationSpec(BaseModel):
    """A seeded shock. Presence of this block ⇒ seeded=True everywhere."""

    t_s: float = Field(ge=0)
    position_m: float = Field(ge=0)
    duration_s: float = Field(gt=0)
    v_drop_ms: float = Field(gt=0)
    """Commanded slowdown magnitude below prevailing speed [m/s]."""


class LaneClosureSpec(BaseModel):
    """A temporary lane closure (work zone, incident) on the corridor.

    Micro tier: for ``t ∈ [t_start_s, t_end_s)`` the listed lanes of every
    corridor edge overlapping ``[start_m, end_m)`` (linear x along the
    corridor) refuse all vehicle classes, so traffic changes lanes ahead of
    the closure through the ordinary strategic lane-change logic and any
    vehicle caught on a closed lane leaves it; the original permissions are
    restored at ``t_end_s``. Macro tier (single pipe): the flux through the
    interfaces of the overlapped cells is capped at the fundamental
    diagram's capacity times the share of lanes left open. A closure is an
    imposed disturbance, so the run is labelled ``seeded=True`` like a
    seeded perturbation (CLAUDE.md §0.2): waves at a closure are not the
    emergent phenomenon.
    """

    start_m: float = Field(ge=0.0)
    end_m: float = Field(gt=0.0)
    lanes: list[int] = Field(min_length=1)
    """SUMO lane indices closed (0 = rightmost) on each overlapped edge; an
    index beyond an edge's lane count is skipped there and recorded."""
    t_start_s: float = Field(ge=0.0)
    t_end_s: float = Field(gt=0.0)
    label: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end_m <= self.start_m:
            raise ValueError("closure end_m must exceed start_m")
        if self.t_end_s <= self.t_start_s:
            raise ValueError("closure t_end_s must exceed t_start_s")
        if any(i < 0 for i in self.lanes) or len(set(self.lanes)) != len(self.lanes):
            raise ValueError("closure lanes must be distinct non-negative lane indices")
        return self


class ManagedLaneSpec(BaseModel):
    """A managed (HOV) lane: for the window only eligible vehicles may use it.

    Micro tier: the listed lanes of every corridor edge overlapping
    ``[start_m, end_m)`` (measured like a closure, from the start of the
    analysis corridor) admit only SUMO class ``hov`` for ``[t_start_s,
    t_end_s)`` and get their permissions back afterwards; eligible vehicles
    are the share ``FleetSpec.hov_fraction`` of passenger vehicles, drawn per
    vehicle and written with ``vClass="hov"`` (heavy vehicles are never
    eligible). Outside the window every vehicle may use the lane. A managed
    lane is a standing rule of the road, not a disturbance: it does NOT set
    ``seeded=True``. The macro tier does not represent it (meta says so).
    """

    start_m: float = Field(ge=0.0)
    end_m: float = Field(gt=0.0)
    lanes: list[int] = Field(min_length=1)
    """SUMO lane indices (0 = rightmost); on a four-lane freeway the left
    lane is index 3."""
    t_start_s: float = Field(ge=0.0)
    t_end_s: float = Field(gt=0.0)
    label: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end_m <= self.start_m:
            raise ValueError("managed lane end_m must exceed start_m")
        if self.t_end_s <= self.t_start_s:
            raise ValueError("managed lane t_end_s must exceed t_start_s")
        if any(i < 0 for i in self.lanes) or len(set(self.lanes)) != len(self.lanes):
            raise ValueError("managed lanes must be distinct non-negative lane indices")
        return self


class MacroOptions(BaseModel):
    """Macro-tier (CTM screening) solver options — CLAUDE.md §5.

    Two knobs of the screening tier that are model choices rather than
    physics of the corridor, so they are carried on the scenario (and in the
    config hash when set) instead of being runner-call arguments no artifact
    records: the CTM cell length and which discretization of the moving
    bottleneck the AV cells use. The micro tier has neither a cell grid nor a
    moving flux constraint and ignores the block (it says so in its
    ``meta.json`` notes rather than dropping it silently).

    Attributes:
        dx_m: Target cell length [m]; the grid uses
            ``n_cells = max(10, round(length / dx_m))``, so the realized
            ``Δx`` recorded in ``meta.json["grid"]`` can differ slightly.
            The CFL step is derived from it (``macrosim.ctm.cfl_max_dt``).
        bottleneck_variant: Discretization of an AV's moving bottleneck —
            ``"flux_cap"`` (the v1 constraint ``F ← min(F, ρ·v*)``, the
            discrete analog of the Delle Monache–Goatin (2014) moving flux
            constraint) or ``"capacity"`` (``F ← min(F, α·q_max(v*))``).
            CLAUDE.md §5.5 asks for both to be reachable and compared
            against micro-tier ground truth.
    """

    model_config = ConfigDict(extra="forbid")

    dx_m: float = Field(default=100.0, gt=0.0, le=2000.0)
    bottleneck_variant: Literal["flux_cap", "capacity"] = "flux_cap"


class ScenarioConfig(BaseModel):
    """A complete, hashable scenario description."""

    name: str = Field(min_length=1)
    tier: Literal["micro", "macro"] = "micro"
    network: Network
    fleet: FleetSpec = Field(default_factory=FleetSpec)
    av: AVSpec = Field(default_factory=AVSpec)
    sim: SimSpec
    fd_calibration: str | None = None
    """Path to an ``FDCalibration`` artifact (as given, else resolved against
    the repository root — the same rule as ``FleetSpec.idm_calibration``, and
    confined to the API's allow-listed data roots). When set, the macro tier
    runs on that fitted triangular diagram instead of the uncalibrated
    ``v1_legacy`` preset (CLAUDE.md §5.1: FD parameters are calibrated
    per-corridor inputs, not constants) and ``meta.json["fd"]`` names the
    artifact. The micro tier has no fundamental diagram and does not use it;
    the field is deliberately tier-independent so the same scenario can be
    run on both tiers (the micro runner notes that it was ignored)."""
    macro: MacroOptions | None = None
    """Macro-tier solver options (:class:`MacroOptions`); ``None`` keeps the
    runner defaults (Δx = 100 m, ``flux_cap``)."""
    perturbation: PerturbationSpec | None = None
    closures: list[LaneClosureSpec] = Field(default_factory=list)
    """Temporary lane closures (:class:`LaneClosureSpec`); any entry labels
    the run ``seeded=True``."""
    managed_lanes: list[ManagedLaneSpec] = Field(default_factory=list)
    """Managed (HOV) lane rules (:class:`ManagedLaneSpec`); eligibility comes
    from ``FleetSpec.hov_fraction``."""
    seed: int = 42
    replicates: int = Field(default=20, ge=1, le=MAX_REPLICATES)
    """Seeded replicates per run. The ≥ 20 floor for headline claims is
    CLAUDE.md §0.6; the ``MAX_REPLICATES`` ceiling is a resource guard — a
    config is a request to execute this many simulations, and the largest
    study in this repository (the 540-run M3 sweep) uses 20 per cell."""

    @model_validator(mode="after")
    def _check_heavy_lane_shares(self) -> Self:
        """``HeavyVehicleSpec.lane_shares`` must match the entry lane count.

        Checked here and not on the fleet, which cannot see the network: a
        corridor states its lane count, an OSM import inherits it from the map
        (checked at run time by the route writer), and a ring has no entry.
        """
        heavy = self.fleet.heavy
        shares = None if heavy is None else heavy.lane_shares
        if shares is None:
            return self
        net = self.network
        if isinstance(net, RingNetwork):
            raise ValueError(
                "heavy.lane_shares needs a corridor or OSM network (a ring has no entry)"
            )
        if isinstance(net, CorridorNetwork) and len(shares) != net.lanes:
            raise ValueError(f"heavy.lane_shares has {len(shares)} entries for {net.lanes} lanes")
        entry = net.entry_lane_shares
        if entry is not None and len(shares) != len(entry):
            raise ValueError(
                f"heavy.lane_shares has {len(shares)} entries against "
                f"{len(entry)} entry_lane_shares"
            )
        return self

    @property
    def seeded(self) -> bool:
        """True when results come from an imposed disturbance — a seeded shock
        or a lane closure — and must be labeled as such (CLAUDE.md §0.2)."""
        return self.perturbation is not None or bool(self.closures)

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


CONFIG_HASH_VERSION: Final[int] = 2
"""Version of the hashing policy (docs/CONTRACTS.md §2). Bump it whenever a
field DEFAULT changes (a default change is a physics change and must move
every hash) — `tests/test_flowstate_core/test_config_hash.py` pins the
defaults snapshot and fails when one drifts without a bump."""


def config_hash_payload(cfg: ScenarioConfig) -> dict[str, Any]:
    """The object that is hashed: the policy version plus the config with
    every field at its default omitted (policy v2, 2026-09-06).

    Omitting defaults means a new optional field leaves the hash of every
    scenario that does not use it unchanged, so schema growth no longer
    invalidates goldens, sweeps and run trees; the network's ``kind`` is
    kept explicitly since it is a default-valued discriminator.
    """
    dumped = cfg.model_dump(mode="json", exclude_defaults=True)
    network = dict(dumped.get("network") or {})
    network["kind"] = cfg.network.kind
    dumped["network"] = network
    return {"hash_version": CONFIG_HASH_VERSION, "config": dumped}


def config_hash(cfg: ScenarioConfig) -> str:
    """12-hex-char sha256 of the canonical JSON form of
    :func:`config_hash_payload` (sorted keys, no whitespace)."""
    canonical = json.dumps(config_hash_payload(cfg), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]
