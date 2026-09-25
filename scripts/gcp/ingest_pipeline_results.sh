#!/bin/bash
# Unpack a pipeline results archive (scripts/gcp/pipeline_i24.sh -> final.tgz) into the
# repository and rebuild what depends on it: criteria rows re-scored with the published
# sweep grid, the figures and lane profiles of the zip family, and a short summary.
# Usage (repo root): scripts/gcp/ingest_pipeline_results.sh /path/to/final.tgz
set -euo pipefail
TGZ="${1:?usage: ingest_pipeline_results.sh <final.tgz>}"
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
TMP="$(mktemp -d)"
# GNU tar on the VM stores a path listed twice as a hardlink; bsdtar reports those as
# "hardlink pointing to itself" and skips them (the first copy is extracted). Not fatal.
tar xzf "$TGZ" -C "$TMP" 2>"$TMP/tar.err" || echo "tar: $(grep -c . "$TMP/tar.err") warnings (hardlink duplicates are harmless)"
echo "== archive contents"; find "$TMP" -maxdepth 2 | head -20
mkdir -p logs/pipeline_vm
[ -d "$TMP/docs/reports" ] && rsync -a "$TMP/docs/reports/" docs/reports/ && echo "docs/reports updated"
[ -f "$TMP/data/i24motion/processed/i24_wb_episode_positions.json" ] && cp "$TMP/data/i24motion/processed/i24_wb_episode_positions.json" data/i24motion/processed/ && echo "episode positions sidecar installed"
[ -f "$TMP/data/i24motion/processed/i24_wb_lane_change_gaps.parquet" ] && cp "$TMP/data/i24motion/processed/i24_wb_lane_change_gaps.parquet" data/i24motion/processed/ && echo "lane-change gap records installed"
for f in i24_wb_gap_sequences.parquet i24_wb_critical_gap_drivers.parquet; do
  [ -f "$TMP/data/i24motion/processed/$f" ] && cp "$TMP/data/i24motion/processed/$f" data/i24motion/processed/ && echo "critical-gap table $f installed"
done
cp -R "$TMP"/logs/. logs/pipeline_vm/ 2>/dev/null || true
# artifacts and scenarios written by the VM (new families only; the canonical ones are untouched
# except the heavy arm's artifact and the cap sweep)
for f in "$TMP"/artifacts/*.json; do b=$(basename "$f"); case "$b" in
  i24_validation_zip_*|i24_validation_flow_*|i24_validation_tracked.json|i24_validation_corrected.json|i24_validation_speedcal.json|i24_validation_ramps.json|i24_validation_speedcal_heavy.json|us101_validation_calibrated.json|i24_merge_experiment_zipper_jm.json|i24_merge_experiment_scripted.json|i24_merge_experiment_entryflow.json|i24_merge_experiment_ohlevel.json|i24_merge_experiment_heavylanes.json|demand_scale_i24_zip.json|demand_scale_i24_flow.json|i24_boundary_ramps_fit_zip.json|i24_boundary_ramps_fit_flow.json|i24_replica_inputs_zip.json|i24_replica_inputs_flow.json|demand_i24_zip.json|demand_i24_flow.json|i24_cap_sweep_summary.json|i24_sweep_summary.json|i24_merge_experiment_mergefleet.json|idm_i24_merge.json|idm_i24_capacity_equilibrium.json|validation_mndot_*|sweep_mndot_*|i24_lane_change_gaps.json|i24_critical_gaps.json)
    cp "$f" artifacts/"$b"; echo "artifact $b" ;;
esac; done
for f in "$TMP"/scenarios/i24_replica_zip*.yaml "$TMP"/scenarios/i24_replica_flow*.yaml; do [ -f "$f" ] && cp "$f" scenarios/ && echo "scenario $(basename "$f")"; done
# first-seed replicates (figures / lane profiles)
mkdir -p runs
[ -d "$TMP/runs/i24_validation_zip" ] && rsync -a "$TMP/runs/i24_validation_zip/" runs/i24_validation_zip/ && echo "runs: i24_validation_zip"
[ -d "$TMP/runs/i24_validation/speedcal_heavy" ] && rsync -a "$TMP/runs/i24_validation/speedcal_heavy/" runs/i24_validation/speedcal_heavy/ && echo "runs: speedcal_heavy"
[ -d "$TMP/runs/i24_validation_flow" ] && rsync -a "$TMP/runs/i24_validation_flow/" runs/i24_validation_flow/ && echo "runs: i24_validation_flow"
for d in "$TMP"/runs/mndot_*; do [ -d "$d" ] && rsync -a "$d/" "runs/$(basename "$d")/" && echo "runs: $(basename "$d")"; done
for arm in tracked corrected speedcal ramps; do [ -d "$TMP/runs/i24_validation/$arm" ] && rsync -a "$TMP/runs/i24_validation/$arm/" "runs/i24_validation/$arm/" && echo "runs: $arm"; done
[ -d "$TMP/runs/m3_us101" ] && rsync -a "$TMP/runs/m3_us101/" runs/m3_us101/ && echo "runs: m3_us101"
rm -rf "$TMP"
echo "== re-score"
uv run --no-sync python scripts/i24_validate.py --family zip --criteria-only --arms all --ring-seeds 0 2>&1 | grep -v pyarrow | grep "^\[" -A7 | head -60
uv run --no-sync python scripts/i24_validate.py --criteria-only --arms speedcal_heavy --ring-seeds 0 2>&1 | grep -v pyarrow | grep "^\[" -A7
echo "== figures"
uv run --no-sync python scripts/i24_validation_figures.py --family zip 2>&1 | grep -v pyarrow | tail -2
uv run --no-sync python scripts/i24_lane_profile.py --family zip --arms speedcal ramps --out artifacts/i24_lane_profile_zip.json --fig docs/figures/i24_lane_profile_zip.png 2>&1 | grep -v pyarrow | tail -2
echo "== done; see logs/pipeline_vm/pipeline.log and artifacts/i24_cap_sweep_summary.json"
