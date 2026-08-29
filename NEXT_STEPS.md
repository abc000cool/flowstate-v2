# NEXT_STEPS.md — FlowState v2: Roadmap, Recommendation Review, and Business Model

Companion to `CLAUDE.md`. That file tells Claude Code *how* to build; this file
tells you *what order*, *why*, and *where the business is*.

---

## 1. My assessment of the audit's recommendations

The audit made six recommendations. Here is where I agree, where I'd modify,
and where I push back — including on my own earlier framing.

### 1.1 "Reframe the phantom-jam claim" — **agree, and do it first**
This is the highest-leverage change and it costs a weekend, not a rebuild.
Until the claim is reframed, every technically literate reader (professor,
judge, DOT engineer, investor) who knows that LWR is string-stable will
discount everything else in the project. Rewrite the report abstract and
README *now*, before any code changes: "FlowState v1 studied controlled
dissipation of seeded shocks in a first-order model; v2 studies emergent
stop-and-go waves in a calibrated microscopic model." That sentence converts
a flaw into a research narrative — v1 becomes your motivated preliminary work.

### 1.2 "Migrate to SUMO + IDM" — **agree, with one modification**
Agree it must be the primary engine (emergent waves, CIRCLES comparability,
OSM import for arbitrary corridors, free, industry-recognized).
**Modification:** the audit offered ARZ-with-relaxation as a parallel
macroscopic track. I recommend *demoting ARZ to "someday"* — it's a numerics
research project with no product payoff — and instead **keeping your existing
LWR/CTM code as the screening + state-estimation tier**. Three reasons:
(a) CTM + Kalman filtering is the actual industry-standard method for
real-time traffic state estimation, i.e., your v1 engine is the seed of the
future live-product tier, just with the wrong job title today; (b) a
Numba-compiled CTM sweeps parameter grids ~1000× faster than SUMO, which you
will want constantly; (c) it preserves four months of work and the
moving-bottleneck flux cap, which was genuinely correct. This two-tier design
is codified as ADR-1 in `CLAUDE.md`.

### 1.3 "Calibrate/validate on I-24 MOTION + PeMS, GEH < 5" — **agree; fix the sequencing**
Agree on targets and criteria. But I-24 MOTION is enormous and access takes
time, so the practical order is: (1) **register at i24motion.org today** —
it's the long pole; (2) meanwhile calibrate IDM on the reconstructed NGSIM
dataset (small, well-understood, immediately available) and optionally highD;
(3) fit the fundamental diagram from a PeMS pull; (4) graduate to I-24 for the
flagship validation. Also: the ring-road (Sugiyama/Stern) reproduction is a
*validation result in itself* and needs no external data — it's your first
credible milestone, achievable in weeks.

### 1.4 "Delete the nav-app deployment claims" — **agree completely; and this is the business pivot**
Waze CIFS can't carry speed advisories and Google's speed fields are
read-only, so the consumer-advisory business does not exist as a channel.
Rather than treating that as a loss, treat it as the answer to "what's the
business?": **you are not in the advisory-delivery business; you are in the
simulation and decision-support business.** Advisory delivery, if it ever
happens, goes through partners who own an actuation channel (DOT variable
speed limit gantries, OEM adaptive cruise control à la CIRCLES). Section 3
below builds the model on this.

### 1.5 "Engineer for reproducibility" — **agree; it's also a sales feature**
Seeds, CIs, config hashes, golden tests — yes. Note that in the B2G world,
reproducibility isn't hygiene, it's the *product*: a calibration report a DOT
reviewer can rerun is worth more than a prettier dashboard.

### 1.6 "Outreach to I-24 MOTION / CIRCLES; open-source MVP" — **agree; add Texas, and add a thin product layer earlier**
Two modifications. First, you're in North Texas — use it: **Texas A&M
Transportation Institute (TTI)** is one of the largest transportation research
organizations in the country and in-state; **NCTCOG** (the DFW-area MPO) funds
regional congestion work; **UT Austin's Center for Transportation Research**
is strong in traffic flow theory. A cold-email from a student with a working,
validated open-source corridor tool + a NASA-mentored research record is a
credible cold-email. Second, the audit's MVP ("validated single-corridor
simulation") is scientifically right but commercially inert; I'd pull one
product feature forward into the MVP: the **auto-generated calibration/
validation report** (`validation/report.py`). It's cheap to build on top of
the validation work you're doing anyway, and it's the single feature that
makes a consultant or DOT engineer say "I'd pay for that."

---

## 2. Phased implementation plan

Assumes part-time student hours; timelines are honest, not aspirational.
Each phase ends at a decision gate.

### Phase 0 — Reframe + repo hygiene (Weeks 1–2)
- Rewrite README/report claims per §1.1. Archive v1 as a tagged release.
- Stand up the monorepo layout from `CLAUDE.md` §2; port `macrosim` with the
  full test battery (Riemann, conservation, CFL); CI green.
- Register for I-24 MOTION data access; request highD access.
- **Gate 0:** v1 behavior reproduced under `preset: v1_legacy` with tests.

### Phase 1 — Microscopic core (Weeks 2–6)
- SUMO 1.27.1 via pip; `ring_sugiyama` scenario; confirm emergent wave with
  no seeding; implement FollowerStopper, PI-sat, JAD, VSL as pure functions;
  reproduce Stern-style dampening on the ring.
- String-stability analyzer; instability-band map for default IDM params.
- **Gate 1 (the credibility gate):** emergence + dampening test green in CI.
  If waves don't emerge, fix IDM parameters/heterogeneity before touching
  anything else — nothing downstream matters until this passes.

### Phase 2 — Calibration (Weeks 6–12)
- Loaders: reconstructed NGSIM, PeMS CSV, (highD/I-24 as access arrives).
- IDM population calibration with holdout; FD fit with bootstrap CIs;
  demand fitter for `corridor_10km`.
- **Gate 2:** holdout gap-RMSE reported; calibrated params produce emergent
  waves with backward speed in the 14–22 km/h band.

### Phase 3 — Validation flagship (Weeks 12–16)
- `i24_replica` (or best-available corridor if I-24 access lags — a PeMS-rich
  California segment is an acceptable substitute) through the GEH/RMSPE/wave
  criteria; full penetration × compliance sweep, ≥ 20 seeds, CIs; macro-tier
  cross-check vs micro (the flux-cap comparison — small paper material).
- Auto-report end-to-end.
- **Gate 3:** criteria table with honest pass/fails. A documented *failure*
  to hit GEH < 5 with analysis is still a legitimate research output;
  fabricating a pass is project death.

### Phase 4 — Product layer (Weeks 16–24)
- FastAPI service + React dashboard: pick/upload corridor (OSM bbox) → run
  sweep → heatmaps → download report; Docker; API key.
- The `osm_generic` pipeline is the "any city" feature — invest here.
- **Gate 4:** one external user (mentor, teacher, TTI contact) completes a
  corridor study without you driving.

### Phase 5 — Outreach + business validation (parallel from Phase 3 onward)
- Publish the validation writeup (arXiv preprint + repo). Enter it in the
  competition/program cycle you're already in (this is strong ISM
  final-product / science-fair material).
- Emails: I-24 MOTION team (Vanderbilt), CIRCLES PIs (Berkeley), TTI, NCTCOG,
  UT-Austin CTR — offer the tool + ask for a problem, not a job.
- 10 discovery interviews with traffic consultants/DOT engineers (see §3.4).

---

## 3. Business model

### 3.1 The honest market read
- **Who pays for traffic simulation today:** DOTs, MPOs, and the consultancies
  they hire, using PTV Vissim, Aimsun, or TransModeler — commercial licenses
  commonly run five figures per seat — or free-but-DIY SUMO. Calibration to
  FHWA criteria is slow, manual, expert work measured in weeks.
- **The wedge:** a corridor-scoped, web-based tool that (1) onboards any
  corridor from OSM, (2) semi-automates calibration to public data, (3) runs
  smoothing/VSL scenario analysis, and (4) **auto-generates the calibration/
  validation report** reviewers demand. You are not competing with Vissim on
  breadth; you're competing with two weeks of a $200/hr engineer's time on
  one narrow, recurring task.
- **Who buys first:** small/mid traffic consultancies (fast procurement,
  acute pain), then university labs (freemium/credibility), then MPO/DOT
  pilots (slow procurement, big contracts). Not consumers. Not app users.

### 3.2 Model sketch
- **Open-core:** engines, controllers, validation library = MIT/Apache
  open source (credibility + academic adoption + your moat *is* the validation
  evidence). **Hosted product:** corridor workspace, compute, report
  generation, support = paid.
- **Pricing hypothesis to test, not assume:** per-corridor-study pricing
  (e.g., low hundreds per study) for consultants; annual site license for
  labs; pilot contracts for agencies. Validate in interviews.
- **Moat honesty:** thin technical moat (SUMO is free); the defensible assets
  are (a) published validation record, (b) the calibration-automation
  pipeline, (c) relationships with testbeds/agencies. Move on (a) and (c)
  early — they compound and can't be forked.

### 3.3 What "rolled out to different cities" means now
Not advisories on drivers' phones. It means: **any corridor in any city can be
onboarded into a calibrated digital twin in under a day.** That's the
`osm_generic` pipeline + calibration automation. City rollout = data
availability + one champion at an agency/consultancy, corridor by corridor.
Real-world actuation (VSL timing plans, fleet controllers) is Phase-6+
territory and only ever through a partner who owns the infrastructure —
budget years, not months, and treat it as upside, not the plan.

### 3.4 Discovery interview script (do 10 before building Phase 4 polish)
Ask consultants/DOT engineers: How do you calibrate today and how long does
it take? What tool do you use and what does it cost you per study? What do
reviewers reject reports for? Would an auto-generated FHWA-criteria report
from an open engine be usable in your workflow — and if not, what's missing?
(The last answer is your real product spec.)

### 3.5 Funding/credibility paths appropriate to your stage
Science-fair/competition cycle (ISEF-track, Regeneron STS) with the
validation study; arXiv preprint + open-source release; micro-grants aimed at
young builders (e.g., Emergent Ventures) once you have the validated demo;
university-lab collaboration (TTI/Vanderbilt) as the institutional on-ramp.
Venture funding is premature until at least one paying pilot exists — and this
may be a services-first business for a long time; that's fine.

---

## 4. Risks and kill-criteria

| Risk | Signal | Mitigation |
|---|---|---|
| Waves don't emerge post-calibration | Gate 1/2 failure | tune heterogeneity, T, a_max within literature ranges; switch to EIDM; this is a known-solvable problem — do not ship around it |
| I-24 access delayed | > 6 weeks | proceed with NGSIM+highD+PeMS; I-24 becomes v2.1 flagship |
| GEH < 5 unreachable on flagship corridor | Gate 3 | publish the honest gap analysis; still a legitimate research result and a better science-fair story than a fake pass |
| Scope creep (RL, ARZ, live data) | any pre-Gate-3 work on deferred items | `CLAUDE.md` §10 exists precisely to say no |
| Time vs. school year | phases slipping > 50% | cut Phase 4 scope to report-generation CLI (no dashboard); the science survives, the demo shrinks |
| No commercial pull in interviews | < 2 of 10 interviews express willingness to trial | keep it as open-source research infrastructure + academic route; the credential value alone justifies the build |

**The one metric that matters for the next 6 weeks:** the ring-road
emergence-and-dampening test passing in CI. Everything else is downstream.
