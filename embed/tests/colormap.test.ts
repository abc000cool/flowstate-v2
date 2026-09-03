import { describe, expect, it } from 'vitest';
import { buildLUT, speedColor, speedRGB } from '../src/colormap';

describe('speed colour scale', () => {
  it('runs from jam red to free-flow blue and clamps outside [0, 1]', () => {
    expect(speedRGB(0)).toEqual([229, 72, 77]);
    expect(speedRGB(1)).toEqual([42, 120, 214]);
    expect(speedRGB(-3)).toEqual(speedRGB(0));
    expect(speedRGB(7)).toEqual(speedRGB(1));
    expect(speedRGB(Number.NaN)).toEqual(speedRGB(0));
  });
  it('formats css colours and builds a 256-entry table', () => {
    expect(speedColor(0, 10)).toBe('rgb(229,72,77)');
    expect(speedColor(5, 0)).toBe('rgb(229,72,77)');
    const lut = buildLUT();
    expect(lut.length).toBe(768);
    expect([lut[765], lut[766], lut[767]]).toEqual([42, 120, 214]);
  });
});
