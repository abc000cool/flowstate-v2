/** SweepsView against the real API's shapes: the POST /sweeps body carries
 * `controllers` (a list) plus `include_baseline`, and the matrix reads
 * aggregates keyed by the validation.metrics.Metrics field names. */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setOfflineFallback } from '../api/client';
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
  return { mean, lo95: mean * 0.95, hi95: mean * 1.05, n, underpowered: n < 20 };
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

  it('launches with a controllers list and include_baseline', async () => {
    render(
      <MemoryRouter initialEntries={['/sweeps']}>
        <SweepsView />
      </MemoryRouter>,
    );
    await screen.findByRole('option', { name: 'corridor_10km' }, { timeout: 4000 });
    fireEvent.click(screen.getByRole('button', { name: /^Launch/ }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))).toBe(true);
    });
    const first = calls.find((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))!.body as Record<string, unknown>;
    expect(first.controllers).toEqual(['follower_stopper']);
    expect(first).not.toHaveProperty('controller');
    expect(first.include_baseline).toBe(true);
    expect(first).toMatchObject({ scenario_id: 'scn-corridor', replicates: 20 });
    expect(first.penetrations).toEqual([0.01, 0.02, 0.05, 0.1, 0.15, 0.2]);
    expect(first.compliances).toEqual([0.25, 0.5, 0.8, 1.0]);

    // opting out of the baseline cell is sent explicitly, not omitted
    fireEvent.click(screen.getByLabelText('include p=0 baseline cell'));
    fireEvent.click(screen.getByRole('button', { name: /^Launch/ }));
    await waitFor(() => {
      expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/sweeps')).length).toBe(2);
    });
    const second = calls.filter((c) => c.method === 'POST' && c.url.endsWith('/sweeps'))[1].body as Record<string, unknown>;
    expect(second.include_baseline).toBe(false);
  });
});
