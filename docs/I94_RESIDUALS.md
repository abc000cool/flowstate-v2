# What drives the I-94 baseline-gate residuals (2026-10-07)

The calibrated-driver battery (docs/DISCHARGE_CALIBRATION.md §4; scenario
`scenarios/mndot_i94_wb_stpaul_weave_dc.yaml`, hash `db9fbab5fc6e`, 20 seeds,
four hours) still fails the baseline gate of docs/FRISCO_PROTOCOL.md §6:

| check | calibration days | validation days | target |
|---|---|---|---|
| C1 GEH < 5 | 61.8 % | 60.0 % | ≥ 85 % |
| C3 15-min speed RMSPE | 33.9 % | 38.8 % | ≤ 15 % |
| C4 backward wave speed | 4.9 km/h (observed 19.1) | — | 14–22 km/h |
| C6 bottlenecks | phantom S790→S97 in 15 of 20 | pass | — |

This note finds what drives each residual, station by station and hour by
hour, and ranks the fixes. Nothing was simulated, no code, scenario, target or
golden was changed, and nothing was committed. Inputs: the committed
artifacts named in each section, the observations of the protocol's day split
(`artifacts/p1_rehearsal_2026-10-04/observations_{calibration,validation}.json`),
the scenario, the demand artifact, the speed-contour PNGs of the dc and p1
reports, and, for one missing number (C6 day by day), the 5-minute station
table `data/mndot/mndot_i94_wb_stpaul/detectors.csv` (6 MB, S790 and S97
only).

Labels: **[artifact]** read from a committed file; **[computed]** computed here
from committed files (method in §9); **[contour]** read off the committed
speed-contour PNG of one seed (§1.2); **[estimate]** an estimate with its basis
stated.

## 0. In plain English

- **Two things drive C1, about equally.**
  - **The T.H.52 weave carries too little in the first peak hour.** From
    06:30 to 07:30 the real road pushes about 6,150 veh/h into the weave (S790
    plus the T.H.52 entrance count). The model holds S790 at 3,780–4,010 veh/h
    in every seed, so at most about 5,140 veh/h enter. Its queue forms 10–15
    minutes early and is deeper, and flows are 5–28 % too low from S1066 to
    S97 in that hour. This is 47 % of the failing station-hours on
    calibration days (54 % on validation days).
  - **Ramp inputs that do not balance.** After 07:30 the model's own S790
    error is small (−101 veh/h), but the stations upstream inside its queue
    are 11–30 % too high (S1067–S1948) and S791 is 9–14 % low. The cause is
    an inflated Mounds Blvd exit: in 07:30–08:30 the model removes 1,330
    veh/h more than the counts between S1948 and S791, so the queued section
    upstream of it carries too much flow. This is 48 % of the failing station-hours on calibration days
    (41 % on validation days).
  - The upstream boundary is fine (1.5 %).
- **The Mounds Blvd exit is inflated, and the cause is two detector
  problems, not the exit itself.** The T.H.61 NB ramp detector reads 214–475
  veh/h more than the mainline gains across its bracket; the demand step
  carries that excess to the Mounds exit. S792 (two of three loops, ×1.5)
  reads 330–690 veh/h below S791 downstream of it, so the Mounds exit takes
  that too, and the same amount comes back in on the Mounds/Kittson entrance
  (planned 876–1,307 veh/h against its passage loop's 552). Between S1948 and
  S791 the model loses 1,126–1,611 veh/h; the counts lose 281–526.
- **C3:** about one fifth of the error is speed outside any queue: the model
  drives 82–87 km/h where people drive 103–115 km/h (speed factor 1, posted
  55 mph). The rest is the queue: too far upstream at its peak (to S1065
  instead of S1067), too slow inside (S1070–S790 at 15–30 km/h against
  25–58), clearing too early at S1068–S1069 and too late at S1070–S1948.
  Almost all of the error is "too slow".
- **C4 measures the wrong thing on this corridor.** The 4.9 km/h is the speed
  at which the queue's tail grows upstream. The observed tail grows at
  4.6–5.7 km/h, so the model gets that right. The observed 19.1 km/h is the
  speed of stop-and-go waves *inside* the queue, from detrended detector
  cross-correlation. The model does make those waves; on the one seed that
  can be read, the same stack detector restricted to the queue reads about
  14.5–14.8 km/h: inside the band, at its edge, and slower than observed.
- **C6's phantom is the real T.H.52 bottleneck, not the 6th St defect.** On
  4 of the 5 calibration days S790→S97 is an active bottleneck for 45–105
  minutes. On the 5-day mean the rule misses it by two windows, because S97 is
  itself slow on some days and averaging blurs the speed difference. The
  model's S790 is also 20–50 km/h too slow, so its version is stronger and
  lasts longer.
- **Allowed calibration helps but cannot pass the gate** [estimate].
  - Consistent ramp inputs are worth about +7 to +10 pp on C1 on calibration
    days (+2 to +3 on validation days).
  - The speed factor is worth about −3 to −4 pp on C3.
  - Getting C1 to 85 % and C3 to 15 % also needs the T.H.52 throughput at the
    peak, which no calibration knob reaches. It needs a measurement and an
    amendment.
- **Two current inputs use validation days:** the demand was built from all
  nine days, and the driver check that recommends the speed factor read all
  nine days. Both should be rebuilt on the five calibration days before
  anything else is changed.

## 1. Method and what it cannot see

### 1.1 Flows (C1)

The committed per-seed records (`per_seed[*].link_hours` in
`artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc{,_gated}.json`) hold
each seed's simulated hourly volume at every station for the hours starting
06:30, 07:30 and 08:30. The gate anchors its hours at 06:00, and its
per-station-hour values are not committed. The breakdown below therefore
compares the committed 06:30-anchored hours with the calibration-day and
validation-day observations aggregated to the same hours. It reproduces the
committed nine-day `obs_veh_h` exactly, and gives GEH < 5 on **53.6 %**
(calibration) and **49.8 %** (validation) of station-hour-seeds, against the
gate's 61.8 % and 60.0 % [computed]. The patterns and the cause shares are
used below. The absolute levels are not the gate's.

### 1.2 Speeds (C3, C4, C6)

Simulated point speeds per station are not committed. The one committed view
of simulated speed is the contour PNG of seed 6914975401685141156 in each
report (`docs/reports/mndot_i94_wb_stpaul_weave_xlsfg_{dc,p1}/`). It was
digitized:
- axis ticks give the pixel-to-(x, t) map;
- the colour bar, read pixel by pixel, gives the colour-to-speed table;
- the result is resampled to the stack detector's 15 s × 75 m bins.

**Check.** The criterion's own stack detector on the digitized field reads
4.99 km/h, against the committed 4.98 km/h for this seed; on the p1 contour it
reads 6.32 km/h against 6.33 [computed]. The digitized field is faithful at
the scale that detector uses.

**Bias.** The contour shows the mean of trajectory samples in each bin. In
stop-and-go traffic slow vehicles contribute more samples, so it reads slower
than a loop, which counts each crossing once. The digitized 15-minute RMSPE of
this seed against the calibration days is 40.6 %, against its committed
criterion value of 31.8 % (the gate's per-replicate list has the same seed
order: correlation 0.85 with the per-seed records). So:
- the shares of the error that sit inside queues are upper bounds;
- the free-flow cells are not biased.

This seed's 31.8 % is close to the 20-seed median of 31.6 % (mean 33.9 %).

## 2. C1: flows

### 2.1 Station by hour, calibration days

Observed mean over the five calibration days against the 20-seed median,
veh/h, and the share of the 20 seeds with GEH < 5 [computed]. The last
column gives the same shares on the validation days.

| station | x m | 06:30–07:30 obs / sim (%) | GEH<5 | 07:30–08:30 obs / sim (%) | GEH<5 | 08:30–09:30 obs / sim (%) | GEH<5 | validation GEH<5, three hours |
|---|---|---|---|---|---|---|---|---|
| S1063 | 1,089 | 3,712 / 3,712 (0) | 1.00 | 3,633 / 3,604 (−1) | 1.00 | 2,798 / 2,862 (+2) | 0.95 | 1.00 / 1.00 / 0.95 |
| S1064 | 2,305 | 3,350 / 3,296 (−2) | 1.00 | 3,077 / 3,040 (−1) | 1.00 | 2,470 / 2,518 (+2) | 0.95 | 1.00 / 1.00 / 0.90 |
| S1065 | 3,468 | 2,975 / 2,936 (−1) | 1.00 | 2,462 / 2,478 (+1) | 0.95 | 2,120 / 2,155 (+2) | 0.85 | 0.90 / 0.95 / 0.85 |
| S1066 | 4,060 | 3,489 / 3,308 (−5) | 0.75 | 2,890 / 2,991 (+4) | 0.85 | 2,493 / 2,534 (+2) | 0.85 | 0.75 / 0.70 / 0.85 |
| S1067 | 4,970 | 3,931 / 3,552 (−10) | 0.35 | 3,048 / 3,384 (+11) | 0.30 | 2,841 / 2,890 (+2) | 0.85 | 0.35 / 0.15 / 0.85 |
| S1068 | 5,580 | 3,871 / 3,445 (−11) | 0.20 | 2,542 / 3,002 (+18) | 0.10 | 2,724 / 2,740 (+1) | 0.85 | 0.25 / 0.15 / 0.85 |
| S1947 | 6,075 | 4,352 / 3,848 (−12) | 0.05 | 2,848 / 3,381 (+19) | 0.10 | 3,138 / 3,183 (+1) | 0.85 | 0.05 / 0.05 / 0.85 |
| S1069 | 6,684 | 3,731 / 3,210 (−14) | 0.05 | 2,387 / 2,890 (+21) | 0.05 | 2,682 / 2,726 (+2) | 0.85 | 0.05 / 0.05 / 0.85 |
| S1070 | 7,675 | 4,986 / 4,722 (−5) | 0.75 | 3,596 / 4,660 (+30) | 0.00 | 3,784 / 4,092 (+8) | 0.40 | 0.15 / 0.00 / 0.85 |
| S1948 | 8,401 | 4,922 / 4,560 (−7) | 0.45 | 3,669 / 4,686 (+28) | 0.00 | 3,900 / 4,142 (+6) | 0.70 | 0.10 / 0.00 / 0.85 |
| S792 | 8,977 | 4,068 / 3,322 (−18) | 0.00 | 2,702 / 3,058 (+13) | 0.05 | 3,159 / 3,017 (−5) | 0.85 | 0.00 / 0.80 / 0.85 |
| S791 | 9,716 | 4,396 / 3,152 (−28) | 0.00 | 3,388 / 3,075 (−9) | 0.25 | 3,520 / 3,016 (−14) | 0.00 | 0.00 / 0.00 / 0.00 |
| S790 | 10,128 | 4,846 / 3,840 (−21) | 0.00 | 3,965 / 3,864 (−3) | 1.00 | 4,114 / 3,814 (−7) | 0.60 | 0.00 / 0.45 / 0.50 |
| S97 | 11,072 | 4,536 / 3,630 (−20) | 0.00 | 4,077 / 3,831 (−6) | 0.90 | 3,900 / 3,690 (−5) | 0.80 | 0.00 / 0.15 / 0.85 |

### 2.2 Failures by cause

Failing station-hour-seeds (GEH ≥ 5), grouped by what drives them [computed;
the grouping is argued in §2.3–2.5]:

| cause | stations and hours | calibration: failures (share; pp of C1) | validation |
|---|---|---|---|
| **T.H.52 too restrictive, first peak hour** | S1066–S97, 06:30–07:30, all too low | 168 (43.1 %; 20.0 pp) | 186 (44.1 %) |
| **T.H.52 and downstream, later** | S790, S97, 07:30–09:30 | 14 (3.6 %; 1.7 pp) | 41 (9.7 %) |
| *subtotal: T.H.52 throughput* | | *182 (46.7 %; 21.7 pp)* | *227 (53.8 %)* |
| **Inflated Mounds exit acting on the queue** | S1066–S1069, 07:30–08:30, too high | 72 (18.5 %; 8.6 pp) | 78 (18.5 %) |
| | S1070, S1948, 07:30–08:30, too high | 40 (10.3 %; 4.8 pp) | 40 (9.5 %) |
| **Ramp inputs, later** | S1070, S1948, 08:30–09:30 | 18 (4.6 %; 2.1 pp) | 6 (1.4 %) |
| **Ramp inputs and S792's count** | S792, S791, 07:30–09:30 | 57 (14.6 %; 6.8 pp) | 47 (11.1 %) |
| *subtotal: ramp inputs* | | *187 (47.9 %; 22.3 pp)* | *171 (40.5 %)* |
| Upstream boundary demand | S1063–S1065, all hours | 6 (1.5 %; 0.7 pp) | 9 (2.1 %) |
| Queue tail, last hour (mixed) | S1066–S1069, 08:30–09:30 | 15 (3.8 %; 1.8 pp) | 15 (3.6 %) |
| **all** | | **390 of 840** | **422 of 840** |

The two day sets show the same pattern, so the residual is structural, not a
calibration-day fit.

**Why the 07:30–08:30 surpluses are counted as inputs, not as stored vehicles
draining.** Going upstream, each station's error equals the next station's
error plus the error of the ramp volume between them. That is an exact
accounting identity. Inside a queue it is also the causal reading, because a
queued station's flow is set from downstream: the discharge, plus the exits,
minus the entrances in between. At S1069 [computed]:

| term (veh/h) | 06:30–07:30 | 07:30–08:30 | 08:30–09:30 |
|---|---|---|---|
| S790 error (the T.H.52 discharge) | −1,006 | −101 | −300 |
| extra loss between S1948 and S791 (model 1,408 / 1,611 / 1,126; counts 526 / 281 / 380) | +882 | +1,330 | +746 |
| T.H.61 NB entrance excess (model over observed bracket change) | −257 | −562 | −265 |
| Mounds/Kittson entrance excess | −237 | −212 | −204 |
| S1070→S1948 bracket | +98 | +47 | +67 |
| **sum = S1069 error** | **−520** | **+502** | **+44** |

- **In 07:30–08:30 both the model and the road are queued from S1068 to
  S791**, so the reading is causal. The +503 is input-driven; the T.H.52 term
  is only −101.
- **In 06:30–07:30 both are queued at S1069 from about 06:45.** The T.H.52
  term (−1,006) dominates, and the input errors offset about half of it.
- **In 08:30–09:30 the two cancel at S1069**, but not further downstream:
  S791 is −504 and S1070 is +309.
- **Upstream of S1068 there is also a real release.** In hour 2, McKnight Rd
  and Hudson Rd deliver the backlog they built in hour 1 (+235 and +85 veh/h
  over plan, §2.3).
- **The demand was built on nine days, not five.** That leaves a 111 veh/h
  difference at the Ruth St/White Bear bracket.

### 2.3 The T.H.52 weave at the peak

| hour | observed S790 + T.H.52 entrance count (rnd_91040) | simulated S790 (20-seed median) + planned entrance | observed / simulated S97 |
|---|---|---|---|
| 06:30–07:30 | 4,846 + 1,304 = **6,150** | 3,840 + 1,297 ≤ **5,137** | 4,536 / 3,630 |
| 07:30–08:30 | 3,965 + 1,372 = 5,338 | 3,864 + 1,354 ≤ 5,218 | 4,077 / 3,831 |
| 08:30–09:30 | 4,114 + 1,219 = 5,334 | 3,814 + 1,200 ≤ 5,013 | 3,900 / 3,690 |

[computed; the simulated sums are upper bounds, because the entrance departs
only 90 % of its plan, `insertion.ramps`]

- **The model holds S790 at about 3,850 veh/h in every hour.** In the first
  hour every seed is between 3,779 and 4,012, against an observed 4,846.
- **After 07:30 the real weave is limited from downstream.** S97 itself runs
  at 47–54 km/h from 07:30, a queue from beyond the study end. The downstream
  speed boundary reproduces that, and the model is then within 3–7 % at S790
  and S97.
- **The shortfall is the first peak hour:** about 1,000 veh/h (19 %) of
  throughput that the real weave has before downstream congestion caps it.
  The section fixture (docs/WEAVE_LOSS_DIAGNOSIS.md) measured a 465 veh/h loss
  at the 05:30–05:50 demand of 4,877 veh/h. The corridor's peak demand is
  higher, and its peak-hour shortfall is about twice that.
- **Where it shows.** Inside a queue, each upstream station's flow is the
  bottleneck's discharge plus the exits minus the entrances in between. The
  deficit at S790 therefore reaches every queued station: S1067–S1069 are
  10–14 % low in the first hour although their own ramp brackets match the
  inputs.
- **Storage and release, upstream end only.** The model's early queue holds
  back the McKnight Rd (scripted) and Hudson Rd entrances in hour 1. They
  deliver about 200 and 120 veh/h less than planned in hour 1, and about as
  much more in hour 2. The larger hour-2 surplus further downstream (S1068 to
  S1948) is the inputs of §2.2 and §2.4, not stored vehicles:

  | bracket | hour 1 obs / sim / planned | hour 2 obs / sim / planned |
  |---|---|---|
  | S1066→S1067 (McKnight) | +442 / +243 / +449 | +158 / +393 / +177 |
  | S1065→S1066 (Hudson) | +514 / +372 / +494 | +428 / +513 / +407 |

  In hour 3 the simulated change across every bracket from S1063 to S1069
  matches the observed change within 35 veh/h. The upstream ramp inputs are
  right; their timing follows the queue.

### 2.4 Ramp inputs that do not balance

Observed change in station flow across each bracket on the calibration days,
the scenario's planned ramp volume, and the simulated change, in veh/h for
hours 06:30 / 07:30 / 08:30 [computed]:

| bracket (ramp) | observed station change | ramp detector | scenario planned | simulated change |
|---|---|---|---|---|
| S1069→S1070 (T.H.61 NB entrance, `53062592`, detector-scaled) | +1,255 / +1,208 / +1,101 | rnd_88807: 1,716 / 1,683 / 1,315 | +1,733 / +1,696 / +1,338 | +1,512 / +1,770 / +1,366 |
| S1948→S792 (Mounds Blvd exit, `18207912`, by conservation) | −854 / −967 / −741 | rnd_87205 unusable (reads 5,100–5,400) | fraction 0.281 / 0.352 / 0.240 ≈ −1,397 / −1,298 / −948 | −1,238 / −1,628 / −1,125 |
| S792→S791 (6th St left exit, `42165869`) | **+328 / +686 / +361** across an exit | rnd_87209 no data | −51 / 0 / 0 | −170 / +16 / −1 |
| S1948→S791 (both exits) | −526 / −281 / −380 | | | **−1,408 / −1,611 / −1,126** |
| S791→S790 (Mounds/Kittson entrance, `40648744`, by conservation) | +451 / +577 / +594 | passage loop 3244: 552 (05:30–09:30 nine-day mean, docs/ONBOARDING_MNDOT.md §11) | **+876 / +1,307 / +990** | +688 / +789 / +798 |

What this shows:

1. **The T.H.61 NB detector reads more than the mainline gains.** The excess
   is 460 / 475 / 214 veh/h in the scored hours and 321 on the nine-day
   48-window mean. Over four hours that cannot be queue storage in a 1 km
   bracket.
   - The demand step trusted the detector and carried the remainder
     (`bracket_residuals`: S1069→S1070 −317) through S1070→S1948 (−262 = −321
     carried plus that bracket's own +54) to the next exit, Mounds Blvd.
   - Under protocol §2.3 a ramp detector whose bracket does not close is not
     used; the volume comes from the mainline difference.
2. **S792 undercounts.** Its third loop (3240) is excluded and the station is
   scaled ×1.5 from two lanes on every calibration day
   (`source.scaled_station_days`). It reads 328–686 veh/h below S791, with
   only an exit between them.
   - The ×1.5 assumes the missing left lane carries a third of the flow.
     Neighbouring left lanes carry about 40 % (S791 with its order corrected,
     docs/I94_LANE_SHARES.md §1), which would make S792 about 10 % low
     [estimate].
   - The Mounds exit, set by conservation against S792, absorbs the shortfall.
     The positive part of S791 − S792 (+366 mean) is carried to the
     Mounds/Kittson entrance, whose planned volume is 1.6–2.4 times its
     passage loop's four-hour mean (552) and 1.7–2.3 times the observed
     S790 − S791 in the same hour.
3. **The model therefore removes 2.7–5.7 times as many vehicles between
   S1948 and S791 as the counts show** (1,126–1,611 against 281–526 veh/h).
   - The planned Mounds share is 28 / 35 / 24 % by hour; realised, 27 / 35 /
     27 % of simulated S1948.
   - Count-consistent readings give 8–11 % (S1948 − S791, which also includes
     the unmeasured 6th St exit), 17–26 % (S1948 − S792, with S792 low), or
     12–19 % (S1069 + T.H.61 detector − S791). The lane-share note's "about
     20 %" lies in this range.
   - The scenario is outside every one of them. Which count is wrong (S1070
     and S1948, or rnd_88807 and S1069) is not settled by these data. That is
     two unmeasured exits in one bracket, which §2.3 calls unidentified.
4. **Effects.**
   - S1070 and S1948 carry 214–475 veh/h of traffic that does not exist,
     making them +28–30 % in hour 2 together with the release.
   - S791 is low in every hour, because the inflated entrance takes 200–240
     veh/h of the T.H.52 discharge from the mainline.
   - The entrance departs only 77 % of its plan (801 vehicles never depart per
     run, `insertion.ramps`). About 1,350 planned vehicles per run on that
     ramp (+337 veh/h over the loop, four hours) are demand the loop does not
     see.
5. **These errors cancel the T.H.52 deficit in hour 1 and create the
   surpluses afterwards.**
   - In hour 1 the inflated exit drains about 880 veh/h from the queue that in
     reality continues to T.H.52. That is why S1070 and S1948 are only 5–7 %
     low in hour 1 while S791 and S790 are 21–28 % low.
   - In hours 2 and 3, when the T.H.52 error is small, the same drain makes
     the queued stations upstream too high (the chain table in §2.2).
   - Corrected inputs alone would make S1070 and S1948 worse in hour 1 and
     better afterwards (§6, fix 1).

### 2.5 What is not a cause

- **Upstream boundary.** S1063 matches within 2 %, and S1063–S1065 fail in 6
  of 180 station-hour-seeds. Four of the six are one near-lock seed (below);
  five are in the last hour.
- **Given-up weave exits.** Ruth St 2.09 % (598 over 20 runs, about 7.5 veh/h
  per run) and T.H.52 1.31 % (about 13 veh/h) shift the downstream flows by
  far less than one GEH unit.
- **Near-lock seeds.** Seed 3011106312394044631 (departed 0.889) queues back
  to the entry: 1,902 mainline vehicles never depart, four upstream entrances
  deliver 71–75 % of plan, and S1063–S1065 carry 0–576 veh/h in its last
  hour. Seed 677105600768189526 (0.903) delivers 63 % and 74 % of the
  Mounds/Kittson and T.H.52 entrances' plans. They are 2 of 20 seeds, but
  they add failures everywhere in hour 3.

## 3. C3: speeds

### 3.1 Station by 15 minutes, seed 6914975401685141156

Simulated (digitized, bin means) / observed calibration-day mean, km/h
[contour; computed]:

| from | S1063 | S1064 | S1065 | S1066 | S1067 | S1068 | S1947 | S1069 | S1070 | S1948 | S792 | S791 | S790 | S97 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 06:00 | 87/114 | 86/108 | 86/109 | 87/108 | 86/106 | 86/108 | 86/103 | 86/108 | 84/107 | 82/107 | 77/105 | 84/90 | 80/92 | 86/97 |
| 06:15 | 84/115 | 86/108 | 86/109 | 86/108 | 85/106 | 85/107 | 83/102 | 85/107 | 73/103 | 70/102 | 58/98 | 63/87 | **31/81** | 79/93 |
| 06:30 | 82/115 | 83/108 | 84/110 | 82/109 | 80/105 | 82/102 | 72/91 | 81/82 | 45/75 | 37/73 | 39/69 | 20/54 | 26/50 | 80/87 |
| 06:45 | 83/115 | 84/108 | 85/110 | 84/108 | 76/105 | 78/95 | 70/68 | 49/43 | 24/56 | 28/56 | 30/47 | 15/43 | 21/50 | 74/73 |
| 07:00 | 84/115 | 85/108 | 85/110 | 84/108 | 77/102 | 58/83 | 28/67 | 15/50 | 17/53 | 23/56 | 26/47 | 15/41 | 27/48 | 72/75 |
| 07:15 | 82/114 | 83/107 | 82/110 | 62/101 | **15/79** | 13/47 | 10/38 | 8/30 | 14/49 | 21/51 | 26/38 | 17/32 | 25/43 | 63/65 |
| 07:30 | 83/114 | 84/107 | **9/110** | **8/98** | 9/54 | 13/27 | 13/28 | 12/22 | 18/44 | 28/46 | 27/28 | 18/24 | 28/38 | 49/47 |
| 07:45 | 81/115 | 77/108 | **9/111** | 22/101 | 34/59 | **57/28** | 22/28 | 14/19 | 24/41 | 41/43 | 28/28 | 17/26 | 27/38 | 46/49 |
| 08:00 | 84/115 | 83/108 | 50/110 | 43/107 | 41/76 | **69/47** | 49/38 | 18/28 | 24/42 | 22/48 | 29/31 | 16/27 | 22/38 | 51/51 |
| 08:15 | 82/114 | 84/107 | 86/110 | 84/108 | 82/89 | **85/45** | 39/38 | 13/29 | 20/40 | 24/46 | 30/30 | 17/25 | 27/37 | 53/51 |
| 08:30 | 84/115 | 83/107 | 85/110 | 85/108 | 82/102 | 85/53 | **84/42** | 62/36 | 17/43 | 25/46 | 26/33 | 18/28 | 27/41 | 51/54 |
| 08:45 | 83/115 | 84/108 | 85/110 | 84/109 | 84/105 | 85/84 | 83/63 | **85/43** | 34/48 | 22/46 | 31/33 | 15/30 | 27/40 | 61/68 |
| 09:00 | 84/115 | 84/108 | 85/110 | 85/109 | 84/106 | 85/107 | 84/85 | 86/78 | 49/67 | 24/58 | 26/46 | 16/35 | 24/42 | 66/71 |
| 09:15 | 86/115 | 86/108 | 86/110 | 86/109 | 83/106 | 85/108 | 84/102 | 86/94 | **18/84** | **20/80** | 26/64 | 15/42 | 26/46 | 68/75 |

### 3.2 Where the squared error sits

Same seed, 196 fifteen-minute cells against the calibration days. A cell is
"congested" below 40 mph (64.4 km/h) [computed]:

| section | share of squared error | mean error | split by regime |
|---|---|---|---|
| S1063–S1067 (upstream of the observed queue) | 27.5 % | −30 % | both free: 59 cells, 9.9 %, −23 %; observed free, model queued: 9 cells, 14.9 %, −70 % |
| S1068–S1069 (observed queue tail) | 29.1 % | −6 % | both queued: 16 cells, 15.8 %, −23 %; observed queued, model free: 6 cells, 10.5 %, **+70 %** |
| S1070–S1948 (T.H.61 NB to Mounds) | 22.1 % | −48 % | both queued: 19 cells, 16.0 %, −50 %; observed free, model queued: 5 cells, 5.1 % |
| S792–S790 (Mounds to T.H.52) | 21.0 % | −36 % | both queued: 35 cells, 18.2 %, −37 % |
| S97 | 0.2 % | −5 % | reproduced (the downstream speed boundary) |

**By regime, all sections:**

| regime | cells | share of squared error | mean error |
|---|---|---|---|
| both free | 91 | 12.8 % | −20 % |
| observed free, model queued | 22 | 23.9 % | −54 % |
| observed queued, model free | 6 | 10.5 % | +70 % |
| both queued | 77 | 52.8 % | −36 % |

**By time:** 06:00–07:00 16 %; 07:00–08:00 43 %; 08:00–09:00 29 %;
09:00–09:30 12 %.

**The validation days** give the same picture (digitized 41.5 %; S1068–S1069
36 % of the squared error).

### 3.3 Reading

1. **Outside any queue, the model is 20–27 % too slow everywhere.**
   - It drives 82–87 km/h against observed 103–115 km/h. The fleet runs speed
     factor 1, so every driver is capped at the posted 55 mph; the fleet block
     of the dc scenario sets no `speed_factor`.
   - The driver check (`artifacts/p1_rehearsal_2026-10-04/transfer_lanes/transfer_check.md`)
     flagged this as a mismatch: 104.3 km/h observed against 87.9 km/h, and a
     speed factor of 1.23 recommended, inside its measured range 1.09–1.54.
   - These cells are not biased by the digitization. They hold about 4.1 of
     this seed's roughly 19.6 squared-error units at its committed 31.8 %:
     about **21 %**. Making them right would bring this seed to about 28.4 %,
     and the 20-seed mean to about 30–31 % [estimate].
2. **The rest, about 79 %, is the queue.**
   - **Extent.** At its peak (07:15–08:00) the model's queue passes S1065
     (3.5 km) and reaches about 2.4 km, while the observed queue only grazes
     S1067 (4.97 km; 54–59 km/h).
   - **Timing at the head.** The model's head forms at S790 by 06:15
     (31 km/h against 81).
   - **Depth.** Inside the queue the model runs 8–30 km/h against observed
     19–58 km/h. The digitization exaggerates this (§1.2), and a point-speed
     reading would be faster. But at S792 the digitized queue speeds already
     agree with observation (26–31 against 28–33 km/h, 07:30–08:45), so the
     bias is not large enough to explain the 35–50 % deficits at S1070,
     S1948, S791 and S790.
   - **Clearing, upstream end.** The model's queue clears S1068–S1069 30–45
     minutes early (07:45–08:45: 57–85 km/h against 28–53). These are the only
     "too fast" cells, 10.5 % of the error. They are consistent with §2.2: the
     inflated Mounds exit drains the queue's upstream part, which therefore
     carries more flow and dissolves sooner.
   - **Clearing, middle.** The model keeps S1070–S1948 queued to 09:30 (18–24
     km/h against 80–84 km/h at 09:15). That is the section carrying the
     phantom T.H.61 traffic of §2.4.
3. **Too slow, not too fast.** The mean error is −30 %. Apart from the
   early-clearing cells, every section is slow.
4. **A few seeds lift the mean.** Per-replicate C3 (calibration) has a
   median of 31.6 % and a mean of 33.9 %. The near-lock seed
   3011106312394044631 reads 52.8 %, and seed 165503670820534583 reads 42.3 %.

## 4. C4: why the wave speed reads 4.9 km/h

**The detector.** The criterion uses `validation.waves.STACK_DETECTOR`: it
two-way demeans the 15 s × 75 m field and finds the front speed at which the
moving structure lines up best. Its docstring states the limitation that
applies here: "when a growing queue's tail sweeps the field its
(larger-amplitude) shock wins over the stripes inside".

**What it measures on this corridor.**
- **The model's queue tail.** On the digitized seed, the tail (smoothed field,
  contiguous below 15 m/s back from 10.3 km) runs from 10.0 km at 06:20 to
  2.4 km at 07:49: **−4.93 km/h** [contour; computed]. The stack on the whole
  field reads 4.99. The committed per-seed values are 3.1–5.5 km/h (mean 4.9).
- **The observed queue tail.** The first 5-minute window below 40 mph at each
  station runs S790 06:30, S791 06:35, S792 06:40, S1069 06:40, S1070/S1948
  06:45, S1947 06:50, S1068 07:20, S1067 07:30. A line through those gives
  **−4.6 km/h** (calibration days) and **−5.7 km/h** (validation days)
  [computed]. The model reproduces the queue-tail speed.
- **The observed 19.1 km/h** (calibration days, 7 of 13 pairs; 21.3 km/h on
  nine days) comes from a different estimator (`calibration.waves_observed`):
  it cross-correlates 30-second speed series of adjacent stations over
  congested episodes, after removing a roughly 20-minute moving mean. It
  measures oscillations inside the queue and, by construction, not the slow
  queue growth.
- **The model's waves inside the queue.** The contour shows backward-running
  stripes inside the queue. Restricting the same stack function to the queue
  core gives the following [contour; computed]:

  | window | stack speed | contrast | notes |
  |---|---|---|---|
  | 7.4–10.45 km, 06:45–09:30 | **14.74 km/h** | 4.0 | |
  | 7.5–10.4 km, 07:00–09:30 | 14.71 km/h | 3.7 | |
  | 7.6–10.3 km, 07:00–09:30 | 14.68 km/h | 3.5 | |
  | 7.5–10.4 km, 07:30–09:30 | 14.76 km/h | 3.6 | |
  | 7.5–10.4 km, 07:00–08:30 | 14.54 km/h | 3.6 | |
  | 7.5–10.4 km, 08:00–09:30 | 14.47 km/h | 3.8 | |
  | 6.7–10.1 km, 07:15–09:00 | — | 2.7, below the floor of 3 | 14.75 km/h (contrast 3.9) after removing a 20-minute moving mean |
  | narrower: 8.6–10.3 km or 7.5–9.4 km, 07:00–09:30 | — | 2.2 and 2.8, below the floor | |

  Removing a 20-minute moving mean first leaves the reading unchanged
  (14.7 km/h). The p1 reference contour gives 12.8–13.6 km/h.

**Answer.**
- C4 reads the queue tail, not stop-and-go waves, and on that quantity the
  model agrees with the data (4.9 against 4.6–5.7 km/h).
- The model does produce emergent stop-and-go waves on this corridor. On the
  one seed that can be read they run at about 14.5–14.8 km/h: inside the
  14–22 km/h band at its lower edge, and about 4–5 km/h (roughly 25 %) slower
  than the observed 19.1 km/h. This is one seed, read off a PNG. A 20-seed
  reading could land either side of 14 km/h.
- The model's queue is also denser and slower than observed (§3.3), which is
  consistent with slower waves. Why the waves are slower is not established
  here.

## 5. C6: the phantom bottleneck

**Where.** The phantom is pair S790→S97, the T.H.52 weave (`phantoms`:
upstream S790, downstream S97, 15 of 20 replicates longer than 30 minutes, 16
with any activation). In the model it activates at 3,000–3,900 s
(06:20–06:35), stays active 30–60 minutes, and reaches S1066–S1070 (queue-reach
index 3–8, median S1947) [artifact]. It is **not** the 6th St defect: that
trap lane lies between S792 and S791 (9.30–9.55 km), a pair the rule never
flags.

**Why it counts as a phantom.**

1. **On the five-day mean the observed bottleneck falls just short.** The
   observed condition (S790 below 40 mph and S97 at least 20 mph faster) holds
   at 06:30, 06:35 and 06:40, with speed differences of 33–41 km/h. For the
   next 45 minutes the difference is only 20–28 km/h (the rule needs 32.2),
   because S97 is itself slowed on some days by a queue from beyond the study
   end. Three windows of seven fall two short of the 5-of-7 rule. On the
   validation-day mean the same pair holds for five windows (06:30–06:50), so
   it is observed there and C6 passes [computed; artifact].
2. **Day by day it is a recurrent bottleneck.** The rule applied to each
   calibration day (5-minute station table, 06:00–09:30, not quality-masked)
   [computed]:

   | day | active | from | S97 median speed 06:30–08:30 |
   |---|---|---|---|
   | 09-02 | 45 min | 06:30 | 62 km/h |
   | 09-03 | 105 min | 06:30 | 83 km/h |
   | 09-08 | 100 min | 07:05 | 88 km/h |
   | 09-15 | 0 | — | 33 km/h (S97 itself queued) |
   | 09-16 | 45 min | 08:40 | 53 km/h |

   It is active at least 30 minutes on 4 of 5 days. The driver check counted
   291 active windows over the nine days for the same pair.
3. **The model's version is stronger.** The model reproduces S97 (79 / 80 / 74
   / 72 / 63 km/h against observed 93 / 87 / 73 / 75 / 65 for 06:15–07:15),
   but its S790 is 31 / 26 / 21 / 27 / 25 km/h against observed 81 / 50 / 50 /
   48 / 43 [contour]. The speed difference therefore clears 32 km/h for 30–60
   minutes. This is the same T.H.52 shortfall as §2.3: a deeper, earlier queue
   at S790.

So the phantom is the real T.H.52 bottleneck. The rule, applied to a five-day
mean against single replicates, does not register it on the calibration days,
and the model makes it stronger than it is.

## 6. Ranked fixes

Effects are on the calibration-day checks and are estimates from the numbers
above, not runs. "Calibration" means allowed under protocol §7 (or §2.3) on
the five calibration days. "Amendment" means a dated protocol amendment,
written before the run that uses it and reported under both the old and the
new rule. Costs follow the stated recipes: the 35-minute slice probe is about
$0.5, a 20-seed four-hour battery about $1.5.

| rank | fix | type | C1 | C3 | C4 | C6 | cheapest test |
|---|---|---|---|---|---|---|---|
| 1 | **Consistent ramp inputs, built on calibration days.** (a) Demand from `observations_calibration.json`, not nine days. (b) T.H.61 NB from the mainline difference (§2.3: its bracket misses by 214–475 veh/h). (c) S792 out of the balance, or rescaled by a measured lane share. (d) `40648744` = S790 − S791 (≈ its loop). (e) Mounds + 6th St exits taken from S1948 − S791, split by a stated assumption and carried as an uncertain input (§2.3, §8.5) | calibration (§2.3, §7.1). Dropping S792 from *scoring* as well would be a data-defect amendment, reported with and without it (§2.2) | **+7 to +10 pp** (validation days +2 to +3): the 07:30–09:30 errors in the queued section collapse to the S790 error (−101 / −300 veh/h); S1070/S1948 get worse in 06:30–07:30 until fix 6. Static chain, 06:30 anchoring; dynamics can take part back | sign uncertain (±2 pp): the queue would no longer clear S1068–S1069 early, but without the inflated drain it may reach further upstream in hour 1 | 0 | small | local demand rebuild and a per-bracket closure table ($0, seconds); a unit fixture that every bracket closes or is declared; then one arm of a battery (~$1.5) |
| 2 | **Speed factor** ≈ 1.23 on passenger vehicles, from the driver check re-run on the five calibration days only | calibration: §7.2 names this knob for this flag; free-flow speed −15.8 % | ≈ 0 | **−3 to −4 pp** (to about 30–31 %) | ≈ 0 | ≈ 0 | driver check locally ($0, seconds), then a second arm of the same battery (~$1). The T.H.52 fixture already shows no throughput side effect (S1 sensitivity +25 [−8, +58] veh/h) |
| 3 | **T.H.52 section at the corridor's peak demand (diagnostic).** Weave and ceiling arms at the 06:30–07:30 inflows (S790 4,846, entrance 1,304 veh/h) | fixture only, nothing changed | decides fix 6 | | | | `scripts/merge_model_selfcheck.py th52` at that demand, locally ($0). It tells whether the peak shortfall of about 1,000 veh/h is the crossing (as at 05:30) or a plain four-lane limit |
| 4 | **C6 applied per observed day:** an observed bottleneck is one active at least 30 min on at least half the calibration days | amendment | 0 | 0 | 0 | the phantom becomes a matched location (4 of 5 days). Timing then probably fails: the model activates about 06:20; observed per-day starts are 06:30, 06:30, 07:05, 08:40 | local re-score of the per-day data ($0) |
| 5 | **C4 measured the way the observed number is measured:** the observed estimator applied to the model's 30-s virtual-detector speeds at the station pairs, or the stack restricted to the congested core | amendment | 0 | 0 | **4.9 → about 14.5–14.8 km/h** (borderline; observed 19.1) | 0 | fixture with planted stripes inside a growing queue ($0); re-score the next battery's trajectories |
| 6 | **T.H.52 throughput at the peak:** (M1) measure the anticipation reach on I-24 MOTION; (M2) bound the T.H.52 ramp-to-ramp share from a data source; then a model amendment | model change or unmeasured input, by amendment. No §7 knob reaches it: tuning `lookahead_m` or the OD split is ruled out (WEAVE_LOSS_DIAGNOSIS §6.1), W1 failed F3/F5, and the measured merge was not accepted | **up to +22 pp** alone (the 182 T.H.52-driven failures); with fix 1, C1 up to about 97 % [upper bound, 06:30 anchoring] | most of the remaining error (≈ 79 % of the seed's squared error is in the queue); the only route to 15 % | indirect (a shorter queue weakens the tail) | S790 faster, so the phantom shrinks | after fix 3: the M1 extraction (cloud, I-24 data), then a battery (~$1.5) |
| 7 | **6th St left-exit trap** (`--ramps.unset 1001426896,45782590`, the `_netfix` scenarios) | calibration: a map correction backed by OSM and IRIS (§7.3) | small, unknown | small, unknown: the queue's slowest stretch, 9.45–9.9 km, begins where the trap ends | 0 | 0 | stage `p5_i94_netfix_probe` (written, $0.40–0.55); add S791/S790 flows and point speeds to its readout |

**Notes on the ranking.**

- **Fixes 1 and 2 are the allowed calibration and go first.** They can share
  one battery as two arms: inputs only, then inputs plus the speed factor,
  about $2.5 on one VM.
  - Fix 1 is also a correctness fix: it removes the validation-day leak and
    the compensating errors, so that fix 6 is not later judged against them.
- **Together they do not pass the gate:** about 69–72 % on C1 and about
  28–32 % on C3 on calibration days [estimate]. The gate's 06:00 anchoring
  will move the C1 figure.
- **Fixes 4 and 5 correct how two checks are measured**, not the model.
  - They are cheap, but they change checks after results have been seen.
  - The case for each rests on evidence independent of passing: for C4, the
    detector's documented limitation and a different observed estimator; for
    C6, the day-by-day table.
  - Each must be reported both ways.
- **Fix 6 is the only path to C1 ≥ 85 % and C3 ≤ 15 %.** It is the largest
  and least certain item. Fix 3 sizes it for free.
- **Upper-bound arithmetic** (06:30 anchoring):
  - Removing the 182 T.H.52-driven failures leaves 208 of 840 (75 % pass).
  - Removing the 187 ramp-input failures as well leaves the 6 boundary and 15
    mixed late-tail failures, 21 of 840 (97.5 %).

## 7. Calibration days against validation days

**Calibration (allowed), on the five calibration days only** (09-02, 09-03,
09-08, 09-15, 09-16):
- rebuilding the demand from `observations_calibration.json`;
- judging each ramp bracket's closure (T.H.61 NB, S792/S791) and measuring
  S792's lane share;
- re-running the driver check before applying the speed factor;
- the per-day C6 evidence;
- the observed C4 reference (19.1 km/h).

**Already not clean, to fix first:**
- The scenario's demand (`artifacts/demand_mndot_i94_wb_stpaul.json`) was
  built from `data/mndot/mndot_i94_wb_stpaul/observations.json`, which
  averages all nine days, including the four validation days.
- The driver check that recommends the speed factor read all nine days,
  00:00–24:00.

The effect on today's numbers is small (calibration-day and nine-day hourly
means differ by about 0.5–3 %), but any value derived from them carries
validation-day information.

**Fitting to the validation days (not allowed):**
- choosing a speed factor, a demand factor, a Mounds/6th St split or a C4/C6
  rule variant by its effect on validation-day scores;
- using the validation-day columns of this note (§2.1, §2.2, §3.2) to choose
  among fixes. They are reported only to show that the residual pattern is the
  same on both day sets, which it is.
- The validation scoring stays a single, unchanged pass of the calibrated
  model.

**The Amendment-1 drivers stand as chosen** (k = 1, keep-right 0.1). The speed
factor is a separate §7.2 adjustment, made at the same driver settings.
§7.2's "a single corridor-wide adjustment" is read here as one value per knob
for the whole corridor. Whether it also limits the number of knobs is the
owner's call.

## 8. Limits

- **Hour anchoring.** The C1 breakdown uses the committed 06:30-anchored
  hours, not the gate's 06:00 anchoring. Its shares are of 390 failures
  (calibration), not the gate's 321.
- **One seed for speeds.** Station speeds come from one seed's contour, as bin
  means of samples. Shares inside queues are upper bounds (§1.2). The C4 queue
  readings rest on that one seed.
- **Count truth.** Which of S1070/S1948, rnd_88807, S1069 or S792 is miscounted
  is not settled. §2.4 shows that the scenario is outside every count-consistent
  reading, not which reading is right.
- **The C6 table** reads the station table without the quality mask.
- **The queue chain.** Fix 1's C1 estimate assumes that every station from
  S1068 to S791 is queued in 07:30–09:30 in both the model and the road, so
  its flow follows from S790 through corrected ramp volumes. It ignores how a
  queue without the inflated drain would grow in hour 1.
- **Every "expected effect" is an estimate** from these numbers. The batteries
  of fixes 1, 2 and 6 decide.

## 9. Reproduce

The session scripts are not committed. Each step uses only committed inputs.

- **C1 table.**
  - For every seed of `artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_gated.json`,
    take `per_seed[*].link_hours[*].sim_veh_h`.
  - Take the observed hourly mean of the twelve 5-minute windows from
    `window_start_s / 300` in `observations_{calibration,validation}.json`.
  - Compute GEH = √(2(m − c)²/(m + c)), with the median over seeds for the
    table and the per-seed count of GEH ≥ 5 for the cause shares.
- **Brackets.** For the scenario's ramps (positions from the demand artifact):
  - on-ramps: hourly mean of `inflow` × 3,600;
  - off-ramps: hourly mean of `exit_fraction` × the nine-day upstream flow.
- **Chain counterfactual for fix 1.**
  - In 07:30–09:30 (and, as a variant, at S1069–S791 in 06:30–07:30), each
    seed's flow at S1068–S791 is replaced by the calibration-day observed flow
    plus that seed's S790 error against the calibration days. S792 takes S791's
    observed flow plus the same error.
  - It is scored against each day set. Calibration: 53.6 → 63.3 % (60.4 % with
    the hour-1 variant). Validation: 49.8 → 52.5 % (51.8 %).
- **Contours.**
  - Pixel map: x ticks at columns 258.5–756.5 (2–10 km); t ticks at rows
    68.5–504.5 (14,000–2,000 s).
  - Colour bar: the column at x = 905, ticks at rows 83.5–511.5 (25–0 m/s)
    for dc and 81.5–511.5 for p1. Each pixel takes the nearest colour-bar
    colour; a pixel more than 12 RGB units away is treated as a gap.
  - Resampling: 15 s × 75 m by nearest pixel. Station speeds are the mean over
    ±2 columns and the window's rows.
  - Detector: `validation.waves.stack_wave_speed` on the whole field and on
    sub-windows.
- **C6 per day.**
  - S790 and S97 `speed_ms` from `detectors.csv`, 06:00–09:30.
  - Condition: S790 < 17.88 m/s and S97 − S790 ≥ 8.94 m/s.
  - A window counts as active when it lies in a 7-window span with at least 5
    windows meeting the condition.
