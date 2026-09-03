#!/usr/bin/env bash
# Pull the sweep results from a VM into this checkout's runs/i24_sweep.
# The sweep keeps only summaries (trajectories are deleted), so this is small.
#   scripts/gcp/fetch_results.sh <instance-name> [zone]
set -euo pipefail
VM="${1:?usage: fetch_results.sh <instance-name> [zone]}"
ZONE="${2:-us-central1-a}"
ROOT="$(git rev-parse --show-toplevel)"
TMP="$(mktemp -d)"
gcloud compute ssh "$VM" --zone "$ZONE" --command "cd ~/flowstate && tar czf /tmp/i24_sweep.tgz runs/i24_sweep logs"
gcloud compute scp "$VM:/tmp/i24_sweep.tgz" "$TMP/i24_sweep.tgz" --zone "$ZONE"
tar xzf "$TMP/i24_sweep.tgz" -C "$ROOT"
rm -rf "$TMP"
echo "-> $ROOT/runs/i24_sweep ($(find "$ROOT/runs/i24_sweep" -mindepth 4 -maxdepth 4 -name meta.json | wc -l | tr -d ' ') completed runs)"
echo "analyse with: uv run --no-sync python scripts/i24_penetration_analyze.py"
