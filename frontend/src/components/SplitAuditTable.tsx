/** Split audit of an onboarded corridor (`CorridorSummaryOut.split_audit`):
 * every exit leaving the chain, the side OSM draws it on against the lanes
 * the compiled network feeds it from. A defect row (`wrong_side`,
 * `added_lane_wrong_side`) is the map fault behind the I-94 WB lock — through
 * traffic trapped in a lane that leads only to the exit — and carries the
 * remedy in the engine's terms under it. Nothing here is enforced by the job;
 * the table exists so a tester sees the verdicts without reading `meta.json`
 * or `summary.txt`. */

import type { CorridorSplitFinding, SplitVerdict } from '../api/types';
import { formatDistKm } from '../lib/format';

/** Human wording of a verdict; the value itself stays in the badge's title. */
const VERDICT_LABEL: Record<SplitVerdict, string> = {
  ok: 'OK',
  wrong_side: 'WRONG SIDE',
  added_lane_wrong_side: 'ADDED LANE, WRONG SIDE',
  unknown: 'UNKNOWN',
};

const VERDICT_TITLE: Record<SplitVerdict, string> = {
  ok: 'The compiled exit leaves on the side the map draws it.',
  wrong_side:
    'The map draws the exit on one side and the compiled network feeds it from the other: ' +
    'through traffic is trapped in a lane that leads only to the exit.',
  added_lane_wrong_side:
    'A lane added by ramp guessing (--ramps.guess) feeds the exit from the wrong side.',
  unknown: 'No continuing mainline way or no usable geometry was found, so the side is undecided.',
};

/** Badge class per verdict: ok green, a defect red, unknown grey. */
export function verdictClass(verdict: SplitVerdict): string {
  if (verdict === 'ok') return 'verdict-ok';
  if (verdict === 'unknown') return 'verdict-unknown';
  return 'verdict-defect';
}

export function isSplitDefect(verdict: SplitVerdict): boolean {
  return verdict === 'wrong_side' || verdict === 'added_lane_wrong_side';
}

function lanesText(f: CorridorSplitFinding): string {
  const lanes = f.exit_from_lanes.length ? f.exit_from_lanes.join(',') : '—';
  const added = f.added_lane ? ' +1 added' : '';
  return `${f.compiled_side} · lane ${lanes} of ${f.compiled_lanes}${added}`;
}

export function SplitAuditTable({
  findings,
}: {
  findings: CorridorSplitFinding[] | undefined;
}): JSX.Element | null {
  if (!findings || findings.length === 0) return null;
  const defects = findings.filter((f) => isSplitDefect(f.verdict)).length;
  return (
    <div className="split-audit">
      <p className="small">
        split audit: {findings.length} exit{findings.length === 1 ? '' : 's'} checked,{' '}
        {defects === 0 ? (
          'none compiled on the wrong side'
        ) : (
          <span className="hint-amber">
            {defects} compiled on the wrong side — the remedy is listed under each
          </span>
        )}
      </p>
      <div className="table-wrap">
        <table className="data" aria-label="split audit">
          <thead>
            <tr>
              <th>x</th>
              <th>split → exit</th>
              <th>OSM side</th>
              <th>compiled lanes</th>
              <th>verdict</th>
            </tr>
          </thead>
          <tbody>
            {findings.map((f) => {
              const key = `${f.from_edge}-${f.exit_edge}`;
              const defect = isSplitDefect(f.verdict);
              return [
                <tr key={key} data-verdict={f.verdict}>
                  <td className="mono">{formatDistKm(f.x_m)}</td>
                  <td className="mono">
                    {f.from_edge} → {f.exit_edge}
                  </td>
                  <td className="mono" title={f.turn_lanes ? `turn:lanes ${f.turn_lanes}` : undefined}>
                    {f.osm_side}
                  </td>
                  <td className="mono">{lanesText(f)}</td>
                  <td>
                    <span
                      className={`tag ${verdictClass(f.verdict)}`}
                      title={`${f.verdict}: ${VERDICT_TITLE[f.verdict]}`}
                    >
                      {VERDICT_LABEL[f.verdict]}
                    </span>
                  </td>
                </tr>,
                defect && f.remedy ? (
                  <tr key={`${key}-remedy`} className="remedy-row">
                    <td colSpan={5} className="remedy small hint-amber">
                      remedy: {f.remedy}
                    </td>
                  </tr>
                ) : null,
              ];
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
