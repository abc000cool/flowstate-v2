/** Scenario schematic thumbnails (ring, corridor, OSM import) for the
 * scenario cards (docs/design/DASHBOARD_DESIGN.md §9.4). Strokes and fills
 * are theme tokens only — `--border-strong` for road, `--text-secondary` for
 * vehicles and markings, `--accent-solid` for the controlled vehicle — so the
 * thumbnails follow the light and dark themes. */

import type { Network } from '../api/types';

const ROAD = 'var(--border-strong)';
const MARK = 'var(--text-secondary)';
const AV = 'var(--accent-solid)';
const WELL = 'var(--bg-surface)';

/** Ring: circle of vehicle dots (one accent = controlled vehicle). */
function RingThumb({ n }: { n: number }): JSX.Element {
  const cx = 60;
  const cy = 48;
  const r = 34;
  const count = Math.min(Math.max(n, 8), 30);
  const dots = Array.from({ length: count }, (_, i) => {
    const a = (2 * Math.PI * i) / count - Math.PI / 2;
    return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a), av: i === 0 };
  });
  return (
    <svg width="120" height="96" viewBox="0 0 120 96" aria-hidden="true" focusable="false">
      <circle cx={cx} cy={cy} r={r} fill="none" stroke={ROAD} strokeWidth={8} />
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill="none"
        stroke={WELL}
        strokeWidth={1}
        strokeDasharray="2 4"
      />
      {dots.map((d, i) => (
        <circle key={i} cx={d.x} cy={d.y} r={d.av ? 3.6 : 2.4} fill={d.av ? AV : MARK} />
      ))}
    </svg>
  );
}

/** Corridor: lane lines with dashes and a few vehicle ticks. */
function CorridorThumb({ lanes }: { lanes: number }): JSX.Element {
  const laneN = Math.min(Math.max(lanes, 1), 4);
  const laneH = 14;
  const top = 48 - (laneN * laneH) / 2;
  const cars = [
    { x: 18, lane: 0, av: false },
    { x: 44, lane: laneN > 1 ? 1 : 0, av: true },
    { x: 66, lane: 0, av: false },
    { x: 92, lane: laneN > 1 ? 1 : 0, av: false },
    { x: 130, lane: 0, av: false },
  ];
  return (
    <svg width="170" height="96" viewBox="0 0 170 96" aria-hidden="true" focusable="false">
      <rect
        x={6}
        y={top - 3}
        width={158}
        height={laneN * laneH + 6}
        rx={3}
        fill={WELL}
        stroke={ROAD}
      />
      {Array.from({ length: laneN + 1 }, (_, i) => {
        const edge = i === 0 || i === laneN;
        return (
          <line
            key={i}
            x1={10}
            x2={160}
            y1={top + i * laneH}
            y2={top + i * laneH}
            stroke={edge ? ROAD : MARK}
            strokeOpacity={edge ? 1 : 0.5}
            strokeWidth={edge ? 1.5 : 1}
            strokeDasharray={edge ? undefined : '5 5'}
          />
        );
      })}
      {cars.map((c, i) => (
        <rect
          key={i}
          x={c.x}
          y={top + c.lane * laneH + laneH / 2 - 2.5}
          width={9}
          height={5}
          rx={1.5}
          fill={c.av ? AV : MARK}
        />
      ))}
      <path d="M158 42 l6 6 -6 6" fill="none" stroke={MARK} strokeWidth={1.5} />
    </svg>
  );
}

function OsmThumb(): JSX.Element {
  return (
    <svg width="120" height="96" viewBox="0 0 120 96" aria-hidden="true" focusable="false">
      <path
        d="M10 70 C 40 60, 50 30, 110 26"
        fill="none"
        stroke={ROAD}
        strokeWidth={8}
        strokeLinecap="round"
      />
      <path
        d="M10 70 C 40 60, 50 30, 110 26"
        fill="none"
        stroke={WELL}
        strokeWidth={1}
        strokeDasharray="3 5"
      />
      <path
        d="M30 90 C 45 70, 42 50, 58 40"
        fill="none"
        stroke={ROAD}
        strokeWidth={5}
        strokeLinecap="round"
      />
      <circle cx={78} cy={33} r={3.4} fill={AV} />
    </svg>
  );
}

export function SchematicThumb({ network, name }: { network?: Network; name: string }): JSX.Element {
  if (network?.kind === 'ring') return <RingThumb n={network.n_vehicles} />;
  if (network?.kind === 'corridor') return <CorridorThumb lanes={network.lanes} />;
  if (network?.kind === 'osm') return <OsmThumb />;
  // no embedded config — guess from the name
  if (name.toLowerCase().includes('ring')) return <RingThumb n={22} />;
  return <CorridorThumb lanes={1} />;
}
