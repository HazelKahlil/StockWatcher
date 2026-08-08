#!/usr/bin/env bash
set -Eeuo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
env_file=${1:-.env.tunnel}
set -a; source "$env_file"; set +a

compose=(docker compose -f docker-compose.yml -f docker-compose.tunnel.yml --env-file "$env_file")
"${compose[@]}" ps
curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:${TUNNEL_ORIGIN_PORT:-18000}/health/ready" >/dev/null
curl --fail --silent --show-error --max-time 15 \
  "https://${DOMAIN}/health/live" >/dev/null
curl --fail --silent --show-error --max-time 15 \
  "https://${DOMAIN}/health/ready" >/dev/null
"${compose[@]}" exec -T worker \
  python -m stock_watcher.server.healthcheck worker
echo "Tunnel origin, HTTPS edge and worker health checks passed."
