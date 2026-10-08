# FlowState validation report (E13)

Draft, written 2026-10-08 05:46 UTC at commit 466193f from committed artifacts only; nothing was simulated
for it. Spec: docs/PRE_FRISCO_PROGRAM.md, "E13". Rules: CLAUDE.md §7 and docs/FRISCO_PROTOCOL.md ("protocol").
Every value carries its source in brackets; these keys stand for artifacts:

| key | artifact |
|---|---|
| [I24] | `artifacts/i24_validation_dc_refit_rc.json`, the I-24 battery (stage p13) |
| [B2] | `artifacts/boundary_b2_corridor.json`, B2's R1–R5 readout |
| [C7] | `artifacts/i24_geh_diagnosis.json`, arm `dc_refit_rc` |
| [L14] | `artifacts/i24_locks_p14_b2_ref.json` |
| [S1] | `artifacts/driver_joint_screen_i24.json` |
| [G94] | `artifacts/baseline_gate_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2.json` |
| [V94] | `artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2.json` |
| [W2C] | `artifacts/weave_w2_corridor.json` (stage p10) |
| [E11] | `artifacts/i80_merge_validation.json` (stage p21) |
| [H] | `tests/golden/scenario_config_hashes.json` |
| [RG] | `tests/golden/ring_sugiyama.json` |

**Verdict: no corridor is validated.**
- **I-24:** a calibration candidate that fails link flows, speeds and the wave-speed row on the morning it was
  built from.
- **I-94:** the baseline gate fails on both the calibration days and the validation days.
- **Ring:** the benchmark passes in 20 of 20 seeds.
- **NGSIM I-80:** the merge configuration this report locks fails two of four pre-registered merge criteria.

## 1. Scope and provenance

- **Corridors and data.**
  - *I-24 westbound, Nashville:* I-24 MOTION, 30 Nov 2022, 06:30–08:30 CST, data x 0–5,492 m, six count
    sections [I24 `observed`]; one morning, no holdout.
  - *I-94 westbound, St. Paul:* MnDOT loops (RTMC Mayfly API), 14 stations [V94 `observations`]. Calibration
    days 2026-09-02, 09-03, 09-08, 09-15, 09-16; validation days 09-01, 09-09, 09-10, 09-17; split seed
    20261004 [G94 `split`].
  - *Raw NGSIM I-80, 13 Apr 2005:* a transfer test of merging only (docs/I80_MERGE_VALIDATION.md §2).
  - *The Sugiyama ring:* 230 m, 22 vehicles [RG].
- **Records.** All used SUMO 1.27.1 [I24, V94, RG `versions`].
  - The I-24 battery is stage p13's, created 2026-10-07 19:07 UTC [I24]. No committed file records its launch
    commit; p14's re-run (from c6c4387) reproduced it exactly (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.4, §8.4.6).
  - The I-94 battery is p10's arm B, run on 5516e05 (protocol Amendment 4 (i)).
  - E11 ran from 8ea59ec [E11 `code`].
- **Hash policy v4** (Amendment 4; docs/CONTRACTS.md §2). Every config hash moved once when W1b and W2 became
  weave defaults. `config_hash_v3` reproduces the version-3 hashes the batteries record:

| configuration | v3 (in its battery) | v4 (current) |
|---|---|---|
| I-24 `i24_replica_flow_rc_speedcal_dc_refit` | 909b89f298c5 [I24] | 7082bcea5442 [H] |
| I-94 `mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2` | 5080d84d4725 [V94] | 395a111cb991 [H] |
| `ring_sugiyama` | d5472987265c [I24 `ring`] | 258c09ac0074 [H, RG] |
| I-80 kept, `i80_replica` | — | 43a66c754f31 [E11 `arms`] |

- **The one locked configuration.** This is E13's reading, approved by the coordinator on 2026-10-07; the owner
  can overturn it. It has four parts:
  - LC2013 lane changing at acceleration lanes (`merge: lane_change`);
  - the runner's weave rules, with W1b and W2 on by default (Amendment 4);
  - scripted merges with `force_guard` on (docs/CONTRACTS.md §2);
  - the AV command guards (CLAUDE.md §3.3); no run here has an AV.

  I-24's four ramps are all `lane_change`. I-94 has 13 `lane_change`, 2 `scripted` and 2 `weave` blocks (the
  scenario files).
- **`merge: measured` is retired** by E11's pre-registered rule (§5). Its deletion (module, runner paths, tests,
  four scenarios including `scenarios/i80_replica_measured.yaml`, two goldens) is the owner's ask-first decision.
  Nothing is deleted yet, and no result below uses it.
- **Fleets** (scenario files): I-24 IDM from `artifacts/idm_i24_capacity_amax_k1.0.json` (Amendment 1's `a_max`
  shift, k = 1), keep-right 0; I-94 EIDM from the same population, keep-right 0.1; the ring IDM, T = 1.2 s.

## 2. I-24: the calibrated-arm candidate (calibration, not validation)

Decision A1 (docs/DECISIONS_2026-10-07.md; Amendment 5) made `i24_replica_flow_rc_speedcal_dc_refit` the
candidate: the k = 1 driver arm at demand scale 0.925 with B2's ramp-count correction, which removes through
traffic from the ramp counts (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3). The battery has 20 seeds
(`spawn_seeds(42, 20)`), profile `fhwa_default` [I24].

| check | result | target | verdict |
|---|---|---|---|
| C1 link flows, criterion row: 144 section × 5-min bins, replicate mean against tracked counts ÷ recommended coverage | 30.6 % [I24 `criteria`] | ≥ 85 % | **FAIL** |
| C1, station-hour form (Amendment 11, reported): 240 comparisons pooled | 64.6 %, per-replicate 95 % interval 61.8–67.4 %; replicate-mean form 58.3 % of 12 [C7 `station_hours`] | ≥ 85 % | **FAIL** |
| C3 speeds, 5-min segment RMSPE (the battery row) | 34.3 % [I24 `rmspe.value`] | ≤ 15 % | **FAIL** |
| C3 speeds, 15-min segment RMSPE (the protocol's aggregation) | 25.4 % [B2 `criteria.R5`] | ≤ 15 % | **FAIL** |
| C4 wave speed, stack detector, unseeded | no qualifying peak in any of 20 replicates (contrast 1.40–2.46 against the floor of 3); observed 19.9 km/h at contrast 3.44 [I24 `waves.by_detector.stack`] | 14–22 km/h | **FAIL** |
| C5 collisions | 0 in 20 runs, 310,548 vehicles departed [I24 `collisions`] | 0 | PASS |
| Locks | not recorded by the battery [I24 `criteria`]; a re-score of p14's identical re-run finds 0 of 20, Clopper–Pearson 0–16.8 % [L14] | 0 | not recorded (sidecar: none) |
| Realised demand | 0.967 (seeds 0.960–0.973) [I24 `simulated.demand_realized_fraction`] | reported | — |
| Peak sections, 2-h flow at 2,200 / 3,200 m | 6,316 / 6,267 against 6,626 / 6,639 veh/h, GEH 3.86 / 4.63 [B2 `arms.b2.peak_sections`] | GEH < 5 (B2's R4) | within |
| Ring rows | 20 of 20 seeds, both rows [I24 `ring`] | every seed | PASS |

**Descriptive metrics** (mean [95 % CI], 20 seeds, measured span [I24 `metrics_ci`]): throughput at data
x = 2,200 m 6,317 [6,302, 6,332] veh/h; mean travel time 619 [614, 625] s; σ_v temporal 4.11 [4.06, 4.16] m/s;
fuel, a model estimate, 95.3 [94.6, 96.0] ml/veh-km. The standard 40-km/h detector (not the criterion's) finds
13.8 [11.8, 15.7] jam components per replicate, backward at 10.5 [9.8, 11.2] km/h; on the observed field it
reads 14.2 km/h mean, 17.5 km/h median [I24 `observed.waves`].

**Reading.**
- **Which rows fail:** link flows, speeds and the wave row. Collisions, the ring rows, the seed count and the
  sensitivity grid pass; the grid row passes because a sweep exists, run on the older `i24_replica_speedcal` arm
  built on uncorrected counts [I24 `notes`; Amendment 5].
- **Calibration, not validation.** The demand level, the driver population, the ramp correction and the scoring
  all use the one recorded morning (Amendments 5 and 9).
- **The wave row.**
  - The arm's mean driver is string-stable at its own capacity density: its unstable band starts at 39.7 veh/km,
    against a capacity density of 29.2 veh/km [S1 `rows`, k = 1, j = 0]. CLAUDE.md §3.1 requires instability
    there.
  - The congested k = 0 arms pass the row at 15.7–15.9 km/h (docs/I24_VALIDATION.md §0.1).
  - Amendment 2's result keeps k = 0 on I-24, yet the candidate is a k = 1 arm (DECISIONS A1 §3). This report
    records the tension and does not resolve it.
  - B6's screen found no `a_max` gain that leaves the mean driver unstable at capacity, so its Stop rule fired
    and nothing launched [S1 `stop_rule`].
- **Link flows (C7).**
  - 100 of 144 bins fail: 75 recording noise, 15 shape, 6 timing, 4 level [C7 `verdict`].
  - Against its own centred 15-min mean, the recording passes 31.25 % of its bins. One seed against the other 19
    passes 75.8 % [C7 `recording_vs_own_mean`, `leave_one_out`].
  - In station-hours the model is 640 and 524 veh/h low at 2,200 and 3,200 m in 06:30–07:30, and on target in
    the second hour (docs/I24_GEH_DIAGNOSIS.md §4).

**Pending rounds** (none has a result):

| round | status | pre-registered criteria |
|---|---|---|
| C7b: the observed lane set at 1,000 / 4,800 m, coverage-consistent demand, a computed 75.7-s insertion shift | PENDING (stage p23_c7b, pre-registered in docs/I24_CONSISTENCY_C7B.md §6); no readout `artifacts/i24_consistency_c7b.json` is committed | R1–R5 against a B2 re-run that must reproduce p13 exactly; B5's arm is the R1–R5 holder with the largest pooled station-hour share on the observed lane set |
| B5: demand level fitted on link flows under an insertion constraint | PENDING (stage p15_i24_b5, pre-registered in docs/PRE_FRISCO_PROGRAM.md B5 and Amendment 6) | against the from-arm, same seeds: zero collisions; realised share ≥ from-arm − 0.01; GEH < 5 share not lower; 15-min RMSPE ≤ from-arm + 0.02; wave verdict unchanged where it passes |
| C9: two more INCEPTION mornings, the first I-24 holdout | PENDING (stage p20_i24_days, pre-registered in docs/PRE_FRISCO_PROGRAM.md C9 and Amendment 9); awaits the owner's download | gate C1, C3, C4, C5 on the validation days, model unchanged, reported; one calibration and two validation days, underpowered against protocol §3.3's 5 / 3 |

## 3. I-94 WB St. Paul: the baseline gate

The Phase A base, `mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2`, has inputs rebuilt from the five calibration
days only (docs/I94_CALIBRATION_DAYS.md) and W1b and W2 at both weaves (the default since Amendment 4); 20 seeds.
Station-hours are anchored at 06:00, 1,800 s after the observations' 05:30 start, three per station
(840 = 20 × 14 × 3) [G94 `hour_anchor_s`; V94 `observations.t0_local`].

| check | calibration days | validation days | target | verdict |
|---|---|---|---|---|
| C1 GEH < 5, station-hours | 36.1 % [34.3, 37.9] | 33.0 % [30.9, 35.1] | ≥ 85 % | **FAIL**, both |
| C2 GEH < 3 (TxDOT, not gating) | 25.2 % | 23.2 % | 100 % | fail |
| C3 15-min station-speed RMSPE | 38.3 % [37.5, 39.2] | 37.9 % [37.0, 38.7] | ≤ 15 % | **FAIL**, both |
| C4 wave speed (stack) | 5.7 [5.5, 5.8] km/h, fronts in 20 of 20; observed 19.1 km/h (7 of 13 pairs) | — | 14–22 km/h | **FAIL** |
| C5 collisions | 0 in 20 runs | — | 0 | PASS |
| C6 bottlenecks, day-set means | no observed bottleneck active ≥ 30 min; phantom S790→S97 in 6 of 20 (≤ 50 %) | one observed activation, 25 min long | protocol §5 | PASS (nothing to reproduce) |
| C6, each validation day (reported) | — | fails on all four: S790→S97 active ≥ 30 min, reproduced in 8 of 20 | | fail |
| **Gate** | | | protocol §6 | **FAILED**: no strategy recommendation may be made |

[G94 `checks`, `day_sets.*.bottlenecks`, `per_day`.]
- **Validation days one by one, C1 / C3:** 09-01 28.7 % / 37.9 %; 09-09 31.3 % / 42.8 %; 09-10 38.3 % / 40.2 %;
  09-17 41.6 % / 43.9 % [G94 `per_day.rows`].
- **The battery's own link-flow row** counts hours from 06:30 and reads 46.1 % [43.4, 48.7] [V94 `geh`];
  docs/I94_CAL_COLLISIONS.md §16 quotes that form. The criterion is the gate's C1, anchored as protocol §4
  requires.

**Integrity counts** [V94; W2C]:
- 0 collisions in 20 runs (636,724 vehicles departed);
- 0 locks (Clopper–Pearson 0–16.8 %);
- mean departed share 0.984 (lowest 0.982);
- given-up exits: Ruth St 599 (1.95 % of 30,717 exit-bound vehicles reaching the section), T.H.52 990 (1.27 % of
  77,870);
- W1b releases: Ruth St 257 of 20,340 entrants, **1.26 %**, above Amendment 4's 1 % disclosure line; T.H.52
  1 of 91,644;
- W2 vetoes of undriven vehicles' own lane changes: Ruth St 6,908, T.H.52 16,011 [W2C `w2_counters_b`, summed].

**Where it fails.** At the T.H.52 weave, S790 carries 3,871 veh/h in 06:30–07:30 (seeds 3,793–3,930) against
4,846 observed, GEH 14.8 [V94 `geh.link_hours`]. This shortfall holds *at the proportional split*:
- under Amendment 3, the T.H.52 ramp-to-ramp share is an uncertain input. It ranges per window from the
  proportional split to 0.70, a stated assumption (docs/TH52_CROSSING_SHARE.md §5);
- the gate is judged at the proportional split;
- Ruth St's split is unexamined.

**Pending:**

| round | status | pre-registered criteria |
|---|---|---|
| Amendment 3 range round: u = 0, 0.5 and 1 on families `_dc_cal_w1b` and `_dc_cal_w1b_w2`. The u0 arm on `_dc_cal_w1b_w2` is also the re-run of p10's arm B on current code that Amendment 4 (i) requires | PENDING (stage p24_i94_a3, pre-registered in the protocol's "Adoption of Amendment 3" and docs/A3_RANGE_ROUND.md) | material at u1 against u0 if any of M1–M4 holds. M1: S790 06:30–07:30 lower bound ≥ +100 veh/h with realised demand ≤ 1 pp lower. M2: a GEH < 5 count at S790 or S97 differs by ≥ 5 of 20 seeds. M3: calibration-day C1 or C3 interval wholly beyond ±2 pp. M4: a collision or lock in one arm only |
| D10: ramp rules (b) T.H.61 NB from the mainline difference and (c) S792 out of the balance; arms `…_rb`, `…_rbc` | PENDING (stage p16_i94_d10, pre-registered in Amendment 10 and docs/PRE_FRISCO_PROGRAM.md D10) | against the reference: zero collisions, no lock; realised demand ≥ reference − 0.01; calibration-day C1 not lower; C3 ≤ reference + 0.02 |
| B5 on I-94: one corridor-wide factor, 0.95–1.05 | PENDING (stage p17_i94_b5, pre-registered in Amendment 6) | as I-24's B5, plus gate C1/C3/C4 and no lock |

Earlier I-94 gate numbers (phase 1; step 3's `_dc`, C1 61.8 %) used inputs that included the validation days; they
stay in the record but are not a clean holdout (docs/I94_CALIBRATION_DAYS.md §2).

## 4. Ring benchmark (Sugiyama emergence, Stern dampening)

**What the tests assert** (`tests/test_microsim/test_microsim_ring_gate.py`, thresholds from
`validation.ring_benchmark`; CI runs the scenario's seed 42, the battery the same checks on 20 seeds):
- **Emergence** (no perturbation, no AVs): the across-vehicle speed spread σ_v over the last 300 s exceeds
  1.5 m/s; some vehicle drops below 3 m/s after the 180-s warm-up; the jam drifts backward at −25 to −5 km/h
  (the empirical 14–22 km/h band, widened because a 230-m ring is short).
- **Dampening** (same seed, one of 22 vehicles a compliant FollowerStopper): σ_v ≤ 0.75 × baseline; the tail
  minimum speed rises by more than 0.5 m/s; the tail mean speed stays above 1 m/s.

| | 20 seeds, mean [95 % CI]; range [I24 `ring`] | golden, seed 42 [RG] |
|---|---|---|
| emergence | 20 of 20 pass | — |
| σ_v, last 300 s | 2.34 [2.25, 2.42] m/s; 1.63–2.47 | 2.48 m/s (`sigma_v_spatial_ms`) |
| minimum speed after warm-up | 0.0 in every seed | — |
| jam drift | −14.5 [−14.8, −14.1] km/h; −15.6 to −11.6 | — |
| dampening | 20 of 20 pass | — |
| σ_v with the AV | 1.8 × 10⁻⁶ m/s; reduction ≥ 99.999 % in every seed | — |
| tail minimum speed with the AV | 2.02 [1.99, 2.04] m/s (0 without) | — |
| collisions | — | 0 |

**Hashes.** The golden pins seed 42's summary statistics at relative tolerance 10⁻⁶ under 258c09ac0074
[RG `tolerance`]. The 20-seed block ran under v3 d5472987265c (damped 148959d59786).

**Caveats.**
- T = 1.2 s replaced the 1.4-s default because emergence was weaker at 1.4 s (scenario header).
- The ring tests string instability and one controller, not any corridor.
- With the AV the ring settles near 2.0 m/s (seeds 1.94–2.16) [I24 `ring.per_seed`]. No baseline mean speed is
  recorded, so no speed or throughput gain is claimed.

## 5. Transfer test: E11 on NGSIM I-80 (merging only)

Criteria E1–E4 were pre-registered in docs/PRE_FRISCO_PROGRAM.md E11 and Amendment M1 (docs/MERGE_MODEL.md).
20 seeds per arm on raw NGSIM, with the I-24 fleet unchanged and nothing fitted [E11].

| arm | E1 partner-speed signs | E2 gap at change ≤ 0.9 / 0.8 of normal, recovering by 10 s | E3 model interval overlaps I-80's (6 medians) | E4 zero collisions |
|---|---|---|---|---|
| kept (`lane_change`, LC2013), 43a66c754f31 | met | **not met** | **not met**, 0 of 6 | met |
| `measured`, 7ef6ce60af40 | met | **not met** | **not met**, 3 of 6 | met |

[E11 `criteria`, `rescue`.] The reading is "measured is not rescued".

**The kept configuration is not validated on I-80 either.**
- Its accepted lag gap is 5.15 [4.99, 5.32] s against I-80's 1.68 [1.47, 1.87].
- Its entrants slow to 1.00 [0.85, 1.16] m/s at 50–100 m along the acceleration lane, against I-80's 3.05.
- LC2013 merges by stopping and waiting; I-80's drivers merge rolling (docs/I80_MERGE_VALIDATION.md §5–§7).

**Caveat: the observed sample.** It is 102 entering changes. The extraction finds 113 changes inside the zone,
against 421 counted ramp entries and 445 raw lane 7 → 6 transitions. The gap is unexplained. It must be checked
on the raw data before E11 is cited outside the project (§8 there; `artifacts/i80_merge_observed.json`
`zone_counts`).

## 6. Limitations

**Standing** (the boilerplate of `validation.report`): results describe one corridor, period and day set, and
transfer is not established; seed-to-seed intervals do not capture the models' own assumptions; fuel is a model
estimate; this report contains no strategy result and no compliance sweep; results are reported as they came
out, including failures.

**Specific:**
1. **One corridor per gate.** I-24 has one morning and no holdout; I-94 has five calibration days and four
   validation days.
2. **Model form.**
   - LC2013 fails I-80's merge measures (§5).
   - W1b's 60 s and 5 m are not measured behaviour (Amendment 4).
   - The I-94 fleet is EIDM, for which the stability criterion is not computed (docs/PRE_FRISCO_PROGRAM.md B6).
   - The I-24 candidate's drivers are stable at capacity (§2).
3. **Recording noise in 5-min link flows.** The recording passes 31.25 % of its own bins. A Poisson count model
   would pass about 74.4 % at 5 min and 99.99 % at station-hours [C7 `counting_noise`]; this is a scale, not a
   floor. The I-24 criterion's form is the owner's decision (Amendment 11).
4. **Our own inconsistencies, under correction in p23** (docs/I24_GEH_DIAGNOSIS.md §6–§7):
   - at 1,000 and 4,800 m the simulated count reads five lanes, the recording four;
   - planned demand is 1.00–1.15 times the row's target;
   - the mainline inflow is stamped 75.7 s late (a lower bound);
   - the runner's corridor is about 72 m shorter than the builder's chain, cause not established
     (docs/I24_CONSISTENCY_C7B.md §9).
5. **I-24 targets are estimates.**
   - They are tracked fragments over a recommended coverage of 0.559–0.674 per window
     [C7 `counting_noise.coverage_per_window`].
   - Ramp-lane coverage is bounded only to [0.33, 1] (docs/I24_GEH_DIAGNOSIS.md §7.1).
   - B2's flags are lower bounds, and its Old Hickory part is provisional (Amendment 5).
6. **I-94.**
   - The T.H.52 share is a stated-assumption range, and Ruth St's split is unexamined (Amendment 3).
   - The Ruth St W1b release share is 1.26 %.
   - C6 passes on the means only because no observed bottleneck there lasts 30 min.
   - The Amendment-1 driver choice was selected using runs on nine-day inputs (docs/I94_CALIBRATION_DAYS.md §2).
   - The driver check's free-flow speed mismatch (108.1 against 87.9 km/h) is uncorrected because the
     speed-factor arm was not adopted (same doc, §0 and the p8 results).
7. **Platform divergence.**
   - SUMO 1.27.1's arithmetic differs between arm64 macOS and x86_64 Linux. On five probed fixtures the state
     parts first, at steps 20–47, and three fixture tests change outcome (docs/E12_PLATFORM_TESTS.md §7.2–§7.3).
   - The batteries ran on x86_64 Linux VMs, and p8c reproduced every p8 collision across Intel and AMD
     (docs/I94_CAL_COLLISIONS.md §15).
   - A macOS re-run is not expected to match a battery bit for bit.
8. **Known stale records.**
   - `artifacts/boundary_b2_corridor.json` `status` still reads "PROPOSED, not adopted".
   - docs/PAPER_DRAFT.md describes B2 as not run, and its `_dc_refit` numbers, like CHANGELOG 2.6.0's, rest on
     uncorrected counts.
   - `docs/reports/i24_replica/` is from the 3 Sep battery.
   - I-24 batteries record no locks; sidecars cover p12 and p14 only.
   - The I-94 battery's ring rows were not evaluated (`--ring-seeds 0`).
   - The p13/p14 readouts' `code` names a VM snapshot outside the repository.
   - `artifacts/i24_boundary_ramps_fit.json`'s `base_config_hash` matches no policy (docs/CONTRACTS.md).
   - The I-94 gate's C2 text quotes C1's per-replicate interval [G94 `checks`].

## 7. What a reader must not conclude

- That any corridor is calibrated to FHWA targets or validated. None is, and the I-24 numbers are in-sample.
- That the ring shows any corridor benefit, or that one AV raises speed or throughput.
- That FlowState's merges are realistic. The locked LC2013 configuration fails E2 and E3 on I-80.
- That `measured` was rejected as worse. It failed the same two criteria, and the rule does not rank.
- That I-94 reproduces its bottleneck. C6 passes only on means with nothing to reproduce, and fails on every
  validation day.
- That any strategy result is deliverable. The gate failed (protocol §6).

**What would change the verdicts.**
- **I-24:** an arm passing C1 (in the form the owner adopts), C3 and C4 on the C9 validation mornings with the
  model unchanged. C7b and B5 can move C1. The wave row needs a population unstable at capacity; on B6's grid only
  the five k = 0 pairs are, so no `a_max` gain is compatible with it [S1 `summary`].
- **I-94:** closing the T.H.52 shortfall. The A3 round shows whether the share range alone moves it materially;
  D10 and B5 test the inputs. After that the gate needs C1 and C3 on both day sets, and C4.
- **I-80:** a merge model meeting E1–E4, read after the 113-against-421 count is explained.
