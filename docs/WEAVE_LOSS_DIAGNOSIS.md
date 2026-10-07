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
    - **Amendment W1b (§10): the same give-up, but only after a 60-s stand.**
      It is aimed at the permanent lock of docs/I94_COLLAPSE_DIAGNOSIS.md.
      - It was pre-registered, implemented opt-in (off is byte-identical) and
        passed every fixture criterion.
      - It released the one fixture lock found: Ruth St, seed 15, with the
        calibrated drivers. Without it the lock stood 16 minutes, to the end
        of the run.
      - It also ended five ordinary Ruth St stands of 61.5–68.5 s, at
        0.09–0.14 % of entrance departures.
      - Adoption needs the corridor round (§10.11, written, not launched) and
        the owner's decision. The key stays off.
      - **Superseded (2026-10-07):** the corridor round ran (stage p9, §11)
        and passed every pre-registered criterion: locked runs 3 of 20 → 0 of
        20 (read with §11's note on lock counting), no collision, no
        measurable throughput cost. The key stays off until the owner adopts it.

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

## 10. Amendment W1b: a lock release after a dwell (pre-registered 2026-10-07 05:42 CDT)

*Written before any W1b code existed and before any W1b or reference run of
this round. Evidence used: the committed W1 artifacts
(`artifacts/weave_loss_2026-10-07/w1/`) and docs/I94_COLLAPSE_DIAGNOSIS.md.
The results are appended below this registration and do not change it.*

### 10.1 Why W1b

- **The lock.** docs/I94_COLLAPSE_DIAGNOSIS.md found that every I-94 collapse
  read so far is a permanent standstill at a weaving gore. Its front vehicle
  is a through-bound entrant at rest 0.05–0.08 m from the end of the
  auxiliary lane, and nothing releases it. It occurred in 3 of 20 four-hour
  replicates with the calibrated drivers.
- **W1 removes that vehicle, but too often.** W1 reroutes an entrant to the
  exit at the first step it is halted at the lane's end with no change to
  request. On the fixtures that also caught ordinary stands that clear by
  themselves, so it failed F3 and F5 on frequency (§8.5).
- **W1b fires only on long stands.** It keeps W1's action and adds a dwell:
  the entrant must have stood at the lane's end for at least T_dwell. A lock
  stands until its run ends: at least 13.6–52.5 minutes in the three four-hour
  replicates (10.3). The fixture's ordinary stands, all of which cleared by
  themselves, last at most about 50 s. So the rule is meant to change nothing
  in normal operation and to release every lock.

### 10.2 The rule

- **New key.** `entrant_giveup_dwell_s` [s], a second member of
  `WEAVE_OPTIONAL_KEYS`. It has no default and must be ≥ 0. Unset or 0 leaves
  W1 exactly as it is. A positive value needs `entrant_giveup_m` > 0, and the
  config is refused otherwise, so the key can never be silently inert.
- **Dwell clock.** It runs per entrant under the weave's control that still
  owes its change from the auxiliary lane into lane 1.
  - It starts at the first step on which the entrant is halted (below
    `HALTING_SPEED_MS`, 0.1 m/s) within `entrant_giveup_m` of the auxiliary
    lane's end (the exit gore).
  - It resets on any step on which the entrant is at or above the halting
    speed, or farther than `entrant_giveup_m` from the end.
  - It measures uninterrupted standstill at the lane's end. That is the
    quantity T_dwell is chosen from (10.3), read from trajectories, so it is
    not stopped by a step on which a change was requested. A requested change
    that executes ends the stand anyway.
- **Release.** The entrant takes the paired exit on a step on which:
  - the clock has run for at least `entrant_giveup_dwell_s` (t − start ≥
    T_dwell); and
  - W1's own condition holds that step: halted, within `entrant_giveup_m` of
    the end, with neither an accepted nor a guard-passing forced change. An
    entrant that can request its change on that step tries the change, not
    the exit.
- **Action and records.** Exactly W1's:
  - `vehicle.changeTarget` to the off-ramp's last edge, the lane-change mode
    restored, handed back, never taken under control again;
  - counted in `n_missed` and `n_entrant_took_exit`;
  - marked `gave_up` in `vehicles.parquet`.

  No new counter. The dwell is recorded in `meta.json["weave_sections"][i]
  ["params"]`.
- **Off is byte-identical.** With the dwell key unset, nothing of it runs and
  nothing is written.
- **The W1b setting.** `entrant_giveup_m` 5 (W1's value) with
  `entrant_giveup_dwell_s` 60.
- **Not included: an exit-side release** for an exiter halted at the front of
  a through lane 5–11 m short of the gore, beyond `exit_giveup_m`.
  - The diagnosis does find one at lane 1's front in every lock (Table 4).
    But it reads that exiter as waiting for a lane that never moves, and it
    proposes no exit-side rule until the trajectories show that the exiter
    stays stuck after the auxiliary lane moves (§7.3 there).
  - The fixture lock definition below (10.4) includes through-lane fronts.
    So if W1b releases an entrant and the lock persists with an exiter at
    the front, that shows up as a lock in the W1b arm, with the front vehicle
    named.

### 10.3 T_dwell = 60 s, and its basis

- **The fixture's ordinary stands** [artifact, `w1/th52_ref_post.json`]. These
  are the T.H.52 section test with the calibrated drivers, seeds 3–22, and
  W1's reference arm. A stand is `strand.py`'s definition: the front of lane 0
  within 15 m of its end, below 0.5 m/s, a through-bound entrant.
  - The longest single stand per seed, in seconds, sorted: 0, 0, 5.0, 6.0,
    6.0, 7.5, 8.0, 9.0, 10.0, 10.5, 13.5, 15.5, 15.5, 17.0, 18.0, 20.0, 20.5,
    29.0, 33.5, **50.5** (seed 4).
  - Every one of them cleared by itself: no lock, and lowest zone minute
    ≥ 6.6 m/s.
  - `strand.py`'s definition is wider than the rule's (15 m and 0.5 m/s
    against 5 m and 0.1 m/s). So a stand by the rule's definition is no
    longer than these.
- **The locks.** The front entrant stands from the lock's onset to the end of
  the run: at least 42.5, 52.5 and 13.6 minutes in the three four-hour
  replicates (last vehicle through the gore at 08:47.5, 08:37.5 and 09:16.4;
  the runs end at t = 14,400 s, which is 09:30 with t = 0 at 05:30). These
  are lower bounds, because each lock still stood when its run ended
  (docs/I94_COLLAPSE_DIAGNOSIS.md §10, the lock detector's run-end reader).
- **The choice.** Any T_dwell between about 51 s and about 13 minutes
  separates the two on this record. 60 s does, with the shortest observed
  lock (at least 13.6 minutes) more than thirteen times longer.
  60 s is the smallest whole minute above every ordinary stand on record,
  with about 10 s of margin over 50.5 s.
  - A shorter dwell would release ordinary stands. The 20–30 s range
    suggested when this round was set up would have acted at 2 to 5 of the 20
    reference seeds.
  - A longer dwell lets the lock spread further before it is released. The
    auxiliary lane stops first and the through lanes stop over minutes as
    exiters become their fronts (the diagnosis's §2).
  - 60 s is not fitted to any outcome. Its only input is the reference stands
    above.
- *Corrected 2026-10-07 (regression review).* The first version of 10.1 and
  this section said that a lock "stands for hours", "about 1.2–1.9 h …
  runs end at 10:30", and that any T_dwell "between about 51 s and an hour"
  separates the two. The four-hour I-94 runs end at 09:30, not 10:30, so the
  observed stands are 13.6–52.5 minutes to the run's end and the separating
  band ends at about 13 minutes. The choice of 60 s is unaffected. The frozen
  pre-registration (`artifacts/weave_loss_2026-10-07/w1b/prereg.md`) keeps
  the original wording.
- **Its limit.** The basis is 20 seeds × 20 min of one fixture. A rarer
  ordinary stand longer than 60 s would be released. F3b/F5b count such
  releases on the fixtures, and C5b counts them on the corridor.

### 10.4 Evaluation sets and definitions

**Arms.** Each set is run twice at the same seeds:
- reference: no key;
- W1b: `--weave-set entrant_giveup_m=5 --weave-set entrant_giveup_dwell_s=60`.

Trajectories are kept for the readers. macOS.

**Sets:**

| set | what | runs per arm |
|---|---|---|
| S1 | The T.H.52 section test with the calibrated drivers, seeds 3–22: `merge_model_selfcheck.py th52 --model weave --fleet-from scenarios/mndot_i94_wb_stpaul_weave_dc.yaml` (W1's F1–F4 set) | 20 |
| S2 | W1's 37-run grid with the fixtures' own fleets: `merge_model_selfcheck.py grid --model weave` (W1's F5 set) | 37 |
| S3 | The calibrated drivers on every grid fixture with a weave block, through a new `grid --fleet-from` option. Ruth St fixtures at seeds 3–22; the others at the grid's seeds. A run whose config, after its fleet is replaced, hashes the same as another run's in the set is run once (the `_fleet` twins) | ≈ 75 |

**Definitions:**
- **Gore.** The end of the weave section's last edge on the corridor axis
  (1,134.09 m on the T.H.52 fixture, as in `strand.py`).
- **Entrant.** A vehicle from the weave's on-ramp not planned for the paired
  exit.
- **Stand (the rule's definition).** An entrant in section lane 0 with
  v < 0.1 m/s and x ≥ gore − 5 m on consecutive 0.5-s samples. Its length is
  last sample − first sample.
- **Lock (fixture form of I94_COLLAPSE_DIAGNOSIS §7.1).** After the 120-s
  warm-up, some vehicle is, for at least 120 s without a break:
  - the front vehicle of a section lane (any lane);
  - within 15 m of the gore;
  - below 0.1 m/s.

  Why these values:
  - 120 s is twice T_dwell and more than twice the longest ordinary stand.
  - 15 m reaches lane 1's front exiter in all three corridor locks
    (5.0–10.8 m short).

  `run_summary`'s lock flag is reported beside it, as in §8.4.
- **Lock-prone run.** A run of S1–S3 whose reference arm has a lock.
- **Releases.** `n_entrant_took_exit` in the W1b arm.

### 10.5 Acceptance criteria, fixed now

If any criterion fails, the key stays off and the failure is reported. No
criterion is re-thresholded.

| # | criterion | relation to §6.2 |
|---|---|---|
| B0 | The rule waits for the dwell. (a) Every entrant the rule reroutes, in every W1b run, had a stand ending at its `gave_up_s` of ≥ 59.5 s (60 s less one sample). (b) No stand in any W1b run lasts longer than 61 s | new; replaces F2 |
| B1 | Inert until it fires. Every W1b run in which the rule never fires is identical to its reference: sha256 of every Parquet file; `meta.json` without wall time, `config_hash` and the weave `params` | new |
| F1b | S1: exit-end flow not lower. Paired W1b − reference, 95 % upper bound ≥ 0 | replaces F1 |
| F3b | Entrants taking the exit ≤ 1 % of the weave entrance's departures, pooled over seeds. S1 over its 20 seeds. In S2 and S3, pooled per weave section over the set's runs of it: Ruth St, T.H.52 (every `th52_*` fixture), T.H.61, the moderate and golden fixtures | replaces F3 and F5's per-run cap |
| F4 | S1: zero collisions; −9 m/s² vehicle-steps ≤ reference + 2; given-up exits ≤ reference | unchanged |
| F5b | S2 and S3: zero collisions. No W1b run has a lock, or a `run_summary` lock flag at a weave section, unless its reference has one | F5 without its frequency cap (now F3b) |
| L1 | Zero locks in the W1b arm on every lock-prone run. If no reference run locks, L1 is **not testable on the fixtures** and is reported as such | new |

**Reasons for each change:**

- **F1 to F1b.** W1b is built not to act below 60 s, and the reference stands
  on S1 are all shorter. So no flow gain is expected there, and a criterion
  that asks for one would test the wrong thing. The question for S1 is
  whether W1b costs anything.
- **F2 to B0.** W1b deliberately leaves stands under 60 s alone, so halving
  stranded time (F2) is not its aim. B0 checks instead that it acts after the
  dwell, and only then. Stranded time per run is still reported.
- **F3 and F5's per-run cap to F3b, pooled over seeds.**
  - On a Ruth St fixture whose entrance departs 73 vehicles, one release is
    1.37 %. A per-run cap of 1 % therefore forbids any release at all on such
    a section, which is where two of the three corridor locks formed.
  - Pooling over seeds measures the rate the cap was meant to bound. With 20
    seeds of S1, 1 % is about 78 entrants.
  - The pooled cap is no looser in intent. For W1b, each release also
    requires a minute's standstill.
- **F5's "no lock" to F5b plus L1.** W1's reference grid already carried a lock flag
  at a weave section (`ruth_entr_fleet` s5, `run_summary`'s unfinished-share
  rule). Only a lock that W1b creates counts against it (F5b). A lock it
  fails to release counts in L1.

**Predictions, registered, not criteria:**
- **S1.** No reference stand reaches 60 s. So the rule never fires, every W1b
  run equals its reference, and B1, F1b, F3b and F4 hold trivially.
- **S2/S3.** Few or no releases. Ruth St's stands are unmeasured, and any
  releases are expected there.
- **L1.** The fixtures may contain no lock at all. W1 found no lock by the
  diagnosis's definition on S1 and had only `run_summary`'s weaker flag on
  S2.

**What passing means.** Passing every fixture criterion does not adopt W1b.
Its purpose, releasing a lock, can be shown only where locks occur. If the
fixtures have none, that is the corridor (10.6), and adoption is the owner's
decision.

### 10.6 Corridor criteria, fixed now (cloud; run only on the owner's decision)

**The runs.** Two arms in one stage, paired seed by seed on step 3's 20 seeds:
- **Reference.** `scenarios/mndot_i94_wb_stpaul_weave_dc.yaml` (hash
  `db9fbab5fc6e`).
- **W1b.** The same scenario with `entrant_giveup_m: 5.0` and
  `entrant_giveup_dwell_s: 60.0` added to both weaves' `weave_params`
  (`exit_prepare: 1.0` kept).
- **Reproduction check.** The reference arm is re-run so that both arms use
  the same code. It is checked against step 3's committed per-seed departed
  shares.

| # | criterion |
|---|---|
| C1 | S790 06:30–07:30 not lower: paired 95 % upper bound ≥ 0 |
| C2 | Departed share not lower: paired upper bound ≥ 0 |
| C3 | Zero collisions |
| C4b | **Zero locks** at T.H.52 and Ruth St over the 20 W1b replicates. The reference count is reported beside it (3 of 20 if it reproduces step 3). Replaces C4, which passes every lock seen (I94_COLLAPSE_DIAGNOSIS §5). The definition is the diagnosis's Table 4 reading, extended to any lane front. A section is locked at the run's end when no vehicle stands between its gore and the next entrance downstream (or the corridor's end), and a vehicle stands at rest at the front of a section lane within 15 m of the gore. Read from each replicate's `vehicles.parquet` (vehicles not arrived, last sample at the run's end) |
| C5b | Entrants taking the exit ≤ 1 % of that weave entrance's departures, pooled over the 20 seeds, at T.H.52 and at Ruth St (`n_entrant_took_exit` over `ramps[k].n_departed`, from each `meta.json`) |

**Clarification of B1 (2026-10-07 05:43 CDT).** Written before any W1b code
or run, after re-reading W1's implementation.
- **What else B1's `meta.json` comparison drops.** Besides wall time,
  `config_hash` and the weave `params`, it drops:
  - `weave_sections[i].n_entrant_took_exit`. W1's code writes this counter
    whenever `entrant_giveup_m` is set, so in a W1b run that never fires it
    reads 0 and the reference has no such key.
  - The run-directory path strings, which contain the config hash.
- **Which weave `params`.** Both copies:
  `meta["config"]…["weave"]["weave_params"]` and
  `weave_sections[i]["params"]`.
- **Nothing else is excluded.**

### 10.7 What was implemented (2026-10-07, after the registration)

- **Config** (`flowstate_core.config`).
  - `entrant_giveup_dwell_s` is the second member of `WEAVE_OPTIONAL_KEYS`.
  - A negative value is refused, and so is a positive value without
    `entrant_giveup_m` > 0.
  - It is not in `WEAVE_DEFAULTS`, so `tests/golden/config_defaults.json` is
    unchanged.
- **Runner** (`microsim.runner._weave_step`, new `_weave_entrant_giveup_dwell_s`).
  - Each controlled entrant's state carries `halt_since`, the clock of §10.2.
    It is kept only when the dwell is on.
  - W1's release condition gains `t − halt_since ≥ T_dwell`, with a 10⁻⁶ s
    tolerance (`_WEAVE_DWELL_EPS_S`), so a dwell of a whole number of steps
    fires on the step that completes it.
  - Nothing else changed: the same action, counters and records as W1.
- **Script** (`scripts/merge_model_selfcheck.py`). `grid` gains `--fleet-from`
  and `--seeds`, which S3 needed. Within one call, a config that hashes as an
  earlier one is run once and listed in `skipped_same_config`. Without the two
  options the grid runs as before; S2's reference reproduces W1's
  `grid_ref.json` field for field (§10.9).
- **Tests.** `TestWeaveEntrantGiveupDwell` in
  `tests/test_microsim/test_microsim_merge_managed_meter.py`. On the fake
  harness it checks:
  - the stand is kept at 59.5 s and released at 60 s;
  - rolling, or standing 6 m back, resets the clock;
  - after the dwell, an entrant that can change changes, and its clock runs
    through the requests;
  - unset or 0 is W1, with no clock kept;
  - the schema.

  One SUMO test on the T.H.52 section with the calibrated drivers, seed 4:
  - a 10-s dwell (a mechanism check) releases entrants only after 10 s of
    standstill, read from the trajectories;
  - a dwell of 0 is byte-identical to W1 alone.
- **Harness.** `artifacts/weave_loss_2026-10-07/w1b/harness/`:
  - `post.py` reads each run: stands, releases, lane-front locks, B1 digests;
  - `eval.py` evaluates the criteria;
  - `releases.py` compares each release with the same entrant in the
    reference;
  - `corridor_w1b.py` is the corridor readout of §10.6.

### 10.8 Off is byte-identical [run]

**Method.** As §8.2:
- Two `git archive HEAD` trees (`84a272e`), the second with only `config.py`
  and `runner.py` copied in.
- W1's `harness/cmp.py` was run in each tree through `harness/py.sh` (each
  tree's packages first on `sys.path`), with the case configs from the HEAD
  tree.
- W1's `harness/hashes.py` hashed the committed scenarios.

**Result** (`w1b/identity_head.json`, `w1b/identity_change.json`,
`w1b/scenario_hashes_*.json`):
- **37 of 37 cases identical:**
  - the 12 micro goldens;
  - the T.H.52, Ruth St, McKnight Rd, T.H.61 and golden weave fixtures;
  - the T.H.52 section with the calibrated drivers, seeds 3–5;
  - the measured model.

  "Identical" means the sha256 of every Parquet file, `meta.json` without
  wall time, the metrics and the config hash.
- **Against W1's record of the same 37 cases** (taken at `daf8826`): every
  Parquet file is identical too. Only the config hashes and `meta.json`
  differ, through the fixtures' absolute OSM paths.
- **All 42 committed scenarios hash the same in both trees**, and
  `WEAVE_DEFAULTS` is unchanged. The 33 scenarios of W1's record keep W1's
  hashes.
- **Tests.**
  - `pytest -m "not slow" tests/test_microsim tests/test_flowstate_core`:
    762 passed, 13 xfailed, 2 xpassed. All of these marks are pre-existing and
    non-strict.
  - `ruff check`, `ruff format --check` and `mypy packages/flowstate_core`:
    clean.

### 10.9 Results [run]

All runs: macOS, eclipse-sumo 1.27.1, at most two SUMO processes at once. The
rows are in `artifacts/weave_loss_2026-10-07/w1b/`:
- `s*_ref.json`, `s*_w1b.json`: the self-check rows;
- `*_post.json`: `post.py`;
- `criteria.json`, `releases.json`.

**Both reference arms reproduce W1's.**
- S1's reference matches `w1/th52_ref.json` in every field but the config
  hash at all 20 seeds (280 fields), and `strand.py`'s stranded time matches
  `w1/th52_ref_post.json`.
- S2's reference matches `w1/grid_ref.json` in all 407 fields.

**Per set** (reference → W1b; "stand" is the rule's definition of §10.4):

| | S1: T.H.52 section, 20 runs | S2: grid, fixture fleets, 37 runs | S3: grid weaves, calibrated drivers, 75 runs |
|---|---|---|---|
| runs in which the rule fired | 0 | 1 (`ruth_exit_fleet_271` s5) | 5 (Ruth St) |
| entrants released | 0 | 2 | 5 |
| runs identical to the reference | 20 of 20 | 36 of 36 not fired | 70 of 70 not fired |
| longest stand [s] | 49.0 → 49.0 | 68.5 → 60.0 | **971.5** → 60.0 |
| reference stands ≥ 60 s | none | 68.5 (s5) | 61.5, 62.5, 64.0, 67.5, **971.5** |
| locks (§10.4) | 0 → 0 | 0 → 0 | **1 run** (`ruth_exit_fleet_271` s15) → 0 |
| `run_summary` lock flags | 0 → 0 | `merge_golden` s3, `ruth_entr_fleet` s5 → the same two | `ruth_exit_fleet_271` s15 → none |
| vehicles departed | 31,652 → 31,652 | 48,081 → 48,081 | 93,497 → 93,838 |
| exits given up | 66 → 66 | 78 → 74 | 445 → 437 |
| collisions | 0 → 0 | 0 → 0 | 0 → 0 |
| −9 m/s² vehicle-steps | 2 → 2 | 20 → 20 | 17 → 16 |
| exit-end flow, veh/h (mean) | 4,360.8 → 4,360.8 | | |
| stranded time (`strand.py`), s per section-run | 18.9 → 18.9 | 18.5 → 18.9 | 73.3 → 61.5 |

**The fixture lock.** `ruth_exit_fleet_271` at seed 15 with the calibrated drivers.

*Reference:*
- **The front row is the diagnosis's lock state.** A through-bound entrant
  (v00953) stands 0.08 m from the auxiliary lane's end from 228.5 s to the
  end of the run (971.5 s).
  - Lane 1's front exiter stands 180 s from 234.5 s. A second lane-1 exiter
    stands 555.5 s from 644.5 s.
  - At the end: lane 1's front is an exiter 10.78 m short of the gore, lanes
    2 and 3 have exiters 29.2 m and 81.6 m short, and nothing is past the
    gore.

  This is the picture of Table 4 in docs/I94_COLLAPSE_DIAGNOSIS.md.
- **It forms over minutes.** Gore crossings per minute run 22–36 until
  minute 11, then 11, then **0 from minute 12 to the end**.
- **What it costs.** The entrance departs 47 of 73, the run 673 of 1,014
  vehicles, and 28 controlled vehicles are unfinished.

*W1b:*
- **The release.** The same entrant is released at 288.5 s, after exactly
  60 s.
- **The lane-1 exiter** had stood 57.5 s. It gets into the auxiliary lane
  3.5 s later, so no exit-side release was needed.
- **Discharge.** The gore discharges 29–44 vehicles a minute to the end.
- **What it saves.** The entrance departs 73 of 73 and the run 1,014 of
  1,014. Nothing is unfinished, given-up exits fall 9 → 4, and there is no
  collision.

The other 74 runs of S3 and all of S1–S2 have no lock.

**Every release** (`releases.json`). "Reference" is the same entrant in the
reference run. Each run is identical to its reference up to its first release
(B1's mechanism), so the reference stand is comparable only for a run's first
release.

| set | fixture | seed | released at [s] | its stand [s] | the reference stand | given-up exits ref → W1b | lowest zone minute [m/s] ref → W1b |
|---|---|---|---|---|---|---|---|
| S2 | `ruth_exit_fleet_271` | 5 | 408.5 | 60.0 | 68.5 s, then changed into lane 1 at the gore | 14 → 10 | 5.9 → 5.2 |
| S2 | `ruth_exit_fleet_271` | 5 | 919.0 | 60.0 | (after the first release; not comparable) | | |
| S3 | `ruth_exit_fleet_271` | 15 | 288.5 | 60.0 | **971.5 s, to the run's end (the lock)** | 9 → 4 | 0.0 → 4.8 |
| S3 | `ruth_exit_fleet_271` | 16 | 140.5 | 60.0 | 67.5 s, then changed | 12 → 8 | 7.3 → 6.9 |
| S3 | `ruth_exit_fleet_271` | 17 | 360.0 | 60.0 | 62.5 s, then changed | 14 → 10 | 4.6 → 4.8 |
| S3 | `ruth_exit_fleet_271` | 6 | 1002.0 | 60.0 | 64.0 s, then changed | 13 → 13 | 7.5 → 7.7 |
| S3 | `ruth_exit` | 6 | 1001.5 | 60.0 | 61.5 s, then changed | 7 → **12** | 7.9 → **4.7** |

**What the releases show:**
- **One is the lock and five are ordinary stands.** Of the six first
  releases, one ends the lock. The other five end stands that would have
  cleared by themselves 1.5–8.5 s later. Their entrants finally changed into
  lane 1 at a standstill at the gore.
- **T_dwell's basis did not cover Ruth St.** 60 s was chosen above every
  ordinary stand of the T.H.52 section test (§10.3). Ruth St's ordinary stands
  reach 61.5–68.5 s with either fleet. So T_dwell does not separate ordinary
  waits from locks there: the rule acts on the tail of ordinary waits as
  well.
- **This cost stays within the pre-registered caps** (F3b): 0.09–0.14 % of
  entrance departures, pooled. Its direction is mixed: given-up exits fall
  in three of those runs and rise 7 → 12 in one (`ruth_exit` s6).

### 10.10 Verdict, criterion by criterion (as pre-registered in §10.5)

| # | criterion | result | verdict |
|---|---|---|---|
| B0 | (a) every release after a stand ≥ 59.5 s; (b) no stand > 61 s in a W1b run | 7 releases, every stand exactly 60.0 s; longest W1b stand 60.0 s | **pass** |
| B1 | every W1b run without a release identical to its reference | 126 of 126 (S1 20, S2 36, S3 70) | **pass** |
| F1b | S1 exit-end flow, paired 95 % upper bound ≥ 0 | +0.0 [0.0, 0.0]: the rule never fired on S1 | **pass** |
| F3b | entrants taking the exit ≤ 1 %, pooled over seeds per weave section | S1 0 of 7,744; S2 Ruth St 2 of 1,425 (0.14 %), others 0; S3 Ruth St 5 of 5,480 (0.09 %), others 0 | **pass** |
| F4 | S1: 0 collisions; −9 m/s² ≤ reference + 2; given-up exits ≤ reference | 0; 2 against 2; 66 against 66 | **pass** |
| F5b | S2, S3: zero collisions; no lock or lock flag the reference lacks | 0 and 0; none new | **pass** |
| L1 | zero locks in W1b on every lock-prone run | testable: one lock-prone run (S3 `ruth_exit_fleet_271` s15), unlocked under W1b | **pass** |

**Outcome.** Every fixture criterion passes, and the predictions for S1 held:
no release, and every run identical to its reference.

**What this does not settle.** By §10.5 this does not adopt W1b:
- **L1 rests on one lock.** One lock-prone run in 132 fixture runs, so the
  fixtures show the mechanism, not a rate.
- **Ordinary waits are not untouched on Ruth St.** The aim that ordinary
  waits pass untouched holds on T.H.52 but not on Ruth St (§10.9). The cost
  is within the registered caps.

The corridor round (§10.6, §10.11) and the owner's decision remain. The key
stays off in every committed scenario.
**Superseded (2026-10-07):** the corridor round ran and passed (§11); the owner's decision
remains.

### 10.11 The corridor round (cloud; written, not launched)

**Superseded (2026-10-07):** launched the same day as stage p9; results in §11.

**What it tests.** It measures W1b on the corridor where the locks were seen
(3 of 20 four-hour replicates), against the criteria fixed in §10.6.

**The fixture check of its lock reader.** `corridor_w1b.py`'s lock reader
applies C4b's end-of-run front-row rule. On the 60 Ruth St runs of S3's
reference it finds exactly one lock, the seed-15 lock above, with the
picture of Table 4, and no false positive.

**Reading C4b.** `vehicles.parquet` holds positions, not speeds. So, as in the
diagnosis's own Table 4, "at rest" is read from the empty reach past the
gore. This is how C4b will be read; it is fixed before any corridor run.

**The reader's corridor check.** Step 3's per-replicate files are not on this
machine, so the reader has not seen the corridor locks. The stage's own
reference arm checks it: if that arm reproduces step 3 (`reproduction.identical`),
the reader must report the three known locks (…189526 at T.H.52, …044631
and …041784 at Ruth St). If it does not, C4b is reported as unread.

**The battery's own detector.** Since this registration another session has
added `validation.locks` (docs/I94_COLLAPSE_DIAGNOSIS.md §10; uncommitted at
the time of writing). Its run-end reader finds exactly those three locks in
step 3's files, and batteries will carry `per_seed[i].locks` and
`zero_locks`.
- If the stage runs on a tree that has it, its per-seed records are reported
  beside C4b.
- C4b's verdict stays `corridor_w1b.py`'s reading, as registered.
- A disagreement between the two readers is reported, not resolved in
  either's favour.

**Proposed stage text** for `scripts/gcp/pipeline_i24.sh`, after p8 (also in
`w1b/harness/stage_p9_proposed.sh.txt`):

```sh
# p9 (proposed, docs/WEAVE_LOSS_DIAGNOSIS.md §10.6 and §10.11; opt-in; not in the default list). Amendment W1b's
#     corridor round: the I-94 four-hour battery under the reference configuration with the calibrated drivers
#     (scenarios/${MNDOT}_weave_dc.yaml, name ${MNDOT}_weave_xlsfg_dc, hash db9fbab5fc6e; step 3's 20 seeds, spawned
#     from its seed 42) and the same scenario with entrant_giveup_m 5 and entrant_giveup_dwell_s 60 added to both
#     weaves' weave_params (exit_prepare 1 kept), both run here on one code tree; then C1-C5b, the reference's
#     reproduction of step 3 and the end-of-run front row of every replicate
#     (artifacts/weave_loss_2026-10-07/w1b/harness/corridor_w1b.py; meta.json and vehicles.parquet only, never
#     trajectories) -> artifacts/weave_w1b_corridor.json. Run trees under runs/mndot_*_p9/baseline, so make_archive's
#     runs/mndot_*/*/*/*/{meta.json,vehicles.parquet} brings every replicate's files back. Needs no data set (launch
#     with --data-set none): the scenario, observations and populations are tracked.
P9_W1B=scenarios/${MNDOT}_weave_dc_w1b.yaml
if echo " $STAGES " | grep -q " p9_i94_w1b "; then
  stage p9_i94_w1b bash -c "set -e; \
    sed -e 's#^name: ${MNDOT}_weave_xlsfg_dc\$#name: ${MNDOT}_weave_xlsfg_dc_w1b#' \
        -e 's#weave_params: {exit_prepare: 1.0}#weave_params: {exit_prepare: 1.0, entrant_giveup_m: 5.0, entrant_giveup_dwell_s: 60.0}#' \
        scenarios/${MNDOT}_weave_dc.yaml > $P9_W1B; \
    [ \$(grep -c 'entrant_giveup_dwell_s: 60.0' $P9_W1B) -eq 2 ]; \
    for S in ${MNDOT}_weave_dc ${MNDOT}_weave_dc_w1b; do \
      N=\$(sed -n 's/^name: //p' scenarios/\$S.yaml | head -1); \
      $RUN scripts/corridor_battery.py --scenario scenarios/\$S.yaml --observations data/mndot/$MNDOT/observations.json \
        --replicates $REPS --procs $PROCS --out runs/\${N}_p9/baseline --artifact artifacts/validation_\${N}_p9.json \
        --report-dir docs/reports/\${N}_p9 --criteria-profile fhwa_tat3_2004; \
    done; \
    $RUN artifacts/weave_loss_2026-10-07/w1b/harness/corridor_w1b.py \
      --ref artifacts/validation_${MNDOT}_weave_xlsfg_dc_p9.json \
      --w1b artifacts/validation_${MNDOT}_weave_xlsfg_dc_w1b_p9.json \
      --committed-ref artifacts/validation_${MNDOT}_weave_xlsfg_dc.json \
      --out artifacts/weave_w1b_corridor.json" || say "p9_i94_w1b failed; continuing"
fi
```

**Launch** (after the stage is committed and pushed: the owner's call):

```sh
scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p9 --machine n2-standard-32 --bucket gs://<bucket>/p9 \
  --self-delete --via-bucket --data-set none --cap-min 150 --pipeline-args '--stages "p9_i94_w1b"'
```

**Cost [estimate].**
- Two batteries of 20 four-hour runs, one after the other, on n2-standard-32.
  Step 3's took 1,599 s at 30 processes, so about 55–60 min for the two.
- Plus 10–15 min of boot and setup through the bucket, and a minute for the
  readout.
- Total: about 70–75 min billed at about $1.55/h, **about $1.9**. The
  150-minute cap bounds it at about $3.9.
- Re-running the reference costs about $0.7 of that. It is kept for two
  reasons:
  - Pairing needs one code tree. Step 3's artifact was built at 06:20 UTC on
    2026-10-07, and at least three later commits touch the runner (`d7fc807`,
    `fadcfe7`, `decdccc`; `4957527` too if the VM ran an older tree). Each is
    recorded as byte-identical when off, which the re-run checks.
  - Its three known locks are what checks C4b's reader.

**What would change the reading.**
- If the W1b arm still locks, `corridor_w1b.py` names each lock's front row.
  An exiter at the front with the auxiliary lane moving would be the case for
  an exit-side release, which §10.2 deferred.
- C5b counts the ordinary stands W1b also ends (§10.9). On the corridor they
  include Ruth St's.

### 10.12 Limits

- **Fixtures, macOS.** The corridor rounds on Linux decide.
- **One fixture lock.** L1's pass rests on a single locked run. The
  frequency of locks on the fixtures is not estimated, and nothing here says
  how often W1b would have to act on the corridor.
- **T_dwell was chosen from one section.** Its basis (T.H.52's stands) does
  not cover Ruth St's ordinary stands, which run up to 68.5 s (§10.9).
  - A dwell chosen from Ruth St's stands too (about 70 s or more) would have
    left those five stands alone and would still have released the lock,
    which had stood 971.5 s.
  - That value was not registered and was not run. Any change of T_dwell is
    a new amendment with its own criteria.
- **Release ≠ realism.** Whether drivers in the field take the exit after a
  minute at the end of an exit-only lane is not measured. W1b is a release of
  a model state that has no field counterpart: a permanent standstill.

### 10.13 Reproduce

From the repository root, with `$W` a scratch directory. Pass the
`--weave-set` flags literally: zsh does not split a variable holding them.

```sh
DC=scenarios/mndot_i94_wb_stpaul_weave_dc.yaml
P=merge_model_selfcheck.py
uv run --no-sync python scripts/$P th52 --model weave --fleet-from $DC --seeds 3-22 --keep --work-dir $W/s1_ref --out $W/s1_ref.json
uv run --no-sync python scripts/$P th52 --model weave --fleet-from $DC --seeds 3-22 \
    --weave-set entrant_giveup_m=5 --weave-set entrant_giveup_dwell_s=60 --keep --work-dir $W/s1_w1b --out $W/s1_w1b.json
uv run --no-sync python scripts/$P grid --model weave --keep --work-dir $W/s2_ref --out $W/s2_ref.json
uv run --no-sync python scripts/$P grid --model weave \
    --weave-set entrant_giveup_m=5 --weave-set entrant_giveup_dwell_s=60 --keep --work-dir $W/s2_w1b --out $W/s2_w1b.json
RUTH=ruth_entr,ruth_exit,ruth_entr_fleet,ruth_exit_fleet,ruth_exit_fleet_271
REST=th52_corridor_demand,th52_capacity,th52_upstream,th52_upstream_fleet,weave_moderate,weave_golden,th52_corridor,th61
uv run --no-sync python scripts/$P grid --model weave --fleet-from $DC --only $RUTH --seeds 3-22 --keep --work-dir $W/s3a_ref --out $W/s3a_ref.json
uv run --no-sync python scripts/$P grid --model weave --fleet-from $DC --only $REST --keep --work-dir $W/s3b_ref --out $W/s3b_ref.json
#   ... and the same two with the two --weave-set flags into s3a_w1b / s3b_w1b
H=artifacts/weave_loss_2026-10-07/w1b/harness
for s in s1_ref s1_w1b s2_ref s2_w1b s3a_ref s3a_w1b s3b_ref s3b_w1b; do
  uv run --no-sync python $H/post.py $W/$s $W/${s}_post.json; done
uv run --no-sync python $H/eval.py $W        # B0, B1, F1b, F3b, F4, F5b, L1 -> $W/criteria.json
uv run --no-sync python $H/releases.py $W    # every release beside its reference -> $W/releases.json
```

**Identity runs.** As §8.7, with W1's `harness/cmp.py` and `harness/hashes.py`
on the trees `git archive HEAD` (`84a272e`) and that tree with `config.py`
and `runner.py` copied in.

## 11. W1b's corridor round — 2026-10-07 (stage p9, one n2-standard-16 in us-central1-a, 99 min, about $1.30, self-deleted)

The I-94 four-hour battery under the reference configuration with the calibrated drivers (`_dc`, step 3's 20
seeds) run twice on one code tree (b066935): the reference, and the same scenario with `entrant_giveup_m` 5 and
`entrant_giveup_dwell_s` 60 on both weaves (`scenarios/mndot_i94_wb_stpaul_weave_dc_w1b.yaml`). Read by
`corridor_w1b.py` against the criteria of §10.6, fixed before any run (`artifacts/weave_w1b_corridor.json`;
`artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc{,_w1b}_p9.json`; `docs/reports/..._p9/`).

| criterion | result | verdict |
|---|---|---|
| reference reproduces step 3 | identical (max difference 0.0) | — |
| C1 T.H.52 throughput (S790), paired | +7.2 veh/h [−9.6, +23.9] | **pass** |
| C2 realised demand, paired | +0.7 pp [−0.25, +1.66] | **pass** |
| C3 collisions | 0 (reference 0) | **pass** |
| C4b locks | **0** (reference 3: 677105600768189526 at T.H.52; 3011106312394044631 and 8026499204807041784 at Ruth St) | **pass** |
| C5b releases | Ruth St 108 of 19,900 entrants (0.54 %); T.H.52 5 of 91,801 (0.005 %) | **pass** |

Beside the criteria: link-flow GEH < 5 on 56.1 % of link-hours against 52.3 %, lowest realised demand 0.958 against
0.889, and the battery's own `no_locks` row (validation.locks) PASS against FAIL. **W1b removes every lock with no
measurable throughput cost and no collision.** It stays opt-in until the owner adopts it (a default change is a
config-hash change under policy v3).

**Note (2026-10-07):** both batteries' `validation.locks` records (`per_seed[i].locks`, `locks`, the `no_locks`
row) were scored on b066935, before the second regression review made one standing queue one lock (CHANGELOG
2026-10-07, "Second regression review"). The reference's record lists 6, 5 and 3 lock heads in its three locked
runs, the extra ones upstream of the weave, so its `by_section` table names the McKnight Rd merge (on-ramp
178547099) in all three. The run-level counts (3 of 20, 0 of 20) and the weave heads agree with C4b's reader;
the per-run lists would read differently under today's code.
The generated reports under `docs/reports/*_p9/` carry the same pre-fix lock lists and are left as generated.
