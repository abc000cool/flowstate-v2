# Where the T.H.52 weave loses its capacity (2026-10-07)

A diagnosis of the gap between the T.H.52 section test's no-crossing ceiling
and the weave, with the calibrated I-94 drivers
(`--fleet-from scenarios/mndot_i94_wb_stpaul_weave_dc.yaml`). Fixtures only. No
package code, test, golden or scenario was changed, and nothing was committed.

Labels:
- **[run]**: a run made for this document. Per-seed rows are in
  `artifacts/weave_loss_2026-10-07/arms/<arm>.json`. The trajectory tables of
  §3 are in `artifacts/weave_loss_2026-10-07/decomposition_seeds3-7.txt`.
- **[artifact]**: read from the named committed JSON.
- **[record]**: quoted from the named document.
- **[estimate]**: an estimate, with its basis stated.

## 0. In plain English

- **The loss is 465 veh/h** (95 % CI 427–504; 20 paired seeds), not 450. The
  ceiling (nothing to cross) carries 4,826 veh/h. The weave carries 4,361.
- **Where it happens.** The weave breaks down 3–5 simulated minutes in, at
  the start of the section (the entrance gore), in the auxiliary lane and the
  right through lane. It stays broken down for 78–100 % of the rest of the
  test. After that, the two crossing streams swap lanes at the gore at 6–8 m/s
  and the section discharges about 4,430 veh/h.
- **Where the missing vehicles wait.**
  - About half are queued on the mainline upstream. Most of them are exiters
    in the right through lane, which runs at 4–5 m/s for 600 m back from the
    gore.
  - The rest are on the ramp or waiting to enter it.
- **No single rule causes the loss.** Removing each suspected cause in turn
  left it in place: the gap-choice blind spot that strands changers at the
  section end, the entrant stuck at the end of the auxiliary lane, the missing
  post-crossing relaxation, lane-change eagerness, and keep-right. The largest
  of these recovered 32 veh/h.
- **Two inputs move it. Neither is measured.**
  - **How many vehicles cross.** This rests on an assumed origin–destination
    split; the ramp-to-ramp share is not observed. With a quarter fewer
    crossers the weave passes the flow criterion (GEH < 5) at 18 of 20
    seeds.
  - **How far ahead drivers line up their merge.** This is the weave's
    `lookahead_m`, an engineering constant of 120 m. At 300 m it recovers
    177 veh/h.
- **Recommendation.**
  - Do not tune either input to pass the test.
  - Measure the anticipation distance on I-24.
  - Bound the ramp-to-ramp share, or carry it as an uncertain input.
  - Adopt one small physical fix as an opt-in amendment, with criteria fixed
    here (§6.2): an entrant stuck at the end of the auxiliary lane takes the
    exit, the mirror of the existing exit give-up.
    - **Implemented and evaluated the same day (§8).** It passes F1, F2 and
      F4 and fails F3 and F5. So the key stays off.

## 1. Setup and provenance

**Code.** The runs used a snapshot of HEAD `6de4e21` (`git archive` into the
session scratch directory), imported ahead of the editable install through
`PYTHONPATH`. The snapshot was needed because `packages/microsim/microsim/runner.py`
was being edited by another session throughout. The working tree's runner was
never used.

**Reproduction.** On this snapshot:
- the weave at seeds 3–22 reproduces
  `artifacts/p3_driver_grid_2026-10-07/th52_section_dc_weave.json` exactly,
  seed by seed;
- the ceiling reproduces `th52_ceiling_dc.json` exactly at its seeds 3–12.

Config hashes differ from the artifacts only because the fixture's `osm_file`
is an absolute path, here into the snapshot.

**Fixture.** `_th52_corridor_config` with the `fleet` block of
`scenarios/mndot_i94_wb_stpaul_weave_dc.yaml`:
- EIDM, mean `a_max` + 1 sd;
- `lc_strategic` 5, `lc_strategic_ramp` 1, `lc_keep_right` 0.1;
- speed factor 1, as the test is locked;
- `WEAVE_DEFAULTS`, so `exit_prepare` 0;
- 20 min at a 0.5 s step.

All figures are macOS records (docs/LESSONS.md); the corridor rounds on Linux
decide.

**Harness.** `artifacts/weave_loss_2026-10-07/harness/`:
- `arm.py` runs an arm with existing knobs.
- `cfarm.py` runs the counterfactuals: scratch monkeypatches of the snapshot,
  never package edits.
- The rest are the trajectory readers.

The harness expects the snapshot at `harness/head`. Each counterfactual was
first run with its new branch disabled and reproduced the baseline bit for bit
(`cf_h1eq` at seeds 3–5, `cf_log` at seeds 3–7).

**Pre-registration.** `artifacts/weave_loss_2026-10-07/prereg.md` is
timestamped and was written before each batch it governs:
- 01:08 CDT: H1, H2 and the knob screen;
- 01:17: H3 and S1;
- 01:23: H5;
- 01:26: D1 and D2;
- 01:30: the LC2013 screen.

Before 01:08 only the baseline weave and ceiling at seeds 3–7 had run. Arms
that combine registered changes (H1+H2, H2+H3, H2+H3+H5) were not registered
separately and are labelled as such.

**Statistics.** Paired t-intervals over seeds: 19 degrees of freedom for 20
seeds, 4 for the five-seed screen.

## 2. The loss

| | exit-end flow veh/h (mean ± sd) | GEH < 5 | all criteria | lowest station speed m/s (mean) | entrance departed | collisions | −9 m/s² steps |
|---|---|---|---|---|---|---|---|
| weave, seeds 3–22 [run] | 4,361 ± 83 | 1 / 20 | 0 / 20 | 17.07 | 0.951 | 0 | 2 |
| ceiling, seeds 3–22 [run] | 4,826 ± 19 | 20 / 20 | 20 / 20 | 21.43 | 1.000 | 0 | 0 |
| ceiling − weave, paired | **+465 [+427, +504]** | | | +4.37 [+3.65, +5.08] | +0.048 | | |

The observed inflow is 4,877 veh/h. GEH < 5 needs at least 4,535.

## 3. Decomposition

Sections 3.1–3.8 read the kept run trees of seeds 3–7: weave and ceiling, same
seeds, same planned departures. Counters over 20 seeds are from the 20-seed
runs.

### 3.1 Where the missing vehicles are at t = 1,200 s

Weave minus ceiling, vehicles; mean of seeds 3–7 [run, `acct2.py`]:

| | vehicles | share of the deficit |
|---|---|---|
| fewer vehicles through the exit end, 120–1,200 s | −136.6 | (≈ 455 veh/h) |
| more queued on the mainline upstream of the section | +65.4 | 48 % |
| more on the ramp (inserted, not yet on the section) | +51.4 | 38 % |
| more ramp vehicles not yet inserted | +16.4 | 12 % |
| more in the section | +1.8 | 1 % |
| more mainline vehicles not yet inserted | +1.6 | 1 % |

Ramp travel time (insertion to the section) has a median of 53–140 s by seed
against 39–40 s in the ceiling. Its 90th percentile is 149–259 s against 41–49 s.

### 3.2 Where speeds collapse

Mean speed, t 300–1,200 s, by section lane. Lane 0 is the auxiliary lane,
lanes 1–3 are the through lanes from right to left, and x is the fixture's
corridor axis; the section runs from 829 to 1,134 m. Values are weave / ceiling
in m/s [run, `stspeed.py`].

| x [m] | lane 0 | lane 1 | lane 2 | lane 3 |
|---|---|---|---|---|
| 0–300 | | 13.2 / 22.4 | 16.1 / 22.9 | 21.3 / 23.7 |
| 500–600 | | 5.4 / 22.0 | 10.0 / 22.5 | 20.1 / 23.4 |
| 600–700 | | 4.0 / 21.6 | 10.7 / 22.1 | 20.6 / 23.2 |
| 800–829 | | 5.0 / 20.3 | 15.7 / 21.2 | 21.6 / 23.0 |
| **829–860 (gore)** | **6.6 / 20.6** | **6.6 / 20.4** | 16.6 / 21.4 | 21.7 / 23.0 |
| 950–1,000 | 13.3 / 20.5 | 14.2 / 21.5 | 18.9 / 22.2 | 21.8 / 22.9 |
| 1,100–1,134 | 11.7 / 20.3 | 12.5 / 21.7 | 19.0 / 22.4 | 22.3 / 22.9 |
| 1,134–1,200 | | 17.2 / 21.8 | 20.5 / 22.4 | 22.6 / 23.0 |
| 1,200–1,355 | | 20.4 / 21.7 | 21.7 / 22.4 | 22.9 / 23.0 |

- The lowest speeds are at the gore and in lane 1 upstream of it.
- Vehicles accelerate through the section and are nearly free downstream of
  it.
- So the gore is an active bottleneck.
- Lane 2 also slows upstream: exiters still in lane 2 wait for the jammed
  lane 1. Lane 3 is nearly unaffected.

### 3.3 Flows by lane at the gore

At x = 840 m, t 300–1,200 s, veh/h [run, `xsec.py`]:

| lane | weave | of which | ceiling | difference |
|---|---|---|---|---|
| 0 | 848 | ramp→exit 318, entrants still owing their change 376, exiters that crossed on arrival 154 | 1,405 | |
| 1 | 1,020 | exiters owing their change 625, entrants that crossed on arrival 319, through 76 | 754 | |
| 0 + 1 | 1,868 | | 2,159 | −291 |
| 2 | 1,071 | through 911, exiters 156 | 1,297 | −226 |
| 3 | 1,492 | | 1,468 | +24 |
| all | 4,431 | | 4,923 | −492 |

At x = 800 m, lane 1 carries 773 exiters and 77 through vehicles (91 %
exiters). The vacate rule and `lcStrategic` 5 have made it an exit lane well
upstream of the gore.

### 3.4 When

**Breakdown time.** The first 30-s window in which the lane-1 mean speed over
700–829 m falls below 15 m/s, after the first minute, is at 270 / 180 / 210 /
330 / 210 s for seeds 3–7.

**Demand at the time.** Demand into the section is 4,324 veh/h in 0–300 s and
4,777 in 300–600 s; the ceiling carries both in free flow.

**Congested share.** The share of minutes 2–19 with that lane below 15 m/s is
0.89 / 1.00 / 0.78 / 0.89 / 0.94 (seeds 3–7) [run].

**Free flow is not recovered.** Seed 3 recovers for one minute and breaks down
again.

### 3.5 Who is at the gore while it is congested

Shares of vehicle-steps in congested minutes, with mean speed [run, `extra.py`]:

| zone | lane 0 | lane 1 |
|---|---|---|
| 50 m upstream of the gore | — | exiters 92 % (4.7 m/s), through 8 % |
| first 50 m of the section | entrants still owing 41 % (6.2 m/s), ramp→exit 34 % (7.2), exiters already crossed 26 % (8.2) | exiters still owing 60 % (6.4 m/s), entrants already crossed 33 % (8.0), through 7 % |
| last 100 m of the section | exiters 71 % (12.3), ramp→exit 26 % | entrants 55 % (14.3), exiters still owing 21 % (9.6), through 24 % |

**The congested gore is a two-way swap of two queues at walking pace.** The
ramp's queue is in lane 0. The exiters' queue is in lane 1. Each must cross
the other within the first tens of metres.

### 3.6 The crossings: where, how fast, how long they wait

All values are per seed, seeds 3–7 [run, `events.py`, `extra.py`].
"Arrival" means a change made in the step the vehicle reached the section,
which is LC2013's own change before the runner takes over.

| movement | changes / seed | position past the gore p10 / p50 / p90 [m] | speed at change p50 [m/s] | free flow: speed; within one step of arrival | congested: speed; within one step |
|---|---|---|---|---|---|
| entrants, lane 0 → 1 | 218 | 1.3 / 15 / 163 | 8.6 | 18.2; 67 % | 7.4; 37 % |
| exiters, lane 1 → 0 | 308 | 3.4 / 54 / 272 | 9.8 | 17.2; 37 % | 8.8; 10 % |

Other changes:
- Exiters lane 2 → 1: 97 per seed, median 58 m before the gore, 90th
  percentile 233 m into the section.
- Exiters lane 3 → 2: 23 per seed.
- Through vehicles lane 1 → 2: 188 per seed, median 427 m upstream, the vacate
  rule's window.
- Entrants continuing lane 1 → 2: 80 per seed, median 173 m into the section.
- Jackson-exit vehicles moving right, lane 2 → 1: 62 per seed, median 27 m
  into the section.

Waits, measured from the first sample on the section to the change
[run, `extra.py`]:

| | wait p50 / p90 [s] |
|---|---|
| entrants, free flow | 0 / 11.3 |
| entrants, congested | 3.3 / 16.0 |
| exiters, free flow | 2.5 / 19.5 |
| exiters, congested | 7.5 / 25.0 |

Two notes on the waits:
- Exiters also wait in the upstream queue, and that wait is not included.
- The runner's own wait means over 20 seeds, which cover the changes it
  controlled only, are 8.4 s entering (6.4–10.9) and 11.6 s exiting
  (8.5–13.7) [run, `meta.json`].

### 3.7 Accepted gaps against the measured ones

Time gaps at the change: the lead gap divided by the changer's speed, and the
lag gap divided by the follower's speed (the convention of
`i24_lane_change_gaps.json`).

| | lead p10 / p50 [s] | lag p10 / p50 [s] | source |
|---|---|---|---|
| model entrants (weave) | 1.10 / 3.07 | 1.20 / 2.51 | [run] |
| I-24 HH–BR weave, entering | 0.60 / 1.69 | 0.82 / 2.46 | [record] MERGE_MODEL_BRIEF §1.2 |
| model exiters (weave) | 1.01 / 2.06 | 1.13 / 2.08 | [run] |
| I-24 HH–BR weave, exiting | 0.79 / 3.12 | 0.80 / 2.63 | [record] MERGE_MODEL_BRIEF §1.2 |

The measured critical gaps are 0.46 s (lead) and 0.92 s (lag) for entering,
and 1.11 s (lag) for exiting [record, MERGE_MODEL_BRIEF §1.1].

**The model's entrants never use the short gaps real entrants use.** Their
10th percentile is 1.1–1.2 s against 0.6–0.8 s.

**Why.** The acceptance asks for `s0 + 0.6 s · v` on both sides, plus the
changer's brake gap, plus the follower absorbing the changer at its full `T`.
At the congested crossing speed of about 8 m/s, `s0` alone (about 1.6–3.7 m
in the drawn population) adds 0.2–0.46 s.

**Coverage caveat.** I-24's lead gaps are upper bounds and its lag gaps are
expected high, so the true real-driver tails are, if anything, shorter
[record, MERGE_MODEL_BRIEF coverage rules].

### 3.8 What starts the breakdown

**(a) Changers halting at the section end.** In every baseline seed, the first
vehicle to halt in the section is a changer at the end of its lane: an exiter
at the end of lane 1, an entrant at the end of lane 0, or an abreast crossing
pair. These first halts occur at 327 / 100 / 172 / 500 / 157 s for seeds 3–7.
Three mechanisms lead there.

- **The gap-choice dead zone** (newly identified).
  - **Where it comes from.** `_weave_choose_gap` takes a target-lane vehicle
    as a gap leader only if its front is at or ahead of the changer's front.
    It takes a vehicle as a follower only if the vehicle is wholly behind the
    changer's rear.
  - **The blind spot.** A target-lane vehicle whose front lies within the
    changer's own length behind the changer's front is neither. No gap around
    it is a candidate, so nothing cooperates and the changer is not eased.
  - **How it persists.** At matched speeds the configuration lasts.
    - Trace, seed 5: exiter v00090 rides 0–5 m abreast of ramp→exit v01227 for
      250 m at 17–20 m/s.
    - It then brakes for the end of its lane while v01227 passes.
    - It halts at x = 1,133 at t = 171 s, the first halt of that seed.
  - **How common it is.**
    - 11–17 % of changer-steps are in the dead zone.
    - 407 of 2,057 changers (20 %) spend at least 2 s there, 5.7 s on average.
  - **Its share of the halts.** Those changers account for 18 of the 28 halted
    at the section end (64 %) and 86 % of the halted time [run, `deadzone.py`].
  - It is present in `measured` too, which reuses this gap choice.
- **A stranded entrant blocks the exit lane.**
  - **What happens.** An entrant that did not reach lane 1 halts at the end of
    the auxiliary lane. Every exit-bound vehicle behind it in that lane then
    stops.
  - **Why nothing frees it.** The exit give-up (`exit_giveup_m`) has no
    entering mirror. The network's `lane_end_giveup_m` rule skips weaving
    sections.
  - **Trace, seed 4.** v01207 stood at x = 1,134.0 from 101.5 s to past 150 s.
    By t = 122 s, nine exit-bound vehicles were stopped behind it.
  - **How often.** 18.9 s per run on average over 20 seeds (0–62.5 s; 1.3
    episodes) [run, `strand.py`].
- **Abreast crossing pairs.** An entrant in lane 0 and an exit-bound vehicle
  in lane 1 within 5 m of each other for at least 5 s: 26 pairs per run on
  average (13–46) over 20 seeds [run].

**(b) The gore breaks down by itself.**
- **H1 removes most early halts.** It removes the dead zone for non-partner
  leaders (§4). The first end-of-section halt moved from 100–500 s to
  790–900 s in three of five seeds and to 213 s and 499 s in the other two.
- **The gore still breaks down.** Lane 1 upstream still fell below 12 m/s at
  360–450 s, at the gore, with the downstream half of the section at
  15–21 m/s.
- **H1 recovers nothing.** The 20-seed flow gain was +6 [−38, +50].
- **So the halts set when the breakdown comes, not whether it comes.**
- **Trace of a gore breakdown** (H1, seed 4, t = 342.5 s). Exiter v00281 in
  lane 1, 116 m before the gore with its own leader 75 m ahead, brakes at its
  full `b` (−3.79 m/s²) for 2 s, from 22 to 13 m/s. A logged replay confirms
  this is a cooperation command (`cf_h1log4`). v00281 is the follower of the
  gap chosen by entrant v01282, still on the ramp 113 m before the gore and
  7.8 m ahead of it: the ramp anticipation. Lane 1 upstream of the gore falls
  below 12 m/s within the next minute. It stays there for 68 % of the
  remaining 30-s windows of that run.

**The runner's braking commands.** Logged without changing behaviour; seeds
3–7 replay bit for bit [run, `cf_log`].

| | free flow, before breakdown | congested |
|---|---|---|
| minutes (summed over seeds) | 15 | 80 |
| command-steps that brake, per minute | 329 | 401 |
| of which at the vehicle's full `b`, per minute | 168 | 165 |
| speed removed by commands [m/s per minute] | 175 | 190 |

In free flow, 60 % of the speed removed comes from exiters' crossings (their
easing and their followers' cooperation), 31 % from the ramp anticipation and
9 % from entrants in the section.

### 3.9 Are the weave's scripted mechanics binding?

Counters over 20 seeds, per run, as mean (range) [run]:

| mechanism | counter | mean (range) |
|---|---|---|
| easing | vehicle-steps | 2,557 (1,664–3,264) |
| cooperation | vehicle-steps | 12,472 (8,735–14,769) |
| forced changes | completed | 40 (18–60) |
| forced changes | refused steps | 299 (88–541) |
| pair release | releases | 24.5 (3–76) |
| exit give-up | exits given up | 3.3 (1–6); 66 of 8,334 reached, 0.79 % |
| — | unfinished | 4.0 |
| vacate rule (seeds 3–7 only) | vacated | 116–146 |
| vacate rule (seeds 3–7 only) | refused | 1–17 |

**Easing and cooperation bind all the time.** Every second braking command is
at the vehicle's full `b` (§3.8).

**The rest bind occasionally.**
- The forced zone, the pair release and the exit give-up fire tens of times
  per run.
- The vacate rule binds upstream: it makes lane 1 an exit lane.
- `exit_prepare` is inert on this fixture. The corridor's reference
  configuration uses it, so it was tested here (§4).

**Is the loss in these mechanics?** Not that changing them recovers it.
Moving the forced zone to 150 m, the vacate window to 300 or 800 m, or turning
on `exit_prepare` changes the flow by less than 20 veh/h (§4).

### 3.10 LC2013: too late, too early, or blocked by keep-right?

- **Exiters position early.** `lcStrategic` 5 puts 77 % of exiters in lane 1
  by x = 500 m and 81 % by 800 m. Those still in lanes 2 and 3 are blocked by
  the lane-1 queue, not late.
- **Arrival-step crossings are early, and they crowd the gore.** LC2013 makes
  34 % of entering crossings in the arrival step, 67 % in free flow, all
  within a few metres of the gore. It makes 10–37 % of exiting crossings
  likewise (37 % in free flow, 10 % in congestion).
- **None of LC2013's parameters binds** (five-seed screen, §4):
  - `lc_strategic` 2: +39 [−98, +176];
  - `lc_strategic` 10: +41 [−56, +138];
  - `lc_strategic_ramp` 5: +30 [−46, +106];
  - `lc_assertive` 1.5: +83 [−21, +187].
- **Keep-right does not bind.** `lc_keep_right` 0 gives −5 [−76, +65].

### 3.11 The crossing volume

**The split is an assumption.** The fixture draws the exit for mainline
vehicles and entrants alike. That is HCM 7.1's proportional split
(Equations 13-2 to 13-6), and the ramp-to-ramp share is unobserved
[record, docs/WEAVE_MODEL_PLAN.md WP-76]. The movement in question is US 52
northbound off the Lafayette Bridge to exit 242B (I-35E North / US 10 West)
(OSM ways 769818012 and 18207598).

**D1 sensitivity.** D1 relocates each crossing vehicle to the leg next to its
target with probability p, the ceiling's relocation applied to a random
subset. A larger ramp-to-ramp share at the same counts does the same thing:
both crossing flows fall. About 635 vehicles per run would cross at p = 0
(≈ 1,900 veh/h).

| p | crossers left [veh/h, estimate] | flow | paired vs weave | GEH < 5 | all criteria | collisions |
|---|---|---|---|---|---|---|
| 0 (weave) | ≈ 1,900 | 4,361 | — | 1 / 20 | 0 / 20 | 0 |
| 0.25 | ≈ 1,430 | 4,636 | +275 [+235, +316] | 18 / 20 | 0 / 20 | 1 |
| 0.5 | ≈ 950 | 4,810 | +449 [+411, +488] | 20 / 20 | 1 / 20 | 0 |
| 0.75 | ≈ 480 | 4,822 | +462 [+423, +500] | 20 / 20 | 6 / 20 | 0 |
| 1 (ceiling) | 0 | 4,826 | +465 [+427, +504] | 20 / 20 | 20 / 20 | 0 |

**The flow is steeply nonlinear in the crossing volume.** A quarter fewer
crossers recovers 59 % of the loss; half recovers 97 %.

**What p = 0.25 means in ramp-to-ramp terms** [estimate]: about 49 % of the
entrance taking exit 242B instead of the proportional 29 %. The basis is equal
counts moved from each crossing movement, at the 20-minute mean demand.

**Speed lags flow.** The station-speed criterion recovers more slowly than
flow (all criteria at 1/20 and 6/20 for p = 0.5 and 0.75).

**One collision at p = 0.25** was not traced.

### 3.12 Where the 465 veh/h go: the answer

1. **Mechanism.** The loss is the weave's own breakdown: a drop in discharge
   to about 4,430 veh/h once the gore congests (§3.3–3.4). The demand of
   4,777–5,137 veh/h after the first 5 minutes cannot be served at that rate.
   - **Stock.** The deficit accumulates as stock: 48 % in an exiter queue in
     lane 1 (and the slowed lane 2) upstream, 38 % on the ramp, and 12 % not
     yet able to enter the ramp.
2. **Trigger.** Either a changer halting at the section end (§3.8a) or the
   gore itself, through the braking that every crossing costs in the target
   lane, a full IDM gap opened at up to `b` (§3.8b).
3. **What removing each mechanism recovers** (paired, 20 seeds; §4):

   | mechanism removed | recovered [veh/h] |
   |---|---|
   | the stranded entrant | +32 [+8, +57] |
   | the dead zone | +6 [−38, +50] |
   | the missing relaxation | +13 [−16, +43] |
   | relaxation plus a relaxed-T gap choice | −35 [−112, +43] |
   | LC2013 eagerness, keep-right, forced zone, vacate window, `exit_prepare` | inside the noise |
4. **What does move it.**
   - How far ahead the gaps are formed: `lookahead_m` 200 m gives
     +98 [+55, +141]; 300 m gives +177 [+134, +219].
   - How many vehicles cross: §3.11.

## 4. Hypotheses and fixture results

All arms are paired against the weave baseline on the same seeds [run].

| arm | what changed | seeds | flow | Δ flow [95 %] | Δ lowest station speed [95 %] | GEH < 5 | coll. | −9 m/s² steps | given up (arm / base) | stranded s/run |
|---|---|---|---|---|---|---|---|---|---|---|
| **baseline** | weave, calibrated drivers | 3–22 | 4,361 | — | — | 1 | 0 | 2 | 66 | 18.9 |
| **existing knobs, screen** | | | | | | | | | | |
| `exit_prepare` 1 | weave key (corridor reference) | 3–7 | 4,377 | +9 [−91, +108] | −0.36 [−4.40, +3.68] | 1 | 0 | 0 | 11 / 14 | 21.9 |
| + `lane_end_giveup_m` 7.5 | the full xlsfg reference | 3–7 | 4,377 | identical | | | | | | |
| `lookahead_m` 200 | weave key | 3–7 | 4,487 | +119 [−3, +240] | −0.41 [−4.07, +3.25] | 2 | 0 | 0 | 21 / 14 | 24.9 |
| `accept_gap_s` 0.46 | weave key; measured entering-lead median | 3–7 | 4,436 | +67 [+10, +125] | +1.05 [−2.84, +4.95] | 1 | 0 | 0 | 9 / 14 | 2.6 |
| `force_within_m` 150 | weave key | 3–7 | 4,372 | +3 [−99, +106] | +2.65 [−0.43, +5.73] | 0 | 0 | 0 | 5 / 14 | 4.4 |
| `vacate_ahead_m` 800 / 300 | weave key | 3–7 | 4,387 / 4,381 | +18 [−59, +95] / +12 [−80, +104] | | 0 / 0 | 0 | 0 | 24 / 12 (of 14) | |
| `lc_assertive` 1.5 | fleet | 3–7 | 4,451 | +83 [−21, +187] | +0.43 [−3.39, +4.24] | 1 | 0 | 0 | 10 / 14 | 26.1 |
| `lc_strategic_ramp` 5 | fleet | 3–7 | 4,399 | +30 [−46, +106] | +0.74 [−1.37, +2.86] | 1 | 0 | 0 | 15 / 14 | 15.2 |
| `lc_strategic` 2 / 10 | fleet | 3–7 | 4,408 / 4,409 | +39 [−98, +176] / +41 [−56, +138] | | 1 / 1 | 0 | 0 | 25 / 7 (of 14) | |
| `lc_keep_right` 0 | fleet (pre-calibration value) | 3–7 | 4,363 | −5 [−76, +65] | −0.52 [−1.83, +0.79] | 1 | 0 | 2 | 15 / 14 | 19.5 |
| **K: `lookahead_m` 200** | registered follow-up (> +100 at 5 seeds) | 3–22 | 4,458 | **+98 [+55, +141]** | +0.11 [−0.76, +0.99] | 3 | 0 | 0 | 64 / 66 | 16.9 |
| **D2: `lookahead_m` 300** | dose-response | 3–22 | 4,538 | **+177 [+134, +219]** | +0.86 [+0.15, +1.58] | 10 | 0 | 0 | 62 / 66 | 15.1 |
| **counterfactuals (scratch code)** | | | | | | | | | | |
| H1 | dead zone removed for non-partner leaders | 3–22 | 4,367 | +6 [−38, +50] | +0.21 [−0.64, +1.06] | 1 | 0 | 0 | 59 / 66 | 14.4 |
| **H2** | stranded entrant takes the exit | 3–22 | 4,393 | **+32 [+8, +57]** | +0.54 [−0.02, +1.10] | 2 | 0 | 0 | 59 / 66 | 4.2 |
| H1+H2 | combination (not separately registered) | 3–22 | 4,390 | +30 [+1, +58] | +0.43 [−0.55, +1.41] | 0 | 0 | 1 | 62 / 66 | 3.0 |
| H3 | post-crossing relaxation (MERGE_MODEL §2 constants), entering crossings | 3–22 | 4,374 | +13 [−16, +43] | −0.14 [−0.82, +0.54] | 1 | 0 | 0 | 72 / 66 | 18.8 |
| H2+H3 | combination (not separately registered) | 3–22 | 4,399 | +38 [+1, +76] | +0.70 [−0.06, +1.45] | 1 | 0 | 0 | 53 / 66 | 2.9 |
| H5 (with H3) | gap choice and absorption at the relaxed T | 3–22 | 4,326 | −35 [−112, +43] | **−1.70 [−2.41, −1.00]** | 1 | 0 | 2 | 102 / 66 | 52.7 |
| H2+H3+H5 | combination (not separately registered) | 3–22 | 4,429 | +68 [+29, +108] | −1.17 [−2.10, −0.25] | 2 | **1** | 3 | 76 / 66 | 7.0 |
| **sensitivities** | | | | | | | | | | |
| S1: speed factor 1.245, weave | MERGE_MODEL §2's registered sensitivity | 3–22 | 4,386 | +25 [−8, +58] | −0.15 [−1.04, +0.74] | 0 | 0 | 0 | 108 / 66 | 28.8 |
| S1: speed factor 1.245, ceiling | | 3–22 | 4,824 | weave − ceiling at 1.245: −438 [−471, −405] | | 20 | 0 | 0 | 0 | 0 |
| D1: crossing volume | §3.11 | 3–22 | | | | | | | | |

Notes:
- **Screen rule.** The pre-registered rule took a five-seed arm to 20 seeds
  only above +100 veh/h. Only `lookahead_m` 200 qualified. `accept_gap_s` 0.46
  (+67 at 5 seeds) was not followed up, by that rule.
- **H2.** 29 entrants took the exit over 20 seeds, about 0.5 % of entrance
  departures [estimate: about 280 entrants per seed].
- **H3.** Granted relaxation at 680 entrant and 577 follower crossings out of
  3,063 entering crossings. Most crossings reached the change with a gap at or
  above the follower's normal gap, because the cooperation had pre-opened it.
  So H3 had little to act on.
- **H5.** Made followers open less before the change. The result was more
  entrants stranded at the auxiliary lane's end (52.7 s per run) and more
  given-up exits.
- **S1 caveat.** The weave's runner assumes factor 1 (`_weave_veh`,
  `_weave_lane_vmax`). Even so, the ceiling is unchanged and the gap stays at
  438. Speed is not the missing capacity.

**Pre-registered predictions against outcomes.**

| arm | predicted | outcome |
|---|---|---|
| H1 | a gain | **failed** |
| H2 | a gain smaller than H1's, with at most 2 % of entrants taking the exit | held (0.5 %) |
| knob screen | no knob above about 100 veh/h | held, except `lookahead_m` |
| H3 | a gain | **failed** |
| H5 | a gain | **failed** |
| D1 | monotone in p, with GEH < 5 reached somewhere in (0.25, 1] | held; reached at 0.25 already (18/20) |
| D2 | a resolved gain over 200 m | held |
| LC2013 screen | nothing above 100 veh/h | held |

## 5. Reading

- **The loss is not in a rule.** Rules were removed one by one:
  - the gap-choice blind spot;
  - the stranded entrant;
  - the missing relaxation;
  - the full-T cooperation;
  - LC2013 eagerness and keep-right;
  - the forced-zone, vacate and `exit_prepare` settings.

  None recovers more than 7 % of the loss. The weave's scripted mechanics fire
  constantly, but they are not the cause.
- **What sets the loss.**
  - **The crossing.** About 1,900 crossings an hour in 305 m, each made by
    braking the target lane into an IDM gap at up to `b`. At this total
    demand the weave carries the full demand at about 950 crossers an hour.
    At about 1,430 it falls 190 veh/h short.
  - **What the HCM says.** HCM 7.1 puts the observed demand at d/c 0.59–0.70
    [record, WEAVE_MODEL_PLAN WP-76].
  - **What the road does.** It runs at 25.8–26.6 m/s [record, test docstring].
  - This is WP-65's "every crossing ties a car-following gap in both lanes",
    now quantified.
- **What the dose-responses add.** The two levers are the anticipation reach
  and the crossing volume.
  - **Anticipation reach.** With `lookahead_m` 300, more of the commanded
    braking moves onto the ramp: the anticipation share of command-steps rises
    from 0.49 to 0.70 in congestion. Exiter-related and in-section commands
    fall. The first breakdown moves later in two seeds (270 → 420 s,
    180 → 360 s), and the congested share falls in three of five
    [run, `cf_log_l300`]. Gaps formed farther upstream crowd the gore less.
    The lever is the reach, not less braking.
  - **Crossing volume.** It is an assumed split, and the model is steeply
    sensitive to it (§3.11).
- **For the measured model** (docs/MERGE_MODEL_READINESS.md):
  - The dead zone of §3.8 is in its gap choice too.
  - On the weave, principle (iii) did not lift capacity, alone (H3) or with
    the relaxed-T gap choice (H5). That is one more negative datum for the
    coupled-principles hypothesis on this section.

## 6. Recommendation

### 6.1 What not to do

- **Do not set `lookahead_m` to 200 or 300 m to recover throughput.**
  - It is an unmeasured engineering constant ("speed-match reach", 120 m).
  - Picking the value that comes closest to passing is the tuning the protocol
    forbids.
  - Even 300 m passes GEH at only 10 of 20 seeds and all criteria at 1.
- **Do not change the fixture's origin–destination split** to make the test
  pass. The split is unobserved either way.
- **Do not pursue further acceptance or cooperation rules for capacity.** Five
  such changes (H1, H2, H3, H5 and the knob screen) are now on record without
  a capacity effect above 7 %.

### 6.2 Proposed amendment W1: the entering give-up (opt-in)

*Implemented and judged against the criteria below on 2026-10-07 (§8). It
passes F1, F2 and F4 and fails F3 and F5, so the key stays off.*

**Rule.** An entrant still owing its change from the auxiliary lane into lane 1
takes the paired exit when all of these hold in a step:
- it has halted (below `HALTING_SPEED_MS`, 0.1 m/s);
- it is within `entrant_giveup_m` of the auxiliary lane's end, the exit gore;
- it has neither an accepted nor a guard-passing forced change that step.

It is then rerouted with `vehicle.changeTarget` to the off-ramp's last edge,
handed back, and counted in `n_missed` and a new `n_entrant_took_exit`.

**The key.** `entrant_giveup_m`: default 0 (off, hash-neutral, as the other
weave keys), opt-in value 5 m (one vehicle length, as `exit_giveup_m`).

**Why.**
- It mirrors two existing rules:
  - `exit_giveup_m`, the weave's exit give-up;
  - `lane_end_giveup_m`'s `n_took_exit`, which already reroutes "a vehicle
    bound elsewhere on an exit-only lane to the off-ramp's last edge" at every
    other diverge but skips weaving sections.
- A driver stopped at the end of an exit-only lane takes the exit. Real
  drivers do not stand there for up to a minute (62.5 s in the baseline)
  blocking every exiter behind them.

**Expected effect.** Small: the counterfactual gave +32 [+8, +57], about 7 % of
the loss. **It is a fidelity fix, not the capacity fix.**

**Acceptance criteria, fixed now, before implementation and before any
corridor run.** If a criterion fails, the key stays off and the result is
reported; no criterion is re-thresholded.

Fixture (macOS; calibrated drivers; seeds 3–22; the locked section test
through `merge_model_selfcheck.py th52 --model weave --fleet-from …` with the
key at 5 m, paired against the same command without it):

| # | criterion |
|---|---|
| F1 | Exit-end flow gain with a 95 % lower bound above 0 |
| F2 | Stranded-entrant time at most half of the reference: ≤ 9.5 s per run (the reference is 18.9). Definition: the auxiliary lane's front vehicle is below 0.5 m/s within 15 m of its end and owes its change (the `strand.py` definition) |
| F3 | Entrants taking the exit ≤ 1 % of the entrance's departures at every seed |
| F4 | Zero collisions. −9 m/s² vehicle-steps ≤ the reference + 2 over the 20 seeds. Given-up exits ≤ the reference (66) |
| F5 | The 29-run grid and the G0 rows (`merge_model_selfcheck.py grid --model weave`, the key set on every weave): zero collisions, no lock, entrants taking the exit ≤ 1 % per run |

Corridor (cloud, later; the I-94 four-hour battery of 20 seeds on the
reference configuration with the key on both weaves, paired against step 3's
reference battery on the same seeds and drivers):

| # | criterion |
|---|---|
| C1 | S790 06:30–07:30 not lower: paired 95 % upper bound ≥ 0 |
| C2 | Departed share not lower: upper bound ≥ 0 |
| C3 | Zero collisions |
| C4 | No lock: the lowest departed share ≥ 0.8 × the median |
| C5 | Entrants taking the exit ≤ 1 % of each weave entrance's departures (T.H.52 and Ruth St) |

### 6.3 Measure the two inputs before any further capacity work

**M1, the anticipation reach.**
- **The problem.** `lookahead_m` sets two things at once: the ramp
  anticipation, and the reach of the follower search. The ramp anticipation is
  what moves the result (§5).
- **The extraction.** From the I-24 MOTION trajectories (cloud only), for
  entering changes at Old Hickory and the HH–BR weave: the distance before the
  gore, or before the change, at which the entrant's speed first tracks its
  future gap. Operationally: the last time before the change from which the
  entrant's speed stays within ±1 m/s of its future leader's, and the distance
  it covered since then.
- **The decision rule, fixed now.**
  - Adopt only a measured median, as a dated amendment, judged by F1 and F4.
  - With no extraction, `lookahead_m` stays at 120 m.

**M2, the ramp-to-ramp share at T.H.52** (US 52 NB to exit 242B).
- Detector counts cannot give it.
- Until a source exists, carry it as an uncertain input: sample the share
  between the proportional split (about 29 %) and 50 % in the protocol's
  uncertainty design (`validation.uncertainty`), and report the T.H.52 and
  S790 results conditional on it.
- The corridor shortfall at T.H.52 should not be attributed to the merge model
  until that bound is known. On the fixture, the model meets GEH < 5 at 18
  of 20 seeds at 75 % of the proportional crossing volume, and carries the
  full demand at 50 %.

### 6.4 Recorded, not proposed

**The dead zone of §3.8** is a real blind spot in the shared gap choice:
- 20 % of changers spend at least 2 s there;
- it accounts for 64 % of the end-of-section halts.

Removing it (H1) moved the first halt 7–13 minutes later in three of five
seeds but changed nothing in flow, speed or safety. It is recorded here for whoever
next revises the gap choice, of the weave or of `measured`, and no amendment
is proposed for it.

## 7. Limits

- **Fixture and platform.**
  - One fixture: 20 minutes, the 05:30–05:50 demand.
  - macOS.
  - The corridor's T.H.52 runs inside a longer chain and with `exit_prepare`
    on. On this fixture that configuration reads the same as the default
    (+9 [−91, +108]).
- **Counterfactuals.** Scratch implementations of rules that do not exist in
  the package. A package implementation must be re-measured under its own
  criteria (§6.2).
- **D1's relocation is random per vehicle.** It does not move exactly equal
  counts from each crossing movement, so its mapping to a ramp-to-ramp share
  is an estimate.
- **I-24 comparisons** (§3.7) are subject to the partial-coverage bounds of
  MERGE_MODEL_BRIEF.
- **Not traced:**
  - the collision in D1 at p = 0.25;
  - the collision in H2+H3+H5.

## 8. W1 implemented and evaluated (2026-10-07)

Amendment W1 (§6.2) was implemented as an opt-in key and judged on the
fixture criteria F1–F5 exactly as pre-registered. Labels as in §1; **[run]**
rows are in `artifacts/weave_loss_2026-10-07/w1/`.

### 8.0 In plain English

- **The rule works as intended.** An entrant stuck at the end of the
  auxiliary lane now takes the exit. Stranded-entrant time falls from
  18.9 to 3.8 s per run, and flow rises by 29 veh/h (95 % CI 10–48).
- **Off changes nothing.** With the key unset, every golden, every committed
  scenario hash and 37 fixture runs are byte-identical to HEAD.
- **It fails two of the five criteria fixed in advance.**
  - **F3.** At one seed of 20, 4 of 386 entrance departures (1.04 %) took
    the exit. The cap is 1 % at every seed.
  - **F5.** In 8 of 37 grid runs, all on the Ruth St fixtures (whose
    entrance departs only 73 or 128 vehicles), 1–5 entrants took the exit
    (1.4–6.8 %). The cap is 1 % per run.
- **So the key stays off.** No committed scenario sets it, and no criterion
  was re-thresholded. Adoption, a revised criterion or the corridor rounds
  are the owner's decision.

### 8.1 What was implemented

- **Config** (`flowstate_core.config`).
  - `entrant_giveup_m` is the one key of a new `WEAVE_OPTIONAL_KEYS`. It is
    accepted in `WeaveSpec.weave_params`, has no default, and must be ≥ 0.
    Unset or 0 means off.
  - **Deviation from §6.2's wording.** §6.2 said "default 0". The key is not
    in `WEAVE_DEFAULTS`, because `tests/golden/config_defaults.json` pins that
    table. A key added there with any value would change a golden. The
    behaviour is the same: off unless set to a positive distance.
- **Runner** (`microsim.runner._weave_step`).
  - **When it fires.** The rule of §6.2 is applied in the step loop next to
    the exit give-up, after the acceptance has been read. An entrant must
    still owe its change and be on lane 0 of an exit-only edge. It must be
    halted (below `HALTING_SPEED_MS`) within `entrant_giveup_m` of the gore,
    with neither an accepted nor a guard-passing forced change that step.
  - **What it does.** `vehicle.changeTarget` sends the entrant to the
    off-ramp's last edge (`RampSpec.edges[-1]`). Its lane-change mode is
    restored and it is handed back. It is counted in `n_missed` and the new
    `n_entrant_took_exit`. It is never taken under control again (`took_exit`).
- **Records.**
  - `meta.json["weave_sections"][i]["n_entrant_took_exit"]` is written only
    when the rule is on, right after `n_missed_exit`.
  - `vehicles.parquet` marks the entrant `gave_up`, with `gave_up_s`. Its
    `destination_final` is the exit's label; its `destination` keeps the plan.
- **How it differs from the H2 counterfactual (§4).**
  - H2 ran after the step, so its cooperation commands for that step had
    already been issued. W1 skips them, as the exit give-up does.
  - H2 used `sorted(exit_edges)[-1]` as the target. W1 uses the ramp's last
    edge.
  - H2 did not exclude a rerouted entrant from being taken again on the next
    step. W1 does.
- **Script.** `scripts/merge_model_selfcheck.py` has a new
  `--weave-set KEY=VALUE` for `grid`, `th52` and `ceiling`. It sets the key on
  every weave block, with `--model weave` only. `th52` rows gain
  `entrants_took_exit`, and the `grid` zone rows gain `n_entrant_took_exit`.
- **Tests.**
  - `TestWeaveEntrantGiveup` in
    `tests/test_microsim/test_microsim_merge_managed_meter.py`, on the fake
    harness: a stranded entrant takes the exit and is not driven again.
  - Unset and 0 change nothing; still rolling or 6 m back is kept; a halted
    entrant that can change does; the schema.
  - One SUMO test on the T.H.52 fixture with the calibrated drivers, seeds 4
    and 15: entrants are rerouted and recorded. Unset writes no counter and
    reroutes nobody. 0 is byte-identical to unset.

### 8.2 Off is byte-identical [run]

**Method.** The method of docs/PERFORMANCE_2026-10-07.md: two `git archive HEAD`
trees (`daf8826`), the second with only `config.py` and `runner.py` copied in.
Each was run through `harness/py.sh` (its packages first on `sys.path`), with
the case configs taken from the HEAD tree in both legs.

**Compared per run:**
- the sha256 of every Parquet file;
- `meta.json` without `wall_time_s` and `realtime_factor`;
- the `compute_metrics` output;
- the config hash.

**Cases (37):**
- the 12 micro golden cases;
- the T.H.52 corridor fixture (seeds 3, 4), capacity (4, 5), corridor demand,
  upstream and upstream-with-fleet;
- Ruth St in five forms;
- the moderate and golden weave fixtures;
- McKnight Rd (seeds 3–5), T.H.61, the scripted merge golden;
- the T.H.52 section with the calibrated drivers (seeds 3–5);
- the measured model on T.H.52, Ruth St and McKnight.

**Result.**
- **37 of 37 identical** (`identity_head.json`, `identity_change.json`).
- **The 33 committed scenarios hash the same** in both trees
  (`scenario_hashes_*.json`), and `WEAVE_DEFAULTS` is unchanged.
- **The section test's reference arm reproduces §2** (`arms/base.json`) field
  for field at seeds 3–22, stranded time included.

### 8.3 F1–F4: the T.H.52 section test, seeds 3–22 [run]

**Command.** `merge_model_selfcheck.py th52 --model weave --fleet-from
scenarios/mndot_i94_wb_stpaul_weave_dc.yaml`, once with
`--weave-set entrant_giveup_m=5` and once without, at the same seeds.

**Readouts.** Stranded time is `harness/strand.py`'s definition (F2). Locks are
`run_summary`'s flag. Paired t-intervals, 19 degrees of freedom; macOS.

| | without (reference) | with W1 | W1 − reference, paired [95 %] |
|---|---|---|---|
| exit-end flow, veh/h (mean ± sd) | 4,361 ± 83 | 4,390 ± 77 | **+29.0 [+9.7, +48.3]** |
| lowest station speed, m/s | 17.07 | 17.59 | +0.52 [−0.10, +1.15] |
| entrance departed share | 0.951 | 0.963 | +0.011 [+0.000, +0.023] |
| mainline departed share | 0.9995 | 0.9999 | +0.0004 [−0.0002, +0.0010] |
| stranded-entrant time, s per run | 18.9 | **3.8** | −15.05 [−22.30, −7.80] |
| longest single stand, s | 50.5 | 5.0 | |
| entrants taking the exit (20 seeds) | — | 27 (0–4 per seed; 0.34 % of 7,837) | |
| highest per-seed share of entrance departures | — | **1.04 %** (seed 15: 4 / 386) | |
| exits given up (20 seeds) | 66 of 8,334 | 62 of 8,370 | |
| collisions | 0 | 0 | |
| −9 m/s² vehicle-steps (20 seeds) | 2 | 0 | |
| locks | 0 | 0 | |
| GEH < 5 / all criteria | 1 / 0 of 20 | 2 / 0 of 20 | |

**Per-seed shares of entrance departures.**
- Seed 15: 1.04 %.
- Seed 20: 0.81 % (3 of 370).
- Seeds 3, 12, 16, 21: 0.49–0.54 % (2 each).
- Elsewhere: 0–0.27 %.

**Who took the exit.** Of the 27 entrants:
- 24 were bound for the corridor's end and 3 for the Jackson exit;
- none was a ramp-to-exit vehicle;
- 26 left the network by the exit; one, given up at 1,199 s (seed 20), was
  still in the network when the run ended.

**Seed 15.** Two of its four were consecutive entrants, v01283 and v01284,
given up 9.5 s apart. It is the seed with the longest stand in the reference
(62.5 s).

**Against the counterfactual.** The package rule reads like H2: +29 [+10, +48]
against +32 [+8, +57], 27 entrants against 29, and 3.8 s per run against 4.2.

### 8.4 F5: the fixture grid with the key on every weave [run]

**Command.** `merge_model_selfcheck.py grid --model weave --weave-set
entrant_giveup_m=5` covers the 29 runs of the weave record and the 8 G0 rows.
The same grid was run without the key for reference. The share is of the
weave entrance's departures in that run.

| | without | with W1 |
|---|---|---|
| runs with a collision | 0 of 37 | **0 of 37** |
| runs flagged as a lock | 2: `merge_golden`; `ruth_entr_fleet` s5 | **1: `merge_golden`** |
| runs with entrants taking the exit above 1 % | — | **8 of 37** |
| entrants taking the exit, total | — | 28 |
| exits given up, total | 78 | 64 |
| vehicles departed, total | 48,081 | 48,161 |
| runs in which no entrant took the exit (4 of them have no weave) | | 22 |

**About the remaining lock flag.**
- `merge_golden` is the scripted merge golden. It has no weave block, so the
  key cannot reach it, and the run is the same in both arms.
- It is flagged by `run_summary`'s rule that more than 10 % of entered
  vehicles are unfinished: 10 of 45 are still on the acceleration lane at
  300 s.
- The flag predates W1.

**The rows above 1 %.** All are on Ruth St. The T.H.52 rows stay at or below
0.6 %.

| fixture | seed | entrants took the exit / entrance departed | share | exits given up, without → with | lowest zone minute [m/s], without → with |
|---|---|---|---|---|---|
| `ruth_exit` | 3 | 1 / 73 | 1.37 % | 1 → 1 | 16.6 → 16.6 |
| `ruth_entr_fleet` | 3 | 2 / 128 | 1.56 % | 1 → 0 | 13.1 → 16.7 |
| `ruth_exit_fleet` | 3 | 3 / 73 | 4.11 % | 12 → 6 | 6.6 → 12.1 |
| `ruth_exit_fleet` | 4 | 1 / 73 | 1.37 % | 2 → 3 | 10.7 → 10.6 |
| `ruth_exit_fleet` | 5 | 3 / 73 | 4.11 % | 4 → 4 | 13.4 → 12.2 |
| `ruth_exit_fleet_271` | 3 | 5 / 73 | 6.85 % | 7 → 8 | 8.1 → 9.6 |
| `ruth_exit_fleet_271` | 4 | 2 / 73 | 2.74 % | 2 → 5 | 9.5 → 9.6 |
| `ruth_exit_fleet_271` | 5 | 2 / 73 | 2.74 % | 14 → 3 | 5.9 → 12.8 |

Read beside the cap:
- On these short-section, small-entrance fixtures one entrant is 1.4 %.
  Neither the record nor these runs say whether that rate is unrealistic.
- The same runs give up fewer exits overall (Ruth St: 45 → 31).
- They clear the reference's one weave lock (`ruth_entr_fleet` s5, lowest
  minute 8.9 → 18.2 m/s).
- The T.H.52 corridor-fleet G0 row moves both ways. At seed 4 its lowest
  minute fell from 10.9 to 6.6 m/s and its given-up exits rose from 1 to 4.

### 8.5 Verdict, criterion by criterion (as pre-registered in §6.2)

| # | criterion | result | verdict |
|---|---|---|---|
| F1 | exit-end flow gain, 95 % lower bound above 0 | +29.0 [+9.7, +48.3] | **pass** |
| F2 | stranded-entrant time ≤ 9.5 s per run | 3.8 s | **pass** |
| F3 | entrants taking the exit ≤ 1 % of entrance departures at every seed | 1.04 % at seed 15; ≤ 0.81 % at the other 19 | **fail** |
| F4 | zero collisions; −9 m/s² steps ≤ reference + 2 (≤ 4); given-up exits ≤ the reference (66) | 0; 0; 62 | **pass** |
| F5 | grid and G0 rows: zero collisions, no lock, entrants taking the exit ≤ 1 % per run | 0 collisions; one lock flag (`merge_golden`, no weave, the same run without the key); above 1 % in 8 of 37 runs, all Ruth St | **fail** |

**Outcome.** Two fixture criteria fail. Under §6.2's rule the key stays off
and nothing is re-thresholded.

**What the pass criteria show.**
- W1 does what it was built to do: it removes the exit-lane blockage, with no
  safety cost.
- Its capacity effect is small, as §6.2 expected: about 6 % of the 465 veh/h
  loss.

**What the failures are.** Both concern how often the rule fires on small
entrances. Neither concerns safety or flow.

### 8.6 What the corridor criteria C1–C5 would need (cloud; not run)

Under §6.2, the failure of F3 and F5 already keeps the key off. The corridor
round would be run only if the owner revisits that decision. It needs:

- **A scenario variant.** `scenarios/mndot_i94_wb_stpaul_weave_dc.yaml` (the
  xlsfg reference configuration of stage `p1_mndot_ref` with the Amendment-1
  drivers; config hash `db9fbab5fc6e`), with
  `weave_params: {exit_prepare: 1, entrant_giveup_m: 5}` on both weave
  entrances:
  - `on-ramp 769818012`, T.H.52 → `off-ramp 18207598`;
  - `on-ramp 745524613`, Ruth St → `C-D split 18208090`.

  Written as an uncommitted variant; its config hash differs from the
  committed one.
- **A paired battery.** The four-hour, 20-seed battery on the same seeds and
  drivers as step 3's reference battery, paired seed by seed. One
  n2-standard-32 runs all 20 seeds in parallel, at about $0.2–1.2 for the
  simulation phase (docs/PERFORMANCE_2026-10-07.md §5).
- **Readouts:**
  - C1: S790 06:30–07:30, paired 95 % upper bound ≥ 0.
  - C2: departed share, paired upper bound ≥ 0.
  - C3: zero collisions.
  - C4: lowest departed share ≥ 0.8 × the median.
  - C5: per weave, `weave_sections[i].n_entrant_took_exit` over that
    entrance's `ramps[k].n_departed` ≤ 1 %, at T.H.52 and Ruth St, read from
    each run's `meta.json`.
- **A tooling gap for C5.** Neither `scripts/corridor_battery.py` nor
  `scripts/corridor_sweep.py` aggregates `n_entrant_took_exit` yet. It was
  not added, because adoption is a separate decision.

### 8.7 Reproduce

From the repository root, with `$W` a scratch directory:

```sh
uv run --no-sync python scripts/merge_model_selfcheck.py th52 --model weave \
    --fleet-from scenarios/mndot_i94_wb_stpaul_weave_dc.yaml --seeds 3-22 \
    --keep --work-dir $W/ref --out $W/ref.json
uv run --no-sync python scripts/merge_model_selfcheck.py th52 --model weave \
    --fleet-from scenarios/mndot_i94_wb_stpaul_weave_dc.yaml --seeds 3-22 \
    --weave-set entrant_giveup_m=5 --keep --work-dir $W/w1 --out $W/w1.json
uv run --no-sync python artifacts/weave_loss_2026-10-07/w1/harness/eval_post.py $W/ref $W/ref_post.json
uv run --no-sync python artifacts/weave_loss_2026-10-07/w1/harness/eval_post.py $W/w1 $W/w1_post.json
uv run --no-sync python artifacts/weave_loss_2026-10-07/w1/harness/eval_stats.py $W   # F1-F4
uv run --no-sync python scripts/merge_model_selfcheck.py grid --model weave \
    --weave-set entrant_giveup_m=5 --out $W/grid_w1.json                              # F5
```

**Identity runs.** `harness/py.sh TREE harness/cmp.py HEAD_TREE OUT WORK` runs
them once per tree, and `harness/hashes.py` hashes the scenarios. Each tree is
a `git archive HEAD` extraction, the second with the two changed files copied
in.

## 9. Anticipation measured on I-24 — 2026-10-07

docs/MERGE_ANTICIPATION.md's measurement ran (`artifacts/merge_anticipation_i24.json`): the pre-registered
estimate is 125 m (95 % interval 107–154 m, 858 entering changes at the Hickory Hollow weave and Old Hickory,
≥ 20 m/s), which rounds to the model's 120 m. The `lookahead_m` route to the missing ~450 veh/h is closed by
measurement. The remaining named input is the ramp-to-ramp crossing share at T.H.52 (an assumed proportional
split); bounding it from data is the next step.
