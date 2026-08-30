/** Display definitions for the standard metrics (CLAUDE.md §0.3): throughput,
 * mean travel time, σ_v, fuel, wave count, wave speed. Unknown metric keys
 * from the API still render via the generic fallback. */

export interface MetricDef {
  key: string;
  label: string;
  unit: string;
  digits: number;
  /** Which direction is an improvement (drives sweep delta colouring). */
  good: 'up' | 'down' | 'neutral';
}

/** Labels carry their final display case — the UI must NOT css-uppercase
 * them, or the Greek σ becomes Σ. */
export const METRIC_DEFS: MetricDef[] = [
  { key: 'throughput_vph', label: 'THROUGHPUT', unit: 'veh/h', digits: 0, good: 'up' },
  { key: 'mean_travel_time_s', label: 'MEAN TRAVEL TIME', unit: 's', digits: 1, good: 'down' },
  { key: 'sigma_v', label: 'σ_v', unit: 'm/s', digits: 2, good: 'down' },
  { key: 'fuel_ml_per_vkm', label: 'FUEL', unit: 'ml/veh·km', digits: 1, good: 'down' },
  { key: 'wave_count', label: 'WAVE COUNT', unit: 'waves', digits: 1, good: 'down' },
  { key: 'wave_speed_kmh', label: 'WAVE SPEED', unit: 'km/h', digits: 1, good: 'neutral' },
];

export function metricDef(key: string): MetricDef {
  const found = METRIC_DEFS.find((d) => d.key === key);
  if (found) return found;
  return { key, label: key.replace(/_/g, ' '), unit: '', digits: 2, good: 'neutral' };
}

/** Order metric keys: known defs first (in canonical order), then the rest. */
export function orderedMetricKeys(keys: string[]): string[] {
  const known = METRIC_DEFS.map((d) => d.key).filter((k) => keys.includes(k));
  const rest = keys.filter((k) => !known.includes(k)).sort();
  return [...known, ...rest];
}

/** Reporting standard: results below 20 replicates are underpowered
 * (CLAUDE.md §0.6). */
export const MIN_REPLICATES = 20;
