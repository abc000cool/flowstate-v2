import { describe, expect, it } from 'vitest';
import {
  distUnit,
  formatDeltaPct,
  formatDensityVehKm,
  formatDensityVehKmMi,
  formatDistAdaptive,
  formatDistKm,
  formatFetchError,
  formatNumber,
  formatSpeedKmh,
  formatSpeedKmhMph,
  formatTickDist,
  formatTickMin,
  formatTimeMin,
  KM_PER_MI,
  MS_TO_KMH,
  MS_TO_MPH,
  spaceTicks,
  timeTicks,
} from '../lib/format';

describe('time axis formatter', () => {
  it('renders seconds as whole minutes', () => {
    expect(formatTimeMin(0)).toBe('0 min');
    expect(formatTimeMin(300)).toBe('5 min');
    expect(formatTimeMin(1200)).toBe('20 min');
  });
  it('keeps one decimal for fractional minutes', () => {
    expect(formatTimeMin(90)).toBe('1.5 min');
  });
});

describe('space axis formatter', () => {
  it('renders metres as kilometres', () => {
    expect(formatDistKm(10000)).toBe('10 km');
    expect(formatDistKm(2500)).toBe('2.5 km');
    expect(formatDistKm(0)).toBe('0 km');
  });
  it('adapts to metre scale for short (ring) networks', () => {
    expect(formatDistAdaptive(150, 230)).toBe('150 m');
    expect(formatDistAdaptive(5000, 10000)).toBe('5 km');
  });
});

describe('speed and density readouts', () => {
  it('converts m/s to km/h', () => {
    expect(formatSpeedKmh(30)).toBe('108 km/h');
    expect(formatSpeedKmh(0)).toBe('0 km/h');
  });
  it('converts veh/m to veh/km', () => {
    expect(formatDensityVehKm(0.038)).toBe('38 veh/km');
  });
  it('converts m/s to km/h with mph alongside', () => {
    expect(MS_TO_MPH).toBe(2.2369362920544);
    // 47 km/h = 13.056 m/s = 29.2 mph
    expect(formatSpeedKmhMph(47 / MS_TO_KMH)).toBe('47 km/h (29 mph)');
    expect(formatSpeedKmhMph(0)).toBe('0 km/h (0 mph)');
    // 33.3 m/s is the 120 km/h display maximum, 74.5 mph
    expect(formatSpeedKmhMph(33.3)).toBe('120 km/h (74 mph)');
  });
  it('converts veh/m to veh/km with veh/mi alongside', () => {
    expect(KM_PER_MI).toBe(1.609344);
    expect(formatDensityVehKmMi(0.038)).toBe('38 veh/km (61 veh/mi)');
    expect(formatDensityVehKmMi(0.16)).toBe('160 veh/km (257 veh/mi)');
    expect(formatDensityVehKmMi(0)).toBe('0 veh/km (0 veh/mi)');
  });
  it('signs percentage deltas', () => {
    expect(formatDeltaPct(-0.231)).toBe('-23.1%');
    expect(formatDeltaPct(0.05)).toBe('+5.0%');
  });
});

describe('bare tick labels (the axis title carries the unit)', () => {
  it('labels time ticks in minutes', () => {
    expect(formatTickMin(300)).toBe('5');
    expect(formatTickMin(90)).toBe('1.5');
  });
  it('labels distance ticks in km, or m on a ring under 1 km', () => {
    expect(formatTickDist(2500, 10000)).toBe('2.5');
    expect(formatTickDist(150, 230)).toBe('150');
    expect(distUnit(10000)).toBe('km');
    expect(distUnit(230)).toBe('m');
  });
});

describe('fetch failures', () => {
  it('prefixes the HTTP status and keeps the server’s words', () => {
    const err = Object.assign(new Error('observations_path is outside the allowed data roots'), { status: 422 });
    expect(formatFetchError(err)).toBe('HTTP 422 — observations_path is outside the allowed data roots');
  });
  it('does not repeat a status the message already starts with', () => {
    expect(formatFetchError(Object.assign(new Error('404 Not Found'), { status: 404 }))).toBe('404 Not Found');
  });
  it('prints no "HTTP 0" for a request the client never sent', () => {
    const err = Object.assign(new Error('API offline — reconnect first'), { status: 0 });
    expect(formatFetchError(err)).toBe('API offline — reconnect first');
  });
  it('passes a network error through unchanged', () => {
    expect(formatFetchError(new TypeError('Failed to fetch'))).toBe('Failed to fetch');
    expect(formatFetchError('')).toBe('no detail');
  });
});

describe('tick generators', () => {
  it('emits nice minute multiples covering the range', () => {
    const ticks = timeTicks(0, 1200, 6);
    expect(ticks[0]).toBe(0);
    expect(ticks).toContain(600);
    expect(ticks[ticks.length - 1]).toBeLessThanOrEqual(1200);
    // all ticks fall on whole-minute nice steps
    for (const t of ticks) expect(t % 60).toBe(0);
  });
  it('emits nice kilometre multiples', () => {
    const ticks = spaceTicks(0, 10000, 5);
    expect(ticks).toContain(0);
    expect(ticks).toContain(4000);
    for (const x of ticks) expect(x % 1000).toBe(0);
  });
});

describe('fixed-digit numbers', () => {
  it('prints a tiny negative that rounds to zero as zero, not -0.00', () => {
    expect(formatNumber(-0.0001, 2)).toBe('0.00');
    expect(formatNumber(-0.04, 1)).toBe('0.0');
    expect(formatNumber(-0.4, 0)).toBe('0');
    expect(formatNumber(-0.05, 2)).toBe('-0.05');
    expect(formatNumber(null)).toBe('—');
  });
});
