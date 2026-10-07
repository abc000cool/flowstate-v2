import { describe, expect, it } from 'vitest';
import {
  binColor,
  deltaClass,
  DELTA_LEGEND,
  DENSITY_DOMAIN_MAX_VEHM,
  DENSITY_STOPS,
  NULL_BIN_RGB,
  ordinalVar,
  rampGradientCSS,
  sampleRamp,
  seriesVar,
  signedImprovement,
  SPEED_DOMAIN_MAX_MS,
  SPEED_STOPS,
  speedColor,
} from '../lib/colormap';

/** "FlowState speed" anchors, DASHBOARD_DESIGN.md §7.1 (every 15 km/h). */
const SPEED_ANCHORS: [number, [number, number, number]][] = [
  [0.0, [106, 13, 24]],
  [0.125, [175, 37, 32]],
  [0.25, [222, 101, 49]],
  [0.375, [241, 175, 93]],
  [0.5, [246, 240, 218]],
  [0.625, [165, 210, 237]],
  [0.75, [111, 174, 226]],
  [0.875, [68, 135, 208]],
  [1.0, [42, 97, 177]],
];

/** Density anchors, §7.2. */
const DENSITY_ANCHORS: [number, [number, number, number]][] = [
  [0.0, [250, 247, 236]],
  [0.2, [239, 210, 158]],
  [0.4, [239, 158, 79]],
  [0.6, [223, 98, 43]],
  [0.8, [177, 45, 38]],
  [1.0, [111, 19, 28]],
];

describe('speed ramp ("FlowState speed": jam red → pale neutral → free-flow blue)', () => {
  it('returns the exact anchor colours at anchor stops', () => {
    expect(SPEED_STOPS.map((s) => s.at)).toEqual(SPEED_ANCHORS.map(([at]) => at));
    for (const [at, rgb] of SPEED_ANCHORS) expect(sampleRamp(SPEED_STOPS, at)).toEqual(rgb);
  });

  it('clamps outside [0, 1]', () => {
    expect(sampleRamp(SPEED_STOPS, -0.4)).toEqual([106, 13, 24]);
    expect(sampleRamp(SPEED_STOPS, 1.7)).toEqual([42, 97, 177]);
  });

  it('interpolates linearly between anchors', () => {
    // midpoint of the first segment: t = 0.0625
    const [r, g, b] = sampleRamp(SPEED_STOPS, 0.0625);
    expect(r).toBe(Math.round((106 + 175) / 2));
    expect(g).toBe(Math.round((13 + 37) / 2));
    expect(b).toBe(Math.round((24 + 32) / 2));
  });

  it('maps SI speeds through the fixed display domain (0–120 km/h)', () => {
    expect(SPEED_DOMAIN_MAX_MS).toBe(33.3);
    expect(speedColor(0)).toEqual([106, 13, 24]);
    expect(speedColor(SPEED_DOMAIN_MAX_MS)).toEqual([42, 97, 177]);
    // 60 km/h is the pale neutral transition stop
    expect(speedColor(0.5 * SPEED_DOMAIN_MAX_MS)).toEqual([246, 240, 218]);
  });
});

describe('density ramp (pale empty road → deep red at jam density)', () => {
  it('returns the exact anchor colours at anchor stops', () => {
    expect(DENSITY_STOPS.map((s) => s.at)).toEqual(DENSITY_ANCHORS.map(([at]) => at));
    for (const [at, rgb] of DENSITY_ANCHORS) expect(sampleRamp(DENSITY_STOPS, at)).toEqual(rgb);
  });

  it('maps veh/m through the 0–160 veh/km domain', () => {
    expect(DENSITY_DOMAIN_MAX_VEHM).toBe(0.16);
    expect(binColor('density', 0)).toEqual([250, 247, 236]);
    expect(binColor('density', DENSITY_DOMAIN_MAX_VEHM)).toEqual([111, 19, 28]);
  });
});

describe('null bins', () => {
  it('use the light --viz-null-bg as the image-pass placeholder', () => {
    expect(NULL_BIN_RGB).toEqual([241, 240, 239]);
    expect(binColor('speed', null)).toEqual(NULL_BIN_RGB);
    expect(binColor('density', null)).toEqual(NULL_BIN_RGB);
    expect(binColor('speed', Number.NaN)).toEqual(NULL_BIN_RGB);
  });

  it('are clearly distinct from stopped traffic', () => {
    // the old palette put "no data" ΔE 4.5 from 0 km/h; now they sit at
    // opposite ends of the lightness range
    const stopped = speedColor(0);
    const dist = Math.hypot(...NULL_BIN_RGB.map((c, i) => c - stopped[i]));
    expect(dist).toBeGreaterThan(250);
  });
});

describe('legend gradient', () => {
  it('places every stop at its exact position', () => {
    const css = rampGradientCSS(SPEED_STOPS);
    expect(css.startsWith('linear-gradient(90deg, ')).toBe(true);
    expect(css).toContain('rgb(106,13,24) 0%');
    expect(css).toContain('rgb(175,37,32) 12.5%');
    expect(css).toContain('rgb(246,240,218) 50%');
    expect(css).toContain('rgb(68,135,208) 87.5%');
    expect(css).toContain('rgb(42,97,177) 100%');
  });
});

describe('deltaClass (sweep matrix binned diverging, §7.4)', () => {
  it('is neutral below 2 %', () => {
    expect(deltaClass(0)).toBe('delta-n');
    expect(deltaClass(0.0194)).toBe('delta-n');
    expect(deltaClass(-0.0194)).toBe('delta-n');
  });

  it('bins |s| at 2, 10, 25 and 50 %, lower edges inclusive', () => {
    expect(deltaClass(0.02)).toBe('delta-b1');
    expect(deltaClass(0.0994)).toBe('delta-b1');
    expect(deltaClass(0.1)).toBe('delta-b2');
    expect(deltaClass(0.2494)).toBe('delta-b2');
    expect(deltaClass(0.25)).toBe('delta-b3');
    expect(deltaClass(0.4994)).toBe('delta-b3');
    expect(deltaClass(0.5)).toBe('delta-b4');
    expect(deltaClass(3)).toBe('delta-b4');
  });

  it('picks the worse arm for a negative signed improvement', () => {
    expect(deltaClass(-0.02)).toBe('delta-w1');
    expect(deltaClass(-0.1)).toBe('delta-w2');
    expect(deltaClass(-0.25)).toBe('delta-w3');
    expect(deltaClass(-0.5)).toBe('delta-w4');
    expect(deltaClass(-0.9)).toBe('delta-w4');
  });

  it('agrees with the printed 0.1 % precision at the edges', () => {
    // prints "+10.0%" and "-2.0%", so it is coloured as 10 % and 2 %
    expect(deltaClass(0.09996)).toBe('delta-b2');
    expect(deltaClass(-0.01996)).toBe('delta-w1');
    // prints "+1.9%": still neutral
    expect(deltaClass(0.0194)).toBe('delta-n');
  });

  it('has no class for a non-finite value', () => {
    expect(deltaClass(Number.NaN)).toBe('delta-n');
    expect(deltaClass(Number.POSITIVE_INFINITY)).toBe('delta-n');
  });

  it('signs the improvement by the metric’s good direction', () => {
    // σ_v down by 50 % is better; throughput down by 50 % is worse
    expect(deltaClass(signedImprovement(-0.5, 'down'))).toBe('delta-b4');
    expect(deltaClass(signedImprovement(-0.5, 'up'))).toBe('delta-w4');
    expect(deltaClass(signedImprovement(0.05, 'up'))).toBe('delta-b1');
  });

  it('lists the legend worst to best around the neutral class', () => {
    expect(DELTA_LEGEND.map((d) => d.cls)).toEqual([
      'delta-w4',
      'delta-w3',
      'delta-w2',
      'delta-w1',
      'delta-n',
      'delta-b1',
      'delta-b2',
      'delta-b3',
      'delta-b4',
    ]);
  });
});

describe('series slots (§7.5)', () => {
  it('fixes each controller to its slot, the baseline to the neutral', () => {
    expect(seriesVar('follower_stopper')).toBe('var(--viz-series-1)');
    expect(seriesVar('pi_saturation')).toBe('var(--viz-series-2)');
    expect(seriesVar('jad')).toBe('var(--viz-series-3)');
    expect(seriesVar('vsl')).toBe('var(--viz-series-4)');
    expect(seriesVar('pi_meanfrac')).toBe('var(--viz-series-5)');
    expect(seriesVar(null)).toBe('var(--viz-baseline)');
    expect(seriesVar('none')).toBe('var(--viz-baseline)');
    // never a generated hue for an entity without a slot
    expect(seriesVar('brand_new_controller')).toBeNull();
  });

  it('steps ordinal levels monotonically over the four ordinal tokens', () => {
    expect([0, 1, 2, 3].map((i) => ordinalVar(i, 4))).toEqual([
      'var(--viz-ord-1)',
      'var(--viz-ord-2)',
      'var(--viz-ord-3)',
      'var(--viz-ord-4)',
    ]);
    const six = [0, 1, 2, 3, 4, 5].map((i) => Number(ordinalVar(i, 6).match(/ord-(\d)/)![1]));
    expect(six[0]).toBe(1);
    expect(six[5]).toBe(4);
    for (let i = 1; i < six.length; i++) expect(six[i]).toBeGreaterThanOrEqual(six[i - 1]);
  });
});
