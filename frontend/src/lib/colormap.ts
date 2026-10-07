/** Data colours: the heatmap colour ramps, the sweep matrix's binned
 * diverging classes, and the categorical / ordinal series slots
 * (docs/design/DASHBOARD_DESIGN.md §7).
 *
 * SPEED, "FlowState speed" (§7.1): semantic heat, deep red for stopped
 * traffic, a pale neutral at the 60 km/h transition, blue at free flow. Each
 * arm is monotone in OKLCH lightness (0.34 → 0.955 congested, 0.955 → 0.50
 * free flow), so wave fronts get a crisp light edge. Worst cross-arm
 * separation: ΔE 17.6 deuteranopia, 18.7 protanopia, 19.3 tritanopia (OKLab
 * ×100, Machado 2009). Anchors every 15 km/h:
 *
 *     0.000    0 km/h  #6a0d18  stopped (jam core)
 *     0.125   15 km/h  #af2520  crawling
 *     0.250   30 km/h  #de6531  stop-and-go
 *     0.375   45 km/h  #f1af5d  slow
 *     0.500   60 km/h  #f6f0da  transition (neutral)
 *     0.625   75 km/h  #a5d2ed  recovering
 *     0.750   90 km/h  #6faee2  near free flow
 *     0.875  105 km/h  #4487d0  free flow
 *     1.000  120 km/h  #2a61b1  free flow at v0
 *
 * DENSITY (§7.2): sequential, monotone lightness (OKLCH L 0.975 → 0.355) from
 * a pale empty road to deep red at jam density, so "deep red = congested"
 * holds in both fields.
 *
 * Both ramps are theme-invariant: the plot is a measurement image and reads
 * the same in a screenshot, the report PDF and either theme. Only the chrome
 * around it (frame, ticks, crosshair, null hatch) follows the theme.
 *
 * Interpolation is piecewise linear in sRGB between anchor stops (rounded to
 * integer channels), so anchors reproduce exactly (unit tested). */

import type { HeatField } from '../api/types';

export type RGB = [number, number, number];

export interface ColorStop {
  /** Normalized position in [0, 1]. */
  at: number;
  rgb: RGB;
}

export const SPEED_STOPS: ColorStop[] = [
  { at: 0.0, rgb: [106, 13, 24] },
  { at: 0.125, rgb: [175, 37, 32] },
  { at: 0.25, rgb: [222, 101, 49] },
  { at: 0.375, rgb: [241, 175, 93] },
  { at: 0.5, rgb: [246, 240, 218] },
  { at: 0.625, rgb: [165, 210, 237] },
  { at: 0.75, rgb: [111, 174, 226] },
  { at: 0.875, rgb: [68, 135, 208] },
  { at: 1.0, rgb: [42, 97, 177] },
];

export const DENSITY_STOPS: ColorStop[] = [
  { at: 0.0, rgb: [250, 247, 236] },
  { at: 0.2, rgb: [239, 210, 158] },
  { at: 0.4, rgb: [239, 158, 79] },
  { at: 0.6, rgb: [223, 98, 43] },
  { at: 0.8, rgb: [177, 45, 38] },
  { at: 1.0, rgb: [111, 19, 28] },
];

/** Image-pass placeholder for empty (null) bins: `--viz-null-bg` of the light
 * theme. HeatmapCanvas then paints the themed null background and a 45° hatch
 * over every null bin, so "no data" never reads as stopped traffic. */
export const NULL_BIN_RGB: RGB = [241, 240, 239];

/** Display normalization domains (SI): speed 0–33.3 m/s (120 km/h desired
 * speed, CLAUDE.md §3.1); density 0–0.16 veh/m (ρ_jam of the v1_legacy FD
 * preset). */
export const SPEED_DOMAIN_MAX_MS = 33.3;
export const DENSITY_DOMAIN_MAX_VEHM = 0.16;

/** The wave-detection speed threshold's *default* (CLAUDE.md §7.2
 * `v_jam_thresh`). A run may configure another value, so the legend says
 * "default". */
export const WAVE_THRESHOLD_DEFAULT_KMH = 40;

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

/** "12.5" from 12.5, "25" from 25.0: stop positions in a CSS gradient. */
function pctStop(at: number): string {
  return `${Number((at * 100).toFixed(3))}%`;
}

/** CSS linear-gradient mirroring a ramp, for legends. */
export function rampGradientCSS(stops: ColorStop[]): string {
  const parts = stops.map((s) => `rgb(${s.rgb[0]},${s.rgb[1]},${s.rgb[2]}) ${pctStop(s.at)}`);
  return `linear-gradient(90deg, ${parts.join(', ')})`;
}

/* ------------------- sweep matrix: binned diverging ------------------- */

/** One class of the sweep matrix's binned diverging scale (§7.4): neutral,
 * better arm 1–4 (blue) or worse arm 1–4 (red). The CSS colours the cell from
 * the `--viz-div-*` tokens, so a theme flip needs no JavaScript. */
export type DeltaClass =
  | 'delta-n'
  | 'delta-b1'
  | 'delta-b2'
  | 'delta-b3'
  | 'delta-b4'
  | 'delta-w1'
  | 'delta-w2'
  | 'delta-w3'
  | 'delta-w4';

/** Lower edges of classes 1–4, in percent of |s|: below 2 % is neutral;
 * 2–10, 10–25, 25–50 and ≥ 50 % are classes 1, 2, 3 and 4. Each lower edge
 * is inclusive. Discrete classes, not a gradient (Carbon). */
export const DELTA_CLASS_EDGES_PCT = [2, 10, 25, 50] as const;

/** The class of a *signed improvement* `s` (positive = better), where
 * `s = (good === 'down' ? -Δ : Δ)` and `Δ = (cell − ref) / |ref|`.
 *
 * |s| is first rounded to the precision the cell prints (0.1 %, see
 * `formatDeltaPct`), so the colour always agrees with the printed number: a
 * cell reading "+10.0%" is class 2 even when its unrounded value is 0.09996.
 * A non-finite `s` has no class and is neutral. */
export function deltaClass(s: number): DeltaClass {
  if (!Number.isFinite(s)) return 'delta-n';
  // the same rounding `formatDeltaPct` prints with (toFixed(1) of the percent)
  const magPct = Number((Math.abs(s) * 100).toFixed(1));
  let k = 0;
  for (const edge of DELTA_CLASS_EDGES_PCT) if (magPct >= edge) k += 1;
  if (k === 0) return 'delta-n';
  return `delta-${s > 0 ? 'b' : 'w'}${k}` as DeltaClass;
}

/** The signed improvement of a relative delta for a metric whose good
 * direction is `good` (§7.4). `neutral` metrics are read as `up`, as the
 * brief's formula does; the sweep never offers them. */
export function signedImprovement(delta: number, good: 'up' | 'down' | 'neutral'): number {
  return good === 'down' ? -delta : delta;
}

/** Legend of the binned scale, worst to best, as the matrix's legend row
 * reads left to right (§7.4). */
export const DELTA_LEGEND: { cls: DeltaClass; label: string }[] = [
  { cls: 'delta-w4', label: '≥50' },
  { cls: 'delta-w3', label: '25–50' },
  { cls: 'delta-w2', label: '10–25' },
  { cls: 'delta-w1', label: '2–10' },
  { cls: 'delta-n', label: '±2' },
  { cls: 'delta-b1', label: '2–10' },
  { cls: 'delta-b2', label: '10–25' },
  { cls: 'delta-b3', label: '25–50' },
  { cls: 'delta-b4', label: '≥50' },
];

/* ------------------- categorical and ordinal series ------------------- */

/** Fixed categorical slots (§7.5): colour follows the entity, never its rank,
 * and matches `CONTROLLER_COLORS` in scripts/m3_analyze_sweep.py so the
 * dashboard and the report figures agree. Values are CSS custom properties
 * (themed in tokens.css); SVG uses them in `fill`/`stroke` directly. */
export const SERIES_VARS: Readonly<Record<string, string>> = {
  follower_stopper: 'var(--viz-series-1)',
  pi_saturation: 'var(--viz-series-2)',
  jad: 'var(--viz-series-3)',
  vsl: 'var(--viz-series-4)',
  pi_meanfrac: 'var(--viz-series-5)',
};

/** "Baseline (no controlled vehicles)": a neutral, not a categorical slot. */
export const BASELINE_VAR = 'var(--viz-baseline)';

/** The series colour of a controller or strategy. `null`/`none` is the
 * uncontrolled baseline. An entity with no slot gets null: a sixth series is
 * never a generated hue; fold it into "Other" or facet instead. */
export function seriesVar(entity: string | null | undefined): string | null {
  if (entity == null || entity === 'none' || entity === 'baseline') return BASELINE_VAR;
  return SERIES_VARS[entity] ?? null;
}

/** Ordinal steps (§7.5) for penetration or compliance as series: never
 * categorical. Light to dark in the light theme (lighter-larger in dark). */
export const ORDINAL_VARS = [
  'var(--viz-ord-1)',
  'var(--viz-ord-2)',
  'var(--viz-ord-3)',
  'var(--viz-ord-4)',
] as const;

/** The ordinal step of the i-th of n ordered levels (n ≤ 4 uses the steps in
 * order; more levels are spread over the four steps, monotonically). */
export function ordinalVar(i: number, n: number): string {
  const last = ORDINAL_VARS.length - 1;
  if (n <= 1) return ORDINAL_VARS[last];
  if (n <= ORDINAL_VARS.length) return ORDINAL_VARS[Math.min(last, Math.max(0, i))];
  const k = Math.round((Math.min(n - 1, Math.max(0, i)) * last) / (n - 1));
  return ORDINAL_VARS[k];
}
