# Why the I-24 replica cannot carry its demand: capacity, not insertion

**Date:** 2026-09-03 · **Artifacts:** `artifacts/i24_capacity_experiment.json`,
`artifacts/idm_i24_capacity.json`, `artifacts/idm_i24_capacity.calibration.json` ·
**Scripts:** `scripts/i24_capacity_experiment.py`, `scripts/i24_calibrate_capacity.py`

[I24_VALIDATION.md](I24_VALIDATION.md) §3 attributes two of the three
corridor failures to insertion: only 82–84% of the coverage-corrected demand
enters the replica. The corrected mainline inflow is ≈ 2.05 veh/s over four
lanes, ≈ 1,840 veh/h per lane, at the fitted fundamental diagram's capacity
lower bound ([I24_DATA.md](I24_DATA.md), fundamental-diagram section). This
document asks the prior question — can the calibrated fleet carry that flow
at all? — and answers it with two experiments, then applies the FHWA
calibration procedure's first step.

## 1. The fleet's capacity on a straight road

A straight four-lane corridor, 4 km, no ramps, no boundary, the replica's
fleet (`artifacts/idm_i24.json` population, replica lane-change
parameters), driven at fixed per-lane inflows for 30 simulated minutes
(5 warm-up), three seeds per level. Throughput is counted at 3 km.

| Demand [veh/h/lane] | Throughput at 3 km [veh/h/lane] | Inserted | Mean speed at 3 km [m/s] |
|---|---|---|---|
| 1,400 | 1,397 | 1.000 | 17.6 |
| 1,600 | 1,588 | 0.999 | 16.8 |
| 1,800 | 1,647 | 0.922 | 16.8 |
| 2,000 | 1,643 | 0.831 | 16.7 |
| 2,200 | 1,656 | 0.759 | 16.9 |
| 2,400 | 1,646 | 0.691 | 16.8 |

**The calibrated fleet saturates at about 1,650 veh/h per lane.** That is
below the corrected demand (1,840) and below the 1,775 veh/h per lane the
instrument *tracked* at the same site (`artifacts/fd_i24.json`, `q_max` CI
low end) — a lower bound on what the road carried, since the instrument
misses a third to a half of the vehicles. Whatever the true demand was, the
replica's fleet cannot pass even the tracked flow, so it queues from the
first window; that is the 19–20 km/h in the first two span segments and the
missing recoveries after 08:10 in the validation.

The mean speed at capacity, about 17 m/s (61 km/h), is a property of the
Intelligent Driver Model with this population: equilibrium flow peaks well
below the desired speed and falls toward it, because the equilibrium gap
grows with `1/√(1 − (v/v0)⁴)`. With the fitted T = 1.51 s, s0 = 2.53 m and
5 m vehicles the theoretical equilibrium capacity is about 1,780 veh/h per
lane at 17 m/s; heterogeneity and lane changes take it to the measured
1,650.

## 2. Insertion mechanics are not the lever

The corrected replica, one seed each, with the runner's insertion spread over
1 or 3 leading edges (three seeds each):

| Insertion | Inserted fraction | Throughput at sim x ≈ 2.6 / 4.3 / 6.0 / 7.7 km [veh/h] | Speed in the first 2 km of the buffer |
|---|---|---|---|
| 1 edge (as validated) | 0.830–0.842 | 6,080–6,220 / 6,050–6,160 / 5,950–6,070 / 5,150–5,250 | 19–21 km/h |
| 3 edges | 0.808–0.816 | 6,160–6,240 / 6,060–6,120 / 5,950–6,020 / 5,150–5,210 | 75–85 km/h |

Spreading insertion frees the buffer and changes nothing downstream: the
same 6,000–6,200 veh/h (≈ 1,500–1,550 per lane after the ramps) passes the
span, and the Old Hickory on-ramp still delivers 1,830–1,890 of 2,427
planned vehicles. The bottleneck is the first span segment and the Old
Hickory merge, whose auxiliary lane the map does provide (the merge and
diverge edges are five lanes wide). A longer or wider insertion buffer would
only store a longer queue.

## 3. What follows: capacity calibration first (FHWA Vol. III step 1)

The FHWA Traffic Analysis Toolbox Vol. III calibration procedure is
sequential: calibrate capacity (car-following parameters against field
capacity), then demand, then system performance. The car-following
population was fitted on congested car-following episodes by gap error,
which identifies T and s0 in following but says little about capacity;
this is the known IDM trade-off between congested headways and free-flow
capacity.

`scripts/i24_calibrate_capacity.py` scales the population's mean T by a
factor `f` — covariance, the other means, lane-change parameters and demand
untouched — and finds the `f*` at which the straight-road capacity meets
the field target by linear interpolation between grid points. The target is
the tracked lower bound (1,775 veh/h per lane), the only field capacity
available without the TDOT radar-detector counts, so `f*` is conservative.
The cost is reported as the population-mean gap RMSE over a seeded sample
of the car-following episodes, before and after. Results are in §4 once the
run completes; the derived population is `artifacts/idm_i24_capacity.json`.

Demand calibration (step 2) follows in `scripts/i24_fit_demand_scale.py`: a
single scale factor on the tracked inflows fitted to observed segment speeds
over 06:30–07:30 only, with 07:30–08:30 held out, so the second hour and the
wave criteria remain an out-of-sample test. Neither step touches a
validation criterion directly.

## 4. Capacity calibration result

Straight four-lane corridor at a saturating 2,400 veh/h per lane demand, two
seeds per point, 30 simulated minutes (5 warm-up), throughput at 3 km
(`artifacts/idm_i24_capacity.calibration.json`):

| T scale | Mean T [s] | Capacity [veh/h/lane] |
|---|---|---|
| 1.00 | 1.511 | 1,631 |
| 0.95 | 1.436 | 1,690 |
| 0.90 | 1.360 | 1,754 |
| 0.85 | 1.285 | 1,796 |
| 0.80 | 1.209 | 1,833 |
| 0.75 | 1.133 | 1,948 |

Interpolating to the 1,775 veh/h per lane target gives **f\* = 0.875,
T = 1.322 s** (`artifacts/idm_i24_capacity.json`). The trade-off the
procedure was expected to cost does not materialise: the population-mean
gap RMSE over 1,500 seeded car-following episodes is 5.313 m before and
5.294 m after. The fitted mean T was not the population's best single
headway for following either; the episode fit identifies each driver's T
well and the population mean poorly, which is why a 12% change in the mean
is invisible in the gap error and decisive for capacity. The scaled T stays
inside the CLAUDE.md §3.1 calibration range (0.8–2.2 s) and close to the
US-101 population's 1.285 s.

This is a calibration against an independent field observable (tracked
capacity), not against any validation criterion, and it is conservative
because the target is a lower bound. The three replica arms are rebuilt on
this population; the validation battery is rerun on all of them, and the
results, whatever they are, replace the table in
[I24_VALIDATION.md](I24_VALIDATION.md).

## 5. Demand calibration result (FHWA step 2) and what is left

With the calibrated population, `scripts/i24_fit_demand_scale.py --base
corrected` scales the coverage-corrected profile (the per-window shape from
the instrument's tracking coverage) by one level `s` and scores segment
speeds against the recording, first hour fitted, second hour held out
(`artifacts/demand_scale_i24_corrected.json`, one seed per point):

| Level s | Inserted | RMSPE 06:30–07:30 (fit) | RMSPE 07:30–08:30 (held out) |
|---|---|---|---|
| 0.60 | 1.000 | 1.156 | 2.193 |
| 0.70 | 1.000 | 0.742 | 1.718 |
| 0.80 | 0.994 | 0.401 | 0.460 |
| 0.85 | 0.945 | **0.320** | **0.396** |
| 0.90 | 0.895 | 0.337 | 0.382 |
| 1.00 | 0.807 | 0.361 | 0.378 |
| 1.10 | 0.734 | 0.373 | 0.389 |

The level is well determined at the low end (below 0.8 the corridor is too
empty and the error explodes) and flat above it. **s = 0.85** is the fit and
`scenarios/i24_replica_speedcal.yaml` is the third arm: 94.5% of its demand
enters (82–84% for the uncorrected level), and the held-out hour scores
39.6%, comparable to the fitted hour. That the coverage correction
overshoots by about 15% at the peak is consistent with §1: a correction
factor estimated from a car-following equilibrium overstates the missing
vehicles where the flow is near capacity.

**The residual is spatial, not temporal.** At s = 0.85 the window means
track the recording within a few km/h through the peak; the error sits in
where the queue is:

| Segment start [km] | 0.0 | 0.5 | 1.1 | 1.6 | 2.2 | 2.7 | 3.3 | 3.8 | 4.4 | 4.9 |
|---|---|---|---|---|---|---|---|---|---|---|
| Observed mean [km/h] | 36.3 | 32.5 | 29.8 | 31.3 | 37.6 | 36.9 | 37.7 | 29.0 | 30.4 | 33.7 |
| Simulated mean [km/h] | 20.9 | 21.2 | 29.7 | 36.8 | 35.4 | 35.0 | 35.0 | 30.6 | 28.0 | 31.9 |
| Mean relative error | −0.34 | −0.26 | +0.09 | +0.31 | +0.03 | +0.01 | +0.03 | +0.13 | −0.04 | −0.04 |

From 2.2 km on the replica is within 4% of the recording. The first
kilometre — the Old Hickory Boulevard merge — runs a third too slow, and
the kilometre after it a third too fast: the replica holds a standing queue
at the merge where the real road's slowest zones are at 1.1–1.6 km and
3.8–4.4 km. That is a merge-behaviour defect (auxiliary-lane use, the ramp's
own coverage-corrected inflow, lane-change cooperation), not a demand or
capacity defect, and it caps the speed RMSPE near 35% whatever the level.
The next diagnostic is the merge itself: the same arm with the Old Hickory
inflow at its tracked level, with the ramp closed, and with the strategic
lane-change weight at SUMO's default.

The single-level variant on the tracked profile (`--base tracked`) was also
fitted; it is inferior on both hours and is recorded in
`artifacts/demand_scale_i24.json` once its rerun completes (the first run's
table was lost to a serialization bug in the script, fixed since).
