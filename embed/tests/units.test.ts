import { describe, expect, it } from 'vitest';
import {
  formatKmh,
  formatKmhValue,
  formatMph,
  formatSpeedKmhMph,
  KM_PER_MI,
  KMH_PER_MS,
  kmhToMph,
  MS_TO_MPH,
  mphToKmh,
  msToKmh,
  niceTicks,
} from '../src/units';

describe('unit constants', () => {
  it('are the exact definitions', () => {
    expect(KMH_PER_MS).toBe(3.6);
    expect(KM_PER_MI).toBe(1.609344);
    expect(MS_TO_MPH).toBeCloseTo(3600 / 1609.344, 12);
  });
  it('agree with each other', () => {
    expect(kmhToMph(msToKmh(13.05))).toBeCloseTo(13.05 * MS_TO_MPH, 10);
    expect(mphToKmh(kmhToMph(120))).toBeCloseTo(120, 10);
  });
});

describe('speed formatting', () => {
  it('reads km/h with mph alongside', () => {
    expect(formatKmh(13.0556)).toBe('47.0 km/h');
    expect(formatMph(13.0556)).toBe('29.2 mph');
    expect(formatSpeedKmhMph(13.0556)).toBe('47 km/h (29 mph)');
    expect(formatKmhValue(47)).toBe('47 km/h (29 mph)');
    expect(formatKmh(0.5)).toBe('1.8 km/h');
  });
});

describe('niceTicks', () => {
  it('steps by 1, 2, 2.5 or 5 × 10^k from zero up to the top', () => {
    expect(niceTicks(120, 6)).toEqual([0, 20, 40, 60, 80, 100, 120]);
    expect(niceTicks(120 / KM_PER_MI, 8)).toEqual([0, 10, 20, 30, 40, 50, 60, 70]);
    expect(niceTicks(23.9, 6)).toEqual([0, 5, 10, 15, 20]);
    expect(niceTicks(12.2, 6)).toEqual([0, 2.5, 5, 7.5, 10, 12.5].filter((v) => v <= 12.2));
  });
  it('degrades to [0] for a non-positive or non-finite top', () => {
    expect(niceTicks(0)).toEqual([0]);
    expect(niceTicks(Number.NaN)).toEqual([0]);
  });
});
