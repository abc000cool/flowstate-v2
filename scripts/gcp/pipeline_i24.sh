#!/bin/bash
# I-24 compute pipeline for one cloud VM: zipper-merge calibration, the FHWA
# sequence on the corrected map, the 20-seed batteries, the heavy arm and the
# headway-cap sweep. Resumable (stage markers under logs/).
#
# Stop and result guarantees (revised after the 2026-09-06 run, see README):
#   * the results archive lives in $HOME (never /tmp, which Debian clears at
#     boot) and is rebuilt atomically after EVERY stage, so whatever stops the
#     machine — the EXIT trap, the boot-time hard cap, a systemd stop — leaves the
#     completed stages fetchable; with PIPELINE_BUCKET set it is also copied to
#     that bucket after every stage, so nothing depends on the VM being up;
#   * a SIGTERM (systemd stop at shutdown) runs the EXIT trap too;
#   * the EXIT trap powers the machine off (or deletes the instance when
#     PIPELINE_SELF_DELETE=1 and the archive reached the bucket).
#
# Usage on the VM (from the repo root, under systemd-run so it survives logout):
#   scripts/gcp/pipeline_i24.sh [--procs N] [--no-shutdown] [--quick] [--stages "name name ..."]
# --stages runs only the named stages (the others are skipped as not selected); the
# committed artifacts and scenarios stand in for the skipped ones.
set -u
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
PROCS=$(( $(nproc) - 2 )); [ "$PROCS" -lt 1 ] && PROCS=1
SHUTDOWN=1
QUICK=0
ARCHIVE="$HOME/final.tgz"
BUCKET="${PIPELINE_BUCKET:-}"
SELF_DELETE="${PIPELINE_SELF_DELETE:-0}"
STAGES=""
DIAG_SEED=677105600768189526   # the seed the mndot_weave_seed5 stage maps (VM L default)
DIAG_REPS=5                    # its spawn index + 1: the battery runs the first DIAG_REPS replicates
DIAG_WEAVE_PARAMS=""            # e.g. exit_prepare=1.0[,k=v]: the map runs a copy of the weave scenario with these weave_params
while [ $# -gt 0 ]; do
  case "$1" in
    --procs) PROCS="$2"; shift 2 ;;
    --no-shutdown) SHUTDOWN=0; shift ;;
    --quick) QUICK=1; shift ;;
    --stages) STAGES="$2"; shift 2 ;;
    --diag-seed) DIAG_SEED="$2"; shift 2 ;;
    --diag-reps) DIAG_REPS="$2"; shift 2 ;;
    --diag-weave-params) DIAG_WEAVE_PARAMS="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
mkdir -p logs
LOG=logs/pipeline.log
say() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
UPLOADED=0
make_archive() {  # make_archive light|full — atomic replace of $ARCHIVE, then the optional bucket copy
  local mode="${1:-light}" extra=""
  if [ "$mode" = full ]; then
    extra=$(for d in runs/i24_validation_zip/*/*/ runs/i24_validation/speedcal_heavy/*/; do ls -d "$d"*/ 2>/dev/null | sort | head -1; done)
    [ -f runs/i24_validation_zip/ring/ring_benchmark.json ] && extra="runs/i24_validation_zip/ring/ring_benchmark.json $extra"
  fi
  # per-run metrics of the cap sweep ride along in every archive: the sweep is resumable from them
  extra="$extra $(ls runs/i24_cap_sweep/*/*/metrics.json 2>/dev/null | tr '\n' ' ')"
  # the penetration sweep's per-run metrics and manifest (the summary is built from them by
  # scripts/i24_penetration_analyze.py; the 2026-09-18 run lost 6.5 h of them to this omission)
  extra="$extra $(ls runs/i24_sweep/*/*/*/metrics.json runs/i24_sweep/MANIFEST.json 2>/dev/null | tr '\n' ' ')"
  # regenerated reports and the episode-position sidecar (data/, gitignored) ride along too
  [ -d docs/reports ] && extra="$extra docs/reports"
  # onboarded corridors: per-seed metrics/scores of every run, first-seed trajectories only, sweep metrics + summaries
  # run trees are <root>/<cell-or-arm>/<config hash>/<seed>/ — four levels below runs/mndot_*
  # meta.json rides along too (2026-09-24): the meter and weave counters live there, and the
  # ALINEA round's archive carried metrics.json only, so the share of unstoppable passes was lost
  extra="$extra $(ls runs/i24_strat_sweep/*/*/*/metrics.json runs/i24_strat_sweep/*/*/*/meta.json runs/i24_strat_sweep/MANIFEST.json runs/i24_strat_sweep/analysis.json 2>/dev/null | tr '\n' ' ')"
  # the per-vehicle table (vehicles.parquet, 2026-09-25, WP-69: origin, destination, give-ups; ~2 MB a run) rides along too
  extra="$extra $(ls runs/mndot_*/*/*/*/metrics.json runs/mndot_*/*/*/*/meta.json runs/mndot_*/*/*/*/observed_scores.json runs/mndot_*/*/*/*/vehicles.parquet runs/mndot_*_sweep/MANIFEST.json runs/mndot_*_sweep/analysis.json 2>/dev/null | tr '\n' ' ')"
  [ -f data/i24motion/processed/i24_wb_episode_positions.json ] && extra="$extra data/i24motion/processed/i24_wb_episode_positions.json"
  # the per-change table of the lane-change gap stage (WP-77; data/ is gitignored, a few MB)
  [ -f data/i24motion/processed/i24_wb_lane_change_gaps.parquet ] && extra="$extra data/i24motion/processed/i24_wb_lane_change_gaps.parquet"
  # the critical-gap stage's lookback samples and driver table (WP-78; gitignored; one row per sampled instant / per change)
  for f in data/i24motion/processed/i24_wb_gap_sequences.parquet data/i24motion/processed/i24_wb_critical_gap_drivers.parquet; do
    [ -f "$f" ] && extra="$extra $f"
  done
  # the US-101 lane-change stage's per-run records and the sweep manifest (WP-81; <cell>/<hash>/<seed>/, three levels;
  # the artifact rebuilds from them with scripts/us101_lane_changes.py --analyze-only, no trajectory read)
  extra="$extra $(ls runs/us101_penetration/*/*/*/lane_changes.json runs/us101_penetration/MANIFEST.json 2>/dev/null | tr '\n' ' ')"
  # shellcheck disable=SC2086
  tar czf "$ARCHIVE.part" --exclude=net artifacts/*.json scenarios/*.yaml logs $extra 2>/dev/null \
    || tar czf "$ARCHIVE.part" artifacts/*.json scenarios/*.yaml logs 2>/dev/null || { rm -f "$ARCHIVE.part"; return 1; }
  mv -f "$ARCHIVE.part" "$ARCHIVE"
  ls -la "$ARCHIVE" | awk -v m="$mode" '{print "archive (" m "):", $5, "bytes"}' | tee -a "$LOG"
  if [ -n "$BUCKET" ]; then
    if gcloud storage cp "$ARCHIVE" "$BUCKET/final.tgz" >>"$LOG" 2>&1; then UPLOADED=1; say "archive copied to $BUCKET/final.tgz"; else UPLOADED=0; say "bucket copy FAILED (see $LOG)"; fi
  fi
}
self_delete() {  # only meaningful with the compute-rw scope (launch_i24_pipeline.sh --bucket)
  local zone
  zone=$(curl -s -H Metadata-Flavor:Google http://metadata.google.internal/computeMetadata/v1/instance/zone | awk -F/ '{print $NF}')
  say "deleting this instance ($(hostname), $zone)"
  gcloud compute instances delete "$(hostname)" --zone "$zone" --quiet >>"$LOG" 2>&1
}
finish() {
  rc=$?
  say "PIPELINE_EXIT rc=$rc"
  echo "rc=$rc $(date -u +%FT%TZ)" > logs/PIPELINE_EXIT
  # guest-side evidence of WHY the machine is going down rides along (2026-09-24: an instance
  # was deleted mid-sweep and nothing on the laptop side could say by whom)
  { sudo tail -n 50 /var/log/idle-guard.log 2>/dev/null; echo "--- journal"; sudo journalctl -n 120 --no-pager 2>/dev/null; echo "--- uptime $(uptime)"; } > logs/guest_exit.log 2>&1 || true
  make_archive light   # seconds: survives a systemd stop window
  make_archive full    # minutes: the first-seed replicates for the figures
  if [ "$SHUTDOWN" -eq 1 ]; then
    if [ "$SELF_DELETE" = 1 ] && [ "$UPLOADED" = 1 ]; then
      self_delete || { say "self-delete failed; powering off in 3 minutes"; sudo shutdown -h +3 "pipeline finished rc=$rc" || true; }
    else
      say "powering off in 3 minutes (EXIT trap); cancel with: sudo shutdown -c"
      sudo shutdown -h +3 "pipeline finished rc=$rc" || true
    fi
  fi
}
trap finish EXIT
trap 'say "SIGTERM received (system stop?)"; exit 143' TERM
trap 'exit 130' INT
stage() {  # stage <name> <command...>: skip when logs/<name>.done exists; archive after every stage
  local name="$1"; shift
  if [ -n "$STAGES" ] && ! echo " $STAGES " | grep -q " $name "; then say "stage $name: not selected, skipped"; return 0; fi
  if [ -f "logs/$name.done" ]; then say "stage $name: done earlier, skipped"; return 0; fi
  say "stage $name: start"
  local t0=$SECONDS
  if "$@" >> "logs/$name.log" 2>&1; then
    echo "$(date -u +%FT%TZ) $((SECONDS - t0)) s" > "logs/$name.done"
    say "stage $name: OK ($((SECONDS - t0)) s)"
    make_archive light
  else
    say "stage $name: FAILED rc=$? ($((SECONDS - t0)) s) — see logs/$name.log"
    make_archive light
    return 1
  fi
}
RUN="uv run --no-sync python"
REPS=20; RING=20
if [ "$QUICK" -eq 1 ]; then REPS=1; RING=2; fi
say "pipeline start: procs=$PROCS quick=$QUICK stages=${STAGES:-all} commit=$(git rev-parse --short HEAD) archive=$ARCHIVE bucket=${BUCKET:-none} self_delete=$SELF_DELETE"

# 0. Scripted late-merge probe (RampSpec.merge = "scripted"; opt-in: --stages "merge_scripted"),
#    single seed each, against the reference arm's inserted / Old Hickory / 15-min / GEH numbers.
if echo " $STAGES " | grep -q " merge_scripted "; then
  stage merge_scripted $RUN scripts/i24_merge_experiment.py \
    --variants geometry_corrected_ramplc1_entrylanes \
               geometry_corrected_ramplc1_entrylanes_scripted \
               geometry_corrected_ramplc1_entrylanes_scripted_court2 \
               geometry_corrected_ramplc1_entrylanes_scripted_accept0.3_court2 \
               geometry_corrected_ramplc1_entrylanes_scripted_force1_within150_court2 \
               geometry_corrected_ramplc1_entrylanes_scripted_force1_within150_accept0.3_court4 \
    --procs "$PROCS" --out artifacts/i24_merge_experiment_scripted.json || exit 1
fi

# 0b. Entry lane shares in flow units instead of vehicle-time (docs/MERGE_ROUND6_PLAN.md
#     §2.1, candidate 1; opt-in: --stages "merge_entryflow"), single seed each, against the
#     reference arm's entry-lane (vehicle-time) numbers. Needs artifacts/i24_lane_profile.json
#     with flow_share rows (scripts/i24_lane_profile.py).
if echo " $STAGES " | grep -q " merge_entryflow "; then
  stage merge_entryflow $RUN scripts/i24_merge_experiment.py \
    --variants geometry_corrected_ramplc1_entrylanes \
               geometry_corrected_ramplc1_entryflow \
               geometry_corrected_ramplc1_entryflow_heavy \
    --procs "$PROCS" --out artifacts/i24_merge_experiment_entryflow.json || exit 1
fi

# 0b. Round of 2026-09-17 (opt-in stages): the "flow" scenario family (corrected map, ramp-origin
#     eagerness 1, entry lane FLOW shares, the canonical lane-change merge) through the FHWA
#     sequence and its 20-seed batteries, and a re-run of the canonical arms so the published
#     metrics (throughput, travel time, sigma_v, fuel, waves) carry the corrected definitions
#     (warm-up discarded, median travel-time span; CHANGELOG 2026-09-17). Criteria rows must
#     reproduce to the digit (same config hashes, same seeds).
if echo " $STAGES " | grep -q " build_flow "; then
  stage build_flow $RUN scripts/i24_build_replica.py --suffix flow --osm corrected \
    --lc-strategic 5 --lc-strategic-ramp 1 --entry-lanes observed_flow || exit 1
fi
if echo " $STAGES " | grep -q " demand_flow "; then
  stage demand_flow $RUN scripts/i24_fit_demand_scale.py --base corrected \
    --base-yaml scenarios/i24_replica_flow_corrected.yaml --procs "$PROCS" --write-scenario \
    --out artifacts/demand_scale_i24_flow.json --scenario-out scenarios/i24_replica_flow_speedcal.yaml \
    --name i24_replica_flow_speedcal || exit 1
fi
if echo " $STAGES " | grep -q " ramps_flow "; then
  stage ramps_flow $RUN scripts/i24_fit_boundary_ramps.py --base-yaml scenarios/i24_replica_flow_speedcal.yaml \
    --procs "$PROCS" --write-scenario --out artifacts/i24_boundary_ramps_fit_flow.json \
    --scenario-out scenarios/i24_replica_flow_speedcal_ramps.yaml --name i24_replica_flow_speedcal_ramps || exit 1
fi
if echo " $STAGES " | grep -q " battery_flow "; then
  # the three congested arms of the family (no tracked, no heavy: both were negative in every family)
  stage battery_flow bash -c "for arm in corrected speedcal ramps; do $RUN scripts/i24_validate.py --family flow --arms \$arm --replicates $REPS --procs $PROCS --analysis-procs 8 --ring-seeds $RING || exit 1; done" || exit 1
  stage prune_flow bash -c 'for a in runs/i24_validation_flow/*/; do for h in "$a"*/; do [ -d "$h" ] || continue; first=$(ls -d "$h"*/ 2>/dev/null | sort | head -1); for r in "$h"*/; do [ "$r" = "$first" ] && continue; rm -f "$r/trajectories.parquet"; done; done; done; du -sh runs/i24_validation_flow' || true
fi
if echo " $STAGES " | grep -q " battery_canonical "; then
  # the five canonical arms, one at a time (the archive grows after each), then pruned
  stage battery_canonical bash -c "for arm in tracked corrected speedcal ramps speedcal_heavy; do $RUN scripts/i24_validate.py --arms \$arm --replicates $REPS --procs $PROCS --analysis-procs 8 --ring-seeds $RING || exit 1; done" || exit 1
  # reports from the full batteries, BEFORE the prune below removes the replicates' trajectories
  if echo " $STAGES " | grep -q " reports_0917 "; then stage reports_0917 $RUN scripts/i24_report.py || exit 1; fi
  stage prune_canonical bash -c 'for a in runs/i24_validation/*/; do for h in "$a"*/; do [ -d "$h" ] || continue; first=$(ls -d "$h"*/ 2>/dev/null | sort | head -1); for r in "$h"*/; do [ "$r" = "$first" ] && continue; rm -f "$r/trajectories.parquet"; done; done; done; du -sh runs/i24_validation' || true
fi
if echo " $STAGES " | grep -q " rescore_flow "; then
  stage rescore_flow bash -c "$RUN scripts/i24_validate.py --family flow --criteria-only --arms all --ring-seeds 0" || true
fi
if echo " $STAGES " | grep -q " rescore_canonical "; then
  stage rescore_canonical bash -c "$RUN scripts/i24_validate.py --criteria-only --arms all --ring-seeds 0 && $RUN scripts/i24_validate.py --criteria-only --arms speedcal_heavy --ring-seeds 0" || true
fi

if echo " $STAGES " | grep -q " battery_us101 "; then
  # US-101 replica battery under the corrected metric definitions (needs data/ngsim on the VM)
  stage battery_us101 bash -c "M3_PROCS=$PROCS $RUN scripts/m3_us101_validate.py --arms with_boundary calibrated --replicates $REPS --artifact-out artifacts/us101_validation_calibrated.json" || exit 1
  if echo " $STAGES " | grep -q " reports_us101 "; then stage reports_us101 $RUN scripts/m3_us101_report.py || exit 1; fi
  stage prune_us101 bash -c 'for h in runs/m3_us101/*/*/; do [ -d "$h" ] || continue; first=$(ls -d "$h"*/ 2>/dev/null | sort | head -1); for r in "$h"*/; do [ "$r" = "$first" ] && continue; rm -f "$r/trajectories.parquet"; done; done; du -sh runs/m3_us101' || true
fi

if echo " $STAGES " | grep -q " probe_ohlevel "; then
  # Candidate 3 of docs/MERGE_ROUND6_PLAN.md: the Old Hickory demand level on the flow family's
  # fitted arm, single seed; the 1.0 point is the arm itself (flow_speedcal).
  stage probe_ohlevel $RUN scripts/i24_merge_experiment.py --base scenarios/i24_replica_flow_speedcal.yaml \
    --variants flow_speedcal flow_speedcal_oh0.55 flow_speedcal_oh0.75 flow_speedcal_oh1.25 flow_speedcal_oh1.6 \
    --procs "$PROCS" --out artifacts/i24_merge_experiment_ohlevel.json || exit 1
fi

if echo " $STAGES " | grep -q " probe_heavylanes "; then
  # Candidate 4 of docs/MERGE_ROUND6_PLAN.md: uniform against lane-placed heavy population on the
  # canonical fitted arm, single seed (the lane profile of the kept run dirs gives the per-lane decider).
  stage probe_heavylanes $RUN scripts/i24_merge_experiment.py \
    --variants as_is as_is_heavy as_is_heavylanes --procs "$PROCS" --out artifacts/i24_merge_experiment_heavylanes.json || exit 1
fi

if echo " $STAGES " | grep -q " sweep_0917 "; then
  # The 500-run penetration x compliance battery on the fitted arm, re-run so its summary carries
  # the corrected metric definitions (CHANGELOG 2026-09-17); cells resume on disk, run dirs pruned after.
  stage sweep_0917 $RUN scripts/i24_penetration_sweep.py --scenario i24_replica_speedcal --procs "$PROCS" --replicates "$REPS" || exit 1
  # the summary artifact is built from the per-run metrics by the analysis script, on the VM
  stage analyze_sweep $RUN scripts/i24_penetration_analyze.py || exit 1
  stage prune_sweep bash -c 'find runs/i24_sweep -name trajectories.parquet -delete 2>/dev/null; du -sh runs/i24_sweep' || true
fi

# 0c. The discharge-capacity question (docs/I24_VALIDATION.md 0.11; opt-in stages): a
#     sub-corridor IDM population fitted on the Old Hickory merge zone only, its closed-form
#     capacity against the corridor-wide populations, and a single-seed probe of the fitted
#     arm driven by it. (The report stages live beside their batteries above, before the prune.)
if echo " $STAGES " | grep -q " merge_positions "; then
  stage merge_positions $RUN scripts/i24_extract_episodes.py --positions || exit 1
fi
if echo " $STAGES " | grep -q " merge_fit "; then
  stage merge_fit $RUN scripts/fit_idm_i24.py --x-range 750 2500 --tag merge --procs "$PROCS" || exit 1
fi
if echo " $STAGES " | grep -q " merge_eq "; then
  stage merge_eq $RUN scripts/i24_calibrate_capacity.py --equilibrium || exit 1
fi
if echo " $STAGES " | grep -q " probe_mergefleet "; then
  stage probe_mergefleet $RUN scripts/i24_merge_experiment.py \
    --variants as_is as_is_fleetmerge --procs "$PROCS" --out artifacts/i24_merge_experiment_mergefleet.json || exit 1
fi

# 1. Zipper merged-lane negotiation: the junction time gap, single seed each.
stage jm_sweep $RUN scripts/i24_merge_experiment.py \
  --variants geometry_corrected_ramplc1_entrylanes_zipper_jm0.5 \
             geometry_corrected_ramplc1_entrylanes_zipper_jm0.75 \
             geometry_corrected_ramplc1_entrylanes_zipper_jm1 \
             geometry_corrected_ramplc1_entrylanes_zipper_jm1.5 \
  --procs 4 --out artifacts/i24_merge_experiment_zipper_jm.json || exit 1
JM=$($RUN - <<'PY'
import json
d = json.load(open("artifacts/i24_merge_experiment_zipper_jm.json"))
rows = d["variants"] if isinstance(d, dict) and "variants" in d else d
best = min(rows, key=lambda r: r["rmspe_train"])  # fit hour only; the held-out hour is reported
print(best["variant"].rsplit("_jm", 1)[1])
PY
)
say "jm_timegap chosen on the fit hour: $JM s"
echo "$JM" > logs/jm_choice.txt

# 2. Scenario family "zip": corrected map, ramp-origin eagerness 1, measured entry lanes, zipper merge.
stage build_zip $RUN scripts/i24_build_replica.py --suffix zip --osm corrected \
  --lc-strategic 5 --lc-strategic-ramp 1 --entry-lanes observed --merge zipper --jm-timegap "$JM" || exit 1

# 3. FHWA step 2: demand level on the corrected profile (fit hour, held-out hour reported).
stage demand_zip $RUN scripts/i24_fit_demand_scale.py --base corrected \
  --base-yaml scenarios/i24_replica_zip_corrected.yaml --procs "$PROCS" --write-scenario \
  --out artifacts/demand_scale_i24_zip.json --scenario-out scenarios/i24_replica_zip_speedcal.yaml \
  --name i24_replica_zip_speedcal || exit 1

# 4. FHWA step 3: ramp levels, exit share, boundary, gap acceptance (out of sample).
stage ramps_zip $RUN scripts/i24_fit_boundary_ramps.py --base-yaml scenarios/i24_replica_zip_speedcal.yaml \
  --procs "$PROCS" --write-scenario --out artifacts/i24_boundary_ramps_fit_zip.json \
  --scenario-out scenarios/i24_replica_zip_speedcal_ramps.yaml --name i24_replica_zip_speedcal_ramps || exit 1

# 5. Heavy arm of the zip family (the canonical heavy arm is committed).
stage heavy_zip $RUN scripts/i24_add_heavy_arm.py --scenario scenarios/i24_replica_zip_speedcal.yaml \
  --out scenarios/i24_replica_zip_speedcal_heavy.yaml || exit 1

# 6. Batteries: the zip family (5 arms) and the canonical heavy arm, 20 seeds each.
stage battery_zip $RUN scripts/i24_validate.py --family zip --arms all --replicates "$REPS" \
  --procs "$PROCS" --analysis-procs 8 --ring-seeds "$RING" || exit 1
stage prune_zip bash -c 'for a in runs/i24_validation_zip/*/; do for h in "$a"*/; do [ -d "$h" ] || continue; first=$(ls -d "$h"*/ 2>/dev/null | sort | head -1); for r in "$h"*/; do [ "$r" = "$first" ] && continue; rm -f "$r/trajectories.parquet"; done; done; done; du -sh runs/i24_validation_zip' || true
stage battery_heavy $RUN scripts/i24_validate.py --arms speedcal_heavy --replicates "$REPS" \
  --procs "$PROCS" --analysis-procs 8 --ring-seeds "$RING" || exit 1
stage prune_heavy bash -c 'h=$(ls -d runs/i24_validation/speedcal_heavy/*/ | head -1); first=$(ls -d "$h"*/ | sort | head -1); for r in "$h"*/; do [ "$r" = "$first" ] && continue; rm -f "$r/trajectories.parquet"; done; du -sh runs/i24_validation' || true

# 6b. The three zip-family arms whose batteries were lost with the first VM (2026-09-06):
#     rerun on the committed, hash-checked scenario files; ring benchmark on every artifact.
stage battery_lost bash -c "for arm in tracked speedcal speedcal_heavy; do $RUN scripts/i24_validate.py --family zip --arms \$arm --replicates $REPS --procs $PROCS --analysis-procs 8 --ring-seeds $RING || exit 1; done" || exit 1
stage prune_lost bash -c 'for a in runs/i24_validation_zip/*/; do for h in "$a"*/; do [ -d "$h" ] || continue; first=$(ls -d "$h"*/ 2>/dev/null | sort | head -1); for r in "$h"*/; do [ "$r" = "$first" ] && continue; rm -f "$r/trajectories.parquet"; done; done; done; du -sh runs/i24_validation_zip' || true

# 7. Headway-cap sweep of the capacity-aware FollowerStopper on the canonical fitted arm.
stage cap_sweep $RUN scripts/i24_cap_sweep.py --procs "$PROCS" --replicates "$REPS" || say "cap sweep failed; continuing"

# 8. Re-score the criteria rows with the published sweep grid.
stage rescore bash -c "$RUN scripts/i24_validate.py --family zip --criteria-only --arms all --ring-seeds 0 && $RUN scripts/i24_validate.py --criteria-only --arms speedcal_heavy --ring-seeds 0" || true

# 10. Onboarded corridors (opt-in; generic scripts, no corridor names in code). The MnDOT I-94 WB
#     St. Paul round (2026-09-23): 20-seed baseline scored against the detector observations, then
#     a small controller/strategy sweep with the baseline cell; trajectories pruned to the first seed.
MNDOT=mndot_i94_wb_stpaul
stage battery_mndot $RUN scripts/corridor_battery.py --scenario scenarios/$MNDOT.yaml \
  --observations data/mndot/$MNDOT/observations.json --replicates "$REPS" --procs "$PROCS" \
  --out runs/$MNDOT/baseline --artifact artifacts/validation_$MNDOT.json --report-dir docs/reports/$MNDOT \
  --criteria-profile fhwa_tat3_2004 || say "battery_mndot failed; continuing"
#     Sweep grid (12 cells × 20 seeds): baseline, VSL-only, ALINEA-only, then FollowerStopper at
#     5/10/20 % (full compliance) under none / vsl / alinea. ALINEA target = the corridor FD's
#     critical density per lane (artifacts/fd_mndot_i94_wb_stpaul.json, rho_c 0.0199 veh/m).
#     Throughput at S97 (x = 11 027 m on the chain); analysed span S1063..S97 (1 110..11 027 m).
stage sweep_mndot $RUN scripts/corridor_sweep.py --scenario scenarios/$MNDOT.yaml \
  --penetration 0.05 0.10 0.20 --compliance 1.0 --controllers follower_stopper \
  --strategies none vsl alinea --rho-target-veh-km 19.9 --x-ref 11027 --span 1110 11027 \
  --replicates "$REPS" --procs "$PROCS" \
  --out runs/${MNDOT}_sweep --summary artifacts/sweep_${MNDOT}_summary.json || say "sweep_mndot failed; continuing"

# 10b. Minnesota driver population (2026-09-24): the I-24 episode-fitted population scaled to the
#     I-94 WB St. Paul fundamental diagram's capacity (q_max bootstrap lower bound,
#     artifacts/fd_mndot_i94_wb_stpaul.json) by the US-101 procedure (docs/US101_CALIBRATED.md;
#     scripts/calibrate_capacity.py), straight 3-lane corridor, default grid and seeds. The episode
#     cost rows need data/i24motion (shipped only with --data-set i24). Then the 20-seed battery with
#     that population on a scenario copy that differs only in the fleet artifact and the name.
stage mndot_population $RUN scripts/calibrate_capacity.py --source artifacts/idm_i24.json \
  --fd artifacts/fd_mndot_i94_wb_stpaul.json --lanes 3 --base-scenario scenarios/$MNDOT.yaml \
  $( [ -f data/i24motion/processed/i24_wb_episodes.pkl ] && echo "--episodes data/i24motion/processed/i24_wb_episodes.pkl" ) \
  --out artifacts/idm_${MNDOT}_capacity.json --procs "$PROCS" || say "mndot_population failed; continuing"
stage battery_mndot_mnpop bash -c "sed -e 's#^name: $MNDOT\$#name: ${MNDOT}_mnpop#' \
    -e 's#artifacts/idm_i24_capacity.json#artifacts/idm_${MNDOT}_capacity.json#' scenarios/$MNDOT.yaml \
    > scenarios/${MNDOT}_mnpop.yaml && $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_mnpop.yaml \
  --observations data/mndot/$MNDOT/observations.json --replicates $REPS --procs $PROCS \
  --out runs/${MNDOT}_mnpop/baseline --artifact artifacts/validation_${MNDOT}_mnpop.json \
  --report-dir docs/reports/${MNDOT}_mnpop --criteria-profile fhwa_tat3_2004" || say "battery_mndot_mnpop failed; continuing"

# 10b-ii. Why the capacity-scaled Minnesota population plateaus (2026-09-24, block 3): the same T-scaling
#     grid with (a) the MnDOT fleet on a 4-lane road and (b) the I-24 corrected fleet (its lane-change
#     settings) on the 3-lane road. Diagnostics, not populations: nothing points at these artifacts.
stage capprobe_mnfleet_4l $RUN scripts/calibrate_capacity.py --source artifacts/idm_i24.json \
  --fd artifacts/fd_mndot_i94_wb_stpaul.json --lanes 4 --base-scenario scenarios/$MNDOT.yaml \
  --out artifacts/idm_capacity_probe_mnfleet_4l.json --procs "$PROCS" || say "capprobe_mnfleet_4l failed; continuing"
stage capprobe_i24fleet_3l $RUN scripts/calibrate_capacity.py --source artifacts/idm_i24.json \
  --fd artifacts/fd_mndot_i94_wb_stpaul.json --lanes 3 --base-scenario scenarios/i24_replica_corrected.yaml \
  --out artifacts/idm_capacity_probe_i24fleet_3l.json --procs "$PROCS" || say "capprobe_i24fleet_3l failed; continuing"

# 10c. Weaving-section round (2026-09-24): the same corridor with the Ruth St and T.H.52 entrances as
#     weaving sections (merge: weave, docs/WEAVE_MODEL_PLAN.md) and two entrances on the scripted merge,
#     scenarios/mndot_i94_wb_stpaul_weave.yaml; 20 seeds with the I-24 population, then with the
#     Minnesota capacity-scaled population from stage mndot_population.
#     A 4-seed probe on the committed 35-minute peak slice first (scenarios/${MNDOT}_weave_slice.yaml):
#     minutes, not hours, and it says whether the map correction at the two downstream splits
#     (data/osm/mndot_i94_wb_stpaul.splits.con.xml, --ramps.unset 1001426896) removed the lock.
stage mndot_slice_weave $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave_slice.yaml \
  --observations data/mndot/$MNDOT/observations.json --replicates 4 --procs "$PROCS" \
  --out runs/${MNDOT}_weave_slice/baseline --artifact artifacts/validation_${MNDOT}_weave_slice.json \
  --report-dir docs/reports/${MNDOT}_weave_slice --criteria-profile fhwa_tat3_2004 || say "mndot_slice_weave failed; continuing"
stage battery_mndot_weave $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave.yaml \
  --observations data/mndot/$MNDOT/observations.json --replicates "$REPS" --procs "$PROCS" \
  --out runs/${MNDOT}_weave/baseline --artifact artifacts/validation_${MNDOT}_weave.json \
  --report-dir docs/reports/${MNDOT}_weave --criteria-profile fhwa_tat3_2004 || say "battery_mndot_weave failed; continuing"
stage battery_mndot_weave_mnpop bash -c "sed -e 's#^name: ${MNDOT}_weave\$#name: ${MNDOT}_weave_mnpop#' \
    -e 's#artifacts/idm_i24_capacity.json#artifacts/idm_${MNDOT}_capacity.json#' scenarios/${MNDOT}_weave.yaml \
    > scenarios/${MNDOT}_weave_mnpop.yaml && $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave_mnpop.yaml \
  --observations data/mndot/$MNDOT/observations.json --replicates $REPS --procs $PROCS \
  --out runs/${MNDOT}_weave_mnpop/baseline --artifact artifacts/validation_${MNDOT}_weave_mnpop.json \
  --report-dir docs/reports/${MNDOT}_weave_mnpop --criteria-profile fhwa_tat3_2004" || say "battery_mndot_weave_mnpop failed; continuing"

# 10d. Where the head of the I-94 queue forms (2026-09-24, block 3, WP-42): the first hour of the
#     corrected weave scenario, 4 seeds, as is and with the 40648744 entrance on the scripted merge;
#     then the standstill maps (50 m x 1 min, lanes; 100 m x 5 min) of each variant's first seed.
for V in head60 head60_scripted; do
  stage mndot_${V} $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave_${V}.yaml \
    --observations data/mndot/$MNDOT/observations.json --replicates 4 --procs "$PROCS" \
    --out runs/${MNDOT}_weave_${V}/baseline --artifact artifacts/validation_${MNDOT}_weave_${V}.json \
    --report-dir docs/reports/${MNDOT}_weave_${V} --criteria-profile fhwa_tat3_2004 || say "mndot_${V} failed; continuing"
  stage mndot_${V}_diag bash -c "D=\$(dirname \$(find runs/${MNDOT}_weave_${V} -name trajectories.parquet | head -1)) && \
    $RUN artifacts/mndot_rounds/weave_2026-09-24/diag_fine.py.txt \$D > logs/diag_${V}_50m_1min_lanes.txt && \
    $RUN artifacts/mndot_rounds/weave_2026-09-24/diag_lock.py.txt \$D > logs/diag_${V}_100m_5min.txt" || say "mndot_${V}_diag failed; continuing"
done

# 10e. The seed that fell to 0.743 under the speed-aware acceptance (VM K, 2026-09-24, block 3;
#     docs/ONBOARDING_MNDOT.md §11): seed index 4 of the weave scenario's spawn (677105600768189526,
#     0.865 under VM J) — the first five replicates, so that seed runs with its trajectory kept, then
#     the standstill maps (100 m x 5 min; 50 m x 1 min x lane over the whole corridor and run).
#     --diag-seed / --diag-reps select the seed and how many leading replicates run (VM N, 2026-09-24:
#     the seed the exiter-yield rule locked, 6904272788004776631, index 12).
#     --keep-trajectories: the battery prunes every trajectory but the first seed's (VM L, 2026-09-24,
#     ran the five replicates — the seed read 0.743 again — and the maps found no file).
#     --diag-weave-params k=v[,k2=v2]: the diagnostic runs scenarios/${MNDOT}_weave_diag.yaml, the weave
#     scenario with those weave_params on every weave section (VM R, 2026-09-25: exit_prepare's collapsed seeds).
DIAG_SCEN=scenarios/${MNDOT}_weave.yaml
if [ -n "$DIAG_WEAVE_PARAMS" ]; then
  DIAG_WEAVE_PARAMS=$(printf '%s' "$DIAG_WEAVE_PARAMS" | sed -e 's/=/: /g' -e 's/,/, /g')   # k=v,k2=v2 -> k: v, k2: v2 (no spaces on the command line)
  DIAG_SCEN=scenarios/${MNDOT}_weave_diag.yaml
  sed -e "s#^name: ${MNDOT}_weave\$#name: ${MNDOT}_weave_diag#" -e "s#weave_params: {}#weave_params: {${DIAG_WEAVE_PARAMS}}#" \
    scenarios/${MNDOT}_weave.yaml > "$DIAG_SCEN"
  say "diagnostic scenario $DIAG_SCEN: weave_params {${DIAG_WEAVE_PARAMS}} on $(grep -c "weave_params: {${DIAG_WEAVE_PARAMS}}" "$DIAG_SCEN") sections"
fi
stage mndot_weave_seed5 $RUN scripts/corridor_battery.py --scenario "$DIAG_SCEN" \
  --observations data/mndot/$MNDOT/observations.json --replicates "$DIAG_REPS" --procs "$PROCS" \
  --out runs/${MNDOT}_weave_seed5/baseline --artifact artifacts/validation_${MNDOT}_weave_seed5.json \
  --report-dir docs/reports/${MNDOT}_weave_seed5 --criteria-profile fhwa_tat3_2004 --keep-trajectories || say "mndot_weave_seed5 failed; continuing"
stage mndot_weave_seed5_diag bash -c "D=\$(ls -d runs/${MNDOT}_weave_seed5/baseline/*/$DIAG_SEED | head -1) && \
  $RUN artifacts/mndot_rounds/weave_2026-09-24/diag_lock.py.txt \$D > logs/diag_seed5_100m_5min.txt && \
  $RUN artifacts/mndot_rounds/weave_2026-09-24/diag_seed.py.txt \$D > logs/diag_seed5_50m_1min_lanes.txt && \
  for d in runs/${MNDOT}_weave_seed5/baseline/*/*/; do echo \$d; $RUN artifacts/mndot_rounds/weave_2026-09-24/diag_lock.py.txt \$d | tail -n 12; done > logs/diag_seed5_all_100m_5min.txt" || say "mndot_weave_seed5_diag failed; continuing"

# 10f. Per-lane crossing counts through the T.H.52 weave (WP-59's next measurement, 2026-09-24, block 3):
#     the corrected weave scenario's first hour, 4 seeds, every trajectory kept; then, per seed, the
#     per-lane flow and crossing speed in 5-min windows at 9.90 / 10.15 / 10.30 / 10.45 / 10.70 km
#     (simulation x) — whether lane 0 carries almost nothing on the 3-lane edge before the gore.
stage mndot_head60_lanes $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave_head60.yaml \
  --observations data/mndot/$MNDOT/observations.json --replicates 4 --procs "$PROCS" \
  --out runs/${MNDOT}_weave_head60_lanes/baseline --artifact artifacts/validation_${MNDOT}_weave_head60_lanes.json \
  --report-dir docs/reports/${MNDOT}_weave_head60_lanes --criteria-profile fhwa_tat3_2004 --keep-trajectories \
  || say "mndot_head60_lanes failed; continuing"
stage mndot_head60_lanes_diag bash -c "for d in runs/${MNDOT}_weave_head60_lanes/baseline/*/*/; do \
  $RUN artifacts/mndot_rounds/weave_2026-09-24/diag_lanes.py.txt \$d; done > logs/diag_head60_lane_crossings.txt 2>&1" \
  || say "mndot_head60_lanes_diag failed; continuing"

# 10g. How the corridor's T.H.52 exiters arrive at the gore (WP-61's "check first", 2026-09-24, block 3):
#     on the 10f run's trajectories, per seed, the exiters' lane at seven positions through the approach
#     and the section, and where they first reach the auxiliary lane (diag_exiters.py.txt).
stage mndot_head60_exiters_diag bash -c "for d in runs/${MNDOT}_weave_head60_lanes/baseline/*/*/; do \
  $RUN artifacts/mndot_rounds/weave_2026-09-24/diag_exiters.py.txt \$d; done > logs/diag_head60_exiters.txt 2>&1" \
  || say "mndot_head60_exiters_diag failed; continuing"

# 10h. WP-62's exit_prepare rule on the corridor (2026-09-25, block 3): the rule measured off on the
#     fixtures, whose approach puts 76-89 % of the exiters in the rightmost lane where the corridor puts
#     55-68 % (VM P) — so the corridor, not the fixture, decides it. Both weave sections of the corrected
#     scenarios with weave_params {exit_prepare: 1.0}; the slice (4 seeds) then the 20-seed battery.
for V in slice ""; do
  SUF=${V:+_$V}
  stage mndot_weave${SUF}_xprep bash -c "sed -e 's#^name: ${MNDOT}_weave${SUF}\$#name: ${MNDOT}_weave${SUF}_xprep#' \
      -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0}#' scenarios/${MNDOT}_weave${SUF}.yaml \
      > scenarios/${MNDOT}_weave${SUF}_xprep.yaml && grep -c 'exit_prepare: 1.0' scenarios/${MNDOT}_weave${SUF}_xprep.yaml && \
    $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave${SUF}_xprep.yaml \
      --observations data/mndot/$MNDOT/observations.json --replicates \$([ -n '$V' ] && echo 4 || echo $REPS) --procs $PROCS \
      --out runs/${MNDOT}_weave${SUF}_xprep/baseline --artifact artifacts/validation_${MNDOT}_weave${SUF}_xprep.json \
      --report-dir docs/reports/${MNDOT}_weave${SUF}_xprep --criteria-profile fhwa_tat3_2004" || say "mndot_weave${SUF}_xprep failed; continuing"
done

# 10i. Who stops first at a lock (WP-66's lead, 2026-09-25, block 3): on the diagnostic stage's run of DIAG_SEED,
#     the first vehicles at rest inside [LOCK_X0, LOCK_X1] m during [LOCK_T0, LOCK_T1] min — lane, leader, where each
#     entered and whether its track ends at an exit — and the per-minute lane speeds there (diag_lockveh.py.txt).
stage mndot_weave_seed5_lockveh bash -c "D=\$(ls -d runs/${MNDOT}_weave_seed5/baseline/*/$DIAG_SEED | head -1) && \
  $RUN artifacts/mndot_rounds/weave_2026-09-24/diag_lockveh.py.txt \$D ${LOCK_T0:-140} ${LOCK_T1:-170} ${LOCK_X0:-8300} ${LOCK_X1:-8600} \
  > logs/diag_lockveh.txt 2>&1" || say "mndot_weave_seed5_lockveh failed; continuing"

# 10j. exit_prepare with the lane-end give-up (WP-71, 2026-09-25, block 3): VM Q ran exit_prepare alone — GEH passes
#     nearly doubled, three of 20 seeds collapsed late at the T.H.61 two-lane weave's gore (VM T: exiters held on through
#     lanes, through vehicles on exit-only lanes); OSMNetwork.lane_end_giveup_m = 7.5 frees the front vehicle of such a
#     lane. The corrected scenarios with weave_params {exit_prepare: 1.0} and the network's lane_end_giveup_m: 7.5.
for V in slice ""; do
  SUF=${V:+_$V}
  stage mndot_weave${SUF}_xlend bash -c "sed -e 's#^name: ${MNDOT}_weave${SUF}\$#name: ${MNDOT}_weave${SUF}_xlend#' \
      -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0}#' scenarios/${MNDOT}_weave${SUF}.yaml \
      | awk '{print} /^  kind: osm\$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' > scenarios/${MNDOT}_weave${SUF}_xlend.yaml && \
    grep -c 'exit_prepare: 1.0' scenarios/${MNDOT}_weave${SUF}_xlend.yaml && grep -c '^  lane_end_giveup_m: 7.5' scenarios/${MNDOT}_weave${SUF}_xlend.yaml && \
    $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave${SUF}_xlend.yaml \
      --observations data/mndot/$MNDOT/observations.json --replicates \$([ -n '$V' ] && echo 4 || echo $REPS) --procs $PROCS \
      --out runs/${MNDOT}_weave${SUF}_xlend/baseline --artifact artifacts/validation_${MNDOT}_weave${SUF}_xlend.json \
      --report-dir docs/reports/${MNDOT}_weave${SUF}_xlend --criteria-profile fhwa_tat3_2004" || say "mndot_weave${SUF}_xlend failed; continuing"
done

# 10k. The reference configuration (10j, VM U) plus WP-70's ramp_outlet (2026-09-25, block 3): on the corridor section fixture
#     ramp_outlet lets the T.H.52 entrance pass; on the corridor the question is whether it lifts the weave's throughput
#     (S790 ~3,340 veh/h against 4,911 observed). weave_params {exit_prepare: 1.0, ramp_outlet: 1.0} + lane_end_giveup_m: 7.5
#     (ramp_outlet is inert on sections shorter than 253.8 m, so Ruth St is unchanged).
for V in slice ""; do
  SUF=${V:+_$V}
  stage mndot_weave${SUF}_xlout bash -c "sed -e 's#^name: ${MNDOT}_weave${SUF}\$#name: ${MNDOT}_weave${SUF}_xlout#' \
      -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0, ramp_outlet: 1.0}#' scenarios/${MNDOT}_weave${SUF}.yaml \
      | awk '{print} /^  kind: osm\$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' > scenarios/${MNDOT}_weave${SUF}_xlout.yaml && \
    grep -c 'ramp_outlet: 1.0' scenarios/${MNDOT}_weave${SUF}_xlout.yaml && grep -c '^  lane_end_giveup_m: 7.5' scenarios/${MNDOT}_weave${SUF}_xlout.yaml && \
    $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave${SUF}_xlout.yaml \
      --observations data/mndot/$MNDOT/observations.json --replicates \$([ -n '$V' ] && echo 4 || echo $REPS) --procs $PROCS \
      --out runs/${MNDOT}_weave${SUF}_xlout/baseline --artifact artifacts/validation_${MNDOT}_weave${SUF}_xlout.json \
      --report-dir docs/reports/${MNDOT}_weave${SUF}_xlout --criteria-profile fhwa_tat3_2004" || say "mndot_weave${SUF}_xlout failed; continuing"
done

# 10l. The reference configuration (10j, VM U) with overtaking on the right allowed (WP-76, 2026-09-25, block 3): SUMO's
#     default forbids it (the European rule); with the fleet's lc_keep_right 0 a slow leftmost-lane driver then paces every
#     lane, and on the corridor section fixture the exit-end criterion passes at 9 of 10 seeds with it allowed against 2 of
#     10 without (crossings removed). US freeways allow it; a diagnostic battery, not an adoption: fleet lc_overtake_right 1.0.
for V in slice ""; do
  SUF=${V:+_$V}
  stage mndot_weave${SUF}_xlovr bash -c "sed -e 's#^name: ${MNDOT}_weave${SUF}\$#name: ${MNDOT}_weave${SUF}_xlovr#' \
      -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0}#' -e 's#^  lc_overtake_right: null\$#  lc_overtake_right: 1.0#' \
      scenarios/${MNDOT}_weave${SUF}.yaml \
      | awk '{print} /^  kind: osm\$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' > scenarios/${MNDOT}_weave${SUF}_xlovr.yaml && \
    grep -c '^  lc_overtake_right: 1.0' scenarios/${MNDOT}_weave${SUF}_xlovr.yaml && grep -c '^  lane_end_giveup_m: 7.5' scenarios/${MNDOT}_weave${SUF}_xlovr.yaml && \
    $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave${SUF}_xlovr.yaml \
      --observations data/mndot/$MNDOT/observations.json --replicates \$([ -n '$V' ] && echo 4 || echo $REPS) --procs $PROCS \
      --out runs/${MNDOT}_weave${SUF}_xlovr/baseline --artifact artifacts/validation_${MNDOT}_weave${SUF}_xlovr.json \
      --report-dir docs/reports/${MNDOT}_weave${SUF}_xlovr --criteria-profile fhwa_tat3_2004" || say "mndot_weave${SUF}_xlovr failed; continuing"
done

# 10m. The reference configuration (10j, VM U) with the weave's acceptance calibrated to real drivers (VM Z,
#     2026-09-25, block 3; artifacts/i24_critical_gaps.json proposal: accept_gap_s 0.089 s, exit_accept_gap_s 1.78 s,
#     from the I-24 MOTION Hickory Hollow-Bell Road weave's fitted critical gaps). A calibration, measured on the corridor.
for V in slice ""; do
  SUF=${V:+_$V}
  stage mndot_weave${SUF}_xlcal bash -c "sed -e 's#^name: ${MNDOT}_weave${SUF}\$#name: ${MNDOT}_weave${SUF}_xlcal#' \
      -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0, accept_gap_s: 0.089, exit_accept_gap_s: 1.78}#' \
      scenarios/${MNDOT}_weave${SUF}.yaml \
      | awk '{print} /^  kind: osm\$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' > scenarios/${MNDOT}_weave${SUF}_xlcal.yaml && \
    grep -c 'accept_gap_s: 0.089' scenarios/${MNDOT}_weave${SUF}_xlcal.yaml && grep -c '^  lane_end_giveup_m: 7.5' scenarios/${MNDOT}_weave${SUF}_xlcal.yaml && \
    $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave${SUF}_xlcal.yaml \
      --observations data/mndot/$MNDOT/observations.json --replicates \$([ -n '$V' ] && echo 4 || echo $REPS) --procs $PROCS \
      --out runs/${MNDOT}_weave${SUF}_xlcal/baseline --artifact artifacts/validation_${MNDOT}_weave${SUF}_xlcal.json \
      --report-dir docs/reports/${MNDOT}_weave${SUF}_xlcal --criteria-profile fhwa_tat3_2004" || say "mndot_weave${SUF}_xlcal failed; continuing"
done

# 10n. The reference configuration (10j, VM U) with WP-92's guard against opposing entries into one lane in one step
#     (2026-09-25, block 3; WEAVE_DEFAULTS["opposing_entry_guard"], off by default). Run beside mndot_weave_xlend at the
#     same commit and seeds, so the two batteries pair seed by seed: does the guard remove the corridor's collisions
#     (15 over 20 seeds on VM U) without costing departures, RMSPE or GEH? A diagnostic battery, not a default change.
for V in slice ""; do
  SUF=${V:+_$V}
  stage mndot_weave${SUF}_xlopp bash -c "sed -e 's#^name: ${MNDOT}_weave${SUF}\$#name: ${MNDOT}_weave${SUF}_xlopp#' \
      -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0, opposing_entry_guard: 1.0}#' \
      scenarios/${MNDOT}_weave${SUF}.yaml \
      | awk '{print} /^  kind: osm\$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' > scenarios/${MNDOT}_weave${SUF}_xlopp.yaml && \
    grep -c 'opposing_entry_guard: 1.0' scenarios/${MNDOT}_weave${SUF}_xlopp.yaml && grep -c '^  lane_end_giveup_m: 7.5' scenarios/${MNDOT}_weave${SUF}_xlopp.yaml && \
    $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave${SUF}_xlopp.yaml \
      --observations data/mndot/$MNDOT/observations.json --replicates \$([ -n '$V' ] && echo 4 || echo $REPS) --procs $PROCS \
      --out runs/${MNDOT}_weave${SUF}_xlopp/baseline --artifact artifacts/validation_${MNDOT}_weave${SUF}_xlopp.json \
      --report-dir docs/reports/${MNDOT}_weave${SUF}_xlopp --criteria-profile fhwa_tat3_2004" || say "mndot_weave${SUF}_xlopp failed; continuing"
done

# 10o. The reference configuration (10j, VM U) with WP-93's forced-change guard on the two scripted merges (2026-09-26,
#     block 3; merge_params {force_guard: 1.0} on on-ramps 18207436 and 178547099, off by default). VM AF put 14 of the
#     reference's 15 collisions on those two merges' lanes; on the McKnight Rd fixture every collision is a forced change
#     under mode 256 landing in front of a follower 10-16 m/s faster, and the guard removes them all without costing the
#     merge in a queue. Does it on the corridor, without starving the two ramps or costing departures, RMSPE or GEH?
#     Run beside mndot_weave_xlend at the same commit so the batteries pair seed by seed. Diagnostic, not a default change.
for V in slice ""; do
  SUF=${V:+_$V}
  stage mndot_weave${SUF}_xlsfg bash -c "sed -e 's#^name: ${MNDOT}_weave${SUF}\$#name: ${MNDOT}_weave${SUF}_xlsfg#' \
      -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0}#' scenarios/${MNDOT}_weave${SUF}.yaml \
      | awk '{print} /^  kind: osm\$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' \
      | awk '/^    merge: scripted\$/ {s=1; print; next} s && /^    merge_params: \{\}\$/ {print \"    merge_params: {force_guard: 1.0}\"; s=0; next} {s=0; print}' \
      > scenarios/${MNDOT}_weave${SUF}_xlsfg.yaml && \
    [ \$(grep -c '^    merge_params: {force_guard: 1.0}\$' scenarios/${MNDOT}_weave${SUF}_xlsfg.yaml) -eq 2 ] && \
    grep -c '^  lane_end_giveup_m: 7.5' scenarios/${MNDOT}_weave${SUF}_xlsfg.yaml && \
    $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave${SUF}_xlsfg.yaml \
      --observations data/mndot/$MNDOT/observations.json --replicates \$([ -n '$V' ] && echo 4 || echo $REPS) --procs $PROCS \
      --out runs/${MNDOT}_weave${SUF}_xlsfg/baseline --artifact artifacts/validation_${MNDOT}_weave${SUF}_xlsfg.json \
      --report-dir docs/reports/${MNDOT}_weave${SUF}_xlsfg --criteria-profile fhwa_tat3_2004" || say "mndot_weave${SUF}_xlsfg failed; continuing"
done

# 10p. The reference configuration (10j, VM U) with the two scripted merges on SUMO's own lane change (2026-09-26, block 3,
#     WP-93; merge: lane_change on on-ramps 18207436 and 178547099, an existing option). The scripted merge was put on
#     them in round 2, before the map defects of 2026-09-24 were found; on the McKnight Rd fixture SUMO's own merge
#     collides never and merges more ramp vehicles, sooner, in every regime measured. Does it hold on the corridor, where
#     round 2's lane-change merges locked? Run beside mndot_weave_xlend at the same commit. Diagnostic, not a scenario change.
for V in slice ""; do
  SUF=${V:+_$V}
  stage mndot_weave${SUF}_xlmlc bash -c "sed -e 's#^name: ${MNDOT}_weave${SUF}\$#name: ${MNDOT}_weave${SUF}_xlmlc#' \
      -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0}#' -e 's#^    merge: scripted\$#    merge: lane_change#' \
      scenarios/${MNDOT}_weave${SUF}.yaml \
      | awk '{print} /^  kind: osm\$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' > scenarios/${MNDOT}_weave${SUF}_xlmlc.yaml && \
    [ \$(grep -c '^    merge: scripted\$' scenarios/${MNDOT}_weave${SUF}_xlmlc.yaml) -eq 0 ] && \
    grep -c '^  lane_end_giveup_m: 7.5' scenarios/${MNDOT}_weave${SUF}_xlmlc.yaml && \
    $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave${SUF}_xlmlc.yaml \
      --observations data/mndot/$MNDOT/observations.json --replicates \$([ -n '$V' ] && echo 4 || echo $REPS) --procs $PROCS \
      --out runs/${MNDOT}_weave${SUF}_xlmlc/baseline --artifact artifacts/validation_${MNDOT}_weave${SUF}_xlmlc.json \
      --report-dir docs/reports/${MNDOT}_weave${SUF}_xlmlc --criteria-profile fhwa_tat3_2004" || say "mndot_weave${SUF}_xlmlc failed; continuing"
done

# 11. Operational strategies on the validated I-24 arm (opt-in, 2026-09-23): six cells × 20 seeds —
#     baseline, VSL only, ALINEA only, FollowerStopper 10 % under none / vsl / alinea. ALINEA target
#     29.2 veh/km/lane = the capacity-scaled population's equilibrium capacity 1,985.5 veh/h/lane at
#     18.912 m/s (artifacts/idm_i24_capacity_equilibrium.json). Throughput at data x = 2,200 m
#     (sim x 4,412 m); analysed span the measured 2,256–7,638 m (artifacts/i24_replica_inputs.json).
#     Memory: a FollowerStopper-under-strategy run of this arm takes about 9 GB of RAM (the kernel
#     OOM-killed the 32-process pool twice on 2026-09-24, at 112/120 and 117/120 runs — the guest
#     journal in logs/guest_exit.log), so the pool is capped at 12 on the 125 GB machine.
stage sweep_i24_strat $RUN scripts/corridor_sweep.py --scenario scenarios/i24_replica_flow_speedcal_ramps.yaml \
  --penetration 0.10 --compliance 1.0 --controllers follower_stopper --strategies none vsl alinea \
  --rho-target-veh-km 29.2 --x-ref 4411.8 --span 2256.2 7637.8 --replicates "$REPS" --procs "$(( PROCS < 12 ? PROCS : 12 ))" \
  --out runs/i24_strat_sweep --summary artifacts/sweep_i24_strategies_summary.json || say "sweep_i24_strat failed; continuing"

# 12. How real drivers take gaps (WP-77, 2026-09-25, block 3; opt-in, needs --data-set i24): every lane change of the
#     I-24 MOTION westbound day (the processed 5 Hz table the launch ships, 06:00-10:00 CST, in 15-min chunks) with the
#     bumper-to-bumper gaps and speeds of its new leader and follower, by ramp zone (the Old Hickory merge, the Hickory
#     Hollow diverge, the Hickory Hollow-Bell Road weave, the Bell Road diverge) and movement, and the share the weave
#     model's acceptance would refuse (calibration.lane_change_gaps) -> artifacts/i24_lane_change_gaps.json; the per-change
#     table data/i24motion/processed/i24_wb_lane_change_gaps.parquet rides along in the archive. One process, a few GB.
if echo " $STAGES " | grep -q " i24_lane_change_gaps "; then
  stage i24_lane_change_gaps $RUN scripts/i24_lane_change_gaps.py || say "i24_lane_change_gaps failed; continuing"
fi

# 13. Critical gaps of the weave's crossings (WP-78, 2026-09-25, block 3; opt-in, needs --data-set i24): the same chunks,
#     zones and acceptance as stage 12, and for every entering and exiting change in a ramp zone the target-lane gaps of
#     the 10 s before it, every 1 s, inside the change's zone (calibration.lane_change_gaps.gap_sequences: the pair it
#     entered, the pairs it let go by, empty instants); per driver the accepted and largest rejected gaps; Troutbeck's
#     maximum-likelihood critical gaps per side and the joint lead-lag estimator (log-normal, 200 bootstrap replicates)
#     per zone x movement x speed class (calibration.critical_gap); the acceptance time gaps that reproduce the fitted
#     medians at speed parity, proposed, WEAVE_DEFAULTS untouched -> artifacts/i24_critical_gaps.json; the samples and
#     driver tables ride along in the archive. One process: ~1 GB peak per 15-min chunk on a 3 M-row synthetic stand-in.
if echo " $STAGES " | grep -q " i24_critical_gaps "; then
  stage i24_critical_gaps $RUN scripts/i24_critical_gaps.py || say "i24_critical_gaps failed; continuing"
fi

# 14. The multi-lane hypothesis behind the US-101 fuel result (WP-81, docs/ROADMAP.md §5 D2, 2026-09-25; opt-in; needs no
#     data set: launch with --data-set none). The penetration sweep of docs/US101_PENETRATION.md re-run as it was
#     (scripts/us101_penetration_sweep.py: baseline + FollowerStopper at 1/2/5/10/20 %, 100 % compliance, the 20 seeds of
#     spawn_seeds(42, 20), the measured downstream boundary — the boundary block of the committed
#     scenarios/us101_replica_calibrated.yaml when the runs/m3_us101 snapshot is absent, the same config hash), every
#     trajectory kept (13-14 MB a baseline run of the with-boundary replica, so about 2 GB for the 120 runs); then
#     scripts/us101_lane_changes.py: per run the lane changes per veh-km over the replica's 640 m by class (AV / human;
#     pass-arounds of an AV directly ahead; cut-ins ahead of one), the counterfactual pass-arounds behind the same vehicles
#     in the seed's baseline, per-vehicle fuel against lane changes and the original analysis's metrics; per level the
#     paired-by-seed change against the baseline with 95 % CIs and the pre-registered checks
#     -> artifacts/us101_lane_change_penetration.json. The committed artifacts/us101_penetration_summary.json is not
#     rewritten (scripts/us101_penetration_analyze.py does not run). Analysis: about 1 s and 0.5 GB per run measured on
#     a 588k-row synthetic run of the replica's size; the pool is capped at 16.
if echo " $STAGES " | grep -q " us101_lane_changes "; then
  stage us101_lane_changes bash -c "$RUN scripts/us101_penetration_sweep.py --procs $PROCS --replicates $REPS && \
    $RUN scripts/us101_lane_changes.py --sweep runs/us101_penetration --procs $(( PROCS < 16 ? PROCS : 16 )) \
      --out artifacts/us101_lane_change_penetration.json" || say "us101_lane_changes failed; continuing"
fi

# 15. The gap after the crossing in real traffic (WP-88, 2026-09-25, block 3; opt-in, needs --data-set i24): for every
#     confirmed entering, exiting and through lane change of the I-24 MOTION westbound day (stage 12's chunks, span, ramp
#     zones, lanes and debounce; each 15-min chunk loaded with a 38 s pad), the new follower's and the changer's time and
#     space gaps at 0-30 s after the change, tracked through tracker fragment switches and censored at the first lane
#     change, cut-in, lost track or loss of car-following; as ratios to the vehicle's own pre-change gap (30-5 s before),
#     to the population's car-following time gap at the same speed and to s0 + vT at the idm_i24_capacity means; per zone
#     kind x movement x changer-speed class, with an exponential relaxation time and its bootstrap interval where the data
#     support one (calibration.lane_change_relaxation) -> artifacts/lane_change_relaxation_i24.json (summaries and a
#     100-change sample only). One process; estimated from a quarter-chunk synthetic stand-in (779k rows: 2.3 s, 433 MB)
#     and a 100k-side bootstrap fit (2.7 s): about 5-10 min and 2-3 GB peak.
if echo " $STAGES " | grep -q " i24_lane_change_relaxation "; then
  stage i24_lane_change_relaxation $RUN scripts/lane_change_relaxation.py --source i24 \
    || say "i24_lane_change_relaxation failed; continuing"
fi

# 16. The same on NGSIM US-101 (WP-88; opt-in; reads data/ngsim, which the launch ships with --data-set i24 when present):
#     the raw data.transportation.gov export the repository holds (scripts/us101_data.py; NOT the Montanino-Punzo
#     reconstruction), de-duplicated and split into its two recording periods, 10 Hz; lanes 1-5 mainline, 6 the auxiliary
#     lane between the Ventura on-ramp and the Cahuenga off-ramp (the weaving zone: the 0.5-99.5 % span of lane-6
#     positions, read off the data); s0 + vT at the idm_us101 means -> artifacts/lane_change_relaxation_us101.json.
#     One process; the 2.4 M-row CSV load dominates: a few minutes and 1-2 GB peak (estimated).
if echo " $STAGES " | grep -q " us101_lane_change_relaxation "; then
  stage us101_lane_change_relaxation $RUN scripts/lane_change_relaxation.py --source us101 \
    || say "us101_lane_change_relaxation failed; continuing"
fi

# 17. Coverage thinning on NGSIM US-101 (WP-91, 2026-09-25, block 3; opt-in; reads data/ngsim, which the launch ships
#     with --data-set i24 when present): the raw export stage 16 reads, thinned to I-24-like coverage
#     (calibration.thinning: vehicle-level, a fraction F of the vehicle ids with whole tracks; fragment-level, every
#     track cut into I-24-like fragments, log-normal median 9.9 s, with untracked spells between them and a new tracker
#     id per fragment) at F = 1.0 / 0.65 / 0.5, five thinning seeds each, and every lane-change measure of stages 12, 13
#     and 16 read again on each thinned table: the gaps after the crossing at 0 / 5 / 10 s (ratio_pop, ratio_eq, time
#     and space gaps, the partner speeds), the gap records with VM X's acceptance and its refusal shares per term, and the
#     critical gaps of the weaving zone's entering and exiting crossings -> artifacts/coverage_thinning_us101.json (per
#     measure the reference and, per model and F, the seeds' values, mean, min-max, t-interval and shift). One process;
#     on a 522k-row synthetic stand-in 12.8 s at 432 MB peak, so about 1-3 min and 1.5-2 GB on the 1.9 M-row dump
#     (estimated; stage 16's load alone peaked at 1.0 GB).
if echo " $STAGES " | grep -q " us101_coverage_thinning "; then
  stage us101_coverage_thinning $RUN scripts/coverage_thinning.py \
    || say "us101_coverage_thinning failed; continuing"
fi

# 9. Done marker; the EXIT trap builds the final archives (light, then full with the first-seed replicates).
echo "PIPELINE_DONE $(date -u +%FT%TZ)" > logs/PIPELINE_DONE
say "PIPELINE_DONE"
