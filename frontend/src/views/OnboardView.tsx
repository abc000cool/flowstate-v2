/** Onboard corridor: the "any freeway corridor from public data" path in
 * three inputs (CLAUDE.md §3.2.4, docs/ONBOARDING_MNDOT.md §3).
 *
 * A bounding box and a direction of travel, a detector export with its
 * station inventory, and the two mainline stations at the ends of the span.
 * `POST /corridors` answers 202; the view polls `GET /corridors/{id}` through
 * the named stages (extract → network → observations → demand → install) and
 * then shows what the job *found and derived*: the chain it followed, the
 * lane profile, the ramps it discovered and where each ramp's flow came from,
 * which stations it placed and which it refused to place, the demand peaks,
 * and the brackets whose flow change no ramp could carry.
 *
 * Nothing on the summary panel is a claim about how the corridor behaves.
 * Onboarding produces a scenario whose demand traces to detectors; whether it
 * reproduces the corridor is what the report answers, which is why the two
 * follow-on buttons are "Run 20 seeds" (the reporting standard, n ≥ 20) and
 * then "Report against observations" — the second only once a run has
 * finished, because there is nothing to score before that.
 *
 * Writes never fall back to the in-browser demo backend (`assertWritable`):
 * an onboarding accepted by this browser would be a corridor calibrated
 * against nothing. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  createCorridor,
  createReport,
  createRun,
  getCorridor,
  getRun,
  OFFLINE_WRITE_MESSAGE,
} from '../api/client';
import type { CorridorOut, RunDetail } from '../api/types';
import { useAppState } from '../components/AppContext';
import { StatusChip } from '../components/bits';
import { toast, toastError } from '../components/toast';
import { formatDistKm, formatNumber } from '../lib/format';
import { useAuthFailed, useOfflineFallback, usePoll } from '../lib/hooks';

const CORRIDOR_POLL_MS = 2000;
const RUN_POLL_MS = 3000;

/** The reporting standard a headline metric must meet (CLAUDE.md §0.6); the
 * launch button commits to it rather than offering a cheaper, unquotable n. */
const REPORT_SEEDS = 20;

/** `api.schemas.CORRIDOR_NAME_PATTERN` — the name becomes a directory and a
 * `scenarios/<name>.yaml` preset file on the server. */
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

/** Which onboarding this browser last started, so the panel survives the jump
 * to Runs that launching makes. Two server-side ids and nothing else: the
 * summary, the status and the run are re-read from the API on the way back,
 * never restored from a stale copy in this browser. */
const LS_LAST = 'flowstate.onboard.last';

interface LastOnboarding {
  corridor_id: string;
  run_id?: string;
}

function readLast(): LastOnboarding | null {
  try {
    const raw = window.localStorage.getItem(LS_LAST);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as LastOnboarding;
    return typeof parsed?.corridor_id === 'string' ? parsed : null;
  } catch {
    return null; // storage unavailable or corrupt: start from an empty form
  }
}

function writeLast(value: LastOnboarding | null): void {
  try {
    if (value === null) window.localStorage.removeItem(LS_LAST);
    else window.localStorage.setItem(LS_LAST, JSON.stringify(value));
  } catch {
    /* storage unavailable — the panel then lives for this visit only */
  }
}

/** Form defaults: a 4-hour AM peak in 5-minute windows with a 30-minute
 * warm-up — the span docs/ONBOARDING_MNDOT.md §4 analyses. */
const DEFAULTS = {
  window_s: '300',
  t0_local: '06:00',
  duration_s: '14400',
  warmup_s: '1800',
};

interface Bbox {
  south: number;
  west: number;
  north: number;
  east: number;
}

/** Parse `"S,W,N,E"` (commas, spaces or both) into the four bounds.
 *
 * Returns the reason it is not a bounding box instead of throwing, so the
 * form can say so under the field before anything is sent. */
export function parseBbox(raw: string): { bbox: Bbox } | { error: string } {
  const parts = raw
    .split(/[,\s]+/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
  if (parts.length !== 4) {
    return { error: `needs four numbers "south, west, north, east" — got ${parts.length}` };
  }
  const nums = parts.map(Number);
  if (nums.some((n) => !Number.isFinite(n))) return { error: 'all four values must be numbers' };
  const [south, west, north, east] = nums;
  if (south >= north) return { error: `south (${south}) must be below north (${north})` };
  if (west >= east) return { error: `west (${west}) must be left of east (${east})` };
  if (south < -90 || north > 90 || west < -180 || east > 180) {
    return { error: 'outside the WGS84 range (lat ±90, lon ±180)' };
  }
  return { bbox: { south, west, north, east } };
}

function isTerminal(status: string): boolean {
  return status === 'done' || status === 'failed';
}

/** The form fields that can refuse an onboarding, in the order the button
 * reports them. Every failing one states its reason under its own control:
 * fixing one problem to be told about the next, one round at a time, is the
 * behaviour this replaces. */
type ProblemField =
  | 'name'
  | 'bbox'
  | 'bearing'
  | 'upstream'
  | 'downstream'
  | 'detectors'
  | 'stations'
  | 'window'
  | 'duration'
  | 'warmup';

const PROBLEM_ORDER: ProblemField[] = [
  'name',
  'bbox',
  'bearing',
  'upstream',
  'downstream',
  'detectors',
  'stations',
  'window',
  'duration',
  'warmup',
];

export type Problems = Partial<Record<ProblemField, string>>;

export function OnboardView(): JSX.Element {
  const [name, setName] = useState('');
  const [bboxRaw, setBboxRaw] = useState('');
  const [bearingRaw, setBearingRaw] = useState('270');
  const [upstream, setUpstream] = useState('');
  const [downstream, setDownstream] = useState('');
  const [detectors, setDetectors] = useState<File | null>(null);
  const [stations, setStations] = useState<File | null>(null);
  const [windowRaw, setWindowRaw] = useState(DEFAULTS.window_s);
  const [t0Local, setT0Local] = useState(DEFAULTS.t0_local);
  const [durationRaw, setDurationRaw] = useState(DEFAULTS.duration_s);
  const [warmupRaw, setWarmupRaw] = useState(DEFAULTS.warmup_s);

  const [corridor, setCorridor] = useState<CorridorOut | null>(null);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [busy, setBusy] = useState(false);
  // The ids the polls read. They live in refs, not in the poll callbacks'
  // dependency lists: `usePoll` restarts its interval whenever the callback's
  // identity changes, and a callback closing over the polled *payload* would
  // be rebuilt by its own result — a tight fetch loop, not a 2 s poll.
  const corridorIdRef = useRef<string | null>(null);
  const runIdRef = useRef<string | null>(null);

  const trackCorridor = useCallback((next: CorridorOut) => {
    corridorIdRef.current = next.corridor_id;
    setCorridor(next);
  }, []);
  const trackRun = useCallback((next: RunDetail) => {
    runIdRef.current = next.run_id;
    setRun(next);
  }, []);

  const navigate = useNavigate();
  const authFailed = useAuthFailed();
  const offline = useOfflineFallback();
  // the shell's CORRIDOR label; renamed here because `setCorridor` is already
  // this view's onboarding-job setter
  const { setCorridor: setActiveCorridor } = useAppState();

  // A finished onboarding is what makes a corridor the one being worked on:
  // without this the top bar keeps naming whatever was active before (or
  // nothing at all) until some other view sets it.
  const onboardedName = corridor?.status === 'done' ? corridor.name : null;
  useEffect(() => {
    if (onboardedName) setActiveCorridor(onboardedName);
  }, [onboardedName, setActiveCorridor]);

  const bbox = useMemo(() => parseBbox(bboxRaw), [bboxRaw]);
  const bearing = Number(bearingRaw);

  const problems = useMemo<Problems>(() => {
    const p: Problems = {};
    if (!NAME_RE.test(name)) p.name = 'Name: letters, digits, - and _ (it becomes a preset file)';
    if ('error' in bbox) p.bbox = `Bounding box: ${bbox.error}`;
    if (!Number.isFinite(bearing) || bearing < 0 || bearing > 360) {
      p.bearing = 'Bearing: compass degrees within 0–360 (270 = westbound)';
    }
    if (!upstream.trim()) p.upstream = 'Name the upstream boundary station';
    if (!downstream.trim()) p.downstream = 'Name the downstream boundary station';
    else if (upstream.trim() === downstream.trim()) {
      p.downstream = 'The upstream and downstream stations must be different';
    }
    if (!detectors) p.detectors = 'Choose the detector CSV';
    if (!stations) p.stations = 'Choose the stations CSV';
    if (!(Number(windowRaw) > 0)) p.window = 'Window must be positive';
    if (!(Number(durationRaw) > 0)) p.duration = 'Duration must be positive';
    if (!(Number(warmupRaw) >= 0) || Number(warmupRaw) >= Number(durationRaw)) {
      p.warmup = 'Warm-up must be at least 0 and shorter than the duration';
    }
    return p;
  }, [
    name,
    bbox,
    bearing,
    upstream,
    downstream,
    detectors,
    stations,
    windowRaw,
    durationRaw,
    warmupRaw,
  ]);

  /** What the button says, and whether it can be pressed at all. */
  const problem = PROBLEM_ORDER.map((f) => problems[f]).find((m) => m !== undefined) ?? null;

  // Launching a run leaves for the Runs view, so the last onboarding is
  // restored from the API (not from a copy in this browser) on the way back.
  useEffect(() => {
    const last = readLast();
    if (!last) return;
    let live = true;
    void (async () => {
      try {
        const fetched = await getCorridor(last.corridor_id);
        if (live) trackCorridor(fetched);
      } catch {
        writeLast(null); // the server no longer has it: forget it quietly
        return;
      }
      if (!last.run_id) return;
      try {
        const fetched = await getRun(last.run_id);
        if (live) trackRun(fetched);
      } catch {
        /* the run is gone; the panel simply offers a fresh launch */
      }
    })();
    return () => {
      live = false;
    };
  }, [trackCorridor, trackRun]);

  const pollCorridor = useCallback(async () => {
    const id = corridorIdRef.current;
    if (!id) return;
    try {
      setCorridor(await getCorridor(id));
    } catch {
      /* retried by usePoll; the rail status dot carries connectivity */
    }
  }, []);
  usePoll(
    pollCorridor,
    corridor && !isTerminal(corridor.status) && !authFailed ? CORRIDOR_POLL_MS : null,
  );

  const pollRun = useCallback(async () => {
    const id = runIdRef.current;
    if (!id) return;
    try {
      setRun(await getRun(id));
    } catch {
      /* retried by usePoll */
    }
  }, []);
  usePoll(pollRun, run && !isTerminal(run.status) && !authFailed ? RUN_POLL_MS : null);

  async function onboard(): Promise<void> {
    if (problem || 'error' in bbox) return;
    const form = new FormData();
    form.set('name', name);
    form.set(
      'bbox',
      `${bbox.bbox.south} ${bbox.bbox.west} ${bbox.bbox.north} ${bbox.bbox.east}`,
    );
    form.set('bearing_deg', String(bearing));
    form.set('upstream_station', upstream.trim());
    form.set('downstream_station', downstream.trim());
    form.set('window_s', String(Number(windowRaw)));
    form.set('t0_local', t0Local);
    form.set('duration_s', String(Number(durationRaw)));
    form.set('warmup_s', String(Number(warmupRaw)));
    if (detectors) form.set('detectors', detectors);
    if (stations) form.set('stations', stations);
    setBusy(true);
    try {
      const created = await createCorridor(form);
      trackCorridor(created);
      runIdRef.current = null;
      setRun(null);
      writeLast({ corridor_id: created.corridor_id });
      toast('info', `Onboarding ${created.name} — ${created.corridor_id}`);
    } catch (err) {
      toastError(err, 'Onboarding refused');
    } finally {
      setBusy(false);
    }
  }

  async function launchRun(): Promise<void> {
    if (!corridor?.scenario_id) return;
    setBusy(true);
    try {
      const created = await createRun({
        scenario_id: corridor.scenario_id,
        replicates: REPORT_SEEDS,
      });
      trackRun(await getRun(created.run_id));
      writeLast({ corridor_id: corridor.corridor_id, run_id: created.run_id });
      toast('ok', `Launched ${REPORT_SEEDS} seeds — ${created.run_id}`);
      navigate('/runs');
    } catch (err) {
      toastError(err, 'Launch refused');
    } finally {
      setBusy(false);
    }
  }

  async function reportAgainstObservations(): Promise<void> {
    if (!corridor?.observations_path || !run) return;
    setBusy(true);
    try {
      await createReport(
        [run.run_id],
        `${corridor.name} against its detectors`,
        undefined,
        corridor.observations_path,
      );
      toast('ok', 'Report requested — scored against the uploaded observations');
      navigate('/reports');
    } catch (err) {
      toastError(err, 'Report refused');
    } finally {
      setBusy(false);
    }
  }

  const writeBlocked = offline ? OFFLINE_WRITE_MESSAGE : undefined;
  const summary = corridor?.summary ?? null;

  return (
    <div className="view">
      <div className="view-title">
        Onboard Corridor{' '}
        <span className="count mono">
          {corridor
            ? `${corridor.name} · ${corridor.progress.stage ?? 'queued'}`
            : 'bbox + detectors + two stations'}
        </span>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Corridor</span>
        </div>
        <div className="panel-body row wrap">
          <div className="field">
            <label htmlFor="ob-name">Name</label>
            <input
              id="ob-name"
              className="input"
              style={{ minWidth: 220 }}
              placeholder="mndot_i94_wb_stpaul"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            {problems.name && <span className="hint-amber">{problems.name}</span>}
          </div>
          <div className="field">
            <label htmlFor="ob-bbox">Bounding box (S, W, N, E)</label>
            <input
              id="ob-bbox"
              className="input mono"
              style={{ minWidth: 320 }}
              placeholder="44.9425, -93.0990, 44.9613, -92.9612"
              value={bboxRaw}
              onChange={(e) => setBboxRaw(e.target.value)}
            />
            {bboxRaw.trim() !== '' && problems.bbox && (
              <span className="hint-amber">{problems.bbox}</span>
            )}
          </div>
          <div className="field">
            <label htmlFor="ob-bearing">Bearing (deg)</label>
            <input
              id="ob-bearing"
              className="input"
              type="number"
              min={0}
              max={360}
              style={{ width: 100 }}
              value={bearingRaw}
              onChange={(e) => setBearingRaw(e.target.value)}
            />
            {problems.bearing && <span className="hint-amber">{problems.bearing}</span>}
          </div>
          <div className="field">
            <label htmlFor="ob-up">Upstream station</label>
            <input
              id="ob-up"
              className="input mono"
              style={{ width: 130 }}
              placeholder="S1063"
              value={upstream}
              onChange={(e) => setUpstream(e.target.value)}
            />
            {problems.upstream && <span className="hint-amber">{problems.upstream}</span>}
          </div>
          <div className="field">
            <label htmlFor="ob-down">Downstream station</label>
            <input
              id="ob-down"
              className="input mono"
              style={{ width: 130 }}
              placeholder="S97"
              value={downstream}
              onChange={(e) => setDownstream(e.target.value)}
            />
            {problems.downstream && <span className="hint-amber">{problems.downstream}</span>}
          </div>
        </div>
        <div className="panel-body row wrap">
          <div className="field">
            <label htmlFor="ob-detectors">Detector CSV</label>
            <input
              id="ob-detectors"
              className="input"
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => setDetectors(e.target.files?.[0] ?? null)}
            />
            {problems.detectors && <span className="hint-amber">{problems.detectors}</span>}
          </div>
          <div className="field">
            <label htmlFor="ob-stations">Stations CSV</label>
            <input
              id="ob-stations"
              className="input"
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => setStations(e.target.files?.[0] ?? null)}
            />
            {problems.stations && <span className="hint-amber">{problems.stations}</span>}
          </div>
          <div className="field">
            <label htmlFor="ob-window">Window (s)</label>
            <input
              id="ob-window"
              className="input"
              type="number"
              min={1}
              style={{ width: 100 }}
              value={windowRaw}
              onChange={(e) => setWindowRaw(e.target.value)}
            />
            {problems.window && <span className="hint-amber">{problems.window}</span>}
          </div>
          <div className="field">
            <label htmlFor="ob-t0">Span start (local)</label>
            <input
              id="ob-t0"
              className="input mono"
              style={{ width: 90 }}
              value={t0Local}
              onChange={(e) => setT0Local(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="ob-duration">Duration (s)</label>
            <input
              id="ob-duration"
              className="input"
              type="number"
              min={1}
              style={{ width: 110 }}
              value={durationRaw}
              onChange={(e) => setDurationRaw(e.target.value)}
            />
            {problems.duration && <span className="hint-amber">{problems.duration}</span>}
          </div>
          <div className="field">
            <label htmlFor="ob-warmup">Warm-up (s)</label>
            <input
              id="ob-warmup"
              className="input"
              type="number"
              min={0}
              style={{ width: 110 }}
              value={warmupRaw}
              onChange={(e) => setWarmupRaw(e.target.value)}
            />
            {problems.warmup && <span className="hint-amber">{problems.warmup}</span>}
          </div>
          <div className="field">
            <label>&nbsp;</label>
            <button
              className="btn primary"
              onClick={() => void onboard()}
              disabled={problem !== null || busy || offline}
              title={writeBlocked ?? problem ?? undefined}
            >
              Onboard corridor
            </button>
          </div>
          <div className="field">
            <label>&nbsp;</label>
            <span className="small muted">
              {writeBlocked ??
                problem ??
                'Downloads the map extract, builds the network and derives the demand.'}
            </span>
          </div>
        </div>
      </div>

      {corridor && (
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">Progress</span>
            <StatusChip status={corridor.status} />
            <span className="mono small muted">
              {corridor.progress.stage ?? 'queued'} · {corridor.progress.completed_stages}/
              {corridor.progress.total_stages} stages
            </span>
            <span className="spacer" />
            <span className="mono small muted">{corridor.corridor_id}</span>
          </div>
          {corridor.error && (
            <div className="panel-body">
              <p className="small">
                Onboarding failed at the <b>{corridor.progress.stage}</b> stage. Nothing was
                installed.
              </p>
              <pre className="mono small">{corridor.error}</pre>
            </div>
          )}
        </div>
      )}

      {summary && corridor && (
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">What the onboarding found</span>
            <span className="mono small muted">config_hash {corridor.config_hash}</span>
          </div>
          <div className="panel-body">
            <p className="small">
              The corridor follows a {formatDistKm(summary.chain_length_m)} chain of{' '}
              {summary.n_chain_edges} map edges. {summary.n_ramps} interchange ramps were
              discovered beside it, and the entry inflow peaks at{' '}
              {formatNumber(summary.inflow_peak_veh_h, 0)} veh/h.
            </p>

            <dl className="fact-list">
              {summary.lanes_profile.map(([x0, x1, lanes]) => (
                <div className="fact" key={`${x0}-${x1}`}>
                  <dt>
                    {formatDistKm(x0)} – {formatDistKm(x1)}
                  </dt>
                  <dd>{lanes} lanes</dd>
                </div>
              ))}
            </dl>

            <div className="table-wrap">
              <table className="data" aria-label="stations placed">
                <thead>
                  <tr>
                    <th>station</th>
                    <th>x on the corridor</th>
                    <th>offset from centreline</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.stations_placed.map((s) => (
                    <tr key={s.station}>
                      <td className="mono">{s.station}</td>
                      <td className="mono">{formatDistKm(s.x_m)}</td>
                      <td className="mono">{formatNumber(s.offset_m, 1)} m</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {summary.stations_rejected.length > 0 && (
              <p className="small">
                {summary.stations_rejected.map((s) => (
                  <span key={s.station}>
                    {s.station} sits {formatNumber(s.offset_m, 0)} m from the centreline — another
                    carriageway or another road. Its counts are not compared with this corridor.{' '}
                  </span>
                ))}
              </p>
            )}

            <div className="table-wrap">
              <table className="data" aria-label="ramp demand">
                <thead>
                  <tr>
                    <th>ramp</th>
                    <th>x</th>
                    <th>peak</th>
                    <th>from</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.ramps.map((r) => (
                    <tr key={`${r.name}-${r.x_m}`}>
                      <td>
                        {r.kind === 'on' ? 'entrance' : 'exit'} {r.name}
                      </td>
                      <td className="mono">{formatDistKm(r.x_m)}</td>
                      <td className="mono">
                        {r.unit === 'veh/h'
                          ? `${formatNumber(r.peak, 0)} veh/h`
                          : `${formatNumber(r.peak * 100, 1)}% diverging`}
                      </td>
                      <td className="mono small">
                        {r.station ? `detector ${r.station}` : r.method.replace(/_/g, ' ')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {summary.residuals.map((r, i) => (
              <p className="small hint-amber" key={`residual-${i}`}>
                Between {String(r.from)} and {String(r.to)} an average of{' '}
                {formatNumber(Number(r.mean_residual_veh_h), 0)} veh/h could not be assigned to any
                ramp of the needed kind. It is carried into the next bracket and recorded, not
                smeared over the ramps.
              </p>
            ))}
            {summary.zeroed_ramps.map((z) => (
              <p className="small muted" key={z}>
                Zeroed: {z}. Its traffic is already inside the nearest station count.
              </p>
            ))}
            {summary.unmatched_detectors.length > 0 && (
              <p className="small muted">
                Ramp detectors matched to no discovered ramp:{' '}
                {summary.unmatched_detectors.join(', ')}. Their flow stays in the conservation
                term.
              </p>
            )}

            <details>
              <summary className="small muted">Plain-text summary</summary>
              <pre className="mono small">{summary.lines.join('\n')}</pre>
            </details>
          </div>
          <div className="panel-body row wrap">
            <button
              className="btn primary"
              onClick={() => void launchRun()}
              disabled={!corridor.scenario_id || busy || offline}
              title={writeBlocked}
            >
              Run {REPORT_SEEDS} seeds
            </button>
            <button
              className="btn"
              onClick={() => void reportAgainstObservations()}
              disabled={
                run?.status !== 'done' || !corridor.observations_path || busy || offline
              }
              title={
                writeBlocked ??
                (run?.status === 'done'
                  ? undefined
                  : 'A finished run is what a report is scored from')
              }
            >
              Report against observations
            </button>
            <span className="small muted">
              {run
                ? `Run ${run.run_id}: ${run.status} (${run.progress.completed_replicates}` +
                  `/${run.progress.total_replicates} seeds)`
                : `Onboarding is not validation — ${REPORT_SEEDS} seeds, then a report scored ` +
                  'against the detectors you uploaded.'}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
