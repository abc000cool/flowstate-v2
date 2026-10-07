/** Accessibility audit of every route and of the states only a live API
 * produces (docs/design/DASHBOARD_DESIGN.md §12.4: "axe reports 0 serious or
 * critical issues per route").
 *
 * axe-core is not installed and §12.1 forbids adding it, so the audit is
 * `./a11y.ts`: the serious and critical axe rules that can be judged from the
 * DOM in jsdom, on Testing Library's accessible-name computation. Colour
 * contrast and anything else that needs layout is out of its reach (the token
 * contrast table is computed in the brief, §11.1). Both themes render the
 * same DOM, so one pass covers them for every rule checked here. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, setOfflineFallback } from '../api/client';
import { App } from '../App';
import { auditA11y, formatViolations } from './a11y';

function renderAt(route: string): void {
  render(
    <MemoryRouter initialEntries={[route]}>
      <App />
    </MemoryRouter>,
  );
}

/** Nothing on the page is still a loading placeholder. */
async function settled(): Promise<void> {
  await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull(), {
    timeout: 6000,
  });
}

function expectClean(): void {
  expect(formatViolations(auditA11y(document.body))).toEqual([]);
}

/* ------------------------- demo data (API down) -------------------------- */

describe('accessibility audit: every route on demo data', () => {
  beforeEach(() => {
    clearAuthFailure();
    setOfflineFallback(true);
    // jsdom has no canvas; the heatmap draws nothing and keeps its frame
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (): Promise<Response> => {
        throw new TypeError('network down');
      }),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    setOfflineFallback(false);
  });

  const routes: [string, () => Promise<unknown>][] = [
    ['/scenarios', () => screen.findByText('corridor_10km', {}, { timeout: 4000 })],
    ['/onboard', () => screen.findByRole('button', { name: 'Onboard corridor' })],
    [
      '/runs',
      () => within(screen.getByRole('table', { name: 'runs' })).findByText('run-a41d09', {}, { timeout: 4000 }),
    ],
    [
      '/runs/run-a41d09',
      () => screen.findByRole('img', { name: /^Space–time speed field/ }, { timeout: 4000 }),
    ],
    ['/sweeps', () => screen.findByRole('heading', { level: 1, name: 'Sweeps' })],
    // the demo baseline beside the demo follower_stopper run: both fields drawn
    [
      '/compare?a=run-8f2c11&b=run-a41d09',
      () => screen.findAllByRole('img', { name: /^Space–time speed field/ }, { timeout: 4000 }),
    ],
    // a sweep id while offline is refused rather than drawn from demo data;
    // the matrix is audited against the live API below
    ['/sweeps?sweep=sw-demo', () => screen.findByText(/could not be loaded/, {}, { timeout: 4000 })],
    [
      '/reports',
      () => within(screen.getByRole('table', { name: 'finished runs' })).findAllByText(/^run-/, {}, { timeout: 4000 }),
    ],
    ['/first-run', () => screen.findByText('0/6 done')],
  ];

  for (const [route, ready] of routes) {
    it(`${route} has no serious or critical violations`, async () => {
      renderAt(route);
      await ready();
      await settled();
      expectClean();
    }, 15000);
  }
});

/* ------------------------------ live API ---------------------------------- */

const RING = {
  name: 'ring_sugiyama',
  filename: 'ring_sugiyama.yaml',
  config_hash: 'a226444c0145',
  preset: true,
  config: {
    name: 'ring_sugiyama',
    tier: 'micro',
    network: { kind: 'ring', circumference_m: 230, n_vehicles: 22 },
    fleet: { model: 'IDM' },
    av: { penetration: 0, compliance: 1, controller: null },
    sim: { duration_s: 600, warmup_s: 180 },
    seed: 42,
    replicates: 20,
  },
};

const OSM = {
  name: 'mndot_i94_wb_stpaul',
  filename: 'mndot_i94_wb_stpaul.yaml',
  config_hash: 'osmhash000001',
  preset: true,
  config: {
    name: 'mndot_i94_wb_stpaul',
    tier: 'micro',
    network: {
      kind: 'osm',
      osm_file: 'data/osm/mndot_i94_wb_stpaul.osm',
      bbox: [44.9425, -93.099, 44.9613, -92.9612],
      corridor_edges: ['78288791', '638519815'],
      inflow: [[0, 1.1]],
    },
    fleet: { model: 'IDM' },
    av: { penetration: 0, compliance: 1, controller: null },
    sim: { duration_s: 14400, warmup_s: 1800 },
    seed: 42,
    replicates: 20,
  },
};

const STORED = {
  scenario_id: 'scn_i24',
  name: 'i24_replica',
  config_hash: 'c0ffeec0ffee',
  created_at: '2026-10-01T00:00:00',
  preset: false,
  config: {
    name: 'i24_replica',
    tier: 'micro',
    network: { kind: 'corridor', length_m: 6400, lanes: 4, inflow: [[0, 1.4]] },
    fleet: { model: 'IDM' },
    av: { penetration: 0, compliance: 1, controller: null },
    sim: { duration_s: 7800 },
    seed: 42,
    replicates: 20,
  },
};

function run(id: string, status: string, done: number, error: string | null = null): unknown {
  return {
    run_id: id,
    scenario_id: 'scn_i24',
    sweep_id: null,
    status,
    tier: 'micro',
    config_hash: 'c0ffeec0ffee',
    seeded: false,
    progress: { completed_replicates: done, total_replicates: 20 },
    seeds: [1, 2, 3],
    error,
    error_kind: error ? 'ValueError' : null,
    created_at: '2026-10-01T00:00:00',
  };
}

const RUNS = [
  run('run-done', 'done', 20),
  run('run-going', 'running', 7),
  run('run-failed', 'failed', 0, 'Duration 1800 s leaves no measurement window'),
];

const CRITERIA = [
  {
    name: 'fhwa_default',
    source: 'FlowState default (CLAUDE.md §7.1), from FHWA-HRT-04-040 §5.6.',
    geh_threshold: 5.0,
    geh_pass_fraction: 0.85,
    geh_pass_inclusive: true,
    rmspe_max: 0.15,
    wave_speed_band_kmh: [14.0, 22.0],
    min_seeds: 20,
    require_ring_emergence: true,
    require_ring_dampening: true,
    require_sensitivity_grid: true,
    wave_detector: 'stack',
    default: true,
  },
];

function split(over: Record<string, unknown>): Record<string, unknown> {
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
    compiled_lanes: 5,
    exit_from_lanes: [0],
    compiled_side: 'rightmost',
    option_lanes: [],
    added_lane: false,
    exit_lanes: 1,
    continuing_lanes: 4,
    verdict: 'ok',
    remedy: '',
    ...over,
  };
}

const SUMMARY = {
  corridor: 'mndot_i94_wb',
  chain_length_m: 11820,
  n_chain_edges: 32,
  lanes_profile: [
    [0, 3200, 3],
    [3200, 11820, 4],
  ],
  n_ramps: 2,
  stations_placed: [
    { station: 'S1063', x_m: 1110, offset_m: 4.2 },
    { station: 'S97', x_m: 11027, offset_m: 6.7 },
  ],
  stations_rejected: [{ station: 'S1450', x_m: 4820, offset_m: 168.4 }],
  stations_without_chain_x: ['S1450'],
  lanes_compared: 2,
  lane_mismatches: [
    { station: 'S1063', x_m: 1110, compiled_lanes: 4, inventory_lanes: 3, hint: 'acceleration lane' },
  ],
  inflow_peak_veh_h: 4275,
  ramps: [
    { name: 'I-494 entrance', kind: 'on', x_m: 1980, method: 'detector', peak: 1260, unit: 'veh/h', station: 'D1064' },
    { name: 'Mounds Blvd exit', kind: 'off', x_m: 10240, method: 'conservation', peak: 0.08, unit: 'frac', station: null },
  ],
  residuals: [{ from: 'S792', to: 'S791', mean_residual_veh_h: 775, note: 'carried' }],
  zeroed_ramps: ['Kellogg Blvd exit (x=11510 m): outside the observed span'],
  unmatched_detectors: ['D1210'],
  split_audit: [
    split({}),
    split({
      from_edge: '45782590-AddedOffRampEdge',
      exit_edge: '42165869',
      osm_side: 'left',
      compiled_side: 'leftmost',
      compiled_lanes: 4,
      exit_from_lanes: [3],
      added_lane: true,
      added_lane_side: 'right',
      trapped_lanes: [3],
      trapped_evidence: 'turn:lanes',
      verdict: 'through_lane_exit_only',
      remedy: 'add `--ramps.unset 45782590` to netconvert_extra, then re-audit',
    }),
    split({ exit_edge: '82150350', verdict: 'added_lane_wrong_side', added_lane: true, remedy: '--ramps.unset 1001426896' }),
  ],
  split_audit_before_fixes: [split({ verdict: 'wrong_side', remedy: 'patch' })],
  ramp_guessing: true,
  split_fixes: true,
  split_fixes_applied: 1,
  split_defects_remaining: 2,
  applied: 'ramp guessing on; split fixes: 1 applied, 2 remaining',
  lines: ['corridor mndot_i94_wb: 11.82 km along 32 edges'],
};

const CORRIDOR = {
  corridor_id: 'cor_9f21ab77cd10',
  name: 'mndot_i94_wb',
  status: 'done',
  progress: { stage: 'done', completed_stages: 5, total_stages: 5 },
  scenario_id: 'scn_onboarded01',
  preset_filename: 'mndot_i94_wb.yaml',
  config_hash: 'a1b2c3d4e5f6',
  observations_path: '/srv/runs/corridors/cor_9f21ab77cd10/observations.json',
  corridor_dir: 'corridors/cor_9f21ab77cd10',
  summary: SUMMARY,
  error: null,
  error_kind: null,
  created_at: '2026-09-23T06:00:00',
};

function ci(mean: number, n = 20): unknown {
  return { mean, lo95: mean * 0.95, hi95: mean * 1.05, n, underpowered: n < 20, reason: null };
}

function aggregate(sigma: number, throughput: number): unknown {
  return {
    throughput_veh_h: ci(throughput),
    mean_tt_s: ci(500),
    p90_tt_s: ci(600),
    sigma_v_spatial_ms: ci(sigma),
    sigma_v_temporal_ms: ci(sigma * 0.8),
    vmt_veh_km: ci(5800),
    vht_veh_h: ci(80),
    fuel_ml_per_veh_km: ci(60),
    wave_count: ci(3),
    wave_speed_kmh: ci(17),
    wave_amplitude_ms: ci(10),
  };
}

const progressDone = { completed_replicates: 20, total_replicates: 20 };

/** A SweepOut: the baseline cell, a finished cell and one not reached yet. */
const SWEEP = {
  sweep_id: 'swp-1',
  scenario_id: 'scn_i24',
  status: 'running',
  tier: 'micro',
  error: null,
  created_at: '2026-10-01T00:00:00',
  runs_total: 3,
  runs_done: 2,
  runs_failed: 0,
  cells: [
    { penetration: 0, compliance: 1.0, controller: 'follower_stopper', config_hash: 'b0', run_id: 'run-base', status: 'done', progress: progressDone, aggregate: aggregate(5.8, 1700) },
    { penetration: 0.05, compliance: 0.8, controller: 'follower_stopper', config_hash: 'b1', run_id: 'run-p5', status: 'done', progress: progressDone, aggregate: aggregate(2.9, 1785) },
    { penetration: 0.1, compliance: 0.8, controller: 'follower_stopper', config_hash: 'b2', run_id: null, status: null, progress: null, aggregate: null },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

/** A small live API: enough of each endpoint for every view to render the
 * states the demo backend cannot (progress bars, failure reasons, a finished
 * corridor's summary, dialogs). */
async function liveApi(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  const method = init?.method ?? 'GET';
  if (url.endsWith('/health')) return json({ status: 'ok' });
  if (method !== 'GET') return json({ detail: `unexpected ${method} ${url}` }, 404);
  const path = url.replace(/^.*\/api\/v1/, '');
  if (path === '/scenarios/preset') return json([RING, OSM]);
  if (path === '/scenarios') return json([STORED]);
  if (path === '/runs') return json(RUNS);
  const runMatch = /^\/runs\/([^/]+)$/.exec(path);
  if (runMatch) {
    const found = RUNS.find((r) => (r as { run_id: string }).run_id === runMatch[1]);
    return found ? json(found) : json({ detail: 'no such run' }, 404);
  }
  if (path === '/criteria') return json(CRITERIA);
  if (path === `/sweeps/${SWEEP.sweep_id}`) return json(SWEEP);
  if (path === '/reports') return json([]);
  if (path === '/corridors') {
    const { summary: _summary, ...row } = CORRIDOR;
    return json([row]);
  }
  if (path === `/corridors/${CORRIDOR.corridor_id}`) return json(CORRIDOR);
  return json({ detail: `unexpected ${method} ${url}` }, 404);
}

describe('accessibility audit: live API states', () => {
  beforeEach(() => {
    clearAuthFailure();
    setOfflineFallback(false);
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    vi.stubGlobal('fetch', vi.fn(liveApi));
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it('Runs: live progress bars, a failure reason and the launch confirmation', async () => {
    renderAt('/runs');
    const table = await screen.findByRole('table', { name: 'runs' }, { timeout: 4000 });
    await within(table).findByText('run-going', {}, { timeout: 4000 });
    expect(within(table).getAllByRole('progressbar').length).toBeGreaterThan(0);
    // the long stored scenario takes the cost gate
    const select = screen.getByLabelText('Scenario');
    await waitFor(() => expect(within(select).getAllByRole('option').length).toBeGreaterThan(1));
    fireEvent.change(select, { target: { value: 'scn_i24' } });
    await waitFor(() => expect(screen.getByLabelText('Duration (s)')).toHaveValue(7800));
    fireEvent.click(screen.getByRole('button', { name: 'Launch run' }));
    await screen.findByRole('dialog', { name: 'Launch this run?' });
    await settled();
    expectClean();
  }, 15000);

  it('Scenarios: the OSM composer and the launch dialog', async () => {
    renderAt('/scenarios');
    const osmName = await screen.findByText('mndot_i94_wb_stpaul', {}, { timeout: 4000 });
    const card = osmName.closest('article') as HTMLElement;
    fireEvent.click(within(card).getByRole('button', { name: 'Load in composer' }));
    expect(screen.getByLabelText('imported network')).toBeInTheDocument();
    const ring = (await screen.findByText('ring_sugiyama')).closest('article') as HTMLElement;
    fireEvent.click(within(ring).getByRole('button', { name: 'Run…' }));
    await screen.findByRole('dialog', { name: 'Launch ring_sugiyama' });
    await settled();
    expectClean();
  }, 15000);

  it("Onboard: a finished corridor's summary, split audit and history", async () => {
    window.localStorage.setItem(
      'flowstate.onboard.last',
      JSON.stringify({ corridor_id: CORRIDOR.corridor_id }),
    );
    renderAt('/onboard');
    const audit = await screen.findByRole('table', { name: 'split audit' }, { timeout: 4000 });
    expect(within(audit).getByText('THROUGH LANE EXIT-ONLY')).toBeInTheDocument();
    await screen.findByRole('table', { name: 'corridors on this server' }, { timeout: 4000 });
    await settled();
    expectClean();
  }, 15000);

  it('Run detail: a failed run', async () => {
    renderAt('/runs/run-failed');
    await screen.findAllByText(/leaves no measurement window/, {}, { timeout: 4000 });
    await settled();
    expectClean();
  }, 15000);

  it('Sweeps: the matrix of a sweep, with its legend and cells', async () => {
    renderAt('/sweeps?sweep=swp-1');
    const matrix = await screen.findByRole('table', { name: 'sweep matrix' }, { timeout: 4000 });
    expect(within(matrix).getAllByRole('button').length).toBeGreaterThan(0);
    await settled();
    expectClean();
  }, 15000);

  it('Reports: the run picker with a run selected', async () => {
    renderAt('/reports');
    const picker = await screen.findByRole('table', { name: 'finished runs' }, { timeout: 4000 });
    fireEvent.click(await within(picker).findByLabelText('select run-done', {}, { timeout: 4000 }));
    await screen.findByRole('button', { name: 'Generate report (1)' });
    await settled();
    expectClean();
  }, 15000);

  it('First run: the walkthrough against a live service', async () => {
    renderAt('/first-run');
    const use = await screen.findByRole('button', { name: 'Use ring_sugiyama' }, { timeout: 4000 });
    await waitFor(() => expect(use).toBeEnabled(), { timeout: 4000 });
    await settled();
    expectClean();
  }, 15000);

  it('the Settings drawer', async () => {
    renderAt('/runs');
    await screen.findByText('API LINK', {}, { timeout: 4000 });
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await screen.findByRole('dialog', { name: 'Settings' });
    expectClean();
  }, 15000);
});

/* ------------------------- the audit itself ------------------------------- */

/** The audit must find what it claims to check, or a clean report above is
 * vacuous. Each fixture breaks one rule. */
describe('accessibility audit: the checker', () => {
  const cases: [string, JSX.Element][] = [
    ['button-name', <button type="button" />],
    ['link-name', <a href="/runs" />],
    ['label', <input type="number" />],
    ['label', <input type="file" />],
    ['select-name', <select><option>a</option></select>],
    ['image-alt', <img src="x.png" />],
    ['aria-progressbar-name', <div role="progressbar" aria-valuenow={1} />],
    ['aria-dialog-name', <div role="dialog">x</div>],
    ['aria-roles', <div role="buton">x</div>],
    ['aria-valid-attr-value', <button type="button" aria-describedby="nowhere">Go</button>],
    ['aria-required-attr', <div role="checkbox" tabIndex={0} aria-label="c" />],
    ['aria-required-children', <div role="tablist" aria-label="t"><button type="button">a</button></div>],
    ['aria-required-parent', <div><button type="button" role="tab">a</button></div>],
    ['aria-allowed-attr', <a href="/x" aria-selected="true">x</a>],
    ['aria-prohibited-attr', <span aria-label="insertion verdict">ok</span>],
    ['aria-prohibited-attr', <dl aria-label="facts"><dt>a</dt><dd>b</dd></dl>],
    ['aria-hidden-focus', <div aria-hidden="true"><button type="button">x</button></div>],
    ['nested-interactive', <div role="button" tabIndex={0}>a <a href="/x">b</a></div>],
    ['tabindex', <button type="button" tabIndex={2}>x</button>],
    ['list', <ul><div>x</div></ul>],
    ['listitem', <div><li>x</li></div>],
    ['definition-list', <dl><p>x</p></dl>],
    ['dlitem', <div><dt>x</dt></div>],
    [
      'duplicate-id-aria',
      <>
        <span id="d">a</span>
        <span id="d">b</span>
        <button type="button" aria-describedby="d">x</button>
      </>,
    ],
  ];

  for (const [rule, markup] of cases) {
    it(`reports ${rule}`, () => {
      const { container } = render(markup);
      expect(auditA11y(container).map((v) => v.rule)).toContain(rule);
    });
  }

  it('passes the corrected forms', () => {
    const { container } = render(
      <>
        <button type="button">Go</button>
        <button type="button" aria-label="Close settings" />
        <label htmlFor="f">File</label>
        <input id="f" type="file" />
        <input type="file" hidden />
        <span role="group" aria-label="insertion verdict">ok</span>
        <dl role="group" aria-label="facts">
          <div>
            <dt>a</dt>
            <dd>b</dd>
          </div>
        </dl>
        <div role="tablist" aria-label="Heatmap field">
          <button type="button" role="tab" aria-selected="true">SPEED</button>
        </div>
        <svg role="group" aria-label="strip">
          <g role="img" tabIndex={0} aria-label="seed 1 · 2 m/s" />
        </svg>
        <div aria-hidden="true">
          <button type="button" tabIndex={-1}>x</button>
        </div>
      </>,
    );
    expect(formatViolations(auditA11y(container))).toEqual([]);
  });
});
