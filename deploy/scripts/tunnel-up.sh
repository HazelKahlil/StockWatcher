#!/usr/bin/env bash
set -Eeuo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
env_file=${1:-.env.tunnel}

[[ -f "$env_file" ]] || { echo "Missing $root/$env_file" >&2; exit 2; }
[[ -f secrets/stockwatcher_master_key ]] || {
  echo "Missing local master key; create it without displaying its content." >&2
  exit 2
}

compose=(docker compose -f docker-compose.yml -f docker-compose.tunnel.yml --env-file "$env_file")
"${compose[@]}" config --quiet
"${compose[@]}" up -d web worker tunnel-gateway cloudflared
"${compose[@]}" ps
