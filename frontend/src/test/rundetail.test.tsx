/** Render smoke of the Run detail view against the mock backend. */

import { render, screen, within } from '@testing-library/react';
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
    expect(screen.getAllByText('σ_v SPATIAL').length).toBeGreaterThan(0);
    // every API metric has a display definition — no generic fallback labels
    expect(screen.queryByText('throughput veh h')).toBeNull();
    expect(screen.getAllByText(/95% CI/).length).toBeGreaterThan(0);
    // heatmap field toggle present
    expect(screen.getByRole('tab', { name: 'SPEED' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'DENSITY' })).toBeInTheDocument();
    // a micro run has no fundamental diagram, so no FD provenance is claimed
    expect(screen.queryByText(/v1_legacy/)).toBeNull();
    // this demo run carries one ramp meter and one weaving section, so the
    // merge diagnostics section shows both tables, labelled with their seed
    expect(await screen.findByText('Merge diagnostics · seed 2000', {}, { timeout: 4000 }))
      .toBeInTheDocument();
    const meters = within(screen.getByLabelText('ramp meters'));
    expect(meters.getByText('Passed unstoppable')).toBeInTheDocument();
    expect(meters.getByText('412')).toBeInTheDocument();
    const weaves = within(screen.getByLabelText('weaving sections'));
    expect(weaves.getByText('Deferred (vehicle-steps)')).toBeInTheDocument();
    expect(weaves.getByText('143 / 147')).toBeInTheDocument();
  });

  /** A failed run answers *why*: the reason is in `RunOut.error`, which this
   * view already fetched, and sending the user to the server logs for it is
   * the defect being fixed. */
  it('shows the service’s reason for a failed run', async () => {
    render(
      <AppStateProvider>
        <MemoryRouter initialEntries={['/runs/run-e2190c']}>
          <Routes>
            <Route path="/runs/:runId" element={<RunDetailView />} />
          </Routes>
        </MemoryRouter>
      </AppStateProvider>,
    );

    expect(await screen.findByText('Run failed', {}, { timeout: 4000 })).toBeInTheDocument();
    const reason = await screen.findByText(
      /leaves no measurement window/,
      {},
      { timeout: 4000 },
    );
    expect(reason.textContent).toContain('ValueError');
    // demo rows say so and never print a config hash no server holds
    expect(screen.getAllByText('DEMO').length).toBeGreaterThan(0);
    expect(screen.getByText('— demo, no server hash —')).toBeInTheDocument();
  });

  it('names the fundamental diagram a macro run used, and flags the preset', async () => {
    render(
      <AppStateProvider>
        <MemoryRouter initialEntries={['/runs/run-d0417a']}>
          <Routes>
            <Route path="/runs/:runId" element={<RunDetailView />} />
          </Routes>
        </MemoryRouter>
      </AppStateProvider>,
    );

    expect(await screen.findByText('MACRO SCREENING', {}, { timeout: 4000 })).toBeInTheDocument();
    // the FD is the calibration every screening number rests on: named, and
    // marked uncalibrated when it is the documented v1_legacy preset
    const fd = await screen.findByText(/v1_legacy preset/, {}, { timeout: 4000 });
    expect(fd).toBeInTheDocument();
    expect(fd.textContent).toContain('(uncalibrated)');
    // a run with no ramp meter and no weaving section has no merge
    // diagnostics to show — the section is absent, not empty
    expect(screen.queryByTestId('merge-diagnostics')).toBeNull();
  });
});
