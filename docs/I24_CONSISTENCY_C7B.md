# I-24 scoring and input consistency before B5 (C7b): pre-registration

Written 2026-10-07, after C7 (docs/I24_GEH_DIAGNOSIS.md) and before any run of this round. Nothing here was
simulated. Every number below is computed from committed files at $0
(`artifacts/i24_consistency_2026-10-07/c7b_expected.json`, written by
`artifacts/i24_consistency_2026-10-07/harness/corridor_c7b.py expected`) or quoted from C7's artifact
(`artifacts/i24_geh_diagnosis.json`) and the other artifacts named where they are used. The criteria are §8.4.3's R1–R5 (docs/I24_DISCHARGE_DIAGNOSIS.md) and GEH 5;
no other threshold is introduced. Stage `p23_c7b` (`artifacts/i24_consistency_2026-10-07/stage_p23_c7b.sh.txt`),
readout `artifacts/i24_consistency_c7b.json`. It runs before `p15` (docs/PRE_FRISCO_PROGRAM.md, schedule change),
so that B5 fits a demand level against a consistent target. Nothing here is adopted; adoption is the owner's.

**One departure from the brief, flagged before anything runs (§4.2).** The brief asked for the
coverage-consistent family with "planned/target = 1.00 in every window" and "s = 0.925 carried". On a base built
at the target's own coverage, planned/target equals s in every window, so the two cannot both hold. Carrying the
multiplier 0.925 would plan 14.4 % fewer vehicles than B2; s = 1 would plan 7.4 % fewer. On the committed
demand-scale grids the first level runs free in every window and the second sits in the transition to free flow in
most. Under either, R5 would very likely fail on the level alone, and the rule would then reject the coverage
correction for a reason B5 exists to fix. This document therefore
pre-registers carrying B2's planned level (computed: s = 1.080413, nothing fitted). The two literal readings
remain one harness flag each (`--level s`, `--level target`), with their expected hashes in §4.2. The coordinator
may switch before this file is committed, not after.

## 1. What C7 found

On the B2 arm (`i24_replica_flow_rc_speedcal_dc_refit`, 909b89f298c5 under hash policy 3), 100 of the row's 144
(section, 5-min) bins fail. The classes split 75 recording noise, 4 level, 6 timing and 15 shape. By the rule,
recording noise is largest under every order, so a station-hour amendment is proposed. C1's station-hour form
reads 64.6 % pooled (per-replicate interval 61.8–67.4 %) and 58.3 % on the replicate mean, against the row's
30.6 %. Three consistency defects of ours were also found:

- **Lane sets.** At 1,000 m and 4,800 m the sections lie on 5-lane edges: the Old Hickory acceleration lane and the
  Hickory Hollow–Bell Road weave lane. The simulated count reads every lane; the observed count reads lanes 1–4.
- **Demand coverage.** The corrected arms divide the tracked counts by the builder's equilibrium coverage, then
  multiply by s = 0.925. The row's target divides the same counts by the recommended coverage. Planned/target is
  therefore 1.002–1.152, rising through the period.
- **Insertion time.** The mainline inflow carries the clock time of the count at data x = 200 m. Vehicles enter
  2,452 m upstream, 75.7 s away at the fleet's mean v0.

## 2. Station-hour reporting: proposed Amendment 11 (text)

*Proposed number; the coordinator assigns it. Written before any run that uses it.*

> **Amendment 11 — 2026-10-07: I-24 is reported in C1's station-hour form beside its 5-min row.** C7
> (docs/I24_GEH_DIAGNOSIS.md) classified B2's failing link-flow bins by its fixed rule. Recording noise is the
> largest class under every order: 75 of 100 failing bins have an observed 5-min count at GEH ≥ 5 from its own
> centred 15-min mean. Scored against that mean, the recording passes 31.25 % of its bins; the model's row is
> 30.6 %. From now on, every I-24 battery reports C1's station-hour form (§4) beside the criteria row.
> - Hours are anchored at the study period's start: 06:30–07:30 (windows 0–11) and 07:30–08:30 (windows 12–23).
> - A station-hour's simulated flow is, per replicate, the sum of its twelve 5-min crossings. That equals the mean
>   of their twelve hourly-equivalent flows.
> - The observed station-hour is the mean of the row's recommended-coverage table
>   (`hourly_flows_veh_h_recommended`) over the hour.
> - Each (replicate, section, hour) is one GEH comparison, pooled over the replicates. On I-24 that is
>   20 × 6 × 2 = 240 comparisons, the form `validation.baseline_gate` uses for C1.
> - The share with GEH < 5 is read against 85 % (`fhwa_default`). The per-replicate share's mean and 95 % t-interval,
>   and the replicate-mean form (12 comparisons, the I-24 row's convention), are reported beside it
>   (`scripts/i24_geh_diagnosis.py station_hours`).
>
> The I-24 gate stays on the committed row (144 five-minute bins, replicate mean) until the owner adopts this
> amendment. Reporting both forms changes no verdict. B2 reads 30.6 % on the row and 64.6 % / 58.3 % on station-hours;
> both fail 85 %.

## 3. Lane-set consistency (a scorer correction, not a model change)

**Rule.** At every section, the simulated count reads the observed lane set, lanes 1–4, the four leftmost. In
SUMO's numbering (0 = rightmost) these are the four highest lane indices of the section's edge. The runner's
acceleration and weave lanes are lane 0 of their attach edge (`microsim.runner`). So only the 5-lane sections
change: lanes 1–4 are read at 1,000 m and 4,800 m, and every lane elsewhere, as now. The correction is to our own
scoring; the model and the recording are untouched.

**Built (opt-in; the defaults write what they wrote before).**
- `scripts/i24_validate.py --lane-crossings` records `simulated.lane_crossings` for every replicate and section:
  - `by_lane[lane][window]`: crossings by the SUMO lane of the later sample. The crossing rule is
    `crossings_per_window`'s, so the sum over lanes is the all-lane count exactly, checked
    (`sums_equal_counts_per_replicate`).
  - `transitions[prev][cur]`: crossings by lane pair over the period.
  - The section's lane count is one more than the largest index crossed in. Its lane set is the four highest
    indices.
  - The lane-set counts (per replicate, replicate mean, hourly-equivalent) follow. `geh.lane_set` adds the three
    GEH tables on them beside the row; the criteria row is unchanged.
- `--section-lanes observed` scores the criteria row on the lane-set table (`geh.primary` `recommended_lane_set`;
  `--criteria-only` keeps it). C7b does not use it: the stage reports both forms and changes no row.

**Residual difference from the recording's rule.** The recording drops lanes ≥ 5 before counting
(filter-then-cross). The simulated lane-set count attributes each crossing to its later sample's lane. The two differ
only for crossings whose two samples, 0.5 s apart, straddle the lane set's edge. `into_set_total` and
`out_of_set_total` count those crossings, so the residual is measured, not assumed.

**What the correction does to B2's current row, from C7's bounds (reported, no re-threshold;
`c7b_expected.json` `lane_set_bounds_b2`).** The archives keep no per-lane counts, so only bounds are available
until the stage runs.
- **2-h flows** (C7 §7.1, like for like):
  - 1,000 m: GEH 8.30 as scored becomes 0.20 at the auxiliary band's lower bound and 5.12 at the row's coverage.
  - 4,800 m: GEH 1.28 becomes 5.68 and 8.29, a shortfall that the extra lane hides.
- **The 5-min row.** 44 of 144 bins pass now (30.6 %); 7 of them are at 1,000 m and 11 at 4,800 m. The correction
  re-scores those 48 bins and leaves 96. With nothing assumed about the 48, the row lies between 26/144 = 18.1 % and
  74/144 = 51.4 %. In C7's comparison (the model's all-lane count against lanes 1–4 plus the recording's
  auxiliary-band flow), spreading the 2-h band flow uniformly or in proportion to each window's lanes-1–4 count,
  at coverage 1 or at the row's coverage, the row reads 25.7–30.6 % (37–44 bins).
- **Station-hours** in the same comparison: pooled 64.6 % becomes 51.7–68.3 %; replicate mean 58.3 % becomes
  50.0–66.7 %.

The correction is consistency, not a gain: at 4,800 m it uncovers a shortfall. The stage measures the actual effect
on B2's re-run from its own per-lane counts.

## 4. Coverage-consistent demand (`…_rcc`): an input correction like B2

### 4.1 The correction

`scripts/i24_build_replica.py --demand-coverage recommended` divides the corrected arm's mainline and on-ramp inflows
by `artifacts/i24_coverage.json` `pooled.recommended_filled` per 15-min window. These are the values
`scripts/i24_validate.py` divides the observed counts by for the row's target. This is the arithmetic of the existing
`--coverage-estimator recommended`. The new option adds a family guard: its letter `c` must appear in the suffix
token, `rc` + `c` + `s`, e.g. `flow_rcc`. It also refuses a coverage artifact made from another recording. It records
a `demand_coverage` block and states the coverage in the corrected arm's header. The B2 counts (`--ramp-through-traffic
exclude`), the exit fractions and the boundary are unchanged.

Per 15-min window from 06:30 (`c7b_expected.json`):

| window | c_used (equilibrium) | c_rec | c_rec / c_used | B2 planned / target | rcc planned / target (pre-registered) |
|---|---|---|---|---|---|
| 06:30 | 0.6053 | 0.6558 | 1.0836 | 1.002 | 1.080 |
| 06:45 | 0.5591 | 0.6325 | 1.1313 | 1.046 | 1.080 |
| 07:00 | 0.5645 | 0.6481 | 1.1479 | 1.062 | 1.080 |
| 07:15 | 0.5624 | 0.6737 | 1.1978 | 1.108 | 1.080 |
| 07:30 | 0.5036 | 0.6274 | 1.2458 | 1.152 | 1.080 |
| 07:45 | 0.4909 | 0.5872 | 1.1963 | 1.107 | 1.080 |
| 08:00 | 0.4805 | 0.5592 | 1.1638 | 1.076 | 1.080 |
| 08:15 | 0.4839 | 0.5842 | 1.2074 | 1.117 | 1.080 |

- **B2's column** is computed from the arm's written inflows (mainline and on-ramps, planned vehicles over tracked
  counts / c_rec). It reproduces C7's `inputs_vs_targets` to 1e-3.
- **On the rcc base**, the coverage ratio is 1.000 in every window, and planned/target is the level s in every
  window.

### 4.2 The level: why B2's planned level is carried, not the multiplier

| level | s on the rcc base | planned/target, every window | planned vehicles, 06:30–08:30 (B2: 14,640) | per window, the B2-base scale it equals |
|---|---|---|---|---|
| `s`: 0.925 carried | 0.925 | 0.925 | 12,534 (−14.4 %) | 0.742–0.854 |
| `target`: s = 1 | 1.000 | 1.000 | 13,551 (−7.4 %) | 0.803–0.923 |
| **`b2`: B2's level carried (pre-registered)** | **1.080413** | **1.080** | **14,640 (0 %)** | **0.867–0.997** |

Planned vehicles are the 24 window rates × 300 s, summed over the mainline and on-ramp streams. The last column
gives, for each window, the scale on B2's equilibrium-coverage base that plans the same demand (s × c_used / c_rec).

- **Where the committed grids say these levels run.**
  - On B2's own base (`artifacts/demand_scale_i24_flow_rc.json`, p14, one seed per scale), 5-min segment-speed
    RMSPE is 1.218 at 0.8 (free flow, inserted 0.995) and 0.361 at 0.9.
  - On the `_dc` base (`artifacts/demand_scale_i24_flow_dc.json`, p4), it is 0.887 at 0.8, 0.451 at 0.825 and
    0.398 at 0.85.

  The literal multiplier puts every window in 0.742–0.854; s = 1 puts most in 0.80–0.88. B2's R5 bound is 15-min
  RMSPE ≤ 0.254 + 0.02 (§8.4.4). It would very likely fail on the level alone.
- **Why the multiplier does not carry.** docs/I24_DATA.md ("Tracking coverage revisited") records that the
  recommended estimator and the speed-fitted demand level measure the same overshoot: "0.85 ≈ 1/1.17: a data-only
  estimator and the simulation fit agree". s was fitted on the equilibrium base and compensates it, so carrying it
  onto the recommended base applies the correction twice.
- **The rule, fixed now.** s_rcc = 0.925 × V(B2 base) / V(rcc base) = 0.925 × 15,827.29 / 13,550.60 = 1.080413.
  V is the planned study-period vehicles defined above. This is computed, not fitted. The rcc arm keeps B2's
  planned total and changes only its time profile. Against B2 per window that is +7.8 %, +3.2 %, +1.8 %, −2.5 %,
  −6.2 %, −2.4 %, +0.4 % and −3.3 %, which addresses C7's first-hour shortfall and 200-m second-hour excess. The
  level itself is B5's to refit.

### 4.3 Expected documents

| arm | file | config hash, policy v4 | policy v3 (`config_hash_v3`) |
|---|---|---|---|
| B2 (reference) | `scenarios/i24_replica_flow_rc_speedcal_dc_refit.yaml` | 7082bcea5442 | 909b89f298c5 |
| rcs | `scenarios/i24_replica_flow_rcs_speedcal_dc_refit.yaml` | 921fb1f42c67 | 29fce53890bb |
| rcc | `scenarios/i24_replica_flow_rcc_speedcal_dc_refit.yaml` | e17c205d725d | 639cc02b0830 |
| rccs | `scenarios/i24_replica_flow_rccs_speedcal_dc_refit.yaml` | a35b0f3b8b10 | a43dfb109248 |

Under the literal levels, rcc / rccs would be 59acbc596d18 / d6f28e50afbf (`s`) or cadd00b292e6 / c0faf908be0c
(`target`), policy v4.

The policy-v4 hashes were computed in a working tree whose `flowstate_core.config` (policy 4, the W1b + W2
builder's change) was not yet committed. `c7b_expected.json` therefore names no commit: `code` is null,
`code_dirty_paths` lists the files that differed from HEAD, `code_sha256` names them, and `config_hash_policy` is 4.
A hash quoted in a committed record (909b89f298c5, 219f7db55a74) is compared only through `config_hash_v3`.

The harness checks each arm's hash against the derivation under either policy. On the VM, `corridor_c7b.py arm`
writes an arm only if three things hold:

- the rebuilt family's inputs equal the committed `_rc` inputs in everything the correction does not touch (data
  hash, mainline and ramp counts, B2's corrected counts, exit fractions, boundary, entry lanes, geometry, equilibrium
  coverage), and record exactly the correction;
- its driver-calibrated base equals the derived base;
- the fitter's own `scaled_config` output equals the derived arm.

## 5. Insertion-time shift (`…_rcs`, `…_rccs`): computed, not fitted

`scripts/i24_build_replica.py --insertion-shift-s free_flow` moves every mainline inflow step after the first earlier
by the free-flow time from the network entry to the count section. The entry is sim x = 0, the first corridor edge's
start, where `microsim.vehicles` inserts with `departPos="base"`. The count section is data x = 200 m. The time is
taken at the fleet's mean v0 and rounded to 0.1 s.

- **On the `_rc` geometry:** distance = 2,256.5337 + 0.979853 × 200 = 2,452.50 m. v0 = 32.3996 m/s, equal in the
  builder's population (`artifacts/idm_i24_capacity.json`) and the arms' (`…_amax_k1.0.json`). That gives 75.6955 s;
  **the shift is 75.7 s**. C7 found 75.7 s on the validator's mapping.
- **The steps.** The first step stays at 0 s and covers the warm-up. The others start at 824.3, 1,124.3, …,
  7,424.3 s instead of 900, 1,200, …, 7,500 s. Rates are unchanged.
- **Recorded.** The `insertion_shift` block of the family's inputs holds the rule, distance, v0, the population's
  sha256, the exact and applied values, and both time grids. The scenario headers state it too. The family guard
  is the letter `s`. An explicit number in (0, 300) s is also accepted and recorded as `explicit`; C7b uses
  `free_flow`.
- **What it does not move.** On-ramp inflows (C7 did not compute their offsets) and exit fractions (applied at each
  vehicle's departure time, `microsim.vehicles.build_corridor_plan`) are unchanged.
- **75.7 s is a lower bound** on the fleet's mean travel time. Heterogeneous v0, edge limits below v0, insertion
  below v0 and congestion all lengthen it (C7 §6).
- **Levels.** rcs carries s = 0.925 (B2's rates and level, moved in time). rccs is rcc's document with rcs's times;
  its level is rcc's s_rcc.

## 6. Criteria, readings and the rule (fixed now)

**Arms and pairing.** rcs, rcc and rccs, each against the B2 arm re-run on the same VM. Each runs 20 seeds,
spawn_seeds(42, 20), step 3's seeds. The R-criteria are §8.4.3's, read exactly as stage p13's
`harness_b2/corridor_b2.py score` read them:

| # | criterion |
|---|---|
| R1 | 0 collisions in every run, every replicate recording the counter |
| R2 | mean realised demand (departed / planned) ≥ the B2 re-run's |
| R3 | each ramp's modelled 2-h flow (vehicles.parquet, `corridor_b2.py reduce`) within GEH 5 of its corrected count at the pooled recommended coverage |
| R4 | 2-h GEH at 2,200 and 3,200 m (pooled targets 6,626 / 6,639) not above the B2 re-run's |
| R5 | the wave verdict unchanged where the B2 re-run's wave row passes (it failed on p13, so this half binds only if the re-run passes); 15-min segment-speed RMSPE ≤ the B2 re-run's + 0.02 |

**The B2 re-run must reproduce the committed p13 battery exactly** (`artifacts/i24_validation_dc_refit_rc.json`).
That covers the scenario (both hashes name the same document), seeds, per-replicate counts, realised fractions,
collision counts, segment speeds, standard and stripe fronts, every criteria row's value and verdict, and the
observed side apart from its build time. One row is excepted: `no_locks`, which p13 did not record and the re-run
does. Its lock flags are reported beside p14's re-scored ones (`artifacts/i24_locks_p14_b2_ref.json`). A difference
stops the stage before any arm runs (`corridor_c7b.py check-ref`); nothing is read and the owner decides.

**Reported for all four batteries, not gating:**
- the 5-min row and C1's station-hour share both ways (every lane as scored; the observed lane set), pooled with
  the per-replicate interval and on the replicate mean;
- 2-h flows and GEH at all six sections, both ways;
- the per-window planned/target ratios;
- `no_locks`, and the coordinator's breakdown reading (a replicate realising < 0.9 while the battery's mean is
  ≥ 0.95);
- paired per-seed differences against B2 (realised share, section flows; 95 % t-intervals);
- the lane-set residual (`into_set_total` / `out_of_set_total`).

**Problems that block the reading:**
- a missing battery or reduction;
- the re-run not reproducing p13;
- seeds that are not step 3's;
- an arm battery whose hash does not name its derived document;
- batteries scored against different observed sides;
- reductions that are not their battery's;
- a battery without lane crossings, or whose per-lane sums differ from its counts;
- a section whose crossed lane count differs from its edge's lane count in the `_rc` geometry (4, 5, 4, 4, 5, 4);
- a void count check.

**The selection rule (the coordinator's, made exact here).** The candidates are B2 and every arm that holds R1–R5.
The arm B5 refits is the candidate with the largest C1 station-hour share, pooled over the 20 replicates, on the
observed lane set. The lane-set form is used because it is the like-for-like count of §3, applied to every candidate
alike. Ties are exact equality and go to the smaller change: B2 < rcs (moves no rate) < rcc (changes every window's
rate) < rccs. A recorded problem leaves the selection undetermined.

**What each outcome means for B5 (stage `p15_i24_b5`).**
- **B2 selected:** p15 runs as built (its default from-arm). The coverage and timing corrections stay proposed and
  are not used.
- **rcs, rcc or rccs selected:** first commit the family's scenarios, inputs and battery from the archive. Then p15
  runs on the selected arm with `--p15-arm FROM:BASE:LABEL[:SCALE]`, which the readout writes in full
  (`selection.b5.p15_arm`). For rcc it is
  `i24_replica_flow_rcc_speedcal_dc_refit:i24_replica_flow_rcc_corrected_dc:dc_refit_rcc:1.080412746822078`.
  - B5's insertion floor then reads that arm's battery.
  - On an rcc base, B2's level is s = 1.080, inside B5's coarse grid (0.6–1.1) and near its top. The refine step
    (±2 × 0.025) reaches 1.15 at most.
- **Undetermined:** B5 waits; the owner decides.
- **B5's objective (whatever the outcome).** It scores the row's 06:30–07:30 bins, which include 1,000 m and
  4,800 m, on every lane. If the owner adopts §3, the objective should read the lane-set count there too. That is a
  change to `scripts/fit_demand_level.py`, another owner's file, and is not made here.

## 7. The stage, its cost and its launch

Stage `p23_c7b` (in `scripts/gcp/pipeline_i24.sh` after p21's block; `stage_p23_c7b.sh.txt` is its copy, checked
verbatim by the tests; `scripts/gcp/ingest_pipeline_results.sh` takes its new artifacts) runs four steps:

1. **Families.** Builds the three families with p13's builder line plus each family's flags; applies the
   Amendment-1 driver calibration; writes each arm with `corridor_c7b.py arm`. Any refusal runs nothing.
2. **B2 re-run.** Label `dc_refit_rc_p23`, with `--lane-crossings` and the ramp-flow reduction, then `check-ref`.
   An interim archive follows.
3. **Arms.** The three arm batteries in the order rcs, rcc, rccs (labels `dc_refit_<code>`), each archived as it
   finishes.
4. **Readout.** `corridor_c7b.py evaluate`.

Every battery uses scripts/i24_validate.py with 20 replicates, the 20-seed ring rows and `--analysis-procs 8`. The
`dc_refit_*` labels put every replicate's `meta.json` in the light archive and the first seed's replicate in the
full one.

**Cost [estimate]** on n2d-standard-16 at about $0.78/h:
- three builds at about 2–3 min each;
- four batteries at 1,313–1,697 s each (p13: 1,522 / 1,663 s; p14: 1,313–1,697 s), plus about 1–2 min each for the
  per-lane pass and the reduction.

That is about 115–130 min of stage time and 127–142 min billed, **about $1.65–1.85**. A failed `check-ref` stops at
about 40 min (about $0.7). `--cap-min 210` bounds it at about $2.7. The running total of docs/PRE_FRISCO_PROGRAM.md
rises by this amount.

```
scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p23 --machine n2d-standard-16 \
  --zone us-east1-b,us-east1-c,us-east1-d --bucket gs://<bucket>/p23 \
  --self-delete --via-bucket --data-set i24 --cap-min 210 --pipeline-args '--stages "p23_c7b"'
```

## 8. Predictions (written before the run; not criteria)

- **rcs:** R1–R5 hold (75.7 s is a quarter of a window). The 200-m cross-correlation lag moves from +57 s toward 0.
  The station-hour share moves by at most a few comparisons.
- **rcc:** more demand early, less late. The 200-m second-hour excess (+401 veh/h on station-hours) shrinks. The
  first-hour shortfall at 2,200 / 3,200 m narrows only as far as the downstream end discharges (§8.4.6). R2 is the
  criterion at risk: more early demand meets the morning's first queue.
- **Lane set:** 1,000 m's level miss disappears or turns into a small shortfall, and 4,800 m shows a shortfall of
  about 450–660 veh/h (C7 §7.1's bounds).

## 9. Limits

- **One recorded morning** (30 Nov 2022), one direction. The scored window is the window the inputs come from. Every
  arm still fails the battery's gate. Nothing here is validation.
- **The shift is a lower bound,** and only the mainline moves (§5).
- **The level is a choice** (§4.2). B2's planned total is carried so that C7b tests the shape, and B5 refits the
  level.
- **The lane-set count attributes a crossing to its later sample's lane;** the residual against the recording's
  rule is measured (§3).
- **Outside C7b, seen while building it, not corrected.** The runner's corridor is 8,555.74 m against the builder
  chain's 8,629.80 m. Edge 977008894 starts at runner x 3,070.07 m, not 3,141.82 m. That compares a p12 replicate's
  `meta.json` (`runs/i24_validation/p12_dc_refit_ref/…`, from that stage's archive, on the same corrected map as B2;
  `ramps[0].attach_x_m`, `corridor.total_length_m`) with the `_rc` inputs' edge lengths.
  The validator maps data x to sim x on the builder's chain and applies it to the runner's x, so the simulated
  sections downstream of 977008894's start sit about 72 m further along their edges than the builder places them:
  1,000 m is 166 m into its edge, not 94 m. Every section stays on the same edge, so neither the lane sets nor the
  shift (measured to where the simulated count is taken) change. The cause is not established.
- **Hash policy.** Every hash computed here is policy v4 (uncommitted in the working tree on 2026-10-07; it lands
  with the W1b + W2 builder's commit, before this round's). A committed record's policy-3 hash is compared through
  `config_hash_v3`, never against a v4 hash; the harness names a document by either and records
  `config_hash_policy`.

## 10. Reproduce

```
uv run --no-sync python artifacts/i24_consistency_2026-10-07/harness/corridor_c7b.py expected
uv run --no-sync pytest tests/test_scripts/test_i24_build_replica_c7b.py \
    tests/test_scripts/test_i24_validate_lanes.py tests/test_scripts/test_corridor_c7b_harness.py
bash -n artifacts/i24_consistency_2026-10-07/stage_p23_c7b.sh.txt
```

The §3 bounds are `c7b_expected.json` `lane_set_bounds_b2` (`corridor_c7b.py` `lane_set_bounds`), computed from
`artifacts/i24_validation_dc_refit_rc.json`, `artifacts/i24_count_consistency.json`
(`sections[*].aux_band_tracked_veh_h`) and `scripts/i24_geh_diagnosis.py`'s `station_hours`; the tests pin them.
