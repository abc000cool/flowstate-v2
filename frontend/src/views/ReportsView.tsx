/** Reports: select finished MICRO runs, request an FHWA-style report, then
 * track it to completion. `POST /reports` is asynchronous (202): under the
 * Redis queue the report comes back `queued` and only later turns `done` or
 * `failed` (a screening-only run set is refused as
 * `error_kind=report_refused`), so the list polls `GET /reports/{id}` while
 * anything is pending and the markdown download (`GET /reports/{id}/markdown`)
 * is enabled only once a report is done. Macro (screening) selection is
 * disabled — mirrors the backend rule that screening-tier results cannot
 * support validation claims.
 *
 * Three downloads are offered: the markdown (which *links* its figures), the
 * zip archive (markdown + figure PNGs — the one that is readable on its own)
 * and the optional PDF.
 *
 * The table is `GET /reports` (the server's own history, newest first) merged
 * with this browser's localStorage records, which now cover only what the
 * server list does not return — a report requested against another API, or
 * before the list endpoint existed. Each row says which it is, because a local
 * row is a memory of a request, not evidence the server still holds the
 * bundle. `ReportOut.report_path` is results-root-relative and is never shown:
 * it is an identifier for the bundle, not a path or a link this browser can
 * follow — the downloads go through the three routes.
 *
 * Both polls run through the same client as everything else, so while the API
 * is unreachable they are answered by the in-browser demo backend. Neither may
 * launder that into evidence: each captures its source at call time and a
 * demo-sourced row is badged DEMO, never SERVER, is not written to
 * localStorage, and never raises the "ready" toast — a real report that was
 * queued when the API went away must not come back done from demo data. */

import { useCallback, useMemo, useRef, useState } from 'react';
import {
  ApiError,
  createReport,
  getReport,
  getReportArchive,
  getReportMarkdown,
  getReportPdf,
  isMockActive,
  listReports,
  listRuns,
  listScenarios,
  OFFLINE_WRITE_MESSAGE,
} from '../api/client';
import type { ReportOut, ReportRecord, RunSummary } from '../api/types';
import { SeededBadge, StatusChip, TierBadge } from '../components/bits';
import { toast, toastError } from '../components/toast';
import { saveBlob, saveText } from '../lib/download';
import { useAuthFailed, useOfflineFallback, usePoll } from '../lib/hooks';

const LS_REPORTS = 'flowstate.reports';
const REPORT_POLL_MS = 2000;
const REPORT_LIST_POLL_MS = 3000;
/** Retry interval once `GET /reports` has answered 404 (a service older than
 * the list endpoint). The table already runs on local records and says so, so
 * the only thing left to watch for is the service being upgraded — polling a
 * route that does not exist at 3 s is the same hot 401 loop this view stands
 * down from elsewhere. */
const REPORT_LIST_RETRY_MS = 60_000;
const RUNS_POLL_MS = 5000;
const SCENARIOS_POLL_MS = 3000;

/** Where a table row came from. A `server` row is `GET /reports`; a `local`
 * row is only this browser's memory of a request the server list does not
 * return (another API, or one older than the endpoint); a `demo` row came
 * from the in-browser backend while the API was unreachable and is evidence
 * of nothing at all. */
type RowOrigin = 'server' | 'local' | 'demo';

interface ReportRow {
  rec: ReportRecord;
  origin: RowOrigin;
}

const isPending = (r: ReportRecord): boolean => r.status === 'queued' || r.status === 'running';

/** Records written before reports carried a status have none; they are
 * treated as pending so the next poll resolves their real state from the API. */
function normalizeRecord(raw: unknown): ReportRecord | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Partial<ReportRecord>;
  if (typeof r.report_id !== 'string') return null;
  const status =
    r.status === 'queued' || r.status === 'running' || r.status === 'done' || r.status === 'failed'
      ? r.status
      : 'queued';
  return {
    report_id: r.report_id,
    run_ids: Array.isArray(r.run_ids) ? r.run_ids.map(String) : [],
    title: typeof r.title === 'string' ? r.title : undefined,
    status,
    error: typeof r.error === 'string' ? r.error : null,
    error_kind: typeof r.error_kind === 'string' ? r.error_kind : null,
    created_at: typeof r.created_at === 'string' ? r.created_at : new Date().toISOString(),
  };
}

function loadReports(): ReportRecord[] {
  try {
    const raw = window.localStorage.getItem(LS_REPORTS);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map(normalizeRecord).filter((r): r is ReportRecord => r !== null);
  } catch {
    return [];
  }
}

function saveReports(list: ReportRecord[]): void {
  try {
    window.localStorage.setItem(LS_REPORTS, JSON.stringify(list));
  } catch {
    /* per-browser convenience only */
  }
}

function recordFromOut(out: ReportOut, requestedRunIds: string[] = [], demo = false): ReportRecord {
  // `out.report_path` is deliberately dropped: it is results-root-relative,
  // meaningless to this browser, and must never be rendered as a path or link.
  return {
    report_id: out.report_id,
    run_ids: out.run_ids.length > 0 ? out.run_ids : requestedRunIds,
    title: out.title,
    status: out.status,
    error: out.error ?? null,
    error_kind: out.error_kind ?? null,
    created_at: out.created_at,
    demo,
  };
}

/** The server's list (already newest first) followed by the local records it
 * does not cover, newest first. A report the server knows about is shown from
 * the server's row: its status is authoritative.
 *
 * `serverDemo` says the list came from the in-browser backend, not a server:
 * those rows are badged DEMO. A local record whose status was resolved from
 * demo data carries the same flag on itself. */
function mergeRows(
  server: ReportOut[] | null,
  serverDemo: boolean,
  local: ReportRecord[],
): ReportRow[] {
  const serverOrigin: RowOrigin = serverDemo ? 'demo' : 'server';
  const rows: ReportRow[] = (server ?? []).map((out) => ({
    rec: recordFromOut(out, [], serverDemo),
    origin: serverOrigin,
  }));
  const known = new Set(rows.map((r) => r.rec.report_id));
  const extras = local
    .filter((r) => !known.has(r.report_id))
    .slice()
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  return [
    ...rows,
    ...extras.map((rec) => ({ rec, origin: (rec.demo ? 'demo' : 'local') as RowOrigin })),
  ];
}

const MACRO_TOOLTIP =
  'Screening tier cannot be validated — macro (CTM) results are labeled tier:"screening" and the API refuses to generate a validation report from them.';

export function ReportsView(): JSX.Element {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [scenarios, setScenarios] = useState<{ scenario_id: string; name: string }[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [reports, setReports] = useState<ReportRecord[]>(loadReports);
  const [serverReports, setServerReports] = useState<ReportOut[] | null>(null);
  /** Whether the list currently held in `serverReports` came from the demo
   * backend rather than a server. */
  const [serverDemo, setServerDemo] = useState(false);
  /** False once the service answered 404 to `GET /reports` (an API older than
   * the list endpoint): the table then runs on local records alone and says
   * so, instead of claiming the server has no reports. */
  const [serverListed, setServerListed] = useState(true);
  const [busy, setBusy] = useState(false);
  const authFailed = useAuthFailed();
  const offline = useOfflineFallback();
  // latest list for the (referentially stable) poll callback
  const reportsRef = useRef(reports);
  reportsRef.current = reports;

  const commit = useCallback((next: ReportRecord[]): void => {
    reportsRef.current = next;
    setReports(next);
    saveReports(next);
  }, []);

  /** Show an update without recording it. Demo answers move the table (a demo
   * report must not be stranded at `queued`) but are never persisted: a
   * localStorage record is this browser's memory of a *request to a server*,
   * and writing in-browser state there would make it indistinguishable from
   * one on the next load. */
  const show = useCallback((next: ReportRecord[]): void => {
    reportsRef.current = next;
    setReports(next);
  }, []);

  // continuous quiet poll — newly finished runs appear without a reload, and
  // the offline-fallback race resolves on the next tick
  const refresh = useCallback(async () => {
    try {
      const all = await listRuns();
      setRuns(all.filter((r) => r.status === 'done'));
    } catch {
      /* connectivity surfaced by the status dot / banner */
    }
  }, []);
  usePoll(refresh, authFailed ? null : RUNS_POLL_MS);

  // the server's report history — the table's source of truth, so a report
  // requested in another browser (or after this one cleared its site data) is
  // still listed and still downloadable
  const refreshServerReports = useCallback(async () => {
    // capture the source before the call: the client decides demo vs live at
    // call time, and a list that came back from the demo backend must not be
    // rendered as this server's history
    const demo = isMockActive();
    try {
      const list = await listReports();
      setServerReports(list);
      setServerDemo(demo);
      setServerListed(true);
    } catch (err) {
      // 404 = this service predates GET /reports; anything else is transient
      if (err instanceof ApiError && err.status === 404) setServerListed(false);
    }
  }, []);
  usePoll(
    refreshServerReports,
    authFailed ? null : serverListed ? REPORT_LIST_POLL_MS : REPORT_LIST_RETRY_MS,
  );

  // the API's RunOut carries no scenario name, only the id: resolve it once
  const loadScenarios = useCallback(async () => {
    try {
      setScenarios(await listScenarios());
    } catch {
      /* quiet; the table falls back to the id */
    }
  }, []);
  usePoll(loadScenarios, authFailed || scenarios.length > 0 ? null : SCENARIOS_POLL_MS);
  const scenarioNames = useMemo(
    () => new Map(scenarios.map((s) => [s.scenario_id, s.name])),
    [scenarios],
  );

  // status poll for queued/running reports; paused when nothing is pending
  const pollReports = useCallback(async () => {
    // capture the source before the calls: the poll is not paused in demo mode
    // (that would strand a demo report at `queued` forever), but what comes
    // back is in-browser data and is labelled, not persisted, and not
    // announced as a finished report
    const demo = isMockActive();
    const pending = reportsRef.current.filter(isPending);
    if (pending.length === 0) return;
    const updates = new Map<string, Partial<ReportRecord>>();
    await Promise.all(
      pending.map(async (rec) => {
        try {
          const out = await getReport(rec.report_id);
          updates.set(rec.report_id, {
            status: out.status,
            error: out.error ?? null,
            error_kind: out.error_kind ?? null,
            run_ids: out.run_ids.length > 0 ? out.run_ids : rec.run_ids,
            title: out.title,
            demo,
          });
        } catch (err) {
          // a vanished report (store reset) is terminal; anything else is
          // transient and retried on the next tick — including the demo
          // backend's "not in this session", so a real queued report the
          // fallback cannot answer for keeps its own status
          if (err instanceof ApiError && err.status === 404) {
            updates.set(rec.report_id, { status: 'failed', error: err.message, error_kind: 'not_found' });
          }
        }
      }),
    );
    if (updates.size === 0) return;
    const next = reportsRef.current.map((r) => {
      const u = updates.get(r.report_id);
      return u ? { ...r, ...u } : r;
    });
    if (demo) show(next);
    else commit(next);
    for (const [id, u] of updates) {
      // "ready" is a claim about a generated bundle; demo data cannot make it
      if (u.status === 'done' && !demo) toast('ok', `report ${id} ready`);
      else if (u.status === 'failed') toast('error', `report ${id} failed: ${u.error ?? 'unknown error'}`);
    }
  }, [commit, show]);
  const anyPending = reports.some(isPending);
  usePoll(pollReports, anyPending && !authFailed ? REPORT_POLL_MS : null);

  const rows = useMemo(
    () => mergeRows(serverReports, serverDemo, reports),
    [serverReports, serverDemo, reports],
  );
  const localOnly = rows.filter((r) => r.origin === 'local').length;
  const demoRows = rows.filter((r) => r.origin === 'demo').length;

  const toggleRun = (id: string): void => {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const generate = async (): Promise<void> => {
    const ids = [...selected];
    if (ids.length === 0) return;
    // defensive: the checkboxes disable macro rows, but a screening run must
    // never reach POST /reports — screening results cannot be validated
    const macro = ids.filter((id) => runs.some((r) => r.run_id === id && r.tier === 'macro'));
    if (macro.length > 0) {
      toast('error', `macro (screening) runs cannot be reported: ${macro.join(', ')}`);
      return;
    }
    // POST /reports never falls back to the demo backend (api/client), so this
    // is only demo data under VITE_MOCK — labelled, and not recorded as a
    // request some server is holding
    const demo = isMockActive();
    setBusy(true);
    try {
      const out = await createReport(ids);
      const rec = recordFromOut(out, ids, demo);
      const next = [rec, ...reportsRef.current.filter((r) => r.report_id !== rec.report_id)];
      if (demo) show(next);
      else commit(next);
      setSelected(new Set());
      if (rec.status === 'done') toast('ok', `report ${rec.report_id} generated`);
      else if (rec.status === 'failed')
        toast('error', `report ${rec.report_id} failed: ${rec.error ?? 'unknown error'}`);
      else toast('info', `report ${rec.report_id} queued — download unlocks once it is done`);
    } catch (err) {
      toastError(err, 'report');
    } finally {
      setBusy(false);
    }
  };

  const downloadMarkdown = async (rec: ReportRecord): Promise<void> => {
    try {
      const md = await getReportMarkdown(rec.report_id);
      // the markdown links its figures rather than carrying them
      saveText(md, `flowstate-report-${rec.report_id}.md`);
    } catch (err) {
      toastError(err, 'download');
    }
  };

  const downloadArchive = async (rec: ReportRecord): Promise<void> => {
    try {
      const zip = await getReportArchive(rec.report_id);
      saveBlob(zip, `flowstate-report-${rec.report_id}.zip`);
    } catch (err) {
      toastError(err, 'archive');
    }
  };

  const downloadPdf = async (rec: ReportRecord): Promise<void> => {
    try {
      const pdf = await getReportPdf(rec.report_id);
      saveBlob(pdf, `flowstate-report-${rec.report_id}.pdf`);
    } catch (err) {
      // 404 = the report was generated without the optional PDF rendering
      toastError(err, 'pdf');
    }
  };

  const microSelected = [...selected].filter((id) =>
    runs.some((r) => r.run_id === id && r.tier === 'micro'),
  );

  return (
    <div className="view">
      <div className="view-title">
        Validation Reports <span className="count mono">{rows.length} reports</span>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Finished runs — pick micro runs to report</span>
          <span className="spacer" />
          <button
            className="btn primary"
            disabled={busy || microSelected.length === 0 || offline}
            title={offline ? OFFLINE_WRITE_MESSAGE : undefined}
            onClick={() => void generate()}
          >
            Generate report ({microSelected.length})
          </button>
        </div>
        <div className="table-wrap">
          <table className="data" aria-label="finished runs">
            <thead>
              <tr>
                <th style={{ width: 34 }} />
                <th>Run</th>
                <th>Scenario</th>
                <th>Tier</th>
                <th>Status</th>
                <th>Config hash</th>
                <th>Labels</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const macro = r.tier === 'macro';
                return (
                  <tr key={r.run_id} className={macro ? 'disabled' : ''} title={macro ? MACRO_TOOLTIP : undefined}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`select ${r.run_id}`}
                        disabled={macro}
                        checked={selected.has(r.run_id)}
                        onChange={() => toggleRun(r.run_id)}
                        style={{ accentColor: 'var(--accent)' }}
                      />
                    </td>
                    <td style={{ fontWeight: 700 }}>{r.run_id}</td>
                    <td className="muted" title={r.scenario_id}>
                      {r.scenario_name ?? scenarioNames.get(r.scenario_id) ?? r.scenario_id}
                    </td>
                    <td>
                      <TierBadge tier={r.tier} />
                    </td>
                    <td>
                      <StatusChip status={r.status} />
                    </td>
                    <td className="hash">{r.config_hash}</td>
                    <td>
                      <SeededBadge seeded={r.seeded} />
                    </td>
                  </tr>
                );
              })}
              {runs.length === 0 && (
                <tr>
                  <td colSpan={7}>
                    <div className="empty">no finished runs yet</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Generated reports</span>
          <span className="spacer" />
          <span
            className="small muted"
            title={
              demoRows > 0
                ? 'The API is unreachable, so these rows come from the built-in demo backend. Nothing here was generated by a server, and no status shown for a DEMO row is evidence about a real report.'
                : serverListed
                  ? 'GET /reports, newest first. Rows badged LOCAL exist only in this browser: they were requested against another API, or before the list endpoint existed, so this server may not hold them.'
                  : 'This service answered 404 to GET /reports, so only this browser’s own records can be listed.'
            }
          >
            {demoRows > 0
              ? `built-in demo data — ${demoRows} row${demoRows === 1 ? '' : 's'} from no server`
              : serverListed
                ? localOnly > 0
                  ? `server history (GET /reports) + ${localOnly} local-only record${localOnly === 1 ? '' : 's'}`
                  : 'server history (GET /reports), newest first'
                : "this service has no GET /reports — this browser's records only"}
          </span>
        </div>
        <div className="table-wrap">
          <table className="data" aria-label="generated reports">
            <thead>
              <tr>
                <th>Report</th>
                <th>Source</th>
                <th>Status</th>
                <th>Created</th>
                <th>Runs</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map(({ rec, origin }) => (
                <tr key={rec.report_id}>
                  <td style={{ fontWeight: 700 }}>{rec.report_id}</td>
                  <td>
                    {origin === 'server' && (
                      <span className="tag server" title="Listed by GET /reports — held by this server">
                        SERVER
                      </span>
                    )}
                    {origin === 'local' && (
                      <span
                        className="tag demo"
                        title="This browser's own record: GET /reports did not return it, so this server may not hold the bundle (requested against another API, or before the list endpoint existed)."
                      >
                        LOCAL
                      </span>
                    )}
                    {origin === 'demo' && (
                      <span
                        className="tag demo"
                        title="Built-in demo data, not a server answer: the API was unreachable when this row was read, so no server has generated or is holding this report."
                      >
                        DEMO
                      </span>
                    )}
                  </td>
                  <td>
                    <StatusChip status={rec.status} />
                    {rec.error && (
                      <div className="small" style={{ color: 'var(--danger)', marginTop: 4 }}>
                        {rec.error_kind ? `${rec.error_kind}: ` : ''}
                        {rec.error}
                      </div>
                    )}
                  </td>
                  <td className="muted">{rec.created_at.replace('T', ' ').slice(0, 19)} UTC</td>
                  <td className="muted">{rec.run_ids.join(', ')}</td>
                  <td>
                    <div className="row wrap" style={{ gap: 6 }}>
                      <button
                        className="btn sm"
                        disabled={rec.status !== 'done'}
                        title={
                          rec.status === 'done'
                            ? 'Markdown only — its figures are linked, not embedded'
                            : `report is ${rec.status} — the markdown is served only once it is done`
                        }
                        onClick={() => void downloadMarkdown(rec)}
                      >
                        Download .md
                      </button>
                      <button
                        className="btn sm"
                        disabled={rec.status !== 'done'}
                        title={
                          rec.status === 'done'
                            ? 'Markdown plus the figure PNGs it references'
                            : `report is ${rec.status} — the archive is served only once it is done`
                        }
                        onClick={() => void downloadArchive(rec)}
                      >
                        Download .zip (with figures)
                      </button>
                      <button
                        className="btn sm"
                        disabled={rec.status !== 'done'}
                        title={
                          rec.status === 'done'
                            ? 'Optional PDF rendering — 404 when the report was generated without one'
                            : `report is ${rec.status} — the PDF is served only once it is done`
                        }
                        onClick={() => void downloadPdf(rec)}
                      >
                        Download PDF
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6}>
                    <div className="empty">
                      {serverListed
                        ? 'no reports on this server yet'
                        : 'no reports requested in this browser yet'}
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
