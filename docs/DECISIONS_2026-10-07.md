# Decisions — 2026-10-07

*The owner delegated these three decisions; the coordinator took them on 2026-10-07 at 21:20 CDT, on the memos'
evidence, and each is recorded in the dated **Decision** paragraph under its memo's heading; the memos are
otherwise unchanged. The protocol text is in docs/FRISCO_PROTOCOL.md: "Adoption of Amendment 3", Amendment 4
(W1b and W2) and Amendment 5 (B2).*

## A2 — Adopt W1b and W2?

**Decision — 2026-10-07, 21:20 CDT.** Adopted: W1b and W2 together, on by default at every weaving section, never
W2 alone (docs/FRISCO_PROTOCOL.md Amendment 4, based on the draft in §5 below). Decided by the coordinator under the
owner's delegation; the evidence of this memo is the basis. The amendment states that it was written after CW5b's
failure was seen, and p10's verdict ("W2 not adopted on this round") stands on record. W1b's release share per
weave is reported beside `no_locks`; a share above 1 % is listed in the limitations, not a gate failure. The rules
may be switched off only to reproduce published results; every published I-94 result except p9's and p10's own arms
ran with both off and stays reported beside. The default flips in code only after (i) a reproduction re-run of
p10's arm B on the current code (the veto-capture fix landed after p10; the capture cannot occur on I-94's
geometry), for which the proportional arm of the A3 round on the W1b + W2 family serves, and (ii) `validation.battery`
and the report count W1b releases.

*Abbreviations: `W1B` = `artifacts/weave_w1b_corridor.json`, `W2C` = `artifacts/weave_w2_corridor.json`, `G` = `artifacts/weave_collision_guards_2026-10-07/`.*

**Recommendation:** adopt both together, at every weave, by a dated amendment (§5).

### 1. What each amendment does

- **W1b** (`entrant_giveup_m` 5, `entrant_giveup_dwell_s` 60; docs/WEAVE_LOSS_DIAGNOSIS.md §10.2). An entrant owing its change into lane 1 that has stood below 0.1 m/s within 5 m of the auxiliary lane's end for 60 s unbroken, with no acceptable change, takes the paired exit. Defect: the gore lock (a through entrant at rest 0.05–0.08 m from the lane's end, an exiter at lane 1's front, nothing passing the gore again; 3 of 20 step-3 replicates).
- **W2** (docs/I94_CAL_COLLISIONS.md §13.2). `weave_handback` withholds a one-step weave speed target when the vehicle's own model must brake harder than `decel`, SUMO 1.27.1's cap under a TraCI target (WP-95). `weave_close_leader` reads a leader inside `minGap` as a leader, not a free road (WP-96). `weave_resolve_opposing` resolves same-step entries into one lane from opposite sides (`merge_model.resolve_opposing`); the loser waits a step. Defects: the Ruth St rear-ends (p8c: R3 pinned at b; R1, R2 braked beyond b, late) and T.H.52's opposing entries T1, T2; T3, a cut-in from the right, is outside W2's design (`artifacts/i94_cal_collisions_trace.json`, `events[*].verdict`).

### 2. Evidence

| round (artifact) | arms, runs | collisions | locks | paired flow; departed share | W1b releases (cap ≤ 1 %) | verdict as registered |
|---|---|---|---|---|---|---|
| W1b fixtures (`artifacts/weave_loss_2026-10-07/w1b/criteria.json`) | ref vs W1b, 132 each | 0 / 0 | 1 → 0 (`ruth_exit_fleet_271` s15) | S1 +0.0 veh/h | Ruth St 0.09–0.14 %, others 0 | all seven pass |
| p9, `_dc` (`W1B`) | ref vs W1b, 20 seeds | 0 / 0 | **3 → 0** (`criteria.C4b`) | S790 +7.2 [−9.6, +23.9] veh/h; +0.7 pp [−0.25, +1.66] | Ruth St 0.54 %, T.H.52 0.005 % (`criteria.C5b.pools`) | C1–C5b pass |
| W2 fixtures (`G/criteria.json`) | ref vs W2, 132 operating + 80 stress | 0 → 0 operating; **4 → 0** stress (1 R, 3 T) | 1 new lock (`th52_upstream_fleet` s3); 3 new speed flags (X-R2, 1 m/s boundary) | S1 +21.8 [−19.8, +63.5] veh/h | — | G6 fails; G0–G5, G7 pass |
| p8, `_dc_cal` (`artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal.json`); context, other tree, unpaired | neither rule, 20 seeds | 2 (Ruth St) | not scored; lowest realised demand 0.597 (A's: 0.982) | — | — | — |
| p10, `_dc_cal` (`W2C`) | A = W1b vs B = W1b + W2, 20 seeds | A 1 (T.H.52), B 0 | 0 / 0 (`criteria.CW4`) | S790 −13.5 [−32.8, +5.8] veh/h; +0.01 pp [−0.04, +0.07] | Ruth St 1.26 % in **both** arms (257 of 20,340) | CW1–CW4, CW5a pass; **CW5b fails** |

- **p10's collisions.** A's one is at T.H.52 (`criteria.CW3.by_section_a`); with none at Ruth St, R had nothing to remove, and the pre-registered power note (I94_CAL_COLLISIONS §13.6) says the pair tests T only for cost. One in 20 against none (Fisher p = 1.0 [computed]) cannot show W2 caused the drop; W2's evidence is G2 (four reproducing fixture runs, clean under W2) and the command recorder.
- **The releases are W1b's.** Pooled totals match; per-seed counts differ in 18 of 20 (`per_seed[*].{a,b}.weaves`): W2 changes which entrants are released, not how many.
- **G6.** W2 alone ends `th52_upstream_fleet` seed 3 in the gore lock (entrant at lane 0's front from 727 s, exiter at lane 1's; 1,462 of 2,094 departed against 1,635). No single switch does it; together they steer that run into the state W1b releases, and W1b + W2 does not lock (`G/lockprobe_w1bw2_post.json`).
- **Veto capture** (third regression review, after p10): a vehicle vetoed by one section could be captured in the same step by another's hold, losing its model lane changing for good. Fixed (`_lc_mode_owned`). p10's arm B ran before the fix, but capture needs a second hold in reach: I-94's gores are 5.4 km apart, beyond the 500-m vacate window, with no measured zone and the scripted merges upstream. p10 holds for the fixed code by inference, not re-run.

### 3. Risks

- **W2 is a model fix.** Each switch removes a command-layer artefact (a braking cap, a misread gap sign, two same-step changes executed in order), not a driver behaviour. Against: B suspended undriven vehicles' own lane changing for a step 22,919 times, intent unrecorded (`w2_counters_b`, Ruth St/T.H.52: vetoes 6,908/16,011 of 6,985/16,767 deferrals; hand-back skips 3,546/2,880; close-leader withholds 427/101); given-up exits lean up (Ruth St 540 → 599, T.H.52 954 → 990, inside CW5a's bounds 608/1,043; fixture G4a with 0.004 per run to spare); and the netfix pair (p8's T collisions) never ran.
- **W1b is a guard.** For: the state it releases, minutes of standstill at the gore, has no field counterpart. Against: the deadlock's cause and T.H.52's 465 veh/h shortfall remain, and neither 60 s nor 5 m is measured, nor whether real drivers take the exit after a minute (WEAVE_LOSS_DIAGNOSIS §10.12). The dwell separates T.H.52's ordinary stands (≤ 50.5 s) from locks (≥ 13.6 min), but Ruth St's reach 61.5–68.5 s, so it also ends ordinary waits (5 of 6 first fixture releases). The share counts interventions, not realism, and grows with queued time at Ruth St (06:30 to the run's end on `_dc_cal`; there 12.9 releases per run add ~3 veh/h to the C-D split's ~390 [computed]). With W1b on, `no_locks` passes; only this count still shows the deadlock.
- **Waves and throughput.** No resolvable effect; wave speed 4.9 → 5.0 km/h (p9), 5.7 → 5.7 (p10), outside 14–22 km/h in all arms. Neither rule seeds a disturbance (§0.2): both react to state at the gore, and W2's withdrawals hand vehicles back to their own model.
- **Published results.** Every published I-94 result ran with both off (phase-1 rehearsal, step 3's `_dc` gate C1 61.8 %, driver grid, netfix probe, p8's `_dc_cal*` batteries, strategy rehearsals); only p9's `_dc_w1b` and p10's arms had them. Adoption rewrites none; step 3's gate has no W1b counterpart (p9 scored nine-day targets only: GEH share 52.3 → 56.1 %).

### 4. Alternatives

- **W1b only.** Arm A collided, failing the gate's C5 (`artifacts/baseline_gate_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b.json`); the weave keeps both fixture-proven collision mechanisms (CLAUDE.md §3.3).
- **Both, as registered.** Unavailable: p10's rule keeps W2 off if any clause fails.
- **Both, release share reported as a standing cost.** A dated amendment admitting it follows the 1.26 %. Allowed: it turns a gate into a disclosure beside the original verdict and makes no failed clause pass.
- **Both opt-in, strategies run with and without.** Doubles every battery and sweep (a pair cost $1.3–1.4 in p9/p10), and the "without" arms of p8, p9 and p10 each failed `no_collisions` or `no_locks`, so none is deliverable (§6, §9.4–9.5).
- **Re-read CW5b against arm A** ("B ≤ A"). Swaps a fixed bound for a relative one after the result, which B passes only because A fails identically: re-thresholding, forbidden by CLAUDE.md §0 and the protocol's change control.

### 5. Recommendation

Adopt both under the third alternative. Families: `_dc_cal` (the honest I-94 inputs), `_dc`, and every later corridor, because §7.4 fixes merge rules model-wide; the change moves config hashes, as WP-98's did. First re-run p10's arm B on the fixed tree (about $0.7 [estimate]) to confirm it reproduces `W2C` per seed, and make `validation.battery` and the report aggregate `n_entrant_took_exit`.

**Amendment text (docs/FRISCO_PROTOCOL.md):**

> ### Amendment 4 — 2026-10-07: weave rules W1b and W2 are part of the model
> Written after the p9 and p10 rounds were read, including p10's failed clause CW5b (Ruth St releases 1.26 % against ≤ 1 %); p10's verdict, "W2 not adopted on this round", stays on record. (1) Every weaving section runs with `entrant_giveup_m` 5, `entrant_giveup_dwell_s` 60, and `weave_handback`, `weave_close_leader`, `weave_resolve_opposing` at 1; never tuned per corridor (§7.4), never W2 without W1b. (2) Every battery and report states each weave's W1b releases as a share of its entrance's departures, pooled over seeds, beside `no_locks`, with W2's counters; a share above 1 % is flagged in the limitations, not a gate failure. (3) Any of the five may be turned off only to reproduce a result published before this amendment; on I-94, whose results came first, the batteries with both off (step 3, p8) stay reported beside.

**Frisco report wording:**

> Weaving sections run with two safeguards that are not measured driver behaviour. A car stopped a full minute at the end of a lane that becomes an exit takes the exit: at [weave], [n] of [N] entering cars ([x] %)[, above the 1 % design bound]. Three guards stop the model's own lane-change commands from causing collisions. Both are on in the baseline and every strategy. They keep the simulation running; they do not improve its fit.

## A1 — Adopt B2?

**Decision — 2026-10-07, 21:20 CDT.** Adopted provisionally, as a ramp-count correction extending protocol §2.3
(ramp volumes), not §7.1 (docs/FRISCO_PROTOCOL.md Amendment 5). Decided by the coordinator under the owner's
delegation; the evidence of this memo is the basis. `i24_replica_flow_rc_speedcal_dc_refit` (909b89f298c5) becomes
the I-24 calibrated-arm candidate in place of `_dc_refit` (ada3f406504b); no file is renamed. The Old Hickory part of
the correction is revertible pending the flag audit or external counts. The five follow-ups of §5 are
pre-registered. The arm still fails the full gate and is not validation. The `_dc_refit` numbers in the CHANGELOG
(2.6.0, "Step 3") and docs/PAPER_DRAFT.md were built on uncorrected counts; the note is in
docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.4 and the CHANGELOG, and correcting the paper draft is a follow-up.

**Recommendation:** adopt provisionally (§5).

### 1. What B2 changes, and why it is an input correction

**The defect.** Between 2,200 and 5,400 m the recording's counts do not conserve (−403 [−689, −135] veh/h at pooled coverage). Per-section coverage does not explain it (+294); removing the through traffic counted in ramp lanes does (−191 [−466, +68]). The section targets 6,626 / 6,639 / 6,009 stand (`artifacts/i24_count_consistency.json` `verdict`, `pairs[5]`).

**The rule.** Per 5-min window, before coverage scaling, B2 removes every ramp-lane crossing whose own fragment was in lanes 1–4 before an on-ramp count, or after an off-ramp count (`artifacts/i24_replica_inputs_flow_rc.json` `ramp_through_traffic`). Tracked crossings, 06:30–08:30:

- Old Hickory on: 1,329 → 1,142 (−14.1 %)
- Hickory Hollow off: 740 → 724 (−2.2 %)
- Hickory Hollow on: 862 → 557 (−35.4 %)
- Bell Road off: 406 → 382 (−5.9 %)

They were already in the upstream mainline counts, so the builder inserted them twice. At Hickory Hollow the count lies in the weave lane toward Bell Road, where mainline exiters are expected. The B2 arm (909b89f298c5) differs from `_dc_refit` (ada3f406504b) only in name and ramp values.

**What it leaves in.** A flag reads only the same fragment within its 15-min chunk (60-s lead pad). Fragments are short (median 9.9 s, 117 m; docs/I24_DATA.md §2), so a through vehicle whose mainline samples are on an earlier fragment is missed; chunk edges only drop flags. B2 removes a lower bound.

It keeps the builder's unmeasurable assumption that ramp lanes are tracked like the mainline (lane-5 coverage bounded only to [0.33, 1]; `artifacts/i24_coverage_lane5.json` `estimate.bounds`).

### 2. Evidence

| 20 paired seeds | `_dc_refit` (p13 reference) | B2 | reading |
|---|---|---|---|
| R1 collisions | 0 | 0 | pass |
| R2 realised demand, mean (seed range) | 0.921 (0.912–0.931) | 0.967 (0.960–0.973); paired +0.045 [0.042, 0.049] | pass |
| R3 ramp GEH vs corrected counts (OH on / HH off / HH on / BR off) | 1.1 / 1.5 / **12.0** / 0.9 | 1.8 / 1.2 / 1.3 / 2.0 | pass |
| R4 2-h flow 2,200 / 3,200 m (GEH vs 6,626 / 6,639) | 6,047 / 5,983 (7.28 / 8.25) | 6,316 / 6,267 (3.86 / 4.63); paired +269 [248, 290] / +284 [266, 302] | pass |
| R5 15-min segment-speed RMSPE; wave row | 0.273; fails | 0.254; fails | pass (wave half n/a) |
| 2,200-m flow, seed range | 5,988–6,142 | 6,265–6,381 | [computed] |
| 5,400-m flow (recorded 6,009), mean (range) | 5,735 (5,691–5,768) | 5,772 (5,712–5,812) | [computed] |
| Gate: hourly GEH < 5 share (≥ 85 %) | 25.7 % | 30.6 % | fail |
| Gate: 5-min RMSPE (≤ 15 %) | 0.333 | 0.343 | fail, worse |
| Backward fronts per replicate, standard / stripe | 5.05 / 58.7 | 10.1 / 65.2 | — |

*Sources:* `artifacts/boundary_b2_corridor.json` (`criteria`, `paired_b2_minus_reference`) and `artifacts/i24_validation_dc_refit_{p13ref,rc}.json` (`simulated.demand_realized_fraction`, `.counts_per_replicate`, `.waves*_per_replicate[].n_backward`, `criteria[]`).

**For.**
- R1–R5 hold, and both arms reproduce exactly:
  - the p13 reference matches the committed `_dc_refit` (`reference_reproduces_committed`);
  - p14's B2 re-run matches p13 field for field (`artifacts/boundary_b1b2_corridor.json` `part_i.reference_reproduces_p13.exact`).
- B2 alone is stable (per-seed mean segment speed 8.47–9.01 m/s [computed]). Seed 134183728835869882, which collapsed under B1 + B2, realises 0.971 and 6,358.5 veh/h at 2,200 m under B2 alone.
- Conservation tests the larger, Hickory Hollow correction: the residual moves toward zero without crossing it.

**Against.**
- The gate still fails. R4 is 2-h and peak-only; R3 checks delivery of the model's own inputs, not their truth.
- The Old Hickory correction (about 150 veh/h at pooled coverage) is outside the tested span, so its sign rests on the flag alone.
  - On 200 → 5,400 m the residual is consistent with zero both before (−200 [−650, +289]) and after (+162 [−270, +623]) the correction (`pairs[7]`).
  - On 200 → 2,200 m the correction moves it away from zero at pooled coverage (+204 → +353) and toward zero at section coverage (−676 → −527) [computed: `pairs[0]` + `pairs[1]`].
- A genuine entrant is flagged only if its own fragment shows a lanes-1–4 sample before the count, which takes a lane-assignment error or identity switch (rate unmeasured).
- The canonical `ramps` arms' speed-objective fit set Hickory Hollow on × 1.25, the opposite sign (docs/I24_VALIDATION.md §0); a speed objective does not see insertion.
- The carried s = 0.925 was fitted on contaminated inputs: B2 realises 0.967, below Amendment 2's proposed 0.977 floor, and s = 0.925 inserts 0.9635 on the `_rc` fit seed (§8.4.6).
- One morning, no holdout, the same data corrected and scored: not validation.
- B2 on top of B1 fails its own R5 (0.310 against 0.256), so B2's case rests on the arm without B1.

### 3. Consequences of adopting

**What changes meaning.** `_dc_refit` was never adopted on I-24 (CHANGELOG.md 2.6.0, "Step 3"; Amendment 2's result keeps k = 0). `i24_replica_flow_rc_speedcal_dc_refit` replaces it as the calibrated-arm candidate. Its numbers stay correct but must be labelled as built on uncorrected counts: CHANGELOG step 3, docs/PAPER_DRAFT.md (driver-calibration table, claim 57), §8.4.2, and p12's B1 arm.

**Nothing needs re-running,** because no strategy or sweep result used `_dc_refit`:
- the penetration × compliance battery, cap sweep and controller probe used `i24_replica_speedcal`;
- the VSL/ALINEA sweep used `i24_replica_flow_speedcal_ramps` (docs/I24_STRATEGIES.md).

All rest on the same counts, which becomes a stated limitation. Rebuilding the published `speedcal` record on `_rc` inputs is a separate decision. B2 has run only on k = 1 arms, which fail the wave row; the congested k = 0 arms pass it (docs/I24_VALIDATION.md §0.1, §0.10).

**Protocol.** §7.1 allows "boundary and ramp inflows, by the GEH-driven demand fit (`calibration.demand.fit_inflow`), within the count uncertainty of the data-quality artifact". B2 corrects counts (by up to 35 %), not a fitted level.
- It belongs under §2.3, where a ramp count that fails its segment's mass balance is replaced and reported as an estimate; change control then requires results under both inputs.
- §9.1 tests the same peak flow (≈ 6,600 veh/h, GEH < 5), which `_rc` inputs reach (3.86 / 4.63) without any merge change; merge models must be judged on fixed inputs.

**Frisco report.** Loop data carry no trajectories, so §2.3's mass-balance test is what transfers. With trajectories, the report would read: "Ramp counts at [ramp] included mainline vehicles in the ramp lane; at least [N] veh/h were removed; results are shown both ways."

### 4. Alternatives

- **Do not adopt; carry B2 as a sensitivity.** Keeps a known double count (about 150 and 240 veh/h, §8.4.2) against §2.3, and doubles every later round's arms.
- **Adopt provisionally, pending external counts.** TDOT radar counts for 30 Nov 2022 are not in hand (docs/ROADMAP.md §6, item 7), and their ramp coverage is unknown. A second I-24 MOTION morning would also test the flags.
- **Adopt only after an insertion-aware demand fit (B5).** B5 sets s, not the counts, and runs on `_rc` either way, so it follows adoption rather than gating it. On the recorded grid, `--min-inserted 0.98` picks 0.9; 0.825–0.875 were not run (§8.4.6).

### 5. Recommendation

Adopt B2 provisionally, then run B5, by this amendment to docs/FRISCO_PROTOCOL.md:

> **Amendment 5 — 2026-10-07: ramp counts that carry mainline traffic (adopted provisionally, owner decision).** §2.3 is extended. Where trajectories show that vehicles counted in a ramp lane were in a mainline lane before an on-ramp count, or returned to one after an off-ramp count, those vehicles are subtracted from that ramp's count per 5-minute window, before coverage scaling: corrected = max(counted − flagged, 0). This is a §2 data-quality step, not a §7 calibration change; it changes no target. Reports state that the flags are lower bounds.
>
> On I-24 the step is `scripts/i24_build_replica.py --ramp-through-traffic exclude` with `artifacts/i24_count_consistency.json` (sha256 ea403bcf…). The calibrated-arm candidate becomes `i24_replica_flow_rc_speedcal_dc_refit` (909b89f298c5), replacing `i24_replica_flow_speedcal_dc_refit` (ada3f406504b), whose results stay in the record, reported beside it.
>
> If an independent count or the Old Hickory flag audit contradicts a ramp's correction in sign, that ramp reverts and the arm is re-read against R1–R5. B1 is not adopted, and the published `speedcal` record is unchanged.

**Mark, don't rename.** Names enter the config hash, and the `_rc` headers record their bases' sha256, so leave the files untouched. Record the adoption in the protocol, §8.4.4, docs/I24_VALIDATION.md and the CHANGELOG, and there:
- mark `_dc_refit` and `_dc` as built on uncorrected counts;
- mark `…_rc_speedcal_dc_refit_b1` (fails A5) and `…_dc_refit2` (fails C2 and C3) as not adopted;
- correct docs/PAPER_DRAFT.md, which says neither correction has run.

**Pre-register:**
1. B2 on the k = 0 arm (`flow_speedcal` → `_rc`), read by R1–R5 with the wave half binding.
2. A data-only audit of the Old Hickory flags: the lateral position and duration of each flagged fragment's lanes-1–4 samples, with the rule fixed first.
3. B5 on `_rc`, read by C1–C5.
4. A diagnosis of the hourly GEH shortfall on `_rc`.
5. External ramp counts.

## A3 — Amendment 3 and the crossing-share range round

**Decision — 2026-10-07, 21:20 CDT.** Amendment 3 adopted now, as proposed (docs/FRISCO_PROTOCOL.md, "Adoption of
Amendment 3"). Decided by the coordinator under the owner's delegation; the evidence of this memo is the basis. The
T.H.52 ramp-to-ramp share is an uncertain input. The gate is still judged at the proportional split (u = 0), so no
verdict changes; once the round has run, every I-94 headline is accompanied by the range reading. The three-point
corridor round of §A3.3 (u = 0, 0.5, 1, share 0.70 at u = 1; families `_dc_cal_w1b` and `_dc_cal_w1b_w2`, the
latter the reference since A2 is adopted; step 3's 20 seeds; P-A3's readings and its material / not material /
inconclusive rules; about $3.9) is pre-registered in the protocol, to run after the per-window share setting is
built and tested. No share is ever chosen from the round.

*Nothing was run. **[computed]**: from `artifacts/demand_mndot_i94_wb_stpaul_cal.json`, S790 in
`artifacts/p1_rehearsal_2026-10-04/observations_calibration.json` and p10's batteries. Adoption and launch are the
owner's calls.*

### A3.1 The input, and why it is unmeasured

- **Movement.** US 52 NB (`on-ramp 769818012`) joins I-94 WB; 305 m later some of it leaves at exit 242B (I-35E N /
  US 10 W, `off-ramp 18207598`). The input is s = v_RR / v_ON (docs/TH52_CROSSING_SHARE.md §1).
- **Assumption.** HCM 7.1's proportional split: entrants take the mainline's exit fraction, itself a conservation
  closure (242B has no detector). On the calibration-day inputs s = 0.29 at 05:30–05:50 and 0.18 at 06:30–07:30,
  about 1,900 and 1,955 crossers/h [computed; nine-day 1,919 / 1,962, §2.2].
- **Public evidence: no number.** MnDOT's 2022–2024 study names this weave a key problem; its report is on request
  only (E1). Its shortlisted fixes give US 52 → I-35E N drivers their own lane or bridge (E3); the 2018
  congestion plan classes I-94 at I-35E "ramp to ramp weaving" (E6).
- **Range [assumed].** Per window, P_w to 0.70, the upper 95 % limit of the least-biased exploratory count
  reading 0.58 [0.44, 0.71], itself biased upward (§5).
- **The fixture verdict flips inside it** (§10.3, seeds 3–22): GEH < 5 at 1, 9, 20, 20, 20 of 20 seeds at
  proportional, 0.40, 0.50, 0.60, 0.70; the locked test at 0, 0, 1, 2, 6 (station speed binds); no collision or lock.
- **Why now.** Other routes are closed: anticipation measured 125 m [107, 154] (model: 120 m)
  (docs/MERGE_ANTICIPATION.md); the measured merge model is NO-GO (docs/MERGE_MODEL_READINESS.md). On p10's
  `_dc_cal_w1b` battery S790 carries 3,884 veh/h (3,830–3,946) at 06:30–07:30 against 4,846 observed; GEH < 5 at
  0 of 20 seeds [computed, `artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b.json`].

### A3.2 What Amendment 3 changes

- **Reporting.** Every I-94 headline the T.H.52 section can move is reported at u = 0 (the documented assumption)
  with its u = 1 value beside it. Strategy effects are robust only if their sign holds in ≥ 90 % of §8.5 samples,
  u included.
- **Criteria touched.** C1: S790, S97 and, through the queue, every station upstream (46.7 % of step 3's failing
  calibration-day station-hours were T.H.52-driven, docs/I94_RESIDUALS.md §2.2). C3: the queue (about 79 % of
  one seed's squared speed error, §3.3). C4: the stack detector reads the queue tail, set by
  the discharge (5.7 km/h on p10). C6: the S790→S97 pair. Collisions and locks: two of p8's three T.H.52
  collisions were opposing entries (docs/I94_CAL_COLLISIONS.md §15).
- **Pass/fail.** The gate stays judged at u = 0: a fail stays a fail, a pass at u = 1 never counts, a pass failing
  at u = 1 is "not robust to the share". A T.H.52 shortfall is "at the proportional split", not a
  merge-model finding, while the range moves it.

### A3.3 The range round

**Prerequisite (laptop, $0).** `WeaveSpec.ramp_to_ramp_share` takes one share per run and refuses a window whose exit
volume is below the ramp-to-ramp volume asked: 0.70 fails in eight calibration-day windows, 07:30–08:10 (lowest
v_OFF / v_ON 0.36 at 07:50) [computed]. The round needs Amendment 3's unbuilt per-window form, s_w = P_w + u ·
(0.70 − P_w) clipped to v_OFF,w / v_ON,w, clips counted in `meta.json["ramp_to_ramp_shares"]`, byte-identical unset
(checked as W1b was).

| arm | u | s at 05:30–05:50 / 06:30–07:30 | crossers 06:30–07:30, veh/h | clipped windows | fixture at that share (§10.3): flow vs proportional; GEH < 5 |
|---|---|---|---|---|---|
| u0 | 0 | 0.29 / 0.18 | 1,955 | 0 | —; 1 of 20 |
| u05 | 0.5 | 0.50 / 0.44 | 1,282 | 1 (07:50) | +353 [+301, +404]; 20 of 20 |
| u1 | 1 | 0.70 / 0.70 | 609 | 8 | +458 [+417, +499]; 20 of 20 |

[computed, expected volumes; GEH < 5 needs S790 ≥ about 4,505.]

**Pre-registration P-A3** (fixed before any corridor run varying the share):
1. **Families.** F1 `_dc_cal_w1b` (`0d26de2a5f01`, p10's arm A), always. F2 `_dc_cal_w1b_w2` (`5080d84d4725`, or
   its hash under A2's Amendment 4) if the owner adopts A2: F2 is then the reference, its u0 is A2's arm-B re-run,
   and F1 shows whether the share's effect depends on W2, whose opposing-entry rule acts on the crossings the share
   removes (16,767 T.H.52 deferrals in p10, §16). A2 undecided at launch: F1 alone.
2. **Arms.** u0, u05, u1 on the T.H.52 block only (Ruth St stays proportional, reported unexamined), step 3's
   20 seeds, paired. u0 re-runs the committed scenario; departed shares unequal to p10's are reported.
3. **Readings.** S790 and S97 hourly flows and GEH; gate C1, C3, C4, C6; realised demand; collisions by section;
   locks (`corridor_w1b.py`'s front-row reader beside `validation.locks`); given-up exits and W1b releases per weave;
   crossings (`n_changed_in`, `n_changed_out`); realised share and clipped windows; per-lane hourly flows at S790
   and the gore from each battery's one kept trajectory (seed 6914975401685141156, read on the VM). Contrasts
   against u0: paired t, 19 df.
4. **Material** if at u1 against u0: (M1) S790 06:30–07:30 lower bound ≥ +100 veh/h (10 % of the 962 veh/h
   shortfall; p9/p10 half-widths 17–19), mean realised demand at most 1 pp lower; or (M2) S790's or S97's GEH < 5
   count differs by ≥ 5 of 20 seeds in any hour; or (M3) calibration-day C1 or C3, paired, interval wholly beyond
   ±2 pp; or (M4) a collision or lock in one arm only. **Not material** if none holds and the S790 interval lies
   within ±100 veh/h; otherwise **inconclusive**, and no seeds are added.
5. **Never.** No share is chosen from the round; u05 shows shape only; every result is reported.

**Stage sketch** `p15_i94_a3_share` (opt-in, after p8's block):
```
p15_copy <stem> <suffix> <u>   # as p10_copy: only name and the T.H.52 block's u change
for F in F1 [F2]: p8_one F; p8_one F_a3u05; p8_one F_a3u1
corridor_a3.py ... --out artifacts/th52_share_corridor.json    # imports corridor_w1b.py's readers
```

**Cost.** p10's batteries took 3,084 and 3,093 s on n2d-standard-16, scoring included; its stage billed about $1.4
(`artifacts/weave_collision_guards_2026-10-07/p10_i94_cal_w1b_w2.log.txt`). At `--procs 10`, one family
is about 2 h 55 min billed, **about $2.0** (`--cap-min 240`); both, **about $3.9** (`--cap-min 420`).

### A3.4 Alternatives

- **Wait for MnDOT's report** (the owner is asking). Most direct, but of unknown date and content; a reported share
  enters only by a §7 amendment that still reports both splits, so runs at u0 and at that share are needed
  either way. If it arrives before launch, add that arm (about $0.6).
- **Fixture-only sensitivity** (done) lacks the peak (proportional 0.18), clipping, queue spillback onto S790
  and upstream, four-hour locks and collisions, realised demand and the gate checks.
- **Infer from the corridor's counts.** §4 is exploratory: confounded time-of-day rows, count error biasing the
  share upward, specifications chosen while looking, validation days pooled. A pre-registered version is expected
  to fail its 0.20-width rule (calibration days 0.58 [0.41, 0.78]).

### A3.5 Recommendation

**Adopt Amendment 3 now. Run the round after A2 (F2's u0 is A2's re-run), once the per-window key is built and
identity-checked; do not wait for MnDOT.** Adoption changes no verdict; the range predates every run; §2.3 already
asks for the test. Wording for Amendment 3:

> **Adopted 2026-10-__ by the owner**, before any corridor run that varies the share, as proposed, with: (a) every
> I-94 result the T.H.52 section can move (C1, C3, C4, C6, collisions, locks, S790 and S97 flows) is reported at
> u = 0 with its u = 1 value beside it, labelled "range over the T.H.52 ramp-to-ramp share [proportional, 0.70],
> stated assumption"; (b) the first run under it is pre-registration P-A3 of docs/DECISIONS_2026-10-07.md §A3, whose
> materiality rule decides how the T.H.52 shortfall is described; (c) no share in the range is selected except by
> a §7 amendment resting on route (a), (b) or (c) above.
