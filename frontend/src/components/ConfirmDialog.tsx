/** Modal confirmation for actions that commit real compute.
 *
 * A sweep grid or a full-length replicate set costs hours of simulation; the
 * dashboard must show the size of what is about to be enqueued (cells,
 * replicates, total simulated minutes) and take an explicit second click. */

import { useEffect, useRef, type ReactNode } from 'react';

export interface ConfirmDialogProps {
  title: string;
  /** Label → value lines, rendered as a mono cost table. */
  facts?: [string, string][];
  children?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
}

export function ConfirmDialog({
  title,
  facts = [],
  children,
  confirmLabel,
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
  busy = false,
}: ConfirmDialogProps): JSX.Element {
  const confirmRef = useRef<HTMLButtonElement>(null);
  // The latest onCancel, read through a ref so neither effect below has to
  // depend on it. Callers pass an inline arrow (`onCancel={() => setX(null)}`),
  // a new closure on every parent render, so an effect that depends on it
  // re-runs on every keystroke typed into a field inside the dialog.
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;

  // Focus the confirm button once, when the dialog mounts. Re-focusing on every
  // parent render stole focus back from the field the user was typing in (and
  // from the cancel button, letting an Enter keypress confirm a launch the user
  // was about to dismiss), so this effect must stay mount-only.
  useEffect(() => {
    confirmRef.current?.focus();
  }, []);

  // One listener for the life of the dialog: it calls whatever onCancel the
  // current render passed, so Escape after a re-render dismisses with the
  // newest handler without the listener being torn down and re-added.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') cancelRef.current();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <>
      <div className="drawer-scrim" onClick={onCancel} />
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        {facts.length > 0 && (
          <dl className="fact-list">
            {facts.map(([k, v]) => (
              <div key={k} className="fact">
                <dt>{k}</dt>
                <dd className="mono">{v}</dd>
              </div>
            ))}
          </dl>
        )}
        {children}
        <div className="row" style={{ marginTop: 'auto' }}>
          <button className="btn primary" ref={confirmRef} disabled={busy} onClick={onConfirm}>
            {confirmLabel}
          </button>
          <button className="btn" onClick={onCancel}>
            {cancelLabel}
          </button>
        </div>
      </div>
    </>
  );
}
