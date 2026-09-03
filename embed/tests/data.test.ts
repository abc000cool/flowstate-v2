import { describe, expect, it } from 'vitest';
import {
  decodeRun,
  fleetStats,
  referenceSpeed,
  sampleAt,
  wrapDelta,
  type RunData,
  type RunRecord,
} from '../src/data';

function rec(nVeh: number, nSamples: number): RunRecord {
  return {
    id: 't',
    n_vehicles: nVeh,
    n_av: 0,
    activation_s: 0,
    seed: 1,
    controller: null,
    config_hash: 'x',
    file: 'runs/t.bin',
    t0_s: 0,
    dt_s: 0.5,
    n_samples: nSamples,
    av_index: [],
    sigma_v_per_minute_ms: [],
    mean_v_per_minute_ms: [],
    min_v_per_minute_ms: [],
    last300: { sigma_v_ms: 0, mean_v_ms: 0, stopped_fraction: 0 },
  };
}

function encode(x: number[], v: number[]): ArrayBuffer {
  const buf = new ArrayBuffer(4 * x.length);
  const dv = new DataView(buf);
  x.forEach((val, i) => dv.setUint16(2 * i, Math.round(val * 10), true));
  v.forEach((val, i) => dv.setUint16(2 * (x.length + i), Math.round(val * 100), true));
  return buf;
}

describe('decodeRun', () => {
  it('round-trips decimetre positions and cm/s speeds, little-endian', () => {
    const buf = encode([0, 115.5, 229.9, 3.2], [0, 1.23, 7.89, 12.3]);
    const { x, v } = decodeRun(buf, 2, 2);
    expect(Array.from(x)).toEqual([0, 115.5, 229.9, 3.2].map((a) => Math.fround(a)));
    expect(Array.from(v)).toEqual([0, 1.23, 7.89, 12.3].map((a) => Math.fround(a)));
  });
  it('rejects a blob of the wrong size', () => {
    expect(() => decodeRun(new ArrayBuffer(10), 2, 2)).toThrow(/expected 16/);
  });
});

describe('wrapDelta', () => {
  it('takes the short way round the ring', () => {
    expect(wrapDelta(225, 5, 230)).toBeCloseTo(10);
    expect(wrapDelta(5, 225, 230)).toBeCloseTo(-10);
    expect(wrapDelta(10, 20, 230)).toBeCloseTo(10);
  });
});

describe('sampleAt', () => {
  const run: RunData = {
    rec: rec(1, 3),
    nVeh: 1,
    nSamples: 3,
    x: Float32Array.from([220, 5, 20]),
    v: Float32Array.from([2, 4, 6]),
    vRef: 6,
  };
  const ox = new Float32Array(1);
  const ov = new Float32Array(1);
  it('interpolates linearly and wraps across the ring seam', () => {
    sampleAt(run, 230, 0.25, ox, ov);
    expect(ox[0]).toBeCloseTo(227.5, 4); // halfway from 220 to 235 ≡ 5
    expect(ov[0]).toBeCloseTo(3, 5);
    sampleAt(run, 230, 0.75, ox, ov);
    expect(ox[0]).toBeCloseTo(12.5, 4);
  });
  it('clamps before the first and after the last sample', () => {
    sampleAt(run, 230, -5, ox, ov);
    expect(ox[0]).toBeCloseTo(220);
    sampleAt(run, 230, 99, ox, ov);
    expect(ox[0]).toBeCloseTo(20);
    expect(ov[0]).toBeCloseTo(6);
  });
});

describe('fleetStats / referenceSpeed', () => {
  it('computes mean, population std, min and stopped count', () => {
    const s = fleetStats(Float32Array.from([0.2, 2, 4, 6]));
    expect(s.mean).toBeCloseTo(3.05);
    expect(s.min).toBeCloseTo(0.2);
    expect(s.stopped).toBe(1);
    expect(s.std).toBeCloseTo(Math.sqrt(((0.2 - 3.05) ** 2 + (2 - 3.05) ** 2 + (4 - 3.05) ** 2 + (6 - 3.05) ** 2) / 4), 5);
  });
  it('uses the 95th percentile with a 1 m/s floor', () => {
    expect(referenceSpeed(Float32Array.from([0, 0, 0, 0.1]))).toBe(1);
    const v = Float32Array.from({ length: 100 }, (_, i) => i / 10);
    expect(referenceSpeed(v)).toBeCloseTo(9.5, 5);
  });
});
