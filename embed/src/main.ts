/** FlowState embed: wires the views to the data pack and the controls. */
import './tokens.css';
import './style.css';
import { SPEED_DOMAIN_MAX_KMH, WAVE_THRESHOLD_KMH } from './colormap';
import {
  baselineFor,
  findRun,
  fleetStats,
  loadIndex,
  loadObserved,
  loadRun,
  sampleAt,
  STOPPED_BELOW_MS,
  type IndexFile,
  type RunData,
  type RunRecord,
  type Selection,
} from './data';
import { renderLegend } from './legend';
import { ObservedView } from './observed';
import { drawRing } from './ring';
import { SpaceTimeView } from './spacetime';
import { drawStrip } from './strip';
import { applyThemeParam, onThemeChange, readVizTokens } from './theme';
import { formatKmh, formatMph, msToKmh } from './units';

const DATA_BASE = `${import.meta.env.BASE_URL}data/`;
const RATES = [1, 5, 20, 60];
const SEEK_STEP_S = 5;
/** The pack's headline window: the last 300 s of every run (index.json `last300`). */
const LAST_WINDOW_S = 300;

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
  if (s === 0) return 'from start';
  return s % 60 === 0 ? `after ${s / 60} min` : `after ${fmtClock(s)}`;
}

function pct(f: number): string {
  return `${(100 * f).toFixed(f > 0 && f < 0.1 ? 1 : 0)}%`;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

interface State {
  index: IndexFile;
  sel: Selection;
  rec: RunRecord;
  base: RunRecord | undefined;
  run: RunData | null;
  /** Top of the ring colour scale [m/s]. */
  vTop: number;
  t: number;
  playing: boolean;
  rate: number;
  tab: 'ring' | 'observed';
}

class App {
  private readonly params = new URLSearchParams(location.search);
  private readonly reduced =
    typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  /** Autoplay unless the host asked for ?autoplay=0 or the visitor prefers reduced motion. */
  private readonly autoplay = this.params.get('autoplay') !== '0' && !this.reduced;
  private readonly cvRing = $<HTMLCanvasElement>('cv-ring');
  private readonly cvStrip = $<HTMLCanvasElement>('cv-strip');
  private readonly st = new SpaceTimeView($<HTMLCanvasElement>('cv-st'));
  private readonly obs = new ObservedView($<HTMLCanvasElement>('cv-obs'), $('obs-readout'));
  private readonly btnPlay = $<HTMLButtonElement>('btn-play');
  private readonly rng = $<HTMLInputElement>('rng-time');
  private readonly loading = $('loading');
  private readonly runCache = new Map<string, RunData>();
  private state!: State;
  private xs = new Float32Array(0);
  private vs = new Float32Array(0);
  private lastFrame = 0;
  private raf = 0;
  private lastReadout = -1;
  private observedLoaded = false;
  private loadToken = 0;
  /** Set once the stage has been on screen; autoplay waits for it. */
  private seen = false;
  private lastHeight = 0;

  async start(): Promise<void> {
    applyThemeParam(location.search);
    if (this.params.get('embed') === '1') document.body.classList.add('is-embed');
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
    this.state = {
      index,
      sel,
      rec,
      base: baselineFor(index, rec),
      run: null,
      vTop: 1,
      t: 0,
      playing: false,
      rate: this.pickParam('rate', RATES, 20),
      tab: this.params.get('tab') === 'observed' ? 'observed' : 'ring',
    };
    this.rng.max = String(index.scenario.duration_s);
    this.buildControls();
    this.bindTransport();
    this.bindTabs();
    this.bindKeys();
    this.bindResize();
    this.bindVisibility();
    onThemeChange(() => this.repaintAll());
    this.showTab(this.state.tab);
    await this.loadCurrent(true);
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
    this.buildSeg($('grp-vehicles'), grid.n_vehicles, (v) => `${v}`, (v) => this.select({ n_vehicles: v }));
    this.buildSeg($('grp-av'), grid.n_av, (v) => (v === 0 ? 'none' : `${v}`), (v) => this.select({ n_av: v }));
    this.buildSeg($('grp-activation'), grid.activation_s, activationLabel, (v) => this.select({ activation_s: v }));
    this.buildSeg($('grp-seed'), grid.seeds, (v) => `${v}`, (v) => this.select({ seed: v }));
    this.buildSeg($('grp-rate'), RATES, (v) => `${v}×`, (v) => {
      this.state.rate = v;
      this.refreshSegs();
    });
    for (const b of $('grp-rate').querySelectorAll<HTMLButtonElement>('button')) {
      b.setAttribute('aria-label', `${b.dataset.value} times real time`);
    }
    this.refreshSegs();
  }

  private buildSeg(host: HTMLElement, values: number[], label: (v: number) => string, onPick: (v: number) => void): void {
    host.replaceChildren();
    for (const v of values) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label(v);
      b.dataset.value = String(v);
      b.setAttribute('aria-pressed', 'false');
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
    try {
      const url = new URL(location.href);
      url.searchParams.set('run', rec.id);
      history.replaceState(null, '', url);
    } catch {
      /* sandboxed host: the URL is a convenience only */
    }
    void this.loadCurrent(false);
  }

  private async getRun(rec: RunRecord): Promise<RunData> {
    const hit = this.runCache.get(rec.id);
    if (hit) return hit;
    const run = await loadRun(DATA_BASE, rec);
    this.runCache.set(rec.id, run);
    return run;
  }

  private async loadCurrent(first: boolean): Promise<void> {
    const s = this.state;
    const token = ++this.loadToken;
    const wasPlaying = s.playing;
    this.setPlaying(false);
    s.run = null;
    const needsFetch = !this.runCache.has(s.rec.id) || (s.base !== undefined && !this.runCache.has(s.base.id));
    if (needsFetch) {
      this.loading.hidden = false;
      this.loading.textContent = 'Loading the recorded simulation runs…';
    }
    let run: RunData;
    let baseRun: RunData | undefined;
    try {
      [run, baseRun] = await Promise.all([this.getRun(s.rec), s.base ? this.getRun(s.base) : Promise.resolve(undefined)]);
    } catch (err) {
      if (token === this.loadToken) this.loading.textContent = `Could not load the run: ${(err as Error).message}`;
      return;
    }
    if (token !== this.loadToken) return; // superseded by a later selection
    this.loading.hidden = true;
    s.run = run;
    // One colour scale for a run and its uncontrolled twin (same ring, same seed): the
    // uncontrolled run's 95th-percentile speed.
    s.vTop = baseRun ? baseRun.vRef : run.vRef;
    s.t = 0;
    this.xs = new Float32Array(run.nVeh);
    this.vs = new Float32Array(run.nVeh);
    this.st.setRun(run, s.index.scenario.circumference_m, s.index.scenario.duration_s, s.vTop);
    this.lastReadout = -1;
    this.renderLegendRing();
    this.renderProvenance();
    this.renderComparison();
    this.renderStripKeys();
    this.drawFrame();
    this.postHeight();
    // a new selection keeps playing if it was; otherwise it plays only when autoplay is allowed
    // (never with ?autoplay=0 or for visitors who prefer reduced motion)
    const play = first ? this.autoplay && this.seen : wasPlaying || this.autoplay;
    if (play) this.setPlaying(true);
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
    const s = this.state;
    s.playing = on && s.run !== null;
    $('btn-play-label').textContent = s.playing ? 'Pause' : s.t >= s.index.scenario.duration_s ? 'Replay' : 'Play';
    this.btnPlay.setAttribute('aria-pressed', String(s.playing));
    this.lastFrame = 0;
    if (s.playing && !this.raf) this.raf = requestAnimationFrame((ts) => this.frame(ts));
  }

  private bindKeys(): void {
    document.addEventListener('keydown', (e) => {
      const target = e.target as HTMLElement;
      // buttons, the slider, the tabs and the observed plot handle their own keys
      if (target.closest('button, input, select, textarea, [role="tablist"], #cv-obs')) return;
      if (this.state.tab !== 'ring' || e.metaKey || e.ctrlKey || e.altKey) return;
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
        e.preventDefault();
        s.t = 0;
        this.lastReadout = -1;
        this.drawFrame();
      }
    });
  }

  private bindTabs(): void {
    const tabs = [$('tab-ring'), $('tab-observed')];
    tabs[0].addEventListener('click', () => this.showTab('ring'));
    tabs[1].addEventListener('click', () => this.showTab('observed'));
    for (const t of tabs) {
      t.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight' && e.key !== 'Home' && e.key !== 'End') return;
        e.preventDefault();
        const next = this.state.tab === 'ring' ? 'observed' : 'ring';
        const target = e.key === 'Home' ? 'ring' : e.key === 'End' ? 'observed' : next;
        this.showTab(target);
        $(`tab-${target}`).focus();
      });
    }
  }

  private showTab(tab: 'ring' | 'observed'): void {
    this.state.tab = tab;
    for (const t of ['ring', 'observed'] as const) {
      const on = t === tab;
      const el = $(`tab-${t}`);
      el.setAttribute('aria-selected', String(on));
      el.tabIndex = on ? 0 : -1;
      $(`panel-${t}`).hidden = !on;
    }
    if (tab === 'observed') {
      if (this.state.playing) this.setPlaying(false);
      void this.ensureObserved();
    } else {
      this.st.resize();
      this.lastReadout = -1;
      this.drawFrame();
    }
  }

  private async ensureObserved(): Promise<void> {
    if (this.observedLoaded) {
      this.obs.draw();
      return;
    }
    renderLegend($('lg-obs'), {
      topKmh: SPEED_DOMAIN_MAX_KMH,
      threshold: { kmh: WAVE_THRESHOLD_KMH, label: `wave threshold ${WAVE_THRESHOLD_KMH} km/h (FlowState default)` },
      noData: true,
      endDigits: 0,
    });
    try {
      const f = await loadObserved(DATA_BASE, this.state.index.observed.file);
      this.observedLoaded = true;
      this.obs.setField(f);
      $('obs-note').textContent = `${f.source}. ${f.coverage_note} Data hash ${f.data_hash.slice(0, 12)}.`;
    } catch (err) {
      $('obs-note').textContent = `Could not load the observed field: ${(err as Error).message}`;
    }
  }

  private repaintAll(): void {
    this.st.resize();
    this.lastReadout = -1;
    this.drawFrame();
    if (this.state.tab === 'observed') this.obs.draw();
  }

  private bindResize(): void {
    let lastW = 0;
    const ro = new ResizeObserver(() => {
      const w = $('app').clientWidth;
      if (w !== lastW) {
        lastW = w;
        this.repaintAll();
      }
      this.postHeight();
    });
    ro.observe($('app'));
  }

  /** Tell a host page our height ({type: 'flowstate-embed', height}) so it can size the iframe. */
  private postHeight(): void {
    if (window.parent === window) return;
    const height = Math.ceil(document.documentElement.getBoundingClientRect().height);
    if (height === this.lastHeight) return;
    this.lastHeight = height;
    window.parent.postMessage({ type: 'flowstate-embed', height }, '*');
  }

  /** Autoplay starts the first time the stage is on screen, so a visitor sees the wave form. */
  private bindVisibility(): void {
    const start = (): void => {
      if (this.seen) return;
      this.seen = true;
      if (this.autoplay && this.state.run && !this.state.playing && this.state.t === 0 && this.state.tab === 'ring') {
        this.setPlaying(true);
      }
    };
    if (typeof IntersectionObserver !== 'function') {
      start();
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          start();
          io.disconnect();
        }
      },
      { threshold: 0.25 },
    );
    io.observe($('cv-ring'));
  }

  // --- rendering ----------------------------------------------------------

  private frame(ts: number): void {
    this.raf = 0;
    const s = this.state;
    if (!s.playing || !s.run) return;
    if (this.lastFrame > 0) {
      s.t += ((ts - this.lastFrame) / 1000) * s.rate;
      if (s.t >= s.index.scenario.duration_s) {
        s.t = s.index.scenario.duration_s;
        this.setPlaying(false);
      }
    }
    this.lastFrame = ts;
    this.drawFrame();
    if (s.playing) this.raf = requestAnimationFrame((n) => this.frame(n));
  }

  private drawFrame(): void {
    const s = this.state;
    const run = s.run;
    if (!run || s.tab !== 'ring') return;
    const C = s.index.scenario.circumference_m;
    const duration = s.index.scenario.duration_s;
    sampleAt(run, C, s.t, this.xs, this.vs);
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = this.cvRing.clientWidth;
    const h = this.cvRing.clientHeight;
    if (this.cvRing.width !== Math.round(w * dpr) || this.cvRing.height !== Math.round(h * dpr)) {
      this.cvRing.width = Math.round(w * dpr);
      this.cvRing.height = Math.round(h * dpr);
    }
    const tk = readVizTokens();
    const ctx = this.cvRing.getContext('2d');
    if (ctx && w > 0 && h > 0) {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawRing(ctx, w, h, this.xs, this.vs, {
        circumference: C,
        vehicleLength: s.index.scenario.vehicle_length_m,
        avIndex: new Set(run.rec.av_index),
        vTop: s.vTop,
        controllerOn: this.controllerOn(),
        tokens: tk,
      });
    }
    this.st.draw(s.t);
    this.drawStrip(tk);
    this.rng.value = String(s.t);
    this.rng.style.setProperty('--pct', `${(100 * s.t) / duration}%`);
    this.rng.setAttribute('aria-valuetext', `${fmtClock(s.t)} of ${fmtClock(duration)}`);
    $('ro-time').textContent = `${fmtClock(s.t)} / ${fmtClock(duration)}`;
    if (!s.playing) $('btn-play-label').textContent = s.t >= duration ? 'Replay' : 'Play';
    if (this.lastReadout < 0 || Math.abs(s.t - this.lastReadout) >= 0.25) {
      this.lastReadout = s.t;
      this.renderReadouts();
    }
  }

  private controllerOn(): boolean {
    const s = this.state;
    return s.rec.n_av > 0 && s.t >= s.rec.activation_s;
  }

  private drawStrip(tk = readVizTokens()): void {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = this.cvStrip.clientWidth;
    const h = this.cvStrip.clientHeight;
    if (w === 0 || h === 0) return;
    if (this.cvStrip.width !== Math.round(w * dpr) || this.cvStrip.height !== Math.round(h * dpr)) {
      this.cvStrip.width = Math.round(w * dpr);
      this.cvStrip.height = Math.round(h * dpr);
    }
    const ctx = this.cvStrip.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const minutes = Math.ceil(this.state.index.scenario.duration_s / 60);
    drawStrip(ctx, w, h, this.state.rec, this.state.base, this.state.t, minutes, tk);
  }

  private renderReadouts(): void {
    const s = this.state;
    const st = fleetStats(this.vs);
    $('ro-mean').textContent = formatKmh(st.mean);
    $('ro-mean-mph').textContent = formatMph(st.mean);
    $('ro-std').textContent = formatKmh(st.std);
    $('ro-std-mph').textContent = formatMph(st.std);
    $('ro-min').textContent = formatKmh(st.min);
    $('ro-min-mph').textContent = formatMph(st.min);
    $('ro-stopped').textContent = `${st.stopped} of ${this.vs.length}`;
    $('ro-stopped-sub').textContent = `below ${formatKmh(STOPPED_BELOW_MS)}`;
    const rec = s.rec;
    const what = plural(rec.n_av, 'controlled vehicle', 'controlled vehicles');
    $('ring-status').textContent =
      rec.n_av === 0 ? 'No control' : this.controllerOn() ? `${what}: on` : `${what}: on at ${fmtClock(rec.activation_s)}`;
  }

  private renderLegendRing(): void {
    const s = this.state;
    const topKmh = msToKmh(s.vTop);
    renderLegend($('lg-ring'), { topKmh, endDigits: 1 });
    const own = s.base === undefined || s.base.id === s.rec.id;
    $('lg-ring-note').textContent = own
      ? `Colour scale: 0 to ${topKmh.toFixed(1)} km/h, the 95th-percentile speed of this uncontrolled run. Runs with control on the same ring and seed use the same scale.`
      : `Colour scale: 0 to ${topKmh.toFixed(1)} km/h, the 95th-percentile speed of the uncontrolled run with the same ring and seed, so both runs share one scale.`;
    $('av-key').hidden = s.rec.n_av === 0;
  }

  private renderStripKeys(): void {
    const { rec, base } = this.state;
    const controlled = rec.n_av > 0;
    $('sk-run').classList.toggle('is-baseline', !controlled);
    $('sk-run-label').textContent = controlled ? 'this run' : 'this run (no control)';
    $('sk-base').hidden = !controlled || base === undefined;
  }

  private renderComparison(): void {
    const { rec, base, index } = this.state;
    const d = index.scenario.duration_s;
    const cap = $('cmp-caption');
    cap.replaceChildren(
      document.createTextNode(`Last ${LAST_WINDOW_S / 60} minutes (${fmtClock(d - LAST_WINDOW_S)} to ${fmtClock(d)}), seed ${rec.seed}`),
    );
    const sub = document.createElement('span');
    sub.className = 'cap-sub';
    sub.textContent = `From the recorded trajectories. Stopped means below ${formatKmh(STOPPED_BELOW_MS)} (${formatMph(STOPPED_BELOW_MS)}).`;
    cap.appendChild(sub);

    const cols: { label: string; r: RunRecord }[] = [{ label: rec.n_av > 0 ? 'This run' : 'This run (no control)', r: rec }];
    if (rec.n_av > 0 && base) cols.push({ label: 'No control, same seed', r: base });
    const head = $('cmp-head');
    head.replaceChildren();
    const th0 = document.createElement('th');
    th0.scope = 'col';
    th0.textContent = 'Measure';
    head.appendChild(th0);
    for (const c of cols) {
      const th = document.createElement('th');
      th.scope = 'col';
      th.className = 'num';
      th.textContent = c.label;
      head.appendChild(th);
    }

    const body = $('cmp-body');
    body.replaceChildren();
    const speedCell = (ms: number): HTMLTableCellElement => {
      const td = document.createElement('td');
      td.className = 'num';
      td.textContent = formatKmh(ms);
      const s2 = document.createElement('span');
      s2.className = 'sub';
      s2.textContent = formatMph(ms);
      td.appendChild(s2);
      return td;
    };
    const rows: { label: (th: HTMLElement) => void; cell: (r: RunRecord) => HTMLTableCellElement }[] = [
      {
        label: (th) => {
          th.append('Speed spread σ');
          th.appendChild(document.createElement('sub')).textContent = 'v';
        },
        cell: (r) => speedCell(r.last300.sigma_v_ms),
      },
      { label: (th) => th.append('Mean speed'), cell: (r) => speedCell(r.last300.mean_v_ms) },
      {
        label: (th) => th.append('Time stopped'),
        cell: (r) => {
          const td = document.createElement('td');
          td.className = 'num';
          td.textContent = pct(r.last300.stopped_fraction);
          return td;
        },
      },
    ];
    for (const row of rows) {
      const tr = document.createElement('tr');
      const th = document.createElement('td');
      row.label(th);
      tr.appendChild(th);
      for (const c of cols) tr.appendChild(row.cell(c.r));
      body.appendChild(tr);
    }
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
