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
  preset?: boolean;
}

export interface CreateScenarioResponse {
  scenario_id: string;
  config_hash: string;
}

export interface RunProgress {
  done: number;
  total: number;
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

export interface AggregateStat {
  mean: number;
  lo95: number;
  hi95: number;
  n: number;
  underpowered: boolean;
}

export interface RunMetrics {
  /** One record per replicate; keys are metric names plus `seed`. */
  per_replicate: Record<string, number>[];
  aggregate: Record<string, AggregateStat>;
}

export interface Heatmap {
  /** Time bin edges [s], length nt+1. */
  t_edges: number[];
  /** Position bin edges [m], length nx+1. */
  x_edges: number[];
  /** Row-major [nt][nx]; m/s (speed) or veh/m (density); null = empty bin. */
  values: (number | null)[][];
}

export interface CreateSweepRequest {
  scenario_id: string;
  penetrations: number[];
  compliances: number[];
  controller: string;
  replicates: number;
}

export interface SweepCell {
  penetration: number;
  compliance: number;
  run_id: string;
  status: RunStatus;
  aggregate?: Record<string, AggregateStat>;
}

export interface SweepDetail {
  sweep_id: string;
  cells: SweepCell[];
}

export interface CreateReportResponse {
  report_id: string;
}

/** Client-side record of a generated report (persisted in localStorage —
 * the contract has no report-list endpoint). */
export interface ReportRecord {
  report_id: string;
  run_ids: string[];
  created_at: string;
}
