/** The app shell of docs/design/DASHBOARD_DESIGN.md §6 and §11.3: grouped
 * navigation, the breadcrumb, the theme toggle, Settings → Appearance, the
 * mobile navigation drawer, the skip link, and the PageHeader contract.
 * (Connection reporting is covered by layout.test.tsx.) */

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, setOfflineFallback } from '../api/client';
import { AppStateProvider, useAppState } from '../components/AppContext';
import { Layout } from '../components/Layout';
import { PageHeader } from '../components/PageHeader';
import { THEME_STORAGE_KEY } from '../lib/theme';

function SetCorridor({ name }: { name: string }): JSX.Element {
  const { setCorridor } = useAppState();
  return (
    <button type="button" onClick={() => setCorridor(name)}>
      set corridor
    </button>
  );
}

function renderShell(path = '/runs'): void {
  render(
    <AppStateProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/runs" element={<SetCorridor name="i24_replica" />} />
            <Route path="/runs/:runId" element={<div>detail</div>} />
            <Route path="/reports" element={<div>reports</div>} />
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
  delete document.documentElement.dataset.theme;
  window.localStorage.clear();
  document.title = 'FlowState';
});

describe('navigation', () => {
  it('groups the sections as Set up, Simulate and Report, with First run in the footer', async () => {
    renderShell();
    await screen.findByText('API LINK', {}, { timeout: 4000 });
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    const group = (name: string): HTMLElement => within(nav).getByRole('group', { name });
    expect(within(group('Set up')).getAllByRole('link').map((a) => a.textContent)).toEqual([
      'Onboard corridor',
      'Scenarios',
    ]);
    expect(within(group('Simulate')).getAllByRole('link').map((a) => a.textContent)).toEqual([
      'Runs',
      'Sweeps',
      'Compare',
    ]);
    expect(within(group('Report')).getAllByRole('link').map((a) => a.textContent)).toEqual([
      'Reports',
    ]);
    expect(within(nav).getByRole('link', { name: 'Runs' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'First run' })).toHaveAttribute('href', '/first-run');
  });

  it('has no ticking clock', async () => {
    renderShell();
    await screen.findByText('API LINK', {}, { timeout: 4000 });
    expect(screen.queryByText(/\d\d:\d\d:\d\d UTC/)).toBeNull();
  });

  it('shows the section in the breadcrumb, and the run id on run detail', async () => {
    renderShell('/runs/run-a41d09');
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' });
    expect(within(crumbs).getByRole('link', { name: 'Runs' })).toHaveAttribute('href', '/runs');
    expect(within(crumbs).getByText('run-a41d09')).toHaveAttribute('aria-current', 'page');
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('names the active corridor in the top bar', async () => {
    renderShell();
    expect(screen.getByText('No active corridor')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'set corridor' }));
    expect(screen.getByText('i24_replica')).toBeInTheDocument();
    expect(screen.queryByText('No active corridor')).toBeNull();
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('puts a skip link first that moves focus to the content', async () => {
    renderShell();
    const skip = screen.getByRole('link', { name: 'Skip to content' });
    const firstFocusable = document.querySelector('a[href], button, input, select');
    expect(firstFocusable).toBe(skip);
    fireEvent.click(skip);
    expect(document.activeElement).toBe(document.getElementById('content'));
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });
});

describe('theme controls', () => {
  it('cycles System, Light, Dark from the top bar and remembers the choice', async () => {
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: 'Theme: System' }));
    expect(document.documentElement.dataset.theme).toBe('light');
    fireEvent.click(screen.getByRole('button', { name: 'Theme: Light' }));
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    fireEvent.click(screen.getByRole('button', { name: 'Theme: Dark' }));
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    expect(screen.getByRole('button', { name: 'Theme: System' })).toBeInTheDocument();
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('applies Settings → Appearance at once, without Save', async () => {
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const drawer = screen.getByRole('dialog', { name: 'Settings' });
    const group = within(drawer).getByRole('radiogroup', { name: 'Theme' });
    expect(within(group).getByRole('radio', { name: 'System' })).toBeChecked();
    fireEvent.click(within(group).getByRole('radio', { name: 'Dark' }));
    expect(document.documentElement.dataset.theme).toBe('dark');
    // the top bar toggle reads the same preference
    expect(screen.getByRole('button', { name: 'Theme: Dark', hidden: true })).toBeInTheDocument();
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });
});

describe('settings drawer', () => {
  it('takes focus, makes the app inert, closes on Escape and returns focus', async () => {
    renderShell();
    const opener = screen.getByRole('button', { name: 'Settings' });
    opener.focus();
    fireEvent.click(opener);
    const drawer = screen.getByRole('dialog', { name: 'Settings' });
    expect(drawer.contains(document.activeElement)).toBe(true);
    expect(document.querySelector('.app')).toHaveAttribute('inert');

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Settings' })).toBeNull();
    expect(document.querySelector('.app')).not.toHaveAttribute('inert');
    expect(document.activeElement).toBe(opener);
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });

  it('keeps Tab inside the drawer', async () => {
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const drawer = screen.getByRole('dialog', { name: 'Settings' });
    const save = within(drawer).getByRole('button', { name: 'Save' });
    const close = within(drawer).getByRole('button', { name: 'Close settings' });
    save.focus();
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(document.activeElement).toBe(close);
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(save);
    fireEvent.click(close);
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });
});

describe('mobile navigation drawer', () => {
  it('opens from the menu button as a dialog, and Escape returns focus to it', async () => {
    renderShell();
    const menu = screen.getByRole('button', { name: 'Open navigation' });
    expect(menu).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(menu);
    const drawer = screen.getByRole('dialog', { name: 'Navigation' });
    expect(menu).toHaveAttribute('aria-expanded', 'true');
    expect(drawer.contains(document.activeElement)).toBe(true);
    expect(document.querySelector('.main')).toHaveAttribute('inert');

    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(screen.queryByRole('dialog', { name: 'Navigation' })).toBeNull();
    expect(document.querySelector('.main')).not.toHaveAttribute('inert');
    expect(document.activeElement).toBe(menu);
    await screen.findByText('API LINK', {}, { timeout: 4000 });
  });
});

describe('PageHeader', () => {
  it('renders the title as the h1 with meta, description and actions', () => {
    render(
      <PageHeader
        title="Runs"
        meta={<span className="mono">38 runs</span>}
        description="Launch a scenario and follow its replicates."
        actions={<button type="button">Upload YAML</button>}
      />,
    );
    expect(screen.getByRole('heading', { level: 1, name: 'Runs' })).toBeInTheDocument();
    expect(screen.getByText('38 runs')).toBeInTheDocument();
    expect(screen.getByText('Launch a scenario and follow its replicates.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Upload YAML' })).toBeInTheDocument();
  });

  it('names the browser tab while mounted', () => {
    const { unmount } = render(<PageHeader title="Reports" documentTitle="Reports" />);
    expect(document.title).toBe('Reports · FlowState');
    unmount();
    expect(document.title).toBe('FlowState');
  });
});
