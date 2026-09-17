import { useEffect, useSyncExternalStore } from 'react';
import { isAuthFailed, subscribeConnection } from '../api/client';

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
 * (and `/healthz` is auth-exempt, so the status dot would stay green). */
export function useAuthFailed(): boolean {
  return useSyncExternalStore(subscribeConnection, isAuthFailed, isAuthFailed);
}
