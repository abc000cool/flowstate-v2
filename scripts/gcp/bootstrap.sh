#!/usr/bin/env bash
# FlowState compute bootstrap for a fresh Debian 12 VM (GCP or any cloud).
#
# Installs system deps, uv, the workspace (SUMO 1.27.1 from PyPI wheels), and
# starts the I-24 penetration sweep in the background with one worker per
# vCPU minus two. Safe to rerun: a clone is reused, and the sweep is
# resumable, so rerunning after a Spot preemption continues where it stopped.
#
#   curl -fsSL https://raw.githubusercontent.com/abc000cool/flowstate-v2/main/scripts/gcp/bootstrap.sh \
#     | bash -s -- [--procs N] [--scenario i24_replica_corrected] [--ref main] [--no-run]
set -euo pipefail

PROCS=""
SCENARIO="i24_replica_corrected"
REF="main"
RUN=1
while [ $# -gt 0 ]; do
  case "$1" in
    --procs) PROCS="$2"; shift 2 ;;
    --scenario) SCENARIO="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --no-run) RUN=0; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$PROCS" ]; then PROCS=$(( $(nproc) - 2 )); fi
if [ "$PROCS" -lt 1 ]; then PROCS=1; fi

echo "== system packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  git curl ca-certificates libgl1 libxml2 >/dev/null

echo "== uv"
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi
export PATH="$HOME/.local/bin:$PATH"

echo "== repository ($REF)"
if [ ! -d "$HOME/flowstate/.git" ]; then
  git clone -q https://github.com/abc000cool/flowstate-v2.git "$HOME/flowstate"
fi
cd "$HOME/flowstate"
git fetch -q origin
git checkout -q "$REF"
git pull -q --ff-only origin "$REF" || true

echo "== python workspace"
uv python install 3.12 >/dev/null
uv sync --all-packages --dev >/dev/null
uv run --no-sync python -c "import libsumo; print('libsumo ok')"

mkdir -p logs
if [ "$RUN" -eq 0 ]; then
  echo "== ready (no run requested); e.g. uv run --no-sync python scripts/i24_penetration_sweep.py --scenario $SCENARIO --procs $PROCS"
  exit 0
fi

echo "== starting sweep: $SCENARIO with $PROCS workers"
nohup bash -c "
  n=0
  until uv run --no-sync python scripts/i24_penetration_sweep.py --scenario $SCENARIO --procs $PROCS; do
    n=\$((n+1)); if [ \$n -ge 5 ]; then echo 'SWEEP_FAILED after 5 attempts'; exit 1; fi
    echo \"sweep exited non-zero; retrying in 30 s (attempt \$n)\"; sleep 30
  done
  echo SWEEP_DONE
" > logs/sweep.log 2>&1 &
echo "sweep running; follow with: tail -f ~/flowstate/logs/sweep.log"
