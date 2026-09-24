# Roadmap — next phase (from 2026-09-02)

Companion to `NEXT_STEPS.md` (which sets strategy) and `CLAUDE.md` (which sets
technical law). This file is the working plan: what happens next, in what order,
and what is blocked on whom.

Horizon: **~2 months.** Goal: **all four tracks**, segmented so they can be
worked independently, with one shared critical path feeding them all.

---

## 0. Where things stand

Released **v2.1.0**. Two engines, four controllers, calibration from public
NGSIM, a 540-run sweep, a validated ring benchmark in CI, a product layer that
runs from one command, 480 tests at 95.5% coverage on the physics.

The single weakness, unchanged since v2.0.0: **every headline result lives on
either a synthetic corridor or a 640 m site that fails 5 of 6 FHWA criteria.**
Everything below is downstream of fixing that.

The data to fix it is now on disk: `data/i24motion/` holds the 30 Nov 2022
INCEPTION day (4 hours, 4 miles, 5.4 GB compressed / 19.5 GB as a single JSON)
plus corridor auxiliary information.

---

## 1. Critical path: the I-24 flagship

Everything in Tracks A–C depends on this. Do it first, in order.

**1.1 Stream-process the INCEPTION day.** ✅ *Done 2026-09-02.* The archive holds one 19.5 GB JSON
array of per-vehicle records (MongoDB export shape: `_id`, `timestamp[]`, and
position arrays). It must never be extracted to disk. Write a streaming reader
that decompresses from the zip, parses object by object, filters to the study
segment and period, and writes compact Parquet. Extend
`calibration/loaders/i24motion.py` — the loader exists but was written against
the documented schema, not this file, so expect schema reconciliation.
*Output:* trajectories in the contract's Parquet shape, a few GB at most.
*Finding:* westbound 576,511 documents → 42.8 M rows at 5 Hz (993 MB) in 309 s;
every document is a **fragment** (median 117 m / 9.9 s) and tracking coverage in
the peak is ≈ 0.5–0.65 of vehicle-time, so counts and densities are lower bounds
while speeds are sound (docs/I24_DATA.md).

**1.2 Recalibrate on I-24.** ✅ *IDM done 2026-09-02; FD fit running.* Rerun the IDM population fit against the new
episodes. Expect far more than NGSIM's 2,452, from a modern instrument rather
than 2005 camera footage. This is where the "raw NGSIM noise" limitation that
our own artifact flags finally goes away. *Output:* `artifacts/idm_i24.json`,
`artifacts/fd_i24.json`.
  **Done 2026-09-03:** fitted, 236,717 bins, w = 16.1 km/h [15.7, 16.5] inside the band and consistent with the observed fronts and Newell; q_max and ρ_jam are coverage lower bounds (docs/I24_DATA.md, fundamental-diagram section).
*Finding:* 17,652 episodes (7× NGSIM), holdout gap RMSE 5.29 m (NGSIM 6.44 m);
T = 1.51 s, s0 = 2.53 m, a_max = 1.06 m/s² — `a_max` stays high on smoothed data,
so the noise explanation was not the whole story.

**1.3 Build `i24_replica`.** ✅ *Built 2026-09-02 (`scenarios/i24_replica.yaml`,
`scripts/i24_build_replica.py`).* Four miles of real geometry from the auxiliary
corridor data plus OSM, with measured boundary conditions at both ends. This is
what the 640 m US-101 site could never be: long enough for waves to form,
propagate and be measured properly.
*Finding:* 3.4 of the 4 miles (MM 62.7 → Bell Road) with two on-ramps and two
off-ramps modeled (`RampSpec`), measured downstream boundary on the exit edge,
demand from fragment crossings in two labeled arms (as tracked / divided by the
apparent coverage); SUMO's default lane-change eagerness created a spurious
diverge bottleneck, fixed by `FleetSpec.lc_strategic`.

**1.4 Validate.** ✅ *Done 2026-09-03 ([I24_VALIDATION.md](I24_VALIDATION.md)).* Run the full criteria battery, 20+ seeds. **The honest
expectation is that some criteria still fail** — and per CLAUDE.md §0.1 that
gets published as-is. But the wave-speed diagnosis
(`docs/WAVE_SPEED_DIAGNOSIS.md`) predicts this corridor should pass the
wave-speed criterion where US-101 could not, and that prediction is now a
falsifiable test of our own understanding.
*Finding:* 1 PASS / 5 FAIL in both demand arms. Coverage-corrected demand
takes RMSPE from 183% to 36.8% and produces a stop-and-go field that looks like
the recording, but insertion caps the demand at 82–84%, the jams stay
shallower than the real ones, and the fronts run at 8.7 km/h (standard
detector) / 12.4 (relative) against 14.2 / 16.4 observed. **The wave-speed
prediction is not confirmed on the corridor**; the fleet reaches the band on a
ring only above ~80 veh/km, a density this replica does not reach. What a pass
needs is now specific (full corrected demand through the entry; radar counts).
*Follow-up 2026-09-03 ([I24_CAPACITY.md](I24_CAPACITY.md)):* the insertion cap
is a **capacity** limit, not an insertion artifact — the population fitted on
congested episodes saturates at ≈ 1,650 veh/h per lane on a straight road,
below the 1,775 the instrument *tracked*. FHWA Vol. III step 1 applied: mean T
scaled 1.511 → 1.322 s to meet the tracked capacity (gap RMSE unchanged,
5.31 → 5.29 m); both arms rebuilt on that population (new config hashes);
step 2 fits one demand scale on the first hour's speeds with the second hour
held out. The battery is being rerun on all arms with the ring rows
evaluated. *Result (3 Sep):* **3 PASS / 3 FAIL per arm** (ring rows now evaluated and
passing; GEH, RMSPE and wave speed still fail). The corrected arm's fronts
moved to 10.4 km/h standard / 14.2 km/h stripe (in band with the stripe
detector) and RMSPE to 33.7%; the fitted arm inserts 95.5% of its demand. The
residual is the Old Hickory merge queue (I24_VALIDATION.md §0, I24_CAPACITY.md §5).
*Four-arm rerun (2026-09-05, cloud VM, criterion scored with the slant-stack
detector):* **5 PASS / 2 FAIL on each congested arm** (tracked 4 / 3). Link-flow
GEH (16.7–20.1% of link-hours under 5 against the recommended-coverage count
table, the artifacts' criterion row; 10.4–15.3% against the apparent-coverage
tables the arms were first scored on) and speed RMSPE (33.7–35.9%) still fail;
the wave row passes at 15.7–15.9 km/h against 19.9 observed with the same
estimator and would fail with the standard detector (8–10 km/h) — the
prediction confirmed as specified, detector-dependent, caveats in
I24_VALIDATION.md §0.4. The fitted-ramps arm buys one RMSPE point and 1.4 GEH
points on the criterion's count table (it had cost five on the
apparent-coverage table); the merge is still the residual.
*Diagnosis 2026-09-06 (I24_VALIDATION.md §0.5):* the 5-min speed criterion is
below the recording's own repeatability (its 15-min moving average differs by
33%; at 15-min the arms are 25–27% against a 15% floor); the flow target is now
the coverage estimator's recommended counts (schema 6; the shortfall is real
discharge); two map defects at the auxiliary lanes are corrected from the
landmark layer (`scripts/i24_correct_osm.py`); the merge crawl survives every
lane-change parameter, the ramp-origin eagerness field, the measured entry lane
distribution, and the sublane model gridlocks as configured — a merge lock of
the lane-discrete model. The non-locking merge model (the zipper junction with
its negotiation gap fitted) and the FHWA sequence on the corrected map ran as
the `zip` scenario family on 2026-09-06 (below): a documented negative result.
*Built 2026-09-06 (owner's list):* temporary lane closures and a heavy-vehicle
share (fitted from the recording's semis and trucks), on-ramp merge models
(`RampSpec.merge`: acceleration lane, zipper) as netconvert patches, ALINEA
ramp metering (`RampSpec.meter`), a capacity-aware FollowerStopper
(`follower_stopper_capacity`, headway cap), managed (HOV) lane rules, the
speed criterion by aggregation with its floor in the auto-report, and
config-hash policy v2 (defaults excluded, so schema growth stops moving
hashes). Single-seed probes: the capacity-aware controller halves
FollowerStopper's throughput cost for the same smoothing — a probe result
the twenty-seed cap sweep of 2026-09-07 does not reproduce (every cap value
costs what FollowerStopper costs; docs/I24_SWEEP.md, last section); the
merge models on the corrected map are in I24_VALIDATION.md §0.5 (j).
*Cloud calibration round (2026-09-06, I24_VALIDATION.md §0.6):* the zipper
family — corrected map, ramp-origin eagerness 1, measured entry lanes, zipper
junction gap 0.5 s chosen on the fit hour — went through the FHWA sequence
(demand level s = 0.925, then ramp levels and the exit share; held-out hour
reported) and the 20-seed batteries. Against the canonical family it moves
neither failing row: `zip_ramps` RMSPE 34.2% (canonical 34.8%), GEH < 5 on
23% of recommended-coverage link-hours (20%), stack wave speed 15.7 km/h
(15.7); the family is kept as the documented negative result of §0.5 (k), and
the merge admittance stays the open mechanism. The canonical heavy arm
(`speedcal_heavy`, 20 seeds) sits with the others: 35.5% / 23% / 17.3 km/h,
throughput 5,170 veh/h against 5,710 without the heavy share; the zipper
heavy arm 34.1% / 20% / no stack peak. The first VM was cut by its own hard
cap during the headway-cap sweep and lost three batteries and the sweep
(scripts/gcp/README.md post-mortem, LESSONS.md rows 14–16); a second VM the
same evening reran the batteries (identical numbers) and the sweep, with the
scripts now archiving after every stage. The headway-cap sweep (2026-09-07, I24_SWEEP.md last section):
the cap is not the lever — −34% to −38% throughput at every `h_max_s`
against FollowerStopper's −36%, same smoothing, same fuel penalty — so the
next controller has to change what it does at short gaps. *Fourth merge round (2026-09-07, I24_VALIDATION.md §0.7):* fourteen
single-seed probes of the levers no earlier round had touched — the
zipper's interleaving distance, the sublane model's lateral parameters with
internal links, keep-right, speed-gain, passing on the right. Inert,
destructive, or a flow-for-speed trade; SUMO's lane-change and junction
parameter space is exhausted for this merge. Next, in this order: a
scripted late-merge behaviour for ramp vehicles (a controlled-vehicle
class: run the acceleration lane at a target speed, seek a gap in its last
part; tested like a controller, single seeds first), a short-gap smoothing
controller, then radar counts for the flow target.
*Engine refinements, second round (2026-09-03/04):* the wave-speed detector is
benchmarked on synthetic congested fields and the criterion names its detector
(default: the slant-stack estimator; the standard detector finds nothing on
congested backgrounds); a gap-based coverage estimator agrees with the fitted
demand level (I24_DATA.md, last section); the lane-change model is a
calibration target with its own artifact; an out-of-sample fitter for ramp
levels, boundary discharge and gap acceptance runs on the VM; the same two
calibration steps applied to US-101 with no retuning improve speeds and
overshoot flows (US101_CALIBRATED.md). The merge parameters alone do not fix
the merge (I24_CAPACITY.md §6.1).

**1.5 Rerun the sweep on the flagship.** The penetration × compliance battery on
a validated corridor is the result the whole project has been building toward.
`docs/US101_PENETRATION.md` showed the no-cost claim is corridor-dependent; this
settles what it actually is on a real, long, multi-lane freeway.
*Status 2026-09-03:* **the corridor is not validated** (§1.4: 3 PASS / 3 FAIL per
arm after calibration; 1 PASS / 5 FAIL before; 5 / 2 in the 5 Sep rerun, with GEH
and RMSPE still failing), so this is being run and will be reported as what it is — the
battery on a replica that reproduces the recording's stop-and-go pattern but
not its criteria — never as a validated-corridor result.
*Result 2026-09-04 ([I24_SWEEP.md](I24_SWEEP.md)):* 500 runs on the fitted arm
(`i24_replica_speedcal`, cloud VM). **FollowerStopper at its literature defaults
costs throughput at every cell and the cost grows with penetration** — at 5% /
100%: throughput −36%, travel time +82%, fuel +111%, σ_v −56%, waves halved;
lane changes rise from 1.25 to 2.32 per vehicle-km at 20% (the D2 statistic).
The synthetic no-cost result does not survive a real corridor near capacity;
the next controller must be capacity-aware.
`scripts/i24_penetration_sweep.py --scenario i24_replica_speedcal` (500 runs,
cells ordered so the baseline and the 100%-compliance ladder land first;
metrics kept, trajectories discarded) → `scripts/i24_penetration_analyze.py`.

---

## 2. Track A — Science fair / competition

Depends on §1 landing. Judges reward a clear question, an honest method, and a
result you can defend under questioning.

- **A1.** A single-sentence claim and the one figure that proves it. Candidate:
  the penetration dose-response with CIs on the validated I-24 corridor.
- **A2.** A "what we got wrong and fixed" section. The PI-saturation
  spec error, JAD's oracle bimodality and the wave-speed diagnosis are *assets*
  here — self-correction is exactly what distinguishes real research from a
  polished demo, and it inoculates against the hardest judging question.
  *Drafted 2026-09-02:* [LESSONS.md](LESSONS.md), twelve corrections with
  evidence, including the I-24 fragment/coverage findings.
- **A3.** A live demo: `docker compose up`, pick a corridor, run a sweep, watch
  the heatmap. Already works; needs rehearsal and a fallback if wifi fails.
- **A4.** Poster/board assets from `docs/figures/` — print-styled already.
- **A5.** Anticipated-questions doc: why not LWR; why SUMO; what a GEH of 5
  means; why some criteria fail; what a 1% penetration result means practically.
  *Drafted 2026-09-02:* [QA.md](QA.md); the I-24 answers point at
  I24_VALIDATION.md and will be sharpened once the flagship sweep lands.

## 3. Track B — Preprint and academic outreach

- **B1.** Write the paper. The spine already exists across `docs/`: method,
  calibration, validation, sweep, controller comparison, plus two genuinely
  novel bits — the detection-latency result and the flux-cap comparison.
  *Outline drafted 2026-09-03:* [PAPER_OUTLINE.md](PAPER_OUTLINE.md) — the
  coverage finding (I24_DATA.md §4) is the paper's central methodological
  point; the I-24 sweep enters as a result on an unvalidated replica.
- **B2.** arXiv preprint (cs.MA or eess.SY), citing I-24 MOTION and Stern et al.
  as their licences require.
- **B3.** Cold emails, *after* the flagship validates: the I-24 MOTION team at
  Vanderbilt (whose data we used, with results they would find interesting),
  CIRCLES at Berkeley, TTI, NCTCOG, UT-Austin CTR. Offer the tool and ask for a
  problem, not a job (`NEXT_STEPS.md` §5).
- **B4.** The deferred-commitment JAD controller. If latency helps because it
  defers commitment, an explicit deferral rule should capture the benefit with a
  perfect sensor — turning an accidental finding into a designed one. This is a
  publishable result on its own and needs no new data.
  *Built 2026-09-03:* `controllers.jad` parameter `commit_delay_s` (unit-tested);
  experiment `scripts/jad_deferral_experiment.py` (baseline, perfect, perfect +
  30 s / 60 s deferral, noisy 30 s; 20 CRN seeds) → `artifacts/jad_deferral_summary.json`,
  written up in [JAD_DEFERRAL_RESULTS.md](JAD_DEFERRAL_RESULTS.md).

## 4. Track C — Product and business validation

- **C1.** The 10 discovery interviews (`NEXT_STEPS.md` §3.4). These need no code
  and can run in parallel with everything else. The last question — "what's
  missing?" — is the real product spec. *Kit ready 2026-09-02:*
  [INTERVIEWS.md](INTERVIEWS.md) (target roles, script, outreach template,
  record sheet); the conversations themselves need introductions (§6 item 6).
- **C2.** A hosted demo so a link can be sent to someone. *Done 2026-09-24 in its smallest form: docs/HOSTED_TESTER.md (Cloud Run).* Was local-only
  by design; a small cloud VM would change what outreach can accomplish.
- **C3.** Harden the auto-report as the sellable artifact — it is the one
  feature a consultant would pay for.
- **C4.** Corridor onboarding time-to-value: measure how long a new corridor
  actually takes end to end, and shrink it. The claim "any corridor in under a
  day" needs a number behind it. *Measured 2026-09-03:*
  [ONBOARDING_TIME.md](ONBOARDING_TIME.md) — ~1.5 h of machine time to a
  criteria table; one session of engineering for a corridor with a new data
  product, most of it reusable; the bottleneck is understanding the data.

## 5. Track D — Engineering depth (no venue required)

Work that improves the artifact regardless of audience. Good filler when
blocked.

- **D1.** ✅ *Done 2026-09-02.* Fix the wave detector above ~80 veh/km. Threshold segmentation labels
  the whole field as jammed in heavy congestion, so it finds nothing — exactly
  where a DOT cares most. Needs a gradient or relative-speed method.
  *Finding:* relative mode (`detect_waves(relative_frac=0.5)`, jam = below
  0.5 × p90 of the field) resolves the stripes: the 80 and 100 veh/km ring rows
  go from zero fronts to 62 and 46 fronts at 17.1 and 17.0 km/h (98% / 85% in
  band); the I-24 fleet gives 16.4 / 16.9 km/h there (WAVE_SPEED_DIAGNOSIS.md
  follow-up). A labeled variant; the §7.1 criterion stays on the absolute
  threshold.
- **D2.** Test the multi-lane hypothesis behind the US-101 fuel result by
  counting lane-change events against penetration. Cheap; either confirms or
  kills a stated hypothesis.
- **D3.** Compliance sweep on real geometry (only 100% has been run).
- **D4.** highD cross-validation once access arrives: clean multi-lane German
  motorway data, useful precisely because the fuel result hinged on multi-lane
  effects we could not test.
- **D5.** Deferred-by-policy items stay deferred until §1.4 passes: RL
  controllers, ARZ, the live estimator (`CLAUDE.md` §10).

---

## 6. What is needed from Ansh, and why

| # | Need | Why it blocks | Effort |
|---|---|---|---|
| 1 | **Permission to reclaim disk** — delete `runs/` (12 GB, gitignored and regenerable) and shrink the colima VM (23 GB) | 15 GB free cannot process a 19.5 GB file. This is the immediate blocker on §1.1. | 1 min to approve |
| 2 | **highD access request** | levelxdata.com form, manually reviewed. Only gates D4, so it is not urgent — but lead time is days, so submitting early costs nothing. I can draft the intended-use text. | 10 min |
| 3 | **Decision: hosted demo?** | Gates C2 and changes what B3 outreach can do. Needs a GCP project or similar. | a decision |
| 4 | **Naming decision** | See §7 — cheap now, expensive after a preprint and outreach carry the name. | a decision |
| 5 | **Which competition, and its deadline** | Track A's entire shape depends on the venue and date. | a decision |
| 6 | **Interview introductions** | C1 needs actual traffic engineers to talk to. Cold outreach works, warm is faster. | ongoing |
| 8 | **Push credentials** | ~~Resolved 2026-09-03.~~ The owner account `abc000cool` is now logged into `gh` on this machine; the 2026-09-03 commits (`34d215b` … `11ef522`) were pushed with `git -c credential.helper='!gh auth git-credential' push` after `gh auth switch -u abc000cool`, and the previously active account was restored afterwards. Future pushes need the same switch, or make `abc000cool` the active account. | done |
| 7 | **I-24 radar detector (RDS) counts for 30 Nov 2022** | The trajectory export tracks only ≈ 0.5–0.65 of vehicle-time in the peak (docs/I24_DATA.md §4), so every count-based input and criterion (demand, GEH) is a lower bound. The testbed's TDOT Wavetronix RDS gives 30-s volumes; if the i24motion.org data listing offers them for this day, they replace both the tracked demand and the observed side of GEH. Check the account's data listing; if absent, ask the I-24 MOTION team when writing to them (B3). | 10 min to check |

**Not needed:** the reconstructed NGSIM dataset. Its host (`its-rde.net`) is a
lapsed domain now serving unrelated content, and I-24 MOTION supersedes it. If
the denoised benchmark is ever wanted, the Montanino–Punzo method is published
and can be implemented directly against the raw data already on disk.

---

## 7. The name

`FlowState` is not scientifically wrong: "flow" is standard traffic-engineering
vocabulary (flow *q* in veh/h is fundamental to microscopic and macroscopic
models alike; the canonical car-following textbook is titled *Traffic Flow
Dynamics*). There is also an owned domain, a public repo, two releases and a
docs corpus carrying the name.

If a change is still wanted, the strongest candidate is **Lagrange**: the v1→v2
pivot is precisely the shift from an Eulerian description (a density field) to a
Lagrangian one (following individual vehicles), and the control literature calls
vehicle-based actuation "Lagrangian control." The name would encode the exact
scientific change. Alternatives and trade-offs are in the chat record; the
decision is cheap now and expensive after a preprint.

---

## 8. Suggested sequencing

| Weeks | Focus |
|---|---|
| 1 | Disk cleanup, §1.1 streaming loader, §1.2 recalibration. Submit highD request. Start C1 interviews. *(Done 2026-09-02, first day: §1.1–1.3, D1, the C1 kit, A2/A5 drafts; highD request and interviews remain owner-blocked.)* |
| 2–3 | §1.3 replica, §1.4 validation. D1/D2 while runs execute. |
| 4 | §1.5 flagship sweep. Track A materials begin. |
| 5–6 | B1 paper draft; A1–A5 competition assets. |
| 7–8 | B2 preprint, B3 outreach, C2 hosted demo if wanted. Buffer for what breaks. |

Nothing here is load-bearing on a single week: Tracks C and D run in parallel
and survive any slip in the critical path.


## Addendum 2026-09-16

- Merge, fifth round (docs/I24_VALIDATION.md §0.8): the scripted late merge
  is a negative result on I-24 (entry speed matches, ramp admittance falls);
  the model stays as a schema option. The sixth round is planned in
  docs/MERGE_ROUND6_PLAN.md: ranked candidates outside the merge itself,
  each with a pre-registered threshold and a cost; the lead candidate is
  data-only (the entry lane shares are vehicle-time shares from 5 Hz samples,
  applied by SUMO as flow shares).
- Production-readiness round (CHANGELOG, same date): path confinement, body
  caps, job recovery and reconciliation, dashboard contract, docs
  consistency. Carried forward: a pure-ASGI body counter for chunked JSON
  bodies, worker-side root checks in `microsim` and a macro-tier closure
  golden (all three closed on 2026-09-17); `schema_version` 6 for the four
  canonical validation artifacts is not reachable by re-scoring (six fields
  only a re-run battery writes).
- Website: the Results page's updated-record card is on branch
  `results-note` of the site repository; Vercel's daily build limit blocked
  its deployment on 2026-09-16 — merge and push once it resets.

## Addendum 2026-09-17, end of the block

- First work of the next block: the regression review's confirmed items
  (CHANGELOG, "Regression review of the day's changes"), above all one
  travel-time span per run on the API and sweep path (the report already
  shares one), the dashboard's confirm-dialog focus and demo-fallback write
  actions, and the pool wrapper re-embedding a child exception's message.
- Then: `api.schemas.CalibrationParams` exposing the fitter's new knobs;
  re-running the published reports and the I-24 and US-101 batteries under
  the corrected metric definitions (warm-up window, median travel-time
  span) so the artifacts and `docs/reports/` match the generator; the next
  full FHWA sequence on the flow-share entry lanes (`--entry-lanes
  observed_flow`, a cloud round of about five dollars); merge candidates 2
  to 5 of docs/MERGE_ROUND6_PLAN.md.
- Website: the Results page's updated-record card is live (site commit
  82c4aab, deployed 2026-09-17).

## Addendum 2026-09-17, evening

- Merge round six is closed (docs/I24_VALIDATION.md §0.9 to §0.11, docs/MERGE_ROUND6_PLAN.md
  addenda): candidates 1, 2 and 5 closed on data, 3 and 4 by their probes. The merge is
  recorded as a model-form limitation; the next research question is the discharge
  capacity of the calibrated IDM population under merging (about 5,880 against the
  observed 6,630 veh/h), a calibration question rather than a scenario one.
- The record's artifacts carry the corrected metric definitions and hash-policy-v2
  hashes as of the 2026-09-17 re-run; the 500-run sweep and the cap sweep keep the
  earlier definitions and are labelled so.

## Addendum 2026-09-17, night

- The calibration question is answered (docs/I24_VALIDATION.md §0.12): the merge
  shortfall is not in the car-following population. The merge stays a model-form
  limitation; the only remaining research route is a measured gap-acceptance merge
  model, outside the product scope for now.
- Published reports regenerated under the corrected definitions; the sweep and cap
  sweep re-run is on the cloud chain and lands overnight.
- Next product step: a pilot-shaped onboarding of a third corridor, end to end.

### Addendum 2026-09-23 — third corridor from public data, strategy axis, screening FD, tester path

Built in the 2026-09-22/23 block (CHANGELOG 2026-09-23):
- **Any-corridor onboarding** is a function and a dashboard flow, not a script: bounding box + bearing → OSM motorway extract → one-direction chain, ramps, station positions (`microsim.geo`, `corridor_from_bbox`); detector CSV → observations and demand artifacts (`flowstate.observations/1`, `flowstate.demand/1`); `POST /corridors` installs a preset and `POST /reports` scores GEH and RMSPE against the observations. MnDOT I-94 WB through east St. Paul is the third corridor (`docs/ONBOARDING_MNDOT.md`).
- **What the first battery taught:** a map's lane count through a merge is not to be trusted — OSM tags I-94 as three lanes straight through its entrances, its on-ramps starved (four of seven by the session's reading), and the corridor never congested. The onboarding path now compiles with netconvert's ramp guessing and maps the split pieces back onto the scenario's ids (lesson 28). The next product item is a lane-profile-versus-inventory check printed before any battery, and ramp discovery that accepts lane-add merges (White Bear Ave) and collector–distributor roads (Mounds Blvd).
- **Strategies beside AV controllers:** `strategies` on sweeps (VSL, ALINEA, both) through one shared config patch for CLI and API; ALINEA's target traces to the corridor's fitted diagram; the report carries a strategy-comparison table. First measured result on the I-24 arm in docs/I24_STRATEGIES.md (VSL: smoothing paid for in capacity); ALINEA's meter needs its stop placement fixed before it can be measured (lesson 31).
- **Screening tier on a calibrated diagram:** `ScenarioConfig.fd_calibration`, macro options on runs and sweeps, dashboard banners; the MnDOT diagram is fitted from per-lane one-minute samples.
- **Tester path:** `scripts/doctor.py` and `docs/QUICKSTART.md`.

Open after the block (design note: docs/WEAVE_MODEL_PLAN.md — the weaving-section model, ranked candidates, a two-day first slice and the falsifiers): the driver population on MnDOT is the I-24 fit (no Minnesota trajectories); the demand's bracket-closing residuals at White Bear Ave and the Mounds Blvd re-entry; the detector wave speed as a report context row (30-s data allows it); the dashboard onboarding flow needs a guided first run and progress detail per stage; a hosted deployment for an external tester was prepared for but not deployed (owner undecided).

### Addendum 2026-09-24 — the I-94 lock was a map defect; ALINEA ran; a hosted tester exists

Overnight block from 2026-09-23 23:14 CDT (scheduled to 09-24 03:45), all
compute on self-deleting cloud VMs. What changed the picture:

- **The I-94 WB lock had a map cause, not a driver-model cause.** Reading the
  weave-model slice (no new run) put the queue's origin at the corridor's
  downstream end, where netconvert had compiled the right-hand 12th Street
  exit onto an added left lane and the Mounds/Kellogg exit onto the two
  leftmost lanes of five; through traffic trapped in exit-only lanes stalled
  the corridor from t = 87 s (docs/ONBOARDING_MNDOT.md §9, lesson 32). Fixed
  with `--ramps.unset` and a connection patch through the new
  `OSMNetwork.patch_files`, pinned by a test on the committed extract, and
  generalised into a **split audit** on every onboarding (verdicts, generated
  patch, `--fail-on-split-defect`, dashboard table). The 4-seed slice probe
  on the corrected network raised the departed share to 0.878 (from 0.834;
  a session record, not yet in a committed artifact); the 20-seed batteries on
  the corrected network with the weave model were launched on the second VM
  and are not yet reported (they will be §10 of the MnDOT record).
- **Weaving-section model** (`merge: weave`, docs/WEAVE_MODEL_PLAN.md) built,
  golden-tested, reviewed (one latent defect fixed); its pre-registered slice
  criterion was not met on the defective map and is re-asked on the corrected
  one.
- **ALINEA ran** on the I-24 replica after the stop-placement fix: 20 seeds
  paired, travel time −33.4 %, fuel −15.6 %, σ_v −11.7 %, throughput −5.8 % at
  the reference section, many more and larger waves (docs/I24_STRATEGIES.md).
  The instance was deleted mid-sweep, so the two combined cells are partial.
- **Minnesota driver population** by capacity scaling: the target
  (1,907 veh/h/lane) is not reached — the curve plateaus near 1,700 — and the
  artifact says so (docs/ONBOARDING_MNDOT.md §8).
- **Ramp discovery** now finds collector–distributor split/re-entry pairs
  (White Bear Ave is one) and lane-add joins; ramp guessing and split fixes
  are the onboarding defaults.
- **C2 done in its smallest form:** a hosted tester on Cloud Run
  (docs/HOSTED_TESTER.md; the API key is handed out by the owner).

Open: the 20-seed weave batteries' verdict (this block's second VM); the
Mounds Blvd +775 veh/h detector residual; what limits the capacity plateau
of the scaled population; the meter counters in sweep archives; why the
first VM was deleted at 71 minutes (the pipeline now archives the guest's
idle-guard log and journal on exit).

### Addendum 2026-09-24 (block 3) — the grid closed, the weave re-derived six times, the scoring made to fit

Daytime block on 2026-09-24 (CHANGELOG `## [Unreleased]`, "2026-09-24 —
block 3"), compute on four more self-deleting cloud VMs. The runner, the
scoring and the MnDOT inputs all moved; every number below is from the
committed file named in parentheses.

- **Two sweep VMs died of memory, not of a mystery (lesson 33).** The guest
  journal, archived on exit since the previous block, shows the kernel OOM
  killer taking a FollowerStopper-under-strategy run of the I-24 arm
  (anon-rss 8.8 GB; 32 of them in the pool on a 125 GB machine), at 112/120
  and then 117/120 runs (docs/LESSONS.md row 33; CHANGELOG block 3). The
  pipeline caps that stage's pool at 12 (`scripts/gcp/pipeline_i24.sh`), the
  per-run metrics of a strategy sweep ride along with every data set so an
  interrupted sweep resumes, and a stored run counts as done on
  `metrics.json` alone — the 2026-09-24 resume of 112 stored runs had started
  all 120 over because the older archive carried no `meta.json` (CHANGELOG
  block 3).
- **The I-24 strategy grid is complete: six cells × 20 seeds, no incomplete
  cell** (`artifacts/sweep_i24_strategies_summary.json`, `incomplete_cells`
  empty; docs/I24_STRATEGIES.md, block-3 section). FollowerStopper 10 % alone
  costs throughput −49.6 %, travel time +124.7 %, fuel +208.0 %; **under
  ALINEA the same controller costs −28.9 % / +26.7 % / +61.7 % with σ_v still
  −54.8 %** — the meter keeps the mainline below the density at which the
  smoothing controller starves it. Under VSL it is worse than alone on every
  capacity metric (−53.3 % / +154.4 % / +238.3 %). ALINEA alone remains the
  only cell that improves travel time (−33.4 %) and fuel (−15.6 %). The meter
  counters sit in each cell's `diagnostics` (ALINEA alone: 0.13 % unstoppable
  passes at Hickory Hollow, none at Old Hickory; under FollowerStopper 444 /
  571 releases per run). One calibrated arm, one penetration.
- **Why the Minnesota population plateaus: EIDM on an IDM-fitted
  population.** Two more capacity grids (`artifacts/idm_capacity_probe_mnfleet_4l.json`,
  `artifacts/idm_capacity_probe_i24fleet_3l.json`; docs/ONBOARDING_MNDOT.md
  §10, "Why the plateau"): the corridor's fleet block on four lanes plateaus
  the same way (1,609–1,689 veh/h/lane), so the lane count is not the cause;
  the I-24 replica's plain-IDM fleet block on the same 3-lane road reaches the
  1,907 veh/h/lane target at T × 0.795 (T = 1.201 s). The corridor's
  onboarding default `model: EIDM` costs about 11 % of straight-road capacity
  at every headway. The car-following model of an onboarded corridor is a
  calibration decision to make deliberately; the probes are diagnostics and no
  scenario points at them.
- **The Mounds Blvd residual was a chattering lane loop, and the inputs were
  regenerated.** The +775 veh/h between S792 and S791 is S792's degraded
  lane-3 loop 3240 (617 veh/h at 14.3 % occupancy against 943 and 1,394 in
  the leftmost lane of the neighbouring stations; 78 % of its 00–04 samples null),
  not the split detector (docs/ONBOARDING_MNDOT.md §7 item 2). The loader
  takes reviewer-declared exclusions by name (`--exclude-detectors 3240`,
  written into the observations' `source`), and the observations, demand and
  scenario were rebuilt with the onboarding defaults: 17 ramps with the White
  Bear Ave C-D pair (out 374 / back 442 veh/h), the Mounds exit 701 instead of
  1,226 veh/h, the S792→S791 residual +366 (was +775) (§11; CHANGELOG
  block 3). The review of the rebuild found the wave context computed with the
  loop still in (recomputed: median 21.3 km/h unchanged, IQR 18.6–24.2), the
  slice's boundary schedule unshifted (fixed, pinned by a test), and the
  downtown entrance assigned 889 veh/h against its passage loop's 552 —
  evidence the +366 is still a detector question, not traffic (§11).
- **Weave model, six derivations on one fixture.** `tests/fixtures/weave_th52.osm`
  mirrors the T.H.52 section (mainline 4,500 veh/h with 25 % exiting, entrance
  1,400 veh/h, 20 min) and a strict `xfail` pins the lock; the entrance's
  departed count of 466 planned is the ledger (docs/WEAVE_MODEL_PLAN.md, the
  seven dated sections, the seventh measured and rejected; CHANGELOG block 3): **81** at the lock (lane 1 at
  0.0 m/s from minute 4; eight zipper variants worse) → **225** with follower
  cooperation as a car-following target (deferred forced changes 16,576 → 0,
  lock to crawl) → **317** with through traffic vacating the weave lane 150 m
  upstream (the HCM influence area) → **325** with the easing bounded by
  feasibility (`a_req ≤ b`; within seed noise, two premises measured and
  rejected) → **389–395** across seeds 3–5 with easing only when needed and a
  stopped changer–follower pair released after 2 s — no lock since the fifth,
  no collision → **411 / 412 / 420** with the sixth, whose trace put the bound
  on the ramp's own queue (3.1-s discharge headways imposed by easing against
  an overlapping leader; now 2.95 s, 1,215 veh/h). The criterion is 419
  entrants and lane 1 above 5 m/s in every minute; the sixth meets the first
  at seed 5 only and the second at no seed over seeds 3–5, so the `xfail`
  stays (the seventh derivation's table extends the sixth to seeds 6–8, 397 /
  424 / 407, where seed 7 meets both — seed noise around a rule 5–25 entrants
  short, not a pass). Golden
  `merge_weave` followed the rules: mean travel time 97.1 → 70.1 → 69.5 →
  69.82 → 70.83 s, σ_v spatial 7.59 → 4.274 m/s, throughput 1,429 → 1,687.5
  veh/h, config hash unchanged (`tests/golden/merge_weave.json`). The new
  counters (cooperations, vacated / refused, pair releases) are in `meta.json`,
  the API schema, the sweep summary and the dashboard's merge panel. The
  35-minute slice of the regenerated corridor under the second derivation's
  runner departs 0.892 over 4 seeds (lowest 0.847) against 0.878 on the old
  inputs and rules
  (`artifacts/mndot_rounds/weave_2026-09-24/slice_regenerated_corridor_cooperative_weave_1bed27f.json`).
- **Battery scoring: parallel, then made to fit in memory.** The cloud log
  showed 20 seeds of a four-hour corridor (1.15 GB of trajectory each) scored
  serially for about 100 min while 31 CPUs idled, then the report re-reading
  every trajectory three times; scoring now runs in a spawn pool
  (`--score-procs`), the report takes the stored metrics and draws the first
  seed only, artifacts byte-identical across pool sizes (CHANGELOG block 3).
  The pool is sized by memory: one worker measured at ≈ 545 B per trajectory
  row, ≈ 45 GB on an 80 M-row replicate — six of them would have asked a
  125 GB machine for ≈ 260 GB — so the default fits `640 B/row + 512 MB` per
  worker into 80 % of `MemAvailable`. Then the worker's copies were removed
  (one read into four arrays, one stable sort per replicate, windows as
  slices): `analyse_replicate` 416 → 115 B/row with byte-identical outputs,
  the constant 640 → 160 B/row (`validation.battery.SCORE_WORKER_BYTES_PER_ROW`;
  ≈ 13 GB per worker instead of 45). A review re-proved the outputs on 640
  probes and fixed two divergences on inputs the runner never produces (NaN
  timestamps, missing vehicle ids). The report's run set is the battery's own
  config-hash tree, so ring-benchmark runs are no longer report groups.
- **Hosted tester:** redeployed three times during the day from the pushed `HEAD` (revisions 00003–00005: the split-audit and merge-diagnostics panels, the onboarding opt-outs, the block-3 dashboard changes) with `scripts/gcp/deploy_cloud_run.sh`; the service record is docs/HOSTED_TESTER.md
  (the revision times are the service's own record, not reproducible from the
  tree).

- **The exit side of the weave (later on 2026-09-24).** The corridor's own
  slice showed the T.H.52 weave locking at its EXIT end (exit-bound vehicles
  could not drop into the auxiliary lane; 8,971 deferred forced changes in
  35 minutes) while the six entrance-side derivations had been tuned on the
  fixture's entrance. A fixture at the corridor's own T.H.52 flows (4,873
  veh/h arriving, 21 % exiting, 1,412 entering) reproduced the stall, and
  the exit-side rule — a due exiter has priority over the auxiliary lane
  (its beside-follower holds at rest), a halted exiter within 5 m of the
  gore's end is rerouted through and counted as a missed exit — makes that
  fixture pass; the review fixed the give-up's ordering. On the corridor
  slice it lifts the departed share to **0.979** (4 seeds, no starved ramp;
  0.848 with the sixth derivation, 0.892 with the second on the reset
  settings). The battery artifact now reports given-up exits per section
  and degrades its verdict above a 2 % share (`weave_exits`). The 20-seed
  four-hour battery with this runner (VM H) departed 0.743 of demand with
  speed RMSPE 0.782 — from 0.247 and 0.963 — the first change that moves the
  four-hour corridor, though it is not reproduced (docs/ONBOARDING_MNDOT.md §10).

Open / pending: VM J's 20-seed battery with the cross-edge 500 m window departed 0.859 (RMSPE 0.706; no seed locks; 8 % of link-hours pass GEH — after VM H's 0.743 with the 150 m window; docs/ONBOARDING_MNDOT.md §11), and the per-seed read (docs/ONBOARDING_MNDOT.md §11a) puts the head of every queue in the corridor's last 850 m — the 40648744 entrance, a plain lane-change merge carrying 890–1,150 veh/h inferred by conservation, feeding the T.H.52 weave, from the warm-up on; the locking seed locked there inside the warm-up — the first-hour standstill maps (VM I) put the head at 10.25–10.30 km in lane 0 at minute 11, insensitive to that entrance's merge model, and the two-entrance fixture at the corridor's flows names the mechanism: the weave's own vacate requests flood the middle lane (about 780 veh/h asked into a lane carrying 560) over the 230 m before the gore; bounding the requests by spare capacity is now the default, gap-conditioning them starves the section (kept as an option), nine short-section scalings were rejected on the 136 m Ruth St fixture, and the next attempt asks upstream of the entrance's merge with a window that crosses edges (docs/WEAVE_MODEL_PLAN.md, the last dated sections); the two earlier batteries on the regenerated corridor are recorded in docs/ONBOARDING_MNDOT.md §10 (VM F on the reset lane-change settings: departed 0.233 over 20 seeds; VM G on the corrected settings with the sixth derivation: 0.247, the lock at the T.H.52 exit end); the seventh weave derivation — the abreast entrant–lane-1 pair resolved
symmetrically rather than by which side pays — was then measured in fifteen
forms at seeds 3–8 and rejected (entrance mean 361.7 against the sixth's 411.8,
and a lock at a seed the sixth does not lock), so the runner keeps the sixth
(CHANGELOG block 3; docs/WEAVE_MODEL_PLAN.md, seventh dated section); the corridor's car-following model as a
stated calibration choice with a population derived under it; the +366 veh/h
at S792 as a detector question; the sixth derivation's trace harness and
per-variant runs are session records, not committed.
