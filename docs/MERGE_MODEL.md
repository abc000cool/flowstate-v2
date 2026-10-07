# The measured merge model (Stage 1 item 5): the specification

Written 2026-10-05, before any implementation or run of the model. Its
evidence base is [MERGE_MODEL_BRIEF.md](MERGE_MODEL_BRIEF.md) (the research
brief: measured quantities, mechanisms, failures, acceptance paths, design and
the case against it); section numbers `B§` refer to it. Decisions recorded
here are fixed before results; a change after the first probe is a dated
entry in "Amendments", reported both ways.

## 1. What the model is

`RampSpec.merge = "measured"`: one runner-driven model for **mandatory lane
changes inside merge zones** — acceleration lanes of on-ramps and weaving
sections (both movements) — built on three measured principles that ship
together (B§5.1: each alone has failed on record; they are coupled):

1. **Gap acceptance from measured critical gaps** — per driver, one lead and
   one lag critical time gap per movement, drawn from the measured joint
   log-normal fits (B§1.1), compared with bumper-to-bumper gaps defined as in
   `calibration.lane_change_gaps` (lead over the changer's speed, lag over the
   follower's), plus brake guards on both sides.
2. **Matching the target lane's speed** — a speed *ceiling* (never `setSpeed`)
   at the chosen gap's speed plus the measured offset δ, approached
   kinematically, with the desired speed `speedFactor × lane limit` capped by
   `maxSpeed` (honours `FleetSpec.speed_factor`).
3. **Relaxation after the crossing** — the entrant and its new follower get a
   temporary time headway equal to the accepted gap (floored), recovering to
   their own T with the measured time constant, via `vehicle.setTau`.

SUMO's LC2013 keeps every discretionary change, strategic positioning upstream
of the zones, plain diverges and the cooperation of vehicles that are not a
driven changer's chosen follower (B§5.9). The runner owns every mandatory
crossing inside a zone from one step before a vehicle can reach it (B§5.9).

## 2. Decisions (fixed before any run)

| Question | Decision | Why |
|---|---|---|
| Name and default | `merge: "measured"`; `RampSpec.merge`'s default stays `lane_change` | a default change would move every published I-24 hash and result; scenarios opt in |
| Zones | acceleration lanes of on-ramps (terminated as today) and paired weaving sections; the ramp's last `lookahead_m` as the approach | B§5.2 |
| Critical gaps, entering, acceleration lanes | lead 0.42 s (σ 1.42), lag 0.59 s (σ 1.46), log-normal, truncated to [p2.5, p97.5] | I-24 Old Hickory joint fit (B§1.1) |
| Critical gaps, entering, weaves | lead 0.46 s (σ 1.44), lag 0.92 s (σ 1.28) | I-24 HH–BR weave (B§1.1) |
| Critical gaps, exiting, weaves | lag 1.11 s (σ 1.90); **no lead time gate**, the lead brake guard only | WP-79/80: the I-24 lead (2.89 s) made exiters wait and give up; US-101's complete-coverage lead is 0.42 s (B§5.8) |
| Speed classes | none (one distribution per movement and side) | the data do not resolve them (B§1.1) |
| Speed offset δ | +0.6 m/s acceleration lanes, +1.0 m/s weaves | partner speeds, robust under thinning (B§1.3) |
| Relaxation | entering movement only; start T_eff = clamp((g − s0)/v, 0.5·T_i, T_i); T_eff(τ) = T_i − (T_i − T_eff,0)·e^(−τ/7.5 s); restore at 4τ_r, the next lane change or leaving; never below max(step length, 0.5·T_i); s0 unchanged | US-101 leader-side fit τ_r 7.5 s [5.2, 15.9], r0 0.54; exiters do not start short (B§1.4, B§5.7) |
| Execution | holding under mode 512 with no open request; the accepting step mode 256 + one-step `changeLane`, back to 512 next step; forced zone 80 m / 4 s only through the brake guard; opposing entries into one lane resolved before requesting (always on) | B§5.6 |
| Kept from the weave as fixed constants | gap choice (nearest gap the follower can open within b_F, evaluated at the relaxed T), one-step cooperation of the chosen follower only, ramp anticipation, pair release 2 s, exit priority when due, exit give-up 5 m, vacate 500 m with the 2,050 veh/h spare bound, `exit_prepare` on, lane-end give-up 7.5 m, creep 3 m/s, lookahead 120 m, request re-issue 2 s | load-bearing on record (B§2.3, B§5.10) |
| Parameter source | `artifacts/merge_model_params.json`, written by `scripts/merge_model_params.py` from the committed artifacts before any acceptance run; values global, never tuned per corridor | protocol §7.4 |
| Pre-registered sensitivity arms | US-101 critical gaps (entering 0.29 / 0.45 s; exiting lag 0.54 s); δ = 0; τ_r 5.2 / 15.9 s | B§5.11 |
| Speed factor in the T.H.52 test | the acceptance test runs as locked (the corridor fleet, factor 1); factor 1.245 (the driver check's recommendation) is reported beside it as a sensitivity, not as the acceptance | changing the fleet would change the test, not the merge (B§6.7) |
| Deletions | after the model passes the cheap gates of §4: `scripted`, `weave`, `merge_params`, `weave_params`, `SCRIPTED_MERGE_DEFAULTS`, `WEAVE_DEFAULTS` and their runner code, the off-by-default rules, `acceleration_lane` (unused, inert); the published `zipper` family stays (published scenarios use it); results made with deleted options are reproduced from release 2.5.0 | protocol §9.6; B§5.10 |

## 3. Self-check against the measurements (fixtures, before any probe)

The committed extractors run on fixture trajectories must show: (a) the
model's own fitted critical gaps recover the inputs; (b) partner speeds with
the real signs (the entrant faster than both new partners); (c) the entrant's
new follower at ≤ 0.9 of a normal gap at the change and the leader side ≤
0.8, recovering over seconds; (d) arrival-step crossings not suppressed. A
model that fails these does not represent the measurements whatever capacity
it reaches.

## 4. Cheap gates (phase 2 runs only these; no 20-seed battery)

| Gate | Run | Pass |
|---|---|---|
| G0 local | fixture tests (ramp fixture, McKnight, T.H.52 corridor section at seeds 3–22, T.H.52 capacity, Ruth St, T.H.61, the 29-run grid) | zero collisions, no lock, the self-check of §3 |
| G1 local | the T.H.52 section test (`test_th52_corridor_section_carries_free_flow_demand`) at seed 3, and its 20-seed form | reported; pass at seed 3 is the locked acceptance (3) |
| A cloud | I-24 Old Hickory single seed (`scripts/i24_merge_experiment.py`, `measured` against `lane_change`), central and US-101 parameters | peak sections (2,200 / 3,200 m) above about 6,000 veh/h with Old Hickory admitting ≥ the reference's 2,066 and zero collisions; otherwise stop and diagnose |
| B cloud | I-94 35-minute slice, 4 seeds, every weave and scripted ramp and 40648744 on `measured` | S790 flow, departed share, zero collisions, no lock |
| C cloud | the two phase-1 colliding (sample, seed) pairs as 4-hour runs on `measured` | zero collisions |

The 20-seed I-94 battery (acceptance (2), (4), (5)) and the I-24 FHWA
re-sequence with its battery (acceptance (1)) are **not run in phase 2**
(owner, 2026-10-05); the model cannot be called accepted until they run.

## Amendments

### A1 — 2026-10-06, after stage 1's fixture runs (before any cloud probe)

**What stage 1 showed** (the model as specified above, built alongside the
existing models; fixtures only): zero collisions and no lock in 37 grid runs
and 20 T.H.52 seeds, but the self-check of §3 fails — (b) partner speeds
(McKnight Rd: entrant − new follower −2.44 m/s, new leader − entrant +3.09;
T.H.52 weave: +0.09 / +1.87) and (c) the gap at the change (follower 0.93 /
1.23 of normal, leader 1.21 / 1.18), and (a) on the weave zone (fitted lead /
lag 0.99 / 1.27 s against inputs 0.46 / 0.92). The T.H.52 section test fails as
the weave's does: exit-end flow at seeds 3–22 mean 3,769 veh/h (weave 3,873),
GEH 16.9 (15.2). Cause: in free flow the short measured gaps accept entrants on
their first steps in the lane, still at ramp speed; a speed ceiling can only
cap, so nothing brought the entrant to the target lane's speed before it
changed. The vehicle-steps at −9 m/s² (87 against the replaced models' 20 in
the grid) trace to exiters admitted by the lead brake guard alone about 0 m
behind an equal-speed auxiliary-lane vehicle.

**A1.1 Speed condition on acceptance.** The critical gaps were estimated from
crossings at the measured partner speeds (the joint estimator works at speed
parity, `calibration.critical_gap`), so applying them to an entrant far slower
than its follower misapplies the measurement. Acceptance now also requires the
entrant's speed not below the new follower's by more than the measured lower
quartile of (entrant − new follower) at the change: 0.80 m/s at acceleration
lanes, 0.32 m/s in weaves (I-24, `lane_change_relaxation_i24.json`,
`summary_by_zone_kind`, entering, follower side, `rel_speed_ms` p25 at offset
0; robust under thinning, B§1.3). A forced change (80 m / 4 s) is exempt, as
before, guarded by the brake guards. The exiting movement keeps no speed
condition (exiters do not start short, B§1.4).

**A1.2 The changer's own model on the lead side.** Acceptance also requires the
changer's own SUMO model (at its relaxed T when entering, its own T when
exiting) not to brake harder than its comfortable deceleration behind the new
leader (`vehicle.getFollowSpeed`, mirroring the lag side's check of the
follower). Measured as a session diagnostic in stage 1 on the capacity
fixture: −9 m/s² vehicle-steps 9 / 12 / 6 → 1 / 0 / 0 at seeds 3–5.

Both are reported against stage 1's numbers above.

### A2 — 2026-10-06, after A1's fixture runs (before any cloud probe)

**What A1 showed.** A1.2 (the changer's own model on the lead side) did what
it was meant to: −9 m/s² vehicle-steps in the 37-run grid 87 → 11 (the
replaced models: 20), collisions 0. A1.1 (the speed condition) made the model
worse: SUMO's own lane-end braking slows entrants down a short acceleration
lane (McKnight: entrant median 21.7 → 9.9 m/s from 0 to 200 m), the runner
cannot raise a speed, so entrants refused for being too slow rode into the
forced zone, where A1.1 does not apply (forced crossings 24 → 69 at seed 3;
entrant − new follower −2.44 → −3.18 m/s); the self-check now fails every
item. On the T.H.52 section the model's take-over of the arrival step removed
LC2013's fast crossings (exiters reaching the section already in the auxiliary
lane: 78 of 327 against the weave's 157 of 336; median crossing speed exiters
6.6 against 11.0 m/s). T.H.52 section flow, seeds 3–22: 3,652 veh/h (stage 1
3,769; weave 3,873).

**A2.1** A1.1 is withdrawn; A1.2 stays.
**A2.2** The take-over one step before the zone applies to the entering movement
only; an exiter that LC2013 changes in the arrival step keeps that change (as
the weave did), and the model drives only exiters still in the wrong lane
inside the zone.
**A2.3** Before judging any merge model on the T.H.52 section test, its ceiling
is measured: the section's exit-end flow and station speed with no crossing
needed (vehicles placed in their target lanes, WP-76's pre-placement), seeds
3–12. If that ceiling fails criterion (ii), no merge model can pass the test
on this fixture and the test measures something other than merging; that is
reported, and the test is not changed.

### A3 — 2026-10-06, after A2's fixture runs (before any cloud probe)

**What A2 showed.** (1) *The T.H.52 section test's ceiling* (A2.3; WP-76's
pre-placement rebuilt in `scripts/merge_model_selfcheck.py ceiling`, which
reproduces WP-76's departures exactly): with nothing to cross, the section
carries 4,770–4,863 veh/h (criterion (ii-a) passes at 10 of 10 seeds) but its
station speed falls below 20 m/s in one window at 3 of 10 seeds — **including
seed 3, the locked test's seed** (19.97 m/s) — under speed factor 1 and 1.245
alike. A scratch check with `lcOvertakeRight` 1 passes all three: slow drivers
(18–26 per seed below 20 m/s desired) hold the left lanes with `lcKeepRight` 0
and cannot be passed on the right (no right-passing, owner 2026-09-27). **No
merge model can pass the locked test at seed 3 on this fixture**; the test is
not changed (A2.3), and what it measures beyond merging is reported. (2) A2.1
and A2.2 did not raise the section's flow (seeds 3–22: 3,653 veh/h; A1 3,652;
the weave 3,873); −9 m/s² steps in the grid 11 → 2, collisions 0. (3) The
measured model's loss against the weave traces to the speed ceiling's
fallback: with no gap chosen, a changer was capped at the mean speed of
target-lane vehicles within ±50 m, which at the section start is the slow
auxiliary lane's (median about 4 m/s in the first 50 m), so exiters still in
the through lane 1 dragged it down (lane-1 median 4.2 against the weave's 6.4
m/s). Without the fallback: +180 ± 44 veh/h over seeds 3–22, level with the
weave; grid 0 collisions, 2 −9 m/s² steps, 0 locks.

**A3** The speed ceiling applies only towards a chosen gap (its leader's speed
plus δ, approached kinematically); with no gap chosen, no ceiling is set.

### A4 — 2026-10-06, after cloud gates A and B

**Gate A (I-24 Old Hickory, seed 6914975401685141156, `artifacts/i24_merge_experiment_measured.json`)** — fails:
the measured model's peak sections read 5,882 / 5,864 veh/h against the
reference `lane_change`'s 5,836 / 5,810 (the kill gate asks for about 6,000;
the recording 6,626 / 6,639), Old Hickory admitting 2,109 of 2,109 in both,
zero collisions; the entry segments run too fast (43 / 40 against observed
36 / 32 km/h; 15-min RMSPE 0.309 against 0.232); the US-101 gap set reads
5,828 / 5,768; the Hickory Hollow weave on `measured` reads 5,654 / 5,581 and
admits 1,766 of 2,109. **Gate B (I-94 35-min slice, 4 seeds)**: S790 in windows
2–7 carries 4,566 veh/h under `measured` and 4,574 under the weave reference
(observed 4,667); departed 0.948 against 0.970; missed exits 0.9 % against
1.8 %; RMSPE 0.431 against 0.461; zero collisions in both.

**Consequence.** The measured model is safer and level, but it does not lift
merge capacity: the coupled-principles hypothesis (§1, B§5.1) is not
confirmed at I-24's acceptance (1). Under §2's deletion rule, `scripted` and
`weave` are **not** deleted; `measured` stays as an option, not a default.

**A4.** The dead switches are deleted now, independently of the measured
model's outcome: every `WEAVE_DEFAULTS` / `SCRIPTED_MERGE_DEFAULTS` key that is
off or unset by default, set by no committed scenario or pipeline stage, and
recorded as a negative result (`accept_lag_gap_s`, `exit_accept_lag_gap_s`,
`vacate_no_follower_braking`, `exit_giveup_patience_s`,
`exit_abreast_patience_s`, `exiter_yields`, `entrant_yields`,
`exiter_yields_halting`, `exiter_yield_lead_s`, `entry_speed_bound`,
`hold_release_s`, `anticipation_gate`, `swap_pairs`, `spread_crossings`,
`ramp_outlet`, `exit_priority_onset`, `anticipation_spares_exiters`,
`opposing_entry_guard`, the scripted merge's `courtesy`), the weave's pinned,
never-read `force_guard`, and the unused, inert `merge: "acceleration_lane"`.
Their derivations and measurements stay in docs/WEAVE_MODEL_PLAN.md and
CHANGELOG; results made with them are reproduced from release 2.5.0. Keys
that are on by default or used by the reference (`exit_prepare`, the vacate
window, pair release, give-ups, forcing, `force_guard` of the scripted merge)
stay.

### Gate C and where the model stands — 2026-10-06

**Gate C (`artifacts/merge_model_gate_c.json`)** — passes: the two phase-1
(sample, seed) pairs that collided under the weave reference (s00 headway
×0.90, s02 ×1.43) run for four hours on `measured` with zero collisions
(departed 0.905 and 0.773; the low share at ×1.43 is the long-headway
population's own capacity).

**Where it stands.** `measured` is safer than the models it was meant to
replace (no collision anywhere, far fewer hard stops, no lock on the
fixtures, the phase-1 collisions gone) and level with them on throughput, but
it does not lift merge capacity: I-24's acceptance (1) is not approached
(gate A), and the self-check against the real-driver measurements still fails
(entrants cross slower than their new partners). The T.H.52 section test
cannot be passed at its locked seed by any merge model on its fixture
(A3). Acceptance (2), (4), (5) need the 20-seed corridor battery, not run in
this phase. Not accepted; not a default.

**What the evidence now says about the shortfall.** In SUMO a ramp vehicle
brakes for the end of its lane and the runner can only lower speeds, so an
entrant cannot be brought to the target lane's speed before it changes; and on
I-24 the measured model's merge admits every ramp vehicle yet the downstream
sections still discharge about 5,880 veh/h — the shortfall sits downstream of
the merge, in how the queue discharges, at least as much as in the merge's
gap acceptance. The next research question is the discharge (the population's
queue-discharge rate and SUMO's lane-end behaviour), not more acceptance rules.

**Update 2026-10-07 (docs/I24_DISCHARGE_DIAGNOSIS.md, fixtures only):** the I-24 peak-section ceiling (about 6,050 veh/h with the calibrated drivers) is not set by the Old Hickory merge — in I-24's own 4-lane geometry with free outflow the merge discharges 6,620 ± 62 veh/h (old drivers) and 6,979 ± 49 (calibrated), above the recording's 6,626. The ceiling comes from downstream: mostly the representation of the measured downstream boundary (its speed applied as every driver's desired speed over the 992-m last edge: drivers travel at about 33 km/h and pass 5,743 veh/h where the road passed 6,009), partly the Hickory Hollow weave, and about half of the remaining gap looks like an inconsistency between the recorded section counts and ramp counts. A proposed, opt-in boundary correction (Amendment B1) is not yet run on the corridor; until it is, statements here about a merge or queue-discharge shortfall at I-24 should be read as superseded.
