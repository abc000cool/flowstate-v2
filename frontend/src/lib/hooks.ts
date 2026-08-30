import { useEffect } from 'react';

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
