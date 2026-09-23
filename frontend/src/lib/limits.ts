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

/** `SimSpec.duration_s` is `Field(gt=0)` — one second is the smallest value
 * the API accepts, and an empty or zeroed field must never be sent as 0. */
export const MIN_DURATION_S = 1;

/** The launcher's own ceiling on `sim.duration_s`: 24 simulated hours. The
 * API sets no upper bound, so this is a sanity stop on a typo (a stray zero
 * turning a 20-minute run into a 3-hour one per replicate), not a mirror of a
 * server limit. */
export const MAX_DURATION_S = 86_400;

/** `ScenarioConfig.seed` is a plain int, but SUMO's `--seed` and the RNG
 * helpers are non-negative, so the launcher clamps to a 32-bit unsigned range
 * rather than posting a negative seed the runner would reject. */
export const MIN_SEED = 0;
export const MAX_SEED = 2_147_483_647;

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

/** The shortest measurement window a launch must leave after the warm-up.
 *
 * The runner discards everything before `sim.warmup_s`, so a duration at or
 * below the warm-up leaves nothing to measure and every replicate dies with
 * "warm-up N s leaves no measurement window". One minute of simulated time is
 * the smallest window worth enqueueing compute for. */
export const MIN_MEASUREMENT_WINDOW_S = 60;

/** Why a launch would leave no usable measurement window, or null when it
 * would. Stated in the numbers the user typed, so the fix is obvious without
 * a round trip to the API. */
export function warmupProblem(durationS: number | null, warmupS: number | null): string | null {
  if (durationS === null || warmupS === null) return null;
  if (!Number.isFinite(durationS) || !Number.isFinite(warmupS) || warmupS <= 0) return null;
  if (durationS > warmupS + MIN_MEASUREMENT_WINDOW_S) return null;
  return (
    `Duration ${durationS} s leaves no measurement window: the scenario discards ` +
    `its first ${warmupS} s as warm-up. Use at least ` +
    `${warmupS + MIN_MEASUREMENT_WINDOW_S + 1} s, or lower the warm-up.`
  );
}

/** Clamp a number-input value into [lo, hi]; non-numeric input falls back. */
export function clampInt(v: number, lo: number, hi: number, fallback = lo): number {
  if (!Number.isFinite(v)) return fallback;
  return Math.min(hi, Math.max(lo, Math.round(v)));
}

/** `clampInt` for a raw `<input type="number">` value, where an emptied field
 * reads as `''` and `Number('')` is **0**, not NaN: clearing the Duration box
 * must fall back to the scenario's own duration, never post `duration_s: 0`
 * (which the API rejects) or silently clamp a cleared field to 1 second. */
export function clampField(raw: string, lo: number, hi: number, fallback: number): number {
  if (raw.trim() === '') return fallback;
  return clampInt(Number(raw), lo, hi, fallback);
}
