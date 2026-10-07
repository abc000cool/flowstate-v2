/** HeatmapCanvas (DASHBOARD_DESIGN.md §7.3): the dual-unit legend with the
 * default wave-threshold notch and the "no data" key, the readout row driven
 * by the keyboard (arrows = 1 bin, Shift = 10, Home/End = time edges), the
 * accessible name, and the CSV twin of the binned field. Canvas painting is
 * not exercised: jsdom has no 2D context. */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import type { Heatmap } from '../api/types';
import {
  formatBinValue,
  HeatmapCanvas,
  heatmapCSV,
  heatmapCSVFilename,
  heatmapExtent,
  heatmapHeight,
  isEmptyHeatmap,
  RampLegend,
} from '../components/HeatmapCanvas';

// jsdom has no 2D context; say so quietly instead of logging "not implemented"
beforeAll(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
});
afterAll(() => {
  vi.restoreAllMocks();
});

/** 3 time bins of 60 s × 2 position bins of 500 m; one empty bin. */
const FIELD: Heatmap = {
  t_bins: [30, 90, 150],
  x_bins: [250, 750],
  values: [
    [10, null],
    [20, 5],
    [33.3, 0],
  ],
};

describe('RampLegend', () => {
  it('reads speed in km/h above and mph below, with the default wave threshold', () => {
    render(<RampLegend field="speed" />);
    expect(screen.getByText('km/h')).toBeInTheDocument();
    expect(screen.getByText('mph')).toBeInTheDocument();
    for (const t of ['20', '40', '60', '80', '100', '120', '10', '30', '50', '70']) {
      expect(screen.getAllByText(t).length).toBeGreaterThan(0);
    }
    // 0 is on both scales
    expect(screen.getAllByText('0')).toHaveLength(2);
    // "default": a run may configure another v_jam_thresh
    expect(screen.getByText('wave threshold 40 km/h (default)')).toBeInTheDocument();
    expect(screen.getByText('no data')).toBeInTheDocument();
  });

  it('places each mph tick where that speed falls on the km/h bar', () => {
    const { container } = render(<RampLegend field="speed" />);
    const bottom = container.querySelectorAll('.rl-ticks-bottom .rl-tick');
    const seventy = [...bottom].find((el) => el.textContent === '70') as HTMLElement;
    // 70 mph = 112.65 km/h of 120
    expect(parseFloat(seventy.style.left)).toBeCloseTo((70 * 1.609344 * 100) / 120, 6);
  });

  it('reads density in veh/km and veh/mi, with no speed threshold', () => {
    render(<RampLegend field="density" />);
    expect(screen.getByText('veh/km')).toBeInTheDocument();
    expect(screen.getByText('veh/mi')).toBeInTheDocument();
    expect(screen.getByText('160')).toBeInTheDocument();
    expect(screen.getByText('250')).toBeInTheDocument();
    expect(screen.queryByText(/wave threshold/)).toBeNull();
    expect(screen.getByText('no data')).toBeInTheDocument();
  });
});

describe('HeatmapCanvas inspect', () => {
  it('names the plot, and scrubs bins from the keyboard into the readout row', () => {
    render(<HeatmapCanvas heatmap={FIELD} field="speed" />);
    const plot = screen.getByRole('img', {
      name: 'Space–time speed field, 0–3 min, 0–1 km. Colour scale 0–120 km/h (0–75 mph).',
    });
    // idle: says how to read values
    expect(screen.getByText(/Hover the field, or focus it and use the arrow keys/)).toBeInTheDocument();

    fireEvent.focus(plot);
    // focus starts at the center bin: t 90 s, x 750 m, 5 m/s
    const readout = screen.getByText('t 1.5 min · x 0.8 km · 18 km/h (11 mph)');
    expect(readout).toHaveAttribute('aria-live', 'polite');

    fireEvent.keyDown(plot, { key: 'ArrowRight' });
    expect(screen.getByText('t 2.5 min · x 0.8 km · 0 km/h (0 mph)')).toBeInTheDocument();
    fireEvent.keyDown(plot, { key: 'ArrowDown' });
    expect(screen.getByText('t 2.5 min · x 0.3 km · 120 km/h (74 mph)')).toBeInTheDocument();
    fireEvent.keyDown(plot, { key: 'Home' });
    expect(screen.getByText('t 0.5 min · x 0.3 km · 36 km/h (22 mph)')).toBeInTheDocument();
    // downstream is up: ArrowUp moves to the larger position, an empty bin
    fireEvent.keyDown(plot, { key: 'ArrowUp' });
    expect(screen.getByText('t 0.5 min · x 0.8 km · no data')).toBeInTheDocument();
    // Shift moves 10 bins, clamped to the field
    fireEvent.keyDown(plot, { key: 'ArrowRight', shiftKey: true });
    expect(screen.getByText('t 2.5 min · x 0.8 km · 0 km/h (0 mph)')).toBeInTheDocument();
    fireEvent.keyDown(plot, { key: 'Home' });
    fireEvent.keyDown(plot, { key: 'End' });
    expect(screen.getByText('t 2.5 min · x 0.8 km · 0 km/h (0 mph)')).toBeInTheDocument();

    // leaving the plot ends the live region and the inspection
    fireEvent.blur(plot);
    expect(screen.getByText(/Hover the field/)).toHaveAttribute('aria-live', 'off');
  });

  it('reads density in both units', () => {
    expect(formatBinValue('density', 0.038)).toBe('38 veh/km (61 veh/mi)');
    expect(formatBinValue('speed', null)).toBe('no data');
  });
});

describe('heatmap helpers', () => {
  it('writes the binned field as SI CSV, empty for a bin with no vehicle', () => {
    const csv = heatmapCSV(FIELD, 'speed');
    const lines = csv.trimEnd().split('\n');
    expect(lines[0]).toBe('t_s,x_m,speed_ms');
    expect(lines).toHaveLength(1 + 3 * 2);
    expect(lines[1]).toBe('30,250,10');
    expect(lines[2]).toBe('30,750,');
    expect(lines[6]).toBe('150,750,0');
    expect(heatmapCSV(FIELD, 'density').split('\n')[0]).toBe('t_s,x_m,density_vehm');
    expect(heatmapCSVFilename('run-a41d09', 'density')).toBe('flowstate-run-a41d09-density.csv');
  });

  it('extends bin centers by half a bin to the field edges', () => {
    expect(heatmapExtent(FIELD)).toEqual({ t0: 0, t1: 180, x0: 0, x1: 1000 });
  });

  it('knows an empty field', () => {
    expect(isEmptyHeatmap({ t_bins: [], x_bins: [], values: [] })).toBe(true);
    expect(isEmptyHeatmap({ t_bins: [0], x_bins: [], values: [[]] })).toBe(true);
    expect(isEmptyHeatmap(FIELD)).toBe(false);
  });

  it('sizes the plot clamp(300, 0.42 w, 460), 240 floor on narrow containers', () => {
    expect(heatmapHeight(1440)).toBe(460);
    expect(heatmapHeight(900)).toBe(378);
    expect(heatmapHeight(720)).toBe(302);
    expect(heatmapHeight(600)).toBe(252);
    expect(heatmapHeight(400)).toBe(240);
  });
});
