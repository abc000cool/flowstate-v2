#!/bin/bash
# Create one on-demand VM, ship the I-24 data, and start scripts/gcp/pipeline_i24.sh
# under systemd with every stop guarantee armed:
#   1. the pipeline's EXIT trap powers the VM off 3 min after it ends (success or failure);
#   2. the VM's startup script arms a hard cap (`shutdown -h +$CAP_MIN`) at every boot,
#      independent of the pipeline;
#   3. scripts/gcp/watch_pipeline.sh (run locally after this) fetches the results and
#      DELETES the instance, and deletes it unconditionally at its own deadline.
# Usage (repo root, pushed commit):
#   scripts/gcp/launch_i24_pipeline.sh [--vm NAME] [--zone Z] [--machine TYPE] [--cap-min 300] [--quick]
set -euo pipefail
VM=flowstate-pipeline; ZONE=us-west1-b; MACHINE=n2-standard-32; CAP_MIN=300; QUICK=""
PROJECT=$(gcloud config get-value project 2>/dev/null)
while [ $# -gt 0 ]; do
  case "$1" in
    --vm) VM="$2"; shift 2 ;;
    --zone) ZONE="$2"; shift 2 ;;
    --machine) MACHINE="$2"; shift 2 ;;
    --cap-min) CAP_MIN="$2"; shift 2 ;;
    --quick) QUICK="--quick"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
REF=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain | grep -v '^??')" ]; then echo "commit and push first (the VM clones $REF)" >&2; exit 2; fi
if ! git merge-base --is-ancestor "$REF" origin/main 2>/dev/null; then echo "HEAD is not on origin/main; push first" >&2; exit 2; fi
STARTUP=$(mktemp)
cat > "$STARTUP" <<EOF
#!/bin/bash
# boot-time hard cap: the machine powers off $CAP_MIN minutes after every boot, whatever runs on it
shutdown -h +$CAP_MIN "boot-time hard cap (${CAP_MIN} min)"
EOF
echo "== creating $VM ($MACHINE, $ZONE, project $PROJECT), hard cap $CAP_MIN min from boot"
gcloud compute instances create "$VM" --project "$PROJECT" --zone "$ZONE" --machine-type "$MACHINE" \
  --image-family debian-12 --image-project debian-cloud --boot-disk-size 120GB --boot-disk-type pd-balanced \
  --metadata-from-file startup-script="$STARTUP" --labels purpose=flowstate-pipeline,autostop=yes >/dev/null
rm -f "$STARTUP"
mkdir -p "$ROOT/logs"; echo "$(date -u +%FT%TZ) $VM $ZONE $PROJECT $REF" > "$ROOT/logs/pipeline_launch.txt"
ssh_cmd() { gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" --quiet --ssh-flag="-o ConnectTimeout=25" --command "$1"; }
echo "== waiting for ssh"
for i in $(seq 1 30); do if ssh_cmd "true" 2>/dev/null; then break; fi; sleep 10; done
echo "== data archive"
DATA=$(mktemp -d)/i24_data.tar
tar cf "$DATA" data/i24motion/processed/i24_wb_20221130 data/i24motion/processed/i24_wb_episodes.pkl \
  data/i24motion/processed/i24_wb_episodes_heavy.pkl data/i24motion/processed/i24_wb_episode_summary.json \
  data/i24motion/processed/i24_wb_episode_summary_heavy.json data/i24motion/auxiliary_information \
  runs/i24_validation/observed_i24.json
ls -la "$DATA" | awk '{print "   ", $5, "bytes"}'
# The repository is private: the code goes up as a git-archive snapshot of HEAD
# (no clone, no token on the VM); scripts/gcp/vm_setup.sh installs the system
# packages and uv, unpacks code and data, syncs the workspace and starts the unit.
REPO_TAR="$(dirname "$DATA")/repo.tar"
git archive --format=tar -o "$REPO_TAR" HEAD
echo "== shipping code snapshot ($(du -h "$REPO_TAR" | cut -f1)) and data ($(du -h "$DATA" | cut -f1))"
gcloud compute scp "$REPO_TAR" "$ROOT/scripts/gcp/vm_setup.sh" "$DATA" "$VM:/tmp/" --project "$PROJECT" --zone "$ZONE" --quiet
rm -rf "$(dirname "$DATA")"
echo "== VM setup and pipeline start (systemd unit 'pipeline', survives logout)"
ssh_cmd "chmod +x /tmp/vm_setup.sh && /tmp/vm_setup.sh $REF $QUICK" 2>&1 | tail -6
echo "== launched. Now run:  scripts/gcp/watch_pipeline.sh --vm $VM --zone $ZONE"
