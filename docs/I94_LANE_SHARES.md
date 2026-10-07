# Why the I-94 lane shares are 7.6–9.9 pp off (2026-10-07)

The Amendment-1 grid (docs/DISCHARGE_CALIBRATION.md §3) left I-94's per-lane
detector shares 7.6–9.9 percentage points RMSE off at every one of the 25
driver pairs, and raising `lc_keep_right` made it worse. On I-24 the same
comparison is 1.4–4.5 pp. This note asks whether the I-94 gap comes from the
measurement or from driver behaviour. It ran nothing on a corridor. Its
inputs are the committed grid readings
(`artifacts/p3_driver_grid_2026-10-07/grid_i94/**/readings.json`, crossings
by SUMO lane at every station, 2 seeds per pair), the committed targets
(`artifacts/driver_calibration_i94*.json`), the IRIS inventory
(`data/mndot/config/metro_config.xml.gz`), the 30-s per-lane cache (9
weekdays) and one netconvert compile of the grid's reference network (hash
1dc4729644dd, 1.4 s, 190 MB). Nothing here is a validation claim.

## Verdict

**Mixed. The largest single part is a measurement artifact.**

1. **Measurement: S791's lane order is reversed in IRIS.** The loop labelled
   lane 1 (3234) reads the leftmost lane and the loop labelled lane 3 (3236)
   reads the rightmost. Three independent signatures show it (§3). S791
   makes up 57 % of the pooled squared error at the reference pair and 62 %
   at the chosen pair. With the order corrected, the grid's range is
   **5.0–8.2 pp** instead of 7.6–9.9 (4.9–8.6 pp with S791 dropped). Under
   either correction the rule still picks **(k 1, keep-right 0.1)**.
2. **Simulated network: a lane-mapping defect at the 6th Street left exit.**
   Ramp guessing turned the left lane of edge `45782590` into an exit-only
   lane and added a lane on the right. In OSM that left lane is an option
   lane (`turn:lanes slight_left;through|none|none`, 3 lanes). As a result,
   the through lanes shift one place left at 9.30–9.55 km. S791's simulated
   left lane carries the traffic of the upstream middle lane (§4).
3. **Inputs and throughput.** At S791 and S97 the simulated left lane carries
   about the observed flow. The right and middle lanes together are
   700–1,020 veh/h short, which is the known discharge deficit seen lane by
   lane. At S1070 the simulated Mounds Blvd exit carries 30–33 % of the flow
   against at most about 20 % observed, a residual carried over from the
   demand balance.
4. **Behaviour (real, smaller).** At S1066, simulated through traffic leaves
   the left lane 1–2 km before the exits. At S1068 the right lane is not
   refilled after the C-D split. At S1070, through entrants from the 2-lane
   T.H.61 ramp crowd into its inner lane. No single keep-right value fits,
   because the stations pull in opposite directions (§5).
5. The corrected 5–8 pp is still far above the noise floor of about 1 pp
   (§3.8). Also, the I-24 number is not directly comparable: it is four
   vehicle-time shares pooled over 5.5 km and 2 h, while I-94's is 23
   station-lane values measured at points 112–421 m from lane-count changes.

## 1. Stations

Positions are on the simulated chain. Lane-count changes come from the
grid's `LANES.json`. IRIS ramp nodes are taken from
`observations_calibration.json`, projected onto the same chain.

| station | x (m) | lanes IRIS / sim | nearest sim lane-count change upstream | downstream | nearest IRIS ramp node |
|---|---|---|---|---|---|
| S1066 | 4,060 | 3 / 3 | 112 m, 4→3 (end of the guessed Hudson Rd acceleration lane) | 237 m, 3→4 (McKnight Rd acceleration lane) | McKnight entrance +203 m; Hudson entrance −456 m |
| S1067 | 4,970 | 3 / 3 | 421 m, 4→3 (McKnight acceleration lane end) | 211 m, 3→4 (Ruth St / White Bear weave) | Ruth St entrance +128 m |
| S1068 | 5,580 | 3 / 3 | 264 m, 4→3 (White Bear C-D split) | 350 m, 3→4 (C-D re-entry) | White Bear exit −216 m |
| S1069 | 6,684 | 3 / 3 | 369 m, 4→3 (T.H.61 exit) | 705 m, 3→5 (T.H.61 NB two-lane add) | T.H.61 exit −309 m |
| S1070 | 7,675 | 5 / 5 | 286 m, 3→5 (T.H.61 NB add) | 850 m, 5→3 (Mounds Blvd two-lane exit) | T.H.61 NB entrance −380 m |
| S791 | 9,716 | 3 / 3 | 169 m, 4→3 (6th St left exit, §4) | 228 m, 3→4 (40648744 acceleration lane) | left C-D exit −33 m |
| S97 | 11,072 | 3 / 3 | 340 m, 4→3 (T.H.52 weave end); 12th St exit −120 m without a count change | corridor end +406 m | Jackson St exit −64 m |

Shares are in percent, listed from the right lane to the left (IRIS lane 1
first). The window is 07:05–07:35 on the five calibration days. "ref" is
the pair (0, 0) and "chosen" is (1, 0.1). The station RMSE is in pp.

| station | observed | sim ref | sim chosen | RMSE ref / chosen | share of pooled SSE, chosen (as built → S791 corrected) | flow sim / obs (chosen) |
|---|---|---|---|---|---|---|
| S1066 | 25.5 / 36.8 / 37.7 | 32.4 / 35.2 / 32.3 | 34.8 / 36.5 / 28.7 | 5.1 / 7.5 | 10 % → 22 % | −2 % |
| S1067 | 28.3 / 34.8 / 36.9 | 27.0 / 32.8 / 40.2 | 30.1 / 35.0 / 34.9 | 2.4 / 1.5 | 0 % → 1 % | −2 % |
| S1068 | 28.3 / 34.4 / 37.3 | 21.8 / 31.4 / 46.8 | 23.9 / 34.0 / 42.1 | 6.9 / 3.7 | 3 % → 5 % | −2 % |
| S1069 | 31.9 / 32.1 / 36.0 | 26.7 / 33.1 / 40.1 | 28.9 / 35.8 / 35.3 | 3.9 / 2.8 | 1 % → 3 % | 0 % |
| S1070 | 12.8 / 13.9 / 22.1 / 24.4 / 26.7 | 6.0 / 21.3 / 25.6 / 28.8 / 18.4 | 5.0 / 20.3 / 26.5 / 31.0 / 17.2 | 6.4 / 7.1 | 16 % → 33 % | +11 % |
| S791, IRIS order | 40.3 / 33.8 / 25.9 | 22.7 / 28.0 / 49.3 | 20.5 / 29.2 / 50.3 | 17.2 / 18.3 | 62 % → – | −20 % |
| S791, corrected | 25.9 / 33.8 / 40.3 | same | same | 6.4 / 7.1 | – → 20 % | |
| S97 | 26.2 / 33.4 / 40.5 | 16.7 / 33.1 / 50.2 | 17.9 / 34.5 / 47.6 | 7.8 / 6.3 | 7 % → 16 % | −16 % |
| pooled (23 values) | | | | 8.24 / 8.40 as built; 5.87 / 5.76 corrected | | |

**Which stations dominate the error.** In the IRIS lane order, S791 accounts
for 34–64 % of the pooled squared error across the 25 pairs. Once S791 is
corrected, S1070, S97, S1066 and S791 together account for 91 % at the
chosen pair. At the reference pair they account for 74 %, and S1068 adds
18 %.
Upstream of S1070, the simulated station totals are within 0–3 % of the
observed ones, so the share errors there are about which lane drivers
choose, not about how much flow arrives.

## 2. Grid range under the corrections (all 25 pairs, committed readings, no runs)

| scoring | grid range, pp | minimum at | rule's choice |
|---|---|---|---|
| as built (IRIS order) | 7.63–9.86 | (0.5, 0) / (1.0, 0) | (1.0, 0.1) |
| S791 order reversed | 4.97–8.16 | (1.0, 0) | (1.0, 0.1) (9 pairs in the 1-pp band) |
| S791 dropped | 4.90–8.60 | (1.0, 0) | (1.0, 0.1) (8 pairs in the band) |

The targets were fixed before the run (Amendment 1). Correcting S791 is
therefore a correction to a target, not a new selection. Because the chosen
pair does not change, nothing has to be re-run. Whether to record the
correction is the owner's decision.

## 3. Mapping and numbering checks

1. **IRIS lane 1 = rightmost: confirmed.**
   - At S1070 and S1948, IRIS lanes 1–2 are the two lanes that the T.H.61 NB
     entrance adds and the Mounds Blvd exit drops. The r_node `shift` goes
     9 → 7 at the right edge as the lane count goes 5 → 3, and OSM tags
     `45608485` with `turn:lanes |||slight_right|slight_right`. Those two
     lanes carry 2–7 % of the traffic at night (01–05).
   - At 10 of the 11 three-lane stations, lane 1 has the highest occupancy
     per vehicle at night (ms on the loop per vehicle), and lane 3 carries
     the least night traffic (9–17 %; S97 29 %). Both are the
     truck-and-keep-right signature. The one exception is S791.
2. **SUMO side: correct.**
   - The trajectories record `VAR_LANE_INDEX`, where 0 is the rightmost lane
     of the current edge (`runner.py` trajectory capture). Internal links
     are off, so every sample is on a normal edge.
   - `sumo_lane_of_right_number(n) = n − 1` is applied in `_score_detectors`.
     For S1070, lanes 1–5 map to SUMO 0–4.
3. **Per-station lane order: S791 is reversed. Three signatures, 9 weekdays:**
   - *Neighbour correlation.* This test correlates the 5-min lane-share time
     series at each station with those of its upstream neighbour (about
     2,000 windows).
     - Every pair has a positive diagonal and a negative anti-diagonal. For
       example, S1948 lanes 3–5 → S790 gives +0.72 / +0.85 against −0.75 /
       −0.80, and S790 → S97 gives +0.57 / +0.84.
     - The two pairs that involve S791 are exactly inverted. S791 lane 1
       correlates +0.84 with S1948 lane 5 (the leftmost) and −0.73 with
       S1948 lane 3. Against S790, S791 lane 1 ↔ S790 lane 3 is +0.91 and
       S791 lane 3 ↔ S790 lane 1 is +0.81.
   - *Occupancy per vehicle.* At S791 it rises from lane 1 to lane 3 in every
     free-flow band: night 257 / 316 / 356 ms, midday 269 / 307 / 358 ms,
     evening 255 / 278 / 299 ms. At every other three-lane station lane 1
     reads highest at night, for example S790 334 / 320 / 249 ms and S97
     404 / 357 / 274 ms. One loop departs from this in the evening only:
     S1066's lane-3 loop 5054 (field 8.0 ft) reads high then. That is a
     sensitivity difference that does not affect counts, and S1066 passes
     the correlation test.
   - *Night shares.* S791 has 18.7 / 43.8 / 37.5 %. Elsewhere lane 1 has
     27–65 % and lane 3 has 9–17 %. Reversed, S791's night flows (158 / 185 /
     79 veh/h) match the lanes that continue from S1948 (199 / 182 /
     80 veh/h).

   The data-quality report has no lane-order check, so a swapped station
   passes every rule it applies. The IRIS `field` values (26.5 / 25.0 /
   22.6 ft) follow the labels, so they cannot catch it either.
4. **Auxiliary and merge loops.** The compared stations have only
   mainline-category loops, one per lane, covering lanes 1..n. The
   Auxiliary loops are at S1063, which is not compared. Merge and passage
   loops sit on ramp r_nodes and are not read.
5. **Lane counts and positions.**
   - IRIS and the simulation agree on the lane count at all 7 compared
     stations.
   - S1064, S1065, S1947 and S790 are excluded because the simulation has a
     guessed fourth lane at each of them.
   - The IRIS ramp nodes fall in the same order as the simulated ramps.
     Most lie within 100 m of the simulated merge or diverge points. The
     exceptions are:
     - the 6th St left exit, 137 m away;
     - the T.H.52 entrance, 180 m away;
     - the Mounds Blvd exit, whose IRIS node is 399 m past the OSM gore
       (ONBOARDING §7).

     None of these puts a compared station on the other side of a ramp.
   - S1066 is the closest station to a simulated lane-count change. The
     guessed Hudson Rd acceleration lane ends 112 m upstream of it.
6. **Crossing rule.**
   - Each vehicle is counted once, in the lane of its first sample past the
     station. At 2 Hz and 25 m/s that sample is at most 12.5 m past the
     loop.
   - SUMO lane changes are instantaneous, so no vehicle straddles two lanes.
     The window matches (sim t 300–2,100 s = 07:05–07:35).
   - Difference from a real loop: a loop can miss a vehicle that changes
     lane over it, or count it twice. That is negligible at the 1-pp level.
   - Caveat: the slice starts empty at 07:00 and has a 300-s warm-up. At S97
     the first scored window is partly fill-up (1,596 veh/h, against
     3,300–3,900 veh/h afterwards). It is 1 of the 6 windows.
7. **Bad sensors.** Loop 3240 at S792 is excluded. S1948 lanes 1–2 are
   flagged `lane_imbalance`, and loop 5139 at S1063 is excluded. None of
   these stations is compared.
8. **Noise floor.**
   - Observed: a single day differs from the 5-day mean by 1.6 pp RMSE, so
     the standard error of the 5-day mean is about 0.7 pp.
   - Simulated: the two seeds differ from each other by 1.1–1.6 pp, so the
     2-seed mean carries about 0.8–1.1 pp.
   - Combined floor: about 1–1.3 pp.

## 4. The 6th Street left exit (network)

The netconvert compile of the reference network (identical to the grid's
`LANES.json`) maps lanes as follows at 9,295 m:

- `45782590` lane 0 → `-AddedOffRampEdge` lanes 0 and 1.
- Lane 1 → lane 2.
- Lane 2 → lane 3, which leads only to exit `42165869`.

Every other junction on the chain maps the lanes left-aligned, as an
added right lane should. Real-world evidence:

- OSM tags `45782590` with 3 lanes and
  `turn:lanes slight_left;through|none|none`: the left lane is an option
  lane.
- IRIS shows 3 lanes at `shift` 7 both before and after the exit, with the
  exit attached on the left. The mainline does not drop a lane there.

So the simulation turns an option lane into a left lane drop.
`microsim.split_audit` rated this split "ok" (docs/ONBOARDING_MNDOT.md §9)
because it checks only which side the exit lane is on.

The committed counts show the shift directly. At S792 (on `45782590`) the
simulated shares are 37 / 43 / 20 % (ref) and 39 / 49 / 12 % (chosen). The
left lane is emptied because it leads only to the exit. At S791 the shares
become 23 / 28 / 49 % and 21 / 29 / 50 %. S791's left lane (1,402 /
1,660 veh/h) is about S792's middle lane (1,331 / 1,730 veh/h).

The same left lane runs back through the five-lane section, where it is
lane 4 of `45608485`. That plausibly explains why the simulated leftmost
lane is empty at S1070 (17–18 % against 26.7 %, falling to 11 % at
keep-right 1.0). This part is a hypothesis: the Mounds exit's inflated share
pulls in the same direction (§5).

Compiling with `--ramps.unset 1001426896,45782590` (checked locally,
netconvert only) gives the OSM layout. Lanes 0, 1, 2 continue straight, lane
2 also feeds the exit, and the Mounds Blvd split patch is unaffected. That
compile has the variant hash 4f08fd8b6615.

## 5. Behaviour and inputs (what remains after §3–§4)

- **S791 and S97: deficit in the right lanes.** Using the corrected order at
  S791:

  | station | lane | observed veh/h | sim veh/h (ref / chosen) |
  |---|---|---|---|
  | S791 | right | 1,064 | 646 / 677 |
  | S791 | middle | 1,388 | 796 / 965 |
  | S791 | left | 1,656 | 1,402 / 1,660 |
  | S97 | right | 1,176 | 554 / 674 |
  | S97 | middle | 1,498 | 1,100 / 1,300 |
  | S97 | left | 1,817 | 1,667 / 1,790 |

  The left lanes run at about the observed flow. The shortfall sits in the
  right lanes. It combines three things:
  - the T.H.52 weave's under-discharge (ONBOARDING §10, VM O);
  - the demand balance, which puts the unexplained S792 → S791 residual on
    the 40648744 entrance downstream of S791;
  - at S791, the lane shift described in §4.

  Lane shares at these two stations cannot match while their flows are
  16–31 % short.
- **S1070: three causes.**
  - The simulated Mounds Blvd exit realises 30–33 % of the flow (sim S1948 →
    S792). The observed share is about 20 % (S1948 − S792′ = 931 veh/h,
    07:05–07:35), or less if S792′ is low. So in the simulation more
    mainline drivers leave the left lanes, and the station carries 9–11 %
    more flow.
  - Through entrants from the 2-lane T.H.61 NB ramp (inserted with
    `departLane="free"`) move to the ramp's inner lane before the merge. In
    the simulation, IRIS lane 2 carries 20–21 % and lane 1 5–7 %. The
    observed split is 13.9 % and 12.8 %.
  - The trap lane from §4.
- **S1066: early right moves.** Between S1065 and S1066 the simulated left
  lane loses 147–189 veh/h, while the observed left lane gains 76. With
  keep-right at 0, the only reason to move right is strategic. The
  plausible trigger is the White Bear C-D exit 1.3 km ahead and the T.H.61
  exit 2.3 km ahead, under `lc_strategic` 5. That value was set on I-24 to
  fix diverge stalls (docs/LESSONS.md row 10) and was never calibrated on
  I-94; it is outside Amendment 1.
- **S1068: right lane not refilled.** Across the White Bear split and the
  Ruth St entrance, the simulation takes the net loss from the right lane:
  −269 / −156 / +104 veh/h across right / middle / left. The observed change
  is spread evenly: −100 / −135 / −111. At (k 1, keep-right 1.0) the
  simulated changes match the observed ones (−100 / −120 / −102), but the
  same keep-right ruins S1066, S1069 and S1070.
- **Why keep-right makes it worse overall.** At k = 0, moving keep-right from
  0 to 1.0 changes the station RMSE as follows:

  | station | effect | RMSE pp (kr 0 → 1.0) |
  |---|---|---|
  | S1068 | improves | 6.9 → 1.0 |
  | S791 (corrected) | improves | 6.4 → 3.4 |
  | S97 | improves | 7.8 → 6.2 |
  | S1066 | worsens | 5.1 → 9.1 |
  | S1069 | worsens | 3.9 → 9.2 |
  | S1070 | worsens | 6.4 → 8.1 |

  The stations want opposite settings, so one global value cannot fit them.

## 6. Next step (cheapest first)

1. **Free, local, no runs.**
   - Record S791's lane order as reversed. Do this as a named reviewer
     correction, the same way 3240 was excluded, for example with a
     `--reverse-lane-order S791` input to `--build-observed-lanes`.
   - Add the neighbour-correlation lane-order test from §3.3 to the
     data-quality report.
   - Re-score the grid with `--analyze-only`. It reproduces §2: the choice
     stays (1, 0.1). This needs an owner decision, because it changes a
     target that was fixed in advance.
2. **Local, seconds.**
   - Add `45782590` to `--ramps.unset` in the I-94 scenarios.
   - Extend `split_audit` and `tests/test_microsim/test_microsim_osm_split_patch.py`
     to flag a through lane whose only successor is an exit where OSM marks
     an option lane.
   - This changes the scenario hash (as the §9 fixes did), so it is the
     owner's call.
3. **Cloud probe (decides §4).**
   - Runs: the 35-minute slice under the reference `xlsfg` recipe, 2
     networks (as built; `--ramps.unset 1001426896,45782590`) × 2 driver
     settings ((0, 0); (1, 0.1)) × 4 seeds (`spawn_seeds(42, 4)`, whose
     first two are the grid's seeds). That is 16 runs at 4 GB each.
   - Reader: the grid's own `readings_detectors`, which gives crossings by
     lane at every station plus S97's discharge, plus the collision count.
     This needs a small stage `p3_i94_leftexit_probe` (the grid script has
     no option to run a subset of pairs).
   - Time on an n2-standard-32 (`--procs 16`): one wave of about 80–95 s per
     run (from the grid's run times), plus about 10–15 min of boot and setup
     through the bucket launch. That is 15–20 min billed, about
     **$0.40–0.55** at $1.55/h; budget 30 min, $0.78.
   - Expected result if §4 is right: S791 and S97 simulated left shares fall
     toward 40 %, S1948 and S1070 lane 5 recover toward 27–32 %, and the
     corrected pooled RMSE falls below the 5.0 pp grid minimum.
   - If S1070's left lane does not recover, the Mounds exit share (a demand
     input, §5) is the next item.

   Re-running the full 25-pair grid on the fixed network instead (50 runs,
   4 waves, about 7 min of compute) would cost about $0.55–0.65. Changing
   `lc_strategic` on I-94 would need a protocol amendment and is not
   proposed here.

## Method (to reproduce)

- Grid re-scoring: per pair, sum the two seeds' `crossings_by_station` counts
  by SUMO lane. Take shares and compare them with
  `targets.lane_use.stations_compared`, reversing S791's observed list or
  dropping S791. Apply the Amendment-1 rule: the 1-pp band, then the
  smallest discharge error, then ties.
- Lane-order tests: the per-lane 30-s cache, all 9 weekdays.
  - Shares by band use only samples that are present.
  - Occupancy per vehicle is Σ(occupancy × 30 s) / Σ counts.
  - The correlations use 5-min windows where every lane reports and at
    least 60 vehicles pass.
- Network: `microsim.runner._build_network` on
  `calibrate_driver_grid.reference_raw(SPECS["i94"])`, plus the variant with
  `45782590` added to `--ramps.unset`. This is netconvert only; there is no
  simulation.
- The session scripts are not committed. Every input above is committed or
  in the cache.

## Fixes (2026-10-07)

Tools only. Nothing was simulated locally, no committed scenario, hash, target
or golden changed, and correcting S791's target is still the owner's decision
(§2).

1. **Lane-order check in the data-quality report** (`lane_order`,
   `calibration.data_quality.lane_order_check`).
   - Method (§3.3, made generic). Per-lane shares are formed over 5-minute
     windows (30-s data summed) with at least 60 vehicles, pooled over the dates.
     Each station is correlated with the nearest station on either side that has
     the same lane count, lane by lane (same-numbered lanes) and mirrored
     (lane k against n + 1 − k).
   - `reversed` needs all of: mirrored against every neighbour it can be read
     against; those neighbours anchored (two of them, or one that agrees with a
     third station); and both available supporting signatures pointing against
     the neighbours (free-flow occupancy per vehicle and light-traffic share,
     lane 1 against the last lane).
   - Its sensor-days become `suspect` with nothing masked, and the report lists
     the station in `lane_order.lanes_reversed`.
   - Any weaker mirrored reading is `uncertain`: flagged, never listed for a
     remap. A station with no comparable neighbour, or incomplete lane
     numbering, is `not_checkable`, which is not a pass. MnDOT lane numbers come
     from IRIS (`iris_lane_numbers`; the per-lane frame's lane ids are detector
     names).
   - Remapping is opt-in and recorded. `--build-observed-lanes` accepts
     `--reverse-lane-order S791` (a named reviewer correction, like the 3240
     exclusion) or `--remap-reversed-lanes` (the report's `lanes_reversed`).
     Without either it uses the IRIS labels and lists the stations the report
     flagged.
   - On the real 30-s cache: 9 weekdays, loop 3240 excluded, read from
     `data/mndot/cache`, nothing committed from `data/`.

     | span | S791 | rest |
     |---|---|---|
     | whole days, 00:00–24:00 | **`reversed`, `lanes_reversed: [S791]`**. Mirrored against S1069 (same-numbered −0.48, mirrored +0.55, 2,001 windows) and S790 (−0.82 / +0.86, 2,059 windows). Occupancy per vehicle 273 / 304 / 340 ms and light-traffic share 20 / 44 / 36 %: both rise toward lane 3, opposite to the neighbours (S790: 422 / 338 / 278 ms, 46 / 37 / 17 %). | 9 stations `ok`, including S790, S97 and S1069. S1070 and S1948 `inconclusive`: no window with every lane present at S1948. S1063 and S792 `not_checkable`: incomplete numbering. |
     | 05:30–09:30, the span stage p3 uses | **`uncertain`**, not remapped. Mirrored against S790 (−0.61 / +0.64, 431 windows). Against S1069 the morning reading is not clear (+0.12 / −0.06). The corridor's other three-lane stations single S791 out on occupancy per vehicle (269 / 301 / 334 ms), but no third station anchors the pair. | S790 `ok` |

     So the morning span flags S791 but cannot confirm it; whole days can.
     Run the check over whole days before acting on its verdict.
2. **Split audit: a lane OSM draws as continuing that is compiled exit-only**
   (`microsim.split_audit`).
   - When the side is right, the split is also checked lane by lane
     (`trapped_through_lanes`). Which lanes continue is read from
     `turn:lanes`. With no tag, it is read from an unchanged lane count, but
     only when the guessed lane sits on the side opposite the exit.
   - Where ramp guessing put its lane is read from the upstream connections
     (`added_lane_side`).
   - A trapped lane gets the verdict `through_lane_exit_only`. The remedy is
     `--ramps.unset <edge>` when ramp guessing widened the edge, and a
     connection patch otherwise. Onboarding applies the remedy by default.
   - On the committed extract without fixes there are 3 defects: the 6th
     Street exit (lane 3 of 4, trapped by `turn:lanes`) and the two §9
     defects. With the committed fixes, only the 6th Street defect remains.
   - The two right exits whose exit lane is a compiled auxiliary lane
     (`1014336806`, `998737536`) stay `ok`, because no OSM lane is trapped.
   - Synthetic fixture: `tests/fixtures/split_left_option.osm` (the 6th Street
     tag on a left exit). Test: `TestThroughLaneMadeExitOnly`.
   - Also updated: the layout audit flag, the API schema (`added_lane_side`,
     `trapped_lanes`, `trapped_evidence`, additive) and the CLI messages. The
     dashboard's verdict type is not.
3. **Network fix as new scenarios.** The committed files and their hashes are
   unchanged.

   | file | `--ramps.unset` | config hash |
   |---|---|---|
   | `scenarios/mndot_i94_wb_stpaul_weave_slice_netfix.yaml` | `1001426896,45782590` | `c0a7c9143a57` (slice `c8332755623a`) |
   | `scenarios/mndot_i94_wb_stpaul_weave_dc_netfix.yaml` | `1001426896,45782590` | `bd97bc3dcbeb` (dc `db9fbab5fc6e`) |

   - Nothing else differs except the name. Each file has a provenance header.
   - Checked with netconvert only, through the runner's network build, for both
     files:
     - `45782590` keeps 3 lanes (1,045 m, no `-AddedOffRampEdge` piece);
     - lanes 0–2 continue on `1000805867`, and lane 2 also feeds `42165869`, an
       option lane on the left;
     - the audit reports 0 defects;
     - the chain is 2.3 m shorter, from junction geometry.
   - `1001426896` must stay in the list: it is the 12th Street fix of
     ONBOARDING §9. Unsetting `45782590` alone brings back the guessed fourth
     lane on the left of `1001426896` (`added_lane_wrong_side`). netconvert also
     refuses a second `--ramps.unset` option, which is why the two edges share
     one list.
4. **Cloud probe, written, not launched.**
   - Stage `p5_i94_netfix_probe` of `scripts/gcp/pipeline_i24.sh`, script
     `scripts/i94_netfix_probe.py`.
   - Runs: the slice under `xlsfg`, as built (the grid's own hashes
     `1dc4729644dd` / `faa4ab5b219c`) against netfix (`892939b1c2fe` /
     `e0a582ceaa29`). Drivers (k 0, keep-right 0) and (k 1, keep-right 0.1). Seeds
     `spawn_seeds(42, 4)`, the first two being the grid's. 16 runs, about 4 GB each.
   - Scoring uses the grid's reader and scorer. Lane RMSE is computed four ways:
     own and common stations, each as committed and with S791 reversed. Also
     reported: S97 discharge, shares at every station, collisions, the §6.3
     expectations, and whether the as-built runs reproduce the committed grid
     readings.
   - Output: `artifacts/i94_netfix_probe.json`.
   - Time: one wave at 16 processes, about 3 min of compute; about 15–20 min
     billed with boot and setup.
   - Launch:

     ```
     scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p5 --bucket gs://<bucket>/p5 --self-delete \
       --via-bucket --data-set none --cap-min 45 --pipeline-args '--stages "p5_i94_netfix_probe"'
     ```

## Netfix probe — 2026-10-07 (stage p5, one n2-standard-16, us-central1-a; `artifacts/i94_netfix_probe.json`)

16 runs of the 35-minute slice, 4 seeds, zero collisions; the as-built runs reproduce the committed grid's
readings exactly at the two shared seeds.

| network | drivers | lane-share RMSE (as recorded) | with S791's lanes reversed | S97 discharge (target 4,490.5) |
|---|---|---|---|---|
| as built | k 0, keep-right 0 | 7.98 pp | 5.62 pp | 3,335 veh/h |
| as built | k 1, keep-right 0.1 | 8.22 pp | 5.60 pp | 3,028 veh/h |
| netfix | k 0, keep-right 0 | 7.86 pp | 5.33 pp | 3,336 veh/h |
| netfix | k 1, keep-right 0.1 | **7.26 pp** | **4.57 pp** | **3,760 veh/h** |

With the calibrated drivers the corrected 6th Street exit lowers the lane-share error by about 1 pp.
**Corrected 2026-10-07 (regression review; the first version claimed a ~730 veh/h discharge gain):** S97's
discharge by seed is as built [3,896, 3,632, 3,710, **876**] and netfix [3,878, 3,662, 3,680, 3,820] veh/h - at
three of four seeds the two networks are level (3,746 against 3,740), and the whole difference in the means comes
from one as-built run (seed 3747978530954135749) in which the calibrated drivers' slice **collapsed** (S97 876
veh/h, realised demand 0.857 against 0.966-0.987 in every other run). The map fix shows no resolved discharge
effect; whether it prevents such collapses cannot be read from one event, and the collapse itself is a
robustness finding about the as-built calibrated configuration. The fix stays justified as the correction of an
input defect (calibration, with the lane-share gain); it goes into the calibration-day scenarios
(docs/I94_CALIBRATION_DAYS.md).
