/** The API's `CIOut` has three states and the dashboard must render three
 * (packages/api/api/results.py `ci_to_json`, `api.schemas.CIOut`):
 *
 * - `n >= 20` — a headline-quotable mean with its interval;
 * - `0 < n < 20` — an estimate, flagged UNDERPOWERED;
 * - `n == 0` with `reason: "no_observations"` — *no estimate at all*. A dash
 *   next to "95% CI — – —" reads like a rendering failure; the honest render
 *   says no replicate produced the metric, and never a zero.
 *
 * The mock backend must emit the same three states, or demo mode would hide a
 * state the live API produces. */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { AggregateStat } from '../api/types';
import { MetricCard } from '../components/bits';
import { hasNoObservations } from '../lib/metrics';
import { mockCreateSweep, mockGetRunMetrics } from '../mocks/mockApi';

const quotable: AggregateStat = { mean: 17.6, lo95: 16.9, hi95: 18.3, n: 20, underpowered: false, reason: null };
const underpowered: AggregateStat = { mean: 17.6, lo95: 15.2, hi95: 20.0, n: 8, underpowered: true, reason: null };
const absent: AggregateStat = { mean: null, lo95: null, hi95: null, n: 0, underpowered: false, reason: 'no_observations' };

describe('hasNoObservations', () => {
  it('separates "no estimate" from an underpowered or quotable one', () => {
    expect(hasNoObservations(absent)).toBe(true);
    expect(hasNoObservations(underpowered)).toBe(false);
    expect(hasNoObservations(quotable)).toBe(false);
    expect(hasNoObservations(undefined)).toBe(false);
    // a measured zero is a value, not an absence
    expect(hasNoObservations({ mean: 0, lo95: 0, hi95: 0, n: 20, underpowered: false, reason: null })).toBe(false);
    // an API that predates the `reason` field is still readable
    expect(hasNoObservations({ mean: null, lo95: null, hi95: null, n: 0, underpowered: false })).toBe(true);
  });
});

describe('MetricCard renders all three CIOut states', () => {
  it('quotes a mean with its interval when n >= 20', () => {
    render(<MetricCard metricKey="wave_speed_kmh" stat={quotable} />);
    expect(screen.getByText('17.6')).toBeInTheDocument();
    expect(screen.getByText(/95% CI 16.9 – 18.3 · n=20/)).toBeInTheDocument();
    expect(screen.queryByText('UNDERPOWERED')).toBeNull();
    expect(screen.queryByText('no observations')).toBeNull();
  });

  it('flags an estimate below the reporting standard', () => {
    render(<MetricCard metricKey="wave_speed_kmh" stat={underpowered} />);
    expect(screen.getByText('UNDERPOWERED')).toBeInTheDocument();
    expect(screen.getByText('17.6')).toBeInTheDocument();
    expect(screen.queryByText('no observations')).toBeNull();
  });

  it('says "no observations" for n=0 — no interval, no zero, not underpowered', () => {
    render(<MetricCard metricKey="wave_speed_kmh" stat={absent} />);
    expect(screen.getByText('no observations')).toBeInTheDocument();
    expect(screen.getByText('no replicate produced a value · n=0')).toBeInTheDocument();
    // neither an interval nor the UNDERPOWERED tag: more seeds are not the
    // missing ingredient when nothing was detected at all
    expect(screen.queryByText(/95% CI/)).toBeNull();
    expect(screen.queryByText('UNDERPOWERED')).toBeNull();
    expect(screen.queryByText('0')).toBeNull();
    expect(screen.getByText('NO DATA')).toBeInTheDocument();
  });
});

describe('the mock backend emits the same three states as api.results.ci_to_json', () => {
  it('quotable, underpowered, and no-observation aggregates', async () => {
    // 20 replicates of the baseline corridor run
    const full = await mockGetRunMetrics('run-8f2c11');
    expect(full.aggregate.throughput_veh_h).toMatchObject({ n: 20, underpowered: false, reason: null });
    expect(full.aggregate.throughput_veh_h.mean).not.toBeNull();

    // the macro screening run carries 8 replicates
    const few = await mockGetRunMetrics('run-d0417a');
    expect(few.aggregate.sigma_v_spatial_ms).toMatchObject({ n: 8, underpowered: true, reason: null });
    expect(few.aggregate.sigma_v_spatial_ms.mean).not.toBeNull();

    // a heavily dampened sweep cell detects no wave in any replicate, so the
    // wave-front metrics have nothing to average
    const sweep = await mockCreateSweep({
      scenario_id: 'scn-corridor',
      penetrations: [0.2],
      compliances: [1.0],
      controllers: ['follower_stopper'],
      replicates: 20,
      include_baseline: false,
    });
    const cell = sweep.cells[0];
    expect(cell.aggregate?.wave_count).toMatchObject({ mean: 0, n: 20 });
    expect(cell.aggregate?.wave_speed_kmh).toEqual({
      mean: null,
      lo95: null,
      hi95: null,
      n: 0,
      underpowered: false,
      reason: 'no_observations',
    });
  }, 10000);
});
