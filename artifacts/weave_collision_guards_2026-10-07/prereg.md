## 13. Amendment W2: weave collision guards (pre-registered 2026-10-07 08:35 CDT)

*Written before any W2 code existed and before any W2 or reference run of this round, at HEAD `6670388`. Evidence
used: §4, §5 and §9 above, the committed W1/W1b fixture artifacts (`artifacts/weave_loss_2026-10-07/w1`, `w1b`) and
`artifacts/weave_w1b_corridor.json`. Results are appended below this registration and do not change it. A frozen copy
is `artifacts/weave_collision_guards_2026-10-07/prereg.md`.*

### 13.1 Why W2

- §4.2 and §5.2 name two weaknesses of the weave's command path, each the human-driver counterpart of a defect
  already fixed elsewhere:
  - **R (Ruth St):** a weave speed target caps the vehicle's braking at its comfortable deceleration (WP-95's SUMO
    behaviour), and `_weave_command` reads a leader closer than `minGap` as a free road (WP-96's defect).
  - **T (T.H.52):** two entries into one lane from opposite sides in one step, the rear one a mode-256 weave change
    that lands at any gap (WP-92). The weave's guard was deleted on 2026-10-06; `merge: measured` keeps
    `merge_model.resolve_opposing`, always on.
- W2 puts the fixes of §9 (F-R, F-T) behind three opt-in keys and tests them on fixtures. The cloud confirmation of
  the mechanisms (§10, p8c) is separate work.

### 13.2 The rule

Three keys of `weave_params`, members of `WEAVE_OPTIONAL_KEYS`: no default, `1` on, `0` or unset off, any other
value refused. Unset, nothing of a guard runs and nothing is written. The **W2 setting** is all three at `1` on every
weave block.

1. **`weave_handback`** (F-R.1).
   - **What it does.** In `_weave_step`, before each one-step target `vehicle.slowDown(id, v, 0.0)` (the changer's
     easing, the chosen follower's cooperation, the ramp anticipation), the target is withheld for that step when
     `_handback_needed(mod, id, v, b_cmd, step_s)` holds.
   - **The test.** `_handback_needed` is the AV path's helper, unchanged: the vehicle's own model's follow speed
     behind its real leader (`vehicle.getFollowSpeed`) is below `v − b_cmd·Δt`, the lowest speed a commanded
     vehicle can reach. `b_cmd = _command_decel(fleet model, decel, emergencyDecel)`, which is `decel` for EIDM.
   - **Counter.** `n_handback_skips`: vehicle-steps on which a target was withheld. A withheld target is not
     counted in `n_cooperations` or `n_changer_eased`.
2. **`weave_close_leader`** (F-R.2).
   - **What it does.** In `_weave_command`, a leader with a negative reported gap (closer than the vehicle's
     `minGap`) is read as a leader at bumper gap `max(reported gap + minGap, 0)`, not as a free road. The IDM
     estimate of the vehicle's own acceleration then reads that leader (−∞ at a zero gap), so no target is recorded
     when the vehicle must brake harder than `b`.
   - **Counter.** `n_close_leader_withheld`: target requests the free-road reading would have recorded and this
     reading does not.
3. **`weave_resolve_opposing`** (F-T, port of `merge_model.resolve_opposing`).
   - **What it does.** The section's change requests of a step (accepted, or forced through the guard) are
     collected and executed after the per-vehicle loop. Before they execute, `merge_model.resolve_opposing`
     (unchanged) decides every pair entering one lane from both sides in that step, on the pre-step state:
     - priority: a due forced change, then any other runner request, then a model-driven change of an undriven
       vehicle; between equals, the one ahead goes;
     - the loser waits one step: a runner request is withheld (mode 512, no request); an undriven vehicle's model
       change is vetoed (its model bits cleared for the step and restored at the start of the next step by
       `_weave_opposing_restore`).
   - **The weave's inputs.** `due` is "the forced change is due" (zone delay spent, or a released partner);
     `accept_s` the movement's accepted gap; `v`, `minGap` and `b` the changer's. An opponent that is driven with a
     request made in the previous step is `open`; driven without one, `driven`; undriven with model bits, `model`;
     undriven without, `held`.
   - **Counters.** `n_opposing_deferred` (withheld requests plus vetoes, as the measured model counts it) and
     `n_opposing_vetoed` (the vetoes).
- **Records.** Each counter is written in `meta.json["weave_sections"][i]` only when its key is on.
- **Not included:**
  - the arrival-step change of an undriven entrant that SUMO's model makes in the very step it reaches the section
    (T1's other possible path, §5.1): the runner sees it only afterwards;
  - braking constraints other than the leader (a lane-end stop), as in the AV path;
  - storing collision speeds in `meta.json` (§9, "Recording"). The harness reads speeds from the trajectories.

### 13.3 Arms and sets

**Arms.** Each set is run twice at the same seeds, with trajectories kept for the readers (macOS, at most two SUMO
processes at once):

- reference: no key;
- W2: `--weave-set weave_handback=1 --weave-set weave_close_leader=1 --weave-set weave_resolve_opposing=1`.

**Diagnostic arms** (reported, never criteria):

- each key alone, on S1 and on every stress set in which some reference run reproduces R or T;
- W1b + W2 against W1b alone on S3a.

**Sets.** S1–S3 are W1b's sets (docs/WEAVE_LOSS_DIAGNOSIS.md §10.4). X-R1 to X-T2 are new stress sets written to
provoke the two mechanisms; their configs are fixed here.

| set | what | runs per arm |
|---|---|---|
| S1 | The T.H.52 section test with the calibrated drivers, seeds 3–22: `merge_model_selfcheck.py th52 --model weave --fleet-from scenarios/mndot_i94_wb_stpaul_weave_dc.yaml` | 20 |
| S2 | The 37-run grid with the fixtures' own fleets: `merge_model_selfcheck.py grid --model weave` | 37 |
| S3a | The five Ruth St fixtures with the calibrated drivers at seeds 3–22: `grid --model weave --fleet-from …_dc.yaml --only ruth_entr,ruth_exit,ruth_entr_fleet,ruth_exit_fleet,ruth_exit_fleet_271 --seeds 3-22` (a config hashing as an earlier one runs once) | 60 |
| S3b | The grid's other weave fixtures with the calibrated drivers at the grid's seeds | 15 |
| X-R1 | Ruth St queued at its entrance peak: `ruth_entr` (`RUTH_DEMAND["entrance_peak"]`) with the calibrated drivers, a downstream speed schedule on the last corridor edge (`network.boundary`, 2 m/s from t = 300 s), 2,400 s, seeds 3–22 | 20 |
| X-R2 | Ruth St queued at the C-D split's exit peak: `ruth_exit` (`RUTH_DEMAND["exit_peak"]`), the calibrated drivers, 1 m/s from t = 300 s, 2,400 s, seeds 3–22 | 20 |
| X-T1 | S1 with `ramp_to_ramp_share` 0 on the weave: every T.H.52 entrant crosses into the mainline and every exiter comes from it, every leg's volume kept | 20 |
| X-T2 | The T.H.52 fixture at the corridor's demand (`th52_corridor_demand`: mainline 4,919 veh/h, entrance 1,412 veh/h, exit fraction 0.212) with the calibrated drivers and `ramp_to_ramp_share` 0, seeds 3–22 | 20 |

**Why these stress sets.**

- **X-R1, X-R2.** R1–R3 happened while the downstream queue covered Ruth St (§6), with the auxiliary lane full end to
  end. No fixture on record forms that state (§10, "A fixture is not the cheaper first step here").
  - The boundary speeds are set so that the queue reaches the gore early in the run. At the fleet means a lane at
    v carries about v/(L + s0 + v·T) vehicles a second. At 2 m/s that is about 2,140 veh/h over three lanes against
    about 4,300 arriving (X-R1), so the queue reaches the gore at about 550 s. At 1 m/s it is about 1,230 against
    about 2,160 (X-R2), so it reaches the gore at about 1,000 s.
  - 2,400 s then leaves 23–31 minutes of queued operation.
- **X-T1, X-T2.** T1–T3 are crossings of an exiter and an entrant. The proportional split sends about a fifth of the
  entrants to the exit without crossing. Share 0 maximises the crossing volume without changing any leg's volume.
  X-T2 adds the corridor's demand, under which the T.H.52 ramp queues, the regime of T2 and T3.

### 13.4 Definitions

- **Collision.** An event of `meta.json["collisions"]` (`n_collisions` counts them all).
- **Mechanism of a collision.** Read from the trajectories (one sample per 0.5-s step), section lanes by
  `meta.json` and the network:
  - **R (rear-end in the auxiliary lane):**
    - the event's lane is lane 0 of a weave section edge;
    - neither party has a sample outside lane 0 in [t − 5 s, t]. A vehicle on the ramp has no sample, which is
      allowed.
  - **T (opposing entries):**
    - the event's lane is section lane k ≥ 1;
    - the collider's and the victim's last entries into lane k before t are at the same sample, within [t − 10 s, t];
    - the collider came from lane k + 1 and the victim from lane k − 1.
  - **Other:** anything else, reported with lanes, roles and the last samples.
- **Reproducing run.** A reference run (any set) with at least one collision of mechanism R or T.
- **Signature (reported, not a criterion).** For each R collision, the collider's acceleration over the 4 samples
  before contact beside its vType's `decel` (from the run's routes file); §10 calls a collider pinned at `−decel`
  the confirming signature.
- **Lock.** W1b's fixture form (§10.4 there): after the 120-s warm-up, the same vehicle is the front of a section
  lane, within 15 m of the gore, below 0.1 m/s, for ≥ 120 s without a break. `run_summary`'s lock flag is read
  beside it.
- **Given-up exits.** `n_missed_exit` of the weave sections.
- **Hard brakes.** Vehicle-steps with a ≤ −9 m/s² + 1e-6 (`run_summary`'s count).

### 13.5 Acceptance criteria, fixed now

If any criterion fails, the keys stay off and the failure is reported. No criterion is re-thresholded.

| # | criterion |
|---|---|
| G0 | **Off is byte-identical.** A `git archive HEAD` tree against the working tree, through W1's `harness/cmp.py` (37 cases: the 12 micro goldens; the T.H.52, Ruth St, McKnight Rd, T.H.61 and golden weave fixtures; the T.H.52 section with the calibrated drivers at seeds 3–5; the measured model on T.H.52, Ruth St and McKnight). Identical means the sha256 of every Parquet file, `meta.json` without wall time, the metrics and the config hash. Also: every committed scenario hashes the same in both trees; `WEAVE_DEFAULTS` and `tests/golden/` unchanged; each key at 0 byte-identical to unset on one SUMO fixture |
| G1 | **Zero collisions** in the W2 arm on every run of S1, S2, S3a and S3b |
| G2 | **Reproduction.** For every reproducing run, the W2 run of the same (set, fixture, seed) has zero collisions. If no reference run reproduces R or T, G2 is **not testable on the fixtures**, and is reported so (not as a pass) |
| G3 | **T.H.52 throughput.** S1 exit-end flow, paired W2 − reference: 95 % t-interval lower bound > −50 veh/h |
| G4 | **Given-up exits.** (a) S1, paired per run W2 − reference: 95 % upper bound ≤ +1.5. (b) S2, and S3a + S3b pooled: the W2 total ≤ ref + 2 + 2·√(2·ref), ref being the reference total |
| G5 | **Hard brakes**, per set (S1, S2, S3a + S3b): the W2 total ≤ ref + 2 + 2·√(2·ref) |
| G6 | **No new lock.** No W2 run of any set (stress sets included) has a lock (§13.4) or a `run_summary` lock flag at a weave section that its reference run lacks |
| G7 | **Stress sets.** No W2 collision of mechanism R or T, and the W2 arm's collisions over X-R1 to X-T2 are no more than the reference's. Every other collision is reported with its mechanism |

**How the thresholds were chosen** (from the committed W1 record, before any W2 run):

- **G3, X = 50 veh/h.**
  - **The noise.** W1's paired S1 comparison (`w1/th52_ref.json` against `w1/th52_w1.json`) has a per-seed sd of
    41 veh/h, a 95 % half-width of 19 veh/h.
  - **Passing a null effect.** With a true effect of zero, the lower bound clears −50 with probability above 0.999.
  - **Catching a real loss.** A true loss of 50 veh/h fails with probability ≥ 0.5, and one of 70 veh/h with about
    0.98.
  - **Scale.** 50 veh/h is 1.1 % of the section's 4,361 veh/h reference flow and about a tenth of the weave's
    465 veh/h loss (docs/WEAVE_LOSS_DIAGNOSIS.md §2).
  - **What it accepts.** The guards withdraw cooperation only in emergencies and defer a change by one step, so a
    cost below that scale is accepted for a collision fix.
- **G4 (a), +1.5 exits per run.**
  - **The noise.** W1's paired given-up difference has an sd of 1.77 per run, a half-width of 0.83. A null effect
    passes with probability ≈ 0.95.
  - **Scale.** 1.5 exits are 0.36 % of the ≈ 417 exiters reaching the section per run (reference: 3.3 given up per
    run).
- **G4 (b) and G5, the band ref + 2 + 2·√(2·ref).**
  - **Why a band.** Once a guard has acted, the two runs diverge, and counts of rare events behave as independent
    draws. The band is two standard deviations of the difference of two Poisson counts at the reference's total,
    plus W1/W1b's absolute allowance of 2 (their F4).
  - **Hard brakes may rise for a good reason.** A withheld target lets a vehicle brake harder than `b` when its
    model needs to. A rise inside the band is accepted; above it, the guard has made driving abrupt.

**Predictions, registered, not criteria:**

- **G0** holds by construction.
- **The reference arms** reproduce W1b's reference rows (`w1b/s1_ref.json`, `s2_ref.json`, `s3a_ref.json`,
  `s3b_ref.json`) field for field, config hash aside. This also checks the tree.
- **Guard activity.**
  - The handback and the close-leader reading fire in queued fixtures.
  - The resolution fires at crossing pairs.
  - So most W2 runs diverge from their references.
  - A W2 run in which all three counters read 0 is expected to equal its reference (Parquet sha256; `meta.json`
    without wall time, config hash, the weave params and the W2 counters). This is reported as diagnostic D1, not a
    criterion.
- **Reproduction is uncertain.** Every fixture on record has zero collisions. X-R1/X-R2 force the standing
  auxiliary-lane queue that R needs, and X-T1/X-T2 raise the crossings T needs. Neither is known to collide.

**What passing means.** Passing every fixture criterion does not adopt W2. If no fixture reproduces either
mechanism, the fixtures show only that the guards cost nothing. Whether they remove the corridor's collisions is the
corridor round's question (13.6), and adoption is the owner's decision.

### 13.6 Corridor criteria, fixed now (cloud; run only on the owner's decision)

**The runs.** Two batteries on one code tree, the I-94 four-hour battery of p8 (20 seeds spawned from seed 42;
`--observations` the calibration-day targets; profile `fhwa_tat3_2004`), paired by seed:

- **A:** `scenarios/mndot_i94_wb_stpaul_weave_dc_cal.yaml` (hash `beaaa710e6b3`) with W1b on both weaves
  (`entrant_giveup_m` 5, `entrant_giveup_dwell_s` 60; `exit_prepare` 1 kept);
- **B:** A with the W2 setting added on both weaves.

**Why A is W1b, not `_dc_cal`.**

- The proposal is W1b + W2 together.
- Pairing B with A isolates W2.
- A on its own shows whether W1b already removes the Ruth St collisions by shortening the stranded queue (§9).
- p8's `_dc_cal` battery (2 collisions, both R) is the context, not the pair.

| # | criterion |
|---|---|
| CW1 | S790 06:30–07:30 simulated flow, paired B − A: 95 % lower bound > −50 veh/h (p9's paired half-width was 16.8 veh/h; 50 is 1.1 % of the observed 4,667) |
| CW2 | Departed share, paired B − A: 95 % lower bound > −1.0 pp (p9's half-width 0.95 pp) |
| CW3 | Zero collisions in B. A's collisions are reported by section (999007700 Ruth St, 51388891 T.H.52, other) |
| CW4 | Zero locks in B: `corridor_w1b.py`'s end-of-run front-row reading (C4b) and the battery's own `validation.locks` per-seed records. A disagreement between the two is reported, not resolved |
| CW5 | Given-up exits (`n_missed_exit`) per weave, pooled over seeds: B ≤ A + 2 + 2·√(2·A). W1b's releases ≤ 1 % of each entrance's departures, pooled (C5b) |

Reported beside, not criteria: link-flow GEH < 5 share, the baseline gate's verdicts, the W2 counters per section.

**A note on power.** `_dc_cal` had R collisions in 2 of 20 replicates and T collisions in none (all three were under
netfix, §7). The pair tests R directly; for T it tests only that the guard costs nothing. A netfix pair
(`_dc_cal_netfix` with W1b, and with W1b + W2) is the optional extension for T, with the same criteria.
