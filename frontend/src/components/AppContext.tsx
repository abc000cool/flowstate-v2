/** App-level context: active corridor name (topbar) + link status. */

import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';

interface AppState {
  corridor: string | null;
  setCorridor: (name: string | null) => void;
}

const Ctx = createContext<AppState>({ corridor: null, setCorridor: () => undefined });

export function AppStateProvider({ children }: { children: ReactNode }): JSX.Element {
  const [corridor, setCorridor] = useState<string | null>(null);
  const value = useMemo(() => ({ corridor, setCorridor }), [corridor]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppState(): AppState {
  return useContext(Ctx);
}
