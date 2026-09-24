/** The shell's connection reporting. `/health` needs no key, so a rejected
 * key leaves the health probe green while every authenticated call 401s — the
 * rail must not claim a live API link, and the banner must say what to fix.
 *
 * The latch also needs a way out: one transient 401 (a restarting API, a key
 * rotated server-side) must not need a page reload, so the banner offers a
 * Retry and the client schedules one automatic attempt. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, listRuns, setOfflineFallback } from '../api/client';
import { AppStateProvider } from '../components/AppContext';
import { Layout } from '../components/Layout';

function renderShell(): void {
  render(
    <AppStateProvider>
      <MemoryRouter initialEntries={['/runs']}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/runs" element={<div>child</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AppStateProvider>,
  );
}

describe('Layout connection status', () => {
  let keyRejected = true;

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    keyRejected = true;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        if (url.endsWith('/health')) {
          // auth-exempt: healthy whatever the key is
          return new Response(JSON.stringify({ status: 'ok' }), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          });
        }
        if (!keyRejected) {
          return new Response('[]', { status: 200, headers: { 'content-type': 'application/json' } });
        }
        return new Response(JSON.stringify({ detail: 'invalid or missing X-API-Key' }), {
          status: 401,
          headers: { 'content-type': 'application/json' },
        });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthFailure();
  });

  it('reports a rejected key instead of a green API link', async () => {
    renderShell();
    expect(await screen.findByText('API LINK', {}, { timeout: 4000 })).toBeInTheDocument();

    // one authenticated call is enough to learn the key is wrong
    await act(async () => {
      await listRuns().catch(() => undefined);
    });

    await waitFor(() => {
      expect(screen.getByText('KEY REJECTED')).toBeInTheDocument();
    });
    expect(screen.queryByText('API LINK')).toBeNull();
    expect(screen.getByRole('alert')).toHaveTextContent('API key rejected');
    expect(screen.getByRole('button', { name: 'Open Settings' })).toBeInTheDocument();
  }, 10000);

  it('offers a Retry that clears the latch without a reload', async () => {
    renderShell();
    await act(async () => {
      await listRuns().catch(() => undefined);
    });
    const banner = await screen.findByRole('alert');
    // the latch is not a dead end: it says an automatic retry is coming
    expect(banner).toHaveTextContent(/Retrying once in \d+ s/);

    // the API comes back (or the key is re-issued) before that fires
    keyRejected = false;
    fireEvent.click(within(banner).getByRole('button', { name: 'Retry now' }));
    await waitFor(() => {
      expect(screen.queryByRole('alert')).toBeNull();
    });
    expect(screen.queryByText('KEY REJECTED')).toBeNull();
    // and the resumed call really goes through
    await act(async () => {
      await listRuns();
    });
    await waitFor(() => {
      expect(screen.getByText('API LINK')).toBeInTheDocument();
    });
  }, 10000);
});
