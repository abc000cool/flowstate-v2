/**
 * The observed I-24 westbound day as a space-time mean-speed field (absolute §7.1 scale,
 * 0–120 km/h). Bins with no tracked vehicle are hatched so they cannot be read as stopped
 * traffic. Hover, or focus the plot and use the arrow keys, to read a bin.
 */
import { buildLUT, NULL_BIN_RGB, SPEED_DOMAIN_MAX_KMH } from './colormap';
import type { ObservedField } from './data';
import { hatchPattern, readVizTokens } from './theme';
import { formatKmhValue } from './units';

const M = { l: 50, r: 12, t: 8, b: 36 };
const CST_OFFSET_H = -6; // 30 Nov 2022 is standard time in Nashville
const IDLE = 'Hover the field, or focus it and use the arrow keys, to read values.';

export function clockLabel(tOriginUnix: number, tS: number): string {
  const d = new Date((tOriginUnix + tS) * 1000);
  const hh = (d.getUTCHours() + CST_OFFSET_H + 24) % 24;
  const mm = d.getUTCMinutes();
  return `${hh.toString().padStart(2, '0')}:${mm.toString().padStart(2, '0')} CST`;
}

export class ObservedView {
  private field: ObservedField | null = null;
  private img: HTMLCanvasElement | null = null;
  private readonly lut = buildLUT();
  /** Selected bin (time index, position index), or null when idle. */
  private cur: { it: number; ix: number } | null = null;
  private focused = false;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly readout: HTMLElement,
  ) {
    this.readout.textContent = IDLE;
    canvas.addEventListener('pointermove', (e) => this.onPointer(e));
    canvas.addEventListener('pointerleave', () => {
      if (!this.focused) this.setCur(null);
    });
    canvas.addEventListener('focus', () => {
      this.focused = true;
      this.readout.setAttribute('aria-live', 'polite');
      if (!this.cur && this.field) {
        const nt = this.field.mean_speed_kmh.length;
        const nx = nt > 0 ? this.field.mean_speed_kmh[0].length : 0;
        this.setCur({ it: Math.floor(nt / 2), ix: Math.floor(nx / 2) });
      }
    });
    canvas.addEventListener('blur', () => {
      this.focused = false;
      this.readout.removeAttribute('aria-live');
      this.setCur(null);
    });
    canvas.addEventListener('keydown', (e) => this.onKey(e));
  }

  setField(f: ObservedField): void {
    this.field = f;
    const nt = f.mean_speed_kmh.length;
    const nx = nt > 0 ? f.mean_speed_kmh[0].length : 0;
    const img = document.createElement('canvas');
    img.width = nt;
    img.height = nx;
    const ctx = img.getContext('2d');
    if (!ctx) return;
    const data = ctx.createImageData(nt, nx);
    for (let it = 0; it < nt; it++) {
      for (let ix = 0; ix < nx; ix++) {
        const v = f.mean_speed_kmh[it][ix];
        const row = nx - 1 - ix; // x increases upward
        const p = 4 * (row * nt + it);
        if (v === null) {
          data.data[p] = NULL_BIN_RGB[0];
          data.data[p + 1] = NULL_BIN_RGB[1];
          data.data[p + 2] = NULL_BIN_RGB[2];
        } else {
          const k = Math.min(255, Math.max(0, Math.round((v / SPEED_DOMAIN_MAX_KMH) * 255)));
          data.data[p] = this.lut[3 * k];
          data.data[p + 1] = this.lut[3 * k + 1];
          data.data[p + 2] = this.lut[3 * k + 2];
        }
        data.data[p + 3] = 255;
      }
    }
    ctx.putImageData(data, 0, 0);
    this.img = img;
    const t0 = f.t_edges_s[0];
    const t1 = f.t_edges_s[f.t_edges_s.length - 1];
    const x1 = f.x_edges_m[f.x_edges_m.length - 1];
    this.canvas.setAttribute(
      'aria-label',
      `Space-time mean-speed field, I-24 westbound, ${clockLabel(f.t_origin_unix, t0)} to ${clockLabel(f.t_origin_unix, t1)}, ` +
        `0 to ${(x1 / 1000).toFixed(1)} km. Colour scale 0 to ${SPEED_DOMAIN_MAX_KMH} km/h; hatched bins have no tracked vehicle.`,
    );
    this.draw();
  }

  draw(): void {
    const f = this.field;
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    if (!f || !this.img || w === 0 || h === 0) return;
    const tk = readVizTokens();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    const ctx = this.canvas.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.fillStyle = tk.surface;
    ctx.fillRect(0, 0, w, h);
    const pw = Math.max(1, w - M.l - M.r);
    const ph = Math.max(1, h - M.t - M.b);
    const nt = f.mean_speed_kmh.length;
    const nx = nt > 0 ? f.mean_speed_kmh[0].length : 0;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(this.img, M.l, M.t, pw, ph);

    // hatch every no-data bin with the themed pattern
    const pat = hatchPattern(ctx, tk.nullBg, tk.nullHatch);
    if (pat) {
      ctx.fillStyle = pat;
      const bw = pw / nt;
      const bh = ph / nx;
      for (let it = 0; it < nt; it++) {
        for (let ix = 0; ix < nx; ix++) {
          if (f.mean_speed_kmh[it][ix] !== null) continue;
          const x0 = Math.floor(M.l + it * bw);
          const y0 = Math.floor(M.t + (nx - 1 - ix) * bh);
          ctx.fillRect(x0, y0, Math.ceil(M.l + (it + 1) * bw) - x0, Math.ceil(M.t + (nx - ix) * bh) - y0);
        }
      }
    }

    ctx.strokeStyle = tk.frame;
    ctx.lineWidth = 1;
    ctx.strokeRect(M.l - 0.5, M.t - 0.5, pw + 1, ph + 1);

    const t0 = f.t_edges_s[0];
    const t1 = f.t_edges_s[f.t_edges_s.length - 1];
    const x1 = f.x_edges_m[f.x_edges_m.length - 1];
    ctx.strokeStyle = tk.axis;
    ctx.fillStyle = tk.tick;
    ctx.font = `400 11px ${tk.fontMono}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const tStep = pw < 420 ? 3600 : 1800;
    for (let t = t0; t <= t1 + 1e-6; t += tStep) {
      const x = Math.round(M.l + ((t - t0) / (t1 - t0)) * pw) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, M.t + ph);
      ctx.lineTo(x, M.t + ph + 4);
      ctx.stroke();
      const label = clockLabel(f.t_origin_unix, t).slice(0, 5);
      const half = ctx.measureText(label).width / 2;
      ctx.fillText(label, Math.min(w - 1 - half, Math.max(half + 1, x)), M.t + ph + 6);
    }
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let km = 0; km * 1000 <= x1 + 1e-6; km += 1) {
      const y = Math.round(M.t + (1 - (km * 1000) / x1) * ph) + 0.5;
      ctx.beginPath();
      ctx.moveTo(M.l - 4, y);
      ctx.lineTo(M.l, y);
      ctx.stroke();
      ctx.fillText(`${km}`, M.l - 6, y);
    }
    ctx.fillStyle = tk.inkSecondary;
    ctx.font = `500 12px ${tk.fontSans}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    ctx.fillText('Time (CST)', M.l + pw / 2, h - 1);
    ctx.save();
    ctx.translate(13, M.t + ph / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textBaseline = 'middle';
    ctx.fillText('Position (km)', 0, 0);
    ctx.restore();

    if (this.cur) {
      const cx = M.l + ((this.cur.it + 0.5) / nt) * pw;
      const cy = M.t + (1 - (this.cur.ix + 0.5) / nx) * ph;
      ctx.save();
      ctx.beginPath();
      ctx.rect(M.l, M.t, pw, ph);
      ctx.clip();
      ctx.lineWidth = 3;
      ctx.strokeStyle = tk.halo;
      ctx.beginPath();
      ctx.moveTo(cx, M.t);
      ctx.lineTo(cx, M.t + ph);
      ctx.moveTo(M.l, cy);
      ctx.lineTo(M.l + pw, cy);
      ctx.stroke();
      ctx.globalAlpha = 0.9;
      ctx.lineWidth = 1;
      ctx.strokeStyle = tk.crosshair;
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.arc(cx, cy, 5, 0, 2 * Math.PI);
      ctx.fillStyle = tk.halo;
      ctx.fill();
      ctx.beginPath();
      ctx.arc(cx, cy, 3, 0, 2 * Math.PI);
      ctx.fillStyle = tk.crosshair;
      ctx.fill();
      ctx.restore();
    }
  }

  private setCur(cur: { it: number; ix: number } | null): void {
    const same = cur && this.cur && cur.it === this.cur.it && cur.ix === this.cur.ix;
    if (same || (!cur && !this.cur)) return;
    this.cur = cur;
    this.renderReadout();
    this.draw();
  }

  private renderReadout(): void {
    const f = this.field;
    if (!f || !this.cur) {
      this.readout.textContent = IDLE;
      this.readout.classList.add('is-idle');
      return;
    }
    const { it, ix } = this.cur;
    const t = f.t_edges_s[0] + (it + 0.5) * f.dt_s;
    const x = f.x_edges_m[0] + (ix + 0.5) * f.dx_m;
    const v = f.mean_speed_kmh[it][ix];
    this.readout.classList.remove('is-idle');
    this.readout.textContent = `${clockLabel(f.t_origin_unix, t)} · x ${(x / 1000).toFixed(2)} km · ${
      v === null ? 'no data (no tracked vehicle)' : formatKmhValue(v)
    }`;
  }

  private onPointer(e: PointerEvent): void {
    const f = this.field;
    if (!f) return;
    const r = this.canvas.getBoundingClientRect();
    const pw = r.width - M.l - M.r;
    const ph = r.height - M.t - M.b;
    const u = (e.clientX - r.left - M.l) / pw;
    const q = 1 - (e.clientY - r.top - M.t) / ph;
    if (u < 0 || u > 1 || q < 0 || q > 1) {
      if (!this.focused) this.setCur(null);
      return;
    }
    const nt = f.mean_speed_kmh.length;
    const nx = f.mean_speed_kmh[0].length;
    this.setCur({ it: Math.min(nt - 1, Math.floor(u * nt)), ix: Math.min(nx - 1, Math.floor(q * nx)) });
  }

  private onKey(e: KeyboardEvent): void {
    const f = this.field;
    if (!f) return;
    const nt = f.mean_speed_kmh.length;
    const nx = f.mean_speed_kmh[0].length;
    const c = this.cur ?? { it: Math.floor(nt / 2), ix: Math.floor(nx / 2) };
    const step = e.shiftKey ? 10 : 1;
    let { it, ix } = c;
    if (e.key === 'ArrowRight') it += step;
    else if (e.key === 'ArrowLeft') it -= step;
    else if (e.key === 'ArrowUp') ix += step;
    else if (e.key === 'ArrowDown') ix -= step;
    else if (e.key === 'Home') it = 0;
    else if (e.key === 'End') it = nt - 1;
    else return;
    e.preventDefault();
    this.setCur({ it: Math.min(nt - 1, Math.max(0, it)), ix: Math.min(nx - 1, Math.max(0, ix)) });
  }
}
