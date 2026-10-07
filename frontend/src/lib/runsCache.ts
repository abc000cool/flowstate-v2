/** The newest runs list any view has read, kept for the command palette.
 *
 * The palette offers "open a recent run" without a fetch of its own: every
 * `GET /runs` the views already make (the Runs table's 2 s poll, the Reports
 * picker, the guided first run) goes through `api/client.listRuns`, which
 * records its answer here. Nothing is read from the network on the palette's
 * behalf, so a palette opened before any of those views has loaded simply
 * lists no runs.
 *
 * The rows keep the provenance they were read with: `demo` is true when the
 * in-browser demo backend answered (the API was unreachable), so the palette
 * badges them DEMO like every other demo row (lib/demo). Reads are sequenced:
 * an answer overtaken by a newer request is dropped, and changing the API
 * settings drops whatever was recorded or still in flight under the old ones.
 */

import { useSyncExternalStore } from 'react';
import type { RunSummary } from '../api/types';

export interface CachedRuns {
  rows: RunSummary[];
  /** Served by the in-browser demo backend, not a server. */
  demo: boolean;
}

let current: CachedRuns | null = null;
/** Sequence of the newest read started, and of the newest one applied. */
let started = 0;
let applied = 0;
const listeners = new Set<() => void>();

function notify(): void {
  for (const l of [...listeners]) l();
}

/** Call when a runs read starts; pass the result to `recordRuns`. */
export function beginRunsRead(): number {
  started += 1;
  return started;
}

/** Record the answer of the read numbered `seq`, unless a newer read has
 * already been recorded (or the cache was cleared after `seq` started). */
export function recordRuns(seq: number, rows: RunSummary[], demo: boolean): void {
  if (seq <= applied) return;
  applied = seq;
  current = { rows, demo };
  notify();
}

/** Forget the recorded rows and anything still in flight (new API settings:
 * the old server's runs are not this one's). */
export function clearCachedRuns(): void {
  applied = started;
  if (current === null) return;
  current = null;
  notify();
}

export function getCachedRuns(): CachedRuns | null {
  return current;
}

export function subscribeCachedRuns(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

/** The recorded runs as React state; null until some view has read them. */
export function useCachedRuns(): CachedRuns | null {
  return useSyncExternalStore(subscribeCachedRuns, getCachedRuns, getCachedRuns);
}

/** The runs newest first: by `created_at` where both rows carry it, else by
 * list position, later first (the API lists in insertion order and its
 * timestamps have 1 s resolution, so equal stamps fall back to position). */
export function newestFirst(rows: RunSummary[]): RunSummary[] {
  return rows
    .map((r, i) => ({ r, i }))
    .sort((a, b) => {
      const ta = a.r.created_at ?? '';
      const tb = b.r.created_at ?? '';
      if (ta !== '' && tb !== '' && ta !== tb) return ta < tb ? 1 : -1;
      return b.i - a.i;
    })
    .map(({ r }) => r);
}
