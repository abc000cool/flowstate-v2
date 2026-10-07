import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
// Order matters: tokens, then base → shell → components → viz → views
// (docs/design/DASHBOARD_DESIGN.md §12.2). Plain global CSS, no modules.
import './styles/tokens.css';
import './styles/base.css';
import './styles/shell.css';
import './styles/components.css';
import './styles/viz.css';
import './styles/views/scenarios.css';
import './styles/views/onboard.css';
import './styles/views/runs.css';
import './styles/views/run-detail.css';
import './styles/views/sweeps.css';
import './styles/views/compare.css';
import './styles/views/reports.css';
import './styles/views/first-run.css';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';

const rootEl = document.getElementById('root');
if (!rootEl) throw new Error('missing #root');

createRoot(rootEl).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
