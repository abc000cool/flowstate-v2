/** Data access for the embed: the JSON index, binary runs, the observed field. */

export interface Last300 {
  sigma_v_ms: number;
  mean_v_ms: number;
  stopped_fraction: number;
}

export interface RunRecord {
  id: string;
  n_vehicles: number;
  n_av: number;
  activation_s: number;
  seed: number;
  controller: string | null;
  config_hash: string;
  file: string;
  t0_s: number;
  dt_s: number;
  n_samples: number;
  av_index: number[];
  sigma_v_per_minute_ms: number[];
  mean_v_per_minute_ms: number[];
  min_v_per_minute_ms: number[];
  last300: Last300;
}

export interface ScenarioInfo {
  name: string;
  yaml: string;
  circumference_m: number;
  vehicle_length_m: number;
  duration_s: number;
  output_hz: number;
  idm: Record<string, number>;
  controller: string;
  seeded_perturbation: boolean;
}

export interface IndexFile {
  schema: string;
  generated_by: string;
  engine: { sumo: string | null; model: string; step_s: number };
  scenario: ScenarioInfo;
  grid: { n_vehicles: number[]; n_av: number[]; activation_s: number[]; seeds: number[] };
  runs: RunRecord[];
  observed: { file: string; n_t: number; n_x: number };
}

export interface ObservedField {
  source: string;
  data_hash: string;
  t_origin_unix: number;
  dt_s: number;
  dx_m: number;
  t_edges_s: number[];
  x_edges_m: number[];
  mean_speed_kmh: (number | null)[][];
  coverage_note: string;
}

export interface RunData {
  rec: RunRecord;
  nVeh: number;
  nSamples: number;
  /** Wrapped ring position [m], row-major [sample][vehicle]. */
  x: Float32Array;
  /** Speed [m/s], same layout. */
  v: Float32Array;
  /** 95th-percentile speed of the run, the colour scale's top. */
  vRef: number;
}

export interface Selection {
  n_vehicles: number;
  n_av: number;
  activation_s: number;
  seed: number;
}

export const X_SCALE = 0.1; // uint16 decimetres → m
export const V_SCALE = 0.01; // uint16 cm/s → m/s

/** Decode a run blob (see scripts/website_sim_pack.py for the layout). */
export function decodeRun(
  buf: ArrayBuffer,
  nSamples: number,
  nVeh: number,
): { x: Float32Array; v: Float32Array } {
  const n = nSamples * nVeh;
  if (buf.byteLength !== 4 * n) {
    throw new Error(`run blob is ${buf.byteLength} bytes, expected ${4 * n}`);
  }
  const dv = new DataView(buf);
  const x = new Float32Array(n);
  const v = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    x[i] = dv.getUint16(2 * i, true) * X_SCALE;
    v[i] = dv.getUint16(2 * (n + i), true) * V_SCALE;
  }
  return { x, v };
}

/** 95th-percentile speed, floored at 1 m/s so a fully jammed run still has a scale. */
export function referenceSpeed(v: Float32Array): number {
  if (v.length === 0) return 1;
  const s = Float32Array.from(v).sort();
  const k = Math.min(s.length - 1, Math.floor(0.95 * s.length));
  return Math.max(1, s[k]);
}

/** Shortest signed distance from a to b on a ring of circumference C. */
export function wrapDelta(a: number, b: number, C: number): number {
  let d = b - a;
  if (d > C / 2) d -= C;
  else if (d < -C / 2) d += C;
  return d;
}

/** Linear interpolation of every vehicle's position and speed at time t (wrap-aware). */
export function sampleAt(run: RunData, C: number, t: number, outX: Float32Array, outV: Float32Array): void {
  const { rec, nVeh, nSamples } = run;
  const k = (t - rec.t0_s) / rec.dt_s;
  let i0 = Math.floor(k);
  let f = k - i0;
  if (i0 < 0) {
    i0 = 0;
    f = 0;
  }
  if (i0 >= nSamples - 1) {
    i0 = nSamples - 1;
    f = 0;
  }
  const i1 = Math.min(i0 + 1, nSamples - 1);
  for (let j = 0; j < nVeh; j++) {
    const x0 = run.x[i0 * nVeh + j];
    const x1 = run.x[i1 * nVeh + j];
    const x = x0 + f * wrapDelta(x0, x1, C);
    outX[j] = ((x % C) + C) % C;
    outV[j] = run.v[i0 * nVeh + j] * (1 - f) + run.v[i1 * nVeh + j] * f;
  }
}

export interface FleetStats {
  mean: number;
  std: number;
  min: number;
  stopped: number;
}

/** Mean, population standard deviation, minimum, and count below `stoppedBelow` m/s. */
export function fleetStats(v: Float32Array, stoppedBelow = 0.5): FleetStats {
  const n = v.length;
  if (n === 0) return { mean: 0, std: 0, min: 0, stopped: 0 };
  let sum = 0;
  let min = Infinity;
  let stopped = 0;
  for (let i = 0; i < n; i++) {
    sum += v[i];
    if (v[i] < min) min = v[i];
    if (v[i] < stoppedBelow) stopped++;
  }
  const mean = sum / n;
  let ss = 0;
  for (let i = 0; i < n; i++) ss += (v[i] - mean) * (v[i] - mean);
  return { mean, std: Math.sqrt(ss / n), min, stopped };
}

async function fetchOk(url: string): Promise<Response> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res;
}

export async function loadIndex(base: string): Promise<IndexFile> {
  const idx = (await (await fetchOk(`${base}index.json`)).json()) as IndexFile;
  if (idx.schema !== 'flowstate-embed-data/1') throw new Error(`unexpected data schema ${idx.schema}`);
  return idx;
}

export async function loadRun(base: string, rec: RunRecord): Promise<RunData> {
  const buf = await (await fetchOk(`${base}${rec.file}`)).arrayBuffer();
  const { x, v } = decodeRun(buf, rec.n_samples, rec.n_vehicles);
  return { rec, nVeh: rec.n_vehicles, nSamples: rec.n_samples, x, v, vRef: referenceSpeed(v) };
}

export async function loadObserved(base: string, file: string): Promise<ObservedField> {
  return (await (await fetchOk(`${base}${file}`)).json()) as ObservedField;
}

export function findRun(index: IndexFile, sel: Selection): RunRecord | undefined {
  const act = sel.n_av === 0 ? 0 : sel.activation_s;
  return index.runs.find(
    (r) => r.n_vehicles === sel.n_vehicles && r.n_av === sel.n_av && r.activation_s === act && r.seed === sel.seed,
  );
}

/** The uncontrolled run with the same ring and seed (common random numbers). */
export function baselineFor(index: IndexFile, rec: RunRecord): RunRecord | undefined {
  return index.runs.find((r) => r.n_vehicles === rec.n_vehicles && r.n_av === 0 && r.seed === rec.seed);
}
