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

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuthFailure, OFFLINE_WRITE_MESSAGE, setOfflineFallback } from '../api/client';
import { AppStateProvider, useAppState } from '../components/AppContext';
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
    expect(button).toHaveAttribute('title', expect.stringContaining('Name'));

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
    expect(button).toHaveAttribute('title', 'Choose the detector CSV');
    fireEvent.change(screen.getByLabelText('Detector CSV'), {
      target: { files: [new File(['x'], 'det.csv')] },
    });
    expect(button).toHaveAttribute('title', 'Choose the stations CSV');
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
    expect(button).toHaveAttribute('title', OFFLINE_WRITE_MESSAGE);
    expect(calls).toHaveLength(0);
  });

  it('refuses the two boundary stations being the same', () => {
    renderView();
    fillForm();
    fireEvent.change(screen.getByLabelText('Downstream station'), { target: { value: 'S1063' } });
    const button = screen.getByRole('button', { name: 'Onboard corridor' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', expect.stringContaining('must be different'));
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
