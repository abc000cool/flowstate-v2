# Why I-94 runs collapse: a lock at a weaving section's exit gore (2026-10-07)

Two kinds of run collapsed with the calibrated I-94 drivers (k 1, keep-right 0.1):

- **The slice.** In the netfix probe (docs/I94_LANE_SHARES.md, "Netfix probe", corrected 2026-10-07), the
  35-minute slice on the as-built network at seed 3747978530954135749 discharged 876 veh/h at S97 against
  3,632–3,896 at the other seeds, and departed 0.857 of its demand against 0.965–0.987 in every other run.
- **The four-hour battery.** In step 3's 20-seed battery (docs/DISCHARGE_CALIBRATION.md §4, hash `db9fbab5fc6e`),
  seeds 677105600768189526 (departed 0.903) and 3011106312394044631 (0.889) left 10–11 % of their planned
  vehicles undeparted, against 3.4–4.2 % in the others.

This note asks what mechanism produces the collapses, where it acts, and whether it belongs to the 6th Street
map defect, the calibrated drivers or the weave model.

Nothing was simulated. No code, scenario, artifact, target or golden was changed, and nothing was committed.
Python was used only to read JSON and Parquet files. Inputs:

- **The probe's 16 runs**: `meta.json` and `readings.json` of
  `runs/p5/i94_netfix_probe/{as_built,netfix}/<pair>/<hash>/<seed>/`. They come from the archive of stage p5 and
  are not committed. That archive did not keep the runs' `vehicles.parquet`.
- **`artifacts/i94_netfix_probe.json`.**
- **`artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc.json`**: `per_seed` insertion, link-hours, scores,
  metrics and waiting.
- **The step-3 battery's per-replicate files**: `meta.json` and `vehicles.parquet` of all 20 replicates under
  `runs/mndot_i94_wb_stpaul_weave_xlsfg_dc/baseline/db9fbab5fc6e/<seed>/`. They were extracted from stage 23's
  archive (`final.tgz`, which `make_archive` ships with them) and are not committed. `vehicles.parquet` holds
  one row per departed vehicle (about 1.5 MB per run): origin, planned destination, and its last sampled position,
  lane and time. No `trajectories.parquet` was read.
- **`artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_p1.json`**: the k 0 reference battery on the same 20
  seeds and the same map.
- **`packages/microsim/microsim/runner.py`**, read only, for what each counter means.

Labels: **[artifact]** read from a committed file; **[run file]** read from the uncommitted per-run files above;
**[computed]** computed here from those; **[estimate]** an estimate, with its basis stated; **[hypothesis]** not
shown by these files.

## 0. In plain English

- **It is a lock (a permanent standstill), and it forms at a weaving section's exit gore.**
  - Once it forms, nothing passes the gore again. The slice's S97 carries 0 veh/h for its last 20 minutes.
  - It is not a breakdown: a broken-down T.H.52 still discharges 3,100–4,000 veh/h in every other run.
  - It is not a lost-exit cascade: given-up exits stay in their normal range.
  - It is not teleports (teleporting is off) and not collisions (none were recorded).
- **What stands at the front. It was seen directly in all three four-hour replicates that locked** (the two
  named above, plus a late lock at seed 8026499204807041784 that barely moves its departed share):
  - **Auxiliary lane.** An entrant bound *through* stands within 0.1 m of the end of the auxiliary lane, and
    that lane leads only to the exit. Every exit-bound vehicle behind it in that lane is stopped.
  - **Each through lane.** The front vehicle is an exit-bound vehicle that cannot get into the stopped lane on
    its right. It stands 5.0–10.8 m (lane 1) or 65–180 m (lanes 2–3) short of the gore, and through traffic
    queues behind it.
  - **Downstream.** The road past the gore is empty.
  - **Where.** One lock is at T.H.52 (10.73 km) and two are at Ruth St (5.32 km).
- **Why nothing frees it.** No release rule reaches any vehicle at the front:
  - The weave's exit give-up acts only on an exiter halted within 5 m of the gore.
  - The entering give-up (amendment W1) is off.
  - The network's lane-end give-up skips weaving sections.
  - Teleporting is off.
- **The slice collapse is very probably the same state at T.H.52, but this is inferred, not seen.** Its T.H.52
  counters match the four-hour T.H.52 lock: 30 % of the section's controlled vehicles still unfinished,
  changer easing ×10–16 and deferred forced changes ×6 to ×150 against the other 15 runs, and only 43 % of the
  exit-bound vehicles that reached the section exited. Its vehicles table was not archived.
- **Frequency.**
  - Calibrated drivers: 3 of 20 four-hour replicates (95 % interval 3–38 %) and 1 of 8 slices locked.
  - k 0 drivers: 0 of 20 four-hour replicates (same seeds, same map) and 0 of 8 slices.
  - The difference suggests the drivers matter, but it is not resolved (Fisher p = 0.23 on the four-hour
    runs).
- **The 6th Street defect: no evidence either way.** Two of the three four-hour locks are at Ruth St, 4 km
  upstream of the defect. The netfix run at the collapsed seed did not lock, but it is a different random
  realisation, not the same event with the map fixed.
- **Batteries did not report a lock** (a detector does since 2026-10-07, §10).
  - Nothing in the battery artifact names one. The verdict reads "backlog: 4 %", no ramp is flagged as starved,
    and the "no lock" rule written for W1's corridor round passes every lock seen here.
  - The three locked replicates are the three worst for failed station-hours. Removing them for comparison
    only would move the per-replicate GEH pass mean from 52.3 to 56.7 % and the speed RMSPE from 48.1 to
    46.2 %.
  - The one locked slice is the whole of the probe's apparent discharge gain from the 6th Street fix. It also
    decides the discharge condition of the mechanical rule that lets stage p8 run the netfix battery.
- **Confirming the slice.** Re-run the probe once with trajectories kept and map that one run. This needs a
  short committed stage on one n2-standard-16 VM, about $0.30 (§6). Not launched.
- **Fix direction (proposal):**
  - First, count locks in every battery: per replicate, by section, with onset.
  - Then the owner may revisit W1, which removes the front vehicle of every lock seen here. It failed only its
    fixture frequency caps. It would need an amendment and the corridor round, with a no-lock criterion that
    can actually see these locks.

## 1. The collapsed slice against the other 15 probe runs

**Table 1.** Counters of the collapsed run against the other runs [run file]. In the meta, T.H.52 is
`weave_sections[*]` with `exit_edge` 18207598 and Ruth St the one with `exit_edge` 18208090. Columns:

- "same pair": the other three seeds of as built with k 1, keep-right 0.1.
- "other 12": every run of the other three network × driver arms.
- The last two columns are the collapsed seed under netfix with k 1 and under as built with k 0.

| counter | collapsed (as built, k 1, seed …135749) | same pair, 3 seeds | other 12 runs | netfix k 1, same seed | as built k 0, same seed |
|---|---|---|---|---|---|
| departed share | **0.857** | 0.974–0.978 | 0.965–0.987 | 0.976 | 0.973 |
| arrived | **2,442** | 4,647–4,786 | 4,300–4,779 | 4,691 | 4,476 |
| in the network at 2,100 s (departed − arrived) | **2,501** | 853–984 | 909–1,269 | 934 | 1,132 |
| insertion delay, sum [s] | **494,324** | 51,448–67,242 | 38,007–122,776 | 68,289 | 75,301 |
| S97 5-min flow, mean of windows 3–6 (900–2,100 s) [veh/h] | **0** | 3,717–3,966 | 3,096–3,960 | 3,879 | 3,459 |
| entrance 40648744 / T.H.52 769818012 departed | **0.42 / 0.38** | 0.79–0.86 / 0.93–0.97 | 0.82–0.93 / 0.86–0.98 | 0.83 / 0.95 | 0.84 / 0.91 |
| T.H.52 weave: vehicles taken under control | 104 | 565–612 | 483–604 | 595 | 532 |
| — still unfinished at the end (share of those) | **31 (30 %)** | 1–4 (≤ 0.7 %) | 0–7 (≤ 1.3 %) | 1 | 7 |
| — forced changes refused by the guard [vehicle-steps] | **7,043** | 145–594 | 46–1,141 | 556 | 264 |
| — changer eased towards its gap's leader [vehicle-steps] | **49,743** | 3,318–4,405 | 3,177–4,837 | 3,629 | 3,496 |
| — follower cooperations [vehicle-steps] | 24,530 | 16,867–19,570 | 16,776–19,407 | 18,327 | 16,786 |
| — stopped pairs released | **282** | 3–8 | 2–75 | 11 | 4 |
| — forced changes completed | 3 | 44–70 | 32–69 | 34 | 42 |
| — exits given up at the gore (`n_missed_exit`) | 3 | 1–7 | 0–12 | 9 | 1 |
| — exited / reached the section, exit-bound | **42 / 98 (43 %)** | 97–99 % | 95–99 % | 98 % | 98 % |
| — mean wait to enter [s] | **41.9** | 7.7–8.1 | 7.9–11.8 | 7.9 | 8.2 |
| Ruth St weave: unfinished / refused forced changes | 0 / 452 | 0–1 / 149–965 | 0–4 / 29–1,284 | 0 / 57 | 0 / 933 |
| scripted merges 18207436 / 178547099, mean wait [s] | 7.0 / 20.3 | within the others' 6.3–9.5 / 19.0–32.1 | | | |
| lane-end give-ups: exit given up / exit taken | 1 / 4 | 0–1 / 0–3 | 0–1 / 0–6 | 0 / 0 | 0 / 1 |
| collisions | 0 | 0 | 0 | 0 | 0 |

How to read the counters (runner.py, `_weave_meta`):

- **Unfinished** vehicles are still under the weave's control at the end of the run, owing a lane change.
- **Refused forced changes, easing and cooperations** are counted per vehicle-step. They grow by roughly one per
  step for every vehicle that is standing in the section while owing a change.

The collapsed run has three properties no other run has:

- **The section is full of stuck changers.** 31 controlled vehicles still owe their change at the end. Another
  53 exit-bound vehicles reached the section and neither exited nor gave up (98 − 42 − 3) [computed]. That makes
  it a section full of vehicles, not one that has drained.
- **Most of its controlled time is spent standing.** Easing runs at about 17 vehicle-steps per step after the
  onset (an extra ≈ 45,000 over ≈ 2,600 steps) [estimate].
- **The release rules barely fire.** Only three exits were given up, and pair releases did fire but did not
  clear it.

Everything upstream of the 40648744 entrance is normal:

- Ruth St, the two scripted merges and the lane-end give-ups read like every other run.
- The mainline and every entrance upstream of 9.9 km inserted all of their demand.
- 821 of the run's 822 vehicles that never departed belong to the two entrances closest to T.H.52.

**Table 2.** Crossings at each station, t = 300–2,100 s [run file]. The last column [estimate] is when each
station stopped carrying flow. It assumes the station carried the same pair's mean rate until then and nothing
afterwards: t ≈ 300 + 1,800 × (share of the same pair's mean).

| station | x [km] | collapsed | same pair, mean | share | flow stops at about [s] |
|---|---|---|---|---|---|
| S1063–S1947 | 1.09–6.07 | within 2 % of the other three runs at the same seed | | | never inside the window |
| S1069 | 6.68 | 1,465 | 1,664 | 88 % | 1,890 |
| S1070 | 7.68 | 1,808 | 2,538 | 71 % | 1,580 |
| S1948 | 8.40 | 1,390 | 2,519 | 55 % | 1,290 |
| S792 | 8.98 | 811 | 1,750 | 46 % | 1,130 |
| S791 | 9.72 | 567 | 1,646 | 34 % | 920 |
| S790 | 10.13 | 533 | 1,958 | 27 % | 790 |
| S97 | 11.07 | 438 | 1,873 | 23 % | 720–780 (5-min windows 1,668 / 3,084 / 2,172, then 0) |

- **The onset.** Flow at S97 stops about 720–790 s into the slice (07:12–07:13 local), and at S790 at about the
  same time.
- **The standstill then runs upstream.** It reaches S1069 just before the end, moving at 2.5–3.6 m/s
  (9–13 km/h) between S791 and S1069 [estimate].
- **Before the onset nothing was unusual.** The first 5-minute window at S97 is normal: 1,668 veh/h against
  1,512–1,656 in the other runs.

## 2. The same state in the four-hour battery, seen vehicle by vehicle

The battery artifact holds only per-seed totals [artifact]. Its link-hours already place the two named seeds:

- **677105600768189526.** Its first two scored hours are normal. In 08:30–09:30 S97 carries 829 veh/h against
  3,560–3,787 in the other seeds, and S790 979, S791 810 and S792 928. Entrances 40648744 and 769818012 deliver
  0.63 and 0.74 of their plan (0.74–0.79 and 0.89–0.93 elsewhere).
- **3011106312394044631.** S1065–S1068 carry 0 veh/h in 08:30–09:30, and S1063 576 and S1064 80. Its mainline
  inserts only 0.844 of its plan. The four entrances up to and including Ruth St deliver 0.71–0.75. The C-D re-entry and
  T.H.61 deliver all of theirs, and the two entrances by T.H.52 deliver more than in the other seeds (0.94 and
  0.96), because less mainline traffic reaches them.
- **8026499204807041784.** A third seed, with departed share 0.959, shows the Ruth St pattern in its last hour:
  S1066–S1068 at 1,134–1,438 veh/h against 2,541–2,897.

**Table 3.** Weave counters of the three locked replicates [run file].

| counter | …189526 | …044631 | …041784 | 17 other replicates |
|---|---|---|---|---|
| departed share | 0.903 | 0.889 | 0.959 | 0.958–0.966 |
| T.H.52: unfinished / refused forced changes | **24 / 44,551** | 0 / 3,895 | 1 / 4,083 | 0–10 / 3,237–6,207 |
| T.H.52: changer eased | **116,885** | 31,356 | 33,604 | 31,864–39,607 |
| T.H.52: exited / reached, exit-bound | **3,075 / 3,165** | 3,709 / 3,761 | 3,828 / 3,872 | 3,759–3,996 / 3,816–4,056 |
| Ruth St: unfinished / refused forced changes | 1 / 12,758 | **22 / 82,081** | **16 / 41,455** | 0–2 / 4,985–17,514 |
| Ruth St: changer eased | 14,612 | **124,738** | **62,327** | 3,822–12,667 |
| Ruth St: exited / reached, exit-bound | 1,313 / 1,336 | **714 / 773** | 1,175 / 1,229 | 1,387–1,535 / 1,420–1,564 |
| exits given up, T.H.52 / Ruth St | 48 / 22 | 47 / 29 | 39 / 29 | 33–71 / 14–40 |
| collisions | 0 | 0 | 0 | 0 |

**Table 4.** What stands at the gore at the run's end, t = 14,400 s [run file, computed].

- Source: `vehicles.parquet`, vehicles not arrived whose last sample is at the end.
- Positions are on the corridor axis. The gores are the weave ramps' `attach_end_x_m`: 10,731.64 m (T.H.52) and
  5,316.64 m (Ruth St).
- Lane 0 is the auxiliary lane; lanes 1–3 are the through lanes, right to left.
- "Exiter" means bound for that section's exit: T.H.52 → 18207598, Ruth St → the C-D split 18208090.

| | …189526, T.H.52 | …044631, Ruth St | …041784, Ruth St |
|---|---|---|---|
| lane 0 front | T.H.52 entrant bound for the corridor's end, **0.07 m** from the gore | Ruth St entrant bound for the T.H.52 exit (through at Ruth St), **0.05 m** | Ruth St entrant bound for the corridor's end, **0.08 m** |
| lane 0, rest of the section | 27 exiters, 8 more through-bound entrants | 11 exiters, 2 through-bound | 14 exiters, 4 through-bound |
| lane 1 front | exiter (McKnight Rd → T.H.52 exit), **10.8 m** short | C-D exiter, **5.03 m** short | C-D exiter, **10.7 m** short |
| lane 2 front | exiter, 78.5 m short | C-D exiter, 64.9 m short | C-D exiter, 99.3 m short |
| lane 3 front | exiter, 180.2 m short | C-D exiter, 118.1 m short | C-D exiter, 133.1 m short |
| vehicles past the gore, up to the next entrance | **0** (to the corridor's end) | **0** (to the C-D re-entry at 5.93 km) | **0** |
| last vehicle through the gore (arrival-based) | ≈ 08:47 | ≈ 08:37 | ≈ 09:16 |
| the front entrant departed | 08:36 (planned 08:17) | 08:07 | 08:46 |

For contrast, at the end of an unlocked replicate (6914975401685141156):

- 18 vehicles are between the Ruth St gore and the C-D re-entry.
- No through-bound vehicle stands at an auxiliary lane's end.

**How the lock develops.** Two readings show it forming over minutes rather than at a single step [computed]:

- **Ruth St, 3011106312394044631.** The front entrant departed at 08:07. Arrivals of vehicles that crossed the
  gore fall over three successive 10-minute bins: 480, then 357, then 76.
- **T.H.52, 677105600768189526.** The bins fall from 607 to 325 over 08:30–08:50.

So the lanes stop one after another: the auxiliary lane first, the through lanes as an exiter becomes each
one's front vehicle.

## 3. Mechanism and where

**The most likely mechanism.** It was seen in all three four-hour locks and is inferred for the slice at
T.H.52.

1. **A through-bound entrant halts at the end of the auxiliary lane.** This is the "stranded entrant" of
   docs/WEAVE_LOSS_DIAGNOSIS.md §3.8(a). On the T.H.52 fixture it stood 0–62.5 s per run and always cleared.
2. **Nothing releases it.**
   - The entering give-up `entrant_giveup_m` (amendment W1) is unset in every committed scenario.
   - The network's lane-end give-up skips both weave edges (`lane_end_giveups.skipped_edges`: `51388891`,
     `999007700` in every meta).
   - Teleporting is off (`--time-to-teleport -1`, runner.py).
3. **The auxiliary lane leads only to the exit**, so every exiter behind the entrant in that lane stops behind
   it: 27, 11 and 14 vehicles in Table 4.
4. **Exiters still in the through lanes cannot enter the stopped lane on their right.** Each halts beside or
   just behind stopped vehicles in that lane and becomes the front vehicle of its own lane:
   - in lane 1, 5.0–10.8 m short of the gore;
   - in lanes 2 and 3, 65–180 m short.

   Through traffic stacks up behind them. So all four lanes have a front vehicle that waits for a lane that
   never moves.
5. **The exit give-up never fires.** It reroutes an exiter only when that exiter is halted **within
   `exit_giveup_m` = 5 m of the gore** with no change possible. None of the lane fronts is that close. Lane 1's
   front at …044631, at 5.03 m, is on the boundary, and the axis cannot place it inside or outside. So given-up
   exits stay in their normal range (Tables 1 and 3).
6. **The lock spreads upstream.** Discharge through the gore is zero, and the reach downstream of it drains
   empty. The standstill runs upstream at about 3 m/s, starving the nearest entrances first and reaching the
   corridor's entry within an hour in …044631.

**Why the through lanes stop where they do [hypothesis].** The weave model's own commands look like the likely
reason, with SUMO's own approach to a lane end its route does not continue on possibly contributing:

- **Two commands keep a vehicle behind a stopped one** (runner.py docstring, "Easing", "Cooperation"):
  - each changer gets a one-step target towards its chosen gap's leader in the target lane;
  - a gap's follower is driven towards the changer as a virtual leader.

  When the target lane stands, both targets are standstills.
- **The easing counter fits this reading.** It runs at roughly 15–20 vehicle-steps per step while a run is
  locked (Tables 1 and 3).

**What is not known.** Why the stranded entrant's own change into lane 1 is never executed. In two of the three
locks lane 1 is empty ahead of it, and lane 1's front vehicle is 5–6 m behind its rear, assuming 5 m vehicles.
In the third (…044631) the two overlap by a few centimetres, so neither can change. Three candidates for the
first two:

- **The front exiter.** It is the entrant's gap follower, and it is itself an exiter whose change is due, with
  priority over the auxiliary lane.
- **The guards' floors.**
- **The pair release.** It treats two vehicles as a pair only within one vehicle length, and in …189526 the
  gap is about 5.8 m against 5 m (with the same 5 m assumption). This one is inferred.

The trajectories decide it (§6).

**Ruled out, with the evidence:**

| candidate | why not |
|---|---|
| a hard breakdown propagating upstream | A broken-down section still discharges. T.H.52 at S97 carries 3,096–3,966 veh/h in the other runs' last 20 minutes; here it carries 0, and the reach past the gore ends empty (Table 4). |
| a lost-exit cascade | Given-up exits are inside their normal ranges in every locked run: 3 in the slice, 39–48 at T.H.52 and 22–29 at Ruth St over four hours. Lane-end give-ups are normal too. |
| teleports | Off by design. A lock is therefore permanent and visible, not resolved by deleting vehicles. |
| collisions | 0 in all 36 runs read. |
| the 40648744 merge, the corridor's usual queue head (docs/ONBOARDING_MNDOT.md §11b) | A lock there would leave the T.H.52 section draining. Instead it ends with 31 changers still owing their change, and in the four-hour T.H.52 lock the front row is at the T.H.52 gore. |
| the Jackson St exit or the downstream boundary | In the four-hour T.H.52 lock nothing stands past the gore, and the lane-end give-up at the Jackson St diverge (`1001426896`) did not act. |

**Where.**

- **The slice:** the T.H.52 weave, edge `51388891`, at its exit gore (10.73 km). This is inferred from Table 1;
  its front row was not observed.
- **The four-hour battery:**
  - T.H.52's gore (10.73 km), in 677105600768189526;
  - the Ruth St gore (5.32 km, edge `999007700`, exit to the C-D split), in 3011106312394044631 and
    8026499204807041784.

  These three are observed.

**History.** Earlier rounds locked at the same sections (docs/ONBOARDING_MNDOT.md §10–11):

- VM G's seeds locked at the T.H.52 exit end.
- VM H's locking seed stopped at that same end; it was not mapped further.
- VM H's two low seeds semi-locked at Ruth St.
- VM N mapped VM M's exiter-yield lock at T.H.52: an exiter yielding to a halted entrant that was never freed.
- VM R–T mapped a lock at the T.H.61 two-lane weave's gore, before the lane-end give-up existed.

Every lock mapped there from 2026-09-24 on was at a weaving section.

## 4. The collapsed seed on netfix and at k 0

Both runs at seed 3747978530954135749 ran normally (Table 1, last two columns):

| run | S97 windows 1–6 [veh/h] | T.H.52 unfinished |
|---|---|---|
| netfix, k 1 | 3,468–4,080 | 1 |
| as built, k 0 | 3,192–3,840 | 7 |

**What this implies: little.**

- **A seed fixes the random streams, not the event.** The simulation is deterministic per seed but chaotic.
  Changing the network or the drivers re-realises every vehicle from the first divergence on, so these two runs
  are independent draws, not the collapsed event replayed with one input changed.
- **The seed carries nothing across scenarios.** The same seed runs normally in the four-hour battery (departed
  0.963). Seed 6914975401685141156 locked under VM V's `ramp_outlet` and ran normally in the other rounds recorded
  here.
- **Four seeds per arm cannot rank arms on a 1-in-7-to-20 event.**

**What the wider evidence says:**

| question | evidence | reading |
|---|---|---|
| the 6th Street defect | 2 of the 3 four-hour locks are at Ruth St, 4 km upstream of the defect. Slices: as built 1 of 4, netfix 0 of 4. | The lock state does not need the defect. A contribution at T.H.52 is possible [hypothesis]: the defect shifts traffic one lane left at 9.30–9.55 km, so more T.H.52 exiters must cross lanes 2–3, and two lane fronts in …189526 are such exiters. Untested. No four-hour netfix battery has run. |
| the calibrated drivers | Four hours, same 20 seeds, same map: k 1 has 3 locks, k 0 has 0. The k 0 battery's only station-hours under half their observed flow are at S1063/S1064 in 07:30, the queue at the entry [artifact]. Slices: k 1 1 of 8, k 0 0 of 8. | Suggestive, not resolved: Fisher exact p = 0.23 (four hours), 0.11 if the slices are crudely pooled. The k 1 drivers carry about 500 veh/h more through S790; whether that or the drivers' dynamics raises the rate is not known. |
| the weave | Every lock seen, and every mapped one before it, has its head at a weaving gore. The front vehicle is always the state the weave model has no release for. Scripted merges and lane-end diverges read normal in every locked run. | The lock is a property of the weave model's unreleased stranded-entrant state. Its frequency depends on the configuration. |

## 5. What it means for trusting batteries

**Frequency [computed].** Interval is Clopper–Pearson 95 %.

| set | locked | interval | where and when |
|---|---|---|---|
| step 3, four hours, k 1 (`db9fbab5fc6e`) | 3 of 20 | 3–38 % | T.H.52 at ≈ 08:37–08:47; Ruth St at ≈ 08:07–08:37 and ≈ 08:46–09:16, all late in the run |
| p1 reference, four hours, k 0 (`b550b46fe751`, same seeds) | 0 of 20 | 0–17 % | |
| probe slices, k 1 (as built + netfix) | 1 of 8 | 0.3–53 % | T.H.52, ≈ 07:13 |
| probe slices, k 0 | 0 of 8 | 0–37 % | |

**What the battery artifacts say about locked replicates: nothing that names them.**

| summary | what it shows | why it misses the lock |
|---|---|---|
| `insertion.verdict` | "backlog: 4 % of planned vehicles never departed" | It pools all replicates. |
| `insertion.starved_ramps` | `[]` | A ramp counts only below half of its plan; the worst in the locked seeds is 0.63. |
| `zero_collisions` | true | Locks involve no collision. |
| `weave_exits` | flags Ruth St at 2.1 % | Given-up exits are normal in the locked seeds. |
| W1's corridor criterion C4 (WEAVE_LOSS_DIAGNOSIS §6.2): "lowest departed share ≥ 0.8 × the median" | passes 0.889, 0.903 and 0.959 (threshold 0.769), and the slice's 0.857 (threshold 0.78) | A lock that forms late in a run costs only 0–7 points of departed share; the slice lost 12. |
| `merge_model_selfcheck.run_summary`'s lock flag (unfinished > 10 % of entered) | flags the slice (30 %) | Misses the four-hour locks (0.6–3.3 %): entered counts accumulate over four hours. |

**How much locked replicates move the summaries [computed].**

- Source: the battery's `per_seed`, the nine-day observations, the hours anchored at 06:30, t-intervals over
  the replicates.
- The "without" column is a sensitivity for reading this note. It is **not** proposed as a way to report, since
  dropping replicates would break CLAUDE.md §0.6.

| quantity | all 20 | without the 3 locked | the 3 locked |
|---|---|---|---|
| GEH < 5, per-replicate mean | 52.3 % [46.0, 58.6] | 56.7 % [52.4, 61.0] | 26.2 / 21.4 / 33.3 % |
| failed station-hours (of 42 per replicate) | 401 in all | 309 (10–23 each) | 31 / 33 / 28 — the three worst |
| speed RMSPE | 48.1 % [45.3, 50.9] | 46.2 % [45.1, 47.3] | 57.7 / 69.1 / 49.7 % |
| throughput [veh/h] | 2,866 [2,755, 2,976] | 2,955 [2,946, 2,964] | 2,431 / 2,056 / 2,588 |
| fuel [ml/veh-km] | 112.5 [105.4, 119.7] | 107.3 [104.7, 109.8] | 145.9 / 161.9 / 119.0 |
| mean travel time [s] | 572.6 | 572.6 | 636 / **497** / 586 |
| mean travel time incl. waiting [s] | — | median 784 (689–854) | 1,039 / 1,112 / 825 |
| stack wave speed, criterion C4 | 4.90 km/h, front in 19 of 20 | 4.41–5.45 per replicate | 5.37 / 3.08 / **no front** |

What the table shows:

1. **A lock inflates failures everywhere upstream of it in the hours after its onset.** In 3011106312394044631,
   14 of the 14 station-hours in 08:30–09:30 fail, with GEH up to 76.
2. **Locked replicates widen the intervals more than they move the means.** For RMSPE the half-width falls from
   2.8 to 1.1 points without them, and for throughput from ±110 to ±9 veh/h. A battery's interval mixes
   ordinary seed variance with a rare discrete failure.
3. **Plain mean travel time is blind to a lock** and can even improve. Vehicles trapped in the queue never
   finish, so they are censored. 3011106312394044631 reads the second-lowest mean travel time of all 20 seeds,
   496.7 s (the lowest is 435.1 s, at the unlocked seed 165503670820534583; the battery mean is 572.6 s;
   `per_seed[].metrics.mean_tt_s`). *Corrected 2026-10-07 (regression review): the first version said "the
   lowest".* The waiting block, which includes delay before departure, exposes it: censored vehicles 6,209 and
   7,221 against 2,108–2,700.
4. **Criterion C4's one replicate without a backward front is the late Ruth St lock.**
5. **The gate does not change.** Its per-replicate values are not committed, so its C1 and C3 were not
   re-split here. The direction is the same, and no gate verdict changes: C1 61.8 % against 85 %, C3 33.9 %
   against 15 %.

**Four-seed probes.**

- The probe's as-built k 1 mean discharge, 3,028 veh/h, is three normal seeds (mean 3,746) and the locked one
  (876).
- Stage p8's mechanical rule for running `_dc_cal_netfix` (`p8_netfix_helps`, pipeline_i24.sh) requires the
  netfix S97 discharge error to be no larger than the as-built one. On the committed artifact that condition
  holds, 0.163 against 0.326, only because of the lock. At the three seeds where neither arm locked, netfix
  reads 3,740 veh/h against 3,746, an error of 0.167 against 0.166, and the condition fails.
- p8 has not run; no `_dc_cal*` artifact is committed.
  **Superseded (2026-10-07):** p8 ran later the same day, all three batteries including `_dc_cal_netfix`
  under the rule as committed (docs/I94_CALIBRATION_DAYS.md, "Results of stage p8"). The netfix arm locked
  (lowest realised demand 0.761) and recorded 4 collisions (docs/I94_CAL_COLLISIONS.md).
- Whether to run `_dc_cal_netfix` should therefore rest on the fix being the correction of an input defect,
  which docs/I94_LANE_SHARES.md already argues, not on this rule. That decision is the owner's.

**Reading rule until locks are detected.** (Superseded for batteries, reports and sweeps by the detector of §10;
still the rule for probes and for artifacts written before it.) For any I-94 battery or probe:

- read the per-replicate departed shares and the weave counters (`n_unfinished`, `n_changer_eased`) before the
  means;
- treat a replicate whose easing count is several times the median as a candidate lock;
- check its `vehicles.parquet` front row as in Table 4.

## 6. Confirming the slice event (cloud; written, not launched)

**Already confirmed at no cost.** The four-hour locks' front rows (Table 4). For the slice, the counters cannot
show the front row, and the p5 archive did not keep its `vehicles.parquet`.

**The cheapest confirmation.** Re-run the probe once with trajectories kept, then map the collapsed run with the
committed readers:

- `diag_seed` (50 m × 1 min per lane);
- `diag_lockveh` (who comes to rest first, with origin → destination from `vehicles.parquet`);
- `diag_lock` (100 m × 5 min).

The probe runs its 16 runs in one wave, so re-running all 16 costs no more wall time than re-running one. The
probe already has `--keep-trajectories`, so only a pipeline stage has to be written.

The launcher runs only stages of a pushed commit, so a short stage has to be committed first (the owner's
call). Proposed text for `scripts/gcp/pipeline_i24.sh`, after p5:

```sh
# p5b (proposed, docs/I94_COLLAPSE_DIAGNOSIS.md §6). The probe again with trajectories kept; then the collapsed
#     run's reproduction check, standstill maps and front row. One n2-standard-16, --data-set none.
stage p5b_i94_collapse_map bash -c "set -e; \
  $RUN scripts/i94_netfix_probe.py --procs $(( PROCS < 16 ? PROCS : 16 )) --keep-trajectories \
    --out runs/p5b/i94_netfix_probe --artifact artifacts/i94_netfix_probe_rerun.json; \
  D=runs/p5b/i94_netfix_probe/as_built/k1.0_kr0.1/faa4ab5b219c/3747978530954135749; \
  grep -q '\"n_vehicles_departed\": 4943' \$D/readings.json; \
  $RUN artifacts/mndot_rounds/weave_2026-09-24/diag_seed.py.txt \$D > logs/p5b_collapse_50m_1min_lanes.txt; \
  $RUN artifacts/mndot_rounds/weave_2026-09-24/diag_lockveh.py.txt \$D 9 20 10300 10800 > logs/p5b_collapse_front_row.txt; \
  $RUN artifacts/mndot_rounds/weave_2026-09-24/diag_lock.py.txt \$D > logs/p5b_collapse_100m_5min.txt" \
  || say "p5b_i94_collapse_map failed; continuing"
```

- Also add `runs/p5b/i94_netfix_probe/*/*/*/*/{readings.json,meta.json,vehicles.parquet}` to `make_archive`'s
  list, without the trajectories.
- **Launch:**

  ```sh
  scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p5b --machine n2-standard-16 --bucket gs://<bucket>/p5b \
    --self-delete --via-bucket --data-set none --cap-min 45 --pipeline-args '--stages "p5b_i94_collapse_map"'
  ```

**What confirms the mechanism, and what refutes it:**

- **Reproduction.** The re-run must read 4,943 departed, with S97 windows 1,668 / 3,084 / 2,172 / 0 / 0 / 0 / 0.
  The probe also checks its as-built runs against the grid's readings by itself.
  - Two commits since p5 touch the code the probe runs, and neither should change a run [estimate]:
    `decdccc` adds a default-off, hash-neutral weave input, and `268d70c` changes scoring and the probe's S791
    handling, not the runner.
  - If the run does not reproduce, relaunch from the commit p5 ran.
- **Confirmed if:**
  - the first stopped 50 m cell is in lane 0 at 10.68–10.73 km at about minute 12–13, followed by lane 1 at the
    gore and lanes 2–3 further back; and
  - `diag_lockveh` lists a T.H.52 entrant bound for the corridor's end at rest at the auxiliary lane's end,
    exiters behind it, and exiters at the fronts of lanes 1–3 outside the 5 m zone.
- **Refuted if** the first standstill is elsewhere (for example at the 40648744 acceleration lane's end at
  10.20 km, or past 10.73 km), or if the front of lane 0 is not a through-bound entrant.
- **What it also shows.** The same trajectories show which command holds each lane front in the minute before
  the lock, which answers §3's open question.

**Cost [estimate].**

- About 4 min for the 16 runs (p5 took 204 s at 14 processes, and the trajectories are written in every run and
  only kept here).
- About 3 min for the three readers on one 35-minute trajectory.
- 10–15 min of boot and setup through the bucket.
- Total: about 20–25 min billed on n2-standard-16 at about $0.78/h, so **about $0.30**. The 45-minute cap bounds
  it at about $0.60. The disk is negligible: a few GB of kept trajectories on the 120 GB disk.

**Optional, on the same VM: a paired causal check.** This needs a small code change, a `--weave-set` option on
`i94_netfix_probe.py` like `merge_model_selfcheck.py`'s.

- **The check.** Run the same seed with `entrant_giveup_m` = 5.
- **Why it is paired.** W1 acts only once its condition first holds, so the run should match the collapsed one
  until W1 first fires [hypothesis: check that the trajectories agree up to the first `n_entrant_took_exit`].
- **What it shows.** If W1 first fires on the lock's front entrant and the run then does not lock, the stranded
  entrant is shown to be the cause, not only the first vehicle in the queue.
- **Cost.** About 4 more minutes.

## 7. Proposed fix direction

*Proposal only. Each part needs a dated amendment and the protocol's checks; nothing here is adopted.*

1. **Detect and report locks (reporting only, no change to the physics). This is the first step.** Done
   2026-10-07 (§10); W1's C4 has not been replaced, which is an amendment and the owner's.
   - **What counts as a lock**, read from files every replicate already writes:
     - at the run's end, a through-bound entrant at rest within 1 m of an auxiliary lane's end and no vehicle
       between that gore and the next entrance (`vehicles.parquet`, as in Table 4); or
     - during the run, a scored 5-minute window with zero flow at a station whose upstream neighbour is queued.
   - **How to report it.** Report "k of n replicates locked (section, onset)" next to every criterion and keep
     every replicate in every criterion. Add the without-locks value only as a labelled sensitivity.
   - **W1's C4.** Replace its corridor criterion ("lowest departed share ≥ 0.8 × the median") with this record,
     because that rule passes every lock seen here.
2. **Remove the root: the stranded entrant.** W1 (`entrant_giveup_m`) is the existing remedy:
   - It is pre-registered and implemented.
   - On the T.H.52 fixture it cut the stranded time from 18.9 to 3.8 s per run, with no safety cost.
   - It failed only its frequency caps: F3, 1.04 % at one seed; F5, Ruth St fixtures with 73–128 entrance
     departures (docs/WEAVE_LOSS_DIAGNOSIS.md §8).

   This note adds a fact the fixtures could not show: a stranded entrant stands at the front of all three
   four-hour locks, and locks occur in 3 of 20 replicates. Revisiting W1 is the owner's decision. If revisited,
   it needs:
   - an amendment that weighs the 1 % cap against that lock rate;
   - the corridor round of docs/WEAVE_LOSS_DIAGNOSIS.md §8.6, paired on step 3's seeds, with C4 replaced as in
     item 1.

   **The prediction to register:** zero locks at T.H.52 and Ruth St in 20 seeds, and given-up entrants within
   C5's 1 %.
3. **Possibly needed with W1, and not proposed until the trajectories show it.**
   - **The gap.** Lane 1's front exiter stands 5.0–10.8 m short of the gore, outside the exit give-up's 5 m.
     Once the auxiliary lane moves it should get a gap. If it does not, the give-up would need to key on "front
     vehicle of its lane, halted, no change possible" rather than on a fixed distance.
   - **What it would take.** The earlier bounded-patience variants (WP-52/53) were removed on 2026-10-06, so
     this would need its own derivation and fixture criteria.
4. **Not fixes:**
   - turning teleporting on (it hides the lock and deletes vehicles);
   - dropping locked replicates (CLAUDE.md §0.6);
   - tuning weave or driver knobs until the locks vanish from a battery.

## 8. Limits

- **The slice's front row is inferred.** It comes from its counters and the three four-hour front rows; the
  slice's own vehicles table was not archived.
- **The four-hour evidence is a run-end snapshot.** `vehicles.parquet` holds each vehicle's last sample, not the
  sequence. Onset times are arrival-based (gore → corridor end or C-D re-entry), good to about ±2 min.
- **Table 2's stop times assume a constant rate.** Each station is assumed to carry its reference rate until the
  standstill reaches it and nothing afterwards.
- **Distances use the corridor axis**, not lane positions. Lane 1's 5.03 m in …044631 cannot be placed on
  either side of the 5 m threshold.
- **Rates rest on few events.** 3 in 20 and 1 in 8. The k comparison is not significant, and the slices and
  four-hour runs differ in length and demand window.
- **The k 0 control is read from link-hours only.** Its per-replicate files were not read.
- **The summary shifts in §5 are on the nine-day, ungated artifact**, not on the gate's day-set values.
- **The causes in §3 are hypotheses.** The easing and cooperation commands, and SUMO's own lane-end approach,
  are not shown by these files.

## 9. Reproduce

All reads are with `uv run --no-sync python` (json, pandas, scipy); no simulation.

- **Probe table.**
  - For each `runs/p5/i94_netfix_probe/*/*/*/*/meta.json`, read:
    - `journeys`;
    - `weave_sections[*]` (pick by `exit_edge`);
    - `lane_end_giveups`;
    - `scripted_merges`;
    - `ramps[*].n_departed / n_planned`.
  - From the `readings.json` beside it, read `crossings_by_station` (summed over lanes) and
    `discharge_windows.S97.sim_veh_h`.
- **Battery per-replicate files.**
  - Extract `runs/mndot_i94_wb_stpaul_weave_xlsfg_dc/baseline/db9fbab5fc6e/*/{meta.json,vehicles.parquet}`
    from stage 23's `final.tgz` (`tar -xzf final.tgz --include '…/meta.json' --include '…/vehicles.parquet'`).
    Never the trajectories.
- **Front row.**
  - Take vehicles with `arrived` false and `last_t_s` = 14,400.
  - Per `last_lane`, sort by `last_x_m` descending.
  - The gore is the weave ramp's `attach_end_x_m` in the meta.
  - "Past the gore" means `last_x_m` greater than the gore and below the next entrance's `attach_x_m` (or the
    corridor's end).
- **Onset.**
  - Take arrived vehicles with `entry_x_m` < gore − 50 m and `last_x_m` > gore + 10 m.
  - The last `last_t_s` among them gives the last crossing, and their counts per 10 minutes give the decline.
- **Summaries.** `per_seed[*]`:
  - `geh_pass_fraction`, `rmspe`, `metrics`, `waiting`;
  - `link_hours[*].geh` ≥ 5 for the failures;
  - t-intervals over replicates.
- **Rates.** `scipy.stats.beta` (Clopper–Pearson) and `scipy.stats.fisher_exact`.
- **p8 rule.**
  - `results.{as_built,netfix}.k1.0_kr0.1.scores.common.discharge_veh_h_by_seed.S97` in
    `artifacts/i94_netfix_probe.json`.
  - Error = |mean − 4,490.5| / 4,490.5 over the seeds kept.

## 10. Detector (2026-10-07)

Item 1 of §7 is implemented as `validation.locks` (docs/CONTRACTS.md, "Locks", 2026-10-07). Reporting only: no
runner, scenario, hash or golden changed, and nothing was simulated.

- **Definition.** A run locks when vehicles stand with zero discharge past a point for at least 10 minutes while
  vehicles are queued upstream of it.
- **Two readers, both on files every replicate keeps.**
  - The space-time reader takes `edges.parquet` (15 s × 100 m Edie bins). A cell stands when its density is at
    least 20 veh/km and its flow at most 18 veh/h. A cell standing for 10 minutes is locked. The head is the most
    downstream locked cell whose downstream neighbour is not locked when it starts.
  - The run-end reader is §2's front-row method on `vehicles.parquet`: a front vehicle with an empty road ahead and
    a queue behind, and no vehicle from upstream seen past it for 10 minutes before the end. It gives an upper bound
    on the onset and a lower bound on the duration.
- **What each run records.** Locked or not, and per lock:
  - onset and duration;
  - head `x`, edge, and the weave, merge or diverge whose gore is within 250 m downstream;
  - vehicles trapped in the network (upstream, bound past the head);
  - vehicles never departed from origins upstream.
- **Where it is reported.**
  - `no_locks` is a criteria row of every run set (PASS / FAIL / NOT RECORDED).
  - The battery artifact gains `per_seed[i].locks`, `locks` and `zero_locks`; the sweep summary gains the same per
    cell.
  - The report's Model integrity section, banner and limitations name every lock, and the client summary's
    confidence table states whether any run locked.
- **Re-detection on step 3 [computed].** The run-end reader on the 20 `vehicles.parquet` of `db9fbab5fc6e`
  (no `edges.parquet` was archived) finds exactly the three locks of §2 and no other:

  | seed | section | head x [m] | last discharge (onset ≤) | duration ≥ | trapped in network | never departed upstream |
  |---|---|---|---|---|---|---|
  | 677105600768189526 | T.H.52 weave (`on-ramp 769818012`) | 10,731.57 | 11,848.5 s (08:47.5) | 42.5 min | 3,049 | 3,294 |
  | 3011106312394044631 | Ruth St weave (`on-ramp 745524613`) | 5,316.59 | 11,252.0 s (08:37.5) | 52.5 min | 1,928 | 3,398 |
  | 8026499204807041784 | Ruth St weave (`on-ramp 745524613`) | 5,316.56 | 13,585.0 s (09:16.4) | 13.6 min | 1,593 | 382 |

  Battery-level: 3 of 20 locked, 15 % (Clopper–Pearson 95 % 3.2–37.9 %). The last-discharge times are Table 4's
  last crossings (t = 0 at 05:30).
- **The space-time reader on real runs [computed].** On the 25 I-24 runs with `edges.parquet` in `runs/`, it finds
  locks in exactly the three sublane probes docs/I24_VALIDATION.md records as locking (82–93 min standing) and none
  in the other 22.
- **Limits.**
  - The space-time reader pools lanes, so a lock of one lane beside moving lanes is seen only by the run-end
    reader, and only if it lasts to the end.
  - The run-end reader cannot see a second lock upstream of a first while vehicles stand in the first's queue.
  - Neither reader has been run on an I-94 battery with `edges.parquet`; the next battery records both.
