/**
 * The error state of a panel whose data failed to load
 * (docs/design/DASHBOARD_DESIGN.md §9.10): a persistent danger Callout with
 * the server's own words, an `HTTP {status} — ` prefix when there is one, and
 * a Retry action. The toast stays as the transient echo; a failure must never
 * leave a "loading…" placeholder.
 *
 * Connectivity and auth failures are shell banners; views do not repeat them
 * (`isShellError` tells the two apart).
 */
import type { ReactNode } from 'react';
import { ApiError } from '../../api/client';
import { Icon } from '../icons';
import { Callout } from './Callout';

/** `HTTP 422 — sim.duration_s: …` for an API refusal; the message otherwise. */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) return `HTTP ${err.status} — ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

/** True for failures the shell already reports (network down, key rejected). */
export function isShellError(err: unknown): boolean {
  if (err instanceof ApiError) return err.status === 401 || err.status === 403;
  // fetch() rejects with a TypeError when the network is unreachable
  return err instanceof TypeError;
}

export function ErrorCallout(props: {
  error: unknown;
  /** What failed, e.g. "Could not load the scenario library." */
  title?: ReactNode;
  onRetry?: () => void;
}): JSX.Element {
  const { error, title, onRetry } = props;
  return (
    <Callout
      tone="danger"
      title={title}
      role="status"
      action={
        onRetry ? (
          <button type="button" className="btn sm" onClick={onRetry}>
            <Icon name="refresh-cw" size={14} />
            Retry
          </button>
        ) : undefined
      }
    >
      <span className="mono">{describeError(error)}</span>
    </Callout>
  );
}
