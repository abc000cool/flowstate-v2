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

**1.1 Stream-process the INCEPTION day.** The archive holds one 19.5 GB JSON
array of per-vehicle records (MongoDB export shape: `_id`, `timestamp[]`, and
position arrays). It must never be extracted to disk. Write a streaming reader
that decompresses from the zip, parses object by object, filters to the study
segment and period, and writes compact Parquet. Extend
`calibration/loaders/i24motion.py` — the loader exists but was written against
the documented schema, not this file, so expect schema reconciliation.
*Output:* trajectories in the contract's Parquet shape, a few GB at most.

**1.2 Recalibrate on I-24.** Rerun the IDM population fit against the new
episodes. Expect far more than NGSIM's 2,452, from a modern instrument rather
than 2005 camera footage. This is where the "raw NGSIM noise" limitation that
our own artifact flags finally goes away. *Output:* `artifacts/idm_i24.json`,
`artifacts/fd_i24.json`.

**1.3 Build `i24_replica`.** Four miles of real geometry from the auxiliary
corridor data plus OSM, with measured boundary conditions at both ends. This is
what the 640 m US-101 site could never be: long enough for waves to form,
propagate and be measured properly.

**1.4 Validate.** Run the full criteria battery, 20+ seeds. **The honest
expectation is that some criteria still fail** — and per CLAUDE.md §0.1 that
gets published as-is. But the wave-speed diagnosis
(`docs/WAVE_SPEED_DIAGNOSIS.md`) predicts this corridor should pass the
wave-speed criterion where US-101 could not, and that prediction is now a
falsifiable test of our own understanding.

**1.5 Rerun the sweep on the flagship.** The penetration × compliance battery on
a validated corridor is the result the whole project has been building toward.
`docs/US101_PENETRATION.md` showed the no-cost claim is corridor-dependent; this
settles what it actually is on a real, long, multi-lane freeway.

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
- **A3.** A live demo: `docker compose up`, pick a corridor, run a sweep, watch
  the heatmap. Already works; needs rehearsal and a fallback if wifi fails.
- **A4.** Poster/board assets from `docs/figures/` — print-styled already.
- **A5.** Anticipated-questions doc: why not LWR; why SUMO; what a GEH of 5
  means; why some criteria fail; what a 1% penetration result means practically.

## 3. Track B — Preprint and academic outreach

- **B1.** Write the paper. The spine already exists across `docs/`: method,
  calibration, validation, sweep, controller comparison, plus two genuinely
  novel bits — the detection-latency result and the flux-cap comparison.
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

## 4. Track C — Product and business validation

- **C1.** The 10 discovery interviews (`NEXT_STEPS.md` §3.4). These need no code
  and can run in parallel with everything else. The last question — "what's
  missing?" — is the real product spec.
- **C2.** A hosted demo so a link can be sent to someone. Currently local-only
  by design; a small cloud VM would change what outreach can accomplish.
- **C3.** Harden the auto-report as the sellable artifact — it is the one
  feature a consultant would pay for.
- **C4.** Corridor onboarding time-to-value: measure how long a new corridor
  actually takes end to end, and shrink it. The claim "any corridor in under a
  day" needs a number behind it.

## 5. Track D — Engineering depth (no venue required)

Work that improves the artifact regardless of audience. Good filler when
blocked.

- **D1.** Fix the wave detector above ~80 veh/km. Threshold segmentation labels
  the whole field as jammed in heavy congestion, so it finds nothing — exactly
  where a DOT cares most. Needs a gradient or relative-speed method.
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
| 1 | Disk cleanup, §1.1 streaming loader, §1.2 recalibration. Submit highD request. Start C1 interviews. |
| 2–3 | §1.3 replica, §1.4 validation. D1/D2 while runs execute. |
| 4 | §1.5 flagship sweep. Track A materials begin. |
| 5–6 | B1 paper draft; A1–A5 competition assets. |
| 7–8 | B2 preprint, B3 outreach, C2 hosted demo if wanted. Buffer for what breaks. |

Nothing here is load-bearing on a single week: Tracks C and D run in parallel
and survive any slip in the critical path.
