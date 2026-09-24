/** Typed mirror of the FlowState v2 API contract (base /api/v1) and of the
 * ScenarioConfig schema in packages/flowstate_core/flowstate_core/config.py. */

export type Tier = 'micro' | 'macro';
export type RunStatus = 'queued' | 'running' | 'done' | 'failed';
export type HeatField = 'speed' | 'density';

/* ------------------------- ScenarioConfig ------------------------- */

export interface RingNetwork {
  kind: 'ring';
  circumference_m: number;
  n_vehicles: number;
}

export interface CorridorNetwork {
  kind: 'corridor';
  length_m: number;
  lanes: number;
  /** Piecewise-constant (t_start [s], inflow [veh/s]) steps. */
  inflow: [number, number][];
}

/** One interchange ramp of an OSM corridor (`flowstate_core.config.RampSpec`).
 * Only the fields the dashboard states as read-only facts are modelled. */
export interface OSMRamp {
  kind: 'on' | 'off';
  attach_edge?: string;
}

export interface OSMNetwork {
  kind: 'osm';
  osm_file?: string | null;
  bbox?: [number, number, number, number] | null;
  corridor_edges?: string[];
  inflow?: [number, number][];
  ramps?: OSMRamp[];
  /** Measured downstream boundary condition, when the onboarding derived one
   * (`flowstate_core.config.BoundarySpec`). Its presence is a fact about the
   * corridor; the schedule itself is not something the composer edits. */
  boundary?: { kind?: string } | null;
}

export type Network = RingNetwork | CorridorNetwork | OSMNetwork;

export interface FleetSpec {
  model: 'IDM' | 'EIDM';
  v0?: number;
  T?: number;
  a_max?: number;
  b?: number;
  s0?: number;
  delta?: number;
  heterogeneity_frac?: number;
  idm_calibration?: string | null;
}

export interface AVSpec {
  penetration: number;
  compliance: number;
  controller: string | null;
  controller_params?: Record<string, number>;
  vsl?: string | null;
  vsl_params?: Record<string, number>;
}

export interface SimSpec {
  duration_s: number;
  step_length_s?: number;
  action_step_s?: number;
  warmup_s?: number;
  output_hz?: number;
}

/** Mirrors `flowstate_core.config.MacroOptions` — the macro (CTM screening)
 * tier's solver options. `dx_m` is the target cell length; the realized Δx is
 * `length / max(10, round(length / dx_m))`. `bottleneck_variant` picks the
 * discretization of a controlled vehicle's moving bottleneck: `flux_cap`
 * (`F <- min(F, rho*v*)`, the discrete Delle Monache-Goatin constraint) or
 * `capacity` (`F <- min(F, alpha*q_max(v*))`). The micro tier has neither and
 * ignores the block. */
export interface MacroOptions {
  dx_m?: number;
  bottleneck_variant?: 'flux_cap' | 'capacity';
}

export interface PerturbationSpec {
  t_s: number;
  position_m: number;
  duration_s: number;
  v_drop_ms: number;
}

export interface ScenarioConfig {
  name: string;
  tier: Tier;
  network: Network;
  fleet: FleetSpec;
  av: AVSpec;
  sim: SimSpec;
  /** Path to an `FDCalibration` artifact; when set the macro tier runs on
   * that fitted fundamental diagram instead of the uncalibrated `v1_legacy`
   * preset. */
  fd_calibration?: string | null;
  macro?: MacroOptions | null;
  perturbation?: PerturbationSpec | null;
  seed: number;
  replicates: number;
}

/* --------------------------- API payloads ------------------------- */

export interface ScenarioSummary {
  scenario_id: string;
  name: string;
  config_hash: string;
  /** Full config when the API embeds it (the mock always does). */
  config?: ScenarioConfig;
  /** The API's `ScenarioOut.preset`, always **false**: a stored scenario is a
   * user scenario even when it was created from a preset's config. Optional
   * so a pre-marker API response still type-checks; when the field is absent
   * the client falls back to the endpoint the item came from. */
  preset?: boolean;
}

/** A repo `scenarios/*.yaml` offered by `GET /scenarios/preset` (API `PresetOut`).
 * Presets are not stored scenarios: they have no `scenario_id` until one is
 * created from their config. `preset` is the API's own marker (always true
 * here), so a merged library can tell the two lists apart from the data. */
export interface PresetSummary {
  name: string;
  filename: string;
  config_hash: string;
  config: ScenarioConfig;
  preset: true;
}

export interface CreateScenarioResponse {
  scenario_id: string;
  config_hash: string;
}

/** Mirrors the API's `ProgressOut`. */
export interface RunProgress {
  completed_replicates: number;
  total_replicates: number;
}

export interface RunSummary {
  run_id: string;
  scenario_id: string;
  scenario_name?: string;
  status: RunStatus;
  progress: RunProgress;
  config_hash: string;
  seeded: boolean;
  tier: Tier;
  /** Why the run failed, as the worker reported it (`RunOut.error`) — the
   * text a failed run must show instead of a bare "RUN FAILED". Optional so a
   * service older than the field still type-checks. */
  error?: string | null;
  /** Coarse classification of `error` (`RunOut.error_kind`), when the service
   * sends one. */
  error_kind?: string | null;
  created_at?: string;
}

export interface RunDetail extends RunSummary {
  seeds: number[];
}

export interface CreateRunRequest {
  scenario_id: string;
  overrides?: Record<string, unknown>;
  replicates?: number;
  tier?: Tier;
  /** Macro-tier solver options; merged into the effective config, so they are
   * part of the run's `config_hash`. Unknown keys are refused with 422. */
  macro?: MacroOptions;
}

/** Mirrors the API's `CIOut` — a t-distribution CI over the replicates that
 * produced a value for this metric, in exactly three states (see
 * `api.results.ci_to_json`):
 *
 * - `n === 0` — no replicate produced the metric (no wave detected, no
 *   emission model). `mean`/`lo95`/`hi95` are null, `reason` is
 *   `'no_observations'` and `underpowered` is **false**: there is no estimate
 *   to be underpowered about. Render the metric as absent, never as an
 *   interval and never as a zero.
 * - `0 < n < 20` — an estimate below the headline minimum: `underpowered` is
 *   true (`lo95`/`hi95` null at n === 1, no dispersion from one value).
 * - `n >= 20` — headline-quotable; `underpowered` false, `reason` null. */
export interface AggregateStat {
  /** null when the metric is undefined for every replicate (API `CIOut`). */
  mean: number | null;
  lo95: number | null;
  hi95: number | null;
  n: number;
  underpowered: boolean;
  /** Why there is no estimate, when there is none (`n === 0`); null/absent
   * otherwise. Optional so a pre-`reason` API response still type-checks
   * (`lib/metrics.hasNoObservations` is the predicate to use). */
  reason?: 'no_observations' | null;
}

export interface ReplicateMetrics {
  seed: number;
  /** Metric name → value; null when undefined for this replicate. */
  metrics: Record<string, number | null>;
}

/** Mirrors the API's `MetricsOut` (packages/api/api/schemas.py). */
export interface RunMetrics {
  run_id?: string;
  config_hash?: string;
  tier?: Tier;
  seeded?: boolean;
  n_replicates?: number;
  underpowered?: boolean;
  replicates: ReplicateMetrics[];
  aggregate: Record<string, AggregateStat>;
  /** Macro (screening) runs only: where the fundamental diagram came from —
   * the `FDCalibration` artifact path the config named, or `'v1_legacy
   * preset'` for the documented *uncalibrated* default. Absent/null on micro
   * runs and on a service older than the field, in which case the provenance
   * is unknown and must not be presented as either one. */
  fd_source?: string | null;
  /** Ramp-meter and weaving-section counters of the run's first replicate
   * (`meta.json["ramp_meters"]` / `["weave_sections"]`, 2026-09-24). Absent
   * or null when the run has neither, and on a service older than the
   * field. One seed's counters, not a replicate aggregate. */
  merge_diagnostics?: MergeDiagnostics | null;
}

/** Mirrors the API's `RampMeterDiagnosticsOut`: one ramp meter's counters. */
export interface RampMeterDiagnostics {
  ramp: string;
  controller: string;
  edge: string;
  interval_s: number;
  /** Vehicles the meter held and released. */
  n_released: number;
  /** Vehicles already too close to the stop line to brake when first seen on
   * the ramp, which passed the meter that cycle (docs/LESSONS.md row 31). */
  n_passed_unstoppable: number;
  /** How many times the rate was set (length of the `rates` log). */
  n_rate_updates: number;
}

/** Mirrors the API's `WeaveSectionDiagnosticsOut`: one weaving section's
 * counters (`microsim.runner._weave_meta`). `n_forced_deferred` counts
 * vehicle-steps, not vehicles. */
export interface WeaveSectionDiagnostics {
  ramp: string;
  exit: string;
  length_m?: number | null;
  n_entered: number;
  n_changed_in: number;
  n_changed_out: number;
  n_exited: number;
  /** Exit-bound vehicles that entered the section during the run — the
   * denominator of `n_exited`. Absent (null) in a meta written before the
   * counter existed (2026-09-24); `n_departed_exiting` then stands in. */
  n_reached_section_exiting?: number | null;
  /** Departed vehicles routed through the exit, including those still
   * upstream of the section when the run ended. */
  n_departed_exiting: number;
  n_forced: number;
  n_forced_deferred: number;
  n_missed: number;
  n_unfinished: number;
  /** Vehicle-steps on which a target-lane follower was given a speed target
   * for a changer (2026-09-24, block 3). Absent (null) in a meta written
   * before the follower-cooperation rule existed. */
  n_cooperations?: number | null;
  /** Mean commanded deceleration over those steps [m/s²], positive braking;
   * null without any cooperation, and in an older meta. */
  mean_follower_decel_ms2?: number | null;
  /** Vehicle-steps on which a changer was given a speed target towards its
   * gap's leader; null in an older meta. */
  n_changer_eased?: number | null;
  /** Through vehicles asked to leave the weave lane upstream of the section
   * that changed before reaching it, each once (third weave derivation);
   * null in a meta written before the rule existed. */
  n_vacated?: number | null;
  /** Such requests that expired or reached the section unchanged, each once;
   * null in an older meta. */
  n_vacate_refused?: number | null;
  /** Stopped changer–follower pairs released, each pair once per release
   * (fifth weave derivation); null in an older meta. */
  n_pair_releases?: number | null;
  wait_s_mean?: number | null;
  wait_in_s_mean?: number | null;
  wait_out_s_mean?: number | null;
}

/** Mirrors the API's `MergeDiagnosticsOut`. */
export interface MergeDiagnostics {
  /** The replicate the counters were read from. */
  seed: number;
  ramp_meters: RampMeterDiagnostics[];
  weave_sections: WeaveSectionDiagnostics[];
}

/** Mirrors the API's `HeatmapOut`: bin CENTERS, not edges. */
export interface Heatmap {
  /** Time bin centers [s], length nt. */
  t_bins: number[];
  /** Position bin centers [m], length nx. */
  x_bins: number[];
  /** Row-major [nt][nx]; m/s (speed) or veh/m (density); null = empty bin. */
  values: (number | null)[][];
}

/** The infrastructure axis of a sweep (`flowstate_core.strategies.Strategy`):
 * what the operator deploys, as opposed to what the controlled vehicles do.
 * `vsl` posts gantry speed limits, `alinea` meters every on-ramp. */
export type SweepStrategy = 'none' | 'vsl' | 'alinea' | 'vsl+alinea';

/** Mirrors the API's `SweepCreateRequest` (packages/api/api/schemas.py): the
 * grid is penetrations × compliances × controllers × strategies. The field is
 * the plural `controllers` — a singular `controller` is not part of the
 * contract and would be dropped, leaving every cell without a controller. */
export interface CreateSweepRequest {
  scenario_id: string;
  penetrations: number[];
  compliances: number[];
  /** Controller names; `null` = AVs drive as humans. */
  controllers: (string | null)[];
  /** Infrastructure strategies; omitted = `['none']` (the scenario as
   * calibrated). Each strategy other than `none` also gets one uncontrolled
   * cell of its own, so it can be priced without any controlled vehicle. */
  strategies?: SweepStrategy[];
  /** Metering target of the `alinea` strategies. Omitted, the API reads the
   * critical density from the scenario's `fd_calibration` artifact and
   * answers 422 when there is none — it never invents a target. */
  alinea?: { rho_target_veh_km: number };
  replicates: number;
  /** Ask the API to add a p=0 (no controlled vehicles) baseline cell so the
   * matrix has an uncontrolled reference from the same sweep. */
  include_baseline: boolean;
  overrides?: Record<string, unknown>;
  tier?: Tier;
  /** Macro-tier solver options applied to every cell (see
   * `CreateRunRequest.macro`). */
  macro?: MacroOptions;
}

/** Mirrors the API's `SweepCellOut`. `run_id`/`status` are null until the
 * fan-out job has created the cell's run. */
export interface SweepCell {
  penetration: number;
  compliance: number;
  controller?: string | null;
  /** `SweepCellOut.strategy`: the infrastructure this cell deploys. Absent on
   * a service older than the axis, where every cell ran the scenario as
   * calibrated (`none`). */
  strategy?: SweepStrategy;
  config_hash?: string;
  run_id: string | null;
  status: RunStatus | null;
  progress?: RunProgress | null;
  aggregate?: Record<string, AggregateStat> | null;
}

/** Mirrors the API's `SweepOut`. */
export interface SweepDetail {
  sweep_id: string;
  scenario_id?: string | null;
  status?: RunStatus;
  /** Tier every cell of the grid runs on (`SweepOut.tier`): `macro` means the
   * whole matrix is screening-tier output and cannot support a validation
   * claim. Absent on a service older than the field. */
  tier?: Tier | null;
  error?: string | null;
  created_at?: string;
  runs_total?: number;
  runs_done?: number;
  runs_failed?: number;
  cells: SweepCell[];
}

export type ReportStatus = RunStatus;

/** Mirrors the API's `CriteriaProfileOut` — one row of `GET /criteria`, the
 * selectable acceptance-threshold sets for `POST /reports`.
 *
 * Thresholds only: the *measurements* scored against them are computed from
 * run artifacts and are never accepted from a client, so nothing here is a
 * result — picking a profile chooses which published protocol a report is
 * scored against, not what it measured. */
export interface CriteriaProfile {
  name: string;
  /** Which document, section and table the numbers were transcribed from,
   * what was verified and which rows are FlowState's own conventions. Shown
   * to the user before a profile is quoted in a deliverable, never
   * summarised by the dashboard. */
  source: string;
  /** Per-comparison GEH bound (strict `<`). */
  geh_threshold: number;
  /** Share of link-hour comparisons that must satisfy the bound. */
  geh_pass_fraction: number;
  /** `>=` the share (true) or strictly `>` it (the 2004 table's wording). */
  geh_pass_inclusive: boolean;
  /** Segment-speed RMSPE bound as a fraction; null when the profile's source
   * defines none (the row is then not produced at all). */
  rmspe_max: number | null;
  wave_speed_band_kmh: [number, number];
  min_seeds: number;
  require_ring_emergence: boolean;
  require_ring_dampening: boolean;
  require_sensitivity_grid: boolean;
  /** Name of the wave detector whose recipe the wave-speed row is scored
   * against. */
  wave_detector: string;
  /** True for the profile a report request that names none is scored against. */
  default: boolean;
}

/** Mirrors the API's `ReportOut.observed` — the provenance block of
 * `validation.observed.ObservedProvenance.to_dict()`, computed from the
 * artifact and the scoring, never from the request. Every field is optional
 * here: a service older than a given field omits it, and the dashboard must
 * say "not reported" rather than print a zero the server never sent. */
export interface ObservedProvenanceOut {
  path?: string;
  corridor?: string;
  provider?: string;
  dates?: string;
  url?: string;
  aggregation?: string;
  t0_local?: string;
  window_s?: number;
  /** Mainline stations in the artifact, and windows of its grid. */
  n_stations?: number;
  n_windows?: number;
  /** Windows inside the scored measurement window. */
  n_windows_compared?: number;
  /** Share of the station-window grid carrying an observed flow / speed. */
  flow_fraction?: number;
  speed_fraction?: number;
  /** Comparisons actually formed, pooled over replicates. */
  n_link_hours?: number;
  n_speed_cells?: number;
  n_replicates?: number;
  /** Stations excluded because their cross-section lies outside the
   * simulated position span — no vehicle can cross them, so they are left
   * out of both comparisons rather than scored as a zero simulated flow. */
  n_stations_outside_span?: number;
  stations_outside_span?: string;
  /** Why no comparison was formed, when none was (e.g. an artifact with one
   * positioned mainline station); empty otherwise. */
  note?: string;
}

/** Mirrors the API's `ReportOut` — returned by `POST /reports` (202; still
 * `queued` under the Redis queue, terminal under the inline queue), by
 * `GET /reports/{id}` and, newest first, by `GET /reports`. */
export interface ReportOut {
  report_id: string;
  status: ReportStatus;
  run_ids: string[];
  title: string;
  /** The acceptance-criteria profile the report was scored against (a name
   * from `GET /criteria`). Optional here because a service older than the
   * profile parameter answers without it — an unknown profile, not
   * `fhwa_default`, so the dashboard says "unknown" rather than naming a
   * profile the server never confirmed. */
  profile?: string;
  /** The bundle's markdown file *relative to the server's results root*
   * (`reports/<report_id>/report.md`) — an identifier for the bundle, not a
   * URL and not a path this browser can open. The UI must never render it as
   * a link or a path; the content comes from `/reports/{id}/markdown`,
   * `/pdf` and `/archive`. */
  report_path?: string | null;
  /** The observations artifact the run set was scored against, as the server
   * resolved it; null when the report has no observed side. Like
   * `report_path` it is a server-side path and is never rendered as one. */
  observations_path?: string | null;
  /** What the observed comparison rested on, once the report is done; null
   * while it runs, on failure, and for a report with no observations. */
  observed?: ObservedProvenanceOut | null;
  error: string | null;
  /** `report_refused` when the run set cannot support a validation report
   * (screening-tier only). */
  error_kind: string | null;
  created_at: string;
}

/** A row of the dashboard's report table. `GET /reports` is the source of
 * truth; this shape also carries the browser's own localStorage records,
 * which now only cover reports the server list does not return (requested
 * against another server, or before the list endpoint existed). Local rows
 * refresh their `status`/`error` from `GET /reports/{id}`. */
export interface ReportRecord {
  report_id: string;
  run_ids: string[];
  title?: string;
  /** The criteria profile the report was requested under. Absent on records
   * written before the dashboard sent one, and on server rows from a service
   * that does not report it. */
  profile?: string;
  status: ReportStatus;
  error: string | null;
  error_kind?: string | null;
  created_at: string;
  /** The server's observed-comparison provenance for this report, when the
   * server reported one. `normalizeRecord` does not read it back out of
   * localStorage: it is a server computation, not a browser record, and a
   * stale copy would be a claim about a comparison this browser never saw. */
  observed?: ObservedProvenanceOut | null;
  /** True when this row's status came from the in-browser demo backend (the
   * API was unreachable), so it is not evidence about any server's report.
   * Demo rows are badged DEMO and are never persisted to localStorage. */
  demo?: boolean;
}

/* ------------------------- corridor onboarding ------------------------ */

/** Mirrors the API's `CorridorProgressOut`: named stages, not a time
 * estimate — onboarding downloads a map extract and runs `netconvert`, and a
 * percentage of unknown work is a guess. */
export interface CorridorProgress {
  /** `extract` | `network` | `observations` | `demand` | `install` | `done`. */
  stage: string | null;
  completed_stages: number;
  total_stages: number;
}

/** Mirrors the API's `CorridorStationOut` — where a detector station fell on
 * the corridor. A rejected station carries the nearest chain position and the
 * offset that got it rejected. */
export interface CorridorStation {
  station: string;
  x_m: number;
  offset_m: number;
}

/** Mirrors the API's `CorridorRampOut`. `method` says where the ramp's
 * profile came from — a detector, or the station-to-station balance — which
 * is the provenance a reviewer asks for first. */
export interface CorridorRamp {
  name: string;
  kind: 'on' | 'off';
  x_m: number;
  method: string;
  /** veh/h for an on-ramp, a diverging fraction for an off-ramp. */
  peak: number;
  unit: 'veh/h' | 'frac';
  station?: string | null;
}

/** Mirrors the API's `CorridorLaneMismatchOut`: one mainline station where the
 * compiled map and the detector inventory disagree on the lane count. Reported,
 * never enforced — which of the two is wrong is the operator's call. */
export interface CorridorLaneMismatch {
  station: string;
  /** Position along the corridor [m]. */
  x_m: number;
  /** Lanes the compiled network carries there — what SUMO simulates. */
  compiled_lanes: number;
  /** Lanes the detector inventory reports at that station. */
  inventory_lanes: number;
  /** One line naming the likeliest cause (a triage aid, not a diagnosis). */
  hint: string;
}

export type SplitVerdict = 'ok' | 'wrong_side' | 'added_lane_wrong_side' | 'unknown';

/** Mirrors the API's `CorridorSplitFindingOut`: one exit leaving the corridor,
 * the side OSM draws it on versus the lanes the compiled network feeds it
 * from (`microsim.split_audit`). A `wrong_side` / `added_lane_wrong_side`
 * verdict traps through traffic in a lane that leads only to the exit; the
 * `remedy` names the fix in the engine's terms. Reported, never enforced. */
export interface CorridorSplitFinding {
  from_edge: string;
  exit_edge: string;
  continuing_edge?: string | null;
  /** Chain position of the split [m]. */
  x_m: number;
  osm_way: string;
  osm_lanes?: number | null;
  turn_lanes?: string | null;
  turn_lanes_side: 'left' | 'right' | 'unknown';
  osm_side: 'left' | 'right' | 'unknown';
  /** Signed lateral offsets [m] of the link's first nodes (+ left, − right). */
  osm_offsets_m: number[];
  compiled_lanes: number;
  /** SUMO lane indices (0 = rightmost) that feed the exit. */
  exit_from_lanes: number[];
  compiled_side: 'rightmost' | 'leftmost' | 'middle' | 'all';
  option_lanes: number[];
  added_lane: boolean;
  exit_lanes: number;
  continuing_lanes?: number | null;
  verdict: SplitVerdict;
  remedy: string;
}

/** Mirrors the API's `CorridorSummaryOut`: what the onboarding discovered and
 * derived. None of it is a claim about how the corridor behaves — that is
 * what a report scored against the observations answers. */
export interface CorridorSummary {
  corridor: string;
  chain_length_m: number;
  n_chain_edges: number;
  /** `(x_start_m, x_end_m, lanes)` runs along the chain. */
  lanes_profile: [number, number, number][];
  n_ramps: number;
  stations_placed: CorridorStation[];
  stations_rejected: CorridorStation[];
  stations_without_chain_x: string[];
  inflow_peak_veh_h: number;
  ramps: CorridorRamp[];
  /** Brackets whose flow change no ramp of the needed kind could carry. */
  residuals: Record<string, unknown>[];
  zeroed_ramps: string[];
  unmatched_detectors: string[];
  /** Mainline stations whose lane count could be compared with the map
   * (absent on a corridor onboarded before the check existed). */
  lanes_compared?: number;
  /** Of those, the ones that disagree. */
  lane_mismatches?: CorridorLaneMismatch[];
  /** Every exit leaving the chain, audited for the side it was compiled on
   * (absent on a corridor onboarded before the audit existed, 2026-09-24). */
  split_audit?: CorridorSplitFinding[];
  /** The audit the split fixes were derived from; null (or absent) when no
   * fix was applied, the two audits then being one. Additive, 2026-09-24. */
  split_audit_before_fixes?: CorridorSplitFinding[] | null;
  /** Whether the network was compiled with `--ramps.guess` (the default
   * since 2026-09-24; false for a corridor onboarded before, or opted out). */
  ramp_guessing?: boolean;
  /** Whether the split audit's fixes were asked for (`split_fixes` on the
   * request). */
  split_fixes?: boolean;
  /** Defects the fixes addressed (patch + `--ramps.unset`). */
  split_fixes_applied?: number;
  /** Defects the final audit still carries. */
  split_defects_remaining?: number;
  /** The connection patch written beside the extract, when a `wrong_side`
   * finding needed one. */
  split_patch_file?: string | null;
  /** One line saying what ran: `ramp guessing on; split fixes: 2 applied,
   * 0 remaining`. Empty or absent on a corridor onboarded before the field. */
  applied?: string;
  lines: string[];
}

/** Mirrors the API's `CorridorRowOut` — one onboarding's status row, as
 * `GET /corridors` lists them (newest first). The summary is not listed: it
 * belongs to the corridor being looked at, and a row is here to be picked as
 * the target of a run (`scenario_id`) or a report (`observations_path`), not
 * to restate what the onboarding found. */
export interface CorridorRow {
  corridor_id: string;
  name: string;
  status: RunStatus;
  progress: CorridorProgress;
  /** The stored scenario to launch runs against, once the job is done. */
  scenario_id: string | null;
  /** The `scenarios/<name>.yaml` preset the scenario was installed as. */
  preset_filename: string | null;
  config_hash: string | null;
  /** Server-side path of the observations artifact, to hand back to
   * `POST /reports` as `observations_path`. */
  observations_path: string | null;
  /** The bundle directory, relative to the server's results root — an
   * identifier, not a path this browser can open. */
  corridor_dir: string | null;
  error: string | null;
  error_kind: string | null;
  created_at: string;
}

/** Mirrors the API's `CorridorOut` — returned by `POST /corridors` (202) and
 * by `GET /corridors/{id}`, which the view polls until the status is
 * terminal: the status row plus the summary of what was discovered. */
export interface CorridorOut extends CorridorRow {
  summary: CorridorSummary | null;
}
