/** The shell's connection reporting. `/healthz` needs no key, so a rejected
 * key leaves the health probe green while every authenticated call 401s — the
 * rail must not claim a live API link, and the banner must say what to fix. */

import { act, render, screen, waitFor } from '@testing-library/react';
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
  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
        const url = String(input);
        if (url.endsWith('/healthz')) {
          // auth-exempt: healthy whatever the key is
          return new Response(JSON.stringify({ status: 'ok' }), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          });
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
});
