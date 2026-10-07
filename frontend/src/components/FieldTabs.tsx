/** SPEED | DENSITY segmented tabs over a space–time field panel
 * (docs/design/DASHBOARD_DESIGN.md §9.5): `role="tablist"` with the tab
 * text `SPEED` / `DENSITY` (test anchors), and Left/Right move and select.
 * Shared by Run detail and Compare; `idPrefix` keeps the tab ids unique
 * when a page could hold two, and `panelId` names the tabpanel they
 * control. */

import type { KeyboardEvent } from 'react';
import type { HeatField } from '../api/types';

export const HEAT_FIELDS: HeatField[] = ['speed', 'density'];

/** The id of a field's tab: `{idPrefix}-tab-{field}`. */
export function fieldTabId(idPrefix: string, field: HeatField): string {
  return `${idPrefix}-tab-${field}`;
}

export function FieldTabs({
  field,
  onChange,
  idPrefix = 'field',
  panelId = 'field-tabpanel',
}: {
  field: HeatField;
  onChange: (f: HeatField) => void;
  idPrefix?: string;
  panelId?: string;
}): JSX.Element {
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const i = HEAT_FIELDS.indexOf(field);
    const next =
      HEAT_FIELDS[(i + (e.key === 'ArrowRight' ? 1 : HEAT_FIELDS.length - 1)) % HEAT_FIELDS.length];
    onChange(next);
    e.currentTarget.querySelector<HTMLButtonElement>(`#${fieldTabId(idPrefix, next)}`)?.focus();
  };
  return (
    <div className="seg" role="tablist" aria-label="Heatmap field" onKeyDown={onKeyDown}>
      {HEAT_FIELDS.map((f) => (
        <button
          key={f}
          id={fieldTabId(idPrefix, f)}
          type="button"
          role="tab"
          aria-selected={field === f}
          aria-controls={panelId}
          tabIndex={field === f ? 0 : -1}
          className={field === f ? 'active' : ''}
          onClick={() => onChange(f)}
        >
          {f.toUpperCase()}
        </button>
      ))}
    </div>
  );
}
