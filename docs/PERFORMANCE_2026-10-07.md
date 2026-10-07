# Performance pass, 2026-10-07: same outputs, less time and memory

The goal was to make runs cheaper without changing any result. Every output file of 28
seeded fixture runs is byte-identical to `HEAD` (571b9e4), every config hash is unchanged,
and no golden was touched. The simulator spends 9–12 % less time on the corridor workloads.
A simulation worker no longer has a memory spike at the end of a run that grows with the
number of trajectory rows. Scoring one replicate now peaks 8–19 % lower.

All measurements were taken on the owner's laptop (macOS arm64, Python 3.12, SUMO 1.27.1
via libsumo, pandas 3.0.5, pyarrow 25.0.1, numpy 2.5.2). Simulation runs were limited to
10 simulated minutes or less, with one SUMO process at a time. Nothing read
`data/i24motion/processed/*` or a `runs/**` trajectory.

## 1. Baseline (HEAD 571b9e4)

**Where a corridor step's time went.** Each named function was wrapped with a
`perf_counter` accumulator (`cProfile` gave the same ranking). The run was the 10-min slice
of `mndot_i94_wb_stpaul_weave_slice_measured` (5 measured zones, lane-end give-up), seed 42:

| Share of wall time | Item |
|---|---|
| 50 % | `simulationStep` (SUMO itself) |
| 16 % | `_measured_step`: 0.40 s of it was its own pass over **every vehicle in the network**, once per zone per step. The vacate and exit-prepare rules and the lane-end give-up made more passes of the same kind. |
| 9 % | `vehicle.getAllSubscriptionResults` (libsumo builds the Python dicts) |
| ~7 % | Inline per-step Python in `run_micro`: corridor filter, `xs`/`speeds`, fuel, and a trajectory capture that appended ten values per vehicle per step into Python lists |
| ~8 % | Trajectory row-group flushes (`pa.array` from Python lists) and the end-of-run Edie frame |
| 7 % | Network build (netconvert; once per replicate) |

On `i24_replica_flow_speedcal` (no runner-driven merges), SUMO took 58 %, subscription
results 14 %, flushes 7 %, and the inline capture code about 14 %.

**Real-time factors** (`sim s / wall s`, including network build), compared with the
§3.4 targets:

| Case | HEAD | Now | §3.4 target |
|---|---|---|---|
| `ring_sugiyama` (600 s) | 1,734× | 1,754× | ≥ 50× |
| `ring_sugiyama` + 1 FollowerStopper AV | ~1,550× | ~1,650× | — |
| `corridor_10km`, 5 sim-min | 719× | 744× | ≥ 5× (20-min form not run locally) |
| I-24 base, 10 sim-min | 188× | 207× | — |
| I-24 measured, 10 sim-min | 157× | 175× | — |
| I-94 measured slice, 10 sim-min | 144× | 163× | — |
| I-94 weave slice, 10 sim-min | 169× | 187× | — |

**Simulation worker memory.** At the end of a run the old writer still held a copy of every
row's `(t, x, v)` (24 B/row). It then concatenated that into a DataFrame (24 B/row more), and
the Edie `np.add.at` added about 24 B/row of temporaries. The Python-list row buffer added
about 125 MB on top. For the 80–106 M-row four-hour corridor replicates, that is about
5.5–7.5 GB **per simulation worker** at its last second. With 20 concurrent seeds, that is
110–150 GB on the 128 GB n2-standard-32. Only staggered finish times kept this under the limit.

**Scoring memory** (`validation.battery.analyse_replicate`, fresh process, synthetic
contract-schema replicate with time-major 500k-row groups):

| Rows | Peak RSS above import floor, HEAD |
|---|---|
| 2.09 M | 265 MB (133 B/row) |
| 4.18 M | 438 MB (110 B/row; slope 83 B/row) |

`tracemalloc` breaks the numpy part down as follows: the frame is 32 B/row. Above it,
`compute_metrics` adds 30 B/row, `score_replicate` 35 B/row (a sorted copy of `v` in
`crossing_speeds`), and the wave speed 22 B/row. On macOS the Arrow pool (mimalloc) also
kept ~50 MB of freed batch buffers that numpy could not reuse.

## 2. What changed

All changes are in `packages/microsim/microsim/runner.py` unless noted.

1. **Columnar trajectory capture.** `_TrajectoryWriter.append_step` buffers one step as numpy
   arrays (`veh_id` stays a list). A row group concatenates the chunks. The per-vehicle
   `cols[...].append` loop with numpy-scalar indexing is gone. The flag columns are built from
   id sets, and a flag no vehicle carries is a constant column. The values, row-group
   boundaries and Parquet bytes are the same as before.
2. **Streamed Edie bins.** `_EdieAccumulator` fills the `edges.parquet` bins from each row
   group as it is written, and the whole run's `(t, x, v)` is never kept.
   `_edie_edges_frame(traj, …)` keeps its signature and calls the accumulator once.
   `np.add.at` is unbuffered and adds in index order, so chunked input gives the same additions.
3. **Per-step state from one row list.** The subscription keys are hoisted out of the loop.
   `rows = [results[v] for v in ids]` feeds `speeds`, `xs`, fuel and capture. With ramps,
   the corridor filter and the off-corridor fuel take one pass instead of two. The
   arithmetic and its order are unchanged; the ring unwrap code is untouched.
4. **`_RoadIndex`.** Each step's vehicles are bucketed by road on the first query, in
   `results` order. `_measured_step`, `_weave_step`, `_weave_vacate_step`,
   `_weave_exit_prepare_step`, `_lane_end_step` and `_scripted_merge_step` take it as an
   optional last argument. Each scans only its own roads (`_zone_scan_roads`: exit edges,
   `lane_map` roads, section edges), in the original relative order. A vehicle on any other
   road changed nothing in those loops except one thing: a driven vehicle on an internal
   junction lane gets `in_transit`, which is now set from the section's own `veh`.
   `in_transit` is only tested for membership. The unit tests call these functions without
   the index and get the old code path.
5. **`_downstream_bins`** (per AV per step): `np.bincount` replaces `np.add.at`. Both add
   each weight to its bin in input order starting from 0.0. Agreement was checked bit for bit
   on 3,000 random cases (ties, ego exactly on a vehicle, a vehicle exactly at the horizon,
   ring and corridor).
6. **Scoring** (`packages/validation/validation/metrics.py`, `battery.py`):
   `_ByVehicle.crossing_points` gathers the crossing pairs' speeds through `order` instead of
   taking a sorted copy of the whole `v` column. The value is the same element, so the result
   is identical. The scoring spawn pool's workers (`_score_worker_init`) allocate Arrow
   buffers from the system allocator, which never changes a decoded value. The calling
   process (the API report job) is not switched. `SCORE_WORKER_BYTES_PER_ROW` stays at 160
   until the change is measured on Linux (jemalloc there).

## 3. After

**Interleaved A/B wall time.** Five alternating runs per case compared HEAD packages (an
extracted `git archive` copy put first on `sys.path`) with the working tree, same seed. The
table gives the minimum of five runs; the medians agree within 1 %.

| Case | HEAD | Now | Change |
|---|---|---|---|
| I-24 base, 10 min | 3.198 s | 2.894 s | −9.5 % |
| I-24 measured, 10 min | 3.828 s | 3.437 s | −10.2 % |
| I-94 measured slice, 10 min | 4.180 s | 3.687 s | −11.8 % |
| I-94 weave slice, 10 min | 3.542 s | 3.217 s | −9.2 % |
| `corridor_10km`, 5 min | 0.417 s | 0.403 s | −3.4 % |
| `ring_sugiyama` | 0.346 s | 0.342 s | −1.2 % |

These times include the fixed per-replicate costs (network build 0.13–0.27 s, SUMO start).
In a four-hour run those are amortized, and the per-step share of the saving is about
10–13 %. After the changes, `_measured_step` takes 11 % instead of 16 % on the I-94 slice,
and SUMO plus libsumo take 67–80 % of the remaining wall time.

**Capture path alone.** 200 recorded I-24 steps (134k vehicle rows) were replayed through
the HEAD and the new state, fuel and capture code, including flushes and the edges frame.
It took 988 → 548 ns per vehicle-row. The fuel dict, edges frame and Parquet bytes were
identical.

**Simulation worker memory**, from the replayed capture path over long runs:

| Rows | HEAD | Now |
|---|---|---|
| 1.34 M | 262 MB | 138 MB |
| 5.36 M | 536 MB (slope 68 B/row) | 143 MB (flat) |

Extrapolated to 106 M rows, that is ≈ 7.4 GB → ≈ 0.15 GB per worker. On the 10-min slices,
peak RSS fell from 466 to 359 MB (I-24) and from 437 to 339 MB (I-94).

**Scoring:**

| | 2.09 M rows | 4.18 M rows |
|---|---|---|
| HEAD | 265 MB | 438 MB |
| Gather change only (in-process) | 263 MB | 402 MB |
| Plus system Arrow allocator | 211 MB | 353 MB (−19 %; slope 68 B/row) |

Through the real spawn pool (`analyse_replicates`, 2 workers), the largest child peaked at
609 MB with HEAD and 533 MB now. Traced numpy peak above the frame: `score_replicate` fell
from 35 to 27 B/row. The largest phase is now `compute_metrics` at 30 B/row.

## 4. Identical-output evidence

- **Simulation.** 28 runs: the 12 golden cases; ring with and without a FollowerStopper AV;
  `corridor_10km` 5 min plain, with FollowerStopper, with JAD under a noisy and delayed
  oracle, and with VSL; I-24 with a 5 % PI-saturation AV fleet; I-24 measured and I-94
  measured slices at 5 and 10 min; I-24 base and I-94 weave slices at 10 min (weave,
  scripted, measured and lane-end paths); and three runs forced to 10,007-row row groups.
  Each was run with HEAD's packages and with the working tree, and the following were
  compared:
  - the sha256 of every Parquet file (trajectories, edges, vehicles, journeys);
  - `meta.json` without `wall_time_s` and `realtime_factor` (run-directory paths normalized);
  - `compute_metrics` output;
  - the config hash.

  **28 of 28 are identical.** HEAD run in the tree and HEAD run from the extracted copy also
  agree, so the reference copy is faithful.
- **Scoring.** `metrics.json` and `observed_scores.json` have the same sha256 under HEAD and
  now, for both synthetic sizes, in-process and through the spawn pool.
- **Unit tests.** `tests/test_microsim/test_microsim_streaming.py` pins two claims: chunked
  Edie equals one pass bit for bit, and `_RoadIndex.on` returns exactly the filtered pass in
  `results` order. `test_microsim_vehicle_table.py::TestWriterFirstLast` now uses `append_step`.
- No file under `tests/golden/` changed. No config field changed.

## 5. Remaining opportunities (ranked by expected saving)

1. **Machine type for simulation stages.** The end-of-run spike was the reason a 20-seed
   four-hour battery needed 128 GB. The workers' own peak should now be SUMO plus ~0.2 GB.
   Measure one worker's peak RSS on the VM, then consider n2-highcpu-32 (32 GB, about 26 %
   cheaper per hour than n2-standard-32 at list price; we pay ~$1.55/h for the latter). Scoring would then run with
   fewer workers, or on its own stage. **This is the largest dollar lever left.**
2. **Skip the downstream bins for controllers that never read them.** Only JAD reads
   `obs.downstream`, but the bins are computed for every AV every step. On the I-24 5 % PI run
   they took 8.6 % of wall time, and at 20 % penetration roughly 25–35 %. Skipping them for
   FollowerStopper and PI, while still drawing the oracle-noise numbers when noise is on,
   would leave outputs identical and cut penetration sweeps by that share. It needs a
   `reads_downstream` registry flag, so it is a design decision, not done here. A
   sorted-position version was tried: it was bit-identical but slower at N ≈ 1,500 because of
   numpy call overhead.
3. **Linux measurement of the scoring allocator change.** If the macOS −17 % holds, lower
   `SCORE_WORKER_BYTES_PER_ROW` (160 → ~135), which gives about 3 more scoring workers on a
   128 GB VM.
4. **One `(veh_id, t)` sort per replicate in scoring.** `compute_metrics`, `link_hour_geh`
   (up to twice) and `crossing_speeds` each build `_ByVehicle`, which is a stable argsort of
   all rows. Sharing it would cut scoring CPU by maybe 30 %, but holding it would raise peak
   memory by ~25 B/row unless the phases are reordered. Scoring is ~5–10 % of battery VM time.
5. **What stays in Python per step** is now small. The largest item is libsumo's
   subscription dict (10–15 %); dropping any of the six subscribed variables would change the
   outputs. The network build (≤ 0.3 s per replicate) could be cached per config hash across
   seeds, which is negligible for four-hour runs.

**Cloud cost.** A 20-seed four-hour I-94 battery runs all seeds in parallel on one
n2-standard-32. The simulation phase lasts about as long as the slowest seed: 344–470 s in
free flow, up to 2,780 s gridlocked, or about $0.2–1.2 at $1.55/h. A 10–13 % cut saves
$0.02–0.15 per battery, or about $0.8 on a typical 5 h ($8) pipeline. That is 11–15 % more
seeds per dollar. The memory change matters more than the dollars: it removes an OOM risk
that grew with every longer or denser run, and it opens item 1.

## 6. Follow-up, 2026-10-07: downstream bins built only for JAD; a relaxation bookkeeping fix

Two changes to `packages/microsim/microsim/runner.py` (plus `controllers/registry.py`), both
measured against `HEAD` (fb92473). The same seeded cases were run from two extracted trees:
`git archive HEAD`, and that tree with only the two changed files copied in. This keeps
uncommitted work elsewhere in the repository out of the comparison. At most two SUMO
processes ran at once.

### 6.1 Downstream bins only for a controller that reads them (§5, item 2)

`controllers.registry.VEHICLE_CONTROLLER_READS_DOWNSTREAM` declares, for every registered
vehicle controller, whether it reads `ControllerObs.downstream`. It is `True` for `jad`
only. `reads_downstream(name)` raises `KeyError` for an undeclared name, so a new
controller cannot silently receive no bins. Tests pin three things: every registered
controller is declared; JAD's command changes with the bins; and each controller declared
`False` gives identical commands and memory over 400 random observations, with and without
bins. When the run's controller is not declared `True`, `run_micro` skips the
`_downstream_bins` pass, the oracle-noise draws and the delayed oracle's snapshot buffer.
Nothing else reads any of them: the oracle RNG (`seed + 7919`) feeds only the noise. The
controller receives the contract's default `downstream=()`. VSL, the Gym backend
(`microsim.gym_backend` bins its own observation), the AV state builder, logging and
`meta.json` read none of them.

**Identical outputs.** For 26 cases, the sha256 of every Parquet file (trajectories, edges,
vehicles, journeys) is the same in both trees, and so are `meta.json` (without
`wall_time_s` and `realtime_factor`), the `compute_metrics` output and the config hash.
The cases:

- ring with one FollowerStopper, PI-saturation or JAD AV;
- `corridor_10km` for 5 sim-min:
  - with no AVs;
  - with VSL;
  - FollowerStopper at 5 and 20 %;
  - PI-saturation at 5 and 20 %;
  - FollowerStopper at 5 % under a noisy, delayed oracle (20 s, ±20 %), the case where the
    skipped noise draws could have shown;
  - `follower_stopper_capacity` and `pi_meanfrac` at 5 %;
  - JAD at 5 % with a perfect and with a noisy, delayed oracle;
- the 12 golden cases.

**26 of 26 are identical.** At 20 % penetration the FollowerStopper run made 9,402
`_downstream_bins` calls at `HEAD` and makes none now. The JAD runs still make all of
theirs (1,556).

**Time.** The share was read from a `perf_counter` wrapper around `_downstream_bins` and
`_apply_oracle_noise` in `HEAD` runs. It is a ratio, so it holds up under the machine's load.
The A/B times are the minimum (and median) of five alternating runs, with network build and
SUMO start included. All runs are `corridor_10km`:

| Case | AVs | Bins share at `HEAD` | A/B wall, `HEAD` → now | Change, min / median |
|---|---|---|---|---|
| 10 min, FollowerStopper 5 % | 15 | 5.6–6.2 % | 0.967 → 0.912 s | −5.7 / −6.1 % |
| 10 min, FollowerStopper 10 % | 29 | 10.7–10.9 % | (too noisy, see below) | — |
| 10 min, FollowerStopper 20 % | 59 | 15.5–16.3 % | 1.190 → 0.993 s | −16.6 / −16.4 % |
| 10 min, PI-saturation 5 % | 15 | 5.8–6.3 % | 0.974 → 0.911 s | −6.5 / −6.9 % |
| 10 min, PI-saturation 20 % | 59 | 15.6–16.1 % | 1.194 → 1.001 s | −16.2 / −16.8 % |
| 5 min, FollowerStopper 20 % | 29 | 10.6–11.3 % | 0.841 → 0.741 s | −12.0 / −13.3 % |
| 5 min, PI-saturation 20 % | 29 | — | 0.664 → 0.576 s | −13.3 / −13.1 % |
| 10 min, JAD 20 % (bins still built) | 59 | — | 1.795 → 1.804 s | +0.5 / −1.4 % |

The saving equals the measured share, as expected. Later A/B batches ran while other
programs held the laptop's load average at 5–6. In those batches all wall times were
1.5–2× longer. They agree at 20 % (−16 to −22 %) but scatter by ±10 % at 5 %, including on
JAD, whose path did not change. Only the first batch, whose minima and medians agree, is
tabulated. A bin call costs about 12–17 µs on `corridor_10km`. A call scans every vehicle
in the network, so its cost grows with network size. On the I-24 replica, §5 measured
8.6 % at 5 % PI. No I-24 run was made here (laptop rule). The estimate of 25–35 % at 20 %
is therefore still an extrapolation, now with the identity proven.

### 6.2 Relaxation continuity under internal links (review finding, minor)

`_measured_crossings` passed `_measured_grant` the new follower's lane as it found it. With
`OSMNetwork.internal_links=True` that lane can be an internal junction lane (`:J_0`) between
two zone pieces. `_measured_relax_step` compares each later normal lane with the stored one
through `_lane_successors`, which is built from a network read without internal edges. The
internal pair has no entry there, so the next normal edge read as a lane change. The
relaxation was restored a step or two after the grant (the follower braked at once, which
B§5.7 is meant to prevent), and `n_relax_restored_lane_change` was miscounted. A relaxed
vehicle passing over an edge shorter than one step of travel was misread the same way.

Fix:

- A grant on an internal lane is anchored on the normal lane that junction lane leaves
  from. That lane comes from the connection's `via`, which `sumolib` keeps
  (`_internal_lane_origins`). A junction lane no connection names (the second lane of an
  internal junction) keeps the vehicle's existing anchor. If it has none, the anchor is set
  on its next normal lane.
- A lane counts as continuity if it is reachable within `RELAX_CONTINUITY_HOPS = 3`
  connection hops (`_lane_continues`). One hop is the old test.

A lane change made on the junction lane after the grant is still read as one. Six unit tests
on fake modules (`TestRelaxationContinuity` in `test_microsim_merge_model.py`) cover this.
The reviewer's case and the edge-skip case fail at `HEAD` and pass now.

**Committed outputs unchanged.** These measured cases are identical between the two trees
(every Parquet sha256, `meta.json`, metrics, config hash):

- the two measured goldens;
- the G0 fixtures of `test_microsim_merge_measured.py`:
  - the ramp fixture, also with AVs;
  - McKnight, seeds 3–5;
  - the measured golden weave;
  - Ruth St, five cases;
  - T.H.52 capacity, seeds 4 and 5;
  - T.H.61;
  - the T.H.52 corridor section.

None of them uses internal links. A counting wrapper confirmed that the new code paths never
fired on them: no grant on a junction lane, and no continuity beyond one hop. The fixture
configs name their OSM files by absolute path, so both trees were pointed at the
repository's unchanged `tests/fixtures`.

**Where it matters:** the same fixtures rerun with `internal_links: true` (not committed
configurations). All three change, with no collision in either tree:

| Fixture, `internal_links: true` | Grants made on a junction lane | Restored as a lane change, `HEAD` → now |
|---|---|---|
| ramp fixture, seed 3 | 5 | 5 of 53 → 1 of 55 |
| measured golden weave, seed 3 | 2 | 8 of 11 → 7 of 11 |
| T.H.52 corridor section, seed 3 | 15 | 80 of 106 → 70 of 108 |

**Checks.** `pytest -m "not slow" tests/test_microsim tests/test_controllers
tests/test_integration`: 826 passed, 13 xfailed, 2 xpassed (both marks are non-strict and
predate this change). ruff check and format are clean. mypy `--strict` on `controllers` is
clean. `runner.py` has the same 8 non-strict mypy findings as at `HEAD`. No golden and no
config field changed.
