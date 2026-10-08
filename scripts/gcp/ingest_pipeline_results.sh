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
echo "== archive contents"; { find "$TMP" -maxdepth 2 | head -20; } || true  # head closes the pipe early; under pipefail that was exit 141 (2026-10-07, the p12 ingest)
# every destination exists before anything is copied: GNU rsync and cp on Linux do not create missing parents
# (macOS rsync does; 2026-10-07, the CI failures of the stage-23 ingest test)
mkdir -p logs/pipeline_vm artifacts scenarios docs/reports runs runs/i24_validation
[ -d "$TMP/docs/reports" ] && rsync -a "$TMP/docs/reports/" docs/reports/ && echo "docs/reports updated"
[ -f "$TMP/data/i24motion/processed/i24_wb_episode_positions.json" ] && cp "$TMP/data/i24motion/processed/i24_wb_episode_positions.json" data/i24motion/processed/ && echo "episode positions sidecar installed"
[ -f "$TMP/data/i24motion/processed/i24_wb_lane_change_gaps.parquet" ] && cp "$TMP/data/i24motion/processed/i24_wb_lane_change_gaps.parquet" data/i24motion/processed/ && echo "lane-change gap records installed"
for f in i24_wb_gap_sequences.parquet i24_wb_critical_gap_drivers.parquet; do
  [ -f "$TMP/data/i24motion/processed/$f" ] && cp "$TMP/data/i24motion/processed/$f" data/i24motion/processed/ && echo "critical-gap table $f installed"
done
cp -R "$TMP"/logs/. logs/pipeline_vm/ 2>/dev/null || true
# artifacts and scenarios written by the VM (new families only; the canonical ones are untouched
# except the heavy arm's artifact and the cap sweep). Stage p13 (amendment B2) writes the rc family's inputs and
# demand (i24_replica_inputs_flow_rc.json, demand_i24_flow_rc.json), its two batteries (i24_validation_dc_refit_
# p13ref / _rc, under i24_validation_dc*), their ramp-flow reductions (i24_b2_ramp_flows_<label>.json, which the
# score re-reads: the archive carries at most the first seed's vehicles.parquet) and boundary_b2_corridor.json
# (2026-10-07 review: the reductions, inputs and demand were dropped). Stage p14 (B1 + B2, the demand re-sequence)
# writes its batteries (i24_validation_p14_*), their reductions (i24_b2_ramp_flows_p14_*, taken by the line above),
# the re-sequence's fit (demand_scale_i24_flow_rc[_b1].json) and boundary_b1b2_corridor.json. Stages p15 / p17 (B5)
# write the fits (demand_level_fit_{i24,i94}.json), the readouts (demand_level_{i24,i94}.json), p15's battery
# (i24_validation_p15_*) and its reduction (taken by i24_b2_ramp_flows_*), and p17's battery, gate and gated report
# (validation_mndot_*, baseline_gate_*_b5.json)
# p16 (D10, 2026-10-07) writes i94_d10_repro.json and i94_d10_corridor.json; p21 (E11) the I-80 records (i80_data.json,
# i80_merge_observed.json, i80_merge_sim_<arm>.json, i80_merge_validation.json, i80_replica_inputs.json); p20 (C9) writes
# under artifacts/i24_days_2026-10-07/ (a directory, taken below). p23 (C7b) writes the three families' inputs and demand
# (i24_replica_inputs_flow_{rcs,rcc,rccs}.json, demand_i24_flow_{rcs,rcc,rccs}.json) and the readout
# i24_consistency_c7b.json; its batteries (i24_validation_dc_refit_*) and reductions (i24_b2_ramp_flows_*) and its
# scenarios (i24_replica_flow*.yaml) and run trees (runs/i24_validation/dc*) are taken by the existing lines.
# p24 (Amendment 3's range round) writes the readout a3_range.json and one a3_lanes_<label>.json per battery (the
# kept trajectory's per-lane flows, read on the VM); its batteries, gates and gated reports (validation_mndot_*,
# baseline_gate_*_dc_cal*.json) and run trees (runs/mndot_*) are taken by the existing lines; its scenario copies
# stay on the VM (runs/p24_a3/scenarios/, never archived).
# p25 (B2 on the k = 0 arm, docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.7) writes the readout boundary_b2_k0_corridor.json and
# one braking count i24_hard_braking_<label>.json per battery; its batteries (i24_validation_flow_speedcal_{p25ref,rc})
# are taken by i24_validation_flow_*, its reductions by i24_b2_ramp_flows_* and its arm (i24_replica_flow_rc_speedcal.yaml)
# by i24_replica_flow*.yaml; its run trees stay on the VM.
for f in "$TMP"/artifacts/*.json; do [ -f "$f" ] || continue; b=$(basename "$f"); case "$b" in
  i24_validation_zip_*|i24_validation_flow_*|i24_validation_tracked.json|i24_validation_corrected.json|i24_validation_speedcal.json|i24_validation_ramps.json|i24_validation_speedcal_heavy.json|us101_validation_calibrated.json|i24_merge_experiment_zipper_jm.json|i24_merge_experiment_scripted.json|i24_merge_experiment_entryflow.json|i24_merge_experiment_ohlevel.json|i24_merge_experiment_heavylanes.json|demand_scale_i24_zip.json|demand_scale_i24_flow.json|i24_boundary_ramps_fit_zip.json|i24_boundary_ramps_fit_flow.json|i24_replica_inputs_zip.json|i24_replica_inputs_flow.json|demand_i24_zip.json|demand_i24_flow.json|i24_cap_sweep_summary.json|i24_sweep_summary.json|i24_merge_experiment_mergefleet.json|idm_i24_merge.json|idm_i24_capacity_equilibrium.json|validation_mndot_*|sweep_mndot_*|i24_lane_change_gaps.json|i24_critical_gaps.json|lane_change_relaxation_*|coverage_thinning_*|sweep_*_hb_summary.json|sweep_*_cc_summary.json|collisions_*_hb.json|collisions_*_cc.json|sweep_*_wp96*_summary.json|collisions_*_wp96*.json|i24_validation_dc*.json|validation_*_dc*.json|baseline_gate_*_dc.json|baseline_gate_*_dc_cal*.json|demand_scale_*_dc.json|i94_netfix_probe.json|merge_anticipation_i24.json|weave_w1b_corridor.json|i94_cal_collisions_trace.json|i24_count_consistency.json|weave_w2_corridor.json|i24_validation_p12_*.json|boundary_b1_corridor.json|boundary_b2_corridor.json|i24_b2_ramp_flows_*.json|i24_replica_inputs_flow_rc.json|demand_i24_flow_rc.json|i24_validation_p14_*.json|boundary_b1b2_corridor.json|demand_scale_i24_flow_rc.json|demand_scale_i24_flow_rc_b1.json|demand_level_fit_i24.json|demand_level_fit_i94.json|demand_level_i24.json|demand_level_i94.json|i24_validation_p15_*.json|baseline_gate_*_b5.json|i94_d10_repro.json|i94_d10_corridor.json|i80_data.json|i80_merge_observed.json|i80_merge_sim_*.json|i80_merge_validation.json|i80_replica_inputs.json|i24_replica_inputs_flow_rcs.json|i24_replica_inputs_flow_rcc.json|i24_replica_inputs_flow_rccs.json|demand_i24_flow_rcs.json|demand_i24_flow_rcc.json|demand_i24_flow_rccs.json|i24_consistency_c7b.json|a3_range.json|a3_lanes_*.json|boundary_b2_k0_corridor.json|i24_hard_braking_*.json)
    case "$b" in i24_validation_zip_*|i24_validation_speedcal_heavy.json) cmp -s "$f" "artifacts/$b" || ZIP_FAMILY=1 ;; esac
    cp "$f" artifacts/"$b"; echo "artifact $b" ;;
  *)  # not on the list: say so when it is new or differs from the repository's copy (2026-10-07 review: stage 23's
      # calibrated batteries and gate were dropped without a word)
    if [ ! -f "artifacts/$b" ] || ! cmp -s "$f" "artifacts/$b"; then echo "artifact $b NOT ingested (new or changed, not on the list; copy by hand if wanted)"; fi ;;
esac; done
# scenarios written by the VM: the I-24 zip/flow families; the handback re-runs' copies (WP-95, stage 18: *_hb.yaml, and
# the US-101 boundary copies); the command-path re-runs' (WP-96, stage 19: *_wp96f.yaml, *_wp96fh.yaml); stage 23's
# calibrated-driver scenarios (*_dc.yaml) and every variant of them (*_dc_*.yaml: the I-24 demand refit *_dc_refit,
# p8's *_dc_cal*, p9's W1b copy *_dc_w1b; 2026-10-07 review: the W1b copy its battery names was dropped). Stage
# p13's rc scenarios fall under i24_replica_flow*.yaml (i24_replica_flow_rc{,_corrected,_corrected_dc,
# _speedcal_dc_refit}.yaml), and so do stage p14's (i24_replica_flow_rc_speedcal_dc_refit_b1, _rc_corrected_dc_b1,
# _rc_speedcal_dc_refit2[_b1]); stage p15's arm (i24_replica_flow_rc_speedcal_dc_b5) too, and stage p17's
# (<from-arm stem>_b5.yaml, by the *_b5.yaml case whatever D10's arm is). Any other scenario that is new or differs from
# the repository's copy is named, as the artifacts are.
ingested_scenario() {
  case "$1" in
    i24_replica_zip*.yaml|i24_replica_flow*.yaml|*_hb.yaml|us101_replica_boundary_*.yaml|*_wp96f.yaml|*_wp96fh.yaml) return 0 ;;
    *_dc.yaml|*_dc_*.yaml|*_b5.yaml) return 0 ;;
  esac
  return 1
}
for f in "$TMP"/scenarios/*.yaml; do
  [ -f "$f" ] || continue
  b=$(basename "$f")
  if ingested_scenario "$b"; then
    cp "$f" scenarios/"$b"; echo "scenario $b"
    # a copy made by sed keeps its source's header, which names the source and its config hash
    head_name=$(head -1 "$f" | sed -n 's/^# \([A-Za-z0-9_.-]*\): .*/\1/p')
    own_name=$(awk '/^name: /{sub(/^name: /, ""); print; exit}' "$f")
    if [ -n "$head_name" ] && [ -n "$own_name" ] && [ "$head_name" != "$own_name" ]; then
      echo "scenario $b: WARNING its header describes $head_name, not its own name $own_name (a copy of another scenario?): the header's provenance and config hash are not this file's; correct the header before committing"
    fi
  elif [ ! -f "scenarios/$b" ] || ! cmp -s "$f" "scenarios/$b"; then
    echo "scenario $b NOT ingested (new or changed, not on the list; copy by hand if wanted)"
  fi
done
# p20 (C9) mirrors artifacts/i24_days_2026-10-07/ into logs/p20_i24_days/ (every make_archive carries logs/ in full).
[ -d "$TMP/logs/p20_i24_days" ] && mkdir -p artifacts/i24_days_2026-10-07 && rsync -a "$TMP/logs/p20_i24_days/" artifacts/i24_days_2026-10-07/ && echo "artifacts: i24_days_2026-10-07 (from logs/p20_i24_days)"
# first-seed replicates (figures / lane profiles)
[ -d "$TMP/runs/i24_validation_zip" ] && rsync -a "$TMP/runs/i24_validation_zip/" runs/i24_validation_zip/ && echo "runs: i24_validation_zip"
[ -d "$TMP/runs/i24_validation/speedcal_heavy" ] && rsync -a "$TMP/runs/i24_validation/speedcal_heavy/" runs/i24_validation/speedcal_heavy/ && echo "runs: speedcal_heavy"
[ -d "$TMP/runs/i24_validation_flow" ] && rsync -a "$TMP/runs/i24_validation_flow/" runs/i24_validation_flow/ && echo "runs: i24_validation_flow"
for d in "$TMP"/runs/mndot_*; do [ -d "$d" ] && rsync -a "$d/" "runs/$(basename "$d")/" && echo "runs: $(basename "$d")"; done
# stage 18's run trees (metrics.json and meta.json per run, WP-95)
for d in "$TMP"/runs/*_hb "$TMP"/runs/*_cc; do [ -d "$d" ] && rsync -a "$d/" "runs/$(basename "$d")/" && echo "runs: $(basename "$d")"; done
# stage 19's run trees (WP-96: _wp96c, _wp96f, _wp96fh)
for d in "$TMP"/runs/*_wp96*; do [ -d "$d" ] && rsync -a "$d/" "runs/$(basename "$d")/" && echo "runs: $(basename "$d")"; done
# stage 23's I-24 batteries (runs/i24_validation/dc, dc_refit: per-replicate meta.json, first-seed trajectories);
# stage p13's two batteries (dc_refit_p13ref, dc_refit_rc) are taken by the same line
for d in "$TMP"/runs/i24_validation/dc*; do [ -d "$d" ] && rsync -a "$d/" "runs/i24_validation/$(basename "$d")/" && echo "runs: i24_validation/$(basename "$d")"; done
# stage 24's probe (readings, meta and lane geometry of every run; the artifact rebuilds from them)
[ -d "$TMP/runs/p5/i94_netfix_probe" ] && mkdir -p runs/p5 && rsync -a "$TMP/runs/p5/i94_netfix_probe/" runs/p5/i94_netfix_probe/ && echo "runs: p5/i94_netfix_probe"
# stage p8c's re-runs (the pair manifest; per run meta.json, vehicles.parquet and the reader's window slice, no trajectories)
[ -d "$TMP/runs/p8c" ] && mkdir -p runs/p8c && rsync -a "$TMP/runs/p8c/" runs/p8c/ && echo "runs: p8c"
# stage p12's batteries (per replicate meta.json and edges.parquet, each battery's braking counts; no trajectories)
for d in "$TMP"/runs/i24_validation/p12_*; do [ -d "$d" ] && rsync -a "$d/" "runs/i24_validation/$(basename "$d")/" && echo "runs: i24_validation/$(basename "$d")"; done
# stage p14's batteries (the same files as p12's)
for d in "$TMP"/runs/i24_validation/p14_*; do [ -d "$d" ] && rsync -a "$d/" "runs/i24_validation/$(basename "$d")/" && echo "runs: i24_validation/$(basename "$d")"; done
# stage p15's battery (the same files as p12's); stage p17's fit runs and battery are runs/mndot_* trees, taken above
for d in "$TMP"/runs/i24_validation/p15_*; do [ -d "$d" ] && rsync -a "$d/" "runs/i24_validation/$(basename "$d")/" && echo "runs: i24_validation/$(basename "$d")"; done
for arm in tracked corrected speedcal ramps; do [ -d "$TMP/runs/i24_validation/$arm" ] && rsync -a "$TMP/runs/i24_validation/$arm/" "runs/i24_validation/$arm/" && echo "runs: $arm"; done
[ -d "$TMP/runs/m3_us101" ] && rsync -a "$TMP/runs/m3_us101/" runs/m3_us101/ && echo "runs: m3_us101"
rm -rf "$TMP"
# The re-score, figure and lane-profile steps below rewrite the 2026-09 zip-family records under the current code; they run
# only when this archive carried a zip-family battery that DIFFERS from the repository's copy (every archive carries the
# committed artifacts directory, so presence alone is no signal; 2026-10-08: the p21 and p16 ingests re-scored six committed batteries).
if [ -n "${ZIP_FAMILY:-}" ]; then
echo "== re-score"
uv run --no-sync python scripts/i24_validate.py --family zip --criteria-only --arms all --ring-seeds 0 2>&1 | grep -v pyarrow | grep "^\[" -A7 | head -60
uv run --no-sync python scripts/i24_validate.py --criteria-only --arms speedcal_heavy --ring-seeds 0 2>&1 | grep -v pyarrow | grep "^\[" -A7
echo "== figures"
uv run --no-sync python scripts/i24_validation_figures.py --family zip 2>&1 | grep -v pyarrow | tail -2
uv run --no-sync python scripts/i24_lane_profile.py --family zip --arms speedcal ramps --out artifacts/i24_lane_profile_zip.json --fig docs/figures/i24_lane_profile_zip.png 2>&1 | grep -v pyarrow | tail -2
else
  echo "== re-score/figures skipped: this archive carried no zip-family battery"
fi
echo "== done; see logs/pipeline_vm/pipeline.log and artifacts/i24_cap_sweep_summary.json"
