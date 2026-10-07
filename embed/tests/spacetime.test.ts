import { describe, expect, it } from 'vitest';
import type { RunData, RunRecord } from '../src/data';
import { headwayField } from '../src/spacetime';

function run(x: number[][], v: number[][]): RunData {
  const nSamples = x.length;
  const nVeh = x[0].length;
  const rec = { id: 't', n_vehicles: nVeh, t0_s: 0, dt_s: 0.5, n_samples: nSamples } as unknown as RunRecord;
  return { rec, nVeh, nSamples, x: Float32Array.from(x.flat()), v: Float32Array.from(v.flat()), vRef: 1 };
}

describe('headwayField', () => {
  it('gives every position the speed of the vehicle whose headway it lies in, wrapping at the seam', () => {
    // a 10 m ring (20 bins of 0.5 m): vehicle 0 at 2 m (stopped), vehicle 1 at 6 m (at the scale top)
    const f = headwayField(run([[2, 6]], [[0, 4]]), 10, 4);
    expect(f.w).toBe(1);
    expect(f.h).toBe(20);
    const at = (xm: number): number => f.idx[(f.h - 1 - Math.floor(xm * 2)) * f.w + 0];
    expect(at(2)).toBe(0); //   2–6 m: vehicle 0's headway
    expect(at(5.9)).toBe(0);
    expect(at(6)).toBe(255); // 6–10 m and 0–2 m: vehicle 1's headway, across the seam
    expect(at(9.9)).toBe(255);
    expect(at(0)).toBe(255);
    expect(at(1.9)).toBe(255);
  });
  it('orders vehicles by position whatever their column order', () => {
    const f = headwayField(run([[8, 1]], [[2, 1]]), 10, 4);
    const at = (xm: number): number => f.idx[(f.h - 1 - Math.floor(xm * 2)) * f.w + 0];
    expect(at(3)).toBe(Math.round((1 / 4) * 255)); // 1–8 m belongs to the vehicle at 1 m
    expect(at(9)).toBe(Math.round((2 / 4) * 255)); // 8–11 m (wrapping) to the vehicle at 8 m
  });
});
