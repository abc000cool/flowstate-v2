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
evaluated. *Result:* **3 PASS / 3 FAIL per arm** (ring rows now evaluated and
passing; GEH, RMSPE and wave speed still fail). The corrected arm's fronts
moved to 10.4 km/h standard / 14.2 km/h stripe (in band with the stripe
detector) and RMSPE to 33.7%; the fitted arm inserts 95.5% of its demand. The
residual is the Old Hickory merge queue (I24_VALIDATION.md §0, I24_CAPACITY.md §5).

**1.5 Rerun the sweep on the flagship.** The penetration × compliance battery on
a validated corridor is the result the whole project has been building toward.
`docs/US101_PENETRATION.md` showed the no-cost claim is corridor-dependent; this
settles what it actually is on a real, long, multi-lane freeway.
*Status 2026-09-03:* **the corridor is not validated** (§1.4: 3 PASS / 3 FAIL per
arm after calibration; 1 PASS / 5 FAIL before), so this is being run and will be reported as what it is — the
battery on a replica that reproduces the recording's stop-and-go pattern but
not its criteria — never as a validated-corridor result.
`scripts/i24_penetration_sweep.py --scenario i24_replica_corrected` (500 runs,
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
- **C2.** A hosted demo so a link can be sent to someone. Currently local-only
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
