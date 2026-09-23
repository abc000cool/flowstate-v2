import { Navigate, Route, Routes } from 'react-router-dom';
import { AppStateProvider } from './components/AppContext';
import { GuidedFirstRun } from './components/GuidedFirstRun';
import { Layout } from './components/Layout';
import { OnboardView } from './views/OnboardView';
import { ReportsView } from './views/ReportsView';
import { RunDetailView } from './views/RunDetailView';
import { RunsView } from './views/RunsView';
import { ScenariosView } from './views/ScenariosView';
import { SweepsView } from './views/SweepsView';

export function App(): JSX.Element {
  return (
    <AppStateProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/scenarios" replace />} />
          {/* the rail's "First run": the guided panel on its own, always
              shown (the Scenarios mount hides itself once the server has runs) */}
          <Route path="/first-run" element={<GuidedFirstRun standalone />} />
          <Route path="/onboard" element={<OnboardView />} />
          <Route path="/scenarios" element={<ScenariosView />} />
          <Route path="/runs" element={<RunsView />} />
          <Route path="/runs/:runId" element={<RunDetailView />} />
          <Route path="/sweeps" element={<SweepsView />} />
          <Route path="/reports" element={<ReportsView />} />
          <Route path="*" element={<Navigate to="/scenarios" replace />} />
        </Route>
      </Routes>
    </AppStateProvider>
  );
}
