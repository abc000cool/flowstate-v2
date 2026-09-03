/** Small presentational atoms: status chips, tier/seeded tags, progress bars,
 * CI range bars, per-replicate strip charts, scenario schematic thumbnails. */

import type { AggregateStat, Network, RunStatus, Tier } from '../api/types';
import { formatNumber } from '../lib/format';
import { metricDef } from '../lib/metrics';

export function StatusChip({ status }: { status: RunStatus }): JSX.Element {
  return (
    <span className={`chip ${status}`}>
      <span className="dot" />
      {status}
    </span>
  );
}

export function TierBadge({ tier }: { tier: Tier }): JSX.Element {
  return tier === 'micro' ? (
    <span className="tag micro">MICRO</span>
  ) : (
    <span className="tag macro" title="Fast CTM screening tier — cannot support validation claims">
      MACRO SCREENING
    </span>
  );
}

export function SeededBadge({ seeded }: { seeded: boolean }): JSX.Element | null {
  if (!seeded) return null;
  return (
    <span
      className="tag seeded"
      title="Results come from a seeded perturbation, not emergent instability — labeled per policy"
    >
      SEEDED
    </span>
  );
}

export function ProgressBar({
  done,
  total,
  status,
}: {
  done: number;
  total: number;
  status: RunStatus;
}): JSX.Element {
  const pct = total > 0 ? Math.min(100, (100 * done) / total) : 0;
  const cls = status === 'failed' ? 'failed' : status === 'done' ? 'done' : '';
  return (
    <div className="row" style={{ gap: 10, minWidth: 140 }}>
      <div className={`progress ${cls}`} style={{ flex: 1 }}>
        <i style={{ width: `${pct}%` }} />
      </div>
      <span className="mono small muted" style={{ minWidth: 44, textAlign: 'right' }}>
        {done}/{total}
      </span>
    </div>
  );
}

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

/** Aggregate metric card: mono value, unit, CI range bar, honesty tags. */
export function MetricCard({
  metricKey,
  stat,
}: {
  metricKey: string;
  stat: AggregateStat;
}): JSX.Element {
  const def = metricDef(metricKey);
  return (
    <div className="metric-card">
      <div className="m-label">
        <span>{def.label}</span>
        {stat.underpowered && (
          <span
            className="tag underpowered"
            title={`n=${stat.n} < 20 replicates — below reporting standard`}
          >
            UNDERPOWERED
          </span>
        )}
      </div>
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
    </div>
  );
}

/* ----------------------- scenario schematic SVGs ---------------------- */

/** Ring: circle of vehicle dots (one accent = controlled vehicle). */
function RingThumb({ n }: { n: number }): JSX.Element {
  const cx = 60;
  const cy = 48;
  const r = 34;
  const count = Math.min(Math.max(n, 8), 30);
  const dots = Array.from({ length: count }, (_, i) => {
    const a = (2 * Math.PI * i) / count - Math.PI / 2;
    return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a), av: i === 0 };
  });
  return (
    <svg width="120" height="96" viewBox="0 0 120 96" aria-hidden>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--panel-edge)" strokeWidth={6} />
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="#232b3d" strokeWidth={1} strokeDasharray="2 4" />
      {dots.map((d, i) => (
        <circle
          key={i}
          cx={d.x}
          cy={d.y}
          r={d.av ? 3.4 : 2.4}
          fill={d.av ? 'var(--accent)' : 'var(--muted)'}
        />
      ))}
    </svg>
  );
}

/** Corridor: lane lines with dashes and a few vehicle ticks. */
function CorridorThumb({ lanes }: { lanes: number }): JSX.Element {
  const laneN = Math.min(Math.max(lanes, 1), 4);
  const laneH = 14;
  const top = 48 - (laneN * laneH) / 2;
  const cars = [
    { x: 18, lane: 0, av: false },
    { x: 44, lane: laneN > 1 ? 1 : 0, av: true },
    { x: 66, lane: 0, av: false },
    { x: 92, lane: laneN > 1 ? 1 : 0, av: false },
    { x: 130, lane: 0, av: false },
  ];
  return (
    <svg width="170" height="96" viewBox="0 0 170 96" aria-hidden>
      <rect x={6} y={top - 3} width={158} height={laneN * laneH + 6} rx={3} fill="#0d1119" stroke="var(--panel-edge)" />
      {Array.from({ length: laneN + 1 }, (_, i) => (
        <line
          key={i}
          x1={10}
          x2={160}
          y1={top + i * laneH}
          y2={top + i * laneH}
          stroke={i === 0 || i === laneN ? '#2a3348' : '#232b3d'}
          strokeWidth={i === 0 || i === laneN ? 1.5 : 1}
          strokeDasharray={i === 0 || i === laneN ? undefined : '5 5'}
        />
      ))}
      {cars.map((c, i) => (
        <rect
          key={i}
          x={c.x}
          y={top + c.lane * laneH + laneH / 2 - 2.5}
          width={9}
          height={5}
          rx={1.5}
          fill={c.av ? 'var(--accent)' : 'var(--muted)'}
        />
      ))}
      <path d="M158 42 l6 6 -6 6" fill="none" stroke="var(--faint)" strokeWidth={1.5} />
    </svg>
  );
}

function OsmThumb(): JSX.Element {
  return (
    <svg width="120" height="96" viewBox="0 0 120 96" aria-hidden>
      <path d="M10 70 C 40 60, 50 30, 110 26" fill="none" stroke="var(--panel-edge)" strokeWidth={7} strokeLinecap="round" />
      <path d="M10 70 C 40 60, 50 30, 110 26" fill="none" stroke="#232b3d" strokeWidth={1} strokeDasharray="3 5" />
      <path d="M30 90 C 45 70, 42 50, 58 40" fill="none" stroke="var(--panel-edge)" strokeWidth={4} strokeLinecap="round" />
      <circle cx={78} cy={33} r={3} fill="var(--accent)" />
    </svg>
  );
}

export function SchematicThumb({ network, name }: { network?: Network; name: string }): JSX.Element {
  if (network?.kind === 'ring') return <RingThumb n={network.n_vehicles} />;
  if (network?.kind === 'corridor') return <CorridorThumb lanes={network.lanes} />;
  if (network?.kind === 'osm') return <OsmThumb />;
  // no embedded config — guess from the name
  if (name.toLowerCase().includes('ring')) return <RingThumb n={22} />;
  return <CorridorThumb lanes={1} />;
}
