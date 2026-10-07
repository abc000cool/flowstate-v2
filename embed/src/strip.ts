/**
 * Speed spread σv per minute: this run (filled bars) against the uncontrolled run with the
 * same ring and seed (outlined bars). Values come from the data pack's per-minute record.
 */
import type { RunRecord } from './data';
import type { VizTokens } from './theme';
import { msToKmh, niceTicks } from './units';

export function drawStrip(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  rec: RunRecord,
  base: RunRecord | undefined,
  tNow: number,
  minutes: number,
  tk: VizTokens,
): void {
  ctx.clearRect(0, 0, w, h);
  const ml = 34;
  const mr = 8;
  const mt = 8;
  const mb = 18;
  const pw = Math.max(1, w - ml - mr);
  const ph = Math.max(1, h - mt - mb);
  // whole minutes of the run only: a trailing bin holding the final sample is not a minute
  const a = rec.sigma_v_per_minute_ms.slice(0, minutes).map(msToKmh);
  const showBase = rec.n_av > 0 && base !== undefined;
  const b = showBase ? base.sigma_v_per_minute_ms.slice(0, minutes).map(msToKmh) : [];
  const n = Math.max(a.length, b.length, 1);
  const top = Math.max(1, ...a, ...b);
  const ticks = niceTicks(top, 3);
  const ymax = Math.max(top, ticks[ticks.length - 1]) * 1.04;
  const bw = pw / n;
  const y = (v: number): number => mt + ph - (v / ymax) * ph;

  // horizontal gridlines only
  ctx.font = `400 11px ${tk.fontMono}`;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  ctx.lineWidth = 1;
  for (const v of ticks) {
    const yy = Math.round(y(v)) + 0.5;
    ctx.strokeStyle = v === 0 ? tk.axis : tk.grid;
    ctx.beginPath();
    ctx.moveTo(ml, yy);
    ctx.lineTo(ml + pw, yy);
    ctx.stroke();
    ctx.fillStyle = tk.tick;
    ctx.fillText(`${v}`, ml - 6, yy);
  }

  const fill = rec.n_av > 0 ? tk.series1 : tk.baseline;
  for (let i = 0; i < n; i++) {
    const x0 = ml + i * bw + Math.min(3, bw * 0.15);
    const wb = Math.max(1, bw - 2 * Math.min(3, bw * 0.15));
    if (a[i] !== undefined) {
      ctx.fillStyle = fill;
      ctx.fillRect(x0, y(a[i]), wb, y(0) - y(a[i]));
    }
    if (b[i] !== undefined) {
      ctx.strokeStyle = tk.baseline;
      ctx.lineWidth = 1.5;
      ctx.strokeRect(x0 + 0.75, y(b[i]) + 0.75, wb - 1.5, Math.max(0, y(0) - y(b[i]) - 1.5));
    }
  }

  ctx.fillStyle = tk.tick;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const every = pw / n < 22 ? 2 : 1;
  for (let i = 0; i <= n; i += every) ctx.fillText(`${i}`, ml + i * bw, y(0) + 4);

  // the playhead, in minutes
  const xc = Math.round(ml + Math.min(n, Math.max(0, tNow / 60)) * bw) + 0.5;
  ctx.lineWidth = 3;
  ctx.strokeStyle = tk.halo;
  ctx.beginPath();
  ctx.moveTo(xc, mt);
  ctx.lineTo(xc, mt + ph);
  ctx.stroke();
  ctx.lineWidth = 1;
  ctx.strokeStyle = tk.crosshair;
  ctx.stroke();
}
