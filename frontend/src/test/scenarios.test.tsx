/** ScenariosView against the real API's shapes (presets carry no scenario_id),
 * plus the honesty rules: demo cards are labelled and replaced when the API
 * comes back, the launcher states its cost before enqueueing anything, and the
 * composer carries unmodelled preset fields through unchanged. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, setOfflineFallback } from '../api/client';
import { AppStateProvider } from '../components/AppContext';
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

  it('badges a preset from the preset endpoint (the API sends no `preset` field)', async () => {
    renderView();
    expect(await screen.findByText('ring_sugiyama', {}, { timeout: 4000 })).toBeInTheDocument();
    // PresetOut has no `preset` key — the badge must come from the source list
    expect(preset).not.toHaveProperty('preset');
    expect(screen.getByText('PRESET')).toBeInTheDocument();
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
    fireEvent.change(within(dialog).getByLabelText('Duration (s)'), { target: { value: '120' } });
    fireEvent.change(within(dialog).getByLabelText('Seed'), { target: { value: '7' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch run' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/runs'))).toBe(true);
    });
    const run = calls.find((c) => c.method === 'POST' && c.url.endsWith('/runs'));
    expect(run?.body).toMatchObject({
      scenario_id: 'scn_new',
      replicates: 20,
      overrides: { sim: { duration_s: 120 }, seed: 7 },
    });
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
