/**
 * CopyButton and hash display (docs/design/DASHBOARD_DESIGN.md §9.18).
 *
 * A 24 px ghost icon button. On click it writes `value` to the clipboard and
 * swaps to a check icon for 1.2 s, announcing "Copied" politely. The full
 * value is always what is copied, never the truncated display.
 *
 * `HashValue` shows a hash truncated in the middle (`3f9a…c21e`, first 4 and
 * last 4) with the full value in `title`, plus a CopyButton. The demo
 * placeholder (`— demo, no server hash —`) is never truncated and never gets
 * a copy button.
 */
import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { Icon } from '../icons';

export const COPIED_MS = 1200;

/** `3f9a…c21e`: first 4 and last 4 characters; short values are unchanged. */
export function truncateMiddle(value: string, head = 4, tail = 4): string {
  if (value.length <= head + tail + 1) return value;
  return `${value.slice(0, head)}…${value.slice(value.length - tail)}`;
}

/** True for a placeholder that is not a real server hash (never truncated or copied). */
export function isPlaceholderHash(value: string): boolean {
  return /\s/.test(value) || value.startsWith('—');
}

async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.className = 'visually-hidden';
    document.body.appendChild(ta);
    ta.select();
    const ok = typeof document.execCommand === 'function' && document.execCommand('copy');
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

export function CopyButton(props: {
  /** The full text to copy. */
  value: string;
  /** What is copied, for the accessible name: `Copy {label}`. */
  label: string;
  className?: string;
}): JSX.Element {
  const { value, label, className } = props;
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const onClick = async (e: MouseEvent): Promise<void> => {
    // Copy buttons live inside clickable rows; copying must not navigate.
    e.stopPropagation();
    const ok = await writeClipboard(value);
    if (!ok) return;
    setCopied(true);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), COPIED_MS);
  };

  return (
    <button
      type="button"
      className={`btn ghost icon-only copy-btn${copied ? ' copied' : ''}${className ? ` ${className}` : ''}`}
      aria-label={`Copy ${label}`}
      onClick={(e) => void onClick(e)}
    >
      <Icon name={copied ? 'check' : 'copy'} size={14} />
      <span className="visually-hidden" aria-live="polite">
        {copied ? 'Copied' : ''}
      </span>
    </button>
  );
}

/**
 * A hash shown truncated in the middle with a copy button. Placeholders
 * (anything with whitespace or starting with an em dash) render verbatim
 * with no button.
 */
export function HashValue(props: { value: string; label?: string; className?: string }): JSX.Element {
  const { value, label = 'config hash', className } = props;
  if (isPlaceholderHash(value)) {
    return <span className={`hash-value mono${className ? ` ${className}` : ''}`}>{value}</span>;
  }
  const short = truncateMiddle(value);
  if (short === value) {
    return (
      <span className={`hash-value${className ? ` ${className}` : ''}`}>
        <span className="mono">{value}</span>
        <CopyButton value={value} label={label} />
      </span>
    );
  }
  return (
    <span className={`hash-value${className ? ` ${className}` : ''}`}>
      {/* the truncated form is for the eye; assistive tech (and text search)
          get the full value, never the shortened one */}
      <span className="mono" title={value} aria-hidden="true">
        {short}
      </span>
      <span className="visually-hidden">{value}</span>
      <CopyButton value={value} label={label} />
    </span>
  );
}
