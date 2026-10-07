#!/bin/zsh
# L5 (prereg entry after FULL refit): waits for chain1, then FULL refit with the scaled boundary.
RUNS=/private/tmp/claude-501/-Users-anshpathak-Desktop-apps-flowstate/b1e30512-d596-4228-ba4a-2c84632c93b4/scratchpad/i24dis/runs
cd /Users/anshpathak/Desktop/apps/flowstate/artifacts/i24_discharge_2026-10-07
while pgrep -f "chain1.sh" > /dev/null; do sleep 15; done
echo "chain2 start $(date +%H:%M)"
memory_pressure | tail -1
uv run --no-sync python batch_full.py $RUNS/FULL.jsonl full_refit_l5 2>&1 | grep -v -i "pyarrow\|Try to"
echo "chain2 done $(date +%H:%M)"
