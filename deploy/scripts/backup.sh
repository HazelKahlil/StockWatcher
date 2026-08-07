#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo "Run this operation with sudo/root." >&2
  exit 1
}

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root/deploy"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="/backups/stockwatcher-${stamp}"

docker compose --env-file .env exec -T worker \
  python -m stock_watcher.server.admin_cli backup --output "$target"

host_target="$root/deploy/backups/stockwatcher-${stamp}"
[[ -d "$host_target" ]] || { echo "Backup output missing: $host_target" >&2; exit 1; }
(
  cd "$host_target"
  find . -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS.txt
  sha256sum -c SHA256SUMS.txt >/dev/null
)
echo "Backup complete: $host_target"
