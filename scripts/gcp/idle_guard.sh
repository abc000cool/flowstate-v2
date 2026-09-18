#!/bin/bash
# On-VM idle guard (runs as root from boot, started by the launch script's startup script).
# Deletes this instance whenever no pipeline is running: a launch that died after the instance
# was created, a pipeline whose self-delete failed, a laptop that went to sleep or was
# force-shut mid-launch. The only local dependency is this loop. Grace: GRACE_UPTIME_S seconds
# of uptime (default 75 min) for the data upload and the workspace setup to finish; after that,
# two checks five minutes apart without a pipeline process are enough. Falls back to a power-off
# if the instance cannot delete itself (no instanceAdmin grant), which still stops the CPU bill.
set -u
GRACE_UPTIME_S="${GRACE_UPTIME_S:-4500}"
LOG=/var/log/idle-guard.log
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
md() { curl -sf -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/$1"; }
NAME=$(md instance/name); ZONE=$(md instance/zone | awk -F/ '{print $NF}')
log "idle guard armed on $NAME ($ZONE): grace until uptime ${GRACE_UPTIME_S}s, then delete when no pipeline runs"
strikes=0
while true; do
  up=$(cut -d. -f1 /proc/uptime)
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
      if gcloud compute instances delete "$NAME" --zone "$ZONE" --quiet >>"$LOG" 2>&1; then log "delete issued"; else log "delete failed; powering off"; shutdown -h now; fi
      sleep 600
    fi
  else
    strikes=0
  fi
  sleep 300
done
