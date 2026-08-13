#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=runtime-lib.sh
source "$script_dir/runtime-lib.sh"

stockwatcher_cnb_prepare_runtime
stockwatcher_cnb_export_app_env

if ! "$script_dir/registry-snapshot.sh" restore-key; then
  echo "CNB private key escrow is unavailable; run the private setup workspace first" >&2
  exit 1
fi

if [[ ! -s "$SW_CNB_DB" ]]; then
  if ! "$script_dir/registry-snapshot.sh" restore; then
    echo "CNB result snapshot is unavailable; run the private setup workspace first" >&2
    exit 1
  fi
fi

if ! python -m stock_watcher.server.admin_cli migrate >/dev/null; then
  echo "CNB database validation failed; restoring the last verified result snapshot" >&2
  if ! "$script_dir/registry-snapshot.sh" restore; then
    echo "CNB database is corrupt and no verified result snapshot is available" >&2
    exit 1
  fi
  python -m stock_watcher.server.admin_cli migrate >/dev/null
fi

run_stamp=$(date +%Y%m%dT%H%M%S)
web_log="$SW_CNB_LOGS/web-$run_stamp.log"
worker_log="$SW_CNB_LOGS/worker-$run_stamp.log"
web_pid=""
worker_pid=""
snapshot_running=0

start_worker() {
  python -m stock_watcher.server.worker >>"$worker_log" 2>&1 &
  worker_pid=$!
}

create_snapshot() {
  local marker=$1
  local marker_file="$SW_CNB_BACKUPS/$marker.ok"
  local failure_file="$SW_CNB_BACKUPS/$marker.failed"
  [[ -f "$marker_file" ]] && return 0
  [[ $snapshot_running -eq 1 ]] && return 1

  if [[ -f "$failure_file" ]]; then
    local now_epoch failure_epoch
    now_epoch=$(date +%s)
    failure_epoch=$(stat -c %Y "$failure_file" 2>/dev/null || echo 0)
    (( now_epoch - failure_epoch < 300 )) && return 1
  fi

  snapshot_running=1
  if "$script_dir/registry-snapshot.sh" create; then
    date -Iseconds >"$marker_file"
    rm -f -- "$failure_file"
  else
    date -Iseconds >"$failure_file"
    echo "CNB result snapshot failed; Web remains fail-closed and will retry"
    snapshot_running=0
    return 1
  fi
  snapshot_running=0
  return 0
}

shutdown() {
  local exit_status=$?
  trap - EXIT INT TERM
  if [[ -n "$worker_pid" ]]; then
    kill -TERM "$worker_pid" 2>/dev/null || true
  fi
  if [[ -n "$web_pid" ]]; then
    kill -TERM "$web_pid" 2>/dev/null || true
  fi
  wait "$worker_pid" 2>/dev/null || true
  wait "$web_pid" 2>/dev/null || true
  if [[ -s "$SW_CNB_DB" ]]; then
    if ! create_snapshot "$(date +%Y%m%d)-shutdown"; then
      echo "CNB final result snapshot failed during shutdown" >&2
    fi
  fi
  exit "$exit_status"
}
trap shutdown EXIT INT TERM

python -m stock_watcher.server.web >>"$web_log" 2>&1 &
web_pid=$!
start_worker

manual_minutes=${STOCKWATCHER_CNB_MANUAL_DURATION_MINUTES:-}
backup_interval_seconds=${STOCKWATCHER_CNB_BACKUP_INTERVAL_SECONDS:-900}
if [[ ! "$backup_interval_seconds" =~ ^[1-9][0-9]*$ ]]; then
  echo "STOCKWATCHER_CNB_BACKUP_INTERVAL_SECONDS must be a positive integer" >&2
  exit 1
fi
last_periodic_snapshot_epoch=$(date +%s)
if [[ -n "$manual_minutes" ]]; then
  cutoff_epoch=$(( $(date +%s) + manual_minutes * 60 ))
else
  cutoff_epoch=$(python - "${STOCKWATCHER_CNB_END_TIME:-16:15}" <<'PY'
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

hour, minute = (int(value) for value in sys.argv[1].split(":"))
now = datetime.now(ZoneInfo("Asia/Shanghai"))
cutoff = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
print(int(cutoff.timestamp()))
PY
  )
fi

echo "StockWatcher Web preview started; health endpoint is /health/ready"

while (( $(date +%s) < cutoff_epoch )); do
  if ! kill -0 "$web_pid" 2>/dev/null; then
    echo "Web process exited unexpectedly; see $web_log" >&2
    exit 1
  fi
  if ! kill -0 "$worker_pid" 2>/dev/null; then
    wait "$worker_pid" 2>/dev/null || true
    sleep 10
    start_worker
  fi

  now_epoch=$(date +%s)
  if (( now_epoch - last_periodic_snapshot_epoch >= backup_interval_seconds )); then
    periodic_slot=$(( now_epoch / backup_interval_seconds ))
    if create_snapshot "$(date +%Y%m%d)-periodic-$periodic_slot"; then
      last_periodic_snapshot_epoch=$now_epoch
    fi
  fi

  if [[ -z "$manual_minutes" ]]; then
    current_time=$(date +%H:%M)
    IFS=',' read -r -a backup_times \
      <<<"${STOCKWATCHER_CNB_BACKUP_TIMES:-11:35,15:55,16:10}"
    for backup_time in "${backup_times[@]}"; do
      if [[ "$current_time" > "$backup_time" || "$current_time" == "$backup_time" ]]; then
        create_snapshot "$(date +%Y%m%d)-${backup_time/:/}" || true
      fi
    done
  fi
  sleep 15
done

if create_snapshot "$(date +%Y%m%d)-shutdown"; then
  echo "StockWatcher Web preview window completed; stopping after verified snapshot"
else
  echo "StockWatcher Web preview stopped without a verified final snapshot" >&2
  exit 1
fi
