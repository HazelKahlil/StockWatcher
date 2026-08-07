#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo "Run this operation with sudo/root." >&2
  exit 1
}

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root/deploy"
output="/backups/provider-preflight-$(date -u +%Y%m%dT%H%M%SZ).json"
docker compose --env-file .env exec -T worker \
  python -m stock_watcher.server.admin_cli provider-preflight \
  --scales 1,100,300,800,full --json-output "$output"
echo "Preflight evidence written to deploy${output}."
