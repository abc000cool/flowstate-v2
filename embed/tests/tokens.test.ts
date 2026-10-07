/** The embed's copy of the Paper & Signal tokens keeps the brief's theme mechanics. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(join(__dirname, '..', 'src', 'tokens.css'), 'utf8');

function blockAfter(marker: string): string {
  const i = css.indexOf(marker);
  expect(i, marker).toBeGreaterThanOrEqual(0);
  const open = css.indexOf('{', i + marker.length - 1);
  let depth = 0;
  for (let k = open; k < css.length; k++) {
    if (css[k] === '{') depth++;
    else if (css[k] === '}' && --depth === 0) return css.slice(open + 1, k);
  }
  throw new Error(`unclosed block after ${marker}`);
}

function decls(body: string): Map<string, string> {
  const out = new Map<string, string>();
  const clean = body.replace(/\/\*[\s\S]*?\*\//g, '');
  for (const m of clean.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out.set(m[1], m[2].trim());
  return out;
}

describe('tokens.css', () => {
  const light = decls(blockAfter('/* ---------- light theme (default) ---------- */\n:root {'));
  const darkOs = decls(blockAfter(":root:not([data-theme='light']) {"));
  const darkPinned = decls(blockAfter(":root[data-theme='dark'] {"));

  it('declares the dark theme twice with identical bodies', () => {
    expect([...darkOs.entries()]).toEqual([...darkPinned.entries()]);
  });
  it('defines every light colour token in dark too', () => {
    for (const k of light.keys()) expect(darkOs.has(k), k).toBe(true);
  });
  it('keeps the brief values for surfaces, text and accent', () => {
    expect(light.get('--bg-surface')).toBe('#ffffff');
    expect(light.get('--accent-solid')).toBe('#256abf');
    expect(darkOs.get('--gray-2')).toBe('#191918');
    expect(darkOs.get('--accent-text')).toBe('#86b6ef');
  });
  it('ships no web fonts: system stacks only', () => {
    const fonts = decls(blockAfter('/* ---------- theme-invariant ---------- */\n:root {'));
    expect(fonts.get('--font-sans')).toMatch(/^system-ui,/);
    expect(fonts.get('--font-mono')).toMatch(/^ui-monospace,/);
    for (const k of ['--font-sans', '--font-mono']) expect(fonts.get(k), k).not.toMatch(/Inter|JetBrains/);
  });
  it('uses canvas-parsable colours for the tokens canvases read', () => {
    for (const map of [light, darkOs]) {
      for (const [k, v] of map) if (k.startsWith('--viz-')) expect(v, k).not.toMatch(/color-mix/);
    }
  });
});
