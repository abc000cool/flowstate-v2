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
 * Advanced holds what an arbitrary export needs and a tidy one does not: the
 * `column_map` naming the upload's own columns, an `IDMCalibration` the fleet
 * is drawn from (a path on the *server*, inside its allow-listed roots), and
 * the provenance string recorded on the observations artifact. Each is sent
 * only when it is filled in.
 *
 * `GET /corridors` lists the onboardings this server holds, so a corridor
 * built in another session can be picked up and run or reported on: its
 * scenario and its observations live on the server, and nothing but the two
 * ids is kept in this browser.
 *
 * Writes never fall back to the in-browser demo backend (`assertWritable`):
 * an onboarding accepted by this browser would be a corridor calibrated
 * against nothing. */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ApiError,
  createCorridor,
  createReport,
  createRun,
  getCorridor,
  getRun,
  isMockActive,
  listCorridors,
  OFFLINE_INFLIGHT_MESSAGE,
  OFFLINE_WRITE_MESSAGE,
} from '../api/client';
import type { CorridorOut, CorridorProgress, CorridorRow, RunDetail } from '../api/types';
import { useAppState } from '../components/AppContext';
import { StatusChip } from '../components/bits';
import { Icon } from '../components/icons';
import { PageHeader } from '../components/PageHeader';
import { SplitAuditTable, isSplitDefect } from '../components/SplitAuditTable';
import { toast, toastError } from '../components/toast';
import { Callout } from '../components/ui/Callout';
import { HashValue } from '../components/ui/CopyButton';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorCallout, isShellError } from '../components/ui/ErrorCallout';
import { SkeletonRows } from '../components/ui/Skeleton';
import { formatDistKm, formatNumber } from '../lib/format';
import { useAuthFailed, useOfflineFallback, usePoll } from '../lib/hooks';

const CORRIDOR_POLL_MS = 2000;
const RUN_POLL_MS = 3000;
/** How often the corridor history (`GET /corridors`) is re-read. Slower than
 * the job poll: it changes only when an onboarding starts or finishes. */
const HISTORY_POLL_MS = 5000;
/** Retry interval once `GET /corridors` has answered 404 (a service older
 * than the list endpoint). Watched for an upgrade, not hammered. */
const HISTORY_RETRY_MS = 60_000;

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

/** The canonical detector fields the loader names
 * (`calibration.loaders.detector_csv`, docs/CONTRACTS.md "Detector
 * observations"), each with the Advanced input that maps it onto whatever the
 * uploaded export calls it. Only the ones filled in are sent, as the
 * `column_map` JSON object `POST /corridors` accepts; an empty form sends no
 * mapping at all and the loader reads the canonical spellings. */
const COLUMN_FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: 'timestamp', label: 'Timestamp column', placeholder: 'timestamp' },
  { key: 'station', label: 'Station column', placeholder: 'station' },
  { key: 'flow', label: 'Flow column', placeholder: 'flow_veh_h' },
  { key: 'occupancy', label: 'Occupancy column', placeholder: 'occupancy_pct' },
  { key: 'speed', label: 'Speed column', placeholder: 'speed_ms' },
  { key: 'lanes', label: 'Lanes column', placeholder: 'lanes' },
];

/** The filled-in column names as the `column_map` request field, or null when
 * none is set (the field is then omitted rather than sent as `{}`). */
export function columnMapField(raw: Record<string, string>): string | null {
  const entries = COLUMN_FIELDS.map((f) => [f.key, (raw[f.key] ?? '').trim()] as const).filter(
    ([, value]) => value !== '',
  );
  return entries.length === 0 ? null : JSON.stringify(Object.fromEntries(entries));
}

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

/** The onboarding stages in their documented order (`CorridorProgress.stage`,
 * api/types.ts). The stepper draws only what the server reported. */
export const ONBOARD_STAGES = ['extract', 'network', 'observations', 'demand', 'install'] as const;

type StageState = 'done' | 'current' | 'failed' | 'pending';

const STAGE_WORD: Record<StageState, string> = {
  done: 'complete',
  current: 'in progress',
  failed: 'failed',
  pending: 'not started',
};

/** Per-stage states from the server's own counters, or null when the API
 * names a stage outside the documented list (the panel then shows the text
 * alone: progress is never invented). */
export function stageStates(status: string, progress: CorridorProgress): StageState[] | null {
  const stage = progress.stage;
  const done = Math.max(0, Math.min(ONBOARD_STAGES.length, progress.completed_stages));
  if (stage !== null && stage !== 'done' && !(ONBOARD_STAGES as readonly string[]).includes(stage)) {
    return null;
  }
  const at = stage === null || stage === 'done' ? -1 : ONBOARD_STAGES.indexOf(stage as never);
  return ONBOARD_STAGES.map((_, i) => {
    if (i < done) return 'done';
    if (i === at) return status === 'failed' ? 'failed' : 'current';
    return 'pending';
  });
}

function StageStepper({ states }: { states: StageState[] }): JSX.Element {
  return (
    <ol className="stage-steps" aria-label="onboarding stages">
      {ONBOARD_STAGES.map((name, i) => {
        const st = states[i];
        return (
          <li
            key={name}
            className={`stage-step ${st}`}
            aria-current={st === 'current' ? 'step' : undefined}
          >
            <span className="stage-dot" aria-hidden="true">
              {st === 'done' && <Icon name="check" size={12} strokeWidth={2.5} />}
              {st === 'failed' && <Icon name="x" size={12} strokeWidth={2.5} />}
            </span>
            <span className="stage-label">{name}</span>
            <span className="visually-hidden">, {STAGE_WORD[st]}</span>
          </li>
        );
      })}
    </ol>
  );
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
  // Advanced: the export's own column names, a driver population to draw
  // from, and where the detector data came from. All optional, all sent only
  // when filled in.
  const [columns, setColumns] = useState<Record<string, string>>({});
  const [idmCalibration, setIdmCalibration] = useState('');
  const [source, setSource] = useState('');
  // The build stage's two switches (2026-09-24), both on by default as on the
  // API and the CLI: always sent, since "unset" means "on" to the API and a
  // tester who unticked one expects the request to say so.
  const [rampGuessing, setRampGuessing] = useState(true);
  const [splitFixes, setSplitFixes] = useState(true);

  const [corridor, setCorridor] = useState<CorridorOut | null>(null);
  /** `GET /corridors` — the onboardings this server holds; null until it has
   * answered. */
  const [history, setHistory] = useState<CorridorRow[] | null>(null);
  /** False once the service answered 404 to `GET /corridors` (an API older
   * than the list endpoint): the panel says so instead of claiming the server
   * has onboarded nothing. */
  const [historyListed, setHistoryListed] = useState(true);
  /** Whether the list currently held in `history` came from the in-browser
   * demo backend rather than a server. */
  const [historyDemo, setHistoryDemo] = useState(false);
  /** The last `GET /corridors` failure the shell does not already report. */
  const [historyError, setHistoryError] = useState<unknown>(null);
  /** Fields the operator has left at least once: their problems turn from a
   * neutral hint into a warning (§9.2). The text is the same either way. */
  const [touched, setTouched] = useState<Partial<Record<ProblemField, true>>>({});
  const touch = (f: ProblemField) => (): void =>
    setTouched((t) => (t[f] ? t : { ...t, [f]: true }));
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

  // The corridors this server has onboarded. Without it a corridor built in
  // another session (or another browser) is unreachable from here: its
  // scenario_id and observations_path live only on the server, and the two
  // follow-on actions need both.
  const loadHistory = useCallback(async () => {
    // capture the source before the call: a list answered by the in-browser
    // backend is not this server's history and must not be shown as one
    const demo = isMockActive();
    try {
      const rows = await listCorridors();
      setHistory(rows);
      setHistoryDemo(demo);
      setHistoryListed(true);
      setHistoryError(null);
    } catch (err) {
      // 404 = a service older than GET /corridors; anything else is retried,
      // and stated in the panel unless the shell already says it
      if (err instanceof ApiError && err.status === 404) setHistoryListed(false);
      else setHistoryError(isShellError(err) ? null : err);
    }
  }, []);
  usePoll(loadHistory, authFailed ? null : historyListed ? HISTORY_POLL_MS : HISTORY_RETRY_MS);

  /** Make a listed corridor the one this view acts on.
   *
   * The row is re-read in full (`GET /corridors/{id}`): a listing carries no
   * summary, and the panel below states what the onboarding found, never a
   * reconstruction of it. A run this browser launched for that same corridor
   * is picked up with it, so "Report against observations" is offered exactly
   * when there is a finished run to score. */
  async function selectCorridor(row: CorridorRow): Promise<void> {
    setBusy(true);
    try {
      const fetched = await getCorridor(row.corridor_id);
      trackCorridor(fetched);
      const last = readLast();
      const runId = last?.corridor_id === row.corridor_id ? last.run_id : undefined;
      runIdRef.current = null;
      setRun(null);
      writeLast({ corridor_id: row.corridor_id, run_id: runId });
      if (runId) {
        try {
          trackRun(await getRun(runId));
        } catch {
          /* the run is gone; the panel simply offers a fresh launch */
        }
      }
    } catch (err) {
      toastError(err, 'Corridor');
    } finally {
      setBusy(false);
    }
  }

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
    // Advanced, all optional: an empty one is left out of the request rather
    // than sent blank — `{}` and `""` are not what "unset" means to the API.
    const columnMap = columnMapField(columns);
    if (columnMap) form.set('column_map', columnMap);
    if (idmCalibration.trim()) form.set('idm_calibration', idmCalibration.trim());
    if (source.trim()) form.set('source', source.trim());
    form.set('ramp_guessing', rampGuessing ? 'true' : 'false');
    form.set('split_fixes', splitFixes ? 'true' : 'false');
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

  /** Why the write controls are disabled. While one of this view's own writes
   * is still open, `/health` going quiet does not mean the request was never
   * sent — a corridor onboarding is precisely the request that keeps the
   * server from answering the probe (see `OFFLINE_INFLIGHT_MESSAGE`). */
  const writeBlocked = offline
    ? busy
      ? OFFLINE_INFLIGHT_MESSAGE
      : OFFLINE_WRITE_MESSAGE
    : undefined;
  const summary = corridor?.summary ?? null;
  const states = corridor ? stageStates(corridor.status, corridor.progress) : null;

  /** A field's problem line: neutral until the field has been left once, a
   * warning after. Same text either way; the control points at it. */
  const problemLine = (f: ProblemField, show = true): ReactNode => {
    const msg = problems[f];
    if (!msg || !show) return null;
    return (
      <span id={`ob-${f}-problem`} className={touched[f] ? 'hint-amber' : 'field-hint'}>
        {msg}
      </span>
    );
  };
  /** aria-invalid / aria-describedby for the control a problem belongs to. */
  const problemAttrs = (
    f: ProblemField,
    show = true,
  ): { 'aria-invalid'?: true; 'aria-describedby'?: string } => {
    if (!problems[f] || !show) return {};
    return {
      'aria-invalid': touched[f] ? true : undefined,
      'aria-describedby': `ob-${f}-problem`,
    };
  };
  const bboxShown = bboxRaw.trim() !== '';

  return (
    <div className="view">
      <PageHeader
        title="Onboard a corridor"
        documentTitle="Onboard corridor"
        meta={
          corridor ? (
            <span className="mono">
              {corridor.name} · {corridor.progress.stage ?? 'queued'}
            </span>
          ) : undefined
        }
        description="Build a scenario from an OpenStreetMap extract and a detector export. Onboarding is not validation."
      />

      <section className="panel" aria-labelledby="ob-form-title">
        <div className="panel-head">
          <h2 className="panel-title" id="ob-form-title">
            New corridor
          </h2>
        </div>
        <div className="panel-body onboard-form">
          <div className="form-section">
            <h3>Corridor</h3>
            <div className="form-grid">
              <div className="field">
                <label htmlFor="ob-name">Name</label>
                <input
                  id="ob-name"
                  className="input mono"
                  placeholder="mndot_i94_wb_stpaul"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onBlur={touch('name')}
                  {...problemAttrs('name')}
                />
                {problemLine('name')}
              </div>
              <div className="field span-2">
                <label htmlFor="ob-bbox">Bounding box (S, W, N, E)</label>
                <input
                  id="ob-bbox"
                  className="input mono"
                  placeholder="44.9425, -93.0990, 44.9613, -92.9612"
                  value={bboxRaw}
                  onChange={(e) => setBboxRaw(e.target.value)}
                  onBlur={touch('bbox')}
                  {...problemAttrs('bbox', bboxShown)}
                />
                {problemLine('bbox', bboxShown)}
              </div>
              <div className="field">
                <label htmlFor="ob-bearing">Bearing (deg)</label>
                <input
                  id="ob-bearing"
                  className="input"
                  type="number"
                  min={0}
                  max={360}
                  value={bearingRaw}
                  onChange={(e) => setBearingRaw(e.target.value)}
                  onBlur={touch('bearing')}
                  {...problemAttrs('bearing')}
                />
                {problemLine('bearing')}
              </div>
            </div>
          </div>

          <div className="form-section">
            <h3>Boundary stations</h3>
            <div className="form-grid">
              <div className="field">
                <label htmlFor="ob-up">Upstream station</label>
                <input
                  id="ob-up"
                  className="input mono"
                  placeholder="S1063"
                  value={upstream}
                  onChange={(e) => setUpstream(e.target.value)}
                  onBlur={touch('upstream')}
                  {...problemAttrs('upstream')}
                />
                {problemLine('upstream')}
              </div>
              <div className="field">
                <label htmlFor="ob-down">Downstream station</label>
                <input
                  id="ob-down"
                  className="input mono"
                  placeholder="S97"
                  value={downstream}
                  onChange={(e) => setDownstream(e.target.value)}
                  onBlur={touch('downstream')}
                  {...problemAttrs('downstream')}
                />
                {problemLine('downstream')}
              </div>
            </div>
          </div>

          <div className="form-section">
            <h3>Detector data</h3>
            <div className="form-grid">
              <div className="field span-2">
                <label htmlFor="ob-detectors">Detector CSV</label>
                <input
                  id="ob-detectors"
                  className="input"
                  type="file"
                  accept=".csv,text/csv"
                  onChange={(e) => setDetectors(e.target.files?.[0] ?? null)}
                  onBlur={touch('detectors')}
                  {...problemAttrs('detectors')}
                />
                {problemLine('detectors')}
              </div>
              <div className="field span-2">
                <label htmlFor="ob-stations">Stations CSV</label>
                <input
                  id="ob-stations"
                  className="input"
                  type="file"
                  accept=".csv,text/csv"
                  onChange={(e) => setStations(e.target.files?.[0] ?? null)}
                  onBlur={touch('stations')}
                  {...problemAttrs('stations')}
                />
                {problemLine('stations')}
              </div>
            </div>
          </div>

          <div className="form-section">
            <h3>Time span</h3>
            <div className="form-grid">
              <div className="field">
                <label htmlFor="ob-window">Window (s)</label>
                <input
                  id="ob-window"
                  className="input"
                  type="number"
                  min={1}
                  value={windowRaw}
                  onChange={(e) => setWindowRaw(e.target.value)}
                  onBlur={touch('window')}
                  {...problemAttrs('window')}
                />
                {problemLine('window')}
              </div>
              <div className="field">
                <label htmlFor="ob-t0">Span start (local)</label>
                <input
                  id="ob-t0"
                  className="input mono"
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
                  value={durationRaw}
                  onChange={(e) => setDurationRaw(e.target.value)}
                  onBlur={touch('duration')}
                  {...problemAttrs('duration')}
                />
                {problemLine('duration')}
              </div>
              <div className="field">
                <label htmlFor="ob-warmup">Warm-up (s)</label>
                <input
                  id="ob-warmup"
                  className="input"
                  type="number"
                  min={0}
                  value={warmupRaw}
                  onChange={(e) => setWarmupRaw(e.target.value)}
                  onBlur={touch('warmup')}
                  {...problemAttrs('warmup')}
                />
                {problemLine('warmup')}
              </div>
            </div>
          </div>

          <details className="disclosure">
            <summary>
              Advanced — detector column names, driver population, provenance, map fixes
            </summary>
            <div className="disclosure-body">
              <p className="field-help">
                An export whose columns are not the canonical ones is read through a{' '}
                <span className="mono">column_map</span>: name the upload&rsquo;s own column
                beside each field it carries. Blank fields are left out of the request.
              </p>
              <div className="form-grid">
                {COLUMN_FIELDS.map((f) => (
                  <div className="field" key={f.key}>
                    <label htmlFor={`ob-col-${f.key}`}>{f.label}</label>
                    <input
                      id={`ob-col-${f.key}`}
                      className="input mono"
                      placeholder={f.placeholder}
                      value={columns[f.key] ?? ''}
                      onChange={(e) =>
                        setColumns((prev) => ({ ...prev, [f.key]: e.target.value }))
                      }
                    />
                  </div>
                ))}
              </div>
              <div className="form-grid">
                <div className="field span-2">
                  <label htmlFor="ob-idm">IDM calibration (server path)</label>
                  <input
                    id="ob-idm"
                    className="input mono"
                    placeholder="artifacts/idm_i24_capacity.json"
                    value={idmCalibration}
                    onChange={(e) => setIdmCalibration(e.target.value)}
                    aria-describedby="ob-idm-help"
                  />
                  <span className="field-help" id="ob-idm-help">
                    A driver population the fleet is drawn from. Read by the server, so it must
                    lie inside the server&rsquo;s allow-listed roots (its artifacts/ or data/
                    directories, or the results root) — a path on this machine means nothing
                    there.
                  </span>
                </div>
                <div className="field span-2">
                  <label htmlFor="ob-source">Source</label>
                  <input
                    id="ob-source"
                    className="input"
                    placeholder="MnDOT IRIS 30-second archive, 2026-04-14"
                    value={source}
                    onChange={(e) => setSource(e.target.value)}
                    aria-describedby="ob-source-help"
                  />
                  <span className="field-help" id="ob-source-help">
                    Recorded on the observations artifact as the provenance of these detector
                    counts; it travels into every report scored against them.
                  </span>
                </div>
              </div>
              {/* the two build switches of 2026-09-24, on by default as on the
                  API and the CLI; both faults were found on I-94 WB St. Paul */}
              <div className="form-grid">
                <div className="field span-2">
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={rampGuessing}
                      onChange={(e) => setRampGuessing(e.target.checked)}
                    />
                    Guess acceleration lanes (netconvert ramps.guess)
                  </label>
                  <span className="field-help">
                    OSM usually lacks acceleration lanes, so entrances starve without them; found
                    on I-94 WB St. Paul.
                  </span>
                </div>
                <div className="field span-2">
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={splitFixes}
                      onChange={(e) => setSplitFixes(e.target.checked)}
                    />
                    Fix exits compiled on the wrong side (split audit)
                  </label>
                  <span className="field-help">
                    A diverge compiled on the wrong side traps through traffic in a lane that
                    leads only to the exit; found on I-94 WB St. Paul.
                  </span>
                </div>
              </div>
            </div>
          </details>
        </div>
        <div className="panel-foot form-actions">
          {/* the hint is the button's *description*, never its name: a
              control announced as "Name: letters, digits, - and _" tells a
              screen-reader user nothing about what pressing it does */}
          <span className="form-actions-summary" id="onboard-hint">
            {writeBlocked ??
              problem ??
              'Downloads the map extract, builds the network and derives the demand.'}
          </span>
          <div className="form-actions-buttons">
            <button
              type="button"
              className="btn primary"
              onClick={() => void onboard()}
              disabled={problem !== null || busy || offline}
              aria-describedby="onboard-hint"
            >
              Onboard corridor
            </button>
          </div>
        </div>
      </section>

      {corridor && (
        <section className="panel" aria-labelledby="ob-progress-title">
          <div className="panel-head">
            <h2 className="panel-title" id="ob-progress-title">
              Progress
            </h2>
            <StatusChip status={corridor.status} />
            <span className="panel-sub mono">
              {corridor.progress.stage ?? 'queued'} · {corridor.progress.completed_stages}/
              {corridor.progress.total_stages} stages
            </span>
            <span className="spacer" />
            <span className="panel-sub mono">{corridor.corridor_id}</span>
          </div>
          {(states || corridor.error) && (
            <div className="panel-body stack">
              {states && <StageStepper states={states} />}
              {corridor.error && (
                <Callout
                  tone="danger"
                  role="status"
                  title={
                    <>
                      Onboarding failed at the <b>{corridor.progress.stage}</b> stage. Nothing was
                      installed.
                    </>
                  }
                >
                  <pre className="mono">{corridor.error}</pre>
                </Callout>
              )}
            </div>
          )}
        </section>
      )}

      {summary && corridor && (
        <section className="panel" aria-labelledby="ob-summary-title">
          <div className="panel-head">
            <h2 className="panel-title" id="ob-summary-title">
              What the onboarding found
            </h2>
            <span className="spacer" />
            {corridor.config_hash && (
              <span className="onboard-hash">
                <span className="panel-sub mono">config_hash</span>
                <HashValue value={corridor.config_hash} label="config hash" />
              </span>
            )}
          </div>
          <div className="panel-body stack">
            <p className="onboard-lead">
              The corridor follows a {formatDistKm(summary.chain_length_m)} chain of{' '}
              {summary.n_chain_edges} map edges. {summary.n_ramps} interchange ramps were
              discovered beside it, and the entry inflow peaks at{' '}
              {formatNumber(summary.inflow_peak_veh_h, 0)} veh/h.
            </p>

            <div className="grid-12 onboard-columns">
              <div className="col-span-6 stack">
                <h3 className="onboard-col-title">Network</h3>
                <div className="stack-sm">
                  <h4>Lane profile</h4>
                  <dl className="fact-list">
                    {summary.lanes_profile.map(([x0, x1, lanes]) => (
                      <div className="fact" key={`${x0}-${x1}`}>
                        <dt>
                          {formatDistKm(x0)} – {formatDistKm(x1)}
                        </dt>
                        <dd className="mono">{lanes} lanes</dd>
                      </div>
                    ))}
                  </dl>
                </div>

                <div className="stack-sm">
                  <h4>Stations placed</h4>
                  <div className="table-wrap">
                    <table className="data compact" aria-label="stations placed">
                      <thead>
                        <tr>
                          <th>Station</th>
                          <th className="num">Position</th>
                          <th className="num">Offset from centreline</th>
                        </tr>
                      </thead>
                      <tbody>
                        {summary.stations_placed.map((s) => (
                          <tr key={s.station}>
                            <td className="mono">{s.station}</td>
                            <td className="num">{formatDistKm(s.x_m)}</td>
                            <td className="num">{formatNumber(s.offset_m, 1)} m</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {summary.stations_rejected.length > 0 && (
                  <p className="onboard-note">
                    {summary.stations_rejected.map((s) => (
                      <span key={s.station}>
                        {s.station} sits {formatNumber(s.offset_m, 0)} m from the centreline —
                        another carriageway or another road. Its counts are not compared with this
                        corridor.{' '}
                      </span>
                    ))}
                  </p>
                )}

                {summary.lanes_compared !== undefined && summary.lanes_compared > 0 && (
                  <div className="stack-sm">
                    <p className="onboard-note">
                      lanes vs inventory:{' '}
                      {summary.lanes_compared - (summary.lane_mismatches?.length ?? 0)} of{' '}
                      {summary.lanes_compared} stations match
                    </p>
                    {(summary.lane_mismatches ?? []).map((m) => (
                      <p className="hint-amber" key={`lanes-${m.station}`}>
                        {m.station} at {formatDistKm(m.x_m)}: the map carries {m.compiled_lanes}{' '}
                        lanes, the inventory says {m.inventory_lanes} — {m.hint}.
                      </p>
                    ))}
                  </div>
                )}
              </div>

              <div className="col-span-6 stack">
                <h3 className="onboard-col-title">Demand</h3>
                <div className="stack-sm">
                  <h4>Ramp demand</h4>
                  <div className="table-wrap">
                    <table className="data compact" aria-label="ramp demand">
                      <thead>
                        <tr>
                          <th>Ramp</th>
                          <th className="num">Position</th>
                          <th className="num">Peak</th>
                          <th>Source</th>
                        </tr>
                      </thead>
                      <tbody>
                        {summary.ramps.map((r) => (
                          <tr key={`${r.name}-${r.x_m}`}>
                            <td>
                              {r.kind === 'on' ? 'entrance' : 'exit'} {r.name}
                            </td>
                            <td className="num">{formatDistKm(r.x_m)}</td>
                            <td className="num">
                              {r.unit === 'veh/h'
                                ? `${formatNumber(r.peak, 0)} veh/h`
                                : `${formatNumber(r.peak * 100, 1)}% diverging`}
                            </td>
                            <td className="mono secondary">
                              {r.station ? `detector ${r.station}` : r.method.replace(/_/g, ' ')}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {summary.residuals.map((r, i) => (
                  <p className="hint-amber" key={`residual-${i}`}>
                    Between {String(r.from)} and {String(r.to)} an average of{' '}
                    {formatNumber(Number(r.mean_residual_veh_h), 0)} veh/h could not be assigned to
                    any ramp of the needed kind. It is carried into the next bracket and recorded,
                    not smeared over the ramps.
                  </p>
                ))}
                {summary.zeroed_ramps.map((z) => (
                  <p className="onboard-note" key={z}>
                    Zeroed: {z}. Its traffic is already inside the nearest station count.
                  </p>
                ))}
                {summary.unmatched_detectors.length > 0 && (
                  <p className="onboard-note">
                    Ramp detectors matched to no discovered ramp:{' '}
                    {summary.unmatched_detectors.join(', ')}. Their flow stays in the conservation
                    term.
                  </p>
                )}
              </div>
            </div>

            {/* what the build stage ran (2026-09-24): the API's one-line
                `applied`, then the audit the fixes were derived from when it
                differs from the audit the scenario compiles — a defect count
                only; the table below is the audit that stands */}
            <div className="stack-sm">
              <h4>Split audit</h4>
              {summary.applied && <p className="onboard-note">applied: {summary.applied}</p>}
              {(() => {
                const before = summary.split_audit_before_fixes;
                if (
                  !before ||
                  JSON.stringify(before) === JSON.stringify(summary.split_audit ?? [])
                ) {
                  return null;
                }
                const defects = before.filter((f) => isSplitDefect(f.verdict)).length;
                return (
                  <p className="onboard-note">
                    before fixes: {defects} defect{defects === 1 ? '' : 's'}
                  </p>
                );
              })()}
              <SplitAuditTable findings={summary.split_audit} />
            </div>

            <details className="disclosure">
              <summary>Plain-text summary</summary>
              <div className="disclosure-body">
                <pre className="onboard-lines mono">{summary.lines.join('\n')}</pre>
              </div>
            </details>
          </div>
          <div className="panel-foot form-actions">
            <span className="form-actions-summary">
              {run
                ? `Run ${run.run_id}: ${run.status} (${run.progress.completed_replicates}` +
                  `/${run.progress.total_replicates} seeds)`
                : `Onboarding is not validation — ${REPORT_SEEDS} seeds, then a report scored ` +
                  'against the detectors you uploaded.'}
            </span>
            <div className="form-actions-buttons">
              <button
                type="button"
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
              <button
                type="button"
                className="btn primary"
                onClick={() => void launchRun()}
                disabled={!corridor.scenario_id || busy || offline}
                title={writeBlocked}
              >
                Run {REPORT_SEEDS} seeds
              </button>
            </div>
          </div>
        </section>
      )}

      <section className="panel" aria-labelledby="ob-history-title">
        <div className="panel-head">
          <h2 className="panel-title" id="ob-history-title">
            Corridors on this server
          </h2>
          {historyDemo && (
            <span className="tag demo" title="Not from the API — built-in demo data">
              DEMO DATA
            </span>
          )}
          <span className="spacer" />
          <span
            className="panel-sub"
            title={
              historyDemo
                ? 'The API is unreachable, so these rows come from the built-in demo backend. Nothing here was onboarded by a server.'
                : historyListed
                  ? 'GET /corridors, newest first. Picking one makes it the corridor the two actions above act on — its scenario and its observations live on the server, not in this browser.'
                  : 'This service answered 404 to GET /corridors, so corridors onboarded in other sessions cannot be listed here.'
            }
          >
            {historyDemo
              ? 'built-in demo data — onboarded by no server'
              : historyListed
                ? 'GET /corridors, newest first'
                : 'this service has no GET /corridors'}
          </span>
        </div>
        {!historyListed ? (
          <div className="panel-body">
            <Callout tone="neutral" role="note">
              This service answered 404 to GET /corridors, so corridors onboarded in other sessions
              cannot be listed here.
            </Callout>
          </div>
        ) : history === null && historyError !== null ? (
          <div className="panel-body">
            <ErrorCallout
              error={historyError}
              title="Could not read the corridor history."
              onRetry={() => void loadHistory()}
            />
          </div>
        ) : history === null && !authFailed ? (
          // first load: 3 placeholder rows, outside the labelled table so the
          // table only ever holds what the server listed
          <div className="table-wrap" aria-busy="true">
            <table className="data" aria-hidden="true">
              <tbody>
                <SkeletonRows rows={3} columns={4} />
              </tbody>
            </table>
          </div>
        ) : history === null ? (
          <div className="panel-body">
            <EmptyState
              title="No corridor history."
              description="It is read from GET /corridors once the API accepts the key."
            />
          </div>
        ) : (
          <div className="table-wrap scroll-y">
            <table className="data" aria-label="corridors on this server">
              <thead>
                <tr>
                  <th>Corridor</th>
                  <th>Status</th>
                  <th>Started</th>
                  <th>
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {history.length === 0 && (
                  <tr>
                    <td colSpan={4}>
                      <EmptyState
                        compact
                        title="No corridors onboarded yet."
                        description="Corridors appear here after an onboarding finishes."
                      />
                    </td>
                  </tr>
                )}
                {history.map((row) => (
                  <tr
                    key={row.corridor_id}
                    className={corridor?.corridor_id === row.corridor_id ? 'selected' : undefined}
                  >
                    <td className="mono id-cell" title={row.corridor_id}>
                      {row.name}
                    </td>
                    <td>
                      <StatusChip status={row.status} />
                    </td>
                    <td className="mono secondary">
                      {row.created_at.replace('T', ' ').slice(0, 19)} UTC
                    </td>
                    <td className="row-action">
                      <button
                        type="button"
                        className="btn sm"
                        // the status is part of the name: two rows for the
                        // same corridor differ only by how their onboarding
                        // ended, and "use walk2_i94" alone cannot tell a
                        // screen-reader user which one is the failed attempt
                        aria-label={`use ${row.name} (${row.status})`}
                        disabled={busy || corridor?.corridor_id === row.corridor_id}
                        title={
                          corridor?.corridor_id === row.corridor_id
                            ? 'Already the selected corridor'
                            : row.scenario_id
                              ? 'Load this corridor: its summary, and the run and report actions'
                              : 'Load this corridor — it installed no scenario, so there is nothing to run on it'
                        }
                        onClick={() => void selectCorridor(row)}
                      >
                        Use
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
