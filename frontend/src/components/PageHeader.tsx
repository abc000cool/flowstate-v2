/** The one page header every view uses (docs/design/DASHBOARD_DESIGN.md §6.2):
 *
 *   Title (h1)  [meta: count · badges]              [ghost] [secondary] [primary]
 *   One-sentence description of what the page is for.
 *
 * `meta` sits inline after the title (counts in mono, a live indicator, the
 * DEMO DATA badge). `actions` are right-aligned, at most one primary, and wrap
 * under the description below 1024 px. `documentTitle` names the browser tab
 * (`Runs · FlowState`) while the page is mounted. */

import { useEffect, type ReactNode } from 'react';

export const APP_TITLE = 'FlowState';

export function PageHeader(props: {
  title: ReactNode;
  description?: ReactNode;
  /** Counts, live indicator, DEMO DATA badge. */
  meta?: ReactNode;
  /** Right-aligned; at most one primary. */
  actions?: ReactNode;
  /** Sets document.title = `${documentTitle} · FlowState`. */
  documentTitle?: string;
}): JSX.Element {
  const { title, description, meta, actions, documentTitle } = props;

  useEffect(() => {
    if (documentTitle === undefined) return;
    document.title = `${documentTitle} · ${APP_TITLE}`;
    return () => {
      document.title = APP_TITLE;
    };
  }, [documentTitle]);

  return (
    <div className="page-header">
      <div className="page-header-main">
        <div className="page-header-titlerow">
          <h1 className="page-title">{title}</h1>
          {meta != null && <div className="page-meta">{meta}</div>}
        </div>
        {description != null && <p className="page-desc">{description}</p>}
      </div>
      {actions != null && <div className="page-actions">{actions}</div>}
    </div>
  );
}
