# Design brief: one measured merge model (Frisco plan item 5, phase 2)

Written 2026-10-05 against `main` at 6358213. This is a read-only research brief: no repo file was changed and no simulation was run.

Abbreviations used throughout:
- `WMP` = docs/WEAVE_MODEL_PLAN.md
- `I24V` = docs/I24_VALIDATION.md
- `MN` = docs/ONBOARDING_MNDOT.md
- `cfg` = packages/flowstate_core/flowstate_core/config.py
- `runner` = packages/microsim/microsim/runner.py
- `L####` = a WMP line number

All line numbers are as of 6358213.

## 0. Summary

- **What real drivers do.** These are measured, with the caveats in §1.
  - At the I-24 Old Hickory (OH) acceleration lane, real entrants accept median critical gaps of **0.42 s to the leader and 0.59 s to the follower**.
  - They cross about **0.6 m/s faster than both new partners**.
  - They land at **0.6–0.9 of a normal car-following gap**.
  - On complete-coverage NGSIM US-101, the leader-side gap recovers with a supported **τ_r ≈ 7.5 s**.
- **What the model does.** The model's weave entrants:
  - show critical gaps of about 1.2 s;
  - cross at 5–6 m/s, against real entrants' 11.7 m/s;
  - cross 1.37 m/s *slower* than their new leader;
  - land at 1.05 (follower) and 1.31 (leader) of the normal gap.
- **The proposal.** One runner-driven *mandatory-change* model for acceleration lanes and weaving sections.
  - It keeps the weave's load-bearing core: gap choice, one-step cooperation, easing, pair release, guards and give-ups.
  - It makes three measured substitutions:
    1. per-driver log-normal **lead and lag critical gaps** per movement;
    2. a **speed-match ceiling**: the chosen gap's speed plus the measured offset, honouring `speed_factor`;
    3. a per-vehicle **`setTau` relaxation**, starting at the accepted gap and recovering over τ_r, for the entrant and its new follower.
  - The `scripted` and `weave` options and their 31 keys are deleted.
- **The honest prior.** Each principle has already been tried *alone*, and none moved capacity:
  - calibrated acceptance: WP-79/80;
  - post-crossing allowance: WP-90;
  - speed matching: I24V §0.8.

  The case for this design is that the three are coupled (§5.1): measured gaps are only usable with relaxation, and relaxation only has something to act on with measured gaps. That case is untested. §5.13 therefore puts cheap kill gates before any 20-seed battery.

---

## 1. Measured real-driver quantities

### Coverage rules

These rules apply to every I-24 number below. They come from WMP L8499–8573 (the corrections) and VM AE (L8721–8779, `artifacts/coverage_thinning_us101.json`).
- I-24 MOTION tracks about 0.5–0.65 of peak vehicle-time.
- **Bounds under partial tracking** (each is "the same or higher" or "lower" by construction):
  - space gaps: upper bounds;
  - lead time gaps: upper bounds;
  - the leader side's `ratio_eq`: an upper bound;
  - `ok_lead_time` refusal shares: lower bounds.
- **Not bounds but expected high:** lag time gaps, fitted critical gaps, the overall refusal share (expected low), the follower side's `ratio_eq`, and `ratio_pop`.
- **Robust:** partner speeds.
- Thinning complete US-101 to F = 0.65 / 0.5 shifts these quantities as follows:
  - lag time-gap p50: +0.78 / +1.53 s;
  - refusal share: −18.5 / −29.3 points;
  - critical gaps: "undetermined" (lead +0.10 s, lag +0.17 / +0.39 s).
- The artifact `i24_critical_gaps.json` still says `"upper bounds"` in its `limitations`. The corrections section supersedes that.

### 1.1 Critical gaps

Source: `artifacts/i24_critical_gaps.json` `.fits[]`. Method: joint lead–lag Troutbeck maximum likelihood, log-normal, 200-bootstrap 95 % CIs (WMP WP-78 L5921ff; VM Z L6111). I-24 is 30 Nov 2022 WB, 06:00–10:00.

| zone, movement | drivers (inconsistent, excluded) | lead median [95 %], σ | lag median [95 %], σ | by changer speed, lead / lag (v<10, 10–20, ≥20 m/s) |
|---|---|---|---|---|
| **OH acceleration lane, entering** | 3,213 (814) | **0.42 [0.37, 0.47]**, 1.42 | **0.59 [0.51, 0.66]**, 1.46 | 0.35 / 0.50, 0.44 / 0.64, 0.48 / 0.62 |
| HH–BR weave (585 m), entering | 1,881 (417) | 0.46 [0.40, 0.53], 1.44 | 0.92 [0.84, 1.03], 1.28 | 0.45 / 0.99, 0.45 / 0.84, 0.50 / 1.04 |
| HH–BR weave, exiting | 1,493 (489) | 2.89 [2.47, 3.38], 1.85 | 1.11 [0.91, 1.34], 1.90 | 3.87 / 1.24, 3.09 / 1.19, 1.87 / 0.84 |
| HH diverge, exiting | 1,688 (595) | 3.29 [2.62, 3.97], 2.12 | 2.93 [1.81, 4.55], 4.15 | poorly identified |
| BR diverge, exiting | 76 | at bound (not identified) | 0.40 [0.01, 3.3] | unfitted |
| US-101 lane-6 weave, entering (complete coverage, raw NGSIM; thinning artifact `.results` reference) | 180 (9) | 0.29 [0.23, 0.37] | 0.45 [0.36, 0.54] | — |
| US-101 weave, exiting | 72 (4) | 0.42 [0.06, 0.96] | 0.54 [0.28, 0.86] | — |
| *Model*, T.H.52 fixture, entering / exiting (`artifacts/th52_fixture_critical_gaps.json`, WP-78) | 320 / 868 | 1.18 / 0.89 | 1.21 / 0.72 | — |

**Reading the table.**
- **Speed dependence is not resolved.** The OH classes' CIs overlap: lead v<10 [0.27, 0.43] against 10–20 [0.36, 0.51]; lag [0.40, 0.64] against [0.55, 0.76]. The data support one time-gap distribution per movement and side, with the classes as a sensitivity.
- **Dispersion is very large.** σ ≈ 1.4 means p10 ≈ 0.16 × median and p90 ≈ 6 × median. 25–35 % of drivers are inconsistent, so the consistent-driver model fits only loosely (WP-78 reading (c), L6066).
- **Exiting lead side.** I-24's 2.9 s and US-101's 0.42 s disagree. VM Z could not tell whether the long lead is "chosen or is the slower auxiliary-lane traffic they fall in behind" (L6127).
- **Implied follower braking.** `.mapping[]` for OH: the lag medians (0.50–0.64 s) lie **below the absorption floor** of an IDM follower at the fleet means (0.94–1.12 s). No acceptance time gap reproduces them. Absorbing them would demand **4.8–12.8 m/s²** of the follower (`implied_follower_decel_ms2` 12.8 / 4.8 / 4.9).
  - Real followers evidently do not brake to equilibrium at once. This is the quantitative case for principle (iii).

### 1.2 Accepted gaps at the change

Source: `artifacts/i24_lane_change_gaps.json` `.summary_by_zone[]`. Confirmed, non-suspect changes; bumper-to-bumper gaps. Lead time gap = gap / changer speed; lag time gap = gap / follower speed (WMP L5735ff).

| zone, movement | n | v p50 [m/s] | lead gap p10 / p50 [m] | lead time gap p5 / p10 / p25 / p50 [s] | lag gap p10 / p50 [m] | lag time gap p5 / p10 / p25 / p50 [s] | refused by the 0.6 s acceptance |
|---|---|---|---|---|---|---|---|
| OH merge, entering | 3,213 | 12.7 | 5.0 / 20.6 | 0.44 / 0.61 / 1.00 / 1.81 | 6.2 / 22.6 | 0.55 / 0.75 / 1.26 / 2.27 | 47.3 % (expected low) |
| — v<10 | 1,248 | 6.2 | 3.7 / 10.4 | 0.54 / 0.71 / 1.07 / 1.88 | 4.7 / 12.5 | 0.60 / 0.92 / 1.47 / 2.69 | 59.6 % |
| — 10–20 | 1,178 | 14.7 | 8.0 / 23.4 | 0.40 / 0.55 / 0.92 / 1.61 | 8.9 / 26.7 | 0.52 / 0.72 / 1.15 / 2.10 | 42.4 % |
| — ≥20 | 787 | 23.8 | 12.6 / 48.0 | 0.41 / 0.56 / 1.07 / 2.00 | 15.2 / 46.7 | 0.52 / 0.69 / 1.10 / 2.06 | 34.8 % |
| HH–BR weave, entering | 1,881 | 11.7 | 5.4 / 19.3 | 0.45 / 0.60 / 0.95 / 1.69 | 7.1 / 21.9 | 0.60 / 0.82 / 1.25 / 2.46 | 48.5 % |
| HH–BR weave, exiting | 1,493 | 14.6 | 8.8 / 41.2 | 0.51 / 0.79 / 1.45 / 3.12 | 9.2 / 28.0 | 0.51 / 0.80 / 1.40 / 2.63 | 22.1 % |
| *Model*, T.H.52 fixture, entering (WP-77, L5801) | 2,220 | 6.5 | 8.4 / 20.6 | p50 3.29 | 7.3 / 11.8 | p50 3.12 | 20.4 % |

### 1.3 Partner speeds at the change

These are robust under thinning (VM AE). Values are medians: front speed minus rear speed.

| site, entering | entrant − new follower [m/s] | new leader − entrant [m/s] | source |
|---|---|---|---|
| **I-24 OH merge** | **+0.68** (IQR −0.80…+2.27; n 2,534) | **−0.56** (IQR −2.49…+0.76; n 2,781) | `lane_change_relaxation_i24.json`, `summary_by_zone_kind`, merge/entering, `rel_speed_ms` |
| I-24 HH–BR weave | +1.18 | −0.83 | same artifact; VM AE table (4), L8764 |
| US-101 weave | +1.01 | −0.40 | `lane_change_relaxation_us101.json` |
| *Model*, T.H.52 fixture | +0.72 | **+1.37** (wrong sign) | WP-91, L8575 |

Real entrants cross slightly *faster* than the target lane. The model's entrant crosses slower than its new leader, into 1.3 times the normal gap (L8764–8767).

### 1.4 The gap at and after the crossing (relaxation)

Measures (WP-88 L7980ff; VM AC L8128ff):
- `ratio_pop` = gap over the population's median car-following time gap at the rear vehicle's speed.
- `ratio_eq` = gap over s0 + vT at the fleet means.

| side (entering) | dataset | at 0 s | 2 s | 5 s | 10 s | fit |
|---|---|---|---|---|---|---|
| new follower, `ratio_pop` / `ratio_eq` | US-101 | **0.76 / 0.81** | 0.88 | 0.95 | 1.03 | not supported (145/200 refits); τ 5.3 s [1.9, 16.9] |
| entrant behind new leader | US-101 | **0.57 / 0.62** | 0.61 | 0.72 | 0.88 | **supported: τ_r 7.5 s [5.2, 15.9], r0 0.54 [0.48, 0.61] → 0.92** |
| new follower | I-24 OH merge | 0.90 / 1.17 | 0.92 | 0.92 | 0.89 | `ratio_pop` not supported; `ratio_own_eq` 0.63 → 0.76, **τ 6.0 s [1.6, 19.5] supported** |
| entrant behind new leader | I-24 OH merge | 0.77 / **0.96** (an upper bound) | 0.66 | 0.66 | 0.74 | not supported |
| new follower / leader | I-24 HH–BR weave | 0.87 / 1.14; 0.72 / **0.92** (an upper bound) | | | | none supported |
| exiter's partners | I-24 weave exiting | 0.95 / 1.02 (no short start) | | | | → relaxation for the **entering movement only** (WP-90, L8173ff) |
| new follower / leader | *Model*, T.H.52 fixture | **1.05 / 1.31** | 1.21 / 1.40 | 1.33 / 1.27 | 1.23 / 1.10 | the gap *opens* |

- **Coverage-robust short starts:** US-101 on both sides, and I-24's leader side (L8527).
- I-24's follower `ratio_eq` of 1.14 plausibly transfers to 0.52–0.77 (VM AE §3, L8756).

### 1.5 Speeds along the acceleration lane and where entrants merge

Source: I-24 OH, `artifacts/i24_lane_profile.json` `.observed.rows`, 06:30–08:30, 250 m bins. Lanes are numbered 1 (left) to 4; lane 5 is the auxiliary lane.

| data x [m] | lane-5 speed [km/h] | lane-4 speed | lane-5 tracked vehicles at the bin's count line |
|---|---|---|---|
| 750 | 46.4 | 27.8 | 1,297 |
| 1,000 | 49.0 | 23.7 | 903 |
| 1,250 | 45.5 | 22.6 | 824 |
| 1,500 | 33.3 | 19.3 | 669 |
| 1,750 | 23.1 | 22.0 | 179 |
| 2,000 | — | 29.0 | 27 |

- The acceleration lane runs over data x 751–1,899 m (1.15 km on the corrected map; I24V §0.5(d)).
- **Real entrants run the lane 20 km/h faster than the target lane, then slow in its last 400 m.** Lane 4 gives up a third of its speed inside the lane, not upstream (I24V §0.5(c)).
- **"Entry 36 km/h observed"** is the corridor's *first segment* mean (data x 0–549 m, upstream of the merge; I24V §0.8 table "observed 36 32 30 …"). It is not an acceleration-lane speed.
- **Merge position: no distribution has been extracted.**
  - The per-change parquet is gitignored; the 200-row sample has 3 OH changes.
  - Proxy, using the coverage-dependent counts above: about 30 % leave in the first 250 m, and about half are still in the lane at 1.5–1.75 km.
  - A cheap cloud addition would fix this: x-quantiles of OH entering changes from `i24_wb_lane_change_gaps.parquet`.
- Model reference (T.H.52 fixture): entrants cross at p10 / p50 / p90 = 5.3 / 36.7 / 194 m from the section start; exiters at 3.1 / 53.8 / 268 m (L5850).

### 1.6 Related measured facts that bear on the design

- **Merge-zone car-following** (I24V §0.12; `artifacts/idm_i24_merge.json`): T = 1.580 s, against 1.511 corridor-wide and 1.322 in the capacity-scaled arms.
  - Drivers at the merge do *not* follow closer.
  - The capacity the recording gets is in the lane-change process: 93 % of straight-road capacity against the model's 83 %.
- **I-94 drivers run about 20 % above the posted limit** (PHASE1_REHEARSAL §2): 105.2 against 87.9 km/h free-flow speed; `speed_factor` 1.245 (WP-109).
  - Capacity per lane downstream of the active bottleneck: observed 1,482 against the model's 1,663.
- **The real T.H.52 section carried its 05:30–05:50 demand in free flow**: S790 at 25.8–26.6 m/s, above the factor-1 fleet's 24.59 m/s cap (test docstring, `tests/test_microsim/test_microsim_merge_managed_meter.py:1911ff`).

---

## 2. The current mechanisms

### 2.1 The values of `RampSpec.merge`

`cfg:885`. Validator `_check_kind` at `cfg:962–1012`; off-ramps must stay `lane_change`.

| value | what it does | code | outcome on record |
|---|---|---|---|
| `lane_change` (default) | SUMO LC2013 at the lane end | — | I-24 merge locks into a right-lane crawl and under-discharges (I24V §0.5) |
| `acceleration_lane` | netconvert patch `acceleration="1"` on lane 0 | `runner:229–391` `_apply_merge_models`; `networks.py:520` `merge_patch_files`; `:649` `accel_lane_end` | byte-identical to `lane_change` on I-24 (I24V §0.5(j)); used by no committed scenario |
| `zipper` | lane 0 connected into the next edge, zipper node; `merge_visibility_m` | same | removes the crawl, admits 60–64 % of the ramp; `jm*` / visibility inert (I24V §0.5(j–k), §0.7); zip family negative (§0.6) |
| `scripted` | the runner drives lane 0 of the attach edge | `_scripted_merge_step` `runner:892–1002`; `_scripted_force_gap_ok` `:834–889`; setup `:7454–7489`; dispatch `:7994–7995`; meta `:8314–8330` | I-24: admits 1,544–1,638 of 2,241 against the reference's 2,066 (I24V §0.8); on I-94 McKnight / Hudson its forced changes caused all 15 collisions until `force_guard` (WP-93, L8978) |
| `weave` | the runner drives both movements of a ramp weave | `_check_weave_pairs` `runner:394–446`; about 45 helpers `:1005–4857`; `_weave_short_section_rule` `:4801`; `_weave_step` `:4858–5740`; `_weave_meta` `:5741`; dispatch `:7997–8002`; geometry `networks.py:799` `weave_sections` | no lock in the corridor reference, but T.H.52 is capacity-short (§3) |

Related machinery:
- `OSMNetwork.lane_end_giveup_m` (`cfg:1065`): `_lane_end_diverges` / `_lane_end_step` at `runner:5902–6084`, exempting runner-commanded vehicles (`:6085–6106`).
- FleetSpec LC fields (`cfg:1233–1321`):
  - Both corridors run `lc_strategic` 5, `lc_strategic_ramp` 1 and `lc_keep_right` 0.
  - `lc_impatience` is not read by LC2013 (L6770).
- `speed_factor` / `speed_dev` (`cfg:1323–1366`).
  - The scripted and weave models assume factor 1. `_weave_veh` caches `maxSpeed` (`runner:1467–1485`), and `_weave_lane_vmax` the lane limit (`:1488`).
  - The run only writes a note about it (`_speed_factor_merge_note`, `runner:8533–8559`).

### 2.2 `SCRIPTED_MERGE_DEFAULTS`

`cfg:174–189`. None of these values is fitted.

| key | default | what the record measured | load-bearing in the I-94 reference? |
|---|---|---|---|
| `accept_gap_s` | 0.6 s (plus s0, both sides) | 0.3 s on I-24 moved admittance by ≤ 94 vehicles (I24V §0.8) | the value in use |
| `force_after_s` / `force_within_m` | 4 s / 80 m | 1 s / 150 m: no gain (§0.8) | last resort |
| `change_duration_s` | 2 s | — | mechanical |
| `lookahead_m` | 120 m | speed-match reach | yes |
| `courtesy` | 0 m/s | 2 and 4 m/s: no gain (§0.8) | off |
| `force_guard` | 1.0, on since 2026-10-04 (WP-98) | collisions 15 → 0 over 20 seeds (VM AG, L9185); without it, mode 256 lands 0.1–4.5 m ahead of a follower 9.8–16.3 m/s faster (L9052) | **yes** |

### 2.3 `WEAVE_DEFAULTS`

`cfg:191–277`, docstring to `:802`: 31 keys, 7 of them inherited from the scripted set. "Ref" is the I-94 xlsfg reference configuration.

| key | default | state | derivation and measurement | Ref |
|---|---|---|---|---|
| `accept_gap_s`, `exit_accept_gap_s` | 0.6 / 0.6 s | on | not fitted. VM Z's proposal 0.089 / 1.78 s failed the grid and the capacity pin; the exit value is harmful (WP-79, L6388) | on |
| `accept_lag_gap_s`, `exit_accept_lag_gap_s` | None (= the leader side) | unset | WP-80: the four VM Z values tripled give-ups (44 → 134) and broke the pin at all seeds; the entering pair alone fails the pin at seed 4 (L6692) | — |
| `force_within_m` / `force_after_s` | 80 m / 4 s | on | removing forcing → 16–22 give-ups, lane 1 at 0.9–1.3 m/s (L3017, L4139); every short-section scaling rejected (`runner:4801`, L330) | **load-bearing** |
| `change_duration_s`, `lookahead_m` | 2 s, 120 m | on | `lookahead_m` sets the ramp anticipation, which is load-bearing (fourth derivation, L92 / L129; removal −40…−86 entrants, L3012) | **yes** |
| `courtesy` | 0 | inert in the weave | kept for hash stability | — |
| `force_guard` | 0 (pinned, never read) | — | the weave's forced changes always use `_weave_force_gap_ok` (`runner:1134`) | — |
| `vacate_ahead_m` | 500 m | on | third derivation. HCM 150 m and MUTCD 800 m bracket it, so it is an engineering choice, not fitted; without it the section end locks (L313, L377) | **yes** |
| `vacate_max_veh_h` | 0 = auto (2,050 − flow) | on | re-derived bound | yes |
| `vacate_no_follower_braking` | 0 | off | L377–381: the section end locks, and one collision | — |
| `pair_release_s` | 2 s | on | fifth derivation (two reaction times); without it seeds 3–4 lock (L135, L3017) | **yes** |
| `exit_giveup_m` | 5 m | on | exit-side derivation (one vehicle length); clears the gore-end standstill (L240–261) | **yes** |
| `exit_giveup_patience_s` | 0 | off | WP-52: 44 → 48 give-ups (L821) | — |
| `exit_abreast_patience_s` | 0 | off | WP-53: 44 → 46 (L1012) | — |
| `exiter_yields` | 0 | off | WP-54: 44 → 39 on fixtures, but **locked VM M seed 6904272788004776631 (0.356)** (L1437) | — |
| `entrant_yields` | 0 | off | feasible in 2 of 24 pairs; locks the Ruth St window (L1204) | — |
| `exiter_yields_halting` | 0 | off | WP-55: 39 → 43 (L1421) | — |
| `exiter_yield_lead_s` | 0 | off | WP-56: "every row it moves is a re-roll" (L1691) | — |
| `entry_speed_bound` | 0 | off | WP-57: 44 → 54; falsified the momentum hypothesis (L1906) | — |
| `hold_release_s` | 0 | off | WP-58: 44 → 74, pin broken in every form (L2198) | — |
| `anticipation_gate` | 0 | off | WP-60: entrances 5,944 → 5,415 (L2397) | — |
| `exit_prepare` | 0 | **off by default, ON in Ref** | WP-62 fixture-negative (L2666). With `lane_end_giveup_m` it removed the corridor's late locks (VM U: departed 0.884, lowest 0.867, RMSPE 0.691, GEH 16 %; L6729) | **yes (Ref)** |
| `swap_pairs` | 0 | off | WP-64 (L2859) | — |
| `spread_crossings` | 0 | off | WP-67: unfinished 45 → 208 (L3423) | — |
| `ramp_outlet` | 0 | off | WP-70: entrance +, exit end no faster, pin seed 5 (L3773) | — |
| `exit_priority_onset` | 0 | off | WP-73: Ruth St seed 5 locks (L4768) | — |
| `anticipation_spares_exiters` | 0 | off | WP-75: locks seed 5 (L5326) | — |
| `opposing_entry_guard` | 0 | off | WP-92: removes all 33 runner-rear conflicts and WP-90's collision and 9 m/s² stops, but breaks the pin at seed 5. On the corridor (VM AF) it locked seed 3944094060050347669 at 0.347; collisions there were not the weave's (L8939, L8969) | — |

Load-bearing **always-on** weave mechanisms with no key:
- the gap choice (nearest gap whose follower can open within b_F);
- cooperation as a virtual-leader IDM target via one-step `slowDown(v, 0)`, clipped at −b_F (`_weave_command` `runner:2442–2493`);
- the easing of the rear of an abreast pair;
- the ramp anticipation;
- the exit priority when due;
- the speed-aware brake-gap acceptance and guard (L537–676).

Removing every hold or every target locks the section (L3017, L4141).

### 2.4 Which scenarios use which merge

| scenario family | merges |
|---|---|
| `scenarios/i24_replica{,_corrected,_speedcal,_speedcal_heavy,_speedcal_ramps}.yaml` | `lane_change` (key omitted). Fitted arm v3 `4a2329533017` / v2 `43def6306dd6` |
| `i24_replica_flow*` | `lane_change`, explicit; base for new families. Flow speedcal v2 `8075db417a5a` |
| `i24_replica_zip*` (5 files) | OH `zipper`, HH `lane_change` (published negative family) |
| `mndot_i94_wb_stpaul.yaml` | 9 × `lane_change` |
| `mndot_i94_wb_stpaul_weave{,_slice,_head60}.yaml` | 5 `lane_change`; 2 `scripted`; 2 `weave`. All params `{}` |
| `..._weave_head60_scripted.yaml` | as above, plus 40648744 `scripted` |

- **Scripted ramps:** Hudson Rd 18207436 (attach `43917735#1`) and McKnight Rd 178547099 (attach `638519829`).
- **Weave ramps:**
  - Ruth St 745524613 on `999007700`, 136 m, paired with C-D split 18208090.
  - T.H.52 769818012 on `51388891`, 305 m, paired with exit 18207598.
- **`lane_change` ramps:** the other five, including **40648744, whose acceleration lane contains S790** (MN L1383).
- **The xlsfg reference is not committed.** It is the weave scenario plus:
  - `weave_params {exit_prepare: 1}`;
  - `lane_end_giveup_m: 7.5`;
  - `force_guard`, now the default.

  It is generated by `sed` / `awk` in `scripts/gcp/pipeline_i24.sh:555–568` and `:899–907`; v3 hash `b550b46fe751`. No committed scenario sets non-empty `merge_params` / `weave_params`, `merge_visibility_m` or `lane_end_giveup_m`.
- **VM AG** (L9185): moving both scripted ramps to `lane_change` costs departed −0.013, RMSPE +0.004, GEH −0.057. Lane-change did better on every fixture measure (L9090). The scripted merges are only marginally load-bearing.

---

## 3. Failure mechanisms on record

1. **The I-24 gore crawl and under-discharge** (I24V §0.5(c, f, g), §0.11, §0.12).
   - The replica's right lane crawls at 6–9 km/h with 35–40 % of vehicle-time from 1.5 km upstream of the gore, and ramp vehicles merge *at* the gore at 9–13 km/h.
   - The recording's right lane is the *fastest* at the entry and slows only inside the lane.
   - In flow terms the crawling lane carries 14 % of the mainline flow against 25 %: under-discharge, not overload.
   - Peak sections (20-seed fitted battery, data x 2,200 / 3,200 m): **5,838 / 5,798 veh/h against 6,626 / 6,639 observed**.
   - Causes excluded, one artifact each: every LC2013 / junction parameter, the zipper, sublane, entry lanes, the OH demand level (admittance saturates at about 2,240 whatever is planned), heavy-vehicle placement, and the car-following population (merge-zone fit discharges 9 % *less*).
   - Mechanism named in I24V §0.5(g): "right-lane vehicles keep braking for merging traffic and merging traffic slows to match the lane it merges into". That is LC2013's cooperative negotiation.
   - **`lc_assertive` 1.5–3 admits all ramp demand and +200 veh/h**, but the crawl stays and downstream runs 41–44 against 31–38 km/h (§0.5(f)).
2. **The scripted merge's negative result** (I24V §0.8; LESSONS rows 17–18).
   - Entry speed was right (36–41 km/h), but OH admitted 20–25 % less.
   - "A ramp vehicle that waits for a gap it judges acceptable, with the mainline no longer cooperating through SUMO's signalled change, gets fewer gaps in dense traffic."
   - Its first draft used `setSpeed` and rear-ended crawling leaders. The influencer applies the max-decel clamp after the safe-speed clamp; fixed with `setMaxSpeed`.
3. **The T.H.52 capacity shortfall.**
   - S790 06:30: **3,343 (3,248–3,426) against 4,911 observed**, GEH 24.4 (`artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_p1.json`, `geh.link_hours.rows`).
   - The weave saturates at about **4,000 veh/h over four lanes**, about 1,000 per lane, 60 % of the fleet's straight-road 1,670. The real road fed 5,137–6,275 veh/h into it at 25.9 m/s (VM O, MN L1443–1472). It congests at its exit end first.
   - WP-65 (L3003–3017): before the queue the entry carries what arrives. Afterwards the auxiliary lane plus lane 1 hold about one lane's worth, because **every crossing ties a car-following gap in both lanes**. No command family sets the rate.
   - WP-72 (L4217): 96–99 % of second-half crossings are below 20 m/s, through SUMO's lane-end braking. "What an exiter lacks is being at an acceptable gap while still at speed."
   - WP-68: one lane holds about 1,700 veh/h above 20 m/s, so the shortfall is the weave's.
   - WP-76 (L5504–5550): with no crossing at all, criterion (ii) still fails at 8 of 10 seeds because of no-right-passing plus `lcKeepRight` 0. The owner said no to right-passing (2026-09-27).
4. **The abreast pair** (L50–60, L129–161).
   - Two changers each take the other as their gap's leader and brake to a standstill.
   - A vehicle beside the changer is not a followable leader (s ≤ 0).
   - Resolved by easing the rear vehicle at −b plus the pair release.
5. **The crossing pair at the lane ends** (WP-53/54, L838–1037).
   - An exiter halted at the end of lane 1 beside an entrant halted at the end of the auxiliary lane, in four geometries; one halted entrant heads chains of 3–4 give-ups.
   - "It does not form by momentum" (WP-57, L1910).
6. **Exit give-ups** (WP-52/53, L822–845): of 44, 24 are crossing pairs, 10 sliding, 9 brake-distance and 1 crawling. 12 of 39 have no feasible stop at the exiter's own b (L1400).
7. **Locks.**
   - VM M (`exiter_yields`): 0.356.
   - VM AF (`opposing_entry_guard`): 0.347.
   - VM V: 0.22. VM Q: 0.667.
   - Locks of the T.H.61 two-auxiliary-lane weave, which the one-lane weave misfits (WP-66, L3044–3199).
   - Every fixture rule that passed the 29-run grid could still lock a corridor seed (L4465, L2200).
8. **The ramp queue and the target loops** (WP-84, L7462–7475; WP-86/87, L7716–7951).
   - The ramp queue is the entry's breakdown reaching back; every head-step traces to a weave target.
   - 88 % of lane 1's slow steps trace to holds towards lane-0 partners, which are themselves held. "The loop is an equilibrium": the holds sit at the follower's own s0 + vT.
   - **The runner's targets are ceilings and cannot push a vehicle closer or faster than its own model** (`runner:2455–2468`).
9. **The entrant crosses too slowly.**
   - The model's entrants reach the section from the ramp queue at about 4.6–5 m/s and cross at about 6 m/s, against 11.7 m/s real. They cross 1.37 m/s slower than their leader (WP-79/80, L6388 / L6692; WP-91).
   - With the calibrated acceptance they are refused mostly because **nothing is beside them** (no room in 85 % of evaluated steps, WP-79 L6388).
10. **The follower over-braking after a cut-in.**
    - A 0.6 s accepted gap makes an IDM follower brake at about 4 m/s² (L46–52).
    - Measured lag gaps imply 4.8–12.8 m/s² for the fleet's follower (§1.1).
    - Forced changes under mode 256 land in front of followers 10–16 m/s faster, which hit them at 9 m/s² (WP-93, L9052).
    - Opposing entries into one lane in one step: SUMO executes front-first, and mode 256 checks overlap only (WP-92, L8801–8819). This caused WP-80's and WP-90's collision and every 9 m/s² stop (WP-90 safety table and traces, L8342–8375).
11. **SUMO changes lanes before the runner sees the vehicle.**
    - LC2013 makes a sixth to two fifths of the weave crossings in the arrival step, the fast ones. Owning them requires the mode to be set within two steps' travel, and that refused 19–48 % of them under the old acceptance (WP-67, L3216–3467).
    - `lcAssertive` divides both secure gaps by one factor, so no value reproduces the asymmetric real gaps. It would need 3.38 on the lead side and 1.25 on the lag side (WP-82, L6782–6799).
12. **Phase-1 collisions under varied headway** (PHASE1_REHEARSAL §2).
    - Lanes `999007700_0` and `51388891_1`, about 20 m in, are **the entry ends of the Ruth St and T.H.52 weaving sections**. They are not scripted merges or acceleration-lane ends.
    - Samples s00 (seed 5128615122007998373, headway ×0.9037) and s02 (seed 303958152840581829, ×1.4288) in `artifacts/uncertainty_mndot_i94_wb_stpaul_p1{,b}_rehearsal.json`.
    - The mechanism is not traced: the artifacts hold no lane, time or vehicle.

---

## 4. How each acceptance test is measured today

FRISCO_PROTOCOL §9. Each item below is a measurement path today.

**(1) I-24 problem merge, about 6,600 veh/h.**
- **Observed:** `artifacts/i24_validation_observed.json` `hourly_flows_veh_h_recommended`, in five-minute windows, 06:30–08:30.
  - It is tracked crossings ÷ recommended coverage (`scripts/i24_validate.py:176–250`; `artifacts/i24_coverage.json`).
  - Sections: `SECTIONS_M = (200, 1000, 2200, 3200, 4800, 5400)` (`i24_validate.py:84`).
  - **The peak sections are data x 2,200 and 3,200 m** (6,626 / 6,639; I24V L283). They lie downstream of the acceleration lane, which ends at about 1.9 km.
  - GEH < 5 needs **≥ 6,225 / 6,238** (docs/MERGE_ROUND6_PLAN.md L125–129, 197–206).
  - That plan's closure rule also requires:
    - OH admits ≥ 2,130 of 2,241;
    - the first two segments within 3 km/h of 36.3 / 32.5;
    - no segment at 1.6–3.3 km more than 3 km/h too fast;
    - 15-min RMSPE ≤ 18 %.
- **Today:** 20-seed fitted battery `artifacts/i24_validation_speedcal.json`, **5,838 / 5,798**; throughput 5,839 [5,808, 5,870].
  - I24V §0.11/§0.12 quote sections 1,000 / 2,200 m (5,885 / 5,836) by mistake. At the threshold's sections that probe reads 5,836 / 5,810 (`artifacts/i24_merge_experiment_ohlevel.json`).
  - No script computes "peak sections". They are indices 2–3 of `hourly_flow_mean_by_section`.
- **Probe:**
  - Command: `uv run --no-sync python scripts/i24_merge_experiment.py --base scenarios/i24_replica_flow_speedcal.yaml --variants … --procs N --out artifacts/i24_merge_experiment_<tag>.json`.
  - Seed 6914975401685141156 = `spawn_seeds(42, 20)[0]`.
  - Cost: 308–699 s per variant; 5 variants in 24 min on an n2-standard-32, about $0.60 (I24V L747).
- **Battery:**
  - Command: `uv run --no-sync python scripts/i24_validate.py --family flow --arms speedcal --replicates 20 --procs $PROCS …`.
  - Needs `--data-set i24` on the VM.
  - Cost: about 12 min at 30 processes. A full FHWA family sequence (demand scale, then ramps, then batteries) is 1 h 45 min, about $3 (I24V §0.10).
- **Gap:** neither script records collisions. Read `meta.json` `n_collisions`, or run `scripts/collision_census.py`.

**(2) T.H.52, S790 about 4,900 veh/h.**
- **Observed:** S790 06:30–07:30 = 4,911 veh/h, a nine-day mean; day-to-day sd 157–918 veh/h (MN L1389–1392). GEH < 5 needs ≥ 4,567.
  - S790 is at x = 10,128 m, inside the 40648744 acceleration lane, about 300 m before the T.H.52 gore.
- **Today:** 3,343 at GEH 24.4 (p1 artifact above; hours aligned to 05:30 by `validation.observed.ObservedCorridor.hourly_link_flows` via `scripts/corridor_battery.py`).
- **Command** (`pipeline_i24.sh:904–907`): `corridor_battery.py --scenario scenarios/mndot_i94_wb_stpaul_weave_xlsfg.yaml --observations data/mndot/mndot_i94_wb_stpaul/observations.json --replicates 20 --procs $PROCS --criteria-profile fhwa_tat3_2004 …`.
  - The I-94 data is in git, so `--data-set none`.
- **Cost:** 39–48 min on one n2-standard-32 (MN L1622, L1646), about $1–1.25.
- **Cheap probe:** the 35-min slice (`…_weave_slice`), 4 seeds.

**(3) The T.H.52 section test.**
- `tests/test_microsim/test_microsim_merge_managed_meter.py::TestWeaveRun::test_th52_corridor_section_carries_free_flow_demand` (`:1895–1964`).
- It is a strict xfail and runs **seed 3 only** (`:1953`); the reason text quotes seeds 3 / 4 / 5.
- Fixture `tests/fixtures/weave_th52_corridor.osm`; demand `TH52_OBSERVED_0530` (`:1089–1119`); config `_th52_corridor_config` (`:1124–1171`): 1,200 s at 0.5 s, the corridor fleet from the weave scenario.
- Criteria (`:1180–1258`):
  - (i) ≥ 95 % departed on the mainline and on the entrance;
  - (ii-a) exit-end flow GEH < 5 against S790 + rnd_91040 = 4,877;
  - (ii-b) station speed > 20 m/s in all four 5-min windows;
  - (iii) 0 collisions;
  - (iv) ≤ 2 % of exits given up.
- Today: 4,100 / 3,813 / 3,697 veh/h, GEH 11.6 / 16.1 / 18.0, lowest windows 17.8 / 17.1 / 12.0 m/s.
- It runs in CI (integration, not slow); 3–7 s per seed on macOS.
- **The protocol asks for ≥ 20 seeds:** a parametrised 20-seed form is about 2 min locally.

**(4) Zero collisions.**
- Corridor batteries write `collisions`, `zero_collisions` and per-seed `n_collisions` (p1 xlsfg: 0 in 20 runs).
- The cheapest probe that exposes the phase-1 collisions is the **two colliding (sample, seed) pairs**:
  - build each sample's scenario with `validation.uncertainty.apply` (`uncertainty.py:1292`);
  - call `run_micro` on the seed with trajectories kept;
  - about 35–40 min per 4-h run on one core, so a small VM is under $0.5.
- The collision minute is unknown, so the 35-min slice cannot be relied on.
- Then re-run stage `p1_uncertainty` (4 samples × 2 seeds; `scripts/uncertainty_runs.py`, `pipeline_i24.sh:969–977`).

**(5) No lock.**
- Not computed by any code. The rule: min / median of `per_seed[i].insertion.departed_fraction` ≥ 0.8.
- Today 0.8598 / 0.8790 = 0.978 (p1). Past locks: 0.356, 0.347, 0.22.

**(6) Switches deleted.** See §5.10.

---

## 5. Design proposal: one mandatory-change model (working name `merge: "measured"`)

### 5.1 Why the three principles must ship together

Each principle has failed alone, and the record shows why:
- **(i) alone** (WP-79/80). Shorter gaps are accepted, but the follower then brakes back to its full s0 + vT (WP-88, gap 1.05 → 1.33 of normal over 5 s). Each crossing still "ties a car-following gap in both lanes" (WP-65), so capacity does not move.
- **(iii) alone** (WP-90). There is no short gap to relax from. The 0.6 s acceptance plus the hold at s0 + vT put the follower at 1.05 of normal before any allowance.
- **(ii) alone** (I24V §0.8). Speed is right, but acceptance stays conservative (0.6 s + s0 on both sides) and there is no cooperation, so admittance falls.

The measured data couple them:
- The real lag gaps (0.5–0.64 s at OH) are *below* the fleet's absorption floor (§1.1). A follower can accept them without braking at 5–13 m/s² only if its effective T is lowered at the change: (i) requires (iii).
- The IDM interaction term s* = s0 + vT + vΔv/(2√(ab)) is smallest when the entrant is at or above the follower's speed (Δv = v_F − v_C ≈ −0.7 m/s real): (i) and (iii) require (ii).

### 5.2 Scope and zones

- The model drives **mandatory changers**: vehicles whose lane does not lead to their route's next edge inside a zone. The set is computed from the compiled connections (`lane.getOutgoing()` against the route), not from lane indices. This extends `_lane_end_diverges` (`runner:5902`) and makes the n-lane T.H.61 case (WP-66) a geometry, not a new model.
- **Zones:**
  - acceleration lanes, terminated as today (`accel_lane_end`, `runner:229–391`);
  - weaving sections (`weave_sections`), entering and exiting;
  - the ramp's last `lookahead_m` (the approach).
- **Plain diverges stay with LC2013:** `lc_strategic` 5, the measured I-24 diverge fix (I24-data facts), plus the lane-end give-up made **always on** at 7.5 m (`cfg:1065` docstring).
  - The I-24 diverge critical gaps (σ 2–4, 35 % inconsistent) do not support a runner-driven diverge.

### 5.3 Driver state (seeded, drawn before SUMO starts)

- Per vehicle: one lead and one lag critical time gap per movement it can make.
  - Draw from the zone kind's joint log-normal (§5.11).
  - Use a new child stream (`microsim.vehicles._child_stream`, as `speed_factor_stream` does, `vehicles.py:369–409`), so every existing draw and golden is unchanged.
  - Record in the `FleetPlan` and in `vehicles.parquet`.
- Consistent drivers: one draw per driver, matching the estimator (WP-78).
- Truncate to the distribution's [p2.5, p97.5] so that the 0.07 s tail does not leave all the safety to the guard.

### 5.4 Principle (ii): speed (ceilings only)

- **Rule.** v_des,i = min(v0_i^target, max(v_creep, v_gap + δ)).
  - v_gap is the speed of the chosen gap's leader. With no gap chosen, it is the mean speed of target-lane vehicles within ±50 m; with an empty target lane, v0_i.
  - δ = +0.6 m/s at merges and +1.0 m/s in weaves (§1.3).
  - **v0_i^target = `vehicle.getSpeedFactor(i)` × `lane.getMaxSpeed(target)`, capped by `vehicle.getMaxSpeed(i)`.** This honours `speed_factor` and `speed_dev` and removes the factor-1 assumption (`runner:1467–1496`, `:8533`).
- **Kinematic approach.** The ceiling is not applied until the entrant needs it. The entrant keeps its own speed until it is within its braking distance at its own b of the chosen gap's position: v_ceiling = √((v_gap + δ)² + 2·b_i·d_gap).
  - This reproduces the observed profile: 45–49 km/h down the lane, slowing in its last 400 m (§1.5).
  - It replaces the scripted merge's lane-leader matching (`runner:952–960`).
- **Mechanics.**
  - Apply through `setMaxSpeed`, never `setSpeed` (LESSONS row 17); restore on leaving.
  - Speed targets are ceilings (`runner:2455–2468`): the runner cannot push a car faster than its own model. The entrant's own a_max does the accelerating.
  - The lane-end stop stays SUMO's: EIDM onset is vT + v²/(2√(ab)), 170 m at 20 m/s (WP-73).
- **Remove LC2013's speed adaptation of the changer.**
  - Driven vehicles hold `laneChangeMode` with bits 0–7 cleared, so no model-driven changes.
  - **No TraCI request is left open across steps** (§5.6). Under 512, SUMO "adapts the requester's speed to a gap of its own choosing" (L50), which is the I24V §0.5(g) mechanism.

### 5.5 Principle (i): gap choice, cooperation, acceptance

- **Gap choice: keep the weave's** (`_weave_choose_gap`, `runner:2300`): the nearest gap that F can open within b_F.
  - Choosing by least deceleration makes the change at the gore (L56).
  - F is evaluated at its *relaxed* T (§5.7).
- **Cooperation: keep the one-step `slowDown(F, v, 0)` ceiling** at IDM towards the changer, clipped at −b_F (`_weave_command`), on **the chosen gap's follower only**.
  - The hold reads F's T at the relaxation start (WP-90 "X" form, L8213).
  - It replaces LC2013's cascading cooperation, which is the I-24 crawl.
  - Keep the ramp anticipation (load-bearing).
  - Do **not** bound the hold in time: WP-58/60 read worse in every form.
- **Acceptance, each step.** Gaps are bumper-to-bumper. `getNeighbors` returns gaps net of minGap, the leader side net of the changer's and the follower side net of the follower's (`runner:850`, L5744), so **add the minGap back** before comparing with measured gaps.
  - **lead:** g_L ≥ t_cL,i · v_C, and the brake guard g_L − c_L·Δt > c_L²/(2 b_C), with c_L = (v_C − v_L)⁺.
  - **lag:** g_F ≥ t_cG,i · v_F, the brake guard on F at b_F, and **F's own model at the relaxed T does not brake harder than b_F** for that gap.
    - Read it from SUMO itself, not a re-implementation: `setTau(F, T_relaxed)` → `vehicle.getFollowSpeed(F, v_F, g, v_C, b_C, C)` → restore, all in one step.
    - This avoids the IDM/EIDM closed-form mismatch (WP-68).
  - The **opposing-entry check** is always on (§5.6).
  - The definitions match `calibration.lane_change_gaps` (`lane_change_gaps.py:528`): lead time gap over the changer's speed, lag over the follower's speed. The model can then be self-checked with the same extraction (§5.13).

### 5.6 Execution and safety

- **Holding:** mode 512 with no open request.
- **Accepting step:** mode 256 + `changeLane(target, one step)`, then back to 512 on the next step. This is `force_guard`'s form generalised (`runner:976–1002`); WP-93 found 5 of 13 collisions came from a request refused under 512 and still open when the mode turned to 256 (L9134).
- **Forced zone** (80 m / 4 s): the same execution, and only when the brake guard passes. That is today's `force_guard` semantics, made unconditional.
- **Opposing entries:** before requesting, resolve any pair entering one lane from opposite sides in the same step (WP-92's ordering; deferral by one step). This must be on: it is the only known cause of the weave's collisions and 9 m/s² stops (WP-90, L8368–8375).
  - VM AF's lock under the WP-92 key (0.347) must be traced before it is trusted. That is a risk (§5.12).
- **Invariants, enforced and recorded:**
  - SUMO `speedMode` untouched (CLAUDE.md §3.3).
  - tau ≥ max(step length 0.5 s, 0.5·T_i).
  - No vehicle under `setSpeed` from the merge model.
  - AVs: the merge model owns the lane change; the AV controller's longitudinal command takes over after the change. Ramp AVs are exempt from relaxation.

### 5.7 Principle (iii): post-crossing relaxation

- **At the change** (detected on the step the vehicle's lane index changes), for the entrant C and for its new follower F when F is within car-following range:
  - T_eff,0 = clamp((g − s0)/v, f_floor·T_i, T_i), so the vehicle's equilibrium gap equals the accepted gap: no braking to a full gap at once.
  - Then T_eff(τ) = T_i − (T_i − T_eff,0)·exp(−τ/τ_r).
  - Set by `vehicle.setTau` each step, or every 0.5 s.
- **Restore T_i** at τ = 4τ_r (98 %), or at that vehicle's next lane change, or on leaving the network.
  - The state is per vehicle (a driver state), not per pair, so a cut-in does not reset it. A new crossing in front of F re-grants from the new gap, taking the smaller T.
- **SUMO facts.**
  - `setTau` acts on EIDM (`MSCFModel::setHeadwayTime`; WP-90 L8200).
  - LC2013's secure gaps read tau (L6783), so a relaxed vehicle also accepts SUMO's own cut-ins at shorter gaps. This is consistent with the literature, but count them.
  - The runner's cached constants must be updated with tau (WP-90 hand-on (d), L8477).
- **Movement:** entering only. Real exiters do not start short (§1.4).
- s0 is not scaled. `setMinGap` changes SUMO's standstill margins and overlap test and was never measured (WP-90 limitation).

### 5.8 Exit side and weaving sections

- **Exiters in a weave are driven like entrants**, with:
  - the measured exiting **lag** critical gap (I-24 1.11 s, US-101 0.54 s);
  - **no lead-side time gate**, only the lead brake guard;
  - the exit priority when due, the give-up at 5 m, and no relaxation.
- **Rationale for dropping the lead gate.** Gating on I-24's 2.89 s lead made exiters wait, cross 62–122 m nearer the gore and give up four times as often (WP-79, WP-80 L6692). US-101's complete-coverage lead is 0.42 s.
  - Check, not gate: the model's exiting lead gaps at the change should reproduce I-24's (p50 3.1 s) from car-following behind slower auxiliary-lane traffic.
- **Keep, as fixed constants:**
  - the vacate request at 500 m with the spare-capacity bound;
  - `exit_prepare` on (the reference configuration's late-lock fix).
- Neither is measured. The I-24 lane-share profile upstream of the HH–BR weave (`i24_lane_profile.json`) could calibrate both later.

### 5.9 What LC2013 still does, and what the runner takes over

- **LC2013 keeps:**
  - all discretionary changes (speed gain; keep-right off; no right-passing, owner 2026-09-27);
  - strategic positioning *upstream* of zones (`lc_strategic` 5, `lc_strategic_ramp` 1);
  - plain diverges;
  - cooperation by vehicles that are not the chosen follower of a driven changer.
- **The runner owns** every mandatory crossing inside a zone, including the arrival step. Vehicles are taken one step before they can reach the zone (`_weave_handover_step`, `runner:3541`), because SUMO otherwise makes a sixth to two fifths of crossings there (WP-67).
  - With measured critical gaps, far fewer of those arrival crossings should be refused than WP-67's 19–48 % under the old acceptance. Measure this in the fixture tests.

### 5.10 What replaces what

**Deleted** (protocol §9.6):
- `RampSpec.merge` values `scripted` and `weave`, replaced by `measured`.
- `RampSpec.merge_params`, `SCRIPTED_MERGE_DEFAULTS` (7 keys, `cfg:174–189`), `WeaveSpec.weave_params` and `WEAVE_DEFAULTS` (31 keys).
- `_scripted_merge_step` and the 17 off-by-default weave rules with their helpers, about 3,000 runner lines: `_weave_giveup_patient`, `_weave_giveup_abreast`, `_weave_yield_*`, `_weave_halting_first`, `_weave_entry_*`, `_weave_hold_release`, `_weave_coop_gate`, `_weave_swap_*`, `_weave_spread_*`, `_weave_outlet_length`, `_weave_brake_onset_m`.
- The meta keys they write, from the 44 in `weave_sections`.
- `FleetSpec.lc_impatience` (not read by LC2013, L6770).
- `OSMNetwork.lane_end_giveup_m`: becomes the fixed 7.5 m.
- Goldens `merge_weave`, `merge_scripted` → a new `merge_measured`.
- `WeaveSpec` keeps only `exit_ramp` and `length_m`, which are geometry.

**Owner's call:**
- `acceleration_lane`: unused, inert; delete.
- `zipper`, `merge_visibility_m`, `jm_timegap_minor_s`, `jm_ignore_foe_prob`: used by the five published `i24_replica_zip*` scenarios and their artifacts. Deleting them ends their reproducibility.
- The sublane fields (`SimSpec.lateral_resolution_m` and six FleetSpec fields): they lock, but they are not merge switches.

**Survive as fixed constants of the model** (documented provenance, not keys):
- creep 3 m/s;
- forced zone 80 m / 4 s;
- pair release 2 s;
- exit give-up 5 m;
- lane-end give-up 7.5 m;
- vacate 500 m plus the 2,050 veh/h spare bound;
- `exit_prepare` on;
- request re-issue 2 s;
- lookahead 120 m;
- both guards.

**Defaults and hashes.**
- `measured` becomes the default for every on-ramp with an acceleration lane, and for every paired weave.
- `lane_change` stays as the SUMO-only baseline: the published I-24 record and comparisons need it, and the published scenarios must state it explicitly.
- A default change requires `CONFIG_HASH_VERSION` 4, regenerated goldens and a CHANGELOG entry (memory: hash policy).
- Protocol §7.4: parameter values are fixed globally, never tuned per corridor.

### 5.11 Minimal parameter set and where each comes from

Fix the values in a versioned artifact, for example `artifacts/merge_model_params.json`, written by a script from the committed artifacts *before* any acceptance run.

| parameter | central value | source | sensitivity arm (pre-registered) |
|---|---|---|---|
| entering, merge zones: lead / lag critical gap, log-normal (μ, σ) | 0.42 s, σ 1.42 / 0.59 s, σ 1.46 | `i24_critical_gaps.json` OH joint fit | US-101 0.29 / 0.45 s (complete coverage, n 180) |
| entering, weave zones: lead / lag | 0.46 s, 1.44 / 0.92 s, 1.28 | HH–BR joint fit | US-101 as above |
| exiting, weaves: lag | 1.11 s, σ 1.90 | HH–BR | US-101 0.54 s |
| exiting lead | brake guard only (no time gate) | WP-79/80; US-101 0.42 s | I-24 2.89 s (expected to fail, as WP-80) |
| δ (speed offset) | +0.6 m/s merge, +1.0 m/s weave | `lane_change_relaxation_i24.json` merge `rel_speed_ms`; VM AE | 0 (no offset) |
| relaxation start | the accepted gap, floored at f_floor = 0.5 | US-101 leader r0 0.54 [0.48, 0.61] | fixed f 0.76 / 0.57 (WP-90 X) |
| τ_r | 7.5 s | US-101 leader fit; I-24 OH follower `ratio_own_eq` 6.0 s [1.6, 19.5] | 5.2 / 15.9 s |
| tau floor | max(0.5 s, 0.5·T_i) | SUMO step warning; WP-90 | — |
| constants | §5.10 | dated derivations | — |

Speed classes are not parametrised, because the data do not resolve them (§1.1). The by-class fits are one more sensitivity arm.

### 5.12 Risks

1. **The target loops survive** (WP-86/87). The holds sit at the partner's own equilibrium. Relaxation lowers the equilibrium but does not remove the loop. Watch the loop-share diagnostic.
2. **Too permissive.** Short gaps plus relaxed tau mean more SUMO cut-ins (LC2013 reads tau), more disturbances, and breakdown earlier. WP-90's floor bound cost the entrance 38 per run. I-24 `lc_assertive` traded flow for speed error (§0.5(f)): the queue can move without RMSPE improving, and MERGE_ROUND6's rule needs both.
3. **The lane-end stop on short guessed acceleration lanes** (`--ramps.guess` 250 m on I-94; EIDM onset 170 m at 20 m/s). Entrants may brake before any gap is offered.
4. **Locks** at crossing pairs in short weaves (Ruth St, 136 m), and the VM AF lock under the opposing guard. **The 29-run grid does not catch corridor locks** (VM M lesson), so a 20-seed corridor battery is required before shipping.
5. **Collisions:**
   - tau below the step length;
   - the opposing-entry ordering across edge boundaries (L8852);
   - mode 256 with a stale acceptance;
   - AVs on ramps.
   Any collision fails C5.
6. **Platform and seed sensitivity.** Linux and macOS fixture outcomes differ (L265–278, L7212). A 0.01 m geometry change moves a run (L7163).
7. **Demand refits.** OH's ramp level and the I-94 demand were fitted under the old merges, so the FHWA sequence must be re-run (protocol §7.1). Judging the merge on old demand fits confounds the two.
8. **Cost per step.** Python per driven vehicle on nine I-94 ramps. Plus `getFollowSpeed` trials: profile on the 4-h run.

### 5.13 Test plan

**Local and cheap** (macOS, one process; the memory rule allows fixture runs only):
1. **Pure-function unit tests** in a new `microsim/merge_model.py`, controller style:
   - seeded draws, with a KS test against the log-normal and the truncation;
   - acceptance equal to `lane_change_gaps` definitions on hand-built cases, including the minGap add-back;
   - the speed ceiling honouring `speed_factor`;
   - the relaxation schedule (floors, restore, re-grant);
   - the guard (the existing `_scripted_force_gap_ok` cases);
   - opposing-entry resolution.
2. **Fixture runs**, zero collisions and no lock required on each, with 9 m/s² stops counted:
   - the ramp fixture (`test_microsim_merge_managed_meter.py` spill / ramp OSM);
   - `mcknight_merge.osm` (WP-93's collision case, 5–15 s per run);
   - `weave_th52_corridor.osm` at **seeds 3–22** (test 3 at 20 seeds, about 2 min);
   - `weave_th52.osm` capacity seeds 4–5, with the pin rewritten without weave-specific counters;
   - `weave_ruth.osm`;
   - `weave_th61_lane_end.osm` (about 60 s);
   - the 29-run grid (about 1 min).
3. **Micro self-check** with the committed extractors on fixture runs:
   - `scripts/i24_lane_change_gaps.py --sim-run-dir`, `scripts/i24_critical_gaps.py --sim-run-dir`, `scripts/lane_change_relaxation.py --source trajectories`.
   - Required:
     - (a) the model's own fitted critical gaps recover the inputs (WP-78's self-check);
     - (b) partner speeds have the real signs: entrant faster than both partners;
     - (c) the entrant's new follower is ≤ 0.9 of normal at the change, and the leader side ≤ 0.8, recovering over seconds;
     - (d) arrival-step crossings are not suppressed.
   - Fail any of these and the model does not represent the measurements, whatever capacity it reaches.
4. Determinism, schema round-trip, goldens, and `pytest -m "not slow"` / ruff / mypy (owner rules).

**Cloud and cheap** (each gate decides the next):
- **A. I-24 single seed.** `i24_merge_experiment.py` on `i24_replica_flow_speedcal` with OH set to `measured`, under the central and US-101 parameter sets, with `lane_change` as the reference. One n2-standard-32, about 25 min, about $0.60. Add `n_collisions` to the probe's output first.
  - Read sections 2,200 / 3,200 m against ≥ 6,225 / 6,238, OH admitted against 2,241, entry segments, 15-min RMSPE, collisions.
  - **Kill gate:** the peak sections not above about 6,000 with admittance ≥ the reference's 2,066 and 0 collisions → stop and diagnose before any battery.
- **B. I-94 slice.** The 35-min slice, 4 seeds, with every weave and scripted ramp plus 40648744 on `measured`. Minutes, under $0.5. Read S790, departed, collisions.
- **C. Collisions.** The two phase-1 (sample, seed) pairs as 4-h runs (about 40 min, under $0.5), then `p1_uncertainty` (4 × 2).
- **D. Only after A–C pass:**
  - the I-94 20-seed 4-h battery: 40–48 min, about $1.25; tests (2), (4), (5);
  - the I-24 FHWA re-sequence plus the 20-seed battery: about 1 h 45 min, about $3; test (1).
  - All of A–D comes to about $7.

---

## 6. What in the record argues against this design

1. **Every ingredient has failed on its own.**
   - Calibrated acceptance did not make crossings faster (WP-79/80).
   - The post-crossing allowance moved the follower's gap only two fifths of the way and did not improve the entry; its floor made it worse (WP-90).
   - The scripted speed-matching merge admitted 20–25 % less on I-24 (§0.8).
   - "No weave rule closes it. Fifteen derivations … moved the conflict, locked the section or read worse" (L6727).
   - The coupling argument (§5.1) is a hypothesis, not a result.
2. **The runner can only brake people.** Targets are ceilings (`runner:2455`). If the binding constraint is the entrant arriving from a queue at 5 m/s, no ceiling fixes it. The queue is the breakdown reaching back (WP-84): this model must prevent the breakdown, and cannot undo it.
3. **The measured gaps are not clean thresholds.**
   - σ ≈ 1.4 and 25–35 % inconsistent drivers.
   - I-24 critical gaps are "undetermined" under coverage thinning.
   - US-101 is raw NGSIM and small (180 entering, 72 exiting).
   - The exiting lead side contradicts itself: 2.89 s on I-24 against 0.42 s on US-101.
   - "The proposal is then a central value, not a threshold" (L6066).
4. **Relaxation is only partly supported.**
   - No supported follower-side fit in either dataset.
   - The only supported leader-side τ_r is from 176 US-101 sides.
   - I-24's follower start (0.87–0.90 of normal) is not coverage-robust.
5. **The targets may be partly outside the merge.**
   - **I-24's 6,630** is a *coverage-corrected estimate*. The recommended estimator is "biased LOW [coverage] where the true spacing scale is heterogeneous", which would bias flows high (`artifacts/i24_coverage.json` `.recommendation`).
   - **S790's 4,911** has a day-to-day sd of up to 918 veh/h. It sits in a different entrance's (40648744) acceleration lane, and it needs the T.H.52 weave to pass about 6,200 veh/h against its saturation of about 4,000 (VM O): +55 %, not a tweak.
   - The weave's exit end fails criterion (ii) even with *no crossings* at 8 of 10 seeds, because of no-right-passing (WP-76).
   - The owner has ruled out right-passing.
6. **The model is already over capacity elsewhere.** I-94 capacity per lane is 1,663 against 1,482 observed. A merge that discharges more may move the queue rather than match the corridor, as `lc_assertive` did on I-24.
7. **The speed factor confounds the T.H.52 tests.**
   - The real section ran at 25.8–26.6 m/s, above the factor-1 fleet's 24.59 m/s cap.
   - The locked fixture uses factor 1. Running it with the measured 1.245 would change the test, not the merge.
   - Decide and record this as a dated protocol amendment **before** any run.
8. **SUMO-level limits remain.**
   - The lane-end stop term.
   - Front-first execution, and the fact that model changes cannot be read in advance.
   - `lcAssertive` is symmetric.
   - The opposing-entry guard locked a corridor seed (VM AF).
   - Each was reached before and is still there.
9. **Process risk.**
   - Fixture successes have repeatedly failed to transfer to the corridor (VM M, VM AF).
   - Macs and Linux disagree.
   - Every rule needs a 20-seed four-hour battery, and the test plan's kill gates exist so that this one fails cheaply if it fails.
