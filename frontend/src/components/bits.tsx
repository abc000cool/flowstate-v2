/** Small presentational atoms: status pills, tier/seeded badges, progress bars
 * (docs/design/DASHBOARD_DESIGN.md §9.6–9.7). Metric cards live in
 * metrics.tsx, scenario schematics in schematics.tsx. */

import type { RunStatus, Tier } from '../api/types';
import { Icon } from './icons';

// Kept so existing imports of MetricCard from bits keep working (the
// no-observations test imports it from here).
export { MetricCard } from './metrics';

/** States a status pill can show: the API's run states plus the guided
 * checklist's `current` / `blocked`. */
export type PillState = RunStatus | 'blocked' | 'current';

/** The 12 px leading glyph of a status pill (§9.7). Decorative: the pill's
 * text is the verbatim state word. */
export function StatusGlyph({ state }: { state: PillState }): JSX.Element {
  switch (state) {
    case 'queued':
      return <Icon name="circle-dashed" size={12} />;
    case 'done':
      return <Icon name="check" size={12} strokeWidth={2.25} />;
    case 'failed':
      return <Icon name="x" size={12} strokeWidth={2.25} />;
    case 'blocked':
      return <Icon name="circle-minus" size={12} />;
    default:
      // running / current: a 6 px pulsing dot (static with reduced motion)
      return <span className="chip-dot" aria-hidden="true" />;
  }
}

/** A run's status, as the API words it (`done`, not "Succeeded"). */
export function StatusChip({ status }: { status: RunStatus }): JSX.Element {
  return (
    <span className={`chip ${status}`}>
      <StatusGlyph state={status} />
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
    <div className="progress-row">
      <div
        className={`progress ${cls}`}
        role="progressbar"
        aria-label="Replicates complete"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
      >
        {/* computed geometry: the fill width */}
        <i style={{ width: `${pct}%` }} />
      </div>
      <span className="progress-count">
        {done}/{total}
      </span>
    </div>
  );
}
