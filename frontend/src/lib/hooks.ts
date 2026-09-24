import { useEffect, useSyncExternalStore } from 'react';
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
