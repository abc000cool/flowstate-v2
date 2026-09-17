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

import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmDialog } from '../components/ConfirmDialog';

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
