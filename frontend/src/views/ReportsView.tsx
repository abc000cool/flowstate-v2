/** Reports: select finished MICRO runs, request an FHWA-style report, list
 * generated reports with markdown downloads. Macro (screening) selection is
 * disabled — mirrors the backend rule that screening-tier results cannot
 * support validation claims. */

import { useCallback, useState } from 'react';
import { createReport, getReportMarkdown, listRuns } from '../api/client';
import type { ReportRecord, RunSummary } from '../api/types';
import { SeededBadge, StatusChip, TierBadge } from '../components/bits';
import { toast, toastError } from '../components/toast';
import { usePoll } from '../lib/hooks';

const LS_REPORTS = 'flowstate.reports';

function loadReports(): ReportRecord[] {
  try {
    const raw = window.localStorage.getItem(LS_REPORTS);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as ReportRecord[]) : [];
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

const MACRO_TOOLTIP =
  'Screening tier cannot be validated — macro (CTM) results are labeled tier:"screening" and the API refuses to generate a validation report from them.';

export function ReportsView(): JSX.Element {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [reports, setReports] = useState<ReportRecord[]>(loadReports);
  const [busy, setBusy] = useState(false);

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
  usePoll(refresh, 5000);

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
    setBusy(true);
    try {
      const res = await createReport(ids);
      const rec: ReportRecord = {
        report_id: res.report_id,
        run_ids: ids,
        created_at: new Date().toISOString(),
      };
      const next = [rec, ...reports];
      setReports(next);
      saveReports(next);
      setSelected(new Set());
      toast('ok', `report ${res.report_id} generated`);
    } catch (err) {
      toastError(err, 'report');
    } finally {
      setBusy(false);
    }
  };

  const download = async (rec: ReportRecord): Promise<void> => {
    try {
      const md = await getReportMarkdown(rec.report_id);
      const blob = new Blob([md], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `flowstate-report-${rec.report_id}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      toastError(err, 'download');
    }
  };

  const microSelected = [...selected].filter((id) =>
    runs.some((r) => r.run_id === id && r.tier === 'micro'),
  );

  return (
    <div className="view">
      <div className="view-title">
        Validation Reports <span className="count mono">{reports.length} generated</span>
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
          <table className="data">
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
                    <td className="muted">{r.scenario_name ?? r.scenario_id}</td>
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
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Report</th>
                <th>Created</th>
                <th>Runs</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {reports.map((rec) => (
                <tr key={rec.report_id}>
                  <td style={{ fontWeight: 700 }}>{rec.report_id}</td>
                  <td className="muted">{rec.created_at.replace('T', ' ').slice(0, 19)} UTC</td>
                  <td className="muted">{rec.run_ids.join(', ')}</td>
                  <td>
                    <button className="btn sm" onClick={() => void download(rec)}>
                      Download .md
                    </button>
                  </td>
                </tr>
              ))}
              {reports.length === 0 && (
                <tr>
                  <td colSpan={4}>
                    <div className="empty">no reports generated in this browser yet</div>
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
