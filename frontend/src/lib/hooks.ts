import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { isAuthFailed, isOfflineFallback, subscribeConnection } from '../api/client';

/** Run `fn` immediately and then every `ms` milliseconds. Pass ms=null to
 * pause. `fn` must be referentially stable (useCallback). */
export function usePoll(fn: () => void | Promise<void>, ms: number | null): void {
  useEffect(() => {
    if (ms === null) return;
    let live = true;
    const tick = (): void => {
      if (live) void fn();
    };
    tick();
    const id = window.setInterval(tick, ms);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [fn, ms]);
}

/** True once the API rejected the configured key. Every view gates its poll
 * on this: retrying a rejected key at 2 s forever produces nothing but 401s
 * (and `/health` is auth-exempt, so the status dot would stay green). */
export function useAuthFailed(): boolean {
  return useSyncExternalStore(subscribeConnection, isAuthFailed, isAuthFailed);
}

/** True while the API is unreachable and reads are being served from the
 * in-browser demo backend. Writes are refused in that state
 * (`api/client.assertWritable`), so every control that would launch real
 * compute disables itself and says why, instead of failing on the click. */
export function useOfflineFallback(): boolean {
  return useSyncExternalStore(subscribeConnection, isOfflineFallback, isOfflineFallback);
}

/** Where keyboard focus goes after a busy action (docs/design/DASHBOARD_DESIGN.md
 * §9.1 "Busy", §11.2). See `useBusyFocus`. */
export interface BusyFocus {
  /** Call first thing in the action, with the control that started it. Focus
   * is handed on later only when it was on that control now. */
  begin(control: Element | null | undefined): void;
  /** Where focus goes once `busy` clears: the control again after a refusal,
   * or the logical next element after a success. Call it before the
   * `setBusy(false)` that ends the action. */
  after(target: () => HTMLElement | null | undefined): void;
}

/** Hand keyboard focus on after a busy action.
 *
 * A control disabled while its action runs drops keyboard focus to <body>
 * (so does one the action removes, like a finished guided step), and a
 * keyboard user starts again from the top of the page. This puts it where
 * the user would look next. Nothing moves unless focus was on the control
 * when the action started — a pointer user in a browser that does not focus
 * a clicked button is left alone — and nothing moves once focus has gone
 * elsewhere: only from <body>, or from the control itself where a browser
 * keeps focus on a disabled button.
 *
 * An action that navigates away has no control left to return to: it passes
 * `FOCUS_CONTENT_STATE` to `navigate`, and the shell focuses the new page's
 * content (`components/Layout.tsx`). */
export function useBusyFocus(busy: boolean): BusyFocus {
  const from = useRef<Element | null>(null);
  const to = useRef<(() => HTMLElement | null | undefined) | null>(null);
  useEffect(() => {
    if (busy || to.current === null) return;
    const target = to.current;
    const control = from.current;
    to.current = null;
    from.current = null;
    const active = document.activeElement;
    if (active === null || active === document.body || active === control) target()?.focus();
  }, [busy]);
  return useMemo(
    () => ({
      begin(control) {
        from.current = control != null && document.activeElement === control ? control : null;
        to.current = null;
      },
      after(target) {
        if (from.current !== null) to.current = target;
      },
    }),
    [],
  );
}

/** Router state for a navigation that follows an action on the page being
 * left (a launch that lands on Runs, a report request that lands on Reports):
 * the shell moves focus to the new page's `<main id="content">`, since the
 * control that had it is gone with its page. */
export const FOCUS_CONTENT_STATE = { focusContent: true } as const;

/** Whether a location's router state asks for `FOCUS_CONTENT_STATE`. */
export function wantsContentFocus(state: unknown): boolean {
  return (
    typeof state === 'object' &&
    state !== null &&
    (state as { focusContent?: unknown }).focusContent === true
  );
}
