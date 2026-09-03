# Paper spine (Track B1)

Working title: *Emergent stop-and-go waves and sparse-vehicle smoothing on a
calibrated I-24 replica: what a trajectory instrument can and cannot validate*.
Target: arXiv preprint (eess.SY, cross-list cs.MA), then a journal or
conference submission once the owner-blocked items in ROADMAP §6 land. Every
section below names the document that already carries its evidence; nothing
here is a new claim.

## 1. Introduction

- Stop-and-go waves as an emergent string instability; the CIRCLES / Stern et
  al. lineage of sparse Lagrangian control; why validation on a real corridor
  is the open question for anyone who wants to act on it (CLAUDE.md §0, ADR-1).
- What this paper adds: an open, reproducible corridor digital-twin pipeline
  (OSM → calibration → seeded experiments → FHWA-style criteria report), run
  end to end on a 4-mile instrumented freeway, with its failures reported.
- The v1 → v2 correction as motivation: a first-order model cannot form the
  waves it was said to dissipate (docs/LESSONS.md #1).

## 2. Method

- Two-tier engine and why: SUMO + IDM primary (emergent waves, per-vehicle
  control), CTM screening tier labeled as such (CLAUDE.md ADR-1; the flux-cap
  comparison as the one macro result, docs/M3_US101_VALIDATION.md §5).
- Controllers as pure functions: FollowerStopper, PI-with-saturation (faithful
  to Stern et al. Eqs. 3–5 after the spec correction, docs/PI_CONTROLLER_FIX.md),
  JAD with a swappable detection oracle (docs/JAD_ORACLE_RESULTS.md), VSL.
- Calibration: gap-based per-episode IDM fitting with holdout (Kesting &
  Treiber), triangular FD with bootstrap CIs, demand from boundary counts
  (docs/M2_RESULTS.md, docs/I24_DATA.md §5).
- Validation: FHWA-style criteria as data (GEH, RMSPE, emergent wave speed,
  ring benchmarks, ≥ 20 seeds), a wave detector with an absolute and a
  relative threshold (docs/CONTRACTS.md §4), paired common-random-number
  designs with t CIs.
- Reproducibility as method: seeds, config hashes, byte-stable goldens, the
  auto-generated report (docs/reports/).

## 3. Data: I-24 MOTION, and what it can support

- The instrument and the INCEPTION export; streaming ingestion; schema facts
  (docs/I24_DATA.md §1).
- Fragmentation (median 117 m / 9.9 s) and the coverage finding: ≈ 0.5–0.65 of
  vehicle-time tracked in the peak, from Edie density against the calibrated
  equilibrium spacing — speeds and wave speeds are sound, counts are lower
  bounds (docs/I24_DATA.md §2, §4). This is the paper's central methodological
  point and should be stated as a general caution for camera-derived
  trajectory datasets.
- Episodes and the population fit: 17,652 episodes, holdout 5.29 m, parameters
  vs NGSIM and the literature; `a_max` is high on both datasets (§5).
- Corridor geometry from OSM + landmark layers; the raw-way trap (§6).

## 4. Results

1. **Ring benchmark and the density dependence of wave speed.** Emergence and
   single-vehicle dampening in CI; wave speed rising from ≈ 12 km/h near
   critical density to ≈ 17 at 80–100 veh/km, for two independently
   calibrated fleets, between the FD's `w` and Newell's `(s0+L)/T`
   (docs/WAVE_SPEED_DIAGNOSIS.md and its follow-up).
2. **US-101 replica:** 1 PASS / 5 FAIL with causes; the boundary-condition
   result (docs/M3_US101_VALIDATION.md).
3. **I-24 replica:** two demand arms, 1 PASS / 5 FAIL each; coverage-corrected
   demand reproduces the stop-and-go pattern (RMSPE 36.8%) but inserts 82–84%
   of demand and its fronts run at 8.7 / 12.4 km/h against 14.2 / 16.4; the
   wave-speed prediction not confirmed on the corridor and the specific reason
   (docs/I24_VALIDATION.md).
4. **Controller results on the synthetic corridor:** dose-response at 1%
   penetration, FollowerStopper ≈ JAD-with-realistic-oracle, faithful PI trails
   (docs/M3_RESULTS.md, docs/CONTROLLER_COMPARISON.md).
5. **Detection latency helps JAD** — the deferred-commitment finding
   (docs/JAD_ORACLE_RESULTS.md); B4 would turn it into a designed rule.
6. **Corridor dependence of the cost:** US-101 (docs/US101_PENETRATION.md) and
   the I-24 battery (docs/I24_SWEEP.md, once run) — reported as results on a
   replica that is not validated, with that sentence in the abstract.
7. **Flux-cap comparison** for the macro tier (docs/M3_US101_VALIDATION.md §5).

## 5. Discussion

- What validated, what did not, and why the failures are informative (the
  self-correction record, docs/LESSONS.md).
- SUMO modelling choices that had to be made explicit (lane-change eagerness,
  keep-right; docs/CONTRACTS.md §2) and why behaviour parameters calibrated on
  independent observables are not tuning.
- The coverage problem as a limitation of camera-trajectory instruments for
  flow-based calibration; radar counts as the fix.
- Limitations: one day, one direction, 3.4 of 4 miles, passenger-only fleet,
  fragment-based ramp inputs.

## 6. Reproducibility statement

Repository, release tag, scripts per figure, data access conditions (I-24
MOTION registration; NGSIM public), config hashes and seeds per table.

## Figures (all exist or are generated by committed scripts)

1. `docs/figures/i24_wb_overview.png` — the day.
2. `docs/figures/i24_validation_fields.png` — observed vs two arms.
3. `docs/figures/i24_validation_waves.png` — front-speed histogram.
4. `docs/figures/m3_sigma_v_vs_penetration.png` — the dose-response.
5. `docs/figures/fd_scatter_triangle.png` — calibration.
6. Ring wave-speed vs density (to be drawn from
   `artifacts/wave_speed_sitelength*.json`).

## Required citations

Gloudemans et al. (2023) and Ji et al. (2024) for I-24 MOTION data and tools
(data-use agreement); Stern et al. (2018); Sugiyama et al. (2008); Treiber &
Kesting (2013); Kesting & Treiber (2008); Delle Monache & Goatin (2014);
FHWA-HOP-18-036; the CIRCLES MegaVanderTest.
