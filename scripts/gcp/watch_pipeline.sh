#!/bin/bash
# Local watcher for scripts/gcp/pipeline_i24.sh: polls the VM, fetches the results
# archive when the pipeline reports done, and DELETES the instance. Failsafes:
#   * absolute deadline (--deadline-min, default 330): the instance is deleted whether or
#     not it answered, so a hung job or a lost network cannot run up the bill;
#   * if the VM powered itself off before the fetch (the EXIT trap or the boot cap won the
#     race), it is NEVER restarted at the working size: the watcher shrinks it to a
#     2-vCPU type, starts it with a 20-minute cap, fetches, and deletes.
# Usage (repo root):  scripts/gcp/watch_pipeline.sh --vm NAME [--zone Z] [--poll-s 180] [--deadline-min 330] [--dest DIR]
set -u
VM=flowstate-pipeline; ZONE=us-west1-b; POLL=180; DEADLINE_MIN=330; DEST="$(pwd)"
while [ $# -gt 0 ]; do
  case "$1" in
    --vm) VM="$2"; shift 2 ;;
    --zone) ZONE="$2"; shift 2 ;;
    --poll-s) POLL="$2"; shift 2 ;;
    --deadline-min) DEADLINE_MIN="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
PROJECT=$(gcloud config get-value project 2>/dev/null)
T0=$(date +%s); DEADLINE=$((T0 + DEADLINE_MIN * 60))
say() { echo "$(date -u +%FT%TZ) $*"; }
status() { gcloud compute instances describe "$VM" --project "$PROJECT" --zone "$ZONE" --format='value(status)' 2>/dev/null; }
ssh_cmd() { gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" --quiet --ssh-flag="-o ConnectTimeout=25" --command "$1" 2>/dev/null; }
delete_vm() {
  gcloud compute instances delete "$VM" --project "$PROJECT" --zone "$ZONE" --quiet >/dev/null 2>&1 \
    && say "VM_DELETED $VM" || say "VM_DELETE_FAILED (retry manually: gcloud compute instances delete $VM --zone $ZONE)"
  say "post-delete instances: $(gcloud compute instances list --project "$PROJECT" --format='value(name,status)' 2>/dev/null | tr '\n' ' ')"
}
fetch() {  # -> 0 when $DEST/final.tgz is a readable archive
  ssh_cmd "ls -la /tmp/final.tgz" >/dev/null || return 1
  gcloud compute scp "$VM:/tmp/final.tgz" "$DEST/final.tgz" --project "$PROJECT" --zone "$ZONE" --quiet 2>/dev/null || return 1
  tar tzf "$DEST/final.tgz" >/dev/null 2>&1 || return 1
  say "FETCHED $(stat -f %z "$DEST/final.tgz" 2>/dev/null || stat -c %s "$DEST/final.tgz") bytes -> $DEST/final.tgz"
}
say "watching $VM ($ZONE, $PROJECT); poll ${POLL}s; absolute deadline $(date -u -r "$DEADLINE" +%FT%TZ 2>/dev/null || date -u -d @"$DEADLINE" +%FT%TZ)"
while true; do
  now=$(date +%s)
  st=$(status)
  if [ -z "$st" ]; then say "VM_GONE (already deleted)"; exit 0; fi
  if [ "$now" -ge "$DEADLINE" ]; then
    say "DEADLINE reached with status=$st; deleting the instance unconditionally"
    fetch || say "no archive fetched before the deadline"
    delete_vm; exit 0
  fi
  if [ "$st" = "RUNNING" ]; then
    done_flag=$(ssh_cmd "cat ~/flowstate/logs/PIPELINE_DONE 2>/dev/null; cat ~/flowstate/logs/PIPELINE_EXIT 2>/dev/null" | tr '\n' ' ')
    progress=$(ssh_cmd "ls ~/flowstate/logs/*.done 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' '")
    say "status=RUNNING stages_done=[${progress}] flags=[${done_flag}]"
    if echo "$done_flag" | grep -q "PIPELINE_DONE\|rc="; then
      ssh_cmd "sudo shutdown -c 2>/dev/null; sudo shutdown -h +25 'watcher fetch window'" >/dev/null
      if fetch || { sleep 60; fetch; }; then delete_vm; exit 0; fi
      say "FETCH_FAILED twice; leaving the VM to its own shutdown, retrying next poll"
    fi
  elif [ "$st" = "TERMINATED" ] || [ "$st" = "STOPPING" ]; then
    say "VM powered itself off (status=$st); fetching through a 2-vCPU restart with a 20-min cap"
    sleep 30
    gcloud compute instances set-machine-type "$VM" --project "$PROJECT" --zone "$ZONE" --machine-type e2-standard-2 --quiet >/dev/null 2>&1 \
      || say "could not shrink the instance; NOT restarting at the working size"
    if gcloud compute instances describe "$VM" --project "$PROJECT" --zone "$ZONE" --format='value(machineType)' 2>/dev/null | grep -q e2-standard-2; then
      gcloud compute instances start "$VM" --project "$PROJECT" --zone "$ZONE" --quiet >/dev/null 2>&1
      for i in $(seq 1 12); do sleep 15; ssh_cmd "true" && break; done
      ssh_cmd "sudo shutdown -c 2>/dev/null; sudo shutdown -h +20 'fetch cap'" >/dev/null
      fetch || { sleep 60; fetch; } || say "fetch after restart failed"
    fi
    delete_vm; exit 0
  else
    say "status=$st"
  fi
  sleep "$POLL"
done
