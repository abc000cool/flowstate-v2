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

**Results:** to be added.
