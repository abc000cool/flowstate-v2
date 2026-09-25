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

> **Correction (2026-09-25).** The throughput column below was measured at x = 400 m in trajectory coordinates, which is inside the
> 640 m insertion buffer, upstream of the replica (and travel time over 100–600 m likewise); σ_v, fuel and waves cover the whole recorded
> road. The throughput cost is therefore not a measurement of the replica. The re-run described in "Testing the multi-lane hypothesis"
> below reports throughput and travel time on the replica beside the original definitions.


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

> The throughput part of this summary rests on the column corrected above (measured upstream of the replica); the fuel and σ_v parts do not.


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

## Testing the multi-lane hypothesis — method (2026-09-25)

**Status: method and code only. The result will come from the VM run** of the
opt-in pipeline stage `us101_lane_changes` (`scripts/gcp/pipeline_i24.sh` §14),
which has not been launched. Nothing in this section is a result.

**What is tested.** The "multi-lane behaviour" explanation above
(docs/ROADMAP.md §5 D2), one link at a time: do humans change lanes more when
AVs are present, are the extra changes made behind the AVs, do the humans (and
not the AVs themselves) burn the extra fuel, and do drivers who change lanes
burn more than drivers who do not.

**The runs.** The sweep behind this document, re-run unchanged by
`scripts/us101_penetration_sweep.py`: baseline and FollowerStopper at 1, 2, 5,
10 and 20 %, 100 % compliance, the same 20 common-random-number seeds
(`spawn_seeds(42, 20)`), the measured downstream boundary. A cloud VM has no
`runs/m3_us101` snapshot, so the boundary comes from the committed
`scenarios/us101_replica_calibrated.yaml`, which carries the same schedule: the
replica with it has the snapshot's config hash (ab879e240aed, checked
2026-09-25). The cells' hashes under the current hash policy are ab879e240aed
(baseline), 1dfb116ac999 (1 %), e0dc898e0fc9 (2 %), 258d7c037638 (5 %),
7f646d6e4be8 (10 %) and ded8acc47c9e (20 %); the summary artifact quotes the
policy-v1 hashes of 2026-08-30. The engine has changed since then, so the fuel
increase has to reproduce on the new runs before the explanation can be
tested, and the analysis recomputes the original metrics with the original
definitions for that. Every trajectory is kept on the VM.

**Definitions** (the full text is the docstring of `scripts/us101_lane_changes.py`):

* *Lane changes* are read by the `calibration.lane_change_gaps` detector with
  its 1 s A-B-A debounce. They are counted per vehicle-km inside the replica's
  640 m, after the 180 s warm-up; the insertion and exit buffers are excluded.
  Human changes are per human vehicle-km, AV changes per AV vehicle-km.
* A *pass-around* is a human change whose origin-lane leader was an AV, at the
  change or at any of the changer's samples in that lane during the 5 s
  before. The leader is the nearest vehicle ahead in the changer's lane,
  within 200 m. The stricter reading, at the change only, is reported too. A
  *cut-in* is a human change into the gap ahead of an AV: its new follower is
  an AV.
* *Counterfactual.* Under common random numbers, the vehicles that are AVs at
  a penetration are ordinary drivers in the same seed's baseline, because the
  AV tags are drawn after the drivers. The baseline's changes behind those
  same vehicles are the pass-arounds that happen without the controller. The
  *excess* is the level's rate minus that.
* *Fuel.* The runner records only whole-trip totals per vehicle
  (`meta.json` `fuel_ml_per_vehicle`, HBEFA4, from the insertion buffer to the
  exit), so fuel per km is whole-trip. The analysis reports the humans' and the
  AVs' ratios and the exact split of the change in fuel between them. Humans
  with a whole journey are binned by 0, 1 and 2+ lane changes; this relation is
  associational.

**Decision rule, written before any result.** At each penetration a check
holds when its 95 % interval over seeds lies above zero:

* (a) the fuel increase reproduces: the paired change in `compute_metrics`'
  fuel per vehicle-km;
* (b) humans change lanes more: the paired change in human changes per human
  vehicle-km;
* (c) the extra changes are behind the AVs: the excess pass-arounds;
* (d) the humans burn more: the paired change in the humans' fuel per km;
* (e) within the level's runs, humans who changed lanes burn more per km than
  humans who did not.

Where (a) fails the level is not applicable. The hypothesis is supported where
(b) to (e) all hold. It is killed at a level where the fuel increase
reproduces but the humans do not change lanes more (b), or their fuel does not
rise (d): the increase is then the AVs' own consumption. A diagnostic reports
whether humans who never changed lanes also burn more, which would be a part
of the increase that does not go through lane changes.

**A note on the original metrics.** `scripts/us101_penetration_analyze.py`
measures throughput at x = 400 m and travel time over 100–600 m. Those are
trajectory coordinates, in which the first 640 m are the insertion buffer
(`microsim.networks.corridor`); a 300 m test corridor run on 2026-09-25
recorded `x_first_edge_m` = 300 m and first samples from 5.1 m. So the
throughput column above was measured upstream of the replica, while σ_v, fuel
and waves cover the whole recorded road, as
[M3_US101_VALIDATION.md](M3_US101_VALIDATION.md) notes for `compute_metrics`.
The stage reports the original definitions and, beside them, throughput and
travel time on the replica itself.

**Cost.** 120 runs. The 20-seed with-boundary battery simulated in 12.4 s of
wall time on a pipeline VM (`artifacts/us101_validation_calibrated.json`,
committed 2026-09-17, `arms.with_boundary.simulated.wall_s`). The analysis took about 1 s and 0.5 GB
per run on a synthetic run of the replica's size (588,212 rows).

## Testing the multi-lane hypothesis — result (2026-09-25, VM AB)

The `us101_lane_changes` stage (snapshot 763a429, n2-standard-8, 243 s) re-ran the sweep — baseline and FollowerStopper at 1, 2, 5,
10 and 20 %, 100 % compliance, the same 20 seeds — with every trajectory kept, and counted lane changes on the replica
(`artifacts/us101_lane_change_penetration.json`). Paired against each seed's baseline, means with 95 % intervals over the 20 seeds:

| penetration | fuel (all vehicles) | human lane changes per human veh-km | excess cut-ins ahead of an AV | excess passes around an AV | humans who never changed lanes, fuel | throughput on the replica (original x = 400 m) | pre-registered verdict |
|---|---|---|---|---|---|---|---|
| 1 % | +1.29 % | +0.034 [0.028, 0.040] | +0.021 | +0.000 [−0.000, 0.001] | +0.75 ml/km [0.32, 1.19] | −0.72 % (−0.20 %) | not supported (c) |
| 2 % | +1.91 % | +0.064 [0.058, 0.071] | +0.043 | +0.001 [−0.000, 0.003] | +1.11 ml/km [0.69, 1.52] | −1.05 % (−0.46 %) | not supported (c) |
| 5 % | +2.21 % | +0.108 [0.098, 0.118] | +0.088 | +0.007 [0.006, 0.009] | +0.96 ml/km [0.36, 1.56] | −1.69 % (−0.89 %) | supported |
| 10 % | +1.14 % | +0.148 [0.137, 0.159] | +0.129 | +0.022 [0.018, 0.025] | −0.06 ml/km [−0.54, 0.43] | −1.97 % (−1.47 %) | supported |
| 20 % | +0.44 % (not resolved) | +0.132 [0.120, 0.145] | +0.114 | +0.037 [0.031, 0.043] | −0.83 ml/km [−1.21, −0.45] | −2.74 % (−1.89 %) | not applicable (a) |

The baseline's human rate is 0.049 changes per human veh-km. At every level humans who changed lanes burn about 6 ml/km more than
those who did not (5.8–6.3 ml/km, every interval above zero), and the extra fuel is almost all the humans' (at 5 %: 1.40 of 1.48 ml per
veh-km).

**Reading.** The fuel increase reproduces at 1–10 % and fades at 20 %, as first published. Humans do change lanes much more around
FollowerStopper vehicles — two to four times the baseline rate — and changing lanes costs fuel. But the extra changes are not mainly
humans *leaving* an AV's lane to pass it, which is what the pre-registered check (c) tested: at 1 and 2 % there is no such excess at
all, hence "not supported" there. They are humans **cutting in to the larger gap the AV keeps ahead of it**: 80–95 % of the excess
changes. And at 1–5 % humans who never change lanes also burn more, so lane changes are not the whole mechanism. The stated
hypothesis therefore holds in its broad form (multi-lane interaction costs fuel) but not in its specific one (passing around the AV);
the measured mechanism is cut-ins into the controller's gap. Measured on the replica, the throughput cost is 0.7–2.7 %, about twice
the published figures, which were measured upstream of it (correction note above).
