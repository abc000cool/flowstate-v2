/** Contract guard: the dashboard's METRIC_DEFS keys must be exactly the
 * field names of `validation.metrics.Metrics`. The API's aggregate dict is
 * keyed verbatim by `dataclasses.fields(Metrics)` (packages/api/api/main.py
 * → api/results.py), so a key that is not a Metrics field never receives a
 * value and the sweep matrix renders no delta for it. */

import { describe, expect, it } from 'vitest';
import { DEFAULT_SWEEP_METRIC, METRIC_DEFS, metricDef, orderedMetricKeys } from '../lib/metrics';

/** Mirror of the dataclass (packages/validation/validation/metrics.py). The
 * first test pins METRIC_DEFS to this list; the second pins this list to the
 * checked-out Python source, so a field added or renamed on either side
 * fails here. */
const METRICS_DATACLASS_FIELDS = [
  'throughput_veh_h',
  'mean_tt_s',
  'p90_tt_s',
  'sigma_v_spatial_ms',
  'sigma_v_temporal_ms',
  'vmt_veh_km',
  'vht_veh_h',
  'fuel_ml_per_veh_km',
  'wave_count',
  'wave_speed_kmh',
  'wave_amplitude_ms',
];

const METRICS_PY = 'packages/validation/validation/metrics.py';

/** Read the dataclass source. The test file is typechecked by the browser
 * tsconfig (no @types/node), so the node builtin is imported dynamically;
 * vitest runs on node regardless. Vitest's cwd is frontend/ (`npm test`) or
 * the repo root, so walk up from it to the monorepo checkout. */
async function readMetricsPy(): Promise<string> {
  const fsModule = 'node:fs';
  const fs = (await import(/* @vite-ignore */ fsModule)) as {
    readFileSync(path: string, encoding: 'utf8'): string;
    existsSync(path: string): boolean;
  };
  const cwd = (globalThis as { process?: { cwd(): string } }).process?.cwd() ?? '';
  let dir = cwd;
  for (let i = 0; i < 4 && dir; i++) {
    const candidate = `${dir}/${METRICS_PY}`;
    if (fs.existsSync(candidate)) return fs.readFileSync(candidate, 'utf8');
    dir = dir.replace(/\/[^/]+$/, '');
  }
  throw new Error(`${METRICS_PY} not found above ${cwd} — run the frontend tests inside the monorepo`);
}

/** Field names of `class Metrics:` — the typed attribute lines of its body
 * (the docstring's attribute list is indented deeper and carries prose). */
function dataclassFields(source: string): string[] {
  const start = source.indexOf('class Metrics:');
  if (start < 0) return [];
  const body = source.slice(start + 'class Metrics:'.length);
  const end = body.search(/\n(?:def |class |@)/);
  const block = end < 0 ? body : body.slice(0, end);
  return [...block.matchAll(/^ {4}([a-z_][a-z0-9_]*): (?:float|int)(?:\s*=.*)?\s*$/gm)].map((m) => m[1]);
}

describe('metric definitions mirror validation.metrics.Metrics', () => {
  it('METRIC_DEFS keys are exactly the Metrics dataclass fields', () => {
    expect(new Set(METRIC_DEFS.map((d) => d.key))).toEqual(new Set(METRICS_DATACLASS_FIELDS));
    expect(new Set(METRIC_DEFS.map((d) => d.key)).size).toBe(METRIC_DEFS.length);
  });

  it('the mirrored field list matches the checked-out dataclass', async () => {
    const fields = dataclassFields(await readMetricsPy());
    expect(fields.length).toBeGreaterThan(0);
    expect(new Set(fields)).toEqual(new Set(METRICS_DATACLASS_FIELDS));
  });

  it('the default sweep metric is a known, directional def', () => {
    const def = metricDef(DEFAULT_SWEEP_METRIC);
    expect(METRIC_DEFS).toContain(def);
    expect(def.good).not.toBe('neutral');
  });

  it('every def has a unit and a display label', () => {
    for (const d of METRIC_DEFS) {
      expect(d.label.length).toBeGreaterThan(0);
      expect(d.unit.length).toBeGreaterThan(0);
    }
  });

  it('unknown keys fall back to a generic def instead of throwing', () => {
    const def = metricDef('brand_new_metric');
    expect(def).toMatchObject({ key: 'brand_new_metric', label: 'brand new metric', unit: '', good: 'neutral' });
  });

  it('orders known keys canonically and appends unknown ones sorted', () => {
    expect(orderedMetricKeys(['zeta', 'wave_count', 'alpha', 'throughput_veh_h'])).toEqual([
      'throughput_veh_h',
      'wave_count',
      'alpha',
      'zeta',
    ]);
  });
});
