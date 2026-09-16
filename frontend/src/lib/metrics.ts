/** Display definitions for the standard metrics (CLAUDE.md §0.3).
 *
 * Keys are the field names of `validation.metrics.Metrics`
 * (packages/validation/validation/metrics.py, docs/CONTRACTS.md §7). The
 * API's `aggregate` dict is keyed verbatim by `dataclasses.fields(Metrics)`
 * with no renaming, so a key that is not a Metrics field never receives a
 * value and the sweep matrix would render no deltas for it.
 * `src/test/metrics.test.ts` checks this mirror against the dataclass.
 * Unknown metric keys from the API still render via the generic fallback. */

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
  { key: 'throughput_veh_h', label: 'THROUGHPUT', unit: 'veh/h', digits: 0, good: 'up' },
  { key: 'mean_tt_s', label: 'MEAN TRAVEL TIME', unit: 's', digits: 1, good: 'down' },
  { key: 'p90_tt_s', label: 'P90 TRAVEL TIME', unit: 's', digits: 1, good: 'down' },
  { key: 'sigma_v_spatial_ms', label: 'σ_v SPATIAL', unit: 'm/s', digits: 2, good: 'down' },
  { key: 'sigma_v_temporal_ms', label: 'σ_v TEMPORAL', unit: 'm/s', digits: 2, good: 'down' },
  { key: 'fuel_ml_per_veh_km', label: 'FUEL', unit: 'ml/veh·km', digits: 1, good: 'down' },
  { key: 'vmt_veh_km', label: 'VMT', unit: 'veh·km', digits: 0, good: 'neutral' },
  { key: 'vht_veh_h', label: 'VHT', unit: 'veh·h', digits: 1, good: 'neutral' },
  { key: 'wave_count', label: 'WAVE COUNT', unit: 'waves', digits: 1, good: 'down' },
  { key: 'wave_speed_kmh', label: 'WAVE SPEED', unit: 'km/h', digits: 1, good: 'neutral' },
  { key: 'wave_amplitude_ms', label: 'WAVE AMPLITUDE', unit: 'm/s', digits: 1, good: 'down' },
];

/** Default metric of the sweep matrix: spatial σ_v is the dampening headline. */
export const DEFAULT_SWEEP_METRIC = 'sigma_v_spatial_ms';

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
