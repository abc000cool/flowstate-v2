# Controller comparison at 5% penetration / 100% compliance

**Date:** 2026-08-30 · **Scenario:** `corridor_10km` (EIDM, emergent/unseeded) ·
**20 common-random-number seeds per cell** · Sources:
`artifacts/m3_sweep_summary.json`, `artifacts/pi_retune_summary.json`,
`artifacts/jad_oracle_summary.json`.

Every cell runs the same scenario with the same seed list, so this is
like-for-like: the uncontrolled baseline is numerically identical across all
three experiments (temporal σ_v 3.3851 m/s, CI lower bound 2.8283 m/s).
`config_hash` values differ between the earlier and later experiments because
`OracleSpec` was added to the schema in between; the physics did not change
(see CHANGELOG).

## Absolute results

| Controller | σ_v temporal [m/s] | Waves / run | Throughput [veh/h] | Fuel [ml/veh-km] |
|---|---|---|---|---|
| Baseline (no control) | 3.39 [2.83, 3.94] | 3.85 [2.59, 5.11] | 1246.65 [1226.90, 1266.40] | 65.44 [64.57, 66.32] |
| FollowerStopper | 1.31 [1.24, 1.38] | 0.15 [-0.02, 0.32] | 1258.22 [1246.63, 1269.81] | 62.19 [61.91, 62.47] |
| JAD, 30 s + 20% noise oracle | 1.33 [1.24, 1.42] | 0.35 [-0.28, 0.98] | 1259.12 [1248.34, 1269.89] | 62.17 [61.89, 62.45] |
| JAD, perfect oracle | 1.78 [1.30, 2.26] | 2.10 [0.12, 4.08] | 1161.61 [1049.66, 1273.55] | 67.15 [60.45, 73.84] |
| PI-saturation (Stern Eqs. 3–5) | 2.38 [2.12, 2.64] | 2.25 [1.59, 2.91] | 1237.93 [1209.22, 1266.65] | 63.57 [63.12, 64.02] |
| PI mean-fraction (superseded §4.2) | 2.14 [1.96, 2.33] | 2.25 [1.75, 2.75] | 79.47 [29.02, 129.92] | 241.43 [196.40, 286.46] |

## Paired change vs baseline (per seed, common random numbers)

| Controller | σ_v temporal | Wave count | Throughput | Fuel |
|---|---|---|---|---|
| FollowerStopper | -61.2% (**resolved**) | -96.1% (**resolved**) | +0.9% (not resolved) | -5.0% (**resolved**) |
| JAD, 30 s + 20% noise oracle | -60.6% (**resolved**) | -90.9% (**resolved**) | +1.0% (not resolved) | -5.0% (**resolved**) |
| JAD, perfect oracle | -47.4% (**resolved**) | -45.5% (not resolved) | -6.8% (not resolved) | +2.6% (not resolved) |
| PI-saturation (Stern Eqs. 3–5) | -29.7% (**resolved**) | -41.6% (**resolved**) | -0.7% (not resolved) | -2.9% (**resolved**) |
| PI mean-fraction (superseded §4.2) | -36.7% (**resolved**) | -41.6% (**resolved**) | -93.6% (**resolved**) | +268.9% (**resolved**) |

## Reading it

**FollowerStopper and JAD-with-a-realistic-oracle are statistically tied at the
top, and either is a defensible choice.** FollowerStopper reaches σ_v 1.31
[1.24, 1.38] and 0.15 waves per run; JAD under a 30 s / ±20% oracle reaches
1.33 [1.24, 1.42] and 0.35 waves. Their CIs overlap heavily on every metric,
including fuel (62.19 vs 62.17 ml/veh-km) and throughput. Nothing in this
experiment separates them.

**FollowerStopper is nonetheless the safer default.** It needs no downstream
detection infrastructure at all — only the gap and speed of the vehicle directly
ahead — whereas JAD needs a 2 km downstream speed field and, as measured, is
*unreliable when that field is too good*: with a perfect oracle it chatters,
hurts 5 of 20 seeds, and its wave-count benefit stops being resolved
([JAD_ORACLE_RESULTS.md](JAD_ORACLE_RESULTS.md)). FollowerStopper is also the
controller the CI-gated ring benchmark exercises.

**PI-with-saturation, implemented faithfully, works but trails both** — a
resolved 29.7% σ_v reduction at no throughput cost, against roughly 60% for the
leaders. Its published constants were set for a ring field experiment and no
corridor-specific tuning was attempted ([PI_CONTROLLER_FIX.md](PI_CONTROLLER_FIX.md)).

**The mean-fraction variant is kept only as a cautionary result.** Its σ_v and
wave numbers look mid-pack precisely because the corridor is at a standstill:
throughput collapses 93.6% and fuel rises 269%. Nobody should deploy it; it is
retained solely to keep the M3 finding reproducible.

## Limitations

One corridor, one penetration/compliance point, EIDM fleet with
screening-calibrated demand — the caveats of [M3_RESULTS.md](M3_RESULTS.md) §1
apply unchanged. No controller was tuned for this corridor; all use published or
spec defaults, so this ranks *default configurations*, not the controllers at
their best. A validated real corridor would be required before presenting any of
this as a deployment recommendation.
