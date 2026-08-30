# FlowState v2 — us101_replica validation report (NGSIM US-101 p1, measured downstream boundary)

Generated: 2026-08-30T01:10:41Z

## Provenance

Profile: `fhwa_default`. Seeds: 134183728835869882, 165503670820534583, 2378473973028931053, 3011106312394044631, 3747978530954135749, 3944094060050347669, 4910985839736976611, 5690692725577505498, 6134032994440706937, 6143473282319009404, 6538422657834023852, 661281422688282993, 677105600768189526, 6904272788004776631, 6914975401685141156, 6953598295321596746, 7382187975121682178, 8026499204807041784, 8557154790156791364, 887972120279483394.

| Run | Config hash | Seed | Tier | seeded | Wall time [s] |
|---|---|---|---|---|---|
| 134183728835869882 | `e897b6479ed4` | 134183728835869882 | micro | seeded=False | 3.813 |
| 165503670820534583 | `e897b6479ed4` | 165503670820534583 | micro | seeded=False | 3.86 |
| 2378473973028931053 | `e897b6479ed4` | 2378473973028931053 | micro | seeded=False | 3.982 |
| 3011106312394044631 | `e897b6479ed4` | 3011106312394044631 | micro | seeded=False | 4.059 |
| 3747978530954135749 | `e897b6479ed4` | 3747978530954135749 | micro | seeded=False | 3.926 |
| 3944094060050347669 | `e897b6479ed4` | 3944094060050347669 | micro | seeded=False | 3.939 |
| 4910985839736976611 | `e897b6479ed4` | 4910985839736976611 | micro | seeded=False | 3.937 |
| 5690692725577505498 | `e897b6479ed4` | 5690692725577505498 | micro | seeded=False | 2.703 |
| 6134032994440706937 | `e897b6479ed4` | 6134032994440706937 | micro | seeded=False | 3.896 |
| 6143473282319009404 | `e897b6479ed4` | 6143473282319009404 | micro | seeded=False | 3.839 |
| 6538422657834023852 | `e897b6479ed4` | 6538422657834023852 | micro | seeded=False | 3.794 |
| 661281422688282993 | `e897b6479ed4` | 661281422688282993 | micro | seeded=False | 3.855 |
| 677105600768189526 | `e897b6479ed4` | 677105600768189526 | micro | seeded=False | 3.938 |
| 6904272788004776631 | `e897b6479ed4` | 6904272788004776631 | micro | seeded=False | 3.646 |
| 6914975401685141156 | `e897b6479ed4` | 6914975401685141156 | micro | seeded=False | 3.861 |
| 6953598295321596746 | `e897b6479ed4` | 6953598295321596746 | micro | seeded=False | 3.936 |
| 7382187975121682178 | `e897b6479ed4` | 7382187975121682178 | micro | seeded=False | 2.769 |
| 8026499204807041784 | `e897b6479ed4` | 8026499204807041784 | micro | seeded=False | 2.715 |
| 8557154790156791364 | `e897b6479ed4` | 8557154790156791364 | micro | seeded=False | 4.039 |
| 887972120279483394 | `e897b6479ed4` | 887972120279483394 | micro | seeded=False | 2.733 |

### Package versions (from run metadata)

- eclipse-sumo: `1.27.1`
- flowstate_core: `2.0.0-dev`
- libsumo: `1.27.1`
- microsim: `2.0.0.dev0`
- numpy: `2.5.2`
- pandas: `3.0.5`
- pyarrow: `25.0.1`
- python: `3.12.13`

### Calibration artifacts used

| Artifact | data_hash |
|---|---|
| `artifacts/idm_us101.json` | `8578f4754b267ad09eed5b0b8b3e18c83b7d9036b7afdcec295d5d3f19195e22` |

## Acceptance criteria

| Criterion | Value | Threshold | Evaluated | Result |
|---|---|---|---|---|
| link_flows_geh | 0.5556 | GEH < 5 for >= 85% of link-hour comparisons | yes | FAIL — fraction of comparisons with GEH < 5 |
| speeds_rmspe | 0.3661 | segment-speed RMSPE <= 15% | yes | FAIL |
| wave_speed | 7.388 | backward wave speed in [14, 22] km/h (emergent, unseeded) | yes | FAIL |
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
| throughput_veh_h | 7693 | 7686 | 7700 | 20 | no |
| mean_tt_s | 49.26 | 48.51 | 50 | 20 | no |
| p90_tt_s | 71.27 | 69.94 | 72.6 | 20 | no |
| sigma_v_spatial_ms | 3.554 | 3.495 | 3.613 | 20 | no |
| sigma_v_temporal_ms | 2.925 | 2.844 | 3.007 | 20 | no |
| vmt_veh_km | 3623 | 3621 | 3625 | 20 | no |
| vht_veh_h | 72.55 | 71.84 | 73.26 | 20 | no |
| fuel_ml_per_veh_km | 66.42 | 65.98 | 66.87 | 20 | no |
| wave_count | 1.65 | 1.27 | 2.03 | 20 | no |
| wave_speed_kmh | 7.388 | 6.794 | 7.981 | 20 | no |
| wave_amplitude_ms | 10.75 | 9.729 | 11.76 | 20 | no |

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
