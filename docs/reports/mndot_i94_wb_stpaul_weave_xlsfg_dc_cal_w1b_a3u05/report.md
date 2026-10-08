# FlowState calibration & validation report

> **MODEL INTEGRITY FAILURE — 1 SUMO collision(s) in 1 of 20 run(s); the no_collisions acceptance criterion fails. A collision is a model defect, not a traffic outcome: see Model integrity and Limitations.**
Generated: 2026-10-08T09:42:45Z

## Client summary

Baseline gate FAILED (docs/FRISCO_PROTOCOL.md section 6): C1 link flows (calibration), C3 speeds (calibration), C1 link flows (validation), C3 speeds (validation), C4 wave speed (calibration), C5 collisions (all runs). No strategy recommendation may be made from this model.

- replicates PASS: Replicates: 20 seeded replicate(s); the protocol scores every check over at least 20. Source: [FlowState] CLAUDE.md section 0.6; protocol section 4.
- days PASS: Day sets: the calibration-day artifact holds exactly the split's 5 calibration day(s) and the validation-day artifact its 4 validation day(s), none shared. Source: [FlowState] protocol section 3.2.
- quality PASS: Targets quality-masked: calibration days: runs/p1_rehearsal/dq/data_quality.json (sha256 8cae907dec40): 24 masked detector-day(s) (24 excluded whole), 912 reading(s) set aside; validation days: runs/p1_rehearsal/dq/data_quality.json (sha256 8cae907dec40): 19 masked detector-day(s) (19 excluded whole), 720 reading(s) set aside. Source: [FlowState] protocol section 2.2.
- C1 FAIL: Link flows, calibration days: GEH < 5 on 74.9 % of 840 station-hour comparisons pooled over 20 replicate(s) (per-replicate mean 74.9 %, 95 % interval 73.2 % to 76.6 %); the target is >= 85.0 %, short by 10.1 percentage points; hours anchored at the study period's start (1800 s). Source: [federal] FHWA TAT Vol. III 2004 (profile fhwa_default).
- C2 FAIL (reported, not part of the gate): Link flows, Texas criterion, calibration days: GEH < 3 on 58.6 % of 840 station-hour comparisons pooled over 20 replicate(s) (per-replicate mean 74.9 %, 95 % interval 73.2 % to 76.6 %); the target is >= 100.0 %, short by 41.4 percentage points; hours anchored at the study period's start (1800 s). Source: [federal] TxDOT TSAP ch. 13 (profile txdot_tsap_ch13); reported, not gating.
- C3 FAIL: Speeds, calibration days: RMSPE of 15-minute station mean speeds 42.5 % (mean over 20 replicate(s), 95 % interval 40.6 % to 44.4 %; point speeds at the stations, as a loop reads them); the target is at most 15.0 %, over by 27.5 percentage points (diagnostics: 5 min 44.7 %; 60 min 36.2 %). Source: common microsimulation practice (CLAUDE.md section 7.1), cited in the report.
- C6 PASS: Bottlenecks, calibration days: 0 observed bottleneck(s), 0 active for 30 min or more, compared with 20 replicate(s) (point speeds at the stations, as a loop reads them); all four rules hold. Source: [FlowState] protocol section 5.
- C1 FAIL: Link flows, validation days: GEH < 5 on 73.2 % of 840 station-hour comparisons pooled over 20 replicate(s) (per-replicate mean 73.2 %, 95 % interval 71.4 % to 75.0 %); the target is >= 85.0 %, short by 11.8 percentage points; hours anchored at the study period's start (1800 s). Source: [federal] FHWA TAT Vol. III 2004 (profile fhwa_default).
- C2 FAIL (reported, not part of the gate): Link flows, Texas criterion, validation days: GEH < 3 on 56.1 % of 840 station-hour comparisons pooled over 20 replicate(s) (per-replicate mean 73.2 %, 95 % interval 71.4 % to 75.0 %); the target is >= 100.0 %, short by 43.9 percentage points; hours anchored at the study period's start (1800 s). Source: [federal] TxDOT TSAP ch. 13 (profile txdot_tsap_ch13); reported, not gating.
- C3 FAIL: Speeds, validation days: RMSPE of 15-minute station mean speeds 54.3 % (mean over 20 replicate(s), 95 % interval 51.1 % to 57.4 %; point speeds at the stations, as a loop reads them); the target is at most 15.0 %, over by 39.3 percentage points (diagnostics: 5 min 58.2 %; 60 min 42.4 %). Source: common microsimulation practice (CLAUDE.md section 7.1), cited in the report.
- C6 PASS: Bottlenecks, validation days: 1 observed bottleneck(s), 0 active for 30 min or more, compared with 20 replicate(s) (point speeds at the stations, as a loop reads them); all four rules hold. Source: [FlowState] protocol section 5.
- C4 FAIL: Wave speed: a backward front in 18 of 20 replicate(s) (90.0 %; at least 80.0 % needed, a replicate without a front counting as a miss); simulated backward wave speed 3.8 km/h, 95 % interval 3.6 to 4.1 km/h over those replicates (stack detector); the band is 14-22 km/h, outside it by 10.2 km/h; observed on the calibration days (detector cross-correlation): median 19.1 km/h from 7 of 13 station pairs. Source: empirical stop-and-go literature (flowstate_core.constants.WAVE_SPEED_BAND_KMH).
- C5 FAIL: Collisions: 1 SUMO collision(s) in 1 of 20 run(s) that record the counter; the gate requires zero. Source: [FlowState] CLAUDE.md section 3.3; owner decision 2026-10-04.

### What we are confident about, and what we are not

| Statement | Confident | Basis |
|---|---|---|
| Traffic counts at the detectors (link flows, C1) | no | Link flows, calibration days: GEH < 5 on 74.9 % of 840 station-hour comparisons pooled over 20 replicate(s) (per-replicate mean 74.9 %, 95 % interval 73.2 % to 76.6 %); the target is >= 85.0 %, short by 10.1 percentage points; hours anchored at the study period's start (1800 s). Link flows, validation days: GEH < 5 on 73.2 % of 840 station-hour comparisons pooled over 20 replicate(s) (per-replicate mean 73.2 %, 95 % interval 71.4 % to 75.0 %); the target is >= 85.0 %, short by 11.8 percentage points; hours anchored at the study period's start (1800 s). |
| Speeds at the detectors, 15-minute averages (C3) | no | Speeds, calibration days: RMSPE of 15-minute station mean speeds 42.5 % (mean over 20 replicate(s), 95 % interval 40.6 % to 44.4 %; point speeds at the stations, as a loop reads them); the target is at most 15.0 %, over by 27.5 percentage points (diagnostics: 5 min 44.7 %; 60 min 36.2 %). Speeds, validation days: RMSPE of 15-minute station mean speeds 54.3 % (mean over 20 replicate(s), 95 % interval 51.1 % to 57.4 %; point speeds at the stations, as a loop reads them); the target is at most 15.0 %, over by 39.3 percentage points (diagnostics: 5 min 58.2 %; 60 min 42.4 %). |
| Where and when the slowdowns form (bottlenecks, C6) | yes | Bottlenecks, calibration days: 0 observed bottleneck(s), 0 active for 30 min or more, compared with 20 replicate(s) (point speeds at the stations, as a loop reads them); all four rules hold. Bottlenecks, validation days: 1 observed bottleneck(s), 0 active for 30 min or more, compared with 20 replicate(s) (point speeds at the stations, as a loop reads them); all four rules hold. |
| Stop-and-go wave speed (C4) | no | Wave speed: a backward front in 18 of 20 replicate(s) (90.0 %; at least 80.0 % needed, a replicate without a front counting as a miss); simulated backward wave speed 3.8 km/h, 95 % interval 3.6 to 4.1 km/h over those replicates (stack detector); the band is 14-22 km/h, outside it by 10.2 km/h; observed on the calibration days (detector cross-correlation): median 19.1 km/h from 7 of 13 station pairs. |
| No simulated collisions (C5) | no | Collisions: 1 SUMO collision(s) in 1 of 20 run(s) that record the counter; the gate requires zero. |
| No permanent standstill (gridlock) in any run | yes | no run of 20 had a queue standing with no discharge for 10 min or more |
| Fuel (model estimate, SUMO HBEFA emission class; not validated against measured fuel) | no | fuel is not validated: no measured fuel was compared, so every fuel figure is a model estimate (docs/FRISCO_PROTOCOL.md section 8.6) |
| Effects of the strategies | no | not delivered: the model did not reproduce the corridor |
| Robustness of strategy effects to driver and demand uncertainty | no | not evaluated in this report (docs/FRISCO_PROTOCOL.md section 8.5) |
| Waiting time on ramps and before entering the network | yes | counted: every run records total delay and travel time including waiting (docs/FRISCO_PROTOCOL.md section 8.2); no strategy recommendation is made because the run set has no single baseline (do-nothing) group to compare against |

### Strategy results

This report contains no strategy recommendations: the model did not reproduce the corridor (the baseline gate failed), so strategy results are not delivered as findings.

### Data, days and assumptions

- Calibration days: 2026-09-02, 2026-09-03, 2026-09-08, 2026-09-15, 2026-09-16. Validation days: 2026-09-01, 2026-09-09, 2026-09-10, 2026-09-17. Drawn by the protocol's seeded split (seed 20261004, docs/FRISCO_PROTOCOL.md section 3).
- Excluded detector 3240: S792 lane-3 loop chatters: 14 percent occupancy against 27-32 percent on its neighbours, 78 percent null night samples, count rises 775 veh/h into a bracket nothing enters (docs/ONBOARDING_MNDOT.md section 7 item 2, 2026-09-24)
- Observed data: artifacts/p1_rehearsal_2026-10-04/observations_calibration.json, dates 20260902, 20260903, 20260908, 20260915, 20260916; mean over dates per window (weekday typical profile).
- Ramp volumes estimated from mainline differences, and every split assumption behind them, are listed in the study's ramp-estimation artifact, not in this run set (docs/FRISCO_PROTOCOL.md section 2.3).
- Strategy assumptions: each strategy arm's penetration, compliance and settings are as its label states; they are assumptions, not measured behaviour.

### Limits of this study

- Single corridor: the results describe this corridor, period and day set; transfer to other corridors is not established.
- Model-form uncertainty: the car-following and lane-change models' own assumptions are not captured by the seed-to-seed intervals.
- Every fuel figure is a model estimate (SUMO HBEFA emission class), not validated against measured fuel; differences in fuel between configurations are model predictions.
- Compliance and strategy assumptions: strategy results hold only for the settings simulated, under the configured driver population and demand.
- Results are reported as they came out, including failures.

## Provenance

Profile: `fhwa_tat3_2004`. Seeds: 134183728835869882, 165503670820534583, 2378473973028931053, 3011106312394044631, 3747978530954135749, 3944094060050347669, 4910985839736976611, 5690692725577505498, 6134032994440706937, 6143473282319009404, 6538422657834023852, 661281422688282993, 677105600768189526, 6904272788004776631, 6914975401685141156, 6953598295321596746, 7382187975121682178, 8026499204807041784, 8557154790156791364, 887972120279483394.

Measurement window: each run's configured warm-up is discarded from every metric (warm-up per run, in seconds: 1800). Travel times keep whole journeys that begin inside the window and are measured over [1089.1, 11071.8] m. Fuel per vehicle-km, a model estimate (SUMO HBEFA emission class; not validated against measured fuel), remains a whole-run ratio unless the run records a post-warm-up fuel total.

Insertion: 647080 vehicles planned over 20 run(s), 646724 departed (0.999 of plan on average, lowest 0.997), 31826.5 arrived per run (over 20 of 20); verdict: ok.

Weave exits at on-ramp 745524613 (exit C-D split 18208090): 680 of 30988 reached exiters (2.19 %) were given up at the gore's end and rerouted through over 20 run(s), above the 2 % threshold; the exit's link flow is short by that count and every mainline link downstream carries it.

Weave exits at on-ramp 769818012 (exit off-ramp 18207598): 536 of 84091 reached exiters (0.637 %) were given up at the gore's end and rerouted through over 20 run(s), within the 2 % threshold; the exit's link flow is short by that count and every mainline link downstream carries it.

| Run | Config hash | Seed | Tier | seeded | Wall time [s] |
|---|---|---|---|---|---|
| 134183728835869882 | `03fec0ed5521` | 134183728835869882 | micro | seeded=False | 976.4 |
| 165503670820534583 | `03fec0ed5521` | 165503670820534583 | micro | seeded=False | 642.4 |
| 2378473973028931053 | `03fec0ed5521` | 2378473973028931053 | micro | seeded=False | 600.9 |
| 3011106312394044631 | `03fec0ed5521` | 3011106312394044631 | micro | seeded=False | 915.5 |
| 3747978530954135749 | `03fec0ed5521` | 3747978530954135749 | micro | seeded=False | 755.7 |
| 3944094060050347669 | `03fec0ed5521` | 3944094060050347669 | micro | seeded=False | 789.1 |
| 4910985839736976611 | `03fec0ed5521` | 4910985839736976611 | micro | seeded=False | 760.8 |
| 5690692725577505498 | `03fec0ed5521` | 5690692725577505498 | micro | seeded=False | 781.8 |
| 6134032994440706937 | `03fec0ed5521` | 6134032994440706937 | micro | seeded=False | 752.6 |
| 6143473282319009404 | `03fec0ed5521` | 6143473282319009404 | micro | seeded=False | 716.6 |
| 6538422657834023852 | `03fec0ed5521` | 6538422657834023852 | micro | seeded=False | 728.5 |
| 661281422688282993 | `03fec0ed5521` | 661281422688282993 | micro | seeded=False | 715.8 |
| 677105600768189526 | `03fec0ed5521` | 677105600768189526 | micro | seeded=False | 999.5 |
| 6904272788004776631 | `03fec0ed5521` | 6904272788004776631 | micro | seeded=False | 713.6 |
| 6914975401685141156 | `03fec0ed5521` | 6914975401685141156 | micro | seeded=False | 606.4 |
| 6953598295321596746 | `03fec0ed5521` | 6953598295321596746 | micro | seeded=False | 898.6 |
| 7382187975121682178 | `03fec0ed5521` | 7382187975121682178 | micro | seeded=False | 794.8 |
| 8026499204807041784 | `03fec0ed5521` | 8026499204807041784 | micro | seeded=False | 756.9 |
| 8557154790156791364 | `03fec0ed5521` | 8557154790156791364 | micro | seeded=False | 586.1 |
| 887972120279483394 | `03fec0ed5521` | 887972120279483394 | micro | seeded=False | 732.1 |

### Package versions (from run metadata)

- eclipse-sumo: `1.27.1`
- flowstate_core: `2.0.0`
- libsumo: `1.27.1`
- microsim: `2.0.0`
- numpy: `2.5.2`
- pandas: `3.0.5`
- pyarrow: `25.0.1`
- python: `3.12.15`

### Calibration artifacts used

| Artifact | data_hash |
|---|---|
| `artifacts/idm_i24_capacity_amax_k1.0.json` | `aa97dd93d2bf250ea23c3624f91afe8d7035a672fd13efbd53013709e12c7d56` |

### Observed data

The link-flow and segment-speed criteria are scored against this observed artifact: hourly volumes formed from the fully observed windows of each hour at every mainline station, and mean speeds per station segment (each station owns the span to the midpoints with its neighbours). Simulation time zero is the artifact's local start time; the run's warm-up, and any window the run does not cover to its end, are excluded, and a station-window the detector did not measure is skipped, never imputed — the coverage rows say how much of the grid was compared. A station whose cross-section lies outside the simulated position span is excluded from both comparisons rather than scored against a simulated flow of zero; the rows say how many were. When the artifact carries a detector-estimated backward wave speed, it is printed here as context for the band the simulated wave speed is scored against — it is a property of the corridor, not a target, and no criterion is evaluated from it.

| Field | Value |
|---|---|
| artifact | artifacts/p1_rehearsal_2026-10-04/observations_calibration.json |
| corridor | I-94 WB |
| source provider | MnDOT RTMC Mayfly API |
| source dates | 20260902, 20260903, 20260908, 20260915, 20260916 |
| source url | https://data.dot.state.mn.us/mayfly |
| aggregation | mean over dates per window (weekday typical profile) |
| local clock time of simulation t = 0 | 05:30 |
| observation window [s] | 300 |
| mainline stations compared | 14 |
| observation windows | 48 |
| windows inside the measurement window | 42 |
| station-windows with an observed flow [fraction] | 1 |
| station-windows with an observed speed [fraction] | 1 |
| link-hour comparisons (pooled over replicates) | 840 |
| speed cells compared (pooled over replicates) | 11760 |
| replicates scored against the observations | 20 |
| stations excluded (outside the simulated span) | 0 |
| detector-estimated backward wave speed (context, not a criterion) | median 19.1 km/h (IQR 18–23.9) from 7 of 13 station pairs; leave-one-date-out 18.5–20.8 km/h over 5 dates (fewest 5 pairs); the model's band is 14–22 km/h |

## Model integrity

What the runs' metadata records about the simulation itself misbehaving
(docs/CONTRACTS.md). A collision in a car-following simulation is a model
defect, not a traffic outcome, and so is a lock: vehicles standing with no
discharge past a point while a queue builds behind it, read from each run's
space-time table and vehicle table (validation.locks). A run whose metadata
carries no counter, or that has neither table, is reported as not recorded,
never as zero.

- Collisions: 1 over 20 run(s) that record the counter; per run 0.05 [-0.05465, 0.1547] (two-sided 95 % t-interval, n = 20); 0.00155 per 1,000 departed vehicles (1 over 646724 departed in 20 run(s), whole runs including warm-up).
- Runs with collisions: 887972120279483394 (1)
- Locks: 0 of 20 run(s) locked (0 %; 95 % Clopper–Pearson interval 0–16.8 %). A lock is a queue standing with no discharge past a point for at least 10 min.
- Weave rules at on-ramp 745524613 (exit C-D split 18208090): W1b released 90 of 20340 entrance departures (0.442 %) over 20 run(s), within the 1 % design bound; W2 (0 of 20 run(s) with all three guards on): handback skips not recorded, close-leader withholds not recorded, opposing deferrals not recorded, of them vetoes not recorded.
- Weave rules at on-ramp 769818012 (exit off-ramp 18207598): W1b released 3 of 101644 entrance departures (0.00295 %) over 20 run(s), within the 1 % design bound; W2 (0 of 20 run(s) with all three guards on): handback skips not recorded, close-leader withholds not recorded, opposing deferrals not recorded, of them vetoes not recorded.
- Forced lane changes at on-ramp 18207436 (scripted merge): 683 of 8682 completed change(s) over 20 run(s) were made under SUMO's forced lane-change mode, in which SUMO refuses a change only on an overlap — the path around its own lane-change safety checks.
- Forced lane changes at on-ramp 178547099 (scripted merge): 2824 of 16754 completed change(s) over 20 run(s) were made under SUMO's forced lane-change mode, in which SUMO refuses a change only on an overlap — the path around its own lane-change safety checks.
- Forced lane changes at on-ramp 745524613 (weave section): 5726 of 17017 completed change(s) over 20 run(s) were made under SUMO's forced lane-change mode, in which SUMO refuses a change only on an overlap — the path around its own lane-change safety checks.
- Forced lane changes at on-ramp 769818012 (weave section): 5967 of 57667 completed change(s) over 20 run(s) were made under SUMO's forced lane-change mode, in which SUMO refuses a change only on an overlap — the path around its own lane-change safety checks.

| Lane | Edge | Collisions logged | Position along the lane [m] | Runs |
|---|---|---|---|---|
| `51388891_1` | `51388891` | 1 | 29.9 | 887972120279483394 |

## Acceptance criteria

| Criterion | Value | Threshold | Evaluated | Result |
|---|---|---|---|---|
| link_flows_geh | 0.6964 | GEH < 5 for > 85% of link-hour comparisons | yes | FAIL — fraction of comparisons with GEH < 5 |
| wave_speed | 3.838 | backward wave speed in [14, 22] km/h (emergent, unseeded) | yes | FAIL — detector: stack: slant-stack peak of the two-way demeaned field on 15 s x 75 m bins over front speeds [-40, -2] km/h in 0.25 km/h steps, peak/median contrast >= 3, edge peaks rejected |
| ring_emergence | — | Sugiyama ring emergence benchmark reproduced | no | NOT EVALUATED — not evaluated: input not supplied |
| ring_dampening | — | Stern single-AV dampening benchmark reproduced | no | NOT EVALUATED — not evaluated: input not supplied |
| n_seeds | 20 | n_seeds >= 20 | yes | PASS |
| sensitivity_grid | — | penetration {1%, 2%, 5%, 10%, 15%, 20%} x compliance {25%, 50%, 80%, 100%} grid published with CIs | no | NOT EVALUATED — not evaluated: input not supplied |
| no_collisions | 1 | zero SUMO collisions in every run, each run recording the counter | yes | FAIL — 1 collision(s) in 1 of 20 run(s) that record the counter; FlowState internal standard (CLAUDE.md §3.3; owner decision 2026-10-04), not an FHWA or DOT criterion |
| no_locks | 0 | no run locked (no queue standing without discharge for 10 min or more), every run recorded | yes | PASS — no lock in 20 run(s); FlowState internal standard (CLAUDE.md §0.1; docs/I94_COLLAPSE_DIAGNOSIS.md §7, 2026-10-07), not an FHWA or DOT criterion |
| w1b_release_share (on-ramp 745524613) | 0.004425 | reported beside no_locks, not gating: W1b releases as a share of the entrance's departures, pooled over the runs; above 1% flagged in Limitations | yes | REPORTED — reported, not gating: 90 of 20340 entrance departures released into the paired exit by W1b over 20 run(s) (0.44 %), within the 1% design bound; W2 off (any switch) in 20 of 20 run(s); W2 over the runs it was on in: handback skips not recorded, close-leader withholds not recorded, opposing deferrals not recorded, of them vetoes not recorded; Amendment 4 (docs/FRISCO_PROTOCOL.md, 2026-10-07): weave rules W1b and W2 are part of the model; a FlowState disclosure, not an FHWA or DOT criterion |
| w1b_release_share (on-ramp 769818012) | 2.951e-05 | reported beside no_locks, not gating: W1b releases as a share of the entrance's departures, pooled over the runs; above 1% flagged in Limitations | yes | REPORTED — reported, not gating: 3 of 101644 entrance departures released into the paired exit by W1b over 20 run(s) (0.00 %), within the 1% design bound; W2 off (any switch) in 20 of 20 run(s); W2 over the runs it was on in: handback skips not recorded, close-leader withholds not recorded, opposing deferrals not recorded, of them vetoes not recorded; Amendment 4 (docs/FRISCO_PROTOCOL.md, 2026-10-07): weave rules W1b and W2 are part of the model; a FlowState disclosure, not an FHWA or DOT criterion |

A row marked not evaluated is never a pass: either no input was supplied,
or the value was measured with a recipe this profile does not accept (the
row's result says which). An unevaluated criterion counts as failing. A row
marked not recorded is never a pass either: some run in the set carries no
record of what the row checks, and a missing record is not a zero. The
no_collisions row is a FlowState model-integrity requirement, not an FHWA
criterion: every run must record its SUMO collision counter, and the total
must be zero. So is the no_locks row: every run must carry a lock record, and
no run may lock.

Wave-speed criterion input: mean over 18 of 20 unseeded replicate(s) of group merge scripted on on-ramp 18207436 + merge scripted on on-ramp 178547099 + merge weave on on-ramp 745524613 + merge weave on on-ramp 769818012 (`03fec0ed5521`), measured with the stack detector on its own bins; 2 replicate(s) detected no backward front and are excluded from the mean; fewer than 20 contributing replicate(s) — underpowered, not a headline value. A row marked REPORTED is a disclosure, never a pass or a fail: Amendment 4 (docs/FRISCO_PROTOCOL.md, 2026-10-07) puts weave rules W1b and W2 into the model at every weaving section, and each w1b_release_share row states the share of the entrance's departures W1b sent into the paired exit after standing its dwell at the end of the auxiliary lane, pooled over every run of the set, with W2's counters. A share above 1 % is listed under Limitations.

### Speed criterion by time aggregation

The segment-speed criterion compares a replicate mean with one recorded
day. The floor column is the recorded field against its own three-window
moving average: below that resolution the day does not repeat itself, so
no ensemble mean can score under the floor. The criterion row keeps its
native resolution; this table says how much of its value is resolution.

| Aggregation | RMSPE, replicate mean vs observed | Floor: observed vs its own three-window average |
|---|---|---|
| 5 min (criterion) | 0.4275 | 0.03733 |
| 15 min | 0.4181 | 0.07091 |
| 30 min | 0.3964 |  |
| 60 min | 0.3765 |  |
| whole period | 0.2197 |  |

## Metrics

Mean with two-sided t-distribution confidence bounds at the
95 percent level over n replicates. Rows flagged
underpowered have fewer than 20 replicates and must not be
quoted as headline results. Travel-time rows rest on the vehicles counted by
the n_travel_time_veh row, which are those that entered within the
measurement window and completed the span.

The wave_speed_kmh row is the metrics detector's diagnostic reading (standard); the acceptance criterion above is measured separately with the profile's stack detector on its own bins, so the two values can differ.

Rows marked (model estimate) carry fuel (model estimate, SUMO HBEFA emission class; not validated against measured fuel). SUMO computes fuel from its HBEFA emission classes; this study has measured no fuel to check it against (docs/FRISCO_PROTOCOL.md section 8.6).

### merge scripted on on-ramp 18207436 + merge scripted on on-ramp 178547099 + merge weave on on-ramp 745524613 + merge weave on on-ramp 769818012 (`03fec0ed5521`)

Replicates: n = 20 distinct seeds (134183728835869882, 165503670820534583, 2378473973028931053, 3011106312394044631, 3747978530954135749, 3944094060050347669, 4910985839736976611, 5690692725577505498, 6134032994440706937, 6143473282319009404, 6538422657834023852, 661281422688282993, 677105600768189526, 6904272788004776631, 6914975401685141156, 6953598295321596746, 7382187975121682178, 8026499204807041784, 8557154790156791364, 887972120279483394); replicate
criterion (n_seeds >= 20): PASS.

Not delivered: the model did not reproduce the corridor (the baseline gate failed, docs/FRISCO_PROTOCOL.md section 6). Strategy runs are kept for internal learning only; their numbers are not findings and are not shown.


## Speed contours

![Space-time mean-speed contour, seed 6914975401685141156](speed_contour_00_seed_6914975401685141156.png)

## Limitations

- This run set contains 1 SUMO collision(s) in 1 of 20 run(s) (887972120279483394 (1)), at lane `51388891_1` (edge `51388891`, 29.9 m): 1. A collision is a model defect, not a traffic outcome, and every metric of those runs includes the vehicles involved; see Model integrity.
- At the on-ramp 745524613 weave, W2 (any guard) off in 20 of 20 run(s). Amendment 4 puts both rules into the model; turning one off is allowed only to reproduce a result published before it, and such runs are not the model's current form.
- At the on-ramp 769818012 weave, W2 (any guard) off in 20 of 20 run(s). Amendment 4 puts both rules into the model; turning one off is allowed only to reproduce a result published before it, and such runs are not the model's current form.
- Results cover a single corridor/scenario family; transfer to other
  corridors is not established.
- Model-form uncertainty (IDM/SUMO car-following assumptions, effective
  single-pipe demand representation) is not captured by seed-to-seed
  confidence intervals.
- AV penetration and compliance are swept assumptions, not measured
  behavior; conclusions hold only within the swept grid.
- Metrics from fewer than 20 replicates are flagged
  underpowered and are diagnostics, not claims.
- Every fuel figure is a model estimate (SUMO HBEFA emission class), not validated against measured fuel; differences in fuel between configurations are model predictions.
- Measurement window: each run's configured warm-up is discarded from every metric (warm-up per run, in seconds: 1800). Travel times keep whole journeys that begin inside the window and are measured over [1089.1, 11071.8] m. Fuel per vehicle-km, a model estimate (SUMO HBEFA emission class; not validated against measured fuel), remains a whole-run ratio unless the run records a post-warm-up fuel total.
- The observed comparison covers the artifact's stations, windows and days
  only; station-windows the detectors did not measure are excluded from both
  criteria rather than filled in, so the coverage rows bound how much of the
  corridor and period the two rows actually speak for.
