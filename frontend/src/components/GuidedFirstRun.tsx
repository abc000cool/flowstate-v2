/** Guided first run — the ten-minute path of docs/QUICKSTART.md as a panel.
 *
 * A traffic engineer who has just been handed the dashboard has no way of
 * knowing that a preset is not a stored scenario, that a run is asynchronous,
 * or that a report is requested from a *finished* run. The six steps below are
 * exactly the QUICKSTART sequence (API → preset → 2-replicate smoke run →
 * metrics/heatmap → report) with live state on each one, so the path is
 * walkable without the document.
 *
 * Two honesty rules govern it.
 *
 * (1) **Nothing here is progress the server did not report.** Every step is
 * `done`, `current` or `blocked`, and `done` is only ever set by an answer
 * from the API (a stored `scenario_id`, a `RunOut` that says `done`, the
 * `RunMetrics` the metrics route handed over, a `ReportOut` that says
 * `done`) — never by a click, which is a thing the browser did and not a
 * thing the service answered. While the demo fallback is serving reads —
 * or under `VITE_MOCK` — every step that needs the server is `blocked` with
 * the reason (`OFFLINE_WRITE_MESSAGE`, the same refusal `api/client` raises),
 * and reads captured from the in-browser backend are discarded rather than
 * shown as this server's state. A checklist that ticked itself off demo data
 * would be exactly the unvalidated claim the platform must never make.
 *
 * (2) **Two replicates is a smoke test, not a result.** The launch is
 * deliberately `SMOKE_REPLICATES = 2` because that is what makes the
 * walkthrough fast, and the step says in full what that costs: a headline
 * number is a mean with a 95% CI over at least `MIN_REPLICATES` seeds
 * (CLAUDE.md §0.6), so the metrics come back `underpowered` and the report's
 * `n_seeds` criterion fails. That is the honest output, and the panel says so
 * before the click rather than after it.
 *
 * No new endpoints: every call is an `api/client` function the other views
 * already use, and the preset is stored through `lib/library.ensureStored`,
 * the same path the Scenarios and Runs launchers take. */

import { useCallback, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  ApiError,
  createReport,
  createRun,
  DEFAULT_CRITERIA_PROFILE,
  getReport,
  getReportMarkdown,
  getRun,
  getRunMetrics,
  getSettings,
  isMockActive,
  isMockEnv,
  listCriteriaProfiles,
  listPresetScenarios,
  listRuns,
  OFFLINE_WRITE_MESSAGE,
} from '../api/client';
import type { PresetSummary, ReportOut, RunDetail, RunMetrics } from '../api/types';
import { saveText } from '../lib/download';
import { failureReason } from '../lib/format';
import { useAuthFailed, useOfflineFallback, usePoll } from '../lib/hooks';
import { ensureStored } from '../lib/library';
import { warmupProblem } from '../lib/limits';
import { MIN_REPLICATES } from '../lib/metrics';
import { ProgressBar, StatusChip } from './bits';
import { toast, toastError } from './toast';

/** The preset the walkthrough runs: the canonical emergence benchmark, and
 * the only scenario in the repo that needs no data file on the machine
 * (docs/QUICKSTART.md §4, CLAUDE.md §3.2.1). */
export const RING_PRESET_FILENAME = 'ring_sugiyama.yaml';
export const RING_PRESET_NAME = 'ring_sugiyama';

/** Replicates the guided launch commits to. Two, because the point is to
 * reach a result in a few clicks — and it is labelled a smoke test
 * everywhere it appears. */
export const SMOKE_REPLICATES = 2;

/** Said at the launch step, before the click. */
export const SMOKE_NOTE =
  `${SMOKE_REPLICATES} replicates is a smoke test, not a result: a headline number is a ` +
  `mean with a 95% CI over at least ${MIN_REPLICATES} seeds (CLAUDE.md §0.6). The metrics ` +
  `come back marked underpowered and the report's n_seeds criterion fails — that is the ` +
  `honest output, not a defect. Re-run the preset at its own ${MIN_REPLICATES} replicates ` +
  `for anything quotable.`;

/** Why a step that needs the server is blocked under `VITE_MOCK`. */
export const MOCK_ENV_REASON =
  'Demo mode (VITE_MOCK=1) — this dashboard is answering itself, so nothing launched here ' +
  'would be a run on any server.';

/** Why the connection step is blocked while `/healthz` is silent. */
export const OFFLINE_HEALTH_REASON =
  'The /healthz probe is not answering, so the dashboard is showing demo data. Start the ' +
  'API (docs/QUICKSTART.md §3), then check the base URL and key in Settings.';

/** Why every step is blocked once the key has been rejected. */
export const AUTH_REASON =
  'API key rejected (401) — paste the key this service is running with into Settings.';

const REPORT_TITLE = 'Ring smoke (guided first run)';

const PRESET_POLL_MS = 3000;
const CRITERIA_POLL_MS = 3000;
const RUN_POLL_MS = 1500;
const REPORT_POLL_MS = 1500;
const RUNS_POLL_MS = 5000;

/** Every step is in exactly one of these; `done` is only ever set by an
 * answer from the API. */
type StepState = 'done' | 'current' | 'blocked';

interface Step {
  key: string;
  title: string;
  /** True only because the API said so. */
  done: boolean;
  /** Why this step cannot be taken, when it cannot. */
  why: string | null;
  what: ReactNode;
  action?: ReactNode;
  extra?: ReactNode;
}

const chipClass = (s: StepState): string =>
  s === 'done' ? 'done' : s === 'current' ? 'running' : 'blocked';

/** `done` wins over a blocking reason: a step the server has already
 * confirmed does not become un-done when the API goes away. */
function stateOf(step: Step): StepState {
  if (step.done) return 'done';
  return step.why === null ? 'current' : 'blocked';
}

export interface GuidedFirstRunProps {
  /** Render only once this server has itself reported that it holds no runs
   * (the Scenarios mount): an unreachable API is not an empty one. Once the
   * panel has stored a scenario or launched a run it stays on screen — its
   * own run is the first row in that list. */
  onlyWhenEmpty?: boolean;
  /** Wrap in a `view` with a title — the rail's "First run" route. */
  standalone?: boolean;
}

export function GuidedFirstRun({
  onlyWhenEmpty = false,
  standalone = false,
}: GuidedFirstRunProps): JSX.Element | null {
  const authFailed = useAuthFailed();
  const offline = useOfflineFallback();
  const mockEnv = isMockEnv();
  /** Reads are being answered by the in-browser backend, writes are refused. */
  const demo = mockEnv || offline;

  const [preset, setPreset] = useState<PresetSummary | null>(null);
  /** True once `GET /scenarios/preset` answered from a real server, whatever
   * it contained — so "this service serves no ring preset" is said only when
   * the service really said so. */
  const [presetsAnswered, setPresetsAnswered] = useState(false);
  const [scenarioId, setScenarioId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<RunDetail | null>(null);
  /** The run's metrics, once `GET /runs/{id}/metrics` has answered from a
   * real server. A click on a link is not a read: the step is done when the
   * service has handed over the numbers, not when the browser navigated. */
  const [metrics, setMetrics] = useState<RunMetrics | null>(null);
  const [report, setReport] = useState<ReportOut | null>(null);
  const [downloaded, setDownloaded] = useState(false);
  /** The criteria profile the report will be scored against, as the service
   * named it; null means the request carries no `profile` field (a service
   * older than the parameter forbids the extra key). */
  const [profileName, setProfileName] = useState<string | null>(null);
  const [criteriaAnswered, setCriteriaAnswered] = useState(false);
  /** How many runs this server holds; null until it has said so itself (demo
   * answers are discarded, so "no runs yet" is never inferred from the
   * in-browser backend). */
  const [serverRuns, setServerRuns] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  /** The service's own words for the last refusal, kept beside the step that
   * asked — a toast that has already faded is not the message to act on. */
  const [error, setError] = useState<{ step: string; msg: string } | null>(null);

  /** The walkthrough has been started here, so the panel stays mounted. */
  const engaged = scenarioId !== null || runId !== null;

  const fail = (step: string, err: unknown): void => {
    setError({ step, msg: err instanceof Error ? err.message : String(err) });
    toastError(err, step);
  };

  /* ----------------------------- polls ------------------------------ */

  const loadPreset = useCallback(async () => {
    // capture the source before the call: a preset list from the demo backend
    // is not this service's library
    const fromDemo = isMockActive();
    try {
      const presets = await listPresetScenarios();
      if (fromDemo) return;
      setPreset(
        presets.find((p) => p.filename === RING_PRESET_FILENAME) ??
          presets.find((p) => p.name === RING_PRESET_NAME) ??
          null,
      );
      setPresetsAnswered(true);
    } catch {
      /* transient; the connection is reported by the shell's status dot */
    }
  }, []);
  usePoll(loadPreset, authFailed || demo || preset !== null ? null : PRESET_POLL_MS);

  // the acceptance-criteria profile the report will be scored against, read
  // once: the registry is fixed for a service, and the default is the profile
  // `POST /reports` applies to a request that names none
  const loadCriteria = useCallback(async () => {
    if (isMockActive()) return;
    try {
      const list = await listCriteriaProfiles();
      if (list.length === 0) return;
      const pick =
        list.find((p) => p.default) ??
        list.find((p) => p.name === DEFAULT_CRITERIA_PROFILE) ??
        list[0];
      setProfileName(pick.name);
      setCriteriaAnswered(true);
    } catch (err) {
      // 404 = a service older than GET /criteria. ReportCreateRequest forbids
      // unknown fields there, so the request names no profile at all rather
      // than being refused over a value that service applies anyway.
      if (err instanceof ApiError && err.status === 404) setCriteriaAnswered(true);
    }
  }, []);
  usePoll(loadCriteria, authFailed || demo || criteriaAnswered ? null : CRITERIA_POLL_MS);

  const runTerminal = run?.status === 'done' || run?.status === 'failed';
  const pollRun = useCallback(async () => {
    if (runId === null) return;
    const fromDemo = isMockActive();
    try {
      const r = await getRun(runId);
      if (!fromDemo) setRun(r);
    } catch {
      /* transient */
    }
  }, [runId]);
  usePoll(
    pollRun,
    runId !== null && !runTerminal && !authFailed && !demo ? RUN_POLL_MS : null,
  );

  const reportPending = report?.status === 'queued' || report?.status === 'running';
  // keyed on the id, not the row: a callback that changed with every answer
  // would re-arm `usePoll` on each tick and hammer the endpoint
  const reportId = report?.report_id ?? null;
  const pollReport = useCallback(async () => {
    if (reportId === null) return;
    const fromDemo = isMockActive();
    try {
      const out = await getReport(reportId);
      if (!fromDemo) setReport(out);
    } catch {
      /* transient */
    }
  }, [reportId]);
  usePoll(pollReport, reportPending && !authFailed && !demo ? REPORT_POLL_MS : null);

  // only for the Scenarios mount: a server that already holds runs does not
  // need the walkthrough taking the top of the library
  const checkRuns = useCallback(async () => {
    const fromDemo = isMockActive();
    try {
      const all = await listRuns();
      if (!fromDemo) setServerRuns(all.length);
    } catch {
      /* transient */
    }
  }, []);
  usePoll(
    checkRuns,
    onlyWhenEmpty && !engaged && !authFailed && !demo ? RUNS_POLL_MS : null,
  );

  /* ---------------------------- actions ----------------------------- */

  const storePreset = async (): Promise<void> => {
    if (preset === null) return;
    setBusy('preset');
    setError(null);
    try {
      // a preset is a repo YAML, not a stored scenario: the same path the
      // Scenarios launcher takes, so the same config hash is reused
      const { scenario_id, stored } = await ensureStored(preset);
      setScenarioId(scenario_id);
      toast(
        'ok',
        stored
          ? `preset ${preset.name} stored as ${scenario_id}`
          : `preset ${preset.name} already stored as ${scenario_id}`,
      );
    } catch (err) {
      fail('preset', err);
    } finally {
      setBusy(null);
    }
  };

  const launch = async (): Promise<void> => {
    if (scenarioId === null) return;
    setBusy('run');
    setError(null);
    try {
      // the preset's own duration and seed: only `replicates` is overridden,
      // so the run keeps the preset's config apart from the seed count
      const res = await createRun({ scenario_id: scenarioId, replicates: SMOKE_REPLICATES });
      setRunId(res.run_id);
      setRun(null);
      toast('ok', `run ${res.run_id} queued`);
    } catch (err) {
      fail('run', err);
    } finally {
      setBusy(null);
    }
  };

  /** Pull the metrics the run detail is about to render. Fired by the same
   * click that opens that view, so the step ticks on the service's answer
   * rather than on the navigation. */
  const readMetrics = async (): Promise<void> => {
    if (runId === null) return;
    // capture the source before the call: metrics from the in-browser backend
    // are not this run's
    const fromDemo = isMockActive();
    setError(null);
    try {
      const out = await getRunMetrics(runId);
      if (!fromDemo) setMetrics(out);
    } catch (err) {
      fail('read', err);
    }
  };

  const generate = async (): Promise<void> => {
    if (runId === null) return;
    setBusy('report');
    setError(null);
    try {
      const out = await createReport(
        [runId],
        REPORT_TITLE,
        profileName === null ? undefined : profileName,
      );
      setReport(out);
      setDownloaded(false);
    } catch (err) {
      fail('report', err);
    } finally {
      setBusy(null);
    }
  };

  const download = async (): Promise<void> => {
    if (report === null) return;
    setBusy('download');
    setError(null);
    try {
      const md = await getReportMarkdown(report.report_id);
      saveText(md, `${report.report_id}.md`);
      setDownloaded(true);
    } catch (err) {
      fail('report', err);
    } finally {
      setBusy(null);
    }
  };

  /* ----------------------------- steps ------------------------------ */

  /** Why a step that talks to the server cannot run, or null. */
  const serverBlock = authFailed
    ? AUTH_REASON
    : mockEnv
      ? MOCK_ENV_REASON
      : offline
        ? OFFLINE_WRITE_MESSAGE
        : null;

  /** A launch that leaves no measurement window dies on the worker; the same
   * guard the other launchers use refuses it here first. */
  const warmupBlock = warmupProblem(
    preset?.config.sim.duration_s ?? null,
    preset?.config.sim.warmup_s ?? null,
  );

  const baseUrl = getSettings().baseUrl;
  const profileLabel =
    profileName ?? 'the profile this service applies when a request names none';

  const steps: Step[] = [];

  {
    /* (the steps are pushed in QUICKSTART order; the block only scopes the
       per-step reasons) */
    // (a) connection
    const apiWhy = authFailed
      ? AUTH_REASON
      : mockEnv
        ? MOCK_ENV_REASON
        : offline
          ? OFFLINE_HEALTH_REASON
          : null;
    steps.push({
      key: 'api',
      title: 'Connect to the API',
      done: !demo && !authFailed,
      why: apiWhy,
      what: (
        <>
          The same <span className="mono">/healthz</span> probe as the rail&apos;s status dot,
          and the key from Settings. Base <span className="mono">{baseUrl}</span>.
        </>
      ),
    });

    // (b) the preset
    const presetWhy =
      serverBlock ??
      (preset !== null
        ? null
        : presetsAnswered
          ? `This service serves no ${RING_PRESET_NAME} preset (GET /scenarios/preset).`
          : 'Reading the repo presets from GET /scenarios/preset…');
    steps.push({
      key: 'preset',
      title: `Store the ${RING_PRESET_NAME} preset as a scenario`,
      done: scenarioId !== null,
      why: presetWhy,
      what: (
        <>
          230 m ring, 22 vehicles — the canonical emergence benchmark, and the one preset that
          needs no data file on the machine. A preset is a repo YAML with no{' '}
          <span className="mono">scenario_id</span>: this stores it (or reuses the stored copy
          with the same config hash).
        </>
      ),
      action:
        scenarioId === null ? (
          <button
            className="btn sm primary"
            disabled={busy !== null || presetWhy !== null}
            onClick={() => void storePreset()}
          >
            Use {RING_PRESET_NAME}
          </button>
        ) : null,
      extra:
        scenarioId !== null ? (
          <div className="small muted">
            scenario <span className="mono">{scenarioId}</span>
            {preset && (
              <>
                {' '}
                · hash <span className="mono">{preset.config_hash}</span>
              </>
            )}
          </div>
        ) : null,
    });

    // (c) the launch
    const launchWhy =
      serverBlock ?? (scenarioId === null ? 'Store the preset first.' : warmupBlock);
    steps.push({
      key: 'run',
      title: `Launch ${SMOKE_REPLICATES} replicates`,
      done: runId !== null,
      why: launchWhy,
      what: <>{SMOKE_NOTE}</>,
      action:
        runId === null ? (
          <button
            className="btn sm primary"
            disabled={busy !== null || launchWhy !== null}
            onClick={() => void launch()}
          >
            Launch {SMOKE_REPLICATES}-replicate smoke run
          </button>
        ) : null,
      extra:
        runId !== null ? (
          <div className="small muted">
            run <span className="mono">{runId}</span>
          </div>
        ) : null,
    });

    // (d) watching it finish
    const failed = run?.status === 'failed';
    const watchWhy = failed
      ? failureReason(run?.error, run?.error_kind)
      : runId === null
        ? 'Launch the run first.'
        : serverBlock;
    steps.push({
      key: 'watch',
      title: 'Wait for the run to finish',
      done: run?.status === 'done',
      why: watchWhy,
      what: (
        <>
          <span className="mono">POST /runs</span> is asynchronous: the job is polled until it
          is <span className="mono">done</span> or <span className="mono">failed</span>. The
          ring runs far faster than real time, so this is seconds.
        </>
      ),
      extra:
        run !== null ? (
          <div className="row" style={{ gap: 12 }}>
            <StatusChip status={run.status} />
            <ProgressBar
              done={run.progress.completed_replicates}
              total={run.progress.total_replicates}
              status={run.status}
            />
          </div>
        ) : null,
    });

    // (e) metrics + heatmap
    const readWhy =
      run?.status === 'done'
        ? null
        : failed
          ? 'The run failed, so it produced no metrics.'
          : 'The run has to finish first.';
    steps.push({
      key: 'read',
      title: 'Read the metrics and the space-time heatmap',
      done: metrics !== null,
      why: readWhy,
      what: (
        <>
          Aggregate metrics with 95% CIs, the per-replicate strips and the speed/density
          contour. At n={SMOKE_REPLICATES} every card is badged UNDERPOWERED — that is the API
          saying so, not a rendering fault.
        </>
      ),
      action:
        run?.status === 'done' && runId !== null ? (
          <Link className="btn sm" to={`/runs/${runId}`} onClick={() => void readMetrics()}>
            Open run detail
          </Link>
        ) : null,
      extra:
        metrics !== null ? (
          <div className="small muted">
            metrics read · {metrics.replicates.length} replicate
            {metrics.replicates.length === 1 ? '' : 's'}
            {metrics.underpowered ? ' · underpowered' : ''}
          </div>
        ) : null,
    });

    // (f) the report
    const reportWhy =
      serverBlock ?? (run?.status === 'done' ? null : 'The report needs a finished run.');
    const reportFailed = report?.status === 'failed';
    const reportDone = report?.status === 'done';
    steps.push({
      key: 'report',
      // the bundle exists once the server says `done`; the step is finished
      // when its markdown has actually been pulled down
      done: reportDone && downloaded,
      title: 'Generate the report and download the markdown',
      why: reportFailed
        ? failureReason(report?.error, report?.error_kind)
        : reportDone
          ? null
          : reportWhy,
      what: (
        <>
          Provenance, the acceptance-criteria table scored against{' '}
          <span className="mono">{profileLabel}</span>, metric tables with CIs and a limitations
          section. Criteria whose evidence is observed field data this run set does not hold come
          back <span className="mono">not evaluated</span>, and an unevaluated criterion counts as
          failing.
        </>
      ),
      action: (
        <div className="row" style={{ gap: 8 }}>
          {report === null || reportFailed ? (
            <button
              className="btn sm primary"
              disabled={busy !== null || reportWhy !== null}
              onClick={() => void generate()}
            >
              Generate report
            </button>
          ) : null}
          {report !== null && !reportFailed ? (
            <button
              className="btn sm primary"
              disabled={busy !== null || !reportDone}
              title={reportDone ? undefined : `report ${report.status} — the markdown route answers 409 until it is done`}
              onClick={() => void download()}
            >
              Download report.md
            </button>
          ) : null}
        </div>
      ),
      extra:
        report !== null ? (
          <div className="small muted">
            report <span className="mono">{report.report_id}</span> ·{' '}
            <span className="mono">{report.status}</span>
            {downloaded && ' · downloaded'}
          </div>
        ) : null,
    });
  }

  const doneCount = steps.filter((s) => s.done).length;

  // every hook has run: the Scenarios mount appears only once *this server*
  // has answered that it holds no runs at all, and stands down again while
  // the API is unreachable. An unreachable API is not an empty one, and that
  // view already carries the offline banner and its own demo labels — a
  // second copy of the same refusal there is noise. The rail's "First run"
  // route is where the blocked walkthrough lives.
  if (onlyWhenEmpty && !engaged && (serverRuns !== 0 || demo)) return null;

  const panel = (
    <div className="panel guided">
      <div className="panel-head">
        <span className="panel-title">Guided first run</span>
        <span className="spacer" />
        <span className="small muted mono">
          {doneCount}/{steps.length} done
        </span>
      </div>
      <div className="panel-body">
        <p className="small muted" style={{ margin: '0 0 16px' }}>
          The ten-minute path of <span className="mono">docs/QUICKSTART.md</span>, in a few
          clicks: the ring benchmark, a {SMOKE_REPLICATES}-replicate smoke run, its metrics and a
          report bundle. Every step below is ticked by an answer from the API — never by demo
          data.
        </p>
        <ol className="guided-steps">
          {steps.map((s, i) => {
            const state = stateOf(s);
            return (
            <li key={s.key} className={`guided-step ${state}`}>
              <span className="g-num mono">{i + 1}</span>
              <div className="g-main">
                <div className="g-head">
                  <span className="g-title">{s.title}</span>
                  <span className={`chip ${chipClass(state)}`} aria-label={`${s.title}: ${state}`}>
                    <span className="dot" />
                    {state}
                  </span>
                </div>
                <div className="small muted g-what">{s.what}</div>
                {!s.done && s.why !== null && <div className="g-why">{s.why}</div>}
                {s.extra}
                {s.action}
                {error !== null && error.step === s.key && (
                  <p className="fail-reason">{error.msg}</p>
                )}
              </div>
            </li>
            );
          })}
        </ol>
      </div>
    </div>
  );

  if (!standalone) return panel;
  return (
    <div className="view">
      <div className="view-title">
        First run <span className="count mono">docs/QUICKSTART.md</span>
      </div>
      {panel}
    </div>
  );
}
