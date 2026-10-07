/** Light/dark theme: the user's preference, the theme actually showing, and
 * token reads for canvas/SVG code (docs/design/DASHBOARD_DESIGN.md §5.1).
 *
 * The preference is System (follow the OS), Light or Dark. A pinned choice is
 * stored under `flowstate.theme` and mirrored to `<html data-theme>`, which is
 * what tokens.css keys on; `index.html` applies the stored value before React
 * mounts so the page never flashes the wrong theme. System removes the
 * attribute and lets `prefers-color-scheme` decide.
 *
 * jsdom has no `window.matchMedia`, so every use is guarded and a missing one
 * reads as light. */

import { useSyncExternalStore } from 'react';

export type ThemePref = 'system' | 'light' | 'dark';
export type Theme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'flowstate.theme';
/** Dispatched on `window` whenever `setThemePref` changes the preference. */
export const THEME_EVENT = 'flowstate:themechange';

const DARK_QUERY = '(prefers-color-scheme: dark)';

function isThemePref(v: unknown): v is ThemePref {
  return v === 'system' || v === 'light' || v === 'dark';
}

function darkQuery(): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null;
  return window.matchMedia(DARK_QUERY);
}

/** The stored preference; 'system' when nothing (or something invalid) is
 * stored or storage is unavailable. */
export function getThemePref(): ThemePref {
  try {
    const v = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemePref(v) ? v : 'system';
  } catch {
    return 'system';
  }
}

/** Store the preference, apply it to `<html data-theme>` and notify
 * subscribers. Light/Dark pin the theme; System follows the OS. */
export function setThemePref(p: ThemePref): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, p);
  } catch {
    // storage blocked (private mode, quota): the choice still applies to this page
  }
  const root = document.documentElement;
  if (p === 'system') delete root.dataset.theme;
  else root.dataset.theme = p;
  window.dispatchEvent(new Event(THEME_EVENT));
}

/** The theme showing now: a pinned `data-theme` if set, otherwise the OS
 * preference (light when it can't be read). */
export function resolvedTheme(): Theme {
  const pinned = document.documentElement.dataset.theme;
  if (pinned === 'light' || pinned === 'dark') return pinned;
  return darkQuery()?.matches ? 'dark' : 'light';
}

function subscribe(onChange: () => void): () => void {
  const mq = darkQuery();
  mq?.addEventListener('change', onChange);
  window.addEventListener(THEME_EVENT, onChange);
  return () => {
    mq?.removeEventListener('change', onChange);
    window.removeEventListener(THEME_EVENT, onChange);
  };
}

/** The resolved theme as React state: re-renders when the OS preference
 * flips or `setThemePref` runs. Canvas painters list it in their effect
 * dependencies so a theme flip repaints. */
export function useResolvedTheme(): Theme {
  return useSyncExternalStore(subscribe, resolvedTheme, () => 'light');
}

/** A CSS custom property's computed value, trimmed (e.g.
 * `readToken('--viz-frame')`). Read at paint time so it follows the theme;
 * returns '' when the property is not defined. */
export function readToken(name: string, el?: Element): string {
  return getComputedStyle(el ?? document.documentElement)
    .getPropertyValue(name)
    .trim();
}
