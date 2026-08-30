/** Scenarios: preset cards with schematic thumbnails, YAML upload drop-zone,
 * and a Compose form building a ScenarioConfig for POST /scenarios. */

import yaml from 'js-yaml';
import { useCallback, useRef, useState, type DragEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { createRun, createScenario, listPresetScenarios, listScenarios } from '../api/client';
import type { Network, ScenarioConfig, ScenarioSummary } from '../api/types';
import { useAppState } from '../components/AppContext';
import { SchematicThumb } from '../components/bits';
import { toast, toastError } from '../components/toast';
import { usePoll } from '../lib/hooks';
import { MIN_REPLICATES } from '../lib/metrics';

const CONTROLLERS = ['follower_stopper', 'pi_saturation', 'jad', 'none'] as const;

interface ComposeState {
  name: string;
  kind: 'ring' | 'corridor';
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

function composeToConfig(c: ComposeState): ScenarioConfig {
  const network: Network =
    c.kind === 'ring'
      ? { kind: 'ring', circumference_m: c.circumference_m, n_vehicles: c.n_vehicles }
      : { kind: 'corridor', length_m: c.length_m, lanes: c.lanes, inflow: [[0, 0.55]] };
  return {
    name: c.name,
    tier: 'micro',
    network,
    fleet: { model: c.model },
    av: {
      penetration: c.penetration / 100,
      compliance: c.compliance / 100,
      controller: c.controller === 'none' ? null : c.controller,
      vsl: c.vsl ? 'threshold' : null,
    },
    sim: { duration_s: c.duration_s },
    seed: 42,
    replicates: c.replicates,
  };
}

function scenarioMeta(s: ScenarioSummary): JSX.Element {
  const net = s.config?.network;
  return (
    <div className="scen-meta">
      {net?.kind === 'ring' && (
        <>
          <span>
            circ <b>{net.circumference_m} m</b>
          </span>
          <span>
            vehicles <b>{net.n_vehicles}</b>
          </span>
        </>
      )}
      {net?.kind === 'corridor' && (
        <>
          <span>
            length <b>{(net.length_m / 1000).toFixed(1)} km</b>
          </span>
          <span>
            lanes <b>{net.lanes}</b>
          </span>
        </>
      )}
      {s.config && (
        <>
          <span>
            duration <b>{Math.round(s.config.sim.duration_s / 60)} min</b>
          </span>
          <span>
            reps <b>{s.config.replicates}</b>
          </span>
        </>
      )}
      <span>
        hash <b className="hash">{s.config_hash}</b>
      </span>
    </div>
  );
}

export function ScenariosView(): JSX.Element {
  const [items, setItems] = useState<ScenarioSummary[]>([]);
  const [compose, setCompose] = useState<ComposeState>(DEFAULT_COMPOSE);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const { setCorridor } = useAppState();

  const [loaded, setLoaded] = useState(false);
  const refresh = useCallback(async () => {
    const [presets, all] = await Promise.all([listPresetScenarios(), listScenarios()]);
    const seen = new Set<string>();
    const merged: ScenarioSummary[] = [];
    for (const s of [...presets.map((p) => ({ ...p, preset: true })), ...all]) {
      if (seen.has(s.scenario_id)) continue;
      seen.add(s.scenario_id);
      merged.push(s);
    }
    setItems(merged);
    setLoaded(true);
  }, []);

  // quiet retry until the library loads (offline-fallback race)
  const tryRefresh = useCallback(async () => {
    try {
      await refresh();
    } catch {
      /* retried by usePoll; connectivity surfaced by the status dot */
    }
  }, [refresh]);
  usePoll(tryRefresh, loaded ? null : 3000);

  const set = <K extends keyof ComposeState>(k: K, v: ComposeState[K]): void =>
    setCompose((c) => ({ ...c, [k]: v }));

  const submitCompose = async (): Promise<void> => {
    setBusy(true);
    try {
      const res = await createScenario(composeToConfig(compose));
      toast('ok', `scenario ${res.scenario_id} created · ${res.config_hash}`);
      await refresh();
    } catch (err) {
      toastError(err, 'compose');
    } finally {
      setBusy(false);
    }
  };

  const launchRun = async (s: ScenarioSummary): Promise<void> => {
    try {
      const res = await createRun({
        scenario_id: s.scenario_id,
        replicates: s.config?.replicates ?? 20,
      });
      setCorridor(s.name);
      toast('ok', `run ${res.run_id} queued`);
      navigate('/runs');
    } catch (err) {
      toastError(err, 'run');
    }
  };

  const loadIntoComposer = (s: ScenarioSummary): void => {
    const cfg = s.config;
    if (!cfg) {
      toast('info', 'preset has no embedded config to load');
      return;
    }
    setCorridor(s.name);
    setCompose({
      name: `${cfg.name}_variant`,
      kind: cfg.network.kind === 'ring' ? 'ring' : 'corridor',
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

  return (
    <div className="view">
      <div className="view-title">
        Scenario Library <span className="count mono">{items.length} configs</span>
      </div>

      <div className="card-grid">
        {items.map((s) => (
          <div key={s.scenario_id} className="panel scen-card">
            <div className="thumb">
              <SchematicThumb network={s.config?.network} name={s.name} />
            </div>
            <div className="name mono">
              {s.name}
              {s.preset && <span className="tag preset">PRESET</span>}
            </div>
            {scenarioMeta(s)}
            <div className="scen-actions">
              <button className="btn sm primary" onClick={() => void launchRun(s)}>
                Run
              </button>
              <button className="btn sm" onClick={() => loadIntoComposer(s)}>
                Load in composer
              </button>
            </div>
          </div>
        ))}
        {items.length === 0 && <div className="empty">no scenarios yet</div>}
      </div>

      <div
        className={`dropzone${dragOver ? ' over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => fileRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter') fileRef.current?.click();
        }}
      >
        Drop a scenario YAML here — or click to browse
        <input
          ref={fileRef}
          type="file"
          accept=".yaml,.yml"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void f.text().then((t) => handleYamlText(t, f.name));
            e.target.value = '';
          }}
        />
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Compose scenario</span>
        </div>
        <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
          <div className="compose-grid">
            <div className="field">
              <label htmlFor="c-name">Name</label>
              <input
                id="c-name"
                className="input"
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
                onChange={(e) => set('kind', e.target.value as ComposeState['kind'])}
              >
                <option value="corridor">corridor</option>
                <option value="ring">ring</option>
              </select>
            </div>
            {compose.kind === 'corridor' ? (
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
                    max={4}
                    value={compose.lanes}
                    onChange={(e) => set('lanes', Number(e.target.value))}
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
                className="input"
                value={compose.controller}
                onChange={(e) => set('controller', e.target.value as ComposeState['controller'])}
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
                value={compose.replicates}
                onChange={(e) => set('replicates', Number(e.target.value))}
              />
              {underpowered && (
                <span className="hint-amber">below reporting standard n ≥ {MIN_REPLICATES}</span>
              )}
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
          <div className="row">
            <button className="btn primary" disabled={busy} onClick={() => void submitCompose()}>
              Create scenario
            </button>
            <span className="small muted mono">POST /scenarios · validated server-side</span>
          </div>
        </div>
      </div>
    </div>
  );
}
