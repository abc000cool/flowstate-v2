/** Keyboard focus after a busy action (docs/design/DASHBOARD_DESIGN.md §9.1
 * "Busy", §11.2). A button disabled while its action runs loses focus to
 * <body> in a browser; jsdom keeps focus on it, so these tests drop focus the
 * way a browser does (`dropFocus`). Once the action ends focus goes back to
 * the button or on to
 * the next logical element — never away from wherever the user moved it, and
 * never for an action that did not start from the focused button — and an
 * action that navigates away lands focus on the new page's content. */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useRef, useState } from 'react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, setOfflineFallback } from '../api/client';
import { AppStateProvider } from '../components/AppContext';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Layout } from '../components/Layout';
import { FOCUS_CONTENT_STATE, useBusyFocus } from '../lib/hooks';
import { dropFocus } from './focus';

/** Resolves the harness's pending action: true succeeds, false refuses. */
let settle: (ok: boolean) => void = () => undefined;

/** A button whose action takes a while and then succeeds or is refused, as
 * the views' write buttons do. */
function Harness(): JSX.Element {
  const [busy, setBusy] = useState(false);
  const busyFocus = useBusyFocus(busy);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const nextRef = useRef<HTMLHeadingElement>(null);
  const run = async (): Promise<void> => {
    busyFocus.begin(buttonRef.current);
    setBusy(true);
    const ok = await new Promise<boolean>((resolve) => {
      settle = resolve;
    });
    busyFocus.after(() => (ok ? nextRef.current : buttonRef.current));
    setBusy(false);
  };
  return (
    <>
      <h2 tabIndex={-1} ref={nextRef}>
        Progress
      </h2>
      <input aria-label="Elsewhere" />
      <button type="button" ref={buttonRef} disabled={busy} onClick={() => void run()}>
        Go
      </button>
    </>
  );
}

/** Press the button from the keyboard: focus it, click it, and lose focus the
 * way a browser does when the button disables. */
function pressFromKeyboard(button: HTMLElement): void {
  button.focus();
  fireEvent.click(button);
  expect(button).toBeDisabled();
  dropFocus();
}

describe('useBusyFocus', () => {
  it('gives focus back to the button after a refusal', async () => {
    render(<Harness />);
    const button = screen.getByRole('button', { name: 'Go' });
    pressFromKeyboard(button);
    await act(async () => settle(false));
    await waitFor(() => expect(button).toHaveFocus());
  });

  it('hands focus to the logical next element after a success', async () => {
    render(<Harness />);
    pressFromKeyboard(screen.getByRole('button', { name: 'Go' }));
    await act(async () => settle(true));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Progress' })).toHaveFocus());
  });

  it('does not move focus for an action that did not start from the focused button', async () => {
    render(<Harness />);
    // a pointer click in a browser that does not focus buttons
    fireEvent.click(screen.getByRole('button', { name: 'Go' }));
    await act(async () => settle(true));
    expect(document.activeElement).toBe(document.body);
  });

  it('never takes focus back from where the user moved it meanwhile', async () => {
    render(<Harness />);
    pressFromKeyboard(screen.getByRole('button', { name: 'Go' }));
    const elsewhere = screen.getByLabelText('Elsewhere');
    elsewhere.focus();
    await act(async () => settle(false));
    expect(elsewhere).toHaveFocus();
  });
});

/** The confirm button is disabled while busy; a refused launch leaves the
 * dialog open, and focus must come back to it rather than stay on <body>
 * behind an inert app. */
describe('ConfirmDialog after busy', () => {
  function Dialog({ busy, withField }: { busy: boolean; withField?: boolean }): JSX.Element {
    return (
      <ConfirmDialog
        title="Launch this run?"
        confirmLabel="Launch"
        busy={busy}
        onConfirm={() => undefined}
        onCancel={() => undefined}
      >
        {withField && <input aria-label="Seed" className="input" />}
      </ConfirmDialog>
    );
  }

  it('refocuses the confirm button when busy clears with focus lost', () => {
    const { rerender } = render(<Dialog busy={false} />);
    const confirm = screen.getByRole('button', { name: 'Launch' });
    expect(confirm).toHaveFocus();
    rerender(<Dialog busy />);
    dropFocus();
    rerender(<Dialog busy={false} />);
    expect(confirm).toHaveFocus();
  });

  it('leaves a field the user is in alone when busy clears', () => {
    const { rerender } = render(<Dialog busy withField />);
    const field = screen.getByLabelText('Seed');
    field.focus();
    rerender(<Dialog busy={false} withField />);
    expect(field).toHaveFocus();
  });
});

/** An action that navigates away (a launch landing on Runs) passes
 * FOCUS_CONTENT_STATE; the shell focuses the new page's content. */
describe('Layout content focus after an action navigates', () => {
  function Leave({ withState }: { withState: boolean }): JSX.Element {
    const navigate = useNavigate();
    return (
      <button
        type="button"
        onClick={() => navigate('/runs', withState ? { state: FOCUS_CONTENT_STATE } : undefined)}
      >
        Leave
      </button>
    );
  }

  function renderShell(withState: boolean): void {
    render(
      <AppStateProvider>
        <MemoryRouter initialEntries={['/scenarios']}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/scenarios" element={<Leave withState={withState} />} />
              <Route path="/runs" element={<div>runs page</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AppStateProvider>,
    );
  }

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (): Promise<Response> => {
        return new Response(JSON.stringify({ status: 'ok' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.title = 'FlowState';
  });

  it('focuses the content of the page the action landed on', async () => {
    renderShell(true);
    fireEvent.click(screen.getByRole('button', { name: 'Leave' }));
    expect(screen.getByText('runs page')).toBeInTheDocument();
    expect(document.activeElement).toBe(document.getElementById('content'));
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('leaves an ordinary navigation alone', async () => {
    renderShell(false);
    fireEvent.click(screen.getByRole('button', { name: 'Leave' }));
    expect(screen.getByText('runs page')).toBeInTheDocument();
    expect(document.activeElement).toBe(document.body);
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });
});
