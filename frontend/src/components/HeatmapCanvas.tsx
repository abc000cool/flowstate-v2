/** Space-time heatmap (docs/design/DASHBOARD_DESIGN.md §7.3). x-axis = time,
 * y-axis = position (downstream up), colour = speed or density via the
 * theme-invariant ramps in lib/colormap.
 *
 * Bins are painted at native resolution into an offscreen canvas and scaled
 * with nearest-neighbour so wave fronts stay crisp. Empty (null) bins are
 * then painted with `--viz-null-bg` and a 45° hatch, so "no data" never reads
 * as stopped traffic. The chrome (frame, ticks, crosshair, hatch) reads the
 * `--viz-*` tokens at paint time and repaints when the theme flips; the data
 * colours never change with the theme.
 *
 * Inspect: hover, or focus the plot and use the arrow keys (Shift = 10 bins,
 * Home/End = the time edges). Either way the readout row under the legend
 * shows `t · x · value`; it is a polite live region only while the plot has
 * focus. The binned field is also downloadable as CSV (the table-view twin),
 * headed by `#` provenance lines: run, tier ("screening" for a macro run),
 * seed and replicate, config hash, units, bin sizes and export time.
 * The plot follows its container through a ResizeObserver, so it tracks a
 * sidebar collapse, not only window resizes. */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import type { HeatField, Heatmap, RunDetail, Tier } from '../api/types';
import {
  binColor,
  rampGradientCSS,
  stopsFor,
  WAVE_THRESHOLD_DEFAULT_KMH,
} from '../lib/colormap';
import { DEMO_HASH_LABEL } from '../lib/demo';
import { saveText } from '../lib/download';
import {
  distUnit,
  formatDensityVehKmMi,
  formatDistAdaptive,
  formatSpeedKmhMph,
  formatTickDist,
  formatTickMin,
  formatTimeMin,
  KM_PER_MI,
  spaceTicks,
  timeTicks,
} from '../lib/format';
import { readToken, useResolvedTheme } from '../lib/theme';

const MARGIN = { l: 64, r: 12, t: 8, b: 40 };
/** Hatch tile: a 1 px line every 6 px. */
const HATCH_PX = 6;
const TICK_LEN = 4;

/** The plot's height for a container width: clamp(300, 0.42 w, 460), with a
 * 240 px floor on narrow (tablet) containers. */
export function heatmapHeight(cssW: number): number {
  const floor = cssW < 720 ? 240 : 300;
  return Math.round(Math.min(460, Math.max(floor, cssW * 0.42)));
}

/** True when the API returned no bins on either axis. */
export function isEmptyHeatmap(h: Heatmap): boolean {
  return h.values.length === 0 || h.values[0].length === 0;
}

/** Outer edges of the field. The API sends bin centers; extend by half a bin
 * on each side so the outer bins are drawn at full width. */
export function heatmapExtent(h: Heatmap): { t0: number; t1: number; x0: number; x1: number } {
  const half = (c: number[]): number => (c.length > 1 ? (c[1] - c[0]) / 2 : 0.5);
  return {
    t0: (h.t_bins[0] ?? 0) - half(h.t_bins),
    t1: (h.t_bins[h.t_bins.length - 1] ?? 1) + half(h.t_bins),
    x0: (h.x_bins[0] ?? 0) - half(h.x_bins),
    x1: (h.x_bins[h.x_bins.length - 1] ?? 1) + half(h.x_bins),
  };
}

/** The binned field as CSV in SI units: `t_s,x_m,speed_ms` (or
 * `density_vehm`), one row per bin, empty for a bin with no vehicle. Exactly
 * the values already on screen; nothing is fetched or computed. This is the
 * data block only: the exported file puts `heatmapCSVHeader` ahead of it. */
export function heatmapCSV(h: Heatmap, field: HeatField): string {
  const col = field === 'speed' ? 'speed_ms' : 'density_vehm';
  const lines = [`t_s,x_m,${col}`];
  for (let it = 0; it < h.values.length; it++) {
    const row = h.values[it];
    for (let ix = 0; ix < row.length; ix++) {
      const v = row[ix];
      lines.push(`${h.t_bins[it]},${h.x_bins[ix]},${v === null || !Number.isFinite(v) ? '' : v}`);
    }
  }
  return `${lines.join('\n')}\n`;
}

/** What the page knows about an exported field beyond the field itself. */
export interface HeatmapExportContext {
  runId: string;
  /** The run on screen: its seed list places the replicate, its `seeded`
   * label travels with the file, and its tier and hash stand in when the
   * heatmap answer leaves them out (an older service). */
  run?: Pick<RunDetail, 'tier' | 'config_hash' | 'seeds' | 'seeded'> | null;
  /** The field was served by the in-browser demo backend. */
  demo?: boolean;
  /** When the file is written; defaults to now. */
  exportedAt?: Date;
}

/** Where an exported field came from. Every value is one the API answered
 * (or the run record it came with); an unknown stays null, never a guess. */
export interface HeatmapProvenance {
  runId: string;
  field: HeatField;
  /** The replicate's RNG seed (`HeatmapOut.seed`). */
  seed: number | null;
  /** 1-based position of `seed` in the run's seed list, and its length. */
  replicate: { index: number; of: number } | null;
  configHash: string | null;
  tier: Tier | null;
  seeded: boolean | null;
  demo: boolean;
  exportedAt: Date;
}

export function heatmapProvenance(
  h: Heatmap,
  field: HeatField,
  ctx: HeatmapExportContext,
): HeatmapProvenance {
  const seed = h.seed ?? null;
  const seeds = ctx.run?.seeds ?? [];
  const at = seed === null ? -1 : seeds.indexOf(seed);
  return {
    runId: ctx.runId,
    field,
    seed,
    replicate: at < 0 ? null : { index: at + 1, of: seeds.length },
    configHash: h.config_hash ?? ctx.run?.config_hash ?? null,
    tier: h.tier ?? ctx.run?.tier ?? null,
    seeded: ctx.run?.seeded ?? null,
    demo: ctx.demo ?? false,
    exportedAt: ctx.exportedAt ?? new Date(),
  };
}

/** The spacing of bin centres: one number when uniform, else what it is. */
function binSpacing(centers: number[]): string {
  if (centers.length < 2) return centers.length === 1 ? 'unknown (one bin)' : 'unknown (no bins)';
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 1; i < centers.length; i++) {
    const step = centers[i] - centers[i - 1];
    lo = Math.min(lo, step);
    hi = Math.max(hi, step);
  }
  const tidy = (v: number): string => String(Number(v.toPrecision(6)));
  const tol = 1e-6 * Math.max(Math.abs(lo), Math.abs(hi), 1);
  return hi - lo <= tol ? tidy(lo) : `irregular (${tidy(lo)} to ${tidy(hi)})`;
}

const TIER_LINE: Record<Tier, string> = {
  micro: 'micro (SUMO microsimulation)',
  macro: 'screening (macro CTM run: a screening result, it cannot support validation claims)',
};

const FIELD_LINE: Record<HeatField, { field: string; unit: string }> = {
  speed: { field: 'speed (mean speed in each bin)', unit: 'speed_ms = m/s' },
  density: { field: 'density (mean density in each bin)', unit: 'density_vehm = veh/m' },
};

/** The `#` comment lines ahead of the data block: `# key: value`, one per
 * line, the convention `pandas.read_csv(comment="#")`, `numpy.loadtxt` and
 * R's `read.csv(comment.char = "#")` skip. Ends with a newline. */
export function heatmapCSVHeader(h: Heatmap, p: HeatmapProvenance): string {
  const unknown = 'unknown (the service did not report it)';
  const lines: [string, string][] = [
    ['run_id', p.runId],
    [
      'source',
      p.demo
        ? 'DEMO (built-in demo data from the in-browser backend, not a server answer: no server ran this run)'
        : `server (GET /runs/${p.runId}/heatmap)`,
    ],
    ['tier', p.tier === null ? unknown : TIER_LINE[p.tier]],
    ['seeded', p.seeded === null ? 'unknown' : String(p.seeded)],
    ['seed', p.seed === null ? 'unknown (the service did not say which replicate this is)' : String(p.seed)],
    [
      'replicate',
      p.seed === null
        ? "unknown (one replicate's field, not a mean over the run)"
        : p.replicate === null
          ? "the seed above (one replicate's field, not a mean over the run)"
          : `${p.replicate.index} of ${p.replicate.of} (this replicate's field alone, not a mean over the run)`,
    ],
    ['config_hash', p.demo ? DEMO_HASH_LABEL : (p.configHash ?? unknown)],
    ['field', FIELD_LINE[p.field].field],
    [
      'units',
      `t_s = s, x_m = m (bin centres); ${FIELD_LINE[p.field].unit}; empty = no data for the bin`,
    ],
    ['t_bin_s', binSpacing(h.t_bins)],
    ['x_bin_m', binSpacing(h.x_bins)],
    ['exported_at', p.exportedAt.toISOString()],
  ];
  const title =
    '# FlowState space-time field, one replicate, binned. Lines starting with # are metadata.';
  return `${[title, ...lines.map(([k, v]) => `# ${k}: ${v}`)].join('\n')}\n`;
}

/** `flowstate-{run_id}-{field}-seed{seed}-{tier}.csv`, the tier being
 * `screening` for a macro run; `-demo` marks a demo export, and an unknown
 * seed or tier says so rather than being left out. */
export function heatmapCSVFilename(
  p: Pick<HeatmapProvenance, 'runId' | 'field' | 'seed' | 'tier' | 'demo'>,
): string {
  const seed = p.seed === null ? 'seed-unknown' : `seed${p.seed}`;
  const tier = p.tier === null ? 'tier-unknown' : p.tier === 'macro' ? 'screening' : p.tier;
  return `flowstate-${p.runId}-${p.field}-${seed}-${tier}${p.demo ? '-demo' : ''}.csv`;
}

/** The exported file: provenance header, then the unchanged data block. */
export function heatmapExport(
  h: Heatmap,
  field: HeatField,
  ctx: HeatmapExportContext,
): { filename: string; text: string } {
  const p = heatmapProvenance(h, field, ctx);
  return { filename: heatmapCSVFilename(p), text: heatmapCSVHeader(h, p) + heatmapCSV(h, field) };
}

/** Save the field on screen as CSV (lib/download.saveText; no new API). */
export function downloadHeatmapCSV(h: Heatmap, field: HeatField, ctx: HeatmapExportContext): void {
  const { filename, text } = heatmapExport(h, field, ctx);
  saveText(text, filename, 'text/csv');
}

/** The value readout for one bin: `47 km/h (29 mph)`, `38 veh/km (61 veh/mi)`
 * or `no data`. */
export function formatBinValue(field: HeatField, v: number | null): string {
  if (v === null || !Number.isFinite(v)) return 'no data';
  return field === 'speed' ? formatSpeedKmhMph(v) : formatDensityVehKmMi(v);
}

/* ------------------------------ legend ------------------------------ */

interface LegendTick {
  /** Position along the bar, 0–1. */
  at: number;
  label: string;
}

interface LegendSpec {
  unitTop: string;
  unitBottom: string;
  top: LegendTick[];
  bottom: LegendTick[];
  /** "0–120 km/h (0–75 mph)": the range, for the plot's accessible name. */
  range: string;
}

/** Nominal top of each display domain in the legend's primary unit. */
const SPEED_LEGEND_MAX_KMH = 120;
const DENSITY_LEGEND_MAX_VEHKM = 160;

const range = (from: number, to: number, step: number): number[] => {
  const out: number[] = [];
  for (let v = from; v <= to + 1e-9; v += step) out.push(v);
  return out;
};

const LEGENDS: Record<HeatField, LegendSpec> = {
  speed: {
    unitTop: 'km/h',
    unitBottom: 'mph',
    top: range(0, 120, 20).map((k) => ({ at: k / SPEED_LEGEND_MAX_KMH, label: String(k) })),
    // mph ticks sit where that speed falls on the km/h scale
    bottom: range(0, 70, 10).map((m) => ({
      at: (m * KM_PER_MI) / SPEED_LEGEND_MAX_KMH,
      label: String(m),
    })),
    range: `0–120 km/h (0–${Math.round(SPEED_LEGEND_MAX_KMH / KM_PER_MI)} mph)`,
  },
  density: {
    unitTop: 'veh/km',
    unitBottom: 'veh/mi',
    top: range(0, 160, 40).map((k) => ({ at: k / DENSITY_LEGEND_MAX_VEHKM, label: String(k) })),
    bottom: range(0, 250, 50).map((m) => ({
      at: m / KM_PER_MI / DENSITY_LEGEND_MAX_VEHKM,
      label: String(m),
    })),
    range: `0–160 veh/km (0–${Math.round(DENSITY_LEGEND_MAX_VEHKM * KM_PER_MI)} veh/mi)`,
  },
};

/** Where the wave-threshold notch sits on the speed bar. */
const THRESHOLD_AT = WAVE_THRESHOLD_DEFAULT_KMH / SPEED_LEGEND_MAX_KMH;

function TickRow({ ticks, className }: { ticks: LegendTick[]; className: string }): JSX.Element {
  return (
    <div className={`rl-ticks ${className}`}>
      {ticks.map((t) => (
        // computed geometry: the tick's position on the bar
        <span key={t.label} className="rl-tick" style={{ left: `${t.at * 100}%` }}>
          {t.label}
        </span>
      ))}
    </div>
  );
}

/** The colour-scale legend (§7.3): a 320 px bar drawn from the ramp's own
 * stops, primary-unit ticks above, the imperial unit below, the default wave
 * threshold notched at 40 km/h, and the hatched "no data" key. */
export function RampLegend({ field }: { field: HeatField }): JSX.Element {
  const spec = LEGENDS[field];
  return (
    <div className="ramp-legend">
      <div className="rl-scale">
        <span className="rl-unit">{spec.unitTop}</span>
        <TickRow ticks={spec.top} className="rl-ticks-top" />
        <span aria-hidden="true" />
        <div
          className="rl-bar"
          // computed: the gradient is generated from the colormap's stops
          style={{ background: rampGradientCSS(stopsFor(field)) }}
        >
          {field === 'speed' && (
            <span className="rl-notch" style={{ left: `${THRESHOLD_AT * 100}%` }} />
          )}
        </div>
        <span className="rl-unit">{spec.unitBottom}</span>
        <TickRow ticks={spec.bottom} className="rl-ticks-bottom" />
      </div>
      <div className="rl-keys">
        <span className="rl-key">
          <span className="rl-swatch-null" aria-hidden="true" />
          no data
        </span>
        {field === 'speed' && (
          <span className="rl-key">
            <span className="rl-swatch-notch" aria-hidden="true" />
            wave threshold {WAVE_THRESHOLD_DEFAULT_KMH} km/h (default)
          </span>
        )}
      </div>
    </div>
  );
}

/* ------------------------------- plot ------------------------------- */

/** The inspected bin, and where the crosshair sits (pointer, or the bin's
 * center when keyboard-driven). */
interface Cursor {
  it: number;
  ix: number;
  px: number;
  py: number;
}

const IDLE_READOUT =
  'Hover the field, or focus it and use the arrow keys, to read values.';

export function HeatmapCanvas({
  heatmap,
  field,
}: {
  heatmap: Heatmap;
  field: HeatField;
}): JSX.Element {
  const wrapRef = useRef<HTMLDivElement>(null);
  const baseRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const [cssW, setCssW] = useState(920);
  const [cursor, setCursor] = useState<Cursor | null>(null);
  const [focused, setFocused] = useState(false);
  const theme = useResolvedTheme();

  const cssH = heatmapHeight(cssW);
  const plotW = Math.max(1, cssW - MARGIN.l - MARGIN.r);
  const plotH = Math.max(1, cssH - MARGIN.t - MARGIN.b);

  const nt = heatmap.values.length;
  const nx = nt > 0 ? heatmap.values[0].length : 0;
  const { t0, t1, x0, x1 } = useMemo(() => heatmapExtent(heatmap), [heatmap]);
  const xSpan = x1 - x0;

  /* follow the container's width (sidebar collapse included) */
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = (): void => {
      const w = el.clientWidth;
      if (w > 100) setCssW(Math.round(w));
    };
    measure();
    if (typeof ResizeObserver === 'function') {
      const ro = new ResizeObserver(measure);
      ro.observe(el);
      return () => ro.disconnect();
    }
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, []);

  /* a new field or a new run starts uninspected */
  useEffect(() => setCursor(null), [heatmap, field]);

  /* paint the base layer: surface, bins, null hatch, frame, axes */
  useEffect(() => {
    const canvas = baseRef.current;
    if (!canvas || nt === 0 || nx === 0) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return; // jsdom / test environments
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    ctx.fillStyle = readToken('--viz-surface');
    ctx.fillRect(0, 0, cssW, cssH);

    // --- bins at native resolution (null bins get a placeholder) ---
    const off = document.createElement('canvas');
    off.width = nt;
    off.height = nx;
    const offCtx = off.getContext('2d');
    if (!offCtx) return;
    const img = offCtx.createImageData(nt, nx);
    for (let iy = 0; iy < nx; iy++) {
      const ix = nx - 1 - iy; // position increases upward
      for (let it = 0; it < nt; it++) {
        const rgb = binColor(field, heatmap.values[it][ix]);
        const p = (iy * nt + it) * 4;
        img.data[p] = rgb[0];
        img.data[p + 1] = rgb[1];
        img.data[p + 2] = rgb[2];
        img.data[p + 3] = 255;
      }
    }
    offCtx.putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(off, 0, 0, nt, nx, MARGIN.l, MARGIN.t, plotW, plotH);
    ctx.imageSmoothingEnabled = true;

    // --- null bins: themed background + 45° hatch, one path of column runs ---
    const tile = document.createElement('canvas');
    tile.width = HATCH_PX;
    tile.height = HATCH_PX;
    const tctx = tile.getContext('2d');
    const pattern = (() => {
      if (!tctx) return null;
      tctx.fillStyle = readToken('--viz-null-bg');
      tctx.fillRect(0, 0, HATCH_PX, HATCH_PX);
      tctx.strokeStyle = readToken('--viz-null-hatch');
      tctx.lineWidth = 1;
      tctx.beginPath();
      // the diagonal plus its two corner stubs, so tiles join seamlessly
      tctx.moveTo(0, HATCH_PX);
      tctx.lineTo(HATCH_PX, 0);
      tctx.moveTo(-1, 1);
      tctx.lineTo(1, -1);
      tctx.moveTo(HATCH_PX - 1, HATCH_PX + 1);
      tctx.lineTo(HATCH_PX + 1, HATCH_PX - 1);
      tctx.stroke();
      return ctx.createPattern(tile, 'repeat');
    })();
    if (pattern) {
      const bw = plotW / nt;
      const bh = plotH / nx;
      const path = new Path2D();
      let any = false;
      for (let it = 0; it < nt; it++) {
        const col = heatmap.values[it];
        let ix = 0;
        while (ix < nx) {
          if (col[ix] !== null && Number.isFinite(col[ix])) {
            ix++;
            continue;
          }
          const start = ix;
          while (ix < nx && (col[ix] === null || !Number.isFinite(col[ix] as number))) ix++;
          // rows [start, ix) in position order; position increases upward
          path.rect(MARGIN.l + it * bw, MARGIN.t + plotH - ix * bh, bw, (ix - start) * bh);
          any = true;
        }
      }
      if (any) {
        ctx.fillStyle = pattern;
        ctx.fill(path);
      }
    }

    // --- frame (no gridlines over the field: they would hide the data) ---
    ctx.strokeStyle = readToken('--viz-frame');
    ctx.lineWidth = 1;
    ctx.strokeRect(MARGIN.l - 0.5, MARGIN.t - 0.5, plotW + 1, plotH + 1);

    // --- ticks and tick labels ---
    const pxT = (t: number): number => MARGIN.l + ((t - t0) / (t1 - t0)) * plotW;
    const pyX = (x: number): number => MARGIN.t + (1 - (x - x0) / (x1 - x0)) * plotH;
    const axis = readToken('--viz-axis');
    const tickText = readToken('--viz-tick-text');
    ctx.font = `11px ${readToken('--font-mono') || 'monospace'}`;

    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (const t of timeTicks(t0, t1, Math.max(3, Math.floor(plotW / 110)))) {
      const px = Math.round(pxT(t)) + 0.5;
      ctx.strokeStyle = axis;
      ctx.beginPath();
      ctx.moveTo(px, MARGIN.t + plotH);
      ctx.lineTo(px, MARGIN.t + plotH + TICK_LEN);
      ctx.stroke();
      ctx.fillStyle = tickText;
      ctx.fillText(formatTickMin(t), px, MARGIN.t + plotH + TICK_LEN + 3);
    }
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (const x of spaceTicks(x0, x1, Math.max(3, Math.floor(plotH / 70)))) {
      const py = Math.round(pyX(x)) + 0.5;
      ctx.strokeStyle = axis;
      ctx.beginPath();
      ctx.moveTo(MARGIN.l - TICK_LEN, py);
      ctx.lineTo(MARGIN.l, py);
      ctx.stroke();
      ctx.fillStyle = tickText;
      ctx.fillText(formatTickDist(x, xSpan), MARGIN.l - TICK_LEN - 4, py);
    }

    // --- axis titles: sentence case, with units ---
    ctx.fillStyle = readToken('--text-secondary');
    ctx.font = `500 12px ${readToken('--font-sans') || 'sans-serif'}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    ctx.fillText('Time (min)', MARGIN.l + plotW / 2, cssH - 4);
    ctx.save();
    ctx.translate(14, MARGIN.t + plotH / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textBaseline = 'middle';
    ctx.fillText(`Position (${distUnit(xSpan)})`, 0, 0);
    ctx.restore();
  }, [heatmap, field, theme, cssW, cssH, plotW, plotH, nt, nx, t0, t1, x0, x1, xSpan]);

  /* crosshair overlay: solid halo then line, never dashed */
  useEffect(() => {
    const canvas = overlayRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    if (!cursor) return;
    const halo = readToken('--viz-crosshair-halo');
    const line = readToken('--viz-crosshair');
    const x = Math.round(cursor.px) + 0.5;
    const y = Math.round(cursor.py) + 0.5;
    const strokeCross = (): void => {
      ctx.beginPath();
      ctx.moveTo(x, MARGIN.t);
      ctx.lineTo(x, MARGIN.t + plotH);
      ctx.moveTo(MARGIN.l, y);
      ctx.lineTo(MARGIN.l + plotW, y);
      ctx.stroke();
    };
    ctx.strokeStyle = halo;
    ctx.lineWidth = 3;
    strokeCross();
    ctx.strokeStyle = line;
    ctx.globalAlpha = 0.9;
    ctx.lineWidth = 1;
    strokeCross();
    ctx.globalAlpha = 1;
    // 6 px center dot with a 2 px halo
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(cursor.px, cursor.py, 5, 0, 2 * Math.PI);
    ctx.fill();
    ctx.fillStyle = line;
    ctx.beginPath();
    ctx.arc(cursor.px, cursor.py, 3, 0, 2 * Math.PI);
    ctx.fill();
  }, [cursor, theme, cssW, cssH, plotW, plotH]);

  /** The crosshair at a bin's center (keyboard). */
  const atBin = useCallback(
    (it: number, ix: number): Cursor => ({
      it,
      ix,
      px: MARGIN.l + ((it + 0.5) / nt) * plotW,
      py: MARGIN.t + (1 - (ix + 0.5) / nx) * plotH,
    }),
    [nt, nx, plotW, plotH],
  );

  const onMove = useCallback(
    (e: ReactMouseEvent<HTMLDivElement>): void => {
      const rect = e.currentTarget.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      if (
        nt === 0 ||
        nx === 0 ||
        mx < MARGIN.l ||
        mx > MARGIN.l + plotW ||
        my < MARGIN.t ||
        my > MARGIN.t + plotH
      ) {
        if (!focused) setCursor(null);
        return;
      }
      const it = Math.min(nt - 1, Math.max(0, Math.floor(((mx - MARGIN.l) / plotW) * nt)));
      const ix = Math.min(nx - 1, Math.max(0, Math.floor((1 - (my - MARGIN.t) / plotH) * nx)));
      setCursor({ it, ix, px: mx, py: my });
    },
    [nt, nx, plotW, plotH, focused],
  );

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (nt === 0 || nx === 0) return;
    const step = e.shiftKey ? 10 : 1;
    const cur = cursor ?? atBin(Math.floor(nt / 2), Math.floor(nx / 2));
    let { it, ix } = cur;
    switch (e.key) {
      case 'ArrowLeft':
        it -= step;
        break;
      case 'ArrowRight':
        it += step;
        break;
      case 'ArrowUp': // downstream is up
        ix += step;
        break;
      case 'ArrowDown':
        ix -= step;
        break;
      case 'Home':
        it = 0;
        break;
      case 'End':
        it = nt - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    setCursor(atBin(Math.min(nt - 1, Math.max(0, it)), Math.min(nx - 1, Math.max(0, ix))));
  };

  const value = cursor ? (heatmap.values[cursor.it]?.[cursor.ix] ?? null) : null;
  const readout = cursor
    ? `t ${formatTimeMin(heatmap.t_bins[cursor.it])} · x ${formatDistAdaptive(
        heatmap.x_bins[cursor.ix],
        xSpan,
      )} · ${formatBinValue(field, value)}`
    : null;

  const unit = distUnit(xSpan);
  const ariaLabel =
    `Space–time ${field} field, ${formatTickMin(t0)}–${formatTickMin(t1)} min, ` +
    `${formatTickDist(x0, xSpan)}–${formatTickDist(x1, xSpan)} ${unit}. ` +
    `Colour scale ${LEGENDS[field].range}.`;

  return (
    <div className="heatmap">
      <div
        ref={wrapRef}
        className="heatmap-box"
        tabIndex={0}
        role="img"
        aria-label={ariaLabel}
        onMouseMove={onMove}
        onMouseLeave={() => {
          if (!focused) setCursor(null);
        }}
        onKeyDown={onKeyDown}
        onFocus={() => {
          setFocused(true);
          setCursor((c) => c ?? (nt > 0 && nx > 0 ? atBin(Math.floor(nt / 2), Math.floor(nx / 2)) : null));
        }}
        onBlur={() => {
          setFocused(false);
          setCursor(null);
        }}
      >
        <canvas ref={baseRef} style={{ height: cssH }} />
        <canvas ref={overlayRef} className="heatmap-overlay" style={{ height: cssH }} />
        {/* the focus ring, drawn around the plot frame rather than the axes */}
        <span
          className="heatmap-focus"
          aria-hidden="true"
          style={{ left: MARGIN.l, top: MARGIN.t, width: plotW, height: plotH }}
        />
      </div>
      <RampLegend field={field} />
      <div className={`heatmap-readout${readout ? '' : ' idle'}`} aria-live={focused ? 'polite' : 'off'}>
        {readout ?? IDLE_READOUT}
      </div>
      <p className="heatmap-caption">
        Downstream is up. Waves travelling upstream slope down to the right.
      </p>
    </div>
  );
}
