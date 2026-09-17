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

function reportOut(status: Status, reportId = 'rpt-1') {
  const failed = status === 'failed';
  return {
    report_id: reportId,
    status,
    run_ids: ['run-a'],
    title: 'FlowState calibration & validation report',
    // results-root-relative, exactly as api.main._relative_report_path serves
    // it: an identifier for the bundle, not a URL and not a filesystem path
    report_path: status === 'done' ? `reports/${reportId}/report.md` : null,
    error: failed ? 'no validated micro runs in the set' : null,
    error_kind: failed ? 'report_refused' : null,
    created_at: '2026-09-16T00:00:01',
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

interface Call {
  url: string;
  method: string;
}

const LS_REPORTS = 'flowstate.reports';

describe('ReportsView (asynchronous report contract)', () => {
  const calls: Call[] = [];
  // what GET /reports/rpt-1 currently answers — flipped mid-test like a worker would
  let status: Status = 'queued';
  /** What GET /reports answers; null = the service has no list endpoint. */
  let serverList: ReturnType<typeof reportOut>[] | null = [];
  const urlApi = URL as unknown as { createObjectURL?: unknown; revokeObjectURL?: unknown };

  beforeEach(() => {
    setOfflineFallback(false);
    window.localStorage.clear();
    calls.length = 0;
    status = 'queued';
    serverList = [];
    // jsdom has neither blob URLs nor navigation
    urlApi.createObjectURL = vi.fn(() => 'blob:report');
    urlApi.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        const method = init?.method ?? 'GET';
        calls.push({ url, method });
        if (url.endsWith('/runs') && method === 'GET') return json([doneRun]);
        if (url.endsWith('/scenarios') && method === 'GET') {
          return json([{ scenario_id: 'scn-corridor', name: 'corridor_10km', config_hash: 'c0ffee', created_at: 't' }]);
        }
        // the Redis-queue answer: accepted, not yet generated
        if (url.endsWith('/reports') && method === 'POST') return json(reportOut('queued'), 202);
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
        if (url.endsWith('/reports/rpt-1')) return json(reportOut(status));
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
