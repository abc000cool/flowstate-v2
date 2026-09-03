/** Per-minute speed spread: this run (filled) against its uncontrolled baseline (outlined). */
import type { RunRecord } from './data';

const INK = '#8b8a85';

export function drawStrip(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  rec: RunRecord,
  base: RunRecord | undefined,
  minuteNow: number,
): void {
  ctx.clearRect(0, 0, w, h);
  const ml = 30;
  const mb = 16;
  const mt = 6;
  const pw = w - ml - 8;
  const ph = h - mt - mb;
  const a = rec.sigma_v_per_minute_ms;
  const b = base?.sigma_v_per_minute_ms ?? [];
  const n = Math.max(a.length, b.length, 1);
  const ymax = Math.max(0.5, ...a, ...b) * 1.1;
  const bw = pw / n;
  const y = (v: number): number => mt + ph - (v / ymax) * ph;

  ctx.strokeStyle = '#1c1f26';
  ctx.beginPath();
  ctx.moveTo(ml, y(0));
  ctx.lineTo(ml + pw, y(0));
  ctx.stroke();

  for (let i = 0; i < n; i++) {
    const x0 = ml + i * bw + 2;
    const wb = Math.max(1, bw - 4);
    if (b[i] !== undefined) {
      ctx.strokeStyle = '#eb6834';
      ctx.lineWidth = 1;
      ctx.strokeRect(x0 + 0.5, y(b[i]) + 0.5, wb - 1, y(0) - y(b[i]) - 1);
    }
    if (a[i] !== undefined) {
      ctx.fillStyle = i === minuteNow ? '#2a78d6' : 'rgba(42,120,214,0.55)';
      ctx.fillRect(x0, y(a[i]), wb, y(0) - y(a[i]));
    }
  }

  ctx.fillStyle = INK;
  ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  ctx.fillText(ymax.toFixed(1), ml - 4, y(ymax / 1.1));
  ctx.fillText('0', ml - 4, y(0));
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (let i = 0; i < n; i += 2) ctx.fillText(`${i}`, ml + (i + 0.5) * bw, y(0) + 3);
  ctx.textAlign = 'left';
  ctx.fillText('σv per minute [m/s]  ■ this run  □ baseline', ml, mt - 4 < 0 ? 0 : mt - 4);
}
