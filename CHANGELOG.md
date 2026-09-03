# Changelog

All notable changes to FlowState are documented here. Every headline number
below traces to a committed, seeded artifact (CLAUDE.md §0.1/§0.5); nothing
is quoted that cannot be reproduced from the referenced runs.

## [Unreleased] — I-24 MOTION flagship (docs/ROADMAP.md §1)

### Added

- **Streaming I-24 MOTION loader** (`calibration.loaders.i24motion`):
  `iter_i24_documents` decodes the INCEPTION MongoDB export one document at a
  time straight out of the zip (the 30 Nov 2022 run is a 19.5 GB JSON array of
  816,694 documents in a 5.8 GB zip; it is never extracted), and
  `convert_i24_to_parquet` writes filtered, decimated Parquet with a
  per-fragment table and provenance `meta.json`. The schema was reconciled
  against the real file and the official v1.x data documentation:
  `_id` is `{"$oid": ...}`, `x_position` is the *back*-center roadway
  coordinate in feet (MM 60 ≡ 316,800 ft), `y_position` is positive westbound
  with lane 1 (HOV) at 12–24 ft, and every document is a trajectory
  **fragment**. The loader's `x` is the front bumper on a travel-oriented axis
  so the existing bumper-to-bumper gap logic applies unchanged; the loader
  tests' expected gap changed accordingly (spacing minus *follower* length,
  not minus leader length). `scripts/i24_extract.py` runs the conversion
  (westbound: 576,511 fragments → 42.8 M rows at 5 Hz, 993 MB, 309 s).
- **Ramps and boundaries on OSM corridors** (`flowstate_core.config.RampSpec`,
  `OSMNetwork.ramps`, `OSMNetwork.boundary`; docs/CONTRACTS.md §2). A real
  corridor exchanges traffic at interchanges; the I-24 westbound testbed has
  two on-ramps and two off-ramps inside the span whose flows are a sizable
  share of the mainline, and the US-101 replica's missing merge was a
  documented structural failure. On-ramps insert their own seeded demand on
  the ramp's first edge; off-ramps divert a per-vehicle seeded fraction; ramp
  edges are kept through OSM pruning and their connectivity is checked before
  SUMO starts; `meta.json` records per-ramp planned/departed/exiting counts.
  The measured-boundary schedule now also applies to the last corridor edge
  of an OSM network, and OSM insertion uses the entry edge's real lane count.
  Integration-tested on a hand-built interchange fixture
  (`tests/test_microsim/test_microsim_osm_ramps.py`). Two SUMO facts learned
  and encoded in that fixture: a ramp must feed an auxiliary lane (a ramp
  squeezed into a same-width edge faces priority-junction gap acceptance and
  never merges against a steady stream), and hand-drawn ramps must leave at
  gore-like shallow angles (SUMO caps turning speed by curvature, so a steep
  link is an artificial 5 m/s bottleneck).
- `FleetSpec.lc_strategic` (SUMO `lcStrategic`, default 1.0 = SUMO's own
  default, written on vTypes only when changed). On the I-24 replica the
  default leaves 894 stalled 10-s samples in two hours at the Hickory Hollow
  diverge and a 29.8 km/h mean speed over the kilometre upstream of it — a
  fixed bottleneck the data does not have — against 44 samples / 84.7 km/h at
  5.0 and 3 / 86.8 at 20.0 (same seed, same demand). The replica uses 5.0.
- `FleetSpec.lc_keep_right` (SUMO `lcKeepRight`, default 1.0). The default
  encodes a keep-right obligation US freeways do not have: on the I-24
  replica it spreads vehicle-time 24/25/25/27% across the four lanes (left
  to right) but crawls at 23–27 km/h in the two right lanes through the Old
  Hickory merge while the left lanes run at 65–97 km/h — 11,535 stalled
  10-s samples in two hours. At 0 the lane shares become 32/26/22/20%
  against the observed 30/24/20/26% (all observed lanes at 30–33 km/h) and
  the stalls fall to 200 (same seed, same demand). The replica uses 0.
- **I-24 replica validation (ROADMAP §1.4)** — `scripts/i24_validate.py`,
  `artifacts/i24_validation_{tracked,corrected,observed}.json`,
  `artifacts/i24_validation_waves_relative.json`, docs/I24_VALIDATION.md,
  auto-reports under `docs/reports/i24_replica/`. Honest outcome: **1 PASS /
  5 FAIL in both demand arms**, 20 seeds each. With demand as tracked (a lower
  bound) the span free-flows (RMSPE 183%); dividing demand by the
  instrument's apparent coverage produces a stop-and-go field that looks like
  the recording (RMSPE 36.8%, mean speed 28.0 vs 33.5 km/h observed), but the
  replica inserts only 82–84% of that demand, its jams are shallower than the
  real ones, and its backward fronts run at 8.7 km/h with the standard
  detector (12.4 with the relative one) against 14.2 (16.4) observed. The
  wave-speed prediction of docs/WAVE_SPEED_DIAGNOSIS.md is therefore **not
  confirmed on the corridor**; the same fleet reaches the 14–22 km/h band on a
  ring only above ~80 veh/km per lane, a density the replica does not reach.
- **I-24 calibration artifacts.** `artifacts/idm_i24.json`: IDM population
  fitted on all 17,652 ≥ 30 s leader-follower episodes of the westbound day
  (7× NGSIM's count; holdout gap RMSE 5.29 m vs 6.44 m for NGSIM; T = 1.51 s,
  a_max = 1.06 m/s², s0 = 2.53 m). `artifacts/demand_i24.json`: mainline
  inflow per 5 min for 06:30–08:30 CST, a lower bound at the instrument's
  tracking coverage. `artifacts/i24_replica_inputs.json`: every derived input
  of the replica (geometry mapping, ramp flows, exit fractions, boundary
  schedule, coverage factors). See docs/I24_DATA.md.
- **Relative-threshold wave detection** (`validation.waves.detect_waves(...,
  relative_frac=f)`, ROADMAP D1): thresholds at `f × p90` of the field's
  non-empty bin speeds, so stripes inside a field that is congested
  everywhere are segmented instead of being merged into one pinned blob (the
  documented failure above ~80 veh/km, docs/WAVE_SPEED_DIAGNOSIS.md). Unit
  tests plant a −16 km/h stripe in a 22 km/h field: the absolute detector
  returns one 0 km/h blob, relative mode recovers −16 km/h. A labeled variant;
  the §7.1 criterion stays on the absolute threshold.
- **Bounded-memory trajectory capture.** `microsim.runner` now streams the
  trajectory table to Parquet in 500k-row groups during the run instead of
  holding every sampled row as Python lists until the end. A 7,800 s
  four-lane I-24 run captures ~10 M rows; the old buffer cost several GB per
  process and eight concurrent workers took a 16 GB machine down twice
  during the I-24 validation. File content is unchanged (the byte-exact
  determinism test still passes); peak memory per worker drops to well under
  1 GB. `validation.metrics.compute_metrics` and the report generator's
  contour renderer now read only the trajectory columns they use, which cuts
  their peak from ~7 GB to a few GB on the same runs.
- `calibration.fd_fit.fit_triangular_fd(n_procs=...)` refits bootstrap
  resamples in a process pool; the resample draws are made up front from the
  seeded generator in serial order and each refit is deterministic, so the
  artifact is identical to the serial path (each exact-LP quantile refit costs
  ~2 min on the 237k-bin I-24 data set).

### Changed

- **Config hashes change for every scenario**: `OSMNetwork` gained
  `boundary` and `ramps` (defaults `None` / `[]`) and `FleetSpec` gained
  `lc_strategic` (default 1.0), and `config_hash` covers the whole serialized
  config (CLAUDE.md §0.5) — the same situation as the `OracleSpec` addition
  in 2.1.0. Physics is unchanged for every existing scenario: the defaults
  reproduce SUMO's own behaviour and the route files they generate are
  byte-identical (`tests/test_microsim/test_microsim_vehicles.py::
  TestLcStrategic`). Hashes quoted in earlier documents (the gallery's
  `7529e2b0dd63` / `f8c6011feb3e`, the M3 and 2.1.0 experiments) refer to the
  pre-Phase-6 schema.
- `runs/` (12 GB of regenerable per-replicate trajectories) was deleted to
  make room for the I-24 processing; the small machine-readable result files
  the results documents cite were preserved verbatim under
  `artifacts/run_summaries/<experiment>/` (see its README).

## [2.1.0] — 2026-09-02

### Added

- **Delayed / noisy wave-detection oracle** (`OracleSpec` on `AVSpec`),
  completing CLAUDE.md §4.3's requirement that the oracle be swappable and that
  every headline JAD result also be reported under a degraded oracle. `delay_s`
  makes the controller read the traffic state as it was `delay_s` ago;
  `amplitude_noise_frac` applies seeded multiplicative error per bin. Default is
  a perfect oracle, so existing configs are unchanged.
- `docs/jad_derivation.md` — the JAD intercept-timing derivation with geometry
  that CLAUDE.md §4.3 required and that was never written.

### Changed

- **Diagnosed the US-101 wave-speed criterion failure** as a site/operating-density
  artifact rather than a calibration defect. The calibrated fleet produces a mean
  emergent backward wave speed of 14.6 km/h on a ring at 60 veh/km (71% of fronts
  inside the 14-22 km/h band) - matching the independently fitted macroscopic FD's
  w = -14.6 km/h from a completely separate estimation path. Near critical density
  fronts are slower (11.4 km/h at 40 veh/km), close to the replica's measured
  10.7 km/h. The criterion still FAILS as measured on the 640 m replica and that
  stands in the validation table; what a passing test needs is now identified.
  Also recorded: an open 10 km corridor cannot reach the instability band with
  these parameters (insertion caps density near 25 veh/km vs a 31.8 veh/km
  threshold), and threshold-based wave detection breaks down above ~80 veh/km
  where the whole field reads as jammed. See `docs/WAVE_SPEED_DIAGNOSIS.md`.

- **The "free lunch" framing is now qualified by a real-geometry check.** The
  penetration ladder was rerun on the `us101_replica` (640 m of real 5-lane
  US-101, calibrated fleet, real demand, measured downstream boundary), 6 cells
  x 20 CRN seeds. The σ_v dose-response replicates cleanly (-8.2% at 1% to
  -53.2% at 20%, every step resolved). The no-cost result does not: that
  saturated site shows a resolved throughput cost of 0.3-1.6% and a resolved
  fuel *increase* of 1.4-2.7% at 1-10% penetration, against a 2.8-5.7% saving on
  the synthetic corridor. Smoothing holds; "for free" is corridor-dependent.
  See `docs/US101_PENETRATION.md`.

- **Config hashes changed for every scenario** when `OracleSpec` was added to
  `AVSpec`: `config_hash` covers the whole serialized config (CLAUDE.md §0.5),
  so a new field with a default still changes the digest. Hashes recorded in
  artifacts produced before this change (M2, M3, the US-101 validation) refer to
  the pre-Phase-5 schema and will not reproduce against current code, though the
  *physics* is unchanged: the `corridor_10km` baseline gives bit-identical
  metrics before and after (temporal σ_v 3.3851, CI lower bound 2.8283 in both
  the pre-change `pi_retune` run and the post-change `jad_oracle` run), which is
  the intended evidence that a default `oracle` is inert.

- **JAD's M3 bimodality is explained and resolved.** The perfect oracle was the
  cause: it fires the instant any bin in the 2 km lookahead qualifies, so the AV
  completes slow-in/hold/fast-out before the front arrives and re-triggers —
  30.7 acceleration sign-reversals per run against 16.6 under a 30 s delay —
  and each abrupt fast-out can seed a secondary wave. Under a perfect oracle
  5/20 seeds end worse than the uncontrolled baseline (one goes 1 -> 11 waves)
  and the wave-count benefit is not resolved. With 30-60 s latency and +/-20%
  noise, **no seed is worse than baseline** and wave count (-3.50 [-4.71,
  -2.29]), sigma_v and fuel all improve with resolved CIs. Realistic detection
  is what makes JAD reliable here. See `docs/JAD_ORACLE_RESULTS.md`.

### Fixed

- **PI-with-saturation now implements Stern et al. (2018) Eqs. (3)–(5).** The
  M3 sweep's headline failure — `pi_saturation` gridlocking the open corridor,
  94% throughput collapse — was caused by the CLAUDE.md §4.2 *simplification*
  (`v_target = 0.75 · platoon mean`), not by the literature controller. That
  factor does not appear in the paper: its target is the AV's own ≈38 s mean
  speed plus a bounded, non-negative gap-scheduled catch-up term, which cannot
  ratchet downward. Re-run on the same scenario and seeds, the faithful
  controller calms the corridor — σ_v −29.7% [−1.35, −0.66], waves −41.6%,
  fuel −2.9%, no resolved throughput cost (n = 20, paired) — and still dampens
  the ring (σ_v 2.07 → 0.46 m/s). The simplification is retained as
  `controllers.pi_meanfrac`, clearly labeled, so the M3 result stays
  reproducible. CLAUDE.md §4.2 corrected against the source per its own §13.
  See `docs/PI_CONTROLLER_FIX.md` and `artifacts/pi_retune_summary.json`.
- `pi_saturation`'s output range is `[0, U + v_catch]`, not `[0, U]`; the
  property test and docs/CONTRACTS.md §8 record the exception with its citation.

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
