#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo "Run this operation with sudo/root." >&2
  exit 1
}

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root/deploy"
[[ -f .env ]] || { echo "Missing deploy/.env; copy .env.example first." >&2; exit 1; }
[[ -f secrets/stockwatcher_master_key ]] || { echo "Missing master key; run scripts/generate-master-key.sh." >&2; exit 1; }
"$root/scripts/prepare-host.sh"

docker compose --env-file .env config >/dev/null
docker compose --env-file .env build --pull
docker compose --env-file .env stop web worker >/dev/null 2>&1 || true
docker compose --env-file .env run --rm --no-deps web \
  python -m stock_watcher.server.admin_cli migrate
docker compose --env-file .env up -d web worker caddy
"$root/scripts/healthcheck.sh"
