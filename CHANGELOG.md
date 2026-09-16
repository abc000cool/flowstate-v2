# Changelog

All notable changes to FlowState are documented here. Every headline number
below traces to a committed, seeded artifact (CLAUDE.md §0.1/§0.5); nothing
is quoted that cannot be reproduced from the referenced runs.

## [Unreleased] — I-24 MOTION flagship (docs/ROADMAP.md §1)

### Production-readiness round: API hardening, job recovery, dashboard contract, docs consistency (2026-09-16)

A two-stage audit (five auditors, every finding attacked by an adversarial verifier: 23 confirmed, 2 refuted) followed by fixes in five file groups, each reviewed. Test count 986 → 1,000+ non-slow; frontend 38 vitest cases, typecheck and build green.

- **Security (API).** Scenario configs could name any server file (`network.osm_file`, `fleet.idm_calibration`, `fleet.heavy.idm_calibration`): the worker read it and the run error echoed its contents. `POST /scenarios`, `/runs` (overrides) and every `/sweeps` cell now answer 422 (`type: path_outside_roots`) unless the path resolves inside the results root, the uploads dir, `FLOWSTATE_DATA_DIR`, or the repository's `artifacts/` and `data/`; relative paths resolve against the repository root, so the shipped presets keep working. Defence in depth: run/sweep/report error text is an exception chain without frames or paths, and pydantic validation errors are recorded without their input values (`api.jobs._error_text`). A non-ASCII `X-API-Key` answers 401 instead of 500. Request bodies are capped (`FLOWSTATE_MAX_BODY_MB`, default 8; calibration uploads `FLOWSTATE_MAX_UPLOAD_MB`, default 200; HTTP 413; uploads streamed in 1 MiB chunks, never read whole); calibration `params` are validated by `api.schemas.CalibrationParams` (extra forbidden, every numeric bounded). Hostile upload filenames land inside the upload directory. Request models `RunCreateRequest`, `SweepCreateRequest`, `ReportCreateRequest` forbid unknown fields.
- **Correctness (API).** `seeded` in run and metrics responses now comes from `ScenarioConfig.seeded` (perturbation *or* lane closures) instead of the perturbation alone, matching `meta.json` and the report. `POST /sweeps` gained `include_baseline` (default false; one penetration-0 / compliance-1 cell per distinct controller, counted toward the 200-cell ceiling, skipped when the grid already holds penetration 0).
- **Job recovery (worker).** RQ job ids equal store row ids (`JobQueue.enqueue(..., job_id=)`, unique enqueue); rows are claimed with a compare-and-set; `api.jobs.reconcile_store` repairs rows left at `queued`/`running` by a dead work horse, a SIGKILLed worker or a lost Redis (runs at worker start, on RQ's maintenance pass, and once at API start); the worker's `work_horse_killed_handler` fails the row of a killed horse. Sweep child rows are created in one transaction with the grid, and a re-run dispatches only cells still `queued`. Per-replicate metrics are precomputed by the worker at the end of every run and written atomically; `GET /sweeps/{id}` reads caches only (no trajectory reads in a polled request path) and `GET /runs/{id}/metrics` computes once only for runs from before this change.
- **Deployment.** `docker-compose.yml`: Redis append-only on its own volume (`redis-data`), worker with `init: true` and a 6 h `stop_grace_period` (a plain `docker compose down` lets a run finish; `docker compose kill worker` abandons it). Dockerfile installs all extras, so the deployed image renders report PDFs (`GET /reports/{id}/pdf` answers 200). CI installs `redis-server` and the Redis-queue tests refuse to skip under `CI`. Coverage now measures `packages/api/api` too.
- **Dashboard.** The sweep launcher sent `controller` (singular), which the API silently dropped: every dashboard sweep ran without its controller. Now `controllers: [..]` plus `include_baseline` (checkbox, default on), a labelled baseline row in the matrix, deltas against the penetration-0 cell (previously against the lowest AV cell), and a failed fan-out stops polling. Metric keys renamed to the `validation.metrics.Metrics` field names (a vitest contract test parses the dataclass); the default sweep metric is `sigma_v_spatial_ms`. Reports: the view polls `GET /reports/{id}` while queued/running, shows refusals and failures, and "Download .md" reads `/reports/{id}/markdown` (it previously saved the JSON status body). The demo-mode mock follows the same contract.
- **Docs.** `docs/I24_SWEEP.md` baseline paragraph rendered from the artifact (it was unrendered format placeholders). The I-24 GEH criterion row is quoted everywhere as 0.7 / 16.7 / 18.8 / 20.1% (recommended-coverage count table, the artifacts' primary rule) with the former 24.3 / 11.8 / 15.3 / 10.4% kept as a labelled own-assumption-table history row (README, ROADMAP, dossier, I24_VALIDATION, I24_CAPACITY); the dossier's reviewer Q&A says 5 of 7 rows like the rest of it; README's controller caveats match its results table; the sweep reproducer command and script defaults name `i24_replica_speedcal`.
- **Tests.** Boundary-schedule integration test (three steps applied, congestion arrives and clears on the exit edge); golden regressions for the work-zone closure, heavy vehicles, the three merge models, ramp metering and managed lanes (`tests/golden/`, summary statistics only; the two existing goldens regenerated with the new exact-compared counters, values unchanged to every digit); a checked-in interchange fixture `tests/fixtures/merge.osm`. The work-zone golden compares at 1e-4 relative instead of 1e-6: SUMO's Linux build (CI) and the macOS build differ at 2e-6 in that case's σ_v (the first CI run of this commit failed on it; the counts and every other case match to 1e-6).
- `docs/DEPLOYMENT.md`: the pilot runbook (the Compose stack, every environment variable, sizing from the M5 load test, backups of the runs and Redis volumes, key rotation, upgrade with the 6 h stop grace, job recovery and `rq requeue`, reading failures); linked from README and the docs index.
- Not done, carried on the roadmap: a pure-ASGI body counter for chunked bodies to `/runs`, `/sweeps`, `/reports` (today only `/scenarios` streams with a running cap; chunked uploads are capped after Starlette spools them); worker-side root checks in `microsim.vehicles.resolve_calibration_path` and `microsim.networks.osm_import` (the API confines the paths); a macro-tier closure golden; `schema_version` of the four canonical validation artifacts stays 5 after the in-place GEH re-score (`speedcal_heavy` is 6).

### Scripted late-merge model and collision accounting (2026-09-16)

- **Schema:** `RampSpec.merge = "scripted"` and `RampSpec.merge_params` (keys `accept_gap_s`, `force_after_s`, `force_within_m`, `change_duration_s`, `lookahead_m`, `courtesy`; defaults in `flowstate_core.config.SCRIPTED_MERGE_DEFAULTS`, unknown keys rejected, hash-relevant). The network is the plain `lane_change` one (no netconvert patch); the runner drives every vehicle on the acceleration lane: desired speed matched to the mainline vehicle ahead through `setMaxSpeed` (never `setSpeed`, whose max-deceleration clamp overrides its safe-speed clamp in SUMO's influencer and produced rear-end collisions in the first draft), a lane change requested once the mainline gaps ahead and behind both clear the accepted time gap, a forced change (`laneChangeMode` 256, the follower yields) after a wait inside the last stretch of the lane, and optional courtesy yielding (the blocking mainline follower's desired speed is lowered until the gap opens). `meta.json` records `scripted_merges` (entered, changed, forced, unfinished, mean and p90 wait). `scripts/i24_merge_experiment.py` parses `_scripted` with `_accept<s>`, `_force<s>`, `_within<m>`, `_look<m>`, `_court<m/s>`; the cloud pipeline gained the opt-in stage `merge_scripted` (six single-seed I-24 variants against the reference arm).
- **Fixture result (tests/test_microsim/test_microsim_merge_managed_meter.py, 2-lane mainline at 0.6 veh/s plus a 0.25 veh/s ramp, 300 s, seeds 3 and 4):** no collisions in any variant after the `setMaxSpeed` change; ramp vehicles through the merge in 300 s: SUMO lane-change model 47 / 45, zipper 57 / 59, scripted 31 / 32, scripted with courtesy 2 m/s 36 / 37. On this congested fixture the scripted merge is slower than SUMO's own negotiation because the mainline no longer cooperates with a signalled change; whether it breaks the I-24 replica's right-lane lock (a different mechanism, docs/I24_VALIDATION.md §0.5) is the question the cloud probe answers. Not a headline result: single seeds, a synthetic fixture.
- **I-24 cloud probe (VM `flowstate-pipeline`, n2-standard-8, 22 minutes, about fifteen cents; `artifacts/i24_merge_experiment_scripted.json`, docs/I24_VALIDATION.md §0.8):** reference (SUMO lane change) against five scripted variants on the fitted arm, single seed. The scripted merge lifts the entry segment from 26 km/h to 36 to 41 km/h (observed 36) but admits only 1544 to 1638 of Old Hickory's 2241 planned vehicles against the reference's 2066, so insertion falls from 0.935 to 0.91 and the held-out RMSPE rises from 0.457 to 0.56 to 0.65. A negative result, documented; the fitted arms keep the lane-change model. The bucket copy plus self-delete path worked end to end (archive in the bucket 2 s after the pipeline exit, instance gone before the watcher's own delete); the bucket was deleted after ingest.
- `scripts/gcp/launch_i24_pipeline.sh`: `--allow-dirty` (the VM runs `git archive HEAD` either way) and the bucket-root parsing of the IAM grant fixed (`${BUCKET%%/*}` produced `gs:` and aborted the launch after the VM was created); `ingest_pipeline_results.sh` accepts the scripted-merge artifact; `bootstrap.sh` defaults to the published sweep arm.
- `meta.json` gains `n_collisions` (exact count of SUMO collision detections over the run; `--collision.action warn` keeps both vehicles, so a persisting overlap is counted every step) and `collisions` (the first 50 events: time, collider, victim, type, lane, position). Every scripted-merge test asserts zero.

### Business model, outreach list and the website's plain-English mode (2026-09-15)

- `docs/BUSINESS_MODEL.md`: Path A (corridor studies, agency pilots, a yearly workspace) and Path B (a live speed advisory on the agency's own signs or a partner's connected-vehicle channel, never a consumer app), the B2B sales order, unit economics and cost to build, with public anchors (HNTB and Kimley-Horn rate schedules, the USDOT ITS Costs Database, probe-data contracts, the I-24 SMART Corridor gantry cost, AMPO) and this repository's measured compute costs. Prices remain hypotheses and are labelled so.
- `scripts/business_cost_model.py` → `docs/business/flowstate_cost_model.xlsx` (Assumptions with every input, Cost to build, Unit economics A, Path B live advisory, three-year P&L with live formulas, Sources). `scripts/business_outreach_list.py` → `docs/business/outreach_targets.xlsx` and `.csv`: 200 organisations in seven segments (52 state DOTs, 45 MPOs, 35 consultancies, 28 labs, 12 federal and industry bodies, 12 fleets and OEMs, 16 data and sensor vendors) with the role to reach, the channel and a pitch angle; no personal e-mail addresses are invented.
- Website (repository `flowstate-site`): a Business page, a Business link in the primary navigation, and on the research pages a large button that switches to a playful plain-English version of the research (an overlay; the technical page stays intact underneath).

### Merge levers for the next round (2026-09-07)

- **Fourth merge round, fourteen single-seed probes (docs/I24_VALIDATION.md §0.7; `artifacts/i24_merge_experiment_{visibility,sublane2,keepright,overtake}.json`):** the zipper's interleaving distance is inert at 200–600 m (admittance 61–64%); the sublane model locks with internal links at one sublane per lane and at 0.8 m with pushiness and right alignment (runs normally for ten minutes, then loses capacity everywhere); keep-right off reproduces the references to the digit; overtaking on the right raises insertion to 95–96% and GEH to 21–22% while the 15-min speed error rises from 24.8% to 33–34%. SUMO's lane-change and junction parameter space is exhausted for this merge; the next design is a scripted late-merge behaviour for ramp vehicles, tested like a controller.
- **Schema:** `FleetSpec.lc_overtake_right` (SUMO `lcOvertakeRight`, the US passing rule), written when set.
- **Schema:** `RampSpec.merge_visibility_m` (zipper connection `visibility`, the interleaving distance; `microsim.networks.merge_patch_files(visibility_m=...)`) and the sublane fields `FleetSpec.lc_sublane`, `lc_pushy`, `lc_impatience`, `lc_accel_lat`, `max_speed_lat`, `min_gap_lat`, `lat_alignment` (SUMO `lcSublane`, `lcPushy`, `lcImpatience`, `lcAccelLat`, `maxSpeedLat`, `minGapLat`, `latAlignment`; `microsim.vehicles.sublane_vtype_attrs`). All `None` by default and hash-neutral when unset; docs/CONTRACTS.md. `scripts/i24_merge_experiment.py` parses `_vis<m>`, `_sublane<res>`, `_pushy<v>`, `_impat<v>`, `_acclat<v>`, `_latspeed<v>`, `_mingaplat<v>`, `_lcsub<v>`, `_latalign<mode>` variant suffixes in any order.

### Cloud pipeline for the calibration round (2026-09-06)

- `scripts/gcp/pipeline_i24.sh` (VM side, resumable stage markers, EXIT trap powers the machine off on success or failure): zipper junction time-gap sweep (`FleetSpec.jm_timegap_minor_s`, chosen on the fit hour), the `zip` scenario family (`scripts/i24_build_replica.py --suffix zip --osm corrected --lc-strategic-ramp 1 --entry-lanes observed --merge zipper --jm-timegap`), the FHWA sequence on it (`i24_fit_demand_scale.py --base-yaml/--scenario-out/--name`, `i24_fit_boundary_ramps.py --base-yaml/--scenario-out/--name`), the heavy arm (`scripts/i24_add_heavy_arm.py`), the 20-seed batteries (`i24_validate.py --family zip`, plus the canonical `speedcal_heavy` arm), and the headway-cap sweep (`scripts/i24_cap_sweep.py`, paired 95% intervals, trajectories pruned). `scripts/gcp/launch_i24_pipeline.sh` creates the VM with a boot-time hard cap (startup script `shutdown -h +300`), ships the data and starts the pipeline under systemd; `scripts/gcp/watch_pipeline.sh` fetches the archive, deletes the instance, never restarts it at the working size, and deletes it unconditionally at its own deadline.
- `FleetSpec.jm_timegap_minor_s` (SUMO `jmTimegapMinor`, written on every vType when set): the zipper's merged-lane lever. Builder options `--suffix`, `--lc-strategic-ramp`, `--merge`, `--merge-ramps`, `--jm-timegap`; validator `--family` and the `speedcal_heavy` arm; `scenarios/i24_replica_speedcal_heavy.yaml` (the fitted arm plus the recording's heavy share, `scripts/i24_add_heavy_arm.py`).
- **Second VM (21:33–00:55 UTC, 3.4 h at 32 vCPUs, about five dollars):** `launch_i24_pipeline.sh --bucket --self-delete --pipeline-args '--stages "battery_lost prune_lost cap_sweep rescore"'`; the archive reached the bucket after every stage and the instance deleted itself 90 s after its final archive; the bucket was deleted after ingest. The project's default compute service account needed `roles/storage.objectAdmin` on the bucket and `roles/compute.instanceAdmin.v1` on the instance (this project grants it no Editor role); the launch script now grants both.
- **Outcome of the first run (VM `flowstate-pipeline`, 08:55–13:57 UTC at 32 vCPUs, deleted 19:47 UTC; about eight dollars).** The calibration stages and both batteries completed by 11:31 UTC; the boot-time cap (300 min) killed the machine 2.4 h into the headway-cap sweep, and the archive — written only by the EXIT trap, to `/tmp`, which Debian clears at boot — did not survive (scripts/gcp/README.md, docs/LESSONS.md rows 14–16). Recovered intact from a cut-off interim transfer and installed: `artifacts/i24_merge_experiment_zipper_jm.json` (jm 0.5 s chosen on the fit hour), `demand_scale_i24_zip.json` (s = 0.925; RMSPE train 0.331, held-out 0.441), `i24_boundary_ramps_fit_zip.json` (Old Hickory × 0.75, Hickory Hollow × 1.25, its exit × 1.125; train 0.332, held-out 0.356, 96% inserted), `i24_validation_zip_corrected.json`, `i24_validation_zip_ramps.json` and the canonical `i24_validation_speedcal_heavy.json` (20 seeds each). Lost: the `zip` family's `tracked`, `speedcal` and `speedcal_heavy` battery artifacts (rerun on a second VM the same evening, stage `battery_lost`), every scenario file of the family, the VM logs and the cap sweep (rerun on the second VM with the summary now written after every configuration; until its artifact lands, docs/I24_SWEEP.md keeps the single-seed probe as the only evidence on `h_max_s`).
- The lost scenario files are rebuilt bit-for-bit from the fit artifacts: `scripts/i24_build_replica.py --suffix zip …` (config `2cda89cfbaf8`, the hash the `zip_corrected` battery recorded), `scripts/i24_fit_demand_scale.py --from-artifact` (`f2209020a42a`, the base hash the ramp fit's provenance recorded) and `scripts/i24_fit_boundary_ramps.py --from-artifact` (`b5175be6d854`, the hash the `zip_ramps` battery recorded); both fitters gained `--from-artifact` for exactly this (no simulation; the scenario is a function of the artifact) and the ramp fitter's scenario header now names the artifact it came from. `scripts/i24_validate.py --criteria-only --ring-seeds N` stores a fresh ring benchmark on an artifact whose ring rows were not run (`refresh_criteria(arm, ring_block)`): the heavy arm's ring rows are scored from 20 fresh seeds instead of standing as not evaluated.
- Zipper family against the canonical family (20 seeds each, criteria rows re-scored with the published sweep grid): `zip_ramps` RMSPE 34.2% (canonical `ramps` 34.8%), GEH < 5 on 23% of recommended-coverage link-hours (20%), stack wave speed 15.7 km/h (15.7); `zip_corrected` 37.7% / 22% / 16.1 (canonical 33.7% / 17% / 15.9); `zip_speedcal` 43.0% / 22% / 16.2 (35.9% / 19% / 15.8); `zip_speedcal_heavy` 34.1% / 20% / no stack peak (canonical heavy 35.5% / 23% / 17.3); `zip_tracked` 183.5%, free-flowing, 4 / 3 like the canonical tracked arm. The second VM's reruns reproduce the first VM's console numbers to every digit. The zipper merge, with its junction gap fitted, moves neither failing row out of failure and leaves the wave row passing: the merge-admittance defect of docs/I24_VALIDATION.md §0.5 (k) stands, and the family is kept as a documented negative result, not as a replacement.
- **Headway-cap sweep (2026-09-07, second VM, `artifacts/i24_cap_sweep_summary.json`; docs/I24_SWEEP.md last section):** baseline, FollowerStopper and `follower_stopper_capacity` at `h_max_s` 1.3 / 1.5 / 1.7 / 2.0 s, 20 seeds each with common random numbers on `i24_replica_speedcal` at 5% / 100%, paired 95% intervals. The baseline and FollowerStopper cells reproduce the 500-run sweep's cell to the digit. **The cap is not the lever:** throughput −37.6 / −37.8 / −33.9 / −34.8% against FollowerStopper's −36.0% (paired intervals about ±6 points), σ_v −55.5 to −56.1% against −56.2%, fuel +102 to +123% against +111%. The single-seed probe's halving (`artifacts/i24_controller_probe.json`) did not survive twenty seeds and is no longer quoted as a result.
- `scripts/i24_cap_sweep.py`: resumable per seed (replicates with metrics are not rerun; replicates with trajectories but no metrics are only analysed) and the per-run metrics now run in their own process pool (`--analysis-procs`, default min(procs, 8)); the second VM's sweep showed the sequential analysis idling 31 of 32 vCPUs for most of each configuration. The pooled path has not run on a VM yet.
- The cloud scripts after the post-mortem: `pipeline_i24.sh` archives to `$HOME` atomically after every stage (light: artifacts, scenarios, logs; full at exit with the first-seed replicates), copies to `PIPELINE_BUCKET` when set, runs its EXIT trap on SIGTERM, and deletes its own instance when `PIPELINE_SELF_DELETE=1` and the archive reached the bucket; `launch_i24_pipeline.sh --bucket/--self-delete`, default cap 480 min (the cap is a backstop, sized at twice the estimate); `watch_pipeline.sh` fetches the archive whenever it changed, treats a describe timeout as unknown rather than as a deleted instance, restarts a stopped instance only at two vCPUs and only when the local archive is incomplete, re-arms the cap before each fetch, pulls from the bucket when there is one, and is meant to run under `caffeinate -i`.

### Merge models, ramp metering, capacity-aware smoothing, managed lanes, report aggregation (2026-09-06)

- `RampSpec.merge` (`lane_change` | `acceleration_lane` | `zipper`): on-ramp acceleration lanes as SUMO's acceleration-lane attribute or a zipper junction, applied as netconvert patches in a second import pass (`microsim.networks.merge_patch_files`; `meta.json` `merge_models`, `net_patch_files`). Built to break the lane-discrete merge lock of docs/I24_VALIDATION.md §0.5 (single seed, corrected map, `artifacts/i24_merge_experiment_mergemodels.json`, §0.5 (j)): the acceleration-lane attribute reaches the net and changes nothing; the zipper removes the upstream crawl (entry 40 km/h against 36 observed, right lane the fastest lane as in the recording) but its merged lane admits too little (62% of the ramp's demand in, merge zone at 4–7 km/h, downstream 10–15 km/h too fast; 15-min RMSPE 40.7% against 24.8%) — the junction negotiation gaps are the next lever; ALINEA released 577 vehicles at 648 veh/h and kept the mainline in free flow, as a meter should.
- `RampSpec.meter: RampMeterSpec` with `controllers.ramp_meter.alinea` (Papageorgiou et al. 1991, density form, target = the calibrated diagram's critical density, no built-in target): a virtual stop line on the ramp's last edge releasing one vehicle per metered headway; rates, densities and releases in `meta.json` `ramp_meters`. Registry group `ramp_meter`.
- `controllers.follower_stopper_capacity` (`follower_stopper_capacity`): FollowerStopper with a time-headway cap (`h_max_s` 2.0, `g0_m` 4.0, `blend_m` 5.0) — beyond the cap the command is released toward the leader so the controlled vehicle stops holding traffic back; identical to FollowerStopper inside the cap, continuous across it, output within `[0, U]`. Single-seed comparison on the I-24 fitted arm at 5% / 100% (`artifacts/i24_controller_probe.json`, docs/I24_SWEEP.md last section): FollowerStopper −48% throughput / +128% travel time / −58% σ_v / +166% fuel; the capacity-aware variant −26% / +59% / −55% / +59% — the cap halves the cost for the same smoothing and does not remove it; `h_max_s` is the lever for the next sweep.
- `managed_lanes: list[ManagedLaneSpec]` + `FleetSpec.hov_fraction`: HOV lane rules as lane permission windows admitting SUMO class `hov`; eligible vehicles drawn per vehicle and flagged (`is_hov` column, `n_hov`). Not seeded. Not applied to the I-24 replica: the recording shows lane 1 carrying 34% of vehicle-time at general-lane speed on 30 Nov 2022, and neither the rule in force nor the eligible share is known for that day.
- `validation.report`: the speed criterion by time aggregation with the recorded field's own repeatability floor (`speed_aggregation_rows`, inputs `segment_speeds_obs/sim`, `segment_window_s`), and group labels for closures, heavy shares, managed lanes, meters and merge models so they enter the contrast table like controllers. `scripts/i24_report.py --arms all` passes the arm matrices (the I-24 run trees are pruned, so the reports regenerate with the next battery).
- Tests: patch writer, merge models and metering on an extended OSM ramp fixture, managed lanes on a corridor, ALINEA and capacity-FS unit tests, report table and labels.

### Config-hash policy v2 (2026-09-06)

- `config_hash` now hashes `{"hash_version": CONFIG_HASH_VERSION, "config": model_dump(exclude_defaults=True)}` (network `kind` kept). Adding an optional field no longer moves the hash of scenarios that do not use it; a default change must bump `CONFIG_HASH_VERSION`, which `tests/test_flowstate_core/test_config_hash.py` enforces through a pinned defaults snapshot (`tests/golden/config_defaults.json`) and a pinned ring hash. Every hash moved once (v1 → v2); run metadata carries `config_hash_version`; documents dated before 2026-09-06 quote v1 hashes, and their artifacts keep their config snapshots. Goldens regenerated with statistics unchanged.

### Temporary lane closures and heavy vehicles (2026-09-06)

- **Schema:** `ScenarioConfig.closures: list[LaneClosureSpec]` (`start_m`, `end_m` from the start of the analysis corridor, `lanes` as SUMO indices, `t_start_s`, `t_end_s`, `label`). Micro tier: the listed lanes of every overlapped corridor edge refuse every vehicle class for the window and get their permissions back afterwards (vehicles caught on a closed lane leave it; the insertion edge is never closed); `meta.json` records lane ids, skipped ids and applied/released times. Macro tier: the overlapped cells' interfaces are capped at `q_max × share of lanes open`. Any closure labels the run `seeded=True` (an imposed disturbance is not the emergent phenomenon). Example: `scenarios/corridor_10km_workzone.yaml`.
- **Schema:** `FleetSpec.heavy: HeavyVehicleSpec | None` — a heavy-vehicle share drawn per vehicle after every existing draw (fleets without the block reproduce their previous draws exactly), with its own length, SUMO `vClass`, emission class and IDM population (`idm_calibration` or all five means; no built-in truck defaults). Heavy vehicles are never controlled vehicles. New trajectory column `is_heavy` on every run; `meta.json` `n_heavy` / `heavy_fraction_realized`. The macro tier does not represent them and says so in its meta.
- **Calibration:** `scripts/i24_extract_episodes.py --classes heavy` and `scripts/fit_idm_i24.py --classes heavy` → `artifacts/idm_i24_heavy.json`: 197 semi/truck follower episodes (138 fit / 59 held out), holdout gap RMSE 7.09 m, means v0 30.4 m/s, T 1.96 s, a_max 0.67 m/s², b 1.69 m/s², s0 2.76 m (cars after capacity calibration: T 1.32, a_max 1.06). Not capacity-calibrated (no step 1 applied to the heavy population). `scripts/i24_heavy_share.py` → `artifacts/i24_heavy_observed.json`: 9.2% of mainline fragments and 5.5% of vehicle-time are heavy in the study period, median length 20.3 m. `scripts/i24_build_replica.py --heavy` puts them into the replica (off by default, keeps hashes); `scripts/i24_merge_experiment.py as_is_heavy` runs one seed with them (`artifacts/i24_merge_experiment_heavy.json`): insertion 94.5 → 88.0%, section flows 8% lower, speeds within 1–2 km/h — trucks at their fitted headways widen the replica's capacity shortfall, so an arm carrying them needs the capacity calibration redone with the mixed fleet; the validation arms stay without heavy vehicles.
- Nine new tests (schema, plan draws, route file attributes, an integration run with a closure and a heavy share, a macro closure run). The two schema fields move every config hash; goldens regenerated with statistics unchanged (`ring_sugiyama` 543ae9029c50, `corridor_10km_smoke` 6b68fec0a947, `macro_corridor` 154e14a6f4d2).

### I-24 residual taken apart: map defects, lane-level diagnosis, scoring tables (2026-09-06)

- `scripts/i24_correct_osm.py` → `data/osm/i24_motion_corrected.osm` (+ `.provenance.json`): the OSM extract ends the Old Hickory acceleration lane 244 m early and starts the Hickory Hollow deceleration pocket 345 m late against the provider's landmark layer and the recording's auxiliary-lane occupancy; the script moves the two way boundaries to the landmark positions (one interpolated node each, way membership transferred; no node, tag or lane count changed; connections unchanged after netconvert). `scripts/i24_build_replica.py --osm {original,corrected} --lc-strategic V` builds on either map (config hashes move with the map; the original stays the default).
- `scripts/i24_lane_profile.py` → `artifacts/i24_lane_profile.json`, `docs/figures/i24_lane_profile.png`: lane-by-lane share and speed profiles, recording vs replica (SUMO lane indices mapped through the edge lane count). The replica's right lane crawls at 8 km/h with 37% of vehicle-time 1.5 km upstream of the gore and ramp traffic merges at 9–13 km/h; the recording's right lane is the fastest lane at the entry and slows only inside the acceleration lane while lanes 1–3 hold 30–34 km/h.
- `scripts/i24_validate.py` results schema 6: a third GEH table scores every arm against the tracked crossings divided by the coverage artifact's recommended estimator (`vs_recommended_coverage_counts`, `primary = "recommended"`, rule in `primary_rule`; observed cache version 3), and the `rmspe` block gains per-replicate values and a leave-one-out floor when the run stored per-replicate segment speeds. Re-scored (`--criteria-only`): GEH pass fractions 0.7 / 16.7 / 18.8 / 20.1% (tracked / corrected / speedcal / ramps) — the flow shortfall at the peak sections (12–13% against a 6% tolerance) is a discharge property, not a counting one. The recorded 5-min field differs from its own 15-min moving average by 33.4%, which is where the arms sit; at 15-min aggregation the arms score 25–27% against the recording's own 15.1% floor. The criterion keeps its resolution; the table is reported (`docs/I24_VALIDATION.md` §0.5).
- `scripts/i24_merge_experiment.py`: `geometry_corrected`, `geometry_corrected_lc{1,2}` and `geometry_corrected_assertive_*` variants, 15-min RMSPE and GEH-vs-recommended in the output, `--keep-dir` for lane profiling. Single seed (`artifacts/i24_merge_experiment_geometry.json`): the corrected map admits 93% instead of 85% of the ramp's demand and leaves the mean profile within 1 km/h; strategic eagerness back at 1–2 re-creates the diverge stall even with the longer pocket.
- The missing-leader hypothesis for the fitted headways is rejected: near equilibrium the gap-to-fitted-gap ratio is unimodal (1.4% beyond 2.5).
- **Schema change:** `FleetSpec.lc_strategic_ramp: float | None = None` — the SUMO `lcStrategic` written on the vTypes of vehicles whose route starts on an on-ramp (`None` = same as `lc_strategic`). One eagerness cannot serve exiting vehicles (reach the deceleration pocket early) and entering vehicles (use the acceleration lane) at once; with the single elevated value the replica's ramp traffic merged at the gore at 9–13 km/h. Route files of existing scenarios are byte-identical. Three unit tests cover the field.
- **Schema change:** `CorridorNetwork.entry_lane_shares` / `OSMNetwork.entry_lane_shares: list[float] | None = None` — the measured share of mainline entries per lane (left to right) as an upstream boundary condition; `None` keeps the round-robin insertion byte for byte. When set, `build_corridor_plan` draws each mainline vehicle's departure lane from the run's RNG (`FleetPlan.depart_lane`) and the route writer emits it. On I-24 the right lane carries 17% of vehicle-time at the entry against 34% in the left lane (the Old Hickory off-ramp has just drained it); a replica inserting a quarter of the flow into the lane the next on-ramp merges into queues that lane 1.5 km upstream of the gore. Four unit tests.
- **Schema change:** `SimSpec.lateral_resolution_m: float | None = None` — SUMO's `--lateral-resolution`; a value switches the run to the sublane lane-change model (LC_SL2015), exposed because the lane-discrete model locked the replica's merge under every lane-change parameter tried (docs/I24_VALIDATION.md §0.5). `None` keeps every existing run identical.
- `scripts/i24_merge_experiment.py` `*_entrylanes` and `*_sublane` variants (`artifacts/i24_merge_experiment_entrylanes.json`): the measured entry lane distribution on the corrected map with ramp-origin eagerness 1 gives the best single-seed 15-min error so far (24.8% against 25.9% for the original replica) and shortens the queue by half a kilometre, but the crawl at the gore itself stays at 8–11 km/h.
- Sublane model on the replica (`artifacts/i24_merge_experiment_sublane.json`): at 0.8 m lateral resolution with default lateral parameters and no internal links, all three single-seed runs gridlock (38–45% inserted, 1–10 km/h everywhere); recorded as a negative result, the option stays for a calibrated attempt. Conclusion of the round (`docs/I24_VALIDATION.md` §0.5 (i)): the merge crawl is a lane-discrete lane-change merge lock, not a map length, eagerness, gap-acceptance, cooperation or entry-distribution effect; the criteria rows are unchanged.
- The three schema fields move **every config hash**; the three goldens were regenerated with their summary statistics unchanged (`ring_sugiyama` 59e8181c0bd6, `corridor_10km_smoke` 07e450e0632a, `macro_corridor` 5d3972b6a4de) — the physics did not move, only the snapshot.

### I-24 four-arm battery under the criterion's stack detector (2026-09-05)

- `scripts/i24_validate.py` rerun on the cloud VM for all four demand arms (tracked `4cd18bf46147`, corrected `d8c6924188eb`, speedcal `009ed0e2a7c0`, ramps `d06808e7b8e1`; 20 seeds each; results schema 5 carries every registered wave detector under `waves.by_detector`), then `--criteria-only` locally with the published sweep grid: **5 PASS / 2 FAIL on each congested arm, 4 / 3 on the tracked arm.** Link-flow GEH (10.4–24.3% of link-hours under 5) and segment-speed RMSPE (33.7–35.9%; 187.8% tracked) fail everywhere. The wave-speed row passes with the profile's `stack` detector (15.7–15.9 km/h simulated, 19.9 observed; the stack finds a peak in 7–12 of 20 replicates per arm) and would fail with the standard detector (7.9–10.4 km/h), which is reported beside it in `docs/I24_VALIDATION.md` §0.4; the three arms already run on 3 Sep reproduce that run's numbers to every digit after the `FleetSpec` schema change. Fitted-ramps arm: RMSPE 34.8%, GEH 10.4%, throughput 5,574 veh/h, 96.5% inserted — one RMSPE point gained, five GEH points lost. Figures regenerated with four arms and the criterion-detector markers (`scripts/i24_validation_figures.py`, `docs/figures/i24_validation_*.png`); README, ROADMAP, dossier, paper outline, website brief and lessons updated to the four-arm count. The auto-reports under `docs/reports/i24_replica/` remain those of the 3 Sep battery (replicate trees pruned on the VM).
- Cloud: the `flowstate-sweep` VM was deleted after the results were fetched; no compute resources remain in the project.

### I-24 fundamental diagram and audit list

- `artifacts/fd_i24.json` (`scripts/fit_fd_i24.py`, 200-resample bootstrap, seed 42): per-lane triangular FD from Edie bins; congested wave speed 16.1 km/h [15.7, 16.5]; capacity and jam density recorded as coverage lower bounds. Section added to `docs/I24_DATA.md`.
- `docs/AUDIT_2026-09-03.md`: findings of a ten-lens code audit; two frontend schema-drift bugs verified (run-detail metrics and heatmap types did not match the API), the rest recorded as unverified follow-ups.

### Fixed

- Dashboard progress bars read `done`/`total` while the API's `ProgressOut` sends `completed_replicates`/`total_replicates`, so every run and sweep cell showed an empty bar; the frontend type now mirrors the API and the mock produces the same shape.
- Dashboard scenario library: pressing Run on a repo preset sent a run request without a `scenario_id` (presets from `GET /scenarios/preset` are not stored scenarios and carry none), and the merge keyed on that missing id collapsed every preset into one card. Run on a preset now stores it first (reusing a stored scenario with the same config hash), and presets already in the library show as their stored copy. The mock backend now returns the API's `PresetOut` shape, and a test drives the flow against real API shapes.
- Frontend default API key is now the API's own inline-queue default (`dev-key-change-me`), as the README already documented; it had drifted to the Compose fallback, so the no-Docker dev path (`uvicorn` + `npm run dev`) answered 401 out of the box. Compose users paste their key in Settings as before.
- Frontend `RunMetrics`/`Heatmap` types now mirror the API's `MetricsOut`/`HeatmapOut` (replicates with per-seed metrics; bin centers, not edges); CI fields are nullable as in `CIOut`; the run-detail page no longer throws on a real finished run. Mock backend updated to the same shapes.

### Engine gaps closed (audit follow-up, CLAUDE.md §3.2.4, §4.4, §4.5, §7.4, §9)

- **VSL (§4.4).** The micro tier scales every posted limit by fleet compliance against the edge's own base limit through the pure `controllers.vsl.effective_limit` (compliance-weighted mean, clamped, never above the base) and dispatches on 0.5–1.0 km gantry segments (`NetBundle.segments()` / `controllers.vsl.gantry_segments`) instead of raw edges; compliance 1.0 on generated corridors reproduces the previous outputs bit for bit. The macro tier applies `cfg.av.vsl` for the first time, capping the free-flow branch per cell through the existing interface caps (CFL from the uncapped `v_f`, conservation intact, outputs still `tier="screening"`). Both tiers record a `vsl_dispatch` provenance block in `meta.json`. 41 tests added; no config-schema change.
- **Report (§7.4).** `validation.report` groups micro runs by `config_hash`, labels each group from its `av` block, renders one metric table and replicate check per group, and adds a controller-minus-baseline contrast table (seed-paired under common random numbers, Welch otherwise, `resolved` when the interval excludes zero) with baseline/controller contour pairs per matched seed. Seeded replicates are excluded from the wave-speed criterion input; duplicate seeds within a configuration are an error. `generate_report(..., pdf=True)` renders the same Markdown to `report.pdf` through the optional `validation[pdf]` extra (fpdf2); the API gained `GET /reports/{id}/pdf` and the report job requests the PDF with a Markdown-only fallback.
- **Golden regressions and tier sanity (§9).** `tests/golden/` holds engine-produced summary statistics for `ring_sugiyama`, a 2-minute `corridor_10km` smoke run and a seeded macro corridor (config hash, seed, SUMO and package versions recorded); `test_microsim_golden.py` and `test_macrosim_golden.py` compare re-runs at rel 1e-6 (SUMO) / 1e-9 (numpy, numba), with the PR-note rule for updates in `tests/golden/README.md`. `tests/test_integration/test_tier_sanity.py` runs one seeded shock through both tiers and requires backward fronts in each. The §3.4 20-replicate wall-clock test is slow-marked and `.github/workflows/perf.yml` runs `pytest -m slow` weekly and on demand.
- **Demand fitter, GEH aggregation, criteria profiles, ring rows (§6.3, §7.1, §7.3).** `calibration.demand.fit_inflow(scenario, counts)` is the §6.3 entry point, with a microscopic adapter (`microsim.demand_adapter.make_simulate_fn`, one seeded `run_micro` per iteration) and the legacy injected-simulator form kept as `fit_inflow_profile`. `validation.metrics` gained `count_crossings`, `crossings_per_window`, `link_hour_geh` and `geh_pass_fraction`. `validation.criteria` gained a `source`-bearing profile registry (`fhwa_default`, `fhwa_tat3_2004`, `odot_vissim_2011`, `txdot_tsap_ch13`) transcribed from the verified FHWA 2004 table, the ODOT 2011 protocol and TxDOT ch. 13 — the 2019 FHWA update prescribes no GEH target, and CLAUDE.md §7.1 now says so — plus the §7.1 `sensitivity_grid` row. `validation.ring_benchmark` turns the CI ring gate's checks into functions evaluated over seeds, and `scripts/i24_validate.py --ring-seeds` feeds them into the corridor battery so the ring rows are evaluated instead of marked FAIL (results schema_version 4).
- **Onboarding and RL hook (§3.2.4, §4.5).** `microsim.scenarios.scenario_from_osm` completes the `osm_generic` pipeline: import and prune an OSM extract (file or bbox), verify the corridor chain against the compiled net with sumolib, and return a hashable `ScenarioConfig` with the versioned `corridor_10km` defaults; the YAML round-trip preserves the config hash. `MicrosimBackend.from_scenario` and `FlowStateEnv(scenario=...)` tie the Gymnasium environment to `corridor_10km.yaml` (short episodes for smoke tests), with `close()` added to the backend protocol.

### Dossier

- `docs/FLOWSTATE_DOSSIER.md`: the long-form technical and commercial dossier (how it works, why, the full evidence and validation record with tables and figures, market, revenue hypotheses, channels, implementation options, risks, appendices), written so every number traces to a committed artifact and rendered to PDF with the repository's own renderer.

### I-24 flagship sweep (ROADMAP §1.5) and the calibration follow-ups

- `artifacts/i24_sweep_summary.json`, `docs/I24_SWEEP.md`, `docs/figures/i24_sweep_dose_response.png` (`scripts/i24_sweep_figures.py`): 500 runs on the fitted arm (cloud VM, 0 failures). FollowerStopper at its literature defaults costs throughput at every cell, growing with penetration (5% / 100%: throughput −36%, travel time +82%, fuel +111%, σ_v −56%, waves halved; lane changes 1.25 → 2.32 per vehicle-km at 20%). Documented as a sensitivity battery on an unvalidated replica; the mechanism (a held gap is a moving capacity drop against capacity-bound demand) is stated and the next controller must be capacity-aware.
- Joint out-of-sample fit (`artifacts/i24_boundary_ramps_fit.json`, `scenarios/i24_replica_speedcal_ramps.yaml`): 43 evaluations; the best point moves ramp demand from Old Hickory (×0.75) to Hickory Hollow (×1.25) with a slightly larger Hickory Hollow exit share, boundary and gap acceptance unchanged; fitted hour 35.6% → 33.2%, held-out hour 42.6% → 35.6%, 96% inserted. The lane-change grid (`artifacts/i24_lanechange_fit.json`, 18 points) prefers high gap acceptance on lane shares, which the speed objective rejects; nothing adopted from it (`docs/I24_CAPACITY.md` §7–8).
- `scripts/i24_validate.py`: the `sensitivity_grid` criterion row is fed from `artifacts/i24_sweep_summary.json` when present, and `--criteria-only` re-evaluates the criteria rows of existing arm artifacts without simulating.

### Engine refinements (2026-09-03, second round)

- **Lane-change model as a calibration target.** `calibration.lanechange` computes lane-use shares, held lane changes per vehicle-km and change locations per section from any trajectories-like frame, identically for I-24 MOTION fragments (band lanes, flicker guard) and simulated runs (SUMO lane index mapped to the band convention, verified on the compiled net), with a documented objective (RMS lane-share difference + 0.1 × rate RMSPE). New `LaneChangeCalibration` artifact records parameters, observed and simulated observables on the fitted first hour and the held-out second hour, the grid and provenance. `scripts/i24_lanechange_observed.py` wrote `artifacts/i24_lanechange_observed.json` (06:30–08:30 CST, 288,842 fragments: lane shares 0.28–0.33 / 0.23–0.27 / 0.17–0.23 / 0.17–0.33 left to right by section, 1.3–3.2 lane changes per vehicle-km); `scripts/i24_fit_lanechange.py` evaluates an `lc_*` grid on the fitted arm (one seeded replicate per point). Only the smoke was run locally; the grid runs on the VM.
- **Second corridor through the same procedure (US-101, no retuning).** `scripts/calibrate_capacity.py` and `scripts/fit_demand_scale.py` are corridor-agnostic versions of the I-24 step-1 and step-2 scripts (shared pure helpers; the I-24 artifacts are reproduced by test). Applied to the US-101 replica: capacity calibration scales the population's mean T by 0.794 (1.285 → 1.020 s) to meet its FD's q_max lower bound (2,068 veh/h/lane) at a 6.8% gap-RMSE cost; the demand level fitted on the first half of the 15-minute study period is 1.25 (held-out RMSPE 31.5%). The rerun battery (`artifacts/us101_validation_calibrated.json`, 20 seeds) stays 1 PASS / 5 FAIL: speed RMSPE 36.6% → 27.9%, GEH pass fraction 56% → 22% because the level overshoots flow to stand in for the missing on-ramp merge, wave speed unchanged (site artifact). `scripts/m3_us101_validate.py` gained a `calibrated` arm, `--artifact-out` and a 150 s observed table. `docs/US101_CALIBRATED.md`.
- `scripts/i24_build_replica.py --coverage-estimator {equilibrium,gap_mixture,section_gap_mixture,recommended}` reads a gap-based coverage from `artifacts/i24_coverage.json` for the corrected arm (default unchanged, so config hashes are unchanged) and records the source in the inputs artifact.
- **Tracking-coverage estimators without the equilibrium assumption.** `calibration.coverage`: the IDM-equilibrium ratio, a random-thinning moment estimator, a maximum-likelihood geometric-gamma spacing mixture with censoring and duplicate removal, a capacity lower bound with the `max(estimate, bound)` rule, and a section-crossing variant; `synthetic_validation` recovers a known coverage on thinned synthetic lanes and pins the failure modes. `scripts/i24_coverage.py` → `artifacts/i24_coverage.json`: the recommended coverage is 0.56–0.67 over 06:30–08:30, 8–25% above the equilibrium factor, lowering the peak corrected inflow from 1,935 to 1,786 veh/h/lane — the correction the speed fit found independently. 50 new tests.
- **Wave-speed detector made explicit and benchmarked.** `validation.waves` gained a registry of named detector recipes (`standard`, `stripe`, `relative`, and a new threshold-free slant-stack estimator `stack`), a planted-stripe benchmark generator, and a benchmark measured in the tests: on congested backgrounds the standard 40 km/h detector found no backward front at any congested fraction, the relative detector none below 90% congestion, the stripe detector was biased by up to 4.4 km/h near its own threshold, and the stack recovered a planted 16 km/h within 0.1 km/h everywhere. `CriteriaProfile.wave_detector` (default `stack`) and every evaluated wave-speed row now names the recipe behind its value; `scripts/i24_validate.py` computes all four and scores the profile's (results schema 5); the report labels its row as the standard detector that `compute_metrics` uses. Artifacts written earlier keep their standard-detector rows until rerun.
- **Out-of-sample fitter for ramp levels, boundary discharge and gap acceptance** (see the step-3 entry below).

### Schema: merge lane-change parameters (config hashes change)

- `FleetSpec` gains `lc_cooperative` (SUMO `lcCooperative`, [0, 1]), `lc_assertive` (`lcAssertive`, > 0) and `lc_speed_gain` (`lcSpeedGain`, ≥ 0), written on every vType only when they differ from SUMO's default 1.0 (route files of existing scenarios stay byte-identical). Exposed for the Old Hickory merge calibration (`docs/I24_CAPACITY.md` §6); `scripts/i24_merge_experiment.py` gained cooperation, gap-acceptance and speed-gain variants and reports first-hour and held-out-hour RMSPE. **Every config hash changes** because the snapshot carries the new fields; goldens under `tests/golden/` were regenerated with unchanged physics (summary statistics identical, only the recorded hashes differ — the PR note required by `tests/golden/README.md`).

### Scientific validity: three-arm rerun on the calibrated population

- `scripts/i24_validate.py` rerun (cloud VM, 20 seeds per arm, ring rows evaluated, results schema 4): **3 PASS / 3 FAIL per arm** — ring emergence and dampening 20/20, replicates ≥ 20; link-flow GEH, speed RMSPE and wave speed still fail in all arms. Corrected arm: RMSPE 36.8% → 33.7%, fronts 8.7 → 10.4 km/h (standard) and 12.3 → 14.2 km/h (stripe, now inside 14–22 against 16.0 observed), throughput 5,266 → 5,576 veh/h. Fitted arm (`i24_replica_speedcal`): 95.5% of demand inserted, RMSPE 36.0%, throughput 5,710. The residual is spatial: the Old Hickory merge holds a standing queue at the entry; everything from 2.2 km on is within a few km/h of the recording (`docs/I24_VALIDATION.md` §0). `--reuse-runs` analyses complete replicates on disk.

### Scientific validity: capacity calibration of the I-24 fleet

- `scripts/i24_capacity_experiment.py` → `artifacts/i24_capacity_experiment.json`: the car-following population fitted on congested episodes (`idm_i24.json`, T = 1.51 s) saturates at ≈ 1,650 veh/h per lane on a straight four-lane road, below the 1,775 veh/h per lane the instrument *tracked* (the FD `q_max` lower bound); spreading insertion over three edges frees the buffer and changes nothing downstream. The replica's insertion cap (82–84%) is a capacity limit, not an insertion artifact (`docs/I24_CAPACITY.md`).
- `scripts/i24_calibrate_capacity.py` → `artifacts/idm_i24_capacity.json` (+ `.calibration.json`): FHWA Vol. III step 1. Population mean T scaled by 0.875 (1.511 → 1.322 s) so the straight-road capacity meets the tracked lower bound; covariance, other means, lane-change parameters and demand untouched; population-mean gap RMSE on 1,500 sampled episodes 5.313 → 5.294 m.
- `scripts/i24_build_replica.py` now builds both replica arms on the calibrated population: config hashes `i24_replica` 5e15ca999c19 → 17e9e80ffd83 and `i24_replica_corrected` a3efae6955bd → de82b62e4ef6. The §1.5 sweep in flight on the cloud VM runs the previous corrected hash and is documented as such.
- `scripts/i24_fit_demand_scale.py` (FHWA step 2): one scale factor on the tracked mainline and on-ramp inflows fitted to observed segment speeds over 06:30–07:30 CST only, 07:30–08:30 held out → `artifacts/demand_scale_i24.json` and `scenarios/i24_replica_speedcal.yaml` (third arm).

### Cloud compute

- The bootstrap now arms an automatic shutdown five minutes after the sweep finishes (`--no-auto-stop` to keep the VM up), after a VM idled about 40 hours on 2026-09-04/05 because the session that launched it lost connectivity.

- `scripts/gcp/`: bootstrap for a fresh Debian VM (system deps, uv, workspace, resumable sweep under nohup), a results fetcher, and a README with the exact `gcloud` commands and costs. `data/osm/i24_motion.osm` (89 KB, ODbL) is now versioned so the I-24 replica runs from a plain clone.

### Embeddable simulation

- `embed/`: a static Vite + TypeScript page (no backend, ~21 kB of script)
  that replays real engine output: 45 `ring_sugiyama` runs generated by
  `scripts/website_sim_pack.py` (18/22/26 vehicles × 0/1/2 FollowerStopper
  vehicles × switch-on from start or after 5 min × seeds 42/7/123, stored as
  uint16 binaries, 4.8 MB) plus the observed I-24 westbound field. Top-down
  ring, time-space diagram, live readouts, per-minute σ_v against the
  same-seed baseline, provenance line (SUMO version, config hash, seed),
  keyboard control, reduced-motion respect, `?embed=1` mode with a
  postMessage height for iframe hosts. Unit tests plus a data-pack integrity
  test; Dockerfile (nginx) with `embed/fly.toml`, and a root `render.yaml`
  Blueprint for a Render static site. CI gains a `web` job that typechecks,
  tests and builds both `frontend/` and `embed/`.

### Website brief

- `docs/WEBSITE_BRIEF.md`: the design prompt for the public site, and `scripts/website_hero_data.py` → `docs/website/hero_data.json` (the observed I-24 westbound speed field copied from `artifacts/i24_wb_overview.json` plus three seeded `ring_sugiyama` runs, one with the single FollowerStopper vehicle switched on at 300 s). No scenario changes; the ring config hashes are the CI-gated ones.

### Added

- **Streaming I-24 MOTION loader** (`calibration.loaders.i24motion`):
  `iter_i24_documents` decodes the INCEPTION MongoDB export one document at a
  time straight out of the zip (the 30 Nov 2022 run is a 19.5 GB JSON array of
  816,694 documents in a 5.8 GB zip; it is never extracted), and
  `convert_i24_to_parquet` writes filtered, decimated Parquet with a
  per-fragment table and provenance `meta.json`. The schema was reconciled
  against the real file and the official v1.x data documentation:
  `_id` is `{"$oid": ...}`, `x_position` is the *back*-center roadway
  coordinate in feet (MM 60 ≡ 316,800 ft), `y_position` is positive westbound
  with lane 1 (HOV) at 12–24 ft, and every document is a trajectory
  **fragment**. The loader's `x` is the front bumper on a travel-oriented axis
  so the existing bumper-to-bumper gap logic applies unchanged; the loader
  tests' expected gap changed accordingly (spacing minus *follower* length,
  not minus leader length). `scripts/i24_extract.py` runs the conversion
  (westbound: 576,511 fragments → 42.8 M rows at 5 Hz, 993 MB, 309 s).
- **Ramps and boundaries on OSM corridors** (`flowstate_core.config.RampSpec`,
  `OSMNetwork.ramps`, `OSMNetwork.boundary`; docs/CONTRACTS.md §2). A real
  corridor exchanges traffic at interchanges; the I-24 westbound testbed has
  two on-ramps and two off-ramps inside the span whose flows are a sizable
  share of the mainline, and the US-101 replica's missing merge was a
  documented structural failure. On-ramps insert their own seeded demand on
  the ramp's first edge; off-ramps divert a per-vehicle seeded fraction; ramp
  edges are kept through OSM pruning and their connectivity is checked before
  SUMO starts; `meta.json` records per-ramp planned/departed/exiting counts.
  The measured-boundary schedule now also applies to the last corridor edge
  of an OSM network, and OSM insertion uses the entry edge's real lane count.
  Integration-tested on a hand-built interchange fixture
  (`tests/test_microsim/test_microsim_osm_ramps.py`). Two SUMO facts learned
  and encoded in that fixture: a ramp must feed an auxiliary lane (a ramp
  squeezed into a same-width edge faces priority-junction gap acceptance and
  never merges against a steady stream), and hand-drawn ramps must leave at
  gore-like shallow angles (SUMO caps turning speed by curvature, so a steep
  link is an artificial 5 m/s bottleneck).
- `FleetSpec.lc_strategic` (SUMO `lcStrategic`, default 1.0 = SUMO's own
  default, written on vTypes only when changed). On the I-24 replica the
  default leaves 894 stalled 10-s samples in two hours at the Hickory Hollow
  diverge and a 29.8 km/h mean speed over the kilometre upstream of it — a
  fixed bottleneck the data does not have — against 44 samples / 84.7 km/h at
  5.0 and 3 / 86.8 at 20.0 (same seed, same demand). The replica uses 5.0.
- `FleetSpec.lc_keep_right` (SUMO `lcKeepRight`, default 1.0). The default
  encodes a keep-right obligation US freeways do not have: on the I-24
  replica it spreads vehicle-time 24/25/25/27% across the four lanes (left
  to right) but crawls at 23–27 km/h in the two right lanes through the Old
  Hickory merge while the left lanes run at 65–97 km/h — 11,535 stalled
  10-s samples in two hours. At 0 the lane shares become 32/26/22/20%
  against the observed 30/24/20/26% (all observed lanes at 30–33 km/h) and
  the stalls fall to 200 (same seed, same demand). The replica uses 0.
- **I-24 replica validation (ROADMAP §1.4)** — `scripts/i24_validate.py`,
  `artifacts/i24_validation_{tracked,corrected,observed}.json`,
  `artifacts/i24_validation_waves_relative.json`, docs/I24_VALIDATION.md,
  auto-reports under `docs/reports/i24_replica/`. Honest outcome: **1 PASS /
  5 FAIL in both demand arms**, 20 seeds each. With demand as tracked (a lower
  bound) the span free-flows (RMSPE 183%); dividing demand by the
  instrument's apparent coverage produces a stop-and-go field that looks like
  the recording (RMSPE 36.8%, mean speed 28.0 vs 33.5 km/h observed), but the
  replica inserts only 82–84% of that demand, its jams are shallower than the
  real ones, and its backward fronts run at 8.7 km/h with the standard
  detector (12.4 with the relative one) against 14.2 (16.4) observed. The
  wave-speed prediction of docs/WAVE_SPEED_DIAGNOSIS.md is therefore **not
  confirmed on the corridor**; the same fleet reaches the 14–22 km/h band on a
  ring only above ~80 veh/km per lane, a density the replica does not reach.
- **I-24 calibration artifacts.** `artifacts/idm_i24.json`: IDM population
  fitted on all 17,652 ≥ 30 s leader-follower episodes of the westbound day
  (7× NGSIM's count; holdout gap RMSE 5.29 m vs 6.44 m for NGSIM; T = 1.51 s,
  a_max = 1.06 m/s², s0 = 2.53 m). `artifacts/demand_i24.json`: mainline
  inflow per 5 min for 06:30–08:30 CST, a lower bound at the instrument's
  tracking coverage. `artifacts/i24_replica_inputs.json`: every derived input
  of the replica (geometry mapping, ramp flows, exit fractions, boundary
  schedule, coverage factors). See docs/I24_DATA.md.
- **Deferred-commitment JAD** (`controllers.jad`, parameter `commit_delay_s`,
  default 0 = the original controller; ROADMAP B4). From CRUISE, slow-in now
  starts only once a wave has been detected continuously for
  `commit_delay_s`; the deferral that a 30–60 s detection latency supplied by
  accident in 2.1.0 (docs/JAD_ORACLE_RESULTS.md) becomes an explicit design
  rule usable with a perfect sensor. Unit-tested (persistence, transient
  detections reset the clock, the full cycle still runs); the experiment is
  `scripts/jad_deferral_experiment.py` → `artifacts/jad_deferral_summary.json`.
- **Relative-threshold wave detection** (`validation.waves.detect_waves(...,
  relative_frac=f)`, ROADMAP D1): thresholds at `f × p90` of the field's
  non-empty bin speeds, so stripes inside a field that is congested
  everywhere are segmented instead of being merged into one pinned blob (the
  documented failure above ~80 veh/km, docs/WAVE_SPEED_DIAGNOSIS.md). Unit
  tests plant a −16 km/h stripe in a 22 km/h field: the absolute detector
  returns one 0 km/h blob, relative mode recovers −16 km/h. A labeled variant;
  the §7.1 criterion stays on the absolute threshold.
- **Bounded-memory trajectory capture.** `microsim.runner` now streams the
  trajectory table to Parquet in 500k-row groups during the run instead of
  holding every sampled row as Python lists until the end. A 7,800 s
  four-lane I-24 run captures ~10 M rows; the old buffer cost several GB per
  process and eight concurrent workers took a 16 GB machine down twice
  during the I-24 validation. File content is unchanged (the byte-exact
  determinism test still passes); peak memory per worker drops to well under
  1 GB. `validation.metrics.compute_metrics` and the report generator's
  contour renderer now read only the trajectory columns they use, which cuts
  their peak from ~7 GB to a few GB on the same runs.
- `calibration.fd_fit.fit_triangular_fd(n_procs=...)` refits bootstrap
  resamples in a process pool; the resample draws are made up front from the
  seeded generator in serial order and each refit is deterministic, so the
  artifact is identical to the serial path (each exact-LP quantile refit costs
  ~2 min on the 237k-bin I-24 data set).

### Changed

- **Config hashes change for every scenario**: `OSMNetwork` gained
  `boundary` and `ramps` (defaults `None` / `[]`) and `FleetSpec` gained
  `lc_strategic` (default 1.0), and `config_hash` covers the whole serialized
  config (CLAUDE.md §0.5) — the same situation as the `OracleSpec` addition
  in 2.1.0. Physics is unchanged for every existing scenario: the defaults
  reproduce SUMO's own behaviour and the route files they generate are
  byte-identical (`tests/test_microsim/test_microsim_vehicles.py::
  TestLcStrategic`). Hashes quoted in earlier documents (the gallery's
  `7529e2b0dd63` / `f8c6011feb3e`, the M3 and 2.1.0 experiments) refer to the
  pre-Phase-6 schema.
- `runs/` (12 GB of regenerable per-replicate trajectories) was deleted to
  make room for the I-24 processing; the small machine-readable result files
  the results documents cite were preserved verbatim under
  `artifacts/run_summaries/<experiment>/` (see its README).

## [2.1.0] — 2026-09-02

### Added

- **Delayed / noisy wave-detection oracle** (`OracleSpec` on `AVSpec`),
  completing CLAUDE.md §4.3's requirement that the oracle be swappable and that
  every headline JAD result also be reported under a degraded oracle. `delay_s`
  makes the controller read the traffic state as it was `delay_s` ago;
  `amplitude_noise_frac` applies seeded multiplicative error per bin. Default is
  a perfect oracle, so existing configs are unchanged.
- `docs/jad_derivation.md` — the JAD intercept-timing derivation with geometry
  that CLAUDE.md §4.3 required and that was never written.

### Changed

- **Diagnosed the US-101 wave-speed criterion failure** as a site/operating-density
  artifact rather than a calibration defect. The calibrated fleet produces a mean
  emergent backward wave speed of 14.6 km/h on a ring at 60 veh/km (71% of fronts
  inside the 14-22 km/h band) - matching the independently fitted macroscopic FD's
  w = -14.6 km/h from a completely separate estimation path. Near critical density
  fronts are slower (11.4 km/h at 40 veh/km), close to the replica's measured
  10.7 km/h. The criterion still FAILS as measured on the 640 m replica and that
  stands in the validation table; what a passing test needs is now identified.
  Also recorded: an open 10 km corridor cannot reach the instability band with
  these parameters (insertion caps density near 25 veh/km vs a 31.8 veh/km
  threshold), and threshold-based wave detection breaks down above ~80 veh/km
  where the whole field reads as jammed. See `docs/WAVE_SPEED_DIAGNOSIS.md`.

- **The "free lunch" framing is now qualified by a real-geometry check.** The
  penetration ladder was rerun on the `us101_replica` (640 m of real 5-lane
  US-101, calibrated fleet, real demand, measured downstream boundary), 6 cells
  x 20 CRN seeds. The σ_v dose-response replicates cleanly (-8.2% at 1% to
  -53.2% at 20%, every step resolved). The no-cost result does not: that
  saturated site shows a resolved throughput cost of 0.3-1.6% and a resolved
  fuel *increase* of 1.4-2.7% at 1-10% penetration, against a 2.8-5.7% saving on
  the synthetic corridor. Smoothing holds; "for free" is corridor-dependent.
  See `docs/US101_PENETRATION.md`.

- **Config hashes changed for every scenario** when `OracleSpec` was added to
  `AVSpec`: `config_hash` covers the whole serialized config (CLAUDE.md §0.5),
  so a new field with a default still changes the digest. Hashes recorded in
  artifacts produced before this change (M2, M3, the US-101 validation) refer to
  the pre-Phase-5 schema and will not reproduce against current code, though the
  *physics* is unchanged: the `corridor_10km` baseline gives bit-identical
  metrics before and after (temporal σ_v 3.3851, CI lower bound 2.8283 in both
  the pre-change `pi_retune` run and the post-change `jad_oracle` run), which is
  the intended evidence that a default `oracle` is inert.

- **JAD's M3 bimodality is explained and resolved.** The perfect oracle was the
  cause: it fires the instant any bin in the 2 km lookahead qualifies, so the AV
  completes slow-in/hold/fast-out before the front arrives and re-triggers —
  30.7 acceleration sign-reversals per run against 16.6 under a 30 s delay —
  and each abrupt fast-out can seed a secondary wave. Under a perfect oracle
  5/20 seeds end worse than the uncontrolled baseline (one goes 1 -> 11 waves)
  and the wave-count benefit is not resolved. With 30-60 s latency and +/-20%
  noise, **no seed is worse than baseline** and wave count (-3.50 [-4.71,
  -2.29]), sigma_v and fuel all improve with resolved CIs. Realistic detection
  is what makes JAD reliable here. See `docs/JAD_ORACLE_RESULTS.md`.

### Fixed

- **PI-with-saturation now implements Stern et al. (2018) Eqs. (3)–(5).** The
  M3 sweep's headline failure — `pi_saturation` gridlocking the open corridor,
  94% throughput collapse — was caused by the CLAUDE.md §4.2 *simplification*
  (`v_target = 0.75 · platoon mean`), not by the literature controller. That
  factor does not appear in the paper: its target is the AV's own ≈38 s mean
  speed plus a bounded, non-negative gap-scheduled catch-up term, which cannot
  ratchet downward. Re-run on the same scenario and seeds, the faithful
  controller calms the corridor — σ_v −29.7% [−1.35, −0.66], waves −41.6%,
  fuel −2.9%, no resolved throughput cost (n = 20, paired) — and still dampens
  the ring (σ_v 2.07 → 0.46 m/s). The simplification is retained as
  `controllers.pi_meanfrac`, clearly labeled, so the M3 result stays
  reproducible. CLAUDE.md §4.2 corrected against the source per its own §13.
  See `docs/PI_CONTROLLER_FIX.md` and `artifacts/pi_retune_summary.json`.
- `pi_saturation`'s output range is `[0, U + v_catch]`, not `[0, U]`; the
  property test and docs/CONTRACTS.md §8 record the exception with its citation.

## [2.0.0] — 2026-08-29

Complete rewrite of FlowState (M0–M5, developed and hardened 2026-08-29).
v2 replaces the v1 single-engine LWR study with a two-tier, calibrated,
honestly-validated corridor platform.

### Two-tier simulation engine (ADR-1)

- **Microscopic tier (primary):** Eclipse SUMO 1.27.1 + IDM/EIDM via
  libsumo, `microsim` package. String instability is **emergent** — waves
  grow from calibrated car-following dynamics and insertion jitter, never
  from hand-seeded shocks (seeded runs are permitted but labeled
  `seeded=True` everywhere).
- **Macroscopic tier (screening):** the v1 LWR/CTM Godunov engine ported
  into `macrosim` with a full test battery (Riemann exactness, mass
  conservation, CFL guards) and Numba compilation. It is repurposed for fast
  parameter screening and future state estimation; it makes **no** claims
  about phantom-jam formation or dissipation (LWR is string-stable by
  construction). Every macro artifact carries `tier="screening"` and can
  never back a validation report.

### Ring gate (M1)

- Permanent CI integration tests on the 230 m / 22-vehicle Sugiyama ring:
  stop-and-go waves **emerge** without any seeded perturbation (Sugiyama et
  al. 2008), and a single FollowerStopper AV dampens them (Stern et al.
  2018). These gates must stay green for any change to merge.
- Interpretable controller library (`controllers`): FollowerStopper,
  PI-with-saturation, Jam-Absorption Driving, and a VSL segment controller,
  all pure functions with literature-default parameters, plus a Gymnasium
  environment hook for future RL work (ADR-2: RL deferred).

### NGSIM calibration (M2)

- Real-data calibration pipeline (`calibration`) fed by the public NGSIM
  US-101 Socrata dump (provenance-hashed): triangular fundamental-diagram
  fit with bootstrap CIs and an IDM population fit from reconstructed
  car-following episodes with held-out gap RMSE (docs/M2_RESULTS.md).
- Loaders for NGSIM, PeMS, highD and I-24 MOTION formats; demand fitting
  for corridor scenarios.

### 540-run sweep (M3)

- Penetration × compliance × controller battery on the synthetic
  `corridor_10km` EIDM scenario: 27 cells × 20 common-random-number
  replicates = **540 emergent micro runs**, aggregated as mean ± 95% t CIs
  (docs/M3_RESULTS.md, `artifacts/m3_sweep_summary.json`). Standard metrics
  only: throughput, mean travel time, σ_v, fuel proxy, wave count/amplitude.

### US-101 validation with honest criteria (M3)

- `us101_replica` validated against the real NGSIM US-101 recording with
  FHWA-style acceptance criteria (GEH, segment-speed RMSPE, wave speed).
  Headline stated honestly: imposing the measured downstream boundary
  improves RMSPE from 72.8% to 36.6% and produces backward-propagating
  waves in all 20 replicates, but the replica **fails** the acceptance
  criteria for documented structural reasons (640 m site, missing on-ramp
  merge, IDM discharge behavior) — see docs/M3_US101_VALIDATION.md. No
  validated-corridor claim is made.
- End-to-end auto-report generation (`validation.report`), which refuses
  macro-only run sets.

### Product layer (M4)

- FastAPI service + RQ/Redis job queue + SQLite (WAL) metadata store
  (`api`): scenarios, runs, sweeps, calibrations, reports — no endpoint
  executes a simulation synchronously; every response carries
  `config_hash`. Single API-key auth.
- Mission-control dashboard (React/Vite) served single-origin by the API;
  one-command Docker deploy (`docker compose up -d --build`) with the same
  image for API and workers.

### Hardening and release (M5, this release)

- Load test: 10 concurrent macro sweep jobs plus a micro (SUMO) sweep
  through the full Docker stack with 2 workers — 42/42 runs done, zero
  failed, all 42 metrics endpoints HTTP 200, `/healthz` p95 under load
  ≤ 11.9 ms against a 500 ms budget. The run's own JSON output is committed
  as `artifacts/m5_load_test.json`; machine spec, per-phase tables and
  provenance for the numbers the JSON does not carry are in
  docs/M5_LOAD_TEST.md (`scripts/m5_load_test.py`).
- `pyarrow` constrained to `>=18,!=24.0.0` across the workspace. Pinning to
  24.0.0 (the version matching libsumo 1.27.1's bundled libarrow, which
  silences libsumo's import-time mismatch warning) was tried and **rejected
  with evidence**: on macOS the two identically-named `libarrow.2400.dylib`
  copies interact fatally — parquet writes after `import libsumo`
  intermittently livelock (hard spin in mimalloc's
  `mi_bitmap_clear_once_set`; reproduced in `tests/test_microsim`, which
  hung >50 min, and bisected to the `pyarrow.Table.from_pandas`/write in
  `microsim.runner`; the same file passes in 3.3 s on pyarrow 25.0.1).
  The cosmetic version-mismatch warning therefore stays; the real
  filesystem-factory-registry clash remains handled by the parquet-path
  shim in `tests/test_microsim/conftest.py` (verified still required on
  25.0.1), and production code writes parquet through open file objects and
  is unaffected on either version.
- Version 2.0.0 across the workspace (root + all packages).

### Retired from v1 (CLAUDE.md §12 — do not reintroduce)

- Phantom-jam formation/dissipation claims from LWR; the ">15% AV
  penetration → human gap exploitation" artifact; the non-standard
  "Efficient Frontier score" as a headline metric; consumer nav-app
  advisory-push features (Waze CIFS cannot carry speed advisories; Google
  Routes speed data is read-only); per-step unseeded compliance coin-flips;
  unseeded `np.random` calls; the v1 Flask endpoints.

## [1.0-legacy] — superseded

FlowState v1 — controlled dissipation of *seeded* shocks in a first-order
LWR model, with a Flask service and the "Efficient Frontier" score. Kept as
motivated preliminary work at
[abc000cool/FlowState](https://github.com/abc000cool/FlowState). Its engine
survives in v2 as the ported, tested `macrosim` screening tier, and its
hard-coded fundamental diagram as the labeled-uncalibrated
`v1_legacy` FD preset (`macrosim.fundamental.v1_legacy_fd`).
