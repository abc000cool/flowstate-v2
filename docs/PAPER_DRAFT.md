# Emergent stop-and-go waves and sparse-vehicle smoothing on a calibrated I-24 replica: what a trajectory instrument can and cannot validate

> **Unsubmitted first draft, 2026-09-25 (roadmap item B1); revised
> 2026-10-07.** This draft has not been submitted or circulated. Whether and
> when it goes to arXiv (roadmap item B2) is the owner's decision. It follows
> [PAPER_OUTLINE.md](PAPER_OUTLINE.md), updated with the record since
> 2026-09-03. It adds no new claims and no simulation was run for it: every
> number carries a bracketed pointer to the committed document section or
> artifact it comes from, and Appendix A lists the headline numbers with
> their seeds and sources. Appendix C lists places where the source
> documents disagreed; items 1–18 were settled on 2026-09-25 against the
> artifacts. Of items 19–22, found during the 2026-10-07 revision, 19 and 20
> were settled in the same commit (`4d32042`) and 21 and 22 are left open.
> The 2026-10-07 revision is listed in the Revision notes below; it ran no
> simulation either.

**Authors:** Ansh Pathak, Sujan Sannidhi, Venkata Shashish Vasireddi
**Affiliation:** FlowState
**Contact:** [to be completed by the owner]
**Target:** arXiv, eess.SY with a cs.MA cross-list [PAPER_OUTLINE.md]

## Revision notes (2026-10-07)

What changed in this draft on 2026-10-07, and why, for the authors' review of
the diff. The new material comes from documents and artifacts committed on
2026-10-06 and 2026-10-07, through commit `9bf6cc6` (which landed during the
revision and adds the insertion re-analysis quoted in §4.2 and §5.7).
Uncommitted work in the tree that day (docs/I24_DISCHARGE_DIAGNOSIS.md and
its artifacts) is not used. **None of the new results is a passing
validation.** Each is labelled where it appears as a *calibration* (fitted
and scored on the same data), a *rehearsal* (the corridor-study protocol's
gate run on I-94, which fails) or a *diagnostic* (fixtures, probes and
re-readings of committed runs).

1. **Abstract, §1.4, §1.5.** Sentences added for the driver calibration, the
   I-94 protocol rehearsal and the weave's unmeasured input. §1.5 states that
   nothing added on 2026-10-07 is validation.
2. **§2.2.** The string-stability criterion applied to the calibrated
   populations: the mean driver is unstable from 28.2 veh/km at the measured
   mean acceleration (capacity density 29.2) and only from 39.8 veh/km at
   +1 sd. Why: CLAUDE.md §3.1 requires instability near capacity, and the
   calibrated drivers weaken it.
3. **§2.4, §2.5, §4.2.** New paragraphs on the corridor-study protocol (day
   split, checks C1–C6, baseline gate, change control) and its Amendments
   1–2. A note in §4.2 that the speed-only demand objective does not see
   insertion.
4. **§2.7 (new).** The merge and weave models, the three opt-in weave keys of
   2026-10-07 and lock detection. Why: results now depend on which model and
   which keys ran.
5. **§4.4, §4.6 (new).** The Amendment-1 grid: mean `a_max` + 1 sd chosen on
   both corridors, keep-right 0 kept on I-24 and 0.1 chosen on I-94; lane use
   barely responds. §4.6 also gives the fixture diagnosis that motivated it.
6. **§5.5 corrected; §5.7 (new).** §5.5 said no further car-following
   calibration would move the failing rows. The mean maximum acceleration,
   never calibrated, moves the merge discharge about a quarter of the way
   (5,850 → 6,031 veh/h over 20 seeds, against 6,630 recorded) and the GEH
   row from 21.5% to 25.0%, and loses the wave criterion; Amendment 2's
   smaller shifts keep the waves but hold 7–8% of the demand off the network.
   Not adopted on I-24. §5.7 records that the shortfall is a merge/discharge
   shortfall, not a demand limit; the source first read it as demand-limited
   and corrected itself the same day. No earlier sentence of this draft had
   called it demand-limited. §5's closing sentence no longer blames the
   lane-change model alone.
7. **§7.3 amended; §7.4–7.7 (new).** The protocol's baseline gate with the
   calibrated I-94 drivers (rehearsal: GEH < 5 on 61.8% / 60.0% of
   station-hours, 15-min RMSPE 33.9% / 38.8%; the gate fails); the
   validation-day leak and the rebuilt calibration-day inputs (not yet run);
   the lane-share measurement and map defects, with the netfix probe's
   discharge gain withdrawn (it came from one collapsed run); the residual
   decomposition; the T.H.52 weave's 465 veh/h fixture loss, its anticipation
   reach measured on I-24 (125 m, the model's 120 m) and its unmeasured
   ramp-to-ramp share, on which the flow verdict flips; W1 and W1b; locks in
   3 of 20 calibrated four-hour replicates.
8. **§7 closing sentence corrected.** It said no controller or strategy sweep
   was run on I-94. Strategy rehearsals were run on 2026-10-04; they state no
   verdict, and the gate a strategy result needs fails.
9. **§8.2 corrected.** It placed the I-24 shortfall in the merging process
   rather than car following, placed the I-94 loss in SUMO's lane-change
   model and our weave rules, and named a merge model built on the measured
   gaps as the next step. That model was built and its pre-registered
   go/no-go criteria read NO-GO (no capacity gain wherever measured; −52
   veh/h [−96, −9] on the T.H.52 section); part of the I-24 shortfall is in
   the drivers' acceleration; and the I-94 fixture loss is in no single rule
   and turns on an unmeasured crossing share. §8.1 and §8.3 updated; §8.7
   (new) on locks.
10. **§8.6 corrected.** It repeated the false premise that US freeways have no
    keep-right rule; Tennessee, Minnesota and Texas require slower traffic to
    keep right. §4.4 had been corrected on 2026-10-06; §8.6 had not. §8.6
    also lists the three corrections of 2026-10-07.
11. **§9.** Item 3 now says every I-24 calibration uses the one recorded
    morning with no holdout. Items 1 and 9 extended. New items 15–20: the
    I-94 validation-day leak, unmeasured ramp-to-ramp shares, string
    stability under the calibrated drivers, locks, the unmeasured episode-fit
    cost of the shifted acceleration, and fixture platform limits.
12. **§10, References, Appendices A and C.** Code commits and scripts for the
    new results; seven new reference entries and the HCM entry extended;
    claims-ledger rows 53–70; Appendix C items 19–22.
13. **Regression-review corrections (2026-10-07, after `4d32042`).** Each
    was checked against the committed artifact it cites. §7.5: the leak's
    size is stated as I94_CALIBRATION_DAYS.md §1.4 gives it (ramp moves up
    to 10–12%, the boundary speed up to 2.4 m/s), not "about 0.5–3%". §7.7:
    locks stood at least 13.6–52.5 minutes to the runs' end at 09:30, not
    "for hours"; one locked replicate has the second-lowest mean travel
    time, not the lowest. §5.7: the column of 9.05 / 13.65 / 6.65 counts all
    jam components of the standard detector, not backward waves. §7.6: the
    direction of the anticipation reach's bias is not known; its zone-start
    sensitivity reads longer (134 m). §2.4, §5.7, §8.3: Amendment 2's
    backlog clause was written before any result was available or read, but
    after the k = 0.25 battery had been written. Appendix C items 19 and 20
    marked resolved.

---

## Abstract

Stop-and-go waves on congested freeways come from string instability in car
following. On a ring road, one controlled vehicle can remove them. Whether
that holds on a real corridor, and at what cost, can only be judged with a
model checked against that corridor. We describe an open, seeded pipeline
that builds a microscopic replica of a freeway from OpenStreetMap, calibrates
it to trajectory and detector data, runs controller experiments with 20
common-random-number seeds per cell, and scores the replica against
FHWA-style acceptance criteria. We run it end to end on 3.4 miles of
westbound I-24 near Nashville, recorded by the I-24 MOTION camera testbed on
30 November 2022.

The central methodological finding is about the instrument. Every record in
the export is a trajectory fragment (median 117 m, 9.9 s). In the morning
peak only about half of the vehicle-time is tracked (apparent coverage
0.52–0.66). Speeds and wave speeds are sound. Counts, flows and densities are
lower bounds. Any flow-based calibration or validation from such data has to
correct for this; a model-free gap-based estimator and a simulation fit
agree on the size of the correction.

After capacity, demand and ramp calibration, the replica passes 5 of 7
criteria rows on each congested demand arm; four of the five passing rows
test the ring benchmark and the experimental design, not the fit to the
recording. It fails link-flow GEH (17–20% of
link-hours under 5, against 85% required) and segment-speed RMSPE (34–36%,
against 15%). The emergent backward wave speed passes with the criterion's
slant-stack estimator (15.7–15.9 km/h simulated, 19.9 km/h observed) and
would fail with a standard 40 km/h threshold detector (8–10 km/h). The two
failing rows trace to one on-ramp merge, which the simulator discharges at
about 5,880 veh/h where the recording sustains 6,630. The corridor is not
validated. Calibrating the drivers' mean maximum acceleration, by a rule fixed
before any run, raises that discharge to about 6,030 veh/h and no further, and
costs the emergent-wave criterion; it is not adopted.

On this unvalidated replica, FollowerStopper at its literature constants
smooths traffic and costs capacity at every penetration and compliance level
tested. At 5% penetration and full compliance, throughput falls 38% and the
temporal speed spread 59%. On a synthetic single-lane corridor the same
controller has no resolved throughput cost. We also report that detection
latency makes Jam-Absorption Driving reliable and that an explicit deferral
rule recovers the benefit with a perfect sensor; that on a five-lane replica
most of the extra fuel is burned by human drivers, who change lanes more
around the controlled vehicles, mostly by cutting in to their gap, though the
link from those changes to the fuel is only an association; and that the
discrete Delle Monache–Goatin flux cap tracks microscopic
ground truth better than a reduced-capacity variant. A second corridor, I-94
westbound in St. Paul built from public loop-detector data, was not
reproduced. In a rehearsal of a pre-registered corridor-study protocol on it,
the calibrated drivers raise the share of station-hours within GEH 5 from
15.5% to 61.8% on the calibration days, and the protocol's gate still fails.
Its weaving section's
capacity loss on a test fixture turns on an unmeasured input, the share of
entering vehicles bound for the next exit; the other candidate, how far ahead
entrants line up their merge, was measured on I-24 (125 m) and rounds to the
model's constant (120 m).

---

## 1. Introduction

### 1.1 Stop-and-go waves and sparse control

A platoon is string-unstable when a small speed disturbance grows as it
passes back through the following vehicles. The Intelligent Driver Model
(IDM) is string-unstable in a band of densities near and above capacity
(Treiber, Hennecke & Helbing 2000; Treiber & Kesting 2013). In that band a
wave forms from nearly uniform flow, with no bottleneck and no incident.
Sugiyama et al. (2008) showed this with 22 vehicles on a 230 m ring. Stern et
al. (2018) showed on a ring that one vehicle running FollowerStopper, or a PI
controller with saturation, can remove such a wave. The CIRCLES
MegaVanderTest took sparse control to 100 vehicles on I-24 in November 2022
[CLAUDE.md §13].

### 1.2 The open question

Ring results do not transfer by themselves. A ring has no inflow, no ramps
and no lanes to change into. Its vehicle count is fixed, so its throughput is
not set by demand. A freeway corridor differs in each of these ways. An
agency that wants to act on a smoothing controller or a variable speed limit
needs a model of its own corridor. The model has to be calibrated to that
corridor's data and checked against criteria a reviewer accepts. This paper
is about that check: what it takes, what passes, what fails and why.

### 1.3 Motivation from our own record

The first version of this project used a first-order
Lighthill–Whitham–Richards (LWR) model and reported dissipating phantom
jams. LWR is string-stable by construction. It cannot form the waves it was
said to dissipate. A hand-seeded jam that disappears in LWR shows the
scheme's own dissipation, not a controller's merit [LESSONS.md row 1;
CLAUDE.md ADR-1]. The second version makes a microscopic model primary and
keeps the macroscopic model as a labelled screening tier. That correction is
the first of 33 rows in a ledger of mistakes and their fixes, which we
publish with the code [LESSONS.md].

### 1.4 Contributions

1. **A reproducible corridor pipeline.** OpenStreetMap geometry, gap-based
   car-following calibration with a holdout, the FHWA calibration sequence
   (capacity, then demand, then ramps), seeded experiments with common random
   numbers, and an auto-generated criteria report. Every run records its seed
   and a hash of its full configuration (§2).
2. **A coverage measurement for camera trajectory data.** On the I-24 MOTION
   export, roughly half of the peak vehicle-time is tracked. We measure this
   two ways and show what it does to demand, to flow targets and to the
   fundamental diagram (§3.4). Thinning a complete dataset to the same
   coverage shows which lane-change measures it biases and which it leaves
   alone (§3.9). We think this is the paper's most general point.
3. **The calibration sequence on two corridors without retuning**, with its
   costs and failures (§4).
4. **A full criteria battery on the I-24 replica.** Five of seven rows pass
   on each congested arm; both rows that compare flows and speeds with the
   recording fail. The residual is located at one merge, and six rounds of
   candidate causes are excluded with an artifact each (§5). A later
   calibration of the drivers' acceleration closes about a quarter of it and
   costs the wave row (§5.7).
5. **Controller results with 95% confidence intervals**: the capacity cost of
   gap-keeping smoothing on a corridor near capacity; detection latency and
   deferred commitment in Jam-Absorption Driving; the lane-change mechanism
   behind a fuel penalty on a multi-lane site; and a comparison of two
   moving-bottleneck constraints in the macroscopic tier (§6).
6. **A second corridor that was not reproduced**, from public loop data, and
   what its weaving sections say about merge modelling in the simulator (§7):
   a rehearsal of a pre-registered study protocol on it, a decomposition of
   its residuals, the dependence of its weave on an unmeasured input, and a
   permanent-lock failure mode that a battery's means hide (§7.4–7.7).

### 1.5 What this paper does not claim

It does not claim a validated corridor. The I-24 penetration sweep is a
result about the replica, not about Nashville [I24_SWEEP.md]. No controller
was tuned for any corridor; every controller runs its published or specified
constants [CONTROLLER_COMPARISON.md; I24_SWEEP.md], and the one parameter
sweep of a controller (the headway cap of §6.7) is reported as a sweep, not
as a tuning. No experiment reported here uses a seeded perturbation: every
run is `seeded=False`, and measured boundary conditions and ramp demands are
calibration inputs, not shocks [M3_RESULTS.md §1; M3_US101_VALIDATION.md
header; I24_VALIDATION.md header]. Single-seed probes are reported as such
and never as results.

Nothing added on 2026-10-07 is validation either. The I-24 driver
calibration (§4.6, §5.7) is fitted and scored on the one recorded morning,
with no holdout [FRISCO_PROTOCOL.md, Amendment 1]. The I-94 gate results
(§7.4) are a rehearsal of the corridor-study protocol and fail it, and their
validation days are not a clean holdout (§7.5). The weave, anticipation and
lock results (§7.6–7.7) are diagnostics on fixtures, probes and re-read
replicates. Fixture results are macOS records; the corridor runs on Linux
decide [WEAVE_LOSS_DIAGNOSIS.md §1].

---

## 2. Method

### 2.1 Two tiers

The primary engine is Eclipse SUMO 1.27.1 with the IDM car-following model,
driven in-process through libsumo [CLAUDE.md ADR-1]. On the calibrated
corridors each simulated driver draws its own IDM parameters from a truncated
multivariate normal fitted to trajectory data, with hard physical floors
[M2_RESULTS.md §6]. The default step is 0.5 s [CLAUDE.md §3.2]. Fuel comes
from SUMO's HBEFA4 emission classes, which are not validated against measured
consumption here [US101_PENETRATION.md, Limitations].

The synthetic corridor `corridor_10km` runs SUMO's extended IDM (EIDM). A
single-lane open corridor with plain IDM cannot be driven into the unstable
band: single-lane insertion caps the realised inflow near 1,800 veh/h, about
25 veh/km, below the 31.8 veh/km threshold of the US-101 population
[WAVE_SPEED_DIAGNOSIS.md, test 1].

The secondary tier is a cell-transmission model (CTM) with Daganzo's
supply–demand flux, a hard CFL guard and conservation tests [CLAUDE.md §5].
Its outputs carry `tier="screening"`, and the service refuses to build a
validation report from macroscopic runs alone [CLAUDE.md §5.6]. The one
macroscopic result in this paper is the flux-cap comparison of §6.9.

### 2.2 String stability and the ring benchmark

For a car-following law `a = f(s, v, Δv)` the platoon is string-stable when
`f_v²/2 − f_v·f_Δv − f_s ≥ 0` at equilibrium [CLAUDE.md §3.1]. The criterion
is implemented in closed form for IDM. For the IDM population fitted to NGSIM
US-101 it marks 31.8–141.5 veh/km as unstable [WAVE_SPEED_DIAGNOSIS.md].

CLAUDE.md §3.1 makes instability near capacity a requirement of a calibrated
fleet, not a defect. For the capacity-scaled I-24 population (§4.1), which
both the I-24 and the I-94 fleets run, the mean driver is unstable from
28.2 veh/km, just below its capacity density of 29.2 veh/km, as required.
Raising the population's mean maximum acceleration by half and by one
standard deviation (§4.6) moves that onset to 32.5 and 39.8 veh/km: at +1 sd
the mean driver is stable at capacity density, and the share of unstable
drivers at 30 veh/km falls from 0.43 to 0.22 [DISCHARGE_CALIBRATION.md §1,
§4; population mean drivers, 5 m vehicles, `validation.string_stability`].
The heterogeneous fleet still forms waves, but the instability near capacity
is weaker, and on I-24 the wave criterion is lost (§5.7). No artifact holds
these onsets; they are recorded in the source document only.

The ring benchmark is a permanent CI test. It runs 22 vehicles on a 230 m
ring for 600 simulated seconds. Emergence requires the across-vehicle speed
spread over the last 300 s to exceed 1.5 m/s, at least one vehicle to stop,
and the jam to drift backward at −25 to −5 km/h. Dampening requires one
FollowerStopper vehicle to bring the spread below 0.75 of the baseline and to
raise the minimum speed [FLOWSTATE_DOSSIER.md §6.1]. It is also a criteria
row in every I-24 battery.

### 2.3 Controllers

Controllers are pure functions `(state, params, memory) → (v_cmd, memory)`
in SI units, shared by both tiers [CLAUDE.md §4]. Commands go through
`vehicle.setSpeed` with SUMO's safety checks on [CLAUDE.md §3.3]. *Correction
2026-09-26:* those checks do not make a controller collision-free. Under SUMO
1.27.1 a held command never brakes harder than the vehicle's comfortable
deceleration, while its own model and every human brake at up to 9 m/s², and
the I-24 strategy sweep's FollowerStopper cells record 311 collisions in 51 of
120 runs, 305 with a controlled vehicle behind [I24_STRATEGIES.md, WP-95;
artifacts/collisions_i24_strat_sweep.json]. An option that hands braking back
to the model when it needs more removes them on a fixture with the controller's
effect unchanged. Re-run with it, the strategy sweep, the US-101 penetration
sweep and the synthetic comparison lose every collision and change no metric by
a resolved amount [I24_STRATEGIES.md, VM AH]; the I-24 penetration battery's
collisions are not recorded. Compliance is drawn once per
controlled vehicle per run (Bernoulli), and a non-compliant vehicle ignores
its command [CLAUDE.md §3.3].

| Controller | Rule | Constants | Source |
|---|---|---|---|
| FollowerStopper | Command speed from the gap and the closing rate, in three regions bounded by `Δx_k = Δx_k⁰ + (Δv₋)²/(2 d_k)`; reference speed `U` = rolling platoon mean | `Δx_k⁰` = 4.5 / 5.25 / 6.0 m, `d_k` = 1.5 / 1.0 / 0.5 m/s² | Stern et al. 2018 [CLAUDE.md §4.1] |
| Capacity-aware FollowerStopper | FollowerStopper inside a headway cap `g_max = g0 + h_max·v`; beyond it, release toward the leader's speed | 4 m + 2.0 s default | this work [I24_SWEEP.md, probe section] |
| PI with saturation | `U` = the vehicle's own ≈ 38 s mean speed; target `U` plus a bounded non-negative gap term; blend with the leader's speed at short gaps (Eqs. 3–5) | `g_l` 7 m, `g_u` 30 m, `v_catch` 1 m/s, `γ` 2 m | Stern et al. 2018 [PI_CONTROLLER_FIX.md §2] |
| PI mean-fraction (superseded) | `v_target = 0.75 · v̄_platoon` | — | kept only to reproduce a failure [PI_CONTROLLER_FIX.md §1] |
| Jam-Absorption Driving (JAD) | CRUISE → SLOW_IN → HOLD → FAST_OUT; commit when a downstream wave is detected; intercept `t_int = x_w / (v_slow − w_wave)` | lookahead 2 km, wave threshold 40 km/h; optional `commit_delay_s` | He, Liu & Liu 2016 [jad_derivation.md §2; JAD_DEFERRAL_RESULTS.md] |
| Variable speed limit (VSL) | gantry segments post a speed from a ladder by downstream conditions | ladder {90 … 50} km/h; 1 km segments on I-24 | [CLAUDE.md §4.4; I24_STRATEGIES.md] |
| ALINEA ramp metering | meter each on-ramp to hold the downstream density at a target | target 29.2 veh/km/lane on I-24 | [I24_STRATEGIES.md, setup] |

JAD's downstream wave oracle is swappable. The perfect oracle reads the
simulated speed field as it is. The degraded oracle reads it `delay_s` late
and multiplies each bin speed by `1 + U(−f, +f)`, drawn from the run's
seeded generator; it degrades what the controller sees, never what the
simulator knows [JAD_ORACLE_RESULTS.md §1]. The deferral rule starts slow-in
only after a wave has been detected continuously for `commit_delay_s`; a
detection that disappears resets the clock [JAD_DEFERRAL_RESULTS.md].

### 2.4 Calibration

**Car following.** Leader–follower episodes of at least 30 s, with no lane or
leader change, are fitted one by one by seeded differential evolution on the
gap RMSE, following Kesting & Treiber (2008), with δ fixed at 4. Episodes are
split 70/30 into training and holdout. Fits in the worst decile of RMSE are
trimmed from the population statistics. The population is a truncated
multivariate normal over the per-episode fits. The holdout number is the gap
RMSE of the population-mean parameters re-simulated on the held-out episodes
[M2_RESULTS.md §3; I24_DATA.md §5]. Gap-based objectives are preferred
because they identify the parameters better than speed or acceleration
objectives [CLAUDE.md §6.2].

**Fundamental diagram.** A triangular diagram is fitted to Edie generalized
flow and density in 30 s × 50 m bins per lane: free branch by regression,
capacity from the 95th-percentile flow, congested branch by quantile
regression at τ = 0.9, with a 200-resample bootstrap [M2_RESULTS.md §4;
I24_DATA.md, fundamental-diagram section].

**The FHWA sequence.** The FHWA Traffic Analysis Toolbox Vol. III procedure is
sequential: capacity, then demand, then system performance [I24_CAPACITY.md
§3]. We apply it as follows. Step 1 scales the population's mean desired time
headway `T` until a straight road carries a field capacity, leaving the
covariance and the other means unchanged. Step 2 fits one demand level on the
first half of the study period and holds the second half out. Step 3 fits the
ramp levels, exit shares, boundary discharge and gap acceptance jointly on the
first half by compass search, again scoring the second half without fitting
it [I24_CAPACITY.md §3–7; US101_CALIBRATED.md]. No step touches a validation
criterion directly.

**Boundaries and lane changing.** Where a corridor's congestion enters from
downstream, the measured downstream speed is imposed on an exit buffer
outside the measured span [M3_US101_VALIDATION.md §2]. Two SUMO lane-change
parameters on I-24 were set from independent observables before any
criterion was evaluated (§4.4).

**Driver acceleration and keep-right (added 2026-10-06).** No step above
calibrates the population's mean maximum acceleration `a_max`. A fixture
diagnosis found that it, rather than the size of the capacity drop, sets the
level at which a merge discharges (§4.6). Amendment 1 of the corridor-study
protocol (§2.5), written before any run that uses it, lets calibration shift
the mean `a_max` inside the measured mean ± 1 sd (1.055 ± 0.43 m/s²,
`artifacts/idm_i24.json`) on the grid mean + k·sd, k ∈ {0, 0.25, 0.5, 0.75,
1}, with the covariance and the other means unchanged, and set SUMO's
`lc_keep_right` on {0, 0.1, 0.25, 0.5, 1}. The rule: among the pairs whose
lane-share RMSE is within 1 point of the grid's minimum, take the one with
the smallest discharge error; ties go to the smaller change
[FRISCO_PROTOCOL.md, Amendment 1 and its clarifications]. Amendment 2,
proposed and not adopted, constrains the shift on I-24 only: an arm qualifies
only if its 20-seed battery keeps the wave criterion and does no worse on GEH
and RMSPE than the reference arm. A clarification adds that the arm must
realise at least 0.977 of its planned demand, so that no arm can win by
holding vehicles off the network. It was written at 09:11 UTC, before any of
the amendment's results was available or read, but after the k = 0.25
battery had been written on the cloud machine at 09:05 UTC; it decides the
outcome for k = 0.5, which meets the original rule without it
[FRISCO_PROTOCOL.md, Amendment 2 and its clarification, as corrected;
artifacts/i24_validation_dck025_refit.json, `created_at`].

### 2.5 Validation criteria and statistics

The criteria are data, not prose, and each profile carries its source
[CLAUDE.md §7.1; I24_VALIDATION.md §0].

| Row | Criterion | Source of the threshold |
|---|---|---|
| Link flows | GEH < 5 on ≥ 85% of link-hour comparisons, GEH = √(2(m−c)²/(m+c)) | FHWA Vol. III 2004 (FHWA-HRT-04-040) §5.6; the 2019 update (FHWA-HOP-18-036) states no GEH target [CLAUDE.md §7.1] |
| Segment speeds | RMSPE ≤ 15% on segment mean speeds | common microsimulation practice [CLAUDE.md §7.1] |
| Emergent wave speed | backward front speed in 14–22 km/h, no seeding | empirical stop-and-go literature [CLAUDE.md §7.1] |
| Ring emergence, ring dampening | reproduced on every seed | Sugiyama et al. 2008; Stern et al. 2018 |
| Replicates | ≥ 20 seeds | internal standard |
| Sensitivity | penetration {1, 2, 5, 10, 15, 20}% × compliance {25, 50, 80, 100}% published with CIs | internal standard |

A wave-speed number is only meaningful with its detector, so every detector
recipe is registered and named in the criteria row [CONTRACTS.md §4]:

| Detector | Bins | Rule | Statistic |
|---|---|---|---|
| `standard` | 15 s × 75 m | jam = speed < 40 km/h, connected components | mean backward Theil–Sen front speed |
| `stripe` | 10 s × 50 m | jam = speed < 25 km/h | same |
| `relative` | 15 s × 75 m | jam = speed < 0.5 × p90 of the field | same |
| `stack` | 15 s × 75 m | no threshold: slant-stack of the demeaned field over −40…−2 km/h; peak/median contrast ≥ 3 | peak speed |

On synthetic fields with planted backward stripes inside a standing queue,
`stack` is the only recipe that recovers the planted speed at every
congested fraction from 0.3 to 0.95; `standard` finds no backward front on a
congested background at any of them [CONTRACTS.md §4]. The default criteria
profile therefore names `stack`. That choice was made on the synthetic
benchmark, after the first I-24 battery had been scored with `standard`
(§5.4).

Every stochastic result uses 20 seeds from `spawn_seeds(42, 20)`. Every cell
of an experiment reuses the same seed list (common random numbers), so a cell
and its baseline can be differenced seed by seed. We report means with
t-distribution 95% intervals and paired differences with their own
intervals. An effect is "resolved" when its paired interval excludes zero
[M3_RESULTS.md §1]. With 24 cells tested at the 95% level, about one cell is
expected to resolve by chance; we say so where it matters [M3_RESULTS.md
§4.4].

**The corridor-study protocol (2026-10-04).** Later corridor work runs under
a protocol committed before any client data. It fixes the detectors, a
seeded, volume-stratified split of the candidate days into calibration and
validation days, the checks, and what calibration may change
[FRISCO_PROTOCOL.md, header, §3, §7]. The checks are:

- C1, GEH < 5 on at least 85% of station-hours;
- C2, the Texas rule of GEH < 3 on every station-hour, reported only;
- C3, station-speed RMSPE ≤ 15% at 15-minute aggregation, fixed in advance
  because 5-minute speeds carry their own noise floor (§5.5);
- C4, the `stack` detector's backward wave speed in 14–22 km/h, with a front
  in at least 80% of replicates;
- C5, zero collisions;
- C6, every observed active bottleneck reproduced in location, timing and
  queue reach, with no phantom bottleneck in more than half of the
  replicates (after Chen, Skabardonis & Varaiya 2004)
  [FRISCO_PROTOCOL.md §4–5].

The baseline gate passes only if C1, C3, C5 and C6 pass on the calibration
days and C1, C3 and C6 on the validation days, with C4 passing or not
applicable. A model that fails it may not be used for strategy
recommendations [FRISCO_PROTOCOL.md §6]. A change made after data arrive is
a dated amendment, and results are then reported under both rules
[FRISCO_PROTOCOL.md, change control]. The I-94 runs of §7.4 are a rehearsal
of this protocol on public data, not a client study.

### 2.6 Reproducibility as method

Every run writes a `meta.json` with its seed, package versions, calibration
provenance and a configuration hash; the hash policy excludes defaults, so
schema growth does not move existing hashes [CHANGELOG, 2026-09-06
"Config-hash policy v2"]. CI compares summary statistics of fixed-seed runs
with stored goldens [CLAUDE.md §9]. The criteria report is generated from the
run set and lists every seed, hash and version [M3_US101_VALIDATION.md §7;
docs/reports/].

Two metric defects were found by audit and fixed before this draft. The
warm-up period was documented as discarded and never was, which biased
throughput and travel time by 2–13% [CHANGELOG 2.2.0]. The travel-time span
ended at the farthest point any vehicle reached, so exactly one vehicle
completed it [LESSONS.md rows 20–21]. The I-24 batteries, the US-101 arms,
the 500-run I-24 sweep and the headway-cap sweep were re-simulated with the
same seeds under the corrected definitions. Every I-24 criteria row
reproduced to the digit [I24_VALIDATION.md §0.1]; on US-101 the speed and
wave rows, re-scored on the windowed field, moved by under a point with
unchanged verdicts [M3_US101_VALIDATION.md, re-run of 2026-09-17]. The
synthetic-corridor
experiments (§6.1–6.4) predate the fix. Their metrics include a 120 s
warm-up that is identical across cells under common random numbers, so
paired contrasts are unaffected and absolute levels include the transient
[M3_RESULTS.md §5 item 5].

### 2.7 Merge and weave models, and lock detection

Where traffic joins or crosses, four models are in use, and every result
names the one that ran.

| Model | What it does | Where it runs | Source |
|---|---|---|---|
| `lane_change` | SUMO's own lane-change model at an acceleration lane | the I-24 arms | [I24_VALIDATION.md §1] |
| Scripted merge | a runner-driven late merge for ramp vehicles; its forced change is allowed only when the gap exceeds the follower's braking distance (§7.2) | two I-94 on-ramps | [ONBOARDING_MNDOT.md §11, VM AG] |
| `weave` | our rules for a weaving section (below) | the I-94 T.H.52 and Ruth St weaves | [MERGE_ANTICIPATION.md §1; WEAVE_LOSS_DIAGNOSIS.md §3.11] |
| `measured` | built on 2026-10-05 from three measured principles together: gap acceptance from the I-24 critical gaps, a speed ceiling toward the target lane's speed, and a temporary headway after the crossing; opt-in and not adopted (§8.2) | fixtures and gates only | [MERGE_MODEL.md §1; MERGE_MODEL_READINESS.md §2–3] |

In the weave, an entrant starts choosing a gap in the target lane
`lookahead_m` (120 m, an engineering constant) before the section; the chosen
gap's follower is slowed to open it, and the entrant eases toward the gap's
leader [MERGE_ANTICIPATION.md §1]. An exiter halted within 5 m of the exit
gore gives up its exit [I94_COLLAPSE_DIAGNOSIS.md §0]. Who crosses is set by
drawing every vehicle's exit with the same probability, whether it came from
the mainline or the ramp: the Highway Capacity Manual's proportional split,
an assumption where no origin–destination count exists
[WEAVE_LOSS_DIAGNOSIS.md §3.11; TH52_CROSSING_SHARE.md §0].

Three opt-in weave settings were added on 2026-10-07. Each is off by default,
and with it off 37 fixture and golden runs are byte-identical to the code
before it; no committed scenario sets any of them.

- `entrant_giveup_m` (amendment W1): an entrant halted at the end of the
  auxiliary lane, still owing its change and with no change to request,
  takes the paired exit [WEAVE_LOSS_DIAGNOSIS.md §6.2, §8.1–8.2].
- `entrant_giveup_dwell_s` (amendment W1b): the same, but only after an
  unbroken stand of that length, registered at 60 s
  [WEAVE_LOSS_DIAGNOSIS.md §10.2–10.3, §10.8].
- `WeaveSpec.ramp_to_ramp_share`: the share of a weave's entrants that take the paired
  exit, set by swapping destinations within each 300-s window, so that every
  leg's and every exit's volume is unchanged [TH52_CROSSING_SHARE.md
  §10.1–10.2].

**Lock detection (2026-10-07).** A run locks when vehicles stand with zero
discharge past a point for at least 10 minutes while a queue builds
upstream. Two readers find it in files every replicate already writes. A
space-time reader takes the 15 s × 100 m Edie bins: a cell stands at
≥ 20 veh/km and ≤ 18 veh/h, and ten minutes standing is a lock. A run-end
reader takes each vehicle's last position: a front vehicle with an empty road
ahead and a queue behind. Each lock records its onset, duration, head
position and the weave, merge or diverge it stands at, and every run set now
carries a `no_locks` criteria row (PASS / FAIL / NOT RECORDED)
[I94_COLLAPSE_DIAGNOSIS.md §10; CHANGELOG, 2026-10-07]. The detector only
reports: no runner, configuration hash or golden changed. Run sets scored
before it carry no lock record (§7.7, §9 item 18).

---

## 3. Data: I-24 MOTION, and what it can support

### 3.1 The instrument and the export

I-24 MOTION is a camera testbed on four miles of I-24 near Nashville
(Gloudemans et al. 2023). We use its INCEPTION v1.x export of 30 November
2022, 06:00–10:00 CST, mile markers 58.7–62.7 [I24_DATA.md header]. The
export is one 5.8 GB zip holding a single 19.5 GB JSON array of 816,694
documents. It is never extracted. A streaming reader decodes one document at
a time, keeps one carriageway and writes 5 Hz Parquet. The westbound
direction, the morning-peak direction, gives 576,511 documents and
42,764,894 rows at 5 Hz in 309 s [I24_DATA.md §1].

The loader had been written against the documented schema before the file
existed. The real file differed: object ids are exported as `{"$oid": …}`,
and positions are in feet at the back centre of the vehicle, not the front
bumper. Positions were converted to the front bumper so that bumper-to-bumper
gaps come out right [I24_DATA.md §1; LESSONS.md row 6].

### 3.2 Every record is a fragment

| Quantity | Value |
|---|---|
| Fragment duration, median / p90 / max | 9.9 s / 31.4 s / 1,270 s |
| Fragment span along the road, median / p90 | 117 m / 450 m |
| Fragments lasting ≥ 30 s | 62,784 (10.9%) |

*Source: [I24_DATA.md §2].* A median fragment covers about one camera field
of view. The documentation calls them fragments, and the tools paper states
that v1 trajectories are not yet suitable for long-term vehicle-following
analyses (Ji et al. 2024) [I24_DATA.md §2]. We stitch nothing and use the
fragments as delivered.

### 3.3 The day

| Window (CST) | Fragments | Mean bin speed [km/h] | Bins < 40 km/h |
|---|---|---|---|
| 06:00–06:15 | 10,176 | 120.1 | 0.6% |
| 06:30–06:45 | 43,709 | 47.1 | 38.1% |
| 07:00–07:15 | 43,820 | 45.0 | 42.4% |
| 07:30–07:45 | 47,497 | 31.6 | 71.3% |
| 07:45–08:00 | 46,385 | 29.1 | 77.5% |
| 08:15–08:30 | 37,387 | 43.7 | 43.2% |
| 09:00–09:15 | 32,025 | 60.1 | 22.8% |
| 09:45–10:00 | 18,088 | 112.1 | 0.0% |

*Source: [I24_DATA.md §3; artifacts/i24_wb_overview.json]; 60 s × 100 m
bins.* Congestion enters from the downstream (Bell Road) end at about 06:20.
Stop-and-go stripes then propagate upstream across the whole instrument until
about 09:30, at a visually consistent backward slope (Figure 1). The replica's
study period is 06:30–08:30 CST, after a 600 s warm-up [I24_DATA.md §3].

### 3.4 Tracking coverage: the limitation that governs everything flow-based

This is the paper's central methodological point.

**The symptom.** On a ramp-free stretch the true count is the same at every
cross-section, up to travel-time lag. The tracked crossing counts are not.
In the peak hour some sections see about 90 veh/h and others 1,400, against
3,500–4,300 at their neighbours, because fragments break at camera
boundaries and under overpasses. Edie flow over 500 m cells, which does not
care where fragments break, puts the tracked mainline flow at about
3,300 veh/h over most of the corridor, or 830 veh/h per lane [I24_DATA.md
§4]. That is too low for stop-and-go traffic at 25–35 km/h.

**The measurement.** Edie speed is a ratio of vehicle-distance to
vehicle-time, so it is robust to missing vehicles. Density is not. We compare
the tracked Edie density per lane with the density the calibrated
car-following population holds at the observed Edie speed,
`ρ_eq = 1/(s_eq(v) + L)` with `s_eq = (s0 + vT)/√(1 − (v/v0)⁴)` and
`L = 5 m`. The ratio is the share of vehicle-time that was tracked.

| Window (CST) | Tracked ρ [veh/km/lane] | Edie v [km/h] | ρ_eq(v) [veh/km/lane] | Apparent coverage |
|---|---|---|---|---|
| 06:30–06:45 | 26.7 | 40.9 | 40.3 | 0.66 |
| 06:45–07:00 | 28.1 | 33.5 | 46.2 | 0.61 |
| 07:00–07:15 | 26.7 | 36.8 | 43.3 | 0.62 |
| 07:15–07:30 | 28.3 | 33.5 | 46.2 | 0.61 |
| 07:30–07:45 | 29.6 | 25.8 | 54.4 | 0.54 |
| 07:45–08:00 | 29.9 | 24.1 | 56.6 | 0.53 |
| 08:00–08:15 | 28.2 | 25.9 | 54.3 | 0.52 |
| 08:15–08:30 | 23.8 | 34.8 | 45.1 | 0.53 |

*Source: [I24_DATA.md §4; `equilibrium_legacy_fleet` in
artifacts/i24_coverage_lane5.json]. The table uses the population fitted
first; `coverage` in artifacts/i24_replica_inputs.json now carries the
capacity-calibrated population's values, 0.481–0.605 (§4.1).*
The I-24 MOTION paper reports a position recall of 0.95 on its labelled
validation clips [I24_DATA.md §4]. On this day's post-processed export, in
the peak, about half of the vehicle-time is tracked. Occlusion by tall
vehicles in interior lanes is the documented mechanism.

**A second, model-free estimate.** The check above leans on the calibrated
spacing. A gap-based estimator does not. If each vehicle is tracked with
probability `c`, the spacing between consecutive tracked vehicles in a lane
is a geometric sum of true spacings. A geometric-gamma mixture fitted by
maximum likelihood per speed class recovers `c`; on synthetic lanes it
recovers a known `c` within 0.01 for a homogeneous spacing scale, and is
biased low by up to 0.05 under correlated losses [I24_DATA.md, "Tracking
coverage revisited"].

| CST | Equilibrium method (above) | Gap mixture | Section gap mixture (recommended) |
|---|---|---|---|
| 06:30 | 0.605 | 0.638 | 0.656 |
| 07:00 | 0.565 | 0.615 | 0.648 |
| 07:30 | 0.504 | 0.578 | 0.627 |
| 08:00 | 0.481 | 0.548 | 0.559 |
| 08:15 | 0.484 | 0.552 | 0.584 |

*Source: [I24_DATA.md, "Tracking coverage revisited";
artifacts/i24_coverage.json]; the equilibrium column here uses the
capacity-calibrated population of §4.1, hence its values differ from the
table above.* Lane 1 tracks at 0.70–0.76 and interior lane 3 at 0.40–0.54.
The recommended coverage is 8–25% above the equilibrium value. With it, the
corrected mainline inflow falls from a 1,935 to a 1,786 veh/h/lane peak, and
from 1,656 to 1,418 on the study-period mean. That is the size of the
overshoot that a demand-level fit on speeds found independently in §4.2
(level 0.85 ≈ 1/1.17): a data-only estimator and a simulation fit agree
[I24_DATA.md, "Tracking coverage revisited"; I24_CAPACITY.md §5].

**Consequences, carried through the rest of the paper.**

- Speeds, wave speeds and wave counts are trustworthy.
- Counts, flows and densities are lower bounds at the local coverage. The
  branch slopes of the fundamental diagram, `v_f` and `w`, are invariant to a
  uniform coverage factor; capacity and jam density are not.
- Lane-change measures split the same way. Thinned to this coverage, a
  complete dataset's gaps and refusal shares move, and only some of them
  are bounds; its relative speeds at a crossing do not move (§3.9).
- Demand is ambiguous, so the replica is run in labelled demand arms: as
  tracked, and divided by the coverage (§4.2).
- The link-flow criterion needs an observed side, and the tracked counts are
  not one. We score it against the tracked crossings divided by the
  recommended estimator, which needs no car-following model, and we keep the
  tracked and apparent-coverage tables beside it as bounds [I24_VALIDATION.md
  §0.5(b)].
- The decisive fix is external: the testbed's radar detectors give 30 s
  volumes. If those counts exist for this day, they replace both the tracked
  demand and the observed side of GEH [I24_DATA.md §4; ROADMAP.md §6 item 7].

**A general caution.** Camera-derived trajectory datasets are attractive for
calibration because they give every vehicle's path. This one shows that
"every vehicle" can mean half of them in the peak, unevenly by lane and by
cross-section, with no flag in the data. Speed-based quantities survive
that. Count-based ones do not, and a model calibrated to raw counts from such
data will be starved of demand: our tracked-demand arm free-flows at
76–79 km/h where the road ran at 30–38 km/h (§5.3). We would check the
coverage of any trajectory dataset against an independent density or count
before using its flows.

### 3.5 Car-following episodes and the population fit

Each mainline vehicle is paired with the nearest tracked vehicle ahead in its
lane at the same 0.2 s slot, because the schema publishes no leader ids.
Episodes of at least 30 s are cut at fragment ends, lane changes and leader
changes. A gap above 100 m (probably an untracked leader) or below 0.5 m
(probably a duplicate fragment) cuts the episode instead of poisoning it
[I24_DATA.md §5].

| Quantity | I-24 (this work) | NGSIM US-101 |
|---|---|---|
| Episodes | 17,652 (16,857 followers) | 2,452 |
| Total car-following time | 216.9 h | 36.7 h |
| Train / holdout | 12,356 / 5,296 | 1,716 / 736 |
| v0 [m/s], mean (sd) | 32.40 (5.50) | 32.12 (6.24) |
| T [s] | 1.51 (0.52) | 1.29 (0.45) |
| a_max [m/s²] | 1.06 (0.43) | 1.11 (0.40) |
| b [m/s²] | 1.70 (0.89) | 1.69 (0.87) |
| s0 [m] | 2.53 (0.74) | 2.02 (0.91) |
| **Holdout gap RMSE** | **5.29 m** | **6.44 m** |

*Source: [I24_DATA.md §5; artifacts/idm_i24.json; M2_RESULTS.md §2–3;
artifacts/idm_us101.json].* Nashville drivers keep a longer headway and a
larger standstill gap than the Los Angeles population. `a_max` is high on
both, against a literature default of 0.73 m/s². We had attributed the NGSIM
value to differentiation noise in raw positions; the smoothed I-24 positions
give the same value, so that explanation was incomplete [LESSONS.md row 9].
`v0` is weakly identified on both datasets because most samples are
congested (83% below 40 km/h on I-24) [I24_DATA.md §5].

Heavy vehicles are fitted separately: 9.2% of mainline fragments in the study
period are semis or trucks, holding 5.5% of the vehicle-time. 197 episodes
with a heavy follower give a holdout gap RMSE of 7.09 m, a mean `T` of
1.96 s and a mean `a_max` of 0.67 m/s² [I24_DATA.md, heavy-vehicle section;
artifacts/idm_i24_heavy.json]. The sample is small.

### 3.6 The fundamental diagram, and three wave-speed estimates that agree

| Quantity | Value | 95% CI | Status |
|---|---|---|---|
| Free-flow branch slope v_f | 67.2 km/h | 67.0–67.4 | slope; R² 0.33; not a free speed |
| Congested wave speed w | 16.1 km/h | 15.7–16.5 | slope, coverage-invariant |
| Critical density ρ_c | 26.5 veh/km/lane | 26.4–26.6 | lower bound |
| Capacity q_max | 1,780 veh/h/lane | 1,775–1,787 | lower bound |
| Jam density ρ_jam | 137 veh/km/lane | 135–140 | lower bound |

*Source: [I24_DATA.md, fundamental-diagram section; artifacts/fd_i24.json];
236,717 bins of 30 s × 50 m per lane, 200 bootstrap resamples.* The
congested wave speed is the one number here that the coverage does not
touch. Three independent estimates of it agree: `w` = 16.1 km/h from the
fitted diagram; the observed front speeds of the same day, 14.2 km/h with
the standard detector and 16.4 km/h with the relative one
[I24_VALIDATION.md §4]; and Newell's `(s0 + L)/T` for the fitted population,
16.8–17.9 km/h [I24_DATA.md §5]. On the ring, the same fleet's emergent waves
run at 16.4 km/h at 80 veh/km (§5.1). The slant-stack estimator reads the
observed field higher, at 19.9 km/h, the top of the band (§5.4); the
estimates agree within the band, not to a decimal. Capacity and jam density are lower
bounds and must not be quoted as facility values [I24_DATA.md,
fundamental-diagram section].

### 3.7 Corridor geometry

The network is compiled with `netconvert` from an OpenStreetMap extract of
the testbed (106 motorway and motorway-link ways). The provider's landmark
layers (WGS84) are placed on the compiled network by a re-implemented UTM
projection, with a worst mismatch of 8 mm against OSM junction nodes. A
linear fit of chain position on the 26 mile-marker signs gives 1,577 chain
metres per data mile (residual RMS 58 m) and places the two on-ramp gores
within about 10 m of where ramp-lane fragments appear in the data
[I24_DATA.md §6].

The replica covers 3.4 of the 4 miles, from MM 62.7 to the Bell Road
collector road: a 2.2 km insertion buffer, the measured span (data
x ∈ [0, 5,492) m), and a 992 m exit edge carrying the observed downstream
speed schedule at 30 s resolution. It models the Old Hickory and Hickory
Hollow on-ramps and the Hickory Hollow and Bell Road off-ramps with
data-derived inflows and exit fractions [I24_VALIDATION.md §1]. Two map
defects were found later against the landmark layer: the Old Hickory
acceleration lane ends 244 m too early in OSM and the Hickory Hollow
deceleration pocket starts 345 m too late. A corrected extract moves two way
boundaries and nothing else [I24_VALIDATION.md §0.5(d)]. Correcting them did
not move the failing rows (§5.5).

### 3.8 The second dataset: NGSIM US-101

The raw NGSIM US-101 export covers about 640 m of southbound US-101 in Los
Angeles, five mainline lanes plus an auxiliary lane, on 15 June 2005
[M2_RESULTS.md §1]. 21.5% of its 2.4 million rows are exact duplicates and
were dropped. The dump holds recording period 1 completely and the first
8.8 minutes of period 2 [M2_RESULTS.md §1]. The site is congested
throughout. Its fitted diagram gives `w` = −14.6 km/h (95% CI −19.0 to
−10.6) and `q_max` = 2,097 veh/h/lane (2,068–2,130); its `v_f` of 57 km/h is
the fastest observed operation, not a free-flow speed [M2_RESULTS.md §4]
(Figure 2). The reconstructed (Montanino–Punzo) version of these data was not
used; its method is published and could be applied to the raw data
[ROADMAP.md §6].

### 3.9 Coverage and lane-change measures

The weaving-section work of §7.3 reads lane changes from the same data. An
entering crossing is a change from an auxiliary lane into the mainline
inside a weaving section. The leader side is the changer behind its new
leader; the follower side is the new follower behind the changer. Coverage
acts on these measures too, but not in one direction for all of them.

**What partial tracking bounds.** A missed vehicle can only lengthen a
recorded space gap. It can also change which vehicle is recorded as the
neighbour. Behind the changer, that can be a different vehicle at a
different speed. A quantity is a bound only if it reads no neighbour's
speed.

| Quantity at the change | Recorded against true | Bound? |
|---|---|---|
| Space gap, either side | the same or longer | upper |
| Lead time gap (gap ahead over the changer's own speed) | the same or longer | upper |
| Leader side's gap over s0 + vT at the population means (`ratio_eq`) | the same or higher | upper |
| Share refused by the acceptance's lead-time term alone | the same or lower | lower |
| Lag time gap (gap behind over the recorded follower's speed); follower side's `ratio_eq` | expected longer (higher) | no |
| Share refused by the whole acceptance, and by its other terms | expected lower | no |
| Fitted critical gaps | expected higher | no |
| Gap over the population's normal time gap at that speed (`ratio_pop`) | no expected direction | no: the normal is measured on the same tracked pairs |
| Partner speed (the front vehicle's speed minus the rear one's) | no expected direction | no |

*Source: [WEAVE_MODEL_PLAN.md, corrections from the review of 2026-09-25,
table (a); WP-91, expectations table].* The acceptance is the weave model's
own gap-acceptance rule, applied to each recorded crossing at the I-24
population's means and a 0.6 s accepted time gap
[artifacts/i24_lane_change_gaps.json, `acceptance`].

**A test on complete data.** NGSIM US-101 records every vehicle on its site
[WEAVE_MODEL_PLAN.md, WP-91]. We thinned it to I-24-like coverage and
measured every quantity again [WEAVE_MODEL_PLAN.md, WP-91 and VM AE;
artifacts/coverage_thinning_us101.json]. The fragment model cuts each track
into tracked spells and gives each spell a new tracker id. The spells are
log-normal with median 9.9 s and σ 0.900, set from I-24's committed median
and share of fragments lasting 30 s or more (§3.2). Untracked spells
between them are sized so that a fraction F of the vehicle-time is kept.
F = 0.65 and F = 0.5 bracket I-24's peak coverage (§3.4); the kept
vehicle-time achieved was 0.64–0.65 and 0.49–0.50. Each condition was
drawn with five thinning seeds. Two controls separate the causes. F = 1.0
keeps all vehicle-time and only cuts and renames tracks. A vehicle model
keeps whole tracks of a fraction F of the vehicles, so only the recorded
neighbour changes.

**The rule was written first.** The reading rule was committed with the
code (commit `2946afc`) before the stage ran [WEAVE_MODEL_PLAN.md, WP-91,
"How the thinning will be read"]. The shift is the mean over five seeds
minus the unthinned reference, with a 95% t-interval over the seeds. The
tolerances are 0.05 on ratios, 5 points on refusal shares, 0.10 s on time
gaps and critical gaps, 1.0 m on space gaps and 0.5 m/s on speeds. The
fragment model decides, at both F = 0.65 and F = 0.5:

- *robust*: the interval lies inside ± the tolerance at both;
- *biased as expected*: the expected sign, the interval clear of zero, at
  both;
- *against expectation*: the opposite sign, the interval clear of zero, at
  either;
- *undetermined*: anything else.

Two checks came before any row was read, and both pass. The unthinned
reference reproduces the committed US-101 medians at the change to the
digit. No bounded quantity moves the wrong way under either model
[artifacts/coverage_thinning_us101.json, `reference_check_stage16`;
WEAVE_MODEL_PLAN.md, VM AE (1)].

| Quantity (weave, entering, at the change) | Reference, US-101 complete | Shift, F = 0.65 | Shift, F = 0.5 | Verdict | I-24 value |
|---|---|---|---|---|---|
| Lag time gap, median [s] | 1.21 | +0.78 [+0.33, +1.23] | +1.53 [+1.07, +1.99] | biased as expected | 2.46 |
| Lead time gap, median [s] | 0.91 | +0.60 [+0.32, +0.89] | +1.32 [+1.14, +1.50] | biased as expected (a bound) | 1.69 |
| Acceptance refusal share [%] | 71.7 | −18.5 [−25.3, −11.7] | −29.3 [−32.4, −26.1] | biased as expected | 48.5 |
| Refused by the lead-time term [%] | 58.3 | −21.1 [−28.5, −13.6] | −29.9 [−35.2, −24.6] | biased as expected (a bound) | 31.5 |
| Critical gap ahead, joint fit [s] | 0.29 | +0.10 [−0.04, +0.24] | +0.10 [−0.08, +0.29] | undetermined | 0.46 |
| Critical gap behind, joint fit [s] | 0.45 | +0.17 [−0.06, +0.40] | +0.39 [+0.14, +0.63] | undetermined | 0.92 |
| Follower side `ratio_eq` | 0.81 | +0.37 [+0.17, +0.58] | +0.62 [+0.31, +0.94] | biased as expected | 1.14 |
| Leader side `ratio_eq` | 0.62 | +0.31 [+0.10, +0.53] | +0.63 [+0.36, +0.90] | biased as expected (a bound) | 0.92 |
| Follower side `ratio_pop` | 0.76 | +0.14 [−0.01, +0.29] | +0.20 [−0.01, +0.40] | undetermined | 0.87 |
| Leader side `ratio_pop` | 0.57 | +0.14 [−0.02, +0.30] | +0.28 [+0.10, +0.46] | shifted (no expected direction) | 0.72 |
| Partner speed, entrant minus new follower [m/s] | +1.01 | +0.05 [−0.16, +0.25] | +0.11 [−0.25, +0.47] | robust | +1.18 |
| Partner speed, new leader minus entrant [m/s] | −0.40 | −0.04 [−0.19, +0.11] | +0.04 [−0.09, +0.16] | robust | −0.83 |

*Source: [artifacts/coverage_thinning_us101.json; WEAVE_MODEL_PLAN.md, VM AE
(2)]. Fragment model; 180 entering crossings in the reference, 80–103 after
thinning (seed means); medians; shifts with 95% t-intervals over five
thinning seeds. I-24 column: [artifacts/i24_lane_change_gaps.json (time
gaps, refusals); artifacts/i24_critical_gaps.json;
artifacts/lane_change_relaxation_i24.json (ratios, partner speeds)].*

- Every time-gap median, `ratio_eq` and refusal share in the table moves by
  far more than its tolerance, in the expected direction, at both coverage
  levels. Complete data show shorter gaps and more refusals than thinned
  data.
- Fragmentation alone (F = 1.0) moves none of these rows by more than its
  tolerance. The vehicle model moves the gap, refusal and `ratio_eq` rows
  the same way and by about as much (the refusal share by −18.5 and −24.7
  points). The shifts come from the recorded neighbour being another
  vehicle, not from cut tracks.
- The fitted critical gaps are undetermined. Their point shifts are
  positive, but three of the four intervals include zero. The follower
  side's `ratio_pop` is undetermined too: its point shifts are large, but
  both intervals reach just below zero.
- The partner speeds are robust.
- Fragment thinning also cuts the curves after the crossing short: the
  follower sides still read 10 s after the change fall from 85 to 9.4 and
  6.8 (seed means). I-24 reads 89 of its 1,488 follower sides at 10 s. Its
  short curves
  are consistent with fragment-level loss, not a property of its drivers
  [WEAVE_MODEL_PLAN.md, VM AE (2)].

**What this means for the I-24 numbers.** These are transfers from another
site, not corrections [WEAVE_MODEL_PLAN.md, VM AE (3)].

- I-24's gap and refusal quantities are one-sided, with US-101's shifts as
  the scale. The weave acceptance refuses 48.5% of I-24's recorded entering
  crossings; on US-101 the same thinning lowers the refusal share by
  18.5–29.3 points. I-24's new follower reads 1.14 of s0 + vT; on US-101 the
  thinning raises that ratio by 0.37–0.62.
- The bounds stay bounds. The leader side's 0.92 is an upper bound, loose by
  0.31–0.63 on US-101. The lead-time term's 31.5% is a lower bound, loose by
  21–30 points.
- The critical gaps keep their expected upward direction, which this test
  neither confirms nor rules out (§7.3).
- The partner speeds are usable as values.

**Limitations of the test.** One complete dataset: the raw NGSIM US-101
export, 640 m, congested throughout, 180 entering crossings. The intervals
are over thinning seeds on one sample. They say whether a shift stands out
from the thinning's own noise, not how US-101 would vary on another day. The
shape of the untracked spells is assumed; I-24 publishes no statistic of
it. I-24's losses are correlated by camera and by lane (§3.4), and it has
duplicate fragments and position noise; neither thinning model has these.
The fragments achieved have a median of about 8 s against the 9.9 s drawn,
a difference not explained. On exiting crossings (72 in the reference) only
the refusal share resolves, and the exiting critical gaps cannot be fitted
at F = 0.5. The transferred shifts carry US-101's traffic, not I-24's
[artifacts/coverage_thinning_us101.json, `limitations`; WEAVE_MODEL_PLAN.md,
VM AE].

---

## 4. Calibration

### 4.1 Step 1: capacity

Before calibration, the fitted I-24 population cannot carry the demand the
recording implies. On a straight four-lane road with no ramps, driven at
fixed per-lane inflows (three seeds per level, 30 simulated minutes), it
saturates at about 1,650 veh/h per lane:

| Demand [veh/h/lane] | 1,400 | 1,600 | 1,800 | 2,000 | 2,200 | 2,400 |
|---|---|---|---|---|---|---|
| Throughput at 3 km [veh/h/lane] | 1,397 | 1,588 | 1,647 | 1,643 | 1,656 | 1,646 |
| Inserted | 1.000 | 0.999 | 0.922 | 0.831 | 0.759 | 0.691 |

*Source: [I24_CAPACITY.md §1; artifacts/i24_capacity_experiment.json].*
That is below the 1,775 veh/h per lane the instrument tracked, which is
itself a lower bound. Spreading the replica's insertion over three edges
instead of one frees the buffer and changes nothing downstream, so the
limit is capacity, not insertion [I24_CAPACITY.md §2]. The episode fit
identifies each driver's headway in following; it says little about the
population's capacity, which is the known IDM trade-off between congested
headways and free-flow capacity [I24_CAPACITY.md §3].

Scaling the population's mean `T` by a factor `f` gives:

| T scale | 1.00 | 0.95 | 0.90 | 0.85 | 0.80 | 0.75 |
|---|---|---|---|---|---|---|
| Mean T [s] | 1.511 | 1.436 | 1.360 | 1.285 | 1.209 | 1.133 |
| Capacity [veh/h/lane] | 1,631 | 1,690 | 1,754 | 1,796 | 1,833 | 1,948 |

*Source: [I24_CAPACITY.md §4; artifacts/idm_i24_capacity.calibration.json];
two seeds per point, saturating demand.* Interpolating to the tracked
capacity of 1,775 veh/h/lane gives `f*` = 0.875 and a mean `T` of 1.322 s
[artifacts/idm_i24_capacity.json]. The cost in following behaviour is nil:
the population-mean gap RMSE over 1,500 seeded episodes is 5.313 m before and
5.294 m after [I24_CAPACITY.md §4]. The target is a lower bound, so the
scaling is conservative.

### 4.2 Step 2: the demand level

The coverage-corrected demand profile is scaled by one level `s`, fitted on
segment speeds over 06:30–07:30 with 07:30–08:30 held out, one seed per
point:

| Level s | 0.60 | 0.70 | 0.80 | 0.85 | 0.90 | 1.00 | 1.10 |
|---|---|---|---|---|---|---|---|
| Inserted | 1.000 | 1.000 | 0.994 | 0.945 | 0.895 | 0.807 | 0.734 |
| RMSPE, fitted hour | 1.156 | 0.742 | 0.401 | **0.320** | 0.337 | 0.361 | 0.373 |
| RMSPE, held-out hour | 2.193 | 1.718 | 0.460 | **0.396** | 0.382 | 0.378 | 0.389 |

*Source: [I24_CAPACITY.md §5; artifacts/demand_scale_i24_corrected.json].*
The level is well determined at the low end and flat above it. `s` = 0.85 is
the fit; this is the `speedcal` arm. A single level on the tracked profile
is worse on both hours, the signature of a coverage that varies in time
[I24_CAPACITY.md §5].

*Note 2026-10-07.* The fitter's objective is segment speed, and it does not
see insertion. Re-run under stronger drivers, it chose levels whose 20-seed
batteries realised only 0.918–0.930 of the planned demand (§5.7)
[DISCHARGE_CALIBRATION.md §4; FRISCO_PROTOCOL.md, Result of Amendment 2]. At
the published level 0.85 the
single-seed fit above inserts 0.945 of the demand, and the fitted arm's
battery realises 95.5% (§5.3). Whether part of that arm's speed fit also
comes from vehicles held off the network was not tested. The fitter now has
an opt-in insertion constraint (`--min-inserted`), off by default and
byte-identical when off; no published arm has been refitted with it
[FRISCO_PROTOCOL.md, insertion re-analysis of the Amendment 2 refits;
CHANGELOG, 2026-10-07].

At `s` = 0.85 the replica is within 4% of the recording from 2.2 km on. The
first kilometre, the Old Hickory merge, runs a third too slow and the
kilometre after it a third too fast [I24_CAPACITY.md §5]. With the Old
Hickory on-ramp closed the corridor free-flows at 45–65 km/h over its first
four kilometres: that merge is the replica's only bottleneck
[I24_CAPACITY.md §6].

### 4.3 Step 3: ramps, jointly and out of sample

A compass search over six multipliers (two on-ramp inflows, two exit
fractions, the boundary speed schedule and SUMO's gap acceptance), fitted on
the first hour only, 43 evaluations:

| Point | Old Hickory | Hickory Hollow | HH exit | Others | RMSPE fitted hour | RMSPE held-out hour | Inserted |
|---|---|---|---|---|---|---|---|
| As is | 1.00 | 1.00 | 1.00 | 1.00 | 0.356 | 0.426 | 0.942 |
| Best | 0.750 | 1.250 | 1.125 | 1.000 | 0.332 | 0.356 | 0.960 |

*Source: [I24_CAPACITY.md §7; artifacts/i24_boundary_ramps_fit.json].* The
fit moves demand between the two on-ramps rather than adding it. The held-out
hour improves by 7 points, more than the fitted hour. This is the `ramps`
arm. Two later families on corrected maps converged to the same multipliers
[I24_VALIDATION.md §0.6, §0.10].

### 4.4 Lane-change parameters from independent observables

SUMO's default lane-change rules created two spurious bottlenecks, found by
tracing vehicles before any criterion was evaluated [I24_VALIDATION.md §1;
LESSONS.md row 10]:

- At the Hickory Hollow diverge, exiting vehicles in inner lanes stopped to
  wait for a gap: 894 stalled 10 s samples in two hours and 29.8 km/h over the
  kilometre upstream. Strategic eagerness `lc_strategic` = 5 gives 44 stalled
  samples and 84.7 km/h.
- At the Old Hickory merge, SUMO's keep-right obligation, which US freeways do
  not have, crowded the two right lanes (11,535 stalled samples). With
  `lc_keep_right` = 0 there are 200, and the lane shares are 32/26/22/20%
  against the observed 30/24/20/26%. *(Corrected 2026-10-06: the legal premise is wrong — Tennessee (Code §55-8-115), Minnesota (Stat. §169.18 subd. 10(b)) and Texas (Transp. Code §545.051(b)) require slower traffic to keep right; the lane-share calibration itself stands, and docs/FRISCO_PROTOCOL.md Amendment 1 re-calibrates the setting; docs/DISCHARGE_CALIBRATION.md §2.)*

Both values were set on the same seed and demand and are documented as fleet
fields, not hidden in route files [I24_VALIDATION.md §1]. A grid over the
remaining lane-change parameters, scored on lane shares and lane-change rates,
preferred high gap acceptance, which the speed-based joint fit had rejected;
the two objectives disagree, so nothing from that grid was adopted
[I24_CAPACITY.md §8].

*Recalibrated 2026-10-07 under Amendment 1 (§2.4, §4.6).* On I-24 five grid
pairs lie inside the rule's 1-point lane-share band, and the rule keeps
`lc_keep_right` = 0; on I-94 it chooses 0.1. Lane use barely responds: every
I-24 pair lies 1.4–4.5 points from the observed shares with no trend in
keep-right (one seed per pair), and on I-94 raising keep-right makes the fit
worse [DISCHARGE_CALIBRATION.md §3].

### 4.5 The same procedure on US-101, without retuning

The same two steps were run on the US-101 replica through corridor-agnostic
versions of the same scripts, with the same grid, seeds per point,
interpolation rule, objective and split rule [US101_CALIBRATED.md §1].

| | I-24 | US-101 |
|---|---|---|
| Capacity target [veh/h/lane] | 1,775 (tracked lower bound) | 2,068 (bootstrap lower bound of q_max) |
| Capacity before scaling | 1,631 | 1,823 |
| f*, mean T after | 0.875, 1.322 s | 0.794, 1.020 s |
| Population gap RMSE, before → after | 5.313 → 5.294 m | 6.532 → 6.976 m (+6.8%) |
| Link-flow GEH pass share, before → after | see §5.3 | 55.6% → 22.2% |
| Segment-speed RMSPE, before → after | see §5.3 | 36.6% → 27.9% |

*Source: [I24_CAPACITY.md §4; US101_CALIBRATED.md §2, §4;
artifacts/idm_us101_capacity.json; artifacts/us101_validation_calibrated.json].*
On US-101 the target is a 95th-percentile flow of 30 s bins, which probably
overstates sustained capacity; the site's sustained section flows are
1,440–1,710 veh/h/lane [US101_CALIBRATED.md §2]. The step costs gap error
there. The demand step then bought a better speed level with flow the road
never carried: seven of nine flow bins fail, and the reversed speed gradient
at the missing on-ramp merge remains [US101_CALIBRATED.md §4]. On both
corridors the demand step converged on the missing bottleneck, a merge
[US101_CALIBRATED.md §5]. A demand level cannot stand in for a merge.

### 4.6 Driver acceleration and keep-right (Amendment 1, 2026-10-06 and 2026-10-07)

**The diagnosis.** On bottleneck fixtures driven by each corridor's own fleet
(20 seeds), the model's capacity drop is inside the observed and published
range. After a lane drop it discharges 0.90 of the pre-breakdown flow with
the I-24 fleet and 0.83 with the I-94 fleet, against up to about 10% below
the pre-queue flow in Cassidy & Bertini (1999); on I-94's own detectors no
drop is resolvable (discharge ÷ pre-breakdown flow 1.10 [0.98, 1.23], on 8
of 9 days). What is low is the level. An on-ramp merge fixture with the I-24
fleet discharges 1,460 ± 20 veh/h per lane; four lanes of that reproduce the
replica's peak sections, against 1,657 per lane recorded (coverage-corrected,
possibly high). The setting that moves it is the population's mean maximum
acceleration, which no step had calibrated: +1 sd raises the fixture to
1,632 per lane and leaves straight-road capacity and free-flow speed
unchanged. Mean `T` cannot reach that level inside its range without
inflating pre-breakdown capacity to 1,917–1,929 per lane, and `b`, `s0`,
EIDM's extra parameters and the speed factor each move it by about 3% or
less [DISCHARGE_CALIBRATION.md §1]. These are fixture readings recorded in
the source document; no artifact holds them.

**The grid.** The rule of §2.4 was applied on each corridor's calibration
data. On I-24 that is its one recorded morning, with no holdout, one seed per
pair, on the flow family's fitted arm (`i24_replica_flow_speedcal`;
[I24_VALIDATION.md §0.10]). On I-94 it is the 35-minute slice, two seeds per
pair, against per-lane detector shares and S97's discharge on the five
calibration days. 75 runs, none failed, no collision [DISCHARGE_CALIBRATION.md
§3].

| Corridor | Pair (k, keep-right) | Lane-share RMSE [points] | Discharge [veh/h] | Target [veh/h] | Discharge error | Departed share |
|---|---|---|---|---|---|---|
| I-24 | current (0, 0) | 2.31 | 5,837 / 5,810 | 6,626 / 6,639 | 12.2% | 0.984 |
| I-24 | (0.5, 0) | 2.80 | 6,027 / 5,984 | | 9.5% | 0.996 |
| I-24 | chosen (1.0, 0) | 2.33 | 6,021 / 5,995 | | 9.4% | 0.995 |
| I-94 | current (0, 0) | 8.24 | 3,321 (S97) | 4,490.5 | 26.0% | 0.971 |
| I-94 | (1.0, 0) | 7.64 | 3,680 | | 18.0% | 0.977 |
| I-94 | chosen (1.0, 0.1) | 8.40 | 3,764 | | 16.2% | 0.977 |

*Source: [DISCHARGE_CALIBRATION.md §3, results; artifacts/driver_calibration_i24.json;
artifacts/driver_calibration_i94.json; per-run readings in
artifacts/p3_driver_grid_2026-10-07/]. I-24 discharge at data x = 2,200 /
3,200 m. A calibration grid: one seed per pair on I-24, two on I-94.* The
rule chose k = 1 on both corridors, a mean `a_max` of 1.4833 m/s², the top of
the measured range.

- On I-24 the discharge error falls by about a quarter and then plateaus:
  5,955–6,037 veh/h across the pairs from k = 0.5 to 1. The fixture's
  projection of 6,480–6,640 veh/h at +1 sd was not reached
  [DISCHARGE_CALIBRATION.md §1, §3].
- On I-94, S97 rises by about 440 veh/h and stays 16% short; the rest is the
  T.H.52 weave (§7.6), which `a_max` was not expected to fix. The choice of
  keep-right 0.1 over 0 is inside two seeds' noise (84 veh/h between them,
  against up to 264 veh/h between seeds at one pair), which the rule allows
  [DISCHARGE_CALIBRATION.md §3].
- Lane use barely responds to keep-right on either corridor (§4.4). The
  largest single part of I-94's lane-share error is a detector defect, and a
  map defect adds to it (§7.5).
- The I-94 grid ran on demand inputs that include the validation days, though
  its targets did not (§7.5) [I94_CALIBRATION_DAYS.md §1.3, §2].

Whether the choice survives full batteries is §5.7 for I-24 and §7.4 for
I-94. It is not adopted on I-24, and it is recommended, not adopted, on I-94.

---

## 5. Validation

### 5.1 The ring, and the density dependence of wave speed

The ring benchmark passes on 20 of 20 seeds for both emergence and
dampening in every I-24 battery [I24_VALIDATION.md §0.1]. It is the only
place where the emergence claim of this paper is tested with no inflow and
no boundary.

On a ring the density can be set directly. Both calibrated fleets were run
on a 1,500 m ring for 900 s at four densities, five seeds each, with no
controlled vehicles:

| Density [veh/km] | US-101 fleet: fronts / mean / in 14–22 km/h band | I-24 fleet: mean / in band |
|---|---|---|
| 40 | 14 / 12.0 km/h / 29% | 12.1 km/h / 31% |
| 60 | 30 / 14.0 km/h / 70% | 14.2 km/h / 69% |
| 80 | 62 / 17.1 km/h / 98% | 16.4 km/h / 95% |
| 100 | 46 / 17.0 km/h / 85% | 16.9 km/h / 88% |

*Source: [WAVE_SPEED_DIAGNOSIS.md, follow-up of 2026-09-02;
artifacts/wave_speed_sitelength.json; artifacts/wave_speed_sitelength_i24.json];
relative detector. Diagnostic counts, no confidence intervals.* The emergent
wave speed rises from about 12 km/h near critical density to about 17 km/h
at 80–100 veh/km. For the US-101 fleet this lies between the fitted
diagram's `w` of 14.6 km/h and Newell's 18.3–19.7 km/h
[WAVE_SPEED_DIAGNOSIS.md]. The absolute 40 km/h detector finds no fronts at
80 and 100 veh/km, because it labels the whole field as one jam; this is
what led to the relative and stack detectors [WAVE_SPEED_DIAGNOSIS.md;
LESSONS.md row 12]. Two populations, calibrated independently on
instruments a generation apart, put emergent waves in the empirical band
once the density is there. Figure 3 shows the curve.

### 5.2 US-101: a 640 m site

The US-101 replica runs the NGSIM population and the observed upstream
inflow on 640 m of five-lane geometry, 20 seeds per arm [M3_US101_VALIDATION.md
§1]. Without a downstream boundary it free-flows. With the measured
downstream speed imposed on an exit buffer outside the span, congestion
spills back into the span as it did on the road [M3_US101_VALIDATION.md §2].

| Criterion | No boundary | Measured boundary | Boundary + FHWA steps 1–2 |
|---|---|---|---|
| Link-flow GEH < 5, share of bins (≥ 85%) | 77.8% FAIL | 55.6% FAIL | 22.2% FAIL |
| Segment-speed RMSPE (≤ 15%) | 72.8% FAIL | 36.6% FAIL | 27.9% FAIL |
| Backward wave speed (14–22 km/h), standard detector | none found FAIL | 5.8 km/h FAIL | 5.8 km/h FAIL |
| Ring emergence / dampening | not evaluated by this driver, counted as FAIL | same | same |
| Replicates ≥ 20 | PASS | PASS | PASS |

*Source: [M3_US101_VALIDATION.md §3; US101_CALIBRATED.md §4;
artifacts/run_summaries/m3_us101/; artifacts/us101_validation_calibrated.json].*
Every arm is 1 PASS / 5 FAIL. Three of the five are measured failures (link
flows, speeds, wave speed). The other two are the ring rows, which this
driver does not evaluate and counts as failing. The one pass is the
replicate count. Under the corrected metric definitions the
speed row reads 35.9% with the boundary and 28.4% calibrated, and the
standard detector's wave reading 6.1 and 5.9 km/h; no verdict changes
[M3_US101_VALIDATION.md, re-run of 2026-09-17]. Scored with the profile's
stack detector, as the criterion requires, the wave row reads NaN on both
arms: no backward front in any of the 20 replicates, and none in the
observed field either. On this 640 m site the row therefore cannot tell the
model from the data; it fails because the detector finds no front
[M3_US101_VALIDATION.md, note of 2026-09-25;
artifacts/us101_validation_calibrated.json]. The re-run's artifact also carries the sensitivity-grid row,
not evaluated by this driver either [artifacts/us101_validation_calibrated.json].

What each failure says. The site is a third of a typical wave's wavelength,
so the standard detector's front is the boundary queue pinned at the site
edge. The observed stripes run at 15.6 km/h with a stripe-level detector,
the simulated ones at 10.7 km/h [M3_US101_VALIDATION.md §4]. The ring
diagnostic of §5.1 reads this as a site-length and operating-density effect,
not a calibration defect: the same fleet makes 14.6 km/h waves at 60 veh/km,
the fitted `w` [WAVE_SPEED_DIAGNOSIS.md]. The speed error is mostly a
reversed gradient: the road is slow upstream, at an on-ramp merge the
replica does not model, and the boundary-driven replica is slow downstream
[M3_US101_VALIDATION.md §3]. The boundary also changes what the run shows. It
demonstrates that the model propagates an imposed congestion state, not that
it predicts congestion onset; the no-boundary arm is the predictive baseline,
and it free-flows [M3_US101_VALIDATION.md §6 item 2].

The wave-speed failure on this site was the basis of a prediction: a longer,
more congested corridor should pass the wave row where 640 m could not
[WAVE_SPEED_DIAGNOSIS.md]. I-24 is that test.

### 5.3 I-24: the criteria battery

Four demand arms were built on the capacity-calibrated population of §4.1:
demand as tracked (`tracked`); demand divided by the apparent coverage
(`corrected`); the coverage-shaped demand at the fitted level 0.85
(`speedcal`); and that level with the fitted ramps (`ramps`). Each ran 20
seeds on the original map. The criteria rows below were re-simulated on
2026-09-17 under the corrected metric definitions and reproduced to the
digit [I24_VALIDATION.md §0.1].

| Criterion | Tracked | Corrected | Fitted level | Fitted level + ramps | Threshold |
|---|---|---|---|---|---|
| Link-flow GEH < 5, vs tracked crossings ÷ recommended coverage | 0.7% FAIL | 16.7% FAIL | 18.8% FAIL | 20.1% FAIL | ≥ 85% |
| Segment-speed RMSPE, 5 min × 549 m | 187.8% FAIL | 33.7% FAIL | 35.9% FAIL | 34.8% FAIL | ≤ 15% |
| Backward wave speed, `stack` | no peak; standard 7.9 km/h FAIL | 15.9 km/h PASS | 15.8 km/h PASS | 15.7 km/h PASS | 14–22 km/h |
| Ring emergence (20 seeds) | PASS | PASS | PASS | PASS | every seed |
| Ring dampening (20 seeds) | PASS | PASS | PASS | PASS | every seed |
| Replicates | PASS | PASS | PASS | PASS | ≥ 20 |
| Sensitivity grid with CIs | PASS | PASS | PASS | PASS | 24 cells |
| **PASS / FAIL** | **4 / 3** | **5 / 2** | **5 / 2** | **5 / 2** | |
| Demand realised | 100% | 81.3% | 95.5% | 96.5% | |

*Source: [I24_VALIDATION.md §0.1; artifacts/i24_validation_{tracked,corrected,speedcal,ramps}.json].*
Before capacity calibration the same battery scored 1 PASS / 5 FAIL on the
two arms then built, with the ring rows not yet evaluated
[I24_VALIDATION.md §2].

Read the table carefully. Of the seven rows, only three compare the replica
with the recording: link flows, segment speeds and wave speed. The ring rows
test the fleet on a ring, and the last two rows test the experiment design.
Of the three comparisons, the two that concern flows and speeds fail in every
arm, and the wave row passes with a detector-dependent reading (§5.4).

| Metric (20 seeds, mean [95% CI]) | Tracked | Corrected | Fitted level | Fitted + ramps | Observed |
|---|---|---|---|---|---|
| Throughput at data x = 2,200 m [veh/h] | 4,061 [4,052, 4,070] | 5,687 [5,660, 5,714] | 5,839 [5,808, 5,870] | 5,699 [5,683, 5,715] | 5,820–7,138 (corrected counts) |
| Mean travel time over the span [s] | 247 [244, 249] | 629 [623, 636] | 590 [582, 598] | 607 [602, 612] | ≈ 220 at free flow |
| σ_v temporal [m/s] | 4.44 | 4.75 | 4.94 | 4.86 | |
| Fuel [ml/veh-km] | 65.6 | 108.2 | 100.7 | 103.1 | |

*Source: [I24_VALIDATION.md §0.2].* The tracked arm is a half-empty road at
76–79 km/h against 30–38 km/h observed; the instrument's counts cannot be used
as demand. The three congested arms reproduce the corridor from 2.2 km on
within a few km/h and reproduce its stop-and-go pattern (Figure 4)
[I24_VALIDATION.md §0.3].

### 5.4 The wave-speed row, and what it rests on

| Detector | Tracked | Corrected | Fitted level | Fitted + ramps | Observed |
|---|---|---|---|---|---|
| `stack` (the criterion) | — (0/20) | 15.9 (9/20) | 15.8 (12/20) | 15.7 (7/20) | 19.9 (contrast 3.44) |
| `standard`, 40 km/h | 7.9 | 10.4 | 9.9 | 8.4 | 14.2 (median 17.5) |
| `stripe`, 25 km/h | 7.4 | 14.2 | 14.4 | 14.0 | 16.0 |
| `relative`, 0.5 × p90 | 5.5 | 13.8 | 13.8 | 13.6 | 16.4 |

*Source: [I24_VALIDATION.md §0.2]; km/h; "N/20" is the number of replicates
in which the stack found a peak at contrast ≥ 3.*

The order of events matters. The first battery, on 3 September, scored the
row with the standard detector and failed it; that battery also ran on the
uncalibrated population, and there the prediction of §5.2 was recorded as
not confirmed [I24_VALIDATION.md §4]. The detector benchmark of §2.5 then
showed the standard detector recovers nothing on a congested background, and
the criteria profile was changed to name `stack`. The battery scored under
that profile is the first in which the row passes. The same configurations
reproduced their earlier fields to every reported digit, so the change in
the row is a change in the instrument, not in the physics
[I24_VALIDATION.md §0.4].

What the pass rests on [I24_VALIDATION.md §0.4]:

- The stack finds a peak in only 7–12 of 20 replicates per arm. In the rest,
  no single backward front speed dominates at the required contrast.
- The observed field clears the contrast threshold narrowly (3.44 against 3).
- Simulated and observed estimates differ by 4 km/h. The criterion scores
  the band, not agreement; an agreement test at ± 2 km/h would fail.
- Under every recipe that resolves the fronts, the replica's waves are 2–4
  km/h slower than the recording's.

So the prediction is confirmed as the criterion is specified, and the
detector dependence is part of the result. The standard-detector reading
stays in the table. Figure 5 shows the front-speed distributions.

### 5.5 Why the flow and speed rows fail

**The speed row is scored below the recording's own repeatability.** RMSPE
compares the 20-seed mean field with one recorded day on 5-min × 549 m bins.
The recording against a 15-min moving average of itself already scores
33.4% [I24_VALIDATION.md §0.5(a)].

| Arm | 5-min (criterion) | 15-min | 30-min | 60-min | 2-h |
|---|---|---|---|---|---|
| Corrected | 33.7% | 27.1% | 24.9% | 21.5% | 21.3% |
| Fitted level | 35.9% | 26.3% | 23.4% | 19.6% | 18.9% |
| Fitted + ramps | 34.8% | 25.5% | 22.1% | 19.4% | 18.9% |
| Recording vs its own 3-block moving average | 33.4% | 15.1% | | | |

*Source: [I24_VALIDATION.md §0.5(a)].* At 5 min no ensemble mean can reach
15% on this corridor. At 15 min the recording's floor equals the threshold
and the arms sit ten points above it; those ten points are real. The
criterion keeps the 5-min resolution it was given before the result was
known, because changing it afterwards would be tuning the test
[I24_VALIDATION.md §0.5(a)].

**The flow shortfall is discharge, not counting.** Against the recommended
coverage-corrected counts, the fitted arm is 12–13% short at the two peak
sections, where GEH < 5 allows about 6%. The replica discharges less than the
road [I24_VALIDATION.md §0.5(b)].

**Where the error is, lane by lane.** In the recording the right lane is the
fastest lane at the entry, because the Old Hickory off-ramp has just drained
it; ramp traffic runs down the acceleration lane at 45–49 km/h and merges in
its last 900 m, and the right lane gives up a third of its speed there. In
the replica the right lane crawls at 8 km/h from 1.5 km upstream of the gore
and ramp traffic merges at the gore at 9–13 km/h. The replica's bottleneck is
a merge that happens too early and too slowly [I24_VALIDATION.md §0.5(c)]
(Figure 6).

**Six rounds of candidate causes, each excluded with an artifact.**

| Candidate | Test | Outcome | Source |
|---|---|---|---|
| Acceleration-lane length (map defect) | corrected map, single seed | admits 93% instead of 85% of ramp demand; profile moves < 1 km/h | [I24_VALIDATION.md §0.5(e)] |
| SUMO lane-change eagerness, cooperation, gap acceptance, speed gain | single-seed grids | inert, or clears the merge and makes the corridor too fast downstream | [I24_CAPACITY.md §6.1; I24_VALIDATION.md §0.5(f)] |
| Entry lane distribution (vehicle-time, then flow shares) | single seed, then a full FHWA sequence and 20-seed batteries | 1–3 points on the flow row, nothing on the speed row | [I24_VALIDATION.md §0.5(g), §0.9, §0.10] |
| Sublane lane-change model | single seeds | locks or loses capacity as configured | [I24_VALIDATION.md §0.5(h), §0.7] |
| Zipper junction and its parameters | single seeds, then a full FHWA sequence and 20-seed batteries | removes the upstream crawl, admits 60–64% of the ramp; moves neither failing row | [I24_VALIDATION.md §0.5(j–k), §0.6] |
| Keep-right, speed gain, passing on the right, interleaving distance | 14 single seeds | inert, destructive, or a flow-for-speed trade | [I24_VALIDATION.md §0.7] |
| Scripted late merge (a controlled ramp-vehicle class) | 6 single seeds | entry speed matches; a fifth to a quarter of the ramp demand never enters | [I24_VALIDATION.md §0.8] |
| Scoring target weighted by lane coverage | re-score | +0.54 points on the fitted arm's speed row | [I24_VALIDATION.md §0.9a] |
| Old Hickory demand level | 5 single seeds | admittance saturates at 2,224–2,241 vehicles; peak sections stay at 5,860–5,890 veh/h | [I24_VALIDATION.md §0.11] |
| Heavy vehicles placed by lane | 3 single seeds | speeds move ≤ 2 km/h; admittance falls | [I24_VALIDATION.md §0.11] |

Every single-seed row is a probe, not a result. What they show together is
consistent: with the calibrated car-following population, the Old Hickory
merge discharges about 5,880 veh/h where the recording sustains 6,630, and
the queue that shortfall builds is the whole of the two failing rows
[I24_VALIDATION.md §0.11].

**The car-following headways are not where the capacity is missing.** A
population refitted only on the 4,193 episodes inside the merge zone keeps a
4.6% longer headway and a 5% larger minimum gap than the corridor as a whole,
with a better holdout fit:

| Population | Episodes fit / holdout | Holdout gap RMSE [m] | Mean T [s] | Equilibrium capacity [veh/h/lane] |
|---|---|---|---|---|
| Corridor-wide | 12,356 / 5,296 | 5.29 | 1.511 | 1,788 |
| Corridor-wide, capacity-scaled (the arms') | 12,356 / 5,296 | 5.29 | 1.322 | 1,986 |
| Merge zone only | 2,935 / 1,258 | 4.53 | 1.580 | 1,720 |

*Source: [I24_VALIDATION.md §0.12; artifacts/idm_i24_merge.json;
artifacts/idm_i24_capacity_equilibrium.json]; closed-form capacity, an index
rather than a simulated value.* Driven by the merge-zone population, the
fitted arm discharges 9% less through the merge in a single-seed probe
[I24_VALIDATION.md §0.12]. The capacity-scaled population carries 7,100 veh/h
on four straight lanes and 5,880 through the merge, 83%; the recording
sustains 6,630, 93% of that straight-road figure. What the recording does at
the merge and the simulator does not is the merging itself: gap acceptance
and cooperation during the lane change. We record the merge as a model-form
limitation of the simulator as configured. No further car-following
calibration will move the two failing rows [I24_VALIDATION.md §0.12].

*Corrected 2026-10-07.* This paragraph's heading read "the car-following
population", and its last sentence ruled out any further car-following
calibration. That holds for the headway parameters, `T` and `s0`, which the
merge-zone refit examined. It does not hold for the population's mean maximum
acceleration, which no step had calibrated. Raised by one standard deviation
under a rule fixed in advance, it lifts the peak sections from 5,850 / 5,821
to 6,031 / 6,025 veh/h over 20 seeds, about a quarter of the way to the
recording, and the GEH row from 21.5% to 25.0% on the flow family's fitted
arm. Neither failing row passes, and the wave row is lost (§5.7). A merge
model built on the measured gap acceptance did not recover the rest either
(§8.2). What remains, about 600 veh/h, is a merge and discharge shortfall
whose mechanism is not established [DISCHARGE_CALIBRATION.md §1, §4;
MERGE_MODEL_READINESS.md §2, §5].

### 5.6 Variants that do not change the record

| Arm (20 seeds) | GEH < 5 | 5-min RMSPE | Stack wave [km/h] | Rows | Source |
|---|---|---|---|---|---|
| Fitted level + heavy vehicles (9.2% share) | 23% | 35.5% | 17.3 | 5 / 2 | [I24_VALIDATION.md §0.6] |
| Zipper family, fitted + ramps | 23% | 34.2% | 15.7 | 5 / 2 | [I24_VALIDATION.md §0.6] |
| Flow-share family, corrected | 17.4% | 33.7% | 15.4 | 5 / 2 | [I24_VALIDATION.md §0.10] |
| Flow-share family, fitted + ramps | 21.5% | 41.8% | 15.8 | 5 / 2 | [I24_VALIDATION.md §0.10] |

The heavy-vehicle arm's throughput falls by the heavy share's own capacity:
5,276 [5,246, 5,305] against 5,839 [5,808, 5,870] veh/h under the corrected
definitions of the 2026-09-17 re-run [artifacts/i24_validation_speedcal_heavy.json;
artifacts/i24_validation_speedcal.json], and 5,170 [5,144, 5,196] against
5,710 [5,679, 5,741] under those of 2026-09-05 [I24_DATA.md, heavy-vehicle
section]. The zipper
heavy arm finds no stack peak and scores 4 / 3 [I24_VALIDATION.md §0.6].
The canonical family remains the published record [I24_VALIDATION.md §0.10].

### 5.7 Driver acceleration on I-24: a calibration on the one recorded morning (2026-10-07)

*Calibration, not validation. The I-24 recording is one morning, and every
arm below is fitted and scored on it, with no holdout* [FRISCO_PROTOCOL.md,
Amendment 1].

The Amendment-1 choice (k = 1, keep-right 0; §4.6) was run as 20-seed
batteries on the flow family's fitted arm, the arm the grid used. The
reference arm, run on the same code, reproduces that arm's committed battery
exactly (GEH 21.5%, RMSPE 37.2%, stack wave 15.9 km/h; [I24_VALIDATION.md
§0.10]). Every run recorded zero collisions, and the ring rows passed at 20
of 20 seeds in every battery [DISCHARGE_CALIBRATION.md §4].

| Arm (20 seeds) | Demand scale | GEH < 5 | Segment-speed RMSPE (5 min) | Wave row (`stack`) | Jam components per replicate, standard detector (with a backward front) | Peak sections [veh/h] | Realised demand (mean, lowest) | Mean travel time [s] |
|---|---|---|---|---|---|---|---|---|
| Reference, measured-mean drivers (k = 0) | 0.800 | 21.5% | 37.2% | 15.9 km/h PASS | 9.05 (7.3) | 5,850 / 5,821 | 0.987, 0.977 | 574 |
| k = 1, old demand | 0.800 | 25.0% | 78.4% | no qualifying peak, FAIL | 13.65 (10.3) | 6,031 / 6,025 | 0.996, 0.994 | 536 |
| k = 1, demand refit | 0.925 | 25.7% | 33.3% | no qualifying peak, FAIL | 6.65 (5.05) | 6,047 / 5,983 | 0.921, 0.912 | 669 |
| k = 0.25, demand refit (Amendment 2) | 0.900 | 16.0% | 34.1% | 15.7 km/h PASS | — | 5,871 / 5,820 | 0.918, 0.908 | — |
| k = 0.5, demand refit (Amendment 2) | 0.900 | 24.3% | 33.9% | 15.1 km/h PASS | — | 5,953 / 5,899 | 0.930, 0.916 | — |

*Source: [DISCHARGE_CALIBRATION.md §4; FRISCO_PROTOCOL.md, Result of
Amendment 2; MERGE_MODEL_READINESS.md §5 (peak sections of the first three
arms); artifacts/i24_validation_flow_speedcal_ref.json;
artifacts/i24_validation_dc.json; artifacts/i24_validation_dc_refit.json;
artifacts/i24_validation_dck025_refit.json;
artifacts/i24_validation_dck05_refit.json;
artifacts/demand_scale_i24_flow_dc.json;
artifacts/demand_scale_i24_flow_dck{025,05}.json]. Peak sections at data
x = 2,200 / 3,200 m, against 6,626 / 6,639 veh/h recorded and 6,225 / 6,238
needed for GEH 5. Jam components are the standard detector's connected
jam regions (`simulated.waves_per_replicate[].count`, the mean of which is
`wave_count_mean`); the count in parentheses is those with a backward front
(`n_backward`). "—": not quoted in the source documents.*

- **More demand did not raise the peak flow.** The demand fitter of §4.2, run
  again under the stronger drivers, raised the demand by 15.6%. The peak
  sections did not move (6,031 / 6,025 → 6,047 / 5,983 veh/h). The extra
  demand built a backlog instead: the realised share fell from 0.996 to
  0.921, and mean travel time rose from 536 to 669 s. The plateau is
  therefore a merge and discharge shortfall at about 6,030 veh/h against the
  recording's 6,630, not a demand limit. The source first read the grid's
  plateau as demand-limited; the refit reversed that reading the same day
  [DISCHARGE_CALIBRATION.md §3, correction, and §4; MERGE_MODEL_READINESS.md
  §5].
- **Part of the refit's better speed fit comes from vehicles held off the
  network.** Its GEH and RMSPE are slightly better than the reference's, but
  not as an improvement of the model [DISCHARGE_CALIBRATION.md §4]. The
  fitter's objective does not see insertion, and in all three refits under
  stronger drivers it built a backlog (§4.2) [FRISCO_PROTOCOL.md, Result of
  Amendment 2]. A later re-analysis of the three refits on their single fit
  seed finds that most of their speed advantage was the backlog. Constrained
  to insert at least 0.97–0.98 of the demand, the fitter picks scales 0.05–0.10
  lower, and the k = 1 refit is then worse than the reference's fit on both
  hours (RMSPE 38.3 / 51.0% against 32.7 / 46.0%). That is one seed, and no
  battery has run at the constrained scales [FRISCO_PROTOCOL.md, insertion
  re-analysis of the Amendment 2 refits].
- **The wave criterion is lost at k = 1.** In both k = 1 arms the stack
  detector finds no peak at the required contrast. With the refit, the
  standard detector finds 6.65 jam components per replicate against 9.05
  (5.05 against 7.3 with a backward front), and only the secondary
  detectors still read speeds near the band (stripe 14.3, standard 9.3
  km/h). This is the risk recorded before any run: a higher mean `a_max`
  makes the drivers more string-stable (§2.2) [DISCHARGE_CALIBRATION.md §1,
  §4].
- **Smaller shifts keep the waves but not the demand.** Under Amendment 2's
  rule, k = 0.25 and k = 0.5 both keep the wave row, and k = 0.5 is no worse
  than the reference on GEH and RMSPE. Both refits hold 7–8% of the planned
  vehicles off the network, below the 0.977 floor. The floor was written
  before any result was available or read, though after the k = 0.25
  battery had been written on the cloud machine (§2.4). No arm qualifies,
  and k = 0 stays on I-24 [FRISCO_PROTOCOL.md, Result of Amendment 2 and
  the corrected clarification].

The calibrated drivers are therefore not adopted on I-24. The flow family's
fitted arm with the measured-mean drivers stays the reference for this work,
and the k = 1 refit is kept as a documented alternative
[DISCHARGE_CALIBRATION.md §4]; the published record of §5.3 is unchanged
(Appendix C, item 19). The same driver shift is the largest single
improvement the I-94 model has had (§7.4) and costs I-24 its emergent waves,
so one calibration recipe does not serve both corridors
[DISCHARGE_CALIBRATION.md §4].

**The corridor is not validated.** The replica reproduces the recording's
stop-and-go pattern and its wave-speed band under the criterion's detector.
It does not reproduce its flows or its segment speeds, and the reason is
one merge that the simulator does not discharge at the observed rate: not
with SUMO's lane-change model (§5.5), not with the drivers' acceleration at
the top of its measured range (this section), and not with a merge model
built on the measured gaps (§8.2). *(Revised 2026-10-07: this sentence had
attributed the shortfall to the lane-change model alone.)*

---

## 6. Controller and strategy results

Every result in this section is `seeded=False` and uses 20
common-random-number seeds per cell unless marked otherwise. Paired changes
are per seed against the same-seed baseline. Three settings appear, and they
should not be confused: the synthetic single-lane `corridor_10km` (§6.1–6.4,
§6.9), the US-101 replica with its measured boundary (§6.5), and the
unvalidated I-24 replica (§6.6–6.8).

### 6.1 The synthetic corridor: dose-response at no resolved cost

`corridor_10km` is a single-lane 10 km corridor with an EIDM fleet at the
specification's default parameters (15% heterogeneity) and a demand profile
tuned for wave emergence, not fitted to counts. It is not a real corridor and
its results are not field predictions [M3_RESULTS.md, scope disclaimer]. The
battery is 27 cells × 20 seeds = 540 runs [M3_RESULTS.md §1].

| FollowerStopper penetration (100% compliance) | σ_v temporal [m/s] | Paired σ_v reduction | Waves / run | Throughput [veh/h] | Fuel [ml/veh-km] |
|---|---|---|---|---|---|
| 0 (baseline) | 3.39 [2.83, 3.94] | — | 3.85 [2.59, 5.11] | 1,247 [1,227, 1,266] | 65.4 [64.6, 66.3] |
| 1% | 2.44 [2.14, 2.73] | 24.5% [17.7, 31.4] | 2.10 [1.17, 3.03] | 1,256 [1,242, 1,270] | 63.6 [63.2, 64.0] |
| 2% | 1.99 [1.80, 2.18] | 36.6% [28.5, 44.7] | 1.55 [0.85, 2.25] | 1,257 [1,244, 1,271] | 62.9 [62.6, 63.1] |
| 5% | 1.31 [1.24, 1.38] | 56.8% | 0.15 [−0.02, 0.32] | 1,258 [1,247, 1,270] | 62.2 [61.9, 62.5] |
| 10% | 0.96 [0.92, 1.00] | 68.1% | 0.00 | 1,254 [1,244, 1,264] | 61.9 [61.6, 62.2] |
| 15% | 0.80 [0.76, 0.85] | 73.0% | 0.00 | 1,253 [1,242, 1,263] | 61.8 [61.5, 62.1] |
| 20% | 0.70 [0.64, 0.75] | 76.4% | 0.00 | 1,250 [1,239, 1,260] | 61.7 [61.4, 61.9] |

*Source: [M3_RESULTS.md §2, §4.1–4.2; artifacts/m3_sweep_summary.json].*
Paired reductions are means of per-seed percentages; every paired interval
excludes zero. Figure 7 shows the dose-response.

- **Low penetration already resolves.** All eight cells at 1–2% penetration
  resolve a σ_v reduction, down to 6.9% [0.9, 12.9] at 1% penetration and
  25% compliance; the wave-count change is not resolved at 25% compliance
  [M3_RESULTS.md §4.2].
- **Compliance trades against penetration.** Reductions collapse
  approximately onto the complied share of the fleet: 1% / 100% gives 24.5%,
  2% / 50% gives 21.1%; 5% / 50% gives 42.3% and 10% / 25% gives 41.8%
  [M3_RESULTS.md §4.3].
- **No resolved throughput cost.** The paired throughput change is positive
  in all 24 cells and resolved in 6, all small gains (+9 to +15 veh/h). With
  24 tests, about one would resolve by chance, and no cell survives a
  Bonferroni correction. We read this as an inflow-limited corridor, not as a
  throughput gain [M3_RESULTS.md §4.4].
- **Fuel falls with dose**, from −2.8% [−3.8, −1.7] at 1% / 100% to −5.7%
  [−7.2, −4.2] at 20% / 100% [M3_RESULTS.md §4.4].
- **The synthetic corridor fails the wave row.** Its baseline backward waves
  run at 8.2 [7.0, 9.4] km/h (15 of 20 replicates with a front; flagged
  underpowered), below the 14–22 km/h band [M3_RESULTS.md §4.6].

### 6.2 Controllers compared at 5% penetration, 100% compliance

| Controller | σ_v temporal | Wave count | Throughput | Fuel |
|---|---|---|---|---|
| FollowerStopper | −61.2% (resolved) | −96.1% (resolved) | +0.9% (n.r.) | −5.0% (resolved) |
| JAD, 30 s + 20% noise oracle | −60.6% (resolved) | −90.9% (resolved) | +1.0% (n.r.) | −5.0% (resolved) |
| JAD, perfect oracle | −47.4% (resolved) | −45.5% (n.r.) | −6.8% (n.r.) | +2.6% (n.r.) |
| PI with saturation (Stern Eqs. 3–5) | −29.7% (resolved) | −41.6% (resolved) | −0.7% (n.r.) | −2.9% (resolved) |
| PI mean-fraction (superseded) | −36.7% (resolved) | −41.6% (resolved) | −93.6% (resolved) | +268.9% (resolved) |

*Source: [CONTROLLER_COMPARISON.md]; paired change against the uncontrolled
baseline as a share of the baseline mean; `corridor_10km`; n.r. = not
resolved.* This table expresses changes relative to the baseline mean; §6.1
averages per-seed percentages, which is why FollowerStopper reads 61.2% here
and 56.8% there (Appendix C, item 5).

FollowerStopper and JAD with a realistic oracle are statistically tied; their
intervals overlap on every metric [CONTROLLER_COMPARISON.md]. FollowerStopper
is the simpler choice because it needs only the gap and speed of the vehicle
ahead, while JAD needs a 2 km downstream speed field [CONTROLLER_COMPARISON.md].
The faithful PI controller works and trails both. None of these controllers
was tuned for this corridor, so the table ranks default configurations
[CONTROLLER_COMPARISON.md, Limitations].

### 6.3 A specification error, not a controller failure

The first sweep reported that the controller registered as PI with
saturation gridlocked the corridor at 5% penetration: throughput fell by
1,167 veh/h [−1,220, −1,114], 93.6%, and fuel rose 268.9%. In a replicate,
99.6% of vehicles were stopped by t > 900 s [M3_RESULTS.md §4.5;
PI_CONTROLLER_FIX.md §3]. The controller under test was a simplification in
our own specification, `v_target = 0.75 · v̄_platoon`. On an open corridor
that is a geometric ratchet: the controlled vehicle slows the platoon whose
mean sets its own target [PI_CONTROLLER_FIX.md §1]. Stern et al. (2018) §3.2
has no such factor. Its target is the vehicle's own mean speed plus a
bounded, non-negative gap term, which cannot ratchet. Implemented as
published, the controller cuts σ_v by 29.7% at no resolved throughput or
travel-time cost [PI_CONTROLLER_FIX.md §2–3]. On the ring, one PI vehicle of
22 takes σ_v from 2.07 to 0.46 m/s and FollowerStopper to 0.17 m/s, over 3
seeds with no interval; this is a regression check, not a result
[PI_CONTROLLER_FIX.md §4]. The simplification is kept as `pi_meanfrac` only
so that the failure stays reproducible [LESSONS.md row 2].

### 6.4 Detection latency makes JAD reliable, and deferral makes it a rule

CLAUDE.md §4.3 requires every headline JAD result to be reported under a
delayed and noisy oracle as well as a perfect one. Doing so inverted the
expected ordering.

| Paired change vs baseline | Perfect oracle | 30 s delay + 20% noise | 60 s delay + 20% noise |
|---|---|---|---|
| σ_v temporal [m/s] | −1.60 [−2.27, −0.94] | −2.05 [−2.62, −1.49] | −2.09 [−2.66, −1.52] |
| Waves per run | −1.75 [−4.04, +0.54] (n.r.) | −3.50 [−4.71, −2.29] | −3.75 [−4.95, −2.55] |
| Fuel [ml/veh-km] | +1.70 [−4.69, +8.09] (n.r.) | −3.27 [−4.33, −2.21] | −3.25 [−4.30, −2.19] |
| Seeds ending with more waves than baseline | 5 of 20 | 0 of 20 | 0 of 20 |
| AV acceleration reversals per run | 30.7 | 16.6 | 15.8 |

*Source: [JAD_ORACLE_RESULTS.md §2, §4; artifacts/jad_oracle_summary.json];
`corridor_10km`, 5% / 100%.* With a perfect oracle, JAD commits the instant
any bin in its 2 km lookahead qualifies. It then completes slow-in, hold and
fast-out before the front arrives, re-detects the same wave, and repeats; each
abrupt fast-out can seed a secondary wave. Latency halves the chatter and the
wave outcomes follow [JAD_ORACLE_RESULTS.md §4]. This is a finding about
this commit rule, not an argument for poor sensors.

If latency helps because it defers commitment, an explicit deferral should
capture the benefit with a perfect sensor. It does:

| Cell | σ_v temporal [m/s] | Waves / run | Paired σ_v vs baseline | Paired waves vs baseline | Paired fuel vs baseline | Paired σ_v vs undeferred JAD |
|---|---|---|---|---|---|---|
| Perfect oracle, no deferral | 1.781 [1.298, 2.264] | 2.10 [0.12, 4.08] | −47.4% | −45.5% (n.r.) | +2.6% (n.r.) | — |
| Perfect oracle, 30 s deferral | 1.331 [1.228, 1.435] | 0.25 [−0.27, 0.77] | −60.7% | −93.5% | −5.0% | −25.2% (resolved) |
| Perfect oracle, 60 s deferral | 1.294 [1.234, 1.354] | 0.05 [−0.06, 0.16] | −61.8% | −98.7% | −5.0% | −27.3% (resolved) |
| 30 s delay + 20% noise, no deferral | 1.333 [1.244, 1.422] | 0.35 [−0.28, 0.98] | −60.6% | −90.9% | −5.0% | −25.2% (resolved) |

*Source: [JAD_DEFERRAL_RESULTS.md; artifacts/jad_deferral_summary.json];
all changes vs baseline resolved unless marked; no deferral cell has a seed
worse than baseline on σ_v.* Throughput is unchanged within its interval in
every deferral cell [JAD_DEFERRAL_RESULTS.md]. The rule has no hysteresis on
the speed threshold, and 30 s and 60 s were taken from the specified latency
range, not optimised [JAD_DEFERRAL_RESULTS.md, Limitations]. JAD has not been
run on the I-24 replica.

### 6.5 US-101: smoothing replicates, the cost appears, and its mechanism

On the US-101 replica with its measured boundary, FollowerStopper at 100%
compliance, 20 seeds per level:

| Penetration | σ_v temporal | Fuel, original runs | Throughput, as first published (x = 400 m) | Throughput on the replica (re-run) |
|---|---|---|---|---|
| 1% | −8.2% | +1.7% | −0.3% | −0.72% |
| 2% | −12.3% | +2.2% | −0.4% | −1.05% |
| 5% | −24.2% | +2.7% | −0.9% | −1.69% |
| 10% | −37.3% | +1.4% | −1.3% | −1.97% |
| 20% | −53.2% | +0.5% (n.r.) | −1.6% | −2.74% |

*Source: [US101_PENETRATION.md, paired-change table and its correction of
2026-09-25; artifacts/us101_penetration_summary.json;
artifacts/us101_lane_change_penetration.json]; paired changes vs baseline.
The first three columns are from the original runs and resolved unless
marked; the last column is the mean paired change from the 2026-09-25
re-run, for which the source quotes no interval.* The σ_v dose-response replicates on different
geometry, fleet and demand. The "no cost" part does not: fuel rises at 1–10%
and throughput falls. The throughput column as first published was measured
at x = 400 m in trajectory coordinates, inside the 640 m insertion buffer and
upstream of the replica. Re-measured on the replica, the cost is 0.7–2.7%,
about twice the published figure [US101_PENETRATION.md, correction note and
result section].

The first document proposed a multi-lane explanation for the fuel increase
and said it was untested [US101_PENETRATION.md, "Why the difference is
plausible"]. The test was pre-registered with a decision rule and run on the
same seeds, with every trajectory kept and lane changes counted inside the
replica [US101_PENETRATION.md, method section]:

| Penetration | Fuel (all vehicles) | Human lane changes per human veh-km | Excess cut-ins ahead of an AV | Excess passes around an AV | Humans who never changed lanes, fuel | Pre-registered verdict |
|---|---|---|---|---|---|---|
| 1% | +1.29% | +0.034 [0.028, 0.040] | +0.021 | +0.000 [−0.000, 0.001] | +0.75 ml/km [0.32, 1.19] | not supported (c) |
| 2% | +1.91% | +0.064 [0.058, 0.071] | +0.043 | +0.001 [−0.000, 0.003] | +1.11 ml/km [0.69, 1.52] | not supported (c) |
| 5% | +2.21% | +0.108 [0.098, 0.118] | +0.088 | +0.007 [0.006, 0.009] | +0.96 ml/km [0.36, 1.56] | supported |
| 10% | +1.14% | +0.148 [0.137, 0.159] | +0.129 | +0.022 [0.018, 0.025] | −0.06 ml/km [−0.54, 0.43] | supported |
| 20% | +0.44% (n.r.) | +0.132 [0.120, 0.145] | +0.114 | +0.037 [0.031, 0.043] | −0.83 ml/km [−1.21, −0.45] | not applicable (a) |

*Source: [US101_PENETRATION.md, result section of 2026-09-25;
artifacts/us101_lane_change_penetration.json].* The baseline human rate is
0.049 changes per human vehicle-km. Humans change lanes 1.7–4.0 times as
often around FollowerStopper vehicles (1.7 at 1%, 4.0 at 10%). Humans who
changed lanes burn 5.8–6.3 ml/km more than humans who did not, every interval
above zero. That gap is an association, not a cost of the change: it is
already 5.47 ml/km [5.04, 5.90] in the baseline, with no AV, and its paired
change against the baseline is +0.30 to +0.82 ml/km, resolved at 1, 5 and 20%
only. The extra fuel is almost all the humans' (at 5%: 1.40 of 1.48 ml per
vehicle-km). But the extra changes are not mainly humans passing the
controlled vehicle. They are mostly humans cutting in to the larger gap it
keeps ahead of itself. Excess cut-ins are 0.63 / 0.67 / 0.82 / 0.87 / 0.86 of
the paired rise in human changes at 1 / 2 / 5 / 10 / 20%, so at 1–2% about a
third of the rise is neither an excess cut-in nor an excess pass. As a share
of excess cut-ins plus excess passes, cut-ins are 0.99 / 0.98 / 0.92 / 0.86 /
0.75 (ratios of the 20-seed means). At 1–5%, humans who never change lanes
also burn more, so lane changes are not the whole mechanism
[US101_PENETRATION.md, result section and its correction of 2026-09-25;
artifacts/us101_lane_change_penetration.json]. The hypothesis holds in its
broad form, as an association, and not in its specific one. These are
results on a 640 m replica that scores 1 PASS / 5 FAIL (§5.2), at one
compliance level.

### 6.6 The penetration × compliance battery on the unvalidated I-24 replica

**This corridor is not validated (§5), so every number here describes the
replica, not the road** [I24_SWEEP.md]. The battery runs FollowerStopper at
its literature constants on the fitted-level arm, penetration {1, 2, 5, 10,
15, 20}% × compliance {25, 50, 80, 100}% plus the baseline, 20
common-random-number seeds per cell, 500 runs, under the corrected metric
definitions [I24_SWEEP.md; artifacts/i24_sweep_summary.json, base config
`43def6306dd6`]. It also supplies the sensitivity row of the criteria table
(§5.3).

Baseline: throughput 5,839 [5,808, 5,870] veh/h at data x = 2,200 m, mean
travel time 590 [582, 598] s, σ_v temporal 4.94 [4.92, 4.97] m/s, 12.9
[11.3, 14.5] waves per replicate, fuel 101 [100, 102] ml/veh-km, 1.25 lane
changes per vehicle-km [I24_SWEEP.md].

| Penetration / compliance | Throughput [veh/h] | Δ | Mean travel time [s] | Δ | σ_v temporal [m/s] | Δ | Fuel [ml/veh-km] | Δ | Lane changes / veh-km |
|---|---|---|---|---|---|---|---|---|---|
| 1% / 25% | 5,741 [5,717, 5,766] | −2% | 607 [599, 614] | +3% | 4.52 [4.47, 4.57] | −9% | 103 [102, 103] | +2% | 1.27 |
| 1% / 100% | 5,541 [5,520, 5,563] | −5% | 651 [644, 658] | +10% | 3.74 [3.71, 3.77] | −24% | 107 [106, 108] | +6% | 1.34 |
| 2% / 100% | 5,014 [4,728, 5,300] | −14% | 828 [698, 958] | +40% | 3.03 [2.91, 3.15] | −39% | 130 [113, 147] | +29% | 1.44 |
| 5% / 25% | 5,290 [5,026, 5,554] | −9% | 702 [657, 748] | +19% | 3.49 [3.38, 3.60] | −29% | 118 [105, 131] | +17% | 1.37 |
| 5% / 100% | 3,626 [3,261, 3,992] | −38% | 1,191 [1,063, 1,318] | +102% | 2.02 [1.96, 2.08] | −59% | 213 [182, 243] | +111% | 1.70 |
| 10% / 100% | 2,475 [2,279, 2,671] | −58% | 1,579 [1,413, 1,744] | +168% | 1.53 [1.50, 1.56] | −69% | 322 [291, 354] | +220% | 1.98 |
| 20% / 25% | 4,010 [3,803, 4,217] | −31% | 1,053 [996, 1,110] | +79% | 2.07 [2.04, 2.11] | −58% | 178 [164, 192] | +77% | 1.66 |
| 20% / 100% | 1,373 [1,231, 1,515] | −76% | 2,081 [1,862, 2,299] | +253% | 1.29 [1.26, 1.32] | −74% | 552 [501, 602] | +448% | 2.32 |

*Source: [I24_SWEEP.md, the grid; artifacts/i24_sweep_summary.json]; Δ =
paired change against the same-seed baseline as a share of the baseline;
eight of the 24 cells shown, the full grid is in the source.* At 5% / 100%
the paired throughput change is −2,212 veh/h [−2,577, −1,848], −37.9%
[artifacts/i24_sweep_summary.json].

**FollowerStopper at its literature constants costs throughput on this
replica at every penetration and compliance level, and the cost grows with
penetration.** One vehicle in a hundred costs 5% of throughput. The
smoothing is real and monotone too: σ_v falls 24% at 1%, 59% at 5% and 74%
at 20% with full compliance. The pair is the result, not either half
[I24_SWEEP.md, headline and "What it means"]. Figure 8 shows it.

The mechanism, as the source reads it: demand at the entry sits at the
calibrated population's capacity. A controlled vehicle that holds a
larger-than-equilibrium gap and never exceeds the platoon's recent mean
speed is a moving capacity drop, and the humans behind it queue. Lane changes
rise from 1.25 to 2.32 per vehicle-km at 20% as drivers overtake the slow
vehicles [I24_SWEEP.md, Mechanism]. The source reads this as the "gap
exploitation" that version 1 of the project asserted without vehicles or
lanes, now measured in the microscopic tier [I24_SWEEP.md, Mechanism;
CLAUDE.md §12 item 2]; on this unvalidated replica it is a measured
lane-change rate, not a validated behaviour. The queue reaches the insertion
buffer, so the corridor-level numbers include the buffer's rejections
[I24_SWEEP.md, probe section]. Magnitudes will move if
the replica ever passes its criteria. The source argues the sign will not,
because the mechanism is the gap policy against a capacity-bound demand
[I24_SWEEP.md]; that is an argument, not a measurement on a validated
corridor.

### 6.7 A headway cap is not the lever

A capacity-aware FollowerStopper releases its command toward the leader's
speed beyond a time-headway cap. A single-seed probe suggested the cap halves
the throughput cost; twenty seeds do not reproduce it:

| Configuration (5% / 100%) | Throughput [veh/h] | vs baseline | Mean travel time vs baseline | σ_v vs baseline | Fuel vs baseline |
|---|---|---|---|---|---|
| Baseline | 5,839 [5,808, 5,870] | | | | |
| FollowerStopper | 3,626 [3,261, 3,992] | −37.9% [−44, −32] | +102% [+80, +123] | −59.1% | +111% [+81, +142] |
| Cap `h_max` 1.3 s | 3,528 [3,198, 3,858] | −39.6% [−45, −34] | +94% [+76, +112] | −58.4% | +112% [+87, +138] |
| Cap `h_max` 1.5 s | 3,517 [3,139, 3,896] | −39.8% [−46, −33] | +110% [+83, +136] | −59.0% | +123% [+88, +158] |
| Cap `h_max` 1.7 s | 3,758 [3,388, 4,128] | −35.6% [−42, −29] | +97% [+76, +118] | −58.3% | +102% [+71, +132] |
| Cap `h_max` 2.0 s | 3,705 [3,363, 4,048] | −36.5% [−42, −31] | +110% [+79, +141] | −58.9% | +106% [+77, +135] |

*Source: [I24_SWEEP.md, headway-cap sweep; artifacts/i24_cap_sweep_summary.json];
unvalidated replica, 20 seeds each.* Every cap value costs what
FollowerStopper costs, within intervals about ±6 points wide, and buys the
same smoothing. The differences are not ordered with the cap. The cost is
made inside the cap, where the capacity-aware controller is FollowerStopper
by construction. A controller that costs less here has to change what it
does at short gaps [I24_SWEEP.md]. The single-seed probe stays in its
artifact as what one seed showed [artifacts/i24_controller_probe.json] and is
not a result.

### 6.8 Ramp metering and speed limits on the unvalidated replica

This grid runs on a different arm from §6.6: the flow-share family's fitted
level with fitted ramps (config `0cddf2002979`), which scores 5 of 7 rows
like the others and is not validated [I24_STRATEGIES.md; I24_VALIDATION.md
§0.10]. ALINEA meters both entrances at 29.2 veh/km/lane, the critical
density implied by the capacity-scaled population's equilibrium capacity.
VSL runs a threshold ladder on 1 km gantry segments at its defaults
[I24_STRATEGIES.md, setup].

| Cell (paired vs baseline) | Throughput | Mean travel time | σ_v spatial | Fuel | Wave count | Wave amplitude |
|---|---|---|---|---|---|---|
| Baseline means | 5,671 veh/h | 575 s | 5.58 m/s | 89.6 ml/veh-km | 10.25 | 7.00 m/s |
| VSL alone | −7.4% | +7.9% | −19.2% | +16.3% | +68.3% | −24.5% |
| ALINEA alone | −5.8% | −33.4% | −11.7% | −15.6% | +326.8% | +26.8% |
| FollowerStopper 10% alone | −49.6% | +124.7% | −67.7% | +208.0% | −12.2% (n.r.) | −24.2% |
| FollowerStopper 10% + ALINEA | −28.9% | +26.7% | −54.8% | +61.7% | +378.0% | −28.4% |
| FollowerStopper 10% + VSL | −53.3% | +154.4% | −74.1% | +238.3% | −46.3% | −31.2% |

*Source: [I24_STRATEGIES.md, "the six-cell grid is complete";
artifacts/sweep_i24_strategies_summary.json]; 20 seeds per cell; resolved
unless marked.*

- ALINEA alone is the only cell that improves travel time and fuel. Its
  travel time excludes the wait at the ramp meter, because the travel-time
  span is the mainline and the stop line lies upstream of it. It also turns a
  few long waves into many short, larger ones [I24_STRATEGIES.md, ALINEA
  section].
- VSL at its default ladder is a net loss on this replica: it lowers the
  speed spread and the wave amplitude, costs throughput, and raises travel
  time, fuel and the number of waves. It is one ladder at default thresholds; a tuned ladder
  would be a different experiment [I24_STRATEGIES.md, Reading].
- Metering the entrances upstream of 10% FollowerStopper recovers much of
  its cost: throughput −28.9% instead of −49.6%, travel time +26.7% instead of
  +124.7%, with σ_v still −54.8%. The source reads this as the meter keeping
  the mainline below the density at which the controller starves it
  [I24_STRATEGIES.md, complete grid].

One arm, one penetration, default strategy parameters.

### 6.9 The macroscopic tier: which moving-bottleneck constraint

The macroscopic tier represents a controlled vehicle as a moving constraint
on the flux. Two discretizations were compared against microscopic ground
truth: the flux cap `F ← min(F, ρ·v*)`, the discrete analogue of Delle
Monache & Goatin (2014), and a reduced-capacity cap `F ← min(F, α·q_max(v*))`
[CLAUDE.md §5.5]. With FollowerStopper the constraints never bound, because
it never commands below the local equilibrium speed; that null is recorded.
The comparison was redesigned with JAD at 5% / 100% on `corridor_10km`,
whose slow-in does command below the prevailing speed. Each complied
vehicle's recorded trajectory is played back through the macroscopic tier,
so both variants see the same `v*` [M3_US101_VALIDATION.md §5].

| Metric vs micro ground truth (20 replicates) | Flux cap ρ·v* | Reduced capacity |
|---|---|---|
| Speed RMSE [m/s] | 4.37 [3.66, 5.07] | 5.21 [4.03, 6.39] |
| Density RMSE [veh/km] | 7.84 [6.59, 9.09] | 8.23 [7.07, 9.40] |
| Upstream-shadow RMSE [m/s] | 3.88 [3.13, 4.62] | 4.79 [3.61, 5.98] |
| Binding fraction | 0.990 [0.985, 0.995] | 0.991 [0.986, 0.996] |

*Source: [M3_US101_VALIDATION.md §5; artifacts/run_summaries/m3_fluxcap/results.json].*
The paired difference in speed RMSE, reduced capacity minus flux cap, is
+0.84 m/s [0.36, 1.33]. The flux cap is closer on speed and on the upstream
shadow in every one of the 20 replicates; on density the reduced-capacity
variant is closer in 4 of 20 [M3_US101_VALIDATION.md §5]. The absolute errors
of 4–5 m/s are dominated by LWR's model form, which cannot grow emergent
waves. This ranks two discretizations; it does not validate the macroscopic
tier, whose outputs remain screening-only [M3_US101_VALIDATION.md §5].
Figure 9 shows the waviest replicate.

---

## 7. A second corridor: MnDOT I-94 westbound, St. Paul (not reproduced)

### 7.1 Why, and from what data

One corridor cannot support a method claim [FLOWSTATE_DOSSIER.md §12]. The
second corridor was built from public data only, through the same onboarding
path a user would take: a bounding box and bearing give an OpenStreetMap
extract, a one-direction chain, ramps and station positions; a detector
export gives observations and demand [ROADMAP.md, addendum of 2026-09-23;
ONBOARDING_MNDOT.md, header].

MnDOT publishes every Twin Cities freeway loop detector at 30 s resolution
with no registration [ONBOARDING_MNDOT.md §1]. The corridor is I-94
westbound from station S1063 to S97 through east St. Paul. The observations
are nine weekday mornings (Tuesday to Thursday, 1–17 September 2026,
05:30–09:30) at 14 mainline stations, all 100% valid over the span
[ONBOARDING_MNDOT.md §4]. Mass balance between stations showed the ramp
detectors could not be trusted as demand: three read zero on every day and
one "exit" counted a collector–distributor split. Demand is therefore closed
bracket by bracket on the mainline counts, with ramp detectors used only for
shares [ONBOARDING_MNDOT.md §4–5; LESSONS.md row 25]. The diagram is fitted
from per-lane 1-minute samples, because 5-minute station means give a flat
congested branch [ONBOARDING_MNDOT.md §5; LESSONS.md row 26]. No Minnesota
trajectory data exists, so the driver population is the I-24 fit, a stated
model-transfer assumption [ONBOARDING_MNDOT.md §5].

The loop data give the corridor's own wave speed as context, not as a
criterion. From the lag of cross-correlated 30 s speed series between
adjacent stations, 6 of 13 station pairs qualify, with a median of 21.3 km/h
(IQR 18.6–24.2). Leaving out any one date moves the median between 18.4 and
21.6 km/h [ONBOARDING_MNDOT.md §4a, §11].

### 7.2 What was tried

| Round (20 seeds each) | What changed | Departed share of planned | Speed RMSPE | GEH < 5, share of link-hours | Source |
|---|---|---|---|---|---|
| Round 1 | OSM lanes as tagged; entrances join at plain junctions | corridor in free flow | 93% | 17% | [ONBOARDING_MNDOT.md §6; artifacts/mndot_rounds/round1_starved_ramps_validation.json] |
| Round 2 | 250 m acceleration lanes by ramp guessing | 0.406 (0.391–0.421); every seed gridlocked | 90% (one seed scored) | none (one seed scored) | [ONBOARDING_MNDOT.md §6; artifacts/mndot_rounds/round2_gridlock_record.json] |
| Weave, corrected map | two exit splits corrected; weaving-section model | 0.444 (0.225–0.557) | 0.883 | 0.000 | [ONBOARDING_MNDOT.md §10; artifacts/mndot_rounds/weave_2026-09-24/battery_weave_*_scores.txt] |
| Regenerated inputs, sixth weave derivation (VM G) | a faulty detector loop excluded; lane-change settings restored | 0.247 | 0.963 | 0.000 | [ONBOARDING_MNDOT.md §11; artifacts/mndot_rounds/weave_2026-09-24/battery_corrected_inputs_sixth_derivation_68b69f0.json] |
| Exit-side weave rule (VM H) | exiters get priority over the auxiliary lane; a halted exiter gives up its exit | 0.743 | 0.782 (0.760–0.804) | 0.000 | [ONBOARDING_MNDOT.md §11; artifacts/mndot_rounds/weave_2026-09-24/battery_corrected_inputs_exit_side_84cc857.json] |
| Reference configuration (VM U) | exiters prepared upstream, plus a lane-end give-up at every diverge | 0.884 (lowest seed 0.867) | 0.691 (0.688–0.695) | 0.163 (0.138–0.188) | [ONBOARDING_MNDOT.md §11; artifacts/mndot_rounds/weave_2026-09-24/battery_exit_prepare_lane_end_f24ba43.json] |

Round 1 ran free because OpenStreetMap tags the mainline as three lanes
straight through its entrances. SUMO joined each ramp at a plain junction
where ramp vehicles yield to the mainline lane, the entrances were starved
(a session reading of the first seed; its per-ramp counts are not
committed), and the corridor never congested [ONBOARDING_MNDOT.md §5a, §6;
LESSONS.md row 28]. Round 2 gridlocked in every
seed. At the time it was read as the I-24 merge finding compounded at a
corridor whose peaks sit at the discharge the model cannot reach
[ONBOARDING_MNDOT.md §6; LESSONS.md row 29]. The next day's reading put the
queue's origin elsewhere: `netconvert` had compiled a right-hand exit onto an
added left lane and a second exit onto the two leftmost of five lanes, and
through vehicles trapped in exit-only lanes stalled the corridor from
t = 87 s [ONBOARDING_MNDOT.md §9; LESSONS.md row 32]. Every onboarding now
runs a split audit that compares the map's exit side with the compiled lanes
[ONBOARDING_MNDOT.md §9].

The driver population was then scaled toward the corridor's own capacity, as
on I-24 and US-101. The target, 1,907 veh/h/lane, is not reached: the
straight-road capacity plateaus near 1,700 [ONBOARDING_MNDOT.md §8]. The
plateau is a model-form effect. The onboarding path had written SUMO's
extended IDM (EIDM) for this corridor, while the population was fitted as
plain IDM; under plain IDM the same scaling meets the target at T × 0.795,
and EIDM costs about 11% of straight-road capacity at every headway
[ONBOARDING_MNDOT.md §10, "Why the plateau"]. The car-following model of an
onboarded corridor is therefore a calibration decision to make deliberately.

The corridor's reference battery recorded 15 collisions over 20 seeds, although
SUMO's safety checks are on. Fourteen came from the two on-ramps modelled with
the scripted merge. Its forced lane change, made under a SUMO mode that refuses
a change only on an overlap, put a ramp vehicle that had slowed for the end of
its acceleration lane in front of a follower 10–16 m/s faster
[WEAVE_MODEL_PLAN.md, WP-93]. Allowing the forced change only when the gap
exceeds the follower's braking distance removes all 15 collisions over the same
20 seeds, with no resolved change in departures, speed error or link-flow fit
(collisions −0.75 [−1.09, −0.41] a seed, paired) [ONBOARDING_MNDOT.md §11,
VM AG; artifacts/mndot_rounds/weave_2026-09-24/battery_reference_plus_force_guard_1259a9b.json].
The corridor's reference configuration now includes that guard. A merge model
is a path by which a simulator's safety checks can be bypassed, and each one
needs its own collision test.

### 7.3 The weaving sections

The best configuration still carries 3,344 veh/h at station S790 in
06:30–07:30, against 4,911 observed [ONBOARDING_MNDOT.md §11, VM U]. The
corridor is short of capacity at the T.H.52 weaving section, not over-fed:
per-lane counts through the section show it saturating at about 4,000 veh/h
over four lanes, about 1,000 veh/h per lane or 60% of the fleet's
straight-road capacity, and congesting first at its downstream end. The real
section carried 5,137 veh/h at 25.9 m/s at 05:40 and 6,275 veh/h at 06:25
[ONBOARDING_MNDOT.md §11, WP-59 and VM O]. With the calibrated drivers of
§4.6, S790 carries 3,780–4,010 veh/h in 06:30–07:30 in every seed of a 20-seed
battery, against 4,911 observed and 4,567 needed for GEH 5 [I94_RESIDUALS.md
§0; MERGE_MODEL_READINESS.md §5].

The weaving-section model was derived and re-derived on fixtures that
reproduce the section [WEAVE_MODEL_PLAN.md]. Where that work stands
[WEAVE_MODEL_PLAN.md, "where item 1 stands" and its correction]:

- A strict expected-failure test runs the T.H.52 section as the corridor
  compiles it, at the observed early-morning demand. The Highway Capacity
  Manual (edition 7.1, chapter 13) puts that demand at a demand-to-capacity
  ratio of 0.59–0.70; the real section carried it at 26 m/s. The test does
  not pass. With the calibrated drivers of §4.6 the section's no-crossing
  ceiling passes at 10 of 10 seeds, so the test is passable in principle,
  and the section carries 4,361 ± 83 veh/h against 3,873 ± 106 with the old
  drivers; the test still fails at 20 of 20 seeds (macOS)
  [DISCHARGE_CALIBRATION.md §3; artifacts/p3_driver_grid_2026-10-07/th52_ceiling_dc.json;
  artifacts/p3_driver_grid_2026-10-07/th52_section_dc_weave.json]. Where the
  loss sits is §7.6.
- Fifteen derivations of the weave's conflict rules moved the conflict,
  locked the section or read worse. Attribution found no family of the
  model's commands that sets the rate at the entry or at the exit end.
- Real I-24 drivers entering a weave accept critical gaps of 0.46 s ahead and
  0.92 s behind, by Troutbeck's maximum-likelihood estimator on the gaps each
  driver let go by [WEAVE_MODEL_PLAN.md, WP-78; artifacts/i24_critical_gaps.json].
  The coverage of §3.4 is expected to push both estimates up, but they are not
  bounds. A missed vehicle can only lengthen a recorded space gap. The
  estimator also reads the rejected gaps, which can lengthen too or be lost
  into the accepted one. Behind the
  changer, the recorded follower can be a different vehicle at a different
  speed, so a follower time gap can also read short
  [calibration.critical_gap, module docstring, coverage paragraph].
  Thinning complete US-101 data to I-24's coverage did not settle the
  direction: both fitted medians rose, but only one of four shift intervals
  excluded zero (§3.9). On the
  corridor section fixture the model's own entrants read 0.98 s ahead and
  0.66 s behind [WEAVE_MODEL_PLAN.md, correction to "where item 1 stands"].
  Calibrating the model's acceptance to the real values
  lets entrants take real drivers' gaps but not cross faster, and did not
  transfer to the corridor (VM AA: departed 0.865 against 0.884)
  [ONBOARDING_MNDOT.md §11].
- SUMO's own lane-change model cannot be calibrated to those gaps. Its one
  gap parameter, `lcAssertive`, scales the leader-side and follower-side
  secure gaps by the same factor, and the real sides need different factors
  [WEAVE_MODEL_PLAN.md, WP-82].
- The ramp's queue is the entry's breakdown reaching back onto the ramp;
  every step of its head traces to one of the weave model's own speed
  targets, none to SUMO's merge [WEAVE_MODEL_PLAN.md, WP-84]. At the entry,
  the two lanes the weave connects hold each other through those targets:
  88% of lane 1's slow vehicle-steps trace to a target whose partner is in
  lane 0 [WEAVE_MODEL_PLAN.md, WP-86].

**Real crossings against the model's.** At the moment of the change, real
entering crossings differ from the model's in gap and in speed. These are
measurements on a test fixture and on real data. They locate a difference;
they are not a validated model result.

| At the change (weave, entering, medians) | Model, corridor section fixture | NGSIM US-101 | I-24 MOTION |
|---|---|---|---|
| New follower's gap over the normal (`ratio_pop`) | 1.05 [1.01, 1.10] (740) | 0.76 (177) | 0.87 (1,454); not coverage-robust |
| Entrant's gap to its new leader over the normal (`ratio_pop`) | 1.31 (594) | 0.57 (176) | 0.72 (1,591) |
| Entrant's speed minus its new follower's [m/s] | +0.72 (740) | +1.01 (177) | +1.18 (1,488) |
| New leader's speed minus the entrant's [m/s] | +1.37 (594) | −0.40 (176) | −0.83 (1,612) |

*Source: [artifacts/lane_change_relaxation_us101.json;
artifacts/lane_change_relaxation_i24.json; WEAVE_MODEL_PLAN.md, VM AC and
VM AE (4)]. Brackets after a value: sides read, except I-24's `ratio_pop`
rows, which give its finite values (of 1,488 and 1,612 sides read). The
model's values come from fixture runs at seeds 3–7 recorded in
[WEAVE_MODEL_PLAN.md, WP-88 and WP-91]; the interval is a 95% t-interval
over the five seeds' medians. No committed artifact holds the model's
values.*

- Real entrants cross into a short gap. On US-101, complete in coverage,
  the entrant sits at 0.57 of the normal gap behind its new leader and its
  new follower at 0.76. The model's sit at 1.31 and 1.05. On I-24 the
  leader side is short on both references (0.72 of the normal; 0.92 of
  s0 + vT, an upper bound). Its follower side is short only on `ratio_pop`,
  whose reference inflates too (1.14 of s0 + vT; §3.9).
- Real entrants cross faster than their new leader: −0.40 m/s on US-101 and
  −0.83 m/s on I-24 (the leader's speed minus the entrant's). The model's
  entrant crosses 1.37 m/s slower than its new leader. On the follower side
  the model is within 0.5 m/s of both datasets.
- By the rule written before these numbers, the model's crossings differ in
  speed as well as in gap [WEAVE_MODEL_PLAN.md, WP-91, VM AE (4)]. The
  partner speeds are robust to coverage (§3.9), so I-24's count as values.
- A bounded post-crossing gap allowance, set from US-101's short starts,
  moved the model's new follower to 0.93 [0.89, 0.97] of the normal at the
  weave defaults and left the entrant's side at 1.29. It did not improve
  the entry and was not adopted
  [WEAVE_MODEL_PLAN.md, WP-90; CHANGELOG, 2026-09-25].

The fixture results are macOS records; two threshold-sensitive tests land
differently on Linux [WEAVE_MODEL_PLAN.md, WP-85].

### 7.4 The protocol's baseline gate with calibrated drivers (rehearsal, 2026-10-07)

*A rehearsal of the corridor-study protocol (§2.5) on public data. The gate
fails, and nothing in this section is validation.*

The protocol's seeded day split put five of the nine mornings in calibration
(2, 3, 8, 15 and 16 September 2026) and four in validation (1, 9, 10 and 17
September) [artifacts/baseline_gate_mndot_dc.json, `split`]. The four-hour
weave scenario in the reference configuration was run with the Amendment-1
drivers (k = 1, keep-right 0.1; config `db9fbab5fc6e`), 20 seeds, and scored
by the gate. The comparison is the phase-1 reference battery (k = 0, the
reference configuration with the scripted-merge guard of §7.2, config
`b550b46fe751`) on the same day sets [DISCHARGE_CALIBRATION.md §4;
PHASE1_REHEARSAL.md §1].

| Check | Day set | Reference (k = 0) | Calibrated drivers (k = 1) | Target |
|---|---|---|---|---|
| C1 GEH < 5, share of station-hours | calibration | 15.5% | 61.8% [56.5, 67.0] | ≥ 85% |
| C1 | validation | 14.5% | 60.0% [54.0, 66.0] | ≥ 85% |
| C2 GEH < 3 (reported, not gating) | calibration / validation | 12.3% / 11.4% | 45.8% / 42.5% | 100% |
| C3 station-speed RMSPE, 15 min | calibration | 49.7% | 33.9% [31.3, 36.5] | ≤ 15% |
| C3 | validation | 48.7% | 38.8% [34.2, 43.4] | ≤ 15% |
| C4 backward wave speed (`stack`) | calibration | 6.6 km/h | 4.9 km/h (front in 19 of 20) | 14–22 km/h |
| C5 collisions | all | 0 | 0 | 0 |
| C6 bottlenecks | calibration / validation | fail (phantom) / pass | fail (phantom) / pass | — |
| **Gate** | | fail | **fail** | |

*Source: [DISCHARGE_CALIBRATION.md §4; artifacts/baseline_gate_mndot_dc.json;
artifacts/baseline_gate_mndot_i94_wb_stpaul_p1.json;
artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc{,_gated}.json];
brackets: 95% intervals over the 20 replicates.* The gate scores
station-hours and 15-minute station speeds against day-set targets. The
table of §7.2 used the battery's own scoring against the nine-day mean, so
the two tables are not directly comparable.

The calibrated drivers are the largest single improvement the I-94 model has
had: four times the share of station-hours inside GEH 5 on both day sets,
about a third less speed error on the calibration days and a fifth less on
the validation days [DISCHARGE_CALIBRATION.md §4]. The gate still fails on
C1, C3, C4 and C6. The wave speed moved the wrong way, and §7.6 shows that
the check reads the growth of the queue's tail on this corridor. About 4% of
the planned vehicles never departed, 10–11% in two seeds, and 2.1% of the
exiters at the Ruth St weave gave up, above the 2% threshold
[DISCHARGE_CALIBRATION.md §4; I94_COLLAPSE_DIAGNOSIS.md §5]. Those two seeds
are locks (§7.7). The validation-day column is not a clean holdout (§7.5).

The drivers are recommended as the I-94 reference, not adopted. At k = 1 the
mean driver is string-stable at capacity density (§2.2), which CLAUDE.md
§3.1 treats as a requirement; adoption is the owner's decision, with that
trade-off stated [DISCHARGE_CALIBRATION.md §4].

### 7.5 Inputs: a validation-day leak, lane shares and a map defect

**The leak.** The scenario's mainline inflow, all 17 ramp profiles and the
downstream speed schedule were built on 2026-09-24 from the mean of all nine
mornings, ten days before the protocol split them. The driver check that
recommends a passenger speed factor read all nine days too
[I94_CALIBRATION_DAYS.md §0, §1.1]. The validation-day scores of §7.4 (C1
60.0%, C3 38.8%), and the phase-1 reference's (14.5%, 48.7%), therefore score
a model whose inputs include the validation days' own counts at 4/9 weight.
They are not a clean holdout, and the leak is expected to make validation
look closer to calibration than a clean holdout would. Both gates fail on
both day sets by wide margins, so no verdict changes [I94_CALIBRATION_DAYS.md
§2]. Under the committed demand method, the calibration-day inputs differ
from the nine-day ones by 0.5–1.9% in the S1063 inflow's hourly means, by
less than 5% in most hourly ramp values and by up to 10–12% in the largest
ramp moves (the White Bear Ave C-D split at 07:30, 743 → 819 veh/h; the
T.H.120 exit at 06:30, 410 → 362; the Mounds Blvd exit at 08:30, 979 → 876).
The S97 boundary speed changes by 0.62 m/s on average and by up to 2.4 m/s
[I94_CALIBRATION_DAYS.md §1.4]. The low-volume 6th St left exit moves more
in relative terms (61 → 74 veh/h at 06:30, 0 → 12 at 08:30)
[I94_CALIBRATION_DAYS.md §4]. The Amendment-1 grid and
the netfix probe below ran on the same nine-day inputs, against
calibration-day targets [I94_CALIBRATION_DAYS.md §1.2–1.3].

**The correction, built and not yet run.** The inputs were rebuilt from the
five calibration days. Each ramp without a usable detector now takes only its
own segment's mainline difference, as protocol §2.3 says; the earlier method
passed each segment's unexplained remainder on to the next ramp. In the 06:30
hour the Mounds Blvd exit falls from 1,542 to 946 veh/h (observed check 854)
and the Mounds/Kittson entrance from 876 to 451 (its loop 593). Re-run on the
calibration days over the study period, the driver check recommends a
passenger speed factor of 1.3026, inside the measured range 1.094–1.542
[CHANGELOG, 2026-10-07; I94_CALIBRATION_DAYS.md §0, §4–5]. Three scenarios
carry the rebuild: `_dc_cal`, `_dc_cal_sf` with the speed factor, and
`_dc_cal_netfix` with the map fix below. Their 20-seed batteries and gates
(stage `p8_i94_cal`) ran later the same day: the honest inputs fit *worse*
(GEH < 5 on 34–35 % of calibration-day and 31–34 % of validation-day
station-hours against 62 / 60 % with the nine-day inputs; speed RMSPE 42–45 %),
runs lock (lowest realised demand 0.597, 0.761, 0.200) and collisions appear (2
and 4 runs in two arms), because the nine-day inputs' residual carrying had
inflated the Mounds Blvd exit and hidden the T.H.52 weave's capacity shortfall
[I94_CALIBRATION_DAYS.md, Results of stage p8;
`artifacts/baseline_gate_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal{,_netfix,_sf}.json`]. Two further
input rules, the T.H.61 NB ramp from the mainline difference and S792 taken
out of the balance, would change the recorded detector choice after results
were seen; they need a protocol amendment and are not applied
[I94_CALIBRATION_DAYS.md §7].

**Lane shares.** The grid of §4.6 left I-94's per-lane detector shares
7.6–9.9 points off at every pair, against 1.4–4.5 on I-24. The largest part
is a measurement defect: station S791's lanes are numbered in reverse in the
agency's inventory, by three independent signatures, and S791 holds 57–62%
of the pooled squared error. With its order corrected the grid reads
5.0–8.2 points, and the rule's choice is unchanged. A second part is a map
defect: `netconvert`'s ramp guessing turned an option lane at the 6th Street
left exit into an exit-only lane, shifting the through lanes one place left.
The rest is behaviour that no single keep-right value fits, because stations
pull in opposite directions [I94_LANE_SHARES.md, Verdict]. The two numbers
are not directly comparable: I-24's pools four vehicle-time shares over
5.5 km and two hours, while I-94's are 23 station-lane values read at points
[I94_LANE_SHARES.md, Verdict item 5]. The data-quality report now checks
lane order against neighbouring stations: it marks S791 reversed on whole
days and only uncertain on the morning span. Correcting S791's target is the
owner's decision [I94_LANE_SHARES.md, Fixes].

**The map fix, probed.** A 16-run probe of the 35-minute slice (4 seeds)
measured the 6th Street fix. With the calibrated drivers it lowers the
lane-share error from 8.22 to 7.26 points (5.60 to 4.57 with S791 reversed),
with no collision, and the as-built runs reproduce the committed grid exactly
[I94_LANE_SHARES.md, Netfix probe; artifacts/i94_netfix_probe.json].
*Corrected the same day:* the probe's mean S97 discharge rose from 3,028 to
3,760 veh/h, which was first read as a gain. At three of the four seeds the
two networks are level (3,746 against 3,740 veh/h). The whole difference
comes from one as-built run that collapsed (S97 876 veh/h, realised demand
0.857; §7.7). The map fix shows no resolved discharge effect; it stays
justified as the correction of an input defect [I94_LANE_SHARES.md, Netfix
probe, correction; I94_COLLAPSE_DIAGNOSIS.md §5].

### 7.6 The residuals, and where the weaving section loses its capacity

*Diagnostics: a re-reading of the committed battery, fixture runs (macOS) and
one measurement on I-24 MOTION. Nothing in this section is validation.*

**What drives the gate's residuals** [I94_RESIDUALS.md §0, §2–5]. Two causes
split C1's failing station-hours about equally.

- The T.H.52 weave carries too little in the first peak hour: 47% of the
  failing station-hours on calibration days, 54% on validation days. The
  real road pushes about 6,150 veh/h into the weave from 06:30 to 07:30; the
  model's S790 holds 3,780–4,010.
- Ramp inputs do not balance: 48% and 41%. Chiefly, an inflated Mounds Blvd
  exit makes the queued section upstream of it carry too much flow; the
  rebuild of §7.5 addresses it.

About one fifth of C3's error is speed outside any queue: the model drives
82–87 km/h where people drive 103–115 km/h. The rest is the queue, which
reaches too far upstream, is too slow inside and clears at the wrong times.
C4's 4.9 km/h is the speed at which the queue's tail grows upstream, and on
that quantity the model agrees with the detectors (observed 4.6–5.7 km/h).
The observed 19.1 km/h comes from a different estimator, which measures
oscillations inside the queue. Read from one seed's speed contour, the
model's waves inside the queue run at about 14.5–14.8 km/h, at the band's
lower edge and about a quarter slower than observed. That is one seed read
off a figure; twenty could land on either side of 14 km/h. C6's phantom is
the real T.H.52 bottleneck: it is active for at least 30 minutes on 4 of the
5 calibration days, the five-day mean blurs it, and the model makes it
stronger. The cause shares come from the battery's committed 06:30-anchored
hours, not the gate's 06:00 anchoring, and the speeds from one digitised
contour [I94_RESIDUALS.md §1, §8]. By the source's estimates, the allowed
calibration (consistent ramp inputs, the speed factor) cannot pass the gate;
only more T.H.52 throughput at the peak can, and no calibration knob reaches
it [I94_RESIDUALS.md §6].

**The weave's loss on a fixture.** On the T.H.52 section test with the
calibrated drivers (seeds 3–22), the weave carries 4,361 ± 83 veh/h. The same
section with nothing to cross carries 4,826 ± 19: a paired loss of 465 veh/h
[427, 504], against an observed inflow of 4,877 and 4,535 needed for GEH 5.
The gore breaks down 3–5 simulated minutes in and then discharges about
4,430 veh/h [WEAVE_LOSS_DIAGNOSIS.md §0, §2, §3.12;
artifacts/weave_loss_2026-10-07/arms/]. No single rule causes it. Removing,
one at a time, a blind spot in the gap choice, the entrant stranded at the end
of the auxiliary lane, the missing post-crossing relaxation, and SUMO's
lane-change eagerness and keep-right recovers at most 32 veh/h [+8, +57].
Each removal was pre-registered, and three predicted gains failed
[WEAVE_LOSS_DIAGNOSIS.md §4–5]. The loss is the crossing itself: about 1,900
crossings an hour in 305 m, each braking the target lane into a full
car-following gap [WEAVE_LOSS_DIAGNOSIS.md §5]. Two inputs move it, and
neither had been measured:

- **The anticipation reach**, the weave's `lookahead_m` of 120 m: at 200 m
  the section carries +98 [+55, +141] veh/h, at 300 m +177 [+134, +219]
  [WEAVE_LOSS_DIAGNOSIS.md §4].
- **The crossing volume**, set by the proportional split (§2.7): with a
  quarter fewer crossers the flow criterion passes at 18 of 20 seeds
  [WEAVE_LOSS_DIAGNOSIS.md §3.11].

Choosing either value because it passes the test is the tuning the protocol
forbids, so each was to be measured or bounded first
[WEAVE_LOSS_DIAGNOSIS.md §6.1, §6.3].

**The anticipation reach, measured.** For each entering lane change in the
I-24 MOTION recording, the distance the entrant drove beside the gap it
finally entered was read back from the change, censored where tracks end,
and summarised by a Kaplan–Meier median with bootstrap intervals. The rule
for adopting a value was registered before the measurement ran
[MERGE_ANTICIPATION.md §0, §8]. The primary stratum, the Hickory
Hollow–Bell Road weave at 20 m/s or more, reads 82 m on 233 changes but fails
the interval-width check (67–154 m). The fallback, the weave and the Old
Hickory acceleration lane pooled, reads **125 m [107, 154]** on 858 entering
changes, 67% censored, and passes every check. Rounded to 10 m it is 120 m,
the model's constant, which stays under the registered rule
[MERGE_ANTICIPATION.md, Result; artifacts/merge_anticipation_i24.json]. The
direction of the remaining bias is not known. Half coverage is expected to
make the reach read long (§3.4), but by an unmeasured amount and not as a
bound; censoring at the zone start, where ramp tracks begin, may make it read
short. The sensitivity built for the second, changes at least 200 m past the
zone start, reads longer: 134 m [110, 170] on the fallback stratum, and the
run without fragment bridging also reads 134 m [107, 156]. Either would
round to 130 m [MERGE_ANTICIPATION.md §5, §10;
artifacts/merge_anticipation_i24.json, `sensitivities`, `limitations`]. So
the stratum's true median may be somewhat longer than measured; its point
estimates run from 125 to 134 m. No interval of the selected stratum, with or
without these sensitivities, reaches 200 m (the primary stratum's 200-m
sensitivity, on 90 changes, identifies no upper end). I-24's entrants
therefore do not support a reach of 200–300 m, and that route to the weave's
capacity is closed on this evidence [MERGE_ANTICIPATION.md, Result;
WEAVE_LOSS_DIAGNOSIS.md §9].

**The crossing share, bounded.** The movement is US 52 northbound entering
I-94 westbound and leaving 305 m later at exit 242B. No count, published
figure or agency figure gives the share of entrants that do so. MnDOT's
2022–2024 study of this interchange names the weave as a key problem, but
its final report is available only on request [TH52_CROSSING_SHARE.md §0,
§3]. The proportional split gives 0.29 in the fixture's window and 0.18 in
the peak hour. An exploratory reading of the corridor's own counts, after a
1999 MnDOT method (Kwon 1999), leans above it: 0.58 [0.44, 0.71] in its
least-biased form. But it moves with the specification from 0.26 to 0.96, is
biased upward by count error and was not pre-registered, so it is not a
measurement [TH52_CROSSING_SHARE.md §4]. The working range is from the
proportional split to 0.70, an assumed bound [TH52_CROSSING_SHARE.md §5].
The section test was run across it with the key of §2.7:

| Ramp-to-ramp share | Planned crossers [veh/h] | Exit-end flow [veh/h] | vs proportional, paired [95%] | GEH < 5 | Station speed > 20 m/s | Whole test |
|---|---|---|---|---|---|---|
| proportional (0.295 realised) | 1,916 | 4,361 ± 83 | — | 1 / 20 | 0 / 20 | 0 / 20 |
| 0.40 | 1,657 | 4,527 ± 106 | +166 [+113, +220] | 9 / 20 | 0 / 20 | 0 / 20 |
| 0.50 | 1,411 | 4,713 ± 87 | +353 [+301, +404] | 20 / 20 | 1 / 20 | 1 / 20 |
| 0.60 | 1,171 | 4,791 ± 66 | +430 [+389, +472] | 20 / 20 | 2 / 20 | 2 / 20 |
| 0.70 | 925 | 4,819 ± 34 | +458 [+417, +499] | 20 / 20 | 6 / 20 | 6 / 20 |

*Source: [TH52_CROSSING_SHARE.md §10.3;
artifacts/th52_crossing_share_2026-10-07/summary.json and its per-share
files]; calibrated I-94 drivers, seeds 3–22, macOS; no collision or lock at
any share.* The flow verdict flips between 0.40 and 0.50, inside the bounded
range. The test as a whole fails at every share, because station speed lags
flow. This is a sensitivity, not a calibration: no share is chosen from it,
and the model keeps the proportional split. Until the share is measured, the
T.H.52 shortfall cannot be read as a merge-model finding
[TH52_CROSSING_SHARE.md §10.4–10.5]. Protocol Amendment 3, proposed and not
adopted, would carry the share as an uncertain input over this range, judge
the gate at the proportional split and report the range beside it. Its
routes to a calibrated share are MnDOT's report, a pre-registered count
estimate, or a count at the gore [FRISCO_PROTOCOL.md, Amendment 3]. The Ruth
St weave's split has not been bounded at all [FRISCO_PROTOCOL.md,
Amendment 3, item 3].

**W1, a give-up for a stranded entrant.** One physical fix was
pre-registered with fixture criteria: an entrant halted at the end of the
auxiliary lane, with no change to request, takes the exit, the mirror of the
existing exit give-up. It does what it was built for: stranded-entrant time
falls from 18.9 to 3.8 s per run and the flow rises by 29.0 veh/h [9.7,
48.3], with no collision. It recovers about 6% of the loss, as expected of a
fidelity fix. It fails two of its five criteria, both on how often it fires
on small entrances: 1.04% of departures at one seed against a 1% cap, and
above 1% in 8 of 37 grid runs, all at Ruth St. By its rule it stays off
[WEAVE_LOSS_DIAGNOSIS.md §8.3–8.5; artifacts/weave_loss_2026-10-07/w1/].

### 7.7 Locks: a failure the batteries did not report

*Diagnostics: the run-end files of committed batteries re-read, and fixture
runs. No simulation was run for the reading.*

Three of the 20 four-hour replicates with the calibrated drivers (§7.4) end
in a permanent standstill at a weaving section's exit gore: one at T.H.52 and
two at Ruth St, all late in the run. At the front of each, an entrant bound
through stands within 0.1 m of the end of the auxiliary lane, which leads
only to the exit. The exit-bound vehicle at the front of each through lane
cannot get into that stopped lane, and the road past the gore is empty.
Nothing releases any of them: the weave's exit give-up acts only on an
exiter within 5 m of the gore, W1 is off, the network's lane-end give-up
skips weaving sections, and teleporting is off [I94_COLLAPSE_DIAGNOSIS.md §0,
§2–3]. The netfix probe's collapsed slice (§7.5) is very probably the same
state at T.H.52; that is inferred from its counters, because its vehicle
table was not archived [I94_COLLAPSE_DIAGNOSIS.md §0, §8].

| Set | Locked | 95% interval (Clopper–Pearson) |
|---|---|---|
| Four hours, calibrated drivers (k = 1), 20 seeds | 3 of 20 | 3–38% |
| Four hours, phase-1 reference (k = 0), same seeds and map | 0 of 20 | 0–17% |
| 35-minute probe slices, k = 1 | 1 of 8 | 0.3–53% |
| 35-minute probe slices, k = 0 | 0 of 8 | 0–37% |

*Source: [I94_COLLAPSE_DIAGNOSIS.md §5, §10]; read from the batteries'
per-replicate run files, which are in the stage archives and not committed.*
The difference suggests that the drivers matter, but it is not resolved
(Fisher p = 0.23 on the four-hour runs). Whether the 6th Street map defect
plays a part is not known: two of the three locks are 4 km upstream of it
[I94_COLLAPSE_DIAGNOSIS.md §0].

**The batteries did not name them.** The battery's summary read "backlog: 4%
of planned vehicles never departed", flagged no starved ramp and recorded
zero collisions. The protocol's own no-lock rule (no seed's departed share
below 0.8 of the median) passes every lock seen, because a lock that forms
late costs only 0–7 points of departed share. Plain mean travel time is
blind to a lock and can even improve, since trapped vehicles never finish:
one locked replicate has the second-lowest mean travel time of the 20
(496.7 s, against 435.1 s for the lowest, an unlocked replicate, and a
battery mean of 572.6 s)
[artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc.json,
`per_seed[].metrics.mean_tt_s`]. The three locked replicates are the
battery's three worst for failed station-hours,
and they widen its intervals more than they move its means
[I94_COLLAPSE_DIAGNOSIS.md §5]. They stay in every criterion; dropping them
would break CLAUDE.md §0.6.

**Detection.** The detector of §2.7, run on the step-3 replicates, finds
exactly these three locks and no other (3 of 20, 15%; Clopper–Pearson
3.2–37.9%). On 25 local I-24 runs it flags exactly the three sublane-model
probes recorded as locking (§5.5) and none of the other 22
[I94_COLLAPSE_DIAGNOSIS.md §10; CHANGELOG, 2026-10-07]. Neither of its
readers has yet run on an I-94 battery that kept its space-time bins
[I94_COLLAPSE_DIAGNOSIS.md §10, limits].

**W1b, a release after a dwell.** W1 removes the vehicle at the front of
every lock seen, but it also fired on ordinary stands. W1b, registered before
any code or run, fires only after an unbroken 60-s stand at the lane's end.
Sixty seconds is the smallest whole minute above every ordinary stand of the
T.H.52 section test's reference (longest 50.5 s). The three corridor locks
had stood at least 13.6, 42.5 and 52.5 minutes when their four-hour runs
ended at 09:30, so 60 s lies far below every lock on record
[WEAVE_LOSS_DIAGNOSIS.md §10.1–10.3; I94_COLLAPSE_DIAGNOSIS.md §10]. On 132
paired fixture runs it passes all seven registered criteria. It waits
exactly 60 s before each of its 7
releases; each of the 126 runs in which it never fires is byte-identical to
its reference; T.H.52 flow is unchanged; releases are 0.09–0.14% of entrance
departures pooled per section; and there is no collision and no new lock.
One fixture locked in exactly the corridor's state: Ruth St, seed 15,
calibrated drivers, the entrant standing 971.5 s to the run's end, zero
discharge from minute 12. W1b released it after 60 s, and the run then
departed 1,014 of 1,014 vehicles against 673 [WEAVE_LOSS_DIAGNOSIS.md
§10.9–10.10; artifacts/weave_loss_2026-10-07/w1b/criteria.json;
artifacts/weave_loss_2026-10-07/w1b/releases.json].

Three cautions. The pass rests on one lock in 132 runs, so it shows the
mechanism, not a rate. The other five releases ended ordinary Ruth St stands
of 61.5–68.5 s that would have cleared by themselves, so 60 s does not
separate ordinary waits from locks at Ruth St. And whether drivers in the
field take the exit after a minute at the end of an exit-only lane is not
measured; the rule releases a model state that has no field counterpart
[WEAVE_LOSS_DIAGNOSIS.md §10.9, §10.12]. The key stays off. Its corridor round
(stage `p9_i94_w1b`, about $1.9) has not been launched, and adoption is the
owner's decision [WEAVE_LOSS_DIAGNOSIS.md §10.11; CHANGELOG, 2026-10-07].

**The I-94 corridor is not reproduced, and no controller or strategy result
is reported for it** [ONBOARDING_MNDOT.md §6, §11]. *(Corrected 2026-10-07:
this sentence said no controller or strategy sweep was run on it. Strategy
runs were made on 2026-10-04 to rehearse the protocol's tools; they are
labelled rehearsals and state no verdict [I94_CALIBRATION_DAYS.md §1.3, §2].
The protocol's gate, which a strategy result needs, fails (§7.4)
[FRISCO_PROTOCOL.md §6].)*

---

## 8. Discussion

### 8.1 What reproduced and what did not

| Setting | Reproduced | Not reproduced |
|---|---|---|
| Ring, 22 vehicles on 230 m | emergence and single-vehicle dampening, 20 of 20 seeds | — |
| US-101, 640 m, with measured boundary | congestion propagating from an imposed downstream state; backward waves in 20 of 20 replicates (standard detector; none with the criterion's stack detector) | flows, speeds, wave speed (1 PASS / 5 FAIL) |
| I-24, 3.4 miles, four demand arms | the stop-and-go pattern from 2.2 km on; wave speed in band under the criterion's detector | link flows (17–20% of link-hours under GEH 5); segment speeds (34–36% RMSPE) |
| I-24, drivers' acceleration calibrated (calibration, one morning) | merge discharge about a quarter closer (about 6,030 of 6,630 veh/h) | link flows (25.0–25.7%); the wave row is lost; a refit holds 8% of the demand off the network (§5.7) |
| I-94 WB, St. Paul | — | the corridor (departed 0.884, RMSPE 0.691, GEH 16%, reference configuration) |
| I-94 WB, protocol gate, calibrated drivers (rehearsal) | the queue tail's growth speed (a diagnostic reading: 4.9 against 4.6–5.7 km/h observed; §7.6) | the gate: GEH < 5 on 61.8% / 60.0% of station-hours, 15-min RMSPE 33.9% / 38.8%, wave speed, bottleneck timing (§7.4) |

*Sources: §5, §7.* The failures are informative because each has a stated
cause and an artifact behind it. On I-24 the residual is one merge, which
neither calibrated drivers nor a measured merge model discharges at the
recorded rate. On I-94 it is a weaving section, whose fixture loss turns on
an unmeasured crossing share (§7.6), and three of its 20 calibrated
replicates end in a permanent lock at a weaving gore (§7.7). On US-101 it is
a missing on-ramp and a site shorter than a wave.

### 8.2 Merging is where the simulator falls short

On I-24 the capacity-scaled population passes 83% of its straight-road
capacity through the Old Hickory merge (5,880 of 7,100 veh/h), where the
recording sustains 6,630 veh/h, 93% of the same figure
[I24_VALIDATION.md §0.12]. A population refitted on the merge zone alone is
more conservative, not less [I24_VALIDATION.md §0.12]. From that we
concluded that the missing discharge is not in car following but in the
merging process: gap acceptance and cooperation during the lane change. We
placed the I-94 weave's loss in SUMO's lane-change model together with our
own weave rules (§7.3), and named a merge model built on the measured gaps
as the next step [I24_VALIDATION.md §0.12].

*Corrected 2026-10-07.* Each of those readings was too strong.

- **Part of the I-24 shortfall is in car following.** A fixture diagnosis
  found the model's capacity drop normal and its merge discharge level low,
  set by the population's mean maximum acceleration, which no step had
  calibrated. Raising it to the top of its measured range closes about a
  quarter of the I-24 gap and then plateaus near 6,030 veh/h, and a demand
  refit shows the plateau is a discharge shortfall, not a demand limit
  (§4.6, §5.7) [DISCHARGE_CALIBRATION.md §1, §4]. The headway parameters
  remain ruled out (§5.5).
- **A merge model built on the measured gaps did not supply the rest.** The
  `measured` model (§2.7) was built on the I-24 critical gaps, speed matching
  and post-crossing relaxation. It fails its own self-check against the
  measurements at every amendment with the old drivers; the self-check has
  not been run with the calibrated ones. On I-24, with the old drivers, it
  moved the merge by +46 / +54 veh/h on one seed, inside one seed-to-seed
  standard deviation, and on the
  T.H.52 section with the calibrated drivers it carries 52 veh/h less than
  the weave [−96, −9]. Its pre-registered go/no-go criteria read NO-GO. The
  recorded cause is structural: SUMO brakes an entrant for the end of its
  lane and the runner can only lower speeds, so an entrant cannot be brought
  up to the target lane's speed before it changes [MERGE_MODEL_READINESS.md
  §0, §2–3; artifacts/i24_merge_experiment_measured.json;
  artifacts/p3_driver_grid_2026-10-07/th52_section_dc_measured.json;
  artifacts/p3_driver_grid_2026-10-07/th52_section_dc_weave.json].
- **The I-94 weave's fixture loss is in no single rule.** On I-94 a weaving
  section runs at about 60% of straight-road lane capacity in the reference
  configuration [ONBOARDING_MNDOT.md §11]. On the section fixture with the
  calibrated drivers, removing the suspected rules one at a time recovers at
  most 32 of the 465 veh/h lost. The loss is set by the crossing itself, and
  the flow verdict flips inside the bounded range of an unmeasured input, the
  ramp-to-ramp share (§7.6). Until that share is measured, the T.H.52
  shortfall cannot be attributed to the merge model. The other candidate
  input, the anticipation reach, was measured on I-24 and rounds to the
  model's constant [WEAVE_LOSS_DIAGNOSIS.md §5; TH52_CROSSING_SHARE.md §10.4;
  MERGE_ANTICIPATION.md, Result].

What remains on I-24, about 600 veh/h, has no established mechanism.

Two cautions carry over from §3.9 and §7.3. Under the instrument's coverage,
I-24's gaps and refusal shares are one-sided and the direction of its
critical gaps is undetermined; the relative speeds at a crossing are values.
And the model's crossings differ from real ones in speed as well as in gap.

### 8.3 Behaviour parameters set on independent observables are not tuning

Several modelling choices had to be made explicit. `lc_strategic` and
`lc_keep_right` were set from stall counts and lane shares before any
criterion was evaluated [I24_VALIDATION.md §1]. The capacity step targets a
field capacity, not a criterion [I24_CAPACITY.md §4]. The demand and ramp
steps fit the first hour and score the second without fitting it
[I24_CAPACITY.md §5, §7]. The speed criterion keeps the 5-min resolution it
was given before the result was known, even though at that resolution no
ensemble can pass [I24_VALIDATION.md §0.5(a)]. The merge candidates of the
sixth round each had a pre-registered threshold [I24_VALIDATION.md §0.9,
§0.11]. The lane-change grid whose objective disagreed with the speed fit was
not adopted [I24_CAPACITY.md §8]. The one decision that changed a verdict,
naming the stack detector, was made on a synthetic benchmark and is reported
next to the standard-detector reading (§5.4).

The 2026-10-07 work kept the same rule. Every new setting was registered
with its range, targets and selection rule before it ran: the drivers'
acceleration and keep-right (Amendment 1); the wave and backlog constraints
of Amendment 2, whose backlog clause was written before any of its results
was available or read, though after its first battery had been written on
the cloud machine (§2.4); the
anticipation reach, adopted only from a measurement by a rule fixed in
advance; and W1 and W1b, each with fixture criteria, of which W1 failed two
and stays off (§2.4, §7.6–7.7). Values that would have helped were refused:
an anticipation reach of 200–300 m, and any ramp-to-ramp share chosen from
its sensitivity [WEAVE_LOSS_DIAGNOSIS.md §6.1; TH52_CROSSING_SHARE.md
§10.5]. Two lessons came with it. A fit objective can improve without the
model improving: a speed-only demand refit lowered RMSPE by holding vehicles
off the network (§5.7). And one calibration recipe does not serve every
corridor: the same driver shift is the largest single improvement the I-94
model has had and costs I-24 its emergent waves. On both corridors the shift
is held back, at least in part, by a requirement that no fit objective sees:
CLAUDE.md §3.1's instability near capacity (§2.2) [DISCHARGE_CALIBRATION.md
§4].

### 8.4 Coverage in camera-trajectory instruments

The I-24 MOTION data made this study possible: 17,652 car-following episodes,
seven times NGSIM, with a better holdout fit [I24_DATA.md §5]. Its speeds and
wave speeds are sound. Its counts are not traffic counts. In the peak about
half the vehicle-time is missing, unevenly by lane and by section (§3.4).
Every flow-based step of this study had to be designed around that: the
demand arms, the coverage-corrected flow target, the conservative capacity
target, and the lower-bound labels on the fitted diagram. The remedy is an
independent count. The same testbed carries radar detectors with 30 s
volumes, and those counts would replace both the tracked demand and the
observed side of GEH [I24_DATA.md §4; ROADMAP.md §6 item 7].

### 8.5 Smoothing near capacity is paid for in capacity

On a synthetic single-lane corridor with spare capacity, gap-keeping
smoothing had no resolved throughput cost (§6.1). On a five-lane 640 m site
at saturation it cost 0.7–2.7% of throughput and 1–3% fuel (§6.5). On the
I-24 replica, where demand sits at the calibrated capacity, it cost 5% at 1%
penetration and 38% at 5% (§6.6). A headway cap did not change that (§6.7);
metering the entrances upstream of the controller recovered much of it on one
arm at one penetration (§6.8). The consistent reading is that a controlled
vehicle holding a larger gap is a moving capacity drop wherever capacity
binds. A deployment argument has to be made per corridor, with that
corridor's calibration [US101_PENETRATION.md, honest summary; I24_SWEEP.md].
On an unvalidated replica, relative comparisons on the same corridor with the
same seeds are supportable; absolute predictions for a roadway are not
[FLOWSTATE_DOSSIER.md §12].

### 8.6 Self-correction as part of the method

Most corrections in the project's ledger came from reading the primary source
or the raw file instead of a summary of it: the PI specification, the I-24
schema, the fragment structure. The rest came from refusing a number that
violated physics: an LWR model dissipating waves it cannot form, fragment
counts too low for the observed speeds, a lane crawling behind SUMO's
keep-right obligation [LESSONS.md, closing paragraph]. *(Corrected
2026-10-07: this sentence said US freeways have no keep-right rule. They do:
Tennessee (Code §55-8-115), Minnesota (Stat. §169.18 subd. 10(b)) and Texas
(Transp. Code §545.051(b)) require slower traffic to keep right. The
lane-share fix stands; its stated reason was wrong, and the setting has since
been recalibrated (§4.4) [DISCHARGE_CALIBRATION.md §2; LESSONS.md row 10].)*
Two defects in the metrics themselves were found by audit; the I-24 and
US-101 artifacts were re-simulated with their original seeds; the
synthetic-corridor experiments, whose paired contrasts the warm-up defect
does not affect, were not (§2.6). We report these because they are the
evidence that the remaining numbers were checked.

The work of 2026-10-07 added three corrections, each found by a review of the
same day's results and each recorded beside the text it corrects: a plateau
read as demand-limited that a demand refit showed was a discharge shortfall
(§5.7); a discharge gain from a map fix that came entirely from one collapsed
run (§7.5); and I-94 inputs built partly from the validation days (§7.5)
[DISCHARGE_CALIBRATION.md §3–4; I94_LANE_SHARES.md, Netfix probe;
I94_CALIBRATION_DAYS.md §0].

### 8.7 What a battery's means can hide

A battery summarises 20 replicates by means and intervals. On I-94, three of
the 20 calibrated replicates ended in a permanent lock, and nothing in the
battery named them (§7.7). A lock that forms late moves the mean departed
share by a few points, leaves the mean travel time unchanged or better, and
shows up mainly as wider intervals [I94_COLLAPSE_DIAGNOSIS.md §5]. The remedy
is reporting, not removal: every run set now carries a lock row beside its
criteria, and every replicate stays in every criterion
[I94_COLLAPSE_DIAGNOSIS.md §7, §10]. We would add a standstill detector to
any microsimulation battery whose merge or weave logic is driven by its own
rules, because in every lock seen here the vehicle at the front was an
entrant under the weave's control, and no rule, the weave's or SUMO's,
released it (§7.7) [I94_COLLAPSE_DIAGNOSIS.md §0].

---

## 9. Limitations

1. **No corridor is validated.** I-24 fails link-flow GEH and segment-speed
   RMSPE; US-101 scores 1 PASS / 5 FAIL; I-94 is not reproduced, and with
   the calibrated drivers it fails the protocol's baseline gate in rehearsal
   (§5, §7).
2. **Coverage.** Every I-24 count, flow and density is a lower bound. The
   corrected demand arms rest on coverage estimates, not on counts (§3.4).
   I-24's lane-change gaps and refusal shares are one-sided, and the size of
   their bias is transferred from one thinned dataset, US-101 (§3.9).
3. **One day, one direction, two hours on I-24.** 30 November 2022,
   westbound, 06:30–08:30; no weather or incident metadata used
   [I24_DATA.md §7; I24_VALIDATION.md §6]. Every I-24 calibration, the
   driver calibration of §4.6 and §5.7 included, is fitted and scored on that
   one morning; there is no holdout day, so I-24 results are calibration, not
   validation [FRISCO_PROTOCOL.md, Amendment 1]. The held-out second hour of
   §4.2–4.3 is a holdout in time on the same morning, not a different day.
4. **3.4 of 4 miles.** The span ends at the Bell Road collector road; the Bell
   Road on-ramp is not modelled and enters only through the observed boundary
   speed [I24_VALIDATION.md §6].
5. **Ramp inputs are fragment counts** with the same coverage bias, and one
   exit fraction is taken inside a weaving section [I24_VALIDATION.md §6].
6. **Fleet.** The canonical arms are passenger-only. The heavy-vehicle arm
   uses a population fitted on 197 episodes, with no capacity step for the
   mixed fleet [I24_DATA.md, heavy-vehicle section].
7. **Fragmented episodes.** Episodes never outlive the shorter of two
   fragments; `v0` is under-excited; a minority of episodes pair a follower
   with the wrong leader when the true leader is untracked within 100 m
   [I24_DATA.md §7].
8. **The wave-speed pass is detector-dependent** and rests on a stack peak in
   7–12 of 20 replicates per arm (§5.4).
9. **Model form.** SUMO's lane-discrete lane-change model under-discharges the
   I-24 merge; with the drivers' acceleration at the top of its measured
   range the merge is still about 600 veh/h short, and a merge model built on
   the measured gaps did not close it (§5.5, §5.7, §8.2). The I-94 weave is
   our own rule set; it does not reach observed capacity, and on the fixture
   its loss turns on an unmeasured input rather than on any single rule
   (§7.3, §7.6). EIDM and IDM differ in capacity by about 11% on the same
   population [ONBOARDING_MNDOT.md §10].
10. **Controllers at default constants.** No controller was tuned for any
    corridor; the results rank default configurations
    [CONTROLLER_COMPARISON.md, Limitations].
11. **Synthetic-corridor results** (§6.1–6.4, §6.9) come from an EIDM fleet
    at default parameters with demand tuned for wave emergence; they are not
    field predictions, and that corridor fails the wave-speed row
    [M3_RESULTS.md §5].
12. **Fuel** comes from SUMO's HBEFA4 model, not validated against measured
    consumption [US101_PENETRATION.md, Limitations].
13. **Coverage of the experiment grid.** JAD was not run on I-24; the US-101
    replica was run at full compliance only; the I-24 strategy grid covers
    one penetration on one arm [JAD_DEFERRAL_RESULTS.md, Limitations;
    ROADMAP.md §5 D3; I24_STRATEGIES.md].
14. **Transferred population on I-94.** No Minnesota trajectories exist; the
    driver population is the I-24 fit [ONBOARDING_MNDOT.md §5].
15. **I-94's validation days are not a clean holdout.** The demand, ramp and
    boundary inputs of the phase-1 and step-3 batteries averaged all nine
    mornings, four of them validation days, and the Amendment-1 grid ran on
    those inputs. The rebuilt calibration-day inputs exist but have not been run
    (§7.5) [I94_CALIBRATION_DAYS.md §2, §6].
16. **Unmeasured ramp-to-ramp shares.** At the T.H.52 weave the share of
    entrants bound for the next exit is an assumed proportional split. No
    count or published figure exists, and the fixture's flow verdict flips
    inside its bounded range. The Ruth St weave's split has not been bounded
    at all (§7.6) [TH52_CROSSING_SHARE.md §10.4; FRISCO_PROTOCOL.md,
    Amendment 3].
17. **String stability under the calibrated drivers.** At mean `a_max`
    + 1 sd the mean driver is stable at capacity density (unstable only from
    39.8 veh/km, against 28.2 at the measured mean), which CLAUDE.md §3.1
    treats as a requirement. Any I-94 result on those drivers carries weaker
    instability near capacity; on I-24 the wave criterion was lost and the
    drivers were not adopted (§2.2, §5.7, §7.4) [DISCHARGE_CALIBRATION.md §4].
    The onsets are recorded in the source document; no artifact holds them.
18. **Locks.** Run sets scored before 2026-10-07 carry no lock record. The
    detector has been reported on step 3's I-94 replicates and on 25 local
    I-24 runs, not on the published I-24 batteries of §5.3. On I-94, 3 of 20
    calibrated four-hour replicates locked (3–38%), from run files that are
    not committed (§7.7) [I94_COLLAPSE_DIAGNOSIS.md §5, §10].
19. **The shifted acceleration's cost in car following is unmeasured.** The
    episode-fit gap RMSE of the population with mean `a_max` + 1 sd was not
    computed, because the episodes are cloud-only data
    [DISCHARGE_CALIBRATION.md §1].
20. **Fixtures and platform.** The weave, crossing-share, W1 and W1b results
    come from one 20-minute fixture of one section at its early-morning
    demand, and from short fixtures at Ruth St, run on macOS. Fixture records
    can differ by platform (two earlier threshold-sensitive tests landed
    differently on Linux, §7.3), and the corridor runs on Linux decide
    [WEAVE_LOSS_DIAGNOSIS.md §1, §7, §10.12; WEAVE_MODEL_PLAN.md, WP-85].

---

## 10. Reproducibility statement

**Code.** The analysis in this draft uses the repository tree at commit
`b41190c`, after release v2.4.0 [CHANGELOG]. The coverage thinning and the
gaps and speeds at the change in §3.9 and §7.3 were measured later, at
commit `2946afc` (artifacts ingested in `55bd1bb`):
`artifacts/coverage_thinning_us101.json` and
`artifacts/lane_change_relaxation_{i24,us101}.json`. The two US-101
artifacts record `code_dirty: true`; that flag is a known false positive,
and the code was the clean snapshot of `2946afc` [WEAVE_MODEL_PLAN.md,
VM AE, provenance]. A release tag for submission, and the public repository
URL, are for the owner to set: [repository URL and tag to be inserted].

The results added on 2026-10-07 (§2.7, §4.6, §5.7, §7.4–7.7) are recorded in
documents committed through `9bf6cc6`, each of which names the code tree it
ran: the Amendment-1 grid at `571b9e4`, the step-3 batteries at `6de4e21`,
the weave-loss diagnosis on a snapshot of `6de4e21`, W1's identity runs
against `daf8826`, W1b's against `84a272e`, and the crossing-share
sensitivity against `9bd17ff` [DISCHARGE_CALIBRATION.md §3–4;
WEAVE_LOSS_DIAGNOSIS.md §1, §8.2, §10.8; TH52_CROSSING_SHARE.md §10.2]. The
fixture results are macOS records. The lock counts and the residual
decomposition were read from per-replicate files in the stage archives, which
are not committed [I94_COLLAPSE_DIAGNOSIS.md, header; I94_RESIDUALS.md §1].

**Software.** Eclipse SUMO and libsumo 1.27.1, pinned because goldens are
per SUMO version [CLAUDE.md §9]; Python 3.12 [M3_RESULTS.md §1]. Commands
run as `uv run --no-sync python scripts/...` from the repository root
[M2_RESULTS.md, header].

**Seeds and hashes.** Replicate seeds come from `spawn_seeds(42, 20)`; the
same list is used in every cell of an experiment. Every run records its seed,
configuration hash, package versions and calibration provenance in
`meta.json` [M3_RESULTS.md §1; M3_US101_VALIDATION.md header]. The
configuration hashes of every table are in the cited artifacts and documents
(for example, the I-24 arms under hash policy v2: tracked `6e09678057bc`,
corrected `e676cdb0453c`, fitted level `43def6306dd6`, fitted level + ramps
`baa746ca199d` [I24_VALIDATION.md §0.1]). Simulations are deterministic per
seed and SUMO version: batteries re-run on different machines reproduced
every criteria value to the digit [I24_VALIDATION.md §0.1, §0.12].

**Scripts per result.**

| Result | Scripts | Artifact |
|---|---|---|
| I-24 extraction, episodes, IDM and FD fits | `scripts/i24_extract.py`, `i24_overview.py`, `i24_extract_episodes.py`, `fit_idm_i24.py`, `fit_fd_i24.py` | `artifacts/idm_i24.json`, `artifacts/fd_i24.json` [I24_DATA.md header] |
| Coverage estimators | `scripts/i24_coverage.py` | `artifacts/i24_coverage.json` |
| Replica build and capacity, demand, ramp steps | `scripts/i24_build_replica.py`, `i24_calibrate_capacity.py`, `i24_fit_demand_scale.py`, `i24_fit_boundary_ramps.py` | `artifacts/idm_i24_capacity.json`, `artifacts/demand_scale_i24_corrected.json`, `artifacts/i24_boundary_ramps_fit.json` |
| I-24 criteria battery | `scripts/i24_validate.py` | `artifacts/i24_validation_*.json`, `docs/reports/i24_replica/` |
| I-24 sweep, cap sweep, strategies | `scripts/i24_penetration_sweep.py`, `i24_penetration_analyze.py`, `i24_cap_sweep.py`, `corridor_sweep.py` | `artifacts/i24_sweep_summary.json`, `artifacts/i24_cap_sweep_summary.json`, `artifacts/sweep_i24_strategies_summary.json` |
| US-101 calibration, validation, penetration, lane changes | `scripts/fit_idm_us101.py`, `fit_fd_us101.py`, `m3_us101_validate.py`, `calibrate_capacity.py`, `fit_demand_scale.py`, `us101_penetration_sweep.py`, `us101_lane_changes.py` | `artifacts/idm_us101*.json`, `artifacts/us101_validation_calibrated.json`, `artifacts/us101_penetration_summary.json`, `artifacts/us101_lane_change_penetration.json` |
| Synthetic corridor sweep, PI, JAD | `scripts/m3_sweep.py`, `m3_analyze_sweep.py`, `pi_retune_experiment.py`, `jad_oracle_experiment.py`, `jad_deferral_experiment.py` | `artifacts/m3_sweep_summary.json`, `artifacts/pi_retune_summary.json`, `artifacts/jad_oracle_summary.json`, `artifacts/jad_deferral_summary.json` |
| Flux-cap comparison | `scripts/m3_fluxcap_compare.py` | `artifacts/run_summaries/m3_fluxcap/results.json` |
| Ring wave speed vs density | `scripts/wave_speed_sitelength.py` | `artifacts/wave_speed_sitelength.json`, `artifacts/wave_speed_sitelength_i24.json` |
| MnDOT onboarding and batteries | `scripts/mndot_fetch.py`, `onboard_corridor.py`, `corridor_demand.py`, `corridor_battery.py` | `artifacts/mndot_rounds/` [ONBOARDING_MNDOT.md §3] |
| Lane-change measures and coverage thinning | `scripts/i24_lane_change_gaps.py`, `i24_critical_gaps.py`, `lane_change_relaxation.py`, `coverage_thinning.py` | `artifacts/i24_lane_change_gaps.json`, `artifacts/i24_critical_gaps.json`, `artifacts/lane_change_relaxation_{i24,us101}.json`, `artifacts/coverage_thinning_us101.json` |
| Driver calibration (Amendments 1–2) and its batteries | `scripts/derive_population.py`, `calibrate_driver_grid.py`, `apply_driver_calibration.py`, `i24_fit_demand_scale.py`, `i24_validate.py`, `corridor_battery.py`, `baseline_gate.py` | `artifacts/idm_i24_capacity_amax_k{0.25,0.5,0.75,1.0}.json`, `artifacts/driver_calibration_{i24,i94}.json`, `artifacts/p3_driver_grid_2026-10-07/`, `artifacts/i24_validation_{flow_speedcal_ref,dc,dc_refit,dck025_refit,dck05_refit}.json`, `artifacts/baseline_gate_mndot_dc.json`, `artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc{,_gated}.json` |
| T.H.52 fixtures: weave loss, W1, W1b, crossing share, measured model | `scripts/merge_model_selfcheck.py` and the harnesses committed beside the artifacts | `artifacts/weave_loss_2026-10-07/`, `artifacts/th52_crossing_share_2026-10-07/`, `artifacts/p3_driver_grid_2026-10-07/th52_*.json` |
| Merge anticipation reach | `scripts/measure_merge_anticipation.py` (`calibration.merge_anticipation`) | `artifacts/merge_anticipation_i24.json` |
| I-94 netfix probe; calibration-day inputs | `scripts/i94_netfix_probe.py`, `i94_calibration_days.py` | `artifacts/i94_netfix_probe.json`, `artifacts/demand_mndot_i94_wb_stpaul_cal.json`, `scenarios/mndot_i94_wb_stpaul_weave_dc_cal{,_sf,_netfix}.yaml` |
| Lock detection; string stability | `validation.locks`, `validation.string_stability` | `per_seed[i].locks` and `zero_locks` in battery artifacts written after 2026-10-07; no committed artifact carries them yet; the string-stability onsets of §2.2 are in DISCHARGE_CALIBRATION.md §4 only |

**Compute.** The large batteries and sweeps ran on self-deleting cloud
machines (n2-standard-32); for example, the re-run of the 500-run sweep
together with the headway-cap sweep took 7 h 32 min of work on one such
machine [CHANGELOG, 2026-09-19]. Per-replicate trajectories are pruned after
scoring (the first seed of each I-24 arm is kept for the figures); the
summaries every table cites are committed [I24_VALIDATION.md header].

**Data access.** I-24 MOTION data require registration and are used under
the I-24 MOTION data-use agreement; published use cites Gloudemans et al.
(2023) [I24_DATA.md header]. The source zip's sha256 prefix
`aa97dd93d2bf250e` is recorded in every I-24 artifact [I24_DATA.md header].
NGSIM US-101 is public (data.transportation.gov, resource `8ect-6jqj`); its
provenance hash is recorded in every US-101 artifact [M2_RESULTS.md §1].
MnDOT loop data are public without registration [ONBOARDING_MNDOT.md §1].

---

## References

Every entry is cited in the text and appears in the project's reference list
[CLAUDE.md §13] or in the source documents named. The source documents give
authors, years, venues and identifiers, not titles; titles and missing
details are marked "[to complete]" and must be checked before submission.

- Cassidy & Bertini (1999). Freeway bottleneck discharge below the pre-queue
  flow. *Transportation Research Part B* 33(1) [DISCHARGE_CALIBRATION.md §1;
  FRISCO_PROTOCOL.md, Amendment 1]. [title to complete]
- Chen, Skabardonis & Varaiya (2004). Active-bottleneck identification.
  *Transportation Research Record* 1867, as summarised in NCDOT report
  2016-10 [FRISCO_PROTOCOL.md §5]. [title to complete; the 5-of-7
  persistence rule is to be checked against the original]
- CIRCLES MegaVanderTest, I-24, November 2022 (BAIR blog, 2025-03-25)
  [CLAUDE.md §13]. [to complete]
- Clopper–Pearson exact binomial interval, used for lock rates
  [I94_COLLAPSE_DIAGNOSIS.md §5]. [to complete]
- Daganzo (1994, 1995). The cell transmission model; supply–demand
  flux [CLAUDE.md §13]. [to complete]
- Delle Monache & Goatin (2014). Moving flux constraint. *Journal
  of Differential Equations* 257 [CLAUDE.md §13]. [to complete]
- Edie. Generalized definitions of flow, density and speed [cited by
  name in M2_RESULTS.md §4 and I24_DATA.md]. [to complete]
- FHWA (2004). Traffic Analysis Toolbox Volume III, FHWA-HRT-04-040
  [CLAUDE.md §7.1, §13].
- FHWA (2019). Traffic Analysis Toolbox Volume III update, FHWA-HOP-18-036
  [CLAUDE.md §7.1, §13].
- Gloudemans et al. (2023). I-24 MOTION testbed. *Transportation Research
  Part C* 155:104311; arXiv:2302.12308 [I24_DATA.md header; CLAUDE.md §13].
  [title to complete]
- He, Liu & Liu (2016). Jam-absorption driving.
  *Transportation Research Part B* [CLAUDE.md §13; jad_derivation.md].
  [to complete]
- Highway Capacity Manual, Edition 7.1, Chapter 13 [WEAVE_MODEL_PLAN.md,
  WP-76]. Its simple weaving-volume estimate (Equations 13-2 to 13-6) is the
  proportional split of §2.7 [TH52_CROSSING_SHARE.md §3, E8]. [to complete]
- Ji et al. (2024). Virtual-trajectory tools for I-24 MOTION.
  arXiv:2311.10888 [I24_DATA.md §2; CLAUDE.md §13]. [title to complete]
- Kaplan–Meier product-limit estimator, used for the censored anticipation
  reach [MERGE_ANTICIPATION.md §0, §4]. [to complete]
- Keep-right statutes: Tennessee Code §55-8-115; Minnesota Statutes §169.18
  subd. 10(b); Texas Transportation Code §545.051(b)
  [DISCHARGE_CALIBRATION.md §2].
- Kesting & Treiber (2008). Car-following calibration methodology
  [CLAUDE.md §13]. [to complete]
- Kwon (1999). *Estimation of the Capacity in Freeway Weaving Areas for
  Traffic Management and Operations*. MnDOT report MN/RC-1999-40
  [TH52_CROSSING_SHARE.md §3, E7].
- Lighthill & Whitham (1955); Richards (1956). The LWR
  model [CLAUDE.md §13]. [to complete]
- MnDOT. *Hwy 52 Lafayette Bridge, I-94 and I-35E in St. Paul*, study
  January 2022 – October 2024; final report available on request
  [TH52_CROSSING_SHARE.md §3, E1].
- Montanino & Punzo. Reconstructed NGSIM trajectories [CLAUDE.md §13;
  ROADMAP.md §6]. [to complete]
- Newell. First-order car-following theory; wave speed `(s0 + L)/T`
  [cited by name in WAVE_SPEED_DIAGNOSIS.md]. [to complete]
- NGSIM US-101 data, data.transportation.gov resource `8ect-6jqj`
  [M2_RESULTS.md §1].
- Stern et al. (2018). *Transportation Research Part C* 89:205–221;
  arXiv:1705.01693 [CLAUDE.md §13; PI_CONTROLLER_FIX.md §2]. [title to
  complete]
- Sugiyama et al. (2008). *New Journal of Physics* 10:033001 [CLAUDE.md
  §13]. [title to complete]
- Theil–Sen line estimator, used for front fits [M3_RESULTS.md §1;
  CONTRACTS.md §4]. [to complete]
- Treiber, Hennecke & Helbing (2000). The Intelligent Driver
  Model [CLAUDE.md §13]. [to complete]
- Treiber & Kesting (2013). *Traffic Flow Dynamics* [CLAUDE.md §13].
  [to complete]
- Troutbeck's maximum-likelihood critical-gap estimator [WEAVE_MODEL_PLAN.md,
  WP-78]. [to complete]


---

## Appendix A. Claims ledger

Each headline number, its value with the interval where one exists, the
number of seeds behind it, and the committed file it traces to. "—" in the
seeds column means the number is a data measurement, not a simulation.
Single-seed probes are marked; none is a headline result on its own.

| # | Claim | Value (95% CI where available) | Seeds | Source |
|---|---|---|---|---|
| 1 | I-24 westbound export size | 576,511 documents → 42,764,894 rows at 5 Hz | — | [I24_DATA.md §1] |
| 2 | Fragment length | median 9.9 s / 117 m; 10.9% last ≥ 30 s | — | [I24_DATA.md §2] |
| 3 | Apparent tracking coverage, 06:30–08:30 | 0.52–0.66 per 15 min (first population; 0.48–0.61 on the capacity-calibrated population) | — | [I24_DATA.md §4; artifacts/i24_coverage_lane5.json, `equilibrium_legacy_fleet` and `equilibrium_capacity_fleet`] |
| 4 | Recommended (section gap mixture) coverage | 0.559–0.674 over the eight 15-min windows 06:30–08:15 (0.559–0.656 over the five tabulated in §3.4) | — | [I24_DATA.md, coverage revisited; artifacts/i24_coverage.json] |
| 5 | Corrected peak inflow under the recommended coverage | 1,786 veh/h/lane (1,935 under the equilibrium method) | — | [I24_DATA.md, coverage revisited] |
| 6 | I-24 car-following episodes and holdout fit | 17,652 episodes; holdout gap RMSE 5.29 m on 5,296 episodes | — | [artifacts/idm_i24.json; I24_DATA.md §5] |
| 7 | NGSIM US-101 holdout fit | 2,452 episodes; 6.44 m on 736 | — | [artifacts/idm_us101.json; M2_RESULTS.md §3] |
| 8 | I-24 congested wave speed w | 16.1 km/h [15.7, 16.5] | 200 bootstrap resamples | [artifacts/fd_i24.json] |
| 9 | I-24 tracked capacity (lower bound) | 1,780 veh/h/lane [1,775, 1,787] | 200 bootstrap resamples | [artifacts/fd_i24.json] |
| 10 | Fitted population's straight-road capacity before scaling | about 1,650 veh/h/lane | 3 seeds per demand level | [I24_CAPACITY.md §1; artifacts/i24_capacity_experiment.json] |
| 11 | Capacity step | f* = 0.875, mean T 1.511 → 1.322 s; gap RMSE 5.313 → 5.294 m | 2 seeds per grid point | [artifacts/idm_i24_capacity.json; I24_CAPACITY.md §4] |
| 12 | Demand level | s = 0.85; RMSPE 0.320 fitted hour, 0.396 held-out | 1 seed per point | [artifacts/demand_scale_i24_corrected.json] |
| 13 | I-24 criteria, congested arms | 5 PASS / 2 FAIL each; tracked arm 4 / 3 | 20 per arm | [artifacts/i24_validation_{tracked,corrected,speedcal,ramps}.json; I24_VALIDATION.md §0.1] |
| 14 | Link-flow GEH < 5 share | 16.7% / 18.8% / 20.1% (tracked 0.7%); threshold 85% | 20 per arm | same |
| 15 | Segment-speed RMSPE, 5 min | 33.7% / 35.9% / 34.8% (tracked 187.8%); threshold 15% | 20 per arm | same |
| 16 | Stack wave speed | 15.9 / 15.8 / 15.7 km/h (peaks in 9 / 12 / 7 of 20); observed 19.9 (contrast 3.44) | 20 per arm | [I24_VALIDATION.md §0.2] |
| 17 | Standard-detector wave speed | 10.4 / 9.9 / 8.4 km/h; observed 14.2 | 20 per arm | [I24_VALIDATION.md §0.2] |
| 18 | Recording vs its own 15-min moving average | 33.4% RMSPE | — | [I24_VALIDATION.md §0.5(a)] |
| 19 | Flow shortfall at the two peak sections, fitted arm | 12–13% | 20 | [I24_VALIDATION.md §0.5(b)] |
| 20 | Merge discharge, simulated vs recorded | about 5,880 vs 6,630 veh/h | single-seed probes | [I24_VALIDATION.md §0.11; artifacts/i24_merge_experiment_ohlevel.json] |
| 21 | Merge-zone population | 4,193 episodes; holdout 4.53 m; mean T 1.580 s; capacity index 1,720 veh/h/lane | — | [artifacts/idm_i24_merge.json; artifacts/idm_i24_capacity_equilibrium.json] |
| 22 | Ring emergence and dampening | 20 of 20 seeds each | 20 | [artifacts/i24_validation_*.json, ring rows] |
| 23 | Ring wave speed at 80 veh/km | 17.1 km/h (US-101 fleet), 16.4 km/h (I-24 fleet); no CI | 5 per density | [artifacts/wave_speed_sitelength.json; artifacts/wave_speed_sitelength_i24.json] |
| 24 | US-101 criteria | 1 PASS / 5 FAIL in every arm (three measured failures; two ring rows not evaluated by the driver, counted as failing); RMSPE 36.6% with boundary, 27.9% calibrated (35.9% / 28.4% under corrected definitions) | 20 per arm | [artifacts/run_summaries/m3_us101/results_with_boundary.json; artifacts/us101_validation_calibrated.json] |
| 25 | I-24 fitted-arm baseline | throughput 5,839 [5,808, 5,870] veh/h; travel time 590 [582, 598] s | 20 | [artifacts/i24_sweep_summary.json; I24_VALIDATION.md §0.2] |
| 26 | FollowerStopper 5% / 100% on the unvalidated I-24 replica | throughput −2,212 [−2,577, −1,848] veh/h (−37.9%); travel time +102%; σ_v −59%; fuel +111% | 20 per cell, 500 runs | [artifacts/i24_sweep_summary.json] |
| 27 | FollowerStopper 1% / 100%, I-24 replica | throughput −298 [−328, −267] veh/h (−5.1%) | 20 | [artifacts/i24_sweep_summary.json] |
| 28 | FollowerStopper 20% / 100%, I-24 replica | throughput −76%; σ_v −74%; lane changes 1.25 → 2.32 per veh-km | 20 | [artifacts/i24_sweep_summary.json] |
| 29 | Headway cap, 5% / 100%, I-24 replica | −35.6% to −39.8% throughput at every cap, against −37.9% | 20 per configuration | [artifacts/i24_cap_sweep_summary.json] |
| 30 | ALINEA alone, I-24 flow-family arm | travel time −192 [−203, −182] s (−33.4%); fuel −15.6%; throughput −327 [−337, −317] veh/h (−5.8%) | 20 | [artifacts/sweep_i24_strategies_summary.json] |
| 31 | FollowerStopper 10% alone vs under ALINEA | throughput −49.6% vs −28.9% | 20 per cell | [artifacts/sweep_i24_strategies_summary.json] |
| 32 | Synthetic corridor, FollowerStopper σ_v reduction | 24.5% [17.7, 31.4] at 1% / 100%; 56.8% at 5% / 100% (per-seed mean; 61.2% as a ratio of means) | 20 per cell, 540 runs | [artifacts/m3_sweep_summary.json; M3_RESULTS.md §4.1; CONTROLLER_COMPARISON.md] |
| 33 | Synthetic corridor, throughput | no resolved cost in any of 24 cells | 20 per cell | [M3_RESULTS.md §4.4] |
| 34 | PI mean-fraction vs faithful PI | −1,167 [−1,220, −1,114] veh/h (−93.6%) vs −0.7% (n.r.); faithful σ_v −1.01 [−1.35, −0.66] m/s (−29.7%) | 20 per cell | [artifacts/pi_retune_summary.json] |
| 35 | JAD, perfect vs degraded oracle | seeds with more waves than baseline 5 / 20 vs 0 / 20; waves −1.75 [−4.04, +0.54] vs −3.50 [−4.71, −2.29] | 20 per cell | [artifacts/jad_oracle_summary.json] |
| 36 | JAD with a 30 s commit deferral, perfect sensor | σ_v 1.331 [1.228, 1.435] m/s vs 1.781 [1.298, 2.264] undeferred; −25.2% paired (resolved) | 20 per cell | [artifacts/jad_deferral_summary.json] |
| 37 | US-101 σ_v dose-response | −8.2% (1%) to −53.2% (20%), all resolved | 20 per level | [artifacts/us101_penetration_summary.json] |
| 38 | US-101 throughput cost on the replica | −0.72% (1%) to −2.74% (20%) | 20 per level | [artifacts/us101_lane_change_penetration.json] |
| 39 | US-101 lane-change mechanism | excess cut-ins 0.63–0.87 of the paired rise in human changes (0.75–0.99 of excess cut-ins plus excess passes); human rate 1.7–4.0× baseline; lane changers +5.8–6.3 ml/km over non-changers, an association (+5.47 in the no-AV baseline) | 20 per level | [artifacts/us101_lane_change_penetration.json; US101_PENETRATION.md, result section and its correction of 2026-09-25] |
| 40 | Flux cap vs reduced capacity, speed RMSE difference | +0.84 m/s [0.36, 1.33] | 20 | [artifacts/run_summaries/m3_fluxcap/results.json] |
| 41 | I-94 reference configuration | departed 0.884 (lowest 0.867); RMSPE 0.691 [0.688, 0.695]; GEH < 5 on 0.163 [0.138, 0.188] | 20 | [artifacts/mndot_rounds/weave_2026-09-24/battery_exit_prepare_lane_end_f24ba43.json] |
| 42 | I-94 flow at S790, 06:30–07:30 | 3,344 veh/h simulated vs 4,911 observed | 20 | [ONBOARDING_MNDOT.md §11, VM U] |
| 43 | I-94 observed wave speed (context) | median 21.3 km/h, IQR 18.6–24.2, 6 of 13 pairs | — (9 dates) | [ONBOARDING_MNDOT.md §4a, §11; data/mndot/mndot_i94_wb_stpaul/observations.json] |
| 44 | Real I-24 entering critical gaps | 0.46 s ahead, 0.92 s behind (expected high under coverage; not bounds; direction undetermined by the thinning test, row 48) | — | [artifacts/i24_critical_gaps.json; WEAVE_MODEL_PLAN.md, "where item 1 stands"] |
| 45 | US-101 thinned to I-24-like coverage (fragment model, F = 0.65 / 0.5), weave entering: lag time gap median | +0.78 [+0.33, +1.23] / +1.53 [+1.07, +1.99] s on 1.21 s; biased as expected (lead time gap +0.60 / +1.32 s on 0.91, a bound) | 5 thinning seeds per F; 180 entering crossings | [artifacts/coverage_thinning_us101.json; WEAVE_MODEL_PLAN.md, WP-91 (rule, written first) and VM AE] |
| 46 | Same test: the weave acceptance's refusal share | −18.5 [−25.3, −11.7] / −29.3 [−32.4, −26.1] points on 71.7%; biased as expected (the lead-time term −21.1 / −29.9 points on 58.3%, a bound) | same | same |
| 47 | Same test: gap over s0 + vT at the change (`ratio_eq`) | follower side +0.37 [+0.17, +0.58] / +0.62 [+0.31, +0.94] on 0.81; leader side +0.31 [+0.10, +0.53] / +0.63 [+0.36, +0.90] on 0.62 (a bound); both biased as expected | same | same |
| 48 | Same test: rows left undetermined | critical gap ahead +0.10 [−0.04, +0.24] / +0.10 [−0.08, +0.29] s on 0.29 s; behind +0.17 [−0.06, +0.40] / +0.39 [+0.14, +0.63] s on 0.45 s; follower `ratio_pop` +0.14 [−0.01, +0.29] / +0.20 [−0.01, +0.40] on 0.76 | same | same |
| 49 | Same test: partner speeds at the change | entrant minus new follower +0.05 [−0.16, +0.25] / +0.11 [−0.25, +0.47] m/s on +1.01; new leader minus entrant −0.04 [−0.19, +0.11] / +0.04 [−0.09, +0.16] m/s on −0.40; robust | same | same |
| 50 | Partner speeds at the change, weave entering, medians | new leader minus entrant: US-101 −0.40 m/s (176 sides), I-24 −0.83 (1,612), model +1.37 (594; seed mean 1.45 [1.21, 1.68]); entrant minus new follower: US-101 +1.01 (177), I-24 +1.18 (1,488), model +0.72 (740; seed mean 0.75 [0.56, 0.95]) | — (data); 5 (model, seeds 3–7, fixture) | [artifacts/lane_change_relaxation_us101.json; artifacts/lane_change_relaxation_i24.json; model: WEAVE_MODEL_PLAN.md, WP-91, no committed artifact] |
| 51 | Gap at the change over the population's normal, weave entering (new follower / entrant behind its new leader) | US-101 0.76 (177) / 0.57 (176); I-24 0.87 (1,454 finite values) / 0.72 (1,591), follower side not coverage-robust; model 1.05 [1.01, 1.10] (740) / 1.31 (594) | — (data); 5 (model, seeds 3–7, fixture) | [artifacts/lane_change_relaxation_us101.json; artifacts/lane_change_relaxation_i24.json; model: WEAVE_MODEL_PLAN.md, WP-88 and VM AC, no committed artifact] |
| 52 | Collisions on the MnDOT corridor's reference battery, without and with the scripted-merge guard | 15 and 0 over 20 seeds each; paired −0.75 [−1.09, −0.41] a seed; departed −0.002 [−0.008, +0.003] | 20 seeds, paired | [artifacts/mndot_rounds/weave_2026-09-24/collisions_reference_and_guard_556f737.json; battery_reference_plus_force_guard_1259a9b.json; collisions_force_guard_and_lane_change_1259a9b.json] |
| 53 | Merge discharge on fixtures, and the acceleration lever (diagnostic) | on-ramp fixture, I-24 fleet, 1,460 ± 20 veh/h/lane against 1,657 recorded (coverage-corrected); 1,632 at mean `a_max` + 1 sd; lane-drop discharge ÷ pre-breakdown 0.90 (I-24 fleet), 0.83 (I-94 fleet) | 20 per fixture | [DISCHARGE_CALIBRATION.md §1]; no committed artifact |
| 54 | Observed I-94 discharge ÷ pre-breakdown flow, S790→S97 | 1.10 [0.98, 1.23]; no drop resolvable | — (8 of 9 days) | [DISCHARGE_CALIBRATION.md §1] |
| 55 | Amendment-1 grid choice (calibration) | k = 1 (mean `a_max` 1.4833 m/s²) on both; keep-right 0 (I-24), 0.1 (I-94); discharge error 12.2% → 9.4% (I-24), 26.0% → 16.2% (I-94; S97 3,321 → 3,764 veh/h) | 1 seed per pair (I-24), 2 (I-94); 75 runs | [artifacts/driver_calibration_i24.json; artifacts/driver_calibration_i94.json; DISCHARGE_CALIBRATION.md §3] |
| 56 | String-stability onset of the population's mean driver | 28.2 veh/km at k = 0 (capacity density 29.2), 32.5 at k = 0.5, 39.8 at k = 1; unstable share at 30 veh/km 0.43 → 0.22 | — (closed form) | [DISCHARGE_CALIBRATION.md §1, §4]; `validation.string_stability`, no committed artifact |
| 57 | I-24 batteries with the calibrated drivers (calibration, one morning) | peak sections 5,850 / 5,821 (reference) → 6,031 / 6,025 (k = 1) → 6,047 / 5,983 veh/h (k = 1 + refit); realised demand 0.987 / 0.996 / 0.921; GEH 21.5 / 25.0 / 25.7%; RMSPE 37.2 / 78.4 / 33.3%; wave 15.9 km/h PASS / no peak / no peak | 20 per arm | [artifacts/i24_validation_flow_speedcal_ref.json; artifacts/i24_validation_dc.json; artifacts/i24_validation_dc_refit.json; DISCHARGE_CALIBRATION.md §4; MERGE_MODEL_READINESS.md §5] |
| 58 | Amendment 2, smaller shifts with demand refits (calibration) | k = 0.25: wave 15.7 km/h, GEH 16.0%, RMSPE 34.1%, realised 0.918; k = 0.5: 15.1 km/h, 24.3%, 33.9%, 0.930; floor 0.977; none qualifies, k = 0 stays | 20 per arm | [artifacts/i24_validation_dck025_refit.json; artifacts/i24_validation_dck05_refit.json; FRISCO_PROTOCOL.md, Result of Amendment 2] |
| 59 | I-94 protocol baseline gate, calibrated drivers (rehearsal) | C1 61.8% [56.5, 67.0] / 60.0% [54.0, 66.0] (calibration / validation; reference 15.5% / 14.5%); C3 33.9% [31.3, 36.5] / 38.8% [34.2, 43.4] (reference 49.7% / 48.7%); C4 4.9 km/h; gate fails; validation days not a clean holdout | 20 | [artifacts/baseline_gate_mndot_dc.json; artifacts/baseline_gate_mndot_i94_wb_stpaul_p1.json; DISCHARGE_CALIBRATION.md §4; I94_CALIBRATION_DAYS.md §2] |
| 60 | I-94 S790, 06:30–07:30, calibrated drivers | 3,780–4,010 veh/h in every seed, against 4,911 observed | 20 | [I94_RESIDUALS.md §0; MERGE_MODEL_READINESS.md §5]; from the battery's per-seed station-hours |
| 61 | I-94 lane shares; netfix probe (diagnostic) | grid 7.6–9.9 points, 5.0–8.2 with S791 reversed; probe, calibrated drivers, 8.22 → 7.26 points (5.60 → 4.57); S97 level at three of four seeds (3,746 against 3,740 veh/h), no resolved discharge effect | 2 per pair; 4 per arm | [I94_LANE_SHARES.md, Verdict and Netfix probe; artifacts/i94_netfix_probe.json] |
| 62 | T.H.52 section test, calibrated drivers (fixture, macOS) | weave 4,361 ± 83 veh/h; no-crossing ceiling 4,826 ± 19; paired loss 465 [427, 504]; old drivers 3,873 ± 106; GEH < 5 at 1 of 20 | 20 (seeds 3–22) | [WEAVE_LOSS_DIAGNOSIS.md §2; artifacts/weave_loss_2026-10-07/arms/; artifacts/p3_driver_grid_2026-10-07/th52_section_dc_weave.json] |
| 63 | Weave rule removals and the two levers (fixture, macOS) | largest rule removal +32 [+8, +57] veh/h; `lookahead_m` 200 m +98 [+55, +141], 300 m +177 [+134, +219] | 20 paired | [WEAVE_LOSS_DIAGNOSIS.md §4; artifacts/weave_loss_2026-10-07/arms/] |
| 64 | Merge anticipation reach, I-24 MOTION | 125 m [107, 154] (fallback stratum, 858 entering changes, 67% censored); primary stratum 82 m [67, 154], fails the width check; proposal 120 m, the current value | — (data; 1,000 bootstrap resamples) | [artifacts/merge_anticipation_i24.json; MERGE_ANTICIPATION.md, Result] |
| 65 | T.H.52 ramp-to-ramp share: model and exploratory reading | proportional 0.29 (fixture window), 0.18 (peak hour); counts 0.58 [0.44, 0.71], exploratory and not pre-registered; working range up to 0.70, assumed | — (9 days) | [TH52_CROSSING_SHARE.md §4–5] |
| 66 | Section test across the share range (fixture, macOS) | GEH < 5 at 1 / 9 / 20 / 20 / 20 of 20 seeds (proportional / 0.40 / 0.50 / 0.60 / 0.70); whole test 0 / 0 / 1 / 2 / 6 of 20 | 20 per share | [artifacts/th52_crossing_share_2026-10-07/summary.json; TH52_CROSSING_SHARE.md §10.3] |
| 67 | W1 (fixture, macOS) | flow +29.0 [+9.7, +48.3] veh/h; stranded time 18.9 → 3.8 s per run; fails F3 (1.04% at one seed) and F5 (8 of 37 runs above 1%); off | 20 paired; 37-run grid | [artifacts/weave_loss_2026-10-07/w1/; WEAVE_LOSS_DIAGNOSIS.md §8.5] |
| 68 | Measured merge model against the weave, T.H.52 section, calibrated drivers (fixture, macOS) | −52 veh/h [−96, −9]; lowest station speed −1.20 m/s [−1.87, −0.54]; go/no-go NO-GO | 20 paired | [artifacts/p3_driver_grid_2026-10-07/th52_section_dc_measured.json; artifacts/p3_driver_grid_2026-10-07/th52_section_dc_weave.json; MERGE_MODEL_READINESS.md §2–3] |
| 69 | I-94 locks (diagnostic) | 3 of 20 four-hour replicates at k = 1 (3–38%) against 0 of 20 at k = 0 on the same seeds (0–17%); Fisher p = 0.23 | 20 per arm | [I94_COLLAPSE_DIAGNOSIS.md §5, §10]; per-replicate files in the stage archive, not committed |
| 70 | W1b (fixture, macOS) | 7 releases, each after exactly 60.0 s; 126 of 126 runs without a release byte-identical to their references; the one fixture lock released (1,014 of 1,014 departed against 673); five ordinary Ruth St stands of 61.5–68.5 s also released | 132 paired fixture runs | [artifacts/weave_loss_2026-10-07/w1b/criteria.json; artifacts/weave_loss_2026-10-07/w1b/releases.json; WEAVE_LOSS_DIAGNOSIS.md §10.9–10.10] |

---

## Appendix B. Figures

All figures exist in the repository and are generated by committed scripts.

| # | File | Content | Source document |
|---|---|---|---|
| 1 | `docs/figures/i24_wb_overview.png` | The recorded day: mean tracked speed (60 s × 100 m) and crossing counts per section (§3.3) | [I24_DATA.md §3] |
| 2 | `docs/figures/fd_scatter_triangle.png` | US-101 fundamental-diagram fit (§3.8) | [M2_RESULTS.md §4] |
| 3 | `docs/figures/wave_speed_vs_density.png` | Ring wave speed against density (§5.1) | [WAVE_SPEED_DIAGNOSIS.md, follow-up] |
| 4 | `docs/figures/i24_validation_fields.png` | Observed and simulated speed fields, first seed of each arm (§5.3) | [I24_VALIDATION.md §0.2] |
| 5 | `docs/figures/i24_validation_waves.png` | Backward front speeds by arm, with the criterion detector's estimates (§5.4) | [I24_VALIDATION.md §0.2] |
| 6 | `docs/figures/i24_lane_profile.png` | Lane shares and speeds through the Old Hickory merge, recording vs replica (§5.5) | [I24_VALIDATION.md §0.5(c)] |
| 7 | `docs/figures/m3_sigma_v_vs_penetration.png` | Synthetic-corridor dose-response (§6.1) | [M3_RESULTS.md §3] |
| 8 | `docs/figures/i24_sweep_dose_response.png` | Dose-response on the unvalidated I-24 replica, regenerated 2026-09-19 (§6.6) | [CHANGELOG, 2026-09-19] |
| 9 | `docs/figures/fluxcap_comparison.png` | Flux-cap and reduced-capacity macro fields against micro ground truth (§6.9) | [M3_US101_VALIDATION.md §5] |

The M3 synthetic figure predates the metric correction of §2.6; the I-24
validation figures show the first seed of the 2026-09-05 four-arm rerun
[I24_VALIDATION.md §5 note].

---

## Appendix C. Source inconsistencies found while drafting

These are places where committed documents disagreed with each other or with
their own artifacts. Each was checked on 2026-09-25 against the committed
artifacts and the code that wrote them, not against another document. Where
two numbers are both right under different definitions, the fix names the
definition beside each. Dated records were not rewritten: they carry a dated
correction note beside the original text. CHANGELOG.md was not edited in this
pass; the corrections its past entries need were handed to the coordinator.
Each item below ends with its status.

1. **The I-24 sweep's 5% / 100% cell in ROADMAP.md §1.5** reads throughput
   −36%, travel time +82%, σ_v −56% (fuel +111%). I24_SWEEP.md and
   `artifacts/i24_sweep_summary.json`, re-run on 2026-09-19 under the
   corrected metric definitions, read −38%, +102%, −59%, +111%. The ROADMAP
   addendum of 2026-09-17 also says the sweep and cap sweep "keep the earlier
   definitions", which CHANGELOG 2026-09-19 supersedes. The draft follows the
   artifact.
   *Resolved.* Cell `fs_p0.05_c1.00`, paired against the baseline:
   throughput −37.9%, mean travel time +101.8%, σ_v temporal −59.1%
   (spatial −61.9%), fuel +111.4%, waves −52.7%. The old figures are the
   2026-09-05 artifact's (git `d7a2d7a`: −36.0%, +81.6%, −56.2%, +111.4%).
   ROADMAP.md carries dated corrections at §1.5, at the headway-cap sentence
   of 2026-09-07 (now −35.6% to −39.8% against −37.9%,
   `artifacts/i24_cap_sweep_summary.json`) and at the 2026-09-17 addendum.
   The same pass found I24_SWEEP.md's "What it means" still quoting the old
   σ_v figures (−56% at 5%, −67% at 20%); it now carries a dated correction
   (−24%, −59%, −74%).
2. **I24_SWEEP.md's header** gives the scenario's config hash as
   `b072d754492d`; the artifact records `base_config_hash` `43def6306dd6`
   (the arm's hash-policy-v2 hash, per CHANGELOG 2026-09-19), and the cap
   sweep names its baseline `6ab4219ffd92`. The draft cites the artifact.
   *Resolved.* All three name the same simulation. `config_hash` recomputed
   on `scenarios/i24_replica_speedcal.yaml` gives `43def6306dd6` (hash
   policy v2). `b072d754492d` is what the 2026-09-05 artifact recorded under
   the earlier policy (git `d7a2d7a`). `6ab4219ffd92` is the same config
   renamed `i24_cap_baseline` by `scripts/i24_cap_sweep.py`; the name enters
   the hash. I24_SWEEP.md's header and cap-sweep paragraph now say so. The
   comment in `scenarios/i24_replica_speedcal.yaml` still reads
   `b072d754492d`; scenario files were outside this pass.
3. **I24_STRATEGIES.md** opens with "the first strategy sweep on a validated
   corridor" and calls its scenario "the canonical I-24 westbound arm". The
   scenario is the flow family's fitted-plus-ramps arm (config
   `0cddf2002979`), which I24_VALIDATION.md §0.10 keeps out of the canonical
   record and which fails 2 of 7 rows. The draft calls it the unvalidated
   flow-family arm (§6.8).
   *Resolved.* `artifacts/sweep_i24_strategies_summary.json` has base
   `0cddf2002979`; `artifacts/i24_validation_flow_ramps.json` passes 5 of 7
   rows and fails GEH (21.5%) and RMSPE (41.8%). I24_STRATEGIES.md carries a
   dated correction after its status line and a pointer in its setup.
   CHANGELOG 2.3.0 repeats "validated corridor"; correction text handed to
   the coordinator.
4. **The `corridor_10km` baseline throughput** is 1,246.65 veh/h in
   M3_RESULTS.md, JAD_ORACLE_RESULTS.md, PI_CONTROLLER_FIX.md and
   CONTROLLER_COMPARISON.md, and 1,327 veh/h in JAD_DEFERRAL_RESULTS.md (the
   perfect-oracle cell: 1,162 vs 1,233), although that document says the
   baseline reproduces the earlier experiments "to the digit". Its σ_v, wave
   count and fuel do reproduce. The draft quotes only paired percentages for
   JAD throughput.
   *Resolved.* Same simulations, different cross-section. The M3, PI and JAD
   oracle analyses measure throughput at x = 7,000 m and travel time over
   2,000–11,500 m (the `X_REF`/`SPAN` constants of their analysis scripts).
   `scripts/jad_deferral_experiment.py` passes neither, so it took the
   `compute_metrics` defaults of 2026-09-02: the midpoint of the observed
   position range, and the whole range. σ_v, waves and fuel match to every
   digit (`artifacts/jad_deferral_summary.json`,
   `artifacts/jad_oracle_summary.json`). JAD_DEFERRAL_RESULTS.md carries a
   dated correction naming both definitions. QA.md's "baseline numerically
   identical" now names the metrics it holds for.
5. **FollowerStopper's σ_v reduction at 5% / 100% on `corridor_10km`** is
   56.8% in M3_RESULTS.md §4.1 (mean of per-seed percentages) and 61.2% in
   CONTROLLER_COMPARISON.md, "61%" in I24_SWEEP.md and the dossier (the
   change of the means relative to the baseline mean: 1 − 1.31/3.39 ≈ 0.61).
   Both are arithmetically right; the convention should be stated wherever
   the number appears. The draft states it (§6.2).
   *Resolved.* `artifacts/m3_sweep_summary.json`:
   `sigma_v_temporal_ms_reduction_pct` 56.8 [50.3, 63.4];
   `sigma_v_temporal_ms_delta` −2.071 m/s against a 3.385 m/s baseline,
   61.2%. The convention is now stated in CONTROLLER_COMPARISON.md,
   M3_RESULTS.md §4.1, I24_SWEEP.md, FLOWSTATE_DOSSIER.md, README.md and
   WEBSITE_BRIEF.md (which also called σ_v a "variance"; it is a standard
   deviation).
6. **The US-101 throughput cost** is still quoted as −0.3% to −1.6% in
   LESSONS.md row 5 and as "a small resolved throughput ... cost" in QA.md,
   and the "Honest summary" of US101_PENETRATION.md keeps "roughly 1%
   throughput" under a warning note. The correction of 2026-09-25 in the
   same document shows that column was
   measured upstream of the replica; on the replica the cost is 0.7–2.7%. The
   draft uses the corrected figure.
   *Resolved.* `artifacts/us101_lane_change_penetration.json`,
   `site_throughput_veh_h`: −0.72% (1%), −1.05% (2%), −1.69% (5%), −1.97%
   (10%), −2.74% (20%), all resolved. Changed: LESSONS.md row 5 (dated
   correction), QA.md, US101_PENETRATION.md (the "what replicates" paragraph
   and the summary's note: 0.7–2.0% at 1–10%) and README.md. CHANGELOG 2.1.0
   carries 0.3–1.6%; correction text handed to the coordinator.
7. **The density at which the I-24 fleet's ring waves reach the band**:
   ROADMAP.md §1.4 says "only above ~80 veh/km"; QA.md says "above
   ~60 veh/km". WAVE_SPEED_DIAGNOSIS.md gives 14.2 km/h at 60 veh/km (69% in
   band) and 16.4 km/h at 80 (95%). The draft quotes the table.
   *Resolved.* Both are right under different definitions. With the relative
   detector the mean front speed enters the band at 60 veh/km (I-24 fleet
   14.2 km/h, 69% of fronts inside; US-101 fleet 14.0, 70%); nearly every
   front is inside at 80 (16.4, 95%; 17.1, 98%)
   (`artifacts/wave_speed_sitelength_i24.json`,
   `artifacts/wave_speed_sitelength.json`). ROADMAP.md §1.4 carries a dated
   note; QA.md names both. CHANGELOG 2.1.0 carries "~80"; correction text
   handed to the coordinator.
8. **The coverage range** is 0.52–0.66 in the I24_DATA.md §4 table, "≈
   0.5–0.65" in ROADMAP.md §1.1 and PAPER_OUTLINE.md, "≈ 0.5–0.7" in
   I24_DATA.md §7, and "52–67%" in QA.md. The draft quotes the table.
   *Resolved.* Three estimators over the eight windows 06:30–08:30
   (`artifacts/i24_coverage_lane5.json`): the equilibrium method on the
   first population 0.519–0.663 (the §4 table); on the capacity-calibrated
   population 0.481–0.605 (what the scenarios divide by,
   `artifacts/i24_replica_inputs.json`); the recommended gap estimator
   0.559–0.674. QA.md's 67% was the one wrong value and is corrected. "≈
   0.5–0.65" and "≈ 0.5–0.7" are roundings; I24_DATA.md §7 and
   PAPER_OUTLINE.md now name the estimators, and ROADMAP.md §1.1 is left as
   written. I24_DATA.md §4 gains a source note: its table no longer matches
   `i24_replica_inputs.json`. This draft's §3.4 source line and ledger rows
   3–4 are corrected to match; row 4 was 0.559–0.656 over five windows and
   is 0.559–0.674 over all eight.
9. **Two metric definitions for the same I-24 arm**: the fitted arm's
   throughput is 5,710 veh/h under the 2026-09-05 definitions
   (I24_CAPACITY.md §7, I24_VALIDATION.md §0.6, I24_DATA.md heavy section)
   and 5,839 veh/h after the 2026-09-17 re-run (I24_VALIDATION.md §0.2). The
   heavy-arm comparison (5,170 vs 5,710) is under the old definitions. The
   draft labels which definition each number uses.
   *Resolved.* `artifacts/i24_validation_speedcal.json` 5,839 [5,808, 5,870]
   and `artifacts/i24_validation_speedcal_heavy.json` 5,276 [5,246, 5,305]
   under the corrected definitions; 5,710 and 5,170 in their 2026-09-05/06
   versions (git `014a6b2`, `de10089`). Dated notes in I24_DATA.md (heavy
   section), I24_CAPACITY.md §7, I24_VALIDATION.md §0.3 and §0.6, and
   ROADMAP.md (the 2026-09-06 round). §5.6 of this draft now gives both
   pairs.
10. **The I-24 strategy grid switches σ_v conventions** between its tables:
    temporal in the 2026-09-23 table (VSL −29.1%, FollowerStopper 10%
    −59.5%), spatial in the complete grid (−19.2%, −67.7%). The draft uses
    the complete grid and labels it spatial.
    *Resolved.* `artifacts/sweep_i24_strategies_summary.json`,
    `vs_baseline_paired`: temporal −29.1 / −27.2 / −59.5 / −52.9 / −68.5%,
    spatial −19.2 / −11.7 / −67.7 / −54.8 / −74.1% (VSL, ALINEA,
    FollowerStopper 10%, with ALINEA, with VSL). I24_STRATEGIES.md carries a
    dated note with both columns.
11. **The MnDOT observed wave-speed IQR** is 18.4–24.2 km/h in
    ONBOARDING_MNDOT.md §4a and 18.6–24.2 km/h after the loop exclusion in
    §11; the median is 21.3 km/h in both. The draft uses §11.
    *Resolved.* `context.detector_wave_speed` in
    `data/mndot/mndot_i94_wb_stpaul/observations.json`: median 21.26 km/h,
    IQR 18.59–24.18, leave-one-date-out 18.45–21.58. ONBOARDING_MNDOT.md §4a
    carries a dated correction. CHANGELOG 2.3.0 carries 18.4–24.2;
    correction text handed to the coordinator.
12. **The weave model's entering critical gaps** are 1.18 / 1.21 s in
    WEAVE_MODEL_PLAN.md "where item 1 stands" point 6 and 0.98 / 0.66 s in
    its correction (WP-82). The draft uses the correction.
    *Resolved; no edit in this pass.* WEAVE_MODEL_PLAN.md carries
    "Correction to 'where item 1 stands', point 6 (2026-09-25, from WP-82)"
    with 0.98 [0.86, 1.10] / 0.66 [0.54, 0.79] s. The corrected values live
    in that dated section and CHANGELOG 2026-09-25 only. The committed
    artifact `artifacts/th52_fixture_critical_gaps.json` still holds the
    uncorrected fit (1.18 / 1.21 s, WP-78, without SUMO's arrival crossings).
13. **"Seeds worse than baseline" for perfect-oracle JAD** is 5 of 20 by wave
    count in JAD_ORACLE_RESULTS.md and 1 of 20 by σ_v in
    JAD_DEFERRAL_RESULTS.md. Both are right for their statistic; the
    statistic should be named wherever the number is quoted. The draft names
    it.
    *Resolved.* `artifacts/jad_deferral_summary.json`, `jad_perfect`,
    `n_worse_than_reference`: σ_v 1, wave count 5;
    `artifacts/jad_oracle_summary.json` `n_seeds_worse` 5 by wave count. The
    statistic is now named in JAD_DEFERRAL_RESULTS.md,
    CONTROLLER_COMPARISON.md, QA.md, README.md (twice) and LESSONS.md row 3
    (dated note). JAD_ORACLE_RESULTS.md and jad_derivation.md already named
    it. CHANGELOG 2.1.0 says "5/20 seeds end worse"; correction text handed
    to the coordinator.
14. **The FHWA citation.** PAPER_OUTLINE.md's required-citation list names
    only FHWA-HOP-18-036 (2019), and M3_US101_VALIDATION.md §2 cites it for
    boundary-condition practice. CLAUDE.md §7.1 records that the GEH target
    comes from the 2004 volume (FHWA-HRT-04-040) and that the 2019 update
    states no GEH target. The draft cites both, with the GEH target on 2004.
    *Resolved for the GEH target.* `validation.criteria` records the check of
    both volumes. PAPER_OUTLINE.md now lists both, with the GEH target on
    2004. M3_US101_VALIDATION.md §2 cites the 2019 volume only for
    boundary-condition practice, not for GEH, and is unchanged; that
    attribution was not checked against the 2019 text in this pass.
15. **PAPER_OUTLINE.md** lists the ring wave-speed figure as "to be drawn";
    `docs/figures/wave_speed_vs_density.png` already exists and is used as
    Figure 3.
    *Resolved.* The figure was committed on 2026-09-02 (`07a5c4f`), drawn by
    `scripts/make_wave_speed_figure.py` from
    `artifacts/wave_speed_sitelength{,_i24}.json`. PAPER_OUTLINE.md now
    names it.
16. **The re-run under corrected metrics.** The CHANGELOG 2.2.0 release
    summary says the canonical I-24 arms, US-101 and both sweeps were
    re-simulated and "every criteria row reproduced to the digit". The
    detailed CHANGELOG entry of 2026-09-17 and M3_US101_VALIDATION.md's
    re-run section say US-101's speed and wave rows, re-scored on the
    windowed field, moved within a point (RMSPE 36.6% → 35.9% and 27.9% →
    28.4%; wave 5.8 → 6.1 and 5.8 → 5.9 km/h) with unchanged verdicts. The
    draft states the I-24 and US-101 cases separately (§2.6).
    *Resolved.* Verified: every I-24 row reproduced
    (I24_VALIDATION.md §0.1); US-101's rows moved
    (`artifacts/us101_validation_calibrated.json`: RMSPE 0.359 and 0.284,
    wave 6.06 and 5.86 km/h). No other document is wrong. The release
    summary now carries a dated correction note beside it (CHANGELOG.md,
    2.2.0, 2026-09-25).
17. **The US-101 row count.** US101_PENETRATION.md says the replica "fails 5
    of 6 FHWA criteria"; M3_US101_VALIDATION.md scores 1 PASS / 5 FAIL of
    which two FAILs are ring rows not evaluated by that driver. Both are
    consistent, but "fails 5 of 6" reads as five measured failures. The
    draft says 1 PASS / 5 FAIL and names the two not-evaluated rows (§5.2).
    *Resolved.* `artifacts/run_summaries/m3_us101/results_with_boundary.json`
    has six rows: three measured failures, two ring rows not evaluated, and
    the replicate count passing. The 2026-09-17 re-run's
    `artifacts/us101_validation_calibrated.json` adds a seventh,
    `sensitivity_grid`, also not evaluated. US101_PENETRATION.md, ROADMAP.md
    §0 and PAPER_OUTLINE.md now name the measured failures; so do §5.2 and
    ledger row 24 of this draft.

Found while resolving the list:

18. **The US-101 wave row's detector label.** In
    `artifacts/us101_validation_calibrated.json` the `wave_speed` row reads
    6.06 and 5.86 km/h, but its detail names the stack detector and adds
    "caller did not state which detector produced the value". The values
    come from the standard 40 km/h detector on the driver's own speed field
    (`scripts/m3_us101_validate.py` calls `detect_waves(field)` at its
    default threshold), as §5.2 of this draft labels them. The report
    generated from the with-boundary runs
    (`docs/reports/us101_replica/report.md`) scores the row with the stack
    detector, finds no front in any of the 20 replicates and reads NaN. The
    verdict is FAIL either way.
    *Resolved 2026-09-25.* `scripts/m3_us101_validate.py` now scores the
    row with the profile's stack detector and names it (commit 9fef785), and
    the artifact was regenerated on a cloud VM: the row reads NaN on both
    arms (no backward front in 0 of 20 replicates, nor in the observed
    field), FAIL as before; GEH and RMSPE reproduce to the digit
    [M3_US101_VALIDATION.md, note of 2026-09-25].

Found while revising on 2026-10-07. The revision pass edited only this
draft and left each item for the coordinator; items 19 and 20 were settled in
the same commit (`4d32042`), and items 21 and 22 remain open.

19. **Which I-24 arm is "canonical".** DISCHARGE_CALIBRATION.md §4 said
    "`i24_replica_flow_speedcal` stays canonical". I24_VALIDATION.md §0.10
    calls that scenario the flow family's fitted arm and keeps the canonical
    family as the published record. Both are consistent in context: the first
    names the reference arm of the driver-calibration work. The draft calls
    it the flow family's fitted arm, the reference for §4.6 and §5.7, and
    keeps §5.3's record unchanged. *Resolved in commit `4d32042`.*
    DISCHARGE_CALIBRATION.md §4 now reads "The canonical I-24 arm
    (`i24_replica_speedcal`) and the Amendment-1 reference
    (`i24_replica_flow_speedcal`) are unchanged", as the CHANGELOG's step-3
    entry of 2026-10-07 does.
20. **The I-24 plateau in CHANGELOG 2026-10-07.** The entry for the
    Amendment-1 grid read the I-24 plateau "as demand-limited", with no
    dated note. The step-3 entry of the same date and
    DISCHARGE_CALIBRATION.md §3 carry the correction: a merge and discharge
    shortfall. The draft follows the correction (§5.7). *Resolved in commit
    `4d32042`.* The entry now reads "first read as demand-limited (corrected
    the same day: the step-3 refit raised demand without raising the peak
    sections and built a backlog, so the plateau is a merge/discharge
    shortfall)".
21. **Two string-stability readings.** DISCHARGE_CALIBRATION.md §1 gives the
    onset of instability as 29 veh/km (I-24 fleet) and 35 veh/km (I-94 fleet)
    today, against 41 and 49 at + 1 sd. §4 gives 28.2 against 39.8 veh/km for
    the population's mean driver with 5 m vehicles. On I-24 the two differ by
    about 1 veh/km. §1 does not state its vehicle length or desired speed, so
    the cause of the difference is not established here. The draft quotes §4
    with its definition (§2.2, ledger row 56). *Open.*
22. **The netfix rule of stage p8.** I94_CALIBRATION_DAYS.md §6 records the
    rule that lets p8 run the netfix battery as holding (S97 discharge error
    0.326 → 0.163). I94_COLLAPSE_DIAGNOSIS.md §5 shows that it holds only
    through the one collapsed as-built run; at the three unlocked seeds the
    errors are 0.167 against 0.166, and the condition fails. The draft does
    not use the rule. Whether to run `_dc_cal_netfix` rests on the fix as an
    input correction, which is the owner's decision. *Open.*
