/** Render smoke of the Run detail view against the mock backend. */

import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeAll, describe, expect, it } from 'vitest';
import { setOfflineFallback } from '../api/client';
import { AppStateProvider } from '../components/AppContext';
import { RunDetailView } from '../views/RunDetailView';

describe('RunDetailView (mock data)', () => {
  beforeAll(() => {
    // force the demo backend, as the offline auto-fallback would
    setOfflineFallback(true);
  });

  it('renders header badges, metrics with CIs, and honesty labels', async () => {
    render(
      <AppStateProvider>
        <MemoryRouter initialEntries={['/runs/run-a41d09']}>
          <Routes>
            <Route path="/runs/:runId" element={<RunDetailView />} />
          </Routes>
        </MemoryRouter>
      </AppStateProvider>,
    );

    // run header arrives after the mock getRun latency
    expect(await screen.findByText('run-a41d09', {}, { timeout: 4000 })).toBeInTheDocument();
    // honesty labels: seeded run must carry the amber SEEDED tag; micro badge
    expect(await screen.findByText('SEEDED', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText('MICRO')).toBeInTheDocument();
    // aggregate metric cards (mean ± CI) after metrics resolve
    const throughputs = await screen.findAllByText('THROUGHPUT', {}, { timeout: 4000 });
    expect(throughputs.length).toBeGreaterThan(0);
    expect(screen.getAllByText('σ_v').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/95% CI/).length).toBeGreaterThan(0);
    // heatmap field toggle present
    expect(screen.getByRole('tab', { name: 'SPEED' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'DENSITY' })).toBeInTheDocument();
  });
});
