# The penetration × compliance battery on the I-24 replica (ROADMAP §1.5)

**Date:** 2026-09-04 · **Scenario:** `scenarios/i24_replica_speedcal.yaml` (the fitted
demand arm on the capacity-calibrated population, config `b072d754492d`) ·
**Controller:** FollowerStopper (Stern et al. 2018 constants, CLAUDE.md §4.1) ·
**Grid:** penetration {1, 2, 5, 10, 15, 20}% × compliance {25, 50, 80, 100}% plus the
uncontrolled baseline, **20 common-random-number seeds per cell, 500 runs** on a
cloud VM · **Artifact:** `artifacts/i24_sweep_summary.json`
(`scripts/i24_penetration_sweep.py` → `scripts/i24_penetration_analyze.py`).

**This corridor is not validated** ([I24_VALIDATION.md](I24_VALIDATION.md): 5 PASS /
2 FAIL on the fitted arm; link-flow GEH and segment-speed RMSPE fail), so every
number here describes the replica, not Nashville.
Read it as a sensitivity battery on the best available replica. It is
reported as required by CLAUDE.md §7.1, with 95% CIs from 20 seeds and paired
deltas against the same-seed baseline (`resolved` = the paired CI excludes
zero; "n.r." marks the few that do not).

## Headline

**FollowerStopper at its literature defaults costs throughput on this
corridor at every penetration and compliance level, and the cost grows with
penetration.** At 5% penetration and full compliance, throughput at the
reference cross-section falls 36% (5,710 → 3,652 veh/h), mean travel time
over the span rises 82% (564 → 1,025 s) and fuel per vehicle-kilometre
doubles, while the temporal speed spread falls 56% and the wave count halves.
Even one vehicle in a hundred costs 5% of throughput. The synthetic
`corridor_10km` result ([M3_RESULTS.md](M3_RESULTS.md): −61% σ_v at no
throughput cost) and the US-101 warning that the no-cost claim is
corridor-dependent ([US101_PENETRATION.md](US101_PENETRATION.md)) resolve
here into a clear statement: **on a real multi-lane corridor near capacity,
smoothing by gap-keeping is paid for in capacity.**

Baseline (no control): throughput {:.0f} [{:.0f}, {:.0f}] veh/h, mean travel
time {:.0f} [{:.0f}, {:.0f}] s, σ_v temporal {:.2f} [{:.2f}, {:.2f}] m/s,
{:.1f} [{:.1f}, {:.1f}] waves per replicate, fuel {:.0f} [{:.0f}, {:.0f}] ml/veh-km,
1.25 lane changes per vehicle-km.

## The grid

| Penetration | Compliance | Throughput [veh/h] | Δ | Mean travel time [s] | Δ | σ_v temporal [m/s] | Δ | Waves / run | Fuel [ml/veh-km] | Δ | Lane changes / veh-km |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1% | 100% | 5428 [5408, 5449] | -5% | 617 [611, 623] | +9% | 3.82 [3.79, 3.85] | -23% | 7.2 [6.3, 8.2] | 107 [106, 108] | +6% | 1.34 |
| 2% | 100% | 4939 [4673, 5204] | -14% | 759 [661, 858] | +35% | 3.13 [3.02, 3.24] | -37% | 7.2 [5.8, 8.6] | 130 [113, 147] | +29% | 1.44 |
| 5% | 100% | 3652 [3314, 3990] | -36% | 1025 [944, 1106] | +82% | 2.18 [2.13, 2.23] | -56% | 7.3 [6.3, 8.4] | 213 [182, 243] | +111% | 1.70 |
| 10% | 100% | 2577 [2396, 2758] | -55% | 1243 [1156, 1330] | +120% | 1.76 [1.72, 1.79] | -65% | 5.3 [4.4, 6.3] | 322 [291, 354] | +220% | 1.98 |
| 15% | 100% | 2073 [1936, 2210] | -64% | 1245 [1165, 1325] | +121% | 1.66 [1.61, 1.71] | -67% | 5.1 [4.2, 6.0] | 403 [370, 436] | +300% | 2.14 |
| 20% | 100% | 1544 [1412, 1675] | -73% | 1242 [1113, 1371] | +120% | 1.66 [1.61, 1.71] | -67% | 4.3 [3.3, 5.4] | 552 [501, 602] | +448% | 2.32 |
| 1% | 80% | 5494 [5479, 5509] | -4% | 606 [600, 613] | +7% | 3.94 [3.91, 3.97] | -21% | 7.8 [6.9, 8.6] | 105 [105, 106] | +5% | 1.32 |
| 2% | 80% | 5032 [4760, 5304] | -12% | 710 [650, 771] | +26% | 3.31 [3.20, 3.42] | -34% | 6.9 [5.3, 8.5] | 125 [110, 141] | +24% | 1.41 |
| 5% | 80% | 3936 [3619, 4254] | -31% | 974 [892, 1057] | +73% | 2.34 [2.29, 2.40] | -53% | 7.8 [6.5, 9.0] | 192 [163, 220] | +90% | 1.63 |
| 10% | 80% | 2992 [2718, 3266] | -48% | 1227 [1119, 1336] | +117% | 1.86 [1.83, 1.89] | -63% | 6.0 [4.9, 7.1] | 277 [239, 316] | +176% | 1.86 |
| 15% | 80% | 2254 [2041, 2467] | -61% | 1240 [1162, 1319] | +120% | 1.73 [1.68, 1.78] | -65% | 5.8 [4.6, 7.1] | 380 [335, 425] | +277% | 2.05 |
| 20% | 80% | 2060 [1961, 2158] | -64% | 1341 [1290, 1392] | +138% | 1.59 [1.56, 1.62] | -68% | 4.5 [3.7, 5.3] | 404 [382, 427] | +302% | 2.12 |
| 1% | 50% | 5534 [5481, 5587] | -3% | 592 [582, 602] | +5% | 4.23 [4.17, 4.29] | -15% | 9.1 [7.9, 10.3] | 105 [103, 107] | +4% | 1.30 |
| 2% | 50% | 5421 [5399, 5444] | -5% | 622 [613, 631] | +10% | 3.81 [3.77, 3.84] | -24% | 6.9 [5.6, 8.2] | 108 [107, 109] | +7% | 1.34 |
| 5% | 50% | 4964 [4817, 5110] | -13% | 725 [685, 766] | +28% | 3.01 [2.94, 3.08] | -40% | 7.3 [6.2, 8.4] | 125 [117, 133] | +24% | 1.46 |
| 10% | 50% | 3844 [3576, 4113] | -33% | 992 [927, 1057] | +76% | 2.19 [2.15, 2.23] | -56% | 6.3 [5.2, 7.4] | 194 [171, 217] | +93% | 1.67 |
| 15% | 50% | 3113 [2903, 3324] | -45% | 1196 [1126, 1266] | +112% | 1.88 [1.85, 1.90] | -62% | 6.3 [5.0, 7.6] | 258 [233, 283] | +156% | 1.85 |
| 20% | 50% | 2518 [2320, 2716] | -56% | 1291 [1184, 1398] | +129% | 1.76 [1.73, 1.78] | -65% | 5.2 [4.2, 6.2] | 336 [301, 370] | +233% | 1.98 |
| 1% | 25% | 5618 [5595, 5641] | -2% | 579 [571, 586] | +3% | 4.57 [4.52, 4.62] | -8% | 9.7 [8.3, 11.1] | 103 [102, 103] | +2% | 1.27 |
| 2% | 25% | 5450 [5308, 5593] | -5% | 609 [580, 637] | +8% | 4.18 [4.10, 4.26] | -16% | 7.0 [5.6, 8.4] | 108 [102, 114] | +7% | 1.31 |
| 5% | 25% | 5198 [4955, 5442] | -9% | 658 [624, 691] | +17% | 3.58 [3.48, 3.68] | -28% | 7.8 [6.3, 9.4] | 118 [105, 131] | +17% | 1.37 |
| 10% | 25% | 4744 [4491, 4998] | -17% | 772 [719, 825] | +37% | 2.89 [2.81, 2.97] | -42% | 7.2 [5.8, 8.6] | 138 [122, 153] | +37% | 1.48 |
| 15% | 25% | 4378 [4124, 4632] | -23% | 872 [800, 945] | +55% | 2.49 [2.44, 2.54] | -50% | 6.8 [5.6, 8.0] | 158 [139, 178] | +57% | 1.57 |
| 20% | 25% | 4006 [3816, 4196] | -30% | 938 [900, 976] | +66% | 2.22 [2.19, 2.25] | -55% | 7.2 [6.0, 8.5] | 178 [164, 192] | +77% | 1.66 |

Δ = paired change against the same-seed baseline, percent of baseline.

## Mechanism

FollowerStopper keeps the controlled vehicle's gap above the region
boundaries Δx₁…Δx₃ (4.5–6 m plus the quadratic approach-rate term) and never
lets it exceed the platoon's recent mean speed. On a ring that removes the
wave at no cost because the ring's throughput is not demand-driven (the
CI-gated benchmark and the embed show exactly that). On this corridor,
demand at the entry sits at the population's calibrated capacity
([I24_CAPACITY.md](I24_CAPACITY.md)); a controlled vehicle that holds a
larger-than-equilibrium gap is a moving capacity drop, and the humans behind
it queue. The lane-change rate rises from 1.25 to
2.32 per vehicle-km at 20% penetration as drivers
overtake the slow vehicles (the D2 lane-change statistic of ROADMAP §1.5),
which is the "gap exploitation" v1 asserted without a mechanism and which
now appears with one, in the microscopic tier, measured. Lower compliance
softens every effect roughly in proportion because fewer vehicles actually
hold the gap.

## What it means

* The smoothing benefit is real and monotone (σ_v −24% at 1%, −56% at 5%,
  −67% at 20% with full compliance) and so is its price. The dose-response
  the product must show a DOT is the pair, not the first line alone.
* The controller, not the concept, is what this battery indicts: the
  constants are the ring field test's, the reference speed is the platoon
  mean, and nothing in FollowerStopper knows about capacity. A
  throughput-aware variant (a floor on the commanded speed at the local
  equilibrium speed, or a gap policy tied to the fundamental diagram) is the
  next controller to build, and this grid is its baseline.
* Magnitudes will move when the replica passes its criteria; the sign at
  these penetrations will not, because the mechanism is the gap policy
  against a capacity-bound demand.

## Sensitivity criterion (CLAUDE.md §7.1)

All 24 required cells are present with CIs, so the `sensitivity_grid` row of
the criteria battery is satisfied by this artifact; the arm artifacts written
before this sweep completed carry it as not evaluated (see
[I24_VALIDATION.md](I24_VALIDATION.md) §0).
