# D10: I-94 ramp rules (b) and (c) as pre-registered rounds (result)

**Amendment 4 defaults; the reproduction re-run of p10's arm B is pending** (FRISCO_PROTOCOL Amendment 4, "Deviation
recorded — 2026-10-08"). Every number below carries this label.

Written 2026-10-08, 01:26 CDT, from the records of stage `p16_i94_d10`; nothing was simulated for this note.

The pre-registration was fixed on 2026-10-07, before the run: docs/PRE_FRISCO_PROGRAM.md "D10", FRISCO_PROTOCOL
Amendment 10, `artifacts/i94_d10_2026-10-07/stage_p16_d10.sh.txt` and `…/harness/corridor_d10.py`.

Every value comes from the record named with it: the readout `artifacts/i94_d10_corridor.json`, the reproduction
`artifacts/i94_d10_repro.json`, the batteries `artifacts/validation_<label>.json` and gates
`artifacts/baseline_gate_<label>.json` (label `mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2` plus `_rb` or `_rbc`;
without a suffix, the reference, p10's arm B), the stage log `logs/p16_i94_d10.log` in `p16_final.tgz`, and
`artifacts/i94_d10_2026-10-07/scenario_diff.json`.

## 1. Result

The readout records no `problems` and no `notes`.

| arm | rules | config hash (v4) | D1 safety | D2 demand | D3 C1 | D4 C3 | holds |
|---|---|---|---|---|---|---|---|
| `_rbc` | (b) T.H.61 NB from the mainline difference, (c) S792 out of the balance, still scored | ad158ff561b1 | met | met | met | met | **yes** |
| `_rb` | (b) only | 1f4412f6b393 | met | met | met | **not met** | no |

`_rb` fails D4. Its calibration-day C3 (15-min speed RMSPE) is 47.4 % against the reference's 38.3 %: a paired
difference of **+0.090 [0.056, 0.124]** where at most +0.02 is allowed.

**B5's I-94 arm is `_rbc`**, by the rule fixed before the run ("`_rbc` if it holds, else `_rb`, else the
reference"). p17 runs on `scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2_rbc.yaml`.

As the readout's `status` puts it, an arm that holds is "a calibration candidate for B5's I-94 fit, not a validation
result". The gate fails in all three arms (§5).

## 2. Provenance and checks

- **Run.** VM flowstate-p16 (us-central1-c, instance 970363102378500263, launched as n2d-standard-16,
  self-deleting). The stage ran 04:39:40Z–06:10:11Z (OK, 5,431 s); `PIPELINE_EXIT rc=0` at 06:10:25Z; the VM was
  gone by 06:12:45Z. About $1.2 [estimate: about 94 VM-minutes at the program's $0.013/min].
- **Code.** 3856257 (`code` in both records; `vm_snapshot` 941f7fb is the VM's own commit). None of the scenarios,
  demand records, `scripts/i94_calibration_days.py` or `artifacts/i94_d10_2026-10-07/` changed from 3856257 to
  HEAD ef28156. On the VM, `--check --only d10` matched both arms before anything ran.
- **Hashes**, recomputed as `config_hash(ScenarioConfig.model_validate(yaml))`.
  - `_rb` gives 1f4412f6b393 and `_rbc` ad158ff561b1 (v4). Each equals the hash in its battery, gate and gated
    battery.
  - The reference file gives 395a111cb991 (v4) and 5080d84d4725 (v3). Its committed records quote 5080d84d4725.
- **Local checks.** The ten ingested records and three report directories are byte-identical to the archive copies.
  Re-running `corridor_d10.py repro` and `evaluate` at ef28156 reproduces both records, except `created_at`, `code`
  and `vm_snapshot`.
- **The label.** The arms write the five W1b/W2 keys explicitly, as arm B did, so their inputs do not depend on the
  default. What is pending is arm B's 20-seed, per-seed re-run on current code (p24's F2 u0 arm). The one-seed check
  below is not that re-run.
- **The reproduction.** The reference's first seed, 6914975401685141156, was run at 3856257. It matched the
  committed records value for value (`reproduced` true, `differs` empty). The comparison covered every `per_seed`
  field (`run_dir` aside), the observed side, and the gate's per-replicate 5-, 15- and 60-min RMSPE and simulated
  bottlenecks on both day sets. The new field `weave_releases` was listed, not compared. The hashes are consistent
  and were not compared: bae67e48f30b is the file at `replicates` 1 (v4), 5080d84d4725 the committed v3 hash. So the
  committed battery and gate are the reference.

## 3. What the rules change in the inputs

From `scenario_diff.json` (`only_expected_differ` true in both arms) and the demand records' `balance_rules`.
Values are planned free-flow veh/h for the hours from 06:30, 07:30 and 08:30, then the 05:30–09:30 mean.

| series | reference | `_rb` | `_rbc` |
|---|---|---|---|
| T.H.61 NB entrance 53062592 | 1,716 / 1,683 / 1,321 / 1,486 (rnd_88807) | 1,255 / 1,208 / 1,101 / 1,165 (S1070 − S1069) | as `_rb` |
| Mounds Blvd exit 18207912 | 946 / 1,066 / 762 / 733 | fraction unchanged | 268 / 136 / 182 / 220 |
| 6th St left exit 42165869 | 84 / 0 / 12 / 137 | fraction unchanged | 268 / 136 / 182 / 220 |

**Rule (b).** S1069→S1070's residual has the same sign on all five calibration days (−426.1 to −284.5), and its
4-h mean, −316.2, lies outside the ±261.4 quadrature band. Planned vehicles per run fall from 32,354 to 31,069,
all of it at T.H.61 NB (118,520 planned there over 20 seeds, against 92,820).

**Rule (c)** changes only the two exit fractions, which now split S1948 − S791 equally (the protocol's §2.3
documented assumption).

In the plan, every station except S792 comes within about 125 veh/h of its count (I94_CALIBRATION_DAYS §4).

**Scope.** Rule (b) was applied only to rnd_88807, the sole ramp of its bracket (`provenance.draft_1_scope`).
`draft_1_screen` shows the test is also met at Hudson Rd, S1064→S1065 (−436.4 against ±179.8, the same sign on all
five days). That bracket has two ramps, which one mainline difference cannot separate, so it was left as built.

## 4. D1–D4 and the paired contrasts (`spawn_seeds(42, 20)`, the same seeds in every arm)

| criterion | reference | `_rb` | `_rbc` |
|---|---|---|---|
| D1 collisions; locks (`validation.locks`); lowest departed share | 0; 0 of 20; 0.9815 | 0; 0 of 20; 0.9815 | 0; 0 of 20; 0.9712 |
| D2 realised demand (floor 0.9740) | 0.9840 | 0.9849 | 0.9789 |
| D3 C1, calibration days (≥ 36.1 %) | 36.1 % | 74.8 % (+0.387) | 53.3 % (+0.173) |
| D4 C3 15 min, calibration days (≤ 40.3 %) | 38.3 % | 47.4 % (+0.090) | 39.7 % (+0.013) |
| paired C3 15 min, calibration, mean [95 % t] | | +0.090 [+0.056, +0.124] | +0.013 [+0.005, +0.022] |
| paired C3 15 min, validation | | +0.244 [+0.203, +0.285] | +0.012 [+0.003, +0.020] |
| paired departed share | | +0.0009 [+0.00002, +0.0017] | −0.0051 [−0.0067, −0.0035] |

**`_rbc`'s speeds are worse than the reference's on both day sets**: both intervals exclude zero. On the
calibration days the upper end, +0.022, crosses the +0.02 margin; D4, as pre-registered, compares the gate values.

**Locks.** The readout's D1 lock reading is the program's superseded wording (no seed below 0.8 of the median). It
passes, as does `validation.locks`.

**Breakdowns and backlog.** There are no breakdowns. In `_rbc`, 10 of 20 seeds leave 2–3 % of planned vehicles
undeparted (the battery's backlog note).

## 5. Gate rows with and without both rules (phase-1 day sets; 20 replicates, mean [95 %])

| row (gating) | reference: neither | `_rb`: (b) | `_rbc`: (b)+(c) | target |
|---|---|---|---|---|
| C1 GEH < 5, calibration | 36.1 % [34.3, 37.9] | 74.8 % [73.0, 76.5] | 53.3 % [51.1, 55.6] | ≥ 85 % |
| C1 GEH < 5, validation | 33.0 % [30.9, 35.1] | 72.4 % [71.0, 73.8] | 48.8 % [46.1, 51.5] | ≥ 85 % |
| C3 RMSPE 15 min, calibration | 38.3 % [37.5, 39.2] | 47.4 % [44.5, 50.2] | 39.7 % [39.3, 40.1] | ≤ 15 % |
| C3 RMSPE 15 min, validation | 37.9 % [37.0, 38.7] | 62.3 % [58.7, 65.9] | 39.0 % [38.7, 39.4] | ≤ 15 % |
| C4 wave speed, calibration | 5.7 km/h [5.5, 5.8] | 4.4 [4.3, 4.5] | 5.4 [5.2, 5.5] | 14–22 (observed 19.1) |
| C5 collisions | 0 | 0 | 0 | 0 |
| C6 bottlenecks, calibration / validation | pass / pass | pass / pass | pass / pass | |
| validation days 09-01 / 09-09 / 09-10 / 09-17, C1 | 28.7 / 31.3 / 38.3 / 41.6 % | 66.8 / 70.7 / 62.3 / 66.2 % | 47.9 / 50.4 / 37.1 / 66.0 % | reported |
| the same days, C3 | 37.9 / 42.8 / 40.2 / 43.9 % | 78.0 / 91.2 / 53.4 / 71.1 % | 37.5 / 42.8 / 41.4 / 44.9 % | reported |
| verdict | failed | failed | failed | |

C6 fails on every validation day, taken one at a time, in all three arms.

**Rule (b) alone** raises C1 from 36.1 to 74.8 %, but worsens speeds on both day sets (+0.090, +0.244). Total delay,
including waiting, falls from 5,330.7 to 2,650.6 veh-h (`waiting`).

**Adding rule (c)** is what brings C3 back, to 39.7 % and 39.0 %. It keeps +0.173 of the +0.387 C1 gain, and total
delay is 5,461.2 veh-h.

The station table below shows where this happens: battery `link_hours`, simulated minus observed, calibration days,
06:30 / 07:30 / 08:30.

| station | reference | `_rb` | `_rbc` |
|---|---|---|---|
| S1070 | −564 / +758 / +441 | −524 / +586 / −50 | −876 / +23 / −162 |
| S1948 | −619 / +689 / +339 | −579 / +641 / −146 | −934 / −54 / −285 |
| S792 (scored, outside `_rbc`'s balance) | −458 / +614 / +129 | −456 / +597 / −119 | −379 / +765 / +293 |
| S791 | −966 / −68 / −243 | −959 / −75 / −420 | −977 / −72 / −238 |
| S790 | −975 / −83 / −240 | −968 / −80 / −374 | −985 / −77 / −241 |
| S97 | −900 / −260 / −132 | −884 / −255 / −248 | −891 / −253 / −125 |

In `_rbc`, the 07:30 surplus at S1070 and S1948 is gone. Their 06:30 hour runs 876–934 veh/h short, although the
plan there was 0 and +64 veh/h from the counts (I94_CALIBRATION_DAYS §4).

## 6. Safety, locks and the weave disclosures

None of the 60 runs had a collision or a lock (`collisions.total` 0; `locks.n_runs_locked` 0 of 20 per battery).

W1b releases and W2 counters are reported beside `no_locks` and never gate; a W1b share above 1 % is flagged
(Amendment 4). The reference's W1b and W2 counts come from p10's `weave_w2_corridor.json`. Given-up exits come from
`weave_exits`, which uses a 2 % threshold.

| per weave (Ruth St 745524613 / T.H.52 769818012) | reference | `_rb` | `_rbc` |
|---|---|---|---|
| W1b releases, Ruth St | 257 / 20,340 = 1.26 % | 45 / 20,340 = 0.22 % | **281 / 20,340 = 1.38 %** (flagged) |
| W1b releases, T.H.52 | 1 / 91,644 = 0.001 % | 2 / 92,595 = 0.002 % | 6 / 91,997 = 0.007 % |
| W2 vetoes, Ruth St / T.H.52 | 6,908 / 16,011 | 2,057 / 15,147 | 6,946 / 15,589 |
| given-up exits, Ruth St / T.H.52 | 1.95 % / 1.27 % | **2.04 %** / 1.12 % | 1.49 % / 1.26 % |

**Ruth St W1b share.** `_rbc`'s share is above 1 %, as the reference's was (CW5b's failure). `_rbc`'s report lists it
under Limitations.

**Given-up exits.** `_rb` is the one arm over the 2 % threshold: 627 of 30,687 at Ruth St, battery verdict "exits
given up: 2.0 % at on-ramp 745524613".

## 7. What the arms do not do

**The gate fails in every arm**: C1 and C3 on both day sets, and C4. C5 and the pooled C6 pass. `_rbc` still falls
short as follows:
- C1: 31.7 points below 85 % (calibration days);
- C3: 24.7 points above 15 %;
- C4: 8.6 km/h below the band.

**Neither rule touches the 06:30 shortfall** at S791, S790 and S97: 884–985 veh/h in all three arms (§5). S790 and
S97 are stations that Amendment 3 (c) says the T.H.52 section can move.

**Neither rule changes the T.H.52 weave.** Its fixture shortfall at the proportional split, 465 veh/h (95 % CI
427–504; docs/WEAVE_LOSS_DIAGNOSIS.md §0), was not measured here. It remains the binding defect named in D10's
"Risk".

No validation claim and no strategy recommendation follow from any arm.

## 8. Limitations

- **Day sets.** There are five calibration days and four validation days, and D3 and D4 read only the calibration
  days.
- **Uncertain inputs.**
  - The T.H.52 ramp-to-ramp share is at the proportional split in every arm. Its range [proportional, 0.70] is
    unexamined until Amendment 3's round (p24).
  - Ruth St's proportional split is unexamined.
  - `_rbc`'s equal split of S1948 − S791 (protocol §2.3, §8.5) has had no sensitivity runs.
- **The Amendment-4 label.** If p24's F2 u0 arm does not reproduce arm B, these results stay under the label and
  Amendment 4's adoption is re-read. The readout and the arms' reports come from code committed before the deviation
  note, so they do not carry the label; this note does.
- **Rule (1)'s scope.** It is worded for every corridor, but it was tested at one single-ramp bracket (§3).
- **Two C1 readings.** Each battery's own `link_flows_geh` row reads the same runs on link-hours from 06:30, 07:30 and
  08:30; the gate anchors at `hour_anchor_s` 1800.
  - That row gives 46.1 / 65.6 / 67.7 % (reference, `_rb`, `_rbc`), ordering the two arms the other way.
  - D3 reads the gate, as pre-registered.
- **D4.** `_rbc`'s paired C3 interval reaches past D4's margin (§4).
- **Uncommitted reproduction records.** The one-seed run's records (`…_p16repro`) are not committed. They quote the
  one-replicate hash bae67e48f30b, which `test_every_committed_record_reproduces_under_its_policy` rejects. They stay
  in `p16_final.tgz`, and `i94_d10_repro.json` names them.
