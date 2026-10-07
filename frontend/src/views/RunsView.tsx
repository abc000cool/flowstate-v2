/** Runs: mono table with status chips, live progress bars (2 s poll),
 * config hashes, SEEDED and tier badges; plus a launcher that states what it
 * is about to enqueue.
 *
 * The launcher sends `replicates` plus an `overrides` patch (`sim.duration_s`,
 * `seed`) — the API deep-merges it onto the stored config and re-hashes, so a
 * shortened smoke run is a first-class, hash-distinct run rather than an
 * unlabelled variant. Large launches (replicates × duration past
 * `CONFIRM_SIM_MINUTES`) take an explicit second click. It offers the repo
 * presets beside the stored scenarios and stores the chosen preset before
 * running it (`lib/library.ensureStored`), because a fresh install has no
 * stored scenario and an empty launcher is a dead end. Until the user picks
 * one it opens on the cheap `ring_sugiyama` preset, or the cheapest preset
 * when the service has no ring (`lib/library.defaultPreset`), never on
 * whatever sorts first (`corridor_10km`: 20 × 20 min ≈ 6.7 sim-hours).
 * The list can answer after the user has started typing (a cold server parses
 * every preset YAML first): a late answer still chooses that opening scenario
 * and fills the fields nobody has touched, but never replaces a typed value
 * and never moves a scenario the user picked.
 *
 * Two honesty rules, the same ones the Scenarios cards follow: a failed run
 * shows the service's own reason rather than a bare status chip, and a row
 * served by the in-browser demo backend is badged DEMO, carries no config hash
 * and does not animate a progress bar for replicates no worker is computing.
 *
 * Layout per docs/design/DASHBOARD_DESIGN.md §10.3: page header (count, Live
 * pill), the launcher as a form grid with a cost/reason action bar, then the
 * runs table, whose run id is a real link. The table renders skeleton rows on
 * first load, an error callout if that first read fails, an empty state, then
 * rows. A later poll error keeps the last render in the table, but the header
 * stops calling it live: the Live pill becomes "Stale — last update hh:mm:ss"
 * with the service's error beside it, until a poll lands again. (`/health`
 * can answer while `GET /runs` fails — a locked store, a 502 from the front
 * end — so the shell's offline banner does not cover this.)
 *
 * Filter chips above the table (§10.3 P2) narrow the loaded rows by status,
 * tier and scenario on the client, with faceted counts; the choice lives in
 * the URL (`?status=running,failed`) so a filtered view can be shared, and a
 * filter that matches nothing says so with a way to clear it. The command
 * palette's "Launch a ring run…" lands here with router state that opens the
 * launcher on the ring preset, prefilled and focused; nothing is queued until
 * Launch run is pressed. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MouseEvent as ReactMouseEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  createRun,
  isMockActive,
  listPresetScenarios,
  listRuns,
  listScenarios,
  OFFLINE_WRITE_MESSAGE,
} from '../api/client';
import type {
  CreateRunRequest,
  PresetSummary,
  RunStatus,
  RunSummary,
  ScenarioConfig,
} from '../api/types';
import { ProgressBar, SeededBadge, StatusChip, TierBadge } from '../components/bits';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Icon } from '../components/icons';
import { PageHeader } from '../components/PageHeader';
import { RunFilterGroup } from '../components/RunFilterGroup';
import { toast, toastError } from '../components/toast';
import { Callout } from '../components/ui/Callout';
import { HashValue } from '../components/ui/CopyButton';
import { EmptyState } from '../components/ui/EmptyState';
import { SkeletonRows } from '../components/ui/Skeleton';
import { DEMO_HASH_LABEL, DEMO_ROW_TITLE } from '../lib/demo';
import { failureReason, formatClockTime, formatFetchError } from '../lib/format';
import { useAuthFailed, useOfflineFallback, usePoll, wantedLaunchPreset } from '../lib/hooks';
import {
  defaultPreset,
  ensureStored,
  isPreset,
  itemKey,
  mergeLibrary,
  type LibraryItem,
} from '../lib/library';
import {
  clampInt,
  describeSimMinutes,
  MAX_DURATION_S,
  MAX_REPLICATES,
  MAX_SEED,
  MIN_DURATION_S,
  MIN_SEED,
  needsLaunchConfirm,
  simMinutes,
  warmupProblem,
} from '../lib/limits';
import { MIN_REPLICATES } from '../lib/metrics';
import {
  filterRuns,
  hasRunFilters,
  NO_RUN_FILTERS,
  parseRunFilters,
  runFacet,
  runFiltersSearch,
  toggleRunFilter,
  type RunFilterKey,
  type RunFilters,
} from '../lib/runFilters';

const RUNS_POLL_MS = 2000;
/** Columns of the runs table (skeleton rows and the empty row span them). */
const RUN_COLUMNS = 7;
const SCENARIOS_POLL_MS = 3000;
/** Once the library is loaded, refresh it slowly (new scenarios, names). */
const SCENARIOS_IDLE_POLL_MS = 30000;

/** The launcher's fields that a scenario's own values fill. */
type LaunchField = 'reps' | 'duration' | 'seed';

/** Parse an optional numeric field: '' means "use the scenario's own value". */
function numOr(raw: string, fallback: number | null): number | null {
  if (raw.trim() === '') return fallback;
  const v = Number(raw);
  return Number.isFinite(v) ? v : fallback;
}

/** Clamp a numeric field's raw value while keeping '' meaningful here: an
 * empty box is "use the scenario's own value", not zero. Everything else goes
 * through `clampInt`, so a typo cannot post `duration_s: -5` or a negative
 * seed the runner would reject. */
function clampRaw(raw: string, lo: number, hi: number): string {
  return raw === '' ? '' : String(clampInt(Number(raw), lo, hi, lo));
}

/** Chip labels for the status and tier filters. Capitalised, unlike the
 * verbatim lower-case status pills in the table, so a chip is never mistaken
 * for a row's status (and never matches it). */
const STATUS_CHIP_LABELS: Record<RunStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
};
const TIER_CHIP_LABELS: Record<string, string> = { micro: 'Micro', macro: 'Macro' };

export function RunsView(): JSX.Element {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  /** True when the rows on screen came from the in-browser demo backend,
   * captured at fetch time (the client decides mock vs live per call). */
  const [runsDemo, setRunsDemo] = useState(false);
  const [library, setLibrary] = useState<LibraryItem[]>([]);
  /** The presets of the last library load, kept apart from the merged
   * library: a preset already stored shows there as the stored scenario, and
   * the launcher's default is chosen among presets either way. */
  const [presets, setPresets] = useState<PresetSummary[]>([]);
  const [launchKey, setLaunchKey] = useState('');
  const [launchTier, setLaunchTier] = useState<'micro' | 'macro'>('micro');
  const [repsRaw, setRepsRaw] = useState('');
  const [durationRaw, setDurationRaw] = useState('');
  const [seedRaw, setSeedRaw] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  /** Why the newest read of the runs list to settle failed; null once a poll
   * lands. With nothing on screen yet it is the table's error callout; after a
   * first answer the rows stay and the header reads stale instead of live. */
  const [loadError, setLoadError] = useState<string | null>(null);
  /** When the rows on screen were read: the stale pill's "last update". */
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const authFailed = useAuthFailed();
  // POST /runs never falls back to the demo backend, so the launcher says so
  // up front instead of failing on the click (api/client.assertWritable)
  const offline = useOfflineFallback();

  // Sequence numbers for the two polls: an older answer never replaces a newer
  // one. Without this the last demo-backed read of a reconnect — in flight
  // when the link came back — lands after the live one and puts demo rows back
  // on screen as if they were the server's.
  const runsSeq = useRef(0);
  /** The runs poll is compared against the newest read that has *settled*
   * (rows or an error), not the newest sent: `usePoll` starts a read every 2 s
   * without waiting for the last, so once `GET /runs` takes longer than that
   * (a store held by its busy timeout, a gateway 504 after its own timeout)
   * every read is overtaken by the next before it answers. "Drop unless newest
   * sent" then discarded every answer, failures included, and the header said
   * Live over rows that had stopped updating. Each answer now lands unless a
   * newer one already has, so the header follows the newest outcome: Live
   * after a success, Stale after a failure. */
  const runsSettled = useRef(0);
  const librarySeq = useRef(0);
  /** The library is compared against the newest response *applied*, not the
   * newest request sent: `GET /scenarios/preset` takes about a second, and the
   * retry poll, the reconnect effect and the mount each start another load, so
   * "drop unless newest sent" discarded every answer until the requests
   * stopped overlapping (the launcher sat empty for ~6 s). An older answer
   * still never replaces a newer one. */
  const libraryApplied = useRef(0);

  const poll = useCallback(async () => {
    const seq = ++runsSeq.current;
    const fromDemo = isMockActive();
    try {
      const rows = await listRuns();
      if (seq < runsSettled.current) return;
      runsSettled.current = seq;
      setRuns(rows);
      setRunsDemo(fromDemo);
      setLoadError(null);
      setUpdatedAt(new Date());
    } catch (err) {
      // a toast per failed poll would spam at 2 s cadence (and a rejected key
      // pauses this poll entirely). A first load that has nothing to show says
      // why in the table, instead of a skeleton forever; after that the rows
      // stay and the header says they are stale, with this error.
      if (seq < runsSettled.current) return;
      runsSettled.current = seq;
      setLoadError(formatFetchError(err));
    }
  }, []);
  usePoll(poll, authFailed ? null : RUNS_POLL_MS);

  // quiet retry until the library loads (covers the offline-fallback race
  // where the first fetch fires before the health probe flips to demo), then a
  // slow refresh so newly created scenarios and their names appear
  const libraryLoaded = library.length > 0;
  const loadLibrary = useCallback(async () => {
    const seq = ++librarySeq.current;
    try {
      // a service older than `GET /scenarios/preset` answers 404: the presets
      // are then unavailable, which must not empty the stored list with them
      const [loadedPresets, stored] = await Promise.all([
        listPresetScenarios().catch(() => [] as PresetSummary[]),
        listScenarios(),
      ]);
      if (seq < libraryApplied.current) return;
      libraryApplied.current = seq;
      setPresets(loadedPresets);
      setLibrary(mergeLibrary(loadedPresets, stored));
    } catch {
      /* retried by usePoll; connectivity is surfaced by the status dot */
    }
  }, []);
  usePoll(
    loadLibrary,
    authFailed ? null : libraryLoaded ? SCENARIOS_IDLE_POLL_MS : SCENARIOS_POLL_MS,
  );

  // On reconnect the runs poll is back within 2 s while the library refresh is
  // 30 s away, so the table would print raw scenario ids for half a minute.
  // Refresh the library the moment the link comes back instead.
  useEffect(() => {
    if (!offline && !authFailed) void loadLibrary();
  }, [offline, authFailed, loadLibrary]);

  const selected = library.find((s) => itemKey(s) === launchKey);
  const base = selected?.config;

  // The config the launcher last showed. Launching a preset stores it, and the
  // merged library then lists it as the stored scenario under a new key: follow
  // the config there instead of leaving the select on an option that no longer
  // exists (the select would show its first option while Launch stayed off).
  // With nothing shown yet, open on the cheap default preset (or its stored
  // copy, same config hash), and only then on whatever is listed first.
  const lastHash = useRef<string | null>(null);
  useEffect(() => {
    if (selected) lastHash.current = selected.config_hash;
  }, [selected]);
  useEffect(() => {
    if (library.length === 0 || library.some((s) => itemKey(s) === launchKey)) return;
    const same = library.find((s) => s.config_hash === lastHash.current);
    const fallbackHash = defaultPreset(presets)?.config_hash;
    const cheap = library.find((s) => s.config_hash === fallbackHash);
    setLaunchKey(itemKey(same ?? cheap ?? library[0]));
  }, [library, presets, launchKey]);

  // The fields the user has typed into since the launcher last showed a
  // scenario's values at the user's own request (a pick in the select, or the
  // palette's "Launch a ring run…"). Values that arrive by themselves — the
  // library answering after the first keystroke, a refresh carrying an edited
  // preset — fill only the other fields, so they never replace what was
  // typed. An emptied box counts as typed: it means "the scenario's own
  // value" and stays empty.
  const edited = useRef(new Set<LaunchField>());
  /** Show `cfg`'s own values in every field the user has not typed into. */
  const fillUntouched = useCallback((cfg: ScenarioConfig | undefined): void => {
    if (!edited.current.has('reps')) setRepsRaw(cfg ? String(cfg.replicates) : '');
    if (!edited.current.has('duration')) setDurationRaw(cfg ? String(cfg.sim.duration_s) : '');
    if (!edited.current.has('seed')) setSeedRaw(cfg ? String(cfg.seed) : '');
  }, []);
  const typeInto = (field: LaunchField, set: (raw: string) => void, raw: string): void => {
    edited.current.add(field);
    set(raw);
  };

  // Show the scenario's own values as the starting point, once per config the
  // launcher lands on by itself (the opening default, a preset followed to
  // its stored copy, a preset edited on the server): the scenario poll hands
  // back fresh objects every tick, so keying this on the config identity
  // would refill on every tick. Keyed on the hash, so a preset turning into
  // its stored copy is not a new config.
  const prefilledFor = useRef<string | null>(null);
  const selectedKey = selected ? selected.config_hash : '';
  useEffect(() => {
    // nothing selected (the library loading, or a stored preset between its
    // old and new key) is not a new config: keep what the user typed
    if (selectedKey === '' || prefilledFor.current === selectedKey) return;
    prefilledFor.current = selectedKey;
    fillUntouched(base);
  }, [selectedKey, base, fillUntouched]);

  /** A scenario picked in the select is a new starting point: every field
   * shows its values, whatever was typed for the one before. */
  const pickScenario = (key: string): void => {
    setLaunchKey(key);
    const item = library.find((s) => itemKey(s) === key);
    if (!item) return;
    edited.current.clear();
    prefilledFor.current = item.config_hash;
    fillUntouched(item.config);
  };

  // "Launch a ring run…" from the command palette arrives as router state
  // (lib/hooks launchPresetState). Taken once and cleared from the history
  // entry, so a reload or a Back to it does not re-apply it; applied once the
  // library has loaded. It only prefills and focuses the launcher. It is the
  // user's newest word on every field when it is taken: what was typed
  // before it gives way to the scenario's values, what is typed while the
  // library is still loading does not.
  const [launchRequest, setLaunchRequest] = useState<string | null>(null);
  const [focusLauncher, setFocusLauncher] = useState(false);
  const scenarioSelectRef = useRef<HTMLSelectElement>(null);
  useEffect(() => {
    const wanted = wantedLaunchPreset(location.state);
    if (wanted === null) return;
    setLaunchRequest(wanted);
    setLaunchTier('micro');
    edited.current.clear();
    navigate({ pathname: location.pathname, search: location.search }, { replace: true, state: null });
  }, [location, navigate]);

  useEffect(() => {
    if (launchRequest === null || library.length === 0) return;
    setLaunchRequest(null);
    const preset = presets.find(
      (p) => p.name === launchRequest || p.filename === `${launchRequest}.yaml`,
    );
    // the preset, or its stored copy (same config hash), or a stored scenario
    // of that name on a service that serves no presets
    const item =
      (preset && library.find((s) => s.config_hash === preset.config_hash)) ??
      library.find((s) => s.name === launchRequest);
    if (!item) {
      toast('info', `This service has no ${launchRequest} scenario; choose one in the launcher.`);
      setFocusLauncher(true);
      return;
    }
    setLaunchKey(itemKey(item));
    // the scenario's own values, even if this scenario was already selected
    // and its fields had been edited before the request (cleared when it was
    // taken), but not over a field typed into since
    prefilledFor.current = item.config_hash;
    lastHash.current = item.config_hash;
    fillUntouched(item.config);
    setFocusLauncher(true);
  }, [launchRequest, library, presets, fillUntouched]);

  // focus the launcher's first field, unless the user has moved focus on
  // since the request (the shell parks it on the page content meanwhile)
  useEffect(() => {
    if (!focusLauncher) return;
    setFocusLauncher(false);
    const active = document.activeElement;
    if (active === null || active === document.body || active.id === 'content') {
      scenarioSelectRef.current?.focus();
    }
  }, [focusLauncher, launchKey]);

  /** The table's filters, read from and written to the URL (lib/runFilters),
   * so a filtered view is a link. Written with replace: toggling chips does
   * not fill the Back history. */
  const filters = useMemo(() => parseRunFilters(location.search), [location.search]);
  const setFilters = (next: RunFilters): void => {
    navigate(
      { pathname: location.pathname, search: runFiltersSearch(location.search, next) },
      { replace: true },
    );
  };

  /** Scenario id → name, so the table names the corridor rather than an id
   * (the API's RunOut carries no scenario name). Presets have no id yet, so
   * only the stored side of the library can name a run's scenario. */
  const scenarioNames = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of library) if (!isPreset(s)) m.set(s.scenario_id, s.name);
    return m;
  }, [library]);

  const plannedReps = numOr(repsRaw, base?.replicates ?? null);
  const plannedDuration = numOr(durationRaw, base?.sim.duration_s ?? null);
  const plannedSeed = numOr(seedRaw, base?.seed ?? null);
  const total =
    plannedReps !== null && plannedDuration !== null
      ? simMinutes(plannedReps, plannedDuration)
      : null;
  // a duration inside the warm-up kills every replicate on the worker; say so
  // here rather than spending the round trip and the queue slot
  const warmupBlock = warmupProblem(plannedDuration, base?.sim.warmup_s ?? null);

  const buildRequest = (scenarioId: string): CreateRunRequest => {
    const overrides: Record<string, unknown> = {};
    if (plannedDuration !== null && plannedDuration !== base?.sim.duration_s) {
      overrides.sim = { duration_s: plannedDuration };
    }
    if (plannedSeed !== null && plannedSeed !== base?.seed) overrides.seed = plannedSeed;
    const req: CreateRunRequest = { scenario_id: scenarioId, tier: launchTier };
    if (plannedReps !== null) req.replicates = plannedReps;
    if (Object.keys(overrides).length > 0) req.overrides = overrides;
    return req;
  };

  const launchRef = useRef<HTMLButtonElement>(null);
  const refocusLaunch = useRef(false);

  const doLaunch = async (): Promise<void> => {
    if (!selected || warmupBlock) return;
    // the button is disabled while busy, which drops keyboard focus to <body>;
    // put it back afterwards when the launch started from the button
    const fromButton = document.activeElement === launchRef.current;
    // a launch confirmed in the dialog closes it while this button is still
    // disabled, so the dialog's own focus restore to its opener finds nothing
    // to focus: hand focus back here once the button is enabled again. (A
    // refused launch keeps the dialog open; it refocuses its own button.)
    const fromDialog = confirming;
    setBusy(true);
    try {
      // a preset is a repo YAML: it has to be stored before a run can name it
      const { scenario_id, stored } = await ensureStored(selected);
      if (stored) toast('ok', `preset ${selected.name} stored as ${scenario_id}`);
      const res = await createRun(buildRequest(scenario_id));
      toast('ok', `run ${res.run_id} queued`);
      setConfirming(false);
      if (fromDialog) refocusLaunch.current = true;
      await poll();
      await loadLibrary(); // a stored preset joins the library immediately
    } catch (err) {
      toastError(err, 'launch');
    } finally {
      setBusy(false);
      if (fromButton) refocusLaunch.current = true;
    }
  };

  const launch = (): void => {
    if (!selected || warmupBlock) return;
    if (plannedReps !== null && plannedDuration !== null && needsLaunchConfirm(plannedReps, plannedDuration)) {
      setConfirming(true);
      return;
    }
    void doLaunch();
  };

  // a click anywhere on a row opens the run, except on its own controls (the
  // id link navigates by itself; the copy button must not navigate)
  const openRow = (e: ReactMouseEvent<HTMLTableRowElement>, runId: string): void => {
    if ((e.target as Element).closest('a, button, input, select, label')) return;
    navigate(`/runs/${runId}`);
  };

  const launchBlocked = !selected || busy || offline || warmupBlock !== null;

  // once the button is enabled again (a disabled button cannot take focus; a
  // stored preset briefly leaves the select between keys), unless focus has
  // moved on since
  useEffect(() => {
    if (!refocusLaunch.current || launchBlocked) return;
    refocusLaunch.current = false;
    if (document.activeElement === document.body) launchRef.current?.focus();
  }, [launchBlocked]);

  const replicatesLow = plannedReps !== null && plannedReps < MIN_REPLICATES;

  const visible = useMemo(() => (runs === null ? [] : filterRuns(runs, filters)), [runs, filters]);
  const filtered = hasRunFilters(filters);
  const clearFilters = (): void => setFilters(NO_RUN_FILTERS);
  /** What a scenario chip is called: the library's name for the id, else the
   * scenario part of a row's name (a row may name its variant after a `·`,
   * which is the run's, not the scenario's), else the id itself. */
  const scenarioLabel = (id: string): string =>
    scenarioNames.get(id) ??
    runs
      ?.find((r) => r.scenario_id === id && r.scenario_name)
      ?.scenario_name?.split('·')[0]
      .trim() ??
    id;

  let tableBody: JSX.Element;
  if (runs === null) {
    if (authFailed) {
      tableBody = (
        <tr>
          <td colSpan={RUN_COLUMNS}>
            <EmptyState
              compact
              title="Runs are not loading."
              description="The API key was rejected. Save a new key in Settings to resume."
            />
          </td>
        </tr>
      );
    } else if (loadError !== null) {
      tableBody = (
        <tr>
          <td colSpan={RUN_COLUMNS} className="runs-error-cell">
            <Callout
              tone="danger"
              title="The runs list could not be read."
              action={
                <button type="button" className="btn sm" onClick={() => void poll()}>
                  <Icon name="refresh-cw" size={14} />
                  Retry
                </button>
              }
            >
              <span className="mono">{loadError}</span>
            </Callout>
          </td>
        </tr>
      );
    } else {
      tableBody = <SkeletonRows rows={5} columns={RUN_COLUMNS} />;
    }
  } else if (runs.length === 0) {
    tableBody = (
      <tr>
        <td colSpan={RUN_COLUMNS}>
          <EmptyState
            compact
            title="No runs yet."
            description="Runs appear here after you launch one above."
          />
        </td>
      </tr>
    );
  } else if (visible.length === 0) {
    tableBody = (
      <tr>
        <td colSpan={RUN_COLUMNS}>
          <EmptyState
            compact
            title="No runs match these filters."
            description={`Clear them to see all ${runs.length} run${runs.length === 1 ? '' : 's'}.`}
            action={
              <button type="button" className="btn sm" onClick={clearFilters}>
                Clear filters
              </button>
            }
          />
        </td>
      </tr>
    );
  } else {
    tableBody = (
      <>
        {visible.map((r) => (
          <tr key={r.run_id} className="rowlink" onClick={(e) => openRow(e, r.run_id)}>
            <td className="run-id-cell">
              <Link to={`/runs/${r.run_id}`} className="run-id mono">
                {r.run_id}
              </Link>
            </td>
            <td className="secondary" title={r.scenario_id}>
              {r.scenario_name ?? scenarioNames.get(r.scenario_id) ?? r.scenario_id}
            </td>
            <td>
              <TierBadge tier={r.tier} />
            </td>
            <td>
              <StatusChip status={r.status} />
              {r.status === 'failed' && (
                // the reason the API already sent, not a status chip the
                // user has to take to the server logs
                <div className="fail-reason small" title={failureReason(r.error, r.error_kind)}>
                  {failureReason(r.error, r.error_kind)}
                </div>
              )}
            </td>
            <td className="runs-progress-cell">
              {runsDemo ? (
                // an animated bar would show a worker computing replicates
                // that no server has ever been asked for
                <span className="mono small muted">
                  {r.progress.completed_replicates}/{r.progress.total_replicates} — demo
                </span>
              ) : (
                <ProgressBar
                  done={r.progress.completed_replicates}
                  total={r.progress.total_replicates}
                  status={r.status}
                />
              )}
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
        ))}
      </>
    );
  }

  // set together with the rows, so known whenever there are rows to be stale
  const lastUpdate = updatedAt ? formatClockTime(updatedAt) : 'unknown';
  let meta: JSX.Element;
  if (authFailed) {
    meta = <span className="meta-warning">paused — API key rejected</span>;
  } else {
    meta = (
      <>
        {runs !== null && (
          <span className="mono">{`${runs.length} run${runs.length === 1 ? '' : 's'}`}</span>
        )}
        {runsDemo ? (
          <span className="tag demo" title={DEMO_ROW_TITLE}>
            DEMO DATA
          </span>
        ) : runs !== null && loadError !== null ? (
          // the newest polls failed: the rows are the last answer, not live
          <>
            <span
              className="meta-stale"
              title={`GET /runs is failing, so the table shows the answer read at ${lastUpdate}. Retrying every 2 s.`}
            >
              <Icon name="triangle-alert" size={12} className="meta-stale-icon" />
              {`Stale — last update ${lastUpdate}`}
            </span>
            <span className="meta-stale-reason mono" title={loadError}>
              {loadError}
            </span>
          </>
        ) : (
          runs !== null && (
            <span className="meta-live" title="Polling every 2 s">
              <span className="meta-live-dot" aria-hidden="true" />
              Live
            </span>
          )
        )}
      </>
    );
  }

  return (
    <div className="view">
      <PageHeader
        title="Runs"
        documentTitle="Runs"
        meta={meta}
        description="Launch a scenario and follow its replicates. Every run records its seeds and config hash."
      />

      {runsDemo && (
        <Callout tone="warning">
          The API is unreachable, so these are built-in demo runs: no server has queued or computed
          them, their hashes exist nowhere, and their replicate counts are not progress.
        </Callout>
      )}

      <section className="panel" aria-labelledby="runs-launch-title">
        <div className="panel-head">
          <h2 className="panel-title" id="runs-launch-title">
            Launch a run
          </h2>
        </div>
        <div className="panel-body">
          <div className="form-grid runs-launch-grid">
            <div className="field runs-field-scenario">
              <label htmlFor="l-scn">Scenario</label>
              <select
                id="l-scn"
                ref={scenarioSelectRef}
                className="input"
                value={launchKey}
                onChange={(e) => pickScenario(e.target.value)}
              >
                {library.map((s) => (
                  <option key={itemKey(s)} value={itemKey(s)}>
                    {isPreset(s) ? `${s.name} (preset)` : s.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="l-tier">Tier</label>
              <select
                id="l-tier"
                className="input"
                value={launchTier}
                onChange={(e) => setLaunchTier(e.target.value as 'micro' | 'macro')}
              >
                <option value="micro">micro (SUMO)</option>
                <option value="macro">macro (CTM screening)</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="l-dur">Duration (s)</label>
              <input
                id="l-dur"
                className="input"
                type="number"
                min={MIN_DURATION_S}
                max={MAX_DURATION_S}
                step={60}
                placeholder="scenario"
                value={durationRaw}
                aria-invalid={warmupBlock !== null || undefined}
                aria-describedby={warmupBlock ? 'l-dur-hint l-launch-reason' : undefined}
                onChange={(e) =>
                  typeInto(
                    'duration',
                    setDurationRaw,
                    clampRaw(e.target.value, MIN_DURATION_S, MAX_DURATION_S),
                  )
                }
              />
              {warmupBlock !== null && (
                <span className="hint-amber" id="l-dur-hint">
                  inside the {base?.sim.warmup_s} s warm-up
                </span>
              )}
            </div>
            <div className="field">
              <label htmlFor="l-seed">Seed</label>
              <input
                id="l-seed"
                className="input"
                type="number"
                min={MIN_SEED}
                max={MAX_SEED}
                placeholder="scenario"
                value={seedRaw}
                onChange={(e) =>
                  typeInto('seed', setSeedRaw, clampRaw(e.target.value, MIN_SEED, MAX_SEED))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="l-reps">Replicates</label>
              <input
                id="l-reps"
                className="input"
                type="number"
                min={1}
                max={MAX_REPLICATES}
                placeholder="scenario"
                value={repsRaw}
                aria-describedby={replicatesLow ? 'l-reps-hint' : undefined}
                onChange={(e) =>
                  typeInto(
                    'reps',
                    setRepsRaw,
                    e.target.value === ''
                      ? ''
                      : String(clampInt(Number(e.target.value), 1, MAX_REPLICATES, 1)),
                  )
                }
              />
              {replicatesLow && (
                <span className="hint-amber" id="l-reps-hint">
                  below reporting standard n ≥ {MIN_REPLICATES}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="panel-foot form-actions">
          <div className="form-actions-summary" id="l-launch-reason">
            {warmupBlock ? (
              <span className="hint-amber">{warmupBlock}</span>
            ) : offline ? (
              <span>Reconnect before launching: nothing is sent while the server is unreachable.</span>
            ) : total === null ? (
              <span>Scenario defaults</span>
            ) : (
              <span className="mono">
                {`${plannedReps} × ${(plannedDuration ?? 0) / 60} min = ${describeSimMinutes(total)}`}
              </span>
            )}
          </div>
          <div className="form-actions-buttons">
            <button
              ref={launchRef}
              type="button"
              className="btn primary"
              onClick={launch}
              disabled={launchBlocked}
              aria-busy={busy || undefined}
              aria-describedby={warmupBlock || offline ? 'l-launch-reason' : undefined}
              title={offline ? OFFLINE_WRITE_MESSAGE : (warmupBlock ?? undefined)}
            >
              {busy && <Icon name="loader-circle" size={16} className="spin" />}
              Launch run
            </button>
          </div>
        </div>
      </section>

      <section className="panel runs-table-panel">
        {runs !== null && runs.length > 0 && (
          <div className="runs-filters" role="group" aria-label="Filter runs">
            {(['status', 'tier', 'scenario'] as RunFilterKey[]).map((key) => {
              const facet = runFacet(runs, filters, key);
              const chosen = filters[key] as string[];
              // a dimension with one value filters nothing: shown only when the
              // URL already chose something in it
              if (key !== 'status' && facet.values.length < 2 && chosen.length === 0) return null;
              return (
                <RunFilterGroup
                  key={key}
                  id={key}
                  label={key === 'status' ? 'Status' : key === 'tier' ? 'Tier' : 'Scenario'}
                  facet={facet}
                  chosen={chosen}
                  labelFor={(v) =>
                    key === 'status'
                      ? STATUS_CHIP_LABELS[v as RunStatus]
                      : key === 'tier'
                        ? TIER_CHIP_LABELS[v]
                        : scenarioLabel(v)
                  }
                  titleFor={
                    key === 'scenario'
                      ? (v) => v
                      : key === 'tier'
                        ? (v) => (v === 'macro' ? 'CTM screening runs' : 'SUMO microsimulation runs')
                        : undefined
                  }
                  onAll={() => setFilters({ ...filters, [key]: [] })}
                  onToggle={(v) => setFilters(toggleRunFilter(filters, key, v))}
                />
              );
            })}
            {filtered && (
              <div className="runs-filter-summary">
                <span>
                  Showing <span className="mono">{visible.length}</span> of{' '}
                  <span className="mono">{runs.length}</span>
                </span>
                <button type="button" className="btn link sm" onClick={clearFilters}>
                  Clear filters
                </button>
              </div>
            )}
          </div>
        )}
        <div className="table-wrap scroll-y" aria-busy={runs === null && !authFailed && loadError === null}>
          <table className="data" aria-label="runs">
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Scenario</th>
                <th scope="col">Tier</th>
                <th scope="col">Status</th>
                <th scope="col" className="runs-progress-col">
                  Progress
                </th>
                <th scope="col">Config</th>
                <th scope="col">Labels</th>
              </tr>
            </thead>
            <tbody>{tableBody}</tbody>
          </table>
        </div>
      </section>

      {confirming && (
        <ConfirmDialog
          title="Launch this run?"
          busy={busy}
          facts={[
            ['Scenario', selected?.name ?? launchKey],
            ['Tier', launchTier],
            ['Replicates', String(plannedReps)],
            ['Duration', `${((plannedDuration ?? 0) / 60).toFixed(0)} sim-min each`],
            ['Total', describeSimMinutes(total ?? 0)],
          ]}
          confirmLabel="Launch"
          onConfirm={() => void doLaunch()}
          onCancel={() => setConfirming(false)}
        >
          <p className="small muted">
            Every replicate runs the full duration. Lower the replicate count or the duration for a
            smoke test; n ≥ {MIN_REPLICATES} is the reporting standard for headline numbers.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
