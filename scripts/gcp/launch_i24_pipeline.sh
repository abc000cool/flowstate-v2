#!/bin/bash
# Create one on-demand VM, ship the I-24 data, and start scripts/gcp/pipeline_i24.sh
# under systemd with every stop guarantee armed:
#   1. the pipeline's EXIT trap powers the VM off 3 min after it ends (success or failure);
#   2. the VM's startup script arms a hard cap (`shutdown -h +$CAP_MIN`) at every boot,
#      independent of the pipeline;
#   3. scripts/gcp/watch_pipeline.sh (run locally after this, under caffeinate) fetches
#      the archive after every stage and DELETES the instance, unconditionally at its deadline;
#   4. with --bucket, the VM copies the archive to that bucket after every stage and, with
#      --self-delete, deletes itself at the end — no local machine has to be awake.
#   5. an on-VM idle guard (scripts/gcp/idle_guard.sh, started from the boot script) deletes the
#      instance whenever no pipeline runs after a 75-minute grace: a launch that dies after the
#      instance exists, a failed self-delete, a laptop asleep or force-shut — none can leave it idle.
#   6. the launch script itself deletes the instance if any step after creation fails.
# The hard cap must exceed the expected runtime with margin: the 2026-09-06 run was
# killed by a 300-min cap during its last stage. Size it at about twice the estimate;
# the EXIT trap, not the cap, is the normal stop.
# Usage (repo root, pushed commit):
#   scripts/gcp/launch_i24_pipeline.sh [--vm NAME] [--zone Z] [--machine TYPE] [--cap-min 480]
#       [--bucket gs://bucket/prefix] [--self-delete] [--quick] [--pipeline-args '--stages "..."'] [--allow-dirty]
set -euo pipefail
VM=flowstate-pipeline; ZONE=us-west1-b; MACHINE=n2-standard-32; CAP_MIN=480; QUICK=""; BUCKET=""; SELF_DELETE=0; PIPELINE_ARGS=""; ALLOW_DIRTY=0
PROJECT=$(gcloud config get-value project 2>/dev/null)
while [ $# -gt 0 ]; do
  case "$1" in
    --vm) VM="$2"; shift 2 ;;
    --zone) ZONE="$2"; shift 2 ;;
    --machine) MACHINE="$2"; shift 2 ;;
    --cap-min) CAP_MIN="$2"; shift 2 ;;
    --quick) QUICK="--quick"; shift ;;
    --bucket) BUCKET="${2%/}"; shift 2 ;;
    --self-delete) SELF_DELETE=1; shift ;;
    --pipeline-args) PIPELINE_ARGS="$2"; shift 2 ;;   # e.g. --pipeline-args '--stages "battery_lost prune_lost cap_sweep rescore"'
    --allow-dirty) ALLOW_DIRTY=1; shift ;;   # the VM runs `git archive HEAD` either way; skip the clean-tree check
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [ "$SELF_DELETE" -eq 1 ] && [ -z "$BUCKET" ]; then echo "--self-delete needs --bucket (the archive must leave the machine first)" >&2; exit 2; fi
BUCKET_ROOT=""; [ -n "$BUCKET" ] && BUCKET_ROOT="gs://$(printf '%s' "${BUCKET#gs://}" | cut -d/ -f1)"   # gs://bucket[/prefix] -> gs://bucket
if [ -n "$BUCKET" ] && ! gcloud storage buckets describe "$BUCKET_ROOT" >/dev/null 2>&1; then echo "bucket $BUCKET_ROOT is not readable with this account (create it first: gcloud storage buckets create gs://NAME --location=us-west1)" >&2; exit 2; fi
SCOPES=""; [ -n "$BUCKET" ] && SCOPES="--scopes=storage-rw,compute-rw,logging-write,monitoring-write"
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
REF=$(git rev-parse HEAD)
if [ "$ALLOW_DIRTY" -eq 0 ] && [ -n "$(git status --porcelain | grep -v '^??')" ]; then echo "commit and push first (the VM clones $REF); --allow-dirty skips this" >&2; exit 2; fi
if ! git merge-base --is-ancestor "$REF" origin/main 2>/dev/null; then echo "HEAD is not on origin/main; push first" >&2; exit 2; fi
STARTUP=$(mktemp)
cat > "$STARTUP" <<EOF
#!/bin/bash
# boot-time hard cap: the machine powers off $CAP_MIN minutes after every boot, whatever runs on it
shutdown -h +$CAP_MIN "boot-time hard cap (${CAP_MIN} min)"
# idle guard (scripts/gcp/idle_guard.sh, shipped as instance metadata): deletes the instance when no
# pipeline is running after the grace period, independent of any laptop-side watcher
curl -sf -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/idle-guard > /usr/local/bin/idle-guard.sh \\
  && chmod +x /usr/local/bin/idle-guard.sh \\
  && systemd-run --unit=idle-guard --property=Restart=always /usr/local/bin/idle-guard.sh
EOF
echo "== creating $VM ($MACHINE, $ZONE, project $PROJECT), hard cap $CAP_MIN min from boot"
# shellcheck disable=SC2086
gcloud compute instances create "$VM" --project "$PROJECT" --zone "$ZONE" --machine-type "$MACHINE" \
  --image-family debian-12 --image-project debian-cloud --boot-disk-size 120GB --boot-disk-type pd-balanced \
  --metadata-from-file startup-script="$STARTUP",idle-guard="$ROOT/scripts/gcp/idle_guard.sh" --labels purpose=flowstate-pipeline,autostop=yes $SCOPES >/dev/null
rm -f "$STARTUP"
mkdir -p "$ROOT/logs"; echo "$(date -u +%FT%TZ) $VM $ZONE $PROJECT $REF" > "$ROOT/logs/pipeline_launch.txt"
# From here on the instance bills. If anything below fails (an scp cut by the network, a setup
# error), delete it: a created-but-idle VM cost about fifteen dollars on 2026-09-18 while it
# waited for a human. LAUNCHED=1 disarms the trap once the pipeline unit is running.
LAUNCHED=0
cleanup_on_failure() {
  if [ "$LAUNCHED" -ne 1 ]; then
    echo "== launch failed after the instance was created; deleting $VM" >&2
    gcloud compute instances delete "$VM" --project "$PROJECT" --zone "$ZONE" --quiet >/dev/null 2>&1 && echo "== $VM deleted" >&2
  fi
}
trap cleanup_on_failure EXIT
if [ -n "$BUCKET" ]; then
  # the VM's service account must be able to write the bucket and (for --self-delete) delete this
  # one instance; a project that grants the default account no Editor role has neither by default
  SA=$(gcloud compute instances describe "$VM" --project "$PROJECT" --zone "$ZONE" --format='value(serviceAccounts[0].email)')
  echo "== granting $SA: objectAdmin on $BUCKET, instanceAdmin on $VM"
  gcloud storage buckets add-iam-policy-binding "$BUCKET_ROOT" --member="serviceAccount:$SA" --role=roles/storage.objectAdmin >/dev/null
  [ "$SELF_DELETE" -eq 1 ] && gcloud compute instances add-iam-policy-binding "$VM" --project "$PROJECT" --zone "$ZONE" --member="serviceAccount:$SA" --role=roles/compute.instanceAdmin.v1 >/dev/null
fi
ssh_cmd() { gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" --quiet --ssh-flag="-o ConnectTimeout=25" --command "$1"; }
echo "== waiting for ssh"
for i in $(seq 1 30); do if ssh_cmd "true" 2>/dev/null; then break; fi; sleep 10; done
echo "== data archive"
DATA=$(mktemp -d)/i24_data.tar
tar cf "$DATA" data/i24motion/processed/i24_wb_20221130 data/i24motion/processed/i24_wb_episodes.pkl \
  data/i24motion/processed/i24_wb_episodes_heavy.pkl data/i24motion/processed/i24_wb_episode_summary.json \
  data/i24motion/processed/i24_wb_episode_summary_heavy.json data/i24motion/auxiliary_information \
  runs/i24_validation/observed_i24.json
# the US-101 battery (stage battery_us101) reads data/ngsim (~170 MB); shipped when present
[ -d data/ngsim ] && tar rf "$DATA" data/ngsim
# per-run metrics of an earlier, interrupted cap sweep or penetration sweep let the VM resume them
for pat in "runs/i24_cap_sweep/*/*/metrics.json" "runs/i24_sweep/*/*/metrics.json" "runs/i24_sweep/*/*/meta.json" "runs/i24_sweep/MANIFEST.json"; do
  # shellcheck disable=SC2086
  ls $pat >/dev/null 2>&1 && tar rf "$DATA" $pat
done
ls -la "$DATA" | awk '{print "   ", $5, "bytes"}'
# The repository is private: the code goes up as a git-archive snapshot of HEAD
# (no clone, no token on the VM); scripts/gcp/vm_setup.sh installs the system
# packages and uv, unpacks code and data, syncs the workspace and starts the unit.
REPO_TAR="$(dirname "$DATA")/repo.tar"
git archive --format=tar -o "$REPO_TAR" HEAD
echo "== shipping code snapshot ($(du -h "$REPO_TAR" | cut -f1)) and data ($(du -h "$DATA" | cut -f1))"
for attempt in 1 2 3; do
  gcloud compute scp "$REPO_TAR" "$ROOT/scripts/gcp/vm_setup.sh" "$DATA" "$VM:/tmp/" --project "$PROJECT" --zone "$ZONE" --quiet && break
  [ "$attempt" -eq 3 ] && { echo "scp failed three times" >&2; exit 1; }
  echo "== scp attempt $attempt failed (network); retrying in 60 s" >&2; sleep 60
done
rm -rf "$(dirname "$DATA")"
echo "== VM setup and pipeline start (systemd unit 'pipeline', survives logout)"
ssh_cmd "chmod +x /tmp/vm_setup.sh && PIPELINE_BUCKET='$BUCKET' PIPELINE_SELF_DELETE=$SELF_DELETE PIPELINE_ARGS='$PIPELINE_ARGS' /tmp/vm_setup.sh $REF $QUICK" 2>&1 | tail -6
LAUNCHED=1
echo "== launched. Now run (under caffeinate):  caffeinate -i scripts/gcp/watch_pipeline.sh --vm $VM --zone $ZONE${BUCKET:+ --bucket $BUCKET}"
