/** ScenariosView against the real API's shapes (presets carry no scenario_id),
 * plus the honesty rules: demo cards are labelled and replaced when the API
 * comes back, the launcher states its cost before enqueueing anything, and the
 * composer carries unmodelled preset fields through unchanged. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, OFFLINE_WRITE_MESSAGE, setOfflineFallback } from '../api/client';
import { AppStateProvider } from '../components/AppContext';
import { Toasts } from '../components/toast';
import { mockListRuns } from '../mocks/mockApi';
import { ScenariosView } from '../views/ScenariosView';

const preset = {
  name: 'ring_sugiyama',
  filename: 'ring_sugiyama.yaml',
  config_hash: 'abc123def456',
  // The API returns the full validated ScenarioConfig for a preset.
  config: {
    name: 'ring_sugiyama',
    tier: 'micro',
    network: { kind: 'ring', circumference_m: 230, n_vehicles: 22 },
    fleet: { model: 'IDM', v0: 33.3, T: 1.2, a_max: 0.73, b: 1.67, s0: 2.0, delta: 4.0, heterogeneity_frac: 0.12 },
    av: { penetration: 0, compliance: 1, controller: null, controller_params: {} },
    sim: { duration_s: 600, step_length_s: 0.5, action_step_s: 0.5, warmup_s: 180, output_hz: 2 },
    perturbation: null,
    seed: 42,
    replicates: 3,
  },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

interface Call {
  url: string;
  method: string;
  body?: unknown;
}

function renderView(): void {
  render(
    <AppStateProvider>
      <Toasts />
      <MemoryRouter initialEntries={['/scenarios']}>
        <ScenariosView />
      </MemoryRouter>
    </AppStateProvider>,
  );
}

describe('ScenariosView (real API shapes)', () => {
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
        if (url.endsWith('/scenarios/preset')) return json([preset]);
        if (url.endsWith('/scenarios') && method === 'GET') return json([]);
        if (url.endsWith('/scenarios') && method === 'POST') {
          return json({ scenario_id: 'scn_new', name: preset.name, config_hash: preset.config_hash, created_at: 't', config: preset.config }, 201);
        }
        if (url.endsWith('/runs') && method === 'POST') return json({ run_id: 'run_new', status: 'queued' }, 202);
        if (url.endsWith('/runs')) return json([]);
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setOfflineFallback(false);
  });

  it('badges a preset from the preset endpoint when the API sends no marker', async () => {
    renderView();
    expect(await screen.findByText('ring_sugiyama', {}, { timeout: 4000 })).toBeInTheDocument();
    // this fixture carries no `preset` key: the badge falls back to the
    // endpoint the item was loaded from
    expect(preset).not.toHaveProperty('preset');
    expect(screen.getByText('PRESET')).toBeInTheDocument();
  });

  it('clamps the launcher Duration and Seed rather than posting 0 or a negative', async () => {
    renderView();
    expect(await screen.findByText('ring_sugiyama', {}, { timeout: 4000 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Run…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Launch ring_sugiyama' });
    const duration = within(dialog).getByLabelText('Duration (s)');
    const seed = within(dialog).getByLabelText('Seed');

    // Number('') is 0, not NaN: an emptied field falls back to the scenario's
    // own value instead of committing sim.duration_s = 0 (which the API, with
    // SimSpec.duration_s gt=0, rejects)
    fireEvent.change(duration, { target: { value: '' } });
    expect(duration).toHaveValue(600);
    fireEvent.change(seed, { target: { value: '' } });
    expect(seed).toHaveValue(42);

    fireEvent.change(duration, { target: { value: '-90' } });
    expect(duration).toHaveValue(1);
    fireEvent.change(duration, { target: { value: '99999999' } });
    expect(duration).toHaveValue(86400);
    fireEvent.change(seed, { target: { value: '-7' } });
    expect(seed).toHaveValue(0);

    // back to the preset's own values, then launch: nothing to override
    fireEvent.change(duration, { target: { value: '' } });
    fireEvent.change(seed, { target: { value: '' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch run' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/runs'))).toBe(true);
    });
    const run = calls.find((c) => c.method === 'POST' && c.url.endsWith('/runs'));
    expect(run?.body).toMatchObject({ scenario_id: 'scn_new', replicates: 3 });
    expect(run?.body).not.toHaveProperty('overrides');
    for (const c of calls) {
      expect(JSON.stringify(c.body ?? {})).not.toContain('"duration_s":0');
    }
  });

  it('asks before launching and then stores the preset before running it', async () => {
    renderView();
    expect(await screen.findByText('ring_sugiyama', {}, { timeout: 4000 })).toBeInTheDocument();

    // the card's action opens the launcher; nothing is enqueued yet
    fireEvent.click(screen.getByRole('button', { name: 'Run…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Launch ring_sugiyama' });
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    // the preset's own replicate count and duration are shown and editable
    expect(within(dialog).getByLabelText('Replicates')).toHaveValue(3);
    expect(within(dialog).getByLabelText('Duration (s)')).toHaveValue(600);
    expect(within(dialog).getByText(/30 sim-min/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch run' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/runs'))).toBe(true);
    });
    const stored = calls.find((c) => c.method === 'POST' && c.url.endsWith('/scenarios'));
    expect(stored?.body).toMatchObject({ name: 'ring_sugiyama' });
    const run = calls.find((c) => c.method === 'POST' && c.url.endsWith('/runs'));
    expect(run?.body).toMatchObject({ scenario_id: 'scn_new', replicates: 3 });
    // unchanged duration/seed are not sent as overrides
    expect(run?.body).not.toHaveProperty('overrides');
  });

  it('sends edited duration, seed and replicates as an overrides patch', async () => {
    renderView();
    expect(await screen.findByText('ring_sugiyama', {}, { timeout: 4000 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Run…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Launch ring_sugiyama' });
    fireEvent.change(within(dialog).getByLabelText('Replicates'), { target: { value: '20' } });
    // past this preset's 180 s warm-up: a shorter run is refused by the
    // launcher now, since it would leave no measurement window
    fireEvent.change(within(dialog).getByLabelText('Duration (s)'), { target: { value: '300' } });
    fireEvent.change(within(dialog).getByLabelText('Seed'), { target: { value: '7' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch run' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/runs'))).toBe(true);
    });
    const run = calls.find((c) => c.method === 'POST' && c.url.endsWith('/runs'));
    expect(run?.body).toMatchObject({
      scenario_id: 'scn_new',
      replicates: 20,
      overrides: { sim: { duration_s: 300 }, seed: 7 },
    });
  });

  it('closes the launcher when the API drops, and refuses one already open', async () => {
    renderView();
    expect(await screen.findByText('ring_sugiyama', {}, { timeout: 4000 })).toBeInTheDocument();
    // live: real cards, and the card's launcher is open for business
    const runButton = screen.getByRole('button', { name: 'Run\u2026' });
    expect(runButton).toBeEnabled();
    fireEvent.click(runButton);
    const dialog = await screen.findByRole('dialog', { name: 'Launch ring_sugiyama' });

    // the API goes away while the launcher is open. `staleDemo` only learns
    // that on the next library refresh; the write path knows immediately.
    const demoRunsBefore = (await mockListRuns()).length;
    act(() => setOfflineFallback(true));
    expect(screen.getByRole('button', { name: 'Run\u2026' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Run\u2026' })).toHaveAttribute(
      'title',
      OFFLINE_WRITE_MESSAGE,
    );

    // confirming the already-open launcher must fail loudly, not quietly
    // enqueue a run in this browser's demo backend
    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch run' }));
    expect(await screen.findByText(new RegExp('API offline'), {}, { timeout: 4000 })).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    expect((await mockListRuns()).length).toBe(demoRunsBefore);
  });

  it('carries fields the composer does not model through unchanged', async () => {
    renderView();
    expect(await screen.findByText('ring_sugiyama', {}, { timeout: 4000 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load in composer' }));
    // the form says which fields it is not modelling
    expect(screen.getByText(/carried through from/)).toBeInTheDocument();
    expect(screen.getByText(/fleet\.heterogeneity_frac/)).toBeInTheDocument();
    expect(screen.getByText(/sim\.warmup_s/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Create scenario' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/scenarios'))).toBe(true);
    });
    const body = calls.find((c) => c.method === 'POST' && c.url.endsWith('/scenarios'))
      ?.body as Record<string, Record<string, unknown>>;
    // modelled fields come from the form, everything else from the preset
    expect(body.name).toBe('ring_sugiyama_variant');
    expect(body.fleet).toMatchObject({ model: 'IDM', v0: 33.3, T: 1.2, heterogeneity_frac: 0.12 });
    expect(body.sim).toMatchObject({ duration_s: 600, warmup_s: 180, step_length_s: 0.5, output_hz: 2 });
    expect(body.seed).toBe(42);
    expect(body.network).toMatchObject({ kind: 'ring', circumference_m: 230, n_vehicles: 22 });
  });
});

describe('ScenariosView demo fallback', () => {
  let live = false;
  const calls: string[] = [];

  beforeEach(() => {
    clearAuthFailure();
    live = false;
    calls.length = 0;
    setOfflineFallback(true); // as the Layout health poll would
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        calls.push(url);
        if (!live) throw new TypeError('network down');
        if (url.endsWith('/scenarios/preset')) return json([]);
        if (url.endsWith('/scenarios')) {
          return json([
            {
              scenario_id: 'scn-real',
              name: 'i24_replica',
              config_hash: 'realhash1234',
              created_at: 't',
              config: preset.config,
            },
          ]);
        }
        return json({ detail: `unexpected ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setOfflineFallback(false);
  });

  it('labels demo cards, hides their fake hashes, and replaces them when the API returns', async () => {
    renderView();
    // demo data first: mock scenarios, badged, unrunnable, no server hash
    expect(await screen.findByText('corridor_10km', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getAllByText('DEMO').length).toBeGreaterThan(0);
    expect(screen.getAllByText('— demo, no server hash —').length).toBeGreaterThan(0);
    for (const b of screen.getAllByRole('button', { name: 'Run…' })) expect(b).toBeDisabled();

    // the API comes back: the library must swap itself over without a reload
    live = true;
    setOfflineFallback(false);
    expect(await screen.findByText('i24_replica', {}, { timeout: 6000 })).toBeInTheDocument();
    expect(screen.queryByText('corridor_10km')).toBeNull();
    expect(screen.queryByText('DEMO')).toBeNull();
    expect(screen.getByText('realhash1234')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run…' })).toBeEnabled();
  }, 15000);
});

describe('ScenariosView preset marker (API ScenarioOut.preset / PresetOut.preset)', () => {
  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        const method = init?.method ?? 'GET';
        // the API now stamps its own marker on both lists
        if (url.endsWith('/scenarios/preset')) {
          return json([{ ...preset, config_hash: 'presethash01', preset: true }]);
        }
        if (url.endsWith('/scenarios') && method === 'GET') {
          return json([
            {
              scenario_id: 'scn_stored',
              name: 'ring_stored_from_preset',
              config_hash: 'storedhash01',
              created_at: 't',
              config: preset.config,
              preset: false,
            },
          ]);
        }
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setOfflineFallback(false);
  });

  it("follows the server's flag: a stored scenario made from a preset is not a preset", async () => {
    renderView();
    expect(await screen.findByText('ring_stored_from_preset', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText('ring_sugiyama')).toBeInTheDocument();
    // exactly one PRESET badge, and it belongs to the preset-endpoint card
    const badges = screen.getAllByText('PRESET');
    expect(badges.length).toBe(1);
    expect(badges[0].parentElement?.textContent).toContain('ring_sugiyama');
    expect(badges[0].parentElement?.textContent).not.toContain('ring_stored_from_preset');
  });
});

/** An onboarded corridor is an OSM network: a map extract, the edge chain
 * along it, its ramps and its measured boundary. The composer models none of
 * that, and used to coerce the whole block to a 10 km single-lane corridor
 * while printing "Dropped by the network-kind change" — silently throwing the
 * corridor away. It is now a third, read-only kind. */
const osmPreset = {
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
      corridor_edges: ['78288791', '638519815', '996266724'],
      inflow: [[0, 1.1]],
      ramps: [
        { kind: 'on', attach_edge: '638519815' },
        { kind: 'off', attach_edge: '996266724' },
      ],
      boundary: { kind: 'speed_schedule' },
    },
    fleet: { model: 'IDM' },
    av: { penetration: 0, compliance: 1, controller: null },
    sim: { duration_s: 14400, warmup_s: 1800 },
    seed: 42,
    replicates: 20,
  },
};

describe('ScenariosView with an OSM (onboarded) preset', () => {
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
        if (url.endsWith('/scenarios/preset')) return json([osmPreset]);
        if (url.endsWith('/scenarios') && method === 'GET') return json([]);
        if (url.endsWith('/scenarios') && method === 'POST') {
          return json({ scenario_id: 'scn_osm', config_hash: osmPreset.config_hash }, 201);
        }
        if (url.endsWith('/runs') && method === 'POST') return json({ run_id: 'run_new' }, 202);
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setOfflineFallback(false);
  });

  it('keeps the imported network read-only and sends it back unchanged', async () => {
    renderView();
    expect(await screen.findByText('mndot_i94_wb_stpaul', {}, { timeout: 4000 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load in composer' }));

    // the kind is `osm`, and it cannot be switched to a synthetic network
    const kind = screen.getByLabelText('Network kind');
    expect(kind).toHaveValue('osm');
    expect(kind).toBeDisabled();
    // the corridor's own fields are not offered for editing …
    expect(screen.queryByLabelText('Length (m)')).toBeNull();
    expect(screen.queryByLabelText('Lanes')).toBeNull();
    // … they are stated as read-only facts
    const facts = within(screen.getByLabelText('imported network'));
    expect(facts.getByText('data/osm/mndot_i94_wb_stpaul.osm')).toBeInTheDocument();
    expect(facts.getByText('3 edges')).toBeInTheDocument();
    expect(facts.getByText('2')).toBeInTheDocument();
    expect(facts.getByText('measured (speed_schedule)')).toBeInTheDocument();
    // and nothing of the network is reported as dropped
    expect(screen.queryByText(/Dropped by the network-kind change/)).toBeNull();

    // editing a non-network field still creates a scenario, network intact
    fireEvent.change(screen.getByLabelText('AV penetration'), { target: { value: '10' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create scenario' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/scenarios'))).toBe(true);
    });
    const body = calls.find((c) => c.method === 'POST' && c.url.endsWith('/scenarios'))
      ?.body as Record<string, unknown>;
    expect(body.network).toEqual(osmPreset.config.network);
    expect(body.av).toMatchObject({ penetration: 0.1 });
  });

  /** A duration at or inside the warm-up leaves nothing to measure: both
   * replicates die on the worker with "warm-up N s leaves no measurement
   * window in a run recorded over …". The launcher refuses it here. */
  it('refuses a launch whose duration leaves no measurement window', async () => {
    renderView();
    expect(await screen.findByText('mndot_i94_wb_stpaul', {}, { timeout: 4000 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Run…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Launch mndot_i94_wb_stpaul' });

    fireEvent.change(within(dialog).getByLabelText('Duration (s)'), { target: { value: '1800' } });
    expect(
      within(dialog).getByText(/Duration 1800 s leaves no measurement window/),
    ).toHaveTextContent('first 1800 s as warm-up');
    const confirm = within(dialog).getByRole('button', { name: 'Launch run' });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(calls.some((c) => c.method === 'POST')).toBe(false);

    // clear of the warm-up by a measurable window: launchable again
    fireEvent.change(within(dialog).getByLabelText('Duration (s)'), { target: { value: '3600' } });
    expect(within(dialog).getByRole('button', { name: 'Launch run' })).toBeEnabled();
  });
});
