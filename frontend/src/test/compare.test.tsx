/** Compare (views/CompareView.tsx) against the real API's shapes: `RunOut`
 * (with `sweep_id`), `MetricsOut` (aggregates keyed by the
 * validation.metrics.Metrics field names, `CIOut` in its three states),
 * `HeatmapOut` and `SweepOut`. The difference columns are checked against
 * hand-computed values, a metric a run did not record must read "not
 * recorded" (never a zero), and a mismatched tier, scenario or config hash
 * must be said, not silently folded into B − A. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, setOfflineFallback } from '../api/client';
import type { AggregateStat, Heatmap, RunDetail, ScenarioSummary, SweepDetail } from '../api/types';
import { AppStateProvider } from '../components/AppContext';
import { sameExtent, unionExtent } from '../components/HeatmapCanvas';
import {
  compareNotes,
  compareSections,
  describeController,
  describePenComp,
  describeSeeds,
  formatSigned,
  metricDifference,
  nonzeroDifferences,
  resolveRunSetup,
  sameDemandRealisation,
  sharedSeed,
  sideStat,
} from '../lib/compare';
import { CompareView } from '../views/CompareView';
import { RunDetailView } from '../views/RunDetailView';
import { auditA11y, formatViolations } from './a11y';

/* ------------------------------ fixtures ------------------------------ */

const CORRIDOR = {
  scenario_id: 'scn-corridor',
  name: 'corridor_10km',
  config_hash: 'c0ffee000001',
  created_at: '2026-10-01T00:00:00',
  preset: false,
  config: {
    name: 'corridor_10km',
    tier: 'micro',
    network: { kind: 'corridor', length_m: 10000, lanes: 1, inflow: [[0, 0.55]] },
    fleet: { model: 'IDM' },
    av: { penetration: 0, compliance: 1, controller: null },
    sim: { duration_s: 1200 },
    seed: 42,
    replicates: 20,
  },
};

const RING = {
  scenario_id: 'scn-ring',
  name: 'ring_sugiyama',
  config_hash: 'a226444c0145',
  created_at: '2026-10-01T00:00:00',
  preset: false,
  config: {
    name: 'ring_sugiyama',
    tier: 'micro',
    network: { kind: 'ring', circumference_m: 230, n_vehicles: 22 },
    fleet: { model: 'IDM' },
    av: { penetration: 0.05, compliance: 1, controller: 'follower_stopper' },
    sim: { duration_s: 600, warmup_s: 180 },
    seed: 42,
    replicates: 20,
  },
};

/** Replicate seeds as the API serialises them: decimal strings. */
const SEEDS = Array.from({ length: 20 }, (_, i) => String(2000 + i));

/** `RunOut` exactly as the API serialises it. */
function runOut(id: string, over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: id,
    scenario_id: 'scn-corridor',
    sweep_id: 'swp-1',
    status: 'done',
    tier: 'micro',
    config_hash: `${id}-hash-0000`,
    seeded: false,
    progress: { completed_replicates: 20, total_replicates: 20 },
    seeds: SEEDS,
    error: null,
    error_kind: null,
    created_at: '2026-10-02T00:00:00',
    ...over,
  };
}

const RUNS: Record<string, Record<string, unknown>> = {
  // the sweep's p=0 baseline cell and a controlled cell of the same sweep
  'run-base': runOut('run-base', { config_hash: 'b0b0b0b0b0b0' }),
  'run-p5': runOut('run-p5', { config_hash: 'b1b1b1b1b1b1' }),
  // a screening run launched on its own, with overrides (hash ≠ scenario's)
  'run-macro': runOut('run-macro', { sweep_id: null, tier: 'macro', config_hash: 'ma0000000001' }),
  // a ring run launched as stored (hash = scenario's)
  'run-ring': runOut('run-ring', { sweep_id: null, scenario_id: 'scn-ring', config_hash: 'a226444c0145' }),
  'run-going': runOut('run-going', {
    sweep_id: null,
    status: 'running',
    progress: { completed_replicates: 7, total_replicates: 20 },
  }),
  // finished, but its metrics read fails
  'run-err': runOut('run-err', { sweep_id: null, config_hash: 'e0e0e0e0e0e0' }),
  // run-base's configuration run again (same hash, so the same seeds): once
  // reproducing it exactly, once not
  'run-twin': runOut('run-twin', { sweep_id: null, config_hash: 'b0b0b0b0b0b0' }),
  'run-drift': runOut('run-drift', { sweep_id: null, config_hash: 'b0b0b0b0b0b0' }),
  // 64-bit seeds as the API sends them (spawn_seeds(42, ·) is 6914975401685141156, …):
  // past 2^53, so JSON.parse of a number would round each of them. A's two
  // seeds are one double apart from nothing — Number() makes them equal.
  'run-big-a': runOut('run-big-a', {
    sweep_id: null,
    config_hash: 'b16a00000001',
    seeds: ['6914975401685141156', '6914975401685141157'],
    progress: { completed_replicates: 2, total_replicates: 2 },
  }),
  // B ran A's first seed second, so B's field names it in the request
  'run-big-b': runOut('run-big-b', {
    sweep_id: null,
    config_hash: 'b16b00000001',
    seeds: ['134183728835869882', '6914975401685141156'],
    progress: { completed_replicates: 2, total_replicates: 2 },
  }),
};

/** Paths the stub answers 503 for (a store busy for a moment). */
const failing = new Set<string>();

function ci(mean: number, lo: number, hi: number, n = 20): AggregateStat {
  return { mean, lo95: lo, hi95: hi, n, underpowered: n < 20, reason: null };
}

/** `CIOut` when no replicate produced the metric. */
const NO_OBS: AggregateStat = {
  mean: null,
  lo95: null,
  hi95: null,
  n: 0,
  underpowered: false,
  reason: 'no_observations',
};

const AGG_BASE: Record<string, AggregateStat> = {
  throughput_veh_h: ci(1700, 1650, 1750),
  mean_tt_s: ci(512.4, 505.0, 519.8),
  p90_tt_s: ci(640.2, 628.0, 652.4),
  sigma_v_spatial_ms: ci(5.8, 5.5, 6.1),
  sigma_v_temporal_ms: ci(4.64, 4.4, 4.88),
  vmt_veh_km: ci(5800, 5700, 5900),
  vht_veh_h: ci(80.0, 78.0, 82.0),
  fuel_ml_per_veh_km: ci(61.2, 60.1, 62.3),
  wave_count: ci(3.2, 2.8, 3.6),
  wave_speed_kmh: ci(17.9, 16.8, 19.0),
  wave_amplitude_ms: ci(10.4, 9.8, 11.0),
  n_travel_time_veh: ci(1412, 1390, 1434),
};

/** The controlled cell: no fuel entry at all (not recorded), no wave detected
 * in any replicate (no observations for the wave speed). */
const AGG_P5: Record<string, AggregateStat> = {
  throughput_veh_h: ci(1785, 1741, 1829),
  mean_tt_s: ci(498.1, 491.0, 505.2),
  p90_tt_s: ci(611.0, 600.0, 622.0),
  sigma_v_spatial_ms: ci(2.9, 2.6, 3.2),
  sigma_v_temporal_ms: ci(2.32, 2.1, 2.54),
  vmt_veh_km: ci(5820, 5720, 5920),
  vht_veh_h: ci(77.5, 75.6, 79.4),
  wave_count: ci(1.1, 0.8, 1.4),
  wave_speed_kmh: NO_OBS,
  wave_amplitude_ms: ci(4.2, 3.7, 4.7),
  n_travel_time_veh: ci(1420, 1398, 1442),
};

function metricsOut(id: string, aggregate: Record<string, AggregateStat>, tier = 'micro'): unknown {
  return {
    run_id: id,
    config_hash: RUNS[id].config_hash,
    tier,
    seeded: false,
    n_replicates: 20,
    underpowered: false,
    replicates: SEEDS.map((seed) => ({ seed, metrics: { throughput_veh_h: aggregate.throughput_veh_h?.mean ?? null } })),
    aggregate,
    fd_source: tier === 'macro' ? 'v1_legacy preset' : null,
    merge_diagnostics: null,
    insertion: null,
    weave_exits: null,
  };
}

const METRICS: Record<string, unknown> = {
  'run-base': metricsOut('run-base', AGG_BASE),
  'run-p5': metricsOut('run-p5', AGG_P5),
  'run-macro': metricsOut('run-macro', AGG_BASE, 'macro'),
  'run-ring': metricsOut('run-ring', AGG_P5),
  'run-twin': metricsOut('run-twin', AGG_BASE),
  'run-drift': metricsOut('run-drift', { ...AGG_BASE, throughput_veh_h: ci(1702, 1652, 1752) }),
  'run-big-a': metricsOut('run-big-a', AGG_BASE),
  'run-big-b': metricsOut('run-big-b', AGG_P5),
};

function heatmapOut(id: string, field: string, seed: string): unknown {
  return {
    run_id: id,
    config_hash: RUNS[id].config_hash,
    seed,
    field,
    tier: RUNS[id].tier,
    t_bins: [30, 90, 150],
    x_bins: [250, 750],
    values: [
      [30, 31],
      [8, null],
      [29, 30],
    ],
  };
}

const progress = { completed_replicates: 20, total_replicates: 20 };

/** `SweepOut`: the API stamps the sweep's controller on its p=0 cell too. */
const SWEEP = {
  sweep_id: 'swp-1',
  scenario_id: 'scn-corridor',
  status: 'done',
  tier: 'micro',
  error: null,
  created_at: '2026-10-02T00:00:00',
  runs_total: 2,
  runs_done: 2,
  runs_failed: 0,
  cells: [
    { penetration: 0, compliance: 1, controller: 'follower_stopper', strategy: 'none', config_hash: 'b0b0b0b0b0b0', run_id: 'run-base', status: 'done', progress, aggregate: AGG_BASE },
    { penetration: 0.05, compliance: 0.8, controller: 'follower_stopper', strategy: 'none', config_hash: 'b1b1b1b1b1b1', run_id: 'run-p5', status: 'done', progress, aggregate: AGG_P5 },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

/* ------------------------------ harness ------------------------------- */

const calls: string[] = [];

function Where(): JSX.Element {
  const l = useLocation();
  return <div data-testid="where">{`${l.pathname}${l.search}`}</div>;
}

function renderAt(url: string): void {
  render(
    <AppStateProvider>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route
            path="/compare"
            element={
              <>
                <CompareView />
                <Where />
              </>
            }
          />
          <Route path="/runs/:runId" element={<RunDetailView />} />
        </Routes>
      </MemoryRouter>
    </AppStateProvider>,
  );
}

const pickerA = (): HTMLElement => screen.getByRole('group', { name: 'Run A' });
const pickerB = (): HTMLElement => screen.getByRole('group', { name: 'Run B' });

/** The metric table's row for a Metrics field name. */
async function metricRow(key: string): Promise<HTMLElement> {
  const table = await screen.findByRole('table', { name: 'metric comparison' }, { timeout: 4000 });
  await waitFor(() => expect(table.querySelector(`tr[data-metric="${key}"]`)).not.toBeNull());
  return table.querySelector<HTMLElement>(`tr[data-metric="${key}"]`) as HTMLElement;
}

/** A row's cells: [A, B, B − A, B vs A]. */
function cells(row: HTMLElement): HTMLElement[] {
  return Array.from(row.querySelectorAll('td'));
}

beforeEach(() => {
  setOfflineFallback(false);
  clearAuthFailure();
  calls.length = 0;
  failing.clear();
  // jsdom has no canvas: the plots keep their frames and draw nothing
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
      const url = String(input);
      calls.push(url);
      const path = url.replace(/^.*\/api\/v1/, '');
      if (failing.has(path)) return json({ detail: 'store busy' }, 503);
      if (path === '/scenarios') return json([CORRIDOR, RING]);
      if (path === '/runs') return json(Object.values(RUNS));
      if (path === '/sweeps/swp-1') return json(SWEEP);
      const m = path.match(/^\/runs\/([^/?]+)(\/metrics|\/heatmap)?(?:\?(.*))?$/);
      if (m) {
        const [, id, sub, query] = m;
        const run = RUNS[id];
        if (!run) return json({ detail: `run '${id}' not found` }, 404);
        if (sub === undefined) return json(run);
        if (sub === '/metrics') {
          if (id === 'run-err') return json({ detail: 'meta.json unreadable' }, 500);
          return json(METRICS[id]);
        }
        const q = new URLSearchParams(query ?? '');
        // the seed exactly as asked, like the API (no detour through a number)
        const seed = q.get('seed') ?? (run.seeds as string[])[0];
        if (!(run.seeds as string[]).includes(seed)) {
          return json({ detail: `run '${id}' has no replicate for seed ${seed}` }, 404);
        }
        return json(heatmapOut(id, q.get('field') ?? 'speed', seed));
      }
      return json({ detail: `unexpected ${url}` }, 404);
    }),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/* ------------------------------- view --------------------------------- */

describe('CompareView (real API shapes)', () => {
  it('pre-selects both runs from the deep link and says what each ran', async () => {
    renderAt('/compare?a=run-base&b=run-p5');
    expect(within(pickerA()).getByRole('combobox', { name: 'Run A' })).toHaveValue('run-base');
    expect(within(pickerB()).getByRole('combobox', { name: 'Run B' })).toHaveValue('run-p5');

    // B: the sweep cell's controller, penetration and compliance
    const b = within(pickerB());
    expect(await b.findByText('follower_stopper', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(b.getByText('p 5% · c 80%')).toBeInTheDocument();
    expect(b.getByText(/from sweep swp-1, this run’s cell/)).toBeInTheDocument();
    // A: the p=0 cell has no controlled vehicle, whatever controller the
    // API stamped on it
    const a = within(pickerA());
    expect(await a.findByText('none (no controlled vehicles)')).toBeInTheDocument();
    expect(a.queryByText('follower_stopper')).toBeNull();
    // scenario name, seeds and tier
    expect(a.getByText('corridor_10km')).toBeInTheDocument();
    expect(a.getByText('20 · 2000–2019')).toBeInTheDocument();
    expect(a.getByText('MICRO')).toBeInTheDocument();
    expect(a.getByRole('link', { name: 'run-base' })).toHaveAttribute('href', '/runs/run-base');

    // both fields, one shared legend, the same seed named
    const plots = await screen.findAllByRole('img', { name: /^Space–time speed field/ }, { timeout: 4000 });
    expect(plots).toHaveLength(2);
    expect(screen.getAllByText('wave threshold 40 km/h (default)')).toHaveLength(1);
    // one scenario, both micro: the shared seed is one demand realisation
    expect(screen.getByText(/Both fields are seed 2000/)).toHaveTextContent(
      'Both fields are seed 2000: one replicate each, the same demand realisation in both runs, not a mean over the replicates.',
    );
    expect(screen.getByRole('button', { name: 'Download CSV of run A' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Download CSV of run B' })).toBeEnabled();
    // the runs share their first seed, so no seed is named in the request
    expect(calls.filter((u) => u.includes('/heatmap')).every((u) => !u.includes('seed='))).toBe(true);
  });

  it('computes B − A and B vs A against hand-computed values', async () => {
    renderAt('/compare?a=run-base&b=run-p5');

    // throughput: 1785 − 1700 = +85 veh/h; 85 / 1700 = +5.0 %
    const tp = cells(await metricRow('throughput_veh_h'));
    expect(within(tp[0]).getByText('1700')).toBeInTheDocument();
    expect(within(tp[0]).getByText('95% CI 1650 – 1750 · n=20')).toBeInTheDocument();
    expect(within(tp[1]).getByText('1785')).toBeInTheDocument();
    expect(tp[2]).toHaveTextContent(/^\+85$/);
    expect(tp[3]).toHaveTextContent(/^\+5\.0%$/);

    // σ_v spatial: 2.90 − 5.80 = −2.90 m/s; −2.90 / 5.80 = −50.0 %
    const sv = cells(await metricRow('sigma_v_spatial_ms'));
    expect(within(sv[0]).getByText('5.80')).toBeInTheDocument();
    expect(within(sv[1]).getByText('2.90')).toBeInTheDocument();
    expect(sv[2]).toHaveTextContent(/^-2\.90$/);
    expect(sv[3]).toHaveTextContent(/^-50\.0%$/);

    // mean travel time: 498.1 − 512.4 = −14.3 s; −14.3 / 512.4 = −2.79 % → −2.8 %
    const tt = cells(await metricRow('mean_tt_s'));
    expect(tt[2]).toHaveTextContent(/^-14\.3$/);
    expect(tt[3]).toHaveTextContent(/^-2\.8%$/);

    // the note says what the column is and that no interval is pooled
    expect(screen.getByText(/B − A is the difference of the two runs’ means/)).toHaveTextContent(
      /are not pooled into one/,
    );
  });

  it('marks a metric a run did not record as "not recorded", never as 0', async () => {
    renderAt('/compare?a=run-base&b=run-p5');
    const fuel = cells(await metricRow('fuel_ml_per_veh_km'));
    expect(within(fuel[0]).getByText('61.2')).toBeInTheDocument();
    expect(within(fuel[1]).getByText('not recorded')).toBeInTheDocument();
    expect(fuel[1]).not.toHaveTextContent(/\b0(\.0)?\b/);
    // no difference against a missing value, and the reason is spoken
    expect(within(fuel[2]).getByText('No difference: B did not record it.')).toBeInTheDocument();
    expect(fuel[2]).not.toHaveTextContent(/[0-9]/);

    // no replicate produced a wave speed in B: no observations, n=0
    const ws = cells(await metricRow('wave_speed_kmh'));
    expect(within(ws[1]).getByText('no observations')).toBeInTheDocument();
    expect(within(ws[1]).getByText('n=0')).toBeInTheDocument();
    expect(within(ws[2]).getByText('No difference: B has no observations.')).toBeInTheDocument();
  });

  it('warns when the tiers and the config hashes differ, without blocking', async () => {
    renderAt('/compare?a=run-base&b=run-macro');
    const tier = await screen.findByText(
      /Different tiers: A is a micro \(SUMO\) run and B is a macro \(CTM screening\) run/,
      {},
      { timeout: 4000 },
    );
    expect(tier.closest('[data-note]')).toHaveAttribute('data-note', 'tier');
    expect(screen.getByText(/Config hashes differ \(A b0b0…b0b0, B ma00…0001\)/)).toBeInTheDocument();
    expect(within(pickerB()).getByText('MACRO SCREENING')).toBeInTheDocument();
    // B was launched with overrides: its controller is not claimed
    expect(within(pickerB()).getAllByText('not reported')).toHaveLength(2);
    expect(
      within(pickerB()).getByText(/config hash differs from scenario corridor_10km’s/),
    ).toBeInTheDocument();
    // the comparison still renders
    const tp = cells(await metricRow('throughput_veh_h'));
    expect(tp[2]).toHaveTextContent(/^0$/);
  });

  it('warns when the scenarios differ, and reads a stored run’s setup from its scenario', async () => {
    renderAt('/compare?a=run-base&b=run-ring');
    expect(
      await screen.findByText(/Different scenarios: A runs corridor_10km and B runs ring_sugiyama/, {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    // same hash as its stored scenario: no overrides, so the scenario's
    // controller is the run's
    const b = within(pickerB());
    expect(await b.findByText('follower_stopper')).toBeInTheDocument();
    expect(b.getByText(/from scenario ring_sugiyama: same config hash/)).toBeInTheDocument();
  });

  it('offers finished runs only, and choosing B updates the link', async () => {
    renderAt('/compare?a=run-base');
    expect(await screen.findByText('Run B is not chosen yet.', {}, { timeout: 4000 })).toBeInTheDocument();
    const selectB = within(pickerB()).getByRole('combobox', { name: 'Run B' });
    await waitFor(() => expect(within(selectB).getAllByRole('option').length).toBeGreaterThan(1));
    const values = within(selectB)
      .getAllByRole('option')
      .map((o) => (o as HTMLOptionElement).value);
    expect(values).toContain('run-p5');
    expect(values).not.toContain('run-going');
    // the option names what the run is
    expect(within(selectB).getByRole('option', { name: /^run-p5 · corridor_10km · micro · 20 reps · b1b1…b1b1$/ }))
      .toBeInTheDocument();

    fireEvent.change(selectB, { target: { value: 'run-p5' } });
    expect(screen.getByTestId('where')).toHaveTextContent('/compare?a=run-base&b=run-p5');
    const tp = cells(await metricRow('throughput_veh_h'));
    expect(tp[2]).toHaveTextContent('+85');
  });

  it('swaps A and B, which flips the sign of B − A', async () => {
    renderAt('/compare?a=run-base&b=run-p5');
    expect(cells(await metricRow('throughput_veh_h'))[2]).toHaveTextContent('+85');
    fireEvent.click(screen.getByRole('button', { name: 'Swap A and B' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/compare?a=run-p5&b=run-base');
    const row = (): HTMLElement =>
      document.querySelector<HTMLElement>('tr[data-metric="throughput_veh_h"]') as HTMLElement;
    await waitFor(() => expect(cells(row())[2]).toHaveTextContent(/^-85$/));
    // −85 / 1785 = −4.76 %
    expect(cells(row())[3]).toHaveTextContent(/^-4\.8%$/);
  });

  it('says a deep-linked run is not finished instead of comparing it', async () => {
    renderAt('/compare?a=run-base&b=run-going');
    expect(await screen.findByText('Run B (run-going) is running.', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'metric comparison' })).toBeNull();
    expect(await within(pickerB()).findByRole('option', { name: /^run-going .* — running$/ })).toBeInTheDocument();
  });

  it('shows a failed metrics read in the service’s words, with Retry', async () => {
    renderAt('/compare?a=run-base&b=run-err');
    expect(
      await screen.findByText('The metrics of run B (run-err) could not be loaded.', {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(screen.getByText('HTTP 500 — meta.json unreadable')).toBeInTheDocument();
    const before = calls.filter((u) => u.endsWith('/runs/run-err/metrics')).length;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() =>
      expect(calls.filter((u) => u.endsWith('/runs/run-err/metrics')).length).toBe(before + 1),
    );
  });

  it('asks for two runs when none is chosen', async () => {
    renderAt('/compare');
    expect(await screen.findByText('No runs to compare yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Swap A and B' })).toBeDisabled();
  });

  it('has no serious or critical accessibility violations once loaded', async () => {
    renderAt('/compare?a=run-base&b=run-p5');
    await screen.findAllByRole('img', { name: /^Space–time speed field/ }, { timeout: 4000 });
    await metricRow('throughput_veh_h');
    await within(pickerB()).findByText('follower_stopper');
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull());
    expect(formatViolations(auditA11y(document.body))).toEqual([]);
  });

  it('is reachable from a finished run’s detail page as "Compare with…"', async () => {
    renderAt('/runs/run-base');
    const link = await screen.findByRole('link', { name: /Compare with…/ }, { timeout: 4000 });
    expect(link).toHaveAttribute('href', '/compare?a=run-base');
  });
});

describe('CompareView seeds, notes and re-reads', () => {
  const heatmapCalls = (id: string): string[] =>
    calls.filter((u) => u.includes(`/runs/${id}/heatmap`));

  it('shows a 64-bit seed exactly, and asks for exactly that seed', async () => {
    renderAt('/compare?a=run-big-a&b=run-big-b');
    // the seed lists, digit for digit (consecutive by BigInt, not by double)
    expect(
      await within(pickerA()).findByText('2 · 6914975401685141156–6914975401685141157', {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(within(pickerB()).getByText('2')).toHaveAttribute(
      'title',
      '134183728835869882 · 6914975401685141156',
    );

    // both fields are the shared seed: A's default (its first), B's second
    await screen.findAllByRole('img', { name: /^Space–time speed field/ }, { timeout: 4000 });
    const heads = Array.from(document.querySelectorAll('.compare-heat-seed')).map((e) => e.textContent);
    expect(heads).toEqual([
      'seed 6914975401685141156 · replicate 1 of 2',
      'seed 6914975401685141156 · replicate 2 of 2',
    ]);
    expect(screen.getByText(/^Both fields are seed 6914975401685141156:/)).toBeInTheDocument();

    // A's field is its default replicate; B's is asked for by the exact string
    expect(heatmapCalls('run-big-a').every((u) => !u.includes('seed='))).toBe(true);
    expect(heatmapCalls('run-big-b')).toEqual([
      expect.stringMatching(/\/runs\/run-big-b\/heatmap\?field=speed&seed=6914975401685141156$/),
    ]);
    // never the rounded double (6914975401685141000 as JSON.parse prints it)
    expect(calls.some((u) => /69149754016851410{3}|6914975401685141504/.test(u))).toBe(false);
  });

  it('lists a 64-bit seed exactly on Run detail', async () => {
    renderAt('/runs/run-big-a');
    fireEvent.click(await screen.findByRole('button', { name: 'Seeds: 2' }, { timeout: 4000 }));
    expect(screen.getByText('6914975401685141156 · 6914975401685141157')).toBeInTheDocument();
    // its field is the first replicate, named by the seed the API sent
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Download CSV/ })).toHaveAttribute(
        'title',
        expect.stringContaining('(seed 6914975401685141156)'),
      ),
    );
  });

  it('says the same config hash should give exactly zero, and warns when it does not', async () => {
    renderAt('/compare?a=run-base&b=run-twin');
    const same = await screen.findByText(/^Same config hash \(b0b0…b0b0\)\./, {}, { timeout: 4000 });
    await metricRow('throughput_veh_h');
    expect(same).toHaveTextContent(
      'The same configuration ran on the same replicate seeds, so B − A should be exactly zero; ' +
        'a nonzero difference is not sampling noise',
    );
    expect(same).not.toHaveTextContent(/replicate noise/);
    // every B − A is zero: an info note, not a warning
    expect(same.closest('[data-note]')).toHaveAttribute('data-note', 'hash-same');
    expect(screen.queryByText('What else differs between A and B')).toBeNull();
  });

  it('turns the same-hash note into a warning when a B − A is not zero', async () => {
    renderAt('/compare?a=run-base&b=run-drift');
    // throughput 1702 − 1700 = +2: impossible on the same seeds
    expect(cells(await metricRow('throughput_veh_h'))[2]).toHaveTextContent(/^\+2$/);
    const warning = await screen.findByText(/^Same config hash \(b0b0…b0b0\), yet B − A is not zero \(throughput\)\./);
    expect(warning).toHaveTextContent(/not sampling noise — the code or SUMO version, or a file the config references, changed/);
    expect(warning.closest('[data-note]')).toHaveAttribute('data-note', 'hash-same');
    expect(screen.getByText('What else differs between A and B')).toBeInTheDocument();
  });

  it('claims one demand realisation only for one scenario on the micro tier', async () => {
    // another scenario, same seeds
    renderAt('/compare?a=run-base&b=run-ring');
    const note = await screen.findByText(/^Both fields are seed 2000:/, {}, { timeout: 4000 });
    expect(note).toHaveTextContent(/^Both fields are seed 2000: one replicate each, not a mean\.$/);
    expect(screen.queryByText(/demand realisation/)).toBeNull();
  });

  it('claims no demand realisation across tiers either', async () => {
    renderAt('/compare?a=run-base&b=run-macro');
    const note = await screen.findByText(/^Both fields are seed 2000:/, {}, { timeout: 4000 });
    expect(note).toHaveTextContent(/^Both fields are seed 2000: one replicate each, not a mean\.$/);
  });

  it('reads everything again when the API comes back, dropping what the demo backend answered', async () => {
    // offline: every read goes to the in-browser demo backend
    setOfflineFallback(true);
    renderAt('/compare?a=run-base&b=run-8f2c11');
    expect(
      await screen.findByText(/The API is unreachable, so these are built-in demo runs/, {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    // the demo backend has no run-base, and does have a demo run-8f2c11
    expect(
      await within(pickerA()).findByText('Run A (run-base) could not be loaded.', {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(await within(pickerB()).findByText('DEMO', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(calls).toEqual([]);

    act(() => setOfflineFallback(false));

    // the lists and both runs are read from the server, without a Retry
    await waitFor(() => expect(calls.some((u) => u.endsWith('/api/v1/runs'))).toBe(true), { timeout: 4000 });
    expect(calls.some((u) => u.endsWith('/api/v1/scenarios'))).toBe(true);
    await waitFor(() =>
      expect(screen.queryByText(/The API is unreachable, so these are built-in demo runs/)).toBeNull(),
    );
    expect(await within(pickerA()).findByText('20 · 2000–2019', {}, { timeout: 4000 })).toBeInTheDocument();
    // the demo run is not this server's: its DEMO facts are gone, not kept
    expect(
      await within(pickerB()).findByText('Run B (run-8f2c11) could not be loaded.', {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(within(pickerB()).queryByText('DEMO')).toBeNull();
    expect(calls.filter((u) => u.endsWith('/runs/run-base'))).toHaveLength(1);
  });

  it('reads again, exactly once, on Retry after a failed Reload', async () => {
    renderAt('/compare?a=run-base&b=run-going');
    expect(await screen.findByText('Run B (run-going) is running.', {}, { timeout: 4000 })).toBeInTheDocument();
    const reads = (): number => calls.filter((u) => u.endsWith('/runs/run-going')).length;

    // Reload fails: the run read before stays, under an error with Retry
    failing.add('/runs/run-going');
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(
      await within(pickerB()).findByText('Run B (run-going) could not be loaded.', {}, { timeout: 4000 }),
    ).toBeInTheDocument();

    failing.delete('/runs/run-going');
    const before = reads();
    fireEvent.click(within(pickerB()).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(reads()).toBe(before + 1));
    // the answer lands (the facts are back) and no second read went out
    expect(await within(pickerB()).findByRole('link', { name: 'run-going' })).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    expect(reads()).toBe(before + 1);
    expect(within(pickerB()).queryByText('Run B (run-going) could not be loaded.')).toBeNull();
  });
});

/* ------------------------------- logic -------------------------------- */

describe('lib/compare', () => {
  it('differences two means, with the ratio to |A| only when A is not 0', () => {
    const d = metricDifference(sideStat({ x: ci(-4, -5, -3) }, 'x'), sideStat({ x: ci(-3, -4, -2) }, 'x'));
    // (−3) − (−4) = +1; 1 / |−4| = +25 %
    expect(d).toEqual({ kind: 'difference', diff: 1, rel: 0.25 });
    expect(metricDifference(sideStat({ x: ci(0, 0, 0) }, 'x'), sideStat({ x: ci(2, 1, 3) }, 'x'))).toEqual({
      kind: 'difference',
      diff: 2,
      rel: null,
    });
    expect(metricDifference(sideStat({}, 'x'), sideStat({ x: NO_OBS }, 'x'))).toEqual({
      kind: 'none',
      reason: 'No difference: A did not record it and B has no observations.',
    });
  });

  it('prints a sign only on a difference that does not round to zero', () => {
    expect(formatSigned(85, 0)).toBe('+85');
    expect(formatSigned(-2.9, 2)).toBe('-2.90');
    expect(formatSigned(0.001, 1)).toBe('0.0');
    expect(formatSigned(-0.001, 1)).toBe('0.0');
  });

  it('lists the headline metrics even when neither run recorded them', () => {
    const keys = compareSections({}, {}).flatMap((s) => s.keys);
    expect(keys).toEqual(
      expect.arrayContaining(['throughput_veh_h', 'mean_tt_s', 'sigma_v_spatial_ms', 'fuel_ml_per_veh_km', 'wave_count', 'wave_amplitude_ms']),
    );
  });

  it('reads a run’s setup from its sweep cell, its scenario, or says it cannot', () => {
    const sweep = SWEEP as unknown as SweepDetail;
    const corridor = CORRIDOR as unknown as ScenarioSummary;
    const base = resolveRunSetup(RUNS['run-base'] as unknown as RunDetail, corridor, sweep);
    expect(base.known && describeController(base.setup)).toBe('none (no controlled vehicles)');
    const p5 = resolveRunSetup(RUNS['run-p5'] as unknown as RunDetail, corridor, sweep);
    expect(p5.known && describePenComp(p5.setup)).toBe('p 5% · c 80%');
    // still reading the sweep, and a failed sweep read
    expect(resolveRunSetup(RUNS['run-p5'] as unknown as RunDetail, corridor, undefined).known).toBe(false);
    const failed = resolveRunSetup(RUNS['run-p5'] as unknown as RunDetail, corridor, null);
    expect(!failed.known && failed.reason).toMatch(/could not be read/);
    // overrides: only the scenario's default is known
    const macro = resolveRunSetup(RUNS['run-macro'] as unknown as RunDetail, corridor, undefined);
    expect(macro.known).toBe(false);
    expect(!macro.known && macro.scenarioDefault?.controller).toBe(null);
  });

  it('picks the first seed both runs ran, and describes seed lists', () => {
    expect(sharedSeed(['5', '6', '7'], ['7', '6'])).toBe('6');
    expect(sharedSeed(['1', '2'], ['3'])).toBeNull();
    expect(describeSeeds(SEEDS)).toBe('20 · 2000–2019');
    expect(describeSeeds(['3', '9'])).toBe('2');
  });

  it('keeps 64-bit seeds exact: compared as strings, counted with BigInt', () => {
    // all three are one double: Number() would call them the same seed
    const [s0, s1, s2] = ['6914975401685141156', '6914975401685141157', '6914975401685141158'];
    expect(Number(s0)).toBe(Number(s1));
    expect(sharedSeed([s0], [s1])).toBeNull();
    expect(sharedSeed([s1, s0], [s0])).toBe(s0);
    expect(describeSeeds([s0, s1, s2])).toBe(`3 · ${s0}–${s2}`);
    expect(describeSeeds([s0, s2])).toBe('2');
    expect(describeSeeds([s0])).toBe(`1 · ${s0}`);
    // not a decimal seed: never called consecutive
    expect(describeSeeds(['x', 'y'])).toBe('2');
  });

  it('expects exactly zero from one config hash, and warns on any nonzero B − A', () => {
    const side = (id: string) => ({
      run: RUNS[id] as unknown as RunDetail,
      scenarioName: 'corridor_10km',
      demo: false,
    });
    const drift = { ...AGG_BASE, mean_tt_s: ci(512.5, 505.1, 519.9) };
    expect(nonzeroDifferences(AGG_BASE, AGG_BASE)).toEqual([]);
    expect(nonzeroDifferences(AGG_BASE, drift)).toEqual(['mean_tt_s']);

    // metrics not read yet, or all zero: the info note says what zero means
    for (const aggregates of [{}, { a: AGG_BASE, b: AGG_BASE }]) {
      const [note] = compareNotes(side('run-base'), side('run-twin'), aggregates);
      expect(note.id).toBe('hash-same');
      expect(note.tone).toBe('info');
      expect(note.text).toMatch(/same replicate seeds, so B − A should be exactly zero/);
      expect(note.text).toMatch(/not sampling noise/);
    }
    // one mean differs: a warning naming it
    const [note] = compareNotes(side('run-base'), side('run-twin'), { a: AGG_BASE, b: drift });
    expect(note).toMatchObject({ id: 'hash-same', tone: 'warning' });
    expect(note.text).toMatch(/^Same config hash \(b0b0…b0b0\), yet B − A is not zero \(mean travel time\)\./);
  });

  it('calls one seed one demand realisation only within a scenario on the micro tier', () => {
    const side = (id: string) => ({
      run: RUNS[id] as unknown as RunDetail,
      scenarioName: 'x',
      demo: false,
    });
    expect(sameDemandRealisation(side('run-base'), side('run-p5'))).toBe(true);
    expect(sameDemandRealisation(side('run-base'), side('run-ring'))).toBe(false);
    expect(sameDemandRealisation(side('run-base'), side('run-macro'))).toBe(false);
    expect(sameDemandRealisation(side('run-macro'), side('run-macro'))).toBe(false);
  });

  it('notes the same run, and skips hash notes for demo rows', () => {
    const side = (id: string, demo = false) => ({
      run: RUNS[id] as unknown as RunDetail,
      scenarioName: 'corridor_10km',
      demo,
    });
    expect(compareNotes(side('run-base'), side('run-base')).map((n) => n.id)).toEqual(['same-run']);
    expect(compareNotes(side('run-base'), side('run-p5')).map((n) => n.id)).toEqual(['hash-differs']);
    expect(compareNotes(side('run-base', true), side('run-p5', true))).toEqual([]);
  });

  it('shares axes over the union of two fields', () => {
    const h = (t: number[], x: number[]): Heatmap => ({
      t_bins: t,
      x_bins: x,
      values: t.map(() => x.map(() => 1)),
    });
    const u = unionExtent([h([30, 90], [250, 750]), h([30, 90, 150], [250, 750])]);
    expect(u).toEqual({ t0: 0, t1: 180, x0: 0, x1: 1000 });
    expect(sameExtent(u!, { t0: 0, t1: 180, x0: 0, x1: 1000 })).toBe(true);
    expect(sameExtent(u!, { t0: 0, t1: 120, x0: 0, x1: 1000 })).toBe(false);
  });
});
