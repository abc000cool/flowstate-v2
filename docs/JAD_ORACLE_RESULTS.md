# JAD under a degraded wave-detection oracle

**Date:** 2026-08-30 · **Scenario:** `corridor_10km` (EIDM, emergent/unseeded),
5% penetration / 100% compliance · **Cells:** 4 x 20 common-random-number seeds ·
**Artifacts:** `artifacts/jad_oracle_summary.json`, `runs/jad_oracle/analysis.json` ·
**Scripts:** `scripts/jad_oracle_experiment.py`, `scripts/jad_oracle_analyze.py`

CLAUDE.md §4.3 requires the wave-detection oracle to be swappable and **every
headline JAD result to be reported under the delayed/noisy variant**, not only
under a perfect one. That requirement was outstanding until now. Delivering it
also explains the bimodality M3 flagged.

## 1. Setup

`AVSpec.oracle` (`OracleSpec`, docs/CONTRACTS.md §2) degrades what the
controller sees, never what the simulator knows:

* **delay** — the controller reads the downstream speed field as it was
  `delay_s` ago; its own position stays current, matching loop-detector or
  probe latency;
* **amplitude noise** — each observed bin speed is multiplied by
  `1 + U(-f, +f)`, drawn per bin per control step from the run's seeded RNG.
  Empty bins stay empty; speeds are floored at zero.

Cells: perfect; 30 s + 20% noise; 60 s + 20% noise (the top of §4.3's 10–60 s
range).

## 2. Results

Marginal means, 95% t-CIs over 20 seeds:

| Metric | baseline | perfect | 30 s + 20% | 60 s + 20% |
|---|---|---|---|---|
| Throughput [veh/h] | 1246.65 [1226.90, 1266.40] | 1161.61 [1049.66, 1273.55] | 1259.12 [1248.34, 1269.89] | 1261.07 [1251.13, 1271.01] |
| σ_v temporal [m/s] | 3.39 [2.83, 3.94] | 1.78 [1.30, 2.26] | 1.33 [1.24, 1.42] | 1.29 [1.23, 1.35] |
| Fuel [ml/veh-km] | 65.44 [64.57, 66.32] | 67.15 [60.45, 73.84] | 62.17 [61.89, 62.45] | 62.20 [61.92, 62.48] |
| Waves per run | 3.85 [2.59, 5.11] | 2.10 [0.12, 4.08] | 0.35 [-0.28, 0.98] | 0.10 [-0.11, 0.31] |

Paired per-seed deltas against baseline (common random numbers):

| Metric | perfect | 30 s + 20% | 60 s + 20% |
|---|---|---|---|
| Throughput | -85.04 [-193.93, +23.84] (not resolved) | +12.47 [-0.74, +25.68] (not resolved) | +14.42 [+1.50, +27.34] (**resolved**) |
| σ_v temporal | -1.60 [-2.27, -0.94] (**resolved**) | -2.05 [-2.62, -1.49] (**resolved**) | -2.09 [-2.66, -1.52] (**resolved**) |
| Fuel | +1.70 [-4.69, +8.09] (not resolved) | -3.27 [-4.33, -2.21] (**resolved**) | -3.25 [-4.30, -2.19] (**resolved**) |
| Wave count | -1.75 [-4.04, +0.54] (not resolved) | -3.50 [-4.71, -2.29] (**resolved**) | -3.75 [-4.95, -2.55] (**resolved**) |

Seeds ending with **more** waves than the uncontrolled baseline:

| Oracle | seeds worse / 20 |
|---|---|
| perfect | **5** |
| 30 s + 20% noise | 0 |
| 60 s + 20% noise | 0 |

## 3. The headline, stated carefully

**Realistic detection does not degrade JAD here — it is what makes JAD reliable.**
Under a perfect oracle the wave-count benefit is *not statistically resolved*
(-1.75 [-4.04, +0.54] (not resolved)) because the controller helps on most seeds and
badly hurts on a few: 5 of 20 seeds finish with more waves than doing nothing,
one going from 1 wave to 11. That is the bimodality M3 reported. Under 30–60 s
of latency with ±20% speed error, **no seed is worse than baseline**, and wave
count, σ_v and fuel all improve with resolved CIs.

This inverts the usual expectation that idealised sensing flatters a controller.
It should be read as a finding about *this commit rule*, not as an argument for
bad sensors.

## 4. Mechanism, measured not assumed

JAD's intercept geometry ([jad_derivation.md](jad_derivation.md) §2) assumes the
vehicle commits when the front is a useful distance away. A perfect oracle fires
the instant any bin inside the 2 km lookahead qualifies — often far too early.
The AV then completes slow-in, hold and fast-out before the front arrives,
recovers, re-detects the same wave, and repeats. Each abrupt fast-out compresses
the platoon behind it and can seed a secondary wave.

The chattering is directly observable in the trajectories — mean per-AV
acceleration sign-reversals and speed variability per run:

| Oracle | accel reversals / run | AV speed σ [m/s] |
|---|---|---|
| perfect | **30.7** | 1.46 |
| 30 s + 20% noise | 16.6 | 0.75 |
| 60 s + 20% noise | 15.8 | 0.70 |

Latency roughly halves the oscillation, and the wave outcomes follow.

> **Built (2026-09-03):** the deferral rule proposed below now exists as
> `controllers.jad`'s `commit_delay_s` and reproduces this result with a
> *perfect* sensor — σ_v 1.331 [1.228, 1.435] m/s at 30 s deferral against
> 1.333 [1.244, 1.422] for the 30 s noisy oracle, 0/20 seeds worse than
> baseline, a resolved 25% σ_v improvement over the undeferred controller.
> See [JAD_DEFERRAL_RESULTS.md](JAD_DEFERRAL_RESULTS.md).

## 5. What this suggests next (not built)

If the benefit comes from *deferring commitment*, then a controller that uses
`JAD-1` to wait until the front is within `v_slow · t_int` should capture it
with a perfect sensor and no artificial lag — plausibly beating both cells here.
That is the obvious next iteration. It has not been implemented or tested, and
nothing in this document should be read as evidence for it.

## 6. Limitations

* One corridor, one penetration/compliance point (5% / 100%), EIDM fleet with
  screening-calibrated demand: the caveats in [M3_RESULTS.md](M3_RESULTS.md) §1
  apply unchanged. This is not a validated real corridor.
* Noise is multiplicative and independent per bin per step; real detector error
  is correlated in space and time, which is a gentler perturbation than modelled
  in some respects and a harsher one in others.
* `w_wave` remains a fixed parameter rather than a measured per-wave quantity.
* Two latency values were tested, not a sweep; the 30 s and 60 s results are
  statistically indistinguishable from each other.
