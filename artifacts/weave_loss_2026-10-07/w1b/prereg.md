2026-10-07 05:42 CDT (frozen 05:42:59 CDT)
PRE-REGISTRATION of amendment W1b: docs/WEAVE_LOSS_DIAGNOSIS.md section 10.1-10.6, copied verbatim below before any W1b code or run.

## 10. Amendment W1b: a lock release after a dwell (pre-registered 2026-10-07 05:42 CDT)

*Written before any W1b code existed and before any W1b or reference run of
this round. Evidence used: the committed W1 artifacts
(`artifacts/weave_loss_2026-10-07/w1/`) and docs/I94_COLLAPSE_DIAGNOSIS.md.
The results are appended below this registration and do not change it.*

### 10.1 Why W1b

- **The lock.** docs/I94_COLLAPSE_DIAGNOSIS.md found that every I-94 collapse
  read so far is a permanent standstill at a weaving gore. Its front vehicle
  is a through-bound entrant at rest 0.05–0.08 m from the end of the
  auxiliary lane, and nothing releases it. It occurred in 3 of 20 four-hour
  replicates with the calibrated drivers.
- **W1 removes that vehicle, but too often.** W1 reroutes an entrant to the
  exit at the first step it is halted at the lane's end with no change to
  request. On the fixtures that also caught ordinary stands that clear by
  themselves, so it failed F3 and F5 on frequency (§8.5).
- **W1b fires only on long stands.** It keeps W1's action and adds a dwell:
  the entrant must have stood at the lane's end for at least T_dwell. A lock
  stands for hours. The fixture's ordinary stands, all of which cleared by
  themselves, last at most about 50 s. So the rule is meant to change nothing
  in normal operation and to release every lock.

### 10.2 The rule

- **New key.** `entrant_giveup_dwell_s` [s], a second member of
  `WEAVE_OPTIONAL_KEYS`. It has no default and must be ≥ 0. Unset or 0 leaves
  W1 exactly as it is. A positive value needs `entrant_giveup_m` > 0, and the
  config is refused otherwise, so the key can never be silently inert.
- **Dwell clock.** It runs per entrant under the weave's control that still
  owes its change from the auxiliary lane into lane 1.
  - It starts at the first step on which the entrant is halted (below
    `HALTING_SPEED_MS`, 0.1 m/s) within `entrant_giveup_m` of the auxiliary
    lane's end (the exit gore).
  - It resets on any step on which the entrant is at or above the halting
    speed, or farther than `entrant_giveup_m` from the end.
  - It measures uninterrupted standstill at the lane's end. That is the
    quantity T_dwell is chosen from (10.3), read from trajectories, so it is
    not stopped by a step on which a change was requested. A requested change
    that executes ends the stand anyway.
- **Release.** The entrant takes the paired exit on a step on which:
  - the clock has run for at least `entrant_giveup_dwell_s` (t − start ≥
    T_dwell); and
  - W1's own condition holds that step: halted, within `entrant_giveup_m` of
    the end, with neither an accepted nor a guard-passing forced change. An
    entrant that can request its change on that step tries the change, not
    the exit.
- **Action and records.** Exactly W1's:
  - `vehicle.changeTarget` to the off-ramp's last edge, the lane-change mode
    restored, handed back, never taken under control again;
  - counted in `n_missed` and `n_entrant_took_exit`;
  - marked `gave_up` in `vehicles.parquet`.

  No new counter. The dwell is recorded in `meta.json["weave_sections"][i]
  ["params"]`.
- **Off is byte-identical.** With the dwell key unset, nothing of it runs and
  nothing is written.
- **The W1b setting.** `entrant_giveup_m` 5 (W1's value) with
  `entrant_giveup_dwell_s` 60.
- **Not included: an exit-side release** for an exiter halted at the front of
  a through lane 5–11 m short of the gore, beyond `exit_giveup_m`.
  - The diagnosis does find one at lane 1's front in every lock (Table 4).
    But it reads that exiter as waiting for a lane that never moves, and it
    proposes no exit-side rule until the trajectories show that the exiter
    stays stuck after the auxiliary lane moves (§7.3 there).
  - The fixture lock definition below (10.4) includes through-lane fronts.
    So if W1b releases an entrant and the lock persists with an exiter at
    the front, that shows up as a lock in the W1b arm, with the front vehicle
    named.

### 10.3 T_dwell = 60 s, and its basis

- **The fixture's ordinary stands** [artifact, `w1/th52_ref_post.json`]. These
  are the T.H.52 section test with the calibrated drivers, seeds 3–22, and
  W1's reference arm. A stand is `strand.py`'s definition: the front of lane 0
  within 15 m of its end, below 0.5 m/s, a through-bound entrant.
  - The longest single stand per seed, in seconds, sorted: 0, 0, 5.0, 6.0,
    6.0, 7.5, 8.0, 9.0, 10.0, 10.5, 13.5, 15.5, 15.5, 17.0, 18.0, 20.0, 20.5,
    29.0, 33.5, **50.5** (seed 4).
  - Every one of them cleared by itself: no lock, and lowest zone minute
    ≥ 6.6 m/s.
  - `strand.py`'s definition is wider than the rule's (15 m and 0.5 m/s
    against 5 m and 0.1 m/s). So a stand by the rule's definition is no
    longer than these.
- **The locks.** The front entrant stands from the lock's onset to the end of
  the run: about 1.2–1.9 h in the three four-hour replicates (last vehicle
  through the gore at about 08:37–09:16, runs end at 10:30).
- **The choice.** Any T_dwell between about 51 s and an hour separates the two.
  60 s is the smallest whole minute above every ordinary stand on record,
  with about 10 s of margin over 50.5 s.
  - A shorter dwell would release ordinary stands. The 20–30 s range
    suggested when this round was set up would have acted at 2 to 5 of the 20
    reference seeds.
  - A longer dwell lets the lock spread further before it is released. The
    auxiliary lane stops first and the through lanes stop over minutes as
    exiters become their fronts (the diagnosis's §2).
  - 60 s is not fitted to any outcome. Its only input is the reference stands
    above.
- **Its limit.** The basis is 20 seeds × 20 min of one fixture. A rarer
  ordinary stand longer than 60 s would be released. F3b/F5b count such
  releases on the fixtures, and C5b counts them on the corridor.

### 10.4 Evaluation sets and definitions

**Arms.** Each set is run twice at the same seeds:
- reference: no key;
- W1b: `--weave-set entrant_giveup_m=5 --weave-set entrant_giveup_dwell_s=60`.

Trajectories are kept for the readers. macOS.

**Sets:**

| set | what | runs per arm |
|---|---|---|
| S1 | The T.H.52 section test with the calibrated drivers, seeds 3–22: `merge_model_selfcheck.py th52 --model weave --fleet-from scenarios/mndot_i94_wb_stpaul_weave_dc.yaml` (W1's F1–F4 set) | 20 |
| S2 | W1's 37-run grid with the fixtures' own fleets: `merge_model_selfcheck.py grid --model weave` (W1's F5 set) | 37 |
| S3 | The calibrated drivers on every grid fixture with a weave block, through a new `grid --fleet-from` option. Ruth St fixtures at seeds 3–22; the others at the grid's seeds. A run whose config, after its fleet is replaced, hashes the same as another run's in the set is run once (the `_fleet` twins) | ≈ 75 |

**Definitions:**
- **Gore.** The end of the weave section's last edge on the corridor axis
  (1,134.09 m on the T.H.52 fixture, as in `strand.py`).
- **Entrant.** A vehicle from the weave's on-ramp not planned for the paired
  exit.
- **Stand (the rule's definition).** An entrant in section lane 0 with
  v < 0.1 m/s and x ≥ gore − 5 m on consecutive 0.5-s samples. Its length is
  last sample − first sample.
- **Lock (fixture form of I94_COLLAPSE_DIAGNOSIS §7.1).** After the 120-s
  warm-up, some vehicle is, for at least 120 s without a break:
  - the front vehicle of a section lane (any lane);
  - within 15 m of the gore;
  - below 0.1 m/s.

  Why these values:
  - 120 s is twice T_dwell and more than twice the longest ordinary stand.
  - 15 m reaches lane 1's front exiter in all three corridor locks
    (5.0–10.8 m short).

  `run_summary`'s lock flag is reported beside it, as in §8.4.
- **Lock-prone run.** A run of S1–S3 whose reference arm has a lock.
- **Releases.** `n_entrant_took_exit` in the W1b arm.

### 10.5 Acceptance criteria, fixed now

If any criterion fails, the key stays off and the failure is reported. No
criterion is re-thresholded.

| # | criterion | relation to §6.2 |
|---|---|---|
| B0 | The rule waits for the dwell. (a) Every entrant the rule reroutes, in every W1b run, had a stand ending at its `gave_up_s` of ≥ 59.5 s (60 s less one sample). (b) No stand in any W1b run lasts longer than 61 s | new; replaces F2 |
| B1 | Inert until it fires. Every W1b run in which the rule never fires is identical to its reference: sha256 of every Parquet file; `meta.json` without wall time, `config_hash` and the weave `params` | new |
| F1b | S1: exit-end flow not lower. Paired W1b − reference, 95 % upper bound ≥ 0 | replaces F1 |
| F3b | Entrants taking the exit ≤ 1 % of the weave entrance's departures, pooled over seeds. S1 over its 20 seeds. In S2 and S3, pooled per weave section over the set's runs of it: Ruth St, T.H.52 (every `th52_*` fixture), T.H.61, the moderate and golden fixtures | replaces F3 and F5's per-run cap |
| F4 | S1: zero collisions; −9 m/s² vehicle-steps ≤ reference + 2; given-up exits ≤ reference | unchanged |
| F5b | S2 and S3: zero collisions. No W1b run has a lock, or a `run_summary` lock flag at a weave section, unless its reference has one | F5 without its frequency cap (now F3b) |
| L1 | Zero locks in the W1b arm on every lock-prone run. If no reference run locks, L1 is **not testable on the fixtures** and is reported as such | new |

**Reasons for each change:**

- **F1 to F1b.** W1b is built not to act below 60 s, and the reference stands
  on S1 are all shorter. So no flow gain is expected there, and a criterion
  that asks for one would test the wrong thing. The question for S1 is
  whether W1b costs anything.
- **F2 to B0.** W1b deliberately leaves stands under 60 s alone, so halving
  stranded time (F2) is not its aim. B0 checks instead that it acts after the
  dwell, and only then. Stranded time per run is still reported.
- **F3 and F5's per-run cap to F3b, pooled over seeds.**
  - On a Ruth St fixture whose entrance departs 73 vehicles, one release is
    1.37 %. A per-run cap of 1 % therefore forbids any release at all on such
    a section, which is where two of the three corridor locks formed.
  - Pooling over seeds measures the rate the cap was meant to bound. With 20
    seeds of S1, 1 % is about 78 entrants.
  - The pooled cap is no looser in intent. For W1b, each release also
    requires a minute's standstill.
- **F5's "no lock" to F5b plus L1.** W1's reference grid already carried a lock flag
  at a weave section (`ruth_entr_fleet` s5, `run_summary`'s unfinished-share
  rule). Only a lock that W1b creates counts against it (F5b). A lock it
  fails to release counts in L1.

**Predictions, registered, not criteria:**
- **S1.** No reference stand reaches 60 s. So the rule never fires, every W1b
  run equals its reference, and B1, F1b, F3b and F4 hold trivially.
- **S2/S3.** Few or no releases. Ruth St's stands are unmeasured, and any
  releases are expected there.
- **L1.** The fixtures may contain no lock at all. W1 found no lock by the
  diagnosis's definition on S1 and had only `run_summary`'s weaker flag on
  S2.

**What passing means.** Passing every fixture criterion does not adopt W1b.
Its purpose, releasing a lock, can be shown only where locks occur. If the
fixtures have none, that is the corridor (10.6), and adoption is the owner's
decision.

### 10.6 Corridor criteria, fixed now (cloud; run only on the owner's decision)

**The runs.** Two arms in one stage, paired seed by seed on step 3's 20 seeds:
- **Reference.** `scenarios/mndot_i94_wb_stpaul_weave_dc.yaml` (hash
  `db9fbab5fc6e`).
- **W1b.** The same scenario with `entrant_giveup_m: 5.0` and
  `entrant_giveup_dwell_s: 60.0` added to both weaves' `weave_params`
  (`exit_prepare: 1.0` kept).
- **Reproduction check.** The reference arm is re-run so that both arms use
  the same code. It is checked against step 3's committed per-seed departed
  shares.

| # | criterion |
|---|---|
| C1 | S790 06:30–07:30 not lower: paired 95 % upper bound ≥ 0 |
| C2 | Departed share not lower: paired upper bound ≥ 0 |
| C3 | Zero collisions |
| C4b | **Zero locks** at T.H.52 and Ruth St over the 20 W1b replicates. The reference count is reported beside it (3 of 20 if it reproduces step 3). Replaces C4, which passes every lock seen (I94_COLLAPSE_DIAGNOSIS §5). The definition is the diagnosis's Table 4 reading, extended to any lane front. A section is locked at the run's end when no vehicle stands between its gore and the next entrance downstream (or the corridor's end), and a vehicle stands at rest at the front of a section lane within 15 m of the gore. Read from each replicate's `vehicles.parquet` (vehicles not arrived, last sample at the run's end) |
| C5b | Entrants taking the exit ≤ 1 % of that weave entrance's departures, pooled over the 20 seeds, at T.H.52 and at Ruth St (`n_entrant_took_exit` over `ramps[k].n_departed`, from each `meta.json`) |

---- appended 05:43:30 CDT, before any W1b code or run ----
**Clarification of B1 (2026-10-07 05:43 CDT).** Written before any W1b code
or run, after re-reading W1's implementation.
- **What else B1's `meta.json` comparison drops.** Besides wall time,
  `config_hash` and the weave `params`, it drops:
  - `weave_sections[i].n_entrant_took_exit`. W1's code writes this counter
    whenever `entrant_giveup_m` is set, so in a W1b run that never fires it
    reads 0 and the reference has no such key.
  - The run-directory path strings, which contain the config hash.
- **Which weave `params`.** Both copies:
  `meta["config"]…["weave"]["weave_params"]` and
  `weave_sections[i]["params"]`.
- **Nothing else is excluded.**
