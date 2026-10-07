/** Metric display: aggregate metric cards with CI range bars, and
 * per-replicate strip charts. Moved verbatim from bits.tsx
 * (docs/design/DASHBOARD_DESIGN.md §12.2, step 0); step 3 replaces these with
 * `MetricTile` + `DotStrip` (§9.12). */

import type { AggregateStat } from '../api/types';
import { formatNumber } from '../lib/format';
import {
  hasNoObservations,
  metricDef,
  NO_OBSERVATIONS_LABEL,
  NO_OBSERVATIONS_TITLE,
} from '../lib/metrics';

/** Confidence-interval range bar: track spans a padded [lo, hi] domain with
 * the CI filled and a tick at the mean. */
export function CIBar({ stat }: { stat: AggregateStat }): JSX.Element {
  if (stat.mean === null || stat.lo95 === null || stat.hi95 === null) {
    // The API reports null when the metric is undefined for every replicate.
    return <div className="ci-track" aria-label="no value" />;
  }
  const { mean, lo95, hi95 } = stat;
  const span = Math.max(hi95 - lo95, Math.abs(mean) * 0.02, 1e-9);
  const d0 = lo95 - span * 0.6;
  const d1 = hi95 + span * 0.6;
  const pos = (v: number): number => (100 * (v - d0)) / (d1 - d0);
  return (
    <div className="ci-track">
      <div
        className="ci-fill"
        style={{ left: `${pos(lo95)}%`, width: `${pos(hi95) - pos(lo95)}%` }}
      />
      <div className="ci-mean" style={{ left: `calc(${pos(mean)}% - 1px)` }} />
    </div>
  );
}

/** Tiny-multiple strip chart of per-replicate values for one metric. */
export function StripChart({
  metricKey,
  values,
  mean,
}: {
  metricKey: string;
  values: number[];
  mean: number;
}): JSX.Element {
  const def = metricDef(metricKey);
  const w = 168;
  const h = 34;
  const pad = 3;
  const lo = Math.min(...values, mean);
  const hi = Math.max(...values, mean);
  const span = hi - lo || 1;
  const y = (v: number): number => h - pad - ((h - 2 * pad) * (v - lo)) / span;
  const x = (i: number): number =>
    values.length > 1 ? pad + ((w - 2 * pad) * i) / (values.length - 1) : w / 2;
  return (
    <div className="strip">
      <div className="s-label">
        {def.label} <span style={{ opacity: 0.6 }}>· {values.length} reps</span>
      </div>
      <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden>
        <line
          x1={pad}
          x2={w - pad}
          y1={y(mean)}
          y2={y(mean)}
          stroke="var(--accent)"
          strokeWidth={1}
          strokeDasharray="3 3"
          opacity={0.7}
        />
        {values.map((v, i) => (
          <line
            key={i}
            x1={x(i)}
            x2={x(i)}
            y1={h - pad}
            y2={y(v)}
            stroke="var(--muted)"
            strokeWidth={1.5}
            opacity={0.55}
          />
        ))}
      </svg>
    </div>
  );
}

/** Aggregate metric card: mono value, unit, CI range bar, honesty tags.
 *
 * `CIOut` has three states and the card renders three (see `AggregateStat`):
 * no observations at all (n=0) is said in words, because a dash next to a
 * "95% CI — – —" line reads like a rendering failure rather than the answer
 * "no replicate produced this metric". */
export function MetricCard({
  metricKey,
  stat,
}: {
  metricKey: string;
  stat: AggregateStat;
}): JSX.Element {
  const def = metricDef(metricKey);
  const noObs = hasNoObservations(stat);
  return (
    <div className="metric-card">
      <div className="m-label">
        <span>{def.label}</span>
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
          <div className="m-value m-noobs" title={NO_OBSERVATIONS_TITLE}>
            {NO_OBSERVATIONS_LABEL}
          </div>
          <div className="ci-text">no replicate produced a value · n=0</div>
        </>
      ) : (
        <>
          <div className="m-value mono">
            {stat.mean === null ? '—' : formatNumber(stat.mean, def.digits)}
            <span className="m-unit">{def.unit}</span>
          </div>
          <CIBar stat={stat} />
          <div className="ci-text">
            95% CI {stat.lo95 === null ? '—' : formatNumber(stat.lo95, def.digits)} –{' '}
            {stat.hi95 === null ? '—' : formatNumber(stat.hi95, def.digits)} · n=
            {stat.n}
          </div>
        </>
      )}
    </div>
  );
}
