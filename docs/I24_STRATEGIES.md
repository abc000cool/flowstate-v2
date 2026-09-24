# Operational strategies on the I-24 replica: VSL and ALINEA beside FollowerStopper

**Status (2026-09-23):** the first strategy sweep on a validated corridor. The
run is described here; the numbers are filled from
`artifacts/sweep_i24_strategies_summary.json` when the round lands (nothing
below is quoted from anything else).

## Setup

- Scenario: `scenarios/i24_replica_flow_speedcal_ramps.yaml` — the canonical
  I-24 westbound arm with the Old Hickory and Hickory Hollow ramps
  (docs/I24_VALIDATION.md). Nothing in the scenario was changed; every cell is
  a patch of it recorded by config hash in the summary.
- Grid (`scripts/corridor_sweep.py`, 20 seeds per cell, the same seed list in
  every cell so deltas are paired): `baseline`; `strategy_vsl` (VSL threshold
  ladder on every 1 km gantry segment, no AVs); `strategy_alinea` (ALINEA on
  every on-ramp, no AVs); `follower_stopper_p0.10_c1.00_{none,vsl,alinea}`
  (10 % FollowerStopper at full compliance under each).
- ALINEA target: 29.2 veh/km/lane, the critical density implied by the
  capacity-scaled population's equilibrium capacity (1,985.5 veh/h/lane at
  18.912 m/s, `artifacts/idm_i24_capacity_equilibrium.json`; density = flow ÷
  speed). Gain and rate bounds are the controller defaults
  (`controllers.ramp_meter.ALINEA_DEFAULTS`).
- Metrics on the measured span (data x 2,256–7,638 m of the I-24 MOTION
  recording; throughput at data x = 2,200 m), warm-up discarded per the
  scenario, 95 % t-intervals over seeds and paired deltas versus baseline.

## Results

**ALINEA did not run.** Every ALINEA cell failed in every seed at the same
vehicle on the Hickory Hollow Pkwy entrance: the meter's stop line (30 m before
the end of the ramp's last edge, `19441652#1`) is refused by SUMO for a
vehicle that cannot brake in time (`TraCIException: stop … is too close to
brake`). The defect is recorded in the changelog and lesson 31.

**FollowerStopper under VSL is incomplete:** the VM's watcher deadline (75
minutes) cut the stage after 6 of its 20 seeds; the artifact lists it under
`incomplete_cells` and the tables below omit it. The three complete cells
(baseline, VSL alone, FollowerStopper 10 % alone) carry 20 seeds each.

Per-cell means with 95 % t-intervals over the seeds (`aggregate`), then the
paired per-seed deltas versus baseline with their 95 % intervals and the share
of the baseline mean (`vs_baseline_paired`; "resolved" = the interval excludes zero).

| cell | n | throughput [veh/h] | mean travel time [s] | σ_v temporal [m/s] | fuel [ml/veh-km] | waves |
|---|---|---|---|---|---|---|
| baseline | 20 | 5,671 [5,662, 5,681] | 575.4 [565.5, 585.2] | 4.76 [4.71, 4.80] | 89.6 [88.6, 90.6] | 10.2 [9.0, 11.5] |
| VSL alone | 20 | 5,252 [5,233, 5,271] | 620.7 [614.0, 627.5] | 3.37 [3.35, 3.39] | 104.2 [103.6, 104.8] | 17.2 [15.8, 18.7] |
| FollowerStopper 10 % | 20 | 2,859 [2,399, 3,319] | 1,292.7 [1,091.7, 1,493.6] | 1.93 [1.85, 2.00] | 275.9 [229.0, 322.8] | 9.0 [7.0, 11.0] |
| FollowerStopper 10 % + VSL | 6 of 20 | cut by the VM deadline — not reported (see above) | — | — | — | — |
| ALINEA alone | 0 | failed (see above) | failed (see above) | failed (see above) | failed (see above) | failed (see above) |
| FollowerStopper 10 % + ALINEA | 0 | failed (see above) | failed (see above) | failed (see above) | failed (see above) | failed (see above) |

Paired deltas versus baseline:

| cell | Δ throughput [veh/h] | Δ mean travel time [s] | Δ σ_v temporal [m/s] | Δ fuel [ml/veh-km] | Δ waves |
|---|---|---|---|---|---|
| VSL alone | -420 [-441, -398] (-7.4 %, resolved) | +45.4 [+33.5, +57.2] (+7.9 %, resolved) | -1.39 [-1.44, -1.34] (-29.1 %, resolved) | +14.6 [+13.4, +15.8] (+16.3 %, resolved) | +7.0 [+5.1, +8.9] (+68.3 %, resolved) |
| FollowerStopper 10 % | -2,812 [-3,271, -2,354] (-49.6 %, resolved) | +717.3 [+516.1, +918.5] (+124.7 %, resolved) | -2.83 [-2.92, -2.74] (-59.5 %, resolved) | +186.3 [+139.4, +233.3] (+208.0 %, resolved) | -1.2 [-3.5, +1.0] (-12.2 %) |

Incomplete cells in the artifact: ['follower_stopper_p0.10_c1.00_alinea', 'follower_stopper_p0.10_c1.00_vsl', 'strategy_alinea']; seeds per cell: 20; base config hash `0cddf2002979`; ALINEA target 29.2 veh/km/lane; metrics args {'x_ref': 4411.8, 'span': [2256.2, 7637.8]}.

## Reading

The VSL threshold ladder at its defaults is a net loss on this corridor: it
lowers the temporal speed standard deviation (-29.1 %) and the
wave amplitude (-24.5 %), but throughput falls -7.4 %, mean
travel time rises +7.9 % (p90 +22.8 %), fuel per vehicle-km rises
+16.3 %, and the corridor carries more waves (+68.3 %), slower
ones (-35.7 %). Every delta is paired over the 20 seeds and resolved.
This is the same trade the FollowerStopper sweep found (docs/I24_SWEEP.md):
smoothing is paid for in capacity, and a posted-speed ladder pays more of it
than gap-keeping vehicles do. It is one ladder at default thresholds on one
corridor; a tuned ladder is a different experiment, not a correction of this
one.

**FollowerStopper 10 % alone** (20 seeds, paired) on this ramps arm: σ_v -59.5 %, throughput -49.6 %, mean travel time +124.7 %, fuel +208.0 % — the same direction and a larger size than the published dose-response on the arm without ramps (docs/I24_SWEEP.md), which is the corridor's known behaviour: gap-keeping vehicles smooth the field by starving it. Beside it, the VSL ladder buys a smaller σ_v reduction for a smaller capacity cost; neither is a free improvement here.

**Cells not measured:** FollowerStopper under VSL (6 of 20 seeds before the
cut-off; not reported) and both ALINEA cells (the stop-line defect).


## 2026-09-24 — ALINEA ran: 20 seeds paired, after the stop-placement fix

The ramp-meter stop placement was fixed on 2026-09-24 (CHANGELOG; a vehicle
that cannot brake for the line passes that cycle and is counted), and the
six-cell grid was rerun on a self-deleting VM (`flowstate-strat-a`,
n2-standard-32, us-west1-c; stage `sweep_i24_strat`). The instance was
deleted by its own service account at 05:40:59 UTC with the sweep at 112 of
120 runs (the pipeline logged a SIGTERM and archived on exit; the guest logs
did not survive, so the cause is not proven — the idle guard is the only
other actor with that identity, and its 75-minute grace was minutes away).
The 112 per-run metrics were fetched and the summary rebuilt with
`--analyze-only --allow-partial` (`artifacts/sweep_i24_strategies_summary.json`,
`incomplete_cells` lists the two cut cells).

**ALINEA alone versus baseline, 20 seeds paired** (per-seed deltas, 95 % t-intervals; `resolved` = the interval excludes zero):

| metric | baseline mean | ALINEA mean | Δ vs baseline | paired Δ [95 % CI] | resolved |
|---|---|---|---|---|---|
| throughput [veh/h] | 5671.35 | 5344.35 | -5.8 % | -327.00 [-337.4, -316.6] | yes |
| mean travel time [s] | 575.36 | 383.22 | -33.4 % | -192.14 [-202.8, -181.5] | yes |
| σ_v spatial [m/s] | 5.58 | 4.92 | -11.7 % | -0.65 [-0.7, -0.6] | yes |
| fuel [ml/veh-km] | 89.60 | 75.59 | -15.6 % | -14.01 [-15.1, -12.9] | yes |
| wave count | 10.25 | 43.75 | +326.8 % | +33.50 [+30.5, +36.5] | yes |
| wave amplitude [m/s] | 7.00 | 8.88 | +26.8 % | +1.88 [+1.6, +2.2] | yes |
| wave speed [km/h] | 10.37 | 12.42 | +19.8 % | +2.05 [+1.3, +2.8] | yes |

Reading: metering the two entrances (Old Hickory Blvd, Hickory Hollow Pkwy) at the corridor's critical density
(29.2 veh/km/lane) lowers the mainline travel time by a third and fuel per
vehicle-kilometre by 16 % at the price of 5.8 % of the throughput measured at
the reference section (the held ramp vehicles are not on the mainline), and
it multiplies the number of detected waves while making them larger: the
stop-and-go pattern moves from a few long waves to many short ones. Two
things are outside these numbers and must be read with them: the ramp queue
wait is not part of `mean_tt_s` (the travel-time span is the mainline
2,256–7,638 m, and a metered vehicle's wait at the stop line lies upstream of
it), and the meter counters (`n_released`, `n_passed_unstoppable` per ramp)
were not archived by this round (the archive carries `metrics.json` only), so
the share of vehicles that passed the meter unstoppable is not known for
these runs — the next round archives `meta.json` with them.

The two combined cells are **not reported**: FollowerStopper 10 % under ALINEA
reached 15 seeds and under VSL 17 seeds before the instance was deleted; their
aggregates are in the artifact under `incomplete_cells` and are not headline
numbers.
