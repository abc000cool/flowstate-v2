/** RunsView: what the launcher actually sends, the cost gate in front of a
 * long replicate set, the scenario name in the table, and the response to a
 * rejected API key (stop polling, say so — `/healthz` is auth-exempt, so a
 * silent retry loop behind a green dot is the failure mode being prevented).
 *
 * Plus the honesty rules a browser walkthrough found missing: a failed run
 * states the service's reason, demo rows are labelled and carry neither a
 * server hash nor a moving progress bar, presets are launchable from here, a
 * duration inside the warm-up is refused before it costs a queue slot, and a
 * reconnect re-reads the scenario library instead of printing raw ids for
 * half a minute. */

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
        if (url.endsWith('/scenarios/preset')) return json([]);
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
        if (url.endsWith('/scenarios/preset')) return json([]);
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

/** A failed run answers *why* — `RunOut.error` is in the payload the table
 * already fetched, and "RUN FAILED 2/2" with the reason dropped sends the user
 * to the server logs for something the dashboard was holding. */
describe('RunsView failure reasons', () => {
  const FAILURE =
    'ValueError: warm-up 120 s leaves no measurement window in a run recorded over [1.5, 120] s';

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        if (url.endsWith('/scenarios/preset')) return json([]);
        if (url.endsWith('/scenarios')) return json([scenario]);
        if (url.endsWith('/runs')) {
          return json([
            {
              ...run,
              status: 'failed',
              progress: { completed_replicates: 2, total_replicates: 2 },
              error: FAILURE,
              error_kind: 'ValueError',
            },
          ]);
        }
        return json({ detail: `unexpected ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthFailure();
  });

  it('prints the reason the API reported, not just the failed chip', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const table = await screen.findByRole('table', { name: 'runs' }, { timeout: 4000 });
    const reason = await within(table).findByText(
      /leaves no measurement window/,
      {},
      { timeout: 4000 },
    );
    // the kind prefixes the text, and the full line is also the cell tooltip
    expect(reason.textContent).toContain('ValueError:');
    expect(reason).toHaveAttribute('title', `ValueError: ${FAILURE}`);
  });
});

/** Reads fall back to the in-browser demo backend when the API is unreachable.
 * The Scenarios cards already label that; the runs table did not, and showed
 * fabricated config hashes and a moving "running 3/20" bar for replicates no
 * worker had ever been asked to compute (CLAUDE.md §0.1). */
describe('RunsView demo fallback', () => {
  beforeEach(() => {
    clearAuthFailure();
    setOfflineFallback(true);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (): Promise<Response> => {
        throw new TypeError('network down');
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setOfflineFallback(false);
  });

  it('badges demo rows, hides their hashes and does not animate their progress', async () => {
    const { container } = render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const table = await screen.findByRole('table', { name: 'runs' }, { timeout: 4000 });
    await within(table).findByText('run-8f2c11', {}, { timeout: 4000 });

    expect(within(table).getAllByText('DEMO').length).toBeGreaterThan(0);
    // a demo hash exists on no server: printing one fabricates provenance
    expect(within(table).getAllByText('— demo, no server hash —').length).toBeGreaterThan(0);
    expect(within(table).queryByText('run-8f2c11')?.textContent).toBe('run-8f2c11');
    // and nothing animates a replicate count that is not being computed
    expect(container.querySelectorAll('.progress').length).toBe(0);
    expect(within(table).getAllByText(/— demo$/).length).toBeGreaterThan(0);
  }, 10000);
});

/** The demo backend itself used to advance its rows from the wall clock, so a
 * dashboard left open with the API dead walked a run from 4/20 to 19/20 —
 * compute no worker had been asked for, shown as progress (CLAUDE.md §0.1).
 * Demo data may illustrate the shapes the API returns; it may never look
 * live. */
describe('mock backend run progress', () => {
  it('serves the same n/N however long the page has been open', async () => {
    const shape = (rows: Awaited<ReturnType<typeof mockListRuns>>): string[] =>
      rows.map(
        (r) =>
          `${r.run_id} ${r.status} ` +
          `${r.progress.completed_replicates}/${r.progress.total_replicates}`,
      );

    const before = await mockListRuns();
    const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 45 * 60_000);
    try {
      expect(shape(await mockListRuns())).toEqual(shape(before));
    } finally {
      clock.mockRestore();
    }

    // and the partly-finished row is a fixed fraction: still a running row,
    // just not one that moves
    const running = before.find((r) => r.status === 'running');
    expect(running?.progress.completed_replicates).toBe(7);
    expect(before.some((r) => r.status === 'queued')).toBe(false);
  }, 10000);
});

/** The launcher used to offer stored scenarios only, so a fresh install — no
 * stored scenario yet — had an empty dropdown and no way to start anything.
 * Presets are repo YAMLs: they are stored first, then run. */
describe('RunsView preset launching', () => {
  const calls: Call[] = [];
  const preset = {
    name: 'ring_sugiyama',
    filename: 'ring_sugiyama.yaml',
    config_hash: 'presethash01',
    preset: true,
    config: {
      name: 'ring_sugiyama',
      tier: 'micro',
      network: { kind: 'ring', circumference_m: 230, n_vehicles: 22 },
      fleet: { model: 'IDM' },
      av: { penetration: 0, compliance: 1, controller: null },
      sim: { duration_s: 600 },
      seed: 42,
      replicates: 3,
    },
  };

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
        if (url.endsWith('/scenarios/preset')) return json([preset]);
        if (url.endsWith('/scenarios') && method === 'GET') return json([]);
        if (url.endsWith('/scenarios') && method === 'POST') {
          return json({ scenario_id: 'scn_stored', config_hash: preset.config_hash }, 201);
        }
        if (url.endsWith('/runs') && method === 'POST') return json({ run_id: 'run-new' }, 202);
        if (url.endsWith('/runs')) return json([]);
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthFailure();
  });

  it('offers presets and stores the chosen one before launching it', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const select = await screen.findByLabelText('Scenario', {}, { timeout: 4000 });
    await waitFor(() => {
      expect(within(select).getByRole('option', { name: 'ring_sugiyama (preset)' })).toBeTruthy();
    }, { timeout: 4000 });
    // the preset's own values prefill the launcher
    await waitFor(() => expect(screen.getByLabelText('Replicates')).toHaveValue(3));

    fireEvent.click(screen.getByRole('button', { name: 'Launch run' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/runs'))).toBe(true);
    });
    const stored = calls.find((c) => c.method === 'POST' && c.url.endsWith('/scenarios'));
    expect(stored?.body).toMatchObject({ name: 'ring_sugiyama' });
    const posted = calls.find((c) => c.method === 'POST' && c.url.endsWith('/runs'));
    expect(posted?.body).toMatchObject({ scenario_id: 'scn_stored', replicates: 3 });
  });
});

/** A duration at or inside the warm-up leaves no measurement window: every
 * replicate dies on the worker with "warm-up N s leaves no measurement
 * window". The launcher says so before spending the queue slot. */
describe('RunsView warm-up guard', () => {
  const calls: Call[] = [];
  const warmed = {
    ...scenario,
    config: { ...scenario.config, sim: { duration_s: 14400, warmup_s: 1800 } },
  };

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
        if (url.endsWith('/scenarios/preset')) return json([]);
        if (url.endsWith('/scenarios')) return json([warmed]);
        if (url.endsWith('/runs') && method === 'POST') return json({ run_id: 'run-new' }, 202);
        if (url.endsWith('/runs')) return json([]);
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthFailure();
  });

  it('refuses a duration inside the warm-up, naming both numbers', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByLabelText('Duration (s)')).toHaveValue(14400), {
      timeout: 4000,
    });
    fireEvent.change(screen.getByLabelText('Duration (s)'), { target: { value: '1800' } });

    const launch = screen.getByRole('button', { name: 'Launch run' });
    expect(launch).toBeDisabled();
    expect(launch.getAttribute('title')).toMatch(/1800 s as warm-up/);
    expect(screen.getByText(/Duration 1800 s leaves no measurement window/)).toBeInTheDocument();
    fireEvent.click(launch);
    expect(calls.some((c) => c.method === 'POST')).toBe(false);

    // a duration that clears the warm-up by a measurable window is fine again
    fireEvent.change(screen.getByLabelText('Duration (s)'), { target: { value: '3600' } });
    expect(screen.getByRole('button', { name: 'Launch run' })).toBeEnabled();
  });
});

/** After a reconnect the runs poll is back within 2 s but the library refresh
 * is 30 s away, so the table printed raw `scn_…` ids in the meantime. */
describe('RunsView on reconnect', () => {
  let live = false;

  beforeEach(() => {
    clearAuthFailure();
    live = false;
    setOfflineFallback(true);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        if (!live) throw new TypeError('network down');
        if (url.endsWith('/scenarios/preset')) return json([]);
        if (url.endsWith('/scenarios')) return json([scenario]);
        if (url.endsWith('/runs')) return json([run]);
        return json({ detail: `unexpected ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setOfflineFallback(false);
  });

  it('re-reads the scenario library at once instead of printing ids for 30 s', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const table = await screen.findByRole('table', { name: 'runs' }, { timeout: 4000 });
    await within(table).findByText('run-8f2c11', {}, { timeout: 4000 });

    live = true;
    act(() => setOfflineFallback(false));
    // the slow library poll is 30 s away; this must not wait for it
    expect(
      await within(table).findByText('i24_replica', {}, { timeout: 6000 }),
    ).toBeInTheDocument();
    expect(within(table).queryByText('scn_72b91153417c')).toBeNull();
  }, 15000);
});
