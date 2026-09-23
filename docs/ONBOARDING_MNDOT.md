# Onboarding a third corridor from public data: MnDOT I-94 westbound, east St. Paul

**Status (2026-09-23):** data, network, demand and fundamental diagram are
committed; the 20-seed baseline against the detector observations and the
strategy sweep are being run on a self-deleting cloud VM (§6 is filled in
from the committed artifacts when they land). Every number below traces to a
file named next to it.

The point of this exercise is the product path, not the corridor: a
traffic engineer with a bounding box and a detector export should get a
calibrated baseline and an FHWA-style report without touching corridor-specific
code. What needed hand work is listed in §7 as the next product items.

## 1. Data source

MnDOT's Regional Transportation Management Center publishes every Twin Cities
freeway loop detector at 30-s resolution, no registration:

| What | Where | Notes |
|---|---|---|
| Detector inventory | `https://data.dot.state.mn.us/iris_xml/metro_config.xml.gz` | 151 corridors, 15,162 nodes, 10,377 detectors; single-quoted XML attributes |
| 30-s samples | `https://data.dot.state.mn.us/mayfly/{counts,occupancy,speed}?district=metro&year=YYYY&date=YYYYMMDD&detector=ID` | JSON list of 2,880 values, `null` = missing, 404 = no data; ≈0.4 s per request |
| Date index | `/mayfly/dates?district=metro&year=2026` | |

The older `trafdat` archive path (documented in most papers) is behind a web
application firewall that rejects every client we tried, and the host answers
only over HTTPS. `calibration.loaders.mndot` wraps the API with a per
detector-day JSON cache (`data/mndot/cache/`, untracked, re-fetchable; 15 MB for
this corridor's nine days).

## 2. Corridor choice (scan, not opinion)

`scan_corridors` (kept in the session scratchpad; the ranking is reproduced
by `scripts/mndot_fetch.py` + the observations artifact) pulled one weekday
(2026-09-16) for twelve corridors (I-94, I-35W, I-494, I-394, T.H.100, I-35E,
both directions), aggregated to 5-min station flow/speed, and ranked every
9–16 km window by *coverage × congestion* (share of station-slots below
30 mph in the best 3-hour window). I-94 WB from **S1063 (west of the I-494
east junction) to S97 (I-35E east junction)** won on coverage (every station
≥ 0.91 valid, no dead station), a clean free-flow entry (upstream stations at
66–73 mph while Ruth St and downstream drop to 7–27 mph in the AM peak), no
managed lane, and an end *before* the I-94/I-35E overlap. I-35W NB north of
downtown and I-94 EB were close but have a dead station each and (I-35W) the
I-694 system interchange in the middle.

Selection record: `data/mndot/mndot_i94_wb_stpaul/selection.json`.

## 3. Steps, commands and wall-clock

| Step | Command | Time |
|---|---|---|
| Fetch nine weekdays (72 detectors × 3 series) into the cache | `scripts/mndot_fetch.py` (or the loader) | 106 s |
| Station table, tidy frame, observations artifact | `uv run --no-sync python scripts/mndot_fetch.py --corridor "I-94 WB" --from-station S1063 --to-station S97 --dates 20260901,…,20260917 --window-s 300 --t0 05:30 --duration-s 14400 --out data/mndot/mndot_i94_wb_stpaul --config data/mndot/config/metro_config.xml.gz` | 2 s from cache |
| Network from the bounding box, ramps, station positions | `uv run --no-sync python scripts/onboard_corridor.py --name mndot_i94_wb_stpaul --bbox 44.9425 -93.0990 44.9613 -92.9612 --bearing 265 --stations …/stations.csv --stations-out …/stations_x.csv --workdir runs/onboard/mndot_i94_wb_stpaul --out scenarios/mndot_i94_wb_stpaul.yaml --duration-s 14400 --netconvert-extra "--ramps.guess --ramps.ramp-length 250" --max-chain-m 11400` | ≈60 s (Overpass + netconvert) |
| Demand, ramps, boundary, population | `uv run --no-sync python scripts/corridor_demand.py --scenario scenarios/mndot_i94_wb_stpaul.yaml --observations …/observations.json --stations-x …/stations_x.csv --upstream S1063 --downstream S97 --idm-calibration artifacts/idm_i24_capacity.json --demand-out artifacts/demand_mndot_i94_wb_stpaul.json` | 3 s |
| Fundamental diagram from per-lane 1-min samples | `calibration.fd_fit.fit_triangular_fd` on the cache (see the artifact's `source`) | 40 s |

Result of the network step (`scripts/onboard_corridor.py` output): 11.82 km
chain of 32 OSM edges, lanes 3 → 4 → 3 → 4 → 3 → 5 → 3 → 4 → 3 → 5/6 along the
chain, 17 ramps discovered, 31 of 31 inventory nodes placed on the chain with
offsets ≤ 22 m (S1063 at x = 1,110 m, S97 at x = 11,027 m; the chain extends
1.1 km upstream and 0.8 km downstream of the observed span).

### From the dashboard

The three commands above (network, then demand, then a run and a report) are
one guided flow in the dashboard's **Onboard corridor** view, which closes
item 4 of §7: the same three inputs, no Python session.

1. **Name, bounding box, bearing, and the two boundary stations.** For this
   corridor: `mndot_i94_wb_stpaul`, `44.9425, -93.0990, 44.9613, -92.9612`,
   bearing 265, upstream `S1063`, downstream `S97`.
2. **The two CSVs.** The detector export on the tidy contract
   (`timestamp, station, flow_veh_h, occupancy_pct, speed_ms, lanes, kind`;
   a `column_map` maps a state DOT's own column names onto it) and the
   station inventory (`station,label,lat,lon,lanes,kind`).
   `scripts/mndot_fetch.py` writes both for MnDOT.
3. **Window, span start, duration and warm-up** default to 300 s, 06:00,
   14,400 s and 1,800 s — the span §4 analyses.

`POST /api/v1/corridors` (docs/CONTRACTS.md, "Corridor onboarding from the
dashboard") runs the same pipeline as a job: Overpass extract → chain, lanes,
ramps and station positions → observations artifact → demand
(`calibration.onboarding.calibrate_scenario`, the library the CLI of the
table above is now a thin wrapper over) → the scenario installed as a
`scenarios/<name>.yaml` preset. The view then shows what was found — chain
length, lane profile, the ramps and *where each ramp's flow came from*, the
stations it placed and the ones it refused to place, the demand peaks, the
carried residuals and the zeroed ramps — and offers **Run 20 seeds** followed
by **Report against observations**, which is the report of §6 scored against
the detector export that was uploaded.

A name that already exists as a preset is refused (409) rather than
overwritten, so re-onboarding a corridor is an explicit new name. Nothing in
the summary panel is a claim about the corridor's behaviour; the report is.

## 4. Observations (`data/mndot/mndot_i94_wb_stpaul/observations.json`)

Nine weekdays (Tue–Thu, 2026-09-01 … 09-17), 05:30–09:30 local, 48 five-minute
windows, mean over dates per window with the day-to-day standard deviation
kept under `spread`. All 14 mainline stations are 100 % valid over the span;
13 ramp detectors are present, one (the collector–distributor exit) has no
data.

Data quirks found by a mass-balance check between consecutive stations (the
reason the demand step does not trust ramp detectors alone):
- three on/off ramp "Merge"/"Exit" detectors read zero on every day (T.H.120
  exit, the second Hudson Rd entrance, McKnight Rd entrance);
- the Mounds Blvd "Exit" pair reads more than the mainline — it counts the
  collector–distributor split, not an exit;
- S1063 has two auxiliary lanes; the station total therefore includes the
  `Auxiliary` category (a simulated vehicle crossing that x is counted
  whatever lane it is in);
- two "T…" temporary loops at S790 never report; they are excluded from the
  validity denominator so the station keeps its three live lanes.

## 5. Demand (`artifacts/demand_mndot_i94_wb_stpaul.json`)

Entry inflow = S1063's cross-section count per 5-min window (peak 4,275 veh/h).
Ramp flows close the mainline balance bracket by bracket: live ramp detectors
fix the split between ramps of the same kind and the flow of the kind that is
not closing; the closing kind absorbs the remainder; a bracket with no ramp of
the needed kind carries its residual forward (listed under
`bracket_residuals` — the largest is the White Bear Ave entrance, which the
OSM discovery did not find as a link and whose +442 veh/h lands on the T.H.61
NB entrance one bracket later, and the collector–distributor re-entry before
Kellogg Blvd, +775 veh/h). Two ramps outside the observed span are zero (their
traffic is inside the nearest station count). The downstream boundary is S97's
observed mean speed per window. Warm-up 1,800 s; analysed span 06:00–09:30.

Driver population: `artifacts/idm_i24_capacity.json` — the I-24 MOTION fit
(capacity-scaled), because no Minnesota trajectory data exists to refit; this
is a stated model-transfer assumption and the first thing a Minnesota reviewer
should challenge.

Fundamental diagram (`artifacts/fd_mndot_i94_wb_stpaul.json`, 300k per-lane
1-min samples, density = occupancy / 7 m): v_f 26.9 m/s, w −2.8 m/s, ρ_jam
0.210 veh/m, ρ_c 0.0199 veh/m, q_max 0.535 veh/s. The same fit from 5-min
station means fails the plausibility check (flat congested branch); the raw
30-s cache is kept for this reason. ρ_c is the ALINEA target in the sweep.

### 5a. The first battery, and the merge fix (2026-09-23, 01:10–01:40)

The first 20-seed battery (VM round 1, scenario `adfb118b0015`) scored GEH < 5 on
17 % of link-hours, RMSPE 93 % and found no waves: the corridor never congested.
Reading the first seed's `meta.json` showed why — 34,367 vehicles planned,
25,051 departed; the entrances at Hudson Rd (both), McKnight Rd and the
downtown approach delivered 4–6 % of their demand. OSM tags the mainline as
three lanes straight through those merges, so SUMO joined each ramp to lane 0
at a plain priority junction where ramp vehicles yield to a 1,400 veh/h lane.
Lane-change parameters (assertiveness, strategic eagerness) changed nothing;
an acceleration lane did. The scenario now compiles with netconvert's ramp
guessing (`--ramps.guess --ramps.ramp-length 250`, recorded in
`network.netconvert_extra`), which adds 250 m acceleration and deceleration
lanes by splitting the highway edge; the importer maps the split pieces back
onto the scenario's edge ids. The chain also now ends on a 3-lane edge 440 m
past S97 (`--max-chain-m 11400`) so the downstream speed boundary can throttle
(the first chain ended on a 5-lane edge at the I-35E split, which no schedule
could restrict). In a 35-minute peak slice on the rebuilt network every
upstream entrance delivers 100 % of its demand and the two downtown-approach
entrances queue under the mainline's congestion (37 % and 52 % in the slice),
which is the observed condition there. Round 2 (§6) runs this scenario
(`21720f1e998c`).

## 6. Baseline batteries (two cloud rounds, 2026-09-23) — the corridor is not reproduced

Both rounds are recorded under `artifacts/mndot_rounds/`; no validated
baseline exists for this corridor and no sweep was run on it.

**Round 1** (scenario `adfb118b0015`, `round1_starved_ramps_validation.json`,
`round1_starved_ramps_report.md`; 20 seeds, wall 350–470 s per 4-hour
replicate): GEH < 5 on 17% of 840 pooled link-hours (FAIL), RMSPE
93% over 11,760 speed cells, no waves detected, throughput at x = 6.67 km
1,805 veh/h [1,798, 1,812] against an observed
mean near 2,700. Cause (§5a): OSM carries no acceleration lanes; four
entrances delivered 4–6 % of their demand and the corridor ran in free flow.

**Round 2** (scenario `21720f1e998c` — ramp guessing, chain ended before the
I-35E widening, I-24 lane-change settings; `round2_gridlock_record.json`;
20 seeds, wall 2,780 s per replicate): every seed gridlocked —
40.6% of planned vehicles departed (min 39.1%,
max 42.1%); the one seed scored before the VM was stopped
has RMSPE 90% and no passing link-hour, with simulated mean speeds
below 2 m/s at every station. Departure fractions by entrance (mean over
seeds): Hudson Rd 0.49 and 0.67, McKnight Rd 0.99, Ruth St 1.00, T.H.61 NB
0.18, the downtown approach 0.25 and T.H.52 0.41. The three large entrances
near the downtown approach (T.H.61 NB ≈ 2,000 veh/h, the collector–distributor
re-entry ≈ 1,700, T.H.52 ≈ 1,400) merge into a mainline already near 4,300
veh/h on three lanes; SUMO's lane-change merge locks there, the queue grows
for three hours and fills the corridor. A 35-minute peak slice reproduces the
onset locally (speeds fall to 1–7 m/s from S1070 downstream).

This is the flagship's merge finding again (docs/I24_VALIDATION.md §0.11:
the I-24 replica discharges about 5,880 veh/h where the recording sustains
6,630) at a corridor whose peak sits at the discharge the model cannot reach,
so the deficit compounds instead of costing a few per cent. The engine's
gap-acceptance (`scripted`) merge could be applied only to the two entrances
whose acceleration lane dead-ends on a split piece; the locking entrances
attach to short edges whose added lane runs on into the next edge. What would
be needed next: a merge model that does not need a dead-ending lane (or a
network patch that ends the added lane), and a Minnesota driver population.

A last attempt the same night (03:00–03:40): the engine can now end an added
acceleration lane with a connection patch so the gap-acceptance (`scripted`)
merge applies to entrances on short attach edges; on the peak slice it took
four of seven entrances, and the three it refused — Ruth St (the added lane
feeds the White Bear Ave exit: a weave), T.H.61 NB (a 1,071 m added lane
that runs on as a through lane) and T.H.52 (the lane feeds both lanes of an
exit piece) — are exactly where the jam starts. Merge waits fell with a
stronger gap acceptance (7.2 → 4.4 s) but the downstream speeds did not move
(3.4, 1.0, 1.7 m/s at the last three stations). The next model is a weave
section, not a merge.

Cost of both rounds: about 2.2 hours of n2-standard-32, ≈ $3.3.

## 7. What needed hand work (product backlog)

1. The White Bear Ave entrance is not a `motorway_link` chain in OSM; ramp
   discovery should also accept lane-add merges tagged on the mainline way.
1b. Acceleration lanes are usually absent from OSM; the onboarding path
   should apply ramp guessing by default and print the lane profile beside the
   inventory's lane counts at every station so a mismatch is caught before a
   battery runs (this round caught it after one).
2. Collector–distributor roads: the discovery captures the split as an
   off-ramp and misses the re-entry; the balance step carries the residual.
   A C-D road should become a parallel edge chain with its own ramps.
3. Dead detectors are common (4 of 13 ramp detectors here); the loader flags
   them by mass balance, but a reviewer-facing "detector health" table
   belongs in the report.
4. The demand step is a script; the dashboard needs the same three inputs
   (bounding box + bearing, detector CSV, upstream/downstream station) as a
   guided flow.
