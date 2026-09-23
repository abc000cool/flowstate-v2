/** Minimal toast bus + renderer. Restrained: top-right, mono, auto-dismiss. */

import { useEffect, useState } from 'react';

export type ToastKind = 'info' | 'ok' | 'error';

interface ToastItem {
  id: number;
  kind: ToastKind;
  msg: string;
}

type Listener = (items: ToastItem[]) => void;

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<Listener>();

function emit(): void {
  for (const l of listeners) l([...items]);
}

/** How long an `info`/`ok` toast stays up: long enough to read, short enough
 * not to sit over the view. */
export const TOAST_MS = 5200;

/** How long an `error` toast stays up. A refusal is the thing the operator has
 * to act on — a 409 that says a corridor of that name already exists is the
 * whole answer to the click — so it outlives the acknowledgement toasts and
 * can be dismissed by hand rather than only by waiting. */
export const ERROR_TOAST_MS = 15_000;

/** Remove the toast with this id, if it is still up. */
export function dismissToast(id: number): void {
  items = items.filter((t) => t.id !== id);
  emit();
}

export function toast(kind: ToastKind, msg: string): void {
  const id = nextId++;
  items = [...items, { id, kind, msg }].slice(-4);
  emit();
  setTimeout(() => dismissToast(id), kind === 'error' ? ERROR_TOAST_MS : TOAST_MS);
}

export function toastError(err: unknown, prefix = ''): void {
  const msg = err instanceof Error ? err.message : String(err);
  toast('error', prefix ? `${prefix}: ${msg}` : msg);
}

export function Toasts(): JSX.Element {
  const [list, setList] = useState<ToastItem[]>([]);
  useEffect(() => {
    const l: Listener = setList;
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {list.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          {t.msg}
          <button
            className="toast-x"
            aria-label={`dismiss: ${t.msg}`}
            onClick={() => dismissToast(t.id)}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
