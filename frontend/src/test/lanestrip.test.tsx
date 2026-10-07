/** The Onboard lane strip (docs/design/DASHBOARD_DESIGN.md §10.2 P2): the
 * model built from the onboarding summary alone, the SVG schematic (lanes per
 * segment, ramps, split verdicts with trapped lanes, lane-count mismatches)
 * and its text alternative. */

import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, setOfflineFallback } from '../api/client';
import type { CorridorSplitFinding, CorridorSummary } from '../api/types';
import { AppStateProvider } from '../components/AppContext';
import { LaneStrip, laneStripModel, laneStripSummary, splitText } from '../components/LaneStrip';
import { OnboardView } from '../views/OnboardView';
import { auditA11y, formatViolations } from './a11y';

function split(over: Partial<CorridorSplitFinding>): CorridorSplitFinding {
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
    osm_offsets_m: [-8.1],
    compiled_lanes: 4,
    exit_from_lanes: [0],
    compiled_side: 'rightmost',
    option_lanes: [],
    added_lane: false,
    exit_lanes: 1,
    continuing_lanes: 3,
    verdict: 'ok',
    remedy: '',
    ...over,
  };
}

const SUMMARY: CorridorSummary = {
  corridor: 'mndot_i94_wb',
  chain_length_m: 11820,
  n_chain_edges: 32,
  lanes_profile: [
    [0, 3200, 3],
    [3200, 11820, 4],
  ],
  n_ramps: 2,
  stations_placed: [{ station: 'S1063', x_m: 1110, offset_m: 4.2 }],
  stations_rejected: [],
  stations_without_chain_x: [],
  inflow_peak_veh_h: 4275,
  ramps: [
    { name: 'I-494 entrance', kind: 'on', x_m: 1980, method: 'detector', peak: 1260, unit: 'veh/h', station: 'D1064' },
    { name: 'Mounds Blvd exit', kind: 'off', x_m: 10240, method: 'conservation', peak: 0.08, unit: 'frac', station: null },
  ],
  residuals: [],
  zeroed_ramps: [],
  unmatched_detectors: [],
  lanes_compared: 2,
  lane_mismatches: [
    { station: 'S1063', x_m: 1110, compiled_lanes: 4, inventory_lanes: 3, hint: 'acceleration lane' },
  ],
  split_audit: [
    split({}),
    split({
      exit_edge: '42165869',
      x_m: 6120,
      osm_side: 'left',
      compiled_side: 'leftmost',
      exit_from_lanes: [3],
      added_lane: true,
      added_lane_side: 'right',
      trapped_lanes: [3],
      trapped_evidence: 'turn:lanes',
      verdict: 'through_lane_exit_only',
      remedy: 'add `--ramps.unset 45782590` to netconvert_extra, then re-audit',
    }),
  ],
  lines: [],
};

describe('laneStripModel', () => {
  it('reads segments, ramps, splits and mismatches, and lists them by position', () => {
    const model = laneStripModel(SUMMARY);
    expect(model).not.toBeNull();
    expect(model!.length).toBe(11820);
    expect(model!.rows).toBe(4);
    expect(model!.entrances).toEqual([{ x: 1980, name: 'I-494 entrance' }]);
    expect(model!.exits).toEqual([{ x: 10240, name: 'Mounds Blvd exit' }]);
    expect(model!.splits.map((s) => [s.x, s.defect, s.trapped])).toEqual([
      [9650, false, []],
      [6120, true, [3]],
    ]);
    expect(model!.events.map((e) => [e.x, e.tone])).toEqual([
      [0, 'plain'],
      [1110, 'warning'],
      [1980, 'plain'],
      [3200, 'plain'],
      [6120, 'defect'],
      [9650, 'plain'],
      [10240, 'plain'],
    ]);
    expect(model!.events[0].text).toBe('3 lanes');
    expect(model!.events[3].text).toBe('3 → 4 lanes');
    expect(laneStripSummary(model!)).toBe(
      '3–4 lanes · 1 entrance · 1 exit · 2 splits audited, 1 defect · 1 lane-count mismatch',
    );
  });

  it('words each verdict, naming the trapped lane', () => {
    expect(splitText(SUMMARY.split_audit![1])).toBe(
      'split to exit 42165869: defect, lane 3 of 4 is drawn as continuing (by turn:lanes) ' +
        'but compiled exit-only; through traffic there is trapped',
    );
    expect(splitText(split({}))).toBe('split to exit 18207912: as drawn (right side), fed from lane 0 of 4');
    expect(splitText(split({ verdict: 'wrong_side', exit_from_lanes: [2, 3], compiled_side: 'leftmost' }))).toMatch(
      /defect, drawn on the right side but compiled leftmost \(fed from lanes 2, 3 of 4\)/,
    );
    expect(splitText(split({ verdict: 'unknown' }))).toMatch(/side undecided/);
  });

  it('has nothing to draw without a lane profile', () => {
    expect(laneStripModel({ ...SUMMARY, lanes_profile: [] })).toBeNull();
  });

  it('widens the carriageway for a split compiled with more lanes than the profile', () => {
    const wide = laneStripModel({
      ...SUMMARY,
      split_audit: [split({ compiled_lanes: 6, exit_from_lanes: [5] })],
    });
    expect(wide!.rows).toBe(6);
  });
});

describe('LaneStrip', () => {
  it('draws the corridor as one named image, with a text alternative', () => {
    const { container } = render(<LaneStrip summary={SUMMARY} />);
    const img = screen.getByRole('img', { name: /^Lane strip, 11\.8 km: 3–4 lanes · 1 entrance/ });
    expect(img.tagName.toLowerCase()).toBe('svg');
    // (the legend's swatches reuse the classes, so counts are on the plot)
    const plot = (sel: string): NodeListOf<Element> => img.querySelectorAll(sel);
    // one band per lanes_profile run; 3 + 4 lanes means 2 + 3 dashed lane lines
    expect(plot('.ls-road')).toHaveLength(2);
    expect(plot('.ls-lane-line')).toHaveLength(5);
    // ramps, verdict markers and the mismatch marker
    expect(plot('.ls-ramp')).toHaveLength(2);
    expect(plot('.ls-mark-ok')).toHaveLength(1);
    expect(plot('.ls-mark-defect')).toHaveLength(1);
    expect(plot('.ls-mark-warn')).toHaveLength(1);
    // the trapped lane is hatched, the healthy exit's feeding lane shaded
    const trapped = plot('.ls-trapped');
    expect(trapped).toHaveLength(1);
    expect(trapped[0].getAttribute('fill')).toMatch(/^url\(#.+-hatch\)$/);
    expect(plot('.ls-feed')).toHaveLength(1);
    expect(plot('.ls-split.is-defect')).toHaveLength(1);
    // every marker carries a hover title; the text list carries the same
    expect(plot('g > title').length).toBe(5); // 2 splits, 1 mismatch, 2 ramps
    // no literal colours: tokens through classes only
    expect(container.innerHTML).not.toMatch(/#[0-9a-f]{6}\b|rgb\(/i);
  });

  it('lists every position in the text alternative, defects marked in words', () => {
    render(<LaneStrip summary={SUMMARY} />);
    expect(screen.getByText('Lane strip as text').tagName).toBe('SUMMARY');
    // the legend is a <ul>; the text alternative is the ordered list
    const ol = document.querySelector('ol.lane-strip-list') as HTMLElement;
    const lines = within(ol).getAllByRole('listitem').map((li) => li.textContent);
    expect(lines).toEqual([
      '0 km 3 lanes',
      '1.1 km station S1063: lane count differs, the map has 4 and the detector inventory 3',
      '2 km entrance, I-494 entrance',
      '3.2 km 3 → 4 lanes',
      expect.stringMatching(/^6\.1 km split to exit 42165869: defect, lane 3 of 4/),
      '9.7 km split to exit 18207912: as drawn (right side), fed from lane 0 of 4',
      '10.2 km exit, Mounds Blvd exit',
    ]);
    expect(within(ol).getAllByRole('listitem')[4]).toHaveClass('is-defect');
    expect(within(ol).getAllByRole('listitem')[1]).toHaveClass('is-warning');
  });

  it('shows a legend for what is drawn only', () => {
    render(<LaneStrip summary={{ ...SUMMARY, split_audit: [], lane_mismatches: [] }} />);
    const legend = document.querySelector('ul.lane-strip-legend') as HTMLElement;
    expect(within(legend).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Entrance', 'Exit']);
    expect(screen.getByRole('img')).toHaveAccessibleName(/1 entrance · 1 exit\. Each position/);
  });

  it('renders nothing for a summary without a lane profile', () => {
    const { container } = render(<LaneStrip summary={{ ...SUMMARY, lanes_profile: [] }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('has no serious or critical accessibility violations', () => {
    render(<LaneStrip summary={SUMMARY} />);
    expect(formatViolations(auditA11y(document.body))).toEqual([]);
  });
});

describe('Onboard: the lane strip in the summary', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('draws the finished corridor under the lead, without a request of its own', async () => {
    setOfflineFallback(false);
    clearAuthFailure();
    const corridor = {
      corridor_id: 'cor_9f21ab77cd10',
      name: 'mndot_i94_wb',
      status: 'done',
      progress: { stage: 'done', completed_stages: 5, total_stages: 5 },
      scenario_id: 'scn_onboarded01',
      preset_filename: 'mndot_i94_wb.yaml',
      config_hash: 'a1b2c3d4e5f6',
      observations_path: '/srv/obs.json',
      corridor_dir: 'corridors/cor_9f21ab77cd10',
      summary: SUMMARY,
      error: null,
      error_kind: null,
      created_at: '2026-09-23T06:00:00',
    };
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        urls.push(url);
        const body = url.endsWith(`/corridors/${corridor.corridor_id}`) ? corridor : [];
        return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
      }),
    );
    window.localStorage.setItem('flowstate.onboard.last', JSON.stringify({ corridor_id: corridor.corridor_id }));
    render(
      <AppStateProvider>
        <MemoryRouter initialEntries={['/onboard']}>
          <OnboardView />
        </MemoryRouter>
      </AppStateProvider>,
    );
    const img = await screen.findByRole('img', { name: /^Lane strip, 11\.8 km/ }, { timeout: 4000 });
    const panel = img.closest('section') as HTMLElement;
    expect(within(panel).getByRole('heading', { name: 'What the onboarding found' })).toBeInTheDocument();
    expect(within(panel).getByRole('heading', { name: 'Lane strip' })).toBeInTheDocument();
    // the strip reads the summary already fetched: no extra endpoint
    expect(urls.every((u) => /\/corridors(\/cor_9f21ab77cd10)?(\?.*)?$/.test(u))).toBe(true);
  });
});
