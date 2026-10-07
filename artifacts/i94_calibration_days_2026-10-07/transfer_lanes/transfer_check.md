# Do our driver settings fit this corridor?

- **Detector data**: data/mndot/cache
- **Data hash (sha256 of the loaded rows)**: fed19d9d44e27a85
- **Driver population**: scenarios/mndot_i94_wb_stpaul_weave_dc.yaml
- **Code**: ca08a6209177
- **Created**: 2026-10-07T08:57:47Z

## Summary

The driver population **scenarios/mndot_i94_wb_stpaul_weave_dc.yaml** (EIDM) was measured on other roads. These are its settings against 14 detector station(s) of this corridor over 5 day(s), 05:30–09:30 local.

- **Free-flow speed:** drivers here drive about 23% faster than the population we measured would on this road: 108 km/h (67 mph) observed (median of 893 five-minute readings in light traffic at 11 station(s); 95 % interval 107.6–109.0 km/h), 88 km/h (55 mph) for the model at the same traffic level. The posted limit is 55 mph; observed drivers are +22% relative to it, and the model's drivers never exceed it. Verdict: **does not fit** (the model is 18.7% below the observed value, and more than 5% outside its whole interval).
- **Capacity per lane:** the road carried about 1,676 vehicles per hour per lane downstream of the active bottleneck(s), at S97 (95 % interval 1,628–1,728). The model has no capacity value here: no model value. Verdict: **not available**.
- **Truck share:** not available in these data (no classification counts were given (loop counts carry no vehicle class)); the model assumes 0.0%.

| Quantity | Observed (95 % interval) | Model | Difference | Rule | Verdict |
|---|---|---|---|---|---|
| truck share | — | 0.0% | — | model within ±3 points of the observed share | **not_available** |
| free flow speed | 108.1 km/h (107.6 km/h–109.0 km/h) | 87.9 km/h | +23.0% | model within ±5% of the observed median window-mean speed in light traffic | **mismatch** |
| capacity per lane | 1,676 (1,628–1,728) | — | — | model within ±5% of the observed capacity (95th percentile of 5-minute per-lane flow at the stations that reached capacity) | **not_available** |

Differences read as *this corridor relative to the model*. A verdict of *cannot tell* means the difference is beyond the tolerance of the observed value but not of its interval: more days would decide it.

## Recommendations

These are recommendations. A person decides whether to apply any of them, and the decision is recorded with the study (docs/FRISCO_PROTOCOL.md §7.2: one of the measured populations, with a single corridor-wide adjustment inside the measured ranges, never a per-location setting).

### Truck share: no data

The truck share is not measured in these data; the model keeps 0.0%. Ask for classification counts, or test how much the results depend on it (docs/FRISCO_PROTOCOL.md §8.5).

### Free flow speed: adjust

Set the speed factor on the posted limit (SUMO speedFactor) to 1.303 (now 1; measured range 1.094–1.542) corridor-wide: fleet.speed_factor in the scenario (FleetSpec.speed_factor, WP-109): written as SUMO's speedFactor on every passenger vehicle, whose desired speed becomes min(v0, factor × the posted limit); heavy vehicles keep 1.0, fleet.speed_dev stays as configured, and a downstream boundary schedule is posted divided by the factor so its measured speeds are still the speeds driven. This is a recommendation — a reviewer decides and records the decision.

| Knob | Now | Needed | Measured range | Fits | In the engine today |
|---|---|---|---|---|---|
| the passenger population's mean desired speed (factor on v0) | 1 | never reached | 0.8302–1.17 | no | yes |
| the speed factor on the posted limit (SUMO speedFactor) | 1 | 1.303 | 1.094–1.542 | yes | yes |

- *v0_scale* range: measured population artifacts/idm_i24.json (source of artifacts/idm_i24_capacity_amax_k1.0.json: mean a_max shifted from artifacts/idm_i24_capacity.json (sha256 80a7579b7251; scripts/derive_population.py, no sidecar); that base: mean T / v0 scaled from the source (sidecar artifacts/idm_i24_capacity.calibration.json)): v0 mean 32.4 ± 1 sd (5.5), within CLAUDE.md §3.1's 25–38.
- *v0_scale*: over its range the model's free-flow speed spans 23.37–24.58 m/s and never reaches 30.03 m/s.
- *speed_factor* range: factor × posted limit (55 mph) inside the measured desired-speed range 26.9–37.9 m/s, and inside SUMO's speed-factor cut-offs 0.2–2.
- *speed_factor*: drivers here exceed the posted limit, which caps every model driver.

### Capacity per lane: no data

No model capacity for this EIDM fleet: run scripts/calibrate_capacity.py for the population under its own model, then rerun this check.

## Ranges for the uncertainty runs

How far each driver setting is varied when results are tested for robustness (docs/FRISCO_PROTOCOL.md §8.5):

- **Mean desired speed (v0):** × 0.83–1.17 (mean desired speed 96.8–136.4 km/h): the whole measured range, the spread of individual drivers rather than what is unknown about this corridor's population, so the uncertainty runs label it assumed; the reason: no value of the mean desired speed (v0) in the measured range keeps the model's free-flow speed inside the observed interval 29.9–30.28 m/s; over it the model's free-flow speed spans 23.37–24.58 m/s.
- **Mean time headway (T):** × 0.749–1.54 (mean time headway 0.99–2.03 s): the whole measured range, the spread of individual drivers rather than what is unknown about this corridor's population, so the uncertainty runs label it assumed; the reason: the model has no capacity per lane value here (no model value).

## Free-flow speed by station

| Station | Lanes | Limit | Windows | Days | Median | 15th–85th pct | Flow (veh/h/lane) |
|---|---|---|---|---|---|---|---|
| S1063 | 4 | 55 mph | 0 | 0 | — | — | — |
| S1064 | 3 | 55 mph | 150 | 5 | 107.7 km/h | 106–110 km/h | 812 |
| S1065 | 3 | 55 mph | 210 | 5 | 110.2 km/h | 109–112 km/h | 754 |
| S1066 | 3 | 55 mph | 154 | 5 | 108.8 km/h | 107–111 km/h | 809 |
| S1067 | 3 | 55 mph | 99 | 5 | 106.1 km/h | 104–108 km/h | 844 |
| S1068 | 3 | 55 mph | 83 | 5 | 107.9 km/h | 106–110 km/h | 832 |
| S1069 | 3 | 55 mph | 51 | 5 | 108.9 km/h | 108–110 km/h | 872 |
| S1070 | 5 | 55 mph | 69 | 5 | 107.4 km/h | 105–109 km/h | 770 |
| S1947 | 3 | 55 mph | 38 | 5 | 103.4 km/h | 102–105 km/h | 882 |
| S1948 | 5 | 55 mph | 0 | 0 | — | — | — |
| S790 | 3 | 55 mph | 4 | 4 | 95.3 km/h | 93–98 km/h | 878 |
| S791 | 3 | 55 mph | 17 | 5 | 93.2 km/h | 90–94 km/h | 916 |
| S792 | 3 | 55 mph | 0 | 0 | — | — | — |
| S97 | 3 | 55 mph | 18 | 5 | 99.8 km/h | 97–101 km/h | 932 |

Corridor: median 108 km/h (67 mph), 15th–85th percentile 105 km/h (65 mph) to 111 km/h (69 mph) of the five-minute means. The model's drivers want 88 km/h (55 mph) on average (15th–85th 89 km/h (55 mph) to 89 km/h (55 mph), individual drivers — not comparable with the spread of five-minute means, which averages many vehicles).

## Capacity by station

| Station | Lanes | What limits its flow | 95th pct flow (veh/h/lane) | Speed there | Discharge flow | Pre-breakdown flow (breakdowns) |
|---|---|---|---|---|---|---|
| S1063 | 4 | never congested (a lower bound) | — | — | — | — |
| S1064 | 3 | never congested (a lower bound) | 1,240 (1,204–1,281) | 107 km/h | — | — |
| S1065 | 3 | never congested (a lower bound) | 1,092 (1,044–1,122) | 111 km/h | — | — |
| S1066 | 3 | never congested (a lower bound) | 1,280 (1,240–1,297) | 108 km/h | — | — |
| S1067 | 3 | reached congestion | 1,444 (1,396–1,460) | 102 km/h | — | 1,437 (3) |
| S1068 | 3 | reached congestion | 1,452 (1,420–1,500) | 100 km/h | — | 1,419 (4) |
| S1069 | 3 | reached congestion | 1,448 (1,404–1,456) | 94 km/h | — | 1,473 (5) |
| S1070 | 5 | reached congestion | 1,138 (1,092–1,151) | 73 km/h | — | 1,089 (4) |
| S1947 | 3 | reached congestion | 1,660 (1,597–1,669) | 91 km/h | — | 1,591 (5) |
| S1948 | 5 | reached congestion | — | — | — | — |
| S790 | 3 | reached congestion | 1,769 (1,740–1,820) | 55 km/h | — | 1,564 (6) |
| S791 | 3 | reached congestion | 1,616 (1,556–1,651) | 66 km/h | — | 1,489 (6) |
| S792 | 3 | reached congestion | — | — | — | — |
| S97 | 3 | discharges an active bottleneck | 1,676 (1,628–1,728) | 90 km/h | 1,440 | 1,650 (4) |

Active bottlenecks (docs/FRISCO_PROTOCOL.md §5 rule):

- S790 → S97: 59 active window(s) on 4 day(s)

## Model values and how they were obtained

- Free-flow speed: EIDM steady state of 20,000 drivers drawn as the simulator draws them (seed 11), at the observed light-traffic flow of 812 veh/h/lane, each driver's desired speed capped at the posted limit (SUMO speedFactor 1). Analytical, no simulation.
- Capacity, mean driver (closed form): 2,210 veh/h/lane at 88 km/h (55 mph).
- Capacity, drawn population (one lane, common speed): 1,963 veh/h/lane at 57 km/h (36 mph).
- Capacity used for the verdict: none.
- Time headway per lane at capacity (diagnostic): observed 2.15 s (1.85 s net of the 7.5 m jam spacing), model — (— net); the population's mean desired headway T is 1.32 s.

Simulated capacity measurements considered:

- artifacts/idm_capacity_probe_i24fleet_3l.calibration.json: not used — measures a different population
- artifacts/idm_capacity_probe_mnfleet_4l.calibration.json: not used — measures a different population
- artifacts/idm_i24_capacity.calibration.json: not used — measures a different population
- artifacts/idm_mndot_i94_wb_stpaul_capacity.calibration.json: not used — measures a different population
- artifacts/idm_us101_capacity.calibration.json: not used — measures a different population

## How each number was measured

- Readings the data-quality check excluded or set aside are not used (applied: 45 detector-day(s) excluded, 23 kept with caveats); nothing is filled in.
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

## Notes

- no simulated capacity for this population under EIDM, and its analytical value is not the fleet's capacity (SUMO's EIDM carried about 11 % less than plain IDM on the same population, docs/ONBOARDING_MNDOT.md §10): run scripts/calibrate_capacity.py for it

## Limits

- The model values are analytical (steady-state car following) except a simulated straight-road capacity where the repository holds one; none is a simulation of this corridor.
- Free-flow speeds are five-minute means at detectors, not individual vehicles' speeds; their spread is not a spread of drivers.
- A station's capacity value depends on the hours and days given: a span without the peak understates it.
- The truck share is used only where classification counts were given; it is never inferred from loop data.
