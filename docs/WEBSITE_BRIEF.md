# FlowState website brief

**Purpose.** This document is the prompt for building the public FlowState
website with Claude Design (Claude Fable 5.1). Paste it whole, attach the files
listed in §11, and iterate from there. Every figure and number in it traces to
a committed artifact in this repository; the site must keep that property.

---

## 1. The ask

Build the public website for **FlowState**, a calibrated corridor digital-twin
platform that reproduces stop-and-go ("phantom") traffic waves on real freeway
corridors and evaluates how sparse controlled vehicles and variable speed
limits dissipate them.

The bar is **Apple product page / activetheory.net / wisprflow.ai**: a
cinematic, dark, WebGL-driven site with a real-data introduction animation,
scroll-driven 3-D sequences, and genuinely interactive instruments. It has to
impress two audiences at once, in this order on the home page:

1. **DOT, MPO and traffic-engineering consultancy buyers.** They want to see
   that a corridor can be onboarded from OpenStreetMap, calibrated against
   public data, swept across controller strategies, and turned into an
   FHWA-style calibration/validation report a reviewer can rerun.
2. **Investors and a general technical audience.** They want the vision: the
   phenomenon (a wave that forms from nothing and travels backwards at
   roughly 17 km/h), the lever (one vehicle in twenty-two dissolves it), and
   the honesty of the evidence.

Both audiences must be served by the same first screen. Lead with the
phenomenon and the product together, not with a science lecture and not with
a sales pitch.

**Non-negotiable content rule.** FlowState publishes results *including the
ones that failed*. The site may not state, imply or animate any claim that is
not in §5 of this brief. No invented testimonials, customer logos, deployment
counts, "trusted by" rows, or "validated" badges. Where a validation criterion
fails, the site shows FAIL, in the same typographic weight as PASS, and gives
the cause. This is a differentiator: nobody else in this space shows their
failures, and the audiences we care about notice.

## 2. Brand and visual direction

* **Name:** FlowState. Wordmark in the display face, no icon lock-up needed;
  if a mark is wanted, derive it from the diagonal stripes of a space-time
  speed field (jams travelling backwards appear as diagonal bands).
* **Direction:** dark, cinematic. Near-black ground (`#07080a` or close),
  one cool accent family from the dataviz palette used in the repo figures
  (I-24 blue `#2a78d6`, wave-band green `#1baf7a`, a warm secondary
  `#eb6834` used sparingly for "uncontrolled / jam"), soft white text
  (`#e9e9e4`), muted ink (`#8b8a85`). The 3-D speed fields use a perceptual
  speed colormap from jam-red through amber to free-flow blue; keep it
  consistent everywhere on the site.
* **Typography:** a geometric display face for headlines (Geist, Inter Tight,
  or Söhne-like), a humanist text face for body, and a monospace face for
  numbers, config hashes and code. Large, calm headline sizes with tight
  tracking; generous line height in body text. Numbers are always tabular.
* **Motion language:** slow, physical, eased (`power3.out`, 600–1200 ms),
  parallax depth rather than bounce. Nothing loops distractingly in the
  background while someone reads. Every animated element has a
  `prefers-reduced-motion` fallback that shows the final frame.
* **Feel references:** activetheory.net (depth, WebGL transitions between
  routes), Apple product pages (scroll-pinned sequences that explain one idea
  at a time), wisprflow.ai (restraint, typographic confidence, product
  screenshots as hero objects), Linear.app (spec and docs pages).

## 3. Information architecture (tabs)

Top navigation with seven routes. Each route is its own full experience with
route-level WebGL transitions (the hero scene persists as a dimmed backdrop
and re-focuses per tab).

| Tab | Purpose | Primary audience |
|---|---|---|
| **Home** | Intro animation, the two-audience pitch, the four proof points, calls to action | both |
| **Product** | The workflow: onboard → calibrate → run → sweep → report; dashboard screenshots; API | buyers |
| **Science** | Emergent string instability, the controllers, the two-tier engine, why not LWR | investors, academics |
| **Results** | The evidence, with CIs and with the failures: controller comparison, dose-response, I-24 validation, wave-speed diagnosis | both |
| **Specs** | Engine, schema, performance targets, API surface, testing and reproducibility guarantees | buyers, engineers |
| **Docs** | The documentation index rendered as a docs site, the generated reports, the changelog | engineers |
| **About** | Provenance, the v1 → v2 story, roadmap, contact | both |

Footer on every route: repository link, changelog version (v2.1.0,
2026-09-02), "every number on this site traces to a committed artifact" with
a link to the artifacts directory, and a contact link.

## 4. The introduction animation (Home, ~14 s, skippable)

Built entirely from **real data** in `hero_data.json` (§11). No stock footage,
no procedural fakery. Two movements, then the page.

**Movement 1 — the corridor (0–6 s).** The I-24 westbound space-time
mean-speed field of 30 November 2022 (`observed.mean_speed_kmh`, 240 time bins
of 60 s × 65 space bins of 100 m; null bins have no tracked vehicle) rises out
of the dark as a 3-D surface: x is position along the corridor, depth is
time, height and colour are speed. The camera glides low along the surface so
the diagonal jam bands are seen travelling *against* the direction of travel.
A single caption fades in: "Nashville, I-24 westbound. 30 November 2022.
Every band is a stop-and-go wave moving backwards through traffic at about
14 km/h." (14.2 km/h is the observed front speed; see §5.)

**Movement 2 — the ring (6–14 s).** Cut to a top-down view of a 230 m ring
with 22 vehicles (`ring[2]`, the run named `follower_stopper_activated`).
Drive vehicle positions from `x_m` (metres along the ring, wrap at
`circumference_m`) and colour them by `v_ms`. Play at roughly 40× so the
emergence is visible: the fleet starts uniform, a jam condenses within the
first three minutes and circulates backwards. At `t = 300 s` the vehicle
flagged `is_av: true` lights up (accent colour, a quiet ring pulse) and the
jam dissolves over the following ~90 s. A small live readout shows the
per-minute fleet speed standard deviation from `sigma_v_per_minute_ms`
falling from 2.42 to 0.00 m/s. Caption: "One vehicle in twenty-two, running
FollowerStopper. Nothing else changed."

Then the camera pulls back, the ring becomes the "o" in the FlowState
wordmark, and the home page settles beneath it. Provide a skip control from
the first frame and a replay control afterwards. On reduced-motion, show the
final composed frame with the two captions.

The other two ring runs (`baseline`, `follower_stopper_from_start`) are for
the interactive comparison in §6.1, not the intro.

## 5. The numbers the site may use (with sources)

All confidence intervals are 95%, from 20 seeded replicates with common
random numbers unless stated. Cite the source document on hover or in a
footnote component; never round in a way that changes the claim.

**Controller comparison** (synthetic 10 km corridor, 5% penetration, 100%
compliance, 20 seeds; `docs/CONTROLLER_COMPARISON.md`,
`artifacts/m3_sweep_summary.json`, `artifacts/jad_oracle_summary.json`):

| Controller | σ_v, m/s | Waves per run | Fuel, ml/veh-km | Throughput change |
|---|---|---|---|---|
| Baseline, no control | 3.39 [2.83, 3.94] | 3.85 [2.59, 5.11] | 65.44 | — |
| FollowerStopper | 1.31 [1.24, 1.38] | 0.15 [−0.02, 0.32] | 62.19 | +0.9% (not resolved) |
| JAD, 30 s + 20% noise oracle | 1.33 [1.24, 1.42] | 0.35 | 62.17 | +1.0% (not resolved) |
| PI-with-saturation (Stern 2018) | 2.38 [2.12, 2.64] | 2.25 | 63.57 | −0.7% (not resolved) |

Paired reductions vs baseline: FollowerStopper −61.2% σ_v, −96.1% waves,
−5.0% fuel (all resolved). Deferred-commitment JAD with a perfect sensor and
a 30 s deferral matches the noisy-oracle cell (σ_v 1.331 vs 1.333;
`docs/JAD_DEFERRAL_RESULTS.md`).

**Dose-response sweep** (`docs/M3_RESULTS.md`): 540 runs, 27 cells of
penetration × compliance, 20 seeds each, paired CIs. Figures
`m3_sigma_v_vs_penetration.png`, `m3_sigma_v_reduction_matrix.png`,
`m3_spacetime_baseline_vs_fs.png`.

**Ring benchmark** (`scenarios/ring_sugiyama.yaml`, CI-gated): 230 m, 22
vehicles, waves emerge without seeding within 3 minutes; one FollowerStopper
vehicle removes them. The hero data pack is three seeded runs of exactly this
scenario.

**Emergent wave speed vs density** (`docs/WAVE_SPEED_DIAGNOSIS.md`,
`docs/figures/wave_speed_vs_density.png`): on a 1500 m ring the calibrated
US-101 fleet produces backward fronts at 12.0 km/h near critical density
rising to 17.1 km/h at 80 veh/km with 98% of fronts inside the empirical
14–22 km/h band; the I-24 fleet gives 16.4 km/h and 95% at the same density.
Two fleets calibrated from two instruments a generation apart agree.

**I-24 MOTION flagship** (`docs/I24_DATA.md`, `docs/I24_VALIDATION.md`):

* 19.5 GB JSON export parsed as a stream, never extracted: 576,511 westbound
  trajectory fragments → 42.8 million rows at 5 Hz in 309 s.
* Instrument tracks ~0.5–0.65 of vehicle-time at the peak, so counts are lower
  bounds and speeds are trustworthy; demand therefore run in two labelled
  arms (as tracked, coverage-corrected).
* IDM population fit on 17,652 car-following episodes (seven times the NGSIM
  set): holdout gap RMSE 5.29 m vs 6.44 m for NGSIM; T = 1.51 s, s0 = 2.53 m,
  a_max = 1.06 m/s².
* Replica: 3.4 of the 4 instrumented miles from real OpenStreetMap geometry,
  two on-ramps, two off-ramps, measured downstream boundary.
* **Validation, 20 seeds per arm: 3 PASS / 3 FAIL per arm** after FHWA-style
  capacity and demand calibration (`docs/I24_CAPACITY.md`), 1 PASS / 5 FAIL
  before. Ring emergence, dampening and replicate rows pass; link-flow GEH,
  speed RMSPE (33.7% best arm) and wave speed (10.4 km/h standard detector,
  14.2 km/h stripe detector against 14.2 / 16.0 observed) fail.
  **The wave-speed prediction is not confirmed on the corridor.** Cause, now
  local: a standing queue at the Old Hickory merge; from 2.2 km downstream the
  replica is within a few km/h of the recording. Figures `i24_wb_overview.png`, `i24_validation_fields.png`,
  `i24_validation_waves.png`.

**US-101 replica** (`docs/M3_US101_VALIDATION.md`): 2,452 NGSIM episodes;
validation honestly mixed, 1 PASS / 5 FAIL; the wave-speed failure diagnosed
as a 640 m site and operating-density artifact, not a calibration defect.

**Platform** (`CLAUDE.md`, `docs/M5_LOAD_TEST.md`): Eclipse SUMO 1.27.1 with
IDM/EIDM through libsumo as the primary engine; a Numba-compiled CTM/LWR
screening tier that is never allowed to make phantom-jam claims; four
pure-function controllers (FollowerStopper, PI-with-saturation, Jam-Absorption
Driving, VSL) plus a Gymnasium hook; FastAPI + RQ job queue; Vite + React
dashboard; Docker; 490 tests with a coverage gate of 85% on the core
packages; API load-tested at 10 concurrent sweep jobs; seeded, config-hashed,
bit-stable replicates. Current release v2.1.0 (2026-09-02).

**Out of scope, and the site must not suggest otherwise:** delivering speed
advisories to drivers or navigation apps. FlowState is simulation and
decision support.

## 6. Interactive instruments

Each is a self-contained component with a title, a one-line "what you are
looking at", the instrument, and a "source" footnote. They must work with
keyboard and touch. Defer their WebGL contexts until scrolled into view;
never run more than two WebGL contexts at once.

### 6.1 Ring sandbox (Home and Science)
**Already built as a deployable embed** (`embed/`, see `embed/README.md`):
45 real runs across vehicles × controlled vehicles × switch-on × seed, with
ring view, time-space diagram, readouts and baseline comparison. The site
can iframe it (`?embed=1`) rather than rebuild it; restyle only if the host
page's look demands it, and keep its provenance line visible.

The three runs from `hero_data.json` side by side or toggled: baseline,
controlled from the start, controlled at 300 s. Scrubber over time, play at
1×–60×, the per-minute σ_v strip chart below, and a "follow the wave" camera
mode that tracks the slowest vehicle. Optional 3-D view where the ring is
extruded along time into a helix so the backward-travelling jam appears as a
spiral band.

### 6.2 Corridor twin flyover (Product)
Scroll-pinned 3-D sequence of a stylised I-24 westbound corridor (four
mainline lanes, two on-ramps, two off-ramps, mile markers 58.7–62.7) with the
observed speed field draped over it as a translucent ribbon along time.
Scroll advances the camera and, in five stops, explains: (1) geometry from
OpenStreetMap, (2) boundary and ramp flows from the instrument, (3) calibrated
driver population, (4) 20 seeded replicates, (5) criteria table and report.
Use `i24_validation_fields.png` as a texture reference for what observed vs
simulated looks like; the actual observed field data is in `hero_data.json`.

### 6.3 Sweep matrix explorer (Results)
The 27-cell penetration × compliance grid as a 3-D bar field (height = σ_v
reduction, colour = whether the CI excludes zero). Hover or tap a cell for the
metrics with CIs. A controller selector switches the field. Data:
`m3_sweep_summary.json` (attached); mirror its structure rather than
re-typing numbers.

### 6.4 Observed vs simulated comparator (Results)
Two space-time speed fields with a draggable divider and a toggle between the
two demand arms; a strip beneath shows the criteria table for the selected
arm with PASS/FAIL. Figure references: `i24_validation_fields.png`.

### 6.5 Wave detector (Science)
The wave-speed-vs-density chart (`wave_speed_vs_density.png` as reference,
values from `wave_speed_sitelength.json` and `wave_speed_sitelength_i24.json`)
with a toggle between the absolute 40 km/h detector and the relative
0.5 × p90 detector, showing why the absolute detector finds nothing at high
density. Shade the 14–22 km/h band.

### 6.6 Controller explainer (Science)
Four cards. Each opens into a diagram: FollowerStopper's three parabolic
region boundaries in the gap–approach-rate plane with the commanded speed
surface; PI-with-saturation's Eqs. 3–5 with the additive gap correction; JAD's
slow-in / hold / fast-out phase timeline against a moving wave front, with the
deferral parameter; VSL gantries along a corridor. Equations typeset
properly (KaTeX), constants shown with their literature source.

### 6.7 Report preview (Product)
A scrollable, realistic rendering of the auto-generated calibration report
(`docs/reports/us101_replica/report.md` attached as the template): provenance
block with config hash and seeds, criteria table, per-replicate contours,
metric tables with CIs, limitations section. This is the product artefact
buyers will ask for.

## 7. Page-by-page content

### Home
1. Intro animation (§4) settling into the hero: wordmark, one line
   ("Calibrated corridor digital twins for dissipating stop-and-go traffic
   waves"), two buttons: "See the evidence" (Results) and "How it works"
   (Product).
2. **The two sentences** for the two audiences, side by side: "Onboard a real
   corridor, calibrate it against public data, sweep controllers and speed
   limits, and hand a reviewer a report they can rerun." / "A wave forms from
   nothing, travels backwards at 17 km/h, and one vehicle in twenty-two can
   remove it. We measure that, with confidence intervals."
3. **Four proof points**, each a tile with a live micro-visual and a number
   from §5: −61% speed variance at 5% penetration; 540-run sweep with paired
   CIs; 42.8 million I-24 rows ingested in 309 s; 3 PASS / 3 FAIL on the
   flagship after calibration, shown, with the cause.
4. Ring sandbox (§6.1) in compact form.
5. **What FlowState is not**: a short, calm section stating scope (simulation
   and decision support; no advisory delivery) and the honesty policy.
6. Closing call to action: request a corridor study, read the docs, view the
   repository.

### Product
Workflow strip (five steps) → corridor flyover (§6.2) → dashboard
screenshots (`dashboard_run_detail.png`, `dashboard_sweep_matrix.png`,
`dashboard_scenarios.png`) presented as device-free floating panels with
parallax → API surface summary (the endpoints in §8 of `CLAUDE.md`: scenarios,
runs, metrics, heatmap, sweeps, calibrations, reports; async jobs; API-key
auth) → report preview (§6.7) → onboarding-time section pointing to
`docs/ONBOARDING_TIME.md` (use its measured numbers only).

### Science
Why waves emerge (string instability, the IDM criterion, the unstable
density band as an animated diagram) → why the first-order model cannot
(the v1 lesson, stated plainly) → two-tier engine diagram (micro primary,
macro screening only) → controller explainer (§6.6) → wave detector (§6.5)
→ reading list (the references in `CLAUDE.md` §13).

### Results
Controller comparison table with CIs (§5) → dose-response figures and the
sweep explorer (§6.3) → I-24 flagship: the day (`i24_wb_overview.png`), the
coverage limitation, the two arms, the comparator (§6.4), the criteria table
with **3 PASS / 3 FAIL** (1 / 5 before calibration) and the cause per row, and the wave-speed prediction
test result stated as not confirmed → US-101 summary → "What would change
these results" (radar-detector counts, longer and denser corridor, highD).

### Specs
Engine and versions; the scenario schema as an annotated YAML block
(network, fleet, AV penetration and compliance, controller, sim, seed,
replicates; ramps and boundary for OSM corridors); performance targets
(ring ≥ 50× real time, 10 km corridor ≥ 5× real time, 20-replicate sweep
≤ 15 min, CTM 1,000 cells × 10,000 steps ≤ 1 s); reproducibility guarantees
(seed, config hash, version pinning, golden regressions); testing (490
tests, property tests, CI-gated ring benchmark); API reference table; system
requirements; licence and repository.

### Docs
A documentation site: left rail with the index from `docs/README.md`
(Architecture, Results, Generated report, Demo corridor gallery, Figures),
rendered Markdown with the figures inline, search, and the changelog. The
generated report pages get the same typographic care as the marketing pages.

### About
The v1 → v2 story in three paragraphs (v1 studied seeded shocks in a model
that cannot form jams; v2 studies emergent waves in a calibrated microscopic
model and publishes the failures); the roadmap in one figure; how to get in
touch; the "every number traces to an artifact" statement with the
repository link.

## 8. Technical requirements

* **Stack:** Next.js (App Router) + React, TypeScript, Three.js via
  react-three-fiber and drei, GSAP with ScrollTrigger, Lenis smooth scroll,
  Framer Motion for DOM transitions, KaTeX for equations, MDX for Docs.
  Tailwind is acceptable for layout tokens but the visual system must be
  bespoke.
* **Performance budgets:** first contentful paint under 1.5 s on a mid-range
  laptop; the intro must start within 2 s with a progressive loader (stream
  `hero_data.json`, decode the observed field first, then the ring runs).
  Keep the main thread free: parse JSON in a worker, build geometry once,
  update with instanced meshes and attribute buffers, not per-frame object
  creation. Lighthouse performance ≥ 90 on every route with WebGL paused
  off-screen.
* **Responsiveness:** phone, tablet, laptop, ultrawide. On phones the 3-D
  sequences become shorter pinned sequences with the same content; nothing
  is dropped, only simplified.
* **Accessibility:** semantic landmarks, keyboard navigation for every
  instrument, visible focus, captions for every animation, reduced-motion
  fallbacks, colour contrast ≥ 4.5:1 for text, the speed colormap paired with
  a labelled scale.
* **Theme:** dark by default; a light theme is not required for launch but
  tokens must be defined so one can be added.
* **No tracking beyond privacy-respecting page analytics; no cookie banner
  needed if none is used.**
* **Content integrity:** all numbers live in one typed content module
  (`content/results.ts`) with a `source` field per value that renders the
  footnote; no number is typed twice.

## 9. Copy voice

Plain, confident, specific. Short sentences. No exclamation marks, no
"revolutionary", no "AI-powered". Prefer the measured phrase: "removes the
waves in 19 of 20 seeds" over "eliminates traffic". Say what failed in the
same tone as what worked. Technical terms are used correctly and defined on
first use with a hover definition (string instability, penetration,
compliance, GEH, RMSPE, σ_v).

## 10. Deliverables

1. The full site as a runnable Next.js project with the content module,
   the hero and instrument components, and the docs pipeline.
2. A design-tokens file and a short style guide page (`/styleguide`) showing
   type scale, colours, the speed colormap, motion curves, and component
   states.
3. Storyboard frames for the intro animation before building it, so the
   sequence can be signed off.
4. A checklist confirming every number on the site against §5 and its source.

## 11. Files to attach alongside this brief

From the repository (`docs/website/`, `docs/figures/`, `artifacts/`, `docs/`):

* `docs/website/hero_data.json` — observed I-24 speed field + three ring runs
  (generated by `scripts/website_hero_data.py`; 1.1 MB)
* Figures: `i24_wb_overview.png`, `i24_validation_fields.png`,
  `i24_validation_waves.png`, `wave_speed_vs_density.png`,
  `m3_sigma_v_vs_penetration.png`, `m3_sigma_v_reduction_matrix.png`,
  `m3_spacetime_baseline_vs_fs.png`, `m3_controller_comparison.png`,
  `fd_scatter_triangle.png`, `dashboard_run_detail.png`,
  `dashboard_sweep_matrix.png`, `dashboard_scenarios.png`
* Artifacts: `m3_sweep_summary.json`, `wave_speed_sitelength.json`,
  `wave_speed_sitelength_i24.json`, `i24_validation_corrected.json`,
  `i24_validation_tracked.json`, `idm_i24.json`
* Documents: `README.md`, `CLAUDE.md`, `docs/README.md`,
  `docs/CONTROLLER_COMPARISON.md`, `docs/M3_RESULTS.md`,
  `docs/I24_DATA.md`, `docs/I24_VALIDATION.md`,
  `docs/WAVE_SPEED_DIAGNOSIS.md`, `docs/ONBOARDING_TIME.md`,
  `docs/reports/us101_replica/report.md`, `CHANGELOG.md`

## 12. Questions the designer should ask before building

Ask these back before storyboarding if they are not already answered:

1. Domain name and whether the site is hosted with the dashboard or apart.
2. Whether a contact form or a calendar link is the call to action.
3. Whether the repository stays public (affects "view source" links).
4. Whether a light theme is needed for embedding in DOT procurement decks.
