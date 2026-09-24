# FlowState v2 — Shared Interface Contracts

This document is the **binding contract** between packages. Every package codes
against these interfaces; changes require updating this file first. CLAUDE.md
§0 non-negotiables apply everywhere. SI units internally: m, s, m/s, veh/m,
veh/s. Conversions ONLY via `flowstate_core.units`.

## 1. Controller contract (`controllers` ↔ `microsim` ↔ `macrosim`)

Vehicle-level (Lagrangian) controllers are **pure functions**:

```python
from flowstate_core.controller_types import ControllerObs, Memory, VehicleControllerFn


@dataclass(frozen=True)
class ControllerObs:
    t: float  # sim time [s]
    dt: float  # control interval [s]
    v: float  # ego speed [m/s]
    gap: float  # bumper-to-bumper gap to leader [m]; math.inf if none
    v_leader: float  # leader speed [m/s]; math.nan if no leader
    v_ref: float  # reference speed U [m/s] (rolling platoon mean, runner-supplied)
    downstream: tuple[float, ...] = ()  # mean speeds of downstream bins [m/s], nearest first
    downstream_dx: float = 100.0  # bin width of `downstream` [m]


Memory = dict[str, float]  # JSON-serializable; integrator/phase state lives here

VehicleControllerFn = Callable[[ControllerObs, Mapping[str, float], Memory], tuple[float, Memory]]
# returns (v_cmd in m/s, new_memory). Pure: no I/O, no globals, no RNG, no time.
```

Segment-level (VSL) controllers:

```python
@dataclass(frozen=True)
class SegmentObs:
    t: float
    dt: float
    seg_speed: tuple[float, ...]  # mean speed per segment [m/s], upstream→downstream
    seg_density: tuple[float, ...]  # density per segment [veh/m]


SegmentControllerFn = Callable[
    [SegmentObs, Mapping[str, float], Memory], tuple[tuple[float, ...], Memory]
]
# returns (speed limit per segment in m/s, new_memory)
```

Registry: `controllers.registry.get_vehicle_controller(name)` /
`get_segment_controller(name)`, `get_ramp_meter(name)`. Names: `"follower_stopper"`,
`"follower_stopper_capacity"` (2026-09-06: FollowerStopper with a time-headway
cap — beyond `g0_m + h_max_s · v` the command is released toward
`min(v_leader, U)` over `blend_m`, so the controlled vehicle smooths without
holding a hole open, the mechanism behind the I-24 sweep's throughput cost;
identical to FollowerStopper inside the cap, continuous across it, output in
`[0, U]`), `"alinea"` (ramp meter, `RampMeterFn`: `(RampMeterObs, params,
memory) -> (rate_veh_h, memory)`, see `RampMeterSpec`), `"pi_saturation"`,
`"jad"`, `"pi_meanfrac"` (vehicle); `"vsl_threshold"` (segment).
`"pi_meanfrac"` is the superseded CLAUDE.md §4.2 simplification, retained only
to reproduce the M3 result (docs/PI_CONTROLLER_FIX.md); `"pi_saturation"` is
the faithful Stern et al. (2018) Eqs. (3)–(5) implementation. Unknown name → `KeyError` with
available names in the message. Default params per controller exposed as
`controllers.registry.default_params(name) -> dict[str, float]`.

## 2. Scenario configuration (`flowstate_core.config`)

Pydantic v2 models, YAML round-trip via `ScenarioConfig.from_yaml(path)` /
`.to_yaml(path)`. Discriminated union on `network.kind`:

- `RingNetwork(kind="ring", circumference_m: float, n_vehicles: int)`
- `CorridorNetwork(kind="corridor", length_m: float, lanes: int ∈ [1, 8],
  inflow: list[tuple[float, float]], boundary: BoundarySpec | None = None)`
  # inflow: (t_start_s, inflow veh/s) steps, TOTAL across all lanes; lanes
  # raised from ≤4 to ≤8 in Phase 2 for the 5-lane US-101 replica (M2)
- `BoundarySpec(kind="speed_schedule", steps: list[tuple[float, float]],
  exit_buffer_m: float = 200.0)` — measured downstream boundary condition
  (added in Phase 3 for the US-101 replica). `steps` are time-ordered
  `(t_s [s, sim time], v_limit [m/s])` with `v_limit > 0`; each limit holds
  until the next step. The micro runner appends an `exit_buffer_m`-long
  exit edge AFTER the corridor proper and applies the schedule there via
  `edge.setMaxSpeed`, so the boundary acts outside the measured span and
  congestion spills back into it. Imposing measured boundary conditions is
  standard FHWA microsim calibration practice (Traffic Analysis Toolbox
  Vol. III, FHWA-HOP-18-036, 2019). A data-derived schedule does NOT set
  `seeded=True` (in-span waves stay emergent) but its provenance must be
  recorded wherever results are reported. Macro tier: not implemented
  (screening runs stay free-outflow; a run needing the boundary is a
  micro-tier run).
- `OSMNetwork(kind="osm", bbox: (S, W, N, E) | osm_file: str, corridor_edges: list[str],
  inflow, boundary: BoundarySpec | None = None, ramps: list[RampSpec] = [])`
  # Phase 6 (I-24 flagship) additions:
  # * `boundary` — the schedule is applied to the LAST edge of
  #   `corridor_edges`, which plays the exit-buffer role (its real length is
  #   recorded as `exit_buffer_m` in meta.json; `BoundarySpec.exit_buffer_m`
  #   is ignored) and lies outside the measured span. Needs ≥ 2 corridor
  #   edges.
  # * `ramps` — interchange ramps (`RampSpec` below); ramp edges are kept and
  #   pinned through OSM pruning alongside the corridor (`osm_import(...,
  #   keep_edges=...)`) and their connectivity to `attach_edge` is checked in
  #   the compiled net before SUMO starts.
  # * insertion on an OSM corridor uses the entry edge's real lane count
  #   (round-robin `departLane`, the M3 multi-lane scheme) instead of the
  #   single-lane scheme it used before Phase 6.
- `RampSpec(kind: "on" | "off", edges: list[str], attach_edge: str,
  inflow: list[tuple[float, float]] = [], exit_fraction: list[tuple[float, float]] = [],
  name: str = "")` — `kind="on"`: `edges` end at the junction where the ramp
  joins `attach_edge`; vehicles are inserted at the start of `edges[0]` per
  `inflow` (same `(t_start_s, veh/s)` convention as the corridor inflow).
  `kind="off"`: `edges` start where the ramp leaves `attach_edge`; every
  vehicle passing that point exits with probability `exit_fraction` at its
  departure time (one seeded Bernoulli draw per vehicle per reachable
  off-ramp, corridor order, first success wins) and leaves the network at the
  end of `edges[-1]`. Ramp vehicles draw fleet parameters, AV tags and
  compliance exactly like mainline vehicles (RNG order documented on
  `microsim.vehicles.build_corridor_plan`); positions on ramp edges have no
  linear `x` and are not written to `trajectories.parquet` — a ramp vehicle
  appears once it is on a corridor edge — while its fuel is accounted
  throughout. `meta.json` gains a `ramps` list (`index, name, kind,
  attach_edge, edges, n_planned, n_departed, n_planned_exiting,
  acceleration_lane_terminated`). Ramp demand
  derived from observations is a calibration input: it does NOT set
  `seeded=True`.

Other blocks:

- `FleetSpec`: `model: Literal["IDM","EIDM"]`, base params
  (`v0, T, a_max, b, s0, delta` — SI), `heterogeneity_frac: float = 0.12`
  (σ as fraction of mean, truncated normal at ±3σ, params drawn per vehicle
  with the run's RNG), `idm_calibration: str | None` — path to an
  `IDMCalibration` artifact (as given, else resolved against the repo root).
  When set, the artifact's population `mean`/`cov` OVERRIDE the scalar
  fields and `heterogeneity_frac`: per-vehicle params are drawn from the
  truncated multivariate normal (±3σ per marginal, hard physical floors
  kept) with the run's RNG, and run outputs record the artifact's
  `data_hash` (`meta.json` key `fleet_calibration`). `lc_strategic: float =
  1.0` (Phase 6) is SUMO's `lcStrategic` — eagerness for route-required
  lane changes — written on every vType only when it differs from 1.0, so
  route files of existing scenarios are byte-identical. It exists because
  with the default, vehicles bound for an off-ramp that are still in an
  inner lane at the diverge stop at the edge end and wait for a gap, which
  is a spurious fixed bottleneck (measured, docs/I24_VALIDATION.md); it does
  not touch car-following. `lc_keep_right: float = 1.0` (Phase 6) is SUMO's
  `lcKeepRight`, written the same way; US freeways carry no keep-right
  obligation and the I-24 lane-use data (vehicle-time 30/24/20/26% left to
  right, all lanes at similar speed) is the calibration target for it.
- `AVSpec`: `penetration: float ∈ [0, 0.3]`, `compliance: float ∈ [0.1, 1.0]`,
  `controller: str | None`, `controller_params: dict[str, float]`,
  `oracle: OracleSpec`.
- `OracleSpec(kind="perfect"|"noisy", delay_s: float = 0.0,
  amplitude_noise_frac: float = 0.0)` — wave-detection realism for
  downstream-reading controllers (JAD), added in Phase 5 for CLAUDE.md §4.3.
  `delay_s` makes the controller observe the traffic state as it was `delay_s`
  ago (its own position stays current); `amplitude_noise_frac` multiplies each
  observed bin speed by `1 + U(-f, +f)` from the run's seeded RNG, leaving
  empty bins empty and flooring speeds at 0. Default is a perfect oracle, so
  existing configs are unchanged; a degraded oracle does NOT set `seeded=True`
  (it perturbs perception, not the physics). See docs/JAD_ORACLE_RESULTS.md.
- `SimSpec`: `duration_s`, `step_length_s = 0.5`, `action_step_s = 0.5`,
  `warmup_s = 0.0`, `output_hz = 2.0`, `lateral_resolution_m: float | None
  = None` (2026-09-06: SUMO `--lateral-resolution`; `None` keeps the
  lane-discrete LC2013 lane-change model, a value in (0, 4] switches to the
  sublane model LC_SL2015 with continuous lateral positions — exposed for
  on-ramp merges after the lane-discrete model locked the I-24 replica's
  merge under every parameter tried, docs/I24_VALIDATION.md §0.5; moves
  every config hash).
- `PerturbationSpec | None`: seeded shock (`t_s, position_m, duration_s,
  v_drop_ms`). Non-None ⇒ every output row/report labels `seeded=True`.
- `closures: list[LaneClosureSpec]` (2026-09-06): temporary lane closures —
  `start_m, end_m` (measured from the start of the analysis corridor, i.e.
  after a generated corridor's insertion buffer; the runner records the
  span in the trajectories' linear x as `x_lo_m`/`x_hi_m` and never closes
  the insertion edge), `lanes` (SUMO lane indices,
  0 = rightmost, distinct), `t_start_s, t_end_s`, `label`. Micro tier: for
  the window the listed lanes of every corridor edge overlapping the span
  refuse every vehicle class (`lane.setDisallowed`, the fleet's classes),
  so traffic changes lanes ahead of the closure through the ordinary
  strategic logic and vehicles caught on a closed lane leave it; the
  original permissions are restored at `t_end_s`; an index beyond an edge's
  lane count is skipped there. `meta.json` records `closures` (lane ids,
  skipped ids, applied/released times). Macro tier (single pipe): the
  overlapped cells' interfaces are capped at `q_max × share of lanes open`
  (`lanes` from the corridor network; a ring counts one lane). Any closure
  labels the run `seeded=True` — an imposed disturbance is not the emergent
  phenomenon (CLAUDE.md §0.2).
- `RampSpec.merge: "lane_change" | "acceleration_lane" | "zipper"` (2026-09-06,
  on-ramps): how the acceleration lane hands traffic to the mainline.
  `lane_change` (default) is the dead-ending lane under the lane-change
  model; `acceleration_lane` marks lane 0 of the attach edge with SUMO's
  `acceleration="true"` (no braking for the lane end); `zipper` connects
  lane 0 into the next corridor edge's lane 0 beside the mainline lane 1
  and makes the end node a zipper junction. Both are netconvert patches
  (`microsim.networks.merge_patch_files`, `.edg.xml` / `.nod.xml` +
  `.con.xml`) applied in a second import pass; they need lane 0 to
  dead-end at the attach edge's end node and either exactly one lane to drop
  into the next edge or the same width when the lane was terminated there
  (checked at run time). `meta.json` lists `merge_models` and
  `net_patch_files`.
- Acceleration-lane termination (2026-09-23, every merge model): with
  `--ramps.guess` the guessed lane dead-ends at the attach edge only when
  that edge is longer than `--ramps.ramp-length`; on a shorter edge it
  spills into the following corridor edges and lane 0 continues, which every
  merge model refuses. `microsim.runner._apply_merge_models` then follows
  lane 0 downstream (`microsim.networks.accel_lane_end`) and, when it is a
  spill — it dead-ends within `ACCEL_LANE_TAIL_MAX_M` (400 m) and feeds
  nothing but the next corridor edge's lane 0 — terminates it at the attach
  edge's end with a `<delete>` connection patch
  (`microsim.networks.lane_end_patch_file`), lanes `1..n` untouched. The
  patch names compiled edge ids, so it is applied by
  `microsim.networks.patch_net` (a netconvert pass over the built network,
  `--sumo-net-file`), not by an OSM re-import, which reads connection files
  before ramp guessing has created those ids. Refused, with the lane counts
  in the message: an attach edge with no added lane, a lane 0 that feeds an
  exit (a weaving section's auxiliary lane) and a lane that runs on as an
  added through lane. `meta.json` marks the ramps it fired on
  (`ramps[].acceleration_lane_terminated`) and lists the patches in
  `net_patch_files`.
- `RampSpec.meter: RampMeterSpec | None` (2026-09-06, on-ramps): ramp
  metering as a virtual signal `stop_line_m` before the end of the ramp's
  last edge — every ramp vehicle stops there and the first waiting vehicle
  is released once `3600 / rate` s have elapsed since the last release. The
  rate comes from the registry (`RampMeterSpec.controller`, `"alinea"`:
  `controllers.ramp_meter.alinea`, integral feedback on the per-lane density
  of the corridor edge after the attach edge, `params.rho_target_veh_km`
  required, gain 50 veh/h per veh/km by default, clipped to
  `[rate_min_veh_h, rate_max_veh_h]` with anti-windup) every `interval_s`.
  `meta.json` `ramp_meters` carries the rates, densities and release times.
  Macro tier: not represented.
- `managed_lanes: list[ManagedLaneSpec]` and `FleetSpec.hov_fraction`
  (2026-09-06): managed (HOV) lane rules — like a closure (span from the
  start of the analysis corridor, SUMO lane indices, window) but the lanes
  admit only SUMO class `hov` for the window; eligible vehicles are the
  `hov_fraction` share of passenger vehicles, drawn per vehicle after every
  other draw and written with `vClass="hov"` (`FleetPlan.is_hov`, the
  trajectory column `is_hov`, `meta.json` `n_hov` / `managed_lanes`). A
  managed lane does not set `seeded=True`. Macro tier: not represented.
- `OSMNetwork.internal_links: bool = False` (2026-09-06): compile the
  import with SUMO's internal junction lanes (netconvert without
  `--no-internal-links`). Off keeps every existing import identical; on,
  junction movements are resolved along internal lanes, which is where SUMO
  computes zipper interleaving (`RampSpec.merge = "zipper"`; the vType
  junction gap `jm_timegap_minor_s` turned out to have no effect on a road
  zipper — four values gave byte-identical runs). Vehicles on an internal
  lane are not recorded (a few metres per junction).
- `RampSpec.merge = "scripted"` and `RampSpec.merge_params: dict[str, float]`
  (2026-09-16): a run-time merge behaviour instead of a netconvert patch.
  Every vehicle on lane 0 of the attach edge (which must dead-end, checked
  at run time like the other models) is driven by
  `microsim.runner._scripted_merge_step`: desired speed matched to the
  mainline vehicle ahead within `lookahead_m` via `vehicle.setMaxSpeed`
  (restored on exit; never `setSpeed`), a `changeLane` request under
  `laneChangeMode` 512 once the mainline gaps ahead and behind both clear
  `s0 + accept_gap_s × v`, mode 256 (forced; the follower yields, SUMO still
  refuses collisions) after `force_after_s` inside the last `force_within_m`,
  and with `courtesy > 0` the blocking mainline follower's desired speed is
  held `courtesy` m/s below the ramp vehicle's until the gap opens. Keys are
  validated against `SCRIPTED_MERGE_DEFAULTS` (`accept_gap_s` 0.6,
  `force_after_s` 4, `force_within_m` 80, `change_duration_s` 2,
  `lookahead_m` 120, `courtesy` 0); `merge_params` on any other model or on
  an off-ramp is rejected; both fields enter the config hash. `meta.json`
  lists `scripted_merges` (`ramp, attach_edge, params, n_entered, n_changed,
  n_forced, n_unfinished, wait_s_mean, wait_s_p90`).
- `meta.json.n_collisions` and `meta.json.collisions` (2026-09-16): the exact
  number of SUMO collision detections over the run (a persisting overlap under
  `--collision.action warn` counts every step) and the first 50 events
  (`t, collider, victim, type, lane, pos_m`). Additive; older artifacts lack
  the keys.
- `RampSpec.merge_visibility_m: float | None = None` (2026-09-07, zipper
  merges): SUMO connection `visibility` [m] on the ramp lane's and the
  merging mainline lane's connections into the merged lane — how far
  upstream of the zipper junction the two lanes consider each other and
  interleave (SUMO default 100 m). `None` keeps the default; a value near
  the acceleration lane's length makes the ramp feed the mainline over the
  lane. Hash-neutral when unset.
- `FleetSpec.lc_sublane`, `lc_pushy` (0–1), `lc_impatience` (−1–1),
  `lc_accel_lat`, `max_speed_lat`, `min_gap_lat`, `lat_alignment`
  (`left|right|center|compact|nice|arbitrary`), all `None` by default
  (2026-09-07): SUMO `lcSublane`, `lcPushy`, `lcImpatience`, `lcAccelLat`,
  `maxSpeedLat`, `minGapLat`, `latAlignment`, written on every vType only
  when set (`microsim.vehicles.sublane_vtype_attrs`), so unset fleets keep
  byte-identical route files. They parametrise the sublane model
  (`SimSpec.lateral_resolution_m`) that gridlocked at SUMO's defaults
  (docs/I24_VALIDATION.md §0.5 (h)); `lc_impatience` also acts under the
  lane-discrete model.
- `FleetSpec.lc_overtake_right: float | None = None` (2026-09-07): SUMO
  `lcOvertakeRight`, the probability of passing on the right (SUMO default
  0, the European rule; US freeways allow it). Written when set.
- `FleetSpec.jm_ignore_foe_prob: float | None = None` (2026-09-06): SUMO
  junction-model `jmIgnoreFoeProb` (with `jmIgnoreFoeSpeed` set high so any
  foe qualifies) written on every vType when set — the probability that a
  vehicle entering a junction ignores a foe; the last vehicle-side lever
  tried for a zipper merge's admittance.
- `FleetSpec.jm_timegap_minor_s: float | None = None` (2026-09-06): SUMO
  junction-model `jmTimegapMinor` [s] written on every vType when set (SUMO
  default 1.0 s) — the minimum time gap accepted when entering a junction
  ahead of another vehicle, which sets the merged-lane throughput of a
  zipper merge (`RampSpec.merge`).
- `FleetSpec.heavy: HeavyVehicleSpec | None` (2026-09-06): heavy vehicles
  as a share of the human fleet — `fraction` (Bernoulli per vehicle from the
  run's RNG, drawn after every existing draw so fleets without the block
  reproduce their previous draws exactly), `length_m`, `emission_class`
  (written on the heavy vTypes; the passenger class stays the fleet
  default), `vclass` (`truck` | `trailer`, written as `vClass`), and the
  heavy population as `idm_calibration` or all five explicit means with
  `heterogeneity_frac` — no built-in truck defaults exist, because none
  would carry provenance. Heavy vehicles are never tagged as controlled
  vehicles (effective penetration is `penetration × (1 − fraction)`).
  `FleetPlan.is_heavy`, the trajectory column `is_heavy` (every run, all
  false without the block) and `meta.json` `n_heavy` /
  `heavy_fraction_realized` carry the flags. The macro tier does not
  represent heavy vehicles (a calibrated fundamental diagram already embeds
  the observed mix; `meta.json` says so).
- `fd_calibration: str | None = None` (2026-09-23): path to an
  `FDCalibration` artifact (as given, else resolved against the repository
  root — the `fleet.idm_calibration` rule, and confined to the API's
  allow-listed roots with the same 422). When set, the macro tier runs on
  that fitted triangular diagram instead of the uncalibrated `v1_legacy`
  preset (CLAUDE.md §5.1: FD parameters are calibrated per-corridor inputs,
  not constants) and `meta.json["fd"]` names the artifact. Tier-independent
  on purpose, so one scenario can be run on both tiers; the micro tier has
  no fundamental diagram and records a note in `meta.json["notes"]` saying
  it did not use the field. Hash-neutral when unset.
- `macro: MacroOptions | None = None` (2026-09-23): macro-tier solver
  options — `dx_m: float = 100.0` (target cell length; the grid is
  `n_cells = max(10, round(length / dx_m))` and the realized Δx is in
  `meta.json["grid"]`) and `bottleneck_variant: "flux_cap" | "capacity" =
  "flux_cap"` (the discretization of a controlled vehicle's moving
  bottleneck, CLAUDE.md §5.5). `extra="forbid"`: an unknown key or variant is
  a 422. `macrosim.run_macro`'s `dx_m` / `bottleneck_variant` arguments now
  default to `None` = "take the config's block", an explicit argument still
  wins, and the effective pair is recorded in `meta.json["macro_options"]`.
  The micro tier notes that it ignored the block. Hash-neutral when unset.
- `seed: int`, `replicates: int = 20`, `tier: Literal["micro","macro"]`.

  `lc_cooperative: float = 1.0` (SUMO `lcCooperative`, mainline willingness
  to open gaps, in [0, 1]), `lc_assertive: float = 1.0` (`lcAssertive`, gap
  acceptance divisor, > 0) and `lc_speed_gain: float = 1.0` (`lcSpeedGain`)
  complete the lane-change block (2026-09-03, for the Old Hickory merge
  calibration, docs/I24_CAPACITY.md §6); like the two above they are written
  on the vType only when they differ from 1.0, so route files of existing
  scenarios are byte-identical, but their presence in the config snapshot
  moves every config hash. `lc_strategic_ramp: float | None = None`
  (2026-09-06) is the `lcStrategic` written on the vTypes of vehicles whose
  route starts on an on-ramp (`None` = same as `lc_strategic`): one
  eagerness cannot serve exiting vehicles (reach the pocket early) and
  entering vehicles (use the acceleration lane) at once, and the elevated
  single value made the I-24 replica's ramp traffic merge at the gore at
  crawl speed (docs/I24_VALIDATION.md §0.5). Its presence moves every config
  hash again; goldens regenerated with the PR note.
- `CorridorNetwork.entry_lane_shares` / `OSMNetwork.entry_lane_shares:
  list[float] | None = None` (2026-09-06): the measured share of mainline
  entries per lane, LEFT to RIGHT (normalised at use; the corridor kind
  checks the length against `lanes`, the OSM kind against the first corridor
  edge's lane count at run time). `None` keeps the round-robin insertion
  scheme byte for byte. When set, `build_corridor_plan` draws each mainline
  vehicle's departure lane from the run's RNG (`FleetPlan.depart_lane`,
  SUMO index, `-1` for ramp-origin vehicles) and the writer emits it as
  `departLane`. An upstream boundary carries a lane distribution as much as a
  flow: on I-24 the right lane holds 17% of vehicle-time at the entry against
  34% in the left lane, and a replica that feeds a quarter of the flow into
  the lane the next on-ramp merges into queues that lane 1.5 km upstream of
  the gore (docs/I24_VALIDATION.md §0.5). Moves every config hash.

`flowstate_core.config.config_hash(cfg) -> str`: sha256 over the canonical
JSON (sorted keys, no whitespace) of `config_hash_payload(cfg)`, first 12
hex chars; recorded in every output artifact with `config_hash_version`.
**Policy v2 (2026-09-06):** the payload is `{"hash_version":
CONFIG_HASH_VERSION, "config": model_dump(exclude_defaults=True)}` with the
network `kind` kept explicitly. A field at its default is omitted, so
adding an optional field no longer moves the hash of any scenario that
does not use it (three schema additions on 2026-09-06 had each moved every
hash and forced a golden regeneration). The price is that a *default*
change is invisible to the hash, so it must be paid for explicitly: bump
`CONFIG_HASH_VERSION` (which moves every hash once) and regenerate
`tests/golden/config_defaults.json`, the pinned full default dump that
`tests/test_flowstate_core/test_config_hash.py` compares on every run.
Policy v1 (sha256 of the full dump) produced every hash quoted in documents
dated before 2026-09-06; those artifacts keep their v1 hashes and their
config snapshots, which is enough to rerun them.

**Ramp-meter stop placement (2026-09-23, docs/LESSONS.md row 31).** The
meter used to set its stop only once a vehicle was on the ramp's last edge;
on the I-24 Hickory Hollow entrance (`19441652#1`) a vehicle reached that
short edge at speed, SUMO refused the stop (`TraCIException: ... too close
to brake`) and every ALINEA cell of the strategy sweep aborted. Now each
ramp vehicle is decided once, at its first step on any ramp edge (normally
on entering `edges[0]`); the stop position is unchanged (`stop_line_m`
before the end of `edges[-1]`). A vehicle already within its braking
distance `v² / (2 b) + v · Δt` of the line (`b` = `vehicle.getDecel`, the
comfortable deceleration; `Δt` the step length; distance summed along the
ramp edges, junction lanes excluded) is not stopped and passes the meter
that cycle, as does a vehicle whose stop SUMO refuses as "too close to
brake"; both are counted in the new `meta.json["ramp_meters"][i]
["n_passed_unstoppable"]`. Any other TraCI error still aborts the run. No
schema field changed (config hashes unchanged); on a single-edge ramp whose
line is beyond every vehicle's braking distance the behaviour is identical
to before (same releases). `RampMeterObs.queue_len` now counts every
vehicle holding a stop assignment, including those still upstream of the
last ramp edge (ALINEA does not read it). The fix was verified on synthetic
ramp fixtures; the I-24 ramps arm was then rerun on 2026-09-24 (ALINEA alone,
20 seeds paired, docs/I24_STRATEGIES.md dated section) with no ALINEA cell
failing, though that round did not archive the meter counters.

**Weaving sections: `RampSpec.merge = "weave"` and `WeaveSpec` (2026-09-23,
docs/WEAVE_MODEL_PLAN.md §2(A)).** An entrance whose auxiliary lane also
feeds the next exit (HCM 7th ed. ch. 13, a one-sided ramp weave) — the
geometry `accel_lane_end` refuses to terminate — can now run on its own
merge model. `RampSpec.weave: WeaveSpec | None = None` is required by, and
only allowed with, `merge="weave"` on an on-ramp: `exit_ramp: str` names the
paired off-ramp, which must be the only off-ramp of that `name` and attach to
the same `attach_edge` (schema check, `OSMNetwork`); `length_m: float | None`
is the field short length L_S (`None` = measured); `weave_params:
dict[str, float]` is validated against `WEAVE_DEFAULTS` = the scripted-merge
defaults plus `exit_accept_gap_s` 0.6 (unknown keys rejected). Hash-neutral
when unset (policy v2); both fields enter the hash when set. At run time
`microsim.networks.weave_sections(net, chain, ramps)` (the lane-0 walk shared
with `accel_lane_end`) must find the pair — lane 0 of the attach edge reaching
the exit's first edge, lane 0 to lane 0 along the chain, within
`WEAVE_LENGTH_MAX_M` 1,500 m — else `_apply_merge_models` refuses with the
edges and lane-0 connections in the message. A weave ramp gets no termination
and no netconvert patch. `microsim.runner._weave_step` then drives, on the
section's edges: entering vehicles (not routed to the paired exit) on a lane 0
that leads only to the exit change left under `accept_gap_s`, never forced;
exiting vehicles (route id `*_off<j>`) on lanes ≥ 1 change right one lane per
request under `exit_accept_gap_s`, forced (`laneChangeMode` 256) after
`force_after_s` inside the last `force_within_m` before the exit gore. A
forced change passes a minimum-gap guard (`_weave_force_gap_ok`: leader and
follower gaps must exceed the vehicle's `minGap` plus `exit_accept_gap_s` ×
the closing speed) and is requested for one step only; a refused one is
deferred, the vehicle is put back on mode 512, and the vehicle-step is
counted in `n_forced_deferred`. Both
match the target lane's speed via `setMaxSpeed` (never `setSpeed`);
`courtesy` > 0 makes the blocking target-lane follower yield for either
movement. Two fixed rules prevent the abreast-at-the-gore deadlock: in an
exchange (the target-lane follower is driven and wants this vehicle's lane)
the rear vehicle drops back (`WEAVE_EXCHANGE_YIELD_MS` 2 m/s below, no creep
floor), and an exiting vehicle holds station `2 s0 + exit_accept_gap_s · v`
behind the auxiliary-lane vehicle ahead (`WEAVE_HOLD_TAU_S` 2 s).
Amended 2026-09-24 (the T.H.52 lock, docs/ONBOARDING_MNDOT.md §10): the
speed matching and the station-keeping of an exiting vehicle apply only
inside the last `force_within_m` before the exit gore — farther out it drives
with its own lane (matching a slow auxiliary-lane vehicle from up to
`lookahead_m` behind pulled the through lane down to that vehicle's speed);
and an entering vehicle whose gaps are accepted **and** pass
`_weave_force_gap_ok` executes the change under mode 256 for one step (the
follower yields) instead of a mode-512 request, which SUMO refused while a
through-lane follower was closing from far back and answered by braking the
entering vehicle to drop in behind it; a step with no request puts the
vehicle back on mode 512. Golden `merge_weave` regenerated for this change.
`meta.json["weave_sections"]` lists per section `ramp, exit, edges,
exit_edge, exit_edges, length_m, length_m_measured, params, n_entered,
n_changed_in, n_changed_out, n_forced, n_missed, n_forced_deferred,
n_unfinished, n_exited, n_reached_section_exiting, n_departed_exiting,
wait_s_mean, wait_in_s_mean, wait_out_s_mean`
(`n_entered = n_changed_in + n_changed_out + n_missed + n_unfinished`).
Exit counting (2026-09-24, review finding): an exit-bound vehicle (route
`*_off<j>`) is `n_exited` once when seen on **any** edge of the paired
off-ramp (`exit_edges` = its `RampSpec.edges`) or when, having reached the
section, it is gone from the network while last seen on a section edge or an
internal lane after one — a per-step sighting on the ramp's first edge alone
undercounts a ramp edge shorter than one step of travel (12.5 m at 25 m/s,
0.5 s); with teleporting off and `collision.action warn` an exit-bound
vehicle can leave the network from the section only by driving its route to
the ramp's end. `n_reached_section_exiting` counts the exit-bound vehicles
seen on a section (or ramp) edge during the run and is the denominator of
`n_exited`; `n_departed_exiting` also counts those still upstream when the
run ends (`n_exited ≤ n_reached_section_exiting ≤ n_departed_exiting`, the
last two equal only once demand has drained through the section). A reached
vehicle next seen on another named edge left by the mainline (a reroute) and
is never counted.
Verified on the synthetic fixture `tests/fixtures/weave.osm` (golden
`merge_weave.json`); on the MnDOT I-94 WB 35-min slice it has one seed and no
validation claim.
Amended 2026-09-24 (block 3, the T.H.52 lock): the rules above are unchanged
and are **known to lock a one-sided weave at capacity**. The fixture
`tests/fixtures/weave_th52.osm` (three through lanes, a 308 m auxiliary lane
from entrance to exit, 583 m of approach; mainline 4,500 veh/h with 25 %
exiting, entrance 1,400 veh/h, 20 min, seed 3) is pinned by
`TestWeaveRun::test_th52_weave_at_capacity_flows`, `xfail(strict=True)`: lane
1 over the section's first 60 m must average above 5 m/s in every 60-s window
after a 120-s warm-up, the entrance must depart 90 % of its plan, at most 10 %
of driven vehicles may be unfinished, no collision. Under these rules lane 1
reads 20.2, 10.9, 2.9, 0.2 m/s in minutes 0–3 and 0.0 thereafter, the
entrance departs 81 of 466, 0 changes are forced and 16,576 are deferred. The
per-step trace names the rule: at t = 55.5 s the station-keeping rule
(`v_lead + (g_lead − g_hold)/τ`, no floor) commands 0.0 m/s to an exit-bound
vehicle in the **middle** through lane at x = 228 m — it holds behind
whatever is ahead in the lane to its right, here a lane-1 vehicle, while its
forced change is deferred — and lane 2 queues to a stop behind it; lane 1's
first stop at the section start (t = 116 s) is an exit-bound vehicle braking
at −8 m/s² after the exchange rule dropped it to 4 m/s and an entering vehicle
speed-matched to a 3.2 m/s lane-1 leader executed its mode-256 change at
2.9 m/s ahead of it. Gap parameters, SUMO's cooperative model (driven
vehicles run under modes 512/256 with every model change off) and the exit
link are not involved. The zipper re-derivation of docs/WEAVE_MODEL_PLAN.md
(dated paragraph) was tried in eight forms on this fixture and every one
locked the section as hard or harder, so nothing of it ships; the golden
`merge_weave` is unchanged. Two facts constrain the next attempt: a change
confined to the last `force_within_m` puts entrants and exiters through one
point of lane 0 (2,525 veh/h against a lane's ≈ 2,050 veh/h at T = 1.4 s), so
a jam formed at the gore never discharges; and an accepted change at the
0.6 s gap makes an IDM follower with T = 1.4 s brake at ≈ 4 m/s², so lane 1
is compressed into platoons with no acceptable hole after a few insertions.
Second attempt (2026-09-24, block 3): the rules above are **replaced**. No
desired-speed cap is written any more (no speed matching, no station-keeping,
no exchange rule; `courtesy` is accepted for hash stability and inert). A
driven vehicle keeps SUMO's car-following under mode 512 and every speed
request is a one-step target `vehicle.slowDown(v, 0.0)` (duration 0 is the
one-step form on SUMO 1.27.1; the default `speedMode` clamps it to the safe
speed and to the vehicle's `decel`). Each step a changer lists the target
lane on the section's linear axis — the section edges, the through lanes of
the corridor edge before and after them (`_weave_lane_map`) and the on-ramp
at negative positions — and chooses, among the gaps it is in or abreast of
within `lookahead_m` behind it, the nearest whose follower F can open it at
no more than F's comfortable deceleration (IDM towards the changer as
virtual leader, F's own `tau/accel/decel/minGap/maxSpeed`), gaps it can also
follow into first; the commitment holds while F still opens it within its
b. F is driven at min(its own IDM, that acceleration clipped at −b); the
changer is driven towards its gap's leader the same way when it would have
to brake for it (only the rear one of an abreast pair has such a gap); an
entering vehicle still on the ramp within `lookahead_m` of the section is
anticipated the same way before it appears on lane 0. Both movements are
forced in the last `force_within_m` after `force_after_s`, under
`_weave_force_gap_ok`; acceptance is the time gaps (`accept_gap_s` /
`exit_accept_gap_s`) plus the immediate follower absorbing the changer
within its b, executed under mode 256 for one step. `weave_sections[i]`
gains `n_cooperations` (vehicle-steps a follower was given a target),
`mean_follower_decel_ms2` (mean commanded deceleration over them, `None`
without any) and `n_changer_eased`. Fixture, seed 3, before → after: lane 1
over the first 60 m 20.2, 10.9, 2.9, 0.2 then 0.0 → 12.7, 11.8, 12.4, 10.3
then 4.2–6.4 m/s; entrance 81 → 225 of 466; driven 92 → 334 with unfinished
49 → 2 and deferred 16,576 → 0; exits 3 of 62 → 273 of 280; collisions 0. The
strict `xfail` stays: what remains is a crawl equilibrium at the section
entry (docs/WEAVE_MODEL_PLAN.md, dated paragraph, has the lane flows), not
a lock. Golden `merge_weave` regenerated (hash unchanged).
Third derivation (2026-09-24, block 3): one rule **added**, the second
derivation's rules untouched — **through traffic vacates the weave lane
upstream of the section** (`microsim.runner._weave_vacate_step`). A through
vehicle (not bound for the paired exit) on the lane of the corridor edge
before the section that feeds section lane 1, once within `vacate_ahead_m`
of the section start (new `WEAVE_DEFAULTS` key, default 150 m = the HCM 7th
ed. ch. 13 weaving segment's 500-ft upstream influence area, not a fitted
value; `0` disables; hash-neutral unless set), is asked **once** to move one
lane left: `vehicle.changeLane(vid, lane_to, duration)` under mode 512
(`LC_MODE_SCRIPTED_SAFE`: every model-driven change off, SUMO's own safety
check on the target lane's leader and follower gaps decides, the vehicle
adapts its speed to reach such a gap), the request living for the travel
time to the section start at the vehicle's speed when asked (floored at
`SCRIPTED_MERGE_CREEP_MS`). The original `laneChangeMode` is restored when
the vehicle is seen in the target lane (`n_vacated`) or when the request
has expired or the vehicle has reached the section still in the weave lane
(`n_vacate_refused`); a request still open then is ended with a one-step
stay in the current lane (it is by lane *index*, and on the section's edge
that index is the weave lane). The window is truncated to the edge before
the section (its lanes are the only upstream lanes in `lane_map`); a
two-lane section or a section with no corridor edge before it makes the
rule inert (`_weave_vacate_lanes`). Mode 768 (the same check, no speed
adaptation) executed 5 of 159 requests on the fixture and was rejected; the
weave's own 0.6-s acceptance under a one-step mode 256 executed 8–14 and
was rejected. `weave_sections[i]` gains `n_vacated` and
`n_vacate_refused`. Fixture, seed 3, second → third derivation: lane 1
over the first 60 m in minutes 2–19 from 12.7, 11.8, 12.4, 10.3 then 4.2–6.4
to 11.5, 10.0, 6.9, 8.1, 11.2, 12.9, 12.6, 8.6, 11.7, 11.1, 5.4, 4.5, 7.1,
6.3, 12.0, 13.4, 12.5, 10.8 m/s; entrance 225 → 317 of 466; driven 334 → 307
with unfinished 2 → 0; 228 through vehicles vacate, 28 refused; collisions
0. The strict `xfail` stays: one minute is below 5 m/s and the entrance
criterion (≥ 419) fails at every window (docs/WEAVE_MODEL_PLAN.md, dated
paragraph: the sensitivity table, and the ramp now held at 3 m/s over its
first 100 m by the easing rule at the anticipation-zone entry). Golden
`merge_weave` regenerated (throughput 1,658 → 1,662 veh/h, mean travel time
70.1 → 69.5 s, σ_v spatial 4.57 → 4.34 m/s, changes in/out/forced
20/13/2 → 18/17/1, hash unchanged).
Fourth derivation (2026-09-24, block 3, the entrant side): one bound
**added** to the second derivation's easing, nothing else changed —
`microsim.runner._weave_easing_ok`. A changer is eased towards its gap's
leader L only while dropping in behind L by the section end needs no more
than its comfortable deceleration: with `t_a = remaining / max(v_c, creep)`
(for an entrant on the ramp, the ramp to the gore plus the section), the
drop `d = (s0 + accept · v_c) − s_l` (its accepted gap behind L's rear) and
L holding its speed, `a_req = 2·(d + (v_c − v_l)·t_a)/t_a²` must be `≤ b`;
otherwise the changer keeps its own car-following speed and the change
waits for its follower's cooperation or the forced mode. `_weave_cooperate`
takes the movement's accepted time gap and the remaining section length for
it. The rule binds rarely and its effect on `weave_th52.osm` is within seed
noise (seed 3: entrance 317 → 325, every minute above 5 m/s; seed 4: 311 →
307, two minutes below 5; seed 5: unchanged to the counter). The two
entrant-side rules the derivation set out with were measured at seeds 3–5
and **rejected** (docs/WEAVE_MODEL_PLAN.md, dated paragraph, has the
table): no easing of an entrant upstream of the gore moves the entrant's
positioning brake to lane 0's first metres and settles lanes 0 and 1 at
3–5 m/s in every minute (entrance 277); no cooperation from a ramp vehicle
for an exit-bound changer locks the section at seed 4 (0.0 m/s from minute
13, entrance 204). Easing only when the drop is needed within the horizon
reaches 400 of 466 at seeds 3 and 5 and locks at seed 4; it is the lead for
a fifth derivation, not shipped. The strict `xfail` stays (entrance 325 of
466 against 419). Golden `merge_weave` unchanged (the bound never binds on
`weave.osm`); determinism preserved.
Fifth derivation (2026-09-24, block 3): two rules, on top of the fourth.
**(1) Easing only when needed** — `_weave_easing_ok` now requires
`0 < a_req ≤ b`: a changer gets no brake for a gap leader that opens the gap
by itself within the horizon (faster than the changer, or already clear by
more than the accepted gap). **(2) A stopped changer–follower pair is
released** (`microsim.runner._weave_pair_release`, new `WEAVE_DEFAULTS` key
`pair_release_s` = 2 s, two ≈ 1 s human reaction times, Treiber & Kesting
2013 ch. 12, not a fitted value; hash-neutral unless set): a driven changer X
and the follower F of its committed gap whose front is within one vehicle
length (the longer of the two) behind X's rear on the section axis, both
below the creep speed `SCRIPTED_MERGE_CREEP_MS` for more than
`pair_release_s` (`t − since > pair_release_s`), are released: the one
farther from the section end (more section ahead of its front; ties, which
cannot arise since F is behind X, by the vehicle id string, the greater
yielding) yields for the step — no speed target in either role, no request
if it is itself driven, its own commitment dropped and re-chosen next step —
while the other executes its change under the normal acceptance, or under
the forced mode at once when within `force_within_m`, guarded by
`_weave_force_gap_ok` against closing only (`s0` floor dropped; both are
below the creep speed and mode 256 still refuses an overlap). The pair the
fourth derivation described (an entrant that is the exiter's cooperating
follower, the exiter its gap leader) is this state with F a driven entrant;
the pair that actually locks `weave_th52.osm` under (1) at seeds 4 and 5 is
an exiter stopped at the gore in lane 1 (held by SUMO: lane 1 does not
continue on its route) with a *done* exit-bound vehicle in lane 0 held bumper
to bumper behind its rear by the exiter's own cooperation command every
step; a release of entrant–exiter pairs alone was measured inert on it
(session record), so the pair is defined by the commitment, not the
movement. `weave_sections[i]` gains `n_pair_releases` (each pair once per
release). Fixture, fourth → fifth derivation: seed 3 lane 1 over the first
60 m 7.0–12.2 m/s in every minute (was 6.2–12.9), entrance 325 → 395 of
466, all vehicles 1,376 → 1,529 of 1,966, driven 308 → 452 with unfinished
0 → 6 and forced 5 → 20, no release fires, no collision; seed 4 307 → 392
(2 of 501 unfinished, 9 releases, was a lock from minute 16 under (1)
alone with 25 unfinished); seed 5 295 → 389 (4 of 483 unfinished, 1
release; under (1) alone a lock from minute 16 with 10 collisions in the
jam, none after). `test_th52_weave_at_capacity_does_not_lock` pins seeds 4
and 5 (every minute above 2 m/s, ≤ 10 % unfinished, no collision, entrance
≥ 80 %); the strict `xfail` stays on the entrance criterion (395 against
419; the ramp still queues at 4–5 m/s over its first 100 m). Golden
`merge_weave` regenerated (throughput 1,661.5 → 1,687.5 veh/h, mean travel
time 69.51 → 69.82 s, σ_v spatial 4.34 → 4.28 m/s, changes in/out/forced
18/17/1 → 17/20/1, hash 436cd4ec9e5d unchanged); determinism preserved.

## 3. Run outputs

`RunResult` directory layout (one per replicate), written by runners:

```
runs/<config_hash>/<seed>/
  trajectories.parquet     # micro tier
  edges.parquet            # binned edge/segment data (both tiers)
  meta.json                # config snapshot, config_hash, seed, versions, tier,
                           # seeded flag, wall_time_s, fuel totals
```

`trajectories.parquet` columns (micro): `t: f64 [s]`, `veh_id: str`,
`x: f64 [m]` (linear position along route; ring = arc length),
`lane: i32`, `v: f64 [m/s]`, `a: f64 [m/s²]`, `is_av: bool`, `complied: bool`,
`is_heavy: bool` (2026-09-06; false on every vehicle without a `FleetSpec.heavy` block),
`is_hov: bool` (managed-lane eligibility; false without `hov_fraction`).
Sampled at `sim.output_hz`. On corridors, `x` spans entry buffer + corridor
proper (+ exit buffer when a `BoundarySpec` is configured); micro `meta.json`
then carries a `boundary` object (`kind`, `exit_edge`, `exit_buffer_m`,
`n_steps`, `n_steps_applied`, `v_limit_min_ms`, `v_limit_max_ms`).

`edges.parquet` (both tiers): `t_bin: f64 [s]`, `x_bin: f64 [m]`,
`mean_speed: f64 [m/s]`, `density: f64 [veh/m]`, `flow: f64 [veh/s]`.
When a scenario sets `av.vsl`, both tiers add a `vsl_dispatch` block to
`meta.json`: the gantry segments (edge ids for micro, cell ranges for macro),
their lengths, the base speed limits, and the per-dispatch posted and applied
limits (posted = controller output scaled by compliance through
`controllers.vsl.effective_limit`, never above the base limit). The macro
tier also records `vsl` (controller name and parameters).

Macro-tier rows carry `tier="screening"` in meta.json — the report generator
MUST refuse macro-only validation reports (CLAUDE.md §5.6).

`LaneChangeCalibration` (`kind="lanechange"`, `flowstate_core.artifacts`): fitted
`params` over `(lc_cooperative, lc_assertive, lc_speed_gain, lc_keep_right)`,
scenario and config hashes, seed, fitted and held-out windows (study-relative,
half-open), objective values, and observed/simulated `LaneObservablesRecord`s
(sections, band-convention lanes, additive vehicle-time, vehicle-km, change
counts and histogram; derived shares and rates validated against the counts),
the evaluated grid, provenance, and a `smoke` flag that marks a run that is
never a calibration. Definitions live in `calibration.lanechange`.

## 4. Space-time field (`validation.waves` + heatmaps)

```python
@dataclass
class SpeedField:
    t_edges: np.ndarray   # [nt+1] s
    x_edges: np.ndarray   # [nx+1] m
    mean_speed: np.ndarray  # [nt, nx] m/s, NaN = no vehicles in bin

speed_field(trajectories: pd.DataFrame, dt_bin=15.0, dx_bin=75.0) -> SpeedField
```

Wave detection returns `WaveSet`: per-wave `speed_ms` (negative = backward),
`amplitude_ms`, `duration_s`, `extent_m`; plus `count`. Detection: threshold
`v < v_jam_thresh` (default 40 km/h → 11.11 m/s), connected components,
front extraction, robust line fit (Theil–Sen). **Relative mode** (Phase 6,
ROADMAP D1): `detect_waves(field, relative_frac=f)` thresholds at
`f × p90` of the field's non-empty bin speeds instead, which resolves the
stripes inside a field that is congested everywhere (the absolute threshold
labels it as one jam with a pinned front).

**Detector recipes.** Which detector produced a wave-speed number is part
of the number, so every recipe is a frozen `WaveDetector` in
`validation.waves.WAVE_DETECTORS`, keyed by name, carrying its bins and
parameters, with `measure(field) -> WaveMeasurement` (the criterion
statistic `speed_kmh`, the per-front speeds, the threshold applied) and
`describe()` (one line with every parameter, written into criteria rows).
`measure` refuses a field whose bins differ from the recipe's.

| name | bins | rule | statistic |
|---|---|---|---|
| `standard` | 15 s × 75 m | jam = v < 40 km/h, 8-connected components ≥ 4 bins | mean magnitude of backward Theil–Sen front speeds |
| `stripe` | 10 s × 50 m | jam = v < 25 km/h, same segmentation | same |
| `relative` | 15 s × 75 m | jam = v < 0.5 × p90 of non-empty bins | same |
| `stack` | 15 s × 75 m | no threshold: `stack_wave_speed` slant-stacks the two-way demeaned field over front speeds −40…−2 km/h (0.25 km/h steps) and takes the peak; rejected below peak/median contrast 3 or at a range edge | peak speed |

`planted_stripe_field(...)` builds the synthetic benchmark (a periodic train
of backward stripes of known speed inside a standing queue occupying the
downstream `congested_fraction` of the corridor, free flow upstream) on
which every recipe is measured; the table of recovered speeds lives in the
`validation.waves` module docstring and is pinned by
`tests/test_validation/test_validation_waves.py::TestDetectorBenchmark`.

**Criterion.** The CLAUDE.md §7.1 wave-speed criterion is defined on the
detector named by the criteria profile (`CriteriaProfile.wave_detector`,
default `stack` — the only registered recipe that recovers the planted speed
at every congested fraction 0.3–0.95; `standard` finds no backward front on
a congested background at any of them). `evaluate(..., wave_speed_kmh=v,
wave_detector=d)` records `d.describe()` in the row's `detail`; a value
measured with a recipe other than the profile's is reported as not
evaluated, and a caller that states no detector gets a "not stated" note.
Artifacts written before this registry (`artifacts/i24_validation_*.json`
schema ≤ 4, the M3 US-101 files) carry `standard`-detector criterion rows.

`artifacts/i24_validation_<arm>.json` schema 6 (2026-09-06,
`scripts/i24_validate.py`): the `geh` block carries three tables —
`vs_tracked_counts` (fragment crossings, a lower bound),
`vs_coverage_corrected_counts` (crossings ÷ the apparent coverage that shapes
the corrected arm's demand) and `vs_recommended_coverage_counts` (crossings ÷
the recommended estimator of `artifacts/i24_coverage.json`) — and
`primary = "recommended"` with the rule spelled out in `primary_rule`; the
observed cache (`runs/i24_validation/observed_i24.json`, `cache_version` 3)
adds `hourly_flows_veh_h_recommended` and `coverage_recommended_per_window`.
The `rmspe` block adds, when the run stored `segment_speeds_ms_per_replicate`,
`per_replicate_vs_observed` (one seed against the recording) and
`leave_one_out_floor` (one seed against the mean of the other seeds — the
model's own realisation-to-ensemble distance at the 5-min × 549 m
resolution); `--criteria-only` re-scores older artifacts under the schema-6
rule without simulating.

## 5. Calibration artifacts (`flowstate_core.artifacts`)

Pydantic models with `.save(path)` / `.load(path)` JSON round-trip, all carrying
`schema_version`, `created_at` (ISO, caller-supplied), `source: str`,
`data_hash: str`:

- `TriangularFD`: `v_f [m/s]`, `w [m/s, negative]`, `rho_jam [veh/m]`;
  derived properties `rho_c`, `q_max`. Bootstrap CIs as
  `ci95: dict[str, tuple[float, float]]`.
- `FDCalibration`: fitted `TriangularFD` + fit diagnostics.
- `IDMCalibration`: population `mean: dict`, `cov: list[list[float]]`
  (order: v0, T, a_max, b, s0), per-episode fit table, `holdout_gap_rmse_m`.
- `DemandProfile`: `steps: list[tuple[float, float]]` (t_s, inflow veh/s),
  provenance.

The `v1_legacy` FD preset (documented default, NOT calibrated):
`v_f = 100 km/h`, `rho_jam = 160 veh/km`, `w = −20 km/h` (converted to SI via
`flowstate_core.units`). Lives at `flowstate_core.constants.V1_LEGACY_FD`.

## 6. RNG discipline (`flowstate_core.rng`)

- `spawn_seeds(master_seed: int, n: int) -> list[int]` via `np.random.SeedSequence`.
- `make_rng(seed: int) -> np.random.Generator`.
- SUMO gets `seed % 2**31`. Never `random.*`, never unseeded generators.

## 7. Metrics (`validation.metrics`)

`compute_metrics(run_dir) -> Metrics` (dataclass): `throughput_veh_h` (at
reference cross-section), `mean_tt_s`, `p90_tt_s`, `sigma_v_spatial_ms`,
`sigma_v_temporal_ms` (definitions in docstrings), `fuel_ml_per_veh_km`,
`wave_count`, `wave_speed_kmh` (mean of backward fronts), `wave_amplitude_ms`.
`aggregate(list[Metrics]) -> dict[str, CI]` with
`CI = (mean, lo95, hi95, n)` — t-distribution CIs over replicates.
Headline reporting requires `n >= 20` (CLAUDE.md §0.6); `aggregate` sets
`underpowered=True` flag when n < 20.

## 8. Testing conventions

- Tests live in `tests/test_<package>/`; golden summaries in `tests/golden/`.
- Markers: `integration` (real SUMO), `slow` (sweeps, >5 min). CI runs
  `-m "not slow"`.
- Property tests via hypothesis for: densities ∈ [0, ρ_jam], speeds ≥ 0,
  controller outputs ∈ [0, U], config round-trip. Exception: `pi_saturation`
  ranges over `[0, U + v_catch]` — Stern et al. (2018) Eq. (3) allows a bounded
  catch-up above the desired speed so the AV can close a gap.
- Every stochastic test passes an explicit seed.


## API and dashboard contract additions (2026-09-16)

- `POST /api/v1/sweeps`: `include_baseline: bool = false` appends exactly one
  uncontrolled cell (penetration 0, compliance 1.0, controller null) after
  the grid, none when the grid already holds an uncontrolled cell (penetration
  0 in `penetrations` or null in `controllers`); it counts toward the 200-cell
  ceiling and appears last in `cells` (the per-controller form of 2026-09-16
  ran identical simulations and produced two baseline groups). Request models
  (`RunCreateRequest`, `SweepCreateRequest`, `ReportCreateRequest`) forbid
  unknown fields (422).
- `POST /api/v1/scenarios`, `/runs` (overrides) and each `/sweeps` cell: 422
  with `type: "path_outside_roots"` when `network.osm_file`,
  `fleet.idm_calibration` or `fleet.heavy.idm_calibration` resolves outside
  the results root, the uploads directory, `FLOWSTATE_DATA_DIR`, or the
  repository's `artifacts/` and `data/`; relative values resolve against the
  repository root (`api.settings.REPO_ROOT`), never the process CWD.
- HTTP 413 on `/api/` bodies above `FLOWSTATE_MAX_BODY_MB` (default 8) and
  calibration uploads above `FLOWSTATE_MAX_UPLOAD_MB` (default 200); the
  detail names the limit and the variable. `POST /calibrations/{kind}`
  `params` are validated by `api.schemas.CalibrationParams` (extra forbidden,
  bounds in its field definitions); stored `params` hold only caller-set keys.
- `RunOut.seeded` / `MetricsOut.seeded` equal `ScenarioConfig.seeded`
  (perturbation or closures). `RunOut.error`, `SweepOut.error`,
  `ReportOut.error` carry an exception chain (`Type: message`, `caused by:`
  lines, at most 4000 characters) with no traceback frames, no server source
  paths, no file contents and no pydantic input values; a path under the
  results root may appear, as it does in `report_path`.
- Job identity: the RQ job id equals the store row id for every job
  (`api.jobs.JobQueue.enqueue(..., job_id=)`); `api.jobs.reconcile_store`
  repairs `queued`/`running` rows whose job is gone (worker start, RQ
  maintenance, API start). Per-replicate `metrics.json` (schema 1) is written
  by the worker at the end of every run (`api.results.precompute_run_metrics`)
  and is the only source read by `GET /sweeps/{id}`.
- Dashboard metric keys (`frontend/src/lib/metrics.ts` `METRIC_DEFS`) must
  equal the `validation.metrics.Metrics` field names; a vitest contract test
  parses the dataclass and fails on a rename without a matching entry.
- Golden files (`tests/golden/*.json`, schema 1): the `run` section gains the
  exact-compared counters `n_collisions`, `n_heavy`, `n_hov`,
  `n_meter_releases`, `n_scripted_merged`, `n_scripted_forced`; new cases
  for closures, heavy vehicles, the merge models, metering and managed lanes.

- Hardening round two (2026-09-17): `FLOWSTATE_API_KEYS` (comma-separated
  extra keys; `Settings.api_keys: tuple[str, ...]`, `Settings.api_key` is the
  primary/first key); `X-Request-Id` on every response (client ids of at most
  64 `[A-Za-z0-9._-]` characters reused); 500 bodies are
  `{"detail": "internal error; request id <id>"}`; over-cap chunked bodies
  answer 413 `{"detail": "request body exceeds the limit of <cap> bytes for
  <path> (FLOWSTATE_MAX_BODY_MB)"}`; log streams `api.access` (one INFO line
  per request) and `api` (tracebacks). `microsim.paths` (`ROOTS_ENV_VAR =
  "FLOWSTATE_WORKER_PATH_ROOTS"`, `env_path_roots`, `effective_roots`,
  `within_roots`, `ensure_within_roots`, `outside_roots_error`); keyword-only
  `allowed_roots` on `microsim.vehicles.resolve_calibration_path`,
  `load_idm_calibration` and `microsim.networks.osm_import` (None = the
  process default from the environment; empty tuple = unrestricted).
  Golden files: `tests/golden/macro_corridor_workzone.json`; the macro golden
  module's exact keys are `wave_count, n_cells, n_closures, n_closure_cells`.
- API after the walkthrough (2026-09-17): `FLOWSTATE_CORS_ORIGINS`
  (`Settings.cors_origins`, default `api.settings.DEFAULT_CORS_ORIGINS`);
  `GET /api/v1/reports?limit=` (1..200, newest first, `list[ReportOut]`;
  `Store.list_reports`); `ReportOut.report_path` is relative to the results
  root (`reports/<id>/report.md`), never a filesystem path or URL, content
  comes from `/reports/{id}/markdown|pdf|archive`; `POST /reports` answers
  422 for a run set mixing micro and macro tiers (detail names the macro run
  ids); `PresetOut.preset: true`, `ScenarioOut.preset: false`; `CIOut.reason:
  "no_observations" | null` with `underpowered = false` when `n == 0`.
- Metrics and runner after the second audit (2026-09-17): `validation.metrics.Metrics`
  gains `n_travel_time_veh: int = 0` (last field); `compute_metrics(...,
  warmup_s: float | None = None)` (None = the run's `config.sim.warmup_s`
  via `warmup_from_meta`, 0.0 = whole record); `default_travel_span(traj)`
  = (min observed x, median per-vehicle furthest x); `mean_tt_s`/`p90_tt_s`
  are NaN below two completers. `generate_report` measures the wave-speed
  criterion with the profile's own detector on its bins and derives one
  travel-time span per run set. Micro `meta.json` gains
  `output_hz_realized` (Hz; the cadence after step rounding, which is what
  `edges.parquet` density and flow are scaled by) and is written last: a
  replicate directory without it is incomplete (`microsim.runner.is_run_complete`,
  `require_complete_run`). `FDCalibration.schema_version = 2` with defaulted
  `n_rows_input`, `dropped_rows`, `fit_rows`, `bounds` fields; `fit_triangular_fd`
  gains `max_fit_rows` (50,000, seeded subsample), `bounds` (`FDBounds`,
  per-lane physical ranges) and refuses an implausible diagram.
- API after the second audit (2026-09-17): `ReportCreateRequest.profile`
  (default `fhwa_default`, one of `validation.criteria.CRITERIA_PROFILES`),
  `ReportOut.profile`; `GET /api/v1/criteria` (profiles with source,
  thresholds, wave detector, `default`); OpenAPI security scheme
  `ApiKeyAuth` (`X-API-Key`); `include_baseline` appends exactly one
  `(penetration 0, compliance 1.0, controller null)` cell, none when the grid
  already holds an uncontrolled cell; identical grid triples are
  de-duplicated; `api.results._METRICS_CACHE_SCHEMA = 2` (per-replicate
  `metrics.json` written under schema 1 are recomputed); the image copies
  `artifacts/` and `data/osm/`.
- Closing the regression review (2026-09-17): micro `meta.json.corridor`
  (`kind: ring | corridor | osm`, `total_length_m`, `x_first_edge_m`, the
  linear-x geometry as built; a ring's numbers describe the loop, not a
  span); `api.results._METRICS_CACHE_SCHEMA = 3`; `analysis_span` for
  `kind == "osm"` is `(x_first_edge_m, total_length_m - exit_buffer_m)` when
  the boundary schedule is hosted on the last edge, else `total_length_m -
  CORRIDOR_EXIT_MARGIN_M`; `CalibrationParams` gains `max_fit_rows`,
  `max_dropped_fraction`, `max_speed_ratio_factor`,
  `max_out_of_range_fraction` (an explicit `max_fit_rows: null` means no
  row cap; every other null means unset).
- `HeavyVehicleSpec.lane_shares: list[float] | None = None` (2026-09-17,
  candidate 4 of docs/MERGE_ROUND6_PLAN.md): left-to-right departure-lane
  distribution of heavy vehicles (normalised at use; length equal to the
  corridor's lanes or to `entry_lane_shares`; refused on a ring). Drawn from
  an independent seeded stream (`microsim.vehicles.heavy_lane_stream`,
  `HEAVY_LANE_SPAWN_KEY`), so with the field unset every plan is byte-identical
  and with it set only heavy vehicles' lanes change. Hash-neutral when unset.
  `FleetPlan.heavy_lane_shares` and `meta.json.heavy_lane_shares` record the
  effective shares; `FleetPlan.depart_lane == -1` now also means "the route
  writer's round-robin scheme". Helper
  `microsim.vehicles.heavy_lane_shares_from_artifact` turns
  `artifacts/i24_heavy_by_lane.json` and the entry flow shares into
  p_lane ∝ flow_share_lane × heavy_fraction_lane (I-24: 0.048 / 0.172 / 0.459 /
  0.321).

## Detector observations (`flowstate.observations/1`) and demand (`flowstate.demand/1`) — 2026-09-22

Corridor onboarding from public detector archives (WP-A; CLAUDE.md §6.1/§6.3).

**Tidy detector frame** (`calibration.loaders.detector_csv`,
`DETECTOR_COLUMNS`) — what every detector loader returns and what the generic
CSV upload contains, one row per station × window: `timestamp` (ISO-8601 with
offset, the window START, on one regular grid), `station` (str), `flow_veh_h`
(station TOTAL across the mainline lanes, NaN when the window was not measured
well enough), `occupancy_pct`, `speed_ms`, `lanes` (int; 0 = not stated),
`kind` ∈ {`mainline`, `on_ramp`, `off_ramp`}, `x_m` (optional corridor
position). `load_detector_csv(path, *, column_map=None, speed_unit="ms",
occupancy_unit="pct", kind_default="mainline")` accepts any spelling through
`column_map` (canonical fields `timestamp, station, flow, occupancy, speed,
lanes, kind, x_m`); `write_detector_csv(df, path)` is its inverse.
`detector_interval_s(df)` validates the grid: the shortest gap is the
interval and every other gap must be a whole multiple of it (gaps are
expected — an unmeasured window, the overnight break between fetched dates;
a *second* interval is refused). Missing is NaN and is never filled in.

**Stations table** (§2 of the contract; `mndot.stations_table`): `station,
label, lat, lon, x_m, lanes, kind, speed_limit_ms, detectors`.

**MnDOT source** (`calibration.loaders.mndot`): `MetroConfig.load(path_or_gz)`
parses the IRIS `metro_config.xml(.gz)` into corridors → `Station` (id, label,
lat, lon, lanes, `speed_limit_ms`, `x_m` = cumulative great-circle distance
along the r_node chain, mainline detector names) and `RampNode`
(Entrance/Exit, detectors by IRIS category; `Merge` → `on_ramp`, `Exit` →
`off_ramp`); abandoned detectors are excluded. `fetch_detector_day(detector,
date, endpoint, *, cache_dir, session=None)` reads one detector-day of the
30-second archive (`counts` veh/30 s, `occupancy` percent, `speed` mph;
`null` = missing, HTTP 404 = an empty series) through a JSON cache.
`station_frame(config, corridor, stations, dates, *, window_s=300,
cache_dir, max_workers=8)` aggregates to the tidy frame. **Validity rule:** a
window is valid when ≥ 80% (`MIN_SAMPLE_FRACTION`) of the station's mainline
30-second samples are present, otherwise flow, speed and occupancy are all
NaN; in a valid window the count is scaled to the full window in two stages —
per detector by its own presence, then by
`n_detectors / n_detectors_with_data` — speed is the mean of the present lane
speeds (a 0 mph sample is no measurement, not a measured standstill) and
occupancy the mean of the present samples.

**Observations artifact** (`calibration.observations.Observations`,
`schema: "flowstate.observations/1"`): `corridor`, `source`, `window_s`,
`t0_local`, `duration_s`, `n_windows`, `aggregation`, `stations` (id, label,
x_m, lanes, kind, lat, lon, speed_limit_ms), `flows_veh_h` / `speeds_ms` /
`occupancy_pct` (station id → per-window list), `spread`
(`flows_veh_h_sd`, `speeds_ms_sd`, sample sd across dates) and `quality`
(`fraction_valid`, `n_dates`). Simulation t=0 is `t0_local`; window `k` is
`[t0 + k·window_s, t0 + (k+1)·window_s)` and starts at `k·window_s` in
simulation time; the span must lie inside one local day. NaN means "not
observed" and is written as JSON `null` — no value is ever invented.
`from_frame(df, stations, *, window_s, t0_local, duration_s, corridor,
source)` averages the dates per window (a row inside the span that is off the
window grid is an error, not a silent drop); `to_json`/`from_json` round-trip.
Derived views: `hourly_link_flows(obs) -> DataFrame(x_ref_m, window_start_s,
flow_veh_h, station)` for GEH, hours reported only when **all** of their
windows are valid at that station; `segment_speed_matrix(obs) -> (windows ×
stations array, station x list)` for RMSPE; `coverage(obs)` per station.

**Demand artifact** (`calibration.demand.DemandArtifact`, `schema:
"flowstate.demand/1"`): `corridor`, `observations` (path), `upstream_station`,
`step_s`, `inflow_steps` `[[t_start_s, veh_s], ...]`, `ramps` and `method`.
`demand_from_observations(obs, upstream_station, *, step_s=300)` is the
boundary inflow in SI; `ramp_flows_from_observations(obs, ramps)` gives each
ramp `inflow_steps` (on) or `exit_fraction_steps` (off) with `method:
"detector"` where the ramp has its own station, else `"conservation"`
(`q_on = max(0, q_down − q_up)`, `f_off = max(0, (q_up − q_down)/q_up)`
between the bracketing mainline stations). A step no window was observed in
is omitted, so the previous step holds across it, and the count is recorded
(`coverage.n_steps_carried`, per-ramp `n_steps_carried`); a station with
nothing observed is an error, not a filled profile. The scenario YAML carries
these numbers literally (`network.inflow`, `network.ramps[*]`), the artifact
records provenance.

**API** (`POST /api/v1/calibrations/{kind}`): `CalibrationParams` gains
`column_map`, `kind_default`, `occupancy_unit: "pct"` (alias of `"percent"`;
the PeMS loader is given its own spelling) and the demand options `window_s`,
`t0_local`, `duration_s`, `upstream_station`, `stations`, `ramps`, `corridor`
(extra keys still refused with 422; the caps are `MAX_COLUMN_MAP_ENTRIES`,
`MAX_WINDOW_S`, `MAX_OBSERVED_DURATION_S`, `MAX_STATIONS`). `loader:
"detector_csv"` fits an FD from the tidy frame (per-lane flow; density from
`q/v`, else from occupancy through the PeMS g-factor). The new kind
`"demand"` (`api.calibration_jobs.demand_calibration_job`) writes
`observations.json` and `demand.json` into the calibration's artifact
directory; `CalibrationOut.artifact_paths` carries both
(`{"observations": ..., "demand": ...}`), `artifact_path`/`artifact` remain
the observations artifact. `scripts/mndot_fetch.py` is the CLI form
(`stations.csv`, `detectors.csv`, `observations.json` + a coverage print).

## Scoring a run set against observations — 2026-09-23

The observed side of the validation report (WP-B; CLAUDE.md §7.1/§7.4).

**Reader** (`validation.observed`, no dependency on `calibration` — the two
packages agree on the file, not on an import). `ObservedCorridor.from_json(
path)` parses a `flowstate.observations/1` artifact; `from_dict` refuses a
different `schema`, a series of the wrong length, an `n_windows · window_s`
that disagrees with `duration_s`, and two mainline stations at the same
position. Views: `mainline_stations()` / `mainline_x_refs()` (positioned
`kind: mainline` stations, ordered by x — ramps and unpositioned stations take
part in no comparison), `segment_bins() -> [(x_start_m, x_end_m)]` (each
station owns the span to the midpoints with its neighbours; outer half-spans
mirror the adjacent spacing; needs ≥ 2 stations), `speed_matrix(windows=None)`
(`[window][station]`), `analysis_windows(warmup_s, duration_s)` (windows wholly
inside the measurement window), `hourly_link_flows()` (rows `x_ref_m,
window_start_s, flow_veh_h, station`; hours aligned to window 0, reported only
when **every** window of the hour is valid at that station; `window_start_s` is
simulation time `k · window_s`) and `coverage()` (`flow_fraction`,
`speed_fraction` over the mainline station-window grid).

`score_run_against_observed(trajectories, observed, *, warmup_s, duration_s,
x_offset_m=0.0) -> ObservedScores(geh_values, n_link_hours, rmspe,
n_speed_cells, segment_speeds_sim, segment_speeds_obs, windows)` scores one
replicate: GEH via `metrics.link_hour_geh` on hourly volumes, RMSPE on the
simulated mean sampled speed per (window, segment) cell. `x_offset_m` maps the
observed origin onto simulation `x` (a generated corridor's insertion buffer,
`microsim.demand_adapter.corridor_x_offset_m`; 0 for ring/OSM). NaN
observations, unsampled cells and zero observed speeds are skipped and
counted. A mainline station whose cross-section falls outside `[x.min(),
x.max()]` of the trajectory frame is excluded from **both** comparisons rather
than scored against a simulated flow of zero, and is reported on
`ObservedScores.n_stations_outside_span` / `.stations_outside_span`.
`pool_scores(observed, scores, path=...)` pools GEH across
replicates, means the RMSPE, means the simulated matrices and builds the
`ObservedProvenance` the report prints (which carries the excluded stations
and a `note`); it refuses replicates whose speed matrices differ in shape or
that were scored over different observation windows.
`no_comparison_provenance(observed, path=...)` returns a zero-count
provenance whose `note` says why when the artifact has fewer than two
positioned mainline stations, and `None` otherwise — `report_job` uses it to
leave the two rows NOT EVALUATED instead of failing the report. `metrics.link_hour_geh` gained
`sim_span=(t_lo, t_hi)`: the recorded span the observed windows must lie
inside, so a run whose last output sample falls short of its nominal end does
not lose its final hour. `metrics.ci(values) -> CI` is `aggregate`'s
replicate-interval convention for one series.

`validation.battery` holds what the CLI and the API job share:
`load_meta`, `measurement_window(meta) -> (warmup_s, duration_s)`,
`read_trajectories`, `score_replicate(run_dir, observed, *, x_offset_m)`,
`replicate_wave_speed_kmh(run_dir, detector)` and `mean_finite`.

**Report.** `validation.report.generate_report(..., observed=ObservedProvenance
| None)` adds an **Observed data** block (artifact, corridor, provider, dates,
url, aggregation, `t0_local`, window, stations, windows, windows compared,
flow/speed coverage fractions, link-hours and speed cells compared, replicates
scored) and a limitations bullet; the template body still contains no
free-text numerals. `geh_values` / `rmspe_value` keep their meaning: supplied
→ the row is evaluated, absent → NOT EVALUATED.

**API.** `ReportCreateRequest.observations_path: str | None` — a server-side
artifact path confined to `Settings.config_path_roots` (422 with
`type: "path_outside_roots"`, 404 when missing; relative values resolve
against the repository root). `report_job` then scores every completed micro
replicate of every run in the set, pools the GEH values, means the RMSPE and
passes them plus the provenance into `generate_report`. `ReportOut` gains
`observations_path` and `observed` (the provenance dict, null until the report
is done); the `reports` table gains `observations_path` and `observed_json`
(only `observed_json` is nulled by a re-claim — a requeued report re-scores the
same artifact, so its path is part of the request, not of the attempt).

**Corridor battery artifact** (`scripts/corridor_battery.py`, schema
`flowstate.corridor_validation/1`): `created_at`, `scenario`, `corridor`,
`config_hash`, `seeds`, `replicates`, `wall_s`, `versions`,
`criteria_profile {name, source}`, `criteria` (the `CriteriaResult` rows),
`observations` (the provenance dict), `x_offset_m`, `geh` (pooled values,
threshold, pooled pass fraction, CI over the per-replicate pass fractions),
`rmspe` (mean + CI + pooled cell count), `wave_speed` (profile detector, mean,
CI, replicates with a backward front), `metrics_ci` (per-metric mean/lo95/hi95
/n/underpowered), `per_seed` (seed, run dir, per-seed scores and metrics),
`ring` (a `validation.ring_benchmark` block or null), `report_path` and
`notes`. Each replicate directory also carries `metrics.json` and
`observed_scores.json`, which `--criteria-only` re-scores from without
re-simulating; trajectories are pruned to the first seed unless
`--keep-trajectories`. Since 2026-09-24 the per-seed files are written by
`validation.battery.analyse_replicate` in a scoring pool (`--score-procs`),
the report's speed contour is the first seed's only, and its per-replicate
numbers come from the stored scoring, so the report needs no other seed's
trajectory and `--criteria-only` regenerates it after pruning.

## Calibrated screening tier: FD provenance and macro options — 2026-09-23

- **`meta.json["fd"]` (macro tier)** gains `source`, `artifact`, `rho_c` and
  `q_max` beside the existing `preset`, `v_f`, `w`, `rho_jam`. `preset` is
  `"v1_legacy"` (no diagram supplied), `"artifact"` (read from an
  `FDCalibration`) or `"custom"` (a caller-supplied diagram); `source` is the
  artifact path or `"v1_legacy preset"`, `artifact` is the path or `null`.
  Parameters are recorded *as the solver used them*, so a multi-lane corridor
  shows the effective single pipe's `rho_jam`/`rho_c`/`q_max`. An
  `fd_artifact=` without an `fd=` is dropped with a note rather than claiming
  a provenance the run does not have.
- **`meta.json["macro_options"]`** records the effective `{dx_m,
  bottleneck_variant}` of every macro replicate.
- **`POST /api/v1/runs` and `POST /api/v1/sweeps`** accept
  `macro: {dx_m?, bottleneck_variant?}` (`api.schemas` re-exports
  `flowstate_core.config.MacroOptions`, `extra="forbid"`). It is applied to
  the effective config like `tier`/`replicates` — so it is hashed with the
  run, stored on the row, and survives a reconciliation re-enqueue — and every
  sweep cell inherits it. An unknown key or variant, or `dx_m <= 0`, is a 422.
- **`fd_calibration` is a config file field**: `POST /scenarios`, `/runs`
  overrides and every `/sweeps` cell answer 422 `type:
  "path_outside_roots"`, `loc: ["fd_calibration"]` when it escapes the
  allow-listed roots. The worker re-checks it against the same roots before
  opening the artifact (`api.jobs._load_fd_calibration`, the macro-tier
  counterpart of `microsim.vehicles.load_idm_calibration`; it does not import
  `microsim`, which would pull SUMO into a macro worker). A named-but-absent
  artifact fails the run (`FileNotFoundError`) — never a silent fallback to
  the preset.
- **`SweepOut.tier`** (`"micro" | "macro" | null`): the tier every cell runs
  on, read from the stored cell configs so it is available before the fan-out
  job has created a run. `null` only for a sweep with no cells.
- **`MetricsOut.fd_source`** (`str | null`): `meta.json["fd"]["source"]` of a
  macro run's first replicate; `null` for micro runs and when the meta cannot
  be read (the dashboard then says the source is unknown rather than naming
  one).
- **Dashboard**: the sweep launcher sends `tier` explicitly (a micro/macro
  select; omitting it ran macro requests on the scenario's own tier), the
  launcher, the confirmation dialog and the sweep matrix carry a persistent
  "Screening tier (CTM) — not a validation result" banner for macro sweeps,
  and the run detail of a macro run states the FD source, flagging
  `v1_legacy preset` as uncalibrated. `frontend/src/api/types.ts` mirrors
  `MacroOptions`, `ScenarioConfig.fd_calibration`/`macro`, `SweepDetail.tier`
  and `RunMetrics.fd_source`.

## Sweep strategies: the infrastructure axis — 2026-09-23

- **`flowstate_core.strategies`** is the one implementation of the patch:
  `Strategy = "none" | "vsl" | "alinea" | "vsl+alinea"`, `STRATEGIES` in that
  order, and `apply_strategy(cfg, strategy, rho_target_veh_km)` mutating a
  serialized `ScenarioConfig` in place — `vsl` sets `av.vsl =
  "vsl_threshold"` (leaving `av.vsl_params` as configured), `alinea` writes
  `meter = {controller: "alinea", params: {rho_target_veh_km}}` on every
  `network.ramps` entry with `kind: "on"`. It is idempotent and additive:
  `none` never strips a VSL or a meter the scenario itself configures.
  `StrategyError` (a `ValueError`) names the three refusals: unknown
  strategy, an ALINEA strategy without a target, an ALINEA strategy on a
  network with no on-ramp. `scripts/corridor_sweep.py` and
  `POST /api/v1/sweeps` both call it, so a CLI cell and an API cell of one
  grid point are the same configuration and share a `config_hash`.
- **`POST /api/v1/sweeps`** gains `strategies: ["none" | "vsl" | "alinea" |
  "vsl+alinea"]` (default `["none"]`, at most `MAX_SWEEP_AXIS_VALUES`
  entries) and `alinea: {rho_target_veh_km}` (`extra="forbid"`, `> 0`).
- **Cell set** (`SweepCreateRequest.grid_cells()`, fan-out order): the
  product `strategies × penetrations × compliances × controllers`; then one
  uncontrolled cell per requested strategy other than `none`
  (`strategy_cells()`: penetration 0, compliance 1, controller `null` — the
  API's form of `scripts/corridor_sweep.py`'s `strategy_<s>` cells, so an
  infrastructure strategy can be priced without any controlled vehicle);
  then the `include_baseline` cell (penetration 0, compliance 1, controller
  `null`, strategy `none`), skipped when `none` is among the strategies and
  the product already holds an uncontrolled cell. Repeated tuples collapse,
  first occurrence winning. `MAX_SWEEP_CELLS` counts all three groups, from
  the four list lengths, before any cell is built.
- **ALINEA target resolution**: `alinea.rho_target_veh_km` when given;
  otherwise `fd.rho_c` of the scenario's `fd_calibration` artifact converted
  to veh/km (`flowstate_core.units.veh_m_to_veh_km`), read in the request
  path after the base config is path-confined. With neither, 422 naming both
  sources; an unreadable artifact, or a network with no on-ramp, is likewise
  422 — the service never invents a metering target (CLAUDE.md §0.1).
- **`SweepCellOut.strategy`** (`"none"` default) echoes each cell's strategy;
  sweeps stored before this axis have no `strategy` key in their grid rows
  and report `none`.
- **Report**: a `## Strategy comparison` section — one table, one row per run
  group (`validation.report.group_label`, baseline first), one column per
  metric in `validation.report.COMPARISON_METRICS` (throughput, mean travel
  time, σ_v temporal, fuel per veh-km, wave count). Each cell is `mean [lo95,
  hi95]` and, off the baseline row, `· Δ mean [lo95, hi95] resolved|
  unresolved` from the same `contrast()` the per-metric contrast tables use
  (seed-paired or Welch, named in the row's first cell). Rendered from
  `_comparison_rows(groups, baseline)`; shown only for a run set with at
  least two configurations, and without the Δ half when the set has no single
  baseline group.
- **Dashboard**: the sweep launcher has a strategies multi-select (default
  `none`) and an ALINEA target field shown only when a metering strategy is
  selected — empty means "read it from the scenario's FD calibration", and a
  non-numeric entry is refused client-side rather than posted. The matrix has
  one row per (penetration, strategy) pair, an infrastructure-only row per
  strategy beside the baseline row, and names the strategy in each cell's
  label. `frontend/src/api/types.ts` mirrors `SweepStrategy`,
  `CreateSweepRequest.strategies`/`alinea` and `SweepCell.strategy`.
- **Sweep summary `diagnostics` (2026-09-24)**: every cell of a
  `scripts/corridor_sweep.py` summary carries an additive `diagnostics` key,
  `{n_runs_with_meta, ramp_meters, weave_sections}`, read from the runs'
  archived `meta.json` (a run without one contributes nothing; the two maps
  are empty and `n_runs_with_meta` is 0 on an archive that has none).
  `ramp_meters[<ramp>]` = `{controller, n_released, n_passed_unstoppable,
  share_passed_unstoppable}` and `weave_sections[<on-ramp>]` =
  `{n_entered, n_exited, n_reached_section_exiting, n_forced,
  n_forced_deferred, n_unfinished, wait_s_mean}` and, since 2026-09-24
  block 3, the follower-cooperation counters `n_cooperations`,
  `mean_follower_decel_ms2`, `n_changer_eased` (`WEAVE_FIELDS`), each value
  a seed mean with `lo95`/`hi95` (t-interval), `n` and `underpowered`, the
  share being `n_passed_unstoppable / (n_released + n_passed_unstoppable)`
  per seed (a seed on which the meter saw no vehicle is left out of the
  share). A meta written before a counter existed contributes nothing to
  that counter's interval (`n` = 0, `mean` null), as a section without a
  wait does to `wait_s_mean`. Every pre-existing key of the summary is
  unchanged.

## Corridor onboarding from the dashboard — 2026-09-23

**`POST /api/v1/corridors`** (202, multipart) onboards a freeway corridor from
public data in one asynchronous job (CLAUDE.md §3.2.4, §6.3). Form fields:

| field | required | default | meaning |
|---|---|---|---|
| `name` | yes | — | scenario name, `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`; it becomes the results directory and the `scenarios/<name>.yaml` preset |
| `bbox` | yes | — | `"south west north east"` (commas and/or spaces), WGS84 |
| `bearing_deg` | yes | — | direction of travel, compass degrees (270 = westbound) |
| `upstream_station` / `downstream_station` | yes | — | mainline detector ids at the two ends of the analysed span |
| `detectors` | yes | — | the tidy detector CSV (see "Detector observations") |
| `stations` | yes | — | station inventory CSV: `station,label,lat,lon,lanes,kind` |
| `column_map` | no | `{}` | JSON object of string → string mapping the canonical detector fields to the upload's own column names |
| `idm_calibration` | no | `null` | server-side `IDMCalibration` path, confined to the allow-listed roots |
| `window_s` | no | `300` | observation window [s]; must equal the upload's interval |
| `t0_local` | no | `"06:00"` | local wall-clock time of simulation t = 0 |
| `duration_s` | no | `14400` | analysed span and simulated duration [s] |
| `warmup_s` | no | `1800` | metrics warm-up [s]; `0 ≤ warmup_s < duration_s` |
| `source` | no | — | provenance string recorded on the observations artifact |

Refusals: 422 for a malformed name, bbox (four numbers, `south < north`,
`west < east`, WGS84 range), bearing, window/duration/warm-up or
`column_map`, and for an `idm_calibration` outside
`Settings.config_path_roots` (`type: "path_outside_roots"`,
`loc: ["body", "idm_calibration"]`); **409** when a preset of that name
already exists — a corridor is never onboarded over a scenario other runs
were launched from — and **409** when an onboarding of that name is still
`queued` or `running`: the store takes the name when it creates the row
(`api.store.CorridorNameTaken`), so two requests for one name never both
dispatch a job onto the same preset and extract paths, and the name is free
again as soon as the first settles (a failed onboarding removes what it
installed); 413 above `FLOWSTATE_MAX_UPLOAD_MB`.

**Stages** (`api.schemas.CORRIDOR_STAGES`, recorded on the row as it runs):
`extract` (Overpass, motorway ways only) → `network`
(`microsim.scenarios.corridor_from_bbox`) → `observations`
(`Observations.from_frame` over the uploaded CSV) → `demand`
(`calibration.onboarding.calibrate_scenario`) → `install` → `done`.

**`GET /api/v1/corridors/{id}`** answers `CorridorOut`:

- `status` (`queued`/`running`/`done`/`failed`), `progress`
  `{stage, completed_stages, total_stages}`;
- `scenario_id` — the stored scenario `POST /runs` takes;
  `preset_filename` — the `scenarios/<name>.yaml` it was installed as;
  `config_hash`;
- `observations_path` — the server-side `flowstate.observations/1` artifact,
  ready to pass to `POST /reports` as `observations_path`;
- `corridor_dir` — the bundle directory *relative to the results root*
  (`corridors/<id>/`, holding `scenario.yaml`, `stations_x.csv`,
  `observations.json`, `demand.json`, `extract.osm`, `summary.txt`) — an
  identifier, not a URL;
- `summary` (`CorridorSummaryOut`): `chain_length_m`, `n_chain_edges`,
  `lanes_profile` `[(x_start_m, x_end_m, lanes)]`, `n_ramps`,
  `stations_placed` / `stations_rejected` (`{station, x_m, offset_m}`),
  `stations_without_chain_x`, `inflow_peak_veh_h`, `ramps`
  (`{name, kind, x_m, method, peak, unit, station}` — `method` is `detector`,
  `detector_scaled`, `conservation` or `zero_outside_observed_span`),
  `residuals`, `zeroed_ramps`, `unmatched_detectors` and `lines` (the plain
  text of `summary.txt`);
- `error` + `error_kind` (`corridor_<stage>`) on failure, as plain messages
  with no frames or file contents.

The OSM extract is persisted at `FLOWSTATE_DATA_DIR/osm/<name>.osm` (the
results root when no data directory is mounted) and the installed scenario's
`network.osm_file` points at it, so the preset re-imports the same map the
corridor was measured on and the path stays inside the allow-listed roots.

**Library.** `calibration.onboarding.calibrate_scenario(scenario, *,
observations, stations_x, net_path, upstream, downstream, idm_calibration,
warmup_s, match_radius_m=350, alive_veh_h=30) -> OnboardingResult` holds the
derivation (station positions onto the chain, ramp matching, the bracket
balance, the downstream boundary, the demand artifact). It writes no file:
`OnboardingResult.demand` carries empty `observations`/`scenario` fields for
the caller to fill. `scripts/corridor_demand.py` and
`api.onboarding_jobs.corridor_onboarding_job` are both thin callers, so the
CLI and the dashboard derive identical numbers.

**Dashboard.** `frontend/src/views/OnboardView.tsx` ("Onboard corridor" in
the rail) posts the form, polls the stages, shows what was discovered and
derived, and offers "Run 20 seeds" (`POST /runs`, `replicates: 20`) and then
"Report against observations" (`POST /reports` with `observations_path`, once
a run has finished). `createCorridor(FormData)` / `getCorridor(id)` in
`frontend/src/api/client.ts`; `createReport` gained an optional fourth
argument for `observations_path`. Onboarding is not validation: the summary
states what was measured and where each demand number came from, and the
report is what scores the corridor.

**`GET /api/v1/corridors` — 2026-09-23.** Lists the onboardings this server
holds, newest first (`?limit=`, default and maximum `MAX_CORRIDOR_LIST` =
200; `limit < 1` is 422). Each row is `CorridorRowOut` — `CorridorOut` minus
`summary` (`corridor_id`, `name`, `status`, `progress`, `scenario_id`,
`preset_filename`, `config_hash`, `observations_path`, `corridor_dir`,
`error`, `error_kind`, `created_at`) — because the summary belongs to the one
corridor being looked at and `GET /corridors/{id}` serves it. A failed
onboarding is listed with `scenario_id`/`observations_path` null rather than
hidden. `api.store.Store.list_corridors(limit)` is the query (rowid
descending, like `list_reports`). Dashboard: `listCorridors(limit?)` in
`frontend/src/api/client.ts` (`CorridorRow` in `types.ts`); OnboardView lists
the corridors and loads one into the run/report actions (re-reading the full
row, so the summary is the server's, not reconstructed); ReportsView's
launcher has a "Score against observations" select offering the finished
corridors' `observations_path` values plus a typed server path, sent as
`POST /reports`'s `observations_path`, with the API's refusal shown verbatim
beside the button. OnboardView's **Advanced** disclosure sends the optional
`column_map` (the six canonical detector fields, JSON only when one is
filled in), `idm_calibration` and `source` form fields. A service without the
list route answers 404 and both views say so rather than claiming an empty
history.

**Split audit — 2026-09-24.** Every onboarding (`corridor_from_bbox`, so
`scripts/onboard_corridor.py` and `POST /corridors` alike) audits each exit
leaving the chain against the OSM extract it was compiled from
(`microsim.split_audit.audit_splits(net_path, osm_path, corridor_edges)`).
Per connection from a corridor edge into a non-corridor edge it records the
**OSM side** of the leaving way — the signed lateral offsets [m] of the
link's first nodes (up to 300 m along it) from the continuing mainline way,
by cross product in local metres, negative = right of travel — and the
`turn:lanes` reading when the way is tagged (lanes listed left → right;
trailing lanes with `right` are a right exit, `through` on one makes it an
option lane); the **compiled side** — the `fromLane` indices feeding the exit
relative to the edge's lane count (`rightmost` / `leftmost` / `middle` /
`all`; SUMO lane 0 is the rightmost), the option lanes that also continue,
and whether the edge gained a lane over its OSM `lanes` tag; and a
**verdict**: `ok`, `wrong_side` (drawn right, compiled from the left, or the
reverse), `added_lane_wrong_side` (the offending lane was added by
`--ramps.guess`), or `unknown` (no continuing mainline way found, or no
usable geometry). Geometry decides the expected side; the tag stands in only
when the geometry is unknown. Each defect carries its `remedy` in the
engine's terms: an `OSMNetwork.patch_files` connection patch restating the
split (`connection_patch_lines`: the exit's lanes on the drawn side, every
other lane continuing in order, an option lane continuing at its own index —
the lines `data/osm/mndot_i94_wb_stpaul.splits.con.xml` states) for
`wrong_side`, `--ramps.unset <edge>` (`ramps_unset_edges`) for
`added_lane_wrong_side`. Stored beside the lane check: `CorridorBuild.split_audit`
(a tuple of `SplitFinding`; `split_defects()` filters), the `splits` block of
`CorridorBuild.summary()` / `summary.txt`, and `CorridorSummaryOut.split_audit`
(`CorridorSplitFindingOut`, additive, empty for corridors onboarded before
this date). Reported, never enforced by the job; the CLI's
`--fail-on-split-defect` exits 4 and `--write-split-patch PATH`
(`microsim.scenarios.apply_split_fixes`) writes the patch, adds it and the
`--ramps.unset` entries to the scenario, re-imports the network with them into
the build's net directory and audits again before the YAML is written. On the
committed I-94 WB extract compiled without its fixes the audit reports exactly
the two §9 defects (`45608485 → 18207912` wrong_side, lanes 3–4 of 5 for a
link 8–9 m to the right; `1001426896 → 82150350` added_lane_wrong_side, lane
3 of 4 for a link 1–45 m to the right) and the 6th Street exit `42165869` as
a genuine left exit (`ok`); with the fixes, none
(`tests/test_microsim/test_microsim_split_audit.py`). A `wrong_side` verdict
on a ramp-split piece (`…-AddedOffRampEdge`) is patched by its load-time id
with the load-time lane count (`SplitFinding.load_time_lanes`, the piece
minus the guessed lane) and the edge is added to `--ramps.unset`
(`ramps_unset_edges`), since a patch is read before the split exists and
guessing would rebuild the piece over it; a restated split repeats, as
compiled, the connections into every other exit of the same edge
(netconvert drops the computed ones of an edge a patch names); the patch
comment never carries `--` (netconvert refuses it). Limits: an exit whose
link the extract does not carry is `unknown`; a lane guessing added under
`--ramps.no-split` (no piece) on a way without a `lanes` tag cannot be told
from a mapped lane.

**Onboarding defaults: ramp guessing and split fixes — 2026-09-24.** Every
onboarding (`microsim.scenarios.corridor_from_bbox`, so
`scripts/onboard_corridor.py` and `POST /corridors`) compiles with
`--ramps.guess --ramps.ramp-length 250` (`RAMP_GUESSING_OPTIONS`, the MnDOT
values) unless told not to: `corridor_from_bbox(ramp_guessing=False)`,
`--no-ramp-guessing`, form field `ramp_guessing=false` (additive, default
`true`). `with_ramp_guessing(extra, enabled)` puts the defaults in front of
the caller's `netconvert_extra` and skips each option the caller already
gave (as `--opt` or `--opt=value`), so nothing reaches `netconvert` twice
and a caller's own `--ramps.ramp-length` wins. After the split audit a
`wrong_side` / `added_lane_wrong_side` finding is fixed by default
(`split_fixes=True`, `--no-split-fixes`, form field `split_fixes=false`):
`apply_split_fixes` writes the connection patch beside the extract at
`default_split_patch_path(osm_file)` = `<extract stem>.splits.con.xml`
(`data/osm/<name>.osm` → `data/osm/<name>.splits.con.xml`; a bbox download
→ `<workdir>/net/extract.splits.con.xml`; the API job →
`<data root>/osm/<name>.splits.con.xml` beside the installed extract,
`api.onboarding_jobs.split_patch_path`, registered for the failure cleanup
like the extract, and registered only when no file was there, so a failed
job never deletes one it found; `--write-split-patch PATH` /
`split_patch_path=` choose another place, refused together with
`--no-split-fixes`, exit 2; a default-path patch already there whose
connection lines differ — another corridor's, from the same `--osm-file` —
is left alone and the new one goes to `<extract stem>.<scenario
name>.splits.con.xml`, `apply_split_fixes(fallback_path=)`), adds it to
`patch_files` and `--ramps.unset <edge>` to `netconvert_extra`, re-imports
and audits again. `CorridorBuild` keeps both audits — `split_audit` is the
network the scenario compiles, `split_audit_before_fixes` the one the fixes
were derived from (`None` when none were applied) — with
`split_fixes_applied`, `split_fixes`, `split_patch_file` and the
`ramp_guessing` property; `applied_line()` is the inventory's
`applied   ramp guessing on; split fixes: 2 applied, 0 remaining` (`off, N
remaining` when fixes were not asked for); `summary()` prints `splits before
fixes`, `splits`, then that line. `--fail-on-split-defect` exits 4 on the
final audit. `CorridorSummaryOut` gains, additively, `split_audit_before_fixes`
(`null` when no fix was applied), `ramp_guessing`, `split_fixes`,
`split_fixes_applied`, `split_defects_remaining`, `split_patch_file` and
`applied`; corridors onboarded before this date read as `ramp_guessing:
false`, `split_fixes_applied: 0`. On the committed I-94 WB extract under the
defaults: 8 exits, 2 defects before the fixes, 0 after, `netconvert_extra`
`--ramps.guess --ramps.ramp-length 250 --ramps.unset 1001426896`, the
generated patch's connection lines equal to the committed file's; on the
synthetic API fixture, five chain pieces with two 250 m guessed lanes and no
defect (`tests/test_microsim/test_microsim_split_audit.py`,
`tests/test_scripts/test_onboard_corridor.py`, `tests/test_api/test_corridors.py`).
The dashboard's Onboard form has no netconvert options in its Advanced
section and is unchanged; the API defaults apply to it.

**`MetricsOut.merge_diagnostics` and the dashboard's diagnostics — 2026-09-24.**
`GET /api/v1/runs/{id}/metrics` gains the additive field `merge_diagnostics`
(`MergeDiagnosticsOut`, `null` by default): the ramp-meter and weaving-section
counters of the run's **first replicate**, read from its `meta.json` the way
`fd_source` is, so a tester sees them without the run directory. `seed` names
the replicate; `ramp_meters[i]` (`RampMeterDiagnosticsOut`) carries `ramp`,
`controller`, `edge`, `interval_s`, `n_released`, `n_passed_unstoppable`
(0 for a meter written before the counter existed) and `n_rate_updates` (the
length of the `rates` log — the log itself and `releases_s` stay in the meta);
`weave_sections[i]` (`WeaveSectionDiagnosticsOut`) carries `ramp`, `exit`,
`length_m` and the `_weave_meta` counters `n_entered`, `n_changed_in`,
`n_changed_out`, `n_exited`, `n_reached_section_exiting` (null for a meta
written before 2026-09-24; the dashboard's "Exited / reached" column then
falls back to `n_departed_exiting`), `n_departed_exiting`, `n_forced`,
`n_forced_deferred` (vehicle-steps), `n_missed`, `n_unfinished`,
`wait_s_mean`, `wait_in_s_mean`, `wait_out_s_mean`, and (2026-09-24, block
3, the follower-cooperation weave step) `n_cooperations` (vehicle-steps),
`mean_follower_decel_ms2` (null when none was commanded) and
`n_changer_eased` (vehicle-steps) — the three are null for a meta written
before the rule existed, and the dashboard shows them as the columns
"Follower cooperations (vehicle-steps)", "Mean follower decel [m/s²]" and
"Changer easings" with a dash for null; likewise `n_vacated` and
`n_vacate_refused` (third weave derivation: through vehicles asked to leave
the weave lane upstream of the section that changed before it / whose request
expired or reached the section unchanged, each once) and `n_pair_releases`
(fifth: stopped changer–follower pairs released, each pair once per release),
null for a meta written before their rule and shown as the columns "Through
vacated", "Vacate refused" and "Pair releases" with a dash for null; the
sweep summary's `diagnostics` block aggregates all six the same way
(`scripts/corridor_sweep.py` `WEAVE_FIELDS`, an older meta contributing
nothing to a counter's interval) and its console line prints them. The field
is `null` when
the run has neither list or both are empty (every ring and plain corridor
run), when the meta cannot be read, and when an entry lacks the counters the
schema requires — absent is honest, a partly filled table is not. They are one
seed's counters, not a replicate aggregate, and describe the merge models'
behaviour, never a corridor result. Dashboard: the Onboard view's corridors
panel renders `CorridorSummaryOut.split_audit` as a "Split audit" table
(verdict badge `ok` green, `wrong_side` / `added_lane_wrong_side` red,
`unknown` grey, the remedy under a defect row) and the run detail shows a
"Merge diagnostics" section only when `merge_diagnostics` has a row;
`frontend/src/api/types.ts` mirrors `CorridorSplitFinding`,
`RampMeterDiagnostics`, `WeaveSectionDiagnostics` and
`RunMetrics.merge_diagnostics`.

## Observed backward wave speed as report context — 2026-09-23

The corridor's *own* stop-and-go wave speed, measured from the detector
archive and printed beside the band the simulated one is scored against
(CLAUDE.md §7.1; `docs/ONBOARDING_MNDOT.md` §4a). It is **context, never a
criterion**: no acceptance row is evaluated from it, and a corridor whose real
waves run outside 14–22 km/h is not a corridor whose model has failed.

**Estimator** (`calibration.waves_observed.detector_wave_speed(station_series,
x_m, *, dt_s, v_thresh_ms=V_JAM_THRESH, max_lag_s=900, min_events=3,
detrend_s=1200, gap_s=0, loo=None) -> ObservedWaveSpeed`). `station_series` is station id → speeds
[m/s] on one regular `dt_s` grid (`None`/NaN = not measured, every series the
same length), `x_m` is station id → corridor position [m] increasing
downstream. For each adjacent pair, with the wave reaching the **downstream**
station (larger x) first: the downstream series' samples below `v_thresh_ms`
mark congested episodes (a run of ≥ `MIN_EVENT_SAMPLES` = 2 bins is one event;
fewer than `min_events` events rejects the pair — `min_events` counts **runs,
not dates**); a run of ≥ `gap_s` samples unmeasured at both stations is a
**barrier** (the separator between two concatenated dates, `gap_s = 0` = one
continuous record) that no correlation pair and no detrending window may
cross, so no lag can align one date against the next whatever `max_lag_s` is;
both series have a centred moving mean of width `detrend_s` removed, which
takes out the slow envelope every station of a corridor shares and leaves the
oscillation, and which is defined only where that window is **two-sided**
(wholly inside the series and barrier-free — a one-sided mean would leave the
local trend in the residual); over the
congested episodes dilated by the maximum lag, the Pearson correlation of
`v_down(t)` against `v_up(t + k)` is computed for every lag
`k ∈ [−max_lag, max_lag]`; the peak lag, refined by a parabola through its two
neighbours (clamped to ±½ bin), divided into the spacing, is the pair's wave
speed. A pair is used only when — **tested in this order** — the peak lag is
strictly positive (a non-positive lag is not a backward wave, and a peak at
`−max_lag` is reported as that rather than as a bound artefact), the peak is
not on the search bound, the peak lag is ≥ `MIN_PEAK_LAG_BINS` (2; below that
the half-bin clamp alone spans a factor of three, and the ±10–15% resolution
is reached at 3 bins), and the peak correlation is ≥ `MIN_PEAK_CORRELATION`
(0.3); every rejection carries its reason. The corridor summary is the median
of the used pairs with their IQR. `ObservedWaveSpeed.to_dict()` is the JSON
form: `median_kmh`, `iqr_kmh`, `n_pairs`, `n_used`, `dt_s`, `v_thresh_ms`,
`max_lag_s`, `min_events`, `detrend_s`, `gap_s`, `min_peak_correlation`,
`min_peak_lag_bins`, `band_kmh`,
`method`, `pairs` (per pair: `upstream`, `downstream`, `dx_m`, `lag_s`,
`lag_bins`, `speed_kmh`, `correlation`, `n_samples`, `n_events`, `used`,
`reason`) and `rejected` (reason → count). NaN is `null` throughout.

**Leave-one-date-out** (`leave_one_date_out(by_date, x_m, *, dt_s, …, gap_s)
-> LeaveOneDateOut`, with `concatenate_dates(by_date, *, gap_bins)` joining
per-date series into the separator-delimited one the estimator takes). A
median over a handful of pairs moves when one morning is dropped, so the whole
estimate — pair rejection included — is re-run once per omitted date; the
result carries the omitted `dates`, the `medians_kmh` and the `n_used` of each
subset, and its `median_min_kmh` / `median_max_kmh` / `pairs_min`. Passed to
`detector_wave_speed(..., loo=...)` it is written into the same JSON block,
flat as `loo_median_min_kmh`, `loo_median_max_kmh` and `loo_pairs_min` and in
full under `leave_one_date_out` (`n_dates`, `by_omitted_date`); the keys are
**absent**, not null, when no sensitivity was computed. It is a sensitivity,
not a confidence interval: the subsets share most of their data.

**Series** (`calibration.loaders.mndot.station_speed_series(config, corridor,
stations, dates, *, t0_s=0, duration_s=None, cache_dir, max_workers=8,
session=None, district, gap_s=SPEED_SERIES_GAP_S)`): the raw 30-second grid,
not the analysis-window grid (a 5-minute window is longer than the lag being
measured). Each bin is the mean over the station's mainline lane detectors of
the samples that reported it, in m/s, with a 0 mph sample dropped as no
measurement; a bin no detector reported is `None`. The requested daily span of
each date is concatenated in the order given, separated by `gap_s` (3600 s) of
`None`, so no correlation pairs samples of two different days and each day's
congestion stays a separate event.

**Artifact** (`flowstate.observations/1`): `Observations` gains an optional
`context: dict`. It is written **only when non-empty**, so an artifact without
one is byte-for-byte what earlier versions wrote, and `from_dict` of an
artifact without one yields `{}` — old artifacts load unchanged.
`scripts/mndot_fetch.py --wave-context` computes the estimate over the same
span and dates as the artifact (one `station_speed_series` call per date, then
`concatenate_dates`) and stores it under `context["detector_wave_speed"]`,
printing the per-pair table, the leave-one-date-out table and the summary
line.

**Report** (`validation.observed`, `validation.report`): `ObservedCorridor`
carries `context` verbatim; `DetectorWaveSpeed.from_context(context)` reads
the `detector_wave_speed` block into `(median_kmh, iqr_kmh, n_pairs, n_used,
rejections)` plus the leave-one-date-out numbers (`loo_n_dates`,
`loo_median_min_kmh`, `loo_median_max_kmh`, `loo_pairs_min`, with `has_loo`
saying whether they are usable) and returns None when the artifact carries
none or carries one this version cannot read (never a report failure). `ObservedProvenance` gains
`wave_speed: DetectorWaveSpeed | None` (`to_dict()` gains
`detector_wave_speed`, null when absent), filled by both `pool_scores` and
`no_comparison_provenance`. The report's **Observed data** block then prints
one computed row, "detector-estimated backward wave speed (context, not a
criterion)" → "median X km/h (IQR a–b) from U of N station pairs;
leave-one-date-out y–z km/h over D dates (fewest P pairs); the model's
band is lo–hi km/h", the band taken from the active `CriteriaProfile` and the
leave-one-date-out clause present only when the artifact carries one. The
acceptance-criteria table is untouched.

## Measurement-window refusal: micro only, and once per sweep — 2026-09-23

`api.main._check_measurement_window` refuses a request whose warm-up leaves
nothing to measure (`sim.duration_s <= sim.warmup_s`, HTTP 422 naming both
numbers) — but only for `tier: micro`. **The macro tier is exempt**:
`api.results.macro_metrics` reports over the whole run and never applies
`sim.warmup_s`, so a macro run with `duration_s <= warmup_s` completes and
yields metrics; refusing it was a false refusal. The macro tier's reporting
window is unchanged by this (it remains the whole run).

`POST /sweeps` now makes the same check **once for the grid**, on the first
cell it builds: no cell patch touches `sim`, so one 422 replaces a fan-out of
cells that each died on the worker. The refusal precedes the sweep row and
every child run, so nothing is queued.

**Network patches (2026-09-24).** `OSMNetwork.patch_files` lists plain-XML
netconvert patches (`*.nod.xml` / `*.edg.xml` / `*.con.xml`) loaded at every
import of the network, before the merge-model patches the runner generates;
paths are relative to the working directory and must lie inside the allowed
data roots (the `osm_file` rule; `microsim.runner._user_patch_files`). An
explicit connection list for an edge replaces every connection netconvert
computed for it. The field is in the config hash whenever set. First use:
`data/osm/mndot_i94_wb_stpaul.splits.con.xml`, the Mounds/Kellogg split.
