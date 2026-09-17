# The I-24 merge defect, round six: a research plan

**Date:** 2026-09-17 · **Status:** a plan, not a result. Every number is from a
committed artifact, from `CHANGELOG.md`, or from a dated section of
`docs/I24_VALIDATION.md` (a bare §x.y) or `docs/I24_DATA.md`, named at the number. Each
candidate carries the experiment that would confirm or exclude it and a threshold fixed
here, before the runs, so the reading is not chosen after seeing it (CLAUDE.md §0.1).

## 1. The defect as the evidence now defines it

Two of the seven acceptance rows fail on every demand arm and they fail in the same
place. On the fitted arm link flows reach GEH < 5 on 18.8% of link-hours against the
FHWA target of 85%, and the segment-speed RMSPE is 35.9% against a 15% target (§0.1).
Behind the flow row is a 12 to 13% discharge shortfall at the two peak sections, where
GEH < 5 allows about 6% (§0.5(b): recommended observed section means 5,418 / 5,752 /
6,626 / 6,639 / 6,170 / 6,009 veh/h against the fitted arm's 4,950 / 5,800 / 5,800 /
5,750 / 5,900 / 5,600). Behind the speed row is one kilometre at the Old Hickory
on-ramp: the reference lane-change run holds 26.3 and 23.6 km/h over the first two
segments against 36.3 and 32.5 observed (`artifacts/i24_merge_experiment_scripted.json`,
config `483b8721c9d2`, against that file's `observed_segment_mean_kmh`). Lane by lane
the replica inverts the recording: at data x = 0 the recording puts 17% of vehicle-time
in the right lane at 50 km/h, the fastest lane there, where the zipper family puts 38%
at 10 km/h, and from 0.75 to 1.75 km the recording holds 6 to 8% of vehicle-time in the
acceleration lane at 33 to 49 km/h where the family holds 17 to 33% at 4 km/h
(`artifacts/i24_lane_profile_zip.json`, observed rows and arm `f2209020a42a`). Five
rounds have excluded the mechanisms inside the merge: acceleration-lane length and the
two corrected map defects (§0.5(d), (e)); strategic, cooperative, assertive and
ramp-origin eagerness and gap acceptance (§0.5(e), (f)); the entry lane distribution as
then computed (§0.5(g)); SUMO's acceleration-lane attribute (§0.5(j)); the zipper's time
gap, internal lanes and foe-ignoring probability (§0.5(k)); its interleaving distance,
inert from 200 to 600 m (`i24_merge_experiment_visibility.json`: 1,358 to 1,423 of 2,241
admitted); the sublane model (`..._sublane2.json`: insertion 0.4299 to 0.4412 against
the reference's 0.9349); keep-right and speed gain (`..._keepright.json`: keep-right 0
reproduces the reference at 0.3739 / 0.2481); passing on the right (`..._overtake.json`:
insertion 0.9538 but 15-min RMSPE 0.3265 against 0.2732); and a scripted late merge
driven by the runner (§0.8: the entry segment reaches 35.7 to 40.7 km/h but Old Hickory
admits 1,544 to 1,638 of 2,241 against the reference's 2,066, and the held-out RMSPE
rises from 0.4565 to between 0.5612 and 0.6541). What remains is the inputs the merge is
fed with and the target it is scored against.

## 2. Candidates outside the merge, ranked

Every probe runs in two stages: one seed through `scripts/i24_merge_experiment.py` on
the fitted arm (`scenarios/i24_replica_speedcal.yaml`, seed 6914975401685141156, the
seed of the artifacts above), then, only on a clear, a 20-seed battery through
`scripts/i24_validate.py` with `spawn_seeds(42, 20)`, the replicate-noise floor beside
the row as in §0.6. Cost anchors: six single-seed variants took 8 to 12 minutes each
(`wall_s` 459 to 699 s in `..._scripted.json`) and 22 minutes of wall time on an
n2-standard-8, about fifteen cents (CHANGELOG, 2026-09-16); a 20-seed battery takes
about an hour per arm on an n2-standard-32 at about 1.55 dollars per hour (CHANGELOG,
2026-09-06, whose second VM ran 3.4 hours at 32 vCPUs for about five dollars). Data-only
work costs nothing.

### 2.1 The entry lane boundary is in the wrong units and imposed in the wrong place

**Evidence.** `scripts/i24_build_replica.py --entry-lanes observed` counts 5 Hz samples
per lane in data x ∈ [0, 250) m and normalises them (lines 506 to 512), so
`OSMNetwork.entry_lane_shares` carries a share of vehicle-time and is then used to draw
the lane of each inserted vehicle, which is a share of flow. The two differ when lane
speeds differ: at x = 0 the recording's lane speeds are 31 / 32 / 32 / 50 km/h at shares
35 / 26 / 22 / 17% (`i24_lane_profile_zip.json`), and §0.5(g) puts the right lane at 25%
of mainline flow against the replica's 14%. The shares are imposed at the insertion edge
2.3 km upstream of x = 0 (§0.5(g)) and do not survive the trip: the zip arms carry them
and arrive at x = 0 with 23 / 18 / 21 / 38% and a right lane at 10 km/h, already 12 km/h
at x = -500 while lanes 1 to 3 run 32 to 45 km/h (same artifact). A standing queue in
one lane 1.25 km upstream of the gore is not a spillback. The counts are
coverage-weighted too: lane 1 tracks at 0.70 to 0.76, lane 3 at 0.40 to 0.54
(`docs/I24_DATA.md`).

**Experiment.** Give `scripts/i24_build_replica.py` a lane-share estimator that counts
section crossings rather than samples and divides each lane's count by that lane's
coverage from `artifacts/i24_coverage.json`. Six single-seed variants on the §0.6 base:
the present time share (control), the flow share, the coverage-corrected flow share, the
last two with insertion moved to within 500 m of x = 0, and one with the flow share held
by a lane-keeping incentive over the approach. Then one 20-seed battery on the variant
that clears.

**Decider.** `scripts/i24_lane_profile.py` on the probe's first seed: the right lane's
share at x = 0 within 5 points of the observed 17% and its mean speed above 35 km/h
(observed 50, present 10), and the acceleration lane holding at most 12% of vehicle-time
from 0.75 to 1.75 km (observed 6 to 8%, present 17 to 33%). Excluded if the corrected
boundary leaves that lane below 20 km/h. **Cost:** fifteen cents for the probe; on a
clear, a refit of the demand and ramp levels on the new boundary (§0.6 sequence, one
seed per point, about two dollars) and one battery at 1.55 dollars, so about four
dollars.

### 2.2 The scored target is itself lane-coverage weighted

**Evidence.** Edie speed is coverage-robust per lane, but the observed segment mean
averages over lanes weighted by tracked vehicle-time, and the tracking rate is
lane-dependent: 0.70 to 0.76 in lane 1 against 0.40 to 0.54 in lane 3
(`docs/I24_DATA.md`), while the lanes differ by 20 to 30 km/h through the merge zone
(`i24_lane_profile_zip.json`, observed rows x = 1000 to 1750: 33 / 30 / 27 / 24 / 49
km/h). The size and sign of the resulting bias on the RMSPE row are not known; no
artifact measures it.

**Experiment.** Data only. Recompute the observed segment speed field with per-lane
coverage weights from `artifacts/i24_coverage.json`, validate the weighting on the
synthetic lanes the coverage estimator is already tested on, then re-score the four
committed 20-seed artifacts with `scripts/i24_validate.py --criteria-only` against both
targets, publishing both rows. The correction goes in whichever direction it moves the
score, and the criterion row keeps the unweighted target unless the weighted one is
first shown correct on the synthetic validation.

**Decider.** The fitted arm's 5-min RMSPE under the weighted target against 35.9% under
the present one. Material if they differ by more than that arm's replicate-noise floor,
measured at 11.4 to 15.6% for the zip batteries and 16.8% for the canonical heavy arm
(§0.6); otherwise the row stands as scored and this candidate closes. **Cost:** no cloud
cost, about half a day of laptop work.

### 2.3 The Old Hickory ramp level, and the ramp lane's own coverage

**Evidence.** The ramp is planned at 2,241 vehicles (`..._scripted.json`,
`ramps[0].n_planned`) and no configuration has admitted more than 2,066 (its reference
row). The out-of-sample joint fit of §0.6 chose Old Hickory × 0.75 and Hickory Hollow ×
1.25, the direction a ramp demand set too high produces. Ramp demand is corrected with
the mainline coverage of 0.52 to 0.66 (`docs/I24_DATA.md` §4), whose per-lane table
covers lanes 1 to 4 only: the auxiliary lane's tracking rate has never been estimated.

**Experiment.** Data first: fit the gap-mixture estimator of `scripts/i24_coverage.py`
to lane 5 over the acceleration lane's span, 0.75 to 1.95 km (the lane-5 occupancy range
of §0.5(d)), reported with the bias bounds its synthetic validation gives. Then three
single-seed variants at Old Hickory levels {0.75, 0.875, 1.0} on the §0.6 base, and a
battery only on a clear.

**Decider.** The two peak sections' hourly flows, which must rise from 5,798 and 5,760
veh/h to at least 6,225 and 6,238 veh/h to reach GEH 5 against the recommended observed
6,626 and 6,639 (CLAUDE.md §7.1's formula solved at the criterion value), with the entry
segments no lower than their present 26.3 and 23.6 km/h. Excluded if a lower ramp level
buys entry speed only by removing flow the flow row needs, the trade §0.8 measured for
the scripted merge. **Cost:** three variants inside one probe run, under ten cents, and
one battery at 1.55 dollars on a clear.

### 2.4 Heavy vehicles placed by lane and by origin

**Evidence.** `artifacts/i24_heavy_observed.json` is corridor-wide over lanes 1 to 4 and
the whole span: 9.2% of 288,827 fragments are heavy and hold 5.5% of vehicle-time, semis
alone 8.0% and 4.8%, heavy median length 20.3 m. Nothing breaks that down by lane or
ramp origin. The arms carrying it spread it uniformly and moved neither row (RMSPE 35.5%
against 35.9%, GEH 23% against 19%, `docs/I24_DATA.md` heavy section; 34.1% for the
zipper heavy arm, §0.6). What a lane-placed population would have to reproduce is in the
recording: lanes 1 to 3 hold 33 / 30 / 27 km/h from 1.0 to 1.5 km while the right lane
gives up a third of its speed, to 24 / 23 / 19 km/h (`i24_lane_profile_zip.json`).

**Experiment.** Data first: extend `scripts/i24_heavy_share.py` to report the heavy
share by lane and for ramp-lane fragments, as counts and as vehicle-time, carrying that
artifact's own note that heavy vehicles are easier to track than cars. Then, if the
right lane or the ramp carries a materially larger share, place the population by lane
and origin instead of uniformly, redo the capacity calibration for the mixed fleet
(never done for a heavy arm, per `docs/I24_DATA.md`), and probe three single-seed
variants.

**Decider.** The right lane's mean speed from 1.0 to 1.5 km, which must fall to within 5
km/h of the observed 24 / 23 / 19 while lanes 1 to 3 stay within 5 km/h of 33 / 30 / 27
and admittance stays at or above the reference's 2,066. Excluded if the measured
per-lane share differs from the corridor share by less than 3 points, in which case the
uniform arms already answered it. **Cost:** data free; the mixed-fleet capacity
calibration about one battery-hour at 1.55 dollars, three probe variants under ten
cents, one battery at 1.55 dollars, so about three dollars.

### 2.5 The downstream diverge and the exit shares

**Evidence.** Downstream of the taper the replica's lane split inverts the recording's:
at x = 2000 to 2500 the zipper `speedcal` arm holds 38 / 26 / 22 / 14 to 38 / 22 / 23 /
17% at 30 / 48 / 55 / 56 km/h, the recording 29 / 23 / 18 / 30 to 31 / 24 / 18 / 27% at
34 / 34 / 35 / 29 km/h (`i24_lane_profile_zip.json`): too little traffic in the right
lane downstream, middle lanes 15 to 20 km/h fast. Exits are planned at 1,205 at Hickory
Hollow and 748 at Bell Road (`..._scripted.json`, `ramps[1]`, `ramps[3]`) and the §0.6
fit raised the Hickory Hollow exit share by 1.125. Whether this is a diverge defect or
the downstream face of the merge queue is not established.

**Experiment.** Data first: measure from the recording the lane distribution within 1 km
upstream of each off-ramp gore. Then three single-seed variants of
`RampSpec.exit_fraction` and of the lane exiting vehicles use, on the §0.6 base.

**Decider.** The right lane's share at x = 2000 to 2500, which must reach at least 22%
against the observed 26 to 30% and the present 14 to 17%, and the segments from 2.2 to
3.3 km, which must stay within 3 km/h of the observed 37.6 / 36.9 / 37.7. Excluded if
the downstream split does not move with the exit assignment, which puts it back inside
the merge. **Cost:** three probe variants under ten cents, and one battery at 1.55
dollars only if the downstream segments move.

### 2.6 Order and total

The data-only work (2.2, and the first halves of 2.3, 2.4 and 2.5) runs first at no
cloud cost, because two of those can close their candidate without a simulation. The
probes fit into two six-variant runs, about thirty cents; at most three batteries follow
at about five dollars, plus the refit of 2.1 at about two dollars: a ceiling near ten
dollars for the round.

## 3. When the defect is closed, and when the merge is unreproducible

Both verdicts are decided on a 20-seed battery of the fitted arm with 95% intervals and
the replicate-noise floor beside every row (CLAUDE.md §0.6), and recorded in the
artifact before they are written in prose.

**The merge defect is closed** when one arm meets all four at once: Old Hickory admits
at least 2,130 of its 2,241 planned vehicles (95%; the reference admits 2,066, the
zipper 1,350 to 1,423, the scripted merge 1,544 to 1,638); the two peak sections reach
at least 6,225 and 6,238 veh/h, that is GEH < 5 against the recommended observed 6,626
and 6,639; the first two segments are within 3 km/h of the observed 36.3 and 32.5 km/h
while no segment from 1.6 to 3.3 km exceeds its observed value by more than 3 km/h, the
overshoot that disqualifies the scripted and zipper runs; and the 15-min RMSPE falls to
18% or below, against the recording's own 15-min repeatability floor of 15.1% and the
best value measured so far, 24.8% (§0.5(a), (g)).

**The corridor is validated** only on the criterion rows as written: GEH < 5 on at least
85% of link-hours and 5-min segment-speed RMSPE at or below 15% (CLAUDE.md §7.1).
§0.5(a) measures the recording against a smoothed copy of itself at 33.4% on 5-min bins,
so no ensemble mean passes the speed row at that resolution; any change of resolution is
a `validation.criteria` profile with its source cited, made before the battery is
scored.

**The merge is unreproducible with this simulator** when all five candidates have been
taken to the stage their thresholds call for, no arm meets the four closure observables,
and the best arm's residual is published as: the first two segments short by 8 km/h or
more (today 10.1 and 8.9), the two peak sections short by 10% or more (today 12.5 and
13.2%), and a 15-min RMSPE of 22% or above against the 15.1% floor. The corridor then
keeps its 5 PASS / 2 FAIL, the merge becomes a named model-form limitation of a
lane-discrete microsimulator on a 1.2 km acceleration lane in every report's limitations
section, and no further compute is spent on it without new data, meaning the TDOT
radar counts of `docs/I24_DATA.md` §4.

## 4. What not to try again, and the artifact that settled it

* Any further sweep of SUMO's lane-change parameters here: strategic, cooperative,
  assertive and ramp-origin eagerness and gap acceptance (§0.5(e), (f)); keep-right and
  speed gain (`i24_merge_experiment_keepright.json`, where keep-right 0 reproduces the
  reference to every digit); passing on the right (`i24_merge_experiment_overtake.json`,
  which buys insertion and loses speed).
* The zipper's negotiation parameters: time gap, internal lanes, foe-ignoring
  probability (§0.5(k)) and the interleaving distance at 200 to 600 m
  (`i24_merge_experiment_visibility.json`, inert across the range). The merged lane's
  admittance is arithmetic, not a parameter.
* The sublane model as configured, at any lateral resolution or pushiness:
  `i24_merge_experiment_sublane2.json` (insertion 0.43 to 0.44, 15-min RMSPE 0.86 to
  0.90, 1,290 to 2,932 s per run against 459 to 699 s). Reopening it means first making
  it hold capacity on a fixture, not on the corridor.
* Tuning the scripted merge's acceptance gap, forced-change timing or courtesy yielding:
  `i24_merge_experiment_scripted.json` moves admittance by at most 94 vehicles across
  five variants and never toward the reference.
* Adding the heavy share uniformly and expecting the rows to move:
  `i24_heavy_observed.json` with the arms of `docs/I24_DATA.md`'s heavy section and §0.6
  (35.5% against 35.9%; 34.1% in the zipper family).
* Lengthening or re-attributing the acceleration lane: §0.5(d), (e), (j).
* Ramp metering as a way to make the rows pass: §0.5(j). It changes the facility, and
  the recording is of an unmetered day.
* Re-scoring the speed row at a coarser aggregation after seeing that it helps: §0.5(a)
  reports the aggregation table beside the criterion for exactly this reason, and the
  criterion keeps the resolution it was given on 3 September.

## Addendum, 2026-09-17: candidate 1 probed

Single seed, `artifacts/i24_merge_experiment_entryflow.json` (docs/I24_VALIDATION.md §0.9): flow shares raise insertion from 0.935 to 0.952 and cut the held-out RMSPE from 0.457 to 0.406; Old Hickory admits 2029 against 2066 of 2,241 (threshold 2,130 not met) and the entry segments stay at 23 to 27 km/h. Candidate 1 is a correct boundary-condition fix to carry into the next full sequence, not the cause of the merge defect. Candidates 2 to 5 stand.

## Addendum, 2026-09-17: candidate 2 closed (data only)

`scripts/i24_weighted_target.py` rebuilds the observed segment-speed field lane by lane
(reproducing the committed field to 3e-13 m/s) and re-pools it with per-lane
coverage-corrected vehicle-time weights from `artifacts/i24_coverage.json` (lane 1 0.70 to
0.76, lane 2 0.50 to 0.58, lane 3 0.40 to 0.54, lane 4 0.49 to 0.63). On synthetic lanes
with known speeds and lane-dependent tracking the weighted target recovers the true pooled
Edie speed (error within one standard error of zero over 20 thinnings) while the
unweighted one is biased high by 0.3 to 1.3 km/h. On the recording the two targets differ
by 1.52% RMSPE in all, so no arm's row can move by more than that. Re-scoring the four
committed 20-seed artifacts (`artifacts/i24_validation_weighted_target.json`, committed
values reproduced to the digit):

| arm (config) | 5-min unweighted | 5-min weighted | Δ points | 15-min unweighted | 15-min weighted | Δ points |
|---|---|---|---|---|---|---|
| tracked (`4cd18bf46147`) | 187.75% | 191.03% | +3.27 | 152.76% | 155.32% | +2.56 |
| corrected (`d8c6924188eb`) | 33.67% | 34.01% | +0.33 | 27.11% | 27.02% | -0.09 |
| speedcal (`009ed0e2a7c0`) | 35.95% | 36.49% | +0.54 | 26.34% | 26.48% | +0.13 |
| ramps (`d06808e7b8e1`) | 34.84% | 35.23% | +0.39 | 25.55% | 25.54% | -0.01 |

The fitted arm moves +0.54 points against a replicate-noise floor of
11.4 to 16.8 points: **not material; the speed row keeps the unweighted target and the
35.9% residual is not a lane-coverage artefact.** Candidate 2 is closed.

## Addendum, 2026-09-17: candidate 4, data half (proceed)

`scripts/i24_heavy_share.py --by-lane` (`artifacts/i24_heavy_by_lane.json`, corridor row
reproducing `i24_heavy_observed.json` exactly) measures the heavy share by lane and by
ramp use over the study period. Heavy fragments are 1.4% of lane 1, 6.5% of lane 2,
19.0% of lane 3, 14.4% of lane 4 and 13.5% of the auxiliary lane against the corridor's
9.2% (vehicle-time 0.4 / 3.2 / 14.4 / 7.0 / 7.6% against 5.5%); fragments that begin on
the Old Hickory acceleration lane are at the corridor share (9.7% / 6.1%). The decider's
3-point band is exceeded in four lanes (lane 3 by 9.8 points), so the candidate proceeds
to a lane-placed heavy population. Two readings for the simulation half: the placement is
by lane, not by ramp origin (origin placement buys nothing); and the lane with the
observed speed collapse at 1.0 to 1.5 km is lane 4, which carries less heavy traffic than
lane 3, so a lane-placed population is not obviously the mechanism and the probe must show
it. No coverage-by-class estimate exists, so the shares are bounded (heavy vehicles track
more easily, so every share is if anything high; the artifact carries a labelled
sensitivity, not a correction). Simulation half: a per-lane heavy placement in the fleet
schema, the mixed-fleet capacity calibration, three single-seed probes; after the cloud
round now running.
