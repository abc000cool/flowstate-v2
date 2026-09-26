# Operational strategies on the I-24 replica: VSL and ALINEA beside FollowerStopper

**Status (2026-09-23):** the first strategy sweep on a validated corridor. The
run is described here; the numbers are filled from
`artifacts/sweep_i24_strategies_summary.json` when the round lands (nothing
below is quoted from anything else).

*Correction 2026-09-25:* the corridor is not validated, and the scenario is
not the canonical arm. `i24_replica_flow_speedcal_ramps` (config
`0cddf2002979`, the artifact's `base_config_hash`) is the flow-share
family's fitted level with fitted ramps. docs/I24_VALIDATION.md §0.10 keeps
that family out of the published record. The arm passes 5 of 7 criteria
rows and fails link-flow GEH (21.5% of link-hours under 5) and segment-speed
RMSPE (41.8%); `artifacts/i24_validation_flow_ramps.json`. Every number
below describes an unvalidated replica.

## Setup

- Scenario: `scenarios/i24_replica_flow_speedcal_ramps.yaml` — the canonical
  I-24 westbound arm with the Old Hickory and Hickory Hollow ramps
  (docs/I24_VALIDATION.md). *(Corrected 2026-09-25: the flow-share family's
  fitted-plus-ramps arm, not the canonical arm; see the note above.)* Nothing
  in the scenario was changed; every cell is a patch of it recorded by config
  hash in the summary.
- Grid (`scripts/corridor_sweep.py`, 20 seeds per cell, the same seed list in
  every cell so deltas are paired): `baseline`; `strategy_vsl` (VSL threshold
  ladder on every 1 km gantry segment, no AVs); `strategy_alinea` (ALINEA on
  every on-ramp, no AVs); `follower_stopper_p0.10_c1.00_{none,vsl,alinea}`
  (10 % FollowerStopper at full compliance under each).
- ALINEA target: 29.2 veh/km/lane, the critical density implied by the
  capacity-scaled population's equilibrium capacity (1,985.5 veh/h/lane at
  18.912 m/s, `artifacts/idm_i24_capacity_equilibrium.json`; density = flow ÷
  speed). Gain and rate bounds are the controller defaults
  (`controllers.ramp_meter.ALINEA_DEFAULTS`).
- Metrics on the measured span (data x 2,256–7,638 m of the I-24 MOTION
  recording; throughput at data x = 2,200 m), warm-up discarded per the
  scenario, 95 % t-intervals over seeds and paired deltas versus baseline.

## Results

**ALINEA did not run.** Every ALINEA cell failed in every seed at the same
vehicle on the Hickory Hollow Pkwy entrance: the meter's stop line (30 m before
the end of the ramp's last edge, `19441652#1`) is refused by SUMO for a
vehicle that cannot brake in time (`TraCIException: stop … is too close to
brake`). The defect is recorded in the changelog and lesson 31.

**FollowerStopper under VSL is incomplete:** the VM's watcher deadline (75
minutes) cut the stage after 6 of its 20 seeds; the artifact lists it under
`incomplete_cells` and the tables below omit it. The three complete cells
(baseline, VSL alone, FollowerStopper 10 % alone) carry 20 seeds each.

Per-cell means with 95 % t-intervals over the seeds (`aggregate`), then the
paired per-seed deltas versus baseline with their 95 % intervals and the share
of the baseline mean (`vs_baseline_paired`; "resolved" = the interval excludes zero).

| cell | n | throughput [veh/h] | mean travel time [s] | σ_v temporal [m/s] | fuel [ml/veh-km] | waves |
|---|---|---|---|---|---|---|
| baseline | 20 | 5,671 [5,662, 5,681] | 575.4 [565.5, 585.2] | 4.76 [4.71, 4.80] | 89.6 [88.6, 90.6] | 10.2 [9.0, 11.5] |
| VSL alone | 20 | 5,252 [5,233, 5,271] | 620.7 [614.0, 627.5] | 3.37 [3.35, 3.39] | 104.2 [103.6, 104.8] | 17.2 [15.8, 18.7] |
| FollowerStopper 10 % | 20 | 2,859 [2,399, 3,319] | 1,292.7 [1,091.7, 1,493.6] | 1.93 [1.85, 2.00] | 275.9 [229.0, 322.8] | 9.0 [7.0, 11.0] |
| FollowerStopper 10 % + VSL | 6 of 20 | cut by the VM deadline — not reported (see above) | — | — | — | — |
| ALINEA alone | 0 | failed (see above) | failed (see above) | failed (see above) | failed (see above) | failed (see above) |
| FollowerStopper 10 % + ALINEA | 0 | failed (see above) | failed (see above) | failed (see above) | failed (see above) | failed (see above) |

Paired deltas versus baseline:

| cell | Δ throughput [veh/h] | Δ mean travel time [s] | Δ σ_v temporal [m/s] | Δ fuel [ml/veh-km] | Δ waves |
|---|---|---|---|---|---|
| VSL alone | -420 [-441, -398] (-7.4 %, resolved) | +45.4 [+33.5, +57.2] (+7.9 %, resolved) | -1.39 [-1.44, -1.34] (-29.1 %, resolved) | +14.6 [+13.4, +15.8] (+16.3 %, resolved) | +7.0 [+5.1, +8.9] (+68.3 %, resolved) |
| FollowerStopper 10 % | -2,812 [-3,271, -2,354] (-49.6 %, resolved) | +717.3 [+516.1, +918.5] (+124.7 %, resolved) | -2.83 [-2.92, -2.74] (-59.5 %, resolved) | +186.3 [+139.4, +233.3] (+208.0 %, resolved) | -1.2 [-3.5, +1.0] (-12.2 %) |

Incomplete cells in the artifact: ['follower_stopper_p0.10_c1.00_alinea', 'follower_stopper_p0.10_c1.00_vsl', 'strategy_alinea']; seeds per cell: 20; base config hash `0cddf2002979`; ALINEA target 29.2 veh/km/lane; metrics args {'x_ref': 4411.8, 'span': [2256.2, 7637.8]}. *(The artifact as written on 2026-09-23. The 2026-09-24 round below overwrote it: its `incomplete_cells` are now the two combined cells only, and the complete cells above reproduce to the digit — same seeds.)*

## Reading

The VSL threshold ladder at its defaults is a net loss on this corridor: it
lowers the temporal speed standard deviation (-29.1 %) and the
wave amplitude (-24.5 %), but throughput falls -7.4 %, mean
travel time rises +7.9 % (p90 +22.8 %), fuel per vehicle-km rises
+16.3 %, and the corridor carries more waves (+68.3 %), slower
ones (-35.7 %). Every delta is paired over the 20 seeds and resolved.
This is the same trade the FollowerStopper sweep found (docs/I24_SWEEP.md):
smoothing is paid for in capacity, and a posted-speed ladder pays more of it
than gap-keeping vehicles do. It is one ladder at default thresholds on one
corridor; a tuned ladder is a different experiment, not a correction of this
one.

**FollowerStopper 10 % alone** (20 seeds, paired) on this ramps arm: σ_v -59.5 %, throughput -49.6 %, mean travel time +124.7 %, fuel +208.0 % — the same direction and a larger size than the published dose-response on the arm without ramps (docs/I24_SWEEP.md), which is the corridor's known behaviour: gap-keeping vehicles smooth the field by starving it. Beside it, the VSL ladder buys a smaller σ_v reduction for a smaller capacity cost; neither is a free improvement here.

**Cells not measured:** FollowerStopper under VSL (6 of 20 seeds before the
cut-off; not reported) and both ALINEA cells (the stop-line defect).


## 2026-09-24 — ALINEA ran: 20 seeds paired, after the stop-placement fix

The ramp-meter stop placement was fixed on 2026-09-24 (CHANGELOG; a vehicle
that cannot brake for the line passes that cycle and is counted), and the
six-cell grid was rerun on a self-deleting VM (`flowstate-strat-a`,
n2-standard-32, us-west1-c; stage `sweep_i24_strat`). The instance was
deleted by its own service account at 05:40:59 UTC with the sweep at 112 of
120 runs (the pipeline logged a SIGTERM and archived on exit; the guest logs
did not survive, so the cause is not proven — the idle guard is the only
other actor with that identity, and its 75-minute grace was minutes away).
The 112 per-run metrics were fetched and the summary rebuilt with
`--analyze-only --allow-partial` (`artifacts/sweep_i24_strategies_summary.json`,
`incomplete_cells` lists the two cut cells).

**ALINEA alone versus baseline, 20 seeds paired** (per-seed deltas, 95 % t-intervals; `resolved` = the interval excludes zero):

| metric | baseline mean | ALINEA mean | Δ vs baseline | paired Δ [95 % CI] | resolved |
|---|---|---|---|---|---|
| throughput [veh/h] | 5671.35 | 5344.35 | -5.8 % | -327.00 [-337.4, -316.6] | yes |
| mean travel time [s] | 575.36 | 383.22 | -33.4 % | -192.14 [-202.8, -181.5] | yes |
| σ_v spatial [m/s] | 5.58 | 4.92 | -11.7 % | -0.65 [-0.7, -0.6] | yes |
| fuel [ml/veh-km] | 89.60 | 75.59 | -15.6 % | -14.01 [-15.1, -12.9] | yes |
| wave count | 10.25 | 43.75 | +326.8 % | +33.50 [+30.5, +36.5] | yes |
| wave amplitude [m/s] | 7.00 | 8.88 | +26.8 % | +1.88 [+1.6, +2.2] | yes |
| wave speed [km/h] | 10.37 | 12.42 | +19.8 % | +2.05 [+1.3, +2.8] | yes |

Reading: metering the two entrances (Old Hickory Blvd, Hickory Hollow Pkwy) at the corridor's critical density
(29.2 veh/km/lane) lowers the mainline travel time by a third and fuel per
vehicle-kilometre by 16 % at the price of 5.8 % of the throughput measured at
the reference section (the held ramp vehicles are not on the mainline), and
it multiplies the number of detected waves while making them larger: the
stop-and-go pattern moves from a few long waves to many short ones. Two
things are outside these numbers and must be read with them: the ramp queue
wait is not part of `mean_tt_s` (the travel-time span is the mainline
2,256–7,638 m, and a metered vehicle's wait at the stop line lies upstream of
it), and the meter counters are now in the summary's `diagnostics` block
(rerun of 2026-09-24, block 3, with `meta.json` archived): over 20 seeds the
Hickory Hollow Pkwy meter released 645 vehicles per run and let 0.85 pass
unstoppable (share 0.13 %, 95 % interval 0.06–0.20 %), the Old Hickory Blvd
meter released 674 and let none pass — the stop-placement fix holds on this
arm.

The two combined cells are **not reported**: FollowerStopper 10 % under ALINEA
reached 15 seeds and under VSL 17 seeds before the instance was deleted (counts
from the fetched run directories — a session record); the artifact names them
under `incomplete_cells` and carries no aggregate for them, so nothing from
those cells is quoted.


**Rerun 2026-09-24 (block 3).** The six-cell grid was resumed on a third VM
(`flowstate-r3-c`); it too was cut, at 117 of 120 runs, and this time the
guest journal came home with the archive: the kernel OOM killer took a
FollowerStopper-under-strategy run (anon-rss 8.8 GB) with the 32-process
pool holding all 125 GB — the same wave that ended the first VM at 112 of
120. The pipeline now caps that stage's pool at 12 processes. The four
complete cells are unchanged to the digit; the two FollowerStopper-under-
strategy cells remain incomplete (19 and 18 seeds) and unreported.


## 2026-09-24 (block 3) — the six-cell grid is complete

The last three runs were resumed on a fourth VM (`flowstate-r3-d`, pool 12,
clean exit); every cell now has 20 seeds and every paired delta below is
resolved unless marked n.s. (`artifacts/sweep_i24_strategies_summary.json`,
`incomplete_cells` empty; per-cell `diagnostics` carry the meter counters).

| cell (20 seeds paired vs baseline) | throughput [veh/h] | mean travel time [s] | σ_v spatial [m/s] | fuel [ml/veh-km] | wave count | wave amplitude [m/s] |
|---|---|---|---|---|---|---|
| baseline means | 5671.35 | 575.36 | 5.58 | 89.60 | 10.25 | 7.00 |
| VSL alone | -7.4 % | +7.9 % | -19.2 % | +16.3 % | +68.3 % | -24.5 % |
| ALINEA alone | -5.8 % | -33.4 % | -11.7 % | -15.6 % | +326.8 % | +26.8 % |
| FollowerStopper 10 % alone | -49.6 % | +124.7 % | -67.7 % | +208.0 % | -12.2 % (n.s.) | -24.2 % |
| FollowerStopper 10 % + ALINEA | -28.9 % | +26.7 % | -54.8 % | +61.7 % | +378.0 % | -28.4 % |
| FollowerStopper 10 % + VSL | -53.3 % | +154.4 % | -74.1 % | +238.3 % | -46.3 % | -31.2 % |

Reading, with the caution that this is one calibrated arm and one
penetration: ALINEA alone is the only cell that improves travel time and
fuel; the FollowerStopper at 10 % costs half the throughput alone, and
**metering the entrances upstream of it recovers much of that** — under
ALINEA the FollowerStopper's throughput loss falls from -49.6 % to
-28.9 %, its travel-time cost from +124.7 % to +26.7 %, its fuel cost
from +208.0 % to +61.7 %, while σ_v stays down (-54.8 %) — the meter
keeps the mainline below the density at which the smoothing controller
starves it. Under VSL the FollowerStopper is worse than alone on every
capacity metric. The meters under FollowerStopper released 444 (Hickory
Hollow) and 571 (Old Hickory) vehicles per run with 0.2 % / 0 % unstoppable
passes. Waves: ALINEA multiplies their count (more, shorter waves) in every
cell it is in; the FollowerStopper reduces their amplitude in every cell.

*Note 2026-09-25 — two σ_v conventions.* The 2026-09-23 tables use the
temporal speed spread (per vehicle over time, averaged over vehicles). The
ALINEA table and this grid use the spatial one (across vehicles at each
instant, averaged over time); `validation.metrics.Metrics` defines both. The
artifact carries both for every cell (`vs_baseline_paired`, share of the
baseline mean):

| cell | σ_v temporal | σ_v spatial |
|---|---|---|
| VSL alone | -29.1 % | -19.2 % |
| ALINEA alone | -27.2 % | -11.7 % |
| FollowerStopper 10 % alone | -59.5 % | -67.7 % |
| FollowerStopper 10 % + ALINEA | -52.9 % | -54.8 % |
| FollowerStopper 10 % + VSL | -68.5 % | -74.1 % |

Baseline means: 4.76 m/s temporal, 5.58 m/s spatial.


## 2026-09-26 (WP-95) — the FollowerStopper cells' collisions: a commanded AV cannot brake harder than its b

**Finding.** The strategy sweep's local runs record 311 SUMO collisions in 51 of the 120 runs. All of them are in the three FollowerStopper cells; the baseline, VSL-alone and ALINEA-alone cells have none. In 305 of the 311 the rear vehicle is a compliant AV. The cause is the way the runner drives an AV, not the controller's law. A `setSpeed` command held under SUMO's default speed mode caps the AV's deceleration at its comfortable `b` (for SUMO's IDM, `max(b, 1.5)` m/s²). The AV's own car-following model, and every human around it, may brake at up to 9 m/s². When the vehicle ahead brakes harder than the AV's cap for long enough, the AV hits it. The FollowerStopper rows of this document's tables are therefore measured in a simulation in which the controlled vehicles recorded 2.8–7.5 collisions a run. A new option, `AVSpec.emergency_handback` (off by default, hash-neutral), removes every collision on a fixture with the controller's measured effect unchanged. The re-runs that measure it on the I-24 replica, and on the other controller results, are written as pipeline stages and have not run.

### What the strategy sweep's runs recorded

Per cell, 20 seeds each (`artifacts/collisions_i24_strat_sweep.json`, written by the new `scripts/collision_census.py` from the 120 local `meta.json`).

| cell | runs with a collision | collisions (`n_collisions`) | per run, mean [95 % CI] | distinct pairs | rear vehicle a compliant AV | front vehicle of those: human / AV | per 1,000 departed | per 1,000 departed AVs |
|---|---|---|---|---|---|---|---|---|
| baseline | 0 | 0 | 0 | 0 | — | — | 0 | — |
| VSL alone | 0 | 0 | 0 | 0 | — | — | 0 | — |
| ALINEA alone | 0 | 0 | 0 | 0 | — | — | 0 | — |
| FollowerStopper 10 % | 20 | 150 | 7.5 [4.99, 10.01] | 119 | 149 | 136 / 13 | 0.81 | 8.06 |
| FollowerStopper 10 % + ALINEA | 19 | 105 | 5.25 [3.89, 6.61] | 94 | 104 | 86 / 18 | 0.50 | 4.97 |
| FollowerStopper 10 % + VSL | 12 | 56 | 2.8 [0.87, 4.73] | 46 | 52 | 45 / 7 | 0.32 | 3.15 |
| **total** | **51** | **311** | | **259** | **305** | **267 / 38** | | |

- *Counter and pairs.* The runner adds every collision SUMO still holds whenever a new one is detected in a step (docs/CONTRACTS.md §2, `meta.json.n_collisions`). So 52 of the 311 are a pair still in contact, counted again in a step in which another pair was new. All 52 coincide with such a step. Net of them there are 259 distinct pairs, 253 with a compliant AV behind.
- *The six with a human behind.* Each follows an AV-caused collision on the same lane 0–71 s earlier (three within 0.5 s).
- *What SUMO calls a collision.* The rear vehicle's front within 0.1 × its own `minGap` of the leader's back: SUMO's IDM sets `collisionMinGapFactor` 0.1 (`MSCFModel_IDM.cpp` 47), and the runner leaves `--collision.mingap-factor` at −1 (`MSLane.cpp` 1991–1999, `MSFrame.cpp` 420). That is contact, not a near miss.
- *No rate per AV-km.* `meta.json` has no per-vehicle distance, and this machine reads no trajectory (the owner's rule). The per-departed rates cover the whole run, warm-up included, as WP-94's do. The first collision of any run is at t = 251.5 s.

Where, by edge (all collisions logged; no run exceeded the 50-event log):

| edge | what joins or leaves there | FollowerStopper | + ALINEA | + VSL | total |
|---|---|---|---|---|---|
| 992666043 | Hickory Hollow Pkwy on-ramp joins; Bell Road exit leaves | 53 | 25 | 53 | 131 (lane 0: 86) |
| 977008892 | Hickory Hollow Pkwy exit leaves | 26 | 64 | 0 | 90 (lanes 1–3: 86) |
| 977008894 | Old Hickory Blvd on-ramp joins | 69 | 10 | 3 | 82 (lane 0: 48, lane 1: 34) |
| 977008893, 977008891 | — | 2 | 6 | 0 | 8 |

Every collision is on an edge where a ramp joins or leaves, or next to one. Metering the entrances moves them from the Old Hickory merge to the Hickory Hollow diverge.

### Mechanism, from SUMO 1.27.1's source (tag `v1_27_1`)

1. *The command.* Every action step the runner sends each compliant AV on a corridor edge `vehicle.setSpeed(vid, max(v_cmd, 0))` under the default speed mode 31 (`microsim/runner.py`, line 7435 at HEAD; CLAUDE.md §3.3).
2. *It is held.* `libsumo::Vehicle::setSpeed` (`src/libsumo/Vehicle.cpp` 1924–1938) installs the speed timeline `[(now, v), (SUMOTime_MAX − DELTA_T, v)]`. It stays in force until `setSpeed(-1)`: between action steps, and after the controller stops updating it (an AV that leaves the corridor for an off-ramp keeps its last command).
3. *The model's own step.* In `MSVehicle::executeMove` the model's next speed is `cfModel.finalizeSpeed(vSafe)` (`MSVehicle.cpp` 4693). There it may brake to `emergencyDecel`: `vMin = min(minNextSpeed(v), max(vSafe, minNextSpeedEmergency(v)))` (`MSCFModel.cpp` 204–207).
4. *The command overrides it.* Next, `vNext = processTraCISpeedControl(vSafe, vNext)` (`MSVehicle.cpp` 4730). That passes `vMin = cfModel.minNextSpeed(v)` (4014–4043) to `Influencer::influenceSpeed` (493–520), which replaces the model's speed with the command and then clamps it: `min(·, vSafe)` (bit 0, safe speed), `min(·, vMax)` (bit 1), `max(·, vMin)` (bit 2, maximum deceleration), in that order. The last clamp wins. Whenever the safe speed is below `v − b′·Δt`, the AV gets `v − b′·Δt`.
5. *The bound b′.* `MSCFModel_IDM::minNextSpeed` brakes at `max(decel, min(emergencyDecel, 1.5))` (`MSCFModel_IDM.cpp` 80–89); every other model, EIDM included, at `decel` (`MSCFModel.cpp` 330–338). The runner writes the driver's drawn `b` as `decel` and no `emergencyDecel` (`microsim/vehicles.py` 835–841), so the latter is the passenger default, `max(decel, 9)` (`SUMOVTypeParameter.cpp` 979–1020). The I-24 capacity population's `b` has mean 1.70 and standard deviation 0.89 m/s² (`artifacts/idm_i24_capacity.json`). Of the 2,000 compliant AVs of the fixture below, which draws from it, 35 % are capped at 1.5 m/s² and 83 % at 2.6 or less (the largest cap is 4.35). The same driver as a human may use 9.
6. *Cooperation is lost too.* The influencer also discards the model's lane-change speed adjustments (`patchSpeed`, `MSCFModel.cpp` 239), among them the slow-down that opens a gap for a vehicle asking to change in ahead. In the harness below the uncommanded vehicle brakes at 1.67 m/s² in the step after the request; the commanded one does not.
7. *The action step.* The runner writes `actionStepLength` = `sim.action_step_s` on every vType and dispatches every `round(action_step_s / step_length_s)` steps. In this sweep both are 0.5 s, so the command is re-sent every step. With a longer action step the model's `vSafe` on the steps between is `v + a·Δt` (`MSVehicle.cpp` 4656). The influencer's clamp still applies in every step.

SUMO's documentation (`docs/web/docs/TraCI/Change_Vehicle_State.md` 32, 347–372) names both checks ("may only drive slower than the speed that is deemed safe … and it may not exceed the bounds on acceleration and deceleration") but not which one wins. The source says the deceleration bound does. So "safety checks left ON" (§3.3) means that a controller cannot make a vehicle faster than safe. It does not mean that the vehicle can brake as hard as safety needs.

### The mechanism, reproduced by hand

A two-lane road. The AV is held at 20 m/s by `setSpeed` (IDM: `accel` 0.73, `decel` 1.67, `tau` 1.4, `minGap` 2). At t = 5 s a vehicle at 12 m/s, 8 m ahead in the next lane, is sent into its lane under `laneChangeMode` 256. From then on, the AV:

| from t = 5 s | AV's strongest deceleration [m/s²] | collision |
|---|---|---|
| keeps its `setSpeed` (the runner's path) | 1.67 (= `decel`) | yes, at t = 7.0 s, 1.5 s after the change |
| released to its model (`setSpeed(-1)`), as a human | 9.00 (`emergencyDecel`) | no |
| `setSpeed` under speed mode 27 (bit 2 off) | 39.7 | no |
| released, with `setMaxSpeed(20)` as a ceiling | 9.00 | no |
| `setSpeed` with the handback (the option below) | 9.00 (command withdrawn 2 steps) | no |

The first row brakes at exactly `decel` from the moment the other vehicle is in its lane. `tests/test_microsim/test_microsim_emergency_handback.py` pins rows 1, 2 and 5.

### On a small fixture

`tests/fixtures/merge.osm` (the golden merge cases' interchange: two lanes, an off-ramp leaving edge 100, an on-ramp joining an added lane on 102 that ends at 103). Mainline 0.8 veh/s, on-ramp 0.2 veh/s, 15 % exiting, a downstream speed schedule on 103 (25 → 7 → 20 → 5 → 22 m/s at 0 / 200 / 450 / 600 / 800 s), 1,000 s with 120 s of warm-up, step and action step 0.5 s. The fleet is the sweep's (IDM, `artifacts/idm_i24_capacity.json`, `lc_strategic` 5, `lc_strategic_ramp` 1, `lc_keep_right` 0). FollowerStopper at 10 %, 100 % compliance. Seeds 1–20 in every arm, one run at a time, 2–5 s each.

Collisions and braking, summed over the 20 seeds ("b′" is each AV's bound from step 5; "samples" are 2 Hz trajectory rows of compliant AVs on the corridor):

| arm | runs with a collision | collisions | distinct pairs | rear vehicle a compliant AV | AV samples braking beyond b′ | at or beyond 4.5 m/s² | at or beyond 9 m/s² | beyond 9 m/s² | strongest AV deceleration [m/s²] |
|---|---|---|---|---|---|---|---|---|---|
| baseline (no AVs) | 0 | 0 | 0 | — | — | — | — | — | — |
| `setSpeed` (the default) | 12 | 36 | 29 | 25 | 8 | 0 | 0 | 0 | 3.95 |
| handback (the option) | 0 | 0 | 0 | 0 | 574 | 46 | 30 | 0 | 9.00 |
| ceiling (`setMaxSpeed`) | 0 | 0 | 0 | 0 | 2,388 | 129 | 42 | 0 | 9.00 |
| speed mode 27 | 0 | 0 | 0 | 0 | 617 | 45 | 32 | 32 | 39.5 |

- The default arm's 8 samples beyond b′ are all an AV's first sample on the corridor, before its first command (on-ramp AVs entering at x ≈ 985–990 m). Under a command no AV braked beyond its bound; its strongest, 3.95 m/s², is an AV whose own bound is that high.
- The humans brake at 9 m/s² in every arm (1,170 samples in the baseline; 504 in the default arm).
- The handback withdrew a command in 3–70 AV-steps a run (614 in all).

The 25 AV-caused pairs of the default arm, from the fixture's trajectories:

| | count |
|---|---|
| the AV braked no harder than its bound in the 10 s before | 25 of 25 |
| the vehicle it hit braked harder than that bound in those 10 s | 24 of 25 (at 9 m/s²: 1) |
| the vehicle hit had been the AV's leader for at least 5 s | 23 of 25 (median 22.5 s; quartiles 16.5–24.5 s) |
| a vehicle entering the lane ahead within 5 s | 2 (both at the start of 103, where the added lane of 102 ends) |
| lane 1 of edge 102 (beside the on-ramp's added lane) / lane 1 of edge 100 (beside the exit lane) | 10 / 9 |

On this fixture the usual sequence is not a cut-in. A leader in a lane beside a ramp's lane brakes at 1.6–3.7 m/s² (median 2.5; twice at 8–9) for a few seconds. The AV behind brakes at its 1.5–2.1 m/s² and closes until contact. Why the leader brakes was not traced. A cut-in close ahead (the harness) is the same demand arriving at once. On the US-101 replica humans change lanes 1.7–4.0 times as often around FollowerStopper vehicles, mostly into the gap ahead of them (docs/US101_PENETRATION.md). That puts more vehicles in front of an AV. Which sequence dominates on I-24 is not measured here: it needs the trajectories.

The controller's effect, paired by seed (20 seeds, mean [95 % CI]; none of the differences between command paths is resolved):

| arm | throughput [veh/h] | mean travel time [s] | σ_v spatial [m/s] | σ_v temporal [m/s] | fuel [ml/veh-km] | waves |
|---|---|---|---|---|---|---|
| default vs baseline | −214 [−254, −174] (−8.2 %) | +19.4 [+11.1, +27.7] (+8.5 %) | −2.87 [−3.14, −2.60] (−48.8 %) | −2.23 [−2.38, −2.08] (−45.3 %) | +8.6 [+4.2, +13.0] (+7.2 %) | −0.35 [−0.88, +0.18] |
| handback vs baseline | −211 [−247, −174] (−8.0 %) | +21.0 [+12.6, +29.5] (+9.2 %) | −2.90 [−3.16, −2.64] (−49.3 %) | −2.22 [−2.37, −2.07] (−45.0 %) | +8.8 [+4.3, +13.2] (+7.3 %) | −0.40 [−0.96, +0.16] |
| handback vs default | +3.7 [−23.7, +31.1] (+0.2 %) | +1.6 [−2.0, +5.3] (+0.7 %) | −0.03 [−0.09, +0.04] | +0.01 [−0.05, +0.07] | +0.2 [−1.6, +2.0] | −0.05 [−0.23, +0.13] |
| ceiling vs default | +5.7 [−41.0, +52.5] (+0.2 %) | −10.0 [−20.7, +0.6] (−4.0 %) | −0.05 [−0.17, +0.06] | +0.06 [−0.03, +0.16] | −3.2 [−8.7, +2.2] | 0.00 [−0.43, +0.43] |
| mode 27 vs default | +12.3 [−19.8, +44.3] (+0.5 %) | +1.8 [−3.1, +6.7] (+0.7 %) | −0.04 [−0.11, +0.02] | −0.01 [−0.06, +0.04] | −0.6 [−2.9, +1.7] | −0.05 [−0.23, +0.13] |

**Which fix.**
- *Speed mode 27* keeps the command and drops the deceleration bound altogether. The AV then brakes as hard as its safe speed asks, which no car can: 32 samples beyond 9 m/s², up to 39.5. Rejected.
- *The ceiling* (`setMaxSpeed(max(v_cmd, v − b′Δt))`, the pattern the scripted merge already uses) gives the AV back to its model entirely. It now yields to lane changers, and it tracks the command through the IDM's desired-speed term. That is a different controller implementation: four times the braking beyond b′ (2,388 samples against 574), and a travel-time shift of −4.0 % that is not resolved at 20 seeds. It would move every published effect size for a reason other than the collisions. Not chosen.
- *The handback* keeps `setSpeed`. It withdraws the command only in a step in which the model must brake harder than the command can, and re-applies it after. It removes all 36 collisions. Every paired interval against the default contains zero; the largest shift is in the wave count (−0.05 a run, −3.6 %), and the others are within 1 %. Implemented.

### The option

`AVSpec.emergency_handback: bool = False` (`flowstate_core.config`).

- *How it works.* In every simulation step, for every AV holding a command, ramps included, `microsim.runner._emergency_handback_step` asks the AV's model for its follow speed behind its current leader (`vehicle.getFollowSpeed`). When that is below `v − b′·Δt` (`_command_decel`: step 5's bound), the command is withdrawn with `setSpeed(-1)`. The model then brakes with its own authority, up to `emergencyDecel`. The held command is re-applied in the first step in which it can again brake enough.
- *Output.* `meta.json["av_emergency_handback"]`: `n_vehicle_steps`, `n_withdrawals` and `n_vehicles`; `null` when off.
- *Contract.* docs/CONTRACTS.md: the `AVSpec` bullet of §2, the meta key in §3, and a dated section at the end.
- *Off.* No hash moves (the fixture's default arm is `e7f56a5d87b9` with and without the field). The runner's step is call for call the one before, and the default arm's runs reproduce to the digit after the change (seeds 1, 6 and 20 re-run).
- *On, with nothing to withdraw.* On the Sugiyama ring with one FollowerStopper AV (the CI gate's damped run) nothing is withdrawn, and the trajectories are the default's row for row (test).
- *Limits.* Only the leader constraint is predicted. A lane end, a junction foe or a stop keep the command's bound. The gym backend's ego has its own `setSpeed` path and no handback.
- *The default is not changed.* Turning it on changes behaviour without changing any config hash (the policy of WP-93's `force_guard`). Whether the published controller results take it is for the re-runs below to decide.

### Which committed controller results ran under this path

Every vehicle-controller run since M1 (2026-08-29) used this command path (the dispatch line is unchanged since `e8642b7`). The collision counter exists since 2026-09-16 (`8fee770`). Before that, collisions were silent (`--collision.action warn --no-warnings`).

| result (document; artifact) | controller(s) | scenario | collisions recorded? | exposure |
|---|---|---|---|---|
| Strategy sweep (this document; `artifacts/sweep_i24_strategies_summary.json`, 2026-09-24) | FollowerStopper 10 % under none / VSL / ALINEA | I-24 flow-share arm with ramps, 4 lanes | yes: local `meta.json`, the tables above | 311 collisions, 305 AV-caused; the three FollowerStopper rows are affected, the three cells without AVs are not |
| Penetration × compliance battery (docs/I24_SWEEP.md; `artifacts/i24_sweep_summary.json`, 2026-09-18) | FollowerStopper 1–20 % × 25–100 % | `i24_replica_speedcal`, 4 lanes | no: the VM runs had the counter, but only `metrics.json` was archived; the local metas are older runs of other hashes (2026-09-03) | exposed, size unknown |
| Headway-cap sweep (docs/I24_SWEEP.md; `artifacts/i24_cap_sweep_summary.json`) | capacity-aware FollowerStopper 5 % | `i24_replica_speedcal` | no (`metrics.json` only) | exposed, size unknown; no stage yet (the script takes no scenario) |
| Controller probe (`artifacts/i24_controller_probe.json`, 2026-09-06) | FollowerStopper, capacity-aware, 5 %, one seed | `i24_replica_speedcal` | no (before the counter) | exposed |
| US-101 penetration sweep (docs/US101_PENETRATION.md, docs/M3_US101_VALIDATION.md; `artifacts/us101_penetration_summary.json`, 2026-08-30) | FollowerStopper 1–20 % | US-101 replica, 5 lanes | no (before the counter) | exposed, multi-lane |
| US-101 lane-change sweep (VM AB; `artifacts/us101_lane_change_penetration.json`, 2026-09-25) | the same | the same | no: the VM runs had the counter, but only `lane_changes.json` was archived | exposed, multi-lane |
| M3 synthetic sweep, controller comparison, PI retune, JAD oracle and deferral (docs/M3_RESULTS.md, docs/CONTROLLER_COMPARISON.md, docs/PI_CONTROLLER_FIX.md, docs/JAD_ORACLE_RESULTS.md, docs/JAD_DEFERRAL_RESULTS.md; 2026-08-29 to 09-02) | FollowerStopper, PI with saturation, the superseded PI, JAD | `corridor_10km`, 1 lane, EIDM | no (before the counter) | exposed through braking leaders only (one lane: no cut-ins); size unknown |
| Ring benchmark (CI gate, CLAUDE.md §3.2.1) | FollowerStopper, 1 of 22 | 230 m ring, 1 lane | at the gate's seed: 0 | the handback never fires there and the run is identical (test); other seeds not checked |

Documents whose controller results are exposed until the re-runs report: docs/I24_STRATEGIES.md (the FollowerStopper rows above), docs/I24_SWEEP.md, docs/US101_PENETRATION.md, docs/M3_US101_VALIDATION.md, docs/M3_RESULTS.md, docs/CONTROLLER_COMPARISON.md, docs/PI_CONTROLLER_FIX.md, docs/JAD_ORACLE_RESULTS.md and docs/JAD_DEFERRAL_RESULTS.md. The summaries that quote them are exposed too: docs/PAPER_DRAFT.md, docs/PAPER_OUTLINE.md, docs/FLOWSTATE_DOSSIER.md, docs/ROADMAP.md, docs/README.md, docs/WEBSITE_BRIEF.md, README.md, NEXT_STEPS.md and the public site. None was edited here.

### The re-runs, written and not launched (`scripts/gcp/pipeline_i24.sh`, stage 18)

Each `_hb` stage runs `scripts/corridor_sweep.py` on a copy of its committed scenario that differs only in its name and `av.emergency_handback: true`. It keeps the committed grid and seed list, so every run pairs by seed with the committed one. Each `_cc` stage runs the committed configuration unchanged, to count the collisions of results that predate the counter. `corridor_sweep.py`'s cells hash like the committed ones: all 25 of the battery, `ab879e240aed` for the US-101 baseline (checked without simulating). Every tree gets a census, and `meta.json` rides along in the archive.

| stage | what | runs | artifacts |
|---|---|---|---|
| 18a `sweep_i24_strat_hb` | this sweep with the key; the key-off arm is the committed runs | 120 (pool ≤ 12, about 9 GB a run) | `sweep_i24_strategies_hb_summary.json`, `collisions_i24_strat_sweep_hb.json` |
| 18b `sweep_i24_cc`, `sweep_i24_hb` | the penetration × compliance battery, as committed and with the key | 500 an arm (at this sweep's 26 min a FollowerStopper run, about 7 h an arm at 30 processes; compliance 1.0 alone is 140) | `sweep_i24_penetration_{cc,hb}_summary.json`, `collisions_i24_sweep_{cc,hb}.json` |
| 18c `us101_penetration_cc`, `us101_penetration_hb` | the US-101 sweep with its measured boundary; metrics on the replica itself (x 640–1,280 m) | 120 an arm | `sweep_us101_penetration_{cc,hb}_summary.json`, `collisions_us101_penetration_{cc,hb}.json` |
| 18d `controllers_10km_cc`, `controllers_10km_hb` | FollowerStopper, PI with saturation and JAD at 5 %, 100 % on `corridor_10km` | 80 an arm | `sweep_controllers_10km_{cc,hb}_summary.json`, `collisions_controllers_10km_{cc,hb}.json` |

How to read them:
1. The cells without a controller must reproduce the `_cc` (or committed) cells to the digit.
2. The `_cc` census is the collision count of each committed result.
3. The `_hb` census should be 0 in the controller cells, or say what remains.
4. The paired `_hb` − `_cc` deltas of the controller cells are the correction each published effect size needs.

`scripts/gcp/ingest_pipeline_results.sh` installs the new artifacts, scenario copies and trees.

### Two side findings, not acted on

- *A command outlives the controller.* The dispatch skips an AV that is not on a corridor edge, but its last `setSpeed` stays in force (mechanism, step 2). An AV that leaves by an off-ramp drives the ramp at its last command, under the same bound, until it arrives. The handback covers it there too; the default does not.
- *At gaps below `s0` the controller sees no leader.* `vehicle.getLeader` returns the gap net of the ego's `minGap`, and `microsim.runner._leader_obs` reads a negative value as "no leader" (`inf`, `nan`). So FollowerStopper is told the road is free exactly when the bumper gap is below the AV's own `s0` (2.5 m on average in the I-24 population), and it commands `U`. In the harness the held AV's `getLeader` gap was −0.75 m at a bumper gap of 1.25 m, one step before the contact. The safe-speed clamp still caps the result, so this does not cause a collision by itself; it is an observation defect of every controller. Changing it would change the default path.

### How it was measured

- *The strategy sweep.* `scripts/collision_census.py --root runs/i24_strat_sweep` on the 120 local `meta.json` (metadata only; no trajectory read), and session scripts for the repeats, the six human colliders and the edge table.
- *SUMO.* SUMO 1.27.1's sources at tag `v1_27_1` (the files cited) and its TraCI documentation, read in the session.
- *The harness.* libsumo 1.27.1 on macOS, one process, with the road from `microsim.networks.corridor`.
- *The fixture.* `microsim.run_micro` at this tree, one run at a time. The candidate paths (speed mode 27, the ceiling, the handback) were first measured by wrapping `libsumo.vehicle.setSpeed` in the session process. The implemented option then reproduced the wrapped handback on all 20 seeds: the same withdrawals, the same collisions (none) and the same metrics to the digit. Metrics: `validation.metrics.compute_metrics` with throughput at mid-corridor and travel time over 5–95 % of it, warm-up discarded. Braking and the collision anatomy come from the fixture runs' trajectories (session files) and each vehicle's `decel` in the run's route file.

### Limitations

- *One fixture.* 20 seeds, one geometry, one demand, one fleet. On I-24 the fix is stage 18a's to measure.
- *Anatomy.* On I-24 the split between cut-ins and braking leaders is not measured; it needs trajectories.
- *What the handback covers.* Only the leader constraint (see "The option").
- *Rates.* No rate per AV-km. The rates per departed vehicle include the warm-up.
- *Platform.* macOS only; WP-85 found fixture runs that land differently on Linux, so the run test pins outcomes that are far from a margin (three default collisions, all AV-caused; none with the key).

### Bookkeeping

- *Edited:*
  - `packages/flowstate_core/flowstate_core/config.py`: `AVSpec.emergency_handback`.
  - `packages/microsim/microsim/runner.py`: `IDM_MIN_NEXT_SPEED_DECEL`, `HANDBACK_EPS_MS`, `_command_decel`, `_handback_needed` and `_emergency_handback_step` (new); the handback state in `run_micro`; the command recorded in the dispatch; the per-step pass after the dispatch and in a step with no corridor vehicle; `meta.json["av_emergency_handback"]`.
  - docs/CONTRACTS.md: §2 `AVSpec`, §3 the meta key, and a dated section at the end.
  - `scripts/gcp/pipeline_i24.sh`: stage 18 and the archive lines.
  - `scripts/gcp/ingest_pipeline_results.sh`: the new artifacts, scenario copies and trees.
  - This section.
- *Created:*
  - `scripts/collision_census.py`.
  - `artifacts/collisions_i24_strat_sweep.json`.
  - `tests/test_microsim/test_microsim_emergency_handback.py`, seven tests: the bound per model; the per-step pass on a fake SUMO (withdraw, count, re-apply, drop); off by default and hash-neutral; the constructed cut-in on real SUMO (the command collides braking at `decel`; the model alone and the handback do not); the fixture (the default collides, every collider a compliant AV; the key does not); the ring (nothing withdrawn, trajectories identical).
  - `tests/test_scripts/test_collision_census.py`, two tests.
- *Not edited:* CHANGELOG.md, ROADMAP.md, README.md, docs/PAPER_DRAFT.md, the other documents' results, the scenarios, every existing test, fixture, golden and committed artifact.
- *Tests run:*
  - the two new modules: 9 passed;
  - `test_config_hash.py`, `test_microsim_golden.py` and `test_microsim_ring_gate.py`: 27 passed;
  - `test_microsim_osm_ramps.py`, `test_microsim_oracle.py`, `test_microsim_runner_smoke.py`, `tests/test_api/test_scenarios.py` and `tests/test_flowstate_core`: 112 passed;
  - `ruff check`, `ruff format --check` and `mypy --strict` on the three strict packages.
- *Session files (`wp95/`, not committed):*
  - `harness_cutin.py` and its five run directories;
  - `fixture_runs.py` (arms, the `setSpeed` wrapper and the per-run records), `fixture_agg.py`, `fixture_tenure.py` and the records `fixture/results_main.jsonl` (120 runs) and `fixture/results.jsonl` (the three re-runs of the default arm);
  - `strat_collisions.py`, `strat_detail.py`, `strat_pairs.py` and `strat_recount.py` (the strategy sweep's metas);
  - `hash_check.py` (the stage configurations' hashes) and `stagecheck/` (the stages' scenario copies, built and validated);
  - `short_probe.py` and `ring_probe.py` (the tests' fixture and ring outcomes);
  - SUMO 1.27.1's sources read, in `sumo_src/`;
  - the fixture's run directories.

Every number above is from those runs and files, from SUMO 1.27.1's source at tag `v1_27_1`, from the 120 local `meta.json` of this sweep, or from the committed files named.

## 2026-09-26 (VM AH) — the re-runs: the handback removes every controller collision, and no controller result moves beyond noise

VM AH (n2-standard-32, us-west1-c, self-deleting; commit a62ed7f, VM snapshot 06b8c25) ran stage 18's cheaper arms: `sweep_i24_strat_hb` (121 min), `us101_penetration_cc` and `_hb`, `controllers_10km_cc` and `_hb` (about 1 min each). Every run pairs by seed with its counterpart.

**Collisions** (the censuses, `artifacts/collisions_*.json`):

| result | configuration as committed | with `emergency_handback` |
|---|---|---|
| I-24 strategy sweep, FollowerStopper alone / + ALINEA / + VSL | 150 / 105 / 56 (20, 19, 12 of 20 runs) | 0 / 0 / 0 |
| I-24 strategy sweep, baseline / ALINEA / VSL | 0 / 0 / 0 | 0 / 0 / 0 |
| US-101 penetration sweep, FollowerStopper 1–20 % and baseline | 0 in 120 runs | 0 in 120 runs |
| synthetic 10 km, PI with saturation at 5 % | 16 (5 of 20 runs) | 0 |
| synthetic 10 km, FollowerStopper and JAD at 5 %, baseline | 0 | 0 |

**The metrics.** The cells without a controller reproduce the committed ones to the digit (strategy sweep: baseline, ALINEA, VSL; 10 km: baseline), and the 10 km FollowerStopper and JAD cells, where the handback never acts, are byte-identical. Where it acts, no change is resolved. Paired by seed, the handback run minus the committed run (95 % t-interval, 20 seeds):

| cell | throughput [veh/h] | σ_v temporal [m/s] | fuel [ml/veh-km] |
|---|---|---|---|
| I-24, FollowerStopper alone | −54.9 [−616.3, +506.6] (−1.9 %) | +0.007 [−0.083, +0.097] | +6.8 [−48.7, +62.2] |
| I-24, FollowerStopper + ALINEA | +63.3 [−481.4, +607.9] (+1.6 %) | +0.021 [−0.092, +0.134] | −4.5 [−39.2, +30.1] |
| I-24, FollowerStopper + VSL | −188.2 [−497.1, +120.7] (−7.1 %) | +0.006 [−0.013, +0.025] | +24.0 [−19.5, +67.5] |
| 10 km, PI with saturation at 5 % | +11.0 [−1.2, +23.2] | −0.003 [−0.042, +0.035] | −0.03 [−0.14, +0.07] |
| US-101, FollowerStopper 1–20 % (five cells) | within ±10 veh/h, every interval containing 0 | within ±0.013 | within ±0.1 |

**Reading.**
- The committed controller results are not changed by the collisions beyond their own noise: the FollowerStopper cells of this sweep, the US-101 penetration sweep and the synthetic 10 km comparison stand as published. Their collisions were a defect of the command path, now measured and removable.
- The I-24 strategy sweep's FollowerStopper cells are wide (throughput ±500–600 veh/h a seed pair), so "not resolved" there is weak evidence of no effect; the synthetic and US-101 cells are tight.
- **Not re-run:** the I-24 penetration × compliance battery (`sweep_i24_cc` / `_hb`, 500 runs an arm, about 7 h each, about $22 for both) and the headway-cap sweep (no stage: its script takes no scenario). Their collision counts are unknown. Given the rows above, re-running them is the owner's call.
- The two side findings of WP-95 (an AV leaving by an off-ramp keeps its last command; `_leader_obs` reports no leader below the AV's own s0) are on the default path and not addressed here.

Every number above is from `artifacts/collisions_{i24_strat_sweep,i24_strat_sweep_hb,us101_penetration_cc,us101_penetration_hb,controllers_10km_cc,controllers_10km_hb}.json`, `artifacts/sweep_{i24_strategies_hb,us101_penetration_cc,us101_penetration_hb,controllers_10km_cc,controllers_10km_hb}_summary.json`, the committed `artifacts/sweep_i24_strategies_summary.json`, and the per-run `metrics.json` of both arms (paired in the session, `vm_ah/`).
