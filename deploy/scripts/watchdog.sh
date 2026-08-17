#!/usr/bin/env bash
# Origin watchdog for the Mac Cloudflare tunnel stack.
# Healthy runs stay quiet aside from a one-line heartbeat.
set -Eeuo pipefail

DOCKER=/usr/local/bin/docker
CURL=/usr/bin/curl
OPEN=/usr/bin/open
OSASCRIPT=/usr/bin/osascript
DATE=/bin/date
MKDIR=/bin/mkdir
SLEEP=/bin/sleep
CAT=/bin/cat
TAIL=/usr/bin/tail
WC=/usr/bin/wc
MV=/bin/mv

script_dir=${STOCKWATCHER_SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}
root=${STOCKWATCHER_DEPLOY_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}
cd "$root"
log_file=${STOCKWATCHER_WATCHDOG_LOG:-$HOME/Library/Logs/stockwatcher-watchdog.log}
state_file=${STOCKWATCHER_WATCHDOG_STATE:-$HOME/Library/Logs/.stockwatcher-watchdog-state}
origin_url=${STOCKWATCHER_WATCHDOG_ORIGIN:-http://127.0.0.1:18000/health/ready}
cooldown_seconds=${STOCKWATCHER_WATCHDOG_COOLDOWN:-1800}
log_keep_lines=${STOCKWATCHER_WATCHDOG_LOG_KEEP:-400}

"$MKDIR" -p "$(dirname "$log_file")" "$(dirname "$state_file")"

log() {
  local ts
  ts=$("$DATE" '+%Y-%m-%dT%H:%M:%S%z')
  echo "$ts $*" >> "$log_file"
}

trim_log() {
  local lines
  if [[ ! -f "$log_file" ]]; then
    return 0
  fi
  lines=$("$WC" -l < "$log_file" | tr -d ' ')
  if [[ "$lines" -gt "$log_keep_lines" ]]; then
    "$TAIL" -n "$log_keep_lines" "$log_file" > "${log_file}.tmp"
    "$MV" "${log_file}.tmp" "$log_file"
  fi
}

notify() {
  local message=$1
  "$OSASCRIPT" <<APPLESCRIPT
display notification "$(printf '%s' "$message" | sed 's/"/\\"/g')" with title "StockWatcher Watchdog"
APPLESCRIPT
}

now_epoch() {
  "$DATE" +%s
}

in_cooldown() {
  local last now
  if [[ ! -f "$state_file" ]]; then
    return 1
  fi
  last=$("$CAT" "$state_file" | tr -d '[:space:]')
  case "$last" in
    ''|*[!0-9]*) return 1 ;;
  esac
  now=$(now_epoch)
  if [[ $((now - last)) -lt "$cooldown_seconds" ]]; then
    return 0
  fi
  return 1
}

mark_restart() {
  now_epoch > "$state_file"
}

wait_for_docker() {
  local i
  i=0
  while [[ "$i" -lt 24 ]]; do
    if "$DOCKER" info >/dev/null 2>&1; then
      return 0
    fi
    "$SLEEP" 5
    i=$((i + 1))
  done
  return 1
}

origin_ready() {
  "$CURL" --fail --silent --show-error --max-time 10 "$origin_url" >/dev/null
}

if [[ ! -x "$DOCKER" ]]; then
  log "docker not found at $DOCKER"
  notify "docker 可执行文件不存在，无法检查隧道。"
  trim_log
  exit 2
fi

if ! "$DOCKER" info >/dev/null 2>&1; then
  log "docker info failed; opening Docker Desktop"
  "$OPEN" -ga Docker || true
  if wait_for_docker; then
    log "docker engine became ready"
  else
    log "docker engine still unavailable after 120s"
    notify "Docker 引擎 120 秒后仍不可用，StockWatcher 隧道未恢复。"
    trim_log
    exit 1
  fi
fi

if origin_ready; then
  log "heartbeat origin ready"
  trim_log
  exit 0
fi

log "origin ready failed; confirming in 15s"
"$SLEEP" 15
if origin_ready; then
  log "origin recovered on second check"
  trim_log
  exit 0
fi

log "origin failed twice: $origin_url"
if in_cooldown; then
  log "skip restart; cooldown active"
  notify "源站连续失败，但仍在 30 分钟冷却中，本次不自动重启。"
  trim_log
  exit 1
fi

log "restarting tunnel stack"
notify "源站连续两次健康检查失败，正在重启隧道栈。"
mark_restart
if ! "$script_dir/tunnel-down.sh"; then
  log "tunnel-down.sh failed"
fi
if ! "$script_dir/tunnel-up.sh"; then
  log "tunnel-up.sh failed"
  notify "隧道自动重启失败，需要人工检查。"
  trim_log
  exit 1
fi

if "$script_dir/tunnel-healthcheck.sh"; then
  log "restart healthcheck passed"
  notify "StockWatcher 隧道已自动重启，健康检查通过。"
  trim_log
  exit 0
fi

log "restart healthcheck failed"
notify "隧道已重启，但健康检查仍失败，需要人工检查。"
trim_log
exit 1
