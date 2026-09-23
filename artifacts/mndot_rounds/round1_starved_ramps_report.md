# FlowState calibration & validation report

Generated: 2026-09-23T06:06:16Z

## Provenance

Profile: `fhwa_tat3_2004`. Seeds: 134183728835869882, 165503670820534583, 2378473973028931053, 3011106312394044631, 3747978530954135749, 3944094060050347669, 4910985839736976611, 5690692725577505498, 6134032994440706937, 6143473282319009404, 6538422657834023852, 661281422688282993, 677105600768189526, 6904272788004776631, 6914975401685141156, 6953598295321596746, 7382187975121682178, 8026499204807041784, 8557154790156791364, 887972120279483394.

Measurement window: each run's configured warm-up is discarded from every metric (warm-up per run, in seconds: 1800). Travel times keep whole journeys that begin inside the window and are measured over [1109.5, 11027] m. Fuel per vehicle-km remains a whole-run ratio unless the run records a post-warm-up fuel total.

| Run | Config hash | Seed | Tier | seeded | Wall time [s] |
|---|---|---|---|---|---|
| adfb118b0015/134183728835869882 | `adfb118b0015` | 134183728835869882 | micro | seeded=False | 349.3 |
| adfb118b0015/165503670820534583 | `adfb118b0015` | 165503670820534583 | micro | seeded=False | 469 |
| adfb118b0015/2378473973028931053 | `adfb118b0015` | 2378473973028931053 | micro | seeded=False | 467.1 |
| adfb118b0015/3011106312394044631 | `adfb118b0015` | 3011106312394044631 | micro | seeded=False | 349.7 |
| adfb118b0015/3747978530954135749 | `adfb118b0015` | 3747978530954135749 | micro | seeded=False | 349.4 |
| adfb118b0015/3944094060050347669 | `adfb118b0015` | 3944094060050347669 | micro | seeded=False | 469.3 |
| adfb118b0015/4910985839736976611 | `adfb118b0015` | 4910985839736976611 | micro | seeded=False | 352.8 |
| adfb118b0015/5690692725577505498 | `adfb118b0015` | 5690692725577505498 | micro | seeded=False | 469.8 |
| adfb118b0015/6134032994440706937 | `adfb118b0015` | 6134032994440706937 | micro | seeded=False | 468.4 |
| adfb118b0015/6143473282319009404 | `adfb118b0015` | 6143473282319009404 | micro | seeded=False | 348.1 |
| adfb118b0015/6538422657834023852 | `adfb118b0015` | 6538422657834023852 | micro | seeded=False | 348.5 |
| adfb118b0015/661281422688282993 | `adfb118b0015` | 661281422688282993 | micro | seeded=False | 465.9 |
| adfb118b0015/677105600768189526 | `adfb118b0015` | 677105600768189526 | micro | seeded=False | 349.5 |
| adfb118b0015/6904272788004776631 | `adfb118b0015` | 6904272788004776631 | micro | seeded=False | 348.2 |
| adfb118b0015/6914975401685141156 | `adfb118b0015` | 6914975401685141156 | micro | seeded=False | 344.1 |
| adfb118b0015/6953598295321596746 | `adfb118b0015` | 6953598295321596746 | micro | seeded=False | 350.5 |
| adfb118b0015/7382187975121682178 | `adfb118b0015` | 7382187975121682178 | micro | seeded=False | 463.3 |
| adfb118b0015/8026499204807041784 | `adfb118b0015` | 8026499204807041784 | micro | seeded=False | 348.6 |
| adfb118b0015/8557154790156791364 | `adfb118b0015` | 8557154790156791364 | micro | seeded=False | 467.1 |
| adfb118b0015/887972120279483394 | `adfb118b0015` | 887972120279483394 | micro | seeded=False | 351.7 |

### Package versions (from run metadata)

- eclipse-sumo: `1.27.1`
- flowstate_core: `2.0.0`
- libsumo: `1.27.1`
- microsim: `2.0.0`
- numpy: `2.5.2`
- pandas: `3.0.5`
- pyarrow: `25.0.1`
- python: `3.12.14`

### Calibration artifacts used

| Artifact | data_hash |
|---|---|
| `artifacts/idm_i24_capacity.json` | `aa97dd93d2bf250ea23c3624f91afe8d7035a672fd13efbd53013709e12c7d56` |

### Observed data

The link-flow and segment-speed criteria are scored against this observed artifact: hourly volumes formed from the fully observed windows of each hour at every mainline station, and mean speeds per station segment (each station owns the span to the midpoints with its neighbours). Simulation time zero is the artifact's local start time; the run's warm-up, and any window the run does not cover to its end, are excluded, and a station-window the detector did not measure is skipped, never imputed — the coverage rows say how much of the grid was compared.

| Field | Value |
|---|---|
| artifact | data/mndot/mndot_i94_wb_stpaul/observations.json |
| corridor | I-94 WB |
| source provider | MnDOT RTMC Mayfly API |
| source dates | 20260901, 20260902, 20260903, 20260908, 20260909, 20260910, 20260915, 20260916, 20260917 |
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

## Acceptance criteria

| Criterion | Value | Threshold | Evaluated | Result |
|---|---|---|---|---|
| link_flows_geh | 0.1667 | GEH < 5 for > 85% of link-hour comparisons | yes | FAIL — fraction of comparisons with GEH < 5 |
| wave_speed | NaN | backward wave speed in [14, 22] km/h (emergent, unseeded) | yes | FAIL — detector: stack: slant-stack peak of the two-way demeaned field on 15 s x 75 m bins over front speeds [-40, -2] km/h in 0.25 km/h steps, peak/median contrast >= 3, edge peaks rejected; no backward wave detected |
| ring_emergence | — | Sugiyama ring emergence benchmark reproduced | no | NOT EVALUATED — not evaluated: input not supplied |
| ring_dampening | — | Stern single-AV dampening benchmark reproduced | no | NOT EVALUATED — not evaluated: input not supplied |
| n_seeds | 20 | n_seeds >= 20 | yes | PASS |
| sensitivity_grid | — | penetration {1%, 2%, 5%, 10%, 15%, 20%} x compliance {25%, 50%, 80%, 100%} grid published with CIs | no | NOT EVALUATED — not evaluated: input not supplied |

A row marked not evaluated is never a pass: either no input was supplied,
or the value was measured with a recipe this profile does not accept (the
row's result says which). An unevaluated criterion counts as failing.

Wave-speed criterion input: mean over 0 of 20 unseeded replicate(s) of group baseline (`adfb118b0015`), measured with the stack detector on its own bins; 20 replicate(s) detected no backward front and are excluded from the mean.

### Speed criterion by time aggregation

The segment-speed criterion compares a replicate mean with one recorded
day. The floor column is the recorded field against its own three-window
moving average: below that resolution the day does not repeat itself, so
no ensemble mean can score under the floor. The criterion row keeps its
native resolution; this table says how much of its value is resolution.

| Aggregation | RMSPE, replicate mean vs observed | Floor: observed vs its own three-window average |
|---|---|---|
| 5 min (criterion) | 0.9287 | 0.02859 |
| 15 min | 0.9126 | 0.06798 |
| 30 min | 0.8883 |  |
| 60 min | 0.8152 |  |
| whole period | 0.4667 |  |

## Metrics

Mean with two-sided t-distribution confidence bounds at the
95 percent level over n replicates. Rows flagged
underpowered have fewer than 20 replicates and must not be
quoted as headline results. Travel-time rows rest on the vehicles counted by
the n_travel_time_veh row, which are those that entered within the
measurement window and completed the span.

The wave_speed_kmh row is the metrics detector's diagnostic reading (standard); the acceptance criterion above is measured separately with the profile's stack detector on its own bins, so the two values can differ.

### baseline (`adfb118b0015`)

Replicates: n = 20 distinct seeds (134183728835869882, 165503670820534583, 2378473973028931053, 3011106312394044631, 3747978530954135749, 3944094060050347669, 4910985839736976611, 5690692725577505498, 6134032994440706937, 6143473282319009404, 6538422657834023852, 661281422688282993, 677105600768189526, 6904272788004776631, 6914975401685141156, 6953598295321596746, 7382187975121682178, 8026499204807041784, 8557154790156791364, 887972120279483394); replicate
criterion (n_seeds >= 20): PASS.

| Metric | Mean | Lower | Upper | n | Underpowered |
|---|---|---|---|---|---|
| throughput_veh_h | 1805 | 1798 | 1812 | 20 | no |
| mean_tt_s | 197.6 | 196.8 | 198.3 | 20 | no |
| p90_tt_s | 424.7 | 424 | 425.3 | 20 | no |
| sigma_v_spatial_ms | 2.758 | 2.713 | 2.804 | 20 | no |
| sigma_v_temporal_ms | 2.042 | 2.014 | 2.071 | 20 | no |
| vmt_veh_km | 1.058e+05 | 1.056e+05 | 1.06e+05 | 20 | no |
| vht_veh_h | 1288 | 1286 | 1291 | 20 | no |
| fuel_ml_per_veh_km | 86.14 | 86.05 | 86.24 | 20 | no |
| wave_count | 25.75 | 23.09 | 28.41 | 20 | no |
| wave_speed_kmh | 6.739 | 6.436 | 7.042 | 20 | no |
| wave_amplitude_ms | 17.42 | 17.3 | 17.54 | 20 | no |
| n_travel_time_veh | 9131 | 9102 | 9161 | 20 | no |

## Speed contours

![Space-time mean-speed contour, seed 134183728835869882](speed_contour_00_seed_134183728835869882.png)
![Space-time mean-speed contour, seed 165503670820534583](speed_contour_01_seed_165503670820534583.png)
![Space-time mean-speed contour, seed 2378473973028931053](speed_contour_02_seed_2378473973028931053.png)
![Space-time mean-speed contour, seed 3011106312394044631](speed_contour_03_seed_3011106312394044631.png)
![Space-time mean-speed contour, seed 3747978530954135749](speed_contour_04_seed_3747978530954135749.png)
![Space-time mean-speed contour, seed 3944094060050347669](speed_contour_05_seed_3944094060050347669.png)
![Space-time mean-speed contour, seed 4910985839736976611](speed_contour_06_seed_4910985839736976611.png)
![Space-time mean-speed contour, seed 5690692725577505498](speed_contour_07_seed_5690692725577505498.png)
![Space-time mean-speed contour, seed 6134032994440706937](speed_contour_08_seed_6134032994440706937.png)
![Space-time mean-speed contour, seed 6143473282319009404](speed_contour_09_seed_6143473282319009404.png)
![Space-time mean-speed contour, seed 6538422657834023852](speed_contour_10_seed_6538422657834023852.png)
![Space-time mean-speed contour, seed 661281422688282993](speed_contour_11_seed_661281422688282993.png)
![Space-time mean-speed contour, seed 677105600768189526](speed_contour_12_seed_677105600768189526.png)
![Space-time mean-speed contour, seed 6904272788004776631](speed_contour_13_seed_6904272788004776631.png)
![Space-time mean-speed contour, seed 6914975401685141156](speed_contour_14_seed_6914975401685141156.png)
![Space-time mean-speed contour, seed 6953598295321596746](speed_contour_15_seed_6953598295321596746.png)
![Space-time mean-speed contour, seed 7382187975121682178](speed_contour_16_seed_7382187975121682178.png)
![Space-time mean-speed contour, seed 8026499204807041784](speed_contour_17_seed_8026499204807041784.png)
![Space-time mean-speed contour, seed 8557154790156791364](speed_contour_18_seed_8557154790156791364.png)
![Space-time mean-speed contour, seed 887972120279483394](speed_contour_19_seed_887972120279483394.png)

## Limitations

- Results cover a single corridor/scenario family; transfer to other
  corridors is not established.
- Model-form uncertainty (IDM/SUMO car-following assumptions, effective
  single-pipe demand representation) is not captured by seed-to-seed
  confidence intervals.
- AV penetration and compliance are swept assumptions, not measured
  behavior; conclusions hold only within the swept grid.
- Metrics from fewer than 20 replicates are flagged
  underpowered and are diagnostics, not claims.
- Measurement window: each run's configured warm-up is discarded from every metric (warm-up per run, in seconds: 1800). Travel times keep whole journeys that begin inside the window and are measured over [1109.5, 11027] m. Fuel per vehicle-km remains a whole-run ratio unless the run records a post-warm-up fuel total.
- The observed comparison covers the artifact's stations, windows and days
  only; station-windows the detectors did not measure are excluded from both
  criteria rather than filled in, so the coverage rows bound how much of the
  corridor and period the two rows actually speak for.
