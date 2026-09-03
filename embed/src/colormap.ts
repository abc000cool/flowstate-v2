/** Speed colour scale: jam red → amber → free-flow blue (one scale for every view). */

type RGB = [number, number, number];

const STOPS: [number, RGB][] = [
  [0.0, [229, 72, 77]],
  [0.4, [245, 165, 36]],
  [0.75, [125, 211, 252]],
  [1.0, [42, 120, 214]],
];

/** Colour for a normalised speed u ∈ [0, 1] (clamped). */
export function speedRGB(u: number): RGB {
  const t = Number.isFinite(u) ? Math.min(1, Math.max(0, u)) : 0;
  for (let i = 1; i < STOPS.length; i++) {
    const [u1, c1] = STOPS[i];
    if (t <= u1) {
      const [u0, c0] = STOPS[i - 1];
      const f = u1 === u0 ? 0 : (t - u0) / (u1 - u0);
      return [
        Math.round(c0[0] + f * (c1[0] - c0[0])),
        Math.round(c0[1] + f * (c1[1] - c0[1])),
        Math.round(c0[2] + f * (c1[2] - c0[2])),
      ];
    }
  }
  return STOPS[STOPS.length - 1][1];
}

export function speedColor(v: number, vRef: number): string {
  const [r, g, b] = speedRGB(vRef > 0 ? v / vRef : 0);
  return `rgb(${r},${g},${b})`;
}

/** 256-entry RGB lookup table for pixel rendering. */
export function buildLUT(): Uint8ClampedArray {
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const [r, g, b] = speedRGB(i / 255);
    lut[3 * i] = r;
    lut[3 * i + 1] = g;
    lut[3 * i + 2] = b;
  }
  return lut;
}

export const EMPTY_BIN: RGB = [24, 26, 31];
