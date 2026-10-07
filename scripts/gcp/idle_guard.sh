#!/bin/bash
# On-VM idle guard (runs as root from boot, started by the launch script's startup script).
# Deletes this instance whenever no pipeline is running: a launch that died after the instance
# was created, a pipeline whose self-delete failed, a laptop that went to sleep or was
# force-shut mid-launch. The only local dependency is this loop. Grace: GRACE_UPTIME_S seconds
# of uptime (default 75 min) for the data upload and the workspace setup to finish; after that,
# two checks five minutes apart without a pipeline process are enough.
# When the delete fails (2026-10-07 review):
#   * a --self-delete launch (instance metadata flowstate-self-delete=1, so the instance holds the
#     instanceAdmin grant on itself): retried every check, NEVER a power-off. A powered-off instance
#     stops Compute Engine's max-run-duration clock, and with no laptop watcher nothing would ever
#     delete it; its disk would bill until a human noticed. Up, it is deleted server-side at the cap.
#   * any other launch (no delete grant): one retry, then a power-off, which stops the CPU bill;
#     the laptop watcher's TERMINATED branch (or a manual `gcloud compute instances delete`)
#     removes the stopped instance.
set -u
GRACE_UPTIME_S="${GRACE_UPTIME_S:-4500}"
LOG="${IDLE_GUARD_LOG:-/var/log/idle-guard.log}"
UPTIME_FILE="${IDLE_GUARD_UPTIME_FILE:-/proc/uptime}"
CHECK_S="${IDLE_GUARD_CHECK_S:-300}"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
md() { curl -sf -m 10 -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/$1"; }
NAME=$(md instance/name); ZONE=$(md instance/zone | awk -F/ '{print $NF}')
SELF_DELETE=$(md instance/attributes/flowstate-self-delete 2>/dev/null); [ "$SELF_DELETE" = 1 ] || SELF_DELETE=0
log "idle guard armed on $NAME ($ZONE, self-delete launch: $SELF_DELETE): grace until uptime ${GRACE_UPTIME_S}s, then delete when no pipeline runs"
delete_self() { gcloud compute instances delete "$NAME" --zone "$ZONE" --quiet >>"$LOG" 2>&1; }
strikes=0
while true; do
  up=$(cut -d. -f1 "$UPTIME_FILE" 2>/dev/null); up=${up:-0}
  busy=0
  pgrep -f 'scripts/gcp/pipeline_i24.sh' >/dev/null && busy=1
  pgrep -f 'vm_setup.sh' >/dev/null && busy=1
  pgrep -f 'uv sync' >/dev/null && busy=1
  pgrep -x sumo >/dev/null && busy=1
  if [ "$up" -gt "$GRACE_UPTIME_S" ] && [ "$busy" -eq 0 ]; then
    strikes=$((strikes + 1))
    log "no pipeline process (uptime $((up / 60)) min), strike $strikes"
    if [ "$strikes" -ge 2 ]; then
      log "deleting $NAME"
      if delete_self; then
        log "delete issued"; sleep 600
      elif [ "$SELF_DELETE" = 1 ]; then
        log "delete failed; NOT powering off (a stopped instance is never deleted by max-run-duration); retrying at the next check"
      else
        sleep 60
        if delete_self; then
          log "delete issued (second attempt)"; sleep 600
        else
          log "delete failed twice (no delete grant: launched without --self-delete); powering off: the laptop watcher deletes the stopped instance"
          shutdown -h now; sleep 600
        fi
      fi
    fi
  else
    strikes=0
  fi
  sleep "$CHECK_S"
done
