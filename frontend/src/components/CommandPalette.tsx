/** Command palette (docs/design/DASHBOARD_DESIGN.md §9.19, §11.3).
 *
 * Opened with ⌘K / Ctrl+K anywhere, or from the top bar's Commands button;
 * the shell owns both (components/Layout.tsx). It offers:
 *
 *   Go to       every page in the sidebar, First run included
 *   Actions     "Launch a ring_sugiyama run…", which opens the Runs launcher
 *               prefilled on the ring preset and focused — it never queues a
 *               run, the user still presses Launch run behind the usual cost
 *               gate — and Open settings
 *   Theme       System, Light, Dark
 *   Recent runs the runs some view has already read (lib/runsCache); the
 *               palette makes no request of its own, and demo rows say DEMO
 *
 * Filtering is fuzzy (lib/fuzzy): every word of the query must match a
 * command's label or keywords, best matches first, matched letters in bold.
 *
 * Keyboard: the search box is an ARIA 1.2 combobox driving a listbox through
 * aria-activedescendant, so focus never leaves the box. Up/Down move (and
 * wrap), Enter runs, Escape closes, Tab stays inside. The app behind is inert
 * while the palette is open, and focus goes back to whatever had it — unless
 * the command navigated, in which case the shell focuses the new page's
 * content (lib/hooks FOCUS_CONTENT_STATE). Its entrance animation uses the
 * duration tokens, which are zero under reduced motion. */

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { fuzzyMatch, highlightSegments } from '../lib/fuzzy';
import { FOCUS_CONTENT_STATE, launchPresetState } from '../lib/hooks';
import { RING_PRESET_NAME } from '../lib/library';
import { newestFirst, useCachedRuns } from '../lib/runsCache';
import { setThemePref, THEME_PREF_LABELS, useThemePref, type ThemePref } from '../lib/theme';
import { Icon, type IconName } from './icons';
import { ALL_PAGES } from './navItems';
import { trapTabKey } from './SettingsDrawer';

type GroupId = 'navigate' | 'actions' | 'theme' | 'runs';

const GROUP_ORDER: GroupId[] = ['navigate', 'actions', 'theme', 'runs'];

const GROUP_LABELS: Record<GroupId, string> = {
  navigate: 'Go to',
  actions: 'Actions',
  theme: 'Theme',
  runs: 'Recent runs',
};

/** Recent runs listed before anything is typed; a query searches them all. */
const RECENT_RUNS_SHOWN = 5;
/** Upper bound on run matches listed for a query. */
const RUN_MATCHES_SHOWN = 30;

/** Extra words each page answers to, beyond its name. */
const PAGE_KEYWORDS: Record<string, string> = {
  '/onboard': 'set up openstreetmap osm detectors import new corridor',
  '/scenarios': 'set up presets yaml composer library',
  '/runs': 'simulate launch replicates progress',
  '/sweeps': 'simulate grid penetration compliance matrix',
  '/reports': 'fhwa calibration validation criteria download',
  '/first-run': 'guided quickstart walkthrough tutorial start',
};

const THEME_ICONS: Record<ThemePref, IconName> = {
  system: 'monitor',
  light: 'sun',
  dark: 'moon',
};

export interface Command {
  id: string;
  group: GroupId;
  /** What is matched and shown (and the option's accessible name). */
  label: string;
  /** Secondary text: what the command does or where it goes. */
  hint?: string;
  /** Words matched but not shown. */
  keywords?: string;
  icon: IconName;
  /** Render the label in mono (machine values: run ids). */
  mono?: boolean;
  /** A DEMO badge: the row came from the in-browser demo backend. */
  demo?: boolean;
  run: () => void;
}

interface Ranked {
  cmd: Command;
  score: number;
  indices: number[];
}

/** True for ⌘K or Ctrl+K (no Alt, no Shift, not a key repeat). */
export function isPaletteShortcut(e: KeyboardEvent): boolean {
  return (
    (e.metaKey || e.ctrlKey) &&
    !e.altKey &&
    !e.shiftKey &&
    !e.repeat &&
    (e.key === 'k' || e.key === 'K')
  );
}

/** The shortcut as this platform writes it: ⌘K on Apple devices, Ctrl K
 * elsewhere. */
export function paletteShortcutLabel(): string {
  if (typeof navigator === 'undefined') return 'Ctrl K';
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  const platform = nav.userAgentData?.platform ?? nav.platform ?? '';
  return /mac|iphone|ipad|ipod/i.test(platform) ? '⌘K' : 'Ctrl K';
}

/** Rank `commands` against `query`: everything, in order, for an empty query;
 * otherwise the matches only, best first within each group, and the groups
 * ordered by their best match. Recent runs are capped either way. */
export function rankCommands(commands: Command[], query: string): { group: GroupId; items: Ranked[] }[] {
  const q = query.trim();
  const groups = new Map<GroupId, Ranked[]>();
  for (const cmd of commands) {
    const m = fuzzyMatch(q, cmd.label, cmd.keywords ?? '');
    if (!m) continue;
    const list = groups.get(cmd.group) ?? [];
    list.push({ cmd, score: m.score, indices: m.indices });
    groups.set(cmd.group, list);
  }
  const out: { group: GroupId; items: Ranked[]; best: number }[] = [];
  for (const id of GROUP_ORDER) {
    let items = groups.get(id);
    if (!items || items.length === 0) continue;
    if (q !== '') items = [...items].sort((a, b) => b.score - a.score);
    if (id === 'runs') items = items.slice(0, q === '' ? RECENT_RUNS_SHOWN : RUN_MATCHES_SHOWN);
    out.push({ group: id, items, best: items[0].score });
  }
  if (q !== '') out.sort((a, b) => b.best - a.best);
  return out.map(({ group, items }) => ({ group, items }));
}

export function CommandPalette({
  onClose,
  onOpenSettings,
}: {
  onClose: () => void;
  onOpenSettings: () => void;
}): JSX.Element {
  const navigate = useNavigate();
  const location = useLocation();
  const themePref = useThemePref();
  const cached = useCachedRuns();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const listId = useId();

  // mount-only: remember the opener, make the app behind inert, focus the
  // search box; undo all three on close (inert first: an inert element
  // cannot take focus back)
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const app = document.querySelector('.app');
    const wasInert = app?.hasAttribute('inert') ?? false;
    app?.setAttribute('inert', '');
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeRef.current();
      } else if (e.key === 'Tab' && boxRef.current) {
        trapTabKey(e, boxRef.current);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      if (!wasInert) app?.removeAttribute('inert');
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  const commands = useMemo<Command[]>(() => {
    const go = (to: string): void => navigate(to, { state: FOCUS_CONTENT_STATE });
    const onRuns = location.pathname === '/runs';
    const list: Command[] = ALL_PAGES.map((page) => ({
      id: `go:${page.to}`,
      group: 'navigate',
      label: `Go to ${page.label}`,
      hint: location.pathname === page.to ? 'Current page' : undefined,
      keywords: PAGE_KEYWORDS[page.to],
      icon: page.icon,
      run: () => go(page.to),
    }));
    list.push(
      {
        id: 'action:launch-ring',
        group: 'actions',
        label: `Launch a ${RING_PRESET_NAME} run…`,
        hint: 'Prefills the Runs launcher; launches nothing',
        keywords: 'new run start ring benchmark sugiyama emergence smoke simulate',
        icon: 'circle-play',
        run: () =>
          // keeps the Runs filters when already there
          navigate(
            { pathname: '/runs', search: onRuns ? location.search : '' },
            { state: launchPresetState(RING_PRESET_NAME) },
          ),
      },
      {
        id: 'action:settings',
        group: 'actions',
        label: 'Open settings',
        hint: 'API connection and appearance',
        keywords: 'preferences api key base url connection configure',
        icon: 'settings',
        run: onOpenSettings,
      },
    );
    for (const pref of ['system', 'light', 'dark'] as ThemePref[]) {
      list.push({
        id: `theme:${pref}`,
        group: 'theme',
        label: `Theme: ${THEME_PREF_LABELS[pref]}`,
        hint: themePref === pref ? 'Current' : undefined,
        keywords: 'toggle switch appearance colour color mode dark light system',
        icon: THEME_ICONS[pref],
        run: () => setThemePref(pref),
      });
    }
    for (const r of newestFirst(cached?.rows ?? [])) {
      const scenario = r.scenario_name ?? r.scenario_id;
      list.push({
        id: `run:${r.run_id}`,
        group: 'runs',
        label: r.run_id,
        hint: `${scenario} · ${r.status} · ${r.tier}`,
        keywords: `open run ${scenario} ${r.scenario_id} ${r.status} ${r.tier}`,
        icon: 'activity',
        mono: true,
        demo: cached?.demo ?? false,
        run: () => go(`/runs/${encodeURIComponent(r.run_id)}`),
      });
    }
    return list;
  }, [navigate, location.pathname, location.search, onOpenSettings, themePref, cached]);

  const groups = useMemo(() => rankCommands(commands, query), [commands, query]);
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);
  const count = flat.length;
  const activeIndex = count === 0 ? -1 : Math.min(active, count - 1);
  const optionId = (i: number): string => `${listId}-opt-${i}`;

  // the active option stays in view as the arrows move it
  useEffect(() => {
    if (activeIndex < 0) return;
    const el = document.getElementById(`${listId}-opt-${activeIndex}`);
    if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' });
  }, [activeIndex, groups, listId]);

  const execute = (cmd: Command): void => {
    onClose();
    cmd.run();
  };

  const onInputKey = (e: ReactKeyboardEvent<HTMLInputElement>): void => {
    if (e.nativeEvent.isComposing) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (count === 0) return;
      const step = e.key === 'ArrowDown' ? 1 : count - 1;
      setActive((activeIndex + step) % count);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (activeIndex >= 0) execute(flat[activeIndex].cmd);
    }
  };

  const hasRuns = (cached?.rows.length ?? 0) > 0;
  let index = 0;

  return (
    <>
      <div className="modal-scrim cmdk-scrim" onClick={onClose} />
      <div
        ref={boxRef}
        className="cmdk"
        role="dialog"
        aria-modal="true"
        aria-label="Command menu"
      >
        <div className="cmdk-search">
          <Icon name="search" className="cmdk-search-icon" />
          <input
            ref={inputRef}
            className="cmdk-input"
            type="text"
            role="combobox"
            aria-label="Search commands"
            aria-expanded={count > 0}
            aria-controls={count > 0 ? listId : undefined}
            aria-activedescendant={activeIndex >= 0 ? optionId(activeIndex) : undefined}
            aria-autocomplete="list"
            autoComplete="off"
            spellCheck={false}
            placeholder="Type a command, a page or a run id…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={onInputKey}
          />
        </div>

        {count > 0 ? (
          <div className="cmdk-list" role="listbox" id={listId} aria-label="Commands">
            {groups.map(({ group, items }) => (
              <div
                key={group}
                className="cmdk-group"
                role="group"
                aria-labelledby={`${listId}-${group}`}
              >
                <div className="cmdk-group-label" id={`${listId}-${group}`} aria-hidden="true">
                  {GROUP_LABELS[group]}
                </div>
                {items.map(({ cmd, indices }) => {
                  const i = index++;
                  const isActive = i === activeIndex;
                  const hintId = cmd.hint || cmd.demo ? `${optionId(i)}-hint` : undefined;
                  return (
                    <div
                      key={cmd.id}
                      id={optionId(i)}
                      role="option"
                      aria-selected={isActive}
                      aria-label={cmd.label}
                      aria-describedby={hintId}
                      className={`cmdk-item${isActive ? ' is-active' : ''}`}
                      // keep focus in the search box when an option is clicked
                      onMouseDown={(e) => e.preventDefault()}
                      onMouseMove={() => {
                        if (!isActive) setActive(i);
                      }}
                      onClick={() => execute(cmd)}
                    >
                      <Icon name={cmd.icon} className="cmdk-item-icon" />
                      <span className={`cmdk-label${cmd.mono ? ' mono' : ''}`}>
                        {highlightSegments(cmd.label, indices).map((seg, k) =>
                          seg.match ? (
                            <mark key={k} className="cmdk-match">
                              {seg.text}
                            </mark>
                          ) : (
                            <span key={k}>{seg.text}</span>
                          ),
                        )}
                      </span>
                      {hintId && (
                        <span className="cmdk-hint" id={hintId}>
                          {cmd.demo && <span className="tag demo">DEMO</span>}
                          {cmd.hint && <span className="cmdk-hint-text">{cmd.hint}</span>}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        ) : (
          <div className="cmdk-empty">
            <p className="cmdk-empty-title">No commands match “{query.trim()}”.</p>
            <p className="cmdk-empty-desc">
              {hasRuns
                ? 'Try a page name, a theme or a run id.'
                : 'Recent runs are listed once a runs list has loaded: open Runs first.'}
            </p>
          </div>
        )}

        <div className="cmdk-foot" aria-hidden="true">
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> move
          </span>
          <span>
            <kbd>Enter</kbd> run
          </span>
          <span>
            <kbd>Esc</kbd> close
          </span>
        </div>

        <div className="visually-hidden" role="status">
          {query.trim() === ''
            ? ''
            : count === 0
              ? 'No commands match'
              : `${count} command${count === 1 ? '' : 's'}`}
        </div>
      </div>
    </>
  );
}
