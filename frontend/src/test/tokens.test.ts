/** tokens.css invariants (docs/design/DASHBOARD_DESIGN.md §5).
 *
 * The dark theme is declared twice, once for the OS preference and once for a
 * pinned `data-theme="dark"`, and the two bodies must stay identical. Every
 * themed token must exist in both the light and the dark theme, or one theme
 * silently falls back to the other's value. And every `var(--x)` in the
 * stylesheets must resolve to a declared custom property. */

import { describe, expect, it } from 'vitest';

interface Fs {
  readFileSync(path: string, encoding: 'utf8'): string;
  readdirSync(path: string): string[];
}

/** The test file is typechecked by the browser tsconfig (no @types/node), so
 * the node builtin is imported dynamically, as in metrics.test.ts. */
async function nodeFs(): Promise<Fs> {
  const fsModule = 'node:fs';
  return (await import(/* @vite-ignore */ fsModule)) as Fs;
}

// src/styles/ as a path string, derived from this file's file:// URL. Not
// `new URL('../styles/', import.meta.url)`: Vite rewrites that pattern into an
// asset URL, and node:fs refuses jsdom's URL objects anyway.
const STYLES = decodeURIComponent(
  import.meta.url.replace(/^file:\/\//, '').replace(/[^/]+\/[^/]+$/, 'styles/'),
);

async function readStyles(): Promise<Map<string, string>> {
  const fs = await nodeFs();
  const out = new Map<string, string>();
  for (const dir of ['', 'views/']) {
    for (const f of fs.readdirSync(STYLES + dir)) {
      if (f.endsWith('.css')) out.set(dir + f, fs.readFileSync(STYLES + dir + f, 'utf8'));
    }
  }
  return out;
}

const stripComments = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, '');

/** `prop: value` declarations of a block body, whitespace-normalised, in order. */
function declarations(body: string): [string, string][] {
  return body
    .split(';')
    .map((d) => d.trim())
    .filter(Boolean)
    .map((d) => {
      const i = d.indexOf(':');
      return [d.slice(0, i).trim(), d.slice(i + 1).replace(/\s+/g, ' ').trim()];
    });
}

function themeBlocks(tokensCss: string): {
  light: [string, string][];
  darkOs: [string, string][];
  darkPinned: [string, string][];
} {
  const css = stripComments(tokensCss);
  const darkOs = css.match(
    /@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root:not\(\[data-theme='light'\]\)\s*\{([^}]*)\}\s*\}/,
  );
  const darkPinned = css.match(/:root\[data-theme='dark'\]\s*\{([^}]*)\}/);
  // the light theme is the top-level `:root { … }` block that sets color-scheme: light
  const light = [...css.matchAll(/(?:^|\})\s*:root\s*\{([^}]*)\}/g)]
    .map((m) => m[1])
    .find((body) => /color-scheme:\s*light/.test(body));
  if (!darkOs || !darkPinned || light === undefined) {
    throw new Error('tokens.css: light, OS-dark or pinned-dark block not found');
  }
  return {
    light: declarations(light),
    darkOs: declarations(darkOs[1]),
    darkPinned: declarations(darkPinned[1]),
  };
}

const names = (decls: [string, string][]): string[] =>
  decls.map(([p]) => p).filter((p) => p.startsWith('--')).sort();

describe('tokens.css themes', () => {
  it('declares the two dark blocks with identical bodies', async () => {
    const tokens = (await readStyles()).get('tokens.css') ?? '';
    const { darkOs, darkPinned } = themeBlocks(tokens);
    expect(darkOs.length).toBeGreaterThan(50);
    expect(darkPinned).toEqual(darkOs);
  });

  it('defines every light-theme token in the dark theme, and no dark-only token', async () => {
    const tokens = (await readStyles()).get('tokens.css') ?? '';
    const { light, darkOs } = themeBlocks(tokens);
    const lightNames = names(light);
    const darkNames = names(darkOs);
    expect(lightNames.filter((n) => !darkNames.includes(n))).toEqual([]);
    expect(darkNames.filter((n) => !lightNames.includes(n))).toEqual([]);
  });

  it('sets color-scheme per theme', async () => {
    const tokens = (await readStyles()).get('tokens.css') ?? '';
    const { light, darkOs, darkPinned } = themeBlocks(tokens);
    const scheme = (d: [string, string][]): string | undefined =>
      d.find(([p]) => p === 'color-scheme')?.[1];
    expect(scheme(light)).toBe('light');
    expect(scheme(darkOs)).toBe('dark');
    expect(scheme(darkPinned)).toBe('dark');
  });
});

describe('stylesheets', () => {
  it('reference only custom properties that some stylesheet declares', async () => {
    const styles = await readStyles();
    expect(styles.has('app.css')).toBe(false);
    const declared = new Set<string>();
    for (const css of styles.values()) {
      for (const m of stripComments(css).matchAll(/(--[\w-]+)\s*:/g)) declared.add(m[1]);
    }
    const missing: string[] = [];
    for (const [file, css] of styles) {
      // a var() with a fallback may name an undeclared property on purpose
      for (const m of stripComments(css).matchAll(/var\(\s*(--[\w-]+)\s*([,)])/g)) {
        if (m[2] === ')' && !declared.has(m[1])) missing.push(`${file}: ${m[1]}`);
      }
    }
    expect(missing).toEqual([]);
  });
});

/** Stylesheets already migrated to Paper & Signal. They read colors from
 * tokens only, so both themes follow; step 5 (§12.2) widens this list to
 * every stylesheet once the legacy aliases are deleted. */
const MIGRATED = ['base.css', 'shell.css', 'views/runs.css', 'views/reports.css'];

/** The legacy alias names of tokens.css (§5, "DELETE in step 5"). */
const LEGACY_ALIAS =
  /var\(--(bg|panel|panel-edge|panel-raised|panel-inset|text|muted|faint|accent|accent-dim|amber|amber-dim|danger|danger-dim|ok|ok-dim|s[1-6]|r[1-3]|font-ui|card-shadow|ring)\)/;

describe('migrated stylesheets', () => {
  it('use no literal colors, so light and dark both follow the tokens', async () => {
    const styles = await readStyles();
    const literal: string[] = [];
    for (const file of MIGRATED) {
      const css = stripComments(styles.get(file) ?? '');
      expect(css.length, file).toBeGreaterThan(0);
      for (const m of css.matchAll(/#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/g)) {
        literal.push(`${file}: ${m[0]}`);
      }
    }
    expect(literal).toEqual([]);
  });

  it('use no legacy aliases and no uppercase transforms (§5.2)', async () => {
    const styles = await readStyles();
    const found: string[] = [];
    for (const file of MIGRATED) {
      const css = stripComments(styles.get(file) ?? '');
      const alias = css.match(LEGACY_ALIAS);
      if (alias) found.push(`${file}: ${alias[0]}`);
      if (/text-transform:\s*uppercase/.test(css)) found.push(`${file}: text-transform`);
    }
    expect(found).toEqual([]);
  });
});
