#!/bin/bash
# Local watcher for scripts/gcp/pipeline_i24.sh: fetches the results archive as it
# grows, and DELETES the instance when the pipeline is done. Failsafes:
#   * every remote call is bounded; an absolute deadline (--deadline-min) deletes the
#     instance whether or not it answered, so a hung job cannot run up the bill (the
#     launcher prints this command with --deadline-min CAP_MIN+20, after Compute Engine's
#     own delete; the default of 450 min would cut a longer run short);
#   * the archive (final.tgz in the pipeline's home on the VM, or --bucket) is fetched after
#     every stage, so a laptop asleep at the wrong moment or a VM killed by its hard cap
#     loses at most the stage in flight — never the completed stages. The pipeline's home is
#     the SSH user's for an SSH launch and /root for a --via-bucket one (it runs as root
#     there): found on the VM, or given with --remote-home;
#   * a fallback archive the pipeline left beside a complete one (final_partial.tgz: artifacts, scenarios and logs
#     of a stage whose complete archive failed) is fetched too, as $DEST/final_partial.tgz, never in final.tgz's place;
#   * an archive is taken as this run's only when its logs/INSTANCE_ID is this instance's id
#     (--instance-id, else read from the instance): a bucket prefix reused while an earlier
#     launch's final.tgz is still there must not stand in for this run's. Archives written
#     before 2026-10-07 carry no id and are used with a warning;
#   * a stopped instance (TERMINATED) is always deleted and never restarted at the working
#     size: if the archive is not already complete locally (or in the bucket) it is shrunk to
#     two vCPUs, given a 60-minute max-run-duration with action DELETE while stopped (so
#     Compute Engine deletes it even if this watcher dies), started, fetched from, deleted.
#     No guest power-off is armed there: a power-off would stop that clock and strand a
#     stopped instance whose disk bills (2026-10-07 review);
#   * after PIPELINE_DONE the fetch window is a guest power-off at +30 min, armed only on
#     instances launched without --self-delete (those delete themselves and never power off).
#     If this watcher dies inside that window, the instance ends stopped: rerun the watcher
#     (its TERMINATED branch deletes it) or delete it by hand.
# Run it under `caffeinate -i` on macOS: a sleeping laptop stalls the loop.
# Usage (repo root):
#   caffeinate -i scripts/gcp/watch_pipeline.sh --vm NAME [--zone Z] [--poll-s 180]
#       [--deadline-min 450] [--dest DIR] [--bucket gs://bucket/prefix] [--instance-id ID] [--remote-home DIR]
set -u
VM=flowstate-pipeline; ZONE=us-west1-b; POLL=180; DEADLINE_MIN=450; DEST="$(pwd)"; BUCKET=""; INSTANCE_ID=""; RHOME=""
RESTART_MAX_RUN=60m   # Compute Engine's limit on the 2-vCPU fetch restart (start, ssh, a bounded 30-min scp)
while [ $# -gt 0 ]; do
  case "$1" in
    --vm) VM="$2"; shift 2 ;;
    --zone) ZONE="$2"; shift 2 ;;
    --poll-s) POLL="$2"; shift 2 ;;
    --deadline-min) DEADLINE_MIN="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --bucket) BUCKET="${2%/}"; shift 2 ;;
    --instance-id) INSTANCE_ID="$2"; shift 2 ;;
    --remote-home) RHOME="${2%/}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
PROJECT=$(gcloud config get-value project 2>/dev/null)
T0=$(date +%s); DEADLINE=$((T0 + DEADLINE_MIN * 60))
ARCHIVE="$DEST/final.tgz"; LAST_STAMP=""
PARTIAL="$DEST/final_partial.tgz"   # pipeline_i24.sh's fallback archive beside a complete one; kept beside it, never as it
say() { echo "$(date -u +%FT%TZ) $*"; }
bounded() { perl -e 'alarm shift; exec @ARGV' "$1" "${@:2}"; }   # macOS ships no `timeout`
status() { bounded 90 gcloud compute instances describe "$VM" --project "$PROJECT" --zone "$ZONE" --format='value(status)' 2>/dev/null; }
instance_id() { bounded 90 gcloud compute instances describe "$VM" --project "$PROJECT" --zone "$ZONE" --format='value(id)' 2>/dev/null; }
ssh_cmd() { bounded 150 gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" --quiet --ssh-flag="-o ConnectTimeout=25" --command "$1" 2>/dev/null; }
vm_listed() {  # vm_listed [timeout_s]: 0 the instance is listed, 1 the list call SUCCEEDED and it is absent, 2 the list call failed
  local names
  # the list's own status, not grep's: a failed call (auth expiry, network drop, the alarm) lists nothing, and an
  # empty listing must not read as "deleted" (review 2026-10-07: a failed delete was reported as VM_GONE)
  names=$(bounded "${1:-60}" gcloud compute instances list --project "$PROJECT" --format='value(name)' 2>/dev/null) || return 2
  printf '%s\n' "$names" | grep -qx "$VM" && return 0
  return 1
}
delete_vm() {
  local listed
  if bounded 300 gcloud compute instances delete "$VM" --project "$PROJECT" --zone "$ZONE" --quiet >/dev/null 2>&1; then
    say "VM_DELETED $VM"
  else
    vm_listed 60; listed=$?
    if [ "$listed" -eq 1 ]; then
      say "VM_GONE $VM (already deleted, e.g. by its own self-delete)"   # 2026-10-07: was reported as a failed delete
    elif [ "$listed" -eq 2 ]; then
      say "VM_DELETE_FAILED (the delete failed and the instance list could not be read either; retry manually: gcloud compute instances delete $VM --zone $ZONE)"
    else
      say "VM_DELETE_FAILED (retry manually: gcloud compute instances delete $VM --zone $ZONE)"
    fi
  fi
  say "post-delete instances: $(bounded 60 gcloud compute instances list --project "$PROJECT" --format='value(name,status)' 2>/dev/null | tr '\n' ' ')"
}
archive_owner() { tar xzOf "$1" logs/INSTANCE_ID 2>/dev/null | tr -d '[:space:]'; }   # the instance id inside an archive
owner_ok() {  # owner_ok <archive>: not another instance's (no id inside, or no id known: accepted, with a warning)
  local owner
  owner=$(archive_owner "$1")
  if [ -n "$owner" ] && [ -n "$INSTANCE_ID" ]; then
    [ "$owner" = "$INSTANCE_ID" ] && return 0
    say "ARCHIVE_FOREIGN: $1 was written by instance $owner, not $INSTANCE_ID"; return 1
  fi
  say "archive UNVERIFIED (its instance id: ${owner:-none}; this instance: ${INSTANCE_ID:-unknown})"
}
archive_complete() {  # the local archive holds the pipeline's exit marker and is this instance's
  [ -f "$ARCHIVE" ] && tar tzf "$ARCHIVE" 2>/dev/null | grep -q "logs/PIPELINE_EXIT\|logs/PIPELINE_DONE" && owner_ok "$ARCHIVE" >/dev/null
}
archive_stages() { [ -f "$ARCHIVE" ] && tar tzf "$ARCHIVE" 2>/dev/null | grep -o "logs/[a-z_]*\.done" | sed 's#logs/##; s#\.done##' | tr '\n' ' '; }
take_partial() {  # take_partial <file.part> <from>: install a verified fallback archive as $PARTIAL, saying what it is
  tar tzf "$1" >/dev/null 2>&1 && owner_ok "$1" || { rm -f "$1"; return 1; }
  mv -f "$1" "$PARTIAL"
  say "FETCHED_PARTIAL $(stat -f %z "$PARTIAL" 2>/dev/null || stat -c %s "$PARTIAL") bytes -> $PARTIAL from $2: a fallback archive (artifacts, scenarios and logs only) of a stage whose complete archive failed; final.tgz may be an earlier stage's [its stages: $(tar tzf "$PARTIAL" 2>/dev/null | grep -o "logs/[a-z_0-9]*\.done" | sed 's#logs/##; s#\.done##' | tr '\n' ' ')]"
}
fetch_partial_bucket() {  # -> 0 when $BUCKET/final_partial.tgz was pulled beside the archive
  [ -n "$BUCKET" ] || return 1
  bounded 600 gcloud storage cp "$BUCKET/final_partial.tgz" "$PARTIAL.part" >/dev/null 2>&1 || { rm -f "$PARTIAL.part"; return 1; }
  take_partial "$PARTIAL.part" "$BUCKET"
}
fetch_partial_vm() {  # -> 0 when the VM's final_partial.tgz was pulled beside the archive (it dies with the VM otherwise)
  local src
  remote_home || return 1
  ssh_cmd "sudo test -f $RHOME/final_partial.tgz" >/dev/null || return 1
  src="$RHOME/final_partial.tgz"
  if [ "$RHOME" = /root ]; then
    src=/tmp/flowstate_fetch_partial.tgz
    ssh_cmd "sudo cp /root/final_partial.tgz $src.part && sudo chmod 0644 $src.part && sudo mv -f $src.part $src" >/dev/null || return 1
  fi
  bounded 900 gcloud compute scp "$VM:$src" "$PARTIAL.part" --project "$PROJECT" --zone "$ZONE" --quiet 2>/dev/null || { rm -f "$PARTIAL.part"; return 1; }
  take_partial "$PARTIAL.part" "the VM"
}
fetch_bucket() {  # -> 0 when the bucket copy was pulled, verified, and is not another instance's
  [ -n "$BUCKET" ] || return 1
  fetch_partial_bucket || true
  bounded 1800 gcloud storage cp "$BUCKET/final.tgz" "$ARCHIVE.part" >/dev/null 2>&1 || { rm -f "$ARCHIVE.part"; return 1; }
  tar tzf "$ARCHIVE.part" >/dev/null 2>&1 || { rm -f "$ARCHIVE.part"; return 1; }
  owner_ok "$ARCHIVE.part" || { say "BUCKET_ARCHIVE_FOREIGN: $BUCKET/final.tgz is not this run's (a reused prefix?); not used"; rm -f "$ARCHIVE.part"; return 1; }
  mv -f "$ARCHIVE.part" "$ARCHIVE"; say "FETCHED_BUCKET $(stat -f %z "$ARCHIVE" 2>/dev/null || stat -c %s "$ARCHIVE") bytes [stages: $(archive_stages)]"
}
remote_home() {  # -> 0 once the pipeline's home on the VM is known: /root (--via-bucket) or the SSH user's
  [ -n "$RHOME" ] && return 0
  RHOME=$(ssh_cmd 'if sudo test -d /root/flowstate; then echo /root; elif [ -d "$HOME/flowstate" ]; then echo "$HOME"; fi' | tail -1)
  [ -n "$RHOME" ] || return 1
  say "pipeline home on the VM: $RHOME"
}
fetch_vm() {  # -> 0 when a newer archive was pulled over ssh (size+mtime stamp changes after every stage)
  local stamp src
  remote_home || return 1
  stamp=$(ssh_cmd "sudo stat -c '%s %Y' $RHOME/final.tgz 2>/dev/null")
  [ -n "$stamp" ] || return 1
  [ "$stamp" = "$LAST_STAMP" ] && return 0
  src="$RHOME/final.tgz"
  if [ "$RHOME" = /root ]; then
    # the pipeline runs as root (--via-bucket): stage a copy the ssh user can read
    src=/tmp/flowstate_fetch.tgz
    ssh_cmd "sudo cp /root/final.tgz $src.part && sudo chmod 0644 $src.part && sudo mv -f $src.part $src" >/dev/null || return 1
  fi
  bounded 1800 gcloud compute scp "$VM:$src" "$ARCHIVE.part" --project "$PROJECT" --zone "$ZONE" --quiet 2>/dev/null || { rm -f "$ARCHIVE.part"; return 1; }
  tar tzf "$ARCHIVE.part" >/dev/null 2>&1 || { rm -f "$ARCHIVE.part"; return 1; }
  mv -f "$ARCHIVE.part" "$ARCHIVE"; LAST_STAMP="$stamp"
  say "FETCHED $(stat -f %z "$ARCHIVE" 2>/dev/null || stat -c %s "$ARCHIVE") bytes -> $ARCHIVE [stages: $(archive_stages)]"
}
fetch_final() {  # after the exit marker: pull until the archive stops changing (light, then full)
  local i
  fetch_partial_vm || true
  for i in 1 2 3 4; do fetch_vm; archive_complete && { sleep 90; fetch_vm; } ; archive_complete && return 0; sleep 60; done
  fetch_bucket && archive_complete
}
# the fetch window after PIPELINE_DONE: the EXIT trap of a launch without --self-delete armed a power-off in 3 min;
# stretch it to 30. A --self-delete instance deletes itself and is never given a power-off (it could only strand it).
FETCH_WINDOW='if [ "$(curl -sf -m 5 -H Metadata-Flavor:Google http://metadata.google.internal/computeMetadata/v1/instance/attributes/flowstate-self-delete)" != 1 ]; then sudo shutdown -c 2>/dev/null; sudo shutdown -h +30 "watcher fetch window"; fi'
[ -n "$INSTANCE_ID" ] || INSTANCE_ID=$(instance_id)
say "watching $VM ($ZONE, $PROJECT, instance ${INSTANCE_ID:-id unknown}); poll ${POLL}s; archive -> $ARCHIVE; bucket ${BUCKET:-none}; absolute deadline $(date -u -r "$DEADLINE" +%FT%TZ 2>/dev/null || date -u -d @"$DEADLINE" +%FT%TZ)"
while true; do
  now=$(date +%s)
  st=$(status)
  if [ -z "$st" ]; then
    # an empty answer is a timeout as often as a deleted instance: confirm on the list before believing it, and
    # only a list call that succeeded without the instance means deleted (a failed one is no answer either)
    vm_listed 90; listed=$?
    if [ "$listed" -eq 1 ]; then say "VM_GONE (deleted)"; fetch_bucket; exit 0; fi
    if [ "$now" -lt "$DEADLINE" ]; then
      if [ "$listed" -eq 0 ]; then say "status unknown (describe timed out)"; else say "status unknown (describe and list both failed)"; fi
      sleep "$POLL"; continue
    fi
    # past the deadline with no answer: the deadline branch below deletes it all the same
  fi
  [ -n "$INSTANCE_ID" ] || INSTANCE_ID=$(instance_id)
  if [ "$now" -ge "$DEADLINE" ]; then
    say "DEADLINE reached with status=$st; deleting the instance unconditionally"
    [ "$st" = "RUNNING" ] && { fetch_vm || fetch_bucket || say "no archive fetched before the deadline"; }
    delete_vm; exit 0
  fi
  if [ "$st" = "RUNNING" ]; then
    flags=""; progress="(pipeline workspace not found yet, or ssh failed)"
    if remote_home; then
      flags=$(ssh_cmd "sudo cat $RHOME/flowstate/logs/PIPELINE_DONE $RHOME/flowstate/logs/PIPELINE_EXIT 2>/dev/null" | tr '\n' ' ')
      progress=$(ssh_cmd "sudo find $RHOME/flowstate/logs -maxdepth 1 -name '*.done' 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/\.done//' | sort | tr '\n' ' '")
    fi
    say "status=RUNNING stages_done=[${progress}] flags=[${flags}]"
    fetch_vm || say "no archive on the VM yet (or ssh failed)"
    if echo "$flags" | grep -q "PIPELINE_DONE\|rc="; then
      ssh_cmd "$FETCH_WINDOW" >/dev/null
      fetch_final || say "final archive incomplete; keeping what was fetched [stages: $(archive_stages)]"
      delete_vm; exit 0
    fi
  elif [ "$st" = "TERMINATED" ] || [ "$st" = "STOPPING" ]; then
    say "VM powered itself off (status=$st); local archive stages: [$(archive_stages)]"
    if fetch_bucket && archive_complete; then delete_vm; exit 0; fi
    if archive_complete; then say "archive already complete locally"; delete_vm; exit 0; fi
    say "fetching through a 2-vCPU restart (never the working size); Compute Engine deletes it $RESTART_MAX_RUN after the start"
    sleep 30
    bounded 180 gcloud compute instances set-machine-type "$VM" --project "$PROJECT" --zone "$ZONE" --machine-type e2-standard-2 --quiet >/dev/null 2>&1 \
      || say "could not shrink the instance; NOT restarting"
    # the restart's cap is server-side (set while stopped; the clock starts with the instance). Without it the
    # launch's own max-run-duration (CAP_MIN, counted from this start) still deletes the restarted instance.
    bounded 120 gcloud compute instances set-scheduling "$VM" --project "$PROJECT" --zone "$ZONE" \
      --max-run-duration="$RESTART_MAX_RUN" --instance-termination-action=DELETE --quiet >/dev/null 2>&1 \
      || say "could not set the $RESTART_MAX_RUN limit; the launch's own max-run-duration deletes the restarted instance"
    if bounded 90 gcloud compute instances describe "$VM" --project "$PROJECT" --zone "$ZONE" --format='value(machineType)' 2>/dev/null | grep -q e2-standard-2; then
      bounded 300 gcloud compute instances start "$VM" --project "$PROJECT" --zone "$ZONE" --quiet >/dev/null 2>&1
      for i in $(seq 1 20); do sleep 15; ssh_cmd "true" && break; done
      LAST_STAMP=""; fetch_vm || { sleep 60; fetch_vm; } || say "fetch after restart failed"
      fetch_partial_vm || true
    fi
    delete_vm; exit 0
  else
    say "status=$st"
  fi
  sleep "$POLL"
done
