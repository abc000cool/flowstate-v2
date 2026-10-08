# Corridor study protocol: the rules, locked before any results

Version 1, written 2026-10-04 (Stage 1, item 7 of the Frisco plan), before
any Frisco data was requested or received and before any simulation of a
Frisco road. It applies to the Frisco pilot and to every later client
corridor study. It fixes, in advance, which data are used, which hours and
days calibrate the model and which test it, how each check is measured, and
what the model must show before any strategy result is reported.

**Change control.** This file is committed before the data arrive. A change
after the data arrive is a dated amendment at the end of this file, with its
reason; the study then reports its results under both the original and the
amended rule. Nothing in this protocol may be changed after simulation
results for the corridor have been seen in order to make a check pass
(CLAUDE.md §0; the owner's standing rule "never tune a scenario to force a
pass").

Labels used below: **[federal]** a published acceptance target (FHWA or a
state DOT, with its source in `validation.criteria`); **[FlowState]** an
internal rule of this protocol, stated as such in every report.

---

## 1. Scope fixed with the agency before analysis

1. One highway stretch, one direction, one period of the day, and one study
   question, agreed in writing with the agency
   ([FRISCO_DATA_REQUEST.md](FRISCO_DATA_REQUEST.md) §1).
2. Highways only: no signalised arterials (FlowState does not model signals;
   Stage 3 item 29).
3. The stretch's layout must be supported (on- and off-ramps, auxiliary
   lanes, lane drops, weaving sections). A layout the engine cannot
   represent is reported as a limitation before work starts, not discovered
   in the results.

## 2. Data quality and detector selection (items 2 and 3)

1. Every detector is checked with `calibration.data_quality` (missing data,
   stuck readings, impossible values, inconsistent flow/occupancy/speed,
   outlier days, station-to-station mass balance). Its verdict per
   detector-day (ok / suspect / exclude) and every reason are written to the
   study's data-quality artifact. A window with no vehicle counted is judged
   by its occupancy, never its speed field: an empty loop (occupancy ≤ 1 %)
   where traffic is expected is a dead loop or a closure; an occupied loop
   is a standstill (traffic, not a fault) when the stop lasts at most 5
   minutes at ≥ 50 % occupancy or a neighbouring lane or station reads
   congestion within 5 minutes; otherwise it is a hanging-on loop.
2. **Mainline stations used for scoring:** every station inside the stretch
   whose verdict is not "exclude" on at least 80 % of the candidate days
   (§3.1, before the split) within the study period [FlowState]. *(Clarified
   2026-10-04, before any data: the first wording used the calibration
   days, which are drawn from days judged on the selected stations — a
   circle; the candidate days break it.)* Every "exclude" detector-day, and
   every window and quantity a "suspect" verdict sets aside, is masked out
   of the observed targets before they are averaged; the targets record
   which (`source.quality`: the data-quality artifact's path and sha256,
   each masked detector-day with its checks, the readings set aside), and a
   target the artifact does not cover is not built (tools:
   `calibration.station_selection`, `scripts/station_selection.py`). The list is written to the
   corridor's `selection.json` and committed **before the first simulation
   of the corridor**. A station may not be dropped later unless a data
   defect is found; the drop is then a dated amendment and results are
   reported with and without the station.
3. **Ramp volumes:** a ramp detector is used if it passes the data-quality
   checks and the mass balance of its segment closes within the tolerance
   of `calibration.data_quality`. Otherwise the ramp's volume comes from
   `calibration.ramp_estimation` (mainline differences), and the report
   lists it as an estimate with its interval and every split assumption. If
   a segment has more than one unmeasured ramp and no defensible split, the
   study says the ramp volumes there are unidentified, uses the agency's
   best available count or a documented assumption, and tests how much the
   results depend on it (item 11).
4. Detector choice, ramp-volume method and every exclusion are recorded
   before calibration starts.

## 3. Days and hours: calibration versus validation (item 7)

1. **Candidate days:** Tuesday, Wednesday and Thursday, not public holidays,
   not days with an incident or weather event affecting the stretch during
   the study period (agency logs), and not days where fewer than 80 % of
   the selected stations are usable in the study period [FlowState]. A date
   the data-quality artifact does not cover is not a candidate; the split
   refuses it unless it is explicitly left out.
2. **The split:** candidate days are stratified by their mainline volume
   in the study period (station-mean volume, so a missing station does not
   push a day down) into terciles. 60 % of all candidate days, rounded
   down, go to **calibration**, allocated across the terciles in proportion
   to their size by largest remainder, at least one per tercile whenever
   the total allows, ties broken in a seeded order; within each tercile a
   seeded random draw (seed **20261004**, `numpy.random.default_rng`)
   picks which days; the rest go to **validation** [FlowState]. The split
   is computed by script (`calibration.day_split`, `scripts/day_split.py`),
   committed with the selection, and never redrawn. *(Corrected
   2026-10-04, before any data: the first wording rounded 60 % down inside
   each tercile, which with three-day terciles sends one day in three to
   calibration; found while implementing it.)*
3. **Minimum:** 5 calibration days and 3 validation days. With fewer on
   either side, the study proceeds but states that it is underpowered and
   on which side.
4. **Hours:** the study period agreed in §1 (for example 15:00–19:00), the
   same on calibration and validation days. The simulation starts earlier by
   a warm-up of at least 30 minutes, or twice the free-flow travel time of
   the stretch if longer; the warm-up is never scored.
5. **Targets:** the calibration target is the mean over calibration days per
   5-minute window (the existing observations artifact,
   `aggregation: mean over dates per window`). Validation scores the
   calibrated model, unchanged, against the mean of the validation days, and
   also day by day so the spread across days is visible.

## 4. The checks and how each is measured

All checks are scored over at least 20 seeded replicates, reported as means
with 95 % confidence intervals (CLAUDE.md §0.6).

| # | Check | Pass rule | Label |
|---|---|---|---|
| C1 | Link flows | GEH < 5 on at least 85 % of station-hour comparisons (`validation.criteria` profile `fhwa_default`); hours are anchored at the study period's start (the warm-up's end), so the whole study period is scored | [federal] FHWA TAT Vol. III 2004 |
| C2 | Link flows, Texas | GEH < 3 on every station-hour (profile `txdot_tsap_ch13`); reported beside C1, not part of the gate | [federal] TxDOT TSAP ch. 13 |
| C3 | Speeds | RMSPE ≤ 15 % on station mean speeds at **15-minute** aggregation; 5- and 60-minute values reported as diagnostics (`speed_aggregation_rows`) | common practice, cited in the report |
| C4 | Wave speed | the simulated backward wave speed (profile detector `STACK_DETECTOR`) within 14–22 km/h, with a backward front found in at least 80 % of the replicates (a replicate without one counts as a miss) and the 95 % interval reported; the observed speed from detector cross-correlation (`calibration.waves_observed`) reported beside it. If the observed data show no recurrent waves (fewer than three station pairs with a valid cross-correlation on calibration days), C4 is "not applicable", decided from the observed data before any simulation | empirical literature |
| C5 | Collisions | zero SUMO collisions in every run; a run set without the counter is "not recorded", never a pass | [FlowState] CLAUDE.md §3.3, owner decision 2026-10-04 |
| C6 | Bottlenecks | see §5 | [FlowState] |

The speed aggregation (15 minutes) is fixed here, in advance, because
5-minute station speeds carry a noise floor of their own (the I-24 record,
docs/I24_VALIDATION.md); choosing the aggregation after seeing results would
be tuning.

## 5. Reproducing where and when the slowdowns form (item 9, check C6)

**Active bottleneck** (after Chen, Skabardonis & Varaiya 2004, TRR 1867, as
summarised in NCDOT report 2016-10): in a 5-minute window, a bottleneck is
active between two neighbouring selected stations when the upstream station's
speed is below 40 mph (64.4 km/h) and the downstream station is at least
20 mph (32.2 km/h) faster. It counts as activated when that holds in at least
5 of 7 consecutive windows. *(The 40 mph and 20 mph thresholds are confirmed
from the secondary source; the 5-of-7 persistence rule is taken from a
secondary summary and must be checked against the original paper before the
first Frisco run. Until then it is a FlowState rule.)*

Applied identically to the observed calibration-day mean and to each
simulated replicate (virtual detectors at the selected stations' positions:
the mean speed of the vehicles crossing the station's position in the
5-minute window, as a loop reads it — not a segment average; a run set
without these point readings is scored on segment means only with the
substitution stated):

1. **Location:** each observed bottleneck active for at least 30 minutes is
   reproduced at the same station pair or an adjacent one in at least 80 %
   of the replicates.
2. **Timing:** the median simulated activation time is within 15 minutes of
   the observed one, and the median active duration within 30 % of it.
3. **Queue reach:** the furthest upstream station that is below 40 mph at
   the observed queue's longest extent is reproduced within one station.
4. **No phantom bottleneck:** at most 50 % of the replicates contain any
   bottleneck active for more than 30 minutes that is not at, or adjacent
   to, an observed one (counted per replicate, so a phantom that moves
   between neighbouring station pairs is counted once per replicate).
   Observed and simulated bottlenecks are matched one to one.

All four hold → C6 passes. These thresholds are FlowState rules and the
report says so.

## 6. The gate (item 9)

The **baseline gate** (no strategy on, `validation.baseline_gate`) passes
only if C1, C3, C5 and C6 pass on the calibration days, and C1, C3 and C6
pass on the validation days, with C4 passing or "not applicable".

- **Gate passes:** strategy results may be reported (§8).
- **Gate fails:** the report states which checks failed and by how much,
  shows the observed-versus-simulated comparison, and contains **no strategy
  recommendations**. Strategy runs may still be made for internal learning;
  they are not delivered as findings.

## 7. What calibration may change, and nothing else

1. **Demand:** boundary and ramp inflows, by the GEH-driven demand fit
   (`calibration.demand.fit_inflow`), within the count uncertainty of the
   data-quality artifact.
2. **Driver population:** one of the measured populations (I-24 MOTION,
   NGSIM US-101, and any later one), with a single corridor-wide adjustment
   inside the measured ranges if the driver-settings check (item 8,
   `calibration.transfer_check`) flags a mismatch in free-flow speed,
   capacity per lane or truck share — the population's mean time headway or
   mean desired speed, or the passenger speed factor on the posted limit
   (`fleet.speed_factor`, for drivers who exceed the limit). A knob's
   measured range is the mean
   ± 1 standard deviation of the measured source population, inside
   CLAUDE.md §3.1's calibration range; a mismatch that no value in that
   range removes is reported as such, not forced. No location-specific
   driver settings.
3. **Map:** corrections only for verified layout errors found by the layout
   checklist (item 4, docs/LAYOUT_CHECKLIST.md), each with the imagery or
   plan it rests on.
4. **Lane changing and merging:** fixed at the values locked by the merge
   fix (item 5, §9). Never tuned per corridor.

Every calibration run is logged with its config hash; the report states how
many calibration iterations were made.

## 8. Comparing strategies fairly (items 10 and 11)

1. **Same everything:** every strategy and the do-nothing baseline run on the
   same demand, the same seeds and the same scoring window.
2. **Waiting counts:** travel time and delay include time spent waiting on
   ramps (meters) and waiting to enter the simulation (insertion backlog);
   a strategy cannot look good by holding cars off the road. Definitions
   (`validation.metrics.WaitingMetrics`, written before any result): every
   vehicle of the demand planned to depart in the scoring window is
   measured from its planned departure; time in system = arrival (or the
   run's end) − planned departure; delay = time in system − the free-flow
   time of the route stretch it covered at its desired speed there,
   min(v0, its speed factor × the base speed limit). A vehicle not arrived by the run's end is censored there,
   by the same rule in every arm, and the censored count is reported per
   arm. Runs end with a cool-down after the last scored departure of at
   least the stretch's free-flow travel time, so that censoring is rare.
   Mean and 90th-percentile travel times are taken over the vehicles that
   could have finished on an empty road before the end (a rule on the
   demand alone) and are stated as lower bounds when any is censored.
3. **Same measures:** throughput at the reference section, mean and 90th
   percentile travel time, total delay including waiting, σ_v, wave count
   and amplitude, collisions, and fuel (labelled a model estimate).
4. **Tuning:** every strategy gets the same tuning budget (the same number
   of candidate settings), tuned on **tuning seeds** and evaluated on
   separate **evaluation seeds** (at least 20) never used in tuning. The
   tuning objective is fixed before tuning starts: total delay including
   waiting time (vehicle-hours over the study period, all vehicles of the
   demand, §8.2), unless the agency's question (§1) names another measure,
   in which case that measure is written here as a dated amendment before
   the first tuning run. A setting with any collision is disqualified; a
   setting whose throughput at the reference section is lower than the
   baseline's (the paired difference's 95 % interval entirely below zero)
   is reported but cannot be selected as the strategy's best. The N
   candidates of each strategy are its textbook setting and the first N − 1
   points of the unscrambled Halton sequence on a parameter box declared in
   `scripts/strategy_tune.py` before tuning; tuning seeds come from a seed
   stream separate from the evaluation seeds and the two lists are checked
   to be disjoint; a tie goes to the lower candidate index.
5. **Uncertainty:** the best setting of each strategy and the baseline are
   re-run with the driver settings and demand varied within their plausible
   ranges (item 11): at least 10 parameter samples (Latin hypercube) of
   driver population and demand scaling within their measured
   uncertainty, each with at least 5 seeds. The ranges are fixed before any
   run: demand, one corridor-wide factor within ± the data-quality
   artifact's recorded count error; the population's mean time headway and mean
   desired speed, the values whose model capacity per lane and free-flow
   speed stay inside the observed 95 % intervals of the driver-settings
   check (item 8), widened to include the configured (calibrated) value and
   clipped to the §7.2 measured range — where the check gives no interval
   or reads capacity off its analytical index rather than a simulated
   capacity, the §7.2 range itself, labelled assumed; truck share,
   the classification-count interval, else ± 3 points labelled assumed.
   (The §7.2 range is the spread of individual drivers, the range
   calibration may choose from; it is not the uncertainty of a calibrated
   corridor and is used here only as a labelled fallback.) A strategy's
   effect is called robust only if its sign holds in at least 90 % of the
   samples, a sample without an estimate counting against it; otherwise
   the report says the effect is uncertain. Designs below 10 samples × 5
   seeds are rehearsals and state no verdict.
6. **Fuel:** model estimates (SUMO's HBEFA emission classes), never measured
   fuel; reported as estimates until Stage 2 item 20 is done.

## 9. Merge acceptance, fixed before the merge fix (item 5)

The new merge model (Stage 1 item 5, phase 2) is accepted only if all of
these hold, each with at least 20 seeds unless stated:

1. **Nashville (I-24 MOTION) problem merge:** the simulated throughput at
   capacity matches the observed ≈ 6,600 veh/h with GEH < 5 (today ≈ 5,900).
2. **Minnesota T.H.52 weave:** the simulated section carries its observed
   flow (S790 ≈ 4,900 veh/h in the corridor's peak) with GEH < 5.
3. **Minnesota T.H.52 section test** (`test_th52_corridor_section_carries_free_flow_demand`),
   with criterion (ii) as revised on 2026-10-04 (owner's delegation, "do
   what you think is best"): the per-lane 20 m/s floor at the section's last
   60 m is replaced by the station-level checks a loop detector actually
   measures — (ii-a) the hourly flow through the section's exit end (all
   lanes, after a 120-s fill) within GEH < 5 of the observed inflow (S790
   plus the T.H.52 entrance count; nothing leaves before the gore), and
   (ii-b) the vehicle-weighted mean speed across all lanes at the exit end
   above the 20 m/s free-flow bound in every 5-minute window. The per-lane
   floor stays as a reported diagnostic. Under this reading the test still
   fails at today's model (seeds 3 / 4 / 5: 4,100 / 3,813 / 3,697 veh/h
   against 4,877, GEH 11.6 / 16.1 / 18.0), so the revision is not a pass
   made easier.
   Criteria (i) demand departs, (iii) no collision and (iv) at most 2 %
   missed exits are unchanged.
4. **Zero collisions** (C5) on every merge test and corridor battery.
5. **No lock:** no seed of a 20-seed four-hour I-94 battery collapses
   (departed share of demand below 0.8 of the battery's median).
6. **One model:** the switches the merge fix replaces are deleted, not left
   off.

## 10. What the report contains whatever the outcome

The client report (`validation.report`, client summary section) states the
gate result first, then: what we are confident about and what we are not,
every excluded detector and assumption, the calibration/validation split,
and the limitations (single corridor, model-form uncertainty, fuel as an
estimate, the compliance or strategy assumptions). Results are reported as
they come out, including failures.

---

## Amendments

### Amendment 1 — 2026-10-06: two more driver settings may be calibrated

Written before any run that uses them; it applies to every corridor.

**Why.** (1) A diagnosis on bottleneck fixtures (docs/DISCHARGE_CALIBRATION.md §1) found the
model's capacity drop inside the observed and published range (Cassidy &
Bertini 1999: discharge up to about 10 % below the pre-queue flow), but its
merge **discharge level** about 12 % below I-24's recorded one, set by the
population's mean maximum acceleration `a_max`, which no calibration step has
ever adjusted; mean T cannot reach it without inflating pre-breakdown
capacity. (2) The fleets run SUMO's keep-right eagerness `lc_keep_right` = 0,
set on I-24 in 2026-09 from observed lane shares (SUMO's default 1.0 crowded
the two right lanes at the Old Hickory merge) but justified by the false claim
that US freeways have no keep-right rule; Tennessee (Code §55-8-115),
Minnesota (Stat. §169.18 subd. 10(b)) and Texas (Transp. Code §545.051(b))
all require slower traffic to keep right, and at 0 slow drivers hold the left
lanes (the T.H.52 section test's ceiling, docs/MERGE_MODEL.md A3).

**What §7.2 adds.**
1. **Mean `a_max`** of the driver population, inside mean ± 1 sd of the
   measured source population (`artifacts/idm_i24.json`: 1.055 ± 0.43 m/s²),
   as a derived population (mean shifted, covariance unchanged), on the grid
   mean + k·sd, k ∈ {0, 0.25, 0.5, 0.75, 1.0}.
2. **`lc_keep_right`**, on the grid {0, 0.1, 0.25, 0.5, 1.0}.

**Calibration targets and selection rule (fixed now).** Per corridor, on the
calibration data only (I-94: the five calibration days of the committed split;
I-24: its one recorded morning, which has no holdout — stated in every
report), every grid pair is run (I-24: one seed; I-94: the 35-minute slice,
two seeds). (a) **Lane use:** the root-mean-square error of the lane shares of
vehicle-time over the measured segment (I-24: the Old Hickory merge area as
the 2026-09 calibration measured it, observed 30/24/20/26 %; I-94: per-lane
detector shares at the mainline stations). (b) **Discharge:** the flow at the
corridor's downstream peak sections while the bottleneck is active (I-24:
data x 2,200 / 3,200 m against 6,626 / 6,639 veh/h; I-94: S97 against its
observed discharge on the calibration days). **Rule:** among the pairs whose
lane-share RMSE is within 1 percentage point of the grid's minimum, choose the
one whose discharge error is smallest; ties go to the smaller change (k, then
`lc_keep_right` nearer its current 0). The chosen values are recorded with the
grid in an artifact before any acceptance run, and the acceptance runs (the
baseline gate) use them unchanged. If no pair improves lane use or discharge
against the current setting, the current setting stays and the report says so.

**Clarifications of Amendment 1 — 2026-10-06, before any grid run** (found
while implementing it; neither changes a target value):
- *I-24 lane-use segment.* "The Old Hickory merge area as the 2026-09
  calibration measured it" is read as that calibration's measured segment —
  the whole measured span, data x 0–5,500 m, 06:30–08:30, lanes 1–4 counted
  from the left, the auxiliary lane dropped and the four renormalised — the
  only segment that gives the quoted 30/24/20/26 % (exactly 30.33 / 24.16 /
  20.06 / 25.46 %). The merge area alone (750–2,000 m: 28.0 / 23.4 / 19.0 /
  29.6 %) is reported beside it, not scored.
- *I-94 "while the bottleneck is active".* On the calibration-day mean S790
  is below 40 mph in every scored window of the slice, but S97 is never the
  §5 test's 20 mph faster; the slice's fixed window (07:05–07:35) is used as
  the discharge window, against S97's observed mean of 4,490.5 veh/h there,
  and the per-window activity flags are reported, not used to select windows.
- *"Improves"* means a strictly smaller error than the current setting (k = 0,
  `lc_keep_right` = 0), with no noise band.

### Amendment 2 — 2026-10-07: PROPOSED, not adopted (I-24 wave constraint on the driver shift)

Written by the coordinator during the owner's absence, before any run that uses
it; the runs it names are diagnostics, and adopting its outcome for any
corridor needs the owner.

**Why.** Step 3 of Amendment 1 (docs/DISCHARGE_CALIBRATION.md §4) found that
the chosen shift k = 1 improves the I-94 gate strongly but, on I-24, loses the
emergent-wave criterion even with a demand refit (the criterion's slant-stack
detector finds no qualifying peak; 6.65 jam components per replicate on the
standard detector (5.05 with a backward front) against 9.05 (7.3) under the
reference drivers), while flows and speeds fit slightly better (but, as
its clarification below records, partly by holding vehicles off the network)
(GEH < 5 25.7 % against 21.5 %; RMSPE 33.3 % against 37.2 %). CLAUDE.md §3.1
requires the calibrated fleet to stay string-unstable near capacity, and a
shift chosen on discharge and lane use alone cannot see that.

**What it adds.** On I-24 only, mean `a_max` shifts k ∈ {0.25, 0.5}
(`lc_keep_right` 0, Amendment 1's I-24 choice), each with its own demand refit
by the fitter that set 0.800 and 0.925 (`scripts/i24_fit_demand_scale.py`,
corrected profile, 06:30–07:30 fit, 07:30–08:30 held out) and a 20-seed battery
(`scripts/i24_validate.py`; pipeline stage `p7_i24_amax_wave`).

**Rule (fixed now).** An arm qualifies when its battery passes the wave
criterion (stack detector, 14–22 km/h). Among qualifying arms, choose the
largest k whose GEH < 5 share and segment-speed RMSPE are both no worse than
the reference arm's (`artifacts/i24_validation_flow_speedcal_ref.json`: 21.5 %,
37.2 %). If none qualifies, k = 0 stays. Every arm's three readings are
reported whatever the outcome; the I-24 recording is one morning with no
holdout, so any choice here is calibration, not validation, and is reported as
such.

**Clarification of Amendment 2 — 2026-10-07 09:11 UTC, before any p7 result
was available or read** (the p7 bucket held only its inputs; the pipeline
uploads a stage's results only when the stage ends, at 09:53 UTC). *Corrected
by the 2026-10-07 regression review:* the first wording said "before any p7
result existed", which is false — the k = 0.25 battery had already been written
on the VM at 09:05:37 UTC (`artifacts/i24_validation_dck025_refit.json`,
`created_at`), about 5.5 minutes before this clause, though not uploaded or
seen. The clause decides the outcome for k = 0.5 (which meets the original rule
without it); it was motivated by the k = 1 refit's backlog, found earlier and
independently, and a reader should weigh the timing accordingly. The 2026-10-07 regression
review found that a demand refit can improve segment-speed RMSPE by holding
vehicles off the network: under k = 1 the refit raised demand 15.6 % but the
peak sections' flow did not move (6,031 / 6,025 → 6,047 / 5,983 veh/h) while
the realised share of planned vehicles fell from 0.996 to 0.921 and mean travel
time rose from 536 to 669 s. A qualifying arm must therefore also keep its mean
realised demand share (`simulated.demand_realized_fraction`, mean over the 20
replicates) no more than 1 percentage point below the reference arm's (0.987,
`artifacts/i24_validation_flow_speedcal_ref.json`), i.e. at least 0.977; the
backlog is reported for every arm.

### Amendment 3 — 2026-10-07: proposed; ADOPTED 2026-10-07 (the ramp-to-ramp share at a weave with no origin–destination count)

*Adopted 2026-10-07 at 21:20 CDT by the coordinator under the owner's delegation, as proposed, with the
additions and the pre-registered range round in "Adoption of Amendment 3" at the end of this file. The proposal
below is unchanged.*

Drafted by the coordinator during the owner's absence, while the fixture
sensitivity of docs/TH52_CROSSING_SHARE.md §10 was running and before any of
its results were read, and before any corridor run that varies the share. The
range below was fixed in that note's §5 before any run. Adopting it, or any
part of it, needs the owner. *(The sensitivity's result, recorded afterwards
in §10 there: across the range the section test's flow criterion passes at 1,
9, 20, 20 and 20 of 20 seeds, and the test as a whole at 0, 0, 1, 2 and 6.
Nothing below was changed after it.)*

**Why.** At a weaving section the model splits the exit's volume between the
mainline and the entrance in proportion to their volumes (HCM 7th ed. 7.1,
ch. 13, Equations 13-2 to 13-6, the simple weaving-volume estimate). The
ramp-to-ramp share is then an assumption, not a measurement. At the I-94 WB
T.H.52 weave (US 52 NB to exit 242B) no count, published figure or agency
figure gives it (docs/TH52_CROSSING_SHARE.md §3), and the T.H.52 section
fixture's flow verdict changes inside its bounded range (§5 there, D1 of
docs/WEAVE_LOSS_DIAGNOSIS.md §3.11). §2.3 already asks for this case: where a
split cannot be defended, the study uses a documented assumption "and tests
how much the results depend on it (item 11)". §8.5 does not yet list the
share among the varied inputs, and §7 does not list it among what calibration
may change.

**What §8.5 adds.**
1. **Scope.** A weaving section (an entrance and the next exit joined by an
   auxiliary lane) whose ramp-to-ramp volume has no origin–destination
   measurement carries its ramp-to-ramp share s = v_RR / v_ON as an uncertain
   input of the §8.5 design. The documented assumption stays the
   proportional split (`WeaveSpec.ramp_to_ramp_share` unset, the model as
   today).
2. **Range, stated before any run.** From the proportional split P_w of each
   5-minute window to an upper bound s_max with its source; basis
   `stated_assumption`. For T.H.52: s_max = 0.70, source
   docs/TH52_CROSSING_SHARE.md §5 (the upper 95 % limit of the least-biased
   count reading, itself biased upward; at 0.70 the peak hour's mainline flow
   to 242B falls to 4.8 %). Nothing below the proportional split is sampled:
   no source points below it (§3, §4 there). Adding a symmetric lower end is
   the owner's choice at adoption.
3. **Other weaves.** The same rule applies to every weaving section of a
   study. A weave whose s_max has not been bounded with a stated source before
   the first run stays at the proportional split and is listed in the report
   as an unexamined split assumption. On I-94 that is the Ruth St weave today.
4. **Sampling rule.** One more Latin-hypercube coordinate u ∈ [0, 1) per
   weave in scope, from the design's own stream. In each 5-minute window the
   share is s_w = P_w + u · (s_max − P_w), clipped to the window's exit volume
   (v_OFF,w / v_ON,w). u = 0 is the current model at every window, and the
   share rises monotonically with u in every window. The swap keeps the volume
   of every leg and every exit (`microsim.vehicles.build_corridor_plan`), so u
   changes only who crosses, never demand. The other §8.5 rules are
   unchanged: at least 10 samples × 5 seeds, with seeds nested in samples as
   today.
5. **Reporting.**
   - Every strategy effect is reported over the design that includes u. The
     90 % sign rule then decides whether the effect is robust to the share.
   - The T.H.52 and S790 results are also reported conditional on the share:
     the per-sample means against u.
   - The gate (§6) is judged at u = 0, the documented assumption, as today.
     The gate's checks at u = 1 are reported beside it as a sensitivity and
     are never used to pass it.
   - The report's limitations name every weave whose split is carried this
     way, with its range and source.

**What adoption needs (not built).**
- **A per-window form of the key.** `WeaveSpec.ramp_to_ramp_share`
  (2026-10-07, default off, hash-neutral) takes one share for the whole run.
  It refuses a window whose exit volume is below the ramp-to-ramp volume it
  asks for. That serves the 20-minute T.H.52 fixture. It does not serve
  the four-hour corridor. There the proportional split falls from about 0.33
  at 05:30 to 0.18 at the peak, and in seven windows between 07:30 and 08:05
  the conservation exit volume is below 0.70 · v_ON (lowest 0.34 at 07:50,
  where S97's queue breaks conservation; docs/TH52_CROSSING_SHARE.md §5).
  Rule 4 needs the share as u with s_max, applied per window with the clip,
  and the clipped windows counted in `meta.json["ramp_to_ramp_shares"]`.
- **An uncertainty kind** in `validation.uncertainty`, e.g. `ramp_to_ramp_u`:
  range [0, 1], nominal 0, basis `stated_assumption`, source the bounding
  note, mapping to the key of the named entrance. It is not in the default
  space: a study declares it per weave. It was not added on 2026-10-07. It
  touches `default_space` (which lists every derivable kind by default),
  `apply`, the parameter checks (a share is not a positive scale factor) and
  `scripts/uncertainty_runs.py`'s kind options.
- **Timing on long corridors.** The swap pairs entrants with mainline
  vehicles departing in the same 5-minute window. Mainline vehicles are
  windowed at their departure from the corridor's entry, where their exit
  fraction is read. On I-94 that is about 10 km (6–7 free-flow minutes)
  upstream of the weave, so a swapped exit trip reaches the gore about that
  much earlier than the trip it replaces. Adoption either accepts this, as
  the exit draw already does, or windows mainline vehicles by their
  free-flow arrival at the weave.

**What would turn the share into calibration.** Each route needs its own
dated §7 amendment, because §7 does not list an origin–destination split at a
weave. Under the change control above, results are then reported under both
the proportional split and the calibrated share.
- **(a) MnDOT's study report.** The Hwy 52 Lafayette Bridge / I-94 / I-35E
  study (January 2022 – October 2024) offers its final report on request
  (docs/TH52_CROSSING_SHARE.md E1). If it gives an AM-peak volume or share
  for US 52 NB → I-35E NB, that is §2.3's "agency's best available count".
  Requesting it is the owner's call.
- **(b) A pre-registered count-based estimate.** The loop-count method of
  MnDOT MN/RC-1999-40 (E7 there), with the specification fixed before it is
  run:
  - the calibration days only;
  - 15-minute windows, slot fixed effects, free-flow windows;
  - an errors-in-variables correction from the data-quality artifact's count
    error.
  It is adopted only if its 95 % interval is narrower than 0.20. Otherwise
  the interval replaces this amendment's range as the share's range.
  docs/TH52_CROSSING_SHARE.md §4 expects it to fail that test.
- **(c) A direct count.** One AM peak of video at the 242B gore, or an agency
  origin–destination product (MnDOT or the Met Council), counting US 52
  entrants by the exit they take. This is the measurement that settles it.

**Result of Amendment 2 — 2026-10-07** (stage p7, one n2-standard-16 in us-central1-a, 94 min, about $1.20,
self-deleted; `artifacts/i24_validation_dck{025,05}_refit.json`, `artifacts/demand_scale_i24_flow_dck{025,05}.json`).

| arm | demand scale | wave (stack) | GEH < 5 | RMSPE | realised demand (mean, min) | peak sections 2,200 / 3,200 m |
|---|---|---|---|---|---|---|
| reference (k = 0, scale 0.800) | 0.800 | 15.9 km/h pass | 21.5 % | 37.2 % | 0.987, 0.977 | 5,850 / 5,821 |
| k = 0.25 + refit | 0.900 | 15.7 km/h pass | 16.0 % (worse) | 34.1 % | **0.918**, 0.908 | 5,871 / 5,820 |
| k = 0.5 + refit | 0.900 | 15.1 km/h pass | 24.3 % | 33.9 % | **0.930**, 0.916 | 5,953 / 5,899 |

Zero collisions; ring benchmark 20/20 in both. **Under the rule and its clarification no arm qualifies**: both
keep the wave criterion, k = 0.5 is no worse on GEH and RMSPE, but both refits hold 7–8 % of the planned
vehicles off the network (realised demand below 0.977). **k = 0 stays on I-24.** What the run adds: the
I-24 demand fitter (`scripts/i24_fit_demand_scale.py`, objective segment-speed RMSPE) chooses scales that
build a backlog whenever the drivers are stronger — its objective does not see insertion — so a refit under
a changed fleet needs an insertion constraint before its speed fit can be trusted.

**Insertion re-analysis of the Amendment 2 refits — 2026-10-07, after the p7 result. An analysis of
recorded runs, not a calibration: no simulation was run, no artifact was changed, nothing is adopted, and
k = 0 stays on I-24.** The fitter now has an opt-in insertion constraint. `scripts/i24_fit_demand_scale.py
--min-inserted F` chooses the best-objective scale among those whose inserted fraction (departed / planned,
on the fit's single seed) is at least F. If no scale reaches F, it chooses the scale with the highest
inserted fraction and flags `constraint_unmet` in the artifact, the scenario header and the console. With
the option off, the fit and its artifact are byte-identical to before. A GEH reading on the link flows
(CLAUDE.md §6.3) is reported beside the speed RMSPE. It selects only under `--objective geh`.
`--analyze-artifact` re-reads a saved fit's per-scale numbers. On the three refits, and on the fit that set
the reference's 0.800 as a control:

| refit (fit artifact) | rule | scale | inserted (fit seed) | RMSPE fit / held-out hour | GEH < 5, fit hour |
|---|---|---|---|---|---|
| k = 1 (`demand_scale_i24_flow_dc.json`) | recorded (speed only) | 0.925 | 0.9177 | 33.63 / 31.24 % | 25.0 % |
| | ≥ 0.97 | 0.825 | 0.9949 | 38.32 / 50.97 % | 26.4 % |
| | ≥ 0.98 | 0.825 | 0.9949 | 38.32 / 50.97 % | 26.4 % |
| k = 0.25 (`demand_scale_i24_flow_dck025.json`) | recorded (speed only) | 0.900 | 0.9266 | 31.98 / 39.39 % | 25.0 % |
| | ≥ 0.97 | 0.825 | 0.9772 | 34.53 / 36.38 % | 20.8 % |
| | ≥ 0.98 | 0.800 | 0.9959 | 38.61 / 37.27 % | 23.6 % |
| k = 0.5 (`demand_scale_i24_flow_dck05.json`) | recorded (speed only) | 0.900 | 0.9386 | 34.90 / 37.45 % | 18.1 % |
| | ≥ 0.97 | 0.850 | 0.9760 | 36.63 / 35.49 % | 23.6 % |
| | ≥ 0.98 | 0.825 | 0.9851 | 37.31 / 37.70 % | 22.2 % |
| k = 0, control (`demand_scale_i24_flow.json`) | recorded = ≥ 0.97 = ≥ 0.98 | 0.800 | 0.9835 | 32.66 / 45.95 % | 12.5 % |

Reproduce with `uv run --no-sync python scripts/i24_fit_demand_scale.py --analyze-artifact
artifacts/demand_scale_i24_flow_dc.json --analyze-artifact artifacts/demand_scale_i24_flow_dck025.json
--analyze-artifact artifacts/demand_scale_i24_flow_dck05.json --analyze-artifact
artifacts/demand_scale_i24_flow.json --min-inserted 0.97 --min-inserted 0.98`. The table is pinned by
`tests/test_scripts/test_i24_fit_demand_scale.py`. Every number in the table comes from one fit seed
(6914975401685141156), not a 20-seed battery.

- **The constraint moves every refit down by 0.05 to 0.10, to 0.800–0.850, and leaves the reference's
  0.800 unchanged.** That scale inserts 0.9835 on its fit seed, so the constraint does not disturb the
  canonical arm.
- **Most of the refits' speed advantage was the backlog.** Against the control fit on the same seed, k = 1
  at 0.825 is worse on both hours (38.3 / 51.0 % against 32.7 / 46.0 %). k = 0.25 and k = 0.5 at their
  constrained scales are worse on the fit hour (34.5–38.6 % against 32.7 %) and better on the held-out
  hour (35.5–37.7 % against 46.0 %). Whether any of them would pass this amendment's rule is unknown: no
  battery has run at these scales, and the rule needs the wave criterion plus GEH and RMSPE over 20 seeds.
- **F = 0.98 is the threshold consistent with the clarification; F = 0.97 is not.** The fitter's inserted
  fraction comes from one seed, while the clarification bounds the 20-replicate mean (at least 0.977). The
  four batteries run at a fitted scale show the gap. Each battery's first replicate is the fit seed and
  reproduces the fit's fraction exactly, but the mean over 20 replicates differs from it by up to 0.009 in
  either direction (`simulated.demand_realized_fraction`): 0.9835 against 0.9868 (reference), 0.9177
  against 0.9212 (k = 1), 0.9266 against 0.9176 (k = 0.25) and 0.9386 against 0.9298 (k = 0.5). At 0.97,
  k = 0.5's choice (0.850) inserts 0.976 on its fit seed, which is already below 0.977.
- **The table covers the recorded scales only.** A constrained fit centres its refine round on its
  constrained coarse choice, which is 0.800 for all three refits. It would therefore also have run 0.725,
  0.750 and 0.775, and the saved grids lack those scales (`procedure.refine_missing`). Their neighbours
  read 91–98 % (0.700) and 39–42 % (0.800) fit-hour RMSPE, so they are unlikely to win, but this is not
  excluded.
- **GEH is reported, not used to select.** One seed over 72 fit-hour bins moves 1.4 points per bin. On
  the control grid, `--objective geh` would pick 0.775 rather than 0.800, which is too unstable to select
  on without replicates.

A future amendment may pass `--min-inserted 0.98` to the refit stages (`p4_i24_refit`, `p7_i24_amax_wave`
in `scripts/gcp/pipeline_i24.sh`). Those stages are unchanged.

### Adoption of Amendment 3 — 2026-10-07, 21:20 CDT (the coordinator's decision under the owner's delegation)

Decided by the coordinator, to whom the owner delegated the decision, before any corridor run that varies the
share; the basis is the evidence of docs/DECISIONS_2026-10-07.md §A3. Amendment 3 is adopted as proposed: its
range stands, from the proportional split P_w to 0.70, and nothing below the proportional split is sampled (no
lower end is added). Adoption changes no verdict: the range was fixed before any run (docs/TH52_CROSSING_SHARE.md
§5), and the gate is judged where it was.

**What adoption fixes.**
- **(a) An uncertain input.** The T.H.52 ramp-to-ramp share s = v_RR / v_ON (US 52 NB, `on-ramp 769818012`, to
  exit 242B, `off-ramp 18207598`) is an uncertain input of every I-94 result, not a calibrated value. The Ruth St
  weave stays at the proportional split and is listed as an unexamined split assumption (rule 3).
- **(b) The gate is judged at the proportional split** (u = 0), as before: a fail stays a fail; a pass at u = 1
  never counts; a pass that fails at u = 1 is reported as "not robust to the share". A T.H.52 shortfall is
  described as "at the proportional split", not as a merge-model finding, while the range moves it.
- **(c) The range reading beside every headline.** Once the round below has run, every I-94 result the T.H.52
  section can move (C1, C3, C4, C6, collisions, locks, S790 and S97 flows) is reported at u = 0 with its u = 1
  value beside it, labelled "range over the T.H.52 ramp-to-ramp share [proportional, 0.70], stated assumption".
  Until then the limitations name the share and its range (rule 5).
- **(d) No share is chosen.** No share in the range is selected, from the round or otherwise, except by a §7
  amendment resting on route (a), (b) or (c) above, with results reported under both splits.

**The range round, pre-registered now** (pre-registration P-A3 of docs/DECISIONS_2026-10-07.md §A3.3, fixed before
any corridor run that varies the share; not run).
1. **Prerequisite.** The per-window form of the key (rule 4, s_max = 0.70): s_w = P_w + u · (0.70 − P_w), clipped
   to v_OFF,w / v_ON,w, the clipped windows counted in `meta.json["ramp_to_ramp_shares"]`, and byte-identical to
   today's runs when unset (checked as W1b was). The round runs only after this form is built and tested. The
   single-share key cannot serve: on the calibration-day inputs it refuses 0.70 in eight windows, 07:30–08:10
   (lowest v_OFF / v_ON 0.36 at 07:50). The timing item of "What adoption needs" above (mainline vehicles windowed
   at their departure, about 10 km upstream of the weave) is not decided by this adoption; the build states how it
   windows them before the round launches.
2. **Families.** F1 `mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b` (0d26de2a5f01, p10's arm A) and F2
   `mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2` (5080d84d4725, p10's arm B). Amendment 4 below is adopted, so
   both run: F2 is the reference, and its u0 arm is Amendment 4's re-run of p10's arm B; F1 shows whether the
   share's effect depends on W2, whose opposing-entry rule acts on the crossings the share removes (16,767 T.H.52
   deferrals in p10).
3. **Arms.** u0, u05 and u1 (u = 0, 0.5, 1) on the T.H.52 block only (Ruth St stays proportional, reported
   unexamined), step 3's 20 seeds, paired. u0 re-runs the committed scenario; departed shares unequal to p10's are
   reported. Expected on the calibration-day inputs [computed, not run]: s at 05:30–05:50 / 06:30–07:30 of
   0.29 / 0.18, 0.50 / 0.44 and 0.70 / 0.70; 1,955, 1,282 and 609 crossers/h at 06:30–07:30; 0, 1 and 8 clipped
   windows.
4. **Readings.** S790 and S97 hourly flows and GEH; gate C1, C3, C4, C6; realised demand; collisions by section;
   locks (`corridor_w1b.py`'s front-row reader beside `validation.locks`); given-up exits and W1b releases per
   weave; crossings (`n_changed_in`, `n_changed_out`); realised share and clipped windows; per-lane hourly flows
   at S790 and the gore from each battery's one kept trajectory (seed 6914975401685141156, read on the VM).
   Contrasts against u0: paired t, 19 df.
5. **Material** if at u1 against u0: (M1) S790 06:30–07:30 lower bound ≥ +100 veh/h (10 % of the 962 veh/h
   shortfall; p9/p10 half-widths 17–19), mean realised demand at most 1 pp lower; or (M2) S790's or S97's GEH < 5
   count differs by ≥ 5 of 20 seeds in any hour; or (M3) calibration-day C1 or C3, paired, interval wholly beyond
   ±2 pp; or (M4) a collision or lock in one arm only. **Not material** if none holds and the S790 interval lies
   within ±100 veh/h; otherwise **inconclusive**, and no seeds are added.
6. **Never.** No share is chosen from the round; u05 shows shape only; every result is reported.
7. **Cost** [estimate, from p10's batteries: 3,084 and 3,093 s on n2d-standard-16, scoring included]: about $2.0
   per family at `--procs 10` (`--cap-min 240`); both families, about $3.9 (`--cap-min 420`).

**Item 1's timing decision — 2026-10-07, before the launch (docs/A3_RANGE_ROUND.md §2).** The per-window form (`ramp_to_ramp_share: {u: U}`, s_max 0.70) windows every T.H.52 entrant and every swap partner by its free-flow arrival at the start of the entrance's attach edge 51388891 (departure plus Σ L/min(v0, speedFactor × limit), each vehicle's own: 424.0 s from the corridor entry and 37.8 s from US 52's ramp start at the population's mean v0 32.40 m/s, the 24.59 m/s limit binding), not by departure. P_w, v_ON,w and v_OFF,w describe the gore in window w; departure windows would pair the entrants with mainline vehicles reaching the gore 386 s later, so each swap would move an exit trip 6.4 min earlier there (at u1 up to −262/+222 veh/h in single windows around 07:40–08:00) and u would change the timing of exit demand, not only who crosses. The mainline exit draw keeps its departure-time fraction in every arm, as in the unset model. On the model's own gore volumes the expected clipped windows are 0 / 0 / 12 at u0 / u05 / u1 (05:30, in the warm-up, and 07:35–08:40), against item 3's count-data 0 / 1 / 8, which stand; the shares at 05:30–05:50 and 06:30–07:30 are as item 3 states. u = 0 is not byte-identical to the unset model (each window's drawn ramp-to-ramp count becomes its rounded expectation), so the u0 arms run with no share set: F2's committed file, and F1 with W2's three switches written at 0 (p10's arm A; Amendment 4 made W2 the default). Built 2026-10-08 (stage `p24_i94_a3`, `artifacts/a3_range_2026-10-07/`), not run.

### Amendment 4 — 2026-10-07: weave rules W1b and W2 are part of the model (adopted, the coordinator's decision under the owner's delegation)

Decided 2026-10-07 at 21:20 CDT by the coordinator, to whom the owner delegated the decision; the basis is the
evidence of docs/DECISIONS_2026-10-07.md §A2. Written after the p9 and p10 rounds were read, including p10's failed
clause CW5b (Ruth St releases 1.26 % against ≤ 1 %: 257 of 20,340 entrants, the same pooled share in both arms;
docs/I94_CAL_COLLISIONS.md §16). p10's verdict, "W2 not adopted on this round", stays on record. This amendment
re-reads no clause and moves no threshold: it adopts both rules with the release share disclosed as a standing cost.

**Why.** W1b (docs/WEAVE_LOSS_DIAGNOSIS.md §10.2) releases the gore lock, minutes of standstill at the auxiliary
lane's end that have no field counterpart: 3 of step 3's 20 I-94 replicates locked, none under W1b (p9). W2's three
switches (docs/I94_CAL_COLLISIONS.md §13.2) each remove a command-layer artefact, not a driver behaviour: a one-step
weave speed target held to `decel` by SUMO 1.27.1 (WP-95), a leader inside `minGap` read as a free road (WP-96), and
same-step entries into one lane from opposite sides executed in order. On the stress fixtures they removed all four
reproducing collisions. Neither rule is deliverable alone: W1b alone collided at T.H.52 (p10's arm A, failing C5),
and W2 alone ends one fixture run (`th52_upstream_fleet`, seed 3) in the gore lock W1b releases. Neither seeds a
disturbance (CLAUDE.md §0.2): both react to state at the gore.

**Rule.**
1. Every weaving section runs with `entrant_giveup_m` 5, `entrant_giveup_dwell_s` 60, and `weave_handback`,
   `weave_close_leader` and `weave_resolve_opposing` at 1; never tuned per corridor (§7.4), never W2 without W1b.
   This covers the I-94 families (`_dc_cal`, `_dc`) and every later corridor; the I-24 replica models no weaving
   section (its merges are `lane_change`), so its arms are untouched.
2. Every battery and report states each weave's W1b releases (`n_entrant_took_exit`) as a share of its entrance's
   departures, pooled over seeds, beside `no_locks`, with W2's counters. A share above 1 % is listed in the report's
   limitations; it is not a gate failure. With W1b on, the gore lock no longer fails `no_locks`; this share is the
   reading that still shows it.
3. Any of the five settings may be turned off only to reproduce a result published before this amendment, as
   CLAUDE.md §3.3 allows for the AV command guards. Every published I-94 result except the rules' own rounds (p9's
   `_dc_w1b`, p10's two arms) ran with both off: the phase-1 rehearsal, step 3's `_dc` gate (C1 61.8 %), the driver
   grid, the netfix probe, p8's `_dc_cal*` batteries and the strategy rehearsals. Those results stand, and on I-94
   the batteries with both off (step 3, p8) stay reported beside the results under this amendment.

**Before the default flips in code.** Until both items below hold, the five settings stay unset (off) by default
in `weave_params`, and a run under this amendment sets them in its scenario, as p10's arm B did. The flip moves the
weave scenarios' config hashes, as WP-98's did; no scenario file is renamed.
- (i) **A reproduction re-run of p10's arm B on the current code.** Arm B ran on 5516e05, before the veto-capture
  fix (`_lc_mode_owned`, the third regression review). The capture cannot occur on I-94's geometry (the two gores
  are about 5.4 km apart, beyond the 500-m vacate window; there is no measured zone; the scripted merges are
  upstream), so the arm is expected to reproduce `artifacts/weave_w2_corridor.json` per seed; until it is re-run,
  that is an inference. The u0 arm of Amendment 3's range round on F2 (above) serves as this re-run; it is read
  against arm B per seed and every difference is reported.
- (ii) **`validation.battery` and the report count W1b releases** per weave, as rule 2 states them.

**Limits, stated in every report.** Neither rule is measured driver behaviour: neither 60 s nor 5 m is measured,
nor whether real drivers take the exit after a minute (docs/WEAVE_LOSS_DIAGNOSIS.md §10.12), and at Ruth St the
dwell also ends ordinary waits (5 of the first 6 fixture releases). In p10's arm B, W2 suspended undriven vehicles'
own lane changing for a step 22,919 times (vetoes, Ruth St / T.H.52: 6,908 / 16,011). The deadlock's cause and
T.H.52's 465 veh/h shortfall remain. The client report uses the wording of docs/DECISIONS_2026-10-07.md §A2.5.

**Implemented — 2026-10-07.** The five keys are defaults of every weaving section (`WEAVE_AMENDMENT4_DEFAULTS`; docs/CONTRACTS.md §2 "Policy v4" and the dated section "Weave rules W1b and W2 on by default"): config-hash policy version 4, every hash moved once, `config_hash_v3` reproduces the version-3 hashes this protocol's rounds p9–p14 quote. W2 on with W1b off is refused by name. The committed weave scenarios that set none of the keys (10) or only W1b's (2) now run both rules; p9's and p10's arm A reproduce only with `WEAVE_AMENDMENT4_OFF` (all five keys 0), allowed for reproduction alone. Every battery and report states the W1b release share per weave beside `no_locks` (status REPORTED, never gating; above 1 % flagged). No corridor battery has yet run under the defaults; the first is Amendment 3's range round (stage p24) or D10's p16, whichever launches first, and its release share is read against CW5b's 1 % as a disclosed cost, not a criterion.

**Deviation recorded — 2026-10-08, 01:05 CDT (the coordinator).** The code default flipped on 2026-10-07 (8ea59ec) before item (i) of "Before the default flips in code" held: the reproduction re-run of p10's arm B on the current code has not run (item (ii), the release counts in every battery and report, holds since the same commit). That re-run is Amendment 3's range round's F2 u0 arm (stage `p24_i94_a3`, built 2026-10-08, to launch next on the I-94 slot); it is read against arm B (`artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2.json`, `weave_w2_corridor.json`) per seed and every difference is reported. Until it has run, every I-94 result produced under the defaults (stage p16, D10, running at this writing) is labelled "Amendment 4 defaults; the reproduction re-run of p10's arm B is pending" in its readout and report. If the re-run does not reproduce arm B within the per-seed reading, the five keys revert to unset defaults (a further hash-policy version), the results produced under the defaults stay reported under their label, and the amendment's adoption is re-read by the owner. The flip was not a decision to skip (i): it was an error of sequence by the coordinator, found by the short-paper draft's cross-check (docs/PAPER_SHORT.md), and is recorded here rather than undone because reverting would move every hash a second time before the re-run can settle it.

### Amendment 5 — 2026-10-07: ramp counts that carry mainline traffic (adopted provisionally, the coordinator's decision under the owner's delegation)

Decided 2026-10-07 at 21:20 CDT by the coordinator, to whom the owner delegated the decision; the basis is the
evidence of docs/DECISIONS_2026-10-07.md §A1. Written after the count check (stage p11) and the corridor rounds p13
and p14 were read. B2's criteria R1–R5 were fixed before p13 (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3) and hold on
the calibrated arm (§8.4.4 there), so this adopts B2 by its own pre-registered rule; it makes no gate check pass.

**Why.** Between 2,200 and 5,400 m the I-24 recording's counts do not conserve (−403 [−689, −135] veh/h at pooled
coverage). Per-section coverage does not explain it (+294); removing the through traffic counted in ramp lanes does
(−191 [−466, +68]) (`artifacts/i24_count_consistency.json`, `verdict`, `pairs[5]`). Vehicles in a mainline lane
before an on-ramp count were already in the upstream mainline counts, so the builder inserted them twice. That is a
ramp count failing its segment's mass balance, the case of §2.3, not a fitted demand level under §7.1.

**Rule: §2.3 is extended.** Where trajectories show that vehicles counted in a ramp lane were in a mainline lane
before an on-ramp count, or returned to one after an off-ramp count, those vehicles are subtracted from that ramp's
count per 5-minute window, before coverage scaling: corrected = max(counted − flagged, 0). This is a §2
data-quality step, not a §7 calibration change; it changes no target (the I-24 section targets 6,626 / 6,639 /
6,009 veh/h stand). Reports state that the flags are lower bounds. Loop data carry no trajectories, so on a
loop-detector corridor the part that applies is §2.3's mass-balance test.

**On I-24.** The step is `scripts/i24_build_replica.py --ramp-through-traffic exclude` with
`artifacts/i24_count_consistency.json` (sha256 ea403bcf…). Tracked crossings 06:30–08:30, counted → corrected: Old
Hickory on 1,329 → 1,142 (−14.1 %), Hickory Hollow off 740 → 724 (−2.2 %), Hickory Hollow on 862 → 557 (−35.4 %),
Bell Road off 406 → 382 (−5.9 %).
- The calibrated-arm candidate becomes `i24_replica_flow_rc_speedcal_dc_refit` (909b89f298c5), replacing
  `i24_replica_flow_speedcal_dc_refit` (ada3f406504b); the two differ only in name and ramp values. No file is
  renamed: names enter the config hash, and the `_rc` headers record their bases' sha256.
- `_dc_refit`'s results stay in the record, reported beside the candidate and labelled as built on uncorrected
  counts, as change control requires.
- Not adopted: B1, `…_rc_speedcal_dc_refit_b1` (fails A5) and `…_rc_speedcal_dc_refit2` (fails C2 and C3). The
  published `speedcal` record (the penetration × compliance battery, cap sweep and controller probe on
  `i24_replica_speedcal`; the VSL/ALINEA sweep on `i24_replica_flow_speedcal_ramps`) is unchanged; that it rests
  on the uncorrected counts is a stated limitation.

**Provisional.** The Old Hickory part of the correction (about 150 veh/h at pooled coverage) lies outside the span
the conservation test covers, so its sign rests on the flag alone; it is revertible pending the Old Hickory flag
audit or external counts. If either contradicts a ramp's correction in sign, that ramp reverts and the arm is
re-read against R1–R5.

**Not validation.** The candidate still fails the full gate on its 20-seed battery
(`artifacts/i24_validation_dc_refit_rc.json`): hourly GEH < 5 on 30.6 % of comparisons against ≥ 85 %, 5-min
segment-speed RMSPE 0.343 against ≤ 15 %, and the wave row fails. It is one morning with no holdout, the same data
corrected and scored: a calibration candidate, not a validated arm.

**Pre-registered follow-ups**, each with its rule fixed before its run:
1. B2 on the k = 0 congested arm (`i24_replica_flow_speedcal` → `_rc`), read by R1–R5 with the wave half binding
   (B2 has run only on k = 1 arms, which fail the wave row).
2. A data-only audit of the Old Hickory flags: the lateral position and duration of each flagged fragment's
   lanes-1–4 samples, with the rule fixed first.
3. B5, the insertion-aware demand fit, on the `_rc` family, read by C1–C5 (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.5).
4. A diagnosis of the hourly GEH shortfall on `_rc`.
5. External ramp counts (TDOT radar counts for 30 Nov 2022, not in hand; docs/ROADMAP.md §6, item 7).

### Amendment 7 — 2026-10-07: proposed; approved by the coordinator under the owner's delegation (mean `a_max` and mean T moved together under the emergent-wave constraint)

Written before any run that uses it (docs/PRE_FRISCO_PROGRAM.md B6). **Why.** Amendment 1 moved mean `a_max`
alone; at k = 1 the mean driver is string-stable at its capacity density (band from 39.7 veh/km against 29.2) and
every k = 1 I-24 arm lost the wave row; CLAUDE.md §3.1 requires instability near capacity. **What §7.2 adds.** "A
single corridor-wide adjustment" is read as one population moved as a whole, which two of its means satisfy: mean
`a_max` = measured mean + k·sd, k ∈ {0, 0.25, 0.5, 0.75, 1}; mean T = the capacity-calibrated mean + j·0.25·sd(T),
j ∈ {−2…2} (I-24: 1.3222 s, step 0.1303 s); sd from the measured source's covariance; both inside the measured
mean ± 1 sd. **Rule (fixed now).** S1: the mean driver's `unstable_band` lower edge at or below its density of
maximum equilibrium flow (δ = 4, 5-m vehicles). S2: straight-road capacity ≥ 1,775 veh/h/lane (4 lanes,
2,400 veh/h/lane, 30 min, 2 seeds). Grid run on S1 ∩ S2, the corridor's arm at its carried demand, 3 seeds;
Amendment 1's selection (lane-share RMSE within 1 pp of the candidates' minimum, then smallest discharge error,
ties to the smaller change: |k|, then |j|); Amendment 1's last sentence is not applied where the current setting
fails S1, and improvements against it are reported. Stage 3: the chosen pair's Amendment-6 refit and a 20-seed
battery against the arm: stack wave criterion passes (14–22 km/h); GEH < 5 share not below and segment-speed RMSPE
not above the reference's; mean realised share ≥ reference − 0.01; zero collisions; backward fronts per replicate
not more than a third below the reference's. At most two pairs reach stage 3, the second in its own launch.
**Stop:** if S1 admits no pair with k > 0, nothing further runs and the report says so. IDM fleets only.

**Result of S1 — 2026-10-07 (closed form, $0; `artifacts/driver_joint_screen_i24.json`).** 5 of 25 pairs pass,
all k = 0. Every pair with k > 0 is stable at its own capacity density. The nearest miss is k = 0.25, j = −2:
33.87 against 33.27 veh/km. The Stop rule fires: no `a_max` gain on this grid is compatible with §3.1 at
T 1.06–1.58 s. What passes moves T alone, which is Amendment 1's objection, and S2 is one-sided. Nothing launched.

### Amendment 8 — reserved (C8, the timing fit; no text)

Number reserved by docs/PRE_FRISCO_PROGRAM.md for C8. C7's fixed rule did not select C8 (docs/I24_GEH_DIAGNOSIS.md), so no amendment was written; the number stays reserved so the program's references resolve. The amendments below are numbered in the order they were proposed, not the order of this file.

### Amendment 9 — 2026-10-07: a day already used to calibrate stays in calibration; CIRCLES test-fleet days are events (approved by the coordinator under the owner's delegation, before any new day's file was read)

**Why.** §3.2's seeded draw can send a day to validation although the model was already calibrated on it;
every I-24 calibration (FD, driver population, demand scale, the B2 ramp correction) used 30 Nov 2022.
**Rule (§3.1, §3.2 extended).** (1) A day already used to calibrate is pinned to calibration. It must pass
§3.1's screen like any candidate, or the split is refused. The calibration total stays floor(0.6 × all
candidates); pinned days fill its first places; the places left are drawn from the other candidates by §3.2's
procedure (their volume terciles, largest-remainder allocation, at least one per tercile when they allow, seed
20261004). Without a pin the draw is §3.2's exactly. (2) A day on which the CIRCLES test fleet drove the stretch
(the MegaVanderTest week, 14–18 Nov 2022; CLAUDE.md §13) is an event affecting the stretch (§3.1) and is
excluded. **On I-24.** Three candidates give one calibration place, filled by 30 Nov; both new mornings are
validation days and no draw is made. That is below §3.3's 5 / 3 on both sides and is reported as underpowered;
5 / 3 with the pin needs 9 candidates. **Change control.** The split as written is recorded beside the amended
one (`c9_amendment.as_written`) and the re-score is reported under both. Tool: `scripts/day_split.py
--pin-calibration DATE=REASON --circles-events`. Stage `p20_i24_days` (docs/PRE_FRISCO_PROGRAM.md C9;
`artifacts/i24_days_2026-10-07/stage_p20_c9.sh.txt`) runs once the owner's two mornings are in the bucket.

### Amendment 10 — 2026-10-07: I-94 ramp rules (b) and (c) as pre-registered rounds (approved as amendment text by the coordinator under the owner's delegation, before any run; each rule is adopted only on its round's verdict)

**Why.** Fix 1's rules (b) and (c) need amendments (docs/I94_CALIBRATION_DAYS.md §3, drafts 1 and 2; docs/PRE_FRISCO_PROGRAM.md D10).
(b) T.H.61 NB (53062592) is read from rnd_88807, which passes the data-quality checks and whose segment S1069→S1070 closes inside §2.3's configured linear band on four of the five calibration days; yet its residual has the same sign on every day (−304, −284, −302, −426, −287 veh/h), 27–60 % of its 15-minute periods leave the per-period band, and the 4-h calibration-day mean does not close with the detector errors combined in quadrature (−317 against ±261 veh/h). (c) S792 is judged ok on all five days, but a later data-defect finding (two of its three loops scaled ×1.5; S791 − S792 = +328 / +686 / +361 veh/h across an exit in the scored hours, +234 on the 4-h mean) puts it out of the demand balance under §2.2 (a data defect found later is a dated amendment) and §2.4 (detector choice recorded before calibration). With both, every planned station flow is within about 125 veh/h of its count (§4 there).
**Rule.** (1) *General, every corridor (the scope clause; §2.3 extended):* a ramp detector whose segment residual has the same sign on every calibration day and leaves the quadrature count-error band on the calibration-day mean is not used; its ramp takes its own segment's mainline difference. *Scope (decided 2026-10-07, 21:56 CDT, before the run; recorded here 2026-10-08):* single-ramp segments only, because a bracket with two unmeasured ramps cannot be solved from one mainline difference; on I-94 that is rnd_88807 (T.H.61 NB). The Hudson Rd bracket (S1064→S1065) also meets the residual test and stays as built, reported. (2) *I-94 only:* S792 is left out of the I-94 demand balance (two of three loops; reads below S791 across an exit); it stays a scored station. Results are reported with and without both rules.
**Round (stage `p16_i94_d10`, pre-registered in docs/PRE_FRISCO_PROGRAM.md D10 and `artifacts/i94_d10_2026-10-07/stage_p16_d10.sh.txt`).** Arms `mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2_rb` (rule 1) and `_rbc` (rules 1 and 2), written by `scripts/i94_calibration_days.py --only d10` from Phase A's base `_dc_cal_w1b_w2` and checked with `--check`; a one-seed reproduction of the reference battery (else a full re-run of it); each arm's 20-seed battery, baseline gate and gated report, the reference's seeds. Criteria, each arm against the reference: zero collisions and no lock; realised demand ≥ the reference's − 0.01; gate C1 on the calibration days not below the reference's; C3 (15-min) ≤ the reference's + 0.02. C4, C6 and the validation-day rows are reported, with and without both rules. B5's I-94 arm, by this rule fixed now: `_rbc` if it holds, else `_rb`, else the reference. About $2.2 (cap 300 min).
**Status.** Text approved 2026-10-07 (the coordinator's decisions in docs/PRE_FRISCO_PROGRAM.md); nothing run. Adoption of each rule follows the round's verdict and is recorded here after it; until then neither rule is applied to any committed scenario other than the two arms.

**Run — 2026-10-08.** Stage `p16_i94_d10`, VM flowstate-p16, 04:39–06:10 UTC, code 3856257, written 01:26 CDT. Amendment 4 defaults; the reproduction re-run of p10's arm B is pending.

The one-seed reproduction held (`artifacts/i94_d10_repro.json`), so the committed p10 battery and gate are the reference. The readout, `artifacts/i94_d10_corridor.json`, records no problems.
- **`_rbc`** (rules 1 and 2, ad158ff561b1) **holds all four criteria**: zero collisions and no lock; realised demand 0.979 against the floor 0.974; calibration-day C1 53.3 % against 36.1 %; C3 39.7 % against the ceiling 40.3 %.
- **`_rb`** (rule 1, 1f4412f6b393) holds the first three (C1 74.8 %) and **fails C3**: 47.4 %, paired +0.090 [0.056, 0.124] against ≤ +0.02.
- **B5's I-94 arm**, by the rule fixed above, is `_rbc`; p17 runs on it.

**Adoption.** This amendment adopts each rule "only on its round's verdict", and the verdict is the four criteria above, each arm against the reference. Rule 2 ran only together with rule 1, in `_rbc`. `_rbc` meets all four, so adopting rules 1 and 2 together on I-94, as `_rbc` applies them, follows from the verdict as pre-registered. Rule 1 alone (`_rb`) fails C3. The text does not say which arm decides rule 1 by itself, so this round adopts rule 1 only together with rule 2. The program (docs/PRE_FRISCO_PROGRAM.md D10) reserves adoption to the owner: the round's verdict selects `_rbc` for B5's p17 and records that rules 1 and 2 hold together on I-94; they apply to the `_rb`/`_rbc` arms and to no other committed scenario until the owner confirms the adoption (the coordinator, 2026-10-08).

**Scope of rule 1.** As applied, rule 1 changed only rnd_88807, the sole ramp of its bracket. The screen in `artifacts/demand_mndot_i94_wb_stpaul_cal_rbc.json` (`provenance.draft_1_screen`) also meets the test at S1064→S1065, a two-ramp bracket left as built. Rule 1's "every corridor" clause is therefore untested beyond a single-ramp bracket.

**What this does not establish.** The gate still fails in every arm: C1 and C3 on both day sets, and C4. `_rbc` is a calibration candidate, not a validation result.

The owner can overturn this adoption (docs/PRE_FRISCO_PROGRAM.md: "Adoption is the owner's"). Details are in docs/I94_D10_RESULT.md.

### Amendment 11 — 2026-10-07: I-24 is reported in C1's station-hour form beside its 5-min row (proposed by C7b, docs/I24_CONSISTENCY_C7B.md §2; the reporting is adopted, the criterion's form is the owner's decision)

**Why.** C7 (docs/I24_GEH_DIAGNOSIS.md) classified B2's failing link-flow bins by its fixed rule. Recording noise is the largest class under every order: 75 of 100 failing bins have an observed 5-min count at GEH ≥ 5 from its own centred 15-min mean. Scored against that mean, the recording passes 31.25 % of its bins; the model's row is 30.6 %. C7's rule selected a station-hour amendment, reported both ways.
**Rule.** From now on, every I-24 battery reports C1's station-hour form (§4) beside the criteria row:
- hours are anchored at the study period's start: 06:30–07:30 (windows 0–11) and 07:30–08:30 (windows 12–23);
- a station-hour's simulated flow is, per replicate, the sum of its twelve 5-min crossings, which equals the mean of their twelve hourly-equivalent flows;
- the observed station-hour is the mean of the row's recommended-coverage table (`hourly_flows_veh_h_recommended`) over the hour;
- each (replicate, section, hour) is one GEH comparison, pooled over the replicates: 20 × 6 × 2 = 240 comparisons on I-24, the form `validation.baseline_gate` uses for C1;
- the share with GEH < 5 is read against 85 % (`fhwa_default`); the per-replicate share's mean and 95 % t-interval, and the replicate-mean form (12 comparisons, the I-24 row's convention), are reported beside it (`scripts/i24_geh_diagnosis.py station_hours`).
**Status.** The I-24 gate stays on the committed row (144 five-minute bins, replicate mean) until the owner adopts the station-hour form as the criterion; that choice moves the pass threshold's meaning and is not the coordinator's to make. Reporting both forms changes no verdict: B2 reads 30.6 % on the row and 64.6 % / 58.3 % on station-hours; both fail 85 %.
**2026-10-08:** stage p23's readout (`artifacts/i24_consistency_c7b.json`, `readings.<arm>.all_lanes` and `.lane_set`: `station_hour_pooled_share`, `station_hour_pooled_per_replicate`, `station_hour_replicate_mean_share`) reports the station-hour form beside the row for every arm of the round, the B2 re-run included, on every lane and on the observed lane set; the criterion's form is unchanged.

### Amendment 6 — 2026-10-07: the demand level is fitted on link flows under an insertion constraint (approved by the coordinator under the owner's delegation, before any run)

**Why.** §7.1 asks for a GEH-driven demand fit within the count uncertainty. The speed-objective fitter cannot
see vehicles held off the network and twice chose a backlog: Amendment 2's refits realised 0.918 / 0.930 against
the 0.977 floor, and round p14 chose s = 1.125 inserting 0.783, failing C2 and C3 (I24_DISCHARGE_DIAGNOSIS §8.4.6).
**Rule, fixed now.** The demand level is one factor s on the mainline and on-ramp inflows; exit fractions and
the boundary are unchanged. (1) *Seeds:* every grid scale runs on the from-arm battery's first five seeds.
(2) *Constraint:* the mean inserted fraction (departed / planned) over those seeds must be at least the from-arm
battery's mean realised share − 0.01 (Amendment 2's clarification). If no scale qualifies, the fit is
`constraint_unmet`: nothing is chosen, no scenario is written, and the round stops. (3) *Objective:* among
qualifying scales, the largest share of fit-window flow bins with GEH < 5, read with the corridor's own
link-flow estimator. On I-24: the criterion row's (section, 5-min) bins of 06:30–07:30 against the
recommended-coverage counts, on the seeds' mean flow per bin; 07:30–08:30 is held out and reported. On a
detector corridor: C1's calibration-day station-hours (hours anchored at the study period's start), every
seed's pooled. Ties go to the smaller mean GEH over the same bins, then the smaller change |s − s_from| from the
from-arm's level, then the smaller scale. Speed RMSPE is reported, never selected on. (4) *Grids:* I-24 coarse
0.6–1.1 by 0.1, then ±2 × 0.025 around the coarse round's constrained choice. A detector corridor: one factor,
0.95–1.05 by 0.025, which must lie within the data-quality artifact's `count_error`. (5) *Reading:* the chosen
level's 20-seed battery against the from-arm's, same seeds, by §8.4.5's C1–C5; on a detector corridor C3/C4/C5
are the gate's calibration-day C1/C3/C4, plus §9.5's no-lock check. A level that passes is a candidate; adoption
is the owner's. On I-24 this is calibration, never validation. Implemented by `calibration.demand_level`,
`scripts/fit_demand_level.py`, stages `p15_i24_b5` / `p17_i94_b5` (docs/PRE_FRISCO_PROGRAM.md B5);
`scripts/i24_fit_demand_scale.py` is unchanged. p15 runs on the arm that C7b's fixed rule selects.

**Run on I-24 — 2026-10-08.** Stage `p15_i24_b5`, VM flowstate-p15, 07:20–08:36 UTC, code d8186f6, on C7b's `rcs` arm
(921fb1f42c67, carried s = 0.925; floor 0.9570). The fit chose s = 0.925, the from-arm's own level: no grid scale scored
more than 22 of 72 fit-hour bins under GEH 5, s = 0.9 tied on that share and lost on mean GEH (9.247 against 9.150), and
0.95 and above fell below the floor. The refit (`…_b5`, ef355b83cc00) is the from-arm under another name; its 20-seed
battery reproduces the from-arm's. C1–C4 hold and C5 does not bind (the from-arm fails the wave row); the readout
(`artifacts/demand_level_i24.json`) records no problems and marks the level a candidate. It changes no input on I-24;
adoption is the owner's. The arm stays calibration, not validation, and still fails the gate. I-94's run (stage
`p17_i94_b5`, on `_rbc`) is pending. Details: docs/I24_B5_RESULT.md.
