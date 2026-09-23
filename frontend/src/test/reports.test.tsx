/** ReportsView against the real API's asynchronous report contract:
 * POST /reports answers 202 with a queued ReportOut, GET /reports/{id} is
 * polled until done/failed, and the markdown comes from
 * GET /reports/{id}/markdown only once the report is done.
 *
 * The table itself is GET /reports (the server's history, newest first)
 * merged with this browser's localStorage records, each row saying which it
 * is; `report_path` is results-root-relative and is never rendered. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setOfflineFallback } from '../api/client';
import { mockCreateReport } from '../mocks/mockApi';
import { toast } from '../components/toast';
import { ReportsView } from '../views/ReportsView';

// the view's toasts are claims ("report X ready"), so they are asserted on
// directly rather than through the renderer's shared, time-dismissed queue
vi.mock('../components/toast', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../components/toast')>();
  return { ...actual, toast: vi.fn(), toastError: vi.fn() };
});

const doneRun = {
  run_id: 'run-a',
  scenario_id: 'scn-corridor',
  sweep_id: null,
  status: 'done',
  tier: 'micro',
  config_hash: 'abc123',
  seeded: false,
  progress: { completed_replicates: 20, total_replicates: 20 },
  seeds: [1],
  error: null,
  error_kind: null,
  created_at: '2026-09-16T00:00:00',
};

type Status = 'queued' | 'running' | 'done' | 'failed';

/** `GET /criteria` as the API serves it: the registry order, the default
 * first, each row carrying the provenance of its numbers. Trimmed to two
 * profiles and to the fields the dashboard reads. */
const CRITERIA = [
  {
    name: 'fhwa_default',
    source:
      'FlowState default (CLAUDE.md §7.1): GEH < 5 for >= 85% of link-hour comparisons, ' +
      'from the Wisconsin DOT table in §5.6 of FHWA-HRT-04-040.',
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
  {
    name: 'txdot_tsap_ch13',
    source: 'TxDOT Traffic and Safety Analysis Procedures Manual ch. 13 §13.5.2, Table 13-5.',
    geh_threshold: 3.0,
    geh_pass_fraction: 1.0,
    geh_pass_inclusive: true,
    rmspe_max: null,
    wave_speed_band_kmh: [14.0, 22.0],
    min_seeds: 20,
    require_ring_emergence: true,
    require_ring_dampening: true,
    require_sensitivity_grid: true,
    wave_detector: 'stack',
    default: false,
  },
];

function reportOut(status: Status, reportId = 'rpt-1', profile = 'fhwa_default') {
  const failed = status === 'failed';
  return {
    report_id: reportId,
    status,
    run_ids: ['run-a'],
    title: 'FlowState calibration & validation report',
    profile,
    // results-root-relative, exactly as api.main._relative_report_path serves
    // it: an identifier for the bundle, not a URL and not a filesystem path
    report_path: status === 'done' ? `reports/${reportId}/report.md` : null,
    error: failed ? 'no validated micro runs in the set' : null,
    error_kind: failed ? 'report_refused' : null,
    created_at: '2026-09-16T00:00:01',
  };
}

/** A report row from a service older than the `profile` parameter: no
 * `profile` key at all, which is what the API omits rather than defaults. */
function reportOutWithoutProfile(reportId: string): ReturnType<typeof reportOut> {
  const row: Partial<ReturnType<typeof reportOut>> = reportOut('done', reportId);
  delete row.profile;
  return row as ReturnType<typeof reportOut>;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

interface Call {
  url: string;
  method: string;
  body?: string;
}

const LS_REPORTS = 'flowstate.reports';

describe('ReportsView (asynchronous report contract)', () => {
  const calls: Call[] = [];
  // what GET /reports/rpt-1 currently answers — flipped mid-test like a worker would
  let status: Status = 'queued';
  /** What GET /reports answers; null = the service has no list endpoint. */
  let serverList: ReturnType<typeof reportOut>[] | null = [];
  /** What GET /criteria answers; null = the service has no such endpoint (an
   * API older than the criteria-profile registry). */
  let criteria: typeof CRITERIA | null = CRITERIA;
  /** The profile the service recorded on rpt-1 — whatever the POST carried,
   * so the status poll does not contradict the row it created. */
  let createdProfile = 'fhwa_default';
  const urlApi = URL as unknown as { createObjectURL?: unknown; revokeObjectURL?: unknown };

  beforeEach(() => {
    setOfflineFallback(false);
    window.localStorage.clear();
    calls.length = 0;
    status = 'queued';
    serverList = [];
    criteria = CRITERIA;
    createdProfile = 'fhwa_default';
    // jsdom has neither blob URLs nor navigation
    urlApi.createObjectURL = vi.fn(() => 'blob:report');
    urlApi.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        const method = init?.method ?? 'GET';
        const body = typeof init?.body === 'string' ? init.body : undefined;
        calls.push({ url, method, body });
        if (url.endsWith('/runs') && method === 'GET') return json([doneRun]);
        if (url.endsWith('/scenarios') && method === 'GET') {
          return json([{ scenario_id: 'scn-corridor', name: 'corridor_10km', config_hash: 'c0ffee', created_at: 't' }]);
        }
        if (url.endsWith('/criteria') && method === 'GET') {
          if (criteria === null) return json({ detail: 'Not Found' }, 404);
          return json(criteria);
        }
        // the Redis-queue answer: accepted, not yet generated; the profile is
        // echoed exactly as the API records it on the row
        if (url.endsWith('/reports') && method === 'POST') {
          const sent = JSON.parse(body ?? '{}') as { profile?: string };
          createdProfile = sent.profile ?? 'fhwa_default';
          return json(reportOut('queued', 'rpt-1', createdProfile), 202);
        }
        if (url.endsWith('/reports') && method === 'GET') {
          if (serverList === null) return json({ detail: 'Not Found' }, 404);
          return json(serverList);
        }
        if (url.endsWith('/reports/rpt-1/markdown')) {
          if (status === 'done') {
            return new Response('# FlowState calibration & validation report', {
              status: 200,
              headers: { 'content-type': 'text/markdown' },
            });
          }
          return json({ detail: `report 'rpt-1' is ${status}, not done` }, 409);
        }
        if (url.endsWith('/reports/rpt-1/archive')) {
          if (status !== 'done') return json({ detail: `report 'rpt-1' is ${status}, not done` }, 409);
          return new Response('PK\u0003\u0004zip', {
            status: 200,
            headers: { 'content-type': 'application/zip' },
          });
        }
        if (url.endsWith('/reports/rpt-1/pdf')) {
          if (status !== 'done') return json({ detail: `report 'rpt-1' is ${status}, not done` }, 409);
          return new Response('%PDF-1.4', {
            status: 200,
            headers: { 'content-type': 'application/pdf' },
          });
        }
        if (url.endsWith('/reports/rpt-1')) return json(reportOut(status, 'rpt-1', createdProfile));
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    delete urlApi.createObjectURL;
    delete urlApi.revokeObjectURL;
    window.localStorage.clear();
  });

  it(
    'tracks a queued report to done and downloads from /reports/{id}/markdown',
    async () => {
      render(<ReportsView />);
      fireEvent.click(await screen.findByLabelText('select run-a', {}, { timeout: 4000 }));
      fireEvent.click(screen.getByRole('button', { name: 'Generate report (1)' }));
      const table = screen.getByRole('table', { name: 'generated reports' });
      expect(await within(table).findByText('rpt-1')).toBeInTheDocument();
      // a 202/queued answer is not "generated": the row shows queued and
      // the download stays locked (the markdown route would 409)
      expect(within(table).getByText('queued')).toBeInTheDocument();
      const download = within(table).getByRole('button', { name: 'Download .md' });
      expect(download).toBeDisabled();

      status = 'done';
      expect(await within(table).findByText('done', {}, { timeout: 6000 })).toBeInTheDocument();
      expect(calls.some((c) => c.url.endsWith('/reports/rpt-1') && c.method === 'GET')).toBe(true);
      expect(download).toBeEnabled();

      fireEvent.click(download);
      await waitFor(() => {
        expect(calls.some((c) => c.url.endsWith('/reports/rpt-1/markdown'))).toBe(true);
      });
      expect(urlApi.createObjectURL).toHaveBeenCalledTimes(1);
      // the record is persisted with its resolved status
      const stored = JSON.parse(window.localStorage.getItem(LS_REPORTS) ?? '[]') as { report_id: string; status: string }[];
      expect(stored[0]).toMatchObject({ report_id: 'rpt-1', status: 'done' });
    },
    15000,
  );

  it(
    'surfaces an asynchronous refusal as failed with its error_kind and keeps download locked',
    async () => {
      status = 'failed';
      render(<ReportsView />);
      fireEvent.click(await screen.findByLabelText('select run-a', {}, { timeout: 4000 }));
      fireEvent.click(screen.getByRole('button', { name: 'Generate report (1)' }));
      const table = screen.getByRole('table', { name: 'generated reports' });
      expect(await within(table).findByText('failed', {}, { timeout: 6000 })).toBeInTheDocument();
      expect(within(table).getByText('report_refused: no validated micro runs in the set')).toBeInTheDocument();
      expect(within(table).getByRole('button', { name: 'Download .md' })).toBeDisabled();
    },
    15000,
  );

  it('names the scenario of a finished run instead of printing its id', async () => {
    render(<ReportsView />);
    const table = screen.getByRole('table', { name: 'finished runs' });
    expect(await within(table).findByText('corridor_10km', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(within(table).queryByText('scn-corridor')).toBeNull();
  }, 10000);

  it(
    'offers the markdown, the figure archive and the PDF, and keeps the blob URL alive',
    async () => {
      window.localStorage.setItem(
        LS_REPORTS,
        JSON.stringify([
          { report_id: 'rpt-1', run_ids: ['run-a'], status: 'done', created_at: '2026-09-15T00:00:00.000Z' },
        ]),
      );
      status = 'done';
      render(<ReportsView />);
      const table = screen.getByRole('table', { name: 'generated reports' });

      fireEvent.click(within(table).getByRole('button', { name: 'Download .md' }));
      await waitFor(() => {
        expect(calls.some((c) => c.url.endsWith('/reports/rpt-1/markdown'))).toBe(true);
      });
      fireEvent.click(within(table).getByRole('button', { name: 'Download .zip (with figures)' }));
      await waitFor(() => {
        expect(calls.some((c) => c.url.endsWith('/reports/rpt-1/archive'))).toBe(true);
      });
      fireEvent.click(within(table).getByRole('button', { name: 'Download PDF' }));
      await waitFor(() => {
        expect(calls.some((c) => c.url.endsWith('/reports/rpt-1/pdf'))).toBe(true);
      });
      await waitFor(() => {
        expect(urlApi.createObjectURL).toHaveBeenCalledTimes(3);
      });
      // revoking in the same tick as the click aborts the download the browser
      // has only just started (an unfinished .crdownload) — it must be deferred
      expect(urlApi.revokeObjectURL).not.toHaveBeenCalled();
    },
    15000,
  );

  it(
    'shows what an observed comparison rested on, and says nothing without one',
    async () => {
      // rpt-2 was scored against an observations artifact, rpt-1 was not
      serverList = [
        {
          ...reportOut('done', 'rpt-2'),
          observations_path: '/srv/data/i24/observations.json',
          observed: {
            corridor: 'i24_wb',
            n_stations: 4,
            n_link_hours: 12,
            n_speed_cells: 264,
            n_replicates: 3,
            n_stations_outside_span: 1,
            stations_outside_span: 'S4',
          },
        },
        reportOut('done', 'rpt-1'),
      ] as typeof serverList;
      render(<ReportsView />);
      const table = screen.getByRole('table', { name: 'generated reports' });
      expect(await within(table).findByText('rpt-2', {}, { timeout: 6000 })).toBeInTheDocument();
      const rows = within(table).getAllByRole('row');
      expect(
        within(rows[1]).getByText(
          'observed: 12 link-hours, 264 speed cells compared, 1 station excluded (outside the simulated span)',
        ),
      ).toBeInTheDocument();
      // a report with no observed side claims no comparison at all
      expect(within(rows[2]).queryByText(/^observed:/)).toBeNull();
      // the artifact path is server-side, like report_path: never rendered
      expect(within(table).queryByText(/observations\.json/)).toBeNull();
    },
    15000,
  );

  it(
    'lists the server history newest first and marks browser-only records',
    async () => {
      serverList = [reportOut('done', 'rpt-2'), reportOut('done', 'rpt-1')];
      window.localStorage.setItem(
        LS_REPORTS,
        JSON.stringify([
          { report_id: 'rpt-old', run_ids: ['run-a'], status: 'done', created_at: '2026-09-01T00:00:00.000Z' },
        ]),
      );
      render(<ReportsView />);
      const table = screen.getByRole('table', { name: 'generated reports' });
      expect(await within(table).findByText('rpt-2', {}, { timeout: 6000 })).toBeInTheDocument();
      await waitFor(() => {
        expect(within(table).getAllByRole('row').length).toBe(4); // header + 3
      });
      const ids = within(table)
        .getAllByRole('row')
        .slice(1)
        .map((row) => within(row).getAllByRole('cell')[0].textContent);
      // the server's own order, then what only this browser remembers
      expect(ids).toEqual(['rpt-2', 'rpt-1', 'rpt-old']);
      expect(within(table).getAllByText('SERVER').length).toBe(2);
      expect(within(table).getByText('LOCAL')).toBeInTheDocument();
      // report_path is results-root-relative: never a link, never a path
      expect(within(table).queryByText(/reports\/rpt-1\/report\.md/)).toBeNull();
      expect(within(table).queryAllByRole('link').length).toBe(0);
      // a server row is downloadable like any other
      expect(
        within(within(table).getAllByRole('row')[1]).getByRole('button', { name: 'Download .md' }),
      ).toBeEnabled();
    },
    15000,
  );

  it(
    'falls back to this browser\u2019s records when the service has no GET /reports',
    async () => {
      serverList = null; // 404: an API older than the list endpoint
      // every interval this view schedules, so the 404 route can be shown to
      // back off instead of being polled at the live-list rate forever
      const intervals: number[] = [];
      const realSetInterval = window.setInterval.bind(window);
      vi.spyOn(window, 'setInterval').mockImplementation(((
        fn: TimerHandler,
        ms?: number,
      ): number => {
        intervals.push(ms ?? 0);
        return realSetInterval(fn, ms);
      }) as typeof window.setInterval);
      window.localStorage.setItem(
        LS_REPORTS,
        JSON.stringify([
          { report_id: 'rpt-1', run_ids: ['run-a'], status: 'done', created_at: '2026-09-15T00:00:00.000Z' },
        ]),
      );
      render(<ReportsView />);
      const table = screen.getByRole('table', { name: 'generated reports' });
      expect(await within(table).findByText('rpt-1', {}, { timeout: 6000 })).toBeInTheDocument();
      expect(within(table).getByText('LOCAL')).toBeInTheDocument();
      expect(within(table).queryByText('SERVER')).toBeNull();
      expect(await screen.findByText(/this service has no GET \/reports/)).toBeInTheDocument();
      // a route that answers 404 is watched for an upgrade, not hammered: no
      // other poll in this view runs slower than 5 s, so an interval this long
      // can only be the list poll's backoff
      await waitFor(() => {
        expect(intervals.some((ms) => ms >= 30_000)).toBe(true);
      });
    },
    15000,
  );

  it(
    'resolves status-less records from older sessions by polling the API',
    async () => {
      window.localStorage.setItem(
        LS_REPORTS,
        JSON.stringify([{ report_id: 'rpt-1', run_ids: ['run-a'], created_at: '2026-09-15T00:00:00.000Z' }]),
      );
      status = 'done';
      render(<ReportsView />);
      const table = screen.getByRole('table', { name: 'generated reports' });
      expect(await within(table).findByText('done', {}, { timeout: 6000 })).toBeInTheDocument();
      expect(within(table).getByRole('button', { name: 'Download .md' })).toBeEnabled();
    },
    15000,
  );

  it(
    'offers the profiles of GET /criteria, with the server’s default preselected',
    async () => {
      render(<ReportsView />);
      const select = (await screen.findByLabelText('Criteria profile')) as HTMLSelectElement;
      await waitFor(() => {
        expect(within(select).getAllByRole('option')).toHaveLength(CRITERIA.length);
      });
      const options = within(select).getAllByRole('option') as HTMLOptionElement[];
      // the registry the endpoint served, in its order
      expect(options.map((o) => o.value)).toEqual(['fhwa_default', 'txdot_tsap_ch13']);
      // preselected from the row's own `default` flag
      expect(select.value).toBe('fhwa_default');
      expect(options[0].textContent).toContain('(default)');
      expect(options[1].textContent).not.toContain('(default)');
      // each option carries its provenance, so a protocol is chosen from the
      // document it comes from and not from its name
      expect(options[0].title).toBe(CRITERIA[0].source);
      expect(options[1].title).toBe(CRITERIA[1].source);
      expect(select).toBeEnabled();
    },
    10000,
  );

  it(
    'sends the chosen profile with POST /reports and names it on the row',
    async () => {
      render(<ReportsView />);
      const select = await screen.findByLabelText('Criteria profile');
      await waitFor(() => {
        expect(within(select).getAllByRole('option')).toHaveLength(CRITERIA.length);
      });
      fireEvent.change(select, { target: { value: 'txdot_tsap_ch13' } });
      fireEvent.click(await screen.findByLabelText('select run-a', {}, { timeout: 4000 }));
      fireEvent.click(screen.getByRole('button', { name: 'Generate report (1)' }));

      const table = screen.getByRole('table', { name: 'generated reports' });
      expect(await within(table).findByText('rpt-1', {}, { timeout: 6000 })).toBeInTheDocument();
      const post = calls.find((c) => c.url.endsWith('/reports') && c.method === 'POST');
      expect(JSON.parse(post?.body ?? '{}')).toEqual({
        run_ids: ['run-a'],
        profile: 'txdot_tsap_ch13',
      });
      // a pass/fail row means nothing without the thresholds behind it, so the
      // profile the API recorded is shown on the report itself
      const row = within(table).getAllByRole('row')[1];
      expect(within(row).getByText('txdot_tsap_ch13')).toBeInTheDocument();
      const stored = JSON.parse(window.localStorage.getItem(LS_REPORTS) ?? '[]') as {
        profile?: string;
      }[];
      expect(stored[0]).toMatchObject({ profile: 'txdot_tsap_ch13' });
    },
    15000,
  );

  it(
    'names the profile of every row and says unknown when the service reported none',
    async () => {
      serverList = [
        reportOut('done', 'rpt-2', 'txdot_tsap_ch13'),
        reportOutWithoutProfile('rpt-3'),
      ];
      render(<ReportsView />);
      const table = screen.getByRole('table', { name: 'generated reports' });
      expect(await within(table).findByText('rpt-2', {}, { timeout: 6000 })).toBeInTheDocument();
      await waitFor(() => {
        expect(within(table).getAllByRole('row').length).toBe(3); // header + 2
      });
      const criteriaCells = within(table)
        .getAllByRole('row')
        .slice(1)
        .map((row) => within(row).getAllByRole('cell')[3].textContent);
      // a row the service reported no profile for is *unknown*: naming the
      // default there would be this dashboard's assumption, shown as the
      // server's answer
      expect(criteriaCells).toEqual(['txdot_tsap_ch13', 'unknown']);
    },
    15000,
  );

  it(
    'degrades to the default profile alone, and sends none, without GET /criteria',
    async () => {
      criteria = null; // 404: an API older than the criteria-profile registry
      render(<ReportsView />);
      const select = await screen.findByLabelText('Criteria profile');
      await waitFor(() => {
        expect(within(select).getAllByRole('option')[0].title).toMatch(
          /answered 404 to GET \/criteria/,
        );
      });
      const options = within(select).getAllByRole('option') as HTMLOptionElement[];
      expect(options).toHaveLength(1);
      expect(options[0].value).toBe('fhwa_default');
      // there is nothing to choose: the control says so instead of offering a
      // registry this dashboard invented
      expect(select).toBeDisabled();

      // and the request carries no `profile` field at all — ReportCreateRequest
      // forbids unknown fields, so naming it would have such a service refuse
      // the whole report over a value it applies anyway
      fireEvent.click(await screen.findByLabelText('select run-a', {}, { timeout: 4000 }));
      fireEvent.click(screen.getByRole('button', { name: 'Generate report (1)' }));
      await waitFor(() => {
        expect(calls.some((c) => c.url.endsWith('/reports') && c.method === 'POST')).toBe(true);
      });
      const post = calls.find((c) => c.url.endsWith('/reports') && c.method === 'POST');
      expect(JSON.parse(post?.body ?? '{}')).toEqual({ run_ids: ['run-a'] });
    },
    15000,
  );
});

/** The reports polls run through the same client as everything else, so when
 * the API goes away they are answered by the in-browser demo backend. Neither
 * poll may launder that into evidence: a report that was queued on a real
 * server must not come back "done", and rows read from demo data must not be
 * badged SERVER or written to this browser's records. Pausing the polls in
 * demo mode is not the fix — it would strand a demo report at `queued`. */
describe('ReportsView while the API is unreachable (demo fallback)', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(toast).mockClear();
    setOfflineFallback(true); // as the Layout health poll would
    // any call that reached the network would be a bug: the fallback serves
    // the demo backend, and the API is down anyway
    vi.stubGlobal(
      'fetch',
      vi.fn(() => {
        throw new TypeError('network down');
      }),
    );
  });

  afterEach(() => {
    setOfflineFallback(false);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it(
    'leaves a real queued report queued instead of minting it done from demo data',
    async () => {
      // a report this browser requested from a real server, still running when
      // the API became unreachable
      window.localStorage.setItem(
        LS_REPORTS,
        JSON.stringify([
          { report_id: 'rpt-real', run_ids: ['run-a'], status: 'queued', created_at: '2026-09-16T00:00:00.000Z' },
        ]),
      );
      render(<ReportsView />);
      const table = screen.getByRole('table', { name: 'generated reports' });
      expect(await within(table).findByText('rpt-real', {}, { timeout: 4000 })).toBeInTheDocument();

      // well past two status polls (2 s) — the demo backend knows nothing
      // about this id and must say so, not invent a finished report
      await new Promise((r) => setTimeout(r, 2600));
      const row = within(table).getAllByRole('row')[1];
      expect(within(row).getByText('queued')).toBeInTheDocument();
      expect(within(table).queryByText('done')).toBeNull();
      expect(within(table).getByRole('button', { name: 'Download .md' })).toBeDisabled();
      // and no "ready" claim was made about it
      expect(vi.mocked(toast)).not.toHaveBeenCalledWith('ok', expect.stringContaining('ready'));
      // the persisted record is untouched: still queued, still not demo data
      const stored = JSON.parse(window.localStorage.getItem(LS_REPORTS) ?? '[]') as {
        report_id: string;
        status: string;
      }[];
      expect(stored).toHaveLength(1);
      expect(stored[0]).toMatchObject({ report_id: 'rpt-real', status: 'queued' });
    },
    15000,
  );

  it(
    'badges rows read from the demo backend DEMO, never SERVER, and records none of them',
    async () => {
      const demoReport = await mockCreateReport(['run-8f2c11']);
      render(<ReportsView />);
      const table = screen.getByRole('table', { name: 'generated reports' });
      expect(
        await within(table).findByText(demoReport.report_id, {}, { timeout: 6000 }),
      ).toBeInTheDocument();
      expect(within(table).getByText('DEMO')).toBeInTheDocument();
      expect(within(table).queryByText('SERVER')).toBeNull();
      expect(screen.getByText(/built-in demo data/)).toBeInTheDocument();

      // demo rows are not this browser's memory of a request to a server
      expect(window.localStorage.getItem(LS_REPORTS)).toBeNull();
    },
    15000,
  );
});
