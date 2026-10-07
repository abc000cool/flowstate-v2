/**
 * "FlowState speed" colour scale (docs/design/DASHBOARD_DESIGN.md §7.1): deep red
 * when stopped, a pale neutral at the midpoint, blue at the top. One scale for every
 * speed colouring in the embed (ring vehicles, time-space field, I-24 field). It is
 * theme-invariant: only the chrome around a plot follows light/dark.
 *
 * Interpolation is piecewise linear in sRGB between the anchors (the brief's stops are
 * 1/8 of the domain apart, which keeps sRGB interpolation close to perceptual).
 */
import { KMH_PER_MS } from './units';

export type RGB = [number, number, number];

/** §7.1 anchors: [position in the domain, sRGB]. */
export const SPEED_STOPS: readonly (readonly [number, RGB])[] = [
  [0.0, [106, 13, 24]], //   #6a0d18  stopped (jam core)
  [0.125, [175, 37, 32]], // #af2520  crawling
  [0.25, [222, 101, 49]], // #de6531  stop-and-go
  [0.375, [241, 175, 93]], // #f1af5d slow
  [0.5, [246, 240, 218]], // #f6f0da  transition (neutral)
  [0.625, [165, 210, 237]], // #a5d2ed recovering
  [0.75, [111, 174, 226]], // #6faee2 near free flow
  [0.875, [68, 135, 208]], // #4487d0 free flow
  [1.0, [42, 97, 177]], //   #2a61b1  free flow at v0
];

/** §7.1 domain top for absolute speed fields: 33.3 m/s = 120 km/h (the IDM v0, CLAUDE.md §3.1). */
export const SPEED_DOMAIN_MAX_MS = 33.3;
export const SPEED_DOMAIN_MAX_KMH = 120;

/** Default wave threshold v_jam_thresh (CLAUDE.md §7.2), marked on the absolute legend. */
export const WAVE_THRESHOLD_KMH = 40;

/** Placeholder for bins with no data in the image pass; the hatch pass paints the themed value on top. */
export const NULL_BIN_RGB: RGB = [241, 240, 239];

/** Colour for a normalised value u ∈ [0, 1] (clamped; NaN reads as 0). */
export function sampleRamp(u: number, stops: readonly (readonly [number, RGB])[] = SPEED_STOPS): RGB {
  const t = Number.isFinite(u) ? Math.min(1, Math.max(0, u)) : 0;
  for (let i = 1; i < stops.length; i++) {
    const [u1, c1] = stops[i];
    if (t <= u1) {
      const [u0, c0] = stops[i - 1];
      const f = u1 === u0 ? 0 : (t - u0) / (u1 - u0);
      return [
        Math.round(c0[0] + f * (c1[0] - c0[0])),
        Math.round(c0[1] + f * (c1[1] - c0[1])),
        Math.round(c0[2] + f * (c1[2] - c0[2])),
      ];
    }
  }
  const last = stops[stops.length - 1][1];
  return [last[0], last[1], last[2]];
}

/** Alias kept for the views: colour for a normalised speed. */
export function speedRGB(u: number): RGB {
  return sampleRamp(u);
}

/** CSS colour for speed v on the domain [0, vTop] (same unit for both). */
export function speedColor(v: number, vTop: number): string {
  const [r, g, b] = sampleRamp(vTop > 0 ? v / vTop : 0);
  return `rgb(${r},${g},${b})`;
}

/** 256-entry RGB lookup table over the normalised domain, for pixel rendering. */
export function buildLUT(): Uint8ClampedArray {
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const [r, g, b] = sampleRamp(i / 255);
    lut[3 * i] = r;
    lut[3 * i + 1] = g;
    lut[3 * i + 2] = b;
  }
  return lut;
}

/** CSS linear-gradient drawn from the same stops (left = 0, right = domain top). */
export function rampGradientCSS(stops: readonly (readonly [number, RGB])[] = SPEED_STOPS): string {
  const parts = stops.map(([u, [r, g, b]]) => `rgb(${r}, ${g}, ${b}) ${+(u * 100).toFixed(2)}%`);
  return `linear-gradient(to right, ${parts.join(', ')})`;
}

/** Colour for an absolute speed in km/h on the §7.1 domain (0–120 km/h, clamped). */
export function absoluteSpeedRGBKmh(kmh: number): RGB {
  return sampleRamp(kmh / SPEED_DOMAIN_MAX_KMH);
}

// The two domain constants must describe the same speed.
if (Math.abs(SPEED_DOMAIN_MAX_MS * KMH_PER_MS - SPEED_DOMAIN_MAX_KMH) > 0.2) {
  throw new Error('speed domain constants disagree');
}
