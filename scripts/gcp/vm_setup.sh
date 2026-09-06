#!/bin/bash
# One-shot VM setup for the FlowState pipeline when the repository is private:
# the code arrives as a git-archive snapshot (repo.tar), the data as i24_data.tar.
set -euo pipefail
SHA="${1:?commit sha}"
QUICK="${2:-}"
echo "== system packages"
sudo apt-get update -qq
for i in $(seq 1 30); do if sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; then sleep 5; else break; fi; done
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  git curl ca-certificates libgl1 libxml2 libatomic1 libx11-6 libxext6 libxrender1 libxi6 libxtst6 \
  libxfixes3 libxrandr2 libxinerama1 libxcursor1 libfontconfig1 libfreetype6 libglib2.0-0 >/dev/null
echo "== uv"
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null; fi
export PATH="$HOME/.local/bin:$PATH"
echo "== code snapshot $SHA"
mkdir -p "$HOME/flowstate" && tar xf /tmp/repo.tar -C "$HOME/flowstate"
cd "$HOME/flowstate"
git init -q && git add -A && git -c user.name=vm -c user.email=vm@local commit -qm "snapshot $SHA" && echo "snapshot committed ($(git rev-parse --short HEAD))"
echo "== data"
tar xf /tmp/i24_data.tar && rm -f /tmp/i24_data.tar /tmp/repo.tar && du -sh data/i24motion/processed
echo "== python workspace"
uv python install 3.12 >/dev/null
uv sync --all-packages --dev >/dev/null
uv run --no-sync python -c "import libsumo; print('libsumo ok')"
mkdir -p logs && chmod +x scripts/gcp/pipeline_i24.sh
echo "== pipeline unit"
loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER"
systemd-run --user --unit=pipeline --collect bash -lc "cd ~/flowstate && scripts/gcp/pipeline_i24.sh $QUICK"
sleep 8; systemctl --user is-active pipeline; tail -3 logs/pipeline.log
echo "== hard cap: $(cat /run/systemd/shutdown/scheduled 2>/dev/null | tr '\n' ' ')"
