# Queue discharge and lane use: the diagnosis and the calibration (2026-10-06)

The owner's goal after phase 2: "get the simulation running well". Phase 2's
evidence (docs/MERGE_MODEL.md, last section) moved the open question from the
merge's gap acceptance to how traffic discharges after it. This file records
the diagnosis (fixtures and committed data only, no corridor run) and, once
made, the calibration under docs/FRISCO_PROTOCOL.md Amendment 1.

## 1. The diagnosis

**Observed capacity drop (committed data).** I-94 S790→S97, 8 of 9 days with
an active bottleneck (onsets mostly 06:15–06:30): pre-breakdown flow (15-min
maximum in the 30 min before onset) 4,058 veh/h (1,353 / lane), queue
discharge 4,453 veh/h (1,484 / lane; 06:30–07:30: 1,616 / lane); discharge ÷
pre-breakdown 1.10 [0.98, 1.23] (5-min definition 1.02 [0.93, 1.13]) — the
flows before breakdown are demand-limited, so no drop is resolvable. I-24's
pre-breakdown flow is not observable (breakdown during the instrument's
start-up); its peak sections discharge 6,626 / 6,639 veh/h over two hours
(1,657 / lane, coverage-corrected, possibly biased high). Published range:
Cassidy & Bertini (1999, TR-B 33(1)) report discharge up to about 10 % below
the pre-queue flow; later reviews give 3–18 % (cited from secondary summaries).

**Model fixtures** (20 seeds; the corridors' own fleets on
`artifacts/idm_i24_capacity.json`; on-ramp merge "OR", lane drop "LD",
single-lane jam outflow "J1", straight-road capacity "SAT"):

| fleet | OR discharge / lane | LD discharge / pre-breakdown | J1 | SAT |
|---|---|---|---|---|
| I-24 (IDM, 70 mph) | 1,460 ± 20 | 1,470 / 1,630 (0.90) | 1,778 | 1,778 |
| I-94 (EIDM, 55 mph) | 1,400 ± 25 | 1,472 / 1,772 (0.83) | 1,618 | 1,742 |

The I-24 fixture's 1,460–1,470 × 4 lanes = 5,840–5,880 reproduces the
replica's peak sections (5,838 / 5,798). **The capacity drop is not too large;
the merge discharge level is too low** (I-24: 1,460 against 1,657 / lane).

**Which setting moves it.** Mean `a_max` (the population's mean maximum
acceleration, never calibrated; 1.055 ± 0.43 m/s²): +1 sd (1.48) raises OR
discharge to 1,632 (I-24) and 1,632 (I-94) per lane, leaves straight-road
capacity of the IDM fleet unchanged (1,778) and free-flow speed unchanged
(equilibrium speed does not depend on `a_max`); −1 sd lowers it to 1,236.
Rejected: mean T (cannot reach the level inside its range without inflating
pre-breakdown capacity to 1,917–1,929 / lane), b and s0 (≤ 4 %), EIDM's
drive-off, jerk and error parameters, IDM against EIDM, the speed factor (each
≤ about 3 %).

**Projections (not runs).** I-24: mean `a_max` ≈ 1.30–1.34 (+0.57 to +0.65 sd)
reaches the GEH thresholds at the peak sections (6,225 / 6,238); +1 sd gives
about 6,480–6,640. I-94: `a_max` does not reach S790's needed 4,567 (about
3,750–3,800 at +1 sd): its shortfall is the T.H.52 weave, not discharge; and
the EIDM fleet's straight-road capacity would rise from 1,742 to 1,879 /
lane against 1,482 observed.

**Risks.** Fewer emergent waves: at +1 sd the mean driver is string-stable
below 41 veh/km (I-24) and 49 veh/km (I-94) against 29 and 35 today, and the
share of unstable drivers at 30 veh/km falls from 0.43 to 0.22 — the wave
criterion and CLAUDE.md §3.1's requirement that the defaults be unstable near
capacity must be checked on the full runs. The start-wave speed moves closer
to observed (I-24 −16.9 → −19.4 km/h). The episode-fit cost (gap RMSE) of the
shifted mean is not computed (the episodes are cloud-only data).

## 2. Lane use

`lc_keep_right` = 0 was set on I-24 in 2026-09 from observed lane shares
(SUMO's default 1.0 crowded the two right lanes at the Old Hickory merge:
24/25/25/27 % against observed 30/24/20/26 %; at 0: 32/26/22/20 %). Its
recorded justification — that US freeways have no keep-right rule — is
wrong: Tennessee (Code §55-8-115), Minnesota (Stat. §169.18 subd. 10(b)) and
Texas (Transp. Code §545.051(b)) require slower traffic to keep right. At 0
slow drivers hold the left lanes, which is what fails the T.H.52 section
test's ceiling (docs/MERGE_MODEL.md A3).

## 3. The calibration

Under docs/FRISCO_PROTOCOL.md Amendment 1 and its clarifications (grid,
targets, definitions and rule fixed before any run).

**Tools (2026-10-06, before any run).** `scripts/derive_population.py` wrote
the derived populations (`artifacts/idm_i24_capacity_amax_k{0.25,0.5,0.75,1.0}.json`:
mean `a_max` 1.1620 / 1.2691 / 1.3762 / 1.4833 m/s², = 1.0549 + k × 0.4284;
covariance and the other means unchanged; k = 0 is `idm_i24_capacity.json`,
which both corridors' fleets run). `scripts/calibrate_driver_grid.py` runs the
25-pair grid (I-24 `i24_replica_flow_speedcal`, one seed; I-94 the 35-minute
slice under the reference configuration, two seeds; pair (0, 0) is the
reference with the same hash) and applies the rule
(`validation.driver_calibration`); lane use and discharge are read by
`validation.lane_use`. I-24: vehicle-time shares on data x 0–5,500 m,
06:30–08:30 (observed 30.33 / 24.16 / 20.06 / 25.46 %), discharge at 2,200 /
3,200 m against 6,626 / 6,639 veh/h. I-94: detector crossing shares by lane
at the selected stations whose simulated cross-section has the station's lane
count (at least 25 m from a lane-count change), 07:05–07:35 on the five
calibration days, quality-masked (IRIS lane n = SUMO lane n − 1, both from the
right); discharge S97 against 4,490.5 veh/h.

**Results (grid run 2026-10-07, VM flowstate-p3, code 571b9e4; artifacts
`artifacts/driver_calibration_i24.json`, `artifacts/driver_calibration_i94.json`,
per-run readings and manifests in `artifacts/p3_driver_grid_2026-10-07/`).**
75 runs (I-24 25 × 1 seed, I-94 25 × 2 seeds), 0 failed, 0 collisions.

| corridor | pair (k, keep-right) | lane RMSE pp | discharge veh/h | target | error | departed |
|---|---|---|---|---|---|---|
| I-24 | current (0, 0) | 2.31 | 5,837 / 5,810 | 6,626 / 6,639 | 12.2 % | 0.984 |
| I-24 | (0.5, 0) | 2.80 | 6,027 / 5,984 | | 9.5 % | 0.996 |
| I-24 | **chosen (1.0, 0)** | 2.33 | 6,021 / 5,995 | | **9.4 %** | 0.995 |
| I-94 | current (0, 0) | 8.24 | 3,321 (S97) | 4,490.5 | 26.0 % | 0.971 |
| I-94 | (1.0, 0) | 7.64 | 3,680 | | 18.0 % | 0.977 |
| I-94 | **chosen (1.0, 0.1)** | 8.40 | 3,764 | | **16.2 %** | 0.977 |

The rule chose k = 1 (mean `a_max` 1.4833 m/s², the top of the measured
range) on both corridors, `lc_keep_right` 0 on I-24 (5 pairs inside the 1 pp
lane band) and 0.1 on I-94 (16 pairs inside it). Applied by
`scripts/apply_driver_calibration.py` to `scenarios/i24_replica_flow_speedcal_dc.yaml`
(hash 8976a1773674) and `scenarios/mndot_i94_wb_stpaul_weave_dc.yaml`
(hash db9fbab5fc6e).

What the grid says, beyond the rule's pick:

- **I-24: `a_max` closes about a quarter of the gap, then plateaus.** The
  discharge error falls from 12.2 % to about 9.4 % by k = 0.5 and does not
  move from k = 0.5 to 1.0 (5,984–6,030 veh/h at every keep-right). The
  departed share rises from 0.984 to 0.995–0.996 at k ≥ 0.25: with the
  stronger drivers almost every planned vehicle enters, so the 2-h section
  flow is then bounded by the demand the scenario supplies, and that demand
  (scale s = 0.800, docs/I24_VALIDATION.md) was fitted under the old drivers.
  The plateau is therefore read as demand-limited, not as a second discharge
  limit, until a demand refit under the calibrated drivers says otherwise
  (step 3 runs it: pipeline stage `p4_i24_refit`). The fixture projection of
  6,480–6,640 veh/h at +1 sd was not reached.
- **I-94: the gain is real but the gap stays large.** S97 rises from 3,321 to
  3,680–3,764 veh/h (target 4,490.5); the remaining shortfall is the T.H.52
  weave (§1), which `a_max` was not expected to fix. The seed-to-seed spread at
  one pair (up to 264 veh/h) is larger than the difference between keep-right
  0 and 0.1 at k = 1 (84 veh/h): the rule's choice of 0.1 over 0 is inside the
  noise of two seeds, as the rule allows (it fixes no noise band).
- **Lane use barely responds.** On I-24 every pair lies within 1.4–4.5 pp of
  the observed shares with no trend in keep-right (one seed per pair); on
  I-94 every pair is 7.6–9.9 pp off the detector shares, and raising
  keep-right makes it worse. The I-94 lane-share gap is not a keep-right
  problem.
- **No collisions** in any of the 75 runs.

**T.H.52 fixture checks with the calibrated I-94 drivers (2026-10-07, local,
macOS; `scripts/merge_model_selfcheck.py ... --fleet-from
scenarios/mndot_i94_wb_stpaul_weave_dc.yaml`; JSON in
`artifacts/p3_driver_grid_2026-10-07/th52_*.json`).** Fixture tables are macOS
records (docs/LESSONS.md); the corridor rounds on Linux decide.

| check | drivers | seeds | exit-end flow veh/h (mean ± sd) | GEH < 5 | min station speed ≥ 20 m/s | collisions |
|---|---|---|---|---|---|---|
| ceiling (nothing to cross, A2.3) | calibrated (k 1, keep-right 0.1) | 3–12 | 4,826 ± 22 | 10 / 10 | 10 / 10 | 0 |
| ceiling (A3 record) | current (k 0, keep-right 0) | 3–12 | 4,770–4,863 | 10 / 10 | 7 / 10 | 0 |
| locked section test, `weave` | calibrated | 3–22 | 4,361 ± 83 | 1 / 20 | 0 / 20 | 0 |
| locked section test, `weave` | current | 3–22 | 3,873 ± 106 | 0 / 20 | 0 / 20 | 0 |

The ceiling now passes at every seed, including seed 3, the locked test's: the
slow drivers that held the left lanes at keep-right 0 (A3) no longer pull the
station speed under 20 m/s, so the locked test is passable in principle again.
With crossing, the calibrated drivers carry about 490 veh/h (13 %) more through
the weave than the current ones (observed inflow 4,877), but the test still
fails at 20 of 20 seeds: the weave itself, not discharge or lane holding, is
what is left.

Step 3 (the 20-seed batteries on the calibrated scenarios) decides whether the
change improves the criteria; until then nothing here is a validation claim.

## 4. Step 3: the full tests (2026-10-07)

One n2-standard-32 (us-west1-b, after us-west1-c refused for capacity), stage 23
at code 6de4e21, about 1 h 45 min, about $2.80, self-deleted. Every arm is 20
seeds; every run recorded zero collisions; the ring benchmark passed at 20 of
20 seeds in every I-24 battery. Artifacts: `artifacts/baseline_gate_mndot_dc.json`,
`artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc{,_gated}.json`,
`docs/reports/mndot_i94_wb_stpaul_weave_xlsfg_dc/`, `artifacts/i24_validation_dc.json`,
`artifacts/i24_validation_dc_refit.json`, `artifacts/i24_validation_flow_speedcal_ref.json`,
`artifacts/demand_scale_i24_flow_dc.json`, `scenarios/i24_replica_flow_corrected_dc.yaml`,
`scenarios/i24_replica_flow_speedcal_dc_refit.yaml`.

**I-94 (the protocol's baseline gate, four-hour weave scenario under the
reference configuration; calibrated drivers k = 1, keep-right 0.1, against the
phase-1 reference on the same day sets and observations,
`artifacts/baseline_gate_mndot_i94_wb_stpaul_p1.json`).**

| check | day set | reference (p1) | calibrated drivers | target |
|---|---|---|---|---|
| C1 GEH < 5 share | calibration | 15.5 % | **61.8 %** (95 % interval 56.5–67.0) | ≥ 85 % |
| C1 GEH < 5 share | validation | 14.5 % | **60.0 %** (54.0–66.0) | ≥ 85 % |
| C2 GEH < 3 share (not gating) | calibration / validation | 12.3 / 11.4 % | 45.8 / 42.5 % | 100 % |
| C3 speed RMSPE, 15 min | calibration | 49.7 % | **33.9 %** (31.3–36.5) | ≤ 15 % |
| C3 speed RMSPE, 15 min | validation | 48.7 % | **38.8 %** (34.2–43.4) | ≤ 15 % |
| C4 backward wave speed | calibration | 6.6 km/h | 4.9 km/h (front in 19 of 20) | 14–22 km/h |
| C6 bottlenecks | calibration | fail (phantom) | fail (phantom) | — |
| C6 bottlenecks | validation | pass | pass | — |
| C5 collisions | all | 0 | 0 | 0 |

The gate still **fails**, but the calibrated drivers are the largest single
improvement the I-94 model has had: four times the share of station-hours
inside GEH 5 and a third less speed error, on the validation days as much as on
the calibration days. The wave speed moved the wrong way (it was failing
already). About 4 % of planned vehicles never departed (two seeds 10–11 %), and
2.1 % of exiters at on-ramp 745524613 gave up (above the 2 % threshold) — the
T.H.52 weave, whose loss docs/WEAVE_LOSS_DIAGNOSIS.md decomposes.

**I-24 (one recorded morning, no holdout; 20 seeds each, same code).**

| arm | demand scale | GEH < 5 vs recommended | segment-speed RMSPE | wave criterion (stack) | waves per replicate | stripe / standard detector |
|---|---|---|---|---|---|---|
| reference: old drivers (`flow_speedcal_ref`, reproduces the committed arm exactly) | 0.800 | 21.5 % | 37.2 % | **15.9 km/h, pass** | 9.05 | 14.5 / 9.9 km/h |
| calibrated drivers, old demand (`dc`) | 0.800 | 25.0 % | 78.4 % | no qualifying peak | 13.65 | 13.2 / 11.4 km/h |
| calibrated drivers, demand refit (`dc_refit`) | 0.925 | **25.7 %** | **33.3 %** | **no qualifying peak** | 6.65 | 14.3 / 9.3 km/h |

The demand refit (the fitter that set 0.800, on the calibrated drivers; train
RMSPE 0.336, held-out hour 0.312) raised the demand scale to 0.925: the
"plateau" of §3 was indeed demand-limited, and with the demand the calibrated
drivers can carry, flows and speeds both fit a little better than the
reference. But the **wave criterion is lost**: the criterion's slant-stack
detector finds no peak clearing its contrast threshold, the replicates carry
fewer backward waves (6.65 against 9.05), and only the secondary detectors
still read speeds near the band. This is the risk recorded in §1 before any
run — a higher mean `a_max` makes the drivers more string-stable — and
CLAUDE.md §3.1 makes instability near capacity a requirement, not an option.

**What this means.**

- **I-94:** the calibrated drivers should replace the reference drivers for
  further I-94 work (every gating check except the already-failing wave speed
  improved, on validation days as well).
- **I-24:** the calibrated package (k = 1 with the demand refit) is *not*
  adopted as the canonical I-24 arm: a better fit of flows and speeds bought
  with the loss of the emergent-wave criterion is not an improvement of this
  model. `i24_replica_flow_speedcal` stays canonical; `_dc_refit` is kept as a
  documented alternative.
- The one-recipe-for-all-corridors reading of Amendment 1 does not hold: the
  same driver shift helps the weaving corridor and hurts the wave corridor.
  Whether a smaller shift (k = 0.25 or 0.5, with its own demand refit) keeps
  I-24's waves while keeping part of the fit gain is a cheap question; it is
  posed as protocol Amendment 2 (proposed, not adopted).

Nothing here is a pass: neither corridor passes its acceptance criteria.
