# Amendment 3's range round (stage p24): built, not run (2026-10-07)

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
