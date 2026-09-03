/** Top-down ring view. Traffic runs clockwise; the wave you see runs the other way. */
import { speedColor, speedRGB } from './colormap';

export interface RingOpts {
  circumference: number;
  vehicleLength: number;
  avIndex: ReadonlySet<number>;
  vRef: number;
  controllerOn: boolean;
}

const TRACK = '#15181e';
const TRACK_EDGE = '#242833';
const INK = '#8b8a85';
const AV = '#ffffff';

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
  ctx.clearRect(0, 0, w, h);
  const cx = w / 2;
  const cy = h / 2;
  const R = Math.min(w, h) * 0.4;
  const scale = R / (o.circumference / (2 * Math.PI)); // px per metre
  const laneW = Math.max(6, 3.4 * scale);
  const len = Math.max(6, o.vehicleLength * scale);
  const wid = Math.max(3.5, 1.9 * scale);

  ctx.lineWidth = laneW + 2;
  ctx.strokeStyle = TRACK_EDGE;
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, 2 * Math.PI);
  ctx.stroke();
  ctx.lineWidth = laneW;
  ctx.strokeStyle = TRACK;
  ctx.stroke();

  // Direction hint.
  ctx.fillStyle = INK;
  ctx.font = `${Math.max(10, 0.032 * Math.min(w, h))}px system-ui, sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('traffic ↻   wave ↺', cx, cy - 0.12 * R);
  ctx.fillText(`${xs.length} vehicles · ${o.circumference.toFixed(0)} m`, cx, cy + 0.12 * R);

  for (let j = 0; j < xs.length; j++) {
    const a = angleOf(xs[j], o.circumference);
    const px = cx + R * Math.cos(a);
    const py = cy + R * Math.sin(a);
    ctx.save();
    ctx.translate(px, py);
    ctx.rotate(a + Math.PI / 2);
    ctx.fillStyle = speedColor(vs[j], o.vRef);
    ctx.beginPath();
    roundRect(ctx, -len / 2, -wid / 2, len, wid, Math.min(2.5, wid / 2));
    ctx.fill();
    if (o.avIndex.has(j)) {
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = AV;
      if (o.controllerOn) {
        ctx.shadowColor = AV;
        ctx.shadowBlur = 10;
      } else {
        ctx.setLineDash([2, 2]);
      }
      ctx.beginPath();
      roundRect(ctx, -len / 2 - 2, -wid / 2 - 2, len + 4, wid + 4, Math.min(3.5, wid / 2 + 2));
      ctx.stroke();
    }
    ctx.restore();
  }

  // Legend.
  const lw = Math.min(140, 0.36 * w);
  const lx = 12;
  const ly = h - 22;
  const grad = ctx.createLinearGradient(lx, 0, lx + lw, 0);
  for (let i = 0; i <= 10; i++) {
    const [r, g, b] = speedRGB(i / 10);
    grad.addColorStop(i / 10, `rgb(${r},${g},${b})`);
  }
  ctx.fillStyle = grad;
  ctx.fillRect(lx, ly, lw, 6);
  ctx.fillStyle = INK;
  ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillText('0', lx, ly + 8);
  ctx.textAlign = 'right';
  ctx.fillText(`${o.vRef.toFixed(1)} m/s`, lx + lw, ly + 8);
  if (o.avIndex.size > 0) {
    ctx.textAlign = 'right';
    ctx.fillStyle = AV;
    ctx.fillText(o.controllerOn ? '● controlled vehicle: ON' : '○ controlled vehicle: waiting', w - 12, ly + 8);
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
