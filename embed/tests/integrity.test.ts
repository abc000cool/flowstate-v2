/** The committed data pack must be internally consistent with its index. */
import { existsSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { decodeRun, type IndexFile } from '../src/data';

const DATA = join(__dirname, '..', 'public', 'data');
const INDEX = join(DATA, 'index.json');

describe('data pack', () => {
  it('exists (regenerate with scripts/website_sim_pack.py)', () => {
    expect(existsSync(INDEX)).toBe(true);
  });
  if (!existsSync(INDEX)) return;
  const idx = JSON.parse(readFileSync(INDEX, 'utf8')) as IndexFile;

  it('covers the full grid exactly once', () => {
    const expected =
      idx.grid.n_vehicles.length *
      idx.grid.seeds.length *
      (1 + (idx.grid.n_av.length - 1) * idx.grid.activation_s.length);
    expect(idx.runs.length).toBe(expected);
    expect(new Set(idx.runs.map((r) => r.id)).size).toBe(idx.runs.length);
  });

  it('every run decodes, stays on the ring, and matches its record', () => {
    const C = idx.scenario.circumference_m;
    for (const r of idx.runs) {
      const p = join(DATA, r.file);
      expect(statSync(p).size, r.id).toBe(4 * r.n_samples * r.n_vehicles);
      const buf = readFileSync(p);
      const { x, v } = decodeRun(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), r.n_samples, r.n_vehicles);
      let xmax = 0;
      let vmin = 0;
      for (let i = 0; i < x.length; i++) {
        if (x[i] > xmax) xmax = x[i];
        if (v[i] < vmin) vmin = v[i];
      }
      expect(xmax, r.id).toBeLessThan(C + 0.1);
      expect(vmin, r.id).toBeGreaterThanOrEqual(0);
      expect(r.av_index.length, r.id).toBe(r.n_av);
      expect(r.dt_s, r.id).toBeCloseTo(1 / idx.scenario.output_hz, 6);
      expect(r.sigma_v_per_minute_ms.length, r.id).toBeGreaterThanOrEqual(idx.scenario.duration_s / 60);
      expect(r.config_hash, r.id).toMatch(/^[0-9a-f]{12}$/);
      expect(r.controller, r.id).toBe(r.n_av > 0 ? idx.scenario.controller : null);
    }
  });

  it('has an uncontrolled baseline for every ring size and seed', () => {
    for (const n of idx.grid.n_vehicles) {
      for (const s of idx.grid.seeds) {
        expect(idx.runs.some((r) => r.n_vehicles === n && r.seed === s && r.n_av === 0), `n=${n} seed=${s}`).toBe(true);
      }
    }
  });

  it('ships the observed field with matching dimensions', () => {
    const f = JSON.parse(readFileSync(join(DATA, idx.observed.file), 'utf8')) as {
      t_edges_s: number[];
      x_edges_m: number[];
      mean_speed_kmh: (number | null)[][];
    };
    expect(f.mean_speed_kmh.length).toBe(idx.observed.n_t);
    expect(f.mean_speed_kmh[0].length).toBe(idx.observed.n_x);
    expect(f.t_edges_s.length).toBe(idx.observed.n_t + 1);
    expect(f.x_edges_m.length).toBe(idx.observed.n_x + 1);
  });
});
