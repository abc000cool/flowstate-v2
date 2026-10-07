/** The pure logic behind Compare (views/CompareView.tsx): two finished runs
 * side by side, typically an uncontrolled baseline (A) and a controlled run
 * (B) on the same scenario.
 *
 * What the difference columns are, and what they are not:
 *
 * - `B − A` is the difference of the two runs' means exactly as the API
 *   reported them (`CIOut.mean`), in the metric's own unit; `B vs A` is that
 *   difference over |A|, the same ratio the sweep matrix prints for a cell
 *   against its baseline. Both are arithmetic on two server numbers.
 * - Neither carries an interval. Each run's 95% CI is its own t-interval over
 *   its own replicates; the two are shown side by side and are not pooled.
 *   `MetricsOut.replicates` would let the browser compute a Welch or a
 *   seed-paired interval, and it does not: a statistic computed on the client
 *   is exactly what the dashboard leaves to the API
 *   (docs/design/DASHBOARD_DESIGN.md principle 3 and §14.3).
 * - A metric a run's aggregate has no entry for is "not recorded"; one no
 *   replicate produced (`CIOut.n === 0`) is "no observations". Neither is a
 *   zero, and no difference is printed against either.
 *
 * Nothing here fetches. */

import type {
  AggregateStat,
  RunDetail,
  RunSummary,
  ScenarioSummary,
  Seed,
  SweepDetail,
  SweepStrategy,
  Tier,
} from '../api/types';
import { truncateMiddle } from '../components/ui/CopyButton';
import { formatDeltaPct, formatNumber } from './format';
import {
  groupedMetricKeys,
  hasNoObservations,
  metricDef,
  MIN_REPLICATES,
  type MetricGroup,
} from './metrics';

/* ------------------------------ metrics ------------------------------ */

/** The headline metrics (CLAUDE.md §0.3: throughput, mean travel time, σ_v,
 * fuel, wave count and amplitude). Their rows are listed even when neither
 * run recorded them, so an absent headline reads "not recorded" instead of
 * silently vanishing from the table. */
export const HEADLINE_METRIC_KEYS = [
  'throughput_veh_h',
  'mean_tt_s',
  'sigma_v_spatial_ms',
  'sigma_v_temporal_ms',
  'wave_count',
  'wave_amplitude_ms',
  'fuel_ml_per_veh_km',
];

/** How the table words a run without a value. */
export const NOT_RECORDED_LABEL = 'not recorded';

export const NOT_RECORDED_TITLE =
  'This run’s metrics have no entry for this metric: the service did not record it (a run ' +
  'written before the metric existed, or a tier that does not produce it). Not a zero.';

/** One run's side of a metric row. */
export type SideStat =
  | { kind: 'value'; stat: AggregateStat; mean: number }
  | { kind: 'no_observations'; stat: AggregateStat }
  | { kind: 'not_recorded' };

/** What one run's aggregate says about `key`. A stat with a non-finite mean
 * that is not the API's no-observations state has nothing to compare and
 * is treated as not recorded. */
export function sideStat(aggregate: Record<string, AggregateStat> | undefined, key: string): SideStat {
  const stat = aggregate?.[key];
  if (!stat) return { kind: 'not_recorded' };
  if (hasNoObservations(stat)) return { kind: 'no_observations', stat };
  if (typeof stat.mean !== 'number' || !Number.isFinite(stat.mean)) return { kind: 'not_recorded' };
  return { kind: 'value', stat, mean: stat.mean };
}

export type MetricDifference =
  | {
      kind: 'difference';
      /** mean(B) − mean(A), in the metric's unit. */
      diff: number;
      /** `diff / |mean(A)|`; null when A's mean is 0 (no ratio exists). */
      rel: number | null;
    }
  | { kind: 'none'; reason: string };

/** B − A of two runs' means, and its ratio to |A|, when both sides have a
 * mean; otherwise why there is no difference to print. */
export function metricDifference(a: SideStat, b: SideStat): MetricDifference {
  if (a.kind === 'value' && b.kind === 'value') {
    const diff = b.mean - a.mean;
    return { kind: 'difference', diff, rel: a.mean === 0 ? null : diff / Math.abs(a.mean) };
  }
  const why = (s: SideStat, side: 'A' | 'B'): string | null =>
    s.kind === 'not_recorded'
      ? `${side} did not record it`
      : s.kind === 'no_observations'
        ? `${side} has no observations`
        : null;
  const reasons = [why(a, 'A'), why(b, 'B')].filter((r): r is string => r !== null);
  return { kind: 'none', reason: `No difference: ${reasons.join(' and ')}.` };
}

/** A signed value at `digits`: `+85`, `-2.90`, and `0.00` without a sign
 * when it rounds to zero. */
export function formatSigned(v: number, digits: number): string {
  const s = formatNumber(v, digits);
  return v > 0 && /[1-9]/.test(s) ? `+${s}` : s;
}

/** `B vs A` as printed: the sweep matrix's signed percentage. */
export function formatRelative(rel: number): string {
  return formatDeltaPct(rel);
}

/** The table's sections: the headline keys plus every key either run has,
 * in Run detail's groups and order. */
export function compareSections(
  a: Record<string, AggregateStat> | undefined,
  b: Record<string, AggregateStat> | undefined,
): { group: MetricGroup; title: string; keys: string[] }[] {
  const keys = new Set<string>(HEADLINE_METRIC_KEYS);
  for (const k of Object.keys(a ?? {})) keys.add(k);
  for (const k of Object.keys(b ?? {})) keys.add(k);
  return groupedMetricKeys([...keys]);
}

/** The metrics whose B − A is a number other than zero, in the table's
 * order. A metric either run lacks a mean for has no difference and is not
 * listed. */
export function nonzeroDifferences(
  a: Record<string, AggregateStat>,
  b: Record<string, AggregateStat>,
): string[] {
  return compareSections(a, b)
    .flatMap((s) => s.keys)
    .filter((k) => {
      const d = metricDifference(sideStat(a, k), sideStat(b, k));
      return d.kind === 'difference' && d.diff !== 0;
    });
}

/* ------------------------- what each run ran ------------------------- */

/** The controlled-vehicle setup a run ran with. */
export interface RunSetup {
  controller: string | null;
  penetration: number;
  compliance: number;
  /** The sweep cell's infrastructure strategy; absent outside a sweep. */
  strategy?: SweepStrategy;
}

/** Where a run's setup was read from, or why it could not be. */
export type SetupResolution =
  | { known: true; setup: RunSetup; source: string }
  | { known: false; reason: string; scenarioDefault: RunSetup | null };

const scenarioSetup = (s: ScenarioSummary): RunSetup | null =>
  s.config
    ? {
        controller: s.config.av.controller,
        penetration: s.config.av.penetration,
        compliance: s.config.av.compliance,
      }
    : null;

/** A run's controller, penetration and compliance, from what the API says.
 *
 * `RunOut` carries none of the three. A sweep cell's are its cell's (`GET
 * /sweeps/{id}` lists them by run id). Any other run ran its stored
 * scenario's config deep-merged with the request's overrides, which the API
 * does not report: when the run's config hash equals the scenario's there
 * were none and the scenario's values are the run's; otherwise they are only
 * the scenario's default, and the run's own are not known.
 *
 * `sweep` is undefined while it is being read and null when the read failed. */
export function resolveRunSetup(
  run: Pick<RunSummary, 'run_id' | 'scenario_id' | 'config_hash' | 'sweep_id'>,
  scenario: ScenarioSummary | undefined,
  sweep: SweepDetail | null | undefined,
): SetupResolution {
  const fallback = scenario ? scenarioSetup(scenario) : null;
  if (run.sweep_id) {
    if (sweep === undefined) {
      return { known: false, reason: `Reading sweep ${run.sweep_id}…`, scenarioDefault: null };
    }
    const cell = sweep?.cells.find((c) => c.run_id === run.run_id);
    if (cell) {
      return {
        known: true,
        setup: {
          controller: cell.controller ?? null,
          penetration: cell.penetration,
          compliance: cell.compliance,
          strategy: cell.strategy ?? 'none',
        },
        source: `from sweep ${run.sweep_id}, this run’s cell`,
      };
    }
    return {
      known: false,
      reason:
        sweep === null
          ? `This run is a cell of sweep ${run.sweep_id}, which could not be read.`
          : `This run is not listed among the cells of sweep ${run.sweep_id}.`,
      scenarioDefault: fallback,
    };
  }
  if (!scenario || !fallback) {
    return {
      known: false,
      reason: `Scenario ${run.scenario_id ?? '(none)'} is not in the scenario list, so its config is unknown.`,
      scenarioDefault: null,
    };
  }
  if (scenario.config_hash === run.config_hash) {
    return {
      known: true,
      setup: fallback,
      source: `from scenario ${scenario.name}: same config hash, so no overrides`,
    };
  }
  return {
    known: false,
    reason:
      `This run’s config hash differs from scenario ${scenario.name}’s, and the API does not ` +
      'report a run’s overrides.',
    scenarioDefault: fallback,
  };
}

/** A share as a percentage: 0.05 -> "5%", 0.025 -> "2.5%". */
export function formatShare(v: number): string {
  return `${Number((v * 100).toFixed(1))}%`;
}

/** What drove the controlled vehicles. At penetration 0 nothing did, even
 * when the API stamps a sweep's controller on its baseline cell. */
export function describeController(s: RunSetup): string {
  return s.controller === null || s.penetration === 0 ? 'none (no controlled vehicles)' : s.controller;
}

/** `p 5% · c 80%`. */
export function describePenComp(s: RunSetup): string {
  return `p ${formatShare(s.penetration)} · c ${formatShare(s.compliance)}`;
}

/* --------------------------- picking runs ---------------------------- */

/** The scenario a run is shown under: the row's own name when the service
 * sent one (the demo backend names variants after a `·`), else the stored
 * scenario's name, else its id. */
export function runScenarioName(run: RunSummary, names: Map<string, string>): string {
  return run.scenario_name ?? names.get(run.scenario_id) ?? run.scenario_id ?? 'unknown scenario';
}

const TIER_WORD: Record<Tier, string> = { micro: 'micro', macro: 'macro screening' };

/** One picker option: `run-8f2c11 · corridor_10km · micro · 20 reps ·
 * 3f9a…c21e`. A demo row carries no hash (no server holds it). */
export function runOptionLabel(run: RunSummary, scenarioName: string, demo: boolean): string {
  const parts = [
    run.run_id,
    scenarioName,
    TIER_WORD[run.tier] ?? run.tier,
    `${run.progress.total_replicates} reps`,
  ];
  if (run.seeded) parts.push('seeded');
  if (!demo) parts.push(truncateMiddle(run.config_hash));
  return parts.join(' · ');
}

/** A seed's value, or null when the string is not a decimal integer. BigInt,
 * because a 64-bit seed is past what a JS number holds exactly. */
function seedValue(s: Seed): bigint | null {
  return /^[0-9]+$/.test(s) ? BigInt(s) : null;
}

/** `20 · 2000–2019` for a consecutive seed list, else the count alone (the
 * full list goes in a title). Seeds are the API's decimal strings (`Seed`):
 * the run is checked with BigInt and the ends are printed as sent, digit for
 * digit. */
export function describeSeeds(seeds: Seed[]): string {
  if (seeds.length === 0) return '0';
  const values = seeds.map(seedValue);
  const consecutive = values.every((v, i) => {
    if (v === null) return false;
    if (i === 0) return true;
    const prev = values[i - 1];
    return prev !== null && v === prev + 1n;
  });
  return consecutive && seeds.length > 1
    ? `${seeds.length} · ${seeds[0]}–${seeds[seeds.length - 1]}`
    : seeds.length === 1
      ? `1 · ${seeds[0]}`
      : String(seeds.length);
}

/** The replicate both space–time fields are drawn from: A's first seed that
 * B also ran; null when the runs share no seed. Compared as the strings the
 * API sent, so two 64-bit seeds that would round to one number stay apart. */
export function sharedSeed(a: Seed[], b: Seed[]): Seed | null {
  const inB = new Set(b);
  return a.find((s) => inB.has(s)) ?? null;
}

/* ------------------------- reading B − A ----------------------------- */

/** One run as the comparison sees it. */
export interface CompareSide {
  run: RunDetail;
  /** The name shown for its scenario (`runScenarioName`). */
  scenarioName: string;
  /** Served by the in-browser demo backend: its hash exists on no server. */
  demo: boolean;
}

/** Whether one seed is one demand realisation in both runs: only within one
 * scenario on the micro tier, where the seed draws the vehicles' insertions.
 * Across scenarios the same seed number drives different demand, and the
 * macro (CTM) tier draws no demand from its seed at all. */
export function sameDemandRealisation(a: CompareSide, b: CompareSide): boolean {
  return (
    a.run.scenario_id === b.run.scenario_id && a.run.tier === 'micro' && b.run.tier === 'micro'
  );
}

export interface CompareNote {
  id:
    | 'same-run'
    | 'tier'
    | 'macro'
    | 'scenario'
    | 'seeded'
    | 'both-seeded'
    | 'hash-differs'
    | 'hash-same'
    | 'underpowered-a'
    | 'underpowered-b';
  tone: 'warning' | 'info';
  text: string;
}

const tierPhrase = (t: Tier): string =>
  t === 'macro' ? 'a macro (CTM screening) run' : 'a micro (SUMO) run';

/** Each run's metric aggregates (`MetricsOut.aggregate`), once read. */
export interface CompareAggregates {
  a?: Record<string, AggregateStat>;
  b?: Record<string, AggregateStat>;
}

/** What differs between A and B that B − A would otherwise silently fold in.
 * Warnings never block the comparison; they say what it mixes.
 *
 * With both runs' aggregates, a shared config hash is also checked against
 * the table: the same hash means the same configuration on the same replicate
 * seeds, so every B − A must be exactly zero, and one that is not turns the
 * note into a warning. */
export function compareNotes(
  a: CompareSide,
  b: CompareSide,
  aggregates: CompareAggregates = {},
): CompareNote[] {
  if (a.run.run_id === b.run.run_id) {
    return [
      {
        id: 'same-run',
        tone: 'warning',
        text: `A and B are the same run (${a.run.run_id}), so every difference below is zero by construction.`,
      },
    ];
  }
  const notes: CompareNote[] = [];
  if (a.run.tier !== b.run.tier) {
    notes.push({
      id: 'tier',
      tone: 'warning',
      text:
        `Different tiers: A is ${tierPhrase(a.run.tier)} and B is ${tierPhrase(b.run.tier)}. The ` +
        'screening tier is string-stable by construction, so B − A mixes two model forms and ' +
        'says nothing about a controller’s effect on waves.',
    });
  } else if (a.run.tier === 'macro') {
    notes.push({
      id: 'macro',
      tone: 'warning',
      text:
        'Screening tier (CTM) — not a validation result. Both runs are macro: the model is ' +
        'string-stable by construction, so B − A cannot support a claim about phantom-jam ' +
        'formation or dampening.',
    });
  }
  if (a.run.scenario_id !== b.run.scenario_id) {
    notes.push({
      id: 'scenario',
      tone: 'warning',
      text:
        `Different scenarios: A runs ${a.scenarioName} and B runs ${b.scenarioName}, so B − A ` +
        'mixes the corridor and its demand with whatever else changed.',
    });
  }
  if (a.run.seeded !== b.run.seeded) {
    const [seededSide, other] = a.run.seeded ? ['A', 'B'] : ['B', 'A'];
    notes.push({
      id: 'seeded',
      tone: 'warning',
      text:
        `${seededSide} is seeded (a hand-placed perturbation, not emergent instability) and ` +
        `${other} is not, so B − A includes the perturbation.`,
    });
  } else if (a.run.seeded) {
    notes.push({
      id: 'both-seeded',
      tone: 'info',
      text:
        'Both runs are seeded: their waves start from a hand-placed perturbation, not from ' +
        'emergent instability, and are labelled SEEDED wherever they are quoted.',
    });
  }
  // a demo row's hash exists on no server: comparing two of them says nothing
  if (!a.demo && !b.demo) {
    if (a.run.config_hash === b.run.config_hash) {
      const hash = truncateMiddle(a.run.config_hash);
      const why =
        'The same configuration ran on the same replicate seeds, so B − A should be exactly ' +
        'zero; a nonzero difference is not sampling noise — the code or SUMO version, or a ' +
        'file the config references, changed between the runs, or the runs are not ' +
        'reproducible.';
      const nonzero =
        aggregates.a && aggregates.b ? nonzeroDifferences(aggregates.a, aggregates.b) : [];
      if (nonzero.length > 0) {
        const named = nonzero.slice(0, 3).map((k) => metricDef(k).label.toLowerCase());
        const more = nonzero.length > 3 ? ` and ${nonzero.length - 3} more` : '';
        notes.push({
          id: 'hash-same',
          tone: 'warning',
          text: `Same config hash (${hash}), yet B − A is not zero (${named.join(', ')}${more}). ${why}`,
        });
      } else {
        notes.push({ id: 'hash-same', tone: 'info', text: `Same config hash (${hash}). ${why}` });
      }
    } else {
      notes.push({
        id: 'hash-differs',
        tone: 'warning',
        text:
          `Config hashes differ (A ${truncateMiddle(a.run.config_hash)}, B ` +
          `${truncateMiddle(b.run.config_hash)}): the runs did not use the same configuration. ` +
          'That is expected when B adds a controller to A’s scenario; check that nothing else ' +
          'changed (duration, seed, demand) before reading B − A as the controller’s effect.',
      });
    }
  }
  for (const [side, s] of [
    ['A', a],
    ['B', b],
  ] as const) {
    const n = s.run.seeds.length;
    if (n > 0 && n < MIN_REPLICATES) {
      notes.push({
        id: side === 'A' ? 'underpowered-a' : 'underpowered-b',
        tone: 'warning',
        text:
          `${side} has ${n} replicate${n === 1 ? '' : 's'}, below the reporting standard ` +
          `n ≥ ${MIN_REPLICATES}: its intervals are wide and its numbers exploratory.`,
      });
    }
  }
  return notes;
}
