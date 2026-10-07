/**
 * EmptyState (docs/design/DASHBOARD_DESIGN.md §9.9, Stripe pattern): the
 * title states what is missing and ends with a period ("No runs yet."); the
 * description (< 14 words) says when or how data appears; the action echoes
 * the title. A filtered-to-zero list says "No runs match these filters." and
 * offers "Clear filters", never "create your first".
 *
 * `compact` is for use inside a table cell (16 px padding, no dashed border).
 */
import type { ReactNode } from 'react';

export function EmptyState(props: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  /** 20 px, optional. */
  icon?: ReactNode;
  /** Inside tables: 16px padding, no dashed border. */
  compact?: boolean;
}): JSX.Element {
  const { title, description, action, icon, compact } = props;
  return (
    <div className={compact ? 'empty-state compact' : 'empty-state'}>
      {icon != null && <span className="empty-state-icon">{icon}</span>}
      <p className="empty-state-title">{title}</p>
      {description != null && <p className="empty-state-desc">{description}</p>}
      {action != null && <div className="empty-state-action">{action}</div>}
    </div>
  );
}
