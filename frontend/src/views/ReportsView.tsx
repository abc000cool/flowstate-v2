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
 * The acceptance-criteria profile the report is scored against is chosen next
 * to the button, from `GET /criteria` (the FlowState default preselected), and
 * is shown on every row: a pass/fail table is meaningless without the
 * thresholds it was scored against, and those differ between the FHWA table
 * and the state-DOT protocols. Only the *name* is sent and shown — the
 * dashboard never restates a profile's numbers, and a row whose profile the
 * service did not report says so rather than assuming the default. Beside it,
 * a report scored against an observations artifact shows what that comparison
 * actually rested on — link-hours and speed cells compared, and how many
 * stations were excluded for lying outside the simulated span — because
 * "GEH passed" over two link-hours is a different claim from the same line
 * over two hundred.
 *
 * Beside the profile, the launcher chooses the *evidence*: an observations
 * artifact from one of the corridors this server has onboarded
 * (`GET /corridors`), or a server-side path typed by hand. Without one the
 * link-flow and speed criteria are reported as "not evaluated" — the honest
 * default, and what the option says — so the choice is offered rather than
 * assumed. A refusal of the request (a 422 naming a path outside the allowed
 * roots, a 404) stays on screen beside the button in the server's own words.
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
 * queued when the API went away must not come back done from demo data.
 *
 * Layout per docs/design/DASHBOARD_DESIGN.md §10.6: a "New report" panel in
 * two numbered steps (pick finished micro runs; choose the criteria profile
 * and the observations, the profile's source shown under its select), an
 * action bar with the selection summary, the button and a persistent refusal
 * callout, then the generated reports. Both tables render skeleton rows until
 * their first answer, an error callout if that first read fails, an empty
 * state, then rows.
 *
 * `?select=<run_id>` (Run detail's "Report on this run", §10.4 P2) preselects
 * that run once the runs list has it, and a callout under step 1 says so — or
 * says why not: a macro run, a failed one, or one this server does not list.
 * A run still computing is waited for. The parameter is dropped from the URL
 * once it has been dealt with. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MouseEvent as ReactMouseEvent } from 'react';
import { useInRouterContext, useSearchParams } from 'react-router-dom';
import {
  ApiError,
  createReport,
  DEFAULT_CRITERIA_PROFILE,
  getReport,
  getReportArchive,
  getReportMarkdown,
  getReportPdf,
  isMockActive,
  listCorridors,
  listCriteriaProfiles,
  listReports,
  listRuns,
  listScenarios,
  OFFLINE_WRITE_MESSAGE,
} from '../api/client';
import type { CorridorRow, ReportOut, ReportRecord, RunSummary } from '../api/types';
import { SeededBadge, StatusChip, TierBadge } from '../components/bits';
import { Icon } from '../components/icons';
import { PageHeader } from '../components/PageHeader';
import { toast, toastError } from '../components/toast';
import { Callout } from '../components/ui/Callout';
import { HashValue } from '../components/ui/CopyButton';
import { EmptyState } from '../components/ui/EmptyState';
import { SkeletonRows } from '../components/ui/Skeleton';
import { DEMO_HASH_LABEL, DEMO_ROW_TITLE } from '../lib/demo';
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
/** Retry interval for `GET /criteria` until it answers. The profile registry
 * is fixed for a service, so this poll stops on the first answer (including
 * the 404 of a service that has no such endpoint). */
const CRITERIA_POLL_MS = 3000;
/** How often the onboarded corridors are re-read for the observations
 * selector (`GET /corridors`), and the backoff once the service has answered
 * 404 to it. */
const CORRIDOR_POLL_MS = 5000;
const CORRIDOR_RETRY_MS = 60_000;

/** The observations select's two non-corridor options: score against nothing
 * (the API's own default — the GEH and speed rows then read "not evaluated"),
 * and a path typed by hand for an artifact this dashboard cannot list. */
/** A profile source longer than this shows three lines and a toggle. */
const SOURCE_CLAMP_CHARS = 240;
const OBSERVATIONS_NONE = '';
const OBSERVATIONS_CUSTOM = '__server_path__';

/** What the selector needs from a `CriteriaProfile`: the name the request
 * carries, and the provenance text the option shows so a profile is picked
 * from its source rather than its name. The thresholds are deliberately not
 * rendered — the report prints them from the server's own registry, and a
 * dashboard restating them is a second, drifting copy of published numbers. */
interface ProfileOption {
  name: string;
  source: string;
  default: boolean;
}

/** Shown while `GET /criteria` has not answered yet. The name is the profile
 * the API applies to a request that names none, so it is the honest thing to
 * display before the registry is known; the select stays disabled until the
 * real list arrives. */
const LOADING_PROFILE_OPTIONS: ProfileOption[] = [
  {
    name: DEFAULT_CRITERIA_PROFILE,
    source: 'Reading the selectable profiles from GET /criteria…',
    default: true,
  },
];

/** Shown once the service has answered 404 to `GET /criteria` — an API older
 * than the profile registry. Its thresholds are unknown here, so the option
 * claims none: it names only the profile such a service applies anyway, and
 * `generate` then sends no `profile` field at all. */
const FALLBACK_PROFILE_OPTIONS: ProfileOption[] = [
  {
    name: DEFAULT_CRITERIA_PROFILE,
    source:
      'This service answered 404 to GET /criteria, so its profile registry is unknown to ' +
      'this dashboard. Reports are scored against the profile the service applies when a ' +
      'request names none, and the request names none.',
    default: true,
  },
];

/** Title for a row whose report carries no profile: a service older than the
 * parameter reports one for no report at all, and naming the default here
 * would be this dashboard's assumption presented as the server's answer. */
const UNKNOWN_PROFILE_TITLE =
  'This service did not report a criteria profile for this report (an API older than the ' +
  'profile parameter), so the thresholds it was scored against are not known here — read ' +
  'the report bundle itself.';

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
    // absent on records written before the dashboard sent a profile: left
    // undefined, so the row says "unknown" instead of naming a default the
    // request never carried
    profile: typeof r.profile === 'string' ? r.profile : undefined,
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

function recordFromOut(
  out: ReportOut,
  requestedRunIds: string[] = [],
  demo = false,
  requestedProfile?: string,
): ReportRecord {
  // `out.report_path` is deliberately dropped: it is results-root-relative,
  // meaningless to this browser, and must never be rendered as a path or link.
  return {
    report_id: out.report_id,
    run_ids: out.run_ids.length > 0 ? out.run_ids : requestedRunIds,
    title: out.title,
    // the server's own answer first; `requestedProfile` is only what this
    // browser asked for moments ago, and is left undefined when the request
    // named no profile
    profile: out.profile ?? requestedProfile,
    status: out.status,
    error: out.error ?? null,
    error_kind: out.error_kind ?? null,
    created_at: out.created_at,
    observed: out.observed ?? null,
    demo,
  };
}

/** The observed comparison this report rests on, in one line, or null when
 * the server reported none (no observations artifact, or a report that has
 * not finished). The numbers are the server's own counts — what was actually
 * compared, and what was left out — never a restatement or a total this
 * browser computed (CLAUDE.md §7.4). */
function observedLine(observed: ReportRecord['observed']): string | null {
  if (!observed) return null;
  const parts: string[] = [];
  if (typeof observed.n_link_hours === 'number') parts.push(`${observed.n_link_hours} link-hours`);
  if (typeof observed.n_speed_cells === 'number') {
    parts.push(`${observed.n_speed_cells} speed cells`);
  }
  if (parts.length === 0) return null;
  const excluded = observed.n_stations_outside_span ?? 0;
  const tail =
    excluded > 0
      ? `, ${excluded} station${excluded === 1 ? '' : 's'} excluded (outside the simulated span)`
      : '';
  return `observed: ${parts.join(', ')} compared${tail}`;
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

/** Columns of the two tables (skeleton and empty rows span them). */
const PICKER_COLUMNS = 7;
const REPORT_COLUMNS = 7;

/** The message of a failed read, for a first-load error callout. */
function errorText(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  return err instanceof ApiError && err.status ? `HTTP ${err.status} — ${text}` : text;
}

const MACRO_TOOLTIP =
  'Screening tier cannot be validated — macro (CTM) results are labeled tier:"screening" and the API refuses to generate a validation report from them.';

/** What became of a `?select=<run_id>` (Run detail's "Report on this run"):
 * the run was selected, or why it was not. `waiting` keeps the request open
 * until the run finishes. */
type PreselectOutcome = 'selected' | 'macro' | 'waiting' | 'failed' | 'missing';

interface PreselectNote {
  runId: string;
  outcome: PreselectOutcome;
  /** The run's status, for `waiting`. */
  status?: string;
}

/** Reports. Inside the app's router the page honours `?select=<run_id>`;
 * rendered on its own (as most of its tests do) there is no URL to read, and
 * nothing is preselected. */
export function ReportsView(): JSX.Element {
  return useInRouterContext() ? <RoutedReportsView /> : <ReportsPage preselect={null} />;
}

/** Reads `?select=` and drops it from the URL once it has been dealt with,
 * so a reload or a later visit does not select the run again. */
function RoutedReportsView(): JSX.Element {
  const [params, setParams] = useSearchParams();
  const preselect = params.get('select');
  const consume = useCallback(() => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('select');
        return next;
      },
      { replace: true },
    );
  }, [setParams]);
  return <ReportsPage preselect={preselect} onPreselectSettled={consume} />;
}

function ReportsPage({
  preselect,
  onPreselectSettled,
}: {
  /** A run id to select once the runs list has it (`?select=`), or null. */
  preselect: string | null;
  /** Called once `preselect` is selected or cannot be (not while waiting for
   * the run to finish). */
  onPreselectSettled?: () => void;
}): JSX.Element {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  /** Every run of the last read, finished or not: what a `?select=` that is
   * not (yet) in the picker is checked against. */
  const [allRuns, setAllRuns] = useState<RunSummary[]>([]);
  const [preselectNote, setPreselectNote] = useState<PreselectNote | null>(null);
  /** True when the picker's rows came from the in-browser demo backend: they
   * carry the DEMO tag and no config hash, like the Runs table (lib/demo). */
  const [runsDemo, setRunsDemo] = useState(false);
  /** False until `GET /runs` has answered once: the picker shows skeleton
   * rows until then, not "no finished runs". */
  const [runsLoaded, setRunsLoaded] = useState(false);
  /** Why the first read of the runs list failed, while it has never answered. */
  const [runsError, setRunsError] = useState<string | null>(null);
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
  /** Why the first read of `GET /reports` failed (not a 404), while it has
   * never answered. */
  const [listError, setListError] = useState<string | null>(null);
  /** The selectable acceptance-criteria profiles; null until `GET /criteria`
   * has answered. */
  const [profileOptions, setProfileOptions] = useState<ProfileOption[] | null>(null);
  /** False once the service answered 404 to `GET /criteria` (an API older than
   * the profile registry): the selector then offers only the profile such a
   * service applies, and the request carries no `profile` field. */
  const [criteriaListed, setCriteriaListed] = useState(true);
  const [profile, setProfile] = useState(DEFAULT_CRITERIA_PROFILE);
  /** The corridors this server has onboarded, for the observations selector;
   * null until `GET /corridors` has answered (404 included). */
  const [corridors, setCorridors] = useState<CorridorRow[] | null>(null);
  /** False once the service answered 404 to `GET /corridors`: the selector
   * then offers only "none" and a typed server path. */
  const [corridorsListed, setCorridorsListed] = useState(true);
  /** Which observations artifact the next report is scored against: a listed
   * corridor's path, `OBSERVATIONS_CUSTOM` (the typed one), or none. */
  const [observations, setObservations] = useState(OBSERVATIONS_NONE);
  const [customObservations, setCustomObservations] = useState('');
  /** The server's last refusal of `POST /reports`, shown verbatim beside the
   * button — a 422 naming an unreadable observations path is the message the
   * user has to act on, and a toast that has already faded is not it. */
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  /** Where keyboard focus goes once a generate started from its button ends:
   * the list on success, back to the button on a refusal. */
  const refocus = useRef<'list' | 'button' | null>(null);
  const listTitleRef = useRef<HTMLHeadingElement>(null);
  const generateRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (busy || refocus.current === null) return;
    const target = refocus.current === 'list' ? listTitleRef.current : generateRef.current;
    refocus.current = null;
    if (document.activeElement === document.body) target?.focus();
  }, [busy]);
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
    // captured at fetch time, as the Runs view does: the client decides demo
    // vs live per call
    const fromDemo = isMockActive();
    try {
      const all = await listRuns();
      setAllRuns(all);
      setRuns(all.filter((r) => r.status === 'done'));
      setRunsDemo(fromDemo);
      setRunsLoaded(true);
      setRunsError(null);
    } catch (err) {
      // connectivity is surfaced by the status line / banner; only a first
      // load with nothing to show says why, instead of a skeleton forever
      setRunsError(errorText(err));
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
      setListError(null);
    } catch (err) {
      // 404 = this service predates GET /reports; anything else is transient
      if (err instanceof ApiError && err.status === 404) {
        setServerListed(false);
        setListError(null);
      } else {
        setListError(errorText(err));
      }
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
  // the list read while the API was offline is the demo one: re-read it the
  // moment the link is back, or real runs print raw scenario ids for good
  useEffect(() => {
    if (!offline && !authFailed) void loadScenarios();
  }, [offline, authFailed, loadScenarios]);

  // the selectable acceptance-criteria profiles, read once: the registry is
  // fixed for a service, and the profile the API applies by default is the one
  // preselected here, so the button's behaviour is unchanged until the user
  // picks another protocol
  const loadCriteria = useCallback(async () => {
    try {
      const list = await listCriteriaProfiles();
      // an empty registry is not an answer to select from: keep polling rather
      // than render an empty control
      if (list.length === 0) return;
      const opts = list.map((p) => ({ name: p.name, source: p.source, default: p.default }));
      setProfileOptions(opts);
      setCriteriaListed(true);
      // the server's own default, not this file's copy of the name; the poll
      // stops on this answer, so a user's later pick is never overwritten
      const preferred =
        opts.find((p) => p.default) ??
        opts.find((p) => p.name === DEFAULT_CRITERIA_PROFILE) ??
        opts[0];
      setProfile(preferred.name);
    } catch (err) {
      // 404 = this service predates GET /criteria; anything else is transient
      if (err instanceof ApiError && err.status === 404) {
        setProfileOptions(FALLBACK_PROFILE_OPTIONS);
        setCriteriaListed(false);
        setProfile(DEFAULT_CRITERIA_PROFILE);
      }
    }
  }, []);
  usePoll(loadCriteria, authFailed || profileOptions !== null ? null : CRITERIA_POLL_MS);

  // the onboarded corridors, for the observations selector: a report scored
  // against a corridor's own detectors is the only one whose GEH and speed
  // rows are evaluated at all, and the artifact path lives on the server
  const loadCorridors = useCallback(async () => {
    try {
      setCorridors(await listCorridors());
      setCorridorsListed(true);
    } catch (err) {
      // 404 = a service older than GET /corridors; anything else is transient
      if (err instanceof ApiError && err.status === 404) {
        setCorridors([]);
        setCorridorsListed(false);
      }
    }
  }, []);
  usePoll(
    loadCorridors,
    authFailed ? null : corridorsListed ? CORRIDOR_POLL_MS : CORRIDOR_RETRY_MS,
  );

  /** Corridors whose onboarding finished and left an observations artifact —
   * the only ones a report can be scored against. */
  const observationChoices = useMemo(
    () => (corridors ?? []).filter((c) => c.status === 'done' && c.observations_path),
    [corridors],
  );
  const profileChoices = profileOptions ?? LOADING_PROFILE_OPTIONS;
  const profileSources = useMemo(
    () => new Map(profileChoices.map((p) => [p.name, p.source])),
    [profileChoices],
  );
  /** Provenance for a row's profile: the source this service served for that
   * name (nothing, for a profile it no longer offers — the name is still the
   * server's answer), or the explanation for a row that carries none. */
  const profileTitle = (name: string | undefined): string | undefined =>
    name === undefined ? UNKNOWN_PROFILE_TITLE : profileSources.get(name);
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
            // a service that reports no profile leaves the record's own value
            // alone: it is what this browser requested, not an assumption
            profile: out.profile ?? rec.profile,
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

  // `?select=<run_id>`: once the runs list has answered, select the run if it
  // can be reported on, and say what happened either way — a macro run, a
  // failed one or one this server does not list is explained, never dropped
  // silently. A run still computing is waited for, and selected when done.
  const settledRef = useRef(onPreselectSettled);
  settledRef.current = onPreselectSettled;
  useEffect(() => {
    if (preselect === null || !runsLoaded) return;
    const run = allRuns.find((r) => r.run_id === preselect);
    let outcome: PreselectOutcome;
    if (!run) outcome = 'missing';
    else if (run.tier === 'macro') outcome = 'macro';
    else if (run.status === 'done') outcome = 'selected';
    else if (run.status === 'failed') outcome = 'failed';
    else outcome = 'waiting';
    setPreselectNote((prev) =>
      prev && prev.runId === preselect && prev.outcome === outcome && prev.status === run?.status
        ? prev
        : { runId: preselect, outcome, status: run?.status },
    );
    if (outcome === 'waiting') return;
    if (outcome === 'selected') setSelected((s) => (s.has(preselect) ? s : new Set(s).add(preselect)));
    settledRef.current?.();
  }, [preselect, runsLoaded, allRuns]);

  // bring a preselected row into view: the picker scrolls, and the run may
  // sit below its fold
  useEffect(() => {
    if (preselectNote?.outcome !== 'selected') return;
    const id = preselectNote.runId;
    const row = Array.from(document.querySelectorAll<HTMLElement>('tr[data-run-id]')).find(
      (tr) => tr.dataset.runId === id,
    );
    if (row && typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'nearest' });
  }, [preselectNote]);

  const dismissPreselect = (): void => {
    // dismissing a run still being waited for also stops the wait
    if (preselectNote?.outcome === 'waiting') settledRef.current?.();
    setPreselectNote(null);
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
    // a service with no GET /criteria predates the `profile` parameter, and
    // ReportCreateRequest forbids unknown fields — sending the name would have
    // the whole request refused (422) over a field whose value that service
    // applies anyway, so it is omitted rather than sent
    const requested = criteriaListed ? profile : undefined;
    // the evidence the report is scored against, when one was chosen: a
    // server-side path, never a file this browser holds
    const chosen =
      observations === OBSERVATIONS_CUSTOM ? customObservations.trim() : observations;
    const observationsPath = chosen === '' ? undefined : chosen;
    // the button disables while busy (and stays disabled once the selection
    // clears), which drops keyboard focus to <body>: hand it to the list the
    // new report lands in instead
    const fromButton = document.activeElement === generateRef.current;
    setBusy(true);
    setLaunchError(null);
    try {
      const out = await createReport(ids, undefined, requested, observationsPath);
      const rec = recordFromOut(out, ids, demo, requested);
      const next = [rec, ...reportsRef.current.filter((r) => r.report_id !== rec.report_id)];
      if (demo) show(next);
      else commit(next);
      setSelected(new Set());
      // "run-x is selected" no longer describes the picker
      setPreselectNote((n) => (n?.outcome === 'selected' ? null : n));
      if (fromButton) refocus.current = 'list';
      if (rec.status === 'done') toast('ok', `report ${rec.report_id} generated`);
      else if (rec.status === 'failed')
        toast('error', `report ${rec.report_id} failed: ${rec.error ?? 'unknown error'}`);
      else toast('info', `report ${rec.report_id} queued — download unlocks once it is done`);
    } catch (err) {
      // the server's own words, kept on screen: a 422 ("observations_path is
      // outside the allowed data roots") or a 404 is a correction to make,
      // not a notification to miss
      const status = err instanceof ApiError ? err.status : 0;
      const text = err instanceof Error ? err.message : String(err);
      setLaunchError(status ? `HTTP ${status} — ${text}` : text);
      toastError(err, 'report');
      if (fromButton) refocus.current = 'button';
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

  // a click on a selectable row toggles it, except on its own controls (the
  // checkbox toggles by itself; the copy button must not select)
  const clickRow = (e: ReactMouseEvent<HTMLTableRowElement>, run: RunSummary): void => {
    if (run.tier === 'macro') return;
    if ((e.target as Element).closest('a, button, input, select, label')) return;
    toggleRun(run.run_id);
  };

  const chosenCorridor = observationChoices.find((c) => c.observations_path === observations);
  const observationsLabel =
    observations === OBSERVATIONS_NONE
      ? null
      : observations === OBSERVATIONS_CUSTOM
        ? 'a server path'
        : (chosenCorridor?.name ?? 'the chosen observations');
  const profileSource =
    profileOptions === null
      ? LOADING_PROFILE_OPTIONS[0].source
      : criteriaListed
        ? profileSources.get(profile)
        : FALLBACK_PROFILE_OPTIONS[0].source;
  const longSource = (profileSource?.length ?? 0) > SOURCE_CLAMP_CHARS;

  let pickerBody: JSX.Element;
  if (!runsLoaded) {
    if (authFailed) {
      pickerBody = (
        <tr>
          <td colSpan={PICKER_COLUMNS} className="reports-wrap-cell">
            <EmptyState
              compact
              title="Runs are not loading."
              description="The API key was rejected. Save a new key in Settings to resume."
            />
          </td>
        </tr>
      );
    } else if (runsError !== null) {
      pickerBody = (
        <tr>
          <td colSpan={PICKER_COLUMNS} className="reports-wrap-cell">
            <Callout
              tone="danger"
              title="The runs list could not be read."
              action={
                <button type="button" className="btn sm" onClick={() => void refresh()}>
                  <Icon name="refresh-cw" size={14} />
                  Retry
                </button>
              }
            >
              <span className="mono">{runsError}</span>
            </Callout>
          </td>
        </tr>
      );
    } else {
      pickerBody = <SkeletonRows rows={5} columns={PICKER_COLUMNS} />;
    }
  } else if (runs.length === 0) {
    pickerBody = (
      <tr>
        <td colSpan={PICKER_COLUMNS} className="reports-wrap-cell">
          <EmptyState
            compact
            title="No finished runs yet."
            description="Finished runs appear here; macro runs can't be reported."
          />
        </td>
      </tr>
    );
  } else {
    pickerBody = (
      <>
        {runs.map((r) => {
          const macro = r.tier === 'macro';
          const isSelected = selected.has(r.run_id);
          const cls = [macro ? 'disabled' : 'selectable', isSelected ? 'selected' : '']
            .filter(Boolean)
            .join(' ');
          return (
            <tr
              key={r.run_id}
              className={cls}
              data-run-id={r.run_id}
              title={macro ? MACRO_TOOLTIP : undefined}
              onClick={(e) => clickRow(e, r)}
            >
              <td className="reports-check-cell">
                <input
                  type="checkbox"
                  aria-label={`select ${r.run_id}`}
                  disabled={macro}
                  checked={isSelected}
                  onChange={() => toggleRun(r.run_id)}
                />
              </td>
              <td className="reports-id mono">{r.run_id}</td>
              <td className="secondary" title={r.scenario_id}>
                {r.scenario_name ?? scenarioNames.get(r.scenario_id) ?? r.scenario_id}
              </td>
              <td>
                <TierBadge tier={r.tier} />
              </td>
              <td>
                <StatusChip status={r.status} />
              </td>
              <td className="hash">
                <HashValue value={runsDemo ? DEMO_HASH_LABEL : r.config_hash} />
              </td>
              <td>
                <span className="tag-row">
                  <SeededBadge seeded={r.seeded} />
                  {runsDemo && (
                    <span className="tag demo" title={DEMO_ROW_TITLE}>
                      DEMO
                    </span>
                  )}
                </span>
              </td>
            </tr>
          );
        })}
      </>
    );
  }

  // the table runs on local records as soon as there are any; otherwise it
  // waits for the server's first answer
  const reportsLoading = rows.length === 0 && serverListed && serverReports === null;
  let reportsBody: JSX.Element;
  if (rows.length > 0) {
    reportsBody = (
      <>
        {rows.map(({ rec, origin }) => (
          <tr key={rec.report_id}>
            <td className="reports-id mono">{rec.report_id}</td>
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
                <div
                  className="fail-reason"
                  title={`${rec.error_kind ? `${rec.error_kind}: ` : ''}${rec.error}`}
                >
                  {rec.error_kind ? `${rec.error_kind}: ` : ''}
                  {rec.error}
                </div>
              )}
            </td>
            <td className="secondary reports-criteria-cell">
              <span title={profileTitle(rec.profile)}>{rec.profile ?? 'unknown'}</span>
              {observedLine(rec.observed) && (
                <div
                  className="reports-observed small"
                  title="Counted by the server from the observations artifact and the run artifacts: what the link-flow and segment-speed criteria were actually scored on."
                >
                  {observedLine(rec.observed)}
                </div>
              )}
            </td>
            <td className="secondary mono">{rec.created_at.replace('T', ' ').slice(0, 19)} UTC</td>
            <td className="secondary mono">{rec.run_ids.join(', ')}</td>
            <td className="reports-downloads-cell">
              <div className="reports-downloads">
                <button
                  type="button"
                  className="btn ghost sm"
                  aria-label="Download .md"
                  disabled={rec.status !== 'done'}
                  title={
                    rec.status === 'done'
                      ? 'Markdown only — its figures are linked, not embedded'
                      : `report is ${rec.status} — the markdown is served only once it is done`
                  }
                  onClick={() => void downloadMarkdown(rec)}
                >
                  <Icon name="download" size={14} />
                  .md
                </button>
                <button
                  type="button"
                  className="btn ghost sm"
                  aria-label="Download .zip (with figures)"
                  disabled={rec.status !== 'done'}
                  title={
                    rec.status === 'done'
                      ? 'Markdown plus the figure PNGs it references'
                      : `report is ${rec.status} — the archive is served only once it is done`
                  }
                  onClick={() => void downloadArchive(rec)}
                >
                  <Icon name="download" size={14} />
                  .zip (with figures)
                </button>
                <button
                  type="button"
                  className="btn ghost sm"
                  aria-label="Download PDF"
                  disabled={rec.status !== 'done'}
                  title={
                    rec.status === 'done'
                      ? 'Optional PDF rendering — 404 when the report was generated without one'
                      : `report is ${rec.status} — the PDF is served only once it is done`
                  }
                  onClick={() => void downloadPdf(rec)}
                >
                  <Icon name="download" size={14} />
                  PDF
                </button>
              </div>
            </td>
          </tr>
        ))}
      </>
    );
  } else if (reportsLoading && authFailed) {
    reportsBody = (
      <tr>
        <td colSpan={REPORT_COLUMNS} className="reports-wrap-cell">
          <EmptyState
            compact
            title="Reports are not loading."
            description="The API key was rejected. Save a new key in Settings to resume."
          />
        </td>
      </tr>
    );
  } else if (reportsLoading && listError !== null) {
    reportsBody = (
      <tr>
        <td colSpan={REPORT_COLUMNS} className="reports-wrap-cell">
          <Callout
            tone="danger"
            title="The report history could not be read."
            action={
              <button
                type="button"
                className="btn sm"
                onClick={() => void refreshServerReports()}
              >
                <Icon name="refresh-cw" size={14} />
                Retry
              </button>
            }
          >
            <span className="mono">{listError}</span>
          </Callout>
        </td>
      </tr>
    );
  } else if (reportsLoading) {
    reportsBody = <SkeletonRows rows={5} columns={REPORT_COLUMNS} />;
  } else {
    reportsBody = (
      <tr>
        <td colSpan={REPORT_COLUMNS} className="reports-wrap-cell">
          {serverDemo ? (
            // the empty list came from the demo backend: it says nothing
            // about what the server holds
            <EmptyState
              compact
              title="The server's reports are not listed: this page is showing demo data."
              description="Its history appears here once the API answers."
            />
          ) : (
            <EmptyState
              compact
              title={
                serverListed
                  ? 'No reports on this server yet.'
                  : 'No reports requested in this browser yet.'
              }
              description="Generate one above from finished micro runs."
            />
          )}
        </td>
      </tr>
    );
  }

  const generateBlocked = busy || microSelected.length === 0 || offline;

  return (
    <div className="view">
      <PageHeader
        title="Reports"
        documentTitle="Reports"
        meta={
          <>
            {!reportsLoading && (
              <span className="mono">{`${rows.length} report${rows.length === 1 ? '' : 's'}`}</span>
            )}
            {demoRows > 0 && (
              <span
                className="tag demo"
                title="The API is unreachable: rows badged DEMO come from the in-browser demo backend, not a server."
              >
                DEMO DATA
              </span>
            )}
          </>
        }
        description="FHWA-style calibration and validation reports from finished micro runs."
      />

      <section className="panel" aria-labelledby="reports-new-title">
        <div className="panel-head">
          <h2 className="panel-title" id="reports-new-title">
            New report
          </h2>
        </div>
        <div className="panel-body reports-steps">
          <div className="reports-step">
            <h3 className="reports-step-title" id="reports-step-runs">
              <span className="reports-step-num" aria-hidden="true">
                1
              </span>
              Choose finished micro runs
            </h3>
            {preselectNote && (
              <PreselectCallout
                note={preselectNote}
                demo={runsDemo}
                onDismiss={dismissPreselect}
              />
            )}
            <div
              className="table-wrap scroll-y reports-picker"
              aria-busy={!runsLoaded && !authFailed && runsError === null}
            >
              <table className="data" aria-label="finished runs">
                <thead>
                  <tr>
                    <th scope="col" className="reports-check-cell">
                      <span className="visually-hidden">Select</span>
                    </th>
                    <th scope="col">Run</th>
                    <th scope="col">Scenario</th>
                    <th scope="col">Tier</th>
                    <th scope="col">Status</th>
                    <th scope="col">Config hash</th>
                    <th scope="col">Labels</th>
                  </tr>
                </thead>
                <tbody>{pickerBody}</tbody>
              </table>
            </div>
          </div>

          <div className="reports-step">
            <h3 className="reports-step-title" id="reports-step-score">
              <span className="reports-step-num" aria-hidden="true">
                2
              </span>
              Score against
            </h3>
            <div className="form-grid reports-score-grid">
              <div className="field">
                <label htmlFor="r-profile">Criteria profile</label>
                <select
                  id="r-profile"
                  className="input"
                  value={profile}
                  disabled={profileOptions === null || !criteriaListed}
                  aria-describedby="r-profile-source"
                  title={
                    profileOptions === null
                      ? LOADING_PROFILE_OPTIONS[0].source
                      : criteriaListed
                        ? 'Acceptance thresholds the report is scored against (GET /criteria). Thresholds only — the measurements are computed from the run artifacts.'
                        : FALLBACK_PROFILE_OPTIONS[0].source
                  }
                  onChange={(e) => setProfile(e.target.value)}
                >
                  {profileChoices.map((p) => (
                    <option key={p.name} value={p.name} title={p.source}>
                      {p.name}
                      {p.default ? ' (default)' : ''}
                    </option>
                  ))}
                </select>
                {/* provenance of the profile, in the server's words — never
                    its thresholds restated from this dashboard's copy */}
                <p
                  className={`field-help${longSource && !sourceOpen ? ' reports-source-clamp' : ''}`}
                  id="r-profile-source"
                >
                  {profileOptions !== null && criteriaListed && profileSource
                    ? `Source: ${profileSource}`
                    : profileSource}
                </p>
                {/* a profile's provenance can run to a page: three lines by
                    default, all of it on request (the clamp is visual only, so
                    a screen reader reads the whole text either way) */}
                {longSource && (
                  <button
                    type="button"
                    className="btn link sm reports-source-toggle"
                    aria-expanded={sourceOpen}
                    aria-controls="r-profile-source"
                    onClick={() => setSourceOpen((o) => !o)}
                  >
                    {sourceOpen ? 'Show less' : 'Show full source'}
                  </button>
                )}
              </div>
              <div className="field">
                <label htmlFor="r-observations">Score against observations</label>
                <select
                  id="r-observations"
                  className="input"
                  value={observations}
                  aria-describedby="r-observations-help"
                  title={
                    corridorsListed
                      ? 'The detector observations the link-flow (GEH) and segment-speed rows are scored against. Without one those criteria are reported as "not evaluated" — the report still states what was simulated, but nothing is compared with a road.'
                      : 'This service answered 404 to GET /corridors, so the corridors it has onboarded cannot be listed here. A server-side observations path can still be typed.'
                  }
                  onChange={(e) => setObservations(e.target.value)}
                >
                  <option value={OBSERVATIONS_NONE}>none — criteria not evaluated</option>
                  {observationChoices.map((c) => (
                    <option
                      key={c.corridor_id}
                      value={c.observations_path ?? ''}
                      title={`Onboarded ${c.created_at.replace('T', ' ').slice(0, 19)} UTC (${c.corridor_id})`}
                    >
                      {c.name}
                    </option>
                  ))}
                  <option value={OBSERVATIONS_CUSTOM}>server path…</option>
                </select>
                {observations === OBSERVATIONS_CUSTOM && (
                  <input
                    className="input mono"
                    aria-label="Observations path on the server"
                    placeholder="corridors/cor_…/observations.json"
                    value={customObservations}
                    onChange={(e) => setCustomObservations(e.target.value)}
                  />
                )}
                <p className="field-help" id="r-observations-help">
                  {corridorsListed
                    ? 'An onboarded corridor’s detector observations, or a path on the server.'
                    : 'This service cannot list its onboarded corridors; a server path can still be typed.'}
                </p>
              </div>
            </div>
          </div>
        </div>
        <div className="panel-foot reports-foot">
          <div className="form-actions reports-actions">
            <div className="form-actions-summary" id="r-generate-summary">
              <span className="mono">{microSelected.length}</span>
              {` run${microSelected.length === 1 ? '' : 's'} selected · `}
              {observationsLabel === null
                ? 'scored against no observations: link-flow and speed rows read “not evaluated”'
                : `scored against ${observationsLabel}`}
              {offline && (
                <>
                  {' · '}
                  <span className="meta-warning">reconnect to generate</span>
                </>
              )}
            </div>
            <div className="form-actions-buttons">
              <button
                ref={generateRef}
                type="button"
                className="btn primary"
                disabled={generateBlocked}
                aria-busy={busy || undefined}
                aria-describedby="r-generate-summary"
                title={offline ? OFFLINE_WRITE_MESSAGE : undefined}
                onClick={() => void generate()}
              >
                {busy && <Icon name="loader-circle" size={16} className="spin" />}
                Generate report ({microSelected.length})
              </button>
            </div>
          </div>
          {launchError && (
            <Callout tone="danger" title="The server refused this report request." role="status">
              <span className="mono">{launchError}</span>
            </Callout>
          )}
        </div>
      </section>

      <section className="panel reports-table-panel" aria-labelledby="reports-list-title">
        <div className="panel-head">
          <h2 className="panel-title" id="reports-list-title" tabIndex={-1} ref={listTitleRef}>
            Generated reports
          </h2>
          <span className="spacer" />
          <span
            className="panel-sub"
            title={
              demoRows > 0
                ? 'The API is unreachable, so these rows come from the built-in demo backend. Nothing here was generated by a server, and no status shown for a DEMO row is evidence about a real report.'
                : serverDemo
                  ? 'The report list was read from the built-in demo backend, which holds none: it says nothing about the reports the server holds.'
                  : serverListed
                    ? 'GET /reports, newest first. Rows badged LOCAL exist only in this browser: they were requested against another API, or before the list endpoint existed, so this server may not hold them.'
                    : 'This service answered 404 to GET /reports, so only this browser’s own records can be listed.'
            }
          >
            {demoRows > 0
              ? `built-in demo data — ${demoRows} row${demoRows === 1 ? '' : 's'} from no server`
              : serverDemo
                ? 'demo data — server history not listed'
                : serverListed
                  ? localOnly > 0
                    ? `server history (GET /reports) + ${localOnly} local-only record${localOnly === 1 ? '' : 's'}`
                    : 'server history (GET /reports), newest first'
                  : "this service has no GET /reports — this browser's records only"}
          </span>
        </div>
        <div className="table-wrap scroll-y" aria-busy={reportsLoading && !authFailed && listError === null}>
          <table className="data" aria-label="generated reports">
            <thead>
              <tr>
                <th scope="col">Report</th>
                <th scope="col">Source</th>
                <th scope="col">Status</th>
                <th scope="col">Criteria</th>
                <th scope="col">Created</th>
                <th scope="col">Runs</th>
                <th scope="col">
                  <span className="visually-hidden">Downloads</span>
                </th>
              </tr>
            </thead>
            <tbody>{reportsBody}</tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

/** The line under step 1 that answers a `?select=`: which run was selected,
 * or why it was not. Persistent until dismissed (a toast would be gone before
 * the user has found the picker). */
function PreselectCallout({
  note,
  demo,
  onDismiss,
}: {
  note: PreselectNote;
  demo: boolean;
  onDismiss: () => void;
}): JSX.Element {
  const id = <span className="mono">{note.runId}</span>;
  let tone: 'info' | 'warning' | 'neutral' = 'warning';
  let body: JSX.Element;
  switch (note.outcome) {
    case 'selected':
      tone = 'info';
      body = <>{id} is selected. Choose what to score it against, then generate the report.</>;
      break;
    case 'macro':
      body = (
        <>
          {id} is a macro (screening) run, so it is not selected: reports need finished micro runs,
          and the API refuses a validation report from macro runs.
        </>
      );
      break;
    case 'waiting':
      tone = 'neutral';
      body = (
        <>
          {id} is {note.status ?? 'not finished'}. It will be selected here once it is done.
        </>
      );
      break;
    case 'failed':
      body = <>{id} failed, so it has no results to report and is not selected.</>;
      break;
    default:
      body = demo ? (
        <>{id} is not in the demo data shown while the API is unreachable, so it is not selected.</>
      ) : (
        <>{id} is not in this server's runs list, so it is not selected.</>
      );
  }
  return (
    <div className="reports-preselect">
      <Callout
        tone={tone}
        role="status"
        action={
          <button type="button" className="btn ghost sm" onClick={onDismiss}>
            Dismiss
          </button>
        }
      >
        {body}
      </Callout>
    </div>
  );
}
