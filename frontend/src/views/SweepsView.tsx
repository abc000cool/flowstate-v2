/** Sweeps: penetration × compliance grid launcher and a result matrix
 * coloured by metric delta vs the sweep's baseline cell (p=0, no controlled
 * vehicles), CI on hover, click-through to the cell's run detail. */

import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { createSweep, getSweep, listScenarios } from '../api/client';
import type { ScenarioSummary, SweepCell, SweepDetail } from '../api/types';
import { toast, toastError } from '../components/toast';
import { deltaColor } from '../lib/colormap';
import { formatDeltaPct, formatNumber } from '../lib/format';
import { usePoll } from '../lib/hooks';
import { DEFAULT_SWEEP_METRIC, METRIC_DEFS, metricDef, MIN_REPLICATES } from '../lib/metrics';

const PEN_CHOICES = [0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3];
const COM_CHOICES = [0.25, 0.5, 0.8, 1.0];
const CONTROLLERS = ['follower_stopper', 'pi_saturation', 'jad'];
const SWEEP_METRICS = METRIC_DEFS.filter((d) => d.good !== 'neutral');
const SWEEP_POLL_MS = 2500;

interface Tip {
  x: number;
  y: number;
  cell: SweepCell;
}

/** A p=0 cell is the sweep's baseline: with no controlled vehicles the
 * controller cannot act, so the cell is the uncontrolled reference. */
const isBaseline = (c: SweepCell): boolean => c.penetration === 0;

const pct = (v: number): string => `${Math.round(v * 100)}%`;

interface MatrixLayout {
  rows: number[];
  cols: number[];
  /** Every p=0 cell (one per compliance when the request listed p=0). */
  baselines: SweepCell[];
  /** The delta reference: the first baseline, or — when the sweep has no
   * p=0 cell — the lowest p·c grid cell, flagged as such in the header. */
  reference: SweepCell;
  hasBaseline: boolean;
  at: (p: number, c: number) => SweepCell | undefined;
}

function layoutMatrix(sweep: SweepDetail): MatrixLayout | null {
  if (sweep.cells.length === 0) return null;
  const grid = sweep.cells.filter((c) => !isBaseline(c));
  const rows = [...new Set(grid.map((c) => c.penetration))].sort((a, b) => a - b);
  const cols = [...new Set(grid.map((c) => c.compliance))].sort((a, b) => a - b);
  const baselines = sweep.cells
    .filter(isBaseline)
    .sort((a, b) => (a.config_hash ?? '').localeCompare(b.config_hash ?? ''));
  const lowest = [...grid].sort(
    (a, b) => a.penetration * a.compliance - b.penetration * b.compliance,
  )[0];
  const reference = baselines[0] ?? lowest ?? sweep.cells[0];
  const at = (p: number, c: number): SweepCell | undefined =>
    grid.find((x) => x.penetration === p && x.compliance === c);
  return { rows, cols, baselines, reference, hasBaseline: baselines.length > 0, at };
}

export function SweepsView(): JSX.Element {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [scenarioId, setScenarioId] = useState('');
  const [pens, setPens] = useState<number[]>([0.01, 0.02, 0.05, 0.1, 0.15, 0.2]);
  const [coms, setComs] = useState<number[]>([0.25, 0.5, 0.8, 1.0]);
  const [controller, setController] = useState(CONTROLLERS[0]);
  const [replicates, setReplicates] = useState(20);
  const [includeBaseline, setIncludeBaseline] = useState(true);
  const [searchParams, setSearchParams] = useSearchParams();
  const [sweepId, setSweepId] = useState<string | null>(searchParams.get('sweep'));
  const [sweep, setSweep] = useState<SweepDetail | null>(null);
  const [metricKey, setMetricKey] = useState(DEFAULT_SWEEP_METRIC);
  const [tip, setTip] = useState<Tip | null>(null);
  const navigate = useNavigate();

  // quiet retry until the scenario list loads (offline-fallback race)
  const scenariosLoaded = scenarios.length > 0;
  const loadScenarios = useCallback(async () => {
    try {
      const s = await listScenarios();
      setScenarios(s);
      const corridor = s.find((x) => x.config?.network.kind === 'corridor') ?? s[0];
      if (corridor) setScenarioId((cur) => cur || corridor.scenario_id);
    } catch {
      /* retried by usePoll */
    }
  }, []);
  usePoll(loadScenarios, scenariosLoaded ? null : 3000);

  // `sweep.status` is the fan-out job's own status (`done` = every child run
  // exists; cells still finish on their own), so polling stops on the cells —
  // except when the fan-out `failed`: cells without a run then never get one.
  const settled =
    sweep !== null &&
    (sweep.status === 'failed' ||
      sweep.cells.every((c) => c.status === 'done' || c.status === 'failed'));
  const pollSweep = useCallback(async () => {
    if (!sweepId) return;
    try {
      setSweep(await getSweep(sweepId));
    } catch (err) {
      toastError(err, 'sweep');
    }
  }, [sweepId]);
  usePoll(pollSweep, sweepId && !settled ? SWEEP_POLL_MS : null);

  const launch = async (): Promise<void> => {
    if (!scenarioId || pens.length === 0 || coms.length === 0) {
      toast('error', 'pick a scenario plus at least one penetration and compliance');
      return;
    }
    try {
      // the API contract is the plural `controllers` list (SweepCreateRequest)
      const res = await createSweep({
        scenario_id: scenarioId,
        penetrations: [...pens].sort((a, b) => a - b),
        compliances: [...coms].sort((a, b) => a - b),
        controllers: [controller],
        replicates,
        include_baseline: includeBaseline,
      });
      setSweep(null);
      setSweepId(res.sweep_id);
      setSearchParams({ sweep: res.sweep_id }, { replace: true });
      toast(
        'ok',
        `sweep ${res.sweep_id} launched · ${pens.length * coms.length} cells${includeBaseline ? ' + baseline' : ''}`,
      );
    } catch (err) {
      toastError(err, 'sweep');
    }
  };

  const toggle = (list: number[], v: number, set: (l: number[]) => void): void => {
    set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v].sort((a, b) => a - b));
  };

  /* matrix layout */
  const matrix = useMemo(() => (sweep ? layoutMatrix(sweep) : null), [sweep]);

  const def = metricDef(metricKey);
  const baseStat = matrix?.reference.aggregate?.[metricKey];

  const cellDelta = (cell: SweepCell): number | null => {
    const stat = cell.aggregate?.[metricKey];
    if (!stat || !baseStat || stat.mean === null || baseStat.mean === null) return null;
    if (baseStat.mean === 0) return null;
    return (stat.mean - baseStat.mean) / Math.abs(baseStat.mean);
  };

  const goodness = (delta: number): number => {
    const signed = def.good === 'down' ? -delta : delta;
    return Math.max(-1, Math.min(1, signed / 0.5)); // ±50% saturates
  };

  const openCell = (cell: SweepCell): void => {
    if (cell.run_id) navigate(`/runs/${cell.run_id}`);
  };

  /** Baseline cells show the absolute value (their delta is 0 by definition). */
  const renderBaseline = (cell: SweepCell, key: string | number, colSpan: number): JSX.Element => {
    if (cell.status !== 'done' || !cell.aggregate) {
      return (
        <td key={key} colSpan={colSpan} className="cell pending">
          {cell.status ?? 'queued'}
        </td>
      );
    }
    const stat = cell.aggregate[metricKey];
    return (
      <td
        key={key}
        colSpan={colSpan}
        className="cell baseline"
        title="p=0: no controlled vehicles — the uncontrolled reference every delta is measured against"
        onClick={() => openCell(cell)}
        onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, cell })}
        onMouseLeave={() => setTip(null)}
      >
        <div className="d">{stat ? `${formatNumber(stat.mean, def.digits)} ${def.unit}` : '·'}</div>
        <div className="n">BASELINE · n={stat?.n ?? '—'}</div>
      </td>
    );
  };

  return (
    <div className="view">
      <div className="view-title">
        Parameter Sweeps <span className="count mono">penetration × compliance</span>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Launch sweep</span>
        </div>
        <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="row wrap">
            <div className="field">
              <label htmlFor="s-scn">Scenario</label>
              <select
                id="s-scn"
                className="input"
                style={{ minWidth: 200 }}
                value={scenarioId}
                onChange={(e) => setScenarioId(e.target.value)}
              >
                {scenarios.map((s) => (
                  <option key={s.scenario_id} value={s.scenario_id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="s-ctrl">Controller</label>
              <select
                id="s-ctrl"
                className="input"
                value={controller}
                onChange={(e) => setController(e.target.value)}
              >
                {CONTROLLERS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="s-reps">Replicates / cell</label>
              <input
                id="s-reps"
                className="input"
                type="number"
                min={1}
                style={{ width: 90 }}
                value={replicates}
                onChange={(e) => setReplicates(Number(e.target.value))}
              />
              {replicates < MIN_REPLICATES && (
                <span className="hint-amber">below reporting standard n ≥ {MIN_REPLICATES}</span>
              )}
            </div>
          </div>
          <div className="row wrap">
            <div className="field">
              <label>Penetration set</label>
              <div className="row wrap" style={{ gap: 2 }}>
                {PEN_CHOICES.map((p) => (
                  <label key={p} className="check">
                    <input
                      type="checkbox"
                      checked={pens.includes(p)}
                      onChange={() => toggle(pens, p, setPens)}
                    />
                    {pct(p)}
                  </label>
                ))}
              </div>
            </div>
          </div>
          <div className="row wrap">
            <div className="field">
              <label>Compliance set</label>
              <div className="row wrap" style={{ gap: 2 }}>
                {COM_CHOICES.map((c) => (
                  <label key={c} className="check">
                    <input
                      type="checkbox"
                      checked={coms.includes(c)}
                      onChange={() => toggle(coms, c, setComs)}
                    />
                    {pct(c)}
                  </label>
                ))}
              </div>
            </div>
            <div className="field">
              <label>Reference</label>
              <label
                className="check"
                title="Adds a p=0 (no controlled vehicles) cell so every delta in the matrix is measured against an uncontrolled run from the same sweep"
              >
                <input
                  type="checkbox"
                  checked={includeBaseline}
                  onChange={(e) => setIncludeBaseline(e.target.checked)}
                />
                include p=0 baseline cell
              </label>
            </div>
            <span className="spacer" />
            <button className="btn primary" onClick={() => void launch()}>
              Launch {pens.length * coms.length} cells{includeBaseline ? ' + baseline' : ''}
            </button>
          </div>
        </div>
      </div>

      {sweep && matrix && (
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">
              {sweep.sweep_id} · Δ vs {matrix.hasBaseline ? 'baseline' : 'reference'}{' '}
              <span className="mono" style={{ textTransform: 'none' }}>
                (p={pct(matrix.reference.penetration)}
                {matrix.hasBaseline ? '' : `, c=${pct(matrix.reference.compliance)}`}
                {matrix.reference.controller !== undefined
                  ? `, controller ${matrix.reference.controller ?? 'none'}`
                  : ''}
                {baseStat ? `, ${def.label} ${formatNumber(baseStat.mean, def.digits)} ${def.unit}` : ''})
              </span>
            </span>
            <span className="spacer" />
            <div className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <label htmlFor="s-metric">Metric</label>
              <select
                id="s-metric"
                className="input"
                value={metricKey}
                onChange={(e) => setMetricKey(e.target.value)}
              >
                {SWEEP_METRICS.map((d) => (
                  <option key={d.key} value={d.key}>
                    {d.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="panel-body table-wrap">
            <table className="sweep-matrix">
              <thead>
                <tr>
                  <th className="rowh">pen \ comp</th>
                  {matrix.cols.map((c) => (
                    <th key={c}>{pct(c)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {matrix.hasBaseline && (
                  <tr>
                    <th
                      className="rowh"
                      title="p=0: no controlled vehicles — the uncontrolled reference every delta is measured against"
                    >
                      0% · baseline
                    </th>
                    {matrix.baselines.length === 1
                      ? renderBaseline(matrix.baselines[0], 'baseline', Math.max(1, matrix.cols.length))
                      : matrix.cols.map((c) =>
                          renderBaseline(
                            matrix.baselines.find((b) => b.compliance === c) ?? matrix.baselines[0],
                            c,
                            1,
                          ),
                        )}
                  </tr>
                )}
                {matrix.rows.map((p) => (
                  <tr key={p}>
                    <th className="rowh">{pct(p)}</th>
                    {matrix.cols.map((c) => {
                      const cell = matrix.at(p, c);
                      if (!cell || cell.status !== 'done' || !cell.aggregate) {
                        return (
                          <td key={c} className="cell pending">
                            {cell ? (cell.status ?? 'queued') : '—'}
                          </td>
                        );
                      }
                      const delta = cellDelta(cell);
                      return (
                        <td
                          key={c}
                          className="cell"
                          style={{
                            background: delta === null ? undefined : deltaColor(goodness(delta)),
                          }}
                          onClick={() => openCell(cell)}
                          onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, cell })}
                          onMouseLeave={() => setTip(null)}
                        >
                          <div className="d">{delta === null ? '·' : formatDeltaPct(delta)}</div>
                          <div className="n">n={cell.aggregate[metricKey]?.n ?? '—'}</div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            {sweep.status === 'failed' && (
              <p className="hint-amber" style={{ marginTop: 12 }}>
                sweep fan-out failed{sweep.error ? `: ${sweep.error}` : ''} — cells without a run
                will not start
              </p>
            )}
            {!matrix.hasBaseline && (
              <p className="hint-amber" style={{ marginTop: 12 }}>
                no p=0 baseline cell in this sweep — deltas are relative to its lowest p·c cell
                (p={pct(matrix.reference.penetration)}, c={pct(matrix.reference.compliance)}), not to
                an uncontrolled run
              </p>
            )}
            <p className="small muted" style={{ marginTop: 12 }}>
              {def.good === 'down' ? 'green = reduction (improvement)' : 'green = increase (improvement)'} ·
              click a cell to open its run
            </p>
          </div>
        </div>
      )}

      {tip && tip.cell.aggregate && (
        <div className="sweep-tip" style={{ left: tip.x + 14, top: tip.y + 14 }}>
          {(() => {
            const s = tip.cell.aggregate[metricKey];
            if (!s) return <span>no {def.label}</span>;
            return (
              <>
                <div>
                  <span className="t-muted">
                    p={pct(tip.cell.penetration)} · c={pct(tip.cell.compliance)}
                    {isBaseline(tip.cell) ? ' · baseline' : ''}
                  </span>
                </div>
                <div>
                  {def.label} {formatNumber(s.mean, def.digits)} {def.unit}
                </div>
                <div className="t-muted">
                  95% CI [{formatNumber(s.lo95, def.digits)}, {formatNumber(s.hi95, def.digits)}] ·
                  n={s.n}
                  {s.underpowered ? ' · UNDERPOWERED' : ''}
                </div>
              </>
            );
          })()}
        </div>
      )}

      {!sweep && sweepId && <div className="empty">collecting sweep cells…</div>}
    </div>
  );
}
