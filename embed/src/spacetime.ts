/**
 * Time-space diagram of one run. The plane is filled with a speed field built from the
 * trajectories: at every 0.5 s sample, each point of the ring takes the speed of the
 * vehicle whose headway it lies in (the space from that vehicle's front to its leader's
 * front). Nothing is interpolated between vehicles. Every trajectory is drawn over the
 * field as a hairline; controlled vehicles in ink.
 */
import { buildLUT } from './colormap';
import type { RunData } from './data';
import { readVizTokens, type VizTokens } from './theme';

const M = { l: 50, r: 12, t: 8, b: 36 };
/** Field rows per metre of ring (0.5 m bins). */
const ROWS_PER_M = 2;

/**
 * Field image: width = samples, height = position bins (row 0 = top = end of the ring).
 * Pixel values are LUT indices into the speed scale on [0, vTop].
 */
export function headwayField(run: RunData, C: number, vTop: number): { w: number; h: number; idx: Uint8Array } {
  const { nVeh, nSamples } = run;
  const H = Math.max(1, Math.round(C * ROWS_PER_M));
  const idx = new Uint8Array(nSamples * H);
  const order = Array.from({ length: nVeh }, (_, j) => j);
  const xs = new Float64Array(nVeh);
  for (let i = 0; i < nSamples; i++) {
    for (let j = 0; j < nVeh; j++) xs[j] = run.x[i * nVeh + j];
    order.sort((a, b) => xs[a] - xs[b]);
    for (let k = 0; k < nVeh; k++) {
      const j = order[k];
      const lead = order[(k + 1) % nVeh];
      const from = xs[j];
      let to = xs[lead];
      if (k === nVeh - 1) to += C; // the last headway wraps past the seam
      const v = run.v[i * nVeh + j];
      const u = vTop > 0 ? Math.min(1, Math.max(0, v / vTop)) : 0;
      const code = Math.round(u * 255);
      const r0 = Math.floor(from * ROWS_PER_M);
      const r1 = Math.ceil(to * ROWS_PER_M);
      for (let r = r0; r < r1; r++) {
        const pos = ((r % H) + H) % H; // position bin, 0 = start of ring
        idx[(H - 1 - pos) * nSamples + i] = code;
      }
    }
  }
  return { w: nSamples, h: H, idx };
}

export class SpaceTimeView {
  private off: HTMLCanvasElement | null = null;
  private field: HTMLCanvasElement | null = null;
  private fieldKey = '';
  private run: RunData | null = null;
  private C = 1;
  private duration = 1;
  private vTop = 1;
  private tk: VizTokens | null = null;
  private readonly lut = buildLUT();

  constructor(private readonly canvas: HTMLCanvasElement) {}

  setRun(run: RunData, circumference: number, duration: number, vTop: number): void {
    this.run = run;
    this.C = circumference;
    this.duration = duration;
    this.vTop = vTop;
    this.render();
  }

  /** Re-render the chrome (size or theme changed). */
  resize(): void {
    this.render();
  }

  private plotBox(): { w: number; h: number; pw: number; ph: number } {
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    return { w, h, pw: Math.max(1, w - M.l - M.r), ph: Math.max(1, h - M.t - M.b) };
  }

  private fieldCanvas(run: RunData): HTMLCanvasElement | null {
    const key = `${run.rec.id}|${this.vTop}|${this.C}`;
    if (this.field && this.fieldKey === key) return this.field;
    const f = headwayField(run, this.C, this.vTop);
    const c = document.createElement('canvas');
    c.width = f.w;
    c.height = f.h;
    const ctx = c.getContext('2d');
    if (!ctx) return null;
    const img = ctx.createImageData(f.w, f.h);
    for (let p = 0; p < f.idx.length; p++) {
      const k = f.idx[p];
      img.data[4 * p] = this.lut[3 * k];
      img.data[4 * p + 1] = this.lut[3 * k + 1];
      img.data[4 * p + 2] = this.lut[3 * k + 2];
      img.data[4 * p + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    this.field = c;
    this.fieldKey = key;
    return c;
  }

  /** Render the whole diagram once into an offscreen canvas (field, hairlines, axes). */
  private render(): void {
    const run = this.run;
    const { w, h, pw, ph } = this.plotBox();
    if (!run || w === 0 || h === 0) return;
    const tk = (this.tk = readVizTokens());
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const off = document.createElement('canvas');
    off.width = Math.round(w * dpr);
    off.height = Math.round(h * dpr);
    const ctx = off.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.fillStyle = tk.surface;
    ctx.fillRect(0, 0, w, h);

    const px = (t: number): number => M.l + (t / this.duration) * pw;
    const py = (x: number): number => M.t + (1 - x / this.C) * ph;
    const { nVeh, nSamples, rec } = run;
    const tEnd = rec.t0_s + (nSamples - 1) * rec.dt_s;

    const field = this.fieldCanvas(run);
    if (field) {
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      // sample i covers [t_i, t_i + dt): place the image on the time axis accordingly
      ctx.drawImage(field, px(rec.t0_s), M.t, px(tEnd + rec.dt_s) - px(rec.t0_s), ph);
    }

    ctx.save();
    ctx.beginPath();
    ctx.rect(M.l, M.t, pw, ph);
    ctx.clip();
    const av = new Set(rec.av_index);
    const trace = (j: number): void => {
      ctx.beginPath();
      let pen = false;
      for (let i = 0; i < nSamples; i++) {
        const x = run.x[i * nVeh + j];
        const t = rec.t0_s + i * rec.dt_s;
        if (i > 0 && Math.abs(x - run.x[(i - 1) * nVeh + j]) > this.C / 2) pen = false; // wrap
        if (pen) ctx.lineTo(px(t), py(x));
        else ctx.moveTo(px(t), py(x));
        pen = true;
      }
      ctx.stroke();
    };
    ctx.lineJoin = 'round';
    ctx.lineWidth = 0.75;
    ctx.strokeStyle = tk.traj;
    for (let j = 0; j < nVeh; j++) if (!av.has(j)) trace(j);
    for (const j of av) {
      ctx.lineWidth = 3;
      ctx.strokeStyle = tk.halo;
      trace(j);
      ctx.lineWidth = 1.25;
      ctx.strokeStyle = tk.crosshair;
      trace(j);
    }

    if (rec.n_av > 0 && rec.activation_s > 0) {
      const xa = Math.round(px(rec.activation_s)) + 0.5;
      ctx.lineWidth = 3;
      ctx.strokeStyle = tk.halo;
      ctx.beginPath();
      ctx.moveTo(xa, M.t);
      ctx.lineTo(xa, M.t + ph);
      ctx.stroke();
      ctx.lineWidth = 1;
      ctx.strokeStyle = tk.crosshair;
      ctx.stroke();
      ctx.font = `500 11px ${tk.fontSans}`;
      const label = 'controller on';
      const tw = ctx.measureText(label).width + 10;
      const lx = xa + 4 + tw > M.l + pw ? xa - tw - 4 : xa + 4;
      ctx.fillStyle = tk.surface;
      ctx.fillRect(lx, M.t + 4, tw, 17);
      ctx.strokeStyle = tk.frame;
      ctx.lineWidth = 1;
      ctx.strokeRect(lx + 0.5, M.t + 4.5, tw - 1, 16);
      ctx.fillStyle = tk.ink;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, lx + 5, M.t + 12.5);
    }
    ctx.restore();

    // frame, ticks, labels (no gridlines over the field)
    ctx.strokeStyle = tk.frame;
    ctx.lineWidth = 1;
    ctx.strokeRect(M.l - 0.5, M.t - 0.5, pw + 1, ph + 1);
    ctx.strokeStyle = tk.axis;
    ctx.fillStyle = tk.tick;
    ctx.font = `400 11px ${tk.fontMono}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const stepMin = pw < 360 ? 2 : 1;
    for (let s = 0; s <= this.duration + 1e-6; s += 60 * stepMin) {
      const x = Math.round(px(s)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, M.t + ph);
      ctx.lineTo(x, M.t + ph + 4);
      ctx.stroke();
      const label = `${Math.round(s / 60)}`;
      const half = ctx.measureText(label).width / 2;
      ctx.fillText(label, Math.min(w - 1 - half, x), M.t + ph + 6);
    }
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (const x of [0, this.C / 2, this.C]) {
      const y = Math.round(py(x)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(M.l - 4, y);
      ctx.lineTo(M.l, y);
      ctx.stroke();
      ctx.fillText(`${Math.round(x)}`, M.l - 6, y);
    }
    ctx.fillStyle = tk.inkSecondary;
    ctx.font = `500 12px ${tk.fontSans}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    ctx.fillText('Time (min)', M.l + pw / 2, h - 1);
    ctx.save();
    ctx.translate(13, M.t + ph / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textBaseline = 'middle';
    ctx.fillText('Position on ring (m)', 0, 0);
    ctx.restore();

    this.off = off;
  }

  /** Blit the pre-rendered diagram and draw the time cursor. */
  draw(tNow: number): void {
    const { w, h, pw, ph } = this.plotBox();
    if (w === 0 || h === 0) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    if (this.canvas.width !== Math.round(w * dpr) || this.canvas.height !== Math.round(h * dpr)) {
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
      this.render();
    }
    const ctx = this.canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    if (this.off) ctx.drawImage(this.off, 0, 0, this.canvas.width, this.canvas.height);
    ctx.scale(dpr, dpr);
    const tk = this.tk ?? readVizTokens();
    const x = Math.round(M.l + (Math.min(Math.max(tNow, 0), this.duration) / this.duration) * pw) + 0.5;
    ctx.lineWidth = 3;
    ctx.strokeStyle = tk.halo;
    ctx.beginPath();
    ctx.moveTo(x, M.t);
    ctx.lineTo(x, M.t + ph);
    ctx.stroke();
    ctx.globalAlpha = 0.9;
    ctx.lineWidth = 1;
    ctx.strokeStyle = tk.crosshair;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
}
