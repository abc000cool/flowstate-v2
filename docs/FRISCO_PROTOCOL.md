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
detector finds no qualifying peak; 6.65 backward waves per replicate against
9.05 under the reference drivers), while flows and speeds fit slightly better (but, as
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
existed** (the p7 bucket held only its inputs). The 2026-10-07 regression
review found that a demand refit can improve segment-speed RMSPE by holding
vehicles off the network: under k = 1 the refit raised demand 15.6 % but the
peak sections' flow did not move (6,031 / 6,025 → 6,047 / 5,983 veh/h) while
the realised share of planned vehicles fell from 0.996 to 0.921 and mean travel
time rose from 536 to 669 s. A qualifying arm must therefore also keep its mean
realised demand share (`simulated.demand_realized_fraction`, mean over the 20
replicates) no more than 1 percentage point below the reference arm's (0.987,
`artifacts/i24_validation_flow_speedcal_ref.json`), i.e. at least 0.977; the
backlog is reported for every arm.

### Amendment 3 — 2026-10-07: PROPOSED, not adopted (the ramp-to-ramp share at a weave with no origin–destination count)

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
