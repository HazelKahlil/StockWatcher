#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo "Run this operation with sudo/root." >&2
  exit 1
}

username=${1:-}
[[ -n "$username" ]] || { echo "Usage: $0 <admin-username>" >&2; exit 2; }
cd "$(dirname "$0")/../deploy"
read -r -s -p "New admin password: " password; echo
read -r -s -p "Repeat password: " repeat; echo
[[ "$password" == "$repeat" ]] || { echo "Passwords do not match." >&2; exit 1; }
[[ ${#password} -ge 12 ]] || { echo "Password must have at least 12 characters." >&2; exit 1; }
printf '%s\n' "$password" | docker compose --env-file .env run --rm --no-deps -T web \
  python -m stock_watcher.server.admin_cli create-user \
  --username "$username" --role admin --password-stdin
unset password repeat
