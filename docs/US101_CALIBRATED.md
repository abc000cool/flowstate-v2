# The FHWA calibration procedure applied to US-101, with no retuning

**Date:** 2026-09-03 · **Artifacts:** `artifacts/idm_us101_capacity.json`,
`artifacts/idm_us101_capacity.calibration.json`, `artifacts/demand_scale_us101.json`,
`scenarios/us101_replica_calibrated.yaml`, `artifacts/us101_validation_calibrated.json` ·
**Scripts:** `scripts/calibrate_capacity.py`, `scripts/fit_demand_scale.py`,
`scripts/m3_us101_validate.py`

[I24_CAPACITY.md](I24_CAPACITY.md) applies the FHWA Traffic Analysis Toolbox
Vol. III calibration sequence to the I-24 replica: step 1 calibrates the
car-following population's capacity against the field capacity, step 2 fits
one demand level on the first half of the study period with the second half
held out. This document applies the same two steps, through corridor-agnostic
versions of the same scripts, to the US-101 replica of
[M3_US101_VALIDATION.md](M3_US101_VALIDATION.md). The point is to show that
the procedure is a method rather than a fit to one corridor, so **nothing was
retuned beyond the two procedure steps**: same grid, same seeds per grid
point, same corridor for the capacity measurement, same interpolation rule,
same objective, same split rule, and the validation battery unchanged. The
results are published as they came out, including the criterion that got
worse.

Run order, from the repo root:

```
uv run --no-sync python scripts/calibrate_capacity.py --source artifacts/idm_us101.json \
    --fd artifacts/fd_us101.json --lanes 5 --base-scenario scenarios/us101_replica.yaml \
    --episodes data/processed/us101_episodes.pkl --out artifacts/idm_us101_capacity.json --procs 3
uv run --no-sync python scripts/m3_us101_validate.py --arms none      # observed side + boundary snapshot
uv run --no-sync python scripts/fit_demand_scale.py \
    --scenario runs/m3_us101/us101_replica_with_boundary.yaml \
    --fleet-artifact artifacts/idm_us101_capacity.json \
    --observed runs/m3_us101/observed_us101.json --observed-key segment_speeds_fine_ms \
    --window-s 150 --span-m 640 --n-segments 4 --x-offset-m 640 \
    --out artifacts/demand_scale_us101.json \
    --write-scenario scenarios/us101_replica_calibrated.yaml --name us101_replica_calibrated
uv run --no-sync python scripts/m3_us101_validate.py --arms with_boundary calibrated \
    --artifact-out artifacts/us101_validation_calibrated.json
```

## 1. The generalised scripts

`scripts/calibrate_capacity.py` and `scripts/fit_demand_scale.py` take every
corridor-specific input as an argument (the source population, the FD
artifact or an explicit target, the lane count, the base scenario, the
episode cache; the scenario YAML, the observed table, the sim-to-observed
geometry, the fit/holdout windows). They import the I-24 scripts' pure
helpers unchanged and reimplement only what the I-24 scripts do inline in
`main()`. `tests/test_calibration/test_calibration_procedure.py` checks that
the generalised interpolation rule on the recorded I-24 capacity table
(`artifacts/idm_i24_capacity.calibration.json`) rebuilds the committed
`artifacts/idm_i24_capacity.json` (f\* = 0.8749, mean T to 1e-9), and that the
generalised inflow scaler at the recorded I-24 level rebuilds
`scenarios/i24_replica_speedcal.yaml`'s inflows from
`scenarios/i24_replica_corrected.yaml`. The I-24 simulations were not rerun.

## 2. Step 1 — capacity calibration

**Target.** The US-101 fundamental diagram (`artifacts/fd_us101.json`,
docs/M2_RESULTS.md §4) has q_max = 2,097 veh/h/lane with a bootstrap 95% CI
of 2,068–2,130; the procedure takes the lower bound, **2,068 veh/h/lane**.
Two caveats travel with that number. M2's stated FD caveat concerns the free
branch (v_f is the fastest *observed* operation, not free flow) and does not
touch q_max. But q_max is the 95th-percentile flow of 30 s × 50 m per-lane
Edie bins — an upper-tail statistic of very short bins, which tends to
overstate sustained capacity — and the site's own sustained section flows
are 1,440–1,710 veh/h/lane (docs/M3_US101_VALIDATION.md §3). On I-24 the
target was a tracked *lower* bound and f\* was conservative; here the target
is, if anything, high, and f\* is correspondingly aggressive. The procedure
does not know that; it is reported here.

**Measurement.** A straight five-lane corridor, 4 km, no ramps, no boundary,
the replica's fleet and step settings (`scenarios/us101_replica.yaml`), driven
at a saturating 2,400 veh/h/lane for 30 simulated minutes (5 warm-up), two
seeds per grid point, throughput at run x = 3 km — the I-24 settings with the
lane count changed (`artifacts/idm_us101_capacity.calibration.json`). No grid
point was demand-limited (inserted 0.77–0.90).

| T scale | Mean T [s] | Capacity [veh/h/lane] | Mean speed at 3 km [m/s] |
|---|---|---|---|
| 1.00 | 1.285 | 1,823 | 15.5–16.6 |
| 0.95 | 1.221 | 1,881 | 15.0–16.4 |
| 0.90 | 1.157 | 1,939 | 15.6–16.4 |
| 0.85 | 1.093 | 1,996 | 15.5–16.2 |
| 0.80 | 1.028 | 2,062 | 15.5–16.6 |
| 0.75 | 0.964 | 2,115 | 15.5–16.4 |

Interpolating to 2,068 veh/h/lane between T × 0.80 (2,062) and T × 0.75
(2,115) gives **f\* = 0.7936, T = 1.285 → 1.020 s**
(`artifacts/idm_us101_capacity.json`; covariance and the other means
unchanged). The scaled T stays inside the CLAUDE.md §3.1 calibration range
(0.8–2.2 s) but near its low end.

**Cost.** Unlike I-24, the trade-off is real: the population-mean gap RMSE
over 1,500 seeded car-following episodes (`data/processed/us101_episodes.pkl`,
seed 7) goes from **6.532 m to 6.976 m** (+6.8%). On I-24 a 12.5% cut in T
cost nothing (5.313 → 5.294 m) because the fitted mean was not the
population's best following headway; on US-101 a 20.6% cut does cost, which
says the US-101 episode fit identified the population mean T better — or
that the target overshoots the fleet's actual capacity by more. The derived
artifact's `holdout_gap_rmse_m` (6.437 m) is inherited from the source and
describes the source population; the cost of the scaling is in the sidecar.

## 3. Step 2 — demand level

**Split.** The I-24 fit used the first hour of a two-hour study period.
US-101 period 1 is 952.8 s and the battery compares three full 300 s windows
(0–900 s), which cannot be halved at the window level. The fit objective
therefore uses the same 160 m segments on **150 s windows: windows 0–2
(0–450 s) fitted, 3–5 (450–900 s) held out** — the procedure's default split
(first half / second half) at the finest resolution that still averages
~100 vehicle-samples per bin. The observed table
(`segment_speeds_fine_ms` in the observed cache, embedded in the fit
artifact) is computed by the battery's own binning function, and the
battery's criteria are evaluated on its unchanged 300 s windows. Note what the
split means physically: the recording's downstream breakdown arrives at
wall ~750–810 s (M3 §2), so the fitted half is the pre-breakdown state with
the boundary at 17–20 m/s and the held-out half contains the breakdown.

**Scenario.** The with-boundary replica (the M3 arm that develops
congestion at all) on the step-1 population, mainline inflow × s; the
measured boundary schedule is unchanged. One seed per grid point (the
scenario's first replicate seed), coarse grid then ±3 × 0.025 around the
best, ties toward the smaller level (`artifacts/demand_scale_us101.json`):

| Level s | Inserted | RMSPE 0–450 s (fit) | RMSPE 450–900 s (held out) |
|---|---|---|---|
| 0.60 | 0.999 | 0.800 | 1.493 |
| 0.70 | 1.000 | 0.904 | 1.357 |
| 0.80 | 1.000 | 0.649 | 1.263 |
| 0.90 | 0.999 | 0.644 | 0.991 |
| 1.00 | 1.000 | 0.522 | 0.838 |
| 1.10 | 1.000 | 0.378 | 0.385 |
| 1.125 | 0.999 | 0.354 | 0.361 |
| 1.15 | 0.998 | 0.300 | 0.329 |
| 1.175 | 0.995 | 0.342 | 0.344 |
| 1.20 | 0.993 | 0.314 | 0.342 |
| 1.225 | 0.975 | 0.292 | 0.290 |
| **1.25** | 0.959 | **0.291** | **0.315** |
| 1.275 | 0.953 | 0.341 | 0.289 |
| 1.30 | 0.924 | 0.328 | 0.309 |

**s = 1.25** is the fit and `scenarios/us101_replica_calibrated.yaml` (config
hash `2c07a60447ca`, seeded=False) is the calibrated arm. Two readings of
this table.

* The level is not sharply determined. From 1.15 to 1.275 the fit RMSPE
  moves between 0.29 and 0.34 non-monotonically — steps of the size a single
  seed produces — so the level is known to about ±0.06, and the held-out
  half scores the same 0.29–0.34 across that plateau. Below 1.1 the
  corridor is too fast in the first half and the error climbs steeply; that
  end is well determined.
* The level is **25% above the measured counts**, and those counts are
  complete camera counts of every mainline vehicle at the upstream boundary
  (docs/M2_RESULTS.md §5.1), not the tracked lower bounds I-24's coverage
  made of its counts. On I-24 the fitted level corrected a known
  undercount; here it cannot be correcting a count. What it corrects is the
  replica's missing in-span bottleneck: at s = 1.0 the calibrated fleet runs
  the upstream segment at 18–20 m/s in the first half while the recording
  holds 10 m/s there (the on-ramp merge at 150–165 m, docs/M3_US101_VALIDATION.md
  §6.3), and the only lever the procedure has is density. It buys the
  observed upstream speed with a queue in the insertion buffer — 4% of the
  scaled demand never enters — and that queue is what §4 pays for in flow.

## 4. The battery on the calibrated arm

`scripts/m3_us101_validate.py` gained a `calibrated` arm that runs a
self-contained scenario YAML as is, and `--artifact-out`, which writes every
arm of the invocation into one durable JSON. The invocation above ran the
uncalibrated with-boundary arm again on the same code as a same-code
baseline, because the `FleetSpec` schema gained lane-change fields since M3
and config hashes moved (`e897b6479ed4` in M3, `ee741c2f0173` now); the
regenerated baseline reproduces M3's published values to the digit, so the
before column below can be read from either. 20 seeded replicates per arm
(`spawn_seeds(42, 20)`, the same seeds in both arms), `seeded=False`
throughout, FHWA-style profile (`validation.criteria`), ring rows not
evaluated by this driver and therefore failing (CLAUDE.md §0.1).

| Criterion | Threshold | Before: M3 with-boundary arm (docs/M3_US101_VALIDATION.md §3, `runs/m3_us101/results_with_boundary.json`; regenerated here as `arms.with_boundary`) | After: calibrated arm (`arms.calibrated`) | Result |
|---|---|---|---|---|
| Link-flow GEH | ≥ 85% of bins with GEH < 5 | 55.6% (5/9) | **22.2% (2/9)** | FAIL → FAIL, worse |
| Segment-speed RMSPE | ≤ 15% | 36.6% | **27.9%** | FAIL → FAIL, better |
| Backward wave speed | 14–22 km/h, no seeding | 5.8 km/h (stripe-level 10.7), 20/20 replicates | **5.8 km/h (stripe-level 12.9), 20/20** | FAIL → FAIL, unchanged |
| Ring emergence / dampening | reproduced | not evaluated here | not evaluated here | FAIL (not evaluated) |
| Replicates | ≥ 20 | 20 | 20 | PASS |

Before: 1 PASS / 5 FAIL. After: 1 PASS / 5 FAIL. What moved, and why, bin by
bin (all values `artifacts/us101_validation_calibrated.json`; flows are
replicate means of 5-min crossing counts × 12, sections at 100 / 320 / 550 m,
windows 0–300 / 300–600 / 600–900 s wall).

**Flows.** Observed, before, after [veh/h] and the per-bin GEH:

| Section | Window 1 | Window 2 | Window 3 |
|---|---|---|---|
| 100 m, observed | 8,460 | 8,544 | 7,272 |
| 100 m, before (GEH) | 8,732 (2.9) | 8,468 (0.8) | 7,322 (0.6) |
| 100 m, after (GEH) | 10,164 (17.7) | 9,838 (13.5) | 8,485 (13.7) |
| 320 m, observed | 8,004 | 8,436 | 7,608 |
| 320 m, before (GEH) | 8,651 (7.1) | 8,502 (0.7) | 7,087 (6.1) |
| 320 m, after (GEH) | 10,055 (21.6) | 9,830 (14.6) | 7,993 (4.4) |
| 550 m, observed | 7,632 | 8,520 | 7,752 |
| 550 m, before (GEH) | 8,582 (10.6) | 8,489 (0.3) | 6,880 (10.2) |
| 550 m, after (GEH) | 9,884 (24.1) | 9,813 (13.5) | 7,732 (0.2) |

Two effects with opposite signs. Step 1 did what it was meant to: M3 §3
attributed the window-3 failures at 320 and 550 m to the queue discharging
at IDM-calibrated headways below the observed discharge (deficits of 521 and
872 veh/h); on the calibrated arm those two bins are the two that pass
(7,993 vs 7,608, GEH 4.4; 7,732 vs 7,752, GEH 0.2). Step 2 then broke the
other seven: with 25% more demand the sections carry 9,800–10,200 veh/h
against 8,000–8,500 observed before the breakdown, GEH 13–24. Imposing the
observed speed at the boundary never imposed the observed flow (M3 §6.8);
the demand fit, scoring speeds only, is free to overshoot flow, and it does.

**Speeds.** Replicate-mean segment speeds [m/s], rows = windows, columns =
160 m segments from the upstream end:

| | Segment 1 | Segment 2 | Segment 3 | Segment 4 |
|---|---|---|---|---|
| Observed, window 1 | 10.3 | 11.9 | 14.6 | 17.9 |
| Before, window 1 | 16.3 | 15.5 | 14.8 | 13.4 |
| After, window 1 | 14.4 | 13.9 | 13.1 | 11.7 |
| Observed, window 2 | 9.8 | 10.8 | 13.2 | 17.3 |
| Before, window 2 | 14.7 | 14.6 | 14.0 | 12.7 |
| After, window 2 | 12.8 | 12.5 | 11.9 | 10.8 |
| Observed, window 3 | 7.7 | 8.6 | 10.3 | 10.3 |
| Before, window 3 | 12.9 | 11.2 | 9.1 | 7.0 |
| After, window 3 | 8.2 | 7.2 | 6.4 | 5.9 |

The RMSPE improvement is a level effect, not a shape effect. Averaged over
windows the recording runs 9.3 → 10.4 → 12.7 → 15.2 m/s from upstream to
downstream (slow at the merge, fast at the exit); the baseline runs
14.6 → 13.7 → 12.6 → 11.0 and the calibrated arm 11.8 → 11.2 → 10.5 → 9.5 —
the gradient is still reversed, only lower. The calibrated arm now matches
the recording's window means in the first half (13.3 vs 13.7, 12.0 vs 12.8)
and over-congests the third window (6.9 vs 9.2 m/s), which is the held-out
breakdown half arriving on top of a corridor already 25% over its measured
demand. The largest relative errors move from the upstream segments
(+50–68% before) to the downstream ones (−35 to −43% after).

**Waves.** The standard-detector front is the boundary-queue front pinned at
the site edge in both arms (5.85 → 5.83 km/h), which the M3 doc and
[WAVE_SPEED_DIAGNOSIS.md](WAVE_SPEED_DIAGNOSIS.md) already identify as a
640 m-site artifact, so the criterion is unchanged. The internal stripes
move: stripe-level fronts 10.7 → 12.9 km/h (observed 15.6), and
`compute_metrics` over the full network extent 7.4 → 11.5 km/h
(95% CI 10.2–12.8), wave count 1.65 → 3.30 per run, amplitude 10.7 → 8.5 m/s.
Newell's `(s0 + L)/T` on the calibrated population's own parameters (s0 =
2.02 m, T = 1.02 s, L = 4.5–5 m) is 23–25 km/h — the shortened headway pushed
the fleet's intrinsic congested wave speed *above* the band, and the
corridor still measures 6–13 km/h. That is the diagnosis of
WAVE_SPEED_DIAGNOSIS.md restated with a different fleet: on this site the
measured wave speed is set by the site length and operating density, not by
the car-following parameters.

**Replicate metrics** (full network extent, mean and 95% CI over 20 seeds):
throughput at the middle section 7,693 → 8,879 veh/h; mean travel time over
the span 49.3 → 59.6 s, p90 71.3 → 105.8 s; σ_v (spatial) 3.55 → 3.23 m/s;
fuel 66.4 → 74.6 ml/veh-km; demand realised 99.99% → 96.55% (minimum 95.9%).

## 5. What the two corridors say together

The procedure produced the same shape of result on both corridors, which is
the point of running it twice without retuning.

1. **Step 1 is well posed when the target is.** On I-24 it lifted the
   fleet's capacity from 1,631 to the tracked 1,775 veh/h/lane at no cost in
   following behaviour; on US-101 it lifted 1,823 to a 2,068 target that is
   probably above the road's sustained capacity, at a 6.8% cost in gap
   error, and closed the one flow deficit the baseline had (the window-3
   discharge). The step is only as good as the field capacity it is given;
   a short-bin 95th percentile is a weaker target than a sustained count,
   and the next US-101 iteration should take the target from the site's
   sustained section flows or a longer-bin FD.
2. **Step 2 converges on the missing bottleneck, on both corridors.** On
   I-24 the fitted level left a spatial residual at the Old Hickory merge
   (I24_CAPACITY.md §5–6); on US-101 it left the reversed speed gradient at
   the on-ramp merge and bought the level with flow the road never carried.
   A demand level cannot stand in for a merge; the replica needs the
   auxiliary lane and the ramp inflow (M3 §6.3), and then the lane-change
   parameters the I-24 merge diagnostic named (`lc_cooperative`,
   `lc_assertive`, `lc_speed_gain`, now exposed in `FleetSpec`) fitted the
   same way — first half in, second half out.
3. **The wave-speed criterion is not moved by either step** on this site,
   as WAVE_SPEED_DIAGNOSIS.md predicted; a criterion that a 640 m window
   cannot measure is not a calibration target.

Nothing in this document is a pass. The calibrated arm fails GEH, RMSPE and
wave speed as the baseline did, one criterion better and one worse, and the
reasons are structural and stated. The procedure is reproducible from the
five artifacts named at the top; each number above is read from them.

## 6. Limitations

* One seed per grid point in step 2, as on I-24; on a 640 m site that noise
  is visible in the grid and the level is determined to about ±0.06.
* Step 1's target is an upper-tail statistic (see §2); the derived
  population's T = 1.02 s sits near the bottom of the calibration range and
  its episode cost is real.
* The split halves a 15-minute period; the held-out half contains the
  downstream breakdown, so the out-of-sample score is a harder test than
  the fitted one, and neither half is long enough to average out a single
  event.
* All M3 §6 limitations carry over unchanged: 640 m site, boundary-condition
  epistemics, missing on-ramp and auxiliary lane, raw-NGSIM noise, v_f
  identifiability, warm-up versus history, 9-bin GEH.
* `runs/` is regenerable and pruned freely; the observed cache and the
  with-boundary snapshot are rebuilt by `scripts/m3_us101_validate.py --arms
  none` in a few minutes, and every number here also lives under `artifacts/`.
