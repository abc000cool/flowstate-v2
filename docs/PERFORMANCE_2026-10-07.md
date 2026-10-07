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
