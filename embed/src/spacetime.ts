/** Time-space diagram of one run: every trajectory coloured by speed. */
import { speedColor } from './colormap';
import type { RunData } from './data';

const M = { l: 38, r: 26, t: 8, b: 20 };
const INK = '#8b8a85';
const GRID = '#1c1f26';

export class SpaceTimeView {
  private off: HTMLCanvasElement | null = null;
  private run: RunData | null = null;
  private C = 1;
  private duration = 1;
  private dpr = 1;

  constructor(private readonly canvas: HTMLCanvasElement) {}

  setRun(run: RunData, circumference: number, duration: number): void {
    this.run = run;
    this.C = circumference;
    this.duration = duration;
    this.render();
  }

  resize(): void {
    this.render();
  }

  private plotBox(): { w: number; h: number; pw: number; ph: number } {
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    return { w, h, pw: Math.max(1, w - M.l - M.r), ph: Math.max(1, h - M.t - M.b) };
  }

  /** Render the whole diagram once into an offscreen canvas. */
  private render(): void {
    const run = this.run;
    const { w, h, pw, ph } = this.plotBox();
    if (!run || w === 0 || h === 0) return;
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    const off = document.createElement('canvas');
    off.width = Math.round(w * this.dpr);
    off.height = Math.round(h * this.dpr);
    const ctx = off.getContext('2d');
    if (!ctx) return;
    ctx.scale(this.dpr, this.dpr);
    ctx.fillStyle = '#0b0d11';
    ctx.fillRect(0, 0, w, h);

    const px = (t: number): number => M.l + (t / this.duration) * pw;
    const py = (x: number): number => M.t + (1 - x / this.C) * ph;

    ctx.strokeStyle = GRID;
    ctx.lineWidth = 1;
    for (let s = 0; s <= this.duration; s += 60) {
      ctx.beginPath();
      ctx.moveTo(px(s), M.t);
      ctx.lineTo(px(s), M.t + ph);
      ctx.stroke();
    }

    const { nVeh, nSamples, rec } = run;
    ctx.lineWidth = 1.4;
    ctx.lineCap = 'round';
    for (let j = 0; j < nVeh; j++) {
      for (let i = 0; i < nSamples - 1; i++) {
        const x0 = run.x[i * nVeh + j];
        const x1 = run.x[(i + 1) * nVeh + j];
        if (Math.abs(x1 - x0) > this.C / 2) continue; // wrap: break the line
        const t0 = rec.t0_s + i * rec.dt_s;
        ctx.strokeStyle = speedColor(run.v[i * nVeh + j], run.vRef);
        ctx.beginPath();
        ctx.moveTo(px(t0), py(x0));
        ctx.lineTo(px(t0 + rec.dt_s), py(x1));
        ctx.stroke();
      }
    }

    if (rec.n_av > 0) {
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(px(rec.activation_s), M.t);
      ctx.lineTo(px(rec.activation_s), M.t + ph);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = '10px system-ui, sans-serif';
      const label = 'controller on';
      const tw = ctx.measureText(label).width + 10;
      const right = rec.activation_s > this.duration / 2;
      const lx = right ? px(rec.activation_s) - tw - 3 : px(rec.activation_s) + 3;
      ctx.fillStyle = 'rgba(11,13,17,0.85)';
      ctx.fillRect(lx, M.t + 2, tw, 14);
      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(label, lx + 5, M.t + 4);
    }

    ctx.fillStyle = INK;
    ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (let s = 0; s <= this.duration; s += 120) ctx.fillText(`${Math.round(s / 60)} min`, px(s), M.t + ph + 4);
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillText('0 m', M.l - 4, py(0));
    ctx.fillText(`${this.C.toFixed(0)}`, M.l - 4, py(this.C));
    ctx.save();
    ctx.translate(10, M.t + ph / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = 'center';
    ctx.fillText('position on ring →', 0, 0);
    ctx.restore();

    this.off = off;
  }

  /** Blit the pre-rendered diagram and draw the time cursor. */
  draw(tNow: number): void {
    const { w, h, pw } = this.plotBox();
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
    const x = M.l + (Math.min(tNow, this.duration) / this.duration) * pw;
    ctx.strokeStyle = '#e9e9e4';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(x, M.t);
    ctx.lineTo(x, h - M.b);
    ctx.stroke();
  }
}
