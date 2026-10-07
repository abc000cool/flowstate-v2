/** Small presentational atoms: status chips, tier/seeded tags, progress bars.
 * Metric cards live in metrics.tsx, scenario schematics in schematics.tsx. */

import type { RunStatus, Tier } from '../api/types';

// Kept so existing imports of MetricCard from bits keep working (the
// no-observations test imports it from here).
export { MetricCard } from './metrics';

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
