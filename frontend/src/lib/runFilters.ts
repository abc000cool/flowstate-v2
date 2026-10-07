/** Client-side filters of the Runs table (docs/design/DASHBOARD_DESIGN.md
 * §10.3 P2): status, tier and scenario, over the rows already loaded — no
 * request is made for a filter.
 *
 * Within a dimension the chosen values are alternatives (running or queued);
 * across dimensions they all apply (running, and micro). Each chip counts the
 * rows it would show given the other dimensions' choices (faceted counts), so
 * a count is what the click would produce.
 *
 * The state lives in the URL so a filtered view can be shared:
 * `?status=running,failed&tier=micro&scenario=scn_72b91153417c`. Repeated
 * keys (`?status=running&status=failed`) are read too. Unknown status and
 * tier words are ignored; a scenario id is kept whether or not a loaded row
 * has it, so a shared link to a scenario with no runs yet says "no runs match"
 * instead of silently showing everything. */

import type { RunStatus, RunSummary, Tier } from '../api/types';

export const RUN_STATUSES: RunStatus[] = ['queued', 'running', 'done', 'failed'];
export const RUN_TIERS: Tier[] = ['micro', 'macro'];

export interface RunFilters {
  status: RunStatus[];
  tier: Tier[];
  scenario: string[];
}

export type RunFilterKey = keyof RunFilters;

export const RUN_FILTER_KEYS: RunFilterKey[] = ['status', 'tier', 'scenario'];

export const NO_RUN_FILTERS: RunFilters = { status: [], tier: [], scenario: [] };

function listParam(params: URLSearchParams, key: string): string[] {
  const out: string[] = [];
  for (const raw of params.getAll(key)) {
    for (const v of raw.split(',')) {
      const t = v.trim();
      if (t !== '' && !out.includes(t)) out.push(t);
    }
  }
  return out;
}

/** The filters a query string asks for. */
export function parseRunFilters(search: string | URLSearchParams): RunFilters {
  const params = typeof search === 'string' ? new URLSearchParams(search) : search;
  return {
    status: RUN_STATUSES.filter((s) => listParam(params, 'status').includes(s)),
    tier: RUN_TIERS.filter((t) => listParam(params, 'tier').includes(t)),
    scenario: listParam(params, 'scenario'),
  };
}

/** The query string for `filters`, keeping every other parameter of `search`
 * as it was. Values are comma-joined (commas need no escaping in a query, so
 * the shared link stays readable); '' when nothing is left. */
export function runFiltersSearch(search: string, filters: RunFilters): string {
  const params = new URLSearchParams(search);
  const parts: string[] = [];
  for (const [k, v] of params) {
    if (!RUN_FILTER_KEYS.includes(k as RunFilterKey)) {
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
    }
  }
  for (const key of RUN_FILTER_KEYS) {
    const values = filters[key] as string[];
    if (values.length > 0) parts.push(`${key}=${values.map(encodeURIComponent).join(',')}`);
  }
  return parts.length > 0 ? `?${parts.join('&')}` : '';
}

export function hasRunFilters(filters: RunFilters): boolean {
  return RUN_FILTER_KEYS.some((k) => filters[k].length > 0);
}

function valueOf(run: RunSummary, key: RunFilterKey): string {
  if (key === 'status') return run.status;
  if (key === 'tier') return run.tier;
  return run.scenario_id;
}

/** Whether `run` passes every dimension but `except`. */
export function matchesRunFilters(
  run: RunSummary,
  filters: RunFilters,
  except?: RunFilterKey,
): boolean {
  return RUN_FILTER_KEYS.every((key) => {
    if (key === except) return true;
    const chosen = filters[key] as string[];
    return chosen.length === 0 || chosen.includes(valueOf(run, key));
  });
}

export function filterRuns(rows: RunSummary[], filters: RunFilters): RunSummary[] {
  return rows.filter((r) => matchesRunFilters(r, filters));
}

/** One dimension's chips: "All" and each value, with faceted counts. */
export interface RunFacet {
  /** Rows passing the other dimensions (the "All" chip's count). */
  total: number;
  /** Each value present in the loaded rows or chosen in the URL, in a stable
   * order (statuses and tiers in API order, scenarios by first appearance),
   * with the rows it would show. */
  values: { value: string; count: number }[];
}

export function runFacet(rows: RunSummary[], filters: RunFilters, key: RunFilterKey): RunFacet {
  const scoped = rows.filter((r) => matchesRunFilters(r, filters, key));
  const counts = new Map<string, number>();
  for (const r of scoped) counts.set(valueOf(r, key), (counts.get(valueOf(r, key)) ?? 0) + 1);
  const present = new Set<string>(rows.map((r) => valueOf(r, key)));
  for (const v of filters[key] as string[]) present.add(v);
  let order: string[];
  if (key === 'status') order = RUN_STATUSES.filter((s) => present.has(s));
  else if (key === 'tier') order = RUN_TIERS.filter((t) => present.has(t));
  else {
    order = [];
    for (const r of rows) if (!order.includes(r.scenario_id)) order.push(r.scenario_id);
    for (const v of filters.scenario) if (!order.includes(v)) order.push(v);
  }
  return {
    total: scoped.length,
    values: order.map((value) => ({ value, count: counts.get(value) ?? 0 })),
  };
}

/** `filters` with `value` toggled in (or out of) dimension `key`. */
export function toggleRunFilter(filters: RunFilters, key: RunFilterKey, value: string): RunFilters {
  const current = filters[key] as string[];
  let next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
  // statuses and tiers stay in API order, so equal filters give equal links
  if (key === 'status') next = RUN_STATUSES.filter((s) => next.includes(s));
  else if (key === 'tier') next = RUN_TIERS.filter((t) => next.includes(t));
  return { ...filters, [key]: next } as RunFilters;
}
