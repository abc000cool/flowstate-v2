/** Settings drawer (docs/design/DASHBOARD_DESIGN.md §9.16): Connection (API
 * base URL and key, saved on Save) and Appearance (System / Light / Dark,
 * applied at once). A modal layer: focus moves in on open, Tab is trapped,
 * Escape closes, focus returns to the opener, and the app behind is inert. */

import { useEffect, useRef, useState } from 'react';
import { DEFAULT_API_KEY, DEFAULT_BASE_URL, getSettings, saveSettings } from '../api/client';
import { useAuthFailed } from '../lib/hooks';
import {
  setThemePref,
  THEME_PREF_LABELS,
  useThemePref,
  type ThemePref,
} from '../lib/theme';
import { Icon, type IconName } from './icons';
import { toast } from './toast';

/** What Tab can land on inside a modal layer. */
export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
  'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Keep Tab and Shift+Tab inside `container`. Call it from a keydown handler;
 * it acts on Tab only and never moves focus for any other key. */
export function trapTabKey(e: KeyboardEvent, container: HTMLElement): void {
  if (e.key !== 'Tab') return;
  const items = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE));
  if (items.length === 0) {
    e.preventDefault();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  const inside = active instanceof Node && container.contains(active);
  if (e.shiftKey && (active === first || !inside)) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && (active === last || !inside)) {
    e.preventDefault();
    first.focus();
  }
}

const THEME_OPTIONS: { pref: ThemePref; icon: IconName }[] = [
  { pref: 'system', icon: 'monitor' },
  { pref: 'light', icon: 'sun' },
  { pref: 'dark', icon: 'moon' },
];

export function SettingsDrawer({ onClose }: { onClose: () => void }): JSX.Element {
  const current = getSettings();
  const authFailed = useAuthFailed();
  const themePref = useThemePref();
  const [baseUrl, setBaseUrl] = useState(current.baseUrl);
  const [apiKey, setApiKey] = useState(current.apiKey);
  const panelRef = useRef<HTMLDivElement>(null);
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  // mount-only: capture the opener, make the app behind inert, move focus in,
  // and undo all three on close
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const app = document.querySelector('.app');
    app?.setAttribute('inert', '');
    firstFieldRef.current?.focus();
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeRef.current();
      } else if (e.key === 'Tab' && panelRef.current) {
        trapTabKey(e, panelRef.current);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      app?.removeAttribute('inert');
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  const save = (): void => {
    saveSettings({ baseUrl, apiKey });
    toast('ok', 'API settings saved');
    onClose();
  };

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <div className="drawer" role="dialog" aria-modal="true" aria-label="Settings" ref={panelRef}>
        <div className="drawer-head">
          <h2>Settings</h2>
          <button
            type="button"
            className="btn ghost icon-only drawer-close"
            aria-label="Close settings"
            onClick={onClose}
          >
            <Icon name="x" />
          </button>
        </div>

        <div className="drawer-body">
          <section className="drawer-section" aria-labelledby="set-connection">
            <h3 id="set-connection">Connection</h3>
            <div className="field">
              <label htmlFor="set-base">API base URL</label>
              <input
                id="set-base"
                ref={firstFieldRef}
                className="input mono"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={DEFAULT_BASE_URL}
              />
            </div>
            <div className="field">
              <label htmlFor="set-key">API key (X-API-Key)</label>
              <input
                id="set-key"
                className="input mono"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={DEFAULT_API_KEY}
                aria-describedby={authFailed ? 'set-key-hint' : undefined}
                aria-invalid={authFailed || undefined}
              />
              {authFailed && (
                <span className="hint-amber" id="set-key-hint">
                  the current key was rejected (401) — saving a new one resumes loading
                </span>
              )}
            </div>
            <p className="drawer-note">
              Stored locally in this browser. The dev proxy forwards <span className="mono">/api</span>{' '}
              to <span className="mono">localhost:8000</span>; set an absolute base URL to reach a
              remote API. Set <span className="mono">VITE_MOCK=1</span> at build time to force demo
              data.
            </p>
          </section>

          <section className="drawer-section" aria-labelledby="set-appearance">
            <h3 id="set-appearance">Appearance</h3>
            <div className="drawer-field">
              <span className="drawer-label" id="set-theme-label">
                Theme
              </span>
              <div className="theme-seg" role="radiogroup" aria-labelledby="set-theme-label">
                {THEME_OPTIONS.map(({ pref, icon }) => (
                  <label key={pref} className="theme-seg-option">
                    <input
                      type="radio"
                      name="flowstate-theme"
                      value={pref}
                      checked={themePref === pref}
                      onChange={() => setThemePref(pref)}
                    />
                    <Icon name={icon} size={14} />
                    {THEME_PREF_LABELS[pref]}
                  </label>
                ))}
              </div>
              <p className="drawer-note">
                System follows your operating system. The choice applies at once and is remembered
                in this browser.
              </p>
            </div>
          </section>
        </div>

        <div className="drawer-foot">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" onClick={save}>
            Save
          </button>
        </div>
      </div>
    </>
  );
}
