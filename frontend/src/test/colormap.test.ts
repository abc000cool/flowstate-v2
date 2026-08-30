import { describe, expect, it } from 'vitest';
import {
  binColor,
  DENSITY_STOPS,
  NULL_BIN_RGB,
  sampleRamp,
  SPEED_DOMAIN_MAX_MS,
  SPEED_STOPS,
  speedColor,
} from '../lib/colormap';

describe('speed ramp (deep navy → cyan → pale amber)', () => {
  it('returns the exact anchor colours at anchor stops', () => {
    expect(sampleRamp(SPEED_STOPS, 0)).toEqual([10, 16, 42]);
    expect(sampleRamp(SPEED_STOPS, 0.55)).toEqual([79, 209, 224]);
    expect(sampleRamp(SPEED_STOPS, 1)).toEqual([245, 227, 181]);
  });

  it('clamps outside [0, 1]', () => {
    expect(sampleRamp(SPEED_STOPS, -0.4)).toEqual([10, 16, 42]);
    expect(sampleRamp(SPEED_STOPS, 1.7)).toEqual([245, 227, 181]);
  });

  it('interpolates linearly between anchors', () => {
    // midpoint of the first segment: t = 0.275
    const [r, g, b] = sampleRamp(SPEED_STOPS, 0.275);
    expect(r).toBe(Math.round((10 + 79) / 2));
    expect(g).toBe(Math.round((16 + 209) / 2));
    expect(b).toBe(Math.round((42 + 224) / 2));
  });

  it('maps SI speeds through the fixed display domain', () => {
    expect(speedColor(0)).toEqual([10, 16, 42]);
    expect(speedColor(SPEED_DOMAIN_MAX_MS)).toEqual([245, 227, 181]);
    expect(speedColor(0.55 * SPEED_DOMAIN_MAX_MS)).toEqual([79, 209, 224]);
  });
});

describe('density ramp (dark → amber → danger)', () => {
  it('returns the exact anchor colours at anchor stops', () => {
    expect(sampleRamp(DENSITY_STOPS, 0)).toEqual([13, 17, 26]);
    expect(sampleRamp(DENSITY_STOPS, 0.6)).toEqual([245, 184, 92]);
    expect(sampleRamp(DENSITY_STOPS, 1)).toEqual([240, 100, 122]);
  });
});

describe('null bins', () => {
  it('renders empty bins as the page background colour', () => {
    expect(binColor('speed', null)).toEqual(NULL_BIN_RGB);
    expect(binColor('density', null)).toEqual(NULL_BIN_RGB);
  });
});
