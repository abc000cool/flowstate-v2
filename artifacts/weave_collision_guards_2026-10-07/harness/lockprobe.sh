#!/bin/bash
# Diagnostic (not pre-registered; docs/I94_CAL_COLLISIONS.md §14): the one run in which W2 created a lock,
# th52_upstream_fleet at seed 3 of the 37-run grid (S2), under each W2 key alone, all three, W1b alone and
# W1b + W2. From the repository root: lockprobe.sh WORK OUT. Two SUMO processes at a time; each run is read
# by post.py (locks of §13.4) and deleted. (bash 3.2: no associative arrays.)
set -u
W=$1; OUT=$2
P=scripts/merge_model_selfcheck.py
H=artifacts/weave_collision_guards_2026-10-07/harness
W1B="--weave-set entrant_giveup_m=5 --weave-set entrant_giveup_dwell_s=60"
W2="--weave-set weave_handback=1 --weave-set weave_close_leader=1 --weave-set weave_resolve_opposing=1"
keys_of() {
  case $1 in
    ref) echo "" ;;
    hb) echo "--weave-set weave_handback=1" ;;
    cl) echo "--weave-set weave_close_leader=1" ;;
    op) echo "--weave-set weave_resolve_opposing=1" ;;
    w2) echo "$W2" ;;
    w1b) echo "$W1B" ;;
    w1bw2) echo "$W1B $W2" ;;
  esac
}
one() {
  local arm=$1
  uv run --no-sync python $P grid --model weave --only th52_upstream_fleet --seeds 3 $(keys_of "$arm") \
    --keep --work-dir "$W/lk_$arm" --out "$OUT/lockprobe_$arm.json" > "$W/lk_$arm.log" 2>&1 \
  && uv run --no-sync python $H/post.py "$W/lk_$arm" "$OUT/lockprobe_${arm}_post.json" > "$W/lk_${arm}_post.log" 2>&1 \
  && rm -rf "$W/lk_$arm"
}
set -- ref hb cl op w2 w1b w1bw2
while [ $# -gt 0 ]; do
  one "$1" & a=$!
  if [ $# -gt 1 ]; then one "$2" & b=$!; wait $b; shift; fi
  wait $a; shift
done
