/** RunsView: what the launcher actually sends, the cost gate in front of a
 * long replicate set, the scenario name in the table, and the response to a
 * rejected API key (stop polling, say so — `/healthz` is auth-exempt, so a
 * silent retry loop behind a green dot is the failure mode being prevented). */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  clearAuthFailure,
  isAuthFailed,
  OFFLINE_WRITE_MESSAGE,
  setOfflineFallback,
} from '../api/client';
import { Toasts } from '../components/toast';
import { mockListRuns } from '../mocks/mockApi';
import { RunsView } from '../views/RunsView';

const scenario = {
  scenario_id: 'scn_72b91153417c',
  name: 'i24_replica',
  config_hash: 'c0ffeec0ffee',
  created_at: 't',
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

/** The API's RunOut carries no scenario name — only the id. */
const run = {
  run_id: 'run-a41d09',
  scenario_id: 'scn_72b91153417c',
  sweep_id: null,
  status: 'running',
  tier: 'micro',
  config_hash: 'c0ffeec0ffee',
  seeded: false,
  progress: { completed_replicates: 2, total_replicates: 20 },
  seeds: [1, 2],
  error: null,
  error_kind: null,
  created_at: '2026-09-16T00:00:00',
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

interface Call {
  url: string;
  method: string;
  body?: unknown;
}

describe('RunsView launcher', () => {
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
        if (url.endsWith('/scenarios')) return json([scenario]);
        if (url.endsWith('/runs') && method === 'POST') return json({ run_id: 'run-new' }, 202);
        if (url.endsWith('/runs')) return json([run]);
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthFailure();
  });

  it('names the scenario in the table instead of printing its id', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const table = await screen.findByRole('table', { name: 'runs' }, { timeout: 4000 });
    expect(await within(table).findByText('i24_replica', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(within(table).queryByText('scn_72b91153417c')).toBeNull();
  });

  it('confirms before committing 20 replicates of a 130-minute scenario', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    // the launcher prefills the scenario's own duration/seed/replicates
    await waitFor(() => {
      expect(screen.getByLabelText('Duration (s)')).toHaveValue(7800);
    });
    expect(screen.getByLabelText('Replicates')).toHaveValue(20);
    expect(screen.getByLabelText('Seed')).toHaveValue(42);

    fireEvent.click(screen.getByRole('button', { name: 'Launch run' }));
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    const dialog = await screen.findByRole('dialog', { name: 'Launch this run?' });
    expect(within(dialog).getByText('2,600 sim-min (43.3 sim-h)')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/runs'))).toBe(true);
    });
    const posted = calls.find((c) => c.method === 'POST' && c.url.endsWith('/runs'));
    expect(posted?.body).toMatchObject({
      scenario_id: 'scn_72b91153417c',
      replicates: 20,
      tier: 'micro',
    });
    expect(posted?.body).not.toHaveProperty('overrides');
  });

  it('sends a shortened duration as an overrides patch and skips the gate', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByLabelText('Duration (s)')).toHaveValue(7800);
    });
    fireEvent.change(screen.getByLabelText('Duration (s)'), { target: { value: '600' } });
    fireEvent.change(screen.getByLabelText('Replicates'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Launch run' }));
    // 3 x 10 sim-min is small: no confirmation, straight to the API
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/runs'))).toBe(true);
    });
    expect(screen.queryByRole('dialog')).toBeNull();
    const posted = calls.find((c) => c.method === 'POST' && c.url.endsWith('/runs'));
    expect(posted?.body).toMatchObject({
      scenario_id: 'scn_72b91153417c',
      replicates: 3,
      overrides: { sim: { duration_s: 600 } },
    });
  });

  it('clamps Duration and Seed, keeping an empty box as "scenario default"', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const duration = await screen.findByLabelText('Duration (s)', {}, { timeout: 4000 });
    const seed = screen.getByLabelText('Seed');

    fireEvent.change(duration, { target: { value: '-600' } });
    expect(duration).toHaveValue(1);
    fireEvent.change(seed, { target: { value: '-3' } });
    expect(seed).toHaveValue(0);

    // an emptied field still means "whatever the scenario says", so the launch
    // carries no overrides at all
    fireEvent.change(duration, { target: { value: '' } });
    fireEvent.change(seed, { target: { value: '' } });
    expect(duration).toHaveValue(null);
    fireEvent.click(screen.getByRole('button', { name: 'Launch run' }));
    const dialog = await screen.findByRole('dialog', { name: 'Launch this run?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/runs'))).toBe(true);
    });
    const posted = calls.find((c) => c.method === 'POST' && c.url.endsWith('/runs'));
    expect(posted?.body).not.toHaveProperty('overrides');
  });

  it('caps the replicate field at the API maximum', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const reps = await screen.findByLabelText('Replicates', {}, { timeout: 4000 });
    // the launcher prefills this field from the scenario list once that fetch resolves;
    // typing before then would be overwritten by the prefill (a flaky failure on CI)
    await waitFor(() => expect(reps).not.toHaveValue(null), { timeout: 4000 });
    fireEvent.change(reps, { target: { value: '500' } });
    await waitFor(() => expect(reps).toHaveValue(200));
  });
});

describe('RunsView with a rejected API key', () => {
  const calls: string[] = [];

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    calls.length = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        calls.push(String(input));
        return json({ detail: 'invalid or missing X-API-Key' }, 401);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthFailure();
  });

  it('stops polling and says the key was rejected instead of retrying forever', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/paused — API key rejected/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(isAuthFailed()).toBe(true);
    const after = calls.length;
    // well past the 2 s runs poll and the 3 s scenario retry
    await new Promise((r) => setTimeout(r, 4000));
    expect(calls.length).toBe(after);
  }, 15000);
});

/** When `/healthz` stops answering the dashboard serves demo data for reads.
 * A launch is not a read: `POST /runs` answered by the in-browser backend
 * would report a run queued that no worker will ever pick up, so the launcher
 * closes and a confirmation already on screen is refused. */
describe('RunsView with the API offline', () => {
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
        if (url.endsWith('/scenarios')) return json([scenario]);
        if (url.endsWith('/runs') && method === 'POST') return json({ run_id: 'run-new' }, 202);
        if (url.endsWith('/runs')) return json([run]);
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    setOfflineFallback(false);
    vi.unstubAllGlobals();
    clearAuthFailure();
  });

  it('refuses a launch confirmed after the API went away, and enqueues nothing', async () => {
    render(
      <>
        <Toasts />
        <MemoryRouter>
          <RunsView />
        </MemoryRouter>
      </>,
    );
    await waitFor(() => {
      expect(screen.getByLabelText('Duration (s)')).toHaveValue(7800);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Launch run' }));
    const dialog = await screen.findByRole('dialog', { name: 'Launch this run?' });

    // the API drops while the cost gate is on screen
    const demoRunsBefore = (await mockListRuns()).length;
    act(() => setOfflineFallback(true));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch' }));

    expect(await screen.findByText(/API offline/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    // nothing was invented in the demo backend either — no run row that no
    // server has heard of
    expect((await mockListRuns()).length).toBe(demoRunsBefore);

    // and the launcher itself says why it is closed
    const launch = screen.getByRole('button', { name: 'Launch run' });
    expect(launch).toBeDisabled();
    expect(launch).toHaveAttribute('title', OFFLINE_WRITE_MESSAGE);
  }, 15000);
});
