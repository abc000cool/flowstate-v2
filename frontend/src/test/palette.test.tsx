/** The command palette (docs/design/DASHBOARD_DESIGN.md §9.19, §11.3): fuzzy
 * ranking, the ⌘K / Ctrl+K shortcut and the top-bar button, the combobox +
 * listbox keyboard model (arrows wrap, Enter runs, Escape closes, Tab stays
 * in), focus restored or handed to the new page, inert background, every
 * command kind, recent runs from the shared runs cache with no fetch of its
 * own, and the ring-launch command that prefills the Runs launcher without
 * launching. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RunSummary } from '../api/types';
import { clearAuthFailure, saveSettings, setOfflineFallback } from '../api/client';
import { AppStateProvider } from '../components/AppContext';
import { isPaletteShortcut, rankCommands, type Command } from '../components/CommandPalette';
import { Layout } from '../components/Layout';
import { fuzzyMatch, highlightSegments } from '../lib/fuzzy';
import { beginRunsRead, clearCachedRuns, getCachedRuns, newestFirst, recordRuns } from '../lib/runsCache';
import { THEME_STORAGE_KEY } from '../lib/theme';
import { RunsView } from '../views/RunsView';
import { auditA11y, formatViolations } from './a11y';

/* ------------------------------ fuzzy ------------------------------------ */

/** The palette's fixed labels, as `CommandPalette` builds them. */
const PALETTE_LABELS = [
  'Go to Onboard corridor',
  'Go to Scenarios',
  'Go to Runs',
  'Go to Sweeps',
  'Go to Compare',
  'Go to Reports',
  'Go to First run',
  'Launch a ring_sugiyama run…',
  'Open settings',
  'Theme: System',
  'Theme: Light',
  'Theme: Dark',
];

/** Every distinct `size`-letter subsequence of `text`, whitespace skipped (the
 * query splits on it). */
function subsequences(text: string, size: number): string[] {
  const out = new Set<string>();
  const walk = (from: number, word: string): void => {
    if (word.length === size) {
      out.add(word);
      return;
    }
    for (let i = from; i < text.length; i++) if (!/\s/.test(text[i])) walk(i + 1, word + text[i]);
  };
  walk(0, '');
  return [...out];
}

/** The documented subsequence score, maximised by brute force over every
 * alignment of `word` in `text`: 30 − (span − |word|) + 4 per word start. */
function bestAlignmentScore(word: string, text: string): number | null {
  const isStart = (i: number): boolean => i === 0 || /[\s\-_/·:.(),]/.test(text[i - 1]);
  let best: number | null = null;
  const walk = (k: number, from: number, picked: number[]): void => {
    if (k === word.length) {
      const span = picked[picked.length - 1] - picked[0] + 1;
      const score = Math.max(1, 30 - (span - word.length) + 4 * picked.filter(isStart).length);
      if (best === null || score > best) best = score;
      return;
    }
    for (let i = from; i < text.length; i++) if (text[i] === word[k]) walk(k + 1, i + 1, [...picked, i]);
  };
  walk(0, 0, []);
  return best;
}

describe('fuzzyMatch', () => {
  it('matches case-insensitively and ranks prefix > word start > inside > subsequence', () => {
    const prefix = fuzzyMatch('go', 'Go to Reports');
    const wordStart = fuzzyMatch('rep', 'Go to Reports');
    const inside = fuzzyMatch('port', 'Go to Reports');
    const subsequence = fuzzyMatch('gtr', 'Go to Reports');
    expect(prefix && wordStart && inside && subsequence).toBeTruthy();
    expect(prefix!.score).toBeGreaterThan(wordStart!.score);
    expect(wordStart!.score).toBeGreaterThan(inside!.score);
    expect(inside!.score).toBeGreaterThan(subsequence!.score);
    expect(wordStart!.indices).toEqual([6, 7, 8]);
    // the subsequence lands on word starts: G(o) t(o) R(eports)
    expect(subsequence!.indices).toEqual([0, 3, 6]);
  });

  it('needs every word, from the label or the keywords', () => {
    expect(fuzzyMatch('go run', 'Go to Runs')).not.toBeNull();
    expect(fuzzyMatch('go xyz', 'Go to Runs')).toBeNull();
    const kw = fuzzyMatch('dark', 'Theme: Dark', 'toggle appearance');
    const viaKeywords = fuzzyMatch('toggle', 'Theme: Dark', 'toggle appearance');
    expect(viaKeywords).not.toBeNull();
    // a keyword hit highlights nothing in the label, and counts for less
    expect(viaKeywords!.indices).toEqual([]);
    expect(kw!.score).toBeGreaterThan(viaKeywords!.score);
  });

  it('finds a subsequence even where a jump to the next word start would strand later letters', () => {
    // the second "o" of "goto" could start "onboard", but then no "t" is left;
    // the last "o" may (G-o t-O(nboard): a word start outweighs two letters skipped)
    expect(fuzzyMatch('goto', 'Go to Onboard corridor')?.indices).toEqual([0, 1, 3, 6]);
    expect(fuzzyMatch('goto onboard', 'Go to Onboard corridor')).not.toBeNull();
    expect(fuzzyMatch('goon', 'Go to Onboard corridor')).not.toBeNull();
    // the "r" of "first" could start "run", but then no "s" is left
    expect(fuzzyMatch('gofirst', 'Go to First run')?.indices).toEqual([0, 1, 6, 7, 8, 9, 10]);
  });

  it('matches every 2- and 3-letter subsequence of the palette labels, highlighting its letters', () => {
    const misses: string[] = [];
    for (const label of PALETTE_LABELS) {
      const text = label.toLowerCase();
      for (const word of subsequences(text, 2).concat(subsequences(text, 3))) {
        const m = fuzzyMatch(word, label);
        if (!m || m.indices.map((i) => text[i]).join('') !== word) misses.push(`${word} in ${label}`);
      }
    }
    expect(misses).toEqual([]);
  });

  it('scores a subsequence by its best alignment: word starts, then the tightest span', () => {
    for (const label of ['Go to First run', 'Go to Onboard corridor', 'Launch a ring_sugiyama run…']) {
      const text = label.toLowerCase();
      for (const word of subsequences(text, 3)) {
        if (text.includes(word)) continue; // contiguous: scored on its own scale
        expect(fuzzyMatch(word, label)?.score, `${word} in ${label}`).toBe(bestAlignmentScore(word, text));
      }
    }
    // a word-start alignment beats an earlier, tighter one: R(eports), not (go) to (repo)r(ts)
    expect(fuzzyMatch('gtr', 'Go to Reports')?.indices).toEqual([0, 3, 6]);
  });

  it('matches everything with an empty query', () => {
    expect(fuzzyMatch('   ', 'anything')).toEqual({ score: 0, indices: [] });
  });

  it('splits a label into highlighted and plain runs', () => {
    expect(highlightSegments('Go to Runs', [6, 7, 8])).toEqual([
      { text: 'Go to ', match: false },
      { text: 'Run', match: true },
      { text: 's', match: false },
    ]);
  });
});

function cmd(id: string, group: Command['group'], label: string, keywords = ''): Command {
  return { id, group, label, keywords, icon: 'activity', run: () => undefined };
}

describe('rankCommands', () => {
  const commands: Command[] = [
    cmd('a', 'navigate', 'Go to Runs'),
    cmd('b', 'navigate', 'Go to Reports'),
    cmd('c', 'theme', 'Theme: Dark'),
    ...Array.from({ length: 8 }, (_, i) => cmd(`r${i}`, 'runs', `run-00${i}`)),
  ];

  it('keeps the fixed order and shows five recent runs with no query', () => {
    const groups = rankCommands(commands, '');
    expect(groups.map((g) => g.group)).toEqual(['navigate', 'theme', 'runs']);
    expect(groups[2].items).toHaveLength(5);
  });

  it('puts the best match first and drops non-matches', () => {
    const groups = rankCommands(commands, 'rep');
    expect(groups[0].items[0].cmd.label).toBe('Go to Reports');
    expect(groups.flatMap((g) => g.items).some((i) => i.cmd.label === 'Theme: Dark')).toBe(false);
  });

  it('orders groups by their best match', () => {
    expect(rankCommands(commands, 'run-003')[0].group).toBe('runs');
  });
});

describe('isPaletteShortcut', () => {
  const key = (init: KeyboardEventInit): KeyboardEvent => new KeyboardEvent('keydown', init);
  it('is ⌘K or Ctrl+K only', () => {
    expect(isPaletteShortcut(key({ key: 'k', metaKey: true }))).toBe(true);
    expect(isPaletteShortcut(key({ key: 'k', ctrlKey: true }))).toBe(true);
    expect(isPaletteShortcut(key({ key: 'K', ctrlKey: true }))).toBe(true);
    expect(isPaletteShortcut(key({ key: 'k' }))).toBe(false);
    expect(isPaletteShortcut(key({ key: 'k', ctrlKey: true, shiftKey: true }))).toBe(false);
    expect(isPaletteShortcut(key({ key: 'k', ctrlKey: true, altKey: true }))).toBe(false);
    expect(isPaletteShortcut(key({ key: 'k', ctrlKey: true, repeat: true }))).toBe(false);
  });
});

/* ------------------------------ runs cache -------------------------------- */

function runRow(id: string, over: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: id,
    scenario_id: 'scn_ring',
    scenario_name: 'ring_sugiyama',
    status: 'done',
    progress: { completed_replicates: 20, total_replicates: 20 },
    config_hash: 'abc',
    seeded: false,
    tier: 'micro',
    created_at: '2026-10-01T00:00:00',
    ...over,
  };
}

describe('runs cache', () => {
  afterEach(() => clearCachedRuns());

  it('drops an answer overtaken by a newer read', () => {
    const older = beginRunsRead();
    const newer = beginRunsRead();
    recordRuns(newer, [runRow('run-new')], false);
    recordRuns(older, [runRow('run-old')], false);
    expect(getCachedRuns()?.rows.map((r) => r.run_id)).toEqual(['run-new']);
  });

  it('forgets everything, in flight included, when the API settings change', () => {
    const seq = beginRunsRead();
    recordRuns(seq, [runRow('run-a')], false);
    const inFlight = beginRunsRead();
    saveSettings({ baseUrl: '/api/v1', apiKey: 'another-key' });
    expect(getCachedRuns()).toBeNull();
    recordRuns(inFlight, [runRow('run-stale')], false);
    expect(getCachedRuns()).toBeNull();
    window.localStorage.clear();
  });

  it('orders newest first, by timestamp and then by list position', () => {
    const rows = [
      runRow('run-1', { created_at: '2026-10-01T00:00:00' }),
      runRow('run-2', { created_at: '2026-10-01T00:00:00' }),
      runRow('run-3', { created_at: '2026-09-01T00:00:00' }),
    ];
    expect(newestFirst(rows).map((r) => r.run_id)).toEqual(['run-2', 'run-1', 'run-3']);
  });
});

/* ------------------------------ the palette -------------------------------- */

function Where(): JSX.Element {
  const { pathname, search } = useLocation();
  return <div data-testid="where">{`${pathname}${search}`}</div>;
}

const fetchCalls: string[] = [];

function renderShell(path = '/scenarios', runs: JSX.Element = <div>runs page</div>): void {
  render(
    <AppStateProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/scenarios" element={<input aria-label="scenario search" />} />
            <Route path="/runs" element={runs} />
            <Route path="/runs/:runId" element={<div>run detail</div>} />
            <Route path="/sweeps" element={<div>sweeps page</div>} />
            <Route path="/reports" element={<div>reports page</div>} />
          </Route>
        </Routes>
        <Where />
      </MemoryRouter>
    </AppStateProvider>,
  );
}

function pressShortcut(init: KeyboardEventInit = { ctrlKey: true }): void {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', bubbles: true, ...init }));
  });
}

function palette(): HTMLElement {
  return screen.getByRole('dialog', { name: 'Command menu' });
}

function box(): HTMLElement {
  return within(palette()).getByRole('combobox', { name: 'Search commands' });
}

function activeOption(): HTMLElement | null {
  const id = box().getAttribute('aria-activedescendant');
  return id ? document.getElementById(id) : null;
}

beforeEach(() => {
  setOfflineFallback(false);
  clearAuthFailure();
  clearCachedRuns();
  fetchCalls.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
      fetchCalls.push(String(input));
      return new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
});

afterEach(() => {
  // the cache is cleared before each test, not here: a palette still mounted
  // until cleanup would re-render outside act
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.theme;
  window.localStorage.clear();
});

describe('command palette: opening and closing', () => {
  it('opens on Ctrl+K with focus in the search box and the app inert; Escape restores focus', async () => {
    renderShell();
    const field = screen.getByLabelText('scenario search');
    field.focus();
    pressShortcut();
    expect(document.activeElement).toBe(box());
    expect(box()).toHaveAttribute('aria-expanded', 'true');
    expect(document.querySelector('.app')).toHaveAttribute('inert');

    fireEvent.keyDown(box(), { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Command menu' })).toBeNull();
    expect(document.querySelector('.app')).not.toHaveAttribute('inert');
    expect(document.activeElement).toBe(field);
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('toggles with ⌘K and opens from the top-bar Commands button', async () => {
    renderShell();
    pressShortcut({ metaKey: true });
    expect(palette()).toBeInTheDocument();
    pressShortcut({ metaKey: true });
    expect(screen.queryByRole('dialog', { name: 'Command menu' })).toBeNull();

    const button = screen.getByRole('button', { name: 'Commands' });
    expect(button).toHaveAttribute('aria-keyshortcuts', 'Meta+K Control+K');
    button.focus();
    fireEvent.click(button);
    expect(document.activeElement).toBe(box());
    fireEvent.click(document.querySelector('.cmdk-scrim') as HTMLElement);
    expect(screen.queryByRole('dialog', { name: 'Command menu' })).toBeNull();
    expect(document.activeElement).toBe(button);
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('does not open over another modal layer', async () => {
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(screen.getByRole('dialog', { name: 'Settings' })).toBeInTheDocument();
    pressShortcut();
    expect(screen.queryByRole('dialog', { name: 'Command menu' })).toBeNull();
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('keeps Tab inside', async () => {
    renderShell();
    pressShortcut();
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(document.activeElement).toBe(box());
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(box());
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });
});

describe('command palette: keyboard model', () => {
  it('lists every page, the actions and the themes, with the first option active', async () => {
    renderShell();
    pressShortcut();
    const list = within(palette()).getByRole('listbox', { name: 'Commands' });
    for (const page of ['Onboard corridor', 'Scenarios', 'Runs', 'Sweeps', 'Compare', 'Reports', 'First run']) {
      expect(within(list).getByRole('option', { name: `Go to ${page}` })).toBeInTheDocument();
    }
    expect(within(list).getByRole('option', { name: 'Launch a ring_sugiyama run…' })).toBeInTheDocument();
    expect(within(list).getByRole('option', { name: 'Open settings' })).toBeInTheDocument();
    for (const t of ['System', 'Light', 'Dark']) {
      expect(within(list).getByRole('option', { name: `Theme: ${t}` })).toBeInTheDocument();
    }
    expect(within(list).getByRole('group', { name: 'Go to' })).toBeInTheDocument();
    expect(activeOption()).toHaveAccessibleName('Go to Onboard corridor');
    expect(activeOption()).toHaveAttribute('aria-selected', 'true');
    // the current page says so
    expect(within(list).getByRole('option', { name: 'Go to Scenarios' })).toHaveAccessibleDescription(
      'Current page',
    );
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('moves with the arrows, wrapping at both ends', async () => {
    renderShell();
    pressShortcut();
    const options = within(palette()).getAllByRole('option');
    fireEvent.keyDown(box(), { key: 'ArrowUp' });
    expect(activeOption()).toBe(options[options.length - 1]);
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    expect(activeOption()).toBe(options[0]);
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    expect(activeOption()).toBe(options[1]);
    expect(options[1]).toHaveAttribute('aria-selected', 'true');
    expect(options[0]).toHaveAttribute('aria-selected', 'false');
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('filters fuzzily, highlights the match, and Enter goes to the page with content focused', async () => {
    renderShell();
    pressShortcut();
    fireEvent.change(box(), { target: { value: 'swe' } });
    expect(activeOption()).toHaveAccessibleName('Go to Sweeps');
    expect(within(activeOption() as HTMLElement).getByText('Swe').tagName).toBe('MARK');
    expect(within(palette()).getByRole('status')).toHaveTextContent(/^\d+ commands?$/);
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(screen.queryByRole('dialog', { name: 'Command menu' })).toBeNull();
    expect(screen.getByTestId('where')).toHaveTextContent('/sweeps');
    expect(screen.getByText('sweeps page')).toBeInTheDocument();
    expect(document.activeElement).toBe(document.getElementById('content'));
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('finds a page whose name the query spells across words', async () => {
    renderShell();
    pressShortcut();
    fireEvent.change(box(), { target: { value: 'goto onboard' } });
    expect(activeOption()).toHaveAccessibleName('Go to Onboard corridor');
    fireEvent.change(box(), { target: { value: 'gofirst' } });
    expect(activeOption()).toHaveAccessibleName('Go to First run');
    fireEvent.change(box(), { target: { value: 'goto' } });
    const names = within(palette())
      .getAllByRole('option')
      .map((o) => o.getAttribute('aria-label') ?? o.textContent);
    expect(names).toEqual(expect.arrayContaining(['Go to Onboard corridor', 'Go to First run']));
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('runs a clicked option', async () => {
    renderShell();
    pressShortcut();
    fireEvent.click(within(palette()).getByRole('option', { name: 'Go to Reports' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/reports');
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('says when nothing matches and collapses the listbox', async () => {
    renderShell();
    pressShortcut();
    fireEvent.change(box(), { target: { value: 'zzqx' } });
    expect(within(palette()).getByText('No commands match “zzqx”.')).toBeInTheDocument();
    expect(within(palette()).queryByRole('listbox')).toBeNull();
    expect(box()).toHaveAttribute('aria-expanded', 'false');
    expect(box()).not.toHaveAttribute('aria-activedescendant');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(palette()).toBeInTheDocument();
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });
});

describe('command palette: commands', () => {
  it('sets the theme', async () => {
    renderShell();
    pressShortcut();
    fireEvent.change(box(), { target: { value: 'theme dark' } });
    expect(activeOption()).toHaveAccessibleName('Theme: Dark');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    // and says which is current next time
    pressShortcut();
    expect(within(palette()).getByRole('option', { name: 'Theme: Dark' })).toHaveAccessibleDescription('Current');
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('opens Settings, which returns focus to the palette opener on close', async () => {
    renderShell();
    const opener = screen.getByRole('button', { name: 'Commands' });
    opener.focus();
    fireEvent.click(opener);
    fireEvent.change(box(), { target: { value: 'settings' } });
    fireEvent.keyDown(box(), { key: 'Enter' });
    const drawer = screen.getByRole('dialog', { name: 'Settings' });
    expect(drawer.contains(document.activeElement)).toBe(true);
    expect(screen.queryByRole('dialog', { name: 'Command menu' })).toBeNull();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(document.activeElement).toBe(opener);
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('opens a recent run from the shared cache, newest first, without a fetch of its own', async () => {
    recordRuns(
      beginRunsRead(),
      [
        runRow('run-old', { created_at: '2026-09-01T00:00:00', status: 'failed' }),
        runRow('run-a41d09', { created_at: '2026-10-02T00:00:00', scenario_name: 'corridor_10km' }),
      ],
      false,
    );
    renderShell();
    pressShortcut();
    const group = within(palette()).getByRole('group', { name: 'Recent runs' });
    const runs = within(group).getAllByRole('option');
    expect(runs.map((o) => o.getAttribute('aria-label'))).toEqual(['run-a41d09', 'run-old']);
    expect(runs[0]).toHaveAccessibleDescription('corridor_10km · done · micro');
    fireEvent.change(box(), { target: { value: 'a41' } });
    expect(activeOption()).toHaveAccessibleName('run-a41d09');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(screen.getByTestId('where')).toHaveTextContent('/runs/run-a41d09');
    expect(fetchCalls.some((u) => /\/runs/.test(u))).toBe(false);
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('badges runs the demo backend served', async () => {
    recordRuns(beginRunsRead(), [runRow('run-8f2c11')], true);
    renderShell();
    pressShortcut();
    const option = within(palette()).getByRole('option', { name: 'run-8f2c11' });
    expect(within(option).getByText('DEMO')).toHaveClass('tag', 'demo');
    expect(option).toHaveAccessibleDescription(/DEMO/);
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('points at Runs when no runs list has been read yet', async () => {
    renderShell();
    pressShortcut();
    fireEvent.change(box(), { target: { value: 'run-zz9' } });
    expect(within(palette()).getByText(/open Runs first/)).toBeInTheDocument();
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('has no serious or critical accessibility violations while open', async () => {
    recordRuns(beginRunsRead(), [runRow('run-8f2c11')], true);
    renderShell();
    pressShortcut();
    fireEvent.change(box(), { target: { value: 'r' } });
    expect(formatViolations(auditA11y(document.body))).toEqual([]);
    fireEvent.change(box(), { target: { value: 'zzqx' } });
    expect(formatViolations(auditA11y(document.body))).toEqual([]);
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });
});

/* -------------------------- launch a ring run ----------------------------- */

const RING = {
  name: 'ring_sugiyama',
  filename: 'ring_sugiyama.yaml',
  config_hash: 'a226444c0145',
  preset: true,
  config: {
    name: 'ring_sugiyama',
    tier: 'micro',
    network: { kind: 'ring', circumference_m: 230, n_vehicles: 22 },
    fleet: { model: 'IDM' },
    av: { penetration: 0, compliance: 1, controller: null },
    sim: { duration_s: 600, warmup_s: 180 },
    seed: 42,
    replicates: 20,
  },
};

const STORED = {
  scenario_id: 'scn_i24',
  name: 'i24_replica',
  config_hash: 'c0ffeec0ffee',
  created_at: '2026-10-01T00:00:00',
  preset: false,
  config: {
    name: 'i24_replica',
    tier: 'micro',
    network: { kind: 'corridor', length_m: 6400, lanes: 4, inflow: [[0, 1.4]] },
    fleet: { model: 'IDM' },
    av: { penetration: 0, compliance: 1, controller: null },
    sim: { duration_s: 7800 },
    seed: 7,
    replicates: 10,
  },
};

describe('command palette: launch a ring run', () => {
  const methods: string[] = [];

  beforeEach(() => {
    methods.length = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        methods.push(`${init?.method ?? 'GET'} ${url}`);
        const json = (body: unknown): Response =>
          new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
        if (url.endsWith('/health')) return json({ status: 'ok' });
        if (url.endsWith('/scenarios/preset')) return json([RING]);
        if (url.endsWith('/scenarios')) return json([STORED]);
        if (url.endsWith('/runs')) return json([]);
        return new Response(JSON.stringify({ detail: 'unexpected' }), { status: 404 });
      }),
    );
  });

  it('opens the Runs launcher on the ring preset, prefilled and focused, and launches nothing', async () => {
    renderShell('/runs', <RunsView />);
    const select = screen.getByLabelText('Scenario');
    // the user moves the launcher off the ring and edits a field
    await waitFor(() => expect(within(select).getAllByRole('option').length).toBe(2), { timeout: 4000 });
    fireEvent.change(select, { target: { value: 'scn_i24' } });
    await waitFor(() => expect(screen.getByLabelText('Duration (s)')).toHaveValue(7800));
    fireEvent.change(screen.getByLabelText('Tier'), { target: { value: 'macro' } });
    fireEvent.change(screen.getByLabelText('Seed'), { target: { value: '5' } });

    pressShortcut();
    fireEvent.change(box(), { target: { value: 'launch ring' } });
    expect(activeOption()).toHaveAccessibleName('Launch a ring_sugiyama run…');
    fireEvent.keyDown(box(), { key: 'Enter' });

    await waitFor(() => expect(screen.getByLabelText('Scenario')).toHaveValue('preset:ring_sugiyama.yaml'));
    expect(screen.getByLabelText('Tier')).toHaveValue('micro');
    expect(screen.getByLabelText('Duration (s)')).toHaveValue(600);
    expect(screen.getByLabelText('Seed')).toHaveValue(42);
    expect(screen.getByLabelText('Replicates')).toHaveValue(20);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText('Scenario')));
    expect(screen.getByRole('button', { name: 'Launch run' })).toBeEnabled();
    // prefilled, not launched
    expect(methods.some((m) => m.startsWith('POST'))).toBe(false);
    // the request is consumed: nothing left in the history entry to re-apply
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/runs$/);
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  }, 15000);

  it('works from another page, keeping nothing of the old one', async () => {
    renderShell('/sweeps', <RunsView />);
    pressShortcut();
    fireEvent.change(box(), { target: { value: 'launch' } });
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(screen.getByTestId('where')).toHaveTextContent('/runs');
    await waitFor(
      () => expect(document.activeElement).toBe(screen.getByLabelText('Scenario')),
      { timeout: 4000 },
    );
    expect(screen.getByLabelText('Scenario')).toHaveValue('preset:ring_sugiyama.yaml');
    expect(methods.some((m) => m.startsWith('POST'))).toBe(false);
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  }, 15000);
});
