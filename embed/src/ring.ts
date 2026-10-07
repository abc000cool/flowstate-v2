/** Top-down ring view. Traffic runs clockwise; a stop-and-go wave drifts the other way. */
import { speedColor } from './colormap';
import type { VizTokens } from './theme';

export interface RingOpts {
  circumference: number;
  vehicleLength: number;
  avIndex: ReadonlySet<number>;
  /** Top of the colour scale [m/s]. */
  vTop: number;
  controllerOn: boolean;
  tokens: VizTokens;
}

function angleOf(x: number, C: number): number {
  return -Math.PI / 2 + (x / C) * 2 * Math.PI;
}

export function drawRing(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  xs: Float32Array,
  vs: Float32Array,
  o: RingOpts,
): void {
  const tk = o.tokens;
  ctx.clearRect(0, 0, w, h);
  const cx = w / 2;
  const cy = h / 2;
  const R = Math.min(w, h) * 0.39;
  const scale = R / (o.circumference / (2 * Math.PI)); // px per metre
  const laneW = Math.max(9, 3.6 * scale);
  const len = Math.max(7, o.vehicleLength * scale);
  const wid = Math.max(5, 2.0 * scale);

  // the road: a themed band with hairline edges
  ctx.lineWidth = laneW;
  ctx.strokeStyle = tk.track;
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, 2 * Math.PI);
  ctx.stroke();
  ctx.lineWidth = 1;
  ctx.strokeStyle = tk.trackEdge;
  for (const r of [R - laneW / 2, R + laneW / 2]) {
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, 2 * Math.PI);
    ctx.stroke();
  }

  // direction of travel: a small arrowhead on the outer edge at 12 o'clock
  const ay = cy - R - laneW / 2 - 9;
  ctx.fillStyle = tk.tick;
  ctx.beginPath();
  ctx.moveTo(cx + 5, ay);
  ctx.lineTo(cx - 3, ay - 4);
  ctx.lineTo(cx - 3, ay + 4);
  ctx.closePath();
  ctx.fill();

  const fs = Math.max(11, Math.round(0.034 * Math.min(w, h)));
  ctx.fillStyle = tk.ink;
  ctx.font = `600 ${fs + 1}px ${tk.fontSans}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(`${xs.length} vehicles`, cx, cy - 0.11 * R);
  ctx.fillStyle = tk.tick;
  ctx.font = `400 ${fs}px ${tk.fontSans}`;
  ctx.fillText(`${o.circumference.toFixed(0)} m ring, clockwise`, cx, cy + 0.04 * R);
  ctx.fillText('waves drift anticlockwise', cx, cy + 0.04 * R + fs * 1.45);

  for (let j = 0; j < xs.length; j++) {
    const a = angleOf(xs[j], o.circumference);
    const px = cx + R * Math.cos(a);
    const py = cy + R * Math.sin(a);
    ctx.save();
    ctx.translate(px, py);
    ctx.rotate(a + Math.PI / 2);
    // SUMO positions are the front bumper: the body trails behind it
    ctx.beginPath();
    roundRect(ctx, -len, -wid / 2, len, wid, Math.min(2.5, wid / 2));
    ctx.fillStyle = speedColor(vs[j], o.vTop);
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = tk.carOutline;
    ctx.stroke();
    if (o.avIndex.has(j)) {
      ctx.lineWidth = 2;
      ctx.strokeStyle = tk.ink;
      if (!o.controllerOn) ctx.setLineDash([3, 2]);
      ctx.beginPath();
      roundRect(ctx, -len - 3, -wid / 2 - 3, len + 6, wid + 6, Math.min(4, wid / 2 + 3));
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.restore();
  }
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}
