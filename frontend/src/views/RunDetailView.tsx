/** Run detail: the story view of one run (docs/design/DASHBOARD_DESIGN.md
 * §10.4), read top to bottom in the order it was computed: provenance →
 * space-time field → metrics → demand integrity → merge diagnostics.
 *
 * Every panel renders loading → error → empty → content at a stable height.
 * A failed fetch the panel depends on is a persistent danger callout with
 * Retry (the toast only echoes it), never an endless "loading…".
 *
 * A macro (CTM screening) run also states the provenance of its fundamental
 * diagram, which is the calibration its every number rests on: the
 * `FDCalibration` artifact the config named, or the documented *uncalibrated*
 * `v1_legacy` preset (CLAUDE.md §5.1 — FD parameters are calibrated
 * per-corridor inputs, not constants). A service that does not report it
 * leaves the source unknown, and the view says exactly that rather than
 * assuming the preset. */

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { getRun, getRunHeatmap, getRunMetrics, isMockActive } from '../api/client';
import type { HeatField, Heatmap, RunDetail, RunMetrics } from '../api/types';
import { useAppState } from '../components/AppContext';
import { ProgressBar, SeededBadge, StatusChip, TierBadge } from '../components/bits';
import { downloadHeatmapCSV, HeatmapCanvas, isEmptyHeatmap } from '../components/HeatmapCanvas';
import { Icon } from '../components/icons';
import { InsertionPanel } from '../components/InsertionPanel';
import { MergeDiagnosticsPanel } from '../components/MergeDiagnostics';
import { MetricTile, MetricTileSkeleton, replicatePoints } from '../components/metrics';
import { PageHeader } from '../components/PageHeader';
import { toastError } from '../components/toast';
import { Callout } from '../components/ui/Callout';
import { HashValue } from '../components/ui/CopyButton';
import { EmptyState } from '../components/ui/EmptyState';
import { Skeleton } from '../components/ui/Skeleton';
import { DEMO_HASH_LABEL, DEMO_ROW_TITLE } from '../lib/demo';
import { failureReason, formatFetchError } from '../lib/format';
import { useAuthFailed, usePoll } from '../lib/hooks';
import { groupedMetricKeys } from '../lib/metrics';

const FIELDS: HeatField[] = ['speed', 'density'];
const METRIC_SKELETONS = 8;

const FD_PRESET_TITLE =
  'Fundamental diagram: the documented v1_legacy preset — uncalibrated defaults, not a fit to ' +
  'this corridor. Run against an FDCalibration artifact (scenario field fd_calibration) before ' +
  'reading the numbers as this corridor’s.';
const FD_UNKNOWN_TITLE =
  'This service did not report where the fundamental diagram came from, so the calibration ' +
  'behind these numbers is unknown.';

function RetryButton({ onClick }: { onClick: () => void }): JSX.Element {
  return (
    <button type="button" className="btn sm" onClick={onClick}>
      <Icon name="refresh-cw" size={14} />
      Retry
    </button>
  );
}

/** SPEED | DENSITY segmented tabs; Left/Right move and select. */
function FieldTabs({
  field,
  onChange,
}: {
  field: HeatField;
  onChange: (f: HeatField) => void;
}): JSX.Element {
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const i = FIELDS.indexOf(field);
    const next = FIELDS[(i + (e.key === 'ArrowRight' ? 1 : FIELDS.length - 1)) % FIELDS.length];
    onChange(next);
    e.currentTarget.querySelector<HTMLButtonElement>(`#field-tab-${next}`)?.focus();
  };
  return (
    <div className="seg" role="tablist" aria-label="Heatmap field" onKeyDown={onKeyDown}>
      {FIELDS.map((f) => (
        <button
          key={f}
          id={`field-tab-${f}`}
          type="button"
          role="tab"
          aria-selected={field === f}
          aria-controls="field-tabpanel"
          tabIndex={field === f ? 0 : -1}
          className={field === f ? 'active' : ''}
          onClick={() => onChange(f)}
        >
          {f.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

export function RunDetailView(): JSX.Element {
  const { runId = '' } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<RunMetrics | null>(null);
  const [metricsError, setMetricsError] = useState<string | null>(null);
  const [field, setFieldRaw] = useState<HeatField>(
    searchParams.get('field') === 'density' ? 'density' : 'speed',
  );
  const setField = (f: HeatField): void => {
    setFieldRaw(f);
    setSearchParams(f === 'speed' ? {} : { field: f }, { replace: true });
  };
  const [heatmaps, setHeatmaps] = useState<Partial<Record<HeatField, Heatmap>>>({});
  const [heatErrors, setHeatErrors] = useState<Partial<Record<HeatField, string>>>({});
  const [seedsOpen, setSeedsOpen] = useState(false);
  // Whether the row on screen came from the in-browser demo backend, captured
  // at fetch time (the client decides mock vs live per call).
  const [demo, setDemo] = useState(false);
  const { setCorridor } = useAppState();
  const authFailed = useAuthFailed();
  // the run the current responses belong to: a route change to another run
  // reuses this component, and a late answer for the old run must not land
  const currentRun = useRef(runId);
  const heatInFlight = useRef(new Set<HeatField>());
  const metricsInFlight = useRef(false);
  // a failing run poll toasts once per failure streak, not every 2 s
  const runFailing = useRef(false);

  useEffect(() => {
    currentRun.current = runId;
    heatInFlight.current.clear();
    metricsInFlight.current = false;
    runFailing.current = false;
    setRun(null);
    setRunError(null);
    setMetrics(null);
    setMetricsError(null);
    setHeatmaps({});
    setHeatErrors({});
    setSeedsOpen(false);
  }, [runId]);

  const finished = run?.status === 'done';

  const pollRun = useCallback(async () => {
    const fromDemo = isMockActive();
    const id = runId;
    try {
      const r = await getRun(id);
      if (currentRun.current !== id) return;
      runFailing.current = false;
      setRun(r);
      setRunError(null);
      setDemo(fromDemo);
      if (r.scenario_name) setCorridor(r.scenario_name.split('·')[0].trim());
    } catch (err) {
      if (currentRun.current !== id) return;
      if (!runFailing.current) toastError(err, id);
      runFailing.current = true;
      setRunError(formatFetchError(err));
    }
  }, [runId, setCorridor]);

  // poll while pending; stop once terminal — or while the key is rejected
  usePoll(
    pollRun,
    authFailed || (run && (run.status === 'done' || run.status === 'failed')) ? null : 2000,
  );

  useEffect(() => {
    if (!finished || metrics || metricsError || metricsInFlight.current) return;
    const id = runId;
    metricsInFlight.current = true;
    getRunMetrics(id)
      .then((m) => {
        if (currentRun.current === id) setMetrics(m);
      })
      .catch((err) => {
        if (currentRun.current !== id) return;
        toastError(err, 'metrics');
        setMetricsError(formatFetchError(err));
      })
      .finally(() => {
        if (currentRun.current === id) metricsInFlight.current = false;
      });
  }, [finished, metrics, metricsError, runId]);

  useEffect(() => {
    if (!finished || heatmaps[field] || heatErrors[field] || heatInFlight.current.has(field)) return;
    const id = runId;
    const f = field;
    heatInFlight.current.add(f);
    getRunHeatmap(id, f)
      .then((h) => {
        if (currentRun.current === id) setHeatmaps((m) => ({ ...m, [f]: h }));
      })
      .catch((err) => {
        if (currentRun.current !== id) return;
        toastError(err, 'heatmap');
        setHeatErrors((e) => ({ ...e, [f]: formatFetchError(err) }));
      })
      .finally(() => {
        if (currentRun.current === id) heatInFlight.current.delete(f);
      });
  }, [finished, field, heatmaps, heatErrors, runId]);

  const retryHeatmap = (): void => setHeatErrors((e) => ({ ...e, [field]: undefined }));

  const sections = useMemo(
    () => (metrics ? groupedMetricKeys(Object.keys(metrics.aggregate)) : []),
    [metrics],
  );

  const heatmap = heatmaps[field];
  const heatError = heatErrors[field];
  const heatEmpty = heatmap ? isEmptyHeatmap(heatmap) : false;
  // the preset is uncalibrated by definition, so it is flagged rather than
  // printed like a corridor's fitted diagram
  const fdSource = run?.tier === 'macro' ? (metrics?.fd_source ?? null) : null;
  const fdIsPreset = fdSource === 'v1_legacy preset';
  const nReplicates = metrics ? (metrics.n_replicates ?? metrics.replicates.length) : null;

  return (
    <div className="view run-detail">
      <PageHeader
        title={<span className="run-title">{runId}</span>}
        documentTitle={runId}
        meta={
          run && (
            <>
              <StatusChip status={run.status} />
              <TierBadge tier={run.tier} />
              <SeededBadge seeded={run.seeded} />
              {demo && (
                <span className="tag demo" title={DEMO_ROW_TITLE}>
                  DEMO
                </span>
              )}
            </>
          )
        }
      />

      {run ? (
        <dl className="run-facts">
          <div className="run-fact">
            <dt>Scenario</dt>
            <dd title={run.scenario_id}>{run.scenario_name ?? run.scenario_id}</dd>
          </div>
          <div className="run-fact">
            <dt>Config</dt>
            <dd>
              {demo ? (
                <span className="mono" title={DEMO_ROW_TITLE}>
                  {DEMO_HASH_LABEL}
                </span>
              ) : (
                <HashValue value={run.config_hash} label="config hash" />
              )}
            </dd>
          </div>
          <div className="run-fact">
            <dt>Seeds</dt>
            <dd>
              <button
                type="button"
                className="btn ghost sm seeds-toggle"
                aria-expanded={seedsOpen}
                aria-controls="run-seeds"
                aria-label={`Seeds: ${run.seeds.length}`}
                title="RNG seeds of the replicate set — full reproducibility"
                onClick={() => setSeedsOpen((o) => !o)}
              >
                <span className="mono">{run.seeds.length}</span>
                <Icon name={seedsOpen ? 'chevron-down' : 'chevron-right'} size={14} />
              </button>
            </dd>
          </div>
          {run.tier === 'macro' && metrics && (
            <div className="run-fact">
              <dt>FD</dt>
              <dd title={fdIsPreset ? FD_PRESET_TITLE : fdSource ? `Fundamental diagram fitted in the FDCalibration artifact ${fdSource}` : FD_UNKNOWN_TITLE}>
                <span className={fdIsPreset || !fdSource ? 'hint-amber' : 'mono'}>
                  {fdSource ?? 'source unknown'}
                  {fdIsPreset ? ' (uncalibrated)' : ''}
                </span>
              </dd>
            </div>
          )}
        </dl>
      ) : runError ? (
        <Callout
          tone="danger"
          title="This run could not be loaded."
          action={<RetryButton onClick={() => void pollRun()} />}
        >
          <span className="mono">{runError}</span>
        </Callout>
      ) : (
        <div className="run-facts" aria-busy="true">
          <Skeleton width={180} height={14} />
          <Skeleton width={160} height={14} />
          <Skeleton width={80} height={14} />
        </div>
      )}

      {run && seedsOpen && (
        <div id="run-seeds" className="run-seeds mono">
          {run.seeds.join(' · ')}
        </div>
      )}

      {run && (run.status === 'queued' || run.status === 'running') && (
        <section className="panel">
          <div className="panel-head">
            <h2 className="panel-title">Computing replicates</h2>
          </div>
          <div className="panel-body run-progress">
            {demo ? (
              // no worker is computing these replicates, so nothing moves
              <span className="mono small muted">
                {run.progress.completed_replicates}/{run.progress.total_replicates} — demo
              </span>
            ) : (
              <ProgressBar
                done={run.progress.completed_replicates}
                total={run.progress.total_replicates}
                status={run.status}
              />
            )}
            <p className="small muted">Heatmap and metrics appear when all replicates finish.</p>
          </div>
        </section>
      )}

      {run && run.status === 'failed' && (
        // the API already answers *why* (RunOut.error): showing only "run
        // failed 2/2" sends the user to the server logs for a reason the
        // dashboard was holding all along
        <Callout tone="danger" title="Run failed">
          <p className="run-failed-lead">
            No replicate produced results ({run.progress.completed_replicates}/
            {run.progress.total_replicates} replicates finished). The service reported:
          </p>
          <pre className="run-failed-reason mono">{failureReason(run.error, run.error_kind)}</pre>
        </Callout>
      )}

      {finished && (
        <section className="panel field-panel">
          <div className="panel-head">
            <h2 className="panel-title">Space–time field</h2>
            <FieldTabs field={field} onChange={setField} />
            <span className="spacer" />
            <button
              type="button"
              className="btn ghost sm"
              disabled={!heatmap || heatEmpty}
              title={
                heatmap && !heatEmpty
                  ? `The binned ${field} field in SI units, as plotted: one replicate` +
                    `${heatmap.seed === undefined ? '' : ` (seed ${heatmap.seed})`}, ` +
                    'headed by its provenance'
                  : 'Available once the field has loaded'
              }
              onClick={() => heatmap && downloadHeatmapCSV(heatmap, field, { runId, run, demo })}
            >
              <Icon name="download" size={14} />
              Download CSV
            </button>
          </div>
          <div
            className="panel-body"
            id="field-tabpanel"
            role="tabpanel"
            aria-labelledby={`field-tab-${field}`}
          >
            {heatError ? (
              <div className="heatmap-state">
                <Callout
                  tone="danger"
                  title={`The ${field} field could not be loaded.`}
                  action={<RetryButton onClick={retryHeatmap} />}
                >
                  <span className="mono">{heatError}</span>
                </Callout>
              </div>
            ) : !heatmap ? (
              <div className="heatmap-state" aria-busy="true">
                <div className="skeleton-frame">
                  <Skeleton radius="xs" />
                  <span className="skeleton-label">Loading the {field} field…</span>
                </div>
              </div>
            ) : heatEmpty ? (
              <div className="heatmap-state">
                <EmptyState
                  title="This field has no bins."
                  description="The service returned 0 time or position bins for this run."
                />
              </div>
            ) : (
              <HeatmapCanvas heatmap={heatmap} field={field} />
            )}
          </div>
        </section>
      )}

      {finished && (
        <section className="panel">
          <div className="panel-head">
            <h2 className="panel-title">
              Metrics · mean ± 95% CI
              {nReplicates !== null ? ` over ${nReplicates} replicates` : ''}
            </h2>
          </div>
          <div className="panel-body">
            {metricsError ? (
              <Callout
                tone="danger"
                title="The metrics could not be loaded."
                action={<RetryButton onClick={() => setMetricsError(null)} />}
              >
                <span className="mono">{metricsError}</span>
              </Callout>
            ) : !metrics ? (
              <div className="metric-grid" aria-busy="true">
                {Array.from({ length: METRIC_SKELETONS }, (_, i) => (
                  <MetricTileSkeleton key={i} />
                ))}
              </div>
            ) : sections.length === 0 ? (
              <EmptyState
                title="No metrics for this run."
                description="The service returned no aggregate metrics."
              />
            ) : (
              <div className="metric-groups">
                {sections.map((s) => (
                  <section key={s.group} className="metric-group" aria-labelledby={`mg-${s.group}`}>
                    <h4 id={`mg-${s.group}`}>{s.title}</h4>
                    <div className="metric-grid">
                      {s.keys.map((k) => (
                        <MetricTile
                          key={k}
                          metricKey={k}
                          stat={metrics.aggregate[k]}
                          replicates={replicatePoints(metrics.replicates, k)}
                        />
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      {finished && metrics && (
        <InsertionPanel insertion={metrics.insertion} weaveExits={metrics.weave_exits} />
      )}
      {finished && metrics && <MergeDiagnosticsPanel diagnostics={metrics.merge_diagnostics} />}
    </div>
  );
}
