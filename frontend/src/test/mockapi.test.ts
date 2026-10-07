/** The in-browser demo backend (mocks/mockApi) answers `?seed=` the way the
 * API does, and its runs hold seeds no other demo run holds.
 *
 * The demo heatmap used to ignore `?seed=` (always the first replicate) while
 * neighbouring sweep cells held overlapping seed ranges (`9000 + 7000·p +
 * 100·c`: p=1% c=80% ran 9150–9169, p=2% c=25% ran 9165–9184). Compare asked
 * each run for the seed they shared, got each run's first seed back, and said
 * "the runs share no seed" of two runs that shared five. */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ApiError, getRun, getRunHeatmap, setOfflineFallback } from '../api/client';
import type { Seed } from '../api/types';
import {
  mockCreateRun,
  mockCreateSweep,
  mockGetRun,
  mockGetRunHeatmap,
  mockGetRunMetrics,
  mockListRuns,
} from '../mocks/mockApi';

beforeEach(() => {
  setOfflineFallback(true); // as the Layout health poll would
});

afterEach(() => {
  setOfflineFallback(false);
});

/** What `p` rejected with: an `ApiError`, by status and message. */
async function refusal(p: Promise<unknown>): Promise<{ status: number; message: string }> {
  const err = await p.then(
    () => null,
    (e: unknown) => e,
  );
  expect(err).toBeInstanceOf(ApiError);
  const e = err as ApiError;
  return { status: e.status, message: e.message };
}

describe('demo heatmap seeds', () => {
  it('serves the replicate a seed names, through the client, and says which', async () => {
    const run = await getRun('run-8f2c11');
    expect(run.seeds.slice(0, 4)).toEqual(['1000', '1001', '1002', '1003']);

    // no seed: the first replicate, the API's default
    const first = await getRunHeatmap('run-8f2c11', 'speed');
    expect(first.seed).toBe('1000');
    // the same replicate named explicitly is the same field
    expect((await getRunHeatmap('run-8f2c11', 'speed', '1000')).values).toEqual(first.values);

    // another seed: that replicate, its own realisation, named as asked
    const fourth = await getRunHeatmap('run-8f2c11', 'speed', run.seeds[3]);
    expect(fourth.seed).toBe('1003');
    expect(fourth.values).not.toEqual(first.values);
    const last = await mockGetRunHeatmap('run-8f2c11', 'density', run.seeds[19]);
    expect(last).toMatchObject({ run_id: 'run-8f2c11', seed: '1019', field: 'density' });
  });

  it('answers 404 for a seed the run did not run, exactly as asked', async () => {
    const cases: [Seed, string][] = [
      ['999', 'one below the range'],
      ['1020', 'one past it (20 replicates: 1000–1019)'],
      ['2000', 'another demo run’s first seed'],
      ['6914975401685141156', 'a 64-bit seed, compared in BigInt, not rounded'],
      ['1000000000000000000000000000000', 'past 2^64'],
    ];
    for (const [seed, why] of cases) {
      const r = await refusal(getRunHeatmap('run-8f2c11', 'speed', seed));
      expect(r, why).toEqual({
        status: 404,
        message: `run 'run-8f2c11' has no replicate for seed ${seed}`,
      });
    }
  });

  it('answers 422 for a seed that is not decimal digits, like the API', async () => {
    const bad = ['abc', '', '-1', '+1000', '1e3', ' 1000', '1000.0', '1_000'];
    const answers = await Promise.all(bad.map((seed) => refusal(mockGetRunHeatmap('run-8f2c11', 'speed', seed))));
    expect(answers.map((a) => a.status)).toEqual(bad.map(() => 422));
  });
});

describe('demo seed ranges', () => {
  it('gives every demo run seeds no other demo run holds', async () => {
    // neighbouring cells, two controllers at every (p, c), baselines and
    // strategy cells, then two launches: the shapes that used to collide
    await mockCreateSweep({
      scenario_id: 'scn-corridor',
      penetrations: [0.01, 0.02, 0.05],
      compliances: [0.25, 0.5, 0.8, 1.0],
      controllers: ['follower_stopper', 'pi_saturation'],
      strategies: ['none', 'vsl'],
      replicates: 20,
      include_baseline: true,
    });
    await mockCreateRun({ scenario_id: 'scn-corridor', replicates: 150 });
    await mockCreateRun({ scenario_id: 'scn-ring', replicates: 3 });

    const owner = new Map<Seed, string>();
    const rows = await mockListRuns();
    expect(rows.length).toBeGreaterThan(30);
    const details = await Promise.all(rows.map((row) => mockGetRun(row.run_id)));
    for (const run of details) {
      expect(run.seeds.length).toBe(run.progress.total_replicates);
      for (const s of run.seeds) {
        expect(owner.get(s), `seed ${s} of ${run.run_id}`).toBeUndefined();
        owner.set(s, run.run_id);
      }
    }
  });

  it('keeps a sweep cell’s aggregate equal to its run’s metrics', async () => {
    const sweep = await mockCreateSweep({
      scenario_id: 'scn-corridor',
      penetrations: [0.1],
      compliances: [0.5],
      controllers: ['jad'],
      replicates: 20,
      include_baseline: false,
    });
    const cell = sweep.cells[0];
    expect(cell.run_id).not.toBeNull();
    const metrics = await mockGetRunMetrics(cell.run_id as string);
    expect(cell.aggregate).toEqual(metrics.aggregate);
  });
});
