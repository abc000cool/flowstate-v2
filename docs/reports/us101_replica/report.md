# FlowState v2 — us101_replica validation report (NGSIM US-101 p1, measured downstream boundary)

Generated: 2026-09-26T01:44:23Z

## Provenance

Profile: `fhwa_default`. Seeds: 134183728835869882, 165503670820534583, 2378473973028931053, 3011106312394044631, 3747978530954135749, 3944094060050347669, 4910985839736976611, 5690692725577505498, 6134032994440706937, 6143473282319009404, 6538422657834023852, 661281422688282993, 677105600768189526, 6904272788004776631, 6914975401685141156, 6953598295321596746, 7382187975121682178, 8026499204807041784, 8557154790156791364, 887972120279483394.

Measurement window: each run's configured warm-up is discarded from every metric (warm-up per run, in seconds: 180). Travel times keep whole journeys that begin inside the window and are measured over [640, 1280] m. Fuel per vehicle-km remains a whole-run ratio unless the run records a post-warm-up fuel total.

Insertion: 51840 vehicles planned over 20 run(s), 51833 departed (1 of plan on average, lowest 1), 2312.3 arrived per run (over 20 of 20); verdict: ok.

| Run | Config hash | Seed | Tier | seeded | Wall time [s] |
|---|---|---|---|---|---|
| 134183728835869882 | `ab879e240aed` | 134183728835869882 | micro | seeded=False | 14.98 |
| 165503670820534583 | `ab879e240aed` | 165503670820534583 | micro | seeded=False | 17.45 |
| 2378473973028931053 | `ab879e240aed` | 2378473973028931053 | micro | seeded=False | 9.762 |
| 3011106312394044631 | `ab879e240aed` | 3011106312394044631 | micro | seeded=False | 12.04 |
| 3747978530954135749 | `ab879e240aed` | 3747978530954135749 | micro | seeded=False | 15.19 |
| 3944094060050347669 | `ab879e240aed` | 3944094060050347669 | micro | seeded=False | 10.59 |
| 4910985839736976611 | `ab879e240aed` | 4910985839736976611 | micro | seeded=False | 9.376 |
| 5690692725577505498 | `ab879e240aed` | 5690692725577505498 | micro | seeded=False | 10.69 |
| 6134032994440706937 | `ab879e240aed` | 6134032994440706937 | micro | seeded=False | 9.234 |
| 6143473282319009404 | `ab879e240aed` | 6143473282319009404 | micro | seeded=False | 14.15 |
| 6538422657834023852 | `ab879e240aed` | 6538422657834023852 | micro | seeded=False | 14.9 |
| 661281422688282993 | `ab879e240aed` | 661281422688282993 | micro | seeded=False | 14.71 |
| 677105600768189526 | `ab879e240aed` | 677105600768189526 | micro | seeded=False | 9.101 |
| 6904272788004776631 | `ab879e240aed` | 6904272788004776631 | micro | seeded=False | 13.85 |
| 6914975401685141156 | `ab879e240aed` | 6914975401685141156 | micro | seeded=False | 15.14 |
| 6953598295321596746 | `ab879e240aed` | 6953598295321596746 | micro | seeded=False | 12.88 |
| 7382187975121682178 | `ab879e240aed` | 7382187975121682178 | micro | seeded=False | 10.82 |
| 8026499204807041784 | `ab879e240aed` | 8026499204807041784 | micro | seeded=False | 9.47 |
| 8557154790156791364 | `ab879e240aed` | 8557154790156791364 | micro | seeded=False | 9.413 |
| 887972120279483394 | `ab879e240aed` | 887972120279483394 | micro | seeded=False | 9.765 |

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
| `artifacts/idm_us101.json` | `8578f4754b267ad09eed5b0b8b3e18c83b7d9036b7afdcec295d5d3f19195e22` |

## Acceptance criteria

| Criterion | Value | Threshold | Evaluated | Result |
|---|---|---|---|---|
| link_flows_geh | 0.5556 | GEH < 5 for >= 85% of link-hour comparisons | yes | FAIL — fraction of comparisons with GEH < 5 |
| speeds_rmspe | 0.3593 | segment-speed RMSPE <= 15% | yes | FAIL |
| wave_speed | NaN | backward wave speed in [14, 22] km/h (emergent, unseeded) | yes | FAIL — detector: stack: slant-stack peak of the two-way demeaned field on 15 s x 75 m bins over front speeds [-40, -2] km/h in 0.25 km/h steps, peak/median contrast >= 3, edge peaks rejected; no backward wave detected |
| ring_emergence | — | Sugiyama ring emergence benchmark reproduced | no | NOT EVALUATED — not evaluated: input not supplied |
| ring_dampening | — | Stern single-AV dampening benchmark reproduced | no | NOT EVALUATED — not evaluated: input not supplied |
| n_seeds | 20 | n_seeds >= 20 | yes | PASS |
| sensitivity_grid | — | penetration {1%, 2%, 5%, 10%, 15%, 20%} x compliance {25%, 50%, 80%, 100%} grid published with CIs | no | NOT EVALUATED — not evaluated: input not supplied |

A row marked not evaluated is never a pass: either no input was supplied,
or the value was measured with a recipe this profile does not accept (the
row's result says which). An unevaluated criterion counts as failing.

Wave-speed criterion input: mean over 0 of 20 unseeded replicate(s) of group baseline (`ab879e240aed`), measured with the stack detector on its own bins; 20 replicate(s) detected no backward front and are excluded from the mean.

## Metrics

Mean with two-sided t-distribution confidence bounds at the
95 percent level over n replicates. Rows flagged
underpowered have fewer than 20 replicates and must not be
quoted as headline results. Travel-time rows rest on the vehicles counted by
the n_travel_time_veh row, which are those that entered within the
measurement window and completed the span.

The wave_speed_kmh row is the metrics detector's diagnostic reading (standard); the acceptance criterion above is measured separately with the profile's stack detector on its own bins, so the two values can differ.

### baseline (`ab879e240aed`)

Replicates: n = 20 distinct seeds (134183728835869882, 165503670820534583, 2378473973028931053, 3011106312394044631, 3747978530954135749, 3944094060050347669, 4910985839736976611, 5690692725577505498, 6134032994440706937, 6143473282319009404, 6538422657834023852, 661281422688282993, 677105600768189526, 6904272788004776631, 6914975401685141156, 6953598295321596746, 7382187975121682178, 8026499204807041784, 8557154790156791364, 887972120279483394); replicate
criterion (n_seeds >= 20): PASS.

| Metric | Mean | Lower | Upper | n | Underpowered |
|---|---|---|---|---|---|
| throughput_veh_h | 8027 | 8008 | 8046 | 20 | no |
| mean_tt_s | 52.32 | 51.32 | 53.33 | 20 | no |
| p90_tt_s | 75.17 | 72.99 | 77.34 | 20 | no |
| sigma_v_spatial_ms | 3.748 | 3.66 | 3.836 | 20 | no |
| sigma_v_temporal_ms | 2.952 | 2.849 | 3.055 | 20 | no |
| vmt_veh_km | 3142 | 3138 | 3146 | 20 | no |
| vht_veh_h | 65.7 | 65.01 | 66.39 | 20 | no |
| fuel_ml_per_veh_km | 66.9 | 66.48 | 67.32 | 20 | no |
| wave_count | 2.15 | 1.618 | 2.682 | 20 | no |
| wave_speed_kmh | 7.132 | 6.632 | 7.632 | 20 | no |
| wave_amplitude_ms | 8.82 | 7.83 | 9.81 | 20 | no |
| n_travel_time_veh | 1919 | 1915 | 1923 | 20 | no |


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
- Measurement window: each run's configured warm-up is discarded from every metric (warm-up per run, in seconds: 180). Travel times keep whole journeys that begin inside the window and are measured over [640, 1280] m. Fuel per vehicle-km remains a whole-run ratio unless the run records a post-warm-up fuel total.
