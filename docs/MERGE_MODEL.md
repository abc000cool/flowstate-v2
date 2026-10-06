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

None.
