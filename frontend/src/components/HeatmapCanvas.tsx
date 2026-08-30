/** Space-time heatmap renderer (canvas). x-axis = time, y-axis = position
 * (downstream up), colour = speed or density via the ramps in lib/colormap.
 * Bins are painted at native resolution into an offscreen canvas and scaled
 * with nearest-neighbour so wave fronts stay crisp; hover shows a crosshair
 * with a (t, x, value) readout. */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import type { HeatField, Heatmap } from '../api/types';
import {
  binColor,
  domainMaxFor,
  rampGradientCSS,
  stopsFor,
} from '../lib/colormap';
import {
  formatDensityVehKm,
  formatDistAdaptive,
  formatSpeedKmh,
  formatTimeMin,
  MS_TO_KMH,
  M_PER_KM,
  spaceTicks,
  timeTicks,
} from '../lib/format';

const MARGIN = { l: 64, r: 44, t: 14, b: 42 };

interface Hover {
  cssX: number;
  cssY: number;
  t: number;
  x: number;
  value: number | null;
}

export function RampLegend({ field }: { field: HeatField }): JSX.Element {
  const max = domainMaxFor(field);
  const label =
    field === 'speed'
      ? `0 – ${Math.round(max * MS_TO_KMH)} km/h`
      : `0 – ${Math.round(max * M_PER_KM)} veh/km`;
  return (
    <div className="legend">
      <span>{field === 'speed' ? 'v' : 'ρ'}</span>
      <div className="bar" style={{ background: rampGradientCSS(stopsFor(field)) }} />
      <span>{label}</span>
    </div>
  );
}

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
  const [hover, setHover] = useState<Hover | null>(null);

  const cssH = Math.round(Math.min(460, Math.max(300, cssW * 0.42)));
  const plotW = cssW - MARGIN.l - MARGIN.r;
  const plotH = cssH - MARGIN.t - MARGIN.b;

  const nt = heatmap.values.length;
  const nx = nt > 0 ? heatmap.values[0].length : 0;
  const t0 = heatmap.t_edges[0] ?? 0;
  const t1 = heatmap.t_edges[heatmap.t_edges.length - 1] ?? 1;
  const x0 = heatmap.x_edges[0] ?? 0;
  const x1 = heatmap.x_edges[heatmap.x_edges.length - 1] ?? 1;

  /* track container width */
  useEffect(() => {
    const measure = (): void => {
      const w = wrapRef.current?.clientWidth ?? 0;
      if (w > 100) setCssW(w);
    };
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, []);

  /* paint base layer */
  useEffect(() => {
    const canvas = baseRef.current;
    if (!canvas || nt === 0 || nx === 0) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return; // jsdom / test environments
    ctx.scale(dpr, dpr);

    ctx.fillStyle = '#0d1119';
    ctx.fillRect(0, 0, cssW, cssH);

    // --- heatmap bins at native resolution ---
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

    // --- frame ---
    ctx.strokeStyle = '#1b2130';
    ctx.lineWidth = 1;
    ctx.strokeRect(MARGIN.l - 0.5, MARGIN.t - 0.5, plotW + 1, plotH + 1);

    // --- axes ---
    ctx.font = '10px "JetBrains Mono", monospace';
    ctx.fillStyle = '#8a93a6';

    const pxT = (t: number): number => MARGIN.l + ((t - t0) / (t1 - t0)) * plotW;
    const pyX = (x: number): number => MARGIN.t + (1 - (x - x0) / (x1 - x0)) * plotH;

    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (const t of timeTicks(t0, t1, Math.max(3, Math.floor(plotW / 110)))) {
      const px = pxT(t);
      ctx.strokeStyle = '#242b3c';
      ctx.beginPath();
      ctx.moveTo(px + 0.5, MARGIN.t + plotH);
      ctx.lineTo(px + 0.5, MARGIN.t + plotH + 5);
      ctx.stroke();
      ctx.fillText(formatTimeMin(t), px, MARGIN.t + plotH + 9);
    }
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    const xSpan = x1 - x0;
    for (const x of spaceTicks(x0, x1, Math.max(3, Math.floor(plotH / 70)))) {
      const py = pyX(x);
      ctx.strokeStyle = '#242b3c';
      ctx.beginPath();
      ctx.moveTo(MARGIN.l - 5, py + 0.5);
      ctx.lineTo(MARGIN.l, py + 0.5);
      ctx.stroke();
      ctx.fillText(formatDistAdaptive(x, xSpan), MARGIN.l - 9, py);
    }

    // axis titles
    ctx.fillStyle = '#5a6375';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'alphabetic';
    ctx.fillText('TIME →', cssW - MARGIN.r, cssH - 8);
    ctx.save();
    ctx.translate(12, MARGIN.t + 4);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = 'right';
    ctx.fillText('POSITION ↑', 0, 0);
    ctx.restore();
  }, [heatmap, field, cssW, cssH, plotW, plotH, nt, nx, t0, t1, x0, x1]);

  /* crosshair overlay */
  useEffect(() => {
    const canvas = overlayRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);
    if (!hover) return;
    ctx.strokeStyle = 'rgba(79, 209, 224, 0.55)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(hover.cssX + 0.5, MARGIN.t);
    ctx.lineTo(hover.cssX + 0.5, MARGIN.t + plotH);
    ctx.moveTo(MARGIN.l, hover.cssY + 0.5);
    ctx.lineTo(MARGIN.l + plotW, hover.cssY + 0.5);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#4fd1e0';
    ctx.beginPath();
    ctx.arc(hover.cssX, hover.cssY, 2.5, 0, 2 * Math.PI);
    ctx.fill();
  }, [hover, cssW, cssH, plotW, plotH]);

  const onMove = useCallback(
    (e: ReactMouseEvent<HTMLDivElement>): void => {
      const rect = e.currentTarget.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      if (
        mx < MARGIN.l ||
        mx > MARGIN.l + plotW ||
        my < MARGIN.t ||
        my > MARGIN.t + plotH ||
        nt === 0 ||
        nx === 0
      ) {
        setHover(null);
        return;
      }
      const t = t0 + ((mx - MARGIN.l) / plotW) * (t1 - t0);
      const x = x0 + (1 - (my - MARGIN.t) / plotH) * (x1 - x0);
      const it = Math.min(nt - 1, Math.max(0, Math.floor(((t - t0) / (t1 - t0)) * nt)));
      const ix = Math.min(nx - 1, Math.max(0, Math.floor(((x - x0) / (x1 - x0)) * nx)));
      setHover({ cssX: mx, cssY: my, t, x, value: heatmap.values[it][ix] });
    },
    [plotW, plotH, nt, nx, t0, t1, x0, x1, heatmap],
  );

  const valueLabel =
    hover === null
      ? ''
      : hover.value === null
        ? 'no data'
        : field === 'speed'
          ? formatSpeedKmh(hover.value)
          : formatDensityVehKm(hover.value);

  return (
    <div
      ref={wrapRef}
      className="heatmap-box"
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
    >
      <canvas ref={baseRef} style={{ width: cssW, height: cssH }} />
      <canvas ref={overlayRef} className="heatmap-overlay" style={{ width: cssW, height: cssH }} />
      {hover && (
        <div className="hover-readout">
          t {formatTimeMin(hover.t)} · x {formatDistAdaptive(hover.x, x1 - x0)} · {valueLabel}
        </div>
      )}
    </div>
  );
}
