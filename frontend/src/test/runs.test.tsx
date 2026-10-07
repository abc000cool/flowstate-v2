/** RunsView: what the launcher actually sends, the cost gate in front of a
 * long replicate set, the scenario name in the table, and the response to a
 * rejected API key (stop polling, say so — `/health` is auth-exempt, so a
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
import { launchPresetState } from '../lib/hooks';
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
    // type once the launcher is ready, as a user would: the scenario chosen,
    // its own values shown and Launch enabled. (Typing earlier is covered by
    // "when the scenario list answers late" below, with the order fixed.)
    await waitFor(() => expect(duration).toHaveValue(7800), { timeout: 4000 });
    expect(screen.getByRole('button', { name: 'Launch run' })).toBeEnabled();

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
    // type once the launcher has shown the scenario's own values, as a user would
    await waitFor(() => expect(reps).toHaveValue(20), { timeout: 4000 });
    fireEvent.change(reps, { target: { value: '500' } });
    await waitFor(() => expect(reps).toHaveValue(200));
  });
});

/** The launcher fills its fields from the scenario list, and that list can
 * answer after the user has started typing: a cold server parses every preset
 * YAML to answer `GET /scenarios/preset`, and a slow CI runner stretches the
 * same window (it failed "clamps Duration and Seed" above this way: the list
 * landed with the first keystroke and its prefill replaced the typed value).
 * A list that lands late may still choose the scenario the launcher opens on
 * and fill the fields nobody has touched; it never replaces what the user
 * typed and never moves a scenario the user picked. The library reads are
 * held here until the test answers them, so the order is fixed, not timed. */
describe('RunsView launcher when the scenario list answers late', () => {
  const calls: Call[] = [];
  function ringPreset(hash: string, duration_s: number, replicates: number): unknown {
    return {
      name: 'ring_sugiyama',
      filename: 'ring_sugiyama.yaml',
      config_hash: hash,
      preset: true,
      config: {
        name: 'ring_sugiyama',
        tier: 'micro',
        network: { kind: 'ring', circumference_m: 230, n_vehicles: 22 },
        fleet: { model: 'IDM' },
        av: { penetration: 0, compliance: 1, controller: null },
        sim: { duration_s },
        seed: 42,
        replicates,
      },
    };
  }
  /** What `GET /scenarios/preset` serves; a test may edit it between reads. */
  let presets: unknown[] = [];
  /** Library reads (`GET /scenarios/preset`, `GET /scenarios`) not yet answered. */
  let held: (() => void)[] = [];
  let holding = true;

  /** A JSON answer whose body is read in one microtask. How many ticks the
   * runtime's own body parsing takes differs between Node versions, and here
   * it would decide where the answer lands relative to a keystroke. */
  function promptJson(body: unknown): Response {
    const res = json(body);
    res.json = () => Promise.resolve(body);
    return res;
  }

  /** Answer every held library read, and every later one at once. */
  function openGate(): void {
    holding = false;
    for (const answer of held.splice(0)) answer();
  }

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    calls.length = 0;
    presets = [ringPreset('ringhash0001', 600, 3)];
    held = [];
    holding = true;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        const method = init?.method ?? 'GET';
        const body = init?.body ? (JSON.parse(String(init.body)) as unknown) : undefined;
        calls.push({ url, method, body });
        // the payload is taken when the answer is sent, so an edit made while
        // a read is held is what that read answers
        const libraryRead = (payload: () => unknown): Promise<Response> =>
          holding
            ? new Promise((resolve) => held.push(() => resolve(promptJson(payload()))))
            : Promise.resolve(promptJson(payload()));
        if (url.endsWith('/scenarios/preset')) return libraryRead(() => presets);
        if (url.endsWith('/scenarios') && method === 'GET') return libraryRead(() => [scenario]);
        if (url.endsWith('/scenarios') && method === 'POST') {
          return Promise.resolve(json({ scenario_id: 'scn_ring', config_hash: 'ringhash0001' }, 201));
        }
        if (url.endsWith('/runs') && method === 'POST') {
          return Promise.resolve(json({ run_id: 'run-new' }, 202));
        }
        if (url.endsWith('/runs')) return Promise.resolve(json([run]));
        return Promise.resolve(json({ detail: `unexpected ${method} ${url}` }, 404));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthFailure();
  });

  it('keeps what was typed before the list arrived and fills only the untouched field', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const select = screen.getByLabelText('Scenario');
    const duration = screen.getByLabelText('Duration (s)');
    const seed = screen.getByLabelText('Seed');
    const reps = screen.getByLabelText('Replicates');
    // the library reads are out and unanswered: nothing to choose yet
    expect(held.length).toBeGreaterThan(0);
    expect(within(select).queryAllByRole('option')).toHaveLength(0);

    fireEvent.change(duration, { target: { value: '-600' } });
    fireEvent.change(seed, { target: { value: '7' } });
    expect(duration).toHaveValue(1);

    await act(async () => {
      openGate();
      await new Promise((r) => setTimeout(r, 0));
    });
    // the list opens the launcher on the ring, as documented ...
    await waitFor(() => expect(select).toHaveDisplayValue('ring_sugiyama (preset)'), {
      timeout: 4000,
    });
    // ... and fills the field nobody touched, but not the two that were typed
    await waitFor(() => expect(reps).toHaveValue(3));
    expect(duration).toHaveValue(1);
    expect(seed).toHaveValue(7);

    // and the launch sends what the user typed
    fireEvent.click(screen.getByRole('button', { name: 'Launch run' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/runs'))).toBe(true);
    });
    const posted = calls.find((c) => c.method === 'POST' && c.url.endsWith('/runs'));
    expect(posted?.body).toMatchObject({
      scenario_id: 'scn_ring',
      replicates: 3,
      overrides: { sim: { duration_s: 1 }, seed: 7 },
    });
  });

  it('keeps a keystroke that the list lands with, in the same flush', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const select = screen.getByLabelText('Scenario');
    const duration = screen.getByLabelText('Duration (s)');
    // the interleaving that failed "clamps Duration and Seed" on CI: the list
    // has been read and set when the user types, but not rendered yet, so it
    // is applied together with the keystroke. Inside this act React holds the
    // list's update until the keystroke flushes it or the act ends.
    await act(async () => {
      openGate();
      await new Promise((r) => setTimeout(r, 0));
      expect(within(select).queryAllByRole('option')).toHaveLength(0);
      fireEvent.change(duration, { target: { value: '-600' } });
      expect(duration).toHaveValue(1);
    });
    // the list landed (the launcher opened on the ring) and the value stands
    expect(select).toHaveDisplayValue('ring_sugiyama (preset)');
    expect(duration).toHaveValue(1);
    expect(screen.getByLabelText('Replicates')).toHaveValue(3);
  });

  it("applies the palette's ring request when the list lands, around what was typed meanwhile", async () => {
    // "Launch a ring run…" from another page: the request is taken at once,
    // the library it needs is still loading
    render(
      <MemoryRouter
        initialEntries={[{ pathname: '/runs', state: launchPresetState('ring_sugiyama') }]}
      >
        <RunsView />
      </MemoryRouter>,
    );
    const select = screen.getByLabelText('Scenario');
    expect(screen.getByLabelText('Tier')).toHaveValue('micro');
    fireEvent.change(screen.getByLabelText('Tier'), { target: { value: 'macro' } });
    fireEvent.change(screen.getByLabelText('Duration (s)'), { target: { value: '300' } });

    await act(async () => {
      openGate();
      await new Promise((r) => setTimeout(r, 0));
    });
    await waitFor(() => expect(select).toHaveDisplayValue('ring_sugiyama (preset)'), {
      timeout: 4000,
    });
    // the ring's values where nothing was typed; the typed ones stand
    await waitFor(() => expect(screen.getByLabelText('Replicates')).toHaveValue(3));
    expect(screen.getByLabelText('Seed')).toHaveValue(42);
    expect(screen.getByLabelText('Duration (s)')).toHaveValue(300);
    expect(screen.getByLabelText('Tier')).toHaveValue('macro');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });

  it('never moves a picked scenario, nor its typed fields, when a later list edits it', async () => {
    openGate();
    presets = [
      ringPreset('ringhash0001', 600, 3),
      {
        ...(ringPreset('corrhash0001', 1200, 20) as object),
        name: 'corridor_10km',
        filename: 'corridor_10km.yaml',
      },
    ];
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const select = await screen.findByLabelText('Scenario');
    await waitFor(() => expect(select).toHaveDisplayValue('ring_sugiyama (preset)'), {
      timeout: 4000,
    });
    fireEvent.change(select, { target: { value: 'preset:corridor_10km.yaml' } });
    const duration = screen.getByLabelText('Duration (s)');
    await waitFor(() => expect(duration).toHaveValue(1200));
    fireEvent.change(duration, { target: { value: '300' } });

    // the corridor preset is edited on the server (new hash, same file), and
    // the next read is answered only after the user has typed
    presets = [
      ringPreset('ringhash0001', 600, 3),
      {
        ...(ringPreset('corrhash0002', 1500, 10) as object),
        name: 'corridor_10km',
        filename: 'corridor_10km.yaml',
      },
    ];
    holding = true;
    act(() => setOfflineFallback(true));
    act(() => setOfflineFallback(false));
    await waitFor(() => expect(held.length).toBeGreaterThan(0), { timeout: 4000 });
    await act(async () => {
      openGate();
      await new Promise((r) => setTimeout(r, 0));
    });

    // the untouched field shows the edited preset's own value; the typed one
    // and the pick stand
    await waitFor(() => expect(screen.getByLabelText('Replicates')).toHaveValue(10), {
      timeout: 4000,
    });
    expect(select).toHaveDisplayValue('corridor_10km (preset)');
    expect(duration).toHaveValue(300);
  }, 10000);
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

/** When `/health` stops answering the dashboard serves demo data for reads.
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

/** The launcher used to open on whatever the library listed first — on a
 * live service that is the `corridor_10km` preset (file order), 20 × 20 min
 * ≈ 6.7 sim-hours one click from the queue. Until the user picks a scenario
 * it opens on the ring benchmark, or on the cheapest preset when the service
 * has no ring. */
describe('RunsView default scenario', () => {
  function presetOf(name: string, replicates: number, duration_s: number): unknown {
    return {
      name,
      filename: `${name}.yaml`,
      config_hash: `hash-${name}`,
      preset: true,
      config: {
        name,
        tier: 'micro',
        network: { kind: 'corridor', length_m: 10000, lanes: 1, inflow: [[0, 0.55]] },
        fleet: { model: 'IDM' },
        av: { penetration: 0, compliance: 1, controller: null },
        sim: { duration_s },
        seed: 42,
        replicates,
      },
    };
  }

  /** How many times the preset list was read (a library load). */
  let presetReads = 0;

  function serve(presets: unknown[], stored: unknown[] = []): void {
    presetReads = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        if (url.endsWith('/scenarios/preset')) {
          presetReads += 1;
          return json(presets);
        }
        if (url.endsWith('/scenarios')) return json(stored);
        if (url.endsWith('/runs')) return json([]);
        return json({ detail: `unexpected ${url}` }, 404);
      }),
    );
  }

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthFailure();
  });

  it('opens on ring_sugiyama, not the first preset in file order', async () => {
    serve([
      presetOf('corridor_10km', 20, 1200),
      presetOf('corridor_10km_workzone', 20, 1200),
      presetOf('ring_sugiyama', 20, 600),
    ]);
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const select = await screen.findByLabelText('Scenario', {}, { timeout: 4000 });
    await waitFor(() => expect(select).toHaveDisplayValue('ring_sugiyama (preset)'), {
      timeout: 4000,
    });
    // and the launcher is prefilled from the ring's own config
    await waitFor(() => expect(screen.getByLabelText('Duration (s)')).toHaveValue(600));
  });

  it('opens on the stored copy of the ring when the preset is already stored', async () => {
    const ring = presetOf('ring_sugiyama', 20, 600) as { config_hash: string; config: unknown };
    serve(
      [presetOf('corridor_10km', 20, 1200), ring],
      [
        {
          scenario_id: 'scn_big',
          name: 'i24_replica',
          config_hash: 'hash-i24',
          created_at: 't',
          config: scenario.config,
        },
        {
          scenario_id: 'scn_ring',
          name: 'ring_sugiyama',
          config_hash: ring.config_hash,
          created_at: 't',
          config: ring.config,
        },
      ],
    );
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const select = await screen.findByLabelText('Scenario', {}, { timeout: 4000 });
    // the stored copy has no "(preset)" suffix: it is the same config by hash
    await waitFor(() => expect(select).toHaveDisplayValue('ring_sugiyama'), { timeout: 4000 });
  });

  it('opens on the cheapest preset when the service has no ring', async () => {
    serve([
      presetOf('corridor_10km', 20, 1200),
      presetOf('i24_replica', 20, 7800),
      presetOf('corridor_smoke', 3, 600),
    ]);
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const select = await screen.findByLabelText('Scenario', {}, { timeout: 4000 });
    await waitFor(() => expect(select).toHaveDisplayValue('corridor_smoke (preset)'), {
      timeout: 4000,
    });
  });

  it('keeps the scenario the user picked across library refreshes', async () => {
    serve([presetOf('corridor_10km', 20, 1200), presetOf('ring_sugiyama', 20, 600)]);
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const select = await screen.findByLabelText('Scenario', {}, { timeout: 4000 });
    await waitFor(() => expect(select).toHaveDisplayValue('ring_sugiyama (preset)'), {
      timeout: 4000,
    });
    fireEvent.change(select, { target: { value: 'preset:corridor_10km.yaml' } });
    expect(select).toHaveDisplayValue('corridor_10km (preset)');
    // a reconnect re-reads the library at once, handing back fresh objects
    // (the idle refresh is 30 s away); the choice stands
    const before = presetReads;
    act(() => setOfflineFallback(true));
    act(() => setOfflineFallback(false));
    await waitFor(() => expect(presetReads).toBeGreaterThan(before), { timeout: 4000 });
    await waitFor(() => expect(screen.getByLabelText('Duration (s)')).toHaveValue(1200));
    expect(select).toHaveDisplayValue('corridor_10km (preset)');
  }, 10000);
});

/** The header's "Live" pill claims the rows are current. When `GET /runs`
 * starts failing after a first answer (a 503 from the front end, a locked
 * store) while `/health` still answers, the table keeps the last answer —
 * and the header must stop calling it live: it says when that answer came
 * and why the newer reads failed, and turns back to Live once a poll lands. */
describe('RunsView when GET /runs starts failing', () => {
  let runsFail = false;

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    runsFail = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        if (url.endsWith('/scenarios/preset')) return json([]);
        if (url.endsWith('/scenarios')) return json([scenario]);
        if (url.endsWith('/runs')) {
          return runsFail ? json({ detail: 'database is locked' }, 503) : json([run]);
        }
        return json({ detail: `unexpected ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthFailure();
  });

  it('swaps Live for a stale pill naming the last update and the error', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const table = await screen.findByRole('table', { name: 'runs' }, { timeout: 4000 });
    await within(table).findByText('run-a41d09', {}, { timeout: 4000 });
    expect(screen.getByText('Live')).toHaveAttribute('title', 'Polling every 2 s');

    runsFail = true;
    const stale = await screen.findByText(
      /^Stale — last update \d{2}:\d{2}:\d{2}$/,
      {},
      { timeout: 4000 },
    );
    expect(screen.queryByText('Live')).toBeNull();
    expect(stale.getAttribute('title')).toMatch(/GET \/runs is failing/);
    // the service's own words, with the status, beside the pill
    expect(screen.getByText('HTTP 503 — database is locked')).toBeInTheDocument();
    // the rows stay on screen: the last answer, now labelled as such
    expect(within(table).getByText('run-a41d09')).toBeInTheDocument();

    // and a poll that lands again makes the header live again
    runsFail = false;
    expect(await screen.findByText('Live', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByText(/^Stale — last update/)).toBeNull();
    expect(screen.queryByText('HTTP 503 — database is locked')).toBeNull();
  }, 15000);
});

/** The 2 s poll does not wait for the last read, so once `GET /runs` takes
 * longer than that (a store held by its busy timeout, a gateway that answers
 * 504 after its own timeout) the reads overlap and each is overtaken by the
 * next before it answers. Every answer must still count unless a newer one
 * has already landed — failures included — or the header keeps saying Live
 * over rows that stopped updating. */
describe('RunsView when GET /runs is slower than the poll', () => {
  /** The `/runs` reads still waiting for an answer, oldest first. */
  let pending: ((res: Response) => void)[] = [];
  let slow = false;

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    pending = [];
    slow = false;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        if (url.endsWith('/scenarios/preset')) return Promise.resolve(json([]));
        if (url.endsWith('/scenarios')) return Promise.resolve(json([scenario]));
        if (url.endsWith('/runs')) {
          if (!slow) return Promise.resolve(json([run]));
          return new Promise<Response>((resolve) => pending.push(resolve));
        }
        return Promise.resolve(json({ detail: `unexpected ${url}` }, 404));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthFailure();
  });

  /** Answer the `n`th read still pending (0 = oldest sent). */
  async function answer(n: number, res: Response): Promise<void> {
    await act(async () => {
      pending[n](res);
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  it('turns stale when an overtaken read fails, and live again when a newer one lands', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const table = await screen.findByRole('table', { name: 'runs' }, { timeout: 4000 });
    await within(table).findByText('run-a41d09', {}, { timeout: 4000 });
    expect(screen.getByText('Live')).toBeInTheDocument();

    slow = true;
    // two reads in flight at once: the first is overtaken before it answers
    await waitFor(() => expect(pending.length).toBeGreaterThanOrEqual(2), { timeout: 6000 });
    await answer(0, json({ detail: 'gateway timeout' }, 504));
    expect(screen.getByText(/^Stale — last update \d{2}:\d{2}:\d{2}$/)).toBeInTheDocument();
    expect(screen.getByText('HTTP 504 — gateway timeout')).toBeInTheDocument();
    expect(screen.queryByText('Live')).toBeNull();
    // the rows stay, labelled as the last answer
    expect(within(table).getByText('run-a41d09')).toBeInTheDocument();

    // the newer read lands, rows and all, though a third may be in flight
    await answer(1, json([{ ...run, run_id: 'run-b77e10' }]));
    expect(screen.getByText('Live')).toBeInTheDocument();
    expect(screen.queryByText(/^Stale — last update/)).toBeNull();
    expect(within(table).getByText('run-b77e10')).toBeInTheDocument();
    for (const resolve of pending.slice(2)) resolve(json([{ ...run, run_id: 'run-b77e10' }]));
  }, 20000);

  it('never lets an older answer undo a newer one', async () => {
    render(
      <MemoryRouter>
        <RunsView />
      </MemoryRouter>,
    );
    const table = await screen.findByRole('table', { name: 'runs' }, { timeout: 4000 });
    await within(table).findByText('run-a41d09', {}, { timeout: 4000 });

    slow = true;
    await waitFor(() => expect(pending.length).toBeGreaterThanOrEqual(2), { timeout: 6000 });
    // the newer read fails first; the older one's rows, landing after, are
    // not the current state and must not turn the header live
    await answer(1, json({ detail: 'database is locked' }, 503));
    expect(screen.getByText(/^Stale — last update/)).toBeInTheDocument();
    await answer(0, json([{ ...run, run_id: 'run-old' }]));
    expect(screen.getByText(/^Stale — last update/)).toBeInTheDocument();
    expect(screen.queryByText('Live')).toBeNull();
    expect(within(table).queryByText('run-old')).toBeNull();
    for (const resolve of pending.slice(2)) resolve(json([run]));
  }, 20000);
});
