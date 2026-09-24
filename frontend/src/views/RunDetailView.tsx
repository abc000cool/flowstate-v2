/** Run detail: the space-time heatmap centerpiece with a speed/density
 * toggle, then aggregate metric cards with CIs and per-replicate strips.
 *
 * A macro (CTM screening) run also states the provenance of its fundamental
 * diagram, which is the calibration its every number rests on: the
 * `FDCalibration` artifact the config named, or the documented *uncalibrated*
 * `v1_legacy` preset (CLAUDE.md §5.1 — FD parameters are calibrated
 * per-corridor inputs, not constants). A service that does not report it
 * leaves the source unknown, and the view says exactly that rather than
 * assuming the preset. */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { getRun, getRunHeatmap, getRunMetrics, isMockActive } from '../api/client';
import type { HeatField, Heatmap, RunDetail, RunMetrics } from '../api/types';
import { useAppState } from '../components/AppContext';
import {
  ProgressBar,
  SeededBadge,
  StatusChip,
  StripChart,
  TierBadge,
  MetricCard,
} from '../components/bits';
import { HeatmapCanvas, RampLegend } from '../components/HeatmapCanvas';
import { InsertionPanel } from '../components/InsertionPanel';
import { MergeDiagnosticsPanel } from '../components/MergeDiagnostics';
import { toastError } from '../components/toast';
import { DEMO_HASH_LABEL, DEMO_ROW_TITLE } from '../lib/demo';
import { failureReason } from '../lib/format';
import { useAuthFailed, usePoll } from '../lib/hooks';
import { hasNoObservations, orderedMetricKeys } from '../lib/metrics';

export function RunDetailView(): JSX.Element {
  const { runId = '' } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [metrics, setMetrics] = useState<RunMetrics | null>(null);
  const [field, setFieldRaw] = useState<HeatField>(
    searchParams.get('field') === 'density' ? 'density' : 'speed',
  );
  const setField = (f: HeatField): void => {
    setFieldRaw(f);
    setSearchParams(f === 'speed' ? {} : { field: f }, { replace: true });
  };
  const [heatmaps, setHeatmaps] = useState<Partial<Record<HeatField, Heatmap>>>({});
  const [seedsOpen, setSeedsOpen] = useState(false);
  // Whether the row on screen came from the in-browser demo backend, captured
  // at fetch time (the client decides mock vs live per call).
  const [demo, setDemo] = useState(false);
  const { setCorridor } = useAppState();
  const authFailed = useAuthFailed();

  const finished = run?.status === 'done';

  const pollRun = useCallback(async () => {
    const fromDemo = isMockActive();
    try {
      const r = await getRun(runId);
      setRun(r);
      setDemo(fromDemo);
      if (r.scenario_name) setCorridor(r.scenario_name.split('·')[0].trim());
    } catch (err) {
      toastError(err, runId);
    }
  }, [runId, setCorridor]);

  // poll while pending; stop once terminal — or while the key is rejected
  usePoll(
    pollRun,
    authFailed || (run && (run.status === 'done' || run.status === 'failed')) ? null : 2000,
  );

  useEffect(() => {
    if (!finished || metrics) return;
    getRunMetrics(runId)
      .then(setMetrics)
      .catch((err) => toastError(err, 'metrics'));
  }, [finished, metrics, runId]);

  useEffect(() => {
    if (!finished || heatmaps[field]) return;
    getRunHeatmap(runId, field)
      .then((h) => setHeatmaps((m) => ({ ...m, [field]: h })))
      .catch((err) => toastError(err, 'heatmap'));
  }, [finished, field, heatmaps, runId]);

  const metricKeys = useMemo(
    () => (metrics ? orderedMetricKeys(Object.keys(metrics.aggregate)) : []),
    [metrics],
  );
  // a metric no replicate produced has nothing to distribute: the card says
  // "no observations", and a strip drawn from an empty value set would put a
  // mean line at 0 that is not a measurement
  const stripKeys = useMemo(
    () => (metrics ? metricKeys.filter((k) => !hasNoObservations(metrics.aggregate[k])) : []),
    [metrics, metricKeys],
  );

  const heatmap = heatmaps[field];
  // the preset is uncalibrated by definition, so it is flagged rather than
  // printed like a corridor's fitted diagram
  const fdSource = run?.tier === 'macro' ? (metrics?.fd_source ?? null) : null;
  const fdIsPreset = fdSource === 'v1_legacy preset';

  return (
    <div className="view">
      <div className="view-title">
        <Link to="/runs" className="mono">
          ← runs
        </Link>
      </div>

      {run && (
        <div className="run-head">
          <span className="run-id mono">{run.run_id}</span>
          <StatusChip status={run.status} />
          <TierBadge tier={run.tier} />
          <SeededBadge seeded={run.seeded} />
          {demo && (
            <span className="tag demo" title={DEMO_ROW_TITLE}>
              DEMO
            </span>
          )}
          <span className="kv">
            scenario <b>{run.scenario_name ?? run.scenario_id}</b>
          </span>
          <span className="kv">
            config{' '}
            {demo ? (
              <b className="hash muted">{DEMO_HASH_LABEL}</b>
            ) : (
              <b className="hash">{run.config_hash}</b>
            )}
          </span>
          {run.tier === 'macro' && metrics && (
            <span
              className="kv"
              title={
                fdIsPreset
                  ? 'Fundamental diagram: the documented v1_legacy preset — uncalibrated ' +
                    'defaults, not a fit to this corridor. Run against an FDCalibration ' +
                    'artifact (scenario field fd_calibration) before reading the numbers as ' +
                    'this corridor’s.'
                  : fdSource
                    ? `Fundamental diagram fitted in the FDCalibration artifact ${fdSource}`
                    : 'This service did not report where the fundamental diagram came from, ' +
                      'so the calibration behind these numbers is unknown.'
              }
            >
              FD{' '}
              <b className={fdIsPreset || !fdSource ? 'hint-amber' : undefined}>
                {fdSource ?? 'source unknown'}
                {fdIsPreset ? ' (uncalibrated)' : ''}
              </b>
            </span>
          )}
          <span className="kv">
            seeds{' '}
            <b>
              <button
                className="btn sm"
                onClick={() => setSeedsOpen((o) => !o)}
                title="RNG seeds of the replicate set — full reproducibility"
              >
                {run.seeds.length} seeds {seedsOpen ? '▾' : '▸'}
              </button>
            </b>
          </span>
        </div>
      )}

      {run && seedsOpen && (
        <div className="panel">
          <div className="panel-body mono small muted" style={{ wordBreak: 'break-all' }}>
            {run.seeds.join(' · ')}
          </div>
        </div>
      )}

      {run && !finished && (
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">
              {run.status === 'failed' ? 'Run failed' : 'Computing replicates'}
            </span>
          </div>
          <div className="panel-body">
            {demo ? (
              // no worker is computing these replicates, so nothing moves
              <span className="mono small muted">
                {run.progress.completed_replicates}/{run.progress.total_replicates} — demo
              </span>
            ) : (
              <ProgressBar done={run.progress.completed_replicates} total={run.progress.total_replicates} status={run.status} />
            )}
            {run.status === 'failed' ? (
              // the API already answers *why* (RunOut.error): showing only
              // "run failed 2/2" sends the user to the server logs for a
              // reason the dashboard was holding all along
              <>
                <p className="small muted" style={{ marginTop: 12 }}>
                  No replicate produced results. The service reported:
                </p>
                <pre className="fail-reason mono small">
                  {failureReason(run.error, run.error_kind)}
                </pre>
              </>
            ) : (
              <p className="small muted" style={{ marginTop: 12 }}>
                Heatmap and metrics appear when all replicates finish.
              </p>
            )}
          </div>
        </div>
      )}

      {finished && (
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">Space–time field</span>
            <div className="seg" role="tablist" aria-label="Heatmap field">
              {(['speed', 'density'] as HeatField[]).map((f) => (
                <button
                  key={f}
                  role="tab"
                  aria-selected={field === f}
                  className={field === f ? 'active' : ''}
                  onClick={() => setField(f)}
                >
                  {f.toUpperCase()}
                </button>
              ))}
            </div>
            <span className="spacer" />
            <RampLegend field={field} />
          </div>
          <div className="panel-body">
            {heatmap ? (
              <HeatmapCanvas heatmap={heatmap} field={field} />
            ) : (
              <div className="empty">loading field…</div>
            )}
          </div>
        </div>
      )}

      {finished && metrics && (
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">Aggregate metrics · mean ± 95% CI</span>
          </div>
          <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            <div className="metric-grid">
              {metricKeys.map((k) => (
                <MetricCard key={k} metricKey={k} stat={metrics.aggregate[k]} />
              ))}
            </div>
            <div>
              <div className="panel-title" style={{ marginBottom: 10 }}>
                Per-replicate distribution
              </div>
              <div className="strip-row">
                {stripKeys.map((k) => (
                  <StripChart
                    key={k}
                    metricKey={k}
                    values={metrics.replicates
                      .map((r) => r.metrics[k])
                      .filter((v): v is number => typeof v === 'number')}
                    mean={metrics.aggregate[k].mean ?? 0}
                  />
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {finished && metrics && (
        <InsertionPanel insertion={metrics.insertion} weaveExits={metrics.weave_exits} />
      )}
      {finished && metrics && <MergeDiagnosticsPanel diagnostics={metrics.merge_diagnostics} />}
    </div>
  );
}
