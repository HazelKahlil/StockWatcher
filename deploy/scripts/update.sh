#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo "Run this operation with sudo/root." >&2
  exit 1
}

root=$(cd "$(dirname "$0")/.." && pwd)
"$root/scripts/backup.sh"
cd "$root/deploy"
docker compose --env-file .env config >/dev/null
docker compose --env-file .env build --pull
docker compose --env-file .env stop web worker
docker compose --env-file .env run --rm --no-deps web \
  python -m stock_watcher.server.admin_cli migrate
docker compose --env-file .env up -d --remove-orphans web worker caddy
"$root/scripts/healthcheck.sh"
