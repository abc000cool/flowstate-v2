# I-24 discharge diagnosis: pre-registration log

Each entry is written before the batch it governs. Times are CDT, 2026-10-07. Code: a snapshot of
HEAD af138fd (`git archive af138fd packages/flowstate_core packages/microsim packages/validation`),
imported ahead of the editable install by `fx.py`. Fixtures only; macOS; at most two SUMO processes.

## 05:03 — before any run

**Already read (committed artifacts, no run).** `artifacts/i24_validation_{flow_speedcal_ref,dc,dc_refit}.json`
(20 seeds each, same seeds), 2-h mean flows by section, paired over seeds:

| section (data x) | ref (old drivers) | dc (k = 1, scale 0.800) | dc_refit (k = 1, scale 0.925) | refit − dc, paired 95 % |
|---|---|---|---|---|
| 200 | 4,943 | 5,039 | 5,233 | +194 ± 17 |
| 2,200 | 5,850 | 6,031 | 6,047 | +16 ± 17 |
| 3,200 | 5,821 | 6,025 | 5,983 | −42 ± 15 |
| 4,800 | 5,834 | 6,061 | 6,047 | −14 ± 13 |
| 5,400 | 5,531 | 5,737 | 5,735 | −2 ± 13 |

and 5-min windows: the 5,400-m flow of `dc` and `dc_refit` track the boundary speed schedule window by
window and are nearly identical. Segment speeds (2-h means, data x 0–5.5 km): `dc` 62 / 47 / 41 / 44 /
42 / 38 / 35 / 27 / 26 / 29 km/h (upstream free, congestion downstream); `dc_refit` 22–32 km/h
everywhere. This suggests the peak sections are bounded *downstream* of the merge (the measured
boundary at data x ≥ 5,492, applied as an exit-buffer speed limit, and/or the Hickory Hollow–Bell Road
weave), with the net exits between 2,200 and 5,400 m adding to whatever passes there.

**Hypotheses.**
- H_M: the Old Hickory merge's own discharge with the calibrated drivers is below ≈ 6,600 veh/h and is
  what bounds the peak sections.
- H_B: the peak sections are bounded by the downstream end — the boundary representation (an
  observed mean speed imposed as an IDM desired-speed limit) and/or the downstream weave — plus the
  net exits between the sections and the boundary; the merge is not binding at k ≥ 0.5.

**Batch R (reproduction).** The original on-ramp fixture "OR" of docs/DISCHARGE_CALIBRATION.md §1
(session harness `phase3/p31/fixture_or.py`, copied unchanged into `orig/` except the import path and two
fleet entries), BLOCK mode, seeds 1–20, I-24 fleet (IDM, lc 5 / ramp 1 / keep-right 0 / 1 / 1 / 1 =
the fleet block of `scenarios/i24_replica_flow_speedcal_dc.yaml`) with populations k = 0
(`idm_i24_capacity.json`), k = 0.5 and k = 1 (`idm_i24_capacity_amax_k1.0.json`, the calibrated fleet).
Prediction: reproduces the recorded 1,460 ± 20 (k 0) and about 1,632 ± 19 veh/h/lane (k 1; recorded with a
4-decimal mean override, so not bit-identical), i.e. the 2-lane fixture keeps rising with a_max and
projects about 6,530 at 4 lanes: **the fixture does not reproduce the corridor's plateau** (6,021 / 5,995
at k = 1 on the grid seed).

**Batch G (I-24 merge geometry, `oh4`).** 4 through lanes, a 975-m acceleration lane (the replica's
attach edge 977008894), 4 lanes downstream for 2,000 m with free outflow, ramp share 0.16 of demand
(the corridor's Old Hickory share of planned vehicles, 2,109 / 14,688 = 0.144, and of the peak-section
flow, about 0.16), BLOCK mode as the original (fill 0.9 C for 120 s, then 1.35 C with C = 7,200 veh/h;
red 300–390 s at the merge node), 20 min, seeds 1–20, k = 0 / 0.5 / 1. Sections at 367 m and 1,347 m
past the acceleration lane's end (data x 2,200 / 3,200 in the replica's mapping). Discharge = mean flow
over the minutes from red end + 180 s with a vehicle below 8 m/s upstream of the first section (the
original definition).
Prediction under H_B: at k = 1 the merge alone discharges at least 6,400 veh/h at both sections
(the fixture's free outflow removes the downstream bound). Under H_M: below 6,200.
Decomposition read on k = 0 and k = 1, seeds 1–20 (lane-change log on): per-lane flows, entrants'
change positions and speeds, mandatory changes at the lane end versus earlier, discretionary changes,
gaps at the change, the new follower's minimum acceleration within 5 s, headways per lane at the
sections, speeds by lane and 50-m bin.

## 05:12 — after batches R and G, before batches B, S and W

**R and G read (runs/R_or.jsonl, runs/G.jsonl; G_summary.json).** R reproduces the recorded OR
fixture bit for bit at k = 0 (1,460.4 ± 19.9, 20 of 20 seeds identical) and gives 1,535 ± 31 (k 0.5)
and 1,646 ± 19 veh/h/lane (k 1). G (I-24 merge geometry, free outflow): s2200 6,620 ± 62 (k 0),
6,795 ± 59 (k 0.5), 6,979 ± 49 (k 1); k 1 − k 0 paired +359 ± 74. The prediction under H_B held
(≥ 6,400 at k = 1). The 2 collisions (k 0.5, seed 5) are at the merge node's instant red at
t = 300.5 s (BLOCK artefact, no yellow), not in the merge.
Also read (committed artifacts, no run): the observed tracked section flows (4,130 / 4,128 / 3,842 /
3,737 veh/h at 2,200 / 3,200 / 4,800 / 5,400 m) do not conserve with the tracked Hickory Hollow ramp
counts (off 370, on 431): 3,200 → 4,800 should change by about +61, it changes by −286.

**Batch B (boundary throughput).** Fixture `straight`: 4 lanes × 3,000 m, then the 200-m exit buffer
whose limit is set by `edge.setMaxSpeed` (as the runner does). Saturating inflow (7,600 veh/h,
round-robin, insertion-limited), 900 s; flow at x = 2,900 m (100 m before the buffer) over 300–900 s.
Constant buffer limits 20 / 30 / 40 / 50 / 60 / 70 km/h and none; k = 1 fleet; 10 seeds (1–10); k = 0
at 30 / 50 / 70 km/h, 10 seeds. Prediction: the throughput at a limit v is well below the congested
branch v / (l + s0 + vT) of the same population (the IDM free-road term with v0 = the limit inflates
the gaps), and nearly independent of k (equilibrium does not depend on a_max).

**Batch S (the replica's schedule on the straight fixture).** Same fixture, the boundary schedule of
`scenarios/i24_replica_flow_speedcal_dc.yaml` (sim times 0–7,800 s), saturating inflow, k = 1,
seeds 1–5; flow at 2,900 m per 5-min study window (sim t = 600 + 300 i). Prediction under H_B: the
2-h mean is within about 5 % of the replica's 5,400-m flow (5,737 veh/h, dc; 5,735 dc_refit), and its
5-min windows correlate with the replica's (r > 0.8). If it is far above 5,737 (> 6,100), the replica's
5,400-m flow is bounded by something upstream of the boundary (the Hickory Hollow–Bell Road weave)
rather than by the boundary.

**Batch W (the Hickory Hollow on-ramp / Bell Road off-ramp weave).** Fixture `weave`: 4 lanes ×
2,500 m, a 565-m 5-lane section whose lane 0 starts at the on-ramp and ends at the off-ramp, 4 lanes
× 2,000 m with free outflow. Saturating mainline (7,200 veh/h demand), on-ramp 687 veh/h (tracked count
÷ coverage), 5.5 % of the mainline exiting at Bell Road, no ramp-to-ramp; 1,200 s; k = 1; seeds 1–10.
Discharge 300 m downstream over 300–1,200 s. Prediction under H_B: the weave passes more than
6,300 veh/h, i.e. it is not what holds the replica's 5,400-m flow at 5,737.

## 05:25 — after batches B, S and W, before batch DS and any lever run

**B, S, W read (runs/B.jsonl, runs/S.jsonl, runs/W.jsonl, W_summary.json).**
- B (k 1, 10 seeds): boundary throughput 4,064 / 5,019 / 5,705 / 6,218 / 6,623 / 6,905 veh/h at
  20 / 30 / 40 / 50 / 60 / 70 km/h, 7,134 without a limit; k 0 within 70 veh/h at 30 / 50 / 70 km/h.
  Prediction held (a_max does not set it).
- S (k 1, 5 seeds): the replica's schedule on the straight 4-lane fixture passes 6,016 ± 14 veh/h over
  the 2-h study window — the observed 6,009 at 5,400 m — window-correlated with the replica (r 0.988),
  but 279 veh/h above the replica's 5,737. The prediction "within 5 % of 5,737" held (4.9 %), but the
  deficit is structured: 425 veh/h in the 10 windows where the boundary passes ≥ 6,300, 175 elsewhere.
  So the boundary as implemented is not what the replica lacks; something between 2,200 m and the
  boundary takes the rest.
- W (k 1, 10 seeds): the Hickory Hollow–Bell Road weave alone passes 6,426 ± 82 veh/h with a
  saturating mainline; its right lane crawls (about 20 km/h upstream, 1,244 veh/h downstream against
  1,855 in the left lane); entrants cross at median 4.7 m/s (I-24 HH–BR measured 11.7) and 1.9 m/s
  slower than their new leader (measured: 0.83 m/s faster). The 5,400-m flow of the replica in
  permissive windows (about 6,200–6,350) is at this weave's level.

**Batch DS (the replica's downstream end as a fixture).** Fixture `ds` (fixture x = data x − 1,000):
4 lanes 2,352 m; the Hickory Hollow off-ramp diverge, 5 lanes 473 m (lane 0 → off-ramp); 4 lanes 590 m;
the Hickory Hollow on-ramp / Bell Road off-ramp section, 5 lanes 565 m; 4 lanes 1,461 m; the 200-m exit
buffer carrying the replica's boundary schedule; edge lengths from `artifacts/i24_replica_inputs_flow.json`.
Demand: saturating mainline 7,400 veh/h at x 0 (round-robin lanes) of which 9.13 % take the Hickory
Hollow exit and 4.81 % (= 5.29 % of the rest) the Bell Road exit (the replica's mean exit fractions);
the Hickory Hollow on-ramp inflow steps of `scenarios/i24_replica_flow_speedcal_dc.yaml`; 7,800 s;
flows per 5-min study window (sim t 600 + 300 i) at data-x-equivalent sections 2,200 / 3,200 / 4,800 /
5,400 m and 100 m before the buffer. Arms k 0 / 0.5 / 1, seeds 1–10.
Prediction under H_B: with the upstream saturated (as in `dc_refit`), the k = 1 arm reproduces the
refit replica's 2-h flows within ±3 %: 5,400-m 5,735 (5,563–5,907), 2,200-m 6,047 (5,866–6,228).
And the k-dependence of the 2,200-m flow is much smaller than the merge fixture's (+359 k 1 − k 0 in G).
If DS passes much more than the replica (> 6,228 at 2,200 m), the downstream end does not reproduce the
ceiling on its own, and the interaction with the Old Hickory merge (or the lane distribution it
delivers) matters.

**Levers (run on DS against its k = 1 arm, same seeds 1–10, paired; and on W for the mechanism).**
Rationale for each, fixed now:
- **L1 `lcAssertive` at the measured-gap mapping, 1.25 and 3.38.** LC2013 divides both secure gaps by
  `lcAssertive`; the I-24 critical gaps map to 1.25 on the lag side and 3.38 on the lead side
  (docs/MERGE_MODEL_BRIEF.md §3 item 11, WP-82). The fixtures show weave entrants refusing gaps until
  they crawl near the section's end. Prediction: a gain at both values, larger at 3.38; risk: harder
  braking of new followers (reported: share braking beyond b, steps below −4.5 / −8.9 m/s², collisions).
- **L2 `actionStepLength` 1.0 s (reaction time) against the current 0.5 s.** SUMO's action step is the
  model's reaction time; the fleet runs at the step length (0.5 s), the shortest possible, while 1 s is
  the common literature value for driver reaction. The measured T (1.511 s; 1.322 capacity-scaled) is a
  headway, not a reaction time, so this is a sensitivity of the plausible range, not a calibration.
  Prediction: a loss (a slower reaction lowers queue discharge); if so, the reaction-time route to more
  discharge is closed (the model already reacts as fast as it can).
- **L3 EIDM (the I-94 fleet's model, SUMO defaults) against IDM.** The same population on the other
  corridor's car-following model. Recorded on the OR fixture: −3 % (1,413 against 1,460). Prediction: no
  gain; a model-consistency check, not a candidate.
- **δ is not tested.** The decomposition points at lane changing in the weave, not at the free-road
  term; with batch S matching the observed boundary throughput, there is no evidence that would motivate
  a model-form change to δ (CLAUDE.md §3.1 fixes δ = 4).
Each lever is also checked for string stability where it changes car-following (L2, L3: emergent waves
in the fixture's speed field — share of 50-m × 60-s bins below 40 km/h — and the criterion of
`validation.string_stability` for IDM where applicable). A lever that removes the stop-and-go content
is not a fix.

## 05:37 — after batch DS (k = 1), before S992, DS992 and FULL

**DS read (scratch runs; DS_summary.json).** k = 1, seeds 1–10, saturated upstream: 6,362 ± 20 /
6,319 ± 19 / 6,300 ± 21 / 5,959 ± 20 veh/h at data-x 2,200 / 3,200 / 4,800 / 5,400, i.e. 224–315
above the refit replica (6,047 / 5,983 / 6,047 / 5,735). **The DS prediction failed** (it asked for
5,563–5,907 at 5,400 and 5,866–6,228 at 2,200): the downstream end, fed a saturated round-robin
4-lane inflow through a 200-m limited buffer, does not reproduce the ceiling on its own; it passes
about the observed 5,400-m flow (6,009).

**A representation difference found in the runner (read, not run).** On an OSM corridor the boundary
schedule is applied to the *whole last corridor edge* (`runner._build_network`: "the LAST corridor
edge plays the exit-buffer role"), here 634155175, 992 m (data x 5,492–6,505), not to a 200-m buffer;
`exit_buffer_m` applies to `CorridorNetwork` only. B, S and DS used a 200-m buffer.

**S992.** Batch S with the limit on a 992-m last edge (the straight road shortened to keep the
measured section 100 m before it), k = 1, seeds 1–5. Prediction: within ±2 % of S (6,016), i.e. the
limited length does not set the throughput. If it is lower by > 3 %, the 992-m limited edge is part of
the replica's ceiling.

**DS992.** DS with the replica's last 1,389 m: 397 m of 4 lanes, then the 992-m limited edge
(instead of 1,461 m + a 200-m buffer); k = 1, seeds 1–10. Same prediction and criterion as DS.

**FULL.** Fixture `full`: the replica's whole corridor as a straight plain-XML network (4 lanes
3,070 m; Old Hickory acceleration lane 975 m; 4 lanes 1,496 m; Hickory Hollow diverge 473 m; 4 lanes
590 m; Hickory Hollow on / Bell Road off 565 m; 4 lanes 397 m; the 992-m limited edge), with the
replica's own demand drawn by `microsim.vehicles.build_corridor_plan` from the scenario config (inflows,
the four ramps' inflows and time-varying exit fractions, entry lane shares, the calibrated fleet) and
written by `write_corridor_routes`, seeded with the replica's first 10 seeds (so the drawn vehicles,
departures and routes are the replica's; only the network is synthetic). Arms: `full_dc` (scale 0.800)
and `full_refit` (scale 0.925). Prediction: if FULL reproduces the replica's ceiling within ±3 %
(refit: 2,200 m 6,047 → 5,866–6,228; 5,400 m 5,735 → 5,563–5,907), the mechanism is in the fixture
and the toggles below decompose it; if FULL passes much more, the OSM geometry itself (junctions,
lane connections, short edges) holds the missing part.
Toggles (registered now, run only if FULL reproduces the ceiling): (T1) Old Hickory entrants inserted
on the mainline instead of the ramp (same vehicles, same times), (T2) entry lanes round-robin instead
of the measured shares, (T3) exit fractions held at their 2-h means.

## 05:50 — after S992 and DS992, before FULL and before any lever run

**S992 read.** The limit on a 992-m last edge (as the replica applies it, docs/CONTRACTS.md §2:
"`BoundarySpec.exit_buffer_m` is ignored" on OSM) passes 5,829 ± 14 veh/h against 6,016 ± 14 on a
200-m buffer (5 seeds; −187, −3.1 %): the prediction "within ±2 %" failed and the registered reading
applies — the 992-m limited edge is part of the replica's ceiling.
**DS992 read.** 6,114 ± 20 / 6,076 ± 20 / 6,038 ± 24 / 5,744 ± 22 veh/h at 2,200 / 3,200 / 4,800 /
5,400 m (refit replica 6,047 / 5,983 / 6,047 / 5,735; window correlation 0.89 / 0.87 / 0.97 / 0.99).
Both DS criteria are met: **the downstream end alone, with no Old Hickory merge in the fixture,
reproduces the replica's ceiling.** DS992 − DS (paired, 10 seeds) at 2,200 m: −248 ± 15.

**Amendment to the 05:25 lever plan.** L1–L3 run on **DS992** (the fixture that reproduces the
ceiling), paired against its k = 1 arm, seeds 1–10, rationale and predictions as written at 05:25.
Wave content is read with the four registered detectors of `validation.waves` on pooled-lane fields of
the measured span (fixture x 0–4,492 = data x 1,000–5,492, study window), per run; the comparison is
lever against base (same detectors, same span).

**L4 (new) — the boundary limit on a 200-m exit buffer instead of the whole 992-m last edge.**
Rationale: the schedule is the observed mean speed of congested traffic on data x 5,492–6,437; the
drivers there are not driving at a desired speed of 13–60 km/h, they are held by the queue. Imposing the
speed as every vehicle's desired speed over 992 m turns a kilometre into a low-desired-speed road whose
IDM gaps carry the free-road inflation, and its saturated throughput (S992 5,829) is below the flow the
real road carried there (6,009 at 5,400 m) — a boundary condition must not pass less than the
recording passed at the same place, since the observed flow is a lower bound on the real downstream
capacity. The 200-m buffer is the contract's own default (`BoundarySpec.exit_buffer_m` = 200, used
for `CorridorNetwork`) and passes 6,016 (≥ 6,009). Measurable criterion, fixed now: a boundary
representation is admissible if its saturated 2-h throughput on the straight fixture is ≥ the observed
5,400-m flow (6,009) and ≤ the observed + 5 % (6,309). 992 m fails (5,829); 200 m passes (6,016).
**L4 test (run after this entry):** FULL with the 200-m buffer appended after the 992-m last edge at
its posted limit (`full_refit_b200`, `full_dc_b200`) against FULL as the replica applies it, seeds the
replica's first 10, paired. Prediction: refit 2,200-m flow +150 to +300 veh/h, still below the
observed 6,626; dc unchanged within ±50 (demand-limited); 0 collisions; wave readings not removed
(backward fronts per run on the standard and stripe detectors not lower than base by more than a third).
L4 changes an input representation, not a driver; it is never a calibration of drivers.

## 05:57 — during FULL (2 of 20 runs read), before the plateau check and the lever runs

**Plateau check (DS992 at k = 0 and k = 0.5).** If the replica's ceiling at k ≥ 0.5 is the downstream
end, the downstream end's own throughput must be nearly independent of the mean a_max. DS992 at k = 0
(`idm_i24_capacity.json`) and k = 0.5, seeds 1–10, paired against DS992 k = 1. Prediction: the 2,200-m
and 5,400-m 2-h flows within ±80 veh/h of k = 1 at k = 0.5 and within ±150 at k = 0 (batch B moved
≤ 70 veh/h between k 0 and k 1 at fixed limits), against the merge fixture's +175 (k 0.5) / +359 (k 1)
over k 0. If DS992 instead rises with k as steeply as the merge fixture, the plateau is not explained by
the downstream end.

**Run order (laptop, ≤ 2 SUMO processes):** FULL refit arms (running) → L1–L3 and the DS992 base with
wave readings → the plateau check → FULL dc arms (`full_dc`, `full_dc_b200`) at 5 seeds (the replica's
first 5) instead of 10, because their registered prediction is "unchanged within ±50" (demand-limited)
and they are the lowest-information arms; reported as 5-seed results.

## 06:01 — erratum (no new run registered)

The 05:25 entry's reading of batch B says "k 0 within 70 veh/h at 30 / 50 / 70 km/h". The values are
+20 (30 km/h), +68 (50 km/h) and **+104** (70 km/h) for k 1 − k 0 (`runs/B.jsonl`). The 05:57 entry's
plateau prediction quoted "≤ 70"; it stands as written (it was fixed before the plateau runs), and the
document reports the corrected figures.

## 06:45 — after FULL refit (10 seeds) and the L4 test there, before L5

**FULL read (FULL_refit_summary.json).** `full_refit` reproduces the replica `dc_refit` within
12 veh/h at every section (2,200 m 6,047 ± 28 against 6,047; 5,400 m 5,743 ± 15 against 5,735;
realised 0.923 against 0.921): the FULL prediction held. **L4** (`full_refit_b200` − `full_refit`,
paired, 10 seeds): 2,200 m +234 ± 24, 3,200 m +244 ± 23, 5,400 m +208 ± 18, realised +0.023, 0
collisions, backward fronts per run +4 ± 2 (standard) and +17 ± 8 (stripe). Every L4 prediction held.
**Post-hoc check (not registered before the L4 run; labelled so in the document):** the mean speed on
data x 5,492–6,437, where the schedule was measured, over the study window, is 32.9 km/h in
`full_refit` and 32.8 in `full_refit_b200`, against the schedule's 49.9 (window RMSPE 0.34 / 0.36).
Neither representation reproduces the measured state at the boundary: a limit equal to the observed
mean speed makes IDM drivers travel well below it, so the zone becomes the bottleneck.

**L5 — an equilibrium-consistent boundary limit.** Rationale: the measured schedule is a mean speed of
traffic whose drivers' desired speeds are not that speed. A limit v0 reproduces the measured state
when the population's IDM equilibrium at the measured speed carries the measured flow:
q_eq(v_obs; v0) = q_obs, with q_eq(v; v0) = v / (l + (s0 + vT)/√(1 − (v/v0)^4)). With the fleet's mean
driver (T 1.3222 s, s0 2.5327 m, l 5 m; `idm_i24_capacity_amax_k1.0.json`), v_obs = the schedule's
study-window mean 13.869 m/s (49.9 km/h) and q_obs = the recording's 5,400-m flow 6,009 / 4 =
1,502 veh/h/lane, v0 = 1.2185 v_obs. One constant from measured quantities, not fitted to any target
of the criteria; applied to every schedule step on the replica's own 992-m last edge
(`full_refit_l5`), FULL refit demand, the replica's first 10 seeds, paired against `full_refit`.
(Sensitivity, not run: with T 1.511 the factor would be 1.342.)
Criteria, fixed now: (a) boundary-zone mean speed within ±5 km/h of 49.9 and window RMSPE ≤ 0.20;
(b) 5,400-m flow within 5,829–6,309 (−3 % / +5 % of 6,009); (c) 0 collisions; (d) backward fronts per
run (standard, stripe) not lower than `full_refit` by more than a third. Prediction: (a) improves but
is not met (heterogeneous drivers sit below the mean driver's equilibrium; the 30-s steps are not
equilibrium), (b) met, peak sections +200 to +400. L5 changes an input representation, not drivers.
Run after the lever chain (it waits for it).

## 08:39 — reads of the last batches (no new run registered)

- **L1–L3 on DS992** (LEV_summary.json, paired against `k1_992w`, which reproduces DS992 bit for bit):
  L1a +74 ± 20 (2,200 m), 3 steps < −8.9 m/s² against 0, stripe fronts −26 %; L1b +92 ± 20 with 836
  steps < −8.9 and 14.8 % of new followers braking beyond b; L2 −2,249 ± 935, 12 collisions (prediction
  "a loss" held); **L3 +704 ± 21, prediction "no gain" failed**, 5,400-m flow 6,380 (> 6,009 + 5 %),
  stripe fronts −58 % (beyond the one-third rule registered for L4 and L5), boundary zone 42.5 km/h.
- **Plateau check: prediction failed.** DS992 k 0 −281 ± 46, k 0.5 −121 ± 20 against k 1 at 2,200 m.
- **FULL dc, 5 seeds:** reproduces the replica `dc`; L4 +3 ± 5 at 2,200 m (prediction held).
- **L5:** +257 ± 27 (2,200 m), 5,400 m 5,995 — criteria (b), (c), (d) met; (a) not met (zone 39.7 km/h,
  RMSPE 0.211), as predicted.
