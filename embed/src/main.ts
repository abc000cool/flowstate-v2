/** FlowState embed: wires the views to the data pack and the controls. */
import './style.css';
import {
  baselineFor,
  findRun,
  fleetStats,
  loadIndex,
  loadObserved,
  loadRun,
  sampleAt,
  type IndexFile,
  type RunData,
  type RunRecord,
  type Selection,
} from './data';
import { ObservedView } from './observed';
import { drawRing } from './ring';
import { SpaceTimeView } from './spacetime';
import { drawStrip } from './strip';

const DATA_BASE = `${import.meta.env.BASE_URL}data/`;
const RATES = [1, 5, 20, 60];
const SEEK_STEP_S = 5;

function $<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el as T;
}

function fmtClock(t: number): string {
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function activationLabel(s: number): string {
  return s === 0 ? 'from start' : `after ${fmtClock(s)} min`;
}

interface State {
  index: IndexFile;
  sel: Selection;
  rec: RunRecord;
  base: RunRecord | undefined;
  run: RunData | null;
  t: number;
  playing: boolean;
  rate: number;
  tab: 'ring' | 'observed';
}

class App {
  private readonly params = new URLSearchParams(location.search);
  private readonly cvRing = $<HTMLCanvasElement>('cv-ring');
  private readonly cvStrip = $<HTMLCanvasElement>('cv-strip');
  private readonly st = new SpaceTimeView($<HTMLCanvasElement>('cv-st'));
  private readonly obs = new ObservedView($<HTMLCanvasElement>('cv-obs'), $('obs-hover'));
  private readonly btnPlay = $<HTMLButtonElement>('btn-play');
  private readonly rng = $<HTMLInputElement>('rng-time');
  private readonly loading = $('loading');
  private readonly runCache = new Map<string, RunData>();
  private state!: State;
  private xs = new Float32Array(0);
  private vs = new Float32Array(0);
  private lastFrame = 0;
  private lastReadout = -1;
  private lastMinute = -1;
  private observedLoaded = false;
  private loadToken = 0;

  async start(): Promise<void> {
    const index = await loadIndex(DATA_BASE);
    const g = index.grid;
    const sel: Selection = {
      n_vehicles: this.pickParam('n', g.n_vehicles, g.n_vehicles.includes(22) ? 22 : g.n_vehicles[0]),
      n_av: this.pickParam('av', g.n_av, 1),
      activation_s: this.pickParam('t', g.activation_s, 300),
      seed: this.pickParam('seed', g.seeds, g.seeds[0]),
    };
    const byId = index.runs.find((r) => r.id === this.params.get('run'));
    if (byId) Object.assign(sel, { n_vehicles: byId.n_vehicles, n_av: byId.n_av, activation_s: byId.activation_s, seed: byId.seed });
    const rec = findRun(index, sel) ?? index.runs[0];
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const rate = this.pickParam('rate', RATES, 20);
    this.state = {
      index,
      sel,
      rec,
      base: baselineFor(index, rec),
      run: null,
      t: 0,
      playing: false,
      rate,
      tab: this.params.get('tab') === 'observed' ? 'observed' : 'ring',
    };
    if (this.params.get('embed') === '1') document.body.classList.add('is-embed');
    this.rng.max = String(index.scenario.duration_s);
    this.buildControls();
    this.bindTransport();
    this.bindTabs();
    this.bindKeys();
    this.bindResize();
    this.showTab(this.state.tab);
    await this.loadCurrent();
    const autoplay = this.params.get('autoplay') !== '0' && !reduced;
    if (autoplay) this.setPlaying(true);
    requestAnimationFrame((ts) => this.frame(ts));
  }

  private pickParam(key: string, allowed: number[], fallback: number): number {
    const raw = this.params.get(key);
    if (raw === null) return fallback;
    const v = Number(raw);
    return allowed.includes(v) ? v : fallback;
  }

  // --- controls -----------------------------------------------------------

  private buildControls(): void {
    const { grid } = this.state.index;
    this.buildSeg($('grp-vehicles'), grid.n_vehicles, (v) => `${v}`, () => this.state.sel.n_vehicles, (v) => this.select({ n_vehicles: v }));
    this.buildSeg($('grp-av'), grid.n_av, (v) => (v === 0 ? 'none' : `${v}`), () => this.state.sel.n_av, (v) => this.select({ n_av: v }));
    this.buildSeg($('grp-activation'), grid.activation_s, activationLabel, () => this.state.sel.activation_s, (v) => this.select({ activation_s: v }));
    this.buildSeg($('grp-seed'), grid.seeds, (v) => `${v}`, () => this.state.sel.seed, (v) => this.select({ seed: v }));
    this.buildSeg($('grp-rate'), RATES, (v) => `${v}×`, () => this.state.rate, (v) => {
      this.state.rate = v;
      this.refreshSegs();
    });
    this.refreshSegs();
  }

  private buildSeg(
    host: HTMLElement,
    values: number[],
    label: (v: number) => string,
    current: () => number,
    onPick: (v: number) => void,
  ): void {
    host.replaceChildren();
    for (const v of values) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label(v);
      b.dataset.value = String(v);
      b.setAttribute('aria-pressed', String(v === current()));
      b.addEventListener('click', () => onPick(v));
      host.appendChild(b);
    }
  }

  private refreshSegs(): void {
    const s = this.state;
    const mark = (id: string, cur: number, disabled = false): void => {
      for (const b of $(id).querySelectorAll<HTMLButtonElement>('button')) {
        b.setAttribute('aria-pressed', String(Number(b.dataset.value) === cur));
        b.disabled = disabled;
      }
    };
    mark('grp-vehicles', s.sel.n_vehicles);
    mark('grp-av', s.sel.n_av);
    mark('grp-activation', s.sel.n_av === 0 ? -1 : s.sel.activation_s, s.sel.n_av === 0);
    mark('grp-seed', s.sel.seed);
    mark('grp-rate', s.rate);
  }

  private select(patch: Partial<Selection>): void {
    const s = this.state;
    const sel = { ...s.sel, ...patch };
    const rec = findRun(s.index, sel);
    if (!rec) return;
    s.sel = sel;
    s.rec = rec;
    s.base = baselineFor(s.index, rec);
    this.refreshSegs();
    const url = new URL(location.href);
    url.searchParams.set('run', rec.id);
    history.replaceState(null, '', url);
    void this.loadCurrent();
  }

  private async loadCurrent(): Promise<void> {
    const s = this.state;
    const token = ++this.loadToken;
    const wasPlaying = s.playing;
    this.setPlaying(false);
    s.run = null;
    let run = this.runCache.get(s.rec.id);
    if (!run) {
      this.loading.hidden = false;
      this.loading.textContent = 'Loading real simulation data…';
      try {
        run = await loadRun(DATA_BASE, s.rec);
      } catch (err) {
        this.loading.textContent = `Could not load the run: ${(err as Error).message}`;
        return;
      }
      if (token !== this.loadToken) return; // superseded by a later selection
      this.runCache.set(s.rec.id, run);
    }
    this.loading.hidden = true;
    s.run = run;
    s.t = 0;
    this.xs = new Float32Array(run.nVeh);
    this.vs = new Float32Array(run.nVeh);
    this.st.setRun(run, s.index.scenario.circumference_m, s.index.scenario.duration_s);
    this.lastMinute = -1;
    this.lastReadout = -1;
    this.renderProvenance();
    this.renderComparison();
    this.drawStripNow();
    this.drawFrame();
    if (wasPlaying || this.params.get('autoplay') !== '0') this.setPlaying(true);
  }

  // --- transport ----------------------------------------------------------

  private bindTransport(): void {
    this.btnPlay.addEventListener('click', () => {
      const s = this.state;
      if (!s.playing && s.t >= s.index.scenario.duration_s) s.t = 0;
      this.setPlaying(!s.playing);
    });
    this.rng.addEventListener('input', () => {
      this.state.t = Number(this.rng.value);
      this.lastReadout = -1;
      this.drawFrame();
    });
  }

  private setPlaying(on: boolean): void {
    this.state.playing = on;
    this.btnPlay.textContent = on ? 'Pause' : this.state.t >= this.state.index.scenario.duration_s ? 'Replay' : 'Play';
    this.btnPlay.setAttribute('aria-pressed', String(on));
    this.lastFrame = 0;
  }

  private bindKeys(): void {
    $('app').addEventListener('keydown', (e) => {
      if ((e.target as HTMLElement).tagName === 'INPUT' && e.key !== ' ') return;
      const s = this.state;
      if (e.key === ' ') {
        e.preventDefault();
        this.btnPlay.click();
      } else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        s.t = Math.min(s.index.scenario.duration_s, Math.max(0, s.t + (e.key === 'ArrowRight' ? SEEK_STEP_S : -SEEK_STEP_S)));
        this.lastReadout = -1;
        this.drawFrame();
      } else if (e.key === 'Home') {
        s.t = 0;
        this.drawFrame();
      }
    });
  }

  private bindTabs(): void {
    $('tab-ring').addEventListener('click', () => this.showTab('ring'));
    $('tab-observed').addEventListener('click', () => this.showTab('observed'));
  }

  private showTab(tab: 'ring' | 'observed'): void {
    this.state.tab = tab;
    for (const t of ['ring', 'observed'] as const) {
      const on = t === tab;
      $(`tab-${t}`).classList.toggle('is-active', on);
      $(`tab-${t}`).setAttribute('aria-selected', String(on));
      $(`panel-${t}`).hidden = !on;
    }
    if (tab === 'observed') void this.ensureObserved();
    else this.drawFrame();
  }

  private async ensureObserved(): Promise<void> {
    if (this.observedLoaded) {
      this.obs.draw();
      return;
    }
    try {
      const f = await loadObserved(DATA_BASE, this.state.index.observed.file);
      this.observedLoaded = true;
      this.obs.setField(f);
      $('obs-note').textContent = `${f.source}. ${f.coverage_note} Data hash ${f.data_hash.slice(0, 12)}.`;
    } catch (err) {
      $('obs-note').textContent = `Could not load the observed field: ${(err as Error).message}`;
    }
  }

  private bindResize(): void {
    const ro = new ResizeObserver(() => {
      this.st.resize();
      this.drawStripNow();
      this.drawFrame();
      if (this.state.tab === 'observed') this.obs.draw();
      if (window.parent !== window) {
        window.parent.postMessage({ type: 'flowstate-embed', height: document.documentElement.scrollHeight }, '*');
      }
    });
    ro.observe($('app'));
  }

  // --- rendering ----------------------------------------------------------

  private frame(ts: number): void {
    const s = this.state;
    if (s.playing && s.run) {
      if (this.lastFrame > 0) {
        s.t += ((ts - this.lastFrame) / 1000) * s.rate;
        if (s.t >= s.index.scenario.duration_s) {
          s.t = s.index.scenario.duration_s;
          this.setPlaying(false);
        }
      }
      this.lastFrame = ts;
      this.drawFrame();
    }
    requestAnimationFrame((n) => this.frame(n));
  }

  private drawFrame(): void {
    const s = this.state;
    const run = s.run;
    if (!run || s.tab !== 'ring') return;
    const C = s.index.scenario.circumference_m;
    sampleAt(run, C, s.t, this.xs, this.vs);
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = this.cvRing.clientWidth;
    const h = this.cvRing.clientHeight;
    if (this.cvRing.width !== Math.round(w * dpr) || this.cvRing.height !== Math.round(h * dpr)) {
      this.cvRing.width = Math.round(w * dpr);
      this.cvRing.height = Math.round(h * dpr);
    }
    const ctx = this.cvRing.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const controllerOn = run.rec.n_av > 0 && s.t >= run.rec.activation_s;
    drawRing(ctx, w, h, this.xs, this.vs, {
      circumference: C,
      vehicleLength: s.index.scenario.vehicle_length_m,
      avIndex: new Set(run.rec.av_index),
      vRef: run.vRef,
      controllerOn,
    });
    this.st.draw(s.t);
    this.rng.value = String(s.t);
    const minute = Math.floor(s.t / 60);
    if (minute !== this.lastMinute) {
      this.lastMinute = minute;
      this.drawStripNow();
    }
    if (this.lastReadout < 0 || Math.abs(s.t - this.lastReadout) >= 0.25) {
      this.lastReadout = s.t;
      this.renderReadouts(controllerOn);
    }
  }

  private renderReadouts(controllerOn: boolean): void {
    const s = this.state;
    const st = fleetStats(this.vs);
    $('ro-time').textContent = fmtClock(s.t);
    $('ro-mean').textContent = `${st.mean.toFixed(1)} m/s`;
    $('ro-std').textContent = `${st.std.toFixed(2)} m/s`;
    $('ro-min').textContent = `${st.min.toFixed(1)} m/s`;
    $('ro-stopped').textContent = `${st.stopped} of ${this.vs.length}`;
    const rec = s.rec;
    $('ring-status').textContent =
      rec.n_av === 0
        ? 'no control'
        : controllerOn
          ? `${rec.n_av} controlled vehicle${rec.n_av > 1 ? 's' : ''}: on`
          : `${rec.n_av} controlled vehicle${rec.n_av > 1 ? 's' : ''}: switches on at ${fmtClock(rec.activation_s)}`;
  }

  private drawStripNow(): void {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = this.cvStrip.clientWidth;
    const h = this.cvStrip.clientHeight;
    if (w === 0 || h === 0) return;
    this.cvStrip.width = Math.round(w * dpr);
    this.cvStrip.height = Math.round(h * dpr);
    const ctx = this.cvStrip.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawStrip(ctx, w, h, this.state.rec, this.state.base, Math.floor(this.state.t / 60));
  }

  private renderComparison(): void {
    const { rec, base } = this.state;
    const el = $('cmp-text');
    const pct = (f: number): string => `${(100 * f).toFixed(0)}%`;
    if (rec.n_av === 0 || !base) {
      el.textContent = `Uncontrolled baseline, seed ${rec.seed}: over the last 5 minutes the speed spread is ${rec.last300.sigma_v_ms.toFixed(2)} m/s, mean speed ${rec.last300.mean_v_ms.toFixed(1)} m/s, and vehicles are stopped ${pct(rec.last300.stopped_fraction)} of the time.`;
      return;
    }
    el.textContent = `Last 5 minutes, same seed (${rec.seed}) with and without control: speed spread ${rec.last300.sigma_v_ms.toFixed(2)} vs ${base.last300.sigma_v_ms.toFixed(2)} m/s · mean speed ${rec.last300.mean_v_ms.toFixed(1)} vs ${base.last300.mean_v_ms.toFixed(1)} m/s · time stopped ${pct(rec.last300.stopped_fraction)} vs ${pct(base.last300.stopped_fraction)}.`;
  }

  private renderProvenance(): void {
    const { index, rec } = this.state;
    const e = index.engine;
    const sc = index.scenario;
    $('provenance').textContent =
      `Eclipse SUMO ${e.sumo ?? '?'} · ${e.model} (T ${sc.idm.T} s, a ${sc.idm.a_max} m/s², s0 ${sc.idm.s0} m, ±${Math.round(sc.idm.heterogeneity_frac * 100)}% per-driver) · ` +
      `${sc.name} · ${sc.circumference_m} m ring · ${rec.n_vehicles} vehicles, ${rec.n_av} controlled` +
      `${rec.n_av > 0 ? ` (${rec.controller}, on ${activationLabel(rec.activation_s)})` : ''} · ` +
      `seed ${rec.seed} · config ${rec.config_hash} · ${sc.output_hz} Hz samples · seeded perturbation: ${sc.seeded_perturbation ? 'yes' : 'no'}`;
  }
}

new App().start().catch((err: unknown) => {
  const el = document.getElementById('loading');
  if (el) {
    el.hidden = false;
    el.textContent = `Could not load simulation data: ${(err as Error).message}`;
  }
});
