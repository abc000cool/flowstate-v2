# E12: the platform-sensitive tests, steps 1 and 2

This doc covers E12 (item 21) in docs/PRE_FRISCO_PROGRAM.md, design steps 1 and 2.
The goal is that the marked weave fixture tests give identical outcomes on macOS and
on Linux CI, with every mark strict. Only the macOS side has been measured
(2026-10-07, 22:20–22:41 CDT). **Every Linux number is pending** until the
workflow below has run.

## 1. Tree state

All runs used `main` plus uncommitted work. HEAD was 4741648 for the first three
runs, then c355217 and 5822abe once those commits landed. Neither commit touches
`packages/microsim`, `packages/flowstate_core`, `tests/test_microsim`,
`tests/fixtures`, the I-94 scenario that `corridor_fleet_block()` reads, or
`artifacts/idm_i24_capacity.json` (`git diff --stat 4741648 5822abe` over those
paths is empty). So every run below saw the same code.

The weave-default change (W1b and W2 on by default) was not committed while these
runs were made. `git diff --stat` for the files it touches, captured at 22:42:53:

```
 packages/flowstate_core/flowstate_core/config.py   | 279 +++++++++++++++------
 packages/microsim/microsim/runner.py               |  67 +++--
 tests/golden/config_defaults.json                  |   9 +-
 tests/golden/corridor_10km_smoke.json              |  14 +-
 tests/golden/corridor_10km_workzone.json           |  14 +-
 tests/golden/corridor_boundary_schedule.json       |  14 +-
 tests/golden/corridor_workzone_heavy.json          |  14 +-
 tests/golden/hov_corridor.json                     |  14 +-
 tests/golden/macro_corridor.json                   |   8 +-
 tests/golden/macro_corridor_workzone.json          |   8 +-
 tests/golden/merge_measured.json                   |   4 +-
 tests/golden/merge_measured_accel.json             |   4 +-
 tests/golden/merge_meter_alinea.json               |  15 +-
 tests/golden/merge_scripted.json                   |  15 +-
 tests/golden/merge_weave.json                      |  25 +-
 tests/golden/merge_zipper.json                     |  15 +-
 tests/golden/ring_sugiyama.json                    |  14 +-
 .../test_microsim_merge_managed_meter.py           | 275 ++++++++++++++------
 18 files changed, 589 insertions(+), 219 deletions(-)
```

By 22:44 these files had been staged for that change's own commit. That staged set
also adds `tests/golden/scenario_config_hashes.json`.

The diff also changes `validation`, several scripts and docs, which these tests do
not run. This doc makes no claim about config hashes. The probe records each
config's hash on its own machine, labelled `config_hash_this_checkout`.

Platform: macOS 26.5 on arm64, Python 3.12.13, eclipse-sumo 1.27.1, libsumo
1.27.1, numpy 2.5.2, pandas 3.0.5, pyarrow 25.0.1.


*Committed state (added after the runs).* The uncommitted weave-default change named above landed as commit 8ea59ec (2026-10-07, A2: W1b and W2 on by default, config-hash policy v4), together with the hash-policy reconciliation, D10 and C7b; the microsim and core packages, `tests/test_microsim` and the fixtures are as they were during the six runs, so the recorded outcomes describe that commit.

## 2. The list, re-measured on macOS

`tests/test_microsim/e12_platform_tests.txt` holds the 10 selectors, one per line.
They collect 19 tests: the 16 xfail-marked tests and the 3 unmarked parameters
beside them. The program's list (15 marked, 17 collected) predates the
Amendment-4 parametrisation. That change split `[4-0]` into `[off-4-0]` and
`[on-4-0]`, and `[5-1]` into `[off-5-1]` and `[on-5-1]`.

```
uv run --no-sync pytest $(cat tests/test_microsim/e12_platform_tests.txt) -m "not slow" -q -rxX -p no:cacheprovider
```

Six consecutive runs on macOS all gave **3 passed, 11 xfailed, 5 xpassed**, with
the same outcome for every test:

| Runs | Recorder | Time | Wall times |
|---|---|---|---|
| 1–3 | off | 22:20–22:23 CDT | 52.8 s, 53.5 s, 62.0 s |
| 4–6 | on | 22:38–22:41 CDT | 49.4 s, 49.3 s, 48.9 s |

The recorder did not change any outcome. Its three record files are
byte-identical (19 lines, 65,438 bytes each).

The numbers below come from the macOS record
(`artifacts/e12_platform_2026-10-07/state_record_darwin-arm64.jsonl`). "Lane-1
min" is the slowest 60-s window the test reads. A minus sign marks the criterion
that fails.

| Test | Mark | macOS ×6 | macOS numbers (seed as in the id) | Linux |
|---|---|---|---|---|
| `corridor_fleet[entrance_peak-3]` | non-strict | xfailed | −1 of 45 exits given up (2.2 % > 2 %); −lane-1 last 60 m min 4.33 m/s; 44 exit; 0/31 unfinished; 0 collisions | pending |
| `corridor_fleet[entrance_peak-4]` | non-strict | **xpassed** | 0 of 41 given up; lane-1 min 12.61 m/s; 41 exit; 0/23 unfinished | pending |
| `corridor_fleet[entrance_peak-5]` | non-strict | xfailed | 0 of 34 given up; −lane-1 min 1.09 m/s; 32 of 34 exit; 3/29 unfinished | pending |
| `corridor_fleet[exit_peak-3]` | strict | xfailed | −15 of 290 given up (5.2 %); −lane-1 min 2.58 m/s; 273 exit | pending |
| `corridor_fleet[exit_peak-4]` | strict | xfailed | 2 of 281 given up (0.7 %); −lane-1 min 4.97 m/s; 278 exit | pending |
| `corridor_fleet[exit_peak-5]` | non-strict | **xpassed** | 4 of 274 given up (1.5 %); lane-1 min 5.55 m/s; 266 exit; 2/71 unfinished | pending |
| th61 `weave_configuration_carries_the_stretch` | strict | xfailed | −2,592 of 2,885 depart (89.8 % < 95 %); entrance 847/848; 0 collisions | pending |
| measured `th52_capacity_does_not_lock[4]` | none | passed | lane-1 first 60 m min 3.92 m/s (> 2); 7/529 unfinished; entrance 349/466 | pending |
| measured `th52_capacity_does_not_lock[5]` | strict | xfailed | −lane-1 min 1.35 m/s; 6/502 unfinished; entrance 316/466 | pending |
| measured `corridor_section…_measured` | strict | xfailed | −entrance 372/407 (387 req.); −GEH 11.08; −station min 15.34 m/s; mainline 1,178/1,196; 1 of 358 given up | pending |
| `th52_weave_at_capacity_flows` | strict | xfailed | −entrance 391/466 (420 req.); −lane-1 min 3.92 m/s; −`n_missed` 1; 2/472 unfinished | pending |
| `…does_not_lock[off-4-0]` | non-strict | **xpassed** | entrance 401/466; lane-1 min 4.29 m/s; 1 missed (≤ 1); 2/487 unfinished; 23 releases | pending |
| `…does_not_lock[off-5-1]` | none | passed | entrance 373/466 (372.8 req.); lane-1 min 3.31 m/s; 1 missed; 3/513 unfinished; 18 releases | pending |
| `…does_not_lock[on-4-0]` | non-strict | **xpassed** | entrance 393/466; lane-1 min 4.37 m/s; 1 missed (≤ 4); 0/508 unfinished; 8 releases | pending |
| `…does_not_lock[on-5-1]` | none | passed | entrance 405/466; lane-1 min 4.16 m/s; 4 missed (≤ 4); 1/510 unfinished; 6 releases | pending |
| `th52_weave_at_corridor_demand_exit_side` | non-strict | **xpassed** | lane-1 last 60 m min 7.32 m/s; 296 of 304 exit; 2/435 unfinished; 0 collisions | pending |
| `th52_with_upstream_entrance_at_corridor_demand` | strict | xfailed | −E1 211/360; −accel-lane end min 0.20 m/s; −gore min 3.58 m/s; 283 of 295 exit | pending |
| `th52_with_upstream_entrance_on_the_corridor_fleet` | strict | xfailed | −E1 274/360; −accel end min 0.32 m/s; −gore min 0.68 m/s; 303 of 313 exit | pending |
| `th52_corridor_section_carries_free_flow_demand` | strict | xfailed | −entrance 382/407; −GEH 8.87 (4,276.7 vs 4,877.0 veh/h); −station min 14.22 m/s; mainline 1,195/1,196 | pending |

Compared with the program's list:

- Two non-strict marks now pass on macOS under the W1b/W2 defaults:
  `[entrance_peak-4]` and `test_th52_weave_at_corridor_demand_exit_side`.
- The `[4-0]` xpass holds in both of its new arms.
- `[exit_peak-5]` still reads 4 of 274.
- All 9 strict marks still xfail.
- Several reasons in `test_microsim_merge_managed_meter.py` quote numbers from
  before W1b/W2. For example, `test_th52_weave_at_capacity_flows` says 395 at
  seed 3, but the record reads 391.

## 3. The recorder

When `FLOWSTATE_RECORD_STATE` names a file, every test in `tests/test_microsim`
appends one JSON line to it at teardown. The recorder lives in
`tests/test_microsim/_platform_record.py` and is wired as an autouse fixture plus
a report hook in the new `tests/test_microsim/conftest.py`.

Each line holds:

- `test_id`.
- `outcome`: one of passed, failed, xfailed, xpassed, xpassed_strict, skipped or not_run.
- The platform, Python and SUMO / numpy / pandas / pyarrow versions, and `GITHUB_SHA`.
- `runs`: one entry per `run_micro` call. Each has the `meta.json` counters
  (departures per ramp, collisions, and every `n_*` / `wait*` / `mean_*` counter
  of each weave section or measured zone). It also has a sha256 of the
  trajectories (t, veh_id, lane, x, v; exact little-endian bytes), over the
  whole run and per 60-s window.
- `states`: the return value of each state helper the test called, serialized
  at teardown. That covers the dicts and per-minute series its assertions read.

No test file calls the recorder. While a test runs, the recorder wraps that
module's `run_micro` and any helpers named in `STATE_HELPERS`, and puts them back
at teardown. The helpers are:

- `lane1_last60m_windows`
- `th61_lane_end_state`
- `_th52_lane1_windows`
- `_th52_corridor_state`
- `_lane_speed_windows`
- `_th52_state`
- `_th52_lane1_first60m_windows`

As a result, `test_microsim_merge_managed_meter.py` needs **no edit**. It also
means the modules that `scripts/merge_model_selfcheck.py` loads by path import
nothing new. A test checks that every name in the registry exists in its module.

The only test-file edit is in `test_microsim_merge_measured.py`. The
`test_th52_capacity_does_not_lock` lane-1 windows, previously computed inline,
move into the named helper `_th52_lane1_first60m_windows` with the same
computation and assertions.

When the variable is unset, the fixture yields immediately and touches nothing.

## 4. The divergence probe

`scripts/platform_probe.py` runs a fixture as its test builds it, using the test
module's own config function loaded by path. For every step (2,400 steps of
0.5 s) it writes three 12-hex sha256 prefixes:

- `sumo`: every vehicle in the network, ramps included, as (id, lane id, lane
  position, speed). These are read by libsumo getters from a
  `libsumo.addStepListener` listener, which runs after each `simulationStep`,
  before the runner reads the step.
- `traj`: the step's `trajectories.parquet` rows (id, lane, corridor x, v).
- `commands`: the step's `weave_commands.parquet` rows in decision order.
  `WeaveSpec.record_commands` is set on each weave block. The probe checks that
  this leaves the config hash unchanged.

**Capture rate:** the fixtures run at the default `output_hz` of 2 Hz with
`step_length_s` 0.5, so the runner already captures every step. No runner hook
was needed, and `runner.py` is unchanged.

The step listener adds what the trajectories leave out: vehicles on ramp edges.
A test shows that a probed 30-s run writes byte-identical `trajectories`,
`edges` and `vehicles` parquet files to a plain `run_micro` run.

Limitations:

- `commands` records the weave and measured-zone rules only. That is every
  runner write in these fixtures: they have no AVs, VSL, meters, closures or
  boundary schedule.
- When `commands` differs first, the cause is either Python arithmetic or a SUMO
  value outside the state tuple that a rule reads (`getLeader`,
  `getFollowSpeed`, `getNeighbors`, `getLaneChangeState`). Separating those two
  would need the rules' inputs logged beside each command row. That is an
  extension of `_WeaveCommandRecorder` in `runner.py`, which was not made here.

Within a step, the runner reads state S_k and then writes commands C_k, which act
on step k+1. So `--compare` reports one of four verdicts:

- `state_first`: SUMO's step diverged from identical observed state and commands.
- `commands_first`: the runner decided differently on identical state.
- `same_step`: state and commands diverge at the same step, which reads as state first.
- `identical`: no divergence.

macOS outputs (`artifacts/e12_platform_2026-10-07/<fixture>_darwin-arm64.json`,
95–110 KB each, written 22:32 CDT):

| Fixture | Test | Steps with commands | Summary |
|---|---|---|---|
| `ruth_exit_peak_s5` (default) | `[exit_peak-5]` | 1,012 | 4 of 274 given up, 266 exit, ramp 73/73 |
| `th52_capacity_off_s4` (default) | `[off-4-0]` | 2,378 | ramp 401/466, 23 releases, 1 given up |
| `th52_capacity_on_s4` (default) | `[on-4-0]` | 2,378 | ramp 393/466, 8 releases, 1 given up |
| `ruth_entrance_peak_s4` | `[entrance_peak-4]` | 1,223 | 0 of 41 given up, ramp 128/128 |
| `th52_exit_side_s3` | `exit_side` | 2,375 | 296 of 304 exit, 2 given up, ramp 388/470 |

Each summary equals the matching test's recorded numbers. A second macOS pass
(22:32:47) compared `identical` at every step, on all three channels, for all
five fixtures. The probe takes about 4 s per fixture.

## 5. Running the workflow and comparing

`.github/workflows/platform_tests.yml` is `workflow_dispatch` only. Its setup
matches ci.yml: setup-uv with Python 3.12, then `uv sync --all-packages --dev`.
The job then:

1. Runs the list three times with `-rxX` and the recorder on.
2. Runs the probe on all five fixtures.
3. Compares the probe files and records against the committed macOS files, and
   the three records against each other.
4. Uploads `platform-results/`.

A failed pytest run fails the job, but only after the upload. A failed probe also fails the job.

```
gh workflow run platform_tests.yml --ref main          # Linux only (default)
gh workflow run platform_tests.yml --ref main -f runners='["ubuntu-latest","macos-latest"]'
gh run list --workflow platform_tests.yml --limit 1     # the run id
gh run watch <run-id>
gh run download <run-id> -D /tmp/e12_ci                 # platform-tests-<os>-<run-id>/
cat /tmp/e12_ci/platform-tests-ubuntu-latest-<run-id>/compare.txt /tmp/e12_ci/platform-tests-ubuntu-latest-<run-id>/compare_records.txt
uv run --no-sync python scripts/platform_probe.py --compare \
  artifacts/e12_platform_2026-10-07/ruth_exit_peak_s5_darwin-arm64.json \
  /tmp/e12_ci/platform-tests-ubuntu-latest-<run-id>/probe/ruth_exit_peak_s5_linux-x86_64.json
uv run --no-sync python scripts/platform_probe.py --compare-records \
  artifacts/e12_platform_2026-10-07/state_record_darwin-arm64.jsonl \
  /tmp/e12_ci/platform-tests-ubuntu-latest-<run-id>/state_record_run1.jsonl
```

To look at the rows around the first differing step `T`, re-run with
`-f dump_at="T"` and run `--dump-at T` locally.

## 6. What step 3 decides

Once Linux has been measured:

- **Cause.** The coordinator's rule (PRE_FRISCO_PROGRAM.md, decisions of
  2026-10-07) applies. If the probe's verdict is `state_first` or `same_step` on
  the fixtures that diverge, the divergence is SUMO's: option **(b)**, per-platform
  strict marks (`condition=sys.platform …`, strict on each side). If the verdict
  is `commands_first` and the inputs point to Python arithmetic, option **(a)**
  applies: fix the arithmetic order so the trajectories match, with no colima.
- **Coverage.** This covers all 19 tests, not only the marked ones. An unmarked
  test that fails on Linux (`measured[4]`, `[off-5-1]`, `[on-5-1]`) gets a
  per-platform strict mark with its numbers. It is never skip-marked.
- **Non-strict marks.** The 7 current non-strict marks each become strict for
  each platform.
- **Reasons.** Every reason carries both platforms' numbers.
- **Unchanged.** No assertion or threshold changes. Goldens and config hashes
  stay as they are.
- **Done when.** Three consecutive runs on each platform give identical outcomes
  for all 19, and no non-strict mark remains.

Edits to `test_microsim_merge_managed_meter.py` wait until the uncommitted
weave-default commit has landed.
