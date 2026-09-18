# FlowState v2 — i24_replica validation report, speedcal arm: coverage-shaped demand at the fitted level (I-24 MOTION westbound, 30 Nov 2022 06:30-08:30 CST, measured downstream boundary)

Generated: 2026-09-17T22:43:17Z

## Provenance

Profile: `fhwa_default`. Seeds: 134183728835869882, 165503670820534583, 2378473973028931053, 3011106312394044631, 3747978530954135749, 3944094060050347669, 4910985839736976611, 5690692725577505498, 6134032994440706937, 6143473282319009404, 6538422657834023852, 661281422688282993, 677105600768189526, 6904272788004776631, 6914975401685141156, 6953598295321596746, 7382187975121682178, 8026499204807041784, 8557154790156791364, 887972120279483394.

Measurement window: each run's configured warm-up is discarded from every metric (warm-up per run, in seconds: 600). Travel times keep whole journeys that begin inside the window and are measured over [2256.22, 7637.83] m. Fuel per vehicle-km remains a whole-run ratio unless the run records a post-warm-up fuel total.

| Run | Config hash | Seed | Tier | seeded | Wall time [s] |
|---|---|---|---|---|---|
| 134183728835869882 | `43def6306dd6` | 134183728835869882 | micro | seeded=False | 514.3 |
| 165503670820534583 | `43def6306dd6` | 165503670820534583 | micro | seeded=False | 525 |
| 2378473973028931053 | `43def6306dd6` | 2378473973028931053 | micro | seeded=False | 491.8 |
| 3011106312394044631 | `43def6306dd6` | 3011106312394044631 | micro | seeded=False | 499.5 |
| 3747978530954135749 | `43def6306dd6` | 3747978530954135749 | micro | seeded=False | 529.2 |
| 3944094060050347669 | `43def6306dd6` | 3944094060050347669 | micro | seeded=False | 541.8 |
| 4910985839736976611 | `43def6306dd6` | 4910985839736976611 | micro | seeded=False | 678.9 |
| 5690692725577505498 | `43def6306dd6` | 5690692725577505498 | micro | seeded=False | 518.7 |
| 6134032994440706937 | `43def6306dd6` | 6134032994440706937 | micro | seeded=False | 504.1 |
| 6143473282319009404 | `43def6306dd6` | 6143473282319009404 | micro | seeded=False | 530.7 |
| 6538422657834023852 | `43def6306dd6` | 6538422657834023852 | micro | seeded=False | 686.9 |
| 661281422688282993 | `43def6306dd6` | 661281422688282993 | micro | seeded=False | 530.4 |
| 677105600768189526 | `43def6306dd6` | 677105600768189526 | micro | seeded=False | 522.6 |
| 6904272788004776631 | `43def6306dd6` | 6904272788004776631 | micro | seeded=False | 697.6 |
| 6914975401685141156 | `43def6306dd6` | 6914975401685141156 | micro | seeded=False | 698.5 |
| 6953598295321596746 | `43def6306dd6` | 6953598295321596746 | micro | seeded=False | 693.1 |
| 7382187975121682178 | `43def6306dd6` | 7382187975121682178 | micro | seeded=False | 673.5 |
| 8026499204807041784 | `43def6306dd6` | 8026499204807041784 | micro | seeded=False | 686.6 |
| 8557154790156791364 | `43def6306dd6` | 8557154790156791364 | micro | seeded=False | 543.8 |
| 887972120279483394 | `43def6306dd6` | 887972120279483394 | micro | seeded=False | 696.6 |

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

## Acceptance criteria

| Criterion | Value | Threshold | Evaluated | Result |
|---|---|---|---|---|
| link_flows_geh | 0.1875 | GEH < 5 for >= 85% of link-hour comparisons | yes | FAIL — fraction of comparisons with GEH < 5 |
| speeds_rmspe | 0.3595 | segment-speed RMSPE <= 15% | yes | FAIL |
| wave_speed | 15.82 | backward wave speed in [14, 22] km/h (emergent, unseeded) | yes | PASS — detector: stack: slant-stack peak of the two-way demeaned field on 15 s x 75 m bins over front speeds [-40, -2] km/h in 0.25 km/h steps, peak/median contrast >= 3, edge peaks rejected |
| ring_emergence | — | Sugiyama ring emergence benchmark reproduced | no | NOT EVALUATED — not evaluated: input not supplied |
| ring_dampening | — | Stern single-AV dampening benchmark reproduced | no | NOT EVALUATED — not evaluated: input not supplied |
| n_seeds | 20 | n_seeds >= 20 | yes | PASS |
| sensitivity_grid | — | penetration {1%, 2%, 5%, 10%, 15%, 20%} x compliance {25%, 50%, 80%, 100%} grid published with CIs | no | NOT EVALUATED — not evaluated: input not supplied |

A row marked not evaluated is never a pass: either no input was supplied,
or the value was measured with a recipe this profile does not accept (the
row's result says which). An unevaluated criterion counts as failing.

Wave-speed criterion input: mean over 19 of 20 unseeded replicate(s) of group baseline (`43def6306dd6`), measured with the stack detector on its own bins; 1 replicate(s) detected no backward front and are excluded from the mean; fewer than 20 contributing replicate(s) — underpowered, not a headline value.

### Speed criterion by time aggregation

The segment-speed criterion compares a replicate mean with one recorded
day. The floor column is the recorded field against its own three-window
moving average: below that resolution the day does not repeat itself, so
no ensemble mean can score under the floor. The criterion row keeps its
native resolution; this table says how much of its value is resolution.

| Aggregation | RMSPE, replicate mean vs observed | Floor: observed vs its own three-window average |
|---|---|---|
| 5 min (criterion) | 0.3595 | 0.334 |
| 15 min | 0.2634 | 0.1512 |
| 30 min | 0.2336 |  |
| 60 min | 0.1955 |  |
| whole period | 0.1892 |  |

## Metrics

Mean with two-sided t-distribution confidence bounds at the
95 percent level over n replicates. Rows flagged
underpowered have fewer than 20 replicates and must not be
quoted as headline results. Travel-time rows rest on the vehicles counted by
the n_travel_time_veh row, which are those that entered within the
measurement window and completed the span.

The wave_speed_kmh row is the metrics detector's diagnostic reading (standard); the acceptance criterion above is measured separately with the profile's stack detector on its own bins, so the two values can differ.

### baseline (`43def6306dd6`)

Replicates: n = 20 distinct seeds (134183728835869882, 165503670820534583, 2378473973028931053, 3011106312394044631, 3747978530954135749, 3944094060050347669, 4910985839736976611, 5690692725577505498, 6134032994440706937, 6143473282319009404, 6538422657834023852, 661281422688282993, 677105600768189526, 6904272788004776631, 6914975401685141156, 6953598295321596746, 7382187975121682178, 8026499204807041784, 8557154790156791364, 887972120279483394); replicate
criterion (n_seeds >= 20): PASS.

| Metric | Mean | Lower | Upper | n | Underpowered |
|---|---|---|---|---|---|
| throughput_veh_h | 5839 | 5808 | 5870 | 20 | no |
| mean_tt_s | 589.9 | 581.8 | 598 | 20 | no |
| p90_tt_s | 896.5 | 880 | 912.9 | 20 | no |
| sigma_v_spatial_ms | 5.704 | 5.67 | 5.739 | 20 | no |
| sigma_v_temporal_ms | 4.942 | 4.918 | 4.966 | 20 | no |
| vmt_veh_km | 9.354e+04 | 9.307e+04 | 9.401e+04 | 20 | no |
| vht_veh_h | 3365 | 3332 | 3399 | 20 | no |
| fuel_ml_per_veh_km | 100.7 | 99.7 | 101.6 | 20 | no |
| wave_count | 12.9 | 11.26 | 14.54 | 20 | no |
| wave_speed_kmh | 10.18 | 9.514 | 10.85 | 20 | no |
| wave_amplitude_ms | 6.692 | 6.285 | 7.098 | 20 | no |
| n_travel_time_veh | 1.029e+04 | 1.023e+04 | 1.034e+04 | 20 | no |

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
- Measurement window: each run's configured warm-up is discarded from every metric (warm-up per run, in seconds: 600). Travel times keep whole journeys that begin inside the window and are measured over [2256.22, 7637.83] m. Fuel per vehicle-km remains a whole-run ratio unless the run records a post-warm-up fuel total.
