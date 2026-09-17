/** Runs: mono table with status chips, live progress bars (2 s poll),
 * config hashes, SEEDED and tier badges; plus a launcher that states what it
 * is about to enqueue.
 *
 * The launcher sends `replicates` plus an `overrides` patch (`sim.duration_s`,
 * `seed`) — the API deep-merges it onto the stored config and re-hashes, so a
 * shortened smoke run is a first-class, hash-distinct run rather than an
 * unlabelled variant. Large launches (replicates × duration past
 * `CONFIRM_SIM_MINUTES`) take an explicit second click. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createRun, listRuns, listScenarios } from '../api/client';
import type { CreateRunRequest, RunSummary, ScenarioSummary } from '../api/types';
import { ProgressBar, SeededBadge, StatusChip, TierBadge } from '../components/bits';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { toast, toastError } from '../components/toast';
import { useAuthFailed, usePoll } from '../lib/hooks';
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
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [launchScenario, setLaunchScenario] = useState('');
  const [launchTier, setLaunchTier] = useState<'micro' | 'macro'>('micro');
  const [repsRaw, setRepsRaw] = useState('');
  const [durationRaw, setDurationRaw] = useState('');
  const [seedRaw, setSeedRaw] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const authFailed = useAuthFailed();

  const poll = useCallback(async () => {
    try {
      setRuns(await listRuns());
    } catch (err) {
      // toast once per failure burst would spam at 2 s cadence; stay quiet,
      // the rail status dot + banner already surface connectivity (and a
      // rejected key pauses this poll entirely).
      void err;
    }
  }, []);
  usePoll(poll, authFailed ? null : RUNS_POLL_MS);

  // quiet retry until the scenario list loads (covers the offline-fallback
  // race where the first fetch fires before the health probe flips to demo),
  // then a slow refresh so newly created scenarios and their names appear
  const scenariosLoaded = scenarios.length > 0;
  const loadScenarios = useCallback(async () => {
    try {
      setScenarios(await listScenarios());
    } catch {
      /* retried by usePoll; connectivity is surfaced by the status dot */
    }
  }, []);
  usePoll(
    loadScenarios,
    authFailed ? null : scenariosLoaded ? SCENARIOS_IDLE_POLL_MS : SCENARIOS_POLL_MS,
  );

  useEffect(() => {
    if (!launchScenario && scenarios.length > 0) setLaunchScenario(scenarios[0].scenario_id);
  }, [scenarios, launchScenario]);

  const selected = scenarios.find((s) => s.scenario_id === launchScenario);
  const base = selected?.config;

  // Show the scenario's own values as the starting point, once per selected
  // config: the scenario poll hands back fresh objects every tick, so keying
  // this on the config identity would wipe whatever the user typed.
  const prefilledFor = useRef<string | null>(null);
  const selectedKey = selected ? `${selected.scenario_id}:${selected.config_hash}` : '';
  useEffect(() => {
    if (prefilledFor.current === selectedKey) return;
    prefilledFor.current = selectedKey;
    setRepsRaw(base ? String(base.replicates) : '');
    setDurationRaw(base ? String(base.sim.duration_s) : '');
    setSeedRaw(base ? String(base.seed) : '');
  }, [selectedKey, base]);

  /** Scenario id → name, so the table names the corridor rather than an id
   * (the API's RunOut carries no scenario name). */
  const scenarioNames = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of scenarios) m.set(s.scenario_id, s.name);
    return m;
  }, [scenarios]);

  const plannedReps = numOr(repsRaw, base?.replicates ?? null);
  const plannedDuration = numOr(durationRaw, base?.sim.duration_s ?? null);
  const plannedSeed = numOr(seedRaw, base?.seed ?? null);
  const total =
    plannedReps !== null && plannedDuration !== null
      ? simMinutes(plannedReps, plannedDuration)
      : null;

  const buildRequest = (): CreateRunRequest => {
    const overrides: Record<string, unknown> = {};
    if (plannedDuration !== null && plannedDuration !== base?.sim.duration_s) {
      overrides.sim = { duration_s: plannedDuration };
    }
    if (plannedSeed !== null && plannedSeed !== base?.seed) overrides.seed = plannedSeed;
    const req: CreateRunRequest = { scenario_id: launchScenario, tier: launchTier };
    if (plannedReps !== null) req.replicates = plannedReps;
    if (Object.keys(overrides).length > 0) req.overrides = overrides;
    return req;
  };

  const doLaunch = async (): Promise<void> => {
    if (!launchScenario) return;
    setBusy(true);
    try {
      const res = await createRun(buildRequest());
      toast('ok', `run ${res.run_id} queued`);
      setConfirming(false);
      await poll();
    } catch (err) {
      toastError(err, 'launch');
    } finally {
      setBusy(false);
    }
  };

  const launch = (): void => {
    if (!launchScenario) return;
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
      </div>

      <div className="panel">
        <div className="panel-body row wrap">
          <div className="field">
            <label htmlFor="l-scn">Scenario</label>
            <select
              id="l-scn"
              className="input"
              value={launchScenario}
              onChange={(e) => setLaunchScenario(e.target.value)}
              style={{ minWidth: 220 }}
            >
              {scenarios.map((s) => (
                <option key={s.scenario_id} value={s.scenario_id}>
                  {s.name}
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
            <button className="btn primary" onClick={launch} disabled={!launchScenario || busy}>
              Launch run
            </button>
          </div>
          <div className="field">
            <label>&nbsp;</label>
            <span className="small muted mono">
              {total === null
                ? 'scenario defaults'
                : `${plannedReps} × ${(plannedDuration ?? 0) / 60} min = ${describeSimMinutes(total)}`}
            </span>
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
                  </td>
                  <td>
                    <ProgressBar done={r.progress.completed_replicates} total={r.progress.total_replicates} status={r.status} />
                  </td>
                  <td className="hash">{r.config_hash}</td>
                  <td>
                    <SeededBadge seeded={r.seeded} />
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
            ['Scenario', selected?.name ?? launchScenario],
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
