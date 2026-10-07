/** lib/theme.ts: the stored preference, the resolved theme, the React hook,
 * token reads, and the pre-paint script in index.html that applies a pinned
 * theme before React mounts (docs/design/DASHBOARD_DESIGN.md §5.1). */

import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  getThemePref,
  nextThemePref,
  readToken,
  resolvedTheme,
  setThemePref,
  THEME_EVENT,
  THEME_PREF_LABELS,
  THEME_STORAGE_KEY,
  useResolvedTheme,
  useThemePref,
} from '../lib/theme';

/** A controllable `(prefers-color-scheme: dark)` media query. jsdom has no
 * matchMedia at all, which the module must treat as light. */
function stubOsTheme(dark: boolean): { flip(next: boolean): void; listeners: Set<() => void> } {
  const listeners = new Set<() => void>();
  const mql = {
    matches: dark,
    media: '(prefers-color-scheme: dark)',
    addEventListener: (_: string, cb: () => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
  };
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn(() => mql),
  });
  return {
    listeners,
    flip(next) {
      mql.matches = next;
      listeners.forEach((cb) => cb());
    },
  };
}

afterEach(() => {
  delete (window as { matchMedia?: unknown }).matchMedia;
  delete document.documentElement.dataset.theme;
  document.documentElement.style.cssText = '';
  window.localStorage.clear();
});

describe('theme preference', () => {
  it('defaults to system, and ignores a stored value it does not know', () => {
    expect(getThemePref()).toBe('system');
    window.localStorage.setItem(THEME_STORAGE_KEY, 'sepia');
    expect(getThemePref()).toBe('system');
  });

  it('pins light or dark on <html data-theme>, persists it, and announces the change', () => {
    const heard = vi.fn();
    window.addEventListener(THEME_EVENT, heard);
    setThemePref('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    expect(getThemePref()).toBe('dark');
    setThemePref('light');
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(getThemePref()).toBe('light');
    expect(heard).toHaveBeenCalledTimes(2);
    window.removeEventListener(THEME_EVENT, heard);
  });

  it('returns to the OS preference on system', () => {
    setThemePref('dark');
    setThemePref('system');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    expect(getThemePref()).toBe('system');
  });

  it('still applies the choice when storage throws', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    setThemePref('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    spy.mockRestore();
  });
});

describe('resolvedTheme', () => {
  it('reads as light when matchMedia is missing (jsdom)', () => {
    expect(typeof window.matchMedia).toBe('undefined');
    expect(resolvedTheme()).toBe('light');
  });

  it('follows the OS preference when nothing is pinned', () => {
    stubOsTheme(true);
    expect(resolvedTheme()).toBe('dark');
  });

  it('lets a pinned theme win over the OS preference', () => {
    stubOsTheme(true);
    setThemePref('light');
    expect(resolvedTheme()).toBe('light');
    stubOsTheme(false);
    setThemePref('dark');
    expect(resolvedTheme()).toBe('dark');
  });
});

describe('useResolvedTheme', () => {
  it('renders light without matchMedia and follows setThemePref', () => {
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe('light');
    act(() => setThemePref('dark'));
    expect(result.current).toBe('dark');
    act(() => setThemePref('system'));
    expect(result.current).toBe('light');
  });

  it('re-renders when the OS preference flips, and unsubscribes on unmount', () => {
    const os = stubOsTheme(false);
    const { result, unmount } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe('light');
    act(() => os.flip(true));
    expect(result.current).toBe('dark');
    expect(os.listeners.size).toBe(1);
    unmount();
    expect(os.listeners.size).toBe(0);
  });
});

describe('useThemePref', () => {
  it('reports the stored preference and follows setThemePref', () => {
    const { result } = renderHook(() => useThemePref());
    expect(result.current).toBe('system');
    act(() => setThemePref('light'));
    expect(result.current).toBe('light');
    act(() => setThemePref('dark'));
    expect(result.current).toBe('dark');
  });

  it('applies a preference another tab stored', () => {
    const { result } = renderHook(() => useThemePref());
    act(() => {
      window.localStorage.setItem(THEME_STORAGE_KEY, 'dark');
      window.dispatchEvent(new StorageEvent('storage', { key: THEME_STORAGE_KEY }));
    });
    expect(result.current).toBe('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    act(() => {
      window.localStorage.setItem(THEME_STORAGE_KEY, 'system');
      window.dispatchEvent(new StorageEvent('storage', { key: THEME_STORAGE_KEY }));
    });
    expect(result.current).toBe('system');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });
});

describe('nextThemePref', () => {
  it('cycles System, Light, Dark and back, with a label for each', () => {
    expect(nextThemePref('system')).toBe('light');
    expect(nextThemePref('light')).toBe('dark');
    expect(nextThemePref('dark')).toBe('system');
    expect(Object.values(THEME_PREF_LABELS)).toEqual(['System', 'Light', 'Dark']);
  });
});

describe('readToken', () => {
  it('reads a custom property from <html> by default, trimmed', () => {
    document.documentElement.style.setProperty('--viz-frame', '  #dad9d6 ');
    expect(readToken('--viz-frame')).toBe('#dad9d6');
  });

  it('reads from a given element, and is empty for an undefined property', () => {
    const el = document.createElement('div');
    el.style.setProperty('--viz-grid', '#e9e8e6');
    document.body.appendChild(el);
    expect(readToken('--viz-grid', el)).toBe('#e9e8e6');
    expect(readToken('--not-a-token')).toBe('');
    el.remove();
  });
});

describe('index.html pre-paint script', () => {
  /** The inline (non-module) <script> in index.html, run against this document. */
  async function runPrePaintScript(): Promise<void> {
    const fsModule = 'node:fs';
    const fs = (await import(/* @vite-ignore */ fsModule)) as {
      readFileSync(path: string, encoding: 'utf8'): string;
    };
    // frontend/index.html, from this file's file:// URL (src/test/theme.test.ts)
    const indexPath = decodeURIComponent(
      import.meta.url.replace(/^file:\/\//, '').replace(/src\/test\/[^/]+$/, 'index.html'),
    );
    const html = fs.readFileSync(indexPath, 'utf8');
    const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
    expect(inline).toHaveLength(1);
    expect(inline[0]).toContain(`'${THEME_STORAGE_KEY}'`);
    new Function(inline[0])();
  }

  it('applies a stored light or dark before React mounts', async () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark');
    await runPrePaintScript();
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('leaves the OS in charge for system or an unknown value', async () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'system');
    await runPrePaintScript();
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    window.localStorage.setItem(THEME_STORAGE_KEY, 'sepia');
    await runPrePaintScript();
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });
});
