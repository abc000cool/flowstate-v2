/** MetricTile + DotStrip (DASHBOARD_DESIGN.md §9.12, §7.6): "±" only for an
 * interval symmetric at the printed precision; a dot per replicate whose
 * tooltip names the seed, on hover and on keyboard focus; and the metric
 * sections of Run detail. */

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { AggregateStat } from '../api/types';
import { MetricCard, MetricTile, replicatePoints, symmetricHalfWidth } from '../components/metrics';
import { groupedMetricKeys } from '../lib/metrics';

const stat = (mean: number, lo95: number, hi95: number, n = 20): AggregateStat => ({
  mean,
  lo95,
  hi95,
  n,
  underpowered: n < 20,
  reason: null,
});

describe('symmetric half-width', () => {
  it('prints "mean ± hw" when the interval is symmetric', () => {
    render(<MetricTile metricKey="wave_speed_kmh" stat={stat(17.6, 16.9, 18.3)} />);
    expect(screen.getByText('17.6')).toBeInTheDocument();
    expect(screen.getByText('± 0.7')).toBeInTheDocument();
    expect(screen.getByText('km/h')).toBeInTheDocument();
    expect(screen.getByText('95% CI 16.9 – 18.3 · n=20')).toBeInTheDocument();
  });

  it('never prints a symmetric "±" for an asymmetric interval', () => {
    render(<MetricTile metricKey="wave_speed_kmh" stat={stat(10, 8, 13)} />);
    expect(screen.getByText('10.0')).toBeInTheDocument();
    expect(screen.queryByText(/±/)).toBeNull();
    // the CI line carries the interval
    expect(screen.getByText('95% CI 8.0 – 13.0 · n=20')).toBeInTheDocument();
  });

  it('allows half a unit of the last printed digit, and no more', () => {
    // digits = 1: tolerance 0.05
    expect(symmetricHalfWidth(stat(10, 9, 11.05), 1)).toBeCloseTo(1.025, 9);
    expect(symmetricHalfWidth(stat(10, 9, 11.06), 1)).toBeNull();
    // digits = 0 (throughput): tolerance 0.5
    expect(symmetricHalfWidth(stat(1842, 1811, 1873.4), 0)).not.toBeNull();
    expect(symmetricHalfWidth(stat(1842, 1811, 1874), 0)).toBeNull();
    // n = 1: no interval, no "±"
    expect(symmetricHalfWidth({ ...stat(5, 0, 0, 1), lo95: null, hi95: null }, 1)).toBeNull();
  });

  it('keeps the MetricCard name reachable', () => {
    expect(MetricCard).toBe(MetricTile);
  });
});

describe('dot strip', () => {
  const points = [
    { seed: 2001, value: 18.0 },
    { seed: 2000, value: 17.2 },
    { seed: 2002, value: 17.6 },
  ];

  it('draws one dot per replicate and names each by its seed on focus', () => {
    render(<MetricTile metricKey="wave_speed_kmh" stat={stat(17.6, 16.9, 18.3)} replicates={points} />);
    const strip = screen.getByRole('group', { name: 'WAVE SPEED: 3 replicates' });
    const dots = strip.querySelectorAll<SVGGElement>('g.dot');
    expect(dots).toHaveLength(3);
    // one tab stop for the strip: the first dot
    expect([...dots].map((d) => d.getAttribute('tabindex'))).toEqual(['0', '-1', '-1']);
    expect(dots[1]).toHaveAttribute('aria-label', 'seed 2000 · 17.2 km/h');

    act(() => dots[0].focus());
    expect(screen.getByRole('tooltip')).toHaveTextContent('seed 2001 · 18.0 km/h');
    // arrows move in value order: 18.0 is the largest, so Right stays, Left
    // goes to 17.6, then 17.2
    fireEvent.keyDown(strip, { key: 'ArrowLeft' });
    expect(screen.getByRole('tooltip')).toHaveTextContent('seed 2002 · 17.6 km/h');
    fireEvent.keyDown(strip, { key: 'Home' });
    expect(screen.getByRole('tooltip')).toHaveTextContent('seed 2000 · 17.2 km/h');
    expect(dots[1]).toHaveAttribute('tabindex', '0');
  });

  it('shows the same tooltip on hover', () => {
    render(<MetricTile metricKey="wave_speed_kmh" stat={stat(17.6, 16.9, 18.3)} replicates={points} />);
    const dot = screen.getByLabelText('seed 2002 · 17.6 km/h');
    fireEvent.pointerEnter(dot);
    expect(screen.getByRole('tooltip')).toHaveTextContent('seed 2002 · 17.6 km/h');
    fireEvent.pointerLeave(dot);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('has no strip without replicates or for a metric with no observations', () => {
    const { container } = render(
      <MetricTile
        metricKey="wave_speed_kmh"
        stat={{ mean: null, lo95: null, hi95: null, n: 0, underpowered: false, reason: 'no_observations' }}
        replicates={points}
      />,
    );
    expect(container.querySelector('.dot-strip')).toBeNull();
    render(<MetricTile metricKey="wave_speed_kmh" stat={stat(17.6, 16.9, 18.3)} />);
    expect(screen.queryByRole('group')).toBeNull();
  });

  it('takes replicate values with their seeds, skipping replicates with none', () => {
    expect(
      replicatePoints(
        [
          { seed: 1, metrics: { wave_speed_kmh: 17 } },
          { seed: 2, metrics: { wave_speed_kmh: null } },
          { seed: 3, metrics: {} },
        ],
        'wave_speed_kmh',
      ),
    ).toEqual([{ seed: 1, value: 17 }]);
  });
});

describe('metric sections', () => {
  it('groups keys into flow, stability, energy, exposure, then other', () => {
    const groups = groupedMetricKeys([
      'vmt_veh_km',
      'wave_speed_kmh',
      'throughput_veh_h',
      'fuel_ml_per_veh_km',
      'brand_new_metric',
      'wave_amplitude_ms',
      'sigma_v_spatial_ms',
    ]);
    expect(groups.map((g) => g.title)).toEqual([
      'Traffic flow',
      'Stability',
      'Energy',
      'Exposure and sample',
      'Other',
    ]);
    expect(groups[1].keys).toEqual(['sigma_v_spatial_ms', 'wave_amplitude_ms', 'wave_speed_kmh']);
    expect(groups[4].keys).toEqual(['brand_new_metric']);
  });

  it('drops empty sections', () => {
    expect(groupedMetricKeys(['fuel_ml_per_veh_km']).map((g) => g.group)).toEqual(['energy']);
    expect(groupedMetricKeys([])).toEqual([]);
  });

  it('renders the tile eyebrow, badge and CI line for an underpowered estimate', () => {
    render(<MetricTile metricKey="throughput_veh_h" stat={stat(1842, 1811, 1873, 8)} />);
    const tile = screen.getByText('THROUGHPUT').closest('.metric-tile') as HTMLElement;
    expect(within(tile).getByText('UNDERPOWERED')).toBeInTheDocument();
    expect(within(tile).getByText('1842')).toBeInTheDocument();
    expect(within(tile).getByText('± 31')).toBeInTheDocument();
    expect(within(tile).getByText('95% CI 1811 – 1873 · n=8')).toBeInTheDocument();
  });
});
