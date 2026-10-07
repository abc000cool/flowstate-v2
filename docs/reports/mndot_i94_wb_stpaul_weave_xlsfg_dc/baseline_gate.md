# Baseline gate

**Baseline gate FAILED (docs/FRISCO_PROTOCOL.md section 6): C1 link flows (calibration), C3 speeds (calibration), C6 bottlenecks (calibration), C1 link flows (validation), C3 speeds (validation), C4 wave speed (calibration). No strategy recommendation may be made from this model.**

Configuration: `scenarios/mndot_i94_wb_stpaul_weave_dc.yaml` (`db9fbab5fc6e`).

| Check | Name | Days | Status | Gating | Value | Target | Source |
|---|---|---|---|---|---|---|---|
| replicates | Replicates | all runs | PASS | yes | 20 | every check scored over at least 20 seeded replicates | [FlowState] CLAUDE.md section 0.6; protocol section 4 |
| days | Day sets | both day sets | PASS | yes | — | a day split is supplied, its calibration and validation sides are disjoint and non-empty, and each day set's artifact holds exactly its side's dates | [FlowState] protocol section 3.2 |
| quality | Targets quality-masked | both day sets | PASS | yes | — | each day set's observed targets were built with the data-quality check's verdicts masked out (source.quality recorded) | [FlowState] protocol section 2.2 |
| C1 | Link flows | calibration | FAIL | yes | 0.6179 | GEH < 5 on >= 85.0 % of station-hour comparisons, hours anchored at the study period's start | [federal] FHWA TAT Vol. III 2004 (profile fhwa_default) |
| C2 | Link flows, Texas criterion | calibration | FAIL | no | 0.4583 | GEH < 3 on >= 100.0 % of station-hour comparisons, hours anchored at the study period's start | [federal] TxDOT TSAP ch. 13 (profile txdot_tsap_ch13); reported, not gating |
| C3 | Speeds | calibration | FAIL | yes | 0.3388 | RMSPE <= 15.0 % on station mean speeds at 15-minute aggregation | common microsimulation practice (CLAUDE.md section 7.1), cited in the report |
| C6 | Bottlenecks | calibration | FAIL | yes | 1 | every observed bottleneck active for 30 min or more reproduced at the same or an adjacent station pair in at least 80.0 % of replicates (matched one to one), median activation within 15 min, duration and queue reach within tolerance, and phantom bottlenecks in at most half the replicates (protocol section 5) | [FlowState] protocol section 5 |
| C1 | Link flows | validation | FAIL | yes | 0.6 | GEH < 5 on >= 85.0 % of station-hour comparisons, hours anchored at the study period's start | [federal] FHWA TAT Vol. III 2004 (profile fhwa_default) |
| C2 | Link flows, Texas criterion | validation | FAIL | no | 0.425 | GEH < 3 on >= 100.0 % of station-hour comparisons, hours anchored at the study period's start | [federal] TxDOT TSAP ch. 13 (profile txdot_tsap_ch13); reported, not gating |
| C3 | Speeds | validation | FAIL | yes | 0.3876 | RMSPE <= 15.0 % on station mean speeds at 15-minute aggregation | common microsimulation practice (CLAUDE.md section 7.1), cited in the report |
| C6 | Bottlenecks | validation | PASS | yes | 0 | every observed bottleneck active for 30 min or more reproduced at the same or an adjacent station pair in at least 80.0 % of replicates (matched one to one), median activation within 15 min, duration and queue reach within tolerance, and phantom bottlenecks in at most half the replicates (protocol section 5) | [FlowState] protocol section 5 |
| C4 | Wave speed | calibration | FAIL | yes | 4.897 | simulated backward wave speed (stack detector) in 14-22 km/h with a backward front in at least 80.0 % of the replicates, or not applicable when fewer than 3 station pairs show a valid cross-correlation on the calibration days | empirical stop-and-go literature (flowstate_core.constants.WAVE_SPEED_BAND_KMH) |
| C5 | Collisions | all runs | PASS | yes | 0 | zero SUMO collisions in every run, each run recording the counter | [FlowState] CLAUDE.md section 3.3; owner decision 2026-10-04 |

## Findings

- Replicates: 20 seeded replicate(s); the protocol scores every check over at least 20.
- Day sets: the calibration-day artifact holds exactly the split's 5 calibration day(s) and the validation-day artifact its 4 validation day(s), none shared.
- Targets quality-masked: calibration days: runs/p1_rehearsal/dq/data_quality.json (sha256 8cae907dec40): 24 masked detector-day(s) (24 excluded whole), 912 reading(s) set aside; validation days: runs/p1_rehearsal/dq/data_quality.json (sha256 8cae907dec40): 19 masked detector-day(s) (19 excluded whole), 720 reading(s) set aside.
- Link flows, calibration days: GEH < 5 on 61.8 % of 840 station-hour comparisons pooled over 20 replicate(s) (per-replicate mean 61.8 %, 95 % interval 56.5 % to 67.0 %); the target is >= 85.0 %, short by 23.2 percentage points; hours anchored at the study period's start (1800 s).
- Link flows, Texas criterion, calibration days: GEH < 3 on 45.8 % of 840 station-hour comparisons pooled over 20 replicate(s) (per-replicate mean 61.8 %, 95 % interval 56.5 % to 67.0 %); the target is >= 100.0 %, short by 54.2 percentage points; hours anchored at the study period's start (1800 s).
- Speeds, calibration days: RMSPE of 15-minute station mean speeds 33.9 % (mean over 20 replicate(s), 95 % interval 31.3 % to 36.5 %; point speeds at the stations, as a loop reads them); the target is at most 15.0 %, over by 18.9 percentage points (diagnostics: 5 min 35.8 %; 60 min 28.7 %).
- Bottlenecks, calibration days: 0 observed bottleneck(s), 0 active for 30 min or more, compared with 20 replicate(s) (point speeds at the stations, as a loop reads them); rule no_phantom fails — 15 of 20 replicates (75%; limit 50%) contain a bottleneck active for more than 30 min away from every observed one (counted once per replicate); by station pair: S790→S97 in 15.
- Link flows, validation days: GEH < 5 on 60.0 % of 840 station-hour comparisons pooled over 20 replicate(s) (per-replicate mean 60.0 %, 95 % interval 54.0 % to 66.0 %); the target is >= 85.0 %, short by 25.0 percentage points; hours anchored at the study period's start (1800 s).
- Link flows, Texas criterion, validation days: GEH < 3 on 42.5 % of 840 station-hour comparisons pooled over 20 replicate(s) (per-replicate mean 60.0 %, 95 % interval 54.0 % to 66.0 %); the target is >= 100.0 %, short by 57.5 percentage points; hours anchored at the study period's start (1800 s).
- Speeds, validation days: RMSPE of 15-minute station mean speeds 38.8 % (mean over 20 replicate(s), 95 % interval 34.2 % to 43.4 %; point speeds at the stations, as a loop reads them); the target is at most 15.0 %, over by 23.8 percentage points (diagnostics: 5 min 42.1 %; 60 min 30.8 %).
- Bottlenecks, validation days: 1 observed bottleneck(s), 0 active for 30 min or more, compared with 20 replicate(s) (point speeds at the stations, as a loop reads them); all four rules hold.
- Wave speed: a backward front in 19 of 20 replicate(s) (95.0 %; at least 80.0 % needed, a replicate without a front counting as a miss); simulated backward wave speed 4.9 km/h, 95 % interval 4.6 to 5.2 km/h over those replicates (stack detector); the band is 14-22 km/h, outside it by 9.1 km/h; observed on the calibration days (detector cross-correlation): median 19.1 km/h from 7 of 13 station pairs.
- Collisions: none in 20 run(s).

## Why the gate failed

- Link flows, calibration days: GEH < 5 on 61.8 % of 840 station-hour comparisons pooled over 20 replicate(s) (per-replicate mean 61.8 %, 95 % interval 56.5 % to 67.0 %); the target is >= 85.0 %, short by 23.2 percentage points; hours anchored at the study period's start (1800 s).
- Speeds, calibration days: RMSPE of 15-minute station mean speeds 33.9 % (mean over 20 replicate(s), 95 % interval 31.3 % to 36.5 %; point speeds at the stations, as a loop reads them); the target is at most 15.0 %, over by 18.9 percentage points (diagnostics: 5 min 35.8 %; 60 min 28.7 %).
- Bottlenecks, calibration days: 0 observed bottleneck(s), 0 active for 30 min or more, compared with 20 replicate(s) (point speeds at the stations, as a loop reads them); rule no_phantom fails — 15 of 20 replicates (75%; limit 50%) contain a bottleneck active for more than 30 min away from every observed one (counted once per replicate); by station pair: S790→S97 in 15.
- Link flows, validation days: GEH < 5 on 60.0 % of 840 station-hour comparisons pooled over 20 replicate(s) (per-replicate mean 60.0 %, 95 % interval 54.0 % to 66.0 %); the target is >= 85.0 %, short by 25.0 percentage points; hours anchored at the study period's start (1800 s).
- Speeds, validation days: RMSPE of 15-minute station mean speeds 38.8 % (mean over 20 replicate(s), 95 % interval 34.2 % to 43.4 %; point speeds at the stations, as a loop reads them); the target is at most 15.0 %, over by 23.8 percentage points (diagnostics: 5 min 42.1 %; 60 min 30.8 %).
- Wave speed: a backward front in 19 of 20 replicate(s) (95.0 %; at least 80.0 % needed, a replicate without a front counting as a miss); simulated backward wave speed 4.9 km/h, 95 % interval 4.6 to 5.2 km/h over those replicates (stack detector); the band is 14-22 km/h, outside it by 9.1 km/h; observed on the calibration days (detector cross-correlation): median 19.1 km/h from 7 of 13 station pairs.

## Bottlenecks, calibration days

| Station pair | Activation | Active [min] | Queue reach | Reproduced | Median activation offset [min] | Median duration ratio |
|---|---|---|---|---|---|---|
| none observed | | | | | | |

- rule location: holds — no observed bottleneck was active for at least 30 min (0 activated in all); nothing to reproduce
- rule timing: holds — no observed bottleneck was active for at least 30 min (0 activated in all); nothing to reproduce
- rule queue_reach: holds — no observed bottleneck was active for at least 30 min (0 activated in all); nothing to reproduce
- rule no_phantom: FAILS — 15 of 20 replicates (75%; limit 50%) contain a bottleneck active for more than 30 min away from every observed one (counted once per replicate); by station pair: S790→S97 in 15

## Bottlenecks, validation days

| Station pair | Activation | Active [min] | Queue reach | Reproduced | Median activation offset [min] | Median duration ratio |
|---|---|---|---|---|---|---|
| S790→S97 | 06:30 | 25 | S1947 | — | — | — |

- rule location: holds — no observed bottleneck was active for at least 30 min (1 activated in all); nothing to reproduce
- rule timing: holds — no observed bottleneck was active for at least 30 min (1 activated in all); nothing to reproduce
- rule queue_reach: holds — no observed bottleneck was active for at least 30 min (1 activated in all); nothing to reproduce
- rule no_phantom: holds — 0 of 20 replicates (0%; limit 50%) contain a bottleneck active for more than 30 min away from every observed one (counted once per replicate); no simulated bottleneck away from the observed ones

## Calibration and validation days

- Calibration days: 2026-09-02, 2026-09-03, 2026-09-08, 2026-09-15, 2026-09-16
- Validation days: 2026-09-01, 2026-09-09, 2026-09-10, 2026-09-17
- Seed: 20261004; underpowered: no

## Excluded detectors

- 3240: S792 lane-3 loop chatters: 14 percent occupancy against 27-32 percent on its neighbours, 78 percent null night samples, count rises 775 veh/h into a bracket nothing enters (docs/ONBOARDING_MNDOT.md section 7 item 2, 2026-09-24)

## Notes

- simulated station speed = mean speed of the vehicles crossing the station's position in the window, each crossing interpolated between consecutive trajectory samples (observed_scores.json station_point_speeds_sim), as a loop detector there reads it (protocol section 5)
- C1 hours anchored at the study period's start (the warm-up's end), so the whole study period is scored (observed_scores.json link_hours_anchored; protocol section 4, C1)
