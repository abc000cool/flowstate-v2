/** ReportsView against the real API's asynchronous report contract:
 * POST /reports answers 202 with a queued ReportOut, GET /reports/{id} is
 * polled until done/failed, and the markdown comes from
 * GET /reports/{id}/markdown only once the report is done. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setOfflineFallback } from '../api/client';
import { ReportsView } from '../views/ReportsView';

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

function reportOut(status: Status) {
  const failed = status === 'failed';
  return {
    report_id: 'rpt-1',
    status,
    run_ids: ['run-a'],
    title: 'FlowState calibration & validation report',
    report_path: status === 'done' ? '/data/reports/rpt-1/report.md' : null,
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
  const urlApi = URL as unknown as { createObjectURL?: unknown; revokeObjectURL?: unknown };

  beforeEach(() => {
    setOfflineFallback(false);
    window.localStorage.clear();
    calls.length = 0;
    status = 'queued';
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
        // the Redis-queue answer: accepted, not yet generated
        if (url.endsWith('/reports') && method === 'POST') return json(reportOut('queued'), 202);
        if (url.endsWith('/reports/rpt-1/markdown')) {
          if (status === 'done') {
            return new Response('# FlowState calibration & validation report', {
              status: 200,
              headers: { 'content-type': 'text/markdown' },
            });
          }
          return json({ detail: `report 'rpt-1' is ${status}, not done` }, 409);
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
