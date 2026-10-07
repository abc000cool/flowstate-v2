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
  heatmapExport,
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

/** The CSV is one replicate's field, and a file outlives the page it came
 * from: without the seed, config hash and tier it cannot be traced to the run
 * that produced it (CLAUDE.md §0.5), and a macro export loses the
 * "screening" label every CTM output must carry (§5.6). The provenance rides
 * in `#` comment lines ahead of the unchanged data block, and the file name
 * carries the seed and the tier. */
describe('heatmap CSV provenance', () => {
  const AT = new Date('2026-10-07T14:02:31Z');
  /** What `GET /runs/{id}/heatmap` answers: the field plus its provenance. */
  const MACRO: Heatmap = {
    ...FIELD,
    run_id: 'run-m1',
    config_hash: '3f9a0b1c2d3e',
    seed: 43,
    field: 'speed',
    tier: 'macro',
  };
  const MACRO_RUN = { tier: 'macro' as const, config_hash: '3f9a0b1c2d3e', seeds: [42, 43, 44], seeded: false };

  const metaOf = (text: string): string[] => text.split('\n').filter((l) => l.startsWith('#'));
  const valueOf = (text: string, key: string): string | undefined =>
    metaOf(text)
      .find((l) => l.startsWith(`# ${key}: `))
      ?.slice(`# ${key}: `.length);

  it('labels a macro export screening, with its seed, replicate, hash, units, bins and time', () => {
    const { filename, text } = heatmapExport(MACRO, 'speed', {
      runId: 'run-m1',
      run: MACRO_RUN,
      exportedAt: AT,
    });
    expect(filename).toBe('flowstate-run-m1-speed-seed43-screening.csv');
    expect(valueOf(text, 'run_id')).toBe('run-m1');
    expect(valueOf(text, 'tier')).toMatch(/^screening \(macro/);
    expect(valueOf(text, 'seed')).toBe('43');
    expect(valueOf(text, 'replicate')).toMatch(/^2 of 3 /);
    expect(valueOf(text, 'seeded')).toBe('false');
    expect(valueOf(text, 'config_hash')).toBe('3f9a0b1c2d3e');
    expect(valueOf(text, 'field')).toMatch(/^speed/);
    expect(valueOf(text, 'units')).toMatch(/t_s = s.*x_m = m.*speed_ms = m\/s/);
    expect(valueOf(text, 't_bin_s')).toBe('60');
    expect(valueOf(text, 'x_bin_m')).toBe('500');
    expect(valueOf(text, 'exported_at')).toBe('2026-10-07T14:02:31.000Z');
    expect(valueOf(text, 'source')).toMatch(/^server/);

    // the data block is exactly the unchanged CSV, right after the metadata
    const meta = metaOf(text);
    expect(text.split('\n').slice(0, meta.length)).toEqual(meta);
    expect(text.split('\n').slice(meta.length).join('\n')).toBe(heatmapCSV(MACRO, 'speed'));
  });

  it('names a micro export by seed and tier', () => {
    const micro: Heatmap = { ...MACRO, tier: 'micro', seed: 2000 };
    const { filename, text } = heatmapExport(micro, 'density', {
      runId: 'run-a41d09',
      run: { ...MACRO_RUN, tier: 'micro', seeds: [2000, 2001] },
      exportedAt: AT,
    });
    expect(filename).toBe('flowstate-run-a41d09-density-seed2000-micro.csv');
    expect(heatmapCSVFilename({ runId: 'run-a41d09', field: 'density', seed: 2000, tier: 'micro', demo: false })).toBe(
      filename,
    );
    expect(valueOf(text, 'tier')).toMatch(/^micro/);
    expect(valueOf(text, 'units')).toMatch(/density_vehm = veh\/m/);
    expect(text).toContain('\nt_s,x_m,density_vehm\n');
  });

  it('falls back to the run for tier and hash, and never invents a seed', () => {
    // an older service answers the bare field
    const { filename, text } = heatmapExport(FIELD, 'speed', {
      runId: 'run-m1',
      run: MACRO_RUN,
      exportedAt: AT,
    });
    expect(filename).toBe('flowstate-run-m1-speed-seed-unknown-screening.csv');
    expect(valueOf(text, 'seed')).toMatch(/^unknown/);
    expect(valueOf(text, 'replicate')).toMatch(/^unknown/);
    expect(valueOf(text, 'tier')).toMatch(/^screening/);
    expect(valueOf(text, 'config_hash')).toBe('3f9a0b1c2d3e');
  });

  it('marks a demo export and prints no demo hash as provenance', () => {
    const { filename, text } = heatmapExport(MACRO, 'speed', {
      runId: 'run-m1',
      run: MACRO_RUN,
      demo: true,
      exportedAt: AT,
    });
    expect(filename).toBe('flowstate-run-m1-speed-seed43-screening-demo.csv');
    expect(valueOf(text, 'source')).toMatch(/^DEMO/);
    expect(valueOf(text, 'config_hash')).toBe('— demo, no server hash —');
  });

  it('states irregular or single-bin spacing instead of a bin size', () => {
    const irregular: Heatmap = { t_bins: [30, 90, 210], x_bins: [250], values: [[1], [2], [3]] };
    const { text } = heatmapExport(irregular, 'speed', { runId: 'run-x', exportedAt: AT });
    expect(valueOf(text, 't_bin_s')).toBe('irregular (60 to 120)');
    expect(valueOf(text, 'x_bin_m')).toBe('unknown (one bin)');
    expect(valueOf(text, 'tier')).toMatch(/^unknown/);
  });
});
