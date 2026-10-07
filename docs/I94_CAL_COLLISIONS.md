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
