#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo "Run this host-permission step with sudo/root." >&2
  exit 1
}

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root/deploy"

mkdir -p secrets state/db reports backups
chown root:root secrets state
chmod 700 secrets
chmod 750 state

# The application containers run as UID/GID 10001. Bind-mounted writable
# directories and the Compose secret source must therefore be readable/writable
# by that identity without making them world-accessible.
chown -R 10001:10001 state/db reports backups
chmod 750 state/db reports backups

if [[ -f .env ]]; then
  chown root:root .env
  chmod 600 .env
fi
if [[ -f secrets/stockwatcher_master_key ]]; then
  chown 10001:10001 secrets/stockwatcher_master_key
  chmod 400 secrets/stockwatcher_master_key
fi

echo "Host directories and secret permissions are ready for container UID 10001."
