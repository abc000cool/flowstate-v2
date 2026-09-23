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
 * stored scenario and an empty launcher is a dead end.
 *
 * Two honesty rules, the same ones the Scenarios cards follow: a failed run
 * shows the service's own reason rather than a bare status chip, and a row
 * served by the in-browser demo backend is badged DEMO, carries no config hash
 * and does not animate a progress bar for replicates no worker is computing. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  createRun,
  isMockActive,
  listPresetScenarios,
  listRuns,
  listScenarios,
  OFFLINE_WRITE_MESSAGE,
} from '../api/client';
import type { CreateRunRequest, PresetSummary, RunSummary } from '../api/types';
import { ProgressBar, SeededBadge, StatusChip, TierBadge } from '../components/bits';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { toast, toastError } from '../components/toast';
import { DEMO_HASH_LABEL, DEMO_ROW_TITLE } from '../lib/demo';
import { failureReason } from '../lib/format';
import { useAuthFailed, useOfflineFallback, usePoll } from '../lib/hooks';
import { ensureStored, isPreset, itemKey, mergeLibrary, type LibraryItem } from '../lib/library';
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

const RUNS_POLL_MS = 2000;
const SCENARIOS_POLL_MS = 3000;
/** Once the library is loaded, refresh it slowly (new scenarios, names). */
const SCENARIOS_IDLE_POLL_MS = 30000;

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

export function RunsView(): JSX.Element {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  /** True when the rows on screen came from the in-browser demo backend,
   * captured at fetch time (the client decides mock vs live per call). */
  const [runsDemo, setRunsDemo] = useState(false);
  const [library, setLibrary] = useState<LibraryItem[]>([]);
  const [launchKey, setLaunchKey] = useState('');
  const [launchTier, setLaunchTier] = useState<'micro' | 'macro'>('micro');
  const [repsRaw, setRepsRaw] = useState('');
  const [durationRaw, setDurationRaw] = useState('');
  const [seedRaw, setSeedRaw] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const authFailed = useAuthFailed();
  // POST /runs never falls back to the demo backend, so the launcher says so
  // up front instead of failing on the click (api/client.assertWritable)
  const offline = useOfflineFallback();

  // Sequence numbers for the two polls: a response that a newer request has
  // already overtaken is dropped. Without this the last demo-backed read of a
  // reconnect — in flight when the link came back — lands after the live one
  // and puts demo rows back on screen as if they were the server's.
  const runsSeq = useRef(0);
  const librarySeq = useRef(0);

  const poll = useCallback(async () => {
    const seq = ++runsSeq.current;
    const fromDemo = isMockActive();
    try {
      const rows = await listRuns();
      if (seq !== runsSeq.current) return;
      setRuns(rows);
      setRunsDemo(fromDemo);
    } catch (err) {
      // toast once per failure burst would spam at 2 s cadence; stay quiet,
      // the rail status dot + banner already surface connectivity (and a
      // rejected key pauses this poll entirely).
      void err;
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
      const [presets, stored] = await Promise.all([
        listPresetScenarios().catch(() => [] as PresetSummary[]),
        listScenarios(),
      ]);
      if (seq !== librarySeq.current) return;
      setLibrary(mergeLibrary(presets, stored));
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

  useEffect(() => {
    if (!launchKey && library.length > 0) setLaunchKey(itemKey(library[0]));
  }, [library, launchKey]);

  const selected = library.find((s) => itemKey(s) === launchKey);
  const base = selected?.config;

  // Show the scenario's own values as the starting point, once per selected
  // config: the scenario poll hands back fresh objects every tick, so keying
  // this on the config identity would wipe whatever the user typed.
  const prefilledFor = useRef<string | null>(null);
  const selectedKey = selected ? `${itemKey(selected)}:${selected.config_hash}` : '';
  useEffect(() => {
    if (prefilledFor.current === selectedKey) return;
    prefilledFor.current = selectedKey;
    setRepsRaw(base ? String(base.replicates) : '');
    setDurationRaw(base ? String(base.sim.duration_s) : '');
    setSeedRaw(base ? String(base.seed) : '');
  }, [selectedKey, base]);

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

  const doLaunch = async (): Promise<void> => {
    if (!selected || warmupBlock) return;
    setBusy(true);
    try {
      // a preset is a repo YAML: it has to be stored before a run can name it
      const { scenario_id, stored } = await ensureStored(selected);
      if (stored) toast('ok', `preset ${selected.name} stored as ${scenario_id}`);
      const res = await createRun(buildRequest(scenario_id));
      toast('ok', `run ${res.run_id} queued`);
      setConfirming(false);
      await poll();
      await loadLibrary(); // a stored preset joins the library immediately
    } catch (err) {
      toastError(err, 'launch');
    } finally {
      setBusy(false);
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

  return (
    <div className="view">
      <div className="view-title">
        Run Operations{' '}
        <span className="count mono">
          {authFailed
            ? 'paused — API key rejected'
            : runs
              ? `${runs.length} runs · 2 s poll`
              : 'loading…'}
        </span>
        {runsDemo && (
          <span className="tag demo" title={DEMO_ROW_TITLE}>
            DEMO DATA
          </span>
        )}
      </div>

      {runsDemo && (
        <p className="hint-amber">
          The API is unreachable, so these are built-in demo runs: no server has queued or
          computed them, their hashes exist nowhere, and their replicate counts are not progress.
        </p>
      )}

      <div className="panel">
        <div className="panel-body row wrap">
          <div className="field">
            <label htmlFor="l-scn">Scenario</label>
            <select
              id="l-scn"
              className="input"
              value={launchKey}
              onChange={(e) => setLaunchKey(e.target.value)}
              style={{ minWidth: 220 }}
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
              style={{ width: 110 }}
              placeholder="scenario"
              value={durationRaw}
              onChange={(e) =>
                setDurationRaw(clampRaw(e.target.value, MIN_DURATION_S, MAX_DURATION_S))
              }
            />
          </div>
          <div className="field">
            <label htmlFor="l-seed">Seed</label>
            <input
              id="l-seed"
              className="input"
              type="number"
              min={MIN_SEED}
              max={MAX_SEED}
              style={{ width: 110 }}
              placeholder="scenario"
              value={seedRaw}
              onChange={(e) => setSeedRaw(clampRaw(e.target.value, MIN_SEED, MAX_SEED))}
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
              style={{ width: 90 }}
              placeholder="scenario"
              value={repsRaw}
              onChange={(e) =>
                setRepsRaw(
                  e.target.value === ''
                    ? ''
                    : String(clampInt(Number(e.target.value), 1, MAX_REPLICATES, 1)),
                )
              }
            />
            {plannedReps !== null && plannedReps < MIN_REPLICATES && (
              <span className="hint-amber">below reporting standard n ≥ {MIN_REPLICATES}</span>
            )}
          </div>
          <div className="field">
            <label>&nbsp;</label>
            <button
              className="btn primary"
              onClick={launch}
              disabled={!selected || busy || offline || warmupBlock !== null}
              title={offline ? OFFLINE_WRITE_MESSAGE : (warmupBlock ?? undefined)}
            >
              Launch run
            </button>
          </div>
          <div className="field">
            <label>&nbsp;</label>
            {warmupBlock ? (
              <span className="hint-amber">{warmupBlock}</span>
            ) : (
              <span className="small muted mono">
                {total === null
                  ? 'scenario defaults'
                  : `${plannedReps} × ${(plannedDuration ?? 0) / 60} min = ${describeSimMinutes(total)}`}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="table-wrap">
          <table className="data" aria-label="runs">
            <thead>
              <tr>
                <th>Run</th>
                <th>Scenario</th>
                <th>Tier</th>
                <th>Status</th>
                <th style={{ width: 220 }}>Progress</th>
                <th>Config hash</th>
                <th>Labels</th>
              </tr>
            </thead>
            <tbody>
              {(runs ?? []).map((r) => (
                <tr
                  key={r.run_id}
                  className="rowlink"
                  onClick={() => navigate(`/runs/${r.run_id}`)}
                >
                  <td style={{ fontWeight: 700 }}>{r.run_id}</td>
                  <td className="muted" title={r.scenario_id}>
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
                      <div
                        className="fail-reason small"
                        title={failureReason(r.error, r.error_kind)}
                      >
                        {failureReason(r.error, r.error_kind)}
                      </div>
                    )}
                  </td>
                  <td>
                    {runsDemo ? (
                      // an animated bar would show a worker computing replicates
                      // that no server has ever been asked for
                      <span className="mono small muted">
                        {r.progress.completed_replicates}/{r.progress.total_replicates} — demo
                      </span>
                    ) : (
                      <ProgressBar done={r.progress.completed_replicates} total={r.progress.total_replicates} status={r.status} />
                    )}
                  </td>
                  <td className={runsDemo ? 'hash muted' : 'hash'}>
                    {runsDemo ? DEMO_HASH_LABEL : r.config_hash}
                  </td>
                  <td>
                    <SeededBadge seeded={r.seeded} />
                    {runsDemo && (
                      <span className="tag demo" title={DEMO_ROW_TITLE}>
                        DEMO
                      </span>
                    )}
                  </td>
                </tr>
              ))}
              {runs !== null && runs.length === 0 && (
                <tr>
                  <td colSpan={7}>
                    <div className="empty">no runs yet — launch one above</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

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
