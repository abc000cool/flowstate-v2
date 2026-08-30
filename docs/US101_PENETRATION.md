# Does the penetration dose-response survive on real geometry?

**Date:** 2026-08-30 · **Scenario:** `us101_replica` + measured downstream
boundary · **Controller:** FollowerStopper at 100% compliance ·
**6 cells x 20 common-random-number seeds** · **Artifacts:**
`artifacts/us101_penetration_summary.json`, `runs/us101_penetration/analysis.json` ·
**Scripts:** `scripts/us101_penetration_sweep.py`, `scripts/us101_penetration_analyze.py`

M3's headline dose-response was measured on `corridor_10km`: a synthetic,
single-lane, 10 km straight pipe with an EIDM fleet and screening-calibrated
demand. This repeats the penetration ladder on the US-101 replica — 640 m of
real 5-lane geometry, a fleet drawn from the `artifacts/idm_us101.json`
population fit, real upstream demand, and the measured downstream boundary
(without which the replica produces no waves to dampen).

**This is a robustness check, not a validated corridor study.** The replica
fails 5 of 6 FHWA criteria and its limitations are documented in
[M3_US101_VALIDATION.md](M3_US101_VALIDATION.md) §7. What follows tests whether
the *shape* of the effect survives a change of geometry, fleet and demand — not
whether these numbers describe real US-101.

## Absolute results

Baseline: σ_v 2.93 [2.84, 3.01] m/s, throughput
8055.32 [8051.59, 8059.04] veh/h (5 lanes), fuel
66.42 [65.98, 66.87] ml/veh-km, 1.65 [1.27, 2.03] waves/run.

| Penetration | σ_v temporal [m/s] | Throughput [veh/h] | Fuel [ml/veh-km] | Waves / run |
|---|---|---|---|---|
| 1% | 2.69 [2.63, 2.74] | 8033.69 [8026.53, 8040.86] | 67.53 [67.02, 68.03] | 1.60 [1.13, 2.07] |
| 2% | 2.56 [2.51, 2.62] | 8024.95 [8016.63, 8033.26] | 67.89 [67.47, 68.30] | 1.95 [1.48, 2.42] |
| 5% | 2.22 [2.18, 2.26] | 7978.83 [7968.42, 7989.25] | 68.23 [67.74, 68.72] | 2.05 [1.40, 2.70] |
| 10% | 1.84 [1.81, 1.86] | 7948.46 [7937.47, 7959.46] | 67.38 [67.06, 67.70] | 1.30 [1.08, 1.52] |
| 20% | 1.37 [1.34, 1.40] | 7923.18 [7914.67, 7931.69] | 66.73 [66.40, 67.06] | 1.05 [0.95, 1.15] |

## Paired change vs baseline (per seed, common random numbers)

| Penetration | σ_v temporal | Throughput | Fuel | Wave count |
|---|---|---|---|---|
| 1% | -8.2% (**resolved**) | -0.3% (**resolved**) | +1.7% (**resolved**) | -3.0% (not resolved) |
| 2% | -12.3% (**resolved**) | -0.4% (**resolved**) | +2.2% (**resolved**) | +18.2% (not resolved) |
| 5% | -24.2% (**resolved**) | -0.9% (**resolved**) | +2.7% (**resolved**) | +24.2% (not resolved) |
| 10% | -37.3% (**resolved**) | -1.3% (**resolved**) | +1.4% (**resolved**) | -21.2% (not resolved) |
| 20% | -53.2% (**resolved**) | -1.6% (**resolved**) | +0.5% (not resolved) | -36.4% (**resolved**) |

## What replicates, and what does not

**The speed-smoothing dose-response replicates cleanly.** σ_v falls monotonically
with penetration — −8.2% at 1%, −24.2% at 5%, −53.2% at 20% — every step
statistically resolved, on different geometry, a different fleet and different
demand from the corridor that produced the original finding. This is the core
claim of the project and it survives the change.

**The "no cost" part of the finding does not replicate.** On this corridor
FollowerStopper carries a small but *resolved* throughput cost at every
penetration (−0.3% to −1.6%), where `corridor_10km` showed none. Fuel is worse:
a resolved **increase** of 1.4–2.7% at 1–10% penetration, against the 2.8–5.7%
*saving* measured on the synthetic corridor. Only at 20% penetration does the
fuel penalty disappear into noise.

**Wave count is not resolved except at 20%.** The middle penetrations move
around non-monotonically (+18% at 2%, +24% at 5%, both unresolved) before
falling to −36.4% at 20%. On a 640 m site only a handful of wave events occur
per run, so wave count is a low-count statistic here; σ_v is the better-powered
measure at this site.

## Why the difference is plausible

Nothing here is a contradiction of the physics — the two corridors differ in
ways that bear directly on cost:

* **Saturation.** The replica runs at ~8,050 veh/h across 5 lanes on 640 m with
  a congested downstream boundary. Where capacity binds, a vehicle that holds a
  larger gap directly reduces discharge; on the uncongested synthetic corridor
  there was spare capacity to absorb it.
* **Multi-lane behaviour.** `corridor_10km` is single-lane, so nothing can pass.
  On five lanes, neighbours change lanes around a slower AV, and those merges
  are themselves accel/decel events — a plausible route to more fuel burn even
  while the AV smooths its own lane.
* **Site length.** 640 m is a fraction of a wave's wavelength; a controller has
  little room to work, and metrics are dominated by boundary effects.

These are hypotheses consistent with the numbers, not tested mechanisms. Testing
them would mean a long multi-lane corridor — which is exactly what an I-24
MOTION or highD-calibrated flagship would provide.

## Honest summary

The claim that sparse controlled vehicles measurably smooth traffic **holds on
real geometry**. The claim that they do so **for free** is corridor-dependent:
true on the uncongested synthetic corridor, false on this saturated 5-lane site,
where 1–10% penetration buys smoother speeds at a cost of roughly 1% throughput
and 1–3% fuel. Any deployment argument must be made per corridor, with its own
calibration, and cannot be transferred from a synthetic study.

## Limitations

* The replica's known validation failures apply in full
  ([M3_US101_VALIDATION.md](M3_US101_VALIDATION.md)); this inherits them.
* One controller (FollowerStopper), one compliance level (100%).
* 640 m site with a measured downstream boundary: results are dominated by
  boundary conditions in a way a longer corridor would not be.
* Fuel comes from SUMO's HBEFA4 model, unvalidated against measured consumption.
