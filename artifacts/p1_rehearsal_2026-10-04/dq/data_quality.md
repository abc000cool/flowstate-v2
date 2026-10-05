# Detector data quality

- **Inputs**: data/mndot/mndot_i94_wb_stpaul/detectors.csv
- **Data hash (sha256 of the loaded rows)**: ce44356d7a26cd24
- **Code**: 75a54b7ca45a
- **Created**: 2026-10-05T03:15:46Z

## Summary

27 detector stations over 9 day(s) (05:30–09:30 local, 5-minute readings): 243 detector-days.

- **187** detector-days passed every check, **13** are usable with caveats (the readings a finding names are set aside), **43** were dropped.
- The detectors delivered 95.8% of the readings the period should contain; after the checks, **81.8%** are usable.
- Nothing was filled in: a dropped or set-aside reading is simply absent, and no missing value was estimated (imputation is out of scope).

## Detectors dropped (on at least one day)

| Detector | Kind | Days dropped | Why |
|---|---|---|---|
| rnd_87205 | off_ramp | 7 of 9 | impossible (6 days), day_outlier (1 day). Example: physically impossible readings: flow above 3,000 veh/h per lane in 14 (max 4,332) window(s) — 29.2% of the day's readings |
| rnd_87209 | off_ramp | 9 of 9 | missing (9 days). Example: 100% of the day's 48 readings are missing (limit 50%) |
| rnd_88823 | on_ramp | 9 of 9 | zero_day (9 days). Example: no vehicle counted in 4 h of readings — a dead detector, or a closure (the counts cannot tell them apart; a closure must be declared) |
| rnd_88827 | on_ramp | 9 of 9 | day_outlier (8 days), zero_day (1 day). Example: the day's volume is 1.95x this detector's median day (corridor-wide day factor 1.03) — a lost or doubled lane, a recalibration, or a local event |
| rnd_88835 | off_ramp | 9 of 9 | zero_day (9 days). Example: no vehicle counted in 4 h of readings — a dead detector, or a closure (the counts cannot tell them apart; a closure must be declared) |

## Detectors kept with caveats

| Detector | Kind | Days with caveats | Findings |
|---|---|---|---|
| S1063 | mainline | 3 of 9 | missing (3 days) |
| S790 | mainline | 3 of 9 | mass_balance_pattern (3 days) |
| rnd_88811 | off_ramp | 2 of 9 | day_outlier (2 days) |
| rnd_88817 | off_ramp | 3 of 9 | day_outlier (3 days) |
| rnd_88833 | off_ramp | 1 of 9 | day_outlier (1 day) |

## Station-to-station balance

Between two neighbouring stations, the vehicles counted downstream should equal those counted upstream plus the entrances minus the exits in between, within the detectors' stated accuracy (±5% each). A day whose imbalance leaves that band with every ramp measured points at a faulty detector or a ramp missing from the inventory.

| Segment | Length | Ramps | Days judged | Days unexplained | Median residual |
|---|---|---|---|---|---|
| S1063→S1064 | 1,213 m | rnd_88835 (off_ramp, unmeasured) | 9 | 0 | -11.5% |
| S1064→S1065 | 1,150 m | rnd_88833 (off_ramp, measured); rnd_88831 (on_ramp, measured) | 9 | 9 **persistent** | -15.5% |
| S1065→S1066 | 584 m | rnd_88827 (on_ramp, unmeasured) | 9 | 0 | +16.7% |
| S1066→S1067 | 908 m | rnd_88823 (on_ramp, unmeasured) | 9 | 0 | +12.6% |
| S1067→S1068 | 608 m | rnd_88819 (on_ramp, measured); rnd_88817 (off_ramp, measured) | 9 | 0 | +2.4% |
| S1068→S1947 | 491 m | none | 9 | 9 **persistent** | +14.5% |
| S1947→S1069 | 604 m | rnd_88811 (off_ramp, measured) | 9 | 0 | -3.1% |
| S1069→S1070 | 979 m | rnd_88807 (on_ramp, measured) | 9 | 1 | -10.3% |
| S1070→S1948 | 725 m | none | 9 | 0 | +1.3% |
| S1948→S792 | 565 m | rnd_87205 (off_ramp, measured) | 9 | 2 | -15.5% |
| S792→S791 | 743 m | rnd_87209 (off_ramp, unmeasured) | 9 | 1 | +6.4% |
| S791→S790 | 400 m | none | 9 | 9 **persistent** | +13.9% |
| S790→S97 | 940 m | rnd_91040 (on_ramp, measured); rnd_87221 (off_ramp, measured) | 9 | 9 **persistent** | -26.5% |

Stations named as the likely cause of opposite residuals:

- S790 on 2026-09-08: overcounts (+526 veh/h above, -964 veh/h below)
- S790 on 2026-09-10: overcounts (+582 veh/h above, -1,153 veh/h below)
- S790 on 2026-09-17: overcounts (+558 veh/h above, -1,109 veh/h below)

## Unusual days across the corridor

None found.

## What was checked

- **missing**: share of the day's readings that are missing
- **impossible**: physically impossible readings: negative flow, flow above 3,000 veh/h per lane, occupancy outside 0-100 %, speed below 0 or above 160 km/h
- **stuck_flow**: the count repeats one value for 30 min or more where 10+ vehicles pass
- **stuck_occupancy**: occupancy repeats one value (2 % or more) for 2 h or more
- **stuck_speed**: speed repeats one value for 2 h or more
- **zero_run**: no vehicle counted and the loop empty (occupancy at most 1 %, or unreported with no congestion next to it) for a stretch in which this detector usually counts 20 or more (cf. Chen et al. 2003, S1: occupancy = 0)
- **zero_day**: no vehicle counted in three or more hours of readings
- **low_flow**: mean flow below 30 veh/h over three or more hours of readings
- **zero_flow_occupied**: the loop is occupied 5 s or more in a window with no vehicle counted, and neither a standstill (at least 50 % occupancy for at most 5 min) nor congestion at a neighbouring lane or station within 5 min explains it (cf. Chen et al. 2003, S2)
- **flow_without_occupancy**: occupancy reads 0 while 600+ veh/h per lane pass
- **implied_length**: flow, occupancy and speed imply a mean vehicle length outside 2.5-25 m
- **day_outlier**: the day's volume against the detector's median day, with the corridor-wide day factor divided out
- **lane_imbalance**: a lane's flow against the mean of the station's other lanes
- **mass_balance_pattern**: the segments either side of this station have opposite, similar residuals

Day-level judgement follows the PeMS Daily Statistics Algorithm (Chen, Kwon, Rice, Skabardonis & Varaiya 2003, TRR 1855); its thresholds were set for 30-second samples and are not reused. Every threshold here is a named constant with a stated reason:

| Threshold | Value | Source or reason |
|---|---|---|
| missing_suspect_share | 0.2 | convention: complement of the MnDOT loader's 80 % window rule |
| missing_exclude_share | 0.5 | convention: more missing than present |
| flow_ceiling_veh_h_lane | 3000 | 1.2 s mean headway; a quarter above HCM basic-freeway capacity |
| speed_ceiling_ms | 44.4444 | 160 km/h: above any US posted limit (85 mph) for a window mean |
| occupancy_range_pct | 0, 100 | definition of occupancy |
| impossible_exclude_share | 0.05 | convention: one window in twenty |
| stuck_flow_run_s | 1800 | Poisson: 30 min of identical counts (mean >= 10) does not occur |
| stuck_min_count_veh | 10 | Poisson: consecutive agreement <= 0.09 at mean >= 10 |
| stuck_single_run_s | 7200 | beyond integer rounding of occupancy/speed (cf. Chen et al. 2003 S4) |
| stuck_min_occupancy_pct | 2 | integer-rounding floor of light traffic |
| zero_run_min_expected_veh | 20 | Poisson e^-20 against the detector's own profile |
| min_profile_days | 3 | a median of < 3 values is not robust |
| day_judged_min_valid_s | 10800 | 3 h of all-zero readings is a dead detector or a closure |
| low_flow_veh_h | 30 | same as calibration.onboarding.DEFAULT_ALIVE_VEH_H |
| empty_loop_occupancy_pct | 1 | an empty loop reads 0 % (Chen et al. 2003 S1: occupancy = 0); 1 % for rounding and a boundary spill |
| zero_flow_min_occupied_s | 5 | beyond a boundary spill of one vehicle (cf. S2) |
| standstill_min_occupancy_pct | 50 | moving traffic covering the loop half the time is counted (q = o v / L) |
| standstill_max_s | 300 | a 1.7 km standing jam passing at about 20 km/h (CLAUDE.md 7.1) |
| congested_occupancy_pct | 20 | past capacity: 16 % at 2,200 veh/h/lane, 25 m/s, 6.5 m |
| congested_speed_ms | 11.1111 | 40 km/h: CLAUDE.md 7.2 jam threshold (calibration.conservation) |
| queue_context_s | 300 | a jam front moves about 20 km/h: 1.7 km in 5 min (CLAUDE.md 7.1) |
| zero_flow_occupied_suspect_share | 0.01 | convention: one unexplained window in a hundred |
| occupancy_floor_flow_veh_h_lane | 600 | occupancy >= 1.5 % at 600 veh/h/lane, 45 m/s, 4 m |
| implied_length_band_m | 2.5, 25 | shorter than any vehicle / longer than a tractor-trailer |
| implied_length_min_count_veh | 10 | below 10 vehicles the mean length is chance |
| implied_length_min_tested_s | 3600 | one hour of tested windows |
| implied_length_suspect_share | 0.1 | convention |
| implied_length_exclude_share | 0.5 | most windows inconsistent: a quantity is wrong |
| window_flag_exclude_share | 0.1 | convention: 2.4 h of a full day |
| day_ratio_suspect_band | 0.8, 1.25 | convention: +-20 % (a lane at a station of <= 5 lanes) |
| day_ratio_exclude_band | 0.6, 1.66667 | convention: -40 % / +67 % |
| day_min_compared_share | 0.5 | half the day comparable |
| min_sensors_for_corridor_factor | 3 | a median of < 3 sensors is not robust |
| lane_ratio_suspect_band | 0.5, 2 | convention: lanes within a factor 2 at moderate flow |
| lane_ratio_exclude_band | 0.333333, 3 | convention: a factor 3 |
| lane_min_flow_veh_h_lane | 600 | light traffic uses lanes unevenly by choice |
| lane_min_tested_s | 3600 | one hour of tested windows |
| mass_balance_min_evaluated_share | 0.5 | half the day's periods complete |
| mass_balance_persistent_share | 0.5 | half the evaluated days |
| attribution_ratio_band | 0.5, 2 | a miscount shifts both neighbours by the same amount |

## Notes

- station totals only: the lane-imbalance check needs per-lane data

## Limits

- A road or ramp closure reads exactly like a dead detector; closures must be declared, the counts cannot reveal them.
- Detector accuracy (±5%) is an assumption, not a measurement of these detectors.
- The station balance includes the change in the number of vehicles between the stations, which cancels over a day that starts and ends in free flow but not over a span that ends inside a queue.
