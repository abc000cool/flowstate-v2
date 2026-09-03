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
`get_segment_controller(name)`. Names: `"follower_stopper"`, `"pi_saturation"`,
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
  `warmup_s = 0.0`, `output_hz = 2.0`.
- `PerturbationSpec | None`: seeded shock (`t_s, position_m, duration_s,
  v_drop_ms`). Non-None ⇒ every output row/report labels `seeded=True`.
- `seed: int`, `replicates: int = 20`, `tier: Literal["micro","macro"]`.

`flowstate_core.config.config_hash(cfg) -> str`: sha256 over canonical JSON
(sorted keys), first 12 hex chars. Recorded in every output artifact.

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
`lane: i32`, `v: f64 [m/s]`, `a: f64 [m/s²]`, `is_av: bool`, `complied: bool`.
Sampled at `sim.output_hz`. On corridors, `x` spans entry buffer + corridor
proper (+ exit buffer when a `BoundarySpec` is configured); micro `meta.json`
then carries a `boundary` object (`kind`, `exit_edge`, `exit_buffer_m`,
`n_steps`, `n_steps_applied`, `v_limit_min_ms`, `v_limit_max_ms`).

`edges.parquet` (both tiers): `t_bin: f64 [s]`, `x_bin: f64 [m]`,
`mean_speed: f64 [m/s]`, `density: f64 [veh/m]`, `flow: f64 [veh/s]`.
Macro-tier rows carry `tier="screening"` in meta.json — the report generator
MUST refuse macro-only validation reports (CLAUDE.md §5.6).

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
labels it as one jam with a pinned front). It is a labeled variant: results
must state the fraction, and the §7.1 wave-speed criterion stays defined on
the absolute threshold.

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
