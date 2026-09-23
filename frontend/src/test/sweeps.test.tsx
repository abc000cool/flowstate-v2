/** SweepsView against the real API's shapes: the POST /sweeps body carries
 * `controllers` (a list) plus `include_baseline`, and the matrix reads
 * aggregates keyed by the validation.metrics.Metrics field names. The
 * launcher costs real compute, so it must state the arithmetic and take a
 * second click; identical realisations must be flagged, not read as effects. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, OFFLINE_WRITE_MESSAGE, setOfflineFallback } from '../api/client';
import { Toasts } from '../components/toast';
import { SweepsView } from '../views/SweepsView';

const scenario = {
  scenario_id: 'scn-corridor',
  name: 'corridor_10km',
  config_hash: 'c0ffee',
  created_at: 't',
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

function ci(mean: number, n = 20) {
  return { mean, lo95: mean * 0.95, hi95: mean * 1.05, n, underpowered: n < 20, reason: null };
}

/** The API's third CIOut state: no replicate produced the metric at all —
 * null mean and bounds, n=0, `underpowered` false (there is no estimate to be
 * underpowered about) and the reason spelled out. */
function noObs() {
  return { mean: null, lo95: null, hi95: null, n: 0, underpowered: false, reason: 'no_observations' };
}

/** Aggregate keyed exactly as the API keys it: dataclasses.fields(Metrics). */
function aggregate(sigma: number, throughput: number) {
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

const progress = { completed_replicates: 20, total_replicates: 20 };

/** A SweepOut with the baseline cell the API appends for include_baseline
 * (penetration 0, compliance 1.0, controller = the sweep controller — one per
 * distinct controller), one finished grid cell and one cell the fan-out job
 * has not reached yet (null run/status). */
const sweepOut = {
  sweep_id: 'swp-1',
  scenario_id: 'scn-corridor',
  status: 'running',
  tier: 'micro',
  error: null,
  created_at: '2026-09-16T00:00:00',
  runs_total: 3,
  runs_done: 2,
  runs_failed: 0,
  cells: [
    { penetration: 0, compliance: 1.0, controller: 'follower_stopper', config_hash: 'b0', run_id: 'run-base', status: 'done', progress, aggregate: aggregate(5.8, 1700) },
    { penetration: 0.05, compliance: 0.8, controller: 'follower_stopper', config_hash: 'b1', run_id: 'run-p5', status: 'done', progress, aggregate: aggregate(2.9, 1785) },
    { penetration: 0.1, compliance: 0.8, controller: 'follower_stopper', config_hash: 'b2', run_id: null, status: null, progress: null, aggregate: null },
  ],
};

/** A sweep whose fan-out job failed before creating any child run: the API
 * reports `status: failed` with the error, and the cells stay run-less. */
const failedSweepOut = {
  ...sweepOut,
  sweep_id: 'swp-failed',
  status: 'failed',
  error: 'scenario scn-corridor vanished during fan-out',
  runs_done: 0,
  cells: sweepOut.cells.map((c) => ({ ...c, run_id: null, status: null, progress: null, aggregate: null })),
};

/** Two grid cells with different config hashes and a bit-identical aggregate
 * vector — the shape the walkthrough hit, where compliance looked irrelevant
 * because both cells were the same realisation. */
const twinSweepOut = {
  ...sweepOut,
  sweep_id: 'swp-twins',
  status: 'done',
  cells: [
    { penetration: 0, compliance: 1.0, controller: 'follower_stopper', config_hash: 'b0', run_id: 'run-base', status: 'done', progress, aggregate: aggregate(5.8, 1700) },
    { penetration: 0.05, compliance: 0.8, controller: 'follower_stopper', config_hash: 'c1', run_id: 'run-a', status: 'done', progress, aggregate: aggregate(2.9, 1785) },
    { penetration: 0.05, compliance: 1.0, controller: 'follower_stopper', config_hash: 'c2', run_id: 'run-b', status: 'done', progress, aggregate: aggregate(2.9, 1785) },
  ],
};

/** A sweep in which no wave was detected in any replicate of either cell, so
 * the wave metrics carry no observation (n=0 + reason) rather than a zero. */
const noObsSweepOut = {
  ...sweepOut,
  sweep_id: 'swp-noobs',
  status: 'done',
  cells: [
    {
      penetration: 0,
      compliance: 1.0,
      controller: 'follower_stopper',
      config_hash: 'b0',
      run_id: 'run-base',
      status: 'done',
      progress,
      aggregate: { ...aggregate(5.8, 1700), wave_count: ci(0), wave_amplitude_ms: noObs(), wave_speed_kmh: noObs() },
    },
    {
      penetration: 0.05,
      compliance: 0.8,
      controller: 'follower_stopper',
      config_hash: 'c1',
      run_id: 'run-p5',
      status: 'done',
      progress,
      aggregate: { ...aggregate(2.9, 1785), wave_count: ci(0), wave_amplitude_ms: noObs(), wave_speed_kmh: noObs() },
    },
  ],
};

/** A grid with the infrastructure axis: the baseline, the same VSL deployment
 * with and without controlled vehicles, and the controlled cell alone. A
 * penetration is no longer one row — 5 % under VSL and 5 % without it are two
 * configurations, and the matrix has to say which is which. */
const strategySweepOut = {
  ...sweepOut,
  sweep_id: 'swp-strat',
  status: 'done',
  runs_total: 4,
  runs_done: 4,
  cells: [
    { penetration: 0, compliance: 1.0, controller: null, strategy: 'none', config_hash: 'b0', run_id: 'run-base', status: 'done', progress, aggregate: aggregate(5.8, 1700) },
    { penetration: 0, compliance: 1.0, controller: null, strategy: 'vsl', config_hash: 'i1', run_id: 'run-vsl', status: 'done', progress, aggregate: aggregate(5.22, 1720) },
    { penetration: 0.05, compliance: 0.8, controller: 'follower_stopper', strategy: 'none', config_hash: 'c1', run_id: 'run-p5', status: 'done', progress, aggregate: aggregate(2.9, 1785) },
    { penetration: 0.05, compliance: 0.8, controller: 'follower_stopper', strategy: 'vsl', config_hash: 'c2', run_id: 'run-p5-vsl', status: 'done', progress, aggregate: aggregate(2.32, 1800) },
  ],
};

/** A screening-tier grid: the API reports `tier: macro` on the sweep itself,
 * and every delta in it is a first-order CTM comparison, not evidence. */
const macroSweepOut = {
  ...sweepOut,
  sweep_id: 'swp-macro',
  status: 'done',
  tier: 'macro',
};

/** Two cells the API gave the *same* config hash (a p=0 pair differs in no
 * modelled parameter, so one configuration is run twice): identical numbers
 * there are the expected result and must not be flagged as a finding. */
const sameConfigSweepOut = {
  ...sweepOut,
  sweep_id: 'swp-same',
  status: 'done',
  cells: [
    { penetration: 0, compliance: 0.8, controller: 'follower_stopper', config_hash: 'b0', run_id: 'run-base-a', status: 'done', progress, aggregate: aggregate(5.8, 1700) },
    { penetration: 0, compliance: 1.0, controller: 'follower_stopper', config_hash: 'b0', run_id: 'run-base-b', status: 'done', progress, aggregate: aggregate(5.8, 1700) },
    { penetration: 0.05, compliance: 0.8, controller: 'follower_stopper', config_hash: 'g1', run_id: 'run-g1', status: 'done', progress, aggregate: aggregate(2.9, 1785) },
    { penetration: 0.05, compliance: 1.0, controller: 'follower_stopper', config_hash: 'g2', run_id: 'run-g2', status: 'done', progress, aggregate: aggregate(3.1, 1790) },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

interface Call {
  url: string;
  method: string;
  body?: unknown;
}

describe('SweepsView (real API shapes)', () => {
  const calls: Call[] = [];

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    calls.length = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        const method = init?.method ?? 'GET';
        const body = init?.body ? (JSON.parse(String(init.body)) as unknown) : undefined;
        calls.push({ url, method, body });
        if (url.endsWith('/scenarios') && method === 'GET') return json([scenario]);
        if (url.endsWith('/sweeps') && method === 'POST') return json(sweepOut, 202);
        if (url.endsWith('/sweeps/swp-1')) return json(sweepOut);
        if (url.endsWith('/sweeps/swp-failed')) return json(failedSweepOut);
        if (url.endsWith('/sweeps/swp-twins')) return json(twinSweepOut);
        if (url.endsWith('/sweeps/swp-same')) return json(sameConfigSweepOut);
        if (url.endsWith('/sweeps/swp-noobs')) return json(noObsSweepOut);
        if (url.endsWith('/sweeps/swp-macro')) return json(macroSweepOut);
        if (url.endsWith('/sweeps/swp-strat')) return json(strategySweepOut);
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders deltas from Metrics-keyed aggregates and labels the baseline cell', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps?sweep=swp-1']}>
        <SweepsView />
      </MemoryRouter>,
    );
    // default metric is sigma_v_spatial_ms: (2.9 - 5.8) / 5.8 = -50%
    expect(await screen.findByText('-50.0%', {}, { timeout: 4000 })).toBeInTheDocument();
    // the done cell reports its replicate count, not the '—' of a missing key
    expect(screen.getByText('n=20')).toBeInTheDocument();
    // the header must not credit a controller to a cell with no controlled
    // vehicles, even though the API stamps the sweep's controller on it
    expect(screen.getByText(/no controlled vehicles/)).toBeInTheDocument();
    expect(screen.queryByText(/controller follower_stopper/)).toBeNull();
    // the p=0 cell is labelled as the baseline and shows its absolute value
    expect(screen.getByText('0% · baseline')).toBeInTheDocument();
    expect(screen.getByText('BASELINE · n=20')).toBeInTheDocument();
    expect(screen.getByText('5.80 m/s')).toBeInTheDocument();
    // a cell the fan-out has not reached (null status) reads as queued
    expect(screen.getByText('queued')).toBeInTheDocument();
    // switching metric re-reads the aggregate under the real key
    fireEvent.change(screen.getByLabelText('Metric'), { target: { value: 'throughput_veh_h' } });
    expect(await screen.findByText('+5.0%')).toBeInTheDocument();
    expect(screen.getByText('1700 veh/h')).toBeInTheDocument();
  });

  it('surfaces a failed fan-out and stops polling it', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps?sweep=swp-failed']}>
        <SweepsView />
      </MemoryRouter>,
    );
    expect(
      await screen.findByText(/sweep fan-out failed: scenario scn-corridor vanished during fan-out/, {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    // run-less cells read as queued rather than as a delta
    expect(screen.getAllByText('queued').length).toBeGreaterThan(0);
    // the fan-out is terminal: no further GET /sweeps/swp-failed after the first
    const polls = () => calls.filter((c) => c.url.endsWith('/sweeps/swp-failed')).length;
    expect(polls()).toBe(1);
    // wait past one SWEEP_POLL_MS (2500 ms) tick
    await new Promise((r) => setTimeout(r, 2800));
    expect(polls()).toBe(1);
  }, 10000);

  it('flags cells whose aggregates are a bit-identical realisation', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps?sweep=swp-twins']}>
        <SweepsView />
      </MemoryRouter>,
    );
    expect(
      await screen.findByText(/cells share an identical aggregate vector/, {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    // both twins carry the marker; the differently-valued baseline does not
    expect(screen.getAllByTitle(/Identical realisation/).length).toBe(2);
  });

  it('does not flag two cells that share a config hash as a surprise', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps?sweep=swp-same']}>
        <SweepsView />
      </MemoryRouter>,
    );
    // the matrix is up (the grid cell's delta against the baseline)
    expect(await screen.findByText('-50.0%', {}, { timeout: 4000 })).toBeInTheDocument();
    // same configuration run twice: identical aggregates are expected there
    expect(screen.queryAllByTitle(/Identical realisation/).length).toBe(0);
    expect(screen.queryByText(/share an identical aggregate vector/)).toBeNull();
  });

  it('says "no observations" where the API reports none, instead of a delta', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps?sweep=swp-noobs']}>
        <SweepsView />
      </MemoryRouter>,
    );
    // sigma_v is measured in both cells, so the matrix starts with a delta
    expect(await screen.findByText('-50.0%', {}, { timeout: 4000 })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Metric'), { target: { value: 'wave_amplitude_ms' } });
    // both the baseline reference and the grid cell say it in words: n=0 is
    // not a measured zero and not an underpowered estimate
    const labels = await screen.findAllByText('no observations');
    expect(labels.length).toBe(2);
    expect(screen.queryByText('-50.0%')).toBeNull();
    expect(screen.getByText('BASELINE · n=0')).toBeInTheDocument();
  });

  it('confirms the run count before launching, with a controllers list and include_baseline', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps']}>
        <SweepsView />
      </MemoryRouter>,
    );
    await screen.findByRole('option', { name: 'corridor_10km' }, { timeout: 4000 });
    // the default grid is exploratory, not 24 cells x 20 replicates
    expect(screen.getByLabelText('Replicates / cell')).toHaveValue(5);

    fireEvent.click(screen.getByRole('button', { name: /^Launch/ }));
    // nothing is enqueued until the size of the request is acknowledged
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    const dialog = await screen.findByRole('dialog', { name: 'Launch this sweep?' });
    expect(within(dialog).getByText('24 + 1 baseline = 25')).toBeInTheDocument();
    expect(within(dialog).getByText('125')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch 125 runs' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))).toBe(true);
    });
    const first = calls.find((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))!.body as Record<string, unknown>;
    expect(first.controllers).toEqual(['follower_stopper']);
    expect(first).not.toHaveProperty('controller');
    expect(first.include_baseline).toBe(true);
    // the tier is always stated: omitting it ran the grid on whatever tier the
    // stored scenario happened to carry
    expect(first.tier).toBe('micro');
    expect(first).toMatchObject({ scenario_id: 'scn-corridor', replicates: 5 });
    expect(first.penetrations).toEqual([0.01, 0.02, 0.05, 0.1, 0.15, 0.2]);
    expect(first.compliances).toEqual([0.25, 0.5, 0.8, 1.0]);

    // opting out of the baseline cell is sent explicitly, not omitted
    fireEvent.click(screen.getByLabelText('include p=0 baseline cell'));
    fireEvent.click(screen.getByRole('button', { name: /^Launch/ }));
    const secondDialog = await screen.findByRole('dialog', { name: 'Launch this sweep?' });
    fireEvent.click(within(secondDialog).getByRole('button', { name: /^Launch \d+ runs$/ }));
    await waitFor(() => {
      expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/sweeps')).length).toBe(2);
    });
    const second = calls.filter((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))[1].body as Record<string, unknown>;
    expect(second.include_baseline).toBe(false);
  });

  it('sends the selected tier and labels a macro grid as screening, up front', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps']}>
        <SweepsView />
      </MemoryRouter>,
    );
    await screen.findByRole('option', { name: 'corridor_10km' }, { timeout: 4000 });
    // micro by default: no screening label on a grid that is not screening
    expect(screen.queryByText(/Screening tier \(CTM\)/)).toBeNull();

    fireEvent.change(screen.getByLabelText('Tier'), { target: { value: 'macro' } });
    // the label is up before anything is enqueued, and it is text, not a hover
    expect(screen.getAllByText(/Screening tier \(CTM\)/).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /^Launch/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Launch this sweep?' });
    expect(within(dialog).getByText('macro (CTM screening)')).toBeInTheDocument();
    expect(within(dialog).getByText(/Screening tier \(CTM\)/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: /^Launch \d+ runs$/ }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))).toBe(true);
    });
    const body = calls.find((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))!
      .body as Record<string, unknown>;
    expect(body.tier).toBe('macro');
  });

  it('keeps the screening-tier banner on a macro sweep matrix', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps?sweep=swp-macro']}>
        <SweepsView />
      </MemoryRouter>,
    );
    // the matrix is rendered ...
    expect(await screen.findByText('-50.0%', {}, { timeout: 4000 })).toBeInTheDocument();
    // ... and its numbers are labelled screening, from the API's own tier
    const banners = screen.getAllByText(/Screening tier \(CTM\)/);
    expect(banners.length).toBeGreaterThan(0);
    expect(banners[0].textContent).toMatch(/not a validation result/);
    // the full reason is in the banner itself, not behind a hover
    expect(banners[0].closest('p')?.textContent).toMatch(
      /refuses to generate a validation report/,
    );
  });

  it('does not label a micro sweep matrix as screening', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps?sweep=swp-1']}>
        <SweepsView />
      </MemoryRouter>,
    );
    expect(await screen.findByText('-50.0%', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByText(/Screening tier \(CTM\)/)).toBeNull();
  });

  it('names the strategy in every row and cell of the matrix', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps?sweep=swp-strat']}>
        <SweepsView />
      </MemoryRouter>,
    );
    // the controlled cell without infrastructure, and the same cell with it
    expect(await screen.findByText('-50.0%', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText('-60.0%')).toBeInTheDocument();
    // p=0 with VSL is not a second baseline: it is the deployment priced
    // alone, and it carries a delta like any other cell
    expect(screen.getByText('-10.0%')).toBeInTheDocument();
    // one row per (penetration, strategy) pair — a penetration alone no
    // longer identifies a configuration (scoped to the matrix: the launcher
    // has its own "5%" checkboxes)
    const matrix = within(screen.getByRole('table'));
    expect(matrix.getByText('0% · baseline')).toBeInTheDocument();
    expect(matrix.getByText('0% · vsl')).toBeInTheDocument();
    expect(matrix.getByText('5%')).toBeInTheDocument();
    expect(matrix.getByText('5% · vsl')).toBeInTheDocument();
    // and the cell labels say which configuration produced the number
    expect(screen.getAllByText('n=20 · vsl').length).toBe(2);
    expect(screen.getByText('n=20')).toBeInTheDocument();
    // identical realisations are still the only thing flagged as a surprise
    expect(screen.queryByText(/share an identical aggregate vector/)).toBeNull();
  });

  it('sends the strategy axis and the ALINEA target, and asks for neither by default', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps']}>
        <SweepsView />
      </MemoryRouter>,
    );
    await screen.findByRole('option', { name: 'corridor_10km' }, { timeout: 4000 });

    // Default: the scenario as calibrated, stated explicitly like `tier`, and
    // no metering target field to fill in.
    expect(screen.queryByLabelText(/ALINEA target/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Launch/ }));
    const plain = await screen.findByRole('dialog', { name: 'Launch this sweep?' });
    expect(within(plain).getByText('none')).toBeInTheDocument();
    fireEvent.click(within(plain).getByRole('button', { name: /^Launch \d+ runs$/ }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))).toBe(true);
    });
    const first = calls.find((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))!.body as Record<string, unknown>;
    expect(first.strategies).toEqual(['none']);
    expect(first).not.toHaveProperty('alinea');

    // Two strategies: the cell count grows by the product *and* by one
    // uncontrolled cell per strategy, and the metering target appears.
    fireEvent.click(screen.getByLabelText('none'));
    fireEvent.click(screen.getByLabelText('alinea'));
    fireEvent.click(screen.getByLabelText('vsl'));
    const target = screen.getByLabelText('ALINEA target [veh/km/lane]');
    expect(target).toHaveAttribute('placeholder', "from the scenario's FD calibration");
    fireEvent.change(target, { target: { value: '19.9' } });

    fireEvent.click(screen.getByRole('button', { name: /^Launch/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Launch this sweep?' });
    expect(within(dialog).getByText(/vsl, alinea/)).toBeInTheDocument();
    // 6 x 4 x 2 grid cells + 2 infrastructure-only cells + 1 baseline
    expect(within(dialog).getByText('48 + 2 infrastructure + 1 baseline = 51')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: /^Launch \d+ runs$/ }));
    await waitFor(() => {
      expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/sweeps')).length).toBe(2);
    });
    const body = calls.filter((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))[1].body as Record<string, unknown>;
    expect(body.strategies).toEqual(['vsl', 'alinea']); // the API's own order
    expect(body.alinea).toEqual({ rho_target_veh_km: 19.9 });
  });

  it('never invents a metering target: an empty field is sent as no target', async () => {
    render(
      <>
        <Toasts />
        <MemoryRouter initialEntries={['/sweeps']}>
          <SweepsView />
        </MemoryRouter>
      </>,
    );
    await screen.findByRole('option', { name: 'corridor_10km' }, { timeout: 4000 });
    fireEvent.click(screen.getByLabelText('none'));
    fireEvent.click(screen.getByLabelText('vsl+alinea'));

    // a typo is refused here rather than posted as a density
    fireEvent.change(screen.getByLabelText('ALINEA target [veh/km/lane]'), {
      target: { value: 'twenty' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Launch/ }));
    expect(await screen.findByText(/positive number of veh\/km/)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();

    // left empty, the request carries no target and the API reads the
    // scenario's fitted diagram (or refuses)
    fireEvent.change(screen.getByLabelText('ALINEA target [veh/km/lane]'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /^Launch/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Launch this sweep?' });
    expect(within(dialog).getByText(/from the scenario's FD calibration/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: /^Launch \d+ runs$/ }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))).toBe(true);
    });
    const body = calls.find((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))!.body as Record<string, unknown>;
    expect(body.strategies).toEqual(['vsl+alinea']);
    expect(body).not.toHaveProperty('alinea');
  });

  it('cancelling the confirmation enqueues nothing', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps']}>
        <SweepsView />
      </MemoryRouter>,
    );
    await screen.findByRole('option', { name: 'corridor_10km' }, { timeout: 4000 });
    fireEvent.click(screen.getByRole('button', { name: /^Launch/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Launch this sweep?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull();
    });
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });
});

/** A sweep is the most expensive thing the dashboard can enqueue, and the
 * demo backend will happily "launch" one: a whole matrix of in-browser cells
 * that no worker ever ran. `POST /sweeps` therefore never falls back. */
describe('SweepsView with the API offline', () => {
  const calls: Call[] = [];

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    calls.length = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        const method = init?.method ?? 'GET';
        calls.push({ url, method });
        if (url.endsWith('/scenarios') && method === 'GET') return json([scenario]);
        if (url.endsWith('/sweeps') && method === 'POST') return json(sweepOut, 202);
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    setOfflineFallback(false);
    vi.unstubAllGlobals();
  });

  it('refuses a sweep confirmed after the API went away, and shows no demo matrix', async () => {
    render(
      <>
        <Toasts />
        <MemoryRouter initialEntries={['/sweeps']}>
          <SweepsView />
        </MemoryRouter>
      </>,
    );
    await screen.findByRole('option', { name: 'corridor_10km' }, { timeout: 4000 });
    fireEvent.click(screen.getByRole('button', { name: /^Launch/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Launch this sweep?' });

    act(() => setOfflineFallback(true));
    fireEvent.click(within(dialog).getByRole('button', { name: /^Launch \d+ runs$/ }));

    expect(await screen.findByText(/API offline/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    // no in-browser sweep took its place: the results matrix (and its metric
    // picker) never appears
    expect(screen.queryByLabelText('Metric')).toBeNull();

    const launch = screen.getByRole('button', { name: /^Launch \d+ cells/ });
    expect(launch).toBeDisabled();
    expect(launch).toHaveAttribute('title', OFFLINE_WRITE_MESSAGE);
  }, 15000);
});
