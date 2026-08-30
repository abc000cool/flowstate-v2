# M3 US-101 Validation — Observed vs Simulated

Phase-3 (M3) validation of the `us101_replica` scenario against the real
NGSIM US-101 recording, plus the moving-bottleneck variant comparison
(CLAUDE.md §5.5) and the end-to-end auto-report (§7.4). Every number in this
document traces to a machine-readable results artifact produced by the
driver scripts (run order, from the repo root, all with explicit seeds):

```
uv run --no-sync python scripts/m3_us101_validate.py     # both arms, 20 replicates each
uv run --no-sync python scripts/m3_fluxcap_compare.py    # flux-cap variants, 20 replicates
uv run --no-sync python scripts/m3_us101_report.py       # auto-report
```

Artifacts: `runs/m3_us101/results_no_boundary.json`,
`runs/m3_us101/results_with_boundary.json`, `runs/m3_fluxcap/results.json`,
`docs/reports/us101_replica/report.md`,
`docs/figures/fluxcap_comparison.png`. Replicate seeds derive from
`spawn_seeds(42, 20)`; every run's `meta.json` snapshots its full config
(config hashes: no-boundary `bb843086f6b0`, with-boundary `e897b6479ed4`).
All runs are `seeded=False` — no perturbation block anywhere; the measured
boundary schedule is a calibration input, not a seeded shock (see §2).

**Headline honestly stated up front:** with the measured downstream
boundary imposed, segment-speed RMSPE improves from 72.8% to 36.6% and
backward-propagating waves appear in all 20 replicates — but the replica
still **fails** the FHWA-style GEH, RMSPE, and wave-speed acceptance
criteria. The reasons are structural (640 m site, missing on-ramp merge,
IDM congested-discharge behavior) and are documented in §6, not hidden.

> **Follow-up (2026-08-30):** the wave-speed criterion failure was investigated
> separately and is best explained by site length and operating density rather
> than by the calibration: the same fitted fleet produces 14.6 km/h waves on a
> ring at 60 veh/km, matching the independently fitted FD's w = -14.6 km/h. The
> criterion still FAILS as measured here. See
> [WAVE_SPEED_DIAGNOSIS.md](WAVE_SPEED_DIAGNOSIS.md).

## 1. Methodology: observed vs simulated

**Observed side.** Raw NGSIM US-101 (data.transportation.gov Socrata
`8ect-6jqj`; provenance hash `8578f4…95e22` recorded in every artifact),
processed with the same dedup/period logic as M2
(`scripts/us101_data.py`): 21.5% exact-duplicate rows dropped, recording
periods split on the recording origin. Period 1 only (07:49:39.7–08:05:32.5
PDT — the span the replica models), mainline lanes 1–5, all vehicle
classes: 2,169 vehicles. Cached with the data hash in
`runs/m3_us101/observed_us101.json`.

**Simulated side.** `scenarios/us101_replica.yaml`: 640 m, 5-lane corridor,
fleet drawn from the M2 IDM population artifact (`artifacts/idm_us101.json`),
inflow = the M2 deduplicated upstream boundary counts, 180 s warmup +
952.8 s period-1 span. 20 replicates per arm via a spawn process pool.
Demand realization is no longer a gap: 99.85–100% of planned insertions
realized in every replicate (both arms).

**Coordinate mapping.** sim t − 180 s = period-1 wall time; sim x − 640 m
(entry buffer) = NGSIM `local_y`. Identical binning on both sides:

* **Link flows:** interpolated crossing counts at 3 sections (100/320/
  550 m) × 3 full 5-min windows (0–900 s; the trailing partial window is
  excluded), scaled ×12 to hourly volumes; GEH per bin (9 comparisons),
  replicate-mean sim vs observed. Both sides censor vehicles first observed
  at/past a section (recording start vs warmup end are symmetric). Note:
  ~6% of real vehicles enter via the on-ramp inside the site and are
  counted at downstream sections; the replica has no ramp to inject them.
* **Segment speeds:** arithmetic mean of sampled speeds per 5-min window ×
  160 m segment (3 × 4 = 12 bins), replicate-mean sim vs observed → RMSPE.
* **Waves:** `validation.waves.detect_waves` on 15 s × 75 m speed fields,
  clipped to x < 640 m on both sides, plus a stripe-level analysis (§4).
* **Replicate metrics:** `validation.metrics.compute_metrics` per replicate
  (throughput at x = 320 m, travel time over the 640 m span, σ_v, fuel,
  waves) aggregated by `validation.metrics.aggregate` (t-distribution 95%
  CIs over 20 seeds).

## 2. The downstream boundary condition

M2 established that the site's congestion largely enters from DOWNSTREAM
of the 640 m camera range (docs/M2_RESULTS.md §6/§7.3): an inflow-only
replica free-flows at ~63 km/h while the recording crawls at ~40 km/h. No
inflow calibration can fix that — the causal boundary is missing.

**Implementation** (contract extension, docs/CONTRACTS.md §2):
`flowstate_core.config.BoundarySpec` (`kind="speed_schedule"`,
time-ordered `(t_s, v_ms)` steps, `exit_buffer_m = 200`) on
`CorridorNetwork.boundary`. The micro runner appends a 200 m exit-buffer
edge AFTER the corridor proper and applies the schedule there via
`edge.setMaxSpeed` — the constraint acts entirely OUTSIDE the measured
span and congestion spills back into it, exactly as the real downstream
queue did. Imposing measured boundary conditions at the model limits is
standard microsimulation calibration practice (FHWA Traffic Analysis
Toolbox Vol. III, FHWA-HOP-18-036, 2019). The schedule is data-derived, so
runs remain `seeded=False`; its provenance is recorded in the results and
in every `meta.json`.

**Extraction.** Observed mean mainline speed in the last 100 m of the site
(`local_y` ∈ [540, 640) m) per 30 s window, same dedup/period logic as
everything else: 32 windows, 4.8–19.6 m/s. The recording's downstream end
runs at 17–20 m/s for the first ~9 min, sags to ~12 m/s around wall
570–750 s, breaks down to ~5 m/s at ~750–810 s, then recovers — i.e. the
downstream-originating breakdown arrives late in period 1. Sim-time
schedule: first value held through the 180 s warmup, then the 30 s steps
(shifted +180 s). Config snapshot: `runs/m3_us101/us101_replica_with_boundary.yaml`.

## 3. Criteria tables — both arms

FHWA-style profile (`validation.criteria`, 20 seeds each). Ring-benchmark
rows are CI-gated integration tests, not re-run by this driver; they are
reported as **not evaluated and therefore failing** (CLAUDE.md §0.1).

**Arm 1 — no boundary (honest predict-from-nothing):**

| Criterion | Value | Threshold | Result |
|---|---|---|---|
| Link-flow GEH | 77.8% of bins < 5 | ≥ 85% of bins with GEH < 5 | **FAIL** |
| Segment-speed RMSPE | 72.8% | ≤ 15% | **FAIL** |
| Backward wave speed | no backward wave in any of 20 replicates | 14–22 km/h | **FAIL** |
| Ring emergence | not evaluated here (CI-gated) | reproduced | **FAIL (not evaluated)** |
| Ring dampening | not evaluated here (CI-gated) | reproduced | **FAIL (not evaluated)** |
| Replicates | 20 | ≥ 20 | **PASS** |

**Arm 2 — with measured downstream boundary:**

| Criterion | Value | Threshold | Result |
|---|---|---|---|
| Link-flow GEH | 55.6% of bins < 5 | ≥ 85% of bins with GEH < 5 | **FAIL** |
| Segment-speed RMSPE | 36.6% | ≤ 15% | **FAIL** |
| Backward wave speed | 5.8 km/h (std threshold; 10.7 km/h stripe-level), 20/20 replicates | 14–22 km/h | **FAIL** |
| Ring emergence | not evaluated here (CI-gated) | reproduced | **FAIL (not evaluated)** |
| Ring dampening | not evaluated here (CI-gated) | reproduced | **FAIL (not evaluated)** |
| Replicates | 20 | ≥ 20 | **PASS** |

**What changed, and what did not.**

* **Speeds** (replicate-mean, m/s, windows × segments): observed runs
  10.3→17.9 in window 1 degrading to 7.7→10.3 in window 3; no-boundary sim
  sits at a flat ~17–19 everywhere (RMSPE 72.8%). With the boundary the
  sim develops real congestion — window 3 falls to 12.9/11.2/9.1/7.0 —
  and RMSPE halves to 36.6%. But the observed spatial gradient is
  *upstream-slow/downstream-fast* (congestion maintained by the on-ramp
  merge at ~150–165 m plus the downstream queue), while the boundary-driven
  sim is *downstream-slow/upstream-fast*. The remaining error is mostly
  this reversed gradient: the replica has no auxiliary lane/on-ramp, so it
  cannot reproduce the in-span merge bottleneck.
* **Flows:** GEH actually degrades (7/9 → 5/9 bins passing). Two causes,
  both visible in the per-bin table: (i) window 1 at sections 320/550 m
  fails in both arms — the real site is already congested at the recording
  start while the sim warms up from empty (180 s at the first boundary
  value is not enough history); (ii) with the boundary binding, the
  simulated queue discharges at IDM-calibrated headways
  (~0.37 veh/s/lane at 5 m/s), below the observed discharge — window-3
  flows at 320/550 m fall to 7,087 and 6,880 veh/h against 7,608 and
  7,752 observed, deficits of 521 and 872 veh/h (so the largest window-3
  discharge deficit is 872 veh/h, not the ~1,600 a reader would get by
  pairing these sim values against the 8,520 veh/h
  peak, which is window **2** at 550 m — see the flow matrix in
  `observed.hourly_flows_veh_h`, rows = sections, columns = windows).
  Imposing the observed *speed* at the boundary does not impose the
  observed *flow*; the IDM population's congested branch discharges less.
* **Waves:** none at all without the boundary; with it, backward waves in
  **20/20** replicates. Two pipelines report the count and they do not
  agree, so both are named here (the same dual-pipeline gap §7 records for
  wave *speed*): the site-clipped field that matches the observed side —
  the one the 20/20 is counted on — gives a mean count of **1.45**, while
  `compute_metrics` over each run's full network extent gives 1.65, 95% CI
  [1.27, 2.03] (its amplitude 10.7 m/s [9.7, 11.8] is on the same
  full-extent basis). Either way the waves propagate too slowly (§4).

## 4. Wave comparison — observed vs simulated backward speeds

The standard §7.2 detector (40 km/h threshold, 15 s × 75 m) **degenerates
on the observed side**: this 640 m site is congested wall-to-wall, so the
jam mask forms blob components whose upstream front pins at the site
boundary — fitted front slopes of −0.0 km/h (4 components, none classified
backward). This is a detector-scope finding, not evidence of no waves.

A stripe-level analysis (identical parameters both sides: threshold
25 km/h, 10 s × 50 m bins) isolates the deep stop-and-go stripes inside
the congestion:

| Quantity | Observed (NGSIM p1) | Simulated (with boundary) |
|---|---|---|
| Backward stripe fronts | 6 | 20/20 replicates have them |
| Front speeds [km/h] | 18.0, 15.8, 6.0, 18.0, 18.0, 18.0 (mean 15.6) | mean of replicate means 10.7 |
| Standard-threshold front speed [km/h] | degenerate (0.0, front pinned at boundary) | 5.8 (boundary-queue front) |

Cross-checks: the observed stripe speeds (≈16–18 km/h) sit inside the
empirical 14–22 km/h band and agree with the M2 fundamental-diagram
congested branch fitted from the same data (w = −14.6 km/h, 95% CI
[−19.0, −10.6]). The simulated stripes propagate at ~11 km/h — too slow.
The M2 IDM calibration note is the likely cause: raw-NGSIM differentiation
noise biases a_max high and the congested-branch behavior of the fitted
population discharges differently from the real crowd; the wave-speed
criterion (14–22 km/h) accordingly **fails** and we say so.

## 5. Flux-cap comparison with a binding constraint

The M3 first attempt (follower_stopper at 5%/100% through the macro tier)
produced an **identical-variants null**: FollowerStopper never commands
below the local equilibrium speed, so neither the `flux_cap`
(`F ≤ ρ·v*`, discrete Delle Monache–Goatin 2014) nor the `capacity`
(`F ≤ α(v*)·q_max`) constraint ever bound. That null is preserved in the
`runs/m3_fluxcap/results.json` notes; the redesigned arm makes the
constraint bind:

* **Scenario:** `corridor_10km` (EIDM, emergent waves) with **JAD** at 5%
  penetration / 100% compliance — JAD's slow-in genuinely commands
  β·v below prevailing speed. 20 seeded replicates, 25–29 complied AVs each.
* **Playback, not re-simulation:** each complied AV's recorded micro
  trajectory (t, x, v) is played back through the new v*-trajectory entry
  point (`macrosim.bottleneck.VStarTrajectory` → `run_macro(...,
  prescribed_avs=...)`). Both variants see the SAME v* signal; only the
  constraint form differs.
* **FD:** fitted from 3 no-AV baseline replicates of the same corridor
  (v_f 87.2 km/h, w −12.5 km/h, ρ_jam 195 veh/km) — the JAD runs
  themselves are too dampened to populate a congested branch (that is
  JAD working as designed, and it broke the first fit attempt honestly).
* **Comparison:** micro Edie fields vs macro fields on a common 30 s ×
  500 m grid (post-warmup), plus an upstream-shadow profile (mean speed at
  AV-relative offsets −2000…+1000 m).

**Findings (mean over 20 replicates, 95% CIs):**

| Metric vs micro ground truth | flux_cap | capacity |
|---|---|---|
| Speed RMSE [m/s] | **4.37** [3.66, 5.07] | 5.21 [4.03, 6.39] |
| Density RMSE [veh/km] | **7.84** [6.59, 9.09] | 8.23 [7.07, 9.40] |
| Upstream-shadow RMSE [m/s] | **3.88** [3.13, 4.62] | 4.79 [3.61, 5.98] |
| Binding fraction (v* < V_e at AV cell) | 0.990 [0.985, 0.995] | 0.991 [0.986, 0.996] |

The constraint binds (fields differ between variants in **20/20**
replicates), and the paired per-replicate difference is statistically
resolved: capacity − flux_cap speed RMSE = **+0.84 m/s, 95% CI
[0.36, 1.33]**. Stated precisely, the ρ·v* flux cap (the v1 rule, i.e. the
discrete Delle Monache–Goatin constraint) tracks micro ground truth better
than the reduced-capacity variant

* on **all three** RMSE metrics in the 20-replicate mean (table above);
* in **every one of the 20 replicates** on speed RMSE and on
  upstream-shadow RMSE;
* but **not** uniformly on density RMSE, where the capacity variant is the
  closer of the two in **4 of 20** replicates (flux_cap vs capacity, in
  veh/km — seed 3011106312394044631: 14.602 vs 13.344; 165503670820534583:
  14.635 vs 14.131; 6904272788004776631: 6.677 vs 6.654;
  6143473282319009404: 5.720 vs 5.717, the last two near-ties).

The headline ranking therefore rests on the speed and shadow metrics,
which are unanimous, not on density, which is not. Figure:
`docs/figures/fluxcap_comparison.png` (waviest replicate; the capacity
variant visibly under-propagates the breakdown).

Caveats, plainly: the binding fraction is ~0.99 partly because the fitted
free branch (v_f 87 km/h) over-predicts micro speeds at ambient density,
so the caps are weakly active almost always, strongly during slow-in and
breakdown. Absolute macro-vs-micro RMSE (4–5 m/s) is dominated by LWR
model form (it cannot grow emergent waves — ADR-1); this comparison ranks
the two constraint discretizations, it does not validate the macro tier,
whose outputs remain `tier="screening"` and can never back a validation
report (CLAUDE.md §5.6).

## 6. Limitations — read before citing any number above

1. **640 m site.** The study section is a third of a typical wave's
   wavelength. Wave fronts are observable for at most ~2 min before
   leaving the camera range; the standard wave detector degenerates
   (§4); GEH is computed on 9 bins, so one bin is 11 percentage points.
   Nothing here transfers to corridor-scale claims.
2. **Boundary condition necessity — and what it means for claims.** The
   replica only develops congestion when the observed downstream state is
   imposed. That is legitimate FHWA-style calibration practice, but it
   changes the epistemic status of the with-boundary run: it demonstrates
   that the model *propagates* an externally supplied congestion state
   consistently (queue spillback, wave formation inside the span), NOT
   that it *predicts* congestion onset. The no-boundary arm is the honest
   predictive baseline, and it free-flows. Any claim built on the
   with-boundary numbers must carry this caveat.
3. **Missing on-ramp/auxiliary lane.** ~6% of real vehicles enter mid-site
   and the merge at ~150–165 m is an in-span bottleneck the replica lacks;
   the observed upstream-slow speed gradient is reversed in the sim (§3).
   Closing this requires modeling the ramp (OSM/net extension), which is
   out of M3 scope and stated as such.
4. **Raw NGSIM noise.** Speeds are differentiation artifacts of noisy
   positions (staircase quantization; documented in M2). The IDM
   population fitted on it (a_max biased high) plausibly explains both the
   too-slow simulated stripe waves (10.7 vs 15.6 km/h) and the
   under-discharge of the simulated queue (window-3 GEH failures).
   Re-fitting on reconstructed (Montanino–Punzo) data is the upgrade path.
5. **v_f identifiability.** The site never reaches free flow, so the M2
   FD's v_f (57 km/h) is the fastest *observed* operation, not a free-flow
   speed, and the IDM v0 marginal mostly reflects its search bounds. The
   boundary schedule inherits the same limitation: it is the observed
   mean speed of a congested tail, applied to 5 lanes equally.
6. **Warmup vs history.** The recording starts congested; the sim starts
   empty and gets 180 s of warmup at the first boundary value. Window-1
   comparisons (especially flows at 320/550 m) carry this initialization
   error on top of everything else.
7. **Stripe-analysis parameters.** The 25 km/h / 10 s × 50 m stripe
   detector was chosen to resolve internal fronts on a short congested
   site; results are parameter-sensitive at this scale (the 6.0 km/h
   outlier front is a partial component). Parameters are recorded in the
   results JSON and applied identically to both sides.
8. **Boundary speed ≠ boundary flow.** A speed schedule on the exit buffer
   constrains outflow only through the car-following model's discharge
   behavior at that speed; it cannot reproduce observed discharge flow
   exactly (§3). A flow-based boundary (metered outflow) is a possible
   future variant of `BoundarySpec`.
9. **Report metrics quirk.** In the no-boundary arm the travel-time span
   end (x = 1280 m) coincides with the network end, so per-vehicle span
   travel times are undefined there (NaN, n=0 in the CI table); the
   with-boundary arm measures them normally (exit buffer extends the
   network). Both are visible in the respective `metrics_ci` blocks.

## 7. Auto-report (product feature, CLAUDE.md §7.4)

`validation.report.generate_report` ran end-to-end on the 20-replicate
with-boundary run set (scoped to config `e897b6479ed4`):
**`docs/reports/us101_replica/report.md`** with 20 per-replicate speed
contours alongside. The report shows the honest criteria mix (1 PASS /
5 FAIL, including the not-evaluated ring rows), `seeded=False` provenance
on every run row, package versions, and the `artifacts/idm_us101.json`
calibration provenance (data hash `8578f4…95e22`). One generator fix came
out of the real invocation: micro runs record their fleet calibration
under the `fleet_calibration` meta key, which the report's calibration
table now picks up (`validation/report.py`; validation tests stay green).

One deliberate difference between the two artifacts: `compute_metrics`
runs over each run's **full network extent** (entry buffer + span + exit
buffer), while the results JSON's criteria rows use the **site-clipped**
field (x < 640 m) that matches the observed side. The gap shows up in
every wave quantity, not just wave speed, so all three are tabulated:

| Wave quantity | `compute_metrics`, full extent | Site-clipped (x < 640 m) |
|---|---|---|
| Backward front speed [km/h] | 7.4 [6.8, 8.0] | 5.8 |
| Wave count [per run] | 1.65 [1.27, 2.03] | 1.45 |
| Amplitude [m/s] | 10.75 [9.73, 11.76] | 9.66 |

Full-extent values are `simulated.metrics_ci` (mean, 95% CI over 20
replicates). Site-clipped speed and count are stored directly as
`simulated.mean_backward_speed_kmh` and `simulated.wave_count_mean`; the
site-clipped amplitude is the mean over the 20 `simulated.
waves_per_replicate` entries of each replicate's mean detected amplitude.

Both wave-speed values fail the 14–22 km/h band, and both pipelines detect
backward waves in 20/20 replicates, so no pass/fail verdict turns on the
choice. Each value is internally consistent with its own pipeline, both
pipelines are recorded in the artifacts, and any sentence quoting one of
these numbers alongside a site-clipped count must say which pipeline it
came from (§3 does).
