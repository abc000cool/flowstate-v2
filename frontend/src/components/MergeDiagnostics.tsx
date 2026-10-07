/** Merge diagnostics of a run (`MetricsOut.merge_diagnostics`): the ramp-meter
 * and weaving-section counters the runner writes to one replicate's
 * `meta.json` (`ramp_meters[i]`, `weave_sections[i]`). They say how the
 * merge models behaved — vehicles held and released, vehicles that could not
 * stop for the meter, changes made, forced, deferred or never made — and, since
 * 2026-09-24 (block 3), the follower cooperations, their mean commanded
 * deceleration, the changer easings, the through vehicles asked to vacate the
 * weave lane upstream (made or refused) and the stopped pairs released by the
 * weave step — and are one seed's counters, not a replicate aggregate and
 * never a corridor result.
 * The section renders only when the run has at least one of the two lists. */

import type { MergeDiagnostics, RampMeterDiagnostics, WeaveSectionDiagnostics } from '../api/types';
import { formatNumber } from '../lib/format';

const PASSED_TITLE =
  'Ramp vehicles already too close to the stop line to brake for it when first seen on the ' +
  'ramp; they pass the meter that cycle. A large share against Released means the stop line ' +
  'sits too near the ramp’s end for the entry speeds.';

const DEFERRED_TITLE =
  'Vehicle-steps on which a due forced lane change was refused by the minimum-gap guard — ' +
  'steps, not vehicles.';

const UNFINISHED_TITLE = 'Vehicles still under the section’s control when the run ended.';

const MISSED_TITLE = 'Vehicles that left the section, or the network, still owing their change.';

const COOPERATIONS_TITLE =
  'Vehicle-steps on which a target-lane follower was given a speed target to open a gap for a ' +
  'changer — steps, not vehicles. A dash is a meta written before the rule existed.';

const FOLLOWER_DECEL_TITLE =
  'Mean deceleration commanded to cooperating followers over those steps; positive is braking. ' +
  'A dash means no cooperation was commanded, or a meta written before the rule existed.';

const EASINGS_TITLE =
  'Vehicle-steps on which a changer was given a speed target towards the leader of its chosen ' +
  'gap — steps, not vehicles. A dash is a meta written before the rule existed.';

const VACATED_TITLE =
  'Through vehicles asked to leave the weave lane upstream of the section that moved over ' +
  'before reaching it — vehicles, each once. A dash is a meta written before the rule existed.';

const VACATE_REFUSED_TITLE =
  'Such requests that expired or reached the section with the vehicle still in the weave lane ' +
  '— vehicles, each once. A dash is a meta written before the rule existed.';

const PAIR_RELEASES_TITLE =
  'Stopped crossing pairs (a changer and the follower of its committed gap, both stopped ' +
  'behind each other) released so one yields and the other goes — each pair once per ' +
  'release. A dash is a meta written before the rule existed.';

const EXITED_TITLE =
  'Exit-bound vehicles that took the exit, against the exit-bound vehicles that entered the ' +
  'section during the run (a meta written before that counter existed falls back to every ' +
  'departed vehicle routed through the exit).';

function RampMeterTable({ rows }: { rows: RampMeterDiagnostics[] }): JSX.Element {
  return (
    <div className="table-wrap">
      <table className="data compact" aria-label="ramp meters">
        <thead>
          <tr>
            <th>Ramp</th>
            <th>Controller</th>
            <th className="num">Interval [s]</th>
            <th className="num">Released</th>
            <th className="num" title={PASSED_TITLE}>
              Passed unstoppable
            </th>
            <th className="num">Rate updates</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={`${m.ramp}-${m.edge}`}>
              <td>{m.ramp}</td>
              <td className="mono">{m.controller}</td>
              <td className="num">{formatNumber(m.interval_s, 0)}</td>
              <td className="num">{m.n_released}</td>
              <td className={`num${m.n_passed_unstoppable > 0 ? ' hint-amber' : ''}`}>
                {m.n_passed_unstoppable}
              </td>
              <td className="num">{m.n_rate_updates}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WeaveTable({ rows }: { rows: WeaveSectionDiagnostics[] }): JSX.Element {
  return (
    <div className="table-wrap">
      <table className="data compact zebra wide weave-table" aria-label="weaving sections">
        <thead>
          <tr>
            <th>Section</th>
            <th className="num">Entered</th>
            <th className="num">Changed in</th>
            <th className="num">Changed out</th>
            <th className="num" title={EXITED_TITLE}>
              Exited / reached
            </th>
            <th className="num">Forced</th>
            <th className="num" title={DEFERRED_TITLE}>
              Deferred (vehicle-steps)
            </th>
            <th className="num" title={MISSED_TITLE}>
              Missed
            </th>
            <th className="num" title={UNFINISHED_TITLE}>
              Unfinished
            </th>
            <th className="num">Mean wait [s]</th>
            <th className="num" title={COOPERATIONS_TITLE}>
              Follower cooperations (vehicle-steps)
            </th>
            <th className="num" title={FOLLOWER_DECEL_TITLE}>
              Mean follower decel [m/s²]
            </th>
            <th className="num" title={EASINGS_TITLE}>
              Changer easings
            </th>
            <th className="num" title={VACATED_TITLE}>
              Through vacated
            </th>
            <th className="num" title={VACATE_REFUSED_TITLE}>
              Vacate refused
            </th>
            <th className="num" title={PAIR_RELEASES_TITLE}>
              Pair releases
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((w) => (
            <tr key={`${w.ramp}-${w.exit}`}>
              <td>
                {w.ramp} → {w.exit}
              </td>
              <td className="num">{w.n_entered}</td>
              <td className="num">{w.n_changed_in}</td>
              <td className="num">{w.n_changed_out}</td>
              <td className="num">
                {w.n_exited} / {w.n_reached_section_exiting ?? w.n_departed_exiting}
              </td>
              <td className="num">{w.n_forced}</td>
              <td className="num">{w.n_forced_deferred}</td>
              <td className={`num${w.n_missed > 0 ? ' hint-amber' : ''}`}>{w.n_missed}</td>
              <td className={`num${w.n_unfinished > 0 ? ' hint-amber' : ''}`}>{w.n_unfinished}</td>
              <td className="num">{formatNumber(w.wait_s_mean ?? null, 1)}</td>
              <td className="num">{w.n_cooperations ?? '—'}</td>
              <td className="num">{formatNumber(w.mean_follower_decel_ms2 ?? null, 2)}</td>
              <td className="num">{w.n_changer_eased ?? '—'}</td>
              <td className="num">{w.n_vacated ?? '—'}</td>
              <td className="num">{w.n_vacate_refused ?? '—'}</td>
              <td className="num">{w.n_pair_releases ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MergeDiagnosticsPanel({
  diagnostics,
}: {
  diagnostics: MergeDiagnostics | null | undefined;
}): JSX.Element | null {
  if (!diagnostics) return null;
  const meters = diagnostics.ramp_meters ?? [];
  const weaves = diagnostics.weave_sections ?? [];
  if (meters.length === 0 && weaves.length === 0) return null;
  return (
    <section className="panel diag-panel" data-testid="merge-diagnostics">
      <div className="panel-head">
        <h2 className="panel-title">Merge diagnostics · seed {diagnostics.seed}</h2>
      </div>
      <div className="panel-body stack">
        <p className="diag-lead">
          Counters of one replicate&apos;s merge models, read from its meta — how the ramp
          meters and weaving sections behaved, not a corridor result and not a replicate mean.
        </p>
        {meters.length > 0 && (
          <div className="diag-block">
            <h4>Ramp meters</h4>
            <RampMeterTable rows={meters} />
          </div>
        )}
        {weaves.length > 0 && (
          <div className="diag-block">
            <h4>Weaving sections</h4>
            <WeaveTable rows={weaves} />
          </div>
        )}
      </div>
    </section>
  );
}
