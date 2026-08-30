# FlowState v2

**Calibrated corridor digital twins for studying — and dissipating — stop-and-go
("phantom") traffic waves with sparse controlled vehicles and variable speed
limits.**

FlowState v2 turns a freeway corridor into a reproducible simulation study:
onboard the corridor (from OpenStreetMap or a built-in scenario), calibrate
car-following and demand against public trajectory/detector data, run seeded
multi-replicate experiments with smoothing controllers or VSL, and generate a
FHWA-style calibration/validation report a reviewer can rerun. The phenomenon
at the core — string instability growing into stop-and-go waves — is
*emergent* in the microscopic tier, not hand-seeded, which is what makes
controller results on it meaningful. Every headline number in this repository
traces to a seeded run with confidence intervals, including the results that
failed.

![FlowState dashboard — run detail with space-time speed field](docs/figures/dashboard_run_detail.png)

> **On v1:** FlowState v1 studied controlled dissipation of *seeded* shocks in
> a first-order (LWR) model — a model class that is string-stable by
> construction and cannot form phantom jams. v2 studies *emergent* stop-and-go
> waves in a calibrated microscopic model. v1 remains available at
> [abc000cool/FlowState](https://github.com/abc000cool/FlowState) as motivated
> preliminary work.

## Architecture

Two simulation tiers with strictly separated jobs, one shared controller
library, and a validation stack that gates what may be claimed:

```mermaid
flowchart LR
  subgraph DATA["Public data"]
    NGSIM["NGSIM US-101<br/>trajectories"]
    PEMS["PeMS / CSV<br/>detector data"]
    OSM["OpenStreetMap<br/>(any-city bbox)"]
  end

  subgraph CAL["calibration"]
    IDM["IDM population fit<br/>(gap-based DE, holdout)"]
    FD["Triangular FD fit<br/>(bootstrap CIs)"]
    DEM["Demand fitter<br/>(GEH loop)"]
  end

  subgraph SIM["Simulation"]
    MICRO["microsim — PRIMARY<br/>SUMO 1.27.1 + IDM/EIDM, libsumo<br/>emergent waves, per-vehicle control"]
    MACRO["macrosim — SCREENING ONLY<br/>Numba CTM/LWR + moving flux cap<br/>fast sweeps; no phantom-jam claims"]
  end

  CTRL["controllers (pure functions)<br/>FollowerStopper · PI-sat · JAD · VSL<br/>+ Gymnasium hook"]

  subgraph VAL["validation"]
    MET["metrics: σ_v, throughput,<br/>travel time, fuel, waves"]
    CRIT["FHWA-style criteria<br/>(GEH, RMSPE, wave speed)"]
    REP["auto-generated<br/>calibration report"]
  end

  subgraph PROD["Product layer"]
    API["FastAPI + RQ workers<br/>(async jobs, API key)"]
    FE["React dashboard<br/>(heatmaps, sweeps, reports)"]
  end

  NGSIM --> IDM
  PEMS --> FD
  PEMS --> DEM
  OSM --> MICRO
  IDM --> MICRO
  FD --> MACRO
  DEM --> MICRO
  CTRL --> MICRO
  CTRL --> MACRO
  MICRO --> MET
  MACRO -. "screening label only" .-> MET
  MET --> CRIT --> REP
  API --> MICRO
  API --> MACRO
  API --> FE
```

The macro tier is the ported v1 engine with a full test battery (Riemann
exactness, conservation, CFL guards). Its outputs are labeled
`tier="screening"` and the API refuses to build a validation report from them
— first-order models cannot form phantom jams, so they don't get to make
claims about them.

## Results

All results below are reproducible from committed scripts and artifacts;
sources: [docs/M3_RESULTS.md](docs/M3_RESULTS.md),
[docs/M3_US101_VALIDATION.md](docs/M3_US101_VALIDATION.md),
[docs/M2_RESULTS.md](docs/M2_RESULTS.md).

**Ring benchmark (CI-gated).** On the 230 m / 22-vehicle Sugiyama ring,
stop-and-go emerges with no seeding (sustained σ_v ≈ 2.4 m/s, full stops,
jam front drifting backward at ≈ −14 km/h) and a single FollowerStopper
vehicle — 1 of 22 — dampens it (σ_v reduction ≥ 25% asserted, ≈ 100%
measured; minimum speed raised from 0 to ≈ 2 m/s). This reproduction of
Sugiyama et al. (2008) and Stern et al. (2018) runs as a permanent
integration test.

**Penetration × compliance sweep** — 540 emergent (unseeded) SUMO runs on the
synthetic 10 km corridor, 27 cells × 20 common-random-number seeds:

![temporal σ_v vs penetration](docs/figures/m3_sigma_v_vs_penetration.png)

![paired σ_v reduction matrix](docs/figures/m3_sigma_v_reduction_matrix.png)

![baseline vs FollowerStopper space-time speed](docs/figures/m3_spacetime_baseline_vs_fs.png)

| Result | Number (mean, 95% CI, n = 20 seeds) |
|---|---|
| Baseline waves | 3.85 [2.59, 5.11] waves/run; temporal σ_v 3.39 [2.83, 3.94] m/s |
| FollowerStopper 5% / 100% | σ_v 1.31 [1.24, 1.38] m/s; 0.15 [−0.02, 0.32] waves/run |
| FollowerStopper 10–20% / 100% | zero detected waves in all 20 replicates |
| Already resolvable at 1% / 100% | paired σ_v reduction +24.5% [+17.7, +31.4] |
| Throughput cost | none resolved (+11.6 veh/h [−1.3, +24.4] at 5% / 100%) |
| Fuel | −2.8% [−3.8, −1.7] at 1% / 100% → −5.7% [−7.2, −4.2] at 20% / 100% |
| Compliance × penetration | effects collapse onto the complied share of the fleet — half the compliance ≈ double the penetration needed |
| PI-saturation at 5% / 100% | **fails outright**: throughput 79 [29, 130] veh/h (94% collapse, gridlock) with ring-tuned defaults — reported as-is |
| US-101 replica validation | **1 PASS / 5 FAIL** on the FHWA-style criteria table |
| Flux-cap comparison | the v1 ρ·v* cap (discrete Delle Monache–Goatin) beats the reduced-capacity variant against micro ground truth: paired speed-RMSE difference 0.84 m/s [0.36, 1.33] |

**Why the US-101 validation fails, in one line each:** the site is a 640 m
camera range whose congestion enters from downstream (imposing the measured
downstream boundary halves speed RMSPE from 72.8% to 36.6% and produces
backward waves in 20/20 replicates — but demonstrates propagation, not
prediction); the replica lacks the in-span on-ramp merge, so the observed
speed gradient is reversed; and the IDM population fitted on raw NGSIM noise
discharges queues too slowly, giving simulated wave speeds of ~11 km/h
against ~16 km/h observed. All of it is documented, none of it is hidden:
[docs/M3_US101_VALIDATION.md](docs/M3_US101_VALIDATION.md), with the
auto-generated report at
[docs/reports/us101_replica/report.md](docs/reports/us101_replica/report.md).

## Quickstart

One command (Docker + Compose):

```sh
docker compose up -d --build
```

Then open <http://localhost:8000> — the dashboard is served at `/`, the API
under `/api/v1/...`, OpenAPI docs at `/docs`, health at `/healthz`. The stack
is API + RQ worker + Redis; all simulations run on the worker, never in a
request handler. Both containers run as a non-root user (uid 10001), and the
published port is bound to `127.0.0.1` — reachable from your machine, not from
the network. Compose expects to own its results volume, so a `flowstate-runs`
volume left over from a pre-2.0.0 image needs `docker compose down -v` once.

**API key:** every `/api/...` route requires an `X-API-Key` header. Compose
starts with `flowstate-local-dev`, which is in this file and therefore not a
secret — it is safe only because the port is on loopback. Before you publish
the port (uncomment the plain `8000:8000` mapping in `docker-compose.yml`),
set your own:

```sh
FLOWSTATE_API_KEY=your-secret docker compose up -d
```

The API's own built-in default, `dev-key-change-me`, is accepted only under
the inline queue (the no-Docker dev path below). A deployed service —
`FLOWSTATE_QUEUE=redis`, which is what Compose runs — refuses to start on it
and tells you to set `FLOWSTATE_API_KEY`.

The dashboard ships pre-filled with `dev-key-change-me`, so on the Compose
stack open its **Settings** drawer once and paste in the key the API is
actually running (`flowstate-local-dev`, or your own); calls 401 until you do.
Whatever you enter is kept in the browser's `localStorage` under
`flowstate.apiKey` — it is one shared key per deployment, not a per-user
login, and any script that achieves XSS on the page can read it straight back
out, so treat it as a deployment credential and rotate it like one. Real auth
is a Phase 4 concern.

Smoke test:

```sh
curl -s http://localhost:8000/healthz
curl -s -H "X-API-Key: flowstate-local-dev" http://localhost:8000/api/v1/scenarios/preset
```

### Dev path (no Docker)

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12; SUMO 1.27.1 installs
from PyPI wheels as part of the sync.

```sh
uv sync                                  # workspace + dev tools
uv run pytest tests -q -m "not slow"     # fast test battery
uv run uvicorn api.main:app --reload     # API on :8000, inline job queue
```

The inline queue (`FLOWSTATE_QUEUE=inline`, the default outside Docker) runs
jobs synchronously in-process — good for small local runs, not for real
sweeps. For the frontend against a local API: `cd frontend && npm install &&
npm run dev` (Vite on :5173, CORS pre-configured).

## Status

Under active construction. Milestone tracker:

- [x] **M0** — monorepo + macro tier ported with test battery, CI green
- [x] **M1** — ring-road emergence + single-AV dampening reproduced in CI
- [x] **M2** — FD + IDM population calibration from public data
- [x] **M3** — full 540-run sweep with CIs; US-101 validation executed end-to-end (criteria honestly mixed: see docs/M3_US101_VALIDATION.md)
- [x] **M4** — FastAPI service + dashboard + Docker
- [x] **M5** — hardening (load test: [docs/M5_LOAD_TEST.md](docs/M5_LOAD_TEST.md)), docs, versioned release

## Documentation

* [docs/README.md](docs/README.md) — index of all results documents,
  contracts, the generated report, and figures
* [docs/gallery/](docs/gallery/README.md) — demo corridor gallery: I-24
  (Nashville) and US-75 (Dallas) onboarded from OSM bboxes in minutes —
  an onboarding demo, explicitly not a validity claim
* [CHANGELOG.md](CHANGELOG.md) — release history
* [LICENSE](LICENSE) — Apache-2.0

## Limitations and roadmap

What the current results do **not** establish, distilled from the M2/M3
documents (each has the full version):

1. **The sweep corridor is synthetic.** All 540-run sweep results come from
   an EIDM-default fleet on a demand profile tuned for wave emergence, not a
   validated real corridor. They characterize controller behavior under
   controlled conditions; they are not field predictions.
2. **The US-101 site is short.** 640 m is a third of a typical wave's
   wavelength; GEH rests on 9 bins; the standard wave detector degenerates on
   a wall-to-wall congested site. The with-boundary arm shows the model
   propagates an imposed congestion state consistently — not that it predicts
   onset.
3. **Raw NGSIM is noisy.** The IDM population was fitted on the raw Socrata
   export (differentiated-position speed noise; a_max biased high), the
   plausible cause of too-slow simulated waves and queue under-discharge.
   Re-fitting on the Montanino–Punzo reconstruction is the upgrade path.
4. **The macro tier screens; it never validates.** CTM outputs are labeled
   `tier="screening"` and are refused by the report generator by design.
5. **Controller caveats.** JAD has run only with its perfect wave oracle
   (best case); PI-saturation's ring-tuned defaults gridlock an open corridor
   and need retuning before any fair comparison.

Roadmap after the v2.0 release: the `i24_replica` flagship validation on
I-24 MOTION data (the gallery already onboards the corridor geometry),
reconstructed-NGSIM recalibration, ramp/auxiliary-lane modeling for the
US-101 replica, a flow-based downstream boundary variant, the CTM/Kalman
state-estimation tier, and RL controllers through the existing Gymnasium
hook.

## Non-negotiables

1. No unvalidated claims — every headline number reproduces from a seeded run.
2. Emergent, not seeded — seeded experiments are always labeled `seeded=True`.
3. Standard metrics only: throughput, travel time, σ_v, fuel/energy, wave
   count/speed/amplitude.
4. No consumer nav-app advisory features — this is simulation and decision
   support.
5. Reproducibility: explicit seeds, config hashes, golden regression tests.
6. Honest uncertainty: headline metrics are mean ± 95% CI over ≥ 20 replicates.

## License

Apache-2.0. See [LICENSE](LICENSE).
