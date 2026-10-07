/** SplitAuditTable: the corridor panel's split audit (CorridorSummaryOut
 * .split_audit). Pinned: nothing renders without findings; every finding is a
 * row with its position, edges, OSM side and compiled lanes; the verdict badge
 * is green for `ok`, red for either defect, grey for `unknown`; and only a
 * defect row carries its remedy line. */

import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { CorridorSplitFinding } from '../api/types';
import { SplitAuditTable, isSplitDefect, verdictClass } from '../components/SplitAuditTable';

function finding(over: Partial<CorridorSplitFinding>): CorridorSplitFinding {
  return {
    from_edge: '45608485',
    exit_edge: '18207912',
    continuing_edge: '45608486',
    x_m: 9650,
    osm_way: '18207912',
    osm_lanes: 5,
    turn_lanes: null,
    turn_lanes_side: 'unknown',
    osm_side: 'right',
    osm_offsets_m: [-8.1, -9.0],
    compiled_lanes: 5,
    exit_from_lanes: [3, 4],
    compiled_side: 'leftmost',
    option_lanes: [],
    added_lane: false,
    exit_lanes: 2,
    continuing_lanes: 3,
    verdict: 'wrong_side',
    remedy: 'patch_files connection patch restating the split on the right',
    ...over,
  };
}

describe('SplitAuditTable', () => {
  it('renders nothing without findings', () => {
    const { container } = render(<SplitAuditTable findings={undefined} />);
    expect(container).toBeEmptyDOMElement();
    const empty = render(<SplitAuditTable findings={[]} />);
    expect(empty.container).toBeEmptyDOMElement();
    expect(screen.queryByLabelText('split audit')).toBeNull();
  });

  it('lists every exit with its position, edges, OSM side and compiled lanes', () => {
    render(
      <SplitAuditTable
        findings={[
          finding({}),
          finding({
            from_edge: '42165869',
            exit_edge: '42165870',
            x_m: 3420,
            osm_side: 'left',
            exit_from_lanes: [3],
            compiled_lanes: 4,
            exit_lanes: 1,
            verdict: 'ok',
            remedy: '',
          }),
        ]}
      />,
    );
    const table = within(screen.getByLabelText('split audit'));
    expect(table.getByText('9.7 km')).toBeInTheDocument();
    expect(table.getByText('45608485 → 18207912')).toBeInTheDocument();
    expect(table.getByText('right')).toBeInTheDocument();
    expect(table.getByText('leftmost · lane 3,4 of 5')).toBeInTheDocument();
    expect(table.getByText('3.4 km')).toBeInTheDocument();
    expect(table.getByText('42165869 → 42165870')).toBeInTheDocument();
    expect(table.getByText('leftmost · lane 3 of 4')).toBeInTheDocument();
    // the summary line counts the defects, not the rows
    expect(screen.getByText(/split audit: 2 exits checked/)).toHaveTextContent(
      '1 compiled on the wrong side',
    );
  });

  it('colours the verdict badge by verdict and puts the remedy under a defect only', () => {
    render(
      <SplitAuditTable
        findings={[
          finding({ verdict: 'ok', remedy: '', exit_edge: 'e-ok' }),
          finding({ verdict: 'wrong_side', exit_edge: 'e-wrong', remedy: 'patch the split' }),
          finding({
            verdict: 'added_lane_wrong_side',
            exit_edge: 'e-added',
            added_lane: true,
            remedy: '--ramps.unset 1001426896',
          }),
          finding({ verdict: 'unknown', exit_edge: 'e-unknown', remedy: '' }),
        ]}
      />,
    );
    const ok = screen.getByText('OK');
    expect(ok).toHaveClass('tag', 'verdict-ok');
    expect(ok).not.toHaveClass('verdict-defect');
    expect(screen.getByText('WRONG SIDE')).toHaveClass('tag', 'verdict-defect');
    expect(screen.getByText('ADDED LANE, WRONG SIDE')).toHaveClass('tag', 'verdict-defect');
    expect(screen.getByText('UNKNOWN')).toHaveClass('tag', 'verdict-unknown');
    // the raw verdict value stays reachable in the badge's title
    expect(screen.getByText('WRONG SIDE')).toHaveAttribute(
      'title',
      expect.stringContaining('wrong_side:'),
    );
    // the added lane is stated in the lanes column
    expect(screen.getByText('leftmost · lane 3,4 of 5 +1 added')).toBeInTheDocument();
    // remedies: one per defect, none for ok / unknown
    const remedies = screen.getAllByText(/^remedy:/);
    expect(remedies).toHaveLength(2);
    expect(remedies[0]).toHaveTextContent('remedy: patch the split');
    expect(remedies[1]).toHaveTextContent('remedy: --ramps.unset 1001426896');
    expect(screen.getByText(/split audit: 4 exits checked/)).toHaveTextContent(
      '2 compiled on the wrong side',
    );
  });

  it('says so when every exit is on the drawn side', () => {
    render(<SplitAuditTable findings={[finding({ verdict: 'ok', remedy: '' })]} />);
    expect(screen.getByText(/split audit: 1 exit checked/)).toHaveTextContent(
      'none compiled on the wrong side',
    );
    expect(screen.queryByText(/^remedy:/)).toBeNull();
  });

  // 2026-10-07: an exit compiled on the drawn side can still trap a lane the
  // map draws as continuing (the I-94 WB 6th Street exit; docs/CONTRACTS.md
  // "Split audit, lane by lane"). It is a defect with its own badge, its
  // trapped lanes stated, and the --ramps.unset remedy under the row.
  it('shows a through lane compiled exit-only as a defect with its remedy', () => {
    const remedy =
      'lane(s) 3 lead only to the exit although OSM draws them continuing (by turn:lanes): ' +
      'ramp guessing added a lane and shifted the through lanes; add `--ramps.unset 45782590` ' +
      'to netconvert_extra (scripts/onboard_corridor.py applies it), then re-audit';
    render(
      <SplitAuditTable
        findings={[
          finding({
            from_edge: '45782590-AddedOffRampEdge',
            exit_edge: '42165869',
            osm_side: 'left',
            turn_lanes: 'slight_left;through|none|none',
            compiled_side: 'leftmost',
            compiled_lanes: 4,
            exit_from_lanes: [3],
            added_lane: true,
            added_lane_side: 'right',
            trapped_lanes: [3],
            trapped_evidence: 'turn:lanes',
            verdict: 'through_lane_exit_only',
            remedy,
          }),
        ]}
      />,
    );
    const badge = screen.getByText('THROUGH LANE EXIT-ONLY');
    expect(badge).toHaveClass('tag', 'verdict-defect');
    expect(badge).not.toHaveClass('verdict-ok');
    expect(badge).toHaveAttribute('title', expect.stringContaining('through_lane_exit_only:'));
    // the lanes column names the side the lane was added on and the trapped lane
    expect(
      screen.getByText('leftmost · lane 3 of 4 +1 added on the right · trapped 3 (by turn:lanes)'),
    ).toBeInTheDocument();
    const remedies = screen.getAllByText(/^remedy:/);
    expect(remedies).toHaveLength(1);
    expect(remedies[0]).toHaveTextContent('--ramps.unset 45782590');
    // the exit is on the drawn side, so the summary does not call it the wrong one
    const summary = screen.getByText(/split audit: 1 exit checked/);
    expect(summary).toHaveTextContent('1 with a through lane compiled exit-only');
    expect(summary).not.toHaveTextContent('wrong side');
    expect(summary).not.toHaveTextContent('none compiled');
  });

  it('counts side defects and trapped lanes apart in the summary', () => {
    render(
      <SplitAuditTable
        findings={[
          finding({ verdict: 'wrong_side', exit_edge: 'e-wrong' }),
          finding({
            verdict: 'through_lane_exit_only',
            exit_edge: 'e-trap-1',
            trapped_lanes: [2],
            trapped_evidence: 'lane count',
          }),
          finding({
            verdict: 'through_lane_exit_only',
            exit_edge: 'e-trap-2',
            trapped_lanes: [3],
            trapped_evidence: 'turn:lanes',
          }),
          finding({ verdict: 'ok', exit_edge: 'e-ok', remedy: '' }),
        ]}
      />,
    );
    expect(screen.getByText(/split audit: 4 exits checked/)).toHaveTextContent(
      '1 compiled on the wrong side, 2 with through lanes compiled exit-only',
    );
    expect(screen.getAllByText('THROUGH LANE EXIT-ONLY')).toHaveLength(2);
    expect(screen.getAllByText(/^remedy:/)).toHaveLength(3);
    expect(screen.getByText(/trapped 2 \(by lane count\)$/)).toBeInTheDocument();
  });

  it('treats the trapped-lane verdict as a defect in the helpers', () => {
    expect(verdictClass('through_lane_exit_only')).toBe('verdict-defect');
    expect(isSplitDefect('through_lane_exit_only')).toBe(true);
  });

  it('exposes the verdict helpers the view relies on', () => {
    expect(verdictClass('ok')).toBe('verdict-ok');
    expect(verdictClass('wrong_side')).toBe('verdict-defect');
    expect(verdictClass('added_lane_wrong_side')).toBe('verdict-defect');
    expect(verdictClass('unknown')).toBe('verdict-unknown');
    expect(isSplitDefect('ok')).toBe(false);
    expect(isSplitDefect('unknown')).toBe(false);
    expect(isSplitDefect('wrong_side')).toBe(true);
    expect(isSplitDefect('added_lane_wrong_side')).toBe(true);
  });
});
