/** The shared primitives of docs/design/DASHBOARD_DESIGN.md §9 (Callout,
 * EmptyState, Skeleton, CopyButton / HashValue, ErrorCallout), the dialog's
 * focus trap and focus restore (§9.15), the status pill and progress bar
 * semantics (§9.7), and the Onboard stage stepper's refusal to invent
 * progress (§10.2). */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import { ProgressBar, StatusChip } from '../components/bits';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Callout } from '../components/ui/Callout';
import {
  COPIED_MS,
  CopyButton,
  HashValue,
  isPlaceholderHash,
  truncateMiddle,
} from '../components/ui/CopyButton';
import { EmptyState } from '../components/ui/EmptyState';
import { describeError, ErrorCallout, isShellError } from '../components/ui/ErrorCallout';
import { Skeleton, SkeletonRows } from '../components/ui/Skeleton';
import { stageStates } from '../views/OnboardView';

describe('Callout', () => {
  it('renders tone, title, body and action, and never role=alert', () => {
    const { container } = render(
      <Callout tone="danger" title="Could not load." role="status" action={<button>Retry</button>}>
        HTTP 500 — boom
      </Callout>,
    );
    const box = container.querySelector('.callout');
    expect(box).toHaveClass('callout-danger');
    expect(box).toHaveAttribute('role', 'status');
    expect(screen.getByText('Could not load.')).toBeInTheDocument();
    expect(screen.getByText('HTTP 500 — boom')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
    // the icon is decorative
    expect(box?.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
  });
});

describe('EmptyState', () => {
  it('states what is missing, when it appears, and the echoing action', () => {
    const { container } = render(
      <EmptyState
        title="No runs yet."
        description="Runs appear here once launched."
        action={<button>Launch run</button>}
      />,
    );
    expect(screen.getByText('No runs yet.')).toBeInTheDocument();
    expect(screen.getByText('Runs appear here once launched.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Launch run' })).toBeInTheDocument();
    expect(container.querySelector('.empty-state')).not.toHaveClass('compact');
  });

  it('has a compact variant for table cells', () => {
    const { container } = render(<EmptyState compact title="No reports yet." />);
    expect(container.querySelector('.empty-state')).toHaveClass('compact');
  });
});

describe('Skeleton', () => {
  it('is hidden from assistive tech and takes its size', () => {
    const { container } = render(<Skeleton width={120} height="1rem" radius="md" />);
    const block = container.querySelector('.skeleton') as HTMLElement;
    expect(block).toHaveAttribute('aria-hidden', 'true');
    expect(block).toHaveClass('skeleton-r-md');
    expect(block.style.width).toBe('120px');
    expect(block.style.height).toBe('1rem');
  });

  it('renders rows × columns placeholder cells', () => {
    const { container } = render(
      <table>
        <tbody>
          <SkeletonRows rows={5} columns={3} />
        </tbody>
      </table>,
    );
    expect(container.querySelectorAll('tr')).toHaveLength(5);
    expect(container.querySelectorAll('td')).toHaveLength(15);
    for (const tr of container.querySelectorAll('tr')) {
      expect(tr).toHaveAttribute('aria-hidden', 'true');
    }
  });
});

describe('CopyButton and HashValue', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('truncates in the middle, first 4 and last 4', () => {
    expect(truncateMiddle('3f9a8b7c6d5e4c21e')).toBe('3f9a…c21e');
    expect(truncateMiddle('abc123')).toBe('abc123');
    expect(isPlaceholderHash('— demo, no server hash —')).toBe(true);
    expect(isPlaceholderHash('a1b2c3d4e5f6')).toBe(false);
  });

  it('copies the full value, says so, and swaps back after 1.2 s', async () => {
    vi.useFakeTimers();
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    render(<CopyButton value="a1b2c3d4e5f6a7b8" label="config hash" />);
    const button = screen.getByRole('button', { name: 'Copy config hash' });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(writeText).toHaveBeenCalledWith('a1b2c3d4e5f6a7b8');
    expect(screen.getByText('Copied')).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(COPIED_MS + 10);
    });
    expect(screen.queryByText('Copied')).toBeNull();
  });

  it('shows a hash short, keeps the full value readable, and never copies a placeholder', () => {
    const { rerender } = render(<HashValue value="a1b2c3d4e5f6a7b8" />);
    expect(screen.getByText('a1b2…a7b8')).toHaveAttribute('title', 'a1b2c3d4e5f6a7b8');
    // assistive tech and text search get the whole hash
    expect(screen.getByText('a1b2c3d4e5f6a7b8')).toHaveClass('visually-hidden');
    expect(screen.getByRole('button', { name: 'Copy config hash' })).toBeInTheDocument();

    rerender(<HashValue value="— demo, no server hash —" />);
    expect(screen.getByText('— demo, no server hash —')).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });
});

describe('ErrorCallout', () => {
  it("prefixes the HTTP status to the server's words and offers Retry", () => {
    const onRetry = vi.fn();
    render(
      <ErrorCallout
        error={new ApiError(422, 'sim.duration_s: must be > 0')}
        title="Could not load."
        onRetry={onRetry}
      />,
    );
    expect(screen.getByText('HTTP 422 — sim.duration_s: must be > 0')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('leaves connectivity and auth to the shell', () => {
    expect(isShellError(new TypeError('Failed to fetch'))).toBe(true);
    expect(isShellError(new ApiError(401, 'nope'))).toBe(true);
    expect(isShellError(new ApiError(500, 'boom'))).toBe(false);
    expect(describeError(new Error('plain'))).toBe('plain');
  });
});

describe('StatusChip and ProgressBar', () => {
  it('keeps the verbatim API word as the pill text', () => {
    render(<StatusChip status="done" />);
    expect(screen.getByText('done')).toHaveClass('chip', 'done');
  });

  it('exposes progress to assistive tech', () => {
    render(<ProgressBar done={7} total={20} status="running" />);
    const bar = screen.getByRole('progressbar', { name: 'Replicates complete' });
    expect(bar).toHaveAttribute('aria-valuenow', '7');
    expect(bar).toHaveAttribute('aria-valuemax', '20');
    expect(bar).toHaveClass('progress');
    expect(screen.getByText('7/20')).toBeInTheDocument();
  });
});

describe('ConfirmDialog focus management', () => {
  function Opener(): JSX.Element {
    const [open, setOpen] = useState(false);
    return (
      <div className="app">
        <button onClick={() => setOpen(true)}>Open launcher</button>
        {open && (
          <ConfirmDialog
            title="Launch ring"
            confirmLabel="Launch run"
            onConfirm={() => setOpen(false)}
            onCancel={() => setOpen(false)}
          >
            <label htmlFor="f">Seed</label>
            <input id="f" />
          </ConfirmDialog>
        )}
      </div>
    );
  }

  it('traps Tab inside, makes the app inert, and gives focus back on close', () => {
    render(<Opener />);
    const opener = screen.getByRole('button', { name: 'Open launcher' });
    opener.focus();
    fireEvent.click(opener);

    const dialog = screen.getByRole('dialog', { name: 'Launch ring' });
    const confirm = screen.getByRole('button', { name: 'Launch run' });
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    expect(document.activeElement).toBe(confirm);
    expect(document.querySelector('.app')).toHaveAttribute('inert');
    // portalled outside the inert tree
    expect(document.querySelector('.app')?.contains(dialog)).toBe(false);

    // Tab from the last control wraps to the first; Shift+Tab wraps back
    fireEvent.keyDown(confirm, { key: 'Tab' });
    expect(document.activeElement).toBe(screen.getByLabelText('Seed'));
    fireEvent.keyDown(screen.getByLabelText('Seed'), { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(confirm);
    // a Tab between two inner controls is left to the browser
    cancel.focus();
    fireEvent.keyDown(cancel, { key: 'Tab' });
    expect(document.activeElement).toBe(cancel);

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.querySelector('.app')).not.toHaveAttribute('inert');
    expect(document.activeElement).toBe(opener);
  });
});

describe('Onboard stage stepper', () => {
  it('draws only what the server reported', () => {
    expect(
      stageStates('running', { stage: 'network', completed_stages: 1, total_stages: 5 }),
    ).toEqual(['done', 'current', 'pending', 'pending', 'pending']);
    expect(stageStates('failed', { stage: 'network', completed_stages: 1, total_stages: 5 })).toEqual(
      ['done', 'failed', 'pending', 'pending', 'pending'],
    );
    expect(stageStates('done', { stage: 'done', completed_stages: 5, total_stages: 5 })).toEqual([
      'done',
      'done',
      'done',
      'done',
      'done',
    ]);
    expect(stageStates('queued', { stage: null, completed_stages: 0, total_stages: 5 })).toEqual([
      'pending',
      'pending',
      'pending',
      'pending',
      'pending',
    ]);
    // a stage outside the documented list: text only, no stepper
    expect(
      stageStates('running', { stage: 'calibrate', completed_stages: 2, total_stages: 6 }),
    ).toBeNull();
  });
});
