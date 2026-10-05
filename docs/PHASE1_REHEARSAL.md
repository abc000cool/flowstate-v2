# Stage 1, phase 1: the cloud rehearsal on real data (2026-10-04)

One n2-standard-32 VM (`flowstate-p1`, us-west1-c), pipeline stage 20 of
`scripts/gcp/pipeline_i24.sh` at commit 62e0c9e, 00:36–03:53 UTC on
2026-10-05 (about 3 h 20 min, about $5), self-deleted; bucket
`gs://flowstate-p1-1004` (7-day lifecycle). Two purposes: check the crash
fixes now on by default (WP-98) against the results they protect, and run
every Stage 1 tool on a real corridor — the Minnesota I-94 WB St. Paul
corridor, the one with measured ramps and a known failure — before any Frisco
data exists. Nothing here is a Frisco result, and the Minnesota corridor is
still not reproduced. Outputs: `artifacts/*_p1def*`, `artifacts/*_p1*`,
`artifacts/p1_rehearsal_2026-10-04/`, `docs/reports/mndot_i94_wb_stpaul_weave_xlsfg_p1/`.

## 1. The crash fixes on by default (item 6)

Each published controller sweep was re-run at this tree (all three AV fixes on
by default) and paired, seed for seed, with its committed handback-only arm.

| Sweep | Runs | Collisions | Against the committed handback arm |
|---|---|---|---|
| Synthetic 10 km, FS / PI-sat / JAD at 5 % | 80 | 0 | identical in every cell (no off-ramp; the close-leader case never arose) |
| NGSIM US-101 penetration, 1–20 % | 120 | 0 | identical in every cell |
| I-24 strategies (FS 10 % × none / VSL / ALINEA) | 120 | 0 | cells without AVs identical; FS cells moved, none by a resolved amount |

On I-24 the off-ramp release acts (AVs left the corridor holding their last
command before): FollowerStopper alone 2,804 → 2,866 veh/h, travel time
1,304 → 1,284 s, fuel 282.7 → 278.5 ml/veh-km; with VSL 2,462 → 2,596 veh/h;
with ALINEA 4,096 → 4,394 veh/h, 712 → 667 s, fuel 140.4 → 124.4 (every 95 %
interval overlaps the committed one). The published conclusions stand.

The Minnesota reference battery (VM AG's configuration; its scripted-merge
guard is now the default) reproduces VM AG's artifact **to the digit**: every
per-seed value, GEH, RMSPE, departures (0.881), weave exits and wave speed;
config hash 4ab855f5054a → b550b46fe751 (policy v3); zero collisions, now
recorded.

## 2. The tools on the Minnesota corridor's real data

**Data quality (item 2).** 27 detector stations × 9 days (05:30–09:30):
243 detector-days, 187 ok, 13 with caveats, 43 dropped; 95.8 % of readings
delivered, 81.8 % usable after the checks. Without being told, it dropped the
corridor's known faults: two ramp detectors that read zero every day
(rnd_88823, rnd_88835), one with no data (rnd_87209), the collector–distributor
pair reading more than the mainline (rnd_87205, impossible flows on 7 of 9
days). The blind per-lane pass (576 lane-days, 85.4 % usable) flagged the
known chattering loop 3240 only as suspect, on 3 of 9 days: the reviewer's
exclusion of that loop is still needed.

**Ramp estimation (item 3), leave-one-out against measured ramps.** Where the
mainline stations balance, estimates are usable (relative error 13 % at
rnd_88807, 30–31 % at rnd_88817/88819, interval coverage 87 %); where they do
not (the T.H.52 segment S790→S97, the C-D pair, S1064→S1065) they are not
(89–475 %). Pooled: 91 % relative error, 45 % coverage (raw, unmasked: 104 %,
39 %). Conclusion for Frisco: estimated ramp volumes are a fallback, not a
substitute; the data request's ramp counts matter.

**Layout audit (item 4).** 36 segments, 45 events (9 on-ramps, 8 off-ramps,
5 weaves, 11 lane drops, 11 lane gains, 1 C-D road); no defect, 8 warnings
(acceleration lanes that ramp guessing spread over a whole edge, two very
short segments), 18 notes — the checklist a person now works through on the
imagery.

**Driver settings (item 8).** Free-flow speed: observed median 105.2 km/h
(65 mph; interval 105.0–105.4) against the model's 87.9 km/h — the drivers
here run about 20 % faster than the population, which the posted 55 mph
limit caps; only SUMO's speed factor on the limit closes it (1.245, inside
its measured range), which the engine did not expose (added the same day,
WP-109). Capacity per lane downstream of the active bottleneck: observed
1,482 veh/h/lane (1,418–1,540) against the model's simulated 1,663 (−10.9 %):
no mean time headway inside the measured range brings the population down to
it. Truck share: not available (loop counts carry no class).

**Station selection, day split (item 7).** All 14 mainline stations selected;
calibration days 09-02, 09-03, 09-08, 09-15, 09-16; validation days 09-01,
09-09, 09-10, 09-17 (seed 20261004). Targets masked: 24 / 19 detector-days
(912 / 720 readings) set aside on the calibration / validation days.

**Baseline gate (item 9): FAILED, as it should.** Link flows GEH < 5 on 15.5 %
(calibration) / 14.5 % (validation) of station-hours against 85 %; speed RMSPE
at 15 minutes (point speeds at the stations) 49.7 % / 48.7 % against 15 %;
wave speed 6.6 km/h against 14–22 (observed 19.1 on the calibration days);
bottlenecks: a phantom at S790→S97 (the T.H.52 weave) in 20 of 20 replicates;
collisions none. Every validation day fails on its own as well. The
regenerated report opens with the client summary and withholds every strategy
result (`docs/reports/mndot_i94_wb_stpaul_weave_xlsfg_p1/report.md`).

**Uncertainty (item 11), rehearsal (4 samples × 2 seeds).** Driver ranges fell
back to the wide measured range (labelled assumed) because the driver check
gave no interval to read (capacity cannot match, free-flow speed needs the
speed factor). Baseline total delay including waiting 17,248 veh·h (sample
spread 11,715–21,366), throughput 3,039 veh/h (2,758–3,255). **Two of the
eight baseline runs recorded a collision** (samples with mean headway ×0.90
and ×1.43), on lanes 999007700_0 and 51388891_1 about 20 m in: zero collisions
at the calibrated settings does not yet survive varied driver behaviour — a
merge-model question for phase 2. The ALINEA arm failed (below).

**Strategy tuning (item 10), rehearsal on the 35-minute slice.** VSL's best of
two candidates (v_on 65 km/h, ρ_on 50 veh/km) against doing nothing over 4
evaluation seeds: smoother (σ_v spatial −2.6 m/s, wave amplitude −6.6 m/s) but
worse for travellers once waiting counts: total delay including waiting +137
veh·h [57, 218], throughput −152 veh/h [−285, −18], mean travel time +82 s.
With waiting counted, this VSL loses on this model. Rehearsal, not a finding.

## 3. Defect found: ALINEA on a ramp-guessed corridor

Every metered run on the I-94 corridor failed with "'43917735#1-AddedOnRampEdge'
is not in list": the runner re-attaches a scripted ramp to netconvert's
guessed acceleration-lane piece, and the meter's setup looked that piece up in
the scenario's load-time corridor ids. Fixed the same day (the setup uses the
compiled chain, as every other corridor lookup does), reproduced on a
120-second slice before and after, and pinned by
`tests/test_microsim/test_microsim_scripted_force_guard.py::test_meter_on_a_scripted_ramp_of_a_guessed_net`.
The ALINEA halves of the uncertainty and tuning rehearsals are re-run in a
follow-up stage.
