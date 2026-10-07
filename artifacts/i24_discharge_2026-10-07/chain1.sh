#!/bin/zsh
# Run order registered at 05:57 (prereg.md): after FULL refit, the DS992 levers with the base re-run,
# the plateau check, then the FULL dc arms at 5 seeds. At most 2 SUMO processes at a time.
RUNS=/private/tmp/claude-501/-Users-anshpathak-Desktop-apps-flowstate/b1e30512-d596-4228-ba4a-2c84632c93b4/scratchpad/i24dis/runs
cd /Users/anshpathak/Desktop/apps/flowstate/artifacts/i24_discharge_2026-10-07
while pgrep -f "batch_full.py" > /dev/null; do sleep 10; done
echo "chain start $(date +%H:%M)"
memory_pressure | tail -1
uv run --no-sync python batch_ds.py $RUNS/LEV.jsonl k1_992w L1a_992 L1b_992 L2_992 L3_992 --lc-log 2>&1 | grep -v -i "pyarrow\|Try to"
echo "levers done $(date +%H:%M)"
memory_pressure | tail -1
uv run --no-sync python batch_ds.py $RUNS/LEV.jsonl k0_992w k05_992w 2>&1 | grep -v -i "pyarrow\|Try to"
echo "plateau done $(date +%H:%M)"
memory_pressure | tail -1
uv run --no-sync python batch_full.py $RUNS/FULL.jsonl full_dc full_dc_b200 --n-seeds 5 2>&1 | grep -v -i "pyarrow\|Try to"
echo "chain done $(date +%H:%M)"
