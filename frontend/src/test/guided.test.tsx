/** The guided first run against the API's real shapes and the honesty rules
 * it exists to keep: a step is `done` only because the service said so, and
 * while the demo fallback is serving reads every step that needs the server
 * is `blocked` with the refusal `api/client` would raise.
 *
 * The happy path is the whole of docs/QUICKSTART.md §4–5: the preset is
 * stored (it has no `scenario_id` of its own), a 2-replicate smoke run is
 * launched and polled to `done`, and a report is requested, polled and
 * downloaded from `GET /reports/{id}/markdown`. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, OFFLINE_WRITE_MESSAGE, setOfflineFallback } from '../api/client';
import { GuidedFirstRun, SMOKE_REPLICATES } from '../components/GuidedFirstRun';
import { MIN_REPLICATES } from '../lib/metrics';

// the panel's toasts are claims about a server; asserted through the DOM
// state instead of the shared, time-dismissed queue
vi.mock('../components/toast', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../components/toast')>();
  return { ...actual, toast: vi.fn(), toastError: vi.fn() };
});

/** `GET /scenarios/preset` as the API serves the ring benchmark: a repo YAML
 * with the full validated config and no `scenario_id`. */
const PRESET = {
  name: 'ring_sugiyama',
  filename: 'ring_sugiyama.yaml',
  config_hash: 'a226444c0145',
  preset: true,
  config: {
    name: 'ring_sugiyama',
    tier: 'micro',
    network: { kind: 'ring', circumference_m: 230, n_vehicles: 22 },
    fleet: { model: 'IDM', v0: 33.3, T: 1.2, a_max: 0.73, b: 1.67, s0: 2.0, delta: 4.0 },
    av: { penetration: 0, compliance: 1, controller: null, controller_params: {} },
    sim: { duration_s: 600, step_length_s: 0.5, action_step_s: 0.5, warmup_s: 180, output_hz: 2 },
    perturbation: null,
    seed: 42,
    replicates: 20,
  },
};

const CRITERIA = [
  {
    name: 'fhwa_default',
    source: 'FlowState default (CLAUDE.md §7.1), from FHWA-HRT-04-040 §5.6.',
    geh_threshold: 5.0,
    geh_pass_fraction: 0.85,
    geh_pass_inclusive: true,
    rmspe_max: 0.15,
    wave_speed_band_kmh: [14.0, 22.0],
    min_seeds: 20,
    require_ring_emergence: true,
    require_ring_dampening: true,
    require_sensitivity_grid: true,
    wave_detector: 'stack',
    default: true,
  },
];

type Status = 'queued' | 'running' | 'done' | 'failed';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

interface Call {
  url: string;
  method: string;
  body?: string;
}

function renderPanel(): void {
  render(
    <MemoryRouter initialEntries={['/first-run']}>
      <GuidedFirstRun />
    </MemoryRouter>,
  );
}

describe('GuidedFirstRun', () => {
  const calls: Call[] = [];
  /** What `GET /runs/run_ring` currently answers — flipped mid-test like a
   * worker would. */
  let runStatus: Status = 'running';
  let runError: { error: string; error_kind: string | null } | null = null;
  /** What `GET /reports/rpt_ring` currently answers. */
  let reportStatus: Status = 'queued';
  const urlApi = URL as unknown as { createObjectURL?: unknown; revokeObjectURL?: unknown };

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    calls.length = 0;
    runStatus = 'running';
    runError = null;
    reportStatus = 'queued';
    // jsdom has neither blob URLs nor navigation
    urlApi.createObjectURL = vi.fn(() => 'blob:guided');
    urlApi.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        const method = init?.method ?? 'GET';
        const body = typeof init?.body === 'string' ? init.body : undefined;
        calls.push({ url, method, body });
        if (url.endsWith('/scenarios/preset')) return json([PRESET]);
        if (url.endsWith('/scenarios') && method === 'GET') return json([]);
        if (url.endsWith('/scenarios') && method === 'POST') {
          return json({ scenario_id: 'scn_ring01', config_hash: PRESET.config_hash }, 201);
        }
        if (url.endsWith('/criteria')) return json(CRITERIA);
        if (url.endsWith('/runs') && method === 'POST') return json({ run_id: 'run_ring' }, 202);
        if (url.endsWith('/runs') && method === 'GET') return json([]);
        if (url.endsWith('/runs/run_ring')) {
          const failed = runStatus === 'failed';
          return json({
            run_id: 'run_ring',
            scenario_id: 'scn_ring01',
            status: runStatus,
            tier: 'micro',
            config_hash: PRESET.config_hash,
            seeded: false,
            progress: {
              completed_replicates: runStatus === 'done' ? SMOKE_REPLICATES : 1,
              total_replicates: SMOKE_REPLICATES,
            },
            seeds: [42, 43],
            error: failed ? (runError?.error ?? null) : null,
            error_kind: failed ? (runError?.error_kind ?? null) : null,
            created_at: '2026-09-23T00:00:00',
          });
        }
        if (url.endsWith('/reports') && method === 'POST') {
          return json(reportOut('queued'), 202);
        }
        if (url.endsWith('/reports/rpt_ring/markdown')) {
          if (reportStatus !== 'done') {
            return json({ detail: `report 'rpt_ring' is ${reportStatus}, not done` }, 409);
          }
          return new Response('# Ring smoke (guided first run)', {
            status: 200,
            headers: { 'content-type': 'text/markdown' },
          });
        }
        if (url.endsWith('/reports/rpt_ring')) return json(reportOut(reportStatus));
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  function reportOut(status: Status) {
    return {
      report_id: 'rpt_ring',
      status,
      run_ids: ['run_ring'],
      title: 'Ring smoke (guided first run)',
      profile: 'fhwa_default',
      report_path: status === 'done' ? 'reports/rpt_ring/report.md' : null,
      error: status === 'failed' ? 'no validated micro runs in the set' : null,
      error_kind: status === 'failed' ? 'report_refused' : null,
      created_at: '2026-09-23T00:00:01',
    };
  }

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    delete urlApi.createObjectURL;
    delete urlApi.revokeObjectURL;
    // the connection flags are reset in beforeEach, not here: flipping them
    // while the panel is still mounted is a state update outside act()
  });

  it('blocks every server step with the offline refusal in demo mode', async () => {
    setOfflineFallback(true);
    renderPanel();

    // the six steps are all there, none of them ticked off demo data
    expect(screen.getByText('0/6 done')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Launch/ })).toBeDisabled();

    // the write steps carry the client's own refusal, verbatim
    const refusals = screen.getAllByText(OFFLINE_WRITE_MESSAGE);
    expect(refusals.length).toBeGreaterThanOrEqual(3);
    // and the connection step says what to do about it
    expect(screen.getByText(/healthz probe is not answering/)).toBeInTheDocument();

    // no step claims to be done, and nothing was sent to any server
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(6);
    for (const li of items) expect(li.className).toContain('blocked');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  }, 10000);

  it(`states that ${SMOKE_REPLICATES} replicates is a smoke test needing >= ${MIN_REPLICATES} seeds`, async () => {
    renderPanel();
    const note = await screen.findByText(/smoke test, not a result/, {}, { timeout: 4000 });
    expect(note).toHaveTextContent(`at least ${MIN_REPLICATES} seeds`);
    expect(note).toHaveTextContent('underpowered');
    expect(note).toHaveTextContent('n_seeds criterion fails');
  }, 10000);

  it(
    'walks preset -> run -> metrics -> report -> download against the API',
    async () => {
      renderPanel();

      // (b) the preset is a repo YAML: storing it is POST /scenarios
      fireEvent.click(await screen.findByRole('button', { name: 'Use ring_sugiyama' }, { timeout: 4000 }));
      expect(await screen.findByText('scn_ring01', {}, { timeout: 4000 })).toBeInTheDocument();
      expect(calls.some((c) => c.url.endsWith('/scenarios') && c.method === 'POST')).toBe(true);

      // (c) the launch commits exactly two replicates and no other override
      fireEvent.click(
        screen.getByRole('button', { name: `Launch ${SMOKE_REPLICATES}-replicate smoke run` }),
      );
      expect(await screen.findByText('run_ring', {}, { timeout: 4000 })).toBeInTheDocument();
      const launch = calls.find((c) => c.url.endsWith('/runs') && c.method === 'POST');
      expect(JSON.parse(launch?.body ?? '{}')).toEqual({
        scenario_id: 'scn_ring01',
        replicates: SMOKE_REPLICATES,
      });

      // (d) the run is polled, not assumed: it is running until it is not
      expect(await screen.findByText('running', {}, { timeout: 4000 })).toBeInTheDocument();
      runStatus = 'done';
      // (e) the run detail becomes reachable only once the run is done
      const detail = await screen.findByRole('link', { name: 'Open run detail' }, { timeout: 6000 });
      expect(detail).toHaveAttribute('href', '/runs/run_ring');
      fireEvent.click(detail);

      // (f) the report: 202 queued, polled, then the markdown download
      fireEvent.click(screen.getByRole('button', { name: 'Generate report' }));
      const download = await screen.findByRole('button', { name: 'Download report.md' }, { timeout: 4000 });
      // a queued report is not a bundle: the markdown route would answer 409
      expect(download).toBeDisabled();
      const posted = calls.find((c) => c.url.endsWith('/reports') && c.method === 'POST');
      expect(JSON.parse(posted?.body ?? '{}')).toMatchObject({
        run_ids: ['run_ring'],
        profile: 'fhwa_default',
      });

      reportStatus = 'done';
      await waitFor(() => expect(download).toBeEnabled(), { timeout: 6000 });
      fireEvent.click(download);
      await waitFor(() => {
        expect(calls.some((c) => c.url.endsWith('/reports/rpt_ring/markdown'))).toBe(true);
      });
      expect(urlApi.createObjectURL).toHaveBeenCalledTimes(1);

      // every step ticked, each one by an answer from the service
      expect(await screen.findByText('6/6 done', {}, { timeout: 4000 })).toBeInTheDocument();
    },
    20000,
  );

  it(
    "shows a failed run's reason in the service's own words",
    async () => {
      renderPanel();
      fireEvent.click(await screen.findByRole('button', { name: 'Use ring_sugiyama' }, { timeout: 4000 }));
      await screen.findByText('scn_ring01', {}, { timeout: 4000 });
      runStatus = 'failed';
      runError = { error: 'FileNotFoundError: net.net.xml missing', error_kind: 'sim_failed' };
      fireEvent.click(
        screen.getByRole('button', { name: `Launch ${SMOKE_REPLICATES}-replicate smoke run` }),
      );

      expect(
        await screen.findByText('sim_failed: FileNotFoundError: net.net.xml missing', {}, { timeout: 6000 }),
      ).toBeInTheDocument();
      // a failed run produces no metrics and cannot be reported
      const steps = screen.getAllByRole('listitem');
      expect(within(steps[4]).getByText('The run failed, so it produced no metrics.')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Generate report' })).toBeDisabled();
      expect(screen.queryByRole('link', { name: 'Open run detail' })).toBeNull();
    },
    20000,
  );
});
