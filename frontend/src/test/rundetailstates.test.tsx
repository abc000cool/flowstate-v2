/** Run detail's failure states against the real API's shapes
 * (DASHBOARD_DESIGN.md §9.10, §10.4): a failed heatmap or metrics fetch is a
 * persistent danger callout with the server's words and a Retry, never the
 * old endless "loading field…"; Retry fetches again and the content
 * replaces the callout. */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, setOfflineFallback } from '../api/client';
import { AppStateProvider } from '../components/AppContext';
import { RunDetailView } from '../views/RunDetailView';

const run = {
  run_id: 'run-live1',
  scenario_id: 'scn-corridor',
  scenario_name: 'corridor_10km',
  status: 'done',
  progress: { completed_replicates: 2, total_replicates: 2 },
  config_hash: '3f9a0b1c2d3e4f5a6b7c8d9e0f1ac21e',
  seeded: false,
  tier: 'micro',
  seeds: [2000, 2001],
};

const metrics = {
  replicates: [
    { seed: 2000, metrics: { sigma_v_spatial_ms: 3.1 } },
    { seed: 2001, metrics: { sigma_v_spatial_ms: 3.3 } },
  ],
  aggregate: {
    sigma_v_spatial_ms: { mean: 3.2, lo95: 1.93, hi95: 4.47, n: 2, underpowered: true, reason: null },
  },
};

const heatmap = { t_bins: [30, 90], x_bins: [250, 750], values: [[10, 20], [null, 5]] };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

describe('RunDetailView failure states', () => {
  let heatmapFails = true;
  let metricsFail = false;
  const calls: string[] = [];

  beforeAll(() => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  });

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    heatmapFails = true;
    metricsFail = false;
    calls.length = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        calls.push(url);
        if (url.endsWith('/runs/run-live1')) return json(run);
        if (url.includes('/runs/run-live1/heatmap')) {
          return heatmapFails ? json({ detail: 'edges.parquet is missing' }, 500) : json(heatmap);
        }
        if (url.endsWith('/runs/run-live1/metrics')) {
          return metricsFail ? json({ detail: 'metrics not computed' }, 409) : json(metrics);
        }
        return json({ detail: `unexpected ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const renderRun = (): void => {
    render(
      <AppStateProvider>
        <MemoryRouter initialEntries={['/runs/run-live1']}>
          <Routes>
            <Route path="/runs/:runId" element={<RunDetailView />} />
          </Routes>
        </MemoryRouter>
      </AppStateProvider>,
    );
  };

  it('turns a failed heatmap fetch into a callout with Retry', async () => {
    renderRun();
    expect(
      await screen.findByText('The speed field could not be loaded.', {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(screen.getByText('HTTP 500 — edges.parquet is missing')).toBeInTheDocument();
    expect(screen.queryByText(/Loading the speed field/)).toBeNull();
    // the CSV twin has nothing to write yet
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeDisabled();

    heatmapFails = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(
      await screen.findByRole('img', { name: /^Space–time speed field/ }, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(screen.queryByText('The speed field could not be loaded.')).toBeNull();
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeEnabled();
    expect(calls.filter((u) => u.includes('/heatmap')).length).toBe(2);
  });

  it('turns a failed metrics fetch into a callout with Retry', async () => {
    heatmapFails = false;
    metricsFail = true;
    renderRun();
    expect(
      await screen.findByText('The metrics could not be loaded.', {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(screen.getByText('HTTP 409 — metrics not computed')).toBeInTheDocument();

    metricsFail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    // the tile, its section and its replicate strip arrive
    expect(await screen.findByText('σ_v SPATIAL', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Stability' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'σ_v SPATIAL: 2 replicates' })).toBeInTheDocument();
    expect(screen.getByText('Metrics · mean ± 95% CI over 2 replicates')).toBeInTheDocument();
    // a symmetric interval prints its half-width
    expect(screen.getByText('± 1.27')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('The metrics could not be loaded.')).toBeNull());
  });

  it('shows the config hash truncated with a copy button, and the seeds on demand', async () => {
    heatmapFails = false;
    renderRun();
    expect(await screen.findByText('3f9a…c21e', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy config hash' })).toBeInTheDocument();
    const seeds = screen.getByRole('button', { name: 'Seeds: 2' });
    expect(seeds).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(seeds);
    expect(screen.getByText('2000 · 2001')).toBeInTheDocument();
  });
});
