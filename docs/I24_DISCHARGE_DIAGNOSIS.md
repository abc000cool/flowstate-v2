# What limits I-24's peak-section discharge with the calibrated drivers (2026-10-07)

With the calibrated drivers (mean `a_max` + 1 sd,
`artifacts/idm_i24_capacity_amax_k1.0.json`), the I-24 replica's peak sections
(data x 2,200 / 3,200 m) carry about 6,030–6,050 veh/h against the recording's
6,626 / 6,639, and raising `a_max` beyond +0.5 sd no longer helps
(docs/DISCHARGE_CALIBRATION.md §3–§4). This document asks what limits them.
Fixtures only, on the laptop. No package code, test, golden or scenario was
changed, and nothing was committed.

Labels:
- **[run]**: a run made for this document. Harness, pre-registration and
  summaries are in `artifacts/i24_discharge_2026-10-07/`.
- **[artifact]**: read from the named committed JSON.
- **[record]**: quoted from the named document.
- **[estimate]**: an estimate, with its basis stated.

## 0. In plain English

- **The merge is not what limits it.** Built in I-24's own layout (4 lanes and
  a 975-m acceleration lane), the Old Hickory merge with the calibrated drivers
  passes about 6,980 veh/h — more than the recording's 6,626. Even the old
  drivers pass 6,620. The earlier fixture that suggested otherwise had 2 lanes;
  its per-lane number does not scale to 4.
- **The limit is at the corridor's far end.** A fixture of only the last 4.5 km
  (no merge at all) reproduces the replica's ceiling of about 6,050 veh/h, and a
  straight copy of the whole corridor fed with the replica's own vehicles
  reproduces the replica to within 12 veh/h at every count section.
- **About 40 % of the gap is how the downstream boundary is imposed.** The
  replica takes the speed measured downstream (about 50 km/h on average) and
  makes it every driver's *desired* speed over the last kilometre. Model
  drivers told to want 50 km/h drive at about 33 and carry less traffic than
  the real road carried there (5,743 against 6,009 veh/h). That costs about
  230–260 veh/h at the peak sections.
- **About half is in the target.** The recording's own counts lose 617 veh/h
  between the peak sections and the far end, while its ramp counts account for
  only about 235. Camera coverage varies along the road and the counts are
  corrected with one average, so the peak sections' 6,626 is probably too high
  by a few hundred veh/h (an estimate). That needs a data check, not a model
  change.
- **Why more `a_max` stopped helping.** On the calibration grid the corridor
  was simply carrying all the demand it was given from +0.25 sd on. With more
  demand it hits the far-end ceiling. More `a_max` would lift that ceiling a
  little, at the cost of the stop-and-go waves the model must keep.
- **Levers tested** (each written down before it ran):
  - lane-change assertiveness at the measured range: +74 to +92 veh/h; at the
    top value, hundreds of emergency stops. Not a fix.
  - a 1-s reaction time: the corridor collapses. Closed.
  - the I-94 car-following model (EIDM): +704 veh/h, too much, and most of the
    waves disappear. It confirms the boundary mechanism; it is not a fix.
  - the boundary on a short 200-m stretch: +234 veh/h, safe, more waves; the
    boundary zone is still too slow.
  - **the boundary limit raised by a factor computed from the drivers' own
    equilibrium (1.22): +257 veh/h, the far-end flow matches the recording
    (5,995 against 6,009), no emergency stops, more waves.** The boundary zone is
    still 10 km/h too slow, so it is better, not right.
- **Recommendation.**
  - Propose the computed boundary factor as amendment B1, with its corridor
    acceptance criteria fixed here (§8.3); whether to run it in the cloud (about
    $3) is the owner's call.
  - Check the peak sections' counts against the ramps with a data-only cloud
    stage before they are used again as a discharge target (§8.4).
  - Stop pushing `a_max` and merge rules for discharge (§8.2).

## 1. Setup and provenance

**Code.** The runs import a snapshot of HEAD `af138fd`
(`git archive af138fd packages/flowstate_core packages/microsim packages/validation`),
placed ahead of the editable install by the harness, so concurrent edits to the
working tree could not reach them (by the end of the session other work had
modified `flowstate_core/config.py`, `microsim/runner.py` and a test in the
working tree; none of it was used here, and none of it is from this work). The snapshot lives in the session scratch
directory; `fx.py` names its path.

**Harness** (`artifacts/i24_discharge_2026-10-07/`):
- `fx.py` builds straight plain-XML fixtures with netconvert, draws the fleet with
  the corridor's own functions (`microsim.vehicles._draw_from_calibration`,
  `_vtype_xml`, or for the full-length fixture `build_corridor_plan` and
  `write_corridor_routes` on the scenario config), steps libsumo at 0.5 s, and
  records section crossings per lane, every lane change with the new follower's
  response over the next 5 s, SUMO's own lane-change log (reasons and gaps),
  hard-braking steps, a 50-m speed field per lane, and the four registered wave
  detectors of `validation.waves` on pooled-lane fields of the measured span.
- `orig/` holds the original diagnosis harness of docs/DISCHARGE_CALIBRATION.md §1
  (`fixture.py`, `fixture_or.py`), unchanged except the import path and two fleet
  entries.
- `batch_*.py` run the batches (two processes); `analyze_*.py` read them;
  `*_summary.json` are the tables quoted here. Raw run files (crossings, events;
  ≈ 0.8 GB) stayed in the session scratch directory; only `runs/R_or.jsonl` is
  kept here.

**Fleet.** The fleet block of `scenarios/i24_replica_flow_speedcal_dc.yaml`: IDM,
`lc_strategic` 5, `lc_strategic_ramp` 1, `lc_keep_right` 0, the other LC2013
values 1, speed factor 1, action step 0.5 s; population k = 1 unless stated
(k = 0: `idm_i24_capacity.json`; k = 0.5: `idm_i24_capacity_amax_k0.5.json`).

**Pre-registration.** `artifacts/i24_discharge_2026-10-07/prereg.md`, timestamped,
one entry before each batch: 05:03 (R, G), 05:12 (B, S, W), 05:25 (DS and the
levers L1–L3), 05:37 (S992, DS992, FULL), 05:50 (lever plan moved to DS992; L4),
05:57 (plateau check, run order), 06:01 (an erratum), 06:45 (L5). Predictions
that failed are reported as failed.

**Statistics.** Means with 95 % t-intervals over seeds; paired intervals for
arm differences on the same seeds. Fixture figures are macOS records
(docs/LESSONS.md); the corridor rounds on Linux decide.

**Rules kept.** Fixtures only, at most two SUMO processes, `memory_pressure`
checked before each batch (55–61 % free throughout); no corridor scenario run;
no read of `data/i24motion/processed/*` or any `runs/**/trajectories.parquet`.

## 2. What the replica's own batteries already say [artifact]

The three committed 20-seed I-24 batteries ran the same seeds: `flow_speedcal_ref`
(old drivers, demand scale 0.800), `dc` (k = 1, 0.800) and `dc_refit` (k = 1,
0.925). Two-hour flows by section (veh/h):

| data x | observed | ref | dc | dc_refit | refit − dc, paired 95 % |
|---|---|---|---|---|---|
| 200 | 5,418 | 4,943 | 5,039 | 5,233 | **+194 ± 17** |
| 2,200 | 6,626 | 5,850 | 6,031 | 6,047 | +16 ± 17 |
| 3,200 | 6,639 | 5,821 | 6,025 | 5,983 | −42 ± 15 |
| 4,800 | 6,170 | 5,834 | 6,061 | 6,047 | −14 ± 13 |
| 5,400 | 6,009 | 5,531 | 5,737 | 5,735 | **−2 ± 13** |

- **The extra demand enters and stops.** 15.6 % more demand raises the 200-m
  flow by 194 veh/h and the 5,400-m flow by nothing (−2 ± 13). The ceiling is
  not demand; it is downstream of 200 m.
- **The 5,400-m flow follows the boundary.** Per 5-min window the 5,400-m flows of
  `dc` and `dc_refit` are nearly identical and rise and fall with the measured
  boundary speed (4,450 veh/h in windows near 30 km/h, 6,200–6,350 near 60–67 km/h).
- **Where the replica is slow.** Segment speeds (2-h means, 549-m segments from
  data x 0): `dc` 62 / 47 / 41 / 44 / 42 / 38 / 35 / 27 / 26 / 29 km/h (free
  upstream, congested from data x 3.8 km on); `dc_refit` 22–32 km/h everywhere
  (the queue reaches the entry). With the boundary fast (≥ 55 km/h, 8 windows)
  the slowest segments are 8–9 (data x 3.84–4.94 km, the Hickory Hollow–Bell Road
  weave starts at 4.44 km): 27.7 / 27.1 km/h against 35.6 km/h in segment 10
  downstream of it. The recording has the same pattern, milder: 30.7 / 33.5
  against 41.3 km/h.
- **The boundary is applied to a kilometre.** On an OSM corridor the runner applies
  the schedule to the whole last corridor edge (docs/CONTRACTS.md §2:
  "`BoundarySpec.exit_buffer_m` is ignored"), here 634155175, 992 m (data x
  5,492–6,505), as every vehicle's speed limit, i.e. as its IDM desired speed.
- **The observed section flows do not conserve.** Tracked crossings (before the
  coverage factor, which is one pooled value per window for all sections) are
  4,130 / 4,128 / 3,842 / 3,737 veh/h at 2,200 / 3,200 / 4,800 / 5,400 m. Between
  3,200 and 4,800 m the Hickory Hollow off-ramp takes 370 and its on-ramp adds 431
  (tracked ramp-lane counts, `artifacts/i24_replica_inputs_flow.json`), so the
  section flow should change by about +61; it changes by −286 (347 tracked veh/h,
  about 8 % of the section flow). Coverage differs by section by several per
  cent [estimate], and the peak sections' targets carry it.

## 3. The original fixture does not reproduce the plateau (batch R) [run]

The on-ramp fixture "OR" of docs/DISCHARGE_CALIBRATION.md §1 (2 through lanes, a
250-m acceleration lane, 2 lanes downstream; BLOCK mode; seeds 1–20), I-24 fleet:

| population | discharge veh/h/lane | × 4 lanes | record |
|---|---|---|---|
| k = 0 | 1,460 ± 20 | 5,842 | 1,460 ± 20, **20 of 20 seeds bit-identical** |
| k = 0.5 | 1,535 ± 31 | 6,141 | 1,545 (4-decimal mean override) |
| k = 1 (calibrated) | 1,646 ± 19 | 6,582 | 1,632 (4-decimal mean override) |

Zero collisions. The fixture keeps rising with `a_max` and projects about
6,580 veh/h at k = 1. It does not reproduce the corridor's plateau at about
6,030, so whatever holds the corridor is not in this fixture.

## 4. The Old Hickory merge in the I-24 geometry (batch G) [run]

Fixture `oh4`: 4 through lanes; a 1-lane on-ramp joining as lane 0 of a 5-lane
edge whose acceleration lane ends after 975 m (the replica's attach edge
977008894); 4 lanes downstream with free outflow. Ramp share 0.16 of demand;
BLOCK mode as the original (fill 0.9 C, then 1.35 C with C = 7,200 veh/h; red at
the merge node 300–390 s); 20 min; seeds 1–20; discharge over the minutes from
570 s with a vehicle below 8 m/s upstream of the first section (10.0 min in every
run). Sections 367 m and 1,347 m past the acceleration lane's end are the
replica's data x 2,200 / 3,200.

### 4.1 Discharge

| population | 2,200-m eq. | 3,200-m eq. | paired vs k 0 (2,200) | collisions | steps < −8.9 m/s² (outside the red) |
|---|---|---|---|---|---|
| k = 0 | **6,620 ± 62** | 6,491 ± 62 | — | 0 | 0 |
| k = 0.5 | 6,795 ± 59 | 6,675 ± 72 | +175 ± 60 | 2 (at the instant red, t = 300.5 s) | 0 |
| k = 1 | **6,979 ± 49** | 6,840 ± 40 | +359 ± 74 | 0 | 0 |

**The merge alone is not the limit.** With free outflow, even the old drivers
discharge 6,620 veh/h at the 2,200-m section, the recording's level, and the
calibrated drivers 6,979. The pre-registered prediction under "the merge binds"
(below 6,200 at k = 1) failed; the one under "the downstream end binds" (at least
6,400) held. The two collisions are at the red light's instant switch (no
yellow; vehicles at the stop line), a BLOCK artefact, not in the merge.

Why the 2-lane fixture misled: only the rightmost through lane absorbs the
merge. In 4 lanes the other three run at straight-road capacity, so a
per-lane figure from a 2-lane fixture (one of two lanes affected) scaled by 4
understates the 4-lane discharge by 778 veh/h (12 %) at k = 0 (5,842 against 6,620).

### 4.2 Where the merge loses discharge: per lane

Flow by through lane, right (1) to left (4), veh/h [run]:

| | lane 1 | lane 2 | lane 3 | lane 4 |
|---|---|---|---|---|
| k = 0, 2,200-m eq. | **1,272 ± 32** | 1,672 ± 24 | 1,808 ± 15 | 1,869 ± 14 |
| k = 1, 2,200-m eq. | **1,491 ± 34** | 1,766 ± 21 | 1,850 ± 15 | 1,873 ± 11 |
| k = 1, 500 m upstream of the merge | 869 ± 49 | 1,677 ± 41 | 1,872 ± 14 | 1,870 ± 13 |

Speeds by zone, k = 1, minutes 10–19, km/h (lane index on each edge; on the
acceleration-lane edge index 0 is the acceleration lane):

| zone | acc. lane | right through | 2nd | 3rd | left |
|---|---|---|---|---|---|
| 500 m upstream of the merge node | — | 7.8 | 40.4 | 58.5 | 59.9 |
| acceleration lane, first half | 11.1 | 5.8 | 46.0 | 59.2 | 61.7 |
| acceleration lane, second half | 11.3 | 16.5 | 56.7 | 59.7 | 62.7 |
| 0–400 m past its end | | 58.8 | 59.4 | 60.5 | 63.3 |

The loss is all in one lane. The acceleration lane and the right through lane
form a crawl (6–17 km/h) that discharges into lane 1 at a jam-outflow rate
(1,272 at k = 0, 1,491 at k = 1), while lanes 3–4 run at about 1,850–1,870.
`a_max` acts on that jam outflow: +219 in lane 1, +94 in lane 2, +4–42 in lanes
3–4. This is the merge lock recorded in docs/I24_VALIDATION.md §0.5 (the right
lane crawling upstream of the gore), reproduced on a straight fixture.

### 4.3 The entrants' changes

k = 1, all entering changes in the discharge windows of 20 seeds (3,152), with
the I-24 Old Hickory measurements beside them [run; record:
docs/MERGE_MODEL_BRIEF.md §1.2–1.3]:

| | model, k = 1 | model, k = 0 | I-24 OH measured |
|---|---|---|---|
| speed at the change p10 / p50 / p90 [m/s] | 0.1 / **4.0** / 14.0 | 0.0 / 3.5 / 12.7 | p50 **12.7** |
| share below 5 m/s | 57 % | 61 % | (v < 10 m/s: 39 % of 3,213) |
| position in the 975-m lane p10 / p50 / p90 [m] | 445 / 667 / 884 | 424 / 711 / 897 | about 30 % in the first 250 m (proxy) |
| in the lane's last 50 m | 3.7 % | 5.5 % | — |
| LC2013 reason | strategic urgent 99.8 % | 99.4 % | — |
| lead time gap p10 / p50 [s] | 1.00 / 1.81 | 1.08 / 1.97 | 0.61 / 1.81 |
| lag time gap p10 / p50 [s] | 0.14 / 0.72 | 0.13 / 0.67 | 0.75 / 2.27 |
| entrant − new follower [m/s] | +2.9 | +2.6 | +0.68 |
| new leader − entrant [m/s] | **+1.36** | +0.89 | **−0.56** |
| new follower's gap / its equilibrium gap, p50 | 0.26 | 0.22 | 0.6–0.9 (ratio to normal) |
| new follower brakes harder than its b within 5 s | 0.2 % | 0.4 % | — |

**Forced against voluntary.** Every entering change is LC2013's mandatory
("strategic|urgent") change; none is speed-gain. "Forced" in the sense of a
change at the lane's end is rare (3.7 % in the last 50 m): the entrants change
along the lane's second half, but at walking pace, into the crawling right lane,
behind a leader faster than themselves (the wrong sign against the
measurement), and in front of a follower that is itself crawling, so the
follower does not brake (p50 minimum acceleration 0.0; 0.2 % beyond its b). The
short lag gaps (p50 0.72 s, 26 % of the follower's equilibrium gap) are the gaps
of a crawl, not of a cut-in at speed.

**Discretionary changes.** Through traffic leaves the crawling right lane
upstream (275 changes per run-hour on the 2.8-km approach, nearly all speed-gain
to the left; 170 per run-hour on the acceleration-lane edge, of which 149
cooperative-urgent moves to the left in all 20 runs), and spreads back over the
lanes downstream (427 per run-hour, 778 to the left and 645 to the right in all
runs); their new followers brake harder than b in 1.8–2.7 % of changes.

### 4.4 Queue discharge headways

Headways between successive crossings at the 2,200-m-equivalent section during
discharge, k = 1, 20 seeds pooled (0.5-s step, so quantiles are multiples of
0.5 s) [run]:

| lane | mean [s] | p10 / p50 / p90 [s] | veh/h |
|---|---|---|---|
| 1 (merge lane) | **2.41** | 1.5 / 2.0 / 3.5 | 1,493 |
| 2 | 2.04 | 1.5 / 2.0 / 3.0 | 1,766 |
| 3 | 1.94 | 1.0 / 2.0 / 2.5 | 1,851 |
| 4 | 1.92 | 1.0 / 2.0 / 2.5 | 1,873 |

The recording's peak sections carry 6,626 / 4 = 1,657 veh/h/lane, a mean headway
of 2.17 s [artifact]. The population's measured time headway is T = 1.511 ±
0.521 s (`idm_i24.json`; 1.322 in the capacity-scaled population, 1.580 in the
merge-zone fit, `idm_i24_merge.json`); its mean driver's congested branch
v / (l + s0 + vT) at the peak sections' observed 30–33 km/h is 1,617–1,679
veh/h/lane [computed]. So in the merge fixture three lanes discharge faster than
the recording's average lane and the merge lane slower; the 4-lane total is above
the recording. Headway is not what the corridor lacks at the merge.

### 4.5 The merge is not the bottleneck at k ≥ 0.5

In the fixture the bottleneck is the merge (the crawl sits in the acceleration
lane and the right lane; speeds recover within 400 m of the lane's end and
nothing downstream is slow). But its discharge exceeds the recording at k = 0
already. In the corridor, at the 0.800 demand scale, `dc` admits 99.6 % of the
planned vehicles, its first segment runs at 62 km/h and the merge area (segments
2–4) at 41–47 km/h on average: the merge is not holding a queue. What holds the
corridor is downstream.

## 5. The downstream end [run]

### 5.1 The boundary as a speed limit (batches B, S, S992)

Fixture `straight`: 4 lanes, then an edge whose limit is set each step by
`edge.setMaxSpeed`, as the runner does; saturating inflow (7,600 veh/h); flow
100 m before the limited edge; k = 1 unless stated.

**Constant limits** (200-m limited edge, 300–900 s, 10 seeds):

| limit [km/h] | 20 | 30 | 40 | 50 | 60 | 70 | none |
|---|---|---|---|---|---|---|---|
| veh/h, k = 1 | 4,064 ± 28 | 5,019 ± 40 | 5,705 ± 48 | 6,218 ± 50 | 6,623 ± 49 | 6,905 ± 51 | 7,134 ± 84 |
| veh/h, k = 0 | | 4,999 ± 50 | | 6,150 ± 55 | | 6,801 ± 97 | |

`a_max` hardly moves it (≤ 104 veh/h between k = 0 and k = 1 at 70 km/h, ≤ 68 at
30–50 km/h). At 30 km/h the limited edge passes 1,255 veh/h/lane, where the same
population's congested branch at 30 km/h is 1,617 (mean driver): an IDM driver
whose desired speed *is* the limit keeps the free-road term's longer gaps.

**The replica's schedule** (sim time 0–7,800 s; 2-h study-window means; 5 seeds):

| limited edge | 2-h flow [veh/h] | per-window correlation with the replica's 5,400-m flow |
|---|---|---|
| 200 m (the contract's `exit_buffer_m` default) | 6,016 ± 14 | 0.988 |
| **992 m (as the replica applies it)** | **5,829 ± 14** | — |
| *recording, data x 5,400* | *6,009* | |
| *replica `dc` / `dc_refit`, data x 5,400* | *5,737 / 5,735* | |

The length matters: over 992 m every vehicle relaxes to the limit's long
equilibrium gaps; over 200 m it does not have time to. **As the replica applies
it, the boundary passes 180 veh/h (3 %) less than the recording passed at the
same place** with nothing else in the way. The observed flow is a lower bound on
the real road's downstream capacity, so this representation is too tight by at
least that much.

### 5.2 The Hickory Hollow on-ramp / Bell Road off-ramp weave (batch W)

Fixture `weave`: 4 lanes; a 565-m 5-lane section whose lane 0 starts at the
on-ramp and ends at the Bell Road off-ramp; 4 lanes with free outflow. Saturating
mainline (7,200 veh/h, 5.5 % to Bell Road), on-ramp 687 veh/h (tracked count ÷
coverage), 10 seeds, 300–1,200 s.

- Discharge 300 m downstream: **6,426 ± 82 veh/h**, by lane 1,244 / 1,585 / 1,742 /
  1,855 (right to left). Same signature as the merge: the right lane carries the
  crossings at a crawl (about 20 km/h upstream of the section).
- Entrants change at median **4.7 m/s** (51 % below 5 m/s), 411 m into the 565-m
  section, and 1.9 m/s *slower* than their new leader. I-24's own measurement on
  this weave (HH–BR, entering): 11.7 m/s, 0.83 m/s *faster* than the new leader
  [record, MERGE_MODEL_BRIEF §1.2–1.3].
- With the boundary permissive, the replica's 5,400-m flow tops out at about
  6,200–6,350: this weave's level.

### 5.3 The downstream end alone (batches DS, DS992)

Fixture `ds`: the replica from data x 1,000 to the boundary: 4 lanes 2,352 m; the
Hickory Hollow off-ramp diverge (5 lanes, 473 m); 4 lanes 590 m; the Hickory Hollow
on / Bell Road off section (5 lanes, 565 m); 4 lanes; the limited edge with the
replica's schedule. Saturating mainline (7,400 veh/h, round-robin lanes) with the
replica's mean exit fractions (Hickory Hollow 9.13 %, Bell Road 5.29 % of the
rest) and its Hickory Hollow on-ramp inflow steps; 7,800 s; 10 seeds; no Old
Hickory merge at all.

| arm | 2,200 | 3,200 | 4,800 | 5,400 | collisions |
|---|---|---|---|---|---|
| DS: 1,461 m + 200-m limited buffer | 6,362 ± 20 | 6,319 ± 19 | 6,300 ± 21 | 5,959 ± 20 | 0 |
| **DS992: 397 m + the 992-m limited edge (the replica)** | **6,114 ± 20** | **6,076 ± 20** | **6,038 ± 24** | **5,744 ± 22** | 0 |
| DS992 − DS, paired | −248 ± 15 | −244 ± 15 | −262 ± 12 | −215 ± 13 | |
| *replica `dc_refit`* | *6,047* | *5,983* | *6,047* | *5,735* | |
| *recording* | *6,626* | *6,639* | *6,170* | *6,009* | |

**DS992 reproduces the replica's ceiling with no merge in the fixture**: within
+67 / +93 / −9 / +9 veh/h of `dc_refit`, per-window correlation 0.89 / 0.87 /
0.97 / 0.99, and the replica's speed pattern (about 30 km/h from data x 1.0 to
3.3 km, 36 km/h at the diverge, 25–28 km/h at 4.0–4.9 km before and in the weave,
27–29 at 5.0–5.4 km). The pre-registered DS prediction (on the 200-m buffer) failed:
that version passes about the recording's 5,400-m flow and 6,362 at 2,200 m. The
difference between the two is the boundary's length, nothing else.

Inside DS992 (2 h, 10 seeds pooled):
- **Per lane at 5,400 m:** 1,367 / 1,443 / 1,472 / 1,463 veh/h (right to left),
  mean headways 2.63 / 2.50 / 2.45 / 2.46 s against the recording's 2.40 s
  (6,009 / 4) [run, `DS992_headways.json`].
- **The weave's entrants** change at median 4.3 m/s (55 % below 5 m/s), 477 m into
  the 565-m section; the Bell Road exiters at 6.5 m/s; the Hickory Hollow exiters
  at 9.3 m/s, 102 m into the 473-m diverge. New followers brake harder than b in
  0.6–2.5 % of these changes.

### 5.4 The whole corridor as a fixture (batch FULL)

Fixture `full`: the replica's corridor as a straight plain-XML network (4 lanes
3,070 m; the Old Hickory acceleration lane, 5 lanes 975 m; 4 lanes 1,496 m; the
Hickory Hollow diverge 473 m; 4 lanes 590 m; the Hickory Hollow on / Bell Road off
section 565 m; 4 lanes 397 m; the 992-m limited last edge with the measured
schedule). The demand is the replica's own: `build_corridor_plan` and
`write_corridor_routes` on `scenarios/i24_replica_flow_speedcal_dc_refit.yaml`,
seeded with the replica's first 10 seeds, so the drawn drivers, departures, entry
lanes, ramp volumes and exits are the replica's; only the network is synthetic.

| | 200 | 1,000 | 2,200 | 3,200 | 4,800 | 5,400 | realised |
|---|---|---|---|---|---|---|---|
| **FULL, refit demand (10 seeds)** | 5,262 ± 37 | 6,122 ± 35 | **6,047 ± 28** | **5,979 ± 36** | 6,059 ± 19 | **5,743 ± 15** | 0.923 |
| replica `dc_refit` (20 seeds) [artifact] | 5,233 | 6,122 | 6,047 | 5,983 | 6,047 | 5,735 | 0.921 |
| recording | 5,418 | 5,752 | 6,626 | 6,639 | 6,170 | 6,009 | |

Segment speeds (km/h, data x 0–5.5 km): FULL 21 / 21 / 24 / 30 / 30 / 30 / 29 / 24 /
24 / 27, replica 22 / 22 / 25 / 32 / 32 / 32 / 32 / 25 / 24 / 29. **A straight
fixture with the replica's demand reproduces the replica's flows to within
12 veh/h at every section and its realised demand to 0.002.** Nothing specific
to the OSM geometry (curvature, junction shapes, short edges, the 72-m offset
of the compiled chain) is needed to explain the ceiling. 0 collisions; 2 steps
below −8.9 m/s² in 10 runs, and 6 of the 9 steps below −7 m/s² are on the
limited edge itself (data x 6,257–6,424), where the vehicles already on the
edge brake when the schedule steps down.

Per lane at 2,200 m: 1,458 / 1,405 / 1,500 / 1,684 veh/h (right to left); at
5,400 m: 1,360 / 1,444 / 1,476 / 1,463.

## 6. Where the 579 veh/h go [run, artifact, estimate]

Two-hour flows obey 2,200 m = 5,400 m + the net exits in between (Hickory Hollow
off + Bell Road off − Hickory Hollow on; storage over two hours is negligible).
With FULL (refit demand) standing in for the replica:

| | 2,200 m | = 5,400 m | + net exits |
|---|---|---|---|
| recording | 6,626 | 6,009 | 617 |
| FULL / replica | 6,047 | 5,743 | 304 |
| **gap** | **579** | **266** | **313** |

| component | veh/h at 2,200 m | basis |
|---|---|---|
| **The boundary representation**: the measured schedule applied as every vehicle's desired speed over the 992-m last edge | **≈ 234** (+208 at 5,400 m) | L4 (FULL with the limit on a 200-m buffer) − FULL, paired, 10 seeds, 234 ± 24 [run] |
| The downstream lane-change sections (Hickory Hollow diverge, Hickory Hollow–Bell Road weave) and the lane use they leave | ≈ 15–60 at 5,400 m | FULL+L5 5,995 and FULL+L4 5,952 against the recording's 6,009; DS 5,959 against the straight fixture's 6,016 (same boundary) [run] |
| **The net exits between the sections**: the recording's section flows lose 617 veh/h between 2,200 and 5,400 m, the model's ramps 304 (329 with L4) | ≈ 288 | [artifact]; see below |
| The Old Hickory merge | **0** | the merge alone passes 6,979 (§4); DS992 reproduces the ceiling without it (§5.3) [run] |

**The net-exit term is not a model discharge.** The model's ramp volumes come from
the recording's own ramp-lane counts (exit fractions = ramp-lane crossings ÷
mainline crossings; on-ramp inflows = ramp-lane counts ÷ coverage, times the
demand scale). At the pooled coverage those counts give a net exit of about
235 veh/h (Hickory Hollow off 594 + Bell Road off 328 − Hickory Hollow on 687)
[estimate], close to the model's 304 and far from the 617 that the section flows
imply. The section flows themselves do not conserve with the ramp counts, and
ramp coverage cannot be the reason: between 3,200 and 4,800 m the tracked
on-ramp count exceeds the tracked off-ramp count (431 against 370), so no ramp
coverage factor turns the expected +61 into the observed −286 (§2); over 3,200 →
5,400 m the ramp lanes would have to be tracked at about 0.36 of the mainline's
rate (tracked net ramp flow 142 against a tracked section difference of 391)
[estimate]. The consistent reading is that the section counts' coverage differs
from section to section by several per cent: documented in docs/I24_DATA.md §4
("the spread across sections is a coverage diagnostic … fragments break at camera
boundaries and under overpasses"), while the validator divides every section by
one pooled coverage per window. Until it is measured, about half of the peak
sections' shortfall belongs to the target, not to the model.

**Why `a_max` stopped helping.** Not because the downstream end ignores it — my
pre-registered expectation, which failed (§7.6): under the measured schedule the
downstream end's ceiling rises with `a_max` (DS992 at 2,200 m: 5,833 / 5,992 /
6,114 veh/h at k = 0 / 0.5 / 1), although a constant limit hardly responds (§5.1,
≤ 104 veh/h). The driver grid's flat k = 0.5 → 1 (6,027 → 6,021, one seed each)
is its demand: at scale 0.800 the corridor delivers about 6,030 veh/h to 2,200 m
once the merge stops holding vehicles back (departed share 0.984 at k = 0,
0.995–0.996 from k = 0.25), and both arms carry that. The refit's 15.6 % more
demand then meets the downstream ceiling at about 6,050 (k = 1), which is why it
built a backlog instead of flow. `a_max` would still raise the ceiling, by
about 120 veh/h from k = 0.5 to 1 on the fixture, at the price of the waves
(stripe fronts per run 114 / 84 / 69 at k = 0 / 0.5 / 1) — and k = 1 is already
the top of the measured range.

## 7. Levers: pre-written rationale and result [run]

Each lever's rationale and prediction were written into
`artifacts/i24_discharge_2026-10-07/prereg.md` before its first run (entry time
in brackets). L1–L3 run on DS992 (the downstream-end fixture that reproduces the
ceiling), paired against its k = 1 arm re-run with the wave readings
(`k1_992w`, which reproduces DS992 bit for bit), seeds 1–10. L4–L5 run on FULL
(refit demand, the replica's first 10 seeds), paired against `full_refit`.
"Waves" are backward fronts per run from the registered detectors of
`validation.waves` on the fixture's measured span (standard: 40 km/h on
15 s × 75 m; stripe: 25 km/h on 10 s × 50 m); the boundary-zone speed is the
mean speed where the schedule was measured, against the schedule's 49.9 km/h
(a check added after L4's run, labelled post hoc there).

| lever | Δ 2,200 m | Δ 5,400 m | collisions | steps < −8.9 m/s² (10 runs) | new followers braking beyond b | Δ waves standard / stripe | boundary-zone speed [km/h] |
|---|---|---|---|---|---|---|---|
| base DS992 (`k1_992w`) | 6,114 | 5,744 | 0 | 0 | 0.6 % (weave entrants) | 6.4 / 68.7 per run | 32.2 |
| **L1a** `lcAssertive` 1.25 | **+74 ± 20** | +53 ± 16 | 0 | 3 | 2.4 % | −2.2 ± 2.9 / −18.0 ± 8.0 | 31.7 |
| **L1b** `lcAssertive` 3.38 | +92 ± 20 | +65 ± 15 | 0 | **836** | **14.8 %** | −4.8 ± 2.8 / −22.0 ± 6.0 | 31.3 |
| **L2** `actionStepLength` 1.0 s | **−2,249 ± 935** | −2,483 ± 968 | **12** | 160 | 1.7 % | +1.3 ± 3.2 / −19 ± 23 | 26.9 |
| **L3** EIDM (SUMO defaults) | **+704 ± 21** | **+636 ± 18** | 0 | 0 | 2.6 % | +26.5 ± 6.4 / **−39.9 ± 6.5** | 42.5 |
| base FULL (`full_refit`) | 6,047 | 5,743 | 0 | 2 | | 5 / 51 per run | 32.9 |
| **L4** limit on a 200-m buffer | **+234 ± 24** | **+208 ± 18** | 0 | 2 | | +4 ± 2 / +17 ± 8 | 32.8 |
| **L5** equilibrium-consistent limit | **+257 ± 27** | **+251 ± 24** | 0 | 0 | | +4 ± 2 / +18 ± 6 | 39.7 |

### 7.1 L1 — `lcAssertive` at the measured-gap mapping (05:25)

*Rationale (as written).* LC2013 divides both secure gaps by `lcAssertive`; the
I-24 critical gaps map to 1.25 on the lag side and 3.38 on the lead side
(MERGE_MODEL_BRIEF §3 item 11, WP-82). The fixtures show weave entrants refusing
gaps until they crawl near the section's end. Prediction: a gain at both values,
larger at 3.38; risk: harder braking of new followers.

*Result.* The gain is real but small (+74 / +92 veh/h, 14–18 % of DS992's 512-veh/h gap at
2,200 m), and the entrants still cross at walking pace (median 4.5 / 5.1 m/s
against 4.3; measured 11.7). At 3.38 the new followers brake harder than their
comfortable deceleration in 14.8 % of crossings and the fixture records 836
vehicle-steps below −8.9 m/s² (3,857 below −4.5 per run): the gain is bought
with emergency braking. At 1.25: 3 steps below −8.9 m/s² against 0, stripe
fronts −26 %. **Not a fix.** It acts on the weave, which holds about a tenth of
the gap; it leaves the entrants' speed — the measured mismatch — where it was.

### 7.2 L2 — reaction time, `actionStepLength` 1.0 s (05:25)

*Rationale (as written).* The fleet reacts at the step length, 0.5 s, the
shortest possible; 1 s is the common literature value. A sensitivity of the
plausible range, not a calibration. Prediction: a loss; if so, the
reaction-time route to more discharge is closed.

*Result.* The prediction held, violently: −2,249 ± 935 veh/h, 12 collisions, 160
steps below −8.9 m/s², realised demand 0.586 (several seeds gridlock). The fleet
already reacts as fast as the step allows; slower reactions cannot add discharge,
and this population is not safe at a 1-s action step. **Closed.**

### 7.3 L3 — EIDM against IDM (05:25)

*Rationale (as written).* The same population on the I-94 corridor's model
(SUMO defaults). Recorded on the OR fixture: −3 %. Prediction: no gain; a
model-consistency check, not a candidate.

*Result.* **The prediction failed**: +704 ± 21 veh/h at 2,200 m, and the 5,400-m
flow (6,380) *overshoots* the recording (6,009) by 6 %. The congested share of
the span falls from 0.88 to 0.40, the stripe fronts by 58 % (−39.9 ± 6.5 per
run), the boundary zone runs at 42.5 km/h. EIDM is built on the improved IDM,
whose equilibrium gap at the desired speed is s0 + vT without the IDM's free-road
inflation (the "improved IDM" of Treiber & Kesting 2013, *Traffic Flow
Dynamics*, ch. 11); so the limited edge stops
being a bottleneck. This **confirms the mechanism** of §5.1 — the IDM driver
whose desired speed is set to the boundary's observed mean speed — from the
other side. It is not a fix: it overshoots the flow, removes most of the
stop-and-go content the replica must keep (CLAUDE.md §3.1), changes the model
family away from the one I-24 was calibrated in, and its merge behaviour was
worse on the OR fixture.

### 7.4 L4 — the boundary limit on a 200-m exit buffer (05:50)

*Rationale (as written).* The schedule is the observed mean speed of
congested traffic on data x 5,492–6,437; imposing it as every vehicle's desired
speed over 992 m turns a kilometre into a low-desired-speed road, and its
saturated throughput (5,829) is below the flow the recording carried there
(6,009), which is a lower bound on the real downstream capacity. The 200-m buffer
is the contract's own default for `CorridorNetwork`. Admissibility criterion:
saturated straight-fixture throughput between 6,009 and 6,309 veh/h — 992 m
fails (5,829), 200 m passes (6,016). Prediction on FULL: refit +150 to +300 at
2,200 m, still below 6,626; dc unchanged within ±50; 0 collisions; waves not
removed.

*Result (refit).* Every prediction held: +234 ± 24 at 2,200 m (6,281), +244 ± 23
at 3,200 m (6,223), +208 ± 18 at 5,400 m (5,952, the recording 6,009); realised
demand +0.023 (0.946); 0 collisions; more backward fronts, not fewer. Against the
GEH thresholds (6,225 / 6,238): 2,200 m passes (GEH 4.3), 3,200 m misses by
15 veh/h (GEH 5.2) [computed]. On the limited edge itself the −7 to −8 m/s² steps
of the replica's representation (6 of 9 in `full_refit`) disappear.
*Post-hoc check (not registered before this run).* The boundary zone still runs
at 32.8 km/h against the schedule's 49.9 (window RMSPE 0.36; `full_refit` 32.9,
0.34): the queue from the short buffer still covers the zone. L4 restores the
throughput the recording shows at the boundary but not its speed. **Partly
right**: it removes the representation's throughput deficit and its hard-braking
artefact; it does not make the boundary reproduce the measured state.

*Result (dc demand, 5 seeds as re-registered at 05:57).* `full_dc` reproduces the
replica's `dc` (2,200 m 6,029 ± 19 against 6,031; 5,400 m 5,706 ± 44 against
5,737; realised 0.995 against 0.996). L4 there: 2,200 m +3 ± 5 (the prediction
"unchanged within ±50" held: at scale 0.800 the corridor is demand-limited),
5,400 m +108 ± 38, 0 collisions, standard fronts +13 ± 5, stripe −6 ± 15. Its
segment speeds rise everywhere (segments 8–9 34 / 31 km/h against 24 / 24), so
L4 would need the demand level refitted (the FHWA sequence), as the calibrated
drivers did.

### 7.5 L5 — an equilibrium-consistent boundary limit (06:45)

*Rationale (as written).* The schedule is a mean speed of traffic whose drivers'
desired speeds are not that speed. A limit v0 reproduces the measured state when
the population's IDM equilibrium at the measured speed carries the measured flow,
q_eq(v_obs; v0) = q_obs with q_eq(v; v0) = v / (l + (s0 + vT)/√(1 − (v/v0)⁴)).
With the fleet's mean driver (T 1.3222 s, s0 2.5327 m, l 5 m), v_obs = the
schedule's study-window mean (13.869 m/s, 49.9 km/h) and q_obs = the recording's
5,400-m flow per lane (1,502 veh/h), v0 = 1.2185 × v_obs: one constant from
measured quantities, applied to every schedule step on the replica's own 992-m
edge. Criteria: (a) boundary-zone mean speed within ±5 km/h of 49.9 and window
RMSPE ≤ 0.20; (b) 5,400-m flow within 5,829–6,309; (c) 0 collisions; (d)
backward fronts (standard, stripe) not lower than `full_refit` by more than a
third. Prediction: (a) improves but is not met, (b) met, peak sections +200 to
+400.

*Result.* (b), (c) and (d) are met; **(a) is not**, as predicted:

| | `full_refit` | L5 | criterion |
|---|---|---|---|
| 2,200 / 3,200 m [veh/h] | 6,047 / 5,979 | **6,304 / 6,245** (+257 ± 27 / +265 ± 32) | reported |
| 5,400 m [veh/h] | 5,743 | **5,995** (+251 ± 24) | (b) 5,829–6,309: met |
| boundary-zone speed, mean / window RMSPE | 32.9 km/h / 0.34 | 39.7 km/h / 0.211 | (a) 44.9–54.9 and ≤ 0.20: **not met** |
| collisions / steps < −8.9 m/s² | 0 / 2 | 0 / 0 | (c) met |
| fronts per run, standard / stripe | 5 / 51 | 9 / 69 (+4 ± 2 / +18 ± 6) | (d) met |
| realised demand | 0.923 | 0.947 | |
| segment speeds, data x 0–5.5 km [km/h] | 21 / 21 / 24 / 30 / 30 / 30 / 29 / 24 / 24 / 27 | 23 / 23 / 26 / 35 / 34 / 34 / 35 / 28 / 26 / 32 | recording 36 / 33 / 30 / 31 / 38 / 37 / 38 / 29 / 30 / 34 |

L5 moves the replica's boundary to the measured flow (5,995 against 6,009) and
cuts the zone's speed error by about 40 % without a free parameter, and on the fixture
both peak sections clear their GEH thresholds (6,304 ≥ 6,225, GEH 4.0; 6,245 ≥
6,238, GEH 4.9) [computed]. The zone still runs 10 km/h slow: the mean driver's
equilibrium overstates a heterogeneous platoon (the slow drivers set the gaps),
and 30-s limit steps are not equilibrium. The demand refit still leaves a 5.3 %
backlog, so this fixture result is not an acceptance reading (it would need the
demand level refitted under the new boundary, the FHWA sequence).

### 7.6 The plateau check (05:57)

*Registered.* If the ceiling at k ≥ 0.5 is the downstream end, its own throughput
must be nearly independent of `a_max`: DS992 at k = 0 and 0.5 within ±150 / ±80
veh/h of k = 1.

*Result.* **Failed.** DS992, paired against k = 1, seeds 1–10:

| k | 2,200 m | 5,400 m | stripe fronts per run | collisions |
|---|---|---|---|---|
| 0 | 5,833 ± 46 (−281 ± 46) | 5,547 ± 37 (−197 ± 34) | 113.8 | 0 |
| 0.5 | 5,992 ± 23 (−121 ± 20) | 5,663 ± 20 (−81 ± 23) | 84.3 | 0 |
| 1 | 6,114 ± 20 | 5,744 ± 22 | 68.7 | 0 |

A constant limit hardly responds to `a_max` (§5.1), but the measured schedule
steps every 30 s, and the queue on the limited edge has to discharge after every
step up, at a rate `a_max` sets (an interpretation; not traced vehicle by
vehicle). So the downstream ceiling is `a_max`-sensitive, and
the plateau on the driver grid is its demand, not this ceiling (§6). The waves
fall as `a_max` rises (fronts −40 % from k = 0 to k = 1), the risk recorded in
docs/DISCHARGE_CALIBRATION.md §1.

## 8. Recommendation

### 8.1 What limits the discharge

**Not the Old Hickory merge.** In the I-24 geometry the merge alone discharges
6,979 veh/h with the calibrated drivers (6,620 with the old ones); a fixture of
the downstream end with no merge in it reproduces the replica's ceiling; and the
whole corridor as a straight fixture with the replica's own demand reproduces the
replica to within 12 veh/h at every section. The ceiling at about 6,050 veh/h is
the downstream end, and the shortfall against the recording splits roughly in
two (§6):

1. **Model side, about half: the measured downstream boundary as implemented.**
   The schedule — the observed mean speed on data x 5,492–6,437 — is imposed as
   every vehicle's speed limit, i.e. its IDM desired speed, over the whole
   992-m last edge. IDM drivers whose desired speed is the observed mean speed
   travel well below it (32.9 km/h against 49.9) and pass less than the
   recording did there (5,743 against 6,009 veh/h). This is the IDM's free-road
   gap inflation near the desired speed: the IIDM-based EIDM removes it (L3,
   +704 veh/h, now too much), and so does a limit derived from the population's
   equilibrium (L5, +257). The Hickory Hollow diverge and weave add a smaller
   loss (about 15–60 veh/h at 5,400 m), with the same signature as the merge:
   crossers at walking pace in a crawling right lane.
2. **Target side, about half: the net exits.** The recording's section flows
   lose 617 veh/h between 2,200 and 5,400 m; its own ramp counts carry about
   235 and the model 304. Section coverage varies (docs/I24_DATA.md §4) and
   the validator applies one pooled coverage to all sections, so the peak
   sections' 6,626 / 6,639 are not consistent with the downstream sections; how
   much of the 6,626 is real is unmeasured.

**Why `a_max` beyond +0.5 sd stopped helping.** On the driver grid the corridor
was demand-limited from k = 0.25 (departed 0.995–0.996): the plateau was the
0.800 demand scale. With more demand (refit) the downstream ceiling binds at
about 6,050. That ceiling does still rise with `a_max` under the time-varying
schedule (+121 veh/h from k = 0.5 to 1 on the fixture), but k = 1 is the top of
the measured range and every step costs waves (§7.6).

### 8.2 What not to do

- **No further `a_max` shift and no merge-model work aimed at discharge.** The
  merge alone passes the recording's flow already at k = 0 (§4); its real mismatch —
  entrants crossing at 4 m/s against the measured 12.7, behind a faster leader
  where real entrants are faster than theirs — is a lane-change realism item for
  speeds and waves near the gore, not for the peak-section flow.
- **No `lcAssertive` 3.38** (836 steps below −8.9 m/s² for +92 veh/h), **no 1-s
  action step** (collapse, 12 collisions), **no EIDM swap on I-24** (overshoots
  the recorded flow by 6 %, removes 58 % of the stripe fronts, a model-family
  change). `lcAssertive` 1.25 (+74) is smaller than the target's own uncertainty
  (8.4) and adds emergency braking; not worth a corridor run on its own.
- **Do not judge discharge on the peak sections alone** until their coverage is
  checked (8.4).

### 8.3 Proposed amendment B1 — an equilibrium-consistent boundary limit

*Proposed, not adopted. Written 2026-10-07 after the fixture results above and
before any corridor run; the criteria below are fixed now and are not to be
re-thresholded.*

**Change.** On a corridor with a measured downstream boundary, each schedule
step's limit is multiplied by one factor f, computed (not fitted) so that the
fleet population's mean driver, in IDM equilibrium at the schedule's
study-window mean speed v̄, carries the recorded flow per lane q̄ at the last
measured section: q_eq(v̄; f·v̄) = q̄. Inputs are the population artifact
(T, s0, vehicle length), the schedule and the recorded flow; nothing is tuned to
the criteria. I-24 (k = 0 and k = 1 share T and s0, so one value): v̄ =
13.869 m/s, q̄ = 1,502 veh/h/lane, **f = 1.2185**. I-94 (which shares the code
path, `runner._build_network`) gets its own f from its population and boundary
observations, computed and recorded before its run. Implementation as an opt-in
field (e.g. `BoundarySpec.limit_factor`, default 1.0, hash-neutral at the
default, as done for other default-off inputs), so every published result stays
reproducible. The 200-m buffer (L4) is the fallback representation if B1 fails
A2 below: equal on flow, worse on the zone's speed.

**Fixture stage (done, §7.5).** Flow at the boundary within −3 / +5 % of the
recording (5,995: met); 0 collisions (met); no loss of backward fronts (met);
boundary-zone speed within ±5 km/h and RMSPE ≤ 0.20 (**not met**: 39.7 km/h,
0.211). B1 therefore goes to the corridor with that failure stated, as a
strictly better representation than the current one on both measured boundary
quantities (flow 5,995 against 5,743, speed 39.7 against 32.9 km/h), not as one
that reproduces the boundary.

**Corridor acceptance (cloud; 20 seeds, the step-3 batteries' seeds; I-24
canonical `i24_replica_flow_speedcal`, calibrated `_dc` and `_dc_refit` at their
committed demand scales, then, only if A1–A5 hold, the FHWA re-sequence of the
demand level on the adopted arm; I-94 `mndot_i94_wb_stpaul_weave_dc` through the
baseline gate). Each against the same-code reference on the same seeds. A2 is
read on `_dc_refit`, the arm whose demand saturates the downstream end (the
0.800 arms are demand-limited, §7.4); I-94's A2 band is computed from its own
boundary observations before its run:**

| # | criterion | fixed now |
|---|---|---|
| A1 | safety | 0 collisions in every run; steps below −8.9 m/s² not above the reference |
| A2 | the boundary state | `_dc_refit`: 5,400-m 2-h flow within 5,829–6,309 veh/h; boundary-zone mean speed error smaller in magnitude than the reference's |
| A3 | no winning by backlog | realised demand ≥ the reference's (FRISCO_PROTOCOL Amendment 2 clarification) |
| A4 | emergent waves | the criteria profile's wave verdict unchanged where it passes today (canonical I-24: 15.9 km/h, pass); backward fronts per replicate not below the reference by more than a third |
| A5 | speeds | 15-min segment-speed RMSPE not above the reference + 0.02 |
| — | reported, not gating | peak-section flows against the GEH thresholds; lane shares; I-94 gate rows both ways |

Adopt only if A1–A5 hold on the I-24 canonical arm and on I-94; otherwise report
and keep the current representation. Cost: about what step 3 cost for the same batteries, one n2-standard-32 for
about 1 h 45 min, about $2.80 (docs/DISCHARGE_CALIBRATION.md §4) [estimate].

### 8.4 Measure the target before using it again

A data-only cloud stage (no simulation): Edie flows (vehicle-distance per cell,
insensitive to where fragments break, docs/I24_DATA.md §4) in 500-m cells
around 2,200, 3,200, 4,800 and 5,400 m, and the ramp-lane counts, with their
conservation residuals per 15 min. If the peak sections' recorded 6,626 / 6,639
do not survive it, the GEH targets at those sections must be restated before
any discharge calibration uses them. Cost: minutes of a small VM [estimate].

#### 8.4.1 Count-consistency check (built, not run)

*Built 2026-10-07; the reading rules below are fixed now, before any run.*
`scripts/i24_count_consistency.py` (tests: `tests/test_scripts/test_i24_count_consistency.py`,
synthetic tables only), opt-in stage `p11_i24_count_check`
(`artifacts/i24_discharge_2026-10-07/stage_p11_count_check.sh.txt`, not yet in
`scripts/gcp/pipeline_i24.sh`), output `artifacts/i24_count_consistency.json`. Per 5-min
window, 06:30–08:30: lanes 1–4 crossings at the six sections (each fragment once; the
validator's own rule beside it, which must reproduce `counts_tracked` exactly, or the run
is void); the builder's ramp-lane counts, with the vehicles flagged that were in lanes 1–4
before an on-ramp count or return to them after an off-ramp count (through traffic in a
ramp lane); the storage between sections; the lane-5+ flow at every section; and per lane
and 15-min window the recommended coverage estimator applied at each section's own 500-m
cell. Cost on n2-standard-16 (about $0.78/h): about 3–6 min of stage time, about 20 min
billed with boot and setup, about $0.26 [estimate]; `--cap-min 45` bounds it at about $0.60.

One caveat for §2 and §6 as written: the 3,200 → 4,800 m comparison omits the vehicles in
the weave lane at 4,800 m (lanes 5+ are not counted at a section), so it is not a clean
balance. The rules below read only the 2,200 → 5,400 m span, whose end sections are
outside the ramp zones.

**Rules.** All four quantities are residuals on 2,200 → 5,400 m:
`R = Q(5,400) − Q(2,200) − (on − off) + storage rate`, 2-h means with a 95 % circular-block
bootstrap (15-min blocks). The four variants are:
- `Rp`: everything at the pooled recommended coverage, the correction behind 6,626 / 6,639;
- `Rs`: each section at its own measured coverage;
- `Ra`: `Rp` with the flagged through traffic removed from the ramp counts;
- `Rsa`: both corrections together.

- **Non-conservation** means `|Rp| ≥ 100 veh/h` and its interval excludes 0. Otherwise the
  outcome is "consistent".
- **The 6,626 / 6,639 targets are inconsistent** when the counts do not conserve and
  `|Rs| ≤ ½ |Rp|` (this reading wins if `Ra` also meets the bound): one pooled coverage
  hid how coverage varies between sections. Expected
  support: the measured coverage is higher at 2,200 / 3,200 m than at 5,400 m. Every
  section's target is then restated at its own coverage, 5,400 m included. Discharge is
  judged against the restated values, and 6,626 / 6,639 are not used again. If only `Rsa`
  meets the bound, the targets are restated in the same way and the ramp inputs are
  flagged too.
- **The model's downstream representation is at fault** when the adopted targets survive
  and the model still falls short. The adopted targets are the restated ones above, or the
  original ones in two cases: the counts conserve, or `|Ra| ≤ ½ |Rp|` (the ramp counts are
  inflated by through traffic). "Falls short" means `_dc_refit`'s 2-h flow at 2,200 or
  3,200 m is below the adopted target with GEH ≥ 5 on 2-h flows. That shortfall is then
  the model's. The merge passes more than the targets (§4), so the shortfall lies
  downstream: in the boundary representation (B1's corridor round, §8.3, is the test) and,
  under the ramp outcome, also in the model's ramp volumes, built from the inflated counts,
  which make its net exits too small. If instead the restated peak targets come within
  GEH 5 of the model's 6,047 / 5,983, the peak-section shortfall was in the targets. B1 is
  then judged on the boundary state alone (A2).
- **Unexplained**: the counts do not conserve and no variant reaches `½ |Rp|`. The targets
  can be neither confirmed nor restated from this recording, and nothing on the peak
  sections is calibrated until external counts arrive (TDOT radar, I24_DATA.md §4).

The script applies these rules mechanically (`verdict` in the artifact). The window
bootstrap leaves out the coverage estimator's own error. Ramp-lane coverage cannot be
measured (`artifacts/i24_coverage_lane5.json`), so the ramps keep the pooled coverage, as
the builder does.

#### 8.4.2 Count-consistency result — 2026-10-07 (stage p11; one n2d-standard-16, 131 s of stage time, about $0.30, self-deleted)

`artifacts/i24_count_consistency.json`; log `artifacts/i24_discharge_2026-10-07/p11_i24_count_check.log.txt`.
The run is valid: the check's own crossing counts equal the validator's bin for bin (`checks.validator_counts`:
max difference 0) and reproduce the committed counts; the builder's four ramp counts are reproduced exactly.

**Residuals on 2,200 → 5,400 m, 06:30–08:30 (2-h means, veh/h; §8.4.1 rules applied mechanically):**

| variant | residual R | reads |
|---|---|---|
| `Rp` pooled coverage | **−403** [−689, −135] (15-min circular-block bootstrap) | non-conservation: material and the interval excludes 0 |
| `Rs` each section at its own coverage | +294 | does not explain it (bound ½·403 = 202 not met; the sign flips) |
| `Ra` through traffic removed from the ramp counts | **−191** | explains it (≤ 202) |
| `Rsa` both corrections | +506 | not needed |

The support expected for the section-coverage reading was there — measured coverage is higher at 2,200 and
3,200 m than at 5,400 m by 0.069 [0.052, 0.085] and 0.100 [0.087, 0.115] — but restating each section at its own
coverage makes the imbalance worse, not better, so it is not the explanation.

**Outcome (`verdict.outcome`): `ramp_counts_contaminated`.** The section targets stand at the pooled coverage
(6,626 / 6,639 / 6,009 veh/h, unchanged). The ramp counts are at fault: vehicles that were in lanes 1–4 before an
on-ramp count, or returned to them after an off-ramp count, were counted as ramp traffic. Tracked vehicles per hour
over the window (lower bounds: a flag reads the fragment's history inside the loaded chunk only):

| ramp | counted | flagged through traffic | share |
|---|---|---|---|
| Old Hickory Blvd on (950 m) | 664.5 | 93.5 (in lanes 1–4 before the count) | 14 % |
| Hickory Hollow Pkwy off (3,700 m) | 370.0 | 8.0 (returned to lanes 1–4 after it) | 2 % |
| Hickory Hollow Pkwy on (4,600 m) | 431.0 | 152.5 (in lanes 1–4 before the count) | 35 % |
| Bell Road off, collector (5,050 m) | 203.0 | 12.0 (returned after it) | 6 % |

**The model against the adopted targets** (`_dc_refit`, hash ada3f406504b, 20 replicates, 2-h flows): 2,200 m
6,047 against 6,626 (GEH 7.28, fail); 3,200 m 5,983 against 6,639 (GEH 8.25, fail); 5,400 m 5,735 against
6,009 (GEH 3.57, pass). By the fixed rule the peak-section shortfall (580–656 veh/h) is **the model's**, and it
lies downstream: in the boundary representation (B1's corridor round, §8.3, is the test) and in the model's ramp
volumes, which are built from the contaminated counts. The on-ramp inputs carry the flagged through traffic — about
150 and 240 veh/h at the pooled coverage at Old Hickory and Hickory Hollow — so the model inserts vehicles the road
did not receive and its net exits are too small. What that does to the peak sections is not predictable from this
check (the extra entries feed the queue that the downstream end discharges, §7.4), so it is a run, below.

#### 8.4.3 Proposed amendment B2 — ramp counts without through traffic

*Proposed, not adopted. Written 2026-10-07 after §8.4.2 and before any corridor run; criteria fixed now.*

**Change.** The I-24 demand builder subtracts, per 5-min window, the vehicles flagged in §8.4.2 from each ramp's
count before coverage scaling (on-ramps: vehicles in lanes 1–4 before the count; off-ramps: vehicles that return to
lanes 1–4 after it). Opt-in builder option; it changes the demand inputs, so it is a new scenario family (`_rc`),
never a silent change to a published one. The mainline-entry demand is unchanged (the section targets stand).

**Corridor acceptance (cloud; 20 seeds, the step-3 batteries' seeds; arms `_dc_refit` + B2, and `_dc_refit` + B1 +
B2 if B1's stage has run; each against the same-code reference on the same seeds):**

| # | criterion | fixed now |
|---|---|---|
| R1 | safety | 0 collisions in every run |
| R2 | no winning by backlog | realised demand ≥ the reference's |
| R3 | ramp-lane flows | each ramp's modelled 2-h flow within GEH 5 of its corrected count (the reference is scored against the corrected counts too, reported) |
| R4 | peak sections | 2-h GEH at 2,200 and 3,200 m not above the reference's; reported either way |
| R5 | emergent waves and speeds | wave verdict unchanged where it passes today; 15-min segment-speed RMSPE not above the reference + 0.02 |

Adopt only if R1–R5 hold; R4 is read against the pooled targets of §8.4.2, which stand. Cost: one battery per arm
on n2d-standard-16, about $1.3 per arm [estimate].

### 8.5 Corrections the record needs (owner's call)

- docs/DISCHARGE_CALIBRATION.md §1: "The I-24 fixture's 1,460–1,470 × 4 lanes =
  5,840–5,880 reproduces the replica's peak sections … the merge discharge level
  is too low" does not hold in the I-24 geometry: a 4-lane fixture with the
  975-m acceleration lane discharges 6,620 veh/h with the same drivers (§4).
  The 2-lane fixture's per-lane figure does not scale to 4 lanes.
- docs/DISCHARGE_CALIBRATION.md §4 and docs/MERGE_MODEL_READINESS.md §5 read the
  refit's plateau as "a merge/discharge shortfall". It is a downstream-end
  ceiling (the boundary representation and the Hickory Hollow sections) plus a
  target inconsistency; the merge passes more than the ceiling.
- docs/MERGE_MODEL.md (last section) moved the question "downstream of the
  merge, in how the queue discharges": confirmed for the location, but the
  binding element is the boundary representation, not the population's queue
  discharge.

## 9. Limits

- **Fixtures, macOS.** The corridor rounds on Linux decide; nothing here is a
  validation claim.
- **FULL is straight and synthetic.** No curvature, junction shapes or lane
  widths; ramp lengths assumed (Old Hickory 500 m, Hickory Hollow 400 m); ramp
  speed limit the netconvert `motorway_link` default (80 km/h); edge positions
  on the compiled chain, which is 72 m shorter than the OSM chain before the Old
  Hickory edge. It reproduces the replica's flows and realised demand, and its
  segment speeds within 1–3 km/h (dc arm: 3–11 km/h slower upstream).
- **The target-side half rests on coverage arguments.** The observed flows are
  coverage-corrected with one pooled factor; that section coverage varies is
  read from the conservation residuals and docs/I24_DATA.md §4, not measured
  here (8.4).
- **L5's factor uses the mean driver** and a coverage-corrected flow; a
  heterogeneous platoon sits below the mean driver's equilibrium, which is why
  the zone stays 10 km/h slow.
- **Waves.** Fixture readings of the registered detectors on fixture spans; the
  stack detector (the criterion's) finds no qualifying peak in the FULL refit
  runs with or without L4 / L5 (0–1 of 10), matching the replica's
  `dc_refit`. No lever here restores the I-24 wave criterion lost at k = 1;
  the k-dependence of the fronts (§7.6) is the record's own risk, unchanged.
- **Seeds:** R and G 20; DS, DS992, levers and FULL refit 10; S 5; FULL dc 5
  (re-registered before running). The 2 collisions at k = 0.5 in G are at the
  BLOCK mode's instant red, a fixture artefact.
- **EIDM's string stability** is not computed (the criterion of
  `validation.string_stability` is for IDM); its effect is read from the wave
  detectors only. L1 and L4/L5 do not change car-following; L2 changes only the
  reaction step.

## 10. Reproduce

From `artifacts/i24_discharge_2026-10-07/`, with the snapshot of HEAD `af138fd`
at the path in `fx.py` (or `I24DIS_HEAD`), two processes each:

```
uv run --no-sync python batch_r.py runs/R_or.jsonl                                   # R
uv run --no-sync python batch_g.py G.jsonl k0 k05 k1 --lc-log                        # G
uv run --no-sync python batch_b.py B.jsonl B ; ... S.jsonl S ; ... S992.jsonl S992   # B, S, S992
uv run --no-sync python batch_b.py W.jsonl W --lc-log                                # W
uv run --no-sync python batch_ds.py DS.jsonl k1 k1_992 --lc-log                      # DS, DS992
uv run --no-sync python batch_ds.py LEV.jsonl k1_992w L1a_992 L1b_992 L2_992 L3_992 --lc-log
uv run --no-sync python batch_ds.py LEV.jsonl k0_992w k05_992w                       # plateau check
uv run --no-sync python batch_full.py FULL.jsonl full_refit full_refit_b200 full_refit_l5
uv run --no-sync python batch_full.py FULL.jsonl full_dc full_dc_b200 --n-seeds 5
uv run --no-sync python analyze_g.py G.jsonl G_summary.json --base k0                # and analyze_w / _ds / _full
```

Wall times on the laptop: R 49 s, G 4 min, B 4 min, S 2 min, W 0.5 min, DS and
DS992 8 min each, the levers 56 min, the plateau check 23 min, FULL 4–5 min per
run. Summaries quoted here: `G_summary.json`, `G_speed_zones.json`,
`BSR_summary.json`, `W_summary.json`, `DS_summary.json`, `DS992_headways.json`,
`LEV_summary.json`, `LEV_boundary_zone_speed.json`, `FULL_summary.json`,
`FULL_refit_summary.json`, `FULL_boundary_zone_speed.json`; `runs/R_or.jsonl`.
The raw run files (section crossings, lane-change events; about 0.8 GB) stayed in
the session scratch directory and are not kept.
