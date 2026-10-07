#!/bin/zsh
# run python on the HEAD snapshot (insulated from concurrent runner.py edits)
H=/private/tmp/claude-501/-Users-anshpathak-Desktop-apps-flowstate/b1e30512-d596-4228-ba4a-2c84632c93b4/scratchpad/weave_diag/head
export PYTHONPATH=$H/packages/api:$H/packages/calibration:$H/packages/controllers:$H/packages/flowstate_core:$H/packages/macrosim:$H/packages/microsim:$H/packages/validation
cd /Users/anshpathak/Desktop/apps/flowstate
exec uv run --no-sync python "$@"
