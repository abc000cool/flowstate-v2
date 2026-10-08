# Pre-Frisco model work, Phases B–E: the program plan

Written 2026-10-07, before any run it names; nothing was simulated. Thresholds
are quoted from the named document; a **new** one is proposed as a dated
amendment before its run (numbered 6–9 here; the protocol's Amendments 3–5 are
the Phase A decisions recorded on 2026-10-07). Phase A (B2, W1b/W2, Amendment 3, I-24 locks) is
decided elsewhere. Nothing needs Frisco data.

**Cost model.** n2d-standard-16 at about $0.78/h (I24_DISCHARGE_DIAGNOSIS §8.4.5),
$0.013/min; boot and setup about 12 min; an I-24 20-seed battery 25–27 min
(p13: 1,522 s); an I-94 four-hour battery about 60 min at `--procs 10` (p8: 3,527 /
3,708 s); a single-seed I-24 run 12–13 min in a wave of 14 (p14's fit). Quota: 16
N2D CPUs per region, 32 overall, so at most two VMs in different regions. Launch
with `scripts/gcp/launch_i24_pipeline.sh --machine n2d-standard-16 --self-delete
--via-bucket --cap-min N` from a pushed commit; the owner approves each launch.

**Common rules.** 20 seeds = `spawn_seeds(42, 20)`. Zero collisions in every run
(protocol C5). No lock = no queue standing without discharge for 10 min or more in any replicate (`validation.locks`, the rule every battery records since 2026-10-07, b50211d); a seed whose realised demand falls below 0.9 while the battery's mean stays at or above 0.95 is a report-only *breakdown* (the coordinator's reading below), not a lock. (Superseded wording, 2026-10-08: "no seed's departed share below 0.8 of the battery median".)
(§9.5). A failed criterion is reported, never re-thresholded. Adoption is the
owner's.

---

**Status — 2026-10-08: B2 on the k = 0 arm run** (p25, code f0f76c2; I24_DISCHARGE_DIAGNOSIS §8.4.7 "Run"). The wave half held (16.13 km/h); R4 (GEH 10.65 / 10.98) and R5's speeds (RMSPE 0.417 against 0.250) failed. B2 does not hold on k = 0, which stays on the uncorrected counts; the k = 1 candidate is unchanged. About $0.5–0.7 [estimate]. Nothing adopted. With the corrected counts neither population matches both flows and speeds.

**Status — 2026-10-08: Amendment 3's range round run** (p24, code 466193f; docs/A3_RANGE_ROUND.md §8; FRISCO_PROTOCOL "Adoption of Amendment 3", "Run"). Material in both families: at u = 1 against u = 0, S790 06:30 +700.7 [668.8, 732.6] veh/h, calibration-day C1 36.1 → 79.4 %, C3 38.3 → 73.0 % (F2); the gate fails at both ends and no share is chosen. F2's u0 reproduced p10's arm B in 20 of 20 seeds: Amendment 4 (i) holds and the W1b/W2 defaults stand (Amendment 4, "Resolved"). About $2.7 [estimate]. Open: the range reading for I-94 arms not run at u = 1 (D10's, B5's). p24 is not in the schedule table; it ran on a Central slot.

## B5 — a demand fitter whose objective sees insertion

**Purpose.** `scripts/i24_fit_demand_scale.py` minimises segment-speed RMSPE on
one seed per scale. It cannot see vehicles held off the network and has twice
picked a backlog: Amendment 2's refits realised 0.918 / 0.930 against the 0.977
floor, and p14 chose s = 1.125 with 0.783 inserted, failing C2 and C3 (§8.4.6).
Protocol §7.1 and CLAUDE.md §6.3 already require a GEH-driven fit. B5 aligns the
fitter, then reruns the FHWA re-sequence on I-24 and I-94.

**Inputs.** The fitter's opt-in `--min-inserted` / `--objective geh`; Phase A's
I-24 arm (expected the B2 arm `i24_replica_flow_rc_speedcal_dc_refit`, 909b89f298c5;
base `…flow_rc_corrected_dc`, 219f7db55a74; battery
`artifacts/i24_validation_dc_refit_rc.json`, realised 0.967);
`artifacts/i24_validation_observed.json`. I-94: the arm D10 selects, the
calibration-day observations (`artifacts/p1_rehearsal_2026-10-04/`), count error
0.05 (`dq/data_quality.json`, `parameters.count_error`). No external data.

**Design.** `calibration/demand_level.py` (pure selection rule) and
`scripts/fit_demand_level.py --corridor i24|i94`, reusing the I-24 fitter's
helpers and the battery's station-hour scorer; the old script's defaults stay
byte-identical. Rule (proposed Amendment 6):
- *Objective:* the share of scored flow bins with GEH < 5 in the fit window. On
  I-24 these are the criterion row's (section, 5-min) bins of 06:30–07:30, as
  `--objective geh` reads them, with 07:30–08:30 held out. On I-94 they are the
  calibration-day station-hours (C1). Ties go to the smaller mean GEH, then the
  smaller change. Speed RMSPE is reported only.
- *Constraint:* the mean inserted fraction over the fit seeds must be at least
  the from-arm battery's realised share − 0.01 (Amendment 2 clarification; §8.4.5
  C2). If no scale qualifies, the fit flags `constraint_unmet` and stops.
- *Seeds:* five per scale, the battery's first five (**new**). One seed moved the
  GEH share 1.4 points per bin and the inserted fraction up to 0.009 from the
  20-seed mean (Amendment 2 re-analysis).
- *Grids:* I-24 coarse 0.6–1.1 by 0.1, then ±2 × 0.025 around the constrained
  choice. I-94 uses one corridor-wide factor, 0.95–1.05 by 0.025, inside §7.1's
  count uncertainty.

Stages: `p15_i24_b5` (fit; a 20-seed battery via `scripts/i24_validate.py`;
braking counts and ramp reductions as in p14) and `p17_i94_b5` (fit; p8's battery,
`scripts/baseline_gate.py` and the gated report).

**Criteria** (§8.4.5 C1–C5, against the from-arm, same seeds):
- C1: zero collisions.
- C2: realised share ≥ from-arm − 0.01.
- C3: GEH < 5 share not below the from-arm's.
- C4: 15-min segment-speed RMSPE ≤ from-arm + 0.02.
- C5: wave verdict unchanged where the from-arm passes.

I-94 adds a no-lock check and reports gate C1/C3/C4/C6 on both day sets. Tests:
with the options off the artifact is byte-identical, and synthetic grids pin the
rule.

**Readout.** `artifacts/demand_level_{i24,i94}.json` (per-scale, per-seed
readings); dated sections in I24_DISCHARGE_DIAGNOSIS §8.4 and I94_CALIBRATION_DAYS.

**Cost.** p15: 50 fit runs plus a battery, about 82 + 12 min, **$1.2** (cap 180 =
$2.3). p17: 25 four-hour runs plus battery and gate, 155 + 12 min, **$2.2** (cap
300 = $3.9).

**Depends.** p15 on Phase A's I-24 arm; p17 on Phase A's I-94 guards and on D10.

**Frisco.** Protocol §7.1's fit, ready for Frisco's counts.

**Stop.** If the default-off check is not byte-identical, or `constraint_unmet`.

**Status — 2026-10-08.** D10 selected `_rbc` as the I-94 from-arm: p17 runs on `scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2_rbc.yaml` (ad158ff561b1), read against its battery `artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2_rbc.json` (realised 0.979; C2 floor 0.969), under the Amendment-4 label. p15 (I-24) waits for p23's selected arm.

**Status — 2026-10-08 (I-24).** C7b selected `rcs`: p15 runs with `--p15-arm i24_replica_flow_rcs_speedcal_dc_refit:i24_replica_flow_rcs_corrected_dc:dc_refit_rcs` (921fb1f42c67; battery realised 0.9670, C2 floor 0.9570) on the pre-registered all-lane objective; its battery records crossings by lane so the lane-set share is reported beside it.

**Status — 2026-10-08: both corridors run** (Amendment 6). I-24 (p15, code d8186f6; docs/I24_B5_RESULT.md): the level stayed at 0.925. I-94 (p17, f454c7d; docs/I94_B5_RESULT.md): on `_rbc` the level moved from 1 to 0.95 (128 of 210 station-hours under GEH 5); C1–C4 and no-lock hold, C5 n/a, candidate; adoption is the owner's. Both arms still fail the gate. I-24 detail (p15): On `rcs` the fit kept s = 0.925 (22 of 72 fit-hour bins, tied with 0.9, decided by mean GEH). The refit is `rcs` under another name and reproduces its battery; C1–C4 hold, C5 n/a, candidate; nothing changes on I-24, and adoption is the owner's. I-94 is running as p17 on `_rbc`.

## B6 — a joint driver fit under the emergent-wave constraint

**Purpose.** Amendment 1 moved mean `a_max` alone. At k = 1 the mean driver is
string-stable at capacity: its unstable band starts at 39.8 veh/km, against a
capacity density of 29.2 and against 28.2 at k = 0 (DISCHARGE_CALIBRATION §4).
Every k = 1 I-24 arm lost the wave row (step 3, p13, p14). Amendment 2's smaller
shifts kept the waves but were disqualified by the fitter's backlog. CLAUDE.md
§3.1 requires instability near capacity.

**Inputs.**
- `artifacts/idm_i24.json`: its ranges are T 0.99–2.03 s and `a_max` 0.63–1.48
  m/s² (§7.2; CHANGELOG 2026-10-07, lineage).
- `artifacts/idm_i24_capacity.json`, `validation.string_stability`,
  `scripts/derive_population.py`, `scripts/calibrate_driver_grid.py`.
- The capacity target of 1,775 veh/h/lane (I24_CAPACITY §4).
- B5's fitter and Phase A's I-24 arm.

**Design.** Proposed Amendment 7 moves two knobs together. It needs the owner's
reading of §7.2's "single corridor-wide adjustment" (I94_RESIDUALS §7).
1. *Grid:* k ∈ {0, 0.25, 0.5, 0.75, 1} × mean T = 1.322 + j × 0.130 s, j ∈ {−2…2}.
   0.130 s is 0.25 sd of T, the k grid's step [computed from the covariance];
   **new**. That gives 25 derived populations.
2. *Screen S1 (closed form, local, $0):* the mean driver is unstable at its own
   capacity density, i.e. the `unstable_band` lower edge is at or below the density
   of maximum equilibrium flow (DISCHARGE_CALIBRATION §4's measure).
3. *Screen S2 (cloud):* straight-road capacity ≥ 1,775 veh/h/lane on the
   capacity fixture (4 lanes, 2,400 veh/h/lane, 30 min, 2 seeds).
4. *Grid run* on the pairs passing S1 and S2: the I-24 arm at its carried demand,
   3 seeds, selected by Amendment 1's rule. Lane-share RMSE must be within 1 pp of
   the minimum; then the smallest peak-section discharge error wins, with ties to
   the smaller change.
5. *Stage 3:* the chosen pair gets its own B5 refit and a 20-seed battery, read by
   Amendment 2's rule against Phase A's arm. Required: the stack wave criterion
   (14–22 km/h) passes; GEH < 5 share and RMSPE are no worse; realised demand ≥
   reference − 0.01; zero collisions; backward fronts no more than a third below the
   reference (A4). At most two pairs reach stage 3, the second in its own launch.

Stage `p18_i24_b6`. Readout: `artifacts/driver_joint_i24.json`, the battery, and
DISCHARGE_CALIBRATION §6.

**Cost.** About 160 + 12 min, **$2.2** (cap 300 = $3.9). A second pair costs
**$1.1** (cap 150 = $2.0).

**Depends.** B5 and Phase A's arm. I-94 is out of scope: the stability criterion
is for IDM, its fleet is EIDM, and its C4 reads the queue tail (I94_RESIDUALS §4).

**Frisco.** Fixes, before Frisco's data, which knobs §7.2 may move and under what
constraint.

**Stop.** If S1 admits no pair with k > 0: then no `a_max` gain is compatible with
§3.1 at these T. A lower T raises pre-breakdown capacity (Amendment 1's objection),
and S2 is one-sided; the report says so.

## C7 — why I-24's link-flow GEH share is 31 %

**Purpose.** After B2 the peak sections pass on 2-h flows (GEH 3.86 / 4.63), yet
the battery's GEH row is 30.6 % against 85 % (§8.4.4). C7 finds which bins fail
and why.

**Inputs.** The committed batteries (`i24_validation_dc_refit_rc.json`,
`…_p13ref.json`, `…_p14_*.json`, and every later one),
`i24_validation_observed.json`, `i24_count_consistency.json`, `i24_coverage.json`
and `scripts/i24_build_replica.py`. The row scores 144 bins (6 sections × 24
five-min windows, ×12 to hourly), not station-hours (`geh.bins`).

**Design.** `scripts/i24_geh_diagnosis.py` → `artifacts/i24_geh_diagnosis.json`.
It reads JSON only, runs locally and costs $0. Each failing bin goes to the first
class it meets:
1. *Recording noise:* the observed bin is at GEH ≥ 5 from its own centred 15-min
   mean (the analogue of I24_VALIDATION §0.5(a)).
2. *Level:* its section's 2-h flow is at GEH ≥ 5 (R4's measure), with the bin's
   error of the same sign.
3. *Timing:* a simulated bin within ±15 min (protocol §5.2) is within GEH 5.
4. *Shape:* everything else.

Also reported:
- the share in C1's station-hour form;
- each section's cross-correlation lag;
- whether the inflow is stamped at the count section (data x = 200 m) while
  vehicles enter upstream with no travel-time shift.

**Rule, fixed now.**
- If *timing* is the largest class, C8 runs.
- If *level* is largest, the work goes back to B5/B6.
- If *recording noise* is largest, an amendment scoring I-24 on station-hours is
  proposed and reported both ways.
- An insertion offset, if found, is corrected by a computed (not fitted) shift,
  proposed like B2, before C8.

**Cost.** $0. Repeated on every new I-24 battery.

**Frisco.** Whether correct 2-h flows can pass C1's hourly form, Frisco's scoring.
**Risk.** Classes overlap; the order decides.

## C8 — per-window demand timing (only if C7 says timing)

**Purpose.** Fit the demand's time profile, not only its level.

**Inputs.** The arm after B6 (else after B5), `calibration.demand.fit_multipliers`,
and the coverage artifact's per-window estimators.

**Design.** Eight multipliers, one per 15-min window of 06:30–08:30, applied to
mainline and on-ramp inflows; exit fractions and the boundary are unchanged.
Compass search with at most three rounds and five seeds per evaluation; objective
and constraint as in B5, over the whole period. Bounds (proposed Amendment 8,
**new**: §7.1's "count uncertainty" has no I-24 artifact): each multiplier stays
within the spread of that window's coverage estimators relative to the recommended
one. `scripts/i24_fit_demand_timing.py`, stage `p19_i24_c8`.

**Criteria.** §8.4.5 C1–C5 against the from-arm on the calibration day. The fit
consumes the whole day, so the C9 validation days (re-scored, $0) are its only
holdout.

**Cost.** About 250 + 12 min, **$3.4** (cap 420 = $5.5).

**Depends.** C7's rule, B5, B6 and C9.

**Frisco.** Shows whether Frisco's §7.1 fit needs a time profile.

**Stop.** If the validation-day GEH share falls below the from-arm's, the readout
says "overfit" and nothing is proposed.

## C9 — two more I-24 MOTION days and the day split (item 15)

**Purpose.** I-24 has one morning and no holdout (Amendment 1, §8.4.5).

**Inputs.** External: two INCEPTION westbound weekday mornings from i24motion.org.
They are free under the owner's registration, governed by the I-24 MOTION data
agreement, and about 5.8 GB per zip (I24_DATA §1). Committed:
`scripts/i24_extract.py` (`--source`, `--t-origin`, `--name`),
`scripts/i24_data.py`, `scripts/i24_coverage.py`, `scripts/i24_validate.py`,
`scripts/data_quality_report.py`, `scripts/station_selection.py` and
`scripts/day_split.py`.

**Design.**
- *Screen (§3.1):*
  - Tuesday–Thursday (30 Nov 2022 was a Wednesday [computed]);
  - no federal holiday (Thanksgiving, 24 Nov 2022, is out);
  - no incident or weather event (TDOT logs).

  Proposed reading: a day with the CIRCLES test fleet on the road (CLAUDE.md §13)
  is an "event affecting the stretch".
- *Build:* `scripts/i24_data.py` takes a day directory and its t-origin (today it
  is hard-wired to 30 Nov). A new `scripts/i24_virtual_detectors.py` writes each
  day's 5-min flow (recommended coverage), crossing speed and time occupancy at the
  six sections as a generic detector CSV. Data quality, station selection and the
  split then run unchanged.
- *Split:* three candidates give one calibration day and two validation days. That
  is below §3.3's 5 / 3 on both sides, so it is stated as underpowered. As written,
  the seeded draw can send 30 Nov to validation although every I-24 calibration
  used it. Proposed Amendment 9, written before the new files are read: a day
  already used to calibrate is pinned to calibration. Meeting 5 / 3 with the pin
  would need 9 candidate days.
- *Validation:* re-score the adopted arm's battery against each validation day and
  their mean (`--criteria-only` on the per-replicate counts and speeds, with the
  model unchanged, §3.5), at $0.

Stage `p20_i24_days` (extraction, coverage, observed sides, detectors, quality,
split; no simulation).

**Criteria.** Gate C1, C3, C4 and C5 (§6) on the validation days, reported only.

**Cost.** About 40 + 12 min, **$0.7** (cap 120 = $1.6), plus bucket storage
(cents).

**Depends.** The owner's download; nothing from Phase A.

**Frisco.** The first I-24 holdout, and a rehearsal of §3.

**Stop.** A day that fails the screen, or has coverage holes at the count sections,
is replaced before the draw.

## D10 — I-94 ramp rules (b) and (c) as pre-registered rounds

**Purpose.** Two of fix 1's rules need amendments (I94_CALIBRATION_DAYS §3):
- (b) T.H.61 NB is taken from the mainline difference. Its residual has the same
  sign on every calibration day, and its 4-h mean, −317 veh/h, is outside the
  quadrature band of ±261.
- (c) S792 is left out of the demand balance but still scored.

With both, every planned station flow is within about 125 veh/h of its count (§4
there).

**Inputs.** `scripts/i94_calibration_days.py`; `calibrate_scenario`'s
`ignore_ramp_detectors` / `skip_stations` (off by default); the calibration-day
observations; Phase A's base (expected `mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2`
and its battery `artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2.json`).

**Design.** Drafts 1 and 2 (§3 there) are adopted as amendment text before the
run. Scenarios `…_rb` ((b)) and `…_rbc` ((b)+(c)) are written from the base and
checked with `--check`. Stage `p16_i94_d10` runs:
1. a one-seed reproduction of the reference (else a full re-run of it);
2. each arm's 20-seed battery, baseline gate and gated report.

**Criteria** (each arm against the reference, same seeds):
- zero collisions and no lock;
- realised demand ≥ reference − 0.01;
- gate C1 on the calibration days not below the reference's;
- C3 (15-min) ≤ reference + 0.02.

C4, C6 and the validation-day rows are reported, with and without both rules (the
drafts' requirement). B5's I-94 arm, by a rule fixed now: `_rbc` if it holds, else
`_rb`, else the reference.

**Cost.** About 158 + 12 min, **$2.2** (cap 300 = $3.9).

**Depends.** Phase A's W1b/W2. Without W2 the base collided (p8), so C5 could not
hold.

**Frisco.** Sets the general rule for a ramp detector whose segment does not close
(§2.3), on every corridor.

**Risk.** The T.H.52 weave stays the binding defect: honest inputs dropped C1 to
34 % in p8.

**Status — 2026-10-08: run** (p23, code 8ea59ec; docs/I24_CONSISTENCY_C7B.md §11). The B2 re-run reproduced p13. `rcs` holds R1–R5 and `rcc`/`rccs` fail R2 and R4; the rule selects `rcs` (lane-set station-hour share 54.58 % against 53.75 %). About $1.7 [estimate]. Nothing adopted.

**Status — 2026-10-08: run** (p16, code 3856257; Amendment 4 defaults, the reproduction re-run of p10's arm B pending; docs/I94_D10_RESULT.md). `_rbc` holds D1–D4; `_rb` fails D4 (C3 paired +0.090 [0.056, 0.124]). B5's I-94 arm is `_rbc`; rules 1 and 2 hold together (FRISCO_PROTOCOL Amendment 10, "Run"; adoption is the owner's). The gate still fails in every arm.

## E11 — merge gaps and speeds on a never-calibrated site (item 18)

**Purpose.** The merge quantities come from I-24 (critical gaps, partner speeds)
and US-101 (relaxation) (MERGE_MODEL §2). E11 tests the kept configuration and
`merge: measured` on a third site.

**Inputs.** External: NGSIM I-80, data.transportation.gov `8ect-6jqj`,
`location='i-80'`. It is public, needs no registration, and
`calibration.data_fetch.fetch_ngsim_i80` exists. highD or exiD (levelXdata) would
be used only if the owner requests access. Committed: `calibration.loaders.ngsim`,
`calibration.lane_change_gaps`, `calibration.critical_gap`,
`calibration.lane_change_relaxation`, and the US-101 recipe
(`scripts/us101_data.py`: duplicates, period split; M2_RESULTS §1).

**Design.**
- `scripts/i80_data.py` fetches on the VM, de-duplicates and splits periods.
- `scripts/i80_merge_measures.py` measures the on-ramp entering changes: accepted
  lead/lag time gaps, Troutbeck critical gaps with 200-resample bootstrap CIs,
  partner speeds, gap ratios at 0 / 2 / 5 / 10 s, and acceleration-lane speed by
  50-m bin.
- `scenarios/i80_replica.yaml` is built as the US-101 replica (geometry, counted
  demand, measured downstream boundary), with the I-24 population. Nothing is
  fitted on I-80.
- 20 seeds each under the kept configuration and under `measured`, with the same
  extractors on the simulated trajectories.

Stage `p21_i80_merge`.

**Criteria** (fixed now):
- E1 (MERGE_MODEL §3(b)): the partner-speed median signs match I-80's.
- E2 (§3(c)): the new follower is at ≤ 0.9 and the leader side at ≤ 0.8 of a
  normal gap at the change, recovering by 10 s.
- E3 (**new**, proposed Amendment M1 to MERGE_MODEL §3, which has no rule for an
  unparameterised model): for the accepted-gap, critical-gap and partner-speed
  medians, the model's 20-seed 95 % interval overlaps I-80's bootstrap 95 %
  interval.
- E4: zero collisions.

The speed profile is reported. *Rescue:* `measured` is rescued only if it meets
E1–E4 and the kept configuration fails at least one.

**Cost.** About 45 + 12 min, **$0.7** (cap 120 = $1.6).

**Depends.** Phase A's guards, for the kept configuration.

**Frisco.** Evidence that merges transfer to unfitted ramps, as Frisco's will be.

**Risks.** Raw NGSIM is noisy (reconstructed NGSIM is out of scope by owner
rule); gaps and speeds rest on positions. n is reported with every interval.

**Status 2026-10-08.** E11 ran (stage p21, 04:23–04:31 UTC, self-deleting VM, code 8ea59ec; docs/I80_MERGE_VALIDATION.md): kept fails E2 and E3 (0 of 6 overlaps), measured fails E2 and E3 (3 of 6), zero collisions in both; "measured is not rescued". E13: by the pre-registered rule `merge: measured` is retired; the deletion (which must now also cover scenarios/i80_replica_measured.yaml) awaits the owner's ask-first. The kept configuration is not validated on I-80 either, and VALIDATION_REPORT must say so; the observed in-zone count (113 changes against 421 ramp entries) needs a check before E11 is cited.

## E12 — the platform-sensitive tests (item 21)

**List.** `pytest -m "not slow" -q -rxX` on the 15 marked tests, macOS, 2026-10-07:
2 passed, 13 xfailed, 2 xpassed, in 44 s.
- **Strict xfail (9):** `test_short_section_with_the_corridor_fleet[exit_peak-3]`,
  `[exit_peak-4]`; `test_weave_configuration_carries_the_stretch` (th61);
  `test_th52_weave_at_capacity_flows`;
  `test_th52_with_upstream_entrance_at_corridor_demand`;
  `test_th52_with_upstream_entrance_on_the_corridor_fleet`;
  `test_th52_corridor_section_carries_free_flow_demand`;
  `test_th52_capacity_does_not_lock[5]` (measured);
  `test_th52_corridor_section_carries_free_flow_demand_measured`.
- **Non-strict xfail (4):** `…corridor_fleet[entrance_peak-3/4/5]`;
  `test_th52_weave_at_corridor_demand_exit_side`.
- **Xpass on macOS, failing on Linux CI per their reasons (2):**
  `…corridor_fleet[exit_peak-5]` (4 against 7 of 274 exits given up);
  `test_th52_weave_at_capacity_does_not_lock[4-0]`.

**Purpose.** Identical outcomes on macOS and CI (Linux), with no non-strict mark.

**Design.**
1. A `workflow_dispatch` CI job (free on the public repo) runs the 15 with `-rxX`
   and an opt-in recorder writing each test's `state` numbers; likewise locally.
2. For the two xpasses, a per-step divergence probe takes the sha256 of (id, lane,
   x, v) at every step on both platforms. It locates the first differing step and
   whether a SUMO-returned value or a runner command (e.g. a numpy reduction whose
   order depends on SIMD width) differs first.
3. If the cause is in Python, fix the arithmetic order so the trajectories become
   identical. If it is in SUMO, it cannot be made identical in-process, and the
   owner picks:
   - (a) Linux as reference: macOS runs these fixtures in the repo's Docker image
     (colima), with strict marks on the Linux outcome;
   - (b) per-platform strict marks (`condition=`), which are deterministic but not
     identical.

**Criteria.** Three consecutive runs per platform give identical outcomes for all
15, and every mark is strict. No assertion or threshold changes (owner rule:
never skip-mark a failing physics test). Goldens and hashes stay unchanged, and the
reasons carry both platforms' numbers.

**Cost.** $0. **Frisco.** A laptop pass means a CI pass. **Risk.** Option (a)
needs colima on the 16 GB laptop with nothing else running.

**Status 2026-10-08.** E12 is complete. Steps 1–2 (3856257) and step 3 (466193f): the 19 platform-sensitive tests, every mark strict, per platform where the outcome depends on it (option (b): on every probed fixture SUMO 1.27.1's own state diverged first between arm64 macOS and x86_64 Linux, the runner's commands still identical; docs/E12_PLATFORM_TESTS.md §7). Confirmed on CI run 37733321865 at 466193f: macOS 8 passed / 11 xfailed and Linux 5 passed / 14 xfailed, identical in three consecutive runs on each runner, no xpass, no non-strict mark; the laptop matches the macOS runner at every step. The criteria (identical outcomes in three runs per platform; every mark strict; no assertion, threshold, golden or hash changed) hold. Finding: fixture-built configs hash by checkout path (absolute OSM paths); committed scenarios are unaffected.

## E13 — one merge model, a validation report, a concise paper (item 23)

**Status — 2026-10-08: p22 run** (code f0551e7; `artifacts/p22_reports.json`; docs/reports/e13_*/). Five regenerated reports, each figure replicate reproducing its battery's seed. E13's remaining items: VALIDATION_REPORT.md and PAPER_SHORT.md are drafts (ef28156) that must be updated with p23, p15, p16, p17, p24 and p25's results; the deletion of `merge: measured` (four scenarios) awaits the owner.

**Design.**
- **Merge model.** Retire `merge: measured` unless E11 rescues it.
  - Delete `microsim.merge_model`, its runner paths, its tests and scenarios
    (`i24_replica_flow_speedcal_measured`, `mndot_i94_wb_stpaul_weave_measured`,
    `…_slice_measured`) and its goldens (`merge_measured`, `merge_measured_accel`).
    The value is then refused by name, as A4 did.
  - Keep `artifacts/merge_model_params.json`; measured results reproduce from
    2.6.0.
  - "One model" (§7.4, §9.6) is read as one locked configuration for every
    corridor: LC2013 at acceleration lanes, the runner's weave/scripted rules where
    committed scenarios use them, and Phase A's guards plus `force_guard`. Deleting
    weave or scripted would move every I-94 hash and is not proposed. The owner
    confirms this reading and the deletions (ask-first).
- **Validation report.** `docs/VALIDATION_REPORT.md`, at most 4 pages: the gate
  tables on calibration and validation days, the ring benchmark, E11 and the
  limitations, with every value from a named artifact (CLAUDE.md §7.4).
  Auto-reports are regenerated on the cloud (`p22_reports`), because the figures
  need trajectories.
- **Paper.** `docs/PAPER_SHORT.md`, at most 8 pages, from PAPER_DRAFT.md.
  Publishing it is the owner's decision.

**Criteria.**
- Every other committed scenario hashes as before.
- The goldens are unchanged except the two measured ones, removed with a PR note
  (CLAUDE.md §9).
- The not-slow suite, ruff and mypy are clean.
- Every number in the report traces to an artifact.

**Cost.** About 20 + 12 min, **$0.4** (cap 60 = $0.8).

**Depends.** E11, Phase A, and every earlier step.

**Frisco.** §7.4 needs merging locked before Frisco's data arrive.

---

## Schedule

East = us-east1, Central = us-central1. Each slot starts once the previous slot's
results have been read.

| slot | East | Central | local ($0) | expected | running | caps, running |
|---|---|---|---|---|---|---|
| 0 | — | — | C7 on B2; E12 steps 1–2; B5 build; B6 S1; D10 scenarios; E11/C9 scripts | $0 | $0 | $0 |
| 1 | p15 B5 I-24 (needs Phase A arm) | p20+p21 C9 + E11 (needs owner upload) | — | $1.2 + $1.3 | $2.5 | $4.6 |
| 2 | p18 B6 | p16 D10 (needs Phase A guards) | C7 on p15 | $2.2 + $2.2 | $6.9 | $12.4 |
| 3 | p18b B6 second pair, if needed | p17 B5 I-94 | C9 re-score | $1.1 + $2.2 | $10.2 | $18.3 |
| 4 | p19 C8, if C7 says timing | — | C7 on B6; E12 fix | $3.4 | $13.6 | $23.8 |
| 5 | p22 reports | — | E13 | $0.4 | $14.0 | $24.6 |
| — | refused-launch reserve (p13 lost about $0.75 to three refused launches) | | | $1.5 | **$15.5** | **$26.1** |

Expected about **$15.5**; every cap reached, about **$26**; about 14 VM-hours.

## What the owner must decide or provide

1. **Phase A.** Adopt B2 or not (this sets the arm for B5, B6 and C7–C9); W1b/W2
   (the base for D10 and B5's I-94 fit); Amendment 3; lock recording on the I-24
   batteries.
2. **Amendments, before their runs.**
   - 6 (B5);
   - 7 (B6, two knobs together);
   - 8 (C8 bounds);
   - 9 (C9 pin and CIRCLES days);
   - D10 drafts 1 and 2;
   - M1 (E11);
   - E12 (a) or (b);
   - E13's reading of "one merge model", and the deletions.
3. **Provide.**
   - Two I-24 MOTION mornings (free under the registration), uploaded to the
     bucket.
   - Optionally, a highD or exiD access request.
   - Nothing from Frisco.
4. **Approve** each launch and bucket within $30, and decide whether to publish the
   report and the paper.

## Coordinator's decisions on this plan — 2026-10-07, 21:16 CDT

The owner delegated these decisions ("do all of them A → E … you pick, be smart"); they are
mine, taken on the plan above before any run, and the owner can overturn any of them.

- **Amendment 6 (B5)** approved: the GEH-share objective with the insertion constraint and
  five seeds per scale (the one-seed noise is measured: 1.4 points per bin, 0.009 inserted).
- **Amendment 7 (B6)** approved: `a_max` and T moved together on the stated grid; "single
  corridor-wide adjustment" (§7.2) is read as one population moved as a whole, which two knobs
  of the same population satisfy.
- **Amendment 8 (C8)** approved conditionally — only if C7's rule selects timing.
- **Amendment 9 (C9)** approved: a day already used to calibrate is pinned to calibration;
  CIRCLES test-fleet days are events. C9 waits on the owner's download of two INCEPTION
  westbound weekday mornings (the data agreement is under the owner's registration).
- **D10 drafts 1 and 2** approved as amendment text, written before the run.
- **M1 (E11)** approved: interval overlap for an unparameterised model.
- **E12 (a)/(b)** decided after the divergence probe: (b) per-platform strict marks if the
  divergence is SUMO's; (a) only if the cause is in Python and colima is not needed.
- **E13** reading approved (one locked configuration: LC2013, the committed weave/scripted
  rules, the adopted guards); deleting `merge: measured` only if E11 does not rescue it.
- **Added, from A4:** a report-only *breakdown* reading, pre-registered now: a replicate that
  realises less than 0.9 of its planned demand while the battery's mean realised share is at
  least 0.95 is a breakdown (the p14 B1 + B2 seed realised 0.660; the B2 arm's lowest replicate
  0.960; Amendment 2's floor 0.977). Reported per replicate beside `no_locks`; gating only by a
  later amendment.
- **Order and budget** as scheduled; a VM is launched only from a pushed commit with its
  pre-registration committed; ledger kept under $35, flagged before $50.

## Schedule change — C7b inserted before B5's I-24 fit

C7 ran on 2026-10-07 (docs/I24_GEH_DIAGNOSIS.md): recording noise is the largest class under
every order, the station-hour form is 64.6 % (still failing), and two consistency defects of
ours were found — the simulated count reads five lanes at 1,000 and 4,800 m where the recording
reads four, and the demand was built at the equilibrium coverage against a recommended-coverage
target (planned/target 1.00–1.15 over the period) — plus a 75.7 s insertion-time stamp offset.
**C7b** (docs/I24_CONSISTENCY_C7B.md, stage `p23_c7b`, ≈ $1.6) pre-registers the scorer's lane-set
correction, the coverage-consistent demand and the computed insertion shift as B2-style input
corrections with R1–R5, and runs before `p15`: B5 refits the level on the arm C7b's fixed rule
selects, so that a level is not fitted against an inconsistent target. The running total rises
by about $1.6 (expected ≈ $17; caps ≈ $28).
