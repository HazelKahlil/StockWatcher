#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo "Run this operation with sudo/root." >&2
  exit 1
}

backup=${1:-}
confirm=${2:-}
[[ -n "$backup" && "$confirm" == "--yes" ]] || {
  echo "Usage: $0 <backup-directory> --yes" >&2
  exit 2
}
root=$(cd "$(dirname "$0")/.." && pwd)
backup_abs=$(cd "$backup" && pwd)
case "$backup_abs" in
  "$root"/deploy/backups/*) ;;
  *) echo "Backup must be inside deploy/backups." >&2; exit 1;;
esac
(
  cd "$backup_abs"
  sha256sum -c SHA256SUMS.txt
)
cd "$root/deploy"
docker compose --env-file .env stop web worker
# Keep Caddy up; it will return a temporary upstream error during the controlled restore.
docker compose --env-file .env run --rm --no-deps worker \
  python -m stock_watcher.server.admin_cli restore \
  --input "/backups/$(basename "$backup_abs")"
docker compose --env-file .env up -d web worker
"$root/scripts/healthcheck.sh"
