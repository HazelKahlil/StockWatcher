#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo "Run this operation with sudo/root." >&2
  exit 1
}

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root/deploy"
set -a; source ./.env; set +a

docker compose --env-file .env ps
curl --fail --silent --show-error --max-time 10 \
  --retry 12 --retry-delay 5 --retry-all-errors \
  "https://${DOMAIN}/health/live" >/dev/null
curl --fail --silent --show-error --max-time 10 \
  --retry 12 --retry-delay 5 --retry-all-errors \
  "https://${DOMAIN}/health/ready" >/dev/null
docker compose --env-file .env exec -T worker \
  python -m stock_watcher.server.healthcheck worker
echo "Web, TLS and worker health checks passed."
