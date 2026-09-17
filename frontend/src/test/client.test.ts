import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  AUTH_RETRY_DELAY_MS,
  clearAuthFailure,
  createReport,
  createRun,
  createScenario,
  createSweep,
  DEFAULT_API_KEY,
  DEFAULT_BASE_URL,
  DEFAULT_CRITERIA_PROFILE,
  getReport,
  getReportArchive,
  getReportMarkdown,
  getReportPdf,
  isAuthFailed,
  isAuthRetryScheduled,
  listCriteriaProfiles,
  listReports,
  listRuns,
  listScenarios,
  OFFLINE_WRITE_MESSAGE,
  setOfflineFallback,
} from '../api/client';
import {
  mockCreateReport,
  mockListCriteriaProfiles,
  mockListReports,
  mockListRuns,
  mockListScenarios,
} from '../mocks/mockApi';

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

  it('POSTs /reports with the chosen criteria profile, and omits the field when none is given', async () => {
    fetchMock.mockResolvedValue(
      fakeResponse({
        report_id: 'rpt-1',
        status: 'queued',
        run_ids: ['run-a'],
        title: 't',
        profile: 'txdot_tsap_ch13',
        error: null,
        error_kind: null,
        created_at: '2026-09-16T00:00:00',
      }),
    );
    const out = await createReport(['run-a'], undefined, 'txdot_tsap_ch13');
    // the profile the report was scored against comes back on the row
    expect(out.profile).toBe('txdot_tsap_ch13');
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toEqual({ run_ids: ['run-a'], profile: 'txdot_tsap_ch13' });
    // ReportCreateRequest forbids unknown fields, so an omitted title must be
    // absent rather than sent as undefined/null
    expect(body).not.toHaveProperty('title');

    // and an unnamed profile sends no field at all: a service older than the
    // parameter would refuse the whole request over it (422), and it applies
    // DEFAULT_CRITERIA_PROFILE anyway
    fetchMock.mockClear();
    await createReport(['run-a']);
    const [, plain] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(plain.body as string)).not.toHaveProperty('profile');
  });

  it('GETs the criteria profiles from /criteria', async () => {
    fetchMock.mockResolvedValue(
      fakeResponse([
        { name: 'fhwa_default', source: 'CLAUDE.md §7.1', default: true },
        { name: 'txdot_tsap_ch13', source: 'TxDOT TSAP ch. 13', default: false },
      ]),
    );
    const profiles = await listCriteriaProfiles();
    expect(profiles.map((p) => p.name)).toEqual(['fhwa_default', 'txdot_tsap_ch13']);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${DEFAULT_BASE_URL}/criteria`);
    expect(init.method).toBe('GET');
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

describe('api client error rendering', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    window.localStorage.clear();
    clearAuthFailure();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
    clearAuthFailure();
  });

  it('renders a pydantic error list as field: message, not raw JSON', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      json: async () => ({
        detail: [
          {
            type: 'less_than_equal',
            loc: ['body', 'replicates'],
            msg: 'Input should be less than or equal to 200',
            input: 500,
          },
          { type: 'greater_than_equal', loc: ['body', 'sim', 'duration_s'], msg: 'Input should be greater than 0' },
        ],
      }),
    } as unknown as Response);
    await expect(createRun({ scenario_id: 'scn-1', replicates: 500 })).rejects.toThrow(
      'replicates: Input should be less than or equal to 200; sim.duration_s: Input should be greater than 0',
    );
  });

  it("renders the sweep cell wrapper {cell, errors} with the cell it failed on", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      json: async () => ({
        detail: {
          cell: { penetration: 0.05, compliance: 0.8, controller: 'jad' },
          errors: [{ type: 'value_error', loc: ['body', 'av', 'controller'], msg: 'unknown controller' }],
        },
      }),
    } as unknown as Response);
    await expect(
      createSweep({
        scenario_id: 'scn-1',
        penetrations: [0.05],
        compliances: [0.8],
        controllers: ['jad'],
        replicates: 5,
        include_baseline: true,
      }),
    ).rejects.toThrow('cell (penetration=0.05, compliance=0.8, controller=jad) — av.controller: unknown controller');
  });

  it('latches the rejected-key state on 401 and clears it on the next success', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      json: async () => ({ detail: 'invalid or missing X-API-Key' }),
    } as unknown as Response);
    await expect(listRuns()).rejects.toThrow('invalid or missing X-API-Key');
    expect(isAuthFailed()).toBe(true);

    fetchMock.mockResolvedValue(fakeResponse([]));
    await listRuns();
    expect(isAuthFailed()).toBe(false);
  });

  it('retries the latched key once after the delay, then stops retrying', async () => {
    const unauthorized = {
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      json: async () => ({ detail: 'invalid or missing X-API-Key' }),
    } as unknown as Response;
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue(unauthorized);
      await expect(listRuns()).rejects.toThrow();
      expect(isAuthFailed()).toBe(true);
      expect(isAuthRetryScheduled()).toBe(true);

      // one transient 401 must not freeze the dashboard until a reload
      vi.advanceTimersByTime(AUTH_RETRY_DELAY_MS);
      expect(isAuthFailed()).toBe(false);
      expect(isAuthRetryScheduled()).toBe(false);

      // the key really is wrong: it re-latches and does NOT arm another
      // automatic retry, so a rejected key is not polled forever
      await expect(listRuns()).rejects.toThrow();
      expect(isAuthFailed()).toBe(true);
      expect(isAuthRetryScheduled()).toBe(false);
      vi.advanceTimersByTime(AUTH_RETRY_DELAY_MS * 4);
      expect(isAuthFailed()).toBe(true);

      // the banner's Retry gives the next latch its automatic attempt back
      clearAuthFailure();
      expect(isAuthFailed()).toBe(false);
      await expect(listRuns()).rejects.toThrow();
      expect(isAuthRetryScheduled()).toBe(true);
    } finally {
      clearAuthFailure();
      vi.useRealTimers();
    }
  });

  it('a successful call restores the automatic retry for a later latch', async () => {
    const unauthorized = {
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      json: async () => ({ detail: 'invalid or missing X-API-Key' }),
    } as unknown as Response;
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue(unauthorized);
      await expect(listRuns()).rejects.toThrow();
      vi.advanceTimersByTime(AUTH_RETRY_DELAY_MS); // spends the one retry
      fetchMock.mockResolvedValue(fakeResponse([]));
      await listRuns();
      expect(isAuthFailed()).toBe(false);

      fetchMock.mockResolvedValue(unauthorized);
      await expect(listRuns()).rejects.toThrow();
      expect(isAuthRetryScheduled()).toBe(true);
    } finally {
      clearAuthFailure();
      vi.useRealTimers();
    }
  });

  it('lists the server report history from GET /reports', async () => {
    fetchMock.mockResolvedValue(fakeResponse([]));
    await listReports();
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe(`${DEFAULT_BASE_URL}/reports`);
    await listReports(50);
    expect((fetchMock.mock.calls[1] as [string])[0]).toBe(`${DEFAULT_BASE_URL}/reports?limit=50`);
  });

  it('downloads the archive and the PDF from their own routes', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      blob: async () => new Blob(['zip']),
    } as unknown as Response);
    await getReportArchive('rpt-1');
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe(`${DEFAULT_BASE_URL}/reports/rpt-1/archive`);
    await getReportPdf('rpt-1');
    expect((fetchMock.mock.calls[1] as [string])[0]).toBe(`${DEFAULT_BASE_URL}/reports/rpt-1/pdf`);
  });
});

/** Reads may be answered by the in-browser demo backend while the API is
 * unreachable — the dashboard stays demoable and says DEMO everywhere it
 * does. Writes may not: a run "queued" or a report "generated" by the demo
 * store exists in this browser only, so the launch has to fail loudly instead
 * of succeeding against nothing. */
describe('api client writes during the offline fallback', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(fakeResponse([]));
    vi.stubGlobal('fetch', fetchMock);
    window.localStorage.clear();
    clearAuthFailure();
    setOfflineFallback(true); // as the Layout health poll would
  });

  afterEach(() => {
    setOfflineFallback(false);
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('refuses every write, without touching the network or the demo store', async () => {
    const runsBefore = (await mockListRuns()).length;
    const scenariosBefore = (await mockListScenarios()).length;
    const reportsBefore = (await mockListReports()).length;
    fetchMock.mockClear();

    const refused = [
      createScenario({
        name: 'x',
        tier: 'micro',
        network: { kind: 'ring', circumference_m: 230, n_vehicles: 22 },
        fleet: { model: 'IDM' },
        av: { penetration: 0, compliance: 1, controller: null },
        sim: { duration_s: 600 },
        seed: 42,
        replicates: 20,
      }),
      createRun({ scenario_id: 'scn-ring', replicates: 20 }),
      createSweep({
        scenario_id: 'scn-corridor',
        penetrations: [0.05],
        compliances: [0.8],
        controllers: ['follower_stopper'],
        replicates: 5,
        include_baseline: true,
      }),
      createReport(['run-8f2c11']),
    ];
    for (const p of refused) {
      await expect(p).rejects.toThrow(OFFLINE_WRITE_MESSAGE);
      await expect(p).rejects.toBeInstanceOf(ApiError);
    }

    // nothing was sent, and nothing was invented in the demo backend either
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await mockListRuns()).length).toBe(runsBefore);
    expect((await mockListScenarios()).length).toBe(scenariosBefore);
    expect((await mockListReports()).length).toBe(reportsBefore);
  });

  it('still serves reads from the demo backend', async () => {
    fetchMock.mockClear();
    expect((await listRuns()).length).toBeGreaterThan(0);
    expect((await listScenarios()).length).toBeGreaterThan(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

/** The demo backend's criteria profiles are a copy of the real registry
 * (`validation.criteria.CRITERIA_PROFILES`), not a convenience: a demo that
 * accepted any name, or defaulted to a different one, would hide a dashboard
 * sending something the API refuses with 422. */
describe('demo backend criteria profiles', () => {
  it('serves the registry with the real default profile marked default', async () => {
    const profiles = await mockListCriteriaProfiles();
    expect(profiles.length).toBeGreaterThan(1);
    // exactly one default, and it is the name the API applies to a request
    // that carries no profile
    expect(profiles.filter((p) => p.default).map((p) => p.name)).toEqual([
      DEFAULT_CRITERIA_PROFILE,
    ]);
    // registry order, default first, as GET /criteria serves it
    expect(profiles[0].name).toBe(DEFAULT_CRITERIA_PROFILE);
    // and every row states where its numbers come from
    expect(profiles.every((p) => p.source.length > 0)).toBe(true);
  });

  it('honours the requested profile and refuses one outside the registry', async () => {
    const chosen = (await mockListCriteriaProfiles()).find((p) => !p.default);
    expect(chosen).toBeDefined();
    const name = chosen?.name ?? '';
    const out = await mockCreateReport(['run-8f2c11'], undefined, name);
    expect(out.profile).toBe(name);

    // an unnamed profile is the default, exactly as ReportCreateRequest does
    expect((await mockCreateReport(['run-8f2c11'])).profile).toBe(DEFAULT_CRITERIA_PROFILE);

    // and an unknown name is refused, not quietly scored against the default
    await expect(mockCreateReport(['run-8f2c11'], undefined, 'not_a_profile')).rejects.toThrow(
      /unknown criteria profile/,
    );
  });
});
