/** The sweep matrix as an instrument (DASHBOARD_DESIGN.md §7.4, §10.5):
 * cells binned blue/red by signed improvement, every cell a keyboard-
 * focusable button whose tooltip shows on focus as on hover, and a getSweep
 * failure that stays on screen as a callout with Retry. */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, setOfflineFallback } from '../api/client';
import { SweepsView } from '../views/SweepsView';

function ci(mean: number, n = 20) {
  return { mean, lo95: mean * 0.95, hi95: mean * 1.05, n, underpowered: n < 20, reason: null };
}

function aggregate(sigma: number, throughput: number) {
  return {
    throughput_veh_h: ci(throughput),
    mean_tt_s: ci(500),
    p90_tt_s: ci(600),
    sigma_v_spatial_ms: ci(sigma),
    sigma_v_temporal_ms: ci(sigma * 0.8),
    vmt_veh_km: ci(5800),
    vht_veh_h: ci(80),
    fuel_ml_per_veh_km: ci(60),
    wave_count: ci(3),
    wave_speed_kmh: ci(17),
    wave_amplitude_ms: ci(10),
  };
}

const progress = { completed_replicates: 20, total_replicates: 20 };

/** Baseline σ_v 5.8; grid cells at -50 %, -15 %, +1 % and +30 % of it. */
const sweepOut = {
  sweep_id: 'swp-bins',
  scenario_id: 'scn-corridor',
  status: 'done',
  tier: 'micro',
  error: null,
  cells: [
    { penetration: 0, compliance: 1.0, controller: 'follower_stopper', config_hash: 'b0', run_id: 'run-base', status: 'done', progress, aggregate: aggregate(5.8, 1700) },
    { penetration: 0.05, compliance: 0.5, controller: 'follower_stopper', config_hash: 'c1', run_id: 'run-1', status: 'done', progress, aggregate: aggregate(2.9, 1785) },
    { penetration: 0.05, compliance: 1.0, controller: 'follower_stopper', config_hash: 'c2', run_id: 'run-2', status: 'done', progress, aggregate: aggregate(4.93, 1600) },
    { penetration: 0.1, compliance: 0.5, controller: 'follower_stopper', config_hash: 'c3', run_id: 'run-3', status: 'done', progress, aggregate: aggregate(5.858, 1702) },
    { penetration: 0.1, compliance: 1.0, controller: 'follower_stopper', config_hash: 'c4', run_id: 'run-4', status: 'done', progress, aggregate: aggregate(7.54, 1500) },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

describe('sweep matrix', () => {
  let failSweep = false;
  let sweepGets = 0;

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    failSweep = false;
    sweepGets = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        if (url.endsWith('/scenarios')) return json([]);
        if (url.endsWith('/sweeps/swp-bins')) {
          sweepGets += 1;
          return failSweep ? json({ detail: 'sweep store unavailable' }, 503) : json(sweepOut);
        }
        return json({ detail: `unexpected ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const renderAt = (): void => {
    render(
      <MemoryRouter initialEntries={['/sweeps?sweep=swp-bins']}>
        <SweepsView />
      </MemoryRouter>,
    );
  };

  it('bins each cell by signed improvement, blue better and red worse', async () => {
    renderAt();
    const cellBtn = (pct: string): HTMLElement =>
      screen.getByText(pct, { selector: '.d' }).closest('button') as HTMLElement;
    await screen.findByText('-50.0%', {}, { timeout: 4000 });
    // σ_v: down is better
    expect(cellBtn('-50.0%')).toHaveClass('cell-btn', 'delta-b4');
    expect(cellBtn('-15.0%')).toHaveClass('delta-b2');
    expect(cellBtn('+1.0%')).toHaveClass('delta-n');
    expect(cellBtn('+30.0%')).toHaveClass('delta-w3');
    // throughput: up is better, so the same arms swap meaning
    fireEvent.change(screen.getByLabelText('Metric'), { target: { value: 'throughput_veh_h' } });
    expect(cellBtn('+5.0%')).toHaveClass('delta-b1');
    expect(cellBtn('-11.8%')).toHaveClass('delta-w2');
    // the legend states the classes and the secondary encoding
    expect(screen.getByText('≡ identical realisation')).toBeInTheDocument();
    expect(screen.getByText(/Blue = better, red = worse/)).toBeInTheDocument();
  });

  it('makes every cell a button whose tooltip shows on keyboard focus', async () => {
    renderAt();
    await screen.findByText('-50.0%', {}, { timeout: 4000 });
    const cell = screen.getByRole('button', { name: 'p=5% c=50%: -50.0% vs baseline, n=20' });
    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.focus(cell);
    const tip = screen.getByRole('tooltip');
    expect(tip).toHaveTextContent('p=5% · c=50%');
    expect(tip).toHaveTextContent('σ_v SPATIAL 2.90 m/s');
    expect(tip).toHaveTextContent(/95% CI .* · n=20/);
    fireEvent.blur(cell);
    expect(screen.queryByRole('tooltip')).toBeNull();
    // the baseline is a button too, named as the reference
    expect(screen.getByRole('button', { name: 'baseline p=0% c=100%: 5.80 m/s, n=20' })).toBeInTheDocument();
  });

  it('keeps a getSweep failure on screen with Retry, not a forever "collecting"', async () => {
    failSweep = true;
    renderAt();
    expect(
      await screen.findByText('This sweep could not be loaded.', {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(screen.getByText('HTTP 503 — sweep store unavailable')).toBeInTheDocument();
    expect(screen.queryByText(/collecting sweep cells/)).toBeNull();

    failSweep = false;
    const before = sweepGets;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('-50.0%', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(sweepGets).toBeGreaterThan(before);
    await waitFor(() => expect(screen.queryByText('This sweep could not be loaded.')).toBeNull());
  });
});
