#!/usr/bin/env bash
# Copy launchd-facing scripts out of Documents so /bin/bash can execute them.
set -Eeuo pipefail

src=$(cd "$(dirname "$0")" && pwd)
dest=${STOCKWATCHER_SCRIPT_DIR:-$HOME/Library/Application Support/StockWatcher}
mkdir -p "$dest"
for name in db-preflight.sh scheduled-backup.sh watchdog.sh \
  tunnel-up.sh tunnel-down.sh tunnel-healthcheck.sh; do
  cp "$src/$name" "$dest/$name"
  chmod 755 "$dest/$name"
done
echo "Installed launchd scripts into $dest"
