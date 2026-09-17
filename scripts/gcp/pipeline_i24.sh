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
while [ $# -gt 0 ]; do
  case "$1" in
    --procs) PROCS="$2"; shift 2 ;;
    --no-shutdown) SHUTDOWN=0; shift ;;
    --quick) QUICK=1; shift ;;
    --stages) STAGES="$2"; shift 2 ;;
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
  stage prune_us101 bash -c 'for h in runs/m3_us101/*/*/; do [ -d "$h" ] || continue; first=$(ls -d "$h"*/ 2>/dev/null | sort | head -1); for r in "$h"*/; do [ "$r" = "$first" ] && continue; rm -f "$r/trajectories.parquet"; done; done; du -sh runs/m3_us101' || true
fi

if echo " $STAGES " | grep -q " probe_ohlevel "; then
  # Candidate 3 of docs/MERGE_ROUND6_PLAN.md: the Old Hickory demand level on the flow family's
  # fitted arm, single seed; the 1.0 point is the arm itself (flow_speedcal).
  stage probe_ohlevel $RUN scripts/i24_merge_experiment.py --base scenarios/i24_replica_flow_speedcal.yaml \
    --variants flow_speedcal flow_speedcal_oh0.55 flow_speedcal_oh0.75 flow_speedcal_oh1.25 flow_speedcal_oh1.6 \
    --procs "$PROCS" --out artifacts/i24_merge_experiment_ohlevel.json || exit 1
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

# 9. Done marker; the EXIT trap builds the final archives (light, then full with the first-seed replicates).
echo "PIPELINE_DONE $(date -u +%FT%TZ)" > logs/PIPELINE_DONE
say "PIPELINE_DONE"
