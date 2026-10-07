/**
 * Callout: a persistent, in-flow message box (docs/design/DASHBOARD_DESIGN.md
 * §9.8). Use it for state the screen must keep showing (a failed fetch the
 * panel depends on, a caution about provenance). Toasts only echo.
 *
 * `role` is 'note' or 'status', never 'alert': role="alert" is reserved for
 * the shell's auth banner.
 */
import type { ReactNode } from 'react';
import { Icon, type IconName } from '../icons';

export type CalloutTone = 'info' | 'success' | 'warning' | 'danger' | 'neutral';

const TONE_ICON: Record<CalloutTone, IconName> = {
  info: 'info',
  success: 'check',
  warning: 'triangle-alert',
  danger: 'circle-alert',
  neutral: 'info',
};

export function Callout(props: {
  tone: CalloutTone;
  title?: ReactNode;
  children: ReactNode;
  /** A sm button, right-aligned. */
  action?: ReactNode;
  /** Never 'alert': role=alert is reserved for the auth banner. */
  role?: 'note' | 'status';
}): JSX.Element {
  const { tone, title, children, action, role } = props;
  return (
    <div className={`callout callout-${tone}`} role={role}>
      <span className="callout-icon">
        <Icon name={TONE_ICON[tone]} size={16} />
      </span>
      <div className="callout-main">
        {title != null && <div className="callout-title">{title}</div>}
        <div className="callout-body">{children}</div>
      </div>
      {action != null && <div className="callout-action">{action}</div>}
    </div>
  );
}
