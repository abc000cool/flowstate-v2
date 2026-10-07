/** ConfirmDialog stands in front of every action that commits real compute,
 * and it holds two properties that a naive effect list breaks.
 *
 * (1) Focus is taken once, at mount. An effect that re-runs on every parent
 * render (its dependency is an inline `onCancel`, a new closure per render)
 * re-focuses the confirm button while the user is typing into a field inside
 * the dialog — the caret jumps away, and an Enter keypress then confirms a
 * launch instead of ending the edit.
 *
 * (2) Escape dismisses with the handler of the *current* render. The listener
 * is registered once and reads `onCancel` through a ref, so it neither churns
 * on every keystroke nor captures a stale closure from mount. */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { toast, Toasts } from '../components/toast';
import { dropFocus } from './focus';

/** A caller shaped like the real ones: a controlled field inside the dialog,
 * so every keystroke re-renders the parent, and an inline `onCancel` that
 * closes over the newest form state. */
function Host({ onCancel }: { onCancel: (seenValue: string) => void }): JSX.Element {
  const [reps, setReps] = useState('');
  return (
    <ConfirmDialog
      title="Launch this run?"
      confirmLabel="Launch"
      onConfirm={() => undefined}
      onCancel={() => onCancel(reps)}
    >
      <label htmlFor="reps">Replicates</label>
      <input id="reps" className="input" value={reps} onChange={(e) => setReps(e.target.value)} />
    </ConfirmDialog>
  );
}

describe('ConfirmDialog', () => {
  it('focuses the confirm button on mount', () => {
    render(<Host onCancel={() => undefined} />);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Launch' }));
  });

  it('does not steal focus back from a field the user is typing in', () => {
    render(<Host onCancel={() => undefined} />);
    const field = screen.getByLabelText('Replicates');
    field.focus();
    expect(document.activeElement).toBe(field);

    // one keystroke: the parent re-renders and hands the dialog a fresh
    // onCancel closure. Focus must stay where the user put it.
    fireEvent.change(field, { target: { value: '5' } });
    expect(document.activeElement).toBe(field);
    expect(field).toHaveValue('5');

    fireEvent.change(field, { target: { value: '50' } });
    expect(document.activeElement).toBe(field);
  });

  it('Escape after a re-render calls the newest onCancel, not the one from mount', () => {
    const onCancel = vi.fn();
    render(<Host onCancel={onCancel} />);
    fireEvent.change(screen.getByLabelText('Replicates'), { target: { value: '7' } });

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
    // a listener captured at mount would report the empty initial state
    expect(onCancel).toHaveBeenCalledWith('7');
  });

  it('stops listening for Escape once the dialog is gone', () => {
    const onCancel = vi.fn();
    const { unmount } = render(<Host onCancel={onCancel} />);
    unmount();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).not.toHaveBeenCalled();
  });
});

/** (3) Focus stays inside the modal while it is busy. The confirm button is
 * disabled for the round trip, which drops focus to <body> in a browser, and
 * the toasts (rendered after `.app`, so not inert) hold focusable dismiss
 * buttons: a launch toasts "preset … stored" mid-request. A Tab trap that
 * listens on the dialog alone never sees a key pressed from <body> or a
 * toast, so Tab walked out of an aria-modal dialog. Focus now waits on the
 * dialog itself while the button is disabled, and Tab is trapped at the
 * document level for as long as the dialog is open. */
describe('ConfirmDialog focus while busy', () => {
  function Shell({ busy }: { busy: boolean }): JSX.Element {
    return (
      <>
        <div className="app">
          <button type="button">Launch run</button>
        </div>
        <Toasts />
        <ConfirmDialog
          title="Launch this run?"
          confirmLabel="Launch"
          busy={busy}
          onConfirm={() => undefined}
          onCancel={() => undefined}
        />
      </>
    );
  }

  it('moves focus to the dialog itself when the confirm button is disabled', () => {
    const { rerender } = render(<Shell busy={false} />);
    const confirm = screen.getByRole('button', { name: 'Launch' });
    expect(confirm).toHaveFocus();
    rerender(<Shell busy />);
    expect(confirm).toBeDisabled();
    expect(screen.getByRole('dialog', { name: 'Launch this run?' })).toHaveFocus();
    // and back to the button once the request is refused
    rerender(<Shell busy={false} />);
    expect(confirm).toHaveFocus();
  });

  it('opens on the dialog itself when it mounts already busy', () => {
    render(<Shell busy />);
    expect(screen.getByRole('dialog', { name: 'Launch this run?' })).toHaveFocus();
  });

  it('traps Tab from <body> and from a toast, outside the dialog', () => {
    render(<Shell busy />);
    act(() => toast('ok', 'preset ring_sugiyama stored as scn_1'));
    const dialog = screen.getByRole('dialog', { name: 'Launch this run?' });
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    const dismiss = screen.getByRole('button', { name: /^dismiss: preset ring_sugiyama/ });

    // focus lost to <body> (the browser's answer to a disabled button)
    dropFocus();
    expect(fireEvent.keyDown(document.body, { key: 'Tab' })).toBe(false);
    expect(cancel).toHaveFocus();

    // focus on the non-inert toast: Shift+Tab comes back in, not further out
    dismiss.focus();
    expect(fireEvent.keyDown(dismiss, { key: 'Tab', shiftKey: true })).toBe(false);
    expect(cancel).toHaveFocus();

    // from the dialog box itself, Shift+Tab would step to the toasts before
    // it in document order: it wraps to the last control instead
    dialog.focus();
    expect(fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true })).toBe(false);
    expect(cancel).toHaveFocus();

    // the toast store is module state: leave none behind for the next test
    fireEvent.click(dismiss);
  });

  it('stops trapping Tab once the dialog is gone', () => {
    const { unmount } = render(<Shell busy={false} />);
    unmount();
    dropFocus();
    expect(fireEvent.keyDown(document.body, { key: 'Tab' })).toBe(true);
  });
});
