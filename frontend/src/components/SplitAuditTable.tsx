/** Split audit of an onboarded corridor (`CorridorSummaryOut.split_audit`):
 * every exit leaving the chain, the side OSM draws it on against the lanes
 * the compiled network feeds it from. A defect row (`wrong_side`,
 * `added_lane_wrong_side`, `through_lane_exit_only`) is the map fault behind
 * the I-94 WB lock — through traffic trapped in a lane that leads only to the
 * exit — and carries the remedy in the engine's terms under it. The last
 * (2026-10-07) is an exit compiled on the right side whose lane OSM draws as
 * continuing was compiled exit-only, usually because ramp guessing added a
 * lane on the other side (remedy `--ramps.unset <edge>`). Nothing here is
 * enforced by the job; the table exists so a tester sees the verdicts without
 * reading `meta.json` or `summary.txt`. */

import type { CorridorSplitFinding, SplitVerdict } from '../api/types';
import { formatDistKm } from '../lib/format';

/** Human wording of a verdict; the value itself stays in the badge's title. */
const VERDICT_LABEL: Record<SplitVerdict, string> = {
  ok: 'OK',
  wrong_side: 'WRONG SIDE',
  added_lane_wrong_side: 'ADDED LANE, WRONG SIDE',
  through_lane_exit_only: 'THROUGH LANE EXIT-ONLY',
  unknown: 'UNKNOWN',
};

const VERDICT_TITLE: Record<SplitVerdict, string> = {
  ok: 'The compiled exit leaves on the side the map draws it.',
  wrong_side:
    'The map draws the exit on one side and the compiled network feeds it from the other: ' +
    'through traffic is trapped in a lane that leads only to the exit.',
  added_lane_wrong_side:
    'A lane added by ramp guessing (--ramps.guess) feeds the exit from the wrong side.',
  through_lane_exit_only:
    'The exit is on the drawn side, but a lane the map draws as continuing (by turn:lanes or ' +
    'the lane count) was compiled to lead only to the exit: through traffic in it is trapped.',
  unknown: 'No continuing mainline way or no usable geometry was found, so the side is undecided.',
};

/** Badge class per verdict: ok green, a defect red, unknown grey. */
export function verdictClass(verdict: SplitVerdict): string {
  if (verdict === 'ok') return 'verdict-ok';
  if (verdict === 'unknown') return 'verdict-unknown';
  return 'verdict-defect';
}

/** The verdicts that trap through traffic (`split_audit.DEFECT_VERDICTS`). */
const DEFECT_VERDICTS: ReadonlySet<SplitVerdict> = new Set<SplitVerdict>([
  'wrong_side',
  'added_lane_wrong_side',
  'through_lane_exit_only',
]);

export function isSplitDefect(verdict: SplitVerdict): boolean {
  return DEFECT_VERDICTS.has(verdict);
}

/** A defect of the exit's side (`wrong_side`, `added_lane_wrong_side`), as
 * opposed to a trapped lane on an exit compiled on the drawn side. */
function isSideDefect(verdict: SplitVerdict): boolean {
  return verdict === 'wrong_side' || verdict === 'added_lane_wrong_side';
}

function lanesText(f: CorridorSplitFinding): string {
  const lanes = f.exit_from_lanes.length ? f.exit_from_lanes.join(',') : '—';
  const side = f.added_lane_side === 'left' || f.added_lane_side === 'right';
  const added = f.added_lane ? (side ? ` +1 added on the ${f.added_lane_side}` : ' +1 added') : '';
  const trapped = f.trapped_lanes?.length
    ? ` · trapped ${f.trapped_lanes.join(',')}` +
      (f.trapped_evidence ? ` (by ${f.trapped_evidence})` : '')
    : '';
  return `${f.compiled_side} · lane ${lanes} of ${f.compiled_lanes}${added}${trapped}`;
}

/** The summary's defect clause: side defects and trapped lanes counted apart,
 * since "compiled on the wrong side" is not true of a trapped lane. */
function defectsText(findings: CorridorSplitFinding[]): string | null {
  const side = findings.filter((f) => isSideDefect(f.verdict)).length;
  const trapped = findings.filter((f) => f.verdict === 'through_lane_exit_only').length;
  const parts: string[] = [];
  if (side > 0) parts.push(`${side} compiled on the wrong side`);
  if (trapped > 0) {
    parts.push(
      trapped === 1
        ? '1 with a through lane compiled exit-only'
        : `${trapped} with through lanes compiled exit-only`,
    );
  }
  return parts.length > 0 ? parts.join(', ') : null;
}

export function SplitAuditTable({
  findings,
}: {
  findings: CorridorSplitFinding[] | undefined;
}): JSX.Element | null {
  if (!findings || findings.length === 0) return null;
  const defects = defectsText(findings);
  return (
    <div className="split-audit">
      <p className="split-audit-summary">
        split audit: {findings.length} exit{findings.length === 1 ? '' : 's'} checked,{' '}
        {defects === null ? (
          'none compiled on the wrong side'
        ) : (
          <span className="hint-amber">{defects} — the remedy is listed under each</span>
        )}
      </p>
      <div className="table-wrap">
        <table className="data compact" aria-label="split audit">
          <thead>
            <tr>
              <th className="num">Position</th>
              <th>Split → exit</th>
              <th>OSM side</th>
              <th>Compiled lanes</th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {findings.map((f) => {
              const key = `${f.from_edge}-${f.exit_edge}`;
              const defect = isSplitDefect(f.verdict);
              return [
                <tr key={key} data-verdict={f.verdict}>
                  <td className="num">{formatDistKm(f.x_m)}</td>
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
                    <td colSpan={5} className="remedy hint-amber">
                      remedy: <code className="mono">{f.remedy}</code>
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
