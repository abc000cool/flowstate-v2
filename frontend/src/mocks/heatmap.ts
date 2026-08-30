/** Programmatic synthetic space-time fields for mock mode.
 *
 * The corridor field is a free-flow plane at 30 m/s cut by diagonal
 * backward-propagating wave bands: each band's centre moves upstream at
 * -16 km/h and speed inside dips to 3 m/s with a Gaussian cross-profile —
 * i.e. exactly the signature the real micro tier produces when IDM goes
 * string-unstable. A triangular null region at the start models the corridor
 * before the first vehicles arrive. */

import type { Heatmap } from '../api/types';

/** Deterministic PRNG (mulberry32) so demo data is stable across reloads. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), a | 1);
    t = (t + Math.imul(t ^ (t >>> 7), t | 61)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const V_FREE_MS = 30;
const V_JAM_MS = 3;
const WAVE_SPEED_MS = -16 / 3.6; // -16 km/h, empirical stop-and-go range

interface WaveBand {
  /** Time at which the band centre is at x0 (may be negative). */
  t0: number;
  /** Band centre position at t0 [m]. */
  x0: number;
  /** Depth multiplier in (0, 1]; 1 = full dip to V_JAM_MS. */
  depth: number;
}

export interface CorridorFieldOpts {
  durationS?: number;
  dtS?: number;
  lengthM?: number;
  dxM?: number;
  bands?: WaveBand[];
  seed?: number;
}

const DEFAULT_BANDS: WaveBand[] = [
  { t0: -420, x0: 9200, depth: 1.0 },
  { t0: 90, x0: 8800, depth: 0.95 },
  { t0: 540, x0: 9300, depth: 0.9 },
];

function edges(n: number, step: number): number[] {
  return Array.from({ length: n + 1 }, (_, i) => i * step);
}

export function corridorSpeedField(opts: CorridorFieldOpts = {}): Heatmap {
  const duration = opts.durationS ?? 1200;
  const dt = opts.dtS ?? 5;
  const length = opts.lengthM ?? 10000;
  const dx = opts.dxM ?? 100;
  const bands = opts.bands ?? DEFAULT_BANDS;
  const rng = mulberry32(opts.seed ?? 1234);

  const nt = Math.round(duration / dt);
  const nx = Math.round(length / dx);
  const sigma = 190; // band half-width [m]

  const values: (number | null)[][] = [];
  for (let it = 0; it < nt; it++) {
    const t = (it + 0.5) * dt;
    const row: (number | null)[] = [];
    for (let ix = 0; ix < nx; ix++) {
      const x = (ix + 0.5) * dx;
      // leading vehicles travel downstream at ~free flow; ahead of them: empty
      if (x > V_FREE_MS * t + 400) {
        row.push(null);
        continue;
      }
      let v = V_FREE_MS;
      for (const b of bands) {
        if (t < b.t0) continue;
        const centre = b.x0 + WAVE_SPEED_MS * (t - b.t0);
        if (centre < -2 * sigma) continue;
        const d = x - centre;
        const dip = (V_FREE_MS - V_JAM_MS) * b.depth * Math.exp(-(d * d) / (sigma * sigma));
        v = Math.min(v, V_FREE_MS - dip);
      }
      v += (rng() - 0.5) * 1.6; // measurement-scale noise
      row.push(Math.max(0.3, Math.min(33, v)));
    }
    values.push(row);
  }

  return { t_edges: edges(nt, dt), x_edges: edges(nx, dx), values };
}

/** Ring field: one slow-moving backward wave circulating the 230 m loop. */
export function ringSpeedField(seed = 7): Heatmap {
  const duration = 300;
  const dt = 2;
  const circumference = 230;
  const dx = 5;
  const nt = Math.round(duration / dt);
  const nx = Math.round(circumference / dx);
  const rng = mulberry32(seed);

  const vFree = 8; // ring equilibrium speed is low
  const vJam = 0.5;
  const cWave = -1.35; // road-frame backward drift [m/s]
  const sigma = 16;

  const values: (number | null)[][] = [];
  for (let it = 0; it < nt; it++) {
    const t = (it + 0.5) * dt;
    const row: (number | null)[] = [];
    // wave forms after ~40 s (emergent, not seeded)
    const grow = Math.min(1, Math.max(0, (t - 40) / 60));
    let centre = (120 + cWave * t) % circumference;
    if (centre < 0) centre += circumference;
    for (let ix = 0; ix < nx; ix++) {
      const x = (ix + 0.5) * dx;
      let d = Math.abs(x - centre);
      d = Math.min(d, circumference - d); // wrap distance
      const dip = (vFree - vJam) * grow * Math.exp(-(d * d) / (sigma * sigma));
      const v = vFree - dip + (rng() - 0.5) * 0.5;
      row.push(Math.max(0.1, v));
    }
    values.push(row);
  }

  return { t_edges: edges(nt, dt), x_edges: edges(nx, dx), values };
}

/** Density derived from the speed field via a monotone speed–density map
 * anchored at 38 veh/km free flow and ~140 veh/km inside jams. */
export function toDensityField(speed: Heatmap, vRef = 30): Heatmap {
  const rhoFree = 0.038;
  const rhoJam = 0.14;
  const values = speed.values.map((row) =>
    row.map((v) => {
      if (v === null) return null;
      const f = Math.max(0, Math.min(1, 1 - v / vRef));
      return rhoFree + f * (rhoJam - rhoFree);
    }),
  );
  return { t_edges: speed.t_edges, x_edges: speed.x_edges, values };
}
