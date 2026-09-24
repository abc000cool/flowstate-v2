/** Demand integrity of a run (`MetricsOut.insertion`, `MetricsOut.weave_exits`):
 * the two blocks the corridor battery artifact carries under the same names,
 * pooled over the run's replicates by the battery's own functions
 * (`validation.battery.aggregate_insertion`, `weave_exit_summary`;
 * 2026-09-24, block 3).
 *
 * A replicate whose vehicles never departed is not a slow corridor, it is a
 * different scenario: every metric above this panel then describes demand
 * that was never applied. And an exit-bound vehicle the weave step gave up on
 * is missing from the exit's link flow and present on every mainline link
 * downstream, which a GEH comparison cannot tell from a demand error. Both
 * verdicts therefore sit beside the metrics, not in the run directory. The
 * panel renders only when the service reports at least one of the blocks. */

import type { InsertionSummary, WeaveExits } from '../api/types';
import { formatNumber } from '../lib/format';

/** The battery's OK verdict (`validation.battery.OK_VERDICT`). */
const OK_VERDICT = 'ok';
/** The battery's verdict for a run whose plan held no vehicles. */
const NO_PLAN_VERDICT = 'no vehicles planned';

const REACHED_TITLE = 'Exit-bound vehicles that entered the section, summed over the replicates.';

const GIVEN_UP_TITLE =
  'Exit-bound vehicles the weave step rerouted through at the gore’s end, halted still owing ' +
  'their change — missing from the exit’s link flow and present on every mainline link ' +
  'downstream. Share is against Reached; a dash means no exiter reached the section.';

const RUNS_TITLE =
  'Replicates that recorded both counters; a meta written before the exit-side rule ' +
  'contributes nothing.';

function pct(fraction: number | null | undefined, digits = 1): string {
  return fraction == null ? '—' : `${formatNumber(100 * fraction, digits)} %`;
}

/** The verdict as a badge: OK green, a problem red, an empty plan grey. */
export function VerdictTag({ verdict, label }: { verdict: string; label: string }): JSX.Element {
  const kind =
    verdict === OK_VERDICT ? 'ok' : verdict === NO_PLAN_VERDICT ? 'unknown' : 'defect';
  return (
    <span className={`tag verdict-${kind}`} aria-label={`${label} verdict`}>
      {verdict}
    </span>
  );
}

function InsertionLine({ insertion }: { insertion: InsertionSummary }): JSX.Element {
  const worst = insertion.min_departed_fraction;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10 }}>
      <span className="panel-title">Insertion</span>
      <VerdictTag verdict={insertion.verdict} label="insertion" />
      <span className="small muted mono" data-testid="insertion-summary">
        departed {pct(insertion.mean_departed_fraction)} of {insertion.planned} planned
        {worst != null && ` (worst seed ${pct(worst)})`}
        {insertion.mean_arrived != null &&
          ` · arrived ${formatNumber(insertion.mean_arrived, 0)} per replicate` +
            (insertion.n_with_arrived < insertion.n_runs
              ? ` over ${insertion.n_with_arrived} of ${insertion.n_runs}`
              : '')}
      </span>
      {insertion.starved_ramps.length > 0 && (
        <span className="small hint-amber">starved: {insertion.starved_ramps.join(', ')}</span>
      )}
    </div>
  );
}

function WeaveExitTable({ weaveExits }: { weaveExits: WeaveExits }): JSX.Element {
  const threshold = pct(weaveExits.threshold_share, 0);
  return (
    <div className="table-wrap">
      <table className="data" aria-label="weave exits">
        <thead>
          <tr>
            <th>Section</th>
            <th title={REACHED_TITLE}>Reached</th>
            <th title={GIVEN_UP_TITLE}>Given up</th>
            <th title={GIVEN_UP_TITLE}>Share</th>
            <th title={RUNS_TITLE}>Replicates</th>
          </tr>
        </thead>
        <tbody>
          {weaveExits.sections.map((s) => (
            <tr key={`${s.ramp}-${s.exit ?? ''}`}>
              <td>{s.exit ? `${s.ramp} → ${s.exit}` : s.ramp}</td>
              <td className="mono">{s.reached}</td>
              <td className={`mono${s.missed_exit.n > 0 ? ' hint-amber' : ''}`}>
                {s.missed_exit.n}
              </td>
              <td className="mono">
                {pct(s.missed_exit.share)}
                {s.flagged && (
                  <>
                    {' '}
                    <span className="tag verdict-defect" data-testid="weave-exit-flag">
                      above {threshold}
                    </span>
                  </>
                )}
              </td>
              <td className="mono">
                {s.n_runs} / {weaveExits.n_runs}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function InsertionPanel({
  insertion,
  weaveExits,
}: {
  insertion: InsertionSummary | null | undefined;
  weaveExits: WeaveExits | null | undefined;
}): JSX.Element | null {
  if (!insertion && !weaveExits) return null;
  const nRuns = insertion?.n_runs ?? weaveExits?.n_runs ?? 0;
  return (
    <div className="panel" data-testid="insertion-panel">
      <div className="panel-head">
        <span className="panel-title">Demand integrity · {nRuns} replicates</span>
      </div>
      <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <p className="small muted" style={{ margin: 0 }}>
          Whether the run put its demand on the network, pooled over the replicates that recorded
          it — the corridor battery&apos;s own verdicts. A backlog or a given-up exit means the
          metrics above describe demand that was never applied, not a slow corridor.
        </p>
        {insertion && <InsertionLine insertion={insertion} />}
        {weaveExits && (
          <div>
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                alignItems: 'center',
                gap: 10,
                marginBottom: 8,
              }}
            >
              <span className="panel-title">Weave exits</span>
              <VerdictTag verdict={weaveExits.verdict} label="weave exits" />
            </div>
            <WeaveExitTable weaveExits={weaveExits} />
          </div>
        )}
      </div>
    </div>
  );
}
