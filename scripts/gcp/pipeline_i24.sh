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
#   * a SIGTERM (systemd stop at shutdown) runs the EXIT trap too (light archive
#     only: the stop window does not give the full one its minutes);
#   * with PIPELINE_SELF_DELETE=1 the EXIT trap DELETES the instance and never
#     powers it off (2026-10-07 review): light archive (two tries), full archive,
#     and when the full copy fails the light one again (four tries), then the
#     delete (three tries), whatever reached the bucket. A powered-off instance
#     stops Compute Engine's max-run-duration clock and nothing would ever delete
#     it; if the delete itself fails the instance stays up for the idle guard
#     (which retries the delete) and max-run-duration;
#   * without PIPELINE_SELF_DELETE the instance cannot delete itself, so the trap
#     powers it off three minutes after the end and the laptop watcher (its
#     TERMINATED branch) deletes the stopped instance;
#   * logs/INSTANCE_ID (this instance's id) rides in every archive, so the
#     watcher can refuse a bucket archive another launch left under the prefix;
#   * when an archive's file list fails (a read error, or a stop's SIGTERM killing
#     tar in the exit trap) the fallback (artifacts, scenarios, logs only) never
#     replaces a complete archive: beside one, locally or in the bucket, it is
#     final_partial.tgz.
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
# a self-delete without a bucket would delete the only copy of the results (the launcher refuses it too)
if [ "$SELF_DELETE" = 1 ] && [ -z "$BUCKET" ]; then say "PIPELINE_SELF_DELETE=1 without PIPELINE_BUCKET: ignored (power-off instead)"; SELF_DELETE=0; fi
md() { curl -sf -m 10 -H Metadata-Flavor:Google "http://metadata.google.internal/computeMetadata/v1/$1" 2>/dev/null; }
# this instance's id rides in every archive: watch_pipeline.sh compares it with the instance it watches before it
# trusts $BUCKET/final.tgz (a prefix reused while an earlier launch's archive is still there)
if md instance/id > logs/INSTANCE_ID.part && [ -s logs/INSTANCE_ID.part ]; then mv -f logs/INSTANCE_ID.part logs/INSTANCE_ID; else rm -f logs/INSTANCE_ID.part; fi
UPLOADED=0       # 1 when the last make_archive's bucket copy landed
TERMINATING=0    # 1 once a SIGTERM (a system stop) arrived
ARCHIVE_LIGHT="$HOME/final_light.tgz"   # the exit-time light archive, kept for the upload retry after a failed full copy
ARCHIVE_PARTIAL="$HOME/final_partial.tgz"   # a fallback archive (artifacts, scenarios, logs) beside a complete one
upload_archive() {  # upload_archive <file> [tries] [object]: copy <file> to $BUCKET/<object> (final.tgz), waiting 30, 60, 90 s between tries
  local f="$1" tries="${2:-1}" object="${3:-final.tgz}" i=1
  [ -n "$BUCKET" ] || return 1
  while true; do
    if gcloud storage cp "$f" "$BUCKET/$object" >>"$LOG" 2>&1; then say "archive copied to $BUCKET/$object"; return 0; fi
    say "bucket copy FAILED (attempt $i of $tries; see $LOG)"
    [ "$i" -ge "$tries" ] || [ "$TERMINATING" -eq 1 ] && return 1
    sleep $((30 * i)); i=$((i + 1))
  done
}
make_archive() {  # make_archive light|full [tries] — atomic replace of $ARCHIVE, then the optional bucket copy
  local mode="${1:-light}" tries="${2:-1}" extra=""
  UPLOADED=0
  if [ "$mode" = full ]; then
    extra=$(for d in runs/i24_validation_zip/*/*/ runs/i24_validation/speedcal_heavy/*/ runs/i24_validation/dc*/*/; do ls -d "$d"*/ 2>/dev/null | sort | head -1; done)
    [ -f runs/i24_validation_zip/ring/ring_benchmark.json ] && extra="runs/i24_validation_zip/ring/ring_benchmark.json $extra"
  fi
  # per-run metrics of the cap sweep ride along in every archive: the sweep is resumable from them
  extra="$extra $(ls runs/i24_cap_sweep/*/*/metrics.json 2>/dev/null | tr '\n' ' ')"
  # the penetration sweep's per-run metrics and manifest (the summary is built from them by
  # scripts/i24_penetration_analyze.py; the 2026-09-18 run lost 6.5 h of them to this omission); meta.json too
  # (2026-10-07): the sweep counts a run done only when both exist, so a relaunch resumes from them
  extra="$extra $(ls runs/i24_sweep/*/*/*/metrics.json runs/i24_sweep/*/*/*/meta.json runs/i24_sweep/MANIFEST.json 2>/dev/null | tr '\n' ' ')"
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
  # the handback and committed-configuration re-runs (WP-95, stage 18): per-run metrics and meta.json (n_collisions,
  # collisions, av_emergency_handback) of runs/<tree>_hb and runs/<tree>_cc, four levels as corridor_sweep.py writes them
  extra="$extra $(ls runs/*_hb/*/*/*/metrics.json runs/*_hb/*/*/*/meta.json runs/*_cc/*/*/*/metrics.json runs/*_cc/*/*/*/meta.json runs/*_hb/MANIFEST.json runs/*_cc/MANIFEST.json 2>/dev/null | tr '\n' ' ')"
  # the command-path re-runs (WP-96, stage 19): the same files of runs/<tree>_wp96c, _wp96f and _wp96fh
  extra="$extra $(ls runs/*_wp96*/*/*/*/metrics.json runs/*_wp96*/*/*/*/meta.json runs/*_wp96*/MANIFEST.json 2>/dev/null | tr '\n' ' ')"
  # the phase-1 re-runs at the new defaults (stage 20a) and the tool rehearsal's outputs (stage 20c: JSON, markdown and
  # CSV reports, day split, day-set observations; the 30-s cache under data/ stays behind)
  extra="$extra $(ls runs/*_p1def/*/*/*/metrics.json runs/*_p1def/*/*/*/meta.json runs/*_p1def/MANIFEST.json 2>/dev/null | tr '\n' ' ')"
  [ -d runs/p1_rehearsal ] && extra="$extra runs/p1_rehearsal"
  # 20d's trees: per-run metrics/meta (five levels), the design and the tuning manifests, comparison tables
  extra="$extra $(ls runs/p1_unc/DESIGN.json runs/p1_unc/*/*/*/*/metrics.json runs/p1_unc/*/*/*/*/meta.json runs/p1_tune/*.json runs/p1_tune/*.md runs/p1_tune/*/MANIFEST.json runs/p1_tune/*/*/*/*/metrics.json runs/p1_tune/*/*/*/*/meta.json 2>/dev/null | tr '\n' ' ')"
  extra="$extra $(ls runs/p1b_unc/DESIGN.json runs/p1b_unc/*/*/*/*/metrics.json runs/p1b_unc/*/*/*/*/meta.json runs/p1b_tune/*.json runs/p1b_tune/*.md runs/p1b_tune/*/MANIFEST.json runs/p1b_tune/*/*/*/*/metrics.json runs/p1b_tune/*/*/*/*/meta.json 2>/dev/null | tr '\n' ' ')"
  # stage 22's grids (<root>/<pair>/<config hash>/<seed>/: readings and meta of every run, the manifest, the lane geometry)
  # and the per-lane data-quality report the observed I-94 lane shares were masked with; the artifacts ride in artifacts/*.json
  extra="$extra $(ls runs/p3/grid_*/*/*/*/readings.json runs/p3/grid_*/*/*/*/meta.json runs/p3/grid_*/MANIFEST.json runs/p3/grid_*/LANES.json runs/p3/dq_lanes/data_quality.json runs/p3/dq_lanes/data_quality.md 2>/dev/null | tr '\n' ' ')"
  # stage 23's I-24 batteries (runs/i24_validation/dc*/<config hash>/<seed>/): every replicate's meta.json (collision
  # counters); the I-94 battery's per-seed files ride with the runs/mndot_* line above, its reports with docs/reports
  extra="$extra $(ls runs/i24_validation/dc*/*/*/meta.json 2>/dev/null | tr '\n' ' ')"
  # stage 24's probe (<root>/<network>/<pair>/<config hash>/<seed>/: readings and meta of every run, each network's lanes)
  extra="$extra $(ls runs/p5/i94_netfix_probe/*/LANES.json runs/p5/i94_netfix_probe/*/*/*/*/readings.json runs/p5/i94_netfix_probe/*/*/*/*/meta.json 2>/dev/null | tr '\n' ' ')"
  # shellcheck disable=SC2086
  if tar czf "$ARCHIVE.part" --exclude=net artifacts/*.json scenarios/*.yaml logs $extra 2>/dev/null; then
    mv -f "$ARCHIVE.part" "$ARCHIVE"
    ls -la "$ARCHIVE" | awk -v m="$mode" '{print "archive (" m "):", $5, "bytes"}' | tee -a "$LOG"
    if [ -n "$BUCKET" ] && upload_archive "$ARCHIVE" "$tries"; then UPLOADED=1; fi
    return 0
  fi
  # the file list failed (a read error, or a system stop's SIGTERM killed tar inside the exit trap): a fallback of
  # artifacts, scenarios and logs only. It never replaces a complete archive, here or in the bucket (2026-10-07
  # review: a stop during the exit trap's full archive put the fallback over the bucket's complete light copy,
  # which lost runs/** and still passed the watcher's checks): beside one it goes under final_partial.tgz
  tar czf "$ARCHIVE.part" artifacts/*.json scenarios/*.yaml logs 2>/dev/null || { rm -f "$ARCHIVE.part"; return 1; }
  local dest="$ARCHIVE" object=final.tgz
  [ -f "$ARCHIVE" ] && dest="$ARCHIVE_PARTIAL"
  if [ -n "$BUCKET" ] && gcloud storage ls "$BUCKET/final.tgz" >/dev/null 2>&1; then object=final_partial.tgz; fi
  mv -f "$ARCHIVE.part" "$dest"
  ls -la "$dest" | awk -v m="$mode" '{print "archive (" m ", FALLBACK: artifacts, scenarios and logs only):", $5, "bytes"}' | tee -a "$LOG"
  [ "$dest" = "$ARCHIVE" ] || say "the complete archive $ARCHIVE is kept; the fallback is $dest"
  if [ -n "$BUCKET" ] && upload_archive "$dest" "$tries" "$object" && [ "$object" = final.tgz ]; then UPLOADED=1; fi
  return 0
}
self_delete() {  # self_delete [tries]: delete this instance (the compute-rw scope and the instanceAdmin grant of --self-delete)
  local tries="${1:-3}" i name zone
  name=$(md instance/name); [ -n "$name" ] || name=$(hostname)
  zone=$(md instance/zone | awk -F/ '{print $NF}')
  for i in $(seq 1 "$tries"); do
    say "deleting this instance ($name, $zone), attempt $i of $tries"
    gcloud compute instances delete "$name" --zone "$zone" --quiet >>"$LOG" 2>&1 && return 0
    [ "$i" -lt "$tries" ] && [ "$TERMINATING" -eq 0 ] && sleep 60
  done
  return 1
}
finish() {
  rc=$?
  local in_bucket=0 tries
  # a SIGTERM while this trap archives (a system stop) must not end it before the delete below
  trap 'TERMINATING=1; say "SIGTERM received in the exit trap"' TERM
  say "PIPELINE_EXIT rc=$rc"
  echo "rc=$rc $(date -u +%FT%TZ)" > logs/PIPELINE_EXIT
  # guest-side evidence of WHY the machine is going down rides along (2026-09-24: an instance
  # was deleted mid-sweep and nothing on the laptop side could say by whom)
  { sudo tail -n 50 /var/log/idle-guard.log 2>/dev/null; echo "--- journal"; sudo journalctl -n 120 --no-pager 2>/dev/null; echo "--- uptime $(uptime)"; } > logs/guest_exit.log 2>&1 || true
  # light first (seconds: survives a systemd stop window); its copy serves the retry below
  rm -f "$ARCHIVE_LIGHT"
  tries=2; [ "$TERMINATING" -eq 1 ] && tries=1
  make_archive light "$tries" && cp -f "$ARCHIVE" "$ARCHIVE_LIGHT" 2>/dev/null
  [ "$UPLOADED" = 1 ] && in_bucket=1
  if [ "$TERMINATING" -eq 1 ]; then
    say "system stop: light archive only"
  else
    make_archive full    # minutes: the first-seed replicates for the figures
    if [ "$UPLOADED" = 1 ]; then
      in_bucket=1
    elif [ -n "$BUCKET" ] && [ -f "$ARCHIVE_LIGHT" ]; then
      # a failed copy replaces nothing (an object changes only when an upload completes), so a light copy that
      # landed above still stands; upload it again so this run's exit marker is in the bucket either way
      say "the full archive did not reach the bucket; uploading the light archive again"
      tries=4; [ "$TERMINATING" -eq 1 ] && tries=1
      upload_archive "$ARCHIVE_LIGHT" "$tries" && in_bucket=1
    fi
  fi
  [ "$SHUTDOWN" -eq 1 ] || return 0
  if [ "$SELF_DELETE" = 1 ]; then
    # never a power-off here: it would stop Compute Engine's max-run-duration clock, and with no watcher nothing
    # would ever delete the stopped instance; its 120 GB disk would bill until a human noticed (2026-10-07 review)
    if [ "$in_bucket" = 1 ]; then
      say "this run's archive is in $BUCKET; deleting the instance"
    else
      say "NO archive of this run reached $BUCKET (the copies made after earlier stages stand); deleting the instance anyway: powered off it would bill its disk with nothing left to delete it"
    fi
    tries=3; [ "$TERMINATING" -eq 1 ] && tries=1
    self_delete "$tries" || say "self-delete failed; NOT powering off: the instance stays up until the idle guard (it retries the delete every 5 min) or Compute Engine's max-run-duration deletes it"
  else
    say "powering off in 3 minutes (EXIT trap; without --self-delete the watcher deletes the stopped instance); cancel with: sudo shutdown -c"
    sudo shutdown -h +3 "pipeline finished rc=$rc" || true
  fi
}
trap finish EXIT
trap 'TERMINATING=1; say "SIGTERM received (system stop?)"; exit 143' TERM
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
#      (2026-10-06: the key this stage sets was removed as a dead switch, docs/MERGE_MODEL.md A4; the stage now
#      fails at validation by design; reproduce its result with release 2.5.0.)
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
#      (2026-10-06: the key this stage sets was removed as a dead switch, docs/MERGE_MODEL.md A4; the stage now
#      fails at validation by design; reproduce its result with release 2.5.0.)
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

# NOTE (2026-10-04, WP-98; config-hash policy v3, docs/CONTRACTS.md §2). AVSpec.emergency_handback,
#     release_off_corridor and observe_close_leader are now ON by default (and the scripted merge's force_guard), and
#     every config hash moved. Stages 18 and 19 below were written for the old defaults and are not changed here: their
#     "_cc" / "_wp96c" arms ("the committed configuration unchanged") would now run with all three keys on, would not
#     hash like the committed runs and would not reproduce them; the "_hb" / "_wp96f" / "_wp96fh" copies would differ from
#     those arms only in their names. Reproducing the old command path needs a copy that sets the three keys false.
# 18. The AV command handback (WP-95, 2026-09-26; opt-in; needs no data set: launch with --data-set none). Every
#     controller result so far drove its compliant AVs by vehicle.setSpeed under SUMO's default speed mode, whose
#     maximum-deceleration clamp overrides its safe-speed clamp: a commanded AV never brakes harder than its b (IDM:
#     max(b, 1.5)), where its own model and every human may brake at 9 m/s². The strategy sweep (stage 11) recorded 311
#     collisions, all in its FollowerStopper cells, 305 with a compliant AV behind (docs/I24_STRATEGIES.md, 2026-09-26
#     section). AVSpec.emergency_handback (off by default, hash-neutral) hands the AV back to its model in the steps that
#     need more. A "_hb" stage runs a sweep on a copy of its committed scenario that differs only in its name and
#     av.emergency_handback: true (no effect in the cells without a controller, whose metrics must reproduce the committed
#     ones to the digit: a check), with the committed grid and seed list, so every run pairs by seed with the committed run.
#     A "_cc" stage runs the committed configuration unchanged (corridor_sweep.py's cells hash like the committed ones,
#     checked 2026-09-26) for results whose runs predate the collision counter (2026-09-16): it measures how many
#     collisions they contained. meta.json (n_collisions, collisions, av_emergency_handback) rides along beside
#     metrics.json, and each tree gets a census (scripts/collision_census.py -> artifacts/collisions_<tree>.json).
#     Diagnostic batteries, not default changes. Nothing here has run.
#   18a. The strategy sweep with the key; its committed runs are the key-off arm (their census is the committed
#        artifacts/collisions_i24_strat_sweep.json). 120 runs; the pool capped at 12 as in stage 11 (about 9 GB a run).
if echo " $STAGES " | grep -q " sweep_i24_strat_hb "; then
  stage sweep_i24_strat_hb bash -c "sed -e 's#^name: i24_replica_flow_speedcal_ramps\$#name: i24_replica_flow_speedcal_ramps_hb#' \
      scenarios/i24_replica_flow_speedcal_ramps.yaml \
      | awk '{print} /^av:\$/ && !d {print \"  emergency_handback: true\"; d=1}' > scenarios/i24_replica_flow_speedcal_ramps_hb.yaml && \
    grep -c '^  emergency_handback: true\$' scenarios/i24_replica_flow_speedcal_ramps_hb.yaml && \
    $RUN scripts/corridor_sweep.py --scenario scenarios/i24_replica_flow_speedcal_ramps_hb.yaml \
      --penetration 0.10 --compliance 1.0 --controllers follower_stopper --strategies none vsl alinea \
      --rho-target-veh-km 29.2 --x-ref 4411.8 --span 2256.2 7637.8 --replicates $REPS --procs $(( PROCS < 12 ? PROCS : 12 )) \
      --out runs/i24_strat_sweep_hb --summary artifacts/sweep_i24_strategies_hb_summary.json && \
    $RUN scripts/collision_census.py --root runs/i24_strat_sweep_hb --out artifacts/collisions_i24_strat_sweep_hb.json" \
    || say "sweep_i24_strat_hb failed; continuing"
fi

#   18b. The penetration x compliance battery (docs/I24_SWEEP.md, artifacts/i24_sweep_summary.json: FollowerStopper at
#        1/2/5/10/15/20 % x 25/50/80/100 % compliance plus the baseline, 20 seeds, on i24_replica_speedcal) through
#        corridor_sweep.py, whose cells (strategy none) are scripts/i24_penetration_sweep.py's: _cc on the committed
#        scenario (the battery's 25 config hashes), _hb on the copy with the key. Metrics on the summary's own metrics_args.
#        500 runs an arm: the 2026-09-18 battery took about 7 h at 30 processes on n2-standard-32. A first pass can run
#        compliance 1.0 alone (140 runs an arm) by editing --compliance here.
I24_BATTERY_GRID="--penetration 0.01 0.02 0.05 0.10 0.15 0.20 --compliance 0.25 0.50 0.80 1.00 --controllers follower_stopper --strategies none"
I24_BATTERY_METRICS="--x-ref 4411.802228308958 --span 2256.2172746009055 7637.830000000003"
if echo " $STAGES " | grep -q " sweep_i24_cc "; then
  stage sweep_i24_cc bash -c "$RUN scripts/corridor_sweep.py --scenario scenarios/i24_replica_speedcal.yaml $I24_BATTERY_GRID \
      $I24_BATTERY_METRICS --replicates $REPS --procs $PROCS \
      --out runs/i24_sweep_cc --summary artifacts/sweep_i24_penetration_cc_summary.json && \
    $RUN scripts/collision_census.py --root runs/i24_sweep_cc --out artifacts/collisions_i24_sweep_cc.json" \
    || say "sweep_i24_cc failed; continuing"
fi
if echo " $STAGES " | grep -q " sweep_i24_hb "; then
  stage sweep_i24_hb bash -c "sed -e 's#^name: i24_replica_speedcal\$#name: i24_replica_speedcal_hb#' scenarios/i24_replica_speedcal.yaml \
      | awk '{print} /^av:\$/ && !d {print \"  emergency_handback: true\"; d=1}' > scenarios/i24_replica_speedcal_hb.yaml && \
    grep -c '^  emergency_handback: true\$' scenarios/i24_replica_speedcal_hb.yaml && \
    $RUN scripts/corridor_sweep.py --scenario scenarios/i24_replica_speedcal_hb.yaml $I24_BATTERY_GRID \
      $I24_BATTERY_METRICS --replicates $REPS --procs $PROCS \
      --out runs/i24_sweep_hb --summary artifacts/sweep_i24_penetration_hb_summary.json && \
    $RUN scripts/collision_census.py --root runs/i24_sweep_hb --out artifacts/collisions_i24_sweep_hb.json" \
    || say "sweep_i24_hb failed; continuing"
fi

#   18c. The US-101 penetration sweep (docs/US101_PENETRATION.md; stage 14's configuration: scenarios/us101_replica.yaml with
#        the network.boundary block of scenarios/us101_replica_calibrated.yaml, written out by scripts/us101_penetration_
#        sweep.py's own _base_with_boundary(), config hash ab879e240aed; baseline and FollowerStopper at 1/2/5/10/20 %,
#        100 % compliance, 20 seeds): _cc as is (name us101_replica, so the cells hash like the committed sweep's), _hb
#        with the key. Metrics on the replica itself (x 640-1,280 m, throughput at 960 m, as the lane-change artifact's
#        site metrics; docs/US101_PENETRATION.md correction note). 120 runs an arm, minutes.
US101_GRID="--penetration 0.01 0.02 0.05 0.10 0.20 --compliance 1.0 --controllers follower_stopper --strategies none \
  --x-ref 960 --span 640 1280"
for ARM in cc hb; do
  if echo " $STAGES " | grep -q " us101_penetration_$ARM "; then
    SCN=scenarios/us101_replica_boundary_$ARM.yaml
    stage us101_penetration_$ARM bash -c "$RUN -c 'import sys, yaml; sys.path.insert(0, \"scripts\"); \
import us101_penetration_sweep as s; d, src = s._base_with_boundary(); print(src); hb = \"$ARM\" == \"hb\"; \
d.update(name=d[\"name\"] + \"_hb\") if hb else None; d[\"av\"].update(emergency_handback=True) if hb else None; \
open(\"$SCN\", \"w\").write(yaml.safe_dump(d, sort_keys=False))' && \
      $RUN scripts/corridor_sweep.py --scenario $SCN $US101_GRID --replicates $REPS --procs $PROCS \
        --out runs/us101_penetration_$ARM --summary artifacts/sweep_us101_penetration_${ARM}_summary.json && \
      $RUN scripts/collision_census.py --root runs/us101_penetration_$ARM --out artifacts/collisions_us101_penetration_$ARM.json" \
      || say "us101_penetration_$ARM failed; continuing"
  fi
done

#   18d. The single-lane synthetic corridor (docs/CONTROLLER_COMPARISON.md, docs/M3_RESULTS.md; scenarios/corridor_10km.yaml,
#        EIDM, one lane: no cut-ins, but a leader braking harder than the AV's b meets the same clamp) at the comparison
#        point: FollowerStopper, PI with saturation and JAD (perfect oracle) at 5 %, 100 % compliance, plus the baseline,
#        20 seeds; _cc as committed, _hb with the key. Metrics where scripts/m3_analyze_sweep.py places them (throughput at
#        7,000 m, span 2,000-11,500 m) but with the warm-up discarded (the 2026-09-17 definitions), so _cc does not
#        reproduce the 2026-08-30 tables to the digit: it is _hb's paired reference. 80 runs an arm.
TENKM_GRID="--penetration 0.05 --compliance 1.0 --controllers follower_stopper pi_saturation jad --strategies none \
  --x-ref 7000 --span 2000 11500"
if echo " $STAGES " | grep -q " controllers_10km_cc "; then
  stage controllers_10km_cc bash -c "$RUN scripts/corridor_sweep.py --scenario scenarios/corridor_10km.yaml $TENKM_GRID \
      --replicates $REPS --procs $PROCS --out runs/controllers_10km_cc --summary artifacts/sweep_controllers_10km_cc_summary.json && \
    $RUN scripts/collision_census.py --root runs/controllers_10km_cc --out artifacts/collisions_controllers_10km_cc.json" \
    || say "controllers_10km_cc failed; continuing"
fi
if echo " $STAGES " | grep -q " controllers_10km_hb "; then
  stage controllers_10km_hb bash -c "sed -e 's#^name: corridor_10km\$#name: corridor_10km_hb#' scenarios/corridor_10km.yaml \
      | awk '{print} /^av:\$/ && !d {print \"  emergency_handback: true\"; d=1}' > scenarios/corridor_10km_hb.yaml && \
    grep -c '^  emergency_handback: true\$' scenarios/corridor_10km_hb.yaml && \
    $RUN scripts/corridor_sweep.py --scenario scenarios/corridor_10km_hb.yaml $TENKM_GRID \
      --replicates $REPS --procs $PROCS --out runs/controllers_10km_hb --summary artifacts/sweep_controllers_10km_hb_summary.json && \
    $RUN scripts/collision_census.py --root runs/controllers_10km_hb --out artifacts/collisions_controllers_10km_hb.json" \
    || say "controllers_10km_hb failed; continuing"
fi

# 19. The AV command path's two WP-95 side findings (WP-96, 2026-09-26; opt-in; needs no data set: launch with
#     --data-set none). (a) An AV that leaves the corridor by an off-ramp keeps its last setSpeed command until it
#     arrives (AVSpec.release_off_corridor releases it); (b) _leader_obs read a leader closer than the AV's own s0 as
#     "no leader", so FollowerStopper commanded U at the closest gaps (AVSpec.observe_close_leader reports it). Both
#     off by default and hash-neutral; the runner now records both counters in every controller run, on or off
#     (meta.json av_off_corridor, av_close_leader; the census sums them). On two fixtures (20 seeds each,
#     docs/I24_STRATEGIES.md, WP-96 section) neither fix moves a corridor metric by a resolved amount; under (a) the
#     AVs drive the off-ramps at 31-36 % of the limit. A "_wp96c" stage re-runs a committed configuration at this tree (it should reproduce the
#     committed metrics to the digit: a check, where a macOS/Linux difference would also show, WP-85) to count both
#     defects on the published path; a "_wp96f" stage runs a
#     copy that differs only in its name and both keys; "_wp96fh" adds av.emergency_handback: true (pairs with stage
#     18's "_hb"). Every run pairs by seed. Diagnostic batteries, not default changes. Nothing here has run.
wp96_copy() {  # <committed scenario> <suffix> <av key>...: a copy that differs only in its name and the keys
  local src="$1" sfx="$2"; shift 2
  local name; name=$(sed -n 's/^name: //p' "$src" | head -1)
  local dst="scenarios/${name}_${sfx}.yaml" ins=""
  for k in "$@"; do ins="$ins print \"  $k: true\";"; done
  sed -e "s#^name: ${name}\$#name: ${name}_${sfx}#" "$src" | awk "{print} /^av:\$/ && !d {$ins d=1}" > "$dst"
  [ "$(grep -c -E "^  ($(echo "$@" | tr ' ' '|')): true\$" "$dst")" -eq "$#" ] && echo "$dst"
}
export -f wp96_copy  # the stages below call it inside bash -c
#   19a. The strategy sweep (stage 11's grid, as 18a): committed configuration re-run, both keys, both keys with the
#        handback. 120 runs an arm, the pool capped at 12 (about 9 GB a run); about 2 h an arm (18a took 121 min).
STRAT_ARGS="--penetration 0.10 --compliance 1.0 --controllers follower_stopper --strategies none vsl alinea \
  --rho-target-veh-km 29.2 --x-ref 4411.8 --span 2256.2 7637.8"
for ARM in wp96c wp96f wp96fh; do
  if echo " $STAGES " | grep -q " sweep_i24_strat_$ARM "; then
    stage sweep_i24_strat_$ARM bash -c "set -e; SCN=scenarios/i24_replica_flow_speedcal_ramps.yaml; \
      case $ARM in wp96f) SCN=\$(wp96_copy \$SCN wp96f release_off_corridor observe_close_leader) ;; \
        wp96fh) SCN=\$(wp96_copy \$SCN wp96fh release_off_corridor observe_close_leader emergency_handback) ;; esac; \
      $RUN scripts/corridor_sweep.py --scenario \$SCN $STRAT_ARGS --replicates $REPS --procs $(( PROCS < 12 ? PROCS : 12 )) \
        --out runs/i24_strat_sweep_$ARM --summary artifacts/sweep_i24_strategies_${ARM}_summary.json; \
      $RUN scripts/collision_census.py --root runs/i24_strat_sweep_$ARM --out artifacts/collisions_i24_strat_sweep_$ARM.json" \
      || say "sweep_i24_strat_$ARM failed; continuing"
  fi
done
#   19b. The penetration x compliance battery with both keys (500 runs, about 7 h at 30 processes). Its pair is stage
#        18b's sweep_i24_cc, which, run at this tree, records both counters on the committed path: launch the two
#        together (about $22 for both, VM AH's estimate). A first pass can run compliance 1.0 alone (140 runs an arm).
if echo " $STAGES " | grep -q " sweep_i24_wp96f "; then
  stage sweep_i24_wp96f bash -c "set -e; SCN=\$(wp96_copy scenarios/i24_replica_speedcal.yaml wp96f release_off_corridor observe_close_leader); \
    $RUN scripts/corridor_sweep.py --scenario \$SCN $I24_BATTERY_GRID $I24_BATTERY_METRICS --replicates $REPS --procs $PROCS \
      --out runs/i24_sweep_wp96f --summary artifacts/sweep_i24_penetration_wp96f_summary.json; \
    $RUN scripts/collision_census.py --root runs/i24_sweep_wp96f --out artifacts/collisions_i24_sweep_wp96f.json" \
    || say "sweep_i24_wp96f failed; continuing"
fi
#   19c. US-101 (as 18c; no off-ramp, so only (b) can act) and the synthetic 10 km corridor (as 18d; one lane, no ramp:
#        only (b), and only for FollowerStopper and PI with saturation): committed configuration re-run, and both keys.
#        120 and 80 runs an arm, minutes.
for ARM in wp96c wp96f; do
  if echo " $STAGES " | grep -q " us101_penetration_$ARM "; then
    SCN=scenarios/us101_replica_boundary_$ARM.yaml
    stage us101_penetration_$ARM bash -c "$RUN -c 'import sys, yaml; sys.path.insert(0, \"scripts\"); \
import us101_penetration_sweep as s; d, src = s._base_with_boundary(); print(src); fix = \"$ARM\" == \"wp96f\"; \
d.update(name=d[\"name\"] + \"_wp96f\") if fix else None; \
d[\"av\"].update(release_off_corridor=True, observe_close_leader=True) if fix else None; \
open(\"$SCN\", \"w\").write(yaml.safe_dump(d, sort_keys=False))' && \
      $RUN scripts/corridor_sweep.py --scenario $SCN $US101_GRID --replicates $REPS --procs $PROCS \
        --out runs/us101_penetration_$ARM --summary artifacts/sweep_us101_penetration_${ARM}_summary.json && \
      $RUN scripts/collision_census.py --root runs/us101_penetration_$ARM --out artifacts/collisions_us101_penetration_$ARM.json" \
      || say "us101_penetration_$ARM failed; continuing"
  fi
  if echo " $STAGES " | grep -q " controllers_10km_$ARM "; then
    stage controllers_10km_$ARM bash -c "set -e; SCN=scenarios/corridor_10km.yaml; \
      [ $ARM = wp96f ] && SCN=\$(wp96_copy \$SCN wp96f release_off_corridor observe_close_leader); \
      $RUN scripts/corridor_sweep.py --scenario \$SCN $TENKM_GRID --replicates $REPS --procs $PROCS \
        --out runs/controllers_10km_$ARM --summary artifacts/sweep_controllers_10km_${ARM}_summary.json; \
      $RUN scripts/collision_census.py --root runs/controllers_10km_$ARM --out artifacts/collisions_controllers_10km_$ARM.json" \
      || say "controllers_10km_$ARM failed; continuing"
  fi
done

# 20. Stage 1 of the Frisco plan, phase 1 (2026-10-04; docs/FRISCO_PROTOCOL.md): the crash fixes on by default (WP-98)
#     checked on the controller results they protect, and every new phase-1 tool rehearsed on the Minnesota corridor's
#     real data. Diagnostic: nothing here changes a default or a committed result; every artifact is written under a
#     new "_p1" name beside the committed one it pairs with.
#   20a. The committed controller sweeps re-run at this tree, whose defaults turn on emergency_handback,
#        release_off_corridor and observe_close_leader (config-hash policy v3): pair with the committed "_hb" arms
#        (handback alone) seed by seed; the cells without a controller must reproduce them to the digit (a check).
#        Each copy differs from its committed scenario only in its name.
P1_RENAME() {  # <src scenario> <dst scenario>: a copy that differs only in its name (suffix _p1def)
  local name; name=$(sed -n 's/^name: //p' "$1" | head -1)
  sed -e "s#^name: ${name}\$#name: ${name}_p1def#" "$1" > "$2" && grep -q "^name: ${name}_p1def\$" "$2"
}
export -f P1_RENAME
stage p1_controllers_10km_def bash -c "set -e; P1_RENAME scenarios/corridor_10km.yaml scenarios/corridor_10km_p1def.yaml; \
  $RUN scripts/corridor_sweep.py --scenario scenarios/corridor_10km_p1def.yaml $TENKM_GRID --replicates $REPS --procs $PROCS \
    --out runs/controllers_10km_p1def --summary artifacts/sweep_controllers_10km_p1def_summary.json; \
  $RUN scripts/collision_census.py --root runs/controllers_10km_p1def --out artifacts/collisions_controllers_10km_p1def.json" \
  || say "p1_controllers_10km_def failed; continuing"
stage p1_us101_def bash -c "set -e; $RUN -c 'import sys, yaml; sys.path.insert(0, \"scripts\"); \
import us101_penetration_sweep as s; d, src = s._base_with_boundary(); print(src); d.update(name=d[\"name\"] + \"_p1def\"); \
open(\"scenarios/us101_replica_boundary_p1def.yaml\", \"w\").write(yaml.safe_dump(d, sort_keys=False))'; \
  $RUN scripts/corridor_sweep.py --scenario scenarios/us101_replica_boundary_p1def.yaml $US101_GRID --replicates $REPS --procs $PROCS \
    --out runs/us101_penetration_p1def --summary artifacts/sweep_us101_penetration_p1def_summary.json; \
  $RUN scripts/collision_census.py --root runs/us101_penetration_p1def --out artifacts/collisions_us101_penetration_p1def.json" \
  || say "p1_us101_def failed; continuing"
stage p1_i24_strat_def bash -c "set -e; P1_RENAME scenarios/i24_replica_flow_speedcal_ramps.yaml scenarios/i24_replica_flow_speedcal_ramps_p1def.yaml; \
  $RUN scripts/corridor_sweep.py --scenario scenarios/i24_replica_flow_speedcal_ramps_p1def.yaml $STRAT_ARGS --replicates $REPS \
    --procs $(( PROCS < 12 ? PROCS : 12 )) --out runs/i24_strat_sweep_p1def --summary artifacts/sweep_i24_strategies_p1def_summary.json; \
  $RUN scripts/collision_census.py --root runs/i24_strat_sweep_p1def --out artifacts/collisions_i24_strat_sweep_p1def.json" \
  || say "p1_i24_strat_def failed; continuing"
#   20b. The Minnesota corridor's reference battery (stage 10o's _xlsfg recipe, VM AG) at this tree: the scripted
#        merges' force_guard is now the default, so the scenario is the same configuration and its seeds the same
#        (spawn_seeds of the master seed): the battery must reproduce VM AG's artifact to the digit (a check of WP-98),
#        written to a "_p1" artifact. Its run tree is the rehearsal's baseline below.
P1R=runs/p1_rehearsal
stage p1_mndot_ref bash -c "set -e; sed -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0}#' scenarios/${MNDOT}_weave.yaml \
      | awk '{print} /^  kind: osm\$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' \
      | awk '/^    merge: scripted\$/ {s=1; print; next} s && /^    merge_params: \{\}\$/ {print \"    merge_params: {force_guard: 1.0}\"; s=0; next} {s=0; print}' \
      | sed -e 's#^name: ${MNDOT}_weave\$#name: ${MNDOT}_weave_xlsfg#' > scenarios/${MNDOT}_weave_xlsfg.yaml; \
    [ \$(grep -c '^    merge_params: {force_guard: 1.0}\$' scenarios/${MNDOT}_weave_xlsfg.yaml) -eq 2 ]; \
    $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave_xlsfg.yaml \
      --observations data/mndot/$MNDOT/observations.json --replicates $REPS --procs $PROCS \
      --out runs/${MNDOT}_weave_xlsfg_p1/baseline --artifact artifacts/validation_${MNDOT}_weave_xlsfg_p1.json \
      --report-dir docs/reports/${MNDOT}_weave_xlsfg_p1 --criteria-profile fhwa_tat3_2004" || say "p1_mndot_ref failed; continuing"
#   20c. The phase-1 tools on the corridor's real data (WP-101..WP-104), in the order a study runs them: the 30-s
#        archive fetched for all nine dates (fills the per-lane cache), data quality (stations, then lanes blind:
#        loop 3240 left in, so the rules are tested on the known faults), ramp estimation with leave-one-out against
#        the measured ramps, the layout audit, the driver-settings check, the day split, the day-set observations with
#        their wave context, the baseline gate on 20b's battery, and the report regenerated with its client summary.
MN_DATES="20260901,20260902,20260903,20260908,20260909,20260910,20260915,20260916,20260917"
MN_FETCH="--corridor 'I-94 WB' --from-station S1063 --to-station S97 --window-s 300 --t0 05:30 --duration-s 14400 \
  --exclude-detectors 3240 --exclude-reason 'S792 lane-3 loop chatters (docs/ONBOARDING_MNDOT.md section 7 item 2)' --wave-context"
stage p1_fetch_all bash -c "set -e; mkdir -p $P1R; $RUN scripts/mndot_fetch.py $MN_FETCH --dates $MN_DATES --out $P1R/fetch_all" \
  || say "p1_fetch_all failed; continuing"
stage p1_data_quality bash -c "set -e; \
  $RUN scripts/data_quality_report.py --corridor-dir data/mndot/$MNDOT --start 05:30 --end 09:30 --out $P1R/dq; \
  $RUN scripts/data_quality_report.py --corridor-dir data/mndot/$MNDOT --lanes-from-cache data/mndot/cache \
    --metro-config data/mndot/config/metro_config.xml.gz --allow-fetch --start 05:30 --end 09:30 --out $P1R/dq_lanes_blind" \
  || say "p1_data_quality failed; continuing"
stage p1_ramp_estimate bash -c "set -e; \
  $RUN scripts/ramp_estimate.py --corridor-dir data/mndot/$MNDOT --leave-one-out --out $P1R/ramp_loo_raw; \
  $RUN scripts/ramp_estimate.py --corridor-dir data/mndot/$MNDOT --apply-quality --leave-one-out --out $P1R/ramp_loo_quality" \
  || say "p1_ramp_estimate failed; continuing"
stage p1_layout bash -c "$RUN scripts/layout_audit.py --scenario scenarios/${MNDOT}_weave.yaml --out $P1R/layout" \
  || say "p1_layout failed; continuing"
stage p1_transfer bash -c "set -e; \
  $RUN scripts/transfer_check.py --corridor-dir data/mndot/$MNDOT --scenario scenarios/${MNDOT}_weave.yaml --out $P1R/transfer; \
  $RUN scripts/transfer_check.py --corridor-dir data/mndot/$MNDOT --lanes-from-cache data/mndot/cache \
    --metro-config data/mndot/config/metro_config.xml.gz --exclude-detectors 3240 \
    --scenario scenarios/${MNDOT}_weave.yaml --out $P1R/transfer_lanes" \
  || say "p1_transfer failed; continuing"
stage p1_gate bash -c "set -e; \
  $RUN scripts/station_selection.py --quality $P1R/dq/data_quality.json --base data/mndot/$MNDOT/selection.json \
    --out $P1R/selection.json; \
  $RUN scripts/day_split.py --corridor-dir data/mndot/$MNDOT --start 05:30 --end 09:30 --quality $P1R/dq/data_quality.json \
    --selection $P1R/selection.json --out $P1R/day_split.json; \
  for SET in calibration validation; do \
    D=\$($RUN -c \"import json; print(','.join(d.replace('-', '') for d in json.load(open('$P1R/day_split.json'))['\${SET}_dates']))\"); \
    $RUN scripts/mndot_fetch.py $MN_FETCH --dates \$D --out $P1R/fetch_\$SET; \
    $RUN scripts/observations_for_dates.py --corridor-dir data/mndot/$MNDOT --like data/mndot/$MNDOT/observations.json \
      --split $P1R/day_split.json --set \$SET --context-from $P1R/fetch_\$SET/observations.json \
      --quality $P1R/dq/data_quality.json --out $P1R/observations_\$SET.json; \
  done; \
  mkdir -p $P1R/per_day; \
  for D in \$($RUN -c \"import json; print(' '.join(json.load(open('$P1R/day_split.json'))['validation_dates']))\"); do \
    $RUN scripts/observations_for_dates.py --corridor-dir data/mndot/$MNDOT --like data/mndot/$MNDOT/observations.json \
      --dates \$D --quality $P1R/dq/data_quality.json --out $P1R/per_day/observations_\$D.json; \
  done; \
  $RUN scripts/baseline_gate.py --battery-artifact artifacts/validation_${MNDOT}_weave_xlsfg_p1.json \
    --calibration-observations $P1R/observations_calibration.json --validation-observations $P1R/observations_validation.json \
    --day-split $P1R/day_split.json --per-day $P1R/per_day --out-json artifacts/baseline_gate_${MNDOT}_p1.json \
    --out-md docs/reports/${MNDOT}_weave_xlsfg_p1/baseline_gate.md; \
  $RUN scripts/corridor_battery.py --scenario scenarios/${MNDOT}_weave_xlsfg.yaml \
    --observations data/mndot/$MNDOT/observations.json --replicates $REPS \
    --out runs/${MNDOT}_weave_xlsfg_p1/baseline --artifact $P1R/validation_${MNDOT}_weave_xlsfg_p1_gated.json \
    --report-dir docs/reports/${MNDOT}_weave_xlsfg_p1 --criteria-profile fhwa_tat3_2004 --criteria-only --baseline-gate \
    --gate-calibration-observations $P1R/observations_calibration.json \
    --gate-validation-observations $P1R/observations_validation.json --gate-day-split $P1R/day_split.json" \
  || say "p1_gate failed; continuing"

#   20d. The strategy tools rehearsed (WP-105, WP-106; not results: designs far below the protocol's minimums are
#        labelled rehearsals by the tools themselves). Uncertainty: the reference battery's scenario, baseline and
#        ALINEA, 4 samples x 2 seeds, driver ranges from 20c's transfer check (16 runs of 4 h). Tuning: the 35-min
#        slice under the reference configuration, ALINEA and VSL, budget 2, 2 tuning and 4 evaluation seeds (22 runs;
#        the slice has no cool-down, so censoring is expected and reported).
stage p1_uncertainty bash -c "set -e; [ -f scenarios/${MNDOT}_weave_xlsfg.yaml ]; \
  $RUN scripts/uncertainty_runs.py --scenario scenarios/${MNDOT}_weave_xlsfg.yaml \
    --arm alinea strategy=alinea rho_target_veh_km=19.9 --samples 4 --seeds 2 \
    --transfer-check $P1R/transfer/transfer_check.json --data-quality $P1R/dq/data_quality.json \
    --headline total_delay_incl_waiting_veh_h \
    --x-ref 11027 --span 1110 11027 --procs 8 --out runs/p1_unc \
    --summary artifacts/uncertainty_${MNDOT}_p1_rehearsal.json" \
  || say "p1_uncertainty failed; continuing"
stage p1_tune bash -c "set -e; sed -e 's#^name: ${MNDOT}_weave_slice\$#name: ${MNDOT}_weave_slice_xlsfg#' \
      -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0}#' scenarios/${MNDOT}_weave_slice.yaml \
      | awk '{print} /^  kind: osm\$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' \
      | awk '/^    merge: scripted\$/ {s=1; print; next} s && /^    merge_params: \{\}\$/ {print \"    merge_params: {force_guard: 1.0}\"; s=0; next} {s=0; print}' \
      > scenarios/${MNDOT}_weave_slice_xlsfg.yaml; \
    [ \$(grep -c '^    merge_params: {force_guard: 1.0}\$' scenarios/${MNDOT}_weave_slice_xlsfg.yaml) -eq 2 ]; \
  $RUN scripts/strategy_tune.py --scenario scenarios/${MNDOT}_weave_slice_xlsfg.yaml --strategies alinea vsl \
    --budget 2 --tuning-seeds 2 --eval-seeds 4 --rho-target-veh-km 19.9 --x-ref 11027 --span 1110 11027 \
    --procs 16 --out runs/p1_tune --summary artifacts/tune_${MNDOT}_weave_slice_xlsfg_p1_rehearsal.json" \
  || say "p1_tune failed; continuing"

#   20e. The ALINEA halves of 20d again after the meter-setup fix (2026-10-04: every metered run of the
#        ramp-guessed I-94 corridor had failed with "'43917735#1-AddedOnRampEdge' is not in list"). Self-contained
#        (launch with --data-set none): writes the reference scenario and the slice variant, re-runs the station
#        data-quality check and the driver check the uncertainty ranges come from, then the same designs as 20d
#        under new names.
stage p1b_strategies bash -c "set -e; mkdir -p $P1R; \
  for V in '' _slice; do \
    sed -e \"s#^name: ${MNDOT}_weave\${V}\\\$#name: ${MNDOT}_weave\${V}_xlsfg#\" -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0}#' \
        scenarios/${MNDOT}_weave\${V}.yaml \
      | awk '{print} /^  kind: osm\$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' \
      | awk '/^    merge: scripted\$/ {s=1; print; next} s && /^    merge_params: \{\}\$/ {print \"    merge_params: {force_guard: 1.0}\"; s=0; next} {s=0; print}' \
      > scenarios/${MNDOT}_weave\${V}_xlsfg.yaml; \
    [ \$(grep -c '^    merge_params: {force_guard: 1.0}\$' scenarios/${MNDOT}_weave\${V}_xlsfg.yaml) -eq 2 ]; \
  done; \
  $RUN scripts/data_quality_report.py --corridor-dir data/mndot/$MNDOT --start 05:30 --end 09:30 --out $P1R/dq; \
  $RUN scripts/transfer_check.py --corridor-dir data/mndot/$MNDOT --scenario scenarios/${MNDOT}_weave.yaml --out $P1R/transfer; \
  $RUN scripts/uncertainty_runs.py --scenario scenarios/${MNDOT}_weave_xlsfg.yaml \
    --arm alinea strategy=alinea rho_target_veh_km=19.9 --samples 4 --seeds 2 \
    --transfer-check $P1R/transfer/transfer_check.json --data-quality $P1R/dq/data_quality.json \
    --headline total_delay_incl_waiting_veh_h --x-ref 11027 --span 1110 11027 --procs 8 --out runs/p1b_unc \
    --summary artifacts/uncertainty_${MNDOT}_p1b_rehearsal.json; \
  $RUN scripts/strategy_tune.py --scenario scenarios/${MNDOT}_weave_slice_xlsfg.yaml --strategies alinea vsl \
    --budget 2 --tuning-seeds 2 --eval-seeds 4 --rho-target-veh-km 19.9 --x-ref 11027 --span 1110 11027 \
    --procs 16 --out runs/p1b_tune --summary artifacts/tune_${MNDOT}_weave_slice_xlsfg_p1b_rehearsal.json" \
  || say "p1b_strategies failed; continuing"


# 21. Stage 1 phase 2, the measured merge model's cheap gates (docs/MERGE_MODEL.md §4, 2026-10-06; owner: no 20-seed
#     battery in this phase). Diagnostic probes, not acceptance. A: I-24 Old Hickory single seed, measured (central and
#     US-101 gap sets; the Hickory Hollow weave too) against lane_change. B: the I-94 35-min slice, 4 seeds, measured
#     against the weave reference (xlsfg). C: the two phase-1 colliding (sample, seed) pairs as 4-h runs on measured.
stage p2_gate_a bash -c "$RUN scripts/i24_merge_experiment.py --base scenarios/i24_replica_flow_speedcal.yaml \
    --variants flow_speedcal flow_speedcal_measured flow_speedcal_mmus101_gaps_measured flow_speedcal_hhweave_measured \
    --procs 4 --out artifacts/i24_merge_experiment_measured.json" || say "p2_gate_a failed; continuing"
P2B=runs/p2_gate_b
stage p2_gate_b bash -c "set -e; mkdir -p $P2B; \
  sed -e 's#^name: ${MNDOT}_weave_slice\$#name: ${MNDOT}_weave_slice_xlsfg#' -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0}#' \
      scenarios/${MNDOT}_weave_slice.yaml \
    | awk '{print} /^  kind: osm\$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' \
    | awk '/^    merge: scripted\$/ {s=1; print; next} s && /^    merge_params: \{\}\$/ {print \"    merge_params: {force_guard: 1.0}\"; s=0; next} {s=0; print}' \
    > scenarios/${MNDOT}_weave_slice_xlsfg.yaml; \
  for SCN in ${MNDOT}_weave_slice_measured ${MNDOT}_weave_slice_xlsfg; do \
    $RUN scripts/corridor_battery.py --scenario scenarios/\$SCN.yaml --observations data/mndot/$MNDOT/observations.json \
      --replicates 4 --procs 4 --out runs/\$SCN/p2b --artifact artifacts/validation_\${SCN}_p2b.json \
      --report-dir docs/reports/\${SCN}_p2b --criteria-profile fhwa_tat3_2004 --keep-trajectories; \
    $RUN scripts/merge_model_selfcheck.py station-flows --station S790 --clock-offset-s 5400 \
      --observations data/mndot/$MNDOT/observations.json \
      --run-dirs \$(dirname runs/\$SCN/p2b/*/*/meta.json) --out artifacts/merge_model_gate_b_s790_\$SCN.json; \
  done" || say "p2_gate_b failed; continuing"
stage p2_gate_c bash -c "$RUN scripts/merge_model_selfcheck.py colliding-pairs \
    --scenario scenarios/${MNDOT}_weave_measured.yaml \
    --uncertainty-artifact artifacts/uncertainty_mndot_i94_wb_stpaul_p1_rehearsal.json \
    --out-root runs/merge_model_gate_c --population-dir runs/merge_model_gate_c/populations \
    --out artifacts/merge_model_gate_c.json" || say "p2_gate_c failed; continuing"

# 22. Amendment-1 driver calibration grid (docs/FRISCO_PROTOCOL.md Amendment 1, docs/DISCHARGE_CALIBRATION.md §3; 2026-10-06,
#     grid, targets, calibration data and selection rule fixed before any run). Mean a_max at the measured mean + k sd,
#     k 0 / 0.25 / 0.5 / 0.75 / 1 (artifacts/idm_i24_capacity_amax_k*.json, scripts/derive_population.py; k 0 is
#     artifacts/idm_i24_capacity.json itself) x lc_keep_right 0 / 0.1 / 0.25 / 0.5 / 1: every pair run as a variant of the
#     corridor's reference (only the fleet's population and keep-right change; pair (0, 0) is the reference, same hash),
#     scored on lane use and discharge, and the amendment's rule applied by scripts/calibrate_driver_grid.py, which writes
#     artifacts/driver_calibration_{i24,i94}.json (grid table, rule, choice, provenance). The script chooses; nobody else.
#     Needs no data set (launch with --data-set none): every input is tracked, and the MnDOT per-lane cache is fetched here.
#   22a. I-24: scenarios/i24_replica_flow_speedcal.yaml, one seed (spawn_seeds(42, 20)[0]), 25 runs of 2 h 10 min; about
#        9 GB each (stage 11's measurement), so the pool is capped at 12 on the 125 GB machine (the script also caps it by
#        the available memory).
#   22b. I-94: the 35-minute slice under the reference configuration (xlsfg, stage p2_gate_b's recipe, built in the script),
#        two seeds, 50 runs. First the per-lane 30-s cache for all nine dates (the per-lane data-quality check needs every
#        day for its day-outlier rule; loop 3240 excluded as in every MnDOT stage), then the observed lane shares on the
#        five calibration days of the committed split (artifacts/p1_rehearsal_2026-10-04/day_split.json), quality-masked.
P3=runs/p3
stage p3_grid_i24 bash -c "set -e; \
  $RUN scripts/calibrate_driver_grid.py --corridor i24 --plan-only; \
  $RUN scripts/calibrate_driver_grid.py --corridor i24 --procs $(( PROCS < 12 ? PROCS : 12 )) \
    --out $P3/grid_i24 --artifact artifacts/driver_calibration_i24.json" || say "p3_grid_i24 failed; continuing"
stage p3_grid_i94 bash -c "set -e; mkdir -p $P3; \
  $RUN scripts/data_quality_report.py --corridor-dir data/mndot/$MNDOT --lanes-from-cache data/mndot/cache \
    --metro-config data/mndot/config/metro_config.xml.gz --allow-fetch --exclude-detectors 3240 \
    --start 05:30 --end 09:30 --out $P3/dq_lanes; \
  $RUN scripts/calibrate_driver_grid.py --corridor i94 \
    --build-observed-lanes artifacts/driver_calibration_i94_observed_lanes.json \
    --lanes-from-cache data/mndot/cache --metro-config data/mndot/config/metro_config.xml.gz \
    --day-split artifacts/p1_rehearsal_2026-10-04/day_split.json --quality $P3/dq_lanes/data_quality.json \
    --exclude-detectors 3240 --allow-fetch; \
  $RUN scripts/calibrate_driver_grid.py --corridor i94 --plan-only; \
  $RUN scripts/calibrate_driver_grid.py --corridor i94 --procs $(( PROCS < 16 ? PROCS : 16 )) \
    --out $P3/grid_i94 --artifact artifacts/driver_calibration_i94.json" || say "p3_grid_i94 failed; continuing"

# 23. Amendment-1 full tests (step 3, "run the full tests once"; docs/FRISCO_PROTOCOL.md Amendment 1 and §6,
#     docs/DISCHARGE_CALIBRATION.md §3; written 2026-10-06 before the grid's choice was known). Each corridor's calibrated
#     scenario is written from the committed grid artifact (artifacts/driver_calibration_{i24,i94}.json, selection.chosen)
#     by scripts/apply_driver_calibration.py — or, when the file is already committed, checked against that artifact
#     (--check: a file for another pair stops that corridor) — and run unchanged, as the amendment requires. Needs the
#     I-24 data set for the I-24 battery (its observed side checks the data hash): launch with the default --data-set i24.
#   p4_i94_battery_gate: scenarios/${MNDOT}_weave_dc.yaml (name ${MNDOT}_weave_xlsfg_dc: the 4-h weave scenario under the
#     reference configuration xlsfg, stage p1_mndot_ref's recipe line for line, with the chosen population and keep-right),
#     20 seeds, full 4 h, against data/mndot/$MNDOT/observations.json, profile fhwa_tat3_2004; then the protocol's baseline
#     gate (scripts/baseline_gate.py) on the committed phase-1 day sets in artifacts/p1_rehearsal_2026-10-04 (calibration-
#     and validation-day targets, both quality-masked: source.quality names dq/data_quality.json, sha256 8cae907dec40;
#     day_split.json, seed 20261004; per_day/ holds the four validation days) -> artifacts/baseline_gate_mndot_dc.json and
#     docs/reports/${MNDOT}_weave_xlsfg_dc/baseline_gate.md; then the report with its client summary (corridor_battery
#     --criteria-only --baseline-gate; the gated copy is artifacts/validation_${MNDOT}_weave_xlsfg_dc_gated.json). The
#     comparison is the uncalibrated reference ON RECORD, not re-run: artifacts/validation_${MNDOT}_weave_xlsfg_p1.json and
#     artifacts/baseline_gate_${MNDOT}_p1.json (stages p1_mndot_ref / p1_gate: same seeds, same observations, same day sets).
#   p4_i24_battery: scenarios/i24_replica_flow_speedcal_dc.yaml (the grid's I-24 reference with the chosen population and
#     keep-right), 20 seeds and the 20-seed ring rows, through scripts/i24_validate.py --scenario/--label dc ->
#     artifacts/i24_validation_dc.json: the criteria rows of the committed fitted arms (artifacts/i24_validation_flow_speedcal.json
#     is the same scenario under the old drivers, the direct pair; artifacts/i24_validation_speedcal.json is the canonical
#     fitted arm), plus the no_collisions row scored from every replicate's collision counter (the collisions block).
#     Trajectories pruned to the first seed afterwards.
#     KNOWN CONFOUNDER: I-24's demand scale (s = 0.800) and ramp levels were fit under the old drivers and are NOT refit
#     here (protocol §7.1 allows a demand refit). Demand fitted to the old, lower discharge can mask or exaggerate the
#     drivers' effect on the GEH and speed rows. The opt-in stage p4_i24_refit (not in the default list) refits the scale
#     on the calibrated drivers with the fitter that set 0.800 (scripts/i24_fit_demand_scale.py, corrected profile,
#     06:30-07:30 fit hour, 07:30-08:30 held out; 12 single-seed runs) and runs the same battery on the refit scenario
#     (-> artifacts/demand_scale_i24_flow_dc.json, scenarios/i24_replica_flow_speedcal_dc_refit.yaml,
#     artifacts/i24_validation_dc_refit.json); about 30-35 min more. Ramp levels stay as fitted.
#   Resilience: each step says when it fails and the next step runs ("... continuing"); a corridor whose scenario cannot be
#     written or checked is skipped. A stage with a failed step gets no done marker; on a resumed run the I-94 battery is
#     not repeated when its artifact and logs/p4_i94_battery_gate.battery.ok exist (only the gate and report re-run).
#   Cost: about 60-75 min of VM time on n2-standard-32 (the I-94 battery 39-48 min at 30 processes, as p1_mndot_ref ran;
#     gate and report minutes; the I-24 battery about 12 min at 30 processes, 20 runs at once as battery_flow ran on
#     2026-09-17; plus the I-24 data upload at launch). Size --cap-min at about twice that (150-180). Example, after the two
#     grid artifacts are committed and pushed (the scenario files may be committed too, or are written here):
#       scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p4 --bucket gs://<bucket> --self-delete --cap-min 180 \
#         --pipeline-args '--stages "p4_i94_battery_gate p4_i24_battery"'
P1A=artifacts/p1_rehearsal_2026-10-04
DC_I94=scenarios/${MNDOT}_weave_dc.yaml
DC_I94_NAME=${MNDOT}_weave_xlsfg_dc
DC_I24=scenarios/i24_replica_flow_speedcal_dc.yaml
p4_scenario() {  # p4_scenario <corridor> <file> [apply args...]: write <file> from the committed choice, or check it
  local c="$1" f="$2" art="artifacts/driver_calibration_$1.json"
  shift 2
  [ -f "$art" ] || { say "p4: $art is missing (the grid's choice is not committed)"; return 1; }
  if [ -f "$f" ]; then
    $RUN scripts/apply_driver_calibration.py --corridor "$c" --artifact "$art" --out "$f" --check "$@"
  else
    $RUN scripts/apply_driver_calibration.py --corridor "$c" --artifact "$art" --out "$f" "$@"
  fi
}
p4_prune() {  # p4_prune <root>: keep the first seed's trajectories of each configuration under <root>, drop the rest
  local h first r
  for h in "$1"/*/; do
    [ -d "$h" ] || continue
    first=$(ls -d "$h"*/ 2>/dev/null | sort | head -1)
    for r in "$h"*/; do [ "$r" = "$first" ] && continue; rm -f "$r/trajectories.parquet"; done
  done
  du -sh "$1" 2>/dev/null || true
}
p4_i94_battery_gate_steps() {
  local rc=0 obs="data/mndot/$MNDOT/observations.json" art="artifacts/validation_${DC_I94_NAME}.json"
  local rep="docs/reports/${DC_I94_NAME}" out="runs/${DC_I94_NAME}/baseline" ok="logs/p4_i94_battery_gate.battery.ok"
  p4_scenario i94 "$DC_I94" || { say "p4_i94_battery_gate: no calibrated I-94 scenario; battery, gate and report skipped"; return 1; }
  if [ -f "$ok" ] && [ -f "$art" ]; then
    say "p4_i94_battery_gate: battery done earlier ($art), not repeated"
  elif $RUN scripts/corridor_battery.py --scenario "$DC_I94" --observations "$obs" --replicates "$REPS" --procs "$PROCS" \
      --out "$out" --artifact "$art" --report-dir "$rep" --criteria-profile fhwa_tat3_2004; then
    touch "$ok"
  else
    say "p4_i94_battery_gate: battery failed; continuing"; rc=1
  fi
  [ -f "$art" ] || { say "p4_i94_battery_gate: no battery artifact; gate and report skipped"; return 1; }
  $RUN scripts/baseline_gate.py --battery-artifact "$art" \
      --calibration-observations "$P1A/observations_calibration.json" \
      --validation-observations "$P1A/observations_validation.json" \
      --day-split "$P1A/day_split.json" --per-day "$P1A/per_day" \
      --out-json artifacts/baseline_gate_mndot_dc.json --out-md "$rep/baseline_gate.md" \
    || { say "p4_i94_battery_gate: baseline gate failed; continuing"; rc=1; }
  $RUN scripts/corridor_battery.py --scenario "$DC_I94" --observations "$obs" --replicates "$REPS" \
      --out "$out" --artifact "artifacts/validation_${DC_I94_NAME}_gated.json" --report-dir "$rep" \
      --criteria-profile fhwa_tat3_2004 --criteria-only --baseline-gate \
      --gate-calibration-observations "$P1A/observations_calibration.json" \
      --gate-validation-observations "$P1A/observations_validation.json" --gate-day-split "$P1A/day_split.json" \
    || { say "p4_i94_battery_gate: gated report failed; continuing"; rc=1; }
  return $rc
}
p4_i24_battery_steps() {
  local rc=0
  p4_scenario i24 "$DC_I24" || { say "p4_i24_battery: no calibrated I-24 scenario; battery skipped"; return 1; }
  $RUN scripts/i24_validate.py --scenario "$DC_I24" --label dc --replicates "$REPS" --procs "$PROCS" \
      --analysis-procs 8 --ring-seeds "$RING" || { say "p4_i24_battery: battery failed; continuing"; rc=1; }
  p4_prune runs/i24_validation/dc
  return $rc
}
p4_i24_refit_steps() {
  local rc=0 base="scenarios/i24_replica_flow_corrected_dc.yaml" scn="scenarios/i24_replica_flow_speedcal_dc_refit.yaml" pop
  p4_scenario i24 "$base" --source scenarios/i24_replica_flow_corrected.yaml \
    || { say "p4_i24_refit: no calibrated corrected-profile base; refit skipped"; return 1; }
  pop=$($RUN -c "import yaml; print(yaml.safe_load(open('$base'))['fleet']['idm_calibration'])") \
    || { say "p4_i24_refit: cannot read the base's population; refit skipped"; return 1; }
  $RUN scripts/i24_fit_demand_scale.py --base corrected --base-yaml "$base" --fleet-artifact "$pop" --procs "$PROCS" \
      --write-scenario --out artifacts/demand_scale_i24_flow_dc.json --scenario-out "$scn" \
      --name i24_replica_flow_speedcal_dc_refit || { say "p4_i24_refit: demand fit failed; battery skipped"; return 1; }
  $RUN scripts/i24_validate.py --scenario "$scn" --label dc_refit --replicates "$REPS" --procs "$PROCS" \
      --analysis-procs 8 --ring-seeds "$RING" || { say "p4_i24_refit: battery failed; continuing"; rc=1; }
  p4_prune runs/i24_validation/dc_refit
  return $rc
}
stage p4_i94_battery_gate p4_i94_battery_gate_steps || say "p4_i94_battery_gate failed; continuing"
stage p4_i24_battery p4_i24_battery_steps || say "p4_i24_battery failed; continuing"
# The same-code reference for I-24 (the committed flow_speedcal arm predates today's code and hash policy): the
# uncalibrated scenario through the same explicit-scenario path, 20 seeds, about 12 min.
stage p4_i24_ref bash -c "$RUN scripts/i24_validate.py --scenario scenarios/i24_replica_flow_speedcal.yaml --label flow_speedcal_ref \
    --replicates $REPS --procs $PROCS --analysis-procs 8 --ring-seeds $RING" || say "p4_i24_ref failed; continuing"
if echo " $STAGES " | grep -q " p4_i24_refit "; then
  stage p4_i24_refit p4_i24_refit_steps || say "p4_i24_refit failed; continuing"
fi

# 24. The I-94 6th Street left-exit probe (docs/I94_LANE_SHARES.md sections 4 and 6.3; written 2026-10-07 before any
#     run). netconvert's ramp guessing compiles the 6th Street LEFT exit (45782590) with a guessed lane on the right and
#     the left lane exit-only, where OSM draws an option lane (microsim.split_audit: through_lane_exit_only). The 35-min
#     slice under the reference configuration (xlsfg), as built (scenarios/${MNDOT}_weave_slice.yaml, the grid's own
#     config hashes 1dc4729644dd / faa4ab5b219c) and fixed (scenarios/${MNDOT}_weave_slice_netfix.yaml, --ramps.unset
#     1001426896,45782590; 892939b1c2fe / e0a582ceaa29) x the grid's reference drivers (k 0, keep-right 0) and the chosen
#     pair (k 1, keep-right 0.1) x 4 seeds (spawn_seeds(42, 4), the first two the grid's) = 16 runs of about 4 GB, read
#     and scored with the grid's own reader and scorer (scripts/i94_netfix_probe.py) -> artifacts/i94_netfix_probe.json:
#     lane RMSE on each network's compared stations and on the common ones, as committed and with S791's labels reversed
#     (the data-quality lane_order check), S97 discharge, shares at every station, collisions, the note's expectations,
#     and whether the as-built runs reproduce the committed grid readings at its two seeds. Needs no data set (launch
#     with --data-set none): every input is tracked. Cost: one wave of 16 runs at --procs 16 (the grid's slice runs took
#     75-95 s each at 16 processes) plus two netconvert compiles, about 3 min on n2-standard-32; with boot and setup
#     through the bucket about 15-20 min billed. Example:
#       scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p5 --bucket gs://<bucket> --self-delete --via-bucket \
#         --data-set none --cap-min 45 --pipeline-args '--stages "p5_i94_netfix_probe"'
stage p5_i94_netfix_probe bash -c "set -e; \
  $RUN scripts/i94_netfix_probe.py --plan-only; \
  $RUN scripts/i94_netfix_probe.py --procs $(( PROCS < 16 ? PROCS : 16 )) \
    --out runs/p5/i94_netfix_probe --artifact artifacts/i94_netfix_probe.json" || say "p5_i94_netfix_probe failed; continuing"

# p6. The merge anticipation reach on I-24 MOTION (M1 of docs/WEAVE_LOSS_DIAGNOSIS.md §6.3; the definition,
#     the method and the pre-registered adoption rule are docs/MERGE_ANTICIPATION.md, written 2026-10-07 before
#     any run; opt-in, needs the launcher's default --data-set i24). Reads the processed 5 Hz westbound table the
#     launch ships (data/i24motion/processed/i24_wb_20221130/trajectories.parquet and its meta.json; 06:00-10:00
#     CST, 42.8 M rows) in the 15-min chunks, span, ramp zones and lanes of stage 12 (scripts/i24_lane_change_gaps.py),
#     each chunk padded by 8 s + the 120 s lookback, plus the committed artifacts/i24_replica_inputs.json (zone
#     landmarks) and artifacts/i24_coverage.json (coverage context). For every confirmed entering change in the Old
#     Hickory acceleration lane and the Hickory Hollow-Bell Road weave it walks back from the change at 0.2 s and
#     records how far the entrant had been beside the gap it entered and since when its speed had matched its new
#     leader's within 1 and 2 m/s, censored where the tracks run out (calibration.merge_anticipation); Kaplan-Meier
#     reach distributions per zone x changer-speed class with 1,000-resample bootstrap intervals; the sensitivities
#     (no fragment bridging, changes at least 200 m past the zone start, the speed definitions, all speed classes);
#     and the value the pre-registered rule proposes, if any. WEAVE_DEFAULTS is not changed. Writes only
#     artifacts/merge_anticipation_i24.json (summaries, the proposal, coverage, provenance with the table's sha256,
#     and a compact per-event table, about 0.5-1 MB), which rides in every archive with artifacts/*.json. Cost: one
#     process, about 2 GB peak; estimated 3-5 min on n2-standard-32 (stage 12 read the same chunks in 15 s and
#     stage 13 with its lookback in 163 s; the walk itself measured 0.12 s on a 338k-row, 50-event synthetic
#     stand-in), so about 15-20 min billed with boot and setup through the bucket. Example:
#       scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p6 --bucket gs://<bucket> --self-delete --via-bucket \
#         --data-set i24 --cap-min 45 --pipeline-args '--stages "p6_i24_anticipation"'
if echo " $STAGES " | grep -q " p6_i24_anticipation "; then
  stage p6_i24_anticipation bash -c "$RUN scripts/measure_merge_anticipation.py --out artifacts/merge_anticipation_i24.json" \
    || say "p6_i24_anticipation failed; continuing"
fi

# p7. Amendment 2 (PROPOSED, docs/FRISCO_PROTOCOL.md; diagnostics, adoption needs the owner): I-24 at mean a_max
#     shifts k = 0.25 and 0.5 (keep-right 0), each with its own demand refit on the corrected profile and a 20-seed
#     battery, read against the reference arm artifacts/i24_validation_flow_speedcal_ref.json. Needs --data-set i24.
#     Cost: per k about 30 min of refit + 12 min of battery on n2-standard-32 (stage p4_i24_refit took about 45 min).
#     Writes scenarios/i24_replica_flow_corrected_dck{025,05}.yaml, scenarios/i24_replica_flow_speedcal_dck{025,05}_refit.yaml,
#     artifacts/demand_scale_i24_flow_dck{025,05}.json, artifacts/i24_validation_dck{025,05}_refit.json.
p7_i24_amax_wave_steps() {
  local rc=0 k tag base scn pop
  for k in 0.25 0.5; do
    tag=$(echo "$k" | tr -d '.')
    base="scenarios/i24_replica_flow_corrected_dck${tag}.yaml"
    scn="scenarios/i24_replica_flow_speedcal_dck${tag}_refit.yaml"
    pop="artifacts/idm_i24_capacity_amax_k${k}.json"
    [ -f "$pop" ] || { say "p7: $pop missing; k=$k skipped"; rc=1; continue; }
    $RUN scripts/apply_driver_calibration.py --corridor i24 --k "$k" --keep-right 0 \
        --source scenarios/i24_replica_flow_corrected.yaml --out "$base" --name "i24_replica_flow_corrected_dck${tag}" --force \
      || { say "p7: k=$k base scenario not written; skipped"; rc=1; continue; }
    $RUN scripts/i24_fit_demand_scale.py --base corrected --base-yaml "$base" --fleet-artifact "$pop" --procs "$PROCS" \
        --write-scenario --out "artifacts/demand_scale_i24_flow_dck${tag}.json" --scenario-out "$scn" \
        --name "i24_replica_flow_speedcal_dck${tag}_refit" || { say "p7: k=$k demand fit failed; battery skipped"; rc=1; continue; }
    $RUN scripts/i24_validate.py --scenario "$scn" --label "dck${tag}_refit" --replicates "$REPS" --procs "$PROCS" \
        --analysis-procs 8 --ring-seeds "$RING" || { say "p7: k=$k battery failed; continuing"; rc=1; }
    p4_prune "runs/i24_validation/dck${tag}_refit"
  done
  return $rc
}
if echo " $STAGES " | grep -q " p7_i24_amax_wave "; then
  stage p7_i24_amax_wave p7_i24_amax_wave_steps || say "p7_i24_amax_wave failed; continuing"
fi

# p8. The I-94 inputs rebuilt on the protocol's calibration days only (docs/I94_CALIBRATION_DAYS.md, written 2026-10-07
#     before any run; opt-in). The committed scenarios are first checked against their recipe
#     (scripts/i94_calibration_days.py --check: two netconvert compiles, no simulation); then, for each, stage p4's I-94
#     sequence: the 20-seed four-hour battery (profile fhwa_tat3_2004), the protocol's baseline gate on the phase-1 day
#     sets, and the gated report. The battery's own --observations are the CALIBRATION-day targets (stage p4 used the
#     nine-day file; the gate artifacts are the like-for-like comparison with artifacts/baseline_gate_mndot_dc.json).
#     Scenarios, in order: _dc_cal (calibration-day inputs, residuals not carried, protocol §2.3); _dc_cal_sf (plus the
#     calibration-day driver check's speed factor 1.3026, §7.2); _dc_cal_netfix (plus the 6th Street left-exit remedy)
#     only if artifacts/i94_netfix_probe.json shows the fix helps, by this rule (fixed now): the probe is complete and,
#     at the calibrated drivers (pair k1.0_kr0.1) on the stations both networks compare, the netfix network's lane-share
#     RMSE is lower, its S97 discharge error is no larger, and it recorded no collision. Outputs per scenario name N:
#     artifacts/validation_N.json, artifacts/baseline_gate_N.json, artifacts/validation_N_gated.json, docs/reports/N/.
#     Resumable: a battery whose artifact and logs/p8_N.battery.ok exist is not repeated. Needs no data set (every input
#     is tracked): launch with --data-set none. Cost (docs/I94_CALIBRATION_DAYS.md §6): per battery about 27-40 min on
#     n2-standard-32 (one wave of 20 at 30 processes; step 3's battery took 1,599 s, phase 1's 2,356 s) and 50-80 min on
#     n2-standard-16 (two waves; pass --procs 10 to keep ten 4-h runs in its 64 GB); gate and report about 2 min each.
#     Three batteries: about 1.5-2.1 h of stage time on -32, 2.6-4.1 h on -16, plus 10-15 min of boot and setup. Example:
#       scripts/gcp/launch_i24_pipeline.sh --vm flowstate-p8 --machine n2-standard-32 --bucket gs://<bucket>/p8 \
#         --self-delete --via-bucket --data-set none --cap-min 270 --pipeline-args '--stages "p8_i94_cal"'
P8_SCENARIOS="${MNDOT}_weave_dc_cal ${MNDOT}_weave_dc_cal_sf"
p8_netfix_helps() {  # exit 0 when artifacts/i94_netfix_probe.json shows the fix helps the calibrated drivers (rule above)
  [ -f artifacts/i94_netfix_probe.json ] || { say "p8: artifacts/i94_netfix_probe.json is missing"; return 1; }
  $RUN -c "
import json, sys
d = json.load(open('artifacts/i94_netfix_probe.json'))
pair = 'k1.0_kr0.1'
a, b = d['results']['as_built'][pair], d['results']['netfix'][pair]
sa, sb = a['scores']['common'], b['scores']['common']
ok = bool(d.get('complete')) and sb['lane_rmse_pp'] < sa['lane_rmse_pp'] \
    and sb['discharge_error'] <= sa['discharge_error'] and b['n_collisions'] == 0
print(f'p8 netfix rule: lane RMSE {sa[\"lane_rmse_pp\"]:.2f} -> {sb[\"lane_rmse_pp\"]:.2f} pp, S97 discharge error '
      f'{sa[\"discharge_error\"]:.3f} -> {sb[\"discharge_error\"]:.3f}, collisions {b[\"n_collisions\"]}: '
      + ('helps' if ok else 'does not help'))
sys.exit(0 if ok else 1)
"
}
p8_one() {  # p8_one <scenario stem>: battery, baseline gate and gated report of scenarios/<stem>.yaml
  local scn="scenarios/$1.yaml" name rc=0 art rep out ok
  name=$(sed -n 's/^name: //p' "$scn" | head -1)
  [ -n "$name" ] || { say "p8: $scn has no name"; return 1; }
  art="artifacts/validation_${name}.json"; rep="docs/reports/${name}"; out="runs/${name}/baseline"
  ok="logs/p8_${name}.battery.ok"
  if [ -f "$ok" ] && [ -f "$art" ]; then
    say "p8: $name battery done earlier ($art), not repeated"
  elif $RUN scripts/corridor_battery.py --scenario "$scn" --observations "$P1A/observations_calibration.json" \
      --replicates "$REPS" --procs "$PROCS" --out "$out" --artifact "$art" --report-dir "$rep" \
      --criteria-profile fhwa_tat3_2004; then
    touch "$ok"
  else
    say "p8: $name battery failed; continuing"; rc=1
  fi
  [ -f "$art" ] || { say "p8: $name has no battery artifact; gate and report skipped"; return 1; }
  $RUN scripts/baseline_gate.py --battery-artifact "$art" \
      --calibration-observations "$P1A/observations_calibration.json" \
      --validation-observations "$P1A/observations_validation.json" \
      --day-split "$P1A/day_split.json" --per-day "$P1A/per_day" \
      --out-json "artifacts/baseline_gate_${name}.json" --out-md "$rep/baseline_gate.md" \
    || { say "p8: $name baseline gate failed; continuing"; rc=1; }
  $RUN scripts/corridor_battery.py --scenario "$scn" --observations "$P1A/observations_calibration.json" \
      --replicates "$REPS" --out "$out" --artifact "artifacts/validation_${name}_gated.json" --report-dir "$rep" \
      --criteria-profile fhwa_tat3_2004 --criteria-only --baseline-gate \
      --gate-calibration-observations "$P1A/observations_calibration.json" \
      --gate-validation-observations "$P1A/observations_validation.json" --gate-day-split "$P1A/day_split.json" \
    || { say "p8: $name gated report failed; continuing"; rc=1; }
  return $rc
}
p8_i94_cal_steps() {
  local rc=0 stem list="$P8_SCENARIOS"
  $RUN scripts/i94_calibration_days.py --check \
    || { say "p8: the committed calibration-day scenarios are not what their recipe gives; nothing run"; return 1; }
  if p8_netfix_helps; then list="$list ${MNDOT}_weave_dc_cal_netfix"; else say "p8: netfix not run (rule above)"; fi
  for stem in $list; do p8_one "$stem" || rc=1; done
  return $rc
}
if echo " $STAGES " | grep -q " p8_i94_cal "; then
  stage p8_i94_cal p8_i94_cal_steps || say "p8_i94_cal failed; continuing"
fi

# 9. Done marker; the EXIT trap builds the final archives (light, then full with the first-seed replicates).
echo "PIPELINE_DONE $(date -u +%FT%TZ)" > logs/PIPELINE_DONE
say "PIPELINE_DONE"
