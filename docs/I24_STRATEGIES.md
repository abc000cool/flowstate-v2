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
brake`). The defect is recorded in the changelog and lesson 31; the four
remaining cells (baseline, VSL alone, FollowerStopper 10 % alone and under
VSL) are reported below from the summary artifact.

Per-cell means with 95 % t-intervals over the seeds (`aggregate`), then the
paired per-seed deltas versus baseline with their 95 % intervals and the share
of the baseline mean (`vs_baseline_paired`; "resolved" = the interval excludes zero).

| cell | n | throughput [veh/h] | mean travel time [s] | σ_v temporal [m/s] | fuel [ml/veh-km] | waves |
|---|---|---|---|---|---|---|
| baseline | 20 | 5,671 [5,662, 5,681] | 575.4 [565.5, 585.2] | 4.76 [4.71, 4.80] | 89.6 [88.6, 90.6] | 10.2 [9.0, 11.5] |
| VSL alone | 20 | 5,252 [5,233, 5,271] | 620.7 [614.0, 627.5] | 3.37 [3.35, 3.39] | 104.2 [103.6, 104.8] | 17.2 [15.8, 18.7] |
| FollowerStopper 10 % | 0 | failed (see above) | failed (see above) | failed (see above) | failed (see above) | failed (see above) |
| FollowerStopper 10 % + VSL | 0 | failed (see above) | failed (see above) | failed (see above) | failed (see above) | failed (see above) |
| ALINEA alone | 0 | failed (see above) | failed (see above) | failed (see above) | failed (see above) | failed (see above) |
| FollowerStopper 10 % + ALINEA | 0 | failed (see above) | failed (see above) | failed (see above) | failed (see above) | failed (see above) |

Paired deltas versus baseline:

| cell | Δ throughput [veh/h] | Δ mean travel time [s] | Δ σ_v temporal [m/s] | Δ fuel [ml/veh-km] | Δ waves |
|---|---|---|---|---|---|
| VSL alone | -420 [-441, -398] (-7.4 %, resolved) | +45.4 [+33.5, +57.2] (+7.9 %, resolved) | -1.39 [-1.44, -1.34] (-29.1 %, resolved) | +14.6 [+13.4, +15.8] (+16.3 %, resolved) | +7.0 [+5.1, +8.9] (+68.3 %, resolved) |

Incomplete cells in the artifact: ['follower_stopper_p0.10_c1.00_alinea', 'follower_stopper_p0.10_c1.00_none', 'follower_stopper_p0.10_c1.00_vsl', 'strategy_alinea']; seeds per cell: 20; base config hash `0cddf2002979`; ALINEA target 29.2 veh/km/lane; metrics args {'x_ref': 4411.8, 'span': [2256.2, 7637.8]}.

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

**Status of the other cells:** the FollowerStopper cells were still running
on the VM when this document was written; if they finished before the block's
cut-off they appear in the tables above (the artifact's `incomplete_cells`
lists any that did not), and ALINEA did not run (see Results).
