# The penetration × compliance battery on the I-24 replica (ROADMAP §1.5)

**Date:** 2026-09-04 · **Scenario:** `scenarios/i24_replica_speedcal.yaml` (the fitted
demand arm on the capacity-calibrated population, config `b072d754492d` under the
hash policy of 2026-09-04; `43def6306dd6` under hash policy v2, the artifact's
`base_config_hash` since the 2026-09-19 re-run) ·
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
reference cross-section falls 38% (5,839 → 3,626 veh/h), mean travel time
over the span rises 102% (590 → 1,191 s) and fuel per vehicle-kilometre
doubles, while the temporal speed spread falls 59% and the wave count halves.
Even one vehicle in a hundred costs 5% of throughput. The synthetic
`corridor_10km` result ([M3_RESULTS.md](M3_RESULTS.md): −61% temporal σ_v as a
change of the means, 56.8% as the mean of per-seed reductions, at no
throughput cost) and the US-101 warning that the no-cost claim is
corridor-dependent ([US101_PENETRATION.md](US101_PENETRATION.md)) resolve
here into a clear statement: **on a real multi-lane corridor near capacity,
smoothing by gap-keeping is paid for in capacity.**

Baseline (no control; re-run of 2026-09-19 under the corrected metric definitions,
the run's 600 s warm-up discarded and travel times over the measured span, the same
definitions as the validation battery's tables): throughput 5,839 [5,808, 5,870] veh/h, mean travel
time 590 [582, 598] s, σ_v temporal 4.94 [4.92, 4.97] m/s,
12.9 [11.3, 14.5] waves per replicate, fuel 101 [100, 102] ml/veh-km,
1.25 lane changes per vehicle-km (`cells.baseline.aggregate` in the
artifact, n = 20).

## The grid

| Penetration | Compliance | Throughput [veh/h] | Δ | Mean travel time [s] | Δ | σ_v temporal [m/s] | Δ | Waves / run | Fuel [ml/veh-km] | Δ | Lane changes / veh-km |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1% | 25% | 5,741 [5,717, 5,766] | -2% | 607 [599, 614] | +3% | 4.52 [4.47, 4.57] | -9% | 8.3 [7.2, 9.4] | 103 [102, 103] | +2% | 1.27 |
| 1% | 50% | 5,653 [5,595, 5,710] | -3% | 622 [611, 634] | +5% | 4.17 [4.11, 4.23] | -16% | 7.4 [6.2, 8.6] | 105 [103, 107] | +4% | 1.30 |
| 1% | 80% | 5,610 [5,595, 5,626] | -4% | 639 [631, 646] | +8% | 3.87 [3.83, 3.90] | -22% | 6.3 [5.4, 7.2] | 105 [105, 106] | +5% | 1.32 |
| 1% | 100% | 5,541 [5,520, 5,563] | -5% | 651 [644, 658] | +10% | 3.74 [3.71, 3.77] | -24% | 5.8 [4.5, 7.0] | 107 [106, 108] | +6% | 1.34 |
| 2% | 25% | 5,563 [5,410, 5,716] | -5% | 643 [609, 677] | +9% | 4.11 [4.02, 4.20] | -17% | 6.5 [5.2, 7.7] | 108 [102, 114] | +7% | 1.31 |
| 2% | 50% | 5,531 [5,508, 5,554] | -5% | 656 [646, 666] | +11% | 3.73 [3.69, 3.77] | -25% | 6.5 [5.3, 7.6] | 108 [107, 109] | +7% | 1.34 |
| 2% | 80% | 5,113 [4,821, 5,405] | -12% | 766 [686, 846] | +30% | 3.21 [3.10, 3.33] | -35% | 6.2 [4.6, 7.9] | 125 [110, 141] | +24% | 1.41 |
| 2% | 100% | 5,014 [4,728, 5,300] | -14% | 828 [698, 958] | +40% | 3.03 [2.91, 3.15] | -39% | 6.2 [4.8, 7.5] | 130 [113, 147] | +29% | 1.44 |
| 5% | 25% | 5,290 [5,026, 5,554] | -9% | 702 [657, 748] | +19% | 3.49 [3.38, 3.60] | -29% | 7.0 [5.4, 8.6] | 118 [105, 131] | +17% | 1.37 |
| 5% | 50% | 5,040 [4,881, 5,199] | -14% | 779 [728, 830] | +32% | 2.91 [2.84, 2.98] | -41% | 6.2 [5.1, 7.2] | 125 [117, 133] | +24% | 1.46 |
| 5% | 80% | 3,935 [3,590, 4,279] | -33% | 1,117 [986, 1,247] | +89% | 2.19 [2.13, 2.26] | -56% | 6.4 [5.1, 7.7] | 192 [163, 220] | +90% | 1.63 |
| 5% | 100% | 3,626 [3,261, 3,992] | -38% | 1,191 [1,063, 1,318] | +102% | 2.02 [1.96, 2.08] | -59% | 6.1 [5.1, 7.1] | 213 [182, 243] | +111% | 1.70 |
| 10% | 25% | 4,805 [4,530, 5,080] | -18% | 841 [769, 912] | +42% | 2.78 [2.69, 2.87] | -44% | 6.2 [4.9, 7.6] | 138 [122, 153] | +37% | 1.48 |
| 10% | 50% | 3,835 [3,546, 4,125] | -34% | 1,133 [1,032, 1,234] | +92% | 2.04 [1.99, 2.08] | -59% | 5.0 [4.2, 5.9] | 194 [171, 217] | +93% | 1.67 |
| 10% | 80% | 2,920 [2,622, 3,217] | -50% | 1,523 [1,324, 1,723] | +158% | 1.66 [1.63, 1.69] | -66% | 4.5 [3.2, 5.8] | 277 [239, 316] | +176% | 1.86 |
| 10% | 100% | 2,475 [2,279, 2,671] | -58% | 1,579 [1,413, 1,744] | +168% | 1.53 [1.50, 1.56] | -69% | 4.3 [3.6, 5.0] | 322 [291, 354] | +220% | 1.98 |
| 15% | 25% | 4,410 [4,135, 4,686] | -24% | 970 [865, 1,076] | +64% | 2.35 [2.29, 2.41] | -52% | 5.5 [4.7, 6.2] | 158 [139, 178] | +57% | 1.57 |
| 15% | 50% | 3,050 [2,822, 3,278] | -48% | 1,443 [1,321, 1,566] | +145% | 1.68 [1.65, 1.71] | -66% | 5.1 [4.1, 6.1] | 258 [233, 283] | +156% | 1.85 |
| 15% | 80% | 2,128 [1,900, 2,357] | -64% | 1,681 [1,489, 1,873] | +185% | 1.47 [1.43, 1.50] | -70% | 4.3 [3.1, 5.5] | 380 [335, 425] | +277% | 2.05 |
| 15% | 100% | 1,936 [1,790, 2,082] | -67% | 1,710 [1,595, 1,824] | +190% | 1.39 [1.35, 1.42] | -72% | 2.9 [2.4, 3.4] | 403 [370, 436] | +300% | 2.14 |
| 20% | 25% | 4,010 [3,803, 4,217] | -31% | 1,053 [996, 1,110] | +79% | 2.07 [2.04, 2.11] | -58% | 6.3 [5.1, 7.5] | 178 [164, 192] | +77% | 1.66 |
| 20% | 50% | 2,411 [2,196, 2,625] | -59% | 1,668 [1,476, 1,860] | +183% | 1.52 [1.50, 1.54] | -69% | 4.3 [3.4, 5.2] | 336 [301, 370] | +233% | 1.98 |
| 20% | 80% | 1,926 [1,821, 2,031] | -67% | 1,846 [1,722, 1,970] | +213% | 1.31 [1.29, 1.33] | -73% | 2.9 [2.1, 3.6] | 404 [382, 427] | +302% | 2.12 |
| 20% | 100% | 1,373 [1,231, 1,515] | -76% | 2,081 [1,862, 2,299] | +253% | 1.29 [1.26, 1.32] | -74% | 2.6 [1.9, 3.4] | 552 [501, 602] | +448% | 2.32 |

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
  *Correction 2026-09-25:* those three figures are the 2026-09-04 run's and were
  missed by the 2026-09-19 re-derivation. The re-run reads temporal σ_v −24% at
  1%, −59% at 5% and −74% at 20% (the grid above; `artifacts/i24_sweep_summary.json`).
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

## Capacity-aware FollowerStopper: a single-seed probe (2026-09-06)

The battery above says FollowerStopper's held gap is a moving capacity
drop. `controllers.follower_stopper_capacity` keeps the FollowerStopper law
inside a time-headway cap `g_max = g0 + h_max · v` (4 m + 2.0 s by default)
and, beyond it, releases the command toward the leader's speed so the
controlled vehicle stops holding traffic back (docs/CONTRACTS.md §1). One
seed of the fitted arm at 5% penetration and full compliance
(`scripts/i24_controller_probe.py`, `artifacts/i24_controller_probe.json`;
the seed differs from the battery's, so the baseline is not the battery's
baseline):

| Configuration | Inserted | Throughput [veh/h] | Mean travel time [s] | p90 travel time [s] | σ_v temporal [m/s] | Fuel [ml/veh-km] | Waves |
|---|---|---|---|---|---|---|---|
| Baseline | 94.5% | 5,651 | 575 | 904 | 4.94 | 102.5 | 11 |
| FollowerStopper, 5% / 100% | 58.6% | 2,933 (−48%) | 1,314 (+128%) | 3,122 | 2.09 (−58%) | 272.3 (+166%) | 5 |
| Capacity-aware FollowerStopper, 5% / 100% | 79.1% | 4,200 (−26%) | 916 (+59%) | 1,709 | 2.21 (−55%) | 162.5 (+59%) | 14 |

The cap halves the throughput and fuel cost for the same reduction in the
speed spread; it does not remove the cost, because a 2.0 s headway held on
purpose is still half a second more than the calibrated fleet keeps
(1.32 s), and at 5% penetration that is a moving capacity drop of its own.
This is one seed, so it carries no interval and settles nothing; it says
the cap is the lever and that the next sweep should be over `h_max_s`
(1.3–2.0 s) rather than over penetration alone. Insertion falls with the
throughput in both controlled runs — the queue reaches the insertion
buffer — so the corridor-level numbers include the buffer's rejections as
the battery's do. **This is one seed; the twenty-seed sweep in the next section does not reproduce it.**

### The headway-cap sweep (2026-09-07; re-run 2026-09-19 under the corrected metric definitions, 20 seeds, `artifacts/i24_cap_sweep_summary.json`)

`scripts/i24_cap_sweep.py` on the fitted arm (`i24_replica_speedcal`,
config `6ab4219ffd92` for the baseline: the script renames the scenario
`i24_cap_baseline`, and the name enters the hash; with the arm's own name the
same config hashes to `43def6306dd6`) at 5% penetration and 100%
compliance: the uncontrolled baseline, FollowerStopper at the literature
defaults, and the capacity-aware FollowerStopper at `h_max_s` ∈ {1.3, 1.5,
1.7, 2.0} s, 20 seeds each with common random numbers, metrics on the
measured span (throughput at data x = 2,200 m). Contrasts are per-seed
differences against the baseline with t-distribution 95% intervals. Run on
a 32-vCPU cloud VM, 2.8 h of wall time for the six configurations (the
per-run metrics ran on one core; the script now pools them).

| Configuration | Throughput [veh/h] | vs baseline | Mean travel time [s] | vs baseline | σ_v [m/s] | vs baseline | Fuel [ml/veh-km] | vs baseline | Waves | vs baseline |
|---|---|---|---|---|---|---|---|---|---|---|
| Baseline | 5,839 [5,808, 5,870] |  | 590 [582, 598] |  | 4.94 [4.92, 4.97] |  | 101 [100, 102] |  | 12.9 [11.3, 14.5] |  |
| FollowerStopper | 3,626 [3,261, 3,992] | −37.9% [−44, −32] | 1,191 [1,063, 1,318] | +102% [+80, +123] | 2.02 [1.96, 2.08] | −59.1% | 213 [182, 243] | +111% [+81, +142] | 6.1 [5.1, 7.1] | −53% [−67, −38] |
| Capacity-aware, `h_max_s` 1.3 | 3,528 [3,198, 3,858] | −39.6% [−45, −34] | 1,144 [1,039, 1,250] | +94% [+76, +112] | 2.05 [1.99, 2.12] | −58.4% | 214 [188, 240] | +112% [+87, +138] | 5.8 [4.4, 7.2] | −55% [−71, −39] |
| Capacity-aware, `h_max_s` 1.5 | 3,517 [3,139, 3,896] | −39.8% [−46, −33] | 1,239 [1,084, 1,393] | +110% [+83, +136] | 2.02 [1.97, 2.08] | −59.0% | 225 [190, 260] | +123% [+88, +158] | 5.7 [4.6, 6.8] | −56% [−70, −42] |
| Capacity-aware, `h_max_s` 1.7 | 3,758 [3,388, 4,128] | −35.6% [−42, −29] | 1,164 [1,040, 1,288] | +97% [+76, +118] | 2.06 [2.01, 2.11] | −58.3% | 203 [172, 234] | +102% [+71, +132] | 5.7 [4.6, 6.7] | −56% [−67, −45] |
| Capacity-aware, `h_max_s` 2 | 3,705 [3,363, 4,048] | −36.5% [−42, −31] | 1,239 [1,052, 1,426] | +110% [+79, +141] | 2.03 [1.97, 2.09] | −58.9% | 207 [177, 237] | +106% [+77, +135] | 5.3 [4.0, 6.7] | −59% [−73, −44] |

The baseline and FollowerStopper cells reproduce the 500-run sweep's 5% /
100% cell above to the digit (same seeds, same SUMO), which is the
consistency check the common random numbers are for.

**What it shows.** The single-seed probe's halving of the throughput cost
does not survive twenty seeds. Every cap value costs the same throughput as
FollowerStopper within the paired intervals — −36% to −40% against −38%,
each interval about ±6 points wide — and buys the same smoothing (σ_v −58
to −59%, waves halved) at the same fuel penalty (+102 to +123%). The
differences between cap values are not ordered with the cap and sit inside
their own intervals: the cap is not the lever. The cost is not made in the
large-gap regime the cap releases; it is made inside the cap, where the
capacity-aware controller is FollowerStopper by construction — a low
reference speed (the rolling platoon mean) applied at short gaps in flow
that is already near capacity. A controller that costs less on this
corridor has to change what it does at short gaps, not how long it holds
back at long ones. The probe's number stands in
`artifacts/i24_controller_probe.json` as what one seed showed and is not
quoted as a result.


