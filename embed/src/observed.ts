/** The observed I-24 westbound day as a space-time speed heatmap with hover readout. */
import { buildLUT, EMPTY_BIN } from './colormap';
import type { ObservedField } from './data';

const M = { l: 44, r: 10, t: 8, b: 22 };
const V_TOP_KMH = 110;
const INK = '#8b8a85';
const CST_OFFSET_H = -6; // 30 Nov 2022 is standard time in Nashville

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

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly hover: HTMLElement,
  ) {
    canvas.addEventListener('pointermove', (e) => this.onHover(e));
    canvas.addEventListener('pointerleave', () => {
      this.hover.textContent = '';
    });
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
          data.data[p] = EMPTY_BIN[0];
          data.data[p + 1] = EMPTY_BIN[1];
          data.data[p + 2] = EMPTY_BIN[2];
        } else {
          const k = Math.min(255, Math.max(0, Math.round((v / V_TOP_KMH) * 255)));
          data.data[p] = this.lut[3 * k];
          data.data[p + 1] = this.lut[3 * k + 1];
          data.data[p + 2] = this.lut[3 * k + 2];
        }
        data.data[p + 3] = 255;
      }
    }
    ctx.putImageData(data, 0, 0);
    this.img = img;
    this.draw();
  }

  draw(): void {
    const f = this.field;
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    if (!f || !this.img || w === 0 || h === 0) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    const ctx = this.canvas.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.fillStyle = '#0b0d11';
    ctx.fillRect(0, 0, w, h);
    const pw = w - M.l - M.r;
    const ph = h - M.t - M.b;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(this.img, M.l, M.t, pw, ph);

    const t0 = f.t_edges_s[0];
    const t1 = f.t_edges_s[f.t_edges_s.length - 1];
    const x1 = f.x_edges_m[f.x_edges_m.length - 1];
    ctx.fillStyle = INK;
    ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (let t = t0; t <= t1; t += 1800) {
      const x = M.l + ((t - t0) / (t1 - t0)) * pw;
      ctx.fillText(clockLabel(f.t_origin_unix, t).slice(0, 5), x, M.t + ph + 4);
    }
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let km = 0; km * 1000 <= x1; km += 1) {
      const y = M.t + (1 - (km * 1000) / x1) * ph;
      ctx.fillText(`${km} km`, M.l - 4, y);
    }
    ctx.save();
    ctx.translate(10, M.t + ph / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = 'center';
    ctx.fillText('direction of travel →', 0, 0);
    ctx.restore();

    const lw = 120;
    const lx = w - M.r - lw;
    const ly = 2;
    const grad = ctx.createLinearGradient(lx, 0, lx + lw, 0);
    for (let i = 0; i <= 10; i++) {
      const k = Math.round((i / 10) * 255);
      grad.addColorStop(i / 10, `rgb(${this.lut[3 * k]},${this.lut[3 * k + 1]},${this.lut[3 * k + 2]})`);
    }
    ctx.fillStyle = grad;
    ctx.fillRect(lx, ly, lw, 4);
  }

  private onHover(e: PointerEvent): void {
    const f = this.field;
    if (!f) return;
    const r = this.canvas.getBoundingClientRect();
    const pw = r.width - M.l - M.r;
    const ph = r.height - M.t - M.b;
    const u = (e.clientX - r.left - M.l) / pw;
    const q = 1 - (e.clientY - r.top - M.t) / ph;
    if (u < 0 || u > 1 || q < 0 || q > 1) {
      this.hover.textContent = '';
      return;
    }
    const nt = f.mean_speed_kmh.length;
    const nx = f.mean_speed_kmh[0].length;
    const it = Math.min(nt - 1, Math.floor(u * nt));
    const ix = Math.min(nx - 1, Math.floor(q * nx));
    const t = f.t_edges_s[0] + (it + 0.5) * f.dt_s;
    const x = f.x_edges_m[0] + (ix + 0.5) * f.dx_m;
    const v = f.mean_speed_kmh[it][ix];
    this.hover.textContent = `${clockLabel(f.t_origin_unix, t)} · ${(x / 1000).toFixed(1)} km · ${
      v === null ? 'no tracked vehicle' : `${v.toFixed(0)} km/h`
    }`;
  }
}
