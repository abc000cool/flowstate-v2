Seeds: 4 shared by every arm — UNDERPOWERED (a rehearsal, not a headline result).

Means with 95 % intervals over seeds:

| Measure | baseline | vsl_best |
|---|---|---|
| Throughput at the reference section [veh/h] | 3,338 [3,130, 3,545] | 3,186 [3,057, 3,315] |
| Mean travel time including waiting [s] | 361 [332, 389] | 442 [408, 476] |
| 90th-percentile travel time including waiting [s] | 711 [658, 765] | 893 [799, 987] |
| Total delay including waiting [veh·h] | 215 [180, 250] | 353 [291, 414] |
| Speed variation σ_v (temporal) [m/s] | 4.27 [4.03, 4.51] | 3.44 [3.09, 3.78] |
| Speed variation σ_v (spatial) [m/s] | 7.44 [7.39, 7.48] | 4.82 [4.15, 5.48] |
| Wave count [waves] | 13.8 [8.49, 19.0] | 13.0 [8.32, 17.7] |
| Wave amplitude [m/s] | 15.3 [14.7, 15.9] | 8.64 [8.36, 8.91] |
| SUMO collisions [events] | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] |
| Fuel, model estimate (SUMO HBEFA4), not measured [ml/veh·km] | 85.2 [83.3, 87.0] | 96.6 [84.5, 109] |

Paired differences against baseline (same seeds), 95 % intervals; * marks an interval that excludes zero:

| Measure | vsl_best |
|---|---|
| Throughput at the reference section [veh/h] | -152 [-285, -17.7] * |
| Mean travel time including waiting [s] | +81.5 [32.1, 131] * |
| 90th-percentile travel time including waiting [s] | +182 [56.9, 306] * |
| Total delay including waiting [veh·h] | +137 [56.6, 218] * |
| Speed variation σ_v (temporal) [m/s] | -0.83 [-1.34, -0.32] * |
| Speed variation σ_v (spatial) [m/s] | -2.62 [-3.28, -1.96] * |
| Wave count [waves] | -0.75 [-2.75, 1.25] |
| Wave amplitude [m/s] | -6.63 [-6.99, -6.27] * |
| SUMO collisions [events] | 0.00 [0.00, 0.00] |
| Fuel, model estimate (SUMO HBEFA4), not measured [ml/veh·km] | +11.4 [-1.71, 24.5] |

Travel time and delay include the time spent waiting to enter the road and held at ramp meters (docs/FRISCO_PROTOCOL.md §8.2). Fuel is a model estimate (SUMO HBEFA4), not measured.
