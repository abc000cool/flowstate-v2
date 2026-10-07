/** App shell (docs/design/DASHBOARD_DESIGN.md §6.1, §6.4, §11.3).
 *
 * A grouped sidebar (Set up / Simulate / Report; First run, the live
 * connection status and Settings in its footer), a top bar with the
 * breadcrumb, the active corridor and the theme toggle, the auth and offline
 * banners, and the scrolling content column. The top bar also opens the
 * command palette, which ⌘K / Ctrl+K opens from anywhere (CommandPalette.tsx).
 *
 * Responsive: the sidebar is 232 px from 1024 px up, a 56 px icon rail from
 * 768 to 1023 px (labels clipped, not removed, so accessible names are
 * unchanged), and below 768 px a drawer opened from the top bar's menu button
 * (focus trapped, Escape closes, focus returns to the button).
 *
 * The status line polls `/health` every 5 s. `/health` needs no key, so a
 * rejected key would leave it green while every real call 401s: the line and
 * the auth banner report the connection the app actually has. */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import type { MouseEvent as ReactMouseEvent } from 'react';
import { Link, NavLink, Outlet, useLocation, useMatch } from 'react-router-dom';
import {
  AUTH_RETRY_DELAY_MS,
  checkHealth,
  clearAuthFailure,
  isAuthRetryScheduled,
  isMockEnv,
  setOfflineFallback,
} from '../api/client';
import { useAuthFailed, usePoll, wantsContentFocus } from '../lib/hooks';
import { nextThemePref, setThemePref, THEME_PREF_LABELS, useThemePref } from '../lib/theme';
import { useAppState } from './AppContext';
import { CommandPalette, isPaletteShortcut, paletteShortcutLabel } from './CommandPalette';
import { Icon, type IconName } from './icons';
import { ALL_PAGES, FIRST_RUN, NAV_GROUPS, type NavEntry } from './navItems';
import { FOCUSABLE, SettingsDrawer, trapTabKey } from './SettingsDrawer';
import { Toasts } from './toast';

/** Breadcrumb section names, by first path segment. */
const SECTION_LABELS = new Map<string, string>(ALL_PAGES.map((n) => [n.to, n.label]));

/** Width bands of §6.4 (CSS can't read custom properties in media queries,
 * so these mirror the constants in shell.css). */
const RAIL_QUERY = '(min-width: 768px) and (max-width: 1023px)';
const DRAWER_QUERY = '(max-width: 767px)';

/** A media query as React state; false where matchMedia is missing (jsdom). */
function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window.matchMedia !== 'function') return () => undefined;
      const mq = window.matchMedia(query);
      mq.addEventListener('change', onChange);
      return () => mq.removeEventListener('change', onChange);
    },
    [query],
  );
  const read = (): boolean =>
    typeof window.matchMedia === 'function' && window.matchMedia(query).matches;
  return useSyncExternalStore(subscribe, read, () => false);
}

/** The FlowState mark: three wave bands sloping down-right, as stop-and-go
 * waves do in a space-time plot. Decorative; the wordmark carries the name. */
function BrandMark(): JSX.Element {
  return (
    <svg
      className="brand-mark"
      width="20"
      height="20"
      viewBox="0 0 20 20"
      aria-hidden="true"
      focusable="false"
    >
      <rect width="20" height="20" rx="5" fill="currentColor" />
      <path
        d="M4 8 L9 13 M7 5 L14 12 M11 4 L16 9"
        fill="none"
        stroke="var(--bg-surface)"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function NavItem({ entry, railed }: { entry: NavEntry; railed: boolean }): JSX.Element {
  return (
    <NavLink to={entry.to} className="nav-item" title={railed ? entry.label : undefined}>
      <Icon name={entry.icon} />
      <span className="nav-label">{entry.label}</span>
    </NavLink>
  );
}

function Breadcrumb(): JSX.Element | null {
  const { pathname } = useLocation();
  const run = useMatch('/runs/:runId');
  const section = SECTION_LABELS.get(`/${pathname.split('/')[1] ?? ''}`);
  if (!section) return null;
  return (
    <nav className="breadcrumb" aria-label="Breadcrumb">
      <ol>
        {run?.params.runId ? (
          <>
            <li>
              <Link to="/runs">{section}</Link>
            </li>
            <li>
              <span className="breadcrumb-sep" aria-hidden="true">
                /
              </span>
              <span className="breadcrumb-current mono" aria-current="page">
                {run.params.runId}
              </span>
            </li>
          </>
        ) : (
          <li>
            <span className="breadcrumb-current" aria-current="page">
              {section}
            </span>
          </li>
        )}
      </ol>
    </nav>
  );
}

function CorridorChip({ corridor }: { corridor: string | null }): JSX.Element {
  return corridor ? (
    <span className="corridor-chip" title={`Active corridor: ${corridor}`}>
      <Icon name="map-pin" size={14} />
      <span className="visually-hidden">Active corridor: </span>
      <span className="corridor-chip-name mono">{corridor}</span>
    </span>
  ) : (
    <span className="corridor-chip is-empty">
      <Icon name="map-pin" size={14} />
      <span className="corridor-chip-name">No active corridor</span>
    </span>
  );
}

const THEME_ICON: Record<string, IconName> = { system: 'monitor', light: 'sun', dark: 'moon' };

function ThemeToggle(): JSX.Element {
  const pref = useThemePref();
  const next = nextThemePref(pref);
  const label = `Theme: ${THEME_PREF_LABELS[pref]}`;
  return (
    <button
      type="button"
      className="btn ghost icon-only topbar-btn"
      aria-label={label}
      title={`${label} (switch to ${THEME_PREF_LABELS[next]})`}
      onClick={() => setThemePref(next)}
    >
      <Icon name={THEME_ICON[pref]} />
    </button>
  );
}

/** The top bar's way into the command palette, beside the corridor chip: the
 * label clips to an icon below 1024 px; the shortcut hint is decorative (the
 * button announces it through aria-keyshortcuts). */
function CommandsButton({ onOpen }: { onOpen: () => void }): JSX.Element {
  const shortcut = paletteShortcutLabel();
  return (
    <button
      type="button"
      className="btn ghost topbar-cmdk"
      aria-haspopup="dialog"
      aria-keyshortcuts="Meta+K Control+K"
      title={`Commands (${shortcut})`}
      onClick={onOpen}
    >
      <Icon name="search" />
      <span className="topbar-cmdk-label">Commands</span>
      <kbd className="topbar-cmdk-kbd" aria-hidden="true">
        {shortcut}
      </kbd>
    </button>
  );
}

export function Layout(): JSX.Element {
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const paletteOpenRef = useRef(paletteOpen);
  paletteOpenRef.current = paletteOpen;
  const { corridor } = useAppState();
  const mockEnv = isMockEnv();
  const authFailed = useAuthFailed();
  const railed = useMediaQuery(RAIL_QUERY);
  const drawerMode = useMediaQuery(DRAWER_QUERY);
  const location = useLocation();
  const { pathname } = location;
  const sidebarRef = useRef<HTMLElement>(null);
  const mainRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);

  const poll = useCallback(async () => {
    const ok = await checkHealth();
    setHealthy(ok);
    if (!mockEnv) setOfflineFallback(!ok);
  }, [mockEnv]);
  usePoll(poll, 5000);

  // the drawer closes on navigation and when the window grows out of it
  useEffect(() => setNavOpen(false), [pathname]);

  // a view that navigates away after one of its actions (a launch landing on
  // Runs, a report request landing on Reports) asks for focus on the new
  // page's content: the control that had it went with the old page, and
  // focus would otherwise fall to <body> (lib/hooks FOCUS_CONTENT_STATE)
  useEffect(() => {
    if (wantsContentFocus(location.state)) document.getElementById('content')?.focus();
  }, [location]);
  useEffect(() => {
    if (!drawerMode) setNavOpen(false);
  }, [drawerMode]);

  // while the drawer is open: the page behind is inert, focus starts on the
  // first nav item, Tab stays inside, Escape closes, and focus returns to the
  // menu button
  useEffect(() => {
    if (!navOpen) return;
    const sidebar = sidebarRef.current;
    const main = mainRef.current;
    const menu = menuRef.current;
    main?.setAttribute('inert', '');
    sidebar?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setNavOpen(false);
      else if (e.key === 'Tab' && sidebar) trapTabKey(e, sidebar);
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      main?.removeAttribute('inert');
      menu?.focus();
    };
  }, [navOpen]);

  const openSettings = useCallback((): void => {
    setNavOpen(false);
    setSettingsOpen(true);
  }, []);
  const closePalette = useCallback((): void => setPaletteOpen(false), []);

  // ⌘K / Ctrl+K opens the command palette from anywhere, and closes it again.
  // Not over another modal layer (a confirm dialog, Settings, the navigation
  // drawer): that layer owns the keyboard until it closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (!isPaletteShortcut(e)) return;
      if (paletteOpenRef.current) {
        e.preventDefault();
        setPaletteOpen(false);
        return;
      }
      if (document.querySelector('[aria-modal="true"]')) return;
      e.preventDefault();
      setPaletteOpen(true);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const skipToContent = (e: ReactMouseEvent<HTMLAnchorElement>): void => {
    // focus the content without putting #content in the router's URL
    e.preventDefault();
    document.getElementById('content')?.focus();
  };

  const offline = !mockEnv && healthy === false;

  // pulse only when the link is down (§6.1); demo and probing hold still
  let dotCls = 'dot demo';
  let statusText = 'DEMO DATA';
  if (!mockEnv) {
    if (authFailed) {
      // /health needs no key, so it is green while every real call 401s —
      // the status line must report the connection the app actually has.
      dotCls = 'dot down pulse';
      statusText = 'KEY REJECTED';
    } else if (healthy === true) {
      dotCls = 'dot ok';
      statusText = 'API LINK';
    } else if (healthy === false) {
      dotCls = 'dot down pulse';
      statusText = 'API OFFLINE';
    } else {
      dotCls = 'dot demo';
      statusText = 'PROBING…';
    }
  }
  const probeTitle = 'Live /health probe, every 5 s';

  return (
    <>
      <div className={`app${navOpen ? ' nav-open' : ''}`}>
        <a className="skip-link" href="#content" onClick={skipToContent}>
          Skip to content
        </a>

        <aside
          className="sidebar"
          id="app-sidebar"
          ref={sidebarRef}
          {...(navOpen
            ? { role: 'dialog', 'aria-modal': true, 'aria-label': 'Navigation' }
            : {})}
        >
          <div className="brand">
            <BrandMark />
            <span className="brand-name">FlowState</span>
          </div>

          <nav className="sidebar-nav" aria-label="Primary">
            {NAV_GROUPS.map((g) => (
              <div
                key={g.id}
                className="nav-group"
                role="group"
                aria-labelledby={`nav-group-${g.id}`}
              >
                <div className="nav-group-label" id={`nav-group-${g.id}`}>
                  {g.label}
                </div>
                {g.items.map((n) => (
                  <NavItem key={n.to} entry={n} railed={railed} />
                ))}
              </div>
            ))}
          </nav>

          <div className="sidebar-foot">
            <NavItem entry={FIRST_RUN} railed={railed} />
            <div
              className="statusline"
              title={railed ? `${statusText} · ${probeTitle}` : probeTitle}
            >
              <span className={dotCls} />
              <span className="nav-label">{statusText}</span>
            </div>
            <button
              type="button"
              className="nav-item"
              onClick={openSettings}
              title={railed ? 'Settings' : undefined}
            >
              <Icon name="settings" />
              <span className="nav-label">Settings</span>
            </button>
          </div>
        </aside>

        <div className="main" ref={mainRef}>
          <header className="topbar">
            <button
              ref={menuRef}
              type="button"
              className="btn ghost icon-only topbar-btn topbar-menu"
              aria-label="Open navigation"
              aria-controls="app-sidebar"
              aria-expanded={navOpen}
              onClick={() => setNavOpen(true)}
            >
              <Icon name="menu" />
            </button>
            <Breadcrumb />
            <span className="topbar-spacer" />
            <CommandsButton onOpen={() => setPaletteOpen(true)} />
            <CorridorChip corridor={corridor} />
            <ThemeToggle />
          </header>

          <div className="banners">
            {authFailed && (
              <div className="banner auth-banner" role="alert">
                <Icon name="circle-alert" className="banner-icon" />
                <span className="banner-text">
                  API key rejected (401) — data is not loading and polling is stopped.{' '}
                  {isAuthRetryScheduled()
                    ? `Retrying once in ${Math.round(AUTH_RETRY_DELAY_MS / 1000)} s.`
                    : 'The automatic retry was already spent.'}
                </span>
                {/* a 401 is not always a wrong key: a restarting API or a rotated
                    key is transient, and the latch must not need a page reload */}
                <span className="banner-actions">
                  <button
                    type="button"
                    className="btn sm"
                    onClick={() => clearAuthFailure()}
                    title="Resume polling with the current key"
                  >
                    Retry now
                  </button>
                  <button type="button" className="btn sm" onClick={openSettings}>
                    Open Settings
                  </button>
                </span>
              </div>
            )}

            {offline && !authFailed && (
              <div className="banner offline-banner">
                <Icon name="triangle-alert" className="banner-icon" />
                <span className="banner-text">
                  API offline — showing DEMO DATA, not results from this server
                </span>
              </div>
            )}
          </div>

          <main className="content" id="content" tabIndex={-1}>
            <Outlet />
          </main>
        </div>

        {navOpen && <div className="nav-scrim" onClick={() => setNavOpen(false)} />}
      </div>

      <Toasts />
      {paletteOpen && <CommandPalette onClose={closePalette} onOpenSettings={openSettings} />}
      {settingsOpen && <SettingsDrawer onClose={() => setSettingsOpen(false)} />}
    </>
  );
}
