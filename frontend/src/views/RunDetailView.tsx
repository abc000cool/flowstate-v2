/** Run detail: the space-time heatmap centerpiece with a speed/density
 * toggle, then aggregate metric cards with CIs and per-replicate strips. */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { getRun, getRunHeatmap, getRunMetrics } from '../api/client';
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
import { toastError } from '../components/toast';
import { usePoll } from '../lib/hooks';
import { orderedMetricKeys } from '../lib/metrics';

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
  const { setCorridor } = useAppState();

  const finished = run?.status === 'done';

  const pollRun = useCallback(async () => {
    try {
      const r = await getRun(runId);
      setRun(r);
      if (r.scenario_name) setCorridor(r.scenario_name.split('·')[0].trim());
    } catch (err) {
      toastError(err, runId);
    }
  }, [runId, setCorridor]);

  // poll while pending; stop once terminal
  usePoll(pollRun, run && (run.status === 'done' || run.status === 'failed') ? null : 2000);

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

  const heatmap = heatmaps[field];

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
          <span className="kv">
            scenario <b>{run.scenario_name ?? run.scenario_id}</b>
          </span>
          <span className="kv">
            config <b className="hash">{run.config_hash}</b>
          </span>
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
            <ProgressBar done={run.progress.completed_replicates} total={run.progress.total_replicates} status={run.status} />
            {run.status !== 'failed' && (
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
                {metricKeys.map((k) => (
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
    </div>
  );
}
