/** Scenarios: preset cards with schematic thumbnails, YAML upload drop-zone,
 * and a Compose form building a ScenarioConfig for POST /scenarios.
 *
 * Two honesty rules live here. (1) Demo data is labelled at the card, not just
 * in the shell banner: an offline fallback card carries a DEMO badge and no
 * config hash (its hash exists in no server), and the library keeps polling
 * until the real API answers so the demo list is replaced in place. (2) The
 * composer models a subset of ScenarioConfig; every field it does not model is
 * carried through from the loaded scenario unchanged and listed under the
 * form, so "load in composer → create" cannot silently run a different
 * scenario than the one named. */

import yaml from 'js-yaml';
import { useCallback, useRef, useState, type DragEvent, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  createRun,
  createScenario,
  isMockEnv,
  isOfflineFallback,
  listPresetScenarios,
  listScenarios,
  OFFLINE_WRITE_MESSAGE,
} from '../api/client';
import {
  ensureStored,
  isPreset,
  itemKey,
  mergeLibrary,
  type LibraryItem,
} from '../lib/library';

/** Whether to badge a card PRESET. The API now stamps its own marker —
 * `PresetOut.preset` is always true, `ScenarioOut.preset` always false, so a
 * stored scenario created from a preset is correctly *not* badged — and that
 * flag wins whenever it is present. Without it (an older service) the badge
 * falls back to the endpoint the item was loaded from. */
const showsPresetBadge = (s: LibraryItem): boolean =>
  typeof s.preset === 'boolean' ? s.preset : isPreset(s);
import type { CreateRunRequest, Network, OSMNetwork, ScenarioConfig } from '../api/types';
import { useAppState } from '../components/AppContext';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { GuidedFirstRun } from '../components/GuidedFirstRun';
import { Icon } from '../components/icons';
import { PageHeader } from '../components/PageHeader';
import { SchematicThumb } from '../components/schematics';
import { toast, toastError } from '../components/toast';
import { Callout } from '../components/ui/Callout';
import { HashValue } from '../components/ui/CopyButton';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorCallout, isShellError } from '../components/ui/ErrorCallout';
import { Skeleton } from '../components/ui/Skeleton';
import {
  FOCUS_CONTENT_STATE,
  useAuthFailed,
  useBusyFocus,
  useOfflineFallback,
  usePoll,
} from '../lib/hooks';
import {
  clampField,
  clampInt,
  describeSimMinutes,
  MAX_DURATION_S,
  MAX_LANES,
  MAX_REPLICATES,
  MAX_SEED,
  MIN_DURATION_S,
  MIN_SEED,
  simMinutes,
  warmupProblem,
} from '../lib/limits';
import { MIN_REPLICATES } from '../lib/metrics';

const CONTROLLERS = ['follower_stopper', 'pi_saturation', 'jad', 'none'] as const;
const LIBRARY_POLL_MS = 3000;
/** Inflow the composer writes for a corridor it builds from scratch [veh/s]. */
const DEFAULT_INFLOW_VEH_S = 0.55;

/** Where the currently displayed library came from. */
type DemoSource = 'none' | 'env' | 'offline';

/** The network kinds the composer can hold. `osm` is **read-only**: an
 * imported corridor is an onboarding artifact (a map extract, the edge chain
 * along it, its ramps and its measured boundary), none of which this form
 * models — coercing one to a 10 km single-lane corridor, as it used to,
 * silently threw the corridor away. */
type ComposeKind = 'ring' | 'corridor' | 'osm';

interface ComposeState {
  name: string;
  kind: ComposeKind;
  length_m: number;
  lanes: number;
  circumference_m: number;
  n_vehicles: number;
  model: 'IDM' | 'EIDM';
  penetration: number; // percent 0–30
  compliance: number; // percent 10–100
  controller: (typeof CONTROLLERS)[number];
  vsl: boolean;
  duration_s: number;
  replicates: number;
}

const DEFAULT_COMPOSE: ComposeState = {
  name: 'custom_corridor',
  kind: 'corridor',
  length_m: 10000,
  lanes: 1,
  circumference_m: 230,
  n_vehicles: 22,
  model: 'IDM',
  penetration: 5,
  compliance: 80,
  controller: 'follower_stopper',
  vsl: false,
  duration_s: 1200,
  replicates: 20,
};

/** Build the POST body, carrying every field the form does not model over
 * from `base` (the scenario the composer was loaded from) unchanged. */
function composeToConfig(c: ComposeState, base: ScenarioConfig | null): ScenarioConfig {
  const baseNet = base?.network;
  const network: Network =
    // an imported corridor's network block is passed through byte for byte
    c.kind === 'osm' && baseNet?.kind === 'osm'
      ? baseNet
      : c.kind === 'ring'
        ? baseNet?.kind === 'ring'
          ? { ...baseNet, circumference_m: c.circumference_m, n_vehicles: c.n_vehicles }
          : { kind: 'ring', circumference_m: c.circumference_m, n_vehicles: c.n_vehicles }
        : baseNet?.kind === 'corridor'
          ? { ...baseNet, length_m: c.length_m, lanes: c.lanes }
          : {
              kind: 'corridor',
              length_m: c.length_m,
              lanes: c.lanes,
              inflow: [[0, DEFAULT_INFLOW_VEH_S]],
            };
  return {
    ...(base ?? {}),
    name: c.name,
    tier: base?.tier ?? 'micro',
    network,
    fleet: { ...(base?.fleet ?? {}), model: c.model },
    av: {
      ...(base?.av ?? {}),
      penetration: c.penetration / 100,
      compliance: c.compliance / 100,
      controller: c.controller === 'none' ? null : c.controller,
      vsl: c.vsl ? (base?.av.vsl ?? 'threshold') : null,
    },
    sim: { ...(base?.sim ?? {}), duration_s: c.duration_s },
    seed: base?.seed ?? 42,
    replicates: c.replicates,
  };
}

/** Config paths the form does not model: `carried` ride along unchanged,
 * `dropped` cannot survive the requested network-kind change. */
function passthroughFields(
  base: ScenarioConfig | null,
  kind: ComposeKind,
): { carried: string[]; dropped: string[] } {
  if (!base) return { carried: [], dropped: [] };
  const carried: string[] = [];
  const dropped: string[] = [];
  // nothing of an OSM network is modelled here, so every field of it is
  // carried — the form never rewrites the block
  const netModelled =
    kind === 'osm'
      ? ['kind']
      : kind === 'ring'
        ? ['kind', 'circumference_m', 'n_vehicles']
        : ['kind', 'length_m', 'lanes'];
  const net = base.network as unknown as Record<string, unknown>;
  const netExtras = Object.keys(net).filter(
    (k) => k !== 'kind' && !netModelled.includes(k) && net[k] != null,
  );
  if (base.network.kind === kind) {
    carried.push(...netExtras.map((k) => `network.${k}`));
  } else {
    dropped.push(`network: ${base.network.kind} → ${kind}`);
    dropped.push(...netExtras.map((k) => `network.${k}`));
  }
  const groups: [string, Record<string, unknown>, string[]][] = [
    ['fleet', base.fleet as unknown as Record<string, unknown>, ['model']],
    [
      'av',
      base.av as unknown as Record<string, unknown>,
      ['penetration', 'compliance', 'controller', 'vsl'],
    ],
    ['sim', base.sim as unknown as Record<string, unknown>, ['duration_s']],
  ];
  for (const [group, obj, modelled] of groups) {
    for (const k of Object.keys(obj ?? {})) {
      const v = obj[k];
      if (modelled.includes(k) || v === null || v === undefined) continue;
      if (typeof v === 'object' && Object.keys(v as object).length === 0) continue;
      carried.push(`${group}.${k}`);
    }
  }
  // top level: `seed` and `tier` are real config the form cannot edit
  const modelledTop = new Set(['name', 'network', 'fleet', 'av', 'sim', 'replicates']);
  for (const k of Object.keys(base as unknown as Record<string, unknown>)) {
    const v = (base as unknown as Record<string, unknown>)[k];
    if (modelledTop.has(k) || v === null || v === undefined) continue;
    carried.push(k);
  }
  return { carried: [...new Set(carried)].sort(), dropped };
}

/** One label/value pair of a card's fact list. */
function Fact({ label, children }: { label: string; children: ReactNode }): JSX.Element {
  return (
    <div className="scen-fact">
      <dt>{label}</dt>
      <dd className="mono">{children}</dd>
    </div>
  );
}

/** A card's facts as a two-column definition list, then its hash row. */
function scenarioMeta(s: LibraryItem, demo: boolean): JSX.Element {
  const net = s.config?.network;
  return (
    <>
      <dl className="scen-meta">
        {net?.kind === 'ring' && (
          <>
            <Fact label="Circumference">{net.circumference_m} m</Fact>
            <Fact label="Vehicles">{net.n_vehicles}</Fact>
          </>
        )}
        {net?.kind === 'corridor' && (
          <>
            <Fact label="Length">{(net.length_m / 1000).toFixed(1)} km</Fact>
            <Fact label="Lanes">{net.lanes}</Fact>
          </>
        )}
        {net?.kind === 'osm' && (
          <>
            <Fact label="Network">OSM import</Fact>
            <Fact label="Chain">{net.corridor_edges?.length ?? 0} edges</Fact>
          </>
        )}
        {s.config && (
          <>
            <Fact label="Duration">{Math.round(s.config.sim.duration_s / 60)} min</Fact>
            <Fact label="Replicates">{s.config.replicates}</Fact>
          </>
        )}
      </dl>
      <div className="scen-hash">
        <span className="scen-hash-label">Hash</span>
        {demo ? (
          // a demo hash exists on no server — printing one would fabricate provenance
          <span className="hash">— demo, no server hash —</span>
        ) : (
          <HashValue value={s.config_hash} label={`config hash of ${s.name}`} />
        )}
      </div>
    </>
  );
}

/** Placeholder card for the library's first load (§9.11: 3 card skeletons). */
function ScenarioCardSkeleton(): JSX.Element {
  return (
    <div className="panel scen-card" aria-hidden="true">
      <div className="thumb" />
      <Skeleton width="55%" height={14} />
      <Skeleton width="80%" height={12} />
      <Skeleton width="70%" height={12} />
      <Skeleton width="40%" height={12} />
    </div>
  );
}

interface LaunchForm {
  replicates: number;
  duration_s: number;
  seed: number;
}

/** What the launcher opens with for a card whose config the API did not
 * embed — and the fallback an emptied field returns to. */
const DEFAULT_LAUNCH: LaunchForm = { replicates: 20, duration_s: 1200, seed: 42 };

/** The launch form the card's own config implies. */
function launchDefaults(s: LibraryItem | null): LaunchForm {
  return {
    replicates: s?.config?.replicates ?? DEFAULT_LAUNCH.replicates,
    duration_s: s?.config?.sim.duration_s ?? DEFAULT_LAUNCH.duration_s,
    seed: s?.config?.seed ?? DEFAULT_LAUNCH.seed,
  };
}

export function ScenariosView(): JSX.Element {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [demoSource, setDemoSource] = useState<DemoSource>('none');
  const [compose, setCompose] = useState<ComposeState>(DEFAULT_COMPOSE);
  const [baseConfig, setBaseConfig] = useState<ScenarioConfig | null>(null);
  const [baseName, setBaseName] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [launchTarget, setLaunchTarget] = useState<LibraryItem | null>(null);
  const [launchForm, setLaunchForm] = useState<LaunchForm>(DEFAULT_LAUNCH);
  const fileRef = useRef<HTMLInputElement>(null);
  const createRef = useRef<HTMLButtonElement>(null);
  // `Create scenario` is disabled while it posts, which drops keyboard focus
  // to <body>; it gets focus back once the request ends (§9.1 Busy). The
  // launch dialog does the same for its own button (ConfirmDialog), and a
  // launch that lands on Runs hands focus to that page's content.
  const busyFocus = useBusyFocus(busy);
  const navigate = useNavigate();
  const { setCorridor } = useAppState();
  const authFailed = useAuthFailed();
  // Writes never fall back to the demo backend (api/client.assertWritable), so
  // the launcher is closed the moment the API goes away — not one refresh
  // later, when `demoSource` catches up.
  const offline = useOfflineFallback();

  const [loaded, setLoaded] = useState(false);
  /** The last library load failure the shell does not already report (an
   * HTTP refusal, not a dead network or a rejected key). */
  const [loadError, setLoadError] = useState<unknown>(null);
  const refresh = useCallback(async () => {
    // capture the source before the call: it decides mock vs live at call time
    const source: DemoSource = isMockEnv() ? 'env' : isOfflineFallback() ? 'offline' : 'none';
    const [presets, all] = await Promise.all([listPresetScenarios(), listScenarios()]);
    // A preset whose config is already stored shows as the stored scenario.
    setItems(mergeLibrary(presets, all));
    setDemoSource(source);
    setLoaded(true);
    setLoadError(null);
  }, []);

  // quiet retry until the library loads — and, while the demo fallback is
  // serving the cards, forever after: the demo list must be replaced by the
  // real one the moment the API answers, without a manual reload.
  const tryRefresh = useCallback(async () => {
    try {
      await refresh();
    } catch (err) {
      // retried by usePoll; connectivity and auth are the shell's banners
      setLoadError(isShellError(err) ? null : err);
    }
  }, [refresh]);
  const showingDemo = demoSource !== 'none';
  const staleDemo = demoSource === 'offline';
  usePoll(tryRefresh, authFailed ? null : !loaded || staleDemo ? LIBRARY_POLL_MS : null);

  const set = <K extends keyof ComposeState>(k: K, v: ComposeState[K]): void =>
    setCompose((c) => ({ ...c, [k]: v }));

  const passthrough = passthroughFields(baseConfig, compose.kind);

  const submitCompose = async (): Promise<void> => {
    busyFocus.begin(createRef.current);
    setBusy(true);
    try {
      const res = await createScenario(composeToConfig(compose, baseConfig));
      toast('ok', `scenario ${res.scenario_id} created · ${res.config_hash}`);
      await refresh();
    } catch (err) {
      toastError(err, 'compose');
    } finally {
      // back to the button either way: on success the toast names the new
      // scenario and the composer still holds the values for a variant
      busyFocus.after(() => createRef.current);
      setBusy(false);
    }
  };

  /** The card's Run opens the launcher instead of firing: a preset's own
   * `replicates` × `sim.duration_s` can be hours of compute. */
  const openLauncher = (s: LibraryItem): void => {
    setLaunchTarget(s);
    setLaunchForm(launchDefaults(s));
  };

  const launchRun = async (): Promise<void> => {
    const s = launchTarget;
    if (!s || launchWarmupBlock) return;
    setBusy(true);
    try {
      // presets are repo YAMLs, not stored scenarios: store one (or reuse the
      // stored copy with the same config hash) before it can be run
      const { scenario_id: scenarioId, stored } = await ensureStored(s);
      if (stored) toast('ok', `preset ${s.name} stored as ${scenarioId}`);
      const overrides: Record<string, unknown> = {};
      if (launchForm.duration_s !== s.config?.sim.duration_s) {
        overrides.sim = { duration_s: launchForm.duration_s };
      }
      if (launchForm.seed !== s.config?.seed) overrides.seed = launchForm.seed;
      const req: CreateRunRequest = {
        scenario_id: scenarioId,
        replicates: launchForm.replicates,
      };
      if (Object.keys(overrides).length > 0) req.overrides = overrides;
      const res = await createRun(req);
      setCorridor(s.name);
      setLaunchTarget(null);
      toast('ok', `run ${res.run_id} queued`);
      navigate('/runs', { state: FOCUS_CONTENT_STATE });
    } catch (err) {
      toastError(err, 'run');
    } finally {
      setBusy(false);
    }
  };

  const loadIntoComposer = (s: LibraryItem): void => {
    const cfg = s.config;
    if (!cfg) {
      toast('info', 'preset has no embedded config to load');
      return;
    }
    setCorridor(s.name);
    setBaseConfig(cfg);
    setBaseName(s.name);
    setCompose({
      name: `${cfg.name}_variant`,
      // `osm` is carried as itself: the composer edits the non-network fields
      // of an imported corridor and leaves its network block alone
      kind: cfg.network.kind,
      length_m: cfg.network.kind === 'corridor' ? cfg.network.length_m : 10000,
      lanes: cfg.network.kind === 'corridor' ? cfg.network.lanes : 1,
      circumference_m: cfg.network.kind === 'ring' ? cfg.network.circumference_m : 230,
      n_vehicles: cfg.network.kind === 'ring' ? cfg.network.n_vehicles : 22,
      model: cfg.fleet.model,
      penetration: Math.round(cfg.av.penetration * 100),
      compliance: Math.round(cfg.av.compliance * 100),
      controller: (CONTROLLERS.find((c) => c === cfg.av.controller) ?? 'none') as ComposeState['controller'],
      vsl: cfg.av.vsl != null,
      duration_s: cfg.sim.duration_s,
      replicates: cfg.replicates,
    });
  };

  const clearBase = (): void => {
    setBaseConfig(null);
    setBaseName(null);
    // an OSM network exists only as the loaded corridor's own block; with no
    // base there is nothing to carry, so the form returns to a buildable kind
    setCompose((c) => (c.kind === 'osm' ? { ...c, kind: 'corridor' } : c));
  };

  const handleYamlText = async (text: string, filename: string): Promise<void> => {
    try {
      const raw: unknown = yaml.load(text);
      if (!raw || typeof raw !== 'object' || !('name' in raw) || !('network' in raw)) {
        throw new Error('not a ScenarioConfig: needs at least name + network');
      }
      const res = await createScenario(raw as ScenarioConfig);
      toast('ok', `${filename} uploaded → ${res.scenario_id}`);
      await refresh();
    } catch (err) {
      toastError(err, filename);
    }
  };

  const onDrop = (e: DragEvent<HTMLDivElement>): void => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (!file) return;
    void file.text().then((t) => handleYamlText(t, file.name));
  };

  const underpowered = compose.replicates < MIN_REPLICATES;
  const launchTotal = simMinutes(launchForm.replicates, launchForm.duration_s);
  /** What an emptied launcher field falls back to: the target's own value. */
  const launchBase = launchDefaults(launchTarget);
  /** A duration inside the warm-up leaves nothing to measure and kills every
   * replicate on the worker; the launcher refuses it here instead. */
  const launchWarmupBlock = warmupProblem(
    launchForm.duration_s,
    launchTarget?.config?.sim.warmup_s ?? null,
  );
  /** The loaded corridor's OSM network, when the composer is holding one. */
  const osmNet: OSMNetwork | null =
    compose.kind === 'osm' && baseConfig?.network.kind === 'osm' ? baseConfig.network : null;

  const openFilePicker = (): void => fileRef.current?.click();

  return (
    <div className="view">
      <PageHeader
        title="Scenarios"
        documentTitle="Scenarios"
        meta={
          <>
            {loaded && <span className="mono">{items.length} configs</span>}
            {showingDemo && (
              <span className="tag demo" title="Not from the API — built-in demo data">
                DEMO DATA
              </span>
            )}
          </>
        }
        description="Presets and stored scenarios. Run one, or load it into the composer to make a variant."
        actions={
          <button type="button" className="btn" onClick={openFilePicker}>
            <Icon name="upload" />
            Upload YAML
          </button>
        }
      />

      {/* the guided QUICKSTART path, shown only once this server has itself
          reported that it holds no runs; the rail's "First run" always has it */}
      <GuidedFirstRun onlyWhenEmpty />

      {staleDemo && (
        <Callout tone="warning" role="note">
          The API is unreachable, so these are built-in demo scenarios: their hashes exist on no
          server and they cannot be run. The library keeps retrying and replaces them as soon as the
          API answers.
        </Callout>
      )}

      {!loaded && loadError !== null && (
        <ErrorCallout
          error={loadError}
          title="Could not load the scenario library."
          onRetry={() => void tryRefresh()}
        />
      )}

      <section
        className="card-grid"
        aria-label="scenario library"
        aria-busy={!loaded && loadError === null && !authFailed ? true : undefined}
      >
        {/* a rejected key pauses the poll: say so here rather than leave
            placeholders that will never fill (the shell banner has Retry) */}
        {!loaded && authFailed && (
          <EmptyState
            compact
            title="Scenarios are not loading."
            description="The API key was rejected. Save a new key in Settings to resume."
          />
        )}
        {!loaded &&
          loadError === null &&
          !authFailed &&
          [0, 1, 2].map((i) => <ScenarioCardSkeleton key={i} />)}
        {items.map((s) => (
          <article key={itemKey(s)} className={`panel scen-card${showingDemo ? ' demo' : ''}`}>
            <div className="thumb">
              <SchematicThumb network={s.config?.network} name={s.name} />
            </div>
            <div className="name">
              <span className="scen-name mono">{s.name}</span>
              {showsPresetBadge(s) && <span className="tag preset">PRESET</span>}
              {showingDemo && <span className="tag demo">DEMO</span>}
            </div>
            {scenarioMeta(s, showingDemo)}
            <div className="scen-actions">
              <button
                type="button"
                className="btn sm primary"
                disabled={isMockEnv() ? false : offline || staleDemo}
                title={
                  offline
                    ? OFFLINE_WRITE_MESSAGE
                    : staleDemo
                      ? 'demo scenario — connect the API to run it'
                      : 'set replicates, duration and seed before launching'
                }
                onClick={() => openLauncher(s)}
              >
                Run…
              </button>
              <button type="button" className="btn sm" onClick={() => loadIntoComposer(s)}>
                Load in composer
              </button>
            </div>
          </article>
        ))}
        {loaded && items.length === 0 && (
          <EmptyState
            title="No scenarios yet."
            description="Upload a scenario YAML or compose one below."
            icon={<Icon name="layers" size={20} />}
          />
        )}
      </section>

      <div
        className={`dropzone${dragOver ? ' over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={openFilePicker}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openFilePicker();
          }
        }}
      >
        <Icon name="upload" />
        Drop a scenario YAML here — or click to browse
        <input
          ref={fileRef}
          type="file"
          accept=".yaml,.yml"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void f.text().then((t) => handleYamlText(t, f.name));
            e.target.value = '';
          }}
        />
      </div>

      <section className="panel" aria-labelledby="compose-title">
        <div className="panel-head">
          <h2 className="panel-title" id="compose-title">
            Compose scenario
          </h2>
          {baseName && (
            <>
              <span className="spacer" />
              <span className="panel-sub">
                based on <span className="mono">{baseName}</span>
              </span>
              <button type="button" className="btn sm" onClick={clearBase}>
                Start blank
              </button>
            </>
          )}
        </div>
        <div className="panel-body stack">
          <div className="grid-12 compose-sections">
            <div className="form-section col-span-4">
              <h3>Network</h3>
              <div className="form-grid">
                <div className="field">
                  <label htmlFor="c-name">Name</label>
                  <input
                    id="c-name"
                    className="input mono"
                    value={compose.name}
                    onChange={(e) => set('name', e.target.value)}
                  />
                </div>
                <div className="field">
                  <label htmlFor="c-kind">Network kind</label>
                  <select
                    id="c-kind"
                    className="input"
                    value={compose.kind}
                    // an imported corridor cannot be turned into a ring or a
                    // synthetic corridor: the chain, ramps and boundary have no
                    // counterpart there, and the coercion used to drop them
                    disabled={compose.kind === 'osm'}
                    title={
                      compose.kind === 'osm'
                        ? 'Imported (OSM) network — read-only here. Start blank to compose a ' +
                          'synthetic network instead.'
                        : undefined
                    }
                    onChange={(e) => set('kind', e.target.value as ComposeState['kind'])}
                  >
                    {compose.kind === 'osm' ? (
                      <option value="osm">osm (imported, read-only)</option>
                    ) : (
                      <>
                        <option value="corridor">corridor</option>
                        <option value="ring">ring</option>
                      </>
                    )}
                  </select>
                </div>
                {osmNet ? (
                  <div className="field span-2">
                    <span className="field-label">Imported network</span>
                    {/* a group so the label is valid: a <dl> has no ARIA role,
                        and a name on an element without one is ignored */}
                    <dl className="fact-list osm-facts" role="group" aria-label="imported network">
                      <div className="fact">
                        <dt>OSM extract</dt>
                        <dd className="mono">
                          {osmNet.osm_file ??
                            (osmNet.bbox ? `bbox ${osmNet.bbox.join(', ')}` : 'not named')}
                        </dd>
                      </div>
                      <div className="fact">
                        <dt>Corridor chain</dt>
                        <dd className="mono">{osmNet.corridor_edges?.length ?? 0} edges</dd>
                      </div>
                      <div className="fact">
                        <dt>Ramps</dt>
                        <dd className="mono">{osmNet.ramps?.length ?? 0}</dd>
                      </div>
                      <div className="fact">
                        <dt>Downstream boundary</dt>
                        <dd className="mono">
                          {osmNet.boundary
                            ? `measured (${osmNet.boundary.kind ?? 'set'})`
                            : 'none — free outflow'}
                        </dd>
                      </div>
                    </dl>
                    <span className="field-help">
                      Read-only: the network block of {baseName} is sent back unchanged.
                    </span>
                  </div>
                ) : compose.kind === 'corridor' ? (
                  <>
                    <div className="field">
                      <label htmlFor="c-len">Length (m)</label>
                      <input
                        id="c-len"
                        className="input"
                        type="number"
                        min={500}
                        step={500}
                        value={compose.length_m}
                        onChange={(e) => set('length_m', Number(e.target.value))}
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="c-lanes">Lanes</label>
                      <input
                        id="c-lanes"
                        className="input"
                        type="number"
                        min={1}
                        max={MAX_LANES}
                        value={compose.lanes}
                        onChange={(e) =>
                          set('lanes', clampInt(Number(e.target.value), 1, MAX_LANES))
                        }
                      />
                    </div>
                  </>
                ) : (
                  <>
                    <div className="field">
                      <label htmlFor="c-circ">Circumference (m)</label>
                      <input
                        id="c-circ"
                        className="input"
                        type="number"
                        min={50}
                        value={compose.circumference_m}
                        onChange={(e) => set('circumference_m', Number(e.target.value))}
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="c-nveh">Vehicles</label>
                      <input
                        id="c-nveh"
                        className="input"
                        type="number"
                        min={2}
                        value={compose.n_vehicles}
                        onChange={(e) => set('n_vehicles', Number(e.target.value))}
                      />
                    </div>
                  </>
                )}
              </div>
            </div>

            <div className="form-section col-span-4">
              <h3>Fleet &amp; control</h3>
              <div className="form-grid">
                <div className="field">
                  <label htmlFor="c-model">Fleet model</label>
                  <select
                    id="c-model"
                    className="input"
                    value={compose.model}
                    onChange={(e) => set('model', e.target.value as ComposeState['model'])}
                  >
                    <option value="IDM">IDM</option>
                    <option value="EIDM">EIDM</option>
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="c-ctrl">Controller</label>
                  <select
                    id="c-ctrl"
                    className="input mono"
                    value={compose.controller}
                    onChange={(e) =>
                      set('controller', e.target.value as ComposeState['controller'])
                    }
                  >
                    {CONTROLLERS.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="c-pen">AV penetration</label>
                  <div className="slider-row">
                    <input
                      id="c-pen"
                      type="range"
                      min={0}
                      max={30}
                      step={1}
                      value={compose.penetration}
                      onChange={(e) => set('penetration', Number(e.target.value))}
                    />
                    <span className="slider-val">{compose.penetration}%</span>
                  </div>
                </div>
                <div className="field">
                  <label htmlFor="c-com">Compliance</label>
                  <div className="slider-row">
                    <input
                      id="c-com"
                      type="range"
                      min={10}
                      max={100}
                      step={5}
                      value={compose.compliance}
                      onChange={(e) => set('compliance', Number(e.target.value))}
                    />
                    <span className="slider-val">{compose.compliance}%</span>
                  </div>
                </div>
                <div className="field">
                  <label htmlFor="c-vsl">Variable speed limit</label>
                  <label className="check" htmlFor="c-vsl">
                    <input
                      id="c-vsl"
                      type="checkbox"
                      checked={compose.vsl}
                      onChange={(e) => set('vsl', e.target.checked)}
                    />
                    gantry VSL segments
                  </label>
                </div>
              </div>
            </div>

            <div className="form-section col-span-4">
              <h3>Simulation</h3>
              <div className="form-grid">
                <div className="field">
                  <label htmlFor="c-dur">Duration (s)</label>
                  <input
                    id="c-dur"
                    className="input"
                    type="number"
                    min={60}
                    step={60}
                    value={compose.duration_s}
                    onChange={(e) => set('duration_s', Number(e.target.value))}
                  />
                </div>
                <div className="field">
                  <label htmlFor="c-reps">Replicates</label>
                  <input
                    id="c-reps"
                    className="input"
                    type="number"
                    min={1}
                    max={MAX_REPLICATES}
                    value={compose.replicates}
                    aria-invalid={underpowered || undefined}
                    aria-describedby={underpowered ? 'c-reps-hint' : undefined}
                    onChange={(e) =>
                      set('replicates', clampInt(Number(e.target.value), 1, MAX_REPLICATES))
                    }
                  />
                  {underpowered && (
                    <span className="hint-amber" id="c-reps-hint">
                      below reporting standard n ≥ {MIN_REPLICATES}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>

          {baseConfig && (
            <div className="passthrough">
              <Callout tone="neutral" role="note">
                {passthrough.carried.length > 0 ? (
                  <>
                    Not editable here — carried through from <b>{baseName}</b> unchanged:{' '}
                    <span className="mono">{passthrough.carried.join(', ')}</span>
                  </>
                ) : (
                  <>
                    This form models every field of <b>{baseName}</b>.
                  </>
                )}
              </Callout>
              {passthrough.dropped.length > 0 && (
                <Callout tone="warning" role="note">
                  Dropped by the network-kind change:{' '}
                  <span className="mono">{passthrough.dropped.join(', ')}</span>
                </Callout>
              )}
            </div>
          )}
        </div>
        <div className="panel-foot form-actions">
          <span className="form-actions-summary">
            POST /scenarios · validated server-side ·{' '}
            <span className="mono">
              {describeSimMinutes(simMinutes(compose.replicates, compose.duration_s))}
            </span>
          </span>
          <div className="form-actions-buttons">
            <button
              ref={createRef}
              type="button"
              className="btn primary"
              disabled={busy}
              aria-busy={busy || undefined}
              onClick={() => void submitCompose()}
            >
              Create scenario
            </button>
          </div>
        </div>
      </section>

      {launchTarget && (
        <ConfirmDialog
          title={`Launch ${launchTarget.name}`}
          // a launch that cannot measure anything is not confirmable
          busy={busy || launchWarmupBlock !== null}
          confirmLabel="Launch run"
          onConfirm={() => void launchRun()}
          onCancel={() => setLaunchTarget(null)}
          facts={[['Total compute', describeSimMinutes(launchTotal)]]}
        >
          <div className="launch-fields">
            <div className="field">
              <label htmlFor="lr-reps">Replicates</label>
              <input
                id="lr-reps"
                className="input"
                type="number"
                min={1}
                max={MAX_REPLICATES}
                value={launchForm.replicates}
                aria-invalid={launchForm.replicates < MIN_REPLICATES || undefined}
                aria-describedby={
                  launchForm.replicates < MIN_REPLICATES ? 'lr-reps-hint' : undefined
                }
                onChange={(e) =>
                  setLaunchForm((f) => ({
                    ...f,
                    replicates: clampField(
                      e.target.value,
                      1,
                      MAX_REPLICATES,
                      launchBase.replicates,
                    ),
                  }))
                }
              />
              {launchForm.replicates < MIN_REPLICATES && (
                <span className="hint-amber" id="lr-reps-hint">
                  below reporting standard n ≥ {MIN_REPLICATES}
                </span>
              )}
            </div>
            <div className="field">
              <label htmlFor="lr-dur">Duration (s)</label>
              {/* clamped like every other numeric field: an emptied box reads as
                  Number('') === 0, and `sim.duration_s: 0` is both rejected by
                  the API and meaningless as a launch */}
              <input
                id="lr-dur"
                className="input"
                type="number"
                min={MIN_DURATION_S}
                max={MAX_DURATION_S}
                step={60}
                value={launchForm.duration_s}
                aria-invalid={launchWarmupBlock !== null || undefined}
                aria-describedby={launchWarmupBlock ? 'lr-dur-hint' : undefined}
                onChange={(e) =>
                  setLaunchForm((f) => ({
                    ...f,
                    duration_s: clampField(
                      e.target.value,
                      MIN_DURATION_S,
                      MAX_DURATION_S,
                      launchBase.duration_s,
                    ),
                  }))
                }
              />
              {launchWarmupBlock && (
                <span className="hint-amber" id="lr-dur-hint">
                  {launchWarmupBlock}
                </span>
              )}
            </div>
            <div className="field">
              <label htmlFor="lr-seed">Seed</label>
              <input
                id="lr-seed"
                className="input"
                type="number"
                min={MIN_SEED}
                max={MAX_SEED}
                value={launchForm.seed}
                onChange={(e) =>
                  setLaunchForm((f) => ({
                    ...f,
                    seed: clampField(e.target.value, MIN_SEED, MAX_SEED, launchBase.seed),
                  }))
                }
              />
            </div>
          </div>
          <p className="field-help">
            Replicates and duration multiply: every replicate runs the full duration. Changed values
            are sent as an overrides patch, so the run gets its own config hash.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
