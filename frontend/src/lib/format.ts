/** Typed unit/axis formatting helpers. SI internally (s, m, m/s); display in
 * min, km, km/h. Mirrors the flowstate_core.units convention of explicit,
 * named conversions — no inline magic numbers at call sites. */

export const MS_TO_KMH = 3.6;
export const S_PER_MIN = 60;
export const M_PER_KM = 1000;
/** m/s -> mph (exact: 3600 / 1609.344). */
export const MS_TO_MPH = 2.2369362920544;
/** Kilometres per international mile (exact); also km/h per mph. */
export const KM_PER_MI = 1.609344;

/** Trim a fixed-decimal string: 5.0 -> "5", 1.50 -> "1.5", 150 -> "150". */
function trim(v: number, maxDigits: number): string {
  let s = v.toFixed(maxDigits);
  if (s.includes('.')) s = s.replace(/0+$/, '').replace(/\.$/, '');
  return s === '-0' ? '0' : s;
}

/** Seconds -> minutes axis label, e.g. 300 -> "5 min", 90 -> "1.5 min". */
export function formatTimeMin(seconds: number): string {
  return `${trim(seconds / S_PER_MIN, 1)} min`;
}

/** Metres -> kilometres axis label, e.g. 2500 -> "2.5 km", 10000 -> "10 km". */
export function formatDistKm(metres: number): string {
  return `${trim(metres / M_PER_KM, 1)} km`;
}

/** m/s -> km/h readout, e.g. 30 -> "108 km/h". */
export function formatSpeedKmh(ms: number): string {
  return `${Math.round(ms * MS_TO_KMH)} km/h`;
}

/** veh/m -> veh/km readout, e.g. 0.038 -> "38 veh/km". */
export function formatDensityVehKm(vehPerM: number): string {
  return `${Math.round(vehPerM * M_PER_KM)} veh/km`;
}

/** m/s -> both units, e.g. 13.06 -> "47 km/h (29 mph)" (the heatmap readout;
 * the dashboard reads in km/h with mph alongside, like the report). */
export function formatSpeedKmhMph(ms: number): string {
  return `${formatSpeedKmh(ms)} (${Math.round(ms * MS_TO_MPH)} mph)`;
}

/** veh/m -> both units, e.g. 0.038 -> "38 veh/km (61 veh/mi)". */
export function formatDensityVehKmMi(vehPerM: number): string {
  return `${formatDensityVehKm(vehPerM)} (${Math.round(vehPerM * M_PER_KM * KM_PER_MI)} veh/mi)`;
}

/** Seconds -> a bare minutes tick label, e.g. 300 -> "5", 90 -> "1.5" (the
 * axis title carries the unit). */
export function formatTickMin(seconds: number): string {
  return trim(seconds / S_PER_MIN, 1);
}

/** Metres -> a bare tick label in the unit `distUnit(span)` names: km for a
 * corridor, m for a ring under 1 km. */
export function formatTickDist(metres: number, span: number): string {
  return span < M_PER_KM ? trim(metres, 0) : trim(metres / M_PER_KM, 1);
}

/** The distance unit an axis of this span is labelled in. */
export function distUnit(span: number): 'm' | 'km' {
  return span < M_PER_KM ? 'm' : 'km';
}

/** Fixed-digit numeric formatting with thousands grouping for big values. */
export function formatNumber(v: number | null, digits = 1): string {
  // null: the API's CIOut reports null when a metric is undefined for every replicate.
  if (v === null || !Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 10000 && digits === 0) {
    return v.toLocaleString('en-US', { maximumFractionDigits: 0 });
  }
  return v.toFixed(digits);
}

/** Signed percentage, e.g. -0.231 -> "-23.1%". */
export function formatDeltaPct(frac: number): string {
  const pct = frac * 100;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}%`;
}

/** Nice tick step from the 1-2-5 ladder covering `span / target`. */
function niceStep(span: number, target: number): number {
  const raw = span / Math.max(1, target);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 5, 10]) {
    if (mag * m >= raw) return mag * m;
  }
  return mag * 10;
}

/** Tick positions (in seconds) at whole nice-minute multiples. */
export function timeTicks(t0: number, t1: number, target = 6): number[] {
  const stepMin = niceStep((t1 - t0) / S_PER_MIN, target);
  const step = stepMin * S_PER_MIN;
  const ticks: number[] = [];
  for (let t = Math.ceil(t0 / step) * step; t <= t1 + 1e-9; t += step) {
    ticks.push(t);
  }
  return ticks;
}

/** Tick positions (in metres) at whole nice-kilometre multiples; falls back
 * to metre-scale steps for short (ring) networks. */
export function spaceTicks(x0: number, x1: number, target = 5): number[] {
  const spanKm = (x1 - x0) / M_PER_KM;
  const step =
    spanKm >= 1 ? niceStep(spanKm, target) * M_PER_KM : niceStep(x1 - x0, target);
  const ticks: number[] = [];
  for (let x = Math.ceil(x0 / step) * step; x <= x1 + 1e-9; x += step) {
    ticks.push(x);
  }
  return ticks;
}

/** What a failed run says when the service sent no text with the failure. */
export const NO_FAILURE_REASON = 'the service reported no reason';

/** The failure line of a run (`RunOut.error` / `RunOut.error_kind`).
 *
 * A failed run must say *why*: "RUN FAILED 2/2" with the reason dropped sends
 * the user to the server logs for something the API already answered. When the
 * service sent no text, that absence is stated rather than rendered as blank. */
export function failureReason(error?: string | null, kind?: string | null): string {
  const body = (error ?? '').trim() || NO_FAILURE_REASON;
  const k = (kind ?? '').trim();
  return k ? `${k}: ${body}` : body;
}

/** Space axis label that adapts to network scale (m for rings, km else). */
export function formatDistAdaptive(metres: number, span: number): string {
  if (span < M_PER_KM) return `${trim(metres, 0)} m`;
  return formatDistKm(metres);
}

/** A failed fetch in the server's own words, with the HTTP status prefixed
 * when there is one: `HTTP 422 — observations_path is outside …` (§9.10). The
 * status is read off the error (`api/client.ApiError.status`) without
 * importing the client, so this stays a pure formatter. */
export function formatFetchError(err: unknown): string {
  const msg = (err instanceof Error ? err.message : String(err ?? '')).trim() || 'no detail';
  const status =
    err !== null && typeof err === 'object' && typeof (err as { status?: unknown }).status === 'number'
      ? (err as { status: number }).status
      : null;
  if (status === null || msg.startsWith(String(status))) return msg;
  return `HTTP ${status} — ${msg}`;
}
