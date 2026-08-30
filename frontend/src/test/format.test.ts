import { describe, expect, it } from 'vitest';
import {
  formatDeltaPct,
  formatDensityVehKm,
  formatDistAdaptive,
  formatDistKm,
  formatSpeedKmh,
  formatTimeMin,
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
  it('signs percentage deltas', () => {
    expect(formatDeltaPct(-0.231)).toBe('-23.1%');
    expect(formatDeltaPct(0.05)).toBe('+5.0%');
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
