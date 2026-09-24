/** OnboardView: the guided "corridor from public data" flow.
 *
 * What is pinned here is what the user cannot verify by eye — that the form
 * refuses to send an unusable request, that the multipart body carries the
 * fields the API documents, that the panel follows the job's stages to done
 * and reports what was *derived* (including the residual the balance could
 * not close), and that the two follow-on buttons send the run and the report
 * the honesty rules require: 20 seeds, and a report carrying the
 * observations path so its GEH and speed rows are evaluated rather than
 * "not evaluated". */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, OFFLINE_WRITE_MESSAGE, setOfflineFallback } from '../api/client';
import { AppStateProvider, useAppState } from '../components/AppContext';
import { ERROR_TOAST_MS, TOAST_MS, Toasts, toast } from '../components/toast';
import { OnboardView, parseBbox } from '../views/OnboardView';

const CORRIDOR_ID = 'cor_9f21ab77cd10';
const SCENARIO_ID = 'scn_onboarded01';
const RUN_ID = 'run_onboarded01';
const OBSERVATIONS = `/srv/runs/corridors/${CORRIDOR_ID}/observations.json`;

const SUMMARY = {
  corridor: 'mndot_i94_wb',
  chain_length_m: 11820,
  n_chain_edges: 32,
  lanes_profile: [
    [0, 3200, 3],
    [3200, 11820, 4],
  ],
  n_ramps: 3,
  stations_placed: [
    { station: 'S1063', x_m: 1110, offset_m: 4.2 },
    { station: 'S97', x_m: 11027, offset_m: 6.7 },
  ],
  stations_rejected: [{ station: 'S1450', x_m: 4820, offset_m: 168.4 }],
  stations_without_chain_x: ['S1450'],
  lanes_compared: 2,
  lane_mismatches: [
    {
      station: 'S1063',
      x_m: 1110,
      compiled_lanes: 4,
      inventory_lanes: 3,
      hint: 'acceleration lane added by ramp guessing',
    },
  ],
  split_audit: [
    {
      from_edge: '1001426896',
      exit_edge: '82150350',
      continuing_edge: '1001426897',
      x_m: 10730,
      osm_way: '82150350',
      osm_lanes: 3,
      turn_lanes: 'none|none|through;slight_right',
      turn_lanes_side: 'right',
      osm_side: 'right',
      osm_offsets_m: [-1.4, -45.0],
      compiled_lanes: 4,
      exit_from_lanes: [3],
      compiled_side: 'leftmost',
      option_lanes: [],
      added_lane: true,
      exit_lanes: 1,
      continuing_lanes: 3,
      verdict: 'added_lane_wrong_side',
      remedy: '--ramps.unset 1001426896',
    },
  ],
  // the audit the fixes were derived from: one more defect than the audit
  // that stands (a wrong_side exit the connection patch fixed)
  split_audit_before_fixes: [
    {
      from_edge: '45608485',
      exit_edge: '18207912',
      continuing_edge: '45608486',
      x_m: 11510,
      osm_way: '18207912',
      osm_lanes: 5,
      turn_lanes: null,
      turn_lanes_side: 'unknown',
      osm_side: 'right',
      osm_offsets_m: [-8.0, -14.0],
      compiled_lanes: 5,
      exit_from_lanes: [3, 4],
      compiled_side: 'leftmost',
      option_lanes: [],
      added_lane: false,
      exit_lanes: 2,
      continuing_lanes: 3,
      verdict: 'wrong_side',
      remedy: 'patch_files connection restating the split',
    },
    {
      from_edge: '1001426896',
      exit_edge: '82150350',
      continuing_edge: '1001426897',
      x_m: 10730,
      osm_way: '82150350',
      osm_lanes: 3,
      turn_lanes: 'none|none|through;slight_right',
      turn_lanes_side: 'right',
      osm_side: 'right',
      osm_offsets_m: [-1.4, -45.0],
      compiled_lanes: 4,
      exit_from_lanes: [3],
      compiled_side: 'leftmost',
      option_lanes: [],
      added_lane: true,
      exit_lanes: 1,
      continuing_lanes: 3,
      verdict: 'added_lane_wrong_side',
      remedy: '--ramps.unset 1001426896',
    },
  ],
  ramp_guessing: true,
  split_fixes: true,
  split_fixes_applied: 1,
  split_defects_remaining: 1,
  split_patch_file: 'data/osm/mndot_i94_wb.splits.con.xml',
  applied: 'ramp guessing on; split fixes: 1 applied, 1 remaining',
  inflow_peak_veh_h: 4275,
  ramps: [
    {
      name: 'I-494 entrance',
      kind: 'on',
      x_m: 1980,
      method: 'detector',
      peak: 1260,
      unit: 'veh/h',
      station: 'D1064',
    },
    {
      name: 'White Bear Ave entrance',
      kind: 'on',
      x_m: 6120,
      method: 'conservation',
      peak: 442,
      unit: 'veh/h',
      station: null,
    },
    {
      name: 'Mounds Blvd exit',
      kind: 'off',
      x_m: 10240,
      method: 'conservation',
      peak: 0.08,
      unit: 'frac',
      station: null,
    },
  ],
  residuals: [{ from: 'S792', to: 'S791', mean_residual_veh_h: 775, note: 'carried' }],
  zeroed_ramps: ['Kellogg Blvd exit (x=11510 m): outside the observed span [1110, 11027] m'],
  unmatched_detectors: ['D1210'],
  lines: ['corridor mndot_i94_wb: 11.82 km along 32 edges, bearing 265°'],
};

function corridorRow(stage: string, done: boolean): unknown {
  return {
    corridor_id: CORRIDOR_ID,
    name: 'mndot_i94_wb',
    status: done ? 'done' : 'running',
    progress: { stage, completed_stages: done ? 5 : 1, total_stages: 5 },
    scenario_id: done ? SCENARIO_ID : null,
    preset_filename: done ? 'mndot_i94_wb.yaml' : null,
    config_hash: done ? 'a1b2c3d4e5f6' : null,
    observations_path: done ? OBSERVATIONS : null,
    corridor_dir: `corridors/${CORRIDOR_ID}`,
    summary: done ? SUMMARY : null,
    error: null,
    error_kind: null,
    created_at: '2026-09-23T06:00:00',
  };
}

function runRow(status: string): unknown {
  return {
    run_id: RUN_ID,
    scenario_id: SCENARIO_ID,
    sweep_id: null,
    status,
    tier: 'micro',
    config_hash: 'a1b2c3d4e5f6',
    seeded: false,
    progress: {
      completed_replicates: status === 'done' ? 20 : 3,
      total_replicates: 20,
    },
    seeds: [1],
    error: null,
    error_kind: null,
    created_at: '2026-09-23T06:10:00',
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

interface Call {
  url: string;
  method: string;
  body?: unknown;
  form?: FormData;
}

const calls: Call[] = [];
/** Flipped by a test once the onboarding job should report `done`. */
let corridorDone = false;
let runStatus = 'running';

function renderView(): void {
  render(
    <MemoryRouter initialEntries={['/onboard']}>
      <Routes>
        <Route path="/onboard" element={<OnboardView />} />
        <Route path="/runs" element={<div>runs view</div>} />
        <Route path="/reports" element={<div>reports view</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function fillForm(): void {
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'mndot_i94_wb' } });
  fireEvent.change(screen.getByLabelText('Bounding box (S, W, N, E)'), {
    target: { value: '44.9425, -93.0990, 44.9613, -92.9612' },
  });
  fireEvent.change(screen.getByLabelText('Bearing (deg)'), { target: { value: '265' } });
  fireEvent.change(screen.getByLabelText('Upstream station'), { target: { value: 'S1063' } });
  fireEvent.change(screen.getByLabelText('Downstream station'), { target: { value: 'S97' } });
  fireEvent.change(screen.getByLabelText('Detector CSV'), {
    target: { files: [new File(['timestamp,station,flow_veh_h\n'], 'det.csv')] },
  });
  fireEvent.change(screen.getByLabelText('Stations CSV'), {
    target: { files: [new File(['station,lat,lon\n'], 'sta.csv')] },
  });
}

describe('parseBbox', () => {
  it('accepts commas, spaces or both', () => {
    expect(parseBbox('44.94, -93.10, 44.96, -92.96')).toEqual({
      bbox: { south: 44.94, west: -93.1, north: 44.96, east: -92.96 },
    });
    expect(parseBbox('44.94 -93.10 44.96 -92.96')).toEqual({
      bbox: { south: 44.94, west: -93.1, north: 44.96, east: -92.96 },
    });
  });

  it('says what is wrong instead of guessing', () => {
    expect(parseBbox('44.94, -93.10, 44.96')).toEqual({
      error: 'needs four numbers "south, west, north, east" — got 3',
    });
    expect(parseBbox('44.96, -93.10, 44.94, -92.96')).toEqual({
      error: 'south (44.96) must be below north (44.94)',
    });
    expect(parseBbox('44.94, -92.96, 44.96, -93.10')).toEqual({
      error: 'west (-92.96) must be left of east (-93.1)',
    });
    expect(parseBbox('44.94, -93.10, 44.96, north')).toEqual({
      error: 'all four values must be numbers',
    });
    expect(parseBbox('-91, -93.10, 44.96, -92.96')).toEqual({
      error: 'outside the WGS84 range (lat ±90, lon ±180)',
    });
  });
});

describe('OnboardView', () => {
  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    window.localStorage.clear();
    calls.length = 0;
    corridorDone = false;
    runStatus = 'running';
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        const method = init?.method ?? 'GET';
        const call: Call = { url, method };
        if (init?.body instanceof FormData) call.form = init.body;
        else if (init?.body) call.body = JSON.parse(String(init.body)) as unknown;
        calls.push(call);
        if (url.endsWith('/corridors') && method === 'POST') {
          return json(corridorRow('extract', false), 202);
        }
        if (url.includes('/corridors/')) {
          return json(corridorRow(corridorDone ? 'done' : 'network', corridorDone));
        }
        if (url.endsWith('/runs') && method === 'POST') return json({ run_id: RUN_ID }, 202);
        if (url.includes('/runs/')) return json(runRow(runStatus));
        if (url.endsWith('/reports') && method === 'POST') {
          return json({ report_id: 'rpt_1', status: 'queued' }, 202);
        }
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearAuthFailure();
    window.localStorage.clear();
  });

  it('will not send an onboarding that cannot succeed', () => {
    renderView();
    const button = screen.getByRole('button', { name: 'Onboard corridor' });
    expect(button).toBeDisabled();
    // the hint is the button's description; its *name* says what it does
    expect(button).toHaveAccessibleName('Onboard corridor');
    expect(button).toHaveAccessibleDescription(expect.stringContaining('Name'));

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'mndot_i94_wb' } });
    fireEvent.change(screen.getByLabelText('Bounding box (S, W, N, E)'), {
      target: { value: '44.9425, -93.0990, 44.9613' },
    });
    // the bbox problem is named under the field (and again beside the
    // button), not left to a server 422
    expect(screen.getAllByText(/needs four numbers/).length).toBeGreaterThan(0);
    expect(button).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Bounding box (S, W, N, E)'), {
      target: { value: '44.9425, -93.0990, 44.9613, -92.9612' },
    });
    fireEvent.change(screen.getByLabelText('Upstream station'), { target: { value: 'S1063' } });
    fireEvent.change(screen.getByLabelText('Downstream station'), { target: { value: 'S97' } });
    // both files are still missing, and the button says which
    expect(button).toHaveAccessibleDescription('Choose the detector CSV');
    fireEvent.change(screen.getByLabelText('Detector CSV'), {
      target: { files: [new File(['x'], 'det.csv')] },
    });
    expect(button).toHaveAccessibleDescription('Choose the stations CSV');
    fireEvent.change(screen.getByLabelText('Stations CSV'), {
      target: { files: [new File(['x'], 'sta.csv')] },
    });
    expect(button).toBeEnabled();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0);
  });

  it('will not onboard from the demo backend while the API is unreachable', () => {
    setOfflineFallback(true);
    renderView();
    fillForm();
    const button = screen.getByRole('button', { name: 'Onboard corridor' });
    // a corridor "calibrated" by the in-browser backend would be a claim
    // about a road no server ever saw (api/client.assertWritable)
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleName('Onboard corridor');
    expect(button).toHaveAccessibleDescription(OFFLINE_WRITE_MESSAGE);
    expect(calls).toHaveLength(0);
  });

  it('refuses the two boundary stations being the same', () => {
    renderView();
    fillForm();
    fireEvent.change(screen.getByLabelText('Downstream station'), { target: { value: 'S1063' } });
    const button = screen.getByRole('button', { name: 'Onboard corridor' });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(expect.stringContaining('must be different'));
  });

  it('sends the documented multipart body and follows the job to done', async () => {
    renderView();
    fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Onboard corridor' }));

    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/corridors'))).toBe(true);
    });
    const posted = calls.find((c) => c.method === 'POST' && c.url.endsWith('/corridors'))!;
    const form = posted.form!;
    expect(form.get('name')).toBe('mndot_i94_wb');
    expect(form.get('bbox')).toBe('44.9425 -93.099 44.9613 -92.9612');
    expect(form.get('bearing_deg')).toBe('265');
    expect(form.get('upstream_station')).toBe('S1063');
    expect(form.get('downstream_station')).toBe('S97');
    // the documented defaults travel with the request rather than being
    // guessed by the server
    expect(form.get('window_s')).toBe('300');
    expect(form.get('t0_local')).toBe('06:00');
    expect(form.get('duration_s')).toBe('14400');
    expect(form.get('warmup_s')).toBe('1800');
    expect((form.get('detectors') as File).name).toBe('det.csv');
    expect((form.get('stations') as File).name).toBe('sta.csv');

    // the panel tracks the stages while the job runs …
    expect(await screen.findByText(/network · 1\/5 stages/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Run 20 seeds' })).toBeNull();

    // … and then reports what was discovered and derived
    corridorDone = true;
    expect(await screen.findByText(/11.8 km chain of/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText(/4275 veh\/h/)).toBeInTheDocument();
    const ramps = within(screen.getByLabelText('ramp demand'));
    expect(ramps.getByText('detector D1064')).toBeInTheDocument();
    // the two ramps with no detector say so instead of borrowing one
    expect(ramps.getAllByText('conservation')).toHaveLength(2);
    // the bracket the balance could not close is stated, not hidden
    expect(screen.getByText(/could not be assigned to any ramp/)).toHaveTextContent('775 veh/h');
    expect(screen.getByText(/Zeroed: Kellogg Blvd exit/)).toBeInTheDocument();
    expect(screen.getByText(/S1450 sits 168 m from the centreline/)).toBeInTheDocument();
    // the lane pre-flight: the station the map and the inventory disagree on
    expect(screen.getByText(/lanes vs inventory: 1 of 2 stations match/)).toBeInTheDocument();
    expect(screen.getByText(/S1063 at 1.1 km/)).toHaveTextContent(
      'the map carries 4 lanes, the inventory says 3',
    );
    // what the build stage ran, and the audit the fixes were derived from
    // (a count only: the table is the audit that stands)
    expect(screen.getByText(/^applied:/)).toHaveTextContent(
      'ramp guessing on; split fixes: 1 applied, 1 remaining',
    );
    expect(screen.getByText('before fixes: 2 defects')).toBeInTheDocument();
    // the split audit: the exit compiled from a lane ramp guessing added on
    // the wrong side is a red verdict with its remedy under the row
    const splits = within(screen.getByLabelText('split audit'));
    expect(splits.getByText('10.7 km')).toBeInTheDocument();
    expect(splits.getByText('1001426896 → 82150350')).toBeInTheDocument();
    expect(splits.getByText('ADDED LANE, WRONG SIDE')).toHaveClass('verdict-defect');
    expect(splits.getByText(/^remedy:/)).toHaveTextContent('--ramps.unset 1001426896');
  });

  it('launches 20 seeds and only then offers the observed report', async () => {
    renderView();
    fillForm();
    corridorDone = true;
    fireEvent.click(screen.getByRole('button', { name: 'Onboard corridor' }));

    const launch = await screen.findByRole('button', { name: 'Run 20 seeds' }, { timeout: 4000 });
    const report = screen.getByRole('button', { name: 'Report against observations' });
    expect(report).toBeDisabled();
    expect(report).toHaveAttribute('title', expect.stringContaining('finished run'));

    fireEvent.click(launch);
    await waitFor(() => {
      expect(screen.getByText('runs view')).toBeInTheDocument();
    });
    const runPost = calls.find((c) => c.method === 'POST' && c.url.endsWith('/runs'))!;
    expect(runPost.body).toEqual({ scenario_id: SCENARIO_ID, replicates: 20 });
  });

  it('leaves the Advanced fields out of a request that names none', async () => {
    renderView();
    fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Onboard corridor' }));

    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/corridors'))).toBe(true);
    });
    const form = calls.find((c) => c.method === 'POST' && c.url.endsWith('/corridors'))!.form!;
    // an empty mapping is not "the canonical columns": the field is absent,
    // so the loader reads the contract's own spellings
    expect(form.get('column_map')).toBeNull();
    expect(form.get('idm_calibration')).toBeNull();
    expect(form.get('source')).toBeNull();
    // the two build switches are on by default and always travel with the
    // request, so what the server ran is what the form showed
    expect(form.get('ramp_guessing')).toBe('true');
    expect(form.get('split_fixes')).toBe('true');
  });

  it('shows the build switches ticked and sends "false" for an unticked one', async () => {
    renderView();
    fillForm();
    const guess = screen.getByLabelText('Guess acceleration lanes (netconvert ramps.guess)');
    const fixes = screen.getByLabelText('Fix exits compiled on the wrong side (split audit)');
    expect(guess).toBeChecked();
    expect(fixes).toBeChecked();
    fireEvent.click(fixes);
    expect(fixes).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Onboard corridor' }));

    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/corridors'))).toBe(true);
    });
    const form = calls.find((c) => c.method === 'POST' && c.url.endsWith('/corridors'))!.form!;
    expect(form.get('ramp_guessing')).toBe('true');
    expect(form.get('split_fixes')).toBe('false');
  });

  it('sends the Advanced column map, calibration path and source', async () => {
    renderView();
    fillForm();
    fireEvent.change(screen.getByLabelText('Timestamp column'), { target: { value: 'ts' } });
    fireEvent.change(screen.getByLabelText('Flow column'), { target: { value: 'volume' } });
    // typed and then cleared: an emptied field is not part of the mapping
    fireEvent.change(screen.getByLabelText('Speed column'), { target: { value: 'mph' } });
    fireEvent.change(screen.getByLabelText('Speed column'), { target: { value: '  ' } });
    fireEvent.change(screen.getByLabelText('IDM calibration (server path)'), {
      target: { value: '  artifacts/idm_i24_capacity.json  ' },
    });
    fireEvent.change(screen.getByLabelText('Source'), {
      target: { value: 'MnDOT IRIS 30-second archive, 2026-04-14' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Onboard corridor' }));

    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/corridors'))).toBe(true);
    });
    const form = calls.find((c) => c.method === 'POST' && c.url.endsWith('/corridors'))!.form!;
    // exactly the fields that were named, as the API's column_map JSON object
    expect(JSON.parse(String(form.get('column_map')))).toEqual({
      timestamp: 'ts',
      flow: 'volume',
    });
    expect(form.get('idm_calibration')).toBe('artifacts/idm_i24_capacity.json');
    expect(form.get('source')).toBe('MnDOT IRIS 30-second archive, 2026-04-14');
  });

  it('reports the finished run against the uploaded observations', async () => {
    // coming back to the view after the run finished: the panel is restored
    // from the API, not from a copy in this browser
    window.localStorage.setItem(
      'flowstate.onboard.last',
      JSON.stringify({ corridor_id: CORRIDOR_ID, run_id: RUN_ID }),
    );
    corridorDone = true;
    runStatus = 'done';
    renderView();

    const report = await screen.findByRole(
      'button',
      { name: 'Report against observations' },
      { timeout: 4000 },
    );
    await waitFor(() => {
      expect(report).toBeEnabled();
    });
    fireEvent.click(report);
    await waitFor(() => {
      expect(screen.getByText('reports view')).toBeInTheDocument();
    });
    const posted = calls.find((c) => c.method === 'POST' && c.url.endsWith('/reports'))!;
    expect(posted.body).toEqual({
      run_ids: [RUN_ID],
      title: 'mndot_i94_wb against its detectors',
      observations_path: OBSERVATIONS,
    });
  });
});

/** The form used to name one problem at a time, beside the button, with an
 * inline message only for the bounding box: fixing one field to be told about
 * the next is a round trip per field. Every failing field now states its own
 * reason under its own control. */
describe('OnboardView field-level validation', () => {
  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    window.localStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async (): Promise<Response> => json({}, 404)));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('states every failing field at once, next to the field', () => {
    renderView();
    fireEvent.change(screen.getByLabelText('Bounding box (S, W, N, E)'), {
      target: { value: '44.94, -93.10' },
    });
    fireEvent.change(screen.getByLabelText('Bearing (deg)'), { target: { value: '400' } });
    fireEvent.change(screen.getByLabelText('Warm-up (s)'), { target: { value: '99999' } });

    // every wrong field says so simultaneously (the first problem is also
    // repeated beside the button, which is why this one matches twice)
    expect(screen.getAllByText(/letters, digits/).length).toBe(2);
    expect(screen.getAllByText(/needs four numbers/).length).toBeGreaterThan(0);
    expect(screen.getByText(/compass degrees within/)).toBeInTheDocument();
    expect(screen.getByText('Name the upstream boundary station')).toBeInTheDocument();
    expect(screen.getByText('Name the downstream boundary station')).toBeInTheDocument();
    expect(screen.getByText('Choose the detector CSV')).toBeInTheDocument();
    expect(screen.getByText('Choose the stations CSV')).toBeInTheDocument();
    expect(screen.getByText(/shorter than the duration/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Onboard corridor' })).toBeDisabled();
  });
});

/** A corridor onboarded in another session exists only on the server: its
 * scenario and its observations artifact are server-side, and this browser's
 * localStorage remembers one onboarding at most. `GET /corridors` is what
 * makes those corridors reachable from here — listed, and selectable as the
 * target of the two follow-on actions. */
describe('OnboardView corridor history', () => {
  const OTHER_ID = 'cor_0000aaaa1111';

  function listRow(id: string, name: string, done: boolean, created: string): unknown {
    return {
      corridor_id: id,
      name,
      status: done ? 'done' : 'failed',
      progress: { stage: done ? 'done' : 'network', completed_stages: done ? 5 : 1, total_stages: 5 },
      scenario_id: done ? SCENARIO_ID : null,
      preset_filename: done ? `${name}.yaml` : null,
      config_hash: done ? 'a1b2c3d4e5f6' : null,
      observations_path: done ? OBSERVATIONS : null,
      corridor_dir: `corridors/${id}`,
      error: done ? null : 'no chain on that bearing',
      error_kind: done ? null : 'corridor_network',
      created_at: created,
    };
  }

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    window.localStorage.clear();
    calls.length = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        const method = init?.method ?? 'GET';
        calls.push({ url, method });
        if (url.endsWith('/corridors') && method === 'GET') {
          return json([
            listRow(CORRIDOR_ID, 'mndot_i94_wb', true, '2026-09-23T06:00:00'),
            listRow(OTHER_ID, 'mndot_i35_nb', false, '2026-09-22T06:00:00'),
          ]);
        }
        if (url.includes('/corridors/')) return json(corridorRow('done', true));
        if (url.includes('/runs/')) return json(runRow('done'));
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('lists what the server has onboarded, newest first', async () => {
    renderView();
    const table = await screen.findByRole(
      'table',
      { name: 'corridors on this server' },
      { timeout: 4000 },
    );
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((r) => within(r).getAllByRole('cell')[0].textContent)).toEqual([
      'mndot_i94_wb',
      'mndot_i35_nb',
    ]);
    // the row states what the onboarding did, not what the corridor does
    expect(within(rows[0]).getByText('done')).toBeInTheDocument();
    expect(within(rows[1]).getByText('failed')).toBeInTheDocument();
    expect(within(rows[0]).getByText('2026-09-23 06:00:00 UTC')).toBeInTheDocument();

    // the two Use buttons read the same to a screen reader until the status
    // is part of the name — and picking the failed attempt is a different act
    expect(
      within(rows[0]).getByRole('button', { name: 'use mndot_i94_wb (done)' }),
    ).toBeInTheDocument();
    expect(
      within(rows[1]).getByRole('button', { name: 'use mndot_i35_nb (failed)' }),
    ).toBeInTheDocument();
  });

  it('prefills the run and report actions from the corridor that is picked', async () => {
    // a run this browser launched for that corridor, before the page reloaded
    window.localStorage.setItem(
      'flowstate.onboard.last',
      JSON.stringify({ corridor_id: CORRIDOR_ID, run_id: RUN_ID }),
    );
    renderView();
    const use = await screen.findByRole(
      'button',
      { name: 'use mndot_i94_wb (done)' },
      { timeout: 4000 },
    );
    fireEvent.click(use);

    // the full row is re-read: a listing carries no summary, and the panel
    // states what the onboarding found rather than reconstructing it
    await waitFor(() => {
      expect(calls.some((c) => c.url.includes(`/corridors/${CORRIDOR_ID}`))).toBe(true);
    });
    expect(await screen.findByText(/11.8 km chain of/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run 20 seeds' })).toBeEnabled();
    // and the finished run it already has makes the observed report available
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Report against observations' })).toBeEnabled();
    });
  });
});

/** The shell's CORRIDOR label named whatever was active before — or nothing —
 * after an onboarding finished. A finished corridor is the corridor being
 * worked on. */
describe('OnboardView active-corridor label', () => {
  function CorridorProbe(): JSX.Element {
    const { corridor } = useAppState();
    return <div data-testid="active">{corridor ?? '— none —'}</div>;
  }

  beforeEach(() => {
    setOfflineFallback(false);
    clearAuthFailure();
    window.localStorage.clear();
    calls.length = 0;
    corridorDone = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        const method = init?.method ?? 'GET';
        calls.push({ url, method });
        if (url.endsWith('/corridors') && method === 'POST') {
          return json(corridorRow('extract', false), 202);
        }
        if (url.includes('/corridors/')) {
          return json(corridorRow(corridorDone ? 'done' : 'network', corridorDone));
        }
        return json({ detail: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('names the corridor in the shell once the job finishes', async () => {
    render(
      <AppStateProvider>
        <CorridorProbe />
        <MemoryRouter initialEntries={['/onboard']}>
          <Routes>
            <Route path="/onboard" element={<OnboardView />} />
          </Routes>
        </MemoryRouter>
      </AppStateProvider>,
    );
    fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Onboard corridor' }));

    // while it runs, nothing is claimed
    expect(await screen.findByText(/network · 1\/5 stages/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByTestId('active')).toHaveTextContent('— none —');

    corridorDone = true;
    await waitFor(
      () => expect(screen.getByTestId('active')).toHaveTextContent('mndot_i94_wb'),
      { timeout: 6000 },
    );
  }, 15000);
});

/** A refusal is the whole answer to the click. The 409 that says a corridor of
 * that name already exists used to be gone in 5.2 s — while the operator was
 * still reading the form it came from — and there was no way to keep it or to
 * clear it by hand. An error toast now outlives the acknowledgements and has a
 * dismiss control. */
describe('refusal toasts', () => {
  const MSG = "corridor 'mndot_i94_wb' already exists";

  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps a 409 up past 15 s, and dismisses it when asked', () => {
    vi.useFakeTimers();
    render(<Toasts />);

    act(() => {
      toast('error', MSG);
      toast('ok', 'preset stored');
    });
    expect(screen.getByText(new RegExp('already exists'))).toBeInTheDocument();

    // the acknowledgement goes at its own pace; the refusal stays
    act(() => {
      vi.advanceTimersByTime(TOAST_MS + 200);
    });
    expect(screen.queryByText(/preset stored/)).toBeNull();
    expect(screen.getByText(new RegExp('already exists'))).toBeInTheDocument();

    // still up just short of the error lifetime
    act(() => {
      vi.advanceTimersByTime(ERROR_TOAST_MS - TOAST_MS - 400);
    });
    expect(screen.getByText(new RegExp('already exists'))).toBeInTheDocument();
    expect(ERROR_TOAST_MS).toBeGreaterThanOrEqual(15_000);

    // and it goes when the operator says so, not only when it times out
    fireEvent.click(screen.getByRole('button', { name: `dismiss: ${MSG}` }));
    expect(screen.queryByText(new RegExp('already exists'))).toBeNull();
  });
});
