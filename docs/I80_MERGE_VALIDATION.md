# E11: merge gaps and speeds on NGSIM I-80, a site never calibrated on (result)

Written 2026-10-08 from stage `p21_i80_merge`'s records. Pre-registration: docs/PRE_FRISCO_PROGRAM.md "E11",
Amendment M1 in docs/MERGE_MODEL.md (E3, approved 2026-10-07), and the readout
`artifacts/i80_merge_2026-10-07/harness/corridor_e11.py`. Every value below comes from the `artifacts/i80_*.json`
record named with it. Tables give model intervals to two decimals and ratios and E1 medians at the records'
precision; the prose rounds to two decimals.

## 1. Result

`artifacts/i80_merge_validation.json`: `problems` is empty and `reading_text` is **"measured is not rescued"**.

| arm | scenario (config hash) | E1 | E2 | E3 (overlaps) | E4 |
|---|---|---|---|---|---|
| kept (`merge: lane_change`, LC2013) | `i80_replica` (43a66c754f31) | met | **not met** | **not met** (0 of 6) | met |
| measured (`merge: measured`) | `i80_replica_measured` (7ef6ce60af40) | met | **not met** | **not met** (3 of 6) | met |

The rescue rule needed `measured` to meet E1–E4 while the kept configuration failed at least one. The kept
configuration failed two, but `measured` also failed E2 and E3, so `rescue.rescued` is false. Neither
configuration meets the pre-registered criteria on I-80's merges.

## 2. Provenance

- **Run.** The stage ran alone on the self-deleting VM flowstate-p21 (us-central1-a, instance
  8140332728377320619; n2d-standard-16 per the launch line in `artifacts/i80_merge_2026-10-07/stage_p21_e11.sh.txt`).
  It ran from 04:23:15Z to 04:31:12Z (OK, 477 s), and `PIPELINE_EXIT rc=0` came at 04:31:20Z (archive
  `p21_final.tgz`: `logs/pipeline.log`, `logs/p21_i80_merge.done`).
- **Code.** Source commit 8ea59ec94ecd… (`code` in the readout and in every VM record). The VM snapshot 200b97eb…
  is made on the VM by `scripts/gcp/vm_setup.sh` and is not in this repository. The five script-written records
  state `code_dirty: true`: a tracked file under `packages/`, `scripts/`, `scenarios/`, `pyproject.toml` or
  `uv.lock` differed from the snapshot, and the records do not say which. Earlier VM records show the same,
  e.g. `lane_change_relaxation_us101.json`.
- **Checked locally, 2026-10-08.**
  - The eight ingested files are byte-identical to the archive's copies, and the readout's input sha256s match.
  - `config_hash(ScenarioConfig.model_validate(yaml))` gives 43a66c754f31 and 7ef6ce60af40, with `flowstate_core`
    both at 8ea59ec and in the current tree. These equal every run's `config_hash` in
    `i80_merge_sim_{kept,measured}.json` and the readout's `arms`.
  - The two documents differ only in `name` and the on-ramp's `merge: measured`.
  - Re-running `corridor_e11.py readout` on the ingested inputs reproduces the readout except `created_at`,
    `code` and `vm_snapshot`.
  - The observed and simulated measures were not re-derived: the raw data and the trajectories never left the VM.
- **Data** (`artifacts/i80_data.json`, `logs/p21_i80_merge.log`).
  - Source: raw NGSIM I-80 (data.transportation.gov `8ect-6jqj`, `location='i-80'`), not the Montanino–Punzo
    reconstruction. 4,566,387 rows, 0 exact duplicates.
  - Periods, 2005-04-13 PDT: p1 15:58:55.3–16:15:36.5, p2 16:59:27.5–17:15:46.1, p3 17:12:45.6–17:32:14.0.
  - The replica block is p2 + p3 (data hash 82f1bb3f…), with windows p2 36.8–908.3 s and p3 908.3–1811.0 s.
  - Entering changes selected: p2 47 and p3 55, i.e. 102. This matches `i80_merge_observed.json`.

## 3. What was built on I-80, without fitting (`artifacts/i80_replica_inputs.json`)

- **Geometry.**
  - The site is 505 m long. The Powell St acceleration lane runs from the gore at 117.76 m to 202.30 m and
    compiles to 84.54 m: lane 7 next to lanes 1–6, with no exit lane.
  - The map is a straight, hand-built layout. Its nodes were moved until netconvert compiled every edge within
    0.5 m of its measured length; the first pass was off by up to 9.07 m and the second was exact.
  - Speed limits: 65 mph on the mainline (California's maximum; the posting is not verified) and netconvert's
    80 km/h on the 200-m ramp edge.
- **Demand.** Counted entries in 5-min windows: 3,124 on the mainline and 421 on the ramp (p2 206, p3 215), with
  the observed entry-lane shares. The first window's rate is held through a 180-s warm-up, giving 3,961
  vehicles per run, 465 of them on the ramp.
- **Boundary.** The mean mainline speed in the site's last 100 m every 30 s (0.8725–10.067 m/s), applied on the
  exit edge.
- **Fleet.** The kept I-24 arm's block from `scenarios/i24_replica_flow_rc_speedcal_dc_refit.yaml`: IDM population
  `artifacts/idm_i24_capacity_amax_k1.0.json` (heterogeneity 0.12), LC2013 with `lc_keep_right` 0, speed factor
  1, and no heavy vehicles.
- **Runs.** 20 seeds per arm (`spawn_seeds(42, 20)`), 2,147 s at a 0.5-s step. The same extractors
  (`scripts/i80_merge_measures.py`) measured I-80 and the simulated trajectories.
- **`measured`'s inputs.** The central set of `artifacts/merge_model_params.json`. Its entering critical gaps at
  acceleration lanes come from I-24: lead median 0.417 s, lag 0.588 s.

## 4. I-80's merge (`artifacts/i80_merge_observed.json`; 200-resample bootstrap 95 % intervals)

| entering changes (lane 7 to 6), at the change | median | 95 % interval | n |
|---|---|---|---|
| accepted lead / lag time gap [s] | 1.35 / 1.68 | [1.15, 1.52] / [1.47, 1.87] | 102 / 100 |
| joint critical gap, lead / lag [s] | 0.24 / 0.34 | [0.08, 0.42] / [0.07, 0.58] | 97 drivers, 23 with a rejected gap |
| partner speed, follower side (changer − new follower) [m/s] | +0.34 | [0.00, 0.55] | 101 |
| partner speed, leader side (new leader − changer) [m/s] | +0.15 | [−0.08, 0.43] | 102 |
| `ratio_pop` follower, 0 s → 10 s | 0.7668 → 0.8862 | [0.6586, 0.8888] → [0.7787, 0.9413] | 80 → 32 |
| `ratio_pop` leader, 0 s → 10 s | 0.5785 → 0.8809 | [0.5182, 0.735] → [0.7009, 0.9398] | 86 → 46 |
| changer / new follower speed, p50 of the critical-gap rows [m/s] | 3.63 / 3.22 | — | 102 |

The merge is congested. Entrants roll in at about 3.6 m/s into short gaps, and both sides relax towards a normal
gap within 10 s.

## 5. The criteria by arm (`artifacts/i80_merge_validation.json`, `criteria`)

**E3.** The model column gives the mean of the per-seed medians, its 95 % t-interval, and the number of seeds
that gave a reading.

| median | I-80 | kept | overlap | measured | overlap |
|---|---|---|---|---|---|
| accepted lead gap [s] | 1.35 [1.15, 1.52] | 2.82 [2.75, 2.88], 20 | no | 1.38 [1.33, 1.42], 20 | yes |
| accepted lag gap [s] | 1.68 [1.47, 1.87] | 5.15 [4.99, 5.32], 20 | no | 2.25 [2.21, 2.30], 20 | no |
| critical lead gap [s] | 0.24 [0.08, 0.42] | 0.63 [0.55, 0.71], 20 | no | 0.19 [0.15, 0.23], **18** | no (underpowered) |
| critical lag gap [s] | 0.34 [0.07, 0.58] | 1.35 [1.18, 1.52], 20 | no | 0.31 [0.20, 0.42], **9** | no (underpowered) |
| partner speed, follower side [m/s] | +0.34 [0.00, 0.55] | +1.73 [1.46, 2.00], 20 | no | −0.02 [−0.09, 0.04], 20 | yes |
| partner speed, leader side [m/s] | +0.15 [−0.08, 0.43] | +1.36 [1.26, 1.45], 20 | no | +0.25 [0.20, 0.29], 20 | yes |

`measured`'s missing readings are seeds whose joint fit sits at the estimator's 0.01-s lower bound: 2 seeds for
lead and 11 for lag (`i80_merge_sim_measured.json`, `per_seed[].critical_gap_s`). Both of its critical-gap
intervals lie inside I-80's, so only M1's 20-reading requirement fails them.

**E2.** Pooled `ratio_pop` medians, 0 s → 10 s (n).

| side (cap) | I-80 | kept | holds | measured | holds |
|---|---|---|---|---|---|
| follower (≤ 0.9) | 0.7668 → 0.8862 (80 → 32) | 1.0927 → 1.111 (1,453 → 2,633) | no: above the cap | 0.9155 → 0.9753 (2,473 → 2,465) | no: above the cap |
| leader (≤ 0.8) | 0.5785 → 0.8809 (86 → 46) | 1.127 → 1.0122 (4,960 → 5,607) | no: above the cap, no recovery | 0.5654 → 0.9212 (2,906 → 2,856) | yes |

**E1.** Pooled median partner speeds [m/s]:
- follower side: I-80 +0.3353 (101), kept +1.8928 (6,292), measured +0.002 (3,273);
- leader side: I-80 +0.1524 (102), kept +1.3517 (7,426), measured +0.2473 (3,407).

The signs match in both arms. **E4:** 0 collisions in each arm's 20 runs.

## 6. What the failures mean physically

**Kept configuration (LC2013).**
- Entrants slow almost to a stop at the end of the acceleration lane: 0.91 m/s pooled in the 50–100 m bin,
  against I-80's 3.05.
- They then cross ahead of a near-stationary new follower: 0.58 m/s against I-80's 3.22
  (`pooled.critical_gap_s.rows`). This is consistent with the target-lane follower yielding.
- A lag time gap is a distance divided by the follower's speed, so the slow follower stretches it: 5.15 s
  against 1.68.
- The gaps are longer than normal on both sides (1.09 and 1.13 of a normal gap, against 0.77 and 0.58). The
  leader side shrinks afterwards instead of recovering.
- The entrant crosses 1.73 m/s faster than its new follower and 1.36 m/s slower than its new leader, against
  0.34 and 0.15 on I-80.
- In short, LC2013 merges by stopping, waiting, then crossing. I-80's drivers merge with a rolling squeeze.

**`measured`.**
- It reproduces the rolling merge: changer and new follower at 3.57 and 3.23 m/s, against 3.63 and 3.22.
- It matches the accepted lead gap, both partner speeds, and the leader side's short gap and its recovery.
- It does not reproduce the follower side. The new follower keeps 0.9155 of a normal gap at the change, where
  I-80's keeps 0.7668. The model also takes longer lag gaps than I-80's drivers: 2.25 s against [1.47, 1.87].
- Its simulated changes contain few rejected gaps: 11–32 drivers with a rejection per seed, against 34–107 in
  the kept arm.

## 7. Reported, not gating (`reported`)

- **Acceleration-lane speed by 50-m bin,** given as I-80 / kept / measured:
  - 0–50 m: 2.63 [2.59, 2.65] / 2.52 [2.38, 2.65] / 2.34 [2.27, 2.41];
  - 50–100 m: 3.048 [3.048, 3.048] / 1.00 [0.85, 1.16] / 2.16 [2.06, 2.27].
- **Departures.** No locks and no breakdowns in either arm.
  - Kept: departed share 0.9793–0.99697, with all 465 ramp vehicles inserted in every run.
  - Measured: departed share 0.97475–0.98738. Only 417–461 of 465 ramp vehicles were inserted per run.
  - Summed over its 20 runs (`runs[].measured_merges`), `measured` forced 2,234 of 8,247 in-zone crossings and
    left 135 unfinished.

## 8. Limitations

- **Small observed sample.** 102 changes, of which 23 drivers carry a rejected gap for the critical gaps. The
  10-s ratios rest on 32 and 46 changes.
- **E1 has little power.** I-80's follower-side interval starts at 0.00 and its leader-side interval spans zero.
  `measured`'s follower-side sign rests on +0.002 m/s.
- **Unexplained observed count.**
  - The extraction finds 113 entering changes inside the zone (55 + 58). The block has 421 counted ramp
    entries and 445 raw 7→6 transitions (`zone_counts`), and no vehicle ends in lane 7.
  - The simulated arms find about one change per ramp vehicle (kept: 446–464 per seed).
  - The records do not explain the gap. If the selected changes are not representative of the ramp's merges,
    the observed medians are biased in an unknown direction. This should be checked on the raw data, on a VM,
    before E11 is cited outside the project.
- **Different selection shares.** The share of in-zone changes selected (confirmed, not suspect) is 102 of 113
  on I-80, 7,603 of 9,173 for kept and 3,530 of 8,138 for measured. `measured`'s measures describe fewer than
  half of its crossings.
- **Raw NGSIM.** Gaps rest on noisy positions, and time gaps divide by noisy speeds. The degenerate 50–100 m
  interval (3.048 m/s, i.e. 10 ft/s) shows many samples tied at one recorded value.
- **One site and one afternoon.** p1 is not contiguous with the modelled block. p2 and p3 overlap and are
  stitched at the switch.
- **Map.** The map was corrected so that netconvert compiles the measured lengths; uncorrected, it lengthens the
  acceleration lane (CHANGELOG, "E11 built"). There is no curvature, grade or lane width, and lane 1's HOV rule
  is not modelled.
- **Fleet.** The fleet is I-24's, with no heavy vehicles. On I-80, trucks were 3.8 % of vehicles in p2 and
  2.7 % in p3.

## 9. Consequence for E13 (the pre-registered rule's outcome)

- **`merge: measured` is retired.** E13 keeps it only if E11 rescues it, and E11 did not. Deleting it is E13's
  step and an ask-first decision for the owner; nothing has been deleted. The deletion covers
  `microsim.merge_model`, its runner paths, its tests, the three measured scenarios E13 names and the goldens
  `merge_measured` and `merge_measured_accel`. Once committed, `scenarios/i80_replica_measured.yaml` is a fourth
  `merge: measured` scenario that the deletion must also cover, since a refused value would make it unloadable.
- **The kept configuration is not validated on I-80 either.** It fails E2 and all six E3 overlaps. The rule
  chooses which model to keep; it does not validate the one kept. E13's single locked configuration therefore
  has merge gaps and partner speeds that fail on the only unfitted site tested, and docs/VALIDATION_REPORT.md
  must say so.
- **The rule does not rank the arms.** `measured` came closer on these measures: three of the six E3 overlaps
  and E2's leader side, against none for kept.
