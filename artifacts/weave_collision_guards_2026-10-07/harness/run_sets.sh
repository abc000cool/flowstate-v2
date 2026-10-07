#!/bin/bash
# Amendment W2's fixture evaluation (docs/I94_CAL_COLLISIONS.md §13.3), from the repository root:
#   run_sets.sh WORK OUT SET...      SET in s1 s2 s3a s3b xr1 xr2 xt1 xt2
# For each set, the reference and the W2 arm run side by side (two SUMO processes at once), with
# trajectories kept; then post.py reads every run tree into OUT/<set>_<arm>_post.json and the trees
# are deleted (disk). Rows go to OUT/<set>_<arm>.json. ARM_KEYS overrides the W2 arm's keys and
# ARM_NAME its name (the diagnostic single-key arms).
set -u
W=$1; OUT=$2; shift 2
DC=scenarios/mndot_i94_wb_stpaul_weave_dc.yaml
P=scripts/merge_model_selfcheck.py
H=artifacts/weave_collision_guards_2026-10-07/harness
RUTH=ruth_entr,ruth_exit,ruth_entr_fleet,ruth_exit_fleet,ruth_exit_fleet_271
REST=th52_corridor_demand,th52_capacity,th52_upstream,th52_upstream_fleet,weave_moderate,weave_golden,th52_corridor,th61
W2_KEYS=${ARM_KEYS:-"--weave-set weave_handback=1 --weave-set weave_close_leader=1 --weave-set weave_resolve_opposing=1"}
W2_NAME=${ARM_NAME:-w2}
RUN="uv run --no-sync python"

cmd_for() {  # cmd_for SET ARM_KEYS WORKDIR OUTJSON
  local s=$1 keys=$2 wd=$3 oj=$4
  case $s in
    s1) echo "$RUN $P th52 --model weave --fleet-from $DC --seeds 3-22 $keys --keep --work-dir $wd --out $oj" ;;
    s2) echo "$RUN $P grid --model weave $keys --keep --work-dir $wd --out $oj" ;;
    s3a) echo "$RUN $P grid --model weave --fleet-from $DC --only $RUTH --seeds 3-22 $keys --keep --work-dir $wd --out $oj" ;;
    s3b) echo "$RUN $P grid --model weave --fleet-from $DC --only $REST $keys --keep --work-dir $wd --out $oj" ;;
    xr1|xr2|xt1|xt2) echo "$RUN $H/repro.py $s $oj $wd --seeds 3-22 $keys" ;;
  esac
}

for S in "$@"; do
  echo "== $S $(date +%T) $(memory_pressure | tail -1)"
  pids=()
  for ARM in ref "$W2_NAME"; do
    keys=""; [ "$ARM" != ref ] && keys=$W2_KEYS
    if [ -f "$OUT/${S}_${ARM}_post.json" ]; then echo "$S $ARM done earlier"; continue; fi
    rm -rf "$W/${S}_${ARM}"
    $(cmd_for "$S" "$keys" "$W/${S}_${ARM}" "$OUT/${S}_${ARM}.json") > "$W/${S}_${ARM}.log" 2>&1 &
    pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p" || echo "$S: a run failed (pid $p)"; done
  for ARM in ref "$W2_NAME"; do
    [ -f "$OUT/${S}_${ARM}_post.json" ] && continue
    $RUN $H/post.py "$W/${S}_${ARM}" "$OUT/${S}_${ARM}_post.json" > "$W/${S}_${ARM}_post.log" 2>&1 \
      && rm -rf "$W/${S}_${ARM}" || echo "$S $ARM: post failed, tree kept"
  done
  echo "== $S done $(date +%T)"
done
