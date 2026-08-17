#!/usr/bin/env bash
set -Eeuo pipefail

DOCKER=/usr/local/bin/docker

script_dir=${STOCKWATCHER_SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}
root=${STOCKWATCHER_DEPLOY_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}
cd "$root"
env_file=${1:-.env.tunnel}

[[ -f "$env_file" ]] || { echo "Missing $root/$env_file" >&2; exit 2; }
[[ -f secrets/stockwatcher_master_key ]] || {
  echo "Missing local master key; create it without displaying its content." >&2
  exit 2
}

if [[ ! -x "$DOCKER" ]]; then
  echo "docker not found at $DOCKER" >&2
  exit 2
fi

compose=("$DOCKER" compose -f docker-compose.yml -f docker-compose.tunnel.yml --env-file "$env_file")
"${compose[@]}" config --quiet

running_services=$("${compose[@]}" ps --status running --format '{{.Service}}' 2>/dev/null || true)
if echo "$running_services" | grep -Eq '^(web|worker)$'; then
  echo "db-preflight skipped: web/worker already running"
else
  "$script_dir/db-preflight.sh"
fi

"${compose[@]}" up -d web worker tunnel-gateway cloudflared
"${compose[@]}" ps
