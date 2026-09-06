#!/bin/bash
# Local watcher for scripts/gcp/pipeline_i24.sh: fetches the results archive as it
# grows, and DELETES the instance when the pipeline is done. Failsafes:
#   * every remote call is bounded; an absolute deadline (--deadline-min) deletes the
#     instance whether or not it answered, so a hung job cannot run up the bill;
#   * the archive ($HOME/final.tgz on the VM, or --bucket) is fetched after every
#     stage, so a laptop asleep at the wrong moment or a VM killed by its hard cap
#     loses at most the stage in flight — never the completed stages;
#   * a VM that powered itself off is never restarted at the working size: if the
#     archive is not already complete locally (or in the bucket) it is shrunk to two
#     vCPUs, started with a 30-minute cap that is re-armed before every fetch, and deleted.
# Run it under `caffeinate -i` on macOS: a sleeping laptop stalls the loop.
# Usage (repo root):
#   caffeinate -i scripts/gcp/watch_pipeline.sh --vm NAME [--zone Z] [--poll-s 180]
#       [--deadline-min 450] [--dest DIR] [--bucket gs://bucket/prefix]
set -u
VM=flowstate-pipeline; ZONE=us-west1-b; POLL=180; DEADLINE_MIN=450; DEST="$(pwd)"; BUCKET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --vm) VM="$2"; shift 2 ;;
    --zone) ZONE="$2"; shift 2 ;;
    --poll-s) POLL="$2"; shift 2 ;;
    --deadline-min) DEADLINE_MIN="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --bucket) BUCKET="${2%/}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
PROJECT=$(gcloud config get-value project 2>/dev/null)
T0=$(date +%s); DEADLINE=$((T0 + DEADLINE_MIN * 60))
ARCHIVE="$DEST/final.tgz"; LAST_STAMP=""
say() { echo "$(date -u +%FT%TZ) $*"; }
bounded() { perl -e 'alarm shift; exec @ARGV' "$1" "${@:2}"; }   # macOS ships no `timeout`
status() { bounded 90 gcloud compute instances describe "$VM" --project "$PROJECT" --zone "$ZONE" --format='value(status)' 2>/dev/null; }
ssh_cmd() { bounded 150 gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" --quiet --ssh-flag="-o ConnectTimeout=25" --command "$1" 2>/dev/null; }
delete_vm() {
  bounded 300 gcloud compute instances delete "$VM" --project "$PROJECT" --zone "$ZONE" --quiet >/dev/null 2>&1 \
    && say "VM_DELETED $VM" || say "VM_DELETE_FAILED (retry manually: gcloud compute instances delete $VM --zone $ZONE)"
  say "post-delete instances: $(bounded 60 gcloud compute instances list --project "$PROJECT" --format='value(name,status)' 2>/dev/null | tr '\n' ' ')"
}
archive_complete() {  # the local archive holds the pipeline's exit marker
  [ -f "$ARCHIVE" ] && tar tzf "$ARCHIVE" 2>/dev/null | grep -q "logs/PIPELINE_EXIT\|logs/PIPELINE_DONE"
}
archive_stages() { [ -f "$ARCHIVE" ] && tar tzf "$ARCHIVE" 2>/dev/null | grep -o "logs/[a-z_]*\.done" | sed 's#logs/##; s#\.done##' | tr '\n' ' '; }
fetch_bucket() {  # -> 0 when the bucket copy was pulled and verified
  [ -n "$BUCKET" ] || return 1
  bounded 1800 gcloud storage cp "$BUCKET/final.tgz" "$ARCHIVE.part" >/dev/null 2>&1 || { rm -f "$ARCHIVE.part"; return 1; }
  tar tzf "$ARCHIVE.part" >/dev/null 2>&1 || { rm -f "$ARCHIVE.part"; return 1; }
  mv -f "$ARCHIVE.part" "$ARCHIVE"; say "FETCHED_BUCKET $(stat -f %z "$ARCHIVE" 2>/dev/null || stat -c %s "$ARCHIVE") bytes [stages: $(archive_stages)]"
}
fetch_vm() {  # -> 0 when a newer archive was pulled over ssh (size+mtime stamp changes after every stage)
  local stamp
  stamp=$(ssh_cmd "stat -c '%s %Y' ~/final.tgz 2>/dev/null")
  [ -n "$stamp" ] || return 1
  [ "$stamp" = "$LAST_STAMP" ] && return 0
  bounded 1800 gcloud compute scp "$VM:~/final.tgz" "$ARCHIVE.part" --project "$PROJECT" --zone "$ZONE" --quiet 2>/dev/null || { rm -f "$ARCHIVE.part"; return 1; }
  tar tzf "$ARCHIVE.part" >/dev/null 2>&1 || { rm -f "$ARCHIVE.part"; return 1; }
  mv -f "$ARCHIVE.part" "$ARCHIVE"; LAST_STAMP="$stamp"
  say "FETCHED $(stat -f %z "$ARCHIVE" 2>/dev/null || stat -c %s "$ARCHIVE") bytes -> $ARCHIVE [stages: $(archive_stages)]"
}
fetch_final() {  # after the exit marker: pull until the archive stops changing (light, then full)
  local i
  for i in 1 2 3 4; do fetch_vm; archive_complete && { sleep 90; fetch_vm; } ; archive_complete && return 0; sleep 60; done
  fetch_bucket && archive_complete
}
say "watching $VM ($ZONE, $PROJECT); poll ${POLL}s; archive -> $ARCHIVE; bucket ${BUCKET:-none}; absolute deadline $(date -u -r "$DEADLINE" +%FT%TZ 2>/dev/null || date -u -d @"$DEADLINE" +%FT%TZ)"
while true; do
  now=$(date +%s)
  st=$(status)
  if [ -z "$st" ]; then
    # an empty answer is a timeout as often as a deleted instance: confirm on the list before believing it
    if bounded 90 gcloud compute instances list --project "$PROJECT" --format='value(name)' 2>/dev/null | grep -qx "$VM"; then say "status unknown (describe timed out)"; sleep "$POLL"; continue; fi
    say "VM_GONE (deleted)"; fetch_bucket; exit 0
  fi
  if [ "$now" -ge "$DEADLINE" ]; then
    say "DEADLINE reached with status=$st; deleting the instance unconditionally"
    [ "$st" = "RUNNING" ] && { fetch_vm || fetch_bucket || say "no archive fetched before the deadline"; }
    delete_vm; exit 0
  fi
  if [ "$st" = "RUNNING" ]; then
    flags=$(ssh_cmd "cat ~/flowstate/logs/PIPELINE_DONE 2>/dev/null; cat ~/flowstate/logs/PIPELINE_EXIT 2>/dev/null" | tr '\n' ' ')
    progress=$(ssh_cmd "ls ~/flowstate/logs/*.done 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/\.done//' | tr '\n' ' '")
    say "status=RUNNING stages_done=[${progress}] flags=[${flags}]"
    fetch_vm || say "no archive on the VM yet (or ssh failed)"
    if echo "$flags" | grep -q "PIPELINE_DONE\|rc="; then
      ssh_cmd "sudo shutdown -c 2>/dev/null; sudo shutdown -h +30 'watcher fetch window'" >/dev/null
      fetch_final || say "final archive incomplete; keeping what was fetched [stages: $(archive_stages)]"
      delete_vm; exit 0
    fi
  elif [ "$st" = "TERMINATED" ] || [ "$st" = "STOPPING" ]; then
    say "VM powered itself off (status=$st); local archive stages: [$(archive_stages)]"
    if fetch_bucket && archive_complete; then delete_vm; exit 0; fi
    if archive_complete; then say "archive already complete locally"; delete_vm; exit 0; fi
    say "fetching through a 2-vCPU restart with a 30-min cap (never the working size)"
    sleep 30
    bounded 180 gcloud compute instances set-machine-type "$VM" --project "$PROJECT" --zone "$ZONE" --machine-type e2-standard-2 --quiet >/dev/null 2>&1 \
      || say "could not shrink the instance; NOT restarting"
    if bounded 90 gcloud compute instances describe "$VM" --project "$PROJECT" --zone "$ZONE" --format='value(machineType)' 2>/dev/null | grep -q e2-standard-2; then
      bounded 300 gcloud compute instances start "$VM" --project "$PROJECT" --zone "$ZONE" --quiet >/dev/null 2>&1
      for i in $(seq 1 20); do sleep 15; ssh_cmd "true" && break; done
      ssh_cmd "sudo shutdown -c 2>/dev/null; sudo shutdown -h +30 'fetch cap'" >/dev/null
      LAST_STAMP=""; fetch_vm || { sleep 60; ssh_cmd "sudo shutdown -c 2>/dev/null; sudo shutdown -h +30 'fetch cap'" >/dev/null; fetch_vm; } || say "fetch after restart failed"
    fi
    delete_vm; exit 0
  else
    say "status=$st"
  fi
  sleep "$POLL"
done
