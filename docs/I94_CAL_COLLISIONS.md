# Collisions in the calibration-day I-94 batteries (2026-10-07)

Stage p8 (docs/I94_CALIBRATION_DAYS.md, "Results of stage p8") recorded six SUMO collisions with the
calibration-day inputs. Step 3's battery on the nine-day inputs (`_dc`, hash `db9fbab5fc6e`) recorded none, and
neither did the speed-factor arm (`_dc_cal_sf`):

- `_dc_cal` (hash `beaaa710e6b3`): 2 runs, both on edge 999007700 lane 0 at 10–12 m;
- `_dc_cal_netfix` (hash `182e3ec2f500`): 4 runs, one on 999007700 lane 0 (115 m) and three on 51388891 lanes 1–2
  (36–85 m).

This note asks what each collision is: where, between which vehicles, which lane change or merge was involved,
what differs from step 3, and whether the netfix map change plays a part. Zero collisions is a pass/fail criterion
of every run set (CLAUDE.md §3.3), so none of these batteries can be read until the cause is known.

Nothing was simulated. No code, scenario, artifact or golden was changed, and nothing was committed. Python was
used only to read JSON and Parquet files. Inputs:

- **The p8 runs.** `meta.json` and `vehicles.parquet` of all 60 replicates of the three p8 batteries
  (`runs/mndot_i94_wb_stpaul_weave_xlsfg_dc_cal{,_netfix,_sf}/baseline/<hash>/<seed>/`). They were extracted from
  stage p8's archive (`final.tgz`) and are not committed. The archive holds no `trajectories.parquet` and no
  `edges.parquet`.
- **Step 3's 20 replicates.** The same two files, from stage 23's archive, as read in
  docs/I94_COLLAPSE_DIAGNOSIS.md.
- **Committed artifacts:**
  - `artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal{,_netfix,_sf}.json` and `..._dc.json`
    (collision blocks, `per_seed`);
  - `artifacts/i94_netfix_probe.json`;
  - VM AF/AG's collision lists, `artifacts/mndot_rounds/weave_2026-09-24/collisions_*.json`.
- **The scenarios** `scenarios/mndot_i94_wb_stpaul_weave_dc{,_cal,_cal_netfix}.yaml`.
- **Code, read only:** `packages/microsim/microsim/runner.py`.
- **The lock detector** `validation.locks.detect_run_locks`, run on the archived files. Only its run-end reader
  could be used, because `edges.parquet` was not archived.

Labels: **[artifact]** read from a committed file; **[run file]** read from the uncommitted per-run files above;
**[computed]** computed here from those; **[estimate]** an estimate with its basis stated; **[inference]**
follows from the files and the code but is not observed; **[hypothesis]** not shown by these files.

## 0. In plain English

- **All six collisions are at the corridor's two weaving sections.** None is at a scripted merge, an
  acceleration lane or anywhere else. Both scripted ramps run with `force_guard` on and recorded none in all 60
  runs.
- **Ruth St, three collisions (two in `_dc_cal`, one in netfix): a rear-end inside the auxiliary lane.**
  - **Who.** Two consecutive Ruth St on-ramp vehicles, both bound *through*, so both owe a change into lane 1.
    The rear one runs into the front one.
  - **No lane change is involved.** Both entered the section in lane 0. Lane 0 is fed only by the ramp, and an
    entrant never moves right.
  - **Where and how fast.** In two cases the contact is 10–12 m into the section. The front vehicle has stood
    there or crawled for 13–23 s, and the rear one arrives from the ramp at under 3 m/s on average. The third
    contact is 21 m before the gore.
  - **The setting.** One run ends with the auxiliary lane full: 16 vehicles from the gore back to its start,
    headed by a through-bound entrant standing 0.08 m from the lane's end.
  - **Likely mechanism [hypothesis].** The rear vehicle cannot brake hard enough because a weave speed target
    holds it. SUMO caps the braking of a vehicle under a TraCI speed target at its comfortable deceleration.
    That is WP-95's finding for AVs, and it applies equally to the weave's one-step `slowDown` targets on human
    drivers, which have no handback. On top of that, the weave's own guard reads a leader closer than the
    vehicle's `minGap` as "no leader": the defect WP-96 fixed for AVs only.
  - **History.** The same event has happened before: VM AG's `lane_change` arm and the phase-1 rehearsal (§4.4).
    It has never been traced.
- **T.H.52, three collisions, all on netfix: an exiter runs into an entrant that has just crossed into its lane.**
  - **Who.** The rear vehicle is bound for the T.H.52 exit, so it must cross to lane 0. The front vehicle came
    from the T.H.52 ramp.
  - **Where and when.** The front vehicle appeared in lane 0 only 6.5–10.5 s earlier and is already in lane 1
    or 2, 36–85 m into the section. That is far upstream of the forced zone.
  - **Why not a cut-in.** The weave accepts a change only if the follower can absorb it at its comfortable
    deceleration, and with a fast exiter behind that needs about 90 m of gap (§5.2). So an ordinary cut-in in
    front of an exiter already in that lane is very unlikely.
  - **Fitting reading [hypothesis].** The two vehicles entered the same lane from opposite sides in the same
    step. SUMO executes the front change first, and the rear's weave change (mode 256) then lands at any gap
    behind it. That is WP-92's "opposing entries". Its weave guard was left off and deleted on 2026-10-06; the
    measured merge model still resolves such entries, always on.
- **What differs from step 3: not the load at the weaves, but how long the queue covers them.**
  - **Same model.** Geometry, drivers and merge models are identical; only the input series changed.
  - **Ruth St's own demand is unchanged:** 1,017 entrants against 995.
  - **The downstream queue now spills back over Ruth St for most of the peak.** Mainline travel time to the C-D
    split is 316–1,098 s from 06:30 to the end, against 220–250 s outside 07:00–08:00 in step 3.
  - **The Ruth St weave therefore works far harder.** Paired by seed, forced changes rise by 191 per run, pair
    releases by 920 (higher on 20 of 20 seeds), and the entrants' mean wait by 19 s.
  - **T.H.52's counters do not move** between step 3 and `_dc_cal`.
- **Netfix and 51388891: plausibly involved, not shown.**
  - **What the map change touches.** It leaves 51388891 itself unchanged (305.02 m, 4 lanes) and acts 0.9–1.1 km
    upstream, on which lanes traffic arrives in.
  - **Same vehicles in both arms.** The plan is identical in `_dc_cal` and netfix: same vehicles, routes and
    departures.
  - **T1 in free flow.** The same two vehicles reached T.H.52 within 0.5 s of their netfix timing in `_dc_cal`
    and did not collide.
  - **Forced changes.** Netfix raises T.H.52's forced changes by 52 per run [+9, +95].
  - **The rate.** Three collision runs against none is not resolved (Fisher p = 0.23).
- **Fix direction (proposal):**
  - **Ruth St:** bring the AV command path's two fixes to the weave's speed targets: withhold a target when the
    vehicle's own model must brake harder than it allows (`_handback_needed` exists), and read a close leader as
    a leader.
  - **T.H.52:** resolve opposing entries before a weave change executes, either with the measured model's
    resolution or by running both I-94 weaves on `merge: measured`.
  - **Both:** behind keys, fixtures first.
- **The archived files cannot confirm either mechanism.** They hold no trajectories, no command record and no
  collision speeds.
- **Confirmation (implemented 2026-10-07 as stage `p8c_i94_cal_collisions`, not launched).** Re-run the six
  collision replicates plus one paired control in one wave on one n2-standard-16, with trajectories kept, check
  each against p8's logged collision, and read the window before each contact. This costs about
  **$0.60–0.75**, capped at about $1.00 (§10).
  - **Superseded (2026-10-07):** p8c ran the same day (§15; about $0.40). All six collisions reproduced exactly;
    two of the three T.H.52 collisions are opposing entries (the third a late cut-in from the right), and one
    Ruth St rear car was pinned at its comfortable deceleration while the other two braked beyond it, late.

## 1. The two edges

| | 999007700 (Ruth St weave) | 51388891 (T.H.52 weave) |
|---|---|---|
| position (corridor axis) | 5,180.87–5,316.64 m (both arms) | 10,426.62–10,731.64 m; netfix 10,424.30–10,729.32 m |
| length, lanes | 135.77 m, 4 lanes; `short_section: true` | 305.02 m, 4 lanes; not short |
| lane 0 | auxiliary lane, fed **only** by on-ramp 745524613 (Ruth St); leads **only** to the C-D split 18208090 | auxiliary lane from on-ramp 769818012 (T.H.52 / US 52 NB, 844 m of ramp); leads only to off-ramp 18207598 |
| lanes 1–3 | fed by 638519829 lanes 0–2; continue as 95085307 | continue as 1001426896 lanes 0–2; its lane 0 feeds both the Jackson St exit 82150350 and the corridor's end |
| merge model | `weave` (scripted weaving section) | `weave` |
| forced zone (`force_within_m` 80, `force_after_s` 4) | 55.8–135.8 m | 225.0–305.0 m |
| vacate / `exit_prepare` window | 638519829 | 40648793#0, 40648793#1, 40648738-AddedOnRampEdge, 40648738 |

**Sources.** `meta.json` `weave_sections`, `ramps` and `lane_end_giveups.skipped_edges` [run file]; the lane
connections from docs/WEAVE_MODEL_PLAN.md (WP-93 geometry table; the T.H.52 "section as built" table) and
docs/I94_LANE_SHARES.md §4. Both sections are skipped by the network's lane-end give-up, and neither is a scripted
ramp or a lane drop.

**How the weave drives these lanes** (runner.py `_weave_step`, `_weave_cooperate`, `_weave_command`,
`_weave_exec_change`; read only):

- **Entrants.** A vehicle not bound for the paired exit, on lane 0, is driven to change into lane 1 and is
  handed back to SUMO's own lane-change model once it is there.
- **Exiters.** A vehicle bound for the exit, on lane 1 or above, is driven right one lane per request.
- **Every weave change executes under mode 256** (`LC_MODE_SCRIPTED_FORCE`), accepted and forced alike. Mode 256
  refuses only an overlap. The acceptance itself is read on the state after the previous step.
- **Speed targets.** Every step, the weave issues one-step `vehicle.slowDown(id, v, 0.0)` targets to:
  - the changer, easing towards its chosen gap's leader;
  - the follower of the chosen gap, cooperating towards the changer as a virtual leader;
  - ramp vehicles within `lookahead_m` = 120 m of the section, through anticipation.

## 2. What the runner records about a collision

- **The log.** runner.py, the step loop at about lines 6945–6963, keeps for the first 50 events
  (`COLLISION_LOG_MAX`): `t`, `collider`, `victim`, `type`, `lane` and `pos_m` (the collider's position on that
  lane). `n_collisions` counts every event.
  - **When an event is counted.** A pair is counted in the step SUMO first detects it.
  - **What counts as a collision.** The runner leaves `--collision.mingap-factor` at −1, so for EIDM a contact
    is the rear vehicle's front within 0.1 × its own `minGap` of the leader's back (docs/CONTRACTS.md,
    corrected 2026-09-26).
  - **Effect on the run.** `--collision.action warn` keeps both vehicles driving.
- **The type.** All six events are `"collision"`: SUMO's movement-stage contact, a rear-end after any lane
  change had landed without overlap (docs/WEAVE_MODEL_PLAN.md WP-93 §3).
- **What is not recorded:**
  - SUMO's collision object carries `colliderSpeed` and `victimSpeed`; the runner does not store them.
  - The lane each vehicle was in during the steps before.
  - Which weave command was in force.
- **What `vehicles.parquet` adds.** One row per departed vehicle: route, origin and destination, planned and
  actual departure, and the first and last corridor sample (t, x, lane). Ramp edges are not on the corridor axis,
  so a ramp vehicle's first sample is its arrival on the section.

## 3. The six collisions

Clock = 05:30 + t. Positions in the section are measured from its start, in each arm's own x. Mean speeds
assume 5 m vehicles (`VEHICLE_LENGTH_M`).

| # | arm, seed | t [s] (clock) | lane, pos | collider (rear) | victim (front) |
|---|---|---|---|---|---|
| R1 | `_dc_cal`, 134183728835869882 | 14,356.5 (09:29:16) | 999007700_0, 12.16 m | v17620, Ruth St entrant → corridor end; departed 14,327.5, on the section at 14,352.5 at 1.2 m | v17619, Ruth St entrant → corridor end; departed 14,309.5, on the section at 14,333.5 at 0.1 m |
| R2 | `_dc_cal`, 6134032994440706937 | 8,443.5 (07:50:43) | 999007700_0, 10.03 m | v17184, Ruth St → corridor end; on the section at 8,440.5 at 1.7 m | v17183, Ruth St → Mounds Blvd exit 18207912 (through at Ruth St); on the section at 8,430.5 at 1.6 m |
| R3 | netfix, 134183728835869882 | 13,421.0 (09:13:41) | 999007700_0, 114.93 m (21 m before the gore; forced zone) | v17557, Ruth St → corridor end; on the section at 13,406.0 at 1.9 m | v17556, Ruth St → Mounds Blvd exit; on the section at 13,393.5 at 3.2 m |
| T1 | netfix, 165503670820534583 | 574.5 (05:39:35, warm-up) | 51388891_1, 76.74 m | v19448, T.H.61 NB entrance 53062592 (7.40 km) → **T.H.52 exit 18207598**; on the corridor at 433.5 s | v27416, **T.H.52 entrant** → Jackson St exit 82150350; departed on time, on the section at 568.0 at 6.2 m |
| T2 | netfix, 677105600768189526 | 3,538.0 (06:28:58) | 51388891_2, 36.08 m | v12401, Hudson Rd entrance 18207436 (3.39 km) → **T.H.52 exit**; on the corridor at 3,174.5 s | v28312, **T.H.52 entrant** → corridor end; insertion delayed 137 s, 209 s on the ramp, on the section at 3,530.0 at 0.2 m |
| T3 | netfix, 6953598295321596746 | 4,009.5 (06:36:50) | 51388891_2, 85.17 m | v20578, T.H.61 NB entrance → **T.H.52 exit**; on the corridor at 3,679.5 s | v28473, **T.H.52 entrant** → corridor end; insertion delayed 175 s, 222 s on the ramp, on the section at 3,999.0 at 0.6 m |

[run file: `meta.json` `collisions`, `vehicles.parquet`]

**Derived [computed].**

| # | victim on the section before the contact | victim's mean speed since its arrival | collider's mean speed | spacing at departure / arrival |
|---|---|---|---|---|
| R1 | 23.0 s | ≈ 0.7 m/s (front from 0.1 to ≈ 17 m) | 2.7 m/s over its last 4.0 s (1.2 → 12.2 m) | 18.0 s / 19.0 s |
| R2 | 13.0 s | ≈ 1.0 m/s | 2.8 m/s over its last 3.0 s | 10.0 s / 10.0 s |
| R3 | 27.5 s | ≈ 4.2 m/s | 7.5 m/s over its last 15.0 s | 14.0 s / 12.5 s |
| T1 | 6.5 s | ≈ 11.6 m/s | 22.0 m/s over 3.1 km | — |
| T2 | 8.0 s | ≈ 5.1 m/s | 19.5 m/s over 7.1 km | — |
| T3 | 10.5 s | ≈ 8.5 m/s | 9.4 m/s over 3.1 km | — |

What happened after the contact [run file]:

- **R1.** Both vehicles stand where they collided until the run ends 43.5 s later: the victim at 21.4 m, the
  collider at 12.2 m, both in lane 0.
- **R2 and the T pairs** all arrived at their destinations.
- **R3.** Both vehicles were still in the network at the run's end.

## 4. Ruth St (R1–R3): a rear-end inside the auxiliary lane

### 4.1 What the files show

- **Both parties are Ruth St entrants bound through, consecutive in the plan** [run file]. The plan numbers
  vehicles by origin; v16608–v17624 are the Ruth St ramp's 1,017 in these runs. Each owes the weave's entering
  change into lane 1.
- **No lane change put either of them where it was hit** [inference]:
  - both entered the section in lane 0 (`entry_lane` 0), the victim first;
  - lane 0 is fed only by the ramp;
  - the weave drives an entrant only leftwards, and SUMO's own model does not move a through-bound vehicle into
    an exit-only lane;
  - a third vehicle changing in between would itself have been the one hit.

  So R1–R3 are pure car-following contacts in one lane.
- **R1 and R2 happen at the tail of a queue standing at the section's start.**
  - The victim had advanced only about 17 m in 23 s (R1) and 13 m in 13 s (R2).
  - The collider arrived from the ramp (24–26.5 s on it, against a 20–26 s half-hourly median) and covered its
    last 8–11 m at under 3 m/s [computed].
  - R3 is near the gore, in the forced zone, where both owe a forced change and SUMO brakes a through-bound
    vehicle towards the end of the exit-only lane.
- **The setting, seen at R1's run end (43.5 s after the contact)** [run file, computed]:
  - lane 0 of 999007700 holds 16 vehicles from 4.5 m to 135.69 m, 6.7–15.4 m apart: 11 Ruth St entrants
    (10 through-bound) and 5 upstream vehicles bound for the C-D split;
  - its front is v17606, a through-bound Ruth St entrant standing **0.08 m** from the auxiliary lane's end;
  - this is the stranded-entrant state of docs/I94_COLLAPSE_DIAGNOSIS.md §3. Here it is not a lock by
    `validation.locks` (the run-end reader finds none in this run), because lanes 1–3 still move;
  - the same seed under netfix (R3's run) ends the same way: 17 vehicles, a through-bound entrant 0.09 m from
    the end.
- **How often the run ends in that state** [computed; `vehicles.parquet`, vehicles in the network at 14,400 s in
  999007700 lane 0]:

  | arm | runs ending with a through Ruth St entrant within 0.5 m of the gore | median queue in lane 0 |
  |---|---|---|
  | step 3 | 4 of 20 | 0–1 vehicles |
  | `_dc_cal` | 8 of 20 | 5 |
  | netfix | 10 of 20 | 8.5 |

- **Every vehicle in that lane is under weave commands every step** [inference from runner.py]:
  - an entrant on lane 0 is driven (gap choice, and easing towards its lane-1 gap's leader);
  - a lane-0 vehicle is also the cooperating follower of any exiter's chosen gap;
  - a ramp vehicle within 120 m is under anticipation.

  The counters agree. Paired by seed, Ruth St's changer easing and cooperation run +11,600 and +13,800
  vehicle-steps per run above step 3. Neither interval is resolved per seed, but both are higher on 18 of 20
  seeds, and the arm totals are 580,461 against 348,713 and 883,951 against 607,934 [computed].

### 4.2 The likely mechanism [hypothesis]

SUMO's EIDM does not drive into a standing leader on its own: there is no collision on any undriven lane in 60
four-hour runs, and none in step 3. The rear entrant here is a vehicle the weave can command. Two properties of
the weave's command path fit a low-speed rear-end at a standing queue:

1. **A weave speed target caps braking at the vehicle's comfortable deceleration.**
   - **The source.** WP-95 read SUMO 1.27.1's source: under the default speed mode the influencer applies its
     maximum-deceleration clamp after its safe-speed clamp, so a vehicle under a TraCI speed target brakes no
     harder than `minNextSpeed`, that is `decel` (b) for EIDM (runner.py `_command_decel`).
   - **Why it applies here.** `vehicle.slowDown(id, v, 0.0)` goes through the same influencer.
   - **What the weave assumes.** The weave's docstring treats "a brake beyond b cannot be commanded" as a safety
     property. It was written on 2026-09-24, two days before WP-95, and is also a limit: in a step where the
     vehicle's own model must brake harder, it cannot.
   - **The fix exists for AVs only.** `AVSpec.emergency_handback` withdraws the command in such steps
     (`_handback_needed`, via `vehicle.getFollowSpeed`). Nothing does so for the weave's targets on human
     drivers.
2. **The weave's guard reads a close leader as no leader.**
   - **What the guard does.** `_weave_command` records a target only when it is below the vehicle's own
     acceleration, which the runner estimates with its own IDM towards `vehicle.getLeader`, read on the
     pre-step state.
   - **The defect.** A leader closer than the vehicle's `minGap` (a negative reported gap) is treated as a free
     road (`lead[1] < 0.0`). Inside its `minGap` of a standing leader, a vehicle is therefore still commanded,
     and therefore capped at b.
   - **The precedent.** This is the AV path's defect that WP-96 fixed with `observe_close_leader`.
   - **When it bites.** At the fleet's means (b ≈ 1.70 m/s², `minGap` ≈ 2.5 m), a vehicle at its `minGap`
     behind a standing leader cannot stop at b before the 0.1 × `minGap` contact line from about 2.8 m/s, and
     from less once inside it [computed from the means of `artifacts/idm_i24_capacity_amax_k1.0.json`,
     kinematics only, v²/(2b) against 0.9 × `minGap`]. That matches R1/R2's approach speeds. The
     guard's IDM estimate also differs from the vehicle's EIDM (estimation errors, its own stopping behaviour),
     which can open the first step of the cap.

**The signature that would confirm it.** In the steps before contact, the collider decelerates at exactly its
vType's `decel` (not towards its 9 m/s² `emergencyDecel`) while its gap closes, with a weave target in force.

**Alternatives the files cannot exclude:**

- the vehicle's own stop at the dead end of an exit-only lane, combined with the follower's approach, with no
  command acting;
- the junction from the ramp, compiled without internal lanes (`internal_links: false`), in the last steps of
  arrival. This applies to R1/R2 only, whose colliders were on the section for only 3–4 s.

### 4.3 Known or new

- **Not WP-93's `force_guard` event.** No scripted merge acts here, and no lane change lands anyone.
- **Not the slice's 2026-09-23 collision on 999007700_0** (the reason for the weave's speed-aware forced guard,
  runner.py `_weave_force_gap_ok`). That was a forced change *into* lane 0.
- **Not an AV event.** `av.penetration` is 0.
- **The same SUMO behaviour as WP-95/96, acting on the weave's own targets.** That path has never been examined
  for human drivers [hypothesis].

### 4.4 It has happened before, untraced

- **VM AG** (`collisions_force_guard_and_lane_change_1259a9b.json`, `lane_change_merges` arm [artifact]): one
  collision on 999007700_0 at 42.72 m, t = 5,913 s, collider v17015, victim v17014. The ids are consecutive, so
  the two vehicles are probably from one origin; that plan was not rebuilt here, so Ruth St is not checked
  [inference]. VM AG noted it ("1 (in the weave section, edge
  999007700)") and did not trace it.
- **The phase-1 rehearsal** (docs/PHASE1_REHEARSAL.md; docs/MERGE_MODEL_BRIEF.md item 12): collisions on
  999007700_0 and 51388891_1 "about 20 m in". "The mechanism is not traced: the artifacts hold no lane, time or
  vehicle."
- **The measured merge model** ran those two phase-1 cases for four hours with zero collisions (docs/MERGE_MODEL.md,
  gate C).

## 5. T.H.52 (T1–T3): an exiter runs into an entrant that has just crossed into its lane

### 5.1 What the files show

- **The rear vehicle is always an exiter** bound for the T.H.52 exit 18207598, arriving from upstream:
  - from the T.H.61 NB entrance twice and Hudson Rd once;
  - it must cross from lanes 1–3 to lane 0 within 305 m;
  - it is driven by the weave rightwards, one lane per request, each change under mode 256.
- **The front vehicle is always a T.H.52 entrant.** It appeared in lane 0 only 6.5–10.5 s before the contact,
  and at the contact it is in lane 1 (T1) or lane 2 (T2, T3), 36–85 m into the section [run file].
  - The weave drives an entrant only from lane 0 into lane 1 and hands it back there.
  - So T1's victim made the weave's entering change (or SUMO's own change in its arrival step) within those
    6.5 s.
  - T2's and T3's victims, both bound for the corridor's end, also changed from lane 1 into lane 2 by SUMO's own
    model after the hand-back [inference].
- **None of the three is a forced change.** T.H.52's forced zone starts at 225 m; the contacts are at 36–85 m.
  The pair release forces only inside the zone.
- **Two regimes:**
  - **T1 is in free flow** at 05:39, inside the warm-up. The entrant drove the 844 m ramp in 39.5 s (free
    travel ≈ 38 s), and the exiter averaged 22 m/s over the 3.1 km before.
  - **T2 and T3 come out of the T.H.52 ramp queue** (137 / 175 s of insertion delay, 209 / 222 s on the ramp).
    That queue exists from about 06:00 in every arm, step 3 included [computed]:
    - median time on the 844 m ramp 103–118 s at 06:00–06:30, against about 38 s free;
    - median insertion delay 244–289 s at 06:30–07:00.

### 5.2 The reading [hypothesis, the strongest the files allow]

**Opposing entries into one lane in one step** (WP-92, CHANGELOG; docs/MERGE_MODEL_BRIEF.md item 10).

- **How it happens.** The entrant enters the lane from the right in the same step as the exiter enters it from
  the left. In T1 the entrant moves 0 → 1 by the weave's request (or SUMO's arrival change) and the exiter
  2 → 1. In T2/T3 the entrant moves 1 → 2 by SUMO's own LC2013 and the exiter 3 → 2.
- **Why it collides.**
  - SUMO executes an edge's lane changes front first, so the entrant, ahead, lands first.
  - The exiter's weave change was accepted on the pre-step state, where the lane was clear. Under mode 256 it is
    refused only on an overlap, so it lands at whatever gap remains behind the entrant.
  - The exiter is much faster (T1, T2) and makes contact in the next movement.
- **This is how WP-80's and WP-90's collisions arose** on the fixtures.

**Why the plain alternative is unlikely: the entrant's accepted change landing in front of an exiter already in
the lane.** The weave's acceptance (runner.py `_weave_step`) requires all of:

- the follower gap ≥ s0 + 0.6 s × v_F;
- the follower's IDM towards the changer ≥ −b_F;
- the brake-gap guard.

At T1's proxy speeds (follower 22 m/s, entrant ≈ 11 m/s) and the fleet means, the IDM absorption condition alone
asks for about 88 m [computed: IDM with T 1.32 s, a 1.48 m/s², b 1.70 m/s², s0 2.5 m, v0 at the 24.6 m/s limit].
Half a second of closing before execution removes about 5.5 m of that. A contact needs a gap below about one
`minGap`. SUMO's own LC2013 changes are checked against the follower's secure gap at the moment of change, so the
same argument holds for T2/T3's 1 → 2 change.

**What the files cannot show.** Whether the exiter entered the lane in the same step as the entrant, which is
the defining fact. That needs the per-step lanes (§10).

### 5.3 Known or new

- **Known: opposing entries** (WP-92).
  - **The guard's record.** Its weave guard `opposing_entry_guard` was measured on fixtures: 33 conflicts to none
    in 56 runs, and WP-90's collision gone. It failed the T.H.52 capacity pin's give-up clause at seed 5.
  - **On the corridor (VM AF)** it cost −0.029 departed share (not resolved) and one locked seed.
  - **Deleted.** It was deleted with the dead switches on 2026-10-06 (docs/MERGE_MODEL.md A4).
  - **Where a resolution remains.** `merge: measured` keeps an always-on resolution (`merge_model.resolve_opposing`;
    docs/MERGE_MODEL.md §2 "Execution").
- **Recurring.** The phase-1 rehearsal's collision on 51388891_1 about 20 m in is very probably the same family
  [inference].

## 6. What is different from step 3

**The model is the same.** `_dc` and `_dc_cal` differ only in their series: inflow, boundary schedule, ramp
inflows and exit fractions. Network options, fleet, merge models and their parameters are identical [run file:
scenario diff]. So the collisions do not come from new geometry or new rules.

**The load at the weaves themselves barely moved** [run file: `meta.json` `ramps`, seed 134183728835869882]:

| | step 3 `_dc` | `_dc_cal` |
|---|---|---|
| Ruth St entrance 745524613, vehicles planned in 4 h | 995 | 1,017 |
| C-D split 18208090, vehicles planned exiting | 1,570 | 1,554 |
| T.H.52 entrance 769818012 | 5,047 | 5,100 |
| T.H.52 exit 18207598 | 4,322 | 4,339 |
| **Mounds Blvd exit 18207912** | **4,163** | **2,897** |
| **Mounds/Kittson entrance 40648744** | **3,536** | **1,987** |

**What changed is how long the queue covers the weaves.** Less traffic leaves at Mounds Blvd (8.5 km), so the
corridor downstream queues longer (docs/I94_CALIBRATION_DAYS.md, "Results of stage p8"), and the queue spills back
over Ruth St for most of the peak. Median mainline travel time from the corridor start to each exit, by entry
half-hour [computed; 20 runs per arm, mainline-origin vehicles that arrived]:

| to … (km) | arm | 05:30 | 06:00 | 06:30 | 07:00 | 07:30 | 08:00 | 08:30 | 09:00 |
|---|---|---|---|---|---|---|---|---|---|
| Hudson Rd exit (2.54) | step 3 | 104 | 106 | 111 | 112 | 116 | 110 | 109 | 107 |
| | `_dc_cal` | 104 | 106 | 112 | 115 | **372** | 160 | 110 | 106 |
| C-D split, past Ruth St (5.32) | step 3 | 220 | 224 | 249 | 704 | 631 | 249 | 233 | 226 |
| | `_dc_cal` | 220 | 225 | **316** | **1,048** | **1,098** | **655** | **494** | **375** |
| | netfix | 220 | 225 | 294 | 1,058 | 1,174 | 765 | 574 | 618 |
| corridor end (11.47) | step 3 | 478 | 528 | 980 | 1,534 | 1,493 | 1,098 | 1,029 | 921 |
| | `_dc_cal` | 478 | 543 | 1,124 | 1,698 | 1,943 | 1,528 | 1,308 | 1,098 |

- **In step 3 the queue sits over Ruth St for about an hour (07:00–08:00).** With the calibration-day inputs it
  covers Ruth St from 06:30 to the run's end, and reaches back past Hudson Rd at 07:30.
- **R1 and R3 (09:29, 09:13) happen when step 3's Ruth St was in free flow** (226 s); R2 (07:50) when both were
  queued.

**The Ruth St weave works much harder; T.H.52 does not** [computed; paired by seed, `_dc_cal` − step 3, mean
[95 % t-interval], seeds higher]:

| counter (per run) | Ruth St | T.H.52 |
|---|---|---|
| vehicles taken under control | +381 [+249, +512], 18/20 | −88 [−358, +181] |
| forced changes | +191 [+139, +244], 17/20 | −22 [−64, +19] |
| deferred forced changes (vehicle-steps) | +16,835 [+4,666, +29,003], 18/20 | +230 [−5,632, +6,092] |
| stopped pairs released | **+920 [+763, +1,076], 20/20** | −4 [−50, +42] |
| entrants' mean wait [s] | +18.8 [+14.6, +23.1], 19/20 | — |
| exiters asked over early (`exit_prepare`) | +118 [+78, +159], 18/20 | +24 [−61, +110] |

**Reading.**

- **Ruth St.** The calibration-day inputs do not put more traffic through the Ruth St weave. They keep it in
  queued, stop-and-go operation two to three times as long:
  - more vehicles reach it still owing their change;
  - more stand in its auxiliary lane, end to end;
  - more are under weave speed targets each step, which is the exposure the hypothesis of §4.2 needs.
- **T.H.52** was already in its queued regime from 06:00 in step 3, and its counters do not move. Its collisions
  are only in the netfix arm (§7).
- **Rates are not resolved.** Ruth St collisions in 3 of 40 calibration-input runs against 0 of 20 in step 3:
  Fisher p = 0.54. The comparison above concerns operating state, not collision rate.

## 7. Is the netfix map change involved on 51388891?

**What the map change is.** `--ramps.unset 1001426896,45782590` against `--ramps.unset 1001426896`: the 6th
Street left exit compiles as the OSM layout (lanes 0–2 continue, the left lane also feeds the exit) instead of a
left lane drop (docs/I94_LANE_SHARES.md §4). It acts at 45782590, 8.52–9.57 km, 0.9–1.1 km upstream of the T.H.52
section.

**What is the same in both arms:**

- **51388891 itself.** Same length (305.02 m), the same 4 lanes, the same `weave_params`, the same vacate window
  [run file: `meta.json` `weave_sections`, both arms]. Its x shifts by −2.32 m because 45782590 compiles
  differently.
- **The plan.** For every collision seed, `_dc_cal` and netfix have the same vehicles with the same routes and
  planned departures (all 26,106–31,842 common vehicles of each seed checked) [computed]. Netfix adds no demand.

**The evidence for a part:**

- **All three T.H.52 collisions are in netfix,** none in `_dc_cal` or `_dc_cal_sf`.
- **T1 is close to a paired observation.**
  - In `_dc_cal`, at the same seed, the same two vehicles reach T.H.52 almost identically: the exiter enters the
    corridor at 433.0 s (433.5 s in netfix), and the entrant appears on the section at 568.0 s in both.
  - There is no collision there. This is free flow inside the first 10 minutes, so the two runs have had little
    time to diverge.
  - The one systematic difference on the exiter's path is the lane mapping it crossed at 9.30–9.57 km, about
    40–55 s earlier at its 22 m/s mean. What decided T1 is most likely the lane the exiter arrived in
    [inference].
- **T.H.52's completed forced changes rise under netfix** by +52 per run [+9, +95], higher on 15 of 20 seeds.
  T.H.52's other counters do not move resolvably [computed, paired].

**The evidence against:**

- **The rate is not resolved:** 3 of 20 runs against 0 of 20, Fisher p = 0.23.
- **The probe saw none.** The netfix probe's 8 slices (4 per network at the calibrated drivers) had zero
  collisions in both networks [artifact].
- **The lane distribution barely changes.** Its simulated S790 lane shares, 300 m upstream of the section, move
  by at most 2.5 pp between the networks [artifact].

**Verdict.**

- **Plausibly involved, through the lanes in which exiters and through traffic reach the section, not shown.**
- **The map change is not a defect at 51388891.** The defect it corrects is real (an option lane compiled as a
  lane drop), and it stays justified on that ground.
- **What it may do is expose the weave's opposing-entry weakness more often.** That weakness is the thing to
  fix; the map correction is not the thing to revert.

## 8. Known or new: summary

| mechanism on record | what it is | R1–R3 (Ruth St, lane 0) | T1–T3 (T.H.52, lanes 1–2) |
|---|---|---|---|
| WP-93 / `force_guard` | scripted merge's forced change (mode 256) at an added lane's end, in front of a fast follower | no: no scripted merge, no lane change; both scripted ramps (guard on) recorded none in 60 runs | no: not a scripted merge, not in the forced zone |
| the slice's 999007700_0 collision (2026-09-23) | weave forced change into lane 0 in front of a fast follower; closed by the speed-aware forced guard | no: nobody changed into lane 0 | no |
| WP-92 opposing entries | two vehicles enter one lane from both sides in one step; the rear one's mode-256 change lands at any gap | no | **very probably** [hypothesis]; the weave's guard was deleted 2026-10-06 |
| WP-95 / WP-96 (AV handback, close leader) | a TraCI speed target caps braking at `decel`; a leader inside `minGap` read as none | **very probably the same SUMO behaviour, on the weave's targets** [hypothesis]; no AVs here, and the fix covers AVs only | possibly a contributor (the exiter may be under an easing target), not needed by the reading |
| new | — | the weave's command path never examined for this; a recurring, untraced family (VM AG, phase 1) | — |

## 9. Fix direction (proposal; nothing implemented)

*Implemented as amendment W2 and judged against criteria registered beforehand on 2026-10-07 (§13, §14). Both
mechanisms reproduce on stress fixtures, and the switches remove them. G6 (no new lock) fails, so the switches
stay off pending the owner and the corridor round.*

**Order: confirm the mechanism first (§10), then fix behind keys on the fixtures, then the corridor batteries.**
Every key starts off and byte-identical, per the project's rules. Because zero collisions is pass/fail, the owner
may later decide to turn a fix on by default, as WP-98 did for the AV path.

**F-R (Ruth St, the speed targets).** Give the weave's one-step targets the AV path's two fixes:

1. **Handback.** Before `mod.vehicle.slowDown(fid, v_new, 0.0)` in `_weave_step`, skip the target for the step
   when `_handback_needed(mod, fid, v, decel, step_s)` holds. That is the existing AV helper: the vehicle's own
   follow speed behind its real leader (`vehicle.getFollowSpeed`) is below what the capped command can reach.
   Count the skips in `meta.json` per section.
2. **Close leader.** In `_weave_command`, read a negative reported gap as a leader at that gap (bumper gap =
   reported gap + `minGap`, IDM → −∞, no target) instead of a free road. This is the weave's counterpart of
   `observe_close_leader`.

**Expected cost [estimate]:** small. WP-95's handback removed all 311 AV collisions "with the controller effect
unchanged". The weave's cooperation is a soft ceiling, and withdrawing it in an emergency step only lets the
vehicle brake harder.

**Complementary, not a fix:** W1b (docs/WEAVE_LOSS_DIAGNOSIS.md §10) dissolves the stranded-entrant queue that
R1's run ends in, and so shortens the exposure. Its corridor round (p9) should report collisions by section.

**F-T (T.H.52, the opposing entries).** Resolve opposing entries before a weave change executes. Two candidates:

- **Port the resolution into the weave.** `merge_model.resolve_opposing` (always on in `merge: measured`):
  every candidate entry into a lane in one step is decided together, and the rear one waits a step. An undriven
  vehicle's own model-driven change can be vetoed for one step, as `_weave_opposing_restore` already supports.
  - The record warns that WP-92's form cost a T.H.52 capacity give-up and VM AF saw one locked seed.
  - Any such key must be judged with `validation.locks` (the `no_locks` criterion now in every battery) as well
    as collisions.
- **Run both I-94 weaves on `merge: measured`.** It has recorded zero collisions:
  - in its fixture grid;
  - in gate B (I-94 slice, 4 seeds, level S790 flow);
  - in gate C (the phase-1 colliding pairs, four hours).

  It is not a default because it did not lift I-24's capacity (docs/MERGE_MODEL.md A4, gate C). It changes the
  model under the corridor's whole validation record, so it is an owner decision.

**Recording, additive and hash-neutral.** Store SUMO's `colliderSpeed` and `victimSpeed` with each logged
collision in `meta.json`. They are on the collision object the runner already reads. This alone would have
separated a ≈ 2.7 m/s contact from a fast one here.

## 10. What recording would settle it, and the cheapest test

**The archived files cannot pin either mechanism.** Both readings turn on per-step facts:

- the lane each vehicle was in during the step before the contact (T: did both enter the lane in the same
  step?);
- the collider's deceleration in the last steps (R: is it pinned at its `decel`?);
- which weave command was in force.

The p8 archive kept no trajectories and the runner logs neither speeds nor commands. Re-running the collision
seeds reproduces them exactly, because SUMO is deterministic per version and seed (CLAUDE.md §9). The trajectories
record every 0.5-s step (`output_hz` 2 at step 0.5 s) with t, x, lane, v and a for every corridor vehicle.

**The cheapest decisive test is a cloud re-run of the six collision replicates plus one paired control, with
trajectories kept.**

- **Why cloud.** A corridor run is not allowed on the laptop.
- **Why not the battery's `--keep-trajectories`.** It would need `--replicates 10` in two batteries (20 runs) to
  reach seeds 1, 4, 7, 8 and 9 of the spawned list.
- **This form instead.** Seven `run_micro` calls in one wave, then a window reader on the VM. Its specification
  as proposed (implemented 2026-10-07, below, with the differences noted there):
  1. For each run directory under `runs/p8c/`:
     - print `config_hash` (must be `beaaa710e6b3` or `182e3ec2f500`), `n_collisions` and the `collisions` list;
     - compare them with §3's events: `t`, `collider`, `victim`, `lane`, `pos_m` to 1e-6;
     - the control must log no collision before 600 s.
  2. For each event, read `trajectories.parquet` filtered to `t ∈ [t_c − 30, t_c + 2]` (columns t, veh_id, x,
     lane, v, a). Print every step of the collider and the victim: lane, x, v, a, bumper gap. List every entry
     into the event's lane by any vehicle within 150 m behind and 60 m ahead of the contact, with its step and
     side.
  3. Print the two vehicles' vType attributes from `net/demand.rou.xml`: `decel`, `emergencyDecel`, `minGap`,
     `tau`, `accel`, `length`.
  4. For the control (`_dc_cal`, seed 165503670820534583), print the same window at 574.5 s for v19448 and v27416:
     each one's lane on arriving at the section, against T1.

**Implemented 2026-10-07: stage `p8c_i94_cal_collisions`** in `scripts/gcp/pipeline_i24.sh`, after p8, opt-in.
Not launched and not committed: the launcher runs only stages of a pushed commit, so the launch is the owner's call.
**Superseded (2026-10-07):** committed and launched the same day; results in §15.

- **The runs: `scripts/run_pairs.py`.** One call runs the seven (scenario, seed) pairs in one spawn pool of at most
  7 processes (one wave). Each runs exactly as the battery runs a replicate (`microsim.runner._replicate_worker`)
  into `runs/p8c/<arm>/<config hash>/<seed>/`, with every file kept, trajectories included.
  - **Hash guard.** Both scenarios are loaded and hashed first, and nothing runs unless they hash as p8's runs did
    (`--expect-hash dc_cal=beaaa710e6b3 --expect-hash dc_cal_netfix=182e3ec2f500`). Checked on the committed
    scenarios today (load and hash only).
  - A failed run does not stop the others; `runs/p8c/PAIRS.json` records each run's status.

  | arm (scenario) | seeds |
  |---|---|
  | `dc_cal` (`scenarios/mndot_i94_wb_stpaul_weave_dc_cal.yaml`, `beaaa710e6b3`) | 134183728835869882 (R1), 6134032994440706937 (R2), 165503670820534583 (control) |
  | `dc_cal_netfix` (`..._dc_cal_netfix.yaml`, `182e3ec2f500`) | 134183728835869882 (R3), 165503670820534583 (T1), 677105600768189526 (T2), 6953598295321596746 (T3) |

- **The reader: `scripts/i94_collision_trace.py`** (in place of a `.py.txt` harness), on every run under `runs/p8c`.
  It writes `artifacts/i94_cal_collisions_trace.json` and each run's `collision_slice.parquet` (the window's rows).
  1. **Reproduction first.** Each run's collision log is compared with §3's event: the same collider and victim,
     the same edge and lane, `pos_m` within 1e-6 m (the full-precision positions of the committed battery
     artifacts' `collisions.locations`), `t` within one step, the expected config hash, and no other collision.
     - **Stricter than proposed for the control:** it must log no collision at all, as in p8, not only none before
       600 s.
     - **The trajectory is checked too:** the rear car is in the logged lane at `t`, at the section's start plus
       `pos_m`.
     - **When a run does not reproduce,** the artifact's top-level `reproduced` is false, its `reproduction_note`
       names the run and says to relaunch from `31c04c4`, and the reader exits 3, which fails the stage. The run is
       still traced, labelled `reproduced: false`.
  2. **The window:** the 15 s before contact and 1 s after (the proposal's 30 s is `--before-s 30`). Per step: lane,
     x, position on the section, v, a and bumper gaps of the rear car, the front car and the vehicle ahead of the
     front car.
  3. **Braking:** the rear car's per-step deceleration against its vType's `decel` (to 1e-3 m/s²) and its
     `emergencyDecel`.
     - The vTypes set no `emergencyDecel`, so it is SUMO's passenger default max(decel, 9).
     - Over the last 6 steps: whether the rear car was pinned at `decel` while its gap closed (§4.2's signature) or
       braked beyond it.
  4. **Entries:** the step and side from which each car entered the collision lane, over the last 60 s.
     - **The kinds:** a lane change from the right or the left; a renumbering where the section's lanes start (not a
       lane change); or the arrival of a ramp vehicle on the axis.
     - **`opposing_entries`:** both cars changed into the lane in the same step from opposite sides (§5.2).
     - **Also listed:** every entry into the lane within 150 m behind and 60 m ahead, and each car's lane on reaching
       the section, compared between T1 and its control (pair key `T1`).
  5. **Commands:** whether a weave speed target was issued to the rear car for the contact step, when a run logs
     them (`weave_commands.parquet` or `meta.json["weave_commands"]`). The runner does not, so this block reads
     `logged: false` until the optional recorder below exists.
  6. **The vTypes** (`decel`, `emergencyDecel`, `minGap`, `tau`, `accel`, `length`), from `net/demand.rou.xml`.
- **Archive.** `make_archive` ships `runs/p8c/PAIRS.json` and each run's `meta.json`, `vehicles.parquet` and
  `collision_slice.parquet`. It never ships `trajectories.parquet` or `net/` (the vTypes are in the artifact). The
  ingest copies the artifact (on its allow-list) and `runs/p8c`.
- **Tests (no corridor simulation).**
  - `tests/test_scripts/test_i94_collision_trace.py`: a planted rear-end braking at exactly `decel`, a planted
    opposing entry and its one-step-earlier counterpart, reproduction failures, a logged weave target.
  - `test_run_pairs.py`: the hash guard on the committed scenarios, and two 20-s ring runs through the spawn pool.
  - `test_p8c_stage.py`: the stage's two calls, the archive's contents and the ingest.
- **Launch:**

  ```sh
  scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p8c --machine n2-standard-16 --bucket gs://<bucket>/p8c \
    --self-delete --via-bucket --data-set none --cap-min 75 --pipeline-args '--stages "p8c_i94_cal_collisions"'
  ```

- **Expected VM time and cost [estimate].**
  - **The runs, about 30 min:** one wave of seven four-hour runs. p8's waves of ten took 29–31 min on this machine
    type, scoring included.
  - **The reader, at most about 5 min:** it reads only the trajectory row groups of each event's last minute.
  - **Boot and setup through the bucket, 10–15 min.**
  - **Total: about 45–55 VM minutes at about $0.78/h, so about $0.60–0.75.** `--cap-min 75` bounds it at about
    $1.00. Seven kept trajectories, a few GB each, fit the 120 GB disk.
- **Reproduction.**
  - **The commit.** Stage p8's log does not record its commit. The last commit before the VM started (09:17 UTC) is
    `31c04c4` [inference].
  - **Since then.** `decdccc` adds a default-off, hash-neutral weave input; `268d70c` and `b066935` change scoring
    and add W1b off. Each later runner change was recorded byte-identical with its keys off
    (docs/PERFORMANCE_2026-10-07.md; the W1 and W1b records in docs/WEAVE_LOSS_DIAGNOSIS.md §8.2 and §10.8; the
    crossing share's in docs/TH52_CROSSING_SHARE.md §10.2). So HEAD should reproduce p8 [inference].
  - **The stage checks rather than assumes.** If any re-run does not log its §3 event exactly, the artifact says so
    and the run must be relaunched from `31c04c4` before anything in it is read.

**Optional, more decisive: a command recorder** (new harness, like WP-93's `wp93/h93.py`, session record):

- **What it is.** It wraps `libsumo.vehicle.slowDown`, `changeLane`, `setLaneChangeMode` and `changeTarget`, and
  the per-step `simulation.getCollisions()` (with `colliderSpeed` / `victimSpeed`), for the 12 vehicles of §3 in
  the 60 s before their contact.
- **What it adds.** It names the weave rule behind each command: easing, cooperation, anticipation, or an
  accepted or forced change.
- **What it needs.** It must first be shown on a fixture to leave trajectories byte-identical, then used in place
  of the bare `run_micro` call. It adds no measurable wall time.

**Confirmed or refuted:**

| | confirmed if | refuted if |
|---|---|---|
| R1–R3 (§4.2) | in the 2–6 steps before contact the collider's `a` equals −`decel` of its vType (to 1e-3 m/s²) while its gap closes to contact, and, with the recorder, a weave `slowDown` was issued to it for those steps (expect the close-leader branch once inside `minGap`) | the collider brakes harder than `decel` (towards `emergencyDecel`) and still hits, or no weave target acted: then the cause is the approach (the dead-end stop, the junction arrival) and F-R would not help |
| T1–T3 (§5.2) | the collider's first sample in the collision lane is in the same 0.5-s step as the victim's, the collider entering from the left and the victim from the right | the collider was in the lane one or more steps before the victim entered: then the victim's entry landed in front of it, and the acceptance (T1) or LC2013 (T2, T3) must be traced instead |
| netfix (T1 control) | the exiter arrives at the section in a different lane in the two arms, and in netfix in the lane the entrant enters | same lane in both: then the arms differ by chaos only |

**Cost [estimate]:**

- **Simulation, about 30 min.** The p8 batteries ran two waves of 10 four-hour replicates on this machine type
  in 3,527 and 3,708 s, scoring included, so about 29–31 min per wave. Seven processes fit in its 64 GB, which
  p8 sized for ten.
- **Readers, about 5 min.** Time-filtered reads of seven trajectories.
- **Boot and setup through the bucket, 10–15 min.**
- **Total: about 45–55 min billed at about $0.78/h, so about $0.60–0.75.** `--cap-min 75` bounds it at about
  $1.00. Kept trajectories, a few GB each, fit the 120 GB disk.

**A fixture is not the cheaper first step here.**

- **Why.** The grid's Ruth St and T.H.52 fixtures (`scripts/merge_model_selfcheck.py grid --model weave`) have
  recorded zero collisions on record, and none of them forms the end-to-end standing auxiliary-lane queue R1
  sits in. A null there would not refute anything.
- **Where fixtures do belong.** Testing F-R and F-T once the mechanism is shown. Two fixtures fit:
  - for R: `tests/fixtures/weave_ruth.osm` at the corridor's Ruth St flows, with a 3 m/s downstream speed
    schedule from t = 300 s (WP-93's "b3" regime), seeds 3–22;
  - for T: `weave_th52_corridor.osm` at the corridor fleet, seeds 3–22.

  Each run takes 5–15 s on the laptop, one at a time, so about 10 min per arm.

## 11. Limits

- **No per-step data.** Every mechanism statement above is an inference from roles, positions, timings and the
  runner's code, not an observation. The archive kept no trajectories, no command record and no collision
  speeds.
- **Approximate kinematics.** Speeds are means over seconds or kilometres, and victims' positions assume 5 m
  vehicles. The colliders' speeds at contact are unknown.
- **One instant.** The end-of-run queue table (§4.1) is a snapshot at 14,400 s, not a time series.
- **Rates are not resolved.** Three Ruth St and three T.H.52 collision runs are not resolved against zero in step
  3 (p = 0.54) or in `_dc_cal` (p = 0.23). The comparison in §6 concerns operating state, not collision rate.
- **Only the run-end lock reader was used,** because `edges.parquet` was not archived.
- **Step 3's files** come from stage 23's archive, as in docs/I94_COLLAPSE_DIAGNOSIS.md, and are not committed.

## 12. Reproduce (reading only)

- **Files.** Extract `runs/mndot_i94_wb_stpaul_weave_xlsfg_dc_cal*/baseline/*/*/{meta.json,vehicles.parquet}` from
  stage p8's `final.tgz`, and step 3's from stage 23's archive.
- **The events (§3):** `meta.json["collisions"]`, then `vehicles.parquet` rows of the collider and the victim.
  Section offsets come from `meta.json["ramps"][i]["attach_x_m"]`, indices 6 (Ruth St) and 14 (T.H.52).
- **Counters (§6, §7):**
  - `meta.json["weave_sections"]`, split by `exit_edge` (18208090 Ruth St, 18207598 T.H.52);
  - paired by seed over the 20 common seeds, mean difference with a two-sided 95 % t-interval (t = 2.093).
- **Travel times (§6):** `vehicles.parquet`, origin −1, arrived and not given up, grouped by `destination_ramp`
  (2, 7, −1) and `entry_t_s` half-hour, median of `last_t_s − entry_t_s`.
- **End-of-run queue (§4.1):** vehicles not arrived with `last_t_s` ≥ 14,399 s, `last_lane` 0 and `last_x_m`
  inside 5,180.87–5,316.64 m.
- **Plan identity (§7):** `route` and `depart_planned_s` equal per `veh_id` between the arms.
- **Locks:** `validation.locks.detect_run_locks(run_dir)` on each archived run.
- **Fisher tests:** `scipy.stats.fisher_exact`.

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

## 14. W2 implemented and evaluated (2026-10-07)

Amendment W2 (§13) was implemented as three opt-in switches and judged on the criteria of §13.5 exactly as
registered. Labels as in the rest of this note; **[run]** rows are in `artifacts/weave_collision_guards_2026-10-07/`.
All runs: macOS, eclipse-sumo 1.27.1, fixtures only, at most two SUMO processes at once.

### 14.0 In plain English

- **Both mechanisms now reproduce on fixtures.**
  - The stress sets of §13.3 produced four reference collisions: one Ruth St rear-end (R) and three T.H.52 opposing
    entries (T).
  - A command recorder replayed each run byte for byte and showed the mechanism step by step:
    - **R.** The rear vehicle's own model wanted to stop. A weave speed target, issued because a leader inside its
      `minGap` was read as a free road, held its braking at exactly its `decel`, 0.58 m/s².
    - **T.** An exiter and an entrant entered the same lane from opposite sides in the same step, and the exiter hit
      the entrant one step later at −9 m/s².
- **W2 removes all four.** With the three switches on, every one of those runs has zero collisions.
  - **R** goes with either the handback or the close-leader reading alone.
  - **T** goes with the opposing-entry resolution alone.
- **Off changes nothing.** With the switches unset or at 0, 37 golden and fixture runs are byte-identical to HEAD,
  and all 43 committed scenarios hash the same.
- **No measured cost on the operating fixtures.**
  - **Collisions.** Zero in 132 W2 runs.
  - **T.H.52 flow.** +22 veh/h [−20, +63].
  - **Given-up exits and hard brakes.** Inside their registered bounds.
- **One registered criterion fails (G6, no new lock), so the keys stay off.**
  - **The lock.** In one grid run, `th52_upstream_fleet` at seed 3, W2 ends in the known gore lock that the
    reference avoids at that seed. No single switch does this, and W1b + W2 does not lock there.
  - **The flags.** In the X-R2 stress set, three W2 runs carry `run_summary`'s speed flag where their reference
    does not. That set's 1 m/s boundary holds every run at the flag's threshold, and the reference flags more runs
    (15) than W2 (10).
  - Nothing is re-thresholded.
- **Next.** Adoption, and the corridor round (§14.8: W1b + W2 against W1b on `_dc_cal`, about $1.7–1.9), are the
  owner's decision.

### 14.1 What was implemented

- **Config** (`flowstate_core.config`).
  - `WEAVE_W2_SWITCHES` = {`weave_handback`, `weave_close_leader`, `weave_resolve_opposing`} is added to
    `WEAVE_OPTIONAL_KEYS`. A value other than 0 or 1 is refused.
  - Not in `WEAVE_DEFAULTS`, so `tests/golden/config_defaults.json` is unchanged.
  - Dict keys of `weave_params` are present in a dump only when set, so no serializer rule is needed (the
    `ramp_to_ramp_share` exclusion of 6670388 is for a model field).
- **Runner** (`microsim.runner`):
  - `_weave_switch`;
  - `_weave_handback_needed`: `_handback_needed` with `_command_decel` under `ws["cf_model"]`, cached per vehicle;
  - `_weave_command(…, close_leader=False) -> bool`, which returns whether the close-leader reading withheld a
    target;
  - `_weave_resolve_and_execute`: `merge_model.resolve_opposing` unchanged, with the `open`/`driven`/`model`/`held`
    states of §13.2. An executed request is recorded as `opp_req` for the next step's `open` reading;
  - `_weave_step`: the restore of last step's vetoes at its start, the collection of requests, and the handback in
    the final target loop;
  - `_weave_meta`: the counters, each only while its switch is on.

  With every switch off, the per-step code path is the old one. The new branches are taken only when a switch is
  on.
- **Script.** `scripts/merge_model_selfcheck.py` `grid` zone rows carry the counters when present.
- **Tests** (`TestWeaveCollisionGuards`, `tests/test_microsim/test_microsim_merge_managed_meter.py`).
  - **Fake harness:**
    - the handback withholds a target whose model must brake harder, keeps one it can follow, and uses the IDM
      bound;
    - the close-leader reading at −0.5 m and at an overlap, and counted through a step;
    - opposing runner requests (the rear one waits; a due forced change goes first), a veto restored the next step,
      and an open request from the last step winning;
    - the schema.
  - **One SUMO test** on the T.H.52 section with the calibrated drivers, seed 4:
    - the three switches at 0 are byte-identical to unset, with no counter in `meta.json`;
    - all three on run with every counter.
  - The W1b schema test now asks that its two keys be a subset of `WEAVE_OPTIONAL_KEYS`.
- **Docs.** docs/CONTRACTS.md, "Weave collision guards, amendment W2".
- **Harness** (`harness/`):
  - `run_sets.sh`: the arms, two at a time;
  - `repro.py`: the stress sets;
  - `post.py`: per run, collisions and their mechanism, locks, digests and counters;
  - `eval.py`: the criteria → `criteria.json`;
  - `recorder.py`: the command recorder;
  - `diag.py` and `lockprobe.sh`: the diagnostics → `diagnostics.json`;
  - `corridor_w2.py`: the corridor readout;
  - `stage_p10_proposed.sh.txt`: the proposed stage.

### 14.2 Off is byte-identical (G0) [run]

**Method.** As W1b's §10.8 (docs/WEAVE_LOSS_DIAGNOSIS.md):

- **The trees.** Two `git archive HEAD` trees at `6670388`, the second with only `config.py` and `runner.py` copied
  in.
- **The runs.** W1's `harness/cmp.py` was run in each tree through `harness/py.sh`, each tree's packages first on
  `sys.path`, with the case configs from the HEAD tree.
- **The hashes.** W1's `harness/hashes.py` hashed every committed scenario.

**Result** (`identity_head.json`, `identity_change.json`, `scenario_hashes_*.json`):

- **37 of 37 cases identical:** the 12 micro goldens; the T.H.52, Ruth St, McKnight Rd, T.H.61 and golden weave
  fixtures; the T.H.52 section with the calibrated drivers at seeds 3–5; the measured model.
  - That is 148 Parquet files by sha256, plus `meta.json` without wall time, the metrics and the config hash.
- **Against W1b's record of the same 37 cases** (taken at `84a272e`): every Parquet file is identical too. Config
  hashes differ only through the fixtures' absolute OSM paths, as W1b recorded.
- **All 43 committed scenarios hash the same in both trees, and `WEAVE_DEFAULTS` is unchanged.**
  - `_dc_cal` still hashes `beaaa710e6b3`.
  - No file under `tests/golden/` changed.
- **The switches at 0** write byte-identical outputs to unset: the SUMO test of §14.1.
- **The reference arms** (from the current tree) reproduce W1b's committed reference rows field for field, config
  hash and wall time aside: S1 340 fields, S2 407, S3a 660, S3b 165, no difference.
- **Tests.** `pytest -m "not slow" tests/test_microsim tests/test_flowstate_core`: 778 passed, 13 xfailed,
  2 xpassed (all pre-existing, non-strict marks). `ruff check`, `ruff format --check` and `mypy
  packages/flowstate_core` are clean.

### 14.3 Did a fixture reproduce a collision? Yes, both mechanisms [run]

S1, S2, S3a and S3b recorded no collision in either arm (as every fixture on record). Three of the four stress sets
did, in the reference arm only:

| set, fixture, seed | t [s] | lane, position | collider (rear) | victim (front) | mechanism (§13.4) | W2 run |
|---|---|---|---|---|---|---|
| X-R1 `ruth_entr` 9 | 1,256.5 | 102_0 (auxiliary lane), 60.8 m | v02816, Ruth St entrant, 1.84 m/s, vType `decel` 0.579 m/s² | v02815, the entrant before it, 0.83 m/s | **R**: both in lane 0 for the last 5 s | 0 collisions |
| X-T1 `th52_corridor` 7 | 493.5 | 102_1, 29.0 m | v00422, exiter, from lane 2 at 493.0, 14.4 m/s | v01303, entrant, from lane 0 at 493.0, 6.3 m/s | **T** | 0 |
| X-T1 `th52_corridor` 13 | 819.5 | 102_1, 45.9 m | v00752, exiter, from lane 2 at 819.0, 17.1 m/s | v01380, entrant, from lane 0 at 819.0, 11.8 m/s | **T** | 0 |
| X-T2 `th52_corridor_demand` 12 | 156.0 | 102_1, 69.0 m | v00161, exiter, from lane 2 at 155.5, 15.2 m/s | v01685, entrant, from lane 0 at 155.5, 8.4 m/s | **T** | 0 |

X-R2 (Ruth St at the exit peak behind a 1 m/s boundary) recorded none.

**The command recorder** (`harness/recorder.py`, `recorder_*.json`).

- **How it works.** Each run was replayed with libsumo's `vehicle.slowDown` and `vehicle.getLeader` wrapped to log
  the two vehicles' weave targets and leader readings. At each target it also evaluated W2's handback test (one
  extra `getLeader` and `getFollowSpeed`).
- **The replays change nothing.** All four reproduce the stored runs' Parquet files byte for byte. So the logging,
  and the handback's own queries, do not perturb a run.

**What the recorder shows:**

- **R, X-R1 seed 9.** The weave issued v02816 62 targets over the run. At 1,254.5 s and again at 1,256.0 s:
  - **The leader was inside its `minGap`.** The reported gap was −0.87 m, then −2.53 m (bumper gap 0.72 m), so
    `_weave_command` read the road as free and recorded a target.
  - **Its own model wanted to stop.** Its follow speed was 0.0.
  - **The handback test was true.**
  - **In each following step it braked at exactly −0.579 m/s²**, its `decel`, the WP-95 cap. Contact came in the
    second of these steps, at 1.84 m/s, 0.21 m bumper to bumper on the samples.
  - In the two steps between, with no target, its own model accelerated (+0.26, +0.51 m/s²) towards a leader
    1.75 m ahead.

  This is the confirming signature §10 asked for ("decelerates at exactly its vType's decel … with a weave target
  in force"), with both defects of §4.2 acting together.
- **T, X-T1 seeds 7 and 13, X-T2 seed 12.**
  - **The same step.** The exiter's weave change from lane 2 and the entrant's from lane 0 land in lane 1 in the
    same step.
  - **Contact.** It comes one step later, with the exiter braking at −9.0 / −9.0 / −8.9 m/s².
  - **Before the change.** In seeds 7 and 12 the exiter was under easing targets, braking at exactly its `decel`.
    The handback test was false there: its model needed no more.
  - **Seed 13.** The exiter changed again, into lane 0, in the contact step itself.
  - This is §5.2's reading of T1–T3, observed.

**What the reproduction does not show:** how often either happens on the corridor. The stress sets are built beyond
the corridor's operating range (a 2 m/s boundary, every entrant crossing). One R and three T collisions in 80
reference runs show that the mechanisms exist in the model, not their rate.

### 14.4 Results by set [run]

Reference → W2; totals over each set's runs (`criteria.json`).

| set (runs per arm) | collisions | −9 m/s² vehicle-steps | given-up exits | departed | handback skips | close-leader withheld | opposing deferred (vetoes) |
|---|---|---|---|---|---|---|---|
| S1 T.H.52 section (20) | 0 → 0 | 2 → 0 | 66 → 75 | 31,652 → 31,689 | 304 | 10 | 1,002 (967) |
| S2 grid, fixture fleets (37) | 0 → 0 | 20 → 18 | 78 → 80 | 48,081 → 47,965 | 158 | 20 | 1,634 (1,534) |
| S3a Ruth St, calibrated drivers (60) | 0 → 0 | 17 → 9 | 375 → 402 | 69,752 → 70,067 | 718 | 43 | 303 (299) |
| S3b other weaves, calibrated drivers (15) | 0 → 0 | 0 → 0 | 70 → 62 | 23,745 → 23,743 | 233 | 9 | 1,207 (1,137) |
| X-R1 Ruth St queued (20) | **1 R → 0** | 0 → 0 | 16 → 11 | 35,540 → 35,604 | 889 | 155 | 3,528 (3,528) |
| X-R2 Ruth St, 1 m/s (20) | 0 → 0 | 1 → 0 | 142 → 124 | 28,659 → 28,676 | 602 | 1,895 | 1,242 (1,204) |
| X-T1 T.H.52, share 0 (20) | **2 T → 0** | 6 → 1 | 128 → 113 | 29,957 → 30,230 | 433 | 17 | 2,487 (2,300) |
| X-T2 T.H.52 at corridor demand, share 0 (20) | **1 T → 0** | 2 → 0 | 127 → 144 | 32,971 → 32,721 | 371 | 19 | 2,060 (1,942) |

**S1, paired** (W2 − reference, 95 % t-interval, 19 df):

- **Exit-end flow.** +21.8 veh/h [−19.8, +63.5]: 4,360.8 → 4,382.7.
- **Given-up exits per run.** +0.45 [−0.60, +1.50].

**Guard activity.** All three switches act in every weave set. Every W2 run of a weave fixture had at least one
counter above zero. The only W2 runs with no activity are the four grid runs without a weave (McKnight Rd ×3,
the scripted merge golden). So diagnostic D1 has no informative case on these sets. The lock probe (14.6) supplies
two: a switch that never fires leaves its run byte-identical.

### 14.5 Verdict, criterion by criterion (as pre-registered in §13.5)

| # | criterion | result | verdict |
|---|---|---|---|
| G0 | off byte-identical; scenario hashes, `WEAVE_DEFAULTS`, goldens unchanged; 0 = unset | 37/37 cases; 43/43 scenarios; unchanged; identical | **pass** |
| G1 | zero collisions in W2 on S1, S2, S3a, S3b | 0, 0, 0, 0 | **pass** |
| G2 | every reproducing run's W2 twin has zero collisions | testable: 4 reproducing runs (1 R, 3 T), each 0 under W2 | **pass** |
| G3 | S1 exit-end flow, paired lower bound > −50 veh/h | +21.8 [−19.8, +63.5] | **pass** |
| G4 | (a) S1 given-up exits, paired upper bound ≤ +1.5 per run; (b) S2 and S3 totals within the band | (a) +0.45 [−0.60, **+1.496**]; (b) S2 80 ≤ 105.0 (ref 78), S3 464 ≤ 506.7 (ref 445) | **pass** (a: with 0.004 to spare) |
| G5 | hard brakes per set within the band | S1 0 ≤ 8.0 (ref 2); S2 18 ≤ 34.6 (20); S3 9 ≤ 30.7 (17) | **pass** |
| G6 | no W2 run with a lock or `run_summary` flag its reference lacks (all sets) | a new lock and flag: S2 `th52_upstream_fleet` s3; new flags: X-R2 `ruth_exit` s5, s19, s22 | **fail** |
| G7 | stress sets: no W2 collision of R or T, W2 total ≤ reference total | 0 against 4 | **pass** |

**Outcome.** G6 fails. Under §13.5 the keys stay off, the failure is reported here, and nothing is re-thresholded.

**What the G6 failures are** (diagnosed below, after the verdict):

1. **S2 `th52_upstream_fleet` seed 3: the known gore lock.**
   - **The state.** The lock of docs/I94_COLLAPSE_DIAGNOSIS.md:
     - a through-bound entrant stands at the auxiliary lane's front at the gore from 727 s;
     - an exiter stands at lane 1's front from 736.5 s;
     - both until the run ends at 1,200 s.
   - **The cost.** 30 controlled vehicles are unfinished, and the run departs 1,462 of 2,094 vehicles (reference
     1,635).
   - **What causes it (§14.6).**
     - It is not a new kind of lock, and no single switch produces it at this seed.
     - All three together send this run's history into the state W1b exists to release.
     - W1b + W2 does not lock there.
2. **X-R2 seeds 5, 19, 22: `run_summary`'s speed flag.**
   - **The flag.** It fires when a zone minute averages below 0.5 m/s.
   - **This set.** X-R2's 1 m/s boundary holds the section at about that speed in every run:
     - the lowest zone minute is 0.1–0.7 m/s in both arms;
     - the reference flags 15 of 20 seeds and W2 10 of 20;
     - W2 flags three that the reference does not, and the reference eight that W2 does not;
     - every run of both arms has an auxiliary-lane stand of ≥ 120 s, so by §13.4's definition every run "locks".
   - **The reading.** The set cannot tell a model lock from its boundary's standstill. Applying G6 to the stress
     sets was a registration error, and it stands as registered.

### 14.6 Diagnostic arms (registered in §13.3; reported, never criteria) [run]

`diagnostics.json`; arms with one switch only (hb, cl, op), on S1 and the three reproducing stress sets.

| set | reference | handback alone | close leader alone | opposing resolution alone | all three (W2) |
|---|---|---|---|---|---|
| S1: collisions; paired flow [veh/h] | 0 | 0; +20.5 [−19.6, +60.6] | 0; +3.5 [−8.0, +15.0] | 0; +8.7 [−11.7, +29.1] | 0; +21.8 [−19.8, +63.5] |
| X-R1: collisions | 1 R (s9) | 0 | 0 | **1 R (s9)** | 0 |
| X-T1: collisions | 2 T (s7, s13) | 0 | **3 T** (s7, s13, s17) | 0 | 0 |
| X-T2: collisions | 1 T (s12) | 0 | **1 T (s12)** | 0 | 0 |

- **R.** The R collision goes with either F-R switch alone and stays with the opposing resolution alone, as the
  recorder's reading predicts.
- **T.** The T collisions go with the opposing resolution alone and stay with the close-leader reading alone. Under
  the close-leader reading alone a third T collision appears (X-T1 s17).
- **The handback alone also shows no T collision.** The recorder found no target in force at any T contact step,
  so this is most likely run divergence, not a T fix. Per-seed attribution under chaos is weak; the mechanisms rest
  on the recorder, not on these counts.

**The lock run, under each arm** (`lockprobe_*.json`; `harness/lockprobe.sh`; S2 `th52_upstream_fleet` seed 3):

| arm | departed (of 2,094) | lock (§13.4) | controlled vehicles unfinished | note |
|---|---|---|---|---|
| reference | 1,635 | none | 0 | |
| handback alone | 1,619 | none | 0 | 10 skips |
| close leader alone | 1,635 | none | 0 | never fired; **byte-identical to the reference** |
| opposing resolution alone | 1,676 | none | 6 | 58 deferred |
| W2 (all three) | 1,462 | **entrant at lane 0's front 727–1,200 s, exiter at lane 1's front 736.5–1,200 s** | 30 | reproduces the S2 run exactly |
| W1b alone | 1,635 | none | 0 | never fired; **byte-identical to the reference** |
| W1b + W2 | 1,589 | none | 4 | one W1b release |

**W1b + W2 against W1b alone on S3a** (60 Ruth St runs, calibrated drivers):

- **Safety.** No collision and no lock in either arm. Both release the seed-15 lock that the reference and W2 alone
  keep. W2 alone does not release it: its auxiliary-lane front stands 435 s there, against 971.5 s in the reference.
- **Throughput.** Departures are equal in total (70,093), paired per run 0.0 [−0.07, +0.07].
- **Given-up exits.** 367 → 391.
- **Hard brakes.** 16 → 9.
- **W1b releases.** 5 → 4.

**Two more checks that a switch is inert until it fires:**

- **The lock probe.** Each of its two never-firing arms is byte-identical to the reference.
- **The recorder replays.** They show that the handback's extra queries leave a run unchanged.

### 14.7 Changes to the readers during the evaluation

`harness/post.py`'s T test first required the collider to be still in lane k at the contact's sample. The
registered definition (§13.4) asks only for the last entries into lane k *before* t.

- **What it changed.** X-T1 seed 13's exiter left lane 1 in the contact step itself, so the first version labelled
  its collision "other".
- **The fix.** Made at 09:05 CDT, after X-T1 had run and before X-T2's runs were read. It reads entries from the
  samples strictly before the contact's.
- **Re-run.** X-T1 was re-run in full: both arms, 20 runs each, byte-identical to the first pass. With the fix, seed
  13 reads T.
- **No other change.** No criterion, threshold or set was changed. The registration's R and lock definitions are
  implemented as written.

### 14.8 The corridor round (cloud; written, not launched — ran 2026-10-07 as stage p10, results in §16)

**Status.** Under §13.5 the fixture failure of G6 keeps W2 off. The corridor round of §13.6 is run only on the
owner's decision.

**Why it is still worth running.**

- It pairs W1b + W2 against W1b. In that pairing the fixtures show no lock (§14.6), and W1b releases the gore lock
  that G6 caught.
- It is the only place where R and T can be measured at the corridor's own rate. `_dc_cal` recorded two R
  collisions in 20 four-hour runs.

**The arms and their scenarios.** Two copies of `_dc_cal` that differ only in their name and their two
`weave_params` lines. Their schema and hashes were checked locally; nothing was simulated:

- A: `mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b`, hash `0d26de2a5f01`;
- B: `…_dc_cal_w1b_w2`, hash `5080d84d4725`.

**The readout.** `harness/corridor_w2.py` applies CW1–CW5, reusing W1b's `corridor_w1b.py` front-row reader. It was
checked on the committed p9 batteries: it reproduces W1b's C1/C2 intervals (+7.2 [−9.6, +23.9] veh/h). Its meta
reading was checked on a synthetic battery.

**Superseded (2026-10-07):** "Not added to the script" below no longer holds: the stage is in
`scripts/gcp/pipeline_i24.sh` as `p10_i94_cal_w1b_w2` (opt-in, not in the default list); no run of it is recorded.

**Proposed stage text** for `scripts/gcp/pipeline_i24.sh`, after p8's block, since it calls `p8_one`. Not added to
the script; another session is editing it. Also in `harness/stage_p10_proposed.sh.txt`:

```sh
# p10 (proposed, docs/I94_CAL_COLLISIONS.md §13.6 and §14.8; opt-in; not in the default list). Amendment W2's corridor
#     round on the calibration-day I-94 inputs (scenarios/${MNDOT}_weave_dc_cal.yaml, hash beaaa710e6b3; stage p8's 20
#     seeds, spawned from seed 42): arm A with W1b on both weaves (${MNDOT}_weave_xlsfg_dc_cal_w1b) and arm B with
#     W1b + W2 (${MNDOT}_weave_xlsfg_dc_cal_w1b_w2), each a copy written here that differs from _dc_cal only in its name
#     and its two weave_params lines, each run through p8_one (battery against the calibration-day targets, baseline
#     gate, gated report); then CW1-CW5 from each replicate's meta.json and vehicles.parquet, never trajectories
#     (artifacts/weave_collision_guards_2026-10-07/harness/corridor_w2.py) -> artifacts/weave_w2_corridor.json. p8's
#     _dc_cal battery is reported beside as context, never paired. Needs no data set (--data-set none).
P10_W1B="${MNDOT}_weave_dc_cal_w1b"
P10_W2="${MNDOT}_weave_dc_cal_w1b_w2"
P10_KEYS_W1B="{exit_prepare: 1.0, entrant_giveup_m: 5.0, entrant_giveup_dwell_s: 60.0}"
P10_KEYS_W2="{exit_prepare: 1.0, entrant_giveup_m: 5.0, entrant_giveup_dwell_s: 60.0, weave_handback: 1.0, weave_close_leader: 1.0, weave_resolve_opposing: 1.0}"
p10_copy() {  # p10_copy <stem> <name suffix> <weave_params>: _dc_cal with only its name and weave_params changed
  local out="scenarios/$1.yaml"
  { echo "# ${MNDOT}_weave_xlsfg_$2: scenarios/${MNDOT}_weave_dc_cal.yaml (config hash beaaa710e6b3) with"; \
    echo "#   weave_params $3 on both weaving sections (docs/I94_CAL_COLLISIONS.md section 13)."; \
    echo "#   Written by scripts/gcp/pipeline_i24.sh stage p10_i94_cal_w1b_w2; changed, nothing else: name and the two"; \
    echo "#   weave_params lines. The source header applies otherwise; this file's config hash is recorded in its"; \
    echo "#   battery artifact (artifacts/validation_${MNDOT}_weave_xlsfg_$2.json, config_hash)."; \
    sed -e '/^#/d' \
        -e "s#^name: ${MNDOT}_weave_xlsfg_dc_cal\$#name: ${MNDOT}_weave_xlsfg_$2#" \
        -e "s#weave_params: {exit_prepare: 1.0}#weave_params: $3#" \
        "scenarios/${MNDOT}_weave_dc_cal.yaml"; } > "$out"
  [ "$(grep -cF "weave_params: $3" "$out")" -eq 2 ] && grep -qx "name: ${MNDOT}_weave_xlsfg_$2" "$out" \
    || { say "p10: $out is not the intended copy"; return 1; }
}
p10_steps() {
  local rc=0
  p10_copy "$P10_W1B" dc_cal_w1b "$P10_KEYS_W1B" || return 1
  p10_copy "$P10_W2" dc_cal_w1b_w2 "$P10_KEYS_W2" || return 1
  p8_one "$P10_W1B" || rc=1
  p8_one "$P10_W2" || rc=1
  $RUN artifacts/weave_collision_guards_2026-10-07/harness/corridor_w2.py \
      --a "artifacts/validation_${MNDOT}_weave_xlsfg_dc_cal_w1b.json" \
      --b "artifacts/validation_${MNDOT}_weave_xlsfg_dc_cal_w1b_w2.json" \
      --context "artifacts/validation_${MNDOT}_weave_xlsfg_dc_cal.json" \
      --out artifacts/weave_w2_corridor.json \
    || { say "p10: corridor readout failed"; rc=1; }
  return $rc
}
if echo " $STAGES " | grep -q " p10_i94_cal_w1b_w2 "; then
  stage p10_i94_cal_w1b_w2 p10_steps || say "p10_i94_cal_w1b_w2 failed; continuing"
fi
```

**Launch** (after the stage is committed and pushed: the owner's call):

```sh
scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p10 --machine n2-standard-16 --bucket gs://<bucket>/p10 \
  --self-delete --via-bucket --data-set none --cap-min 180 \
  --pipeline-args '--stages "p10_i94_cal_w1b_w2" --procs 10'
```

**Cost on n2-standard-16 [estimate].**

- **The batteries.** Two of 20 four-hour runs, each in two waves of 10 (`--procs 10` keeps ten in its 64 GB). p8's
  `_dc_cal` batteries took 3,527 and 3,708 s on this machine, scoring included.
- **Gates and reports.** Two baseline gates and two gated reports, about 2 min each.
- **Boot and setup** through the bucket: 10–15 min.
- **Total.** About 2.2–2.4 h billed at about $0.78/h, so **about $1.7–1.9**. `--cap-min 180` bounds it at about
  $2.35.

**Optional extension for T.** The same pair on `_dc_cal_netfix` (hash `182e3ec2f500`), where p8's three T.H.52
collisions were. That is two more batteries: about +2.0 h and +$1.6 on the same machine (`--cap-min 330` for both
pairs).

### 14.9 Limits

- **Fixtures, on macOS.** The stress sets are deliberately outside the corridor's operating range. They show that
  the mechanisms exist and that the switches remove them; they say nothing about corridor rates.
- **Four collisions.** One R and three T. The mechanism is shown step by step for each, but nothing here estimates
  a rate, and the X-R1 collision depends on a weak-braking draw (`decel` 0.58 m/s²) of the calibrated population.
- **G4 (a) passes with 0.004 exits per run to spare.** Given-up exits rise in S1 (66 → 75), S3 (445 → 464) and S2
  (78 → 80), all inside the registered bounds. It is the one cost that leans one way.
- **The vetoes are many.** About 1,000 per 20 S1 runs. A veto suspends an undriven vehicle's own lane changing for
  one step whenever it is a conflicting opponent ahead of a runner change. Whether those vehicles intended to change
  is not recorded.
- **What the handback covers.** It reads the leader constraint only, as the AV path does. A lane-end stop is not
  covered. The R collision here was a leader constraint.
- **The arrival-step change of an undriven entrant** (§13.2) is not resolved. None of the four T collisions was
  of that kind.
- **Single-switch attribution under chaos is weak.** The recorder carries the mechanism reading.

### 14.10 Reproduce

From the repository root, with `$W` a scratch directory and `A=artifacts/weave_collision_guards_2026-10-07`:

```sh
$A/harness/run_sets.sh $W $A s1 s2 s3a s3b xr1 xr2 xt1 xt2       # both arms of every set, then post.py
uv run --no-sync python $A/harness/eval.py $A                       # G0-G7, D1 -> $A/criteria.json
for k in hb:weave_handback cl:weave_close_leader op:weave_resolve_opposing; do
  ARM_NAME=${k%%:*} ARM_KEYS="--weave-set ${k##*:}=1" $A/harness/run_sets.sh $W $A/diag s1 xr1 xt1 xt2; done
ARM_NAME=w1b ARM_KEYS="--weave-set entrant_giveup_m=5 --weave-set entrant_giveup_dwell_s=60" \
  $A/harness/run_sets.sh $W $A/diag s3a
ARM_NAME=w1bw2 ARM_KEYS="--weave-set entrant_giveup_m=5 --weave-set entrant_giveup_dwell_s=60 \
  --weave-set weave_handback=1 --weave-set weave_close_leader=1 --weave-set weave_resolve_opposing=1" \
  $A/harness/run_sets.sh $W $A/diag s3a
$A/harness/lockprobe.sh $W $A
uv run --no-sync python $A/harness/diag.py $A/diag $A/diagnostics.json
uv run --no-sync python $A/harness/recorder.py xr1 9 v02816 v02815 1256.5 $A/xr1_ref_post.json $A/recorder_xr1_s9.json $W/rec
```

(`$A/diag` needs the reference rows of s1, xr1, xt1, xt2 and s3a copied in first.)

**Identity runs.** As §8.7 of docs/WEAVE_LOSS_DIAGNOSIS.md: W1's `harness/py.sh TREE harness/cmp.py HEAD_TREE OUT
WORK`, once per tree, and `harness/hashes.py`.

## 15. Confirmation run results — 2026-10-07 (stage p8c on one n2d-standard-16 in us-central1-a, 22 min of stage time, about $0.40, self-deleted)

`artifacts/i94_cal_collisions_trace.json`. **Every collision reproduced exactly** (collider, victim, lane, position,
step), on an AMD n2d machine where p8 ran on Intel n2 — and the control stayed clean.

| event | arm | seed | lane | reader's verdict | rear max decel / its b |
|---|---|---|---|---|---|
| R1 | `_dc_cal` | 134183728835869882 | 999007700_0 (Ruth St) | braked beyond b in the last steps — not a cap at b | 2.29 / 0.94 m/s² |
| R2 | `_dc_cal` | 6134032994440706937 | 999007700_0 | braked beyond b in the last steps — not a cap at b | 3.04 / 0.52 |
| R3 | `_dc_cal_netfix` | 134183728835869882 | 999007700_0 | **pinned at b while the gap closed** (command-cap signature) | 2.04 / 0.57 |
| T1 | `_dc_cal_netfix` | 165503670820534583 | 51388891_1 (T.H.52) | **opposing entries** in the same step | 8.97 / 1.88 |
| T2 | `_dc_cal_netfix` | 677105600768189526 | 51388891_2 | **opposing entries** in the same step | 9.0 / 1.50 |
| T3 | `_dc_cal_netfix` | 6953598295321596746 | 51388891_2 | not opposing: a cut-in from the right, rear not yet in the lane | 8.98 / 1.75 |

**Reading.** The T.H.52 mechanism is confirmed for two of three collisions (the third is a late cut-in), and
the Ruth St braking cap for one of three; the other two Ruth St rear cars did brake beyond b, but late, which
fits the second defect (a leader closer than minGap read as no leader until contact) rather than the cap alone.
Both are what W2's three switches address (§13–§14); W2 reproduced and removed both mechanisms on fixtures but
failed its no-new-lock criterion alone, and the corridor round of W1b + W2 (stage `p10_i94_cal_w1b_w2`) is the
next test. The weave commands were not logged (no recorder in the runner), so which rule issued each command is
inferred, not read.

## 16. The corridor round, run — 2026-10-07 (stage p10 on one n2d-standard-16 in us-east1-b, about 1 h 45 min of stage time, about $1.4, self-deleted)

`artifacts/weave_w2_corridor.json`; batteries `artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b{,_w2}.json`
(+ `_gated`, `baseline_gate_*`); log `artifacts/weave_collision_guards_2026-10-07/p10_i94_cal_w1b_w2.log.txt`; the two
scenarios written on the VM are committed under `scenarios/`. Arm A is the calibration-day inputs with W1b on both
weaves (`mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b`, hash 0d26de2a5f01); arm B adds the three W2 switches
(`…_w1b_w2`, hash 5080d84d4725). Step 3's 20 seeds, paired.

| # | criterion (§13.6) | result | verdict |
|---|---|---|---|
| CW1 | S790 06:30–07:30 flow, paired B − A, 95 % lower bound > −50 veh/h | −13.5 veh/h [−32.8, +5.8] | pass |
| CW2 | departed share, paired B − A, lower bound > −1.0 pp | +0.01 pp [−0.04, +0.07] | pass |
| CW3 | zero collisions in B | B: 0 in 20 runs. A: 1 (T.H.52, one run) | pass |
| CW4 | zero locks in B (front-row reader and `validation.locks`) | none in either arm; readers agree | pass |
| CW5a | given-up exits per weave, B ≤ A + 2 + 2·√(2A) | Ruth St 599 vs bound 608 (A 540); T.H.52 990 vs 1,043 (A 954) | pass |
| CW5b | W1b's releases ≤ 1 % of each entrance's departures, pooled | Ruth St **257 of 20,340 = 1.26 %**; T.H.52 1 of 91,644 | **fail** |

**Reading.** CW1–CW4 and CW5a hold: B had no collision (0 of 20; Clopper–Pearson 0–16.8 %) and no measurable cost
in flow, departures, locks or given-up exits. A had one, at T.H.52; a difference of one collision in 20 paired runs
is within what the pre-registered power note (§13.6) says this round cannot resolve, so the round does not show that
W2 caused the drop. CW5b fails, and arm A fails it at the same pooled share: with W1b alone, 257 of 20,340 Ruth St
entrants are also released (1.26 %; T.H.52: 3 against B's 1). The pooled totals coincide, but the per-seed counts
differ in 18 of 20 seeds, so W2 changes which entrants are released without changing how many. The clause measures
W1b on the calibration-day Ruth St weave, which is denser than the weave p9 passed it on (`_dc`, ≤ 1 %). By the rule fixed in
§13.6, W2 is **not adopted on this round**: the criteria are read as written, and
CW5b is one of them. What the round shows is that the failing clause is W1b's on `_dc_cal`, not W2's; whether to re-run
W1b's release bound against the arm it measures (A), or to treat 1.26 % as the calibration-day cost of W1b, is the
owner's call and not re-thresholded here. Both arms fail the battery gate as every `_dc_cal` arm has (GEH share
44.3 % → 46.1 %, wave 5.7 km/h both; `no_locks` pass in both; `no_collisions` A fail / B pass).

**What W2 did in arm B** (counters over 20 seeds; Ruth St / T.H.52): hand-back skips 3,546 / 2,880; close-leader
withheld 427 / 101; opposing entries deferred 6,985 / 16,767, of which vetoed 6,908 / 16,011. The counters are
recorded only when the switches are on (§14.4). Arm A's one collision is at T.H.52; p8's `_dc_cal` battery without
W1b had two, both at Ruth St (§10): W1b changes which contacts occur, not that they occur; arm B had none, which this round's power cannot attribute to W2 (CW3 above).

**Limits.** One recording's calibration-day inputs; 20 seeds; the two readers of CW4 agree but were written by the
same hand; the W2 counters are not paired with per-vehicle outcomes (no weave command recorder, §15). The round
does not test W2 without W1b (G6's standing lock, §14.7) — that pairing is what the fixtures rejected.
