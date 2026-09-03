# FlowState v2 — i24_replica validation report, tracked arm: demand as tracked (lower bound at the instrument's coverage) (I-24 MOTION westbound, 30 Nov 2022 06:30-08:30 CST, measured downstream boundary)

Generated: 2026-09-03T00:28:40Z

## Provenance

Profile: `fhwa_default`. Seeds: 134183728835869882, 165503670820534583, 2378473973028931053, 3011106312394044631, 3747978530954135749, 3944094060050347669, 4910985839736976611, 5690692725577505498, 6134032994440706937, 6143473282319009404, 6538422657834023852, 661281422688282993, 677105600768189526, 6904272788004776631, 6914975401685141156, 6953598295321596746, 7382187975121682178, 8026499204807041784, 8557154790156791364, 887972120279483394.

| Run | Config hash | Seed | Tier | seeded | Wall time [s] |
|---|---|---|---|---|---|
| 134183728835869882 | `5e15ca999c19` | 134183728835869882 | micro | seeded=False | 227.1 |
| 165503670820534583 | `5e15ca999c19` | 165503670820534583 | micro | seeded=False | 212.9 |
| 2378473973028931053 | `5e15ca999c19` | 2378473973028931053 | micro | seeded=False | 243.4 |
| 3011106312394044631 | `5e15ca999c19` | 3011106312394044631 | micro | seeded=False | 244.6 |
| 3747978530954135749 | `5e15ca999c19` | 3747978530954135749 | micro | seeded=False | 244.8 |
| 3944094060050347669 | `5e15ca999c19` | 3944094060050347669 | micro | seeded=False | 205.3 |
| 4910985839736976611 | `5e15ca999c19` | 4910985839736976611 | micro | seeded=False | 219.1 |
| 5690692725577505498 | `5e15ca999c19` | 5690692725577505498 | micro | seeded=False | 155.8 |
| 6134032994440706937 | `5e15ca999c19` | 6134032994440706937 | micro | seeded=False | 227.5 |
| 6143473282319009404 | `5e15ca999c19` | 6143473282319009404 | micro | seeded=False | 222.2 |
| 6538422657834023852 | `5e15ca999c19` | 6538422657834023852 | micro | seeded=False | 205.2 |
| 661281422688282993 | `5e15ca999c19` | 661281422688282993 | micro | seeded=False | 235.6 |
| 677105600768189526 | `5e15ca999c19` | 677105600768189526 | micro | seeded=False | 191.8 |
| 6904272788004776631 | `5e15ca999c19` | 6904272788004776631 | micro | seeded=False | 221.9 |
| 6914975401685141156 | `5e15ca999c19` | 6914975401685141156 | micro | seeded=False | 221.3 |
| 6953598295321596746 | `5e15ca999c19` | 6953598295321596746 | micro | seeded=False | 244.3 |
| 7382187975121682178 | `5e15ca999c19` | 7382187975121682178 | micro | seeded=False | 175.8 |
| 8026499204807041784 | `5e15ca999c19` | 8026499204807041784 | micro | seeded=False | 155 |
| 8557154790156791364 | `5e15ca999c19` | 8557154790156791364 | micro | seeded=False | 207.7 |
| 887972120279483394 | `5e15ca999c19` | 887972120279483394 | micro | seeded=False | 156.9 |

### Package versions (from run metadata)

- eclipse-sumo: `1.27.1`
- flowstate_core: `2.0.0`
- libsumo: `1.27.1`
- microsim: `2.0.0`
- numpy: `2.5.2`
- pandas: `3.0.5`
- pyarrow: `25.0.1`
- python: `3.12.13`

### Calibration artifacts used

| Artifact | data_hash |
|---|---|
| `artifacts/idm_i24.json` | `aa97dd93d2bf250ea23c3624f91afe8d7035a672fd13efbd53013709e12c7d56` |

## Acceptance criteria

| Criterion | Value | Threshold | Evaluated | Result |
|---|---|---|---|---|
| link_flows_geh | 0.25 | GEH < 5 for >= 85% of link-hour comparisons | yes | FAIL — fraction of comparisons with GEH < 5 |
| speeds_rmspe | 1.83 | segment-speed RMSPE <= 15% | yes | FAIL |
| wave_speed | 8.483 | backward wave speed in [14, 22] km/h (emergent, unseeded) | yes | FAIL |
| ring_emergence | — | Sugiyama ring emergence benchmark reproduced | no | FAIL — not evaluated: input not supplied |
| ring_dampening | — | Stern single-AV dampening benchmark reproduced | no | FAIL — not evaluated: input not supplied |
| n_seeds | 20 | n_seeds >= 20 | yes | PASS |

Rows marked not evaluated had no input supplied and count as failing; an
unevaluated criterion is never a pass.

## Metrics

Mean with two-sided t-distribution confidence bounds at the
95 percent level over n replicates. Rows flagged
underpowered have fewer than 20 replicates and must not be
quoted as headline results.

| Metric | Mean | Lower | Upper | n | Underpowered |
|---|---|---|---|---|---|
| throughput_veh_h | 4023 | 4020 | 4026 | 20 | no |
| mean_tt_s | 257.4 | 254.1 | 260.7 | 20 | no |
| p90_tt_s | 333.4 | 328.4 | 338.5 | 20 | no |
| sigma_v_spatial_ms | 5.478 | 5.438 | 5.519 | 20 | no |
| sigma_v_temporal_ms | 4.592 | 4.552 | 4.632 | 20 | no |
| vmt_veh_km | 6.965e+04 | 6.96e+04 | 6.969e+04 | 20 | no |
| vht_veh_h | 1081 | 1071 | 1090 | 20 | no |
| fuel_ml_per_veh_km | 65.82 | 65.75 | 65.88 | 20 | no |
| wave_count | 25.05 | 22.6 | 27.5 | 20 | no |
| wave_speed_kmh | 8.483 | 7.857 | 9.108 | 20 | no |
| wave_amplitude_ms | 14.16 | 13.91 | 14.41 | 20 | no |

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
