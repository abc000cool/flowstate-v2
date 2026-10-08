# FlowState validation report (E13)

Draft, written 2026-10-08 05:46 UTC at commit 466193f; updated 2026-10-08 19:01 UTC at adf5b2c with the six
rounds run since (p23, p15, p16, p17, p24, p25). From committed artifacts only; nothing was simulated for it.
Spec: docs/PRE_FRISCO_PROGRAM.md, "E13". Rules: CLAUDE.md §7 and docs/FRISCO_PROTOCOL.md ("protocol").
Every value carries its source in brackets; the keys, such as [I24], stand for the artifacts listed in Appendix A.

**Verdict: no corridor is validated.**
- **I-24:** a calibration candidate that fails link flows, speeds and the wave-speed row on the morning it was
  built from. The three rounds since changed none of its inputs (§2).
- **I-94:** the baseline gate fails on both the calibration days and the validation days, in every arm run: the
  reference, D10's and B5's arms, and both ends of the T.H.52 share's stated range (§3).
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
  - The I-94 battery is p10's arm B, run on 5516e05. p24 re-ran it on 466193f under the W1b/W2 code defaults
    and reproduced it in 20 of 20 seeds, which resolves Amendment 4's recorded deviation [A3
    `families.F2.u0_against_p10`; protocol Amendment 4, "Resolved"].
  - E11 ran from 8ea59ec [E11 `code`]; the later rounds' commits are in their write-ups. Each round's same-code
    reference reproduced its committed battery, in all 20 seeds (p23, p25) or at the first seed (p15, p16, p17)
    [C7b `reference_reproduces_p13`; K0 `reference_reproduces_committed`; B5a, B5b `reproduction`;
    `artifacts/i94_d10_repro.json`].
- **Auto-reports** (stage p22, code f0551e7): `docs/reports/e13_{i24_b2,i24_b5,i94_rbc,i94_b5,ring}/`. Tables
  come from the committed batteries, contours from one re-run replicate (seed 6914975401685141156) reproducing
  each battery's record of it [P22 `arms[].reproduction`]. The I-94 reports carry no T.H.52 range label (§3).
- **Hash policy v4** (Amendment 4; docs/CONTRACTS.md §2). Every config hash moved once when W1b and W2 became
  weave defaults; `config_hash_v3` reproduces the version-3 hashes the batteries record (Appendix A).
- **The one locked configuration** (E13's reading, approved by the coordinator on 2026-10-07; the owner can
  overturn it): LC2013 lane changing at acceleration lanes (`merge: lane_change`); the runner's weave rules with
  W1b and W2 on (Amendment 4); scripted merges with `force_guard` on (docs/CONTRACTS.md §2); the AV command
  guards (CLAUDE.md §3.3), unused here. I-24's four ramps are all `lane_change`; I-94 has 13 `lane_change`,
  2 `scripted` and 2 `weave` blocks (the scenario files).
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
| Locks | not recorded by the battery [I24 `criteria`]; p14's identical re-run, re-scored, and p23's re-run each find 0 of 20, Clopper–Pearson 0–16.8 % [L14; C7b `reference_reproduces_p13.no_locks`] | 0 | not recorded (re-runs: none) |
| Realised demand | 0.967 (seeds 0.960–0.973) [I24 `simulated.demand_realized_fraction`] | reported | — |
| Peak sections, 2-h flow at 2,200 / 3,200 m | 6,316 / 6,267 against 6,626 / 6,639 veh/h, GEH 3.86 / 4.63 [B2 `arms.b2.peak_sections`] | GEH < 5 (B2's R4) | within |
| Ring rows | 20 of 20 seeds, both rows (§4) [I24 `ring`] | every seed | PASS |

**Descriptive metrics** (mean [95 % CI], 20 seeds, measured span [I24 `metrics_ci`]; the full table is in
`docs/reports/e13_i24_b2/`): throughput at data x = 2,200 m 6,317 [6,302, 6,332] veh/h; mean travel time 619
[614, 625] s; σ_v temporal 4.11 [4.06, 4.16] m/s; fuel, a model estimate, 95.3 [94.6, 96.0] ml/veh-km.

**Reading.**
- **Which rows fail:** link flows, speeds and the wave row. The sensitivity-grid row passes only because a sweep
  exists, on the older `i24_replica_speedcal` arm and uncorrected counts [I24 `notes`; Amendment 5].
- **Calibration, not validation.** The demand level, the driver population, the ramp correction and the scoring
  all use the one recorded morning (Amendments 5 and 9).
- **The wave row.**
  - The arm's mean driver is string-stable at its own capacity density: its unstable band starts at 39.7 veh/km,
    against a capacity density of 29.2 veh/km [S1 `rows`, k = 1, j = 0]. CLAUDE.md §3.1 requires instability
    there.
  - The congested k = 0 arms pass the row at 15.7–15.9 km/h (docs/I24_VALIDATION.md §0.1).
  - Amendment 2's result keeps k = 0 on I-24, yet the candidate is a k = 1 arm (DECISIONS A1 §3). p25 put B2's
    correction on the k = 0 arm: the waves stayed, peak flows and speeds failed (below). The tension stands.
  - B6's screen found no `a_max` gain that leaves the mean driver unstable at capacity, so its Stop rule fired
    and nothing launched [S1 `stop_rule`].
- **Link flows (C7).** 100 of 144 bins fail: 75 recording noise, 15 shape, 6 timing, 4 level [C7 `verdict`].
  Against its own centred 15-min mean the recording passes 31.25 % of its bins [C7 `recording_vs_own_mean`].

**Rounds since the draft** (criteria fixed before each run; each arm against a reference its code reproduced, on
the same 20 seeds; nothing adopted, adoption is the owner's):

| round (stage) | result | verdict |
|---|---|---|
| C7b (p23): `rcs` inserts the mainline 75.7 s earlier; `rcc` plans coverage-consistent demand at B2's level; `rccs` both | B2's re-run reproduces p13 in all ten checks. `rcs`: peak 2-h GEH 3.68 / 4.51 against 3.86 / 4.63, 15-min RMSPE 0.267 (bound 0.274). `rcc` / `rccs`: realised 0.956 / 0.958 against 0.967; GEH 4.09 / 4.88 and 3.92 / 4.68. No collision or lock in 80 runs [C7b `criteria`, `readings`] | `rcs` holds R1–R5; `rcc`, `rccs` fail R2 and R4. The rule selects `rcs` for B5: lane-set station-hours 54.58 % against 53.75 %, 2 of 240 [C7b `selection`] |
| B5 (p15) on `rcs` | s stays 0.925: 22 of 72 fit-hour bins under GEH 5; 0.9 ties, losing on mean GEH 9.247 against 9.150 [B5a `chosen`, `per_scale`]. The refit is `rcs` renamed; its battery equals the from-arm's [B5a] | C1–C4 hold, C5 does not bind; no input changes |
| B2 on the k = 0 arm (p25), wave half binding | wave row 15.89 → 16.13 km/h; peak 2-h GEH 9.83 / 10.37 → 10.65 / 10.98; 15-min RMSPE 0.230 → 0.417 (bound 0.250) [K0 `criteria`, `wave_half`] | R4 and R5's speed half fail: not adoptable on k = 0, which stays on the uncorrected counts [K0 `reading`] |

C9 (p20), two more INCEPTION mornings and the first I-24 holdout, is PENDING the owner's download
(docs/PRE_FRISCO_PROGRAM.md C9; Amendment 9).

- **The lane set on B2's row** (lanes 1–4 at the five-lane sections): 2-h GEH 8.30 → 2.43 at 1,000 m and
  1.28 → 8.21 at 4,800 m, where the fifth lane hid a 628 veh/h shortfall; pooled station-hours 64.6 % → 53.75 %
  [51.8, 55.7] [C7b `readings.b2`]. The criterion's form stays the owner's (Amendment 11).
- **`rcs` fails the gate as B2 does:** link-flow row 31.25 %, station-hours 63.75 % (lane set 54.58 %), 5-min
  speed RMSPE 0.360, no backward wave [C7b `readings.rcs`; `artifacts/i24_validation_dc_refit_rcs.json`].
- **Neither population matches both flows and speeds on corrected counts.** The k = 1 drivers bring the peak
  sections within GEH 5 but form no waves; with the k = 0 drivers the waves stay but the corridor runs faster
  than the recording and peak flow falls (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.7 "Run"). The k = 0 level (0.800)
  was never refit on corrected counts.

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
- **The batteries' own link-flow rows**, which the auto-reports print, count hours from 06:30: 46.1 %
  [43.4, 48.7] here, 75.8 % on B5's refit [V94 `geh`; `artifacts/validation_…_rbc_b5.json` `geh`]. The criterion
  is the gate's C1, anchored as protocol §4 requires.

**Integrity** [V94; W2C `w2_counters_b`]: 0 collisions in 20 runs (636,724 vehicles departed); 0 locks
(Clopper–Pearson 0–16.8 %); departed share 0.984 (lowest 0.982); given-up exits Ruth St 1.95 %, T.H.52 1.27 %;
W1b releases Ruth St 257 of 20,340 entrants, **1.26 %**, above Amendment 4's 1 % disclosure line, T.H.52 1 of
91,644; W2 vetoes Ruth St 6,908, T.H.52 16,011.

**Where it fails.** At the T.H.52 weave, S790 carries 3,871 veh/h in 06:30–07:30 (seeds 3,793–3,930) against
4,846 observed, GEH 14.8 [V94 `geh.link_hours`]. Under Amendment 3 the T.H.52 ramp-to-ramp share is an uncertain
input from the proportional split (u = 0) to 0.70 (u = 1), a stated assumption (docs/TH52_CROSSING_SHARE.md §5);
the gate is judged at u = 0, and Ruth St's split is unexamined. p24 ran the range with and without W2:
- **Material in both.** On the reference, u = 1 against u = 0: S790 06:30 +700.7 [668.8, 732.6] veh/h;
  calibration-day C1 +0.433 [0.411, 0.456], C3 +0.347 [0.327, 0.366], speeds worse; without W2, S790 +688.3
  [660.3, 716.3] [A3 `families.*.verdict_u1`].
- **The gate fails at both ends.** The S790 06:30 shortfall is 975.5 veh/h at u = 0 and 274.8 at u = 1
  (docs/A3_RANGE_ROUND.md §8.6). **No share is chosen:** one enters only by a dated §7 amendment resting on
  MnDOT's study report, a pre-registered count-based estimate or a direct count at the 242B gore (§8.10 there).

Of the inputs tested on I-94 the share moves C1 and C3 most, more than D10's rules or B5's level
(docs/A3_RANGE_ROUND.md §8.10; docs/I94_B5_RESULT.md §6). Every I-94 figure it can move carries the label
"range over the T.H.52 ramp-to-ramp share [proportional, 0.70], stated assumption"; only the reference has a
u = 1 value, and what D10's and B5's arms carry is open (docs/A3_RANGE_ROUND.md §8.10).

**Rounds since the draft** (gate rows, 20 replicates; nothing adopted, adoption is the owner's):

| row | reference, u = 1 | D10 `_rbc` | B5 `_rbc_b5`, s = 0.95 | target |
|---|---|---|---|---|
| C1 cal / val | 79.4 / 78.6 % | 53.3 / 48.8 % | 61.3 [59.9, 62.7] / 56.3 % | ≥ 85 % |
| C3 cal / val | 73.0 / 86.6 % | 39.7 / 39.0 % | 29.3 / 33.4 % | ≤ 15 % |
| C4 km/h (fronts of 20) | 2.7 (7) | 5.4 (20) | 4.4 (15) | 14–22 (≥ 16) |
| C5 collisions; gate | 0; failed | 0; failed | 0; failed | 0 |

[A3 `families.F2.range`; `artifacts/baseline_gate_…_a3u1.json` C4; D10 `arms.rbc.summary.gate_rows`; G5 `checks`.]
- **D10 (p16).** `_rbc` (rules (b) T.H.61 NB from the mainline difference and (c) S792 out of the balance) holds
  D1–D4 against the reference: no collision or lock, realised 0.979 (floor 0.974), C3 39.7 % (ceiling 40.3 %).
  `_rb` (rule (b) alone) raises C1 to 74.8 % but fails D4: C3 47.4 %, paired +0.090 [0.056, 0.124] against
  ≤ +0.02 [D10 `arms.*.criteria`, `arms.rb.paired`]. By the fixed rule B5 runs on `_rbc`.
- **B5 (p17).** The level moves from 1 to 0.95, the grid's lowest factor: 128 of 210 calibration-day station-hours
  under GEH 5 [B5b `chosen`]. The refit holds C1–C4 and the no-lock reading (realised 0.991 against 0.979); C5 does
  not bind [B5b `criteria`]. One fit run at 1.05, below the insertion floor, recorded a collision at the T.H.52
  weave in the warm-up: reported, not investigated [B5b `per_scale`].
- **Neither closes the 06:30 shortfall:** 884–985 veh/h at S791, S790 and S97 in D10's arms; 750–958 at S1070,
  S1948, S791, S790 and S97 after B5 (docs/I94_D10_RESULT.md §7; docs/I94_B5_RESULT.md §6).

Earlier I-94 gate numbers (phase 1; step 3's `_dc`, C1 61.8 %) used inputs that included the validation days; they
stay in the record but are not a clean holdout (docs/I94_CALIBRATION_DAYS.md §2).

## 4. Ring benchmark (Sugiyama emergence, Stern dampening)

**The tests** (`tests/test_microsim/test_microsim_ring_gate.py`, thresholds from `validation.ring_benchmark`; CI
runs seed 42, the battery 20 seeds). *Emergence* (no perturbation, no AV): the across-vehicle speed spread σ_v over
the last 300 s exceeds 1.5 m/s; a vehicle drops below 3 m/s after the 180-s warm-up; the jam drifts backward at
−25 to −5 km/h (the 14–22 km/h band, widened for a short ring). *Dampening* (same seed, one of 22 vehicles a
compliant FollowerStopper): σ_v ≤ 0.75 × baseline; the tail minimum speed rises by more than 0.5 m/s; the tail
mean speed stays above 1 m/s.

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

The golden pins seed 42's summary statistics at relative tolerance 10⁻⁶ under 258c09ac0074 [RG `tolerance`]; the
20-seed block ran under v3 d5472987265c (damped 148959d59786); p22's `docs/reports/e13_ring/` re-ran one seed of it
[P22]. **Caveats:** T = 1.2 s replaced the 1.4-s default because emergence was weaker at 1.4 s (scenario header).
The ring tests string instability and one controller, not any corridor. With the AV it settles near 2.0 m/s
(seeds 1.94–2.16) [I24 `ring.per_seed`]; no baseline mean speed is recorded, so no speed or throughput gain is
claimed.

## 5. Transfer test: E11 on NGSIM I-80 (merging only)

Criteria E1–E4 were pre-registered in docs/PRE_FRISCO_PROGRAM.md E11 and Amendment M1 (docs/MERGE_MODEL.md).
20 seeds per arm on raw NGSIM, with the I-24 fleet unchanged and nothing fitted [E11].

| arm | E1 partner-speed signs | E2 gap at change ≤ 0.9 / 0.8 of normal, recovering by 10 s | E3 model interval overlaps I-80's (6 medians) | E4 zero collisions |
|---|---|---|---|---|
| kept (`lane_change`, LC2013), 43a66c754f31 | met | **not met** | **not met**, 0 of 6 | met |
| `measured`, 7ef6ce60af40 | met | **not met** | **not met**, 3 of 6 | met |

[E11 `criteria`, `rescue`.] The reading is "measured is not rescued".

**The kept configuration is not validated on I-80 either.** Its accepted lag gap is 5.15 [4.99, 5.32] s against
I-80's 1.68 [1.47, 1.87]; its entrants slow to 1.00 [0.85, 1.16] m/s at 50–100 m along the acceleration lane,
against I-80's 3.05. LC2013 merges by stopping and waiting; I-80's drivers merge rolling
(docs/I80_MERGE_VALIDATION.md §5–§7). **Caveat:** the observed sample is 102 entering changes; the extraction finds
113 changes in the zone against 421 counted ramp entries and 445 raw lane 7 → 6 transitions, unexplained. It must
be checked on the raw data before E11 is cited outside the project (§8 there; `artifacts/i80_merge_observed.json`
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
4. **Our own inconsistencies, tested in p23** (docs/I24_CONSISTENCY_C7B.md §11): five simulated lanes against
   four recorded at 1,000 and 4,800 m (the lane-set reading is reported, not adopted); planned demand at
   1.00–1.15 times the target (the coverage-consistent correction failed R2 and R4); inflow stamped 75.7 s late,
   a lower bound (the shift held R1–R5 and is not adopted); the runner's corridor about 72 m shorter than the
   builder's chain, cause not established, unchanged.
5. **I-24 targets are estimates.** They are tracked fragments over a recommended coverage of 0.559–0.674 per
   window [C7 `counting_noise.coverage_per_window`]; ramp-lane coverage is bounded only to [0.33, 1]
   (docs/I24_GEH_DIAGNOSIS.md §7.1); B2's flags are lower bounds, its Old Hickory part provisional (Amendment 5).
6. **I-94.**
   - The T.H.52 share is material over its stated-assumption range and no share is chosen; D10's and B5's arms
     have no u = 1 value (§3). Ruth St's split and `_rbc`'s equal split of S1948 − S791 are unexamined.
   - Ruth St's W1b release share is 1.26 % on the reference and 1.38 % on `_rbc`, both above 1 %, and 0.18 % on
     B5's refit (docs/I94_D10_RESULT.md §6; docs/I94_B5_RESULT.md §5).
   - D10's rule (b) was tested at one single-ramp bracket only (Amendment 10, "Scope of rule 1").
   - C6 passes on the means only because no observed bottleneck there lasts 30 min.
   - The Amendment-1 driver choice used runs on nine-day inputs; the free-flow speed mismatch (108.1 against
     87.9 km/h) is uncorrected (docs/I94_CALIBRATION_DAYS.md §0, §2).
7. **Platform divergence.** SUMO 1.27.1's arithmetic differs between arm64 macOS and x86_64 Linux: on five probed
   fixtures the state parts first at steps 20–47, and three fixture tests change outcome
   (docs/E12_PLATFORM_TESTS.md §7.2–§7.3). The batteries ran on x86_64 Linux; a macOS re-run is not expected to
   match them bit for bit.
8. **Known stale records** are listed in Appendix B.

## 7. What a reader must not conclude

- That any corridor is calibrated to FHWA targets or validated. None is, and the I-24 numbers are in-sample.
- That the ring shows any corridor benefit, or that one AV raises speed or throughput.
- That FlowState's merges are realistic. The locked LC2013 configuration fails E2 and E3 on I-80.
- That `measured` was rejected as worse. It failed the same two criteria, and the rule does not rank.
- That I-94 reproduces its bottleneck. C6 passes only on means with nothing to reproduce, and fails on every
  validation day.
- That I-94's C1 of 79.4 % at a T.H.52 share of 0.70 is near a pass. Its speeds are worse (C3 73.0 %), the gate
  fails there too, and the share is an assumption, not a fit.
- That D10's rules, B5's levels or C7b's shift are adopted or change a gate verdict. They are candidates awaiting
  the owner; every arm fails the gate.
- That B2 settles I-24's drivers. On corrected counts k = 1 misses the waves, k = 0 the flows and speeds.
- That any strategy result is deliverable. The gate failed (protocol §6).

**What would change the verdicts.**
- **I-24:** an arm passing C1 (in the form the owner adopts), C3 and C4 on the C9 validation mornings with the
  model unchanged. The wave row needs a population unstable at capacity; on B6's grid only the five k = 0 pairs
  are [S1 `summary`], and the k = 0 arm on corrected counts fails R4 and R5 [K0]. The record points to items not
  yet pre-registered: lane-set scoring, the 72-m offset, downstream queue discharge, the Old Hickory merge
  (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.7 "Run").
- **I-94:** a T.H.52 share licensed by one of §3's three routes, then C1 and C3 on both day sets, and C4.
- **I-80:** a merge model meeting E1–E4, read after the 113-against-421 count is explained.

## Appendix A. Sources and hashes

| key | artifact |
|---|---|
| [I24] | `artifacts/i24_validation_dc_refit_rc.json`, the I-24 battery (stage p13) |
| [B2] | `artifacts/boundary_b2_corridor.json`, B2's R1–R5 readout |
| [C7] | `artifacts/i24_geh_diagnosis.json`, arm `dc_refit_rc` |
| [C7b] | `artifacts/i24_consistency_c7b.json`, C7b's readout (stage p23) |
| [B5a] | `artifacts/demand_level_fit_i24.json`, `artifacts/demand_level_i24.json` (stage p15) |
| [K0] | `artifacts/boundary_b2_k0_corridor.json`, B2 on the k = 0 arm (stage p25) |
| [L14] | `artifacts/i24_locks_p14_b2_ref.json` |
| [S1] | `artifacts/driver_joint_screen_i24.json` |
| [G94] | `artifacts/baseline_gate_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2.json` |
| [V94] | `artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2.json` |
| [W2C] | `artifacts/weave_w2_corridor.json` (stage p10) |
| [A3] | `artifacts/a3_range.json`, Amendment 3's range round (stage p24) |
| [D10] | `artifacts/i94_d10_corridor.json` (stage p16) |
| [B5b] | `artifacts/demand_level_fit_i94.json`, `artifacts/demand_level_i94.json` (stage p17) |
| [G5] | `artifacts/baseline_gate_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2_rbc_b5.json` |
| [E11] | `artifacts/i80_merge_validation.json` (stage p21) |
| [P22] | `artifacts/p22_reports.json` and the reports under `docs/reports/e13_*/` (stage p22) |
| [H] | `tests/golden/scenario_config_hashes.json` |
| [RG] | `tests/golden/ring_sugiyama.json` |

| configuration | v3 (in its battery) | v4 (current) |
|---|---|---|
| I-24 `i24_replica_flow_rc_speedcal_dc_refit` | 909b89f298c5 [I24] | 7082bcea5442 [H] |
| I-94 `mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2` | 5080d84d4725 [V94] | 395a111cb991 [H] |
| `ring_sugiyama` | d5472987265c [I24 `ring`] | 258c09ac0074 [H, RG] |
| I-80 kept, `i80_replica` | — | 43a66c754f31 [E11 `arms`] |
| I-24 `…flow_rcs_speedcal_dc_refit` (C7b, B5's from-arm) | — | 921fb1f42c67 [C7b `selection.b5`] |
| I-94 `…_rbc` (D10) / `…_rbc_b5` (B5) | — | ad158ff561b1 / bd00fbaba89a [D10; B5b `chosen`] |

## Appendix B. Known stale records

- `artifacts/boundary_b2_corridor.json` `status` still reads "PROPOSED, not adopted".
- docs/PAPER_DRAFT.md describes B2 as not run; its `_dc_refit` numbers, like CHANGELOG 2.6.0's, rest on
  uncorrected counts.
- `docs/reports/i24_replica/` is from the 3 Sep battery; p22's `docs/reports/e13_*/` are current [P22].
- I-24 batteries before p23 record no locks; sidecars cover p12 and p14 only.
- p16's records carry Amendment 4's "re-run pending" label, now historical (Amendment 4, "Resolved").
- The I-94 battery's ring rows were not evaluated (`--ring-seeds 0`); the I-94 gate's C2 text quotes C1's
  per-replicate interval [G94 `checks`].
- The p13/p14 readouts' `code` names a VM snapshot outside the repository;
  `artifacts/i24_boundary_ramps_fit.json`'s `base_config_hash` matches no policy (docs/CONTRACTS.md).
