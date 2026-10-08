# I-24 link-flow GEH diagnosis (C7)

Written 2026-10-07. This is step C7 of docs/PRE_FRISCO_PROGRAM.md, whose classes, order and rule
were fixed before this run. It reads committed JSON only: no simulation, no recording data and no
trajectories. It ran locally at $0. Script: `scripts/i24_geh_diagnosis.py`. Artifact:
`artifacts/i24_geh_diagnosis.json`. Tests: `tests/test_scripts/test_i24_geh_diagnosis.py`. This
document reports; it writes no amendment and changes no criterion.

**The question.** After B2 the peak sections pass on 2-h flows (GEH 3.86 / 4.63). Yet the battery's
link-flow row is 30.6 % against the 85 % target (I24_DISCHARGE_DIAGNOSIS §8.4.4). Which bins fail,
and why?

## 1. Answer

On the B2 arm (`i24_replica_flow_rc_speedcal_dc_refit`, 909b89f298c5), 100 of the 144 bins fail.
By class:

| class | bins |
|---|---|
| recording noise | 75 |
| shape | 15 |
| timing | 6 |
| level | 4 |

- **The rule selects the station-hour amendment.** Recording noise is the largest class, so an
  amendment scoring I-24 on station-hours is proposed and I-24 is reported both ways. The order of
  the classes does not decide this: 48 failing bins meet only the noise test, and no other class
  could collect more than 26 under any order.
- **An insertion offset is found.** The builder stamps the mainline inflow at the clock time of the
  count at data x = 200 m. Vehicles enter 2,452 m upstream of that section, 75.7 s away at the
  fleet's mean v0. By the rule's fourth bullet, a computed (not fitted) shift is proposed like B2,
  before C8.
- **C8 is not selected.** Timing is 6 of 100.
- **The station-hour form does not pass either.** In C1's station-hour form the share is **64.6 %**
  (pooled over 20 replicates; per-replicate mean interval 61.8–67.4 %). On the replicate mean it is
  **58.3 %**. Both are below 85 %. Scoring on station-hours changes the resolution, not the verdict.
  This figure is reported, not a criterion.
- **The recording disagrees with itself as often as the model does.** Scored against its own
  centred 15-min mean, the recording passes 45 of the 144 bins, 31.25 % (§10). The model's row is
  30.6 %.

Two findings fall outside the classes and the rule (§7):

- **The two sides count different lane sets at 1,000 m and 4,800 m.** In the simulation both
  sections lie on 5-lane edges, and the simulated count reads every lane. The observed count reads
  lanes 1–4. All four of B2's level bins are at 1,000 m, where this mismatch alone can account for
  the 2-h miss.
- **The planned demand differs from the row's target by a time-varying factor.** The corrected arms'
  inflow is the tracked count over the builder's equilibrium coverage, times s = 0.925. The row's
  target is the tracked count over the recommended coverage. The planned demand is 1.00–1.15 times
  the target, rising through the period.

## 2. What was classified

| item | definition | source |
|---|---|---|
| bins | 6 sections (200, 1,000, 2,200, 3,200, 4,800, 5,400 m) × 24 five-min windows, 06:30–08:30 CST. Each window's crossings are multiplied by 12 to give an hourly-equivalent flow. The 20-replicate mean is compared with the tracked crossings over the recommended coverage. | `geh.bins`, `simulated.hourly_flows_veh_h_mean`, `observed.hourly_flows_veh_h_recommended` |
| failing | the row's stored GEH ≥ 5. The script recomputes all 144 values; the largest difference from the stored ones is 0.0 at the stored 3 dp. | `geh.vs_recommended_coverage_counts.values` |
| 1. recording noise | the observed bin is at GEH ≥ 5 from its own centred 15-min mean (the window and its two neighbours). The first and last windows are padded with themselves, as the observed floor of `validation.report.speed_aggregation_rows` is. | the analogue of I24_VALIDATION §0.5(a) |
| 2. level | the section's 2-h flow (the mean of its 24 hourly-equivalent flows, replicate mean against observed) is at GEH ≥ 5, and the bin's error has the same sign | R4's measure, I24_DISCHARGE_DIAGNOSIS §8.4.3; reproduced on the p13 reference to 0.0004 against `i24_count_consistency.json` `verdict.model_vs_targets` |
| 3. timing | a simulated bin of the same section within ±15 min (±3 windows) is at GEH < 5 from the observed bin | FRISCO_PROTOCOL §5.2 |
| 4. shape | every other failing bin | — |

Each failing bin goes to the first class it meets. The only thresholds are GEH 5 (profile
`fhwa_default`), ±15 min and the 15-min centred mean.

Checks on the inputs (artifact `checks`):

- Every battery's observed side equals `artifacts/i24_validation_observed.json`.
- Its per-window coverage equals `artifacts/i24_coverage.json`'s `pooled.recommended_filled` to 4 dp.
- The six 2-h targets equal `i24_count_consistency.json`'s pooled section flows.

## 3. The B2 arm

### 3.1 By section

| section [m] | failing | recording noise | level | timing | shape | 2-h flow, observed / simulated [veh/h] (GEH) |
|---|---|---|---|---|---|---|
| 200 | 19 | 13 | 0 | 2 | 4 | 5,417.6 / 5,483.6 (0.89) |
| 1,000 | 17 | 13 | 4 | 0 | 0 | 5,751.6 / 6,398.3 (**8.30**) |
| 2,200 | 19 | 16 | 0 | 0 | 3 | 6,626.2 / 6,315.8 (3.86) |
| 3,200 | 18 | 13 | 0 | 1 | 4 | 6,639.0 / 6,267.1 (4.63) |
| 4,800 | 13 | 9 | 0 | 1 | 3 | 6,170.5 / 6,070.2 (1.28) |
| 5,400 | 14 | 11 | 0 | 2 | 1 | 6,008.7 / 5,772.4 (3.08) |
| **all** | **100** | **75** | **4** | **6** | **15** | |

### 3.2 By hour (anchored at 06:30)

| hour | failing | recording noise | level | timing | shape |
|---|---|---|---|---|---|
| 06:30–07:30 | 50 | 32 | 2 | 5 | 11 |
| 07:30–08:30 | 50 | 43 | 2 | 1 | 4 |

### 3.3 Overlap

The classes overlap; the order decides. Among the 100 failing bins (`membership.patterns`):

| tests met | bins |
|---|---|
| noise only | 48 |
| noise + timing | 17 |
| noise + level | 9 |
| noise + level + timing | 1 |
| timing only | 6 |
| level only | 2 |
| level + timing | 2 |
| none (shape) | 15 |

Under any order, noise collects at least 48 bins. Under any order, level collects at most 14,
timing at most 26 and shape exactly 15. So noise is the largest class under every order
(`largest_under_every_order`).

### 3.4 What the other classes hold

- **Recording noise.** Its errors split 41 model-high and 34 model-low. A signed bias would not
  split this evenly.
- **Shape.** 13 of the 15 shape bins have the model low. Seven of them fall in 06:30–06:45 at 200 to
  4,800 m. There the recording reads 7,000–8,300 veh/h and the model 6,470–6,760 veh/h
  (`arms.dc_refit_rc.bins`). This is the first-hour discharge shortfall that the station-hour form
  also shows (§4).
- **Level.** All four level bins are at 1,000 m, the section whose lane sets differ (§7.1).

## 4. The station-hour form (reported, not a criterion)

C1's form (FRISCO_PROTOCOL §4) anchors hours at the study period's start, window 0 (06:30). So
06:30–07:30 is windows 0–11 and 07:30–08:30 is windows 12–23.

A station-hour's flow is its twelve 5-min counts summed. That equals the mean of their twelve
hourly-equivalent flows, which is how `validation.observed.ObservedCorridor.hourly_link_flows`
forms it. The observed station-hour is the mean of the row's recommended-coverage table over the
hour.

C1 pools every replicate's station-hours (`validation.baseline_gate`): 20 × 12 = 240 comparisons.
The replicate-mean form (12 comparisons) follows the I-24 row's convention. The I-24 criteria row
stays the 144-bin 5-min form.

| section [m] | hour | observed [veh/h] | simulated, mean [veh/h] | GEH (mean) | replicates passing |
|---|---|---|---|---|---|
| 200 | 06:30–07:30 | 6,003.7 | 5,735.0 | 3.51 | 20 |
| 200 | 07:30–08:30 | 4,831.4 | 5,232.2 | **5.65** | 4 |
| 1,000 | 06:30–07:30 | 6,176.8 | 6,581.6 | **5.07** | 11 |
| 1,000 | 07:30–08:30 | 5,326.4 | 6,215.1 | **11.70** | 0 |
| 2,200 | 06:30–07:30 | 7,145.9 | 6,506.2 | **7.74** | 0 |
| 2,200 | 07:30–08:30 | 6,106.4 | 6,125.3 | 0.24 | 20 |
| 3,200 | 06:30–07:30 | 6,956.7 | 6,432.4 | **6.41** | 0 |
| 3,200 | 07:30–08:30 | 6,321.3 | 6,101.7 | 2.79 | 20 |
| 4,800 | 06:30–07:30 | 6,659.9 | 6,391.8 | 3.32 | 20 |
| 4,800 | 07:30–08:30 | 5,681.1 | 5,748.7 | 0.89 | 20 |
| 5,400 | 06:30–07:30 | 6,331.3 | 6,059.4 | 3.46 | 20 |
| 5,400 | 07:30–08:30 | 5,686.0 | 5,485.3 | 2.69 | 20 |

- **Share.** Pooled (C1's form): 155 of 240 = **64.6 %**, per-replicate mean interval 61.8–67.4 %.
  Replicate mean: 7 of 12 = **58.3 %**.
- **Peak sections, first hour.** At 2,200 and 3,200 m the model is 640 and 524 veh/h low in
  06:30–07:30. It is right in 07:30–08:30. R4's 2-h pass averages a first-hour shortfall with a
  second hour on target.
- **1,000 m.** Both hours fail; this is the lane-set section (§7.1).
- **200 m, second hour.** The model is 401 veh/h high in 07:30–08:30. The planned demand is 1.11 times
  the target in that hour (§7.2).

## 5. Cross-correlation lags

The table gives Pearson r of the simulated profile shifted by L windows against the observed
profile, on the overlap, for |L| ≤ 3 (±15 min). A positive L means the simulated profile is
later. The sub-window value is a three-point parabola through r around an interior peak.
Five-minute windows cannot resolve less, so it is approximate.

| section [m] | best lag | r at best | r at 0 | parabolic estimate [s] |
|---|---|---|---|---|
| 200 | 0 | 0.693 | 0.693 | +57.5 |
| 1,000 | **+1 (5 min)** | 0.589 | 0.367 | +354.7 |
| 2,200 | 0 | 0.683 | 0.683 | +19.4 |
| 3,200 | 0 | 0.672 | 0.672 | +29.5 |
| 4,800 | 0 | 0.841 | 0.841 | +9.5 |
| 5,400 | 0 | 0.891 | 0.891 | −13.2 |

- **No section is shifted by a whole window except 1,000 m.** The downstream sections, whose flow
  is set by the bottleneck's discharge, sit within ±30 s of zero.
- **200 m.** The model is later by about a minute, the size and sign of the insertion offset (§6).
- **1,000 m.** The model is later by about 6 minutes, more than the mainline offset explains. That
  section also carries the Old Hickory on-ramp's own insertion, which is not computed here (§11),
  and the lane-set difference (§7.1).

## 6. The insertion offset

The builder (`scripts/i24_build_replica.py`) counts the mainline inflow at data x = 200 m
(`mainline.count_x_m`). It gives window i's rate the step time `to_sim_time(t_lo + i·300)` =
600 + 300·i s. The first step, at 0 s, also covers the warm-up.

Vehicles depart at the network entry, sim x = 0: `departPos="base"` on the first corridor edge
(`microsim.vehicles`). Read from the inputs (`insertion_offset.stamping`), all 23 step times after
the first equal the count windows' own clock times. The largest shift is 0.0 s. This holds on the
validator's inputs (`i24_replica_inputs.json`) and on the B2 family's (`i24_replica_inputs_flow_rc.json`;
the section distances differ by at most 0.54 m).

The inflow is therefore stamped at the count section with no travel-time shift. The computed
free-flow times below use the validator's mapping (sim x = 2,256.2 + 0.97981 · data x) at the
population's mean v0 of 32.40 m/s. Both `idm_i24_capacity_amax_k1.0.json` (B2's fleet) and
`idm_i24_capacity.json` give this v0.

| section [m] | distance from entry [m] | free flow from entry [s] | from the count section [s] |
|---|---|---|---|
| 200 | 2,452.2 | **75.7** | 0.0 |
| 1,000 | 3,236.0 | 99.9 | 24.2 |
| 2,200 | 4,411.8 | 136.2 | 60.5 |
| 3,200 | 5,391.6 | 166.4 | 90.7 |
| 4,800 | 6,959.3 | 214.8 | 139.1 |
| 5,400 | 7,547.2 | 232.9 | 157.3 |

- **Every section is offset.** Downstream of the count section both the recording and the model
  propagate traffic. The part neither side shares is the entry-to-count-section time, **75.7 s**, at
  all six sections. That is 0.25 of a 5-min window and 0.08 of the ±15-min tolerance.
- **75.7 s is a minimum, not an estimate.** The fleet's mean free-flow time is at least this, since
  v0 varies across drivers. An edge limit below v0 would lengthen it; SUMO's desired speed is
  min(v0, speedFactor × limit), and the network is not read here. So would insertion at
  `departSpeed="avg"`, or congestion.
- **By the rule, the offset is found.** A computed shift of this size would be proposed like B2, as
  a dated input correction with its own pre-registered round, before C8. Nothing is proposed here.

## 7. Two input checks outside the classes

The rule says nothing about these. They are reported because they explain bins the classes put
under level and shape.

### 7.1 The lane sets differ at 1,000 m and 4,800 m

The observed count reads lanes 1–4 (`scripts/i24_data.MAINLINE_LANES`). The simulated count reads
every vehicle on the corridor edge the section lies on, in any lane: trajectory x is the edge offset
plus the lane position, and the validator applies no lane filter. The B2 family's `geometry` places
two sections on 5-lane edges:

| section [m] | simulated edge (lanes) | recording's auxiliary-band flow, tracked [veh/h] | observed lanes 1–4 / all lanes, lower bound / all lanes at the row's coverage [veh/h] | B2 simulated 2-h [veh/h] | GEH as scored / all lanes, lower / at the row's coverage |
|---|---|---|---|---|---|
| 1,000 | 977008894 (5; the Old Hickory acceleration lane), 94 m in | 662.5 | 5,751.6 / 6,414.1 / 6,814.3 | 6,398.3 | **8.30** (model high) / 0.20 (low) / 5.12 (low) |
| 4,800 | 992666043 (5; the Hickory Hollow–Bell Road weave lane), 284 m in | 350.5 | 6,170.5 / 6,521.0 / 6,733.5 | 6,070.2 | 1.28 (low) / **5.68** (low) / **8.29** (low) |

The auxiliary-band flow is `aux_band_tracked_veh_h` in `i24_count_consistency.json`. The "lower"
column takes that flow at coverage 1. The "row's coverage" column divides it by the mainline
coverage the row's table applies over the period at that section (0.623 at both), which is the
builder's assumption for ramp lanes. Lane-5 coverage is bounded only to [0.33, 1]
(`i24_coverage_lane5.json`).

- **At 1,000 m the level miss is not a demonstrated model error.** Counted like for like, the model's
  2-h excess of 647 veh/h vanishes at the lower bound or reverses to a shortfall. So the four level
  bins there are not evidence of a model error.
- **At 4,800 m the same mismatch hides a shortfall.** That section passes as scored, but like for
  like it fails at either bound.
- **Fixing this needs a re-run or an unmeasured coverage.** Either the simulated count must read
  lanes 1–4, which needs trajectories (re-run, since the archives keep none), or the observed side
  must add the auxiliary band, which needs lane-5 coverage. This diagnosis does neither.
- **The mismatch is the same on every arm.** Every arm's like-for-like numbers are under
  `arms.*.lane_set_check`.

### 7.2 The planned demand is a time-varying multiple of the row's target

The corrected arms divide the tracked mainline and on-ramp counts by the builder's
`coverage_used`, the equilibrium estimator. The fitted arms then multiply them by s
(`i24_build_replica.py` `corrected`; `demand_scale_i24_flow_dc.json` `best.scale` = 0.925, carried
to B2 per its scenario header). The row's target divides the same tracked counts by the recommended
coverage. Before insertion losses, the planned demand over the target is s · c_rec / c_used:

| from | c_used | c_rec | planned / target |
|---|---|---|---|
| 06:30 | 0.6053 | 0.6558 | 1.002 |
| 06:45 | 0.5591 | 0.6325 | 1.046 |
| 07:00 | 0.5645 | 0.6481 | 1.062 |
| 07:15 | 0.5624 | 0.6737 | 1.108 |
| 07:30 | 0.5036 | 0.6274 | 1.152 |
| 07:45 | 0.4909 | 0.5872 | 1.107 |
| 08:00 | 0.4805 | 0.5592 | 1.076 |
| 08:15 | 0.4839 | 0.5842 | 1.117 |

- **Hour means.** The ratio averages 1.055 in 06:30–07:30 and 1.113 in 07:30–08:30.
- **The count section follows the same pattern.** At 200 m the simulated-over-observed station-hour
  ratio goes from 0.955 to 1.083. The 200-m second-hour miss is built into the inputs. The first
  hour delivers less than planned, and the realised demand is 0.967.
- **This is not the level class.** The classes test outputs against the target; this checks inputs
  against it.

## 8. The rule's verdict (report-only)

The rule was fixed in the plan before the run. Applied to B2's counts (`verdict`):

- **Timing largest → C8 runs.** Not selected: timing is 6 of 100.
- **Level largest → back to B5/B6.** Not selected: level is 4 of 100.
- **Recording noise largest → a station-hour amendment is proposed, and I-24 is reported both ways.**
  **Selected:** noise is 75 of 100, and largest under every order (§3.3). Both ways: the 5-min row
  is 30.6 %; the station-hour form is 64.6 % pooled and 58.3 % on the replicate mean.
- **An insertion offset, if found, is corrected by a computed shift, proposed like B2, before C8.**
  **Selected:** found, 75.7 s at v0 (§6).

No amendment is written here. Adoption and wording are the owner's.

## 9. Comparison arms

Every committed I-24 battery that C7 names was run through the same steps. The p12 B1 arms were
added as "the B1 arms".

| arm | hash | row share | failing | noise / level / timing / shape | station-hour, pooled / replicate mean | largest under every order |
|---|---|---|---|---|---|---|
| **B2 `dc_refit_rc`** | 909b89f298c5 | 30.6 % | 100 | 75 / 4 / 6 / 15 | 64.6 % / 58.3 % | noise |
| p13 reference `dc_refit_p13ref` | ada3f406504b | 25.7 % | 107 | 79 / 9 / 7 / 12 | 58.3 % / 58.3 % | noise |
| B2 re-run `p14_b2_ref` | 909b89f298c5 | 30.6 % | 100 | 75 / 4 / 6 / 15 | 64.6 % / 58.3 % | noise |
| B1 + B2 `p14_b1b2` | e19e5ab64186 | 25.7 % | 107 | 81 / 4 / 10 / 12 | 54.6 % / 58.3 % | noise |
| B2 at s = 1.125 `p14_refit2_b2` | e20554388a3c | 25.0 % | 108 | 77 / 13 / 9 / 9 | 65.8 % / 66.7 % | none (order decides) |
| `_dc_refit` + B1 `p12_dc_refit_b1` | 843b3b0c8634 | 26.4 % | 106 | 79 / 4 / 10 / 13 | 55.4 % / 58.3 % | noise |
| canonical + B1 `p12_canonical_b1` | 8378b1c05b1f | 22.2 % | 112 | 80 / 15 / 5 / 12 | 43.3 % / 41.7 % | none (order decides) |
| `_dc` + B1 `p12_dc_b1` | 1a14bf3f9e7e | 25.7 % | 107 | 78 / 14 / 12 / 3 | 50.0 % / 50.0 % | none (order decides) |

- **Every arm selects the same outcome.** In the fixed order, recording noise is the largest class
  on all of them, so the rule makes the same selection on each.
- **Three arms depend on the order.** On s = 1.125, canonical + B1 and `_dc` + B1, a level-first or
  timing-first order would make that class largest. On those arms 41–45 failing bins meet the level
  test.
- **`p14_b2_ref` reproduces B2.** Its per-replicate counts are identical (`reproduces`).
- **B1 + B2.** The seed that collapsed brings its pooled station-hour share down to 54.6 %.
  Its 5-min leave-one-out minimum is 17.4 %.

## 10. Context: how far a 5-min bin can agree

These figures are context, not classes.

- **The recording against itself.** The recording's 144 bins against their own centred 15-min mean
  pass at **31.25 %** (45 of 144). By section: 25.0, 33.3, 20.8, 29.2, 37.5 and 41.7 %. The model's row is 30.6 %.
  This is the flow counterpart of §0.5(a)'s speed floor, where the recording's 5-min field scores an
  RMSPE of 33.4 % against its own 15-min average.
- **The model against itself (leave-one-out floor).** Each B2 seed is compared with the mean of the
  other 19, the flow analogue of the battery's `rmspe.leave_one_out_floor`. It passes 75.8 % of the
  5-min bins (interval 74.8–76.7 %, seeds 72.2–78.5 %). It passes 100 % of the station-hours. Even
  with full counting and no tracking loss, one simulated day does not reach 85 % at 5 minutes
  against its own ensemble mean.
- **Counting noise alone (Poisson assumption, `counting_noise`).** Suppose each tracked 5-min count
  is Poisson. Then one standard deviation of the recommended-coverage hourly-equivalent flow is
  GEH √(12 / c) = 4.22–4.63 at the recording's coverage of 0.559–0.674. A model equal to the expected
  count would pass about 74.4 % of the 5-min bins. At station-hours one standard deviation is GEH
  1.24–1.31, and such a model would pass 99.99 %. Traffic counts are not Poisson in congestion, so
  this is a scale, not a floor.

## 11. Limits

- **The classes overlap and the order decides.** On B2 the verdict does not depend on the order
  (§3.3). On three comparison arms it does (§9).
- **One recorded day** (30 Nov 2022), one direction. The observed side is one realisation; the
  simulated side is a 20-seed mean.
- **The noise test does not separate tracking noise from real traffic.** It measures the recording's
  variability below 15 minutes, which includes real 5-minute traffic structure. A stochastic model's
  ensemble mean cannot reproduce that structure in phase either way.
- **The insertion offset is a free-flow minimum** at the mean v0 (§6). The on-ramp insertion offsets
  are not computed: the ramp edges' lengths are in the network, which is not read.
- **The input checks bound rather than measure** (§7). The lane-set check bounds the auxiliary lane
  by its tracked count and by the row's coverage; lane-5 coverage is not measured. The demand ratio
  is planned, before insertion losses.
- **Reported, not criteria.** The station-hour share and the context figures are reported. No
  criterion is changed.

## 12. Reproduce and provenance

```
uv run --no-sync python scripts/i24_geh_diagnosis.py
uv run --no-sync pytest tests/test_scripts/test_i24_geh_diagnosis.py
```

The artifact records the path and sha256 of every input. It covers eight batteries plus the
observed, count-consistency, coverage, replica-input, population and demand-scale artifacts. It
also records the script's commit and the sha256 of the files it ran (`code`).

It was written from an uncommitted script, so `code.dirty` is true and the script's sha256 is the
reference. Re-running after the commit reproduces every number. Nothing reads `data/` or a
trajectory file.

To repeat C7 on a new I-24 battery:

```
uv run --no-sync python scripts/i24_geh_diagnosis.py \
    --battery LABEL=artifacts/i24_validation_LABEL.json --primary LABEL
```

The plan schedules this on p15 and on B6.
