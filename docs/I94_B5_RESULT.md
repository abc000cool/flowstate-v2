# B5 on I-94: the demand level refitted on link flows under the insertion constraint (result)

Written 2026-10-08, 11:03 CDT, from the records of stage `p17_i94_b5`; nothing was simulated for this note. The rule
was fixed before the run (docs/FRISCO_PROTOCOL.md Amendment 6; docs/PRE_FRISCO_PROGRAM.md "B5", with the I-94 no-lock
reading); D10's rule made `_rbc` the from-arm (docs/I94_D10_RESULT.md). Sources, with L =
`mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2`:
- the fit `artifacts/demand_level_fit_i94.json` and the readout `artifacts/demand_level_i94.json`;
- the refit's battery, gated battery and gate (`artifacts/validation_<L>_rbc_b5{,_gated}.json`,
  `artifacts/baseline_gate_<L>_rbc_b5.json`) and its scenario `scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2_rbc_b5.yaml`;
- the same records of the from-arm `…_rbc` (p16) and of the reference `<L>` (p10's arm B);
- `artifacts/a3_range.json` and `logs/p17_i94_b5.log` in `p17_final.tgz`.

[computed] marks values derived here from those files (paired intervals by `validation.metrics.ci`, as in D10's
readout). The readout's `status`: **PROPOSED, not adopted**; its `not_validation`: the gate's verdict, not the
readout, is the validation reading.

## 1. Result

The fit moves the level from **1** (`_rbc` as D10 built it from the calibration-day counts) to **s = 0.95**, the grid's
lowest factor. At 0.95, **128 of 210** calibration-day station-hours have GEH < 5 (0.6095), the best of the four scales
that meet the insertion floor (mean inserted 0.9905 ≥ 0.9689; `constraint_unmet` false; `decided_by` `share`). The
from-arm reproduced exactly at its first seed.

Against `_rbc` on the same 20 seeds, the refit `…_rbc_b5` (bd00fbaba89a) **holds C1–C4 and the no-lock reading; C5 does
not bind** (both arms fail the wave row). `candidate` is true and `problems` is empty. Adoption is the owner's. **The
gate still fails** (§5, §6).

## 2. Provenance and checks

- **Run.** VM flowstate-p17 (us-east1-b, instance 7942077042288944637, self-deleting). The stage ran
  08:58:43Z–10:43:39Z (OK, 6,296 s); `PIPELINE_EXIT rc=0` at 10:43:54Z, at 1 h 45 min uptime. The watcher's polls lapsed
  09:05:55Z–12:42:04Z; it found the VM gone at 15:40:13Z. About $1.4 [estimate: at least 105 VM-minutes at $0.013/min];
  the plan said $2.2.
- **Code.** f454c7d. 34fe1cf is the VM's own commit, not in the repository. Up to HEAD f0f76c2 only an I-24 scenario
  was added. eclipse-sumo 1.27.1.
- **Hashes** (policy 4, at HEAD). **bd00fbaba89a** is the fit's `chosen` and `scenario_out`, the 0.95 fit runs', and the
  battery's, gated battery's and gate's. The fit's recipe gives the file's document exactly: `_rbc` (ad158ff561b1) with
  the mainline and every on-ramp inflow × 0.95, renamed. Planned vehicles per run: 31,069 → 29,456.
- **Files.** The 11 ingested files equal their archive copies; every recorded sha256 matches. At HEAD the harness's
  `evaluate_i94` re-derives the readout except `created_at`, `code` and `vm_snapshot`.
- **Reproduction.** `_rbc` on seed 6914975401685141156 matched its battery's replicate (`reproduction.exact` true).
  [computed] The fit runs at 1.0 equal `_rbc`'s first five replicates, and those at 0.95 the refit's first five, in
  insertion counts, collisions and 5-min RMSPE.
- **Amendment 4's label is satisfied.** This run used the W1b/W2 defaults. p24's F2 u0 arm has reproduced p10's arm B
  in 20 of 20 seeds (`artifacts/a3_range.json` `families.F2.u0_against_p10`: `identical_seeds` 20, `departed_unequal`
  empty). [computed] Its W2 counters equal `artifacts/weave_w2_corridor.json`'s in 40 of 40 seed-weave pairs. No file
  under `packages/` changed from p24's build (466193f) to f454c7d.

## 3. The fit

The seeds are the from-arm battery's first five, and the floor is its realised share minus 0.01 (0.97886 − 0.01 =
0.96886). The grid is one round, 0.95–1.05 by 0.025, inside `count_error` 0.05. The objective pools each seed's 42
calibration-day station-hours (gate C1's anchored hours). The validation days are held out.

| s | planned / run | mean inserted | ≥ floor | GEH < 5, of 210 | mean GEH | held out, of 210 | 15-min RMSPE | collisions |
|---|---|---|---|---|---|---|---|---|
| **0.950** | 29,456 | **0.9905** | yes | **128** | 5.01 | 119 | 0.289 | 0 |
| 0.975 | 30,254 | 0.9870 | yes | 125 | 4.76 | 122 | 0.341 | 0 |
| 1.000 | 31,069 | 0.9796 | yes | 118 | 5.05 | 105 | 0.392 | 0 |
| 1.025 | 31,813 | 0.9690 | yes | 103 | 5.42 | 92 | 0.425 | 0 |
| 1.050 | 32,595 | 0.9570 | **no** | 82 | 6.78 | 68 | 0.461 | **1** |

- **Monotone.** Each step up lowers the share and the inserted fraction and raises the speed error (reported only).
  The choice sits at the lower edge, which the count uncertainty (§7.1) sets.
- **Close at the top.** 0.95 leads 0.975 by 3 bins. Per seed, 0.95 scores 25–26 of 42 and 0.975 scores 21–28. 0.975
  has the smaller mean GEH and the larger held-out count. 1.025 meets the floor by 0.0002.
- **A collision at 1.05.** Seed 134183728835869882 at t = 754.0 s (warm-up), lane 51388891_1 at 5.9 m (the T.H.52
  weave's edge): v12785 (Hudson Rd entrance → T.H.52 exit) struck v27464, a T.H.52 entrant on the corridor for 1.5 s.
  No criterion reads the fit runs, but CLAUDE.md §3.3 makes zero collisions a criterion of every run set. Reported, not
  investigated.

## 4. The readout (refit against `_rbc`, the same 20 seeds)

| | rule | refit | `_rbc` | verdict |
|---|---|---|---|---|
| C1 | 0 collisions in every run, each recorded | 0 of 20 | 0 | holds |
| C2 | mean realised ≥ from-arm − 0.01 | 0.9908 [0.9901, 0.9914] | 0.9789 [0.9771, 0.9806] | holds; paired +0.0119 [0.0101, 0.0137] |
| C3 | gate C1, calibration days, not below the from-arm's | 61.31 % | 53.33 % | holds |
| C4 | gate C3, calibration days, ≤ from-arm + 0.02 | 0.2930 | 0.3969 | holds |
| C5 | gate C4 unchanged where the from-arm passes | fail | fail | does not bind |
| NL | no seed's departed share below 0.8 × the median | none | | holds |

Both batteries: 0 of 20 runs locked (95 % CI 0–17 %), no breakdowns.

## 5. The gate beside the from-arm and the reference (20 replicates; mean [95 %])

| row | reference | `_rbc`, s = 1 | `_rbc_b5`, s = 0.95 | target |
|---|---|---|---|---|
| C1 GEH < 5, calibration | 36.1 % [34.3, 37.9] | 53.3 % [51.1, 55.6] | **61.3 % [59.9, 62.7]** | ≥ 85 % |
| C1 GEH < 5, validation | 33.0 % [30.9, 35.1] | 48.8 % [46.1, 51.5] | **56.3 % [54.8, 57.8]** | ≥ 85 % |
| C2 GEH < 3, cal. / val. (not gating) | 25.2 / 23.2 % | 39.0 / 33.7 % | 31.9 / 30.2 % | 100 % |
| C3 15-min RMSPE, calibration | 38.3 % [37.5, 39.2] | 39.7 % [39.3, 40.1] | **29.3 % [27.7, 30.9]** | ≤ 15 % |
| C3 15-min RMSPE, validation | 37.9 % [37.0, 38.7] | 39.0 % [38.7, 39.4] | **33.4 % [30.0, 36.8]** | ≤ 15 % |
| C4 wave speed, km/h; replicates with a front | 5.7 [5.5, 5.8]; 20 | 5.4 [5.2, 5.5]; 20 | **4.4 [4.3, 4.5]; 15** | 14–22; ≥ 16 |
| C5 collisions; C6 cal. / val. | 0; pass / pass | 0; pass / pass | 0; pass / pass | |
| C3 on 09-01 / 09-09 / 09-10 / 09-17 | 37.9 / 42.8 / 40.2 / 43.9 % | 37.5 / 42.8 / 41.4 / 44.9 % | 54.8 / 57.1 / 30.3 / 43.2 % | reported |
| verdict | failed | failed | failed | |

[computed] Paired refit − `_rbc`: C3 15-min −0.104 [−0.122, −0.086] (calibration) and −0.056 [−0.092, −0.021]
(validation); per run, −1,228 [−1,284, −1,172] vehicles departed and −347 [−423, −270] arrived. C6 fails on every
validation day taken alone, in all three arms.

| weave disclosures (W1b > 1 % flagged; given-up threshold 2 %) | `_rbc` | `_rbc_b5` |
|---|---|---|
| Ruth St 745524613: W1b releases | 281 / 20,340 = 1.38 % (flagged) | 35 / 19,240 = 0.18 % |
| Ruth St: W2 vetoes; given-up exits | 6,946; 1.49 % | 1,483; 1.84 % |
| T.H.52 769818012: W1b releases | 6 / 91,997 = 0.007 % | 4 / 91,343 = 0.004 % |
| T.H.52: W2 vetoes; given-up exits | 15,589; 1.26 % | 15,009; 1.27 % |

## 6. What the new level does and does not do

- **It does** raise link flows by 8.0 points (calibration) and 7.5 (validation), lower the pooled speed error on both
  day sets, end `_rbc`'s 2 % backlog by planning fewer vehicles, and bring Ruth St's W1b share under the flag.
  [computed; battery rows, simulated − observed at 06:30] The mid-corridor shortfall shrinks: S1067 / S1068 / S1947 /
  S1069 go from −566 / −578 / −671 / −671 to −290 / −319 / −436 / −487 veh/h. S1063 and S1064 move from −21 / −33 to
  −206 / −192 veh/h (GEH 3.4).
- **It does not pass the gate.** C1 and C3 fail on both day sets. C4 reads 4.4 km/h, 9.6 below the band, with fronts in
  15 of 20 replicates; the report flags that mean as underpowered. The downstream 06:30 shortfall stays: S1070, S1948,
  S791, S790 and S97 still run 750–958 veh/h short (S792, outside the balance, −271). C3 worsens on 09-01 and 09-09, and the GEH < 3 share falls.
- **The T.H.52 ramp-to-ramp share's range is the larger effect.** On F2 (p10's arm B, without D10's rules; `a3_range.json`
  `families.F2.range` and `verdict_u1`), moving from u0 to u1:
  - gate C1 goes from 36.1 to 79.4 % (calibration) and 33.0 to 78.6 % (validation), paired +0.433 [0.411, 0.456];
  - C3 goes from 38.3 to 73.0 % and 37.9 to 86.6 %, paired +0.347 [0.327, 0.366];
  - S790 at 06:30 rises by 700.7 [668.8, 732.6] veh/h.

  B5's level moves C1 by +8.0 points and C3 by −10.4. It was fitted at the proportional split; the share is
  unexamined on `_rbc`.

No validation claim and no strategy recommendation follow.

## 7. Limitations

- **Grid edge.** The objective still rises toward 0.95; a lower level is outside the count uncertainty.
- **Five seeds.** The 3-bin lead is not shown to be resolved: the same five seeds read 56.2 % for `_rbc`, against its
  20-seed 53.3 %.
- **In-sample.** The readout's C3 and C4 read the days the fit scored. The validation days split day by day (§5).
- **Untested splits, as in D10.** The T.H.52 share, Ruth St's split, and `_rbc`'s equal split of S1948 − S791.
- **Not written here.** The dated docs/I94_CALIBRATION_DAYS.md section that the program's Readout names.
