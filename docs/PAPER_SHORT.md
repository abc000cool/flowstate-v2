# Pre-registered calibration of freeway replicas on I-24 and I-94: a reproducible workflow and an unvalidated record

> **Unsubmitted short draft, 2026-10-08 (E13, docs/PRE_FRISCO_PROGRAM.md).** Condensed from
> [PAPER_DRAFT.md](PAPER_DRAFT.md) (revised 2026-10-07) and brought up to the record committed
> through `466193f`. Publishing it is the owner's decision. No simulation was run for it. Every
> number carries a bracketed pointer to the committed artifact (with its keys) or document section
> it comes from; [computed] marks a figure derived for this draft from the named artifact, and
> [recomputed] a configuration hash recomputed from a committed scenario file. Nothing here is a
> validation claim.

**Authors:** Ansh Pathak, Sujan Sannidhi, Venkata Shashish Vasireddi
**Affiliation:** FlowState
**Contact:** [to be completed by the owner]

## Abstract

We describe an open, seeded workflow for building a microscopic replica of a freeway corridor and
judging it by acceptance criteria fixed before each run. It uses SUMO 1.27.1 with Intelligent
Driver Model drivers drawn from a population fitted to trajectory data. Every round states its
criteria in advance, pairs its arm with a same-code reference on 20 common-random-number seeds and
ends in a dated adoption record; failed criteria are reported, never re-thresholded. We apply it to
3.4 miles of I-24 westbound (one morning recorded by the I-24 MOTION testbed) and to I-94
westbound in St. Paul (nine mornings of public loop data). The ring benchmark reproduces wave
emergence and single-vehicle dampening on every seed. On I-24, fixtures moved the flow ceiling from
the on-ramp merge to the corridor's downstream end, and a count check found through traffic in the
recording's on-ramp counts. Removing it brings the peak sections within GEH 5 on two-hour flows,
yet hourly flows, speeds and, with that arm's drivers, the emergent-wave criterion still fail. On
I-94 the protocol's gate fails. On NGSIM I-80, never calibrated on, the kept merge configuration
fails its gap and speed criteria. No corridor is validated.

*Sources for the abstract's figures: §2.1, §3, §4 and §5.*

## 1. Introduction and contribution

Stop-and-go waves on congested freeways come from string instability in car following: near
capacity a small speed disturbance grows as it passes back through a platoon (Treiber, Hennecke &
Helbing 2000; Treiber & Kesting 2013). Sugiyama et al. (2008) showed such a wave forming among 22
vehicles on a 230 m ring; Stern et al. (2018) showed that one controlled vehicle can remove it; the
CIRCLES MegaVanderTest took sparse control to 100 vehicles on I-24 in November 2022 [CLAUDE.md
§13]. A ring has no inflow, ramps or lanes. An agency that wants to act on a smoothing controller
or a speed limit needs a model of its own corridor, calibrated to that corridor's data and checked
against criteria a reviewer accepts.

This paper is about that check, and it makes no result claim. Its contribution is a workflow in
which every calibration step can be audited:

1. **A corridor pipeline**: OpenStreetMap geometry, heterogeneous drivers fitted to trajectory data
   with a holdout, the FHWA sequence (capacity, demand, ramps) and batteries of 20 seeded
   replicates, each run recording its seed and configuration hash (§2, §6).
2. **Pre-registration**: a corridor-study protocol committed on 2026-10-04 before any client data,
   dated amendments, and rounds whose criteria, references and Stop rules were written before
   launch (§2.4).
3. **A record that says where the model fails and why**, on two corridors and one transfer site,
   located where possible by fixtures and data-only checks (§3–§5).
4. **Reproducibility across hash policies and platforms**, including a measured divergence of
   SUMO's own arithmetic between macOS and Linux (§6).

No corridor is validated, and no controller or strategy result is reported: the protocol forbids
strategy recommendations from a model that fails its gate [FRISCO_PROTOCOL.md §6], and every model
here fails it. The project's first version reported phantom-jam dissipation in a first-order
Lighthill–Whitham–Richards model, which cannot form such waves [LESSONS.md row 1; CLAUDE.md ADR-1].
That error, and those caught since, are why every number below names its source.

## 2. Methods

### 2.1 Simulator and drivers

The engine is Eclipse SUMO 1.27.1, driven in-process through libsumo, with the Intelligent Driver
Model (δ = 4) at a 0.5 s step [CLAUDE.md ADR-1, §3.2]. Car following is fitted per leader–follower
episode (at least 30 s, no lane or leader change) by seeded differential evolution on gap RMSE,
after Kesting & Treiber (2008); every simulated driver draws its parameters from a truncated
multivariate normal over the fits [M2_RESULTS.md §3; I24_DATA.md §5]. On I-24 MOTION the population
rests on 12,356 fitted and 5,296 held-out episodes, with a holdout gap RMSE of 5.29 m and a mean
maximum acceleration `a_max` of 1.055 m/s² (sd 0.428) [artifacts/idm_i24.json,
`n_episodes_fit`, `n_episodes_holdout`, `holdout_gap_rmse_m`, `mean`, `cov`]. FHWA step 1 scales
the mean time headway T by 0.875, to 1.322 s, so that a straight four-lane road carries 1,775
veh/h/lane, the tracked capacity's lower bound [artifacts/idm_i24_capacity.json, `mean.T`, `notes`].

Amendment 1 let calibration move mean `a_max` to mean + k·sd, k ∈ {0, 0.25, 0.5, 0.75, 1}; its rule
chose k = 1 (1.483 m/s²) on both corridors [FRISCO_PROTOCOL.md, Amendment 1;
DISCHARGE_CALIBRATION.md §3]. That choice conflicts with CLAUDE.md §3.1, which requires instability
near capacity. In closed form the mean driver at k = 0 is string-unstable from 28.2 veh/km, below
its capacity density of 29.2; at k = 1 only from 39.7, so it is stable at capacity
[artifacts/driver_joint_screen_i24.json, `rows` with j = 0, `band_lo_veh_km`,
`capacity_density_veh_km`]. I-94 runs the I-24 population, a stated transfer assumption, under
SUMO's extended IDM (EIDM) [ONBOARDING_MNDOT.md §5, §10].

### 2.2 The locked merge and weave configuration

Protocol §7.4 fixes lane changing and merging for every corridor. E13's reading, approved under the
owner's delegation, is one locked configuration [PRE_FRISCO_PROGRAM.md, E13 and "Coordinator's
decisions"]:

- acceleration lanes: SUMO's LC2013 lane-change model (`merge: lane_change`);
- weaving sections (I-94's T.H.52 and Ruth St): the runner's weave rules, with Amendment 4's W1b
  and W2 on together (§4.3) [FRISCO_PROTOCOL.md, Amendment 4];
- two I-94 on-ramps: a scripted late merge whose forced change needs a gap longer than the
  follower's braking distance (`force_guard`) [ONBOARDING_MNDOT.md §11, VM AG].

A fourth model, `merge: measured` (I-24's measured critical gaps, speed matching, a post-crossing
headway), is retired by E11's pre-registered rule (§5); deleting its code is an ask-first step not
yet taken [I80_MERGE_VALIDATION.md §9]. At a weave, who crosses follows the Highway Capacity
Manual's proportional split, an assumption where no origin–destination count exists
[TH52_CROSSING_SHARE.md §0]. Where congestion enters from downstream, the measured downstream speed
is every vehicle's desired speed over the last edge, 992 m on I-24 [I24_DISCHARGE_DIAGNOSIS.md
§5.1].

### 2.3 Acceptance criteria and the gate

**Table 1.** The protocol's checks [FRISCO_PROTOCOL.md §4–§5; CLAUDE.md §7.1].

| Check | Pass rule | Source of the threshold |
|---|---|---|
| C1 link flows | GEH < 5 on ≥ 85% of station-hours | FHWA TAT Vol. III 2004 (FHWA-HRT-04-040) §5.6; the 2019 update (FHWA-HOP-18-036) states no GEH target |
| C2 link flows | GEH < 3 on every station-hour; reported only | TxDOT TSAP ch. 13 |
| C3 speeds | RMSPE ≤ 15% on 15-min station speeds | common microsimulation practice; aggregation fixed in advance |
| C4 wave speed | `stack` backward speed in 14–22 km/h, a front in ≥ 80% of replicates | empirical stop-and-go literature |
| C5 safety | zero collisions in every run | FlowState rule (CLAUDE.md §3.3) |
| C6 bottlenecks | observed active bottlenecks reproduced in place, timing and reach; phantoms in ≤ 50% of replicates | after Chen, Skabardonis & Varaiya (2004); the 5-of-7 persistence rule is from a secondary summary, unchecked |

The gate passes only if C1, C3, C5 and C6 pass on the calibration days and C1, C3 and C6 on the
validation days, with C4 passing or not applicable; a seeded, volume-stratified draw sends 60% of
days to calibration [FRISCO_PROTOCOL.md §3, §6]. I-24 has one morning and cannot be split; its
battery scores GEH < 5 on 144 (section, 5-min) bins of the 20-replicate mean, segment-speed RMSPE
on 5-min × 549 m bins, the `stack` wave row, two ring rows and two design rows [I24_VALIDATION.md
§0.1], and since Amendment 11 it reports C1's station-hour form beside them. The `stack` detector
(a slant-stack peak at contrast ≥ 3) was named on a synthetic benchmark, after the first I-24
battery had been scored with a 40 km/h threshold detector [CONTRACTS.md §4; I24_VALIDATION.md
§0.4]. A `no_locks` row flags cells standing with zero discharge for at least 10 minutes while a
queue builds [CONTRACTS.md, "Locks"]. Corridor batteries use the 20 seeds of `spawn_seeds(42, 20)`
in every arm (common random numbers); fixtures use fixed seed lists stated where quoted. Means
carry t-distribution 95% intervals, paired per-seed differences their own, and rare events
Clopper–Pearson intervals [M3_RESULTS.md §1; I94_COLLAPSE_DIAGNOSIS.md §5].

### 2.4 Pre-registration

A protocol change after data arrive is a dated amendment, with results reported under both rules
[FRISCO_PROTOCOL.md, change control]. Each corridor round fixes its arms and criteria in advance;
its same-code reference must reproduce the committed battery exactly or nothing is read; one rule
decides adoption; Stop rules end a line of work when its premise fails. Single-seed runs are probes.
Fixtures that force a breakdown (a 90-s red phase at a merge; an imposed 1–2 m/s boundary) are
seeded perturbations under CLAUDE.md §0.2 and support mechanism readings only
[I24_DISCHARGE_DIAGNOSIS.md §3–§4; I94_CAL_COLLISIONS.md §13.3]. On 2026-10-07 the owner delegated
decisions to a coordinator; that day's amendments were taken so, and the owner can overturn them
[DECISIONS_2026-10-07.md; PRE_FRISCO_PROGRAM.md, "Coordinator's decisions"].

**Table 2.** Protocol amendments [FRISCO_PROTOCOL.md, Amendments; PRE_FRISCO_PROGRAM.md].

| # | What it does | Status on 2026-10-08 |
|---|---|---|
| 1 | Mean `a_max` and keep-right eagerness may be calibrated on a grid, by a fixed rule | ran; k = 1 chosen on both corridors |
| 2 | I-24: smaller `a_max` shifts must keep the wave row and lose no demand | proposed; no arm qualified; k = 0 stays on I-24 |
| 3 | The T.H.52 ramp-to-ramp share is an uncertain input over [proportional split, 0.70] | adopted; range round built, not run |
| 4 | Weave rules W1b and W2 run together at every weave | adopted after a failed clause, saying so; defaults on in code |
| 5 | Ramp counts lose vehicles the trajectories show in a mainline lane (B2) | adopted provisionally |
| 6 | Demand level fitted on link-flow GEH under an insertion constraint (B5) | approved; not run |
| 7 | `a_max` and T moved together under the emergent-wave constraint (B6) | approved; its Stop rule fired |
| 8 | Bounds for a per-window demand-timing fit (C8) | conditional on C7 selecting timing; it did not; no text |
| 9 | A day used to calibrate stays in calibration; CIRCLES test days are events | approved; waits for two more I-24 mornings |
| 10 | I-94 ramp rules (b) and (c) as pre-registered rounds (D10) | text approved; not run |
| 11 | I-24 reported in C1's station-hour form beside its 5-min row | reporting adopted; criterion's form is the owner's |

## 3. I-24: calibration on one recorded morning

### 3.1 Data and replica

The I-24 MOTION export of 30 November 2022 gives westbound trajectories over four miles; the study
period is 06:30–08:30 [I24_DATA.md §1, §3; Gloudemans et al. 2023]. Every record is a fragment
(median 9.9 s, 117 m), and the recommended, model-free coverage estimator finds 0.559–0.674 of the
vehicle-time tracked in the eight 15-min windows [I24_DATA.md §2; artifacts/i24_coverage.json,
`windows[].pooled.recommended`]. Speeds are sound; counts are lower bounds; flow targets are
tracked crossings divided by that coverage [I24_VALIDATION.md §0.5(b)]. The replica covers 3.4 of
the 4 miles with two on-ramps and two off-ramps (Figure 1) [I24_VALIDATION.md §1].

### 3.2 The published record, on uncorrected ramp counts

**Table 3.** The four demand arms, 20 seeds each, k = 0 drivers, on ramp inputs later found to
carry through traffic (§3.3) [artifacts/i24_validation_{tracked,corrected,speedcal,ramps}.json,
`criteria`, `simulated.wave_speed_by_detector.stack.n_replicates_with_backward_waves`,
`observed.waves_by_detector.stack`].

| Row (threshold) | Tracked demand | Coverage-corrected | Fitted level | Fitted level + ramps |
|---|---|---|---|---|
| Link-flow GEH < 5, share of 5-min bins (≥ 85%) | 0.7% | 16.7% | 18.8% | 20.1% |
| Segment-speed RMSPE, 5 min (≤ 15%) | 187.8% | 33.7% | 35.9% | 34.8% |
| `stack` wave, km/h (14–22; observed 19.9, contrast 3.44) | no peak | 15.9, peak in 9/20 | 15.8, 12/20 | 15.7, 7/20 |
| Ring rows, replicates, sensitivity grid | pass | pass | pass | pass |
| Rows passed | 4 / 7 | 5 / 7 | 5 / 7 | 5 / 7 |

Only three rows compare the replica with the recording. Flows and speeds fail in every arm; the
wave row passes on the `stack` detector alone, with a peak in 7–12 of 20 replicates
[I24_VALIDATION.md §0.2, §0.4]. The recording against its own 15-min moving average already scores
33.4% RMSPE at 5 min, so no ensemble mean can reach 15% at the resolution fixed before the result
[I24_VALIDATION.md §0.5(a)]. The ring rows run the `ring_sugiyama` scenario with literature IDM
parameters, not the corridor's drivers: tail σ_v 2.34 m/s [2.25, 2.42], and one FollowerStopper
vehicle takes it below 10⁻⁵ m/s in every seed [scenarios/ring_sugiyama.yaml;
artifacts/i24_validation_dc_refit_rc.json, `ring`]. The congested arms reproduce the stop-and-go
pattern from 2.2 km on (Figure 2) [I24_VALIDATION.md §0.3].

### 3.3 Where the ceiling sits

The flow family's fitted arm (k = 0, uncorrected ramp counts) carries 5,850 and 5,821 veh/h at the
peak sections (data x = 2,200 and 3,200 m) against 6,626 and 6,639 recorded
[artifacts/boundary_b1_corridor.json, `arms.canonical.reported.peak_sections`]. Six rounds of
merge-side probes did not move it [I24_VALIDATION.md §0.5–§0.11]. Fixtures in I-24's own geometry
(macOS; the merge fixture forces a breakdown) then located it. The Old Hickory merge alone passes
6,620 ± 62 veh/h with the k = 0 drivers and 6,979 ± 49 with k = 1 (20 seeds)
[artifacts/i24_discharge_2026-10-07/G_summary.json, `k0.s2200`, `k1.s2200`; ± is a 95% half-width].
The downstream end alone, with the boundary on its 992 m edge and no merge, passes 6,114 ± 20 with k
= 1 (10 seeds), near the 6,047 of the k = 1 corridor arm on uncorrected counts [artifacts/i24_discharge_2026-10-07/DS_summary.json,
`k1_992.s2200`; artifacts/i24_count_consistency.json, `verdict.model_vs_targets`]. Of the 579 veh/h
between a straight copy of that arm and the recording at 2,200 m, about 234 come from the boundary
representation (drivers given the measured schedule as desired speed travel well below it), about
288 from net exits that are too small, and none from the merge [I24_DISCHARGE_DIAGNOSIS.md §6].

A data-only count check followed, its rule fixed before it ran (stage p11). Between 2,200 and
5,400 m the recording's counts at pooled coverage do not conserve: −403 veh/h [−689, −135].
Per-section coverage flips the sign (+294) and does not explain it; removing ramp-lane vehicles
that the same fragment shows in a mainline lane brings it to −191, inside the rule's bound
[artifacts/i24_count_consistency.json, `verdict.residuals_veh_h`, `verdict.pooled_ci_block`].
The outcome, `ramp_counts_contaminated`, keeps the peak targets and finds at least 14% (Old
Hickory) and 35% (Hickory Hollow) through traffic in the on-ramp counts [`verdict.outcome`;
[computed] `ramps[].prior_mainline_tracked_veh_h` over `tracked_veh_h`]: vehicles already in the
mainline counts, which the model's ramp inputs inserted twice. That is where the net-exit part of
the gap lies [I24_DISCHARGE_DIAGNOSIS.md §8.4.2].

### 3.4 Two corrections on the corridor

**Table 4.** Corridor rounds, criteria fixed before launch, 20 paired seeds per arm, one morning
[artifacts/boundary_b1_corridor.json, `arms.*.criteria`, `adoption`;
artifacts/boundary_b2_corridor.json, `criteria`, `paired_b2_minus_reference`;
artifacts/boundary_b1b2_corridor.json, `part_i.criteria`, `part_i.reported.quoted_figures`,
`part_ii.criteria`, `part_ii.fit.chosen`]. Criteria are abbreviated; their full texts are in
I24_DISCHARGE_DIAGNOSIS.md §8.3, §8.4.3 and §8.4.5.

| Round (stage) and change | Criteria | Readings | Verdict |
|---|---|---|---|
| B1 (p12): boundary schedule × 1.2185, a factor computed from the mean driver's equilibrium; uncorrected ramp counts | A1 no collision; A2 5,400 m flow in 5,829–6,309 veh/h; A3 realised demand not lower; A4 wave verdict kept; A5 15-min RMSPE ≤ reference + 0.02 | k = 1 refit arm: 5,400 m flow 5,735 → 6,008 [5,994, 6,021] veh/h (recorded 6,009). k = 0 arm, where A5 is read: 0.230 → 0.309 (limit 0.250) | A5 fails; not adopted |
| B2 (p13): ramp counts minus flagged through traffic, on the k = 1 refit arm | R1 no collision; R2 realised demand not lower; R3 ramps within GEH 5 of corrected counts; R4 peak GEH not higher; R5 wave verdict kept, RMSPE ≤ reference + 0.02 | realised 0.921 → 0.967, paired +0.045 [+0.042, +0.049]; ramps at GEH 1.2–2.0; peak GEH 7.28 / 8.25 → 3.86 / 4.63, paired +269 [248, 290] / +284 [266, 302] veh/h; RMSPE 0.273 → 0.254 | R1–R5 hold; adopted provisionally |
| B1 on B2 (p14, part i) | A1–A5 against B2 | A5 0.254 → 0.310 (limit 0.274); one seed breaks down, realising 0.660 against 0.971 | A5 fails; not adopted |
| Speed-objective demand refit on B2 (p14, part ii) | C1 no collision; C2 realised ≥ B2's − 0.01; C3 hourly GEH share not lower; C4 RMSPE ≤ B2's + 0.02; C5 wave verdict kept | s = 1.125; realised 0.793, paired −0.174 [−0.176, −0.171]; GEH share 25.0% against 30.6% | C2, C3 fail; s = 0.925 stands |

The RMSPEs here are of the 20-replicate mean field [artifacts/boundary_b1b2_corridor.json,
`part_i.reported.estimators`]. B1 lands the 5,400 m flow on the recording where demand fills the
downstream end, but on the k = 0 arm and on top of B2 the corridor then runs faster than the
congested recording. B2 holds every criterion fixed for it; on the battery's own rows it moves the
5-min link-flow share from 25.7% to 30.6% and the 5-min RMSPE from 0.333 to 0.343, and the wave row
fails in both arms [artifacts/i24_validation_dc_refit_{p13ref,rc}.json, `criteria`]. The refit
picked a backlog, as under Amendment 2, so the carried level stands.

Amendment 5 adopted B2 provisionally as a ramp-count correction under the protocol's data-quality
rule (§2.3), not as a calibration change (§7.1) [FRISCO_PROTOCOL.md, Amendment 5;
DECISIONS_2026-10-07.md §A1]. The arm `i24_replica_flow_rc_speedcal_dc_refit` (config
909b89f298c5 under hash policy v3, 7082bcea5442 under v4 [recomputed]) is the I-24 calibrated-arm
candidate. Its Old Hickory part, about 150 veh/h at pooled coverage, lies outside the span the
count check tested and reverts if a flag audit or an external count contradicts its sign [same].

### 3.5 The link-flow share decomposed (C7)

A diagnosis whose classes, order and rule were fixed before it ran sorted B2's failing bins
[I24_GEH_DIAGNOSIS.md §1–§3; artifacts/i24_geh_diagnosis.json, `verdict`]. Of 144 bins 100 fail:
75 recording noise (the observed 5-min count is itself at GEH ≥ 5 from its centred 15-min mean), 15
shape, 6 timing, 4 level; noise is largest under every order. Against that centred mean the
recording passes 31.25% of its own bins, against the model's 30.6%
[`arms.dc_refit_rc.recording_vs_own_mean`]. The rule selected station-hour reporting
(Amendment 11): 64.6% of 240 pooled comparisons pass (per-replicate interval 61.8–67.4%), 58.3% on
the replicate mean, both short of 85% [`arms.dc_refit_rc.station_hours`]. Three consistency
defects of ours surfaced: at 1,000 and 4,800 m the simulated count reads five lanes where the
recording reads four; planned demand runs at 1.00–1.15 times the target, because inputs and target
use different coverage estimators; and inflow is stamped at the count section's clock time though
vehicles enter 2,452 m upstream, 75.7 s away at the fleet's mean desired speed
[I24_GEH_DIAGNOSIS.md §6–§7]. Round C7b pre-registers computed corrections for all three (§7).

### 3.6 Locks recorded

Re-scored from their archives without simulating, B2 alone and B1 + B2 each show no lock in 20 of 20
seeds (95% interval 0–16.8%) [artifacts/i24_locks_p14_{b2_ref,b1b2}.json, `locks.share_locked`]. The
B1 + B2 seed that broke down is not a lock: its longest standing cell lasted 180 s against the 600 s
a lock needs, because its queue crawled but kept discharging [artifacts/i24_locks_p14_b1b2.json,
`standing_per_replicate`]. A report-only breakdown reading (a replicate below 0.9 of its planned
demand in a battery averaging at least 0.95) is pre-registered beside the lock row
[PRE_FRISCO_PROGRAM.md, "Coordinator's decisions"]. p13's archive cannot be re-scored
[I24_VALIDATION.md §0].

### 3.7 What remains failing

The candidate fails its link-flow, speed and wave rows [artifacts/i24_validation_dc_refit_rc.json,
`criteria`]. Its k = 1 drivers lost the wave row in every I-24 arm that ran them and are
string-stable at capacity density (§2.1). Amendment 2's smaller shifts (uncorrected counts) kept the
waves but realised only 0.918 and 0.930 of the demand against a 0.977 floor, so k = 0 stayed the
I-24 reference [artifacts/i24_validation_dck025_refit.json, `…dck05_refit.json`,
`simulated.demand_realized_fraction`; FRISCO_PROTOCOL.md, Amendment 2]. Amendment 7's screen of
`a_max` and T together admitted 5 of 25 pairs, all at k = 0, and its Stop rule fired
[artifacts/driver_joint_screen_i24.json, `summary`, `stop_rule.fired`]. On that grid no `a_max` gain
keeps the instability CLAUDE.md §3.1 requires, yet the candidate carries one. All of it is fitted
and scored on one morning with no holdout day: calibration, not validation.

## 4. I-94: a public-data corridor that is not reproduced

### 4.1 Corridor and inputs

I-94 westbound through east St. Paul is built from public MnDOT loop data: 14 mainline stations on
nine weekday mornings of September 2026, split by the protocol's seeded draw into five calibration
and four validation days [ONBOARDING_MNDOT.md §4; artifacts/baseline_gate_mndot_dc.json, `split`].
Ramp detectors failed mass balance, so demand is closed bracket by bracket on mainline counts
[ONBOARDING_MNDOT.md §4–§5]. Early rounds were lost to network defects (ramps starved at plain
junctions where OpenStreetMap's lane tags left no acceleration lanes; exits compiled onto the
wrong lanes, which an automatic split audit now catches) [ONBOARDING_MNDOT.md §6, §9]. Inputs
built before 2026-10-07 averaged all nine mornings, so the validation days entered them at 4/9
weight [I94_CALIBRATION_DAYS.md §0–§2].

### 4.2 The gate record (rehearsals)

**Table 5.** The protocol's gate on I-94, 20 replicates per arm, configs under hash policy v3;
every gate fails [artifacts/baseline_gate_mndot_i94_wb_stpaul_p1.json,
artifacts/baseline_gate_mndot_dc.json,
artifacts/baseline_gate_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal{,_w1b,_w1b_w2}.json, `checks`,
`verdict`].

| Arm (stage; config) | Inputs | Weave rules | C1 cal / val (≥ 85%) | C3 cal / val (≤ 15%) | C4 km/h | C5 | C6 cal / val |
|---|---|---|---|---|---|---|---|
| k = 0 (p1; b550b46fe751) | nine days | off | 15.5 / 14.5% | 49.7 / 48.7% | 6.6 | 0 | fail / pass |
| k = 1 (step 3; db9fbab5fc6e) | nine days | off | 61.8 [56.5, 67.0] / 60.0 [54.0, 66.0]% | 33.9 / 38.8% | 4.9 | 0 | fail / pass |
| k = 1 (p8; beaaa710e6b3) | calibration days | off | 34.4 [30.9, 37.9] / 32.1 [29.0, 35.2]% | 41.7 / 41.8% | 5.5 | 2 | pass / pass |
| k = 1 (p10 A; 0d26de2a5f01) | calibration days | W1b | 36.2 / 33.2% | 38.4 / 37.8% | 5.7 | 1 | pass / pass |
| k = 1, reference since Amendment 4 (p10 B; 5080d84d4725) | calibration days | W1b + W2 | 36.1 [34.3, 37.9] / 33.0 [30.9, 35.1]% | 38.3 / 37.9% | 5.7 | 0 | pass / pass |

The k = 1 drivers (with keep-right 0.1) quadrupled C1 on the nine-day inputs. Rebuilt from the
calibration days alone, the same model fits worse: an inflated exit upstream of the T.H.52 weave had
drained traffic the weave cannot carry, and honest inputs expose the shortfall
[I94_CALIBRATION_DAYS.md, "Results of stage p8"]. A better fit is evidence only when its inputs are
right.

### 4.3 Locks, collisions and the weave rules W1b and W2

Three of 20 step-3 replicates ended in a permanent standstill at a weave gore (Clopper–Pearson
3–38%) that the battery's means did not reveal [I94_COLLAPSE_DIAGNOSIS.md §0, §5]. W1b, registered
before any code ran, lets an entrant that has stood 60 s at the end of the auxiliary lane take the
paired exit; its round (p9) passed every criterion: locks 3 → 0, S790 +7.2 veh/h [−9.6, +23.9], no
collision, releases of 0.54% of Ruth St's entrants [artifacts/weave_w1b_corridor.json, `criteria`].
On the calibration-day inputs p8 recorded six collisions inside the two weaves, each reproduced
exactly on re-run. Five fit mechanisms of the weave's own commands, inferred because the runner
logged no commands: speed targets that cap braking at comfortable deceleration and a leader inside
the minimum gap read as none (Ruth St), and same-step entries into one lane from opposite sides
(T.H.52); the sixth was a late cut-in [I94_CAL_COLLISIONS.md §15]. W2's three guards removed all
four reproducing collisions on stress fixtures (a seeded boundary), but W2 without W1b locked one
fixture run
[artifacts/weave_collision_guards_2026-10-07/criteria.json, `G7`, `G6`]. In p10 the W1b + W2 arm had
no collision in 20 runs against one with W1b alone, a difference the round cannot attribute to W2,
and W1b released 257 of 20,340 Ruth St entrants (1.26%) against its registered 1%; the verdict "W2
not adopted on this round" stands [artifacts/weave_w2_corridor.json, `criteria.CW3`,
`criteria.CW5`].

Amendment 4 then adopted both rules together, written after that failure and saying so. It moves no
threshold but turns the failed bound into a disclosed cost: every battery reports W1b's release
share per weave beside `no_locks`, never gating [FRISCO_PROTOCOL.md, Amendment 4;
DECISIONS_2026-10-07.md §A2]. Neither rule is measured driver behaviour. The defaults went on in
code with hash policy v4 (`8ea59ec`), although the amendment makes that wait for a reproduction
re-run of p10's guarded arm; the re-run is the pending range round's u = 0 arm, and no corridor
battery has yet run under the defaults [FRISCO_PROTOCOL.md, Amendment 4, "Before the default
flips", "Implemented"].

### 4.4 The T.H.52 weave and its unmeasured input

At S790, downstream of the T.H.52 weave, the reference carries 3,871 veh/h [3,853, 3,889] in
06:30–07:30 against 4,846 observed, no seed within GEH 5 [computed from `per_seed[].link_hours` of
artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2.json]. On a section fixture
(macOS, seeds 3–22) the weave carries 4,361 ± 83 veh/h against 4,826 ± 19 with nothing to cross, a
paired loss of 465 [427, 504] [computed from artifacts/weave_loss_2026-10-07/arms/{base,ceil}.json,
`rows[].exit_end_flow_vph`]; no single rule removal recovers more than 32 veh/h [+8, +57]
[WEAVE_LOSS_DIAGNOSIS.md §4–§5]. The anticipation reach, measured on I-24 at 125 m [107, 154],
rounds to the model's 120 m and closes that route [artifacts/merge_anticipation_i24.json,
`preregistered_proposal`; WEAVE_LOSS_DIAGNOSIS.md §9]. The share of entrants bound for the next exit
has no count. The proportional split gives 0.29 in the fixture's window and 0.18 in the peak hour,
and the working range runs to 0.70, an assumed bound [TH52_CROSSING_SHARE.md §4–§5]; across it the
fixture's flow check passes at 1, 9, 20, 20 and 20 of 20 seeds (proportional, 0.40, 0.50, 0.60,
0.70) [artifacts/th52_crossing_share_2026-10-07/summary.json, `*.geh_lt5`]. Amendment 3 therefore
keeps the gate at the proportional split, will report each affected headline with its value at 0.70
beside it once the range round has run, and never chooses a share from it [FRISCO_PROTOCOL.md,
"Adoption of Amendment 3"]. The Ruth St split is unexamined.

### 4.5 The gate as it stands

The reference fails C1, C3 and C4 on calibration days and C1 and C3 on validation days, and passes
C5 and C6 (Table 5). Its `stack` wave speed, 5.7 km/h [5.5, 5.8], measures the queue tail's growth,
against 19.1 km/h observed from 7 of 13 station pairs
[artifacts/baseline_gate_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2.json, check C4;
I94_RESIDUALS.md §4]. The corridor is not reproduced.

## 5. Transfer: merges on a site never calibrated on (E11)

E11 compared the kept configuration and `merge: measured`, whose inputs come from I-24 and NGSIM
US-101, with the merges at the Powell Street on-ramp of raw NGSIM I-80, fitting nothing: a replica
built as US-101's was, with counted demand, the measured downstream boundary and the I-24 fleet
[I80_MERGE_VALIDATION.md §2–§3]. The criteria were fixed before the run (Amendment M1): E1
partner-speed signs match; E2 the new follower and the leader side sit at ≤ 0.9 and ≤ 0.8 of a
normal gap at the change and recover by 10 s; E3 the model's 20-seed interval overlaps I-80's
bootstrap interval for six medians; E4 zero collisions. `measured` is rescued only if it meets all
four while the kept configuration fails one [MERGE_MODEL.md, M1].

**Table 6.** E11, 102 observed entering changes; model values are means of per-seed medians with
95% t-intervals [artifacts/i80_merge_validation.json, `criteria.{kept,measured}.{E2,E3,E4}`,
`rescue`; I80_MERGE_VALIDATION.md §4–§5].

| Medians at the change | I-80 [bootstrap 95%] | Kept (LC2013) | `measured` |
|---|---|---|---|
| Accepted lead / lag gap [s] | 1.35 [1.15, 1.52] / 1.68 [1.47, 1.87] | 2.82 [2.75, 2.88] / 5.15 [4.99, 5.32] | 1.38 [1.33, 1.42] / 2.25 [2.21, 2.30] |
| Critical gap, lead / lag [s] | 0.24 [0.08, 0.42] / 0.34 [0.07, 0.58] | 0.63 [0.55, 0.71] / 1.35 [1.18, 1.52] | 0.19 (18 of 20 seeds) / 0.31 (9 of 20): underpowered |
| Partner speed, follower / leader [m/s] | +0.34 [0.00, 0.55] / +0.15 [−0.08, 0.43] | +1.73 [1.46, 2.00] / +1.36 [1.26, 1.45] | −0.02 [−0.09, 0.04] / +0.25 [0.20, 0.29] |
| E3 overlaps | — | 0 of 6 | 3 of 6 |
| E2 gap over normal, follower / leader (≤ 0.9 / ≤ 0.8) | 0.77 / 0.58 | 1.09 / 1.13 | 0.92 / 0.57 |
| E1 signs; E4 collisions | — | match; 0 | match; 0 |

Both arms fail E2 and E3: `measured` is not rescued and is retired by the rule
[artifacts/i80_merge_validation.json, `rescue.rescued`]. The kept configuration is not validated
either. LC2013 merges by stopping and crossing ahead of a near-stationary follower; I-80's drivers
merge in a rolling squeeze at about 3.6 m/s [I80_MERGE_VALIDATION.md §6]. The rule picks which
model to keep, not which is better, and `measured` came closer here. Before E11 is cited outside the
project, one gap needs checking: the extraction finds 113 in-zone entering changes against 421
counted ramp entries [I80_MERGE_VALIDATION.md §8].

## 6. Reproducibility and platforms

Every run writes a `meta.json` with its seed, package versions, calibration provenance and
configuration hash, the first 12 hex characters of a sha256 over the configuration's non-default
fields and the policy version [CONTRACTS.md §2]. A default changed inside a weave block does not
show in that payload, so Amendment 4 bumped the policy to version 4 and moved every hash once (the
ring scenario's from d5472987265c to 258c09ac0074) [CONTRACTS.md §2, "Policy v4"; recomputed].
`config_hash_v3` reproduces the version-3 hashes of the 54 pinned scenarios and of 44 committed
records, and `config_hash_v2` those of 15 older ones; two records older than their scenario's last
rewrite reproduce under no policy [CONTRACTS.md §2, "Policy v4"]. A hash names one physics. Each
round's same-code reference reproduced its committed battery exactly across machines: p13's the
step-3 battery, p14's the p13 battery [artifacts/boundary_b2_corridor.json,
`reference_reproduces_committed`; artifacts/boundary_b1b2_corridor.json,
`part_i.reference_reproduces_p13`]. Goldens are per SUMO version, pinned at 1.27.1 [CLAUDE.md §9].

Determinism holds per platform, not across platforms. E12 hashed every vehicle's state and every
runner command at every 0.5 s step of five weave fixtures on arm64 macOS and x86_64 Linux
[E12_PLATFORM_TESTS.md §4]. SUMO's state diverged first, at steps 20–47, while the runner's
commands stayed identical for 30–121 more steps: the divergence is SUMO 1.27.1's arithmetic
[computed from `steps` of artifacts/e12_platform_2026-10-07/*_{darwin-arm64,linux-x86_64}.json;
E12_PLATFORM_TESTS.md §7.3]. Each platform is deterministic, three runs giving byte-identical
records. Three of 19 fixture tests change outcome by platform and now carry strict per-platform
marks, with no assertion, threshold, golden or hash changed; CI confirmed macOS 8 passed / 11
xfailed and Linux 5 passed / 14 xfailed in each of three runs [E12_PLATFORM_TESTS.md §7.5, §8; §8
was uncommitted when this draft was written]. Fixture results here are macOS records; corridor
batteries run on Linux. Fixture configs hash by checkout path; committed scenarios do not [§7.6
there]. The cloud snapshot commits of p13 and p14 are not in the repository; since `b50211d` the
readouts record the source commit [I24_DISCHARGE_DIAGNOSIS.md §8.4.6].

## 7. Limitations and pending work

1. **No corridor is validated** (§3–§5).
2. **One morning on I-24.** Every I-24 result is calibration. With that day pinned to calibration
   (Amendment 9), two more mornings give one calibration and two validation days, below the
   protocol's 5 / 3 [FRISCO_PROTOCOL.md, Amendment 9].
3. **Coverage.** I-24 counts are lower bounds; every flow target rests on a coverage estimate.
4. **Uncorrected ramp counts.** Every I-24 result except the B2 arms, including Table 3 and the
   long draft's controller and strategy runs, used inputs carrying through traffic
   [DECISIONS_2026-10-07.md §A1].
5. **Drivers.** The I-24 candidate and the I-94 reference run k = 1 drivers, string-stable at
   capacity density against CLAUDE.md §3.1; I-94 and I-80 run a transferred population.
6. **Boundary.** The measured downstream speed acts as every driver's desired speed on the last
   edge; B1 is not adopted, and no factor is defined for I-94's EIDM fleet
   [I24_DISCHARGE_DIAGNOSIS.md §8.3].
7. **Weaves.** W1b and W2 are model guards, not measured behaviour; W1b's release share exceeded its
   bound once; the T.H.52 share is bounded by assumption and Ruth St's is unexamined.
8. **Merges.** The locked configuration misses I-80's gaps and partner speeds, measured on raw,
   not reconstructed (Montanino–Punzo), NGSIM data.
9. **Delegation.** The amendments of 2026-10-07 are the coordinator's; Amendment 4 followed a
   failed clause, and its defaults went on before the re-run it names.
10. **Platforms.** Only darwin-arm64 and linux-x86_64 are measured.

**Table 7.** Pre-registered rounds. No readout is committed as of `466193f`; each is reported with
its criteria, never a guessed value [PRE_FRISCO_PROGRAM.md; FRISCO_PROTOCOL.md, Amendments 3, 6, 9,
10; I24_CONSISTENCY_C7B.md §6; A3_RANGE_ROUND.md §5].

| Stage | Round | Criteria fixed before launch | Status |
|---|---|---|---|
| p23 | C7b, I-24: lane-set scorer, coverage-consistent demand at B2's planned level (s = 1.080413, computed), a computed 75.7 s insertion shift | R1–R5 against a B2 re-run that must reproduce p13 exactly; B5's arm is B2 or the arm holding R1–R5 with the largest pooled station-hour share on lanes 1–4 | PENDING (built, not run) |
| p15 | B5, I-24 (Amendment 6): level fitted on fit-hour GEH < 5 share, five seeds per scale, on p23's arm | C1 no collision; C2 realised ≥ from-arm − 0.01; C3 GEH share not lower; C4 15-min RMSPE ≤ from-arm + 0.02; C5 wave verdict kept; Stop on `constraint_unmet` | PENDING (built, not run) |
| p16 | D10, I-94 (Amendment 10): T.H.61 NB from its segment's mainline difference; S792 out of the demand balance | per arm: no collision or lock; realised ≥ reference − 0.01; calibration-day C1 not lower; C3 ≤ reference + 0.02; B5's I-94 arm is `_rbc` if it holds, else `_rb`, else the reference | PENDING (built, not run) |
| p17 | B5, I-94: one corridor-wide factor, 0.95–1.05 | as p15, with gate C1/C3/C4 and a no-lock check | PENDING (waits on p16) |
| p20 | C9: two more I-24 mornings and the day split (Amendment 9) | gate C1, C3, C4, C5 on the validation days, reported only | PENDING (waits for the owner's download) |
| p24 | Amendment 3's range round: T.H.52 share at u = 0, 0.5, 1, with and without W2; the W1b + W2 family's u = 0 arm is Amendment 4's re-run | material if, at u = 1 against u = 0: (M1) S790 06:30–07:30 lower bound ≥ +100 veh/h with realised demand at most 1 point lower; (M2) S790's or S97's GEH < 5 count moves by ≥ 5 of 20 seeds in an hour; (M3) calibration-day C1 or C3 paired interval wholly beyond ±2 points; (M4) a collision or lock in one arm only. Not material if none holds and the S790 interval lies within ±100 veh/h; else inconclusive. Never a chosen share | PENDING (built, not run) |

Not to be run: B6's cloud stage (p18; Stop rule fired) and C8 (p19; not selected by C7).
Pre-registered but not staged: B2 on the k = 0 arm, an Old Hickory flag audit, external ramp counts
[FRISCO_PROTOCOL.md, Amendment 5]. Deleting `merge: measured` awaits the owner.

## Figures

The I-24 figures show the first seed of the 2026-09-05 four-arm re-run, on uncorrected ramp counts
[PAPER_DRAFT.md, Appendix B].

1. `docs/figures/i24_wb_overview.png`: the recorded morning, mean tracked speed on 60 s × 100 m
   bins and crossing counts [I24_DATA.md §3].
2. `docs/figures/i24_validation_fields.png`: observed and simulated speed fields of the four arms
   [I24_VALIDATION.md §0.2].
3. `docs/figures/i24_validation_waves.png`: backward front speeds by arm with the criterion
   detector's estimates [I24_VALIDATION.md §0.2].

## References

From CLAUDE.md §13 and the long draft's list; "[to complete]" marks details to check before
submission.

- Chen, Skabardonis & Varaiya (2004). Active-bottleneck identification.
  *Transportation Research Record* 1867, as summarised in NCDOT report 2016-10. [title to complete]
- CIRCLES MegaVanderTest, I-24, November 2022 (BAIR blog, 2025-03-25). [to complete]
- Clopper–Pearson exact binomial interval. [to complete]
- FHWA (2004). *Traffic Analysis Toolbox Volume III*, FHWA-HRT-04-040.
- FHWA (2019). *Traffic Analysis Toolbox Volume III* (update), FHWA-HOP-18-036.
- Gloudemans et al. (2023). I-24 MOTION testbed. *Transportation Research Part C* 155:104311;
  arXiv:2302.12308. [title to complete]
- Highway Capacity Manual, Edition 7.1, Chapter 13 (weaving; the proportional split). [to complete]
- Kesting & Treiber (2008). Car-following calibration methodology. [to complete]
- Lighthill & Whitham (1955); Richards (1956). The LWR model. [to complete]
- Montanino & Punzo. Reconstructed NGSIM trajectories. [to complete]
- NGSIM I-80 and US-101 data, data.transportation.gov resource `8ect-6jqj`.
- Stern et al. (2018). *Transportation Research Part C* 89:205–221; arXiv:1705.01693.
  [title to complete]
- Sugiyama et al. (2008). *New Journal of Physics* 10:033001. [title to complete]
- Treiber, Hennecke & Helbing (2000). The Intelligent Driver Model. [to complete]
- Treiber & Kesting (2013). *Traffic Flow Dynamics*. [to complete]
- Troutbeck's maximum-likelihood critical-gap estimator. [to complete]
- TxDOT TSAP, chapter 13 (the GEH < 3 profile `txdot_tsap_ch13` of `validation.criteria`).
  [to complete]
