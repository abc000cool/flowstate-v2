# Onboarding a third corridor from public data: MnDOT I-94 westbound, east St. Paul

**Status (2026-09-23, end of the block):** data, network, demand and
fundamental diagram are committed and the onboarding path works end to end;
the corridor itself is **not reproduced** — two 20-seed cloud batteries
failed for the reasons in §5a and §6, and no sweep was run on it. Numbers
trace to a file named next to them; figures marked as session records are
not committed.

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
| 30-s samples | `https://data.dot.state.mn.us/mayfly/{counts,occupancy,speed}?district=metro&year=YYYY&date=YYYYMMDD&detector=ID` | JSON list of 2,880 values, `null` = missing, 404 = no data; about 0.4 s per request as timed in the session |
| Date index | `/mayfly/dates?district=metro&year=2026` | |

The older `trafdat` archive path (documented in most papers) is behind a web
application firewall that rejects every client we tried, and the host answers
only over HTTPS. `calibration.loaders.mndot` wraps the API with a per
detector-day JSON cache (`data/mndot/cache/`, untracked, re-fetchable; about 15 MB for
this corridor's nine days as measured in the session).

## 2. Corridor choice (scan, not opinion)

*The scan below is a session record: `selection.json` commits only the
chosen corridor and its bounds, not the ranking or the per-corridor coverage
and speed figures quoted here. The committed nine-day profile agrees on the
free-flow entry (65.9–72.1 mph upstream) and on the queue from Ruth St
downstream (minima 10.9–30.5 mph).*

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

*Wall-clock figures were measured in the session and are not committed.*

| Step | Command | Time |
|---|---|---|
| Fetch nine weekdays (72 detectors × 3 series) into the cache | `scripts/mndot_fetch.py` (or the loader) | 106 s |
| Station table, tidy frame, observations artifact | `uv run --no-sync python scripts/mndot_fetch.py --corridor "I-94 WB" --from-station S1063 --to-station S97 --dates 20260901,20260902,20260903,20260908,20260909,20260910,20260915,20260916,20260917 --window-s 300 --t0 05:30 --duration-s 14400 --out data/mndot/mndot_i94_wb_stpaul --config data/mndot/config/metro_config.xml.gz --cache-dir data/mndot/cache --wave-context` (the first run fetches from the API; later runs read the cache) | 2 s from cache |
| Network from the bounding box, ramps, station positions | `uv run --no-sync python scripts/onboard_corridor.py --name mndot_i94_wb_stpaul --bbox 44.9425 -93.0990 44.9613 -92.9612 --bearing 265 --stations …/stations.csv --stations-out …/stations_x.csv --workdir runs/onboard/mndot_i94_wb_stpaul --out scenarios/mndot_i94_wb_stpaul.yaml --duration-s 14400 --netconvert-extra "--ramps.guess --ramps.ramp-length 250" --max-chain-m 11400` | ≈60 s (Overpass + netconvert) |
| Demand, ramps, boundary, population | `uv run --no-sync python scripts/corridor_demand.py --scenario scenarios/mndot_i94_wb_stpaul.yaml --observations …/observations.json --stations-x …/stations_x.csv --upstream S1063 --downstream S97 --idm-calibration artifacts/idm_i24_capacity.json --demand-out artifacts/demand_mndot_i94_wb_stpaul.json` | 3 s |
| Fundamental diagram from per-lane 1-min samples | no script yet: the fit was made in a session with `calibration.fd_fit.fit_triangular_fd` on per-lane 1-min samples built from the cache; the artifact's `source` and `notes` record the inputs, subsample and seed (backlog item 5 in §7) | 40 s |

Result of the network step as committed (`scenarios/mndot_i94_wb_stpaul.yaml`,
`data/mndot/mndot_i94_wb_stpaul/stations_x.csv`, `observations.json`): an
11.48 km chain of 29 corridor edges (the first build before the chain cap and
ramp guessing was 11.82 km over 32 edges — a session figure), 16 ramps in the
scenario (17 ramp nodes in the inventory), 31 of 31 inventory nodes placed on
the chain with offsets up to 22.1 m (S1063 at x = 1,089 m, S97 at
x = 11,072 m; the chain extends 1.1 km upstream and 407 m downstream of the
observed span — the scenario's `exit_buffer_m`).

*Split audit (2026-09-24).* The network step's inventory now ends with a
`splits` block: every exit leaving the chain, the side OSM draws it on (the
link's lateral offsets from the continuing mainline and the `turn:lanes` tag)
against the lanes the compiled network feeds it from, with a verdict
(`microsim.split_audit`; docs/CONTRACTS.md "Split audit"). Two flags on
`scripts/onboard_corridor.py` mirror the lane check's: `--fail-on-split-defect`
exits 4 on a `wrong_side` / `added_lane_wrong_side` verdict, and
`--write-split-patch data/osm/<name>.splits.con.xml` writes the connection
patch for the `wrong_side` splits, adds it and `--ramps.unset` for the
`added_lane_wrong_side` ones to the scenario, re-imports the network with
the fixes and prints the audit again before the YAML is written. Run on the
command of the table above (no `--ramps.unset`, no patch) the audit finds the
two §9 defects and the fixes it writes leave none; the committed scenario
already carries them.

*Defaults (2026-09-24, later the same day).* Both are now the default, so the
table's command no longer needs `--netconvert-extra` or a patch flag: every
onboarding (`corridor_from_bbox`, the CLI, `POST /corridors`) compiles with
`--ramps.guess --ramps.ramp-length 250` (`microsim.scenarios.RAMP_GUESSING_OPTIONS`;
options given in `--netconvert-extra` are kept and never duplicated) and,
when the audit finds a `wrong_side` / `added_lane_wrong_side` exit, applies
the fixes — the connection patch written beside the extract as
`<extract stem>.splits.con.xml` (the committed
`data/osm/mndot_i94_wb_stpaul.splits.con.xml` follows that pattern;
`--write-split-patch PATH` chooses another place), `--ramps.unset` added —
re-imports and audits again. The inventory prints the audit the fixes were
derived from (`splits before fixes`), the audit the scenario compiles
(`splits`) and one line saying what was applied:
`applied   ramp guessing on; split fixes: 2 applied, 0 remaining`. Opt-outs:
`--no-ramp-guessing`, `--no-split-fixes` (`ramp_guessing=false`,
`split_fixes=false` on the API form); `--fail-on-split-defect` judges the
final audit. Re-onboarding this extract under the defaults: 8 exits audited,
2 defects before the fixes, 0 after, the generated patch's connection lines
equal to the committed file's (`tests/test_microsim/test_microsim_split_audit.py`).

*Re-onboarding over the committed scenario (2026-09-24, block 3).* Running the
network step with `--out scenarios/mndot_i94_wb_stpaul.yaml` again now keeps
the file's fleet, sim, seed, replicates, `fd_calibration` and `macro` blocks
and rebuilds only the network (the report says `fleet block kept from … (…
lc_strategic 5.0, lc_strategic_ramp 1.0, lc_keep_right 0.0)`); `--fresh-fleet`
asks for the builder's defaults instead, and a file that does not parse is
refused with exit 2 rather than overwritten — the §11 reset cannot recur
silently, and `scripts/corridor_demand.py` now records the fleet block in the
demand artifact's `fleet_settings` and its summary (docs/CONTRACTS.md,
"Re-onboarding keeps the fleet block").

### From the dashboard

The three commands above (network, then demand, then a run and a report) are
one guided flow in the dashboard's **Onboard corridor** view, which closes
item 4 of §7: the same three inputs, no Python session.

1. **Name, bounding box, bearing, and the two boundary stations.** For this
   corridor: a *new* name such as `mndot_i94_wb_trial` (the service answers
   409 for a name whose preset or extract already exists, and
   `mndot_i94_wb_stpaul` ships with the repository), the box
   `44.9425, -93.0990, 44.9613, -92.9612`, bearing 265, upstream `S1063`,
   downstream `S97`.
2. **The two CSVs.** The detector export on the tidy contract
   (`timestamp, station, flow_veh_h, occupancy_pct, speed_ms, lanes, kind`;
   a `column_map` maps a state DOT's own column names onto it) and the
   station inventory (`station,label,lat,lon,lanes,kind`).
   `scripts/mndot_fetch.py` writes both for MnDOT.
3. **Window, span start, duration and warm-up.** Enter 300 s, **05:30**,
   14,400 s and 1,800 s to match the committed artifact (`t0_local` 05:30,
   48 windows to 09:30; the warm-up makes the analysed span 06:00–09:30). The
   form's default start of 06:00 would ask for a span the upload does not
   cover.

`POST /api/v1/corridors` (docs/CONTRACTS.md, "Corridor onboarding from the
dashboard") runs the same pipeline as a job: Overpass extract → chain, lanes,
ramps and station positions → observations artifact → demand
(`calibration.onboarding.calibrate_scenario`, the library the CLI of the
table above is now a thin wrapper over) → the scenario installed as a
`scenarios/<name>.yaml` preset. The view then shows what was found — chain
length, lane profile, the ramps and *where each ramp's flow came from*, the
stations it placed and the ones it refused to place, the demand peaks, the
carried residuals and the zeroed ramps — and offers **Run 20 seeds** followed
by **Report against observations**, which scores the finished run's GEH and
RMSPE against the detector export that was uploaded.

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
- three on/off ramp "Merge"/"Exit" detectors read zero, or as good as zero,
  on every day (T.H.120 exit and McKnight Rd entrance at 0.0 veh/h; the second
  Hudson Rd entrance at 0.7 veh/h mean);
- the Mounds Blvd "Exit" pair reads more than the mainline — it counts the
  collector–distributor split, not an exit; *corrected 2026-09-24: it counts
  nothing — its lane-1 loop 3241 is a chattering loop (3,600 veh/h at
  00–04, no correlation with any station), see §7 item 2;*
- S792's lane-3 loop 3240 is degraded (a quarter of the lane's flow, §7
  item 2); the loader can now exclude such a loop by name
  (`mndot_fetch.py --exclude-detectors`) so the station is scaled as a
  two-lane count, but the committed observations were fetched before that
  and still carry it;
- S1063 has two auxiliary lanes; the station total therefore includes the
  `Auxiliary` category (a simulated vehicle crossing that x is counted
  whatever lane it is in);
- two "T…" temporary loops at S790 never report; they are excluded from the
  validity denominator so the station keeps its three live lanes.

### 4a. The corridor's own wave speed (context, not a criterion)

`scripts/mndot_fetch.py --wave-context` estimates the *observed* backward wave
speed from the raw 30-second speed series — for each adjacent station pair, the
lag of the normalised cross-correlation peak between the detrended series over
the downstream station's congested episodes, divided into the spacing
(`calibration.waves_observed`, docs/CONTRACTS.md "Observed backward wave speed
as report context"). It is stored under the artifact's
`context.detector_wave_speed` and printed by the validation report as one line
of the **Observed data** block, so a reviewer can put the corridor's real wave
speed next to the 14–22 km/h band the *simulated* one is scored against. No
criterion is evaluated from it.

Nine weekdays, 05:30–09:30, 13 adjacent pairs:

| downstream ← upstream | Δx [m] | lag [s] | km/h | peak r | used |
|---|---|---|---|---|---|
| S1064 ← S1063 | 1212.8 | — | — | — | no: fewer than 3 congested episodes |
| S1065 ← S1064 | 1150.5 | — | — | — | no: fewer than 3 congested episodes |
| S1066 ← S1065 | 584.2 | — | — | 0.223 | no: peak lag not positive |
| S1067 ← S1066 | 907.5 | — | — | 0.168 | no: r below 0.3 |
| S1068 ← S1067 | 607.5 | — | — | 0.153 | no: r below 0.3 |
| S1947 ← S1068 | 491.0 | 94.6 | 18.7 | 0.327 | yes |
| S1069 ← S1947 | 603.7 | — | — | 0.255 | no: r below 0.3 |
| S1070 ← S1069 | 979.1 | 145.1 | 24.3 | 0.325 | yes |
| S1948 ← S1070 | 725.1 | — | — | 0.156 | no: r below 0.3 |
| S792 ← S1948 | 565.3 | 111.4 | 18.3 | 0.357 | yes |
| S791 ← S792 | 742.6 | 102.4 | 26.1 | 0.484 | yes |
| S790 ← S791 | 399.7 | 80.7 | 17.8 | 0.504 | yes |
| S97 ← S790 | 939.9 | 141.9 | 23.8 | 0.315 | yes |

**Median 21.3 km/h (IQR 18.4–24.2) from 6 of 13 pairs; leaving any one of the
nine dates out moves that median between 18.4 and 21.6 km/h, and on two of the
nine subsets only five pairs survive at all.** The median is a median over six
numbers, so the range it moves in belongs beside it wherever it is quoted: a
21.3 with a 3 km/h leave-one-out spread is not a 21.3 to one decimal. Read that
way, the corridor's recurrent waves run at the fast edge of the band the model
is asked to reproduce, and the two upstream pairs never congest at all (§2: the
entry is free-flowing). What the number is worth:

- **Leave-one-date-out.** `calibration.waves_observed.leave_one_date_out`
  re-runs the whole estimate — pair rejection included — once per omitted date;
  `--wave-context` prints the table and stores `loo_median_min_kmh`,
  `loo_median_max_kmh` and `loo_pairs_min` in the artifact, and the report
  prints the range in the same line as the median. Omitting 2026-09-03 or
  09-16 leaves five pairs and a median near 18.4 km/h; omitting 09-01 leaves
  seven pairs and 18.7; the other six subsets sit at 20.9–21.6. The estimate
  is one corridor's nine mornings, not a population statistic, and the spread
  is a sensitivity, not a confidence interval (the subsets share eight ninths
  of their data).
- **Resolution.** The lag is measured on 30-second bins, so a 0.5 km pair
  resolves the speed to roughly ±15% and a 1 km pair to ±8%; the parabolic
  sub-bin refinement helps but does not remove that. Peaks below two bins are
  rejected outright — at one bin the half-bin clamp alone spans a factor of
  three — and the ±10–15% figure holds from three bins up. Every pair used
  here peaks at three bins or more. The IQR is the honest spread, not a
  confidence interval.
- **Detrending matters.** Without removing the ~20-minute envelope, three
  pairs peak at a zero or negative lag (the whole corridor's peak turns on
  almost together) and the median rises to 22.7 km/h with 8 pairs used. Across
  detrending widths of 600–3600 s the median stays between 18.1 and 21.4 km/h
  and the used-pair count between 3 and 9 — the estimate is stable, the pair
  selection is not. The moving mean is only subtracted where its window is
  two-sided: within half a window of a date's first or last sample there is no
  residual, because a one-sided mean would leave the morning's own trend in it.
- **Dates are not mixed.** The nine mornings are correlated as one series
  separated by an hour of NaN, and no correlation pair or detrending window may
  cross that separator, so no lag can align one morning against the next
  whatever `max_lag_s` is set to.
- **Spacing.** The estimate uses the IRIS inventory spacing; the SUMO chain's
  projected spacings differ by ≤ 3% (worst case the 400 m S791→S790 pair),
  well inside the lag quantisation.

## 5. Demand (`artifacts/demand_mndot_i94_wb_stpaul.json`)

Entry inflow = S1063's cross-section count per 5-min window (peak 4,275 veh/h).
Ramp flows are set so that, between two consecutive stations, the entrances
and exits account for the observed change in flow: where a ramp detector is
live its reading is used (and fixes the shares between ramps of the same
kind), and the ramps without a usable detector take whatever remains of the
observed change; a bracket with no ramp of the needed kind carries its
residual forward (listed under
`bracket_residuals` — the largest is the collector–distributor re-entry
before Kellogg Blvd, +775 veh/h (*2026-09-24: not a re-entry — S792's
lane-3 loop under-counts; §7 item 2*); the other is the White Bear Ave entrance,
which the OSM discovery did not find as a link and whose +442 veh/h lands on
the T.H.61 NB entrance one bracket later; *2026-09-24: the White Bear
residual is closed by C-D pairing and the Kellogg reading was wrong — see
§7 items 1 and 2*). One ramp outside the observed span
is zero (its traffic is inside the nearest station count). The downstream boundary is S97's
observed mean speed per window. Warm-up 1,800 s; analysed span 06:00–09:30.

Driver population: `artifacts/idm_i24_capacity.json` — the I-24 MOTION fit
(capacity-scaled), because no Minnesota trajectory data exists to refit; this
is a stated model-transfer assumption and the first thing a Minnesota reviewer
should challenge.

Fundamental diagram (`artifacts/fd_mndot_i94_wb_stpaul.json`, 300k per-lane
1-min samples, density = occupancy / 7 m): v_f 26.9 m/s, w −2.8 m/s, ρ_jam
0.210 veh/m, ρ_c 0.0199 veh/m, q_max 0.536 veh/s (the fit itself ran on a
seeded 50,000-row subsample of those samples, with bootstrap CIs). The same fit from 5-min
station means fails the plausibility check (flat congested branch); the raw
30-s cache is kept for this reason. ρ_c is the ALINEA target in the sweep.

### 5a. The first battery, and the merge fix (2026-09-23, 01:10–01:40)

The first 20-seed battery (VM round 1, scenario `adfb118b0015`) scored GEH < 5 on
17 % of link-hours, RMSPE 93 % and found no waves: the corridor never congested.
Reading the first seed's `meta.json` on the VM showed why — 34,367 vehicles
planned, 25,051 departed; the entrances at Hudson Rd (both), McKnight Rd and
the downtown approach delivered 4–6 % of their demand (a session reading; the
round-1 run metadata was not kept, the free-flow speeds of that seed are
committed in `artifacts/mndot_rounds/round1_first_seed_station_speeds.json`). OSM tags the mainline as
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
could restrict). In a 35-minute peak slice on the rebuilt network (a local session run, not
committed) every upstream entrance delivered 100 % of its demand and the two
downtown-approach entrances queued under the mainline's congestion (37 % and
52 % in the slice), which is the observed condition there. The 20-seed battery on this network
then gridlocked (§6). Round 2 (§6) runs this scenario
(`21720f1e998c`).

## 6. Baseline batteries (two cloud rounds, 2026-09-23) — the corridor is not reproduced

Both rounds are recorded under `artifacts/mndot_rounds/`; no validated
baseline exists for this corridor and no sweep was run on it.

**Round 1** (scenario `adfb118b0015`, `round1_starved_ramps_validation.json`,
`round1_starved_ramps_report.md`; 20 seeds, wall 344–470 s per 4-hour
replicate): GEH < 5 on 17% of 840 pooled link-hours (FAIL), RMSPE
93% over 11,760 speed cells, no waves detected, throughput at the span's mid-point
1,805 veh/h [1,798, 1,812] against observed station means of 2,900–3,000 veh/h at the nearest station
(S1069, `observations.json`). Cause (§5a): OSM carries no acceleration lanes; four
entrances delivered 4–6 % of their demand and the corridor ran in free flow.

**Round 2** (scenario `21720f1e998c` — ramp guessing, chain ended before the
I-35E widening, I-24 lane-change settings; `round2_gridlock_record.json`;
20 seeds, wall 2,780 s per replicate): every seed gridlocked —
40.6% of planned vehicles departed (min 39.1%,
max 42.1%); the one seed scored before the VM was stopped
has RMSPE 90% and no passing link-hour, with simulated mean speeds
below 2 m/s at every station (`round2_first_seed_station_speeds.json`). Departure fractions by entrance (mean over
seeds): Hudson Rd 0.49 and 0.67, McKnight Rd 0.99, Ruth St 1.00, T.H.61 NB
0.18, the downtown approach 0.25 and T.H.52 0.41. The three large entrances
near the downtown approach (peak demand in `demand_mndot_i94_wb_stpaul.json`:
T.H.61 NB 2,196 veh/h, the collector–distributor re-entry 1,696, T.H.52 1,419)
merge into a mainline whose observed peaks on the three-lane sections there are
4,100–5,200 veh/h; SUMO's lane-change merge locks there, the queue grows
for three hours and fills the corridor. A 35-minute peak slice reproduced the
onset locally in the session (speeds fell to 1–7 m/s from S1070 downstream;
not committed).

This is the flagship's merge finding again (docs/I24_VALIDATION.md §0.11:
the I-24 replica discharges about 5,880 veh/h where the recording sustains
6,630) at a corridor whose peak sits at the discharge the model cannot reach,
so the deficit compounds instead of costing a few per cent. The engine's
gap-acceptance (`scripted`) merge could be applied only to the two entrances
whose acceleration lane dead-ends on a split piece; the locking entrances
attach to short edges whose added lane runs on into the next edge. What would
be needed next: a merge model that does not need a dead-ending lane (or a
network patch that ends the added lane), and a Minnesota driver population.

A last attempt the same night (03:00–03:40; the slice figures below are
session records, not committed): the engine can now end an added
acceleration lane with a connection patch so the gap-acceptance (`scripted`)
merge applies to entrances on short attach edges; on the peak slice it took
four of seven entrances, and the three it refused — Ruth St (the added lane
feeds the White Bear Ave exit: a weave), T.H.61 NB (a 1,071 m added lane
that runs on as a through lane) and T.H.52 (the lane feeds both lanes of an
exit piece) — are exactly where the jam starts. Merge waits fell with a
stronger gap acceptance (7.2 → 4.4 s) but the downstream speeds did not move
(3.4, 1.0, 1.7 m/s at the last three stations). What is missing is a model
of a weaving section — an entrance lane that also serves an exit over a short
distance — not another merge model; T.H.61 NB is a different case again (a
two-lane entrance adding lanes before a five-lane station: a lane addition).
The design note is docs/WEAVE_MODEL_PLAN.md.

Cost of both rounds: an estimate from the machine-hours, about 2.2 hours of
n2-standard-32, ≈ $3.3.

## 7. What needed hand work (product backlog)

1. The White Bear Ave entrance is not a `motorway_link` chain in OSM; ramp
   discovery should also accept lane-add merges tagged on the mainline way.
   *2026-09-24, diagnosed from the extract and the live OSM database:* the
   entrance is not a merge on the mainline at all. The exit link 18208090
   continues as 991112953 and 745524608 back onto the mainline (edge
   998737536) 590 m downstream of the split on the raw net (613 m on the
   ramp-guessed build) — a collector–distributor road — and White Bear Avenue
   North (`highway=secondary`, two-way) meets that road at grade at node
   6978586133; its turns are the entrance and the exit. The extract carries
   motorway and motorway_link ways only (`networks.OVERPASS_HIGHWAY_REGEX`),
   so the arterial is absent and no non-link way touches any mainline node;
   the entrance is therefore found as the C-D re-entry (item 2). Done:
   `microsim.geo.ramps_for_chain` also accepts a drivable non-link way
   (`geo.DRIVABLE_TYPES`) that ends on a mainline node within 35° of the
   mainline's heading and does not continue past the node, recorded as
   `discovery="lane_add"` when the mainline gains a lane there and
   `"shallow_join"` otherwise (a crossing road through a shared node, bridge
   or at grade, fails the heading or the pass-through test); the reason is
   printed in the inventory (`[motorway_link]`, `[lane_add]`, …). Tested on
   synthetic fixtures (`tests/test_microsim/test_microsim_geo_ramps.py`); it
   has no instance on this extract. Still open: an Overpass filter that also
   fetches the arterial classes near the mainline, so at-grade junctions on a
   C-D road are inventoried; ramp guessing by default (1b).
1b. Acceleration lanes are usually absent from OSM. Done the same night: the
   onboarding step compiles with ramp guessing when `--netconvert-extra` asks
   for it and prints the lane profile beside the inventory's lane counts at
   every station (`--fail-on-lane-mismatch` turns a disagreement into a
   failure), and since 2026-09-24 audits the side every exit is compiled on
   beside it (`--fail-on-split-defect`, `--write-split-patch`; §3, §9).
   *2026-09-24, done:* ramp guessing (`--ramps.guess --ramps.ramp-length 250`)
   and the split fixes are the defaults of every onboarding path;
   `--no-ramp-guessing` / `--no-split-fixes` (CLI) and `ramp_guessing` /
   `split_fixes` (API form) opt out; the inventory's `applied` line says
   which ran (§3, "Defaults").
2. Collector–distributor roads: the discovery captures the split as an
   off-ramp and misses the re-entry; the balance step carries the residual.
   A C-D road should become a parallel edge chain with its own ramps.
   *2026-09-24, done for the White Bear Ave road:* before a link leaving the
   chain is taken as an exit, `ramps_for_chain` searches the links for a way
   back onto the chain within 3 km. The split becomes an `off` candidate with
   `cd_road`, `rejoin_edge` and `rejoin_x_m`, the re-entry an `on` candidate
   with the same `cd_pair` (both carried into `RampSpec.cd_road`/`cd_pair`);
   ramps attaching to the C-D road itself are inventoried with
   `attach_via_cd` and not simulated (a `RampSpec` attaches to a corridor
   edge). The balance step (`calibration.onboarding`) pairs the ends: a
   re-entry without a live detector first takes back what its split sent out
   in the window, then the bracket's remainder, and the artifact lists the
   pair under `cd_pairs` (out, back, net = the road's own exchange). Re-run on
   the committed extract with the §3 options into a scratch directory (a
   session record: the figures that follow are in no committed artifact; the
   "was" values are the committed demand artifact's): 17
   ramps (was 16); the split, matched to rnd_88817, sends out 374 veh/h on
   average (detector_scaled), the re-entry returns 442 veh/h (conservation),
   net +68 veh/h; the S1068→S1947 residual (+442 veh/h) is closed, 5 brackets
   carry residuals (was 6). Because that +442 used to be carried forward, the
   next brackets now show their own size: S1947→S1069 +9 (was +338),
   S1069→S1070 −317 (was −131), S1070→S1948 −262 (was −76). *Not a C-D road,
   contrary to §5:* the Mounds Blvd split (18207912) fans out to North Mounds
   Boulevard (`highway=primary`) at grade at two nodes, and the entrance
   before Kittson St (40648744, x = 9.94 km) is fed from North Mounds
   Boulevard and East 6th Street downstream of S791 — no way joins the
   mainline between S792 and S791 in OSM (checked against the live
   database), so the +775 veh/h residual there has no map explanation; both
   stations report 3 lanes, and rnd_87205 at the split counts 4,219 veh/h,
   more than the 4,162 veh/h arriving at S1948, so the split detector or S792
   is the question. Not done: the committed scenario and demand artifact are
   unchanged (a re-onboard rewrites the scenario and needs a battery to
   validate); a C-D road as a parallel edge chain with its own simulated
   ramps.
   *2026-09-24, the detector question answered from the IRIS inventory
   (`metro_config.xml` of 2026-09-22) and the 9-weekday 30-s cache.* What
   rnd_87205 is: an `Exit` node of 2 lanes (the mainline's `shift` goes 9 → 7
   as the lanes go 5 → 3) with one `Exit`-category loop per lane, 3241
   (lane 1, "94/MoundsWX1") and 3242 (lane 2, "94/MoundsWX2", field 12 ft),
   both on controller ctl_60415, whose location is "I-94 EB @ Mounds Blvd
   (243.5)" and which also serves the EB merge loop 3194. The loader sums the
   two lanes once (`aggregate_day`), so the 4,219 veh/h is not a double
   count: it is 3241 alone. 3241 is a chattering loop, not a mis-categorised
   mainline lane — it reads 2,400–4,600 veh/h at 00–04 when the whole
   5-lane mainline carries 250–900, its 5-min series has no correlation with
   any station (r = −0.07 against S1948 over 2,355 windows, r ≤ 0.13 against
   every other) and it reports no speed; 3242 reads 115 veh/h at the peak and
   does follow S1948's two exit lanes (r = 0.74), but is one lane of a
   two-lane exit. The balance step's 90 %-share rule rejects the node (and it
   is unmatched anyway: the IRIS node sits 399 m from the OSM gore, past the
   350 m radius), so rnd_87205 never touched the demand; the +775 veh/h is
   not its doing. Where the +775 is: the inventory chain from S792 (rnd_87207)
   to S791 (rnd_87213) holds rnd_676, rnd_91430, rnd_91428 ("T…" loops, no
   data) and the *left* C-D exit rnd_87209 — no Entrance node, as OSM has no
   joining way — so the count can only fall between the two stations, yet it
   rises by 775 veh/h. Lane by lane (peak 05:30–09:30, mean of 9 weekdays):

   | cross-section | lanes | veh/h (per lane) | reading |
   |---|---|---|---|
   | S1070 | 5 | 4,107 | |
   | S1948 | 5 | 4,145 (211 / 323 / 1,024 / 1,192 / 1,394) | lanes 1–2 are the exit lanes (534); lanes 3–5 continue (3,610) |
   | rnd_87205 lane 1, loop 3241 | | 4,104 | chatter (3,592 at 00–04) |
   | rnd_87205 lane 2, loop 3242 | | 115 | follows S1948 lanes 1–2 |
   | S792 | 3 | 2,925 (1,325 / 983 / 617) | lane 3 (loop 3240): 617 at 14.3 % occupancy against 943 at 32.0 % one station down (S791 lane 3) and 1,394 at 26.6 % one station up (S1948 lane 5); at midday 245 veh/h at 1.6 % against 918 at 9.1 % and 1,138 at 6.6 %; 78 % of its 00–04 samples are null (IRIS no-hit auto-fail) while the station's other two loops report 100 % |
   | S791 | 3 | 3,699 (1,479 / 1,277 / 943) | = S1948 lanes 3–5 − 87 (r = 0.976 at 5-min) |
   | S790 | 3 | 4,221 | = S791 + 522; the Mounds Blvd entrance's passage loop 3244 (rnd_87211, metered) reads 552 |

   Loop 3240 is degraded: the leftmost lane cannot lose two thirds of its
   flow in 570 m with no exit on the left, and its occupancy falls with the
   count (missed vehicles, not short detections). The correction the
   inventory justifies is therefore at S792, not at the split: exclude 3240
   and let the loader's dead-lane rule scale the station's two reporting
   lanes by 3/2 — the same rule that already stands in for S790's "T…"
   lanes. Done: `station_frame(exclude_detectors=...)` /
   `mndot_fetch.py --exclude-detectors 3240 --exclude-reason "…"` (the
   names and reason are written under the observations' `source`, the scaled
   station-days beside them; a test on a fixture copied from these r_nodes,
   `tests/test_calibration/fixtures/mndot_metro_config_mounds.xml`). What the
   bracket becomes, from the cache (S792′ = lanes 1–2 × 3/2 = 3,460 veh/h at
   the peak; the committed artifact is unchanged, a re-fetch and a battery
   are the owner's call):

   | peak 05:30–09:30, veh/h | committed | 3240 excluded |
   |---|---|---|
   | Mounds Blvd exit (S1948 − S792) | 1,226 (30 % of arriving) | 701 (17 %) |
   | residual S792 → S791 | +775 | +238 |
   | Mounds/Kittson entrance (S790 − S791 + carried) | 1,299 | 524 (passage loop 3244: 552) |

   Over the analysed 06:00–09:30 span: 1,251 / +812 / 1,354 → 768 / +328 /
   542 (passage 571); at midday 10:00–14:00: 1,116 / +514 / 1,046 → 213 /
   −388 / 533 (577) — the negative midday remainder is what the C-D exit
   rnd_87209 then takes by conservation, which is the sign a left exit
   should have. The entrance's agreement with its own passage loop (524 vs
   552, 542 vs 571, 533 vs 577) is the independent check that the
   correction is right and not a fit to the balance; the +238 that remains
   at the peak is the dead-lane rule's assumption (lane 3 carries the mean
   of lanes 1–2) and stays a residual. rnd_87205 itself is left as it is
   (rejected by the share rule and unmatched); excluding 3241 alone would
   leave the node reading 115 veh/h from one lane, which is worse.
3. Dead detectors are common (4 of 13 ramp detectors here); the loader flags
   them by mass balance, but a reviewer-facing "detector health" table
   belongs in the report. *2026-09-24:* a degraded loop that still reports
   (S792's 3240, item 2) passes every automatic rule; the reviewer's
   exclusion by name (`--exclude-detectors`) is the honest tool until a
   lane-continuity check exists.
4. Done the same night: the dashboard's Onboard view takes the same three
   inputs (bounding box + bearing, detector CSV, upstream/downstream station)
   and installs the preset (§3, "From the dashboard").
5. The fundamental-diagram fit from the raw cache is not a script yet; the
   committed artifact records its inputs.

## 8. A Minnesota driver population by capacity scaling (2026-09-24) — target not reached

The corridor has no trajectory data, so no population can be fitted on it;
the honest alternative is the US-101 procedure (docs/US101_CALIBRATED.md,
`scripts/calibrate_capacity.py`): take the I-24 episode-fitted population
(`artifacts/idm_i24.json`, mean T 1.511 s) and scale its mean desired time
headway until a straight 3-lane road carries the corridor's own fitted
capacity — the fundamental diagram's `q_max` bootstrap lower bound,
1,907 veh/h/lane (`artifacts/fd_mndot_i94_wb_stpaul.json`). Pipeline stage
`mndot_population` on an n2-standard-32 (37 s wall-clock for the twelve runs
in parallel — the pipeline log, a session record; the artifact records 24–31 s
per run), two seeds per grid point,
saturating demand 2,400 veh/h/lane, throughput at 3.0 km of 4.0 km:

| T scale | mean T [s] | capacity [veh/h/lane], mean of 2 seeds |
|---|---|---|
| 1.00 | 1.511 | 1,591 |
| 0.95 | 1.436 | 1,626 |
| 0.90 | 1.360 | 1,670 |
| 0.85 | 1.284 | 1,656 |
| 0.80 | 1.209 | 1,709 |
| 0.75 | 1.133 | 1,703 |

The curve flattens near 1,700 veh/h/lane from T × 0.80 on, 10 % short of the
target; headway scaling alone does not reach it, and the script took the best
grid point (T × 0.80) and says so in the artifact's notes
(`artifacts/idm_mndot_i94_wb_stpaul_capacity.json`, sidecar
`…capacity.calibration.json`). What limits the plateau is not resolved here:
the closed-form equilibrium capacity of the same population at T = 1.21 s is
well above the simulated value (2,127 veh/h/lane at T = 1.209 s in the
`model_factor` rows of `artifacts/idm_i24_capacity_equilibrium.json`, against
1,709 simulated here), so the ceiling lies in the multi-lane dynamics
(lane changes, insertion) of the straight-road measurement, not in the
headway. The episode-cost rows are empty because the cloud stage ran without
the I-24 episodes. Any run with this population is labelled "capacity-scaled,
target not met" — it is a population that carries more than the I-24 one,
not one that carries the observed flow.

## 9. The lock's origin was a map defect at two exits (2026-09-24)

The weave-model slice (§8's neighbour, `docs/WEAVE_MODEL_PLAN.md`; single
seed, session record) was read for where the queue starts, without a new
simulation: on edge `1001426896` (10,732–10,955 m), the last mainline edge
before the 12th Street / State Capitol exit, at t = 87 s, spreading upstream
at about 8 km/h (10.0 km by 300 s, 8.75 km by 600 s, 6.25 km by 1,800 s).
Through vehicles were stopped in the leftmost lane, which led only to that
exit; the other lanes stalled behind them (SUMO's cooperative braking), and
the entrances upstream — 40648744 at 9.94 km, T.H.61 NB at 7.39 km — were
victims of the queue, not its cause.

The compiled network is wrong at that split, and at one more:

| split | OSM | compiled (2026-09-23 rounds) | fix |
|---|---|---|---|
| `1001426896` → 12th Street exit `82150350` | 3 lanes, `turn:lanes none\|none\|through;slight_right`; the link leaves 4–29 m to the RIGHT of the mainline | 4 lanes under `--ramps.guess`, the added LEFT lane the only way into the exit | `--ramps.unset 1001426896`: 3 lanes, lane 0 an option lane (exit and through) |
| `45608485` → Mounds Blvd / Kellogg Blvd exit `18207912` | 5 lanes, `\|\|\|slight_right\|slight_right`; the link leaves 8–14 m to the RIGHT | the two LEFTMOST lanes (3, 4) feed the exit, with or without guessing | explicit connections: lanes 0–1 exit, 2–4 continue (`data/osm/mndot_i94_wb_stpaul.splits.con.xml`, `OSMNetwork.patch_files`) |

The 6th Street exit (`42165869`, the left exit towards the Lafayette Freeway;
ramp discovery classes it as a plain `motorway_link` exit, not a C-D road)
really is a left exit and is left alone. `tests/test_microsim/test_microsim_osm_split_patch.py`
pins both compiled splits on the committed extract; the five guessed
acceleration lanes survive the exclusion. Round 1 (no guessing) ran with the
Mounds/Kellogg defect, round 2 and the weave slice with both. The corrected
network is in all three MnDOT scenarios; whether it removes the lock is the
question of the 2026-09-24 cloud round (`mndot_slice_weave`, then the two
20-seed weave batteries), reported in §10 when it has run.

The check is generic since the same day (`microsim.split_audit`, §3; the
offsets there sample the link's first 300 m, so they differ from the
hand-read figures in the table above): on this extract compiled without the
fixes it reports the eight exits with exactly these two as defects —
`45608485 → 18207912` wrong_side (link 8–9 m to the right, compiled lanes
3–4 of 5) and `1001426896 → 82150350` added_lane_wrong_side (link 1–45 m to
the right, lane 3 of 4 with OSM `lanes=3`) — and the 6th Street exit
(`45782590-AddedOffRampEdge → 42165869`, 5–19 m to the left, lane 3 of 4)
as `ok`; the patch it generates for the Mounds/Kellogg split is the committed
file's five connection lines, and with the fixes it reports none
(`tests/test_microsim/test_microsim_split_audit.py`).

## 10. Weave batteries on the corrected map (2026-09-24) — still locked, now at the T.H.52 weave

Second VM of the night (`flowstate-weave-b`, n2-standard-32, us-west1-c),
stages `mndot_slice_weave` → `battery_mndot_weave` → `battery_mndot_weave_mnpop`
on `scenarios/mndot_i94_wb_stpaul_weave.yaml` (the corrected map of §9, Ruth
St and T.H.52 as weaving sections, two entrances on the scripted merge).

**Slice probe (4 seeds, 35 min):** departed 0.878 (lowest 0.877), no starved
ramp (`artifacts/validation_mndot_i94_wb_stpaul_weave_slice.json` in the
round's archive; the observed windows do not overlap a 35-minute slice, so
no GEH/RMSPE).

**The 4-hour battery locks again.** Per-seed departed shares 0.32–0.43 with
`on-ramp 18207436` (scripted merge) starved — worse than round 2's 0.41. Read
from the first finished seed's trajectories on the VM (scripts and outputs
under `artifacts/mndot_rounds/weave_2026-09-24/`, the scripts kept as `.py.txt`): the first standstill
(50 m × 60 s bins, mean speed < 2 m/s, ≥ 10 samples) is at **x = 10.40 km in
lanes 0 and 1 at minute 4** of the run — the START of the T.H.52 weaving
section (entrance 769818012 on edge 51388891, the exit 18207598 at
10.73 km): the auxiliary lane and the rightmost through lane stop together.
By minute 6 the standstill covers 10.30–10.40 km, by minute 35 it reaches
7.3 km (100 m × 5 min bins). The 12th Street and Mounds/Kellogg splits of §9
are no longer where it starts. The section's own counters for that seed:
3,542 entered / 2,329 exited, mean wait 17 s; Ruth St 598 / 219, mean wait
177 s.

**Weave-parameter probe on the slice** (4 seeds each, run beside the
battery; session record with artifacts under
`artifacts/mndot_rounds/weave_2026-09-24/probe_*.json`):

| variant (both weaves) | departed, mean of 4 seeds (lowest) | starved ramps |
|---|---|---|
| defaults (`accept_gap_s` 0.6, `exit_accept_gap_s` 0.6, `force_within_m` 80, `courtesy` 0) | 0.878 (0.877) | none |
| `courtesy` 1.0 | 0.873 (0.871) | none |
| `force_within_m` 250, `force_after_s` 2 | 0.861 (0.805) | 40648744, 769818012 |
| `accept_gap_s` 0.3, `exit_accept_gap_s` 0.3 | 0.883 (0.879) | none |

None of the three knobs moves the slice; the gap-acceptance parameters are
not the lever. Hypothesis, not tested: the two movements block each other at
the section's start — entering vehicles hold the auxiliary lane while the
rule that keeps an exiting vehicle behind the vehicle ahead in that lane
stops lane 1 behind them (docs/CONTRACTS.md §2, the deadlock rules) — so a
weave that in reality carries about 1,400 veh/h of entries plus the exit
flow through 305 m stalls at its first conflict. The next step is a
re-derivation of the weave's conflict handling (and a slice test that fails
on this exact case), not a parameter change. The Minnesota-population
battery on the same scenario is reported below when it has run.

**After the fixture-level fix (commit 462b731 — an accepted entering change
executes under SUMO lane-change mode 256 instead of being refused by its
brake-gap test, and station-keeping applies only inside the last
`force_within_m`):** the same 4-seed slice on the corrected map departs
0.879 (0.838–0.904; `artifacts/mndot_rounds/weave_2026-09-24/probe_fix_462b731.json`,
session record run on the VM with that runner) against 0.878 before — no
material change at 35 minutes, one seed with 40648744 and 769818012 starved.
The fixture improvement (a 3-lane weave at 1,620 veh/h from a 3 m/s crawl to
11–17 m/s) does not carry to T.H.52 at its demand; the dense fixture case
(3,240 veh/h) still crawls too. The conflict handling of a weaving section
at capacity is the open modelling problem for the next round.

**The 20-seed weave battery, numbers** (per-run files fetched from the VM
while its scoring was still running; `artifacts/mndot_rounds/weave_2026-09-24/battery_weave_partial_scores.txt`,
a session record until the stage's own artifact is ingested): departed share
over 20 seeds mean 0.444 (0.225–0.557); over all 20 seeds (scored on the VM before its cap; `battery_weave_final_scores.txt`
beside it), speed RMSPE 0.883 (0.840–0.957), GEH < 5 on a share of 0.000 of 840 link-hours,
a "wave speed" of 7.2–7.6 km/h that is the queue front, not a stop-and-go
wave; collisions 0–2 per seed (7 in 20 seeds). Round 2 (2026-09-23, the
defective map, lane-change merges) had departed 0.41 (`artifacts/mndot_rounds/round2_gridlock_record.json`); the
corrected map with the weave model is not better on the four-hour window.

**Why the plateau (2026-09-24, block 3; stages `capprobe_mnfleet_4l` and
`capprobe_i24fleet_3l`, artifacts `artifacts/idm_capacity_probe_*.json` with
their `.calibration.json` sidecars — diagnostics, no scenario points at
them).** The same T-scaling grid was run twice more: the corridor's own
fleet block on a 4-lane road, and the I-24 replica's fleet block
(`scenarios/i24_replica_corrected.yaml`) on the 3-lane road.

| fleet block | lanes | T × 1.00 | 0.95 | 0.90 | 0.85 | 0.80 | 0.75 | outcome |
|---|---|---|---|---|---|---|---|---|
| I-94 corridor (`model: EIDM`, heterogeneity 0.15) | 3 (§8) | 1,591 | 1,626 | 1,670 | 1,656 | 1,709 | 1,703 | target not reached |
| I-94 corridor (same block) | 4 | 1,609 | 1,637 | 1,676 | 1,670 | 1,653 | 1,689 | target not reached |
| I-24 replica (`model: IDM`, heterogeneity 0.12) | 3 | 1,672 | 1,726 | 1,766 | 1,836 | 1,904 | 1,938 | 1,907 met at T × 0.795 (T = 1.201 s) |

The lane count is not the cause (4 lanes change nothing). The two fleet
blocks differ in the car-following model (the onboarding path writes
`model: EIDM`, SUMO's extended IDM with estimation errors and action points;
the I-24 replica runs plain IDM) and in the heterogeneity draw (0.15 vs
0.12); the lane-change settings were the same (`lc_strategic` 5.0,
`lc_keep_right` 0) in the corridor scenario the probes inherited from — the one
committed before the §11 regeneration (the probes' `created_at` 13:09–13:10Z
precedes commit 0ef3b67, whose onboarding defaults wrote `lc_strategic` 1.0 and
`lc_keep_right` 1.0 into the corridor's fleet block; the sidecars record the
base scenario's path, not its lane-change values). The population was fitted as IDM on I-24 trajectories,
so running it under EIDM is a model-form change that costs about 11 % of
straight-road capacity at every headway and flattens the curve. Consequence:
a Minnesota population that meets its capacity target exists under IDM
(the I-24 fleet block, T × 0.795); under the corridor's EIDM block the
target cannot be met by headway alone. The corridor scenario's `model` is
therefore a calibration decision to be made deliberately (EIDM was the
onboarding default, not a fit), and the next corridor round should state
which model it runs and derive its population under that model.

## 11. Inputs regenerated (2026-09-24, block 3)

With the S792 lane loop 3240 excluded (§7 item 2) the observations, the
station table, the demand and the scenario were rebuilt from the committed
extract with the onboarding defaults of the night before (ramp guessing on,
split fixes on, collector–distributor pairs):
`scripts/mndot_fetch.py … --exclude-detectors 3240 --exclude-reason "…"`,
`scripts/onboard_corridor.py … --osm-file data/osm/mndot_i94_wb_stpaul.osm`,
`scripts/corridor_demand.py …` (the §3 commands otherwise unchanged). The
scenario now has 17 ramps (the White Bear Ave collector–distributor split
and re-entry as a pair: out 374 veh/h, back 442 veh/h by conservation), the
two split fixes applied by the audit, and the carried residuals
S1066→S1067 −1, S1947→S1069 +9, S1069→S1070 −317, S1070→S1948 −262,
S1948→S792 +1, **S792→S791 +366** veh/h (was +775; what remains there is
still a detector question — the downtown entrance's passage loop reads
552 veh/h against the 889 the balance now assigns — first written here as
524; the review below corrects it). The weave variant and
the 35-minute slice were rebuilt on the new base with the same merge
settings and the same slice offset. Rounds 1–2 and the 2026-09-24 weave
round keep their own config hashes in their records; every later run uses
these inputs.

*Review of the regenerated inputs (2026-09-24, later the same day), corrections
to the figures above:*

- **The entrance figure is wrong.** The balance assigns the Mounds/Kittson
  entrance (on-ramp 40648744) **889 veh/h**, not 524: S790 − S791 is 522 veh/h
  and the carried S792→S791 residual (+366) lands on it, the only entrance in
  the next bracket. Its passage loop 3244 reads 552 veh/h (05:30–09:30 mean),
  so the carried residual overshoots the loop by 337 veh/h — the same evidence
  that the +366 is not real traffic. The +366 is the mean of the *positive*
  part of S791 − S792′ (34 of 48 windows); the net change is +239 (§7's +238),
  and the negative part (−127 veh/h mean) is what the C-D exit rnd_87209
  (off-ramp 42165869) takes by conservation (126 veh/h). The "was +775" is
  the same measure on the old artifact.
- **rnd_87205 is 428 m from the exit's cross-section** (8,923.5 − 8,495.6 m
  on the chain), not 399 m as §7 says — 399.0 is the node's lane position on
  edge 45782590 in `stations_x.csv`. Unmatched either way (radius 350 m).
  Using its live loop 3242 as one lane of two would not move the residual:
  2 × 3242 is 229 veh/h at the peak against the 683 veh/h the exit must take
  (S1948's two exit lanes carry 535), so the share rule scales the exit to
  the bracket's remainder as before (965 → 972 veh/h, `detector_scaled`
  instead of `conservation`; S1948→S792 +1 → +8) and S792→S791 stays at
  +365.6 exactly (re-run of `calibration.onboarding._close_balance` on the
  committed observations with the synthetic match).
- **The first entrance is unchanged.** On-ramp 1077665160 (x = 912 m) is
  zeroed (`zero_outside_observed_span`, the artifact's `zeroed_ramps` lists
  it), its scenario inflow is 0 in every step, and the entry inflow at x = 0
  is S1063's count (mean 3,050 veh/h, peak 4,275), which already includes
  it; `entry_lane_shares` is null. Nothing is double-counted.
- **The wave context had not applied the exclusion.** `station_speed_series`
  read every inventory lane, so `context.detector_wave_speed` was estimated
  with loop 3240 still in S792's 30-second series (the committed context
  reproduces bit for bit from the cache that way). Fixed: the series takes
  `exclude_detectors` and `mndot_fetch.py --wave-context` passes the same
  names; the context was recomputed from the cache with 3240 excluded
  (everything else in `observations.json` unchanged). S1948→S792 18.3 → 18.6
  km/h, S792→S791 26.1 → 25.7 km/h, median 21.3 km/h unchanged, IQR
  18.6–24.2 (was 18.4–24.2), leave-one-date-out 18.4–21.6 unchanged. §4a's
  table shows the pre-exclusion values.
- **The rebuilt slice's boundary was not shifted.** Its inflow and every ramp
  series were the weave's shifted by 5,400 s, but the boundary speed schedule
  was the weave's own 48 steps from 05:30 — the slice's exit was throttled by
  the 05:30–06:05 speeds (27 m/s) while its demand was the 07:00 peak. The
  §10 slice rows predate the rebuild; any slice run between the 08:26 rebuild
  and this fix used the unshifted boundary. Fixed (nine steps from 07:00,
  22.1 m/s at t = 0), with `tests/test_calibration/test_mndot_slice_variant.py`
  pinning every series of the slice to one offset.

**Slice on the regenerated inputs with the cooperative-follower weave
(2026-09-24, block 3; commit 1bed27f's runner, VM `flowstate-r3-d`,
`artifacts/mndot_rounds/weave_2026-09-24/slice_regenerated_corridor_cooperative_weave_1bed27f.json`):**
departed 0.892 over 4 seeds (lowest 0.847; one seed starves 40648744 and
769818012) against 0.878 on the old inputs and rules — a small gain at 35
minutes. The 20-seed four-hour battery on the same inputs runs next (VM E).

**Correction (2026-09-24, block 3, after the traceability audit).** The
§11 regeneration wrote the scenario with the builder's fleet defaults for
lane changing (`lc_strategic` 1.0, `lc_strategic_ramp` unset,
`lc_keep_right` 1.0), silently dropping the corridor's deliberate settings
of 5.0 / 1.0 / 0.0 (the I-24 replica's, chosen because a strategic eagerness
of 5 removes SUMO's diverge lane-change stall and keep-right 0 stops
through traffic crowding the merge lanes). The 20-seed battery of VM F ran
on the reset values (its record below says so); the three scenarios now
carry 5.0 / 1.0 / 0.0 again, with new config hashes, and every later run
uses them. The onboarding path is being changed so that a re-onboard keeps
an existing scenario's fleet block instead of resetting it.


**The 20-seed battery on the regenerated inputs (VM F, 2026-09-24, block 3;
`artifacts/mndot_rounds/weave_2026-09-24/battery_regenerated_inputs_reset_lc_runner_42ce900.json`,
config hash 0901cc8beaff — the §11 inputs with the lane-change settings still
reset to the builder's defaults, and the runner at commit 42ce900, i.e. the
second weave derivation with the cooperative follower).** Departed share
mean 0.233 (lowest 0.184), eight starved entrances including the
collector–distributor re-entry; speed RMSPE 0.968 (95 % interval 0.956–0.981); GEH < 5 on a share of
0.000 of 840 link-hours; the "wave speed" of 7.5 km/h is again the queue
front. Worse than the 0.444 of the old inputs and the first derivation: the
reset lane-change settings (keep-right 1.0 pushes through traffic into the
merge lanes) and the added C-D re-entry demand both weigh on the same
merges. The scoring phase took 6,719 s in one process (the older memory
constant estimated 68 GB per worker for 106 M rows per replicate; the diet
committed since brings that to about 17 GB, so the next round scores in
parallel). The next battery (VM G) runs the corrected scenarios (lane-change
settings restored) with the sixth-derivation runner.


**VM G (2026-09-24, block 3): the corrected scenarios (lane-change settings
restored, config hash 53e4b208fd1d) with the sixth-derivation runner
(commit 68b69f0).** Slice, 4 seeds: departed 0.848 (lowest 0.834) — below the
0.892 of the second derivation on the reset settings; the first seed's
standstill forms at minute 11 at the END of the T.H.52 section (10.60–10.70
km, lanes 0 and 1; 8,971 deferred forced changes, 21 unfinished, 133
exit-bound vehicles reached the section and 88 exited — evidence under
).
Battery, 20 seeds: departed 0.247 (lowest 0.180; the first seed 0.528, the
rest 0.18–0.35), speed RMSPE 0.963 (95 % interval 0.954–0.972), GEH < 5 on
0.000 of 840 link-hours, "wave speed" 6.9 km/h (the queue front) —
no better than VM F. The scoring phase took 158 s on six workers (VM F:
6,719 s in one) — the memory diet holds. Read together with the slice
diagnosis: the entrance-side derivations moved the fixture but on the
corridor the EXIT movement of the T.H.52 weave locks at the gore's end; the
exit-side rule (commit ecf6d79, reviewed in 84cc857) is the next round
(VM H).


**VM H (2026-09-24, block 3): the corrected scenarios (config hash
53e4b208fd1d) with the exit-side runner (commit 84cc857).** Slice, 4 seeds:
departed 0.979 (lowest 0.976), no starved ramp
(`artifacts/mndot_rounds/weave_2026-09-24/slice_corrected_inputs_exit_side_84cc857.json`).
Battery, 20 seeds (`battery_corrected_inputs_exit_side_84cc857.json` beside
it, per-seed shares in `battery_exit_side_per_seed_departed.txt`): departed
**0.743** (19 seeds between 0.696 and 0.799; one seed collapses to 0.230
with three collisions and 152,153 deferred forced changes at the Ruth St
section), speed RMSPE **0.782** (95 % interval 0.760–0.804; 0.963 the
round before), GEH < 5 on 0.000 of 840 link-hours, "wave speed"
6.8 km/h (the queue front, still), 26 % of the planned vehicles never
departed and eight entrances still starve at the peak. Given-up exits (the
re-scored artifact `…_rescored_weave_exits.json`, key `weave_exits`): Ruth St
70 of 8838 reached exiters (0.8 %), T.H.52 206 of 63782
(0.3 %) — both within the 2 % threshold, so the exit flows are honest.
Scoring 137 s on six workers.

Reading: the exit-side rule is the first change that moves the four-hour
corridor — from 0.247 to 0.743 of demand departed and RMSPE from 0.96 to
0.78 — but the corridor is **not reproduced**: a quarter of the demand
still queues at the boundary, the entrances still starve at the peak, one
seed in twenty locks, and no link-hour meets GEH 5. The next levers are the
entrances that still starve (the scripted merges 18207436 and 178547099, the
Ruth St weave) and the seed that locks; the per-seed metas are in the
round's archive.

### 11a. VM H read per seed (2026-09-24, block 3): one queue with its head at the T.H.52 end; the locking seed stops at that same end, not at Ruth St

Read from the per-replicate files under
`runs/mndot_i94_wb_stpaul_weave/baseline/53e4b208fd1d/<seed>/` (`meta.json`,
`metrics.json`, `observed_scores.json`; no trajectories were read — the
laptop rule) with the battery artifact
`artifacts/mndot_rounds/weave_2026-09-24/battery_corrected_inputs_exit_side_84cc857.json`,
`data/mndot/mndot_i94_wb_stpaul/observations.json` and
`artifacts/demand_mndot_i94_wb_stpaul.json`. The two extraction scripts are
session records beside the battery, `vmh_per_seed.py.txt` and
`vmh_flows.py.txt` (run from the repo root as `uv run --no-sync python`).
Conventions: windows are 5 min from 05:30; the scored windows are w6–w47
(06:00–09:30; the 30-minute warm-up is not scored). A station's cell is the
span to its neighbours' midpoints
(`validation.observed.ObservedCorridor.segment_bins`): S1063 0.48–1.70 km,
…, S791 9.35–9.92 km (its downstream end is the 40648744 entrance at
9.88 km), S790 9.92–10.60 km (that entrance through the first 270 m of the
T.H.52 weave, 10.33–10.72 km), S97 10.60–11.54 km (the weave's last 120 m,
its exit gore, the Jackson St exit at 10.91 km and the boundary edge).
"Simulated flow" below is the link-hour flow recovered from each
station-hour's GEH against the observed count (GEH = √(2(m−c)²/(m+c))
solved for m < c, then averaged over seeds); the three hours at S1063 sum to
about 20 % less than the mainline's departed count, so treat it as ±15 %:
it ranks stations and hours, it does not calibrate them. Seeds are
abbreviated to their last six digits in the battery artifact's `per_seed`
order; the locking seed is 5690692725577505498 ("lock"), the two low seeds
8557154790156791364 and 6904272788004776631 ("low"). "Mainline" is the
entry's own delivery: 12,182 planned in every seed (33,912 less the ramps'
21,730). Ramp columns are in corridor order with the demand artifact's
`x_m`; flows at S1069 (`x_ref_m` 6,684) are `metrics.json`'s
`throughput_veh_h`.

**Per seed** (`meta.json` `n_vehicles_departed/planned`,
`ramps[].n_departed/n_planned`, `collisions`; `observed_scores.json`
`rmspe`).

| seed | departed | mainline | Hudson 18207436 (3.30 km) | Hudson 18207653 (3.66 km) | McKnight 178547099 (4.22 km) | Ruth 745524613 (5.12 km) | C-D re-entry (5.92 km) | T.H.61 53062592 (7.37 km) | 40648744 (9.88 km) | T.H.52 769818012 (10.33 km) | collisions (place @ m, minute) | RMSPE | veh/h at S1069 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| …141156 | 0.770 | 0.548 | 0.47 | 0.95 | 0.87 | 0.98 | 1.00 | 1.00 | 0.73 | 0.96 | none | 0.772 | 1662 |
| …869882 | 0.781 | 0.574 | 0.49 | 0.91 | 0.90 | 1.00 | 1.00 | 1.00 | 0.72 | 0.96 | McKnight 178547099 @237 m, min 8; McKnight 178547099 @238 m, min 60; Hudson 18207436 @231 m, min 69; Hudson 18207436 @228 m, min 74 | 0.767 | 1680 |
| …931053 | 0.778 | 0.561 | 0.49 | 0.96 | 0.93 | 1.00 | 1.00 | 1.00 | 0.72 | 0.96 | McKnight 178547099 @241 m, min 59 | 0.766 | 1705 |
| …135749 | 0.781 | 0.560 | 0.52 | 0.96 | 0.95 | 1.00 | 1.00 | 1.00 | 0.72 | 0.96 | C-D split @35 m, min 123 | 0.758 | 1758 |
| …189526 | 0.780 | 0.569 | 0.49 | 0.94 | 0.89 | 0.99 | 1.00 | 1.00 | 0.73 | 0.96 | McKnight 178547099 @217 m, min 61 | 0.765 | 1698 |
| …282993 | 0.774 | 0.556 | 0.48 | 0.95 | 0.90 | 1.00 | 1.00 | 1.00 | 0.72 | 0.95 | T.H.52 exit @300 m, min 222; 18207598 @0 m, min 222 | 0.767 | 1687 |
| …044631 | 0.799 | 0.602 | 0.52 | 1.00 | 0.93 | 1.00 | 1.00 | 1.00 | 0.73 | 0.97 | Hudson 18207436 @231 m, min 75 | 0.753 | 1765 |
| …596746 | 0.772 | 0.556 | 0.47 | 0.94 | 0.87 | 0.97 | 0.99 | 1.00 | 0.71 | 0.97 | T.H.52 exit @20 m, min 50; Hudson 18207436 @218 m, min 74 | 0.769 | 1655 |
| …534583 | 0.785 | 0.571 | 0.50 | 0.98 | 0.93 | 0.98 | 1.00 | 1.00 | 0.72 | 0.97 | C-D split @14 m, min 106 | 0.762 | 1718 |
| …706937 | 0.775 | 0.563 | 0.48 | 0.91 | 0.90 | 1.00 | 1.00 | 1.00 | 0.71 | 0.96 | none | 0.768 | 1686 |
| …791364 (low) | 0.704 | 0.467 | 0.40 | 0.58 | 0.59 | 0.51 | 1.00 | 1.00 | 0.78 | 0.97 | McKnight 178547099 @214 m, min 57 | 0.822 | 1281 |
| …023852 | 0.773 | 0.552 | 0.48 | 0.93 | 0.89 | 1.00 | 1.00 | 1.00 | 0.71 | 0.97 | Hudson 18207436 @219 m, min 78 | 0.766 | 1700 |
| …776631 (low) | 0.696 | 0.447 | 0.39 | 0.53 | 0.54 | 0.47 | 1.00 | 1.00 | 0.81 | 0.98 | McKnight 178547099 @234 m, min 49 | 0.858 | 1199 |
| …009404 | 0.793 | 0.589 | 0.49 | 1.00 | 0.92 | 1.00 | 1.00 | 1.00 | 0.72 | 0.98 | none | 0.758 | 1803 |
| …347669 | 0.776 | 0.561 | 0.49 | 0.91 | 0.89 | 1.00 | 0.99 | 1.00 | 0.72 | 0.97 | McKnight 178547099 @220 m, min 63 | 0.767 | 1667 |
| …976611 | 0.770 | 0.542 | 0.47 | 0.96 | 0.89 | 1.00 | 1.00 | 1.00 | 0.72 | 0.96 | McKnight 178547099 @237 m, min 8 | 0.770 | 1692 |
| …682178 | 0.772 | 0.550 | 0.48 | 0.92 | 0.87 | 0.99 | 1.00 | 1.00 | 0.72 | 0.97 | Hudson 18207436 @222 m, min 74 | 0.768 | 1689 |
| …505498 (lock) | 0.230 | 0.322 | 0.29 | 0.26 | 0.31 | 0.28 | 0.30 | 0.17 | 0.07 | 0.11 | McKnight 178547099 @233 m, min 55; McKnight 178547099 @227 m, min 56; McKnight 178547099 @238 m, min 59 | 0.957 | 232 |
| …483394 | 0.785 | 0.576 | 0.48 | 0.97 | 0.90 | 1.00 | 1.00 | 1.00 | 0.72 | 0.98 | C-D split @26 m, min 73 | 0.761 | 1715 |
| …041784 | 0.774 | 0.547 | 0.48 | 0.95 | 0.93 | 1.00 | 1.00 | 1.00 | 0.72 | 0.97 | McKnight 178547099 @228 m, min 55 | 0.769 | 1709 |

What the table says. (i) The 19 seeds that run depart 0.696–0.799 and their
mainline entry 0.447–0.602 (mean 0.552): of the ≈ 7,780 vehicles a good
seed never inserts, 5,460 (70 %) are the mainline's, 830 (11 %) Hudson Rd
18207436's, 960 (12 %) 40648744's, the other six entrances together 7 %.
(ii) The battery's "eight starved entrances" is the union over replicates
(`validation.battery`: starved = at least 100 planned and under half
delivered, first-seen order): seven of the eight come from the locking seed
alone. In the 19 good seeds one entrance is under the threshold — 18207436
in 16 of them (0.39–0.49; 0.50–0.52 in the other three) — and Ruth St
745524613 in one low seed (0.47). The C-D re-entry's inferred 442 veh/h is
delivered in full (0.99–1.00) in every good seed; it is not the entrance
that starves. (iii) Collisions: 21 in the 19 good seeds, fifteen on the
two scripted merges' acceleration lanes at 214–241 m
(`638519829-AddedOnRampEdge_1` = McKnight Rd 178547099, minutes 49–63 and
twice at minute 8; `43917735#1-AddedOnRampEdge_1` = Hudson Rd 18207436,
minutes 69–78) — the minutes the queue front reaches each merge (below);
the other six at the Ruth St split's first metres (three) and the T.H.52
exit gore (three).

**Weaving sections** (`meta.json["weave_sections"]`, both sections, every
counter).

Weaving section Ruth St (745524613 → C-D split 18208090, 136 m):

| seed | entered | exited | reached (exiting) | departed exiting | missed exit | forced | deferred | unfinished | vacated | vacate refused | pair releases | cooperations | changer eased | wait in s | wait out s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| …141156 | 736 | 454 | 471 | 763 | 6 | 365 | 69936 | 11 | 1506 | 401 | 3720 | 113941 | 73211 | 144 | 80 |
| …869882 | 757 | 498 | 503 | 838 | 1 | 351 | 56917 | 11 | 1481 | 550 | 3255 | 103022 | 64441 | 120 | 69 |
| …931053 | 761 | 494 | 502 | 793 | 5 | 330 | 58278 | 17 | 1597 | 531 | 2960 | 105263 | 57960 | 116 | 68 |
| …135749 | 815 | 528 | 534 | 776 | 5 | 385 | 53991 | 1 | 1630 | 492 | 3724 | 103502 | 57352 | 116 | 67 |
| …189526 | 776 | 483 | 496 | 816 | 8 | 399 | 59611 | 5 | 1528 | 501 | 3919 | 97494 | 64747 | 119 | 91 |
| …282993 | 761 | 463 | 474 | 784 | 4 | 374 | 51015 | 21 | 1593 | 400 | 3206 | 119759 | 68374 | 116 | 63 |
| …044631 | 813 | 589 | 598 | 933 | 3 | 365 | 49867 | 12 | 1534 | 586 | 3011 | 95877 | 56657 | 109 | 71 |
| …596746 | 735 | 462 | 470 | 776 | 3 | 335 | 69155 | 8 | 1540 | 391 | 3400 | 123743 | 75795 | 133 | 105 |
| …534583 | 715 | 473 | 485 | 792 | 3 | 375 | 54651 | 8 | 1632 | 525 | 3187 | 95336 | 61351 | 124 | 75 |
| …706937 | 764 | 456 | 463 | 739 | 2 | 380 | 66347 | 8 | 1517 | 461 | 4138 | 108825 | 63828 | 132 | 85 |
| …791364 (low) | 200 | 107 | 125 | 344 | 1 | 87 | 110641 | 17 | 1089 | 305 | 1259 | 162230 | 191320 | 140 | 81 |
| …023852 | 795 | 515 | 529 | 844 | 4 | 345 | 60371 | 12 | 1449 | 533 | 3236 | 99220 | 61825 | 120 | 71 |
| …776631 (low) | 191 | 85 | 106 | 305 | 3 | 62 | 180789 | 28 | 951 | 282 | 1175 | 161787 | 288358 | 180 | 102 |
| …009404 | 782 | 531 | 536 | 868 | 1 | 358 | 53804 | 1 | 1672 | 489 | 3252 | 94303 | 51339 | 113 | 63 |
| …347669 | 689 | 449 | 469 | 746 | 5 | 316 | 59257 | 21 | 1615 | 411 | 3585 | 133433 | 69460 | 140 | 83 |
| …976611 | 754 | 468 | 489 | 757 | 5 | 379 | 71671 | 18 | 1543 | 436 | 3979 | 126335 | 73746 | 141 | 95 |
| …682178 | 779 | 486 | 493 | 793 | 4 | 366 | 65195 | 4 | 1536 | 388 | 3858 | 127698 | 77827 | 137 | 94 |
| …505498 (lock) | 42 | 51 | 51 | 122 | 0 | 8 | 152153 | 13 | 572 | 106 | 12 | 65703 | 194949 | 7 | 4 |
| …483394 | 761 | 518 | 532 | 808 | 3 | 301 | 68531 | 8 | 1664 | 380 | 3321 | 125561 | 68953 | 143 | 65 |
| …041784 | 789 | 501 | 512 | 774 | 4 | 382 | 58357 | 11 | 1562 | 484 | 3790 | 113486 | 62800 | 123 | 71 |
| mean of 19 | 704 | 451 | 462 | 750 | 4 | 329 | 69389 | 12 | 1507 | 450 | 3262 | 116359 | 83650 | 130 | 79 |

Weaving section T.H.52 (769818012 → off-ramp 18207598, 305 m):

| seed | entered | exited | reached (exiting) | departed exiting | missed exit | forced | deferred | unfinished | vacated | vacate refused | pair releases | cooperations | changer eased | wait in s | wait out s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| …141156 | 3705 | 3338 | 3354 | 3793 | 9 | 370 | 2065 | 2 | 2611 | 89 | 195 | 124998 | 23793 | 7 | 13 |
| …869882 | 3695 | 3393 | 3404 | 3818 | 7 | 294 | 1125 | 2 | 2635 | 129 | 44 | 117528 | 21608 | 6 | 11 |
| …931053 | 3801 | 3345 | 3358 | 3747 | 7 | 305 | 1084 | 4 | 2652 | 107 | 41 | 119460 | 21441 | 7 | 11 |
| …135749 | 3772 | 3313 | 3334 | 3733 | 14 | 326 | 2296 | 3 | 2636 | 132 | 88 | 122011 | 23383 | 8 | 11 |
| …189526 | 3803 | 3282 | 3300 | 3672 | 13 | 323 | 1728 | 1 | 2727 | 125 | 66 | 121493 | 22725 | 7 | 12 |
| …282993 | 3745 | 3243 | 3263 | 3667 | 13 | 304 | 1542 | 3 | 2732 | 77 | 76 | 119130 | 21873 | 7 | 11 |
| …044631 | 3567 | 3252 | 3274 | 3680 | 18 | 334 | 2777 | 4 | 2719 | 157 | 93 | 117834 | 22543 | 8 | 11 |
| …596746 | 3727 | 3293 | 3312 | 3686 | 12 | 331 | 2405 | 0 | 2720 | 113 | 91 | 121889 | 22812 | 7 | 12 |
| …534583 | 3595 | 3244 | 3267 | 3676 | 7 | 299 | 943 | 5 | 2705 | 114 | 29 | 119928 | 21301 | 7 | 11 |
| …706937 | 3701 | 3249 | 3275 | 3665 | 19 | 310 | 2634 | 6 | 2681 | 102 | 141 | 125344 | 24386 | 8 | 12 |
| …791364 (low) | 3803 | 3343 | 3352 | 3587 | 5 | 281 | 1597 | 2 | 2501 | 113 | 60 | 120699 | 22181 | 7 | 11 |
| …023852 | 3714 | 3351 | 3364 | 3792 | 9 | 291 | 1299 | 1 | 2753 | 93 | 34 | 116805 | 20834 | 7 | 11 |
| …776631 (low) | 3625 | 3318 | 3339 | 3594 | 15 | 326 | 2337 | 2 | 2405 | 181 | 106 | 120824 | 23420 | 7 | 12 |
| …009404 | 3783 | 3339 | 3352 | 3746 | 9 | 336 | 1518 | 0 | 2644 | 103 | 67 | 123461 | 22049 | 7 | 11 |
| …347669 | 3720 | 3326 | 3337 | 3733 | 9 | 300 | 1529 | 1 | 2679 | 86 | 54 | 118902 | 21586 | 7 | 11 |
| …976611 | 3814 | 3369 | 3385 | 3818 | 11 | 292 | 1564 | 3 | 2664 | 111 | 60 | 122312 | 21782 | 7 | 11 |
| …682178 | 3747 | 3385 | 3403 | 3813 | 12 | 345 | 2229 | 4 | 2695 | 133 | 81 | 124946 | 22840 | 7 | 12 |
| …505498 (lock) | 436 | 369 | 423 | 1218 | 2 | 45 | 130194 | 40 | 170 | 24 | 150 | 219552 | 492902 | 8 | 11 |
| …483394 | 3789 | 3327 | 3337 | 3732 | 6 | 305 | 1168 | 0 | 2651 | 158 | 32 | 118332 | 21507 | 7 | 11 |
| …041784 | 3737 | 3337 | 3349 | 3791 | 9 | 369 | 1804 | 1 | 2680 | 121 | 101 | 122943 | 22898 | 7 | 12 |
| mean of 19 | 3729 | 3318 | 3335 | 3723 | 11 | 318 | 1771 | 2 | 2657 | 118 | 77 | 120992 | 22366 | 7 | 11 |

Ruth St is the corridor's hardest section in every seed: 47 % of its
entrants are forced (329 of 704), the mean wait is 130 s in and 79 s out,
1,507 vacates, 3,262 pair releases and 50–72 k deferred forced changes per
run, where T.H.52 forces 8.5 % (318 of 3,729), waits 7 s in and 11 s out and
defers 0.9–2.8 k. Ruth St is 136 m long against T.H.52's 305 m and carries
the C-D split (17 % of the mainline exiting at the peak) beside 254 veh/h
entering; its `n_exited` runs 60 % of `n_departed_exiting` in every seed
(the rest exits after the section's end or is still upstream when the run
ends — the artifact's given-up share is 0.8 %, §11).

**Scripted merges** (`meta.json["scripted_merges"]`; both run
`accept_gap_s` 0.6, `force_after_s` 4, `force_within_m` 80, `courtesy` 0).

| seed | Hudson 18207436 entered | Hudson 18207436 changed | Hudson 18207436 forced | Hudson 18207436 unfinished | Hudson 18207436 wait mean s | Hudson 18207436 wait p90 s | McKnight 178547099 entered | McKnight 178547099 changed | McKnight 178547099 forced | McKnight 178547099 unfinished | McKnight 178547099 wait mean s | McKnight 178547099 wait p90 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| …141156 | 309 | 287 | 212 | 22 | 747 | 1498 | 886 | 860 | 706 | 26 | 249 | 432 |
| …869882 | 327 | 304 | 237 | 23 | 682 | 1255 | 883 | 861 | 705 | 22 | 244 | 468 |
| …931053 | 325 | 306 | 232 | 19 | 689 | 1245 | 933 | 909 | 743 | 24 | 226 | 415 |
| …135749 | 384 | 357 | 252 | 27 | 579 | 1169 | 971 | 949 | 780 | 22 | 209 | 401 |
| …189526 | 331 | 304 | 228 | 27 | 653 | 1170 | 864 | 842 | 696 | 22 | 245 | 438 |
| …282993 | 333 | 308 | 219 | 25 | 670 | 1472 | 885 | 867 | 697 | 18 | 238 | 421 |
| …044631 | 364 | 342 | 249 | 22 | 596 | 1266 | 925 | 904 | 762 | 21 | 227 | 433 |
| …596746 | 314 | 292 | 212 | 22 | 694 | 1379 | 855 | 837 | 690 | 18 | 249 | 470 |
| …534583 | 360 | 338 | 242 | 22 | 585 | 1296 | 919 | 899 | 729 | 20 | 229 | 432 |
| …706937 | 331 | 305 | 215 | 26 | 689 | 1315 | 896 | 871 | 688 | 25 | 237 | 444 |
| …791364 (low) | 198 | 174 | 81 | 24 | 369 | 1274 | 480 | 454 | 304 | 26 | 162 | 378 |
| …023852 | 327 | 302 | 215 | 25 | 680 | 1464 | 887 | 865 | 696 | 22 | 241 | 440 |
| …776631 (low) | 170 | 145 | 70 | 25 | 424 | 1561 | 431 | 406 | 261 | 25 | 179 | 447 |
| …009404 | 330 | 308 | 225 | 22 | 681 | 1283 | 908 | 888 | 724 | 20 | 219 | 407 |
| …347669 | 350 | 327 | 226 | 23 | 636 | 1210 | 893 | 870 | 693 | 23 | 238 | 444 |
| …976611 | 330 | 306 | 219 | 24 | 670 | 1484 | 892 | 868 | 710 | 24 | 239 | 436 |
| …682178 | 319 | 297 | 218 | 22 | 717 | 1378 | 845 | 823 | 666 | 22 | 253 | 504 |
| …505498 (lock) | 81 | 55 | 5 | 26 | 4 | 12 | 165 | 139 | 28 | 26 | 7 | 14 |
| …483394 | 315 | 291 | 206 | 24 | 720 | 1388 | 869 | 846 | 683 | 23 | 242 | 439 |
| …041784 | 329 | 305 | 212 | 24 | 673 | 1319 | 900 | 881 | 727 | 19 | 236 | 407 |
| mean of 19 | 318 | 295 | 209 | 24 | 640 | 1338 | 849 | 826 | 666 | 22 | 230 | 435 |

Hudson Rd 18207436 waits 640 s mean and 1,340 s p90 with 66 % of its
entrants forced; McKnight Rd 178547099 230 s / 435 s with 78 % forced. The
scripted merge waits for a gap that a standing mainline does not offer and
forces only inside the last 80 m after 4 s, so it starves in proportion to
its wait; the lane-change entrance between the two, Hudson Rd 18207653
(SUMO's own model, nothing driven), delivers 0.91. These waits are the
queue's arrival at each merge, not its cause (the front reaches S1066 at
06:45 and S1065 at 06:50, below; the merges' collisions cluster at the same
minutes).

**The locking seed 5690692725577505498 (departed 0.230).** Its station
trace against the good-seed mean, 5-min windows, m/s (`segment_speeds_sim`;
lock first, good mean in brackets):

| time | S1063 | S1065 | S1067 | S1069 | S1948 | S791 | S790 | S97 |
|---|---|---|---|---|---|---|---|---|
| 06:00 | 24.1 (23.9) | 23.7 (23.5) | 22.6 (23.0) | 23.6 (23.1) | 17.3 (21.6) | 0.1 (5.2) | 0.0 (5.3) | 0.0 (18.8) |
| 06:05 | 23.8 (23.8) | 23.9 (23.7) | 23.5 (23.3) | 23.0 (23.2) | 1.1 (21.7) | 0.0 (6.3) | 0.0 (5.5) | 0.0 (18.8) |
| 06:10 | 23.2 (23.5) | 23.8 (23.0) | 23.8 (22.6) | 7.7 (22.9) | 0.5 (20.3) | 0.0 (5.5) | 0.0 (5.4) | 0.0 (18.9) |
| 06:15 | 22.8 (23.5) | 21.8 (23.0) | 17.1 (21.3) | 2.0 (22.4) | 0.0 (15.2) | 0.0 (3.8) | 0.0 (5.2) | 0.0 (18.8) |
| 06:20 | 23.7 (23.2) | 21.9 (23.1) | 16.9 (21.0) | 0.4 (22.1) | 0.0 (6.9) | 0.0 (3.3) | 0.0 (5.1) | 0.0 (18.4) |
| 06:25 | 23.7 (23.1) | 24.0 (22.6) | 6.7 (19.1) | 0.0 (18.0) | 0.0 (4.2) | 0.0 (3.3) | 0.0 (5.2) | 0.0 (19.0) |
| 06:30 | 22.9 (22.7) | 22.8 (22.4) | 0.2 (14.5) | 0.0 (4.5) | 0.0 (4.3) | 0.0 (3.5) | 0.0 (5.3) | 0.0 (19.1) |
| 06:35 | 22.5 (22.5) | 4.5 (22.1) | 0.0 (9.1) | 0.0 (1.9) | 0.0 (4.4) | 0.0 (3.5) | 0.0 (5.1) | 0.0 (18.9) |
| 06:40 | 21.0 (22.5) | 0.3 (21.7) | 0.0 (3.1) | 0.0 (1.9) | 0.0 (5.1) | 0.0 (2.6) | 0.0 (4.7) | 0.0 (19.0) |
| 06:45 | 21.2 (22.3) | 0.0 (13.0) | 0.0 (1.4) | 0.0 (2.3) | 0.0 (3.7) | 0.0 (2.3) | 0.0 (5.0) | 0.0 (18.8) |
| 06:50 | 10.1 (22.8) | 0.0 (2.2) | 0.0 (1.2) | 0.0 (1.7) | 0.0 (3.1) | 0.0 (2.4) | 0.0 (4.8) | 0.0 (18.6) |
| 06:55 | 1.2 (22.9) | 0.0 (0.6) | 0.0 (1.3) | 0.0 (1.4) | 0.0 (3.4) | 0.0 (2.3) | 0.0 (4.8) | 0.0 (19.0) |
| 07:00 | 0.0 (22.8) | 0.0 (0.5) | 0.0 (1.0) | 0.0 (1.3) | 0.0 (3.0) | 0.0 (2.2) | 0.0 (4.7) | 0.0 (18.9) |

The lock is complete at the corridor's downstream end in the first scored
window: the S97 cell (10.60–11.54 km: the T.H.52 weave's exit end, the
Jackson St exit and the boundary edge), S790 and S791 read 0.0–0.1 m/s at
06:00 where the good seeds read 18.8, 5.3 and 5.2, and S1948 (8.4 km)
falls from 17 to 1 m/s by 06:05. The standstill then runs upstream — S1069
06:15, S1067 06:30, S1065 06:35, S1063 06:55 — 20–25 minutes ahead of the
good seeds' front and to 0.0 m/s rather than their 0.5–5 m/s crawl; from
07:00 every cell is at zero. The three collisions (McKnight Rd merge,
minutes 55, 56 and 59, i.e. 06:25–06:29) come an hour after the lock and at
the minute the standstill reaches that merge; seven good seeds collide at the
same place and minutes (49–63) without locking. Ruth St's 152,153 deferred
forced changes are a symptom, not the origin: 42 vehicles entered the
section all run (8 forced), so the count is a handful of vehicles standing
in a stopped section and deferring every step (≈ 5 per step over 28,800
steps); the section's counters diverge from 06:30 at the earliest, after
S97/S790/S791 (before 06:00), S1948 (06:05) and S1069 (06:15). The state is
structurally different, not a chaotic late event: the T.H.52 exit end, where
VM G's every seed locked (the "exit movement at the gore's end", above),
still locks in one seed of twenty inside the warm-up, before any collision.
Which of the exit gore, the Jackson St exit 190 m after it or the boundary
edge holds it, the files cannot say; it needs that seed's first 30 minutes
at 50 m × 1 min (`diag_lock.py.txt` in the round's archive).

**The two low seeds (8557154790156791364 at 0.704, 6904272788004776631 at
0.696): Ruth St semi-locks, a second failure mode.** Their Ruth St section
admits 200 and 191 entrants against 764 in the other seventeen (changed-in
154/139 against 472, changed-out 28/21 against 277, deferred 110,641 and
180,789 against 60,409, unfinished 17 and 28 against 10), the three
entrances upstream of it deliver 0.40/0.58/0.59 and 0.39/0.53/0.54 (the
seventeen: 0.47–0.52, 0.91–1.00, 0.87–0.95), and the flow at S1069 is
1,281 and 1,199 veh/h against 1,655–1,803. The cells downstream of the split,
S1947 and S1069, run at 15.0/15.3 and 13.8/14.2 m/s against 4.8/4.4 in the
seventeen — starved by a tighter lock upstream — while S1067/S1068 stand
(4.4/3.7, 4.4/4.2) and their T.H.52 counters and the downstream queue are
everyone's (S791 3.6/4.5 m/s in the first half hour). Ruth St therefore
holds the corridor in 2 of 19 seeds on its own, at the C-D split, with the
downstream queue unchanged.

**Where the queue starts (19 good seeds).** Good-seed mean simulated speed,
observed in brackets, m/s, 15-min steps (every cell, `segment_speeds_sim` /
`segment_speeds_obs`):

| time | S1063 | S1064 | S1065 | S1066 | S1067 | S1068 | S1947 | S1069 | S1070 | S1948 | S792 | S791 | S790 | S97 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 06:00 | 24 (32) | 24 (30) | 23 (30) | 23 (30) | 23 (30) | 23 (30) | 23 (29) | 23 (30) | 22 (30) | 22 (30) | 20 (29) | 5 (25) | 5 (26) | 19 (27) |
| 06:15 | 24 (32) | 23 (30) | 23 (31) | 23 (30) | 21 (30) | 22 (30) | 22 (29) | 22 (30) | 19 (30) | 15 (29) | 10 (28) | 4 (24) | 5 (23) | 19 (26) |
| 06:30 | 23 (32) | 23 (30) | 22 (30) | 21 (30) | 15 (29) | 19 (29) | 17 (27) | 5 (28) | 3 (25) | 4 (24) | 6 (23) | 3 (19) | 5 (15) | 19 (25) |
| 06:45 | 22 (32) | 22 (30) | 13 (31) | 2 (30) | 1 (29) | 1 (26) | 2 (19) | 2 (12) | 3 (16) | 4 (16) | 4 (12) | 2 (11) | 5 (13) | 19 (21) |
| 07:00 | 23 (32) | 5 (30) | 0 (31) | 1 (30) | 1 (29) | 1 (25) | 1 (20) | 1 (14) | 2 (16) | 3 (16) | 4 (14) | 2 (13) | 5 (14) | 19 (22) |
| 07:15 | 1 (32) | 0 (29) | 0 (30) | 1 (29) | 1 (23) | 1 (15) | 1 (12) | 1 (10) | 2 (14) | 3 (15) | 4 (12) | 2 (10) | 5 (13) | 18 (19) |
| 07:30 | 1 (32) | 1 (30) | 0 (30) | 1 (26) | 1 (14) | 1 (8) | 1 (8) | 1 (7) | 3 (12) | 3 (13) | 4 (9) | 2 (7) | 5 (11) | 16 (16) |
| 07:45 | 1 (32) | 1 (30) | 0 (31) | 1 (24) | 1 (15) | 1 (6) | 2 (7) | 1 (5) | 2 (11) | 4 (12) | 3 (7) | 2 (6) | 5 (10) | 13 (12) |
| 08:00 | 1 (32) | 1 (30) | 0 (31) | 1 (29) | 1 (19) | 1 (9) | 4 (8) | 3 (6) | 2 (11) | 3 (13) | 4 (9) | 2 (7) | 5 (11) | 14 (13) |
| 08:15 | 1 (32) | 1 (30) | 0 (31) | 1 (30) | 1 (23) | 1 (14) | 4 (9) | 4 (6) | 3 (11) | 3 (13) | 3 (9) | 2 (7) | 5 (11) | 16 (16) |
| 08:30 | 1 (32) | 1 (30) | 1 (31) | 1 (30) | 1 (27) | 1 (17) | 4 (10) | 4 (8) | 4 (11) | 5 (12) | 4 (9) | 2 (8) | 5 (11) | 15 (14) |
| 08:45 | 1 (32) | 1 (30) | 0 (31) | 1 (30) | 1 (28) | 1 (19) | 4 (14) | 4 (9) | 4 (13) | 6 (13) | 6 (9) | 3 (8) | 5 (11) | 16 (17) |
| 09:00 | 1 (32) | 1 (30) | 0 (31) | 1 (30) | 1 (29) | 1 (29) | 4 (22) | 4 (18) | 4 (16) | 6 (14) | 6 (11) | 5 (10) | 6 (12) | 18 (20) |
| 09:15 | 1 (32) | 0 (30) | 0 (31) | 1 (30) | 1 (29) | 1 (30) | 4 (28) | 4 (26) | 4 (22) | 5 (19) | 6 (13) | 4 (9) | 6 (11) | 18 (19) |

Per station, 19 seeds pooled (RMSPE per station over its 42 windows; share
of the summed squared relative error; first window under 10 m/s; recovered
link-hour flow, sim / observed, veh/h):

| station | x km | sim mean m/s | obs mean m/s | station RMSPE | share of SSE | first window < 10 m/s sim / obs | flow 06–07 sim / obs | 07–08 | 08–09 |
|---|---|---|---|---|---|---|---|---|---|
| S1063 W of I-494 (E Jct) | 1.09 | 8.2 | 31.9 | 0.81 | 0.079 | 07:10 / never | 2,102 / 2,990 | 1,039 / 3,917 | 977 / 3,214 |
| S1064 T.H.120 | 2.31 | 6.8 | 29.9 | 0.84 | 0.084 | 07:00 / never | 1,634 / 2,754 | 919 / 3,337 | 776 / 2,743 |
| S1065 Hudson Rd | 3.47 | 5.6 | 30.5 | 0.87 | 0.091 | 06:50 / never | 1,053 / 2,497 | 853 / 2,835 | 629 / 2,276 |
| S1066 McKnight Rd | 4.06 | 5.1 | 29.2 | 0.88 | 0.092 | 06:45 / never | 1,221 / 2,919 | 1,242 / 3,270 | 950 / 2,675 |
| S1067 Ruth St | 4.97 | 4.6 | 25.4 | 0.87 | 0.091 | 06:35 / never | 1,277 / 3,353 | 1,524 / 3,485 | 1,112 / 2,994 |
| S1068 White Bear Ave | 5.58 | 4.8 | 20.5 | 0.84 | 0.084 | 06:35 / 07:20 | 1,439 / 3,498 | 1,689 / 3,113 | 997 / 2,645 |
| S1947 E of T.H.61 | 6.07 | 5.9 | 17.4 | 0.83 | 0.082 | 06:35 / 07:20 | 1,872 / 4,104 | 2,050 / 3,445 | 1,317 / 2,959 |
| S1069 T.H.61 | 6.68 | 5.4 | 15.1 | 0.86 | 0.087 | 06:30 / 07:15 | 1,586 / 3,652 | 1,763 / 2,911 | 1,112 / 2,487 |
| S1070 Johnson Pkwy | 7.67 | 5.1 | 16.8 | 0.79 | 0.074 | 06:25 / never | 2,992 / 4,874 | 3,640 / 4,139 | 2,627 / 3,671 |
| S1948 E of Mounds Blvd | 8.40 | 5.7 | 16.8 | 0.73 | 0.064 | 06:20 / never | 2,988 / 4,801 | 3,539 / 4,157 | 2,658 / 3,793 |
| S792 Mounds Blvd | 8.98 | 5.7 | 13.6 | 0.66 | 0.052 | 06:15 / 07:25 | 2,569 / 4,315 | 2,513 / 3,252 | 1,818 / 2,880 |
| S791 Kellogg Blvd | 9.72 | 3.1 | 11.4 | 0.78 | 0.072 | 06:00 / 07:15 | 2,054 / 4,181 | 2,401 / 3,841 | 1,995 / 3,465 |
| S790 Kittson St | 10.13 | 5.1 | 13.5 | 0.62 | 0.046 | 06:00 / 07:45 | 2,628 / 4,582 | 3,090 / 4,400 | 2,852 / 4,148 |
| S97 I-35E | 11.07 | 17.0 | 18.9 | 0.15 | 0.003 | never / never | 2,581 / 4,109 | 3,262 / 4,351 | 3,212 / 4,059 |

The queue does not start at the S1063 side. It starts at the corridor's
last kilometre before the scored windows open: at 06:00 S791 (9.35–9.92 km,
ending at the 40648744 entrance) reads 5.2 m/s and S790 (the entrance
through the weave's first 270 m) 5.3 m/s against 25–26 observed, while S97
beyond the weave's exit reads 18.8 (27 observed) — the discharge step sits
inside the S790 cell — and S1063 is free at 24 m/s until 07:05. The front
then travels upstream at 7–9 km/h (S1948 at 8.40 km 06:20, S1069 06:30,
S1067 06:35, S1066 06:45, S1065 06:50, S1064 07:00, S1063 at 1.09 km 07:10),
which is the battery's "wave speed" of 6.8 km/h, and every cell from
S1063 to S791 spends the rest of the morning at 0–6 m/s. The recovered
flows say the same: S790 and S97 carry 2,600–3,300 veh/h in every hour
against 4,100–4,600 observed, the upstream cells 1,000–1,600 against
2,500–3,900. The observed corridor has the same head — S791 6–10 m/s from
07:15 to 08:30 with S790 at 10–14 — but 75 minutes later and at two to three
times the simulated speed, and its second queue, White Bear Ave to T.H.61
(S1068/S1947/S1069 at 5–9 m/s 07:25–08:30, the T.H.61 NB entrance 53062592
at 1,719 veh/h in 07–08) is buried under the front from downstream, which
passes S1069 at 06:30.

Order in which the entrances starve (good seeds), by the front's arrival:
40648744 (inside the standing queue from before 06:00; delivered 0.73,
0.71–0.78, the only entrance under 0.9 besides 18207436; a lane-change
entrance with nothing driven, 450 m before the weave's start, 889 veh/h mean
and 1,148 in 07–08, inferred by conservation — no detector) → T.H.61
53062592 (06:25; 1.00 — its three-edge ramp stores its queue) → the C-D
re-entry (06:35; 1.00) → Ruth St 745524613 (06:35; 0.94) → McKnight Rd
178547099 (06:40–06:45; 0.87) → Hudson Rd 18207653 (06:45–06:50; 0.91) →
Hudson Rd 18207436 (06:50–07:00; 0.48) → the mainline entry (07:10; 0.55).
Along the corridor the delivered fractions are 0.48, 0.91, 0.87, 0.94, 1.00,
1.00, 0.73, 0.97: the two lowest are the first entrance the front reaches
from downstream and the one it reaches last from upstream, whose scripted
merge then waits 11 minutes for a gap.

Worst stations by RMSPE contribution: S1066 McKnight Rd, S1065 Hudson Rd
and S1067 Ruth St (0.87–0.88; 9.1–9.2 % of the summed error each), the
upstream half — observed free at 29–31 m/s all morning, simulated at
0–1 m/s from 06:50–07:10 on; S1069 T.H.61 next (0.86). The shares are flat
(0.046–0.092) because every cell but S97 is wrong in the same way; S97 is
the only cell that matches (0.15) and the only one whose flow matches the
observed within the recovery's error in 07–09. The RMSPE cannot fall far
below 0.75 while the corridor is one standing queue; the head is the whole
error.

**The single next lever: the discharge of the corridor's last 850 m** — the
40648744 entrance at 9.88 km into the T.H.52 weave at 10.33–10.72 km
(769818012 entering 1,267 veh/h mean, 18207598 taking 17–21 %). Evidence:
the head is there from the first scored window in all 19 running seeds
(S791 5.2, S790 5.3, S97 18.8 m/s at 06:00) and it is where the locking
seed stopped inside the warm-up; the S790 → S97 cross-sections carry
2,600–3,300 veh/h against 4,100–4,600 observed in every hour; everything
upstream — the mainline's 45 % never inserted, 18207436's 0.48, the 0.78
RMSPE — is the tail of that one queue, reaching the entry at 07:10; and the
observed corridor has the same head 75 minutes later at two to three times
the speed, so the model has the right place and a discharge one third to
one half short. What the files cannot decide is which of the two movements
holds it: the 40648744 merge (the S791 cell ending at that ramp is the
slowest cross-section of the corridor at 2–3 m/s, while the weave's own
entrants wait only 7 s in and 11 s out) or the weave's conflict itself. That
is the one measurement to take next, on the VM from a good seed's
trajectories: the 50 m × 1 min standstill map of the first 30 minutes
(`diag_lock.py.txt`), which places the first standstill between 9.88 and
10.72 km and names the movement; the design choice that follows (a scripted
or weave-tracked 40648744, or the T.H.52 rule) is that measurement's, not
this note's. Not the lever, with the numbers that say so: the C-D
re-entry's 442 veh/h (1.00 delivered in all 19); the scripted merges'
parameters at Hudson and McKnight (their waits are the front's arrival at
06:45–07:00, 6 km behind the head); the downstream boundary (S97 matches
observed speed in all 19; the schedule's floor is 11.8 m/s and S97 never
falls below 13). Open beside it, cheap to settle from the same trajectories:
the S1063 cell's recovered 06–07 flow is 2,100 veh/h against the 2,990 veh/h
demand while the cell is free-flowing all hour (1,060 in the locking seed
with 50 free minutes); either the recovery's bias or an entry inserting
below its demand — a per-window crossing count at S1063 decides it.

### 11b. The first hour, watched at 50 m × 1 min (VM I, 2026-09-24, block 3)

Two 60-minute, 4-seed runs of the corrected weave scenario with the
exit-side runner (commit 65826a2; scenarios `…_weave_head60.yaml` as is and
`…_weave_head60_scripted.yaml` with the 40648744 entrance on the scripted
merge; artifacts and standstill maps under
`artifacts/mndot_rounds/weave_2026-09-24/first_hour_*`), the measurement
§11a asked for:

| variant | departed, 4 seeds (lowest) | first standstill (50 m × 1 min, mean speed < 2 m/s) | stopped bins at minute 60 (100 m × 5 min) | given-up exits |
|---|---|---|---|---|
| as is (40648744 lane-change) | 0.989 (0.984) | minute 11, 10.25–10.30 km, lane 0; lane 1 from minute 12 at 10.20 km | 6.8–9.8 km | Ruth St 4.2 % (above the 2 % threshold), T.H.52 within |
| 40648744 scripted | 0.988 (0.986) | minute 14, 10.30 km, lane 0; lane 2 from minute 15 at 10.10 km | 7.0–9.7 km | Ruth St 3.3 % (above), T.H.52 within |

So the head forms in the 230 m between the end of the 40648744 acceleration
lane (10.198 km) and the T.H.52 gore (10.43 km), in lane 0 first, about ten
minutes into the peak, and spreads upstream through the hour; the whole
first hour still departs 0.99 of its demand — the 0.743 of the four-hour
battery is that queue accumulated. Scripting the 40648744 merge delays the
head by three minutes and moves nothing else, so the merge model of that
entrance is not the lever; what stands in those 230 m is lane 0 carrying the
40648744 entrants plus the T.H.52 entrants' approach and the exiters'
target lane at once. Ruth St's give-ups above the threshold are a second
finding: the 136 m section rejects 3–4 % of its exiters in the first hour.
Next: a fixture with both entrances (the local twin, docs/WEAVE_MODEL_PLAN.md)
and a rule for the stretch between an entrance and a weave.


**VM J (2026-09-24, block 3): the corrected scenarios (config hash
53e4b208fd1d) with the cross-edge 500 m vacate window (commit cf2e4f6).**
Slice, 4 seeds: departed 0.968 (lowest 0.966), no starved ramp
(`artifacts/mndot_rounds/weave_2026-09-24/slice_corrected_inputs_cross_edge_window_cf2e4f6.json`) — below the
0.979 of the 150 m window. Battery, 20 seeds
(`battery_corrected_inputs_cross_edge_window_cf2e4f6.json`, per-seed shares in
`battery_cross_edge_window_per_seed_departed.txt`): departed **0.859**
(every seed between 0.844 and 0.889 — **no seed locks**), speed RMSPE
**0.706** (95 % interval 0.704–0.709), GEH < 5 on **0.080** of 840
link-hours (the first non-zero share on this corridor), "wave speed"
6.6 km/h (the queue front, still), 14 % of the planned vehicles never
departed, 31 collisions over 20 seeds; given-up exits Ruth St 121 of
19381 (0.6 %), T.H.52 313 of 68996 (0.5 %), both
within the 2 % threshold. Scoring 114 s on six workers.

Reading: the window that asks through traffic to leave the weave lane
500 m ahead — where lane 0 still moves — is the second change that moves
the four-hour corridor (0.743 → 0.859 departed, RMSPE 0.78 → 0.71, and
the locking seed is gone), even though it lowered the 35-minute slice a
little. Not reproduced: a seventh of the demand still queues at the
boundary and the speeds upstream are still far from the observed
free-flow half of the morning. The next change in flight is a
speed-aware exit-side acceptance (a fast exiter no longer drops in behind
a queue head it cannot brake for).

**VM K (2026-09-24, block 3): the same corrected scenarios (config hash
53e4b208fd1d) under the speed-aware weave acceptance (commit 20f9fcb).**
Slice, 4 seeds: departed 0.968 (lowest 0.965), no starved ramp, one
collision on one seed (t = 1275.5 s, the added lane of on-ramp edge
43917735#1), given-up exits 9 of 992 and 16 of 1,853 (0.9 % each)
(`artifacts/mndot_rounds/weave_2026-09-24/slice_corrected_inputs_speed_aware_20f9fcb.json`)
— the same 0.968 as VM J. Battery, 20 seeds
(`battery_corrected_inputs_speed_aware_20f9fcb.json`, per-seed shares and
collisions in `battery_speed_aware_per_seed_departed.txt`): departed
**0.855** (19 seeds between 0.846 and 0.902; one seed, 677105600768189526,
at **0.743** where VM J had 0.865 — its two upstream on-ramps departed 787 of
1,583 and 617 of 995 and 22,688 vehicles arrived, the signature of a queue
reaching the corridor's upstream end; whether a lane stood at 0.0 m/s is
not diagnosed, the round shipped no trajectories), speed RMSPE **0.709**
(95 % interval 0.699–0.718), GEH < 5 on **0.079** of 840 link-hours
(0.059–0.098), "wave speed" 6.5 km/h (the queue front), 15 % of the
planned vehicles never departed, **15 collisions over 20 seeds** (VM J: 31)
on two on-ramp acceleration lanes and two mainline edges; given-up exits
Ruth St 157 of 18,997 (0.8 %), T.H.52 692 of 68,872 (1.0 %), both within
the 2 % threshold. Scoring 2,247 s.

Reading: the speed-aware acceptance halves the collisions and leaves the
corridor where VM J put it — departed 0.855 against 0.859, RMSPE 0.709
against 0.706, the GEH share unchanged — with the exiters giving up a
little more often (0.8 / 1.0 % against 0.6 / 0.5 %), as the fixtures
predicted. The one seed at 0.743 is new: on VM J every seed stayed above
0.844. Not reproduced. The next derivation in flight is the abreast state
(docs/WEAVE_MODEL_PLAN.md, WP-53): the queue vehicle beside a halted, due
exiter, which a trace of the fixture give-ups names as 34 of 44.

**VM L (2026-09-24, block 3): the 0.743 seed re-run for its standstill map
(runner 6e7757e, the same physics as 20f9fcb).** The weave scenario's first
five replicates (`artifacts/mndot_rounds/weave_2026-09-24/battery_seed5_speed_aware_6e7757e.json`):
departed 0.862 / 0.858 / 0.856 / 0.870 / **0.743** — seed 677105600768189526
reproduces VM K's share to the third decimal (the round is deterministic per
seed), speed RMSPE 0.790 against 0.698–0.714 on the other four. The
standstill maps were not produced: the battery prunes every replicate's
trajectory but the first seed's, and the diagnostic stage found no file
(`--keep-trajectories` added to the stage). The seed's read stands as in VM
K's record — upstream ramps starved, a queue at the corridor's upstream end,
a lane at 0.0 m/s not shown — until the stage is re-run.

**VM M (2026-09-24, block 3): the same corrected scenarios (config hash
53e4b208fd1d) under the crossing-pair rule — an exiter inside the forced
zone yields to an entrant already halted at the auxiliary lane's end
(commit 585e588, `exiter_yields` on).** Slice, 4 seeds: departed 0.966
(lowest 0.959), two collisions, given-up exits 9 of 995 and 22 of 1,820
(0.9 / 1.2 %)
(`artifacts/mndot_rounds/weave_2026-09-24/slice_corrected_inputs_exiter_yields_585e588.json`).
Battery, 20 seeds (`battery_corrected_inputs_exiter_yields_585e588.json`,
per-seed shares, collisions and yield counts in
`battery_exiter_yields_per_seed_departed.txt`): departed **0.836** — 19 seeds
between 0.843 and 0.899 and **one seed, 6904272788004776631, at 0.356**
(0.852 under VM K), every on-ramp starved, 18,259 exiter-yield
vehicle-steps against 769–3,512 on the other seeds: a lock under the new
rule. The seed VM K lost (677105600768189526, 0.743) reads 0.865 here.
Speed RMSPE **0.716** (0.695–0.736), GEH < 5 on **0.077** of 840 link-hours
(0.058–0.097), "wave speed" 6.6 km/h, **19 collisions over 20 seeds** (VM
K 15), given-up exits Ruth St 112 of 18,750 (0.6 %), T.H.52 626 of 66,790
(0.9 %) — the lowest shares of the series. Scoring 3,024 s.

Reading: the rule does what it was derived for (fewer exits given up on
both sections) and locks one seed of twenty on the four-hour corridor, a
failure the 29-run fixture grid did not show. A rule that locks the
flagship corridor cannot ship on: `exiter_yields` goes back to 0 by default
(hash-neutral; the golden never bound on it) until the yield is bounded
against the chain WP-53 named — the halted entrant that is never freed.
The lock's standstill map is the next round (the seed's spawn index is
12; the diagnostic stage keeps its trajectory now). Not reproduced.

**VM N (2026-09-24, block 3): the locked seed's standstill map (runner
0362419 — the physics of 585e588 with `exiter_yields` still on; config hash
e6106fd41b1b, the label moved with the keys WP-55 added, every per-seed share
matches VM M).** The weave scenario's first thirteen replicates with their
trajectories kept
(`artifacts/mndot_rounds/weave_2026-09-24/battery_thirteen_seeds_exiter_yields_0362419.json`):
twelve seeds depart 0.843–0.899 and seed 6904272788004776631 **0.356** again,
speed RMSPE 0.899 against 0.686–0.714. Its maps
(`locked_seed_exiter_yields_standstill_50m_1min_lanes.txt`, `_100m_5min.txt`):
the first cells under 2 m/s appear at **minute 18 at 10.20–10.35 km in lane
0** and at minute 19 at 10.00–10.15 km in lanes 1 and 2 — the T.H.52 weave,
where every earlier head formed — and never clear: 5–14 cells per lane stand
there through minute 50, at minute 50–55 the standstill jumps back to 9.05 km
in lane 1, by minute 60–65 five lanes stand between 4.4 and 10.25 km, at
minute 70 the 100 m map reads its first window (6.5–10.7 km stopped), and
from **minute 110 to the end every cell of every lane from 0.0 to 10.7 km
reads 0.0 m/s** — 930 / 1,055 / 1,005 / 480 / 115 cells at rest in lanes 0–4,
a total gridlock for the last 130 minutes. That is the chain WP-53 named,
read on the corridor: an exiter yielding to a halted entrant that is never
freed, the vehicles behind the exiter yielding in turn. On the twelve seeds
that do not lock the last hour's 100 m windows show 8–60 stopped bins with
their minimum-speed bin anywhere from 0.0 to 9 km
(`thirteen_seeds_exiter_yields_standstill_100m_5min_tails.txt`): stop-and-go
queues that reach the corridor's upstream boundary in the last hour on every
seed — the 14–15 % of demand that never departs on the healthy seeds is the
boundary backlog of a corridor whose queues fill it, not a lock.

Reading: the yield's default is off (51389f5); a bounded hold (WP-58, in
flight) is what could bring it back. The healthy seeds' last hour says where
the four-hour corridor's remaining gap is: the queue grows from the T.H.52
weave back to the boundary within the peak, so the upstream half is jammed
where the observation says free flow. Not reproduced.

**Where the corridor's queue comes from (WP-59, 2026-09-24, block 3, analysis only).**
No run and no trajectory; inputs `observations.json` (obs), `artifacts/demand_mndot_i94_wb_stpaul.json` (identical to the weave scenario's `inflow`), VM K's
`artifacts/mndot_rounds/weave_2026-09-24/battery_corrected_inputs_speed_aware_20f9fcb.json` (battery), `artifacts/idm_capacity_probe_mnfleet_4l.calibration.json`,
`artifacts/idm_i24_capacity_equilibrium.json`, `artifacts/fd_mndot_i94_wb_stpaul.json`. The battery writes no simulated flow: its `geh.pooled_values`
(20 seeds × 14 stations × 3 hours, station-major; every seed's pass fraction reproduces) are inverted per seed for the simulated count, root below the
observed one — the root that rises with each seed's directly counted `throughput_veh_h` (r 0.63–0.999 at all 14 stations; the other root falls) and the
only one under the fleet's capacity at S97 06:30–07:30 (the other is 2,110 veh/h/lane). The scored hours are **06:30–07:30, 07:30–08:30, 08:30–09:30**
(`ObservedCorridor.hourly_link_flows` aligns hours to 05:30 and keeps whole hours after the warm-up); §11a's `vmh_flows.py.txt` paired them with the
06:00–09:00 hours, so its "flow sim / obs" columns are half an hour off and are superseded here. Geometry (docs/WEAVE_MODEL_PLAN.md): S790 (10.13 km) sits in
the 40648744 acceleration lane, edge 40648738 has 3 lanes over 229 m from that lane's end (10.198 km) to the T.H.52 gore, the weave edge 51388891 has 3 +
the auxiliary over 305 m, S97 (11.07 km) is past the T.H.52 and Jackson St exits.

*(1)–(3) The weave's flows, veh/h (per lane).* "Discharge" is 06:30–07:15, while the observed head sits at the weave (S790 13.2–14.9 m/s, S97
20.3–25.3 m/s; a nine-day mean, day-to-day sd 157–918 veh/h at S790, obs `spread`). Simulated: mean of 20 seeds (range over seeds and hours).

| cross-section | lanes | obs 06:30–07:30 / 07:30–08:30 / 08:30–09:30 | obs discharge | simulated, same hours | GEH < 5 |
|---|---|---|---|---|---|
| S790 | 3 | 4,911 (1,637) / 4,065 / 4,119 | 4,989 (1,663) | 3,259 (1,086) / 3,231 / 3,182 (2,664–3,394) | 0 of 60 |
| S790 + T.H.52 NB entrance (rnd_91040) | 4 | 6,208 (1,552) / 5,420 / 5,319 | 6,265 (1,566) | not in any artifact | — |
| S97 | 3 | 4,586 (1,529) / 4,120 / 3,886 | 4,621 (1,540) | 3,121 (1,040) / 3,263 / 3,197 (2,957–3,359) | 0 of 60 |

| capacity per lane (artifact) | veh/h/lane |
|---|---|
| corridor EIDM block, 4-lane straight road, T 1.285 / 1.360 s; the scenario's T 1.322 s (`idm_i24_capacity.json`) lies between (probe sidecar) | 1,670 / 1,676 |
| closed-form equilibrium of the scenario's population, heterogeneous / homogeneous (`idm_i24_capacity_equilibrium.json`) | 1,886 / 1,986 |
| fitted FD q_max 0.536 veh/s, 95 % CI (`fd_mndot_i94_wb_stpaul.json`) | 1,930 (1,907–1,946) |

*(4) Demand against the observed corridor* (the artifact's entry, `inflow_steps` and `exit_fraction_steps` propagated in corridor order, no travel time):

| cross-section | observed, three hours | demand-implied | difference |
|---|---|---|---|
| S1063, the boundary (never below 31.4 m/s 06:00–09:30) | 3,732 / 3,596 / 2,853 | identical | 0 |
| S1064 … S1069 | | | within 24 veh/h |
| S1070, S1948 (carried residuals −317, −262 until the Mounds exit) | 5,041 / 3,557 / 3,859 at S1070 | 5,485 / 4,056 / 4,069 | +119 to +523 |
| S791 (the +366 residual moved onto 40648744) | 4,426 / 3,433 / 3,526 | 3,881 / 2,611 / 3,090 | −436 to −822 |
| S790 / S97 | 4,911 / 4,065 / 4,119 and 4,586 / 4,120 / 3,886 | 4,757 / 3,918 / 4,080 and 4,473 / 4,008 / 3,857 | −39 to −153 / −29 to −113 |

Delivered (battery `per_seed[].insertion`, four hours with warm-up): mainline 9,501 of 12,182 (0.780), 40648744 0.752, T.H.52 769818012 0.837,
18207436 0.701, every other entrance ≥ 0.98. 40648744 carries 889 veh/h over 05:30–09:30 (876 / 1,307 / 990 in the scored hours) against its passage
loop's 552 over the same span (§11 review; a cache figure, not in obs): the level at S790 is right, but about 340 veh/h of it arrives as merging traffic
450 m before the weave instead of through S791.

*(5) The observed queue.* First 5-min window under 20 m/s: S790 and S791 06:30, S792 06:35, S1069/S1070/S1948 06:40, S1947 06:45, S1068 07:15, S1067
07:25 (lowest 13.6 m/s at 07:35); S1066 never below 23.6, S1065–S1063 never below 29.5. S97 holds ≥ 20.3 m/s to 07:10, then 11.8–19.6 m/s to 09:20 —
a restriction past S97 joins, so after 07:15 the observed counts bound the weave's capacity from below. The observed queue starts at the weave at 06:30
and its tail stops between S1067 (4.97 km) and S1066 (4.06 km), 5.4–6.3 km behind the weave's start; the upstream 4 km stay free. At 05:40–05:50, when
the simulated head forms (minutes 11–18, §11b and VM N), S790 carried 3,752–3,776 veh/h at 25.8–25.9 m/s, above the simulated discharge.

**Answer: capacity-short at the weave, not demand-high.** In 06:30–07:30 the simulated corridor carries 3,259 veh/h at S790 against 4,911 observed
(−1,652, 34 %) and 3,121 at S97 against 4,586 (−1,465, 32 %); over the three hours −1,141 (26 %) and −1,003 (24 %). The demand implies 1–4 % less than
observed at S790 and S97 and equals the free-flowing count at the boundary.

Reading. The weave must discharge about 4,990 veh/h through the 3-lane edge 40648738 (1,663 per lane) with about 1,280 T.H.52 entrants on top — 6,270 veh/h
into the 4-lane section — against 3,180–3,260 now. Two terms, in order: (i) the lane-change dynamics of edge 40648738 and the weave (LC2013's strategic
and cooperative changes plus the runner's weave rules) cost 35 % of the fleet's own straight-road capacity: 3,259 is 1.95 lanes' worth of 1,670, and the
hypothesis that lane 0 there carries almost nothing (the head forms in lane 0 first, §11b, VM N) is the next measurement — per-lane 1-min crossing counts
at 10.15, 10.30, 10.45 and 10.70 km from a good seed's first hour; (ii) the EIDM fleet's straight-road capacity (1,670–1,676) equals the observed
discharge with no headroom where the closed form gives 1,886 and the FD 1,930, so even a lossless weave would run at capacity — §10's IDM-or-EIDM
decision bounds the result too. Not determinable from committed artifacts: the simulated flow into the weave and at its exit (no station inside it; the
battery keeps ramp deliveries as four-hour totals), per-lane flows, and the weave's real capacity above 6,265 veh/h (S97 itself congests
after 07:15). The battery should write the per-station per-hour simulated and observed counts and per-ramp per-hour deliveries; this note had to invert GEH.

Method (`artifacts/mndot_rounds/weave_2026-09-24/wp59_bottleneck_discharge.py.txt`, committed JSON only, run from the repo root): the battery's
`geh.pooled_values` are ordered seed, then station by position, then hour (`scripts/corridor_battery.py` pools each seed's list;
`validation.observed.ObservedCorridor.hourly_link_flows` emits station then time, stations sorted by `x_m`), so they reshape to 20 × 14 × 3;
each GEH = √(2(m − c)²/(m + c)) is solved for the simulated count m against the observed c, and the root below c is kept — the only one that rises
with each seed's counted throughput (r 0.63–0.999 at all 14 stations) and stays under the fleet's capacity at S97 in the first hour.

**VM O (2026-09-24, block 3): per-lane crossing counts through the T.H.52 weave, the measurement WP-59 named.**
The corrected weave scenario's first hour (05:30–06:30), four seeds, every trajectory kept, current defaults (the physics of 20f9fcb; config
hash ec9667bc60b4; departed 0.971, lowest 0.968); per seed, per lane, per 5-min window, the flow and crossing speed at five positions
(simulation x = data x, offset 0) — `artifacts/mndot_rounds/weave_2026-09-24/first_hour_lane_crossings_20f9fcb_physics.txt`
(script `diag_lanes.py.txt`, artifact `first_hour_lanes_validation_5874c2f.json`). Hourly totals, the four seeds:

| position | lanes | vehicles in the hour | per lane, right to left (seed 134183728835869882) |
|---|---|---|---|
| 9.90 km, just past the 40648744 entrance (9,881) | 3 | 2,753 / 2,770 / 2,770 / 2,771 | 715 / 745 / 1,293 |
| 10.15 km, at S790 (10,128), the entrance's added lane still open | 4 | 3,054 / 3,110 / 3,084 / 3,101 | 170 / 677 / 854 / 1,353 |
| 10.30 km, the 3-lane edge before the T.H.52 gore (10,330) | 3 | 3,042 / 3,088 / 3,060 / 3,070 | 661 / 969 / 1,412 |
| 10.45 km, inside the weave | 4 | 3,998 / 4,024 / 4,026 / 3,990 | 716 / 922 / 962 / 1,398 |
| 10.70 km, before the exit to 18207598 (10,722) | 4 | 3,978 / 4,003 / 3,998 / 3,967 | 1,022 / 681 / 869 / 1,406 |

Reading, from the 5-min windows (seed 134183728835869882; the other three agree to within about 5 %):

1. *WP-59's lane hypothesis is refuted.* Lane 0 of the 3-lane edge before the gore carries 641–673 vehicles in the hour on every
   seed, 22 % of the edge, not almost nothing. The lane that carries least is the 40648744 entrance's added lane at S790 (170–207).
2. *The weave itself saturates at about 4,000 veh/h.* Inside it (10.45 km) the four lanes carry 3,816–4,992 veh/h per window from
   minute 10 on at 5–19 m/s; at its downstream end (10.70 km) 3,804–4,968 at 9–21 m/s; over the hour 3,967–4,026 vehicles on the four
   seeds — about 1,000 veh/h per lane, 60 % of the fleet's straight-road capacity (1,670). Into the real weave S790 plus the T.H.52 entrance carried 5,137 veh/h at 25.9 m/s at
   05:40 and 6,275 at 06:25 (`observations.json`, windows 2 and 11).
3. *It congests at its downstream end first.* At 10.70 km the auxiliary lane (lane 0, entrants and exiters) runs at 11.2 m/s with
   1,092 veh/h in minutes 5–10, and lane 1, the lane both movements cross, carries the least of the four there over the hour on every
   seed (669–715 vehicles; 492–984 veh/h per window from minute 10 at 12–16 m/s); inside the weave lanes 0–1 are at 8 m/s by minutes 10–15; the standstill head (under 2 m/s) appears upstream at
   10.20–10.35 km at minute 18 (VM N). At S790 the simulation carries 3,492 veh/h at 8 m/s in minutes 15–20, where the real road
   carries 3,752 at 25.8 m/s.

So the corridor's gap is the T.H.52 weaving section's own capacity — the object of the T.H.52-at-capacity fixture and block 3's
item 1 — and within it the crossing lane: the fixture target is the right one (WP-60 then found the old T.H.52 fixtures congest at the section's entry end and run at a 39.4 m/s limit where the corridor's section is 24.6 m/s; WP-61 built the corridor's section as a test, docs/WEAVE_MODEL_PLAN.md). Not reproduced.

**VM P (2026-09-24, block 3): how the corridor's T.H.52 exiters arrive at the gore** (WP-61's check; the same first-hour run as VM O,
re-run — its per-lane counts reproduce VM O's to the vehicle; `artifacts/mndot_rounds/weave_2026-09-24/first_hour_exiter_arrival_20f9fcb_physics.txt`,
script `diag_exiters.py.txt`). Trajectories record corridor edges only, so an exiter to 18207598 is a vehicle whose track ends within 40 m
of the section's end (10,731.6 m) and began upstream of the section: 782–806 per seed in the hour (the last-x histogram's 10.70 km bin holds
1,050–1,073, the rest T.H.52 entrants who exit); 939–987 T.H.52 entrants are first seen inside the section. Lanes are SUMO indices, 0 the
rightmost; in the section lane 0 is the auxiliary lane and lane 1 continues the approach's lane 0. The four seeds, both periods of the hour:

| position | where the exiters are |
|---|---|
| 10.30 km, the 3-lane approach 130 m before the section | rightmost lane 55–68 %, middle 28–35 %, left 1–15 % |
| 10.432 km, 5 m into the section | auxiliary lane 9–23 %, lane 1 45–53 %, lane 2 26–39 %, lane 3 1–13 % |
| 10.717 km, 15 m before the exit | auxiliary lane 94–99 % |

They reach the auxiliary lane a median 231–239 m before the section's end (the section is 305 m); 21–25 % only within its last 67 m and
8–9 % within its last 30 m. So a third to two fifths of the exiters are one or two lanes left of the rightmost approach lane 130 m before
the section, and every one of them must cross lane 1 inside it — the lane VM O found carrying the least at the exit end. That is what a
pre-positioning rule would act on (WP-62: measured off on the fixtures, whose approach puts 76–89 % of the exiters in the rightmost lane).

**VM Q (2026-09-25, block 3): WP-62's `exit_prepare` rule on the corridor** — the corrected scenarios with `weave_params
{exit_prepare: 1.0}` on both weave sections (runner a445cfa; config hash 05d8ba043b11; its first battery written with WP-63's
labelled station-hour table), against VM K (20f9fcb; the same physics with the rule off). Slice, 4 seeds
(`artifacts/mndot_rounds/weave_2026-09-24/slice_exit_prepare_a445cfa.json`): departed 0.973 (lowest 0.972) against 0.968 (0.965),
RMSPE 0.464 against 0.463, two collisions against one. Battery, 20 seeds (`battery_exit_prepare_a445cfa.json`):

| | VM K, rule off | VM Q, rule on |
|---|---|---|
| departed, mean (lowest) | 0.855 (0.743) | 0.857 (0.667) |
| speed RMSPE (95 % interval) | 0.709 (0.699–0.718) | 0.706 (0.692–0.719) |
| GEH < 5, share of 840 link-hours (95 % interval) | 0.079 (0.059–0.098) | **0.143 (0.109–0.177)** |
| collisions over 20 seeds | 15 | 15 |
| given-up exits Ruth St / T.H.52 | 157 of 18,997 / 692 of 68,872 | 146 of 19,742 / 479 of 68,093 |
| S790, 06:30–07:30, simulated mean (observed 4,911) | 3,259 (GEH inverted, WP-59) | 3,328 (3,239–3,484, the table) |

Paired by seed, the departed share moves +0.002 (15 of 20 seeds up). The rule nearly doubles the link-hours within GEH 5 on the
seeds it helps (per-seed shares 0.071–0.262 on 17 seeds against 0.048–0.238 with it off) — and **three seeds collapse late**:
4910985839736976611 departs 0.667 (0.855 off) with S790 carrying 3,307 / 1,545 / 1,144 veh/h in the three scored hours,
6134032994440706937 0.749 (0.846) with 3,368 / 2,570 / 1,152, 7382187975121682178 0.771 (0.851) with 3,367 / 3,367 / 495. VM K's
0.743 seed reads 0.882. So the rule stays off by default: it raises the typical seed and locks the corridor in the later hours on a
seventh of them. The lock's standstill map is VM R (seed 6134032994440706937, spawn index 9).

**VM R (2026-09-25, block 3): the standstill map of a seed `exit_prepare` collapsed** (runner ee79450, the weave scenario with
`weave_params {exit_prepare: 1.0}` via the diagnostic stage's new `--diag-weave-params exit_prepare=1.0`; the first ten replicates with
trajectories kept, `artifacts/mndot_rounds/weave_2026-09-24/battery_exit_prepare_ten_seeds_ee79450.json`). Every per-seed share
reproduces VM Q's; seed 6134032994440706937 departs 0.749 again with S790 at 3,368 / 2,570 / 1,152 veh/h. Its maps
(`exit_prepare_collapsed_seed_standstill_50m_1min_lanes.txt`, `_100m_5min.txt`):

1. *The head forms at the T.H.52 approach as on every seed:* the first cells under 2 m/s at minutes 16–19, 9.95–10.20 km, lanes 0–2,
   standing there on and off until minute 50.
2. *The queue grows back through the corridor:* at minute 55–65 it reaches the stretch the T.H.61 entrance widens (on-ramp 53062592
   at 7.37 km adds two lanes, SUMO indices 0–1, which lead only to off-ramp 18207912 at 8.50 km; lane index 4, which the map shows only
   from 7.35 to 8.45 km, is the leftmost through lane of that 5-lane stretch — corrected by WP-66, which first recorded it as the added
   lane), by minute 120 the boundary.
3. *Then the lock moves downstream of itself, to 8.45–8.50 km:* from minute 155 the stopped extent's downstream end is 8.45–8.50 km
   in every lane (it had been 10.0–10.3 km), and the cells upstream of it go to 0.0 m/s — the 5-lane stretch's leftmost lane all 115
   cells at 0.0 from minute 165, every lane at 0.0 over 0–8.5 km from minute 190 to the end (715 / 855 / 855 / 415 / 115 cells).

So this seed's collapse ends at the gore of a weaving stretch the weave model does not cover: the T.H.61 → 18207912 two-lane
auxiliary connection (1,135.6 m; an HCM one-sided weave with two weaving lanes, docs/WEAVE_MODEL_PLAN.md WP-66) — the lane-end state the exit-side derivation removed from the two weave sections
(an exiter held by SUMO at the end of a lane its route does not continue on, stopping the lanes beside it), met here once the T.H.52
queue has reached it. Not diagnosed per vehicle (the round kept trajectories on the VM only). Not reproduced.
