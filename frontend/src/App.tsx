import { Navigate, Route, Routes } from 'react-router-dom';
import { AppStateProvider } from './components/AppContext';
import { Layout } from './components/Layout';
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
