# The merge anticipation reach: measuring `lookahead_m` on I-24 (M1)

Written 2026-10-07, **before any run of the measurement**. Nothing in this
document is a result from I-24 data. It defines the observable, describes the
instrument and its synthetic validation, and pre-registers how a measured value
would be adopted (§8). The measurement itself is a cloud stage (§7).

Labels:
- **[record]**: quoted from the named document.
- **[artifact]**: read from the named committed JSON.
- **[synthetic]**: a test on hand-built trajectories in this repository.
- **[code]**: read from the named source file on 2026-10-07 (read only).

## 0. In plain English

- **The constant.** The weave model's `lookahead_m` is 120 m. Nobody measured
  it. It says how far before the start of a weaving section an entering
  vehicle starts lining up with the gap it will merge into.
- **Why it matters.** At 200 m the T.H.52 weave carries 98 veh/h more
  (95 % CI 55–141), at 300 m 177 more (134–219) [record,
  WEAVE_LOSS_DIAGNOSIS §4]. Choosing a value because it helps is the tuning
  the protocol forbids [record, §6.1]. So it has to be measured.
- **What is measured.** For every vehicle that merges from the acceleration
  lane in the I-24 MOTION recording: how far it drove alongside the gap it
  finally merged into, before merging. That is what the model's constant
  sets.
- **How.** Walk back from each merge, 5 samples a second, and check at each
  instant whether the merging vehicle's neighbours in the target lane were
  still its eventual leader and follower. Where the camera tracks run out the
  value is a lower bound (censored). The distribution is estimated with the
  standard method for censored data (Kaplan–Meier), with bootstrap intervals.
- **The rule, fixed now (§8).**
  - If the measurement is precise enough (four checks), the median for
    free-flow merges in the I-24 weaving section, rounded to 10 m, replaces
    the 120 m. It replaces only the ramp anticipation, not the follower search
    the same constant also sets.
  - It is then checked for safety on the T.H.52 fixture and the I-94
    corridor. Throughput is reported both ways but does not decide.
  - If the checks fail, 120 m stays and the result is reported.
- **Cost.** One process for about 3–5 minutes on the usual cloud machine,
  about 15–20 minutes billed with setup (§7).

## 1. What `lookahead_m` does in the model

[code] `packages/microsim/microsim/runner.py` and
`packages/flowstate_core/flowstate_core/config.py`:

| where | what the constant sets |
|---|---|
| `_weave_step`: `approaching` — an entering vehicle on the on-ramp with `x >= x_start - lookahead_m` | **the ramp anticipation.** From that point the entrant chooses a target-lane gap (`_weave_choose_gap`), the gap's follower F is driven towards the entrant as a virtual leader (cooperation, one-step `slowDown`, clipped at −b_F), and the entrant eases towards the gap's leader L when it would have to brake for L. All three start at this one trigger. |
| `_weave_choose_gap(..., lookahead_m, ...)` | **the follower search reach**: a target-lane vehicle more than `lookahead_m` behind the changer's front is not a candidate follower. |
| section steps until the change | the gap choice, cooperation and easing continue every step; the committed gap is kept while F still opens it within b_F. |
| `_measured_constants` (`merge_model.LOOKAHEAD_M`, 120 m) | the measured merge model reuses both, and puts its speed ceiling on approaching entrants too. |
| `_scripted_merge_step`: `v_match = v_lead if g_lead < lookahead_m` | a different mechanism under the same key: a speed-match reach towards the left leader. |

**So the weave's constant governs when gap-seeking starts** — and with it the
chosen follower's yielding and the entrant's easing. It does not govern a speed
match: the weave has no speed ceiling. In the model an entrant is beside the gap
it will take from `lookahead_m` before the gore until its change, which on the
T.H.52 fixture is at a median 15 m past the gore (p10 / p90: 1.3 / 163 m)
[record, WEAVE_LOSS_DIAGNOSIS §3.6].

**What is on record about it.** Removing the ramp anticipation costs the
entrance 40–86 vehicles (WP-60; [record] WEAVE_MODEL_PLAN, the WP-65 tally).
With 300 m, "more of the commanded braking moves onto the ramp … Gaps formed
farther upstream crowd the gore less. The lever is the reach" [record,
WEAVE_LOSS_DIAGNOSIS §5].

## 2. The definition and why

### 2.1 Chosen: the gap held (`gap`, the primary definition)

> The **anticipation reach** of an entering change is the distance the
> entrant travelled, before the change, while its target-lane neighbours were
> the leader and follower it changed in between: from the start of its last
> run beside its eventual gap to the change. The **anticipation time** is the
> same run's duration.

Why this one:
- **It is the model's own condition.** A gap is a candidate in
  `_weave_choose_gap` exactly when the changer is between its follower and its
  leader. Being beside the gap from a point on is what `lookahead_m` produces.
- **It uses the repository's neighbour definitions unchanged.** The lead is the
  nearest target-lane front strictly ahead of the changer's front, the lag the
  nearest at or behind it, within 200 m (`calibration.lane_change_gaps`,
  WP-77). Being beside the eventual gap is WP-78's `accepted` sample of
  `gap_sequences` [code].
- **The same extractor reads model trajectories**, so the model can be checked
  against the measurement with one instrument (§8.6 states the limit:
  simulated runs do not record ramp edges).

### 2.2 Considered and not chosen

**(a) The speed match** of WEAVE_LOSS_DIAGNOSIS §6.3: "the last time before the
change from which the entrant's speed stays within ±1 m/s of its future
leader's". It is measured and reported as `speed_1` (and `speed_2`, ±2 m/s),
never adopted, for two reasons:
- The weave sets no speed match (§1).
- Real free-flow entrants do not cross at their new leader's speed. In the
  Hickory Hollow–Bell Road weave, entering changes at 20 m/s or more close on
  their new leader at p25 / p50 / p75 = 0.05 / 3.51 / 8.82 m/s (n 295)
  [artifact, `i24_lane_change_gaps.json` `summary_by_zone`, `lead_closing_ms`].
  At least half are more than 1 m/s faster than the new leader at the change.
  A ±1 m/s band would therefore read the crossing's speed difference and
  return zero for most events, whatever the anticipation was. This is
  recorded as prediction P1 (§8.8).

**(b) The departure from free acceleration** (the entrant's speed leaving its
unconstrained path towards the target lane's speed). This needs each driver's
counterfactual free trajectory, which trajectories do not contain. Not used.

**(c) The follower's yielding onset.** The model starts it at the same trigger.
The literature measures a pre-insertion transition of the follower (Zheng,
Ahn, Chen & Laval 2013, Transp. Res. C 26:367–379). Identifying it needs the
follower's own normal gap to its leader, and under I-24 coverage that leader is
often untracked. Deferred (§9).

## 3. The operational definition (as implemented)

Module `packages/calibration/calibration/merge_anticipation.py`
(`anticipation_events`); driver `scripts/measure_merge_anticipation.py`.

**Events.** Confirmed, non-suspect entering changes (auxiliary band 5 →
mainline band 4) from `lane_change_gaps` on the same chunks, span, zones and
lanes as stage 12 (`scripts/i24_lane_change_gaps.py`). Two zones have an
entering movement [artifact, `i24_lane_change_gaps.json` `zones`]:
- the Old Hickory acceleration lane, data x 751–1,899 m (`merge`);
- the Hickory Hollow–Bell Road weave, 4,507–5,092 m (`weave`).

**Partners.** L and F are the change's lead and lag within 200 m. Without L
the event is `no_partner` for every definition; without F, for `gap` only.

**The walk.** From the change back every 0.2 s (the table's cadence), up to
120 s. At each instant the entrant, L and F are found by id, or across a
tracker fragment switch by position continuity: a front carried at the mean of
two speeds landing within 2 m (WP-78's rule). Each instant is one of:

| status | `gap` | `speed_b` |
|---|---|---|
| holds | the entrant's target-lane lead is L and its lag is F (a duplicate fragment of L or F within 2 m counts as L or F) | the entrant's speed is within ±b of L's |
| fails | the entrant is behind F or ahead of L; L or F is not yet in the target lane; or another vehicle is between them and is still tracked one step later | the speed is outside the band |
| unknown | a vehicle is missing for at most 1 s (bridged), or the target-lane neighbour is a duplicate fragment of the entrant itself | a vehicle is missing for at most 1 s |
| censors | see the table below | the same, without the intruder |

**Persistence.** Going back, a run ends at the first spell of failures lasting
at least 1 s (five samples, the lane debounce's dwell). Shorter failures and
unknown instants neither end nor extend it.

**The reach** is `x(t_change) − x(t_onset)` along the entrant's own
trajectory, where `t_onset` is the earliest instant of the final run. The
**time** is `t_change − t_onset`. A reach of 0 means the entrant was beside
the gap only at the change.

**Censoring** (the true reach is at least the one recorded):

| reason | when |
|---|---|
| `window_start` | the frame's first time slot (the recording's start) |
| `changer_track_start` | the entrant's track begins: absent by id and by continuity for more than 1 s |
| `changer_lane` | the entrant was on a mainline lane: it was not an entrant before that |
| `upstream_limit` | more than 500 m upstream of its zone's start |
| `partner_track_start` | L's track begins (F's too, for `gap`) |
| `intruder_track_end` | `gap` only: a vehicle between the entrant and L or F whose own track ends one step later; it may still be there, untracked |
| `lookback_cap` | 120 s reached |

The walk may continue upstream of the zone's start while the entrant is off
the mainline (a tracked ramp lane, band 6 or more). On I-24 MOTION ramp-lane
fragments appear at the gores [record, I24_DATA §6], so in practice the zone
start usually censors through `changer_track_start`.

**Onset diagnostics** (`gap`): the onset position past the zone start
(negative upstream of it), the entrant's speed, L's and F's speed differences,
the bumper gaps, and **the follower's distance behind the entrant at onset**.
That last one is the real-driver counterpart of the model's follower search
reach. It is reported, never adopted.

**Parameters** (module constants, recorded in the artifact's `method`):

| parameter | value | why |
|---|---|---|
| step | 0.2 s | the processed table's cadence |
| lookback | 120 s | covers the 1.15 km Old Hickory lane above 10 m/s; 2.4 km at 20 m/s |
| persistence (`min_break_s`) | 1 s | the debounce's dwell (`calibration.lanechange`) |
| bridge (`max_bridge_s`) | 1 s | at 3 m/s² a 1 s prediction errs < 1.5 m, inside the 2 m continuity tolerance |
| continuity / duplicate tolerance | 2 m | WP-78 (`DEFAULT_SAME_VEHICLE_TOL_M`) |
| partner range | 200 m | WP-77 (`DEFAULT_MAX_RANGE_M`) |
| upstream limit | 500 m | a bound for stray ramp tracks |
| speed bands | ±1, ±2 m/s | the diagnosis's sketch, and a sensitivity |

## 4. Statistics

- **Distribution.** Kaplan–Meier product-limit estimate under right censoring
  (Kaplan & Meier 1958, J. Am. Stat. Assoc. 53:457–481). Quantile q is the
  smallest observed reach at which the survival falls to 1 − q or below. If it
  never does, the quantile is reported as not identified (`null`), never
  extrapolated.
- **Intervals.** 95 % percentile bootstrap over events, 1,000 resamples, seeds
  from `spawn_seeds(20261007, …)`. A resample in which a quantile is not
  identified counts as +∞, so an interval whose upper end would be +∞ is
  reported as not identified.
- **Strata.** Zone × changer speed at the change (v < 10, 10–20, ≥ 20 m/s, the
  classes of every I-24 lane-change artifact) and all speeds; each definition.
- **Sensitivities** (fixed now, reported, never adopted): no fragment bridging;
  only changes at least 200 m past the zone start (the zone start cannot then
  censor below 200 m); the two speed definitions; all speed classes pooled.
- **Also reported:** where entrants change, as quantiles of the distance past
  the zone start. MERGE_MODEL_BRIEF §1.5 records that no such distribution had
  been extracted.

## 5. Coverage and the direction of each bias

I-24 MOTION tracks about half of the peak vehicle-time [record, I24_DATA §4];
the artifact carries the per-window coverage estimates of
`artifacts/i24_coverage.json` beside the event counts.

| source | effect on the `gap` reach | handling |
|---|---|---|
| an untracked vehicle inside the observed gap | two true gaps read as one; the entrant can pass from one to the other unseen: **reads long** | stated; not a bound, because the next row also acts |
| an untracked true L or F at the change | the recorded partner is a farther vehicle; the run can read long or short | stated |
| a partner's track begins, an intruder's track ends | unknown whether the gap persisted | censored, not ended |
| tracker fragment switches, short absences | none if followed | continuity 2 m, bridge 1 s; no-bridge sensitivity |
| the zone start (ramp tracks begin at the gore) | an early changer that lined up on the ramp is censored early; Kaplan–Meier assumes censoring independent of the reach: **may read short** | the ≥ 200 m sensitivity |
| lane bands (`floor(y / 12 ft)`), changes timed mid-manoeuvre | a change is dated when the centre crosses the line | the 1 s debounce; the walk starts at that sample |

The speed definitions read speeds, which are robust to coverage
[record, MERGE_MODEL_BRIEF coverage rules], of a recorded leader that may not be
the true one.

## 6. Validation on synthetic trajectories

[synthetic] `tests/test_calibration/test_calibration_merge_anticipation.py`
(36 tests) and `tests/test_scripts/test_measure_merge_anticipation.py` (2), all
passing on 2026-10-07. The frames are hand-built on the 0.2 s grid: a lane-4
platoon at 25 m/s with fronts 60 m apart, and an entrant in lane 5 that runs at
25 + dv m/s until `t_on`, then holds a mid-gap position and changes at 40 s.

**Planted reaches are recovered exactly.** Six geometries, entrant faster
(dv = 2.5–5 m/s, overtaking into the gap) and slower (dv = −3 and −6 m/s,
falling back into it, the slow-ramp case), with planted `gap` reaches of
211–726 m and `speed_1` reaches of 25–500 m:
- the measured `gap` and `speed_1` reaches equal the planted ones to 1e-6 m
  (the test also asserts the looser tolerance of one sample's travel);
- a batch of 12 planted events is recovered event by event;
- the onset diagnostics read the planted speed differences (±3 m/s) and a
  follower distance of 0.1 m at onset.

**Edge cases, each at the planted sample and for the planted reason:**

| case | outcome |
|---|---|
| the entrant's track switches fragment id mid-run | followed; reach unchanged; `changer_stitched` |
| a 0.6 s hole in the entrant's track | bridged, reach unchanged, 0.6 s unknown; with no bridging, censored `changer_track_start` at the hole |
| the entrant's track begins mid-run | censored `changer_track_start` at its first sample; the speed onset inside the track is still observed |
| L's track begins at 28 s (and at 33 s) | `gap` censored `partner_track_start` at 28 s; `speed_1` censored when L begins inside its run |
| a vehicle between entrant and L whose track ends | censored `intruder_track_end` |
| the same vehicle leaving the target lane (still tracked) | an observed onset, the run ending where it left |
| a duplicate fragment of L, and of the entrant for 0.6 s | no break; 0.6 s unknown |
| lookback cap; frame start | `lookback_cap`; `window_start` |
| the entrant came from the mainline | `changer_lane` where it was on lane 4 |
| a change 1.6 s after the track appears at the zone start (the ramp-start case) | censored `changer_track_start`, reach = change position = 40 m |
| a tracked ramp lane upstream of the gore | the walk continues up it (onset 390 m upstream); with a 50 m limit, `upstream_limit` |
| a vehicle that never changes lanes | no event |
| no follower at the change | `gap` `no_partner`; the speed definitions evaluated |
| speed 3 m/s above L until the change | `speed_1` reach 0 (observed), `gap` reach > 100 m |
| speed excursions of 0.4 s and 1.2 s | the first bridged, the second ends the run |

**The estimator.**
- Kaplan–Meier reproduces the textbook 6-MP arm of Freireich et al. (1963):
  S(22) = 0.538, S(23) = 0.448, median 23, the 75th percentile not identified.
- Under independent censoring (n 600, log-normal reaches, 36 % censored) the
  Kaplan–Meier median is 135.0 m against the sample's own 138.2 m; the
  complete-case median is 111.5 m (19 % low); the 95 % interval
  [125.7, 144.3] covers the sample median.
- The rule (§8.2) adopts the rounded primary median, falls back to the pooled
  stratum when the primary has fewer than 100 events, refuses when the median
  is not identified, and refuses a too-wide interval.

**End to end.** The driver runs its chunk loop on a 5,418-row synthetic
processed table with one entrant in each I-24 zone (zones from the committed
`artifacts/i24_replica_inputs.json`) and writes a complete artifact. The
weave entrant reads 520.8 m (`gap`) and 240.0 m (`speed_1`), as planted. The
slower acceleration-lane entrant reads 262.0 m and 120.0 m; its `speed_2` is
censored at the frame's start, because a 2 m/s offset lies inside the ±2 m/s
band.

**What synthetic validation does not show:** real lane-assignment noise,
coverage holes inside gaps, congestion, and the share of events the censoring
will take. Those are read off the artifact (`coverage`, `outcomes`,
`share_censored`).

## 7. Cost and how to run

- **Stage snippet:** `scripts/gcp/pipeline_i24.sh (stage p6_i24_anticipation)`,
  stage `p6_i24_anticipation`, opt-in, to be pasted into
  `scripts/gcp/pipeline_i24.sh` before its done marker. It was not wired in
  because another session was editing the pipeline.
- **Command on the VM:** `uv run --no-sync python scripts/measure_merge_anticipation.py --out artifacts/merge_anticipation_i24.json`.
- **Data:** the launcher's default `--data-set i24` ships
  `data/i24motion/processed/i24_wb_20221130/`; nothing else is read except
  the committed `artifacts/i24_replica_inputs.json` and
  `artifacts/i24_coverage.json`.
- **Output:** `artifacts/merge_anticipation_i24.json` only. It holds the
  summaries, the proposal, coverage, provenance (the source zip's hash, the
  table's sha256, the code commit and its dirty flag) and a compact per-event
  table. It rides in every archive with `artifacts/*.json`.
- **Estimated cost:**
  - Stage 12 read the same 16 chunks in 15 s, and stage 13 with an 18 s pad
    took 163 s at 1.7 GB peak [artifact, `wall_s` / `peak_rss_mb` of
    `i24_lane_change_gaps.json`, `i24_critical_gaps.json`].
  - The walk measured 0.12 s on a 338k-row, 50-event synthetic stand-in, and
    1,000-resample summaries 0.33 s for 12 strata [synthetic].
  - Estimate: **3–5 min on n2-standard-32, about 2 GB peak, one process**.
    With boot and setup through the bucket, about 15–20 min billed (under
    $1).
- **Example launch:**
  `scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p6 --bucket gs://<bucket> --self-delete --via-bucket --data-set i24 --cap-min 45 --pipeline-args '--stages "p6_i24_anticipation"'`

## 8. Pre-registration: adopting a measured value

### P1 — 2026-10-07, before the measurement runs

Fixed before the stage has run once. A change after the artifact exists is a
dated amendment below, reported both ways. The script implements §8.1–8.2
mechanically (`preregistered_proposal` in the artifact;
`calibration.merge_anticipation.propose`); the rest is the procedure that
follows a proposal.

#### 8.1 What is adopted

- **One constant: the Kaplan–Meier median of the `gap` reach, rounded to the
  nearest 10 m.** It is not a per-driver distribution. A per-driver draw would
  need a new seeded stream in the runner and new goldens, and the one-day,
  one-site sample cannot support a distribution's shape. If the measured
  interquartile range exceeds the median, a per-driver draw may be proposed
  later as its own amendment.
- **Primary stratum:** the Hickory Hollow–Bell Road weave, entering changes
  with the changer at 20 m/s or more at the change. It is the weaving-section
  analog of T.H.52, in the regime the T.H.52 test asks the model to reproduce:
  the real section carried its 05:30–05:50 demand in free flow, S790 at
  25.8–26.6 m/s [record, MERGE_MODEL_BRIEF §1.6].
- **Fallback stratum** (only if the primary fails a check): the weave and the
  Old Hickory acceleration lane pooled, changer at 20 m/s or more.

#### 8.2 Identification checks (each stratum, in order)

1. at least 100 evaluated events (both partners present at the change);
2. the median is identified (the survival falls to 0.5);
3. the median is identified in at least 97.5 % of the 1,000 bootstrap
   resamples, so the 95 % interval has a finite upper end;
4. the interval's width is at most max(50 m, the median).

**If no stratum passes, `lookahead_m` stays at 120 m.** The artifact is
committed and reported as "not measurable at this coverage". No capacity arm
uses 200 or 300 m.

#### 8.3 What the value replaces

**The ramp anticipation only.** The follower search reach keeps 120 m. The
measurement is of the first; the follower's distance at onset (§3) is reported
for the second, not adopted. This needs a hash-neutral split of the key:
- a new weave key (working name `anticipation_m`, `0` = use `lookahead_m`) read
  by the `approaching` test in `_weave_step` and `_measured_step`;
- `lookahead_m` keeps the follower search;
- unset, the split must reproduce the T.H.52 fixture bit for bit at seeds 3–7,
  as the diagnosis's counterfactuals did.

The runner is being edited by another session, so the split is the first step
after the measurement, not part of this change.

**Mapping.** A model entrant tracks its gap from the anticipation reach before
the gore to its change, a median 15 m past the gore on the fixture (§1). The
model's own reach is therefore about the constant plus 15 m. No correction is
applied, because the adopted number would then depend on a model output. The
+15 m is recorded as a known bias, smaller than the interval the checks allow.

#### 8.4 What is not adopted

- the speed definitions (§2.2);
- the Old Hickory value for the scripted merge (a different mechanism) or the
  measured model's acceleration lanes (`merge_model.LOOKAHEAD_M`), unless a
  later amendment registers it;
- any stratum not named in §8.1.

#### 8.5 Fixture checks, after the split

Setup:
- macOS, the calibrated drivers, seeds 3–22;
- the locked section test through `scripts/merge_model_selfcheck.py th52 --model weave --fleet-from scenarios/mndot_i94_wb_stpaul_weave_dc.yaml`
  with `anticipation_m` at the adopted value (the self-check has no option to
  set a weave key yet; one is added with the split, as the diagnosis's
  harness `artifacts/weave_loss_2026-10-07/harness/arm.py` sets keys today);
- paired against the same command at 120 m (reference: 0 collisions, 2
  vehicle-steps at −9 m/s², 66 given-up exits of 8,334 reached; [record]
  WEAVE_LOSS_DIAGNOSIS §2–§4).

If a gate fails, the value is not adopted, the result is reported, and no
gate is re-thresholded.

| # | gate |
|---|---|
| G1 | zero collisions; vehicle-steps at −9 m/s² ≤ the reference + 2 over the 20 seeds |
| G2 | given-up exits ≤ 2 % of the exit-bound vehicles reaching the section, at every seed (the locked test's criterion iv) |
| G3 | the 29-run grid and the G0 rows (`merge_model_selfcheck.py grid --model weave`, the key set on every weave): zero collisions, no lock |

Reported with paired 95 % intervals, **not gates:**
- exit-end flow;
- the lowest station speed;
- the stranded-entrant time;
- the anticipation share of command-steps (`cf_log`'s reading);
- the same arm with both uses at the adopted value (the diagnosis's
  single-constant form).

**Pre-stated direction.** The flow should rise if the adopted value exceeds
120 m (the 200 / 300 m dose-response) and fall below it. Below 120 m nothing
is on record except the removal arm.

#### 8.6 Corridor checks (cloud, after the fixture gates)

Setup:
- the I-94 four-hour battery, 20 seeds, in the `p4_i94_battery_gate`
  configuration (`scenarios/mndot_i94_wb_stpaul_weave_dc.yaml` under the
  xlsfg reference), with the value on both weaves (T.H.52 and Ruth St);
- paired against that battery's own seeds and drivers.

| # | gate |
|---|---|
| C1 | zero collisions |
| C2 | no lock: the lowest departed share ≥ 0.8 × the median |

Reported, not gates: S790 06:30–07:30, departed share, RMSPE and GEH share,
each with paired intervals.

**Self-check, reported.** The model's change positions (§4) beside I-24's, and
the extractor run on fixture trajectories. Simulated runs do not record ramp
edges, so the model's reach there is censored at the gore. It cannot be a
criterion.

#### 8.7 Then

If G1–G3 and C1–C2 pass, the value becomes the default of the anticipation
key. That follows the hash policy:
- `CONFIG_HASH_VERSION` is bumped;
- the goldens are regenerated with a note;
- a CHANGELOG entry records the change, citing this document and the
  artifact's sha256.

Until then it is an opt-in scenario value.

#### 8.8 Predictions (recorded now, judged against the artifact, not gates)

- **P1.** In the primary stratum, `speed_1` reads a reach of 0 for at least
  half of the evaluated events (§2.2(a)).
- **P2.** In the primary stratum, the `gap` reach is censored for a larger
  share than `speed_1`. Its runs are longer, so fragment starts reach them
  more often.
- **P3.** At least one of checks 2–4 may fail in the primary stratum. The
  free-flow class is the smallest: 295 entering changes in the weave, of which
  11 % have no lead and 13 % no lag within 200 m [artifact,
  `i24_lane_change_gaps.json` `summary_by_zone`]. In that case the fallback
  decides.

#### 8.9 Relation to WEAVE_LOSS_DIAGNOSIS §6.3

The diagnosis fixed "adopt only a measured median, as a dated amendment, judged
by F1 and F4". This pre-registration keeps that rule and refines it in three
ways:
- **The definition is `gap`, not the speed sketch** (§2.2(a)).
- **The stratum is fixed** (§8.1).
- **F1, a flow gain with a lower bound above 0, is reported, not a gate.**
  Making adoption conditional on a gain would select the measured value by
  its effect on the fit, which §6.1 forbids. F4's safety terms are kept as G1;
  G2 uses the locked test's own exit criterion.

The owner may restore F1 as a gate before the stage runs. After it has run,
any change is an amendment reported both ways.

## 9. Not done here

- Wiring the snippet into `scripts/gcp/pipeline_i24.sh` (being edited by
  another session).
- The runner split of §8.3 (`runner.py` is being edited by another session).
- A `docs/CONTRACTS.md` entry for the artifact and a CHANGELOG line (both files
  have uncommitted edits from another session).
- The follower's yielding onset (§2.2(c)).
- A coverage-thinning check of the `gap` reach on complete-coverage NGSIM
  US-101, as WP-91 did for gaps (`calibration.thinning`). It would size the
  "reads long" row of §5. US-101 is congested, so it says little about free
  flow.

## 10. Limits

- **One day, one direction, one weaving section and one acceleration lane.**
  The free-flow class is small.
- **Positions, not intentions.** Holding a gap by matched speeds counts as
  anticipation whether or not it was chosen.
- **Coverage.** The `gap` reach is expected to read long (§5), and the
  zone-start censoring may push it short. The two do not cancel by
  construction; the sensitivities show their sizes.
- **Mapping.** One constant in the model stands for a behaviour that varies by
  driver and by traffic state. The rule adopts a central value for the regime
  of the fixture it will be judged on.

## Result — 2026-10-07 (stage p6 on one n2-standard-16, us-central1-a, about 1 minute of compute)

`artifacts/merge_anticipation_i24.json`: 5,094 confirmed entering changes on the 06:00–10:00 table (49.1 M rows;
peak 2.0 GB). Under the pre-registered rule (§8):

| stratum | n (onsets observed) | censored | Kaplan–Meier median of the `gap` reach | 95 % interval | checks |
|---|---|---|---|---|---|
| primary: Hickory Hollow–Bell Road weave, ≥ 20 m/s | 233 (73) | 69 % | 82 m | 67–154 m | interval too wide (fails) |
| fallback: weave + Old Hickory, ≥ 20 m/s | 858 (284) | 67 % | **125 m** | **107–154 m** | all pass |

**The rule proposes 120 m — the model's current constant.** The measured anticipation reach of real entrants
agrees with `lookahead_m` = 120 m; the 200–300 m that would recover 100–180 veh/h of T.H.52 capacity
(docs/WEAVE_LOSS_DIAGNOSIS.md) is not supported by I-24's drivers. `WEAVE_DEFAULTS` is unchanged. The weave's
capacity loss is therefore not an anticipation-distance problem; of the two unmeasured inputs the diagnosis
named, the ramp-to-ramp crossing share is what remains.

**The direction of the remaining bias is not known** (§5, §10). Half coverage is expected to read the reach
long, by an unmeasured amount and not as a bound; censoring at the zone start may read it short. The
sensitivities built for the second read longer, not shorter [artifact, `sensitivities`, fallback stratum,
`gap`]:

| sensitivity | Kaplan–Meier median | 95 % interval | rounded to 10 m |
|---|---|---|---|
| as registered | 124.8 m | 107.4–153.5 m | 120 m |
| changes at least 200 m past the zone start | 133.6 m | 110.2–170.3 m | 130 m |
| no fragment bridging | 133.6 m | 107.4–156.3 m | 130 m |

The registered rule's output stands at 120 m, but the stratum's true median may be somewhat longer than
measured: its point estimates run from 125 to 134 m. No interval in the table reaches 200 m. (In the primary
stratum the 200-m sensitivity reads 121.5 m on 90 changes, against 82.0 m as registered, and identifies no
upper end.) So 200–300 m stays unsupported, and the conclusion above holds on this evidence.
*Corrected 2026-10-07 (regression review).* This paragraph first said that "with the coverage caveat (§7: half
coverage merges gaps and reads the reach long) the true reach is if anything shorter". That contradicted §5,
§10 and the artifact's own `limitations` and `sensitivities`; the coverage discussion is §5, not §7.
