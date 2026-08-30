# Changelog

All notable changes to FlowState are documented here. Every headline number
below traces to a committed, seeded artifact (CLAUDE.md §0.1/§0.5); nothing
is quoted that cannot be reproduced from the referenced runs.

## [2.0.0] — 2026-08-29

Complete rewrite of FlowState (M0–M5, developed and hardened 2026-08-29).
v2 replaces the v1 single-engine LWR study with a two-tier, calibrated,
honestly-validated corridor platform.

### Two-tier simulation engine (ADR-1)

- **Microscopic tier (primary):** Eclipse SUMO 1.27.1 + IDM/EIDM via
  libsumo, `microsim` package. String instability is **emergent** — waves
  grow from calibrated car-following dynamics and insertion jitter, never
  from hand-seeded shocks (seeded runs are permitted but labeled
  `seeded=True` everywhere).
- **Macroscopic tier (screening):** the v1 LWR/CTM Godunov engine ported
  into `macrosim` with a full test battery (Riemann exactness, mass
  conservation, CFL guards) and Numba compilation. It is repurposed for fast
  parameter screening and future state estimation; it makes **no** claims
  about phantom-jam formation or dissipation (LWR is string-stable by
  construction). Every macro artifact carries `tier="screening"` and can
  never back a validation report.

### Ring gate (M1)

- Permanent CI integration tests on the 230 m / 22-vehicle Sugiyama ring:
  stop-and-go waves **emerge** without any seeded perturbation (Sugiyama et
  al. 2008), and a single FollowerStopper AV dampens them (Stern et al.
  2018). These gates must stay green for any change to merge.
- Interpretable controller library (`controllers`): FollowerStopper,
  PI-with-saturation, Jam-Absorption Driving, and a VSL segment controller,
  all pure functions with literature-default parameters, plus a Gymnasium
  environment hook for future RL work (ADR-2: RL deferred).

### NGSIM calibration (M2)

- Real-data calibration pipeline (`calibration`) fed by the public NGSIM
  US-101 Socrata dump (provenance-hashed): triangular fundamental-diagram
  fit with bootstrap CIs and an IDM population fit from reconstructed
  car-following episodes with held-out gap RMSE (docs/M2_RESULTS.md).
- Loaders for NGSIM, PeMS, highD and I-24 MOTION formats; demand fitting
  for corridor scenarios.

### 540-run sweep (M3)

- Penetration × compliance × controller battery on the synthetic
  `corridor_10km` EIDM scenario: 27 cells × 20 common-random-number
  replicates = **540 emergent micro runs**, aggregated as mean ± 95% t CIs
  (docs/M3_RESULTS.md, `artifacts/m3_sweep_summary.json`). Standard metrics
  only: throughput, mean travel time, σ_v, fuel proxy, wave count/amplitude.

### US-101 validation with honest criteria (M3)

- `us101_replica` validated against the real NGSIM US-101 recording with
  FHWA-style acceptance criteria (GEH, segment-speed RMSPE, wave speed).
  Headline stated honestly: imposing the measured downstream boundary
  improves RMSPE from 72.8% to 36.6% and produces backward-propagating
  waves in all 20 replicates, but the replica **fails** the acceptance
  criteria for documented structural reasons (640 m site, missing on-ramp
  merge, IDM discharge behavior) — see docs/M3_US101_VALIDATION.md. No
  validated-corridor claim is made.
- End-to-end auto-report generation (`validation.report`), which refuses
  macro-only run sets.

### Product layer (M4)

- FastAPI service + RQ/Redis job queue + SQLite (WAL) metadata store
  (`api`): scenarios, runs, sweeps, calibrations, reports — no endpoint
  executes a simulation synchronously; every response carries
  `config_hash`. Single API-key auth.
- Mission-control dashboard (React/Vite) served single-origin by the API;
  one-command Docker deploy (`docker compose up -d --build`) with the same
  image for API and workers.

### Hardening and release (M5, this release)

- Load test: 10 concurrent macro sweep jobs plus a micro (SUMO) sweep
  through the full Docker stack with 2 workers — 42/42 runs done, zero
  failed, all 42 metrics endpoints HTTP 200, `/healthz` p95 under load
  ≤ 11.9 ms against a 500 ms budget. The run's own JSON output is committed
  as `artifacts/m5_load_test.json`; machine spec, per-phase tables and
  provenance for the numbers the JSON does not carry are in
  docs/M5_LOAD_TEST.md (`scripts/m5_load_test.py`).
- `pyarrow` constrained to `>=18,!=24.0.0` across the workspace. Pinning to
  24.0.0 (the version matching libsumo 1.27.1's bundled libarrow, which
  silences libsumo's import-time mismatch warning) was tried and **rejected
  with evidence**: on macOS the two identically-named `libarrow.2400.dylib`
  copies interact fatally — parquet writes after `import libsumo`
  intermittently livelock (hard spin in mimalloc's
  `mi_bitmap_clear_once_set`; reproduced in `tests/test_microsim`, which
  hung >50 min, and bisected to the `pyarrow.Table.from_pandas`/write in
  `microsim.runner`; the same file passes in 3.3 s on pyarrow 25.0.1).
  The cosmetic version-mismatch warning therefore stays; the real
  filesystem-factory-registry clash remains handled by the parquet-path
  shim in `tests/test_microsim/conftest.py` (verified still required on
  25.0.1), and production code writes parquet through open file objects and
  is unaffected on either version.
- Version 2.0.0 across the workspace (root + all packages).

### Retired from v1 (CLAUDE.md §12 — do not reintroduce)

- Phantom-jam formation/dissipation claims from LWR; the ">15% AV
  penetration → human gap exploitation" artifact; the non-standard
  "Efficient Frontier score" as a headline metric; consumer nav-app
  advisory-push features (Waze CIFS cannot carry speed advisories; Google
  Routes speed data is read-only); per-step unseeded compliance coin-flips;
  unseeded `np.random` calls; the v1 Flask endpoints.

## [1.0-legacy] — superseded

FlowState v1 — controlled dissipation of *seeded* shocks in a first-order
LWR model, with a Flask service and the "Efficient Frontier" score. Kept as
motivated preliminary work at
[abc000cool/FlowState](https://github.com/abc000cool/FlowState). Its engine
survives in v2 as the ported, tested `macrosim` screening tier, and its
hard-coded fundamental diagram as the labeled-uncalibrated
`v1_legacy` FD preset (`macrosim.fundamental.v1_legacy_fd`).
