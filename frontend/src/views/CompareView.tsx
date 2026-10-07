/** Compare: two finished runs side by side — typically an uncontrolled
 * baseline (A) and a controlled run (B) on the same scenario. Reached from
 * the sidebar and from a run's "Compare with…" action; the choice is the URL
 * (`/compare?a=<run_id>&b=<run_id>`, plus `field=density` and the pickers'
 * `tier` / `scenario` filters), so a comparison is a link.
 *
 * Read top to bottom: the two pickers with what each run ran (scenario,
 * controller, penetration and compliance, seeds, tier, config hash), the
 * warnings about what B − A would otherwise fold in (another scenario,
 * another tier, a seeded perturbation, another config), the metrics table,
 * then both space–time fields on shared axes and one colour scale.
 *
 * Honesty rules, the same as everywhere else in the dashboard:
 * - every number is one the API returned; `B − A` and `B vs A` are the
 *   difference of the two means and its ratio to |A| (lib/compare), with no
 *   interval of their own — each run's 95% CI is shown beside it, not pooled;
 * - a metric a run did not record reads "not recorded", one no replicate
 *   produced reads "no observations", never a zero;
 * - a macro run is labelled screening, a seeded one SEEDED, a demo one DEMO;
 * - a run's controller is shown only where the API says what it was (its
 *   sweep cell, or a scenario whose hash it shares), "not reported" otherwise.
 *
 * Every panel renders loading → error → empty → content; a failed read is a
 * persistent danger callout with Retry. Nothing is polled: a run that is not
 * finished says so and offers a reload. */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import {
  getRun,
  getRunHeatmap,
  getRunMetrics,
  getSweep,
  isMockActive,
  listRuns,
  listScenarios,
} from '../api/client';
import type {
  HeatField,
  Heatmap,
  RunDetail,
  RunMetrics,
  RunSummary,
  ScenarioSummary,
  SweepDetail,
} from '../api/types';
import { SeededBadge, StatusChip, TierBadge } from '../components/bits';
import { FieldTabs, fieldTabId } from '../components/FieldTabs';
import {
  downloadHeatmapCSV,
  HEATMAP_CAPTION,
  HeatmapCanvas,
  heatmapExtent,
  isEmptyHeatmap,
  RampLegend,
  sameExtent,
  unionExtent,
} from '../components/HeatmapCanvas';
import { Icon } from '../components/icons';
import { PageHeader } from '../components/PageHeader';
import { RunFilterGroup } from '../components/RunFilterGroup';
import { toastError } from '../components/toast';
import { Callout } from '../components/ui/Callout';
import { HashValue } from '../components/ui/CopyButton';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorCallout } from '../components/ui/ErrorCallout';
import { Skeleton, SkeletonRows } from '../components/ui/Skeleton';
import {
  compareNotes,
  compareSections,
  describeController,
  describePenComp,
  describeSeeds,
  formatRelative,
  formatSigned,
  metricDifference,
  NOT_RECORDED_LABEL,
  NOT_RECORDED_TITLE,
  resolveRunSetup,
  runOptionLabel,
  runScenarioName,
  sharedSeed,
  sideStat,
  type CompareSide,
  type SideStat,
} from '../lib/compare';
import { DEMO_HASH_LABEL, DEMO_ROW_TITLE } from '../lib/demo';
import { failureReason, formatNumber } from '../lib/format';
import { metricDef, NO_OBSERVATIONS_LABEL, NO_OBSERVATIONS_TITLE } from '../lib/metrics';
import {
  hasRunFilters,
  NO_RUN_FILTERS,
  parseRunFilters,
  runFacet,
  runFiltersSearch,
  toggleRunFilter,
  type RunFilterKey,
  type RunFilters,
} from '../lib/runFilters';

type Side = 'a' | 'b';
const SIDES: Side[] = ['a', 'b'];
const SIDE_NAME: Record<Side, 'A' | 'B'> = { a: 'A', b: 'B' };
/** What each side usually holds; a hint, not a rule. */
const SIDE_HINT: Record<Side, string> = {
  a: 'usually the uncontrolled baseline',
  b: 'usually the controlled run',
};

/** Columns of the metrics table (skeleton rows span them). */
const METRIC_COLUMNS = 5;
const METRIC_SKELETON_ROWS = 7;

/** What the difference columns are, under the table (lib/compare). */
const DIFFERENCE_NOTE =
  'B − A is the difference of the two runs’ means as the API reported them, in the row’s unit; ' +
  'B vs A is that difference over |A|. Neither has a confidence interval of its own: each ' +
  'run’s 95% CI covers its own replicates, and the two are not pooled into one. The service ' +
  'reports no paired interval and the dashboard computes none, so read B − A beside both ' +
  'intervals; overlapping intervals do not show that there is no difference.';

/* ------------------------------ reads -------------------------------- */

interface KeyedReads<T> {
  data: Record<string, T>;
  errors: Record<string, unknown>;
  /** Forget a failure, so the key is read again. */
  retry: (key: string) => void;
  /** Read the key again, keeping what is on screen until the answer lands. */
  reload: (key: string) => void;
}

const has = (o: object, key: string): boolean => Object.prototype.hasOwnProperty.call(o, key);

function without<T>(o: Record<string, T>, key: string): Record<string, T> {
  if (!has(o, key)) return o;
  const next = { ...o };
  delete next[key];
  return next;
}

/** Each key read once and kept by key, so an answer for a run that is no
 * longer selected lands harmlessly under its own key and never on the run
 * now on screen. */
function useKeyedReads<T>(
  keys: string[],
  read: (key: string) => Promise<T>,
  label: string,
): KeyedReads<T> {
  const [data, setData] = useState<Record<string, T>>({});
  const [errors, setErrors] = useState<Record<string, unknown>>({});
  const inFlight = useRef(new Set<string>());
  const readRef = useRef(read);
  readRef.current = read;

  const fetchKey = useCallback(
    (key: string): void => {
      if (inFlight.current.has(key)) return;
      inFlight.current.add(key);
      readRef
        .current(key)
        .then(
          (v) => {
            setData((d) => ({ ...d, [key]: v }));
            setErrors((e) => without(e, key));
          },
          (err: unknown) => {
            toastError(err, label);
            setErrors((e) => ({ ...e, [key]: err }));
          },
        )
        .finally(() => inFlight.current.delete(key));
    },
    [label],
  );

  const wanted = [...new Set(keys)].join('\n');
  useEffect(() => {
    for (const key of wanted === '' ? [] : wanted.split('\n')) {
      if (has(data, key) || has(errors, key)) continue;
      fetchKey(key);
    }
  }, [wanted, data, errors, fetchKey]);

  const retry = useCallback((key: string) => setErrors((e) => without(e, key)), []);
  return { data, errors, retry, reload: fetchKey };
}

/** A run as read, with where the answer came from (captured at read time:
 * the client decides demo vs live per call). */
interface RunRead {
  run: RunDetail;
  demo: boolean;
}

const readRun = async (id: string): Promise<RunRead> => {
  const demo = isMockActive();
  const run = await getRun(id);
  return { run, demo };
};

const heatKey = (id: string, field: HeatField, seed: number | null): string =>
  `${id}|${field}|${seed ?? ''}`;

const readHeatmap = (key: string): Promise<Heatmap> => {
  const [id, field, seed] = key.split('|');
  return getRunHeatmap(id, field as HeatField, seed === '' ? undefined : Number(seed));
};

/* --------------------------- small pieces ---------------------------- */

function RetryButton({ onClick, label = 'Retry' }: { onClick: () => void; label?: string }): JSX.Element {
  return (
    <button type="button" className="btn sm" onClick={onClick}>
      <Icon name="refresh-cw" size={14} />
      {label}
    </button>
  );
}

/** One run's cell of a metric row: mean, its interval and n, or why there is
 * none. The mean is alone in its element. */
function StatCell({ side, digits }: { side: SideStat; digits: number }): JSX.Element {
  if (side.kind === 'not_recorded') {
    return (
      <span className="compare-absent" title={NOT_RECORDED_TITLE}>
        {NOT_RECORDED_LABEL}
      </span>
    );
  }
  if (side.kind === 'no_observations') {
    return (
      <>
        <span className="compare-absent" title={NO_OBSERVATIONS_TITLE}>
          {NO_OBSERVATIONS_LABEL}
        </span>
        <span className="compare-ci">n=0</span>
      </>
    );
  }
  const { stat } = side;
  return (
    <>
      <span className="compare-mean">{formatNumber(side.mean, digits)}</span>
      <span className="compare-ci">
        95% CI {formatNumber(stat.lo95, digits)} – {formatNumber(stat.hi95, digits)} · n={stat.n}
      </span>
      {stat.underpowered && (
        <span
          className="tag underpowered"
          title={`n=${stat.n} < 20 replicates — below reporting standard`}
        >
          UNDERPOWERED
        </span>
      )}
    </>
  );
}

/** The provenance badges of a run, as Run detail shows them. */
function RunBadges({ run, demo }: { run: RunDetail; demo: boolean }): JSX.Element {
  return (
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
  );
}

/* ------------------------------- view -------------------------------- */

export function CompareView(): JSX.Element {
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const ids: Record<Side, string> = {
    a: searchParams.get('a') ?? '',
    b: searchParams.get('b') ?? '',
  };
  const field: HeatField = searchParams.get('field') === 'density' ? 'density' : 'speed';
  const filters = useMemo(() => parseRunFilters(location.search), [location.search]);

  /** Change some query parameters, keeping the rest (filters, field). */
  const setParams = (changes: Record<string, string | null>): void => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(changes)) {
          if (v === null || v === '') next.delete(k);
          else next.set(k, v);
        }
        return next;
      },
      { replace: true },
    );
  };
  const setFilters = (next: RunFilters): void => {
    // runFiltersSearch keeps every non-filter parameter (a, b, field)
    setSearchParams(new URLSearchParams(runFiltersSearch(location.search, next)), { replace: true });
  };

  /* ---- the runs to pick from, and the scenario names ---- */

  const [list, setList] = useState<RunSummary[] | null>(null);
  const [listDemo, setListDemo] = useState(false);
  const [listError, setListError] = useState<unknown>(null);
  const loadList = useCallback(async () => {
    const demo = isMockActive();
    try {
      const rows = await listRuns();
      setList(rows);
      setListDemo(demo);
      setListError(null);
    } catch (err) {
      toastError(err, 'runs');
      setListError(err);
    }
  }, []);

  const [scenarios, setScenarios] = useState<ScenarioSummary[] | null>(null);
  const [scenariosFailed, setScenariosFailed] = useState(false);
  const loadScenarios = useCallback(async () => {
    try {
      setScenarios(await listScenarios());
      setScenariosFailed(false);
    } catch {
      // names fall back to ids, and setups say the scenario is unknown
      setScenariosFailed(true);
    }
  }, []);

  useEffect(() => {
    void loadList();
    void loadScenarios();
  }, [loadList, loadScenarios]);

  const scenarioById = useMemo(() => {
    const m = new Map<string, ScenarioSummary>();
    for (const s of scenarios ?? []) m.set(s.scenario_id, s);
    return m;
  }, [scenarios]);
  const scenarioNames = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of scenarios ?? []) m.set(s.scenario_id, s.name);
    return m;
  }, [scenarios]);

  const finished = useMemo(() => (list ?? []).filter((r) => r.status === 'done'), [list]);
  // the status dimension does not apply: only finished runs are offered
  const pickFilters: RunFilters = { ...filters, status: [] };
  const offered = useMemo(
    () =>
      finished.filter(
        (r) =>
          (filters.tier.length === 0 || filters.tier.includes(r.tier)) &&
          (filters.scenario.length === 0 || filters.scenario.includes(r.scenario_id)),
      ),
    [finished, filters.tier, filters.scenario],
  );

  /* ---- the two runs, their metrics, sweeps and fields ---- */

  const selected = SIDES.map((s) => ids[s]).filter((id) => id !== '');
  const runs = useKeyedReads(selected, readRun, 'run');
  const runOf = (side: Side): RunRead | undefined =>
    ids[side] ? runs.data[ids[side]] : undefined;
  const doneIds = selected.filter((id) => runs.data[id]?.run.status === 'done');
  const metrics = useKeyedReads<RunMetrics>(doneIds, getRunMetrics, 'metrics');
  const sweepIds = selected
    .map((id) => runs.data[id]?.run.sweep_id ?? null)
    .filter((s): s is string => typeof s === 'string' && s !== '');
  const sweeps = useKeyedReads<SweepDetail>(sweepIds, getSweep, 'sweep');

  const ra = runOf('a');
  const rb = runOf('b');
  const bothDone = ra?.run.status === 'done' && rb?.run.status === 'done';
  // both fields from one replicate when the runs share a seed, so the two
  // plots differ by configuration rather than by demand noise
  const seed = bothDone ? sharedSeed(ra.run.seeds, rb.run.seeds) : null;
  const heatKeyOf = (side: Side): string | null => {
    const r = runOf(side)?.run;
    if (!bothDone || !r) return null;
    // name the seed only when it is not the run's default (its first)
    return heatKey(r.run_id, field, seed !== null && seed !== r.seeds[0] ? seed : null);
  };
  const heatKeys = SIDES.map(heatKeyOf).filter((k): k is string => k !== null);
  const heatmaps = useKeyedReads<Heatmap>(heatKeys, readHeatmap, 'heatmap');

  const sideOf = (side: Side): CompareSide | null => {
    const r = runOf(side);
    return r
      ? { run: r.run, scenarioName: runScenarioName(r.run, scenarioNames), demo: r.demo }
      : null;
  };
  const sa = sideOf('a');
  const sb = sideOf('b');
  const notes = sa && sb ? compareNotes(sa, sb) : [];
  const warnings = notes.filter((n) => n.tone === 'warning');
  const infos = notes.filter((n) => n.tone === 'info');

  const swap = (): void => setParams({ a: ids.b, b: ids.a });

  /* ------------------------------ pickers ----------------------------- */

  const optionFor = (r: RunSummary): JSX.Element => (
    <option key={r.run_id} value={r.run_id}>
      {runOptionLabel(r, runScenarioName(r, scenarioNames), listDemo)}
      {/* a deep-linked run that is not finished says so in the list too */}
      {r.status === 'done' ? '' : ` — ${r.status}`}
    </option>
  );

  const renderPicker = (side: Side): JSX.Element => {
    const id = ids[side];
    const inList = id === '' || offered.some((r) => r.run_id === id);
    // a chosen run stays selectable when the filters (or the list) leave it
    // out, so the select never shows one run while the URL names another
    const extra = id !== '' && !inList ? (list ?? []).find((r) => r.run_id === id) : undefined;
    const read = runOf(side);
    const err = id ? runs.errors[id] : undefined;
    return (
      <div className="compare-side" key={side} role="group" aria-labelledby={`cmp-${side}-label`}>
        <div className="field">
          <label htmlFor={`cmp-${side}`} id={`cmp-${side}-label`}>
            Run {SIDE_NAME[side]}
          </label>
          <select
            id={`cmp-${side}`}
            className="input"
            value={id}
            aria-describedby={`cmp-${side}-help`}
            onChange={(e) => setParams({ [side]: e.target.value })}
          >
            <option value="">
              {list === null && listError === null ? 'Loading finished runs…' : 'Choose a finished run…'}
            </option>
            {offered.map(optionFor)}
            {extra && optionFor(extra)}
            {id !== '' && !inList && !extra && (
              <option value={id}>{list === null ? id : `${id} (not among the listed runs)`}</option>
            )}
          </select>
          <span className="field-help" id={`cmp-${side}-help`}>
            {SIDE_HINT[side]}
          </span>
        </div>
        {id === '' ? null : err !== undefined ? (
          <ErrorCallout
            error={err}
            title={`Run ${SIDE_NAME[side]} (${id}) could not be loaded.`}
            onRetry={() => runs.retry(id)}
          />
        ) : !read ? (
          <div className="compare-facts-loading" aria-busy="true">
            <Skeleton width="60%" height={14} />
            <Skeleton width="100%" height={96} radius="sm" />
          </div>
        ) : (
          <RunFacts
            read={read}
            scenarioName={runScenarioName(read.run, scenarioNames)}
            scenario={scenarioById.get(read.run.scenario_id)}
            scenariosState={scenarios !== null ? 'loaded' : scenariosFailed ? 'failed' : 'loading'}
            sweep={
              read.run.sweep_id
                ? has(sweeps.errors, read.run.sweep_id)
                  ? null
                  : sweeps.data[read.run.sweep_id]
                : undefined
            }
          />
        )}
      </div>
    );
  };

  const filterBar =
    finished.length > 0 &&
    (['tier', 'scenario'] as RunFilterKey[]).some(
      (key) => runFacet(finished, pickFilters, key).values.length > 1 || filters[key].length > 0,
    ) ? (
      <div className="runs-filters" role="group" aria-label="Filter the runs offered">
        {(['tier', 'scenario'] as RunFilterKey[]).map((key) => {
          const facet = runFacet(finished, pickFilters, key);
          const chosen = filters[key] as string[];
          if (facet.values.length < 2 && chosen.length === 0) return null;
          return (
            <RunFilterGroup
              key={key}
              id={key}
              idPrefix="cmp-filter"
              label={key === 'tier' ? 'Tier' : 'Scenario'}
              facet={facet}
              chosen={chosen}
              labelFor={(v) =>
                key === 'tier'
                  ? v === 'macro'
                    ? 'Macro'
                    : 'Micro'
                  : (scenarioNames.get(v) ??
                    finished.find((r) => r.scenario_id === v)?.scenario_name?.split('·')[0].trim() ??
                    v)
              }
              titleFor={
                key === 'scenario'
                  ? (v) => v
                  : (v) => (v === 'macro' ? 'CTM screening runs' : 'SUMO microsimulation runs')
              }
              onAll={() => setFilters({ ...pickFilters, [key]: [] })}
              onToggle={(v) => setFilters(toggleRunFilter(pickFilters, key, v))}
            />
          );
        })}
        {hasRunFilters(pickFilters) && (
          <div className="runs-filter-summary">
            <span>
              Offering <span className="mono">{offered.length}</span> of{' '}
              <span className="mono">{finished.length}</span>
            </span>
            <button type="button" className="btn link sm" onClick={() => setFilters(NO_RUN_FILTERS)}>
              Clear filters
            </button>
          </div>
        )}
      </div>
    ) : null;

  /* --------------------------- results -------------------------------- */

  /** A chosen run that cannot be compared yet (not finished, or failed). */
  const notReady = SIDES.flatMap((side) => {
    const r = runOf(side)?.run;
    if (!r || r.status === 'done') return [];
    return [{ side, run: r }];
  });

  let results: ReactNode = null;
  if (!ids.a || !ids.b) {
    results = (
      <EmptyState
        title={!ids.a && !ids.b ? 'No runs to compare yet.' : `Run ${ids.a ? 'B' : 'A'} is not chosen yet.`}
        description={
          !ids.a && !ids.b
            ? 'Pick run A and run B above; only finished runs are offered.'
            : `Pick run ${ids.a ? 'B' : 'A'} above to see the two side by side.`
        }
      />
    );
  } else if (notReady.length > 0) {
    results = (
      <div className="compare-notready">
        {notReady.map(({ side, run }) => (
          <Callout
            key={side}
            tone={run.status === 'failed' ? 'danger' : 'warning'}
            title={`Run ${SIDE_NAME[side]} (${run.run_id}) is ${run.status}.`}
            action={
              run.status === 'failed' ? undefined : (
                <RetryButton label="Reload" onClick={() => runs.reload(run.run_id)} />
              )
            }
          >
            {run.status === 'failed' ? (
              <>
                It has no results to compare. The service reported:{' '}
                <span className="mono">{failureReason(run.error, run.error_kind)}</span>
              </>
            ) : (
              <>
                Compare reads finished runs only: metrics and fields appear once all{' '}
                {run.progress.total_replicates} replicates finish. This page does not follow its
                progress; reload when it is done.
              </>
            )}
          </Callout>
        ))}
      </div>
    );
  } else if (bothDone && sa && sb) {
    results = (
      <>
        <MetricsPanel
          a={sa}
          b={sb}
          metricsA={metrics.data[sa.run.run_id]}
          metricsB={metrics.data[sb.run.run_id]}
          errorA={metrics.errors[sa.run.run_id]}
          errorB={metrics.errors[sb.run.run_id]}
          onRetry={(id) => metrics.retry(id)}
        />
        <FieldsPanel
          a={sa}
          b={sb}
          field={field}
          onField={(f) => setParams({ field: f === 'speed' ? null : f })}
          heatA={heatKeyOf('a')}
          heatB={heatKeyOf('b')}
          heatmaps={heatmaps}
        />
      </>
    );
  }

  return (
    <div className="view compare">
      <PageHeader
        title="Compare"
        documentTitle="Compare"
        description="Two finished runs side by side: each headline metric with its 95% CI, the difference B − A, and both space–time fields on one scale."
        actions={
          <button
            type="button"
            className="btn"
            onClick={swap}
            disabled={!ids.a && !ids.b}
            title="Swap the two runs: B − A changes sign"
          >
            <Icon name="arrow-left-right" size={16} />
            Swap A and B
          </button>
        }
      />

      {listDemo && (
        <Callout tone="warning">
          The API is unreachable, so these are built-in demo runs: no server has computed them, their
          hashes exist nowhere, and nothing here is a result.
        </Callout>
      )}

      <section className="panel compare-pick-panel" aria-labelledby="cmp-pick-title">
        <div className="panel-head">
          <div>
            <h2 className="panel-title" id="cmp-pick-title">
              Runs
            </h2>
            <span className="panel-sub">
              Finished runs only. Each shows what it ran, as the API reports it.
            </span>
          </div>
        </div>
        {filterBar}
        <div className="panel-body">
          {listError !== null && list === null && (
            <div className="compare-list-error">
              <ErrorCallout
                error={listError}
                title="The runs could not be listed."
                onRetry={() => void loadList()}
              />
            </div>
          )}
          {list !== null && finished.length === 0 && !ids.a && !ids.b ? (
            <EmptyState
              title="No finished runs yet."
              description="Runs appear here once all their replicates finish."
              action={
                <Link className="btn sm" to="/runs">
                  Open Runs
                </Link>
              }
            />
          ) : (
            <div className="compare-pick-grid">{SIDES.map(renderPicker)}</div>
          )}
        </div>
      </section>

      {warnings.length > 0 && (
        <Callout tone="warning" title="What else differs between A and B" role="note">
          <ul className="compare-notes">
            {warnings.map((n) => (
              <li key={n.id} data-note={n.id}>
                {n.text}
              </li>
            ))}
          </ul>
        </Callout>
      )}
      {infos.length > 0 && (
        <Callout tone="info" role="note">
          <ul className="compare-notes">
            {infos.map((n) => (
              <li key={n.id} data-note={n.id}>
                {n.text}
              </li>
            ))}
          </ul>
        </Callout>
      )}

      {results}
    </div>
  );
}

/* --------------------------- run facts ------------------------------- */

function RunFacts({
  read,
  scenarioName,
  scenario,
  scenariosState,
  sweep,
}: {
  read: RunRead;
  scenarioName: string;
  scenario: ScenarioSummary | undefined;
  /** Whether the scenario list (a run's setup outside a sweep) has answered. */
  scenariosState: 'loading' | 'loaded' | 'failed';
  /** undefined while read (or for a run outside a sweep); null when it failed. */
  sweep: SweepDetail | null | undefined;
}): JSX.Element {
  const { run, demo } = read;
  const setup = resolveRunSetup(run, scenario, sweep);
  const outsideSweep = !setup.known && !run.sweep_id;
  const unknownReason =
    outsideSweep && scenariosState === 'loading'
      ? 'Reading the scenario list…'
      : outsideSweep && scenariosState === 'failed'
        ? 'The scenario list could not be read, so this run’s scenario config is unknown.'
        : !setup.known
          ? setup.reason
          : null;
  return (
    <div className="compare-facts">
      <div className="compare-run-head">
        <Link className="compare-run-id mono" to={`/runs/${encodeURIComponent(run.run_id)}`}>
          {run.run_id}
        </Link>
        <RunBadges run={run} demo={demo} />
      </div>
      <dl className="fact-list">
        <div className="fact">
          <dt>Scenario</dt>
          <dd title={run.scenario_id}>{scenarioName}</dd>
        </div>
        <div className="fact">
          <dt>Controller</dt>
          <dd>{setup.known ? describeController(setup.setup) : 'not reported'}</dd>
        </div>
        <div className="fact">
          <dt>Penetration · compliance</dt>
          <dd className="mono">{setup.known ? describePenComp(setup.setup) : 'not reported'}</dd>
        </div>
        {setup.known && setup.setup.strategy && setup.setup.strategy !== 'none' && (
          <div className="fact">
            <dt>Strategy</dt>
            <dd className="mono">{setup.setup.strategy}</dd>
          </div>
        )}
        <div className="fact">
          <dt>Seeds</dt>
          <dd className="mono" title={run.seeds.join(' · ')}>
            {describeSeeds(run.seeds)}
          </dd>
        </div>
        <div className="fact">
          <dt>Config</dt>
          <dd>
            {demo ? (
              <span className="mono" title={DEMO_ROW_TITLE}>
                {DEMO_HASH_LABEL}
              </span>
            ) : (
              <HashValue value={run.config_hash} label={`config hash of ${run.run_id}`} />
            )}
          </dd>
        </div>
      </dl>
      <p className="compare-setup-source">
        {setup.known ? (
          <>Controller {setup.source}.</>
        ) : (
          <>
            {unknownReason}
            {setup.scenarioDefault &&
              ` The scenario’s own setting is ${describeController(setup.scenarioDefault)}, ${describePenComp(setup.scenarioDefault)}.`}
          </>
        )}
      </p>
    </div>
  );
}

/* ---------------------------- metrics -------------------------------- */

function MetricsPanel({
  a,
  b,
  metricsA,
  metricsB,
  errorA,
  errorB,
  onRetry,
}: {
  a: CompareSide;
  b: CompareSide;
  metricsA: RunMetrics | undefined;
  metricsB: RunMetrics | undefined;
  errorA: unknown;
  errorB: unknown;
  onRetry: (runId: string) => void;
}): JSX.Element {
  const failed = [
    { side: 'A', id: a.run.run_id, error: errorA },
    { side: 'B', id: b.run.run_id, error: errorB },
  ].filter((f) => f.error !== undefined);
  const loaded = metricsA !== undefined && metricsB !== undefined;
  const sections = loaded ? compareSections(metricsA.aggregate, metricsB.aggregate) : [];

  const head = (
    <thead>
      <tr>
        <th scope="col">Metric</th>
        <th scope="col" className="num">
          A <span className="compare-col-id mono">{a.run.run_id}</span>
        </th>
        <th scope="col" className="num">
          B <span className="compare-col-id mono">{b.run.run_id}</span>
        </th>
        <th scope="col" className="num">
          B − A
        </th>
        <th scope="col" className="num">
          B vs A
        </th>
      </tr>
    </thead>
  );

  let body: ReactNode;
  if (failed.length > 0) {
    body = (
      <div className="compare-errors">
        {failed.map((f) => (
          <ErrorCallout
            key={f.side}
            error={f.error}
            title={`The metrics of run ${f.side} (${f.id}) could not be loaded.`}
            onRetry={() => onRetry(f.id)}
          />
        ))}
      </div>
    );
  } else if (!loaded) {
    body = (
      <div className="table-wrap" aria-busy="true">
        <table className="data compare-table" aria-label="metric comparison">
          {head}
          <tbody>
            <SkeletonRows rows={METRIC_SKELETON_ROWS} columns={METRIC_COLUMNS} />
          </tbody>
        </table>
      </div>
    );
  } else {
    body = (
      <div className="table-wrap">
        <table className="data compare-table" aria-label="metric comparison">
          {head}
          {sections.map((s) => (
            <tbody key={s.group}>
              <tr className="compare-group">
                <th scope="rowgroup" colSpan={METRIC_COLUMNS}>
                  {s.title}
                </th>
              </tr>
              {s.keys.map((k) => {
                const def = metricDef(k);
                const sa = sideStat(metricsA.aggregate, k);
                const sb = sideStat(metricsB.aggregate, k);
                const d = metricDifference(sa, sb);
                return (
                  <tr key={k} data-metric={k}>
                    <th scope="row" className="compare-metric">
                      <span className="compare-metric-label">{def.label}</span>
                      {def.unit && <span className="compare-metric-unit">{def.unit}</span>}
                      {def.good !== 'neutral' && (
                        <span className="compare-metric-good">
                          {def.good === 'down' ? 'lower is better' : 'higher is better'}
                        </span>
                      )}
                    </th>
                    <td className="num compare-stat">
                      <StatCell side={sa} digits={def.digits} />
                    </td>
                    <td className="num compare-stat">
                      <StatCell side={sb} digits={def.digits} />
                    </td>
                    {d.kind === 'difference' ? (
                      <>
                        <td className="num compare-diff">{formatSigned(d.diff, def.digits)}</td>
                        <td className="num compare-rel">
                          {d.rel === null ? (
                            <>
                              <span aria-hidden="true" title="A’s mean is 0, so there is no ratio">
                                —
                              </span>
                              <span className="visually-hidden">No ratio: A’s mean is 0.</span>
                            </>
                          ) : (
                            formatRelative(d.rel)
                          )}
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="num compare-diff compare-none" title={d.reason}>
                          <span aria-hidden="true">—</span>
                          <span className="visually-hidden">{d.reason}</span>
                        </td>
                        <td className="num compare-rel compare-none" title={d.reason}>
                          <span aria-hidden="true">—</span>
                          <span className="visually-hidden">{d.reason}</span>
                        </td>
                      </>
                    )}
                  </tr>
                );
              })}
            </tbody>
          ))}
        </table>
      </div>
    );
  }

  const nA = metricsA ? (metricsA.n_replicates ?? metricsA.replicates.length) : null;
  const nB = metricsB ? (metricsB.n_replicates ?? metricsB.replicates.length) : null;
  return (
    <section className="panel compare-metrics-panel" aria-labelledby="cmp-metrics-title">
      <div className="panel-head">
        <div>
          <h2 className="panel-title" id="cmp-metrics-title">
            Metrics · mean and 95% CI per run, and B − A
          </h2>
          {nA !== null && nB !== null && (
            <span className="panel-sub">
              A over <span className="mono">{nA}</span> replicates, B over{' '}
              <span className="mono">{nB}</span>
            </span>
          )}
        </div>
      </div>
      <div className="panel-body">{body}</div>
      <div className="panel-foot">
        <p className="compare-diff-note">{DIFFERENCE_NOTE}</p>
      </div>
    </section>
  );
}

/* ----------------------------- fields -------------------------------- */

function FieldsPanel({
  a,
  b,
  field,
  onField,
  heatA,
  heatB,
  heatmaps,
}: {
  a: CompareSide;
  b: CompareSide;
  field: HeatField;
  onField: (f: HeatField) => void;
  heatA: string | null;
  heatB: string | null;
  heatmaps: KeyedReads<Heatmap>;
}): JSX.Element {
  const sides = [
    { name: 'A' as const, side: a, key: heatA },
    { name: 'B' as const, side: b, key: heatB },
  ].map((s) => ({
    ...s,
    heatmap: s.key ? heatmaps.data[s.key] : undefined,
    error: s.key ? heatmaps.errors[s.key] : undefined,
  }));
  // both answered (a field or an error): draw together, so neither plot
  // jumps when the other's axes arrive
  const settled = sides.every((s) => s.heatmap !== undefined || s.error !== undefined);
  const drawable = sides
    .map((s) => s.heatmap)
    .filter((h): h is Heatmap => h !== undefined && !isEmptyHeatmap(h));
  const extent = settled ? unionExtent(drawable) : null;
  const spansDiffer =
    drawable.length === 2 && !sameExtent(heatmapExtent(drawable[0]), heatmapExtent(drawable[1]));
  const [ha, hb] = sides.map((s) => s.heatmap);
  const seedA = ha?.seed;
  const seedB = hb?.seed;

  return (
    <section className="panel compare-field-panel" aria-labelledby="cmp-field-title">
      <div className="panel-head">
        <h2 className="panel-title" id="cmp-field-title">
          Space–time fields
        </h2>
        <FieldTabs field={field} onChange={onField} idPrefix="cmp-field" panelId="cmp-field-tabpanel" />
      </div>
      <div
        className="panel-body compare-field-body"
        id="cmp-field-tabpanel"
        role="tabpanel"
        aria-labelledby={fieldTabId('cmp-field', field)}
      >
        <div className="compare-heat-grid">
          {sides.map(({ name, side, heatmap, error, key }) => {
            const run = side.run;
            const at = heatmap?.seed === undefined ? -1 : run.seeds.indexOf(heatmap.seed);
            const empty = heatmap ? isEmptyHeatmap(heatmap) : false;
            return (
              <div className="compare-heat-side" key={name}>
                <div className="compare-heat-head">
                  <h3 className="compare-heat-title">
                    {name} · <span className="mono">{run.run_id}</span>
                  </h3>
                  {heatmap && (
                    <span className="compare-heat-seed mono">
                      {heatmap.seed === undefined
                        ? 'seed not reported'
                        : `seed ${heatmap.seed}${at >= 0 ? ` · replicate ${at + 1} of ${run.seeds.length}` : ''}`}
                    </span>
                  )}
                  <span className="spacer" />
                  <button
                    type="button"
                    className="btn ghost sm"
                    aria-label={`Download CSV of run ${name}`}
                    disabled={!heatmap || empty}
                    title={
                      heatmap && !empty
                        ? `Run ${name}’s binned ${field} field in SI units, as plotted: one replicate, headed by its provenance`
                        : 'Available once the field has loaded'
                    }
                    onClick={() =>
                      heatmap && downloadHeatmapCSV(heatmap, field, { runId: run.run_id, run, demo: side.demo })
                    }
                  >
                    <Icon name="download" size={14} />
                    Download CSV
                  </button>
                </div>
                {error !== undefined ? (
                  <div className="heatmap-state">
                    <ErrorCallout
                      error={error}
                      title={`Run ${name}’s ${field} field could not be loaded.`}
                      onRetry={() => key && heatmaps.retry(key)}
                    />
                  </div>
                ) : !heatmap || !settled ? (
                  <div className="heatmap-state" aria-busy="true">
                    <div className="skeleton-frame">
                      <Skeleton radius="xs" />
                      <span className="skeleton-label">Loading the {field} field…</span>
                    </div>
                  </div>
                ) : empty ? (
                  <div className="heatmap-state">
                    <EmptyState
                      title="This field has no bins."
                      description="The service returned 0 time or position bins for this run."
                    />
                  </div>
                ) : (
                  <HeatmapCanvas heatmap={heatmap} field={field} extent={extent} showLegend={false} />
                )}
              </div>
            );
          })}
        </div>
        <RampLegend field={field} />
        <div className="compare-field-notes">
          <p className="heatmap-caption">
            Both fields share one colour scale and the same axes. {HEATMAP_CAPTION}
            {spansDiffer && ' The runs cover different spans: the blank part of a plot lies outside that run’s field.'}
          </p>
          {seedA !== undefined && seedB !== undefined && (
            <p className="heatmap-caption compare-seed-note">
              {seedA === seedB
                ? `Both fields are seed ${seedA}: one replicate each, the same demand realisation in both runs, not a mean over the replicates.`
                : `A shows seed ${seedA} and B seed ${seedB}: the runs share no seed, so the fields differ by demand noise as well as by configuration. Each is one replicate, not a mean.`}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
