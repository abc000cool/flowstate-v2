/** Heatmap colour ramps for the space-time diagrams.
 *
 * SPEED — a diverging-from-dark ramp tuned for wave-front readability on the
 * #0B0E14 console background:
 *
 *     0.00  rgb(10, 16, 42)    deep navy   — jammed / stopped traffic
 *     0.55  rgb(79, 209, 224)  cyan        — transitional flow (accent hue)
 *     1.00  rgb(245, 227, 181) pale amber  — free flow
 *
 * Jammed bands stay barely brighter than the page background so backward-
 * propagating waves read as dark diagonal strokes cutting through the bright
 * free-flow field; the cyan mid-stop gives a sharp luminance gradient exactly
 * where wave fronts live (10–60 km/h).
 *
 * DENSITY — dark-to-hot, low density fading into the panel, jam density in
 * the danger hue:
 *
 *     0.00  rgb(13, 17, 26)    near-background — empty road
 *     0.60  rgb(245, 184, 92)  amber           — building density
 *     1.00  rgb(240, 100, 122) danger red      — jam density
 *
 * Interpolation is piecewise linear in sRGB between anchor stops (rounded to
 * integer channels), so anchors reproduce exactly — unit tested. */

import type { HeatField } from '../api/types';

export type RGB = [number, number, number];

export interface ColorStop {
  /** Normalized position in [0, 1]. */
  at: number;
  rgb: RGB;
}

export const SPEED_STOPS: ColorStop[] = [
  { at: 0.0, rgb: [10, 16, 42] },
  { at: 0.55, rgb: [79, 209, 224] },
  { at: 1.0, rgb: [245, 227, 181] },
];

export const DENSITY_STOPS: ColorStop[] = [
  { at: 0.0, rgb: [13, 17, 26] },
  { at: 0.6, rgb: [245, 184, 92] },
  { at: 1.0, rgb: [240, 100, 122] },
];

/** Empty (null) bins render as the page background. */
export const NULL_BIN_RGB: RGB = [11, 14, 20];

/** Display normalization domains (SI): speed 0–33.3 m/s (120 km/h desired
 * speed, §3.1); density 0–0.16 veh/m (ρ_jam of the v1_legacy FD preset). */
export const SPEED_DOMAIN_MAX_MS = 33.3;
export const DENSITY_DOMAIN_MAX_VEHM = 0.16;

/** Piecewise-linear sample of a ramp at t ∈ [0, 1] (clamped). Exact at
 * anchor stops. */
export function sampleRamp(stops: ColorStop[], t: number): RGB {
  const first = stops[0];
  const last = stops[stops.length - 1];
  if (t <= first.at) return [...first.rgb];
  if (t >= last.at) return [...last.rgb];
  for (let i = 1; i < stops.length; i++) {
    const hi = stops[i];
    if (t <= hi.at) {
      const lo = stops[i - 1];
      if (t === hi.at) return [...hi.rgb];
      const f = (t - lo.at) / (hi.at - lo.at);
      return [
        Math.round(lo.rgb[0] + (hi.rgb[0] - lo.rgb[0]) * f),
        Math.round(lo.rgb[1] + (hi.rgb[1] - lo.rgb[1]) * f),
        Math.round(lo.rgb[2] + (hi.rgb[2] - lo.rgb[2]) * f),
      ];
    }
  }
  return [...last.rgb];
}

export function stopsFor(field: HeatField): ColorStop[] {
  return field === 'speed' ? SPEED_STOPS : DENSITY_STOPS;
}

export function domainMaxFor(field: HeatField): number {
  return field === 'speed' ? SPEED_DOMAIN_MAX_MS : DENSITY_DOMAIN_MAX_VEHM;
}

/** Colour for a raw SI bin value (or null) of the given field. */
export function binColor(field: HeatField, value: number | null): RGB {
  if (value === null || !Number.isFinite(value)) return [...NULL_BIN_RGB];
  return sampleRamp(stopsFor(field), value / domainMaxFor(field));
}

/** Speed in m/s -> RGB (fixed display domain so runs are comparable). */
export function speedColor(vMs: number): RGB {
  return binColor('speed', vMs);
}

/** CSS linear-gradient mirroring a ramp, for legends. */
export function rampGradientCSS(stops: ColorStop[]): string {
  const parts = stops.map(
    (s) => `rgb(${s.rgb[0]},${s.rgb[1]},${s.rgb[2]}) ${(s.at * 100).toFixed(0)}%`,
  );
  return `linear-gradient(90deg, ${parts.join(', ')})`;
}

/** Diverging cell colour for sweep matrices: `goodness` ∈ [-1, 1] blends the
 * panel surface toward ok-green (improvement) or danger-red (regression). */
export function deltaColor(goodness: number): string {
  const g = Math.max(-1, Math.min(1, goodness));
  const base: RGB = [17, 21, 31];
  const target: RGB = g >= 0 ? [88, 214, 163] : [240, 100, 122];
  const f = Math.abs(g) * 0.55; // never fully saturated — keep it readable
  const mix = base.map((b, i) => Math.round(b + (target[i] - b) * f)) as unknown as RGB;
  return `rgb(${mix[0]},${mix[1]},${mix[2]})`;
}
