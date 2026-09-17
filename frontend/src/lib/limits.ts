/** Client-side mirrors of the API's hard request caps, plus the cost gate the
 * launchers put in front of an expensive click.
 *
 * The caps are the ones the service enforces (packages/api/api/schemas.py and
 * packages/flowstate_core/flowstate_core/config.py); mirroring them in the
 * form controls turns a raw pydantic 422 into a field that simply will not go
 * past the limit. They are *not* a substitute for server validation. */

/** `api.schemas.MAX_REPLICATES` — per run and per sweep cell. */
export const MAX_REPLICATES = 200;

/** `api.schemas.MAX_SWEEP_CELLS` — penetrations × compliances × controllers. */
export const MAX_SWEEP_CELLS = 200;

/** `CorridorNetwork.lanes` is `Field(ge=1, le=8)`. */
export const MAX_LANES = 8;

/** Ask before committing more than this much simulated time in one click.
 * 600 simulated minutes is 10 sim-hours — e.g. 20 replicates × 30 sim-min. */
export const CONFIRM_SIM_MINUTES = 600;

/** Total simulated minutes a launch commits to. */
export function simMinutes(replicates: number, durationS: number): number {
  if (!Number.isFinite(replicates) || !Number.isFinite(durationS)) return 0;
  return (Math.max(0, replicates) * Math.max(0, durationS)) / 60;
}

/** Whether a launch is big enough to deserve an explicit confirmation. */
export function needsLaunchConfirm(replicates: number, durationS: number): boolean {
  return simMinutes(replicates, durationS) > CONFIRM_SIM_MINUTES;
}

/** Human total, e.g. 2600 -> "2,600 sim-min (43.3 sim-h)". */
export function describeSimMinutes(minutes: number): string {
  const mins = minutes.toLocaleString('en-US', { maximumFractionDigits: 0 });
  return `${mins} sim-min (${(minutes / 60).toFixed(1)} sim-h)`;
}

/** Clamp a number-input value into [lo, hi]; non-numeric input falls back. */
export function clampInt(v: number, lo: number, hi: number, fallback = lo): number {
  if (!Number.isFinite(v)) return fallback;
  return Math.min(hi, Math.max(lo, Math.round(v)));
}
