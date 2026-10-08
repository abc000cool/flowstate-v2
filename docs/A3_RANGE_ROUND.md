# Amendment 3's range round (stage p24): built 2026-10-07, run 2026-10-08 (result in §8)

The round is pre-registered in docs/FRISCO_PROTOCOL.md, "Adoption of Amendment 3 — 2026-10-07", items 1-7 (P-A3 of
docs/DECISIONS_2026-10-07.md §A3.3). This note records what was built for it, the timing decision its item 1 asks
for before the launch, and the expected values. The protocol's text is not changed here.

Labels: **[computed]** derived from committed inputs, nothing simulated; **[run]** fixture-sized runs made for this
note on the laptop (no corridor run); **[decided]** a choice made here, before any run that varies the share.

## 0. In plain English

- **What the round asks.** Some US 52 drivers who enter I-94 westbound leave again at exit 242B, 305 m later. Nobody
  has counted how many. The model assumes the proportional split; the range runs from it to 0.70. The round runs the
  four-hour I-94 corridor at three points of that range (u = 0, 0.5, 1) and asks whether the answer matters.
- **What was missing.** The existing setting takes one share for the whole run and refuses windows where the exit
  is too small to carry it. The round needs one share per 5-minute window, and it must clip, not refuse.
- **What was built.** That per-window setting, a stage that runs the round's six batteries on one cloud machine, and
  the readout that applies the pre-registered rule. Nothing has run.
- **The timing decision.** The plan now pairs the US 52 entrants with mainline vehicles that reach the weave in the
  same 5 minutes. Before, it paired them with mainline vehicles that left the corridor's start in the same 5 minutes,
  about 6.4 minutes earlier than they reach the weave.

## 1. The pre-registration, restated (unchanged)

1. **Prerequisite**: the per-window form of the key, `s_w = P_w + u · (0.70 − P_w)`, clipped to `v_OFF,w / v_ON,w`,
   the clipped windows counted in `meta.json["ramp_to_ramp_shares"]`, byte-identical when unset (§3).
2. **Families**: F1 `mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b` (p10's arm A) and F2
   `mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2` (p10's arm B, the reference since Amendment 4).
3. **Arms**: u0, u05 and u1 on the T.H.52 block only. Ruth St stays proportional and is reported as unexamined.
   Step 3's 20 seeds, paired. u0 re-runs the committed scenario.
4. **Readings**: S790 and S97 hourly flows and GEH; gate C1, C3, C4 and C6; realised demand; collisions by section;
   locks (both readers); given-up exits and W1b releases per weave; crossings; realised share and clipped windows;
   per-lane hourly flows at S790 and the gore from each battery's kept trajectory. Contrasts against u0 use a paired
   t with 19 df.
5. **Rule at u1 against u0**: material on M1-M4; not material when none holds and the S790 06:30-07:30 interval lies
   within ±100 veh/h; otherwise inconclusive, with no seeds added.
6. **Never**: no share is chosen from the round; u05 shows shape only; every result is reported.
7. **Cost**: about $2.0 per family at `--procs 10`, both about $3.9, `--cap-min 420`.

## 2. The timing decision (item 1's last sentence) [decided]

**What P_w is computed from.** The T.H.52 exit fraction of window `w` is a conservation closure over the stations
at the weave in that window: `(S790 + rnd_91040 − S97 − rnd_87221) / (S790 + rnd_91040)`. So `P_w`, `v_ON,w` and
`v_OFF,w` describe the vehicles at the gore in window `w`.

**The two rules.**

- **Departure windows (the single share's rule).** The entrants of window `w` trade destinations with mainline
  vehicles that depart the corridor entry in `w`.
  - Those vehicles reach the gore 386 s later (1.29 windows; free-flow times below).
  - So each swap moves an exit trip 6.4 minutes earlier at the gore and a through trip 6.4 minutes later.
  - The HCM partition `v_RR + v_FR = v_OFF` then holds per departure window, not at the gore.
  - u would change when exit demand reaches the gore, not only who crosses (rule 4 says "never demand").
  - At u1 on the scenario's rates [computed, `a3_expected.json` `departure_rule_exit_shift`], the gore's exit flow
    moves by up to +371 veh/h in single 5-minute windows at 05:30 (the network filling) and by −262 to +222 veh/h
    around 07:40-08:00, where the clip swings the swap count. In the scored hours the hourly net is −7 to +6
    vehicles.
- **Free-flow arrival windows (built).** Every entrant and every partner is windowed by `depart + τ`, its free-flow
  time to the start of the entrance's attach edge (`51388891`).
  - The traders reach the gore together, under free flow, in the window whose counts set `P_w`.
  - So each window's exit and through volumes at the gore are unchanged by u, and only who crosses changes.
  - The clip is the model's own exit volume at the gore in that window: the constraint `v_RR,w ≤ v_OFF,w` where it
    binds.

**Choice: free-flow arrival.** It makes `s_w` the share of the gore window the count data describe. It leaves the
existing exit draw as it is: a mainline vehicle still draws its exit at the fraction of its departure window, about
7 minutes before it reaches the gore. That convention is the same in every arm and in the unset model, so it is not
an effect of u. Changing it would change u0, the committed model.

**The free-flow times** [computed]. These come from a netconvert compile of F2's network. Each edge is taken at
`min(v0, speedFactor × base limit)`, the convention of `microsim.runner.RouteGeometry` and `journeys.parquet`. The
values below use the population's mean v0 of 32.40 m/s (`artifacts/idm_i24_capacity_amax_k1.0.json`) and speed
factor 1. The 24.59 m/s (55 mph) limit binds on every mainline edge.

| origin | distance to the start of 51388891 [m] | τ [s] |
|---|---|---|
| corridor entry | 10,426.6 | **424.0** |
| on-ramp 1077665160 (zeroed) | 9,732.7 | 396.9 |
| on-ramp 18207436 | 7,409.9 | 302.9 |
| on-ramp 18207653 | 6,966.2 | 284.3 |
| on-ramp 178547099 | 6,419.4 | 262.3 |
| on-ramp 745524613 (Ruth St) | 5,673.3 | 232.6 |
| C-D re-entry 745524608 | 4,873.3 | 199.8 |
| on-ramp 53062592 | 3,952.9 | 169.0 |
| on-ramp 40648744 | 801.5 | 34.0 |
| **on-ramp 769818012 (T.H.52, the entrants)** | 844.2 | **37.8** |

**How the plan applies it.**

- Each vehicle uses its own v0 and speed factor, which are already drawn.
- The record states each origin's mean, minimum and maximum τ.
- 386 s is a lower bound on the real lag: congestion upstream of the weave (S790's queue at the peak) lengthens it.

**Effect on the expected shares** [computed, `artifacts/a3_range_2026-10-07/a3_expected.json`].

- The count-data column is item 3's figures.
- The other two columns are the same rule applied to the model's own volumes at the gore: the scenario's rates, the
  plan's exit draws and the free-flow times above, under each windowing rule.

| arm | count data (item 3) | model volumes, arrival windows (built) | model volumes, departure windows |
|---|---|---|---|
| u0 s at 05:30-05:50 / 06:30-07:30 | 0.29 / 0.18 | 0.29 / 0.18 | 0.29 / 0.18 |
| u05 s | 0.50 / 0.44 | 0.50 / 0.44 | 0.50 / 0.44 |
| u1 s | 0.70 / 0.70 | 0.70 / 0.70 | 0.70 / 0.70 |
| crossers 06:30-07:30, veh/h (u0 / u05 / u1) | 1,955 / 1,282 / 609 | 1,988 / 1,315 / 643 | 1,960 / 1,287 / 614 |
| clipped windows (u0 / u05 / u1) | 0 / 1 / 8 | **0 / 0 / 12** | 0 / 1 / 11 |

- **Where the clipped windows fall.**
  - Count data, u1: 07:30-08:10 except 08:00.
  - Arrival windows, u1: 05:30 (the empty network's first minutes, inside the warm-up hour), 07:35-08:10, 08:25,
    08:35 and 08:40.
  - Departure windows, u1: 07:30, 07:40-08:10, 08:20, 08:25 and 08:35.
- **Why the model columns differ from the count data.**
  - The pool's mainline exit draws carry the fraction of their departure window. So the 0.089 at 07:50 reaches the
    gore pool at 07:55-08:00 and lowers it there, and the low fractions of 08:05-08:30 do the same in the following
    windows.
  - The model's gore volumes are its own: 1,988 crossers at u0 against the counts' 1,955.
- **Status.** These are expectations, not criteria. The realised clips are counted per seed in each run's record.
  The protocol's figures stand as written.

## 3. The per-window form as built

**Config** (`flowstate_core.config`).

- The per-window form is a mapping under the existing key: `WeaveSpec.ramp_to_ramp_share` is now
  `float | RampToRampRange | None`. `RampToRampRange` holds `u` (required, in [0, 1]) and `s_max` (in (0, 1],
  default `RAMP_TO_RAMP_S_MAX_DEFAULT` 0.70); extra keys are forbidden.
- Being one key, the two forms can never be set together. A mapping that names a single share is refused.
- No new `WeaveSpec` field was added, so `test_config_hash.py`'s schema pin stands untouched.
- Hash-neutral when unset (the existing wrap serializer). Set, the hash moves with `u` and with a non-default
  `s_max`; the default `s_max` is omitted from the payload, as explicit defaults are. `model_dump` and
  `meta.json["config"]` state both values. The policy stays at v4.

**Plan** (`microsim.vehicles._ramp_to_ramp_window`).

- **Windows.** Each vehicle is windowed by its free-flow arrival (§2).
- **P_w.** The entrants' exit probability, averaged over the window's clock.
- **The share.** `s_w = P_w + u (s_max − P_w)`, clipped to `[min_share, max_share]`:
  - `max_share` = the window's pool vehicles bound for the exit / its entrants (`"exit_volume"`);
  - `min_share` = (entrants bound for the exit − partners passing elsewhere) / entrants (`"through_volume"`, never
    met at or above the drawn split).
- **Counts.** Cumulative rounding, the swap of the single share within the window, no random number drawn.
- **Geometry.** `microsim.runner._ramp_to_ramp_geometry` reads it from the compiled net only when a weave sets the
  form.

**Record** (`meta.json["ramp_to_ramp_shares"]`, the existing key). Per weave entrance:

- `form: "per_window"`, `u`, `s_max`, `window_s`, `timing: "free_flow_arrival"`, `arrival_at`, and `free_flow_s`
  per origin;
- the single share's counts;
- `n_windows`, `n_clipped`, `clipped_windows_t0_s`;
- one row per window: `t0_s`, `t1_s`, `n_entrants`, `p`, `s`, `s_applied`, `clip`, `max_share`, `min_share`,
  `n_partners`, `n_partners_to_exit`, `n_ramp_to_ramp_drawn`, `n_ramp_to_ramp`, `share_realized` and the swaps.

The single share's code path and record are unchanged.

**Unset is byte-identical** [run] (`artifacts/a3_range_2026-10-07/identity/`, the W1 method of
docs/WEAVE_LOSS_DIAGNOSIS.md §8.2).

- **The two trees.** Both come from `git archive` at 3856257. The second has only `config.py`, `vehicles.py` and
  `runner.py` of this build copied in. Case configs come from the HEAD tree in both legs.
- **What is compared per run.** Every Parquet file's sha256, the route file's sha256, the whole `meta.json` (wall
  time removed), `compute_metrics` and the config hash.
- **40 of 40 cases identical** (`summary.json`):
  - W1's 37 cases: the 12 micro goldens, 19 weave and merge fixtures, the T.H.52 section with the calibrated
    drivers at seeds 3-5, and the measured model on three fixtures;
  - the T.H.52 section with every weave block's share present as null;
  - the single share 0.5 at seeds 3 and 4 (its refactored path).
- **Scenarios.** The 56 committed scenarios hash and dump the same in both trees, and `WEAVE_DEFAULTS` is unchanged.

**u = 0 is not byte-identical to unset; it equals it in expected shares only** [run].

- u = 0 asks `P_w` in every window, so it replaces the proportional Bernoulli draw of each window's ramp-to-ramp
  count with that count's rounded expectation.
- At seeds 3, 4 and 5 of the T.H.52 section, the unset draws gave 0.314, 0.305 and 0.351. u = 0 gave 0.287 at every
  seed, the windows' `Σ P_w N_w` (2/13, 4/11 and 0/26 swaps to / from the exit).
- Every Parquet file of those runs differs from the unset run's.
- This is why the u0 arm is the committed scenario, unset: F2's u0 is the committed file, and F1's u0 copy sets no
  share. u05 and u1 differ from u0 in their routes (and the Bernoulli noise of the drawn count), every other draw
  being the seed's.

## 4. The stage `p24_i94_a3`

The stage is pasted into `scripts/gcp/pipeline_i24.sh` above "# 9. Done marker"; its text is
`artifacts/a3_range_2026-10-07/stage_p24_a3.sh.txt`.

**Families.**

- **F1** is `scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b.yaml` (policy-v4 hash 2dd495d173f4) with W2's three
  switches (`weave_handback`, `weave_close_leader`, `weave_resolve_opposing`) written at 0 on both weaves.
  - That is p10's arm A, which ran when unset meant off.
  - Under Amendment 4 the committed file alone runs W2, which is F2's physics.
- **F2** is `scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2.yaml` (395a111cb991) as committed.

**Copies.**

- They are written on the VM under `runs/p24_a3/scenarios/`. They are never committed, never archived as scenarios,
  and never ingested.
- Each header names the source, its policy-v4 hash and u.
- `corridor_a3.py check-copy` refuses a copy that differs from its source in anything but the name, the T.H.52
  share and (F1) the switches, and a source that no longer hashes as above. A refused copy runs nothing of its
  family.

| order | label (suffix of the family's scenario name) | scenario | config hash (v4) [computed] |
|---|---|---|---|
| 1 | `…_dc_cal_w1b_w2_a3u0` | the committed F2 file | 395a111cb991 (v3 5080d84d4725, p10's arm B) |
| 2 | `…_dc_cal_w1b_w2_a3u1` | F2 + `{u: 1.0}` | e69cad3d08b0 |
| 3 | `…_dc_cal_w1b_a3u0` | F1 + W2 off | c6151efb4a76 (v3 d12d66b0585b; p10's arm A was 0d26de2a5f01, the switches unset) |
| 4 | `…_dc_cal_w1b_a3u1` | F1 + W2 off + `{u: 1.0}` | 1c19215b5390 |
| 5 | `…_dc_cal_w1b_w2_a3u05` | F2 + `{u: 0.5}` | f33d8ee3de97 |
| 6 | `…_dc_cal_w1b_a3u05` | F1 + W2 off + `{u: 0.5}` | 03fec0ed5521 |

**Per arm.**

- The 20-seed four-hour battery (`spawn_seeds(42, 20)`) at `--procs` min(PROCS, 10), against the calibration-day
  targets, profile `fhwa_tat3_2004`.
- The baseline gate on the phase-1 day sets, and the gated report.
- `corridor_a3.py lanes`: per-lane hourly flows at S790 and 1 m upstream of the T.H.52 section's end, from the
  battery's one kept trajectory (seed 6914975401685141156).
- A light archive.

The order lets a cap cut whole arms while F2's verdict, then F1's, stays readable. After the six arms comes
`corridor_a3.py evaluate`, which writes `artifacts/a3_range.json`. The ingest takes `a3_range.json` and
`a3_lanes_*.json`; the batteries, gates and run trees are taken by the existing lines.

**Launch** (the owner's call, from a pushed commit):

```sh
scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p24 --machine n2d-standard-16 \
  --zone us-central1-a,us-central1-b,us-central1-c,us-central1-f --bucket gs://<bucket>/p24 \
  --self-delete --via-bucket --data-set none --cap-min 420 --pipeline-args '--stages "p24_i94_a3"'
```

The stage caps the batteries at ten processes itself, so `--procs 10` in `--pipeline-args` is optional.

**Cost** (item 7). About $3.9 for six batteries of about 52 min each plus gates and boot. `--cap-min 420` bounds it.

## 5. The readout (`artifacts/a3_range_2026-10-07/harness/corridor_a3.py evaluate`)

**Per family and arm**, item 4's readings:

- S790 and S97 flows and GEH per scored hour (06:30, 07:30, 08:30);
- the gate's C1, C3, C4 and C6 rows on both day sets;
- the calibration-day C1 and C3 (15 min) per replicate. These are re-scored with the gate's own code (the gate
  artifact records C3 per replicate but not C1) and checked against the gate's C1 mean and C3 values;
- realised demand, overall and at T.H.52;
- collisions by section;
- locks by `corridor_w1b.py`'s front-row reader and by the battery's `validation.locks`;
- per weave: given-up exits, W1b releases (as a share of the entrance's departures, against CW5b's 1 % as a
  disclosed cost), W2's counters, and the crossings `n_changed_in` and `n_changed_out`;
- the T.H.52 share three ways: the plan's record (u05, u1, with the clipped windows), the planned routes
  (`journeys.parquet`, every arm) and the departed entrants' destinations;
- the lanes file.

**Contrasts against u0.** Paired t with 19 df: the flows and GEH < 5 counts per station-hour, the departed share,
and the per-replicate C1 and C3.

**Rule M1-M4** at u1, exactly as item 5 states it. The verdict is undetermined (null) when an input is missing or
a problem is recorded.

**Also reported.**

- The adoption's range reading: each moved headline at u0 with its u1 value beside it.
- The u0 arm against p10's battery of the same physics, seed by seed. For F2 this is Amendment 4's item (i).

**Never** a chosen share.

## 6. Limits

- **The timing rule is free flow.** Under the peak queue at S790 vehicles reach the gore later than τ, so traders
  meet at the gore within about a window, not exactly.
- **The exit draw keeps its departure-time fraction** (§2). The clip therefore uses the model's gore exit volume,
  which differs from the counts' `v_OFF,w` around 07:50.
- **The empty network's first minutes.** The fill clips the 05:30 window at u1, inside the warm-up hour.
- **u05 is one interior point.** It shows shape only.
- **Ruth St** stays at the proportional split and is unexamined.
- **The lanes come from one seed's trajectory.** They are a description, not a contrast.
- **The identity check is the W1 method on fixtures.** No corridor run was made on the laptop. F1's u0 copy is p10's
  arm A in physics but not in hash (the switches are written explicitly), and today's code is not p10's (5516e05).
  The readout reports every per-seed difference against p10 and reads nothing into one.

## 7. Reproduce

```sh
H=artifacts/a3_range_2026-10-07
uv run --no-sync python $H/harness/corridor_a3.py expected --out $H/a3_expected.json   # netconvert only
# identity (two git-archive trees, the change tree with config.py, vehicles.py, runner.py copied in):
#   PYTHONPATH=<tree>/packages/... python $H/identity/cmp_a3.py <head tree> OUT WORK   (once per tree)
#   (cd <head tree> && PYTHONPATH=... python $H/identity/hashes_a3.py OUT)             (once per tree)
#   python $H/identity/compare_a3.py DIR $H/identity/summary.json
uv run --no-sync pytest tests/test_flowstate_core/test_ramp_to_ramp_range.py \
  tests/test_microsim/test_microsim_weave_share_window.py tests/test_scripts/test_p24_stage.py \
  tests/test_scripts/test_corridor_a3_harness.py -q -p no:cacheprovider
```

## 8. Result — 2026-10-08

*Written 2026-10-08, 11:10 CDT, after the run, from the records named with each number. §0–§7 are the build and the
pre-registration, unchanged. Nothing was simulated for this note; nothing is adopted and no share is chosen.*

### 8.1 Provenance and checks

- **Run.** Stage `p24_i94_a3`, VM flowstate-p24 (us-central1-c, n2d-standard-16, self-deleting), 06:18:58–09:43:33
  UTC (OK, 12,275 s), `PIPELINE_EXIT rc=0` at 09:44:00 (`p24_final.tgz`: `logs/pipeline.log`). Simulation per
  battery: F2 u0 2,937 s, u1 1,185 s, u05 1,822 s; F1 u0 2,724 s, u1 1,185 s, u05 1,760 s (`logs/p24_i94_a3.log`).
  About $2.7 [estimate: about 210 VM-minutes at the program's $0.013/min], against item 7's $3.9.
- **Code.** 466193f, the commit launched (the coordinator's launch record); the readout carries no `code` field, and
  `pipeline.log`'s `commit=8da6b92` is the VM's own snapshot commit, not in the repository. ef28156, committed during
  the launch, changed docs only.
- **Hashes.** Each arm's battery, gate and gated battery quote the hash that the stage's `check-copy` line recorded
  and §4's table predicted (u0 / u05 / u1): F2 395a111cb991 / f33d8ee3de97 / e69cad3d08b0; F1 c6151efb4a76 /
  03fec0ed5521 / 1c19215b5390. Recomputed with `config_hash` from the `config` of each of the 120 replicates'
  `meta.json` (archive), every one equals its battery's. The configs differ from F2 u0's only in the name, the T.H.52
  block's `ramp_to_ramp_share` (`{u, s_max: 0.7}`) and, in F1, W2's three switches at 0 on both weaves. F2 u0 is the
  committed file: 395a111cb991 (policy v4) = 5080d84d4725 (v3).
- **Records.** The 25 ingested artifacts and the six report directories (30 files) are byte-identical to the archive.
  The readout's `problems` is empty. Every arm's re-scored per-replicate C1 and C3 equal its gate's
  (`c1_c3_against_gate`). Every paired interval below was recomputed from the readout's per-seed values and agrees.

### 8.2 The reproduction of p10's arms: Amendment 4 item (i)

- **F2 u0 against arm B** (`artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2.json`, 5080d84d4725):
  - 20 of 20 seeds identical in every per-seed field p10 recorded (`run_dir` aside). `weave_releases` is new, listed
    and not compared. The departed shares are equal at every seed.
  - The gate's rows, its GEH interval and its per-replicate 5-, 15- and 60-min RMSPE equal arm B's gate's on both day
    sets.
  - The per-seed weave counters equal `artifacts/weave_w2_corridor.json`'s arm B in all 320 values: given-up exits,
    W1b releases, W2's four counters and entrance departures.
- **F1 u0 against arm A** (`…_w1b.json`, 0d26de2a5f01): the same in 20 of 20 seeds and 160 counter values (arm A
  recorded no W2 counters), its one T.H.52 collision (seed 5690692725577505498) included.
- **What this settles.** Current code (466193f, which carries the veto-capture fix) reproduces p10's arms on I-94, as
  Amendment 4 inferred. Item (i) holds and the defaults stand. The label "the reproduction re-run of p10's arm B is
  pending" on p16's and p17's results is satisfied (protocol, Amendment 4, "Resolved — 2026-10-08").

### 8.3 The arms (`spawn_seeds(42, 20)`, paired; calibration-day targets)

S790 and S97 hourly flows: mean veh/h (seeds of 20 with GEH < 5). The readout's `stations`.

| station, hour | observed | F2 u0 | F2 u05 | F2 u1 | F1 u0 | F1 u05 | F1 u1 |
|---|---|---|---|---|---|---|---|
| S790 06:30 | 4,846 | 3,871 (0) | 4,178 (0) | 4,572 (19) | 3,884 (0) | 4,185 (0) | 4,573 (18) |
| S790 07:30 | 3,965 | 3,882 (20) | 4,197 (19) | 4,089 (20) | 3,886 (20) | 4,204 (20) | 4,085 (20) |
| S790 08:30 | 4,114 | 3,874 (20) | 4,036 (20) | 3,743 (0) | 3,867 (18) | 4,023 (20) | 3,744 (0) |
| S97 06:30 | 4,536 | 3,636 (0) | 3,919 (0) | 4,230 (13) | 3,644 (0) | 3,923 (0) | 4,239 (15) |
| S97 07:30 | 4,077 | 3,817 (16) | 4,205 (20) | 4,241 (20) | 3,820 (17) | 4,215 (20) | 4,235 (20) |
| S97 08:30 | 3,900 | 3,768 (20) | 3,993 (20) | 3,645 (18) | 3,766 (20) | 3,986 (20) | 3,645 (18) |

The gate and the other readings: `baseline_gate_<label>.json`, `validation_<label>.json`, and the readout's `weaves`
and `share`. C1 and C3 are given as calibration / validation days.

| reading | F2 u0 | F2 u05 | F2 u1 | F1 u0 | F1 u05 | F1 u1 |
|---|---|---|---|---|---|---|
| C1 GEH < 5 (≥ 85 %) | 36.1 / 33.0 % | 75.1 / 73.5 % | 79.4 / 78.6 % | 36.2 / 33.2 % | 74.9 / 73.2 % | 79.9 / 78.9 % |
| C3 RMSPE 15 min (≤ 15 %) | 38.3 / 37.9 % | 41.8 / 52.7 % | 73.0 / 86.6 % | 38.4 / 37.8 % | 42.5 / 54.3 % | 72.8 / 86.5 % |
| C4 km/h (fronts of 20) | 5.7 (20) | 4.1 (18) | 2.7 (7) | 5.7 (20) | 3.8 (18) | 2.5 (9) |
| C5 collisions | 0 | 0 | 0 | **1** (T.H.52) | **1** (T.H.52) | 0 |
| locks, both readers | 0 | 0 | 0 | 0 | 0 | 0 |
| realised demand (lowest) | 0.984 (0.982) | 0.999 (0.996) | 1.000 (1.000) | 0.984 (0.982) | 0.999 (0.997) | 1.000 (1.000) |
| T.H.52 entrants departed | 0.898 | 0.994 | 1.000 | 0.898 | 0.997 | 1.000 |
| given-up exits, Ruth St / T.H.52 | 1.95 / 1.27 % | 2.25\* / 0.68 % | 2.41\* / 0.24 % | 1.76 / 1.22 % | 2.19\* / 0.64 % | 2.38\* / 0.25 % |
| W1b releases, Ruth St / T.H.52 | 257 = 1.26 %† / 1 | 83 = 0.41 % / 5 | 25 = 0.12 % / 3 | 257 = 1.26 %† / 3 | 90 = 0.44 % / 3 | 21 = 0.10 % / 2 |
| W2 vetoes, Ruth St / T.H.52 | 6,908 / 16,011 | 2,980 / 9,094 | 1,072 / 3,498 | off | off | off |
| T.H.52 `n_changed_in` / `_out` | 41,042 / 51,693 | 24,642 / 33,311 | 12,582 / 14,000 | 40,844 / 51,778 | 24,449 / 33,218 | 12,629 / 14,045 |
| T.H.52 share, run, planned / departed | 0.202 / 0.199 | 0.451 / 0.451 | 0.666 / 0.666 | 0.202 / 0.199 | 0.451 / 0.451 | 0.666 / 0.666 |
| clipped windows, mean (range) | unset | 0.6 (0–2) | 13.25 (10–16) | unset | 0.6 (0–2) | 13.25 (10–16) |
| total delay incl. waiting, veh-h | 5,330.7 | 2,200.3 | 851.1 | 5,327.8 | 2,147.0 | 855.7 |

\* above the battery's 2 % given-up flag. † above Amendment 4's 1 % disclosure. C6 passes in every arm: the observed
days hold no bottleneck active for 30 minutes (0 observed on the calibration days, 1 on the validation days).

- **The gate fails in every arm**, on C1 and C3 on both day sets and on C4, and F1's u0 and u05 also fail C5.
- **At u0, S790 carries 3,871–3,882 veh/h in every hour**, consistent with the discharge of the queue behind T.H.52,
  and 10 % of US 52's entrants never depart. At u1 it follows the demand: above u0 in the peak, below it at 08:30.

### 8.4 Paired contrasts against u0 (paired t, 19 df; the readout's `contrasts`)

| arm − u0 | F2 u05 | F2 u1 | F1 u05 | F1 u1 |
|---|---|---|---|---|
| S790 06:30, veh/h | +307.6 [+289.0, +326.1] | **+700.7 [+668.8, +732.6]** | +300.9 [+276.9, +324.8] | **+688.3 [+660.3, +716.3]** |
| S790 07:30 | +315.2 [+291.1, +339.3] | +206.5 [+171.3, +241.7] | +317.9 [+290.4, +345.4] | +199.0 [+155.8, +242.2] |
| S790 08:30 | +161.5 [+113.8, +209.2] | −131.4 [−153.6, −109.3] | +155.8 [+124.5, +187.1] | −123.5 [−146.9, −100.1] |
| S97 06:30 | +283.6 [+261.8, +305.4] | +594.4 [+566.5, +622.3] | +279.2 [+254.9, +303.5] | +594.3 [+568.5, +620.2] |
| S97 07:30 | +388.0 [+363.4, +412.6] | +424.4 [+387.7, +461.1] | +395.5 [+366.2, +424.9] | +415.1 [+369.7, +460.5] |
| S97 08:30 | +225.6 [+190.0, +261.1] | −122.2 [−147.9, −96.5] | +219.7 [+195.3, +244.1] | −120.6 [−142.7, −98.6] |
| realised demand | +0.0150 [+0.0142, +0.0159] | +0.0160 [+0.0155, +0.0165] | +0.0156 [+0.0149, +0.0163] | +0.0161 [+0.0156, +0.0166] |
| C1, calibration days | +0.391 [+0.373, +0.408] | +0.433 [+0.411, +0.456] | +0.387 [+0.358, +0.416] | +0.437 [+0.409, +0.465] |
| C3 15 min, calibration days | +0.034 [+0.004, +0.065] | +0.347 [+0.327, +0.366] | +0.041 [+0.017, +0.064] | +0.344 [+0.324, +0.364] |

### 8.5 M1–M4 at u1 against u0, and the verdict (`verdict_u1`)

| rule (item 5) | F2, the reference | F1, W2 off |
|---|---|---|
| M1: S790 06:30 lower bound ≥ +100 veh/h; realised demand at most 1 pp lower | **holds**: +700.7 [668.8, 732.6]; +0.016 | **holds**: +688.3 [660.3, 716.3]; +0.016 |
| M2: a GEH < 5 count at S790 or S97 moves by ≥ 5 of 20 | **holds**: S790 06:30 0 → 19, S790 08:30 20 → 0, S97 06:30 0 → 13 | **holds**: 0 → 18, 18 → 0, 0 → 15 |
| M3: calibration-day C1 or C3 interval wholly beyond ±2 pp | **holds**, both (C3 is worse) | **holds**, both |
| M4: a collision or lock in one arm only | does not hold (none in either arm) | **holds**: the u0 collision, none at u1 |
| verdict | **material** | **material** |

No seeds were added. The S790 interval is not within ±100 veh/h in either family. The share's effect does not
depend on W2 at this resolution. The F2 − F1 difference of the u1 − u0 differences, paired by seed (descriptive, not
pre-registered), is +12.4 [−34.1, +58.9] veh/h at S790 06:30, −0.004 [−0.031, +0.024] in C1 and +0.003
[−0.018, +0.024] in C3.

### 8.6 The range reading (adoption item (c))

**Range over the T.H.52 ramp-to-ramp share [proportional, 0.70], stated assumption** (`range`; u = 0 → u = 1 on F2,
F1 in brackets where it differs): C1 36.1 → 79.4 % [36.2 → 79.9 %] on the calibration days and 33.0 → 78.6 %
[33.2 → 78.9 %] on the validation days; C3 38.3 → 73.0 % [38.4 → 72.8 %] and 37.9 → 86.6 % [37.8 → 86.5 %]; C4
5.7 → 2.7 km/h [→ 2.5]; C6 pass → pass; collisions 0 → 0 [1 → 0]; locks 0 → 0; S790 at 06:30 / 07:30 / 08:30
3,871 / 3,882 / 3,874 → 4,572 / 4,089 / 3,743 veh/h; S97 3,636 / 3,817 / 3,768 → 4,230 / 4,241 / 3,645 veh/h.

**The gate (item (b)).** It is judged at u = 0 and fails, as before. At u = 1 it also fails (C1, C3, C4), so it is
not "not robust to the share"; it fails at both ends. The S790 shortfall at 06:30 is 975.5 veh/h at the proportional
split and 274.8 veh/h at 0.70 (F2). So the T.H.52 shortfall is reported "at the proportional split", not as a
merge-model finding.

**Scope.** These values belong to the two families run. D10's `_rb` and `_rbc` and B5's I-94 refit were not run at
u = 1, so they have no u = 1 value of their own. Which reading they carry is open (§8.10).

### 8.7 Where the speeds move [computed, F2]

The table gives mean station point speeds in km/h over the study period's 42 windows and the 20 replicates, from
the archived `observed_scores.json`. The observed side is the calibration-day mean. This pooled form gives 15-min
RMSPE 38.4 % / 73.1 %; the gate's per-replicate mean is 38.3 % / 73.0 %.

| | S1063 | S1066 | S1068 | S1069 | S1948 | S792 | S791 | S790 | S97 |
|---|---|---|---|---|---|---|---|---|---|
| observed | 114.7 | 106.6 | 74.3 | 55.0 | 61.4 | 49.9 | 41.6 | 48.9 | 68.2 |
| u0 | 79.9 | 51.9 | 43.0 | 36.0 | 44.1 | 50.4 | 40.1 | 44.9 | 64.3 |
| u1 | 83.7 | 84.3 | 81.1 | 70.4 | 66.0 | 67.6 | 75.1 | 72.7 | 64.6 |

- **At u0** every station upstream of S792 runs slower than observed: mean signed 15-min error −26 to −52 %.
- **At u1**, with no queue behind T.H.52, those stations recover, and S1068–S790 run faster than the observed
  slowdown there: +16 to +112 %.
- **Neither end of the range places the observed slowdown.**

### 8.8 Per-lane hourly flows, seed 6914975401685141156 (`a3_lanes_<label>.json`)

Veh/h by lane 0 / 1 / 2 / 3 (lane 0 rightmost) on F2; F1's rows are in its three files. Each S790 row sums to within
1 veh/h of that seed's battery flow.

| F2 | S790 (x 10,128 m) 06:30 | 07:30 | 08:30 | gore (x 10,730.6 m; lane 0 auxiliary) 06:30 | 07:30 | 08:30 |
|---|---|---|---|---|---|---|
| u0 | 308 / 644 / 1,141 / 1,762 | 273 / 707 / 1,124 / 1,824 | 301 / 629 / 1,224 / 1,799 | 1,002 / 1,233 / 1,721 / 195 | 1,036 / 1,312 / 1,794 / 191 | 1,065 / 1,259 / 1,772 / 180 |
| u05 | 371 / 684 / 1,357 / 1,820 | 343 / 713 / 1,291 / 1,816 | 125 / 1,010 / 1,232 / 1,678 | 1,186 / 1,368 / 1,858 / 176 | 1,169 / 1,451 / 1,825 / 236 | 1,034 / 1,379 / 1,802 / 204 |
| u1 | 45 / 1,414 / 1,482 / 1,679 | 90 / 1,034 / 1,254 / 1,674 | 13 / 1,095 / 1,065 / 1,569 | 1,322 / 1,524 / 1,866 / 162 | 1,239 / 1,476 / 1,788 / 201 | 924 / 1,294 / 1,641 / 153 |

At u1, S790's lane 0 nearly empties (308 → 45 veh/h at 06:30; F1 287 → 157) and lane 1 doubles. This is consistent
with fewer mainline vehicles bound for 242B, whose exits the swap gives to US 52 entrants. It describes one seed; it
is not a contrast.

### 8.9 The pre-run expectations, scored

The realised shares and crossers are computed from the archived run trees: u05 and u1 from the plan record in
`meta.json`; u0 from the departed vehicles, each windowed at its origin's mean free-flow time. On u05 and u1 the two
methods agree within 0.3 veh/h. Item 3's figures are the count data; §2's are the model's volumes.

| expectation (source) | expected | realised |
|---|---|---|
| s at 05:30–05:50 / 06:30–07:30, u0 · u05 · u1 (item 3; §2) | 0.29 / 0.18 · 0.50 / 0.44 · 0.70 / 0.70 (§2 u1: 0.698 / 0.700) | 0.29 / 0.18 · 0.49 / 0.44 · 0.69 / 0.70 |
| crossers 06:30–07:30, veh/h (item 3; §2) | 1,955 / 1,282 / 609; 1,988 / 1,315 / 643 | 1,986 (departed only) / 1,312 / 649 (588–693) |
| clipped windows (item 3; §2) | 0 / 1 / 8; 0 / 0 / 12 | unset / 0.6 (0–2) / 13.25 (10–16) |
| where u1 clips (§2) | 05:30, 07:35–08:10, 08:25, 08:35, 08:40 | 07:40–08:10 in 17–20 of 20 seeds; 07:35 and 08:15–08:40 in 8–15; 05:30 in 11; scattered windows of 06:50–07:30 in 1–7 |
| GEH < 5 at S790 06:30 needs ≥ about 4,505 veh/h (DECISIONS §A3.3) | | u1 mean 4,572; the seeds failing are exactly those below it (4,493; 4,471) |
| peak crossers, proportional → 0.70 (TH52_CROSSING_SHARE §5) | 1,963 → 625 (−68 %); fixture window 1,917 → 914 (−52 %) | 1,986 → 649 (−67 %) |
| no collision at any share (fixture, TH52_CROSSING_SHARE §10.4) | | F2: none. F1 (W2 off): one at u0 and one at u05 |
| speed lags flow (§10.4) | | C3 38.3 → 41.8 → 73.0 % while C1 rises |
| Amendment 4 (i): u0 reproduces arm B per seed | inferred | 20 of 20 |
| cost (item 7) | about $3.9 | about $2.7 [estimate] |

### 8.10 What this means and does not

- **The share is material.** By the rule fixed before the run, the unmeasured T.H.52 ramp-to-ramp share moves the
  I-94 headlines.
  - Over [proportional, 0.70] it moves calibration-day C1 by +43 pp and C3 by +35 pp, the wrong way for speeds.
  - Both moves are larger than either D10 rule's (docs/I94_D10_RESULT.md §5).
  - Total delay including waiting falls from 5,331 to 851 veh-h.
  - A strategy result on I-94 inherits this spread.
- **It does not choose a share** (item 6, adoption (d)). The flows fit better at u1 and the speeds fit worse, and the
  gate fails at both ends. Nothing in the round says where in the range the road is.
- **What licenses a value.** Only a dated §7 amendment resting on one of three routes, with results then reported
  under both splits:
  - (a) MnDOT's 2022–2024 Hwy 52 / I-94 study report, if it gives an AM-peak volume or share for US 52 NB → 242B.
    Requesting it is the owner's call.
  - (b) A pre-registered count-based estimate (MN/RC-1999-40). It is adopted only if its 95 % interval is narrower
    than 0.20; TH52_CROSSING_SHARE §4 expects it to fail.
  - (c) A direct count: one AM peak of video at the 242B gore, or an agency origin–destination product.
- **Open (the owner's or the coordinator's).** Which range reading I-94 arms carry that were not run at u = 1 (D10's,
  B5's): F2's movement, labelled as F2's, or their own u = 1 battery (a u1 battery simulated in 1,185 s here).

### 8.11 Limitations

- **u05 is one interior point** and shows shape only. The wave rows at u1 are underpowered (fronts in 7 and 9 of 20).
- **F1 writes W2's switches at 0**, which Amendment 4 rule 3 allows only to reproduce a published result; F1's u0
  does (arm A). Its u05 and u1 are the pre-registration's comparison arms, not results of the adopted model, and F1
  fails C5 at u0 and u05.
- **Ruth St.** Its W1b release share is 1.26 % at u0 in both families, above 1 % as in p10 and in D10's reference
  (D10's `_rbc`: 1.38 %); at u05 and u1 it is 0.10–0.44 %, but given-up exits there pass the battery's 2 % flag
  (2.19–2.41 %). Its split stays proportional and unexamined (rule 3).
- **Timing and sources.** The timing rule is free flow and the clip uses the model's gore volumes (§6); the u0
  crossers count departed vehicles only. The lanes are one seed's. §8.7's speeds and §8.9's windowed shares and
  crossers come from the archived run trees, which are not committed.
- **The reports do not carry the label.** The arms' `report.md` files state neither u nor the range label; this note
  does. The batteries' own `link_flows_geh` row reads 86.2 % (F2) and 86.6 % (F1) at u1, on link-hours from 06:30;
  it is not the gate's C1 (79.4 % and 79.9 %, anchored at the study period's start), and a u = 1 value never passes
  the gate.
