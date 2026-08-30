/** Runs: mono table with status chips, live progress bars (2 s poll),
 * config hashes, SEEDED and tier badges; plus a compact launcher. */

import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createRun, listRuns, listScenarios } from '../api/client';
import type { RunSummary, ScenarioSummary } from '../api/types';
import { ProgressBar, SeededBadge, StatusChip, TierBadge } from '../components/bits';
import { toast, toastError } from '../components/toast';
import { usePoll } from '../lib/hooks';
import { MIN_REPLICATES } from '../lib/metrics';

export function RunsView(): JSX.Element {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [launchScenario, setLaunchScenario] = useState('');
  const [launchTier, setLaunchTier] = useState<'micro' | 'macro'>('micro');
  const [launchReps, setLaunchReps] = useState(20);
  const navigate = useNavigate();

  const poll = useCallback(async () => {
    try {
      setRuns(await listRuns());
    } catch (err) {
      // toast once per failure burst would spam at 2 s cadence; stay quiet,
      // the rail status dot + banner already surface connectivity.
      void err;
    }
  }, []);
  usePoll(poll, 2000);

  // quiet retry until the scenario list loads (covers the offline-fallback
  // race where the first fetch fires before the health probe flips to demo)
  const scenariosLoaded = scenarios.length > 0;
  const loadScenarios = useCallback(async () => {
    try {
      const s = await listScenarios();
      setScenarios(s);
      if (s.length > 0) setLaunchScenario((cur) => cur || s[0].scenario_id);
    } catch {
      /* retried by usePoll; connectivity is surfaced by the status dot */
    }
  }, []);
  usePoll(loadScenarios, scenariosLoaded ? null : 3000);

  const launch = async (): Promise<void> => {
    if (!launchScenario) return;
    try {
      const res = await createRun({
        scenario_id: launchScenario,
        replicates: launchReps,
        tier: launchTier,
      });
      toast('ok', `run ${res.run_id} queued`);
      await poll();
    } catch (err) {
      toastError(err, 'launch');
    }
  };

  return (
    <div className="view">
      <div className="view-title">
        Run Operations{' '}
        <span className="count mono">{runs ? `${runs.length} runs · 2 s poll` : 'loading…'}</span>
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
            <label htmlFor="l-reps">Replicates</label>
            <input
              id="l-reps"
              className="input"
              type="number"
              min={1}
              style={{ width: 90 }}
              value={launchReps}
              onChange={(e) => setLaunchReps(Number(e.target.value))}
            />
            {launchReps < MIN_REPLICATES && (
              <span className="hint-amber">below reporting standard n ≥ {MIN_REPLICATES}</span>
            )}
          </div>
          <div className="field">
            <label>&nbsp;</label>
            <button className="btn primary" onClick={() => void launch()} disabled={!launchScenario}>
              Launch run
            </button>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="table-wrap">
          <table className="data">
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
                  <td className="muted">{r.scenario_name ?? r.scenario_id}</td>
                  <td>
                    <TierBadge tier={r.tier} />
                  </td>
                  <td>
                    <StatusChip status={r.status} />
                  </td>
                  <td>
                    <ProgressBar done={r.progress.done} total={r.progress.total} status={r.status} />
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
    </div>
  );
}
