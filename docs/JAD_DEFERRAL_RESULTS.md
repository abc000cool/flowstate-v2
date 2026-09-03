# Deferred-commitment JAD: turning the latency finding into a design rule

**Date:** 2026-09-03 · **Scenario:** `corridor_10km` (EIDM, emergent, unseeded)
· **5% penetration / 100% compliance · 20 common-random-number seeds per cell** ·
**Artifact:** `artifacts/jad_deferral_summary.json` · **Script:**
`scripts/jad_deferral_experiment.py` · **Controller change:**
`controllers.jad`, parameter `commit_delay_s` (default 0 = the 2.1.0 controller).

## The question

[JAD_ORACLE_RESULTS.md](JAD_ORACLE_RESULTS.md) found that Jam-Absorption
Driving is *unreliable with a perfect oracle* — it commits to every transient
jammed bin, finishes its slow-in/hold/fast-out cycle before the front arrives,
re-triggers, and each abrupt fast-out can seed a secondary wave, so 5 of 20
seeds ended worse than no control — and that 30–60 s of detection latency
removed the failure. ROADMAP B4 asked: if latency helps *because it defers
commitment*, an explicit deferral rule should capture the benefit with a
perfect sensor. `commit_delay_s` is that rule: from CRUISE, slow-in starts only
once a wave has been detected continuously for that long; a detection that
disappears resets the clock (unit tests in
`tests/test_controllers/test_controllers_jad.py::TestDeferredCommitment`).

## Cells

| Cell | Oracle | `commit_delay_s` | Config hash |
|---|---|---|---|
| baseline | — (no controller) | — | `f4e7628438f7` |
| jad_perfect | perfect | 0 (the 2.1.0 controller) | `4bfb2f90890c` |
| jad_defer30 | perfect | 30 s | `2b2e71d63422` |
| jad_defer60 | perfect | 60 s | `96a21f998d9c` |
| jad_noisy_30s | 30 s latency, ±20% noise | 0 (the 2.1.0 reference) | `9b71ce90e8de` |

The baseline and the two 2.1.0 cells reproduce the earlier experiments to the
digit (temporal σ_v 3.385 [2.828, 3.942], 1.781 [1.298, 2.264] and
1.333 [1.244, 1.422] m/s; [CONTROLLER_COMPARISON.md](CONTROLLER_COMPARISON.md))
although their config hashes changed with the Phase-6 schema fields — the
intended evidence that the new defaults are inert.

## Absolute results (mean, 95% t CI, n = 20)

| Cell | σ_v temporal [m/s] | Waves / run | Throughput [veh/h] | Fuel [ml/veh-km] |
|---|---|---|---|---|
| baseline | 3.385 [2.828, 3.942] | 3.85 [2.59, 5.11] | 1327 [1309, 1345] | 65.44 [64.57, 66.32] |
| jad_perfect | 1.781 [1.298, 2.264] | 2.10 [0.12, 4.08] | 1233 [1113, 1352] | 67.15 [60.45, 73.84] |
| **jad_defer30** | **1.331 [1.228, 1.435]** | **0.25 [−0.27, 0.77]** | 1337 [1327, 1348] | 62.20 [61.93, 62.48] |
| **jad_defer60** | **1.294 [1.234, 1.354]** | **0.05 [−0.06, 0.16]** | 1338 [1329, 1348] | 62.20 [61.92, 62.48] |
| jad_noisy_30s | 1.333 [1.244, 1.422] | 0.35 [−0.28, 0.98] | 1337 [1327, 1347] | 62.17 [61.89, 62.45] |

## Paired change (per seed, common random numbers)

vs the uncontrolled baseline:

| Cell | σ_v temporal | Wave count | Throughput | Fuel | Seeds worse than baseline (σ_v) |
|---|---|---|---|---|---|
| jad_perfect | −47.4% (**resolved**) | −45.5% (not resolved) | −7.1% (not resolved) | +2.6% (not resolved) | 1/20 |
| jad_defer30 | −60.7% (**resolved**) | −93.5% (**resolved**) | +0.8% (not resolved) | −5.0% (**resolved**) | 0/20 |
| jad_defer60 | −61.8% (**resolved**) | −98.7% (**resolved**) | +0.8% (not resolved) | −5.0% (**resolved**) | 0/20 |
| jad_noisy_30s | −60.6% (**resolved**) | −90.9% (**resolved**) | +0.7% (not resolved) | −5.0% (**resolved**) | 0/20 |

vs `jad_perfect` (the undeferred controller with the same perfect sensor):

| Cell | σ_v temporal | Wave count | Throughput | Fuel |
|---|---|---|---|---|
| jad_defer30 | −25.2% [−0.89, −0.01 m/s] (**resolved**) | −88.1% (**resolved**, just) | +8.5% (not resolved) | −7.4% (not resolved) |
| jad_defer60 | −27.3% [−0.95, −0.02 m/s] (**resolved**) | −97.6% (**resolved**) | +8.6% (not resolved) | −7.4% (not resolved) |
| jad_noisy_30s | −25.2% [−0.88, −0.01 m/s] (**resolved**) | −83.3% (not resolved) | +8.5% (not resolved) | −7.4% (not resolved) |

## Reading it

**The rule captures the benefit.** With a perfect sensor and a 30 s deferral,
JAD reaches σ_v 1.331 [1.228, 1.435] m/s, 0.25 waves per run, a resolved 5.0%
fuel saving and no seed worse than baseline — numerically the same as the
noisy-oracle cell (1.333, 0.35, −5.0%, 0/20) that produced the original
finding, and a resolved 25% σ_v improvement over the undeferred controller on
the same sensor. Sixty seconds is marginally better still (1.294 m/s, 0.05
waves, −98.7% waves resolved). What made JAD reliable was never the *quality*
of the sensing; it was that a late sensor cannot chase transients.

**It is now a design choice, not an accident.** The 2.1.0 result could be read
as "worse sensors are better", which is not a rule anyone can deploy. The
deferral parameter says what to do: on the corridor, do not commit to a
slow-in before a detected front has persisted for 30–60 s. It also composes
with realistic sensing — deferral and latency are the same mechanism, so the
noisy-oracle results should be read as deferral by another route.

**Cost.** Throughput and travel time are unchanged within CIs against the
baseline (+0.8%, not resolved; mean travel time 565 vs 567 s), as they were
for every reliable JAD variant. Nothing resolved separates 30 s from 60 s at
this penetration; the wave count favours 60 s.

## Limitations

* Synthetic corridor, EIDM default fleet, one penetration and compliance;
  the same caveats as [M3_RESULTS.md](M3_RESULTS.md) §limitations. On the
  I-24 replica JAD has not been run at all — the flagship sweep is
  FollowerStopper (docs/ROADMAP.md §1.5).
* The deferral clock restarts when a detection disappears; a front that
  flickers around the 40 km/h threshold could postpone commitment
  indefinitely. No such case appeared in 20 seeds, but the rule has no
  hysteresis on the speed threshold itself.
* 30 and 60 s were chosen from the §4.3 latency range, not optimised.
* Wave-amplitude CIs for the deferral cells are undefined (too few waves
  to average) and are omitted.
