/** ScenariosView against the real API's shapes (presets carry no scenario_id). */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setOfflineFallback } from '../api/client';
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

describe('ScenariosView (real API shapes)', () => {
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
  });

  it('stores a preset first, then launches the run with the stored id', async () => {
    render(
      <AppStateProvider>
        <MemoryRouter initialEntries={['/scenarios']}>
          <ScenariosView />
        </MemoryRouter>
      </AppStateProvider>,
    );
    expect(await screen.findByText('ring_sugiyama', {}, { timeout: 4000 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Run' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/runs'))).toBe(true);
    });
    const stored = calls.find((c) => c.method === 'POST' && c.url.endsWith('/scenarios'));
    expect(stored?.body).toMatchObject({ name: 'ring_sugiyama' });
    const run = calls.find((c) => c.method === 'POST' && c.url.endsWith('/runs'));
    expect(run?.body).toMatchObject({ scenario_id: 'scn_new', replicates: 3 });
  });
});
