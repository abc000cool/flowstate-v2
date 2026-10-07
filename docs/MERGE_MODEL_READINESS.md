# Is the measured merge model worth the full rerun? (2026-10-07)

A decision document for the owner, written 2026-10-07 after the Amendment-1
driver calibration and before step 3's batteries report. It answers one
question: is the 20-seed corridor rerun on `merge: measured` worth paying for
now? It also fixes, before any further result, what would make it worth paying
for.

Nothing was run for this document, so the criteria in §3 come before every
result they judge. Every number is read from a named artifact or document, and
every estimate is labelled with its basis. No simulation, scenario, test or
code was changed.

Labels:
- **[artifact]**: read from the named JSON.
- **[record]**: quoted from the named document; a session record with no JSON
  committed.
- **[computed]**: computed here from the named artifact (paired t-intervals
  over seeds).
- **[estimate]**: an estimate, with its basis stated.

Fixture results are macOS records (docs/LESSONS.md). The corridor rounds on
Linux decide.

## 0. In plain English

**Not yet, and probably never in the merge model's current form.**

**It is cheaper than feared.** Running the measured merge model through the
20-seed tests on both corridors takes about 2–4 hours of one cloud machine,
about **$3–6**. Re-making every published corridor result on it would cost
about **$45–55**. Nothing on record comes near $200.

**The evidence is the obstacle, not the money.** The model had to pass some
cheap checks before the big tests, and it has failed the ones that matter:
- It does not reproduce how real drivers merge. Its self-check has failed at
  every revision.
- It did not raise the throughput of the Nashville merge.
- Today, with the newly calibrated drivers, it carried **52 veh/h less** than
  the existing weave model through the Minnesota test section. The 95 %
  interval runs from −96 to −9, so this is a real loss, not noise. It also had
  lower speeds and admitted fewer ramp cars.

A 20-seed rerun would almost certainly confirm a failure we can already see.
No test can make anyone 100 % sure of a model; what this document offers
instead is a rule fixed in advance.

**What would change the answer.** A revised merge model would first have to do
two things on free laptop runs (§3, C1–C5):
- pass its self-check;
- beat the weave model on the Minnesota section test by more than the noise.

Then two cloud probes, about $0.50–1 together, would follow (C6–C7). Only if
all of these pass is the ~$5 rerun worth paying for.

Until then, the run worth paying for is the one already going: step 3, which
runs the existing merge models with the calibrated drivers.

## 1. What "the full rerun" is, and what it costs

### 1.1 The acceptance rerun (what docs/MERGE_MODEL.md §4 deferred)

docs/MERGE_MODEL.md §4 says: "The 20-seed I-94 battery (acceptance (2), (4),
(5)) and the I-24 FHWA re-sequence with its battery (acceptance (1)) are
**not run in phase 2** (owner, 2026-10-05); the model cannot be called
accepted until they run."

Protocol Amendment 1 requires the acceptance runs to use the calibrated drivers
unchanged. The rerun today is therefore the runs below.

| # | Run (scenario, seeds, stage it copies) | Decides | Missing today |
|---|---|---|---|
| A1 | The 4-h I-94 weave scenario, 20 seeds (the scenario's own spawn, as `p1_mndot_ref` / `p4_i94_battery_gate`). It uses the fleet of `scenarios/mndot_i94_wb_stpaul_weave_dc.yaml` (mean `a_max` + 1 sd, keep-right 0.1) and the five measured zones of `scenarios/mndot_i94_wb_stpaul_weave_measured.yaml` (Hudson Rd, McKnight Rd, Ruth St, 40648744, T.H.52). Commands: `corridor_battery.py --replicates 20 --criteria-profile fhwa_tat3_2004`, then `baseline_gate.py` on the committed phase-1 day sets, as stage `p4_i94_battery_gate` does | protocol §9 (2) S790, (4) collisions, (5) no lock; gate C1/C3/C5/C6 | the `_dc` + `measured` scenario file; a stage |
| A2 | `scenarios/i24_replica_flow_speedcal_dc.yaml` with the Old Hickory on-ramp on `measured`. Steps: the demand refit (`i24_fit_demand_scale.py`, as `p4_i24_refit`); for the full FHWA re-sequence, also the ramp and boundary fit; then 20 seeds plus 20 ring seeds through `i24_validate.py --scenario … --label dc_measured` | §9 (1) | the scenario file; a stage |
| A3 | Collision robustness under varied drivers: the phase-1 uncertainty baseline design on `measured`, 4 samples × 2 seeds of 4 h (`artifacts/uncertainty_mndot_i94_wb_stpaul_p1_rehearsal.json`; MERGE_MODEL_BRIEF §4 (4), "then re-run stage `p1_uncertainty`") | §9 (4) beyond the calibrated point | a stage |
| — | The reference arm: the same seeds and drivers on the existing merge models | the comparison | nothing; step 3 is running it now |

No pipeline stage exists for A1–A3. Stage 21 ran the gates only, and stage 23
runs the reference models.

**Cost.** The machine is an n2-standard-32 at about $1.55/h. The project's
32-vCPU quota allows one such VM at a time.

| Item | Basis on record | VM-hours |
|---|---|---|
| A1 battery | The same battery on the weave reference took 2,356 s = 39.3 min ([artifact] `validation_mndot_i94_wb_stpaul_weave_xlsfg_p1.json` `wall_s`); earlier VMs took 39, 46 and 48 min (docs/ONBOARDING_MNDOT.md §11). `measured` ran the 35-min slice in 81.3 s against the reference's 76.9 s, +6 % ([artifact] `validation_mndot_i94_wb_stpaul_weave_slice_{measured,xlsfg}_p2b.json`). Result: 41.6–50.9 min [estimate] | 0.69–0.85 |
| A1 gate and report | "gate and report minutes" (stage 23 header, `scripts/gcp/pipeline_i24.sh`) [estimate] | 0.05–0.1 |
| A2 | Low end: a scale-only refit plus the battery, "about 30–35 min" (`p4_i24_refit` header) [estimate]. High end: the full re-sequence of one family took 1 h 45 min including three 12-min batteries (docs/I24_VALIDATION.md §0.10); one battery gives 1.75 − 2 × 0.2 = 1.35 h. `measured` on I-24 ran 388.9 s against 383.7 s, +1 % ([artifact] `i24_merge_experiment_measured.json`) | 0.5–1.35 |
| A3 | Gate C ran two of these runs one after the other, alone on the machine, in 1,302 and 1,630 s ([artifact] `merge_model_gate_c.json`, `scripts/merge_model_selfcheck.py colliding-pairs`). Eight run at once, somewhere between that and the battery's 39–48 min per run [estimate] | 0.4–0.8 |
| Boot, setup, I-24 data upload | Both recorded VMs were nearly all run time. Stage 21's VM ran "about an hour, about $2" (CHANGELOG 2026-10-05) for about 59 min of recorded stage time: gate A ≤ 471 s with its variants in parallel, gate B 2 × ~80 s, gate C 1,302 + 1,630 s in sequence. The driver grid's 28 min was likewise mostly runs. The range allows for boot, setup and the I-24 data upload [estimate] | 0.1–0.4 |
| **Total** | | **1.74–3.5** |

At $1.55/h, 1.74 h is **$2.70** and 3.5 h is **$5.43**.

`measured` may be as slow on the 4-h corridor as on the T.H.52 fixture rather
than the slice. On the fixture it took 68.5 s against 47.8 s for 20 seeds,
+43 % ([artifact] today's two `th52_section_dc_*` files). In that case:
- A1 grows to 0.94–1.14 h;
- the top of the range grows to 3.8 h, $5.90.

A cross-check from other work today: docs/PERFORMANCE_2026-10-07.md, which is
uncommitted at the time of writing. It measured on the laptop, with 10-min
slices at HEAD 571b9e4:
- the measured slice at 144× real time, against the weave slice's 169×
  (+17 %), which falls inside the range above;
- 9–12 % less simulator time after its changes. Those changes are not
  committed, so they are not counted here.

**About $3–6.** This agrees with the brief's estimate: "about $1.25" for the
I-94 battery plus "about $3" for the I-24 re-sequence (MERGE_MODEL_BRIEF
§5.13 D). It also agrees with the phase-2 owner report's "about $2–5".

### 1.2 Everything published, re-made on `measured`

`measured` could become the default for every acceleration lane and paired
weave (MERGE_MODEL_BRIEF §5.10). Every I-24 and I-94 configuration hash would
then move, and every published corridor result would have to be re-made. The
US-101 and synthetic 10 km scenarios have no ramp block, so they would not
move.

| Item | Basis on record | VM-hours |
|---|---|---|
| §1.1 (I-94 battery, I-24 re-sequence, collision check) | above | 1.74–3.8 |
| I-24 canonical arms (five arms plus heavy) | VM `flowstate-canon`: 1 h 15 min, including US-101's two arms (CHANGELOG 2026-09-17) | 1.25 |
| I-24 strategy sweep (120 runs) | VM AH: 121 min (docs/I24_STRATEGIES.md) | 2.0 |
| I-24 penetration × compliance battery (500 runs) | 6 h 33 min on the 2026-09-18 VM (CHANGELOG); "about 7 h an arm" (docs/I24_STRATEGIES.md) | 6.55–7.0 |
| I-24 headway-cap sweep (6 × 20 runs) | 2.8 h (docs/I24_SWEEP.md) | 2.8 |
| I-94 strategy sweep (12 cells × 20 seeds × 4 h) | No timing on record; 12 × the battery's 41.6–50.9 min [estimate] | 8.3–10.2 |
| I-94 uncertainty at the protocol minimum: 10 samples × 5 seeds × (baseline + 2 strategies) = 150 4-h runs | 7.5 × 41.6–50.9 min [estimate] | 5.2–6.4 |
| Extra VM launches (about five, one at a time) | 0.1–0.4 h each [estimate] | 0.5–2.0 |
| **Total** | | **28.3–35.4** |

At $1.55/h, 28.3 h is **$43.9** and 35.4 h is **$54.9**: **about $45–55.**

Three of these items buy nothing today:
- The I-94 strategy and uncertainty runs would be withheld by the failing I-94
  baseline gate (protocol §6).
- The owner declined to re-run the penetration battery, at about $22 for two
  arms (decision 6, 2026-09-27).

### 1.3 The $200 figure

No combination of recorded stages reaches $200:
- Re-making everything in §1.2 twice over, for example after one failed
  attempt, comes to about $90–110.
- The rerun that could accept the model (§1.1) costs about $3–6.
- The comparison arm that rerun needs is already paid for by step 3. Stage 23's
  own estimate for step 3 is 60–75 min, $1.55–1.95.

## 2. Evidence ledger: every test the measured model has faced

"Reference" is the model `measured` would replace in the same test:
- `weave`, with the I-94 reference configuration (`xlsfg`) on the corridor;
- `lane_change` at I-24 and McKnight Rd.

**Verdict counts (rows 1–11):**
- **PASS:** rows 1, 4, 9; row 10 is a reference check, passed.
- **FAIL:** rows 2, 3, 6, 7, 11.
- **No gain:** row 8.
- **Not run:** row 12.
- **Row 5** measures the test itself, not the model.

| # | Test (where pre-registered) | Pre-registered criterion | `measured` | Reference | Verdict | Source |
|---|---|---|---|---|---|---|
| 1 | Fixture grid, 37 runs: the 29-run weave grid plus McKnight Rd, the ramp fixture and T.H.61 (G0, MERGE_MODEL §4) | zero collisions, no lock | after A3: 0 collisions, 0 locks, 2 vehicle-steps at −9 m/s², 1.9 % exits given up | weave: 0 collisions, 2 locks, 20 steps, 0.9 % | **PASS**, and safer; more given-up exits | [record] MERGE_MODEL A1–A3; CHANGELOG 2026-10-05 |
| 2 | Self-check (a)–(d), McKnight Rd and T.H.52 fixtures, seeds 3–7 (MERGE_MODEL §3, G0) | all four pass; "a model that fails these does not represent the measurements whatever capacity it reaches" | **stage 1:** (a) fails on the weave zone (fitted lead / lag 0.99 / 1.27 s against inputs 0.46 / 0.92); (b) entrant − new follower −2.44 m/s and new leader − entrant +3.09 at McKnight; (c) follower / leader at 0.93–1.23 / 1.18–1.21 of normal. **A2:** every item fails. **A3:** still fails; entrants cross slower than their new partners, at about normal gaps | real drivers (Old Hickory): +0.68 / −0.56 m/s; gaps 0.6–0.9 of normal | **FAIL** at every amendment | [record] MERGE_MODEL A1, A2, last section; CHANGELOG 2026-10-05 |
| 3 | T.H.52 capacity no-lock test, `weave_th52.osm`, seeds 4–5 (G0) | lane 1's first 60 m above 2 m/s in every minute; ≤ 10 % unfinished; 0 collisions | seed 4 passes. Seed 5 reads 1.35 m/s in minute 9 (a 45-s stop wave). Entrants 349 / 316 of 466 (75 / 68 %) | the weave's own pin asks for 80 % of entrants | **FAIL** at seed 5 (strict xfail since A3) | `tests/test_microsim/test_microsim_merge_measured.py` |
| 4 | Ramp fixture, McKnight Rd, Ruth St, T.H.61 (G0) | 0 collisions, no lock | pass (not xfail) | — | **PASS** | same test file |
| 5 | T.H.52 section ceiling, old drivers, seeds 3–12 (A2.3) | if the ceiling fails (ii), no merge model can pass the test | (nothing to cross) | 4,770–4,863 veh/h; (ii-a) 10/10; (ii-b) 7/10; seed 3 fails at 19.97 m/s | test unreachable at seed 3 with the old drivers | [record] MERGE_MODEL A3 |
| 6 | T.H.52 locked section test, old drivers (G1; protocol §9 (3)) | all of (i)–(iv) at seed 3; the 20-seed form reported | seeds 3–22 mean 3,832 veh/h, 0/20 pass. Seed 3: 4,133, GEH 11.1, lowest station speed 15.3 m/s | weave 3,873 ± 106, 0/20 | **FAIL**; paired −41 ± 47, level | [record] CHANGELOG 2026-10-05, xfail reason; [artifact] `p3_driver_grid_2026-10-07/th52_section_ref_weave.json` |
| 7 | Gate A: I-24 Old Hickory, one seed (6914975401685141156), central parameters (MERGE_MODEL §4) | peak sections (2,200 / 3,200 m) above about 6,000, Old Hickory admitting ≥ the reference, 0 collisions | 5,882 / 5,864 veh/h; Old Hickory 2,109 / 2,109; 0 collisions; first two segments 43 / 40 km/h (observed 36 / 32); 15-min RMSPE 0.309; GEH < 5 share 0.222 | `lane_change` 5,836 / 5,810; 2,109 / 2,109; 0; 32 / 26 km/h; 0.232; 0.160 | **FAIL**. The +46 / +54 lies within one seed-to-seed sd (66 / 64 veh/h, [computed] from `i24_validation_speedcal.json`). The US-101 gap arm read 5,828 / 5,768. The Hickory Hollow weave arm read 5,654 / 5,581, admitting 1,766 | [artifact] `i24_merge_experiment_measured.json` |
| 8 | Gate B: I-94 35-min slice, 4 seeds, old drivers | "S790 flow, departed share, zero collisions, no lock" (no number fixed) | S790 (windows 2–7) 4,566 veh/h; departed 0.948; missed exits 0.9 %; RMSPE 0.431; 0 collisions | xlsfg 4,574; 0.970; 1.8 %; 0.461; 0 | **No gain**. S790 paired −8, 95 % [−149, +132]. Departed share paired −0.022 [−0.030, −0.013] [computed] | [artifact] `merge_model_gate_b_s790_*.json`, `validation_*_slice_*_p2b.json`; MERGE_MODEL A4 |
| 9 | Gate C: the two phase-1 colliding (sample, seed) pairs as 4-h runs | zero collisions | 0 and 0; departed 0.905 and 0.773 | the weave reference recorded 1 collision in each (phase-1 rehearsal) | **PASS** | [artifact] `merge_model_gate_c.json` |
| 10 | T.H.52 ceiling, calibrated I-94 drivers, seeds 3–12 (today) | A2.3 | (nothing to cross) | 4,826 ± 22 veh/h; 10/10 pass, including seed 3 | the locked test is passable again | [artifact] `p3_driver_grid_2026-10-07/th52_ceiling_dc.json` |
| 11 | T.H.52 locked section test, calibrated I-94 drivers, seeds 3–22 (today; protocol §9 (3)) | all of (i)–(iv) at seed 3; at least 20 seeds | 4,309 ± 95 veh/h; 0/20 pass. Criteria passed: (i) 4/20; (ii-a) 1/20 (seed 3, GEH 4.91); (ii-b) 0/20; (iii) 20/20; (iv) 20/20. Lowest station speed mean 15.9 m/s; entrance departed 0.923; 0.89 % given up; 0 steps at −9 m/s²; 0 collisions | weave 4,361 ± 83; 0/20. Criteria passed: (i) 11/20; (ii-a) 1/20 (seed 16); (ii-b) 0/20. 17.1 m/s; 0.951; 0.79 %; 2 steps; 0 collisions | **FAIL**, with a resolved loss against the weave: flow paired −52 (SE 21; 95 % [−96, −9]); lowest station speed −1.20 m/s [−1.87, −0.54]; entrance departed −0.028 [−0.044, −0.012] [computed] | [artifact] `p3_driver_grid_2026-10-07/th52_section_dc_{measured,weave}.json` |
| 12 | Acceptance (1), (2), (4) at corridor scale, and (5): the 20-seed batteries | protocol §9 | not run | — | **NOT RUN** | — |

**Seed 3, the locked acceptance seed.** Here `measured` is the closer of the
two:
- `measured`: 4,540 veh/h, GEH 4.91, which passes (ii-a); it fails only (ii-b),
  at 16.9 m/s.
- weave: 4,490 veh/h, GEH 5.65, 18.4 m/s.

This is one seed. The 20-seed paired comparison goes the other way.

**What the ledger says.**
- **Safety is the model's only consistent advantage.** It had no collision
  anywhere, no lock on the grid, far fewer hard stops, and the two phase-1
  collisions are gone. However, the reference also records zero collisions at
  the calibrated settings (the 75-run driver grid, the phase-1 battery, today's
  fixtures). The advantage shows only under varied headways (gate C).
- **On capacity it is level or worse wherever it was measured:**
  - +46 / +54 veh/h on one I-24 seed: inside the noise, and speeds were worse;
  - −8 veh/h on the I-94 slice: inside the noise, and departures were worse;
  - −41 veh/h on T.H.52 with the old drivers: inside the noise;
  - −52 veh/h with the calibrated drivers: outside the noise.
- **It does not represent the measurements it was built from.** The self-check
  fails, and by the spec's own §3 rule that means no capacity result could make
  the model acceptable.
- **The cause is structural, and on record.** SUMO brakes entrants for the end
  of their lane, and the runner can only lower speeds. An entrant therefore
  cannot be brought to the target lane's speed before it changes (MERGE_MODEL,
  last section). Three amendments (A1–A3) did not move it.
- **What is left is the crossing itself.** On the same fixture, the calibrated
  drivers lifted the weave by 488 veh/h [438, 538] (`th52_section_dc_weave`
  − `th52_section_ref_weave`, [computed]). The weave still sits 453 veh/h
  [396, 509] below the no-crossing ceiling (seeds 3–12, [computed]). That gap
  is the loss in the crossing itself, and neither merge model recovers it.

## 3. GO / NO-GO criteria, fixed now

Written 2026-10-07, before any result beyond those in §2. These criteria decide
whether the §1.1 rerun is worth paying for. They are not the acceptance;
protocol §9 still decides that.

Rules:
- **Order.** All of C1–C7 must hold, in order. The first failure stops the
  sequence, and nothing after it is run.
- **Model changes.** A change to the model is a dated amendment in
  docs/MERGE_MODEL.md, written with its reason before its first fixture run.
  These criteria then apply to it unchanged. A criterion that fails is
  reported; it is never re-thresholded.
- **Fixture setup.** Fixtures run on macOS, one SUMO process at a time. The
  calibrated drivers are the fleet block of
  `scenarios/mndot_i94_wb_stpaul_weave_dc.yaml`, passed with `--fleet-from`. The
  T.H.52 tests run at speed factor 1, as locked (MERGE_MODEL §2).
- **Statistics.** Paired intervals are t-intervals over the seeds: 19 degrees
  of freedom for 20 seeds, 9 for 10.

| # | Criterion | Measured with | Cost | Status today |
|---|---|---|---|---|
| C1 | **The self-check passes:** (a)–(d) of MERGE_MODEL §3 at both zones (McKnight Rd, T.H.52), seeds 3–7, calibrated drivers | `merge_model_selfcheck.py check` | $0, local, minutes [estimate] | Not yet run with the calibrated drivers (`check` has no `--fleet-from`). **Failed** with the old drivers at every amendment |
| C2 | **A paired gain on the locked section:** `measured` − `weave` exit-end flow, seeds 3–22, calibrated drivers, with a 95 % lower bound above 0 veh/h | `merge_model_selfcheck.py th52`, both models | $0, local, about 2 min ([artifact]: 68.5 s + 47.8 s today) | **FAILED** today: −52 [−96, −9] |
| C3 | **No regression on the locked criteria.** For each of (i), (ii-a), (ii-b), (iii) and (iv), at least as many seeds pass under `measured` as under `weave`. The paired lowest-station-speed difference has a 95 % upper bound ≥ 0 | the same runs as C2 | $0 | **FAILED** today: (i) 4 against 11 seeds; speed −1.20 [−1.87, −0.54] m/s |
| C4 | **Zero collisions** in every fixture run (the 37-run grid, the 20 T.H.52 seeds, the capacity fixture) and in C6–C7 | each run's `n_collisions` | — | holding: 0 everywhere |
| C5 | **No lock on the fixtures.** The lock flag is 0 in all 37 grid runs, and `test_th52_capacity_does_not_lock` passes at seeds 4 and 5 (on the fixtures' own fleets, as the tests are written) | `merge_model_selfcheck.py grid --model measured`; pytest | $0, about 1–2 min ([record] "about 1 min" for the grid, MERGE_MODEL_BRIEF §5.13) | **FAILED**: seed 5 has been a strict xfail since A3 |
| C6 | **An I-24 lift with the calibrated drivers.** One seed (6914975401685141156), `measured` and `lane_change` in the same run. `measured`'s peak sections (data x 2,200 / 3,200 m) must be ≥ 6,160 / 6,173 veh/h and ≥ `lane_change` + 65 veh/h at both. Old Hickory must admit at least as many as under `lane_change`, with 0 collisions and a 15-min RMSPE no more than `lane_change`'s + 0.03. Under rule S1 (§5) it runs on the demand-refit scenario | `i24_merge_experiment.py` | ≈ $0.4–0.8 [estimate] | Not run. The old-driver gate A (5,882 / 5,864; RMSPE +0.077) would fail it |
| C7 | **The I-94 slice, paired, 10 seeds**, calibrated drivers, `measured` against the reference (xlsfg). S790 (windows 2–7) gain with a 95 % lower bound above 0. Departed-share difference with a 95 % upper bound ≥ 0. 0 collisions. No lock: lowest departed share ≥ 0.8 × the median | `corridor_battery.py` + `merge_model_selfcheck.py station-flows` | ≈ $0.2–0.5 [estimate]; ≈ $0.5–1 with C6 on one VM | Not run. The old-driver gate B (departed −0.022 [−0.030, −0.013]) would fail it |

**Why these criteria.**
- **C1** is the spec's own precondition (§3).
- **C2–C3** are the cheapest test that can resolve a gain at all. Today's 20
  seeds resolved a 52 veh/h loss, so a gain of that size is detectable. They
  use the locked test and the drivers the acceptance runs will use.
- **C5** is G0 as the spec wrote it.
- **C6.** Single-seed peak sections vary by about 65 veh/h between seeds (the
  sd over the fitted 20-seed battery, [computed]). A seed more than one sd
  short of the GEH thresholds (6,225 / 6,238) gives little chance that 20 seeds
  clear them. The same-seed reference with the calibrated drivers already reads
  6,021 / 5,995: driver grid pair (1.0, 0), [artifact]
  `driver_calibration_i24.json`.
- **C7** puts numbers on gate B's unspecified pass before it runs again. Ten
  seeds narrow gate B's ±140 veh/h interval to about ±63 [estimate, from gate
  B's paired sd of 88 veh/h].

**Verdict today: NO-GO.**
- Three of the five local criteria (C2, C3, C5) have already failed for the
  model as it stands. Two of them failed on today's calibrated-driver runs.
- The one not yet run, C1, failed at every amendment with the old drivers.

**In its current form, the measured model is unlikely ever to justify the
rerun.** Its loss on the section test is resolved, and its self-check fails.
The cause behind both is that entrants cannot be raised to the target lane's
speed. The runner can only cap speeds, so it cannot change that. Only a revised
model can reach GO, and A1–A3 found no revision that raises capacity.

**What to spend on instead.**
1. **Step 3, already running** (stage 23; its estimate is 60–75 min,
   $1.55–1.95). It runs the existing merge models with the calibrated drivers:
   20 seeds on both corridors, the I-94 baseline gate, and a same-code I-24
   reference. This is the full rerun that matters now, and it is already paid
   for.
2. **Depending on step 3 (§5): the I-24 demand refit** under the calibrated
   drivers, if the I-24 plateau proves demand-limited. This is opt-in stage
   `p4_i24_refit`, about $1–1.5 as its own launch [estimate].
3. **The T.H.52 crossing loss, on fixtures** ($0). With the calibrated drivers
   the weave is 453 veh/h [396, 509] below the no-crossing ceiling. That gap,
   not the merge model's acceptance rules, is where any further merge work
   should aim, judged against C2–C3.

Whether to keep `measured` as an option or delete it is the owner's call;
neither costs compute. In its favour, it holds the only record of zero
collisions under varied headways (gate C).

## 4. The cheapest next experiments, ranked by information per dollar

| Rank | Experiment | Command or stage | Cost | Result that would change the decision |
|---|---|---|---|---|
| 1 | **Read step 3** | Already running. Artifacts: `artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc.json`, `artifacts/baseline_gate_mndot_dc.json`, `artifacts/i24_validation_dc.json`, `artifacts/i24_validation_flow_speedcal_ref.json` | $0 more | Rules S1–S4 (§5) |
| 2 | **The self-check with the calibrated drivers** (C1 on the current model) | `check` first needs a `--fleet-from` option, about 10 lines; `with_fleet` already exists for `th52` and `ceiling`. Then: `uv run --no-sync python scripts/merge_model_selfcheck.py check --seeds 3-7 --fleet-from scenarios/mndot_i94_wb_stpaul_weave_dc.yaml --out artifacts/p3_driver_grid_2026-10-07/selfcheck_dc.json` | $0, local, minutes | **Fails again:** close the line, with no further spend or amendment on `measured`. **Passes:** the calibrated drivers make the model represent the measurements, so one amendment aimed at the section loss is worth trying, judged by C2–C5 |
| 3 | **The speed-factor sensitivity** (pre-registered, MERGE_MODEL §2) | `uv run --no-sync python scripts/merge_model_selfcheck.py th52 --model measured --fleet-from scenarios/mndot_i94_wb_stpaul_weave_dc.yaml --speed-factor 1.245 --out artifacts/p3_driver_grid_2026-10-07/th52_section_dc_sf1245_measured.json`, then the same with `--model weave` and `…_weave.json` | $0, local, about 2 min | If `measured` − `weave` has a 95 % lower bound above 0 at factor 1.245, that is the one identified route to a `measured` advantage on I-94: it honours the speed factor, while the weave assumes 1. The next step would then be the speed factor through the driver check (protocol §7.2), not the rerun. Otherwise nothing changes. It does not satisfy C2, because the locked test runs at factor 1 |
| 4 | **The I-24 demand refit**, calibrated drivers, existing merge | stage `p4_i24_refit` (opt-in) | ≈ $1–1.5 [estimate: "about 30–35 min" plus launch] | Only under rule S1. **Refit reaches ≥ 6,225 / 6,238:** I-24's acceptance (1) needs no merge model. **Still below:** the remaining shortfall is in merge or discharge, and C6 runs on the refit |
| 5 | **Gate A with the calibrated drivers** (C6) | `uv run --no-sync python scripts/i24_merge_experiment.py --base scenarios/i24_replica_flow_speedcal_dc.yaml --variants flow_speedcal flow_speedcal_measured --procs 2 --out artifacts/i24_merge_experiment_measured_dc.json` (base `…_dc_refit.yaml` under S1); one VM with `--data-set i24` | ≈ $0.4–0.8 [estimate: two runs of about 384–471 s in parallel, plus launch and the I-24 upload] | Only after C1–C5. **Passing** is the first evidence that the model can serve I-24's acceptance. **Failing** ends the I-24 case |
| 6 | **The I-94 slice, paired** (C7) | Scenarios: `mndot_i94_wb_stpaul_weave_slice_measured.yaml` and the xlsfg slice (stage `p2_gate_b`'s recipe), each with the calibrated fleet (`idm_calibration: artifacts/idm_i24_capacity_amax_k1.0.json`, `lc_keep_right: 0.1`). For each: `corridor_battery.py --replicates 10 --procs 20 --keep-trajectories …`, then `merge_model_selfcheck.py station-flows --station S790 --clock-offset-s 5400 …` | ≈ $0.2–0.5 [estimate: slice runs of 72–97 s, [artifact] `driver_calibration_i94.json` wall times]; on the same VM as rank 5 | Only after C1–C5. **Passing C6 and C7 means GO** for the §1.1 rerun (≈ $3–6) |

Ranks 2 and 3 cannot turn today's NO-GO into GO, because C2 has already failed
for the current model. They decide whether one more amendment is worth writing
at all.

## 5. Step 3: placeholder and the rules that read it

> **Filled 2026-10-07 from the committed step-3 artifacts** (docs/DISCHARGE_CALIBRATION.md §4):
> - **I-94 S790**, 20 seeds, 06:30–07:30: 3,780–4,010 veh/h in every seed (docs/I94_RESIDUALS.md, from the
>   battery's per-seed station-hours), against ≥ 4,567 for GEH < 5 (observed 4,911). Realised demand mean /
>   lowest: 0.955 / 0.889. Collisions: 0. Gate C1 fail (61.8 %), C3 fail (33.9 %), C4 fail (4.9 km/h), C5 pass,
>   C6 fail (calibration days).
> - **I-24 peak sections** (2,200 / 3,200 m), 20 seeds, calibrated drivers at the old demand: 6,031 / 6,025 veh/h,
>   against ≥ 6,225 / 6,238; realised demand 0.996; collisions 0. With the demand refit (scale 0.925):
>   6,047 / 5,983 veh/h, realised demand 0.921. Same-code reference (old drivers): 5,850 / 5,821 veh/h.

**Reading by the rules below.** *S1* applied (realised share ≥ 0.99 with both sections below threshold), so
experiment 4 ran: the refit stayed below 6,225 / 6,238 and built a backlog, so by experiment 4's own wording
"the remaining shortfall is in merge or discharge". *S2* does not apply (neither acceptance is met). *S3*: the
I-94 shortfall at S790 is about 560–790 veh/h in the peak hour — larger than the fixture's 453 veh/h
weave-to-ceiling gap, which `measured` has not recovered. *S4*: these batteries are the reference arm of any
§1.1 rerun. **The NO-GO stands.**

Rules that read these results, fixed now:
- **S1 (I-24 demand-limited).** Suppose the calibrated I-24 battery's departed
  share is ≥ 0.99 and either peak section is below its threshold. The plateau
  is then read as demand-limited, as docs/DISCHARGE_CALIBRATION.md §3 reads it.
  Run experiment 4 before any I-24 merge probe, and run C6 on the refit
  scenario. A demand-limited section cannot show a merge gain.
- **S2 (acceptance met without the merge model).** If the reference meets
  I-24's (1) or I-94's (2) with the calibrated drivers, `measured` is not needed
  for that acceptance. Its only remaining purpose there is safety under varied
  drivers, which the uncertainty design tests, not a battery. The GO criteria
  are unchanged.
- **S3 (the I-94 gap).** If S790 stays below 4,567, the shortfall in veh/h is
  what any merge model must recover at T.H.52. Compare it with the fixture's
  weave-to-ceiling gap of 453 veh/h [396, 509]. `measured` has recovered none
  of that gap so far (row 11).
- **S4 (the reference arm).** Step 3's batteries are the comparison arm of any
  §1.1 rerun: same seeds, same drivers. Without them the rerun has no
  reference.

## 6. Provenance notes

- **The untracked artifact.**
  `artifacts/p3_driver_grid_2026-10-07/th52_section_dc_measured.json` is
  untracked at the time of writing; it was written 2026-10-07 at 00:27 CDT. Its
  pair, `th52_section_dc_weave.json`, is in commit 6de4e21.
- **The working tree.** The working tree also holds an uncommitted runner
  change from other work (`packages/microsim/microsim/runner.py`, last modified
  00:33, after the artifact was written). Nothing here used it. Only the
  baseline timing of docs/PERFORMANCE_2026-10-07.md (§1.1 cross-check) is
  quoted, as a cross-check.
- **The old-driver T.H.52 comparison.** "−41 ± 47" is quoted as recorded
  (CHANGELOG 2026-10-05). The record does not say whether ± is a standard error
  or a 95 % half-width, and its per-seed rows are not committed. Either way the
  difference is unresolved.
- **Grid and self-check numbers.** The 37-run grid and self-check numbers are
  session records in docs/MERGE_MODEL.md and the CHANGELOG. No JSON for them is
  committed.
- **Seed-to-seed spread.** The I-24 peak-section sd (66 / 64 veh/h) comes from
  the old drivers' fitted battery: `artifacts/i24_validation_speedcal.json`,
  `simulated.counts_per_replicate`, 2-h means of 5-min counts. Step 3 will give
  the calibrated drivers' spread.
- **GEH thresholds** solve GEH = 5 for the simulated flow:
  - 6,225 / 6,238 against 6,626 / 6,639 (I-24);
  - 4,567 against S790's 4,911;
  - 4,535 against the fixture's 4,877.
- **Cost.** All costs are on-demand n2-standard-32 time at about $1.55/h. Disk
  and bucket charges, a few cents per run, are left out.
