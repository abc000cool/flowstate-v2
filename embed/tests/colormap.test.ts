import { describe, expect, it } from 'vitest';
import {
  buildLUT,
  NULL_BIN_RGB,
  rampGradientCSS,
  sampleRamp,
  SPEED_DOMAIN_MAX_KMH,
  SPEED_DOMAIN_MAX_MS,
  SPEED_STOPS,
  speedColor,
  speedRGB,
} from '../src/colormap';

const hex = (rgb: readonly number[]): string => `#${rgb.map((c) => c.toString(16).padStart(2, '0')).join('')}`;

describe('FlowState speed scale (DASHBOARD_DESIGN.md §7.1)', () => {
  it('uses the brief anchors verbatim', () => {
    expect(SPEED_STOPS.map(([u]) => u)).toEqual([0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1]);
    expect(SPEED_STOPS.map(([, c]) => hex(c))).toEqual([
      '#6a0d18',
      '#af2520',
      '#de6531',
      '#f1af5d',
      '#f6f0da',
      '#a5d2ed',
      '#6faee2',
      '#4487d0',
      '#2a61b1',
    ]);
    expect(SPEED_DOMAIN_MAX_MS).toBe(33.3);
    expect(SPEED_DOMAIN_MAX_KMH).toBe(120);
    expect(NULL_BIN_RGB).toEqual([241, 240, 239]);
  });

  it('hits every anchor exactly and interpolates linearly between them', () => {
    for (const [u, c] of SPEED_STOPS) expect(sampleRamp(u)).toEqual([...c]);
    const mid = sampleRamp(0.0625); // halfway between the first two anchors
    expect(mid).toEqual([Math.round((106 + 175) / 2), Math.round((13 + 37) / 2), Math.round((24 + 32) / 2)]);
  });

  it('clamps outside [0, 1] and reads NaN as stopped', () => {
    expect(speedRGB(-3)).toEqual(speedRGB(0));
    expect(speedRGB(7)).toEqual(speedRGB(1));
    expect(speedRGB(Number.NaN)).toEqual(speedRGB(0));
  });

  it('formats css colours on a domain and builds a 256-entry table', () => {
    expect(speedColor(0, 10)).toBe('rgb(106,13,24)');
    expect(speedColor(5, 10)).toBe('rgb(246,240,218)');
    expect(speedColor(5, 0)).toBe('rgb(106,13,24)');
    const lut = buildLUT();
    expect(lut.length).toBe(768);
    expect([lut[0], lut[1], lut[2]]).toEqual([106, 13, 24]);
    expect([lut[765], lut[766], lut[767]]).toEqual([42, 97, 177]);
  });

  it('draws the legend gradient from the same stops', () => {
    const css = rampGradientCSS();
    expect(css.startsWith('linear-gradient(to right, rgb(106, 13, 24) 0%')).toBe(true);
    expect(css).toContain('rgb(246, 240, 218) 50%');
    expect(css.endsWith('rgb(42, 97, 177) 100%)')).toBe(true);
  });
});
