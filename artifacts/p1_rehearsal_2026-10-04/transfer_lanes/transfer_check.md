# Do our driver settings fit this corridor?

- **Detector data**: data/mndot/cache
- **Data hash (sha256 of the loaded rows)**: bfeef85a501f567e
- **Driver population**: scenarios/mndot_i94_wb_stpaul_weave.yaml
- **Code**: 75a54b7ca45a
- **Created**: 2026-10-05T03:16:55Z

## Summary

The driver population **scenarios/mndot_i94_wb_stpaul_weave.yaml** (EIDM) was measured on other roads. These are its settings against 14 detector station(s) of this corridor over 9 day(s), 00:00–24:00 local.

- **Free-flow speed:** drivers here drive about 19% faster than the population we measured would on this road: 104 km/h (65 mph) observed (median of 15,359 five-minute readings in light traffic at 11 station(s); 95 % interval 104.1–104.5 km/h), 88 km/h (55 mph) for the model at the same traffic level. The posted limit is 55 mph; observed drivers are +18% relative to it, and the model's drivers never exceed it. Verdict: **does not fit** (the model is 15.8% below the observed value, and more than 5% outside its whole interval).
- **Capacity per lane:** the road carried about 1,482 vehicles per hour per lane downstream of the active bottleneck(s), at S97 (95 % interval 1,418–1,540). The model's drivers carry 1,663 (simulated straight-road capacity (artifacts/idm_mndot_i94_wb_stpaul_capacity.calibration.json, 3 lanes, 1663 veh/h/lane) × 1.000 for this corridor's limit and truck share): the road carries 11% less. Verdict: **does not fit**.
- **Truck share:** not available in these data (no classification counts were given (loop counts carry no vehicle class)); the model assumes 0.0%.

| Quantity | Observed (95 % interval) | Model | Difference | Rule | Verdict |
|---|---|---|---|---|---|
| truck share | — | 0.0% | — | model within ±3 points of the observed share | **not_available** |
| free flow speed | 104.3 km/h (104.1 km/h–104.5 km/h) | 87.9 km/h | +18.7% | model within ±5% of the observed median window-mean speed in light traffic | **mismatch** |
| capacity per lane | 1,482 (1,418–1,540) | 1,663 | -10.9% | model within ±5% of the observed capacity (95th percentile of 5-minute per-lane flow at the stations that reached capacity) | **mismatch** |

Differences read as *this corridor relative to the model*. A verdict of *cannot tell* means the difference is beyond the tolerance of the observed value but not of its interval: more days would decide it.

## Recommendations

These are recommendations. A person decides whether to apply any of them, and the decision is recorded with the study (docs/FRISCO_PROTOCOL.md §7.2: one of the measured populations, with a single corridor-wide adjustment inside the measured ranges, never a per-location setting).

### Truck share: no data

The truck share is not measured in these data; the model keeps 0.0%. Ask for classification counts, or test how much the results depend on it (docs/FRISCO_PROTOCOL.md §8.5).

### Free flow speed: needs engine change

Only the speed factor on the posted limit (SUMO speedFactor) closes the free-flow speed gap inside its measured range (1.23, range 1.094–1.542), and the engine does not expose it today: microsim.vehicles writes speedFactor=1.0 on every vType and FleetSpec has no field for it — a code change, reviewed like any other.

| Knob | Now | Needed | Measured range | Fits | In the engine today |
|---|---|---|---|---|---|
| the passenger population's mean desired speed (factor on v0) | 1 | never reached | 0.8302–1.17 | no | yes |
| the speed factor on the posted limit (SUMO speedFactor) | 1 | 1.23 | 1.094–1.542 | yes | no |

- *v0_scale* range: measured population artifacts/idm_i24.json: v0 mean 32.4 ± 1 sd (5.5), within CLAUDE.md §3.1's 25–38.
- *v0_scale*: over its range the model's free-flow speed spans 23.38–24.58 m/s and never reaches 28.98 m/s.
- *speed_factor* range: factor × posted limit (55 mph) inside the measured desired-speed range 26.9–37.9 m/s.
- *speed_factor*: drivers here exceed the posted limit, which caps every model driver.

### Capacity per lane: cannot match

The population cannot match this corridor inside its measured ranges. No knob closes the capacity gap within its measured range.

| Knob | Now | Needed | Measured range | Fits | In the engine today |
|---|---|---|---|---|---|
| the passenger population's mean time headway (factor on T) | 1 | never reached | 0.8572–1.143 | no | yes |

- *t_scale* range: measured population artifacts/idm_i24.json: T mean 1.51 ± 1 sd (0.521), within CLAUDE.md §3.1's 0.8–2.2; and the sidecar's simulated grid.
- *t_scale*: read off the simulated grid (artifacts/idm_mndot_i94_wb_stpaul_capacity.calibration.json: T × 0.857–1.14 of this population, adjusted to this corridor); the simulated capacity spans 1591–1709 veh/h/lane there and never reaches 1482.

## Ranges for the uncertainty runs

How far each driver setting is varied when results are tested for robustness (docs/FRISCO_PROTOCOL.md §8.5):

- **Mean desired speed (v0):** × 0.83–1.17 (mean desired speed 96.8–136.4 km/h): the whole measured range, the spread of individual drivers rather than what is unknown about this corridor's population, so the uncertainty runs label it assumed; the reason: no value of the mean desired speed (v0) in the measured range keeps the model's free-flow speed inside the observed interval 28.93–29.03 m/s; over it the model's free-flow speed spans 23.38–24.58 m/s.
- **Mean time headway (T):** × 0.749–1.54 (mean time headway 0.99–2.03 s): the whole measured range, the spread of individual drivers rather than what is unknown about this corridor's population, so the uncertainty runs label it assumed; the reason: no value of the mean time headway (T) in the measured range ∩ the simulated grid (not extrapolated) keeps the model's capacity per lane inside the observed interval 1418–1540 veh/h/lane; over it the model's capacity per lane spans 1591–1709 veh/h/lane.

## Free-flow speed by station

| Station | Lanes | Limit | Windows | Days | Median | 15th–85th pct | Flow (veh/h/lane) |
|---|---|---|---|---|---|---|---|
| S1063 | 4 | 55 mph | 0 | 0 | — | — | — |
| S1064 | 3 | 55 mph | 1914 | 9 | 106.1 km/h | 102–108 km/h | 640 |
| S1065 | 3 | 55 mph | 1984 | 9 | 108.7 km/h | 104–111 km/h | 580 |
| S1066 | 3 | 55 mph | 1891 | 9 | 107.1 km/h | 104–109 km/h | 668 |
| S1067 | 3 | 55 mph | 1621 | 9 | 103.6 km/h | 101–106 km/h | 724 |
| S1068 | 3 | 55 mph | 1577 | 9 | 106.1 km/h | 103–108 km/h | 736 |
| S1069 | 3 | 55 mph | 1400 | 9 | 106.2 km/h | 103–108 km/h | 726 |
| S1070 | 5 | 55 mph | 0 | 0 | — | — | — |
| S1947 | 3 | 55 mph | 1116 | 9 | 99.4 km/h | 96–102 km/h | 598 |
| S1948 | 5 | 55 mph | 0 | 0 | — | — | — |
| S790 | 3 | 55 mph | 876 | 9 | 93.5 km/h | 90–98 km/h | 445 |
| S791 | 3 | 55 mph | 1107 | 9 | 88.7 km/h | 87–92 km/h | 584 |
| S792 | 2 | 55 mph | 871 | 9 | 103.0 km/h | 100–106 km/h | 450 |
| S97 | 3 | 55 mph | 1002 | 9 | 94.1 km/h | 91–99 km/h | 556 |

Corridor: median 104 km/h (65 mph), 15th–85th percentile 94 km/h (59 mph) to 108 km/h (67 mph) of the five-minute means. The model's drivers want 88 km/h (55 mph) on average (15th–85th 89 km/h (55 mph) to 89 km/h (55 mph), individual drivers — not comparable with the spread of five-minute means, which averages many vehicles).

## Capacity by station

| Station | Lanes | What limits its flow | 95th pct flow (veh/h/lane) | Speed there | Discharge flow | Pre-breakdown flow (breakdowns) |
|---|---|---|---|---|---|---|
| S1063 | 4 | never congested (a lower bound) | — | — | — | — |
| S1064 | 3 | never congested (a lower bound) | 1,034 (1,012–1,068) | 108 km/h | — | — |
| S1065 | 3 | never congested (a lower bound) | 908 (892–920) | 111 km/h | — | — |
| S1066 | 3 | reached congestion | 1,056 (1,040–1,076) | 108 km/h | — | 1,263 (1) |
| S1067 | 3 | reached congestion | 1,220 (1,192–1,232) | 103 km/h | — | 1,415 (6) |
| S1068 | 3 | reached congestion | 1,196 (1,180–1,216) | 105 km/h | — | 1,394 (8) |
| S1069 | 3 | reached congestion | 1,254 (1,242–1,266) | 104 km/h | — | 1,479 (9) |
| S1070 | 5 | reached congestion | — | — | — | — |
| S1947 | 3 | reached congestion | 1,440 (1,424–1,452) | 98 km/h | — | 1,591 (9) |
| S1948 | 5 | reached congestion | — | — | — | — |
| S790 | 3 | reached congestion | 1,576 (1,531–1,614) | 51 km/h | — | 1,396 (27) |
| S791 | 3 | reached congestion | 1,380 (1,348–1,408) | 43 km/h | — | 1,321 (23) |
| S792 | 2 | reached congestion | 1,536 (1,506–1,548) | 93 km/h | — | 1,672 (15) |
| S97 | 3 | discharges an active bottleneck | 1,482 (1,418–1,540) | 88 km/h | 1,308 | 1,641 (9) |

Active bottlenecks (docs/FRISCO_PROTOCOL.md §5 rule):

- S790 → S97: 291 active window(s) on 9 day(s)

## Model values and how they were obtained

- Free-flow speed: EIDM steady state of 20,000 drivers drawn as the simulator draws them (seed 11), at the observed light-traffic flow of 632 veh/h/lane, each driver's desired speed capped at the posted limit (SUMO speedFactor 1). Analytical, no simulation.
- Capacity, mean driver (closed form): 2,210 veh/h/lane at 88 km/h (55 mph).
- Capacity, drawn population (one lane, common speed): 1,968 veh/h/lane at 57 km/h (36 mph).
- Capacity, simulated: 1,663 veh/h/lane on a straight 3-lane road (artifacts/idm_mndot_i94_wb_stpaul_capacity.calibration.json), × 1.000 for this corridor's limit and truck share = **1,663** (used).
- Capacity used for the verdict: simulated.
- Time headway per lane at capacity (diagnostic): observed 2.43 s (2.12 s net of the 7.5 m jam spacing), model 2.16 s (1.67 s net); the population's mean desired headway T is 1.32 s.

Simulated capacity measurements considered:

- artifacts/idm_capacity_probe_i24fleet_3l.calibration.json: not used — measured under IDM, the fleet runs EIDM
- artifacts/idm_capacity_probe_mnfleet_4l.calibration.json: accepted — same population (T scaled), same car-following model
- artifacts/idm_i24_capacity.calibration.json: not used — its car-following model is not recorded (name it explicitly to use it)
- artifacts/idm_mndot_i94_wb_stpaul_capacity.calibration.json: used — same population (T scaled), same car-following model
- artifacts/idm_us101_capacity.calibration.json: not used — measures a different population

## How each number was measured

- Readings the data-quality check excluded or set aside are not used (applied: 99 detector-day(s) excluded, 32 kept with caveats); nothing is filled in.
- Statistics use 5-minute windows (data: 300 s); per-lane values divide a station's total by its lanes.
- Light traffic: occupancy ≤ 10 %, flow ≤ 1,000 veh/h/lane and ≥ 10 vehicles per lane in the window.
- Capacity: the 95th percentile of a station's per-lane flows; congested below 40 mph for 15 minutes; an active bottleneck as in docs/FRISCO_PROTOCOL.md §5.
- Intervals: 95% percentile bootstrap over days.

| Rule | Value | Source or reason |
|---|---|---|
| free_flow_max_occupancy_pct | 10 | free-branch cut of calibration.fd_fit (0.10) |
| free_flow_max_flow_veh_h_lane | 1000 | HCM 6th ed. ch. 12: FFS measured at flows up to 1,000 pc/h/ln |
| free_flow_min_count_veh_lane | 10 | as calibration.data_quality.IMPLIED_LENGTH_MIN_COUNT_VEH |
| free_flow_fallback_length_m | 7 | default g of calibration.loaders.detector_csv.to_fd_frame |
| analysis_window_s | 300 | CLAUDE.md s.6.1: 5-minute station data |
| capacity_percentile | 95 | CLAUDE.md s.6.1; calibration.fd_fit q_max_percentile |
| breakdown_speed_ms | 17.8816 | FRISCO_PROTOCOL s.5 (40 mph; Chen, Skabardonis & Varaiya 2004) |
| bottleneck_speed_difference_ms | 8.9408 | FRISCO_PROTOCOL s.5 (20 mph) |
| bottleneck_persistence | 5, 7 | FRISCO_PROTOCOL s.5 (5 of 7 windows; a FlowState rule there) |
| congested_min_run_s | 900 | HCM 15-minute analysis period |
| pre_breakdown_s | 900 | HCM 15-minute analysis period (diagnostic only) |
| free_flow_speed_tolerance | 0.05 | +-5 %: about one posted-limit step; inside protocol C3's 15 % |
| capacity_tolerance | 0.05 | +-5 %: the assumed detector count accuracy |
| heavy_share_tolerance | 0.03 | 3 points: <= 3 % of PCE flow at the HCM truck PCE of 2.0 |
| interval_level | 0.95 | CLAUDE.md s.0.6 |
| min_days_for_interval | 3 | fewer days give a degenerate day bootstrap |
| measured_range_sigmas | 1 | inside the central ~68 % of the measured drivers' values |

## Limits

- The model values are analytical (steady-state car following) except a simulated straight-road capacity where the repository holds one; none is a simulation of this corridor.
- Free-flow speeds are five-minute means at detectors, not individual vehicles' speeds; their spread is not a spread of drivers.
- A station's capacity value depends on the hours and days given: a span without the peak understates it.
- The truck share is used only where classification counts were given; it is never inferred from loop data.
