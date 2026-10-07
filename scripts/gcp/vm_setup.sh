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
printf '%s\n' "$SHA" > .source_commit  # the launched source commit, for the readouts' provenance (the VM's HEAD is a snapshot commit; 2026-10-07)
git init -q && git add -A && git -c user.name=vm -c user.email=vm@local commit -qm "snapshot $SHA" && echo "snapshot committed ($(git rev-parse --short HEAD))"
echo "== data"
tar xf /tmp/i24_data.tar && rm -f /tmp/i24_data.tar /tmp/repo.tar && (du -sh data/i24motion/processed 2>/dev/null || echo "no I-24 data shipped (--data-set none)")
echo "== python workspace"
uv python install 3.12 >/dev/null
uv sync --all-packages --dev >/dev/null
uv run --no-sync python -c "import libsumo; print('libsumo ok')"
mkdir -p logs && chmod +x scripts/gcp/pipeline_i24.sh
echo "== pipeline unit"
# PIPELINE_BUCKET / PIPELINE_SELF_DELETE (from launch_i24_pipeline.sh --bucket/--self-delete) reach the unit's environment
if [ "$(id -u)" -eq 0 ]; then
  # --via-bucket: run from the VM's boot script as root; a system unit (2026-10-06). Ordered after the network
  # (2026-10-07 review): at shutdown systemd stops units in reverse order, so the EXIT trap's bucket copy and
  # self-delete run before the network goes down. A transient unit gets no such ordering by default; the SSH
  # path's user unit has it through user@.service (after systemd-user-sessions, after network.target).
  systemd-run --unit=pipeline --collect \
    --property=Wants=network-online.target --property=After=network-online.target --property=After=network.target \
    --setenv=HOME=/root --setenv=PATH="/root/.local/bin:$PATH" \
    --setenv=PIPELINE_BUCKET="${PIPELINE_BUCKET:-}" --setenv=PIPELINE_SELF_DELETE="${PIPELINE_SELF_DELETE:-0}" \
    bash -lc "cd /root/flowstate && scripts/gcp/pipeline_i24.sh $QUICK ${PIPELINE_ARGS:-}"
  sleep 8; systemctl is-active pipeline; tail -3 logs/pipeline.log
else
  loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER"
  systemd-run --user --unit=pipeline --collect \
    --setenv=PIPELINE_BUCKET="${PIPELINE_BUCKET:-}" --setenv=PIPELINE_SELF_DELETE="${PIPELINE_SELF_DELETE:-0}" \
    bash -lc "cd ~/flowstate && scripts/gcp/pipeline_i24.sh $QUICK ${PIPELINE_ARGS:-}"
  sleep 8; systemctl --user is-active pipeline; tail -3 logs/pipeline.log
fi
echo "== hard cap: $(cat /run/systemd/shutdown/scheduled 2>/dev/null | tr '\n' ' ')"
