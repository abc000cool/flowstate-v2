#!/bin/zsh
# py.sh TREE args...: run python with TREE's packages first on sys.path (TREE: a
# `git archive` extraction, e.g. HEAD, or HEAD with the changed files copied in),
# from the repository root
H=$1; shift
export PYTHONPATH=$H/packages/api:$H/packages/calibration:$H/packages/controllers:$H/packages/flowstate_core:$H/packages/macrosim:$H/packages/microsim:$H/packages/validation
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
exec uv run --no-sync python "$@"
