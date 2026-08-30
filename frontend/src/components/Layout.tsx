/** App shell: left rail nav (Scenarios/Runs/Sweeps/Reports) with a live
 * status dot from /healthz polling, top bar with the FLOWSTATE wordmark and
 * active corridor name, offline-fallback banner, settings drawer. */

import { useCallback, useEffect, useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { checkHealth, isMockEnv, setOfflineFallback } from '../api/client';
import { usePoll } from '../lib/hooks';
import { useAppState } from './AppContext';
import { SettingsDrawer } from './SettingsDrawer';
import { Toasts } from './toast';

const NAV = [
  {
    to: '/scenarios',
    label: 'Scenarios',
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <circle cx="7" cy="7" r="5" />
        <circle cx="7" cy="2.4" r="1" fill="currentColor" stroke="none" />
        <circle cx="11" cy="9" r="1" fill="currentColor" stroke="none" />
        <circle cx="3" cy="9" r="1" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
  {
    to: '/runs',
    label: 'Runs',
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M2 11 L5 6 L8 9 L12 3" />
        <path d="M9.5 3 H12 V5.5" />
      </svg>
    ),
  },
  {
    to: '/sweeps',
    label: 'Sweeps',
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <rect x="1.5" y="1.5" width="4.6" height="4.6" rx="1" />
        <rect x="7.9" y="1.5" width="4.6" height="4.6" rx="1" />
        <rect x="1.5" y="7.9" width="4.6" height="4.6" rx="1" />
        <rect x="7.9" y="7.9" width="4.6" height="4.6" rx="1" fill="currentColor" />
      </svg>
    ),
  },
  {
    to: '/reports',
    label: 'Reports',
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M3.5 1.5 h5 l2.5 2.5 v8.5 h-7.5 z" />
        <path d="M5.5 7 h3 M5.5 9.5 h3" />
      </svg>
    ),
  },
];

function useUtcClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return `${now.toISOString().slice(11, 19)} UTC`;
}

export function Layout(): JSX.Element {
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { corridor } = useAppState();
  const clock = useUtcClock();
  const mockEnv = isMockEnv();

  const poll = useCallback(async () => {
    const ok = await checkHealth();
    setHealthy(ok);
    if (!mockEnv) setOfflineFallback(!ok);
  }, [mockEnv]);
  usePoll(poll, 5000);

  const offline = !mockEnv && healthy === false;

  let dotCls = 'dot demo pulse';
  let statusText = 'DEMO DATA';
  if (!mockEnv) {
    if (healthy === true) {
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

  return (
    <div className="app">
      <aside className="rail">
        <nav className="rail-nav" aria-label="Primary">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) => `rail-link${isActive ? ' active' : ''}`}
            >
              {n.icon}
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="rail-foot">
          <div className="statusline" title="Live /healthz probe, every 5 s">
            <span className={dotCls} />
            {statusText}
          </div>
          <button className="btn sm" onClick={() => setSettingsOpen(true)}>
            <svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.3">
              <circle cx="6" cy="6" r="2" />
              <path d="M6 0.8 v1.8 M6 9.4 v1.8 M0.8 6 h1.8 M9.4 6 h1.8 M2.3 2.3 l1.3 1.3 M8.4 8.4 l1.3 1.3 M9.7 2.3 L8.4 3.6 M3.6 8.4 L2.3 9.7" />
            </svg>
            Settings
          </button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <span className="wordmark">
            FLOW<b>STATE</b>
          </span>
          <span className="topbar-sep" />
          <span className="corridor-name">
            CORRIDOR <b>{corridor ?? '— none active —'}</b>
          </span>
          <span className="topbar-spacer" />
          <span className="utc-clock">{clock}</span>
        </header>

        {offline && <div className="offline-banner">API offline — showing demo data</div>}

        <main className="content">
          <Outlet />
        </main>
      </div>

      <Toasts />
      {settingsOpen && <SettingsDrawer onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
