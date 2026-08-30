import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRun, DEFAULT_API_KEY, DEFAULT_BASE_URL, listRuns } from '../api/client';

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
