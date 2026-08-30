/** Sweeps: penetration × compliance grid launcher and a result matrix
 * coloured by metric delta vs the baseline cell, CI on hover, click-through
 * to the cell's run detail. */

import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { createSweep, getSweep, listScenarios } from '../api/client';
import type { ScenarioSummary, SweepCell, SweepDetail } from '../api/types';
import { toast, toastError } from '../components/toast';
import { deltaColor } from '../lib/colormap';
import { formatDeltaPct, formatNumber } from '../lib/format';
import { usePoll } from '../lib/hooks';
import { METRIC_DEFS, metricDef, MIN_REPLICATES } from '../lib/metrics';

const PEN_CHOICES = [0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3];
const COM_CHOICES = [0.25, 0.5, 0.8, 1.0];
const CONTROLLERS = ['follower_stopper', 'pi_saturation', 'jad'];
const SWEEP_METRICS = METRIC_DEFS.filter((d) => d.good !== 'neutral');

interface Tip {
  x: number;
  y: number;
  cell: SweepCell;
}

export function SweepsView(): JSX.Element {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [scenarioId, setScenarioId] = useState('');
  const [pens, setPens] = useState<number[]>([0.01, 0.02, 0.05, 0.1, 0.15, 0.2]);
  const [coms, setComs] = useState<number[]>([0.25, 0.5, 0.8, 1.0]);
  const [controller, setController] = useState(CONTROLLERS[0]);
  const [replicates, setReplicates] = useState(20);
  const [searchParams, setSearchParams] = useSearchParams();
  const [sweepId, setSweepId] = useState<string | null>(searchParams.get('sweep'));
  const [sweep, setSweep] = useState<SweepDetail | null>(null);
  const [metricKey, setMetricKey] = useState('sigma_v');
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

  const allDone = sweep !== null && sweep.cells.every((c) => c.status === 'done');
  const pollSweep = useCallback(async () => {
    if (!sweepId) return;
    try {
      setSweep(await getSweep(sweepId));
    } catch (err) {
      toastError(err, 'sweep');
    }
  }, [sweepId]);
  usePoll(pollSweep, sweepId && !allDone ? 2500 : null);

  const launch = async (): Promise<void> => {
    if (!scenarioId || pens.length === 0 || coms.length === 0) {
      toast('error', 'pick a scenario plus at least one penetration and compliance');
      return;
    }
    try {
      const res = await createSweep({
        scenario_id: scenarioId,
        penetrations: [...pens].sort((a, b) => a - b),
        compliances: [...coms].sort((a, b) => a - b),
        controller,
        replicates,
      });
      setSweep(null);
      setSweepId(res.sweep_id);
      setSearchParams({ sweep: res.sweep_id }, { replace: true });
      toast('ok', `sweep ${res.sweep_id} launched · ${pens.length * coms.length} cells`);
    } catch (err) {
      toastError(err, 'sweep');
    }
  };

  const toggle = (list: number[], v: number, set: (l: number[]) => void): void => {
    set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v].sort((a, b) => a - b));
  };

  /* matrix layout */
  const matrix = useMemo(() => {
    if (!sweep) return null;
    const rows = [...new Set(sweep.cells.filter((c) => c.penetration > 0).map((c) => c.penetration))].sort(
      (a, b) => a - b,
    );
    const cols = [...new Set(sweep.cells.filter((c) => c.penetration > 0).map((c) => c.compliance))].sort(
      (a, b) => a - b,
    );
    const baseline =
      sweep.cells.find((c) => c.penetration === 0) ??
      sweep.cells.reduce((min, c) =>
        c.penetration * c.compliance < min.penetration * min.compliance ? c : min,
      );
    const at = (p: number, c: number): SweepCell | undefined =>
      sweep.cells.find((x) => x.penetration === p && x.compliance === c);
    return { rows, cols, baseline, at };
  }, [sweep]);

  const def = metricDef(metricKey);
  const baseStat = matrix?.baseline.aggregate?.[metricKey];

  const cellDelta = (cell: SweepCell): number | null => {
    const stat = cell.aggregate?.[metricKey];
    if (!stat || !baseStat || baseStat.mean === 0) return null;
    return (stat.mean - baseStat.mean) / Math.abs(baseStat.mean);
  };

  const goodness = (delta: number): number => {
    const signed = def.good === 'down' ? -delta : delta;
    return Math.max(-1, Math.min(1, signed / 0.5)); // ±50% saturates
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
                    {Math.round(p * 100)}%
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
                    {Math.round(c * 100)}%
                  </label>
                ))}
              </div>
            </div>
            <span className="spacer" />
            <button className="btn primary" onClick={() => void launch()}>
              Launch {pens.length * coms.length} cells
            </button>
          </div>
        </div>
      </div>

      {sweep && matrix && (
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">
              {sweep.sweep_id} · Δ vs baseline{' '}
              <span className="mono" style={{ textTransform: 'none' }}>
                (p={Math.round(matrix.baseline.penetration * 100)}%
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
                    <th key={c}>{Math.round(c * 100)}%</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {matrix.rows.map((p) => (
                  <tr key={p}>
                    <th className="rowh">{Math.round(p * 100)}%</th>
                    {matrix.cols.map((c) => {
                      const cell = matrix.at(p, c);
                      if (!cell || cell.status !== 'done' || !cell.aggregate) {
                        return (
                          <td key={c} className="cell pending">
                            {cell ? cell.status : '—'}
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
                          onClick={() => navigate(`/runs/${cell.run_id}`)}
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
                    p={Math.round(tip.cell.penetration * 100)}% · c=
                    {Math.round(tip.cell.compliance * 100)}%
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
