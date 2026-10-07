/** Run detail's "Report on this run" and the Reports `?select=` preselect
 * (docs/design/DASHBOARD_DESIGN.md §10.4 P2).
 *
 * A finished micro run links to `/reports?select=<run_id>`, where the run
 * arrives checked. A run that cannot be reported keeps the button, disabled,
 * with the reason beside it — above all a macro run, which the API refuses to
 * report on (CLAUDE.md §5.6). Reports explains every `?select=` it cannot
 * honour (macro, failed, not listed), waits for a run still computing, and
 * drops the parameter once it is dealt with. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, setOfflineFallback } from '../api/client';
import { AppStateProvider } from '../components/AppContext';
import { reportBlockReason, RunDetailView } from '../views/RunDetailView';
import { ReportsView } from '../views/ReportsView';
import { auditA11y, formatViolations } from './a11y';

function run(id: string, status: string, tier = 'micro'): Record<string, unknown> {
  return {
    run_id: id,
    scenario_id: 'scn_i24',
    scenario_name: 'i24_replica',
    sweep_id: null,
    status,
    tier,
    config_hash: `hash-${id}`,
    seeded: false,
    progress: { completed_replicates: status === 'done' ? 20 : 3, total_replicates: 20 },
    seeds: [1, 2, 3],
    error: status === 'failed' ? 'boom' : null,
    error_kind: status === 'failed' ? 'ValueError' : null,
    created_at: '2026-10-01T00:00:00',
  };
}

let RUNS: Record<string, unknown>[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

async function api(input: RequestInfo | URL): Promise<Response> {
  const url = String(input);
  const path = url.replace(/^.*\/api\/v1/, '');
  if (path === '/runs') return json(RUNS);
  const one = /^\/runs\/([^/]+)$/.exec(path);
  if (one) {
    const r = RUNS.find((x) => x.run_id === one[1]);
    return r ? json(r) : json({ detail: 'no such run' }, 404);
  }
  // metrics, heatmaps, criteria, corridors, reports, scenarios: not needed
  if (path === '/reports' || path === '/corridors' || path === '/scenarios') return json([]);
  return json({ detail: 'not here' }, 404);
}

function Where(): JSX.Element {
  const { pathname, search } = useLocation();
  return <div data-testid="where">{`${pathname}${search}`}</div>;
}

function renderAt(path: string): void {
  render(
    <AppStateProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunDetailView />} />
          <Route path="/reports" element={<ReportsView />} />
        </Routes>
        <Where />
      </MemoryRouter>
    </AppStateProvider>,
  );
}

beforeEach(() => {
  setOfflineFallback(false);
  clearAuthFailure();
  window.localStorage.clear();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.stubGlobal('fetch', vi.fn(api));
  RUNS = [
    run('run-a', 'done'),
    run('run-b', 'done'),
    run('run-macro', 'done', 'macro'),
    run('run-going', 'running'),
    run('run-failed', 'failed'),
  ];
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

/* ------------------------------ Run detail -------------------------------- */

describe('reportBlockReason', () => {
  it('lets only a finished micro run through, and puts macro first', () => {
    expect(reportBlockReason({ status: 'done', tier: 'micro' })).toBeNull();
    expect(reportBlockReason({ status: 'running', tier: 'macro' })).toMatch(/macro \(screening\)/);
    expect(reportBlockReason({ status: 'running', tier: 'micro' })).toBe(
      'Available once the run is done (it is running).',
    );
    expect(reportBlockReason({ status: 'failed', tier: 'micro' })).toMatch(/failed/);
  });
});

describe('Run detail: Report on this run', () => {
  it('links a finished micro run to Reports with the run preselected', async () => {
    renderAt('/runs/run-a');
    const link = await screen.findByRole('link', { name: 'Report on this run' }, { timeout: 4000 });
    expect(link).toHaveAttribute('href', '/reports?select=run-a');
    fireEvent.click(link);
    expect(screen.getByTestId('where')).toHaveTextContent('/reports');
    const picker = await screen.findByRole('table', { name: 'finished runs' });
    const box = await within(picker).findByLabelText('select run-a', {}, { timeout: 4000 });
    await waitFor(() => expect(box).toBeChecked());
    expect(within(picker).getByLabelText('select run-b')).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Generate report (1)' })).toBeEnabled();
  }, 15000);

  it('keeps the button on a macro run, disabled, and says a report needs micro runs', async () => {
    renderAt('/runs/run-macro');
    const button = await screen.findByRole('button', { name: 'Report on this run' }, { timeout: 4000 });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(
      'Reports need finished micro runs: the API refuses a validation report from macro (screening) runs.',
    );
    expect(screen.getByText(/Reports need finished micro runs/)).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Report on this run' })).toBeNull();
    expect(formatViolations(auditA11y(document.body))).toEqual([]);
  });

  it('waits for a running run, and says why', async () => {
    renderAt('/runs/run-going');
    const button = await screen.findByRole('button', { name: 'Report on this run' }, { timeout: 4000 });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription('Available once the run is done (it is running).');
  });
});

/* ------------------------------ Reports ?select= --------------------------- */

async function picker(): Promise<HTMLElement> {
  const t = await screen.findByRole('table', { name: 'finished runs' });
  await within(t).findByLabelText('select run-a', {}, { timeout: 4000 });
  return t;
}

describe('Reports ?select=', () => {
  it('preselects the run, says so, and drops the parameter', async () => {
    renderAt('/reports?select=run-b');
    const t = await picker();
    await waitFor(() => expect(within(t).getByLabelText('select run-b')).toBeChecked());
    expect(within(t).getByLabelText('select run-a')).not.toBeChecked();
    const note = screen.getByRole('status');
    expect(note).toHaveTextContent('run-b is selected. Choose what to score it against, then generate the report.');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/reports$/));
    expect(formatViolations(auditA11y(document.body))).toEqual([]);
    // the note dismisses
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText(/is selected\./)).toBeNull();
    expect(within(t).getByLabelText('select run-b')).toBeChecked();
  });

  it('explains a macro run instead of selecting it', async () => {
    renderAt('/reports?select=run-macro');
    const t = await picker();
    const note = await screen.findByText(/is a macro \(screening\) run/);
    expect(note.closest('.callout')).toHaveClass('callout-warning');
    expect(within(t).getByLabelText('select run-macro')).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Generate report (0)' })).toBeDisabled();
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/reports$/));
  });

  it('explains a failed run', async () => {
    renderAt('/reports?select=run-failed');
    await picker();
    expect(await screen.findByText(/failed, so it has no results to report/)).toBeInTheDocument();
  });

  it('explains a run the server does not list', async () => {
    renderAt('/reports?select=run-nowhere');
    await picker();
    expect(await screen.findByText(/is not in this server's runs list/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/reports$/));
  });

  it('waits for a run still computing, then selects it when it is done', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderAt('/reports?select=run-going');
    await picker();
    expect(await screen.findByText(/is running\. It will be selected here once it is done\./)).toBeInTheDocument();
    // still waiting: the parameter stays
    expect(screen.getByTestId('where')).toHaveTextContent('/reports?select=run-going');

    RUNS = RUNS.map((r) => (r.run_id === 'run-going' ? run('run-going', 'done') : r));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5500);
    });
    const t = screen.getByRole('table', { name: 'finished runs' });
    await waitFor(() => expect(within(t).getByLabelText('select run-going')).toBeChecked());
    expect(screen.getByText(/run-going/, { selector: '.callout-body .mono' })).toBeInTheDocument();
    expect(screen.getByText(/is selected\./)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/reports$/));
  }, 15000);

  it('keeps waiting through a demo answer while the API is down, then selects the run when done', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderAt('/reports?select=run-going');
    await picker();
    expect(await screen.findByText(/is running\. It will be selected here once it is done\./)).toBeInTheDocument();

    // one failed health probe: the next runs poll is answered by the demo
    // backend, which knows nothing of this server's run-going
    act(() => setOfflineFallback(true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5500);
    });
    // the demo list has landed (the server's runs are gone from the picker)...
    const demoPicker = screen.getByRole('table', { name: 'finished runs' });
    await waitFor(() => expect(within(demoPicker).queryByLabelText('select run-a')).toBeNull());
    // ...and the request stays open, saying what it now waits for
    expect(screen.getByTestId('where')).toHaveTextContent('/reports?select=run-going');
    expect(screen.queryByText(/is not in/)).toBeNull();
    const note = screen.getByText(/was running at the server's last answer\. The API is unreachable now/);
    expect(note).toHaveTextContent(/it will be checked again, and selected once done, when the server answers\./);

    // the server is back, and the run has finished meanwhile
    RUNS = RUNS.map((r) => (r.run_id === 'run-going' ? run('run-going', 'done') : r));
    act(() => setOfflineFallback(false));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5500);
    });
    const t = screen.getByRole('table', { name: 'finished runs' });
    await waitFor(() => expect(within(t).getByLabelText('select run-going')).toBeChecked());
    expect(screen.getByText(/is selected\./)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/reports$/));
  }, 15000);

  it('decides nothing on demo data when opened offline, and keeps a server verdict once the API drops', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setOfflineFallback(true);
    renderAt('/reports?select=run-nowhere');
    await waitFor(() => expect(document.querySelector('.reports-preselect')).not.toBeNull(), { timeout: 4000 });
    expect(screen.getByTestId('where')).toHaveTextContent('/reports?select=run-nowhere');
    expect(
      screen.getByText(/cannot be looked up while the API is unreachable: the runs shown are demo data/),
    ).toBeInTheDocument();
    expect(formatViolations(auditA11y(document.body))).toEqual([]);

    // the server answers: now the run's absence is a verdict
    act(() => setOfflineFallback(false));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5500);
    });
    expect(await screen.findByText(/is not in this server's runs list/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/reports$/));

    // and the verdict keeps its source when the list on screen turns demo
    act(() => setOfflineFallback(true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5500);
    });
    const verdict = screen.getByText(/is not in this server's runs list/);
    expect(verdict.closest('.callout')).not.toHaveTextContent(/demo/);
  }, 15000);

  it('stops waiting for the server when the note is dismissed', async () => {
    setOfflineFallback(true);
    renderAt('/reports?select=run-going');
    await screen.findByText(/cannot be looked up while the API is unreachable/, {}, { timeout: 4000 });
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText(/cannot be looked up/)).toBeNull();
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/reports$/));
  });

  it('selects nothing without the parameter, and works outside a router', async () => {
    render(<ReportsView />);
    const t = await picker();
    expect(within(t).getByLabelText('select run-a')).not.toBeChecked();
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull();
  });
});
