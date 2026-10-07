/** Runs filter chips (docs/design/DASHBOARD_DESIGN.md §10.3 P2): the pure
 * filter logic (URL parsing and writing, faceted counts), and the chips in
 * RunsView — toggle buttons with counts, synced to `?status=…&tier=…&
 * scenario=…` so a filtered view is a link, and an empty state that offers
 * to clear them. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RunSummary } from '../api/types';
import { clearAuthFailure, setOfflineFallback } from '../api/client';
import {
  filterRuns,
  hasRunFilters,
  NO_RUN_FILTERS,
  parseRunFilters,
  runFacet,
  runFiltersSearch,
  toggleRunFilter,
} from '../lib/runFilters';
import { RunsView } from '../views/RunsView';
import { auditA11y, formatViolations } from './a11y';

function row(id: string, status: RunSummary['status'], tier: RunSummary['tier'], scenario: string): RunSummary {
  return {
    run_id: id,
    scenario_id: scenario,
    status,
    tier,
    progress: { completed_replicates: status === 'done' ? 20 : 3, total_replicates: 20 },
    config_hash: `hash-${id}`,
    seeded: false,
    error: status === 'failed' ? 'Duration 1800 s leaves no measurement window' : null,
    error_kind: status === 'failed' ? 'ValueError' : null,
    created_at: '2026-10-01T00:00:00',
  };
}

const RUNS: RunSummary[] = [
  row('run-d1', 'done', 'micro', 'scn_i24'),
  row('run-d2', 'done', 'micro', 'scn_ring'),
  row('run-d3', 'done', 'macro', 'scn_i24'),
  row('run-r1', 'running', 'micro', 'scn_i24'),
  row('run-f1', 'failed', 'micro', 'scn_ring'),
];

/* ------------------------------ pure logic -------------------------------- */

describe('run filters: URL', () => {
  it('reads comma lists and repeated keys, and ignores unknown words', () => {
    expect(parseRunFilters('?status=failed,running&status=bogus&tier=micro&scenario=scn_a,scn_b')).toEqual({
      status: ['running', 'failed'],
      tier: ['micro'],
      scenario: ['scn_a', 'scn_b'],
    });
    expect(parseRunFilters('?status=running&status=done')).toMatchObject({ status: ['running', 'done'] });
    expect(parseRunFilters('')).toEqual(NO_RUN_FILTERS);
  });

  it('writes readable links and keeps other parameters', () => {
    const f = { status: ['running', 'failed'], tier: [], scenario: ['scn_i24'] } as never;
    expect(runFiltersSearch('?view=compact&status=done', f)).toBe(
      '?view=compact&status=running,failed&scenario=scn_i24',
    );
    expect(runFiltersSearch('?status=done', NO_RUN_FILTERS)).toBe('');
    // a round trip gives the same filters
    expect(parseRunFilters(runFiltersSearch('', f))).toEqual(f);
  });

  it('toggles a value, keeping statuses in API order', () => {
    let f = toggleRunFilter(NO_RUN_FILTERS, 'status', 'failed');
    f = toggleRunFilter(f, 'status', 'queued');
    expect(f.status).toEqual(['queued', 'failed']);
    expect(toggleRunFilter(f, 'status', 'queued').status).toEqual(['failed']);
    expect(hasRunFilters(f)).toBe(true);
    expect(hasRunFilters(NO_RUN_FILTERS)).toBe(false);
  });
});

describe('run filters: matching and counts', () => {
  it('ORs within a dimension and ANDs across them', () => {
    const f = { status: ['done', 'running'], tier: ['micro'], scenario: [] } as never;
    expect(filterRuns(RUNS, f).map((r) => r.run_id)).toEqual(['run-d1', 'run-d2', 'run-r1']);
  });

  it('counts each chip against the other dimensions only', () => {
    const f = { status: ['done'], tier: ['micro'], scenario: [] } as never;
    // status chips ignore the status choice but honour tier=micro
    expect(runFacet(RUNS, f, 'status')).toEqual({
      total: 4,
      values: [
        { value: 'running', count: 1 },
        { value: 'done', count: 2 },
        { value: 'failed', count: 1 },
      ],
    });
    // tier chips honour status=done
    expect(runFacet(RUNS, f, 'tier')).toEqual({
      total: 3,
      values: [
        { value: 'micro', count: 2 },
        { value: 'macro', count: 1 },
      ],
    });
  });

  it('keeps a chosen value nobody has, at zero, so it can be cleared', () => {
    const f = { status: [], tier: [], scenario: ['scn_gone'] } as never;
    expect(runFacet(RUNS, f, 'scenario').values).toEqual([
      { value: 'scn_i24', count: 3 },
      { value: 'scn_ring', count: 2 },
      { value: 'scn_gone', count: 0 },
    ]);
  });
});

/* ------------------------------ in RunsView -------------------------------- */

const SCENARIOS = [
  { scenario_id: 'scn_i24', name: 'i24_replica', config_hash: 'c1', created_at: 't' },
  { scenario_id: 'scn_ring', name: 'ring_sugiyama', config_hash: 'c2', created_at: 't' },
];

function Where(): JSX.Element {
  const { search } = useLocation();
  return <div data-testid="where">{search}</div>;
}

function renderRuns(path = '/runs'): void {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/runs" element={<RunsView />} />
      </Routes>
      <Where />
    </MemoryRouter>,
  );
}

function chip(group: string, name: RegExp): HTMLElement {
  const bar = screen.getByRole('group', { name: 'Filter runs' });
  return within(within(bar).getByRole('group', { name: group })).getByRole('button', { name });
}

async function table(): Promise<HTMLElement> {
  const t = await screen.findByRole('table', { name: 'runs' }, { timeout: 4000 });
  await within(t).findAllByText(/^run-/, {}, { timeout: 4000 });
  return t;
}

const ids = (t: HTMLElement): string[] => within(t).queryAllByText(/^run-/).map((a) => a.textContent ?? '');

describe('RunsView filter chips', () => {
  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        const json = (body: unknown): Response =>
          new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
        if (url.endsWith('/scenarios/preset')) return json([]);
        if (url.endsWith('/scenarios')) return json(SCENARIOS);
        if (url.endsWith('/runs')) return json(RUNS);
        return new Response('{}', { status: 404 });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows toggle chips with counts for status, tier and scenario', async () => {
    renderRuns();
    const t = await table();
    expect(ids(t)).toHaveLength(5);
    const all = chip('Status', /^All/);
    expect(all).toHaveAttribute('aria-pressed', 'true');
    expect(all).toHaveTextContent('All5');
    expect(chip('Status', /^Done/)).toHaveTextContent('Done3');
    expect(chip('Status', /^Running/)).toHaveAttribute('aria-pressed', 'false');
    expect(chip('Tier', /^Macro/)).toHaveTextContent('Macro1');
    // scenario chips are named, not ids, with the id in the title
    await waitFor(() => expect(chip('Scenario', /^i24_replica/)).toHaveTextContent('i24_replica3'));
    expect(chip('Scenario', /^i24_replica/)).toHaveAttribute('title', 'scn_i24');
    // the verbatim lower-case status pills are not confused with the chips
    expect(within(t).getAllByText('done')).toHaveLength(3);
  });

  it('filters the loaded rows and writes the choice to the URL', async () => {
    renderRuns();
    const t = await table();
    fireEvent.click(chip('Status', /^Failed/));
    expect(screen.getByTestId('where')).toHaveTextContent('?status=failed');
    expect(ids(t)).toEqual(['run-f1']);
    expect(chip('Status', /^Failed/)).toHaveAttribute('aria-pressed', 'true');
    expect(chip('Status', /^All/)).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(/^Showing/)).toHaveTextContent('Showing 1 of 5');

    fireEvent.click(chip('Status', /^Running/));
    expect(screen.getByTestId('where')).toHaveTextContent('?status=running,failed');
    expect(ids(t)).toEqual(['run-r1', 'run-f1']);

    fireEvent.click(chip('Tier', /^Micro/));
    expect(screen.getByTestId('where')).toHaveTextContent('?status=running,failed&tier=micro');
    // faceted: the tier chips count only running or failed runs
    expect(chip('Tier', /^Micro/)).toHaveTextContent('Micro2');
    expect(chip('Tier', /^Macro/)).toHaveTextContent('Macro0');

    fireEvent.click(chip('Status', /^All/));
    expect(screen.getByTestId('where')).toHaveTextContent('?tier=micro');
    expect(ids(t)).toEqual(['run-d1', 'run-d2', 'run-r1', 'run-f1']);
  });

  it('opens filtered from a shared link', async () => {
    renderRuns('/runs?status=done&scenario=scn_i24');
    const t = await table();
    expect(ids(t)).toEqual(['run-d1', 'run-d3']);
    expect(chip('Status', /^Done/)).toHaveAttribute('aria-pressed', 'true');
    expect(chip('Scenario', /i24_replica|scn_i24/)).toHaveAttribute('aria-pressed', 'true');
  });

  it('says when nothing matches and clears from there', async () => {
    renderRuns('/runs?status=failed&tier=macro');
    const t = await screen.findByRole('table', { name: 'runs' }, { timeout: 4000 });
    expect(await within(t).findByText('No runs match these filters.', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(within(t).getByText('Clear them to see all 5 runs.')).toBeInTheDocument();
    expect(formatViolations(auditA11y(document.body))).toEqual([]);
    fireEvent.click(within(t).getByRole('button', { name: 'Clear filters' }));
    expect(screen.getByTestId('where')).toHaveTextContent(/^$/);
    expect(ids(t)).toHaveLength(5);
    expect(screen.queryByText(/^Showing/)).toBeNull();
  });

  it('keeps a filter on a scenario with no runs visible, so it can be undone', async () => {
    renderRuns('/runs?scenario=scn_gone');
    const t = await screen.findByRole('table', { name: 'runs' }, { timeout: 4000 });
    await within(t).findByText('No runs match these filters.', {}, { timeout: 4000 });
    const gone = chip('Scenario', /^scn_gone/);
    expect(gone).toHaveAttribute('aria-pressed', 'true');
    expect(gone).toHaveTextContent('scn_gone0');
    fireEvent.click(gone);
    expect(ids(t)).toHaveLength(5);
  });

  it('has no serious or critical accessibility violations with filters on', async () => {
    renderRuns('/runs?status=done');
    await table();
    expect(formatViolations(auditA11y(document.body))).toEqual([]);
  });
});
