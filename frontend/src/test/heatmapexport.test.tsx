/** Run detail's "Download CSV" against the real API's shapes: the file it
 * writes carries the provenance `GET /runs/{id}/heatmap` answered with (seed,
 * config hash, tier) and the run's own labels, so a macro run's field leaves
 * the dashboard labelled "screening" (CLAUDE.md §5.6) and any export names
 * the one replicate it holds (§0.5). The download itself is stubbed: jsdom
 * cannot save a file. */

import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, setOfflineFallback } from '../api/client';
import { AppStateProvider } from '../components/AppContext';
import { saveText } from '../lib/download';
import { RunDetailView } from '../views/RunDetailView';

vi.mock('../lib/download', () => ({
  REVOKE_DELAY_MS: 10_000,
  saveBlob: vi.fn(),
  saveText: vi.fn(),
}));

const run = {
  run_id: 'run-m1',
  scenario_id: 'scn-corridor',
  scenario_name: 'corridor_10km',
  status: 'done',
  progress: { completed_replicates: 3, total_replicates: 3 },
  config_hash: '3f9a0b1c2d3e4f5a6b7c8d9e0f1ac21e',
  seeded: true,
  tier: 'macro',
  seeds: ['7', '8', '9'],
};

const metrics = {
  replicates: [{ seed: '7', metrics: { sigma_v_spatial_ms: 3.1 } }],
  aggregate: {
    sigma_v_spatial_ms: { mean: 3.1, lo95: null, hi95: null, n: 1, underpowered: true, reason: null },
  },
  fd_source: 'v1_legacy preset',
};

/** `HeatmapOut`: the first seed's field, with its provenance. */
const heatmap = {
  run_id: 'run-m1',
  config_hash: run.config_hash,
  seed: '7',
  field: 'speed',
  tier: 'macro',
  t_bins: [30, 90],
  x_bins: [250, 750],
  values: [
    [10, 20],
    [null, 5],
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

describe('RunDetailView CSV export', () => {
  beforeAll(() => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  });

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    vi.mocked(saveText).mockClear();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        if (url.endsWith('/runs/run-m1')) return json(run);
        if (url.includes('/runs/run-m1/heatmap')) return json(heatmap);
        if (url.endsWith('/runs/run-m1/metrics')) return json(metrics);
        return json({ detail: `unexpected ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('writes a macro run as one labelled screening replicate', async () => {
    render(
      <AppStateProvider>
        <MemoryRouter initialEntries={['/runs/run-m1']}>
          <Routes>
            <Route path="/runs/:runId" element={<RunDetailView />} />
          </Routes>
        </MemoryRouter>
      </AppStateProvider>,
    );
    await screen.findByRole('img', { name: /^Space–time speed field/ }, { timeout: 4000 });
    fireEvent.click(screen.getByRole('button', { name: 'Download CSV' }));

    expect(saveText).toHaveBeenCalledTimes(1);
    const [text, filename, mime] = vi.mocked(saveText).mock.calls[0];
    expect(filename).toBe('flowstate-run-m1-speed-seed7-screening.csv');
    expect(mime).toBe('text/csv');
    const meta = text.split('\n').filter((l) => l.startsWith('#'));
    expect(meta).toContain('# run_id: run-m1');
    expect(meta).toContain('# seed: 7');
    expect(meta).toContain(`# config_hash: ${run.config_hash}`);
    expect(meta).toContain('# seeded: true');
    expect(meta.some((l) => l.startsWith('# tier: screening'))).toBe(true);
    expect(meta.some((l) => l.startsWith('# replicate: 1 of 3'))).toBe(true);
    // the data columns are the ones the design fixed
    expect(text).toContain('\nt_s,x_m,speed_ms\n30,250,10\n');
  });
});
