/** Metric display (docs/design/DASHBOARD_DESIGN.md §9.12, §7.6): the metric
 * tile — eyebrow label, `mean ± half-width` with its unit, the exact 95% CI
 * line, and a dot strip of the replicates with the CI band and a mean tick.
 * It replaces the old MetricCard + CIBar + StripChart (the strip plotted
 * replicate *index* on x, an axis with no meaning).
 *
 * Display only: every number here is one the API returned (`CIOut`,
 * `MetricsOut.replicates`); the half-width is the printed interval halved,
 * shown only when the interval is symmetric at the printed precision. The
 * dashboard computes no statistic of its own (CLAUDE.md §7.4). */

import { useMemo, useState, type KeyboardEvent } from 'react';
import type { AggregateStat, ReplicateMetrics, Seed } from '../api/types';
import { formatNumber } from '../lib/format';
import {
  hasNoObservations,
  metricDef,
  NO_OBSERVATIONS_LABEL,
  NO_OBSERVATIONS_TITLE,
} from '../lib/metrics';
import { Skeleton } from './ui/Skeleton';

/** One replicate's value of a metric, with the seed that produced it. */
export interface ReplicatePoint {
  /** The decimal string the API sent (`Seed`). */
  seed: Seed;
  value: number;
}

/** The replicates of `metricKey` that produced a value, from
 * `RunMetrics.replicates` (a null or missing value is no observation). */
export function replicatePoints(replicates: ReplicateMetrics[], metricKey: string): ReplicatePoint[] {
  const out: ReplicatePoint[] = [];
  for (const r of replicates) {
    const v = r.metrics[metricKey];
    if (typeof v === 'number' && Number.isFinite(v)) out.push({ seed: r.seed, value: v });
  }
  return out;
}

/** Float noise allowance on top of the half-unit rule. */
const SYMMETRY_EPS = 1e-9;

/** Half the interval's width, when the interval is symmetric about the mean
 * at the printed precision: `|(hi − mean) − (mean − lo)| ≤ 0.5 × 10^−digits`.
 * Otherwise null, and the tile prints no "±" (never a symmetric "±" for an
 * asymmetric interval; the CI line carries it). */
export function symmetricHalfWidth(stat: AggregateStat, digits: number): number | null {
  const { mean, lo95, hi95 } = stat;
  if (mean === null || lo95 === null || hi95 === null) return null;
  const skew = Math.abs(hi95 - mean - (mean - lo95));
  if (skew > 0.5 * 10 ** -digits + SYMMETRY_EPS) return null;
  return (hi95 - lo95) / 2;
}

/** Vertical jitter of the i-th dot, deterministic: −5, 0, +5 px. */
const jitter = (i: number): number => ((i % 3) - 1) * 5;

const STRIP_H = 28;
const DOT_R = 4; // 8 px dot
const HIT_R = 12; // 24 px hit target, ring included
const FOCUS_R = 7;

/** Replicate dot strip (§7.6): the CI band (series 1 at --viz-ci-opacity, full
 * height), a 2 × 16 px mean tick, and one 8 px dot per replicate. x spans
 * [min(values, lo95), max(values, hi95)] padded 10 %. Hover or keyboard focus
 * on a dot shows `seed {s} · {value} {unit}`. The strip is one tab stop; the
 * arrow keys move between dots in value order (Home/End jump to the ends). */
export function DotStrip({
  stat,
  points,
  digits,
  unit,
  label,
}: {
  stat: AggregateStat;
  points: ReplicatePoint[];
  digits: number;
  unit: string;
  /** The metric's label, for the strip's accessible name. */
  label: string;
}): JSX.Element | null {
  const [active, setActive] = useState(0);
  const [tip, setTip] = useState<number | null>(null);
  const [focused, setFocused] = useState(false);

  const geom = useMemo(() => {
    const vals = points.map((p) => p.value);
    const lo = Math.min(...vals, ...(stat.lo95 !== null ? [stat.lo95] : []), ...(stat.mean !== null ? [stat.mean] : []));
    const hi = Math.max(...vals, ...(stat.hi95 !== null ? [stat.hi95] : []), ...(stat.mean !== null ? [stat.mean] : []));
    const span = hi - lo;
    const pad = span > 0 ? span * 0.1 : Math.max(Math.abs(hi) * 0.05, 1e-6);
    const d0 = lo - pad;
    const d1 = hi + pad;
    const x = (v: number): number => (100 * (v - d0)) / (d1 - d0);
    // dot indices in value order, for the arrow keys
    const order = points.map((_, i) => i).sort((a, b) => points[a].value - points[b].value);
    return { x, order };
  }, [points, stat.lo95, stat.hi95, stat.mean]);

  if (points.length === 0) return null;
  const { x, order } = geom;
  const text = (i: number): string =>
    `seed ${points[i].seed} · ${formatNumber(points[i].value, digits)}${unit ? ` ${unit}` : ''}`;

  const onKeyDown = (e: KeyboardEvent<SVGSVGElement>): void => {
    const pos = order.indexOf(active);
    let next: number | null = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = order[Math.min(order.length - 1, pos + 1)];
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = order[Math.max(0, pos - 1)];
    else if (e.key === 'Home') next = order[0];
    else if (e.key === 'End') next = order[order.length - 1];
    if (next === null) return;
    e.preventDefault();
    setActive(next);
    setTip(next);
    // move DOM focus to the newly active dot (roving tabindex)
    const g = e.currentTarget.querySelector<SVGGElement>(`[data-dot="${next}"]`);
    g?.focus();
  };

  const shown = tip ?? (focused ? active : null);
  const { lo95, hi95, mean } = stat;
  return (
    <div className="dot-strip">
      <svg
        height={STRIP_H}
        role="group"
        aria-label={`${label}: ${points.length} replicates`}
        onKeyDown={onKeyDown}
        onFocus={() => setFocused(true)}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
            setFocused(false);
            setTip(null);
          }
        }}
      >
        {lo95 !== null && hi95 !== null && (
          <rect
            className="ci-band"
            x={`${x(lo95)}%`}
            width={`${Math.max(0, x(hi95) - x(lo95))}%`}
            y={0}
            height={STRIP_H}
          />
        )}
        {mean !== null && (
          <rect
            className="mean-tick"
            x={`${x(mean)}%`}
            y={(STRIP_H - 16) / 2}
            width={2}
            height={16}
            transform="translate(-1 0)"
          />
        )}
        {points.map((p, i) => {
          const cx = `${x(p.value)}%`;
          const cy = STRIP_H / 2 + jitter(i);
          return (
            <g
              key={i}
              className={`dot${shown === i ? ' active' : ''}`}
              data-dot={i}
              tabIndex={i === active ? 0 : -1}
              // a focusable <g> has no role of its own, so whether its label
              // is read depends on the browser's SVG mapping: each dot is a
              // data-point graphic with a text alternative
              role="img"
              aria-label={text(i)}
              onPointerEnter={() => setTip(i)}
              onPointerLeave={() => setTip(null)}
              onFocus={() => {
                setActive(i);
                setTip(i);
              }}
            >
              <circle className="dot-hit" cx={cx} cy={cy} r={HIT_R} />
              <circle className="dot-focus" cx={cx} cy={cy} r={FOCUS_R} />
              <circle className="dot-mark" cx={cx} cy={cy} r={DOT_R} />
            </g>
          );
        })}
      </svg>
      {shown !== null && points[shown] && (
        <div
          className="viz-tip dot-tip"
          role="tooltip"
          // computed geometry: anchored above the dot
          style={{ left: `${x(points[shown].value)}%` }}
        >
          {text(shown)}
        </div>
      )}
    </div>
  );
}

/** The metric tile (§9.12). Exact text contract (test anchors): the mean is
 * alone in its element (`17.6`); `± {hw}` and the unit are siblings; the CI
 * line is one element `95% CI {lo} – {hi} · n={n}`; a metric no replicate
 * produced reads `NO DATA` / `no observations` /
 * `no replicate produced a value · n=0` and has no strip.
 *
 * `CIOut` has three states and the tile renders three (see `AggregateStat`):
 * no observations at all (n=0) is said in words, because a dash next to a
 * "95% CI — – —" line reads like a rendering failure rather than the answer
 * "no replicate produced this metric". */
export function MetricTile({
  metricKey,
  stat,
  replicates,
}: {
  metricKey: string;
  stat: AggregateStat;
  /** The per-replicate values with their seeds (`replicatePoints`). */
  replicates?: ReplicatePoint[];
}): JSX.Element {
  const def = metricDef(metricKey);
  const noObs = hasNoObservations(stat);
  const hw = noObs ? null : symmetricHalfWidth(stat, def.digits);
  return (
    <div className="metric-tile">
      <div className="mt-head">
        <span className="mt-label">{def.label}</span>
        {noObs ? (
          <span className="tag noobs" title={NO_OBSERVATIONS_TITLE}>
            NO DATA
          </span>
        ) : (
          stat.underpowered && (
            <span
              className="tag underpowered"
              title={`n=${stat.n} < 20 replicates — below reporting standard`}
            >
              UNDERPOWERED
            </span>
          )
        )}
      </div>
      {noObs ? (
        <>
          <div className="mt-value mt-noobs" title={NO_OBSERVATIONS_TITLE}>
            {NO_OBSERVATIONS_LABEL}
          </div>
          <div className="mt-ci">no replicate produced a value · n=0</div>
        </>
      ) : (
        <>
          <div className="mt-value">
            <span className="mt-mean">
              {stat.mean === null ? '—' : formatNumber(stat.mean, def.digits)}
            </span>
            {hw !== null && <span className="mt-hw">± {formatNumber(hw, def.digits)}</span>}
            {def.unit && <span className="mt-unit">{def.unit}</span>}
          </div>
          <div className="mt-ci">
            95% CI {stat.lo95 === null ? '—' : formatNumber(stat.lo95, def.digits)} –{' '}
            {stat.hi95 === null ? '—' : formatNumber(stat.hi95, def.digits)} · n={stat.n}
          </div>
          {replicates && replicates.length > 0 && (
            <DotStrip
              stat={stat}
              points={replicates}
              digits={def.digits}
              unit={def.unit}
              label={def.label}
            />
          )}
        </>
      )}
    </div>
  );
}

/** The old name, kept reachable: `bits.tsx` re-exports it and
 * `noobservations.test.tsx` imports it from there. */
export const MetricCard = MetricTile;

/** First-load placeholder of a tile, at the tile's own size. */
export function MetricTileSkeleton(): JSX.Element {
  return (
    <div className="metric-tile metric-tile-skeleton" aria-hidden="true">
      <Skeleton width="45%" height={12} />
      <Skeleton width="60%" height={28} radius="sm" />
      <Skeleton width="80%" height={12} />
      <Skeleton width="100%" height={28} radius="sm" />
    </div>
  );
}
