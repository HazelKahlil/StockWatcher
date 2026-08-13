#!/usr/bin/env bash

stockwatcher_cnb_prepare_runtime() {
  SW_CNB_WORKSPACE=${CNB_BUILD_WORKSPACE:-$(git rev-parse --show-toplevel)}
  SW_CNB_RUNTIME=${STOCKWATCHER_CNB_RUNTIME_DIR:-$SW_CNB_WORKSPACE/.cnb-runtime}
  SW_CNB_STATE=$SW_CNB_RUNTIME/state
  SW_CNB_DB=$SW_CNB_STATE/db/stockwatcher.db
  SW_CNB_REPORTS=$SW_CNB_STATE/reports
  SW_CNB_BACKUPS=$SW_CNB_RUNTIME/backups
  SW_CNB_LOGS=$SW_CNB_RUNTIME/logs
  SW_CNB_SECRETS=$SW_CNB_RUNTIME/secrets
  SW_CNB_MASTER_KEY=$SW_CNB_SECRETS/stockwatcher_master_key

  mkdir -p \
    "$SW_CNB_STATE/db" \
    "$SW_CNB_REPORTS" \
    "$SW_CNB_BACKUPS" \
    "$SW_CNB_LOGS" \
    "$SW_CNB_SECRETS" \
    "$SW_CNB_RUNTIME/tmp"
  chmod 700 "$SW_CNB_RUNTIME" "$SW_CNB_SECRETS"

  if [[ ! -s "$SW_CNB_MASTER_KEY" ]]; then
    local temporary_key
    temporary_key="$SW_CNB_MASTER_KEY.tmp"
    python - "$temporary_key" <<'PY'
import base64
import os
import sys
from pathlib import Path

target = Path(sys.argv[1])
target.write_text(
    base64.urlsafe_b64encode(os.urandom(32)).decode("ascii") + "\n",
    encoding="ascii",
)
PY
    chmod 400 "$temporary_key"
    mv "$temporary_key" "$SW_CNB_MASTER_KEY"
  fi

  export SW_CNB_WORKSPACE SW_CNB_RUNTIME SW_CNB_STATE SW_CNB_DB
  export SW_CNB_REPORTS SW_CNB_BACKUPS SW_CNB_LOGS SW_CNB_SECRETS
  export SW_CNB_MASTER_KEY
}

stockwatcher_cnb_export_app_env() {
  local require_https_origin=${1:-0}
  local source_commit
  local public_origin
  source_commit=${CNB_COMMIT:-$(git -C "$SW_CNB_WORKSPACE" rev-parse HEAD)}
  public_origin=${CNB_VSCODE_WEB_URL:-http://127.0.0.1:8686}
  if [[ $require_https_origin == 1 && $public_origin != https://* ]]; then
    echo "CNB_VSCODE_WEB_URL must be an HTTPS origin" >&2
    return 1
  fi

  export STOCKWATCHER_ENV=production
  export STOCKWATCHER_DB_PATH=$SW_CNB_DB
  export STOCKWATCHER_REPORT_DIR=$SW_CNB_REPORTS
  export STOCKWATCHER_BACKUP_DIR=$SW_CNB_BACKUPS
  export STOCKWATCHER_MASTER_KEY_FILE=$SW_CNB_MASTER_KEY
  export STOCKWATCHER_BUSINESS_TIMEZONE=Asia/Shanghai
  export STOCKWATCHER_PUBLIC_ORIGIN=$public_origin
  export STOCKWATCHER_SESSION_ABSOLUTE_HOURS=12
  export STOCKWATCHER_SESSION_IDLE_MINUTES=120
  export STOCKWATCHER_LOG_LEVEL=${STOCKWATCHER_LOG_LEVEL:-INFO}
  export STOCKWATCHER_SOURCE_COMMIT=$source_commit
  export STOCKWATCHER_BUILD_VERSION=cnb-web-internal-test-v1
  export STOCKWATCHER_WEB_HOST=0.0.0.0
  export STOCKWATCHER_WEB_PORT=8686
}
