/** Typed fetch client for the FlowState API (base /api/v1, X-API-Key auth).
 *
 * Base URL and key live in localStorage (Settings drawer) with dev defaults.
 * Mock mode: VITE_MOCK=1 forces the in-memory backend; independently, when
 * /healthz is unreachable the app falls back to demo data and shows a banner
 * (see `setOfflineFallback`, driven by the Layout health poll). */

import * as mock from '../mocks/mockApi';
import type {
  CreateRunRequest,
  CreateScenarioResponse,
  CreateSweepRequest,
  HeatField,
  Heatmap,
  RunDetail,
  RunMetrics,
  RunSummary,
  ScenarioConfig,
  PresetSummary,
  ScenarioSummary,
  SweepDetail,
} from './types';

export const DEFAULT_BASE_URL = '/api/v1';
// Matches the key docker-compose.yml sets for a local stack, so the bundled
// dashboard talks to the bundled API out of the box. Any other deployment sets
// FLOWSTATE_API_KEY server-side and pastes the same value into Settings.
export const DEFAULT_API_KEY = 'dev-key-change-me'; // the API's inline-queue default (api.settings.DEFAULT_API_KEY)

const LS_BASE = 'flowstate.apiBase';
const LS_KEY = 'flowstate.apiKey';

export interface ApiSettings {
  baseUrl: string;
  apiKey: string;
}

function lsGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function getSettings(): ApiSettings {
  return {
    baseUrl: lsGet(LS_BASE) || DEFAULT_BASE_URL,
    apiKey: lsGet(LS_KEY) || DEFAULT_API_KEY,
  };
}

export function saveSettings(s: ApiSettings): void {
  try {
    window.localStorage.setItem(LS_BASE, s.baseUrl.replace(/\/+$/, '') || DEFAULT_BASE_URL);
    window.localStorage.setItem(LS_KEY, s.apiKey || DEFAULT_API_KEY);
  } catch {
    /* storage unavailable — session-only settings */
  }
}

/* ------------------------------ mock mode ----------------------------- */

const MOCK_ENV: boolean = `${import.meta.env.VITE_MOCK ?? ''}` === '1';
let offlineFallback = false;

/** True when serving demo data (env-forced or offline auto-fallback). */
export function isMockActive(): boolean {
  return MOCK_ENV || offlineFallback;
}

export function isMockEnv(): boolean {
  return MOCK_ENV;
}

export function isOfflineFallback(): boolean {
  return offlineFallback;
}

export function setOfflineFallback(v: boolean): void {
  offlineFallback = v;
}

/* ------------------------------- fetch -------------------------------- */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

interface RequestInitLite {
  method?: string;
  body?: unknown;
}

async function rawFetch(path: string, init?: RequestInitLite): Promise<Response> {
  const { baseUrl, apiKey } = getSettings();
  const headers: Record<string, string> = { 'X-API-Key': apiKey };
  let body: string | undefined;
  if (init?.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(init.body);
  }
  const res = await fetch(`${baseUrl}${path}`, {
    method: init?.method ?? 'GET',
    headers,
    body,
  });
  if (!res.ok) {
    let detail = '';
    try {
      const j: unknown = await res.json();
      if (j && typeof j === 'object' && 'detail' in j) {
        const d = (j as { detail: unknown }).detail;
        detail = typeof d === 'string' ? d : JSON.stringify(d);
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail || `${res.status} ${res.statusText}`);
  }
  return res;
}

async function request<T>(path: string, init?: RequestInitLite): Promise<T> {
  const res = await rawFetch(path, init);
  return (await res.json()) as T;
}

async function requestText(path: string, init?: RequestInitLite): Promise<string> {
  const res = await rawFetch(path, init);
  return res.text();
}

/** /healthz lives at the server root, not under /api/v1. */
export function healthUrl(): string {
  const { baseUrl } = getSettings();
  const root = baseUrl.replace(/\/api\/v1\/?$/, '');
  return `${root}/healthz`;
}

/** Probe the API; used by the status dot and the offline auto-fallback. */
export async function checkHealth(timeoutMs = 2500): Promise<boolean> {
  if (MOCK_ENV) return false; // env mock never claims a live link
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(healthUrl(), { signal: ctrl.signal });
    // an SPA-fallback server answers 200 text/html for any path — that is
    // not a live API, so require a non-HTML health response
    const ct = res.headers.get('content-type') ?? '';
    return res.ok && !ct.includes('text/html');
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/* ------------------------------ endpoints ----------------------------- */

export function listScenarios(): Promise<ScenarioSummary[]> {
  if (isMockActive()) return mock.mockListScenarios();
  return request<ScenarioSummary[]>('/scenarios');
}

export function listPresetScenarios(): Promise<PresetSummary[]> {
  if (isMockActive()) return mock.mockListPresets();
  return request<PresetSummary[]>('/scenarios/preset');
}

export function createScenario(cfg: ScenarioConfig): Promise<CreateScenarioResponse> {
  if (isMockActive()) return mock.mockCreateScenario(cfg);
  return request<CreateScenarioResponse>('/scenarios', { method: 'POST', body: cfg });
}

export function listRuns(): Promise<RunSummary[]> {
  if (isMockActive()) return mock.mockListRuns();
  return request<RunSummary[]>('/runs');
}

export function getRun(runId: string): Promise<RunDetail> {
  if (isMockActive()) return mock.mockGetRun(runId);
  return request<RunDetail>(`/runs/${encodeURIComponent(runId)}`);
}

export function createRun(req: CreateRunRequest): Promise<{ run_id: string }> {
  if (isMockActive()) return mock.mockCreateRun(req);
  return request<{ run_id: string }>('/runs', { method: 'POST', body: req });
}

export function getRunMetrics(runId: string): Promise<RunMetrics> {
  if (isMockActive()) return mock.mockGetRunMetrics(runId);
  return request<RunMetrics>(`/runs/${encodeURIComponent(runId)}/metrics`);
}

export function getRunHeatmap(runId: string, field: HeatField): Promise<Heatmap> {
  if (isMockActive()) return mock.mockGetRunHeatmap(runId, field);
  return request<Heatmap>(`/runs/${encodeURIComponent(runId)}/heatmap?field=${field}`);
}

export function createSweep(req: CreateSweepRequest): Promise<{ sweep_id: string }> {
  if (isMockActive()) return mock.mockCreateSweep(req);
  return request<{ sweep_id: string }>('/sweeps', { method: 'POST', body: req });
}

export function getSweep(sweepId: string): Promise<SweepDetail> {
  if (isMockActive()) return mock.mockGetSweep(sweepId);
  return request<SweepDetail>(`/sweeps/${encodeURIComponent(sweepId)}`);
}

export function createReport(runIds: string[]): Promise<{ report_id: string }> {
  if (isMockActive()) return mock.mockCreateReport(runIds);
  return request<{ report_id: string }>('/reports', { method: 'POST', body: { run_ids: runIds } });
}

export function getReportMarkdown(reportId: string): Promise<string> {
  if (isMockActive()) return mock.mockGetReport(reportId);
  return requestText(`/reports/${encodeURIComponent(reportId)}`);
}
