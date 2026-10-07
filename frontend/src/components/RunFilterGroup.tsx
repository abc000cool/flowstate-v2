/** One dimension of a runs filter bar (docs/design/DASHBOARD_DESIGN.md §10.3
 * P2): a sentence-case label, then "All" and a toggle chip per value, each
 * with the rows it would show given the other dimensions (`lib/runFilters`).
 * Shared by the Runs table and the Compare pickers; the styles are the
 * `.runs-filter-*` rules in styles/views/runs.css. */

import type { RunFacet, RunFilterKey } from '../lib/runFilters';

export function RunFilterGroup({
  id,
  label,
  facet,
  chosen,
  labelFor,
  titleFor,
  onAll,
  onToggle,
  idPrefix = 'runs-filter',
}: {
  id: RunFilterKey;
  label: string;
  facet: RunFacet;
  chosen: string[];
  labelFor: (value: string) => string;
  titleFor?: (value: string) => string | undefined;
  onAll: () => void;
  onToggle: (value: string) => void;
  /** Prefix of the group label's id (`{idPrefix}-{id}`). */
  idPrefix?: string;
}): JSX.Element {
  return (
    <div className="runs-filter-group" role="group" aria-labelledby={`${idPrefix}-${id}`}>
      <span className="runs-filter-label" id={`${idPrefix}-${id}`}>
        {label}
      </span>
      <button
        type="button"
        className="chip-toggle filter-chip"
        aria-pressed={chosen.length === 0}
        onClick={onAll}
      >
        <span className="filter-chip-label">All</span>
        <span className="filter-chip-count mono">{facet.total}</span>
      </button>
      {facet.values.map(({ value, count }) => (
        <button
          key={value}
          type="button"
          className="chip-toggle filter-chip"
          aria-pressed={chosen.includes(value)}
          title={titleFor?.(value)}
          onClick={() => onToggle(value)}
        >
          <span className="filter-chip-label">{labelFor(value)}</span>
          <span className="filter-chip-count mono">{count}</span>
        </button>
      ))}
    </div>
  );
}
