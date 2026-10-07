/**
 * Skeletons (docs/design/DASHBOARD_DESIGN.md §9.11). First load only. The
 * blocks are aria-hidden; the surrounding region sets aria-busy="true".
 *
 *   <Skeleton width={120} height={12} />
 *   <tbody><SkeletonRows rows={5} columns={6} /></tbody>
 */

export type SkeletonRadius = 'xs' | 'sm' | 'md';

function cssLength(v: number | string | undefined): string | undefined {
  if (v === undefined) return undefined;
  return typeof v === 'number' ? `${v}px` : v;
}

export function Skeleton(props: {
  width?: number | string;
  height?: number | string;
  radius?: SkeletonRadius;
}): JSX.Element {
  const { width, height, radius = 'xs' } = props;
  return (
    <span
      className={`skeleton skeleton-r-${radius}`}
      aria-hidden="true"
      // computed geometry: the caller's requested size
      style={{ width: cssLength(width), height: cssLength(height) }}
    />
  );
}

/** Varying widths so the placeholder rows don't read as a solid grid. */
const CELL_WIDTHS = ['72%', '48%', '60%', '36%', '54%', '40%'];

/** `rows` placeholder `<tr>`s of `columns` cells each, for a `<tbody>`. */
export function SkeletonRows(props: { rows: number; columns: number }): JSX.Element {
  const { rows, columns } = props;
  return (
    <>
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r} className="skeleton-row" aria-hidden="true">
          {Array.from({ length: columns }, (_, c) => (
            <td key={c}>
              <Skeleton width={CELL_WIDTHS[(r + c) % CELL_WIDTHS.length]} height={12} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
