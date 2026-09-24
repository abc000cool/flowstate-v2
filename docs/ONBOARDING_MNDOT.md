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
().
Battery, 20 seeds (, per-seed
shares in ): departed **0.743**
(19 seeds between 0.696 and 0.799; one seed collapses to 0.230 with three
collisions and 152,153 deferred forced changes at the Ruth St section), speed
RMSPE **0.782** (95 % interval 0.760–0.804; 0.963 the round before), GEH < 5 on
0.000 of 840 link-hours, "wave speed" 6.8 km/h (the queue front,
still), 26 % of the planned vehicles never departed and eight entrances
still starve at the peak. Given-up exits (the re-scored artifact
, ): Ruth St 70 of 8838 reached
exiters (0.8 %), T.H.52 206 of 63782 (0.3 %) — both within the
2 % threshold, so the exit flows are honest. Scoring 137 s on six workers.

Reading: the exit-side rule is the first change that moves the four-hour
corridor — from 0.247 to 0.743 of demand departed and RMSPE from 0.96 to
0.78 — but the corridor is **not reproduced**: a quarter of the demand still
queues at the boundary, the entrances still starve at the peak, one seed in
twenty locks, and no link-hour meets GEH 5. The next levers are the
entrances that still starve (the scripted merges 18207436 and 178547099, the
Ruth St weave) and the seed that locks; the per-seed metas are in the
round's archive.
