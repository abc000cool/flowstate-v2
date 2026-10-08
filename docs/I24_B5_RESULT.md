# B5 on I-24: the demand level refitted on link flows under the insertion constraint (result)

Written 2026-10-08, 04:03 CDT, from the records of stage `p15_i24_b5`; nothing was simulated for this note. The rule
was fixed before the run: docs/FRISCO_PROTOCOL.md Amendment 6 and docs/PRE_FRISCO_PROGRAM.md "B5", with the
schedule change that made C7b's `rcs` arm the from-arm (docs/I24_CONSISTENCY_C7B.md §11). Every value comes from the
fit `artifacts/demand_level_fit_i24.json`, the readout `artifacts/demand_level_i24.json`, the refit battery
`artifacts/i24_validation_p15_b5.json` and its ramp reduction `artifacts/i24_b2_ramp_flows_p15_b5.json`, the scenario
`scenarios/i24_replica_flow_rcs_speedcal_dc_refit_b5.yaml`, the from-arm battery
`artifacts/i24_validation_dc_refit_rcs.json` (p23), or `logs/p15_i24_b5.log` in `p15_final.tgz`. The readout's
`status`: **PROPOSED, not adopted**. On I-24 this is calibration, never validation.

## 1. Result

The fit keeps the level at **s = 0.925**, the from-arm's carried level. No scale on the grid scores more than **22 of
72** fit-hour bins under GEH 5 (0.3056). s = 0.9 and 0.925 both score 22; 0.925 wins the first tie-break, the
smaller mean GEH (9.150 against 9.247), and meets the constraint (mean inserted 0.9654 ≥ 0.9570; `constraint_unmet`
false). The refit is the from-arm under another name, and its 20-seed battery reproduces the from-arm's (§4).
**C1–C4 hold; C5 does not bind** (the from-arm fails the wave row); `candidate` true; `problems` empty. **B5
changes no input on I-24.**

## 2. Provenance and checks

- **Run.** VM flowstate-p15 (us-east1-b, instance 8173117248834829569, n2d-standard-16, self-deleting). Stage
  07:20:09Z–08:36:03Z (OK, 4,554 s); `PIPELINE_EXIT rc=0` 08:36:30Z; VM gone by 08:39:18Z (watcher log). About $1.0
  [estimate: about 80 VM-minutes at the program's $0.013/min]; the plan said $1.2.
- **Code.** d8186f6 (`code` in the fit and the readout; `checkout_head` / `vm_snapshot` 972d5ca is the VM's own
  commit, not in the repository). eclipse-sumo 1.27.1.
- **Hashes** (policy 4, recomputed at HEAD f454c7d): from-arm 921fb1f42c67, base `…rcs_corrected_dc` 18b52caef8a9,
  refit `…_b5` **ef355b83cc00**, equal to the fit's `chosen` and `scenario_out`, the battery's and the ramp record's.
  The two scenario files differ only in `name` and the header comment; the `_b5` document under the from-arm's name
  hashes 921fb1f42c67.
- **Files.** The five ingested files equal their archive copies; the readout's sha256 of the fit and both batteries
  match. At HEAD the harness's `fit_checks_i24` (ten checks true) and `criteria_i24` reproduce the stored values
  (`evaluate` itself needs the replicates' `meta.json`, not archived).
- **Reproduction.** The from-arm on the first fit seed (6914975401685141156) matched the from-arm battery's replicate
  in hash, per-window counts, inserted fraction and segment speeds (`reproduction.exact` true). The five fit runs at
  0.925, under the `_b5` name, also equal the battery's first five replicates in counts and inserted fractions.

## 3. The fit

Seeds: the from-arm battery's first five. Floor: its realised share 0.96696 − 0.01 = 0.95696. The coarse round
(0.6–1.1 by 0.1) chose 0.9 on the share; the refine round added 0.85, 0.875, 0.925, 0.95; 51 runs with the
reproduction. The objective reads the five seeds' mean flow per (section, 5-min) bin, 06:30–07:30, on every lane.

| s | mean inserted, 5 seeds | ≥ 0.9570 | fit hour, GEH < 5 | mean GEH | held out, GEH < 5 | speed RMSPE, fit hour |
|---|---|---|---|---|---|---|
| 0.600 | 0.9996 | yes | 2 / 72 | 24.74 | 2 / 72 | 1.211 |
| 0.700 | 0.9973 | yes | 12 | 14.82 | 16 | 1.058 |
| 0.800 | 0.9938 | yes | 20 | 8.99 | 17 | 0.721 |
| 0.850 | 0.9930 | yes | 19 | 9.33 | 24 | 0.428 |
| 0.875 | 0.9912 | yes | 20 | 9.13 | 25 | 0.365 |
| 0.900 | 0.9855 | yes | 22 | 9.25 | 20 | 0.372 |
| **0.925** | **0.9654** | yes | **22** | **9.15** | 22 | 0.360 |
| 0.950 | 0.9403 | no | 21 | 9.40 | 19 | 0.357 |
| 1.000 | 0.8930 | no | 19 | 9.71 | 20 | 0.365 |
| 1.100 | 0.8082 | no | 17 | 10.72 | 21 | 0.354 |

- **The share is flat at the top.** The qualifying scales from 0.8 to 0.925 score 19–22 of 72. Above 0.925 more
  demand is held off the network (0.9403 inserted at 0.95) and the share does not rise (21, 19, 17).
- **Why 0.925.** `decided_by` is `mean_geh`: tied with 0.9 on the share, 0.097 lower in mean GEH. The next tie-break,
  the smaller change from 0.925, was not reached.
- **The held-out hour** (07:30–08:30; reported, never selected on) ranks the scales differently: 0.925 reads 22 of
  72 (mean GEH 11.46), 0.875 reads 25 and 0.85 reads 24.

## 4. The readout: C1–C5, refit against the from-arm on the same 20 seeds

| | rule | refit | from-arm | verdict |
|---|---|---|---|---|
| C1 | 0 collisions in every run | 0 in 20 of 20, each recorded | | holds |
| C2 | mean realised ≥ from-arm − 0.01 | 0.9670 [0.9651, 0.9688] | the same; paired 0 | holds (floor 0.9570) |
| C3 | link-flow GEH < 5 share not below the from-arm's | 31.25 % (45 of 144) | 31.25 % | holds |
| C4 | 15-min speed RMSPE ≤ from-arm + 0.02 | 0.2667 | 0.2667 (limit 0.2867) | holds |
| C5 | wave verdict unchanged where the from-arm passes | fails | fails | n/a: does not bind |

Reported beside, equal in both batteries: 0 of 20 runs locked (95 % CI 0–17 %), no breakdown; 2-h GEH 3.68 / 4.51
/ 3.03 at 2,200 / 3,200 / 5,400 m; ramp GEH against the corrected counts 1.69 / 0.95 / 1.29 / 1.87 (Old Hickory on,
Hickory Hollow off and on, Bell Rd off). The refit counted 2 vehicle-steps below −8.9 m/s² in 20 runs (seeds
677105600768189526 and 6953598295321596746); the from-arm's battery records no such count.

`level_unchanged` and `level_unchanged_reproduces_from_arm` (per-replicate counts) are true. Field by field, every
`simulated` value of the two batteries is identical (lane crossings, waves, locks and metrics included) except the
hash, run directories and wall time, as are the criteria rows and GEH tables; the ramp reductions differ only in
provenance. The scenario name enters the hash, not the simulation.

## 5. What "level unchanged" means, and what it does not

- **It means** that under Amendment 6's objective and constraint no level on this grid improves the `rcs` arm's
  fit-hour link flows, so the level stays at 0.925: the link-flow shortfall is not about the level. This agrees with
  C7 (docs/I24_GEH_DIAGNOSIS.md §1): on B2, of which `rcs` is the insertion-shifted variant, 4 of 100 failing bins
  were level (all at 1,000 m, where the lane sets differ) and 75 recording noise.
- **It means** B5 changes nothing on I-24. `_b5` is `rcs` renamed; adopting it would change no input. The arm keeps
  every gate failure it had: link flows 31.25 % against ≥ 85 %, 5-min speed RMSPE 0.360 against ≤ 0.15, and no
  backward wave from the criterion detector in any replicate (observed 19.9 km/h).
- **It does not mean** that 0.925 is the right level: 0.9 ties on the share, 0.8–0.925 lie within 3 bins of 72, and
  the held-out hour prefers 0.875. **Nor validation:** the fit hour lies inside the scored window of the one recorded
  morning (`not_validation`).

## 6. The lane set beside the all-lane objective

The fit and the battery's criteria row count every lane (`section_lanes` `all`, as pre-registered; the readout's
`fit_objective_alike_c3_rows` true). The fit's runs recorded no crossings by lane, so no lane-set objective exists
per scale. The battery records them (`simulated.lane_crossings`; per-lane sums equal the all-lane counts; on the
5-lane edges at 1,000 and 4,800 m the set is SUMO lanes 1–4), closing the gap C7b §11 noted ("as built, that report
cannot be formed"). Its lane-set 5-min row is **25.0 % (36 of 144)**. Neither the battery nor the readout carries the
station-hour share. Recomputed from the battery's counts with `scripts/i24_geh_diagnosis.py`'s `station_hours`, it
equals C7b's `rcs` readings (`artifacts/i24_consistency_c7b.json`, `readings.rcs`): pooled **63.75 %** (153 of 240)
on every lane and **54.58 %** (131 of 240) on lanes 1–4; replicate mean 58.3 % and 50.0 %. All fail 85 %.

## 7. Limitations

- **Grid.** The refine step is 0.025; nothing between 0.925 (0.9654 inserted) and 0.95 (0.9403, below the floor) was
  examined.
- **Five seeds per scale.** One seed moved the share "1.4 points per bin" and the inserted fraction up to 0.009
  (the program's B5, from Amendment 2's re-analysis); the five-seed noise was not measured. A 1–2-bin difference is
  not shown to be resolved, and the choice between 0.9 and 0.925 rests on mean GEH.
- **All-lane objective,** as pre-registered. Whether a lane-set objective would pick another level is unknown (§6).
- **Amendment 4 does not apply.** The I-24 replica models no weaving section (Amendment 4, rule 1), so neither the
  W1b/W2 defaults nor the pending re-run of p10's arm B touch this result; the reproduction here is exact.
- **`problems`** is empty. **C5** cannot bind on I-24 while the from-arm fails the wave row. **One morning**, fitted
  inside its scored window: calibration only.
