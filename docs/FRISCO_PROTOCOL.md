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
