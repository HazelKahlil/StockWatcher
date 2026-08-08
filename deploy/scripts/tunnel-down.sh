#!/usr/bin/env bash
set -Eeuo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
env_file=${1:-.env.tunnel}

docker compose -f docker-compose.yml -f docker-compose.tunnel.yml \
  --env-file "$env_file" stop cloudflared tunnel-gateway worker web
