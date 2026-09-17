/** Typed fetch client for the FlowState API (base /api/v1, X-API-Key auth).
 *
 * Base URL and key live in localStorage (Settings drawer) with dev defaults.
 * Mock mode: VITE_MOCK=1 forces the in-memory backend; independently, when
 * /healthz is unreachable the app falls back to demo data and shows a banner
 * (see `setOfflineFallback`, driven by the Layout health poll).
 *
 * Connection state (offline fallback + rejected API key) is a tiny observable
 * store: `/healthz` is auth-exempt, so a wrong key leaves the health probe
 * green while every authenticated call 401s. Views subscribe through
 * `lib/hooks` and pause their polls while `isAuthFailed()`, instead of
 * retrying a rejected key forever behind a green status dot. The latch is not
 * a dead end: it schedules exactly one automatic retry (`AUTH_RETRY_DELAY_MS`)
 * so a single transient 401 — a restarting API, a key rotated server-side —
 * cannot freeze the dashboard until someone reloads it, and `clearAuthFailure`
 * is the shell's manual Retry. */

import * as mock from '../mocks/mockApi';
import type {
  CreateRunRequest,
  CreateScenarioResponse,
  CreateSweepRequest,
  CriteriaProfile,
  HeatField,
  Heatmap,
  ReportOut,
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

/** The acceptance-criteria profile a report is scored against when the
 * request names none — `api.schemas.DEFAULT_CRITERIA_PROFILE`. The dashboard
 * needs the name locally so a service too old to serve `GET /criteria` still
 * offers the profile its `POST /reports` will apply. */
export const DEFAULT_CRITERIA_PROFILE = 'fhwa_default';

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
  // a re-typed key must be retried, not blocked by the previous rejection
  clearAuthFailure();
}

/* -------------------- mock mode + connection state -------------------- */

const MOCK_ENV: boolean = `${import.meta.env.VITE_MOCK ?? ''}` === '1';
let offlineFallback = false;
let authFailed = false;

type ConnectionListener = () => void;
const connectionListeners = new Set<ConnectionListener>();

function notifyConnection(): void {
  for (const l of [...connectionListeners]) l();
}

/** Subscribe to connection-state changes (offline fallback, auth failure). */
export function subscribeConnection(l: ConnectionListener): () => void {
  connectionListeners.add(l);
  return () => {
    connectionListeners.delete(l);
  };
}

/** True when serving demo data (env-forced or offline auto-fallback).
 *
 * Reads only. A write must never be answered by the in-browser backend while
 * the auto-fallback is on: the demo store would report a scenario stored, a
 * run queued or a report generated that no server has heard of. Write
 * endpoints gate on `isMockEnv()` and then `assertWritable()`. */
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
  if (offlineFallback === v) return;
  offlineFallback = v;
  notifyConnection();
}

/** How long the rejected-key latch holds before it retries by itself. One
 * automatic attempt per latch: long enough that a wrong key is not hammered
 * (the walkthrough saw 95 GETs in 70 s), short enough that a restarting API or
 * a rotated key recovers without a reload. */
export const AUTH_RETRY_DELAY_MS = 30_000;

let authRetryTimer: number | null = null;
/** True once this latch has spent its one automatic retry; reset by a
 * successful call, by new settings, or by the banner's Retry. */
let autoRetryUsed = false;

/** True once the API rejected the configured key (401/403). Cleared by a
 * successful authenticated call, by saving new settings, by the shell's Retry
 * action, or by the single automatic retry scheduled when the latch closed. */
export function isAuthFailed(): boolean {
  return authFailed;
}

/** True while an automatic retry is still scheduled for the current latch. */
export function isAuthRetryScheduled(): boolean {
  return authRetryTimer !== null;
}

function cancelAuthRetry(): void {
  if (authRetryTimer === null) return;
  window.clearTimeout(authRetryTimer);
  authRetryTimer = null;
}

function setAuthFailedFlag(v: boolean): void {
  if (authFailed === v) return;
  authFailed = v;
  notifyConnection();
}

/** Latch a rejected key and arm the one automatic retry for this latch. */
function latchAuthFailure(): void {
  setAuthFailedFlag(true);
  if (autoRetryUsed || authRetryTimer !== null) return;
  authRetryTimer = window.setTimeout(() => {
    authRetryTimer = null;
    // spend the retry BEFORE unlatching: a still-rejected key re-latches
    // immediately on the resumed poll and must not re-arm the timer
    autoRetryUsed = true;
    setAuthFailedFlag(false);
  }, AUTH_RETRY_DELAY_MS);
}

/** Clear the rejected-key state and give the next latch its retry back (a new
 * key, a working call or an explicit Retry all deserve a fresh attempt). */
export function clearAuthFailure(): void {
  autoRetryUsed = false;
  cancelAuthRetry();
  setAuthFailedFlag(false);
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

/* --------------------- server error-detail rendering ------------------ */

/** `["body", "sim", "duration_s"]` -> `sim.duration_s` (the `body` prefix is
 * FastAPI plumbing, not something a user can act on). */
function locLabel(loc: unknown): string {
  if (!Array.isArray(loc)) return '';
  return loc
    .filter((p) => p !== 'body')
    .map((p) => String(p))
    .join('.');
}

/** One pydantic error entry -> `field: message`. */
function formatErrorEntry(e: unknown): string {
  if (typeof e === 'string') return e;
  if (e && typeof e === 'object') {
    const o = e as { loc?: unknown; msg?: unknown };
    const msg = typeof o.msg === 'string' ? o.msg : JSON.stringify(e);
    const field = locLabel(o.loc);
    return field ? `${field}: ${msg}` : msg;
  }
  return String(e);
}

/** Render a FastAPI `detail` for humans instead of dumping raw JSON.
 *
 * Three shapes reach the dashboard: a plain string, the pydantic error list
 * (`[{type, loc, msg, input}]` — `POST /runs`, `POST /scenarios`), and the
 * sweep cell wrapper `{cell: {...}, errors: [...]}` that `POST /sweeps`
 * raises when one grid cell fails validation (api/main.py). */
export function formatDetail(d: unknown): string {
  if (d === null || d === undefined) return '';
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map(formatErrorEntry).join('; ');
  if (typeof d === 'object') {
    const o = d as { cell?: unknown; errors?: unknown; msg?: unknown; detail?: unknown };
    if (Array.isArray(o.errors) || typeof o.errors === 'string') {
      const errs = Array.isArray(o.errors)
        ? o.errors.map(formatErrorEntry).join('; ')
        : String(o.errors);
      const cell = o.cell as Record<string, unknown> | undefined;
      if (cell && typeof cell === 'object') {
        const where = Object.entries(cell)
          .map(([k, v]) => `${k}=${v === null ? 'none' : String(v)}`)
          .join(', ');
        return `cell (${where}) — ${errs}`;
      }
      return errs;
    }
    if (typeof o.msg === 'string') return formatErrorEntry(d);
    return JSON.stringify(d);
  }
  return String(d);
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
    // /healthz is auth-exempt, so a rejected key shows up only here: latch it
    // so the shell can say so and every poll can stand down.
    if (res.status === 401 || res.status === 403) latchAuthFailure();
    let detail = '';
    try {
      const j: unknown = await res.json();
      if (j && typeof j === 'object' && 'detail' in j) {
        detail = formatDetail((j as { detail: unknown }).detail);
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail || `${res.status} ${res.statusText}`);
  }
  clearAuthFailure(); // the key works: drop the latch and restore its retry
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

async function requestBlob(path: string, init?: RequestInitLite): Promise<Blob> {
  const res = await rawFetch(path, init);
  return res.blob();
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

/* ------------------------- write-path guard --------------------------- */

/** What a write refused by the offline auto-fallback says — and what the
 * controls that would issue one say while they are disabled. */
export const OFFLINE_WRITE_MESSAGE =
  'API offline — reconnect before launching (nothing was sent to the server)';

/** Refuse a write while the demo fallback is serving reads.
 *
 * Reads may fall back to the in-memory backend (the dashboard stays
 * demoable, and every such surface is labelled DEMO). Writes may not: a
 * scenario, run, sweep or report accepted by the demo store exists in this
 * browser only, and reporting it as launched is exactly the unvalidated
 * claim the platform must never make. Status 0 marks an error raised by the
 * client, before any request left it. */
function assertWritable(): void {
  if (offlineFallback) throw new ApiError(0, OFFLINE_WRITE_MESSAGE);
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

/** Write: the demo backend answers only under VITE_MOCK, never under the
 * offline auto-fallback (see `assertWritable`). */
export async function createScenario(cfg: ScenarioConfig): Promise<CreateScenarioResponse> {
  if (isMockEnv()) return mock.mockCreateScenario(cfg);
  assertWritable();
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

/** Write: demo backend under VITE_MOCK only (see `assertWritable`). */
export async function createRun(req: CreateRunRequest): Promise<{ run_id: string }> {
  if (isMockEnv()) return mock.mockCreateRun(req);
  assertWritable();
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

/** `POST /sweeps` answers 202 with the full `SweepOut` (cells still without
 * runs until the fan-out job has started them). Write: demo backend under
 * VITE_MOCK only (see `assertWritable`). */
export async function createSweep(req: CreateSweepRequest): Promise<SweepDetail> {
  if (isMockEnv()) return mock.mockCreateSweep(req);
  assertWritable();
  return request<SweepDetail>('/sweeps', { method: 'POST', body: req });
}

export function getSweep(sweepId: string): Promise<SweepDetail> {
  if (isMockActive()) return mock.mockGetSweep(sweepId);
  return request<SweepDetail>(`/sweeps/${encodeURIComponent(sweepId)}`);
}

/** `GET /criteria` — the acceptance-criteria profiles `POST /reports` will
 * accept (the FlowState default plus the state-DOT protocols), each with the
 * `source` that states where its numbers come from.
 *
 * Thresholds only: this endpoint never carries a measurement, so a profile is
 * a choice of protocol, never evidence. A service older than the endpoint
 * answers 404; callers fall back to `DEFAULT_CRITERIA_PROFILE`, the profile
 * such a service applies anyway. */
export function listCriteriaProfiles(): Promise<CriteriaProfile[]> {
  if (isMockActive()) return mock.mockListCriteriaProfiles();
  return request<CriteriaProfile[]>('/criteria');
}

/** `POST /reports` is asynchronous: 202 with a `ReportOut` that is still
 * `queued` under the Redis queue (terminal only under the inline queue), so
 * callers must poll `getReport` until `done`/`failed`. A macro-only run set
 * is refused — 422 inline, or `status: failed` with
 * `error_kind: report_refused` from the queue. Write: demo backend under
 * VITE_MOCK only (see `assertWritable`).
 *
 * `profile` names the acceptance-criteria thresholds the report is scored
 * against (`listCriteriaProfiles`); the API refuses an unknown name with 422
 * and applies `DEFAULT_CRITERIA_PROFILE` when the field is omitted. The
 * request model forbids extra keys, so each optional field is sent only when
 * the caller gave one. */
export async function createReport(
  runIds: string[],
  title?: string,
  profile?: string,
): Promise<ReportOut> {
  if (isMockEnv()) return mock.mockCreateReport(runIds, title, profile);
  assertWritable();
  const body: Record<string, unknown> = { run_ids: runIds };
  if (title !== undefined) body.title = title;
  if (profile !== undefined) body.profile = profile;
  return request<ReportOut>('/reports', { method: 'POST', body });
}

/** `GET /reports` — every report the server holds, newest first (at most
 * `limit`, the API's own default and maximum being 200). This is the report
 * history; the browser's localStorage records are only a fallback for rows
 * this server does not have (a different server, or a report requested before
 * the endpoint existed). */
export function listReports(limit?: number): Promise<ReportOut[]> {
  if (isMockActive()) return mock.mockListReports(limit);
  const q = limit === undefined ? '' : `?limit=${encodeURIComponent(limit)}`;
  return request<ReportOut[]>(`/reports${q}`);
}

/** `GET /reports/{id}` — the report's status row (JSON), never its content. */
export function getReport(reportId: string): Promise<ReportOut> {
  if (isMockActive()) return mock.mockGetReport(reportId);
  return request<ReportOut>(`/reports/${encodeURIComponent(reportId)}`);
}

/** `GET /reports/{id}/markdown` — the rendered report text; the API answers
 * 409 while the report is queued/running and 422 when it was refused. */
export function getReportMarkdown(reportId: string): Promise<string> {
  if (isMockActive()) return mock.mockGetReportMarkdown(reportId);
  return requestText(`/reports/${encodeURIComponent(reportId)}/markdown`);
}

/** `GET /reports/{id}/archive` — the report bundle (markdown + figure PNGs)
 * as a zip. The markdown alone links figures it does not carry, so this is
 * the download that yields a readable report. */
export function getReportArchive(reportId: string): Promise<Blob> {
  if (isMockActive()) return mock.mockGetReportArchive(reportId);
  return requestBlob(`/reports/${encodeURIComponent(reportId)}/archive`);
}

/** `GET /reports/{id}/pdf` — the optional PDF rendering; the API answers 404
 * when the report was generated without one (`validation[pdf]` extra). */
export function getReportPdf(reportId: string): Promise<Blob> {
  if (isMockActive()) return mock.mockGetReportPdf(reportId);
  return requestBlob(`/reports/${encodeURIComponent(reportId)}/pdf`);
}
