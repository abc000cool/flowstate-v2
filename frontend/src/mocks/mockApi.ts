/** In-memory mock backend for VITE_MOCK=1 and the API-offline fallback.
 * Serves deterministic, physically plausible demo data so the dashboard is
 * fully demoable standalone. Values follow the honesty rules: seeded runs are
 * labeled, macro runs are tier "screening", n<20 is underpowered.
 *
 * Shapes mirror the real API contract (packages/api/api/schemas.py) so the
 * mock cannot mask a client/contract drift: metric names are the
 * `validation.metrics.Metrics` field names, sweeps take `controllers` (a
 * list) and answer with `SweepOut`, reports are asynchronous
 * (`queued` → `running` → `done`) with the markdown served only once done and
 * a results-root-relative `report_path`, aggregates carry all three
 * `api.results.ci_to_json` states (n=0 + reason, underpowered, quotable), and
 * `preset` is the API's own marker (false on stored scenarios, true on
 * presets). */

import type {
  AggregateStat,
  CreateRunRequest,
  CreateScenarioResponse,
  CreateSweepRequest,
  HeatField,
  Heatmap,
  ReportOut,
  RunDetail,
  RunMetrics,
  RunSummary,
  ScenarioConfig,
  PresetSummary,
  ScenarioSummary,
  SweepCell,
  SweepDetail,
  Tier,
} from '../api/types';
import { corridorSpeedField, mulberry32, ringSpeedField, toDensityField } from './heatmap';

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const latency = () => sleep(90 + Math.random() * 140);

function fakeHash(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  const a = (h >>> 0).toString(16).padStart(8, '0');
  const b = (Math.imul(h, 0x9e3779b1) >>> 0).toString(16).padStart(8, '0');
  return (a + b).slice(0, 12);
}

/* ------------------------------ scenarios ----------------------------- */

const ringConfig: ScenarioConfig = {
  name: 'ring_sugiyama',
  tier: 'micro',
  network: { kind: 'ring', circumference_m: 230, n_vehicles: 22 },
  fleet: { model: 'IDM' },
  av: { penetration: 0, compliance: 1.0, controller: null },
  sim: { duration_s: 300, step_length_s: 0.5 },
  seed: 42,
  replicates: 20,
};

const corridorConfig: ScenarioConfig = {
  name: 'corridor_10km',
  tier: 'micro',
  network: {
    kind: 'corridor',
    length_m: 10000,
    lanes: 1,
    inflow: [[0, 0.55]],
  },
  fleet: { model: 'IDM' },
  av: { penetration: 0, compliance: 1.0, controller: null },
  sim: { duration_s: 1200, step_length_s: 0.5, warmup_s: 120 },
  seed: 42,
  replicates: 20,
};

interface ScenarioRecord extends ScenarioSummary {
  config: ScenarioConfig;
  /** Whether this config is also shipped as a repo preset. It is NOT the
   * `preset` field of the response: `ScenarioOut.preset` is always false —
   * a stored scenario is a user scenario even when its config came from a
   * preset — and `mockListPresets` is what serves the preset side. */
  fromPreset: boolean;
}

const scenarios: ScenarioRecord[] = [
  {
    scenario_id: 'scn-ring',
    name: 'ring_sugiyama',
    config_hash: fakeHash(JSON.stringify(ringConfig)),
    config: ringConfig,
    fromPreset: true,
  },
  {
    scenario_id: 'scn-corridor',
    name: 'corridor_10km',
    config_hash: fakeHash(JSON.stringify(corridorConfig)),
    config: corridorConfig,
    fromPreset: true,
  },
];

/* -------------------------------- runs -------------------------------- */

/** One value per `validation.metrics.Metrics` field — the exact keys the
 * API's `aggregate` dict carries (see `src/lib/metrics.ts`). */
interface RunProfile {
  throughput_veh_h: number;
  mean_tt_s: number;
  p90_tt_s: number;
  sigma_v_spatial_ms: number;
  sigma_v_temporal_ms: number;
  vmt_veh_km: number;
  vht_veh_h: number;
  fuel_ml_per_veh_km: number;
  wave_count: number;
  /** Backward wave-front speed magnitude, reported positive like the API. */
  wave_speed_kmh: number;
  wave_amplitude_ms: number;
  /** Vehicles behind mean_tt_s / p90_tt_s; 0 where no whole journey was
   * recorded (the macro tier records none at all). */
  n_travel_time_veh: number;
}

interface RunRecord {
  run_id: string;
  scenario_id: string;
  scenario_name: string;
  tier: Tier;
  seeded: boolean;
  n: number;
  seedBase: number;
  config_hash: string;
  created_at: string;
  kind: 'ring' | 'corridor';
  /** null profile => metrics unavailable (queued/running/failed). */
  profile: RunProfile | null;
  /** 0..1, thins out the wave bands in the heatmap. */
  damping: number;
  fixedStatus?: 'done' | 'failed';
  /** for launched runs: wall-clock schedule */
  launchedAt?: number;
}

const BASELINE: RunProfile = {
  throughput_veh_h: 1748,
  mean_tt_s: 512.4,
  p90_tt_s: 641.0,
  sigma_v_spatial_ms: 5.82,
  sigma_v_temporal_ms: 4.61,
  vmt_veh_km: 5830,
  vht_veh_h: 82.9,
  fuel_ml_per_veh_km: 68.3,
  wave_count: 6,
  wave_speed_kmh: 17.6,
  wave_amplitude_ms: 14.2,
  n_travel_time_veh: 583,
};

const DAMPENED: RunProfile = {
  throughput_veh_h: 1812,
  mean_tt_s: 441.8,
  p90_tt_s: 512.6,
  sigma_v_spatial_ms: 2.11,
  sigma_v_temporal_ms: 1.74,
  vmt_veh_km: 6040,
  vht_veh_h: 74.1,
  fuel_ml_per_veh_km: 55.1,
  wave_count: 1,
  wave_speed_kmh: 16.2,
  wave_amplitude_ms: 6.8,
  n_travel_time_veh: 604,
};

const RING_PROFILE: RunProfile = {
  throughput_veh_h: 1290,
  mean_tt_s: 96.5,
  p90_tt_s: 118.0,
  sigma_v_spatial_ms: 3.4,
  sigma_v_temporal_ms: 2.9,
  vmt_veh_km: 52.8,
  vht_veh_h: 1.83,
  fuel_ml_per_veh_km: 84.9,
  wave_count: 1,
  wave_speed_kmh: 4.9,
  wave_amplitude_ms: 7.1,
  n_travel_time_veh: 22,
};

const MACRO_PROFILE: RunProfile = {
  throughput_veh_h: 1725,
  mean_tt_s: 498.0,
  p90_tt_s: 602.0,
  sigma_v_spatial_ms: 4.9,
  sigma_v_temporal_ms: 3.9,
  vmt_veh_km: 5750,
  vht_veh_h: 79.8,
  fuel_ml_per_veh_km: 66.0,
  wave_count: 4,
  wave_speed_kmh: 18.4,
  wave_amplitude_ms: 12.0,
  // the macro tier has no per-vehicle trajectories, so it reports none
  n_travel_time_veh: 0,
};

const t0 = Date.parse('2026-08-29T14:02:00Z');
const iso = (offsetMin: number) => new Date(t0 + offsetMin * 60000).toISOString();

const runs: RunRecord[] = [
  {
    run_id: 'run-8f2c11',
    scenario_id: 'scn-corridor',
    scenario_name: 'corridor_10km',
    tier: 'micro',
    seeded: false,
    n: 20,
    seedBase: 1000,
    config_hash: fakeHash('corridor-baseline'),
    created_at: iso(0),
    kind: 'corridor',
    profile: BASELINE,
    damping: 0,
    fixedStatus: 'done',
  },
  {
    run_id: 'run-a41d09',
    scenario_id: 'scn-corridor',
    scenario_name: 'corridor_10km · follower_stopper 5%/80%',
    tier: 'micro',
    seeded: true,
    n: 20,
    seedBase: 2000,
    config_hash: fakeHash('corridor-fs-5-80'),
    created_at: iso(9),
    kind: 'corridor',
    profile: DAMPENED,
    damping: 0.8,
    fixedStatus: 'done',
  },
  {
    run_id: 'run-3b9e77',
    scenario_id: 'scn-ring',
    scenario_name: 'ring_sugiyama',
    tier: 'micro',
    seeded: false,
    n: 20,
    seedBase: 3000,
    config_hash: fakeHash('ring-base'),
    created_at: iso(15),
    kind: 'ring',
    profile: RING_PROFILE,
    damping: 0,
    fixedStatus: 'done',
  },
  {
    run_id: 'run-d0417a',
    scenario_id: 'scn-corridor',
    scenario_name: 'corridor_10km · macro screen',
    tier: 'macro',
    seeded: true,
    n: 8,
    seedBase: 4000,
    config_hash: fakeHash('corridor-macro'),
    created_at: iso(21),
    kind: 'corridor',
    profile: MACRO_PROFILE,
    damping: 0.35,
    fixedStatus: 'done',
  },
  {
    run_id: 'run-e2190c',
    scenario_id: 'scn-corridor',
    scenario_name: 'corridor_10km · jad 10%/50%',
    tier: 'micro',
    seeded: false,
    n: 20,
    seedBase: 5000,
    config_hash: fakeHash('corridor-jad'),
    created_at: iso(26),
    kind: 'corridor',
    profile: null,
    damping: 0.5,
    fixedStatus: 'failed',
  },
  {
    run_id: 'run-77aa30',
    scenario_id: 'scn-corridor',
    scenario_name: 'corridor_10km · pi_saturation 10%/100%',
    tier: 'micro',
    seeded: false,
    n: 20,
    seedBase: 6000,
    config_hash: fakeHash('corridor-pi'),
    created_at: iso(31),
    kind: 'corridor',
    profile: DAMPENED,
    damping: 0.6,
    launchedAt: Date.now() - 4000, // appears live on first view
  },
];

const moduleStart = Date.now();

function runStatus(r: RunRecord): { status: RunSummary['status']; done: number } {
  if (r.fixedStatus === 'done') return { status: 'done', done: r.n };
  if (r.fixedStatus === 'failed') return { status: 'failed', done: Math.floor(r.n / 3) };
  const started = r.launchedAt ?? moduleStart;
  const elapsed = (Date.now() - started) / 1000;
  if (elapsed < 3) return { status: 'queued', done: 0 };
  const done = Math.floor((elapsed - 3) / 2.2);
  if (done >= r.n) return { status: 'done', done: r.n };
  return { status: 'running', done };
}

function toSummary(r: RunRecord): RunSummary {
  const { status, done } = runStatus(r);
  return {
    run_id: r.run_id,
    scenario_id: r.scenario_id,
    scenario_name: r.scenario_name,
    status,
    progress: { completed_replicates: done, total_replicates: r.n },
    config_hash: r.config_hash,
    seeded: r.seeded,
    tier: r.tier,
    created_at: r.created_at,
  };
}

/* ------------------------------ metrics ------------------------------- */

/** Per-metric replicate noise scale (≈ one standard deviation). */
const JITTER: Record<keyof RunProfile, number> = {
  throughput_veh_h: 38,
  mean_tt_s: 14,
  p90_tt_s: 20,
  sigma_v_spatial_ms: 0.45,
  sigma_v_temporal_ms: 0.38,
  vmt_veh_km: 120,
  vht_veh_h: 2.4,
  fuel_ml_per_veh_km: 2.6,
  wave_count: 1.2,
  wave_speed_kmh: 1.4,
  wave_amplitude_ms: 1.1,
  n_travel_time_veh: 9,
};

const METRIC_KEYS = Object.keys(JITTER) as (keyof RunProfile)[];

/** Metrics that are counts of things: integer-valued and never negative. */
const COUNT_KEYS: (keyof RunProfile)[] = ['wave_count', 'n_travel_time_veh'];

/** Aggregate one metric's replicate values exactly as `api.results.ci_to_json`
 * does — the three states a client must render differently:
 *
 * - no finite value at all → null mean/bounds, `n: 0`, `underpowered: false`
 *   and `reason: 'no_observations'` (there is no estimate, not a weak one);
 * - a single value → no dispersion, so the bounds stay null;
 * - otherwise the t-ish CI, `underpowered` below the 20-replicate standard. */
function aggregateMetric(values: (number | null)[]): AggregateStat {
  const vals = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  if (vals.length === 0) {
    return { mean: null, lo95: null, hi95: null, n: 0, underpowered: false, reason: 'no_observations' };
  }
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
  const underpowered = vals.length < 20;
  if (vals.length === 1) {
    return { mean: Math.round(mean * 1000) / 1000, lo95: null, hi95: null, n: 1, underpowered, reason: null };
  }
  const sd = Math.sqrt(vals.reduce((a, b) => a + (b - mean) * (b - mean), 0) / (vals.length - 1));
  const half = (1.96 * sd) / Math.sqrt(vals.length);
  return {
    mean: Math.round(mean * 1000) / 1000,
    lo95: Math.round((mean - half) * 1000) / 1000,
    hi95: Math.round((mean + half) * 1000) / 1000,
    n: vals.length,
    underpowered,
    reason: null,
  };
}

function buildMetrics(r: RunRecord): RunMetrics {
  const profile = r.profile ?? BASELINE;
  const rng = mulberry32(r.seedBase);
  const per: { seed: number; metrics: Record<string, number | null> }[] = [];
  for (let i = 0; i < r.n; i++) {
    const rep: Record<string, number | null> = {};
    for (const k of METRIC_KEYS) {
      const noise = (rng() + rng() + rng() - 1.5) * JITTER[k]; // ~normal
      let v = profile[k] + noise;
      // integer counts stay integers, and a profile count of zero stays zero
      // in every replicate — that is what makes the metrics derived from it
      // (wave speed, wave amplitude) genuinely unobserved rather than sparse
      if (COUNT_KEYS.includes(k)) v = profile[k] === 0 ? 0 : Math.max(0, Math.round(v));
      rep[k] = Math.round(v * 1000) / 1000;
    }
    // a replicate where no wave was detected has no wave-front observation:
    // wave speed and amplitude are undefined there, exactly like the API's
    // per-replicate nulls (a fully dampened cell then aggregates to n=0)
    if (rep.wave_count === 0) {
      rep.wave_speed_kmh = null;
      rep.wave_amplitude_ms = null;
    }
    per.push({ seed: r.seedBase + i, metrics: rep });
  }
  const aggregate: Record<string, AggregateStat> = {};
  for (const k of METRIC_KEYS) {
    aggregate[k] = aggregateMetric(per.map((p) => p.metrics[k]));
  }
  return {
    run_id: r.run_id,
    config_hash: r.config_hash,
    tier: r.tier,
    seeded: r.seeded,
    n_replicates: r.n,
    underpowered: r.n < 20,
    replicates: per,
    aggregate,
  };
}

function buildHeatmap(r: RunRecord, field: HeatField): Heatmap {
  let speed: Heatmap;
  if (r.kind === 'ring') {
    speed = ringSpeedField(r.seedBase);
  } else {
    const allBands = [
      { t0: -420, x0: 9200, depth: 1.0 },
      { t0: 90, x0: 8800, depth: 0.95 },
      { t0: 540, x0: 9300, depth: 0.9 },
    ];
    const keep = Math.max(0, Math.round(allBands.length * (1 - r.damping)));
    const bands = allBands
      .slice(0, Math.max(keep, r.damping >= 1 ? 0 : 1))
      .map((b) => ({ ...b, depth: b.depth * (1 - 0.55 * r.damping) }));
    speed = corridorSpeedField({ bands, seed: r.seedBase });
  }
  if (field === 'density') return toDensityField(speed, r.kind === 'ring' ? 9 : 30);
  return speed;
}

/* ------------------------------- sweeps ------------------------------- */

interface SweepRecord {
  sweep_id: string;
  scenario_id: string;
  created_at: string;
  createdAt: number;
  cells: SweepCell[];
}

const sweeps = new Map<string, SweepRecord>();

function buildSweep(sweepId: string, req: CreateSweepRequest): SweepRecord {
  const cells: SweepCell[] = [];
  const mkCell = (p: number, c: number, controller: string | null): SweepCell => {
    // no controller (or no AVs) => nothing to dampen with: baseline physics
    const eff = controller ? p * c : 0;
    const tag = controller ?? 'none';
    const runId = `run-sw-${sweepId.slice(-4)}-${tag.slice(0, 3)}-p${Math.round(p * 100)}-c${Math.round(c * 100)}`;
    const damp = Math.min(0.92, eff * 9);
    const profile: RunProfile = {
      throughput_veh_h: BASELINE.throughput_veh_h * (1 + damp * 0.045),
      mean_tt_s: BASELINE.mean_tt_s * (1 - damp * 0.14),
      p90_tt_s: BASELINE.p90_tt_s * (1 - damp * 0.2),
      sigma_v_spatial_ms: Math.max(1.4, BASELINE.sigma_v_spatial_ms * (1 - damp * 0.68)),
      sigma_v_temporal_ms: Math.max(1.1, BASELINE.sigma_v_temporal_ms * (1 - damp * 0.65)),
      vmt_veh_km: BASELINE.vmt_veh_km * (1 + damp * 0.04),
      vht_veh_h: BASELINE.vht_veh_h * (1 - damp * 0.1),
      fuel_ml_per_veh_km: BASELINE.fuel_ml_per_veh_km * (1 - damp * 0.2),
      wave_count: Math.max(0, Math.round(BASELINE.wave_count * (1 - damp))),
      wave_speed_kmh: BASELINE.wave_speed_kmh,
      wave_amplitude_ms: BASELINE.wave_amplitude_ms * (1 - damp * 0.55),
      n_travel_time_veh: Math.round(BASELINE.n_travel_time_veh * (1 + damp * 0.04)),
    };
    const rec: RunRecord = {
      run_id: runId,
      scenario_id: req.scenario_id,
      scenario_name: `sweep ${tag} p=${Math.round(p * 100)}% c=${Math.round(c * 100)}%`,
      tier: 'micro',
      seeded: true,
      n: req.replicates,
      seedBase: 9000 + Math.round(p * 1000) * 7 + Math.round(c * 100),
      config_hash: fakeHash(runId),
      created_at: new Date().toISOString(),
      kind: 'corridor',
      profile,
      damping: damp,
      fixedStatus: 'done',
    };
    if (!runs.some((r) => r.run_id === runId)) runs.push(rec);
    return {
      penetration: p,
      compliance: c,
      controller,
      config_hash: rec.config_hash,
      run_id: runId,
      status: 'done',
      progress: { completed_replicates: req.replicates, total_replicates: req.replicates },
      aggregate: buildMetrics(rec).aggregate,
    };
  };
  const controllers = req.controllers.length > 0 ? req.controllers : [null];
  // baseline cells as the delta reference, shaped like the API's
  // SweepCreateRequest.baseline_cells(): one (p=0, compliance 1) cell per
  // distinct controller, skipped when the grid already holds p=0. The API
  // appends them after the product; they lead here only so the progressive
  // reveal shows the reference first (the matrix does not depend on order).
  if (req.include_baseline && !req.penetrations.includes(0)) {
    for (const ctrl of new Set(controllers)) cells.push(mkCell(0, 1.0, ctrl));
  }
  for (const p of req.penetrations) {
    for (const c of req.compliances) {
      for (const ctrl of controllers) {
        cells.push(mkCell(p, c, ctrl));
      }
    }
  }
  return {
    sweep_id: sweepId,
    scenario_id: req.scenario_id,
    created_at: new Date().toISOString(),
    createdAt: Date.now(),
    cells,
  };
}

/** `SweepOut` view of a record: cells are revealed progressively so a fresh
 * sweep reads as computing — not-yet-started cells carry null run/status
 * exactly like the API before the fan-out job reaches them. */
function sweepView(s: SweepRecord): SweepDetail {
  const age = (Date.now() - s.createdAt) / 1000;
  const revealed = Math.max(1, Math.floor(age / 0.8) + 1);
  const cells = s.cells.map((c, i) => {
    if (i < revealed) return c;
    if (i === revealed) {
      const total = c.progress?.total_replicates ?? 0;
      return {
        ...c,
        status: 'running' as const,
        progress: { completed_replicates: Math.floor(total / 2), total_replicates: total },
        aggregate: null,
      };
    }
    return { ...c, run_id: null, status: null, progress: null, aggregate: null };
  });
  const done = Math.min(revealed, s.cells.length);
  return {
    sweep_id: s.sweep_id,
    scenario_id: s.scenario_id,
    status: done >= s.cells.length ? 'done' : 'running',
    error: null,
    created_at: s.created_at,
    runs_total: s.cells.length,
    runs_done: done,
    runs_failed: 0,
    cells,
  };
}

/* ------------------------------- reports ------------------------------ */

const REPORT_TITLE = 'FlowState calibration & validation report';

interface ReportRow {
  out: ReportOut;
  markdown: string;
  createdAt: number;
}

const reports = new Map<string, ReportRow>();

function reportMarkdown(reportId: string, runIds: string[]): string {
  const lines: string[] = [];
  lines.push(`# FlowState Validation Report ${reportId}`);
  lines.push('');
  lines.push(`Generated: ${new Date().toISOString()}  `);
  lines.push('Tier: microscopic (SUMO/IDM) — screening-tier runs excluded by policy.');
  lines.push('');
  for (const id of runIds) {
    const r = runs.find((x) => x.run_id === id);
    if (!r) continue;
    lines.push(`## Run \`${id}\``);
    lines.push('');
    lines.push(`- scenario: ${r.scenario_name}`);
    lines.push(`- config_hash: \`${r.config_hash}\``);
    lines.push(`- replicates: ${r.n} · seeds ${r.seedBase}–${r.seedBase + r.n - 1}`);
    lines.push(`- seeded perturbation: ${r.seeded ? '**yes (labeled)**' : 'no (emergent)'}`);
    lines.push('');
    lines.push('| metric | mean | 95% CI | n |');
    lines.push('|---|---:|---:|---:|');
    const m = buildMetrics(r);
    for (const [k, a] of Object.entries(m.aggregate)) {
      lines.push(`| ${k} | ${a.mean} | [${a.lo95}, ${a.hi95}] | ${a.n} |`);
    }
    lines.push('');
  }
  lines.push('## Limitations');
  lines.push('');
  lines.push(
    '- Single corridor; model-form uncertainty not quantified; compliance is a swept assumption, not an observation.',
  );
  lines.push('- Every value above traces to a computed artifact of a seeded run.');
  return lines.join('\n');
}

/** `ReportOut` view of a row: like the Redis-queued API, a report is
 * `queued`, then `running`, then `done` a few seconds after creation.
 *
 * `report_path` is results-root-relative, exactly as the API serves it
 * (`api.main._relative_report_path`) — an identifier for the bundle, never a
 * URL and never the server's directory layout. */
function reportView(row: ReportRow): ReportOut {
  const age = (Date.now() - row.createdAt) / 1000;
  const status: ReportOut['status'] = age < 1.2 ? 'queued' : age < 3 ? 'running' : 'done';
  return {
    ...row.out,
    status,
    report_path: status === 'done' ? `reports/${row.out.report_id}/report.md` : null,
  };
}

function ensureReport(reportId: string): ReportRow {
  let row = reports.get(reportId);
  if (!row) {
    // survive reloads in demo mode: a finished, regenerated demo report
    row = {
      out: {
        report_id: reportId,
        status: 'done',
        run_ids: [],
        title: REPORT_TITLE,
        report_path: null,
        error: null,
        error_kind: null,
        created_at: new Date(0).toISOString(),
      },
      markdown: `# FlowState Validation Report ${reportId}\n\n(Regenerated demo report — original mock session expired.)`,
      createdAt: 0,
    };
    reports.set(reportId, row);
  }
  return row;
}

/* ----------------------------- mock endpoints ------------------------- */

export async function mockHealth(): Promise<{ status: string }> {
  await sleep(30);
  return { status: 'ok' };
}

/** Stored scenarios, with the API's marker: `ScenarioOut.preset` is always
 * false, whatever the config was created from. */
export async function mockListScenarios(): Promise<ScenarioSummary[]> {
  await latency();
  return scenarios.map(({ fromPreset: _fromPreset, ...s }) => ({ ...s, preset: false }));
}

/** Presets have the API's `PresetOut` shape: no `scenario_id` until stored. */
export async function mockListPresets(): Promise<PresetSummary[]> {
  await latency();
  return scenarios
    .filter((s) => s.fromPreset)
    .map((s) => ({
      name: s.name,
      filename: `${s.name}.yaml`,
      config_hash: s.config_hash,
      config: s.config,
      preset: true as const,
    }));
}

export async function mockCreateScenario(cfg: ScenarioConfig): Promise<CreateScenarioResponse> {
  await latency();
  const id = `scn-${fakeHash(JSON.stringify(cfg)).slice(0, 6)}`;
  const hash = fakeHash(JSON.stringify(cfg));
  if (!scenarios.some((s) => s.scenario_id === id)) {
    scenarios.push({
      scenario_id: id,
      name: cfg.name,
      config_hash: hash,
      config: cfg,
      fromPreset: false,
    });
  }
  return { scenario_id: id, config_hash: hash };
}

export async function mockListRuns(): Promise<RunSummary[]> {
  await latency();
  return [...runs]
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
    .map(toSummary);
}

export async function mockGetRun(runId: string): Promise<RunDetail> {
  await latency();
  const r = runs.find((x) => x.run_id === runId);
  if (!r) throw new Error(`run ${runId} not found`);
  return { ...toSummary(r), seeds: Array.from({ length: r.n }, (_, i) => r.seedBase + i) };
}

export async function mockCreateRun(req: CreateRunRequest): Promise<{ run_id: string }> {
  await latency();
  const scn = scenarios.find((s) => s.scenario_id === req.scenario_id);
  const n = req.replicates ?? scn?.config.replicates ?? 20;
  const runId = `run-${fakeHash(req.scenario_id + Date.now()).slice(0, 6)}`;
  const kind = scn?.config.network.kind === 'ring' ? 'ring' : 'corridor';
  const controller = scn?.config.av.controller ?? null;
  const damp = controller ? Math.min(0.9, scn!.config.av.penetration * scn!.config.av.compliance * 9) : 0;
  runs.push({
    run_id: runId,
    scenario_id: req.scenario_id,
    scenario_name: scn?.name ?? req.scenario_id,
    tier: req.tier ?? scn?.config.tier ?? 'micro',
    seeded: scn?.config.perturbation != null,
    n,
    seedBase: 10000 + runs.length * 100,
    config_hash: scn?.config_hash ?? fakeHash(runId),
    created_at: new Date().toISOString(),
    kind,
    profile: damp > 0.3 ? DAMPENED : kind === 'ring' ? RING_PROFILE : BASELINE,
    damping: damp,
    launchedAt: Date.now(),
  });
  return { run_id: runId };
}

export async function mockGetRunMetrics(runId: string): Promise<RunMetrics> {
  await latency();
  const r = runs.find((x) => x.run_id === runId);
  if (!r) throw new Error(`run ${runId} not found`);
  if (runStatus(r).status !== 'done') throw new Error(`run ${runId} has no metrics yet`);
  return buildMetrics(r);
}

export async function mockGetRunHeatmap(runId: string, field: HeatField): Promise<Heatmap> {
  await sleep(160);
  const r = runs.find((x) => x.run_id === runId);
  if (!r) throw new Error(`run ${runId} not found`);
  return buildHeatmap(r, field);
}

export async function mockCreateSweep(req: CreateSweepRequest): Promise<SweepDetail> {
  await latency();
  const id = `swp-${fakeHash(JSON.stringify(req) + Date.now()).slice(0, 6)}`;
  const rec = buildSweep(id, req);
  sweeps.set(id, rec);
  return sweepView(rec);
}

export async function mockGetSweep(sweepId: string): Promise<SweepDetail> {
  await latency();
  let s = sweeps.get(sweepId);
  if (!s) {
    // survive reloads in demo mode: synthesize a default sweep
    s = buildSweep(sweepId, {
      scenario_id: 'scn-corridor',
      penetrations: [0.01, 0.02, 0.05, 0.1, 0.15, 0.2],
      compliances: [0.25, 0.5, 0.8, 1.0],
      controllers: ['follower_stopper'],
      replicates: 20,
      include_baseline: true,
    });
    sweeps.set(sweepId, s);
  }
  return sweepView(s);
}

/** Like the API's inline queue, a macro-only run set is refused up front. */
export async function mockCreateReport(runIds: string[], title = REPORT_TITLE): Promise<ReportOut> {
  await latency();
  const macro = runIds
    .map((id) => runs.find((r) => r.run_id === id))
    .filter((r) => r && r.tier === 'macro');
  if (macro.length > 0) {
    throw new Error('screening-tier (macro) runs cannot be included in a validation report');
  }
  const id = `rpt-${fakeHash(runIds.join(',') + Date.now()).slice(0, 6)}`;
  const row: ReportRow = {
    out: {
      report_id: id,
      status: 'queued',
      run_ids: runIds,
      title,
      report_path: null,
      error: null,
      error_kind: null,
      created_at: new Date().toISOString(),
    },
    markdown: reportMarkdown(id, runIds),
    createdAt: Date.now(),
  };
  reports.set(id, row);
  return reportView(row);
}

/** `GET /reports` — every report this session holds, newest first. */
export async function mockListReports(limit = 200): Promise<ReportOut[]> {
  await latency();
  return [...reports.values()]
    .sort((a, b) => b.createdAt - a.createdAt)
    .slice(0, Math.max(1, limit))
    .map(reportView);
}

export async function mockGetReport(reportId: string): Promise<ReportOut> {
  await latency();
  return reportView(ensureReport(reportId));
}

/** The markdown is served only once the report is done (API: 409 before). */
export async function mockGetReportMarkdown(reportId: string): Promise<string> {
  await latency();
  const row = ensureReport(reportId);
  const view = reportView(row);
  if (view.status !== 'done') throw new Error(`report ${reportId} is ${view.status}, not done`);
  return row.markdown;
}

/** Demo mode has no figure files and no PDF renderer: the archive and PDF
 * downloads say so instead of handing the browser a zip that is not one. */
export async function mockGetReportArchive(reportId: string): Promise<Blob> {
  await latency();
  const view = reportView(ensureReport(reportId));
  if (view.status !== 'done') throw new Error(`report ${reportId} is ${view.status}, not done`);
  throw new Error('demo data has no report archive — connect the API to download the .zip bundle');
}

export async function mockGetReportPdf(reportId: string): Promise<Blob> {
  await latency();
  const view = reportView(ensureReport(reportId));
  if (view.status !== 'done') throw new Error(`report ${reportId} is ${view.status}, not done`);
  throw new Error('demo data has no PDF rendering — connect the API to download the PDF');
}
