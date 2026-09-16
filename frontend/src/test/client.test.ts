import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createReport,
  createRun,
  createSweep,
  DEFAULT_API_KEY,
  DEFAULT_BASE_URL,
  getReport,
  getReportMarkdown,
  listRuns,
} from '../api/client';

function fakeResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe('api client auth', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(fakeResponse([]));
    vi.stubGlobal('fetch', fetchMock);
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('sends the default X-API-Key header against the default base URL', async () => {
    await listRuns();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${DEFAULT_BASE_URL}/runs`);
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe(DEFAULT_API_KEY);
    expect(init.method).toBe('GET');
  });

  it('uses the key and base URL stored in localStorage', async () => {
    window.localStorage.setItem('flowstate.apiBase', 'http://ops.example:8000/api/v1');
    window.localStorage.setItem('flowstate.apiKey', 'ops-key-123');
    await listRuns();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://ops.example:8000/api/v1/runs');
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('ops-key-123');
  });

  it('POSTs JSON with content-type alongside the auth header', async () => {
    fetchMock.mockResolvedValue(fakeResponse({ run_id: 'run-x' }));
    await createRun({ scenario_id: 'scn-1', replicates: 20, tier: 'micro' });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${DEFAULT_BASE_URL}/runs`);
    expect(init.method).toBe('POST');
    const headers = init.headers as Record<string, string>;
    expect(headers['X-API-Key']).toBe(DEFAULT_API_KEY);
    expect(headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(init.body as string)).toEqual({
      scenario_id: 'scn-1',
      replicates: 20,
      tier: 'micro',
    });
  });

  it('throws an ApiError carrying the server detail on non-2xx', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      json: async () => ({ detail: 'invalid API key' }),
    } as unknown as Response);
    await expect(listRuns()).rejects.toThrow('invalid API key');
  });
});

describe('api client contract routes', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('POSTs the sweep grid with a controllers list and include_baseline', async () => {
    fetchMock.mockResolvedValue(fakeResponse({ sweep_id: 'swp-1', cells: [] }));
    const res = await createSweep({
      scenario_id: 'scn-1',
      penetrations: [0.05],
      compliances: [0.8],
      controllers: ['follower_stopper'],
      replicates: 20,
      include_baseline: true,
    });
    expect(res.sweep_id).toBe('swp-1');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${DEFAULT_BASE_URL}/sweeps`);
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    // the API's SweepCreateRequest field is the plural list; a singular
    // `controller` would be silently dropped (every cell without a controller)
    expect(body.controllers).toEqual(['follower_stopper']);
    expect(body).not.toHaveProperty('controller');
    expect(body.include_baseline).toBe(true);
    expect(body).toMatchObject({ scenario_id: 'scn-1', penetrations: [0.05], compliances: [0.8], replicates: 20 });
  });

  it('POSTs /reports with run_ids and returns the ReportOut status row', async () => {
    fetchMock.mockResolvedValue(
      fakeResponse({
        report_id: 'rpt-1',
        status: 'queued',
        run_ids: ['run-a'],
        title: 't',
        error: null,
        error_kind: null,
        created_at: '2026-09-16T00:00:00',
      }),
    );
    const out = await createReport(['run-a']);
    expect(out.status).toBe('queued');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${DEFAULT_BASE_URL}/reports`);
    expect(JSON.parse(init.body as string)).toEqual({ run_ids: ['run-a'] });
  });

  it('polls report status from /reports/{id}', async () => {
    fetchMock.mockResolvedValue(fakeResponse({ report_id: 'rpt-1', status: 'running' }));
    const out = await getReport('rpt-1');
    expect(out.status).toBe('running');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${DEFAULT_BASE_URL}/reports/rpt-1`);
    expect(init.method).toBe('GET');
  });

  it('downloads the markdown from /reports/{id}/markdown, not the JSON status route', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: async () => '# FlowState calibration & validation report',
    } as unknown as Response);
    const md = await getReportMarkdown('rpt-1');
    expect(md.startsWith('#')).toBe(true);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${DEFAULT_BASE_URL}/reports/rpt-1/markdown`);
  });

  it('surfaces the 409 the markdown route answers while a report is not done', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      json: async () => ({ detail: "report 'rpt-1' is running, not done" }),
    } as unknown as Response);
    await expect(getReportMarkdown('rpt-1')).rejects.toThrow("report 'rpt-1' is running, not done");
  });
});
