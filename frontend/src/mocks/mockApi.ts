/** In-memory mock backend for VITE_MOCK=1 and the API-offline fallback.
 * Serves deterministic, physically plausible demo data so the dashboard is
 * fully demoable standalone. Values follow the honesty rules: seeded runs are
 * labeled, macro runs are tier "screening", n<20 is underpowered. */

import type {
  AggregateStat,
  CreateRunRequest,
  CreateScenarioResponse,
  CreateSweepRequest,
  HeatField,
  Heatmap,
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
}

const scenarios: ScenarioRecord[] = [
  {
    scenario_id: 'scn-ring',
    name: 'ring_sugiyama',
    config_hash: fakeHash(JSON.stringify(ringConfig)),
    config: ringConfig,
    preset: true,
  },
  {
    scenario_id: 'scn-corridor',
    name: 'corridor_10km',
    config_hash: fakeHash(JSON.stringify(corridorConfig)),
    config: corridorConfig,
    preset: true,
  },
];

/* -------------------------------- runs -------------------------------- */

interface RunProfile {
  sigma_v: number;
  throughput_vph: number;
  mean_travel_time_s: number;
  fuel_ml_per_vkm: number;
  wave_count: number;
  wave_speed_kmh: number;
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
  sigma_v: 5.82,
  throughput_vph: 1748,
  mean_travel_time_s: 512.4,
  fuel_ml_per_vkm: 68.3,
  wave_count: 6,
  wave_speed_kmh: -17.6,
};

const DAMPENED: RunProfile = {
  sigma_v: 2.11,
  throughput_vph: 1812,
  mean_travel_time_s: 441.8,
  fuel_ml_per_vkm: 55.1,
  wave_count: 1,
  wave_speed_kmh: -16.2,
};

const RING_PROFILE: RunProfile = {
  sigma_v: 3.4,
  throughput_vph: 1290,
  mean_travel_time_s: 96.5,
  fuel_ml_per_vkm: 84.9,
  wave_count: 1,
  wave_speed_kmh: -4.9,
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
    profile: {
      sigma_v: 4.9,
      throughput_vph: 1725,
      mean_travel_time_s: 498.0,
      fuel_ml_per_vkm: 66.0,
      wave_count: 4,
      wave_speed_kmh: -18.4,
    },
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
    progress: { done, total: r.n },
    config_hash: r.config_hash,
    seeded: r.seeded,
    tier: r.tier,
    created_at: r.created_at,
  };
}

/* ------------------------------ metrics ------------------------------- */

function buildMetrics(r: RunRecord): RunMetrics {
  const profile = r.profile ?? BASELINE;
  const rng = mulberry32(r.seedBase);
  const per: Record<string, number>[] = [];
  const jitter: Record<keyof RunProfile, number> = {
    sigma_v: 0.45,
    throughput_vph: 38,
    mean_travel_time_s: 14,
    fuel_ml_per_vkm: 2.6,
    wave_count: 1.2,
    wave_speed_kmh: 1.4,
  };
  for (let i = 0; i < r.n; i++) {
    const rep: Record<string, number> = { seed: r.seedBase + i };
    (Object.keys(jitter) as (keyof RunProfile)[]).forEach((k) => {
      const noise = (rng() + rng() + rng() - 1.5) * jitter[k]; // ~normal
      let v = profile[k] + noise;
      if (k === 'wave_count') v = Math.max(0, Math.round(v));
      rep[k] = Math.round(v * 1000) / 1000;
    });
    per.push(rep);
  }
  const aggregate: Record<string, AggregateStat> = {};
  (Object.keys(jitter) as (keyof RunProfile)[]).forEach((k) => {
    const vals = per.map((p) => p[k]);
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    const sd = Math.sqrt(
      vals.reduce((a, b) => a + (b - mean) * (b - mean), 0) / Math.max(1, vals.length - 1),
    );
    const half = (1.96 * sd) / Math.sqrt(vals.length);
    aggregate[k] = {
      mean: Math.round(mean * 1000) / 1000,
      lo95: Math.round((mean - half) * 1000) / 1000,
      hi95: Math.round((mean + half) * 1000) / 1000,
      n: r.n,
      underpowered: r.n < 20,
    };
  });
  return {
    replicates: per.map((p) => ({ seed: p.seed, metrics: p })),
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

interface SweepRecord extends SweepDetail {
  createdAt: number;
}

const sweeps = new Map<string, SweepRecord>();

function buildSweep(sweepId: string, req: CreateSweepRequest): SweepRecord {
  const cells: SweepCell[] = [];
  const mkCell = (p: number, c: number): SweepCell => {
    const eff = p * c;
    const runId = `run-sw-${sweepId.slice(-4)}-p${Math.round(p * 100)}-c${Math.round(c * 100)}`;
    const damp = Math.min(0.92, eff * 9);
    const profile: RunProfile = {
      sigma_v: Math.max(1.4, BASELINE.sigma_v * (1 - damp * 0.68)),
      throughput_vph: BASELINE.throughput_vph * (1 + damp * 0.045),
      mean_travel_time_s: BASELINE.mean_travel_time_s * (1 - damp * 0.14),
      fuel_ml_per_vkm: BASELINE.fuel_ml_per_vkm * (1 - damp * 0.2),
      wave_count: Math.max(0, Math.round(BASELINE.wave_count * (1 - damp))),
      wave_speed_kmh: BASELINE.wave_speed_kmh,
    };
    const rec: RunRecord = {
      run_id: runId,
      scenario_id: req.scenario_id,
      scenario_name: `sweep ${req.controller} p=${Math.round(p * 100)}% c=${Math.round(c * 100)}%`,
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
      run_id: runId,
      status: 'done',
      aggregate: buildMetrics(rec).aggregate,
    };
  };
  // baseline cell (p=0) for delta reference
  cells.push(mkCell(0, 1.0));
  for (const p of req.penetrations) {
    for (const c of req.compliances) {
      cells.push(mkCell(p, c));
    }
  }
  return { sweep_id: sweepId, cells, createdAt: Date.now() };
}

/* ------------------------------- reports ------------------------------ */

const reports = new Map<string, string>();

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

/* ----------------------------- mock endpoints ------------------------- */

export async function mockHealth(): Promise<{ status: string }> {
  await sleep(30);
  return { status: 'ok' };
}

export async function mockListScenarios(): Promise<ScenarioSummary[]> {
  await latency();
  return scenarios.map((s) => ({ ...s }));
}

/** Presets have the API's `PresetOut` shape: no `scenario_id` until stored. */
export async function mockListPresets(): Promise<PresetSummary[]> {
  await latency();
  return scenarios
    .filter((s) => s.preset)
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
    scenarios.push({ scenario_id: id, name: cfg.name, config_hash: hash, config: cfg });
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

export async function mockCreateSweep(req: CreateSweepRequest): Promise<{ sweep_id: string }> {
  await latency();
  const id = `swp-${fakeHash(JSON.stringify(req) + Date.now()).slice(0, 6)}`;
  sweeps.set(id, buildSweep(id, req));
  return { sweep_id: id };
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
      controller: 'follower_stopper',
      replicates: 20,
    });
    sweeps.set(sweepId, s);
  }
  // reveal cells progressively so a fresh sweep reads as computing
  const age = (Date.now() - s.createdAt) / 1000;
  const revealed = Math.max(1, Math.floor(age / 0.8) + 1);
  const cells = s.cells.map((c, i) =>
    i < revealed ? c : { ...c, status: 'running' as const, aggregate: undefined },
  );
  return { sweep_id: s.sweep_id, cells };
}

export async function mockCreateReport(runIds: string[]): Promise<{ report_id: string }> {
  await latency();
  const macro = runIds
    .map((id) => runs.find((r) => r.run_id === id))
    .filter((r) => r && r.tier === 'macro');
  if (macro.length > 0) {
    throw new Error('screening-tier (macro) runs cannot be included in a validation report');
  }
  const id = `rpt-${fakeHash(runIds.join(',') + Date.now()).slice(0, 6)}`;
  reports.set(id, reportMarkdown(id, runIds));
  return { report_id: id };
}

export async function mockGetReport(reportId: string): Promise<string> {
  await latency();
  return (
    reports.get(reportId) ??
    `# FlowState Validation Report ${reportId}\n\n(Regenerated demo report — original mock session expired.)`
  );
}
