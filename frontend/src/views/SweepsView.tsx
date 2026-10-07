/** Sweeps (docs/design/DASHBOARD_DESIGN.md §10.5): a penetration ×
 * compliance (× strategy) grid launcher and a result matrix binned by metric
 * delta vs the sweep's baseline cell (p=0, no controlled vehicles): blue =
 * better, red = worse, in discrete classes (§7.4). Every cell is a button —
 * hover or keyboard focus shows its value and CI, Enter or click opens its
 * run — and prints its signed delta, so colour never carries the number
 * alone. The deltas are the cell means against the reference mean, exactly as
 * the API reported them; the dashboard computes no statistic of its own.
 *
 * A sweep is a cartesian product times a replicate loop, so the launcher
 * states the arithmetic (cells × replicates = runs) and takes a second,
 * explicit click before any of it is enqueued. The matrix also flags cells
 * whose aggregate vector is bit-identical to another cell's: two different
 * configurations returning the same realisation is a result about the
 * pipeline, not about compliance, and must not read as a finding. */

import { useCallback, useMemo, useRef, useState, type FocusEvent, type MouseEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { createSweep, getSweep, listScenarios, OFFLINE_WRITE_MESSAGE } from '../api/client';
import type {
  CreateSweepRequest,
  ScenarioSummary,
  SweepCell,
  SweepDetail,
  SweepStrategy,
  Tier,
} from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Icon } from '../components/icons';
import { PageHeader } from '../components/PageHeader';
import { toast, toastError } from '../components/toast';
import { Callout } from '../components/ui/Callout';
import { EmptyState } from '../components/ui/EmptyState';
import { Skeleton } from '../components/ui/Skeleton';
import { DELTA_LEGEND, deltaClass, signedImprovement } from '../lib/colormap';
import { formatDeltaPct, formatFetchError, formatNumber } from '../lib/format';
import { useAuthFailed, useOfflineFallback, usePoll } from '../lib/hooks';
import { clampInt, MAX_REPLICATES, MAX_SWEEP_CELLS } from '../lib/limits';
import {
  DEFAULT_SWEEP_METRIC,
  hasNoObservations,
  METRIC_DEFS,
  metricDef,
  MIN_REPLICATES,
  NO_OBSERVATIONS_LABEL,
  NO_OBSERVATIONS_TITLE,
} from '../lib/metrics';

const PEN_CHOICES = [0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3];
const COM_CHOICES = [0.25, 0.5, 0.8, 1.0];
const CONTROLLERS = ['follower_stopper', 'pi_saturation', 'jad'];

/** The API's infrastructure axis (`flowstate_core.strategies.STRATEGIES`),
 * in its own order. `none` runs the scenario as calibrated. */
const STRATEGIES: SweepStrategy[] = ['none', 'vsl', 'alinea', 'vsl+alinea'];
const STRATEGY_TITLES: Record<SweepStrategy, string> = {
  none: 'the scenario as calibrated — no infrastructure deployed',
  vsl: 'gantry speed limits posted along the corridor (controllers.vsl)',
  alinea: 'every on-ramp metered by ALINEA at the corridor’s critical density',
  'vsl+alinea': 'both speed limits and ramp metering',
};
/** Each strategy other than `none` also runs one uncontrolled cell, so the
 * infrastructure can be priced without any controlled vehicle. */
const infraCells = (s: SweepStrategy[]): number => s.filter((x) => x !== 'none').length;
const needsAlinea = (s: SweepStrategy[]): boolean => s.some((x) => x.includes('alinea'));
const SWEEP_METRICS = METRIC_DEFS.filter((d) => d.good !== 'neutral');
const SWEEP_POLL_MS = 2500;
/** Exploratory default. Headline numbers need MIN_REPLICATES (§0.6); a grid
 * at 20 replicates is hundreds of full-length simulations, so the launcher
 * starts cheap and says what the reporting standard is. */
const DEFAULT_SWEEP_REPLICATES = 5;

/** Said wherever a macro sweep's numbers are shown. The macro tier is a
 * first-order CTM: it is string-stable by construction, so its cells are a
 * screening comparison and never evidence about wave formation or dampening
 * (CLAUDE.md §5.6 — the API also refuses to build a validation report from
 * them). The banner is persistent, not a tooltip: a matrix of deltas reads as
 * a result whether or not anyone hovers it. */
const MACRO_SWEEP_BANNER =
  'Screening tier (CTM) — not a validation result';
const MACRO_SWEEP_DETAIL =
  'Macro (CTM) cells are a fast first-order screening comparison. The model is string-stable ' +
  'by construction, so these deltas cannot support a claim about phantom-jam formation or ' +
  'dampening, and the API refuses to generate a validation report from them. Re-run the ' +
  'grid on the micro (SUMO/IDM) tier before quoting any of it.';

const IDENTICAL_TITLE =
  'Identical realisation: this cell\u2019s whole aggregate vector matches another cell\u2019s, ' +
  'so the two runs produced the same numbers despite different configurations.';

/** The cell tooltip: follows the pointer on hover, anchored under the cell
 * when keyboard focus put it there. */
interface Tip {
  x: number;
  y: number;
  cell: SweepCell;
  anchor: 'pointer' | 'cell';
}

/** Rows × columns of the placeholder matrix shown before the first cells. */
const SKELETON_ROWS = 4;
const SKELETON_COLS = 4;

/** The persistent screening-tier label (see MACRO_SWEEP_BANNER). */
function MacroBanner(): JSX.Element {
  return (
    <p className="hint-amber" role="note" title={MACRO_SWEEP_DETAIL}>
      <b>{MACRO_SWEEP_BANNER}</b> · {MACRO_SWEEP_DETAIL}
    </p>
  );
}

/** The cell's infrastructure strategy; a service older than the axis ran
 * every cell as calibrated. */
const strategyOf = (c: SweepCell): SweepStrategy => c.strategy ?? 'none';

/** A p=0 cell with no strategy is the sweep's baseline: no controlled vehicle
 * acts and nothing is deployed, so the cell is the uncontrolled reference. A
 * p=0 cell that *does* deploy something is not a baseline — it is the
 * infrastructure priced on its own, and calling it the reference would hide
 * the very effect it was run to measure. */
const isBaseline = (c: SweepCell): boolean => c.penetration === 0 && strategyOf(c) === 'none';

/** p=0 with a strategy: the infrastructure-only reference cells. */
const isInfraOnly = (c: SweepCell): boolean => c.penetration === 0 && strategyOf(c) !== 'none';

const pct = (v: number): string => `${Math.round(v * 100)}%`;

const cellKey = (c: SweepCell): string =>
  c.run_id ??
  c.config_hash ??
  `${c.penetration}:${c.compliance}:${c.controller ?? 'none'}:${strategyOf(c)}`;

/** The cell's whole aggregate vector as a string — two cells sharing one are
 * the same realisation, whatever their config hashes say. */
function aggregateSignature(cell: SweepCell): string | null {
  const agg = cell.aggregate;
  if (!agg) return null;
  const keys = Object.keys(agg).sort();
  if (keys.length === 0) return null;
  return keys
    .map((k) => {
      const s = agg[k];
      return `${k}=${s.mean}/${s.lo95}/${s.hi95}/${s.n}`;
    })
    .join('|');
}

/** A cell's configuration identity: the hash the API stamped on it, falling
 * back to the grid coordinates for a sweep that carries none. */
const configKey = (c: SweepCell): string =>
  c.config_hash ??
  `${c.penetration}:${c.compliance}:${c.controller ?? 'none'}:${strategyOf(c)}`;

/** Keys of cells whose aggregates are bit-identical to those of a cell with a
 * *different* configuration. Cells sharing a config hash are one configuration
 * (a p=0 row and the baseline hash alike), so identical numbers there are the
 * expected result, not the pipeline finding the note warns about. */
function identicalCells(cells: SweepCell[]): Set<string> {
  const bySig = new Map<string, SweepCell[]>();
  for (const c of cells) {
    const sig = aggregateSignature(c);
    if (sig === null) continue;
    const list = bySig.get(sig) ?? [];
    list.push(c);
    bySig.set(sig, list);
  }
  const dup = new Set<string>();
  for (const list of bySig.values()) {
    if (list.length < 2) continue;
    if (new Set(list.map(configKey)).size < 2) continue;
    for (const c of list) dup.add(cellKey(c));
  }
  return dup;
}

/** One matrix row: a penetration under one infrastructure strategy. With a
 * strategy axis a penetration is no longer one row — the same 5 % of
 * controlled vehicles under VSL and without it are two configurations. */
interface MatrixRow {
  penetration: number;
  strategy: SweepStrategy;
  key: string;
}

interface MatrixLayout {
  rows: MatrixRow[];
  cols: number[];
  /** Every p=0 no-strategy cell (one per compliance when the request listed
   * p=0). */
  baselines: SweepCell[];
  /** p=0 cells that deploy something: the infrastructure priced alone. */
  infra: SweepCell[];
  /** The delta reference: the first baseline, or — when the sweep has no
   * p=0 cell — the lowest p·c grid cell, flagged as such in the header. */
  reference: SweepCell;
  hasBaseline: boolean;
  at: (row: MatrixRow, c: number) => SweepCell | undefined;
}

const rowKey = (p: number, s: SweepStrategy): string => `${p}|${s}`;

function layoutMatrix(sweep: SweepDetail): MatrixLayout | null {
  if (sweep.cells.length === 0) return null;
  const grid = sweep.cells.filter((c) => !isBaseline(c) && !isInfraOnly(c));
  const rows: MatrixRow[] = [];
  for (const c of grid) {
    const key = rowKey(c.penetration, strategyOf(c));
    if (!rows.some((r) => r.key === key)) {
      rows.push({ penetration: c.penetration, strategy: strategyOf(c), key });
    }
  }
  rows.sort(
    (a, b) =>
      a.penetration - b.penetration ||
      STRATEGIES.indexOf(a.strategy) - STRATEGIES.indexOf(b.strategy),
  );
  const cols = [...new Set(grid.map((c) => c.compliance))].sort((a, b) => a - b);
  const byHash = (a: SweepCell, b: SweepCell): number =>
    (a.config_hash ?? '').localeCompare(b.config_hash ?? '');
  const baselines = sweep.cells.filter(isBaseline).sort(byHash);
  const infra = sweep.cells
    .filter(isInfraOnly)
    .sort((a, b) => STRATEGIES.indexOf(strategyOf(a)) - STRATEGIES.indexOf(strategyOf(b)));
  const lowest = [...grid].sort(
    (a, b) => a.penetration * a.compliance - b.penetration * b.compliance,
  )[0];
  const reference = baselines[0] ?? lowest ?? sweep.cells[0];
  const at = (row: MatrixRow, c: number): SweepCell | undefined =>
    grid.find(
      (x) => x.penetration === row.penetration && x.compliance === c && strategyOf(x) === row.strategy,
    );
  return { rows, cols, baselines, infra, reference, hasBaseline: baselines.length > 0, at };
}

export function SweepsView(): JSX.Element {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [scenarioId, setScenarioId] = useState('');
  const [pens, setPens] = useState<number[]>([0.01, 0.02, 0.05, 0.1, 0.15, 0.2]);
  const [coms, setComs] = useState<number[]>([0.25, 0.5, 0.8, 1.0]);
  const [controller, setController] = useState(CONTROLLERS[0]);
  const [strategies, setStrategies] = useState<SweepStrategy[]>(['none']);
  const [alineaTarget, setAlineaTarget] = useState('');
  const [tier, setTier] = useState<Tier>('micro');
  const [replicates, setReplicates] = useState(DEFAULT_SWEEP_REPLICATES);
  const [includeBaseline, setIncludeBaseline] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const [sweepId, setSweepId] = useState<string | null>(searchParams.get('sweep'));
  const [sweep, setSweep] = useState<SweepDetail | null>(null);
  const [metricKey, setMetricKey] = useState(DEFAULT_SWEEP_METRIC);
  const [tip, setTip] = useState<Tip | null>(null);
  const [sweepError, setSweepError] = useState<string | null>(null);
  // the sweep the responses belong to; a failing poll toasts once per streak
  const currentSweep = useRef(sweepId);
  currentSweep.current = sweepId;
  const sweepFailing = useRef(false);
  const navigate = useNavigate();
  const authFailed = useAuthFailed();
  // POST /sweeps never falls back to the demo backend, so the launcher says so
  // up front instead of failing on the click (api/client.assertWritable)
  const offline = useOfflineFallback();

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
  usePoll(loadScenarios, authFailed || scenariosLoaded ? null : 3000);

  // `sweep.status` is the fan-out job's own status (`done` = every child run
  // exists; cells still finish on their own), so polling stops on the cells —
  // except when the fan-out `failed`: cells without a run then never get one.
  const settled =
    sweep !== null &&
    (sweep.status === 'failed' ||
      sweep.cells.every((c) => c.status === 'done' || c.status === 'failed'));
  const pollSweep = useCallback(async () => {
    if (!sweepId) return;
    const id = sweepId;
    try {
      const next = await getSweep(id);
      if (currentSweep.current !== id) return;
      sweepFailing.current = false;
      setSweep(next);
      setSweepError(null);
    } catch (err) {
      if (currentSweep.current !== id) return;
      if (!sweepFailing.current) toastError(err, 'sweep');
      sweepFailing.current = true;
      setSweepError(formatFetchError(err));
    }
  }, [sweepId]);
  usePoll(pollSweep, sweepId && !settled && !authFailed ? SWEEP_POLL_MS : null);

  const cellCount = pens.length * coms.length * strategies.length;
  const infra = infraCells(strategies);
  const totalCells = cellCount + infra + (includeBaseline ? 1 : 0);
  const totalRuns = totalCells * replicates;
  const scenarioName = scenarios.find((s) => s.scenario_id === scenarioId)?.name ?? scenarioId;
  const alineaRequested = needsAlinea(strategies);

  const launch = (): void => {
    if (!scenarioId || pens.length === 0 || coms.length === 0) {
      toast('error', 'pick a scenario plus at least one penetration and compliance');
      return;
    }
    if (strategies.length === 0) {
      toast('error', 'pick at least one strategy (none = the scenario as calibrated)');
      return;
    }
    // Empty is the documented "read it from the scenario's FD calibration";
    // a typo must not be sent as a metering target.
    if (alineaRequested && alineaTarget.trim() && !(Number(alineaTarget) > 0)) {
      toast('error', 'the ALINEA target density must be a positive number of veh/km');
      return;
    }
    if (totalCells > MAX_SWEEP_CELLS) {
      toast('error', `${totalCells} cells exceeds the API cap of ${MAX_SWEEP_CELLS} — trim the grid`);
      return;
    }
    setConfirming(true);
  };

  const doLaunch = async (): Promise<void> => {
    setLaunching(true);
    try {
      // the API contract is the plural `controllers` list (SweepCreateRequest);
      // `tier` is sent explicitly — omitting it ran every macro grid on the
      // scenario's own tier, silently producing micro runs for a macro request
      const body: CreateSweepRequest = {
        scenario_id: scenarioId,
        penetrations: [...pens].sort((a, b) => a - b),
        compliances: [...coms].sort((a, b) => a - b),
        controllers: [controller],
        // stated even when it is the default, like `tier`
        strategies: [...strategies].sort(
          (a, b) => STRATEGIES.indexOf(a) - STRATEGIES.indexOf(b),
        ),
        replicates,
        include_baseline: includeBaseline,
        tier,
      };
      // No target field: the API reads the critical density from the
      // scenario's FD calibration, or refuses — the dashboard invents none.
      if (alineaRequested && alineaTarget.trim()) {
        body.alinea = { rho_target_veh_km: Number(alineaTarget) };
      }
      const res = await createSweep(body);
      setSweep(null);
      setSweepError(null);
      sweepFailing.current = false;
      setSweepId(res.sweep_id);
      setSearchParams({ sweep: res.sweep_id }, { replace: true });
      setConfirming(false);
      toast(
        'ok',
        `sweep ${res.sweep_id} launched · ${cellCount} cells${infra ? ` + ${infra} infrastructure` : ''}${includeBaseline ? ' + baseline' : ''} × ${replicates} reps = ${totalRuns} runs`,
      );
    } catch (err) {
      toastError(err, 'sweep');
    } finally {
      setLaunching(false);
    }
  };

  const toggle = (list: number[], v: number, set: (l: number[]) => void): void => {
    set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v].sort((a, b) => a - b));
  };

  const toggleStrategy = (s: SweepStrategy): void => {
    setStrategies((cur) =>
      cur.includes(s)
        ? cur.filter((x) => x !== s)
        : [...cur, s].sort((a, b) => STRATEGIES.indexOf(a) - STRATEGIES.indexOf(b)),
    );
  };

  /* matrix layout */
  const matrix = useMemo(() => (sweep ? layoutMatrix(sweep) : null), [sweep]);
  const duplicates = useMemo(() => (sweep ? identicalCells(sweep.cells) : new Set<string>()), [sweep]);
  // cells, not keys: two cells can share a key when the API reuses one run
  const twinCount = useMemo(
    () => (sweep ? sweep.cells.filter((c) => duplicates.has(cellKey(c))).length : 0),
    [sweep, duplicates],
  );

  const def = metricDef(metricKey);
  const baseStat = matrix?.reference.aggregate?.[metricKey];

  const cellDelta = (cell: SweepCell): number | null => {
    const stat = cell.aggregate?.[metricKey];
    if (!stat || !baseStat || stat.mean === null || baseStat.mean === null) return null;
    if (baseStat.mean === 0) return null;
    return (stat.mean - baseStat.mean) / Math.abs(baseStat.mean);
  };

  const openCell = (cell: SweepCell): void => {
    if (cell.run_id) navigate(`/runs/${cell.run_id}`);
  };

  /* one inspect behaviour for pointer and keyboard (§7.7) */
  const tipHandlers = (cell: SweepCell) => ({
    onMouseMove: (e: MouseEvent<HTMLButtonElement>) =>
      setTip({ x: e.clientX, y: e.clientY, cell, anchor: 'pointer' }),
    onMouseLeave: () => setTip((t) => (t?.anchor === 'pointer' ? null : t)),
    onFocus: (e: FocusEvent<HTMLButtonElement>) => {
      const r = e.currentTarget.getBoundingClientRect();
      setTip({ x: r.left, y: r.bottom, cell, anchor: 'cell' });
    },
    onBlur: () => setTip(null),
  });

  const refName = matrix?.hasBaseline ? 'baseline' : 'reference';
  const coordsLabel = (cell: SweepCell): string =>
    `p=${pct(cell.penetration)} c=${pct(cell.compliance)}` +
    (strategyOf(cell) !== 'none' ? ` · ${strategyOf(cell)}` : '');

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
    const twin = duplicates.has(cellKey(cell));
    const noObs = hasNoObservations(stat);
    const value = noObs
      ? NO_OBSERVATIONS_LABEL
      : stat
        ? `${formatNumber(stat.mean, def.digits)} ${def.unit}`
        : '·';
    return (
      <td key={key} colSpan={colSpan} className="cell baseline">
        <button
          type="button"
          className="cell-btn baseline-btn"
          title={
            noObs
              ? NO_OBSERVATIONS_TITLE
              : 'p=0: no controlled vehicles — the uncontrolled reference every delta is measured against'
          }
          aria-label={`baseline ${coordsLabel(cell)}: ${value}, n=${stat?.n ?? '—'}${
            twin ? ', identical realisation' : ''
          }`}
          onClick={() => openCell(cell)}
          {...tipHandlers(cell)}
        >
          {/* n=0 is not a value: naming it keeps the reference from reading as a
              measured zero that every delta below would be computed against */}
          <span className={noObs ? 'd noobs' : 'd'}>{value}</span>
          <span className="n">
            BASELINE · n={stat?.n ?? '—'}
            {twin && (
              <span className="identical" title={IDENTICAL_TITLE}>
                {' '}
                ≡
              </span>
            )}
          </span>
        </button>
      </td>
    );
  };

  /** One measured cell: its delta against the reference, its replicate count
   * and — since a penetration alone no longer identifies a configuration —
   * the infrastructure it deploys. Shared by the grid rows and the
   * infrastructure-only rows, which are deltas like any other cell: at p=0
   * with a strategy, the whole difference from the baseline is the
   * deployment. */
  const renderCell = (cell: SweepCell, key: string | number, colSpan: number): JSX.Element => {
    if (cell.status !== 'done' || !cell.aggregate) {
      return (
        <td key={key} colSpan={colSpan} className="cell pending">
          {cell.status ?? 'queued'}
        </td>
      );
    }
    const delta = cellDelta(cell);
    const twin = duplicates.has(cellKey(cell));
    // no replicate produced the metric here: there is no delta to colour, and
    // a bare '·' would read as "still computing" rather than "measured nothing"
    const noObs = hasNoObservations(cell.aggregate[metricKey]);
    const strategy = strategyOf(cell);
    const n = cell.aggregate[metricKey]?.n ?? '—';
    const cls = delta === null || noObs ? 'delta-none' : deltaClass(signedImprovement(delta, def.good));
    const shown = noObs ? NO_OBSERVATIONS_LABEL : delta === null ? '·' : formatDeltaPct(delta);
    const spoken = noObs
      ? NO_OBSERVATIONS_LABEL
      : delta === null
        ? 'no delta'
        : `${formatDeltaPct(delta)} vs ${refName}`;
    return (
      <td key={key} colSpan={colSpan} className="cell">
        <button
          type="button"
          className={`cell-btn ${cls}`}
          title={
            noObs
              ? NO_OBSERVATIONS_TITLE
              : strategy !== 'none'
                ? STRATEGY_TITLES[strategy]
                : undefined
          }
          aria-label={`${coordsLabel(cell)}: ${spoken}, n=${n}${twin ? ', identical realisation' : ''}`}
          onClick={() => openCell(cell)}
          {...tipHandlers(cell)}
        >
          <span className={noObs ? 'd noobs' : 'd'}>{shown}</span>
          <span className="n">
            n={n}
            {strategy !== 'none' ? ` · ${strategy}` : ''}
            {twin && (
              <span className="identical" title={IDENTICAL_TITLE}>
                {' '}
                ≡
              </span>
            )}
          </span>
        </button>
      </td>
    );
  };

  /** The tooltip's lines for the hovered or focused cell. */
  const tipBody = (cell: SweepCell): JSX.Element => {
    const s = cell.aggregate?.[metricKey];
    const coords = (
      <div className="t-muted">
        p={pct(cell.penetration)} · c={pct(cell.compliance)}
        {isBaseline(cell) ? ' · baseline' : ''}
        {strategyOf(cell) !== 'none' ? ` · ${strategyOf(cell)}` : ''}
      </div>
    );
    if (!s) {
      return (
        <>
          {coords}
          <div>no {def.label}</div>
        </>
      );
    }
    if (hasNoObservations(s)) {
      return (
        <>
          {coords}
          <div>
            {def.label} — {NO_OBSERVATIONS_LABEL}
          </div>
          <div className="t-muted">no replicate produced a value · n=0</div>
        </>
      );
    }
    return (
      <>
        {coords}
        <div>
          {def.label} <b>{formatNumber(s.mean, def.digits)}</b> {def.unit}
        </div>
        <div className="t-muted">
          95% CI {formatNumber(s.lo95, def.digits)} – {formatNumber(s.hi95, def.digits)} · n=
          {s.n}
          {s.underpowered ? ' · UNDERPOWERED' : ''}
        </div>
      </>
    );
  };

  const metricPicker = (
    <div className="field field-inline">
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
  );

  const retryButton = (
    <button type="button" className="btn sm" onClick={() => void pollSweep()}>
      <Icon name="refresh-cw" size={14} />
      Retry
    </button>
  );

  return (
    <div className="view sweeps">
      <PageHeader
        title="Sweeps"
        documentTitle="Sweeps"
        description="Penetration × compliance grids, each cell compared with an uncontrolled p=0 baseline."
      />

      <section className="panel">
        <div className="panel-head">
          <h2 className="panel-title">New sweep</h2>
        </div>
        <div className="panel-body sweep-launcher">
          {tier === 'macro' && <MacroBanner />}
          <div className="form-grid">
            <div className="field">
              <label htmlFor="s-scn">Scenario</label>
              <select
                id="s-scn"
                className="input w-full"
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
                className="input w-full"
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
              <label htmlFor="s-tier">Tier</label>
              <select
                id="s-tier"
                className="input w-full"
                value={tier}
                onChange={(e) => setTier(e.target.value as Tier)}
              >
                <option value="micro">micro (SUMO)</option>
                <option value="macro">macro (CTM screening)</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="s-reps">Replicates / cell</label>
              <input
                id="s-reps"
                className="input w-sm"
                type="number"
                min={1}
                max={MAX_REPLICATES}
                value={replicates}
                aria-describedby={replicates < MIN_REPLICATES ? 's-reps-hint' : undefined}
                onChange={(e) => setReplicates(clampInt(Number(e.target.value), 1, MAX_REPLICATES))}
              />
              {replicates < MIN_REPLICATES && (
                <span className="hint-amber" id="s-reps-hint">
                  exploratory — headline numbers need n ≥ {MIN_REPLICATES}
                </span>
              )}
            </div>
          </div>

          <fieldset className="chip-set">
            <legend>Penetration set</legend>
            <div className="chip-row">
              {PEN_CHOICES.map((p) => (
                <label key={p} className="chip-toggle">
                  <input
                    type="checkbox"
                    checked={pens.includes(p)}
                    onChange={() => toggle(pens, p, setPens)}
                  />
                  {pct(p)}
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset className="chip-set">
            <legend>Compliance set</legend>
            <div className="chip-row">
              {COM_CHOICES.map((c) => (
                <label key={c} className="chip-toggle">
                  <input
                    type="checkbox"
                    checked={coms.includes(c)}
                    onChange={() => toggle(coms, c, setComs)}
                  />
                  {pct(c)}
                </label>
              ))}
            </div>
          </fieldset>

          <div className="chip-set-line">
            <fieldset className="chip-set">
              <legend>Strategies</legend>
              <div className="chip-row">
                {STRATEGIES.map((s) => (
                  <label key={s} className="chip-toggle" title={STRATEGY_TITLES[s]}>
                    <input
                      type="checkbox"
                      checked={strategies.includes(s)}
                      onChange={() => toggleStrategy(s)}
                    />
                    {s}
                  </label>
                ))}
              </div>
            </fieldset>
            {alineaRequested && (
              <div className="field">
                <label htmlFor="s-alinea">ALINEA target [veh/km/lane]</label>
                <input
                  id="s-alinea"
                  className="input w-lg"
                  value={alineaTarget}
                  placeholder="from the scenario's FD calibration"
                  onChange={(e) => setAlineaTarget(e.target.value)}
                  title="Per-lane critical density the ramp meters hold downstream. Left empty, the API reads it from the scenario's fitted fundamental diagram and refuses the sweep when the scenario names none."
                />
              </div>
            )}
          </div>

          <fieldset className="chip-set">
            <legend>Reference</legend>
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
          </fieldset>
        </div>
        <div className="panel-foot form-actions">
          <span className="form-actions-summary mono">
            {cellCount} cells{infra ? ` + ${infra} infrastructure` : ''}
            {includeBaseline ? ' + baseline' : ''} = {totalCells} cells × {replicates} reps ={' '}
            {totalRuns} runs
          </span>
          <div className="form-actions-buttons">
            <button
              type="button"
              className="btn primary"
              onClick={launch}
              disabled={offline}
              title={offline ? OFFLINE_WRITE_MESSAGE : undefined}
            >
              Launch {cellCount} cells{infra ? ` + ${infra} infrastructure` : ''}
              {includeBaseline ? ' + baseline' : ''}…
            </button>
          </div>
        </div>
      </section>

      {sweepId && (
        <section className="panel sweep-panel">
          <div className="panel-head">
            <div className="sweep-head-text">
              <h2 className="panel-title">
                <span className="mono">{sweep?.sweep_id ?? sweepId}</span> · Δ vs {refName}
              </h2>
              {matrix && (
                <span className="panel-sub mono">
                  (p={pct(matrix.reference.penetration)}
                  {matrix.hasBaseline ? '' : `, c=${pct(matrix.reference.compliance)}`}
                  {/* a p=0 cell has no controlled vehicles, so naming the sweep's
                      controller there would credit a controller that never acted */}
                  {isBaseline(matrix.reference)
                    ? ', no controlled vehicles'
                    : matrix.reference.controller !== undefined
                      ? `, controller ${matrix.reference.controller ?? 'none'}`
                      : ''}
                  {baseStat
                    ? hasNoObservations(baseStat)
                      ? `, ${def.label} ${NO_OBSERVATIONS_LABEL}`
                      : `, ${def.label} ${formatNumber(baseStat.mean, def.digits)} ${def.unit}`
                    : ''}
                  )
                </span>
              )}
            </div>
            <span className="spacer" />
            {matrix && metricPicker}
          </div>
          <div className="panel-body sweep-body">
            {sweep?.tier === 'macro' && <MacroBanner />}
            {sweepError && (
              <Callout
                tone="danger"
                title={sweep ? 'The sweep stopped updating.' : 'This sweep could not be loaded.'}
                action={retryButton}
              >
                <span className="mono">{sweepError}</span>
              </Callout>
            )}
            {sweep && sweep.status === 'failed' && (
              <Callout tone="danger">
                sweep fan-out failed{sweep.error ? `: ${sweep.error}` : ''} — cells without a run
                will not start
              </Callout>
            )}
            {!sweep && !sweepError && (
              <div className="sweep-skeleton" aria-busy="true">
                {Array.from({ length: SKELETON_ROWS * SKELETON_COLS }, (_, i) => (
                  <Skeleton key={i} width={96} height={56} radius="xs" />
                ))}
              </div>
            )}
            {sweep && !matrix && sweep.status !== 'failed' && (
              <EmptyState
                title="No cells yet."
                description="Cells appear here as the sweep fans out its runs."
              />
            )}
            {sweep && matrix && (
              <>
                <div className="delta-legend" aria-hidden="true">
                  <span className="dl-end">worse</span>
                  {DELTA_LEGEND.map((d, i) => (
                    <span key={i} className={`dl-item${d.cls === 'delta-n' ? ' dl-neutral' : ''}`}>
                      <span className={`dl-swatch ${d.cls}`} />
                      {d.label}
                    </span>
                  ))}
                  <span className="dl-end">better</span>
                  <span className="dl-note">≡ identical realisation</span>
                  <span className="dl-note">n = replicates per cell</span>
                </div>
                <div className="sweep-matrix-wrap">
                  <table className="sweep-matrix" aria-label="sweep matrix">
                    <thead>
                      <tr>
                        <th className="rowh" scope="col">
                          pen \ comp
                        </th>
                        {matrix.cols.map((c) => (
                          <th key={c} scope="col">
                            {pct(c)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {matrix.hasBaseline && (
                        <tr>
                          <th
                            className="rowh"
                            scope="row"
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
                      {matrix.infra.map((cell) => (
                        <tr key={`infra-${strategyOf(cell)}`}>
                          <th
                            className="rowh"
                            scope="row"
                            title="p=0 with infrastructure deployed: no controlled vehicle acts, so the whole difference from the baseline is the deployment"
                          >
                            0% · {strategyOf(cell)}
                          </th>
                          {renderCell(cell, strategyOf(cell), Math.max(1, matrix.cols.length))}
                        </tr>
                      ))}
                      {matrix.rows.map((row) => (
                        <tr key={row.key}>
                          <th className="rowh" scope="row" title={STRATEGY_TITLES[row.strategy]}>
                            {pct(row.penetration)}
                            {row.strategy !== 'none' ? ` · ${row.strategy}` : ''}
                          </th>
                          {matrix.cols.map((c) => {
                            const cell = matrix.at(row, c);
                            return cell ? (
                              renderCell(cell, c, 1)
                            ) : (
                              <td key={c} className="cell pending">
                                —
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!matrix.hasBaseline && (
                  <Callout tone="warning">
                    no p=0 baseline cell in this sweep — deltas are relative to its lowest p·c cell
                    (p={pct(matrix.reference.penetration)}, c={pct(matrix.reference.compliance)}),
                    not to an uncontrolled run
                  </Callout>
                )}
                {twinCount > 0 && (
                  <Callout tone="warning">
                    ≡ {twinCount} cells share an identical aggregate vector with another cell:
                    different configurations returned the same realisation. Read those differences
                    as zero, not as a compliance or penetration effect.
                  </Callout>
                )}
                <p className="sweep-foot">
                  Blue = better, red = worse for this metric · select a cell to open its run
                </p>
              </>
            )}
          </div>
        </section>
      )}

      {tip && tip.cell.aggregate && (
        <div
          className="viz-tip sweep-tip"
          role="tooltip"
          // computed geometry: under the focused cell, or beside the pointer
          style={
            tip.anchor === 'cell'
              ? { left: tip.x, top: tip.y + 6 }
              : { left: tip.x + 14, top: tip.y + 14 }
          }
        >
          {tipBody(tip.cell)}
        </div>
      )}

      {confirming && (
        <ConfirmDialog
          title="Launch this sweep?"
          busy={launching}
          confirmLabel={`Launch ${totalRuns} runs`}
          onConfirm={() => void doLaunch()}
          onCancel={() => setConfirming(false)}
          facts={[
            ['Scenario', scenarioName],
            ['Tier', tier === 'macro' ? 'macro (CTM screening)' : 'micro (SUMO)'],
            ['Controller', controller],
            ['Grid', `${pens.length} penetrations × ${coms.length} compliances`],
            [
              'Strategies',
              `${strategies.join(', ')}${
                alineaRequested
                  ? ` · ALINEA target ${alineaTarget.trim() || "from the scenario's FD calibration"}`
                  : ''
              }`,
            ],
            [
              'Cells',
              `${cellCount}${infra ? ` + ${infra} infrastructure` : ''}${
                includeBaseline ? ' + 1 baseline' : ''
              } = ${totalCells}`,
            ],
            ['Replicates / cell', String(replicates)],
            ['Total runs', String(totalRuns)],
          ]}
        >
          {tier === 'macro' && <MacroBanner />}
          <p className="small muted">
            Every run is a full-length simulation of the scenario. {replicates} replicates per cell
            is {replicates < MIN_REPLICATES ? 'exploratory' : 'at'} the reporting standard of n ≥{' '}
            {MIN_REPLICATES}, which headline numbers require.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
