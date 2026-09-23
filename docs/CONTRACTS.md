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
  attach_edge, edges, n_planned, n_departed, n_planned_exiting`). Ramp demand
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
  dead-end at the attach edge's end node and exactly one lane to drop into
  the next edge (checked at run time). `meta.json` lists `merge_models` and
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
counted. `pool_scores(observed, scores, path=...)` pools GEH across
replicates, means the RMSPE, means the simulated matrices and builds the
`ObservedProvenance` the report prints. `metrics.link_hour_geh` gained
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
(both nulled by a re-claim).

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
`--keep-trajectories`.

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
were launched from; 413 above `FLOWSTATE_MAX_UPLOAD_MB`.

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
