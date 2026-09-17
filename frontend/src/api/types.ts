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

export interface OSMNetwork {
  kind: 'osm';
  osm_file?: string | null;
  bbox?: [number, number, number, number] | null;
  corridor_edges?: string[];
  inflow?: [number, number][];
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

/** Mirrors the API's `SweepCreateRequest` (packages/api/api/schemas.py): the
 * grid is penetrations × compliances × controllers. The field is the plural
 * `controllers` — a singular `controller` is not part of the contract and
 * would be dropped, leaving every cell without a controller. */
export interface CreateSweepRequest {
  scenario_id: string;
  penetrations: number[];
  compliances: number[];
  /** Controller names; `null` = AVs drive as humans. */
  controllers: (string | null)[];
  replicates: number;
  /** Ask the API to add a p=0 (no controlled vehicles) baseline cell so the
   * matrix has an uncontrolled reference from the same sweep. */
  include_baseline: boolean;
  overrides?: Record<string, unknown>;
  tier?: Tier;
}

/** Mirrors the API's `SweepCellOut`. `run_id`/`status` are null until the
 * fan-out job has created the cell's run. */
export interface SweepCell {
  penetration: number;
  compliance: number;
  controller?: string | null;
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
  /** True when this row's status came from the in-browser demo backend (the
   * API was unreachable), so it is not evidence about any server's report.
   * Demo rows are badged DEMO and are never persisted to localStorage. */
  demo?: boolean;
}
