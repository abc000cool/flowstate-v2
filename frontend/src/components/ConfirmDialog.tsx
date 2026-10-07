/** Modal confirmation for actions that commit real compute.
 *
 * A sweep grid or a full-length replicate set costs hours of simulation; the
 * dashboard must show the size of what is about to be enqueued (cells,
 * replicates, total simulated minutes) and take an explicit second click.
 *
 * Layout and behavior: docs/design/DASHBOARD_DESIGN.md §9.15. Focus is taken
 * once, at mount; Tab and Shift+Tab cycle inside the dialog; Escape cancels;
 * the opener gets focus back on close; the app behind is `inert` while open
 * (the dialog is portalled to <body> so it is not inside the inert tree).
 *
 * Only `.app` is inert: the toasts sit outside it, each with a focusable
 * dismiss button, and a launch toasts while the dialog is still open. So the
 * Tab trap listens on the document for the life of the dialog, not on the
 * dialog element (a key pressed from <body> or a toast never reaches that),
 * and the dialog box itself holds focus (tabIndex -1) whenever the confirm
 * button cannot: it is disabled while `busy`, which in a browser drops focus
 * to <body>. When `busy` clears with the dialog still open (a refused
 * launch), focus goes back to the button — never away from a field the user
 * has moved to. */

import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

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

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
  'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Keep Tab and Shift+Tab inside `box`, from wherever focus is. Acts on Tab
 * only, and only when focus would leave: from the last (first) control, from
 * outside the dialog, or from the box itself, whose Shift+Tab would step to
 * the toasts that precede the portalled dialog in document order. */
function trapTab(e: globalThis.KeyboardEvent, box: HTMLElement): void {
  const nodes = Array.from(box.querySelectorAll<HTMLElement>(FOCUSABLE));
  if (nodes.length === 0) {
    e.preventDefault();
    box.focus();
    return;
  }
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  const active = document.activeElement;
  const inside = active instanceof Node && active !== box && box.contains(active);
  if (e.shiftKey && (active === first || !inside)) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && (active === last || !inside)) {
    e.preventDefault();
    first.focus();
  }
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
  const boxRef = useRef<HTMLDivElement>(null);
  // The latest onCancel, read through a ref so neither effect below has to
  // depend on it. Callers pass an inline arrow (`onCancel={() => setX(null)}`),
  // a new closure on every parent render, so an effect that depends on it
  // re-runs on every keystroke typed into a field inside the dialog.
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;
  // Whatever had focus when the dialog first rendered (the button that opened
  // it). Read at first render, before the app is made inert, which would blur
  // it; later renders keep the first answer.
  const openerRef = useRef<{ el: HTMLElement | null } | null>(null);
  if (openerRef.current === null) {
    const active = typeof document !== 'undefined' ? document.activeElement : null;
    openerRef.current = { el: active instanceof HTMLElement ? active : null };
  }

  // The app behind the dialog is inert while it is open: no clicks, no
  // focus, and assistive tech reads only the dialog. Declared before the
  // focus effect so that, on close, inert is lifted before focus goes back to
  // the opener (cleanups run in declaration order; inert cannot take focus).
  useEffect(() => {
    const app = document.querySelector('.app');
    if (!app || app.contains(boxRef.current)) return;
    const was = app.hasAttribute('inert');
    app.setAttribute('inert', '');
    return () => {
      if (!was) app.removeAttribute('inert');
    };
  }, []);

  // Focus the confirm button once, when the dialog mounts, and give focus back
  // to whatever opened the dialog when it goes. Re-focusing on every parent
  // render stole focus back from the field the user was typing in (and from
  // the cancel button, letting an Enter keypress confirm a launch the user was
  // about to dismiss), so this effect must stay mount-only. A dialog opened
  // already busy has a disabled button: the box itself takes focus instead.
  useEffect(() => {
    const opener = openerRef.current?.el ?? null;
    const confirm = confirmRef.current;
    if (confirm && !confirm.disabled) confirm.focus();
    else boxRef.current?.focus();
    return () => {
      if (opener && opener.isConnected) opener.focus();
    };
  }, []);

  // The confirm button is disabled while busy, and a disabled button loses
  // focus to <body>, outside the dialog. When busy starts, the dialog box
  // holds focus instead; when it clears and the dialog is still open (the
  // launch was refused), focus goes back to the button that was pressed. Both
  // only from <body>, the button or the box: a field typed into while the
  // dialog was blocked keeps focus (the Scenarios launcher sets and clears
  // `busy` as a duration is typed).
  const wasBusy = useRef(busy);
  useEffect(() => {
    const was = wasBusy.current;
    wasBusy.current = busy;
    if (was === busy) return;
    const active = document.activeElement;
    const lost = active === null || active === document.body;
    if (busy) {
      if (lost || active === confirmRef.current) boxRef.current?.focus();
    } else if (lost || active === confirmRef.current || active === boxRef.current) {
      confirmRef.current?.focus();
    }
  }, [busy]);

  // One document-level listener for the life of the dialog: Escape calls
  // whatever onCancel the current render passed (so a re-render dismisses
  // with the newest handler without the listener being torn down and
  // re-added), and Tab is trapped from wherever focus is. Neither moves focus
  // on render.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent): void => {
      if (e.key === 'Escape') cancelRef.current();
      else if (e.key === 'Tab' && boxRef.current) trapTab(e, boxRef.current);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return createPortal(
    <>
      <div className="modal-scrim" onClick={onCancel} />
      <div
        ref={boxRef}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
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
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className="btn primary"
            ref={confirmRef}
            disabled={busy}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </>,
    document.body,
  );
}
