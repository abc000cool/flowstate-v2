/**
 * Theme plumbing. The page follows prefers-color-scheme; a host page can pin a theme
 * with ?theme=light|dark (index.html applies it before first paint; applyThemeParam
 * repeats it idempotently). Canvas and chrome colours are read from the CSS tokens at
 * paint time, so a theme flip only needs a repaint.
 */

export type Theme = 'light' | 'dark';

/** The pinned theme from a query string, or null to follow the OS. */
export function themeFromParam(search: string): Theme | null {
  const t = new URLSearchParams(search).get('theme');
  return t === 'light' || t === 'dark' ? t : null;
}

export function applyThemeParam(search: string, root: HTMLElement = document.documentElement): Theme | null {
  const t = themeFromParam(search);
  if (t) root.dataset.theme = t;
  return t;
}

function darkQuery(): MediaQueryList | null {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null;
}

export function resolvedTheme(root: HTMLElement = document.documentElement): Theme {
  const pinned = root.dataset.theme;
  if (pinned === 'light' || pinned === 'dark') return pinned;
  return darkQuery()?.matches ? 'dark' : 'light';
}

/** Calls `cb` whenever the OS colour scheme changes (pinned themes never change). */
export function onThemeChange(cb: () => void): void {
  const mq = darkQuery();
  if (!mq) return;
  mq.addEventListener('change', cb);
}

/** A CSS custom property's computed value, e.g. readToken('--viz-frame') → "#dad9d6". */
export function readToken(name: string, el: Element = document.documentElement): string {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

/** Every token a canvas view needs, read once per paint. */
export interface VizTokens {
  surface: string;
  frame: string;
  axis: string;
  tick: string;
  grid: string;
  crosshair: string;
  halo: string;
  nullBg: string;
  nullHatch: string;
  track: string;
  trackEdge: string;
  carOutline: string;
  traj: string;
  ink: string;
  inkSecondary: string;
  baseline: string;
  series1: string;
  fontSans: string;
  fontMono: string;
}

export function readVizTokens(): VizTokens {
  const r = (n: string): string => readToken(n);
  return {
    surface: r('--viz-surface'),
    frame: r('--viz-frame'),
    axis: r('--viz-axis'),
    tick: r('--viz-tick-text'),
    grid: r('--viz-grid'),
    crosshair: r('--viz-crosshair'),
    halo: r('--viz-crosshair-halo'),
    nullBg: r('--viz-null-bg'),
    nullHatch: r('--viz-null-hatch'),
    track: r('--viz-track'),
    trackEdge: r('--viz-track-edge'),
    carOutline: r('--viz-car-outline'),
    traj: r('--viz-traj'),
    ink: r('--text-primary'),
    inkSecondary: r('--text-secondary'),
    baseline: r('--viz-baseline'),
    series1: r('--viz-series-1'),
    fontSans: r('--font-sans'),
    fontMono: r('--font-mono'),
  };
}

/** A 6×6 45° hatch pattern (1 px line every 6 px) for no-data bins (§7.1). */
export function hatchPattern(ctx: CanvasRenderingContext2D, bg: string, line: string, dpr = 1): CanvasPattern | null {
  const n = Math.max(6, Math.round(6 * dpr));
  const c = document.createElement('canvas');
  c.width = n;
  c.height = n;
  const g = c.getContext('2d');
  if (!g) return null;
  g.fillStyle = bg;
  g.fillRect(0, 0, n, n);
  g.strokeStyle = line;
  g.lineWidth = Math.max(1, dpr);
  g.beginPath();
  // a diagonal plus its two wrapped corners so the tile repeats seamlessly
  g.moveTo(0, n);
  g.lineTo(n, 0);
  g.moveTo(-1, 1);
  g.lineTo(1, -1);
  g.moveTo(n - 1, n + 1);
  g.lineTo(n + 1, n - 1);
  g.stroke();
  return ctx.createPattern(c, 'repeat');
}
