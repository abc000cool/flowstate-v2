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
 * and the optional PDF. The contract has no report-list endpoint, so the table
 * below is this browser's own record and says so. */

import { useCallback, useMemo, useRef, useState } from 'react';
import {
  ApiError,
  createReport,
  getReport,
  getReportArchive,
  getReportMarkdown,
  getReportPdf,
  listRuns,
  listScenarios,
} from '../api/client';
import type { ReportOut, ReportRecord, RunSummary } from '../api/types';
import { SeededBadge, StatusChip, TierBadge } from '../components/bits';
import { toast, toastError } from '../components/toast';
import { saveBlob, saveText } from '../lib/download';
import { useAuthFailed, usePoll } from '../lib/hooks';

const LS_REPORTS = 'flowstate.reports';
const REPORT_POLL_MS = 2000;
const RUNS_POLL_MS = 5000;
const SCENARIOS_POLL_MS = 3000;

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

function recordFromOut(out: ReportOut, requestedRunIds: string[]): ReportRecord {
  return {
    report_id: out.report_id,
    run_ids: out.run_ids.length > 0 ? out.run_ids : requestedRunIds,
    title: out.title,
    status: out.status,
    error: out.error ?? null,
    error_kind: out.error_kind ?? null,
    created_at: out.created_at,
  };
}

const MACRO_TOOLTIP =
  'Screening tier cannot be validated — macro (CTM) results are labeled tier:"screening" and the API refuses to generate a validation report from them.';

export function ReportsView(): JSX.Element {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [scenarios, setScenarios] = useState<{ scenario_id: string; name: string }[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [reports, setReports] = useState<ReportRecord[]>(loadReports);
  const [busy, setBusy] = useState(false);
  const authFailed = useAuthFailed();
  // latest list for the (referentially stable) poll callback
  const reportsRef = useRef(reports);
  reportsRef.current = reports;

  const commit = useCallback((next: ReportRecord[]): void => {
    reportsRef.current = next;
    setReports(next);
    saveReports(next);
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
          });
        } catch (err) {
          // a vanished report (store reset) is terminal; anything else is
          // transient and retried on the next tick
          if (err instanceof ApiError && err.status === 404) {
            updates.set(rec.report_id, { status: 'failed', error: err.message, error_kind: 'not_found' });
          }
        }
      }),
    );
    if (updates.size === 0) return;
    commit(
      reportsRef.current.map((r) => {
        const u = updates.get(r.report_id);
        return u ? { ...r, ...u } : r;
      }),
    );
    for (const [id, u] of updates) {
      if (u.status === 'done') toast('ok', `report ${id} ready`);
      else if (u.status === 'failed') toast('error', `report ${id} failed: ${u.error ?? 'unknown error'}`);
    }
  }, [commit]);
  const anyPending = reports.some(isPending);
  usePoll(pollReports, anyPending && !authFailed ? REPORT_POLL_MS : null);

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
    setBusy(true);
    try {
      const out = await createReport(ids);
      const rec = recordFromOut(out, ids);
      commit([rec, ...reportsRef.current.filter((r) => r.report_id !== rec.report_id)]);
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
        Validation Reports <span className="count mono">{reports.length} requested</span>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Finished runs — pick micro runs to report</span>
          <span className="spacer" />
          <button
            className="btn primary"
            disabled={busy || microSelected.length === 0}
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
            title="The API contract has no report-list endpoint; this table is this browser's own record."
          >
            this browser's record only — reports requested elsewhere are not listed
          </span>
        </div>
        <div className="table-wrap">
          <table className="data" aria-label="generated reports">
            <thead>
              <tr>
                <th>Report</th>
                <th>Status</th>
                <th>Created</th>
                <th>Runs</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {reports.map((rec) => (
                <tr key={rec.report_id}>
                  <td style={{ fontWeight: 700 }}>{rec.report_id}</td>
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
              {reports.length === 0 && (
                <tr>
                  <td colSpan={5}>
                    <div className="empty">no reports requested in this browser yet</div>
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
