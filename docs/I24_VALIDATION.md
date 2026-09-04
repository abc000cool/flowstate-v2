# I-24 replica validation — observed vs simulated

**Date:** 2026-09-03 (rerun the same day on the capacity-calibrated population, §0) ·
**Scenarios:** `scenarios/i24_replica.yaml` (demand as tracked, config `17e9e80ffd83`),
`scenarios/i24_replica_corrected.yaml` (demand ÷ apparent coverage, config `de82b62e4ef6`),
`scenarios/i24_replica_speedcal.yaml` (coverage-shaped demand at the fitted level 0.85, config `b072d754492d`) ·
**20 seeded replicates per arm** (`spawn_seeds(42, 20)`) · **Artifacts:** `artifacts/i24_validation_tracked.json`,
`artifacts/i24_validation_corrected.json`, `artifacts/i24_validation_speedcal.json`,
`artifacts/i24_validation_observed.json`, `artifacts/i24_validation_waves_relative.json`,
`artifacts/i24_replica_inputs.json` · **Scripts:** `scripts/i24_build_replica.py`
→ `scripts/i24_validate.py` → `scripts/i24_validation_figures.py`, `scripts/i24_report.py`.
The original battery on the uncalibrated population (configs `5e15ca999c19` / `a3efae6955bd`,
artifacts at commit `34d215b`) is kept below from §1 on as the record of what changed.

This is the ROADMAP §1.4 result: the criteria battery of CLAUDE.md §7.1 on the
flagship corridor, with every failure and its cause, and the explicit test of
the prediction made in [WAVE_SPEED_DIAGNOSIS.md](WAVE_SPEED_DIAGNOSIS.md). All
runs are `seeded=False`: the measured boundary, the ramp demand and the fleet
calibration are data-derived inputs, not shocks. Read
[I24_DATA.md](I24_DATA.md) first — its §4 (the instrument tracks roughly half
of vehicle-time in the peak) is the reason there are two arms.

**Headline, stated up front.** After the FHWA Vol. III calibration steps
([I24_CAPACITY.md](I24_CAPACITY.md): capacity first, then demand), the replica
still **fails** the GEH, RMSPE and wave-speed criteria in all three demand
arms — **3 PASS / 3 FAIL** per arm on the six criteria that are evaluated,
plus a seventh row (the sensitivity grid) that waits for the flagship sweep.
The two ring rows now pass inside the battery (20/20 seeds each) instead of
being marked FAIL for want of evaluation. On the physical rows the
calibration moved every number toward the recording without reaching it:
speed RMSPE 36.8% → 33.7% (corrected arm), backward fronts 8.7 → 10.4 km/h
with the standard detector and 12.3 → 14.2 km/h with the stripe detector —
the latter now inside the 14–22 km/h band against 16.0 observed — and the
fitted arm inserts 95.5% of its demand where the corrected arm inserts 81%.
The wave-speed prediction is therefore **still not confirmed on the
corridor** by the criterion's detector, and what blocks it is now specific
and local: the Old Hickory merge holds a standing queue at the entry
(§0.3), which caps the speed criterion near 35% however demand is set.

## 0. Rerun on the capacity-calibrated population (three arms)

All three arms use `artifacts/idm_i24_capacity.json` (mean T 1.322 s,
[I24_CAPACITY.md](I24_CAPACITY.md) §4) and the same observed side as §1.
Rows as evaluated by `validation.criteria` (`fhwa_default` profile; the
GEH row of each arm is scored against the count table matching its own
demand assumption, both tables are in the artifacts).

### 0.1 Criteria

| Criterion | Tracked demand | Coverage-corrected demand | Fitted level (speedcal) | Threshold |
|---|---|---|---|---|
| Link flows, GEH < 5 on ≥ 85% of link-hours | 24.3% **FAIL** | 11.8% **FAIL** | 15.3% **FAIL** | ≥ 85% |
| Segment-speed RMSPE ≤ 15% | 187.8% **FAIL** | 33.7% **FAIL** | 36.0% **FAIL** | ≤ 15% |
| Backward wave speed 14–22 km/h (standard detector) | 7.9 km/h **FAIL** | 10.4 km/h **FAIL** | 9.9 km/h **FAIL** | 14–22 |
| Ring emergence (20 seeds, ring-gate checks) | **PASS** 20/20 | **PASS** 20/20 | **PASS** 20/20 | every seed |
| Ring dampening (20 seeds) | **PASS** 20/20 | **PASS** 20/20 | **PASS** 20/20 | every seed |
| Replicates ≥ 20 | **PASS** | **PASS** | **PASS** | ≥ 20 |
| Sensitivity grid published with CIs | not evaluated (sweep in progress) | | | 24 cells |

Demand realised: tracked 100%, corrected 81.3%, speedcal 95.5%.

### 0.2 Waves, metrics and speeds

| | Tracked | Corrected | Speedcal | Observed |
|---|---|---|---|---|
| Backward fronts, standard detector [km/h] | 7.9 | 10.4 | 9.9 | 14.2 (median 17.5) |
| Backward fronts, 25 km/h stripe detector [km/h] | 7.4 | 14.2 | 14.4 | 16.0 |
| Wave components per replicate (standard) | 21.6 | 8.1 | 14.2 | 21 |
| Throughput at data x = 2,200 m [veh/h] | 4,024 [4,020, 4,029] | 5,576 [5,552, 5,600] | 5,710 [5,679, 5,741] | 5,820–7,138 (corrected counts) |
| Mean travel time over the span [s] | 248 | 601 | 564 | ≈ 220 free-flow |
| σ_v temporal [m/s] | 4.53 | 4.80 | 4.98 | |
| Fuel [ml/veh-km] | 65.6 | 108.2 | 100.7 | |

Mean segment speed over the study period [km/h], upstream to downstream
(549 m segments):

| Segment start [km] | 0.0 | 0.5 | 1.1 | 1.6 | 2.2 | 2.7 | 3.3 | 3.8 | 4.4 | 4.9 |
|---|---|---|---|---|---|---|---|---|---|---|
| Observed | 36.3 | 32.5 | 29.8 | 31.3 | 37.6 | 36.9 | 37.7 | 29.0 | 30.4 | 33.7 |
| Tracked | 79.3 | 78.0 | 76.2 | 76.8 | 77.2 | 76.0 | 76.0 | 75.3 | 70.5 | 50.9 |
| Corrected | 19.8 | 20.2 | 28.8 | 34.4 | 33.0 | 31.8 | 32.3 | 26.9 | 25.0 | 32.3 |
| Speedcal | 21.4 | 21.5 | 30.7 | 38.4 | 36.7 | 34.9 | 35.5 | 32.2 | 29.0 | 32.1 |

### 0.3 Reading it

* **The tracked arm is still a half-empty road** (76–79 km/h everywhere
  against 30–38 observed): the instrument's counts are a lower bound and
  cannot be used as demand. Its 21 "waves" per replicate are the
  boundary-queue oscillations of §3, not corridor waves.
* **The corrected and fitted arms reproduce the corridor from 2.2 km on**
  within a few km/h and reproduce its stop-and-go pattern; the stripe
  detector puts their fronts at 14.2–14.4 km/h, inside the empirical band,
  against 16.0 observed with the same detector. The criterion uses the
  standard 40 km/h detector, which merges wall-to-wall congestion into few
  components and reads 10 km/h; that reading fails, and stands.
* **The residual is the Old Hickory merge.** Both congested arms run the
  first kilometre at 20–21 km/h against 32–36 observed and the kilometre
  after it faster than observed: a standing queue at the merge where the
  real road's slowest zones are at 1.1–1.6 km and 3.8–4.4 km. The merge
  and diverge edges carry auxiliary lanes in the map, so this is
  merge behaviour (cooperation, the ramp's own corrected inflow), and it
  is the subject of `scripts/i24_merge_experiment.py`
  (`artifacts/i24_merge_experiment.json`, results in
  [I24_CAPACITY.md](I24_CAPACITY.md) §6 when complete).
* **What the calibration bought and did not buy.** Capacity calibration
  raised throughput from 5,266 to 5,576–5,710 veh/h, lifted insertion to
  95% in the fitted arm, and moved the fronts by 2–3 km/h; it did not move
  the speed criterion below 33%, because the remaining error is where the
  queue sits, not how much traffic there is.

## 1. What is compared

**Observed side** (`i24_validation_observed.json`; cached by data hash): the
westbound mainline fragments (lanes 1–4) between 06:30 and 08:30 CST on the
measured span, data x ∈ [0, 5492) m — MM 62.7 to the Bell Road collector road,
3.4 of the instrument's 4 miles.

* **Link flows:** fragment crossings at six high-coverage sections (data
  x = 200, 1000, 2200, 3200, 4800, 5400 m; the coverage holes at 400 and
  2400 m are avoided) × 24 five-minute windows, ×12 to hourly volumes. The
  tracked counts are lower bounds (I24_DATA.md §4); a second table divides
  them by the per-window apparent coverage (0.52–0.66). GEH per bin,
  replicate-mean simulated vs observed. Both tables are written for both
  arms; the criteria row of each arm uses the counts matching its own demand
  assumption.
* **Segment speeds:** arithmetic mean of sampled speeds per 5-min window ×
  549 m segment (24 × 10 = 240 bins), replicate-mean simulated vs observed →
  RMSPE. Speeds are coverage-robust, so one observed side serves both arms.
* **Waves:** `validation.waves.detect_waves` on 15 s × 75 m fields of the
  span, standard 40 km/h threshold, on both sides; plus the 25 km/h /
  10 s × 50 m stripe variant of the M3 analysis. The standard detector does
  **not** degenerate on this site (unlike the 640 m US-101 window): it finds
  18 backward fronts in the observed field, mean 14.2 km/h, median 17.5,
  61% inside 14–22 km/h (stripe variant: 84 fronts, mean 16.0, median 18.0,
  67% in band; relative-threshold variant at 0.5 × p90: 39 fronts, mean 16.4,
  74% in band).
* **Replicate metrics:** `validation.metrics.compute_metrics` per replicate on
  the measured span (throughput at data x = 2200 m, travel time over the
  span) aggregated by `validation.metrics.aggregate` (t-distribution 95% CIs).

**Simulated side.** The replica of [I24_DATA.md](I24_DATA.md) §6: 13 raw OSM
ways (2.2 km insertion buffer, the 3.4-mile span, a 992 m exit edge carrying
the observed downstream speed schedule at 30 s resolution), the Old Hickory and
Hickory Hollow on-ramps and the Hickory Hollow and Bell Road off-ramps with
data-derived inflows and exit fractions, the `artifacts/idm_i24.json` fleet,
600 s warmup, 7,200 s study period. Coordinates: sim x = 2252 + 0.9806 ×
data x; sim t = data t − 1800 + 600. Every replicate realized 99.98–100% of
planned insertions, including both on-ramps.

Two SUMO lane-change parameters were set from independent observables before
any criterion was evaluated (both measured on the same seed and demand,
`FleetSpec` fields documented in CONTRACTS.md §2 and the CHANGELOG):
`lc_strategic = 5` (SUMO's default eagerness left exiting vehicles stalled at
the Hickory Hollow diverge — 894 stalled 10-s samples in two hours, 29.8 km/h
over the kilometre upstream; 44 samples and 84.7 km/h at 5) and
`lc_keep_right = 0` (the default keep-right rule, which US freeways do not
have, spread vehicle-time 24/25/25/27% across the lanes but crawled at
23–27 km/h in the two right lanes through the Old Hickory merge, 11,535
stalled samples; at 0 the shares are 32/26/22/20% against the observed
30/24/20/26%, with 200 stalled samples).

## 2. Criteria tables — both arms (original population, for the record)

FHWA-style profile (`validation.criteria`, 20 seeds each). Ring-benchmark rows
are CI-gated integration tests, not re-run here; they are reported as **not
evaluated and therefore failing** (CLAUDE.md §0.1).

**Arm 1 — demand as tracked (lower bound; config `5e15ca999c19`):**

| Criterion | Value | Threshold | Result |
|---|---|---|---|
| Link-flow GEH (vs tracked counts) | 25.0% of bins < 5 | ≥ 85% of bins with GEH < 5 | **FAIL** |
| Segment-speed RMSPE | 183.0% | ≤ 15% | **FAIL** |
| Backward wave speed | 7.9 km/h (20/20 replicates; stripe 6.7) | 14–22 km/h | **FAIL** |
| Ring emergence | not evaluated here (CI-gated) | reproduced | **FAIL (not evaluated)** |
| Ring dampening | not evaluated here (CI-gated) | reproduced | **FAIL (not evaluated)** |
| Replicates | 20 | ≥ 20 | **PASS** |

**Arm 2 — demand ÷ apparent coverage (config `a3efae6955bd`):**

| Criterion | Value | Threshold | Result |
|---|---|---|---|
| Link-flow GEH (vs coverage-corrected counts) | 15.3% of bins < 5 (6.3% vs tracked counts) | ≥ 85% of bins with GEH < 5 | **FAIL** |
| Segment-speed RMSPE | 36.8% | ≤ 15% | **FAIL** |
| Backward wave speed (standard detector) | 8.7 km/h (19/20 replicates; stripe 12.3, 20/20) | 14–22 km/h | **FAIL** |
| Ring emergence | not evaluated here (CI-gated) | reproduced | **FAIL (not evaluated)** |
| Ring dampening | not evaluated here (CI-gated) | reproduced | **FAIL (not evaluated)** |
| Replicates | 20 | ≥ 20 | **PASS** |

## 3. What each arm shows

**Arm 2 (coverage-corrected demand)** is where the physics shows. With the
mainline and on-ramp inflows divided by the per-window coverage (0.52–0.66),
the span develops the same kind of field the instrument recorded
(figure below): backward stop-and-go stripes across all 5.5 km from about
10 minutes in, mean segment speed 28.0 km/h against 33.5 observed, 80% of
15 s × 75 m bins below 40 km/h against 57% observed. What still fails, and
why:

* **Insertion caps the demand.** 82.3–84.3% of planned vehicles were
  inserted (99.98–100% in arm 1): the 2.2 km insertion buffer saturates once
  the span's queue backs into it, and the Old Hickory on-ramp delivered
  1,888 of 2,427 planned vehicles in the first seed. Simulated section flows
  are 4,590–5,472 veh/h against 5,820–7,138 coverage-corrected observed
  (median GEH 9–25 per section), so the GEH row fails at 15.3%. This is the
  US-101 insertion-throughput problem at corridor scale (docs/M2_RESULTS.md
  §7.7); the multi-lane insertion scheme that fixed a 640 m entry cannot
  push a saturated corridor's demand through one entry edge.
* **The replica is congested in the wrong places.** The two upstream
  segments (0–1.1 km) run at 19–20 km/h against 32–36 observed — the queue
  from the Old Hickory merge backs up to the entry — while the segments
  between 1.6 and 3.9 km sit at 28–33 km/h, within a few km/h of the
  observed 30–38. Over time the replica goes to 23–27 km/h by 07:00 and
  stays there, where the recording partly recovers at 07:05–07:15 (41 km/h)
  and after 08:15 (43 km/h). RMSPE 36.8% on 240 bins is that spatial and
  temporal mismatch, and it is the same RMSPE the US-101 replica reached
  with its boundary (36.6%).
* **The fronts are slow because the jams are shallow.** The standard detector
  merges the wall-to-wall congestion into few components (98 backward fronts
  over 20 replicates, mean 8.7 km/h, 11% in band) — the pinned-blob
  degeneracy of US-101 §4 again, on a corridor whose field is *more* saturated
  than the recording. The relative-threshold detector (ROADMAP D1; jam below
  0.5 × the field's p90) resolves 1,213 fronts at 12.4 km/h mean, 12.6 median,
  33% in band, against 39 observed fronts at 16.4 mean, 18.0 median, 74% in
  band with the same detector; the 25 km/h stripe variant gives 12.3 vs 16.0.
  On the ring the same fleet produces 12.1 km/h at 40 veh/km, 14.2 at 60 and
  16.4–16.9 at 80–100 (WAVE_SPEED_DIAGNOSIS.md follow-up), so 12–13 km/h says
  the replica's jams sit near 40–60 veh/km per lane while the instrument's
  equilibrium check puts the real peak at 54–57 (I24_DATA.md §4) — consistent
  with the demand it failed to insert.
* **Amplitude and count.** 8.5 waves per replicate with amplitude 8.1 m/s
  (arm 1: 25 shallow boundary-queue oscillations at 14.2 m/s amplitude);
  the observed field's count at the same detector settings is 21 components,
  18 backward.

Throughput at 2200 m is 5,266 [5,246, 5,287] veh/h; mean travel time over the
span 620 s (the free-flow time is about 220 s); fuel 107.9 ml/veh-km.

**Arm 1 (tracked demand)** is the honest lower bound and it fails for one
reason: there is not enough traffic. With inflows of 1,500–4,900 veh/h the
span runs at 67–78 km/h on nine of ten segments (replicate mean; observed
29–38 km/h everywhere) and only the last segment before the boundary drops to
48.7 km/h. The imposed downstream speed does form a queue, but a short one:
its front oscillates within the last kilometre, which is where the 25 waves
per replicate at a mean 7.9 km/h come from — the near-critical-density front
speed the ring diagnostic measured at 40 veh/km (12 km/h) and the US-101
window measured at 10.7 km/h, not the 14–22 km/h of a deep queue. Throughput
at 2200 m is 4,023 [4,020, 4,026] veh/h; the simulated section counts track
the observed *tracked* counts closely in the mean (3,389 vs 3,386 veh/h at
200 m) yet 75% of the individual GEH bins still fail because the observed
crossing counts jump between adjacent sections with coverage, not with
traffic.

## 4. The wave-speed prediction

[WAVE_SPEED_DIAGNOSIS.md](WAVE_SPEED_DIAGNOSIS.md) predicted that on a long,
congested corridor the calibrated fleet's emergent backward waves would fall
in the 14–22 km/h band where the 640 m US-101 window could not. On this
corridor the observed fronts do (median 17.5 km/h with the standard detector,
18.0 with the relative one). The replica's do not, in either arm, with either
detector (`artifacts/i24_validation_waves_relative.json`, 20 replicates per
arm):

| Field | Detector | Fronts | Mean [km/h] | Median | In band |
|---|---|---|---|---|---|
| Observed | standard 40 km/h | 18 | 14.2 | 17.5 | 61% |
| Observed | relative 0.5 × p90 (32.2 km/h) | 39 | 16.4 | 18.0 | 74% |
| Arm 1, tracked | standard | 307 | 7.9 | 9.0 | 9% |
| Arm 1, tracked | relative (46.0 km/h) | 253 | 6.1 | 5.5 | 5% |
| Arm 2, corrected | standard | 98 | 8.7 | 8.0 | 11% |
| Arm 2, corrected | relative (23.8 km/h) | 1,213 | 12.4 | 12.6 | 33% |

Means are means of replicate means; fronts are pooled over replicates. The
prediction is therefore **not confirmed** by this replica, and the reason is
narrower than "calibration": the same fleet does reach the band on a ring once
density exceeds ~80 veh/km per lane, and this replica's congestion — capped by
insertion at 82–84% of the corrected demand — sits where the ring measured
12–14 km/h. What a passing test needs is now specific: a replica that carries
the full corrected demand (a longer or multi-edge insertion buffer, or the
radar-detector counts that would fix the demand outright), on which the
in-span density reaches the observed 54–57 veh/km per lane.

## 5. Replicate metrics (measured span, 20 seeds, 95% t CIs)

| Metric | Arm 1: tracked | Arm 2: corrected |
|---|---|---|
| Throughput at 2200 m [veh/h] | 4,023 [4,020, 4,026] | 5,266 [5,246, 5,287] |
| Mean travel time over the span [s] | 257.4 [254.1, 260.7] | 619.6 [611.9, 627.3] |
| p90 travel time [s] | 333.4 [328.4, 338.5] | 1,018.7 [997.0, 1,040.3] |
| σ_v temporal [m/s] | 4.59 [4.55, 4.63] | 4.49 [4.45, 4.52] |
| Fuel [ml/veh-km] | 65.8 [65.8, 65.9] | 107.9 [107.1, 108.7] |
| Waves per replicate (site-clipped field) | 25.1 [22.6, 27.5] | 8.5 [7.0, 10.0] |
| Backward wave speed [km/h] (`compute_metrics`) | 8.5 [7.9, 9.1] | 10.3 [8.9, 11.6] |
| Wave amplitude [m/s] | 14.2 [13.9, 14.4] | 8.1 [7.6, 8.6] |
| Planned insertions realized | 99.98–100% | 82.3–84.3% |

Observed for scale: mean segment speed 33.5 km/h; tracked hourly counts
3,386–4,130 veh/h across the six sections (5,820–7,138 after the coverage
division).

![observed vs simulated speed fields](figures/i24_validation_fields.png)

![backward wave-front speeds](figures/i24_validation_waves.png)

## 6. Limitations — read before citing any number above

1. **Coverage.** The observed counts are lower bounds and the corrected arm
   rests on a data-derived coverage model (I24_DATA.md §4). Radar detector
   counts for the day would replace both (ROADMAP §6 item 7).
2. **3.4 of 4 miles.** The span ends at the Bell Road collector road because
   OSM edges are not split; the Bell Road on-ramp merges onto the exit edge
   and is not modeled — its effect enters through the observed boundary
   speed.
3. **Ramp inputs are fragment counts** with the same coverage bias; the Bell
   Road exit fraction is taken inside a weaving section.
4. **Passenger-car fleet.** 10% of westbound fragments are semis/trucks; the
   vType is a 5 m car drawn from a passenger-only population.
5. **Lane-change parameters.** `lc_strategic` and `lc_keep_right` were set
   from stall counts and lane shares, not from any criterion, and their
   sensitivity is recorded (CHANGELOG); no other SUMO behaviour parameter was
   touched.
6. **One day, one direction, two hours.** The onset (06:30–06:45) is inside
   the study period; earlier free flow and the full recovery are not.

## 7. Auto-report

`scripts/i24_report.py` runs `validation.report.generate_report` on each arm's
run set: `docs/reports/i24_replica/tracked/report.md` and
`docs/reports/i24_replica/corrected/report.md`, with per-replicate speed
contours, seeds, config hashes, package versions and the
`artifacts/idm_i24.json` provenance.
