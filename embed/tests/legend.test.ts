import { describe, expect, it } from 'vitest';
import { legendTicks } from '../src/legend';
import { kmhToMph } from '../src/units';

describe('legendTicks', () => {
  it('puts the absolute scale on round km/h and mph ticks without an end label', () => {
    const kmh = legendTicks(120, 6, 0);
    expect(kmh.map((m) => m.label)).toEqual(['0', '20', '40', '60', '80', '100', '120']);
    expect(kmh[kmh.length - 1].pct).toBeCloseTo(100);
    const mph = legendTicks(kmhToMph(120), 8, 0);
    expect(mph.map((m) => m.label)).toEqual(['0', '10', '20', '30', '40', '50', '60', '70']);
    expect(mph[1].pct).toBeCloseTo((10 / kmhToMph(120)) * 100);
  });
  it('labels the top of a data-driven scale when no round tick is near it', () => {
    const m = legendTicks(23.904, 6, 1);
    expect(m.map((x) => x.label)).toEqual(['0', '5', '10', '15', '20', '23.9']);
    expect(m[m.length - 1].pct).toBe(100);
  });
});
