# E12: the platform-sensitive tests, steps 1 to 3

This doc covers E12 (item 21) in docs/PRE_FRISCO_PROGRAM.md, design steps 1 to 3.
The goal is that the marked weave fixture tests give deterministic outcomes on macOS
and on Linux CI, with every mark strict. Sections 1–6 are steps 1 and 2: the macOS
side measured on the laptop (2026-10-07, 22:20–22:41 CDT) and the tools. Section 7
is step 3: the Linux side measured by the workflow (run 37727123358, 23:22–23:28
CDT), the verdict, and the marks made strict per platform.

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
not run. The probe records each config's hash on its own machine, labelled
`config_hash_this_checkout`. Why those hashes differ between machines is in §7.6.

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
(`artifacts/e12_platform_2026-10-07/state_record_darwin-arm64.jsonl`) and, in the
last column (added at step 3), the Linux record
(`artifacts/e12_platform_2026-10-07/state_record_linux-x86_64.jsonl`). "Lane-1
min" is the slowest 60-s window the test reads. A minus sign marks a criterion
that fails. Both columns are outcomes under the step 1–2 marks; §7.5 has the marks
of step 3.

| Test | Mark (steps 1–2) | macOS ×6 | macOS numbers (seed as in the id) | Linux CI ×3 (§7) |
|---|---|---|---|---|
| `corridor_fleet[entrance_peak-3]` | non-strict | xfailed | −1 of 45 exits given up (2.2 % > 2 %); −lane-1 last 60 m min 4.33 m/s; 44 exit; 0/31 unfinished; 0 collisions | xfailed: −2 of 45 given up (4.4 %); −lane-1 min 1.31 m/s; 42 exit; 3/49 unfinished |
| `corridor_fleet[entrance_peak-4]` | non-strict | **xpassed** | 0 of 41 given up; lane-1 min 12.61 m/s; 41 exit; 0/23 unfinished | **xpassed**, the same counters as macOS |
| `corridor_fleet[entrance_peak-5]` | non-strict | xfailed | 0 of 34 given up; −lane-1 min 1.09 m/s; 32 of 34 exit; −3/29 unfinished (2.9 allowed; minus added at step 3) | xfailed, the same counters as macOS |
| `corridor_fleet[exit_peak-3]` | strict | xfailed | −15 of 290 given up (5.2 %); −lane-1 min 2.58 m/s; 273 exit | xfailed, the same counters as macOS |
| `corridor_fleet[exit_peak-4]` | strict | xfailed | 2 of 281 given up (0.7 %); −lane-1 min 4.97 m/s; 278 exit | xfailed, the same counters as macOS |
| `corridor_fleet[exit_peak-5]` | non-strict | **xpassed** | 4 of 274 given up (1.5 %); lane-1 min 5.55 m/s; 266 exit; 2/71 unfinished | **xfailed**: −6 of 274 given up (2.2 %); −lane-1 min 4.29 m/s; 267 exit; 0/77 unfinished |
| th61 `weave_configuration_carries_the_stretch` | strict | xfailed | −2,592 of 2,885 depart (89.8 % < 95 %); entrance 847/848; 0 collisions | xfailed: −2,580 of 2,885 depart (89.4 %); entrance 824/848 |
| measured `th52_capacity_does_not_lock[4]` | none | passed | lane-1 first 60 m min 3.92 m/s (> 2); 7/529 unfinished; entrance 349/466 | passed: lane-1 min 3.92 m/s; 7/527 unfinished; entrance 347/466 |
| measured `th52_capacity_does_not_lock[5]` | strict | xfailed | −lane-1 min 1.35 m/s; 6/502 unfinished; entrance 316/466 | xfailed, the same counters as macOS (2,282 changer easings against 2,283) |
| measured `corridor_section…_measured` | strict | xfailed | −entrance 372/407 (387 req.); −GEH 11.08; −station min 15.34 m/s; mainline 1,178/1,196; 1 of 358 given up | xfailed: −entrance 356/407; −GEH 10.46; −station min 15.54 m/s; mainline 1,195/1,196; 2 of 360 given up |
| `th52_weave_at_capacity_flows` | strict | xfailed | −entrance 391/466 (420 req.); −lane-1 min 3.92 m/s; −`n_missed` 1; 2/472 unfinished | xfailed: −entrance 381/466; −lane-1 min 3.92 m/s; −`n_missed` 1; 3/460 unfinished |
| `…does_not_lock[off-4-0]` | non-strict | **xpassed** | entrance 401/466; lane-1 min 4.29 m/s; 1 missed (≤ 1); 2/487 unfinished; 23 releases | **xfailed**: entrance 385/466; lane-1 min 4.53 m/s; −3 missed (≤ 1); 5/489 unfinished; 16 releases |
| `…does_not_lock[off-5-1]` | none | passed | entrance 373/466 (372.8 req.); lane-1 min 3.31 m/s; 1 missed; 3/513 unfinished; 18 releases | passed: entrance 378/466; lane-1 min 3.15 m/s; 1 missed; 10/506 unfinished; 26 releases |
| `…does_not_lock[on-4-0]` | non-strict | **xpassed** | entrance 393/466; lane-1 min 4.37 m/s; 1 missed (≤ 4); 0/508 unfinished; 8 releases | **xfailed**: −entrance 367/466 (372.8 req.); lane-1 min 4.37 m/s; 2 missed; 3/487 unfinished; 20 releases |
| `…does_not_lock[on-5-1]` | none | passed | entrance 405/466; lane-1 min 4.16 m/s; 4 missed (≤ 4); 1/510 unfinished; 6 releases | passed: entrance 376/466; lane-1 min 3.46 m/s; 2 missed; 5/515 unfinished; 20 releases |
| `th52_weave_at_corridor_demand_exit_side` | non-strict | **xpassed** | lane-1 last 60 m min 7.32 m/s; 296 of 304 exit; 2/435 unfinished; 0 collisions | **xpassed**: lane-1 last 60 m min 5.70 m/s; 298 of 303 exit; 1/433 unfinished |
| `th52_with_upstream_entrance_at_corridor_demand` | strict | xfailed | −E1 211/360; −accel-lane end min 0.20 m/s; −gore min 3.58 m/s; 283 of 295 exit | xfailed: −E1 210/360; −accel end min 0.20 m/s; −gore min 3.58 m/s; 284 of 295 exit |
| `th52_with_upstream_entrance_on_the_corridor_fleet` | strict | xfailed | −E1 274/360; −accel end min 0.32 m/s; −gore min 0.68 m/s; 303 of 313 exit | xfailed: −E1 256/360; −accel end min 0.32 m/s; gore min 8.39 m/s; 311 of 320 exit |
| `th52_corridor_section_carries_free_flow_demand` | strict | xfailed | −entrance 382/407; −GEH 8.87 (4,276.7 vs 4,877.0 veh/h); −station min 14.22 m/s; mainline 1,195/1,196 | xfailed: −entrance 382/407; −GEH 9.23 (4,253.3 vs 4,877.0 veh/h); −station min 14.37 m/s; mainline 1,196/1,196 |

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

This section is the rule as fixed before Linux was measured. Section 7 applies it.

## 7. Step 3: Linux measured, verdict (b), every mark strict

### 7.1 The workflow run

Workflow run 37727123358 ran on `main` at 3856257 with both runners, from 04:22 to
04:28 UTC on 2026-10-08 (23:22–23:28 CDT on 2026-10-07). Each runner ran the list
three times, then the probe on all five fixtures. Its artifacts are kept at
`~/.flowstate-block/archives/e12_ci_37727123358/`.

- **Linux** (`ubuntu-latest`): Linux 6.17 (azure) on x86_64, Python 3.12.3,
  eclipse-sumo and libsumo 1.27.1, numpy 2.5.2, pandas 3.0.5, pyarrow 25.0.1.
  All three runs gave 3 passed, 14 xfailed, 2 xpassed, and pytest exited 0 each
  time. The three record files are byte-identical, so Linux is deterministic.
- **macOS** (`macos-latest`): macOS 26.6.2 on arm64, Python 3.12.10, the same
  SUMO and numpy. All three runs gave 3 passed, 11 xfailed, 5 xpassed, like the
  laptop. The three records are byte-identical. Against the committed laptop
  record, every test has identical trajectories and the same numbers, except
  `config_hash` (§7.6). The five probe files compare `identical` at every step on
  all three channels.

Committed from the Linux side, beside the macOS files in
`artifacts/e12_platform_2026-10-07/`: the five probe files
(`<fixture>_linux-x86_64.json`) and the run-1 record, renamed
`state_record_linux-x86_64.jsonl`. Runs 2 and 3 are byte-identical to run 1.

### 7.2 Outcomes, macOS against Linux

Under the step 1–2 marks: the committed macOS record against Linux run 1.

| Test | macOS | Linux |
|---|---|---|
| `…does_not_lock[off-4-0]` | xpassed | **xfailed** |
| `…does_not_lock[on-4-0]` | xpassed | **xfailed** |
| `corridor_fleet[exit_peak-5]` | xpassed | **xfailed** |
| `th52_weave_at_corridor_demand_exit_side` | xpassed | xpassed |
| `corridor_fleet[entrance_peak-4]` | xpassed | xpassed |
| `…does_not_lock[off-5-1]`, `[on-5-1]`; measured `th52_capacity_does_not_lock[4]` | passed | passed |
| the other 11 (§2) | xfailed | xfailed |

Three outcomes depend on the platform, and each platform gives its outcome
deterministically. The numbers behind each outcome are in §2's last column and in
each mark's reason.

### 7.3 Where the platforms part

`--compare` of each Linux probe file against the macOS one gives the following.
Steps are 0-based indices of 0.5-s steps.

| Fixture | `sumo` and `traj` first differ | `commands` first differs | Verdict |
|---|---|---|---|
| `ruth_exit_peak_s5` | step 20 (t = 10.5 s) | step 118 (t = 59.5 s) | `state_first` |
| `ruth_entrance_peak_s4` | step 28 (t = 14.5 s) | step 74 (t = 37.5 s) | `state_first` |
| `th52_exit_side_s3` | step 30 (t = 15.5 s) | step 151 (t = 76.0 s) | `state_first` |
| `th52_capacity_off_s4` | step 47 (t = 24.0 s) | step 77 (t = 39.0 s) | `state_first` |
| `th52_capacity_on_s4` | step 47 (t = 24.0 s) | step 77 (t = 39.0 s) | `state_first` |

On every probed fixture, SUMO's state (id, lane, position, speed) parts first. The
runner's commands stay identical for another 30–121 steps, until the state they
read has moved. So the runner made the same decisions on the same observed state,
and the platforms still produced different next steps. The divergence is SUMO
1.27.1's own arithmetic on arm64 macOS against x86_64 Linux.

The 14 tests that were not probed are attributed to the same cause by inference.
Their trajectories part in the first 60-s window, and no fixture showed a command
differing first.

Five tests part in the trajectories while their integer counters stay equal (the
float counters move in the last digits), and their minute speeds agree to within
2e-13 m/s:

- `[entrance_peak-4]`
- `[entrance_peak-5]`
- `[exit_peak-3]`
- `[exit_peak-4]`
- measured `[5]`, where 2,283 changer easings against 2,282 is the only counter
  that moves, and its minute speeds agree to 3e-6 m/s.

### 7.4 Verdict

The fixed rule (PRE_FRISCO_PROGRAM.md, decisions of 2026-10-07; §6) says: if the
divergence is SUMO's, use option **(b)**, per-platform strict marks. The probe
verdict is `state_first` on all five fixtures, so option (b) applies. Option (a)
would make the trajectories identical by fixing Python arithmetic order. It does
not apply, because no command differs first. Colima is not needed.

### 7.5 The marks

All 19 tests now carry either a strict mark or no mark, and no non-strict mark is
left. A per-platform mark is
`pytest.mark.xfail(condition=sys.platform == "linux", strict=True, …)`: it asserts
a pass on macOS and an xfail on Linux. Each reason gives both platforms' numbers
and the cause in the form "SUMO 1.27.1 arithmetic differs between arm64 macOS and
x86_64 Linux from step N". For a fixture that was not probed, the reason says so
in place of the step.

The edits:

- marks and their reasons only;
- `import sys` in `test_microsim_weave_short_section.py` and
  `test_microsim_merge_managed_meter.py`, for the condition (stdlib, so the modules
  the probe and `merge_model_selfcheck.py` load by path still import nothing new);
- a comment where a mark was removed.

No assertion, threshold, parametrisation, test id, golden or hash changed.

| Test | Mark, steps 1–2 | Mark, step 3 | macOS (laptop, CI) | Linux CI |
|---|---|---|---|---|
| `corridor_fleet[entrance_peak-3]` | non-strict | strict | xfailed | xfailed |
| `corridor_fleet[entrance_peak-4]` | non-strict | **none** (passes on both) | passed | passed |
| `corridor_fleet[entrance_peak-5]` | non-strict | strict | xfailed | xfailed |
| `corridor_fleet[exit_peak-3]`, `[exit_peak-4]` | strict | strict, a reason per seed | xfailed | xfailed |
| `corridor_fleet[exit_peak-5]` | non-strict | **strict on Linux** | passed | xfailed |
| th61 `weave_configuration_carries_the_stretch` | strict | strict | xfailed | xfailed |
| measured `th52_capacity_does_not_lock[4]` | none | none | passed | passed |
| measured `th52_capacity_does_not_lock[5]` | strict | strict | xfailed | xfailed |
| measured `corridor_section…_measured` | strict | strict | xfailed | xfailed |
| `th52_weave_at_capacity_flows` | strict | strict | xfailed | xfailed |
| `…does_not_lock[off-4-0]`, `[on-4-0]` (one mark on seed 4) | non-strict | **strict on Linux** | passed | xfailed |
| `…does_not_lock[off-5-1]`, `[on-5-1]` | none | none | passed | passed |
| `th52_weave_at_corridor_demand_exit_side` | non-strict | **none** (passes on both) | passed | passed |
| `th52_with_upstream_entrance_at_corridor_demand` | strict | strict | xfailed | xfailed |
| `th52_with_upstream_entrance_on_the_corridor_fleet` | strict | strict | xfailed | xfailed |
| `th52_corridor_section_carries_free_flow_demand` | strict | strict | xfailed | xfailed |

Expected per run: **macOS 8 passed, 11 xfailed; Linux 5 passed, 14 xfailed**, with
no xpass on either.

Limits of the per-platform marks:

- Only darwin-arm64 and linux-x86_64 are measured. The condition keys on
  `sys.platform`, as the decision named it, so a Linux arm64 host is held to the
  x86_64 outcome, and an Intel Mac to the arm64 one. Neither host has been run.
- The docstrings of `test_th52_weave_at_corridor_demand_exit_side` ("The marker is
  not strict") and `test_th52_weave_at_capacity_does_not_lock` ("the 80 % pin
  passes on macOS again"), and the `TestRuthStWeave` class docstring, describe the
  marks as they were. They are left as written, because this step edits marks
  and reasons only.

### 7.6 Why fixture configs hash differently on each machine

The record comparison shows `runs.0.config_hash` differing in every record
between macOS and Linux, for example `2b596a6c2b1b` → `fae82f759f7a` for
`test_th52_corridor_section_carries_free_flow_demand`. The cause is not a float
and not x86_64.

Each fixture builder writes its OSM fixture as an absolute path, and
`config_hash_payload` hashes `network.osm_file` (and T.H.61's `patch_files`) as
written. For example, `ruth_config` writes `str(RUTH_OSM)`, which is
`Path(__file__).resolve().parents[1] / "fixtures" / …`, and `_th52_config` writes
`str(Path(__file__).parents[1] / "fixtures" / "weave_th52.osm")`. So the hash
follows the checkout's location:

- the laptop: `/Users/anshpathak/Desktop/apps/flowstate`;
- the Linux runner: `/home/runner/work/flowstate-v2/flowstate-v2`;
- the macOS runner: `/Users/runner/work/flowstate-v2/flowstate-v2`.

The macOS CI record shows the same thing. Its hashes differ from the laptop's
(`2b596a6c2b1b` → `3bdbac9bb581`) while its trajectories are byte-identical to the
laptop's. Rebuilding the 19 tests' configs on the laptop, with the checkout root in
every string swapped for the runner's, reproduces every Linux hash and every macOS
CI hash exactly: 19 of 19 on both. The probe already compares
`portable_sha256`, the payload with `osm_file` made repo-relative, together with
the OSM file's sha256, and those agree on all five fixtures on all three machines.

This has no effect on committed scenarios or on any test outcome:

- No committed scenario is affected. Every `scenarios/*.yaml` writes repo-relative
  paths (`data/osm/…`, `patch_files: data/osm/…`), so their hashes do not depend on
  the checkout. That is why `tests/golden/scenario_config_hashes.json` holds on CI.
- None of the 19 tests asserts on a hash.
- What it does affect: a fixture run's `config_hash` names its config on one
  checkout only. Two records of a fixture compare by the portable digest, never by
  `config_hash`. A run directory keyed by the hash would also differ per checkout.
  Making the fixtures' paths repo-relative would move every fixture hash once, so
  that is not proposed here.

### 7.7 Verified locally (macOS laptop)

The checks below ran from 23:42 to 23:47 CDT on the step-3 tree. The tree also
carried another agent's uncommitted work in `packages/microsim` and
`packages/flowstate_core`: a per-window ramp-to-ramp share that runs only when a
weave sets it.

```
FLOWSTATE_RECORD_STATE=<scratch>/rec_runN.jsonl \
  uv run --no-sync pytest $(cat tests/test_microsim/e12_platform_tests.txt | tr '\n' ' ') -q -rxX -p no:cacheprovider
```

- **Pytest.** Each of the three runs gave `8 passed, 11 xfailed`, with the same
  outcome per test and no xpass. Wall times were 106, 105 and 100 s (the machine
  was shared).
- **Records.** The three records are byte-identical (19 lines, 65,433 bytes).
- **Against the committed step-1 record.** `--compare-records` shows identical
  trajectories and 0 differing numbers for all 19 tests, so the other agent's work
  did not reach these fixtures. The only change is five outcome labels,
  `xpassed` → `passed`, on the tests whose mark was removed or made per-platform.

### 7.8 The record comparison and per-platform outcomes

Before this step, `--compare-records` printed `OUTCOME DIFFERS` for any change of
label. It never failed the workflow, because the step ends `|| true`. But it would
have reported every per-platform mark as an anomaly.

`scripts/platform_probe.py` now classifies each pair of outcomes with
`outcome_relation`:

| Relation | When | Printed as |
|---|---|---|
| `same` | the same outcome | same outcome |
| `same_result` | the assertions held on both sides, or on neither, and neither run failed: the marks differ | same result, different marks |
| `per_platform` | the assertions held on one side only, the records come from different platforms (`platform`, `machine`), and neither run failed | differs by platform, each as its own marks allow |
| `differs` | anything else: a different result on one platform (non-determinism), a `failed` or `xpassed_strict` outcome, or a skipped or not-run test | OUTCOME DIFFERS |

The report ends with one count per relation. The exit code is unchanged: 0 only
when the outcomes, the numbers and the trajectories all agree. The workflow's
compare step is unchanged apart from a comment. Its gate stays the pytest exit
code, which fails a run whenever an outcome is not what that platform's strict
marks allow.

Applied to the evidence:

- the committed macOS record against Linux run 1: 16 same outcome, 3 differs by
  platform;
- this step's laptop record against Linux run 1: 14 same outcome, 2 same result
  with different marks, 3 differs by platform;
- the step-1 record against the step-3 record: 14 same outcome, 5 same result.

### 7.9 What CI must confirm

```
gh workflow run platform_tests.yml --ref main -f runners='["ubuntu-latest","macos-latest"]'
gh run list --workflow platform_tests.yml --limit 1
```

Run this once the step-3 commit is pushed. It is done when, on each runner, all
three runs exit 0, the three records are byte-identical, and the outcomes are as
follows:

- **Linux:** `5 passed, 14 xfailed` in every run, no xpass. The 14 xfails are §7.5's
  11 plus `[exit_peak-5]`, `[off-4-0]` and `[on-4-0]`.
- **macOS:** `8 passed, 11 xfailed` in every run.

In `compare_records.txt`, the comparison against the committed macOS record should
show `0 OUTCOME DIFFERS`:

- on Linux: 14 same outcome, 2 same result (`[entrance_peak-4]`, `exit_side`), and
  3 differs by platform (`[exit_peak-5]`, `[off-4-0]`, `[on-4-0]`);
- on macOS: 14 same outcome and 5 same result. Those five are the step-1 record's
  `xpassed` labels, which now read `passed`.

The probe files should compare as in §7.3 on Linux, and as `identical` on macOS.
