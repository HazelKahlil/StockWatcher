#!/usr/bin/env bash
set -Eeuo pipefail

DOCKER=/usr/local/bin/docker
CURL=/usr/bin/curl

root=${STOCKWATCHER_DEPLOY_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}
cd "$root"
env_file=${1:-.env.tunnel}
set -a; source "$env_file"; set +a

if [[ ! -x "$DOCKER" ]]; then
  echo "docker not found at $DOCKER" >&2
  exit 2
fi

compose=("$DOCKER" compose -f docker-compose.yml -f docker-compose.tunnel.yml --env-file "$env_file")
"${compose[@]}" ps
"$CURL" --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:${TUNNEL_ORIGIN_PORT:-18000}/health/ready" >/dev/null
"$CURL" --fail --silent --show-error --max-time 15 \
  "https://${DOMAIN}/health/live" >/dev/null
"$CURL" --fail --silent --show-error --max-time 15 \
  "https://${DOMAIN}/health/ready" >/dev/null
"${compose[@]}" exec -T worker \
  python -m stock_watcher.server.healthcheck worker
echo "Tunnel origin, HTTPS edge and worker health checks passed."
