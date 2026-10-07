/**
 * Unit conversions and speed formatting. The data pack is SI (m/s); the page reads in
 * km/h with mph alongside (docs/design/DASHBOARD_DESIGN.md §4.7, §7.3). Named constants
 * only, never inline magic numbers (CLAUDE.md §2).
 */

/** km/h per m/s (exact). */
export const KMH_PER_MS = 3.6;
/** mph per m/s (= 3600 / 1609.344). */
export const MS_TO_MPH = 2.2369362920544;
/** km per statute mile (exact). */
export const KM_PER_MI = 1.609344;

export function msToKmh(ms: number): number {
  return ms * KMH_PER_MS;
}

export function msToMph(ms: number): number {
  return ms * MS_TO_MPH;
}

export function kmhToMph(kmh: number): number {
  return kmh / KM_PER_MI;
}

export function mphToKmh(mph: number): number {
  return mph * KM_PER_MI;
}

/** "12.3 km/h" from m/s. */
export function formatKmh(ms: number, digits = 1): string {
  return `${msToKmh(ms).toFixed(digits)} km/h`;
}

/** "7.6 mph" from m/s. */
export function formatMph(ms: number, digits = 1): string {
  return `${msToMph(ms).toFixed(digits)} mph`;
}

/** "47 km/h (29 mph)" from m/s. */
export function formatSpeedKmhMph(ms: number, digits = 0): string {
  return `${formatKmh(ms, digits)} (${formatMph(ms, digits)})`;
}

/** "47 km/h (29 mph)" from km/h. */
export function formatKmhValue(kmh: number, digits = 0): string {
  return `${kmh.toFixed(digits)} km/h (${kmhToMph(kmh).toFixed(digits)} mph)`;
}

/**
 * Tick values 0, step, 2·step, … ≤ top, with step a "nice" number (1, 2, 2.5 or 5 × 10^k)
 * chosen so there are at most `maxTicks` intervals.
 */
export function niceTicks(top: number, maxTicks = 6): number[] {
  if (!(top > 0) || !Number.isFinite(top)) return [0];
  const raw = top / Math.max(1, maxTicks);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw - 1e-12) ?? 10 * mag;
  const out: number[] = [];
  for (let k = 0; k * step <= top + 1e-9; k++) out.push(+(k * step).toFixed(6));
  return out;
}
