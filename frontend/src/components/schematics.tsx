/** Scenario schematic thumbnails (ring, corridor, OSM import) for the
 * scenario cards. Moved verbatim from bits.tsx
 * (docs/design/DASHBOARD_DESIGN.md §12.2, step 0). */

import type { Network } from '../api/types';

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
    <svg width="120" height="96" viewBox="0 0 120 96" aria-hidden>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--panel-edge)" strokeWidth={6} />
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="#232b3d" strokeWidth={1} strokeDasharray="2 4" />
      {dots.map((d, i) => (
        <circle
          key={i}
          cx={d.x}
          cy={d.y}
          r={d.av ? 3.4 : 2.4}
          fill={d.av ? 'var(--accent)' : 'var(--muted)'}
        />
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
    <svg width="170" height="96" viewBox="0 0 170 96" aria-hidden>
      <rect x={6} y={top - 3} width={158} height={laneN * laneH + 6} rx={3} fill="#0d1119" stroke="var(--panel-edge)" />
      {Array.from({ length: laneN + 1 }, (_, i) => (
        <line
          key={i}
          x1={10}
          x2={160}
          y1={top + i * laneH}
          y2={top + i * laneH}
          stroke={i === 0 || i === laneN ? '#2a3348' : '#232b3d'}
          strokeWidth={i === 0 || i === laneN ? 1.5 : 1}
          strokeDasharray={i === 0 || i === laneN ? undefined : '5 5'}
        />
      ))}
      {cars.map((c, i) => (
        <rect
          key={i}
          x={c.x}
          y={top + c.lane * laneH + laneH / 2 - 2.5}
          width={9}
          height={5}
          rx={1.5}
          fill={c.av ? 'var(--accent)' : 'var(--muted)'}
        />
      ))}
      <path d="M158 42 l6 6 -6 6" fill="none" stroke="var(--faint)" strokeWidth={1.5} />
    </svg>
  );
}

function OsmThumb(): JSX.Element {
  return (
    <svg width="120" height="96" viewBox="0 0 120 96" aria-hidden>
      <path d="M10 70 C 40 60, 50 30, 110 26" fill="none" stroke="var(--panel-edge)" strokeWidth={7} strokeLinecap="round" />
      <path d="M10 70 C 40 60, 50 30, 110 26" fill="none" stroke="#232b3d" strokeWidth={1} strokeDasharray="3 5" />
      <path d="M30 90 C 45 70, 42 50, 58 40" fill="none" stroke="var(--panel-edge)" strokeWidth={4} strokeLinecap="round" />
      <circle cx={78} cy={33} r={3} fill="var(--accent)" />
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
