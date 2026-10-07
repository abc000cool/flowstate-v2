# I-94 inputs on the calibration days only (2026-10-07)

docs/I94_RESIDUALS.md §7 found that two inputs of the calibrated I-94 scenario
read the protocol's validation days. This note pins the leak down, rebuilds
those inputs from the five calibration days under the protocol's rules
(docs/FRISCO_PROTOCOL.md §2.3, §3.5, §7), re-runs the driver check that
recommends the speed factor, and writes the cloud stage that tests the result.

Nothing was simulated. The only programs run were the demand method, the driver
check (`scripts/transfer_check.py`, 2.5 s, 220 MB) and two netconvert compiles.
No committed scenario, artifact, target or golden changed; everything below is
in new files. Nothing was committed.

Labels: **[artifact]** read from a committed file; **[computed]** computed here
from committed files and the tracked 30-s cache (§8 reproduces it).

## 0. In plain English

- **The leak.** The scenario's mainline inflow, all 17 ramp profiles and the
  downstream speed schedule were built on 2026-09-24 from the mean of all nine
  fetched weekdays. Four of those nine became the validation days when the
  protocol split them on 2026-10-04. The driver check that recommends the
  speed factor also read all nine days, over whole days.
- **What it touches.** The phase-1 and step-3 I-94 gate results, the residual
  analysis, and, indirectly, the Amendment-1 grid and the netfix probe (their
  runs used the same inputs). Nothing is rewritten. The validation-day numbers
  are not a clean holdout; the gate failed on both day sets by wide margins, so
  no verdict changes (§2).
- **Rebuilt inputs.** `scenarios/mndot_i94_wb_stpaul_weave_dc_cal.yaml` is the
  calibrated scenario with every observation-derived series rebuilt from the
  calibration days, and nothing else changed.
  - The ramps are built the way protocol §2.3 says: a ramp without a usable
    detector takes its own segment's mainline difference. The committed
    method instead passed each segment's unexplained remainder on to the next
    ramp; that is what inflated the Mounds Blvd exit and the Mounds/Kittson
    entrance.
  - The Mounds/Kittson entrance now matches its own passage loop (501 against
    544 veh/h on the 4-h mean; it was 889).
- **Two of fix 1's rules need a protocol amendment, so they are not applied.**
  The T.H.61 NB detector's bracket closes inside the data-quality band on 4 of
  5 calibration days, so §2.3 keeps the detector. S792 passes every data-quality
  check, so taking it out of the balance changes the recorded detector choice
  after results were seen. Their numbers are given so the owner can decide
  (§3).
- **Speed factor.** The driver check on the calibration days over the study
  period recommends **1.3026** (observed 108.1 km/h against 87.9 km/h for the
  model), inside the measured range 1.094–1.542. Over whole calibration days it
  would be 1.2346. `_dc_cal_sf` carries 1.3026. The weave and scripted merges
  still assume factor 1 (§5).
- **Next run.** Stage `p8_i94_cal`: three 20-seed batteries with gate and
  report (`_dc_cal`, `_dc_cal_sf`, and `_dc_cal_netfix`, which the netfix
  probe now qualifies). About 1.7–2.4 h billed on an n2-standard-32, about $3
  (§6).

## 1. The leak, precisely

### 1.1 Inputs built from all nine days

| input of `scenarios/mndot_i94_wb_stpaul_weave_dc.yaml` | provenance [artifact] | days |
|---|---|---|
| `network.inflow` (S1063's count, 48 steps) | `artifacts/demand_mndot_i94_wb_stpaul.json`: `observations: data/mndot/mndot_i94_wb_stpaul/observations.json`, `scenario: scenarios/mndot_i94_wb_stpaul.yaml`, `config_hash 30185e013609`; built by `scripts/corridor_demand.py` → `calibration.onboarding.calibrate_scenario` (docs/ONBOARDING_MNDOT.md §3, regenerated in §11 on 2026-09-24) | nine |
| every ramp's `inflow` / `exit_fraction` (17 ramps) | the same record (`ramps[*]`, `bracket_residuals`, `cd_pairs`) | nine |
| `network.boundary.steps` (S97's speed, 48 steps) | the same call (`boundary: "speed_schedule from the downstream station's observed mean speed"`) | nine |
| the speed-factor recommendation 1.23 (docs/I94_RESIDUALS.md §3.3) | `artifacts/p1_rehearsal_2026-10-04/transfer_lanes/transfer_check.json`: `provenance.inputs.dates` = the nine dates; `argv` has no `--dates`, `--start` or `--end`, so whole days (its report: "over 9 day(s), 00:00–24:00 local"); population `scenarios/mndot_i94_wb_stpaul_weave.yaml` (k = 0). Stage `p1_transfer`. `needed` 1.2297 | nine, whole days |
| the 1.245 of docs/PHASE1_REHEARSAL.md §2 | `artifacts/p1_rehearsal_2026-10-04/transfer/transfer_check.json` (station totals, same nine days, whole days; stages `p1_transfer`, `p1b_strategies`). `needed` 1.2448 | nine, whole days |

The nine-day file (`data/mndot/mndot_i94_wb_stpaul/observations.json`) has
`source.dates` 2026-09-01, 02, 03, 08, 09, 10, 15, 16, 17 and `aggregation`
"mean over dates per window". It has no `source.quality`, so it is not
quality-masked either. The validation days (09-01, 09-09, 09-10, 09-17) carry
4/9 of every window's mean.

**How the series reached `_dc`** [computed]:
- `scenarios/mndot_i94_wb_stpaul_weave.yaml` differs from the base only in the
  merge settings of four ramps.
- `_dc` was written by `scripts/apply_driver_calibration.py`, which changes the
  name, the fleet and the `xlsfg` lines.
- `_dc`'s inflow and all 17 ramp series equal the record's exactly; its 48
  boundary steps equal the nine-day S97 speeds exactly. The same series sit in
  `_dc_netfix`, in the `xlsfg` reference of phase 1, and (shifted by 5,400 s)
  in the 35-minute slice.

**Reproduction** [computed]: running `calibrate_scenario` today on the nine-day
file reproduces the committed record's inflow, every ramp series, method and
matched detector, the bracket residuals and the C-D pair exactly. Ramp `x_m`
differ by up to 97 m, because today's compile of the same network is 55 m
longer (chain 11,477 m against 11,422 m). No ramp changes bracket, so no
series changes.

### 1.2 Inputs that do not leak

- The day split, and the calibration-day and validation-day targets
  (`observations_{calibration,validation}.json`, quality-masked).
- The observed wave speed C4 compares with (19.1 km/h, calibration-day
  context).
- The Amendment-1 targets: lane shares on the calibration days
  (`artifacts/driver_calibration_i94_observed_lanes.json`, `dates` = the five)
  and S97's calibration-day discharge (4,490.5 veh/h).

### 1.3 Leaked indirectly (clean targets, nine-day inputs)

- **The Amendment-1 grid** and **the netfix probe** (stage p5) ran the
  35-minute slice, whose series are the nine-day ones.
- **The phase-1 uncertainty and tuning rehearsals** took their driver ranges
  from the nine-day driver check, and ALINEA's density target (19.9 veh/km)
  from `artifacts/fd_mndot_i94_wb_stpaul.json`, fitted on per-lane samples of
  all nine weekdays. Both are labelled rehearsals and state no verdict.

### 1.4 Size

On the calibration days against nine days [computed]:
- S1063 inflow, hourly means from 06:30 / 07:30 / 08:30: 3,712 / 3,633 / 2,798
  against 3,732 / 3,596 / 2,853 veh/h (−0.5 / +1.0 / −1.9 %). The largest
  5-minute step moves 167 veh/h.
- S97 boundary speed: mean absolute change 0.62 m/s (largest 2.4); 08:30–09:30
  mean 66.9 against 63.7 km/h.
- Ramp series: most hourly values move by less than 5 %; the largest moves
  are 10–12 % (the White Bear Ave C-D split at 07:30, 743 → 819 veh/h; the
  T.H.120 exit at 06:30, 410 → 362; the Mounds Blvd exit at 08:30, 979 → 876).

## 2. Which committed results it touches, and how to read them

| result | files | what it used |
|---|---|---|
| phase-1 rehearsal gate | `artifacts/baseline_gate_mndot_i94_wb_stpaul_p1.json`, `artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_p1.json`, `artifacts/p1_rehearsal_2026-10-04/validation_mndot_i94_wb_stpaul_weave_xlsfg_p1_gated.json`, `docs/reports/mndot_i94_wb_stpaul_weave_xlsfg_p1/` | nine-day series |
| step 3 (calibrated drivers) gate | `artifacts/baseline_gate_mndot_dc.json`, `artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc{,_gated}.json`, `docs/reports/mndot_i94_wb_stpaul_weave_xlsfg_dc/`, docs/DISCHARGE_CALIBRATION.md §4 | nine-day series |
| residual analysis | docs/I94_RESIDUALS.md (its "planned" ramp volumes and the 1.23) | nine-day series and check |
| driver grid, netfix probe | `artifacts/driver_calibration_i94.json`, `artifacts/i94_netfix_probe.json`, docs/I94_LANE_SHARES.md | nine-day slice inputs |
| phase-1 strategy rehearsals | `artifacts/uncertainty_mndot_i94_wb_stpaul_p1{,b}_rehearsal.json`, `artifacts/tune_*_p1{,b}_rehearsal.json` | nine-day check and FD |

**How to read them. None is rewritten.**
- **Validation-day numbers** (phase 1: C1 14.5 %, C3 48.7 %; step 3: C1 60.0 %,
  C3 38.8 %) score a model whose boundary and ramp inputs include the
  validation days' own counts at 4/9 weight. They are validation targets
  against partly in-sample inputs, not a clean holdout. The leak is expected to
  make validation look closer to calibration than a clean holdout would. The gate
  failed on both day sets by wide margins, so no verdict changes.
- **Calibration-day numbers** are scores of a model built on a slightly
  different (nine-day) input set. The differences of §1.4 are small; the
  residual patterns of docs/I94_RESIDUALS.md stand.
- **The Amendment-1 choice** (k = 1, keep-right 0.1) was selected on
  calibration-day targets with runs on nine-day inputs. It stands as chosen
  (I94_RESIDUALS §7). Re-running the 50-run grid on the calibration-day slice
  would cost about $0.6 if the owner wants that choice clean as well (§7).

## 3. The rebuild: rules applied and rules that need an amendment

`scripts/i94_calibration_days.py` runs the corridor's own demand method
(`calibration.onboarding.calibrate_scenario`) on
`artifacts/p1_rehearsal_2026-10-04/observations_calibration.json` (the five
calibration days, quality-masked; the script refuses any other set). It copies
only the observation-derived series into a copy of `_dc`. Fix 1's rules
(I94_RESIDUALS §6), one by one:

| rule | protocol | here |
|---|---|---|
| (a) demand from the calibration days, not nine | §3.5, §7.1 | **applied** |
| each unmeasured ramp takes only its own segment's mainline difference (the residuals are not passed on) | §2.3: "the ramp's volume comes from `calibration.ramp_estimation` (mainline differences)", which solves each segment on its own and redistributes nothing | **applied** (`carry_residuals=False`, a new option of the method, off by default) |
| (b) T.H.61 NB (53062592) from the mainline difference, not rnd_88807 | §2.3 uses a detector that passes the data-quality checks and whose segment "closes within the tolerance of `calibration.data_quality`" | **not applied: needs an amendment** |
| (c) S792 out of the demand balance, still scored | §2.2 (a data defect found later is a dated amendment), §2.4 (detector choice recorded before calibration) | **not applied: needs an amendment** |
| (d) Mounds/Kittson entrance (40648744) = S790 − S791 | follows from §2.3: it is the only unmeasured ramp of its segment | **applied** (by the second row) |
| (e) Mounds Blvd and 6th St exits declared an uncertain input | §2.3, §8.5 | **applied** in the scenario headers and the demand record; the sensitivity runs are not made |

**Why (b) needs an amendment** [artifact: `artifacts/p1_rehearsal_2026-10-04/dq/data_quality.json`, `mass_balance.segment_days`]:
rnd_88807 is judged ok on every calibration day, and its segment S1069→S1070
balances inside the band on four of them:

| day | residual veh/h (share of S1069) | band (5 % of each count, linear) | verdict |
|---|---|---|---|
| 09-02 | −304 (−9.9 %) | ±14.4 % | ok |
| 09-03 | −284 (−8.6 %) | ±13.8 % | ok |
| 09-08 | −302 (−10.3 %) | ±14.6 % | ok |
| 09-15 | −426 (−17.5 %) | ±15.5 % | suspect |
| 09-16 | −287 (−10.0 %) | ±14.9 % | ok |

The evidence for an amendment is real:
- the residual has the same sign on every day;
- 27–60 % of its 15-minute periods leave the per-period band;
- with the errors combined in quadrature (independent detectors), the 4-h
  calibration-day mean would not close (−317 against ±261 veh/h).

But the band §2.3 names is `linear` as configured, and by it the detector is
used.

**Why (c) needs an amendment.**
- S792 is judged ok on all five calibration days.
- Its two segments are ok on four of five days (09-15 suspect for both).
- The case against it is a later data-defect finding: two of three loops
  scaled ×1.5, and S791 − S792 = +328 / +686 / +361 veh/h across an exit
  (scored hours; +234 on the 4-h mean).

**What an amendment would say** (a draft, not adopted; for the owner):
1. "A ramp detector whose segment residual has the same sign on every
   calibration day and leaves the quadrature count-error band on the
   calibration-day mean is not used; its ramp takes the mainline difference."
2. "S792 is left out of the I-94 demand balance (two of three loops; reads
   below S791 across an exit); it stays a scored station."

Results would be reported with and without both rules. Their inputs are the
last numeric column of §4.

## 4. Before and after, per ramp and exit

Hourly means from 06:30 / 07:30 / 08:30, then the 05:30–09:30 mean, veh/h.
Exits are shown as the volume they take from the plan's own free flow
(exit fraction × the flow arriving in the plan). Methods are in brackets
[computed; `--table` prints it].

| ramp | committed (nine days) | calibration days, committed rule (residuals carried) | **written: `_dc_cal`** | fix 1 with (b)+(c) (needs amendment; not written) | observed |
|---|---|---|---|---|---|
| S1063 entry inflow | 3,732 / 3,596 / 2,853 / 3,050 | 3,712 / 3,633 / 2,798 / 3,041 | 3,712 / 3,633 / 2,798 / 3,041 | same | S1063 count |
| T.H.120 exit 18279036 [conservation] | 410 / 578 / 348 / 368 | 362 / 556 / 328 / 345 | 362 / 556 / 328 / 345 | same | rnd_88835 dead |
| Hudson Rd exit 18207390 [detector_scaled] | 853 / 986 / 725 / 755 | 886 / 1,016 / 722 / 769 | 886 / 1,016 / 722 / 769 | same | rnd_88833 315 / 582 / 321 / 332 |
| Hudson Rd entrance 18207436 [detector] | 517 / 399 / 375 / 401 | 511 / 400 / 372 / 398 | 511 / 400 / 372 / 398 | same | rnd_88831 511 / 400 / 372 / 398 |
| second Hudson Rd entrance 18207653 [conservation] | 494 / 407 / 371 / 396 | 514 / 428 / 372 / 404 | 514 / 428 / 372 / 404 | same | dead loop |
| McKnight Rd entrance 178547099 [conservation] | 449 / 177 / 357 / 327 | 442 / 188 / 348 / 328 | 442 / 188 / 348 / 328 | same | dead loop |
| Ruth St entrance 745524613 [detector_scaled] | 245 / 286 / 255 / 254 | 268 / 274 / 261 / 259 | 268 / 279 / 261 / 260 | same | rnd_88819 167 / 224 / 207 / 173 |
| White Bear Ave C-D split 18208090 [detector_scaled] | 311 / 743 / 383 / 374 | 329 / 819 / 379 / 396 | 329 / 792 / 379 / 389 | same | rnd_88817 277 / 715 / 357 / 352 |
| C-D re-entry 745524608 [conservation] | 503 / 288 / 438 / 442 | 481 / 306 / 414 / 434 | 481 / 306 / 414 / 434 | same | — |
| T.H.61 exit 18207880 [detector_scaled] | 614 / 487 / 462 / 447 | 620 / 496 / 463 / 452 | 620 / 501 / 463 / 454 | same | rnd_88811 363 / 429 / 387 / 333 |
| **T.H.61 NB 53062592** | 1,733 / 1,696 / 1,338 / 1,498 [detector_scaled] | 1,716 / 1,683 / 1,322 / 1,487 | **1,716 / 1,683 / 1,321 / 1,486** [detector_scaled] | 1,255 / 1,208 / 1,101 / 1,165 [conservation] | rnd_88807 1,716 / 1,683 / 1,315 / 1,482; S1070 − S1069 1,255 / 1,208 / 1,101 / 1,165 |
| **Mounds Blvd exit 18207912** [conservation] | 1,542 / 1,445 / 979 / 1,052 | 1,534 / 1,486 / 876 / 1,040 | **946 / 1,066 / 762 / 733** | 268 / 136 / 182 / 220 | S1948 − S792 854 / 967 / 741 / 679 |
| **6th St left exit 42165869** [conservation] | 61 / 0 / 0 / 125 | 74 / 0 / 12 / 130 | **84 / 0 / 12 / 137** | 268 / 136 / 182 / 220 | rnd_87209 no data; S792 − S791 −328 / −686 / −361 / −234 |
| **Mounds/Kittson 40648744** [conservation] | 876 / 1,307 / 990 / 889 | 855 / 1,263 / 967 / 867 | **451 / 577 / 594 / 501** | 451 / 577 / 594 / 501 | loop 3244 593 / 539 / 612 / 544; S790 − S791 451 / 577 / 594 / 501 |
| T.H.52 entrance 769818012 [detector] | 1,297 / 1,354 / 1,200 / 1,267 | 1,304 / 1,372 / 1,219 / 1,280 | 1,304 / 1,372 / 1,219 / 1,280 | same | rnd_91040 1,304 / 1,372 / 1,219 / 1,280 |
| T.H.52 weave exit 18207598 [conservation] | 1,116 / 881 / 1,095 / 1,106 | 1,103 / 842 / 1,098 / 1,092 | 1,135 / 808 / 1,045 / 1,084 | 1,141 / 858 / 1,084 / 1,100 | — |
| Jackson St exit 82150350 (12th St in OSM) [detector] | 465 / 384 / 327 / 379 | 472 / 379 / 326 / 379 | 486 / 360 / 310 / 375 | 488 / 384 / 321 / 383 | rnd_87221 484 / 390 / 328 / 386 |

The 4-h mean of loop 3244 (the Mounds/Kittson entrance's own passage loop,
read from the 30-s cache for the five calibration days) is the independent
check of (d): 544 veh/h against 501 written and 889 committed. In the fix-1
column the two exits without detectors split S1948 − S791 equally (the
method's rule for two such ramps in one bracket). Under the amendment that
equal split would itself be the documented assumption of §2.3.

**The plan's station flows minus the calibration-day counts** (free flow,
same hours) [computed]:

| station | committed (nine days) | written `_dc_cal` | fix 1 with (b)+(c) |
|---|---|---|---|
| S1069 | +21 / −28 / +49 | 0 / −19 / −8 | 0 / −19 / −8 |
| S1070 | +499 / +460 / +285 | +461 / +456 / +212 | 0 / −19 / −8 |
| S1948 | +563 / +386 / +169 | +525 / +383 / +95 | +64 / −92 / −124 |
| S792 | −125 / −91 / −69 | +433 / +284 / +74 | +651 / +739 / +434 (its own count) |
| S791 | −514 / −777 / −430 | +21 / −402 / −299 | +55 / −84 / −109 |
| S790 | −89 / −48 / −35 | +21 / −402 / −299 | +55 / −84 / −109 |
| S97 | −62 / −69 / −42 | +14 / −310 / −220 | +40 / −65 / −80 |

What this means:
- **Committed.** The T.H.61 NB surplus and S792's shortfall were both pushed
  onto the Mounds exit and the Mounds/Kittson entrance. Those two ramps hid
  them, so S790 matched its count while S791 was 430–777 short.
- **Written (`_dc_cal`).**
  - The Mounds/Kittson entrance matches its loop.
  - The Mounds exit now follows S1948 − S792. It is still too large by
    S792's shortfall, and §3 explains why that is not corrected here.
  - The T.H.61 NB surplus (−320 veh/h on the 4-h mean, not carried) stays on
    the mainline from S1070 onward instead of leaving at Mounds.
  - In hour 1 the two errors cancel at S791; in hours 2–3 S791, S790 and S97
    run 220–400 veh/h short in the plan.
- **Fix 1 with (b) and (c).** Every station is within about 125 veh/h, except
  S792's own count.

These are plan flows, not simulated ones. The battery decides what the queue
does with them. Bracket residuals of the written build (4-h means, recorded,
not carried): S1066→S1067 −8, S1947→S1069 +11, S1069→S1070 −320,
S1070→S1948 +40, S1948→S792 0, S792→S791 +366 veh/h.

## 5. The speed factor (fix 2)

**The re-run** [artifact: `artifacts/i94_calibration_days_2026-10-07/transfer_lanes/`]:

```
scripts/transfer_check.py --corridor-dir data/mndot/mndot_i94_wb_stpaul --lanes-from-cache data/mndot/cache \
  --metro-config data/mndot/config/metro_config.xml.gz --exclude-detectors 3240 \
  --dates 20260902,20260903,20260908,20260915,20260916 --start 05:30 --end 09:30 \
  --scenario scenarios/mndot_i94_wb_stpaul_weave_dc.yaml --out artifacts/i94_calibration_days_2026-10-07/transfer_lanes
```

This is the committed check's per-lane mode, restricted to the calibration days
and to the study period, with `_dc`'s population. It was run on the committed
calibration code (the balance-rule change of §9 set aside for the run; the
check does not import that module), so its `code_dirty` is false.

- **Free-flow speed:** observed 108.1 km/h (95 % interval 107.6–109.0; median
  of 893 light-traffic 5-minute readings at 11 stations) against 87.9 km/h for
  the model at factor 1. Verdict: mismatch (−18.7 %).
- **Recommendation:** speed factor **1.3026** (`needed`; the engine has the
  knob since WP-109). The measured range is 1.094–1.5415: v0 26.9–37.9 m/s
  (mean ± 1 sd of the measured I-24 population) over the 55 mph limit. The
  mean desired speed (v0) cannot close the gap inside its range.
- Capacity and truck share are not available here: the k = 1 EIDM population
  has no simulated capacity sidecar, and loops carry no vehicle class.

**Which reading, decided before running:** the study period. The script's
documented use is "the study's calibration days with `--dates` and its hours
with `--start`/`--end`", and protocol §3.4 fixes the hours. Sensitivity, not
used: whole calibration days give 104.6 km/h → **1.2346**
(`transfer_lanes_wholeday/`). For comparison, the committed nine-day,
whole-day checks gave 1.2297 (per lane) and 1.2448 (station totals).

**Why the study period reads higher.**
- In 05:30–09:30 the light-traffic readings come mostly from S1064–S1069:
  51–210 readings each, at 106–110 km/h.
- S790, S791 and S97 contribute 4–18 readings each, at 93–100 km/h.
- Over whole days the downstream three add about 1,650 readings at
  89–94 km/h.
- So the difference is mostly which windows are light, not faster drivers.
- A single corridor-wide factor (§7.2: no location-specific settings)
  therefore fits the upstream 6 km. It sets the T.H.52 and downtown section's
  free-flow speed about 8–15 km/h above what its loops read in the study
  period, a section that is queued for most of it.

**Protocol.**
- §7.2 allows this as calibration: the driver check flags a free-flow
  mismatch, the passenger speed factor is a named knob, the value is inside
  its measured range, and it is one corridor-wide value.
- `_dc_cal_sf` is therefore written: `_dc_cal` plus `fleet.speed_factor:
  1.3026`.
- Open for the owner, as in I94_RESIDUALS §7: whether "a single
  corridor-wide adjustment" means one value per knob, or one knob in all.
  Amendment 1 already calibrated a_max and keep-right, so this would be the
  third driver setting.

**The weave and scripted merges assume factor 1.**
- WP-109 (CHANGELOG, "Speed factor on the posted limit") added the factor to
  SUMO's vehicles. It left the weave and scripted merges unchanged.
- Their gap predictions, and the scripted merge's speed matching, take a
  vehicle's desired speed as min(maxSpeed, lane limit). Every run with a
  factor says so in its `meta.json` (`microsim.runner._speed_factor_merge_note`).
- On this corridor that covers the Ruth St (745524613) and T.H.52 (769818012)
  weaves and the Hudson Rd (18207436) and McKnight Rd (178547099) scripted
  merges.
- At 1.3026 mainline drivers want up to 32.0 m/s (115 km/h), while those
  models predict and match at 24.6 m/s.

Implications:
1. **The fixture evidence at 1.245.** docs/WEAVE_LOSS_DIAGNOSIS.md §4, arm
   S1, 20 seeds, measured on the T.H.52 section fixture:
   - flow +25 [−8, +58] veh/h, no collision;
   - given-up exits 108 against the reference's 66;
   - stranded-entrant time 28.8 against 18.9 s per run.
   At 1.30 expect the same direction, somewhat larger.
2. **The `_sf` arm cannot be read as the drivers' effect alone.** Any change
   in T.H.52 throughput or merge behaviour mixes the speed factor with this
   mismatch. Read its weave give-up shares (the 2 % threshold; Ruth St was
   already at 2.1 % in step 3), its departed share and C5 beside C1/C3.
3. **What it can be expected to move.** The I94_RESIDUALS estimate for the
   speed factor is about −3 to −4 pp on C3. The free-flow cells it targets are
   read at 103–115 km/h, so the order holds at 1.30. It is not a route to the
   gate.
4. **Not a fix.** The measured merge model honours the factor
   (docs/MERGE_MODEL.md) but is not accepted; nothing here changes a merge
   model.

## 6. The cloud stage `p8_i94_cal`

Added to `scripts/gcp/pipeline_i24.sh` (opt-in, after p7), modelled on
`p4_i94_battery_gate_steps`:

1. `scripts/i94_calibration_days.py --check`: the committed files must be
   what the recipe gives (two netconvert compiles, no simulation), or nothing
   runs.
2. For `_dc_cal`, then `_dc_cal_sf`, then `_dc_cal_netfix` when the rule
   below holds:
   - the 20-seed four-hour battery (`--criteria-profile fhwa_tat3_2004`;
     the battery's own `--observations` are the calibration-day targets);
   - `scripts/baseline_gate.py` on the phase-1 day sets;
   - the gated report.
3. **Netfix rule (fixed before any run).** The probe is complete and, at the
   calibrated drivers (pair `k1.0_kr0.1`) on the stations both networks
   compare, the fixed network has a lower lane-share RMSE, an S97 discharge
   error no larger than the as-built one, and no collision. Today it holds: 8.22 → 7.26 pp,
   0.326 → 0.163, 0 collisions. So all three run.
4. **Outputs** per scenario name N (`mndot_i94_wb_stpaul_weave_xlsfg_dc_cal`,
   `…_dc_cal_sf`, `…_dc_cal_netfix`): `artifacts/validation_N.json`,
   `artifacts/baseline_gate_N.json`, `artifacts/validation_N_gated.json`,
   `docs/reports/N/`. The ingest script now takes `baseline_gate_*_dc_cal*.json`.
5. **Resumable:** `logs/p8_N.battery.ok` with its artifact skips a finished
   battery. Needs no data set; every input is tracked.

**Expected VM time** (per battery: step 3's took 1,599 s and phase 1's
2,356 s, one wave of 20 at 30 processes):

| machine | per battery | gate + report | three scenarios, stage time | billed with boot and setup | cost |
|---|---|---|---|---|---|
| n2-standard-32 (30 processes, one wave) | 27–40 min | ~2 min | 90–125 min | ~105–140 min | ~$2.7–3.6 at $1.55/h |
| n2-standard-16 (`--procs 10`, two waves) | 50–80 min | ~2 min | 155–245 min | ~170–260 min | ~$2.2–3.4 at $0.78/h |

On the n2-standard-16, pass `--procs 10`. The default 14 would put fourteen
4-h runs in 64 GB; ten keeps the same two waves with less memory.

**Launch** (after the new files are committed and pushed; the VM runs
`git archive HEAD`):

```
scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p8 --machine n2-standard-32 --bucket gs://<bucket>/p8 \
  --self-delete --via-bucket --data-set none --cap-min 270 --pipeline-args '--stages "p8_i94_cal"'
```

On an n2-standard-16: `--machine n2-standard-16 --cap-min 480 --pipeline-args
'--procs 10 --stages "p8_i94_cal"'`.

**Reading the results.**
- Compare each `artifacts/baseline_gate_N.json` with
  `artifacts/baseline_gate_mndot_dc.json`: same seeds, same day sets, same
  profile.
- `_dc_cal` against `_dc` is the leak and the §2.3 rule.
- `_dc_cal_sf` against `_dc_cal` is the speed factor, with the caveat of §5.
- `_dc_cal_netfix` against `_dc_cal` is the map fix.
- The validation-day rows of these three are the first I-94 validation
  scores whose model inputs hold no validation-day data. The Amendment-1
  driver choice itself was made on runs with nine-day inputs (§2).

## 7. Needs an amendment or the owner

1. **Rule (b), T.H.61 NB from the mainline difference: needs an amendment**
   (§3, draft 1).
2. **Rule (c), S792 out of the demand balance: needs an amendment** (§3,
   draft 2). With (b), every planned station flow comes within about
   125 veh/h of its count (§4).
3. **The §2.3 reading behind `_dc_cal`.** Not carrying residuals is applied
   as the protocol's own rule, not as an amendment: unmeasured ramps from
   their own segment's mainline difference. If the owner reads the
   pre-protocol carry rule as the recorded method (§2.4) instead, set
   `CARRY_RESIDUALS = True` in the script and rewrite; that build is the
   "residuals carried" column of §4.
4. **§7.2's "single corridor-wide adjustment"**: one value per knob (read
   here), or one knob in all (then the speed factor needs an amendment).
5. **The speed factor's hours.** The study period (1.3026) was fixed as the
   reading before the run. If the owner prefers whole days (1.2346), that has
   to be decided before p8's results are seen, not after.
6. **The Amendment-1 grid ran on nine-day inputs.** It stands as chosen
   unless the owner wants it re-run on the calibration-day slice (about $0.6).
7. **The Mounds Blvd and 6th St exits are an uncertain input.** §8.5 asks for
   a sensitivity test of how much the results depend on them; it is not run.
8. **Commit, push and launch p8** (about $3 on an n2-standard-32). Not done
   here.

## 8. Reproduce

- **The scenarios and the demand record:**
  `uv run --no-sync python scripts/i94_calibration_days.py` (`--force` to
  rewrite). Check them with `--check`; print §4 with `--table`.
- **The driver check:** the command in §5. For the whole-day sensitivity,
  drop `--start` and `--end` and use `--out …/transfer_lanes_wholeday`.
- **Loop 3244:** the 30-s counts of `data/mndot/cache/<date>/3244.counts.json`
  for the five dates, 05:30–09:30, summed to 5-minute windows (a window with
  fewer than 8 of its 10 samples is skipped), mean over days.
- **The leak checks of §1:** compare `_dc`'s series with
  `artifacts/demand_mndot_i94_wb_stpaul.json` and with the S97 speeds of the
  nine-day file. Re-run `calibrate_scenario` on the nine-day file with the
  network the runner compiles (`microsim.runner._build_network`).

## 9. What changed

- **`calibration.onboarding.calibrate_scenario`** has three balance rules, all
  off by default: `carry_residuals`, `skip_stations`, `ignore_ramp_detectors`.
  A default call is byte-identical (§1.1 reproduction; existing tests pass).
  The rules are recorded under the demand record's `balance_rules`
  (docs/CONTRACTS.md, "Demand balance rules").
- **`scripts/i94_calibration_days.py`** writes and checks the files below.
- **New files:**
  - `scenarios/mndot_i94_wb_stpaul_weave_dc_cal.yaml` (config hash `beaaa710e6b3`);
  - `scenarios/mndot_i94_wb_stpaul_weave_dc_cal_netfix.yaml` (`182e3ec2f500`);
  - `scenarios/mndot_i94_wb_stpaul_weave_dc_cal_sf.yaml` (`8ad95d570201`);
  - `artifacts/demand_mndot_i94_wb_stpaul_cal.json`;
  - `artifacts/i94_calibration_days_2026-10-07/transfer_lanes{,_wholeday}/`.
- **Pipeline:** stage `p8_i94_cal` in `scripts/gcp/pipeline_i24.sh`; the
  ingest pattern `baseline_gate_*_dc_cal*.json`.
- **Tests:**
  - `tests/test_calibration/test_calibration_onboarding_balance_rules.py`;
  - `tests/test_scripts/test_i94_calibration_days.py` (its slow test re-derives
    everything);
  - the ingest case in `tests/test_scripts/test_gcp_scripts.py`.
