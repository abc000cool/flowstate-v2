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

export function toast(kind: ToastKind, msg: string): void {
  const id = nextId++;
  items = [...items, { id, kind, msg }].slice(-4);
  emit();
  setTimeout(() => {
    items = items.filter((t) => t.id !== id);
    emit();
  }, 5200);
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
        </div>
      ))}
    </div>
  );
}
