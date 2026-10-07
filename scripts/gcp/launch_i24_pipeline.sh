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
#   7. Compute Engine itself deletes the instance (disk included) $CAP_MIN minutes after it starts
#      running (`--max-run-duration`, `--instance-termination-action=DELETE`): server-side, so it
#      fires with the guest hung, the idle guard dead and the laptop off. Its clock counts running
#      time only, so the guest's own power-off (2.) is pushed 15 minutes past it: a guest power-off
#      first would stop the clock and leave a stopped VM whose disk still bills (2026-09-24 audit).
# The hard cap must exceed the expected runtime with margin: the 2026-09-06 run was
# killed by a 300-min cap during its last stage. Size it at about twice the estimate;
# the EXIT trap, not the cap, is the normal stop.
# Usage (repo root, pushed commit):
#   scripts/gcp/launch_i24_pipeline.sh [--vm NAME] [--zone Z[,Z2,...]] [--machine TYPE] [--cap-min 480]
#       [--bucket gs://bucket/prefix] [--self-delete] [--quick] [--pipeline-args '--stages "..."'] [--allow-dirty] [--via-bucket]
set -euo pipefail
VM=flowstate-pipeline; ZONE=us-west1-b; MACHINE=n2-standard-32; CAP_MIN=480; QUICK=""; BUCKET=""; SELF_DELETE=0; PIPELINE_ARGS=""; ALLOW_DIRTY=0; DATA_SET=i24; VIA_BUCKET=0
PROJECT=$(gcloud config get-value project 2>/dev/null)
while [ $# -gt 0 ]; do
  case "$1" in
    --vm) VM="$2"; shift 2 ;;
    --zone) ZONE="$2"; shift 2 ;;   # one zone, or a comma-separated list tried in order when a zone has no capacity (2026-10-07: ZONE_RESOURCE_POOL_EXHAUSTED for n2-standard-32 in us-west1-c)
    --machine) MACHINE="$2"; shift 2 ;;
    --cap-min) CAP_MIN="$2"; shift 2 ;;
    --quick) QUICK="--quick"; shift ;;
    --bucket) BUCKET="${2%/}"; shift 2 ;;
    --self-delete) SELF_DELETE=1; shift ;;
    --pipeline-args) PIPELINE_ARGS="$2"; shift 2 ;;   # e.g. --pipeline-args '--stages "battery_lost prune_lost cap_sweep rescore"'
    --allow-dirty) ALLOW_DIRTY=1; shift ;;   # the VM runs `git archive HEAD` either way; skip the clean-tree check
    --data-set) DATA_SET="$2"; shift 2 ;;   # i24 (default: ship the I-24 MOTION processed data) | none (onboarded-corridor rounds: inputs are tracked in git)
    --via-bucket) VIA_BUCKET=1; shift ;;   # no SSH from the laptop: inputs go through the bucket and the VM's boot script sets up and starts the unit (2026-10-06: a laptop-side network that dropped port 22 to the VM broke two launches)
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [ "$VIA_BUCKET" -eq 1 ] && [ -z "$BUCKET" ]; then echo "--via-bucket needs --bucket" >&2; exit 2; fi
if [ "$SELF_DELETE" -eq 1 ] && [ -z "$BUCKET" ]; then echo "--self-delete needs --bucket (the archive must leave the machine first)" >&2; exit 2; fi
BUCKET_ROOT=""; [ -n "$BUCKET" ] && BUCKET_ROOT="gs://$(printf '%s' "${BUCKET#gs://}" | cut -d/ -f1)"   # gs://bucket[/prefix] -> gs://bucket
if [ -n "$BUCKET" ] && ! gcloud storage buckets describe "$BUCKET_ROOT" >/dev/null 2>&1; then echo "bucket $BUCKET_ROOT is not readable with this account (create it first: gcloud storage buckets create gs://NAME --location=us-west1)" >&2; exit 2; fi
SCOPES=""; [ -n "$BUCKET" ] && SCOPES="--scopes=storage-rw,compute-rw,logging-write,monitoring-write"
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
REF=$(git rev-parse HEAD)
if [ "$ALLOW_DIRTY" -eq 0 ] && [ -n "$(git status --porcelain | grep -v '^??')" ]; then echo "commit and push first (the VM clones $REF); --allow-dirty skips this" >&2; exit 2; fi
if ! git merge-base --is-ancestor "$REF" origin/main 2>/dev/null; then echo "HEAD is not on origin/main; push first" >&2; exit 2; fi
build_archives() {  # sets DATA (the data tar) and REPO_TAR (git archive of HEAD) in one temp dir
  echo "== data archive"
  DATA=$(mktemp -d)/i24_data.tar
  if [ "${DATA_SET:-i24}" = "none" ]; then
    tar cf "$DATA" --files-from /dev/null
  else
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
  fi
  # per-run metrics of an interrupted strategy sweep ride along whatever the data set (small; the
  # sweep resumes from them — 2026-09-24: 8 of 120 runs were lost to an instance deletion)
  for pat in "runs/i24_strat_sweep/*/*/*/metrics.json" "runs/i24_strat_sweep/*/*/*/meta.json" "runs/i24_strat_sweep/MANIFEST.json"; do
    # shellcheck disable=SC2086
    ls $pat >/dev/null 2>&1 && tar rf "$DATA" $pat
  done
  ls -la "$DATA" | awk '{print "   ", $5, "bytes"}'
  # The repository is private: the code goes up as a git-archive snapshot of HEAD
  # (no clone, no token on the VM); scripts/gcp/vm_setup.sh installs the system
  # packages and uv, unpacks code and data, syncs the workspace and starts the unit.
  REPO_TAR="$(dirname "$DATA")/repo.tar"
  git archive --format=tar -o "$REPO_TAR" HEAD
}

if [ "$VIA_BUCKET" -eq 1 ]; then
  # 2026-10-06: the inputs go up through the bucket (HTTPS) and the VM's boot script fetches them,
  # sets up and starts the unit — no SSH from the laptop at all
  build_archives
  INPUTS="$BUCKET/inputs/$(date -u +%Y%m%dT%H%M%SZ)"
  printf '%s' "$PIPELINE_ARGS" > "$(dirname "$DATA")/pipeline_args.txt"
  printf '%s %s %s\n' "$REF" "${QUICK:-none}" "$SELF_DELETE" > "$(dirname "$DATA")/launch.txt"
  echo "== uploading code snapshot ($(du -h "$REPO_TAR" | cut -f1)) and data ($(du -h "$DATA" | cut -f1)) to $INPUTS"
  gcloud storage cp "$REPO_TAR" "$DATA" "$ROOT/scripts/gcp/vm_setup.sh" "$(dirname "$DATA")/pipeline_args.txt" \
    "$(dirname "$DATA")/launch.txt" "$INPUTS/" --quiet >/dev/null
  rm -rf "$(dirname "$DATA")"
fi
GUEST_CAP_MIN=$((CAP_MIN + 15))   # fallback only: Compute Engine deletes the VM at CAP_MIN (see 7.)
STARTUP=$(mktemp)
cat > "$STARTUP" <<EOF
#!/bin/bash
# fallback hard cap: the machine powers off $GUEST_CAP_MIN minutes after every boot, whatever runs on
# it; Compute Engine's max-run-duration deletes it at $CAP_MIN minutes first
shutdown -h +$GUEST_CAP_MIN "boot-time fallback cap (${GUEST_CAP_MIN} min)"
# idle guard (scripts/gcp/idle_guard.sh, shipped as instance metadata): deletes the instance when no
# pipeline is running after the grace period, independent of any laptop-side watcher
curl -sf -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/idle-guard > /usr/local/bin/idle-guard.sh \\
  && chmod +x /usr/local/bin/idle-guard.sh \\
  && systemd-run --unit=idle-guard --property=Restart=always /usr/local/bin/idle-guard.sh
EOF
if [ "$VIA_BUCKET" -eq 1 ]; then
cat >> "$STARTUP" <<EOF
# --via-bucket: fetch the inputs, set up and start the pipeline once (as root; the unit is a system unit)
if [ ! -f /var/lib/flowstate-setup.started ]; then
  touch /var/lib/flowstate-setup.started
  export HOME=/root
  cd /tmp
  ok=0
  # the bucket grant is made just after the instance exists: retry the download while it lands
  for i in \$(seq 1 20); do
    if gcloud storage cp "$INPUTS/repo.tar" "$INPUTS/i24_data.tar" "$INPUTS/vm_setup.sh" "$INPUTS/pipeline_args.txt" "$INPUTS/launch.txt" /tmp/ --quiet; then ok=1; break; fi
    sleep 30
  done
  if [ "\$ok" -eq 1 ]; then
    chmod +x /tmp/vm_setup.sh
    read -r SHA QK SD < /tmp/launch.txt
    [ "\$QK" = none ] && QK=""
    if PIPELINE_BUCKET="$BUCKET" PIPELINE_SELF_DELETE="\$SD" PIPELINE_ARGS="\$(cat /tmp/pipeline_args.txt)" /tmp/vm_setup.sh "\$SHA" \$QK > /var/log/flowstate-setup.log 2>&1; then
      gcloud storage cp /var/log/flowstate-setup.log "$INPUTS/STARTED" --quiet
    else
      gcloud storage cp /var/log/flowstate-setup.log "$INPUTS/SETUP_FAILED" --quiet
    fi
  else
    echo "input download failed twenty times" > /var/log/flowstate-setup.log
    gcloud storage cp /var/log/flowstate-setup.log "$INPUTS/SETUP_FAILED" --quiet
  fi
fi
EOF
fi
# Zones are tried in order; only a capacity refusal moves on to the next one (the inputs, uploaded
# once above in --via-bucket mode, serve every zone). Any other error stops the launch.
CREATED=0; CREATE_ERR=$(mktemp)
IFS=',' read -r -a ZONES <<< "$ZONE"
for Z in "${ZONES[@]}"; do
  echo "== creating $VM ($MACHINE, $Z, project $PROJECT), deleted by Compute Engine $CAP_MIN min after it starts running"
  # shellcheck disable=SC2086
  if gcloud compute instances create "$VM" --project "$PROJECT" --zone "$Z" --machine-type "$MACHINE" \
    --max-run-duration="${CAP_MIN}m" --instance-termination-action=DELETE \
    --image-family debian-12 --image-project debian-cloud --boot-disk-size 120GB --boot-disk-type pd-balanced \
    --metadata-from-file startup-script="$STARTUP",idle-guard="$ROOT/scripts/gcp/idle_guard.sh" --labels purpose=flowstate-pipeline,autostop=yes $SCOPES >/dev/null 2>"$CREATE_ERR"; then
    ZONE="$Z"; CREATED=1; break
  fi
  cat "$CREATE_ERR" >&2
  if grep -qE "ZONE_RESOURCE_POOL_EXHAUSTED|does not have enough resources|resource_availability" "$CREATE_ERR"; then
    echo "== no capacity for $MACHINE in $Z; trying the next zone" >&2
    continue
  fi
  break
done
rm -f "$STARTUP" "$CREATE_ERR"
[ "$CREATED" -eq 1 ] || { echo "== could not create $VM in any of: $ZONE" >&2; exit 1; }
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
if [ "$VIA_BUCKET" -eq 1 ]; then
  echo "== waiting for the VM's boot-time setup (marker in $INPUTS, up to 30 min)"
  for i in $(seq 1 60); do
    if gcloud storage ls "$INPUTS/STARTED" >/dev/null 2>&1; then
      gcloud storage cat "$INPUTS/STARTED" 2>/dev/null | tail -4; LAUNCHED=1; break
    fi
    if gcloud storage ls "$INPUTS/SETUP_FAILED" >/dev/null 2>&1; then
      echo "== the VM's setup failed:" >&2; gcloud storage cat "$INPUTS/SETUP_FAILED" 2>/dev/null | tail -20 >&2; exit 1
    fi
    sleep 30
  done
  [ "$LAUNCHED" -eq 1 ] || { echo "== no setup marker after 30 min" >&2; exit 1; }
  echo "== launched (via bucket). Now run (under caffeinate):  caffeinate -i scripts/gcp/watch_pipeline.sh --vm $VM --zone $ZONE --bucket $BUCKET"
  exit 0
fi
ssh_cmd() { gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" --quiet --ssh-flag="-o ConnectTimeout=25" --command "$1"; }
echo "== waiting for ssh"
for i in $(seq 1 30); do if ssh_cmd "true" 2>/dev/null; then break; fi; sleep 10; done
build_archives
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
